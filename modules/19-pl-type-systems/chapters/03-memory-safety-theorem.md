# Chapter 03 — Memory safety as a theorem

> **The 30-second version.** "Memory safe" is a four-clause
> theorem: touch only memory you own, stay inside bounds, never
> lie about types at the byte level, never reach after a
> lifetime ends. That is type soundness (chapter 02) with
> addresses as terms — the *type of an address* is "what may
> live there" — and the MMU already runs a 1-bit version of
> this proof on every access: a segfault is the hardware's type
> error. Rust moved most of the proof to compile time with
> affine types (ownership) and a proof relation on intervals
> (borrowing), behind a trusted kernel (`unsafe`). Every module
> 01 language sits on this spectrum: who proves safety, when,
> which hole.

## The theorem, stated at the address level

A program is **memory-safe** (Crary's formulation) if every
evaluation that reaches a value

- **(a) owns its accesses** — no wild reads or writes into
  memory the program has no right to touch;
- **(b) respects bounds** — an access to object `o` at index
  `i` stays inside `o`;
- **(c) never puns types at the byte level** — no reading a
  `float`'s bytes as a `Node*`;
- **(d) never touches memory after its lifetime** — no
  use-after-free, no dangling references.

The identification that organizes the chapter: **memory safety
is type soundness at the address level** — the type of an
address is *the set of values that may legitimately live there
now*: (b) a bounds type, (c) an ordinary type, (d) a lifetime
region, (a) an ownership predicate. The machine already runs
one proof of this shape: module 01 chapter 03's page table
gives every 4 KiB page a 3-bit permission *type* (R/W/X), and
the MMU **type-checks every access against it, in hardware,
for free**. Writing an R-only page is a type error the hardware
throws — we call it SIGSEGV; NX is a soundness rule;
`mprotect` a checked cast. The granularity is terrible — one
3-bit type per 4,096 bytes — but the shape is Java's per-object
tag word, at page scale. Every language below is a point on one
axis: **how fine-grained the address types are, and who proves
the program well-typed — compiler, runtime, or nobody.**

## Ownership is an affine type system

Rust's ownership is a 1987 theorem that took 28 years to ship
(Girard's linear logic, Wadler's linear uses, Rust 1.0 in
2015). **Affine**: a value may be used *at most once*.
**Linear**: *exactly once*. **Unrestricted**: as often as you
like.

```
          unrestricted      affine             linear
  uses:      0 .. 8           0 .. 1             exactly 1
  owner:     anyone           one at a time      one, obligatorily
  drop:      GC or manual     implicit, at       explicit, on every
             (never/rare)    last-use point     path, even panics
  langs:     Java, Python     Rust (move =       academic linear
             C++ values       consume)           lambda calculi
```

**Why affine won: implicit drop.** Linear types force explicit
consumption on *every* path — panic paths included. Affine lets
the compiler insert the destructor where the value dies, and
the unwinder runs those drops even on panic: the `move`
consumes, the `drop` you never write is the obligation the
compiler discharges.

**Borrowing is the proof relation that buys back usability** —
pure affinity would make a linked list unusable. `&'a mut T`
claims: "for interval `'a` I hold the unique live reference
into `T`," and the checker proves two invariants: *shared XOR
mutable* (many `&T`, or one `&mut T`) and *lifetime containment*
(no reference outlives its target).

**NLL — non-lexical lifetimes, the 2018 borrow-checker
rewrite** — changed what "interval" means: pre-2018, a borrow
lived from declaration to closing brace. NLL reformulated
borrowing as **region inference over a constraint graph solved
by fixed-point iteration** — conceptually ~100 lines: every
*use* of a reference emits "its region includes this point";
containment constraints link regions to their targets; iterate
to a fixed point — regions grow by *liveness*, not syntax
(polynomial, milliseconds in practice). Two borrows whose
solved regions intersect = the error. The live range ends at
**last use**, not scope end:

```rust
let mut x = 42;      // 1
let r = &mut x;      // 2   borrow starts
*r += 1;             // 3   last USE of r: live range ENDS here
println!("{}", x);   // 4   x used directly: NLL accepts
```

```
line:       1        2        3        4        5   end of block
            |        |        |        |        |
x  owner:   =========================================================>
r  borrow:            [========]
                      starts   ends at LAST USE, not scope end
lexical     [============================================]  <- conflicts
(2015):     starts                                       scope ends
NLL         [========]                                   no overlap  OK
(2018):
```

Under 2015 rules, `r`'s scope ran to the brace, so line 4 used
`x` while mutably borrowed — E0502, `cannot borrow \`x\` as
immutable because it is also borrowed as mutable`. Under NLL,
line 3 spent the borrow: same invariants, better search.

**PhantomData and variance.** A type parameter invisible in
your fields needs `PhantomData<T>` so the compiler knows your
type *acts like* it holds a `T` — for drop-checking and
**variance**. `&'a T` is *covariant* — a `&'long T` works
where `&'short T` is expected, because a read-only view can be
safely forgotten. `&'a mut T` is covariant in `'a` but
**invariant in `T`** — load-bearing:

```rust
fn shorten<'short, 'long: 'short>(r: &mut &'long i32, s: &'short i32) {
    *r = s;    // E0308: mismatched types -- lifetime mismatch:
}              // expected &'long i32, found &'short i32
```

`*r = s` writes a `&'short i32` into a slot typed `&'long i32`
— covariance would let `s` coerce up, and that is what
invariance forbids *through a mutable alias*. Suppose the
coercion `&mut &'long i32` → `&mut &'short i32` (shortening
`'a` through the `&mut`) were allowed. The kill program:

```rust
fn plant(r: &mut &'static i32, s: &i32) {
    let short: &mut &i32 = r;  // if this coercion existed...
    *short = s;                // ...plant a short-lived ref into
}                              // a 'static-typed slot

fn main() {
    let local = 42;
    let slot: &i32 = &local;    // type promises a long region
    { let temp = 7;
        plant(&mut slot, &temp); }  // temp dies HERE
    println!("{}", slot);  // reads &temp: use-after-free, no cast
}
```

The write plants a reference that dies early into a slot whose
type promises it lives long; every long-typed reader then reads
a dangling pointer. Lengthening fails symmetrically: reading a
`&'long` out of a slot storing a `&'short`. Only invariance
forbids both — mutable slots must be invariant or every write
becomes an `ArrayStoreException`; read-only slots may be
covariant because forgetting is harmless. Chapter 02's
Java-arrays theorem, hoisted to compile time.

## `unsafe`: the trusted kernel pattern

Rust's safety is a proof decomposition: safe code is proven by
the compiler; every `unsafe` block is a theorem a human
discharges — *unsafe = "I prove it, compiler trusts."* The
program is safe **iff** every unsafe block upholds the
invariants its safe interface advertises — the OS
trusted-kernel pattern. `Vec` is the case study:

```rust
pub struct Vec<T> { ptr: Unique<T>, cap: usize, len: usize }
```

- `ptr` points at `cap` slots from the global allocator, with
  no other live pointer into that allocation (uniqueness — why
  `push` takes `&mut self`);
- `0 <= len <= cap` always, and `[0, len)` are initialized
  `T`s — `set_len` *borrows* this invariant, its caller must
  restore it;
- drop frees the allocation exactly once (affinity's
  destructor as a manual obligation);
- growth must not strand outstanding `&T`/`&mut T` in the old
  buffer — a use-after-free the safe API prevents via the
  borrow on `self`.

Safe code cannot violate these — the membrane; std is a few
hundred unsafe blocks behind checked interfaces, and your
project's audit surface is its `unsafe` count.
## The spectrum: every language as a proof system

The module 01 languages: what proves the four clauses, when,
the hole. One 6-line witness each.

| language | proof mechanism | when | the hole |
|---|---|---|---|
| C | none — hardware faults only | runtime, on crash | all four clauses; OOB *compiles* |
| C++ | RAII + `shared_ptr` refcount | scope end / runtime | no aliasing proof; cycles leak |
| Rust | affine types + borrow proof | compile time | `unsafe`; wraps in release |
| Go | bounds checks + GC | runtime | data races (detector opt-in) |
| Java | GC + klass tag | runtime (pauses = proof cost) | covariant arrays, erasure |
| C# | same + nullable retrofit | runtime + compile warn | warns, not errors |
| Python | `Py_TYPE` tags + GC | runtime | `ctypes`/C extensions erase it |
| JS | maps + GC; OOB gives `undefined` | runtime | silent undefined; native edge |
| Swift/ObjC | ARC, compiler-inserted | runtime counting | cycles; ObjC legacy |
| Haskell/OCaml | static types + GC + purity | compile + runtime | `Obj.magic`, FFI |
| COBOL | no pointers/heap; PIC | compile (layout) | silent MOVE truncation |

**C** — no proof, by design (gcc 14.2, `-Wall` silent, live
run; past `a[2]` is stack garbage):

```c
#include <stdio.h>
int main(void) {
    int a[3] = {10, 20, 30};
    for (int i = 0; i < 6; i++)        /* i=3..5: out of bounds */
        printf("a[%d] = %d\n", i, a[i]);
    return 0;                          /* exit 0, no complaint */
}
```

```console
a[3] = 3
a[4] = 1
a[5] = 0     # garbage; -fsanitize=address is the retrofit
```

**C++** — RAII is affine intuition without an aliasing proof;
`shared_ptr`'s runtime refcount is unsound on cycles (live run
— no `~Node` fires):

