# Chapter 04 — Escape analysis, write barriers, safepoints

> **The 30-second version.** A GC language hands its compiler three duties a
> C compiler never has. First: prove when a "heap" object never needs the
> heap — escape analysis; Go decides per `&x`, HotSpot C2 dissolves objects
> into registers (SROA from chapter 02 in a GC hat). Second: report
> mutations — a generational GC must hear about every store that could
> create an old→young pointer, so the compiler emits a *write barrier* at
> each such store; the 512-byte card table is the ledger. Third: be
> stoppable — the GC needs every thread at a *safepoint*, a PC where
> references are mapped, so compiled code polls on loop backedges. And
> because JITs guess, compiled frames carry deoptimization metadata: every
> frame is *abortable*, un-compilable back to interpreter state mid-flight.

## The contract: three duties the compiler owes the collector

A tracing GC (module 01 ch12/15) needs three things it cannot get for
itself:

1. **A root map.** A register holding `0x7f3a41c0` is just bits; only the
   compiler knows if it is a reference or an integer.
2. **Mutation reports.** Generational and concurrent collectors scan
   lazily; a quiet `oldObj.next = youngRef` creates an edge they will
   never see — and they will free a live object. Every pointer store pays
   a toll.
3. **A stoppable state.** Stacks, objects, and roots move; the GC needs
   all threads frozen at a PC where the root map is valid — reached
   *cooperatively*, hence polls.

The JIT adds a fourth duty for itself: it compiled on speculation, and
speculation must be escapable at runtime — that escape hatch is
deoptimization.

## Escape analysis: proving a heap object can live and die in registers

**Go — one question per address.** Does `&x` outlive the frame that created
`x`? If yes, `x` is *moved to heap*: allocated on the GC heap, scanned,
collected. If no, `x` is a stack slot — its death is the function's return,
free. The analysis is conservative: unprovable means heap.

The subtle part is parameters. In `func id(p *int) *int { return p }`, `p`
escapes into the caller's world — the function cannot know what happens to
the result. Go records this as **"leaking param: p to result ~r0 level=0"**:
the pointer (level 0) flows to the unnamed result slot. Writing through a
pointer (`*pp = q`, destination unknown) leaks the *content* of `pp` —
level 1. The remark family, decoded:

- `moved to heap: x` — the slot becomes a heap allocation.
- `leaking param: p` — may reach a caller-invisible location; callers
  heap-allocate their arguments.
- `leaking param: p to result ~r0 level=0` — flows through the return value.
- `leaking param content: p` — something reachable *through* `p` escapes.
- `p does not escape` — provably stack-safe.
- `... argument does not escape` — a call's variadic slice stays on the
  stack, though `fmt.Println(x)` still boxes `x`.

**HotSpot C2 — objects dissolved.** `new Point(i, i+1)` asks the same
question. If the object does not escape, C2 performs **scalar replacement**:
no allocation; the fields become individual SSA values living in registers
(or spill slots — chapter 03). An "object" can end as two 32-bit values in
`r8d`/`r9d` — no header, no address, no existence as far as the GC is
concerned. Escape analysis *is SROA for a GC language*: chapter 02's
`mem2reg` extended to objects, plus bail-outs when object *identity* is
observable — locked by `synchronized`, passed to native code, given an
identity hash code. C1 does none of this; it is a C2 luxury, budgeted at
`EscapeAnalysisTimeout` = 20 s, arrays capped at
`EliminateAllocationArraySizeLimit` = 64 elements (JDK 21).

## Write barriers: rent paid at every pointer store

Module 01 ch12: minor GCs are cheap because they scan roots plus only the
old-generation cards that point into young. `oldObj.next = youngRef`
manufactures exactly such an edge; unrecorded, the next minor GC frees
`youngRef` while it is still reachable. The compiler must emit the store
*and* the report: the **write barrier**, recorded in the **card table**:

```text
  old generation (contiguous heap)            card table (side array: 1 byte / card)
  +0x0000  +------------------------------+    +----+----+----+----+----+--
           | card 0   512 bytes (2^9)     |    | 0  | 0  | 1  | 0  | 0  |   0 = clean
  +0x0200  +------------------------------+    +----+----+----+----+----+-- 1 = dirty:
           | card 1   512 bytes            |                ^      "somewhere in
           |   oldObj.next = youngRef      |<--- store ------'      this 512-B span
  +0x0400  +------------------------------+       barrier:           lives a young
           | card 2   ...                  |       card 1 -> 1      pointer"
           +------------------------------+

  the store as JIT-compiled pseudo-assembly (x86-64, HotSpot-style):
      mov    [oldObj + 12], youngRef        ; 1. the pointer store, FIRST
      ;    card address = ((oldObj + 12) >> 9) + card_table_base
      mov    byte [card_addr], 0xff         ; 2. the mark, AFTER
```

512-byte cards (HotSpot JDK 21, x86-64): the map costs 1/512 of the heap —
1 MB per 512 MB of old gen — and the mark is one shift plus one byte store,
no call. The barrier roughly doubles the store's cost, so JITs filter: only
*reference-typed* fields into *old* objects, never stores into fresh young
objects (young→anything cannot create an old→young edge).

**Exact placement — why AFTER the store.** The barrier's promise is: *if
the card is dirty, the pointer is already in the object.* Mark first, store
second, and there is a window where the card is dirty but the pointer is
not yet visible — a concurrent collector scans the card, reads the old
value, finds no young pointer, moves on; the store then lands, and the
edge is forever unrecorded. Dirty-before-store is a lie with a deadline.
Store-before-dirty cannot lie: whoever observes the dirty bit must observe
the store — a store→store ordering the runtime must enforce (module 09
ch01).

**SATB vs incremental — the barrier that goes before.** Concurrent
*marking* (G1's old-gen cycle, Go's GC) adds a second style with the
opposite placement, because it protects the *old* value. **Incremental
(Dijkstra, insertion)**: after `p.f = q`, ensure `q` gets marked — the
barrier trails the store, like card marking. **SATB (Yuasa, deletion,
snapshot-at-the-beginning)**: the invariant "everything reachable when
marking started gets marked" means overwriting `p.f` can remove the only
path to an object — so the barrier runs **before** the store, logging the
old value into a buffer, because after the store it is unrecoverable.
"After" protects what you just wrote; "before" protects what you are about
to destroy. Go 1.8+ fuses both into one hybrid barrier (module 01 ch11):
a Go pointer store compiles to the store plus a buffered slot record, not
a card byte. V8 (module 01 ch15) records exact *slots*, not cards — same
duty, tighter ledger.

## Safepoints: the collector needs a quorum, loops need a poll

To move objects, the GC stops every thread at a PC where the compiler
emitted a reference map (which registers and stack slots hold pointers —
the oop map). Those PCs are **safepoints**; compiled code cooperates by
**polling** a shared location — HotSpot uses a polling page/poll word —
and when the GC has protected it, the test faults (or branches) into a
handler that parks the thread. Straight-line code polls at returns, which
it reaches in bounded time. **Loops poll on the backedge** because a hot
loop may not return for hours — otherwise one thread holds the whole
process hostage:

```text
  backedge poll (HotSpot C2, strip-mined loop, JDK 10+):

     L_outer:  test   [poll_word]        ; the poll: one load + branch, no call
               jne    slow_path          ; -> park the thread HERE (map valid here)
               mov    ecx, 1000          ; strip size
     L_inner:  ...    1000 iterations ... ; counted body: no poll, full speed
               dec    ecx
               jne    L_inner
               jmp    L_outer            ; <- the backedge that polls
```

The metric insiders obsess over is **time-to-safepoint (TTSP)** — the gap
between "GC requests a stop" and "last thread parked," shown directly in
`-Xlog:safepoint` as `Reaching safepoint:`. A 4 ms GC pause can be 1 ms of
GC and 250 ms of waiting for one thread; users feel the total. The
notorious cause was the counted loop without a poll — fixed in JDK 10 by
**loop strip mining** (JDK-8186027): C2 wraps the counted loop in an outer
uncounted loop that polls, keeping the inner body poll-free.

