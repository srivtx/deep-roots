# Module 18 — Cryptography internals: the memory model of trust

> Cryptography is the one field where a *timing* difference of 50 nanoseconds
> is a security breach, where a single reused 96-bit nonce destroys a
> terabyte of secrecy, and where the difference between safe and broken is
> which *branch your CPU took*. Every primitive in this module is a memory
> story: entropy is harvested from physical memory you cannot read
> (ring oscillators, DRAM refresh jitter), AES is a byte-shuffling state
> machine that exists because cache lines are 64 bytes, RSA lives in
> heap-allocated bignums whose access pattern *is* the leak, TLS 1.3 is a
> state machine whose entire reason for existing is to never re-expose old
> key material, and Heartbleed — the most expensive memory bug in history —
> was a memcpy past the end of a struct. Module 01's question — *where do
> the bytes live, who can read them, and what can go wrong* — asked of the
> one subsystem where the answer is measured in feasibility, not
> permissions.

**5 chapters · one final exam · prerequisite: module 01 chapters 03 (virtual
memory — permissions and ASLR return as the adversary), 05 (the heap —
Heartbleed was a heap over-read), 20 (hardware memory model — caches and
speculation return as side channels); module 02 (memory security) is the
sibling dark-arts module; module 13 ch03 (TCP) is referenced, not required.**

## How to study it

