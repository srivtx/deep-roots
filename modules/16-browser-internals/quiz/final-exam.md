# Module 16 final exam — Browser engine internals

Twelve questions across all five chapters. Closed book; every answer is
worked, not stated — the derivations are the point. Scoring: 11+ = you own
this module; 8-10 = solid, revisit the flagged chapters; <8 = the chapter
quizzes are the study path.

**Part I — the process lattice and the DOM heap it guards (chapters 01-02)**

1. **Predict.** A 2007 single-process browser holds 60 tabs; over a long
   session each tab independently has a 0.5% chance of hitting a fatal bug.
   (a) Derive the probability the whole session dies. (b) The same session
   on process-per-tab Chrome: what does one tab's 0.5% cost, and what
   exactly has to die for the *session* to die? (c) The invoice for that
   design: 60 tabs of distinct sites, each a minimal renderer at ~40 MB
   private, browser process ~200 MB, GPU ~100 MB, network service ~40 MB —
   total, and total again after the site-isolation tax.

2. **Diagnose.** Desktop Chrome, full site isolation. A top-level page at
   `mail.google.com` embeds three iframes: `accounts.google.com` (sign-in),
   `evil.example` (a 300-pixel ad), and `video.youtube.com` (an embedded
   player). QA counts three renderer processes and files two complaints:
   "isolation is broken — the accounts iframe shares the page's renderer,"
   and "youtube should share too, Google owns both." On a low-RAM Android
   phone the same page shows `evil.example` sharing the mail renderer.
   Diagnose all three findings: the correct count, the unit site isolation
   keys on, and what the phone is doing.

3. **Diagnose.** An SPA gains ~1 MB per route change; teardown calls
   `container.replaceChildren()`. After 25 navigations the heap snapshot
   shows 25 `Detached HTMLDivElement` rows, each with Retained ~0.9 MB, and
   one retainer chain: `Detached HTMLSpanElement <- first_child of Detached
   HTMLDivElement <- onClick <- event_listeners of HTMLBodyElement <-
   window`. (a) Derive the 0.9 MB — each leaked subtree is one root div
   plus 11,000 bare spans at ~80 B of C++ memory per node. (b) Why did
   `replaceChildren()` free nothing? (c) The minimal fix — and why did this
   leak cost zero dropped frames all day?

**Part II — three lanes, one 16.67 ms budget (chapter 03)**

4. **Predict.** A page's rAF callback runs a 12 ms computation; the rest of
   its main-thread work is style ~1 ms, layout ~2 ms, paint-record ~1 ms,
   plus the commit; the compositor takes ~2 ms and the GPU process ~3.6.
   (a) On a 60 Hz display, does the frame make its vsync? What does the
   user see, and for how long? (b) The same page on a 120 Hz gaming
   laptop: recompute the budget and the verdict. (c) During the 12 ms
   callback the user scrolls and the page still responds — name the
   machinery and the clock each lane answers to, and what the user *loses*
   while blocked.

5. **Predict.** A "CSS optimization" adds `will-change: transform` to 12
   hero cards, each promoted to its own composited layer at 2560x1440,
   RGBA. (a) Texture bytes per layer, and the total — show the arithmetic.
   (b) A teammate argues "the JS heap is flat, so this is not a memory
   problem." What actually grew, where do you see it, and when is it
   released? (c) How many 4 KiB page faults does the *first raster* of one
   such layer raise, and why?

6. **Diagnose.** Two jank reports, one page. Report 1: a Performance
   recording shows a 400 ms yellow script block stuffed with ~2,000 thin
   purple "Layout" towers; the handler beneath it runs, per row:
   `row.style.height = computeH(row) + 'px'; if (row.offsetTop > fold)
   row.classList.add('off');`. Report 2: after the fix the handler is fast,
   but a 100,000-row list still drops frames during fast flings, and its
   scrollbar thumb changes size mid-scroll. Diagnose both mechanisms and
   give both fixes — name the law for the first.

**Part III — the page's memory: tanks, leaks, and the guard (chapter 04)**

