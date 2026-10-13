# Chapter 04 — GC as proof search

> **The 30-second version.** A garbage collector is a theorem prover
> that periodically proves "this object is unreachable" and frees the
> refuted. Exact liveness — *will* this be read again? — is the
> halting problem, so every real collector proves the
> over-approximation *reachability*; the gap, retained garbage, is
> what a leak formally is. Tracing is proof search: roots are axioms,
> grey objects the frontier, black objects proved, sweep the
> refutation pass. The proof costs pointer-chasing misses (~10 s per
> live GB naive), and the field's machinery — write barriers,
> generations, cards, concurrent marking, compaction — schedules and
> cheapens it. Reference counting is the alternative: proof by
> construction, no pauses, blind to cycles. Every collector trades
> throughput (amortization), latency (scheduling), and footprint
> (overhead) against each other.

## Liveness is the theorem; reachability is the proof you can afford

A value is **live** at program point p if it *may* be read along some
future path. That is uncomputable: deciding whether a heap cell will
ever be read again is deciding whether a program reaches another
state — the halting problem wearing a heap (chapter 01's machine,
arriving in person). What a runtime *can* compute is
**reachability**: does a reference chain from a root (globals,
statics, stacks, registers at a safepoint) reach the object?

The gap between the two is the formal leak definition, re-derived
from module 01 chapters 14/15: a leak is **reachable-but-dead** —
the collector's proof ("a path exists") is valid, but no future read
will use it. The collector is not wrong; the *static roots* are: no
collector refutes an object held by a live root, however dead.
Three classic shapes cover nearly all production leaks:

- **The accidental global** — a module-level cache or static
  registry keyed by something unbounded: a permanent root proving
  dead values.
- **The forgotten listener** (module 16 ch 04) — an event bus holds
  a callback whose owner died long ago; the bus is a root that never
  did.
