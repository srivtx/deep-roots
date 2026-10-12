# Chapter 01 — The bug from first principles

> **The 30-second version.** A bug is a divergence between the behavior you
> *intended* and the behavior the program *delivers*, and the intended half is
> mostly unwritten, so "it works as coded" is not a defense. Executions double
> with every branch while tests grow linearly, so every suite is a thin slice
> of an exponential space, and the slice misses corners, where the four
> failure families live — each a *mechanism*, not a symptom. Timing,
> allocator layout, and optimization level are invisible inputs: hence
> Heisenbugs and "works on my machine." Fault, error, failure, and Boehm's
> 1:10:100 curve close the argument.

## 1. The definition: a divergence, and half the spec is unwritten

Every bug report is secretly about two programs: the one you meant to write
and the one that exists.

- **The spec**: the input → behavior mappings you intend. For "print the
  file, sorted" it includes clauses no ticket ever wrote: never lose the
  file, never hang, 10 GiB files too.
- **The behavior**: the mappings the program implements — every path you
  never executed, every environment you never ran it in.

**A bug is any input (or input + environment) on which these two sets
disagree.** Three consequences:

1. **The bug exists whether or not anyone observed it.** Divergence is a
   property of the program plus environment, not of the bug report. Code
   that ran in production for a decade is not *verified*, merely
   *un-activated* (section 5: a dormant fault).
2. **"It works as coded" is a category error.** Code approximates intent;
   the bug is the gap. "Bug or feature?" asks what the spec says; where it
   is silent, the answer is a product decision that, once written, makes
   the divergence measurable.
3. **A crash is not the bug.** A crash is one *observable*, usually the
   kindest: loud and immediate. The dangerous bugs diverge silently, and
   the failure surfaces far away and much later.

## 2. Why bugs cluster: paths grow exponentially, tests grow linearly

Take the smallest interesting case, a C function with five independent
two-way branches:

```c
/* discount.c — five independent decisions, each doubling the path count */
int discount(int age, int vet, int student, int coupon, int member) {
    int d = 0;
    if (age > 64)  d += 10;   /* branch 1: 2 outcomes */
    if (vet)       d += 10;   /* branch 2: 2 outcomes */
    if (student)   d += 5;    /* branch 3: 2 outcomes */
    if (coupon)    d += 5;    /* branch 4: 2 outcomes */
    if (member)    d += 5;    /* branch 5: 2 outcomes */
    return d;
}
```

Each branch doubles the routes one call can take: five branches give
**2^5 = 32 paths**, and any test walks exactly *one* of them. "All true"
and "all false" execute every line and every branch both ways, yet touch 2
of 32:

```
   the 32 paths of discount(), one cell each (rows = bits b4b3, cols = b2b1b0)
   X = a path a test walked      . = a path you ship unexecuted

   +---+---+---+---+---+---+---+---+
   | X | . | . | . | . | . | . | . |   2 tests: 100% line coverage,
   | . | . | . | . | . | . | . | . |   100% branch coverage,
   | . | . | . | . | . | . | . | . |   2/32 = 6.25% path coverage.
   | . | . | . | . | . | . | . | X |   <- cell 11111, the all-true input
   +---+---+---+---+---+---+---+---+
```

The coverage argument in miniature, and it scales brutally:

| Two-way branches | Paths | Full path coverage at 1 ms per test |
|---|---|---|
| 5  | 32            | 32 ms |
| 20 | 1,048,576     | 17.5 minutes |
| 25 | 33,554,432    | 9.3 hours |
| 32 | 4,294,967,296 | 49.7 days |

One 32-branch function costs 49.7 *days* to path-cover; a real module has
hundreds. Inputs are not booleans either: one 32-bit argument is 2^32
points *per path*. Loops multiply further: B branches iterated n times
yield up to (2^B)^n traces. Threads: two threads of 6 atomic blocks each
admit C(12,6) = **924 interleavings**; as one loop iteration run 3 times,
924^3 ≈ 7.9 × 10^8.

The other side of the ledger is linear: minutes per test, a CI budget in
hours. **Every test suite is a thin, linear slice of an exponential space,
and bugs cluster where the slice is thinnest**: boundaries between path
regions (off-by-ones), branch *combinations* (each branch tested alone, the
pair never co-executed), rare interleavings. "Corner case" folklore is this
arithmetic.

## 3. The four families: classify by mechanism, not symptom

