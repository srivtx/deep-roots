# Module 16 — Browser engine internals

> The browser is the operating system that won. It runs untrusted code
> from the internet at 60 frames per second, in a sandbox, with three
> heaps per tab (V8's, the DOM's, and the rasterizer's GPU textures) —
> and its internals are everything this program has already derived,
> at web scale: the DOM is a refcounted object graph with a cycle
> problem, the rendering pipeline is a memory-bandwidth problem, the
> sandbox is seccomp + a broker (module 15's machinery), and site
> isolation is Spectre's answer (module 02 ch07's side channels). An
> OS that renders.

**5 chapters · one final exam · prerequisite: module 01 chapters 15
(V8's heap — extended), 03 (page faults, COW); module 02 ch07 (Spectre
— reused); module 13 ch04 (epoll/IPC patterns — referenced); module
15 ch04 (seccomp, capabilities — reused).**

## How to study it

Chapter 01 is the process model (the browser as OS, the sandbox); 02
is the DOM as a heap graph and the two-collector problem (Oilpan + V8);
03 is the rendering pipeline as memory traffic (60 fps arithmetic,
layout thrashing); 04 is the page's memory (leak taxonomy, workers,
WASM); 05 is the security boundary (SOP as isolation policy, site
isolation, Spectre's browser chapter). Labs: any Chrome/Chromium +
DevTools (the most reproducible labs in the whole program — you have
the instrument).

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The browser as an operating system](chapters/01-browser-os.md) | Process-per-site, the broker sandbox, Mojo — why 40 tabs are 40 processes. |
| 02 | [The DOM as a heap graph: Oilpan and two collectors](chapters/02-dom-oilpan.md) | Nodes, wrappers, tracing — the classic detached-node leak derived. |
| 03 | [The rendering pipeline as memory traffic](chapters/03-render-pipeline.md) | Style → layout → paint → composite, the 16.6 ms budget, layout thrashing. |
| 04 | [The page's memory: leaks, workers, WASM](chapters/04-page-memory.md) | The three heaps, the leak taxonomy, guard-page WASM — DevTools as profiler. |
| 05 | [The browser as a security boundary](chapters/05-browser-security.md) | SOP, site isolation, Spectre in the renderer, the two-bug escape rule. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the browser as an operating system.** The evolution told
as an OS's history: the single-process era (one C++ process, one crash
kills everything — tabs as threads; the 2006-2008 pain that motivated
Chrome's manifesto), process-per-tab (2008: the fault isolation win
(c10% of crashes per tab: reload, don't die), the memory cost (each
process pays its own floor: a minimal renderer ~30-50 MB private —
derive "why 40 tabs eat 2 GB"), site isolation (2018-2019, Chrome
67-77: the Spectre response — *cross-site documents get different
processes even in the same tab*; the RAM cost honestly stated (+10-20%
for typical browsing), why it's worth it (ch05's payoff), the "process
per site-instance, not per tab" precision (iframes of other origins
moved out-of-process: OOPIF). The process zoo drawn as a diagram:
the browser process (the broker: owns the powers — UI, disk, network,
privilege), renderer processes (untrusted: the DOM, V8, style/layout —
seccomp'd, no disk, no sockets), the GPU process (a *separate*
sandboxed process — why: kernel GPU drivers are unsecurable (the
historical CVE density), so the dangerous code gets its own crash
domain and the renderers talk to it via IPC), utility/network service
processes (the service-ification split: each power = its own process
with its own seccomp profile — the "least privilege as a process
topology" pattern), extension processes (full-power-ish, the different
threat). The IPC fabric: Mojo (message pipes over a shared channel,
the IDL, the capabilities-as-handles model: "you can only talk to
someone if someone gave you their pipe" — the object-capability model
in production), the shared-memory buffers in the hot paths (the
compositor's tiles: the raster result is passed to the GPU process as
a shared buffer, not a message copy — module 13 ch04's zero-copy idea
inside one machine), the V8 isolate-per-renderer (each renderer's JS
heaps are isolated *by the process boundary* — the same guarantee
module 15 gave VMs, on the JS object level). The per-tab memory
anatomy broken down (the ~30-50 MB floor: Blink + V8 + ICU + skia
code pages, the per-page data: JS heap + DOM + style + images +
compositor layers — the task manager's columns mapped to that
anatomy). Insiders: the crash taxonomy (renderer crash = sad tab;
browser process crash = everything; GPU process crash = visual glitch
then auto-restart — the "why did my screen go black and come back"
answer), the process-count arithmetic (`chrome://process-internals`:
how many processes per tab under site isolation — the "one Gmail tab =
6 processes" decode), the "Chrome eats RAM" honest defense (the memory
is the sandbox's bill — fault isolation costs address space), the
Zygote (Linux Chrome's fork-server: a pre-warmed, pre-sandboxed
template process that forks renderers on demand — ASLR *within* a
zygote family is the trade (the fork inherits page layout: the
mitigation is a per-fork re-randomization... the insider history of
why Chrome later grew namespace-randomization), and the Windows/macOS
equivalents in one line each. Lab: Shift-Esc (Chrome's task manager:
sort by memory, the process types decoded per row), `chrome://sandbox`
(the seccomp layer list per process type — on Linux: "Renderer Sandbox:
Layer 1 Seccomp-BPF" etc.), `chrome://process-internals` (the process
tree), `ps aux | grep -c chrome` from the OS side, about:blank vs a
real site's renderer memory compared in the task manager.

**Chapter 02 — the DOM as a heap graph: Oilpan and two collectors.**
The DOM as C++ objects: the Node hierarchy (Node → Element →
HTMLElement → HTMLDivElement — the inheritance chain as a memory cost:
each level's fields; a Node's pointers: parent, first/last/next/prev
child + sibling links + the document back-pointer + the layout
association (the "attached" state: a node in the render tree) —
~40-100+ bytes per bare node, more with attributes (the attribute map:
a hash), and the namespace/type (the nodeType switch as the classic
visitor pattern). The ownership problem stated: who deletes a node?
The C++ world's answers, all bad alone: manual (the 1990s IE leaks),
refcounting (the DOM's parent↔child + listener↔element cycles: a cycle
drawn and walked — the reference count never hits zero, the leak is
*structural*), so: tracing. Oilpan as Blink's tracing GC: the heap is
divided into pages of fixed-size "buckets" (the typed arena idea:
same-size objects cohabit — module 01 ch19's size-class allocator
recognition, explicit), the collector marks from roots (Persistent
pointers — the stack/handles that outlive collections; Member
pointers — in-graph references that the tracer follows; WeakMember —
the weak refs), the **write barrier** (the collector's contract with
mutators: incremental marking means an old-to-young pointer write must
be recorded (the remembered set) or a live object gets missed — module
01 ch15's V8 write barrier and module 10 ch04's compiler-inserted
barriers return; in Blink this arrives as macro-heavy code (the
Trace() method every class writes — show one), the concurrent +
incremental phases (the 2018 "Blink GC went concurrent" project — the
pause-time story: full GCs from ~100 ms pauses to ~ms-class on heavy
pages). The **two-collector problem** (the chapter's core): V8 has its
own GC (module 01 ch15's generational scavenger) *and* Blink has
Oilpan — two heaps, one address space, sharing objects by reference
*through the wrapper*: the JS-visible DOM object is a **wrapper** (a
V8 object holding a pointer to the C++ node, and the node holding a
back "wrapper" ref), and each collector can only trace its own side:
V8 marks the wrapper as JS-reachable → the wrapper is a *root* into
Blink (if JS holds it, the node lives) — the V8 embedder-tracing API
(`EmbedderHeapTracer`: V8's marker asks Blink "is this wrapper
reachable?" and Blink traces from it into Oilpan's heap); the failure
mode that this machinery exists to kill: **the detached-node leak**
written out in code (create 10,000 nodes, attach one event listener
whose closure references the parent, removeChild the subtree — the
subtree is detached but the listener (a JS closure) ↔ wrapper → node
→ children chain keeps *all of it* alive: the code, the fix (remove
the listener or null the closure's capture), and the scale (10k ×
~80 B = ~1 MB per leak instance, multiplied per navigation) — THE
canonical web leak, derived not asserted). The wrapper lifecycle
(TODO: the wrapper cache (the node → wrapper map kept so re-wrapping
returns the same object: `div === div` — the identity guarantee; the
wrapper as a weak ref on the C++ side (if JS forgets it, the node can
die — the "wrappers don't leak nodes" fix of the modern era) + the
"wrapper disposes node" path (the node's last owner is the wrapper →
V8 collects → Oilpan follows). The diagnosis instrument: DevTools'
heap snapshot (the three numbers: shallow size (the object's own
bytes), retained size (what dies if it dies — the dominator tree),
distance (the shortest root path); the "Detached" nodes view (the
snapshot filter that names detached subtrees: their retainers *named*
— the listener closure shown); the three-snapshot technique (take
before / do the leaky thing / take after / compare: the delta view)
— this is the lab. Insiders: `console.log` retainer folklore (an
object logged to the console is retained *while devtools is open* —
the false leak that ate a debugging afternoon), the WeakRef /
FinalizationRegistry modern escape hatches (what they're for (cache
entries) and why the finalizer's timing is a lie (the GC may never
run: an idle page keeps memory forever — the "the finalizer didn't
fire for 20 minutes" story)), `performance.measureMemory` (the
Chrome-only, cross-origin-isolated API (ch05 connection)). Lab: a
leak page built inline (the HTML+JS in a code block: create/detach
with a capturing listener; the fix variant), the DevTools workflow
performed (heap snapshot → filter "Detached" → the retainer chain
read → fix → re-snapshot: the numbers before/after), the wrapper
identity check in console (`document.body === document.body`, the
"why" as the wrapper cache).

**Chapter 03 — the rendering pipeline as memory traffic.** The frame
pipeline drawn as the three-lane diagram (the main thread: JS → style
→ layout → paint-record; the compositor thread: raster-decision +
scroll/composite; the GPU process: raster + draw) — the lanes have
different clocks and that *is* the 60 fps story. Style: the computed
style per element (the cascade resolved to concrete values; the match
cost: the descendant selectors (`.a .b .c` — the right-to-left match,
why the middle selector is the expensive one: the count of candidate
elements × per-candidate work — the insider's "browsers match
right-to-left" and its cost arithmetic), the invalidation sets (what
Chrome precomputes so a class change doesn't restyle the world: the
"changed A → must restyle B" map — the reason big stylesheets need
discipline), style sharing (the cache of identical computed styles).
Layout: the box tree (the layout objects: block, inline, flex, grid —
each a constraint solver (the flex layout as a two-pass distribute:
the grow factors → the free space; the intrinsic sizing
(max-content/min-content: the text measurement — the "layout must
measure text" fact that makes it I/O-adjacent (the font cache, the
shape caching)), the layout invalidation as subtree-flags (dirty bits
up the tree; the relayout cost = O(dirty subtree) — the containment
argument (contain: layout — the manual subtree wall). Paint &
composite: the display list (the *recorded* draw ops — a display item
is a flat command ("draw rect at x,y w,h color"): the record happens
once per damage; the replay is cheap and thread-safe — the design
reason: record on the main thread, replay/submit from the compositor
*without the DOM* (the main thread can be busy and scrolling still
works — the insider fact every user knows but can't name: "the page
scrolls while the JS is blocked"), the layer tree (what gets its own
composited layer: transforms, video, position:fixed; the GPU texture
per layer: the memory arithmetic — a 1920×1080 RGBA layer = 8.3 MB
(every layer, every frame's worth of buffers (the front/back + the
swap chain): a "too many layers" page can hold *hundreds of MB* of
GPU memory — the `will-change: transform` abuse story (each hinted
element gets a layer: 20 hinted cards × 8.3 MB = 166 MB — the number
that explains the regression), tiling (the layer cut into 256×256 or
512×512 tiles: the raster unit, rastered lazily/nearby (the priority
by viewport distance), in the GPU process (the shared-memory handoff
from ch01)). The 16.6 ms budget arithmetic (60 fps): the split (the
main thread's ~10 ms (JS 5 + style 1 + layout 2 + paint-record 1 +
the commit), the compositor's ~2, raster+GPU the rest, with the 1 ms
vsync slack; the "dropped frame" definition (a frame that missed its
vsync deadline: the *next* vsync shows the old frame — the user sees
33 ms, the jank); the requestAnimationFrame contract (run before
style/layout of the *same* frame: the DOM-read-after-JS-write
pipeline order that makes rAF the only right place for animation
JS). **Layout thrashing** derived (the chapter's payoff): the layout
is *lazy* (dirty flags; the answer to a geometry read
(offsetHeight/getBoundingClientRect) must be *current* → if the DOM
is dirty, the read forces a **synchronous layout NOW** — the
alternating write/read/write/read loop (the classic: 1,000
iterations of `el.style.width = ...; total += el.offsetWidth;` =
1,000 forced layouts: the profile shows the pink "Recalculate Style /
Layout" towers; the fix: batch all reads, then all writes (the
read-write interleaving law), the fast-path (the "layout clean" check
— the browser's own dirty bit); the numbers: the loop measured (the
thrashing version vs the batched version: typically 10-100× — the
inline benchmark), ResizeObserver's batched callback model (the
modern API designed around this law). The invalidation economics
(the small-edit-fast page): the damage rect propagation (the paint
limited to the dirty tiles; the compositor unaffected) — the "why a
blinking cursor is free but a blinking background is not" (the former
invalidates 2 tiles; the latter, everything). content-visibility:
auto (the skip-everything primitive: the offscreen subtree skips
layout+paint+style entirely until near-viewport — the "render only
what's visible" as an explicit contract; the contain-intrinsic-size
placeholder to stop scrollbar jumps). Lab: the DevTools Performance
panel read (record a scroll: the three lanes visible (the main
thread's blocks color-coded: yellow script / purple style+layout /
green paint; the raster lanes below; the long-task signature), the
thrash reproducer (the code inline: the interleaved vs batched loop
timed with performance.now, the 10-100× measured live), the layer
count (the Layers panel: the layer list + the memory column, the
will-change demo: add it to 20 elements, watch the layer/memory
count), the "scroll while blocked" demo (a 5 s busy loop on the main
thread + the page still scrolls — the compositor thread proven).

**Chapter 04 — the page's memory: leaks, workers, WASM.** The anatomy
of a page's memory (the whole inventory, since "the JS heap" is only
the start): the V8 heap (module 01 ch15's scavenger/old-space; the
per-heap floor ~1-3 MB before your first object), the Blink/Oilpan
heap (the DOM + the C++ side, ch02), the images (the decoded cache:
a 4,000×3,000 JPEG is ~500 KB on disk but **48 MB decoded** (RGBA 4
B/px: 12 M px × 4 — the decoded-image cache is the #1 hidden memory
consumer of media-heavy pages; the lifetime policy: the cache's LRU
with the discard-on-pressure; the "the browser re-decodes and
re-stores what the file system already had" trade stated), the fonts
(the shaped-glyph caches), the compositor layers (ch03's textures),
the network cache (the disk cache's index in RAM), the workers (each
dedicated Worker = its own V8 isolate = its own heap floor + its own
thread (~1-2 MB stack) + its own copies of loaded scripts: the "5
workers to parallelize image processing" bill: ~10-15 MB before any
work; the SharedArrayBuffer exception (module 09's shared-memory
returns: one buffer shared across isolates — the thread-safety
contract (Atomics) that made the feature wait for cross-origin
isolation (ch05)), the service worker (the long-lived "daemon" with
its own lifetime politics: the browser *kills it on idle* (~30 s) and
restarts on events (the "your SW is not a daemon, it's a pager" rule;
the Cache Storage it owns (the offline assets: the disk quota
story), the update cycle (the byte-compare; skipWaiting/clients.claim
dance), the storage partitioning note (ch05). The leak taxonomy (the
chapter's practical core — each with the reproducer code, the
symptom, and the fix): (1) the detached subtree (ch02's leak, one
line back-reference), (2) the closure-capture leak (a long-lived
callback capturing a huge scope for one small field — the fix
(destructure the needed fields), the inspector's retainer view
naming the scope chain), (3) the expanding collection (the Map that
grows with each navigation (the listener registry anti-pattern; the
WeakMap fix — the weak key makes the entry collectable), (4) the
un-cancelled timer/rAF (the 60 Hz leak that eats the battery (the
tab's CPU even in background — the browser's throttling
(intensive throttling after 5 min) as the mitigation), (5) the
console.log retainer (ch02's folklore, restated as a diagnosis
hygiene rule), (6) the observer-less leak (the MutationObserver /
ResizeObserver never disconnected). The three-snapshot technique as
the formal method (before / action / after / the comparison view's
delta; the retained-size sort + the retainer walk — the full
DevTools workflow written as a repeatable procedure). WASM's linear
memory model (the chapter's deep end): one contiguous ArrayBuffer per
instance (growable via memory.grow: the 4 GB wasm32 cap; the growth
mechanism in V8: the reservation trick — V8 **reserves 8-10 GB of
address space per wasm memory up front**: the 4 GB linear region plus
a *guard region*; the payoff derived: bounds checks eliminated —
any 32-bit index + length either lands inside the linear memory or
faults in the guard (a SIGSEGV the runtime converts to a wasm trap)
— module 01 ch03's "the page fault as a branch" insight, used as a
compiler optimization (the insider: this is why wasm array code is
fast without per-access checks), the "wasm has no GC" truth (linear
memory is *unmanaged* — C/Rust own it (their malloc, their
lifetimes: module 01 ch08-10's models apply verbatim); the GC
proposal (wasm-gc: struct/array reference types natively — the
Kotlin/Java-to-wasm path) as the change in flight; the JS↔wasm
boundary (the numeric conversions at the boundary (the i32/f64
marshalling — the "the boundary is where numbers become expensive"
 folklore with the struct-return workaround), the SharedArrayBuffer
postMessage (zero-copy shared linear memory between threads — the
"worker + SAB + wasm = the DSP pipeline" pattern). Lab: the image
arithmetic done live (load a 4,000×3,000 image, watch the memory
panel's "Image cache" or the JS heap jump ~48 MB: the measured
proof), the worker-count experiment (spawn 10 workers, the task
manager's per-renderer memory + the process list: the floor
measured), a WASM memory.grow demo (a tiny Rust/AssemblyScript-free
route: the wat text format inline or a Python-compilled... the
honest lab: the browser's `WebAssembly.Memory` API directly (grow it,
read it, catch the OOM), the guard-region claim checked via a
deliberate out-of-bounds access (the trap message caught: "memory
access out of bounds"), the three-snapshot leak hunt (the full
procedure performed on the leaking example from ch02).

**Chapter 05 — the browser as a security boundary.** The same-origin
policy as *memory isolation policy* (the reframe: SOP is the OS-level
permission bit of the web; an origin = scheme + host + port (the
precise tuple; the edge cases that historically broke it (the
document.domain relaxation (two sites sharing a parent domain could
declare themselves same-origin: the escape hatch that became the
hole — killed in Chrome 120-era (the deprecation story as a
case study: a legacy feature's cost), the file:// null origin, the
blob: inheritance rules), what SOP actually restricts (the DOM
access (the cross-origin window: only a whitelist of properties
(location-ish, a few length/count properties), the reads that throw
vs the "silently empty" (the cross-origin frame's document is
*blocked* but its contentDocument access returns null/throws
depending on the path — the enforcement point is the *access check*
in the property getter: the browser engine doing a runtime origin
check on a property read — the mechanism shown), the network reads
(the fetch: CORS as the opt-out protocol (the preflight for
non-simple requests (the Content-Type/Preflight rules), the
Access-Control-Allow-Origin handshake (the credentials wildcard
hole: `*` never matches credentialed requests — the insider rule
that bites every beginner), the storage isolation (localStorage,
IndexedDB, cache: keyed by origin — the "storage is a map keyed by
origin" statement; the *third-party partitioning* modernization (the
double-keying (site, top-site): the end of the shared third-party
cookie jar — CHIPS and the storage partitioning (the "the
third-party iframe sees a different storage world per top-site"
model — the anti-tracking driver (the practical payoff: cross-site
tracking via shared localStorage is dead), postMessage as the
controlled channel (the origin check on receive — the code pattern
with and without the check (the missing-check bug as the classic
finding). Site isolation as Spectre's answer (module 02 ch07
returns, full circle): the attack recapped in 5 lines (the
speculative cache side channel reads cross-origin data from a
shared cache: the renderer *holds* other origins' data in-process
(the same process = same L1/L2 — the 2018 insight: SOP was a
language-level check while the *hardware* shared the data), the
defense hierarchy: (1) process isolation (the full site-isolation
project: the cross-origin document never in this process (the
OOPIF machinery — an iframe's content rendered by a *different
process*, the pixels composited across: the "the iframe's pixels
arrive by IPC" fact — the engineering cost of that fact: +10-20%
RAM, the frame tree split (the browser process owns the frame tree,
renderers own their pieces), (2) the timer resolution clamps (the
high-resolution clock was the side channel's stopwatch:
performance.now clamped to 100 µs without cross-origin isolation
(the precise number), finer (5 µs) inside a crossOriginIsolated
context), (3) the cross-origin isolation gate (COOP:
Cross-Origin-Opener-Policy (the window opener relationship severed:
`same-origin` — the popup's reference severed, blocking the
Spectre-via-opener path), COEP (Cross-Origin-Embedder-Policy: the
*loaded document* promises all its subresources are
CORP-protected... the model drawn: COEP requires CORP on every
subresource (the resource says "I'm cross-origin-readable"), the
reward: the page may use SharedArrayBuffer + high-res timers (the
features gated behind the isolation — the "SAB waits at the gate"
story: why the hottest demo of 2017 was behind a flag for 4 years),
(4) the Spectre mitigations in the renderer (the branch target
logic, the cache partitioning (Chrome's per-site cache keys — the
"your cache lines are partitioned by origin" hardware-adjacent
mitigation)). The sandbox's syscall reality (module 15 ch04's
machinery, now with the browser's specifics): the renderer's
seccomp-bpf profile (no open... the file access is brokered: the
renderer *asks the browser process* (the broker pattern: the
privileged parent does the syscall, the untrusted child the IPC —
the "the renderer can't even open a file" enforcement), the Windows
model (the restricted token: the renderer's token stripped of
rights (the integrity levels: the renderer at "Untrusted" IL), the
macOS model (the sandbox profiles), the GPU process's different
profile (it needs device access — the looser sandbox of the
least-lost-cause code), the extension processes as the sad outlier
(the historical power-grab: the APIs they get (tabs, storage, all
URLs) vs the MV3 tightening (the manifest v3 story as a sandbox
tightening arc: the remote code ban, the service-worker-ification —
the insider's framing). The two-bug rule (the escape economics: a
renderer pwn2own chain = bug 1 (the renderer: the RCE — module
02/03's bugs, a use-after-free in the parser, a JIT miscompile
(module 10 ch03's UB license), bug 2 (the sandbox escape: the broker
or the IPC surface or the kernel), the "one bug gets you the
renderer, two get you the user" sentence as the mental model; the
Mojo API surface as the *real* perimeter (the IDL count (hundreds
of interfaces) — the fuzzing effort (the IPC fuzzers, the clusterfuzz
story in two sentences). Insiders: the process-per-site vs
process-per-site-instance (the "site + profile + browsing context"
precision: two tabs to the same site *can* share a renderer (the
same-origin tabs share — the reasoning (memory vs isolation), the
`--process-per-site` historical flags, the cross-origin opener
policy's window.opener postMessage hole (the "legacy window.open
handshake" folklore). Lab: `crossOriginIsolated` in the console
(false on a normal page — the gate observed), the COOP/COEP demo
(the headers on a test page (a tiny Python http server with the
headers; SAB appears), the Sec-Fetch-* headers observed in the
DevTools network tab (the metadata the browser sends on *every*
request now — the origin-relationship declared to the server: the
Sec-Fetch-Site: cross-origin line decoded), the postMessage origin
check demo (the code with/without), the timer resolution measured
(loop performance.now diffs: the clamp seen as a histogram of
deltas — the 100 µs granularity visible on a non-isolated page).

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **Performance debugging that isn't superstition**: the 16.6 ms
  budget, the thrash law, the layer memory arithmetic — you can read
  a Performance trace and *name* the problem.
- **Memory forensics**: the three-snapshot technique, the retainer
  walk, the leak taxonomy — the page's memory becomes a graph you
  can reason about (module 01's skill, at DOM scale).
- **Security architecture literacy**: SOP as a memory model, site
  isolation as Spectre's bill, the two-bug rule — you can evaluate
  web security claims instead of repeating them.
- **The last tower level**: images, layers, wasm linear memory — the
  browser's heaps mapped onto the memory-model table of module 01.
