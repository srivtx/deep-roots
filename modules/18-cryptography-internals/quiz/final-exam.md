# Module 18 final exam — Cryptography internals: the memory model of trust

Twelve questions across the module's five chapters — three on entropy, three
on the symmetric state machine, two on public-key bignums, two on TLS 1.3,
two on the failure catalog. Closed book; every answer is worked, not
stated — the derivations are the point. Scoring: 11+ = you own this module;
8-10 = solid, revisit the flagged chapters; <8 = the chapter quizzes are the
study path.

**Part I — entropy: randomness from physical memory (chapter 01)**

1. **Diagnose.** A TRNG datasheet claims "256 bits of entropy per boot" from
   256 raw samples. Your measurement: the raw bit stream emits 1 with
   probability 0.6. (a) Compute the honest min-entropy per sample. (b) How
   many bits of real entropy do the 256 samples carry, and how many raw bits
   does an honest 256-bit seed require? (c) The vendor's whitepaper arrives
   at "249 bits" — which measure did they use, why is it the wrong one
   against an adversary's best single guess, and why will nobody notice the
   shortfall in the field?

2. **Predict.** A CTR_DRBG (SP 800-90A) is seeded once with 256 honest bits:
   state = AES key + 128-bit counter, 2^19 bits per Generate call, reseed
   after 2^48 calls, and every call's update overwrites the old state.
   (a) How many bytes can flow between reseeds, and what does that number
   say about what the reseed interval actually is? (b) An attacker steals
   the live state on day 1,000: exactly what can she now predict, what can
   she never recover, and what puts an expiry date on her theft? (c)
   Chapter 05's catalog lists Debian, the clone fleet, and the wall-clock
   seed — which layer did *not* fail in any of them, and what does that say
   about where you audit?

3. **Predict (the number).** A helpdesk mandates 8-character passwords from
   lowercase a–z, chosen "randomly" by users. (a) Bits of entropy if the
   choices really were uniform — and where does the number-sense table put
   that against a laptop at 10^9 guesses/s and a GPU rig at 10^12? (b) The
   same passwords behind a bcrypt-class KDF (attacker speed ÷10^5):
   recompute the GPU time. (c) Corpus-real human passwords carry 20–40
   bits — how many random lowercase characters is that? (d) How many
   characters actually buy 80 bits, and what does a KDF buy instead of
   entropy?

**Part II — the symmetric state machine (chapter 02)**

4. **Derive (the birthday).** A log-shipping fleet seals every record under
   one long-lived AES-GCM key. Each 96-bit nonce is device_id (32 bits,
   fixed per device) ‖ random (64 bits); all devices share the key.
   (a) One device ships 2^20 records: compute the nonce-collision
   probability. (b) At what record count does collision reach 50% per
   device — and what would the horizon be with a full random 96-bit nonce?
   (c) Recompute for a fleet of 1,024 devices each shipping 2^20 records.
   (d) One collision occurs: what does the attacker hold, and what is the
   one fix that repairs the structure rather than the instance?

5. **Predict (byte level).** Two record prefixes were sealed under one CTR
   keystream — the nonce was reused. You know p1 = `"GET "` and hold
   c1 = `DB F7 7E D7`, c2 = `FA DE 4B 90`. Recover the keystream and p2,
   byte by byte. Then: what else does the recovered keystream buy, which
   single ciphertext byte would you flip to turn `G` into `S` in a live
   stream, and why does the same flip under GCM never reach the application?

6. **Diagnose.** A vendor ships full-disk encryption as AES-GCM per
   512-byte sector. Give three independent reasons that design is broken,
   with numbers: the per-sector framing cost, what a torn write on power
   loss does, and the per-key data cap against a 1 TiB disk. Then name the
   mode disks actually use, how it derives its "nonce," and why one flipped
   ciphertext bit garbling exactly one 16-byte block is deliberate.

**Part III — bignums are the attack surface (chapter 03)**

7. **Derive.** On the lab's toy curve (y² = x³ + 2x + 2 over F_17,
   G = (5,1), order n = 19), a device signs H1 = 6 and H2 = 14 with the
   same nonce k and private key d, publishing (r, s1) = (10, 15) and
   (r, s2) = (10, 5). Recover k and d, showing every inverse mod 19. What
   single observable in the published signatures is the tell, and which two
   real incidents died of exactly this?

