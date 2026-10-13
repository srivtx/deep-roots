# Chapter 02 — The DOM as a heap graph: Oilpan and two collectors

> **The 30-second version.** The DOM is not a tree in a textbook sense; it
> is a graph of C++ objects on Blink's own heap — 40–100+ bytes of pointers
> per node (parent, children, siblings, document back-pointer, layout
> association) — and something must decide when those bytes die.
> Refcounting cannot: parent–child and listener–closure references form
> cycles, so counts never hit zero — the structural leak that made IE-era
> pages grow until the tab died. So Blink traces: Oilpan, mark-and-sweep
> over pages of fixed-size buckets, roots from `Persistent`, edges from
> `Member`, write barriers on every store, concurrent marking since 2018.
> But the page has a *second* collector — V8's — and the two share objects
> only through a wrapper: a JS object holding a pointer to the C++ node.
> Each collector traces its own heap; the wrapper is where they trade
> liveness evidence, via V8's embedder tracing. That bridge is also where
> the most famous leak on the web lives: the detached subtree kept alive by
> one closure.

## A node is a C++ object: the inheritance tax, counted in bytes

Start at the bottom, because the whole chapter is about who owns these bytes.
`document.createElement('div')` constructs a C++ object through five levels of
inheritance (Blink, 2024-era): `Node` → `ContainerNode` → `Element` →
`HTMLElement` → `HTMLDivElement`. Each level attaches one concept, and each
concept costs fields:

```text
a bare HTMLDivElement, 64-bit, Blink 2024-era, order-of-magnitude (versions vary)

+0x00  vtable ptr          8 B   every level is polymorphic; one vptr, not five
+0x08  parent_             8 B   Member<Node>       (back-pointer up the tree)
+0x10  next_               8 B   Member<Node>       (sibling ring)
+0x18  previous_           8 B   Member<Node>
+0x20  first_child_        8 B   Member<Node>       (added by ContainerNode)
+0x28  last_child_         8 B   Member<Node>
+0x30  tree_scope_         8 B   which Document's world am I in
+0x38  layout_object_      8 B   raw ptr; non-null == "attached" (in the
                                layout tree; null == detached)
+0x40  attribute_data_     8 B   Element's payload: pointer to the attribute
                                storage (the "attribute map")
+0x48  node_flags_         4 B   type bits, attached bit, rare-data bit
+...   rare_data_          8 B   lazily allocated spillover (inline boxes,
                                mutation records, ...)
                         -----
                       ~ 72-88 B for an empty div; ~40-56 B for a bare
                       Node (text/comment); "100+ B" once attributes,
                       inline style, and rare data attach
```

The numbers are ranges because the layout shifts between Chrome versions; the
*structure* is the lesson. A node is mostly pointers to other nodes — the tree
is literally the heap graph. `layout_object_` is the "attached" state: a node
in the layout tree has a renderer; a detached node has `layout_object_ ==
nullptr` and no parent path to the document — but it still owns its children,
`first_child_` survives detachment, which is why a detached subtree is a whole
subgraph of live bytes, not one object.

Two details that pay off later. `nodeType` (the 1/3/8/9/11 constants from the
DOM spec: element, text, comment, document, fragment) is a switch over the
type bits in `node_flags_` — the classic visitor pattern, executed by every
consumer from the serializer to DevTools. And attributes are not a per-node
hash: an element points to shared `AttributeData` storage, a compact vector of
name–value pairs whose keys are interned in the global `AtomicString` hash
table. Counts are small — pages typically carry 0–4 attributes per element —
so lookup is a linear scan, with the hash doing its work at name-interning
time.

## Who deletes a node? Manual death, refcount death, and the ring that ate IE

The ownership problem, stated plainly: `removeChild` removes a node from a
tree, but the node's bytes still exist and its children still reference it.
Who calls the destructor, and when? C++'s two stock answers both fail alone.

**Manual ownership** (the owner deletes): works until any code path forgets —
then a use-after-free, or a double delete. In a browser, where arbitrary web
script mutates the graph from any callback, the number of code paths is
unbounded. The 1990s browser crashes lived here.

**Refcounting** (delete when the count hits zero): every `parent_`
increments the child's count, every listener increments the closure's count.
Deterministic, incremental — but the DOM's own shape builds rings. Walk the
counts in the IE-era model (COM objects on the DOM side, JScript objects on
the script side, both refcounted):