Symptoms (crash, hang, wrong number, lost data) are many-to-one: every
family can produce every symptom, so symptom triage tells you nothing.
Classify by *mechanism*, the root cause in the execution machinery. Module
01's arc yields exactly four ways the model in the code can diverge from
the model in the head:

| Family | Defining property (mechanism) | Canonical shapes | Detector (→ ch05) |
|---|---|---|---|
| **Memory** | Accesses bytes the program does not own: out of bounds, out of lifetime, or misinterpreted. | Overflow, use-after-free, double-free, uninitialized read. | ASan |
| **Concurrency** | The outcome depends on an interleaving no line of code specifies. | Data race, TOCTOU, deadlock, lost wakeup. | TSan |
| **Numeric / parsing** | A value crosses a representation boundary silently. | Integer overflow, 0.1+0.2, encoding, 2038. | UBSan, property tests |
| **State** | An invariant spanning time or components is broken by a *sequence*, not any single step. | Retry storms, inconsistent snapshots, partial failure. | Invariant checks, fault injection |

One root fact each. Memory: in C/C++ a pointer is a raw number and nothing
checks at runtime which bytes it may name — module 01 ch21's four
primitives, which is why ~70% of severe CVEs at Microsoft and Chromium
(2019) are memory-safety bugs. Concurrency: each thread's code is
sequentially correct; the bug lives *between* threads. Numeric: the
mathematical value and its machine encoding are different objects, and the
conversion is silent. State: every step is locally legal; the forbidden
sequence is nobody's line of code. Mechanism also predicts visibility:
memory faults are deterministic given a layout; concurrency faults need a
rare interleaving to fire — the next section.

## 4. Heisenbugs and "works on my machine": the invisible inputs

The inputs you list in a test: argv, files, network. The inputs your
program *also* reads, unlisted: thread schedule, allocator state, code
layout, optimization level, core count, ASLR base, locale, timezone. The
spec never pins them, so divergences keyed to them are bugs by section 1's
definition.

A **Heisenbug** is a failure whose probability is dominated by invisible
inputs, which is why *observing* it changes it: attach gdb and every thread
stops (schedule changed); add a printf and you allocate a buffer and take a
lock (allocator state and timing changed). The bug appears to flee the
instrument (1980s folklore; its opposite, the always-reproducing bug, is a
*Bohrbug*).

**"Works on my machine" is the same statement in memory-model terms.** The
program is really (source + toolchain + allocator + scheduler); change the
machine and the memory model changes, and it decides which failures you
get. The chain comes from module 01 ch19: glibc's tcache is a per-thread
LIFO cache (bins of 7 chunks), so one extra allocation anywhere (a locale
switch, one more printf) changes which chunk your dangling pointer
aliases. On glibc ≥ 2.29 (verified on 2.41), a tcache free clobbers only
the first 16 user bytes (`next` + `key`), so a use-after-free at offset 16+
reads *old, plausible data* and passes; under ASan the same access hits a
poisoned redzone and aborts. Same source, different heap: silent wrong
answer on one machine, crash on the other.

The compiler plays the same trick. In *See it yourself*, a two-thread
counter with a data race loses about half its updates at `-O0` and prints
the correct total at `-O2`, because gcc 14.2 legally collapsed the loop
into one `addq` — a data race is undefined behavior in C11 (§5.1.2.4), so
the optimizer owes it nothing (module 10 ch02). **"Cannot reproduce" is a
fact about your reproduction, not about the fault.** Match the invisible
inputs, not "same OS."

## 5. Fault, error, failure — and the price of finding out late