- **The closure that captures the world** (module 01 ch 14, in this
  chapter's lab) — a long-lived list of small closures, each pinning
  the 1 MB buffer a dead frame once owned.

All three are one bug — proof obligations left in place — and the
fix is always the same: sever an edge, never "tune the GC."

## Tracing as proof search: the worklist is the frontier

Read mark-sweep as a prover. The **root set** is the axiom list; the
invariant is inductive: an object is justified if it is a root or is
referenced by a justified object. Marking is proof construction: pop
an obligation, mark the object, push its unvisited children. The
**worklist is the proof frontier** — obligations accepted, not yet
discharged; the search ends when the frontier empties. Black =
proved; never reached = refuted *by exhaustion*; **sweep** converts
unproven into free. The **mutator** is the application mutating the
object graph; the **collector** runs the proof. Every design below
is about those two racing.

The complexity story: object graphs are adversarially cache-hostile,
so each object visit is essentially a compulsory miss, and module 01
ch 02's ladder prices a DRAM miss at ~100 ns. A 1 GiB live heap is
~10^8 pointer words:

```text
1 GiB / 8 B/chase = 1.34e8 chases  x  100 ns  ≈ 13 s single-threaded
=> effective naive mark rate ~100 MB/s per GB of live heap
```

Ten-plus seconds of proof per live gigabyte — the number motivating
parallel marking (divide the frontier), incremental marking
(interleave with the mutator), and concurrent marking (separate
threads).

## Tri-color, the lost object, and the barrier as an obligation

Interleaving the proof with the mutator breaks correctness in one
characterizable way. The **tri-color abstraction**: **white** =
unvisited (unproven), **grey** = frontier (visited, references not
expanded), **black** = proved (fully scanned). The invariant is
monotonicity: **a black object must never point to a white
object**. The classic lost object, worked in four mutator steps
(grey A, black B — already scanned — white C, `A.f = C`):

```text
      roots
        |
      [ A ] grey --f--> [ C ] white      B is BLACK (already scanned;
        |                                    fields never re-read)
        +---------------+
                        |
                      [ B ] black

 step 1: B.g = A.f     mutator stores C's ref INTO black B
 step 2: A.f = null    mutator deletes C's ref from grey A
 step 3: collector finishes scanning A -> black; frontier empty
 step 4: sweep frees C ("no proof exists")  ->  black B holds a
         DANGLING pointer: C was reachable the whole time
```

The *interleaving* — no single step — orphaned C: its only path
now leaves an object the collector has finished with. A **write
barrier** is the mutator's obligation to notify the prover on the
stores that could break monotonicity:

| barrier | intercepts | action | per-store cost | deployed |
|---|---|---|---|---|
| Dijkstra (1978, insertion) | step 1 | shade (grey) the stored target if src black, dst white | 2 color checks + enqueue; 3-10 ns hot, ~20 ns triggered | CMS; needs a **remark** rescan |
| Steele (1975, decremental) | step 1 | re-grey the *source*, forcing a rescan | 1 check (src color); rescan lands later | rarely alone |
| Yuasa (1990, snapshot-at-the-beginning) | step 2 | grey the *old value overwritten*, if white | 1 load + null test + enqueue; no color tests | G1, Shenandoah — the deployed one |

Honest instrumented-store overhead: **~2-20 ns**, a few percent of
throughput on pointer-heavy programs. Yuasa wins structurally: no
color tests, no remark phase, and it composes with generations —
the same store notification that protects concurrent marking also
dirties a remembered-set card. Its price, **floating garbage** —
objects that die during marking float one extra cycle — is bounded
and cheaper than the alternatives' machinery.

## The pause is proof latency; generations make the proof small

Run the whole proof stop-the-world: a **serial** collector on a 2 GB
live heap at ~500 MB/s marking pauses every thread for **2 GB / 500
MB/s = 4 s** — the number that motivated CMS and G1. Parallel
marking divides by workers (8 → ~500 ms), still 5x past a
human-perceptible SLO, and the rate is miss-ladder-capped. So shrink
the proof's *subject*. The **generational** idea rests on an
empirical law, not a theorem — the **weak generational hypothesis**:
in real programs, 95-99% of objects die young. The proof "this
nursery object is unreachable" is cheap to *find* because it is
nearly everything in the nursery.

Hence GC's most counterintuitive law: **cost tracks *survivors*,
not garbage and not allocation volume.** A copying young collection
scans roots and cards, copies the survivors out, and reclaims the
whole nursery in one gesture — the dead majority is freed as a side
effect of copying the live minority. Verified in this chapter's
lab: a 256 MB G1 young collection reclaims 253 MB in ~4-7 ms because
~3 MB was copied. Garbage is cheap; liveness is expensive.

Copying survivors needs the old-to-young census (old objects are
part of the root set). The **card table**: divide old space into
512-byte **cards**; a Yuasa-style barrier on old-object stores
marks the containing card dirty; young collections scan only dirty
cards. On an 8 GiB heap:

```text
  8 GiB heap
    / 512 B per card => 2^33 / 2^9 = 2^24 = 16,777,216 cards
  one byte per card  => 16 MiB of card table
  16 MiB / 8 GiB     = 1/512 = 0.195% ~ 0.2% of heap, always mapped

  heap:  | card0 | card1 | card2 | card3 | ...   (512 B each)
                 v store of a young ptr into an old object
  cards: [ 00 ][ 01 ][ 00 ][ 00 ] ...   <- 1 dirty byte = 1 card scanned
         a dirty card costs a 512 B scan at the next young GC
```

8 GB of heap bought 16 MB of proof bookkeeping — 0.2% — and one
stray pointer costs one 512-byte scan. That is also why the
granularity is 512 B and not 4 KB (an 8x worse false-positive
scan per stray pointer).

## Proving while running; moving the proof; counting instead of searching

**Concurrent collectors** run the proof beside the mutator, paying
barriers for the invariant. CMS (2002) marked and swept
concurrently with two short STW phases — and never compacted, so
old space fragmented until promotion could not fit: **concurrent
mode failure**, falling back to a full *serial* compacting STW
collection on an already-huge heap. Those multi-second pauses are
the 2010s LinkedIn/Twitter war stories, visible in `jstat
-gcutil` as the `O` column pinned near 100 with `FGC`/`FGCT`
spiking; CMS was deprecated in JDK 9, removed in 14. **G1** instead
sets a *proof budget*: the heap becomes ~2048 equal regions (1-32
MB; verified here: 1 MB regions on a 2 GB heap), and
`-XX:MaxGCPauseMillis` (default 200 ms) is the budget the
collection-set picker spends — region evacuation costs are
predicted from measured history, and only as many regions as fit
are collected (young, plus some old in **mixed** collections). The
pause becomes an incremental slice of the proof, sized to an SLO,
not the heap.

**Compaction is the address proof.** Object identity *is* an
address (module 01 ch 06), so sliding compaction (Lisp 2) means
**every pointer is a lemma that must be rewritten**:

```text
pass 1 (slide):   walk heap low->high, compute each object's new
                  address, stash it as a forwarding pointer
pass 2 (rewrite): visit every reference (roots + live objects'
                  fields), replace each with the forwarded address
pass 3 (move):    copy the bytes into contiguous homes
```

Three passes, no copy buffer, allocation order preserved.
**Pinning breaks it**: one immovable object mid-heap is a hole the
slide must keep; enough pins and compaction is impossible — .NET's
Large Object Heap is not compacted by default (module 01 ch 13),
and G1's dialect is the humongous object (insiders, below).

**Reference counting is proof by construction** rather than search.
Each object carries its own proof count: every reference copies one
unit of proof in (retain), every drop removes one (release); zero =
refuted, freed *immediately and deterministically*. The cost model
inverts tracing's — RC pays per *pointer mutation* (always, atomic,
cache-line-poisoned under contention); tracing pays per *live
object*, only at collection. And RC's blind spot is structural:
cycles are unrefutable, by hand on `A.b = B`, `B.c = C`, `C.a = A`,
after the last external reference to A drops:

```text
A's count = 1 (held by C)   B's count = 1 (held by A)
C's count = 1 (held by B)   -- a stable triangle of dead objects
no count reaches 0: the cycle IS its own proof of liveness
```

Swift's **ARC** inlines the proof into the binary: the compiler
places every retain/release as emitted instructions. The chapter's
mandated artifact — five lines (Swift 5.10; reproduce with
`swiftc -emit-sil`; trimmed to the ARC operations):

```swift
final class Blob { var n = 0 }          // 1: born with refcount 1

func work(_ b: Blob) -> Int {           // 2: param borrowed from caller
    let a = b                           // 3: strong copy -> +1
    let c = a                           // 4: strong copy -> +1
    c.n += 1                            // 5: ...
    return c.n                          //    scope exit: a, c die -> -2
}
```

```text
// SIL, swiftc -Onone, mangled name elided — the proof as instructions:
sil hidden @$s... : $@convention(thin) (@guaranteed Blob) -> Int {
bb0(%0 : $Blob):
  ...
  strong_retain %0 : $Blob     // 'let a = b'   count 1 -> 2
  strong_retain %0 : $Blob     // 'let c = a'   count 2 -> 3
  ...                            // load n, add, store
  strong_release %0 : $Blob    // 'c' dies      3 -> 2
  strong_release %0 : $Blob    // 'a' dies      2 -> 1
}
// -Onone arm64:  bl _swift_retain x2 / bl _swift_release x2
// -O: the ARC optimizer PROVES both pairs redundant and DELETES
//     them (a proof of a proof's redundancy); surviving ops inline
//     to one ldadda — an atomic add on the header's refcount word
```

That `-O` line is the chapter in miniature: proofs cost; proofs
*about* proofs delete cost. CPython runs the same contract at
runtime (`Py_INCREF`/`Py_DECREF` — the same inserts, executed not
compiled — module 01 ch 16); its no-GIL branch adds **biased RC**
(owner-thread increments non-atomic, others atomic) to stop refcount
ping-pong between cores, and **deferred RC** (ignore short-lived
local refs, scan stacks periodically) to drop the hottest
increments. Cycles need an *out-of-band registry*: Swift's `weak`
references resolve through **side tables** — a hash keyed by object
address, zeroed on dealloc, the liveness proof living outside the
object — and CPython's cycle detector is the same move: an external
scanner for the one proof shape counting cannot build.

