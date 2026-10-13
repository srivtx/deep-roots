# Chapter 02 — AES and modes: the symmetric state machine

> **The 30-second version.** A block cipher is a keyed permutation —
> 128 bits in, 128 bits out — and its 16-byte state is the unit memory
> cares about: a quarter of a cache line. AES runs 10-14 rounds of byte
> arithmetic over GF(2^8); for a decade its fastest implementation was
> 4 KB of lookup tables whose cache-line occupancy leaked the key, until
> AES-NI collapsed one round into one instruction. The primitive was
> never the hard part — the *mode* is where systems broke: ECB leaks
> structure (the penguin), CBC carries a padding oracle by birth, CTR is
> a stream cipher that dies on one repeated nonce, and GCM turns that
> same repetition into permanent forgery.

## A permutation you cannot invert: 2^128 doors over 16 bytes

A block cipher is a family of permutations: each key selects one
bijection {0,1}^128 → {0,1}^128, and AES-128 has 2^128 keys — 2^128
doors, and you lose the map. Without the key, output is indistinguishable
from a random permutation: flip one input bit and each output bit changes
with probability ≈ 1/2. That is the confusion/diffusion pair in memory
terms. **Confusion** is byte-level value scatter — a position's byte
emerges statistically unrelated to the input byte, so value pattern
matching fails. **Diffusion** is position scatter — one input bit's
influence spreads until every output byte depends on every input bit.

AES works on a 4×4 byte matrix over GF(2^8) (input fills
column-by-column) and repeats one round structure:

```
   state (16 bytes, column-major)         one round, 10/12/14 times:
   s0  s4  s8  s12                        SubBytes     value scatter
   s1  s5  s9  s13                        ShiftRows    position spread
   s2  s6  s10 s14                        MixColumns   column blend
   s3  s7  s11 s15                        AddRoundKey  XOR 16-byte key
   (input fills column-by-column;         final round omits MixColumns)
```

SubBytes is the nonlinearity: multiplicative inverse in GF(2^8) plus an
affine bit-mix that kills fixed points. ShiftRows slides row r left by r
so a column's bytes scatter across four columns. MixColumns multiplies
each column by the circulant matrix [2 3 1 1] — one column in, one
column out, every output byte a GF(2^8) combination of all four input
bytes (b0 = 2·a0 ⊕ 3·a1 ⊕ 1·a2 ⊕ 1·a3, rotated for b1..b3). AddRoundKey
XORs in a 16-byte round key. Rounds: 10/12/14 for 128/192/256-bit keys;
the final round drops MixColumns so decryption mirrors encryption (hence
AESENCLAST).

Trace one flipped input bit through a round: SubBytes keeps it in its
byte, ShiftRows slides that byte to a new column, MixColumns spreads it
into all 4 bytes of that column — 4 bytes differ after one round, all 16
after two. The other 8-12 rounds are margin, and the round-count
folklore is honest: attacks reach ~7 of 10 rounds at impractical cost
(bicliques shave brute force to 2^126.1), so 10 rounds buy ~3 of slack
at ~30% speed cost; 20 would buy nothing measurable and halve
throughput — margin calculus, not fear.

The round keys come from the **key schedule**: 4-byte words expanded by
RotWord, SubWord (S-box each byte), and XOR with Rcon (powers of x in
GF(2^8)); AES-128 stretches 4 key words into 44 — 11 round keys, 176
bytes. AES-256 stretches 8 words over 14 rounds, and the schedule's slow
diffusion is the wall that fell: Biryukov-Khovratovich (2009) built a
related-key attack on full AES-256 in ~2^99 time (vs 2^256 brute force).
It needs encryptions under *related* keys — chosen differences that real
protocols never hand out — so it is theoretical, but it exposed the
schedule, not the round function, as AES-256's weakest wall.

## The implementation is the attack surface: 4 KB of tables, one instruction