The dependability community (Avizienis, Laprie, Randell, Landwehr, "Basic
Concepts and Taxonomy of Dependable and Secure Computing," IEEE TDSC 2001)
splits "a bug" into a causal chain. A **fault** is the defect in the code:
static, cheap to fix. An **error** is
the deviated internal state an activated fault produces, invisible from
outside. A **failure** is the delivered service deviating from the spec.

```c
/* the fault: writes n+1 bytes into an n-byte destination */
void copy_name(char *dst, const char *src, size_t n) {
    for (size_t i = 0; i <= n; i++)   /* <= — off-by-one */
        dst[i] = src[i];
}
```

```
 FAULT                  ERROR                  FAILURE
 (code, dormant)        (deviated state)       (observed)

 i <= n in loop  ->  dst[64] hits the    ->  glibc aborts in free():
                      adjacent chunk's      "invalid next size (fast)",
                      size field:           10^7 cycles later, deep
                      0x31 -> 0x41          in malloc, no app frames
     |                  |                    ^
     | activation:      | propagation:       |
     | a 64-byte name   | chunk is freed,    |
     | finally arrives  | reused             |
     v                  v                    |
  (may take years)   (silent)            (the site you debug first)
```

Read it right to left: the failure site is three stages from the fault.
malloc is not the culprit but the *detector* that caught the error ten
minutes late — and that is what sanitizers automate, reporting **errors**
at propagation time, before they become failures (ch05). The chain explains
three pieces of folklore: rebooting "fixes" things because restart clears
*errors* while the *fault* stays in the code; defensive checks are
error-containment barriers; old code that "worked for years" never met its
activation input.

**Why late is expensive.** Boehm's defect-cost escalation (*Software
Engineering Economics*, 1981, IBM and TRW data; refreshed by Boehm & Basili
2001): a defect costing 1 unit at requirements time costs ~10 by
implementation and ~100 after release, for large projects (small
non-critical ones run ~5:1). The curve is exponential in phase because each
phase stacks artifacts on the assumption — design decisions, calling code, tests, docs,
deployed data — and a late fix invalidates them all. For the memory and
state families the error has already propagated into persisted data, so
field cost includes data repair, not just a recompile.