## The design space: allocation is free because free space IS the proof

One beauty spot: in a copying nursery, **allocation is a bump** —
free space is one contiguous extent and allocating is `top += size`:
O(1), ~10 ns, no list, no per-object metadata. The unallocated tail
*is* the proof that the next allocation is safe — proof construction
costs nothing per object, which is why copying nurseries win twice:
collection tracks survivors, allocation is a bump (the JVM's TLAB
gives each thread its own bump extent, no lock). Contrast malloc's
**bin-fit** (module 01 ch 19): size classes, free lists, per-chunk
headers, fragmentation — 10-100 ns of *bookkeeping per allocation*,
because free space is many extents rather than one proof. Tracing
never asks "is this chunk free?" — it asks, wholesale, "which
objects are dead?" — making allocation trivial by making
deallocation batch.

Closing, the three-axis design space — **throughput** (proof
amortization), **latency** (proof scheduling: worst pause),
**footprint** (proof overhead: headers, bitmaps, cards, barriers,
remembered sets) — with every collector from module 01's survey
placed:

| collector | throughput | max pause | footprint overhead |
|---|---|---|---|
| Serial (STW) | good (no barriers) | heap/rate: 2 GB / 500 MB/s = 4 s | mark bitmap |
| Parallel | best | seconds at large heap | bitmaps + stacks |
| CMS (concurrent) | -5-10% | ~100 ms goal, until mode failure | + free-list fragmentation |
| G1 | -5-15% | budgeted: 200 ms default | regions, cards, remembered sets |
| ZGC | ~-10% | ~10 us (verified below) | colored ptrs + forwarding |
| Epsilon | maximum | none — no proof | none |

ZGC's instrument earns its line: **colored pointers** park four
color bits (Marked0, Marked1, Remapped, Finalizable) in the
pointer's upper bits, clear of the 44 bits a 16 TB heap needs —
module 01 ch 06's tagged pointers returning as a **load-barrier
certificate**: every reference load checks the tag; if stale, the
barrier heals it (forward to the new address, mark). Validity
becomes a per-load check instead of a global pause — sub-10-µs
pauses, bought with ~10% throughput and a barrier on every load
the JIT cannot elide. Epsilon, the last row, is the "no proof"
collector: allocation and nothing else, which is what makes it a
profiler (insiders, below).

## What insiders know

- **`System.gc()` is a hint that lies.** On HotSpot it defaults to a
  *full* GC; the caller nobody reads is RMI's distributed GC, which
  invokes it hourly by default
  (`sun.rmi.dgc.server.gcInterval` = 3,600,000 ms). Ops seeing
  hourly multi-second pauses add `-XX:+DisableExplicitGC` — and
  months later hit the trap: RMI DGC depends on those explicit GCs
  to reap unreferenced remote objects, so suppression leaks the
  remote-object tables until OOM. Surgical fixes: raise
  `gcInterval`, or `-XX:+ExplicitGCInvokesConcurrent` — the flag
  that "fixed" the incident caused the next one.
- **Safepoints are proof points.** Roots are only readable where the
  JIT guarantees a walkable frame — a **safepoint**, catalogued by
  **oop maps** (which slots hold pointers: the proof's certificates
  for one PC). Insiders split the pause into *time-to-safepoint*
  and *at-safepoint* (`-Xlog:safepoint`, module 10 ch 04): a 4 ms
  GC with 40 ms TTSP is a safepoint bug, not a GC bug. The classic:
  an `int` **counting loop** carries no interior poll, so one long
  iteration stalls every thread — fixed by JDK 10's loop strip
  mining; before that, insiders forced polls with a `long`
  induction variable. Adjacent trap: *safepoint bias* — profilers
  sampling at safepoints see where threads stop, not where they
  run.
