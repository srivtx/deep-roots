# Chapter 04 — Recovering C++

> **The 30-second version.** Strip every symbol from a C++ binary and its
> skeleton survives. Every polymorphic object begins with a **vptr** pointing
> at a **vtable** whose Itanium-ABI layout is fixed — `[offset-to-top |
> typeinfo | virtuals]` — so vtables, the inheritance graph (RTTI), and the
> names (mangling decodes by hand) are all *derivable* from bytes. Constructors
> betray themselves by writing vptrs (two writes = base then derived),
> `operator new` leaks the class size and the allocator, the STL leaves
> standard shapes (32-byte strings, 24-byte vector triples, red-black nodes),
> and exceptions are tables, not code.

## The vptr: one pointer carries the whole class system

Module 01 ch06: a polymorphic C++ object carries exactly one extra word, the
vptr. From the binary side, that word is a thread you can pull. Lab binary:
`g++ 14.2.0, x86-64, -O0`, `geo::Circle` derived from `geo::Shape`, two
virtuals plus a virtual destructor.

```text
geo::Circle object — 24 bytes (hence operator new(0x18))
+0x00          +0x08       +0x0c      +0x10
+--------------+-----------+----------+-----------------+
| vptr (8 B)   | id_ (4 B) | pad (4)  | r_ (double, 8B) |
+--------------+-----------+----------+-----------------+
   |     fields in declaration order (module 01 ch06)
   |     vptr = vtable ADDRESS POINT = vtable symbol + 0x10
   v
vtable for geo::Circle   _ZTVN3geo6CircleE @ 0x3d48   (.data.rel.ro)
+--------+-------------------------+-----------------------------------+
| offset | value                   | meaning                           |
+--------+-------------------------+-----------------------------------+
| -0x10  | 0                       | offset-to-top (0: primary base)  |
| -0x08  | 0x3da8                  | -> typeinfo for geo::Circle      |
|  0x00  | 0x12e6   <- vptr HERE   | ~Circle() complete-object (D1)   |
| +0x08  | 0x1310                  | ~Circle() deleting       (D0)    |
| +0x10  | 0x12a8                  | Circle::area()  — slot 2         |
| +0x18  | 0x12d4                  | Circle::name()  — slot 3         |
+--------+-------------------------+-----------------------------------+
```

**The layout is ABI-fixed** (Itanium C++ ABI — gcc and clang on Linux):
offset-to-top and the typeinfo pointer always precede the function pointers,
so the vtable symbol sits 16 bytes before where the vptr points; every
`lea ...vtable+0x10` feeding a store to offset 0 is a vptr write, never a
call target. **Virtuals appear in declaration order**, with one trap: a
virtual destructor occupies *two* slots (D1 = the destructor, D0 = destructor
+ `operator delete`). Count slots and you have the virtual interface of a
class you never saw. **Only constructors write the vptr**, and derivation
forces two writes:

```text
geo::Circle::Circle(double)   (gcc 14.2.0, -O0)
    call  11e2 <geo::Shape::Shape()>     ; base ctor wrote vtable-for-Shape
    lea   ...,%rdx  # 3d58 <vtable for geo::Circle+0x10>
    mov   %rdx,(%rax)                    ; vptr write #2
    movsd %xmm0,0x10(%rax)               ; r_ at +0x10
```

Two stores to offset 0 bracketing the base call — the base-then-derived
fingerprint, and it is semantically required: a virtual call inside the base
constructor must dispatch to the **base** version, so the intermediate vptr
is observable. At `-O2`, with the base constructor inlined and no virtual
call between the writes, gcc dead-store-eliminates the base store and
devirtualizes `s->area()` entirely — it just wrote that vptr, so it knows the
dynamic type. Optimized C++ *erases* vtable dispatch wherever the type is
locally known; the indirect calls that remain mark genuinely polymorphic
boundaries.

