# Chapter 13 — C# and .NET: value types on a GC heap

> **The 30-second version.** The CLR gives the same answers as the JVM (ch12) to three of
> the four questions — no owner, a tracing collector, memory- and type-safety with `null`
> as the hole — but it splits the first question in two. A *value* is either a reference
> (classes: heap objects behind a 16-byte prefix) or a *value type* (structs: bare bytes,
> no header, eligible to live in a stack frame from ch04 or inline inside another object).
> The bet: most programs are aggregates of small values, so make those free. The cost is
> unification — one type system means `object` must be able to reference structs too, so
> casting a struct to `object` or a non-generic interface copies it into a heap shell:
> 16 bytes of prefix to carry 8 bytes of payload. The collector is the JVM's generational
> shape — gen0 bump allocation in thread-private contexts, a gen1 nursery, gen2 — plus
> two CLR-only regions: a Large Object Heap for single allocations ≥ 85,000 bytes that is
> never compacted by default, and a Pinned Object Heap (.NET 5+). Escape ladder:
> `stackalloc`, `Span<T>`, `ArrayPool<T>`, `NativeMemory`. Numbers below: .NET 8,
> CoreCLR, x64, unless marked.

## 1. The bet: two kinds of value, one type system

C#'s four answers: a value is a *struct* (including `int`, `bool`, enums, `ValueTuple`)
or a *reference*; ownership is undefined; freeing is the collector's; safety is type and
memory safety with `null` the one hole — the JVM's four answers, with the first one
amended.

Where each kind lives, honestly:

- a struct local in a method — a frame slot (ch04), gone at return;
- a struct *field of a class*, or an element of an array — inline inside that heap
  object, no prefix of its own;
- a struct captured by a lambda or used across `await` — hoisted into the compiler's
  closure or async state-machine object, i.e. the heap;
- a boxed struct — its own heap object (section 2);
- a class instance — always the heap.

So "value types live on the stack" is a claim about *stack eligibility*, not location. The
exact rule: **a struct lives wherever its container lives.** The frame is merely the one
container the compiler guarantees to be automatic storage (ch04).

The payoff for that distinction is the absence of a header. An unboxed struct is pure
payload — no method-table pointer, no sync block, no identity, no lock. Assignment copies
the bytes, like C (ch08). Layout obeys ch06's rules: C# emits `LayoutKind.Sequential` for
structs (default pack 8 on x64), the runtime honors it strictly for blittable content:

```csharp
struct Pt  { public int X; public int Y; }        // 8 bytes: two ints, zero overhead
struct Rec { public byte Tag; public long Id; }   // 9 bytes of fields

Console.WriteLine(Unsafe.SizeOf<Pt>());   // 8   (System.Runtime.CompilerServices.Unsafe:
                                          //      sizeof without the unsafe keyword)
unsafe { Console.WriteLine(sizeof(Rec)); } // 16 — Tag at 0, 7 bytes of padding so the
                                           // long sits at an 8-aligned offset, Id at 8
```

Two ints cost 8 bytes here; the same two ints as fields of a class cost 8 bytes *plus*
the object's 16-byte prefix; the same two ints boxed cost 24 (section 2); as an
`ArrayList` element in ch12's Java they cost a 16-byte `Integer` plus a 4-byte reference.
Same payload, four runtimes, four prices — ch06's arithmetic with real teeth.

**Blittable** types are the subset whose in-memory representation is byte-identical in
managed and unmanaged code: all primitive numeric types, `IntPtr`, pointers,
single-dimensional arrays of them, and structs containing only them. `bool` is *not*
blittable (1 byte in the CLR, but the default P/Invoke marshaling widens it to the 4-byte
Win32 `BOOL`); `string`, `class`, and `object` are not. P/Invoke cares because a blittable
argument can be **pinned and passed as a raw pointer — zero copy** — while a non-blittable
one forces the marshaller to build a converted temporary before the call and, for
out-parameters, copy it back after: double memory traffic and extra per-call work.

```csharp
[DllImport("nativelib")] static extern void Transform(Pt[] pts);   // blittable array:
                                                                  // pinned, address passed
[DllImport("nativelib")] static extern void Log(string msg);       // non-blittable:
                                                                  // converted copy per call
```

## 2. The box: unification has a price