Two managed metrics make the iceberg measurable. **Defect density** (defects
per KLOC): JPL's "Power of Ten" rules (Holzmann, 2006) exist so Class A
flight code can hold ≤ 0.1 defects/KLOC, bought with static analysis plus
review; typical delivered commercial code measures 1–5 defects/KLOC (Capers
Jones's sampling — an order of magnitude, not a constant). **Defect-removal
efficiency** (the iceberg ratio, found vs shipped): Jones and Bonsour (*The
Economics of Software Quality*, 2011) measure ~85% of defects removed
before release on average projects, >95% for the best teams combining
inspections with testing. The average team ships one bug per six it finds;
your users are the last test suite you own.

## What insiders know

- **The 100:1 number gets misquoted constantly.** Boehm & Basili (2001) is
  *100:1 for large projects, 5:1 for small non-critical ones*; a universal
  "10x" is the folklore version.
- **glibc tcache leaves freed data mostly intact.** On glibc ≥ 2.29 a
  tcache free clobbers only the first 16 user bytes (`next` + `key`); a
  use-after-free at offset ≥ 16 reads old, plausible data and passes. Make
  it loud: `MALLOC_PERTURB_=170`. Change reuse:
  `GLIBC_TUNABLES=glibc.malloc.tcache_count=0` (glibc 2.35+ syntax).
- **Sanitizers perturb what they observe.** ASan moves every allocation
  (redzones plus 1:8 shadow) and quarantines frees; TSan slows execution
  5–15x, changing interleavings. "Does not reproduce under the sanitizer"
  is not evidence of absence.
- **A bug report without the optimization level is not reproducible.** The
  same data race lost ~47% of 2,000,000 increments at `-O0` and printed the
  correct total at `-O2` (gcc 14.2, x86-64: one `addq $0xf4240`). Build
  flags are part of the program's identity, like the allocator.
- **gcov counts arcs, not paths.** The lab's 5-branch function reports "12
  branches" (10 from the ifs, 2 from main's usage guard). 100% branch
  coverage was 2/32 paths.
- **~70% launched the rewrite wave.** MSRC (Matt Miller, BlueHat 2019) and
  Chromium (2019) each measured ~70% of severe vulnerabilities as
  memory-safety bugs (module 01 ch21 has the receipts).

## See it yourself

Both labs ran on Debian 13, gcc 14.2.0, glibc 2.41, x86-64, 2 cores. Your
numbers will differ; that difference *is* section 4.

**Lab 1 — count the paths, then watch the metric lie:**

```bash
$ gcc --coverage -O0 discount.c -o discount   # = -fprofile-arcs -ftest-coverage
$ ./discount 70 1 1 1 1
discount=35
$ ./discount 30 0 0 0 0
discount=0
$ gcov -b discount.c
```

```text
File 'discount.c'
Lines executed:100.00% of 13
Branches executed:100.00% of 12
Taken at least once:91.67% of 12
Calls executed:66.67% of 3
```

Two runs, 13/13 lines, every `if` taken both ways — yet only 2 of 32 paths.
The one untaken arc (91.67%) is main's usage guard: the tool counts *arcs*,
never paths. Now pay for the whole space:

```bash
$ rm -f discount.gcda && for i in $(seq 0 31); do
    ./discount $(( (i&1) ? 70 : 30 )) $(( (i>>1)&1 )) $(( (i>>2)&1 )) \
               $(( (i>>3)&1 )) $(( (i>>4)&1 )) > /dev/null; done
$ gcov -b discount.c     # same 91.67%, but all 32 paths now ran
```

Thirty-two launches to path-cover a 20-line function; each new branch
doubles the bill.

**Lab 2 — a Heisenbug you can schedule and unschedule:**

```bash
$ gcc -O0 -pthread race.c -o race0 && gcc -O2 -pthread race.c -o race2
$ for i in 1 2 3; do ./race0; done            # two cores, -O0
counter=1081828 (expected 2000000)
counter=1070473 (expected 2000000)
counter=1035707 (expected 2000000)
$ for i in 1 2 3; do ./race2; done            # same source, -O2
counter=2000000 (expected 2000000)
counter=2000000 (expected 2000000)
counter=2000000 (expected 2000000)
$ objdump -d race2 | grep -A1 '<add>:'
    addq   $0xf4240,0x2e65(%rip)        # the whole loop, one instruction
$ for i in $(seq 1 10); do taskset -c 0 ./race0; done | uniq -c
     10 counter=2000000 (expected 2000000)
```

Same source, same machine, same day, three outcomes: at `-O0` on two cores
~47% of increments are lost, a *different number every run* (timing is an
input); at `-O2` the compiler legally collapsed the racy loop into one
`addq` (data race = UB, C11 §5.1.2.4); pinned to one core, 13/13 runs
printed the correct total — the scheduler stopped supplying the losing
interleaving. The fault (unsynchronized `counter++`) is present in every
configuration; the *failure* is optional.

## How to use this knowledge

- **Walk crashes backwards through the chain.** The abort in `free()` is a
  *failure*; hunt the *error* (which byte, which chunk) and the *fault*
  (which write crossed a boundary). Never patch at the failure site.
- **Pick the detector from the family, not the symptom.** Memory → ASan,
  concurrency → TSan, numeric UB → UBSan, state → invariant checks plus
  fault injection. The wrong sanitizer on a race proves nothing (ch05).
- **Reproduce by matching the invisible inputs.** Allocator
  (`LD_PRELOAD=libjemalloc.so.2`, `MALLOC_PERTURB_=170`,
  `glibc.malloc.tcache_count=0`), build flags (`-O0` vs `-O2`), core count
  (`taskset`), ASLR (`setarch -R`).
- **Defend review time with the curve.** A fault caught in review is the
  1-unit fix; post-release it is the 100-unit one plus incident response.
  That is the business case for review and CI in one number.

## Connections

- **Module 01 ch19 (allocators)** — tcache LIFO and the tunables behind
  "works on my machine."
- **Module 01 ch21 (memory security)** — the four primitive memory failures
  and the ~70% statistic; ch02 is that chapter as a review checklist.
- **Module 02 ch05, ch06** — the attacker's view of the same faults:
  use-after-free as exploitation primitive, the mitigation ladder as
  containment.
- **Module 03 ch02–05** — the families in depth, then the finding machinery:
  today's gcov is the toy version of coverage-guided fuzzing.
- **Module 10 ch02 (queued)** — the optimizer's license over UB: why Lab
  2's `-O2` transformation is legal.

## Quiz

**Q1 (concept).** A teammate closes your ticket: "not a bug — the code does
exactly what I wrote." What is missing from that sentence, between which
two sets does the bug live, and why does that make "bug or feature" a
product decision?

**Q2 (concept).** Define fault, error, and failure, one line each, in the
dependability sense. Why does rebooting make the symptom vanish without
fixing anything?

**Q3 (predict the number).** A function has 7 independent two-way branches.
(a) How many paths? (b) At 1 ms per test, how long is full path coverage?
(c) What is the smallest branch count for which full path coverage at 1 ms
per test exceeds 24 hours? Derive it.

**Q4 (predict the number).** Two threads each execute 6 atomic blocks,
order fixed within each thread. (a) How many interleavings? (b) If those
blocks are one iteration of a loop each thread runs 3 times, how many
total? Give the formula, then the numbers.

**Q5 (diagnosis).** Ten minutes after startup, a C server (glibc 2.41)
aborts with `free(): invalid next size (fast)`, stack entirely inside
malloc. Review finds `for (size_t i = 0; i <= n; i++) dst[i] = src[i];`
copying 64-byte names into a 64-byte heap buffer. Name the fault, the
error, the failure, and explain the ten-minute gap.

**Q6 (diagnosis).** A service crashes about once per 1,000 runs in
production and never under the debugger. Adding printf logging halves the
crash rate. Name the class of bug, the invisible inputs, and explain why
(a) the debugger and (b) the printf each changed the outcome.

**Q7 (diagnosis, cross-module).** Same source, two machines: the dev laptop
(glibc 2.41) runs clean; production (jemalloc via `LD_PRELOAD`, more cores)
segfaults on a `free()`. Using module 01 ch19, give the mechanism chain from
"different allocator" to "different failure," and name two tunables to flip
on the dev laptop.

**Q8 (cross-chapter).** In this chapter's terms, why are ~70% of severe
CVEs at Microsoft and Chromium memory-safety bugs (module 01 ch21) rather
than an even split across the four families? Give at least two mechanisms.

<details>
<summary>Answers</summary>

1. The sentence defines only the *behavior* set. The bug lives between the
   **spec** (intended input → behavior mappings, mostly unwritten) and the
   **behavior** the program plus environment delivers; "what I wrote" pins
   the second and says nothing about the first. With the spec implicit, whether
   a divergence is a bug is an intent decision — a product call that, once
   written, makes the divergence measurable and testable.

2. **Fault**: the defect in the code, static, cheap. **Error**: the
   deviated internal state an activated fault produces, invisible from
   outside. **Failure**: the delivered service deviating from the spec.
   Rebooting clears *errors* (state) while the *fault* (code) remains —
   reboot is error containment, not repair.

3. (a) 2^7 = 128. (b) 128 × 1 ms = 0.128 s. (c) 24 h = 8.64 × 10^7 ms; need
   2^B > 8.64 × 10^7, so B > log2(8.64 × 10^7) ≈ 26.36, hence B = 27.
   Check: 2^26 = 67,108,864 tests ≈ 18.6 h (fits); 2^27 = 134,217,728 ≈
   37.3 h (exceeds 24 h).

4. (a) Placements of thread A's 6 blocks among 12 slots: C(12,6) = 924.
   (b) 924^3 ≈ 7.89 × 10^8, since each iteration interleaves independently.
   Formula: ((k1 + k2)! / (k1! · k2!))^iterations.

5. **Fault**: the `<=` writing 65 bytes into a 64-byte buffer. **Error**:
   byte 65 corrupts the adjacent chunk's size field (0x31 → 0x41-style
   damage), silently. **Failure**: the abort in `free()`. The gap is
   activation plus propagation latency: the fault fires only when a
   64-byte name arrives, and the corrupted chunk must be freed and reused
   before glibc's check trips — 10^7 cycles later. malloc is the detector,
   not the culprit; the fix belongs at the `<=`.