```text
the structural leak, refcounted world (IE 6/7-era, mid-2000s)

   HTMLDivElement (refcounted)         closure onClick (refcounted)
   +---------------------------+  listener map: div owns closure (+1)
   |  count = 2:               | ------------------------------->  count = 2:
   |    +1 from closure's      | <--------------------------------
   |    captured var `div`     |  environment record captures div (+1)
   +---------------------------+
        ^    | parent_ owns child (+1)         child's count = 2:
        |    v                                 +1 parent, +1 own listener
      HTMLSpanElement ...
```

`removeChild` runs: the document decrements the div from 2 to 1. Nobody ever
issues the final decrement: the closure holds the div, the div's listener map
holds the closure. The ring is unreachable from any root and immortal — the
leak is *structural*, not a forgotten `delete`. The 2000s IE folklore ("don't
build closures over DOM nodes") exists because that engine had no collector
that could see both sides of this ring. The ring comes in two stock shapes:
parent↔child (mutual `parent_`/`first_child_` counts) and listener↔element
(the one drawn above).

**So: tracing.** A collector that starts from roots and follows pointers can
discover that a ring has no path from any root — refcounting can only ask
"does anyone point at me?", never "can anyone *reach* me?". That single
difference is why every modern engine traces.

## Oilpan: tracing the Blink heap, with buckets, barriers, and a Trace method

Oilpan (Blink's tracing GC, ~2014-era origin, now on V8's `cppgc` library)
owns the C++ heap you met in the first section. Its heap layout is module 01
chapter 19's size-class allocator re-derived inside a collector: the heap is
carved into pages — 128 KiB (2^17) in 2024-era cppgc — and each page serves
one bucket, a fixed slot size. A request rounds up to its bucket
(pointer-sized, 8 B granularity); same-size objects cohabit, so a freed slot
is reusable by the next same-size node without fragmentation. Objects past
the largest bucket (on the order of 16 KiB; the table is version-specific)
get dedicated large-object pages — the same split a malloc makes between
arenas and `mmap`.

Three pointer types define the programming model:

- `Member<T>` — an on-heap edge. The tracer follows it during marking.
- `Persistent<T>` — a root: a pointer stored *outside* the heap, in the
  fields of non-GC'd C++ objects or in globals, that must outlive any single
  collection.
- `WeakMember<T>` — an edge that does not keep the target alive; cleared
  before the target is swept.

(Raw `Node*` on the C++ stack is handled separately — Oilpan scans the
mutator stack *conservatively* during a stop, which is why rendering code
may hold a plain pointer across a GC point and survive.)

Marking needs to know every edge, and every class declares its own, in the
`Trace()` method — the one piece of macro-heavy ceremony every Blink class
writes:

```cpp
// Simplified from Blink's ContainerNode (Oilpan, 2024-era).
void ContainerNode::Trace(Visitor* visitor) const {
  visitor->Trace(first_child_);   // Member<Node>: if target is white, mark it
  visitor->Trace(last_child_);    // black and enqueue it for its own Trace()
  Node::Trace(visitor);           // base class traces parent_, next_, prev_...
}
```

Because marking is **incremental and concurrent**, the collector runs while
mutator code executes — which creates the classic race the two earlier
modules already derived: an object marked black (done) gains a new child
after the marker passed over it; the child is white, nobody will enqueue it,
and sweep frees a reachable object. The fix is the same contract module 01
chapter 15 gave V8's scavenger and module 10 chapter 04 showed the JVM's JIT
emitting: a **write barrier**. In Oilpan it is compiled into `Member` stores
as a combined barrier (schematic; function names vary across cppgc versions):

```cpp
node->first_child_ = child;              // Member<Node>::operator=
//   1. store the pointer
//   2. WriteBarrier dispatch, one branch on global collector mode:
//        marking phase:     black source, white target -> mark target now
//        generational mode: old source, young target   -> record slot in the
//                           remembered set, so a young GC can find it
```

Skip the remembered set and a young collection misses an old-to-young edge;
skip the marking half and you get a swept-but-reachable object — the
use-after-free crash class that shows up in Blink security bugs as
"heap-use-after-free in blink::*" (more in *What insiders know*).

The pause-time story: the 2018 project that made Blink GC marking concurrent
moved full collections on heavy pages from ~100 ms pauses to the ms class,
per the project's own reports. A 100 ms pause against a 16.6 ms frame budget
is six dropped frames from GC alone — "concurrent" was not optional.

## Two heaps, one DOM: the wrapper bridge

Now the core problem. The renderer has **two** collectors: V8's (module 01
chapter 15's generational scavenger plus its major marker) for JS objects,
and Oilpan for the C++ DOM. One address space, two heaps, and objects that
must reference each other *by design* — your JS closure holds a div, the
div's listener map holds a JS function. They cannot merge (V8 is a shared,
embedder-agnostic library; Oilpan knows C++ types), so they share objects
through an explicit bridge: the **wrapper**.

