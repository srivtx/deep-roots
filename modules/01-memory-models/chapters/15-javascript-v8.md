# Chapter 15 — JavaScript: V8's hidden classes and the scavenger

> **The 30-second version.** ECMAScript says nothing about memory — no `sizeof`, no
> address-of, no `free` — so every engine invents its own heap. V8 (Chrome, Node, Edge,
> Electron) answers the four questions like this: a *value* is a tagged word — a Smi is
> the integer itself packed into the tag bits and never allocated, everything else is a
> pointer whose first target word is a **map**, the hidden class. Because the spec makes
> objects string-keyed dictionaries, V8 turns property lookup into *map check + fixed
> offset load* — that is what hidden-class transition trees, inline caches, and elements
> kinds exist for, and why insertion order and `delete` decide whether your object access
> runs at load speed or hash speed. The heap is generational like the JVM's (ch12): two
> equal semispaces scavenged by Cheney copying (survive twice → old space), then
> mark-compact with time-sliced concurrent marking. Leaks are *reference* leaks through
> closures' heap-allocated Contexts and listener registries; ArrayBuffers park their
> bytes outside the heap. Numbers: V8 as of the Node 20 / Chrome 120 era (2023–2024),
> 64-bit, unless a line says otherwise.

## 1. The spec is silent: one language, many heaps

Part II's four questions — what is a value, who owns it, who frees it, what safety —
have no ECMAScript answer. The spec defines *observable behavior*: which properties a
`{}` has, what `===` sees, in what order `for-in` enumerates. It never defines a byte, an
address, or an object size, and it has no `sizeof` or `free` to violate. So the same
source file runs on three unrelated heaps: **V8** (Chrome, Node, Edge, Electron, Deno),
**SpiderMonkey** (Firefox), and **JavaScriptCore/JSC** (Safari). "JavaScript memory
model" is shorthand for one engine's memory model; this chapter is V8's.

The engines differ at the value level, not just the GC level. JSC and SpiderMonkey use
**NaN-boxing**: one 64-bit word per value; doubles stored as themselves, pointers folded
into quiet-NaN bit patterns (exponent all ones) that real doubles almost never collide
with — chapter 06's tag-bit idea, executed in floating-point space. The price: every
double must be checked for being pointer-shaped and "quieted," and the payload caps at
48 bits. V8 chose the opposite: **pointer tagging** — heap slots hold tagged words,
general-heap doubles are real objects (HeapNumbers), and only double *arrays* get
unboxed (section 4). Same language, different heaps, different constants.

V8's four answers, previewed: a value is a tagged word (Smi or pointer); nobody owns
anything (no `free`, no `delete`); reachability frees — a generational, compacting
tracing collector; and safety is complete at the language level (no pointer arithmetic,
no type casts), enforced *by the engine's own map checks* — which is why the map word,
next, is the most important 8 bytes on the V8 heap.

## 2. Tagged words: Smis, the map word, and what an object costs

Derive the Smi before admiring it. Counters, array indices, lengths, loop variables:
small integers are the most common values in real programs. If every `i` in
`for (let i = 0; ...)` allocated an object, each iteration would pay a bump allocation
plus a header plus later collection (ch12 priced Java's cheapest `new` at ~10 ns; chapter
14 showed CPython paying 28 bytes per `int`). So small integers must be *immediates* —
values packed into the slot itself, never heap objects. But one slot must also be able
to hold a pointer. That is a two-case sum type, and chapter 06 already taught its
encoding: tag bits. V8 reserves the **low bit**: 0 = Smi, 1 = heap object.

```text
A tagged slot holding any JS value, 64-bit V8 — two shipped configurations:

  pointer compression ON (Chrome since 80)      compression OFF (official Node builds)
  +------------- 32 b -----+                    +-------------- 64 b ---------------+
  |  31-bit int  << 1  | 0 |  Smi               | 32-bit int |  32 zero bits  | 0   | Smi
  |  cage offset       | 1 |  pointer           | full 64-bit address          | 1   | ptr
  +------------------------+                   +------------------------------------+

  A Smi is "value shifted left, tag 0": arithmetic survives. x<<1 + y<<1 == (x+y)<<1,
  so Smi add never untags. A pointer is "address | 1": objects are 8-byte aligned, so
  the low 3 bits are provably 0 and setting bit 0 is lossless — untag = mask low bit.
```

Two consequences worth money. First, the Smi *range* depends on the build: 31-bit
payload in Chrome (max `2^30 − 1` = 1,073,741,823), 32-bit payload in Node's
uncompressed word (max `2^31 − 1` = 2,147,483,647; observed: `%DebugPrint(2147483647)`
prints `Smi`, one more boxes a HeapNumber) — the same source line, two heaps. Second,
an unboxed double cannot be a tagged word, so a number that is not a Smi is a
**HeapNumber** — a real object, map word + 8-byte payload. `2**31` costs an allocation;
in Chrome, so does `2**30`.

Every heap object — HeapNumber, string, JSObject, the Maps themselves — starts with the
**map word**: a tagged pointer to a **Map**, V8's hidden class. Compare the three
headers you now know:

- **PyObject** (ch14): 16 bytes — refcount + type pointer. The refcount exists because
  CPython frees deterministically on zero.
- **JVM object** (ch12): 12 bytes — mark word (identity hash, 4-bit GC age, lock bits)
  + compressed class pointer. The lock bits exist because threads synchronize on
  objects; the age field drives tenuring.
- **V8 JSObject**: map word + properties pointer + elements pointer — **24 bytes** in
  Node's uncompressed build, **12** in Chrome's compressed one — and *nothing else*.
  No refcount: V8 traces, so no counter is maintained. No lock bits: an *isolate* (V8's
  VM instance) runs one JS thread; nothing synchronizes on a plain object. No age bits:
  the semispaces themselves are the age counter (section 5 — V8 needs no JVM-style
  4-bit field, and 15 vs 2 collections is a real design fork, quiz 8). Even identity
  hashes are outsourced: an object with no out-of-line properties stores its hash in the
  otherwise-empty *properties* slot (V8's `raw_properties_or_hash`). And during a
  scavenge the map word is overwritten in place with a **forwarding pointer** — the map
  word moonlights as the "I already moved, here's where" note of Cheney's algorithm
  (ch12 section 4).

```text
  +----------------+------------------+------------------+---------------------+
  | map word (8 B) | properties (8 B) | elements (8 B)   | in-object fields .. |
  +----------------+------------------+------------------+---------------------+
   ^ hidden class      ^ named props        ^ indexed props    ^ this.x, this.y
     (a Map object);      backing store /      (FixedArray /       live HERE, not in
     becomes GC           identity hash        FixedDoubleArray    a string-keyed
     forwarding ptr                             / dictionary)       dictionary
```

Named properties live in *in-object slots* up to a pre-allocated count, then spill to
the properties array. Observed on Node 24 / V8 13.6 (uncompressed): `{}` is
**56 bytes** (24 header + 4 spare in-object slots × 8), `{x: 1, y: 2}` is **40**
(24 + 2 slots), a `JSArray` is **32** (header + length), a HeapNumber **16**. In
Chrome's compressed build the same objects are 28, 20, 16, and 12 bytes. "How big is a
JS object" is a question about the *build*, not the language.

## 3. Hidden classes: from string hashing to a guarded load

The spec makes every object a dictionary keyed by strings. Naive `o.x` would hash the
string `"x"`, probe a table, compare strings — every single time. That is chapter 05's
hash-lookup cost paid on the most common operation in the language. V8's fix is the
**Map**: an object's shape — *which* properties, in *which* slots, with *which*
representation — captured in one shared, immutable descriptor list. Two objects with
the same map have the same layout, so `o.x` compiles to: check map, load slot 0.

Shapes are built, not declared. Adding a property to an object transitions it from its
current map to a new map along an edge labeled with the property name — and edges are
**shared**: a second object taking the same transition lands on the *same* map. The
result is a **transition tree** per root shape, not a map per object:

```text
  the canonical path (add x, then y, then z) — one map per shape, shared by
  every object that walks it in this order:

  M0 {} --+"x"--> M1 {x:0} --+"y"--> M2 {x:0,y:1} --+"z"--> M5 {x:0,y:1,z:2}

  a different insertion order (y, then x) forks at the root and never reconverges:

  M0 {} --+"y"--> M3 {y:0} --+"x"--> M4 {y:0,x:1} --+"z"--> M6 {y:0,x:1,z:2}

  shared prefixes share maps; diverged paths never rejoin. M5 and M6 hold the
  SAME three properties with DIFFERENT slot numbers — two hidden classes for
  one property set, forever.
```

```js
function Point(x, y) { this.x = x; this.y = y; }  // one constructor = one path
const a = new Point(1, 2), b = new Point(3, 4);   // same map: sites stay monomorphic
const c = {};  c.y = 2; c.x = 1;                  // forked map: sites turn polymorphic
```

Verified in the lab below with `%HaveSameMap`: `a,b` → `true`; `a,c` → `false` — and
they *stay* different after both gain `z`, because `M2 --+z--> M5` and `M4 --+z--> M6`
are different edges. Same properties, same count, different shape: **two objects with
different insertion histories are different hidden classes**, and a load site compiled
for `M2` must reject `M4` (slot 0 means `x` in one and `y` in the other).

The speedup is the **inline cache (IC)**. At each property-access site, V8 caches
(map → offset) pairs: first execution records the map and the resolved offset; later
executions just *compare the map and load the offset*. The ladder:

- **monomorphic** — 1 map: a compare plus a load; TurboFan inlines it and the offset
  becomes a constant in machine code.
- **polymorphic** — 2–4 maps: a short compare chain. Still cheap.
- **megamorphic** — 5+ maps: the site gives up and consults a global *stub cache*
  hashed by (map, name) — a hash lookup plus a call, and TurboFan can no longer
  constant-fold the offset. That is the miss cost: several-fold to an order of
  magnitude per access — and it does not heal itself.

Now derive the folklore instead of asserting it. **"Always create objects the same
way"** — same assignment order walks every instance down one path to one map, so use
sites stay monomorphic; add properties conditionally (`if (opts.z) this.z = ...`) and
you fork the tree per call pattern. **"Never `delete`"** — a slot cannot simply be
dropped (offsets would shift), so V8 *normalizes* the object to dictionary mode
("slow properties"): every access through the string hash table you were avoiding
(verified in the lab: `%HasFastProperties(o)` flips `true → false` after one `delete`).
Assign `null`/`undefined` instead. **"JSON with optional fields is a shape bomb"** —
`JSON.parse` materializes whatever keys each payload carries, so one hot accessor sees
dozens of maps and goes megamorphic; normalize after parsing (assign every field,
always).

One more derivation hides in constructors: V8 cannot know how many in-object slots a
constructor's instances will eventually need, so the first instances get **slack** —
extra empty slots — and after roughly the first 8 constructions (slack tracking) the
map shrinks to the observed shape. Observed: `new Pt(1,2)` = **104 bytes** (10
in-object slots) on first construction, **40 bytes** (2 slots) after 9 — the early
instances keep the fat layout for life.

## 4. Elements kinds: the other half of every object

Named properties were only half the object — the elements pointer (the third header
word) holds the *indexed* properties: `a[0]`, `a[1]`, …. Arrays are so hot that V8
tracks their element type in the map itself, as an **elements kind**, and the kinds
form a one-way ladder:

```text
  PACKED_SMI_ELEMENTS  -->  PACKED_DOUBLE_ELEMENTS  -->  PACKED_ELEMENTS
  (tagged Smis only)        (raw 8-byte doubles,         (any value: tagged
                            UNBOXED — FixedDoubleArray)   pointers — FixedArray)

  any hole (a[10] on a 3-element array) forks to the HOLEY_* sibling of the current
  kind; a huge gap (index > length + ~1024, approximate) jumps straight to
  DICTIONARY_ELEMENTS: a hash table of {index -> value}, for sparse arrays.
```

Every write that does not fit the current kind transitions the array *down* the
ladder, and the ladder only goes down. The payoff for staying high: a `PACKED_DOUBLE`
array stores raw IEEE-754 doubles — **no HeapNumber, no map word, no 16 bytes per
number** — the same raw Float64 bytes as a C `double[]` (chapter 06's boxing lesson,
dissolved). The cost of falling: one object stored into a double array transitions it
to `PACKED_ELEMENTS` and *boxes every existing double into a fresh HeapNumber* — a
single write can allocate a million objects.

```js
const xs = [1, 2, 3];      // PACKED_SMI_ELEMENTS — FixedArray of tagged Smis
xs.push(4.5);              // -> PACKED_DOUBLE — FixedDoubleArray; 1, 2, 3 are now
                           //    raw 1.0, 2.0, 3.0 (8 bytes each)
xs.push("4");              // -> PACKED — FixedArray of pointers; 4.5 is re-boxed
                           //    into a HeapNumber (map word + double)
xs[100000] = 1;            // gap >> 1024 -> DICTIONARY_ELEMENTS: NumberDictionary,
                           //    and indexed reads become hash lookups
```

Now derive the second piece of folklore: **"don't mix types in an array"**. It is not
style — the first `push` of a non-number demotes the array to tagged pointers and
re-boxes every double; the first string index far from the end demotes it to a
dictionary. Type-stable arrays run at memcpy speed; mixed arrays run at allocation and
hash speed. (Strings and objects share `PACKED_ELEMENTS`, so mixing those two is free —
the cliff is between numbers and everything else, and between Smi and double.)

Typed arrays (section 5) are the escape hatch: a `Float64Array` *cannot* transition —
its element type is fixed by construction, which is exactly the guarantee the ladder
is trying to reconstruct.

## 5. The spaces: semispaces, the scavenger, and bytes outside the heap

Same bet as chapter 12 — most objects die young — but different geometry. **New
space** is two *equal* semispaces (1:1, not the JVM's 8:1:1 eden:survivor split):
allocation bump-points through the active one; when it fills, the **scavenger** runs
Cheney's algorithm — trace roots, copy live objects breadth-first into the idle
semispace (the to-space *is* the work queue), leave forwarding pointers in their map
words, swap the labels, abandon the rest wholesale. Cost tracks survivors, not
garbage; compaction is free; fragmentation cannot exist in new space.

```text
   new space = two equal semispaces (flip every scavenge)
   +---------------------------+   +---------------------------+
   | ACTIVE: bump-pointer      |   | IDLE: empty               |
   | #### live #### garbage ## |   |                           |
   |                ^ top      |   |                           |
   +---------------------------+   +---------------------------+
        scavenge: roots + remembered set -> copy live to the idle space
        (forwarding pointers in map words), swap, abandon the rest.

        an object's 1st survival  -> copied to the other semispace
        its        2nd survival   -> promoted to OLD SPACE, not copied again
        -> an object is young for exactly 2 scavenges, no header bits needed:
           WHICH semispace an object sits in IS the age. (JVM: 4-bit field, 15.)
```

Surviving *two* scavenges promotes to old space — after its first scavenge the object
already sits in the semispace that will be `from` next time, so the second sends it up
instead of across. The JVM burns 4 header bits to make age tunable to 15; V8 spends
zero and fixes it at 2 — one reason its header can be map-word-only.

The scavenger must find every old→new pointer without scanning old space (the whole
point of "minor"). Same answer as ch12's card tables, different encoding: every
reference store into an old object runs a **write barrier** that records the exact
slot in the **store buffer**, later refined into per-page **remembered sets**; the
scavenger then visits just those slots. V8 records slots, not 512-byte cards — and the
same barrier machinery doubles for concurrent marking's tricolor invariant (ch11's Go
barrier is the cousin). The pause: scavenges are stop-the-world but tiny — observed on
Node 24: **0.06–0.55 ms** each, parallel with helper threads.

**Old space** is mark-compact, time-sliced. When allocation pushes old space past its
limit, a **major GC** starts **incremental marking**: marking interleaves with JS in
small steps while concurrent marker threads crawl the object graph off-thread; a brief
final pause finishes and **evacuates** fragmented pages (compaction by moving whole
pages), and **sweeping** runs concurrently afterwards. The trigger is a **heap growing
factor** heuristic: after each major GC, V8 sets the next limit from the new live size
(survival) and observed GC throughput — grow the heap more when collection is cheap
relative to allocation, less when it hurts — bounded in Node by `--max-old-space-size`
(the overall cap is `v8.getHeapStatistics().heap_size_limit`; observed 2213 MB on one
64-bit box). A real major GC, from the trace: `Mark-Compact 178.8 (269.3) -> 148.2
(276.9) MB ... 43.87 / 0.01 ms (+ 138.4 ms in 661 steps ... biggest step 29.3 ms ...)`
— 661 time-sliced steps, ~138 ms of marking spread out, one final ~44 ms pause.
Young GC is ~ms; old GC is incremental.

The remaining spaces: **large object space** for objects over roughly 128 KiB
(`kMaxRegularHeapObjectSize` — approximate), whole-page and never moved — copying
multi-MB objects would blow the pause budget, so like G1's humongous allocations
(ch12), big temporaries are old-gen pressure in disguise; **code space**, executable
and separately write-protected (W^X); and read-only space for the immortals
(`undefined`, the empty FixedArray).

**ArrayBuffers live outside all of it.** A `new ArrayBuffer(50 * 1048576)` creates a
*small* GC-managed wrapper plus a backing store from the embedder's allocator —
`malloc`'d system memory (ch05/ch19 territory), invisible to the tracer: the GC scans
the wrapper, never the bytes. V8's **external memory accounting** makes 200 MB of
buffers pressure GC triggers even though `heapUsed` doesn't move — verified on Node 24:
4 × 50 MB buffers took `external` from **1.9 MB to 201.9 MB** while `heapUsed` sat at
~4 MB. The bytes free when the wrapper dies *and a GC notices* — the same caveat as
Java's direct buffers (ch12). Typed arrays (`Float64Array` etc.) are thin *views* —
length, byteOffset, base pointer — borrowing the backing store without copying:
chapter 13's spans and chapter 14's buffer protocol, invented independently a third
time. This is JS's zero-copy bridge: transfer an ArrayBuffer to a worker (ownership
moves, no memcpy), share a `WebAssembly.Memory`, or point four views at one buffer —
and, structurally, it is JavaScript's one fixed-record type: chapter 17's COBOL,
smuggled in as an API.