For twenty years the fast AES was table-based: fuse SubBytes, ShiftRows,
MixColumns into four tables T0..T3 of 256 32-bit entries — 4×256×4 =
4,096 bytes, 64 cache lines; one round is four lookups plus XORs per
column. Then Bernstein (2005) timed OpenSSL's AES *remotely, over a
network* and recovered complete AES-128 keys. The mechanism is pure
module 01 chapter 02: the table index is a state byte, plaintext⊕key-
dependent after round 0, so line residency is a function of key material
and timing measures it — the lookup address is the leak; the cache line
is the microphone. The tables also evict the cipher's own state: 16
bytes that should live in registers fight 16 hot lines every round.

The software fix was bitslicing — constant-time, ~0.7-1 GB/s, ~2×
slower than the ~1.5 GB/s cache-miss-bound tables. The structural fix
was silicon: **AES-NI** (announced 2008, shipped with Westmere 2010) —
AESENC does SubBytes+ShiftRows+MixColumns+AddRoundKey in one instruction,
AESKEYGENASSIST builds the schedule. No table, no branch; the state never
leaves the vector unit. Latency ~4 cycles, but independent blocks
(CTR/GCM) pipeline, so a steady stream retires ~1 round/cycle: 10 rounds
per 16-byte block ≈ **0.63 cycles/byte** for AES-128 (0.88 for AES-256's
14). Measured on this chapter's sandbox (Xeon with AES-NI, OpenSSL 3.5.7,
16 KiB chunks): 14.6 GB/s for AES-128-GCM — the "AES-GCM at 10+ GB/s"
claims are real AES-NI pipelining (no VAES here; 2019+ cores double it;
ChaCha does 3.8). The instruction did not get faster — the memory traffic
got deleted. And module 16 chapter 05 adds the 2018 twist: Spectre's
speculative reads cross the language sandbox and touch the tables anyway
— why "no tables at all" is architecture, not optimization.

## Modes as state machines: the penguin and CBC's birth defect

ECB (electronic codebook) encrypts each block independently — but a
block cipher is a *deterministic* permutation, so **identical plaintext
blocks produce identical ciphertext blocks**: a 16-byte-aligned equality
oracle over your data. Encrypt a bitmap's pixel payload in ECB and view
it as an image: flat regions collapse to one repeated ciphertext block,
edges stay edges — the famous ECB penguin (the 2011 Linux-Tux demo).
"ECB mode" is a synonym for broken (only defensible for one block that
is itself uniformly random).

CBC makes each block mask the next: C_i = E_K(P_i ⊕ C_{i−1}), with the
IV playing C_0 — equal plaintexts at different positions XOR different
previous ciphertexts, and the penguin dissolves. But CBC carries two
birth defects. First, the IV must be **unpredictable, not merely
unique**. TLS 1.0 chained its IV — the last ciphertext block of record
n−1 opened record n — so the attacker saw the "IV" *before* choosing
the next plaintext, and BEAST (2011) turned that into a chosen-plaintext
game recovering session cookies byte by byte. A unique but predictable
IV keeps this attack alive: use 16 random bytes.

Second, padding. CBC needs a multiple of 16; PKCS#7 appends k bytes each
of value k, k ∈ 1..16 — a full block of 0x10 when aligned. The
**padding oracle** is the second birth defect: a server that decrypts,
then unpads, then MAC-checks answers differently for "bad padding"
versus "bad MAC" (error string, or timing — chapter 05's Lucky 13 is
1-2 µs). Submit C_{i−1}′‖C_i and the server reveals whether D(C_i) ⊕
C_{i−1}′ ends in valid padding: a few hundred queries per byte, and
Vaudenay (2002) decrypts any block. CBC also malleates predictably: flip
one bit in C_i and P_i becomes garbage while P_{i+1} = D(C_{i+1}) ⊕ C_i
flips *exactly that bit* — the propagation the lab shows.

## CTR: a stream cipher wearing a block cipher, and the two-time pad

CTR never runs the inverse cipher: the block cipher becomes a keystream
generator, block i's keystream = E_K over a counter block:

```
   CTR counter block (16 bytes) — GCM/AEAD layout:

   +-------- 96-bit nonce --------+----- 32-bit counter ----+
   | n0 n1 n2 n3 n4 n5 n6 n7 n8 n9 nA nB | c0 c1 c2 c3 |
   +---------------------------------------+---------------+
     identifies THIS message                 block index in
     (random or per-key counter)             this message

   ciphertext_i = plaintext_i XOR E_K(nonce || counter_i)
```

