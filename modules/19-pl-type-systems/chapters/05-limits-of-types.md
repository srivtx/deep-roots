# Chapter 05 — What types cannot do

> **The 30-second version.** Chapters 01–04 built the type system into a
> theorem prover: soundness, memory safety, liveness, machine-checked;
> this chapter is the receipt. Four things no type system can give you:
> termination (halting problem — total languages dodge it by rejecting
> legal loops), spec fidelity (types prove *syntactic* facts; "this
> sorts" is semantics, paid for with tests or module 21), the world
> (I/O and time cannot be caged by a pure type — Haskell's `IO` is an
> honest surrender), and escape hatches (any `unsafe`/`any` is a
> membrane — the same kernel-membrane-bulk shape as an OS kernel and a
> browser). Then the two grand trades — soundness vs precision, and
> static vs dynamic as *when you pay for the proof* — ending in a
> 6-question checklist for any language.

## The first impossibility: no checker admits exactly the terminating programs

Chapter 01's diagonalization sets a ceiling every type system lives
under. Suppose a checker accepted exactly the terminating programs.
Feed it the four-line Collatz loop below: it must either say
"terminates" — thereby proving the Collatz conjecture, open since the
1930s — or refuse, rejecting a loop everyone has run a billion times.
No third answer: by Rice's theorem, every non-trivial semantic
property of programs is undecidable. Real checkers pick a side, and
both sides cost:

- **Over-accept (everyone else).** Java, Rust, OCaml, TypeScript all
  admit non-terminating programs — `fn f() -> ! { f() }` typechecks,
  and the price lands at runtime: the wedged thread, the livelock.
  Soundness (chapter 02) says a program never gets *stuck* —
  not that it *finishes*.
- **Over-reject (total languages).** Agda and Coq make termination a
  typing obligation: every recursive call must be on a *structurally
  smaller* argument (Coq's guard condition) — under Curry–Howard
  (chapter 02), a non-terminating proof is a proof of `False`. The
  price is total programming: a general server loop — `serve ::
  Request -> Response` forever — cannot be written without *lying*,
  typically by threading a fuel argument (`serve n` runs ≤ `n`
  iterations). The honest escape is **codata**: define the server as a
  coinductive process — an infinite object observed lazily; the dual of
  "terminates" is *productivity*: every match delivers its next step in
  finite time though the object never ends. Termination read backwards:
  not "it stops," but "every step arrives." Total
  languages ban ordinary loops; codata recasts them as infinite
  values; industry buys neither and pays at runtime.

## The second: types prove syntax, not specs — and even data models fight

A type claims *shape*: this value is drawn from that set, and soundness
says evaluation stays inside the promised shapes. Every
interesting bug lives one level up, in semantics: "this function
*sorts*," "this dose calculator *never returns a lethal value*," "this
loop *eventually releases the lock*" — specifications, which no
decidable checker can verify (the same Rice theorem). The gap is
filled by **tests** (weaker, finite proofs about sample inputs),
**formal verification** (module 21's machines, at the price of proof
scripts), or **nothing** — the default. The smallest instance: `int`
addition typechecks, yet `x + 1 < x` can be *true* — a value fact,
invisible to a shape checker. Milner's "well-typed
programs cannot go wrong" means *cannot get stuck* — overflow, wrong
answer, wrong dose: all still possible.

Even at the data-model level, guarantees fight: the **expression
problem** is the minimal proof. Extend a type along two axes — *new
cases* (`Triangle` joins `Circle | Rect`) and *new functions*
(`perimeter()` joins `area()`) — under two designs:

```text
                sum types (Rust enum /       OO interfaces
                TS discriminated union)      (a class per case)
+---------------+---------------------------+---------------------------+
| add a CASE    | touch EVERY function:     | free: new subclass,       |
| (variant)     | every exhaustive switch   |   zero existing code      |
|               | must handle it            |   edited                  |
+---------------+---------------------------+---------------------------+
| add a         | free: just write the      | touch EVERY class: the    |
| FUNCTION      |   new function            |   method joins every      |
|               |                           |   implementing class      |
+---------------+---------------------------+---------------------------+
```

Neither column is better; they place the friction on opposite axes.
Wadler named the tension in 1998; the **visitor pattern** is the OO
compromise — cases become one method per case on a visitor interface,
so new *functions* (visitors) are free while new *cases* still
edit every visitor. The visitor moves the seam, never removes it:
impossibility 2 in miniature — both axes cheap is not on the menu.

## The third: the world — effects a pure type cannot cage

A program is a closed box of terms; the world is not. `read()`,
`now()`, another process return different values for identical
expressions; no type system can constrain what the kernel returns, and
the machine beneath even disagrees with itself. Types stop at the
process boundary — Impossibility 3.

Haskell's answer is an honest surrender, not a solution. `IO` brands
a computation as "this *is* an effect," and the only thing the type
system knows about branded values is how to sequence them:

```haskell
-- the entire effect surface of the program is in the type
main :: IO ()
main = do
  putStrLn "name?"          -- effect #1
  name <- getLine           -- effect #2, must happen after #1
  putStrLn ("hi, " ++ name) -- effect #3; () = no result value
```

`main` is not a printed string; it is a *value describing* an effectful
computation, and `do`/`>>=` is the only law: run in order. The type
system never inspects the effect — it cannot prove the file exists or
the writes are ordered. You get *tracking* — which code can touch the
world is a typed fact — instead of *knowledge*. The frontier
tries for more: **effect rows** (Koka) let a function's type say
`<io>`, `<div>`, `<throw>` and compose rows across calls.
Research-frontier: none of module 01's deployed languages has it.

## The fourth: escape hatches — the TCB shape, drawn three times

Any sound system large enough to be useful grows a hole *by design*:
Rust's `unsafe`, TypeScript's `any`, Java's casts, Python's `ctypes`.
The theorem changes shape instead of dying: soundness becomes "the
**kernel** of provably-safe code plus the **membrane** of audited code
upholds the invariants" — the shape of every trustworthy system, seen
twice already in this program:

```text
    OS kernel                 browser                  Rust project
+--------------------+  +--------------------+  +--------------------+
| ring 0: kernel     |  | broker (the        |  | unsafe blocks      |
| small, privileged, |  |   browser process: |  | small, privileged, |
| AUDITED, owns all  |  |   owns screen,     |  | AUDITED, owns raw  |
|   hardware         |  |   disk, sockets)   |  |   memory           |
+--------------------+  +--------------------+  +--------------------+
| membrane: the      |  | membrane: Mojo IPC |  | membrane: the safe |
|   syscall table    |  |   + seccomp filter |  |   API (Vec, Mutex, |
|   (the only door)  |  |   (the only door)  |  |   send — the door) |
+--------------------+  +--------------------+  +--------------------+
| ring 3: processes  |  | renderers:         |  | safe Rust: the     |
|   untrusted bulk   |  |   untrusted DOM/JS |  |   95% the checker  |
|   (the 99%)        |  |   bulk (the 99%)   |  |   proves for free  |
+--------------------+  +--------------------+  +--------------------+
```

Same three layers, three times: a small trusted kernel, a narrow
membrane as the *only* door to its powers, and a bulk that is
never trusted — only checked. This is the program's thesis, stated one
last time: **soundness is never a property of the whole program; it is
a property of the kernel plus the membrane.** The corollary: the audit
surface is measurable — count the kernel, review the membrane. When a
renderer escapes or an `unsafe impl Send` lies, the bug is never in
the bulk: the postmortem goes to the kernel or the membrane.

## Trade one: soundness or precision — pick your false friend

Soundness (a well-typed program really is safe) and precision (the
checker accepts most reasonable programs) trade off, and real
languages have chosen every corner:

```text
                         sound (guarantees hold)
              +--------------------------------------+
              |   Rust / OCaml / SML                 |   Haskell: sound,    |
   precise    |   borrow checker REJECTS legal       |   but the price is   |
   (accepts   |   programs: the false-positive tax   |   effect limits and  |
    a lot)    |   (fighting NLL, variance...)        |   IO puritanism      |
              +--------------------------------------+
              |   TS / Flow: precise, DELIBERATELY   |   (no major taker    |
   permissive |   unsound (any, assertions,          |    of this corner)   |
              |   covariant arrays)                  |                      |
              |   Java 1996-2004: casts + covariant  |                      |
              |   arrays = unsound BY ESCAPE         |                      |
              +--------------------------------------+
                         unsound (holes by design)
```

Read the corners as invoices. Rust/OCaml pay in rejected programs —
every borrow-checker fight and appeasing `clone()` is the
false-positive tax. TypeScript bought the opposite corner deliberately:
`any`, assertions, and covariant arrays leak unsoundness for
near-zero friction on a 10-million-line JavaScript codebase. Java's cast era: `ClassCastException` is a type system
arriving late. Haskell is sound but effects-limited — purity is real,
monadic plumbing is the price. No corner is free; a language claiming
sound *and* precise *and* permissive is lying about one of them.

## Trade two: static vs dynamic is a proof-schedule choice

The holy war dissolves under the module's lens. The *same proofs* —
"this is an int," "this object is live," "this borrow is unique" — can
be scheduled at compile time or at runtime. The tag word on every
Python object (module 01 chapter 06's certificate: 16 B of
`refcount + type ptr` per value), the GC pause (chapter 04's bill:
~4 s on a 2 GB heap at ~500 MB/s marking), and the borrow error
(a compile error today instead of a use-after-free in month nine) are
one proof at three schedules:

```text
+---------------------+------------------------+------------------------+
|                     | static schedule        | dynamic schedule       |
+---------------------+------------------------+------------------------+
| unit cost           | seconds-minutes, once  | ns/op (tag check) to   |
|                     | per build              | seconds (GC pause)     |
| paths paid for      | ALL paths, including   | only paths actually    |
|                     | never-executed ones    | executed               |
| when the bill       | before you run         | mid-request,           |
| arrives             | (compile latency)      | mid-loop, mid-pause    |
| failure mode        | rejected build (cheap) | TypeError / pause /    |
|                     |                        | crash (expensive)      |
+---------------------+------------------------+------------------------+
```

The arithmetic that ends the argument: a proof costing 10 s once
beats 12 ns per execution after 10 s / 12 ns ≈ **8 × 10^8 ≈ 10^9
executions**. A service at 3,000 calls/s crosses that in ~4 days and
banks margin forever — static amortizes. A script run 20 times
pays 10 s to save 480 ns — dynamic wins by eight orders of magnitude,
and the developer inner loop *is* the scripts regime — why the same
person can rightly write Python at 11 a.m. and Rust at 3 p.m. Never
philosophy; when you pay.

What survives is a 6-question checklist for any language, applied to
the four you know best:

| question | Rust | TypeScript | Go | Python |
|---|---|---|---|---|
| proves at compile? | memory + thread safety (mod `unsafe`) | shape/nullability, unsoundly | types; races only with `-race` | nothing (syntax only) |
| proves at runtime? | bounds/overflow panics (debug) | JS tag checks | nil-map, bounds, GC | tag check on EVERY op |
| tag cost? | none (erased; enums pay 1 byte by choice) | none (erased) — JS objects carry shape maps | interfaces carry itabs | 16 B header/obj, ints boxed 28 B |
| escape hatch blast radius? | `unsafe`: whole-crate soundness | `any`: bisection — spreads silently | `interface{}`, `unsafe` | `ctypes`: the process |
| GC proves, when? | nothing (ownership proved at compile) | V8 tracing, pauses = bill | concurrent GC, ~sub-ms | refcount now + cycles later |
| TCB? | unsafe blocks + std + rustc | checker + everything behind `any` | runtime + reflect + unsafe | CPython + every C extension |

## What insiders know

- **The vocabulary ladder is nested, and people blur it.** Type
  soundness ⊆ memory safety ⊂ safety ⊂ correctness, where ⊆ means
  *implies*. Rust/SML sit in the strongest ring. Java-with-casts and
  modern TS are memory-safe but statically unsound — the runtime tags
  catch the lie (`ClassCastException`: a *type* error delivered
  late). Python is memory-safe by running its type system at
  runtime. The interview trap: asked "is X type-safe?", name the ring,
  not the word.
- **Rust `unsafe` ≠ C# `unsafe` — false friends.** Rust's is a *proof
  obligation* (checks lifted; you now owe the invariants), C#'s is a
  *pointer permission* (raw pointers, `fixed` buffers; the CLR skips
  verification for those methods). Opposite membranes: Rust's unsafe
  code must re-establish safety through its API; C#'s can corrupt any
  managed object it reaches.
- **TypeScript `unknown` vs `any` is the disciplined-escape lesson.**
  `any` is assignable in *both* directions — the checker stops and the
  hole spreads to every caller (bisection). `unknown` accepts
  everything *inward* but pays before use: narrow first (typeof,
  instanceof, a guard). Rule: boundary data — JSON, network, parse
  results — gets `unknown`, never `any`.
- **GHC's `-fdefer-type-errors`** reschedules the proof to runtime:
  type errors become warnings, and each deferred expression becomes a
  runtime exception *if evaluated*. Useful for exploring half-fixed
  code; dangerous to ship — an unexecuted path carries an unproven
  theorem.
- **The first static/dynamic war ran 1958–present.** Backus proposed
  compile-time type checking to the Algol 58 committee and was
  rejected; McCarthy's Lisp (1958–60) shipped untyped and won the AI
  decade. The industry's oldest holy war is that Zurich meeting, still
  running.
- **TypeScript 2012 is the industry's proof-debt confession** (10^6
  lines of JS could not be retrofitted with tests), and **linear
  logic → Rust is the 28-year arc**: Girard's linear logic (1987),
  Wadler's "Linear types can change the world!" (1990), Rust 1.0 (May
  2015). Paper-to-product latency like this is why module 01's
  languages look as they do.