## 6. Closures and retention: why the listener keeps the object alive

Chapter 04's stack frame dies at `return`. So a local variable captured by a closure
cannot live in the frame — it must move to a heap object whose address is stable: V8
allocates a **Context** (verified in the lab: a closure's `%DebugPrint` shows
`context: ... <FunctionContext[3]>`) — a small FixedArray-shaped object holding one
slot per captured variable plus a link to the enclosing context. Every closure that
captures a variable carries a pointer to that Context, and the context chain *is* the
scope chain.

The leak follows mechanically. A Context is one object holding *all* of a scope's
captured variables — V8 does not track which variables *this particular* closure
actually uses, so **one live closure retains every sibling captured variable**, the
whole chain of outer contexts, and everything they reference:

```js
function attach() {
  const rows = new Array(1_000_000).fill(0);   // ~4-8 MB
  const config = loadConfigForThisPage();      // also captured: also retained
  document.getElementById("dump")
    .addEventListener("click", () => render(rows, config));
}
```

The event loop is not a memory system, but *retention through it is a memory topic*.
The platform keeps registries — an `EventTarget`'s listener list, the timer table
behind `setInterval`, pending promise jobs, `window` itself — and those registries are
GC roots. The reference chain reads like an indictment:

```text
  window -> document -> <button> -> listener list -> arrow function
         -> Context{rows, config} -> the 4-8 MB array
```

