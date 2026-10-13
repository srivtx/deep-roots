# Chapter 03 — Public key: bignums are the attack surface

> **The 30-second version.** Public-key math is easy forward,
> infeasible backward — multiply two primes, exponentiate in a group —
> and runs as heap-allocated bignums: arrays of 64-bit limbs whose
> *length* and *access pattern* are the leak. RSA's correctness is
> three lines of Fermat plus CRT; its safety is a fixed-length loop
> over the limb array, a blinded exponent, and a verification before
> output — a faulted limb hands over the factorization (Bellcore:
> `gcd = p`). ECC keeps the double-and-add shape in 32 bytes;
> Curve25519 fixes the operation *schedule* for every key. ECDSA's
> nonce *is* the key: reuse it once and the key falls out of two
> signatures by division.

## The trapdoor: cheap forward, infeasible backward

Chapter 02 needed a shared secret in both places first; public-key
crypto wants a rule anyone can run and a rule only one party can — the
**one-way function**, cheap forward, brutal backward:

- Multiply two 300-digit primes: microseconds. Factor the product: the
  record (RSA-250, 829 bits, 2020) burned ~2,700 CPU-core-years.
- Exponentiate in a group: `g^x` costs log(x) multiplications; discrete
  log — recovering x from `g^x` — has no shortcut: generic attacks
  cost ~√n operations.

**RSA is the first of these worn as a system.** Pick secret primes p, q
(~1,024 bits each). Publish `n = pq` and `e` (conventionally 65,537);
keep `d`, the inverse of e modulo `φ(n) = (p−1)(q−1)`. Sign with
`m^d mod n`; anyone verifies with `m^e mod n`. The proof:

Since `e·d ≡ 1 (mod φ(n))`, write `ed = 1 + k·φ(n)`. Modulo p:
`m^(ed) = m · (m^(p−1))^(k(q−1))`. Fermat's little theorem says
`m^(p−1) ≡ 1 (mod p)` whenever p ∤ m, so the big factor collapses and
`m^(ed) ≡ m (mod p)` (if p | m both sides are 0). The same three moves
mod q give `m^(ed) ≡ m (mod q)`. CRT finishes it: the congruences agree
with m modulo p and modulo q, `gcd(p,q) = 1`, so they agree modulo
`pq = n`. ∎

The theorem says honest decryption works; that *inverting* RSA means
factoring n is an unproven assumption. The math is the easy layer — the
rest of this chapter is where the 2,048-bit number lives in memory: the
place every real break in this story happened.

## The BIGNUM: a 2,048-bit key is a heap array with a shape

C has no 2,048-bit integer type: every number here is a heap
allocation whose layout decides whether the key leaks. OpenSSL's
`BIGNUM` (fields stable for decades; opaque since 1.1.0):

```c
struct bignum_st {          /* classic layout, 64-bit platform */
    BN_ULONG *d;   /* heap limb array, LEAST significant limb first */
    int top;       /* limbs in use: n (2048 bits) -> 32   <- varies */
    int dmax;      /* limbs allocated                      <- fixed  */
    int neg;       /* sign */
    int flags;     /* BN_FLG_MALLOCED 0x01 ... BN_FLG_CONSTTIME 0x04 */
};
```

```
BIGNUM holding a 2048-bit modulus (n = pq), 64-bit limbs:

  struct bignum_st { d -------------------> +----------+----------+--
    top  = 32                                |  d[0]    |  d[1]    | ...
    dmax = 36                                | bits     | bits     |
    neg  = 0                                 | 0..63    | 64..127  |
    flags                                    +----------+----------+--
  }                                            ^                     ^
                                    least significant    d[31] holds bits
                                    limb (2^0..2^63)     1984..2047

  wire format (PEM/DER) is BIG-endian: the first byte of n in the
  file is d[31]'s high byte — a memcpy of the limb array is
  byte-REVERSED.  d[32..35] (top..dmax-1): stale heap bytes, cleared
  only by BN_clear_free.
```

