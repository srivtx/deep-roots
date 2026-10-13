# Chapter 01 — Entropy: randomness from physical memory

> **The 30-second version.** A CPU is a deterministic circuit: same inputs,
> same clock, same outputs — forever. Cryptography needs the opposite, so
> every unpredictable bit your machine produces is *measured* out of analog
> physics and committed into digital memory: oscillators racing, DRAM
> capacitors leaking, diodes hissing. Those raw bits are biased and scarce,
> so they are conditioned, then fed to a DRBG that *expands* 256 honest
> bits into gigabytes of keystream. Almost every famous randomness failure
> (Debian's 32,767 keys, cloned VMs sharing one SSH key, `Math.random` IDs
> colliding) is a failure of seeding or of the estimate — never of the
> expansion. Where the bits are born, and where the counting lies.

## A deterministic circuit cannot invent a bit

State the paradox precisely. A processor is a finite state machine: given
state and instruction, the next state is a *function* — a closed
deterministic system emits nothing an observer could not have computed
on day one. Unpredictability cannot be computed into
existence; it must be **measured** from physics the CPU's determinism does
not reach, and committed into memory: a flip-flop or DRAM cell where an
analog race becomes a 0 or a 1. Every entropy source is one story: *a
physical race, sampled into a memory cell at an uncontrolled instant.*

**The ring oscillator** is the purest case. An odd number of inverters
looped has no stable state — it oscillates, frequency set by picosecond
gate delays. Run two rings free and latch one with the other: a flip-flop
clocked by ring A sampling ring B. Their phases drift, pushed by
transistor noise, so the latched bit jitters beyond any model's
prediction. Module 17 chapter 01 called this exact mechanism a **bug**: a
signal sampled in violation of setup/hold goes *metastable*, resolving
unpredictably — hence the two-flop synchronizer on every clock-domain
crossing. A TRNG cultivates the same physics as a **feature**:
metastability *is* unpredictability, harvested.

**DRAM refresh jitter** is module 01 chapter 01's leaky capacitor, reused.
A DRAM bit is a capacitor discharging through its access transistor,
refreshed every ~64 ms; the leak rate varies per cell with temperature and
process noise. Research prototypes (2018-era DRAM-decay TRNG papers)
charge cells, wait a fixed window, and read *which* decayed past the
sense-amplifier threshold — leak racing threshold, committed into a row
of memory. The storage device is also a noise meter.

**Diode noise** is the classical analog source: a reverse-biased Zener
diode above breakdown avalanches stochastically — each carrier multiplying
or not — producing wideband *shot noise*; resistors contribute
Johnson-Nyquist thermal hiss. Both are amplified and sampled into a bit;
wallets and HSMs favor them because an oscilloscope can inspect the
physics, unlike a digital black box.

## Three layers: source, conditioner, DRBG

One noisy bit-stream is not a randomness supply: biased, correlated, slow
(kb/s), unverifiable by reading. Every serious design — SP 800-90B,
Intel's silicon, the Linux kernel — is a three-layer pipeline:

```text
   analog physics                        deterministic digital
+---------------------+   +---------------------+   +--------------------------+
| ENTROPY SOURCE      |   | CONDITIONING        |   | DRBG (SP 800-90A)        |
|---------------------|   |---------------------|   |--------------------------|
| ring oscillators    |-->| AES-CBC-MAC hash   |-->| CTR_DRBG:                |
|   racing (jitter)   |   | de-bias,           |   |   state = AES key + V    |--> consumers:
| DRAM refresh decay  |   | decorrelate        |   |   out   = AES-CTR blocks |    TLS nonces,
| avalanche/shot      |   | (SP 800-90B)       |   |   reseed on fresh        |    AES keys,
|   noise             |   +---------------------+   |   entropy (2^48 cap)    |    wallet seeds
| thermal noise       |                               +--------------------------+
+---------------------+
   raw: biased, scarce,   conditioned: unbiased,    expanded: GB/s, only
   H_inf-limited          estimate-limited          as good as its seed
```

