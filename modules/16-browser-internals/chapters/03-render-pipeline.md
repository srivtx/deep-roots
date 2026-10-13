# Chapter 03 — The rendering pipeline as memory traffic

> **The 30-second version.** A 60 Hz display gives the browser 16.67 ms per
> frame, split across three lanes with three clocks: the main thread (JS,
> style, layout, paint-recording — roughly 10 ms of the budget), a
> compositor thread inside the renderer (scrolling, tile decisions —
> ~2 ms), and the GPU process (raster and the final draw — the remaining
> ~3.6 ms, with ~1 ms of vsync slack). Every stage is memory traffic: the
> DOM graph becomes computed styles (one per element), then a box tree
> (geometry in bytes), then a display list (recorded draw commands), then
> GPU textures (a 1920×1080 RGBA layer is 2,073,600 px × 4 B = 8,294,400
> bytes ≈ 8.3 MB). The load-bearing trick is that layout is *lazy* — a
> write only sets a dirty bit — which makes reads dangerous: reading
> geometry after a write forces a synchronous layout before the read can
> answer, and a write/read loop of 1,000 iterations forces up to 1,000
> layouts. That is layout thrashing, its measured cost is typically
> 10–100×, and the fix is one law: batch all reads, then all writes.

## Three lanes, three clocks