Remove the button from the DOM and it does *not* die: the listener registration keeps
the detached node alive — DevTools heap snapshots literally list it as "Detached," the
classic diagnosis. A forgotten `setInterval` is the same shape through the timer table.
The collector is *right* every time — chapter 12's lesson verbatim: reference leaks,
not GC bugs. Every fix is an edge deletion: `removeEventListener` (requires a named
handler — anonymous closures cannot be removed), `clearInterval`, `{once: true}`, or
restructuring so the registry never points at the big data. The full walkthrough —
snapshot, Retainers, fix, re-snapshot — is the lab below.

One retainer hides in plain sight: `console.log(bigThing)` keeps `bigThing` alive for
as long as the DevTools console holds the entry. Half of all "leaks" that vanish when
you close DevTools were never leaks — snapshot with the console cleared.

## What insiders know

- **Two Smi ranges, two shipped V8s.** Chrome ships pointer compression (since Chrome
  80): 4-byte tagged slots, 31-bit Smis (max 1,073,741,823), 4-byte map words, `{}` =
  28 bytes. Official Node builds ship compression *off*: 8-byte tagged words, Smis
  with a 32-bit payload in the top half (max 2,147,483,647 — `%DebugPrint(2147483647)`
  prints `Smi`; `2147483648` prints a HeapNumber), 8-byte map words, `{}` = 56 bytes.
  "How big is a JS object" depends on which product you're running.