**The measurement trap: safepoint bias.** A profiler that captures stacks
*at safepoints* samples where threads are when they stop — and threads stop
at polls, which live on backedges. Method entries and straight-line code
are under-sampled; loops are overweighted: a function can be 40% of wall
time yet invisible, while a modest loop looks like the whole program.
hprof-era tools and early JFR had this bias; async-profiler exists largely
to sample via signals at arbitrary moments instead. When a profile
contradicts wall clock, check where samples were taken before blaming the
code.

## Deoptimization: every JIT frame is abortable by design

C2 compiled `count(Shape s)` believing the call site monomorphic (`Tri`
only) and inlined `Tri.edges()`. The moment a `Square` arrives, the code
is *wrong for this input* and cannot be patched in place — the runtime
discards it: the method is marked **not entrant**, and any frame executing
it is converted on the spot back into an interpreter frame, locals
re-materialized from compiled slots, resuming at the matching bytecode
index. That is **deoptimization**: "uncompile this frame." Hence metadata
no C frame carries — the oop map (for the GC), the scope/safepoint table
(bytecode↔PC mapping, live locals), and out-of-line *uncommon trap* stubs
that transfer to the deopt machinery. JIT frames look weird in a core dump
because they are abortable contracts, not just code (module 04 ch05's RE
payoff). Deopt fires on tier transitions and every invalidated
speculation; until C2 recompiles, the hot loop runs at interpreter speed.

## What insiders know

- **`-gcflags='-m'` is the insider's favorite Go flag; `-m -m` (twice)
  prints the reasons** — the flow edges (`flow: ~r0 = &x`) behind each
  verdict. "Leaking param: p to result ~r0 level=0" is about the *caller's*
  argument allocation, not this function's. Go's analysis is also
  inlining-limited: function-scoped, so a returned-pointer helper avoids
  the heap only when inlined and the result dies in the caller.
- **Card size is cache-line economics:** 512 B = 2^9, so card index is
  `addr >> 9` — one shift, map cost 0.2% of old gen. G1 (JDK 21) keeps
  512-byte cards but refines them into per-region remembered sets on
  background threads; V8 records exact slots; Go 1.8+ logs pointer slots
  to per-P buffers. One duty, three ledgers.
- **TTSP is the metric that bites:** read `Reaching safepoint` (quorum
  time) separately from `At safepoint` (GC work) in `-Xlog:safepoint`.
  Large first + small second = a loop or native call stalling the world;
  the GC is innocent.
- **Safepoint bias is the profiler artifact** that made async-profiler
  necessary: poll-time samples overweight backedges. A profile that
  contradicts wall clock is a sampling-location problem before it is a
  code problem.
- **Deopt metadata is how you recognize JIT frames in RE:** PC-keyed
  oop/scope tables, uncommon-trap stubs, `made not entrant` lines in
  `-XX:+PrintCompilation` — module 04 ch05 starts from these fingerprints.

## See it yourself

**Lab A (Go) — read the compiler's escape verdicts.** Go is not installed
on the machine this chapter was written on; the output is a documented
walkthrough transcribed from go1.22.5, linux/amd64 — run it and compare
(the remarks are the point; line:column match only an identical layout).
Save as `esc.go`, then `go build -gcflags='-m' .`:

```go
package main

import "fmt"

var sink *int

type S struct{ f *int }

var gsink S

func ret1() *int {
	x := 42
	return &x
}

func use1() int {
	x := 42
	p := &x
	return *p
}

func toGlobal(p *int) { sink = p }

func id(p *int) *int { return p }

func deref(p *int) int { return *p }

func fill(p *int, v int) { *p = v }

func printlnInt(v int) { fmt.Println(v) }

func closure() func() int {
	x := 1
	return func() int { return x }
}

func copyIn(s *S) { gsink = *s }

func fresh() []int { return make([]int, 3) }

func main() {}
```

The verdicts (go1.22.5, linux/amd64):

```text
./esc.go:12:2: moved to heap: x
./esc.go:13:9: &x escapes to heap
./esc.go:22:15: leaking param: p
./esc.go:24:9: leaking param: p to result ~r0 level=0
./esc.go:26:9: p does not escape
./esc.go:28:9: p does not escape
./esc.go:30:22: ... argument does not escape
./esc.go:30:23: v escapes to heap
./esc.go:33:2: moved to heap: x
./esc.go:34:9: func literal escapes to heap
./esc.go:37:15: leaking param content: s
./esc.go:39:28: make([]int, 3) escapes to heap
```