`object`, interfaces, virtual calls — the machinery of one type system — are all
*references to a heap object whose first word is a method-table pointer* (ch06). That is
how any variable can hold any instance. A struct has no method table, so the moment a
struct must appear where a reference is expected — cast to `object`, cast to a
non-generic interface, stored in a pre-generics collection — the runtime conjures the
missing prefix: it allocates a heap object whose method table says "boxed Pt" and copies
the struct's bytes into it. That is **boxing**; unboxing copies back and type-checks.

```text
   UNBOXED struct local (stack frame)                BOXED (GC heap, x64)

   Pt p;                                             object o = p;    // box on the cast
   +--------+--------+                              +---------+--------------+--------+--------+
   | X (4B) | Y (4B) |  8 bytes total               | ObjHdr  | MethodTable* | X (4B) | Y (4B) |
   +--------+--------+  no header, no lock,         +---------+--------------+--------+--------+
                        no identity; copying          offset -8   offset 0        +8       +12
                        IS the assignment             lock+hash    "I am a boxed   \-payload-/
                                                     bits         Pt"              8 bytes
                                                    \--------- 16 bytes of pure overhead ----/
                                                    total 24 bytes to carry 8 bytes of payload
```

Where the JVM spent 12 header bytes (8 mark + 4 compressed klass, ch12), the CLR spends
16 (8-byte `ObjHeader` at offset −8 + 8-byte method-table pointer) — and enforces a
24-byte minimum object size on x64 so any gap left by compaction can itself be recorded
as a filler object. A boxed 2-int struct is exactly 24: 8 + 8 + 8 payload already at the
minimum. A boxed 1-byte struct is also 24.

Boxing is implicit precisely at the unification seams:

```csharp
ArrayList list = new ArrayList();
list.Add(42);            // Add(object): 42 becomes a 24-byte box, 5M of them = 120 MB
IComparable c = 42;      // struct -> non-generic interface: box at the cast
object o = userInput;    // box
Console.WriteLine("n=" + 42);  // old Concat(object,object) pattern: boxes the int
o.GetType();             // non-generic reflection entry: boxes (o above already was)
```

Also: calling `ToString`/`Equals`/`GetHashCode` on a struct that does not *override* them
boxes, because those methods are declared on `object`/`ValueType`. Overriding them on
your structs is not style — it deletes box traffic.

The structural fix is **generics**. C# generics are reified: `List<int>` really contains
an `int[]`, code specialized per value type by the JIT, no casts, no boxes — where
ch12's erased Java generics reduce `ArrayList<Integer>` to a box per element. The deeper
mechanism is the JIT's *constrained call*: invoking an interface method through a
generic type parameter `where T : ISomething` compiles to a direct call on the struct,
no box — the thing erasure makes impossible. The modern fixes are the same family:
`Span<T>`-taking APIs, and interpolated-string handlers (.NET 6's
`DefaultInterpolatedStringHandler`, whose `AppendFormatted<T>` is generic) so
`$"{x}"` no longer boxes `x` the way `string.Format` did.

## 3. The collector: three generations and two heaps that don't move

Allocation is chapter 12's TLAB pattern with different names: each thread grabs an
**allocation context** — an ~8 KB quantum (default, CoreCLR x64) carved from the gen0
budget — and bumps a pointer. No lock, no free list (ch05), cost ≈ zeroing. When the
context is exhausted the thread takes a new quantum; when gen0's *budget* is exhausted,
nothing can bump — that is a gen0 GC trigger, exactly like an eden trigger.

The generations and their one big divergence from the JVM:

```text
        small object heap (SOH) — one ephemeral segment at a time
+--------------+---------------+--------------------------------+
|    gen 0     |     gen 1     |            gen 2               |
| bump pointer |  survivors of | mark - sweep - compact-on-     |
| (per-thread  |  gen 0 GCs    | demand; collected with back-   |
|  contexts)   |  (nursery)    | ground GC running concurrently |
+--------------+---------------+--------------------------------+
 allocate here | survive a gen0 GC -> gen1 | survive a gen1 GC -> gen2
               (CLR promotes on survival — no 4-bit age count, unlike ch12's 15)

large object heap (LOH)              pinned object heap (POH, .NET 5+)
+---------------------------------+  +--------------------------------+
| any single object >= 85,000 B   |  | GC.AllocateArray(n, pinned:    |
| collected with gen 2, SWEPT,    |  |   true): immovable by design,  |
| not compacted (default)         |  | swept with gen 2, never moves  |
+---------------------------------+  +--------------------------------+
```

