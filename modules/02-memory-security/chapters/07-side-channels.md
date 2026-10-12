# Chapter 07 — Side channels

> **The 30-second version.** Every attack so far corrupts bytes. This chapter is about
> attacks that corrupt nothing: attacker and victim share silicon — caches, DRAM rows,
> branch predictors — and the victim's *use* of that hardware leaves physical traces
> that timing reveals. Prime+Probe, Flush+Reload, and Evict+Reload turn the cache into
> a radio station broadcasting the victim's access pattern; Spectre weaponizes
> speculation to touch data the program never architecturally accessed and encode it
> into cache state; Meltdown did the same to kernel memory and forced KPTI; Rowhammer
> flips bits in DRAM with nothing but access patterns. The defense — constant-time
> code plus partitioning — is only ever partial: while two trust domains share a
> machine, timing is a function of the work each side did, and work leaks.

## The boundary that isn't

Chapter 01 drew the trust boundary through the *address space*: page tables, pointers,
file descriptors. Every exploit chain in chapters 02–05 crosses it by writing bytes it
should not own. First-principles question: what else do attacker and victim share, and
is any of it covered by the boundary?

Nearly everything, and none of it. The two processes share the core's L1/L2 when the
scheduler alternates them, the shared L3 whenever they coexist, the DRAM banks, the
branch predictors, the execution ports. The page-table boundary is enforced only in
the memory *map* — below it, the hardware is one machine with no concept of your
security policy. Caches are indexed by physical address and hold lines from every
process on the box.

A **side channel** is a channel whose medium is a shared physical resource and whose
signal is the victim's use of it. The transmitter needs no bug and sends nothing
voluntarily — which breaks Chapter 01's bug → primitive → exploit → impact chain:
impact with no vulnerability, because the "vuln" is that hardware performance depends
on inputs. (A **covert channel** is the same physics used by two colluding processes
to talk, e.g. across an air-gap VM boundary.) The founding result is Kocher 1996, who
recovered RSA keys by timing the *math*: modular exponentiation runs faster on
exponents with fewer set bits, and the exponent was the secret. The general law: any
computation whose work varies with secret data leaks that data through its running
time. Cache attacks are the same idea with bandwidth turned up from bits-per-minute to
kilobytes-per-second.

## The cache-attacker family

Derive the attacks from two facts you own: a set has N ways and a line maps to exactly
one set (module 01 ch02), and a hit costs ~4–70 cycles while a DRAM miss costs
~200–400+ (the lab printed 52 vs 374). If the victim's access to some line changes
*the attacker's own timing*, that is an oracle.

**Prime+Probe** (Osvik, Shamir, Tromer 2006) needs no shared page — only the same L3.
Fill every way of one set with your lines, let the victim run, reload your fillers. A
filler that now goes to DRAM was evicted, and only lines mapping to that same set can
evict it:

```
one L3 set (say 16 ways)          attacker's timeline
+-------------------------------------------+
| A A A A A A A A A A A A A A A A |  1. PRIME: fill every way
+-------------------------------------------+
        (victim runs, elsewhere or later)
+-------------------------------------------+
| A A A A A A A A A A | V | A A A A A |  2. victim's line V maps to
+-------------------------------------------+     this set -> one evicted
+-------------------------------------------+
| A A A A A A A | ....slow.... | A A A A |  3. PROBE: reload each filler;
+-------------------------------------------+     one DRAM-bound reload = leak
```

The resolution is a *set*, not a line: an 8 MB 16-way L3 has 8,192 sets, so one probe
distinguishes 13 bits of set index but nothing about which line inside the set — and
your own kernel traffic fills sets too, so noise is high.

**Flush+Reload** (Gullasch et al. 2011; Yarom & Falkner 2014) is the scalpel. x86's
`clflush` is unprivileged and evicts one line, by virtual address, from every cache
level. But to probe *the victim's* line you must have it mapped — and because caches
are physically indexed, that means the same physical line, i.e. a shared page. This
same-line requirement defines the attack surface:

```
      ONE SHARED PHYSICAL PAGE (e.g. a page of libc mapped in both
       processes: the page cache keeps ONE physical copy, so both
       address spaces hit the same lines)

 attacker (spy)               victim                      line X state
 ----------------------------------------------------------------------
 1. clflush(line X)                                       line X: cold
 2. (attacker idles)                                      line X: cold
 3.                          if (secret_bit) touch(X);   bit=1 -> X warm
                             else              touch(Y); bit=0 -> X cold
 4. t = timed reload of line X
      t ~  50-150 cycles => HIT  => victim touched X  => bit = 1
      t ~ 200-400 cycles => MISS => victim skipped X  => bit = 0
 ----------------------------------------------------------------------
 measured on this chapter's machine: 52 vs 374 cycles (see the lab)
```

That picture is the whole attack class: the victim's secret-dependent *whether-to-touch*
becomes the attacker's *how-fast-did-my-reload-come-back*. Yarom and Falkner used it
to steal a 2048-bit GnuPG RSA key from one decryption: exponentiation touches different
lines for "square" versus "multiply" steps (module 01 ch21 makes the same observation
benignly).

Where do shared pages come from? Three default routes: shared libraries — every process
mapping `libc.so` or `libcrypto.so` shares physical lines with every other; the page
cache maps the same file pages into everyone who `mmap`s a file; and **KSM**, the
kernel's content-based dedup, merges identical anonymous pages *across VMs*, which took
Flush+Reload into the cloud. "Shared pages" and "attack surface" are the same sentence.

**Evict+Reload** (Gruss et al. 2015) replaces `clflush` with eviction: fill the set with
conflicting addresses so the victim's line falls out, then reload. You need it whenever
flushing is unavailable — from JavaScript, or on ARM platforms where user-mode cache
maintenance traps (Apple Silicon is the canonical case, which is why x86-64 demos do
not port).

One piece of arithmetic: a Flush+Reload round leaks `log2(64 / element_size)` bits — the
line, not the byte, is the resolution. A secret index into a byte table leaks 6 bits per
access; into a `uint64_t` table, 3.

## Clocks, and the noise floor

The read-out is a timing measurement, so measurement is the bottleneck: separate ~50-cycle
hits from ~400-cycle misses on a machine that lies at every scale.

- **`rdtsc`/`rdtscp`** read the cycle counter directly — cycle resolution (~0.3 ns at
  3.3 GHz), but not serializing. `rdtscp` waits for prior instructions to retire, yet
  the timed load can still slip past the second read, so serious code uses
  `lfence; rdtsc; <load>; rdtscp; lfence`. Under virtualization the hypervisor can
  scale or offset the TSC, and migration between cores with unsynchronized TSCs makes
  deltas garbage (want `constant_tsc nonstop_tsc` in `/proc/cpuinfo`; still no
  guarantee inside VMs).
- **`perf_event_open`/`perf stat`** is kernel-mediated: migration-correct, with
  hardware counters (`cycles`, `L1-dcache-load-misses`). The tradeoff: it counts over
  a region, not per access, and it is gated by `/proc/sys/kernel/perf_event_paranoid`
  — this chapter's box ships 2 (user-space counting only); Ubuntu 22.04+ ships 4, which
  blocks unprivileged perf outright. Practical attackers in 2024 use `rdtscp` plus
  their own statistics.

The statistics *are* the attack engineering. Between flush and probe the core takes
interrupts, gets preempted, the SMT sibling streams video, the frequency governor
rescales the clock. Each event *inflates* a timing — noise adds, never subtracts — so
the toolkit is: repeat, take the minimum or a low percentile, and set the threshold from
a calibration histogram of known-hit vs known-miss runs. Flush+Reload's ~350-cycle gap
sits comfortably above the ~30–60-cycle jitter floor; Prime+Probe's gap (tens of
cycles) is why its traces need thousands of samples where Flush+Reload needs one. The
lab prints exactly such a two-blob histogram.

## Transient execution: the machine that never happened