Gains: no padding, random access (decrypt block 10,001 alone — seek),
parallelism (independent blocks — why GCM runs at GB/s). The cost is one
rule: **the pair (key, nonce) must never repeat.** Derive why: same key
and nonce → same keystream KS; c1 = p1 ⊕ KS and c2 = p2 ⊕ KS, so

c1 ⊕ c2 = p1 ⊕ p2

the keystream cancels — the **two-time pad**. If p1 is known (HTTP
headers, file magic), KS = c1 ⊕ p1 and p2 = c2 ⊕ KS falls out
verbatim — the class of the PS3's fixed ECDSA nonce (chapter 03 derives
the recovery), WinZip AE-2's CTR (fixed zero counter block; keystream
uniqueness rests entirely on the salt), and chapter 05's cloud-KMS
collisions (timestamp+machine-id counters).

Random nonces have their own arithmetic — the chapter's mandatory
computation. A random 96-bit nonce samples *with replacement* from 2^96
values: after q encryptions there are q(q−1)/2 pairs, each colliding with
probability 2^−96:

- q = 2^32: pairs ≈ 2^63, P ≈ 2^63/2^96 = **2^−33 ≈ 1.2×10^−10** — NIST
  caps random-nonce GCM at 2^32 invocations per key for exactly this.
- q = 2^33: pairs ≈ 2^65, P ≈ 2^65/2^96 = **2^−31 ≈ 4.7×10^−10**.
  Doubling traffic quadruples the risk — the *quadratic* is the content
  of the guidance.
- The "0.06% at 2^33" figure you meet in reviews fails this arithmetic:
  0.06% ≈ 6×10^−4 belongs to q ≈ 2^43, where (2^43)²/2^96 = 2^−10 ≈
  0.1% (exact pairs: 0.05%); 50% collision arrives at q = 2^48.5 — a
  random 96-bit nonce carries only ~48 bits of birthday margin. Randomize
  just 56 of the 96 bits (device-ID and timestamp prefixes are the usual
  culprits) and the 50% horizon collapses to 2^28 messages.

A deterministic counter nonce has no birthday problem — it has a memory
problem: the counter is *state*, and its persistence is a bug class.
Crash, rollback, or a cloned VM (chapter 01's cloned-CRNG story) restarts
the counter and the first records reuse nonces exactly. Nonce storage is
heap discipline: durably advance the counter (fsync before reuse), or
derive a fresh key per boot with HKDF so old counter values cannot
collide.

## GCM, GHASH, and the software answer: ChaCha20-Poly1305

GCM is CTR plus a tag: H = E_K(0^128) is the authentication subkey, J0 =
nonce‖0^31‖1 the initial counter block (96-bit nonces are the fast path —
other lengths pay one extra GHASH), and

tag = E_K(J0) ⊕ GHASH_H(A, C)

GHASH interprets each 16-byte block as a polynomial with 128
coefficients over GF(2) and **multiplies in GF(2^128)** — carry-less
polynomial multiplication, one `PCLMULQDQ` (Westmere, 2010, same
generation as AES-NI) plus reduction mod x^128+x^7+x^2+x+1. GHASH
evaluates AAD, ciphertext, and length blocks chained by multiply-by-H,
and its provable forgery bound sets the per-key cap: advantage degrades
as (#blocks)²/2^128, so NIST caps a GCM key at 2^39−256 bits ≈ **64 GiB**
— 2^36 bytes = 2^32 blocks, keeping the quadratic term at
2^64/2^128 = 2^−64 even summed over a forger's billions of attempts (the
32-bit counter wraps at 2^32 blocks too: one 64 GiB number, two reasons).

**The catastrophic failure mode.** Reuse a GCM nonce and you do not
merely re-expose plaintexts through CTR's two-time pad — you hand over
the authentication key (Joux 2006, the "forbidden attack"). Same (key,
nonce) means same keystream *and* same J0, so both tags carry the same
E_K(J0) mask: T1 ⊕ T2 = GHASH(C1) ⊕ GHASH(C2), a polynomial in H whose
coefficients are the XOR-differences of the ciphertext blocks. Root-find
it in GF(2^128) — a few dozen multiplications — to recover H, then the
mask, then forge valid tags under that nonce, forever. The AES key stays
secret; the authenticated channel is dead.

AEAD is the concept that ended the footgun era: one API — seal(key,
nonce, plaintext, aad) → ciphertext‖tag; open() → plaintext or
`InvalidTag`. No IV choice, no padding, no MAC-then-encrypt composition
left to the developer — the 2000s' oracle plagues were composition bugs.
Its price is the **header tax**: an explicit 12-byte nonce plus 16-byte
tag = 28 bytes per message — 28/1400 = 2.0% on a 1,400-byte record, but
28/64 = 43.75% on a 64-byte RPC. Hence TLS 1.3's folded nonce (static IV
XOR sequence number, chapter 04) and 12-byte truncated tags (forgery
bound 2^−96 per attempt).

