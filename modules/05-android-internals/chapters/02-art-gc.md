# Chapter 02 — ART: objects & GC

> **The 30-second version.** ART is a register-based bytecode VM with a tracing GC — a JVM cousin (module 01 ch12) rebuilt for phones: no swap, a fork-shared boot image, a per-device heap cap. Code climbs a ladder — interpreted, JIT'd when hot (a RAM cache that dies with the process), then AOT'd from a profile of what ran; since Baseline Profiles, that profile ships in the APK, so even first run gets compiled code. Objects carry 8-byte headers (4-byte compressed class word + 4-byte lock word doubling as the GC's mark/forwarding cell) and 4-byte references in a 4 GB heap reservation. The heap splits into a never-moving zygote-mapped ImageSpace, 256 KB bump-pointer regions, and a large-object space for primitive arrays over 12 KB. The Concurrent Copying collector moves live objects while your code runs — its read barrier hides a forwarding pointer in the header — so pauses are µs–ms, and the heap is capped by `dalvik.vm.heapgrowthlimit`, your device's memory class, assigned at birth.

## The ladder: code that earns its compilation

Start from economics. AOT machine code costs flash, install time, and battery — and most methods never run. Android 5.0 (2014) shipped ART with full AOT at install: every method compiled forever, installs that took minutes, hundreds of MB of `.odex`, 95%+ of it dead weight. Android 7.0 (2016) inverted the policy: **compile nothing eagerly; let the running program say what deserves compilation.** Four rungs, each existing because the one below was measured and found wanting:

1. **Interpreter (Mterp).** dex is register-based — fewer dispatches per operation than stack bytecode, which made Dalvik-era interpretation tolerable. Mterp (Android 6.0+) is an assembly interpreter, one hand-written handler per opcode — fine for code that runs twice.
2. **JIT (Android 7.0+).** Each method carries a hotness counter, bumped on entry and on backward branches (loop back-edges — the cheap proxy for loops). Past a tiering threshold (build-time constants), the JIT compiles it into a per-process code cache — on the order of 15 MB (ART, Android 7–10 era). That code lives in RAM, **dies with the process**, and a profile accumulates alongside: hot methods, branch leans, monomorphic call sites.
3. **Profile-guided AOT (`speed-profile`).** The profile persists to disk (`/data/misc/profiles/cur/0/<package>/primary.prof.txt`, the human-readable format since Android 9). Idle and charging, `dex2oat` re-runs with the `speed-profile` filter and AOT-compiles *only the profiled methods* — a few percent of the code covering almost all executed bytecode — into `.odex` under `/data/app/*/oat/<arch>/`.
4. **Baseline Profiles (Android 9+).** The profile can even be *shipped*: `assets/dexopt/baseline.prof` in the APK, plus **Cloud Profiles** aggregated by Play. Install-time `dex2oat` compiles it immediately, so a fresh install's *first* run — the one the mortal JIT cache could never help — already has AOT code for its startup path.

The derivation: **each rung trades memory for speed, and the profile is the gatekeeper that keeps the trade profitable.** Compilation without a profile was the Android 5.0 mistake; a profile without persistence was the JIT's cold-start mistake; shipping the profile fixes both.

## What an object is: eight bytes, two words

Derive the header from what the runtime must do with any heap word:

- **Find its class.** The GC needs the object's size (to walk the heap) and reference fields (to trace) — a pointer to metadata, the *class word*.
- **Lock it, hash it, move it.** Any object can be a `synchronized` monitor, hold an `identityHashCode`, and be relocated by the GC — ART crams all three into one 4-byte *lock word*, low bits selecting the meaning.

64-bit ART, compressed references on (the default, Android 8–14):

```text
   offset  size   field
   ------  ----   ---------------------------------------------------------
   0       4      class word : 32-bit compressed pointer into the boot
                     image / class table (metadata lives off-heap)
   4       4      lock word  : union — exactly one of, picked by state bits:
                       • unlocked / thin lock  [ thread id | recursion count ]
                       • fat lock              [ index → full OS monitor ]
                       • 31-bit identity hash  (installed on first
                                                 identityHashCode() call)
                       • GC FORWARDING address (CC evacuation only — below)
   8       …      instance fields; each reference field is 4 bytes
```

That is the "8–16 byte header story": 8 bytes with compression on; arrays add a 4-byte length word at offset 8; sizes round to 8-byte alignment. HotSpot (module 01 ch12) pays 8-byte mark + 4-byte compressed klass = 12; ART is a third cheaper because the lock word does quadruple duty.

**Compressed references** are HotSpot's oop trick with a mobile twist: reference fields are 4 bytes, not 8. HotSpot (heap < 32 GB) stores `address >> 3`, dropping provably-zero low bits for a 32 GB reach — hence the 32 GB cliff. ART instead reserves **one 4 GB-aligned window of address space** and stores each address's low 32 bits; decode is `window_base | stored32`, and the heap must fit the window. No cliff — no need: `heapgrowthlimit` tops out in the hundreds of MB.

## The heap: an image, a birthright, and a place for big arrays

Spaces split by *who shares the pages* and *what may move* — both inherited from chapter 01's fork:

```text
  One ART heap, one app process (Android 8–14, 64-bit, 4 KB pages)

  ┌──────────────────────────────────────────────────────────────────┐
  │ ImageSpace — mapped read-only from boot.art (in the ART APEX /   │  every process
  │   /system): preloaded framework classes, interned strings.       │  maps the SAME
  │   NEVER collected · NEVER moved · COW-shared (module 01 ch03)    │  physical pages
  ├──────────────────────────────────────────────────────────────────┤
  │ Zygote space — what the zygote's pre-fork GC compacted and kept  │  COW-shared
  │   live for all children (private pages appear only on write)     │  per fork
  ├──────────────────────────────────────────────────────────────────┤
  │ Allocation space = RegionSpace: 256 KB regions, bump-pointer     │  THIS process
  │   allocation; CC evacuates live objects between regions          │  only
  │   (+ NonMovingSpace: the few things the runtime pins)            │
  ├──────────────────────────────────────────────────────────────────┤
  │ Large-object space (LOS) — discontiguous, page-mapped;           │  THIS process
  │   primitive arrays ≥ kLargeObjectThreshold (3 pages = 12 KB)     │  only
  └──────────────────────────────────────────────────────────────────┘
```

**ImageSpace** is chapter 01's payoff made physical: the zygote maps the boot image before any app exists; every `fork()` inherits it. A ~100 MB image shared by 40 processes costs ~100 MB once, not 4 GB — until somebody writes a page. ImageSpace objects are therefore never moved and never collected: a moved object is a dirtied page, and a dirtied page is sharing destroyed. Before forking, the zygote runs a **zygote GC** — compact everything live into the shared region, drop the garbage — so children inherit maximal sharing.

**The allocation space** changed shape with CC. Through Android 7 it was a **RosAlloc** heap — "Run of Slot Size": one size class per run, each thread claiming its own run, so the common case is a lock-free bump inside a run (module 01 ch19's taxonomy — the tcmalloc idea, sized for pockets). Android 8 replaced it with **RegionSpace: 256 KB regions, bump-pointer allocation** — deliberately the dumbest allocator possible, because reclamation is about to become a *moving* operation. Bump allocation and copying GC are one system: allocation near-free, fragmentation fixed by evacuation, the bill in the collector.

**LOS** exists because 12 KB arrays inside 256 KB regions fragment evacuation, and a 5 MB bitmap would get 5 MB *copied* on every compaction. LOS objects sit in their own page mappings, never move, and are reclaimed by unmapping — memory returns to the OS at page granularity. The threshold: `kLargeObjectThreshold = 3 * kPageSize` = **12 KB** (ART, Android 5.0+; 48 KB under Android 15's 16 KB pages). Only **reference-free primitive arrays** qualify: an `Object[20000]` of nulls, 80 KB, stays in RegionSpace at any size — LOS contents are never scanned or barriered, so they must contain no references.

**The birthright.** Each device ships build-time properties that fix the Java heap budget: `dalvik.vm.heapstartsize` (initial capacity, commonly 8 MB), `dalvik.vm.heapgrowthlimit` (the per-app limit: 16 MB on 2010 hardware, 128–256 MB on 2020s flagships, Android 8–14), and `dalvik.vm.heapsize` (what `android:largeHeap="true"` buys, typically ~2×). `ActivityManager.getMemoryClass()` returns your MB; `Runtime.maxMemory()` the same number in bytes. The cap is not about your process's health — it is lmkd's fairness contract (module 05 ch04): the killer can keep N cached processes only if each one's *potential* Java heap is bounded.

## The CC collector: moving furniture while the party runs

A stop-the-world copying collector (module 01 ch12's scavenger) is fast per byte, but its pause scales with live data; 100 MB live stops the world for tens of ms. At 60/120 Hz a frame is 16.7/8.3 ms — Dalvik's 10–100 ms pauses were visible as frozen UI. The **Concurrent Copying (CC) collector** (Android 8.0, 2017; still the backbone through Android 14) buys µs–ms pauses.

Derive the hard part: if the collector copies an object while a mutator holds a pointer to the old copy, the mutator reads a corpse. Write barriers can't help — they catch writes only. Concurrent *moving* demands a barrier on every **read**. ART installs it at object-header level, with no extra word, by overloading the lock word:

- Every reference load (barrier *generated into* JIT and AOT code) fast-checks the loaded reference's header. Already marked — the common case — is one compare-and-branch. That is the read barrier's real cost: not a pause, a toll per field read.
- Unmarked → slow path: atomically flip to gray, queue for concurrent scanning, and the mutator proceeds; marker threads drain the stack alongside the app.
- Regions chosen for evacuation get their live objects copied into fresh regions and the *from-copy's* header CAS'd into the **forwarding state** — the 4-byte lock word now holds the new copy's compressed address. Any thread that later reads a stale pointer checks the header, sees *forwarding*, and follows to the moved object: the stale pointer heals itself on first use.

That trick is the Brooks-style forwarding idea (Shenandoah, module 01 ch12) done on the cheap: Shenandoah pays a permanent 8-byte forwarding word on every object forever; ART pays only during evacuation, borrowing the lock word's rarely-used bits — across billions of objects, the difference between 16-byte and 8-byte headers.

CC stops the world only twice: the **thread flip** (re-scan each thread's stacks and registers as roots, then resume while marking continues) and final weak-reference processing. Both are µs-to-ms: logcat shows `pausing 0.5ms` against `total 132ms`. Since Android 10, a **young generation** layers on top — fresh regions for new allocations, "young concurrent copying" collections evacuating only recent regions, catching the die-young distribution cheaply.

**Compaction vs pinning.** CC's default is to move — that is how RegionSpace stays unfragmented. The exceptions are principled: LOS (no references inside; freed by unmap), ImageSpace (moving = COW suicide), a small NonMovingSpace for runtime internals, and objects held from C++. Since Android 8.0, JNI references are **indirect** — a `jobject` is a handle into a table, not a raw pointer — precisely so the GC can move the object and update one entry; that is why ART can compact an app deep in native code.

## What insiders know

- **The LOS threshold, exactly:** `kLargeObjectThreshold = 3 * kPageSize` = 12 KB (ART, Android 5.0+, 4 KB pages; 48 KB on 16 KB-page Android 15 devices). Primitive arrays only — `byte[20000]` goes to LOS; `Object[20000]` never does, at any size, because LOS contents are unscanned and unbarriered.
- **8-byte headers, no 32 GB cliff:** class word 4 + lock word 4 (HotSpot: 8-byte mark + 4-byte compressed klass = 12). ART's compressed refs are the low 32 bits of addresses in one 4 GB-aligned reservation — decode `base | stored32`, no shift, no cliff: mobile heaps never approach the window.
- **GC pause ranges to quote:** CC pauses typically 0.1–5 ms (Android 8–14; young collections usually sub-millisecond); in the logcat line, `pausing X` is the stop-the-world total, `total Y` the wall clock including concurrent work. Dalvik-era full GCs: 10–100+ ms. That comparison is why Java-on-mobile is viable at 120 Hz.
- **The memory-class getprops:** `adb shell getprop dalvik.vm.heapgrowthlimit` / `heapsize` / `heapstartsize` / `heaptargetutilization` — e.g. 256 / 512 / 8 / 0.75 on a Pixel-class 8 GB device. `ActivityManager.getMemoryClass()` is the per-app runtime view; `largeHeap` switches you to `heapsize`. Per-device build values — read them off your floor models, not your flagship.
- **The read barrier hides in the lock word:** marking and forwarding reuse the same 4 bytes that hold thin locks and identity hashes, so ART pays Shenandoah's forwarding tax only during evacuation. And JNI references became indirect handles in Android 8.0 — the enabling fact for "the GC may move anything."

## See it yourself

Laptop path: an Android Studio emulator or any device over `adb`. No device was available in this writing session, so this is a documented walkthrough — Pixel-class output shapes on Android 14 (field order stable since Android 8; your numbers will differ, and that difference is the point).

```text
$ adb shell getprop | grep dalvik.vm
[dalvik.vm.heapgrowthlimit]: [256]      ← your app's Java-heap ceiling (MB)
[dalvik.vm.heapsize]: [512]             ← the largeHeap ceiling
[dalvik.vm.heapstartsize]: [8]          ← initial heap capacity (MB)
[dalvik.vm.heaptargetutilization]: [0.75]  ← pacing: GC aims to keep heap ~75% used
[dalvik.vm.usejit]: [true]              ← the ladder's rung 2 is on
```

The heap ledger for one app (Chrome; substitute any package):

```text
$ adb shell dumpsys meminfo com.android.chrome

** MEMINFO in pid 25184 [com.android.chrome] **
                   Pss  Private  Private  SwapPss     Rss     Heap    Heap    Heap
                 Total    Dirty    Clean    Dirty    Total     Size   Alloc   Free
                ------   ------   ------   ------   ------   ------  ------  ------
  Native Heap    95104    94312        0      512   122880   102400   98304    4096
  Dalvik Heap    61440    60416        0        0    96256    98304   91136    7168
  Dalvik Other    8192     8192        0        0    12288
  ...
  TOTAL PSS:   172032   ...
```

Notice: **Dalvik Heap Size 98304 KB < 262144 KB** — ~38% of the 256 MB growth limit, nowhere near Java OOM. **Native ≈ 1.5× Dalvik** — the uncapped heap is usually the bigger one. And Pss is fair-share accounting: ImageSpace pages are divided across every zygote child, so 40 apps never count 4 GB of framework objects (the "Dalvik Heap" label is a fossil — Dalvik retired in 5.0).

Then make the collector talk — open the app, scroll something allocation-heavy, and read the GC lines (tagged `art` on Android 8/9, the process name on 10+ — grep the message, not the tag):

```text
$ adb shell logcat -d | grep -E "GC freed"

... I art: Background young concurrent copying GC freed 18040 (1804KB)
      AllocSpace objects, 0 (0B) LOS objects, 49% free, 5MB/10MB,
      pausing 26us total 62.636ms
... I art: Background concurrent copying GC freed 26502 (1794KB)
      AllocSpace objects, 15 (4MB) LOS objects, 47% free, 96MB/192MB,
      pausing 6.942ms total 165.123ms
```

Read one line right to left: `pausing 6.942ms` — stop-the-world cost; `total 165ms` — concurrent wall clock, real CPU on your cores; `96MB/192MB` — occupancy against the growth limit (50%: comfortable); `47% free` — headroom; `15 (4MB) LOS objects` — large primitive arrays churning (decoded images? buffers?); `AllocSpace objects` — small-object churn. No device? The walkthrough is the exercise: sketch this line at `190MB/192MB` and predict the next allocation.

## How to use this knowledge

- **OOM triage in one minute:** `OutOfMemoryError` at N MB with Native > Dalvik in meminfo means the Java cap fired while the real footprint is native. The fix is not `largeHeap` — that moves a different ceiling — but the native ledger (bitmaps, buffers, caches); `largeHeap` is for legitimately bigger object graphs, and lmkd bills whole-process RSS either way.
- **Cold-start work:** before profiling code, check which rung the startup path is on — `adb shell cmd package compile --check-prof -f speed-profile <pkg>` reports whether a usable profile exists. With no baseline profile, first run is interpreter-only *by policy*; micro-optimization cannot fix a rung problem.
- **Frame-budget decisions:** `pausing 0.3ms` is not the cost — the concurrent CPU is, and it scales with allocation rate. "Reduce allocations per frame" is derived physics: a scroll path allocating hundreds of KB per frame schedules collections every few hundred ms on cores your render thread wants.
- **The device matrix as a memory-class map:** the same APK has a 192 MB ceiling on the mid-range phone and 256 MB on the flagship, and lmkd's cached-process budget differs further (module 05 ch04). Reproduce OOM and jank on the low-RAM unit or you have not reproduced them.
- **Reading a leak:** growing LOS with flat AllocSpace in the profiler means big primitive arrays (`byte[]` decode buffers are the usual suspects); growing AllocSpace with flat LOS means an object-graph leak. The space is a free first-pass classifier.

## Connections

- **Module 01 ch12 (Java/JVM)** — HotSpot's 12-byte header, compressed oops, the 32 GB cliff, Shenandoah's Brooks pointer: every ART decision here is those tradeoffs re-priced for a fork-shared, capped heap.
- **Module 01 ch14 (GC alternatives)** — refcounting vs tracing: ART commits fully to tracing plus concurrency; compare against Python's per-object cost model.
- **Module 01 ch19 (allocators)** — RosAlloc's size-class runs and RegionSpace's bump pointer are that taxonomy in production; LOS's page-unmap freeing is the "large objects want page granularity" rule.
- **Module 05 ch01 (boot & the Zygote)** — ImageSpace and the zygote pre-fork GC are this chapter's sharing mechanism; the fork model is why the image never moves.
- **Module 05 ch04 (lifecycle & lmkd)** — `heapgrowthlimit` is the GC's ceiling *and* the killer's input; the uncapped native heap is why RSS, not Java-heap numbers, drives kills.
- **Module 10 ch04 (write barriers & safepoints)** — ART's read barrier is the case study in barrier insertion: generated into every reference load, the forwarding slow path as the escape hatch.

## Quiz

1. **(concept)** An app is launched for the *first* time — no baseline or cloud profile. Five minutes in it is smooth; the first ten seconds were janky. Explain rung by rung what executed the bytecode in those ten seconds, what executes it five minutes in, and what changes on the second cold start after the app is killed.
2. **(predict)** Android 14, 4 KB pages, compressed references on: `new byte[20000]` is allocated. Which space does it land in, roughly how many bytes including header, and will CC ever copy it? Justify from the space's rules.
3. **(predict)** Same device: `new Object[20000]` (all nulls, 80 KB). Which space, and why does it differ from question 2?
4. **(predict, log line)** `Background concurrent copying GC freed 26502 (1794KB) AllocSpace objects, 15 (4MB) LOS objects, 47% free, 190MB/192MB, pausing 0.9ms total 148ms`. What is the `heapgrowthlimit`, how close is the process to Java OOM, and what does `15 (4MB) LOS objects` say? What does the next allocation-heavy screen do?
5. **(bug)** A photo-grid app throws `OutOfMemoryError` at "192MB" while scrolling. `dumpsys meminfo` shows Dalvik Heap Size 196608 KB, Alloc 194560, but Native Heap 480 MB and growing. A teammate proposes `android:largeHeap="true"`. Diagnose the real problem and what the flag will and will not change.
6. **(bug)** A chat app drops frames while scrolling; GC logs show collections every ~200 ms, each `pausing 0.2ms`. The developer says "pauses are sub-millisecond, GC can't be the jank." What is wrong with that reasoning? Name the actual cost and two levers that reduce it.
7. **(concept)** Why can ImageSpace objects never be moved by any collector? Derive from what the kernel does when a process writes a shared page after `fork()` (module 01 ch03 / module 05 ch01), and connect to why the zygote runs a special GC *before* forking.
8. **(cross-chapter, module 01 ch12)** Shenandoah keeps a dedicated 8-byte Brooks forwarding pointer on every object; ART's CC keeps forwarding addresses in the 4-byte lock word only during evacuation. Compare the steady-state memory costs on a heap of 500 million small objects, and why the choice is right for a server JVM and wrong for a phone.

<details>
<summary>Answers</summary>

1. **First ten seconds:** every method runs in Mterp; hot ones tier up to JIT (RAM cache, dies with the process). **Five minutes in:** the hot set runs as JIT code, and the JIT has been writing the runtime profile. **Second cold start:** the JIT cache died, so execution starts in the interpreter — but if the idle background `dex2oat` ran, the profiled methods are now AOT code in `.odex` and launch two starts with compiled code for the measured hot path. The persistent artifact is the profile at `/data/misc/profiles/cur/0/<pkg>/primary.prof.txt`, not the JIT code; Baseline/Cloud Profiles give *first* launches that head start by shipping the measurement in the APK.
2. 20,000 > 12 KB (`kLargeObjectThreshold = 3 × 4 KB`), and `byte[]` is a primitive array — it goes to **LOS**. Size: 12-byte array header (class 4 + lock 4 + length 4) + 20,000 payload, page-mapped — about 20 KB across 5 pages. **CC never copies it:** LOS objects never move; reclamation unmaps pages, returning memory to the OS immediately.
3. `Object[20000]` stays in **RegionSpace** even at 80 KB. Only reference-free primitive arrays enter LOS — their contents are never scanned or barriered, so they must contain no references. An `Object[]` is all references; CC's barrier must see it, so it lives where objects are traced and evacuated (~16-byte array header + 20,000 × 4-byte compressed refs ≈ 80 KB).
4. Growth limit = **192 MB** — the second number of `190MB/192MB`, this device's smaller-than-Pixel birthright. Occupancy ≈ 99% — riding the ceiling; the next allocation beyond what the collector frees in time throws OOM. `15 (4MB) LOS objects` means large primitive arrays churning — decode buffers or serialized blobs — so the next allocation-heavy screen triggers back-to-back GCs fighting the rate, then OOM.
5. Not out of total memory — it hit the *Java-heap* cap (196 MB ≈ the 192 MB class) while the real growth is native (480 MB, uncapped: malloc has no manifest limit). `largeHeap="true"` raises the Java ceiling to `heapsize` (~2×) and merely delays the error; the native growth continues, and lmkd kills on whole-process RSS anyway. Fix the native ledger: cap or downsample the bitmaps and buffers.
6. The pause field is only the stop-the-world slice; the **concurrent phases burn CPU on the same cores the UI needs** (`total 148ms` vs `pausing 0.9ms` in question 4 — that ratio is the cost). GC CPU and barrier traffic scale with **allocation rate**, and one collection per 200 ms means the scroll path allocates MBs per second. Levers: cut allocations per frame (reuse objects, avoid per-item boxing and decoded temporaries) and stop churning big buffers.
7. After `fork()`, the boot-image pages are shared copy-on-write. Writing one — which moving an object would do — makes the kernel copy the page: **private-dirty** in that process, sharing with every other app permanently destroyed. One moved object can cost a 4 KB page of unique RAM per touching process, and the image is ~100 MB of such pages — hence the zygote's pre-fork GC, which compacts live objects into the shared zygote space and empties the garbage first, so children fork with maximal sharing.
8. Shenandoah: 8 bytes × 500 M objects = **4 GB of permanent forwarding overhead** — acceptable on a server heap of tens–hundreds of GB. ART: zero steady-state bytes — the forwarding address exists only mid-evacuation, then the lock word returns to lock/hash duty. On a phone the median object is ~24–32 bytes, so 8 bytes per object would be ~25% overhead at exactly the layer the memory class forbids. The law: **header overhead is a per-object tax — its acceptability scales with the bytes-per-object budget, not the heap size.**
</details>