```cpp
#include <cstdio>
#include <memory>
struct Node { std::shared_ptr<Node> peer; ~Node() { std::puts("~Node"); } };
int main() {
    {   auto a = std::make_shared<Node>();
        auto b = std::make_shared<Node>();
        a->peer = b; b->peer = a;     // rc(a)=rc(b)=2: the hole
    }                                 // brace ends: rc 2->1, never 0
    std::puts("scope ended, no ~Node printed: both objects leaked");
}
```

**Rust** — compile-time proof, real error text:

```rust
fn main() {
    let mut x = 0;
    let r1 = &mut x;
    let r2 = &mut x;    // second unique reference: rejected HERE
    *r1 += 1;           // because r1 is used after (live range)
    *r2 += 1;
}
```

```text
error[E0499]: cannot borrow `x` as mutable more than once at a time
  |     let r1 = &mut x;
  |              ------ first mutable borrow occurs here
  |     let r2 = &mut x;
  |              ^^^^^^ second mutable borrow occurs here
  |     *r1 += 1;
  |     ------ first borrow later used here
```

The bold clauses are the constraint graph's conflict witness.
Holes: `unsafe`, plus release-mode wrapping (chapter 05).

**Go** — (b) and (d) proven *late* (bounds checks, GC); data
races are the hole, policed by an opt-in runtime oracle:

```go
var counter int
func main() {                   // go run -race race.go
    go func() { counter++ }()   // goroutine 6: write
    counter++                   // main: write, same variable
}
// WARNING: DATA RACE -- Write at 0x... by goroutine 6 / main
// (vector clocks, module 09 ch02); 5-10x cost keeps -race out
// of production.
```

**Java** — GC proves liveness, the klass word proves type at
every cast, and the *pause is the proof cost* (chapter 04).
Live run (JDK 21):

```java
import java.util.*;
public class Erasure {
    public static void main(String[] args) {
        List<String> xs = new ArrayList<>();  // compile-time proof
        List raw = xs; raw.add(42);           // raw type: suspended
        String s = xs.get(0);                 // cast fires at the READ
        System.out.println("never reached: " + s);
    }
}
```

```text
Exception in thread "main" java.lang.ClassCastException: class
java.lang.Integer cannot be cast to class java.lang.String ...
at Erasure.main(Erasure.java:7)
```

**C#** — same machinery plus the 2019 nullable retrofit — a
*warning*, i.e., a proof you can ignore:

```csharp
static void Main() {
    string s = Find("key");            // string, not string?
    System.Console.WriteLine(s.Length);
}
static string Find(string k) => null;  // CS8603: possible null
// warning CS8602: Dereference of a possibly null reference.
// C stays silent; C# leaves a witness in the log.
```

**Python** — runtime tags on every operation (a real share of
interpreter cost); the hole is the C boundary: `ctypes` erases
the proof by changing the pointer's *type* (live run):

```python
import ctypes
a = (ctypes.c_int * 3)(10, 20, 30)    # the TYPE carries length 3
p = ctypes.cast(a, ctypes.POINTER(ctypes.c_int))  # length erased
for i in range(6):                    # the bounds check lived in
    print(f"p[{i}] = {p[i]}")         # the type; 3..5: raw reads
```