8. **Predict.** Textbook modular exponentiation walks a 2,048-bit secret
   exponent: one squaring per bit, plus one multiply for every set bit.
   (a) Two keys with popcount 960 and 1,088: compute each operation count
   and the timing spread between them. (b) Per-signature network jitter
   dwarfs that spread — how does the attacker still win? (c) Curve25519's
   Montgomery ladder runs 251 iterations over the clamped key: compute its
   per-key operation counts and variance, and name what influence of the
   key bit survives. (d) For the RSA world, which cannot change algorithm
   shape, name the residual defense and its cost.

**Part IV — TLS 1.3 as memory hygiene (chapter 04)**

9. **Derive.** A long-lived TLS 1.3 connection averages 1,400-byte records.
   (a) How many records exhaust the ~64 GiB GCM per-key budget, and where
   does the spec's rekey guidance of ~2^24.5 records sit relative to it?
   (b) Over that connection's life, which of the 12 nonce bytes ever
   change, and why? (c) KeyUpdate resets the sequence counter to zero —
   why is that the one permitted reuse of low counter values? (d) The
   nonce is never transmitted at all: what does refusing to put it on the
   wire buy, and which two failure modes does the implicit counter trade
   for it?

10. **Diagnose (the mapping).** For each attack, name the single TLS 1.3
    design decision that kills it and the memory structure where the kill
    lives: (a) BEAST (2011) — TLS 1.0 chained each record's CBC IV to the
    previous record's last ciphertext block, so the attacker saw the "IV"
    before choosing the next plaintext; (b) KRACK (2017) — WPA2's
    retransmitted handshake message 3 reinstalled the key and reset the
    packet counter to zero; (c) a MitM forging a TCP FIN mid-response so a
    payment client logs 3 KB of a 10 KB answer as success; (d) a passive
    recorder storing today's handshakes to decrypt later with a stolen
    server key. Then: which attack class did TLS 1.3 consciously leave
    alive, and who enforces the defense?

**Part V — the failure catalog (chapter 05)**

11. **Diagnose (byte level).** A heartbeat request arrives: type 1,
    declared payload_length 0x4000, actual payload 4 bytes, 16 bytes of
    honest padding behind it. The response buffer is
    `malloc(3 + payload + 16)`. (a) How many bytes does the `memcpy` read,
    and how many lie past the attacker's own data? (b) Why is the
    destination *write* in bounds while the *read* is the violation?
    (c) Why did redzone-style memory checkers not flag the original bug,
    and what does that imply for the incident response? (d) The one-line
    fix, the class name, and what leaked first from production heaps.

12. **Diagnose (timing level).** A TLS 1.2 CBC server rejects injected
    records ~1–2 µs faster when the guessed padding is longer. (a) Walk
    the mechanism: what work shrinks with padding length, and why is
    "faster reject = better guess" an oracle? (b) The oracle answers one
    bit per query, at a few hundred queries per plaintext byte and ~2
    million chosen records per session — how long at 250 queries/second,
    and how does the attacker beat LAN jitter to see microseconds at all?
    (c) OpenSSL's patch took three rounds — what was it, and why did it
    keep failing? (d) What did TLS 1.3 do instead, in one sentence that
    generalizes?

<details>
<summary>Answers</summary>

**Part I**

1. (a) Min-entropy is the worst case: H∞ = −log2(p_max) = −log2(0.6) ≈
   **0.737 bits/sample** — an adversary always guessing "1" is right 60%
   of the time, and security prices your worst day. (b) 256 × 0.737 ≈
   **189 bits** — the datasheet overcounts by ~67. An honest 256-bit seed
   needs 256/0.737 ≈ **348 raw bits** (44 bytes). (c) 249 = 256 × 0.971
   is **Shannon** entropy (−0.6·log2 0.6 − 0.4·log2 0.4 ≈ 0.971
   bits/sample): average surprise, which overprices a biased source ~3×
   at this bias — SP 800-90B's estimators shave for exactly this reason.
   Nobody notices in the field because a short seed produces keys that
   are syntactically perfect and pass every self-test — Debian's 15-bit
   seed ran ~20 months unnoticed; only precomputation ever sees it
   (ch01).

