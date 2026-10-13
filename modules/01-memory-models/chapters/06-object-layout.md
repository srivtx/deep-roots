# Chapter 06 — Object layout: what a "value" really looks like in RAM

> **The 30-second version.** Every language answers "what are the bytes of a value?"
> with one of three moves: **unboxed** (the bytes *are* the value — a C struct, a Rust
> struct, a C# struct), **boxed** (a heap block with a header plus payload, reached
> by pointer — every Java object, every Python object), or **tagged** (bits of the
> word itself say what it is — V8's Smis, NaN-boxing). Padding, vtables, headers,
> fat pointers, and the length-of-array problem all live on this map. Learn the three
> moves once, and every language in Part II is a local dialect.

## The three representations

Take the humble value `42`. Depending on the language, it may be:

```
unboxed (C, C++, Rust, C# struct)      boxed (Java, C#, Python, JS object)
 ┌──────────────┐                       ┌─────────┐     ┌──────────────────┐
 │  42          │ 4 bytes on stack/     │ ref ────┼────▶│ header │ 42      │ heap
 └──────────────┘ anywhere              └─────────┘     └──────────────────┘
 no header, no pointer, no metadata                     header = identity+GC bits
                                                 
tagged (V8 Smi, NaN-boxing, classic Lisp)
 ┌───────────────────────┐
 │ 42 << 1  │ tag=0      │  one word: value+type-in-bits (bit 0 clear = Smi)
 └───────────────────────┘  31-bit payload on 32-bit V8 (see Chapter 15)
```

Unboxed is the machine's native language: zero overhead, but no identity (you cannot
ask "is this the *same* 42"), no runtime type, no GC. Boxed buys identity, dynamic
type, and collector integration, at the price of one heap block, one header, and one
pointer dereference per access (a Chapter 02 locality event). Tagged is the
compromise dialects invent when they want unboxed speed and dynamic typing at once:
*steal bits from the pointer or the double* (the pointer tagging and spare-bits
material from Chapter 01).

## Padding, computed by hand

Natural alignment (Chapter 01) plus one extra rule — a struct's own alignment is the
largest alignment of its members, so arrays of structs stay aligned — gives you the
complete layout algorithm. Compute it once by hand and you own it forever:

```c
struct A { char c; int i; char c2; };   // offsets: c@0, pad 1-3, i@4, c2@8, pad 9-11
struct B { int i; char c; char c2; };   // offsets: i@0, c@4, c2@5, pad 6-7
// sizeof(A) = 12,  sizeof(B) = 8 — same fields, 33% waste from ordering alone
```

The compiler is your ally here in C (it may not reorder — the standard fixes member
order for compatibility — but it *inserts* the padding); C++ adds the famous
complication that members are laid out in *declaration order per access-control
group*, and Rust simply fixes order too. Chapter 01's lab proved the numbers on real
hardware; the practice takeaway is Chapter 02's: a struct that fits in one 64-byte
line is a different animal from one that straddles two. Sorting members
largest-alignment-first is the classic 20-30% layout win.

## The header: the price of identity

What does a boxed object carry before your first field? Per language, drawn to
scale (64-bit unless noted):

```
Java (HotSpot, compressed oops):   [ mark word 8B ][ klass ptr 4B ][ fields... ]
                                     hash/lock bits    type          12B header, 8B-align
Python (CPython 3.11):             [ refcnt 8B ][ type ptr 8B ][ payload... ]
                                     → a plain int is 28 bytes total
V8 (Node, no ptr-compression):     [ map word 8B ][ properties ][ elements ][ in-object fields ]
                                     the map IS the hidden class (Chapter 15)
Objective-C/Swift class instance:  [ isa 8B (arm64: packed with refcount!) ][ fields ]
                                     Chapter 16's non-pointer-isa
C++ class with virtuals:           [ vptr 8B ][ fields... ]     no other header!
C# class:                          [ obj header 8B (sync+method table) ][ fields ]
```

Same idea, different contents — the header carries whichever of these the runtime
needs: *type identity* (klass/map/isa/vtable), *GC bookkeeping* (mark bits, age —
the JVM's 4-bit GC age lives in the mark word, Chapter 12), *lock/hash state* (the
same JVM mark word moonlights as a thin lock), and *reference counts* (Python,
Objective-C, Swift). Note the one honest exception: **C++ has no per-object GC or
type header — a polymorphic object carries exactly one pointer, the vptr.** That is
the zero-overhead principle (Chapter 09) drawn at byte scale.