The call site is chapter 03's addressing-mode method one level deeper:
`mov (%rax),%rax` (vptr), `add $0x10,%rax` (slot 2 = `area`),
`mov (%rax),%rdx`, `call *%rdx` — two dependent loads per dispatch, and the
slot constant says *which* virtual was called.

## RTTI: the inheritance graph sits in .data.rel.ro

Every vtable points at a typeinfo object — a small structure you parse as
bytes:

```text
typeinfo for geo::Circle   _ZTIN3geo6CircleE @ 0x3da8  (.data.rel.ro)
+0x00  vptr -> vtable for __cxxabiv1::__si_class_type_info (+0x10)  [relocated]
+0x08  name -> "N3geo6CircleE" (mangled!) in .rodata
+0x10  base -> 0x3dc0 = typeinfo for geo::Shape
```

The first word is a discriminator, *chosen by the compiler to encode the
inheritance shape*:

| vptr points at | class shape |
|---|---|
| `__cxxabiv1::__class_type_info` | no base classes |
| `__cxxabiv1::__si_class_type_info` | single inheritance — one base pointer at +0x10 |
| `__cxxabiv1::__vmi_class_type_info` | multiple/virtual inheritance — base count + flags at +0x10 |

Recovery is a table walk: collect every `_ZTI*` symbol, read each vptr to
classify it, follow the base pointers — the **whole inheritance DAG** without
executing an instruction (exactly what Ghidra's and IDA's class recoverers
automate, chapter 05). Two details in the bytes: typeinfo lives in
`.data.rel.ro` because its vptr needs per-load relocation (chapter 02), and
the name string is stored **mangled**. With `-fno-rtti` the layout is
unchanged but the typeinfo slot at -0x08 is written **0** and the `_ZTI*`
symbols vanish — a zeroed word at `vptr-8` is the fingerprint of deliberate
RTTI removal.

## Mangled names: a compression scheme you can read

The Itanium ABI encodes the full signature into the symbol name. The grammar
is short enough to decode by hand:

```text
_Z N K 3geo 6Circle 4area E v          (read left to right, token by token)
 |  | |  |    |       |     |  |
 |  | |  |    |       |     |  + parameter list: v = void
 |  | |  |    |       |     + E = end of nested name
 |  | |  |    |       + 4 bytes: "area" (the member name)
 |  | |  |    + 6 bytes: "Circle" (the class)
 |  | |  + 3 bytes: "geo" (the namespace)
 |  | + K = const member (method qualifier)
 |  + N = nested name: length-prefixed scope components follow
 + _Z = mangled name

_ZTV N 3geo 6Circle E   ->   TV = "vtable for";  TS/TI = typeinfo name/object
```

`_ZNK3geo6Circle4areaEv` reads "const member `area` of `Circle` in `geo`, no
parameters." Parameters add qualifier letters (`R` reference, `K` const, `P`
pointer) and type codes (`i` int, `d` double, `v` void):
`_ZNSt6vectorIiSaIiEE9push_backERKi` =
`std::vector<int, std::allocator<int>>::push_back(int const&)`. Constructors
and destructors are `C1/C2` and `D0/D1/D2` — complete/base-object
constructors, deleting/complete/base destructors. Tools: `c++filt`, or the
demangle flag `nm -C` / `objdump -dC`. Windows uses a different but equally
regular scheme:

| piece | example | meaning |
|---|---|---|
| `?` | `?area@Circle@@...` | decorated name begins |
| scope chain | `area@Circle@geo@@` | innermost first, `@@` terminates |
| special names | `??0Circle@@` / `??1Circle@@` | `??0` ctor, `??1` dtor, `??2` operator new |
| access/kind | `Q`, `U`, `S`, `A`, `Y` | public / public-virtual / public-static / private / global |
| types | `H`, `N`, `X`, `PEAVCircle@@` | `int`, `double`, `void`, `Circle*` |
| end | `@Z` | parameter list terminator |