```console
p[3] = 0
p[4] = 1535784240    # heap garbage; sometimes SIGSEGV instead
p[5] = 32706
```

**JavaScript** — V8 maps are per-object runtime types; OOB
reads return `undefined` — proof by refusing, not crashing
(live run, node 22):

```js
const a = [10, 20, 30];
console.log(a[3]);            // undefined: safe read, wrong logic
const t = new Int32Array(3);  // raw memory, runtime-bounded
t[3] = 42;                    // no exception: silently dropped
console.log(t[3]);            // undefined -- no crash
```

**Swift / Objective-C** — ARC is a refcount proof whose
*insertion points the compiler chooses*: `swiftc -emit-silo` on
a 5-line file holding a class reference shows the obligations
as SIL — `strong_retain`/`strong_release` pairs bracketing each
use region, one pair per borrow. The counter runs at runtime,
so it inherits `shared_ptr`'s hole: cycles retain forever —
why `weak`/`unowned` exist, and why ObjC's
`unsafe_unretained` says so in the type's name.

**Haskell / OCaml** — static types, GC, purity as a bonus
theorem (no hidden mutation). Proof working, OCaml:

```ocaml
type shape = Circle of float | Square of float
let area s = match s with
  | Circle r -> 3.14159 *. r *. r
(* Warning 8: this pattern match is not exhaustive.
   Here is an example of a case not matched: Square _ *)
```

**COBOL** — no pointers, no heap: (a) and (d) vacuous — safety
by *impoverishment*. The type system is the PICTURE clause
plus the record layout (module 01 ch17), its bound silent:

```cobol
       WORKING-STORAGE SECTION.
       01 NAME PIC X(10).
       PROCEDURE DIVISION.
       MAIN.
           MOVE "ABCDEFGHIJKLM" TO NAME.  *> 13 chars into 10
           DISPLAY NAME.                  *> "ABCDEFGHIJ": truncated
```

No crash ever, but no complaint either: unfailable means silent
data loss.
## Can't prove it, therefore patch it: mitigations as runtime proofs

Module 02 chapter 06's ladder, re-derived: each rung is a
runtime proof system — a fragment of the type system C refused
to have.

- **Canaries** — proof of *intact return path*: the 8-byte
  `AT_RANDOM`-derived secret (module 02 ch02) is a per-frame
  certificate checked at the epilogue — one bit of "return
  address still mine."
- **ASLR** — randomness as a *proof substitute*: the system
  cannot prove an attacker doesn't know an address, so it
  randomizes (~28 bits for x86-64 library mappings); each
  guess is a 2^-28 type-check that fails into a crash.
- **CFI / PAC** — the proof oracle in hardware: indirect branch
  targets get a *type* (the legal-target set), and PAC signs
  return addresses with a per-process key so `ret` becomes
  `verify + jump` — the X-bit treatment per branch, not per
  page.
- **MTE** — the honest runtime type tag: every 16-byte granule
  carries a 4-bit allocation tag in the pointer's top bits,
  checked by the MMU on every access (first consumer silicon:
  Tensor G3, Pixel 8, 2023). The tag width *is* the error
  budget: 4 bits = 16 tags, so a wild pointer's random tag
  collides with the target granule's with probability 1/16 —
  **6.25% false negative per bad access, 15/16 = 93.75%
  caught**, each catch a loud crash in sync mode; four escaping
  accesses survive (1/16)^4 ≈ 0.002%. False positives for
  correct code: zero — the tag is a certificate the allocator
  maintains, itself a trusted kernel; a fault on correct code
  means a tag-manager bug, never noise. The dangling corner: a
  re-allocated granule's fresh random tag equals the stale
  pointer's 1/16 of the time — that UAF walks through
  undetected. MTE is this chapter's thesis in silicon: *the
  hardware runs a type system — 4 bits where Rust's compiler
  uses a lifetime solver.*
## What insiders know

- **`unsafe` is a proof obligation, not a license**, and the
  culture rule is *local provability*: every unsafe block
  carries an invariant comment checkable against a list like
  Vec's. The folklore name for its absence is *fear-driven
  development* — avoiding `unsafe` so hard you build worse safe
  structures. The 2024 edition makes unsafe ops inside an
  `unsafe fn` need their own `unsafe {}` block
  (`unsafe_op_in_unsafe_fn`): an `unsafe fn` is a contract on
  callers, not a license on its body.
