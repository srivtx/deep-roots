# Chapter 12 — Java: the generational heap of the JVM

> **The 30-second version.** Java's answer to "who frees memory?" is "nobody —
> reachability decides." Every thread owns a private stack of frames (locals + operand
> stack — chapter 04's frame, different ISA); all threads share one heap where
> allocation is a pointer bump in a thread-private slice of eden: ~10 ns per object,
> cheaper than `malloc` (chapter 05). Reclamation is tracing GC (chapter 11 deepened
> here) on one bet: *most objects die young* — the heap splits by age (eden + two
> survivors 8:1:1, then old gen), the young are evacuated while a 4-bit header age
> counts to tenure at 15, and old gen belongs to collectors from Serial to ZGC on the
> pause/throughput/footprint axis. Numbers: HotSpot, OpenJDK 17/21,
> 64-bit, compressed oops on, unless stated.

## 1. The frame machine: one shared heap, one stack per thread

Java's four answers: a *value* is a primitive or a reference, never an object;
*ownership* is undefined (no `free`, no `delete`); *freeing* is the collector's job once
unreachable; *safety* is memory + type safety, `null` the one hole.

The JVM spec describes one heap per JVM and **one stack per thread**. Each frame has a
fixed shape, computed at class-file creation time:

- a **local variable table** — slot 0 is `this` in instance methods, then parameters,
  then locals; `long`/`double` take two slots;
- an **operand stack** — the expression workspace; `max_stack` is fixed at compile time;
- a constant-pool pointer.

`javac` computes both sizes, so frames never grow. Default: **1 MB** on 64-bit
Linux/Windows (`-Xss1m`; check `-XX:+PrintFlagsFinal -version | grep ThreadStackSize`).
Exceed it and you get `StackOverflowError`, not a segfault — the guard-region fault
becomes an exception.

The bytecode is a **stack machine**; `javap -c` shows the frame machinery directly:

```java
// a method of class Frames
static int area(int w, int h) {      // locals: [0]=w, [1]=h, [2]=product
    int product = w * h;
    return product + 1;
}
```

```text
$ javap -c Frames
static int area(int, int);
  Code:
     0: iload_0        // push locals[0] (w)          stack: [w]
     1: iload_1        // push locals[1] (h)          [w, h]
     2: imul           // pop 2, push product         [w*h]
     3: istore_2       // pop into locals[2]          []
     4: iload_2        //                             [product]
     5: iconst_1       //                             [product, 1]
     6: iadd           //                             [product+1]
     7: ireturn        // pop top = return value
```

Pushed arguments become the callee's locals[0]/[1]; the result returns on the caller's
stack — chapter 04's convention, except `iload_0` packs operation *and* slot in one
byte. Why stack-machine shaped when every real CPU is a register machine?
**Compactness**: implicit operands ("top of stack") make common instructions 1 byte,
which mattered when class files crossed modems. **Verifiability**: each instruction's
stack effect is exactly known, so type-checking is one linear abstract interpretation —
no dataflow analysis over registers. **Portability**: the spec names no registers; the
tiered JIT (C1/C2) does register allocation per CPU, with profiles. `javap -c` shows
the *pre-JIT* truth — the interpreter is only a bridge.

## 2. What every object pays: the 12-byte header and the 32 GB cliff

Every heap object, arrays included, has the same header (64-bit HotSpot,
compressed class pointers on — the default):

```text
offset  size  field
     0     8   mark word       identity hash (31 bits) | GC age (4 bits) | lock bits (2)
     8     4   klass pointer   compressed -> class metadata in Metaspace
    12     N   fields (HotSpot reorders: longs/doubles 8-aligned first, then ints,
               shorts, bytes, references last)
    ...   pad  total rounded up to a multiple of 8
```

The 8-byte **mark word** is a union — the object's administrative record (classic
64-bit split; biased locking removed in JDK 15, JEP 374):