The core novelty: **the variable length is the leak.** Classic bignum
loops run `for (i = 0; i < top; i++)` — whenever a secret influences
`top` (a value-dependent reduction length, an early-returning compare),
the loop's duration carries those bits to anyone with a stopwatch.
Constant-time discipline, precisely: **fixed-length loops over `dmax`,
every limb touched every time, results selected by masks, never by
branches.**

The hot loop, modular exponentiation, is an oracle in textbook form:

```
for each bit b of d (2048 of them):
    acc = acc^2  mod n          /* always */
    if (b == 1)                 /* the leak: multiply runs only on a */
        acc = acc * m mod n     /* 1-bit; its latency draws d's bits */
```

Time each slot and d is drawn in latency. *Windowed* exponentiation —
precompute `m^1, m^3, m^5, ...`, one lookup per w-bit window — is
faster and worse: **the table index *is* secret key bits**, visible to
a co-tenant through the cache line it touches (catalog below). Two
mitigations:

- **Constant-time shape**: loop over all 2,048 bits unconditionally —
  always square *and* always multiply, result kept via a branchless
  mask: same instructions, same order, same time, every key.
- **Blinding.** Pick random r, compute `m' = m·r^e mod n`, sign m',
  divide by r: `(m')^d = m^d · r^(ed) ≡ m^d · r`, so
  `s = s'·r^(-1) mod n` — the factors cancel by this chapter's own
  theorem. The attacker now times an exponentiation whose secret is
  randomized — the distribution is over r, not d. Budget: ~20% plus a
  precomputed `r^e, r^(-1)` pool.

## CRT-RSA: four times faster, one fault from factorization

Real RSA never computes `m^d mod n` directly: store `d_p = d mod
(p−1)`, `d_q = d mod (q−1)` (both in the key file) and compute
`m_p = m^(d_p) mod p`, `m_q = m^(d_q) mod q`. Each half has half-size
numbers and exponents, ~8× less work each, two halves → **~4× total** —
why every serious implementation is CRT-RSA. **Garner recombination**
glues the halves, with `qinv = q^(-1) mod p` (also in the key):

```
h = (m_p − m_q) · qinv  mod p
m = m_q + q · h
```

Check it: mod p, `q·h ≡ m_p − m_q` so `m ≡ m_p`; mod q, the `q·h` term
vanishes so `m ≡ m_q`.

Now suppose a fault — a cosmic-ray flip, a glitch, module 02 chapter
03's Rowhammer — corrupts just the `m_p` half while `m_q` is correct,
and the device *outputs* the recombined s′. Verify s′:

- Mod q, correct: `s′ ≡ m^d (mod q)`, so `s′^e ≡ m^(ed) ≡ m (mod q)`.
- Mod p, generically wrong: `s′^e ≢ m (mod p)`.

So q divides `s′^e − m` and p does not — the definition of a gcd:

```
gcd(s′^e − m, n) = q      <- one gcd, microseconds, factors n
```

**The Bellcore fault attack (2002): one emitted faulted signature
destroys the key.** Countermeasure, nearly free: verify before output.
Because e = 65,537 = 2^16 + 1, the check `s^e ≡ m (mod n)` is 16
squarings + 1 multiply against ~2,048 to sign — this chapter's build
(OpenSSL 3.5.7, one core): 3,284 signs/s vs 60,527 verifies/s, ~5% of
a sign.

## ECC: the same ladder, with 32-byte numbers

An elliptic curve re-derives the trapdoor with far less memory: `y² =
x³ + ax + b` over F_p (P-256: a 256-bit prime). Point addition is
geometric: draw the line through P and Q; it meets the curve once
more; negate that point's y. The points form a group of order ~2^256,
and discrete log here has no number-field-sieve analog — the best
generic attack (Pollard rho) costs ~√n ≈ 2^128 operations. Hence a
**32-byte** ECC key sits at the same ~112–128-bit security line as a
**256-byte** RSA-2048 key: an 8× shrink of every buffer and signature.