Speculation is the performance engine of every modern core (module 01 ch20 builds on
it): the branch predictor guesses, execution charges ahead, a mispredict squashes the
pipeline. The ISA contract says mispredicted work never happened — registers roll back,
transient faults never fire. That contract covers *architectural state*. The cache is
not architectural state: a transient load brings a line in, and the squash does not
undo cache fills.

Draw the consequence. A transient instruction can (a) compute with data the program
was never allowed to read, (b) encode the result into which cache line is warm, and
(c) leave no architectural trace. Step (c) fails only because of step (b) — the
encoding is the Flush+Reload oracle from the previous section. That is the entire
Spectre class (Kocher et al. and Google Project Zero, disclosed January 2018).

**Bounds-check bypass** (Spectre v1, CVE-2017-5753) is the minimal case. Victim code:

```c
if (x < array1_size)                    // the guard
    y = array2[array1[x] * 4096];       // the gadget
```

The attacker cannot call this with `x` out of bounds — the guard is correct. But it can
train the predictor:

```
 1. TRAIN: call the victim many times with in-bounds x
         cmp x, size -> taken, taken, taken ...   predictor: "always taken"
 2. SETUP: clflush every line of array2, and clflush &array1_size
         (slowing the guard's load widens the speculation window)
 3. ATTACK: call the victim with x far out of bounds
         cmp x, size -------> mispredicted "taken" (it was trained)
         [transient] load array1[x]      <- reads OOB before cmp resolves
         [transient] load array2[v*4096] <- v = stolen byte; one line
                                              of array2 warms per v
         cmp resolves: x >= size -> squash -> y vanishes, no fault
         (the cache fill is NOT rolled back)
 4. READOUT: Flush+Reload over array2 -> the warm line's index = v
```

The 4096-byte slot stride is deliberate — one target per page stops the TLB and the
prefetcher from coupling slots, the same trap the lab dodges. The transient window is
~100–200 µops, plenty for two loads. Published PoCs leak tens to a few thousand bytes
per second depending on variant and noise.

Fixes are partial and layered: compilers insert `lfence` speculation barriers after
bounds checks (GCC 8+ has `__builtin_speculation_safe_value`); branch-target injection
(v2, CVE-2017-5715) got **retpolines** (2018, kernel 4.15) and later hardware modes
(Intel eIBRS, 2022). The class keeps producing — MDS/Fallout/ZombieLoad 2019, LVI 2020,
RetBleed 2022, Downfall/Inception 2023 — because the root cause, speculation that
leaves microarchitectural state, *is* the performance model. As of 2024 no shipped CPU
family removes the class; they shrink instances of it.

**Meltdown, in one paragraph.** Meltdown (CVE-2017-5754, 2018) exploited a checks-late
pipeline on 2015–2017 Intel cores: the kernel was mapped into every user page table (so
syscalls skipped a TLB flush); a user load of a kernel address faulted *eventually*,
but the transient window let the value flow into the same `array2[v*4096]` encoding
before the fault retired — unprivileged code reading kernel memory a byte per round.
KPTI (Linux 4.15, 2018, from the Graz "KAISER" idea) answers by keeping two page tables
per process, one with almost no kernel mapping, at a real cost — ~5–30% on
syscall-heavy workloads, softened by PCID. KPTI fixed Meltdown (its fault was
architectural) but not Spectre (nothing architectural was violated) — the honest
one-line summary of 2018.

## Rowhammer: the DRAM grid as an attack surface

Module 01 ch01 built DRAM from capacitors that leak, arranged in rows, opened by a word
line and sense amps, refreshed every ~64 ms. Hammering is what happens when one row's
activation disturbs its neighbors: opening and closing row A−1 a hundred thousand times
within a refresh window (Kim et al., ISCA 2014) drains capacitors in row A until a
sense-amp decision flips — *without A ever being accessed*:

```
 DRAM bank, side view (rows of 1-transistor 1-capacitor cells)

 row A-1  |||||||||||||||||||||||   <- hammer: ~10^5 activations in <64 ms
 row A    ------ 1 ---------  --    <- victim row: charge leaks in/out
 row A+1  |||||||||||||||||||||||   <- hammer ("double-sided": both neighbors)
            each hammer opens A's neighbor; the word-line/sense-amp swing
            disturbs A's cells until one flips -- A is never addressed
```