```text
+------- 25 ------+------ 31 -----+--- 1 --+--- 4 --+--- 1 --+-- 2 --+
| unused          | identity hash | unused | GC AGE | biased | lock  |
+-----------------+----------------+--------+--------+--------+-------+
              lock: 01 unlocked | 00 thin | 10 inflated | 11 marked
```

The 4-bit **GC age** field is the pivot of section 4 — and why no object survives
more than **15** minor collections before tenure: 4 bits count 0–15, so
`MaxTenuringThreshold` defaults to and maxes at 15.

The 4-byte **compressed class pointer** is half of the compressed-oops trick: with
compressed oops (default when heap < 32 GB), *references* are 32 bits too. Objects are
8-byte aligned, so the low 3 bits of every address are provably zero — drop them. Decode
is a shift: `(narrow_oop << 3) + heap_base`; 32 bits then address 2^32 x 8 = 2^35 =
**32 GB**. Ask for 33 GB and HotSpot falls back to 8-byte references — the cliff. A
32 GB heap and a 31 GB heap are different *machines*: every reference costs 8 bytes in
the first, 4 in the second. Reference-dense graphs (trees, maps, nested DTOs) shrink by
tens of percent at 31 GB — *giving back 1 GB lets more live data fit*: "give back 1 GB,
get several GB back." (`-XX:ObjectAlignmentInBytes=16` pushes it to 64 GB, paying 8-byte field alignment.)

Field layout follows chapter 06's rules, largest-first. Worked example: `class Cell
{ long v; int i; Cell next; }` = 12-byte header, 4 bytes of padding (offset 12 is not
8-aligned and the `long` must be), `v` at 16–24, `i` at 24–28, 4-byte reference `next`
at 28–32 — **32 bytes, only 16 payload**. An `ArrayList<Integer>` pays a 4-byte
reference plus a 16-byte `Integer` per element — 20 bytes where an `int[]` uses 4:
chapter 06's boxing warning, with header teeth.

## 3. Ten-nanosecond allocation: the eden and the TLAB

Chapter 05 priced `malloc` at tens of ns: free-list search, coalescing, thread
agreement. Java's `new` is ~**10 ns** — about the cost of zeroing the memory.
Allocation happens in a **TLAB (thread-local allocation buffer)**: each thread privately
owns a few hundred KB of eden (adaptive) and bumps a pointer — `object = tlab.top;
tlab.top += size;` — then zeroes the object and writes its header.

Three `malloc` costs vanish: no free-list search (fresh contiguous space), no
synchronization (thread-private), no fragmentation bookkeeping (freed memory is
abandoned, never reused). Chapter 05's bump allocator, viable because *reclamation is
batch*. An exhausted TLAB triggers a **TLAB refill** — a new chunk of eden, one sync
per few hundred KB, not per object; oversized objects allocate from eden with a global
CAS. When *eden* runs out, nothing can bump — exactly a minor-GC trigger.

## 4. The generational bet: die young, get copied

Derive it from program structure. Locals, iterators, boxed temporaries, request-scoped
DTOs: allocated on scope entry, dead at scope exit. Long-lived data is the opposite
pole: caches, pools, singletons, config trees — born at startup, alive forever. Real
programs are bimodal: a huge mass of short-lived allocations over a small stable core.
That is the **weak generational hypothesis**. The design falls out:

1. A region where *everything is young* is cheap to collect only if cost scales with
   *live* data, not garbage: mark-sweep (chapter 11's Go) walks the garbage too; a
   **copying collector** copies survivors and abandons the rest.
2. Copying needs empty space and compacts for free (survivors end up dense — chapter
   05's fragmentation dissolved). So young gen = **eden + two survivors**,
   default ratio **8:1:1** (`SurvivorRatio=8`; young is 1/3 of the heap with
   `NewRatio=2` on Serial/Parallel; G1 sizes young dynamically, starting ~5%).

```text
     |<--------- young gen (default ~1/3 of heap) --------->|
     +--------------------------------+--------+--------+---------------+
     |             EDEN               |   S0   |   S1   |    OLD GEN    |
     |          8/10 of young         |  1/10  |  1/10  |   ~2/3 heap   |
     +--------------------------------+--------+--------+---------------+
     new objects die here     S0/S1 swap from/to each minor GC; survivors age++
     minor GC: stop-the-world copy of live eden+from -> to; tenure at age > 15
     or survivor overflow
