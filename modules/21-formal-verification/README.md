# Module 21 — Formal verification: the machines that prove programs

> Every module before this one ended with "the type system can't prove
> that" — this module is about what happens when you refuse to accept
> the answer. There exist machines that have verified an entire OS
> kernel (seL4: ~10,000 lines of C, ~400,000 lines of proof), a
> compiler (CompCert: verified from C down to assembly, the bug-free
> compilation theorem), and the elliptic-curve arithmetic at the heart
> of real crypto. This is the deepest layer of the program: below the
> languages (module 19's type theorems are the easy instance), below
> the compilers, at the level where *proofs about code become code*.
> The memory story is everywhere: a proof assistant is a trust kernel
> in memory (the LCF architecture: the theorem type that can only be
created by the kernel's rules), the verification condition is
derived by walking the same control-flow the compiler walked, model
checking is a brute-force proof with a memory budget — the "state
explosion problem" is literally a heap-out-of-memory error — and
the verified artifacts are the *only* programs in the world whose
memory behavior is a theorem, not a hope.

**5 chapters · one final exam · prerequisite: module 19 (types as
proofs — the easy case this module generalizes; ch02-03 especially),
module 01 ch04 (stacks/frames — the verification conditions walk
them), module 10 ch01 (the compiler's IR: Hoare logic operates on
the same graph); module 09 (concurrent proofs) and module 02
(the bugs that motivated the field) referenced, not required.**

## How to study it

Chapter 01 builds the vocabulary: specifications as math, the
soundness obligation, and the LCF trust kernel (why a proof checker
can be smaller than a C compiler); 02 is automatic verification —
model checking and SAT/SMT — where the state explosion problem is
derived as memory arithmetic and solved by the tricks that make
model checkers fit in RAM; 03 is the crown jewels: seL4 and the
verified-kernel lineage — what was proven, what it cost, what it
bought; 04 is the proof you already use: how the Rust borrow
checker's checks are really a decidable fragment of separation
logic, with the research lineage made explicit; 05 closes with the
verified-compilers-and-crypto world (CompCert, the verified
elliptic curves, HACL*/Vale) and the honest boundary — what still
can't be proven and why the field is not magic. Labs: Dafny (if
installable) or the TLA+ toolkit (TLC), Z3 via python, and
safe Rust examples that compile — the labs here are real tools
run for real, small proofs.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [What is a proof of a program](chapters/01-what-is-a-proof.md) | Specs, soundness, the LCF kernel — why the checking machine can be 100× smaller than the proving machine. |
| 02 | [Model checking and the state explosion](chapters/02-model-checking-explosion.md) | Brute-force proof over states, BDDs and SAT as compressed truth tables, BMC, the memory budget as the frontier. |
| 03 | [The verified kernel](chapters/03-verified-kernels.md) | seL4: the proof effort in person-years, the 100:1 proof-to-code ratio, what the theorem says and doesn't. |
| 04 | [The borrow checker is separation logic](chapters/04-borrow-checker-proof.md) | Ownership as a decidable fragment; the lineage from Reynolds/O'Hearn to NLL; Miri as the operational checker. |
| 05 | [Verified compilers and crypto](chapters/05-verified-compilers-crypto.md) | CompCert's correctness theorem, the verified-field-arithmetic stacks, and the honest boundary of proof. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — what a proof of a program is.** Start from the
specification gap (module 19 ch05's conclusion): types prove weak
properties; tests prove single executions (a passing test = one
point verified); a proof = all executions at once — the quantifier
jump (∀ inputs vs ∃ a good run). The vocabulary built from Hoare
logic up: {P} S {Q} (the triple: precondition, statement,
postcondition), one worked example in full ({x ≥ 0} x := x+1
{x > 0} with the assignment rule — the backward substitution
 ({Q[x:=E]} x:=E {Q} — derived on 3 examples, the direction of
substitution shown as "the postcondition flows upstream"); the
while rule with the invariant ( {I ∧ B} body {I} ⊢ {I} while B do
body {I ∧ ¬B} — THE concept of the chapter: one real invariant
worked (sum-loop: I = "s equals the sum of the first i elements" —
prove all three obligations: initiation, consecution, termination,
each shown as a first-order formula you can check by eye); the
weakest precondition computed on a 5-line program by hand (the
wp calculus as a *program-to-formula compiler*: connect to module
10 ch01's frontend — the same graph walk, different backend:
this correspondence is mandatory and must be drawn as a two-
column diagram: compiler emits code, verifier emits formulas).
Soundness vs completeness (soundness = the tool never accepts a
false proof (the trust question); completeness = the tool can
prove everything true (never achievable for programs — halting
again, module 19 ch01); the practical position: we choose
sound-by-construction and incomplete). **The LCF architecture
as the field's memory-model insight** (the chapter's core):
 Milner 1972's design — a theorem is an abstract datatype whose
 only constructor is the kernel's rules (the ML language was
BUILT to host this: the type system enforcing "you cannot
forge a theorem" — the `thm` type with no public constructors,
the kernel as the ~5,000-line trusted core (vs Coq's ~30,000-
line kernel, Isabelle's ~2,500 (the numbers table: kernel size
= the trusted computing base, the TCB concept of module 19
ch05 returning as a *line count*); the de Bruijn criterion
(small checker, big prover — the "proofs as files" discipline:
the proof object is checkable independently (the kernel's
output = the certificate; why this beats "trust the tool":
the TLA+/TLC vs Coq/Isabelle split (the checker-vs-assistant
trade). The proof assistant as a memory artifact: the proof
state, the goal stack, tactics as state transformers (one
`intro`/`apply`/`induction` session shown as state snapshots
— the proof as a search through goal states, the same shape
as a debugger's call stack); the kernel's discipline: type-
checking the term (the `Prop` sort, the universe check), the
proof term as a λ-term (Curry-Howard from module 19 ch02 now
load-bearing: the proof IS a program (the `Qed` moment: the
term is checked, the theorem sealed). What specifications look
like: JML for Java, ACSL for C, Dafny's pre/post — one real
function specified fully (binary search with the loop
invariant written out in Dafny/ACSL syntax — the spec is 3×
longer than the code, and the chapter says that's normal (the
specification-cost table: ~1-5× code size, the honest
economics previewed for ch03's person-year numbers). Trusted
vs untrusted proof search (the tactic layer may be buggy —
only the kernel's check matters (the proof-carrying-code idea
( Necula 1996: the certificate ships with the code — the JIT
/JVM connection (module 01 ch12): bytecode verification as
proof checking (the JVM's verifier IS a small LCF-style
checker for a type-safety proof — derive this identification
explicitly, it's the chapter's bridge moment: you've been
running a proof checker every time you ran Java). Insiders:
the `admit`/`sorry` escape hatches and the CI rule (grep
for them, the "proof debt" management); the kernel-bug
history (the Isabelle/Coq kernel bugs found over the years
(mostly soundness-bug-in-implementation class, the honest
list, why they matter less than compiler bugs (the kernel
is small enough to review (the 2000-2020 incident list is
short — ~a handful); "proofs checked by a machine" ≠ "the
spec was right" (the VDM-era lesson: the Vienna conference
case study where the spec itself was the bug); the SAT-
solver-in-the-kernel debate (why some kernels avoid calling
external solvers (trust boundary, the reflection pattern:
verified solvers (the Coq SMT reflection work) — the
absolute-state-of-the-art honestly marked as such. Lab:
run Z3 from python (`pip install z3-solver`, the 10-line
prime example: solve/unsat/proof (unsat core = the
explanation, shown), a real verification: the sum-loop
invariant as a SMT implication ( prove
`i ≤ n ∧ s = Σ[0..i] → (after body) i+1 ≤ n ∧ s = Σ[0..i+1]`
 — encode, solve, read UNSAT = verified); write one Dafny
method (if dotnet available: binary search with `requires/
ensures/invariant` — the verifier catches the off-by-one
(or the paper version: the same method in Why3 syntax,
the obligation list derived by hand); inspect a proof
object (Coq's `Print` on a 3-line theorem — the λ-term
as the artifact).

**Chapter 02 — model checking and the explosion.** The automatic
half: verify temporal properties of state machines without a human
writing invariants. Temporal logic from scratch (the operators
as English first: "always" (G), "eventually" (F), "next" (X),
"until" (U) — LTL's five operators with one real spec each (a
mutual exclusion spec: G ¬(both in CS) ∧ G (request → F enter));
CTL vs LTL (the branching-time distinction in one paragraph +
the tree diagram; the expressiveness incomparability stated,
not derived); Kripke structures as the machine (states +
transitions + labels — a 6-state model of a 2-process lock
drawn fully, the state space = |variables|^|bits| (the
arithmetic: 2 processes × 3 states each + flag bits = the
full product; a model of 20 booleans = 2^20 = 1M states,
40 booleans = 1T — **the state explosion as literal RAM
arithmetic: 1M states × 32 bytes = 32 MB (fits), 1T × 32 B
= 32 TB (never fits) — the frontier of the whole field is
this one multiplication, derive it slowly**). The checking
algorithms (explicit-state): the graph search for G ¬bad =
reachability (BFS over the product automaton (the property
automaton × the model: the product construction shown on the
6-state lock (Büchi acceptance for liveness — the cycle-
detection for "F enter" = a cycle without accepting states,
the nested DFS (the two-stack algorithm, 10 lines of
pseudocode); counterexamples as traces (the witness path
the tool emits — the debugging loop: refine model, re-check
(the TLA+/TLC workflow's real daily life). The memory tricks
that made model checking real: **BDDs** (the binary decision
diagram as a *compressed truth table* (the sharing and the
ordering (one worked BDD: a 3-variable function, the DAG with
shared subtrees, the node count vs the 2^n table rows — the
compression arithmetic (the VDD of an n-variable function can
be O(n) for structured functions (addition's ripple-carry
 BDD, the ITE operator and Bryant's canonical-form theorem
 (every function has ONE minimal BDD given a variable order
 — canonicity = O(1) equivalence checking, the payoff: `f ≡
g?` in one pointer compare vs 2^n rows; the variable-order
sensitivity (the famous bad case: multiplication circuits
BDD size linear vs exponential depending on interleaving —
the sifting/renaming heuristics, the "ordering is an art"
honesty); **SAT** (the Boolean satisfiability problem as the
universal solver (3-SAT NP-complete stated, the practical
shock: CDCL (conflict-driven clause learning) solves real
instances with millions of variables — the algorithm
presented as memory: clauses as watched literals (the
2-watched-literal scheme (the lazy data structure: watching
2 per clause, moving watches on assignment — the invariant
and the propagation cost analysis (the clause DB's growth
and deletion (the restarts (Luby's schedule) and the VSIDS
heap (the activity-heap as a priority queue on steroids
(why "the solver's heap" makes the search focus — module
01 ch05's bin heap returning as a weapon); the phase
transition (the density α = clauses/vars ~4.26 knife-edge
where random 3-SAT goes hard — the plot described, the
search-vs-decision asymmetry (the easy-hard-easy curve,
why real problems are often solvable (structure ≠ random);
 **SMT** (SAT + theories: the DPLL(T) architecture (the SAT
core + theory solvers (equality, arrays, arithmetic) — the
 Nelson-Oppen combination (the theory interface's sharing of
equalities — the architecture diagram; the theories that
matter for programs: equality-uninterpreted (EUF (the DAG
congruence closure (the union-find from first-year
algorithms as the theory solver's core — module 19's
logic + module 09's union-find: the fields literally meet),
arrays (the select/store axiom set), linear arithmetic
(the simplex variant (the difference logic's graph
encoding — Floyd-Warshall as a theory solver (the
beautiful mapping: negative cycle = unsat (one worked
example of the graph encoding). **BMC** (bounded model
checking: unroll the transition relation k steps, encode
as SAT (the formula size = k × transition (the bound
arithmetic: the bug within k steps is found in seconds,
the bug at depth 10^6 needs the invariant — the complete
vs bounded distinction, k-induction as the ladder between
(the IC3/PDR algorithm named as the modern unbounded
solver (the frame-reachability idea in 3 sentences — the
state of the art named honestly). What it's all used for:
the industrial case list (TLA+ at Amazon (the AWS S3
(2014-15: the subtle race in S3 replication found by TLC
before deployment — the real war story with numbers (the
person-days vs the incident-cost comparison), AWS's
"why we use TLA+" numbers (450+ specs, ~10^x model-
checking runs), the Microsoft SLAM/SDV lineage (driver
verification shipped in the WDK — the static-driver-
verifier as a product), the protocol that killed: the
Intel Pentium FDIV bug (1994, $475 M, the birth of
industrial model checking (the cost table of the
incident vs the annual EDA license — the moral stated in
one line). Insiders: the "TLA+ is executable pseudocode"
position (Lamport's essays — the spec's audience is
*designers*, not provers (the PlusCal → TLA+ translation
(Why you write the algorithm twice); the TLC vs Apalache
(explicit vs SMT-backed TLC (the scaling numbers: TLC
~10^8-10^9 states on a laptop, distributed TLC on a
cluster (the "just buy RAM" school); the model-checker
as a debugger (the counterexample trace = the failing
test you didn't have to write (the hybrid workflow:
check the design, test the implementation — the
separation of what-to-prove (spec level) from what-to-
run (code level) — the refinement mapping named (TLA+
implementation proofs via refinement (the simulation
relation, one paragraph); the "no true Scotsman" problem
with SMT timeout folklore (the 30-min budget, the
'monotonicity' results (the solver that got slower on
your CI — the pin-the-solver-version discipline). Lab:
TLA+ if the toolkit is available, else pure python model
checker (build a 40-line explicit checker for the 6-state
lock model + a violated liveness property (the
counterexample trace printed — the code provided, must
run); z3: solve the 8-queens as SAT (the encoding, the
runtime for n=8 vs n=12 — the growth measured); a
watched-literal DPLL on paper (trace 4 propagation steps
by hand on a 5-clause formula — the work shown); the
bmc flavor: python `z3` unroll a 3-bit counter 10 steps,
prove "the counter never wraps" is FALSE within k=8 (the
counterexample at k=8 = the wrap found (the bound
arithmetic felt in the runtime: k=8 instant, k=64 what
happens? (document honestly).

**Chapter 03 — the verified kernel.** seL4 as the case study that
defines the field's economics: what it cost, what it proved, and
what the theorem actually says. The project told properly:
2004-2009, NICTA, the L4 verified lineage (the sel4 microkernel
~8,700 lines of C + ~600 lines of asm (the numbers exact),
the proof: **functional correctness** in Isabelle/HOL — the
statement drawn honestly (the C implementation refines the
abstract spec (the refinement tower: abstract → haskell model
→ C implementation, two refinement theorems + the C-parser
's trust base (the CompCert-style semantics question: is the
 C semantics used the same as the compiler's (the seL4
resolution: the C-to-Isabelle parser + the compiler-verification
endgame in ch05); the **proof statistics as the field's
economics** (the mandatory table): ~200k+ lines of Isabelle
script (the often-quoted ~370k-400k line figure with the
honest ranges), ~20-25 person-years of proof effort, the
proof:code ratio ~100:1, ~$M-scale cost (the DARPA HACMS
follow-on: the proof reused across projects (the amortization
story: the one-time cost vs per-vulnerability cost (the
Heartbleed-class cleanup bills as the comparison column));
the bugs found during verification (the ~160 implementation
bugs + ~160 spec bugs caught by the proof process BEFORE
release — the verification-as-testing finding (the bugs the
proof found were the ones tests miss (the initialization
order bug, the rare interleaving (the data is the chapter's
quiet bombshell: proving found real bugs in code that was
already the most-reviewed microkernel in history)). What
the theorem does NOT say (the honesty section, mandatory
in full): (1) the spec itself was not proven "correct"
(what does the kernel *want* to do — the spec is human-
reviewed, ~10k lines (the gap that remains); (2) the
proof assumes the C semantics (compiler bugs escape
(until CompCert-seL4 — ch05's story), the assembly glue
 (~600 hand-written lines unproven (the trust base
enumerated: kernel + compiler + asm glue + hardware (the
 TCB list drawn as a stack with the proof covering the
middle — exactly module 19 ch05's TCB shape returning);
(3) timing is out of scope (no WCET proofs — the
worst-case execution-time verification as a separate
discipline (the timing-analysis tooling (the WCET
tools on seL4's RISC-V/Arm builds, why latency proofs
matter for the RTOS world of module 20); (4) the
hardware is assumed (no Rowhammer (module 02), no
faults (the Bellcore story of module 18 ch03: the
verified-software-paper-over-silicon honesty (what
glitch attacks do to a proven kernel (the 2019+
glitch-seL4 discussion, the defense: the glitch-
detection hardware + the redundancy — the boundary
re-drawn). The architecture of the proof (the tour):
invariants vs refinement (two styles shown: the
invariant-style proof of the kernel's state (page
table invariants (the "every mapped frame has a
capability" theorem — the capability model as the
proof's backbone: seL4's caps = the ownership proof
(module 19 ch03's affine types returning as *kernel
state* — the identification drawn: a capability
system IS a runtime type system for memory (the
clue that ties the whole program together: CHERI's
hardware capabilities as the ISA-level version (the
morecapst/CHERI lineage in 3 sentences); the
refinement proof's structure (the simulate/
correspondence invariant: the forward simulation
(the state relation drawn as a commuting diagram
(abstract state ↔ concrete state, each step
preserved — the diagram IS the proof's shape). The
downstream: verification's second act (the verified
systems stack on seL4 (the Galois/Boeing HACMS
drone: the verified kernel + unverified middleware
(the architecture's compartmentalization doing the
isolation work — module 15's containment story with
a proof at the bottom (the DARPA program's outcome
numbers honestly); the certified-classification
path (the US government's seL4 evaluation (the
insurance argument: a proof as an audit artifact
(the certification economics vs Common Criteria
paperwork — one paragraph each). The other verified
kernels (the comparison table: CertiKOS (the
 Yale/OSU verified kernel (Yale's CertiKOS: the
 compositional approach (the layered proofs vs
seL4's monolith (the trade: composability vs
effort), INTEGRITY-178B (the commercially certified
(but *not* formally verified kernel — the
distinction that marketing blurs (the "verified"
word's legal meaning (DO-178C DAL A vs a theorem
(the honesty paragraph the chapter owes the reader);
the two camps: microkernel-with-proof vs
monolith-with-knowledge (the Linux position (the
invariant checkers / formal methods IN Linux (the
memory-model tooling: the Linux memory model in
Cat (the herd7 tool checking litmus tests — module
01 ch20's model formally verified in 2017 (the
data-race-freedom theorem for the kernel's own
concurrency: the lkmm tool's daily use in kernel
development (the "the kernel's concurrency spec is
a machine-checked artifact" fact — one paragraph,
mandatory: it's the module's proof the field
escaped the lab). Insiders: the proof's maintenance
(the seL4 proof kept up with the code (the
regeneration discipline (proof-CI: the Isabelle
build in the release pipeline (the proof bit-rot
problem: proofs depend on library internals (the
Isabelle/Pure version pinning (the "proof code
review" (a diff in the proof = a semantic diff in
the guarantees — the release-notes discipline); the
proof's teaching value (the seL4 proofs are open
(the repository walk: where the invariants live
(the AbstractSpec/HaskellSpec/CRefine directories
(the map to the commuting diagram); the
"verification engineer" career honesty (the hiring
market (the Ada/SPARK world (the subset-language
escape: SPARK as Ada-with-proof (the subset's
 discipline: no pointers, no aliasing → provable
(the SPARK toolset's flow analysis + proof obligations
(the CubeSat story: the NASA/Ada flight software
(the IRAD work, the prove-the-guidance pattern (the
 ~10-100x proof cost at the component scale — the
component-level economics vs kernel-level); the
Rust-for-verification debate (2020s: Ferris-like
"we get it free from the type system" vs "the
borrow checker proves a weaker theorem than
functional correctness" — the honest placement
(the answer: they compose (the verified component
in Rust-on-seL4 (the CakeML/Rust lines named). Lab:
the seL4 repo walk (the file inventory: `ls` the
proof tree (the lines counted with `wc -l` on the
CRefine (the 100:1 ratio *measured by the reader* —
the command and the number the reader sees (this
lab is mandatory and free (no build needed); the
Isabelle-free comprehension: read the abstract spec
's ` kernel.state` theory (one page of Isabelle
glossed in the chapter (the reader reads the
actual PDF section (the `Inv` theory's page count;
paper-lab: the capability-list invariant (write
the invariant for a 3-object toy capability system
(the checker in 30 lines of python: simulate
rights transfer, assert the invariant never breaks
(the code provided, runs anywhere).

**Chapter 04 — the borrow checker is separation logic.** The
proof-theoretic reading of the tool millions use daily: unpack
Rust's borrow checker as an *automated, decidable* proof system
for a fragment of separation logic. The lineage told properly:
Reynolds' separation logic (1969-2002: the P * Q
(separating conjunction: P holds on heap h1, Q on disjoint h2 —
the points-to assertion (x ↦ e: "the location x holds e AND I
own it" — the ownership reading as the logic's gift (why
"separation": the frame rule ( {P} C {Q} ⊢ {P * R} C {Q * R}
— C can't touch R's memory (the local-reasoning principle (the
proof-carrying invariant: "the code you didn't mention doesn't
change" — the small-footprint proof's power (one worked frame-
rule derivation on a swap function (the proof is 4 lines BECAUSE
of the frame rule — the contrast with classical Hoare logic's
global state (the "the whole heap is my business" problem —
the modularity theorem separation logic bought (the O'Hearn/
Pym/Reynolds 2001-2002 papers as the birth); the concurrent
act (O'Hearn 2007: Rely-guarantee + separation = CSL (the
 critical-section rule with the resource invariant (R * (P)
{C} (Q * R) — the interference-free local reasoning (the
Owicki-Gries explosion killed (the proof-size numbers (the
philosophers problem: Owicki-Gries-style interleaving
reasoning vs CSL's rule-of-release — the modular proof
sizes compared in a table). **The borrow checker as a
decidable fragment**: what "decidable" means here (borrow
checking runs in compile time, seconds — therefore it CANNOT
be full separation logic (semidecidability of general
invariants — the halting problem shadow of module 19 ch01
returning); what the fragment is: (1) the ownership discipline
= affine types (module 19 ch03's foundation), (2) the
lifetime discipline = region constraints (the region
inference lineage (Tofte-Talpin 1994-97: regions as a compile
-time approximation of liveness (the stack-regions idea (the
early ML Kit's region-based ML (the whole-heap-region's
failure and the lesson); (3) the aliasing discipline = the
 borrow rules as a conservative approximation of "no data
race on *mut" (the send/sync auto traits as additional
propositional proof rules (Send: the value may cross threads
(the proof rule: no shared mutable state inside (the auto-
derivation as type-level proof search (the unsafe impl
Send = the admit hole). The NLL reformulation as proof
search (the 2018 rewrite: lexical scopes → live ranges (the
constraint graph: each borrow's lifetime as an interval
constraint DAG (region variables as nodes (outlives edges
( the fixed-point solve (borrowck-as-type-checking (the
 polonius project's name for the next iteration (the
diagnosis: the old checker's false positives = the proof
system's incompleteness (the "rejected legal program" tax
of module 19 ch05 measured in real 2015-2018 GitHub issue
numbers (the borrow-checker-ergonomics roadmap (the
non-lexical-lifetimes RFC's problem statement quoted). The
operational side: Miri as the dynamic checker (the stacked-
borrows/tree-borrows operational semantics (Jung et al 2019-
2023: the model that defines what `unsafe` code must uphold
(the tree-borrows 2023 refinement (the retagging operation,
the protector mechanism (the model's checker = the test
oracle (cargo miri runs your unsafe code against the model
 — the two-level proof: the type system's conservative
static proof + Miri's precise dynamic model (the soundness
statement between them: no UB that Miri catches in tested
paths (the testing gap honestly stated (Miri doesn't run
all paths either — the discipline: unsafe code is proven
by review + Miri + the model's documentation (the tree-
borrows spec as the contract `unsafe` authors read (the
same trust structure as module 19 ch03's unsafe-kernel
pattern). The ghost state technique (the Rust
core/alloc's internal invariant enforcement (the
`PhantomData` type as a logical-level tag (the proof-carrying
type: the compiler's reasoning tracks (the validity invariant
of Vec (the `Unique<T>` marker (the internal unsafe proof
obligations enumerated (the "std is an unsafe kernel with
a safe membrane" repetition (module 19's shape, now with the
proof-theory vocabulary to say it precisely). Beyond Rust:
the language-design lineage (the linear-types→languages
family tree: Cyclone (2001-2006: the C-dialect with regions
(the ownership discipline's first industrial experiment),
Mezzo (the mid-2010s research language), Rust (the
production synthesis (the "the papers finally shipped" arc
closed (the 2015-2025 decade: 28 years Reynolds→Rust-1.0,
 module 19 ch05's timeline completed); the next rungs (the
 research frontier: the alias-borrowing (RustBelt's Iris
proofs: the logical foundations of Rust in separation logic
( 2018-2021: the language's soundness THEOREM (the
 RustBelt result: the core type system + unsafe interop
rules proven sound in Iris (the Coq/Isabelle-class proof of
the borrow checker's own rules — the meta-proof (the proof
that the checker proves what it claims); Hylo/Val,
Oxide, the "RustBelt successor" languages named as the
live frontier (the capability-tagging experiments (the
Crust/unsafe-hygiene research). Insiders: the "borrow
checker proves liveness, not validity" precision (the
uninitialized-memory hole (MaybeUninit as the honest
type (the validity invariant (the 2's-complement reading
of "valid bool" (the UB class the checker doesn't stop
(validity-vs-safety distinction from the RustBelt papers —
the precise statement: the type system proves *safety*
(no UB in safe Rust), the runtime must maintain *validity*
(the bit-pattern obligations (the niche optimization's
validity dependency (the None-is-0 trick + the unsafe
contract); the drop order as a proof obligation (the
 dropck rules (the may-dangle escape (the `#[may_dangle]`
attribute as the advanced lifetime variance tool (the
PhantomDrop pattern); the stacked-vs-tree borrows'
semantic difference (the tree model's more-liberal
definition of "unique enough" (the 2023-2024 migration
debate (the Miri default's story); the specialization-vs-
soundness tension (the sound specialization problem (the
unsound specialization attempt in nightly 2015-2018 (the
min_specialization escape); the async state machine as
the borrow checker's hardest customer (the generator's
across-await borrows (the Pin proof obligations (module
19 ch03's Pin returning with the proof-theory reading:
Pin = the "won't move" ghost proposition). Lab:
compile-time proof experiments (the 5-line program the
checker rejects with the region-constraint reason (the
error message dissected: the "borrowed value does not
live long enough" error = the failed fixed-point (the
constraint diagram hand-drawn from the error output
(the lab turns error messages into proof theory: the
student reads the failing constraint (this is the
chapter's mandatory lab and it is free); `cargo miri`
(if rust available: run the tree-borrows checker on a
20-line unsafe toy (the UB caught: the use of a
dangling reference (or the paper version: the same
program's verdict derived by hand against the tree-
borrows rules); the frame rule on paper (the swap
proof (the reader writes the 4-line separation-logic
proof with the frame rule instantiated (the proof
provided in the chapter, the reader verifies each
line against the rule schemas).

**Chapter 05 — verified compilers and crypto.** The bottom of the
stack: when the compiler and the crypto libraries themselves carry
proofs. CompCert told properly: the x86/ARM/PowerPC/RISC-V-verified
C compiler (the ~2005-2020 Leroy project in Coq), the **statement
(CompCert's correctness theorem)** drawn carefully: the generated
assembly's observable behavior is a refinement of the C source's
semantics (the simulation across ~11 passes (each pass (Clight →
Cminor → RTL → LTL → Mach → Asm) with a correctness proof (the
pipeline diagram with a proof-check mark on each arrow — the
compiler as a tower of verified transformations (the translation
validation alternative (the unverified compiler + the verified
checker (the trade: verification effort vs generality (the
Yocto/RockSalt line)); the numbers (the ~600 kLOC of Coq (the
 ~40-50 kLOC of OCaml extracted + the C source's ~15 kLOC (the
proof:code ratio's second data point (~10-15:1 for the compiler
vs seL4's 100:1 — the ratios compared and the lesson (the more
state-machine-like the artifact, the cheaper the proof (compilers
 = dataflow, kernels = state + concurrency (the insight stated);
the GCC vs CompCert bug-count study (Yang et al 2011: the
fuzzing study where CompCert had ~0 miscompilations vs dozens
for GCC/LLVM in the same harness (the "verified middle-end"
effect (the paper's data as the field's best advertisement
(the retelling with the numbers); the trust base of CompCert
(the Coq kernel (the OCaml extraction (the assembler/linker/
runtime (the proof covers the passes, not the pipeline's edges
(the TCB list drawn one more time — the stack of module 19
ch05 now fully materialized: language proofs (19) → kernel
proofs (21-ch03) → compiler proofs (here) → the hardware
(the next rung named (the RISC-V formal specs (the Sail/
Isabelle RISC-V models (the ISA-verified story (the
information-flow proofs at the ISA level (the CHERI
ISA proofs (the hardware-level TCB's erosion (the "the
proof stops at RTL" honesty (module 17's boundary: the
Yield=proof-of-economics chapter (the silicion bugs
(the errata (the formally-verified CPU cores (the
Kami/Bluespec RISC-V work (the proof tower's current
top floor). The verified-crypto stack (the practitioner's
payoff): HACL* (the INRIA/Microsoft verified TLS
crypto: the Curve25519, P-256, ChaCha20, Poly1305
implementations proven functional in F* (the
 extraction to C (the proof: the C code refines the
 mathematical spec (the *256-bit arithmetic theorems
(the carry-chain proofs (the constant-time property's
partial-verification honesty (functional correctness
proven (timing side-channel freedom = the operational
property (not fully proven — the Vale/CT-verify line:
the constant-time verifier (the symbolic execution
proving "no secret-dependent branches/data accesses"
(the proof of the *side channel class for specific
modules (the two-level honesty: what's proven (the
math), what's checked (the timing), what remains (the
composition (the TLS protocol as the unproven layer
(until Everest (the verified TLS 1.2/1.3 research
milestone (the full-stack verified TLS story: the
Everest/Crymp projects' status honestly dated));
the deployment (the Firefox's HACL* curve25519
(the practical proof: the verified code running in
a browser near you (the headline moment of the
field (the performance cost table: the verified
 implementations within 1.05-1.3× of hand-tuned
(the "proofs no longer cost 10×" claim's data));
the BoringSSL/OpenSSL's verification-adjacent tooling
( the ct-grind/dua (constant-time testing (the
difference between *proven* constant-time and *tested*
constant-time (the spectrum drawn (the same soundness
ladder as everything else: test < check < prove). The
boundary section (the honest close of the module):
what remains unproven in 2026 (the list, stated
plainly: the hardware (Rowhammer/glitches (the
electromagnetic world (the physics of module 17
(the supply chain (the binary you run vs the source
you read (the reproducible-builds gap (the proof-of-
provenance problem (the SLSA framework's "verifiable"
(not "verified" (the word games separated); the
specs (the spec bugs (the seL4 experience: ~160
spec bugs found during verification (the proof
protects the implementation from itself, not from
the requirements (the formal-methods engineer's
honest slogan: "we proved the code matches the
spec (the spec matches your intent is your
problem"); the economics (the 100:1 cost (when
it pays (safety-critical, high-assurance,
long-lived, reused (the four-condition checklist
for choosing verification (the decision rubric
the reader can apply tomorrow (the amortization
table (the seL4 20 py ÷ the 15-year reuse (the
per-project cost → the per-year cost (the
honest comparison with the CVE-of-the-month
alternative); the future (the LLM-assisted proof
debate (2024-2025: the autoformalization experiments
(the AlphaProof-class systems (the proof-assistant
copilots (the "the 100:1 ratio becomes 10:1" bet (
the field's betting line stated as a bet, not a
fact). Insiders: the extraction trust question (
Coq's extraction to OCaml (the ~30-line trust kernel
vs the extraction's semantics preservation (the
seL4's C-to-Isabelle parser (the parser's trust
(the "the proof is about the parser's output" chain
(the hand-verified alternative (the RISC-V Sail
model's use in CompCert-class proofs); the proof-
engineering folklore (the Isabelle/Coq styles (the
Isabelle/HOL's automation (sledgehammer: the SMT
solvers in the loop (the "hammering" workflow (the
 20% of proof lines automated to 80% folklore (the
proof-maintenance cost's invisibility in papers;
the verified-GC story (the Verified GC (the RISCY
GC's frame rule application (the GC's correctness =
the mutator's safety proof (the "collector preserves
the mutator's invariants" theorem (module 19 ch04's
barriers getting their proof (the write barrier's
justification as a lemma (the practical closure:
the module's chain from the barrier instruction
(ch19's ch04) to the proof that it suffices. Lab:
run a verified artifact (the CompCert's `ccomp`
binary (if installable: compile the sum program,
compare gcc/clang output (the "it's a real compiler"
moment (or the paper version: read the CompCert
 pass list (the `ccomp -flongjmp` flags' docs); the
Everest/HACL* walk (the GitHub tree's F* files (the
`Curve25519.fst` file's proof density (the line
counts (the reader measures the proof:code ratio of
real verified crypto — the wc -l lab again (mandatory
(it's free); the constant-time check (the dudect-style
python harness from module 18 ch05 re-run against
a python bignum modexp (the variance plot (the
"not constant time" verdict demonstrated (the
contrast with the `cryptography` library's
ed25519 (the constant-time claim *tested* (the
spectrum: tested vs proven (the chapter's last
artifact: the reader's own measurement.

**Connections requirement.** Every chapter's Connections must
include (beyond same-module): ch01 → module 19 ch02 (Curry-Howard
load-bearing), module 01 ch12 (the JVM verifier as LCF-kernel
moment — mandatory explicit identification); ch02 → module 09 ch01
(the state explosion vs the concurrency tools), module 17 ch03
(the BDD/SAT engines as circuits), module 01 ch05 (the heap-size
frontier arithmetic); ch03 → module 19 ch03 (capabilities =
runtime types), module 07 ch04 (Linux's herd-verified memory
model — mandatory), module 05 ch01 (the mobile kernels' proof-
adjacent world); ch04 → module 19 ch03 (the affine foundation —
mandatory bridge), module 01 ch10 (Rust: the practitioner's view
this chapter re-theorizes), module 09 ch03 (the race-freedom
theorem); ch05 → module 10 ch01 (the compiler's proof mirror),
module 18 ch05 (the constant-time spectrum: tested/proven),
module 17 ch02 (the proof tower's top floor at RTL), module 04
ch05 (binary verification: the untrusted-binary problem from the
RE side — **mandatory: m04's ch05 reverse-reader needs this link
(the "you can't prove what you can't read" honesty from both
sides)).

**The final exam** (`quiz/final-exam.md`): 12 questions, worked
answers, 5 parts (one per chapter, 3+3+2+2+2), at least one
question must compute the state-explosion arithmetic (states ×
bytes vs RAM), at least one must enumerate the TCB of a given
verified stack, and at least one must explain why a specific
program class (e.g. an async state machine) stresses the borrow
checker's fragment.