Double-sided hammering (A−1 and A+1) roughly doubles the disturbance rate; combined
with flushing the victim row out of the row buffer between hammers — `clflush` on the
addresses — it became reliable. Rowhammer.js (2015) triggered it from browser
JavaScript, where `clflush` is unavailable so eviction does the flushing (Evict+Reload's
trick again). Exploitation is a memory-map attack: Drammer (CCS 2016) groomed Android's
physical allocator so a flipped bit landed in a *page-table entry*, redirecting one
mapping onto the page tables themselves — arbitrary read/write, then root. Google's
2021 "Glitch" chain used Rowhammer to take over a Chromebook from a sandboxed context.

Every defense is partial. ECC detects and corrects single flips, but ECCploit
(2018–2019) showed patient hammering flips multiple bits inside one correction window.
**TRR** (Target Row Refresh, DDR4 from ~2019) moved defense into the DRAM chip — on
detecting hammering, refresh the neighbors — but each vendor's TRR is undocumented
heuristics, and TRRespass (USENIX 2020) showed *non-uniform* patterns bypass it on 13
of 42 DDR4 modules tested; Blacksmith (2022) fuzzed patterns and dropped more. As of
2024 no mainstream DDR4/DDR5 module family has a complete public defense: the physics
(charge sharing between adjacent rows) cannot be patched, only monitored. Note the
difference from the rest of the chapter: cache attacks leak *information*; Rowhammer
corrupts *state* — while still corrupting no bytes the attacker owns.

## The defender's menu, and why it never fully closes

**Constant-time code** is a rule set, not a library: (1) no secret-dependent *control
flow* — no branches, early returns, or loop trip counts that vary with secrets; (2) no
secret-dependent *memory addresses* — no secret-indexed tables, since the warmed line
is observable; (3) no secret-dependent *arithmetic latency* — x86 integer `div` finishes
early for some operands, so a secret divisor is a leak; (4) libraries too — table-based
AES left OpenSSL after the 2005–2010 cache attacks, for constant-time code and then
AES-NI. Implementation patterns: branchless selects with masks, bitsliced algorithms,
data-independent algorithm shapes (Montgomery ladder, multiply-always) plus blinding.
Verification exists: `ctgrind` repurposes valgrind by treating secrets as uninitialized
(secret-dependent addresses trip it), `dudect` runs a Welch's t-test on timing
distributions, libsodium ships TIMECOP on the same idea.

**Partitioning** removes the sharing: Intel **CAT** (Cache Allocation Technology, Xeon
since Broadwell-EP 2016; AMD Zen too) gives each class-of-service a contiguous bitmask
of L3 ways via the `resctrl` filesystem (Linux 4.10+); ARM's MPAM (ARMv8.4) is the
analog, sparsely deployed as of 2024. Software page coloring partitions sets by
controlling which physical pages each domain gets — research-grade bookkeeping.

**Shared-page hardening** shrinks the Flush+Reload surface: disable KSM
(`/sys/kernel/mm/ksm/run`), and load private copies of crypto libraries into
key-handling processes. For SMT — two hyperthreads share L1/L2 and execution ports —
OpenBSD has shipped with SMT off by default since 2019 (a ~20–40% throughput cost most
vendors refused), and Linux 5.14 (2021) added core scheduling, which only co-schedules
one trust domain's threads per core.

Why does the menu never close? Timing is physics: the running time of a finite
computation is a function of the work it did, and the only program whose timing leaks
nothing is one whose work is independent of its secrets. Constant-time code buys that
for one function at a performance cost — and a channel opens a level down anyway (DRAM
bank contention, port contention, predictor state, power). You can drive bandwidth
toward zero only by removing secret-dependence or removing sharing; removing sharing
means not co-locating attacker and victim — the cloud's entire economic premise. Every
defense here is a mitigation, and the chapter's first derivation — the boundary that
isn't — is permanent.