ChaCha20-Poly1305 is the software-only AEAD, mandatory in TLS 1.3 for
the no-AES-NI path. ChaCha is **ARX** — add, rotate, xor on 32-bit
words: no table, no secret-dependent memory address, constant-time *by
construction*. The 64-byte state is 16 words — 4 constants ("expa nd 3
2-by te k"), 8 key, 1 counter, 3 nonce (IETF layout) — mixed by 20
rounds of quarter-rounds:

```
quarter-round on words a, b, c, d (32-bit, += is mod 2^32):

   a += b;   d ^= a;   d <<<= 16;
   c += d;   b ^= c;   b <<<= 12;
   a += b;   d ^= a;   d <<<= 8;
   c += d;   b ^= c;   b <<<= 7;
```

Poly1305 is a polynomial MAC over the integers mod 2^130−5, keyed
one-time from the keystream's first 32 bytes — same nonce discipline,
same birthday arithmetic. Without AES instructions ChaCha wins; with
them AES-GCM does (~3.8 vs 14.6 GB/s here) — except at 16-byte payloads,
where setup cost dominates and ChaCha wins even on this Xeon (199 vs
112 MB/s).

## What insiders know

- **Disks use XTS, not GCM.** The tweak is the sector number (encrypted
  under a second key, multiplied by successive powers of α per block) —
  position derives the tweak, no nonce to repeat, and one flipped bit
  garbles exactly one 16-byte plaintext block (deliberate damage
  localization; not truly wide-block). Authentication is impossible per
  sector: sectors cannot grow a 16-byte tag, and a torn write on power
  loss would fail the tag and brick the disk.
- **HKDF is key hygiene.** Never one raw key in two roles; derive per-use
  subkeys: HKDF-Expand(master, "record-traffic", 32) — TLS 1.3's whole
  schedule (chapter 04) is this rule industrialized.
- **Rotation folklore vs the real limits.** Some vendor docs say "rotate
  AES-GCM keys before 2^20 calls" — overshooting NIST's actual limits
  (2^32 random-nonce invocations, 2^39−256 bits of data) by ~2^12; the
  2^20 cargo cult is 64-bit-block birthday fear (Sweet32) laundered into
  AES policy.
- **AES-NI bookkeeping.** The whole AES-128 schedule is 176 bytes — 11
  round keys in registers; AESKEYGENASSIST runs once per key; AESENCLAST
  exists for the MixColumns-free final round.
- **The 96-bit nonce is GCM's fast path** — any other nonce length costs
  an extra GHASH. Durable counter nonces beat random ones whenever the
  counter can survive crashes.

## See it yourself

All parts verified on python `cryptography` 44.0.3, CPython 3.12.

**Part 1 — the penguin, quantified.** A 24-bit BMP with flat regions,
pixel payload encrypted three ways (CBC's extra PKCS#7 block and GCM's
tag are dropped from the visuals):

```python
# penguin.py — 256x256 BMP, 768 B/row: no BMP row padding interferes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
import os, struct
W = H = 256
def bmp(px):
    return (b'BM' + struct.pack('<IHHI', 54+len(px), 0, 0, 54)
          + struct.pack('<IiiHHIIiiII', 40, W, H, 1, 24, 0, 0, 0, 0, 0, 0)
          + bytes(px))
px = bytearray(W*H*3)
for y in range(H):                     # big flat regions: body, belly, sky
    for x in range(W):
        i = 3*(y*W+x)
        if   y > 200 and 80 < x < 176:  px[i:i+3] = b'\x10\x10\x10'
        elif y > 150 and 104 < x < 152: px[i:i+3] = b'\xf0\xf0\xf0'
        else:                           px[i:i+3] = b'\x30\x90\xd0'
key, iv, nonce = os.urandom(32), os.urandom(16), os.urandom(12)
ecb = Cipher(algorithms.AES(key), modes.ECB()).encryptor().update(px)
pad = PKCS7(128).padder()
cbc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor().update(
      pad.update(px) + pad.finalize())
enc = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
gcm = enc.update(px) + enc.finalize()
for name, out in [('ecb', ecb), ('cbc', cbc[:W*H*3]), ('gcm', gcm)]:
    open(f'penguin_{name}.bmp','wb').write(bmp(out))  # view with any viewer
print('ECB distinct blocks:', len({ecb[i:i+16] for i in range(0,W*H*3,16)}))
print('CBC distinct blocks:', len({cbc[i:i+16] for i in range(0,W*H*3,16)}))
```

```console
$ python3 penguin.py
ECB distinct blocks: 8 of 12288
CBC distinct blocks: 12288 of 12288
```

View `penguin_ecb.bmp` (eog/feh): the shapes stare back — the sky
collapsed to a handful of repeated blocks; CBC and GCM are static.

**Part 2 — CBC bit propagation.** Flip one bit of ciphertext block 0:

```python
dec0 = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
clean = dec0.update(cbc) + dec0.finalize()
ct = bytearray(iv + cbc)
ct[16+3] ^= 0x01                      # bit 0 of block 0's byte 3
dec = Cipher(algorithms.AES(key), modes.CBC(bytes(ct[:16]))).decryptor()
pt = dec.update(bytes(ct[16:])) + dec.finalize()
for b in range(3):
    print(f'block {b}:', sum(1 for a, o in
          zip(clean[16*b:16*b+16], pt[16*b:16*b+16]) if a != o), '/16 differ')
```

```console
block 0: 16 /16 differ     <- avalanche: D(C0) destroyed
block 1: 1 /16 differ      <- P1 = D(C1) XOR C0: exactly your bit
block 2: 0 /16 differ      <- untouched: blocks decrypt independently
```

The block-1 diff byte is 0x01 — your flip, passed through — and the
padding block is untouched, so unpadding still succeeds: exactly the
surgical influence padding oracles replay per byte.

**Part 3 — GCM rejects the same flip.** `bad = bytearray(gcm); bad[0] ^=
0x01`, then decrypt with `modes.GCM(nonce, enc.tag)` — `finalize()`
raises `cryptography.exceptions.InvalidTag`.

**Part 4 — the two-time pad, live.** In python `cryptography`, the CTR
"nonce" is the whole 16-byte initial counter block — reuse it and you
reuse the keystream:

```python
ctr = b'\x01' + b'\x00'*15
p1 = b'GET /admin HTTP/1.1\r\nHost: api\r\n\r\n'   # attacker knows p1
p2 = b'the launch code is 8Z4-77-QQQ\r\n'         # the secret
c1 = Cipher(algorithms.AES(key), modes.CTR(ctr)).encryptor().update(p1)
c2 = Cipher(algorithms.AES(key), modes.CTR(ctr)).encryptor().update(p2)
ks = bytes(a ^ b for a, b in zip(c1, p1))        # keystream from known p1
print('c1^c2 =', bytes(a ^ b for a, b in zip(c1, c2)))
print('p2    =', bytes(a ^ b for a, b in zip(c2, ks)))
```

```console
c1^c2 = b'3-1\x00C\x00\x11\x03\n\x06\x00+;05\x0fX]\x115P|BDC\x17q0!d\x07'
p2    = b'the launch code is 8Z4-77-QQQ\r\n'
```

English⊕English shows structure in the XOR; known p1 hands over the
keystream, and p2 falls out verbatim.

## How to use this knowledge

- **Design review in three questions.** Which mode? Where does the nonce
  come from and where does it persist? What caps usage per key? "AES"
  without a mode goes back to the author; a diff containing `modes.ECB`
  gets blocked with the penguin attached.
- **Incident triage.** Identical 16-byte ciphertext blocks across rows →
  ECB. An `InvalidTag` storm right after a restart → the nonce counter
  rolled back: read the persistence code first, the crypto last.
  Partly-readable XORs in a capture → reused nonce; if GCM, treat H as
  recovered and rotate everything.
- **Capacity math.** One GCM key buys ~64 GiB — ~7 seconds at 10 GB/s,
  ~2 days at 400 MB/s. Rekey intervals fall out of arithmetic, and
  BEAST/Lucky 13/KRACK all read as mode-state failures, not math breaks.

## Connections

- **Module 18 chapter 01** — CTR_DRBG is AES-CTR with a lab coat:
  state = (key, counter); reseed = the per-key limits, applied to
  randomness.
- **Module 18 chapter 03** — the PS3's fixed ECDSA nonce: same
  one-time-value class; constant-time returns as bignum blinding.
- **Module 18 chapter 04** — TLS 1.3's nonce = static_iv ⊕ sequence
  number (this chapter's counter block in production); HKDF traffic keys;
  the 64 GiB budget.
- **Module 18 chapter 05** — Lucky 13 is section 3's birth defect grown
  into a TLS attack; the nonce-reuse catalog; Bernstein 2005 expanded.
- **Module 01 chapter 02** — the cache line as cipher state: 16 bytes
  are a quarter of a line, the T-tables 64 of them; that chapter's
  locality loophole is the side channel.
- **Module 16 chapter 05** — Spectre reads cipher tables across the
  renderer sandbox; why table-free AES-NI/ARX is structure, not speed.

## Quiz

1. **Concept.** Define confusion and diffusion as memory properties,
   give the one-measurement test for diffusion, and trace a flipped
   plaintext bit through one AES round: how many state bytes differ,
   and why does round two reach all 16?
2. **Concept.** Why must the CBC IV be unpredictable, not merely unique?
   Describe the attack a unique-but-predictable IV enables — the one
   BEAST executed against TLS 1.0.
3. **Derive.** A service seals 2^33 records under one AES-GCM key, fresh
   random 96-bit nonce each. (a) Compute the nonce-collision probability.
   (b) At what record count does it reach ~50%? (c) A review claims
   "0.06% at 2^33" — what does the arithmetic say, and where does 0.06%
   belong?
4. **Predict the number.** A 64-byte RPC and a 1,400-byte record are
   sealed with AES-GCM (explicit 12-byte nonce, 16-byte tag). Compute
   the header tax for each — and for the 1,400-byte record with the
   nonce folded into per-connection state, as TLS 1.3 does.
5. **Derive.** An unauthenticated 3-block CTR ciphertext has the
   character `0` (0x30) at plaintext byte 17. The attacker flips bit
   0x01 of ciphertext byte 17. Predict exactly which plaintext bytes
   change, what the receiver parses, and why the same flip under GCM
   dies at the tag.
6. **Bug diagnosis.** Two database rows hold byte-identical 16-byte
   ciphertext blocks in the encrypted `last_name` column; ~400 distinct
   blocks cover the whole table. What mode is this, what can an
   attacker recover without touching the key, and what is the fix?
7. **Bug diagnosis.** A gateway builds GCM nonces as machine_id (32) ‖
   boot_epoch_seconds (32) ‖ counter (32). After a crash it restarts
   within the same second and the counter resets to zero, so two early
   records share a (key, nonce). Name the failure class, what the
   attacker now holds, and two fixes.
8. **Cross-chapter.** Module 01 chapter 02 made the 64-byte cache line
   the atom of the hierarchy; module 16 chapter 05 showed Spectre
   reading across the browser sandbox. Explain how 4 KB of T-tables
   turn those two facts into a remote key-recovery channel — and the
   one design property of AES-NI and ChaCha20 that deletes the class.

<details>
<summary>Answers</summary>

1. Confusion is value scatter — a position's byte emerges statistically
   unrelated to the input byte, so value pattern matching fails.
   Diffusion is position scatter — one input bit's effect reaches every
   output bit. Test: flip one plaintext bit, XOR the two ciphertexts,
   count differing bits — ≈ 64 of 128. One round: SubBytes keeps the
   bit's byte in place, ShiftRows slides it to a new column, MixColumns
   mixes that column — **4 bytes of one column differ**. Round two:
   those 4 bytes sit in 4 rows, ShiftRows spreads them to 4 columns,
   MixColumns reaches **all 16**.
2. An IV known before the plaintext is chosen lets the attacker aim the
   first block: craft P₁ so P₁ ⊕ IV places a guessed byte at the target
   position, and compare the emitted E_K(P₁ ⊕ IV) against a captured
   target block. TLS 1.0's chained IV made the guess testable before
   submission, and BEAST recovered session cookies byte by byte. Random
   IVs make aiming impossible; uniqueness alone does not.
3. (a) Pairs = q(q−1)/2 ≈ 2^65, each colliding with probability 2^−96:
   P ≈ 2^65/2^96 = 2^−31 ≈ **4.7×10^−10** (one in ~2.1 billion).
   (b) q²/2^97 ≈ 1/2 → q ≈ 2^48.5 ≈ **3.9×10^14** records.
   (c) At 2^33 the risk is 0.00000005%, not 0.06%. 0.06% ≈ 6×10^−4 needs
   q²/2^96 = 6×10^−4, so q ≈ 2^42.6 — the ~2^43 regime (pairs 0.05%,
   square 0.1%). The lesson: collision probability *squares* with usage.
4. Nonce + tag = 12 + 16 = 28 bytes. 64-byte RPC: 28/64 = **43.75%**.
   1,400-byte record: 28/1400 = **2.0%**. With the nonce implicit
   (static_iv ⊕ per-record sequence number, never transmitted), only the
   tag ships: 16/1400 = **1.1%**. Small messages pay the AEAD tax
   hardest.
5. CTR keystream is position-aligned: byte 17 is block 1 (bytes 16-31),
   offset 1. The flip XORs 0x01 into keystream byte 17, so plaintext
   byte 17 alone changes: 0x30 ⊕ 0x01 = **0x31 — `0` becomes `1`**
   (e.g. `uid=1001` → `uid=1101`). No other byte changes; no error
   surfaces — unauthenticated CTR is a bit-editing machine. Under GCM the
   flip changes a GHASH input, the tag no longer matches, and
   `finalize()` raises `InvalidTag`.
6. ECB. Deterministic per-block encryption leaks equality and frequency;
   with only ~400 distinct surnames, the attacker maps ciphertext blocks
   onto the public surname-frequency distribution and recovers the whole
   column — no key attack needed. Fix: AEAD with a fresh per-row nonce,
   or SIV if deterministic ciphertext is required.
7. Class: **nonce persistence is process state** — a memory bug, not a
   math one (KRACK's reset counter; chapter 01's cloned-VM randomness).
   The attacker holds two records under one (key, nonce): the two-time
   pad (p1⊕p2, and full p2 if p1 is known) *and* — because GCM — the
   forbidden attack: the tag XOR difference is a polynomial in H whose
   roots reveal the authentication key, enabling permanent forgery.
   Fixes: (1) random 96-bit nonces with the 2^32 per-key cap, or (2)
   HKDF-derive a fresh key per boot so old counter values cannot collide
   — plus durably advancing any persisted counter.
8. The T-table index is a key-dependent state byte; each lookup touches
   one of 64 lines, and line residency — set membership in a shared
   structure — is observable through timing (Bernstein 2005, remote) or
   eviction probes. Spectre removes even the co-residency requirement: a
   sandboxed renderer speculatively touches the tables and leaks them
   across the boundary. AES-NI and ChaCha20 kill the class with one
   property: **no secret-dependent memory addresses** — state lives in
   registers, so there is nothing for any cache, in any chapter, to
   observe.

</details>