What to notice: `use1` prints *nothing* — the pointer provably dies
in-frame. `toGlobal`/`id`/`deref` is the parameter-escape taxonomy:
stored-into-unknown (leak), returned (caller decides), dereferenced-only
(no escape). `copyIn` is the level-1 content leak: `gsink = *s` copies the
struct *and its `f` pointer* into a global. `printlnInt` is the interface
tax: the `...` slice is stack-safe, but `v` still escapes into the boxed
`any`.

**Lab B (Java, run here) — scalar replacement, measured with a per-thread
allocation counter.** OpenJDK 21.0.12 (linux/amd64, 64-bit, compressed
oops on), single-file source mode:

```java
import java.lang.management.ManagementFactory;

public class EALab {
    static final int ITERS = 100_000_000;

    // 24 B/object (64-bit HotSpot, compressed oops)
    static class Point { int x, y; Point(int x, int y) { this.x = x; this.y = y; } }

    static Object sink;

    static long sumEscapes() {          // each Point must be materialized
        long s = 0;
        for (int i = 0; i < ITERS; i++) {
            Point p = new Point(i, i + 1);
            s += p.x;
            sink = p;                        // static store: escapes
        }
        return s;
    }

    static long sumScalarized() {       // no store: C2 may dissolve every Point
        long s = 0;
        for (int i = 0; i < ITERS; i++) {
            Point p = new Point(i, i + 1);
            s += (p.x + p.y) & 1L;
        }
        return s;
    }

    static long allocBytes(Runnable r) {
        var tb = (com.sun.management.ThreadMXBean) ManagementFactory.getThreadMXBean();
        long id = Thread.currentThread().threadId();
        long before = tb.getThreadAllocatedBytes(id);
        r.run();
        return tb.getThreadAllocatedBytes(id) - before;
    }

    public static void main(String[] a) {
        for (int warm = 0; warm < 3; warm++) { sumEscapes(); sumScalarized(); }
        System.out.println("iterations per method : " + ITERS);
        System.out.println("sumEscapes()   alloc  : " + allocBytes(EALab::sumEscapes)   + " bytes");
        System.out.println("sumScalarized() alloc : " + allocBytes(EALab::sumScalarized) + " bytes");
        System.out.println("expect if all escape  : " + (long) ITERS * 24 + " bytes (24 B/Point)");
    }
}
```

Real output on this machine:

```text
$ java EALab.java
iterations per method : 100000000
sumEscapes()   alloc  : 2400000000 bytes
sumScalarized() alloc : 0 bytes
expect if all escape  : 2400000000 bytes (24 B/Point)
```

One stored reference line (`sink = p;`) is the difference between exactly
2,400,000,000 bytes and exactly **zero** (100M × 24 B). Prove the
elimination is a compile-time decision, not a language guarantee — a
10M-iteration variant (`EALab2`) with the JIT removed:

```text
$ java -Xint EALab2.java                      # interpreter only
result=10000000  time=544 ms  threadAllocated=263026112 bytes
$ java -XX:TieredStopAtLevel=1 EALab2.java    # C1 only
result=10000000  time=61 ms  threadAllocated=263027616 bytes
```

In the interpreter and in C1, all 10M Points allocate (~240 MB plus ~23 MB
JVM startup). Only C2 dissolves them — chapter 03's compile-time budget,
again. And the quorum duty, live: `java -Xlog:safepoint EALab.java 2>&1 |
grep Reaching` shows `Reaching safepoint: 3390 ns` — microseconds, because
every hot loop polls.

**Lab C (Java, run here) — watch a deoptimization happen.** A call site
goes monomorphic → bimorphic; watch the compiled code get thrown away:

