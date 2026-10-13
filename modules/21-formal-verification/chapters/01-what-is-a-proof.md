# Chapter 01 — What is a proof of a program

> **The 30-second version.** A test proves one thing: this input, on this
> build, behaved this way — an existence proof, ∃ a good run. A
> verification proves a universal claim, ∀ inputs — that quantifier jump
> is the whole discipline. The machinery is small: Hoare triples {P} S {Q},
> one rule per statement shape, run *backward* from the postcondition until
> the program compiles into a formula (wp — the same graph walk a compiler
> does, emitting formulas instead of code). Formulas go to a solver
> or proof assistant, and the non-negotiable property is *soundness*: the
> checking machine never accepts a false proof. The field's answer is LCF
> (Milner, 1972): a kernel so small its trusted computing base is a line
> count (~2,500 Isabelle, ~30,000 Coq), below which everything is
> untrusted and cannot forge a theorem. You already run one: the JVM
> bytecode verifier is a proof checker of this shape.

## The quantifier jump: one good run vs all runs

Module 19 ended with the specification gap: types prove ∀-over-inputs
properties, but only the weak ones; testing comes from the other direction
and stays there. Pin the difference as logic — it decides every tool choice
later:

- **A test is ∃.** "Test 7 passed" asserts: ∃ input (this one) such that
  program(input) = expected. Green CI is a *finite set* of such points —
  each real, none saying anything about the inputs between them. The bug
  lives in the gaps by construction.
- **A proof is ∀.** {P} program {Q} asserts: ∀ states s satisfying P, if
  the run from s terminates, it ends in a state satisfying Q. One theorem
  covers the continuum — 2^32 values of a `uint32` argument, unbounded
  streams, all interleavings.

The asymmetry runs both ways. A single counterexample kills a ∀-claim —
which is why tests, fuzzers, and model checkers (chapter 02) are *bug
finders*: they refute. But nothing finite can *confirm* a ∀-claim over an
infinite input space, and Rice's theorem (module 19 ch01) guarantees no
machine can decide one. A proof must be *built*: a ∀-statement plus a
derivation — an object a small machine can check; in the LCF design below,
that trust boundary is literally a data type.

## Hoare logic: the postcondition flows upstream

A **Hoare triple** {P} S {Q} reads: if S starts where P holds and
terminates, the final state satisfies Q (partial correctness; termination
is separate, below). Each statement shape gets one derivation rule.

**The assignment rule.** For `x := E`, the rule is:

    { Q[x := E] }  x := E  { Q }

Read it right-to-left: to make Q true *after*, make Q[x := E] true
*before*. The postcondition flows upstream because assignment is
deterministic: the final state is a known function of the initial one, so
any claim about the final state *is* a claim about the initial one,
computable by substitution. Three examples, mechanical:

1. `{ ? }  x := x + 1  { x = 5 }` — (x = 5)[x := x+1] = x+1 = 5, so the
   derived precondition is **x = 4**: the future of x as a claim about its
   present.
2. `{ ? }  x := 5  { x = 5 }` — (x = 5)[x := 5] = (5 = 5) = **True**: the
   assignment manufactures the fact from nothing.
3. `{ ? }  x := y - 1  { x > 2 }` — (x > 2)[x := y−1] = y−1 > 2, i.e.
   **y > 3**: the claim jumped to y — the variable you *read* upstream,
   not the one you're about to overwrite.

The derived condition is the *weakest* that works: {x ≥ 0} x := x+1
{x > 0} because backward substitution gives x+1 > 0 = x ≥ 0 over ℤ.

**The while rule — the concept of the chapter.** A loop runs 0..∞ times;
no fixed substitution can flow a postcondition through it — you supply
the missing information:

    { I /\ B }  body  { I }   |-   { I }  while B do body  { I /\ ¬B }

**I is the loop invariant**: true at loop entry and after *every*
iteration — a fixed point of the body's meaning. If I survives one
iteration it survives all; at exit B is false, so you exit holding
I ∧ ¬B. The sum loop:

```
i := 0;  s := 0;
while i < n do
    s := s + a[i];
    i := i + 1
od
```

Invariant: **I ≡ s = Σ_{k=0}^{i−1} a[k] ∧ 0 ≤ i ≤ n** — "s is the sum of
the first i elements, i in range." Three obligations, each a first-order
formula checkable by eye:

1. **Initiation** — the initialization establishes I:
   `i = 0 ∧ s = 0 ∧ n ≥ 0 → (s = Σ_{k=0}^{−1} a[k] ∧ 0 ≤ 0 ≤ n)`.
   The sum of zero elements is 0: s = 0 = the empty sum.
2. **Consecution** — one iteration preserves I (the assignment rule, run
   backward through both statements):
   `(s = Σ_{k<i} a[k] ∧ 0 ≤ i < n) → (s + a[i] = Σ_{k<i+1} a[k] ∧ 0 ≤ i+1 ≤ n)`.
   Load-bearing fact: `Σ_{k=0}^{i} = Σ_{k=0}^{i−1} + a[i]` — the recursive
   definition of Σ; the guard supplies i+1 ≤ n. Check it by eye.
3. **Termination** — partial correctness permits infinite loops, so add a
   **variant** (measure) v = n − i: `(I ∧ i < n) → (0 ≤ v_after < v_before)`
   with v_after = v−1. The guard gives v ≥ 1, each iteration decrements it
   by 1, ℕ is well-founded — the loop terminates.

At exit: I ∧ ¬(i < n) = `s = Σ_{k<n} a[k] ∧ i = n`. Initiation +
consecution + termination = the ∀-claim: every execution respecting the
precondition ends with the total sum. seL4 is thousands of these
(chapter 03).

## The weakest precondition: a program-to-formula compiler

Run those rules through sequences and conditionals and you get the
**weakest precondition** calculus — wp(S, Q), the weakest formula
guaranteeing Q after S:

- `wp(x := E, Q) = Q[x := E]` (the rule you just derived)
- `wp(S1; S2, Q) = wp(S1, wp(S2, Q))` (compose backward)
- `wp(if B then S1 else S2, Q) = (B → wp(S1,Q)) ∧ (¬B → wp(S2,Q))`

By hand on a five-line program, postcondition Q = `y > 1`, integers:

```
1  y := x + 1;
2  y := y * 2;
3  if y > 4 then
4      y := y - 1
5  else x := 0
```

Work from the bottom of the program upward:

- Line 5, `x := 0`: Q[x:=0] = `y > 1`.
- Line 4, `y := y−1`: (y>1)[y:=y−1] = `y > 2`.
- Line 3, the if: `(y>4 → y>2) ∧ (y≤4 → y>1)` ≡ `y > 1` (the first
  conjunct is valid).
- Line 2, `y := y*2`: (y>1)[y:=2y] = 2y > 1, i.e. `y ≥ 1` over ℤ.
- Line 1, `y := x+1`: (y≥1)[y:=x+1] = x+1 ≥ 1 = **`x ≥ 0`**.