## Vtables: virtual dispatch at byte level

```c++
struct Shape { virtual double area(); int id; };
// object:      [ vptr (8B) ][ id (4B) ]
//                    │
//                    ▼  (one indirect load)
//              vtable: [ &Shape::area ][ typeinfo* ]   (layout: ABI-defined, Itanium ABI
//                                                       puts typeinfo at offset −2 words)
```

A virtual call: load vptr, load slot, call indirect — two dependent loads (Chapter 02
says: two potential misses) and no inlining across the call site, which is the real
cost (the *missed* specialization, not the instructions). Multiple inheritance adds
offset thunks; the details are Module 04 material (decompilers reconstruct classes
from exactly these vtable shapes — and Chapter 21's attackers target them: overwrite
a vptr, own the call). Rust and Go compile the same idea into their tagged enums and
fat interfaces (below).

## Fat pointers: one pointer is not always one word

Some values need more than an address to be useful:

```
Go slice            [ data ptr ][ len ][ cap ]          3 words — the length rides WITH
Rust &[T] slice     [ data ptr ][ len ]                 2 words
Go interface        [ itab (type+methods) ][ data ptr ] 2 words — the type rides WITH
std::string_view    [ data ptr ][ size ]                2 words
Swift String        (small-string: inline bytes, 16B total — Chapter 16)
```

The pattern: **the pair (pointer, length/type) travels together in registers/stack
slots** — "fat pointer." This solves the C plague at its root: a Go slice cannot
forget its length; every indexing compiles to a bounds check against the `len`
riding along. Chapter 21's entire "CWE-787: out-of-bounds write" industry exists
because C arrays are naked pointers with the length *forgotten at decay* — the
single most consequential layout decision in this book.

## The length-of-array problem, the master example

Who knows how long the array is?

- **C**: nobody. `int a[10]` knows its length only in the declaring scope;
  `sizeof(a)` works there, but passing `a` decays to a bare pointer (Chapter 08) and
  the length is gone. Every overflow bug (Chapter 21) is this table's first row.
- **Java / C# / JS**: the header carries it — `array.length` reads a field 8 bytes
  from the object start; the runtime enforces it (every indexing = one compare and
  branch).
- **Go**: the 3-word slice header carries it; compiler inserts the check.
- **C++ `std::vector` / Rust `Vec`**: the container object (3 pointers/words:
  begin/end/capacity) carries it.

Four layouts, four safety regimes — one problem. When you meet a new language in
Part II, ask this question first; it predicts the language's whole error personality.

## Tagging and NaN-boxing: stealing spare bits

Chapter 01 left us spare bits: a 48-bit canonical address leaves the top 16 bits of
every pointer unused; IEEE-754 quiet NaNs leave 51 bits of payload unused.

- **V8 Smi**: bit 0 = 0 means "31/32-bit integer in the remaining bits" — integers
  up to 2^31−1 need *no allocation at all* (Chapter 15 makes this a whole world).
- **NaN-boxing** (JavaScriptCore, SpiderMonkey heritage): stuff a pointer or an
  int32 into the payload of a double whose exponent is all-ones. One 64-bit
  register holds *any* JS value unboxed — the reason those engines avoid V8's
  split representation.
- **Lisp/McCarthy**: the original — every word's low bits tagged (Chapter 18's cons
  cells).

The trade is always the same: you buy "no allocation for half the values" with "a
mask/shift on every operation" — and the tagging *IS* the type system at runtime.

## Boxing, when it leaks into your perf profile