2. (a) 2^48 calls × 2^16 B (2^19 bits = 64 KiB) = 2^64 B = **16 EiB**
   between reseeds. The interval is bookkeeping, not a cliff: expansion
   is cheap because the seed was the whole budget. (b) She can predict
   **every output until the next reseed** — each call is AES-CTR
   keystream under the stolen key and counter. She can **never** recover
   pre-theft outputs: every call overwrote the old state — backtracking
   resistance (fails only if stale state lingers: ch04's zeroization
   discipline). Reseeding folds in fresh entropy, so the theft has an
   **expiry date** — prediction resistance. (c) The DRBG layer: in Debian the seed was *deleted*, in
   the clone fleet *duplicated*, in the wall-clock case *public* — the
   deterministic expansion never failed once in the catalog. Seeding
   code is a single point of silent failure: audit upstream (ch01,
   ch05).

3. (a) 26^8 ≈ 2.09×10^11 ≈ 2^37.6 (8 × log2 26 = 8 × 4.70 = 37.6 bits).
   Laptop at 10^9/s: 209 s ≈ **3.5 minutes**; GPU rig at 10^12/s: **0.2
   s** — the table's "32 bits = laptop seconds" row, one notch up.
   (b) 10^12/10^5 = 10^7 effective guesses/s → 2.09×10^11/10^7 ≈ 2.1×10^4 s
   ≈ **5.8 hours**. (c) 20–40 bits = 4.3–8.5 random lowercase characters:
   **4–8 characters** — humans are the entropy floor (and do not choose
   uniformly). (d) 80/4.70 ≈ **17 characters**. The KDF adds zero
   entropy — it charges rent per guess (the ÷10^5), buying time, not
   margin: the honest fix is length, not iterations (ch01's number-sense
   table).

**Part II**

4. (a) q = 2^20 draws with replacement from the 2^64 random
   suffix: pairs = q(q−1)/2 ≈ 2^39, each colliding with probability
   2^−64, so **P ≈ 2^39/2^64 = 2^−25 ≈ 3×10^−8**. (b) 50% at
   q²/2^65 ≈ ½ → q ≈ **2^32.5 ≈ 6×10^9 records per device**. A full
   random 96-bit nonce: 2^48.5 ≈ 4×10^14 — the fixed 32-bit prefix
   deletes 32 bits of birthday margin, a 65,536× collapse. (c) Device risks add linearly: 2^10 × 2^−25 = **2^−15 ≈ 3×10^−5**
   — a ~1-in-33,000 chance the fleet minted a reused (key, nonce). (d) The two-time pad: c1 ⊕ c2 = p1 ⊕ p2, and templated
   log records make p1 known, so p2 falls out verbatim. Worse — it is
   GCM: same (key, nonce) means same J0, so T1 ⊕ T2 = GHASH(C1) ⊕
   GHASH(C2) is a polynomial in H whose roots hand over the
   authentication subkey — Joux's forbidden attack, forgery forever;
   rotate the key, not the nonce. The structural fix: move the device ID
   into the *key* — HKDF-Expand(master, device_id) gives every device its
   own key, restoring the full 96-bit nonce space and making cross-device
   collisions impossible; alternatively random 96-bit nonces under the
   2^32-per-key cap (ch02).