## See it yourself

**1. Termination on an open problem (4 lines, any machine).**

```python
def collatz(n):
    while n != 1:
        n = 3*n + 1 if n % 2 else n // 2
    return n
```

```console
$ python3 -c 'from ch05 import collatz; print(collatz(27))'          # 111 steps
$ python3 -c 'from ch05 import collatz; print(collatz(670617279))'   # 986 steps
```

Every input ever tried halts (verified to 2^68 ≈ 3×10^20); nobody can
prove all do. Any tool deciding termination for this file would decide
Collatz. Watch it run; that is the gap.

**2. Spec fidelity: types pass, the property fails.**

```python
# pip install hypothesis   (Python 3.8+)
from hypothesis import given, strategies as st

def sort(xs: list[int]) -> list[int]:     # the annotation is TRUE
    return sorted(set(xs))                # ...and the spec is FALSE

@given(st.lists(st.integers()))
def test_permutation(xs):
    assert sorted(xs) == sort(xs)         # weaker proof than a theorem,
                                          # stronger than any unit test
test_permutation()
```

Hypothesis 6.x falsifies in ~31 examples and shrinks to the boundary:

```text
Falsifying example: test_permutation(
    xs=[0, 0],      # the duplicate boundary: sorted(set()) drops it
)
```

The annotation `list[int] -> list[int]` is honest and useless:
"permutation" is semantics — no type system catches this.