- **G1's humongous threshold is ½ region.** Objects at or above half
  a region go to contiguous humongous regions, collected lazily,
  fragmenting eagerly. On this chapter's 2 GB lab heap (1 MB
  regions): 512 KB; on 8 GB (4 MB regions): 2 MB — `byte[4_000_000]`
  is old-generation pressure in disguise; .NET's LOH (85,000+
  bytes) speaking Java: big objects don't move, don't fit, don't
  play.
- **Epsilon is a profiler, not a joke.**
  `-XX:+UnlockExperimentalVMOptions -XX:+UseEpsilonGC` runs no
  proof: bump until the heap fills, then OOM by design. A workload
  run to its Epsilon death measures raw allocation rate; the
  throughput delta under a real collector is that collector's proof
  cost in seconds — "no proof" makes every row of the design table
  a measurement instead of a claim.
- **The JIT deletes proofs it can prove redundant.** Card-mark and
  SATB barriers compile to test-and-mark, but the compiler elides
  the mark when the stored value provably cannot be young (null,
  constants, provably-old values), and G1 batches barriers into
  per-thread buffers flushed at safepoints. The ~2-20 ns per-store
  figure is the instrumented worst case, not the steady-state hit.

## See it yourself

All labs ran on this chapter's platform (OpenJDK 21.0.12, 2-core
sandbox; CPython 3.12.15). Your numbers will differ; the *shapes*
are the deliverable.

**Lab 1 — read a real young collection.** 12 rounds of 4M `byte[32]`
allocations, 64 live arrays, `-Xms2g -Xmx2g -Xmn256m`:

```console
$ java -Xms2g -Xmx2g -Xmn256m -Xlog:gc,gc+heap Churn.java
[0.621s][info][gc,heap] GC(0) Survivor regions: 0->3(32)
[0.621s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) 257M->3M(2048M) 7.531ms
```

Annotate: **257M->3M** — 253 MB died, ~3 MB survived (the 64 newest
arrays plus JVM young flotsam), and the pause tracks the 3, not the
253: cost ∝ survivors, measured. **3 of 32 survivor regions** — a
capped slice; `-Xlog:gc+age` shows the rule: `Desired survivor size
16777216 bytes, new threshold 15`. Rerun with
`-Xlog:gc+phases=debug` for the card scan:

```console
[0.542s][info ][gc,phases] GC(0) Evacuate Collection Set: 4.31ms
[0.542s][debug][gc,phases] GC(0) Scan Heap Roots (ms): Sum: 0.15
[0.542s][debug][gc,phases] GC(0) Scanned Cards: Sum: 425
[0.542s][debug][gc,phases] GC(0) Object Copy (ms): Sum: 5.12
```

425 dirty cards × 512 B = ~218 KB of old space re-scanned in 0.15 ms
— the card table working exactly as drawn.

**Lab 2 — the collector pause table (the deliverable).** Same churn,
three collectors; sorted pauses from `-Xlog:gc:file=...` (Serial,
G1) and `-Xlog:gc+phases:file=...` (ZGC):

| collector | pauses | min | median | max |
|---|---|---|---|---|
| Serial | 4 | 2.75 ms | 2.76 ms | 7.47 ms |
| G1 | 4 | 4.91 ms | 6.57 ms | 7.53 ms |
| ZGC | 9 (3/cycle) | 0.005 ms | 0.006 ms | 0.010 ms |

Serial's median only "wins" because the live set is microscopic —
scale it and Serial grows linearly while ZGC stays flat: the ZGC
lines are literally `Pause Mark Start 0.008ms` — a max pause of
**10 microseconds**. Notice what is absent from ZGC's pauses: heap
size.

**Lab 3 — Python's thresholds, and the leak the cycle detector
cannot see.** The defaults, then module 01 ch 14's closure leak in
four lines:

```console
$ python3 - <<'EOF'
import gc, tracemalloc
print(gc.get_threshold(), gc.get_count())  # (700, 10, 10) (688, 10, 0)
def make(size):                  # 1
    buf = bytearray(size)        # 2  1 MiB, "dies with the closure"
    def hold(): return len(buf)  # 3  ...in theory
    return hold                  # 4
leaks = [make(1 << 20) for _ in range(200)]    # static root: the list
tracemalloc.start(); s1 = tracemalloc.take_snapshot()
leaks += [make(1 << 20) for _ in range(200)]
print(tracemalloc.take_snapshot().compare_to(s1, "lineno")[:1])
print("cycles collected:", gc.collect())      # 0
EOF
<stdin>:2: size=200 MiB (+200 MiB), count=400 (+400)
cycles collected: 0
```

Read it: 700 = gen0 fires after 700 net container allocations; each
10 = one generation up per 10 collections below (gen2 ≈ every 100
gen0s). `gc.collect() == 0` is the point: there is **no cycle** —
200 MiB of reachable-but-dead retained garbage; tracemalloc names
the allocation site, `gc` never will. With `gc.set_threshold(5, 5,
5)` gen0 fired 85 times during 200 appends (measured) and freed
nothing — the counter is about *allocation*, not cycles; tightening
it cannot free a byte. Fix = sever the edge: cap the list,
`weakref`, or capture `len(buf)` instead of the buffer.

## How to use this knowledge

- **In an incident, read the GC log as a proof transcript.**
  `-Xlog:gc*` and `jstat -gcutil` tell you which proof ran (Pause
  Young / Mixed / Full), its subject (M->M), and its cost. 257M->3M
  in 4 ms is healthy; 3M->3M in 400 ms means liveness, not garbage,
  is slow — shrink the live set; no GC flag fixes that.
- **Leak triage is root triage.** When RSS climbs and `gc.collect()`
  frees nothing, don't hunt cycles first — hunt the three shapes
  (static registry, listener list, closure capture). tracemalloc or
  a heap histogram names the *class*; this chapter tells you to find
  the *root* proving it.
- **Pick collectors by SLO, not folklore.** 200 ms budget → G1;
  single-digit ms → ZGC/Shenandoah; 10 GB batch analytics with no
  users attached → Parallel (barriers are pure waste there). Each is
  a row of the 3-axis table, and the rows are measurable with the
  Epsilon trick.
- **Design allocation with the laws in mind.** Cost ∝ survivors:
  keep transients genuinely transient — die in the nursery, don't
  store them anywhere old. Avoid the humongous/LOH cliff (½ G1
  region; 85 KB in .NET): chunk big arrays or budget for the
  fragmentation.
- **In interviews, derive, don't recite.** "A pause is liveness-proof
  latency: heap over mark rate. Concurrency needs the tri-color
  invariant and a barrier. Generations exploit the empirical death
  curve; cost tracks survivors" — checkable arithmetic, and almost
  nobody can produce it.

## Connections

- **Module 19 ch 01** — liveness's uncomputability is the halting
  problem's heap dialect; where chapter 01's machine reaches memory.
- **Module 19 ch 02** — "black never points to white" is a
  *preservation* property proved one mutator step at a time.
- **Module 19 ch 03** — memory safety as a theorem used ownership
  proofs at compile time; this chapter is the runtime side.
- **Module 19 ch 05** — exact liveness is undecidable: every leak is
  an impossibility result made livable.
- **Module 01 ch 02** — the ~100 ns miss ladder re-derived as the
  mark-rate floor; pointer-chasing *is* the GC cost model.
- **Module 01 ch 12 / 14 / 15 / 16** — the mandated collector
  re-derivations: JVM generations/CMS/G1 (12), CPython refcount +
  cycle detector (14), V8 scavenger nursery (15), Swift ARC (16) —
  each reread as a proof strategy: by-search, by-construction plus
  external cycle scan, by-search-on-a-nursery, by-construction.
- **Module 01 ch 23** — the grand table's "who proves what" column
  rereads as proof strategies: manual = hand-written lemmas, RC =
  proof by construction, tracing = periodic proof search, ownership
  = compile-time proof. (Orphan repaired: the table reported the
  models' *costs*; the proof reading is its missing theory column —
  *why* those costs have the shapes they do.)