When script first touches a node, Blink creates a wrapper: a plain V8
JSObject with (historically two) internal slots — slot 0 the node pointer
(in sandboxed builds, a handle into V8's external pointer table, chapter 05's
territory), slot 1 the type info. The node caches the wrapper in a field of
its own, and that cache is the **identity guarantee**: re-wrapping returns
the same JS object, which is why `document.body === document.body`. Without
the cache, every access would mint a fresh wrapper and identity comparisons
— the backbone of user code — would all be false.

```text
        V8 heap (the JS world)                 Blink heap (Oilpan)
+---------------------------------+      +------------------------------------+
| onClick (closure)               |      | HTMLDivElement (C++ Node)          |
|   captured var `tree`           |      |   first_child_ --> 10,000 spans    |
|     |                           |      |   parent_ == null (detached)       |
|     v                           |      |            ^                       |
|   wrapper (JSObject)            | slot |            | strong: a wrapper     |
|     slot 0: node handle --------|---O->|  marked live by V8 is an OILPAN    |
|     slot 1: WrapperTypeInfo     |      |  ROOT; Blink marks the node        |
+---------------------------------+      |  and traces Member edges from it   |
        ^                               +------------|-----------------------+
        |  node -> wrapper: a WEAK reference (the wrapper_ field)             |
        +-----------------------------------------------                      |
                                                                             |
   directions, stated once:                                                  |
   - JS keeps the wrapper alive  -> wrapper keeps the NODE alive             |
     (V8 marking -> EmbedderHeapTracer -> Oilpan marking)                    |
   - the node does NOT keep the wrapper alive (weak backref)                 |
   - if JS forgets the wrapper, V8 collects it; if that was the node's       |
     last owner, Oilpan sweeps the node ("wrapper disposes node")            |
```

The coordination, step by step, during one major collection:

1. V8 marks its graph from JS roots (globals, the stack, handles).
2. Wrappers are special: when the marker finds one live, it must ask the
   embedder, because slot 0 is a cross-heap edge V8 refuses to treat as
   "just a pointer." The API is V8's `v8::EmbedderHeapTracer`: V8 hands the
   live wrappers to Blink's implementation, which reads each node pointer
   and **marks the node as an Oilpan root**.
3. Oilpan traces `Member` edges outward — including the *reverse* cross-heap
   edges: an on-heap event listener map holds V8 function references, and
   tracing them marks those JS closures in V8's heap.
4. Both sides iterate to a fixed point — a wrapper discovered late can
   resurrect a node, whose listener can resurrect another closure; only when
   neither heap produces new marks is the verdict final.

Read the semantics off the diagram: liveness flows JS → wrapper → node →
children; it never flows node → wrapper. The weak direction is the modern
"wrappers don't leak nodes" fix — a page that keeps a huge tree alive in C++
but lets JS forget every handle collects the whole tree: no JS root marks the
wrapper, and the node cannot defend its own wrapper.

## The leak: one closure, ten thousand nodes

Everything above exists because of one failure mode. The canonical
reproducer, inline and runnable:

```html
<!doctype html>
<meta charset="utf-8">
<title>detached leak lab</title>
<button id="leak">leak once (~1 MB)</button>
<button id="clean">clean variant</button>
<script>
const body = document.body;

function buildSubtree() {
  const root = document.createElement('div');   // 10,000 bare spans
  for (let i = 0; i < 10000; i++) {
    root.appendChild(document.createElement('span'));   // ~80 B each, C++ side
  }
  return root;
}

document.getElementById('leak').addEventListener('click', () => {
  const tree = buildSubtree();
  body.appendChild(tree);                 // attach: the DOM work is real
  // ONE listener, on an element that STAYS attached; its closure captures
  // the parent of the subtree:
  body.addEventListener('click', function onClick() {
    tree.classList.toggle('go');          // <-- the capture that leaks
  });
  body.removeChild(tree);                 // detach the subtree
  // `tree` (the local) dies; the closure's captured copy does not.
});

document.getElementById('clean').addEventListener('click', () => {
  const tree = buildSubtree();
  body.appendChild(tree);
  body.removeChild(tree);
  // fix: nothing captures `tree`. No listener references it, no global does:
  // the wrapper has no JS root, the node has no C++ owner -- the whole
  // subtree is collectable, all 10,001 nodes, on the next major GC.
});
</script>
```

Draw what the leak button leaves behind:

```text
GC root: the window / document (live, attached)

HTMLBodyElement (attached, live)
   | listener map (a Blink-side, on-heap registry)
   v
onClick (V8 closure)  <-- Oilpan traced INTO V8 to keep this live (step 3)
   | environment record: captured `tree`
   v
wrapper (V8 JSObject) <-- V8 kept this live; via EmbedderHeapTracer it is
   |                      an Oilpan root (step 2)
   | slot 0
   v
HTMLDivElement (DETACHED: parent_ == null, layout_object_ == null)
   | first_child_
   v
HTMLSpanElement x 10,000 (DETACHED)   ~10,001 nodes x ~80 B = ~0.8 MB of
                                       C++ memory, per click, forever
```

The scale: 10,001 nodes × ~80 B ≈ 0.8 MB — call it ~1 MB per leak instance —
plus the V8-side tax (a wrapper per touched node, the closure, the listener
record), so a DevTools delta lands in the 2–3 MB class per click on a
2024-era Chrome (version-dependent). One instance per route change, and a
day-old SPA tab is hundreds of MB. Nothing renders wrong, nothing throws:
detached nodes cost zero frames — chapter 03's pipeline never sees them. The
leak is invisible except to the task manager and the heap snapshot.

**The honest subtlety.** The ring shape from the refcounting section —
listener *on the detached node itself*, closure capturing that same node —
is exactly what embedder tracing was built to kill: a rootless ring marks
nothing from either side's roots, and both heaps collect it in a coordinated
major GC (IE and pre-Oilpan Chrome leaked rings forever). What still leaks
is what this reproducer builds: a **rooted chain** — the listener's owner
(`body`) is alive, so the closure is live, so the wrapper is live, so 10,001
nodes are live. The collectors are working correctly; the program is keeping
garbage reachable. That is why the fix is program-side: remove the listener
(`removeEventListener`, or register with `addEventListener('click', fn,
{ signal: controller.signal })` and abort on teardown), or let the closure
capture nothing that outlives the subtree.

## Reading the heap snapshot: three numbers and a delta

The instrument for all of this is DevTools' Memory panel, with three numbers
per object you must be able to read:

- **Shallow size** — the object's own bytes. A DOM wrapper's row carries the
  C++ node's bytes too (external memory), which is why a detached node shows
  a non-zero shallow size.
- **Retained size** — everything that dies if this object dies; computed from
  the dominator tree (the object is the *unique* gate those bytes pass
  through). For the detached div: itself plus its 10,000 spans, ~0.8 MB on
  one row.
- **Distance** — the shortest path, in objects, from a GC root. Live DOM
  nodes sit at small distances (4–8 is typical); a large distance on
  something you expected to be garbage is the smell.

The full workflow is the lab below; the two views that matter are the
**Detached** filter (class names literally begin with "Detached" —
`Detached HTMLDivElement`, `Detached HTMLSpanElement`) and the **Retainers**
pane, which names the chain — for this leak, roughly `Detached
HTMLSpanElement <- first_child of Detached HTMLDivElement <- onClick <-
event_listeners of HTMLBodyElement <- window` (labels vary slightly by
version). That retainer line is the bug report, pre-written by the engine.

## What insiders know

- **`console.log` is a root.** Objects logged to the console are retained
  *while DevTools is open* — snapshots taken with the console showing them
  "leak" exactly those objects, retainer chains ending in the console. The
  false leak that eats an afternoon: repro visible only when DevTools is
  open, flat memory in closed-console soak tests.