**3. The expression problem in 2 × 30 lines (TypeScript playground).**
Sum-type version:

```typescript
type Shape = { kind: "circle"; r: number } | { kind: "rect"; w: number; h: number };

function area(s: Shape): number {
  switch (s.kind) {
    case "circle": return Math.PI * s.r ** 2;
    case "rect":   return s.w * s.h;
    default: { const _exhaustive: never = s; return _exhaustive; }
  }
}
function perimeter(s: Shape): number { /* same switch shape */ return 0; }
```

Add `{ kind: "tri"; a: number }` to the union: the compiler instantly
flags `area()` **and** `perimeter()` at each `never` check — every
function touched (top-left cell). Now write `describe(): string` —
zero existing lines edited. Then build the OO mirror (`abstract class
Shape`, `Circle`/`Rect` subclasses) and invert: `Triangle extends
Shape` edits nothing; `describe()` edits every class — you have *felt*
both cells of the 2×2.

**4. The escape-hatch census (any real tree, one command).**

```console
$ cd /usr/lib/python3.13/asyncio
$ rg -c "# *type: *ignore" -g '*.py' --no-filename | paste -sd+ | bc   # 3
$ rg -c "except *:"      -g '*.py' --no-filename | paste -sd+ | bc   # 15
$ cat $(rg --files -g '*.py') | wc -l                                  # 14754
```