- **Shallow vs Retained size is a dominator computation.** Shallow: the object's own
  bytes. Retained: shallow plus everything that would be freed if this object died —
  the objects it *dominates*. The trap: one extra incoming reference from anywhere can
  collapse a 40 MB retained size to 40 KB, so "retained" is a fact about the whole
  graph at snapshot time, not about the object.
- **Slack tracking: the first ~8 instances of a class are fatter.** Observed:
  `new Pt(1,2)` — instance size 104 bytes (10 in-object slots); after 9 constructions,
  40 bytes (2 slots). V8 over-allocates constructor maps, shrinks after the trial
  period, and never resizes the early instances.
- **Maps deprecate.** A field's representation generalizes Smi → double → tagged on
  the first offending write; the old map is marked deprecated and live instances
  migrate lazily on their next access. Maps also chain *backwards* (each map stores
  its back pointer) and feed `Object.keys`/`for-in` enumeration order — fast paths
  only exist while shapes stay stable.
- **The IC ladder's cliff is at 5.** 1 map: monomorphic (guard + load, offset is a
  constant in machine code). 2–4: polymorphic compare chain. 5+: megamorphic — global
  stub cache keyed by (map, name), a call instead of a load, and TurboFan loses offset
  inlining. Sites do not un-megamorphize; only deopt/reopt cycles reset them.
- **Strings are a type system: SeqOneByte/TwoByte, ConsString, SlicedString,
  ThinString.** Concatenation builds a ConsString (a rope) in O(1) — but only when
  both sides are ≥ 13 chars, else it copies. `s.length` is O(1) on every type because
  each carries its length; a ConsString's is the cached sum. Flattening (regex,
  indexing) rewrites the rope in place. `s.substring(0, 5)` of a 100 MB string makes a
  SlicedString (for results ≥ 13 chars) — 24 bytes, no copy, **and it keeps the whole
  100 MB parent alive**. Internalization leaves a ThinString forwarding to the
  canonical copy behind.
- **Young-generation arithmetic is one division.** Scavenge frequency = allocation
  rate ÷ active semispace. Default max semispace is 16 MiB on large 64-bit heaps (V8
  as of the Node 20 era; scales down on small ones) — tune with
  `--max-semi-space-size` (MiB). Bigger semispaces: fewer, slightly longer scavenges
  and longer average youth (promotion is scavenge-count-based).
- **`console.log` is a root.** The DevTools console retains every logged object until
  cleared — clear it before trusting a heap snapshot.

## See it yourself

All Node output below is real, from Node 24 / V8 13.6, 64-bit, uncompressed build —
output trimmed to the teaching lines.

```bash
# 1. Elements kinds and the ladder — the map IS the shape:
$ node --allow-natives-syntax -e '
const a = [1, 2, 3];   %DebugPrint(a);
a.push(4.5);           %DebugPrint(a);
a.push("x");           %DebugPrint(a);'
DebugPrint: 0xfbda70e69c9: [JSArray]
 - map: 0x0e7d2fb5b0f9 <Map[32](PACKED_SMI_ELEMENTS)> [FastProperties]
 - elements: 0x364dee8eab61 <FixedArray[3]> [PACKED_SMI_ELEMENTS (COW)]
 - length: 3
DebugPrint: 0xfbda70e69c9: [JSArray]
 - map: 0x364dee8c3fd1 <Map[32](PACKED_DOUBLE_ELEMENTS)> [FastProperties]
 - elements: 0x0fbda70e6aa9 <FixedDoubleArray[22]> [PACKED_DOUBLE_ELEMENTS]
 - length: 4
DebugPrint: 0xfbda70e69c9: [JSArray]
 - map: 0x... <Map[32](PACKED_ELEMENTS)> ... <FixedArray[4]>
```

Watch the *same array* swap its map and its backing store twice — `FixedDoubleArray`
holds raw doubles; `COW` (copy-on-write) marks the literal's shared elements.

