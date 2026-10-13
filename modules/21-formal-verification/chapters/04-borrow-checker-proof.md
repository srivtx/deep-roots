# Chapter 04 — The borrow checker is separation logic

> **The 30-second version.** The tool millions fight daily is a
> theorem prover for a fragment of Reynolds' separation logic — and
> the fight *is* the fragment's incompleteness. The logic: assertions
> that own memory (`x ↦ e`), a conjunction `P * Q` that demands
> disjoint heaps, the frame rule — a proof touches only what it
> mentions. Rust shrank it to something decidable: affine types,
> region constraints, an aliasing discipline, Send/Sync as proof
> rules. "Borrowed value does not live long enough" is a failed
> fixed-point over a region constraint graph. Under the fragment
> sits `unsafe`, where Miri's operational model and RustBelt's Iris
> proofs define what the checker cannot see.

## Reynolds' gift: assertions that own memory

Classical Hoare logic (chapter 01) breaks on pointers. Its assignment
axiom is sound for *variables* because variables cannot alias; add
one heap, and `p`, `q` become distinct names for the *same* cell
while no formula says which cells it describes — so every assertion
is quietly about the whole heap: a swap spec must restate the entire
heap in its postcondition. The "the whole heap is my business"
problem. The fix took decades: Burstall
(1972) scoped formulas to one structure's cells; Reynolds — worried
about aliasing since the late 1960s, already author of Forsythe's
conjunctive types with two conjunctions — published the modern form
in 2002 ("Separation Logic: A Logic for Shared Mutable Data
Structures," LICS), with Ishtiaq–O'Hearn's 2001 bunched-implications
and O'Hearn–Reynolds–Yang's 2001 local-reasoning papers as the birth
sequence. Two primitives do the work:

- **Points-to, `x ↦ e`:** "the location `x` holds `e` — *and I own
  it*." The second clause is the gift: the assertion is a *resource
  held*, not a true boolean. To assert `x ↦ e` is to be the only
  party who may read or write that cell — truth and ownership travel
together.
- **Separating conjunction, `P * Q`:** `P` holds on heap fragment
  `h1`, `Q` on the disjoint `h2`, real heap = `h1 ⊎ h2`. The name is
  literal: `P * Q` is *false* if the footprints share one cell;
  `P ∧ Q` can hold of one overlapping blob. Disjointness becomes a
  syntactic fact.

```text
        {P} C {Q}  ⊢  {P * R} C {Q * R}     here P = x ↦ v * y ↦ w

  heap before C                            heap after C
  +----------------+---------------+       +----------------+---------------+
  | R (the frame)  | P (footprint) |       | R (the frame)  | Q (footprint) |
  | z ↦ 0 * u ↦ 1  | x ↦ v * y ↦ w |  --C->| z ↦ 0 * u ↦ 1  | x ↦ w * y ↦ v |
  +----------------+---------------+       +----------------+---------------+
   disjointness is *'s job: C's proof         R rides along untouched,
   mentions only x, y — R is untouchable      so callers frame their own state
```

The **frame rule**: if `C` is correct given only `P`, it is correct
given `P` plus *any disjoint heap R* — and `R` comes back
byte-for-byte: "the code you didn't mention doesn't change." That is
*local reasoning* — small footprints, scale by framing. The worked
derivation: a swap, with the rule schemas first:

```text
READ   {l ↦ v}  t := [l]  {l ↦ v ∧ t = v}          footprint: one cell
WRITE  {l ↦ _}  [l] := e  {l ↦ e}                  e pure, l not free in e
SEQ    {P} C1 {S}   {S} C2 {Q}  ⊢  {P} C1; C2 {Q}
FRAME  {P} C {Q}  ⊢  {P * R} C {Q * R}             C assigns no var free in R
```

```text
swap(x, y):   t := [x];   [x] := [y];   [y] := t

(1)  {x ↦ v * y ↦ w} t := [x] {x ↦ v * y ↦ w ∧ t = v}     READ on x, FRAME y
(2)  {x ↦ v * y ↦ w ∧ t = v} [x] := [y] {x ↦ w * y ↦ w}   WRITE on x, FRAME y
(3)  {x ↦ w * y ↦ w} [y] := t {x ↦ w * y ↦ v}             WRITE on y, FRAME x
(4)  SEQ (1)(2)(3), then FRAME with arbitrary R:
     {x ↦ v * y ↦ w * R}  swap  {x ↦ w * y ↦ v * R}
```

The proof is four lines *because* of the frame rule. Lines (1)–(3)
mention two cells; line (4) transports that local triple to every
disjoint heap at once — any caller, any `R`, one step. The classical
version writes `h' = h[x↦w][y↦v]` plus "and all else unchanged,"
re-proved per caller and growing with the heap. The modularity
theorem separation logic bought (O'Hearn–Pym–Reynolds, 2001–2002):
proof effort proportional to the code you touch, not the state that
exists.

## O'Hearn's concurrent act: the invariant at the lock

O'Hearn, with Brookes' semantics, took the logic concurrent in 2004;
the journal version — "Resources, Concurrency, and Local Reasoning"
(TCS 375, 2007) — states the critical-section rule:

```text
          {P * R} C {Q * R}
  ---------------------------------  with m do C    R = m's resource invariant
      {P}  with m do C  {Q}                             C's footprint ⊆ P ∪ R
```

Acquiring `m` hands your local `P` the invariant `R`: the protected
cells are yours to break and rebuild. Releasing `m` demands `R`
restored — *borrowed at lock, re-proved at unlock* — the obligation
at one boundary instead of across every interleaving; no interference
is possible, since another process touches `R`'s cells only while
holding `m`.

What it killed was Owicki–Gries (1976): re-verify every assertion of
every process against every atomic statement of every other. Counts
from the methods' own definitions (3 assertions, 4 statements per
philosopher):

| dining philosophers | Owicki–Gries | CSL |
|---|---|---|
| 5 philosophers | 5·3·(4·4) = 240 interference checks | 5 local proofs (~4 triples each) + 1 invariant |
| 100 philosophers | 100·3·(99·4) = 118,800 checks | ~4 triples + 1 invariant (a `*` fold over forks) |
| add a process | re-verify every existing assertion | write one more local proof |
| auxiliary variables | required, then erased and re-verified | none |

For the philosophers, Owicki–Gries reasons through interleavings with
auxiliary variables; CSL states "each fork is on the table or held by
exactly one" and proves each philosopher as if sequential — the
invariant shared once, not multiplied per interleaving. The
proof-size column is the whole argument.

## The decidable fragment: why the checker cannot be the logic

The deduction that set Rust's design: borrow checking terminates in
seconds on 100,000-line crates; full separation logic cannot.
Entailment with general invariants is undecidable — at best
semidecidable: "this loop maintains sortedness" embeds arbitrary
program properties, so proof search subsumes the halting problem —
module 19 ch01's shadow. A compile-time checker must be total on all
inputs, so it drops user formulas, induction, and invariants, keeping
fixed-shape constraints: module 19 ch01's corner reinstalled — *sound
by construction, incomplete forever*. Every "rejected my legal
program" is the incompleteness tax on schedule.

The fragment has one discipline per half of the logic:

1. **Ownership = affine types.** Module 19 ch03's foundation. A
   non-Copy value is *consumed* by the move; use-after-move fails
   syntactically. This is the ownership half of `x ↦ e` as a type
   rule: holding a `T` by value *is* holding the cell's permission.
2. **Lifetimes = region constraints.** Tofte–Talpin (POPL 1994;
   journal 1997): every value lives in a *region* with a
   compile-time lifetime, regions nest like stack frames, inference
   picks the shortest region covering each value's uses. The ML Kit
   (Tofte–Birkedal, late 1990s) built real ML this way — and failed:
   a value escaping its scope drags its whole region into a
   longer-lived one, regions grow until one is the whole heap, and
   memory quietly never comes back. Rust's lesson: regions are not
   *allocation pools* but *type-level constraints on borrows* —
   lifetimes name regions, nothing is allocated "into" one.
3. **Aliasing discipline = the borrow rules.** One `&mut T` XOR many
   `&T` per location, mutation through shared paths forbidden — a
   conservative approximation of "no data race on `*mut`," stronger
   than that theorem needs (it forbids synchronized-but-aliased
   patterns too, ruling by location-and-time, not happens-before),
   but decidable: permission arithmetic on syntax, not proof of a
   formula.

Send and Sync complete the fragment as **propositional proof rules**:
`Send: T` = "the value may cross threads," obligation "no shared
mutable state reachable inside"; `Sync: T` ⟺ `&T: Send`.
Auto-derivation is type-level proof search — a struct is `Send`
exactly when every field is, a conjunction rule solved coinductively
so cyclic types converge. `unsafe impl Send for MyType {}` is an
axiom with no derivation: the **admit hole**, obligation moved to
human review (or RustBelt's machine-checked proof — below). Negative
impls (`impl !Send`) *publish* obligations downstream.

## NLL: the checker as a fixed-point proof search

Until 2018 the checker tied borrows to lexical scopes — creation to
closing brace, regardless of use. The 2016 and 2017 official Rust
surveys both ranked the borrow checker the hardest part of the
language; RFC 2094 (2017, non-lexical lifetimes) collected the
rejected-legal programs, its poster child four lines: borrow a vector
element, print it, push — rejected because the borrow's *scope* ran
to the block's end. The RFC's problem statement, paraphrased: the old
checker ties a borrow's region to its syntactic scope, not to the
span where the value is used. NLL (Rust 1.31, December 2018, 2018
edition; all editions by 1.36, mid-2019) reformulated borrow checking
as proof search over MIR — borrowck became a phase of type checking:

- every borrow and reference gets a **region variable**;
- each *use* makes its region **live** at that point; the live range
  is the least **fixed point** of liveness over the control-flow
  graph — the same mathematics chapter 02's model checker runs, on
  regions instead of states;
- subtyping and loans emit **outlives** edges `'a: 'b` (`&'a T` used
  where `&'b T` is expected), loan edges (a loan's region must cover
  its live range), drop edges;
- the solve computes the least region assignment satisfying every
  edge. A loan whose region must include a point with a conflicting
  move has **no solution** — precisely the error:

```text
     the constraint graph the E0505 error reports (lab 1 below)

 L2  let v = vec![..];   region 'v (the data)                begins
 L3  let r = &v;         region 'r (the loan);  edge 'v: 'r  begins
 L4  drop(v);            move: legal only if no loan live here      KILL
 L5  println!("{r}");    last use of 'r: 'r is live HERE

 liveness fixpoint: live('r) = {L3..L5}   -> 'r must include L4
 outlives edge:     'v must cover 'r       -> 'v must include L4
 move semantics:    'v ends at L4          -> 'v includes AND ends at L4
                                             -> NO ASSIGNMENT -> E0505
```

The error's three spans — "borrow occurs here," "move occurs here,"
"borrow later used here" — are the three facts of the failed proof
search. Delete L5 and the fixed point shrinks: the loan dies before
the move, the program compiles. The error was never *in* a line; it
lives in an edge between spans.

The rejections were incompleteness with a human face: the 2017
roadmap (RFC 2035) named borrow-checker ergonomics the top
learnability problem, and the migration's tracking issue
(rust-lang/rust #47489, 2018) graded the fix across real crates.
Polonius — the 2017–2019 exploratory rewrite, named for Hamlet's
courtier — re-derived NLL as an explicit Datalog-style least fixed
point; it never fully landed in rustc but became the reference for
what NLL *is*.

## Miri: the operational model that defines what unsafe must uphold

The static fragment proves safety for programs inside it. `unsafe`
exits it (raw pointers, transmute, unions, FFI), and the question
that must have an answer is: *what, precisely, must unsafe code
uphold?* Rust's answer is two halves — an operational model of
references and provenance, and a checker: Miri.

Stacked borrows (Jung et al., 2019) is the first complete model.
Every pointer carries a **tag** (a provenance identity); every
location has a **stack** of tags. **Retagging** — creating a `&mut`,
and again at function boundaries — pushes a new Unique tag; using an
older pointer pops the tags above it; *using a popped tag is UB*. A
`&mut x` reborrow thus invalidates a raw pointer cast earlier from
`&mut x` — the aliasing discipline made dynamic. **Protectors**: a
reference passed into a call has its tag protected for the call's
duration; invalidating a protected tag is UB even if the borrower
never uses it again — "the callee's reference stays valid" becomes
enforceable.

Tree borrows (the 2023 refinement) replaces stacks with a tree rooted
at the allocation: reborrows are children; a pointer may write iff no
protected child forbids it — the more-liberal "unique enough,"
accepting unsafe patterns stacked borrows rejects. The 2023–2024
migration debate: which model becomes normative is a compatibility
decision for every unsafe crate; as of Miri 2024 stacked is the
default, tree the `-Zmiri-tree-borrows` opt-in.

`cargo +nightly miri run` executes a program under the model,
reporting violations with the tag and allocation named: dangling
dereferences, popped-tag writes, misalignment, invalid bit patterns,
data races. The two-level proof structure, honestly:

- **Level 1, static and conservative:** the borrow checker proves no
  UB for the fragment — all safe Rust.
- **Level 2, dynamic and precise:** Miri checks the *executed paths*
  of unsafe code against the model.
- Between them: for tested paths, no UB the model can express. The
  testing gap is real — Miri runs neither all paths nor all
  schedules, and no FFI; "Miri-clean" is evidence, not a theorem.
  The author's discipline is module 19 ch03's trust
  structure: the model is the contract; review + Miri + the
  documentation are the proof method.

### Ghost state: how std carries its proofs in its types

Separation logic has ghost state — logical variables with no runtime
content, used only in proofs. Rust's type-level version is
`PhantomData<T>`: a zero-sized field making the compiler reason *as
if* `T` were present — an iterator holding raw pointers is covariant
in `'a` only via its `PhantomData<&'a T>`; dropck and Send/Sync
derivation read the same field. `Unique<T>`, Vec's pointer
wrapper, is `NonNull<T>` plus that marker: "this pointer owns a T,"
restoring the variance and auto-trait truth raw pointers erase, with
non-nullness recorded (that is the niche).

Vec's internal obligations, enumerated — every safe `Vec` method
proves these hold on exit if they held on entry:

1. `ptr` points to `cap` contiguous bytes from allocator `A`,
   aligned for `T`;
2. elements `[0, len)` initialized and valid; `[len, cap)` may be
   uninitialized;
3. `len ≤ cap` in every reachable state;
4. the allocation is uniquely owned — no other live pointer may
   write it;
5. drop runs on exactly `[0, len)`, in order, once; the buffer is
   freed once, by the same `A`, at the same `cap`;
6. `from_raw_parts` reassembles exactly this tuple (or leaks it) —
   never free twice.

That list is Vec's separation-logic invariant in Rust's clothing; the
safe membrane (push, pop, index, iter) is a set of theorems
"obligations in ⇒ obligations out," and the `unsafe` blocks inside
are where the machine's proof yields to a human's. std is an unsafe
kernel with a safe membrane — module 19 ch03's shape in proof-theory
vocabulary: the library exports proof rules, its internals are their
derivations, the membrane checks rather than trusts.

## The lineage ships — and async stress-tests it

```text
 year    system          what it contributed to the shipped logic
 1972    Burstall        formulas scoped to one structure's cells
 1994-97 Tofte-Talpin    region inference: lifetimes as compile-time values
 2001-02 BI / SL         P * Q, x ↦ e, the frame rule (Reynolds, LICS 2002)
 2001-06 Cyclone         safe C: regions + owned pointers, in production
 2004-07 CSL (O'Hearn)   ownership + locks: the resource-invariant rule
 mid-10s Mezzo           affine ML with first-class permissions
 2015    Rust 1.0        the synthesis ships — Reynolds -> Rust: 28 years
 2018-21 RustBelt        λRust proven sound in Iris/Coq — the meta-proof
 2023-   Hylo / Oxide    the live frontier: value semantics; a formal core
```

(1987, Reynolds' Forsythe conjunctive types — the seed of `*` — to
Rust 1.0: 28 years.)

Cyclone (2001–2006, Grossman et al.) was the industrial experiment: a
safe C dialect with region annotations, fat pointers, and owned
pointers — the first ownership discipline compiling real systems
code, and the pipe through which Tofte–Talpin's "regions" reached
Rust. Mezzo (Pottier et al., mid-2010s) gave ML affine types and
explicit permissions — the proof rules on display. Rust 1.0
(May 15, 2015) is the synthesis: the papers finally shipped, closing
module 19 ch05's timeline.

RustBelt (Jung–Jourdan–Dreyer, POPL 2018, extended through 2021) is
the meta-proof this chapter's identification needs: λRust, a core
calculus of Rust's ownership, formalized in Iris, a Coq-mechanized
separation logic. The theorem: safe client code, linked against
libraries whose `unsafe impl`s discharge their Iris-level
obligations, executes without UB. The borrow checker's own rules —
the fragment above — are *proven* sound against the full logic, the
admit holes becoming theorem obligations discharged in Coq instead
of trust: "the decidable fragment is a sound approximation of
separation logic" is a theorem, not folklore. The frontier: Hylo
(formerly Val) drops borrows for mutable value semantics — ownership
without the checker, by construction; Oxide distills Rust's type
system to a formal core for metatheory the full language cannot
carry.

**Async is the fragment's hardest customer.** `async fn` compiles to
a state machine: each `.await` is a suspend point, and every local
live across an await becomes a *field* of the generated `Future`.
The borrow checker then checks that struct — and a local buffer
borrowed by a slice (`buf` and `&mut buf[..]` as two fields) makes
it **self-referential**: a reference field pointing into a sibling
field. The fragment cannot bless that: moving the state machine
dangles the interior pointer, and the region discipline assumes
Tofte–Talpin stack order — a frame's values die with its frame;
suspension breaks that assumption *mid-function*. The resolution is
a ghost proposition in the type: `Pin<P>` — "this value will not
move again before drop," no runtime content, pure obligation.
`!Unpin` marks the types that depend on it; `Box::pin` is the safe
derivation (the heap move before pinning is the last);
`Pin::new_unchecked` is the admit hole. Across-await borrows stress
the fragment: each is a live range crossing a suspension point into
a compiler-generated heap struct whose region variables the
programmer never names.

## What insiders know

- **Validity vs safety — the RustBelt distinction.** The type system
  proves *safety*: no UB in safe code, given the library's invariants
  hold. *Validity* is the bit-pattern obligation: a `bool` is `0x00`
  or `0x01` — the same eight bits are a valid `u8` and an invalid
  `bool` (validity is type-relative; two's-complement bits are always
  valid integers, never "valid enough" for a typed read). References
  must be non-null and aligned; `char` ≤ `0x10FFFF`; uninitialized
  memory must not be read as typed data. Unsafe code can write `0x02`
  into a bool — the UB begins at the read, and `MaybeUninit` is the
  honest type for the hole.
- **Every niche is a validity dependency.** `Option<&T>` is 8 bytes
  because `None` *is* the null bit pattern — and null is not a valid
  reference, so the discriminant is free. Write a null `&T` through
  unsafe and the "None" collides with your data: the optimization is
  only sound because validity is upheld. Same bet for `Box`,
  `NonZero`, and fn pointers.
- **Drop order and `#[may_dangle]`.** Struct fields drop in
  declaration order, locals in reverse; dropck demands `T: 'a`
  strictly because drop *reads* references. `#[may_dangle]` (RFC
  1238) relaxes it for fields the drop will not touch — "I promise
  not to access the dead" — and std reaches for it in the iterator
  family (IntoIter/Drain-style types) where element lifetimes have
  technically ended. The counterpart, a `PhantomData<&'a T>` field *forcing* the
  obligation when you will touch the data, is the "PhantomDrop"
  trick.
- **Stacked vs tree, the live spec decision.** Stacked borrows is
  strict: an older pointer's use invalidates younger reborrows,
  permanently. Tree borrows only asks "unique enough": writes are
  fine unless a protected child disagrees. Patterns legal under tree
  and UB under stacked exist — long-lived raw pointers re-derived
  from a `&mut` across calls. As of Miri 2024 stacked is the default;
  the normative choice is open, and unsafe-crate authors must satisfy
  both or pick a side.
- **The specialization history.** `feature(specialization)` (RFC
  1210, nightly since ~2016) is known unsound: overlapping impls plus
  associated-type projection let *safe* code synthesize wrong types —
  the specialization-as-transmute family. The loud attempts ran
  2015–2018; the surviving escape is `min_specialization`, the
  restricted subset std itself uses, believed sound, still unstable
  as of 2024.

## See it yourself

**Lab 1 — the compile-time proof experiment (mandatory, free).**
Five lines, one rejection, one constraint diagram drawn by hand:

```rust
fn main() {
    let v = vec![1, 2, 3];
    let r = &v;      // the loan begins
    drop(v);         // the move the fixed point cannot allow
    println!("{r}")  // the later use that keeps the loan alive
}
```

```console
$ cargo new prooflab && cd prooflab
$ cargo build 2>&1 | head -12
error[E0505]: cannot move out of `v` because it is borrowed
 --> src/main.rs:4:10
  |
3 |     let r = &v;
  |              - borrow of `v` occurs here
4 |     drop(v);
  |          ^ move out of `v` occurs here
5 |     println!("{r}")
  |               ---- borrow later used here
```

(rustc 1.75, trimmed; spans follow your line numbers.) The error
names three points — the three facts of the failed proof search. Draw
the graph: nodes `'v`, `'r`; edges `outlives('v,'r)`, liveness
forcing `'r` to span L3–L5, the move ending `'v` at L4 — no
assignment satisfies all three. Then delete L5, rebuild — it
compiles: the fixed point shrank, the loan died before the move. The
error lives in an edge between spans, not in a line; reading it as a
failed fixed-point is the skill, and it is free.

**Lab 2 — cargo miri on the checker's blind spot** (paper version if
nightly is unavailable):

```console
$ rustup toolchain install nightly
$ rustup +nightly component add miri
$ cargo +nightly miri run
```

```rust
// miri_lab.rs — the lifetime laundered through a raw pointer.
fn ghost() -> &'static mut u64 {
    let mut local = 42u64;
    unsafe { std::mem::transmute(&mut local) }  // the admit hole
}

fn main() {
    let r = ghost();
    println!("{}", *r);  // Miri: the dangling dereference
}
```

```console
error: Undefined Behavior: dereferencing dangling pointer (8 bytes)
note: details of the allocation, and this stack frame, were already freed
```

(Miri 2024, trimmed.) `-Zmiri-tree-borrows` agrees: this UB is
allocation-level, below both tag models. Paper version: after `ghost`
returns, the frame and its tag stack are gone; any tag derived from
`&mut local` grants nothing, so the deref is UB by the model's first
rule. Why did rustc accept? `transmute` is an admit hole — it
asserted `'static` with no derivation, and the checker had nothing
to check.

**Lab 3 — the frame rule on paper.** Instantiate line (4) of the
swap proof with `R = z ↦ 0` and verify each line against the four
schemas: for (1), the rule that fires (READ on `x`) and what was
framed (`y ↦ w`); likewise (2), (3); for (4), the side condition —
swap assigns only `t`, `z` appears in no command, so
modified-variables ∩ free-variables(R) = ∅ — and the `*`-check:
three distinct cells. Then break it: set `R = x ↦ 0` — `x ↦ v * x ↦
0` is false by `*`'s disjointness (one cell, two owners), the
conclusion unstatable, the rule unable to fire. Framing is licensed
by ownership disjointness, nothing else.

## How to use this knowledge

- **Error triage as proof theory.** Every borrow error is three spans
  plus an unsatisfiable edge; the three legal moves — shorten the
  live range (move the use), shrink the footprint (borrow the field,
  not the struct), change the proposition (own or copy the data) —
  map onto the three families of fix. ("Why is `Option<&T>` 8 bytes?"
  and "why does async need `Pin`?" are the interview questions.)
- **Unsafe review.** The model is the checklist: provenance, tags,
  protectors, alignment, validity, deallocate-exactly-once. "What
  would Miri say?" is the review question; `cargo +nightly miri
  test` in CI is the cheapest oracle an unsafe crate can buy.
- **API and concurrency design.** `split_at`, `chunks`, `iter_mut`
  exist because the fragment proves per-field and per-disjoint-slice;
  design signatures that are *provable*, not merely callable — every
  public signature is a proof rule your users derive against.
  Send/Sync derivation is proof search, negative impls publish
  obligations, `unsafe impl` admits propositions — decide which you
  are doing, on purpose.

## Connections

- **Module 19 chapter 03** — the affine foundation and unsafe-kernel
  pattern re-derived as Reynolds' ownership logic; its `Pin` returns
  as the ghost proposition.
- **Module 01 chapter 10** — the Rust practitioner's view
  re-theorized: "the compiler proves it," named precisely — a
  decidable fragment of separation logic.
- **Module 09 chapter 03** — the race-freedom theorem: `&mut`
  exclusivity + `&T` sharing + Send/Sync; lock-free code builds on
  it.
- **Module 21 chapter 01** — the Hoare triple and LCF kernel: same
  architecture — small sound kernel, large incomplete search in
  front.
- **Module 21 chapter 02** — the NLL solve is the model checker's
  fixed point, on regions instead of states.
- **Module 21 chapter 03** — seL4's invariant maintenance is Vec's
  obligation list at kernel scale: same proof shape, different TCB.
- **Module 21 chapter 05** — the tower continues downward: the
  compiler and the crypto under it, carrying proofs.

## Quiz

1. **Concept.** The points-to assertion `x ↦ e` states two things.
   Name both, identify which one the frame rule consumes, and say
   what goes wrong if `↦` means only "x holds e."
2. **Concept.** Borrow checking terminates in seconds on 100,000-line
   crates. Why does that *forbid* full separation logic, which
   property of invariant checking is the obstacle, and what price
   does the fragment pay?
3. **Predict the number.** Eight dining philosophers, 3 assertions
   and 5 statements each: the Owicki–Gries interference obligation
   count and CSL's proof inventory, at 8 and at 100 philosophers.
4. **Derive.** From `{x ↦ v * y ↦ w} swap {x ↦ w * y ↦ v}`, apply
   the frame rule with `R = z ↦ 0`: write the conclusion, verify the
   side condition, and explain why `R = x ↦ 0` cannot be framed.
5. **Predict.** `let mut v = vec![1,2,3]; let r = &v[0];
   println!("{r}"); v.push(4);` — the 2015 lexical checker's verdict
   and the NLL verdict, each with its region reason.
6. **Diagnose.** `let mut x = 0u8; let raw = &mut x as *mut u8; let
   r = &mut x; *r = 1; unsafe { *raw = 2; } *r = 3;` — Miri reports
   UB at the last statement. Which tag was popped, by which
   operation, and which single change moves the UB earlier?
7. **Diagnose.** `struct Shared<T>(Rc<T>)` with `unsafe impl<T> Send
   for Shared<T> {}` passes single-threaded tests; two threads clone
   and drop it. What breaks at the memory level, and what is the
   proof-theory name for what the `unsafe impl` did?
8. **Cross-chapter.** Why do across-await borrows stress the fragment
   — name the assumption suspension breaks — and what proof-theory
   role does `Pin` play? Tie to module 19 ch03 and module 01 ch04.

<details>
<summary>Answers</summary>

1. Truth — `x` holds `e`; ownership — the asserter alone may read or
   write the cell. The frame rule consumes ownership: `C`'s proof,
   holding `P`, has rights to `P`'s cells only, so `R` is untouchable
   by *permission*, not mere non-mention. If `↦` were truth-only, `C`
   could write `R`'s cells and `{P * R} C {Q * R}` would be unsound —
   `R` could come back changed; hence classical Hoare logic's
   whole-heap restatements.
2. Entailment with user invariants is undecidable (semidecidable at
   best): "this loop maintains sortedness" embeds arbitrary program
   properties, so proof search subsumes the halting problem — module
   19 ch01's shadow. A compile-time checker must terminate on all
   inputs, so it keeps only fixed-shape constraints — types, region
   variables, outlives edges; no user formulas, no induction. The
   price is incompleteness: legal programs rejected, the tax paid in
   ergonomics, never in soundness.
3. Owicki–Gries: every assertion of every process against every
   atomic statement of every other — 8 × 3 × 7 × 5 = **840**
   obligations, each a semantic step, plus auxiliary variables. CSL:
   8 local proofs + 1 invariant (a `*` fold over the forks). At 100:
   100 × 3 × 99 × 5 = **148,500**, quadratic in the population; CSL
   unchanged in shape — one invariant, one proof pattern reused.
   Adding a process re-opens every existing assertion in Owicki–Gries;
   in CSL you write one more local proof.
4. Conclusion: `{x ↦ v * y ↦ w * z ↦ 0} swap {x ↦ w * y ↦ v * z ↦ 0}`.
   Side condition: swap assigns only the local `t`; `z` appears in no
   command, so modified-variables ∩ free-variables(R) = ∅, and `*`
   makes (x, y) and (z) disjoint cells. `R = x ↦ 0` fails earlier:
   `x ↦ v * x ↦ 0` claims two owners of one cell — false by `*` — so
   the precondition is unsatisfiable and the rule cannot fire.
   Framing is licensed by ownership disjointness, nothing else.
5. 2015 lexical checker: **error at `v.push(4)`** — the borrow from
   `&v[0]` lives to the block's end, so push's mutable borrow
   conflicts with a still-in-scope shared borrow (E0502). NLL:
   **compiles** — `r`'s last use is the `println`, the liveness fixed
   point ends the loan there, no region covers both the loan and the
   later mutable borrow. RFC 2094's poster child.
6. The reborrow `let r = &mut x` retagged, pushing Unique(2) above
   raw's Unique(1). `unsafe { *raw = 2 }` uses tag (1) — legal, but
   it *pops* (2); `*r = 3` then uses a popped tag ("no item granting
   write access for tag Unique(2) found in borrow stack"). The
   change: pass `r` into a function and do the raw write inside the
   call — `r`'s tag is now *protected* for the call's duration, so
   the invalidation itself is the UB, one line earlier. Protectors
   move the boundary from use-after-invalidation to invalidation.
7. The `Rc` refcount is a plain non-atomic cell: two threads cloning
   lose an increment, the count hits zero while a live `Rc` exists,
   the buffer is freed, and the next access is a use-after-free
   (possibly a double free). Proof-theory: auto-derivation *refused*
   `Send` (the inner shared-mutable state is what the rule forbids);
   the `unsafe impl` **admitted the proposition with no derivation —
   an unsound axiom**. Downstream code is sound relative to a false
   lemma; the checker is blameless. Miri flags the race on the
   counter first.
8. The async transform turns locals live across an await into fields
   of a generated state machine; a borrow of one field from another
   (`buf`, `&mut buf[..]`) makes it self-referential. The broken
   assumption is Tofte–Talpin stack order — a frame's values die with
   its frame, but a suspended future is a frame outliving its
   function, with live ranges crossing the suspend point into a heap
   struct the programmer never names. `Pin<P>` is the ghost
   proposition "this value will not move again before drop" — no
   runtime content, carried purely in types: `!Unpin` marks
   dependence, `Box::pin` is the safe derivation,
   `Pin::new_unchecked` the admit hole. Module 19 ch03 gave `Pin` as
   the unsafe kernel's contract; module 01 ch04's frames explain why
   moving dangles the interior pointer.

</details>