`??0Circle@@QEAA@N@Z` is "public constructor of `Circle` taking a `double`" —
the same information as `_ZN3geo6CircleC1Ed` in another dialect.

## new and delete: the allocator's autograph

Every `new T` compiles to `operator new(sizeof(T))` — the ELF import `_Znwm` —
and since C++14 gcc emits **sized deallocation**:
`operator delete(void*, unsigned long)`. The lab binary calls
`operator new(0x18)` and `operator delete(ptr, 0x18)`: the class size leaks
at every construction *and* destruction site — a free `sizeof` oracle.
Arrays of non-trivial objects add an 8-byte count **cookie**
(`operator new(10*24+8)`).

`_Znwm` forwards to the malloc-family allocator, and *which one* is a
fingerprint module 01 ch19 taught you to read at call sites: **glibc** =
`malloc`/`free` via PLT, 16-byte chunks, 8-byte header — here (glibc 2.41)
`malloc(31)` yields usable 40; **jemalloc** = `mallocx`/`dallocx`/`sdallocx`
with `MALLOCX_*` flags, `je_`-prefixed symbols when static; **tcmalloc** =
`tc_malloc`/`tc_free` and `MallocExtension` calls; **custom** = every size a
multiple of one quantum (0x40, 0x100), frees never called (bump/arena),
direct `mmap` past a threshold. Spotting the allocator is step zero for heap
grooming and exploit feasibility (module 02 ch04-05).

## The STL's standard shapes

The STL is templates expanded into module 01 ch06's layouts — every container
is a fixed-size record recognizable in a core dump. `libstdc++`, x86-64:

```text
std::string — sizeof = 32
+0x00  _M_p (8 B)     data pointer: obj+0x10 when short, heap when long
+0x08  length (8 B)
+0x10  union (16 B)   local char buffer[16]  OR  heap capacity (8 B) + pad
                      SSO threshold: <= 15 chars inline (15 + NUL = 16)
```

Verified live: a 15-char string has `data() == &s + 0x10` (no allocation); a
16-char string calls `operator new(17)` — the request is always `len + 1` for
the NUL. libc++ (Clang) packs 23 chars into 24 bytes; MSVC 15 in 32.

```text
std::vector<T> — sizeof = 24:  the triple
+0x00  _M_start          -> first element
+0x08  _M_finish         -> one past last element        (size)
+0x10  _M_end_of_storage -> one past last allocated slot (capacity)
```

Every `push_back` has the same shape: compare `_M_finish` vs
`_M_end_of_storage`; if equal, allocate ~2x, copy, free old, rewrite *all
three* words. `map`/`set` are red-black trees: the 48-byte map object holds a
sentinel header plus node count; each node is
`[color(4)+pad][parent(8)][left(8)][right(8)][pair]` — 40 bytes for
`map<int,int>` (verified: `new(40)` on first insert).

## Exceptions: tables that never execute

C++ exceptions are data, not code — three emissions:

- **`.eh_frame`** — frame descriptions (CFI) for every function: how to
  restore registers and find the caller. Present even in C (backtraces need
  it); `.eh_frame_hdr` is its binary-search index.
- **`.gcc_except_table`** — the **LSDA** (language-specific data area), one
  per function that *catches* or runs cleanups. A function that only throws
  has no LSDA at all.
- **`__gxx_personality_v0`** — imported from libstdc++ (`U` symbol plus a
  `DW.ref.*` pointer in `.data.rel.ro`), consulted per frame during
  unwinding: "does this frame handle this exception?"

The throw site is recognizable by its sequence: `__cxa_allocate_exception
(sizeof(E))`, store the fields, `__cxa_throw(obj, &typeinfo_for_E, dtor)` —
the typeinfo argument (`rsi`) *names the exception type*. The catching side
decodes byte by byte (gcc 14.2.0):