- **`Pin` is the theorem "this object will not move."** A struct
  holding a pointer into itself dangles if moved — an
  intra-object use-after-free no borrow checker can see, because
  the borrow is inside the value. `Pin<&mut T>` certifies
  stability; `async` is the reason: the compiled future is a
  state machine whose frames point into its own fields, so it
  must not move between `poll`s.
- **The borrow checker proves an approximation; Miri checks a
  model.** Stacked borrows (2018) and tree borrows (its 2023
  refinement) are *operational* models of reference validity —
  rules an interpreter can check exactly. `cargo miri` executes
  the program under the model and reports UB the approximation
  admits — "no conflict found" is weaker than "the conflict
  provably cannot happen."
- **`Box::leak` is deliberate proof weakening** — it consumes
  the box and returns `&'static mut T`: "I promise never to free
  this," an affine value made unrestricted. The standard trick
  for startup-built global caches.
- **`checked_add` is a type-level service.** Returning
  `Option<u32>` forces every caller to handle overflow *now*; C
  says overflow is UB, licensing the optimizer to delete your
  bounds check (module 03 ch03). Rust's hole: release builds
  wrap silently — the theorem holds only where you asked.

## See it yourself

**Lab 1 — the doubly-linked list: one insert, two universes.**
The canonical unsafe-required case. C++ (compiles and runs
clean under `g++ -std=c++17 -Wall -Wextra`):

```cpp
struct Node { Node* next; Node* prev; int v; };
void insert_after(Node* p, int v) {      // the classic DLL insert
    Node* n = new Node{p->next, p, v};   // two raw writable aliases
    if (p->next) p->next->prev = n;      // into one list: no proof
    p->next = n;                         // required, none attempted
}
int main() { Node h{nullptr, nullptr, 0}; insert_after(&h, 1); }
```

Rust needs `&mut` to two neighbors at once — `prev.next` *and*
`next.prev`, reachable through one list. Safe Rust rejects
every phrasing; the errors are real:

```rust
struct Node { next: Option<Box<Node>>, prev: Option<Box<Node>> }
fn insert_after(prev: &mut Node, fresh: &mut Node) {
    if let Some(old) = prev.next.take() {
        old.prev = Some(Box::new(fresh));   // alias the new node...
        fresh.next = Some(old);
        prev.next = Some(Box::new(fresh));  // ...then MOVE it: E0505
    }
}
```

```text
error[E0505]: cannot move out of `*fresh` because it is borrowed
  |     old.prev = Some(Box::new(fresh));
  |                              ----- borrow of `*fresh` occurs here
  |     prev.next = Some(Box::new(fresh));
  |                        ^^^^^ move out of `*fresh` occurs here
```

Storing `&mut` neighbors instead yields E0499, `cannot borrow
... as mutable more than once at a time`. The working version is
the paper exercise: a `NonNull<Node>` ring with `unsafe` blocks
whose comments read like Vec's invariant list — *every node
freed exactly once on drop; every NonNull non-null and
initialized; no two live `&mut` reach one node; links removed
before free.*

**Lab 2 — Miri, or the honest paper version.** With a toolchain:
`cargo miri run` on a dangling deref (`Box::new(5)`, keep a
`*const i32`, `drop` the box, read through it) halts with
`Undefined Behavior: pointer to alloc N was dereferenced after
it was freed` (wording as of Miri 2024; ids vary). Without one:
read stacked/tree borrows as the rule the interpreter checks,
and mark which of your unsafe blocks it would catch.

**Lab 3 — erasing Python's proof.** Run the ctypes program
above, then the control: `a[5]` on the array itself raises
`IndexError: invalid index` — the check lived in the array
*type* `(c_int * 3)`, and `cast` to `POINTER(c_int)` deleted
the 3. The garbage changes across runs (adjacent heap
metadata); occasionally the interpreter segfaults instead —
clause (a), witnessed.