6. A **Heisenbug**: a concurrency bug whose probability is set by invisible
   inputs — thread schedule, allocator state, core count, timing. (a) The
   debugger stops all threads on attach and at breakpoints, changing the
   interleaving distribution. (b) printf takes the stdout lock and
   allocates on first use (buffering), serializing timing and perturbing
   tcache state. Halving the rate with logging is a measurement effect, not
   a fix; the repair is synchronization.

7. Chain: jemalloc's size classes and free-list policy differ from glibc's
   tcache → the same allocation sequence places the 64-byte object next to
   *different* neighbors → the 65th byte hits a size field on one allocator
   and padding on the other → the check in `free()` trips only where
   metadata was hit. On the dev laptop: `LD_PRELOAD=libjemalloc.so.2` to
   match the allocator, `MALLOC_PERTURB_=170` to surface latent
   use-after-free (then `glibc.malloc.tcache_count=0`, `taskset`).
   Reproduction = matching invisible inputs.

8. (1) **No runtime check**: nothing in C/C++ verifies bounds, lifetime, or
   ownership at access time, so touching unowned bytes is expressible and
   unchecked; faults accumulate. (2) **Silent propagation**: memory errors
   corrupt state and surface as distant, severe failures (leaks,
   control-flow hijack). (3) **Determinism given layout**: memory faults
   fire on every execution of the path, so fuzzing finds them at scale
   while races hide behind rare interleavings — discovery is biased toward
   memory. (4) **Convergence**: numeric and parsing faults often *end* as
   memory corruption (an overflowed length feeds malloc → heap overflow).

</details>