```

3. Each minor GC copies survivors into the other survivor space — **Cheney's 1970
   algorithm**: scan to-space from the front, copy not-yet-copied referents, forward the
   pointers (breadth-first evacuation) — and increments the mark word's 4-bit age,
   tenuring at 15 or survivor overflow. Afterwards eden and old `from` are pure garbage,
   reused wholesale: cost tracks the few percent that lived, not the MB that died.
4. Objects too big for eden (or ≥ 50% of a G1 region: regions are 1–32 MB, so ≥ 1 MB
   qualifies on an 8 GB heap) skip young, going straight to old — G1 calls them
   *humongous*. Big temp arrays are old-gen pressure in disguise.

Two survivors, not one, so evacuation always has an empty contiguous target — a shape
V8 (ch 15) and the CLR (ch 13) reuse.

## 5. The needles and the old folks: card tables and the collector tour

A minor GC must find every pointer into young gen. Roots (thread stacks) are cheap;
pointers *from old gen into eden* are the problem — scanning gigabytes of old gen would
erase the point of "minor." Fix: make stores leave notes. Every reference assignment
runs a **write barrier**; the classic encoding is the **card table** — old gen in
512-byte cards, one side-table byte per card, marked **dirty** when a young
pointer is stored into an old object:

```text
  old gen, 512-byte cards           card table (1 byte/card = 1/512 of heap)
  +---------------+----+-------------+       +---+---+---+
  | card 0        |  1 | card 2      |       | 0 | 1 | 0 |  1 = dirty: "in this
  +---------------+----+-------------+       +---+---+---+     512 B lives a young ptr"
        node.child = freshObject;      write barrier flips the byte