```bash
# 2. Hidden classes: insertion order decides map identity
$ node --allow-natives-syntax -e '
const a = {}; a.x = 1; a.y = 2;
const b = {}; b.x = 1; b.y = 2;
const c = {}; c.y = 2; c.x = 1;
console.log(%HaveSameMap(a,b), %HaveSameMap(a,c));
b.z = 3; a.z = 3;
console.log(%HaveSameMap(a,b), %HaveSameMap(a,c));'
true false
true false

# 3. delete -> dictionary mode (the "slow properties" cliff)
$ node --allow-natives-syntax -e '
const o = {a:1,b:2,c:3};
console.log(%HasFastProperties(o)); delete o.b;
console.log(%HasFastProperties(o));'
true
false
```

```bash
# 4. The scavenger and the major GC, in the trace
$ node --trace-gc -e '
let junk = [];
for (let i = 0; i < 3e6; i++) { junk.push({i}); if (junk.length > 50) junk = []; }'
[5652:0x7192000] 19 ms: Scavenge 4.8 (5.8) -> 3.9 (6.8) MB, 0.55 / 0.00 ms ... allocation failure;
[5652:0x7192000] 22 ms: Scavenge 4.8 (6.8) -> 3.8 (6.8) MB, 0.08 / 0.00 ms ... allocation failure;

$ node --trace-gc -e '
const keep = [];
for (let i = 0; i < 2e6; i++) keep.push({ i, tag: "item" + (i % 7) });'
[5669:0x7d01000] 537 ms: Mark-Compact 178.8 (269.3) -> 148.2 (276.9) MB, 43.87 / 0.01 ms
  (+ 138.4 ms in 661 steps since start of marking, biggest step 29.3 ms,
  walltime since start of marking 187 ms) ... finalize incremental marking via task;
```

Read them: scavenges are sub-millisecond and fire on "allocation failure" — the bump
pointer hit the semispace end; the Mark-Compact line shows the time-slicing (661
steps, ~138 ms over 187 ms walltime) and the final pause.

```bash
# 5. Off-heap memory: ArrayBuffers never touch heapUsed
$ node -e '
const before = process.memoryUsage();
const bufs = [...Array(4)].map(() => new ArrayBuffer(50 * 1048576));
const after = process.memoryUsage();
for (const k of ["rss","heapUsed","external"])
  console.log(k, (before[k]/1048576).toFixed(1), "->", (after[k]/1048576).toFixed(1), "MB");'
rss       44.7 -> 45.6 MB     # pages fault in lazily as they're touched
heapUsed  4.6 -> 4.1 MB       # the GC heap did not move
external  1.9 -> 201.9 MB     # the backing stores, accounted separately

# 6. (optional) force a GC and see the caps:
$ node --expose-gc -e '
  global.gc();
  const s = require("v8").getHeapStatistics();
  console.log("total_heap_size", (s.total_heap_size/1048576).toFixed(1), "MB",
              "| heap_size_limit", (s.heap_size_limit/1048576).toFixed(0), "MB");'
total_heap_size 6.8 MB | heap_size_limit 2213 MB
```

(`performance.measureMemory()` was the per-measurement variant of this; it is not
exposed in current Node builds — check yours before promising it in a runbook.)

**In Chrome DevTools — the leak-diagnosis walkthrough.** Load this page and click the
button five times:

```html
<button id="grow">attach</button>
<script>
  function attach() {
    const rows = new Array(1_000_000).fill(0);      // captured by the listener below
    document.getElementById("grow")
      .addEventListener("click", () => console.log(rows.length));
  }
  document.getElementById("grow").addEventListener("click", attach);
</script>
```

1. DevTools → **Memory** panel → select **Heap snapshot** → click the trash icon
   (force GC) → **Take snapshot**.
2. Know the two columns first: **Shallow size** is the object's own bytes; **Retained
   size** is shallow plus everything that would be freed if this object died — the
   objects it *dominates*. Now filter the constructor list to `Array` and sort by
   Retained Size: five `Array` entries of ~4 MB each — click `grow` five more times,
   re-snapshot, and the count tracks your clicks: *retained*, not garbage.
3. Select one. The **Retainers** panel (bottom) prints the chain: `rows` in
   `Context` ← the arrow `JSFunction` ← `listener` field of `HTMLButtonElement` ←
   … ← `window`. That chain *is* the leak — every hop is a reference you can delete.
4. For allocation *sites*, record with **Allocation sampling** instead, then click the
   button: entries in blue are still live, gray were collected — the blue stacks point
   at the code doing the retaining.
5. Fix it (one named listener, or `{once: true}`, or remove the old one before
   attaching), force GC, re-snapshot: the arrays are gone. For detached-DOM leaks, the
   same snapshot searched for "Detached" names the nodes still pinned by closures.

The classic three-snapshot variant: baseline snapshot → reproduce → snapshot → GC →
snapshot; anything that grew between snapshots 2 and 3 is retained by something you
did — and its Retainers panel names that something.

## How to use this knowledge