```java
public class DeoptLab {
    interface Shape { int edges(); }
    static final class Tri implements Shape { public int edges() { return 3; } }
    static final class Square implements Shape { public int edges() { return 4; } }

    static int count(Shape s) { return s.edges(); }  // starts monomorphic
    public static void main(String[] a) {
        Shape only = new Tri();
        long sink = 0;
        for (int i = 0; i < 20_000_000; i++) sink += count(only);   // inlines Tri.edges()
        System.out.println("phase 1 done, sink=" + (sink % 10));
        Shape second = new Square();
        for (int i = 0; i < 20_000_000; i++) sink += count(second); // speculation broken
        System.out.println("phase 2 done, sink=" + (sink % 10));
    }
}
```

```text
$ java -XX:+PrintCompilation DeoptLab.java 2>&1 | grep "DeoptLab"
571 1146 %     4       DeoptLab::main @ 13 (101 bytes)      <- C2 OSR: loop 1, Tri inlined
phase 1 done, sink=0
577 1142       2       DeoptLab::count (7 bytes)         made not entrant
577 1145       3       DeoptLab::main (101 bytes)        made not entrant   <- deopt
580 1175 %     4       DeoptLab::main @ 62 (101 bytes)   <- recompiled, bimorphic
```

The second `Shape` arrives right after "phase 1 done" — every compiled
version of `main`/`count` is immediately `made not entrant`: frames
deoptimize to interpreter state, then C2 recompiles with the bimorphic
profile.

## How to use this knowledge

- **Go performance triage:** run `go build -gcflags='-m'` (then `-m -m`)
  on the hot path. An `escapes to heap` inside a loop is an allocation and
  a future GC bill; usual culprits are interface boxing, `fmt`, pointers
  from non-inlined helpers.
- **Java allocation work:** measure first — per-thread allocation counter
  or JFR. A hot constructor absent from the profile was scalar-replaced
  (free); one that shows has a single escaping reference (a store, a lock,
  an identity hash) worth hunting.
- **Pause forensics:** split any stall. `-Xlog:gc` says how long the GC
  worked; `-Xlog:safepoint`'s `Reaching safepoint` says how long you
  waited for threads. Large TTSP + small GC = a non-polling loop or a long
  native call — fix loop shape, not GC flags.
- **Distrust loop-heavy profiles:** if a sampling profile contradicts wall
  clock, ask whether it samples at safepoints; prefer signal-based
  samplers.
- **Reading JIT frames in dumps:** `made not entrant` lines,
  uncommon-trap stubs, and PC-keyed oop/scope tables mark abortable JIT
  frames — module 04 ch05's workflow starts here.

## Connections

- **Module 01 ch11 (Go):** Go's concurrent GC consumes the hybrid write
  barrier this chapter's compiler emits — per-P buffers from the factory
  floor.
- **Module 01 ch12 (Java/G1):** the 512-byte cards, remembered sets, SATB
  logs, and minor-GC arithmetic — manufactured by the barrier insertions
  derived here.
- **Module 01 ch15 (V8):** remembered-set slots and incremental marking —
  the same store-report duty, different ledger encoding.
- **Module 10 ch01:** where these passes sit in the pipeline — after SSA
  optimization, tied to lowering and codegen.
- **Module 10 ch02:** the unification — escape analysis is SROA
  (`mem2reg`) generalized to heap objects; barrier filtering reuses alias
  facts.
- **Module 10 ch03:** why C1 (linear scan, fast compiles) skips escape
  analysis while C2 does it — compile-time budget as a tier decision.
- **Module 04 ch05 (queued):** deopt metadata, oop maps, and uncommon
  traps as the RE fingerprints of JIT frames.
- **Module 09 ch01 (queued):** the store→mark ordering is a
  happens-before edge the GC relies on — a memory-model requirement, not
  an implementation detail.

## Quiz

1. **Concept.** The card-marking barrier is emitted *after* the pointer
   store. What failure would marking *before* the store cause, and what
   invariant does store-then-mark protect?
2. **Concept.** Straight-line code polls only at returns; loops poll on
   the backedge. What property of loops makes return-only polling
   insufficient?
3. **Predict.** For each Go function, predict the `-gcflags=-m` remark:
   (a) `func f() *int { x := 1; return &x }`, (b) `func g(p *int) *int
   { return p }`, (c) `func h(p *int) int { return *p }`.
4. **Predict.** A 64-bit HotSpot JVM (JDK 21, compressed oops on) runs a
   100M-iteration loop of `new Point(int, int)` (two `int` fields), every
   instance escaping: how many bytes are allocated, and what fills each
   object? Separately: a 512 MB old generation with 512-byte cards needs
   a card table of what size?
