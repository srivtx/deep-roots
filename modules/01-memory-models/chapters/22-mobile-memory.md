# Chapter 22 — Mobile memory: Android and iOS, where the OS is the GC

> **The 30-second version.** A phone has no disk swap, a few gigabytes of RAM shared with the OS and dozens of background processes, and a battery that pays for every page moved. So the mobile platforms made the decision desktops never had to: when memory runs short, don't reclaim pages — **evict processes**. Android ranks every process in importance bands (`lmkd`, `oom_score_adj`) and kills from the bottom; iOS ranks every process in Jetsam priority bands and does the same. Eviction replaces reclamation: **the OS is the GC**, liveness is measured in foreground seconds, and `SIGKILL` is the sweep. Above that killer sit two very different heaps — Android's ART (a JVM cousin, Chapter 12) with a concurrent copying collector over a fork-shared boot image, and iOS's ARC (Chapter 16) whose deterministic frees keep *dirty pages* — the one number Jetsam actually counts — low. Learn the two contracts and you can predict which process dies, when, and why your bug report says "killed, not crashed."

## The contract, derived: no swap, shared RAM, and joules

Chapter 03 ended with the desktop bargain: RAM runs out, the OS evicts a page and writes it to swap, and the process never knows. Phones break all three preconditions at once.