Value-language code that quietly heap-allocates: C# `struct → interface` cast
(Chapter 13's 24-byte box), Java `int → Integer` autoboxing into collections
(Chapter 12's classic p99 killer), OCaml's polymorphic compare... The detector is a
heap profile that shows small, identical-size allocations at a hot call site. The
fixes form a family: generics (parametric types make the box unnecessary — this is
*why* Java generics and C# generics exist at the memory level), spans/slices over
raw arrays, and unboxed fields on the containing object.

## What insiders know

- **SoA vs AoS**: array-of-structs pays for fields you never touch (every line
  fetch drags all fields along — Chapter 02); struct-of-arrays fetches only the hot
  field. Game ECS engines and vectorized ML kernels are SoA *because of cache lines,
  not aesthetics*.
- **EBO / niche optimization** (Chapters 09, 10): empty base classes and null-
  pointer patterns fold into zero bytes — `sizeof(std::unique_ptr<T>) ==
  sizeof(T*)`, `sizeof(Option<&T>) == sizeof(&T)`. Layout engines *hunt* for
  legally-unused bit patterns.
- **Strings are the polymorphism stress test**: every runtime invents multiple
  representations (SSO in libstdc++/libc++ — Chapter 09; compact strings in the JVM;
  cons/rope/sliced in V8 — Chapter 15's 24-byte `SlicedString` that can pin a
  100 MB parent; small-string in Swift). Read a string's layout and you have read
  the runtime's priorities.
- **Alignment of atomics**: a lock-free `std::atomic<uint64_t>` that straddles a
  cache line is either illegal or catastrophically slow (x86's split lock #AC
  detection made this *audible* — Chapter 20).
- **`offsetof` is a layout debugger** you already own; CTFU trivia: the zero-size
  trailing flexible array member (Chapter 08) exists purely to make one-allocation
  header+payload layouts expressible.
- **Headers vs cache lines is a real design axis**: why Python is slow for small
  ints *per operation* (28-byte boxed objects, header traffic) while V8 Smis fly —
  the representation choice is the performance story, not "interpreted vs
  compiled."

## See it yourself

One C program prints the whole chapter; run it on x86-64:

```c
#include <stdio.h>
#include <stddef.h>
struct A { char c; int i; char c2; };
struct B { int i; char c; char c2; };
struct V { int (*fp)(void); int id; };       /* hand-rolled "vptr" first member */
int main(void) {
    printf("A: size=%zu c@%zu i@%zu c2@%zu\n", sizeof(struct A),
           offsetof(struct A, c), offsetof(struct A, i), offsetof(struct A, c2));
    printf("B: size=%zu i@%zu c@%zu c2@%zu\n", sizeof(struct B),
           offsetof(struct B, i), offsetof(struct B, c), offsetof(struct B, c2));
    printf("ptr=%zu  V: size=%zu\n", sizeof(void*), sizeof(struct V));
    struct V obj = { NULL, 7 };
    printf("V obj bytes: fp=%p id=%d — the 'vptr' is just member 0\n", obj.fp, obj.id);
    return 0;
}
```

Then the boxed-size ladder (Python): `python3 -c "import sys; print(sys.getsizeof(0),
sys.getsizeof(1), sys.getsizeof(10**30), sys.getsizeof(''), sys.getsizeof('a'))"` —
28, 28, (grows), 49, 50 on CPython 3.11: the header *is* the floor (Chapter 14
dissects it). And Rust: `println!("{}", std::mem::size_of::<Option<&u8>>())` → 8 —
the niche optimization, live.

## How to use this knowledge

- **Shrink hot structs**: reorder by alignment (compute it — you now can), convert
  AoS→SoA on hot fields, pack hot flags into one word. 20-30% on layout alone is
  common; more when line-straddling stops.
- **Read any object like a native**: core dumps, COBOL storage dumps (Chapter 17),
  V8 `%DebugPrint` output, exploit development (Chapter 21) — all the same skill:
  header, then fields, at offsets you can derive.
- **Pick representations deliberately** in APIs: fat-pointer slices instead of
  pointer+length pairs; value types (C#) for short-lived small data; arenas of
  unboxed records (Chapter 05) for batches.
- **Diagnose "mystery allocation" profiles** in one glance: boxing sites (interface
  casts, autoboxing) look like identical-size blocks at one call site.

## Connections

- **Chapter 01** — alignment, spare pointer bits, endianness of every field.
- **Chapter 02** — the 64-byte line as the budget every layout negotiates with.
- **Chapter 08 / 09 / 10** — C's naked arrays, C++'s vptr-and-nothing-else, Rust's
  niche-packed tagged unions.
- **Chapter 12 / 13 / 14 / 15 / 16** — each header decoded: mark word, method
  table, PyObject, map word, non-pointer isa.
- **Chapter 17** — COBOL: the whole language as layout-without-header (PIC bytes,
  no padding, layout as external contract).
- **Chapter 21** — vtable/vptr overwrite, type confusion (two layouts disagreeing),
  overflow (length forgotten) — every attack in that chapter is a *layout* fact.

*Forward links — where the rest of the program picks this chapter up:*

- **Module 22 ch01 (the tensor)** — the industrial object: (data_ptr, shape, strides) plus a refcounted storage — the header-of-object pattern at scale.
- **Module 18 ch03 (bignums)** — OpenSSL's BIGNUM is a variable-length object whose limb count is a leak — layout as side channel.
- **Module 16 ch02 (the DOM heap graph)** — every DOM node is a heap object with a header; the wrapper problem is a two-header object.

## Quiz

1. **Concept.** Why does a boxed value cost a pointer dereference even when the
   payload is already in cache? Trace the two-step access.
2. **Concept.** Why must a struct's total size be padded to its largest member's
   alignment? What breaks in *arrays of that struct* otherwise?
3. **Predict the number.** x86-64: `struct {char a; double b; char c;}` — size and
   every offset?
4. **Predict the number.** A C# class holding an `int` and a `char` (auto layout):
   minimum object size on x64? And the same fields as a `struct` (unboxed)?
5. **Predict the behavior.** Java: `Integer a = 127, b = 127; Integer c = 200,
   d = 200;` — what do `a == b` and `c == d` print, and what does it have to do
   with boxing?
6. **Bug diagnosis.** A Go function's `for _, v := range bigSlice` is fast, but the
   same loop over `[]interface{}` is 10x slower with heavy GC. Explain with
   representations.
7. **Bug diagnosis.** A C++ profiler shows a hot virtual call missing constantly at
   L1. Two objects of the same class are being called alternately. What is the
   layout-level mechanism?
8. **Cross-chapter.** Chapter 21 says "CWE-787 is the #1 CWE." Which table in this
   chapter explains why that bug class cannot exist in Java, and by what mechanism
   (two words)?

<details>
<summary>Answers</summary>

1. The variable holds a *reference*: first access loads the pointer itself (usually
   register/stack — cheap), then dereferences to the heap block where the payload
   sits. Even with the block cached, you pay the dependent load latency and — for
   scattered objects — the line/row locality costs of Chapter 01/02. Unboxed reads
   the bytes directly from the variable's own slot.
2. Arrays compute element addresses as base + i × sizeof(struct): if sizeof weren't
   a multiple of the largest alignment, element 1's `double` would land misaligned
   — every element after the first breaks the Chapter 01 alignment contract, and
   atomics (Chapter 20) could tear.
3. size = 24: a@0, pad 1-7, b@8 (8B), c@16, pad 17-23 (trailing pad to multiple of
   8).
4. Class: 8B object header + 4B int + 2B char + pad to 8 → 16 bytes minimum. Unboxed
   struct: 4 + 2 → 8 bytes with packing (or 8 with default sequential layout — the
   point: no header, no pointer; the class costs the header forever, the struct
   costs nothing beyond its fields).
5. `a == b` → true (reference equality, but Integer caches −128..127 — both 127s
   are the *same object*); `c == d` → false (two distinct boxed 200s). This is
   boxing + a small cache — Chapter 12 and 14's small-int singletons, and the reason
   the rule "never == wrappers" exists.
6. `[]T` holds unboxed records contiguous in memory — range iteration streams
   lines (Chapter 02). `[]interface{}` boxes every element: each is a separate heap
   object (map/itab + payload — fat pointer to a boxed value), so iteration
   pointer-chases, allocation rate explodes, and GC pressure (Chapter 11/05's
   allocation tax) dominates. Representation, not the loop, is the cost.
7. Two objects → likely two vtables, and worse, two vptr *values* alternating at one
   call site: the vptr load and the vtable slot load (two dependent loads, Chapter
   02/09) can't stay predicted in the L1 (branch target + data caches both thrash);
   inlining is impossible (unknown target). Megamorphism at machine level — the
   Chapter 15 lesson, visible in C++ clothes.
8. The length-of-array table: Java arrays carry their length in the header, and the
   runtime *bounds-checks* every indexing — "length field + enforced check," the
   two-word answer to why CWE-787 is a C-family disease.

</details>
