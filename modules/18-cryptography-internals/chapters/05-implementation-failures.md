# Chapter 05 — Implementation failures: the attack catalog

> **The 30-second version.** AES has never been broken; the RSA
> factoring record cost 2,700 CPU-core-years; ECDSA's math holds. Every
> attack here with real victims — Heartbleed, Lucky 13, KRACK, Debian's
> RNG, Bleichenbacher's 19-year oracle — failed at *memory discipline*:
> a length field trusted, a microsecond leaked, a counter reset, an
> uninitialized buffer silenced, a comparison that quits early. This is
> the catalog "don't roll your own crypto" refers to. The primitives
> never broke; the memory discipline did.

## Heartbleed: a length field the code didn't own

OpenSSL 1.0.1 shipped RFC 6520's *heartbeat* in March 2012 — a TLS
keepalive that echoes a payload. The request is a textbook
TLV: one type byte, a two-byte big-endian `payload_length`, the
payload, 16 bytes of random padding. The bug, live for two years
until April 2014 (CVE-2014-0160), is one line of C:

```c
memcpy(bp, pl, payload);   /* bp: response buffer, pl: payload pointer,
                              payload: the ATTACKER-DECLARED length (0..65535) */
```

The server never checks `payload` against the record's actual byte
count. Send a one-byte payload with the length field lying `0x0100`:
the `memcpy` reads 256 bytes starting at your one honest byte —
through the end of the record's heap chunk and into whatever the
allocator served next:

```text
      one heap chunk (the received heartbeat record)          the rest of the heap
+------------------------------------------------------+---------------------------+
| 01 | 00 01 | 'A' | ...16 B padding... | chunk slack    | chunk hdr | MASTER-...   |
+------------------------------------------------------+---------------------------+
  ^     ^      ^
  |     |      pl = source of the vulnerable memcpy
  |     |      actual payload: 1 byte ('A')
  |     declared payload_length = 0x0100 = 256  (the lie; max 0xFFFF = 65,535)
  hb_type = 1 (request)

        |<-------------- memcpy reads 256 B -------------->|
         1 byte you own + 255 B of heap you never sent
```

This is a **heap over-read**: the response buffer is sized
`payload + 16`, so the *write* is in bounds; the *read* walks past
the record chunk. It is the complement of use-after-free (module 01/02
chapter 05): a UAF touches bytes the program logically released but
still maps; Heartbleed reads bytes it never logically owned for this
message — stale record buffers, plaintext request bodies, and the
crown jewels: **session keys first** (a mid-connection heap is full of them),
then private keys (Cloudflare's public challenge
settled that within days), passwords, cookies.

The asymmetry that made it historic: the fix is one bounds check —
`if (1 + 2 + payload + 16 > n) return 0;` — but the cost was
re-issuing certificates for ~17% of the secure web (≈ half a million
servers, Netcraft, April 2014). The class, named: **TLV framing
bugs**. A state machine must never trust a length field it doesn't
own — derive it from the record that actually arrived.

## Lucky 13: two microseconds, amplified

TLS 1.2's CBC suites are MAC-then-encrypt: the receiver decrypts,
*strips padding, then checks the MAC over what remains*. Chapter 02
planted the birth defect; here is the exploit. AlFardan and Paterson
(2013) measured that rejecting a guessed record with more valid
padding is faster — fewer bytes left for HMAC to verify — by
**1–2 µs per padding-length step**. One microsecond is nothing; two
million chosen records over a LAN is an adaptive **padding oracle**,
and padding oracles decrypt ciphertext without the key: small biases
amplified by repetition *are* key recovery.

Patch versus structure — the whole lesson. OpenSSL's
fix: make padding handling constant-time — HMACs over dummy lengths,
every padding treated as maximal, no branching. That patch took
**three rounds** (the first two were found still leaky in 2013). TLS
1.3's fix: AEAD only — the tag is computed over the ciphertext
(encrypt-then-MAC ordering), so there is no padding to strip and no
decrypt-then-check sequence to time. Chapter 04's argument: the patch fixes the *instance*; the structure
removes the *class*.