CPython 3.13's asyncio: 14,754 lines, an audit surface of 18 sites
(3 `# type: ignore`, 15 bare `except:` in 7 files) — a **membrane
ratio of 0.12%**. That number *is* the review budget. The same census
on Rust: `rg -c "unsafe" -g '*.rs'`; on TypeScript: `rg -w "any" -g
'*.{ts,tsx}'`. Run it on your dependency tree before you trust it.

**5. The proof-schedule experiment: 10^7 calls, two languages.**

```console
$ python3 timing.py          # loop of 10**7 calls, time.perf_counter
duck-typed method call :  110 ns/call  total 1.10 s
plain function call    :   69 ns/call  total 0.69 s
$ gcc -O2 static.c && ./staticc     # noinline static void step(void)
static C call          :  2.1 ns/call  total 0.02 s
```

(One core of the build VM; CPython 3.12, gcc 14.2.) The naive read —
"dynamic typing is 50× slower" — is wrong; the confound: ~108 of
those 110 ns is *interpreter* tax (bytecode
dispatch, frame setup), not tag-checking. The tag tax visible inside
Python: the ~40 ns between bound-method and plain calls —
and 3.12's specializing interpreter already discounts it; a
JIT with inline caches (V8) amortizes dispatch to single-digit ns.
The chapter's honest conclusion: dynamic costs *per execution,
forever*; static costs *once, for all paths* — 10^7 × ~100 ns is
a full second, every run.

