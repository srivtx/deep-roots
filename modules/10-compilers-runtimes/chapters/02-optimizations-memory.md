# Chapter 02 — Memory-shaping optimizations

> **The 30-second version.** Chapter 01 built the pipeline; this chapter
> catalogs the passes that change what your program's memory *actually does*
> at `-O2`. **SROA** dissolves stack structs into SSA values — why your local
> "vanishes" in gdb, and why module 03's uninitialized experiment flipped
> between `-O0` and `-O2`. **Alias analysis** decides which
> pointers may point at the same bytes; C's `restrict` and Rust's `&mut` are
> *aliasing contracts* that buy precision the compiler cannot compute.
> **Load/store surgery** — redundant-load elimination, store forwarding, DSE —
> deletes memory traffic, including the security scrubs you wrote on purpose.
> **LICM** hoists loop-invariant work out of loops when alias and safety
> conditions permit. All of it runs under one license: the as-if rule — do
> anything the language standard cannot observe; UB is, by definition,
> unobservable.

## The constitution: what "observable" means

The **as-if rule** (C11 §5.1.2.3) is every pass's constitution: do whatever
you like so long as observable behavior matches the abstract machine executing
the program literally. Observable is a short list — I/O, volatile accesses,
and (since C11) atomic synchronization — everything else is negotiable.

The twist: the abstract machine has holes. When behavior is undefined, there
is *no* correct observable behavior to preserve, so "assume the UB never
happens" is a sound deduction — free ammunition for every pass below.

## SROA: the stack destructor

Start from the frontend's problem (ch01). Emitting `int x = 5; x += 1;`
without analyzing anything means giving every named variable one home: a
stack slot. At `-O0` each local is an `alloca` plus `load`/`store`
instructions. Debug builds have variables because the debugger needs a fixed
address per name — and alloca-per-variable is what the frontend emits with
zero analysis.

LLVM's **mem2reg**, the canonical first optimizing pass, unpicks that: walk
the dominator tree, place phi nodes (ch01's register-merger) at joins where
two versions of a variable meet, replace each load with the SSA value that
reaches it, delete the non-escaping allocas. One pass turns "memory with
names" into "values with versions." Release builds have no variables because
nothing after mem2reg can see them — they are SSA values now, which register
allocation (ch03) keeps in registers or spills on demand. Nothing hides your
variable; it was dissolved.

**SROA** (scalar replacement of aggregates) extends mem2reg to structs and
arrays: a non-escaping aggregate alloca is split field by field into separate
scalars. GCC's twin is **SRA** (`-ftree-sra`); HotSpot C2's "scalar
replacement" (ch04) is the same idea in a GC language. The escape condition
is the whole game: pass `&p` to any call and the object must exist in memory,
because an unknown callee may hold the address — ch04's escape analysis, seen
from the factory floor.

```
BEFORE SROA: one 8-byte stack object            AFTER SROA: two SSA scalars, 0 bytes

  struct pair { int a; int b; };  p;
  rbp-8  +----------------+                     store i32 %mul, ptr %p.a  -> gone
         | p.a = x * 2    |   (store)           store i32 %add, ptr %p.b  -> gone
  rbp-4  +----------------+                                 |              |
         | p.b = y + 3    |   (store)                    %mul = mul i32 %x, 2
         +----------------+                                          %add = add i32 %y, 3
          return p.a + p.b;   (2 loads, 1 add)                          \    /
                                                                        add i32 %mul, %add

  8 bytes of frame, 4 memory ops            0 bytes of frame, 1 op: leal 3(%rsi,%rdi,2), %eax
```

This closes module 03 ch02's mystery: its program (an uninitialized `int x`
read after `dirty(41,1)` ran at the same stack depth) printed **42 at `-O0`**
and **1 at `-O2`** on gcc 14.2.0. At `-O0`, `x` has a fixed frame slot the
previous call still occupies (module 01 ch04's stack reuse); at `-O2`,
mem2reg never gives `x` a home, the read of an indeterminate value is UB,
and the function folds to a constant. Same UB, different evidence — why
"works at -O0, breaks at -O2" is folklore rather than diagnosis.

## Alias analysis: who may point where

Every optimization in this chapter reduces to one question: *can a load
through `p` see a store through `q`?* Three answers. **No-alias** — provably
different objects: free to reorder, merge, or delete. **Must-alias** —
provably the same object and offset: a store-then-load can forward the stored
value without touching memory. **May-alias** — cannot rule it out: assume the
worst; this answer is where your `-O2` performance goes to die.

A points-to analysis computes, per pointer, the set of objects it might
reference — ideally field-sensitively (`p` points at `s.b`, not just `s`).
But static aliasing is **undecidable in general** (Landi, 1992), so
production analyses are stacked approximations: LLVM's BasicAA (same-object
reasoning), globals-modref, and TBAA, cashing in C's rule that a `double*`
and an `int*` cannot alias (the `char*` exception is why byte-punning via
`memcpy` works).