- **Performance reviews and interviews become derivations, not vibes**: same-shape
  construction keeps ICs monomorphic (section 3's tree, not a style rule); `delete`
  and far-index writes have a measured cliff (dictionary mode); one mixed-type `push`
  can allocate a million HeapNumbers (section 4). Derive the Smi from allocation
  cost, hidden classes from "the spec says dictionaries," the 1:1 semispaces from
  "no age field in the header" — and contrast each with the JVM's choice (ch12).
- **Leak triage is reading a Retainers chain**: snapshot → sort by Retained Size →
  walk the chain to the first root you control (listener, timer, console) → delete
  that edge → re-snapshot. Detached DOM nodes and `setInterval` closures are found the
  same way in Node (`--inspect` + Chrome) and in the browser.
- **Node capacity arithmetic**: scavenges per second = allocation rate ÷ active
  semispace; majors per hour = live-set growth against `heap_size_limit`
  (`--max-old-space-size`). RSS climbing while `heapUsed` is flat means *external*
  memory — ArrayBuffers, not JS objects — and no GC tuning will fix it.
- **OOM forensics**: Node's `JavaScript heap out of memory` (after a final full-GC
  attempt) is a *live-set* problem — retainer chains; RSS killed by the OS with a
  healthy-looking heap is a backing-store problem — external accounting.

## Connections

- **Ch 05** — the semispace is the bump allocator; scavenging is the batch "free";
  ArrayBuffer backing stores are plain `malloc` (ch19 deepens).
- **Ch 06** — tag bits (Smi/NaN-boxing), boxing (HeapNumber = PyFloat analog, unboxed
  double arrays = the fix), headers and padding arithmetic.
- **Ch 11** — Go: no generations, no compaction, concurrent tricolor; V8 pays barriers
  for *both* generational and concurrent-marking invariants, and compacts.
- **Ch 12** — Cheney copy, write barriers/remembered sets vs card tables, 8:1:1 +
  age-15 vs 1:1 + age-2, humongous vs large-object space, direct-buffer/ArrayBuffer
  freeing semantics.
- **Ch 13, 14** — typed arrays are the third independent invention of spans/the buffer
  protocol: small GC-managed views over non-GC bytes.
- **Ch 16** — ARC would retain the exact same listener chains — and add cycles; the
  closure leak is an ownership-model-independent fact about registries.
- **Ch 17** — typed arrays as fixed records; the ladder in section 4 is what JS pays
  for records being optional.
- **Ch 21** — V8 exploits are map forgeries: type confusion = making a check see the
  wrong hidden class; the map word is the security boundary.
- **Ch 22 + queued security module** — JSC under Jetsam (iOS) vs V8 under the zygote
  (Android); V8 sandboxing work puts a cage around exactly the pointers section 2
  described.

## Quiz

1. V8's object header is a bare map word — no refcount, no lock bits, no GC age field.
   Derive each absence from V8's design, and name what each of PyObject (ch14) and the
   JVM mark word (ch12) needs that V8 does not.

2. Derive the Smi: why must small integers not be heap objects, why is the tag the low
   bit, and why does Smi addition not need to untag its operands?

3. Predict the output of `%HaveSameMap` for each line, and justify from the transition
   tree:

   ```js
   function P(x, y) { this.x = x; this.y = y; }
   const a = new P(1, 2), b = new P(3, 4);
   const c = new P(4, 5); c.z = 6;
   const d = new P(7, 8); d.z = 9;
   console.log(%HaveSameMap(a, b));   // ?
   console.log(%HaveSameMap(c, d));   // ?
   console.log(%HaveSameMap(a, c));   // ?
   ```

4. Predict the elements kind and where the number 1,073,741,824 (`2^30`) is physically
   stored, for `[1073741823, 1073741824]` on (a) Chrome x64 with pointer compression,
   (b) Node's official x64 build — same array literal, two answers. Also: what happens
   to the array's kind in each case?

5. A SPA's memory grows every time the user navigates; DevTools heap snapshot (console
   cleared) shows dozens of entries reading "Detached" — `HTMLDivElement`, each
   retained through a `Context` by a `JSFunction` on some button's listener list, the
   button itself reachable from `document`. Explain the full chain, why navigating
   away does not free it, and the two-line fix.

6. A hot loop reads `r[i].x` over a million rows and was fast in the benchmark (rows
   built by one constructor). In production it is ~10x slower; the rows come from
   `JSON.parse` of records where `x` is sometimes missing, sometimes a string. Name
   the IC state, what it costs, why the benchmark missed it, and the fix.

7. A Node service's RSS climbs for hours and the kernel kills it, but a
   `--inspect` heap snapshot shows a tiny, healthy `heapUsed`. The code caches decoded
   images as `ArrayBuffer`s in a `Map`. Diagnose the mechanism, explain why no
   `--max-old-space-size` value saves it, and name the number in
   `process.memoryUsage()` that was telling the truth.

8. Cross-chapter (ch12): the JVM's young gen is eden + two survivors at 8:1:1 with a
   4-bit header age field (tenure at 15); V8's new space is two equal semispaces with
   no age field, promoting at 2 scavenges. Derive why V8 can omit both the eden and
   the age bits, and one trade it accepts for that simplicity.

<details>
<summary>Answers</summary>

