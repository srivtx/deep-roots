# Chapter 02 — Types as theorems, programs as proofs

> **The 30-second version.** Chapter 01 built the bare machine — and, in the
> same box, programs that get stuck or diverge. A type system is a **proof,
> checkable by recursion on syntax, that evaluation stays inside a set of
> values**. Two theorems define every typed language (Wright–Felleisen):
> *progress* — a well-typed closed term is a value or can step — and
> *preservation* — stepping keeps the type; together, well-typed programs
> never get stuck. Read types as propositions, programs as proofs
> (Curry–Howard): `null` a soundness lie, generics free theorems, variance a
> derivation, `any` a proof pause button.

## The reframe: a proof you can check by recursion

Chapter 01's calculus evaluates until it hits a value or a **stuck term** —
`true true`, which is neither; untyped runtimes die there. A type system
rejects, *before evaluation*, every term that could end stuck — not by
running it (halting problem) but by checking a proof the result lives in a
value set. The proof language: STLC with booleans and unit. Grammar, one line
each:

```text
t ::= x | λx:T.t | t₁ t₂ | true | false | if t₁ then t₂ else t₃ | unit | t₁;t₂
T ::= Bool | Unit | T₁ → T₂
```

A judgment `Γ ⊢ t : T` reads "under assumptions Γ (a list of `x:T` pairs in
scope), `t` is evidence for `T`". Rules have names — typecheckers report
errors by them. Eight rules:

```text
+--------+--------------------------------------+-------------------------------+
| Rule   | Premises                             | Conclusion                    |
+--------+--------------------------------------+-------------------------------+
| T-Var  | x:T is in Γ                          | Γ ⊢ x : T                     |
| T-Abs  | Γ, x:T₁ ⊢ t : T₂                     | Γ ⊢ λx:T₁.t : T₁→T₂           |
| T-App  | Γ ⊢ t₁ : T₁→T₂   and   Γ ⊢ t₂ : T₁   | Γ ⊢ t₁ t₂ : T₂                |
| T-True | (none)                               | Γ ⊢ true : Bool               |
| T-False| (none)                               | Γ ⊢ false : Bool              |
| T-If   | Γ ⊢ t₁:Bool,  Γ ⊢ t₂:T,  Γ ⊢ t₃:T   | Γ ⊢ if t₁ then t₂ else t₃ : T |
| T-Unit | (none)                               | Γ ⊢ unit : Unit               |
| T-Seq  | Γ ⊢ t₁:Unit,  Γ ⊢ t₂:T              | Γ ⊢ t₁;t₂ : T                 |
+--------+--------------------------------------+-------------------------------+
```