```

Minor GC scans roots + dirty cards only; the barrier costs a few instructions per
reference store — the price of cheap minors. G1 collects *regions*, not generations, so
each region keeps a **remembered set** of the cards elsewhere pointing into it, refined
by background threads.

The old generation is the collector *tour*, one stop per pause/throughput/footprint
tradeoff:

- **Serial** (`-XX:+UseSerialGC`): single-threaded mark-sweep-compact, stop-the-world.
  Smallest footprint, pauses scale with heap — containers, tiny heaps.
- **Parallel**: same phases, multi-threaded — max throughput, 0.1–10 s pauses. Default
  through JDK 8.
- **CMS**: concurrent mark-and-sweep, brief initial-mark/remark pauses, never
  compacted — fragmenting into "concurrent mode failure." Deprecated JDK 9, removed
  14 (JEP 363).
- **G1** — default since JDK 9: heap becomes ~2048 equal regions (1–32 MB); young/old
  are *roles* of regions. Concurrent marking with **SATB** (snapshot-at-the-beginning:
  the write barrier also logs the *old* value of an overwritten reference, so the
  marker sees the graph as it was when marking began); then "mixed" GCs evacuate the
  most reclaimable regions. Pause target `-XX:MaxGCPauseMillis`, default 200 ms.
- **ZGC** (JDK 11 experimental, production 15, generational 21) and **Shenandoah**
  (JDK 12, production 15): concurrent *compaction* — the last stop-the-world holdout.
  Moving objects while threads hold pointers is the hard part: ZGC uses **colored
  pointers** (4 stolen bits atop 64-bit addresses: mark0/mark1/remapped) plus **load
  barriers** on every reference dereference, fixing stale references on the fly;
  Shenandoah originally added a **Brooks forwarding pointer** — an extra 8-byte header
  word pointing at the object's current location — now load-reference barriers. Both
  give sub-millisecond pauses independent of heap size (ZGC: 16 TB), paid in a few
  percent throughput (barriers on every load) and up to ~2x
  transient footprint ("floating garbage" outlives a cycle).

The axis in one line: Serial/Parallel buy throughput and footprint with long pauses;
G1 bounded pauses with overhead; ZGC/Shenandoah pause immunity with throughput and
memory.

## 6. Escape hatches: references, escape analysis, memory outside the heap

**Reference objects** grade reachability — you *influence* the collector rather than
command it. `SoftReference` is cleared before `OutOfMemoryError` (roughly while
free-heap-MB x 1000 ms survives — `SoftRefLRUPolicyMSPerMB`); `WeakReference` clears as
soon as the referent is otherwise unreachable; `PhantomReference` is enqueued *after*
finalization and its `get()` always returns `null` — you never touch the corpse, only
learn of the death, via a `ReferenceQueue`:

```java
// WeakHashMap idiom in miniature: dead keys report themselves
ReferenceQueue<BigGraphic> rq = new ReferenceQueue<>();
Map<Reference<BigGraphic>, Sprite> cache = new HashMap<>();
// keys don't keep graphics alive; GC enqueues dead references:
Reference<? extends BigGraphic> dead;
while ((dead = rq.poll()) != null) cache.remove(dead);   // drop the orphaned sprite
```

`finalize()` could keep an object alive anyway — the **resurrection trick**: storing
`this` into a static field inside `finalize()` saves it from the very collection
reclaiming it (it must die a *second* time). Resurrection, unbounded delay, and an extra
GC cycle per dying object are why finalization was deprecated for removal (JEP 421,
JDK 18) in favor of **`java.lang.ref.Cleaner`** — phantom-reference machinery for
registering off-heap cleanup actions.

**Escape analysis** is the JIT quietly deleting allocations. Per compiled hot method,
C2 asks: does this allocation escape — stored to the heap, passed to an uninlined call,
crossing threads? If not, **scalar replacement** dissolves it into registers and stack
slots — "objects on the stack" as a *runtime* optimization — and `synchronized` on it
is deleted outright (**lock elision**). Chapter 11's Go escape analysis moved from
compile time to JIT time: Java can be optimistic — a wrong guess deoptimizes to the
heap; Go's compiler must be statically right, so it stays conservative.

**Memory outside the heap** lives in two places. **Metaspace** holds class metadata —
klass structures, method bytecode, constant pools — in native memory, replacing PermGen,
the fixed-size region infamous for `OutOfMemoryError: PermGen space` after one redeploy
too many (removed JDK 8, JEP 122). It grows dynamically, bounded by `MaxMetaspaceSize`
(default *unbounded* — it can eat the process), with klass structures in a 1 GB
compressed class space indexed by the 4-byte header pointer. **Direct `ByteBuffer`s**
hold native memory (`allocateDirect`), freed when the buffer dies *and* a Cleaner
notices — which requires a GC to run. Four failure signatures:

- `OutOfMemoryError: Java heap space` — live set + transient > `-Xmx`; diff
  `jmap -histo:live` runs to find the growing class.
- `OutOfMemoryError: GC overhead limit exceeded` — > 98% time in GC recovering < 2%
  heap — the OOM of *thrashing*, not of size.
- `OutOfMemoryError: Metaspace` — classloader leak: redeploys, dynamic proxies,
  generated classes pinned by one live instance.
- `OutOfMemoryError: Direct buffer memory` — direct buffers exceeded
  `-XX:MaxDirectMemorySize` (default = `Xmx`) while the heap looks healthy.

## What insiders know

- **`System.gc()` exists — and RMI calls it hourly.** `-XX:+DisableExplicitGC` no-ops
  explicit calls, but direct-`ByteBuffer` Cleaners *depend* on a GC noticing their death
  — the insider move is `-XX:+ExplicitGCInvokesConcurrent` (still collects, but
  concurrently).
- **The 32 GB cliff is a cliff, not a slope.** One GB of `-Xmx` decides 4- vs 8-byte
  references: a 31 GB heap can out-hold a 32 GB one. Measure both before "upgrading."
- **`-XX:+AlwaysPreTouch`** faults every heap page at startup instead of lazily —
  seconds of startup for flat first-minute latency; standard in containers
  (`-Xms == -Xmx`).
- **`System.arraycopy` is an intrinsic** — it compiles to no Java at all; the JIT
  substitutes a hand-vectorized `memcpy`-style primitive, so element-wise copy loops
  never win. Same for `String.hashCode`, `Math.sin`, `Integer.bitCount`.
- **Allocation rate, not heap size, drives GC.** Minor GCs per second = allocation
  rate ÷ eden size: a 2 GB/s service on a 400 MB eden gets ~5 young GCs/s; growing
  young gen buys *frequency*, not cheaper GCs.
- **`-XX:+UseStringDeduplication`** (G1): duplicate `String` backing `byte[]`s share one
  array — free memory when country codes, config keys, log labels repeat millions
  of times.
- **`Integer.valueOf` caches −128..127** (`IntegerCache`, tunable
  `-XX:AutoBoxCachemax`): `==` on boxed values works small, fails at 200 — the bug
  that survives every review.

## See it yourself

OpenJDK 17+:

```bash
# 1. GC log: unified logging, 256 MB heap for readable numbers
$ java -Xms256m -Xmx256m -Xlog:gc,gc+heap Alloc.java
[0.013s][info][gc,heap] Heap region size: 1M (256 regions)      <- G1, 2048-region target
[0.128s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause)
[0.128s][info][gc] GC(0)   12M->3M(256M) 2.1ms                   <- before->after(total) pause
```

Read `12M->3M(256M) 2.1ms`: live set, committed heap, pause. Healthy young pauses are
single-digit ms; the capacity signal is *frequency* — allocation rate ÷ eden size.
`-Xlog:gc*` is the kitchen sink; `-Xlog:gc+tlab` adds TLAB refill stats.

```bash
# 2. Object layout, proven: JOL 0.16 on JDK 17 — a 3-line main:
#    println(ClassLayout.parseInstance(new Object()).toPrintable())
java.lang.Object object internals:
OFF  SZ   TYPE DESCRIPTION               VALUE
  0   4        (object header: mark)     0x00000001 (age: 0)    # the GC age bits live here
  8   4        (object header: class)    0x0000c210              # compressed class pointer
 12   4        (object alignment gap)                              # pad to 8