Since analysis can't be precise, languages let you *sell* precision. C99's
`restrict` (6.7.3.1) promises: "for this scope, every access to this object
is based on this pointer." Violate it and behavior is undefined — and you
know what the optimizer does with UB. Rust's `&mut T` uniqueness is the
same contract, *enforced* rather than promised. The deep unification: **the
borrow checker is an alias analysis that runs at compile time and cannot be
wrong.** Rust lowers `&mut` parameters with LLVM's `noalias` attribute —
handing the optimizer, for free and everywhere, the answer C gets only where
someone wrote `restrict`.

## Load/store surgery

With alias facts, three passes delete memory traffic:

**Redundant-load elimination.** Two loads of the same address with no
intervening may-alias store collapse to one.

**Store forwarding.** A store to `*p`, then a load must-aliasing `p`,
becomes the stored value — no memory round-trip.

**Dead-store elimination (DSE).** A store no later load can ever see is
dead. Locals after their last read: dead — the frame pops (module 01 ch04).
A heap buffer just before `free`: dead, because compilers model `free` as
clobbering the whole object. The bytes often *physically* survive in the
freed chunk (module 03 ch02's UAF reads them), but the standard says they're
gone — CWE-14, "compiler removal of security-critical code." The pattern,
verified on gcc 14.2.0 below:

```c
void wipe_and_free(char *buf, size_t n) {
    memset(buf, 0, n);   /* intent: zero the secrets */
    free(buf);           /* DSE: free clobbers buf -> memset is dead */
}
```

Counters, ascending: `memzero_explicit()` — Linux's memset wrapped in a
compiler barrier, existing precisely because plain memset did not survive
DSE; `explicit_bzero()` (glibc ≥ 2.25); `memset_s()` (C11 Annex K, MSVC);
and `-ftrivial-auto-var-init=zero` (clang ≥ 8, gcc ≥ 12), which makes
initialization of every automatic variable a compiler *contract* rather than
a deletable store — targeting module 03 ch02's uninitialized reads, at under
1% fleet cost in Google's published measurements. Android's platform builds
ship it.

## LICM: motion and its two conditions

**Loop-invariant code motion** moves a computation out of a loop when it
produces the same value every iteration — an invariant load executed 1000
times should execute once. Two conditions gate it:

**Safety** — motion must not introduce a fault that didn't exist. A division
can't move above the guard protecting its zero divisor; a load can't move
above the branch establishing the pointer is non-null, unless the compiler
can prove dereferenceability (LLVM's `dereferenceable` metadata, from
inlining and allocation sites). Faulting on iteration 0 of a loop that might
run zero times is a behavior change.

**Aliasing** — an invariant load `*a` leaves the loop only if no store in
the loop may-aliases `a`:

```c
int sum_aliased(int *a, int *b, int n) {
    int s = 0;
    for (int i = 0; i < n; i++) {
        s += *a;       /* invariant load — but b[i] may alias a */
        b[i] = i;
    }
    return s;
}
```

Nothing rules out `a == b` (call it `sum_aliased(p, p, n)`), so the load
stays inside — verified on gcc 14.2.0, the loop body reloads
`addl (%rdi), %ecx` every iteration. Add `int * restrict b` and the answer
flips to no-alias — the load is hoisted to `movl (%rdi), %eax` *before* the
loop. One keyword, 999 loads saved.

## UB as optimization license

Each case has the same shape: *assume the UB never happens, optimize as-if,
watch the check you wrote evaporate.*

**The deleted overflow check.** `if (x + 1 < x) return true;` looks like a
wrap detector. But if `x + 1` wrapped, that's signed overflow — UB — so the
optimizer assumes it never wraps, and under that assumption `x + 1 < x` is
always false. gcc 14.2.0 at `-O2` compiles the function to
`xorl %eax, %eax; ret` — return false, unconditionally (Lab 1). Same
license, loop form: `for (int i = 0; i + 1 > i; i++)` — the exit test is
always true under the assumption, so it is deleted and the loop becomes
three instructions ending in `jmp` back to itself: an unconditional infinite
loop. Revoke the license project-wide with `-fwrapv` (defined
two's-complement wraparound) — the Linux kernel historically did exactly
that.

**The deleted null check.** Read this as facts, not lines:

```c
int deref_then_check(int *p) {
    int v = *p;        /* if p were null, UB right here */
    if (p == 0)        /* so: p is provably non-null    */
        return -1;     /* this branch is dead           */
    return v;
}
```

The dereference *proves* non-nullness for everything after it. gcc 14.2.0 at
`-O2` emits `movl (%rdi), %eax; ret` — check gone, `-Wall` silent (verified).
The production disaster shape: the deref sits in an earlier statement (a log
line touching `p->value`), the defensive check later; the crash lands at the
deref, and the post-mortem shows a function that "obviously checks for null."
Check *first*, or use clang's static analyzer / gcc's `-fanalyzer`, which do
catch the shape.

**The flag that never flips.**

```c
int ready, data;
int spin_wait(void) {
    while (ready == 0)   /* plain load — may be hoisted */
        ;
    return data;
}
```

gcc 14.2.0 at `-O2` hoists the load and emits `.L3: jmp .L3` — read once,
spin forever; the other thread's write is never seen (and it is also a C11
data race — UB twice over). The Linux kernel's `barrier()` is the cure:
```c
#define barrier() __asm__ __volatile__ ("" ::: "memory")
```

An empty instruction costing zero bytes of machine code that the optimizer
must treat as potentially touching all memory: nothing hoists across it, no
store near it is provably dead. With the barrier in the body, gcc reloads
`ready` every iteration (verified). Why not `volatile`? **volatile is not a
memory model.** It forces the reload but guarantees no ordering between the
volatile flag and the plain `data` write, no atomicity, and a racy volatile
read is still a data race. The correct tool is C11 atomics with explicit
memory order — module 09 ch01's subject; the kernel grew
`READ_ONCE`/`WRITE_ONCE` for the same reason.

## What insiders know

- **`restrict` and `&mut` are aliasing contracts, not optimizations.** They
  add no instruction — they *license* others to. Violating `restrict` compiles
  clean and detonates only at `-O2`; violating `&mut` (via unsafe pointer
  casting) lies to both Rust and LLVM's `noalias` attribute, which inlining
  propagates into aggressively wrong code.
- **DSE deleting your memset is not a compiler bug.** The fix list:
  `memzero_explicit` (kernel, barrier-based), `explicit_bzero` (glibc ≥ 2.25),
  `memset_s` (C11 Annex K/MSVC).
- **`-ftrivial-auto-var-init=zero|pattern`** (clang ≥ 8, gcc ≥ 12) converts
  the whole uninitialized-read family (module 03 ch02) into defined behavior;
  sub-1% fleet cost in Google's published measurements, shipped in Android
  platform builds. It does *not* save an explicit memset-before-free — that's
  the DSE bullet.
- **The null-check-after-deref removal is silent.** gcc 14.2.0's `-Wall`
  reports nothing (verified); clang's static analyzer and gcc `-fanalyzer`
  do. In review, look for dereferences lexically *above* null checks in the
  same function.
- **volatile is not a memory model** (a C11 footnote says so): no ordering
  with non-volatile accesses, no atomicity, racy volatile reads are still
  UB. Kernel `barrier()` is the zero-cost "forget what you know" switch;
  `READ_ONCE`/`WRITE_ONCE` are what the kernel actually uses for flags.
- **The as-if rule is a clause, not a metaphor** — C11 §5.1.2.3's observable
  behavior is volatile accesses, I/O, and atomic synchronization, nothing
  else. "The compiler broke my code" arguments are arguments about what
  counts as observable; UB is unobservable by construction.

## See it yourself

All labs verified on **gcc 14.2.0 (Debian, x86-64)**; clang 18 equivalents
noted where they differ. Every fragment is real output.

**Lab 1 — the deleted overflow check.**

```c
/* ovf.c */
#include <stdbool.h>
bool wrapped(long x) {
    if (x + 1 < x)   /* signed overflow would be UB here */
        return true;
    return false;
}
```

```text
$ gcc -O2 -S ovf.c && sed -n '/wrapped:/,/ret/p' ovf.s
wrapped:
        .cfi_startproc
        xorl    %eax, %eax      # return false — the branch is gone
        ret
```

Notice: no compare, no branch. Recompile with `-fwrapv` — the compare comes
back: wraparound is now defined.

**Lab 2 — the memset that never ran.**

```c
/* wipe.c */
#include <stdlib.h>
#include <string.h>
void wipe_and_free(char *buf, size_t n) {
    memset(buf, 0, n);
    free(buf);
}
```

```text
$ gcc -O2 -S wipe.c && sed -n '/wipe_and_free:/,/ret/p' wipe.s
wipe_and_free:
        .cfi_startproc
        jmp     free@PLT        # memset deleted; tail-call to free
```

Notice: the whole function is a tail-call to `free` — the memset never ran.
Swap in `explicit_bzero(buf, n)` (glibc ≥ 2.25) and it survives;
`gcc -O2 -fdump-tree-optimized -c wipe.c` shows the IR body is just
`free (buf_2(D)); [tail call]`.

**Lab 3 — one keyword, 999 loads (LICM + restrict).** Compile the
`sum_aliased` code with `gcc -O2 -S licm.c` and diff the two loops: the
aliased version reloads `addl (%rdi), %ecx` inside `.L3`; with `restrict` on
`b`, the load sits before the loop as `movl (%rdi), %eax`.

**Lab 4 — read the remarks.**

```text
$ gcc -O2 -fopt-info-inline-optimized -c remark.c
remark.c:7:56: optimized:  Inlining dot/0 into top/1.
```

LLVM's `-Rpass` is the same idea:
`clang -O2 -Rpass=dse wipe.c` prints one remark per deleted store pointing
at the memset's source line; `-Rpass-missed=licm` explains what failed to
move — the fastest way to find a hidden alias.

## How to use this knowledge

- **The -O0/-O2 debugging bridge.** A bug that only reproduces at `-O2` is
  one of three licenses: uninitialized read, UB-removed check, or an
  aliasing assumption. Bisect the *flags*, not the code: try `-O1`, then
  `-O2 -fno-strict-overflow`, then `-ftrivial-auto-var-init=zero` —
  whichever flips the bug names the family. When gdb says
  `<value optimized out>`, that is SROA working as designed: recompile that
  file with `-Og` instead of hunting ghosts.
- **Write scrubs that survive.** Heap secrets: `explicit_bzero`/
  `memzero_explicit` before free. Stack secrets die with the frame — unless
  a copy escaped; audit the escape, not the memset.
- **Keep hot structs un-escapable.** One `&p` handed to a log call pins the
  object to memory and disables SROA for it. Pass fields, not addresses, on
  hot paths.
- **Review C/unsafe-Rust interop as aliasing claims.** Every `&mut` crossing
  into C is a `noalias` promise made *to LLVM*; one aliased raw pointer
  underneath makes the optimizer wrong in both languages at once. (Interview
  version: "why did the null check disappear?" — the deref-before-check
  shape, `movl (%rdi), %eax; ret` as proof.)

## Connections

- **Module 01 ch04 (the stack)** — the frame SROA destroys is the frame that
  chapter drew; spill slots (ch03 here) are the only locals that come back.
- **Module 01 ch08 (C)** — UB as contract; this chapter is the contract cashed
  in, pass by pass.
- **Module 01 ch10 (Rust)** — the borrow checker reread as a compile-time
  alias analysis whose facts LLVM consumes as `noalias`.
- **Module 03 ch02 (memory-bug family)** — the 42-vs-1 uninitialized
  experiment, closed here by mem2reg; the UAF bytes DSE calls "gone" are the
  ones module 03's attackers read.
- **Module 03 ch04 (numeric family)** — the compiler side of overflow bugs:
  `x+1 > x` is only always-true because the bug is UB.
- **Module 09 ch01 (happens-before)** — `barrier()` and volatile-vs-atomics
  seen from inside the optimizer.
- **Module 04 ch03 (reading disassembly)** — recognize these shapes in
  binaries: a missing check, a hoisted load, a dissolved struct.
- **This module ch01** (SSA and the phi — SROA's raw material), **ch03**
  (where dissolved scalars get registers), **ch04** (escape analysis as SROA
  for GC languages), **ch05** (the ABI boundary — memory stops being
  dissolvable when a call must agree on layout).

## Quiz

1. **(Concept)** Distinguish *may-alias* from *must-alias*, and name one
   optimization each licenses or blocks.
2. **(Bug-diagnosis)** Production crash: `deref_then_check` faults at the
   `*p` load; the core shows `if (p == 0)` never compiled. Why is the check
   gone, why is `-Wall` silent, and what source change restores it?
3. **(Predict)** For the SROA diagram's function (`p.a = x*2; p.b = y+3;
   return p.a + p.b;`): how many stack slots does the `-O0` frame use, and
   what single x86-64 instruction does `-O2` emit for the whole function?
4. **(Bug-diagnosis)** A worker thread spinning on a plain `int ready` never
   exits the loop at `-O2` but works at `-O0`. Give the compiler's
   transformation and the language-level license. *(Connects module 09
   ch01.)*
5. **(Predict)** In `sum_aliased(p, p, 1000)`: how many times is `*a` loaded
   per call at `-O2`? What changes with `int * restrict b` — and why is
   calling the restrict version that way wrong?
6. **(Concept)** Security files "the compiler deleted our memset" as a bug.
   Argue the compiler's side from the as-if rule, and give two counters.
7. **(Bug-diagnosis)** A freed 1 KB heap chunk in a core dump still contains
   the plaintext key, though the code calls `memset` right before `free`.
   Which pass removed the write, on what grounds, and what is the one-line
   fix?
8. **(Predict, cross-module)** Module 03 ch02's program prints **42 at
   `-O0`** and **1 at `-O2`** on gcc 14.2.0. Explain both numbers with this
   chapter's passes. *(Connects module 03 ch02 and module 01 ch04.)*

<details>
<summary>Answers</summary>

1. **May-alias** — "cannot prove they differ" — is the pessimistic answer;
   it *blocks* redundant-load elimination, store forwarding, LICM hoisting,
   and vectorization, because a store through `q` might invalidate what the
   pass assumed about `p`. **Must-alias** — "provably same object and
   offset" — licenses store-to-load *forwarding*: the load becomes the
   stored value, no memory access.
2. The dereference runs first in the dataflow sense: a null `p` there is
   already UB, so the optimizer assumes it never happens — `p` is provably
   non-null afterwards, the branch is dead, leaving `movl (%rdi), %eax; ret`.
   `-Wall` stays silent because nothing is violated (gcc 14.2.0, verified).
   Fix: put the null check before *any* dereference, including ones hidden in
   earlier log statements — or run clang's static analyzer / gcc
   `-fanalyzer`.
3. Four slots at `-O0` — `p.a` and `p.b` (the 8-byte aggregate) plus spilled
   copies of parameters `x` and `y` (verified: offsets -8, -4, -20, -24 on
   rbp; gcc spills incoming registers at `-O0`). At `-O2`, SROA + mem2reg
   dissolve `p` and everything folds into one instruction:
   `leal 3(%rsi,%rdi,2), %eax` — `2*x + y + 3` in a single addressing mode.
4. The compiler hoisted the invariant load of `ready` (no may-aliasing store
   in the empty body), read the flag once, and compiled the loop to
   `.L3: jmp .L3` — a self-jump that can never observe the other thread's
   store (gcc 14.2.0, verified). The license: the unsynchronized read/write
   of a plain `int` is a C11 data race, hence UB, hence unobservable —
   hoisting is as-if-correct. Fix with atomics (module 09 ch01) or the
   kernel's `barrier()`/`READ_ONCE`, not `volatile` alone.
5. Aliased: **1,000 loads** — the store `b[i] = i` may-aliases `*a` (here it
   must-alias, `a == b`), so the load stays in the loop. With `restrict`:
   **1 load**, hoisted before the loop. Calling the restrict version as
   `sum_restrict(p, p, n)` breaks the contract — "all accesses to this object
   are based on `b`" is false because `a` accesses it too — which is UB.
6. The as-if rule preserves observable behavior; a store no legal load can
   ever observe is not observable. After `free`, the object's lifetime is
   over, no subsequent load is legal, so the memset's effect is unobservable
   and deleting it is as-if-correct (CWE-14). Counters:
   `explicit_bzero`/`memzero_explicit` for the free case;
   `-ftrivial-auto-var-init=zero` (clang ≥ 8, gcc ≥ 12) for the
   uninitialized-stack case.
7. **DSE** removed it, on the ground that `free` clobbers the entire object
   — post-lifetime stores are dead (verified: `jmp free@PLT`, gcc 14.2.0).
   One-line fix: `explicit_bzero(key, 1024);` (glibc ≥ 2.25) or
   `memzero_explicit()` — both built so DSE cannot prove the bytes dead.
8. At `-O0`, every local has a fixed alloca slot; `f`'s uninitialized `x`
   occupies the offset `dirty`'s locals used one call earlier, and
   `dirty(41,1)` left 41 there — stack reuse (module 01 ch04) — so `x + 1`
   prints 42. At `-O2`, mem2reg promotes locals to SSA values; `x` never gets
   a memory home, the read is UB, and the optimizer folds `x + 1` to a
   constant (0 + 1), printing 1. Neither number is right — the UB is the
   same, only the evidence differs.

</details>