- **Finalizer timing is a lie by design.** `FinalizationRegistry` and
  `WeakRef` callbacks run only after a major GC that notices the death. V8
  schedules GCs from allocation pressure plus a memory-reducer heuristic on
  a seconds-class timer that only fires when it predicts a win — an idle
  page (a dashboard that stopped allocating) can go many minutes, the
  "finalizer didn't fire for 20 minutes" story, with no GC at all. Unreachable
  is not collected; collected is not finalized; finalized is not soon.
- **`performance.measureMemory()`** exists (Chrome 89, 2021, Chromium-only)
  but only on cross-origin-isolated pages (COOP+COEP headers) — which is
  chapter 05's isolation machinery gating a memory API.
- **Snapshot perturbation.** Taking a heap snapshot forces a GC first, and
  the snapshot itself allocates — baselines for the three-snapshot technique
  must be snapshot-to-snapshot deltas, never absolute totals.
- **The crash signature of a missing barrier** is not a leak but a Blink
  use-after-free: an object swept while reachable (`heap-use-after-free in
  blink::...` in crash reports). The write barrier is memory-safety
  machinery, not a performance detail — the same class of bug module 01
  chapter 15's V8 barrier prevents on the other heap.
- **A strong `v8::Persistent` is a permanent root.** Embedder code holding
  one per cached object re-creates the IE leak on a modern engine: the
  collector cannot question it, only the code that drops it.

## See it yourself

Everything runs in any Chrome/Chromium; the instrument is built in. Save the
HTML above as `leak.html`, open it, and:

1. **Identity check first** (console, the wrapper cache made visible):
   `document.body === document.body` → `true`;
   `document.querySelector('button') === document.querySelector('button')`
   → `true` (same node, same wrapper); two `createElement('div')` calls →
   `false` (two nodes, two wrappers).
2. **The leak, measured.** DevTools → Memory → *Heap snapshot* → Snapshot 1
   (it forces a GC first — let it). Click **leak once** three times, take
   Snapshot 2, and type `Detached` in the Summary-view class filter: expect
   ~30,000 rows (`Detached HTMLSpanElement` × ~30,000, `Detached
   HTMLDivElement` × 3) — counts vary with Chrome version, the shape does
   not.
3. **Read the retainer.** Click one `Detached HTMLDivElement`: Shallow in
   the tens-of-bytes wrapper + external C++ class; Retained ~0.8 MB (its
   10,000 spans); the Retainers pane shows the chain through `onClick` to
   `event_listeners` on the live body — the diagram from the previous
   section, printed by the engine.