7. **Predict.** A gallery page decodes 12 hero images, each 4,000x3,000
   RGBA, simultaneously; the flat JPEGs are ~500 KB each on disk. The page
   also spawns 6 dedicated workers for image processing. (a) Exact
   decoded-image bytes. (b) The compression ratio — and why no better
   codec changes it. (c) What the image cache does under memory pressure,
   and the price paid. (d) The worker bill, and what happens to the
   process count.

8. **Diagnose.** A news app's renderer grows ~150 MB per day. Its
   navigation code:
   `const cache = new Map(); function onNavigate(route) {`
   `  cache.set(route.id, parseRoute(route)); }` — `parseRoute` retains
   ~2 MB per entry, ~75 navigations a day. A refactor to `new WeakMap()`
   throws `TypeError: Invalid value used as weak key` on the first
   navigation. (a) Which leak-taxonomy entry is this, and what is the
   retainer-chain terminal? (b) Why does the WeakMap throw? (c) The two
   honest resolutions.

9. **Predict.** (a) Exactly what does this print, and what is thrown?
   ```js
   const m = new WebAssembly.Memory({ initial: 3, maximum: 5 });
   const view = new Uint8Array(m.buffer);
   console.log(m.grow(2));
   console.log(view.byteLength, m.buffer.byteLength);
   try { m.grow(1); } catch (e) { console.log(e.constructor.name); }
   ```
   (b) Derive the minimum guard-region size that lets V8 emit wasm32 loads
   with no bounds-check branch, and state the two checks the trap handler
   makes before converting a SIGSEGV into a trap rather than a crash.

**Part IV — the wall moves down a layer (chapter 05)**

10. **Diagnose.** A page's `performance.now()` delta census shows distinct
    steps of 0.1, 0.2, 0.3 ms, and `typeof SharedArrayBuffer` is
    `"undefined"`. (a) Name the policy behind the steps and derive why it
    defeats the attack it exists for. (b) The page is re-served with
    `Cross-Origin-Opener-Policy: same-origin` and
    `Cross-Origin-Embedder-Policy: require-corp`: what changes in the
    census, what does `crossOriginIsolated` become, and why does the
    browser now trust the page? (c) Why did the January 2018 disclosure
    force this design at all — where did SOP's check run, and where did the
    protected data sit?

11. **Predict.** A page at `https://shop.example` runs:
    ```js
    await fetch("https://api.example/me", {
      credentials: "include",
      headers: { "X-Api-Version": "2" },
    });
    ```
    and `api.example` replies 200 with `Access-Control-Allow-Origin: *`.
    (a) Does the promise resolve with the JSON? What does the console say,
    and what is the server-side fix? (b) Is a preflight sent — why? (c) The
    team abandons fetch for `postMessage` and installs
    `window.addEventListener("message", ev => applyCoupon(ev.data.code))`.
    Name two concrete routes an attacker origin takes to reach this
    handler, and the two-line fix.

12. **Concept (cross-module).** The capstone. (a) What does bug 1 of a
    Chrome full chain buy, and which module 16 chapter's object graph is
    the bug farm it lives in? (b) What must bug 2 defeat — and why is the
    *real* perimeter an IDL file rather than a sandbox policy? (c) Why
    does a full chain pay in the low six figures at Pwn2Own while a
    renderer-only bug pays a fraction? (d) One sentence, with numbers,
    tying the exploit's cost to the RAM invoice the same architecture
    produces.

<details>
<summary>Answers</summary>

**Part I**

1. (a) The session survives only if *no* tab hits its bug:
   P = 0.995^60 = **0.740**, so **P(dies) ≈ 26%** — one session in four.
   The union law is the mechanism: P = 1−(1−c)^N climbs toward 1 with N
   (40 tabs at 1% is already 33%); in one address space the failure modes
   are unioned, so more tabs means more certain death. (b) Process-per-tab
   contains each 0.5% in its own address space: one tab dies — the "Aw,
   Snap" sad tab — and a reload spawns a fresh renderer. The session dies
   only when the *browser process* (the broker, the one process allowed to
   say yes) dies — everything goes, session restore on relaunch. c stays c
   no matter how many tabs; that containment is the entire 2008 trade.
   (c) 60 × 40 = **2,400 MB** of renderers, + 200 browser + 100 GPU + 40
   network = **2,740 MB ≈ 2.7 GB** before any page data rides on top.
   Site isolation (Chrome 67 desktop, the Spectre response) moved the
   boundary from tab to site for a further +10-20%: **~3.0-3.3 GB**.
   Fault isolation costs address space; process count is the bill, and
   (a) is what the bill buys.