1. **No refcount** — V8 frees by tracing reachability, so no per-object counter
   exists (CPython needs one because freeing is deterministic refcount-0 events).
   **No lock bits** — an isolate runs one JS thread and nothing synchronizes on a
   plain object; the JVM's mark word doubles as the monitor for `synchronized`, which
   Java permits on every object. **No age field** — the semispace flip *is* the age:
   first survival copies across, second promotes (answer 8); HotSpot counts 15
   collections in 4 mark-word bits to make tenuring tunable. What all three keep: the
   type descriptor pointer — V8's map word, which also moonlights as the GC
   forwarding pointer during evacuation, with the identity hash outsourced to the
   empty properties slot.

2. **Allocation economics**: counters and indices dominate real programs; if each were
   a heap object, every `i++` would pay a bump allocation + header + collection
   (~10 ns and 8–16 bytes, ch12/ch06) — so small integers are immediates packed in the
   value slot itself. **Tag in bit 0**: one slot must hold either an int or a pointer,
   and the low bit discriminates cheaply — heap objects are ≥8-byte aligned, so their
   low 3 bits are provably 0 and `address | 1` is a lossless reversible encoding
   (untag = mask). **Tag-preserving arithmetic**: the Smi is `value << 1` (compressed
   build), so `(x << 1) + (y << 1) == (x + y) << 1` — addition and subtraction work on
   the tagged words directly, and the tag check is one `test` of bit 0.

3. **true, true, false.** `a` and `b` walk the identical path from the constructor's
   initial map: `+x` then `+y` — same transitions, same map. `c` and `d` also walk the
   *same* path — initial map, `+x`, `+y`, then the same `+z` transition edge from the
   same `{x,y}` map — so they land on the same map *despite* being a different shape
   from `a`. `a` vs `c`: `{x,y}` and `{x,y,z}` are different maps (c has one more
   descriptor), so false — a load site compiled for `a`'s map cannot serve `c`, and a
   site seeing both is polymorphic, not monomorphic.

4. **(a) Chrome: `PACKED_DOUBLE_ELEMENTS` — a `FixedDoubleArray` of raw 8-byte
   doubles, and 2^30 is stored *unboxed* as an IEEE-754 double.** With pointer
   compression, the Smi payload is 31 bits (max 2^30 − 1 = 1,073,741,823), so
   1,073,741,824 is a HeapNumber — and a literal mixing a Smi and a HeapNumber resolves
   to double elements, which re-stores 1,073,741,823 as a raw double too.
   **(b) Node: `PACKED_SMI_ELEMENTS`** — the uncompressed tagged word carries a 32-bit
   Smi payload (max 2^31 − 1), so both values are Smis and no number is a
   heap object at all. The lesson: the Smi boundary is a *build* fact — one source
   line, two heaps.

5. **The chain**: `window → document → HTMLButtonElement → listener list → JSFunction
   (handler) → Context{...} → detached <div> and everything it references.**
   "Removed from the DOM" is not "unreachable": the handler closure captured the
   node (or a component holding it), and the handler was never unregistered — the
   platform's registries are roots (section 6). The GC is correct; the graph is
   wrong. **Fix**: keep a named handler reference and `removeEventListener` on
   teardown (or `{once: true}` / an `AbortController`) — deleting the
   `button → closure` edge drops the context, the node, and the captured data in one
   collection.

6. **Megamorphic.** The benchmark built rows with one constructor: one map, a
   monomorphic site — guard + fixed-offset load, offset constant-folded by TurboFan.
   Production rows come from `JSON.parse`, which materializes exactly the keys each
   payload carries (`x` missing or a string, plus other optional fields): dozens of
   maps flow through `r[i].x`, the cache overflows at 5, and every access is a
   stub-cache hash lookup plus a call — several-fold to an order of magnitude
   slower. **Fix**: normalize after parsing — map each row through one constructor
   assigning every field in the same order (with `Number(r.x ?? 0)`-style coercion),
   so the million rows share one map again.

7. **External, not heap, memory.** Each cached `ArrayBuffer` is a small GC-managed
   wrapper plus a `malloc`'d backing store *outside* the V8 heap (section 5). The
   `Map` retains the wrappers, so the stores stay alive — but they never appear in
   `heapUsed`, and the heap snapshot looks healthy while RSS climbs.
   `--max-old-space-size` caps the traced heap only; the kernel OOM-kill is the only
   limit that ever fires. **The truthful number**: `process.memoryUsage().external`
   (it grew 1.9 → 201.9 MB in the lab). Fix: bound the `Map` (LRU) or release buffers
   explicitly — the wrapper's death frees the store only when a GC also runs.

8. **V8 fuses the roles.** The JVM splits "where allocation happens" (eden, 8/10 of
   young) from "where survivors wait" (S0/S1) and counts age 0–15 in header bits.
   V8's two *equal* semispaces make allocation area and survivor area the same two
   spaces: allocation bump-points in the active one, the scavenge copies survivors
   into the idle one and flips — so an object's *location* encodes its age, and the
   second scavenge promotes it. No eden, because survivors share the next allocation
   space; no age field, because "which semispace" is a one-bit age. **The trade**:
   tenuring is fixed at 2 (not tunable like `MaxTenuringThreshold`), survivors
   compete with fresh allocation for the same space, and the remembered-set write
   barrier is still needed exactly as the JVM needs card tables.

</details>
