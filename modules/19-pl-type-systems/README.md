# Module 19 — Programming language theory & type systems: why the rules are theorems

> Every type system is a proof assistant you didn't know you were using.
> `&amp;mut` in Rust is not a syntax rule — it is the visible surface of a
> machine-checked theorem about aliasing and lifetimes. Python's "no
> types" is not the absence of a type system — it is one whose proofs
> run at runtime instead of compile time. GC pauses are the cost of a
> proof (liveness) too expensive to do eagerly. This module goes where
> module 01 reported: *why* these rules exist, what theorem each one is,
> and what they provably cannot do. The payoff is the strongest
> intuition in software: seeing every language design decision —
> nullability, generics, lifetimes, effects, GC — as a point chosen on
> the expressiveness-versus-guarantees frontier, and knowing exactly
> which side of that frontier you are standing on when you write the
> next line.

**5 chapters · one final exam · prerequisite: module 01 chapters 06
(object layout — tagged/unboxed/tagged is the type-system memory
story), 08-17 (the language survey — every rule this module derives
is one of those languages' rules), 23 (the unification table); module
10 (compilers) is where these rules get *enforced* — referenced, not
required.**

## How to study it

Chapter 01 builds the minimal machine (lambda calculus) and shows it is
already Turing-complete — with the halting problem arriving in the same
box; 02 adds types and derives the central theorem (type soundness =
progress + preservation), including why `null` is "the billion-dollar
mistake" *type-theoretically*; 03 is the module's heart: memory safety
as a theorem — Rust's borrow checker as an affine-type-system proof,
C's unchecked casts as a soundness hole, and the exact boundary where
each language of module 01 sits; 04 derives garbage collection from
liveness analysis — the GC of module 01 ch11/12/14/15/16 re-derived as
proof search; 05 states the limits (Gödel-flavored honesty: what types
cannot express — termination, spec correctness, and the impossibility
results that explain why bugs still exist in Rust programs). Labs run
in any browser or with python/gcc — the theory chapters'
"see-it-yourself" is real interpreters and counterexamples, ~30 lines
of Python each.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The lambda calculus: the minimal machine](chapters/01-lambda-calculus-machine.md) | Three reduction rules, Turing completeness, the halting problem arriving in the same box — why every language is sugar on this. |
| 02 | [Types as theorems, programs as proofs](chapters/02-types-as-proofs.md) | Soundness = progress + preservation; Curry-Howard; null, generics, and subtyping as points on the guarantee frontier. |
| 03 | [Memory safety as a theorem](chapters/03-memory-safety-theorem.md) | The borrow checker as an affine type system; every module-01 language placed on the safety proof spectrum; where "unsafe" holes live. |
| 04 | [GC as proof search](chapters/04-gc-theory.md) | Liveness as the theorem, tracing as proof search, write barriers as monotonicity, pauses as proof cost — all collectors re-derived. |
| 05 | [What types cannot do](chapters/05-limits-of-types.md) | Termination, spec fidelity, integer overflow, and the soundness/expressiveness trades — why bugs survive in safe languages. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the minimal machine.** Start from the question module 01
never asked: *what is the smallest language that computes?* Derive the
lambda calculus from scratch: syntax in 3 forms (variable, λx.t, t1 t2 —
write the grammar in one line), the three conversions (α-renaming,
β-reduction ((λx.t) u → t[x:=u] with substitution's capture-avoidance
shown on one example where naive substitution breaks), η), and normal
order vs applicative order with the classic divergent term Ω = (λx.x x)
(λx.x x) evaluated both ways (one terminates finding the normal form of
(λx.λy.y) Ω, one doesn't). Church encoding so the machine proves
itself: booleans (λa.λb.a true, λa.λb.b false, the `if` combinator),
pairs, and naturals (succ = λn.λf.λx.f (n f x); add via composition;
multiply = λm.λn.λf.m (n f); one worked multiplication traced by hand —
the trace is the lab). Y-combinator for recursion drawn honestly
(Y = λf.(λx.f (x x))(λx.f (x x)), one unfolding step). Turing
equivalence sketched (Church-Turing in two sentences — we don't prove
it, we use it). **The halting problem in one page, correctly**: the
diagonalization proof written with actual code (a `halts(f, x)`
assumption, then `def paradox: if halts(paradox, 0): loop` — the
self-reference constructed in python syntax so it's not hand-waved);
why this single theorem caps everything downstream: no type system can
reject exactly the non-terminating programs (Rice's theorem stated in
plain words: every non-trivial semantic property is undecidable — with
the halting problem as the two-line reduction). Decidability as the
*actual* reason type inference has limits: Hindley-Milner's
principal-types property (inference is decidable for rank ≤ 2
polymorphism — why `let`-polymorphism exists and why higher-rank
inference wanders into undecidability; SML/OCaml/Haskell sit at the
decidable frontier, System F's type inference is undecidable — GHC's
annotations as the escape). Insiders: the word "currying" and the
`λ` in AWS Lambda, both direct descendents; normal order = lazy
evaluation (Haskell), applicative = strict (everything else) — the
one-sentence history of why; the "lambda calculus is untyped by
default" surprise; SKI combinators as the smaller-than-lambda system
(S = λf.λg.λx.f x (g x), K = λx.λy.x, I = K K — and Iota as the
one-combinator system). Lab: a 40-line normal-order lambda interpreter
in python (variables, application, λ, β with capture-avoiding
substitution via de Bruijn or fresh-name renames — give the reader
the code), then run: Church-numeral 2×3, `if true` on Ω (lazy wins,
strict diverges — the divergence shown live), Y unfold once. This
lab is mandatory and must actually run.

**Chapter 02 — types as theorems.** The reframe that organizes the
module: a type system is a syntactically-checkable proof that
evaluation stays inside a value set. Derive the two theorems that
define every typed language (Wright-Felleisen): **progress** (a
well-typed term is either a value or can take a step) and
**preservation** (stepping keeps the type) — prove both by induction
on ONE tiny language (STLC with booleans: give the 8 typing rules and
the 3 evaluation rules; prove preservation for the application case
in full, ~10 lines, induction hypothesis named) — then state the
soundness corollary (well-typed programs don't get stuck) and show a
stuck term that an untyped interpreter hits ((λx.x x) true in the
untyped calculus is fine; in STLC it doesn't typecheck — the same
term, two universes). Curry-Howard as the correspondence table (drawn,
4 columns: types = propositions, programs = proofs, β-reduction =
proof simplification, inhabited type = provable proposition — with
one worked example: `a → b → a` proved by λa.λb.a, checked by hand
against the implication rule). The consequence: `null` derived as a
soundness hole (Tony Hoare's 2009 "billion-dollar mistake" quote used
precisely: the type `Foo` promised *all* values of `Foo`, then null
snuck into every reference type — the type liar; the modern fixes:
`Option`/`Maybe` as an honest sum type (the type now says "value or
absent" and the compiler forces the match — the 2× tag cost is module
01 ch06's tagged-value story re-appearing), nullable reference types
in C# 8 (the migration as retrofitting honesty onto 20 years of code
— **mandatory link: module 01 ch13 C#**), Kotlin's platform types).
Generics: parametric polymorphism (System F: Λα.λx:α.x — the type
abstraction as a real binding; instantiation `bool [true]`), and
**parametricity as the free theorem** (Wadler's "Theorems for Free":
a function `∀α. [α] → [α]` cannot look at elements, cannot reorder
them, can only permute/cycle — derive the intuition on the reverse
function: the free theorem `map f (reverse xs) = reverse (map f xs)`
falls out of the type ALONE; the practical translation: a generic
`Map<K,V>` API can't secretly depend on K being a string — the type
is a contract stronger than documentation; Java's erasure as the
runtime cost table: 1 Object header vs C++'s monomorphized code
bloat vs Rust's monomorphization — **link module 01 ch13 and module
10 ch05's ABI sections**). Subtyping with the width/depth distinction
(record {x, y} ⊆ {x} — width subtyping is safe; depth is where
covariance/contravariance bites: function types are contravariant in
argument (derive with the 3-line substitution argument — this is why
Java arrays' covariance was a runtime exception factory
(`ArrayStoreException` — the real bug class, a soundness hole patched
with a runtime check = a type system running late), and C#'s
`IEnumerable<out T>` as the learned fix). Effects as the frontier
(Koka/Roc's effect rows; exceptions in Java as unchecked effects —
why `IOException` is checked and `NullPointerException` isn't is a
type-system-design opinion, not a law). Gradual typing honestly
(TypeScript's `any` as a soft hole; the soundness holes list:
type assertions, unsound covariant arrays, `any` bisection — the
practical rule: `any` is a proof pause button). Insiders: why `void`
in C is not the unit type; why TypeScript's structural typing accepts
extra fields at assignment but not at literal creation (the excess
property check — a usability hack on a structural system); the
`Never`/bottom type as the uninhabited proposition (the type of
`throw` and infinite loops — used by TS for exhaustiveness checking
via `never`); "well-typed programs can go wrong" — the Milner quote
is the opposite ("cannot go wrong" — but go wrong = stuck only;
integer overflow and logic bugs are OUT of scope, which is ch05's
topic). Lab: run `let f: (x: T) ... ` in TS with `strict` on/off and
catch the errors TS's soundness holes admit (write 4 tiny programs:
one per hole class); write the STLC typechecker in ~60 lines of
python (the 8 rules as code, one ill-typed term rejected with the
rule name — give the code, it must run).

**Chapter 03 — memory safety as a theorem (the module's heart).**
The chapter that pays the module's rent: define memory safety formally
then place every language of module 01 on the spectrum. Define it
(along Crary's lines): a program is memory-safe if all evaluations
that reach a value (a) access only memory they own (no wild reads/
writes), (b) respect object bounds (no OOB), (c) never confuse types
at a byte level (no punning through invalid casts), (d) never touch
memory after its lifetime (no UAF/dangling). Then: the **safety =
typed assembly** insight — memory safety IS type soundness at the
address level (the type of an address is "what may live there";
segmentation faults = the hardware's runtime type error; connect
module 01 ch03: page permissions R/W/X are a 1-bit type system the
MMU checks — derive this identification explicitly, it's the
chapter's bridge). **The borrow checker as a type system** (the
module's centerpiece, ~1/3 of the chapter): ownership = affine
types (types whose values may be used at most once — use = consume;
the linearity spectrum drawn: affine (≤1 use) vs linear (exactly 1)
vs unrestricted; why affine won in Rust: dropping is implicit);
borrowing as a proof-relation on lifetimes: `&'a mut T` = "I hold the
unique live reference into T for the interval 'a" — the rules
(borrow checking = proving a dataflow invariant: no aliasing while a
mutable borrow is live; NLL's reformulation as a constraint graph
solved by fixed-point iteration (2018's borrow checker rewrite —
lifetimes as region inference, not syntax — the 100-line sketch of
constraint generation + solving described, not coded); one worked
example: the classic `let r = &mut x; *r += 1; println x` accepted
under NLL, rejected under lexical — the timeline drawn as lifetime
intervals (draw the interval diagram: borrow's live range ends at
last USE, not scope end); `PhantomData` and variance invariance of
`&mut T` (why `&mut &'a T` can't shorten 'a — derive the counter-
example: shortening through a mutable alias would let you write a
longer-lived reference into it; **this one derivation is mandatory
and must be worked, not stated**). Unsafe Rust = the trusted kernel
pattern: soundness = every unsafe block upholds the invariants
(unsafe = "I prove it, compiler trusts"; `std::vec::Vec`'s invariant
as the case study: (ptr, cap, len) with the malloc'd buffer's
safety proof obligations listed). **Then the spectrum table** —
the chapter's second centerpiece: each language placed with its
proof mechanism and its hole: C (no proof, hardware exceptions
as runtime checks — unsound by design), C++ (RAII = scope-based
affine intuition but no aliasing proof; `shared_ptr` = runtime
proof via refcount, cycles unsound → `weak_ptr`), Rust (compile-time
proof, unsafe escape hatch), Go (compile-time for data races only
in the race detector — unsound for memory (slice aliasing OK,
GC saves the lifetime half), sound for goroutine data races only
when the detector runs), Java/C#/Python/JS (runtime proof: GC
proves liveness, runtime tags prove types — the pause/overhead IS
the proof cost, tag word = the certificate), Swift/ObjC (ARC =
runtime refcount proof with insertion points chosen by the
compiler — the pause-free-but-not-cycle-safe point), Haskell/
OCaml (GC + strong static types, purity as an additional theorem
about effects), COBOL (no pointers, no heap = safety by
impoverishment — "the type system is the PICTURE clause and the
record layout," module 01 ch17 / module 08's whole thesis).
For each: one 6-line program showing the language's proof working
or its hole failing (C's OOB compiles; Rust's borrow error shown
as the actual error text; Go's race caught by `-race`'s vector
clocks (connect module 09 ch02); Java's `ClassCastException` at
the erasure boundary; Swift retain/release inserts shown in SIL
or the -emit-assembly of a 5-line file — one concrete artifact
per language, this is mandatory). The final section: **can't-
prove-it-therefore-patch-it** — the mitigation ladder of module 02
re-derived as "runtime proof systems": canaries = proof of intact
return, ASLR = randomness as a proof substitute, CFI/PAC = the
proof oracle in hardware, MTE = per-16-byte tag = literally a
runtime type tag (the 4-bit type system — derive the false-
positive/false-negative budget from the tag width arithmetic).
Insiders: "fear-driven development" and `unsafe fn` semantics
(unsafe code must be *locally* provable); `Pin` as the theorem
"this object will not move" (self-referential structs — the async
state machine's reason); the stacked-borrows/tree-borrows
operational models as Miri's checker (the borrow checker checks
an approximation — Miri checks a precise model; tree-borrows as
the 2023 refinement story); `Box::leak` as "the proof that I
won't free it"; checked arithmetic as a type-level service
(Rust's `checked_add` returning Option — the overflow decision
pushed to the type level, contrast C's UB license, module 03
ch03's UB chapter). Lab: write the same 8-line doubly-linked-list
insert in C++ and Rust (the canonical unsafe-required case —
show why safe Rust rejects it (two `&mut` to neighbors) and the
`NonNull`/unsafe implementation with its invariant comments
(without running the unsafe Rust, the honest paper version is
acceptable, but the compile ERRORS must be real); run
`cargo miri` on a small program with an intentional UB (or
document); trigger each runtime-proof failure live: python
`ctypes` OOB (crash or silent corruption — document what you
actually see), Java arraystore, TS `any` bisection. Every lab
command real, output fragments shown.

**Chapter 04 — GC as proof search.** The reframe: a GC is a theorem
prover that periodically proves "this object is unreachable" and
frees the refuted. Liveness defined (a value is live at program
point p if it MAY be read along SOME future path — reachability =
the over-approximation the machine can compute; the gap between
live and reachable = retained garbage, the memory leak definition
from module 01 ch14/15 re-derived formally: a leak IS
reachable-but-dead (the static-root problem: caches, statics,
closures — with the 3 classic leak shapes redrawn: the accidental
global, the forgotten listener (module 16 ch04), the closure that
captures the world). Tracing as proof search: mark = inductive
proof construction (each marked object = a proof fragment; the
worklist as the proof frontier), sweep = the free pass; the
complexity story (pointer-following is cache-hostile: mark
= depth-first on a random graph — derive the
pointer-chasing cost from module 01 ch02's 100ns-miss ladder:
touching 1 GB of pointers at ~100 ns/miss = ~10+ s single-threaded
naive — hence the entire parallel/incremental literature; the
mutator/collector word explained (who mutates what). **Write
barriers as monotonicity preservation**: incremental/concurrent
marking needs the invariant "black object doesn't point to white"
( tri-color abstraction: white = unvisited, grey = frontier, black =
proved; the lost-object problem drawn as the 4-step mutator
sequence that orphans a white object (one concrete interleaving
worked) ; the barrier = the mutator's obligation to notify the
prover (Dijkstra increment = re-grey the target; Steele deletion =
re-black with rescan; Yuasa snapshot-at-the-beginning = the
generational+concurrent sweet spot and why it's the deployed one —
the three barriers compared in one table, with the cost per
barrier in instrumented stores (the honest ~2-20 ns / instruction
overhead range). The proof-cost accounting for every collector
of module 01: **serial** = full proof each GC (STW pause = proof
latency — derive the pause from heap bytes / mark rate: 2 GB at
500 MB/s marking ≈ 4 s pause — the CMS/G1 motivation numbers);
**generational** = the weak generational hypothesis as an
EMPIRICAL law (95-99% of objects die young — the proof "most
young objects are unreachable" is cheap to find by scanning the
nursery only; the cost table: nursery scan = live young bytes
ONLY (derive: cost ∝ survivor bytes, not allocation volume —
this is WHY copying nurseries are fast), remembered set = the
old-to-young pointer census (the card table: 512-byte cards,
one byte per card, dirty on old-to-young store via the Yuasa
barrier — the layout drawn byte-by-byte; **mandatory: the card
table diagram with the arithmetic: 8 GB heap = 16 MB card table
= 0.2% overhead**)); **concurrent** = prove while running
(CMS: the failure mode = the sweeping proof can't keep pace with
allocation → concurrent mode failure → the fallback serial STW
(the 2010s LinkedIn/Twitter war stories, jstat's CMS sections);
G1 = the heap as regions, the pause-time goal as proof budget
( the pause = the incremental proof slice: mixed collections with
 the set-max-gc-pause-ms arithmetic)); **compaction = the
address proof** (sliding compaction: object identity is an
address, so moving = updating the proof (every pointer = a lemma
that must be rewritten — forwarding pointers, the Lisp 2
sliding algorithm's 3 passes drawn, why pinning breaks
compaction (the .NET LOH fragmentation story from module 01
ch13 lands here)); **reference counting as a local proof** (
RC = proof by construction (each reference = one unit of proof,
count = 0 = refuted) vs tracing = proof by global search —
the trade table: RC = free immediate, cycles unrefutable
(derive the 3-node cycle's stable counts by hand), tracing =
pause or barrier; Swift's ARC = RC with compiler-placed
retain/release = the proof inlined ( **mandatory: a 5-line
Swift function's SIL/asm with the inserts visible — the proof
as emitted instructions, this is the chapter's one artifact
that must be real**) ; the deferred-RC and biased-RC
optimizations (module 01 ch16's side tables return as the
weak-ref mechanism — the weak table as an out-of-band proof
registry). The theory's boundary: allocation cost (bump
pointer = O(1) because the free space IS the proof (no list —
the copying nursery's beautiful trick: allocation = proof
construction with no per-object cost; contrast malloc's
bin-fit (module 01 ch19))), the cache/generational interaction
( object mortality by cache behavior), and the eternal trade
: throughput (proof amortization) vs latency (proof
scheduling) vs footprint (proof overhead: headers, cards,
barriers — the 3-axis GC design space closing the chapter
with a table placing every collector from module 01 (serial
STW / parallel / concurrent / G1 / ZGC-colored-pointers (the
colored pointer = a 4-bit tag in the pointer (module 01
ch06's tagged pointers returning as the load barrier's
certificate) / Epsilon-no-op (the "no proof" collector as the
baseline)). Insiders: `System.gc()` lies (the JVM hint that
may do nothing, and `-XX:+DisableExplicitGC`'s production
war story — the RMI DGC timer), safepoints as proof points
(module 10 ch04's safepoint return as "the point where the
proof is checkable" — the time-to-safepoint metric on real
JVMs (the JIT'd frame must be walkable: the oop maps as
proof certificates), the safe-point bias bug class (counting
loops), the -Xlog:gc output dissected line-by-line in the lab;
the G1 humongous-object threshold (½ region) as the LOH's
Java dialect; Epsilon as the profiling tool (measure the
proof's cost by refusing to run it). Lab: `-Xlog:gc*` a
young collection with a 256 MB nursery (read pause, survivor,
card-scan times from the actual output — paste the lines and
annotate every number in your own words); compare
`-XX:+UseSerialGC` vs `G1` vs `ZGC` pause distribution on a
50M-short-lived-object churn (the table you produce is the
deliverable); python: `gc.get_threshold` dissected (700/10/
10's meaning: the 3 generations + the threshold-to-trigger
arithmetic), `gc.set_threshold(5,5,5)` + `tracemalloc`
snapshot diff finding a leak the cycle detector can't (the
closure-capture leak of module 01 ch14 reproduced with 4
lines).

**Chapter 05 — the limits.** The honest closing: what NO type
system can do, stated as the four impossibilities + the two
grand trades. **Impossibility 1: termination** (halting problem
→ no type system can admit exactly the terminating programs;
 languages that try (Agda/Coq require structural recursion /
 termination checkers — the price: total programming, you
 can't write a general server loop without lying (codata /
 coinduction as the honest escape — one paragraph, drawn, not
 deep)); **Impossibility 2: spec fidelity** (types prove
syntactic properties; "this function sorts" or "never returns
a wrong medical dose" is a spec — the gap is filled by tests
(weaker proofs), formal methods (module 21), or nothing; the
expression problem as a tiny instance of why even data-model
guarantees fight each other (draw the 2×2: add-a-case vs add-
a-function matrix for sum types vs OO interfaces — the
visitor as the compromise; the 1998 Wadler naming)). **
Impossibility 3: the world** (I/O, time, other processes —
the effect problem: a pure type can't constrain the real
world; Haskell's `IO` monad as the type-system's honest
surrender ("the value is an effect, sequencing is the only
law") — explained in plain words with one 6-line example;
effect systems (Koka's effect rows) as the ongoing research
frontier, not deployed). **Impossibility 4: safe escape
hatches** (any sound system with `unsafe`/`any`/casts has
holes by design — the trusted-computing-base shape (soundness
= the kernel of provably-safe code + the membrane of audited
code — the same shape as OS kernels (supervisor mode) and
browsers (the broker of module 16 ch01) — draw the three
layers side by side: this is the program's thesis stated one
last time: same shape everywhere). The two grand trades:
**soundness vs precision** (a sound system must over-reject
(borrow checker rejects legal programs — the false positive
tax; TypeScript chose precision over soundness — the opposite
corner; neither corner is free, draw the 2×2 with real
languages placed in it: TS/Flow (precise, unsound), Rust/
OCaml (sound, rejecting), Java-cast-era (unsound-by-escape),
 Haskell (sound, effects-limited)) and **static vs dynamic
as proof-schedule choice** (the same proofs, run at compile
time vs runtime: the tag word = the certificate carried per
value (module 01 ch06), the GC pause = the proof bill arriving
 (ch04), the borrow error = the proof delivered early — the
scheduling frame kills the "static vs dynamic" holy war:
it's not philosophy, it's WHEN YOU PAY; the total-cost
table: static = pay once for all paths, dynamic = pay per
execution — the a/b ratio (code written once, run 10^9 times
→ static amortizes; scripts → dynamic wins; the numbers make
the argument, not the vibes). What survives: the practical
checklist — the 6 questions to ask of any language (what
does it prove at compile time, what at runtime, what does
the tag cost, what's the escape hatch's blast radius, what
does the GC prove and when, what's the TCB) applied to Rust,
TypeScript, Go, Python as the closing table. Insiders: the
"soundness" vs "memory safety" vs "type safety" vocabulary
ladder (people use them interchangeably and they're nested:
type ⊆ memory ⊆ ... the containment drawn); the false-
friend `unsafe` in Rust ≠ `unsafe` in C# (Rust = proof
obligation, C# = pointer permission, two different
membranes); the TypeScript `unknown` vs `any` as the
disciplined escape (unknown = the proof-debt type: you must
pay with a check before use — the honest recommendation);
 GHC's `-fdefer-type-errors` as the proof-debt compiler flag;
the historical notes: the 1960s Algol vs Lisp debate as the
first static/dynamic round (Algol 58's committee fight),
Typescript's 2012 launch as the industry's admission that
JS's proof debt was unpayable at scale, Rust 2015 as the
affine-types-finally-shipped moment (the academic lineage:
linear logic 1987 Girard, Wadler's linear use 1990 →
Rust 1.0 2015, 28 years from paper to product — the
" academia-to-product" timeline that explains why module 01's
languages look the way they do). Lab: demonstrate each
impossibility concretely: termination (write a python
program whose termination depends on an open math problem
(Collatz or Goldbach-flavored — 4 lines), watch it run,
note that no static tool can classify it); spec fidelity
(property-test a "sorted" function with hypothesis — the
test finds the boundary bug, proving types alone couldn't
); expression problem in 30 lines each (sum-type version
and OO version, extend both axes, feel the friction);
the escape hatch census (count `unsafe`/`any`/casts in a
small real codebase with grep, compute the membrane ratio
— the number is the audit surface); the scheduling
experiment (python: 10^7 duck-typed calls vs a C
static call — the tag-check overhead measured, honest
numbers, the interpreter overhead confound acknowledged).

**Connections requirement.** Every chapter's Connections must
include (beyond same-module): ch01 → module 10 ch01 (the pipeline
that compiles sugar), module 01 ch18 (the elders — Lisp/Haskell's
lineage); ch02 → module 01 ch13 (C# nullable/generics —
**mandatory: repairs m01 ch13's orphan status**), module 01 ch06
(tagged values as certificates), module 16 ch02 (the DOM's
gradual typing); ch03 → module 01 ch08-17 (the languages placed
on the spectrum — at least 4 of them named), module 02 (the
mitigation ladder as runtime proofs), module 04 ch04 (vtables
as the type-erased proof); ch04 → module 01 ch12/14/15/16 (the
collectors re-derived), module 10 ch04 (safepoints), module 09
ch02 (the race detector as runtime proof — harks to Go's place
in ch03), module 01 ch23 (the grand table — **mandatory: repairs
m01 ch23's orphan status**, the table gets its proof-theoretic
reading); ch05 → module 21 (what formal verification adds),
module 03 (the bug classes that survive), module 01 ch20 (the
memory-model proof the hardware can't give you).

**The final exam** (`quiz/final-exam.md`): 12 questions, worked
answers, 5 parts (one per chapter, 3+3+2+2+2), at least one
question must place a new language on the safety spectrum and
justify it, at least one must derive the variance counter-example
or the mutable-borrow-lifetime counter-example, and at least one
must compute a GC cost (pause from mark-rate, or card-table
overhead from heap size).