2. **Three renderers is correct; both complaints are wrong; the phone is
   policy.** The unit is the **site** — scheme plus eTLD+1 (the
   registrable domain) — not the origin, not the tab, and not the company.
   `mail.google.com` and `accounts.google.com` are different *origins*
   (host comparison is exact string match) but the same *site*: both
   bottom out at `google.com`, so site isolation deliberately *allows*
   them to share a renderer — that is the boundary drawn at the family
   edge, not a broken one. `evil.example` is a different site, so it gets
   its own renderer — an **OOPIF**, out-of-process iframe. And
   `video.youtube.com` bottoms out at `youtube.com`: a *different* site
   from `google.com` despite common ownership — the unit is DNS registry
   structure, not the brand — so it must not share, and QA's second
   complaint is backwards. Between the renderers, pixels cross, never
   pointers: each OOPIF rasterizes its own content, the compositor layers
   the quads, and the browser process owns the frame tree. The phone:
   low-RAM Android ships partial site isolation (Chrome 77-era rollout
   gated on device RAM — a curated allowlist of high-value sites), so
   cross-site sharing there is policy, not a bug.

3. (a) 11,001 nodes × ~80 B ≈ **880 KB ≈ 0.9 MB** of Oilpan C++ memory per
   navigation. The snapshot's Retained is the dominator computation: the
   root div is the unique gate its 11,000 spans pass through, so the whole
   subgraph's bytes land on its row. Twenty-five instances ≈ **22 MB**
   Blink-side; DevTools deltas land in the 2-3 MB class each once the
   V8-side tax is counted — one wrapper per *touched* node, the closure,
   the listener record. (b) `replaceChildren()` detaches; it does not
   de-root. Detachment drops the container's parent-side edges —
   `parent_ == null`, `layout_object_ == null` — but the retainer chain
   names the real owner: the live body's listener map → `onClick` (a V8
   closure that Oilpan traced *into* V8 to keep live) → its captured
   `tree` → the wrapper (a live wrapper is an **Oilpan root**: V8's marker
   found it and handed it to Blink's `EmbedderHeapTracer`, which marked
   the node) → the div → `first_child_` → the spans. Liveness flows JS →
   wrapper → node → children, never node → wrapper; here JS never forgot
   the wrapper, so both collectors are *working correctly* — the program
   keeps garbage reachable. A rooted chain, not the rootless ring embedder
   tracing was built to kill. (c) Fix: remove the listener at teardown —
   `removeEventListener`, or register with `{ signal }` and abort the
   controller on unmount — or capture nothing that outlives the subtree.
   Zero frames: a detached node has `layout_object_ == nullptr`, so it
   owns no layout object, records no display items, and no damage rect can
   name it — chapter 03's pipeline never sees these bytes. The leak is
   invisible except to the task manager and the snapshot.

**Part II**

4. (a) Main-thread work = 12 + 1 + 2 + 1 = **16 ms**, against a share of
   ~10 of the 16.67 ms budget; the pipeline total is 16 + 2 + 3.6 ≈
   **21.6 ms > 16.67** — the frame misses its vsync. Nothing new is ready
   to swap, the display rescans the old frame, and the user sees **33.3 ms
   of stale content** — one dropped frame, jank. (b) At 120 Hz Blink
   follows the display's clock: the budget is **8.33 ms** and the teaching
   split halves (main thread ~5 ms, JS ~2.5). The 12 ms callback alone is
   ~150% of the entire budget; the same 21.6 ms of work now spans three
   vsync periods — the frame swaps at the 25.0 ms tick, **~40 fps on a
   120 Hz panel**. The "60 fps" mental model silently halves the budget on
   gaming laptops; this page halves it again. (c) Scrolling responds
   because it runs on the **compositor thread** (lane 2) on the Begin
   Frame clock driven by vsync — not on the main thread's event-loop
   clock. The compositor holds the last *committed* layer tree and display
   list, applies scroll offsets itself, and issues quads: record once,
   replay without the DOM. What the user loses while blocked is *new
   content* — hover effects, newly revealed text, JS updates wait out the
   callback; the last committed picture keeps moving.