```text
.gcc_except_table @ 0x2134 — LSDA for main() @ 0x11b6
+--------+--------------+------------------------------------------------+
| 0x2134 | ff           | LPStart encoding: omit (base = function entry) |
| 0x2135 | 9b           | type-table encoding: pcrel+indirect+sdata4     |
| 0x2136 | 11           | type-table offset: 17 (table ends at 0x2148)   |
| 0x2137 | 01           | call-site encoding: uleb128, rel. to base      |
| 0x2138 | 08           | call-site table length: 8 bytes                |
| 0x2139 | 18 05 24 01  | site 1: start main+0x18 (call risky), len 5,   |
|        |              |   landing pad main+0x24, action 1              |
| 0x213d | 2d 05 00 00  | site 2: main+0x2d (_Unwind_Resume call), no    |
|        |              |   landing pad, no action                       |
| 0x2141 | 01 00        | action 1: filter +1 = catch type #1, end       |
| 0x2144 | f4 1e 00 00  | type[1] = +0x1ef4 from 0x2144 -> 0x4038 =     |
|        |              |   DW.ref._ZTI1E -> [0x4038] = typeinfo for E   |
+--------+--------------+------------------------------------------------+
```

Site 1's landing pad `main+0x24` is exactly the `cmp $0x1,%rdx` dispatcher
that calls `__cxa_begin_catch` or `_Unwind_Resume`. **What an LSDA encodes**
is: *for this instruction range, if an exception is in flight, jump here and
try these type filters* — negative filters mean cleanup-only (run
destructors, keep unwinding), landing pad 0 means "pass through." One table,
parsed by a personality routine that never appears in your call graph.

## What insiders know

- **The SSO threshold is a library fingerprint**: libstdc++ 15 chars inline
  in 32 bytes (pointer +0, length +8, 16-byte buffer +0x10); libc++ 23 chars
  in 24 bytes; MSVC 15 in 32. Check `data() - &s` first.
- **The vector triple**: any `std::vector` on x86-64 is exactly
  `[begin | end | end_of_storage]` = 24 bytes. Three pointer rewrites
  bracketing a copy loop and a free is a `push_back` growth event.
- **RTTI hides at vptr-16, in .data.rel.ro**: the typeinfo pointer is the
  vtable slot before the address point; the typeinfo's own vptr selects
  `__class_type_info` / `__si_class_type_info` / `__vmi_class_type_info`, so
  the inheritance DAG is one array walk. `-fno-rtti` zeroes the slot but
  keeps the layout.
- **Two vptr writes = base+derived construction** at -O0; at -O2 with the
  base ctor inlined, dead-store elimination can leave one, and devirtualized
  calls may erase the vtable call entirely.
- **Sized delete leaks sizeof**: C++14 `operator delete(void*, size)` passes
  the class size in `rsi` at every delete site.
- **Demangle flags**: `c++filt -s gnu-v3` forces the modern ABI (legacy
  `gnu-v2` names decode wrong by default); `nm -C` / `objdump -dC` demangle
  in place; `-C` is useless on a stripped binary — RTTI is your only name
  source there.

## See it yourself

One file and binutils (gcc 14.2.0 Debian, binutils 2.44, glibc 2.41, x86-64
— mark your own versions):

```cpp
// shapes.cpp
namespace geo {
struct Shape {
    Shape() : id_(0) {}
    virtual ~Shape() {}
    virtual double area() const { return 0.0; }
    virtual const char* name() const { return "shape"; }
    int id_;
};
struct Circle : Shape {
    Circle(double r) : r_(r) {}
    double area() const override { return 3.141592653589793 * r_ * r_; }
    const char* name() const override { return "circle"; }
    double r_;
};
}
int main() {
    geo::Shape* s = new geo::Circle(2.0);
    double a = s->area();
    delete s;
    return a > 0.0 ? 0 : 1;
}
```