1. **No backing store.** Historically, Android and iOS shipped with no swap partition at all — internal flash is small, a page write costs flash wear (Chapter 21's NAND endurance, same physics), and a 4 KB page out to flash costs ~100 microsecond-class latency plus a wear tick. Paging a phone hard would drain its battery and age its storage in weeks. (Honesty, as promised: modern Android *does* run `zram` — swap to a compressed block *in RAM*, covered below — and iOS compresses instead of swapping. Neither writes dirty pages to disk. "No swap" survives as "no disk swap"; compression is the honest asterisk.)
2. **Little RAM, heavily shared.** A 2024 phone has 4–8 GB (Android 8–14 era, 64-bit). The system UI, the launcher, the camera daemon, and 30–50 *cached* app processes live in that same physical pool. Your app is one tenant among dozens.
3. **Every page costs joules.** A collector that touches a 200 MB heap pays DRAM traffic (Chapter 02's latency ladder); a compacting collector *copies* megabytes. On battery power, memory traffic is a first-order energy cost, not just a latency cost.

Derive the consequence. With no disk to page to, "reclaim memory" has only three meanings: **drop clean pages** (they can be re-read from the file they map), **compress dirty pages** (zram on Android, the iOS compressor), or **evict a whole process** and take *all* of its pages at once — clean, dirty, everything — with one `SIGKILL`. The first two buy time; when they run out, only the third is left. So memory management stops being reclamation and becomes **scheduling**: the OS ranks processes by importance and kills the cheapest one to lose. That is why this chapter's title says the OS is the GC — a garbage collector answers "which objects are dead?" from a root set; lmkd and Jetsam answer "which *processes* are dead?" from a root set of foreground-ness and perceptibility. The sweep is a kill. Chapter 12's collector would recognize the shape.

## Android: the zygote — one parent, a thousand forks

Every Android app process is born the same unusual way: it is a `fork()` of one pre-existing process called **zygote**. At boot, `init` starts the zygote; ART loads and verifies the framework classes, preloads common resources, and then the zygote *sleeps on a socket*. Launching an app is: `fork()` the zygote, never `exec()`, and specialize the child. The child inherits the entire preloaded address space.

Why this design? A cold app start would mean: map the ~100–200 MB boot image (framework classes, pre-verified, AOT/JIT-warmed code), verify classes, initialize the runtime — hundreds of milliseconds of pure setup, per app, per launch, competing for the same shared RAM across 50 processes. Fork-sharing amortizes it to near zero, using Chapter 03's copy-on-write:

```
              ZYGOTE (one process, started at boot, parent of every app)
  ┌─────────────────────────────────────────────────────────────────────┐
  │ image space   boot.art: ~100-200 MB of framework classes, mapped    │
  │               read-mostly ---- these pages get SHARED with children │
  │ zygote space  runtime objects PRELOADED and pre-compacted here      │
  │               by the "zygote GC" so they too will be shared         │
  │ alloc space   the zygote's own scratch heap: mostly garbage at      │
  │               fork time; the forked copy reclaims it lazily        │
  └─────────────────────────────────────────────────────────────────────┘
         │ fork()             │ fork()             │ fork()     (no exec —
         ▼                    ▼                    ▼            the warm
    ┌─────────┐         ┌─────────┐          ┌─────────┐       image rides
    │ App A   │         │ App B   │          │ App C   │       along)
    │ shared: SAME physical frames, mapped read-only in every child      │
    │ private: the child's own alloc space, born empty                  │
    └─────────┘         └─────────┘          └─────────┘
       first WRITE to a shared page → page fault → the kernel copies
       4 KB (16 KB on the new page size, see below) → page turns private
```

The economics: all apps share one physical copy of the framework image. If the boot image is 150 MB and 40 processes are alive, the *unique* RAM cost is still ~150 MB, not 6 GB. Chapter 03's COW is the whole trick: pages stay shared exactly as long as nobody writes them, which is why ART goes out of its way to keep image-space pages read-only and to run a **zygote GC** in the parent — a collection that compacts live preloaded objects into the zygote space and clears the scratch alloc space *before* forking, so children inherit maximal sharing, minimal garbage, and no per-child COW faults waiting to happen.

Two honest caveats. First, the per-app heap is entirely private — the sharing is the *image*, never your objects; a Kotlin object created after fork is yours alone. Second, zygote forking has security costs (all children start with identical address layouts; mitigated because the boot-time layout randomization is per-boot, not per-app — a fact Chapter 21's exploit writers know well).

## ART's heap: a JVM cousin rebuilt for pockets

The child process runs **ART** (Android Runtime) — Dalvik's successor, and Chapter 12's cousin. It is a bytecode VM with a tracing GC, but shaped by this chapter's contract: concurrent, compacting, and fork-friendly. The heap is split into spaces:

```
  ART heap of one app process (Android 8-14 / ART, 64-bit)
  ┌───────────────────────────────────────────────────────────────────┐
  │ image space   boot.art, SHARED (COW from zygote), read-only-ish    │
  │ zygote space  preloaded objects, SHARED                            │
  ├───────────────────────────────────────────────────────────────────┤
  │ alloc space   PRIVATE, "region space": bump-pointer allocation    │
  │               into fresh regions — this is where your objects     │
  │               are born (a few nanoseconds per object)             │
  │ LOS           PRIVATE, "large object space": objects above the     │
  │               large-object threshold (~3 pages ≈ 12 KB in ART's    │
  │               default config), page-backed, never moved            │
  │ LOS-zygote    SHARED, large boot-image objects, so bitmaps and     │
  │               tables in the image share pages too                  │
  └───────────────────────────────────────────────────────────────────┘
```

The collector that defines modern ART (Android 8.0, 2017, and still the backbone through Android 14) is the **concurrent copying collector (CC)**. It is Chapter 12's semispace idea done concurrently: Cheney-style evacuation (Chapter 12's scavenger, Chapter 15's V8 cousin) moves live objects out of "from" regions into "to" regions while the app keeps running. To make that safe without stopping the world, every heap read goes through a **read barrier**: each object header carries a forwarding pointer (Brooks-style), and any thread that touches an object during evacuation may be handed the moved copy transparently. The app never pauses for the copy; it pays a small toll per read instead — the same trade Go made in Chapter 11. Since Android 10, ART layers a **young generation** on top: bump-pointer allocation into fresh regions, and young collections that evacuate only recently-allocated regions. That is the modern ART shape — regions + concurrent evacuation, not Eden/Survivor/Old. On top of it run the **sticky collections**: a "sticky" GC traces only objects allocated *since the last GC* (age 0), which catches the die-young distribution (Chapter 12's weak generational hypothesis) at a fraction of a full collection's cost.

**The two heaps, and the cap.** The Java/Kotlin heap has a hard per-app limit: the `dalvik.vm.heapgrowthlimit` property, historically 16 MB on 2010 devices, typically 128–256 MB on 2020s flagships (Android 8–14). `ActivityManager.getMemoryClass()` tells you yours; `android:largeHeap="true"` in the manifest switches you to the full `heapsize` (often 2–4x larger, via `getLargeMemoryClass()`). The limit exists for fairness under the killer: lmkd needs each app's *potential* Java-heap footprint to be bounded so that N cached apps can coexist predictably. The insider truth: **the native heap is separate and uncapped** — `malloc` via jemalloc (since Android 5.0) or scudo (Android 11+; Chapter 19's allocator taxonomy) has no manifest limit, only address space. But lmkd watches whole-process RSS, not the Java heap, so "uncapped" means "the kill comes from a different ledger."

This boundary has moved historically, and the history is the lesson. Bitmaps, pre-Android 3.0 (API ≤ 10): pixel data in native memory, freed by a manual `recycle()` call, not by GC — leak city. Android 3.0–7.1: pixel data moved *onto* the Java heap so GC could manage lifetimes — apps that loaded a few full-res photos hit the 32–64 MB heap limits of that era and died. Android 8.0+ (2017): pixel data moved back to the native heap, cleaned up automatically via `NativeAllocationRegistry` (a finalizer-ish bridge — Chapter 12's caveat applies) with the pixels in ashmem/native buffers. Three regimes, and the platform moved the boundary each time while app code survived by calling the same `BitmapFactory.decode...` API. **Platforms move the boundary; code survives by not assuming where it lies.**

**JNI and verification.** Objects referenced from C++ via JNI are tracked in reference tables: local references per-frame, global references explicit. The classic failure is a loop creating thousands of local references without `PushLocalFrame`/`PopLocalFrame` — the local table overflows (logcat: "local reference table overflow", capacity on the order of hundreds-to-thousands of entries). `CheckJNI` (enabled by default in debuggable builds, `-Xcheck:jni`) validates reference misuse and type confusion, and ART's `-Xverify-heap` style heap verification re-checks heap invariants during GC in debug builds — the same "make the runtime police the contract" philosophy as Chapter 12's JVM tooling.

**The measurement tools.** Android Studio's Memory Profiler graphs the Java heap (allocations, GC events, and — the number that predicts jank — the *allocation rate*). `adb shell dumpsys meminfo <pkg>` is the field tool; its columns deserve real definitions:

- **RSS** (resident set size): every physical page mapped into the process — double-counts shared pages across processes.
- **USS** (unique set size): private pages only; what the process would free if killed right now.
- **PSS** (proportional set size): private pages *plus* each shared page charged 1/N to each of its N sharers. The fair-share number, and the one Android reports — sum PSS over all processes and you get total RAM used without double counting, which is why it's the accounting system uses for rankings.
- **Private Dirty vs Private Clean**: dirty pages (written, must be re-created if evicted — anonymous heap, stacks) vs clean pages (file-backed, unmodified — code, can be dropped and re-read).

`meminfo` also reports `SwapPss` — your pages currently sitting *compressed inside zram*. On modern Android, zram is a real device (`/dev/block/zram0`, typically sized 50–100% of RAM on Pixels) trading CPU and memory bandwidth for effective capacity; the honest way to say the no-swap story is: Android doesn't page to disk, it pages to *a smaller RAM inside RAM*.

## lmkd: killing as scheduling

Android's reclaimer-of-last-resort has a lineage. The original **lowmemorykiller** was an in-kernel driver: it watched free memory against a threshold array (`minfree[]`) and killed the process with the worst `oom_adj`. It was deprecated and eventually removed from staging (Android 8–9 era); its replacement, **lmkd**, runs in userspace and kills with the kernel's normal OOM machinery. Modern lmkd (Android 10+) is driven by **PSI (pressure stall information)**: instead of sampling free memory, the kernel reports how long tasks have actually *stalled* on memory (`/proc/pressure/memory` — "some" and "full" stall percentages over 10s/60s/300s windows). lmkd subscribes and kills *before* the system thrashes, which is the right signal to act on: stall time is the symptom users feel.

The ranking is `oom_score_adj` bands set by ActivityManager as process state changes (Android 8–14 values, approximate):

- **Foreground** (the app the user is touching): adj 0 — dies last.
- **Visible / perceptible** (visible activity, playing audio): adj 100–200.
- **Backup, services, heavy weight**: adj ~300s.
- **Previous / home**: adj ~400–700.
- **Cached** (backgrounded, kept warm for fast task-switching): adj 916–999 — dies first, biggest-footprint first within the band.

That last band is the one every Android developer must internalize, because it is a **memory-model statement**: a cached app can be killed at any moment, at zero notice, with no callback. The platform's promise is *process death and restoration*, not process survival:

```kotlin
object SessionCache {                     // a Kotlin `object` = static singleton
    var lastResults: List<Result>? = null // this field DIES WITH THE PROCESS
}

override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    val results = SessionCache.lastResults                 // null after a kill+restore:
        ?: savedInstanceState?.getParcelableArrayList(KEY) // the Bundle survives in the
        ?: emptyList()                                     // system's memory, not yours
}
```

"Don't rely on statics surviving" is not advice about hygiene — it is the contract: your statics live in your heap, your heap lives in a killable process, and the OS reclaims processes. The polite warning system is `onTrimMemory` (delivered *before* you become a kill candidate, unlike everything above):

```kotlin
// ComponentCallbacks2 levels (Android 8-14): the escalation ladder
override fun onTrimMemory(level: Int) {
    when (level) {
        TRIM_MEMORY_RUNNING_MODERATE,   //  5: device memory getting low, we run
        TRIM_MEMORY_RUNNING_LOW,        // 10: we run, pressure real
        TRIM_MEMORY_RUNNING_CRITICAL    // 15: about to get ugly — release now
            -> cache.resize(16 * 1024 * 1024)
        TRIM_MEMORY_UI_HIDDEN,          // 20: our UI is hidden — trim caches
            -> cache.resize(32 * 1024 * 1024)
        TRIM_MEMORY_BACKGROUND,         // 40: we are a CACHED process now
        TRIM_MEMORY_MODERATE,           // 60: mid kill-queue
        TRIM_MEMORY_COMPLETE            // 80: back of the queue — free everything
            -> cache.evictAll()         // release it before lmkd releases us
    }
}
```

It is a hint, not a guarantee: lmkd can still kill you without ever trimming you.

## iOS: Jetsam, the compressor, and the dirty-page economy

iOS runs the same derivation to a different answer. There is no GC (Chapter 16's ARC, always), no zram, no swap to disk — and one process killer, **Jetsam** (the user-visible name; the kernel subsystem is `memorystatus`). Every process gets a **footprint limit assigned by its Jetsam priority band** (iOS as of 17-era, 64-bit): the foreground app's limit is generous (apps routinely hold 3–5 GB on 6 GB devices); **app extensions get hard limits in the tens of MB** — a fraction of their containing app's, because the OS assumes extensions are small and short-lived. When pages run short, the kernel walks the band table from the lowest priority upward and kills until pressure clears, largest footprint first within a band. There is **no OOM exception, no callback, no `didReceiveMemoryWarning` that saves you** — the memory warning (also real, also polite, also ignorable) is your *last* chance; the kill itself is `SIGKILL` (signal 9), and your code never executes again. From your app's perspective it looks like a crash with no stack; from the kernel's perspective it was Tuesday.

What Jetsam counts is the **footprint**, and the accounting is the whole game on iOS:

```
  pages of ONE iOS process                        counts in footprint?
  ┌─────────────────────────────────────────────────┐
  │ DIRTY  40 MB   written anonymous pages: ARC      │  YES
  │               objects, stacks, decompressed      │  (must stay or die —
  │               images, Swift arrays               │   cannot be evicted)
  ├─────────────────────────────────────────────────┤
  │ COMPRESSED 8 MB  was 30 MB of cold dirty pages   │  YES
  │               the compressor pager squeezed       │  (at compressed size:
  │               them and parked them in RAM         │   RAM still spent)
  ├─────────────────────────────────────────────────┤
  │ CLEAN  80 MB   mmapped read-only code + assets,  │  NO
  │               never-written file pages, purgeable│  (evictable: re-read
  │               memory                            │   from NAND on next
  │                                                  │   touch — ~free)
  └─────────────────────────────────────────────────┘
  footprint = dirty + compressed = 48 MB   ← the number Jetsam watches
```

**The VM compressor** (iOS 7 / macOS 10.9, 2013, still the design) is the honest answer to "no swap": when dirty pages go cold, the kernel compresses the least-recently-used of them — a fast integer dictionary coder (WKdm in xnu) — into a compressed pool *in RAM*, typically 2–3x reduction. Compressing a page costs microseconds of CPU; swapping it to flash costs a write tick of wear plus milliseconds of latency on both ends. On a phone, compression beats disk swap on joules, on wear, and on worst-case latency at the same time. But read the diagram's lesson: the compressed pages **still count in footprint**. Compression defers; it does not cancel. It converts 30 MB of cold dirty memory into 8 MB of still-spent memory, and buys you headroom — until your next 40 MB of allocations erases it. **Compression is a buffer, not a solution.** That law is why iOS engineering culture obsesses over *dirty pages* rather than "memory used."

**ARC's role** (Chapter 16) slots precisely into this economy: deterministic frees mean a released object's pages go back to the kernel *now* — dirty memory actually shrinks, unlike a GC'd heap that holds capacity until the next collection (or never compacts). The cultural rules that follow are footprint rules:

```swift
// Rule 1: autoreleasepool in tight loops. Foundation/ObjC-returning APIs
// hand you autoreleased temporaries; without a pool, their releases are
// deferred to the runloop drain — i.e., after the ENTIRE loop. Dirty
// pages pile up 10,000 iterations deep.
var total = 0
for path in paths {
    autoreleasepool {                    // drain here, every iteration
        let text = try String(contentsOfFile: path, encoding: .utf8)
        total += text.filter { $0.isPunctuation }.count
    }                                    // temporaries released NOW,
}                                        // pages returned immediately

// Rule 2: weak references break cycles (Chapter 16): a cycle is dirty
// pages that ARC will never free — footprint grows monotonically.
manager.onUpdate = { [weak self] in self?.refresh() }
```

And the real insider lever — **memory-map your assets.** Dirty is what kills you; clean is ~free; `mmap` makes files clean:

```swift
let eager = try Data(contentsOf: fileURL)
// ^ slurps 200 MB into the heap: DIRTY. Footprint +200 MB. Jetfood.

let mapped = try Data(contentsOf: fileURL, options: .mappedIfSafe)
// ^ vm_remap: the file's pages are mapped into your address space, CLEAN.
//   Touch a page → read fault → page in from NAND. Memory pressure →
//   kernel evicts them silently → re-read on next touch. Footprint ~0.
//   (Requires read-only, safely-file-backed data — hence .mappedIfSafe.)
```

**The tools.** Xcode's memory gauge shows your live footprint split into dirty + compressed — watch that number, not "resident." The **Memory Graph** debugger snapshots every heap object and its references, flagging retain cycles (Chapter 16's blind spot made visible). **Instruments'** Allocations template gives live bytes by call stack, and VM Tracker breaks the address space into Dirty / Compressed ("swapped" in its column naming) / Clean categories. **MetricKit** (`MXAppMemoryMetric`, iOS 14+) delivers daily peak and average footprint to Xcode Organizer, and `MXAppExitMetric` records the jetsam exits with their limit fields — your production telemetry for "the OS GC'd us."

## The two answers side by side — and what is coming

| | Android (8–14 / ART) | iOS (17-era) |
|---|---|---|
| app object memory | ART: tracing GC, concurrent copying + young gen | ARC: compiler-inserted refcounts, no collector |
| frees | eventually, in bulk, concurrently | deterministically, per-object |
| sharing trick | zygote `fork()` + COW boot image, per boot | dyld shared cache: >1 GB of system frameworks mapped clean into every process at build time — the biggest shared-memory artifact on the device, sharing without ever forking |
| reclaim under pressure | zram (compress RAM into RAM) + clean eviction | the compressor pager (compress dirty) + clean eviction |
| the killer | lmkd, PSI-driven, `oom_score_adj` bands | Jetsam / memorystatus, priority bands |
| per-app cap | Java heap growth limit (128–256 MB typical) + *uncapped* native heap | footprint limit per band (GBs foreground, tens of MB for extensions) |
| what it buys | process warmth: cached apps relaunch instantly; generous per-process ceilings | smallest possible dirty footprint per app; no GC pauses on the UI thread |
| what it costs | cached processes die arbitrarily; GC costs jank if allocation rate spikes | retain cycles are fatal; extensions are tiny; ARC costs per-pointer instructions |

Neither platform "swaps" like the desktop of Chapter 07: both concluded that on flash + battery, dirty pages should be *compressed in RAM or taken by force*, never written to storage. Android spends its complexity budget on sharing and warmth (one image, N forks, cached processes as a relaunch cache); iOS spends it on accounting precision (dirty vs clean as the single metric that matters).

One paragraph on the future. **MTE** (Arm Memory Tagging Extension) is rolling out on Android — Pixel 8 and 8 Pro with Android 14 are the first consumer devices — tagging every 16-byte granule with a 4-bit key checked on access, turning Chapter 21's use-after-free and overflow bugs into synchronous crashes instead of silent corruption; it costs tag memory, which is exactly a memory-model decision (5/64 bytes overhead on tagged regions). Android is also mid-transition to **16 KB pages** (Android 15-era), which changes every constant this module taught — COW granularity (Chapter 03), allocator bin geometry (Chapter 19), ART's large-object threshold (3 pages = 48 KB now). On the Apple side, iPhones keep growing RAM and per-process limits grow with it, while **Apple Silicon's unified memory** (one pool, CPU + GPU + Neural Engine, no dedicated GPU VRAM) makes the "shared physical pool" premise of this chapter literal at the hardware level. The contract — kill, don't page — has not changed; only the numbers do.

## What insiders know

- **PSS vs RSS vs USS is an interview trap with one clean answer:** RSS double-counts shared pages, USS is private-only (what killing frees *now*), PSS is private + shared/N — and the killer property: sum of PSS over all processes ≈ total RAM used, which is why Android reports PSS everywhere and ranks blame with it.
- **"GC is concurrent but your frame budget isn't."** ART's CC never pauses you for a whole collection, but it burns CPU on the same cores your render thread needs, and its read-barrier traffic scales with allocation rate. A scrolling screen allocating ~300 KB/frame (decoded temporaries, lists, lambdas) produces GC activity every few hundred ms and Choreographer "Skipped 120 frames" logs — at 120 Hz you own 8.3 ms per frame, and the allocation-rate graph in Memory Profiler is the diagnostic, not the pause log.
- **Jetsam bands are sorted by priority, and "foreground" ≠ "focused":** the top, user-focused app sits in a band above merely-foreground processes, with the largest limit; kills walk lowest bands first and biggest footprint first *within* a band. So the 600 MB focused app survives while a 100 MB foreground-support extension dies — band beats size, every time.
- **Dirty vs clean is THE iOS metric.** A 500 MB read-only file mapped with `mmap`/`.mappedIfSafe` costs ~0 footprint; the same data slurped into a `Data` costs 500 MB of dirty and a jetsam kill in an extension. "Memory-map your assets" is not folklore, it is the accounting rule.
- **zram's live compression ratio is readable on device:** `cat /sys/block/zram0/mm_stat` — the first two fields are `orig_data_size` and `compr_data_size`; on real Pixel workloads expect ~2.5–3.5x. Your "swap" is RAM, and its ratio is your real memory multiplier.
- **Read logcat GC lines like a perf trace:** lines like `Background concurrent copying GC freed 123456 (12MB) AllocSpace objects, 8 (32MB) LOS objects, 49% free, 96MB/192MB, pausing 0.5ms` give you freed bytes, LOS traffic, heap occupancy against the growth limit, and pause time in one line — free total / occupancy tells you if you're riding the limit.
- **The 16 KB page transition is a memory-model change rippling through every allocator** (Chapter 19): page-backed large-object thresholds, `mmap` alignment requirements, and COW granularity all move at once; native libraries with 4 KB alignment assumptions break. Android moved from 4 KB assumptions the way it once moved bitmaps — the platform shifts the boundary, the code has to follow.

## See it yourself

**Android — the ledger and the trim callback.** With a device attached and any app running:

```text
$ adb shell dumpsys meminfo com.example.app   # abridged; numbers vary per device
              Pss  Private  Private  SwapPss      Rss
                    Dirty    Clean    (zram)
  Heap      24312    24000      312      614    24624
  Native    88104    88000      104        0    88200
  ...
  TOTAL    152000   150300     1692      614   189400
  App Summary:  Java Heap 24624  Native Heap 88000  Code 12100  ...
```

Notice: `Heap` (the ART Java heap, capped by the growth limit) and `Native` (malloc — here 3.6x bigger, uncapped) are separate lines; `SwapPss` is your zram residency; `Pss` is the fair-share number, `Private Dirty` the kill-frees-now number. Now poke the warning system without waiting for real pressure:

```text
$ adb shell am send-trim-memory com.example.app 15   # = RUNNING_CRITICAL
$ adb shell am send-trim-memory com.example.app 80   # = COMPLETE
```

Log your callback: level 15 arrives while running; level 80 means you are a cached process at the back of the queue. Bonus, on Pixels: `adb shell cat /sys/block/zram0/mm_stat` — divide field 2 by field 1 for the live compression ratio.

**iOS — find your jetsam ceiling empirically.** Create a trivial app-extension target (Share or Action extension), attach the debugger, run this in its `viewDidLoad` (Xcode: watch the memory gauge's footprint number climb):

```swift
var payloads: [Data] = []
for i in 0..<500 {                          // 500 MB ceiling, way over the band
    payloads.append(Data(count: 1_000_000)) // malloc + zero-fill = 1 MB DIRTY
    print("iteration \(i): footprint ~\(i + 1) MB")
}
```

What happens: the loop prints up to some iteration in the tens of MB, then *silence* — no exception, no error path, the debugger detaches with "Terminated due to signal 9". The same 500 MB loop in the containing app finishes fine. Afterwards: Xcode Organizer → Crashes/Metrics shows either an `EXC_RESOURCE (RESOURCE_TYPE_MEMORY)` report with the limit, or a jetsam exit recorded by `MXAppExitMetric`/`MXAppMemoryMetric`. You have just measured, in one run, the band your process lives in — and proven "killed, not crashed."

## How to use this knowledge

- **Android OOM triage:** "OutOfMemoryError: Failed to allocate" inside `decodeStream` means the Java-heap growth limit; a silent background death with no stack means lmkd and the RSS ledger. Different heaps, different fixes (downsample bitmaps vs. free native caches / split work into a foreground service).
- **Scroll jank war stories:** you can now read a "Skipped N frames" + frequent-GC trace as an *allocation-rate* bug and defend it: the GC is concurrent, the frame budget is not.
- **iOS extension crash triage:** a crash at 70 MB in an extension that "runs fine at 600 MB" in the app is not proof of a leak — check dirty vs clean in VM Tracker, check the band limit, reproduce with the 1 MB loop before hunting retain cycles.
- **Design decisions:** cache sizing per `getMemoryClass()`, `mmap` for read-only lookup tables and assets, `autoreleasepool` in parse loops, `weak` in every long-lived callback — each one is a footprint or fairness rule, not style.
- **Interview answers:** "why doesn't my phone swap?" (joules + wear + latency, then compression instead), "why does Android have a zygote?" (COW amortizes the boot image — Chapter 03's mechanism as an OS architecture), and "what happens when an iOS app exceeds its memory limit?" (no exception — signal 9, and why).

## Connections

- **Chapter 03** — copy-on-write and demand paging are the zygote's engine and the "clean page = re-read from file" rule behind dirty/clean accounting.
- **Chapter 07** — the desktop swap model this chapter deliberately abandons; the contrast defines both platforms.
- **Chapter 11 / 12 / 15** — Go's concurrent GC, the JVM's generational scavenger, V8's read barriers: ART's CC is their close cousin (Cheney evacuation + Brooks-style read barrier, restated for fork-shared heaps).
- **Chapter 16** — ARC, weak references, autorelease pools: the machinery whose *footprint* consequences this chapter cashes in.
- **Chapter 19** — jemalloc/scudo: the uncapped native heap under every Android app, and the allocator geometry the 16 KB page transition reshuffles.
- **Chapter 21** — memory corruption and MTE: the security module's defenses landing on Android first (Pixel 8+), with a memory cost that is itself a memory-model trade.
- **Chapter 23** — the unification table places "eviction as reclamation" as the third reclamation strategy alongside manual free and tracing.
- **ROADMAP** — the mobile-internals and security modules both start here: app-kill forensics, zram tuning, and exploit mitigations all assume this chapter's contracts.

## Quiz

1. **(concept)** Phones have no disk swap. Derive in three steps why this forces *kill* as the primary reclamation strategy, and explain why zram and the iOS compressor do not break the argument.
2. **(predict)** Three Android processes are alive: A — the foreground app, `oom_score_adj 0`, RSS 400 MB; B — a cached app, adj 916, RSS 80 MB; C — a visible, perceptible service, adj 200, RSS 150 MB. Pressure rises until something dies. Which dies first, which dies last, and why doesn't B's small size save it?
3. **(predict the number)** An app's `dumpsys meminfo` shows Private Dirty 40 MB, Private Clean 10 MB, and a 60 MB shared library mapped equally by 6 processes. Compute RSS, USS, and PSS, and state which number lmkd's fairness accounting effectively uses.
4. **(predict)** An iOS process has 80 MB of mmapped read-only asset pages resident, 30 MB of dirty pages, and 20 MB of formerly-dirty pages now compressed to 8 MB. What is its footprint? Which of the three quantities is ~free under memory pressure?
5. **(bug)** An Android photo-grid app logs `Skipped 120 frames` on scroll. Memory Profiler shows "concurrent copying" GC events every ~200 ms, each freeing ~8 MB; the native heap is flat. Diagnose the root cause and name two fixes that attack it.
6. **(bug)** A share extension is killed at 70 MB footprint while its containing app runs at 600 MB without complaint. The team concludes "the extension leaks." What is wrong with that diagnosis, and how do you prove where the actual ceiling is?
7. **(cross-chapter, Ch 16)** ARC frees objects deterministically — so why does an iOS app whose long-lived manager holds a self-capturing closure still get jetsammed? Connect ARC's blind spot to this chapter's footprint accounting, and name the tool that shows you the cycle.
8. **(cross-chapter, Ch 03)** Describe exactly what the kernel does on the first write by a forked app to a shared zygote boot-image page, why ART's "zygote GC" exists to maximize how rarely that happens, and why Apple's dyld shared cache achieves sharing without any fork at all.

<details>
<summary>Answers</summary>

1. **The derivation.** (1) With no backing store, reclamation can only mean dropping clean pages, compressing dirty pages, or destroying the process that owns them. (2) Clean-page eviction is bounded by how much file-backed memory exists; compression buys 2–3x, not infinity, and costs CPU/joules each time. (3) When both are exhausted, the only remaining lever is taking an entire process's pages at once — dirty, clean, and compressed together. zram and the compressor don't break the argument because they are the *second* lever, not the third: zram is swap into RAM (finite, ratio-bounded), and compressed pages still occupy RAM and still count in footprint. They defer the kill; they never repeal it.
2. **B dies first; A dies last.** lmkd kills from the worst `oom_score_adj` upward: B (cached, 916) is first, then C (perceptible, 200) would go before A ever does. B's 80 MB doesn't save it because band beats size — the point of cached apps is that they are *supposed* to be disposable (the platform will re-fork the process and restore state), so killing B reclaims 80 MB at near-zero user-visible cost, while killing A would be visible instantly. Within a band, size matters (biggest first); across bands, it doesn't.
3. **RSS = 40 + 10 + 60 = 110 MB** (every resident page, shared pages counted in full). **USS = 40 + 10 = 50 MB** (private only — what killing the process frees *right now*). **PSS = 40 + 10 + 60/6 = 60 MB** (private plus each shared page split over its 6 sharers). PSS is the fairness number: summing PSS across all processes yields total RAM without double counting, so it's the metric the platform's accounting (and your blame attribution) is built on. Note Private Clean counts in PSS/USS but is cheap to lose — it's re-readable file data.
4. **Footprint = 30 + 8 = 38 MB.** Dirty always counts; compressed counts at its compressed size (that's the RAM it actually occupies); clean mapped file pages don't count at all. The 80 MB of mapped assets is the ~free part — under pressure the kernel evicts those pages and re-reads them from NAND on next touch, which is why memory-mapping read-only assets is the single biggest footprint lever on iOS. (Caveat as taught: `mappedIfSafe` requires read-only, safely file-backed data.)
5. **It's an allocation-rate bug, not a heap-size bug.** The GC is working (each event frees ~8 MB — the objects *do* die) but the scroll path allocates so much per frame (decoded temporaries, adapter lists, boxed values — commonly hundreds of KB per `onBindViewHolder`) that CC runs every ~200 ms, burning CPU on cores the render thread needs and adding read-barrier traffic. At 60/120 Hz the frame budget is 16.6/8.3 ms; the GC never "pauses" you long, it steals the budget continuously. Fixes: cache the decoded thumbnails (an `LruCache` sized to `getMemoryClass()`), decode once at display size rather than full-res plus downsample, and reuse objects/arrays in the bind path — the goal is allocation-rate near zero per frame.
6. **The diagnosis confuses two ceilings.** Extensions live in tighter Jetsam bands with hard footprint limits independent of (and far below) their containing app's; 70 MB may simply be that band's limit, reached by *correct* code. Proof: run the 1 MB-dirty-per-iteration loop in the extension and watch which iteration the process dies at (signal 9, no exception) — if it dies at the same 70 MB with no leak in your code, the ceiling is the band, not a bug. Then look at dirty vs clean in Instruments' VM Tracker: if "correct" data is being slurped eagerly instead of mmapped, you can cut footprint without removing features. (If it dies *earlier* with your real workload than with the loop, *then* go hunt the leak.)
7. **ARC has no cycle detector (Chapter 16's price), and this chapter supplies the consequence.** The closure captures `self` strongly; the manager strongly holds the closure → a cycle: neither object's refcount can reach zero, so their pages are never released. Freed ARC objects return dirty pages to the kernel immediately, but never-freed objects keep them forever — so footprint grows monotonically as the cycle accumulates state, and Jetsam eventually kills the process (signal 9) regardless of how deterministic the rest of your frees are. The fix is `[weak self]` in the capture list (the memory warning arrives *before* the kill; it is your last chance, not a rescue). The tool: Xcode's **Memory Graph** debugger — it snapshots the object graph and flags the retain cycle explicitly.
8. **The write.** The forked child shares the boot-image page via a read-only PTE pointing at the same physical frame (Chapter 03's COW). The first store traps: the kernel allocates a fresh physical page (4 KB, or 16 KB on the new page size), copies the frame, remaps it private-and-writable in the child, and restarts the faulting store. From then on that page is private RAM paid by one app. The **zygote GC** exists to minimize how often this happens: it compacts the zygote's live preloaded objects together (and clears garbage) before forks, so pointer-dense, frequently-touched data is either genuinely read-only (shared) or packed — reducing the surface of pages any child will dirty. The **dyld shared cache** shares without forking: at OS-build time, all system frameworks are merged and pre-relocated into one file, and every process *maps* those clean, file-backed, read-only pages directly — sharing achieved by construction (identical clean mappings), no COW machinery needed and nothing to write, because it never contains mutable process state. The zygote shares *mutable runtime state* via fork; the shared cache shares *immutable code and data* via mapping.

</details>
