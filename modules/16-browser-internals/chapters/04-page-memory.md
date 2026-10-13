# Chapter 04 — The page's memory: leaks, workers, WASM

> **The 30-second version.** "The page's memory" is not the JS heap.
> A loaded tab pays six storages at once: the V8 heap (floor 1-3 MB
> before your first object, V8 2024-era), the Oilpan DOM heap (ch02),
> the decoded image cache (a 4,000×3,000 JPEG: ~500 KB on disk, ~48 MB
> decoded — 12 M px × 4 B never compresses), fonts, compositor layers,
> and the disk cache's RAM index. Workers multiply the floors: each is
> its own V8 isolate, ~1-2 MB stack, script copies — parallelism is
> billed in MB. Then six canonical leaks, each one retainer edge you
> can name in a heap snapshot. The deep end is WASM: one linear memory
> per instance, where V8 reserves 10 GB of address space (4 GB + 6 GB
> guard) so out-of-bounds accesses become page faults in the guard —
> module 01 ch03's "page fault as a branch," promoted into a compiler
> optimization.

## The inventory: six heaps pretending to be one

Chapter 01 gave one renderer process per site-instance sharing one
address space. DevTools' "JS Heap" is only the first line of the bill;
every memory incident is a story about *which* tank overflowed:

```
one renderer process (Chrome, 2024-era; about:blank -> heavy page)
+---------------------------------------------------------+
| V8 JS heap        ~1-3 MB floor (module 01 ch15);       |
|                   your objects + ch02's DOM wrappers    |
+---------------------------------------------------------+
| Blink / Oilpan     DOM nodes, style, layout (ch02)      |
+---------------------------------------------------------+
| Decoded images     4,000x3,000 px x 4 B = 48,000,000 B  |
|                   (~45.8 MiB) each; LRU, discard on     |
|                   pressure, re-decode on demand         |
+---------------------------------------------------------+
| Fonts              shaped-glyph runs, glyph atlases     |
+---------------------------------------------------------+
| Compositor layers  ch03's tile textures                 |
+---------------------------------------------------------+
| Network cache      RAM index over the disk cache        |
+---------------------------------------------------------+
| ArrayBuffers       backing stores OUTSIDE the V8 heap   |
|                   ("external memory")                   |
+---------------------------------------------------------+
```

The decoded image row deserves its arithmetic — the number one hidden
consumer on media-heavy pages: 4,000 × 3,000 =
12,000,000 px; RGBA is 4 B/px; 12,000,000 × 4 = 48,000,000 B =
45.8 MiB. A flat-surface JPEG can be ~500 KB on disk and 48 MB in RAM
(~96:1); a photographic one is 2-5 MB for the *same* 48 MB decoded —
compression shrinks the disk side only; pixels never. The policy: LRU
with discard-on-pressure — drop decoded pixels, re-decode on next
paint, trading CPU for RAM. The right trade, until a page decodes in
a scroll handler and it becomes jank.

The last row is the trapdoor: an `ArrayBuffer`'s backing store lives
outside the V8 heap, tracked as *external memory* — a page holding
500 MB in TypedArrays shows a small "JS Heap" while the renderer's
real footprint is enormous. When the numbers don't add up, this row is
why.

## The worker bill: parallelism, invoiced