The whole program, proved, collapsed to a two-character formula — and note
*how*: you walked the control flow in a fixed order, emitting one formula
per construct, exactly like a compiler's backend pass except the emission
is *predicates* instead of *instructions* (the front half is module 10
ch01's frontend: same AST, same CFG, same walk order):

```
                source program  (one AST / one CFG)
                        |
        +---------------+--------------------+
        |                                    |
   COMPILER (module 10)              VERIFIER (this module)
   walks the graph,                  walks the SAME graph,
   emits code                         emits formulas
        |                                    |
        v                                    v
   load/store/add/jmp               wp / verification conditions
   machine executes them            SMT solver / kernel checks them
        |                                    |
        v                                    v
   "the program ran"                "the program is proved"
   (one run = one point)            (checked = all runs)
```

## Soundness vs completeness: the halting problem picks your side

Two properties you must not confuse:

- **Soundness** — everything the tool *accepts* is true; it never certifies
  a false proof. The *trust* question.
- **Completeness** — everything *true* the tool can prove. The *coverage*
  question.

For programs, completeness is not on the menu: a complete verifier for any
non-trivial ∀-property decides the halting problem (module 19 ch01), so
every real tool is incomplete. Soundness is fully achievable, and the
field's position is forced and uniform across Coq, Isabelle, Dafny,
Frama-C: **sound by construction, incomplete by necessity**. You buy a
tool that may say "no" but never lies. A failed verification is not a
bug report, and any tool marketing "fully automatic, proves everything"
has quietly moved to a decidable fragment (chapter 04 shows the Rust
borrow checker is one).

## The LCF kernel: trust measured in lines

Everything so far produced formulas; now the machine that consumes them.
Milner's 1972 Stanford LCF design: stop trying to make the *prover*
trustworthy (it searches, backtracks, runs for hours — unverifiable); make
**theorem an abstract data type**. The `thm` type has *no public
constructor*; only the kernel's rules (assume, reflexivity, modus ponens,
instantiation, ...) return a `thm`. Any program, however buggy or
adversarial, can only compute theorems the rules derive — a broken tactic
wastes CPU, it cannot forge `0 = 1`. Milner built a language whose type
system enforces this: **ML**, the meta-language of LCF (Edinburgh LCF,
1978 — abstract types, closures, exceptions for failed tactics, all for
this).

The field's memory-model insight: the assistant is a trust kernel in
memory, and the TCB — module 19 ch05's concept — becomes a line count:

| System | Kernel size (order of, version-dependent) | What you must trust |
|---|---|---|
| HOL Light | ~400 lines of OCaml | the core inference rules |
| Isabelle/Pure | ~2,500 lines of ML | rules + term representation |
| HOL4 (classic HOL) | ~5,000 lines | the LCF discipline, grown |
| Coq | ~30,000 lines of OCaml | rules + guard/universe checks |
| (for scale) gcc | ~15,000,000 lines | everything, all passes |

A kernel bug matters like a compiler bug, but the surface is 2,500 lines
you can read in a week versus 15 million nobody has read: you don't trust
the prover, you trust the checker — and you make the checker tiny.

**The de Bruijn criterion** names the discipline: keep the checking kernel
small, and make the proof object independent of the prover — proofs as
files, checkable offline by anyone's small checker; the kernel's output is
a **certificate**, evidence that travels with the claim. The TLA+/TLC vs
Coq/Isabelle split: TLC (chapter 02) is a *checker* — fully automatic, no
proof object; you trust its ~50k lines of Java, and it only checks a
finite model. The assistants are incomplete but tiny-TCB.
Automatic-and-bounded vs manual-and-unbounded — an engineering decision,
not a law.

**The assistant as a memory artifact.** A proof in progress is a *state*:
a stack of unproved goals, each a formula plus its assumption context; a
tactic is a state transformer — pop, split, push. A proof is a search
through goal states, the same shape as a debugger's call stack. A Coq
session as snapshots:

```coq
Theorem plus_0_r : forall n : nat, n + 0 = n.
Proof.
  intros n.  induction n as [| n' IH].
  - simpl. reflexivity.
  - simpl. rewrite IH. reflexivity.
Qed.
```