Start from hardware. The display refreshes on a vertical synchronization
signal — vsync — every 16.67 ms at 60 Hz (8.33 ms at 120 Hz; Blink follows
the display's clock, not the other way around). A *frame* is all the work
that fits between two vsyncs. A *dropped frame* misses its deadline:
nothing new is ready to swap in, the old frame is scanned out again, and
the user sees 33.3 ms of stale content instead of 16.67 ms — jank. That
deadline is why the pipeline has its shape.

The work between two vsyncs divides across three lanes (Chrome/Blink,
2024-era):

```text
   vsync (every 16.67 ms at 60 Hz) -- the metronome all three lanes answer to
        |
        v
+------------------------------------------------------------------+
| LANE 1: MAIN THREAD (renderer process, the JS event loop)        |
|                                                      ~10 ms share |
|   input events -> JS + rAF -> style -> layout -> paint-record    |
|                     ~5 ms       ~1 ms    ~2 ms     ~1 ms +commit |
|                                                                  |
|   outputs: ComputedStyle (one per element), the box tree,        |
|   the display list (recorded draw items), the layer tree         |
+------------------------------------------------------------------+
        |  commit: display list + property trees + layer tree
        v
+------------------------------------------------------------------+
| LANE 2: COMPOSITOR THREAD (same renderer process)    ~2 ms share |
|                                                                  |
|   applies scroll/transform updates from input, picks tile        |
|   priorities (raster decisions by viewport distance), emits      |
|   draw quads -- runs on Begin Frame messages from vsync,        |
|   NOT on the main thread's event loop                            |
+------------------------------------------------------------------+
        |  quads + raster commands (in the software-raster path the
        |  pixel tiles cross this boundary in shared memory buffers
        |  -- the ch01 process-model machinery)
        v
+------------------------------------------------------------------+
| LANE 3: GPU PROCESS (one process, shared by every tab)           |
|                                                                  |
|   raster: fills 256x256 / 512x512 tiles (Blink 2024-era;         |
|   sizes vary with DPI) -> display compositor (Viz) ->            |
|   swap buffers at the next vsync                                 |
+------------------------------------------------------------------+
        |
        v
   screen: a new frame this vsync -- or the old frame again
   (the user sees 33.3 ms of the same picture = jank)
```

The teaching split of the 16.67 ms budget (a rule of thumb; real pages
skew it in any direction): the main thread's ~10 ms is JS ~5, style ~1,
layout ~2, paint-record ~1, plus the commit; the compositor ~2; raster
and the GPU process the rest (~3.6); ~1 ms of slack absorbs vsync
jitter. The lanes have *different clocks*: the main thread runs whatever
the event loop hands it (a 5 s busy loop is 5 s of that clock), while
the compositor and the GPU process run once per Begin Frame — per vsync
— regardless of the main thread. That clock independence *is* the 60 fps
story: a frame can composite on schedule even while its producer is
stuck.

One contract follows from the lane order and it places animation JS.
Within one frame: **input events → rAF callbacks → style → layout →
paint-record → commit.** Code in a `requestAnimationFrame` callback runs
*before* the style/layout passes of the same frame, so a `style.width`
write inside rAF is consumed by this frame's layout — no extra frame of
latency. The same write in a `setTimeout` runs whenever the task is
scheduled, possibly after this frame's layout, adding a frame of latency
and interleaving with the pipeline's own reads. That is why rAF is the
only right place for per-frame animation JS: it is the one callback that
runs at the correct point in the frame's stage order.

## From nodes to boxes: style and layout as pure computation

Chapter 02 left us with a DOM graph of nodes. This lane turns it into
geometry in two stages, both of them computation over memory-resident
objects.

**Style.** The cascade resolves, per element, a *computed style*: every
property pinned to a concrete value (`color: red` → `rgb(255, 0, 0)`;
`font-size: 2em` → pixels after walking the parent chain's font sizes).
The expensive part is deciding *which rules apply* — selector matching —
and browsers match **right to left**: given `.a .b .c`, the engine first
asks "are you a `.c`?", then walks the ancestor chain for a `.b`, then
up for an `.a`. The cost arithmetic insiders use: matching cost ≈
(elements matching the rightmost selector) × (ancestor nodes walked
before the middle selector fails). The *middle* selector is the
multiplier because it fails deep: a page with 5,000 `.label` elements
and 300-deep ancestor chains does ~1.5 million ancestor visits for
`.list .row .label` — versus 5,000 hash lookups for plain `.label`.
Same page, same elements, ~300× the work, purely from selector order.

Chrome does not restyle the world on every change: at stylesheet parse
time Blink precomputes **invalidation sets** — for each class or
attribute, a map of "if this changes, these descendant/sibling
selectors must restyle." A class change then re-styles the mapped
subset, not the document — which is why big stylesheets need
discipline: 10,000+ rules can make the map broad enough that one class
flip fans out over most of the page. **Style sharing** lets adjacent
siblings with identical matched rules point at one shared ComputedStyle
object instead of carrying copies (Blink 2024-era; sibling insertions
invalidate the cache). Computed styles are memory traffic literally —
each distinct one is a heap object of hundreds of bytes, and 50,000
elements with no sharing hold tens of MB of them.

**Layout.** Surviving elements get layout objects — the **box tree**:
block, inline, flex, grid (Blink's LayoutBlockFlow, LayoutInline,
LayoutFlexibleBox, LayoutGrid). Each flavor is a constraint solver:
given available width, produce position and size. Flex is a clean
two-pass solve: pass one computes each child's base size and grow
factor; pass two distributes the free space (leftover or deficit)
proportionally to those factors. Grid is the same idea with rows and
columns.

The catch that makes layout unlike arithmetic: **layout must measure
text**. `max-content` is the widest unbreakable run of the text,
`min-content` the widest single word — both require the font machinery
to *shape* the string (map code points to positioned glyphs) before
answering. That makes layout I/O-adjacent: fonts load asynchronously and
force re-measure on arrival, and shaping is only affordable because
Blink caches shaped runs (the ShapeResult cache, keyed on font + text)
and reuses them across layouts.

Layout's invalidation is subtree-scoped, like ch02's reachability:
writes set **dirty bits that propagate up the tree**, and relayout cost
is O(dirty subtree) — a 12-node change is microseconds, a dirty
`<body>` is milliseconds. `contain: layout` is the manual wall: a
promise that "this subtree's layout does not depend on anything outside
it," so dirtiness cannot cross the boundary in either direction —
containment hands the browser a proof it would otherwise recompute.

## From boxes to textures: record once, replay without the DOM

Paint, in this pipeline, is not drawing — it is **recording**. The main
thread walks the box tree and emits a flat **display list** of display
items, each a self-contained command like "draw a rect at (x, y, w, h)
in color C" or "draw these shaped glyphs at (x, y)." Two properties make
this the pivot of the whole 60 fps story:

1. **Recording happens once per damage.** If nothing changed, the old
   display list stands.
2. **Replay is cheap and thread-safe.** A display list is immutable
   data — no locks on the DOM, no dependency on the main thread.

The payoff: the *compositor thread* replays and submits geometry without
the DOM. The main thread can sit inside a 5-second `while` loop and
scrolling still works — the compositor holds the last committed display
list and layer tree, applies scroll offsets itself, and issues quads to
the GPU process. This is the insider fact every user knows and nobody
can name: "the page scrolls while the JS is blocked." The scroll offset
lives in the composited layer, on the compositor's side of the boundary;
only non-composited updates wait for the main thread.

Certain subtrees are promoted to their own **composited layer** —
anything with an animated transform, video, `position: fixed`,
scrollers, and anything carrying a `will-change` hint. Each layer
becomes GPU memory, and the arithmetic explains a whole class of
production regressions:

```text
one layer, 1920x1080, RGBA (4 B/px):
  1920 x 1080            = 2,073,600 px
  2,073,600 px x 4 B     = 8,294,400 B  = ~8.3 MB  (7.9 MiB)
  in 4 KiB pages         = 2,025 pages of texture memory
```

A compositor never draws into the buffer being scanned out — the final
frame is double-buffered (front + back, swapped at vsync), so every
layer's texture is live *plus* the frame's swap buffers. A "too many
layers" page holds *hundreds of MB* of GPU memory without a single
leak: the canonical story is `will-change: transform` sprinkled on 20
cards to "smooth the animations" — 20 × 8.3 MB ≈ **166 MB**, allocated
whether or not any animation runs, released only when the hint (or the
element) goes away. `will-change` is a promise of future change, and
the browser pays for it up front.

Layers are not rastered whole: each is cut into **tiles** — 256×256 px
for low-resolution tiles, commonly 512×512 on high-DPI screens (Blink
2024-era; a per-device heuristic). The tile is the unit of raster, and
raster is **lazy, prioritized by viewport distance**: the compositor's
tile priorities (lane 2's real job) tell the GPU process to raster
visible tiles first, near-viewport tiles next, and drop far ones. Raster
runs *in the GPU process* (out-of-process rasterization, standard in
Chrome since ~2020): the renderer sends raster *commands* across the
boundary, and in the software-raster path finished pixel tiles cross
back as shared-memory buffers — the Mojo/shared-memory handoff ch01
derived for the process model. GPU raster writes directly into
textures. One GPU process serves every tab, which is why one
pathological page's raster load becomes everyone's jank.

## The read-write interleaving law (layout thrashing)

Now derive the chapter's payoff. Layout is lazy:
`el.style.width = '10px'` performs no layout — it marks the node and its
ancestors dirty, deferring the work to this frame's layout pass. But
the JS API has geometry *reads* — `offsetWidth`, `offsetTop`,
`getBoundingClientRect()` — and their contract is an answer that is
**current**. If the DOM is dirty, the read cannot return a stale
number; the only way to answer is to flush the pending work: a
**synchronous layout, now**, inside your JS, before the line finishes.

The cost rule — the read-write interleaving law: a geometry read after
a write costs a full layout; a read on clean layout costs almost
nothing (the dirty-bit "layout is clean" fast path returns cached
values). Interleave writes and reads in a loop, and every iteration
pays:

```javascript
// thrash.js -- 1,000 write/read alternations, the classic.
// Each read happens with layout dirty, so each read forces a
// synchronous layout before it can answer.
const els = Array.from({ length: 1000 }, (_, i) => {
  const el = document.createElement('div');
  el.textContent = 'row ' + i;
  document.body.appendChild(el);
  return el;
});

let total = 0;
let t0 = performance.now();
for (const el of els) {
  el.style.width = '11px';   // write: sets a dirty bit, does ~no work
  total += el.offsetWidth;   // read: layout is dirty -> forces layout NOW
}
const thrashed = performance.now() - t0;

// The fix, the law in code: ALL reads first, then ALL writes.
t0 = performance.now();
const widths = els.map(el => el.offsetWidth); // one read pass, layout clean
for (const el of els) el.style.width = '13px'; // one write pass, dirty at the end
const batched = performance.now() - t0;

console.log(`thrashed ${thrashed.toFixed(1)} ms, batched ${batched.toFixed(1)} ms, ` +
            `ratio ${(thrashed / batched).toFixed(1)}x`);
```

The interleaved version forces up to **1,000 layouts**; the batched
version forces **one** (the reads run clean; the writes leave one dirty
tree for the frame's own layout, which happens anyway). In a Performance
recording the loop is unmistakable: a yellow script block stuffed with
1,000 thin purple "Layout" towers, each a forced synchronous layout.
Measured mid-range 2024 laptop, 1,000-element page: thrashed runs
hundreds of ms, batched single-digit-to-tens of ms — ratio typically
**10–100×**, growing with dirty-subtree size (a 100,000-node list pushes
past the top of the band).

Modern APIs are designed around the law. `ResizeObserver` exists
because the old pattern — a `resize` handler per widget, each reading
the others' geometry — was an interleaving machine: it gathers all
pending observations during the frame's layout pass and delivers them
in **one callback per frame**, after layout, so your handler reads
clean state and writes once. The law, enforced for you.

## Invalidation economics: pay only for what changed

The last piece: why a small edit on a big page is cheap. When something
repaints, paint invalidation computes a **damage rect** — the changed
region — and only intersecting tiles are re-recorded and re-rastered.
Everything else is untouched: the layer's other tiles, all other
layers, the compositor's already-rastered output; it just re-composites
old textures with the new.

The canonical comparison: a **blinking text cursor** invalidates ~20×2
px — straddling a 256×256 tile boundary, *two* tiles re-rastered per
blink, invisible in any profile. A **blinking `body` background**
invalidates the whole visible viewport — every visible tile, every
blink, ~60 times per second. Both are "one CSS change per frame"; the
damage arithmetic differs by five orders of magnitude.

The same economics have an explicit modern contract:
**`content-visibility: auto`** lets an offscreen subtree skip *all
three* stages — no style, no layout, no paint — until it nears the
viewport (the browser keeps a margin of rastered content ahead, per the
tile-priority-by-distance scheme). "Render only what's visible" as a
promise; it is what makes a 100,000-row list scrollable. The companion
property: **`contain-intrinsic-size`** — the placeholder size a skipped
subtree reports while unrendered. Without it, unrendered content
contributes no real height, the total scrollable height changes as rows
render in, and the scrollbar thumb jumps while you scroll. With it,
layout has a stable stand-in and scrolling geometry stays put.

## What insiders know

- Blink precomputes **invalidation sets** at stylesheet parse: class X
  changed → these descendant/sibling selectors are dirty. The "one
  class flip restyled the whole page" incident is almost always a rule
  count (10,000+) that made the map cover everything.
- The dirty-bit "layout is clean" check is a *fast path in the read
  APIs*: `getBoundingClientRect()` on a clean DOM is a cached-value
  return, not a computation. The trap is only ever interleaving.
- A long task is any main-thread task over **50 ms** (DevTools marks it
  with a red corner; RUM's Total Blocking Time sums the excess). A 5 s
  busy loop is one 5,000 ms long task — and the page still scrolls,
  because scrolling runs on the compositor's Begin Frame clock, not the
  event loop's.
- `will-change: transform` on 20 1080p cards is ~166 MB of GPU texture
  you pay for even if no animation runs. The Layers panel's "memory
  estimate" column is the fastest place to see it — estimates, not the
  compositor's exact tile accounting.
- vsync is not always 16.67 ms: on a 120 Hz display the frame budget is
  8.33 ms and rAF fires at 120 Hz (Blink follows the display clock); a
  "60 fps" mental model silently halves the real budget on gaming
  laptops.
- Text measurement is cached at the *shaping* level (ShapeResult per
  font+text run, Blink 2024-era): re-layout of a text page is far
  cheaper than the first layout — and a late font load (fonts are
  async) invalidates that font's runs and re-measures.

## See it yourself

Any Chrome/Chromium + DevTools reproduces all of this. Four mini-labs:

**Lab 1 — read the three lanes.** Open a long page (a documentation
site works). DevTools → Performance → Record, scroll ~3 s, stop. The
flame chart shows the lanes: the main thread's blocks color-coded
**yellow** (script), **purple** ("Recalculate Style", "Layout"),
**green** ("Paint", "PrePaint"), with the **Raster** and **GPU** tracks
below. Notice that during smooth scrolling the main thread is nearly
idle — the compositor lane is doing the frames. Then trigger a heavy
script and find the long task: a yellow block over 50 ms with a red
corner — your 16.67 ms budget spent by one lane.

**Lab 2 — the thrash, measured.** Save the benchmark above as
`thrash.html` (wrap it in a minimal page), open it, read the console.
Typical output, mid-range 2024 laptop, Chrome 2024-era:

```console
thrashed 387.4 ms, batched 8.1 ms, ratio 47.8x
```

Re-record in the Performance panel: one yellow block containing ~1,000
thin purple Layout towers — the interleaving law, visible.

**Lab 3 — count your layers.** DevTools → ⋮ menu → More tools →
**Layers**. Note the layer count and the memory estimate column (a
simple page: often 1–5 layers, tens of MB). Then run in console:

```javascript
// add 20 hinted cards; each gets its own composited layer
for (let i = 0; i < 20; i++) {
  const d = document.createElement('div');
  d.style.cssText = 'height:100px;background:#eee;' +
                    'will-change:transform';
  document.body.appendChild(d);
}
```

Refresh the panel: the count jumps by ~20 and the memory estimate
climbs by the texture arithmetic (full-viewport layers would be 166 MB
at 1920×1080 RGBA; your cards' real sizes scale the number — the column
is approximate, the direction is not).

**Lab 4 — the compositor proven.** In the console of any page:

```javascript
// the main thread disappears for 5 s; try to scroll the page NOW
const t0 = Date.now();
while (Date.now() - t0 < 5000) { /* 5 s busy loop, no tasks run */ }
```

While the tab is frozen, scroll: the page moves. The compositor holds
the committed layer tree and applies scroll offsets per Begin Frame —
no DOM, no main thread. (Holds for composited scrollers, the 2024-era
Blink default; rare main-thread-scroll pages would jank instead.)

## How to use this knowledge

- **Diagnosing "janky scroll":** record first, read the lanes. Purple
  towers → layout work (fix the writes). Yellow long tasks → JS (fix
  it or move it to a worker — workers cannot touch the DOM, so geometry
  work stays main-thread and must be batched). Saturated raster lanes →
  too much damage or too many pixels (fix layers, damage, or
  `content-visibility`). Three lanes, three different fixes.
- **Code review, the one-pattern search:** any loop containing both a
  `style.*` write and a geometry read (`offsetWidth`,
  `getBoundingClientRect`) is a forced layout per iteration. The review
  comment writes itself: batch reads before writes, or move the reads
  into one `ResizeObserver` callback.
- **Layer budgets:** review `will-change` and animated-transform counts
  with the one-line arithmetic — 4 B/px × layer dimensions — and check
  the Layers panel's memory column. 20 accidental 1080p layers is a
  166 MB regression no leak detector flags, because nothing leaked; the
  design worked as hinted.
- **Interview-grade answers:** "why does the page scroll while JS is
  blocked" (recorded once, replayed without the DOM) and "where does
  16.67 ms go" (the three-lane split) — answers naming the *mechanism*
  (commit boundary, Begin Frame clock) beat the ones reciting
  "threads".
- **Shipping long lists:** `content-visibility: auto` plus a
  `contain-intrinsic-size` sized to the typical row (100,000 rows ×
  32 px → a 3,200,000 px placeholder) turns an unshippable page
  scrollable — with predictable scrollbar geometry.

## Connections

- **Module 16 ch01** — the lanes cross a process boundary at lane 3:
  the shared GPU process and the shared-memory tile handoff are ch01's
  process-and-IPC model doing this pipeline's logistics.
- **Module 16 ch02** — style recalc and layout are traversals of the
  DOM heap; invalidation sets and dirty bits are the same "don't walk
  the whole graph" idea ch02 applied to collection.
- **Module 16 ch04** — layer textures and the tile cache are line
  items in the page's full memory inventory; the 166 MB will-change
  regression is ch04's leak-taxonomy neighbor that isn't a leak.
- **Module 16 ch05** — `performance.measureMemory` and the
  cross-origin isolation it requires.
- **Module 01 ch03** — a fresh 8.3 MB layer is demand-zero memory:
  first touch of each of its 2,025 4-KiB pages faults one zero-filled
  page in — layer churn shows up as page faults.
- **Module 01 ch15** — the JS ~5 ms share of the frame budget is spent
  inside the V8 heap that chapter derived.
- **Module 13 ch04** — Begin Frame, tile priorities, and quads are IPC
  message flows; that chapter's epoll/IPC patterns are this pipeline's
  nervous system.
- **Module 02 ch07** — Spectre's browser answer (site isolation) puts
  cross-origin content in different renderers while one GPU process
  composites for all; this chapter's boundary, drawn for throughput,
  is ch05's boundary drawn for security.

## Quiz

1. A page executes `while (Date.now() - t0 < 5000) {}` on the main
   thread. The user scrolls with the trackpad and the page scrolls
   smoothly (Chrome 2024-era, default settings). Explain, naming the
   specific machinery, why a completely blocked main thread cannot stop
   scrolling — and what the user would lose if the page needed to
   *change* while blocked.

2. A designer adds `will-change: transform` to 20 hero cards, each
   promoted to its own composited layer at 1920×1080, RGBA. (a) Compute
   the texture bytes for one such layer. (b) Compute the total added GPU
   memory. Show the arithmetic.

3. Two loops run over 1,000 just-appended elements. Loop A: per
   iteration, `el.style.width = w + 'px'; s += el.offsetWidth;`. Loop B:
   reads all 1,000 `offsetWidth` values first, then performs all 1,000
   width writes. What is the maximum number of layout passes each loop
   forces, and what ratio do the wall-clock times typically show (give
   the honest range and what it depends on)?

4. Within one frame, JS in a `requestAnimationFrame` callback runs at a
   specific point in the stage order. State that order, explain why it
   makes rAF the correct place for animation writes — and explain what
   a `getBoundingClientRect()` call at the very top of a rAF callback
   costs when nothing has dirtied the DOM since last frame, and why.

5. A Performance recording shows one 400 ms yellow script block stuffed
   with ~1,000 thin purple towers labeled "Layout". The click handler
   under it contains, per row:
   `row.style.height = computeH(row); if (row.offsetTop > fold) hide(row);`.
   Diagnose the exact mechanism, give the fix, and name the law.

6. After a CSS "optimization," a product page's tab memory grows by
   roughly 160 MB (the OS tab manager shows it; DevTools' JS heap is
   flat). The diff: `will-change: transform` on 20 cards. Why did memory
   grow, why doesn't the JS heap show it, and when is it released?

7. A 10,000-row list with `content-visibility: auto` on the rows
   scrolls with good frame rates, but the scrollbar thumb changes size
   and jumps as you scroll. One property is missing from the CSS. Name
   it, explain the mechanism it fixes, and explain why the frame rate
   was fine anyway.

8. Cross-chapter (ch01 and module 01 ch03): Chrome rasterizes tiles in
   the GPU process, not in the renderer. (a) In the software-raster
   path, what physically crosses the renderer→GPU-process boundary, and
   which ch01 mechanism carries it? (b) Connect to module 01 ch03: what
   happens the first time rasterization touches a freshly allocated
   8.3 MB layer's memory, page by page, and how many page faults is
   that at 4 KiB pages?

<details><summary>Answers</summary>

1. **The compositor thread scrolls it.** Scrolling runs on lane 2, on
   the Begin Frame clock driven by vsync, not the main thread's event
   loop. The compositor holds the last **committed** layer tree and
   display list; a scroll is a change to a layer's scroll offset that
   the compositor applies itself and re-composites into quads — no DOM,
   no main thread. What the user loses while blocked is *new content*:
   anything needing style, layout, or paint-recording (new text
   rendering into the scrolled region, hover effects, JS updates) waits
   out the 5 s long task; the last committed picture keeps moving.

2. **(a)** 1920 × 1080 = 2,073,600 px; × 4 B/px (RGBA) = **8,294,400
   bytes ≈ 8.3 MB** (7.9 MiB; = 2,025 4-KiB pages). **(b)** 20 × 8.3 MB
   ≈ **166 MB** of GPU texture memory, live whether or not any transform
   animates, because `will-change` is a hint that the element *will*
   change and the browser pays the promotion cost up front.

3. **Loop A forces up to 1,000 layout passes** — every read follows a
   write that dirtied layout, so each `offsetWidth` flushes a
   synchronous layout to answer current. **Loop B forces 1** — the
   reads run on a clean DOM (the fast path returns cached geometry),
   and the 1,000 writes leave one dirty tree the frame's own layout
   handles anyway. Wall-clock ratio typically **10–100×** (mid-range
   2024 laptop, honest range), growing with dirty-subtree size and
   document depth: a 1,000-element list sits mid-band, a 100,000-node
   page pushes past the top.

4. Order within a frame: **input events → rAF callbacks → style →
   layout → paint-record → commit**. rAF writes are consumed by the
   *same* frame's style/layout — no extra frame of latency, no
   interleaving with the pipeline's reads. A `getBoundingClientRect()`
   at the top of rAF, on a DOM no prior write has dirtied, costs almost
   nothing: the dirty-bit check says layout is clean, the read returns
   cached values — the fast path. It turns expensive the moment an
   earlier write (same callback, input handler, or stylesheet change)
   sets the dirty bit.

5. **Layout thrashing.** `row.style.height = ...` is a lazy write (a
   dirty bit, no work); the following `row.offsetTop` must return
   current geometry, so with layout dirty it forces a synchronous
   layout *inside the iteration* — ~1,000 times; the purple towers are
   those layouts. Fix: batch — read all `offsetTop` first (one clean
   pass), then write all heights (one dirty tree, one deferred layout);
   or a single `ResizeObserver` callback, whose batched delivery exists
   to enforce this. The law: **all reads, then all writes.**

6. Each hinted element is promoted to its own composited layer with a
   GPU texture: 20 cards × ~8.3 MB (1920×1080 RGBA) ≈ **166 MB**,
   matching the growth. The JS heap is flat because the memory is
   **not** in the JS heap (nor Oilpan's, ch02) — it is GPU-process-side
   texture memory, invisible to heap snapshots; you see it in the
   Layers panel's estimate or the OS's per-process numbers. It is
   released when the element is de-promoted (`will-change` or the
   element removed), not by any garbage collector — no leak detector
   flags it because nothing leaked; the design was merely hinted at 20
   times.

7. Missing: **`contain-intrinsic-size`**. Rows with
   `content-visibility: auto` skip style/layout/paint while offscreen,
   so an unrendered row contributes no measured height until it nears
   the viewport and lays out. The total scrollable height changes as
   rows render in, and the thumb (a viewport-to-total ratio) jumps.
   `contain-intrinsic-size: auto <w> <h>` supplies stand-in dimensions,
   keeping the total stable. Frame rate was fine because skipping the
   work is what made the 10,000 rows cheap — the bug was geometry
   *reporting*, not throughput.

8. **(a)** In the software-raster path the *pixel tiles* cross: the GPU
   process rasterizes into **shared-memory buffers** whose ownership
   moves across the boundary (ch01's Mojo/shared-memory handoff; the
   renderer keeps no copy). In GPU raster, *commands* cross and pixels
   land directly in GPU-accessible memory. **(b)** The 8.3 MB is
   demand-zero: the first write to each untouched 4 KiB page raises a
   minor fault, zero-filled by the kernel. 8,294,400 ÷ 4,096 = **2,025
   pages = 2,025 minor faults** on first raster — module 01 ch03's
   mechanism, why layer churn shows as page-fault counts, not
   allocation counts.

</details>