## What insiders know

- **Flush+Reload needs *physical* line sharing, so "disable dedup" fixes the wrong
  third.** Killing KSM removes cross-VM anonymous-page sharing, but shared libraries
  and the page cache still hand every process a line-level window into every other.
  Real hardening for a key-handling process: a private copy of the crypto library, or
  pages nobody else maps.
- **`rdtsc` vs `perf` is a tradeoff with flags to check**: `rdtscp` gives per-access
  cycle resolution but is hypervisor-scaled and migration-sensitive (`constant_tsc
  nonstop_tsc`, plus pin with `sched_setaffinity`); perf is migration-correct but
  region-granular and gated by `perf_event_paranoid` (2 here; Ubuntu 22.04+: 4, no
  unprivileged perf). Attackers use rdtscp with minimum-of-N statistics.
- **Constant-time has three sins to grep for**: branches on secrets, secrets in array
  indexes, secrets in variable-latency ops (x86 `div`). The branchless-select idiom
  `r = (x & m) | (y & ~m)` replaces an `if` for 2–3 instructions.
- **TRR/TRRespass status**: TRR ships in essentially all DDR4 since ~2019 and in DDR5,
  but is per-vendor secret heuristics; TRRespass (2020) bypassed it on 13/42 tested
  DDR4 modules, Blacksmith (2022) generalized the fuzzing. Plan for "mitigated, not
  solved."
- **CAT availability is SKU politics**: server Xeons since Broadwell-EP (2016) and AMD
  Zen have it; many client SKUs lack or firmware-disable it; ARM MPAM is paper-sparse.
  On Linux, check for the `resctrl` mount before designing around it.
- **One trace is often enough**: Yarom-Falkner recovered a full 2048-bit GnuPG RSA key
  from a *single* decryption's trace; Bernstein's 2005 AES attack ran remotely over a
  network with statistical averaging. Bandwidth is the attacker's convenience, not
  feasibility.

## See it yourself

A real Flush+Reload oracle, two processes, one shared page. The child (victim) touches
one line chosen by a random secret byte; the parent (attacker) never reads that byte —
it flushes a line, waits for the victim, times the reload. Save as `fr.c`:

```c
// fr.c -- Flush+Reload oracle across a fork() boundary.
// Build: gcc -O2 -o fr fr.c     (x86-64 Linux: rdtscp + clflush)
// The MAP_SHARED page stands in for a shared library page: after
// fork(), parent and child hit the same physical lines.
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <unistd.h>
#define LINES 16
#define PAGESZ 4096
static volatile uint8_t *buf;
static inline uint64_t now(void)
{   unsigned lo, hi;
    asm volatile("rdtscp" : "=a"(lo), "=d"(hi) :: "rcx");
    return ((uint64_t)hi << 32) | lo; }
static inline void flush(volatile uint8_t *p)
{   asm volatile("clflush (%0)" :: "r"(p)); }
static inline uint64_t probe(volatile uint8_t *p)    /* timed reload */
{   uint64_t t0 = now(); uint8_t v = *p; uint64_t t1 = now(); (void)v;
    return t1 - t0; }
static void spin(int n) { volatile int j = 0; while (n--) j += n; }

int main(void)
{
    /* trap 3: pin to one CPU -- the L3 is per-socket; a victim scheduled
       on the other socket is invisible to the attacker's probes        */
    cpu_set_t one; CPU_ZERO(&one); CPU_SET(0, &one);
    sched_setaffinity(0, sizeof(one), &one);
    buf = mmap(NULL, LINES*PAGESZ, PROT_READ|PROT_WRITE,
               MAP_SHARED|MAP_ANONYMOUS, -1, 0);
    /* trap 1: fault the pages in with WRITES. Until then they map the
       shared zero page; the victim's first store faults in a fresh page
       whose kernel zeroing warms EVERY line and drowns the signal      */
    for (int i = 0; i < LINES; i++) buf[i*PAGESZ] = 1;

    /* calibration: the two timing distributions on THIS CPU           */
    long h[6] = {0}, m[6] = {0};
    uint64_t bh = ~0ull, bm = ~0ull;
    for (int i = 0; i < 300; i++) {
        uint64_t c = probe(buf);
        if (c < bh) bh = c;
        h[c/100 > 5 ? 5 : c/100]++;
        flush(buf);
        spin(2000);                    /* let the writeback drain      */
        uint64_t mm = probe(buf);
        if (mm < bm) bm = mm;
        m[mm/100 > 5 ? 5 : mm/100]++;
    }
    uint64_t th = bh + (bm - bh)/2;
    printf("calibration: hit >= %lu, miss >= %lu, threshold = %lu\n",
           bh, bm, th);
    printf("  hit  bins x100cyc: %ld %ld %ld %ld %ld %ld\n",
           h[0],h[1],h[2],h[3],h[4],h[5]);
    printf("  miss bins x100cyc: %ld %ld %ld %ld %ld %ld\n",
           m[0],m[1],m[2],m[3],m[4],m[5]);

    /* the oracle: one line, one question, one timed reload            */
    int go[2], done[2], tell[2];
    pipe(go); pipe(done); pipe(tell);
    int correct = 0, trials = 100;
    for (int trial = 0; trial < trials; trial++) {
        pid_t pid = fork();
        if (pid == 0) {                          /* ---- victim ---- */
            uint8_t c, s;
            int ur = open("/dev/urandom", O_RDONLY);
            read(ur, &s, 1); close(ur);
            s &= 15;                     /* secret: which line to touch */
            for (int r = 0; r < LINES; r++) {
                read(go[0], &c, 1);      /* wait for the attacker      */
                buf[s*PAGESZ] = 1;       /* THE secret-dependent access*/
                write(done[1], "D", 1);
            }
            read(go[0], &c, 1);
            write(tell[1], &s, 1);       /* reveal, for scoring only   */
            _exit(0);
        }
        int g = -1;                              /* ---- attacker ---- */
        for (int r = 0; r < LINES; r++) { /* trap 2: ONE line per round */
            flush(&buf[r*PAGESZ]);       /* 1. FLUSH                  */
            write(go[1], "G", 1);        /* 2. victim runs            */
            read(done[0], &(char){0}, 1);
            uint64_t t = probe(&buf[r*PAGESZ]);   /* timed reload     */
            if (t < th) g = r;           /* warm = the victim touched */
        }
        write(go[1], "V", 1);
        uint8_t s; read(tell[0], &s, 1);
        if (g == s) correct++;
        waitpid(pid, NULL, 0);
    }
    printf("recovered %d/%d secret line indices = %.1f%%\n",
           correct, trials, 100.0*correct/trials);
    return 0;
}
```

```console
$ gcc -O2 -o fr fr.c && ./fr
calibration: hit >= 52 cyc, miss >= 374 cyc, threshold = 213
  hit  bins (x100 cyc): 300 0 0 0 0 0
  miss bins (x100 cyc): 0 0 0 187 54 59
recovered 100/100 secret line indices = 100.0%
```

(Run on an Intel Xeon 4th-gen-Scalable "Sapphire Rapids" vCPU under KVM, kernel 5.10;
your numbers will differ — that is the point of printing the calibration. On a laptop
expect a hit near 50–150 and a miss near 200–400; under a VM the miss often inflates
toward 500+.) What to notice:

- **The histogram is the channel**: two blobs, a ~320-cycle gap, threshold at the
  midpoint. No secret is copied, returned, or signaled — timing is the transmission.
  Zero bytes corrupted; the parent still recovers 100% of the secret nibbles.
- **The three commented traps are not decoration.** Un-pinned, this exact program
  scored 1/100 on an earlier run (the victim kept landing on the other socket).
  Skip the pre-fault writes and the kernel's page-zeroing warms the whole target set.
  Probe all 16 lines in a tight loop and the prefetcher warms the page for you.