5. KS = c1 ⊕ p1: DB⊕47 = 9C, F7⊕45 = B2, 7E⊕54 = 2A, D7⊕20 = F7 →
   **KS = 9C B2 2A F7**. Then p2 = c2 ⊕ KS: FA⊕9C = 66 'f', DE⊕B2 = 6C
   'l', 4B⊕2A = 61 'a', 90⊕F7 = 67 'g' → **p2 = "flag"** (equivalently,
   c1 ⊕ c2 = 21 29 35 47 = p1 ⊕ p2 — the two-time pad's signature). The
   keystream decrypts *any* record sealed under that (key, nonce) and
   enables bit-aligned forgery: XOR 0x14 into ciphertext byte 0 —
   0x47 'G' ⊕ 0x14 = 0x53 'S' — and the receiver parses "SET" with no
   error: unauthenticated CTR is a bit-editing machine. Under
   GCM the flip changes a GHASH input, the tag no longer matches, and the
   record dies at `InvalidTag` before the application sees one byte
   (ch02).

6. (a) Framing: every sector would carry a 16-byte tag plus a stored
   12-byte nonce — 28/512 = **5.5% of the disk**. (b) Torn write: power
   loss mid-write leaves sector and tag inconsistent; the next read
   fails authentication and the mount refuses — **the disk bricks itself
   on a power glitch**: per-sector authentication is structurally
   impossible, not merely expensive. (c) The GCM cap: 2^36 B ≈ 64 GiB per
   key — a 1 TiB disk needs 2^40/2^36 = **16 HKDF-derived keys**; and the
   nonce discipline (persisted per-sector counters) is precisely the
   crash/rollback state that reuses nonces exactly. Disks use **XTS**: the
   tweak is the sector number, encrypted under a second key and advanced
   by powers of α per block — position derives the "nonce," so there is
   no state to reset, replay, or collide. One flipped ciphertext bit
   garbles exactly **one 16-byte plaintext block** — deliberate damage
   localization, honest caveat: not truly wide-block (ch02's insiders).

**Part III**

7. Identical r across two different messages is the tell: r is the
   x-coordinate of k·G, so the same r means the same nonce k. Subtract
   the signing equations: (s1 − s2)·k = H1 − H2, so 10·k ≡ 6 − 14 ≡
   −8 ≡ 11 (mod 19); 10^−1 ≡ 2 (10·2 = 20 ≡ 1), so k = 11·2 = 22 ≡
   **3**. Then d = (s1·k − H1)/r: 15·3 − 6 = 39 ≡ 1 (mod 19); r^−1 = 2,
   so d = 1·2 = **2**. Two inverses — the private key, the same algebra
   that signed the PS3's firmware (fixed hardcoded k, 2010) and emptied
   Android/Bitcoin wallets (PRNG state collisions, 2013), scaled to
   256-bit n. The fix is RFC 6979's deterministic
   k = HMAC(d, H(m)): deterministic beats random when randomness is the
   attack surface (ch03).

8. (a) Key A: 2,048 + 960 = **3,008 operations**; key B: 2,048 + 1,088 =
   **3,136** — a **~4.3%** timing spread purely from popcount (mean
   1,024, σ ≈ 22.6; these keys sit at ±2.8σ). (b) One signature's jitter
   is 100× the signal, but averaging N measurements shrinks noise ~√N:
   thousands of timed signatures turn 4% into certainty — the same
   statistics dudect industrializes as a Welch t-test — and windowed
   exponentiation then leaks *which* bits — the table index is secret
   bits choosing cache lines (Tromer: an adjacent VM, ~100 ms). (c) The ladder: 251 iterations, each exactly 1 doubling +
   1 differential addition + 1 conditional swap — **251 doubles, 251
   adds, 251 cswaps, every key; variance zero by construction**. The
   surviving key influence is *which register plays which role*, via a
   branchless swap: nothing reaches branch direction, operand size, or
   memory address (and 2^255 − 19 reduces with shifts and ×19 — no
   comparisons). (d) Blinding: m′ = m·r^e mod n, sign m′, divide by r
   (r^(ed) ≡ r by the chapter's own congruence); cost ~**20%** plus a
   precomputed r^e, r^−1 pool — the timing distribution the attacker
   measures becomes one over r, not d (ch03).

**Part IV**

9. (a) 2^36 B / 1,400 B ≈ 4.9×10^7 ≈ 2^25.5 records exhaust the budget;
   the ~2^24.5 ≈ 2.4×10^7 rekey guidance sits at **half** that — margin,
   not coincidence. (b) The nonce is static_iv ⊕ seq
   with the 64-bit counter right-aligned in 12 bytes: bytes 0–3 XOR zeros
   forever, and below 2^32 records the counter's high 4 bytes are zero
   too — so **only the last 4 of 12 nonce bytes ever change**; the other
   8 stay frozen at static_iv. (c) KeyUpdate installs a *fresh traffic
   key* when it resets the counter — every (key, nonce) pair after the
   reset is new: low values recur, pairs never; key and counter state
   must travel together (KRACK violates exactly this law). (d)
   Transmitting the nonce would spend 12 bytes per record and expose the
   one value GCM dies on; hiding it deletes both — at the price of
   implicit state that must agree on both ends. Failure
   mode one, desync (sender restarts at 0, receiver expects 5): every tag
   fails, `bad_record_mac` — the polite death. Failure mode two, both
   ends restarting at 0 under the same key: identical (key, nonce) pairs
   — the two-time pad, and for GCM H itself recoverable, forgery forever
   (ch04; ch02).

10. (a) **AEAD-only, nonce implicit.** The 1.0 "IV" was the previous
    record's last ciphertext block — visible before the attacker chose
    the next plaintext, so she could aim the first block and test the
    guess (BEAST's chosen-plaintext cookie recovery; an IV must be
    *unpredictable*, not merely unique). TLS 1.3 has no CBC, no padding,
    and no transmitted IV at all: nonce = static_iv ⊕ sequence number,
    rebuilt from a per-direction counter the attacker never sees before
    encryption — there is nothing left to predict. (b) **Counters reset
    only with new keys.** KRACK's shape is state replay: retransmission
    logic reinstalled the key and zeroed the packet counter, replaying
    (key, nonce) pairs. 1.3's KeyUpdate law resets the counter to 0 only
    alongside a fresh traffic key — low values recur, pairs never.
    (c) **close_notify.** Each side must send the encrypted alert
    (record type 21) before closing its write half, and the library must
    surface EOF-without-close_notify as an *error*, not EOF — OpenSSL
    3.0's `SSL_R_UNEXPECTED_EOF_WHILE_READING` exists for this — a forged
    FIN is byte-perfect at the TCP layer: truncation is detected one
    layer above the layer that can be truncated. (d)
    **Ephemeral DHE plus the erasure schedule.** TLS 1.2's RSA key
    transport encrypted the premaster to the long-term key — record
    once, steal the key later (Heartbleed was the theft event), decrypt
    history. 1.3 deletes the mechanism (DHE only) and erases on
    schedule: the DH private key the moment the handshake secret exists,
    handshake traffic secrets after both Finished messages, application
    secrets at close — past captures stay dead because the bytes are.
    Consciously left alive: **0-RTT replay** — early data rides a
    PSK-only traffic secret an active MitM can re-send verbatim; no
    protocol layer can know which of your requests is safe to run twice:
    the application enforces it (idempotent GET yes, POST never) (ch04).

**Part V**

11. (a) The `memcpy` copies the declared **16,384 bytes** starting at the
    payload pointer: 4 bytes the attacker actually sent, then 16 honest
    padding bytes still inside the record's chunk, then **16,364 bytes
    past the message** — chunk slack, the next chunk's header, its
    payload. Total beyond the attacker's own data: 16,380. (b) The
    response buffer was sized 3 + 16,384 + 16 = 16,403 bytes before the
    copy, so the write fits its allocation; the violation is entirely
    the *source read* — a declared length never checked against the
    record that actually arrived. A heap over-read — the complement of
    use-after-free, touching bytes the program never logically owned; it
    needs no write primitive because the read's content is echoed back
    verbatim. (c) In OpenSSL the source read stayed *inside one
    malloc'd 16 KiB record buffer* — stale recycled bytes, not
    out-of-allocation bytes — so redzone checkers saw a legal access;
    what leaked was *whose* stale neighbors sat there (record buffers
    recycle through thousands of connections): session keys first — a
    mid-connection heap is full of them — then private keys, passwords,
    cookies. Hence: rotate everything, not patch-and-move-on. (d) The fix is one bounds check:
    `if (1 + 2 + payload + 16 > n) return 0;`. The class: TLV framing
    bugs — a state machine must never trust a length field it doesn't
    own. One line of code versus re-issuing certificates for ~17% of the
    secure web (ch05).

12. (a) MAC-then-encrypt: the receiver decrypts, strips k padding bytes,
    *then* HMACs what remains — so a valid padding guess of length k
    shortens the MAC's input by k bytes (k ∈ 1..16), and the hash work
    shrinks ~1–2 µs per step. The reject is faster exactly when the
    padding guess is *more* correct: timing is a function of work, and
    the work varied with a secret-derived value — a padding oracle that
    decrypts ciphertext without the key. (b) 2×10^6
    records / 250 per second = 8,000 s ≈ **2.2 hours**. Microseconds
    against LAN jitter: repeat each query thousands of times and average
    — noise shrinks ~√N while the µs bias does not; the same statistical
    amplification dudect formalizes as a t-test. Vaudenay's ~few hundred
    queries per plaintext byte compound into the session. (c) The patch:
    constant-time padding handling — HMACs over dummy maximal lengths,
    every padding treated alike, no branching on the pad value. It took
    three rounds because the first two were found still leaky in 2013:
    constant time decays under refactor — it is *measured* (dudect,
    ctgrind), never claimed — re-run after every refactor. (d) TLS 1.3
    removed the grammar: AEAD only, the tag computed over the ciphertext
    and verified before one byte of plaintext is parsed, so the "bad
    padding" branch cannot exist. *Remove the class of bug, don't fix
    each instance* (ch05; ch04).

</details>