Chapter 01 derived why 40 tabs are 40 processes. Workers invert it:
they are *threads you chose to spawn*, each paying a fixed entry fee.
A dedicated `Worker` is a thread (~1-2 MB stack) plus a full
V8 isolate: its own heap with the same 1-3 MB floor, its own handles,
its own copies of every script loaded into it — module 01 ch15's
per-isolate heaps make worker code safe to run concurrently: no shared
object graph, no locks, no data races. The bill is arithmetical:
5 workers for image processing cost ~10-15 MB before any pixel is
touched (5 × (isolate floor + stack + script copy)); 10 cost
~20-30 MB. Cheap next to 10 *processes* (ch01's ~30-50 MB each); not
free. Topology note: dedicated workers are threads *inside the
renderer process* — the process list doesn't grow, the renderer's
footprint does.

The exception proving the isolation rule is `SharedArrayBuffer`: one
backing store shared across isolates — module 09's shared memory,
now inside the sandbox. The price is the thread-safety contract
(`Atomics` for anything that must not tear) plus Chrome's cross-origin
isolation gate: SAB is exposed only to pages opting into COOP/COEP,
because high-resolution timers plus a shared cache were Spectre's fuel
(ch05 closes that loop).

The service worker is the same isolate math with opposite lifetime
politics. You may write it as a daemon; Chrome treats it as a pager.
It dies after ~30 s idle (Chrome 2024-era; the spec lets the browser
terminate it at any time) and restarts on the next `fetch`/`push`
event. Every start re-fetches the script and byte-compares it with the
cached copy; one differing byte installs a new version that *waits*,
and `skipWaiting()`/`clients.claim()` is how it takes over open pages.
Its `CacheStorage` is disk-backed, per-origin (partitioned by top-site
for third-party contexts — ch05), bounded by the quota
`navigator.storage.estimate()` reports — roughly up to ~60% of disk per
origin, best-effort eviction. Design lesson: state that must survive
the kill lives in CacheStorage/IndexedDB, never in JS variables —
forfeit every ~30 s.

## The leak taxonomy: six ways a page forgets

GC frees what is unreachable; every leak below is one extra reference
edge quietly keeping a graph reachable. Reproducer, symptom, fix:

**(1) The detached subtree.** Chapter 02 derived it fully — a removed
DOM subtree stays alive via the listener closure ↔ wrapper → node →
children chain (10,000 nodes × ~80 B ≈ 0.8 MB per instance, per
navigation):

```js
const detached = [];
function leak() {
  const root = document.createElement("div");
  for (let i = 0; i < 10000; i++) root.appendChild(document.createElement("span"));
  root.firstElementChild.addEventListener("click", () => count(root));
  document.body.appendChild(root);
  setTimeout(() => { document.body.removeChild(root); detached.push("gone"); }, 0);
} // removed from the DOM — retained by the closure. Fix: removeEventListener, or null the capture.
```

**(2) The closure-capture leak.** A long-lived callback captures a
whole scope to use one field:

```js
function onReport(report) {        // report: ~200 MB of parsed rows
  setInterval(() => paint(report.summary), 250); // captures ALL of report
}
// fix: const summary = report.summary; then capture `summary` (12 B), not the scope
```

Symptom: retained size concentrated in one closure; the retainer view
names the scope chain. The rule: *a closure captures variables, not
expressions* — `report.summary` in the body keeps `report` alive:
the variable is what the scope holds.

**(3) The expanding collection.** State that grows with navigation and
never shrinks — the listener-registry anti-pattern:

```js
const handlers = new Map();               // strong key, strong value
function onNavigate(page) {               // every SPA route change:
  handlers.set(page, makeHandler(page));  // the whole page graph enters the Map
}
// fix: if the key OBJECT should decide the entry's lifetime, use a WeakMap —
// the weak key makes the entry collectable when the key dies.
```

**(4) The un-cancelled timer/rAF.** A `setInterval` nobody clears is a
4 Hz CPU leak draining the battery. Browsers fight back: hidden tabs
get timers clamped to once per second; after 5 minutes hidden, chained
timers drop to about one wake per minute (Chrome's "intensive
throttling," Chrome 88+); `requestAnimationFrame` stops entirely in
hidden tabs — but the *retention* half (the callback's closure)
survives. Fix: `clearInterval`/`cancelAnimationFrame` on teardown,
pause on `visibilitychange`.

**(5) The console.log retainer.** Ch02's folklore as hygiene: an
object logged to the console is retained *while DevTools is open*.
Log a 200 MB object once, snapshot, and you "find" a leak that is your
own instrumentation. Close DevTools before measuring.

**(6) The observer-less leak.** A `ResizeObserver` keeps strong
references to every observed element, and its callback often captures
them too:

```js
const ro = new ResizeObserver(entries => relayout(entries[0].target));
document.querySelectorAll(".widget").forEach(el => ro.observe(el));
// teardown never calls ro.disconnect(): every .widget (and its subtree) lives forever
```

(The `MutationObserver` target link is weak by spec — but its queued
records and callback still capture nodes. `disconnect()` fixes both.)

## The formal method: three snapshots and a retainer walk

The taxonomy is recall; the method is procedure — the three-snapshot
technique as a repeatable workflow:

1. Memory panel → *Take heap snapshot* → Snapshot 1 (baseline).
2. Perform the suspected leaky action once.
3. Click the trash icon (force GC — you want
   *unreachable-but-retained* objects, not pending garbage).
4. Take Snapshot 2. Select it, switch the view from "Summary" to
   **Comparison**, baseline Snapshot 1, sort by *Size Delta*.
5. Walk the top grower's **Retainers** panel chain until it terminates
   in a scope, a listener, a Map, or a console log — that terminal is
   the diagnosis; each of the six leaks has a characteristic one.
6. Fix, re-run, confirm the delta collapses to ~0.

Ch02 defined the numbers: shallow size, retained size (what dies if
it dies — the dominator edge), distance. Sort by *retained size* —
leaks are dominators.

## WASM's linear memory: 10 GB of address space to kill one branch

From the JS side, a `WebAssembly.Memory` is one contiguous
`ArrayBuffer` — the *linear memory*. From the engine side, the
browser's sneakiest memory-model decision. Wasm32 rules: memory is
64 KiB pages; `memory.grow` adds pages; the cap is 65,536 pages =
4 GiB, because every address comes from a **32-bit index** that
cannot name anything past 4 GiB.

The classic cost: a bounds check — `index + offset + size <=
current_length` — a compare and branch per access. Module 01 ch03's
insight: the MMU already bounds-checks every access via the page
fault. V8's reservation trick makes that the design: on 64-bit with
the trap handler enabled (V8 2024-era), the engine reserves **10 GB of
address space per wasm memory up front** — the 4 GB linear region plus
a 6 GB guard, `PROT_NONE` beyond current length:

```
V8's reservation for ONE wasm32 memory (64-bit, trap handler on)
        10 GiB reserved virtual address space (commit only as it grows)

base (mem.buffer)                                     reserved end
|<------------- up to 4 GiB linear memory ------------>|<-- 6 GiB guard -->|
+------------------------------------------------------+-------------------+
| 64 KiB pages, mprotect'd writable on memory.grow     | everything else   |
|  TypedArray views start here                         | PROT_NONE         |
+------------------------------------------------------+-------------------+
        any access that computes an address HERE faults: SIGSEGV,
        which V8's trap handler converts to "memory access out of bounds"

worst-case effective address = base + index + offset + access
                              <= 0xFFFFFFFF + 0xFFFFFFFF + 16 B  (~8 GiB 8 B)
                              -> always inside the 10 GiB reservation
```

The guard size is *derived*, not chosen: the effective address is
`(u32)index + (u32)offset + access size`, and two u32s plus 16 B
cannot exceed ~8 GiB 8 B past base — so 4 GiB memory + 6 GiB guard
covers every address any wasm32 access can compute.
Result: **zero bounds-check branches** in generated code. An access
lands in committed memory or faults in the guard; the SIGSEGV
(Windows: VEH exception) is intercepted by V8's handler, which
verifies the faulting PC is registered wasm code and the address is
inside a wasm memory reservation, then converts it to a trap and
resumes at the landing pad. A wild crash in Blink's C++ fails those
checks and is forwarded. Hence wasm array-heavy code at native speed
with no per-access checks, and a reservation that is purely virtual:
RSS counts only touched pages.

The second truth: **wasm has no GC** (over linear memory) — it is
unmanaged bytes, and whatever C or Rust you compiled brings its own
allocator and lifetimes, verbatim from module 01 chapters 08-10: a
C-to-wasm leak is a malloc without a free; a Rust one is `unsafe` or
`mem::forget`. The change in flight is wasm-gc (struct/array reference
types collected by the host GC; Chrome 119 and Firefox 120, late
2023) — the Kotlin/Java-to-wasm path that replaces shipping your own
GC in linear memory.

## The boundary: where numbers become expensive

JS numbers are f64; wasm params are i32/f64/f32. Every crossing
converts (i32 via ToInt32 truncation), and multi-value returns get
boxed into fresh JS objects. The folklore is exact: the boundary is
where numbers become expensive — hot loops live on the wasm side, and
the workaround for "return a struct" is to *write results into linear
memory* and read them through one TypedArray view: one crossing
instead of eight.

The chapter's tying pattern — worker + SAB + wasm = the DSP pipeline:
the main thread creates a `SharedArrayBuffer` and `postMessage`s it to
workers (zero-copy — the handle travels, the bytes never duplicate);
each worker instantiates the same module *importing the shared memory*;
synchronization is `Atomics.wait/notify` (module 09's futex shape).
One shared linear memory, N isolates, no marshalling: the guard region
keeps it safe, cross-origin isolation (ch05) keeps it gated.

## What insiders know

- V8 reserves 10 GB of *virtual* address space per wasm memory (4 GB
  linear + 6 GB guard, V8 2024-era, 64-bit, trap handler on): `top`
  shows a terrifying VIRT; RSS counts touched pages. Without the
  trap handler, V8 emits explicit bounds-check branches — the slow
  path nobody benchmarks.
- `memory.grow` **detaches the old `ArrayBuffer`** even though the
  reservation means the base address never moved — detachment is a
  spec decision (stale views with wrong lengths), not an
  address-space necessity. Classic bug: the module grows once and
  every pre-existing TypedArray silently becomes `.byteLength === 0`.
- Big `ArrayBuffer`s live outside the V8 heap and count as *external
  memory*; V8 watches that counter when scheduling GCs — a page can
  trigger collections with zero JS-heap growth.
- Decoded images are the stealth budget: each visible 4,000×3,000
  image is ~45.8 MiB; under pressure the cache discards and re-decodes
  on demand — decode in a scroll handler and that policy becomes
  main-thread jank.
- Service worker lifetimes: ~30 s idle kill (Chrome, 2024-era),
  restart per event, script byte-compare on every start for updates.
  "Your SW is not a daemon, it's a pager."
- Timer throttling has exemptions: pages playing audio keep full-rate
  background timers — the hidden podcast tab keeps your 4 Hz poll
  alive. Intensive throttling (1 wake/min after 5 min hidden, Chrome
  88+) applies only to chained timers (5+ nesting levels).

## See it yourself

Everything runs in any Chrome/Chromium with DevTools; `Shift+Esc`
opens the task manager for process-level numbers.

**Lab 1 — the 48 MB image, measured.** Generate 12 MP locally, force
the decode, watch the footprint:

```js
// DevTools console, any page
const c = document.createElement("canvas");
c.width = 4000; c.height = 3000;
const blob = await new Promise(r => c.toBlob(r, "image/jpeg", 0.8)); // flat content: ~50-100 KB on disk
const img = new Image();
img.src = URL.createObjectURL(blob);
await img.decode();   // the 12,000,000 px x 4 B decode happens NOW
```

Shift+Esc before and after: the tab's *Memory footprint* steps up
roughly 45-48 MB at `decode()` (±a few MB — the cache may discard
opportunistically). Disk size and RAM size are unrelated.

**Lab 2 — the worker floor, measured.** Ten isolates, no work:

```js
for (let i = 0; i < 10; i++)
  new Worker(URL.createObjectURL(new Blob(["self.onmessage = () => {};"],
                                          { type: "text/javascript" })));
```

Watch the *same* renderer row: expect ~+20-30 MB (10 × (isolate floor
+ stack + script)). The process count in `chrome://process-internals`
does not change — workers are threads, not processes.

**Lab 3 — grow, read, and trap.** The `WebAssembly.Memory` API
directly:

```js
const m = new WebAssembly.Memory({ initial: 1, maximum: 2 }); // 64 KiB pages
new Uint8Array(m.buffer).length;  // 65536
m.grow(1);                        // 1  (returns the OLD page count; old buffer detaches)
m.buffer.byteLength;              // 131072
m.grow(1);                        // Uncaught RangeError: 2 + 1 > maximum 2
```

The trap message, via a 12-line module typed by hand (no toolchain):

```js
const bytes = new Uint8Array([0,97,115,109,1,0,0,0, 1,6,1,96,1,127,1,127,
  3,2,1,0, 5,3,1,0,1, 7,13,2, 3,111,111,98,0,0, 3,109,101,109,2,0,
  10,9,1, 7,0, 32,0, 40,2,0, 11]);
// module: (memory 1) (func (export "oob") (param i32) (result i32) (i32.load (local.get 0)))
const { instance } = await WebAssembly.instantiate(bytes);
instance.exports.oob(64);     // 0  — in bounds, memory is zeroed
instance.exports.oob(70000);  // Uncaught RuntimeError: memory access out of bounds
```

That `RuntimeError` is the guard region's SIGSEGV, caught and
re-labeled — 70,000 > 65,536, with no branch executed to check it.

**Lab 4 — the three-snapshot hunt.** Run leak (2) (the 200 MB
`setInterval` variant) through the six-step procedure from "The
formal method": the retainer walk ends at the interval callback's
scope; apply the fix, re-run, watch the delta collapse.

## How to use this knowledge

- **Memory incidents become graph problems.** "Chrome eats RAM"
  decomposes into the inventory: images (48 MB each — cap sizes,
  lazy-decode below the fold), workers (per-isolate floor — pool
  instead of spawn), or a retainer (snapshot, compare, walk).
- **API design:** registries keyed by objects that come and go are
  `WeakMap`s; observer-bearing components get teardown that actually
  calls `disconnect()`; long-lived callbacks get destructured scopes —
  code-review rules you can now *derive*, not memorize.
- **Wasm performance:** batch work on the wasm side, return results
  through linear memory, design growth up front — `maximum` in the
  constructor is what lets the engine keep the reservation.
- **Interview-grade answers:** derive the 6 GB guard from the two-u32
  arithmetic; explain why `memory.grow` detaches buffers though the
  base doesn't move; state the worker bill with numbers.
- **Battery/CPU triage:** a hidden tab burning CPU is an un-cancelled
timer that plays audio or hasn't hit the 5-minute throttling
threshold yet.

## Connections

- **Module 16 ch01** — the renderer process hosting this inventory;
  workers as threads vs. sites as processes.
- **Module 16 ch02** — Oilpan, the wrapper mechanism behind leak (1),
  the heap-snapshot numbers.
- **Module 16 ch03** — the compositor layers row, the rAF clock behind
  leak (4), where decoded images become textures.
- **Module 16 ch05** — the COOP/COEP gate SAB waits at; storage
  partitioning for Cache Storage.
- **Module 01 ch03** — the page fault as a branch, promoted here into
  the guard-region optimization.
- **Module 01 ch08-10** — C's malloc and Rust's ownership, verbatim
  inside wasm linear memory.
- **Module 01 ch15** — the V8 heap's floor and generations.
- **Module 09** — shared memory and `Atomics.wait/notify`, the SAB
  contract in the DSP pipeline.
- **Module 13 ch04** — zero-copy buffer transfer across a boundary;
  SAB `postMessage` is the same idea.

## Quiz

1. (Concept) Derive the minimum guard-region size that lets wasm32
   code skip per-access bounds checks. Which two wasm-level facts bound
   the worst-case effective address, and what must the runtime verify
   before converting a SIGSEGV into a trap, not a crash?

2. (Predict the number) A gallery page shows 8 images, each
   4,000×3,000, all decoded simultaneously in RGBA. How many bytes of
   decoded-image memory, exactly? What does the cache do under memory
   pressure, and at what cost?

3. (Predict the output) Exactly what does this print, and what is
   thrown (if anything)?

```js
const m = new WebAssembly.Memory({ initial: 2, maximum: 4 });
const view = new Uint8Array(m.buffer);
console.log(m.grow(1));
console.log(view.byteLength, m.buffer.byteLength);
try { m.grow(2); } catch (e) { console.log(e.constructor.name); }
```

4. (Predict the output) Using the hand-written module from the lab
   (`oob` is `(param i32) (result i32)` doing `i32.load` at the
   parameter, 1 page of memory): what do `oob(64)` and `oob(70000)`
   each produce, and what mechanism produced the second?

5. (Diagnose) This page's renderer grows ~200 MB per received
   message and never shrinks, even after the forced-GC trash click:

```js
socket.addEventListener("message", async e => {
  const report = JSON.parse(e.data);           // ~200 MB
  chart.setData(report.rows);
  setInterval(() => chart.tick(report.legend), 250);
});
```

   Name the leak (taxonomy number), the retainer-chain terminal the
   snapshot shows, and the two-part fix.

6. (Diagnose) A team refactors the expanding `Map` listener registry
   into a `WeakMap` — and every navigation now throws `TypeError:
   Invalid value used as weak key`. What did they key it with, why is
   that illegal, and the two honest resolutions?

7. (Diagnose) A user reports a tab spinning a laptop fan while
   *hidden*. The page runs an animation via `requestAnimationFrame`
   and a 250 ms `setInterval` poll. Which one burns the CPU in a
   hidden tab, what does Chrome's throttling do to it, and the fix?

8. (Cross-chapter) Module 01 ch03 called the page fault "a branch the
   MMU executes for you." Explain how V8's wasm trap handler turns a
   fault into a *resumable* trap — the two checks before conversion —
   and why the same mechanism cannot save a use-after-free in Blink's
   C++ heap (module 03's taxonomy).

<details><summary>Answers</summary>

1. **...** — Effective address = `base + (u32)index + (u32)offset + access size`. The two facts: wasm32 indices are 32-bit unsigned (max 0xFFFFFFFF) and static offsets are also u32 immediates, with accesses up to 16 B (SIMD). Worst case ≈ 0xFFFFFFFF + 0xFFFFFFFF + 16 ≈ 8 GiB 8 B past base, so 4 GiB usable + 6 GiB guard (10 GiB reserved) covers every computable address — no bounds-check branch is emitted. Before converting the SIGSEGV, the handler verifies the faulting PC is in registered wasm code (landing-pad registry) and the fault address is inside a wasm memory reservation; otherwise it forwards the signal as a real crash.

2. **...** — 8 × 4,000 × 3,000 × 4 B = 384,000,000 B ≈ 366 MiB. Under pressure the LRU discards decoded pixels (the JPEGs stay on disk); the cost is re-decode — CPU on the paint path, jank if it lands in a scroll handler.

3. **...** — Line 1 prints `2` (`grow` returns the OLD page count). Line 2 prints `0 196608`: the first `grow` detached the old buffer per spec (the base doesn't move; detachment is a spec decision), so `view.byteLength` is 0 while the fresh `m.buffer` is 3 × 65,536 = 196,608 B. Line 3 prints `RangeError`: the JS API throws when 3 + 2 exceeds `maximum: 4`; the wasm *instruction* would return -1 there instead of throwing.

4. **...** — `oob(64)` returns `0`: address 64 is in bounds and wasm memory is zero-initialized, so the little-endian i32 reads 0. `oob(70000)` throws `RuntimeError: memory access out of bounds`: 70,000 ≥ 65,536, so the load computed an address in the PROT_NONE guard, faulted, and the trap handler (PC is registered wasm code; address inside the reservation) converted it. No bounds-check branch executed.

5. **...** — Taxonomy (2) stacked with (4): every message installs a *new* 250 ms interval whose callback captures `report` — the whole 200 MB scope, not `report.legend` — and each interval is permanent, so the count compounds. The retainer walk terminates at the interval callback's scope, one closure per message. Fix: (a) `clearInterval` the previous poll (or create it once outside the handler), (b) destructure — `const legend = report.legend;` — so the closure captures 12 bytes. The delta then collapses to ~0.

6. **...** — They keyed the WeakMap with the page/route *string* (or an id). Weak keys must be objects — collectable identities; a string has no lifetime to be weak over — so `set` throws `TypeError: Invalid value used as weak key`. Resolution 1: key by the page object itself, so entries die with the page. Resolution 2: for a primitive key, keep a Map and delete explicitly on teardown; "entry dies with key" requires an object whose reachability can end.

7. **...** — The `setInterval`. `requestAnimationFrame` never fires in a hidden tab — the compositor stops producing frames, so the callback stops being called (its closure is retained but costs no CPU). The 250 ms poll is clamped to once per second when hidden; after 5 minutes hidden, intensive throttling (Chrome 88+) limits chained timers to about one wake per minute — real but bounded, unless the page plays audio (exempt). Fix: `visibilitychange` → `clearInterval` when hidden, restart when visible; a poll nobody reads is leak (4).

8. **...** — The handler looks up the faulting PC in V8's registry of trap-handler-protected wasm code (the fault is a *wasm-issued load/store*, not arbitrary native code) and checks the fault address against the wasm memory reservations (inside the guard = the computed OOB case). If both pass, it resumes at the landing pad for that load, raising the trap into managed code. A C++ use-after-free fails the first check by construction — the faulting instruction is Blink's compiled C++, not registered wasm code — so the handler forwards the signal and the renderer crashes (ch01's sad tab). Only wasm's controlled address arithmetic makes the outcome a catchable trap rather than memory corruption (module 03: the fault is the symptom, the bug is the lifetime).

</details>
