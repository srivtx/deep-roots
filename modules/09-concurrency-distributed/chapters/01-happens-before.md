# Chapter 01 — Happens-before

> **The 30-second version.** Module 01 chapter 20 showed cores disagreeing
> about the order of two writes. But you program a language, and between you
> and the CPU sits a second reorderer: the compiler. The **language memory
> model** is the combined promise of both, and its currency is
> **happens-before**: program order within each thread, plus manufactured
> **synchronizes-with** edges across threads (release→acquire, unlock→lock,
> thread create/join, message send→receive). Race-free programs get sequential
> consistency back as a theorem (SC-for-DRF); racy ones get torn values, hoisted
> loads, invented stores, and values out of thin air. The rest of this module
> is edge manufacturing.

## 1. Two models, stacked: the machine's and the language's

Chapter 20 (module 01) gave you the **hardware memory model**: MESI guarantees
one agreed truth per *location*, but store buffers let each core's loads run
ahead of its own parked stores, and ARM/POWER reorder far more — a contract
between an instruction stream and the silicon. Your C++ source is not an
instruction stream: the compiler hoists loads, sinks stores, rematerializes
values (module 10 chapter 02), all legal by *single-threaded* semantics.
**Two reorderers, stacked** — compiler, then CPU — and what you observe is the
composition. The **language memory model** (C++11, Rust, Java's JSR-133, Go)
is one contract covering both, in three parts:

1. A definition of **happens-before** — which cross-thread orderings are real.
2. A definition of **data race** — two conflicting accesses (same location, at
   least one a write, at least one *ordinary* non-atomic) with no
   happens-before ordering between them.
3. A verdict on races: **undefined behavior** in C++/Rust; *defined but
   weak* in Java (races get relaxed ordering).

The payoff is the **SC-for-DRF theorem**: *if all your ordinary accesses are
happens-before ordered — no races — every execution is indistinguishable from
some sequential interleaving.* Race-free code gets the interleaving fiction
back as a theorem (Adve–Hill 1990; JSR-133, 2004; C++11 followed). Racy code
gets section 4.

## 2. Deriving happens-before: program order plus manufactured edges

You want to promise a reader: "the data is ready before the flag says so."
Within one thread, order is free — **program order** is real. Across threads,
nothing is ordered by default, so the model needs a way to import one thread's
past into another's future: the **synchronizes-with edge**:

- **release-store → acquire-load**: a `memory_order_release` store pairs with
  a `memory_order_acquire` load on the same variable *that reads the value
  written* — this read-from condition matters (section 3).
- **unlock(M) → lock(M)**: a mutex is a pre-packaged release/acquire pair.
- **thread create / join**: writes before create are visible to the newborn
  thread; its writes are visible after `join()` returns.
- **channel send → receive** (Go, Rust `mpsc`, Erlang mailboxes): the payload
  and everything the sender did before sending.

**Happens-before** is the transitive closure of program order ∪
synchronizes-with: a partial order over all memory operations. Partial — some
events remain unordered, and races can only live between those. The
**message-passing (MP) litmus test** is the smallest program that needs this:

```
MESSAGE PASSING (MP):  T1: data=42; flag=1        T2: r1=flag; r2=data
                       Q: can a reader end with r1==1 AND r2==0?

  under relaxed ordering (NO edge manufactured):

  T1   st data=42 --PO--> st flag=1     stores may swap (store-store reorder)
  T2   ld flag=1  --PO--> ld data       loads may swap (load-load reorder)

  time -->   T1: [flag=1] [data=42]     either reorder alone yields
             T2: [r1=1]   [r2=0]        (r1, r2) = (1, 0). ALLOWED.

  under release/acquire (edge exists BECAUSE r1 read the released value):

  T1   st data=42 --PO--> st flag=1 (release)
                              |
                              |  synchronizes-with   (r1 read 1, the released value)
                              v
  T2   ld flag=1 (r1, acquire) --PO--> ld data == 42, GUARANTEED
       (write-read coherence: data=42 happens-before this load, so it
        must observe 42)
```

The last line is the theorem working: a write that happens-before a read of
the same variable is *the* write the read observes — the guarantee under every
producer/consumer queue ever shipped.

## 3. The ordering menu: five ways to manufacture an edge

Every atomic operation does two jobs: the access, and optionally edge
manufacturing. C++11 and Rust expose five flavors (Rust copied C++ verbatim:
`Ordering::Relaxed/Acquire/Release/AcqRel/SeqCst`); Java's `volatile` is a
*total-order* flavor, with `VarHandle` (JDK 9) adding the finer ones.

| ordering | on a store | on a load | edge manufactured |
|---|---|---|---|
| `relaxed` | atomic only | atomic only | none |
| `release` | earlier ops can't sink below | — | paired with an acquire **that reads it** |
| `acquire` | — | later ops can't hoist above | paired with the release it reads |
| `acq_rel` | both (RMW only, e.g. CAS) | both | whichever side reads |
| `seq_cst` | + one global order | + one global order | edges **plus** totality |

Read it as a price list, priced against chapter 20's holes. On x86-64 (TSO),
stores drain in order and loads respect loads, so release stores and acquire
loads are a plain `mov` — free. TSO's one hole, *a load passing its own parked
store*, is what **store buffering (SB)** exploits:

```
STORE BUFFERING (SB):  T1: x=1; r1=y        T2: y=1; r2=x
                       Q: can (r1, r2) = (0, 0)?

  each core's store parks in ITS store buffer; its own next load
  passes it (different address -- the TSO hole, module 01 ch20):

     core 0                          core 1
     ------                          ------
     [store buffer: x=1 ]            [store buffer: y=1 ]
     r1 = load y == 0                r2 = load x == 0
        ^-- y's only writer is          ^-- x's only writer is
            parked in core 1's buffer      parked in core 0's buffer

  time -->   T1: st x=1 ....... ld y=0
             T2: st y=1 ....... ld x=0

  NO load reads the other thread's store => no synchronizes-with edge
  in EITHER direction => no happens-before => both stale reads legal,
  EVEN under release/acquire (each load reads the initial 0, not a
  released value -- the read-from condition fails).
```

That is the trap in the menu: acquire/release edges are *conditional* — they
materialize only when the acquire reads the released value. SB is the litmus
where nobody reads anybody's store, so acq/rel buys nothing. Only `seq_cst`
forbids (0,0): seq_cst operations also enter one **total order S** consistent
with program order, and a two-line proof kills the outcome — whichever store is
first in S, the other thread's load follows its own store, hence the first
store, in S, and must read 1. On x86-64 that costs the locked op: a `seq_cst`
store is `xchg` (or `mov + mfence`), draining the store buffer — ~10–20 ns
uncontended (Intel Xeon, x86-64) versus release's free `mov`.

When the menu item you want exceeds what the CPU gives free, the compiler drops
in a **fence** — a standalone edge: `atomic_thread_fence(memory_order_seq_cst)`
is `mfence` on x86-64, `dmb ish` on ARM64. At the MESI level (chapter 20's
payoff): `mfence` stalls the core until its store buffer drains — every parked
write becomes globally visible *before* any later load issues. One `mfence`
between store and load in each SB thread fixes (0,0) with no total order at
all — fences are the primitive the menu is built from.

## 4. What a data race actually buys you: SC-for-DRF, negated

SC-for-DRF says race-free programs see interleavings. The contrapositive is
where folklore dies: a race is not "maybe a stale value" — it exits the
contract, and the two reorderers may do things no interleaving of your source
ever does:

- **Torn reads.** A 64-bit `long`/`double` written by one thread and read
  without an edge can appear as two 32-bit halves from *different* writes —
  Java spells this out (JLS 17.7); C++ is worse by decree (UB): the compiler
  may split a 64-bit store even on 64-bit hardware.
- **Hoisted loads.** `while (!stop) {}` with a plain `bool stop` written by
  another thread: single-threaded analysis sees no write to `stop`, so the
  compiler hoists the load out and you spin forever at `-O2` — the write
  happens, your core snoops it, but your *register* never asks.
- **Invented stores.** Take `if (flag) x = 2; else x = 1;` with a racy `flag`.
  The optimizer may rewrite it as `x = 2; if (!flag) x = 1;` — legal
  single-threaded (same final value either way) — and a racy reader can now
  observe `x == 2` while `flag == 0`: a state your source never produced.
- **Out-of-thin-air values.** The load-buffering litmus — `r1 = x; y = r1` on
  one thread, `r2 = y; x = r2` on the other, `x = y = 1` initially — could in
  principle yield `r1 = r2 = 0`: no write of 0 ever exists, yet a speculative
  machine could let each read "predict" the other's write and fulfill itself.
  Java's JSR-133 added causality rules to forbid it; they proved either too
  strong (outlawing real optimizations) or vacuous (the 2018 "out-of-thin-air
  execution is vacuous" result), and C++11 declined to try — **unfixable in
  full generality to this day**. Which is why "the benign data race" is a
  lie: a read you call benign (a stale log value!) is, to the compiler, a
  read it may hoist, cache, or feed to a transform like the invented store
  above.

## What insiders know

- **SB is the litmus acq/rel cannot touch**, hence the shibboleth for "when is
  seq_cst actually needed": two-sided handshakes (Dekker, Peterson, anything
  "both sides flag then check") and multi-variable invariants needing one
  global order. Reference counting, SPSC queues, RCU — acq/rel or weaker.
  When someone proposes seq_cst "for safety," ask which total order
  correctness depends on; no answer means acq/rel plus maybe one fence.
- **Java's `volatile` is stronger than acquire/release** — it's SC: one total
  order over all volatile accesses, so Java forbids SB's (0,0) where C++ needs
  `seq_cst`. The fine-grained modes arrived only in JDK 9's `VarHandle`
  (`plain/opaque/acquire/release/volatile`) — why pre-9 Java concurrency code
  reads as barrier-paranoid.
- **Out-of-thin-air is the bug that never dies.** Java's JMM forbids it,
  C++11 waved it through, and the 2018 vacuity result says the prohibition is
  unenforceable in general — implementations just don't do value prediction
  *yet*.
- **TSan is a happens-before checker, not a scheduler.** Per-thread vector
  clocks (one epoch per thread) shadow every access; mutex and atomic
  operations propagate epochs; a race is two conflicting accesses with no HB
  path — neither clock dominates the other. Cost ~5–15x (module 03 ch05),
  and it only sees interleavings that actually execute.
- **The litmus culture has tooling: `herd7`/`litmus7`.** The test is a
  ~15-line `.litmus` file, the model a `.cat` file (Alglave/Maranget
  herdtools7), and the checker enumerates allowed outcomes — how LKMM and
  ARMv8-A's model were validated. `litmus7` on hardware generates exactly
  the harness the lab below builds: pinned threads, a start flag, one body
  per round.
- **A (0,0) you can't reproduce is a harness problem, not a model problem.**
  The window is the store-buffer drain — tens of nanoseconds. Thread creation
  per round, barrier wakeups (µs apart), or vCPUs as SMT siblings can all
  serialize the bodies so (0,0) never fires. Real harnesses pin threads, spin
  on a start flag, and run 10^6+ rounds; expect 1-in-10^4 to 1-in-10^6 hit
  rates on x86-64.

## See it yourself

The store-buffering litmus test, both orderings, on Linux (g++ 14.2.0-19,
x86-64, 2-vCPU Intel Xeon VM; any C++11 compiler and 2 CPUs work). The
ordering is a template parameter, not a runtime argument — a runtime-selected
`memory_order` makes GCC emit the locked `xchg` for *every* store (verified on
GCC 14.2.0, x86-64):

```cpp
// sb.cpp -- store-buffering litmus test (M09 ch01 lab)
// usage: ./sb <relaxed|acqrel|seqcst> [iterations]
#include <atomic>
#include <thread>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sched.h>

std::atomic<long> x{0}, y{0};       // the two litmus locations
long r1, r2;                        // one private result slot per thread
std::atomic<long> start{0}, done{0};// round plumbing (release/acquire pairs)

void pin(int cpu) {
    cpu_set_t s; CPU_ZERO(&s); CPU_SET(cpu, &s);
    sched_setaffinity(0, sizeof s, &s);
}

template <int M> struct Mo {        // ordering resolved at COMPILE time
    static const std::memory_order st = M==1 ? std::memory_order_release
                                 : M==2 ? std::memory_order_seq_cst
                                        : std::memory_order_relaxed;
    static const std::memory_order ld = M==1 ? std::memory_order_acquire
                                 : M==2 ? std::memory_order_seq_cst
                                        : std::memory_order_relaxed;
};

template <int M> void worker() {    // pinned to CPU 1
    pin(1);
    for (long r = 1;; r++) {
        while (start.load(std::memory_order_acquire) != r) ; // parked
        y.store(1, Mo<M>::st);                     // THE store
        r2 = x.load(Mo<M>::ld);                    // THE load
        done.store(r, std::memory_order_release);  // hand result to main
    }
}

template <int M> void run(long iters) {
    std::thread w(worker<M>);
    pin(0);                         // main owns CPU 0, worker owns CPU 1
    long c00 = 0, c10 = 0, c01 = 0, c11 = 0;
    for (long r = 1; r <= iters; r++) {
        x = 0; y = 0; r1 = -1; r2 = -1;            // reset; visibility via
        start.store(r, std::memory_order_release); // start's release (plumbing)
        x.store(1, Mo<M>::st);                     // THE store (main's body)
        r1 = y.load(Mo<M>::ld);                    // THE load
        while (done.load(std::memory_order_acquire) != r) ;
        if      (r1 == 0 && r2 == 0) c00++;        // both stale: reorder won
        else if (r1 == 0 && r2 == 1) c01++;
        else if (r1 == 1 && r2 == 0) c10++;
        else                         c11++;
    }
    w.detach();
    std::printf("%-7s (r1,r2) = (0,0): %7ld  (1,0): %7ld  (0,1): %7ld  (1,1): %7ld  / %ld\n",
                M==0 ? "relaxed" : M==1 ? "acqrel" : "seqcst", c00, c10, c01, c11, iters);
}

int main(int argc, char **argv) {
    const char *mode = (argc > 1) ? argv[1] : "relaxed";
    long iters = (argc > 2) ? atol(argv[2]) : 1000000;
    if      (!strcmp(mode, "acqrel")) run<1>(iters);
    else if (!strcmp(mode, "seqcst")) run<2>(iters);
    else                              run<0>(iters);
    return 0;
}
```

```console
$ g++ -O2 -std=c++11 -pthread -o sb sb.cpp
$ ./sb relaxed 3000000
relaxed (r1,r2) = (0,0):      41  (1,0):       2  (0,1): 2997204  (1,1):    2753  / 3000000
$ ./sb acqrel 3000000
acqrel  (r1,r2) = (0,0):      40  (1,0):       0  (0,1): 2999627  (1,1):     333  / 3000000
$ ./sb seqcst 3000000
seqcst  (r1,r2) = (0,0):       0  (1,0):  903815  (0,1): 2090571  (1,1):    5614  / 3000000
```

Predict before running — the module's discipline. `relaxed`: all four
outcomes allowed. `acqrel`: still all four (the edge needs a load that *reads*
the released value). `seqcst`: three — the total order forbids (0,0). The
numbers agree: `relaxed` and `acqrel` both hit (0,0), once per ~75,000 rounds
here (your rate will differ: the window is tens of ns); `seqcst` hits it zero
times in 3,000,000. The lopsided (0,1) majority is the harness — main flips
the start flag, so it runs ~100 ns ahead of the worker. And `objdump -d sb`
closes the loop with chapter 20: `worker<0>` and `worker<1>` show
`movq $0x1, y` (plain stores), `worker<2>` shows `xchg %rax, y` (locked) —
free, free, and a store-buffer drain, section 3's price list in instructions.

## How to use this knowledge

- **Diagnosing "impossible" bug reports.** A log line shows a value your source
  never stores, or a worker ignores shutdown: ask which two accesses raced,
  and whether UB licensed the compiler to hoist, cache, or invent — the
  transforms in section 4 are the first suspects.
- **Choosing orderings by cost.** Counter increments: `relaxed` RMW.
  Publishing a pointer plus payload: `release` store / `acquire` load.
  Anything shaped like Dekker: `seq_cst` or a fence. On x86-64: free, free,
  or ~10–20 ns.
- **Reading lock-free code (chapter 03's prerequisite).** "Release on push,
  acquire on pop" stops being folklore: you can point at the exact edge each
  side manufactures and the read-from condition that makes it real.
- **Interview-grade answers.** "Why does double-checked locking need acquire
  semantics?" and "why is my queue correct but my flag handshake not?" both
  reduce to: which edges exist, and did the load actually read the store.

## Connections

- **Module 01 chapter 20** — the layer underneath: TSO's load-passes-store
  hole is SB's (0,0); `mfence`/`xchg` are store-buffer drains; ARM's wider
  reordering is why the menu is priced per platform.
- **Module 03 chapter 03** — the race family as a bug class; this chapter is
  the formal definition those bugs violate.
- **Module 03 chapter 05** — TSan's vector-clock machinery: the HB checker
  described above, its ~5–15x cost and schedule-dependence.
- **Module 09 chapter 02** — locks as pre-packaged release/acquire edges.
- **Module 09 chapter 03** — lock-free lives on this menu:
  release-on-push/acquire-on-pop, and reclamation as an edge-manufacturing
  problem (hazard pointers, epochs, RCU).
- **Module 09 chapter 04** — channels as the architect's edge: send→receive
  is synchronizes-with with ownership transfer bolted on.
- **Module 09 chapter 05** — Lamport clocks: happens-before exported to a
  world with no shared bus.
- **Module 10 chapter 02** — the compiler's reorderings (SROA, DSE,
  hoisting) that make a language model necessary: the second reorderer.

## Quiz

1. **Concept.** Name the two reorderers a language memory model must constrain,
   and why "the CPU is weak" never sufficed as an explanation of reordering
   bugs in single-source programs.

2. **Predict the output.** T1: `data.store(42, relaxed); flag.store(1,
   release);`  T2: `while (flag.load(relaxed) == 0); r2 = data.load(relaxed);`
   (both atomics). Which values can `r2` take, and why does the release store
   not save it?

3. **Predict the codegen (chapter 20 connection).** On x86-64 with `g++ -O2`,
   what implements (a) a `release` store, (b) an `acquire` load, (c) a
   `seq_cst` store? Which one pays, and for what, at the microarchitectural
   level?

4. **Bug diagnosis.** `while (!stop) {}` with plain `bool stop`, written by
   another thread, hangs forever at `g++ -O2`. Which reorderer, what transform,
   why is it legal, and the minimal fix?

5. **Bug diagnosis.** Thread 1 racy-writes plain `int flag`; thread 2 runs
   `if (flag) x = 2; else x = 1;`, and the optimizer rewrites it as
   `x = 2; if (!flag) x = 1;`. What observation becomes possible that was
   impossible in the source, and what licenses the transform?

6. **Bug diagnosis (the lab).** A colleague runs the chapter's lab, sees
   `(0,0)` under `acqrel`, and concludes the release store is "broken" or the
   compiler miscompiled. Refute both with one rule about when
   synchronizes-with edges materialize.

7. **Concept, the catalog.** Which create a synchronizes-with edge? (a)
   unlock(M) then a later lock(M); (b) writes before pthread_create, reads in
   the new thread; (c) a relaxed load that reads a relaxed store; (d) writes
   of a joined thread, reads after join; (e) a release-store read by an
   acquire-load; (f) thread A writes two *different* variables, thread B
   reads them in the opposite order.

8. **Cross-module (module 03 ch05).** TSan reports a race between two accesses
   you believe are separated by a fence. In vector-clock terms, what did it
   compute to decide "no happens-before" — and why can it miss races another
   schedule would expose?

<details>
<summary>Answers</summary>

1. **The compiler and the CPU.** The compiler reorders source under
   single-threaded legality; the CPU reorders instructions under its own
   contract. The language model is one promise covering the composition —
   which is why a bug can reproduce at `-O2` but not `-O0` (compiler layer)
   or on ARM but not x86-64 (hardware layer); "the machine is weak" explains
   at most half.

2. **0 or 42 — both possible.** The release store's edge needs an *acquire*
   (or seq_cst) load that reads the released value. The flag load is
   `relaxed`, so no edge exists and write-read coherence never applies — the
   data load may run before 42 is visible to it. `flag.load(acquire)`
   guarantees 42.

3. **(a) plain `mov`; (b) plain `mov`; (c) `xchg` (or `mov + mfence`).** TSO
   already forbids the reorderings release/acquire constrain, so they are
   free. The seq_cst store pays for the *total order*: TSO still lets a load
   pass a parked store (SB), so the locked `xchg` drains the store buffer —
   10–20 ns uncontended on this Xeon part.

4. **The compiler; the load was hoisted out of the loop.** Single-threaded
   analysis sees no write to `stop` between iterations, so reading it once is
   equivalent — legal *unless* another thread writes it, which plain `bool`
   lets the compiler assume (the race is UB). The core would snoop the write;
   the hoisted register never asks. Fix: `std::atomic<bool> stop` with
   `load(acquire)` (or `relaxed`, if only the exit flag travels).

5. **A racy reader can observe `x == 2` while `flag == 0`.** In the source,
   `x == 2` occurs only when the flag is set; after the transform, the store
   of 2 happens unconditionally and is repaired only if the flag is clear.
   Licensed because single-threaded semantics are identical (same final `x`
   either way), and the race is UB — no concurrent observer may be assumed.

6. **The read-from rule: the edge materializes only when the acquire load
   reads the value written by the release store.** In SB, each thread's load
   reads the *initial* 0, not the other thread's released 1, so no edge
   exists — (0,0) is a legal execution of correct code. Only seq_cst's total
   order (or a fence between store and load per thread) forbids it.

7. **(a), (b), (d), (e) — yes.** (c) no: relaxed-reads-relaxed creates no
   edge (atomicity is not ordering — the quiz-2 trap in reverse). (f) is not
   an edge question: two writes to different variables read in reverse is a
   coherence/total-order question — forbidden under SC/seq_cst, allowed under
   relaxed, and no single edge forbids it.

8. **TSan keeps a vector clock per thread; every intercepted sync op —
   mutex release, release-store, acquire-load — advances the actor's clock
   and merges the partner's.** Accesses race iff neither clock dominates:
   no happens-before path was built. It misses races because clocks record
   only edges the *executed schedule* created — a schedule that never ran
   builds no history (why module 03 ch05 pairs TSan with load generators).

</details>
