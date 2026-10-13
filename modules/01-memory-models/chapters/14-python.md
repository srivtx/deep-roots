# Chapter 14 — Python: everything is a PyObject

> **The 30-second version.** Python's answer to "what is a value?" is uniform: *everything*
> — the integer `1`, the string `"a"`, your function, your class, `None` — is a
> heap-allocated object with the same 16-byte header: a reference count plus a type
> pointer. Chapter 06 called this boxing; Python does it to every value, which is why
> `1` costs 28 bytes and why NumPy exists. Freeing is **reference counting**: the
> moment the count hits zero, the object dies — deterministic, no pauses — but every
> assignment writes a counter, and counters can never reach zero inside a reference
> cycle, so a small **tracing GC** (three generations, thresholds 700/10/10) backs the
> counter up. Small objects come from **pymalloc**: 256 KiB arenas of 4 KiB pools of
> fixed-size blocks — a slab allocator (chapter 19's family). Numbers below: CPython
> 3.11, 64-bit, reference build, unless marked.

## 1. One header for every value

Chapter 06 laid out the options: inline primitives (C), boxed heap objects (JVM),
tagged unions. Python picks boxing *unconditionally*. A Python `int` is a
`PyLongObject`:

```text
   PyLongObject for the value 1        (CPython 3.11, 64-bit)
   offset  size  field
        0     8   ob_refcnt      -- how many references point at me
        8     8   ob_type        -- pointer to the int type object (itself a PyObject)
       16     8   lv_tag         -- sign + digit count (30-bit digits follow)
       24     4   ob_digit[0]    -- little-endian base-2^30 digits
                                 total: 28 bytes  ->  sys.getsizeof(1) == 28
```

That 16-byte prefix — `ob_refcnt` + `ob_type` — is the universal **PyObject header**,
paid by every object: digits, character buffers, a function's code pointer and
globals, a class's method table. `ob_type` points at *another* heap object, which is
why `type(1)` returns something you can subclass and attach attributes to — types
are values too. `None`, `True`, the empty tuple, your lambda: same 16 bytes, same heap.

Measure it (CPython 3.11, 64-bit; `getsizeof` includes the 16-byte collector header
for GC-tracked objects):

```python
import sys
sys.getsizeof(0)         # 24  (16 header + 8 tag + 0 digits)
sys.getsizeof(1)         # 28  (+ one 4-byte digit)
sys.getsizeof(2**63-1)   # 36  (3 digits)
sys.getsizeof(10**100)   # 72  (12 digits)
sys.getsizeof("")        # 49  (48-byte str header + NUL)
sys.getsizeof("a")       # 50
sys.getsizeof([])        # 56  (40-byte list struct + 16 GC)
sys.getsizeof(())        # 40  (24-byte tuple VAR header + 16 GC)
sys.getsizeof({})        # 64  (48-byte dict struct + 16 GC)
```

No value costs less than 16 bytes, and none of these numbers includes what the object
*points to* — a million-element list is a 56-byte struct plus 8 bytes per slot plus a
million boxed payloads. (CPython 3.12 shrank the empty string to 41 by deleting a
legacy field and gave `0` the 28-byte shape — version drift to remember when quoting
folklore.)

Because `1` is expensive, the hot values are manufactured **once**: all ints in
**-5..256 are cached singletons** created at startup — loop counters and small
results are pointer copies, zero allocations. `None`, `True`, `False`, and the 256
one-character latin-1 strings get the same treatment. Side effects: `id(100) ==
id(100)` everywhere, and `x is 257` depends on compilation context (section 4).

## 2. Deterministic death, paid one counter at a time

"Who frees?" is arithmetic: each object carries its own tally of users, and the tally
hitting zero *is* the free. The win: **reclamation is immediate and deterministic**.
`del x` on the last reference: counter hits zero, finalizer runs, memory returns to
the allocator *during the statement*. No ch12-style pause, no ch15 scavenger, no
"later" — and the freed block is reusable in the very next statement (the lab's
`object()` reuse demo exploits exactly this).

The cost, derived: **every reference operation is a counter write**.

```python
x = y            # store 8-byte pointer, then INC y.ob_refcnt (read-modify-write)
return result    # caller stores the pointer (INC); callee's slot dies (DEC)
box[0] = item    # INC item, DEC the previous occupant
x = None         # DEC old x: possibly a cascade of frees and finalizers
```

A pointer store in C or Java writes the pointer and stops. Python also touches the
pointed-to object's header — a second cache line, a read-modify-write — on every
assignment, argument pass, return, and container store or eviction. The count is
per-object, not per-reference: a million aliases still cost one 8-byte field (the
economy of Swift's ARC, chapter 16).

What makes it tolerable is the **GIL**. With one thread executing bytecode at a time,
`ob_refcnt += 1` is a plain non-atomic increment — no lock prefix, no CAS retry.
Atomicity is a *consequence of the interpreter's design*, not of the refcounting.

The honest paragraph about 3.13+: **PEP 703** (free-threaded CPython, experimental
`python3.13t`) removes the GIL, and the free atomicity evaporates. Naive atomic
refcounts would make every assignment a contended cache-line ping (chapter 20's false
sharing, at scale). Instead the 8-byte `ob_refcnt` splits into a 32-bit shared count
plus a 16-bit thread-*local* count (**biased reference counting**): a thread bumps
its private counter — same cache line, no contention — and flushes to the shared word
only when ownership migrates. Hot objects like functions and code get **deferred
refcounting** (some references aren't counted at all). And pymalloc, which relies on
the GIL for its own structures, is replaced by mimalloc in these builds. Check
`sys._is_gil_enabled()` on 3.13+ before reasoning about it.

## 3. The cycle a counter cannot see

Refcounting's blind spot is structural: counts reach zero only if nothing points at
the object, and cycles point at each other forever.

```python
import gc
gc.disable()                       # keep the collector out of the way

def make_cycle():
    a = []                         # a: refcnt 2 (name 'a' + b's element)
    b = [a]                        # b: refcnt 1
    a.append(b)                    # b: refcnt 2 -- a's list points back at b

make_cycle()                       # locals die: a and b each sit at refcnt 1...
print(gc.collect())                # ...held by *each other*.  -> 2
```

Neither count can ever fall to zero. This also settles `del`: `del a` removes *one
name binding* — one decrement — and nothing else; `del` is an assignment of nothing.
Chapter 08's `free` has no Python spelling for cycles.

The **`gc` module** is the tracing backup, and it is deliberately tiny. It tracks only
*containers* — lists, dicts, tuples, sets, instances (an `int` can never join a cycle,
so it is never scanned). Tracked objects sit in one of **three generations**;
`gc.get_threshold()` returns the defaults, **(700, 10, 10)**: generation 0 collects
when *(allocations − deallocations of tracked objects)* exceeds 700; generation 1 is
collected every 10th gen-0 collection; generation 2 every 10th gen-1 — and since 3.11,
gen 2 is additionally gated by a 25% rule: a full collection fires only when new
long-lived objects exceed a quarter of all long-lived objects.

The algorithm is two passes of subtraction:

1. For every object in the generation, copy `ob_refcnt` into a scratch field `gc_refs`.
2. Walk every reference *between two objects of that generation*, subtracting 1 from
   the target's `gc_refs`. A positive remainder means someone **outside** the
   generation points here (stacks, globals, other generations): a root.
3. Trace and mark from the roots. Unmarked objects with `gc_refs == 0` have counts
   composed *entirely of each other* — unreachable despite counts of 1 or more.
   Collect them (weakref callbacks and `__del__` get a chance first; PEP 442 made
   cycles with `__del__` collectable — pre-3.4 folklore says otherwise).

Survivors are promoted, betting like ch12's age bet that survivors survive again, so
old data is re-traced exponentially rarely.

## 4. pymalloc: 256 KiB arenas, 4 KiB pools, 16-to-512-byte blocks

Chapter 05 priced `malloc` at tens of nanoseconds: bins, locks, coalescing. Python
allocates constantly — every boxing, every list, every frame — so CPython interposes
**pymalloc**, a slab-style allocator tuned for thousands of small, short-lived,
fixed-size blocks:

```text
 arena (default 256 KiB, CPython 3.11 64-bit; 1 MiB if built USE_LARGE_ARENAS)
 +----------------------------------------------------------------------+
 | arena bookkeeping | pool 0 | pool 1 | pool 2 | ................| pool 63|
 +----------------------------------------------------------------------+
    each pool = 4 KiB = one OS page, serving exactly ONE size class:

    +--------------------------------------------------------------+
    | pool header (~48 B: class index, free list, refcount)        |
    +--------------------------------------------------------------+
    | block | block | block | ... (256 per 16-B pool)              |
    +--------------------------------------------------------------+
      size classes step by the alignment quantum: 16 bytes on 64-bit
      CPython 3.11+ (32 classes, 16..512 B); 8 bytes on older/32-bit
      builds (64 classes, 8..512 B) -- the number most articles quote.
```

A 25-byte request → class 32 → carved from a 32-byte-class pool; freed blocks thread
onto that pool's free list. Requests **over 512 bytes (`SMALL_REQUEST_THRESHOLD`)
bypass pymalloc** and go to the system allocator — chapters 05 and 19 — as do big
buffers like list pointer arrays, and in free-threaded builds, everything.

Two properties explain everyday mysteries. **Identity artifacts**: `is` compares
pointers, and pymalloc is a *recycling machine* — the block freed a microsecond ago is
the block handed out next.

```python
x = object(); i = id(x); del x      # counter hits 0; block returns to free list
y = object(); id(y) == i            # usually True: the block was re-issued
```

Add the -5..256 cache, one-character strings, and the compiler's constant
deduplication (one `257` object per code object, so `a = 257; b = 257` on one line
gives `is` → `True`, compiled separately gives `False`), and you have the classic
"510-ish" experiments where identity near the cache boundary flips with context.
Identity is a fact about *allocation history*, not value. CPython 3.12 even warns on
`is` with a literal — the compiler flagging that you're testing dedup, not equality.
Use `is` for `None` and sentinels; `==` for values.

**The RSS that never shrinks.** An arena — 64 pools — is returned to the OS (munmap)
only when *every pool in it is empty*. A workload touching many size classes leaves
each arena with a few live blocks, pinning megabytes of mostly-empty pools:
`gc.collect()` can report 0 unreachable while RSS stays flat — the objects *died*,
their arenas didn't (chapter 05's malloc retention covers the >512-byte fraction).
Fragmentation without compaction is the price of a non-moving design: chapter 12's
JVM evacuates to give pages back; Python can never move a live object, because
`id()` promises stability and C extensions hold raw pointers.

## 5. Strings, lists, and the dict that runs the language

**Strings** get three layers of special treatment. Layout: the empty string is 49
bytes — a 48-byte `PyASCIIObject` header (16-byte PyObject + length + cached hash +
state bitfields + a legacy pointer) plus the NUL. Characters come in four *kinds*:
1 byte/char if every code point is ASCII, else 1, 2, or 4 bytes/char (UCS1/2/4)
sized by the string's **maximum** code point — one emoji makes the whole string pay
4 bytes per character. Non-ASCII compact strings pay 16 more header bytes (a
cached-utf8 pair), so `"é"` is 66 on 3.11; 3.12 deleted the legacy pointer (PEP 623)
and the empty string dropped to 41. Second, **interning**: constants that look like
identifiers are deduplicated into a global table at compile time — they are about to
become dict keys, and key comparison collapses to a pointer compare when keys are
shared. `sys.intern()` does it explicitly for runtime-built strings; 3.12 widened
auto-interning to identifier-like literals up to 4096 bytes. Third, **immortal
objects** (PEP 683, 3.12): small ints, `None`, interned strings moved to static
memory with a refcount frozen at a special all-ones value that INCREF/DECREF ignore.
Why: every thread touches `None` and the small ints; if each touch writes the same 8
bytes, the cache line ping-pongs between cores (chapter 20's false sharing, with
every thread, on every use). Freezing removes unbounded write traffic and moves them
out of the heap — `gc.set_debug(gc.DEBUG_STATS)` on 3.12+ lists them as a "permanent
generation."

**Lists** are a 40-byte struct (header, `ob_size`, `ob_item`, `allocated`) plus a
separate, over-allocated pointer array. Appending grows capacity by the formula in
`listobject.c` — `new_allocated = (n + n>>3 + 6) & ~3` — the capacity sequence
**0, 4, 8, 16, 25, 35, 46, 58, 72, 88**, i.e. ~**12.5% (1/8) slack** amortizing
append to O(1) with rare copying. Two consequences insiders use: `list(L)` / `L[:]`
allocate the pointer array to *exactly* `len(L)` — copying compacts away the slack —
while `list.sort()` sorts in place with at most an n/2-pointer temp array (`sorted()`
pays a whole new list).

**Dicts are the language's spine**: module globals, class namespaces, instance
attributes, `kwargs` — the interpreter runs on dicts. Since 3.6 the layout is the
**compact dict**: a 48-byte struct (header, `ma_used`, version tag, `ma_keys`,
`ma_values` — 64 with the GC header) pointing at a keys table of two arrays:

```text
  keys table (8-slot example)              entries (insertion order!)
  +-------------------------+        +--------------------------------------+
  | indices: [3,-,-,0,-,-,-,1]  ----> | 0: (hash, "name", value)  = 24 B    |
  +-------------------------+   |    | 1: (hash, "age",  value)  = 24 B    |
                                +----> slot 3 holds entry 0, slot 7 entry 1 |
  indices widen 1 -> 2 -> 4 -> 8 bytes per slot as the table grows
```

Sparse **indices** (open addressing: probe `i = (5*i + 1 + perturb) & mask`, with
`perturb` a copy of the full hash shifted 5 bits per step so patterns break, plus a
short same-cache-line linear run) point into dense **entries** of `(hash, key, value)`
triples appended in insertion order — which is *why* dict order is guaranteed from
3.7: order is a side effect of the layout. The table resizes at 2/3 full (8 slots →
5 usable entries; the first insert jumps `getsizeof` from 64 to 224). Instances get
**split tables / key sharing**: since 3.3, same-class instances share one keys table
and keep only their values; 3.11+ goes lazier — values sit in a compact C array off
a hidden slot, and a real `__dict__` materializes only when touched (reflection,
`vars()`, pickling).

## 6. Instances, leaks, and the zero-copy escape ladder

A plain instance on 3.11 is 48 bytes (16 header + 8 managed-dict/values slot + 8
weakref list + 16 GC); its attributes live in the shared-key values array above.
Materializing `__dict__` costs a real dict (measured 288 bytes at two keys on 3.12) —
avoid it in hot paths. `__slots__` deletes the dict machinery: the class declares
fixed names, CPython computes **fixed offsets** at class creation, and the instance
becomes a C struct with a PyObject header — `p.x` is one pointer-add at a
compile-time-constant offset, and new attributes raise `AttributeError` by design
(multiple inheritance needs slotted bases to cooperate). Measured with `tracemalloc`
on 3.12: 10,000 two-attribute instances cost 120 bytes each with a dict, 88 with
`__slots__`, and the gap grows with attribute count — slots pay exactly 8 bytes per
attribute, the dict path pays per-key bookkeeping.

Attribute *lookup* chains through memory: `p.x` checks the type's MRO dicts for data
descriptors, then the instance's dict/values, then the MRO again for everything else
(methods bind on the fly). Every class in the MRO is a dict probe — deep hierarchies
multiply the cost of an attribute in a hot loop.

**Leaks in a refcounted world** come in three species, none of them "the GC forgot":

- **Cycles with an external anchor**: a parent↔child graph is one big cycle; leak one
  node to a global and the whole graph survives every collection. Back-references
  belong in `weakref`.
- **C extensions miscounting**: a missing `Py_DECREF` (refcount never zero, invisible
  to `gc`, which can't see C-held references — a pure RSS leak) or one too many
  (premature free → use-after-free, chapter 21's world).
- **Closures capturing frames**: a lambda, callback, or suspended generator holds its
  frame — chapter 04's locals block — and everything those locals reference (chapter
  15 makes this the main event).

The toolbox, in escalation order: `gc.get_referrers(obj)` (who points at me?),
`gc.get_objects()` with per-type counts, `tracemalloc` for *where allocations
happened* (it records the Python call stack of every live block), and `objgraph`
(pip) for rendered back-reference graphs.

The last piece is Python's escape ladder out of boxing, the counterpart of C#'s spans
(chapter 13): the **buffer protocol** (PEP 3118). Objects owning raw memory — `bytes`,
`bytearray`, `array`, NumPy arrays — export it with shape, type, and strides; a
**`memoryview`** is a zero-copy *window* over it:

```python
buf = bytearray(b"deeproots" * 1000)     # exporter owns 9000 bytes
view = memoryview(buf)[4:1004]           # no copy: 184-byte view object
buf[4] = ord("R")                        # mutate the underlying buffer
bytes(view[:6])                          # b'Rootsd' -- the window saw it
sys.getsizeof(buf[4:1004])               # 1033: what the slice *copy* would cost
```

The 184-versus-1033 gap is NumPy's whole pitch: an ndarray is one buffer of unboxed
values plus metadata — a million floats are 8 MB of raw memory with *one* PyObject,
not a million 32-byte boxed floats; slicing and broadcasting are window arithmetic.
(A view over `bytes` is read-only; a view keeps the exporter alive — a window is a
reference.)

## What insiders know

- **`getsizeof` adds the 16-byte GC header for tracked objects**: empty dict 64 =
  48 + 16, empty list 56 = 40 + 16; `__sizeof__` omits it — the two differ by exactly
  the collector's bookkeeping.
- **`a = b` is an 8-byte pointer store plus one counter increment** — a thousand
  aliases still cost one 8-byte field. And `is` is a pointer compare, which is why
  identity is allocation history, not semantics.
- **List growth is 0, 4, 8, 16, 25, 35, 46, 58, 72, 88** (the `listobject.c`
  comment): ~12.5% slack; `list(L)`/`L[:]` allocate exact — copying compacts the
  slack; `list.sort()` is in-place with ≤ n/2 pointers of temp, `sorted()` is not.
- **Tuples ≤ 20 elements are recycled through free lists** (capacity 2000 per size
  in 3.11), so tuple `id()`s repeat aggressively; `() is ()` is `True` — one
  singleton, immortal in 3.12+.
- **On 64-bit CPython 3.11+, fresh small-object addresses are 16-byte aligned**:
  `set(id(o) % 16 for o in [object() for _ in range(50)])` is `{0}` (older/32-bit:
  8-byte quantum, 64 classes not 32).
- **`gc.set_debug(gc.DEBUG_STATS)` prints a "permanent generation" line on 3.12+**
  (a few hundred immortal objects): small ints, `None`, interned strings — refcounts
  frozen, never written, immune to cross-core cache-line traffic (chapter 20).
- **A suspended generator keeps its whole frame alive** — locals and everything they
  reference (chapters 04/15). A lazy pipeline holding a 2 GB buffer at frame bottom
  is a leak by design; `gen.close()` drops it deterministically.
- **The gen-2 25% rule** (3.11+): full collections fire only when long-lived pending
  exceeds a quarter of long-lived total — why `gc` cadence looks irregular in logs,
  and why `gc.disable()` on a mostly-acyclic workload is often-safe folklore.

## See it yourself

```text
$ python3 -i          # interactive: every line is a separate code object
>>> x = 100
>>> y = 100
>>> x is y
True                    # -5..256: the small-int cache
>>> a = 257
>>> b = 257
>>> a is b
False                   # two compilations, two constant objects
>>> c = 257; d = 257
>>> c is d
True                    # one code object: ONE 257 constant, deduplicated
```

The flip is allocator and compiler, not language. Block reuse and the alignment
quantum:

```python
x = object(); i = id(x); del x
y = object()
print(id(y) == i)       # usually True -- pymalloc re-issued the freed block
print(sorted(set(id(o) % 16 for o in [object() for _ in range(50)])))
                        # [0] on 3.11+/64-bit: 16-byte alignment quantum
```

The cycle detector, with generations visible:

```python
import gc
gc.disable()
def make_cycle():
    a = []; b = [a]; a.append(b)
make_cycle()
gc.enable()
print(gc.collect())     # -> 2   (the two list objects in the cycle)
gc.set_debug(gc.DEBUG_STATS)
gc.collect()
# gc: collecting generation 2...
# gc: objects in each generation: 214 4872 0
# gc: objects in permanent generation: 375      <- 3.12+ immortals
# gc: done, 0 unreachable, 0 uncollectable, 0.0004s elapsed
```

Slots versus dict, measured rather than asserted:

```python
import tracemalloc
class P:
    def __init__(self, x, y): self.x, self.y = x, y
class PS:
    __slots__ = ("x", "y")
    def __init__(self, x, y): self.x, self.y = x, y
tracemalloc.start()
xs = [P(i, i) for i in range(10_000)]
print(tracemalloc.get_traced_memory()[0] / 10_000)   # ~120 B/instance (3.12)
del xs; tracemalloc.reset_peak()
ys = [PS(i, i) for i in range(10_000)]
print(tracemalloc.get_traced_memory()[0] / 10_000)   # ~88 B/instance
```

And the production leak-hunt shape — top allocation site:

```python
import tracemalloc
tracemalloc.start(10)
data = [[j for j in range(50)] for _ in range(1000)]
snap = tracemalloc.take_snapshot()
for st in snap.statistics("lineno")[:3]:
    print(st)
# <stdin>:4: size=469 KiB, count=1987, average=242 B   <- the comprehensions
```

Snapshot before and after a request in a long-running service; growing line numbers
are the leak, call stacks attached.

## How to use this knowledge

- **The RSS-never-drops incident report**: memory freed ≠ memory returned. Cycles
  collected, objects dead, arenas pinned by a few live pools each — explain it with
  section 4 before anyone schedules nightly restarts.
- **Data-class design at scale**: a million instances × 30 saved bytes is 30 MB;
  `__slots__` (or `dataclass(slots=True)`) when count is large and schema fixed.
  Measure with `tracemalloc`, not folklore — 3.11's lazy values made the dict path
  cheaper than old posts claim.
- **Never let `is` near values** (NaN checks, integer tests in suites): identity
  returns cache and allocation artifacts. `==` for values, `is` for `None` and
  sentinels; the 3.12 `SyntaxWarning` is your linter.
- **C-extension review discipline**: every borrowed `Py_INCREF` needs its
  `Py_DECREF`; count in review, because no collector will fix it — the leak is
  invisible to `gc` by construction.
- **Zero-copy pipelines**: `readinto` a preallocated `bytearray`, window it with
  `memoryview`, hand it to NumPy without materializing slices — the
  184-versus-1033 arithmetic at file scale; same discipline as C# spans (ch13).

## Connections

- **Ch 04** — closures and suspended generators keep frames (and locals) alive: the
  stack is the leak vector.
- **Ch 05** — pymalloc rides on malloc; >512-byte objects use the raw allocator with
  all its bins and retention.
- **Ch 06** — the PyObject header is boxing taken to totality; tag bits vs headers is
  the design space Python closed.
- **Ch 12** — tracing vs counting, both ways: the JVM buys cheap pointer stores with
  pauses and compaction; Python buys determinism with counter writes and a
  non-moving, never-shrinking heap.
- **Ch 13** — memoryview is Python's span: the escape ladder from boxed memory into
  raw buffers.
- **Ch 15** — V8's hidden classes vs dictionary-mode objects mirror Python's
  dict-everything; closure capture returns as V8's leak story.
- **Ch 16** — ARC is refcounting with the compiler inserting INC/DEC; Python pays
  them at runtime, per operation.
- **Ch 19** — pymalloc is a slab allocator: arenas, pools, size classes — same design
  family as kernel slabs and tcmalloc thread caches.
- **Ch 20** — GIL-as-atomicity and immortal objects are cache-line economics;
  biased refcounting is the no-GIL repair.
- **Ch 21** — C-extension refcount bugs are the use-after-free surface; immortal
  singletons are fixed targets attackers love.

*Forward links — where the rest of the program picks this chapter up:*

- **Module 22 ch01 (the tensor)** — the refcounted Storage under every tensor is the PyObject pattern this chapter taught, at industrial scale.
- **Module 19 ch04 (GC as proof search)** — the cycle detector is a tracing prover for exactly the objects refcounting cannot refute — the 3-generation threshold arithmetic derived.

## Quiz

1. Derive the exact writes performed by `x = y` in Python versus the same statement
   in C. Then explain why the GIL makes the Python version cheap — and name the two
   mechanisms the free-threaded 3.13 build substitutes for that free atomicity.

2. Predict `sys.getsizeof` on CPython 3.11, 64-bit, for `(None, None, None, None)`
   and for `"deeproots"`. Why does `None` being an immortal singleton not change the
   tuple's cost — what physically sits in the four slots?

3. Predict the output, and explain each line with a *different* mechanism:

   ```python
   ns = {}
   exec("x = 100", ns)
   exec("y = 100", ns)
   exec("a = 257", ns)
   exec("b = 257", ns)
   print(ns["x"] is ns["y"], ns["a"] is ns["b"])
   exec("c = 257; d = 257", ns)
   print(ns["c"] is ns["d"])
   ```

4. An empty dict is 64 bytes; after the *first* insert, `sys.getsizeof` reads 224
   (CPython 3.11/3.12). Account for the 160-byte jump: what are the keys table's two
   arrays, why 8 index slots, and why exactly 5 entry slots for one key?

5. A service streams a 2 GB file in 10 MB chunks, deleting each chunk list after
   processing. Afterwards `gc.collect()` reports 0 unreachable and every chunk name
   is `del`-ed — yet RSS plateaus at 900 MB. Give three distinct memory-model
   reasons the process does not shrink.

6. This code keeps a 500 MB DataFrame alive long after `process()` returns. Why,
   precisely — and what does `del big_buffer` actually do?

   ```python
   REGISTRY = []
   def on(event, fn):
       REGISTRY.append(fn)
   def setup(big_buffer):
       on("tick", lambda: process(big_buffer))
   ```

7. A lazy pipeline (`read -> filter -> batch`) over a large file holds hundreds of MB
   though only one line is "active". Which chapter-04 structure pins the memory, what
   will chapter 15 generalize, and what is the deterministic fix?

8. Cross-chapter arithmetic (ch 06, 12, this chapter): the same 30-byte
   record-processor needs ~3 GB on the JVM and ~10 GB in pure Python. Give three
   *derived* reasons from memory models — header arithmetic, field representation,
   allocator behavior — and state which one NumPy's design deletes.

<details>
<summary>Answers</summary>

1. **C: one 8-byte pointer store. Python: the pointer store, plus a
   read-modify-write of `y.ob_refcnt` (a second cache line), plus the DEC of whatever
   `x` previously held — possibly a cascade of frees.** The GIL serializes bytecode
   execution, so the increment is a plain non-atomic write; atomicity is a consequence
   of serialization, not of the counter. The 3.13 free-threaded build substitutes
   **biased reference counting** (a 16-bit thread-local count flushed to a 32-bit
   shared counter only on ownership migration) and **deferred refcounting** for hot
   objects like functions and code; pymalloc is replaced by mimalloc.

2. **Tuple: 24-byte VAR header + 4 × 8-byte pointers + 16 GC = 72 bytes. String:
   48-byte header + 9 chars + NUL = 58.** The four slots hold *pointers* — four
   copies of one address of the singleton `None`; immortality saves four payloads,
   not four slots. Same arithmetic as a Java array of references (ch 12): the
   container pays 8 bytes per element regardless of element cost.

3. **`True False`, then `True`.** First: `100` is in the -5..256 cache — one object,
   both names point at it. Second: each `exec` compiles a separate code object, so
   the two `257` constants are distinct PyLongs (and the first stays alive through
   `a`). Third: one code object stores one `257` constant and the compiler's constant
   deduplication binds both names to it — compilation context, not the cache. Three
   mechanisms: interpreter cache, per-compilation constants, constant dedup.

4. **Keys table = 8 index slots + 5 entry slots + keys-object bookkeeping.** The
   compact dict separates a sparse *indices* array (open-addressing probes) from a
   dense *entries* array of `(hash, key, value)` triples appended in insertion order.
   Minimum table size is 8, and open addressing wants ≤ 2/3 occupancy: 8 × 2/3 = 5
   usable entries — the 6th key would trigger a resize (indices widening to 2-byte
   slots). 160 ≈ 8 index bytes + 5 × 24-byte entries + the keys object's header.
   Insertion order is free: entries are appended.

5. **(a) If any iteration raised, the exception's traceback holds the frame, and the
   frame holds its locals — chunks you believe `del`-ed. (b) `del` removes one name;
   other references (accumulators, caches, closures) keep refcounts positive.
   (c) Genuinely freed small objects return to pymalloc pools — an arena is munmap'd
   only when all 64 pools are empty, and mixed size classes fragment arenas; the
   >512-byte fraction went to malloc, which retains per chapter 05.** Dead objects
   and returned pages are different claims: Python is non-moving by design.

6. **The lambda closes over `setup`'s frame, whose locals — including `big_buffer` —
   stay reachable through `REGISTRY`, a module global.** `del big_buffer` removes one
   *name* (one DEC); the frame's reference keeps the DataFrame's count nonzero, so it
   never dies. Fix at the anchor: don't register closures over big state (or expire
   entries / explicit unregister). Find it with `gc.get_referrers(df)`: the frame →
   the function → the list.

7. **Each suspended generator holds its frame — the chapter-04 locals block — with
   its buffers and iterators alive until exhaustion or close.** Three stages pin
   three frames plus everything they reference; chapter 15 generalizes it to every
   closure capture. The fix: `gen.close()` on abandoned stages (raising
   `GeneratorExit` inside, releasing locals), or restructure so producers don't hold
   read buffers as long-lived coroutines.

8. **(1) Header arithmetic: a 30-byte record is a `str` — 48-byte header + 30 chars +
   NUL ≈ 79 bytes (+ 16 when boxed in a container), where a JVM String pays ~24 plus
   a ~48-byte byte[]; both box, but Python's floor is higher. (2) Field
   representation: a JVM object holds primitives and 4-byte compressed references at
   compiler-computed offsets; N Python fields are N pointers to N separate heap
   objects (or dict rows at 24 bytes per entry plus indices) — the ch 06 header tax
   multiplied per field. (3) Allocator behavior: non-moving pymalloc retains
   fragmented arenas and lists carry 12.5% slack, while the JVM compacts and returns
   pages. NumPy deletes reason (2) — one PyObject over one unboxed buffer — and most
   of (1) with it.**

</details>