**Intel's RDRAND/RDSEED is this diagram in silicon** (Ivy Bridge, 2012,
the i7 era): oscillator jitter as source, AES-CBC-MAC conditioning per
SP 800-90B, an SP 800-90A DRBG behind `RDRAND` (bulk bytes; hundreds of
MB/s to GB/s per core, generation-dependent), and `RDSEED` tapping
earlier — scarcer conditioned seeds for *your own* DRBG.

The **controversy** deserves honesty: the circuit is undocumented — keys,
state, health tests on no datasheet — so no black-box test can rule out a
design that looks random but is predictable to its designer: the
post-Snowden 2013 "Fort Hill"/i7 argument — can an agency-shaped bias
live inside a chip you cannot open? Linus's LKML answer was
architectural: the kernel **XORs RDRAND into the pool**, never copies it
in, so a dishonest RDRAND can only fail to *add* entropy, never
*remove* any — attacker-known bytes XORed into a secret mix teach the
attacker nothing.
Trust became unnecessary. Use the hardware, never alone.

**The DRBG layer deserves formality** — the layer you program against: a
deterministic state machine with secret **state**, a **reseed interval**,
and **prediction resistance**. SP 800-90A's CTR_DRBG is the worked example
(and the model for Intel's and chapter 02's stream ciphers):

- state = AES key K plus 128-bit counter V;
- output = AES-CTR keystream under K — 2^19 bits (64 KiB) per call, 2^48
  calls between reseeds (bookkeeping thresholds, not cliffs);
- each call's update encrypts fresh blocks into a new K, V, *overwriting*
  the old — **backtracking resistance**: state theft today must not
  reconstruct yesterday's outputs (fails if old state lingers — chapter
  04's zeroization discipline);
- reseeding folds in fresh entropy — state theft has an expiry date.

Note what a DRBG cannot do: create unpredictability. Every output bit is
a function of the seed. The seed is the whole budget; conditioning and
expansion make it *spendable*.

## The estimate problem: min-entropy and the Debian disaster

If the seed is the whole budget, the budget *number* is the argument —
and the honest answer is that **the estimate is usually optimistic**. The
right measure is not Shannon entropy (average surprise) but
**min-entropy**, H∞ = −log2(p_max): the worst-case guess. A source
emitting 0 with probability 0.9 has 0.469 Shannon bits per sample but
only −log2(0.9) ≈ **0.152** min-entropy bits — an adversary always
guessing "0" is right 90% of the time, and security prices your worst
day. NIST's SP 800-90B estimators (most-common-value, collision,
compression) deliberately *shave* — lower bounds, because a certified
number must survive adversarial sources. A vendor's "256 bits" assumes
independent, stationary samples; real hardware ages, correlates, fails
warm — validated at startup, crossed fingers in year five.

**Debian, 2008** is the cautionary tale. In 2006, chasing a `valgrind`
warning about reads of uninitialized memory in OpenSSL's RNG seeding, a
Debian maintainer commented out the lines mixing those bytes into the
PRNG — understanding neither. What remained variable was effectively the
process ID, and the era's default `pid_max` was 32768: every key
generated on any affected Debian or Ubuntu machine (September 2006 to
May 2008, ~20 months unnoticed) drew from **≤ 32,767 possible
seedings**. Each PID maps to one deterministic stream, so every key type
and size had at most 32,767 values; researchers enumerated and published
the tables — hand over a "2048-bit" Debian public key and anyone could
look up its private key. A 2048-bit key with 15 bits of *seed* entropy,
broken silently everywhere at once, while the cryptography itself never
failed. **Seeding code is a single point of silent failure**: delete one
line and every derivative key is born predictable, with no test, log, or
crash.

## The Linux pipeline: LFSR pools to one ChaCha20 CRNG