Chapter 01 goes to the root of the whole field: where unpredictability
physically comes from (and why a deterministic CPU must harvest it from
analog reality); 02 builds the symmetric world as a state machine over
16-byte blocks and derives why *modes* exist and how each one fails;
03 is the public-key world — the math, but more importantly the *bignum
memory layouts* that decide whether an implementation leaks its key;
04 assembles TLS 1.3 as the case study of protocol-state-machine design
where transcript hashing and key erasure are memory-hygiene rules; 05
closes with the failure catalog — every famous attack as a memory-model
story (Heartbleed heap over-read, Lucky 13 timing via padding oracles,
Rowhammer flipping RSA bits, Spectre reading across the sandbox). Labs:
any Linux box (`/dev/random` vs `/dev/urandom`, `getrandom(2)`, OpenSSL's
API, `openssl speed`, python `hashlib`/`cryptography`); a browser for the
TLS handshake in DevTools. Platform marked where it varies.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [Entropy: randomness from physical memory](chapters/01-entropy-randomness.md) | Ring oscillators, DRAM jitter, TRNG vs DRBG, `/dev/random`'s pool — why deterministic machines must steal unpredictability from physics. |
| 02 | [AES and modes: the symmetric state machine](chapters/02-symmetric-memory.md) | The 16-byte block, the key schedule, ECB's leaks, CTR's nonce discipline, GCM's GF(2^128) multiply — and each mode's exact failure. |
| 03 | [Public key: bignums are the attack surface](chapters/03-public-key-bignum.md) | RSA and ECC from the arithmetic up; CRT reassembly; constant-time discipline — the layouts that leak and the blinding that hides them. |
| 04 | [TLS 1.3: a state machine about memory hygiene](chapters/04-tls13-handshake.md) | Transcript hash, key schedule, forward secrecy as key erasure, 0-RTT as the replay trade — the handshake as managed memory. |
| 05 | [Implementation failures: the attack catalog](chapters/05-implementation-failures.md) | Heartbleed, Lucky 13, nonce reuse, side channels from Rowhammer to Spectre — every famous break as a memory story. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — entropy.** Start from the paradox: a CPU is a deterministic
circuit, so where does unpredictability come from? Answer: measurement of
analog/quantum noise through memory: ring oscillators (two inverters
racing, phase drift sampled into a flip-flop — module 17 ch01's
metastability returns as a *feature*), DRAM refresh jitter (the same
leaky capacitor of module 01 ch01), shot/thermal noise in diodes, and
Intel's RDRAND/RDSEED pipeline (DRBG post-conditioning with AES-CBC-MAC
per SP 800-90B; the Fort Hill/i7 controversy honestly stated). Distinguish
the three layers with a diagram: **entropy source → conditioning → DRBG
(deterministic expansion) → consumers**. The entropy *estimate* problem:
min-entropy, why NIST's estimator shaves, why "256 bits of estimate" is
usually optimistic (the Debian OpenSSL disaster as the cautionary tale —
2008, `valgrind` warning removed the PID as the only variable, 32,767
possible keys, all factorable, ~2 years to notice). The Linux pool
mechanism in detail: the input pool's LFSR mixing (fast pool + slow pool
of `/dev/random`'s pre-2014 design), `add_device_randomness`, the 2020
rewrite (5.6+ Linus' `random.c` overhaul — `getrandom(2)` as the primary
API, jitter-based fast init, the blocking-urandom fiasco with systemd
boot hangs), CRNG ChaCha20-based, per-CPU batches, `/dev/random` vs
`/dev/urandom` equivalence post-4.8 honestly stated. Virtual machine
entropy starvation (fresh clone shares the CRNG state — the "same SSH
key on every clone" incident class; virtio-rng as the fix) — this is the
cloud-era twist nobody warns about, connect to module 15 (the VM is a
process that copied its randomness). The DRBG concept formally: state +
reseed interval + prediction resistance (SP 800-90A CTR_DRBG as the
worked example: state = key+counter, output = AES-CTR keystream, reseed
on threshold). Fool's gold: why `rand(time(NULL))`, why
`Math.random()`-seeded UUIDs collided in production (the "MoE" browser
fingerprint incident is a real example — short-entropy IDs), why human
passwords are ~20-40 bits not 128. Number sense table: 32 bits of
entropy brute-forced in seconds, 64 in ~a GPU-year, 80 infeasible-ish
today, 128 the "heat death" line, 256 the quantum floor (Grover halves
it). Insiders: the boot-time entropy cliff (kernel 5.4-5.5 booting
faster than the pool fills — the getrandom-blocks race with systemd;
`random.trust_cpu=on` as the RDRAND gamble); jitter entropy's honest
status; hardware wallets derive keys from 256 bits harvested once and
must never expose the derivative. Lab: `cat /proc/sys/kernel/random/entropy_avail`
(legacy) vs `sudo dmesg | grep -i random` (modern "crng init done"), time
the difference, `time dd if=/dev/urandom bs=1M count=64 | sha256sum`,
python `secrets` vs `random` (document `random`'s Mersenne Twister state
recoverable from 624 outputs — a real attack), generate 10k
`random.random()` values in two processes started in the same second and
show identical streams (the PID+time seed trap), then with
`random.SystemRandom` show independence.

**Chapter 02 — symmetric.** Derive the block cipher as a permutation
family: {0,1}^128 → {0,1}^128 keyed permutation, 2^128 keys, the
confusion/diffusion vocabulary translated to memory terms (confusion =
byte-level value scatter, diffusion = one input bit flips half the output
bits). AES anatomy honestly at the byte level: 4×4 state matrix over
GF(2^8), SubBytes (S-box as inverse-in-GF + affine), ShiftRows,
MixColumns (the circulant matrix [2 3 1 1] multiply in GF(2^8), one column
in = one column out), AddRoundKey; 10/12/14 rounds for 128/192/256-bit
keys; the key schedule (RotWord, SubWord, round constants — and the
related-key attacks that punished AES-256's schedule, 2009 Biryukov-
Khovratovich, theoretical but real). The round count folklore (why not
20 rounds? margin vs speed; "2 rounds to break, 20 rounds of margin"
style). The performance story as a memory story: table implementations
(T-tables: 4×256 32-bit entries, 4 KB total) were the fast path until
cache timing attacks (Bernstein 2005) forced bitsliced/AESENI paths;
AES-NI (2008 Westmere+): the `AESENC` instruction does a round in ~1
cycle latency, pipelined ~0.6 cycles/byte; the "AES-GC at 10+ GB/s"
claims are AES-NI pipelining, compare T-table ~1.5 GB/s and bitsliced
~0.7-1 GB/s. **Then the modes — the actual point.** ECB: draw the
ECB-penguin (Tux image in ECB — identical plaintext blocks → identical
ciphertext blocks; the pattern leak), why "ECB mode" is a synonym for
broken. CBC: chaining, IV requirements (unpredictable, not just unique —
the BEAST attack as why), padding (PKCS#7: pad byte value = pad length
1..16) and the padding-oracle birth defect that CBC carries (section 05
gets the full Lucky 13 story; plant it here). CTR: the counter as a
keystream offset, turns the block cipher into a stream cipher, random
access, parallel — and the **nonce discipline**: the (key, nonce) pair
must NEVER repeat, derive why from XOR of two ciphertexts under the same
keystream (c1⊕c2 = p1⊕p2 — the two-time pad); real incidents: the
PS3's fixed ECDSA nonce (that's ch03 but the same class), WinZip AE-2,
and importantly **the 56-bit nonce trap**: GCM's 96-bit nonce means
2^32 encryptions under one key already risk collision (birthday: n^2/2^96
≈ 0.06% at 2^33 — do the arithmetic inline). GCM: CTR mode + GHASH
authentication; GHASH is multiplication in GF(2^128) (the 128-bit block
interpreted as a polynomial, carry-less multiply `PCLMULQDQ` 2010+),
the tag = encrypt(J0)⊕GHASH(A, C); **the catastrophic failure mode**:
nonce reuse in GCM does not just leak plaintext — it reveals the
authentication key H (the forgery-forever result, "authentication key
recovery from a single reused nonce" — Joux 2006 forbidden attack).
Polynomial evaluation honesty: GHASH is polynomial hashing over the
ciphertext, so (tag forgery probability degrades as (#blocks)^2/2^128 —
the ~64 GiB per-key guidance in GCM (NIST: 2^39 bits per key
guidance — state it as ~64 GiB and say why). AEAD as the concept that
ended the footgun era: one API, nonce in, ciphertext+tag out, no
composition choices left to application developers (ChaCha20-Poly1305 as
the software-only alternative: ARX construction — add/rotate/xor, no
table lookups at all, constant-time by construction, the TLS 1.3
mandatory suite for software paths; the ChaCha quarter-round drawn).
Wire the whole chapter to memory: cipher state is 16 bytes — the cache
line; T-tables evict it; nonce storage is the application's heap
discipline (the counter's persistence is a memory bug class). Insiders:
disk encryption uses XTS not GCM (wide-block, no nonce — position
derives the tweak; why authentication is impossible on-sector — the
512-byte sector can't grow a tag), keys derived per-use with HKDF, and
"rotate the key before 2^20 calls in some libs" folklore vs the real
2^32/2^39 limits. Lab: encrypt a BMP with AES-256-ECB vs CBC vs GCM
(python `cryptography`) and view as image (the penguin visible in ECB),
flip one bit in a CBC ciphertext block and watch the next block
garble (propagation property), flip a bit in a GCM ciphertext and
watch the tag reject it (auth), reuse a CTR nonce twice and recover
p1⊕p2 by XOR, then (if p1 is known) recover the keystream and decrypt
p2 — the two-time pad performed live.

**Chapter 03 — public key.** The trapdoor idea from first principles:
one-way functions (multiply two 300-digit primes → factoring back is
infeasible; exponentiation in a finite group vs discrete log). RSA
derived: p, q, n=pq, φ(n)=(p−1)(q−1), e·d ≡ 1 mod φ(n), m^ed ≡ m mod n
(prove the congruence via Fermat — the one honest derivation this
chapter must carry, three lines of CRT). **The bignum memory layout —
the chapter's core novelty:** a 2048-bit RSA key is 256-byte numbers
(big-endian, OpenSSL `BIGNUM` = {d, top, dmax, neg, flags} array of
64-bit limbs, top = current length); the *variable length is the leak*
(constant-time discipline = fixed-length loop over dmax, always-touching
arrays); modular exponentiation as the hot loop (square-and-multiply,
windowed with precomputed powers — the table's access index leaks key
bits, which is why blinding exists). CRT-RSA (the practical
implementation): m_p = m^(d mod p−1) mod p, m_q similarly, recombine
via Garner's formula (m = m_p + p·(inv_p·(m_q−m_p) mod p)) — a 4×
speedup and the **Bellcore fault attack** (a faulted m_p with a valid
signature reveals the factorization: gcd(m′^e − m, n) = p — derive this
inline; countermeasure: verify before output, cost ~one extra exponentiation,
Rowhammer in module 02 makes this not-theoretical). ECC: the curve
picture (y² = x³ + ax + b over F_p), point addition geometric rule,
scalar multiplication as double-and-add — the SAME square-and-multiply
shape; the group order ~2^256 for P-256; why smaller numbers (~32 bytes
vs 256) give equal security (generic attacks √n: Pollard rho ~2^128
operations). Curve25519 as the design that fixed the implementation
footguns (Montgomery ladder — the SAME operation sequence regardless of
the key bit (constant-time by algorithm shape, the ladder's fixed
addition pattern drawn), no secret-dependent branches, 2^251 field prime
making reduction branch-free, the "design for constant-time or die"
philosophy — Bernstein's "safe curves" criteria). ECDSA: r, s = k^-1(H
+ d·r) mod n; **the nonce k must be uniformly random and NEVER reused
or even biased** — the Sony PS3 (fixed k, 2010, one pair of signatures
factors the key) and the Android/Bitcoin ECDSA nonce bias via a broken
RNG (2013: repeated k from PRNG state collisions) worked as
d = (s·k − H)/r — derive the recovery formula inline; RFC 6979
deterministic k = HMAC(d, H) as the fix (the insight: deterministic
beats random when random is your attack surface). Side-channel catalog
tied to memory: timing (variable-length loops), cache (table index
leaks limb values; the OpenSSL RSA cache-timing proof, Tromer 2010
~1024-bit key in ~100 ms of adjacent-VM noise), power/EM (on devices —
connect module 20 embedded), Rowhammer (module 02's flip as key
corruption), Spectre (module 16's sandbox cross — speculative reads of
bignum limbs). Blinding: m′ = m·r^e, sign, divide by r — the
timing-randomizer (with inv r precomputed); the cost accounting
(~20% + precompute). Key storage: keys in process memory are game over
(the Heartbleed read-below, cold boot attacks — DRAM's ~seconds-to-minutes
decay at room temp, module 01 ch01's capacitor again; key wrap
hierarchies: the root key never leaves the TPM/secure enclave — connect
module 05/06 hardware roots). Insiders: OpenSSL `BN_FLG_CONSTTIME`
exists and is a *flag you must know to set* in some paths (the
BN_mod_exp on secret exponents); JWKS/PEM/DER as the key serialization
formats (DER lengths leak nothing but the parsing bugs are historic
ASN.1 minefields); "constant-time validation" libraries
(cryptofuzz/ctgrind/dudect as the testing approach). Lab: generate RSA
keys, dissect a PEM (openssl asn1parse — the ASN.1 tree of
RSAPrivateKey: version, n, e, d, p, q...), time `openssl speed rsa2048`
vs `ecdsa`/`ed25519`, use python `gmpy2` to CRT-verify Garner
recombination numerically, demonstrate the Bellcore gcd attack
numerically on a toy 64-bit "faulted" signature, and demonstrate ECDSA
nonce reuse: take two signatures with the same r, recover d by the
formula (do it in ~20 lines of python on toy parameters).

**Chapter 04 — TLS 1.3.** The protocol as a lesson in memory hygiene:
every design decision in RFC 8446 exists because something in TLS ≤1.2
leaked or enabled memory mistakes. Start with the threat model
(the attacker: MitM, record injection, replay, truncation; NOT
quantum — state that honestly and plant the hybrid PQC footnote:
X25519Kyber768 shipped in browsers 2023-2024, the Chrome+Cloudflare
deployment numbers). The handshake drawn message by message:
ClientHello (key_share X25519 + supported_versions + the middlebox
compatibility dance — the dummy ChangeCipherSpec, why it exists:
~2-8% of middleboxes broke on a clean 1.3, so the wire lies), ServerHello
+ EncryptedExtensions + Certificate + CertificateVerify + Finished — all
encrypted under the handshake secret from the first flight (the 1-RTT
win vs 1.2's 2-RTT, drawn as timeline diagrams). The key schedule as a
hash-chain over memory: HKDF-Extract(CH'||SH) → early secret →
HKDF-Extract(DHE) → handshake secret → HKDF-Extract(Finished) → master
→ traffic keys per direction (derive labels; the "transcript hash" —
every message folded into the running hash — is what makes the
handshake tamper-evident; show the transcript as an append-only log
with a rolling digest: a memory data structure, not just a formula).
**Forward secrecy as key erasure** — the chapter's core reframe:
session keys deleted on schedule mean past captures stay dead; DHE per
connection (vs RSA key transport in 1.2 where one compromised server
key decrypts all history); the erasure points (private key erased
after handshake, resumption secrets have lifetime policies); connect to
module 02 (session keys are the crown-jewel bytes in process memory —
Heartbleed leaked exactly these). 0-RTT: the replay trade derived (the
early data is encrypted under a PSK from a previous session — safe
against passive capture, replayable by an active attacker; the
application-must-decide rule: idempotent GET fine, POST not; the
Chrome deployment statistics; anti-replay windows in the
spec's appendix). Resumption/PSK (ticket obfuscation, ticket key
rotation on the server as its own key-erasure discipline); session
tickets in 1.2 as the forward-secrecy hole (the ticket encrypted under
a long-term key — if the ticket key leaks, the captured sessions
resurface; connect to the real "ticket key in shared memory" CDN
architectures). Record layer as framing: 5-byte TLS header, record ≤16
KiB, the type byte, AEAD nonce = static_iv XOR seq_num (the sequence
number as an implicit memory structure — the (nonce, key) reuse
discipline from ch02 now lives per-connection; the nonce never
transmitted, reconstructed from the counter — if the counter
desyncs, catastrophic); decryption order (decrypt-then-MAC vs
MAC-then-encrypt: 1.3's encrypt-then-auth ordering kills the padding
oracle class by construction — the Lucky 13 root cause killed
structurally, not by patching: the design lesson — *remove the bug
class, don't fix the instance*). The downgrade dance: 1.3 hides the
version inside encrypted extensions + the downgrade sentinel in
ServerHello.random (last 8 bytes "DOWNGRD" — the honest anti-evil-ma
mechanism). Certificate chain verification as a memory walk (the chain
in the handshake, ~3-5 certs, each ~1-2 KB; transparency logs (SCTs)
and the client's local state as a revocation memory — CRLs dead,
OCSP dead, OCSP stapling + must-staple as the compromise; the
Certificate Transparency log structure: append-only Merkle tree — a
memory data structure again, connect module 14's journaling intuition).
Connection closing and half-close semantics (close_notify as graceful
truncation detection — without it, truncation attacks work: the
"connection was fine, data just ends" attack). Insiders: the
middlebox-compatibility mode's ChangeCipherSpec spam between 1st and
2nd ClientHello flight; ECH (encrypted ClientHello) shipping 2024-2025
as the SNI-hiding extension (the outer/inner ClientHello); the
`ssl_key_log` file (SSLKEYLOGFILE) — the debugging key-erasure
backdoor everyone runs in CI (and why production must never); the
"TLS 1.3 in 30 seconds" mnemonic: hello → key share → transcript →
erase → MAC. Lab: DevTools Security panel on a real site (1.3,
cipher suites X25519+AES-GCM/ChaCha20), `openssl s_client -connect
... -tls1_3 -msg 2>&1 | head -60` read the flight structure, capture
with the key log (`SSLKEYLOGFILE=/tmp/keys wireshark` — dissect a real
handshake, find the ClientHello key_share, the transcript, the
Finished MAC), and demonstrate replay: serve 0-RTT data with a
python asyncio snippet and replay the flight (or document the paper
version).

**Chapter 05 — the failure catalog.** Every famous cryptographic failure
told once, as a memory-model story, with the mechanism drawn at byte
level. Heartbleed (2014, CVE-2014-0160): the TLS keepalive heartbeat
echoed `payload_length` (2 bytes, attacker-controlled up to 64 KiB)
without validating it against the actual record — the server
`memcpy`s up to 64 KiB past the heartbeat message: a **heap
over-read**; what leaked: session keys (the crown jewels), private
keys, user passwords in request buffers; the memory-model analysis:
reading uninitialized/stale heap memory = the complement of
use-after-free — connect to module 02 ch05's heap layout (what's in
the adjacent freed/served chunks); the fix's triviality (bounds check)
vs the ecosystem cost (~17% of secure web servers, months of
certificate re-issuance); the lesson: *the protocol's state machine
must never trust a length field it doesn't own* — TLV framing bugs
are the class. Lucky 13 (2013): CBC MAC-then-encrypt padding oracle
IN TLS — the server's decrypt-then-check sequence (remove padding →
check MAC) with timing differences of ~1-2 µs per padding length
(guess more padding = less MAC to verify = faster reject) — the oracle
amplified over LAN; the patch (constant-time padding checks in
OpenSSL — treating all paddings alike in dummy operations) vs the
structural fix (1.3's encrypt-then-MAC ordering — ch04's argument
revisited). Nonce-reuse incidents catalog (the two-time pad of ch02
in the wild): the NSA-era "IV reuse in MS-CHAPv2" class, WPA2-GCMP
rekeying failure (KRACK 2017 — the replay of handshake message 3
resetting the nonce counter to zero: the IV/nonce counter as process
state that must survive retransmission logic), the PS3 fixed-k ECDSA
(ch03 planted), the Cerberus/Ansible VPN nonce incident (2020, the
`--uniqueids` bug class), and the AES-GCM nonce collision in a cloud
KMS reported responsibly (bug class: counter initialized from
timestamp+machine-id — the "entropy at rest" trap). Side channels as
memory reads across trust boundaries: Bernstein's remote cache
timing (2004, AES T-table index → cache line occupancy → timing; the
 Bernstein paper as the origin of the whole subfield), Spectre V1 as
the sandbox-crossing read (module 16 ch05's browser chapter; branch
prediction as a cache polluter), Rowhammer on RSA keys (module 02's
hammer flips a key bit → the Bellcore fault attack of ch03 becomes
practical; the 2021 CISA-era firmware warnings), DRAM cold-boot (the
2011 "Lest We Remember" Princeton study — cooling spray, FROZEN
attacks, keys scavenged from decayed-but-recoverable DRAM: the
grounded capacitor from module 01 ch01 with the ~seconds-minutes
decay constant at 55°C vs −50°C drawn as a table). The
implementation-bug genus: ASN.1/DER parsing (the "binary parsing is
where bugs live" rule, the Bleichenbacher family: PKCS#1 v1.5
padding oracles 1998 + the 2017 ROBOT return — 19 years of the same
bug class surviving in implementations; the million-message oracle's
arithmetic sketched); timing-safe comparison (`CLOCK-
constant-time memcmp` — why `==` on a MAC leaks length-of-match;
dudect/ctgrind as the tooling). The RNG failure genus: Debian
(ch01 planted, expanded here), the 2012 Android/Bitcoin ECDSA
(ch03 planted), the "duplicate SSH host keys at boot in
virtualized fleets" (entropy starvation, ch01's cloud story's
incident). What the catalog teaches — the meta-lesson: primitives
rarely fail (AES unbroken since 2001's Rijndael selection; RSA
factorization record = 829 bits, 2020, ~2700 CPU-core-years), **the
implementation memory discipline is where every real-world break
lived**; draw the layer stack (primitive → mode → implementation →
protocol → key management) with failure counts per layer from this
chapter's catalog (a table: layer, incidents, the fix that finally
worked). Insiders: "don't roll your own crypto" decoded honestly
(it's not about the math — it's this chapter: the discipline of
constant time, nonce state, parsing, and key erasure is invisible in
code review and only visible in adversarial testing); the
constant-time patch pipeline in OpenSSL (security-level flag
`SECLEVEL`, the CT validation harnesses); the nsec/nousage
post-quantum migration timeline (NIST 2024: ML-KEM standardized;
the "harvest-now-decrypt-later" threat arithmetic: data lifetimes >
10 years → migrate now; SSH/YubiKey migrations 2024-2025).
Lab: reproduce the two-time pad live (two messages, one CTR nonce —
recover the XOR and the plaintext), build a timing oracle in
python (compare HMAC check times on full vs first-byte mismatch
over 10k samples — plot the microsecond distributions), run
`ctgrind`/`dudect`-style variance check on a toy implementation
(a python `timeit` harness with statistically honest aggregation),
scan a target with `sslscan`/`testssl.sh` for the historical
weaknesses (CBC in 1.2, RC4, no FS), and use
heartbleed's public PoC logic on a deliberately-vulnerable local
echo server you write in 30 lines (the missing bounds check
reproduced, /proc/self/maps adjacency inspected — or the honest
paper version with the byte-level diagram).

**Connections requirement.** Every chapter's Connections section must
include (in addition to same-module links): ch01 → module 17 ch01
(metastability as entropy), module 15 ch04 (VM clones and entropy),
ch02 → module 01 ch02 (cache line as cipher state), module 16 ch05
(Spectre reads cipher tables); ch03 → module 01 ch05 (bignum as heap
object), module 02 ch03-05 (Rowhammer, UAF on keys), module 04 ch03
(compiler idioms in crypto code); ch04 → module 13 ch03 (TCP buffers
under TLS records — **this link is mandatory: it repairs m13 ch03's
orphan status**), module 01 ch23 (the grand table's "who owns key
material"); ch05 → module 02 (the whole sibling), module 16 ch04/05
(Heartbleed's heap, Spectre's browser), module 01 ch13 (C#/
managed-code crypto arrays and the GC pause leak — **mandatory:
repairs m01 ch13's orphan status**), module 01 ch16 (Swift ARC on key
buffers — **mandatory: repairs m01 ch16's orphan status**).

**The final exam** (`quiz/final-exam.md`): 12 questions, worked
answers, the module's chapters one part each (3+3+2+2+2), predict/
diagnose/derive styles only, at least one question must make the
student compute a birthday-collision probability and at least one
must ask which TLS 1.3 design decision kills which historical attack
(the mapping questions are this module's soul).