(`t₁;t₂` is sugar for `(λx:Unit.t₂) t₁`, so T-Seq is the derived rule; its
evaluation is β in disguise, which is how the work-rule count stays three.)
Evaluation: three work rules, plus congruence ("reduce the leftmost
non-value subterm"):

```text
if true  then t₂ else t₃  →  t₂        (E-IfTrue)
if false then t₂ else t₃  →  t₃        (E-IfFalse)
(λx:T.t) v                →  t[x:=v]   (E-AppAbs — β, on a value argument)
```

The same term in two universes: `(λx.x x) true`. Untyped: β fires and it
becomes `true true` — stuck. In STLC, `λx:Bool.x x` demands `x : Bool` and
`x : Bool→T` at once; no finite type satisfies that, so it is rejected
*before it runs* — in the lab, with the rule name printed. Self-application
— the Y combinator's engine — is what STLC forbids, which is why all STLC
programs terminate: the first point on the expressiveness-versus-guarantees
frontier (chapter 05 returns).

## Wright–Felleisen: the two theorems every typed language stands on

Type soundness is not a vibe; it is one theorem with two halves (Wright and
Felleisen, "A Syntactic Approach to Type Soundness", 1994), for closed terms:

- **Progress.** If `⊢ t : T` then `t` is a value or `t → t'` for some `t'`.
- **Preservation.** If `⊢ t : T` and `t → t'` then `⊢ t' : T`.

**Progress** (sketch — the lab runs the cases). Induction on the typing
derivation, case on the last rule: T-True/T-False/T-Unit/T-Abs conclude in
values; T-Var is impossible (empty Γ). T-App: the induction hypothesis makes
each operand a value or stepping; if both are values, canonical forms — a
closed value of `Bool` is `true` or `false`, of `T₁→T₂` is a `λ` — make the
left operand `λx:T₁.s` with a value argument, and E-AppAbs fires. T-If: the
condition is `true` or `false`; E-IfTrue/E-IfFalse fires. T-Seq: the first
component is `unit`; the desugaring's β fires. No dead ends.

**Preservation, application case, in full.** Given `⊢ t₁ t₂ : T₁₂` by T-App,
with premise derivations **D₁**: `⊢ t₁ : T₁₁→T₁₂` and **D₂**: `⊢ t₂ : T₁₁`.
Suppose `t₁ t₂ → t'`. Three subcases:

```text
* E-AppAbs: t₁ = λx:T₁₁.s is a value. Invert D₁ (only T-Abs types an
  abstraction): its binder is T₁₁ and  ⊢ s : T₁₂  in context  x:T₁₁.
  D₂ says ⊢ t₂ : T₁₁, so the substitution lemma (if Γ,x:S ⊢ u:T and
  Γ ⊢ v:S then Γ ⊢ u[x:=v]:T — proved by induction on u) gives
  ⊢ s[x:=t₂] : T₁₂.  Preserved.
* E-App1 (congruence): t₁ → t₁'.  IH on D₁:  ⊢ t₁' : T₁₁→T₁₂.
  Re-apply T-App with D₂:  ⊢ t₁' t₂ : T₁₂.  Preserved.
* E-App2 (congruence): t₂ → t₂'.  IH on D₂:  ⊢ t₂' : T₁₁.
  Re-apply T-App with D₁:  ⊢ t₁ t₂' : T₁₂.  Preserved.
```

The other cases are easier; any feature that makes this induction fail is
unsound — the rest of the chapter is case studies of that sentence.

**Corollary (soundness).** If `⊢ t : T` and `t →* v` then `v : T`, and no
intermediate state is stuck — preservation keeps every state well-typed,
progress keeps every non-value stepping. This is Milner's 1978 slogan
"well-typed programs cannot go wrong", where **"go wrong" means stuck**,
nothing more.

## Curry–Howard: the proof you compiled

Write `→` as implication and the 8 rules *are* natural deduction. The
Curry–Howard correspondence is not an analogy; it is a bijection you can check
by hand:

```text
+-----------------------------+---------------------------+--------------------------+------------------------+
| Type theory                 | Logic                     | Reading                  | Example                |
+-----------------------------+---------------------------+--------------------------+------------------------+
| type A                      | proposition A             | a claim                  | Bool                   |
| term t : A                  | proof of A                | the evidence             | true                   |
| λ-abstraction               | →-introduction            | discharge an assumption  | λa:A.λb:B.a            |
| application t₁ t₂           | →-elimination             | modus ponens             | (λx:Bool.x) true       |
| β-reduction                 | proof simplification      | cut elimination          | (λx.x) true → true     |
| inhabited type (has a term) | provable proposition      | a closed proof exists    | Bool, by true          |
| uninhabited type            | contradiction             | no proof exists          | never                  |
+-----------------------------+---------------------------+--------------------------+------------------------+
```

The worked example: `A → B → A`, logic's weakening axiom. Its proof term is
`λa.λb.a` (the K combinator of chapter 01's SKI set). Check by hand — typing
left, natural deduction right, the *same tree*:

```text
   a:A, b:B ⊢ a : A      (T-Var)          A, B ⊢ A        (assumption)
   a:A ⊢ λb:B.a : B→A    (T-Abs)          A ⊢ B⊃A         (→-intro, discharge B)
   ⊢ λa:A.λb:B.a         (T-Abs)          ⊢ A⊃B⊃A         (→-intro, discharge A)
      : A→B→A
```

The term is the proof; the type is the theorem. β-reduction *is* proof
simplification — `(λa:A.λb:B.a) x y → x` eliminates a detour. An inhabited
type is provable (`Bool` by `true`, `A→A` by `λx.x`); an uninhabited one is a
contradiction. The rest of the chapter asks: *which proposition is your
language actually proving?*

## null: the type that lied (the billion-dollar soundness hole)

Tony Hoare, QCon 2009, on inventing the null reference in 1965: "I call it my
billion-dollar mistake... I couldn't resist the temptation to put in a null
reference, simply because it was so easy to implement. This has led to
innumerable errors, vulnerabilities, and system crashes, which have probably
caused a billion dollars of pain and damage in the last forty years."

The mistake, type-theoretically, is exact: `Γ ⊢ e : Foo` promises "e evaluates
to one of Foo's values", but `null` inhabits *every* reference type — `null :
Foo` for all `Foo` — so **every proposition became provable with evidence
that proves nothing**, and a system where every claim is provable proves
nothing. The `NullPointerException` at `e.field` is the runtime noticing,
late, a forgery: progress violated, `e.field` stuck.

The honest repair is a sum type: `Option<Foo>` / `Maybe Foo` makes the claim
explicit — *value or absent* — and the compiler enforces the case analysis: no
payload read without handling `None`. The cost is module 01 chapter 06's
tagged-value story re-appearing: a discriminant. Rust's niche optimization
pays it only when a bit pattern is spare: `Option<&T>` is 8 bytes (same as
`&T` — the null-pointer pattern is reserved as `None`); `Option<i32>` is 8
where `i32` is 4 (all 2³² patterns are legal, so a real tag is unavoidable).

Two epilogues. **C# 8 (2019) nullable reference types:** `string?` is a
*compile-time* annotation with flow analysis, retrofitted onto ~20 years of
code that assumed the lie; the migration (per-file opt-in, warnings first,
`#nullable enable`) is what retrofitting honesty looks like (module 01 ch13).
**Kotlin platform types:** a Java `String` of unknown nullability arrives as
`String!` — *neither checked nor trusted*; assert `String` and the NPE
arrives at runtime. The `!` means "obligation suspended".

## Generics: System F and theorems for free

`λx:Bool.x` proves `Bool→Bool` and nothing else. System F (Girard/Reynolds,
1972/74) adds a type-abstraction binder `Λ`: `Λα.λx:α.x : ∀α.α→α` — one
proof, every type. Instantiation is application at the type level:
`(Λα.λx:α.x)[Bool]` becomes `λx:Bool.x`, then `(λx:Bool.x) true → true`.
Java's `<T>`, C#'s `<T>`, Rust's `<T>` are surface syntax over this —
inference writes the `Λ`s (chapter 01: why System F inference is undecidable).

The payoff is **parametricity** (Wadler, "Theorems for Free!", 1989): a term
`∀α.[α]→[α]` receives only the list — elements are opaque α. It cannot
inspect, compare, print, or construct an α; the only operations are
position-shaped, so the type alone proves a theorem about *every*
implementation. The classic: `reverse : ∀α.[α]→[α]` — derive from the type
alone that `map f (reverse xs) = reverse (map f xs)` for every `f`:

```text
1. reverse can only move positions; it never touches element values.
2. Relational parametricity: at every instantiation it moves them the SAME way.
   Take R = graph of f:  a R b  iff  b = f(a).
   Then xs : [A] and map f xs : [B] are R-related elementwise.
3. Parametricity forces reverse to map R-related inputs to R-related outputs:
       reverse xs  is elementwise-R-related to  reverse (map f xs).
   Elementwise R means each right element = f of the left one:
       reverse (map f xs) = map f (reverse xs).   ∎
```

`f` acts only on values, `reverse` only on positions; orthogonal axes
commute. Practical translation: a generic `Map<K,V>` API *cannot* secretly
require `K` to be a string — the type is a contract stronger than docs: docs
can lie, a parametric function cannot.

The bill for `∀` arrives at code generation — two ways to pay:

```text
+---------------------+---------------------------+--------------------------+---------------------------+
|                     | Java (erasure)            | C++ (templates)          | Rust (monomorphize)       |
+---------------------+---------------------------+--------------------------+---------------------------+
| Layout of T         | 1 shared Object header    | one layout per T         | one layout per T          |
|                     | (12-16 B, boxed; no int)  | (primitives stay flat)   | (primitives stay flat)    |
| Code                | 1 shared bytecode copy    | 1 copy per T per TU      | 1 copy per concrete T     |
| Check / price       | insert-time cast; boxing, | compile time; code bloat | compile time; link size,  |
|                     | no List<int>              | (.i explosion)           | dyn escape                |
+---------------------+---------------------------+--------------------------+---------------------------+
```

Java chose erasure in 2004 for binary compatibility with 1995 bytecode — the
element-type tag is module 01 ch12/13's object header doing double duty. C++
and Rust stamp out a copy per instantiation (`Vec<T>` once per concrete `T`;
`dyn Trait` buys it back with one vtable call). Module 10 ch05 covers where
the choice is enforced.

## Subtyping, variance, effects, and the soft hole

**Width subtyping** is safe: `{x, y} <: {x}` — more fields supplies everything
a narrower consumer needs. **Depth** is where it bites: `{m:{a,b}}` <:
`{m:{a}}` is safe for *reads*, unsound for *writes*: alias `Employee
{birthday: RichDate}` as `Person {birthday: Date}`, store a plain `Date`
through it, and the RichDate-only method called later detonates. Mutable
fields make covariance a lie; function types force the rule:

```text
Use f : T₁→T₂ where S₁→S₂ is expected. Callers will pass s:S₁:
  f must accept every S₁ value:          S₁ <: T₁     (argument: contravariant)
  f returns T₂, caller needs S₂:         T₂ <: S₂     (result: covariant)
∴ S₁→S₂ <: T₁→T₂  iff  T₁ <: S₁ and T₂ <: S₂ — inputs flip, outputs don't.
```

Three lines, and Java's most famous runtime exception falls out of ignoring
them. Java arrays are covariant (`String[] <: Object[]`, a 1995 pre-generics
decision so `Arrays.sort(Object[])` could take a `String[]`), so writes
through the wider alias are unsound — and every store into a reference array
carries a runtime element-type check: `Object[] o = new String[2]; o[0] =
Integer.valueOf(42);` throws `ArrayStoreException` — *the static type system
running late*: proof skipped at compile time, bought at every store. Sound
(it fails loudly), but `Object[]` lied. C#'s fix: .NET 4 (2010) added
*declared* variance on output-only surfaces — `IEnumerable<out T>` is
covariant because nothing in it consumes a `T`, so there is no store to
check; arrays stay covariant only for history (`ArrayTypeMismatchException`).

**Effects are the same game on "may do" claims.** Java's `IOException` is a
checked effect — the method's type carries an obligation the caller must
discharge — while `NullPointerException` ("may go stuck on null") is
unchecked. Which is which is a *design opinion* from 1995, not a law;
Hejlsberg's 2003 case against checked exceptions was that the obligation
doesn't scale across components. The frontier is effect types proper: Koka's
and Roc's *effect rows* put a function's permitted effects in its type.

**Gradual typing is the compromise: unsoundness with a receipt.** TypeScript
is deliberately unsound, and the holes are enumerable: `any` (assignable both
ways to everything, and contagious), type assertions (`x as T` — a promise,
not a proof), covariant mutable arrays, method bivariance, the non-null `!`.
Internalize: **`any` is a proof pause button** — the theorem still holds
everywhere else, and the paused regions are greppable. A hole you can list
is a different animal from one you can't; `null` was the second kind, and
the lab catches all four of TS's.

## What insiders know

- **`void` in C is not the unit type.** `unit` has exactly one value (`()`,
  storable); C `void` has none — `void f()` returns *control* without a
  value, but you cannot hold a `void`, and a never-returning function
  (`exit`, `while(1)`) has the third type, bottom. C conflates all three;
  Rust keeps `()` and `!` apart.
- **TypeScript is structural, but fresh object literals get the excess
  property check** — `{name: "x", emial: "y"}` is rejected when the target has
  `email`, *only* at literal creation; a non-literal object with extra fields
  sails through. A typo-detector bolted onto a structural system.
- **`never` is the uninhabited proposition**, the type of `throw` and infinite
  loops — TS uses it for exhaustiveness: a `switch` whose default assigns to
  `never` stops compiling the day someone adds a union member. Proof by
  contradiction doing API review.
- **Milner's quote is precise, and everyone misquotes it.** "Well-typed
  programs cannot go wrong" (1978) — "go wrong" is the technical term
  *stuck*. It promises nothing about integer overflow, wrong logic, or
  divergence; those are chapter 05.
- **`strictFunctionTypes` does not fix methods.** Function-type parameters are
  checked contravariantly, but method syntax stays bivariant so
  `Array<string>` stays assignable to looser interfaces — a documented,
  permanent unsoundness kept for usability.
- **Parametricity's fine print: Haskell's bottom.** Laziness gives every type
  a secret extra inhabitant `⊥` (`undefined`), so `∀a.a→a` has more terms than
  the identity — and `seq` can distinguish them, breaking some free theorems.
  The theorem is free; the purity it assumes is not.

## See it yourself

**Lab 1 — the four TS soundness holes, strict on and off.** Install once
(`npm install typescript`), then check the files both ways (or use
typescriptlang.org/play and flip `strict`):

```console
$ npx tsc --strict --noEmit hole1.ts hole2.ts hole3.ts hole4.ts
$ npx tsc --strict false --noEmit hole1.ts
```

One file per hole class — all four compile *clean* under `--strict`; that is
the point:

```ts
// hole1.ts — `any`: strict catches *implicit* any, never *explicit* any
function double(x) { return x * 2; }            // TS7006 under strict, nothing without
const e: any = JSON.parse('{"port": 8080}');
const port: number = e.potr * 2;                // typo'd key: NaN, no error either way

// hole2.ts — `as` asserts without proving
const first = (x: unknown): string => (x as string).slice(1);
console.log(first(42));          // TypeError: x.slice is not a function

// hole3.ts — mutable arrays are covariant (unsound by design)
const nums: number[] = [1, 2, 3];
const mixed: (number | string)[] = nums;        // accepted
mixed.push("hi");                                // alias write
console.log(nums[3].toFixed(2));                 // TypeError: n.toFixed is not a function

// hole4.ts — method parameters are checked bivariantly
class Grep { match(s: string): boolean { return s.length > 0; } }
interface Matcher { match(s: string | number): boolean; }
const m: Matcher = new Grep();                   // accepted
console.log(m.match(42));                        // silent false: (42).length is undefined
```

What to notice: strict mode's only complaint is `TS7006` on line 2 of hole1 —
the *explicit* `any`, the assertion, the array alias, and the method all pass
untouched. At runtime: `NaN`, two `TypeError`s, and one *silent wrong answer*
(hole4 — no crash, just `false`).

**Lab 2 — the STLC typechecker, 8 rules as ~60 lines of Python.** The
section-1 machinery, executable; rule names are the error messages:

```python
# stlc.py -- STLC with booleans and unit: the 8 typing rules as code.
# Terms (tuples):  ("var",x) ("abs",x,T,t) ("app",t1,t2) ("true",) ("false",)
#                  ("if",c,t,e) ("unit",) ("seq",t1,t2)
# Types: "Bool" | "Unit" | ("arrow",T1,T2)

def show(T):
    return T if isinstance(T, str) else f"{show(T[1])} -> {show(T[2])}"

def lookup(g, x):
    for k, T in reversed(g):
        if k == x:
            return T
    raise TypeError(f"[T-Var] unbound variable {x}")

def type_of(g, t):                       # g: list of (name, type)
    k = t[0]
    if k == "true":  return "Bool"       # T-True
    if k == "false": return "Bool"       # T-False
    if k == "unit":  return "Unit"       # T-Unit
    if k == "var":   return lookup(g, t[1])            # T-Var
    if k == "abs":                                    # T-Abs
        return ("arrow", t[2], type_of(g + [(t[1], t[2])], t[3]))
    if k == "app":                                    # T-App
        Tf = type_of(g, t[1])
        if not (isinstance(Tf, tuple) and Tf[0] == "arrow"):
            raise TypeError(f"[T-App] {show(Tf)} is not a function type")
        Ta = type_of(g, t[2])
        if Ta != Tf[1]:
            raise TypeError(f"[T-App] argument is {show(Ta)}, expected {show(Tf[1])}")
        return Tf[2]
    if k == "if":                                     # T-If
        Tc = type_of(g, t[1])
        if Tc != "Bool":
            raise TypeError(f"[T-If] condition is {show(Tc)}, expected Bool")
        Tt, Te = type_of(g, t[2]), type_of(g, t[3])
        if Tt != Te:
            raise TypeError(f"[T-If] branches differ: {show(Tt)} vs {show(Te)}")
        return Tt
    if k == "seq":                                    # T-Seq
        if type_of(g, t[1]) != "Unit":
            raise TypeError("[T-Seq] first component must be Unit")
        return type_of(g, t[2])
    raise TypeError(f"unknown form {k}")

# demo
ok = ("app", ("abs", "x", ("arrow", "Bool", "Bool"), ("app", ("var", "x"), ("true",))),
             ("abs", "y", "Bool", ("var", "y")))
print("well-typed term:", show(type_of([], ok)))          # Bool

bad = ("app", ("abs", "x", "Bool", ("app", ("var", "x"), ("var", "x"))), ("true",))
try:
    type_of([], bad)
except TypeError as e:
    print("rejected:", e)

seq = ("seq", ("unit",), ("true",))
print("seq term:", show(type_of([], seq)))                # Bool
```

```console
$ python3 stlc.py
well-typed term: Bool
rejected: [T-App] Bool is not a function type
seq term: Bool
```

The `bad` term *is* `(λx.x x) true` — the stuck term from section 1 —
rejected before running, with the rule that failed. Pair the checker with
chapter 01's interpreter: run the term untyped (steps once, then dies), then
ask why STLC refuses it.

## How to use this knowledge

- **Production bug triage.** When a runtime type error escapes a "typed"
  codebase, the stack trace points at the symptom; the proof break is at a
  hole operator. Grep `as `, `!`, `any`, `@ts-expect-error`, platform types —
  the theorem stopped being checked on one of those lines.
- **API design is theorem design.** Nullability annotations are claims the
  compiler holds you to. Make `Option`/`?` match reality in the public
  surface; for legacy code follow C# 8's retrofit: opt in per assembly,
  warnings before errors — never widen a hole to silence one.
- **Generics cost model.** Erasure vs monomorphization is why Java has no
  `List<int>` (boxed, 12–16 B header each) and why Rust binaries balloon with
  generic fan-out; reach for `dyn Trait` when instantiation count, not call
  speed, is the problem.
- **Variance review in one move.** Any *write* through a covariant alias is
  the `ArrayStoreException` pattern — reject it, or make the surface
  read-only (`out T`, `readonly` arrays) and say why. The interview-grade TS
  answer: "unsound by design; the holes are `any`, assertions, covariant
  arrays, method bivariance — usable, priced, greppable."

## Connections

- **19-01 (the minimal machine):** β, the Y combinator, Ω — and the exact
  rule (T-App's arrow demand) that untypable-izes self-application.
- **19-03 (memory safety as a theorem):** progress + preservation at the
  address level — a pointer's type is "what may live there".
- **19-04 / 19-05:** liveness as a theorem priced in pauses; what "cannot go
  wrong" does *not* mean — overflow, logic, termination.
- **Module 01 ch06 (object layout):** the tag byte of `Option`/sums and
  runtime type tags are these proofs' RAM certificates — `Option<&T>` at 8 B
  is the niche optimization.
- **Module 01 ch13 (C#/.NET):** nullable reference types and CLR generics —
  the industrial epilogue of null and erasure (repairs the module's orphaned
  C# link).
- **Module 16 ch02 (DOM heap graph):** the untyped object graph TS describes
  but never checks; where `any` meets `EventTarget`.
- **Module 10 ch05 (ABI/codegen):** where erasure vs monomorphization is
  physically decided.

## Quiz

1. Milner's slogan is "well-typed programs cannot go wrong". What does "go
   wrong" mean *precisely*, in this chapter's vocabulary? Give two failure
   modes the slogan does **not** cover.
2. In proof-system terms, why does `null` break a type system rather than
   merely annoy programmers? Answer with one phrase for what `null` does to
   the proofs of every reference type, and why `Option`/`Maybe` repairs it.
3. Derive by hand, using the 8 rules, the type of `(λf:Bool→Bool. f true)
   (λy:Bool.false)` — write the derivation tree and give the final type.
4. Java: `String[] ss = new String[2]; Object[] os = ss; os[0] = Integer.valueOf(42);`.
   What happens exactly, and which theorem would skipping the check violate?
   Counter-example half: if Java also allowed `List<String> <: List<Object>`
   with no runtime check, construct the silent corruption and state the
   function-variance rule both ignored.
5. A function `h : ∀α.[α]→Int` (think `length`). Using parametricity, derive
   from the type alone that `h (map f xs) = h xs` for every `f`.
6. This TypeScript compiles clean under `--strict`, then dies:
   `const nums: number[] = [1,2,3]; const mixed: (number|string)[] = nums; mixed.push("hi"); nums[3].toFixed(2)`.
   Which unsound rule admitted it, why doesn't `strict` help, and what is the
   minimal sound fix?
7. Kotlin, calling a Java library: `val name: String = javaApi.getName()`
   compiles with no warning; `name.length` throws `NullPointerException` in
   production. Diagnose the exact type the assignment went through, what the
   compiler did with the proof obligation, and the fix.
8. Cross-chapter (module 01 ch06): on 64-bit Rust, `i32` is 4 bytes,
   `Option<i32>` is 8, `&T` is 8 and `Option<&T>` is 8. Explain both numbers
   with the tagged-value layout story, and say which one keeps the proof's
   runtime cost at zero.

<details>
<summary>Answers</summary>

1. **"Go wrong" = stuck**: a closed term that is neither a value nor steps
   (like `true true`). Not covered: (a) integer overflow — mathematically
   wrong answers that typecheck; (b) logic bugs — a well-typed `sort`
   returning a wrongly ordered list; (c) divergence, which never goes stuck.
   Soundness is stuck-freedom, not correctness (chapter 05).
2. `null` is a **universal inhabitant**: it belongs to every reference type,
   so every proposition has a proof term with no evidence — a system where
   every claim is provable proves nothing, and `e.field` is a "well-typed"
   stuck term (progress violated). `Option<Foo>` repairs it by making absence
   a *separate case* (`Foo + Nothing`): the payload read requires the
   compiler-enforced match — the lie becomes a 2-case type with a tag.
3. Bottom-up. `y:Bool ⊢ false : Bool` (T-False), so T-Abs gives
   `⊢ λy:Bool.false : Bool→Bool` (**D₂**). Under `f:Bool→Bool`: T-Var +
   T-True + T-App give `f true : Bool`. T-Abs: `⊢ λf:Bool→Bool.f true :
   (Bool→Bool)→Bool`. Final T-App, argument matching exactly: **`Bool`** —
   the term evaluates to `false`.
4. **`ArrayStoreException` at the store** — every write into a reference
   array checks the runtime element type (arrays are reified, covariant).
   Without the check preservation dies: the store is legal at `Object[]`,
   `ss[0]` becomes a `String`-typed slot holding an `Integer`, and
   `ss[0].length()` is a stuck "well-typed" term. `List` version:
   `List<String> ls; List<Object> lo = ls; lo.add(42);` — legal at
   `List<Object>`, so `ls.get(0)` hands a `String`-proof for an `Integer`,
   and `.length()` on it detonates silently (generic stores carry no runtime
   check — why Java *refuses* that subtyping). Both ignored `S₁→S₂ <: T₁→T₂
   iff T₁ <: S₁ and T₂ <: S₂`: the consumer of the wider type gets to
   *input* the wider element, so input positions are contravariant;
   covariance there is writable-through, unsound.
5. Take R = graph of f (`a R b` iff `b = f(a)`); `xs` and `map f xs` are
   R-related elementwise. Parametricity forces `h` — it can inspect only
   shape — to map R-related inputs to R-related outputs, and on `Int`
   relations are equality: `h (map f xs) = h xs`. `length` counts shape, not
   values — the `reverse` derivation with the result type collapsing the
   relation to equality.
6. **Mutable arrays are covariant**: `number[]` is assignable to
   `(number|string)[]`, sound only if nobody writes through the alias;
   `mixed` and `nums` are the *same* array, so `push("hi")` plants a string
   in a slot still certified as `number`. `strict` doesn't help: array
   variance is not a strictness flag — a documented unsoundness (method
   bivariance is its sibling). Minimal sound fix: `const mixed: readonly
   (number|string)[] = nums` (read-only covariance is sound — no store to lie
   through), or copy: `const mixed = [...nums]`.
7. The assignment goes through the **platform type** `String!` — Kotlin's
   name for a Java type of unknown nullability. The compiler neither forces
   a check (as `String?` would) nor trusts it (as a proven `String` would);
   `val name: String` is *you* asserting the proof, so the obligation moved
   to the programmer. Fix: `val name: String? = javaApi.getName()`, forcing
   the null check (`name?.length`).
8. `Option<&T>` = 8 B: a reference already reserves the all-zero pattern as
   illegal, so the niche optimization reuses it as the `None` tag — the sum
   fits in the pointer's 8 bytes, and the proof's runtime cost is **zero**.
   `Option<i32>` = 8 B: all 2³² patterns are legal `i32`, none spare, so a
   real discriminant rides along — module 01 ch06's tagged-value layout (tag
   word before payload) at full price.

</details>