Instance size: 16 bytes
```

A bare `Object` is 16 bytes — 12 header + 4 alignment; print `Cell` and check its 32.

```bash
# 3. Who eats the heap:
$ jmap -histo:live $PID | head -5
 num     #instances         #bytes  class name (module)
   1:        4011221       96300240  [B                         # byte[]: String/ArrayList payloads
   2:        1202311       28864632  java.lang.String
```

`[B` on top is normal — every `String`/`ArrayList` points at one. Run it twice, minutes
apart; the class whose count grows is the leak — for Metaspace OOMs, look for custom
classloader names.

```java
// 4. Allocation cost — and EA deleting it. Alloc.java
public class Alloc {
    static final class Point { final int x, y; Point(int x, int y) { this.x = x; this.y = y; } }
    static Point[] keep = new Point[1024];

    public static void main(String[] a) {
        long n = 100_000_000, t0 = System.nanoTime(), sink = 0;
        for (long i = 0; i < n; i++) {
            Point p = new Point((int) i, (int) (i >> 32));   // dies immediately...
            if ((i & 1023) == 0) keep[(int) (i & 1023)] = p; // except every 1024th:
            sink += p.x;                                     // an escape defeats scalar replacement
        }
        System.out.printf("%.1f ns/alloc (sink=%d)%n",
                          (System.nanoTime() - t0) / (double) n, sink);
    }
}
```

With the periodic escape, expect ~5–15 ns per allocation. Delete the `keep[...]` line:
C2 scalar-replaces the object, the loop becomes register arithmetic, the timing reads
~0 ns — and the GC log goes silent. That silence *is* escape analysis.

## How to use this knowledge

- **Capacity planning is arithmetic:** allocation rate ÷ eden size = minor-GC frequency;
  cost tracks survivors. Collector choice is an SLO decision: 200 ms (G1) vs sub-ms
  (ZGC/Shenandoah) vs max throughput (Parallel).
- **Diagnose OOMs by message, not `-Xmx`:** four strings, four subsystems — heap leak
  (diff histograms), thrash (live set vs heap), classloader leak (Metaspace), off-heap
  leak (direct buffers).
- **"Java has no leaks" is false:** leaks are *reference* leaks (static caches,
  listeners, `ThreadLocal` pools) — the collector is right, the graph is wrong.
- **Interview-grade derivation:** "why is Java allocation ~10 ns?" — derivable from
  chapters 04–06 in one breath: TLAB bump, no locks, no free list, dead memory
  abandoned.

## Connections

- **Ch 04** — the JVM frame is chapter 04's locals+operand design, different ISA.
- **Ch 05** — TLAB is the bump allocator; the copying collector is the batch "free".
- **Ch 06** — header, reordering, padding: same rules with managed headers; JOL extends
  the arithmetic.
- **Ch 07** — stack-machine bytecode vs register ISAs; the JIT is the bridge.
- **Ch 08–10** — ownership inverts: no owner, reachability decides, no deterministic
  destruction; `Cleaner` is best-effort.
- **Ch 11** — tracing GC without generations or moving; this chapter is what adding
  both buys (cheap young GC, compaction) and costs (write barriers, pauses).
- **Ch 13, 15, 17** — CLR and V8 run the same 8:1:1-shaped bet; COBOL is the opposite
  pole — fixed records, nothing to collect.
- **Ch 22 + Modules 02/04/05 (queued)** — ART under a hard cap; allocation-storm DoS
  and class-file reading both build on this chapter.

*Forward links — where the rest of the program picks this chapter up:*

- **Module 19 ch04 (GC as proof search)** — the generational collectors this chapter described are re-derived there as proof search with write barriers as monotonicity.
- **Module 21 ch01 (the LCF kernel)** — the JVM bytecode verifier is a small proof checker for a type-safety theorem — you run an LCF-style kernel every time you run Java.
- **Module 10 ch04 (safepoints)** — the compiler's GC duties: where safepoints come from, and why the oop map is the proof certificate.

## Quiz

1. The JVM spec defines a stack machine; every real CPU is a register machine. Give
   two derived reasons the spec is stack-shaped, and who does the register assignment.

2. Predict the instance size of `class Cell { long v; int i; Cell next; }` on 64-bit
   HotSpot with compressed oops; account for every byte.

3. Predict total heap bytes of `new byte[5]` on that JVM, header included.

4. This passed every test: `Integer a = 100, b = 100, c = 200, d = 200;` —
   what does `(a == b) + " " + (c == d)` print, and why?

5. A service sets `-XX:+DisableExplicitGC` to silence a library's `System.gc()` calls
   and uses NIO direct `ByteBuffer`s heavily. RSS climbs for hours, `jmap -histo:live`
   looks clean, and it dies with `OutOfMemoryError: Direct buffer memory`. Explain the
   mechanism; name the flag that fixes both problems.

6. A 1 GB-heap app crawls at 100% GC CPU for minutes, then throws
   `OutOfMemoryError: GC overhead limit exceeded`. The dump shows ~980 MB live — a
   static map "cache-primed" with the whole user table. Why minutes of thrash, not a
   fast failure?

7. Cross-chapter (ch 05, ch 11): name two structural reasons a HotSpot `new` costs
   ~10 ns where glibc `malloc` costs tens of ns — and what the JVM pays in exchange.

8. Cross-chapter arithmetic: young gen 512 MB, `SurvivorRatio=8`, allocation rate 2 GB/s.
   Compute eden size and minor-GC frequency, and explain why survivors 10x smaller
   than eden do not overflow.

<details>
<summary>Answers</summary>

1. **Compactness and verifiability.** (a) Implicit operands ("top of stack") let
   common instructions be 1 byte, which mattered for downloaded class files.
   (b) Each instruction's stack effect is exactly known, so the verifier type-checks
   a method by one linear abstract interpretation of the operand stack — no dataflow
   analysis. Register assignment is the tiered JIT's (C1/C2) job, per CPU, with
   profiles; the spec stays hardware-neutral.

2. **32 bytes.** Header = 8 (mark) + 4 (klass pointer) = 12. Fields go
   largest-first: the `long` needs 8-byte alignment and offset 12 is not 8-aligned, so
   4 bytes of padding follow the header; `v` at 16–24, `i` at 24–28, the compressed
   4-byte `next` at 28–32 — already aligned. 12 + 4 + 8 + 4 + 4 = 32, only 16 payload.

3. **24 bytes.** Array header = 8 mark + 4 class + 4 length = 16, plus 5 payload =
   21, rounded to 8-alignment = 24. A `byte[1]` also costs 24 — the header dominates
   small arrays.

4. **`true false`.** Autoboxing routes through `Integer.valueOf`, which caches −128..127,
   so `a` and `b` are the *same object* — `==` (reference comparison) is true. 200 is
   outside the cache: two distinct objects, false. Never `==` boxed types — use
   `equals`; know the range (`IntegerCache`, tunable `-XX:AutoBoxCachemax`), which makes
   the bug intermittent by value.

5. **Direct buffers are freed by a Cleaner, and a Cleaner fires only when a GC
   notices the buffer is unreachable.** With a quiet heap, no GC runs, so dead buffers
   and their native memory accumulate; `DisableExplicitGC` also killed the library's
   `System.gc()` — the cleanup trigger. Fix:
   `-XX:+ExplicitGCInvokesConcurrent` — `System.gc()` still triggers collection
   (Cleaners get serviced), but concurrently. Longer term: pool or free buffers
   explicitly.

6. **The 98/2 rule.** "GC overhead limit exceeded" fires when > 98% of time is in GC
   recovering < 2% of the heap (`GCTimeLimit`/`GCHeapFreeLimit`) — a thrashing detector.
   With ~980 MB live in 1 GB, every full GC walks a nearly all-live heap, frees almost
   nothing, and re-collects immediately; the JVM spends minutes "trying"
   to rule out a transient spike. The fix is not `-Xmx`: the map is the leak — bound
   the cache and the thrash vanishes.

7. **(a) The TLAB is thread-private: no lock, no CAS, no contention — glibc needs
   per-arena locks; Go needs per-P caches for the same reason. (b) The bump pointer
   allocates from fresh contiguous eden: no free list, no coalescing, no best-fit, no
   fragmentation — dead memory is abandoned, not reused.** The JVM pays later, in
   batch: pauses proportional to survivors, plus standing footprint (eden sizing;
   ~2x live for concurrent collectors). `malloc` spreads cost evenly and never stops
   threads; Go sits between — same idea, non-moving.

8. **Eden = 512 x 8/10 ≈ 410 MB; each survivor ≈ 51 MB.** Frequency = 2 GB/s ÷ 410 MB ≈
   **5 minor GCs/s**. Survivors can be 10x smaller because copying cost is proportional
   to *live* data: only objects alive at GC time need a destination; a healthy
   survival rate is a few percent of eden (~10–40 MB), not 410 MB. If occupancy exceeds
   the space routinely, objects overflow into old gen prematurely — survival rate or
   young sizing is wrong. 8:1:1 is that bet, defaulted.

</details>