5. **Bug diagnosis.** A Java 21 service stalls 650 ms. The GC log shows
   a 6 ms pause; `-Xlog:safepoint` reports `Reaching safepoint:
   640000000 ns` for that stop. What class of bug is this, and why is the
   GC innocent?
6. **Bug diagnosis.** A Go microbenchmark shows one allocation per call
   though the function "only builds a small struct" — but the struct goes
   to a helper that caches it in a package-level map. What does
   `-gcflags='-m'` print on the helper, and whose allocation is
   triggered?
7. **Cross-chapter (module 01 ch12).** A minor GC faces 12 dirty cards in
   a 512 MB old generation (512-byte cards). How many bytes must it scan,
   and which compiler-inserted code made skipping the rest possible?
8. **Cross-chapter (module 04 ch05).** In a core dump, a compiled frame's
   code has out-of-line stubs that reconstruct an interpreter frame from
   compiled-frame state, plus PC-keyed tables mapping machine code to
   bytecode indices. What is this machinery for, and which JIT design
   decision made it mandatory?

<details>
<summary>Answers</summary>

1. **Store-then-mark keeps the ledger truthful.** Mark first, and there
   is a window where the card is dirty but the new pointer is not yet
   visible: a concurrent collector scans the card, reads the old value,
   finds no young pointer, moves on; the store then lands, and the edge is
   forever unrecorded — the next minor GC frees the young object while
   `oldObj.next` still points at it. After-the-store enforces "card dirty
   ⇒ store visible" — a store→store ordering the runtime must guarantee
   (module 09 ch01).
2. **Unbounded residence.** Straight-line code reaches a return in
   bounded time; a hot loop can execute its backedge billions of times
   without returning. Safepointing is cooperative — a loop that never
   polls holds the GC's quorum hostage indefinitely, and TTSP grows
   without bound. The "GC pause" is really a waiting-for-one-thread
   pause.
3. (a) `moved to heap: x` (plus `&x escapes to heap` on the return) —
   the address outlives the frame. (b) `leaking param: p to result ~r0
   level=0` — the caller must treat its argument as escaping, unless
   inlining proves otherwise. (c) `p does not escape` — only the pointee
   is read; callers may pass a stack address.
4. **2,400,000,000 bytes** — 24 B per Point: 8 B mark + 4 B compressed
   klass + 2 × 4 B fields = 20 B, rounded to 24 B by 8-byte alignment;
   100M × 24 = 2.4 GB. Card table: 512 MB ÷ 512 B = 2^20 cards =
   **1 MB**, 0.2% of old gen.
5. **Time-to-safepoint, not GC.** The GC did 6 ms of work but waited
   640 ms for one thread to reach a poll: a hot compiled loop without a
   poll (hand-rolled uncounted loop, or code that never reached C2's
   strip mining) or a long native call. `jstack` during the stall to find
   the spinning frame; re-shape the loop (bound it, add a call or
   `Thread.onSpinWait`).
6. The helper prints `leaking param: s` (or `leaking param content: ...`
   depending on what is cached): the struct flows into a package-level
   map, outliving the caller. The allocation happens at the *call site* —
   escape analysis sees the leaking param and moves the caller's stack
   struct to the heap: one allocation per call, caused by the callee's
   signature.
7. 12 cards × 512 B = **6,144 bytes** out of 512 MB — about 0.001% of
   old gen. The enabler is the write barrier at every potentially
   intergenerational reference store (store, then mark the card byte),
   which lets the collector trust "no dirty card ⇒ no old→new edge."
8. It is **deoptimization machinery**: the PC-keyed tables are
   scope/safepoint (and oop) maps; the stubs are uncommon-trap/deopt
   entry points that rebuild interpreter frames from compiled state.
   Mandatory because the JIT compiled on speculation (profiled types,
   inlining, range assumptions); when a speculation breaks, running
   frames must be un-compiled in place — every JIT frame is abortable,
   and this metadata makes aborting possible (module 04 ch05: these
   tables fingerprint JIT code in RE).

</details>