The kernel's RNG traces three eras; each design decision answers the
estimate problem.

**The classic design (roughly 2.6 through 2016).** A 4096-bit **input
pool** mixed as an LFSR — incoming bytes rotate the pool and XOR into
tapped words via two twist polynomials. Feeding it: interrupt and input
timings hashed through two 128-bit **fast pools** — one rotated in
roughly every 64 events (the fast path), one far less often (the slow),
limiting correlation — plus `add_device_randomness`: MACs, serials,
device identity, *not credited as entropy* but unique per machine, so
identical boxes cannot produce identical pools. A timing estimator
credited fractional bits per event via decaying counters — famously
conservative, and gameable the other way (2013 research). The split
everyone memorized: `/dev/random` gated by the *credit*, blocking when
accounting said "empty"; `/dev/urandom` never blocked.

**The CRNG era (4.8, 2016).** Ted Ts'o replaced the output machinery
with a **ChaCha20-based CRNG** — the pool hashed once into the CRNG's
key and nonce, after which `/dev/urandom` and in-kernel consumers draw
keystream. The insight: holding a well-seeded 256-bit state, blocking is
about *state initialization*, not byte scarcity — the estimator's job
collapsed to "is it seeded *yet*?"

**The fiasco, then the rewrite.** Kernels 5.4–5.5 (late 2019) on fast,
quiet hardware exposed the flaw: SSD boots generate few interrupts,
headless boxes have no keyboard, and userspace — systemd prominently —
had converged on `getrandom(2)`, which blocks until the CRNG is seeded.
Boots stalled tens of seconds waiting for physics. Linux 5.6 (2020) —
the `random.c` changes Linus personally landed — answered with
**jitter-based fast initialization**, folding CPU execution-timing
jitter into the early seed; seconds of init became nearly instant. The
teardown completed across 5.17–5.18 (2022, Jason Donenfeld): one CRNG,
**per-CPU batched output** (tens of bytes per refill), `/dev/random`
and `/dev/urandom` converged to one read path, the blocking pool
deleted, `getrandom(2)` (3.17 syscall; vDSO 6.11) promoted to primary
API. Once initialized, **`/dev/random` and `/dev/urandom` are the same
bytes** — the man page says so. Distro kernels backport everything —
a "5.10" tag can behave like 5.18 — check the boot log, not the
version:

```text
   IRQ timings          device IDs           RDRAND (XOR'd in,
   mouse/key/disk       MACs, serials        never trusted alone)
       |                    |                       |
       v                    |                       |
  [fast pools 2x128 b]      |                       |
       |  every 64 events   |                       |
       v                    v                       v
  [input pool: 4096-bit LFSR] <---- add_device_randomness
       | extract, credit-gated
       v
  [ChaCha20 CRNG — since 4.8; per-CPU batches since 2022]
       |
       +--> getrandom(2)   <- primary API (3.17; vDSO 6.11)
       +--> /dev/urandom   <- never blocks
       +--> /dev/random    <- same CRNG since 5.17/5.18 (2022)
```

## Clones inherit their randomness: the cloud-era starvation twist

Module 15's reframe applies brutally: **a VM is a process that copied its
randomness.** Every seeding input a guest used — RNG state, seed file —
is memory in the parent's image file, and copying memory copies state.
The incident class has a signature: a fleet cloned from one golden image
where **every clone presents the same SSH host key**, same TLS key, same
anything-generated-before-the-clone. Naive templating fails this way by
default; scanners crawl clouds for it because identical keys are
visible from outside. The fixes, in order of effectiveness:

- **Generate after first boot, never before cloning** — key generation
  belongs to the instance's lifetime, not the image's.
- **virtio-rng**: a paravirtual device feeding the *host's* fresh
  entropy into the guest as `/dev/hwrng` — the host has physics the
  snapshot lacks.
- **vmgenid**: an ACPI device the hypervisor rewrites on clone or
  snapshot-restore; kernels that watch it (6.11-era support; hedged —
  check yours) re-mix the ID, turning "you are a copy" into an event.