- **Production shape**: replace the anonymous page with a page of a file both processes
  `mmap` — say a range of `/usr/lib/x86_64-linux-gnu/libc.so.6` (offset from
  `/proc/<pid>/maps`) — and you have a line-level window into the child's execution of
  any shared library, exactly how Yarom-Falkner watched GnuPG.
- On **ARM64/Apple Silicon** there is no `clflush`, and user cache-maintenance traps
  on Apple's cores: replace the flush with an eviction set and you have rewritten the
  toy as Evict+Reload — a genuinely different program.

## How to use this knowledge

- **Audit crypto by shape, not math**: three grep-able questions — does any branch
  depend on key material? any array index? any loop bound or division? A "yes" is a
  side-channel finding even when the algorithm is provably correct.
- **Read transient-execution news in 60 seconds**: when the next Spectre-class bug
  lands, map it onto the template — shared state, what executes transiently, how it is
  encoded, how it is read out. Every paper in the class fits; filling it in is how you
  decide whether your threat model cares.
- **Make cloud threat-model decisions from mechanism**: KSM, SMT siblings, and shared
  library pages are each either acceptable risk or must-be-off on a multi-tenant box,
  and you can now argue each with numbers (core scheduling's throughput cost, CAT's SKU
  availability) instead of vendor FUD.
- **Explain Spectre on a whiteboard** — train, flush, attack, probe — the
  interview-grade version: derived, not memorized, and every step is a mechanism you
  can name under follow-up questions.

## Connections

- **Module 01, Chapter 01** — the DRAM physics (leaking capacitors, rows, refresh) that
  Rowhammer abuses; the sense amp is the attack's engine.
- **Module 01, Chapter 02** — set-associativity, the 64-byte line, and the latency
  table are the entire machinery of Prime+Probe/Flush+Reload; the lab's prefetcher
  traps are that chapter's lore read adversarially.
- **Module 01, Chapter 20** — MESI explains why `clflush` evicts *everywhere* (one
  coherence object per line), and speculation's rollback semantics launch this chapter.
- **Module 02, Chapter 01** — the bug → primitive → exploit → impact chain: side
  channels break its first link, which is why they get their own chapter.
- **Module 02, Chapter 06** — the mitigation ladder assumes memory *corruption*;
  against side channels every rung is irrelevant, and new rungs (constant-time, KPTI,
  retpolines, CAT) had to be invented.
- **Module 09, Chapter 01** — happens-before defines what a *sanctioned* observer may
  see; side channels are what an unsanctioned observer sees anyway.

## Quiz

1. **Concept.** Flush+Reload requires the attacker to share a cache line with the
   victim; Prime+Probe does not. Explain why, in terms of what each attack measures and
   how caches are indexed.
2. **Concept.** On a Spectre mispredict the CPU "squashes" the transient work — yet the
   attack succeeds. Distinguish what the squash restores from what it cannot, and why
   the ISA contract does not cover the difference.
3. **Predict the number.** A victim computes RSA with left-to-right square-and-multiply
   over a 2048-bit exponent (top bit always 1). A Flush+Reload trace logs 2,897
   executions of the loop body, of which 2,047 are the always-executed square step. How
   many 1-bits are in the exponent, and why is "multiply-always" immune to this exact
   trace?
4. **Predict the output.** A (bad) library indexes `uint8_t table1[256]` and
   `uint64_t table8[256]`, in both cases with a secret byte. Given one clean
   Flush+Reload observation of each access, how many bits of the secret does each table
   leak, and where does the granularity come from?
5. **Bug diagnosis.** Code review finds:
   `if (key[i] & 0x80) acc ^= 1;` — a conditional branch, no secret-indexed array.
   Name the two distinct channels this one line opens (one needs the branch predictor),
   and give the constant-time rewrite.
6. **Bug diagnosis.** A team replaced table-based AES with constant-time bitsliced AES;
   `dudect` passes. A pen-test still recovers key bits by timing whole decryptions from
   a co-located VM. Name the most likely remaining leak, and the two cheapest
   mitigations.
7. **Bug diagnosis.** A hardened host disabled KSM, keeps hyperthreading on, and gives
   each tenant its own glibc copy — yet a Flush+Reload spy still recovers a victim's
   keystroke timing. Which sharing route almost certainly remains, and why did
   "disable dedup" not cover it?