```text
$ g++ -g -O0 -o shapes shapes.cpp
$ nm shapes | rg _ZTV            # locate the vtables
0000000000003d78 V _ZTVN3geo5ShapeE
0000000000003d48 V _ZTVN3geo6CircleE
$ objdump -s -j .data.rel.ro shapes    # raw vtable bytes
 3d48 00000000 00000000 a83d0000 00000000  .........=......
 3d58 e6120000 00000000 10130000 00000000  ................
 3d68 a8120000 00000000 d4120000 00000000  ................
$ objdump -d -C shapes | rg -B1 -A2 "call.*Shape::Shape"
    1284: e8 59 ff ff ff      call   11e2 <geo::Shape::Shape()>
    1289: 48 8d 15 c8 2a ...  lea    ...# 3d58 <vtable for geo::Circle+0x10>
    1294: 48 89 10             mov    %rdx,(%rax)   # vptr write #2
```

Cross-check against the source: two virtuals + virtual dtor = four slots
(D1, D0, area, name); `r_` is the double at `+0x10`; sizeof = 24 = the
`operator new(0x18)` argument in `main`. Every number in the source now has
an address. In gdb: `p *((void**)s)` prints the vptr, `x/6xg 0x3d48` dumps
the six vtable words, `info vtbl geo::Circle` (gdb 12+) names the slots.
Then check yourself:

```text
$ c++filt _ZNK3geo6Circle4areaEv
geo::Circle::area() const
$ c++filt _ZTVN3geo6CircleE
vtable for geo::Circle
```

Rebuild at `-O2` and diff: the base vptr store is gone (dead-store
eliminated after inlining) and `s->area()` devirtualized to a constant.

## How to use this knowledge

- **Cold triage of a C++ binary**: `nm -C` (or an RTTI sweep on a stripped
  binary) plus the vtable walk yields class list, inheritance DAG, and
  virtual interfaces in minutes — the standard first hour of malware
  analysis on a C++ target.
- **Crash triage**: a segfault at `call *%rdx` after a vptr load means an
  object-lifetime bug (use-after-free, uninitialized or memset-zeroed
  object); match the freed chunk's size against the class's
  `operator new` argument to name the dead class.