- Distrust **seed files** (`/var/lib/systemd/random-seed` and friends):
  copied with the image, shared state by construction — entropy for
  *one machine's* reboots, never shared across machines.

The lesson composes with Debian's: entropy failures are *hereditary* —
deletion broke Debian's seed, duplication breaks the clone's. Both leave
every derivative key predictable, symptom-free.

## Fool's gold: rand(time(NULL)), Math.random, and the number sense

The expansion layer is fine; failures are upstream. The classic chain is
`srand(time(NULL))`: the seed is public to the second and the outputs
observable, so an attacker inverts from a few tokens back to the seed
(glibc's 32-bit seed, ~15 more if a PID is mixed in; PHP's
1990s–2000s session IDs died this way). *The clock is not a secret.*

**`Math.random()` in the browser** is the same trap, modern face: V8's
is XorShift128+ — 128 bits of linear state, recoverable from a handful
of outputs with a solver. Production followed the theory: React Native's
Hermes engine shipped a `Math.random` weak enough that Math.random-based
UUIDs collided at scale (facebook/hermes #1169 — a 71% collision chance
within 100,000 values), and the "MoE" browser-fingerprint incident — a
fingerprinting SDK's `Math.random()` session IDs colliding in production
— is the same class. An ID is only as unpredictable as its seed's
*min-entropy*, nothing like the 122 bits UUIDv4 implies.

**Python's `random`** is the version you can verify yourself, so learn
the attack shape here: MT19937, the Mersenne Twister — 19,937 bits of
linear state, tempering that is bit-mixing only. **624 consecutive
32-bit outputs invert to the full state**, after which every future
value — every "random" token minted afterward — is predictable; a real
attack with a real history (poker sites, embedded devices). The module's
own docs say never for security — `secrets` calls the OS. Two footnotes:
`random.random()` burns two 32-bit words per float but keeps 27+26 bits,
so float-only recovery needs ~624 samples plus linear algebra, while
`getrandbits(32)` is clean; and the two-process trap —
`random.seed(int(time.time()))` gives two programs started in the same
second *identical* streams (lab below).

**Human passwords** are the entropy floor: a
corpus-real password carries ~20–40 bits, not 128 — a random 8-char
lowercase string would be ~41 bits, and humans don't choose randomly.
Password KDFs cannot add entropy; they charge rent per guess.

How much is a bit worth? (Fast hashes, 2020s hardware, hedged;
bcrypt-class slow hashes divide speeds by ~10^5.)

| bits | guesses | who breaks it, honestly |
|------|---------|------------------------|
| 32 | 4.3 × 10^9 | a laptop CPU in seconds; a GPU faster still |
| 64 | 1.8 × 10^19 | GPU-year-class: one card years, an 8-card rig months |
| 80 | 1.2 × 10^24 | past individuals; Bitcoin's network (~6 × 10^20 H/s) in ~30 min |
| 128 | 3.4 × 10^38 | the "heat death" line: that network needs ~10^10 years |
| 256 | 2^256 | the quantum floor: Grover halves it to 128-bit-equivalent |

128 is the design line because no thermodynamically permitted classical
effort reaches it; 256 is the quantum hedge. The rest of this module is
downstream of those numbers.

## What insiders know

- **The boot-time entropy cliff is real and versioned.** 5.4–5.5 on
  quiet hardware: `getrandom(2)` blocks, systemd waits, boots hang tens
  of seconds; 5.6's jitter fast-init is the fix. The escape hatch
  `random.trust_cpu=on` credits RDRAND toward init — the gamble: honest
  CPU, instant boot; subverted CPU, everything seeded
  early is known to the subverter.