**Lab 4 — Java's two proof times.** `java Erasure.java` (JDK
11+, single-file launcher) prints the `ClassCastException` — the
check fires at the *read*, not the insert: erasure left no tag
to check at `add`. Then arrays: `Object[] o = new String[3];
o[1] = Integer.valueOf(42);` throws `java.lang.ArrayStoreException:
java.lang.Integer` at the *write* — arrays are reified, so the
store is checkable; generics are not.

**Lab 5 — TypeScript `any` bisection.** On a large legacy TS
build: `npx tsc --noEmit | wc -l` for the error count; bisect by
casting the boundary of half the files to `any` and re-running —
the count that vanishes is the proof debt you took on. `unknown`
is the disciplined version: a debt type that must be paid with
a check before use, so it stays in the error count until you
do.
## How to use this knowledge

- **CVE triage in four clauses.** Name the clause a
  vulnerability violates; the clause predicts the mitigation
  that catches it (canary/CFI for control data, ASan for (b),
  runtime tags or CFI for (c), MTE for (d)).
- **Choosing a component's language.** Who proves the four
  clauses, and does the proof time fit the threat model? A
  parser over untrusted input wants the proof *before* the
  bytes arrive; a plugin host can pay GC pauses; a kernel can
  afford neither — hence Rust and SPARK in kernels.
- **Reviewing unsafe/FFI code.** Use Vec's list as the template:
  invariants as comments, each block checked against them, Miri
  in CI. An unsafe block without an invariant comment is a
  theorem without a proof — reject the diff.
- **Interview-grade variance.** Deriving `&mut T`'s invariance
  in 60 seconds — write side and read side — is the fastest
  demonstration of real subtyping understanding, and it
  generalizes to Java arrays and C#'s `out T` on the spot.
## Connections

- **Module 19 chapter 02** — soundness as progress +
  preservation, instantiated here with addresses as terms; the
  Java-arrays invariance derivation reused.
- **Module 19 chapter 04** — the runtime half of the spectrum:
  GC as proof search, pauses as proof cost, tags as
  certificates.
- **Module 19 chapter 05** — what this proof cannot buy:
  overflow, logic bugs, the soundness/expressiveness trade.
- **Module 01 ch08-17** — the languages placed here, re-read as
  proofs: C's (ch08) unchecked casts, C++'s (ch09) RAII,
  Rust's (ch10) borrow checker, Go's (ch11) race hole, Java's
  (ch12) klass-word certificates, Swift's (ch16) ARC insertion
  points, COBOL's (ch17) PICTURE-clause bounds.
- **Module 01 chapter 03** — the page table as the original
  hardware type system: R/W/X, segfault as type error.
- **Module 02 chapter 06** — the mitigation ladder re-derived
  here as runtime proof systems; ch02 for the canary.
- **Module 04 chapter 04** — the vptr as a *type-erased* runtime
  proof: one tag word selecting the legal method set — Java's
  tag by convention, which is why CFI must guess it back.
## Quiz

1. **Concept.** State the four clauses of memory safety, and map
   each to what enforces it in Java. Which clause does an MMU
   page check enforce, at what granularity?
2. **Concept.** Define affine, linear, unrestricted by allowed
   uses; explain — via panics and unwinding — why Rust chose
   affine; name what the compiler inserts that a linear
   language would force you to write.
3. **Predict.** `let mut v = vec![1]; let r = &mut v[0]; *r +=
   1; v.push(2);` compiles today but failed 2015 rustc. Draw
   the borrow's live range, name the use that ends it, and say
   what the old checker used instead.
4. **Derive (the variance counter-example).** Suppose
   `&mut &'long T` could coerce to `&mut &'short T`. Write the
   two-function program that turns that into a use-after-free;
   identify the line that plants the dangling reference and the
   line that reads it; explain why the same shortening on a
   *shared* `& &'long T` is safe.
5. **Predict (MTE arithmetic).** 4-bit tags, 16-byte granules:
   (a) chance one wild access escapes; (b) chance an exploit
   needing 4 escaping accesses survives; (c) chance a dangling
   pointer (freed granule re-allocated, fresh random tag) walks
   through undetected; (d) the false-positive rate for correct
   code — and why a sound type system gives the same answer.
6. **Diagnose.** A C++ service's RSS climbs 1 MB/hour; ASan is
   silent; valgrind shows "still reachable," growing. The team
   says "Rust would fix this — it's memory-safe." Bug class,
   C++ fix, and would safe Rust reject the equivalent?