- **Module 10 ch 04** — safepoints and oop maps are this chapter's
  proof points and certificates; TTSP is the pause before the pause.
- **Module 09 ch 01/02** — ch 01's happens-before race detector
  (TSan) is runtime proof in this chapter's exact sense: an
  over-approximating checker whose imprecision is the price of
  decidability; ch 02's lock discipline is the barrier obligation
  at thread granularity.
- **Module 16 ch 04** — the forgotten-listener leak shape, from
  browser page-memory teardown.
- **Module 01 ch 06 / 13 / 19 / 20** — tagged pointers as ZGC's
  color bits (06), LOH pinning (13), malloc bin-fit as the
  allocation contrast (19), biased refcounting as the no-GIL
  repair (20).

## Quiz

1. **(Concept)** A reviewer says: "Your GC freed an object the
   program was about to use — the collector is broken." Under what
   condition is that impossible for a correct collector? What is the
   *opposite* case called — the collector refuses to free something
   the program will never use again — and why can no collector
   eliminate it?

2. **(Concept)** Name the invariant incremental marking must hold,
   why the mutator must help enforce it, and why Yuasa's barrier —
   which can retain floating garbage the others would free — is the
   one modern collectors deploy anyway.

3. **(Derive — GC cost computation)** A service has a 1.2 GiB live
   heap; one marking thread sustains 400 MB/s. (a) The serial
   collector's full-collection pause? (b) With 8 parallel marking
   threads, ideally divided? (c) The SLO is 100 ms — is (b)
   acceptable, and which two *structural* moves does the field use
   instead of more threads? (d) The nursery is 256 MB with 4 MB of
   survivors; evacuation runs at 1 GB/s — roughly the young pause,
   and why does the answer not contain the 256 MB?

4. **(Derive)** A 16 GiB heap uses 512-byte cards, one byte each.
   (a) Card-table size, and what fraction of the heap? (b) One
   old-to-young store dirties one card — how many bytes of old space
   re-scan at the next young collection, worst case? (c) Why would
   4 KB cards make (b) worse, and by what factor?

5. **(Derive)** Nodes A, B, C with `A.b = B`, `B.c = C`, `C.a = A`,
   and one external root holding A. Walk the reference counts:
   initial, after the external root drops, and what pure RC
   concludes. What mechanism refutes the cycle, and which two
   production runtimes pair it with RC?

6. **(Diagnose)** A Java service shows 8-second full-GC pauses
   exactly on the hour; `jstat -gcutil` shows `FGC` incrementing
   hourly; the code never calls `System.gc()`; the service exposes
   RMI endpoints. Ops proposes `-XX:+DisableExplicitGC`. What is
   happening, what will the flag break within weeks, and what is the
   correct fix?

7. **(Diagnose)** A Python worker's RSS grows 200 MB/day. Forced
   `gc.collect()` returns 0; a tracemalloc diff shows one line
   growing: a function returning a closure over a parsed config
   dict, stored in a module-level callback list. Explain the leak in
   the live/reachable vocabulary, why neither the cycle detector nor
   tighter thresholds can help, and the one-line fix.

8. **(Cross-chapter)** Users report 40 ms hitches; the GC log shows
   `Pause Young ... 4.1ms`. Module 10 ch 04's tooling shows
   `Reaching safepoint: 36ms` for the same events. Where did the
   other 36 ms go, what property of a *counting loop* caused it, and
   which JDK change fixed that stall class (plus the pre-fix
   workaround)?

<details>
<summary>Answers</summary>

1. A correct collector frees only **unreachable** objects — no
   reference path from any root — and by definition the program
   cannot reach an unreachable object to use it (if it does, that is
   use-after-free: a broken collector or a safepoint/barrier bug).
   The opposite — reachable but dead — is **retained garbage**, the
   formal leak. No collector eliminates it: "will ever be read" is
   liveness, which is the halting problem; reachability, an
   over-approximation, is the only computable proof. The repair
   severs an edge from a root, never tunes the collector.