- **`entropy_avail` is vestigial.** Modern kernels show 256 and stay
  there — it gates nothing since the rewrite. The real signal is the
  boot log: `random: crng init done` (5.4–5.16 wording) or `crng
  initialized` (newer), timestamped. This platform: 30 ms after boot; a
  quiet 5.4-era box: whole seconds. That delta *is* the cliff.
- **Jitter entropy's honest status:** SP 800-90B-validated (JENT holds a
  certificate), academically contested — physics or repeatable
  microarchitectural state? — and used by the kernel to *speed
  initialization*, pointedly not as the sole security anchor.
- **Hardware wallets derive everything from ~256 bits harvested once**
  — device noise, optionally salted with user die rolls; the BIP39
  mnemonic is that seed in words. Every derived key is a deterministic
  function of it: a leaked seed is the whole wallet, forever. Never
  expose the derivative — not even derived keys, since the tree above
  them is then guessable.
- **RDRAND vs RDSEED is a rate-vs-purity split.** RDRAND: DRBG output,
  GB/s-class, for bulk. RDSEED: conditioned raw seed, scarcer — for
  seeding your own DRBG. Bulk from RDRAND, birthrights from RDSEED.

## See it yourself

Verified on this chapter's platform: Linux 5.10.134, x86-64 VM, CPython
3.12.15.

**1 — The state, old and new interfaces:**

```console
$ cat /proc/sys/kernel/random/entropy_avail
256
$ dmesg | grep -i random
[    0.030269] random: crng init done
```

The 256 is cosmetic; the log line is the truth: this VM's CRNG
initialized **30 ms** into boot (RDRAND plus a hypervisor feeding it),
while a quiet 5.4-era box timestamps seconds later — `dmesg` is how you
time the cliff.

**2 — Expansion is cheap; scarcity was never about bytes:**

```console
$ time dd if=/dev/urandom bs=1M count=64 | sha256sum
50309f79eb7dad32aa349815cd693bd5074d2c2231b6585e53342ed8b4575e60  -
real 0.0.216s
```

64 MiB in ~0.2 s — ~300 MB/s through a pipe and a hash: the DRBG is
never the bottleneck, which is why "conserving" urandom bytes was cargo
cult.