7. **Diagnose.** Release-build Rust: a `u32` counter `x += 1`
   loops forever under `x < MAX`; a debug build had panicked
   with `attempt to add with overflow`. What did the debug
   panic prove, what did release do instead, the two type-level
   fixes?
8. **Cross-chapter.** Module 01 ch03's MMU and MTE both check
   types in hardware. Compare them — type, width, granularity,
   clauses covered — then name the one check CFI/PAC adds that
   neither gives you.

<details>
<summary>Answers</summary>

1. (a) own-access: implied by (b)+(c)+(d) together; (b) bounds:
   runtime check on every array index
   (`ArrayIndexOutOfBoundsException`); (c) no punning: the
   per-object klass tag checked at every cast
   (`ClassCastException`); (d) no post-lifetime: the GC never
   frees reachable objects. The MMU enforces (a) coarsely: one
   3-bit R/W/X "type" per 4,096-byte page, checked every access
   — why wild C pointers *end* at SIGSEGV, not in the kernel.
2. Unrestricted: any number of uses. Linear: exactly one, on
   every path. Affine: at most one. Panics break linearity: an
   unwind between creation and use makes "exactly once"
   unprovable; "at most once" tolerates early exit. The
   compiler inserts the *drop* — the destructor call — on every
   path, including the unwinding one.
3. The borrow starts at `&mut v[0]` and ends at the last *use*
   of `r` — `*r += 1` — so `v.push(2)` (which needs `&mut
   self`) runs after the borrow died: accepted. The lexical
   checker used *scopes*: `r` lived to its block's end, so
   `push`'s borrow conflicted — E0499/E0502. NLL swapped
   syntax-based regions for liveness-based region inference.
4. `fn plant(r: &mut &'long T, s: &'short T) { *r = s; }` —
   the coercion makes the store well-typed. Caller: `let slot:
   &'long T = &long_val; plant(&mut slot, &short_val);` The
   *plant* line writes `&short_val` into a slot typed `'long`;
   once `short_val` dies, any reader of `slot` — legal, since
   the type promises `'long` — reads a dangling reference: the
   read line is `println!("{}", slot)`, no cast anywhere. On a
   shared `& &'long T` shortening is safe because *nobody can
   write through it*: forgetting a view's precision cannot
   corrupt the slot for better-informed viewers. Mutability
   forces invariance; read-only permits covariance — Java's
   covariant arrays + `ArrayStoreException`, at compile time.
5. (a) 1/16 = **6.25%** (15/16 = 93.75% caught). (b) (1/16)^4
   = 1/65,536 ≈ **0.002%** — in sync mode the other ~99.998%
   of the time a check fires and the process dies loudly.
   (c) The fresh tag equals the stale pointer's with
   probability **1/16** — that use-after-free is undetected.
   (d) **Zero**: the tag is a certificate the allocator
   maintains — a fault on correct code means a tag-manager bug,
   never noise; sound type systems reject only ill-typed
   programs, so their false-positive rate is also zero.
6. A `shared_ptr` reference cycle: the refcount never reaches
   zero, destructors never run — memory-*safe* (no dangling)
   but leaky, which is why ASan is silent and valgrind says
   "still reachable." Fix: `weak_ptr` back-pointers. Safe Rust
   would *not* reject the equivalent — `Rc<Node>` cycles leak
   identically: both prove memory safety, not reachability
   (chapter 04).
7. The debug panic was the *overflow check* — a runtime proof
   that exists only in debug builds. Release compiled the add
   as wrapping, so `x` wrapped past `u32::MAX` and `x < MAX`
   stayed true forever: an infinite loop, not memory
   unsafety. Fixes: `checked_add` (returns `Option`, forcing
   the match) or `wrapping_add` (intent in the expression's
   type).
8. MMU: 3 bits (R/W/X) per 4 KiB page — clause (a), coarsely
   (plus NX's control-flow value). MTE: a 4-bit allocation tag
   per 16-byte granule — (a) and (d) (wild and dangling
   pointers carry mismatched tags), *almost* (b): OOB within
   the same granule escapes. Neither constrains *where control
   flows*: CFI/PAC type-checks indirect branch targets — the
   legal-target set, signed per process (PAC).

</details>