8. **Cross-chapter.** Rowhammer sits outside Chapter 06's mitigation ladder in two
   ways: no rung applies, and its target is not bytes in the attacker's address space.
   Using module 01 ch01's cell physics, explain what the hammer disturbs, why refresh
   does not save you, and whether MTE-style tagging would defend against it by
   accident.

<details>
<summary>Answers</summary>

1. Flush+Reload evicts and probes one specific line, named by virtual address — but
   lines are placed by *physical* address, so the attacker's address names the same
   line only if it is the same physical page (shared library, page cache, KSM,
   `MAP_SHARED`). Its signal is "is THIS line warm," resolving up to 6 bits per probe
   for a byte table. Prime+Probe fills and re-reads *its own* lines in one set; the
   signal is "was my filler evicted," which requires only that the victim's accesses
   map to the same set of the same shared L3 — any process qualifies — but resolves
   only the set, not the line.
2. The squash restores *architectural state*: registers, flags, fault status — `y`
   never exists, no exception for the OOB load fires. It cannot restore
   *microarchitectural state*: cache fills (and predictor state, TLB entries, fill
   buffers) are performance structures the ISA does not model; unwinding them would
   require provenance tracking the hardware does not keep. Spectre lives entirely in
   that gap: architecturally nothing happened, physically a line is warm.
3. Left-to-right square-and-multiply squares once per remaining bit (2,047) and
   multiplies once per additional 1-bit: 2,897 = 2,047 squares + 850 multiplies, so the
   popcount is 851. Multiply-always executes the multiply every iteration (dummy
   operand when the bit is 0): a fixed 2 × 2,047 loop body, so the same trace carries
   zero bits. (Blinding randomizes the exponent per run — the trace then leaks bits of
   a value that is discarded.)
4. The observable is the 64-byte line, so one access leaks `log2(64/element_size)`
   bits: `table1` packs 64 entries per line → 6 bits; `table8` packs 8 → 3 bits. The
   granularity is the line fetch (module 01 ch02): the cache shows *which line* warmed,
   never which byte.
5. (a) The taken/not-taken pattern trains the branch predictor — and branch outcomes
   are observable: the two paths execute different code, so different *code lines* warm
   in the shared library, which Flush+Reload on the library's own text reads. (b) Even
   with a perfect predictor, path-dependent instruction streams have path-dependent
   timing. Rewrite: `acc ^= (key[i] >> 7) & 1;` — pure arithmetic, identical
   instructions and timing either way.
6. The leak is shared hardware below the ISA — most likely DRAM bank/row-buffer
   contention from the co-located tenant (SMT port contention is the runner-up).
   `dudect` passes because the *code* is constant-time, not the *machine*. Cheapest
   mitigations: partition — CAT ways via resctl if the SKU has it, or core-scheduling /
   SMT-off for that workload — and schedule the crypto on cores no tenant shares.
   Blinding and random delays only add noise.
7. The page cache: any file both processes map — and the spy deliberately maps the
   victim's `.so` files too — shares one physical copy, and that is not "dedup" but
   the normal file-mapping path. Disabling KSM stopped merging *anonymous* pages by
   content. The remaining window is the victim's execution footprint in whatever
   library both still map, hence the rule: private copies of anything a key-handling
   process executes.
8. The hammer disturbs *charge* in the victim row's capacitors (word-line and
   sense-amp swings on neighbor activations), so the flip happens in silicon the
   attacker never addresses — which is why NX, ASLR, RELRO, CFI, and PIE are all
   irrelevant: none govern DRAM cell charge. Refresh loses because it restores charge
   on a ~64 ms schedule while disturbance accumulates *between* refreshes: enough
   activations inside one window outpace it. MTE-style tagging would not defend: a bit
   flip in a page-table entry's PFN lands in someone else's metadata with a
   well-formed tag; at best a tag *mismatch* catches a corrupted pointer after the
   fact, by accident.

</details>