**3 — The same-second seed trap, live** (Debian's trap in miniature):

```python
# seed_trap.py
import random, time
random.seed(int(time.time()))          # second-resolution wall clock
print([round(random.random(), 6) for _ in range(3)])
```

```console
$ python3 seed_trap.py; python3 seed_trap.py
[0.951576, 0.700956, 0.065973]
[0.951576, 0.700956, 0.065973]
```

Identical streams: the seeding was the wall clock, so two processes born
in one second share one RNG. Swap in `r = random.SystemRandom()` and
outputs differ every run — the seed comes from the kernel, not the
clock. This demo is the whole argument for `secrets`.

**4 — Recover the Mersenne Twister state from 624 outputs** (the real
attack: tempering is invertible, so outputs *are* state):

```python
# mt_recover.py — attacker never learns the seed, only outputs
import random

def untemper(y):                       # inverse of MT19937's temper()
    x = y ^ (y >> 18)                  # undo y ^= y>>18 (self-inverse)
    y = x
    for _ in range(3): x = y ^ ((x << 15) & 0xefc60000)   # undo left
    y = x
    for _ in range(5): x = y ^ ((x << 7) & 0x9d2c5680)    # shifts by
    y = x
    for _ in range(3): x = y ^ (x >> 11)                  # iteration
    return x

rng = random.Random(0xdeadbeef)                  # victim's seed: unknown
observed = [rng.getrandbits(32) for _ in range(624)]   # all we can see
clone = random.Random()
clone.setstate((3, tuple(untemper(o) for o in observed) + (624,), None))
print("MATCH:", [rng.getrandbits(32) for _ in range(4)]
                        == [clone.getrandbits(32) for _ in range(4)])
```

```console
$ python3 mt_recover.py
MATCH: True
```

The clone predicts every future output of a generator whose seed it
never saw: any token or nonce minted by MT19937 after 624 observable
32-bit outputs is public knowledge.

## How to use this knowledge

- **Choosing the API:** `getrandom`/`os.urandom`/`secrets` for anything
  an adversary must not predict; `random` and `rand()` for simulation
  and games only. In review, `srand(time(NULL))`, `Math.random` near an
  ID or token, and RNG state copied across processes are blocking
  findings — each demonstrable with this chapter's labs.
- **Cloud provisioning audits:** identical SSH host keys across a fleet
  is a cloning-process bug, not a crypto bug — fix with
  generate-on-first-boot, virtio-rng, or vmgenid, then ask what *else*
  was generated before the snapshot (TLS keys, machine IDs, Kubernetes
  tokens).
- **Boot-hang triage:** a pre-login stall on a 5.4–5.5-era kernel with
  systemd is the getrandom race until proven otherwise — check `dmesg`
  for the crng-init timestamp before blaming systemd; know the three
  fixes (newer kernel, `random.trust_cpu=on` gamble, an hwrng).
- **Key-length arguments:** the table settles 32/64/80/128/256 in one
  glance, Bitcoin-yardstick and Grover caveat included — a design
  decision instead of folklore.
- **Reading incident reports:** "RNG failure" is almost never the DRBG —
  hunt upstream for the seed (Debian's deletion, the clone's
  duplication, the wall clock, the PID); that is where every real-world
  entry in chapter 05's catalog actually broke.

## Connections

- **Module 18 ch02 (AES and modes):** the DRBG here *is* AES-CTR with
  bookkeeping — CTR's nonce discipline is "the state is the whole
  budget," applied per-message.
- **Module 18 ch03 (public key):** ECDSA's nonce k descends from this
  pipeline; a biased or reused k factors the key.
- **Module 18 ch04 (TLS 1.3):** traffic keys descend from fresh
  handshake entropy — forward secrecy as reseed discipline plus
  erasure.
- **Module 18 ch05 (failure catalog):** the RNG failure genus (Debian,
  Android/Bitcoin ECDSA, duplicate SSH keys), expanded with attack
  arithmetic.
- **Module 17 ch01 (metastability as entropy):** the synchronizer
  suppresses metastable resolution; the TRNG cultivates it — the bug
  becomes the feature.
- **Module 15 ch04 (VM clones and entropy):** the clone copies process
  state, RNG state included — vmgenid is the re-randomization event.

## Quiz

1. **Concept.** A CPU is a deterministic circuit, yet your machine emits
   unpredictable bytes. Explain precisely where those bits come from,
   and why a ring-oscillator TRNG deliberately commits the exact fault
   module 17 chapter 01 spends its synchronizer section suppressing.
2. **Concept.** Linux XORs RDRAND output into the entropy pool rather
   than copying it in. Under XOR-mixing, what can a malicious RDRAND
   never do to the pool — and why does that make "is the chip
   backdoored?" a question the design need not answer?
3. **Derive the number.** Debian, 2008: after the seeding change, the
   only varying input was the process ID, and the era's default pid_max
   was 32768. How many distinct keys of a given size could exist per
   architecture, how many bits of seed entropy is that, and why does
   the 2048-bit modulus offer no protection?
4. **Predict the number.** A source emits 0 with probability 0.9, 1
   with 0.1. Compute per-sample Shannon and min-entropy, the minimum
   sample count to honestly credit 256 bits, and which of the two
   numbers a security estimate must use.
5. **Predict the output.** Two programs each run
   `random.seed(int(time.time()))` then print three `random.random()`
   values, starting within the same second. What is the relationship
   of their outputs, and what does an attacker need to predict all
   future values of either stream — generator, quantity, method?
6. **Diagnose.** A fleet cloned from one golden VM image: every clone
   offers the same SSH host key and duplicate TLS keys. Name the root
   cause in terms of state, and give one image-level and one
   hypervisor-level fix.
7. **Diagnose.** An embedded box on kernel 5.5 boots from quiet flash
   with no input devices and stalls ~90 seconds before login; the log
   shows only a late `random: crng init done`. Explain who blocks on
   what, and give three remedies with each one's trust tradeoff.
8. **Cross-chapter.** A cloud KMS (chapter 05's catalog) initializes
   each AES-GCM key's nonce counter from `machine_id XOR
   boot_timestamp`. Using this chapter's number-sense table and
   chapter 02's incoming rule about (key, nonce) pairs, diagnose the
   failure and state the fix in terms of the entropy pipeline.

<details>
<summary>Answers</summary>

1. The bits are *measured* from physics — races and noise — and committed
   into memory; no computation invents them. The TRNG latches one
   oscillator's edge with another in violation of setup/hold, so the
   flip-flop resolves metastably — unpredictable by construction.
   Module 17 ch01 suppresses that with the two-flop synchronizer
   because a *data path* needs resolution; the TRNG cultivates it
   because a *seeding path* needs the opposite.
2. Never *remove* entropy: attacker-known bytes XORed into secret pool
   bytes leave the attacker exactly as ignorant as before — at worst,
   RDRAND contributes nothing. So the design need not resolve whether
   the chip is honest: honest RDRAND improves the pool, dishonest makes
   it no worse. The question is mooted, not answered.
3. At most 32,767 seeds (PID 0 never runs) → **15 bits**. Each PID
   gives one deterministic stream, so each key type/size has ≤ 32,767
   values — enumerable by *generating* them; the published tables made
   it a lookup. The 2048-bit modulus protects against factoring a
   *randomly chosen* key; here the adversary holds the private key
   already. Key length is irrelevant when seed entropy binds.
4. Shannon: −0.9·log2 0.9 − 0.1·log2 0.1 ≈ **0.469 bits/sample**;
   min-entropy: −log2 0.9 ≈ **0.152 bits/sample**; minimum samples:
   256 / 0.152 ≈ **1,685**. The estimate must use min-entropy — the
   adversary's best single guess ("0") succeeds with p_max = 0.9, and
   security prices the worst case; Shannon overprices this source
   ~3×, which is why SP 800-90B estimators shave.
5. Identical outputs — same second, same seed, same MT19937 stream. To
   predict the future: **624 consecutive 32-bit outputs** via
   `getrandbits(32)` — untemper each (invert the four XOR/shift
   steps), `setstate` the words with index 624, and the clone matches
   every future output. Floats keep only 27+26 of 64 bits, so
   float-only recovery needs ~624 samples plus a linear solve; raw
   words are clean.
6. Root cause: keys were generated *before* the snapshot, and cloning
   copies memory — RNG state included — so every clone regenerates the
   same keys: duplication of state, not weak crypto. Image-level:
   generate keys on first boot and scrub seed files. Hypervisor-level:
   virtio-rng feeding host entropy in as `/dev/hwrng`, or vmgenid
   re-mixed when the clone rewrites it.
7. It boots faster than entropy accumulates — no input devices, few
   interrupts — so the CRNG is unseeded; systemd's early `getrandom(2)`
   blocks until initialization, and the stall ends when `crng init
   done` prints. Remedies: upgrade past 5.6 (jitter fast-init); add an
   hwrng; or `random.trust_cpu=on` — instant boot, but the gamble that
   the CPU's RNG is honest, since a *credited* (not XOR-mixed)
   subverted source yields a predictable early seed.
8. The nonce source is ~32 bits of identity plus a guessable timestamp
   — under 2^40 starting counters, seconds-to-minutes per the table,
   with birthday collisions likely across reboots. On a (key, nonce)
   collision, chapter 02's two-time-pad arithmetic hands over the
   keystream XOR — and for GCM, one reuse can reveal the
   authentication key H, forging forever. Fix: draw counters from the
   DRBG (getrandom), never identity-plus-clock.

</details>