- **No tenuring age.** The JVM counts 4 header bits and tenures at 15 (ch12); the CLR has
  no per-object age — **surviving a collection *is* promotion**. A gen0 survivor lands in
  gen1 at the same GC; a gen1 survivor lands in gen2. Medium-lived objects (buffers that
  outlive one GC) therefore tenure early and pile into gen2; the CLR compensates with
  small, self-tuning gen0/gen1 budgets (typically single-digit MB, sized against cache).
- **The LOH is a fragmentation machine by default.** The 85,000-byte threshold is
  per-object and per-array, header included: `new double[10_000]` (80,024 bytes) is a
  normal gen0 allocation, `new double[12_000]` (96,024) goes straight to the LOH — which
  is gen2 territory from birth and, by default, is only *swept*, never *compacted*. Free
  space between survivors stays as gaps; a process can die with `OutOfMemoryException`
  while the heap reports plenty of free — non-contiguous — bytes. Stopgap:
  `GCSettings.LargeObjectHeapCompactionMode = GCLargeObjectHeapCompactionMode.CompactOnce`
  plus `GC.Collect()`; real fixes are below.
- **The POH (.NET 5+)** exists to *segregate the immovables*: buffers allocated with
  `GC.AllocateArray(n, pinned: true)` live there, swept with gen2, never compacted — and
  never in the way when gen2 compacts everything else.