## How to use this knowledge

- **Choosing a language is arithmetic, not identity.** Run the
  6-question table on the candidates, then the 10^9 amortization on
  your execution count. Batch script → dynamic wins;
  long-lived hot service → static banks margin after ~10^9 runs.
- **Code review targets the membrane.** A PR adding `unsafe`, `any`,
  or a cast is reviewed like a kernel patch: what invariant does the
  author now owe, and does the safe API re-establish it?
- **Postmortem triage by ring.** A crash in "memory-safe" Python or
  Java is in the TCB — a C extension, an `unsafe`, a boundary cast —
  never in the checked bulk; audit at the membrane, where the census
  points.
- **Interview answers that land:** "sound vs complete" placed on the
  2×2 with a language per corner; "static vs dynamic" as *scheduling*
  with the 10^9 break-even; "is TypeScript safe?" with the ring, the
  holes, and `unknown`.

## Connections

- **Chapter 01** — the diagonalization whose ceiling became the four
  impossibilities; Rice's theorem is halting's big sibling.
- **Chapter 02** — progress + preservation, whose *scope* (stuck terms
  only) is why Milner's quote leaves room for module 03's bugs.
- **Chapter 03** — the spectrum table; this chapter generalized
  `unsafe`-Rust-as-kernel into the TCB shape shared with OSes and
  browsers.
- **Chapter 04** — the GC as proof search; its pause is this chapter's
  "proof bill arriving," scheduled dynamically.
- **Module 21 (formal verification)** — what fills the spec-fidelity
  gap: machines that prove whole kernels (seL4: ~10,000 lines of C,
  ~400,000 lines of proof).
- **Module 03 (software bugs)** — what survives typing: logic,
  parsing, numeric, concurrency bugs — Impossibility 2's invoice.
- **Module 01 chapter 20 (hardware memory models)** — Impossibility 3
  in silicon: the proof beneath your types that no type system hands
  you for free.

## Quiz

1. **Concept.** The vocabulary ladder claims type soundness ⊆ memory
   safety. Give the containment direction in words, one language
   occupying each of the two innermost rings, and the runtime event
   that proves the outer ring can hold when the inner one fails.

2. **Concept.** A colleague says "static vs dynamic typing is a
   philosophical preference." In one sentence each, recast the tag
   word, the GC pause, and a borrow-checker error as the same thing
   at three different schedules.

3. **Predict/derive.** A static proof costs 10 s once per release;
   the equivalent dynamic proof costs 12 ns per execution. Derive
   the break-even execution count, then rule on (a) a CLI script run
   daily for 5 years and (b) a service at 3,000 calls/s: which schedule
   wins, by roughly how much?

4. **Predict.** In the sum-type shape code (union of `circle | rect`,
   exhaustive switches with `never` checks in `area()` and
   `perimeter()`), predict exactly what the compiler reports when
   `{ kind: "tri" ... }` joins the union — which functions, which
   line, what error. Then predict the same two edits for the OO
   version.

5. **Derive.** A Python list holds 10^6 distinct integers (CPython
   3.12, 64-bit). Compute total bytes and the *certificate* fraction
   (object header + list pointer) versus payload, given
   `sys.getsizeof(1) == 28` and 8-byte list slots. Which schedule is
   the certificate paying for?

6. **Diagnose.** A Rust service segfaults; the stack ends in
   `Vec::push` reached through `unsafe impl Send for RawChannel` in a
   dependency. Safe code called the safe API. Where is the bug, why
   is "Rust is memory-safe" not falsified, and which two tools does
   the audit use?

7. **Diagnose.** A TypeScript codebase crashes at runtime:
   `TypeError: Cannot read properties of undefined (reading 'price')`
   on `items[0].price`, though `items` is declared `Item[]`. The
   value crossed `JSON.parse(raw) as any` at an API boundary.
   Explain the bisection: what did the checker check downstream, and
   what is the minimal boundary fix and why does it work?