2. The invariant: **a black object never points to a white object**
   (monotonicity of proof construction). The mutator must help
   because it interleaves with marking: a store into a black object
   creates a black→white edge the collector will never revisit —
   the 4-step lost-object interleaving — so sweep frees a live
   object; the barrier restores monotonicity. Yuasa shades the *old
   value* on overwrite, snapshotting the graph at mark start: no
   color tests (cheapest per store), no remark rescan (Dijkstra
   needs one), and it composes with generational remembered sets.
   Its cost — dead-during-mark objects float one extra cycle — is
   bounded, preferred to correctness machinery.

3. (a) 1.2 GiB / 400 MB/s ≈ **3.1 s**. (b) 3.1 s / 8 ≈ **390 ms**
   (ideal; realistically worse — root contention, shared mark
   stacks). (c) No — ~4x the SLO, and the rate is miss-ladder-
   capped, so more threads stop paying. The moves: **generational**
   (prove only the nursery — subject shrinks, cost tracks
   survivors) and **concurrent/incremental** (run the proof with
   the mutator, paying write barriers — CMS, G1, ZGC). (d) ≈
   4 MB / 1 GB/s = **4 ms** plus root/card scan: the young
   collection *copies survivors*; the other 252 MB are reclaimed as
   the side effect of emptying the nursery — cost ∝ survivor bytes,
   not allocation volume.

4. (a) 16 GiB / 512 B = 2^25 cards → one byte each = **32 MiB**;
   32 MiB / 16 GiB = 1/512 ≈ **0.2%**. (b) One card = one
   **512-byte** span re-scanned for pointers into the nursery.
   (c) 4 KB cards: a single stray old-to-young pointer flags
   4096 B — an **8x** worse false-positive scan — which is why
   HotSpot chose 512 B: the byte-per-card tax is fixed and tiny,
   but the false-positive cost scales with card size.

5. Initial: A = 2 (C's reference + external root), B = 1, C = 1.
   After the external drop: A = 1, B = 1, C = 1 — a stable triangle
   where each object's only reference comes from another member. No
   count reaches 0, so RC concludes all three are live: the cycle
   is its own proof of liveness, unrefutable by construction. The
   refuting mechanism is an external tracing scanner over the
   suspect subgraph (trial deletion). Production pairings: CPython
   (RC + generational cycle detector in `gc`) and Swift/ObjC (RC +
   programmer-declared `weak`, side tables zeroed at dealloc).

6. RMI's distributed GC calls `System.gc()` on a timer — default
   `sun.rmi.dgc.server.gcInterval` = 3,600,000 ms, hourly — and a
   default HotSpot `System.gc()` is a full collection: the pauses
   are RMI's timer, not your code. `-XX:+DisableExplicitGC` stops
   them, then breaks RMI DGC: remote-object reaping depends on
   those explicit collections, so unreferenced remote objects and
   their transport bookkeeping accumulate — OOM weeks later.
   Correct fixes: raise `gcInterval`, or
   `-XX:+ExplicitGCInvokesConcurrent` so the hint runs concurrently.

7. The closures and their captured dicts are **reachable**
   (module-level list = static root → closure → cell → dict) but
   **dead** (no callback will ever fire again): retained garbage,
   the leak definition — not a cycle. The refcounts are all ≥ 1 for
   reasons that are *correct* given the root, so there is nothing
   to collect, and tighter thresholds change only *when* collection
   runs, never what is provable. One-line fix: sever the edge —
   `weakref.ref` (or capture only needed fields, or expire entries)
   so the list no longer proves liveness for the config.

8. Pause = GC work + time-to-safepoint: 36 of the 40 ms was
   bringing threads to the safepoint where the young GC could read
   their roots — a TTSP stall, not collector work. The cause: an
   `int`-induction **counted loop** contains no safepoint poll, so
   a thread in one long iteration cannot stop while every other
   thread waits at the quorum. JDK 10's loop strip mining added
   polls to counted loops; before that, insiders forced the loop
   to be non-counted — a `long` induction variable — so the JIT
   emitted polls. (Module 10 ch 04: read `Reaching safepoint`
   separately from `At safepoint`; here the second is innocent.)

</details>