5. (a) 2,560 × 1,440 = 3,686,400 px; × 4 B/px = **14,745,600 B ≈ 14.7 MB
   per layer** (the ~14 MB tile from ch01's zero-copy arithmetic); × 12 =
   **~177 MB** of GPU texture memory. (b) What grew is the
   compositor-layers tank: GPU-process-side texture memory, invisible to
   the JS heap and to Oilpan — which is exactly why the JS heap is flat
   while the OS's per-tab numbers climb. See it in the Layers panel's
   memory-estimate column. It is released when the element is de-promoted
   — the `will-change` hint or the element goes away — never by any
   garbage collector: `will-change` is a *promise* of future change and
   the browser pays for it up front, animation running or not; no leak
   detector flags it because nothing leaked — the design was merely
   hinted at twelve times. (c) 14,745,600 ÷ 4,096 = **3,600 demand-zero
   minor faults**: first touch of each untouched 4 KiB page faults one
   zero-filled page in (module 01 ch03), which is why layer churn shows up
   in page-fault counts, not allocation counts.

6. Report 1 is **layout thrashing**, and the law is *batch all reads, then
   all writes*. The write is lazy — `row.style.height` sets a dirty bit
   and does ~no work — but the following `row.offsetTop` read must return
   *current* geometry, and with layout dirty the only way to answer is a
   synchronous layout inside the iteration: up to **2,000 forced layouts**
   (the purple towers), where the batched shape forces one. Fix: one clean
   read pass (reads on a clean DOM are cached-value returns), then the
   write pass leaving one dirty tree for the frame's own layout — or a
   single `ResizeObserver` callback, whose per-frame batched delivery
   exists to enforce exactly this law. The measured ratio is typically
   10-100×. Report 2 is invalidation geometry, not throughput. The dropped
   frames during flings are damage economics: rows entering the viewport
   must be styled, laid out, and rastered on demand — the fix is
   `content-visibility: auto`, which lets offscreen subtrees skip all
   three stages until they near the viewport. The jumping thumb is the
   missing companion: **`contain-intrinsic-size`**. Unrendered rows
   contribute no measured height, so the total scrollable height changes
   as rows render in and the thumb (a viewport-to-total ratio) jumps; the
   placeholder size gives layout a stable stand-in. The calibration for
   both: a blinking text cursor invalidates ~20×2 px and re-rasters two
   tiles; a blinking body background re-rasters every visible tile ~60
   times per second — same "one CSS change per frame," five orders of
   magnitude apart.

**Part III**

7. (a) 4,000 × 3,000 = 12,000,000 px; × 4 B = 48,000,000 B each; × 12 =
   **576,000,000 B ≈ 549 MiB (~576 MB)** of decoded pixels. (b) ~500 KB
   on disk → 48 MB in RAM is **~96:1** — and a photographic 2-5 MB JPEG
   decodes to the *same* 48 MB. Compression shrinks the disk side only;
   after decode, pixels are 4 B/px forever, and no codec changes that
   (only the encoded bytes). (c) The decoded-image cache is LRU with
   **discard-on-pressure**: under pressure it drops the decoded pixels,
   keeps the disk file, and re-decodes on next paint — trading CPU for
   RAM. The right trade until the re-decode lands in a scroll handler and
   the policy becomes main-thread jank. (d) Each worker is a thread
   *inside the renderer process* plus a full V8 isolate — its own 1-3 MB
   heap floor, ~1-2 MB stack, its own script copies — so 6 × (floor +
   stack + scripts) ≈ **12-18 MB** before any pixel is touched. The
   process count does not change (workers are threads, not processes);
   the renderer's footprint grows, the process list does not — cheap next
   to 6 renderers at ~30-50 MB each.

8. (a) Taxonomy **(3), the expanding collection** — state that grows with
   navigation and never shrinks. The retainer walk terminates at the
   `cache` Map itself: a global strong root holding strong keys *and*
   strong values — 75 entries/day × ~2 MB is the 150 MB. (b) A WeakMap
   key must be an *object* — a collectable identity whose reachability
   can end; `route.id` is a string, and a primitive has no lifetime to be
   weak over, so `set` throws `TypeError: Invalid value used as weak key`
   before a single entry exists. (c) Resolution one: key by an object
   that dies with the route — the route/page object itself — so the entry
   is collectable when the key goes unreachable. Resolution two: keep the
   primitive key and make deletion explicit — a Map plus
   `cache.delete(route.id)` in teardown, or a bounded LRU. "Entry dies
   with key" is only a promise for keys the collector can collect.

9. (a) Line 1 prints **`3`** — `grow` returns the *old* page count. Line
   2 prints **`0 327680`**: the first grow detached the old buffer, so
   the pre-existing view's `.byteLength` is 0, while the fresh
   `m.buffer` is 5 pages × 65,536 B = 327,680 B. Detachment is a spec
   decision about stale views with wrong lengths — the reservation means
   the base address never moved, so it is not an address-space
   necessity. Line 3 prints **`RangeError`**: the JS API throws when
   5 + 1 exceeds `maximum: 5` (the wasm *instruction* would return -1
   instead of throwing). (b) The effective address of any wasm32 access
   is `base + (u32)index + (u32)offset + access size`. The two facts:
   wasm32 indices are 32-bit unsigned (max 0xFFFFFFFF) and static
   offsets are also u32 immediates, with accesses up to 16 B (SIMD).
   Worst case ≈ 0xFFFFFFFF + 0xFFFFFFFF + 16 ≈ **8 GiB 8 B past base**,
   so 4 GiB of usable memory plus a 6 GiB guard — **10 GiB reserved per
   memory** — covers every address any wasm32 access can compute: the
   guard is *derived*, not chosen, and generated code carries **zero
   bounds-check branches**. The reservation is purely virtual: RSS
   counts touched pages only. When an access does land in the guard, the
   SIGSEGV (Windows: VEH exception) becomes a trap only after two
   checks: the faulting PC must be in V8's registry of
   trap-handler-protected wasm code, and the fault address must be
   inside a wasm memory reservation. Anything else — a wild crash in
   Blink's C++ — fails the first check by construction and is forwarded,
   so the renderer dies rather than a trap being faked. (A load at
   70,000 against a 1-page memory: the address is in the guard, both
   checks pass, the caller sees `RuntimeError: memory access out of
   bounds`, and no branch ever executed to check it.)

**Part IV**

10. (a) The **timer clamp**: on an ordinary (non-cross-origin-isolated)
    page, Chrome quantizes `performance.now()` to **100 µs steps**, so
    the census snaps to 0.1/0.2/0.3 ms. The Spectre signal is a cache
    hit-vs-miss — tens of *nanoseconds* — and a 100 µs tick is 100,000
    ns: the clamp drowns a signal three to four orders of magnitude
    smaller, killing the side channel's stopwatch. (b) The census steps
    sharpen to **0.005, 0.01, 0.015 ms** — the 5 µs clock — and
    `crossOriginIsolated` becomes **`true`**. The browser trusts the page
    because the two headers made it prove it is alone: COOP `same-origin`
    severed cross-origin openers (`window.opener` null on both sides),
    and COEP `require-corp` made the document promise every subresource
    is same-origin, CORS-approved, or CORP-marked — no other origin's
    bytes share this process, so a sharp clock is no longer a weapon
    aimed at anyone. The reward: the 5 µs clock and `SharedArrayBuffer`,
    which since Chrome 92 (July 2021) exists only inside this gate —
    shared memory plus a precise clock is a Spectre kit, and the gate is
    the proof-of-solitude fee. (c) Because SOP's check ran *in software,
    inside the renderer* — an access check in a property getter, a CORS
    gate on a response, a keyed storage map — while the protected data
    sat in the *same process's memory*: cached cross-origin responses,
    iframe contents. Same process means same L1/L2, and speculative
    execution reads past a language-level check while the cache leaks the
    transient result. The wall drawing was wrong about location: SOP was
    a checkpoint, not a wall. The wall had to move to the page tables
    (site isolation, Chrome 67, +10-20% RAM) and the stopwatch had to be
    blunted (the clamp) — check and data finally on the same side of a
    hardware boundary.

11. (a) **The promise rejects; the JSON never reaches script.** The 200
    and the `*` are not enough: `credentials: "include"` makes it a
    credentialed request, and the spec forbids the wildcard from ever
    matching one — the console says it verbatim ("The value of
    'Access-Control-Allow-Origin' must not be the wildcard '*' when the
    request's credentials mode is 'include'"). The rule is anti-CSRF
    arithmetic: credentials attach the *victim's* authority to an
    *attacker-initiated* request, and a wildcard would let any page on
    the web read the authenticated response. Server fix: echo the exact
    origin — `Access-Control-Allow-Origin: https://shop.example` — plus
    `Access-Control-Allow-Credentials: true`. (b) Yes: the custom
    `X-Api-Version` header takes the request out of the "simple"
    classification (GET/HEAD/POST with form-ish content types), so the
    browser first sends an `OPTIONS` preflight carrying
    `Access-Control-Request-Method`/`-Headers`; the real request only
    goes out if the server answers it. (c) Any origin that can *obtain a
    Window reference* reaches the handler: an attacker page that
    `window.open`ed this one holds the popup's window and can postMessage
    it — or, sneakier, an attacker *embeds* this page as an iframe,
    because the embedder's script holds `iframe.contentWindow`, a
    parent-to-child line into the handler. `ev.data` is attacker-shaped
    end to end. Two-line fix: gate on
    `if (ev.origin !== "https://expected.example") return;` — `ev.origin`
    is computed by the browser from the sender's realm and cannot be
    forged through the payload — and require senders to pass an explicit
    `targetOrigin`, never `"*"`.

12. (a) Bug 1 is the renderer compromise: a use-after-free in the
    DOM/parser heap — **module 16 ch02's object graph is the bug farm**
    (the missing-write-barrier race that sweeps a still-reachable object
    is exactly the `heap-use-after-free in blink::` class; module 02/03's
    bug classes are how it fires) — or a JIT miscompile (module 10 ch03's
    UB license) turned into arbitrary read/write. It buys code execution
    *inside* the sandboxed renderer: no disk, no sockets, no fork. (b)
    Bug 2 is the escape, and it must defeat the broker: the renderer
    cannot even `open()` a path — it sends Mojo IPC to the privileged
    browser process, which validates against an allowlist and performs
    the syscall (Linux seccomp-bpf; Windows restricted token at Untrusted
    integrity, one notch below Low; macOS seatbelt). So the exploitable
    surface is everything the broker will accept: the **Mojo IDL** — on
    the order of 1,000 `.mojom` files, every method of every interface
    reachable from hostile code inside the renderer. That file set is the
    real perimeter, and it is defended industrially: ClusterFuzz rebuilds
    Chrome on tens of thousands of cores around the clock, including
    dedicated Mojo IPC fuzzers that mutate message fields against the
    validators. (c) Pricing follows scarcity: one bug gets you the
    renderer, two get you the user — and bug 2 is scarce precisely
    because the IDL perimeter is fuzzed continuously, so full chains
    clear the low six figures ($100,000-$200,000-class at the 2023-2024
    Pwn2Own events) while renderer-only bugs are worth a fraction.
    (d) The same lattice bills both sides: ~40 MB of private address
    space per minimal renderer, ~10-20% more for site isolation's
    page-table walls — the RAM users complain about *is* the exploit's
    price tag, paid by the gigabyte.

</details>