8. **Cross-chapter.** A Rust thread sets a flag with
   `AtomicBool::store(flag, Ordering::Relaxed)`; another thread,
   after seeing the flag, reads the buffer published before it. The
   borrow checker approved the code; the second thread sees stale
   data. Connect to module 01 chapter 20: which impossibility is
   this, what exactly did the type system prove (and not prove), and
   where did the proof obligation go?

<details>
<summary>Answers</summary>

1. **Type soundness implies memory safety; the reverse fails.** Inner
   ring (type-sound): Rust or SML — well-typed programs never commit a
   type-confused access, which entails no OOB, no UAF, no wild
   pointers. Outer ring (memory-safe, statically unsound):
   Java-with-casts or modern TypeScript — the runtime event proving
   the outer ring held while the inner failed: `ClassCastException`,
   a *type* error at runtime with no memory corruption.

2. **One proof, three schedules.** The tag word is the certificate
   carried per value, checked on every operation (paid per
   execution); the GC pause is the liveness proof's bill arriving
   mid-run (paid per collection, ~seconds); the borrow error is the
   aliasing proof delivered at compile time (paid once, for all
   paths). Static vs dynamic is the
   *schedule* of one obligation, not a philosophy.

3. **Break-even: 10 s / 12 ns ≈ 8.3 × 10^8 ≈ 10^9 executions.**
   (a) The script runs 5 × 365 ≈ 1,800 times: dynamic pays 1,800 ×
   12 ns ≈ 22 µs against static's 10 s — dynamic wins by ~500,000×.
   (b) The service does 3,000 × 86,400 ≈ 2.6 × 10^8 calls/day,
   crossing 10^9 in ~4 days; over 2 years (~1.9 × 10^11 calls)
   dynamic would pay ≈ 2,300 s ≈ 38 min of checking versus
   10 s once — static wins by ~200× and covers all paths, executed
   or not.

4. **Sum types:** the union edit is accepted, but every exhaustive
   switch fails its `never` check — both `area()` and `perimeter()`
   error at the `const _exhaustive: never = s` line, of the form
   *Type '{ kind: "tri"; a: number; }' is not assignable to type
   'never'* (TS2322). Two existing functions touched: the top-left
   cell. `describe()` is a new function: zero existing lines change.
   **OO:** `Triangle extends Shape` overrides compile with zero edits
   to existing classes; the new `describe()` must be added to the
   base class and implemented in every subclass — every case edited:
   the bottom-right cell.

5. **Total: 10^6 × 28 B (int objects) + 10^6 × 8 B (list slots) ≈ 36
   MB** (−5..256 are cached singletons; "distinct" avoids the
   discount). Payload: one 30-bit digit = 4 B of each 28 B object, so
   payload ≈ 4 MB. Certificate = 24 B header (refcount 8 + type
   ptr 8 + size 8) + 8 B pointer = 32 B per element: 32/36 ≈ **89%
   certificate, 11% payload**. It pays for the dynamic schedule:
   every operation re-proves "this is an int" from the header, on
   every use, every execution.

6. **The bug is in the membrane, not the safe bulk.**
   `unsafe impl Send` asserts "RawChannel has no thread-unsafe
   interior" — a proof obligation the compiler trusts. If the buffer
   is mutated from two threads, the impl lied and the crash is the
   deferred bill. "Rust is memory-safe" survives because the theorem
   was always *conditional*: soundness = every unsafe block upholds
   its invariants. Tools: Miri (chapter 03's operational model) and
   this chapter's unsafe census — audit the ~40-line unsafe kernel,
   not the 10,000 safe callers.

7. **Bisection: `as any` is assignable both directions, so the
   checker stopped at the boundary and every downstream function
   typechecked against a fiction** — `items: Item[]` was never
   contradicted because nothing checked the JSON's actual shape.
   Minimal fix: `const raw: unknown = JSON.parse(rawText)` plus a
   narrowing guard before use (typeof, `Array.isArray`). `unknown`
   accepts anything inward but pays before use, settling the proof
   debt once, at the membrane — downstream guarantees become
   *earned*, not asserted.

8. **Impossibility 3 — the world.** Rust's type system proved
   *data-race freedom* — no two `&mut` to the same memory — so the
   sharing itself was legal. It did not — and cannot — prove
   *ordering*: with `Relaxed`, module 01 chapter 20's hardware memory
   model lets the flag store become visible before the buffer stores
   it guards. The proof obligation moved into happens-before
   reasoning (Acquire/Release — module 09's territory) or a verifier
   (module 21). The types caged aliasing; the memory model is the
   proof the machine refuses to give you.

</details>