4. **The delta view.** Switch the view dropdown to *Comparison* / "Objects
   allocated between Snapshot 1 and Snapshot 2": the # delta for `Detached
   HTMLSpanElement` should be ~+30,000, the size delta in the 6–9 MB class
   for three clicks (version-dependent).
5. **Prove the fix.** Click **clean variant** the same number of times,
   snapshot again, compare: `Detached` rows near zero, delta near zero. Same
   DOM work, same detach — no capture, no leak.
6. **Watch the scheduler sit idle.** After the clicks, note the timestamp and
   wait: `Detached` rows persist for minutes on an otherwise idle page — no
   allocation pressure, no major GC, which is the finalizer lesson in
   instrument form. (The *Allocation instrumentation on timeline* mode shows
   the wrapper allocation stacks if you want the forest, not the trees.)

## How to use this knowledge

- **The "memory grows per navigation" SPA incident.** You can now run the
  full diagnosis: three snapshots, Detached filter, retainer chain — and the
  chain names the listener to remove. The fixes (remove the listener, abort
  its signal, stop the capture) fall out of reading the chain as a
  root→wrapper→node path.
- **Listener architecture.** Per-node listeners on a 10,000-row list mean
  10,000 listener records and 10,000 chances for one closure to capture the
  wrong thing. Event delegation (one listener on the container using
  `event.target`) is not a style preference; it removes 9,999 cross-heap
  chains by construction.
- **Teardown code review.** The question that catches this bug class is
  mechanical: *does any closure that outlives this subtree capture a node
  inside it?* — asked whenever a route or component unmounts.
- **The interview answer.** "Chrome has two tracing collectors per renderer;
  they share DOM nodes through V8 wrappers, coordinated by embedder tracing
  — V8 marks live wrappers, Blink marks the nodes behind them, Oilpan marks
  JS listeners back into V8, and they iterate to a fixed point" — three
  sentences that separate senior browser knowledge from framework
  knowledge.
- **Cache design.** A WeakRef-based cache is fine for *latency* (recompute a
  dead entry) but never the *memory* story: bound it by count or bytes with
  an LRU, because eviction depends on a GC an idle page may not schedule
  for minutes.

## Connections

- **Module 16 ch01** — the renderer process is the leak's blast radius: a
  per-navigation leak accumulates until tab close, because the process (not
  the page) owns both heaps.
- **Module 16 ch03** — detached nodes have `layout_object_ == null`: chapter
  03's invalidation flags are why this leak costs zero frames and hides
  from rendering profiling.
- **Module 16 ch04** — the detached subtree is entry one in the leak
  taxonomy; the three-snapshot technique returns there for workers and WASM
  memory.
- **Module 16 ch05** — cross-origin isolation gates `performance.measureMemory`;
  the V8 sandbox's external pointer table holds the wrapper's slot-0 handle —
  the bridge's raw pointer becomes a capability.
- **Module 01 ch15** — V8's generational scavenger, its remembered-set write
  barrier, the old/young split: the same contract Oilpan implements on the
  C++ side.
- **Module 01 ch19** — the size-class allocator: Oilpan's bucketed pages are
  that pattern re-derived for a collector.
- **Module 10 ch04** — compiler-inserted barriers in managed runtimes: here
  the "compiler" is the `Member<T>` template plus macros, and the developer
  writes the `Trace()` method the JVM's JIT would have generated.

## Quiz

1. **Concept.** V8 can trace the V8 heap; Oilpan can trace the Blink heap.
   Name the two directions of liveness evidence that cross the wrapper
   bridge during one coordinated major GC, and explain why neither
   collector is allowed to treat the wrapper's slot-0 pointer as "just a
   pointer."
2. **Predict the number.** The leak page's subtree holds 10,000 bare
   `HTMLSpanElement`s plus one root div (bare Element ≈ 80 B of C++ memory,
   2024-era). Roughly how much Blink-side memory does one leak instance
   retain? If DevTools reports a size delta of ~2–3 MB per instance, what
   accounts for the difference?
3. **Predict the output.** A page has exactly one `<div>` in its body.
   What does this print, and why — one line of reasoning per line?
   ```js
   const a = document.querySelector('div');
   const b = document.querySelector('div');
   console.log(a === b);
   console.log(document.createElement('div') === document.createElement('div'));
   console.log(document.body === document.body);
   ```
4. **Diagnose.** An SPA gains ~1 MB per route change. Route teardown calls
   `container.replaceChildren()` (emptying the container), but snapshots
   accumulate `Detached HTMLDivElement` rows whose retainer chains end in
   `event_listeners` on a live element. Why doesn't `replaceChildren` help,
   and what is the minimal fix?
5. **Diagnose.** A dashboard registers a `FinalizationRegistry` callback
   that logs "freed" and drops a cache entry. After the page sits idle an
   hour, nothing has logged, `WeakRef.deref()` still returns objects, and a
   heap snapshot shows the entries unreachable. Explain the scheduler, not
   the semantics.
6. **Concept.** Classify each pointer in a Blink-flavored program:
   (a) a `Member<Element>` child field on an on-heap `ContainerNode`;
   (b) a `Node*` held in a local variable of a C++ function that may cross
   a GC point;
   (c) a node pointer stored in a non-GC'd manager object that must keep
   the node alive indefinitely;
   (d) a cache entry in an on-heap map that should *not* keep its target
   alive.
7. **Diagnose.** QA reports a memory "leak" that reproduces only when
   DevTools is open: snapshots grow by every object the page logs. With
   DevTools closed, a 12-hour soak shows flat memory. The page calls
   `console.log(node)` for every node it creates. What is actually retaining
   the objects, and how do you prove it to QA?
8. **Cross-chapter.** Module 01 chapter 15 derived V8's remembered-set write
   barrier; Oilpan uses a combined marking+generational barrier on `Member`
   stores. In a hypothetical build the *marking* half is broken. A black
   `ContainerNode` (already marked) gains a new child after the marker
   passed it; the child is white and reachable only through that parent.
   What happens at sweep time, what does the user see, and why won't the
   heap snapshot show anything "missing"?

<details><summary>Answers</summary>

1. **Evidence flows both ways.** JS → C++: when V8's marker finds a wrapper
   live, V8 hands it to Blink's `EmbedderHeapTracer` implementation, which
   reads slot 0 and marks the node as an Oilpan root (then Oilpan traces
   `Member` edges through the DOM). C++ → JS: when Oilpan traces an on-heap
   listener map, the V8 function references inside it are marked in V8's
   heap, keeping the closure live; the two iterate to a fixed point. Slot 0
   cannot be "just a pointer" for V8: the target lives in a heap V8 does not
   own — opaque data would orphan the DOM side, an unconditional root would
   leak every node forever.
2. **~0.8 MB, call it ~1 MB per instance** on the C++ side: 10,001 nodes ×
   ~80 B ≈ 800 KB. The extra ~1–2 MB is the V8-side tax — one wrapper per
   touched node, the closure, the listener record — plus external memory
   attributed to wrappers. Both numbers are true: "one listener leaks ~1 MB
   of DOM" and "the tool shows ~2–3 MB."
3. **true, false, true.** First: two lookups return the *same node*, and
   the node→wrapper cache guarantees the same wrapper object — identity is
   exactly what the cache exists to provide. Second: two `createElement`
   calls construct two distinct C++ nodes, so two wrappers — `false`.
   Third: `document.body` is one node accessed twice; the cache mints the
   wrapper once and re-returns it — `true` on every call, forever.
4. **`replaceChildren` detaches; it does not de-root.** The container drops
   its parent-side references, but the retainer chain shows the real owner:
   a listener on a *live* element whose closure captures the detached
   subtree. Liveness flows root → `event_listeners` → closure → captured var
   → wrapper → node → children. Minimal fix: remove the listener at teardown
   (`removeEventListener`, or an `AbortController` signal aborted on unmount),
   or rewrite the closure to capture nothing that outlives the route.
5. **Unreachable is not collected; collected is not finalized.** V8 runs
   major GCs when allocation pressure or the memory-reducer heuristic (a
   seconds-class timer that only fires when it predicts a worthwhile
   reduction) says so. An idle dashboard allocates ~nothing, so no trigger
   fires and no marking pass ever notices the entries are unreachable —
   `deref()` keeps succeeding because the target was never swept. Fix the
   cache with an explicit bound (count/bytes + LRU); never make correctness
   depend on finalizer timing.
6. **(a)** `Member<Element>` — an on-heap edge, followed by the tracer via
   the class's `Trace()` method. **(b)** A raw `Node*` on the C++ stack —
   Oilpan scans the mutator stack conservatively at a GC point, so plain
   locals are roots without ceremony (why rendering code may hold `Node*`
   across safepoints). **(c)** `Persistent<Node>` — a root by declaration;
   the collector never questions it, so the manager must eventually clear
   it or the object goes immortal, IE-style. **(d)** `WeakMember<T>` — an
   on-heap weak edge, cleared before the target is swept, so the cache entry
   cannot keep the target alive.
7. **The console is the root.** While DevTools is open, objects logged to
   the console are retained by the console itself — the "retainers" chain
   for those objects ends there. That is the proof: show QA the retainer
   pane naming the console, then repeat the soak with the console cleared
   and watch memory stay flat. The snapshot procedure itself also forces a
   GC and allocates, so absolute totals between runs are not comparable —
   only deltas.
8. **The white child is swept although reachable** — the black parent was
   already marked, the marker will not revisit it, and with the marking half
   of the barrier broken the store never forces the child to be marked. At
   sweep the child's slot is freed and later reused. The user sees a crash,
   not a leak: the classic `heap-use-after-free in blink::` signature. The
   snapshot shows nothing "missing" because the object was *never marked* —
   the snapshot shares the collector's broken liveness view. This is the
   exact failure the barrier exists to prevent, the same race module 01
   ch15's V8 barrier kills on the JS heap and module 10 ch04's JIT-inserted
   barriers kill in the JVM.

</details>