```
after `intros n`:            after `induction n as [|n' IH]`:
+-----------------------+    +-----------------------------------+
| forall-bound: n:nat   |    | goal 2/2: S n' + 0 = S n'         |
|   goal: n + 0 = n     |    |   ctx: n':nat, IH: n' + 0 = n'    |
+-----------------------+    +-----------------------------------+
                             | goal 1/1: 0 + 0 = 0               |
                             |   ctx: (no assumptions)           |
                             +-----------------------------------+
```

`simpl` reduces the top goal, `rewrite IH` rewrites by the hypothesis,
`reflexivity` closes a t = t goal, popping the stack. When the stack
empties, `Qed`: the tactic script is *discarded*, and the kernel checks
the **proof term** — a λ-term whose type is the theorem statement
(Curry-Howard, module 19 ch02, now load-bearing: the proof *is* a program;
`plus_0_r`'s term is `nat_ind`, the recursion principle of `nat`, applied
to motive, base and step). The kernel type-checks it against the
`Prop`/universe discipline and seals the theorem. Tactic-layer bugs are
irrelevant: tactics never touch the `thm` constructor; they *propose*
terms, the kernel disposes.

**Proof-carrying code: the identification you already run.** Necula &
Lee (1996): untrusted code ships *with a proof* of a safety policy; the
consumer checks before executing. The JVM (module 01 ch12), component by
component:

- the class file = program **plus** type annotations; the StackMapTable
  (mandatory since Java 7) is the **certificate** — per-branch snapshots of
  operand-stack and local types;
- the bytecode verifier = the **small checker**: module 01 ch12's one
  *linear abstract interpretation* of the operand stack — a single pass
  over the certificate, the proof work already done by the (untrusted)
  javac;
- the theorem = a per-method **type-safety proof**: no instruction pops a
  type the stack doesn't hold, so the JIT may compile assuming no type
  confusion — memory safety is a checked theorem, not a runtime guard;
- the JIT = full-speed execution *after* the check: the entire PCC payoff.

LCF's architecture, one level down. You have been running a proof checker
every time you ran Java — verification machinery is load-bearing
infrastructure you already depend on.

## Specifications: the program 3× longer than the program

The formulas above were toy postconditions. Real specification languages —
**JML** for Java, **ACSL** for C (Frama-C), **Dafny's** built-in contracts
— attach pre/postconditions and loop invariants; the verifier emits the
obligations we derived by hand. Binary search, fully specified in Dafny:

```dafny
method BinarySearch(a: array<int>, target: int) returns (r: int)
  requires forall i, j :: 0 <= i < j < a.Length ==> a[i] <= a[j]
  ensures 0 <= r < a.Length ==> a[r] == target
  ensures r < 0 ==> forall k :: 0 <= k < a.Length ==> a[k] != target
{
  var lo, hi := 0, a.Length;
  while lo < hi
    invariant 0 <= lo <= hi <= a.Length
    invariant forall k :: 0 <= k < lo ==> a[k] < target
    invariant forall k :: hi <= k < a.Length ==> a[k] > target
    decreases hi - lo
  {
    var mid := lo + (hi - lo) / 2;
    if a[mid] < target { lo := mid + 1; }
    else if a[mid] > target { hi := mid; }
    else { return mid; }
  }
  return -1;
}
```

Count it: three lines of logic, ten of contract — **that is normal**. The
`invariant` lines are the sum-loop discipline verbatim; `requires` is the
caller's obligation, `ensures` the callee's promise, `decreases` the
variant. The honest economics:

| What you write | Size vs code | Gets you |
|---|---|---|
| Types (module 19) | ~0.1–0.3× | memory safety, weak properties |
| Defensive contracts (pre/post) | ~0.5–1× | API discipline |
| Full functional spec + invariants | 1–5× | the behavior, ∀-shaped |
| Machine-checked proof of it | 10–40× | seL4: 400k proof lines / 10k C |

Porting note: Dafny's `int` is mathematical — the classic `(lo+hi)/2`
overflow can't happen — but port that shape to C and the ACSL spec must
carry `lo <= hi` bounds into the arithmetic proofs.

## What insiders know

- **The escape hatches, and the CI rule.** Coq's `admit`/`Admitted`,
  Isabelle's `sorry`, Lean's `sorry`, Dafny's `assume`: every assistant
  ships an axiom-shaped back door for work-in-progress. Teams that ship
  proofs fail CI on `git grep -nE 'sorry|admit|Admitted|assume'` and
  ticket survivors as *proof debt*. `sorry` compiles to an axiom: the
  "theorem" is a hypothesis from that point.
- **"Machine-checked" ≠ "the spec was right."** The kernel certifies
  P ⊢ Q; nothing certifies that Q was what the customer wanted. The VDM-era
  lesson (the Vienna conference case study): a system delivered correct
  *with respect to its specification*, where the spec itself was the bug —
  a requirement-level error no kernel can catch. Review specs like code.
- **The kernel-bug list is short, and that's the argument.** 2000–2020
  produced roughly a handful of real soundness bugs in the major kernels —
  Isabelle's axiomatic-type-class interaction (~2002), Coq guard-checker
  escapes admitting false fixpoints (several, 2018–2021). Each was an
  *implementation* bug in a rule, found by review or adversarial testing,
  fixed in days; none shipped a false theorem. Small TCB = short,
  reviewable incident list — and once fixed, every old proof re-checks
  from its stored term.
- **The SAT-solver-in-the-kernel debate.** Solvers (Z3: ~500k lines of
  C++) would collapse the TCB if trusted. Two live answers: *certificates*
  — proof logs (DRAT for SAT, SMT certificates) re-checked by small,
  sometimes verified checkers — and *reflection* — verified checker code
  running *inside* the logic (SMTCoq: the external solver becomes an
  untrusted hint generator). Still research-grade in places; ask which
  pattern a tool uses before counting its TCB.
- **`coqchk` exists because the de Bruijn criterion is load-bearing.**
  `.vo` files store proof *terms*, not scripts; `coqchk` re-typechecks a
  whole development without loading any tactic code — the artifact must
  survive the checker alone.
- **The `thm` discipline has one classic exception: axioms.** `Axiom` in
  Coq, `axiomatization` in Isabelle add rules with no derivation; `Print
  Assumptions` lists what a "theorem" secretly rests on. seL4's proof
  runs axiom-light; some crypto proofs run axiom-heavy. The list is the
  honesty.

## See it yourself

Labs verified on z3-solver 5.1.0, Python 3.12.

**Lab 1 — Z3 from Python.** `pip install z3-solver`; primality as an
UNSAT question ("no nontrivial divisor exists"), with the unsat core as
the explanation:

```python
from z3 import *

n = 13
d = Int('d')                  # a hypothetical nontrivial divisor
a1, a2, a3, a4 = Bools('a1 a2 a3 a4')
s = Solver()
s.set(unsat_core=True)        # track refutations

s.add(Implies(a1, d > 1))     # a1: nontrivial (>1)
s.add(Implies(a2, d < n))     # a2: proper (<n)
s.add(Implies(a3, n % d == 0))# a3: divides n
s.add(Implies(a4, d * 2 == d * 2))  # a4: filler, useless

print(s.check(a1, a2, a3, a4))  # -> unsat  == 13 is prime
print(s.unsat_core())           # -> [a1, a2, a3]   (a4 dropped)
print(s.check(a1, a3))          # -> sat, d = 13 (drop a2: trivial divisor)
```

Verified output: `unsat`, `[a1, a2, a3]`, then `sat`, `d = 13`. UNSAT
says the ∀-claim holds; the core names the load-bearing hypotheses; the
dropped `a4` was irrelevant. Set `n = 15` — `sat`, `d = 3`: a
counterexample, the testing quantifier.

**Lab 2 — the sum-loop invariant as an SMT implication.** Encode
consecution: prove `I ∧ B → I'` by refuting its negation; the running sum
is axiomatized by its recurrence — which *is* the definition of Σ:

```python
from z3 import *

A = Function('A', IntSort(), IntSort())  # A(k) = a[k]
S = Function('S', IntSort(), IntSort())  # S(i) = sum of a[0..i)
s, i, n = Ints('s i n')
k = Int('k')

slv = Solver()
slv.add(S(0) == 0)                        # empty sum = 0
slv.add(ForAll(k, Implies(k >= 0,
        S(k + 1) == S(k) + A(k))))        # sigma's recurrence

# consecution, NEGATED: the body maps I /\ B to I
slv.add(Not(Implies(And(s == S(i), 0 <= i, i <= n, i < n),
                    And(s + A(i) == S(i + 1), i + 1 <= n))))
print("consecution:", slv.check())        # unsat == obligation holds
```

Output (verified): `consecution: unsat`; initiation likewise. The
termination measure (v = n−i) needs induction over well-founded ℕ — the
boundary between chapter 02's automatic world and the assistant world.

**Lab 3 — one Dafny method (or the paper version).** With .NET
(`dotnet tool install -g dafny`), save the binary search as `bsearch.dfy`,
run `dafny bsearch.dfy` — expect `Dafny program verifier finished with
0 verified, 0 errors`. Then break it: `lo := mid + 1` → `lo := mid` and
rerun — Dafny blames the `decreases hi - lo` clause (when `hi−lo = 1`,
`mid = lo`, the window stops shrinking): the off-by-one found by proof
obligation, not by testing. No .NET? Write the three while-rule
obligations for the invariants above by hand and find the failing one.

**Lab 4 — touch the proof object.** With Coq (`apt install coq` or opam):

```coq
Theorem two : 1 + 1 = 2.
Proof. reflexivity. Qed.
Print two.
Print Assumptions two.
```

Expected: `two = eq_refl : 1 + 1 = 2` — all the evidence is one λ-term
(`eq_refl`), re-checkable by `coqchk` without the tactic machinery.
`Print Assumptions two` → `Closed under the global context`: zero axioms.
Run it on any "big" theorem you meet.

## How to use this knowledge

- **Read verification claims as TCB questions.** Any "machine-checked"
  result — seL4, CompCert, verified crypto — is a conditional: sound kernel
  + stated axioms + given spec. Ask which of the three is doing the work;
  `Print Assumptions` is the confession mechanism.
- **Annotate loops you review.** If you can state what is true at the top
  of every iteration, boundary and termination bugs surface on paper —
  the `lo := mid` class dies at `decreases` before any test run.
- **Debug with wp.** Compute the weakest precondition of the *intended*
  postcondition and compare with what the entry code establishes — the
  divergence is the bug's location, derived not guessed.
- **Interview-grade answers.** "What does seL4's proof guarantee?" — a
  sound, incomplete checker certified an executable spec against a kernel
  model; the TCB is a ~2,500-line kernel plus the spec; the 100:1 ratio
  is the price, not the product.

## Connections

- **Module 19 ch02** — Curry-Howard load-bearing: the proof term *is* a
  λ-term, `Qed` *is* type-checking; the easy instance generalized.
- **Module 01 ch12** — the JVM bytecode verifier as the LCF-style kernel:
  class file = certificate, StackMapTable = proof term, check-then-JIT =
  proof-carrying code.
- **Module 10 ch01** — the compiler frontend: wp/VC generation is the
  same CFG traversal with a formula backend.
- **Module 19 ch05** — the TCB concept, returning as a line count.
- **Module 19 ch01** — undecidability: why the sound/incomplete position
  is forced.
- **Module 21 ch02** — model checking: brute-force ∀ over finite state,
  memory budget as the frontier.
- **Module 21 ch03** — these economics at full size: seL4's 40:1,
  person-years.
- **Module 21 ch04** — the borrow checker as a sound, *decidable*
  fragment: the completeness sacrifice made concrete.

## Quiz

1. **Concept.** "The suite is green, that's proven." State what the suite
   and what a proof establish, as quantified formulas, and name the one
   thing a suite can do that a sound incomplete prover cannot.
2. **Concept.** Why does the assignment rule substitute into the
   *postcondition*? What property of assignment makes backward the only
   mechanical direction?
3. **Predict/derive.** Compute the weakest precondition by hand, showing
   every intermediate formula, for postcondition `y > 2` (integers):
   ```
   1  x := x + 2;
   2  x := x - 1;
   3  if x > 3 then
   4      y := x - 1
   5  else y := x + 1
   ```
4. **Predict/derive.** A colleague proposes `I_w ≡ 0 ≤ i ≤ n` for the sum
   loop. Check the three obligations for `I_w`, state what the while rule
   lets you conclude at exit, and why `s = Σ_{k<n} a[k]` does not follow.
5. **Predict/derive.** Derived precondition of each: (a) `{ ? } x := 7
   { x mod 2 = 1 }`, (b) `{ ? } x := x - 1 { x > 0 }`,
   (c) `{ ? } x := y { x = 3 }`.
6. **Diagnose.** Dafny rejects the binary search after one edit:
   `lo := mid + 1` became `lo := mid`; the report blames the `decreases
   hi - lo` clause, not any invariant. Name the execution that kills the
   measure (window size, what `mid` equals) and why the invariants survive.
7. **Diagnose.** A Dafny-verified `BinarySearch` returns a wrong index in
   production: kernel sound, no `assume`/`sorry`, CI grep clean, code
   byte-identical to the verified artifact. Give two failure modes — one
   at the contract boundary, one at the spec level.
8. **Cross-chapter.** Map the JVM's load-time machinery (module 01 ch12)
   onto the LCF architecture: (a) the certificate, (b) the kernel, (c) the
   theorem, (d) the untrusted producer — and why the JIT needs no
   per-instruction type re-checking.

<details>
<summary>Answers</summary>

1. A suite establishes a finite disjunction of *points*: ∃x₁...∃xₖ (k =
   tests) with program(xᵢ) correct. A proof of {P} S {Q} establishes
   ∀s (P(s) → Q(final(s))) — every input, including ones no test author
   imagined. The suite can *refute*: a failing test is a concrete witness
   of ¬∀, which a sound incomplete prover is not obligated to produce.
2. Assignment is deterministic: the final state is a *function* of the
   initial state (x' = E, rest fixed), so any predicate on the final state
   pulls back to a unique weakest precondition by substitution. Forward,
   no single best postcondition exists — choosing one loses the old x.
3. Line 4: (y>2)[y:=x−1] = x > 3. Line 5: (y>2)[y:=x+1] = x > 1. Line 3:
   (x>3 → x>3) ∧ (x≤3 → x>1) ≡ x > 1. Line 2: (x>1)[x:=x−1] = x > 2.
   Line 1: (x>2)[x:=x+2] = x > 0 — over ℤ, **`x ≥ 1`**.
4. All three hold: initiation (0 ≤ 0 ≤ n), consecution (i < n gives
   0 ≤ i+1 ≤ n), termination (v = n−i). But the while rule hands back
   *your own* invariant: I_w ∧ ¬(i<n) = `i = n`, nothing about s. `s = Σ`
   was never in I_w, so it cannot appear in the conclusion.
5. (a) 7 mod 2 = 1 ≡ **True** — manufactured. (b) **x > 1** — shifted
   upstream by one. (c) **y = 3** — the claim jumps to the variable
   copied in.
6. With `hi − lo = 1`: `mid = lo + 0 = lo`. If a[mid] < target, `lo := mid`
   assigns lo := lo — window unchanged, v stuck at 1: an infinite loop.
   The invariants survive because they only classify the *unexamined*
   regions, and a[mid] < target keeps "all below lo are < target" true.
   Preserved invariant + broken measure = partial correctness without
   termination, hence the `decreases` failure alone.
7. Contract boundary: the proof is conditional on `requires sorted(a)` —
   a proof-time assumption checked nowhere at runtime; a caller passing
   an unsorted array gets garbage-out from a "verified" component. Spec
   level: machine-checked ≠ the spec was right — if `ensures` formalizes
   the wrong behavior, the kernel certifies a theorem about a spec nobody
   wanted (the VDM lesson).
8. (a) Certificate: StackMapTable frames — operand-stack/local types at
   branch targets (Java 7+). (b) Kernel: the bytecode verifier, one linear
   abstract-interpretation pass against the frames. (c) Theorem:
   per-method type safety — nothing pops a type the stack doesn't hold;
   memory safety follows from the JVM type discipline. (d) Producer:
   javac or any bytecode generator. The JIT skips re-checking because the
   theorem was proven once, at load time — the PCC payoff.

</details>
