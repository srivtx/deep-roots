# Chapter 05 — ABI & layout decisions

> **The 30-second version.** A compiler can invent anything *inside*
> one translation unit — but the moment two of its outputs must link,
> or C++ must call Rust, the contract begins. That contract is the ABI:
> how names are spelled for the linker (mangling), where arguments go
> (calling conventions — System V AMD64's 6 integer + 8 SSE registers),
> and where bytes sit in an object (struct and vtable layout). It
> explains why `extern "C"` exists (the C ABI is the one contract
> everyone implements), why C++ compilers never reorder struct fields
> while Rust does it by default (a promise, versus no promise), and why
> every cross-language call costs 10–100× a native one.

## The boundary problem: what two compilers must agree on

Link two object files — or two languages — and the compiler's four
private decisions (symbol spelling, argument placement, struct field
offsets, return path) become contracts. C++ needs more identity than
the linker's flat namespace: overloads (`abs(int)` vs `abs(long)`),
namespaces, templates (`Conn<int>` vs `Conn<double>`),
CV-qualification (`f() const` vs `f()`). Distinct declarations must
become distinct strings — **name mangling** is that reversible
encoding, and the Itanium C++ ABI is the grammar Linux and macOS
compilers use. From the *emitter's* side: encode just enough
declarative identity that no two declarations collide.

```text
_Z                     start
N  3net  4Conn  I i E  4send  E     nested name: "net"(3), "Conn"(4),
                                     template args <int>, "send"(4)
l                      parameter: long
                       => _ZN3net4ConnIiE4sendEl   (nm, g++ 14.2.0)
```

Identifiers are length-prefixed (`7Derived`); template args sit in
`I...E`; types are letters (`i` int, `l` long, `m` unsigned long, `d`
double) with prefixes `P` pointer, `R` reference, `K` const, `V`
volatile; a const member function inserts `K` after the `N` (`_ZNK...`).
CV-qualifiers must appear too — `cache::sendLH(const volatile
unsigned long*, char*)` mangles to `_ZN5cache6sendLHEPVKmPc` so it
cannot collide with its unqualified twin. Special names extend the
scheme: `_ZTV` vtable for, `_ZTI` typeinfo for, `_ZTS` typeinfo name
for, `_ZTT` VTT. MSVC encodes the same identity differently:
leading `?`, `@`-terminated scope components read inside-out
(`?draw@Shape@@...`), letter codes for access, convention and types
(`H` int, `N` double), trailing `@Z` — undecodable by `c++filt`
(undecoding is `undname.exe`'s job). Either way: **mangling is a
load-bearing promise.** GCC, Clang and ICC all speak Itanium on Linux,
which is the only reason clang-built objects link against libstdc++.

**`extern "C"` is the escape hatch** because the C contract is small
enough to implement in a weekend and frozen by each platform's psABI
forever: symbols are names, arguments follow the register table,
structs are flat memory, results return in `rax`/`rdx`. Every FFI —
cgo, JNI, ctypes, C#, Zig — targets it. The C ABI is the lingua
franca of language boundaries: nobody's favorite, everybody's second.

## Calling conventions: the argument placement contract

Registers are free; stack slots are a store plus a load; somebody
must pick which registers survive calls (chapter 03's
callee-vs-caller-saved decision). The ABI decides, once, for every
compiler.

**System V AMD64** (Linux, macOS, psABI §3.2):

```text
  classify each argument in 8-byte chunks: INTEGER (int/pointer bits)
  or SSE (float/double bits):

  f(int, double, long)          ->   edi, xmm0, rsi
  f(struct {double d; long n;}) ->   xmm0, rdi      <- one arg, two banks
```

- First 6 INTEGER chunks → `rdi, rsi, rdx, rcx, r8, r9`; first 8 SSE
  chunks → `xmm0`–`xmm7`; the banks fill independently. This is the
  **first-8-bytes-in-register classification**: a ≤16-byte struct is
  not a blob — each 8-byte half is classified and placed separately, so
  `{double d; long n;}` arrives *split across two register banks* (lab
  A). Over 16 bytes, or a mixed chunk, drops the whole argument to the
  stack.
- The 7th integer argument is the first to pay stack traffic — the
  lab shows `1..4` in `edi/esi/edx/ecx`, `5,6` in `r8/r9`, and `7` as
  a `push` (stack args start at `[rsp+8]` on entry).
- Returns ≤16 bytes: same classification, `rax` (plus `rdx` or
  `xmm0`/`xmm1`); >16 bytes: the caller passes a hidden pointer as the
  *first* argument in `rdi`, the callee writes there and returns the
  address in `rax`.
- The caller keeps `rsp` 16-byte aligned at each `call` (entry then
  sees `rsp % 16 == 8`), so callees can spill XMM registers with
  aligned `movaps` (32-byte if `__m256` is on the stack).

**ARM64 (AAPCS64)** turns the dial toward uniformity: first 8
integer/pointer arguments in `x0`–`x7`, floats in `v0`–`v7`, pure-float
aggregates (HFAs) in the `v` bank, and no SysV-style re-banking of
mixed small structs — composites ≤16 bytes ride `x`-register pairs.
One table, fewer surprises, less cleverness.

**Go turned the dial twice.** ABI0 passed everything on the stack —
stable, hand-assemblable, GC-friendly (stack maps of slots, not
registers) — at the price of memory writes per call even for two
arguments. Go 1.17 (August 2021) switched to **ABIInternal** with
register passing (9 integer + 15 floating registers on amd64),
reported ~5% whole-program improvement, more in call-heavy code. Go
now carries *two named ABIs* with compiler-generated wrappers between
them: an ABI switch is a compat project, not a patch.

## The C++ ABI: vtables from the emitter's side

Module 01 ch06 showed the vtable as a memory fact; emitting one, the
Itanium ABI §5.2.2 fixes it exactly — offset-to-top, typeinfo, one
slot per virtual in declaration order — and the vptr points 16 bytes
in, at the first virtual slot (the **address point**):

```text
  _ZTV7Derived  @ 0x403d90  in .data.rel.ro, 32 bytes   (g++ 14.2.0, Linux x86-64)

  symbol 0x403d90   +0x00  00 00 00 00 00 00 00 00   offset-to-top = 0
                    +0x08  d0 3d 40 00 00 00 00 00   typeinfo = 0x403dd0 = _ZTI7Derived
  address point --> +0x10  4c 11 40 00 00 00 00 00   slot 0   = 0x40114c = _ZN7Derived1fEi
                    +0x18  60 11 40 00 00 00 00 00   slot 1   = 0x401160 = _ZN7Derived1gEv
                             ^-- every Derived object's vptr = 0x403da0 = symbol + 0x10
```

**Construction vtables and the VTT.** While `B::B()` runs on a
`D`-most object, the dynamic type *is* `B` — virtual calls inside the
constructor must hit `B`'s versions, not `D`'s overrides. So before
each base constructor body runs, the compiler installs a **construction
vtable** — a B-view table pointing at `B`'s functions — and swaps in
`D`'s vtable when the most-derived body starts. Virtual inheritance
makes it hard: the shared `A` subobject's offset varies by
most-derived type, so `this`-adjustments cannot be baked in. The
**VTT** — a table of construction vtables plus secondary pointers,
56 bytes = 7 slots for the lab's diamond `D`, symbol `_ZTT1D` — is
passed as a *hidden extra constructor argument* so each base finds
the right table for this most-derived type.

**Thunks.** Covariant returns break the slot's promise: `D*
D::clone()` overrides `A* A::clone()`, but a caller through `A`'s
vtable expects an `A*` to the `A` subobject. The slot holds a
**thunk** — instructions adjusting `this` on entry and the returned
pointer on exit. Real symbol: `_ZTch0_h16_N1D5cloneEv`, "covariant
return thunk to `D::clone()`" — `h0` = no this adjustment, `h16` = add
16 to the result: exactly the `C`-in-`D` offset (measured: C at +16).
Virtual inheritance: the adjustment is not constant — `v0_n24`
encodes "fetch it from the vtable," because the shared base moves.

**The freeze.** Mangling, vtable shape, construction vtables, thunks —
all of it is why a g++-built `.so` links with clang++-built code, and
that interop is purchased with permanence: **the ABI is the layout,
the layout is the promise.** `std::string`'s 32-byte libstdc++ layout
and every exported struct's field order can never change, because
someone compiled against today's offsets. Field reordering is off the
table in C++ — the compiler *knows* `{char, long, char}` would be 12
bytes rearranged, and may not do it. **Rust chose the other side**: no
compiler links against another rustc's field layout, so `repr(Rust)`
promises nothing — rustc reorders fields to minimize padding
(`{u8, u16, u8}` → 4 bytes vs 6). `repr(C)` exists for exactly one
reason: to opt into C layout when a struct must cross a boundary.

## Layout flags: `#pragma pack` and bitfields

`#pragma pack(1)` / `__attribute__((packed))` strips padding — a
24-byte `{char; long; char}` becomes 16, a wire header 24 → 14. On
x86-64 it is *legal* (unaligned loads are allowed) and the cost is
where bytes fall relative to a cache line:

```text
  packed struct, 8-byte field `x` at an odd offset:

  |<----------------- 64-byte cache line ------------>|<-- next line --|
  ... [tag] [x0 x1 x2 x3 x4] | x5 x6 x7] ...
                           ^
    the load of `x` touches TWO lines: 2 L1 accesses. Measured on this
    Xeon: 1.4x per load when every access splits (lab B). A 4 KiB page
    split is ~100+ cycles; a `lock`ed access on a split address is a bus
    lock (~1000 cycles, or a #AC split-lock fault on Ice Lake+); several
    non-x86 ISAs trap outright.
```

The nastier property is social: **packing is not part of the type
system.** Two TUs including the same header, one built with
`-fpack-struct` — link cleanly and disagree about every offset past
the first padding site: silent corruption, no diagnostic (quiz 6).
Bitfields are the sibling: allocation is *implementation-defined*;
the SysV psABI pins C bitfields on Linux (LSB-first, no crossing the
storage unit), MSVC differs — why portable wire formats and Linux
UAPI headers avoid bitfields.

## The cross-language FFI tax

An FFI call looks like one call and is a small expedition: ctypes
converts Python `int` objects into C `int`s and back; cgo switches the
goroutine from its small *movable* stack to the fixed system stack
`g0` and pins it in a system call so the GC will not move the stack
mid-call; JNI pins or copies reference arguments against a moving GC.
Cluster the costs: **stack switches, pinning, marshalling copies** —
the call itself is the cheap part. Lab C measures the tax: 342 ns per
trivial ctypes call vs 0.6–0.8 ns pipelined native and 53 ns for a
pure-Python call — the boundary costs more than the interpreter that
made it. Hence FFI rule #1: *batch across the boundary* — one call
doing 1,000 units of work beats 1,000 calls doing one unit.

**COM is the historical coda.** It ran on one ABI fact: an interface
is a pointer to a pointer to an array of function pointers —
`IUnknown` = `QueryInterface`, `AddRef`, `Release` in slots 0–2, fixed
convention; no mangling (interfaces are GUIDs), no layout beyond
"first 4 bytes are the vtable pointer." That minimal contract let C,
C++, VB, Delphi and script interoperate in the 1990s — a vtable-only
ABI, the same object module 04 recovers from stripped binaries, seen
from the emitter's side. The smaller the ABI, the more languages meet
under it.

## What insiders know

- **The 6+8 rule is the whole SysV argument story:** 6 integer
  registers (`rdi, rsi, rdx, rcx, r8, r9`) and 8 SSE
  (`xmm0`–`xmm7`), filled independently per 8-byte chunk. The 7th
  integer argument is the first to pay stack traffic — 7 args is the
  threshold, not 9.
- **First-8-bytes classification means small structs split and big
  ones fall:** `{double; long}` arrives half in `xmm0`, half in `rdi`;
  past 16 bytes the whole argument drops to the stack. Returns ≤16
  bytes in `rax:rdx`/`xmm0:xmm1`; larger via a hidden `rdi` pointer
  returned in `rax`.
- **Why `repr(C)` exists:** `repr(Rust)` makes no layout promise —
  rustc reorders fields to kill padding, possibly differently between
  releases; `repr(C)` is the opt-in to C layout for FFI and wire
  formats. C++ has no such switch: Itanium interop makes field order a
  permanent public promise — C++ structs carry their padding forever,
  Rust's shrink.
- **The ctypes number to quote is ~100× a native call, and worse for
  trivial functions:** measured 342–441 ns per call (CPython 3.12.15,
  this Xeon) vs 0.6–0.8 ns pipelined C→PLT and 53 ns pure-Python — the
  boundary costs more than staying in the interpreter. cgo and JNI
  charge the same order (tens to ~200 ns, version-dependent), for the
  same three reasons.
- **Construction vtables exist because the dynamic type changes during
  construction:** while `B::B()` runs, virtual calls must hit `B`'s
  slots, so the vptr temporarily points at a B-view construction
  vtable fetched via the VTT (hidden constructor argument) — virtual
  calls inside constructors "ignoring" overrides is the rule, not a
  bug.
- **The address point and `offset-to-top` are forensic fingerprints:**
  the vptr points 16 bytes *into* `_ZTV...`, the typeinfo's first slot
  is itself a vptr into libstdc++'s typeinfo class (relocated at load,
  then RELRO), and `_ZTS` strings ("7Derived") outlive stripping —
  module 04 ch04's raw material.

## See it yourself

All labs ran on Linux x86-64 (Intel Xeon, family 6), g++ 14.2.0
(Debian), binutils 2.44, CPython 3.12.15.

**Lab A — emit a vtable and verify the Itanium layout by hand.** (No
gdb on this box; `objdump`+`nm` do the job — the gdb equivalent is
`x/4gx &_ZTV7Derived`.)

```cpp
// vcheck.cc
#include <cstdio>
struct Base    { virtual int f(int); virtual double g(); };
struct Derived : Base { int f(int) override; double g() override; };
int    Base::f(int x)    { return x + 1; }  double Base::g()    { return 1.5; }
int    Derived::f(int x) { return x + 2; }  double Derived::g() { return 2.5; }
int main() {
    Derived d;
    unsigned long* vptr = *(unsigned long**)&d;   // first 8 bytes of the object
    printf("sizeof(Derived) = %zu\n", sizeof d);
    printf("vptr = %p  [-2]=%ld  [-1]=%p  [0]=%p  [1]=%p\n",
           (void*)vptr, (long)vptr[-2], (void*)vptr[-1],
           (void*)vptr[0], (void*)vptr[1]);
}
```

```text
$ g++ -O0 -no-pie -std=c++17 vcheck.cc -o vcheck && ./vcheck
sizeof(Derived) = 8
vptr = 0x403da0  [-2]=0  [-1]=0x403dd0  [0]=0x40114c  [1]=0x401160
$ nm vcheck | rg "_ZTV7Derived|_ZTI7Derived|_ZN7Derived"
000000000040114c T _ZN7Derived1fEi
0000000000401160 T _ZN7Derived1gEv
0000000000403dd0 V _ZTI7Derived
0000000000403d90 V _ZTV7Derived
$ objdump -s -j .data.rel.ro vcheck
 403d90 00000000 00000000 d03d4000 00000000  .........>@.....
 403da0 4c114000 00000000 60114000 00000000  L.@.....`.@.....
```

Verify against the spec's rules, by hand: (1) vptr 0x403da0 − symbol
0x403d90 = 0x10 — the address point skips offset-to-top and typeinfo.
(2) `vptr[-2]` = 0 = offset-to-top, correct for a primary base at
offset 0. (3) `vptr[-1]` = `_ZTI7Derived` from `nm` ✓. (4) Slots hold
`_ZN7Derived1fEi` (0x40114c) then `_ZN7Derived1gEv` (0x401160) —
*declaration order*. (5) `sizeof(Derived)` = 8: two virtuals, zero
per-object cost. Bonus: `fm(Mix{double d; long n;}, int)` compiles to
`movq %rdx,%xmm0; mov %rax,%rdi` — one struct, two register banks.

**Lab B — pay the misalignment tax, measured.** Same 64-byte stride,
one 8-byte load per iteration; the only variable is whether the load
splits a cache line:

```c
/* pack3.c (abridged: two timed loops over data[64*i + 8] vs data[64*i + 60]) */
$ gcc -O2 pack3.c -o pack3 && ./pack3
aligned (+8 , no line split) : 0.059 s  (2.96 ns/load)  sink=0
packed  (+60, splits line)   : 0.084 s  (4.21 ns/load)  sink=0
slowdown: 1.42x
```

Every "packed" load touches two cache lines: +1.25 ns each, 1.42× —
on hardware famous for tolerating misalignment. The tax is real
exactly when access is latency- or line-bound, which is why "packed
is free on x86" folklore is simultaneously true and wrong.

**Lab C — the ctypes boundary vs its C twin.**

```text
$ gcc -O2 -fPIC -shared nativelib.c -o libnativelib.so    # int add2(int,int)
$ gcc -O2 native.c -L. -lnativelib -Wl,-rpath,. -o native && ./native
C loop -> add2: 0.57 ns/call  (sink 0)        # real `call add2@plt`, pipelined
$ python3 ffibench.py
ctypes CDLL add2                      342.3 ns/call
pure-Python def add(a,b)               52.9 ns/call
```

The Python side (10M calls of `lib.add2(1, 2)`, `argtypes` declared,
`time.perf_counter_ns`; two runs: 342.3 and 440.6 ns/call). The
ctypes call costs ~6.5 pure-Python calls — conversion and result
boxing dwarf the 0.57 ns the CPU spends on the call itself. Move the
loop into C (one crossing, a million iterations inside) and the tax
amortizes to noise — that asymmetry is the whole FFI playbook.

## How to use this knowledge

- **Linker error triage:** "undefined reference to `_ZN3net...`" is
  readable without a demangler — namespace, class, template args,
  parameters — and the usual causes (missing `extern "C"` on one side,
  a header compiled as C vs C++) are mangling bugs, not voodoo.
- **FFI architecture:** batch across the boundary, pass pointers over
  per-call conversions, and budget ~50–400 ns per trivial transition
  when deciding which side a hot loop lives on.
- **Struct design:** order fields large-to-small; `repr(C)` only at
  boundaries; `#pragma pack` is a wire-format tool with a measured
  latency cost, not a size optimization; bitfields stay out of
  cross-compiler structs.
- **Constructor mysteries:** a virtual call inside a base constructor
  hitting the base version is the construction vtable doing its
  mandated job — move the work out or document the two phases.

## Connections

- **Module 01 ch06 (object layout):** the vptr-in-object memory-model
  claim is emitted here — address point, slots, 16-byte prologue.
- **Module 01 ch07 (assembly reference model):** the SysV register
  table and 16-byte alignment are the ground truth that chapter reads
  off every disassembly.
- **Module 01 ch09 (C++) / ch10 (Rust):** the two halves of the layout
  promise — C++ freezes field order for interop; Rust's
  `repr(Rust)`/`repr(C)` split is the same decision made the other way.
- **Module 04 ch04 (recovering C++, queued):** the same vtable object
  this chapter emits, seen from the recovery side.
- **Module 05 (Android) / 06 (iOS):** platform ABIs in the wild —
  ART's JNI over ARM64 AAPCS64; Swift's 2019 ABI freeze repeating the
  C++ freeze story.
- **Module 12 ch01 (GPU, queued):** ABI divergence — CUDA/PTX has no
  SysV register table and no stack; the rules change when the machine
  does.

## Quiz

1. **Concept.** Why is `extern "C"` the escape hatch for *every*
   FFI — cgo, JNI, ctypes — not merely a C++/C convenience? What
   property of the C ABI makes it the universal meeting point?
2. **Concept.** GCC could shrink `struct {char a; long b; char c;}`
   from 24 bytes to 16 by reordering fields, and never does. Explain
   why, in terms of what the Itanium ABI purchased — and why Rust
   defaults to the opposite.
3. **Predict.** Decode by hand: `_ZN3net4ConnIdE4sendEv`. Then write
   the mangled names of `net::Conn<long>::send()` and
   `net::Conn<double>::send()` and say exactly where they differ.
4. **Predict.** Under System V AMD64, state the arrival registers
   for: (a) `f(int, double, long)`; (b) `g(struct {double d; long
   n;}, int)`; (c) `h(long, long, long, long, long, long, long)` —
   including where the last argument of (c) lives.
5. **Predict.** `sizeof` of `struct {char a; u16 b; char c;}` as
   `repr(C)` and as `repr(Rust)` (rustc as of 2024), and why.
6. **Bug diagnosis.** A C library and its test harness include the
   same header, but the library is built with `-fpack-struct=1` and
   the harness without. Both compile cleanly and link. Describe the
   symptom, and why no tool warns.
7. **Bug diagnosis.** `Base`'s constructor calls `virtual void
   init();`, which `Derived` overrides. A developer reports "the
   override is ignored during construction" and files a compiler bug.
   What is actually happening inside the object, byte-wise?
8. **Cross-chapter (module 04 ch04).** In a stripped binary you find
   a read-only 32-byte region `0, ptr, ptr, ptr`; the first `ptr`
   leads to a structure containing the string `"7Derived"`, the third
   to a function. Objects of some class carry a first member equal to
   *region address + 16*. Identify the region and explain how the
   string survives stripping.

<details>
<summary>Answers</summary>

1. **The smallest frozen contract per platform.** `extern "C"` strips
   C++ identity from the symbol (bare name, no mangling) and pins the
   call to the platform's C convention: psABI register table, flat
   struct memory, results in `rax`/`rdx`. Small enough that every
   runtime implements it; frozen forever. The C++ ABI encodes
   mangling, vtables, typeinfo — nobody else wants to reimplement
   that.
2. **C++ sold field order to buy link-level interop.** Itanium-ABI
   compliance lets GCC/Clang/ICC libraries interoperate — turning
   every struct layout into a public promise: the ABI is the layout,
   the layout is the promise. Rust has one compiler and no
   field-level interop: `repr(Rust)` promises nothing, rustc reorders
   freely, `repr(C)` opts in only at boundaries.
3. **`int net::Conn<double>::send()`.** `_Z` start; `N ... E` =
   `3net` + `4Conn` + template args `I d E` (double) + `4send`;
   trailing `v` = no parameters. `Conn<long>` swaps one letter:
   `_ZN3net4ConnIlE4sendEv` vs `_ZN3net4ConnIdE4sendEv` — one
   character distinguishes two instantiations, which is why mangling
   encodes template arguments.
4. (a) `edi`, `xmm0`, `rsi` — the banks fill independently. (b) One
   argument, two chunks: the double → `xmm0`, the long → `rdi`; then
   `i` → `rsi`. (c) Six longs fill `rdi, rsi, rdx, rcx, r8, r9`; the
   7th goes to the stack at `[rsp+8]`, with `rsp` 16-byte aligned at
   the `call`.
5. **`repr(C)`: 6 bytes** — `a` 1, pad 1, `b` 2, `c` 1, pad 1
   (alignment 2). **`repr(Rust)`: 4 bytes** — rustc reorders to
   `b(2), a(1), c(1)`, killing both pad bytes; the order is
   unspecified, the size class is not. The freedom C++ renounced in
   answer 2 is Rust's default.
6. **Silent field corruption, no diagnostic.** Packing is a
   compile-time directive, not part of the linker-visible type: both
   objects agree on symbols, the struct "type" is gone at link time.
   The library writes `b` at offset 1; the harness reads offset 8 —
   values scrambled, crashes rare until a pointer lands badly. Each
   TU is internally consistent; the mismatch is only *between* them.
   Fix: pack in the header, never in per-TU flags.
7. **The construction vtable, working as mandated.** While
   `Base::Base()` runs, the dynamic type is `Base`: a Base-view
   construction vtable (fetched via the VTT for this most-derived
   type) is installed, so the vptr's `init` slot points at
   `Base::init`. When the most-derived body begins, `D`'s real vtable
   replaces it. Rule, not bug.
8. **A vtable — `_ZTV7Derived`.** 32 bytes = offset-to-top (0 for a
   primary base), typeinfo pointer, virtual slots; first member =
   region+16 is the address point. The typeinfo holds the
   length-prefixed name string `"7Derived"` (an `_ZTS` typeinfo-name
   in `.rodata`), which survives stripping because string bytes are
   not symbols: stripping removes symbol names, not referenced data.
   Module 04 ch04's fingerprint.

</details>