**Pinning** is why immovables exist. P/Invoke hands raw addresses to native code, which
walks them while the managed side might trigger a GC; `fixed` scopes, pinned
`GCHandle.Alloc(o, GCHandleType.Pinned)`, and implicit marshaling pins all make the same
promise: *this object does not move*. The cost: a pinned object is an obstacle the
compactor must build around — relocation leaves holes where neighbors moved away. Pinning
something *young* is worst: the fragmentation it causes gets promoted into older
generations. Insiders' rule: pin rarely, briefly, late — or never, by using span-based
marshaling (blittable `Span<T>`/array parameters pin only for the call's duration) and
the POH for long-lived native-visible buffers.

**Workstation vs Server GC** is a deployment decision, not a tuning knob:
`<ServerGarbageCollection>` (runtimeconfig; `DOTNET_gcServer=1`). Workstation — one heap,
one background-GC thread, the default for CLIs — minimizes footprint. Server — the Web
SDK's default for ASP.NET — creates **one heap and one GC thread per core**, marking in
parallel: throughput at memory cost (budgets scale with core count), a problem in
containers where .NET 8's DATAS (dynamic adaptation to application sizes) now scales the
heap count back down on quiet services.

**GC pressure** is ch12's lesson verbatim: GC frequency = allocation rate ÷ gen0 budget.
A service allocating 500 MB/s against an 8 MB gen0 eats ~60 gen0 GCs per second. The fix
is allocating *less* — boxing removal, spans, pooling — not buying RAM.

## 4. Death, delayed: finalizers are the wrong tool

A finalizer (`~T()` in C#, compiled to a `Finalize` override) is registered at
*allocation* time in the finalization list. When the GC finds a finalizable object
unreachable, it does **not** reclaim it: the object is moved to the **f-reachable
queue** — the queue itself roots it, hence "reachable *via finalization*" — and a single
dedicated finalizer thread drains entries and runs each finalizer. Only a *later*
collection frees the memory.

Count the bill, using section 3's promotion rule: the dying object survived its GC
(being f-reachable is surviving), so it was promoted — a short-lived finalizable
resource dies in **gen2**, and its bytes wait for the next full GC. One extra
generation, plus queue latency, plus this: if a finalizer stores `this` into a live
static, the object is *resurrected* — alive again, needing `GC.ReRegisterForFinalize`
to ever die twice. And an exception on the finalizer thread kills the process (.NET
Core+). Note also that modern .NET does not run finalizers at process exit — the OS
reclaims, nothing else.

The real mechanism is deterministic: **`IDisposable`** plus `GC.SuppressFinalize`:

```csharp
class Conn : IDisposable
{
    readonly SafeHandle h;             // the resource holder (see below)
    protected virtual void Dispose(bool d) { /* release h once */ }
    public void Dispose()
    {
        Dispose(true);
        GC.SuppressFinalize(this);     // delete the finalization entry NOW: the object
    }                                  // dies in the gen it's found dead in — no
}                                      // promotion to gen2, no extra GC cycle
```

`GC.SuppressFinalize(this)` deletes the entry from the finalization list — the object
now dies in the generation where the GC first finds it dead, instead of being promoted
to gen2 as an f-reachable corpse. That one call is the difference between "reclaimed at
the next gen0 GC" and "reclaimed at the next gen2 GC." Resurrection — a finalizer
storing `this` into a live static, reviving the object — is the opposite move, undone
only by `GC.ReRegisterForFinalize` and a second death. And an exception on the finalizer
thread kills the process (.NET Core+). The finalizer, if you keep one at all, is a
safety net calling `Dispose(false)` — never blocking, never taking locks, never doing
I/O, because one slow finalizer stalls the single thread every finalizable object in the
process is waiting on. For OS handles the sanctioned net is **`SafeHandle`**: a
runtime-provided critical finalizer that P/Invoke marshaling wraps around raw handles
automatically and that runs before user finalizers. Note also that modern .NET does not
run finalizers at process exit — the OS reclaims; nothing else.

## 5. The manual mode: stackalloc, Span<T>, pools, native memory

The CLR's version of ch09's RAII-free manual mode is *borrowed memory with compiler
rules*. `stackalloc` allocates from the current frame — `alloca` (ch04), but safe:

```csharp
Span<int> buf = stackalloc int[64];   // 256 B in THIS frame, freed at return
// the compiler's ref-safety rules reject: storing buf in a field, a lambda, an async
// continuation — the C footgun from ch08 is a compile error here
```

A **`Span<T>`** is a fat pointer: (`ref T` pointer, `int` length) = 16 bytes on x64, and
a *byref struct* — allowed only on the stack. It can wrap stack memory, an array slice,
a string's characters, or native memory, and every operation is a zero-allocation view:
`s.AsSpan(2, 5)` is arithmetic, not a copy. `ReadOnlySpan<char>` is why this matters for
strings (section 6). `Memory<T>` is the heap-storable cousin — (`object`, `int` index,
`int` length) — for fields and `await` boundaries, where `Span` is forbidden.

```csharp
static int Verb(ReadOnlySpan<char> s) => s switch
{
    "GET"  => 0,     // length check + inline UTF-16 compare: ZERO allocation
    "POST" => 1,
    _      => -1,
};
// the string version — switch (s.Substring(0, 4)) — allocates the substring first;
// span pattern matching exists so parsing hot loops (Kestrel's verb dispatch) don't
```

**`ArrayPool<T>.Rent(n)`** amortizes the expensive allocations — it hands out powers of
two from 16 to 2^20 elements (Shared pool defaults) and recycles them on `Return`. Two
traps: the array is *longer* than you asked (track the logical length), and it comes
*dirty* — `Return(array, clearArray: true)` or you have just built a data leak between
callers.

**`unsafe`** restores C# from ch08: pointers, `fixed`, and fixed-size buffers inside
structs:

```csharp
unsafe struct Packet
{
    public int Length;
    fixed byte Body[64];   // 64 bytes INLINE — no array object, no pointer, no header
}
```

**`NativeMemory`** (.NET 6+) is `malloc` by another name (ch05): `NativeMemory.Alloc`,
`AlignedAlloc`, `Realloc` — raw process heap, freed exactly when you say so, best
wrapped in a `Span` or a `MemoryManager<T>` and a `Dispose` that calls `Free`. The
ladder, top to bottom: GC heap → pool/POH → `stackalloc` → native. Each rung down buys
zero GC pressure and costs you the safety you just descended past.

## 6. string: the type with its own rules

A .NET string on x64 is `[ObjHeader 8][MethodTable* 8][int length 4][UTF-16 chars 2n]`,
total 22 + 2n bytes rounded up to 8 — "" costs 24, "hello" 32. It is **length-prefixed,
not NUL-terminated**: the length is authoritative, the opposite design from C (ch08).
(One honest footnote: CoreCLR quietly stores a single NUL *past* the last char, not
counted in `Length` and invisible to C#, so default UTF-16 P/Invoke marshaling can pin
the string and pass its char pointer without copying.)

Strings are immutable and reference-compared, so the runtime **interns** literals: one
hash table per process, rooted forever, mapping text to a single object. Two occurrences
of `"GET"` in one assembly are the same object — which is why `Object.ReferenceEquals`
on literals is true, and why `string.Equals` tries reference-equality first. The cost
side: the intern table never releases, so `string.Intern` on unbounded runtime-built
input is a deliberate leak; interning is for a fixed vocabulary, not for user data.

The interplay with spans is the payoff: substring, `Split`, `StartsWith`-style parsing
allocates a new string per step; `ReadOnlySpan<char>` views cost nothing — which is why
the switch in section 5, `s.AsSpan(i, j).SequenceEqual("literal")`, and .NET 5's
`string.Concat(ReadOnlySpan<char>, ...)` overloads exist. Parsing pipelines that once
allocated per token now allocate per request.

## What insiders know

- **The 85,000-byte LOH threshold is per-object and per-array, header included** — and
  in .NET Framework there was a second, undocumented-feeling rule: `double[]` with ≥
  1000 elements went to the LOH *regardless of size* (8,000 bytes!). Modern .NET kept
  only the 85,000 rule. The interview trap: `new double[10_000]` is gen0, not LOH.
- **The finalizer thread is a single thread for the whole process** (CoreCLR). One
  finalizer that blocks on a lock or I/O stalls every finalizable death in the process —
  handle exhaustion with a healthy-looking heap is this signature.
- **C# has no Go-style escape analysis (ch11) and no C2-style scalar replacement.**
  RyuJIT will not rescue a `class` you allocated in a loop; stack residency is your
  `struct`/`class` decision at source level. Corollary: every local in an `async` method
  is hoisted into the state-machine heap object, so "async locals" are heap fields.
- **`ValueTuple` is free.** `(int, string)` is a struct; returning `(a, b)` allocates
  nothing, destructuring copies bytes — where Java needed a heap `Tuple` (ch12).
- **`GC.Collect()` in production code is a diagnostic smell** — with one legitimate
  exception: triggering the once-only LOH compaction (`CompactOnce` + `Collect`), after
  which you should ask why the LOH churned in the first place.
- **`fixed byte buf[N]` inside an `unsafe` struct** gives inline storage with no object,
  no pointer, and no GC cost — the C `char[N]` member reborn; C# 11's `[InlineArray]`
  makes the same layout safe.
- **A boxed 1-byte struct is 24 bytes** — the x64 minimum object size. Boxing overhead
  is a multiplier: 3× for a 2-int struct, 24× for a byte.

## See it yourself

.NET 8 SDK. Create a console app and run:

```csharp
// boxlab/Program.cs  (dotnet new console; .NET 8)
using System.Runtime.CompilerServices;

Console.WriteLine(Unsafe.SizeOf<Pt>());                 // 8   — no header on the struct
object a = 42, b = 42;
Console.WriteLine(Object.ReferenceEquals(a, b));        // False — TWO boxes; no Java-style
                                                        // IntegerCache here (ch12)
Console.WriteLine(Object.ReferenceEquals("GET", "GET"));// True  — interned literals
object o = new();
Console.WriteLine(GC.GetGeneration(o));                 // 0
GC.Collect(0); Console.WriteLine(GC.GetGeneration(o));  // 1  — survival IS promotion
GC.Collect();   Console.WriteLine(GC.GetGeneration(o)); // 2

int g0 = GC.CollectionCount(0);
var list = new System.Collections.ArrayList();
for (int i = 0; i < 5_000_000; i++) list.Add(i);        // Add(object): 5M boxes
Console.WriteLine($"gen0 GCs for 5M boxed ints: " +
    $"{GC.CollectionCount(0) - g0}");                    // ~15-30: 120 MB of pure overhead

struct Pt { public int X, Y; }   // top-level programs: type declarations go AFTER
                                 // the statements
```

```text
$ dotnet run -c Release
8
False
True
0
1
2
gen0 GCs for 5M boxed ints: 21
```

Now watch the same pressure from outside (a busy service shows it best):

```text
$ dotnet-counters monitor -n MyApp --counters System.Runtime
    System.Runtime
        alloc-rate                 1,842,000 B/s     <- GC pressure, ch12-style: rate
        gen-0-gc-count             412                   divided by gen-0 budget
        gen-1-gc-count             3
        gen-2-gc-count             0                  <- should grow in minutes, not secs
        time-in-gc                 4.2 %
```

Read it exactly like ch12's `12M->3M`: health is *frequency arithmetic* (alloc rate ÷
gen0 budget ≈ gen0 GCs/s), and a climbing `gen-2-gc-count` is the alarm. For heap
contents, the ch12 `jmap -histo` equivalents exist: `dotnet-gcdump collect -p <pid>`
(then `dotnet-gcdump report <file>` for per-type counts) and `dotnet-dump` for full
lldb-style post-mortem analysis — honest note: they exist, they are heavyweight, and
`dotnet-counters` is where every investigation starts.

## How to use this knowledge

- **Perf triage is allocation-rate triage.** High `alloc-rate` plus frequent gen0 GCs:
  profile for boxing (non-generic interfaces, `ArrayList`-era APIs, string concat),
  swap to generic/span APIs, and the GC counts fall without touching heap config.
- **OOM-with-free-memory is an LOH signature.** Check `loh-size` vs `gc-heap-size` and
  free space: fragmentation, not a leak. Compact once to survive the night; fix with
  `ArrayPool`/POH and uniform buffer sizes.
- **The struct/class choice is the allocation decision** — there is no escape analysis
  coming to save a class. Big structs: pass by `in`/`ref` to avoid byte-copying; any
  struct over an interface reference boxes per use.
- **Never ship a finalizer as the cleanup mechanism.** `Dispose` + `SafeHandle` +
  `SuppressFinalize`; a finalizer costs a generation of delayed reclamation and shares
  one thread with every other finalizer in the process.
- **Interview-grade derivations:** boxed size arithmetic (24/32 bytes), why `List<int>`
  is flat where `ArrayList<Integer>` is boxes (reification vs erasure), why pinning
  young objects fragments old generations.

## Connections

- **Ch 04** — `stackalloc` is `alloca` with ref-safety; frames are still free.
- **Ch 05** — the allocation context is the bump allocator; `NativeMemory` is `malloc`
  behind a managed name.
- **Ch 06** — header/padding arithmetic done CLR-style; the 16-byte prefix vs the JVM's
  12.
- **Ch 08** — length-prefixed vs NUL-terminated strings; fixed-size buffers.
- **Ch 09** — `Dispose` is scope-based determinism grafted onto a GC runtime.
- **Ch 11** — Go's compile-time escape analysis vs the CLR's "you decide, struct or
  class"; both pin, both pay compaction around pins.
- **Ch 12** — the twin generational design: same bet, different header, different
  tenure rule, plus reified generics as the structural anti-boxing move.
- **Ch 14, 15** — Python boxes everything always; V8 reuses this chapter's young-gen
  evacuation shape.
- **Ch 22 + queued modules** — mobile runtimes under memory caps inherit the
  allocation-rate math; queued security/decompilation modules lean on the object-layout
  facts (see ROADMAP.md).

*Forward links — where the rest of the program picks this chapter up:*

- **Module 19 ch02 (types as proofs)** — nullable reference types are the retrofit of honesty onto 20 years of code — C#'s own type system, theorized.
- **Module 18 ch05 (implementation failures)** — managed crypto arrays and the GC pause leak: why key material hates moving collectors.

## Quiz

1. "Value types live on the stack." Name three distinct situations in which a struct
   ends up on the GC heap — none of them an explicit cast written by the programmer.

2. Derive, from the requirement that one variable type (`object`) can reference any
   value, why boxing must exist — and state exactly what the runtime adds to the struct
   to make it referenceable.

3. Predict the total heap bytes of `object o = new Pt();` where `Pt` holds two `int`s,
   on x64 .NET 8. Account for every byte. Then predict the same for a struct holding
   one `byte`.

4. (a) `new double[10_000]` — which region and generation? (b) `new double[12_000]` —
   which region, at which generation is it collected, and what is it *not* subject to
   by default? Show the arithmetic that separates them.

5. A long-running service pins a ~90 KB `byte[]` with `fixed` during each network write
   (tens of thousands of requests). Live-bytes metrics look flat and low, but RSS
   climbs for hours and the process dies with `OutOfMemoryException` while the GC heap
   reports large free space. Diagnose the mechanism; give the stopgap and two real
   fixes.

6. A per-request `Conn` object has a finalizer releasing an OS handle. Between full GCs
   the process's memory climbs steeply, and after a traffic burst the app hits handle
   exhaustion even though "the GC ran." Explain both symptoms using section 3's
   promotion rule and section 4's threading rule.

7. Predict the bytes of pure box garbage produced by
   `foreach (object o in new int[10_000_000]) { }` on x64 — then name the single
   language feature whose absence causes it, and the modern signature that removes it.

8. Cross-chapter (ch12): the JVM tenures at 15 via a 4-bit age field; the CLR promotes
   on first survival. State one consequence of each choice, and why the CLR tolerates
   its consequence.

<details>
<summary>Answers</summary>

1. **(a)** The struct is a field of a class — it lives inline inside the class's heap
   object. **(b)** The struct is an element of an array (`int[]` elements are heap
   bytes inside the array object). **(c)** The struct is captured by a lambda or used
   across `await` — the compiler hoists locals into a closure or async state-machine
   object on the heap. Bonus: casting to a non-generic interface *does* box, and that
   cast is often hidden inside an old API call rather than written by the programmer.

2. `object` is a *reference to a heap object whose first word is a method-table
   pointer* — that prefix is what makes uniform virtual dispatch, type checks, and
   identity possible (ch06). A struct is bare bytes with no method table, so to appear
   behind a reference it must acquire one: the runtime allocates a heap object
   consisting of an 8-byte `ObjHeader` + an 8-byte method-table pointer (16 bytes of
   prefix, x64) and copies the struct's bytes in as payload. The box is exactly
   "struct + the prefix it was missing."

3. **24 bytes** for `Pt`: 8 (`ObjHeader`) + 8 (method-table pointer) + 4 + 4 (two ints)
   = 24, which is exactly the x64 minimum object size, so no padding. **One-byte
   struct: also 24** — 8 + 8 + 1 = 17, rounded up to the 24-byte minimum. The minimum
   exists so the GC can represent any gap as a filler object. Boxing overhead is a
   multiplier: 3× for the two-int struct, 24× for the byte.

4. (a) 10,000 × 8 = 80,000 payload + 24 header = 80,024 < 85,000 → small object heap,
   gen0 — it dies cheaply in a gen0 collection. (b) 12,000 × 8 = 96,000 + 24 ≥ 85,000
   → LOH, born as gen2, collected only with gen2 GCs, and **not compacted by default**
   (swept only). The one line of arithmetic is 85,000 total object bytes — header
   included. Big temporaries are gen2 pressure in disguise (ch12's humongous objects,
   same lesson).

5. **LOH fragmentation caused by pinning plus no compaction.** Each ~90 KB buffer is an
   LOH allocation (≥ 85,000); while pinned it cannot move, so when neighbors die, the
   sweeper leaves free gaps; varied sizes and lifetimes fragment the LOH until no free
   run of bytes fits a 90 KB request, so the heap keeps growing while reporting
   "free" space. Stopgap: `GCSettings.LargeObjectHeapCompactionMode = CompactOnce` +
   `GC.Collect()`. Real fixes: `ArrayPool<byte>.Rent` so buffers are recycled instead
   of churned, and the POH (`GC.AllocateArray(n, pinned: true)`) for long-lived
   native-visible buffers so immovables never share space with compactable ones.

6. **Memory:** the object is unreachable but *finalizable* — the GC moves it to the
   f-reachable queue, which counts as survival, and section 3 says survival = promotion:
   the dead object is promoted into gen2 and its bytes wait for the next full GC. Steady
   request traffic therefore accumulates gen2 corpses between full collections.
   **Handles:** finalizers run on a *single* dedicated thread; a burst of deaths plus
   one slow finalizer queues every release behind it, so handles are freed far slower
   than requests arrive. Fix: `Dispose` + `GC.SuppressFinalize` on the hot path (or
   `SafeHandle`), finalizer only as a net.

7. **240 MB.** 10 million boxes × 24 bytes (from answer 3) = 240,000,000 bytes of pure
   overhead to iterate an array already in memory. Cause: the non-generic
   `IEnumerable`/`object` path — erasure-era APIs typed as `object`, where every
   `int` must be boxed. Removal: *generics* (`IEnumerable<int>`/`List<T>`, reified, no
   boxes — the feature the JVM lacks, ch12) or span-based iteration
   (`foreach (int i in array.AsSpan...)` / `MemoryExtensions`), which never touches
   `object`.

8. **JVM:** medium-lived objects die *cheaply* in the survivor spaces before tenuring —
   but it pays 4 header bits and needs `MaxTenuringThreshold` tuning. **CLR:** no
   per-object age, so anything surviving one GC tenures immediately; gen2 (and full
   GCs) accumulate objects that would have died young under the JVM. The CLR tolerates
   this because gen0/gen1 budgets are small and self-tuning — most objects either die
   in gen0 or genuinely deserve promotion — and because the promotion rule needs zero
   header bits and zero bookkeeping.

</details>