- **Exploitability assessment**: a class is vtable-hijackable only if
  polymorphic — now you can enumerate exactly which heap objects carry
  vptrs (module 02 ch05's reclaim-then-dispatch needs that list).
- **ABI/library audits**: the SSO threshold and demangled `.dynsym` symbols
  identify libstdc++ vs libc++ vs MSVC — which decides link compatibility
  and which runtime advisories apply.

## Connections

- **Module 01 ch06** — the vptr/object-layout diagrams there are the same
  bytes this chapter reconstructs from outside; ch09 (RAII) explains why
  destructor slots (D0/D1/D2) appear at every scope exit.
- **Module 04 ch03** — its struct-offset methodology is the tool applied
  here to vtable slots; ch05 automates all of this (RTTI parsers, vtable
  xref scripts).
- **Module 02 ch05** — use-after-free: the reclaimed object's vptr is the
  attacker's dispatch; fake vtables are built from this chapter's layout.
- **Module 01 ch19** — allocator fingerprints (glibc chunk math, jemalloc
  size classes) are what you apply at `operator new/delete` call sites.
- **Module 10 ch05** — the emitter's side of the same ABI: mangling grammar,
  vtable emission, construction vtables/VTT, thunks.

## Quiz

1. `geo::Circle::Circle(double)` stores to `(%rax)` twice (the second via the
   inlined base constructor). Why does the ABI require the intermediate,
   base-class vptr value? Give the one situation in which the compiler may
   legally delete the first store.

2. Predict: what argument does `main` pass to `operator new` for
   `new geo::Circle(2.0)`, and which vtable offset does `s->area()` load its
   target from? Derive both from the source.

3. Predict: for `std::string a = "012345678901234"` (15 chars) and
   `std::string b = "0123456789012345"` (16 chars) on libstdc++ x86-64 —
   where does each `data()` point, and what does `b` pass to `operator new`?

4. A typeinfo object's vptr targets
   `__cxxabiv1::__si_class_type_info`. What does that tell you about the
   class, and what changes in the binary if it was built with `-fno-rtti`?

5. A crash at `call *%rdx` after `mov (%rax),%rax; add $0x8,%rax; mov
   (%rax),%rdx`, where `rax` pointed into a 48-byte chunk glibc had just
   reused. Diagnose the bug class and the mechanism.

6. This loop crashes with a heap-use-after-free, but only sometimes:
   `for (auto it = v.begin(); it != v.end(); ++it) if (cond(*it)) v.push_back(*it);`
   Explain the failure using the vector triple.

7. Decode by hand: `_ZNK3geo6Circle4nameEv` and `_ZTVN3geo5ShapeE`.

8. A 30-character `std::string` triggers `operator new(31)`, yet glibc 2.41
   reports `malloc_usable_size` of 40 for that block. Reconcile both numbers.

<details>
<summary>Answers</summary>

1. **The intermediate vptr is observable.** During base construction the
   object *is* a base: a virtual call inside the base constructor must
   dispatch to the base's override, and it reads the vptr the base
   constructor wrote. The store is removable only when the compiler can
   prove no such observation — at `-O2` with the base constructor inlined
   and no virtual calls between the writes (the lab's -O2 `main` shows one
   store).

2. **`operator new(0x18)`, slot `+0x10`.** sizeof(Circle) = vptr(8) + id_(4)
   + pad(4) + r_(8) = 24 = 0x18. The vtable is declaration-ordered with the
   virtual destructor in two slots first (D1, D0), so `area()` is entry 3:
   vptr + 0x10, matching `mov (%rax),%rax; add $0x10,%rax; ...`.

3. **`a.data() == &a + 0x10` (no allocation); `b` gets a heap block via
   `operator new(17)`.** The 16-byte SSO buffer sits at object +0x10 (after
   `_M_p` and the length), holding at most 15 chars + NUL. Sixteen chars
   exceeds it, so `_M_p` switches to the heap; the request is `len + 1` = 17
   for the terminator.

4. **Single, non-virtual inheritance** — `__si_class_type_info` is emitted
   only for exactly one non-virtual base, and the typeinfo carries a base
   pointer at +0x10 to walk upward. With `-fno-rtti`, the vtable's typeinfo
   slot (address point -0x08) still exists but is 0, and the `_ZTI*`/
   `_ZTS*` symbols vanish.

5. **Use-after-free surfacing at the virtual dispatch.** The object was
   freed; glibc put the 0x30-class chunk in tcache, whose next/key pointers
   overwrite the first 16 bytes — exactly where the vptr lives. Reuse
   overwrites it with new data; the virtual call (`add $0x8` selects the D0
   deleting-destructor slot) then loads its target from corrupted bytes.
   Module 02 ch05's reclaim-confuse-control: own the first 8 bytes and you
   own the dispatch (fake vtable).

6. **The triple is rewritten under the iterator.** `push_back` on a full
   vector (`_M_finish == _M_end_of_storage`) allocates a bigger block,
   copies, frees the old block, and rewrites all three pointers. The saved
   `it` is `_M_start`-relative and now points into freed memory, so `*it`
   and `++it` read freed bytes; the stale `v.end()` can also loop forever.

7. **`const char* geo::Circle::name() const`** — `_Z` (mangled) + `N`
   (nested) + `K` (const member) + `3geo 6Circle 4name` + `E` (end) + `v`
   (no parameters). **`vtable for geo::Shape`** — `_Z` + `TV` ("vtable
   for") + nested `3geo 5Shape` + `E`.

8. **`std::string` requests `len + 1`** — 30 characters plus the NUL = 31.
   glibc rounds to chunk granularity: 31 + 8-byte header = 39, rounded to a
   16-byte-aligned 48-byte chunk, of which 48 - 8 = 40 bytes are
   user-usable — module 01 ch19's bin arithmetic.

</details>