## The nonce-reuse catalog: counters are process state

Chapter 02 derived the two-time pad: reuse the (key, nonce) pair and
`c1 ⊕ c2 = p1 ⊕ p2`; in GCM, one reuse also hands over the
authentication key H (Joux's 2006 forbidden attack). Every incident
below is a *memory* story about the counter:

- **MS-CHAPv2** (the 1990s–2012 class): static published constants
  stood in for challenge material — a fixed, public nonce space,
  DES brute-forceable by 2012.
- **KRACK, 2017** (Vanhoef–Piessens): WPA2's 4-way handshake
  *retransmits* message 3; the client's handling reinstalls
  the pairwise key, **resetting the packet-number counter to zero** —
  the nonce counter is process state that must survive retransmission
  logic. TKIP leaks keystream; GCMP, same key and nonce both
  directions, leaks the MIC key: forgery.
- **PS3, 2010** (fail0verflow, 27C3): Sony signed firmware with a
  *fixed* ECDSA nonce k. Two signatures share r; then
  `k = (h1 − h2)/(s1 − s2) mod n` and `d = (s1·k − h1)/r mod n`
  (chapter 03) — the master key, factored out of its own
  signatures.
- **Cloud KMS AES-GCM, responsibly disclosed**: the nonce counter was
  initialized from *timestamp + machine-ID* — "entropy at rest." Two
  instances restored from the same snapshot inside one clock tick mint
  identical nonces: one collision, H recovered, permanent forgery. A
  persisted monotonic counter cannot collide until 2^96; a timestamp
  can.
- **Fleet VPN, 2020** (the `--uniqueids` reconnect class):
  connection-identity dedup replayed per-peer nonce state on
  reconnect — KRACK's shape, different trigger.

The genus: a nonce is a counter *in memory*; retransmission, cloning,
restart, and dedup logic are memory operations on it. RFC 6979's
lesson generalizes — deterministic state beats harvested randomness
whenever the harvesting is your attack surface.

## Side channels: memory reads across trust boundaries

A side channel is a memory read the CPU performs on your behalf,
across a boundary the ISA says is solid — ordered by how far the read
crosses:

**Bernstein, 2004** (paper 2005) founded the subfield: AES T-tables —
4 KB of tables, index = key ⊕ plaintext byte. The index selects a
cache line; occupancy changes timing; the attacker's own AES
encryptions evict chosen lines and the victim's latency leaks the
index — remotely. Osvik–Shamir–Tromer (2006–2010) made it local
(prime+probe; Tromer 2010 recovered RSA keys through ~100 ms of
adjacent-VM noise). The structural fix: bitslicing, then **AES-NI**
(2008, Westmere) — `AESENC` is data-independent by construction.

**Spectre V1, 2018** (Kocher et al.): the CPU speculatively executes
the load *before* the bounds check; the result is discarded from
registers but not the cache. Branch prediction becomes the cache
polluter; the read crosses the browser sandbox (module 16
chapter 05): JavaScript reading cross-origin memory through timer
channels. Defenses — timer coarsening, cross-origin isolation — are
*mitigations*: the read primitive remains, only the transmission is
throttled.

**Rowhammer → Bellcore**: module 02's bit flip lands in an RSA private
key's CRT parameter during signing; the faulted signature m′
satisfies `gcd(m′^e − m, n) = p` (chapter 03): one faulted signature
is the factorization. Post-Rowhammer this is a platform threat
(2020–2021 firmware advisories); the countermeasure, verify-before-output,
costs ~one exponentiation.

**DRAM cold boot**: module 01 chapter 01's grounded capacitor holds
its bit after power removal, decaying over seconds to minutes.
Halderman et al., "Lest We Remember" (2008; replicated through 2011):
invert a canned-air can (−50 °C), yank power, boot a tiny dumper,
scavenge keys from decayed-but-recoverable DRAM. Decay is temperature:

```text
DRAM temperature      time until substantial bit decay (typical modules)
  55 °C (hot chip)     seconds
  ~22 °C (room)        seconds to a few minutes
  −50 °C (cold spray)  minutes; most modules near-zero errors at 60 s
  −196 °C (LN2)        hours — effectively frozen
```

Keys in process memory are game over to anyone who can touch the DIMM
— hence TPM/enclave key hierarchies (chapter 03; module 05/06's
hardware roots).

## Parsing, comparison, and a 19-year oracle

**ASN.1/DER is where binary parsing bugs live.** X.509 certificates
are TLV trees: tag, length, value, recursively. Length fields again
(the Heartbleed class wearing a different header), integer
signedness, negative lengths, recursion depth in constructed types,
and BER leniency where DER was mandated — every certificate is
attacker-influenced memory at a parser's trust boundary. The genus:
Microsoft's ASN.1 library (MS04-007, 2003), OpenSSL's 2006 ASN.1
CVEs, BER-acceptance signature forgeries. DER's determinism helps
*keys* not leak; it does nothing for the parser.

**Bleichenbacher, 1998**: PKCS#1 v1.5 requires the
padded block `00 02 || random || 00 || message`; any observable
difference between "bad padding" and other decryption failures is an
oracle. The adaptive attack halves the candidate interval per query —
~2^20 queries, a *million messages*, per session key. TLS's
countermeasure: identical failures, random premaster. **ROBOT, 2017**
(Böck–Somorovsky–Young) found the oracle alive in major TLS
stacks — Facebook, PayPal, Android among them — **19 years later**. The fix that finally worked was
structural: TLS 1.3 removed RSA key transport entirely (DHE/ECDHE
only) — an oracle over a primitive you no longer use is not an oracle.

**Timing-safe comparison** is the micro-version of the same disease:
`memcmp` returns at the first differing byte, so a MAC check leaks
the *length of the match* — a byte-at-a-time forgery, worst case
255 × 32 ≈ 8,160 requests. The fix is the fold:
XOR every byte pair into an accumulator, test once
(`hmac.compare_digest`). The tooling that *proves* it: **dudect**
(Reparaz et al., 2017) — a Welch t-test over timing
distributions for fixed-secret vs. random-secret classes;
**ctgrind** marks secrets "uninitialized" in valgrind so
secret-dependent branches or addresses trigger warnings. Constant
time is measured, not claimed.

## The RNG genus and the layer stack: where breaks actually lived

Three flavors of randomness-as-memory failure:

- **Debian, 2006–2008**: a distro patch removed two lines that mixed
  an *uninitialized* buffer into OpenSSL's RNG seeding — to silence a
  valgrind warning. Uninitialized memory was the entropy. The only
  variable input left: the process ID — 32,767 states, every key
  generated in the two-year window precomputable and factorable
  (DSA-1571; ~500k keys blacklisted later). Nothing crashed; weak keys
  look like strong keys — hence two years unnoticed.
- **Android/Bitcoin, 2013**: a SecureRandom state collision produced
  repeated ECDSA nonces; the PS3 formula recovers the private key,
  and wallets were swept. Randomness *was* the attack surface, as
  RFC 6979 anticipated.
- **The clone fleet**: a VM image boots before the CRNG reseeds; every
  clone mints the *same* SSH host key and TLS keys ("Mining Your Ps
  and Qs," 2012: tens of thousands of duplicates on the
  internet). Entropy starvation is a *boot-order memory bug*;
  virtio-rng is the fix (chapter 01).

The meta-lesson as a table: AES unbroken since 2001; the RSA
factoring record 829 bits (2020, ~2,700 CPU-core-years) — nowhere
near 2048; ECDSA's math holds, its nonces didn't. This chapter's
catalog, by layer:

| Layer | Incidents (this catalog) | The fix that finally worked |
|---|---|---|
| Primitive (AES, RSA, ECC) | **0** | none needed — margins hold |
| Mode | 4: ECB pattern leak; CBC padding birth defect; GCM nonce birthday + Joux; CTR two-time pad | AEAD APIs — one knob left (the nonce); encrypt-then-MAC |
| Implementation | 8: Heartbleed; Lucky 13's µs; Bernstein T-table timing; ASN.1/DER parsing; leaky memcmp; Debian; Android RNG; clone fleets | constant-time code + dudect/ctgrind harnesses; bounds-checked framing; `getrandom(2)`/virtio-rng |
| Protocol | 4: Bleichenbacher→ROBOT; KRACK; BEAST-class IV prediction; 1.2 ticket FS hole | TLS 1.3 structural removal (AEAD-only, DHE-only, transcript hash, key erasure); WPA3 |
| Key management | 5: cold boot; Rowhammer→Bellcore; duplicate host keys; ticket keys in shared CDN memory; harvest-now-decrypt-later | TPM/enclave wrap hierarchies; rotation on suspicion; hybrid PQC |

Zero at the primitive. Eight at the implementation. The meta-lesson:
**the primitives never broke — the memory discipline did** — and
discipline means lengths, timings, counters, and erasure, none of
which appear in the math.

## What insiders know

- **"Don't roll your own crypto," decoded honestly:** the math is the
  reviewable part. The warning is about this chapter:
  constant-time discipline, nonce state machines, TLV parsing, and key
  erasure are *invisible in code review*, visible only under
  adversarial measurement — dudect, fuzzing, or a stopwatch. You
  cannot test what you wrote the way the world will.
- **OpenSSL's `SECLEVEL`:** the `@SECLEVEL=n` cipher-config knob
  (Debian's OpenSSL 3.0 default is 2) refuses ≤112-bit security —
  1024-bit RSA, SHA-1. Talking to legacy gear means
  `-cipher 'DEFAULT:@SECLEVEL=1'`. CT patches now ship with variance
  harnesses — Lucky 13's three-round regression is
  why.
- **Harvest-now-decrypt-later arithmetic:** a recorded X25519
  handshake is Shor-recoverable once a cryptographically relevant
  quantum computer exists; if data must stay secret >10 years, it is
  at risk *now*. ML-KEM (FIPS 203, NIST, August 2024); browsers and
  CDNs hybrid (X25519 + ML-KEM) through 2023–2024; OpenSSH 9.9 (2024)
  defaults to `mlkem768x25519`; YubiKey 5.7 (2024) carries ML-KEM.
  Migration started; not done.
- **Reading crypto CVEs like an insider:** the words "timing,"
  "distinguisher," or "observable difference" mean *oracle* — always
  worse than the CVSS says.
- **Post-incident triage:** length-field bug → rotate keys (bytes
  left the box); nonce/state bug → rotate the *key* (H is burned);
  padding oracle → patch; RNG bug → rotate everything in the window,
  and date the window.
- **The countermeasure-regression law:** ROBOT proves "we return the
  same error" decays. Never accept a constant-time claim older than
  the last refactor; re-run the harness.

## See it yourself

**1. The two-time pad, live** (chapter 02's derivation, performed):

```python
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

key, nonce = os.urandom(32), os.urandom(16)
def stream(msg):                       # both "parties" reuse (key, nonce): the bug
    e = Cipher(algorithms.AES(key), modes.CTR(nonce)).encryptor()
    return e.update(msg)

p1, p2 = b"attack at dawn!", b"attack at dusk!"
c1, c2 = stream(p1), stream(p2)
x = bytes(a ^ b for a, b in zip(c1, c2))
print(x)        # 11 zero bytes, then 14 04 05
print(bytes(a ^ b for a, b in zip(x, p1)))      # b'attack at dusk!'  (p2 recovered)
ks = bytes(a ^ b for a, b in zip(c1, p1))       # the keystream — forge freely
```

**2. A timing oracle on HMAC comparison** — Python leaks ~µs per byte
position, C leaks ns; the shape is the same:

```python
import hmac, os, time, statistics as st

key = os.urandom(32)
good = hmac.digest(key, b"transfer:100", "sha256")
def leaky(a, b):                       # the bug: quits at first mismatch
    for x, y in zip(a, b):
        if x != y: return False
    return True
def t(mac):
    t0 = time.perf_counter_ns(); leaky(good, mac); return time.perf_counter_ns() - t0

for pos in (0, 8, 16, 24, 31):         # length of matching prefix
    mac = good[:pos] + bytes([good[pos] ^ 1]) + b"\x00" * (31 - pos)
    s = [t(mac) for _ in range(10_000)]
    print(f"prefix={pos:2d}  mean={st.mean(s):5.0f} ns  sd={st.pstdev(s):4.0f}")
```

The mean climbs with the prefix — that ladder is a byte-position
oracle. The fix is `hmac.compare_digest`; dudect turns it into a
pass/fail t-test.

**3. Scan for the historical weaknesses**:

```console
$ git clone --depth 1 https://github.com/drwetter/testssl.sh && cd testssl.sh
$ ./testssl.sh --severity HIGH --warnings batch your.host:443
```

Notice: RC4 flags, CBC suites in TLS 1.2 (Lucky 13's home — every
unpatched-era server), the FS column (forward secrecy = key erasure,
chapter 04).

**4. Heartbleed's missing bounds check, 30 lines** — a deliberately
vulnerable echo server (never ship this shape):

```c
/* hb_vuln.c — the 2014 bug's shape: memcpy a DECLARED length unchecked.
 * gcc -O0 -g -o hb_vuln hb_vuln.c  (chunk B sits right after A) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <arpa/inet.h>
#include <sys/socket.h>

int main(void) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = {0};
    a.sin_family = AF_INET; a.sin_port = htons(8443);
    bind(s, (struct sockaddr *)&a, sizeof a); listen(s, 5);
    printf("pid=%d  heap: /proc/%d/maps\n", getpid(), getpid());
    int c = accept(s, NULL, NULL);
    char *rec  = malloc(64);                       /* chunk A: "record"   */
    memset(rec, 0x41, 64);                         /* stale-looking bytes */
    char *keys = malloc(256);                      /* chunk B: adjacent   */
    memcpy(keys, "MASTER-SECRET 9f3a51c2e8", 23); /* crown jewels        */
    for (;;) {
        char wire[512];
        ssize_t n = recv(c, wire, sizeof wire, 0); /* [type][len2BE][payload] */
        if (n < 3) break;
        unsigned short plen = ((unsigned char)wire[1] << 8) | (unsigned char)wire[2];
        memcpy(rec, wire + 3, (size_t)n - 3);      /* bounded: what arrived */
        char *out = malloc(3 + plen);
        memcpy(out, wire, 3);
        memcpy(out + 3, rec, plen);                /* BUG: trusts plen — reads */
        send(c, out, 3 + (size_t)plen, 0);         /* past A into B            */
        free(out);
    }
}
```

```python
import socket
s = socket.create_connection(("127.0.0.1", 8443))
s.sendall(b"\x01\x01\x00A")     # declare 256 B, send 1 real byte
r = s.recv(512)
print(r[3:96])                  # 1 honest byte, then A's slack, B's header...
print(b"MASTER-SECRET" in r)    # True — chunk B's contents crossed the boundary
```

Consecutive `malloc`s come from the top chunk — B is physically
after A (`grep heap /proc/PID/maps`: both live in [heap]). The honest
caveat: OpenSSL's 16 KiB record buffers were recycled through
thousands of connections — what leaked was *whose* stale bytes were
neighbors, hence rotate everything.

## How to use this knowledge

- **Triage a crypto CVE in 60 seconds**: classify by layer (length
  field? timing? counter state? RNG? key exposure?) — the layer
  dictates the response, patch Tuesday to rotate-everything.
- **Design-review questions that catch this chapter's bugs**: who owns
  this length field; what state does this counter survive; is the
  comparison constant-time *and measured*; where keys get erased.
- **Ops tripwires**: fingerprint and dedupe fleet host keys monthly;
  run testssl.sh quarterly; pin CT-harness output in CI.
- **The interview version**: "Heartbleed was a heap over-read of a
  trusted length field; Lucky 13 a 1–2 µs padding oracle AES itself
  was never blamed for" — primitive vs. implementation, in one breath.

## Connections

- **Module 18 chapter 01** — Debian's two removed lines live at the
  entropy layer; the clone fleet is boot-order starvation.
- **Module 18 chapter 02** — the two-time pad derivation this catalog
  performs; GCM's forbidden-attack arithmetic.
- **Module 18 chapter 03** — the PS3 nonce-recovery formula, the
  Bellcore gcd Rowhammer completes, blinding and bignum layouts.
- **Module 18 chapter 04** — the right column of the layer table *is*
  the TLS 1.3 spec: AEAD-only, DHE-only, transcript, key erasure.
- **Module 02** — the sibling dark-arts module: chapter 05's heap
  chunk layout is what Heartbleed reads; Rowhammer and UAF-on-keys
  physically attack chapter 03's key material.
- **Module 16 chapters 04/05** — Heartbleed's heap as the browser-heap
  chapter sees it; Spectre's browser sandbox-crossing read, timer
  coarsening and all.
- **Module 01 chapter 13** — C#/managed crypto arrays: the GC copies
  and pins key material during pause-compaction, reopening the
  key-erasure problem this chapter keeps closing (repairs that
  chapter's orphan status).
- **Module 01 chapter 16** — Swift ARC on key buffers: when the last
  reference drops decides when the key bytes actually vanish —
  deterministic erasure across the language boundary (repairs that
  chapter's orphan status).

## Quiz

1. **Concept.** Heartbleed is "the complement of use-after-free."
   Explain what memory each class touches, why the over-read needs no
   write primitive to leak keys, and why the leaked bytes are *stale*.
2. **Concept.** Decode "don't roll your own crypto" honestly: name the four
   disciplines the warning actually protects, and why none is visible
   in review.
3. **Predict/derive.** A KMS builds each 96-bit GCM nonce from timestamp + machine-ID,
   degenerating to 2^32 distinct nonces per key. Compute the message
   count at which a collision is 50% likely (birthday arithmetic), and
   what one collision hands the attacker under GCM.
4. **Predict/derive.** A CTR keystream was reused for two
   ciphertexts. You compute `c1 ⊕ c2` and get eleven zero bytes,
   then `14 04 05`, then zeros. You know `p1 = "attack at dawn!"`.
   Recover `p2`, then state what else falls out and what you can now do
   that the cipher should have prevented.
5. **Predict/derive.** An attacker sends a heartbeat request: 1-byte
   payload, declared `payload_length` 0xFFFF. How many bytes
   does the `memcpy` read, how many are beyond the attacker's data, and
   why is the *destination* write in-bounds while the *source* read is
   the violation?
6. **Diagnose.** A distro maintainer patches two lines out of the RNG
   seeding path to silence a valgrind "uninitialized value" warning.
   Two years later every key generated on that distro is factorable.
   Reconstruct the chain: what did the lines do, what entropy remained,
   why nothing crashed, and what the maintainer should have done.
7. **Diagnose.** An API's MAC check uses early-exit `memcmp`. Logs show one IP holding
   the first 4 tag bytes constant for an hour while sweeping byte 5
   across 256 values, at ~250 requests/sec. What is happening, roughly
   how many requests finish it, and what is the fix?
8. **Cross-chapter.** Using module 18 chapter 04: name the TLS 1.3 decision that kills each
   attack — (a) Bleichenbacher/ROBOT, (b) Lucky 13, (c) the 1.2
   session-ticket forward-secrecy hole — and the one attack class 1.3
   *consciously accepted* in the trade.

<details>
<summary>Answers</summary>

1. Use-after-free touches memory the program logically released but
   still maps; Heartbleed reads memory it never logically owned for
   this message — past the record chunk's
   end into adjacent chunks. No write primitive is needed: the read's
   content is echoed back
   verbatim — the response buffer is sized for the declared length, so
   the *write* is in-bounds. The bytes are stale, not random, because the heap
   recycles chunks — freed record buffers, plaintext bodies, and
   previously written session-key material sit exactly where the
   over-read walks.
2. (1) constant-time execution — no secret-dependent branches or
   table addresses; (2) nonce/counter state surviving retransmission,
   restart, and cloning; (3) TLV parsing that derives
   lengths from what arrived, not declared; (4) key erasure
   on schedule. All four are invisible in review because they are
   *absences* — a
   timing difference, a reset counter, an unparsed length, an
   un-erased buffer all look like correct code, measurable only under
   adversarial testing (dudect, fuzzing, state-machine replay).
3. 50% collision at ~1.18·√N ≈ 1.18·2^16 ≈ **77,000 messages**
   (~2^17). One collision means two ciphertexts under one keystream
   (`c1 ⊕ c2 = p1 ⊕ p2`); specific to GCM, the forbidden attack:
   reused-nonce polynomials reveal **H**, the GHASH key — permanent tag
   forgery. Rotate the key, not just the nonce.
4. `p2 = (c1 ⊕ c2) ⊕ p1`: `14 04 05` against `a w n` (0x61 0x77 0x6E)
   gives `u s k` — `p2 = "attack at dusk!"`. The zeros mark where the
   plaintexts agree. With p1 known, the keystream falls out
   (`ks = c1 ⊕ p1`): decrypt any other message under that (key, nonce),
   *and encrypt forgeries* — confidentiality (and integrity, in a MAC
   construction) gone from one reuse.
5. The `memcpy` reads the declared **65,535** bytes; **65,534** are
   beyond the attacker's data — chunk slack, the next chunk's header,
   its payload. The write is in-bounds because the response buffer was
   allocated `payload_length + 16` before the copy; the violation is the
   source read — the declared length was never checked against the
   record's actual size, the TLV rule the state machine didn't own.
6. The removed lines mixed an *uninitialized* buffer into the RNG
   seed — its unpredictable contents were the entropy. With them
   gone, only the process ID varied (~32,767 states), so every key in
   the window came from a precomputable, factorable seed (DSA-1571;
   ~500k keys blacklisted). Nothing crashed because weak keys are
   syntactically perfect and pass every self-test; only precomputation
   notices. The maintainer should have
   seeded from initialized-but-unpredictable memory: "uninitialized"
   and "unpredictable" were the same property in disguise.
7. Byte-at-a-time timing forgery: the early exit makes comparison
   time proportional to the matching prefix, so each candidate byte
   that survives longer is (probabilistically) correct; bytes are
   locked left to right and re-tested to beat noise — hence the held
   4-byte prefix and modest rate. Worst case ≈ 255 × 32 ≈
   **8,160 requests**. Fix: constant-time comparison — XOR-fold all
   bytes, test once (`hmac.compare_digest`, `CRYPTO_memcmp`).
8. (a) TLS 1.3 removed RSA key transport — ephemeral (EC)DHE only, so
   there is no PKCS#1 v1.5 decryption to give a
   padding oracle. (b) 1.3 allows AEAD only — the tag covers
   the ciphertext (encrypt-then-MAC), so no padding is stripped
   before authentication; there is no check sequence to time.
   (c) 1.3's resumption: per-ticket key derivation and the spec's
   erasure/lifetime discipline (chapter 04's key schedule). What 1.3
   knowingly accepted: **0-RTT replay** — early data under a PSK is
   replayable by an active attacker, delegated to the application
   (idempotent GET yes, POST no).

</details>