Scalar multiplication `k·P` is *the same shape* as modular
exponentiation — double-and-add: per bit of k, double; if the bit is
set, add P. Every leak returns: the conditional add draws k in latency,
tables leak k's bits through cache lines, blinding randomizes the
point.

Curve25519 (Bernstein, 2005) removed this class structurally. Key
exchange (X25519) runs the **Montgomery ladder**: two x-only
registers hold `(m·P, (m+1)·P)`; every iteration does exactly one
differential addition and one doubling, and the key bit's only
influence is *which register plays which role*, via a branchless
conditional swap:

```
Montgomery ladder over the clamped key bits (251 of them):

  key bit:      1         0         1         1         0    ...
              +---------+---------+---------+---------+-----+
  R0 = m·P    |   1P    |   2P    |   4P    |   9P    | 18P | ...
  R1=(m+1)P   |   2P    |   3P    |   5P    |  10P    | 19P | ...
              +---------+---------+---------+---------+-----+
  every column, no exceptions:  1 DOUBLE + 1 DIFF-ADD + 1 cswap
  251 columns per key: the schedule IS the program.
```

No secret reaches the instruction stream — not branch direction,
operand sizes, or table indices (there is no table). The field prime
`2^255 − 19` folds overflow with shifts and multiplies by 19: no
comparisons, no branches. The clamped scalar (low 3 bits cleared,
bit 254 set — 251 usable bits) kills small-subgroup edge cases.
Bernstein's *SafeCurves* rule: a curve is safe only if its fastest
implementation is constant-time — **design for constant time or die
patching**. Ed25519, the signature flavor, uses the same curve in
Edwards coordinates with *complete* addition formulas — no special
cases.

## ECDSA: the nonce is the private key

With private key d, public `Q = d·G`, group order n, message hash H:

```
R = k·G for random k;  r = R.x mod n;  s = k^(-1)·(H + d·r)  mod n
```

The nonce k must be uniformly random, never reused, not even biased —
k and d share one linear equation. Two signatures, same k (hence the
same r):

```
s1·k = H1 + d·r           s2·k = H2 + d·r
(s1 − s2)·k = H1 − H2     =>  k = (H1 − H2)/(s1 − s2)  mod n
substitute back:              d = (s1·k − H1)/r        mod n
```

Two signatures, two inverses — the private key. The incidents:

- **Sony PS3 (2010).** The PS3's code-signing ECDSA used a fixed
  hardcoded k; fail0verflow (27C3) showed any two published
  signatures solve the equations above, so anyone could sign
  firmware. The key never leaked — the *nonce discipline* did.
- **Android/Bitcoin (2013).** Android's `SecureRandom` produced
  colliding PRNG states — repeated k across different messages;
  attackers drained Bitcoin wallets. Lattice methods (the
  hidden-number problem) extend the kill to *biased* nonces: a few
  leaked bits of k per signature suffice to recover d.
- **The fix, RFC 6979:** make k deterministic — `k = HMAC-DRBG(d,
  H(m))`, an HMAC over the private key and the message hash. Same
  message, same signature; different messages, different k. **The
  insight: deterministic beats random when randomness is your attack
  surface** — the RNG vanishes from the signing path, and chapter 01's
  entropy cliff no longer sits under every signature.

## How keys actually leak: the side-channel catalog

Tie every channel back to the memory layout; chapter 05 raids this
list:

- **Timing.** Variable-length loops above. Defense: fixed loops,
  blinding (~20%), no secret near a branch (early-exit compares —
  chapter 02's Lucky 13 is that rule violated).
- **Cache.** The windowed-exponentiation table: the secret window picks
  a cache line a co-tenant can measure (Prime+Probe, Flush+Reload).
  Tromer (2010) showed an OpenSSL RSA-1024 key falling to an *adjacent
  virtual machine* with ~100 ms of measurements — no code execution on
  the victim. Table indices must never be secret.
- **Power/EM.** On smartcards, a squaring and a multiply draw visibly
  different current — SPA/DPA recover the exponent bit-by-bit
  (module 20).
- **Rowhammer** (module 02 ch03). A bit flip in a key limb is the
  fault primitive Bellcore consumes.
- **Spectre** (module 16). Speculative execution reads past bounds
  checks; bignum limbs — including stale bytes beyond `top` — are the
  neighbors a sandbox read crosses into.
- **Cold boot.** DRAM off-power decay is seconds-to-minutes at room
  temperature, stretched by cooling spray: the Princeton "Lest We
  Remember" study scavenged live keys from frozen, decayed-but-
  recoverable DIMMs — module 01 chapter 01's capacitor, weaponized.
  A key in process RAM is not "at rest."
- **Process memory generally.** Heartbleed (chapter 05) read the heap
  around a TLS record and found the private key in exactly these
  bignums. The structural answer is the **key-wrap hierarchy**: a root
  key generated *inside* a TPM or secure enclave and never exported —
  it unwraps session keys, so a compromised host at worst steals what
  the root transiently decrypts (module 05/06's hardware roots). *The
  never-leaves-silicon root is the only key you can vouch for.*

## What insiders know

- **`BN_FLG_CONSTTIME` (0x04) is a flag, not a property.** Nothing
  sets it for you on every path: `BN_mod_exp` on a secret exponent is
  the call where forgetting it — or losing it on copy/re-expansion —
  became a CVE. Set it on every BIGNUM carrying secret material, and
  know which operations honor it.
- **DER/ASN.1 is a parsing minefield, not a crypto one.** Encoded
  lengths leak nothing (key sizes are public), but the *decoders* carry
  decades of memory-safety CVEs — malformed lengths, bounds overflows.
  A hand-rolled DER parser is how "we only read the public key" becomes
  a heap overflow.
- **Constant time is *tested*, not proven by reading code.** Tools:
  `ctgrind` (valgrind-based; flags secret-dependent branches),
  `dudect` (Welch's t-test on timing of fixed-vs-random secret
  inputs), and `cryptofuzz` for the arithmetic.
- **`e = 65,537 = 2^16+1` is why RSA verify is ~18× cheaper than
  sign** (measured above): 16 squarings + 1 multiply versus ~2,048 for
  a full private exponent. That asymmetry is TLS economics — the
  reason ECDHE + ECDSA took over handshakes.
- **One Montgomery, two algorithms.** The Montgomery ladder of
  Curve25519 and Montgomery *multiplication* (REDC — division-free
  shift-and-add reduction that makes every modexp fast) are both
  Peter Montgomery. REDC needs no division, which is also why it has
  no secret-dependent branches.
- **Stale limbs between `top` and `dmax`** are a real leak surface:
  `BN_clear_free` zeroes the array before release; plain `free`
  hands key bits to the allocator, where module 02 chapter 05's
  freed-chunk scavenging finds them.

## See it yourself

**1. A private key is an ASN.1 tree — go meet yours.**

```console
$ openssl genrsa 2048 2>/dev/null | openssl rsa -traditional \
    -out trad.key 2>/dev/null
$ openssl asn1parse -in trad.key | cut -c1-45
    0:d=0  hl=4 l=1186 cons: SEQUENCE
    4:d=1  hl=2 l=   1 prim: INTEGER           :00      <- version
    7:d=1  hl=4 l= 257 prim: INTEGER           :D298...  <- n (257 B!)
  268:d=1  hl=2 l=   3 prim: INTEGER           :010001   <- e
  273:d=1  hl=4 l= 256 prim: INTEGER           :2AC2...  <- d
  533:d=1  hl=3 l= 129 prim: INTEGER           :EC88...  <- p (129 B)
  665:d=1  hl=3 l= 129 prim: INTEGER           :E3ED...  <- q
  797:d=1  hl=3 l= 128 prim: INTEGER           :1918...  <- d mod p-1
  928:d=1  hl=3 l= 128 prim: INTEGER           :085C...  <- d mod q-1
 1059:d=1  hl=3 l= 128 prim: INTEGER           :4754...  <- q^-1 mod p
```

This is *the CRT key of section three* — `d mod p−1`, `d mod q−1`,
`qinv` all sit in the file. Byte counts: n is 256 bytes of content but
`l= 257` — DER integers are signed, so a top-bit-set modulus gets a
leading `00` (OpenSSL 3.x writes PKCS#8 by default; `-traditional`
exposes this raw RSAPrivateKey).

**2. Where the microseconds go.**

```console
$ openssl speed -seconds 1 rsa2048 ed25519    # OpenSSL 3.5.7, 1 core
  rsa2048        sign 0.000305s  verify 0.000017s   ( 3284/s, 60527/s)
  253 bits EdDSA sign 0.000035s  verify 0.000097s   ( 28681/s, 10287/s)
```

RSA signs at 0.3 ms, verifies 18× faster (small e); Ed25519 signs 9×
faster, verifies 6× slower. Times millions of handshakes: certificates
chain with RSA or ECDSA, key exchange moved to X25519.

**3. Garner and Bellcore, 12 lines of gmpy2** (verified end-to-end):

```python
from gmpy2 import mpz, invert, powmod, gcd, next_prime
p, q = mpz(2)**61 - 1, next_prime(mpz(2)**62)      # toy 64-bit primes
n, e = p * q, 65537
d = invert(e, (p-1)*(q-1))
m = mpz(0xDEADBEEFCAFE)                            # the "digest"
mp_, mq_ = powmod(m, d % (p-1), p), powmod(m, d % (q-1), q)
qinv = invert(q, p)
h = ((mp_ - mq_) * qinv) % p
print("garner ok:", mq_ + q*h == powmod(m, d, n))       # True
mp_bad = mp_ ^ 1                                   # one flipped bit
h = ((mp_bad - mq_) * qinv) % p                    #   in the p-half
s_bad = mq_ + q * h
print("bellcore:", gcd(powmod(s_bad, e, n) - m, n) == q) # True
```

Any wrong `mp_bad` gives gcd = q (or p) — the factorization, small
enough to watch.

**4. ECDSA nonce reuse — recover a key in ~20 lines** (toy curve
`y² = x³ + 2x + 2` over F_17, G = (5,1) of order 19; verified):

```python
p, a, n, G = 17, 2, 19, (5, 1)
def padd(P, Q):                                    # point addition
    if P is None: return Q
    if Q is None: return P
    x1, y1 = P; x2, y2 = Q
    if x1 == x2 and (y1 + y2) % p == 0: return None
    lam = (y2-y1)*pow(x2-x1, -1, p) % p if P != Q \
        else (3*x1*x1 + a)*pow(2*y1, -1, p) % p
    x3 = (lam*lam - x1 - x2) % p
    return (x3, (lam*(x1-x3) - y1) % p)
def pmul(k, P):                                    # double-and-add
    R = None
    for bit in bin(k)[2:]:
        R = padd(R, R)
        if bit == '1': R = padd(R, P)
    return R
d = 7
def sign(h, k):                                    # h = toy hash
    r = pmul(k, G)[0] % n
    return r, pow(k, -1, n) * (h + d*r) % n
r1, s1 = sign(3, 11); r2, s2 = sign(9, 11)         # k = 11, REUSED
assert r1 == r2                                    # same r: the tell
k_rec = (3 - 9) * pow(s1 - s2, -1, n) % n
d_rec = (s1 * k_rec - 3) * pow(r1, -1, n) % n
print("recovered:", d_rec == d)                    # True
```

The same algebra, scaled to 256-bit n, emptied Android Bitcoin wallets
in 2013.

## How to use this knowledge

- **Reading crypto CVEs.** "Timing side channel in RSA implementation"
  parses to a picture: which loop varied, which table index was secret,
  whether blinding was on — you can predict the patch.
- **Performance architecture.** The 18× sign/verify asymmetry and
  ECC's 8× smaller keys decide handshake economics — enough to justify
  "ECDSA certificates + X25519 exchange" in a design review.
- **Code review of anything crypto-adjacent.** Reject
  secret-dependent branches, `top`-bounded loops on secret data,
  hand-rolled DER parsing, and plain `free()` on key bignums — most of
  the field's real-world breakage. War stories now explainable at a
  whiteboard: PS3 (fixed k), Android wallets (repeated k), Bellcore
  (one fault = one gcd), Tromer (adjacent VM, ~100 ms).

## Connections

- **Module 18 chapter 01** — entropy under every nonce; RFC 6979
  deletes it from the signing path.
- **Module 18 chapter 02** — nonce discipline as a family: GCM's
  (key, nonce) pair and ECDSA's k are the same rule.
- **Module 18 chapter 04** — TLS 1.3's key_shares are this chapter's
  X25519 ladder; certificate checks are the measured economics.
- **Module 18 chapter 05** — raids this chapter: Rowhammer into
  Bellcore, PS3 and Android nonces, ASN.1 minefields, Heartbleed on
  key bignums.
- **Module 01 chapter 05** — the bignum as heap object: `d` a
  pointer, stale limbs freed-heap residue, `BN_clear_free` allocator
  hygiene on keys.
- **Module 02 chapters 03–05** — Rowhammer supplies the fault
  Bellcore consumes; freed-chunk and UAF scavenging exposes
  plain-`free`d key limbs.
- **Module 04 chapter 03** — the compiler as constant-time adversary:
  masked selects can become branches, early-returns appear at `-O2`,
  and this chapter's fixed-limb loops need explicit protection.

## Quiz

1. **Concept.** A BIGNUM carries `top` and `dmax`. Why must
   constant-time code loop over `dmax` rather than `top`, what does an
   attacker gain from a `top`-bounded loop on secret data, and why do
   the extra limbs cost nothing asymptotically?

2. **Concept.** Curve25519's Montgomery ladder performs the *same
   operations in the same order* for every key bit, choosing register
   roles with a branchless cswap. Why is that stronger than compiling
   square-and-multiply to branchless code — and what key influence
   still reaches the hardware?

3. **Derive.** A CRT-RSA device emits a signature s′ whose `m_p` half
   was faulted (`m_q` is correct). Derive `gcd(s′^e − m, n)`; then the
   one design change that neutralizes the attack, and why it costs
   ~5% of a sign.

4. **Predict the number.** On the lab's toy curve (n = 19, G = (5,1)),
   d = 7, and signatures of H1 = 3 and H2 = 9 with the same k = 11:
   (r, s1) = (13, 12) and (13, 16). Recover k and d, showing the
   arithmetic mod 19.

5. **Predict the layout.** `openssl asn1parse` on a 2048-bit key shows
   `l= 257` for n and `l= 129` for p. Explain both counts. Then: with
   `top = 32`, which limb holds bits 1984–2047, and what does the
   file's leading `00` byte become?

6. **Diagnose.** A code reviewer finds this in a signature loop:

   ```c
   for (i = 0; i < bn->top; i++)      /* bn = secret exponent */
       acc = mul_add(acc, bn->d[i], m, n);
   ```

   What leaks, through which two physical channels, and what fix
   shape plus randomization hides the residual signal?

7. **Diagnose.** An embedded CRT-RSA signer emits signatures that
   sometimes fail public verification; the team discards failures but
   skips verify-before-output "for latency." Under Rowhammer (module
   02 ch03), why is the next *accepted* faulted signature fatal, and
   what does the attacker compute?

8. **Cross-chapter.** Module 01 chapter 05 says every BIGNUM `d` array
   is a heap allocation; module 02 chapter 05 documents freed-chunk
   scavenging. What does plain `free()` on a key BIGNUM leave behind,
   where does it go, and what does `BN_clear_free` change about the
   limbs between `top` and `dmax`?

<details>
<summary>Answers</summary>

1. `top` varies with the number's *value*: a `top`-bounded loop runs a
   secret-dependent iteration count, so total time (and which limbs are
   cached) is a function of the secret. `dmax` is the allocated
   capacity, fixed at key load — identical work for every key. The
   extra limbs add always-on work, not asymptotic cost: the loop was
   already O(dmax), and results are filtered by masks.

2. Branchless square-and-multiply still executes a multiply-or-not
   whose operands and data flow differ per key bit — instruction
   timing, port pressure, and cache footprints differ, and the
   compiler may re-materialize a branch. The ladder's invariant
   (R0, R1) = (mP, (m+1)P) forces one doubling and one differential
   addition per bit in fixed order; the bit only permutes which
   register holds which result. The key's influence is confined to
   register *labels*: nothing reaches the instruction stream.

3. Mod q the device was correct: `s′ ≡ m^d (mod q)`, so
   `s′^e ≡ m^(ed) ≡ m (mod q)` — q divides `s′^e − m`. Mod p the
   faulted half is generically wrong, so p does not divide it. A number
   divisible by q but not p, against n = pq, yields exactly
   `gcd(s′^e − m, n) = q` — the factorization for one gcd. Fix: verify
   before output — recompute `s^e ≡ m (mod n)`. Since e = 65,537 =
   2^16 + 1, that is 16 squarings + 1 multiply versus ~2,048 to sign:
   ~5%.

4. k = (H1 − H2)/(s1 − s2): (3 − 9) ≡ 13 and (12 − 16) ≡ 15 (mod 19);
   15^(−1) = 14 since 15·14 = 210 ≡ 1; k = 13·14 = 182 ≡ **11** — the
   reused nonce. Then d = (s1·k − H1)/r: 12·11 − 3 = 129 ≡ 15;
   r = 13 and 13^(−1) = 3 since 13·3 = 39 ≡ 1; d = 15·3 = 45 ≡
   **7** (mod 19) — the private key.

5. n is 2,048 bits = 256 bytes, but DER INTEGERs are signed: n's top
   bit is set, so a `00` byte is prepended to keep it positive — 257.
   p is 128 bytes plus the same rule = 129. Limbs are
   least-significant-first: bits 1984–2047 live in `d[31]`; the leading
   `00` vanishes in conversion, and n's first real byte lands in the
   high byte of `d[31]`.

6. The iteration count is secret (`top` of the exponent's bignum):
   timing leaks the exponent's effective length, and per-iteration
   addresses (which limbs get cached) leak its value pattern — via
   timing and cache occupancy (Prime+Probe/Flush+Reload). Fix: loop
   over the fixed `dmax` with masked selection, and blind the input
   (`m' = m·r^e`, divide the signature by r) so residual timing is
   randomized over an unknown r, not the key.

7. The discarded failures are harmless — the attack needs a faulted
   signature that is *emitted*. Skipping verify-before-output hands the
   next Rowhammer flip in the m_p half to the attacker, who computes
   `gcd(s′^e − m, n)` on public values and receives a prime factor of
   n. The monitor sees nothing: the faulted signature is never checked
   at production.

8. Plain `free()` releases the limb array with the key's bits intact:
   live limbs (0..top−1) *and* stale bytes between `top` and `dmax`.
   That memory becomes allocator free-list/tcache inventory (module 01
   chapter 05), where a use-after-free or over-read (module 02
   chapter 05) — or Heartbleed — can read it. `BN_clear_free`
   overwrites every limb up to `dmax` before release: the allocator
   inherits zeros, not key material.

</details>
