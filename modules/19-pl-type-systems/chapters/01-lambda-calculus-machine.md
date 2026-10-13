# Chapter 01 — The lambda calculus: the minimal machine

> **The 30-second version.** Module 01 asked how memory serves languages and
> never asked the question underneath: *what is the smallest language that
> still computes?* Answer: three term shapes — variable, `λx.t`, `t1 t2` —
> and one rewrite rule, β-reduction. That machine is Turing-complete: its
> booleans, pairs, and numbers are all functions; recursion is a term that
> re-grows itself. But Turing completeness ships with the halting problem in
> the same box — no algorithm decides whether the machine stops — and that
> theorem dictates the shape of every type system and inference limit in
> this module. Every language you have used is sugar on this machine.

## Three shapes of term and one rewrite rule

Strip a modern language of everything derivable and what remains is Church's
1936 notation. The whole grammar fits on one line:

```
t ::= x | λx. t | t1 t2          variable | abstraction | application
```

`λx. t` is the only data constructor, juxtaposition the only operator,
variables the only leaves — no numbers, no `if`, no `=`. Bookkeeping: an
occurrence is **bound** if an enclosing `λ` names it, **free** otherwise
(`FV(λx. t) = FV(t) − {x}`); no free variables means **closed**. Three
conversions exist; one computes:

- **α** — rename a binder: `λx. t ≡ λy. t[x := y]`.
- **β** — the compute step: `(λx. t) u → t[x := u]`. The chapter lives in
  this arrow.
- **η** — `λx. f x ≡ f` when `x ∉ FV(f)`: a pure forwarder *is* the
  forwarded; never needed to compute, but it manufactures the delay strict
  languages need for recursion (*insiders*).

β looks like textual search-and-replace; that look is a trap. Substitution
must be **capture-avoiding** — here naive substitution breaks: the argument
`λz. y` carries a *free* `y`, and the body binds a `y` right over the landing
zone:

```
 (λx.  λy.  x  y ) (λz. y)      FV(λz. y) = {y}: the argument's y is FREE,
   |    |   |  |     |  |        its binder is OUTSIDE this term
   |    |   +--+----+-- the argument u = λz. y; one OCCURRENCE of x to replace
   |    +------------- binder y: DANGER, it roofs the landing zone
   +------------------ binder x: what the application binds

 naive β:    λy. (λz. y) y      the free y is now under λy -- CAPTURED
 honest β:   λy0.(λz. y) y0     α-rename the binder to a fresh y0 first
```

The naive result is not an error — it is a *different program*: the `y` that
pointed outside now points at the inner `λy`. One rule makes capture
impossible: when substituting `u` for `x`, if a binder of `t` would swallow a
free variable of `u`, α-rename that binder first (the lab ends the reduction
above in `λy0. y`, fresh binder visible). Those are the subtlest 10 lines in
any functional language — the interpreter's `sub`, exactly — and production
compilers sidestep them (*insiders*).

## Two evaluation orders, one term that never stops

β says a redex *can* fire, not *which one fires first*. With several redexes
in play, that choice is your language's whole semantics:

- **Normal order** — leftmost-outermost: fire the outermost redex, substituting
  arguments *unreduced*.
- **Applicative order** — arguments first: reduce an argument to a value
  before firing its application.

The stress test is a term that eats itself, **Ω**:

```
Ω = (λx. x x) (λx. x x)
→β (λx. x x) (λx. x x)     substituting the argument into body (x x) rebuilds Ω
=  Ω                       the term is its own reduct: Ω → Ω → Ω → ...
```

Evaluate Ω both ways: it has exactly one redex, which returns the same term
forever — order cannot save it; Ω is the calibration weight. Wrap it in one
cheap abstraction and order becomes everything:

```
            (λx.λy. y) Ω
            \___/\___/ \___/
              root β    Ω: its only redex is itself

 normal:      fire the root, substitute Ω UNREDUCED → λy.y   [1 step: done]
              the body never mentions x: Ω discarded, never entered
 applicative: reduce Ω to a value first → Ω → Ω → ... a value is the
              one thing Ω never becomes: DIVERGES
```

Same term, two orders, one answer and one infinite loop. Behind it:
confluence (Church–Rosser) — order never changes *which* normal form you get,
only whether you live to see it — and standardization: if any order
terminates, normal order does. Hence the names — normal order is **lazy**
evaluation (Haskell, the only mainstream language to bet on it), applicative
is **strict** (everything else). Laziness is not magic: a body that *uses*
its argument still runs the substituted copy (quiz 3).

## Data as control flow: the machine proves itself

No data exists here, so it must be built from the only material —
functions. The uniform trick: an encoded value is a function that waits to
be told what to do; its "value" *is* its behavior when consumed.

**Booleans** are two-argument selectors, `if` their consumer:

```
TRUE  = λa.λb. a          FALSE = λa.λb. b
IF    = λc.λt.λe. c t e   -- apply the condition to both branches; it discards one
```

`IF TRUE t e → t` in two steps, dead branch untouched — `if` needs no special
syntax, *because* normal order never forces the branches (quiz 1). Pairs hand
the selector in later: `PAIR = λa.λb.λsel. sel a b`, `FST = λp. p TRUE`,
`SND = λp. p FALSE`. **Naturals** are "apply `f` n times" — `2 = λf.λx. f
(f x)` — and the operators are compositions:

```
SUCC = λn.λf.λx. f (n f x)        -- one more f
ADD  = λm.λn.λf.λx. m f (n f x)   -- compose the ITERATED functions: f^m after f^n
MUL  = λm.λn.λf. m (n f)          -- compose the NUMERALS: (f^n)^m = f^(n*m)
```

Addition composes iterated functions; multiplication composes the numerals —
the same idea one level up, which is why `MUL` is shorter. `MUL 2 3` traced
by hand, every β numbered — the lab prints exactly this normal form:

```
MUL 2 3 = (λm.λn.λf. m (n f)) 2 3
 1 →β λn.λf. 2 (n f)                          m := 2
 2 →β λf. 2 (3 f)                             n := 3
 3 →β λf. λx. (3 f) ((3 f) x)                 2 = λg.λx. g (g x), g := 3 f:
                                               "apply the function (3 f) twice"
 4 →β λf. λx. (λx. f (f (f x))) ((3 f) x)     leftmost (3 f) reduces: 3 applied to f
 5 →β λf. λx. f (f (f ((3 f) x)))             x := ((3 f) x): three f's wrapped
 6 →β λf. λx. f (f (f (λx. f (f (f x)) x)))   the second (3 f) reduces
 7 →β λf. λx. f (f (f (f (f (f x)))))         x := x: inner application fires
 = 6                                          count the f's: SIX
```

Six `f`s — `2 (3 f)` and `3 (2 f)` both land on six: commutative encoding,
different traces.

**Recursion without a recursion primitive.** No term can mention itself, so
recursion must be a term that *regrows itself* — Curry's fixed-point
combinator:

```
Y = λf. (λx. f (x x)) (λx. f (x x))
```

Unfold once, honestly, on a free `g` (a stand-in for any recursive body):

```
Y g →β (λx. g (x x)) (λx. g (x x))        f := g
    →β g ((λx. g (x x)) (λx. g (x x)))    x := the second half
    =   g (Y g)                            the original term reassembled INSIDE g
    →β g (g (Y g)) →β g (g (g (Y g))) ... one unfolding per recursive call, forever
```

`Y g` is a fixed point of `g`: it re-creates itself as `g`'s argument, storing
nothing — no stack, no counter, no self-reference in the syntax.
Non-termination is not *added* to this machine; it was already legal.

Church–Turing, two sentences as promised: everything a Turing machine
computes, the λ-calculus computes, and vice versa — Turing proved the two
1936 definitions equivalent in 1937, and every model since (register machines,
your CPU, cellular automata) joined the class. We do not re-prove it; we use
it — `IF`, numerals, and `Y` give this machine branching, arithmetic, and
recursion, which is everything a Turing machine has.

## The halting problem arrives in the same box

Turing-complete means at least as powerful as every computer that exists, so
this machine inherits the theorem that bounds them all — the diagonalization
proof as actual Python, self-reference included (Python resolves `paradox` by
name at call time):

```python
def halts(f, x):
    ...                                # ASSUMED oracle: True iff calling f(x) returns

def paradox():
    if halts(paradox, 0):              # ask the oracle about MYSELF
        while True:
            pass                       # oracle said True -> do the opposite: loop
    return 1                           # oracle said False -> halt

# True  => paradox loops forever => it does NOT halt: contradiction.
# False => paradox returns 1       => it DOES halt:    contradiction.
# Both impossible => halts cannot exist. Not "hard" -- impossible, for any
# language at least this strong.
```

(A timeout-based `halts` is buildable and useful — CI runs it daily — but it
must be *wrong* on `paradox`-shaped inputs.) The reach of the theorem is
**Rice's theorem** (1953), in plain words: *every non-trivial semantic property
of programs is undecidable* — any property true of some programs, false of
others, and about what a program **does** rather than how it is spelled. The
halting reduction is two lines: to ask "does `f(x)` halt?", build `g` that
runs `f(x)` first, then behaves like a program with the property:

```python
def g(y):
    f(x)                      # line 1: the halting question, smuggled inside
    return p(y)               # line 2: behavior with property P (P true of p,
                              #         false of the diverging function)
# P(g) holds  <=>  f(x) halts: a P-decider would be a halting decider. QED.
```

"Always returns a list?", "injective?", "ever touches this file?" — all
undecidable by this construction. The capstone: **no type system can reject
exactly the non-terminating programs** — a decidable checker that filtered
precisely the divergent terms would be a halting decider. Every real type
system draws its line inside the safe region; the rest of the module is where
each language put it.

## Undecidability is why your type checker has rules

Why does SML/OCaml/Haskell inference feel generous while GHC's higher-rank
types demand annotations? Because the decidability frontier runs exactly
between them.

The λ-calculus is untyped — nothing to infer. Add types and the question
becomes "does this term have any type at all?" The Hindley–Milner result
(Hindley 1969, Milner 1978): inference is **decidable** by unification
(Algorithm W), and every term has a **principal type** — a most general one
of which all others are instances. `fun x -> x` infers not *a* type but
`'a -> 'a`; one algorithm always finds the most general one. That is why
`let id = fun x -> x` then both `id 1` and `id "a"` typechecks: `let`-bound
values are **generalized** — and HM is the last comfortable rung of the
polymorphism ladder:

- **Rank 1** (all `∀`s outside the arrows — HM): decidable with principal
  types. `let`-polymorphism exists because this is the most generalization
  that keeps inference decidable *and* answers unique; the asymmetry is
  deliberate — λ-bound arguments stay **monomorphic** (`fun id -> (id 1,
  id "a")` is rejected), a step below the next rung.
- **Rank 2** (one `∀` nested left of an arrow): still decidable, but
  principal types are lost — no single best answer exists to find.
- **Rank 3+**: inference **undecidable** — a type-level Ω exists; the search
  can loop.
- **System F** (Church 1940, `∀` anywhere — GHC Core's substrate): inference
  is undecidable (Wells 1999 — typability and type-checking collapse into one
  problem). GHC's escape is the annotation: where inference would have to
  guess (`RankNTypes`, impredicativity), the programmer writes the type and
  the checker *verifies* it — verification with the guessing pre-filled stays
  decidable. Annotations are your half of a proof the machine provably cannot
  do.

Carrying the halting problem this far pays off: the limits are not design
taste. `let`-polymorphism exists because rank 1 is decidable with principal
types; higher ranks wander into undecidability; System F needs your
annotations because no algorithm can supply them. Chapter 02 climbs the next
rung on purpose — types as theorems.

## What insiders know

- **"Currying" and AWS Lambda's λ are one lineage.** Multi-argument functions
  are nested single-argument ones, so `a -> b -> c` is right-associative and
  partial application is free — named for Haskell Curry the logician (the
  language too). AWS Lambda (2014) wears λ as its logo because "lambda" has
  meant "anonymous function" since Lisp imported the notation; a cloud lambda
  is one application node — event in, handler out, cold start = allocating
  the closure.
- **Normal = lazy, applicative = strict, one sentence of history.** Landin's
  SECD machine (1964) implemented applicative order and implementations
  followed; Wadsworth (1971) made normal order affordable by *sharing*
  substituted arguments — call-by-need, memoized thunks; Turner's
  SASL → KRC → Miranda bet languages on it, and Haskell (1990) is the
  survivor. Everyone else stayed strict — why `&&` in C must be syntax: a
  strict language cannot afford a function that skips its second argument.
- **Untyped was the original; types are the patch.** Church built λ
  (1932–36) as a foundation for *logic*, not a programming language; it was
  shown inconsistent (Kleene–Rosser 1935), and simply typed λ (1940) was the
  repair. Self-application `x x` — Y's engine — is legal only because there
  are no types; chapter 02's typed calculus forbids it, which is why typed
  languages hand recursion back as a built-in `fix`.
- **SKI is smaller than λ.** Schönfinkel (1924, before Church):
  `S = λf.λg.λx. f x (g x)`, `K = λx.λy. x`, `I = K K` — Turing-complete with
  *no variables*; "point-free" Haskell style is combinator logic in λ
  clothing. Iota collapses to one combinator, `ι = λf. f S K`: a language
  whose only verb is applying `ι`.
- **Nobody renames in production — and strict languages need Z, not Y.**
  Compilers replace names with **de Bruijn indices** (`λf.λx. f x` becomes
  `λ λ 1 0`; the number is binder depth), making capture impossible by
  construction — unreadable, so proof assistants go "locally nameless"
  instead. And under call-by-value, Y builds its infinite argument *before*
  the call fires and diverges instantly; strict languages ship Z, Y with one
  η-expansion — `Z = λf. (λx. f (λv. x x v)) (λx. f (λv. x x v))` — whose
  `λv` wrapper is a delay. It is why `fix` in OCaml/Scala looks like that.

## See it yourself

The whole machine — parser, capture-avoiding substitution, both evaluation
orders, and a step budget so divergence is *shown* instead of hanging your
terminal — is 46 lines of Python. Save as `lam.py`; the output below is real.

```python
# lam.py -- the minimal machine: a normal-order lambda-calculus interpreter.
# Run: python3 lam.py     (CPython 3.12; any 3.8+ works)
import itertools
_fresh = itertools.count()               # gensym counter for capture-avoiding renames

def sub(t, x, u):                        # capture-avoiding t[x := u]
    if t[0] == 'var': return u if t[1] == x else t
    if t[0] == 'lam':
        if t[1] == x: return t           # x shadowed here: not free, stop
        if t[1] in free(u):              # binder would CAPTURE a free var of u
            y = t[1] + str(next(_fresh)) # fresh rename (y -> y0, y1, ...)
            t = ('lam', y, sub(t[2], t[1], ('var', y)))
        return ('lam', t[1], sub(t[2], x, u))
    return ('app', sub(t[1], x, u), sub(t[2], x, u))

def free(t):                             # free variables of t
    if t[0] == 'var': return {t[1]}
    if t[0] == 'lam': return free(t[2]) - {t[1]}
    return free(t[1]) | free(t[2])

def step(t, order='normal'):             # ONE reduction, or None if t is normal
    if t[0] == 'lam':                    # a lambda is not a redex; its body may be
        return ('lam', t[1], s) if (s := step(t[2], order)) is not None else None
    if t[0] != 'app': return None        # a variable is done
    f, a = t[1], t[2]
    if f[0] == 'lam' and (order == 'normal' or step(a) is None):
        return sub(f[2], f[1], a)        # BETA: (λx.t) u -> t[x:=u]
    s = step(f, order)                   # no root redex: leftmost subterm first
    if s is not None: return ('app', s, a)
    return None if (s := step(a, order)) is None else ('app', f, s)

def run(t, order='normal', budget=500):
    n = 0
    while (s := step(t, order)) is not None and n < budget: t, n = s, n + 1
    return show(t) + ('   [%d steps: normal form]' % n if s is None
                      else '   [no normal form in %d steps: DIVERGING]' % budget)

def parse(s):                            # t ::= name | \x. t | t1 t2 | (t)
    toks = (s.replace('λ', '\\').replace('(', ' ( ').replace(')', ' ) ')
            .replace('\\', ' \\ ').replace('.', ' . ').split())
    def atom(i):                         # ONE atom: name | (t) | \x. t
        if toks[i] == '(': r, j = term(i + 1); return r, j + 1
        if toks[i] == '\\': b, j = term(i + 3); return ('lam', toks[i + 1], b), j
        return ('var', toks[i]), i + 1   # error checks dropped for line count
    def term(i):                         # an atom, then absorb its argument run
        r, j = atom(i)
        while j < len(toks) and toks[j] != ')':
            a, j = atom(j); r = ('app', r, a)
        return r, j
    t, j = term(0); assert j == len(toks); return t

def show(t):                             # fully parenthesized, unambiguous
    if t[0] == 'var': return t[1]
    if t[0] == 'lam': return 'λ%s.%s' % (t[1], show(t[2]))
    return '(%s %s)' % (show(t[1]), show(t[2]))

if __name__ == '__main__':
    TRUE, K, IF = 'λa.λb.a', 'λx.λy.x', 'λc.λt.λe. c t e'
    TWO, THREE, MUL = 'λf.λx. f (f x)', 'λf.λx. f (f (f x))', 'λm.λn.λf. m (n f)'
    Y, OMEGA = 'λf. (λx. f (x x)) (λx. f (x x))', '(λx. x x) (λx. x x)'
    print('1) Church 2 x 3 =', run(parse('(%s) (%s) (%s)' % (MUL, TWO, THREE))))
    t = parse('(%s) (%s) (%s) (%s)' % (IF, TRUE, K, OMEGA))    # if true then K else Ω
    print('2) IF TRUE K Ω, normal     ->', run(t, 'normal'))
    print('   IF TRUE K Ω, applicative ->', run(t, 'applicative'))
    t = parse('(%s) g' % Y)             # g free: stand-in for any recursive body
    print('3) Y g, unfolding:')
    for n in range(4):
        print('   step', n, ':', show(t)); t = step(t)
```

```console
$ python3 lam.py
1) Church 2 x 3 = λf.λx.(f (f (f (f (f (f x))))))   [7 steps: normal form]
2) IF TRUE K Ω, normal     -> λx.λy.x   [5 steps: normal form]
   IF TRUE K Ω, applicative -> (λe.λx.λy.x (λx.(x x) λx.(x x)))   [no normal form in 500 steps: DIVERGING]
3) Y g, unfolding:
   step 0 : (λf.(λx.(f (x x)) λx.(f (x x))) g)
   step 1 : (λx.(g (x x)) λx.(g (x x)))
   step 2 : (g (λx.(g (x x)) λx.(g (x x))))
   step 3 : (g (g (λx.(g (x x)) λx.(g (x x)))))
```

What to notice: (1) six `f`s — the hand trace made mechanical; (2) normal
order returns `λx.λy.x` in 5 steps with Ω substituted but never entered, while
applicative order — look closely — already normalized the *function* (`λe.`
became K) and then burned all 500 steps waiting for Ω to become a value;
(3) step 2 is literally `g (Y g)`, step 3 is `g (g (Y g))` — one unfolding,
recursion as re-growth. Then poke the chapter's two delicate spots:

```console
$ python3 -c "import lam; print(lam.run(lam.parse('(λx.λy. x y) (λz. y)')))"
λy0.y   [2 steps: normal form]
```

That is section 1's capture example: `y0` is the fresh binder `sub` invented
to keep the argument's `y` free. For the minimal pair, run
`'(λx.λy. y) ((λx. x x) (λx. x x))'` — `λy.y` in one step; pass `'applicative'`
and it diverges. Swap `λy. y` for `λy. x` (use the argument) and even normal
order diverges: laziness only saves arguments the body never mentions.

## How to use this knowledge

- **Read function types as λ-terms.** `a -> b -> c` is `λa.λb.λc` — curried,
  partial application free; a suspiciously composable signature is a term
  whose β-reductions have not happened yet.
- **Diagnose hangs by asking which redex moved.** A refactor that turns a
  terminating program into a spinner usually moved work from a lazy position
  (substituted, maybe never used) into a strict one (evaluated before the
  call). Python is applicative everywhere — arguments, default-argument
  expressions (quiz 6).
- **Predict lazy-language memory.** Normal order defers every redex; sharing
  makes each one a live heap cell (a boxed thunk — module 01 chapter 18's
  Haskell row). A fold that never forces its accumulator builds a term the
  size of its input; `foldl'`, `seq`, bang patterns are applicative order
  chosen by hand (quiz 7).
- **Answer interviews with proofs, not slogans.** "Why can't we detect
  infinite loops?" — the two-case contradiction on `paradox`, five lines on a
  whiteboard. "Why `let`-polymorphism?" — rank 1 is the last decidable rung
  with principal types. "Why does GHC want annotations?" — System F inference
  is undecidable (Wells 1999).
- **Design DSLs knowing what `if` costs.** First-class control flow requires
  delayed evaluation — thunks, macros, or lazy arguments — because a strict
  evaluator cannot afford `if` as a function.

## Connections

- **Chapter 02 (this module)** — types added to this exact machine: progress
  + preservation; the self-application that powers Y is the term the typed
  calculus rejects.
- **Chapter 03 (this module)** — affine types count *uses* of a value: the
  used-versus-discarded distinction that decided Ω's fate, made checkable.
- **Chapter 04 (this module)** — deferred redexes are the liveness facts a
  lazy GC must prove; thunks are module 01 chapter 18's boxed cells.
- **Chapter 05 (this module)** — this chapter's undecidability theorems get
  teeth: what safe languages still permit.
- **Module 10, chapter 01 (the pipeline)** — this interpreter is the semantic
  spec a compiler implements; strictness analysis recovers applicative order
  inside a lazy language; inference is a compiler pass.
- **Module 01, chapter 18 (the elders)** — McCarthy lifted Church's λ-notation
  into Lisp; early Lisp's dynamic scoping is capture at language scale
  (quiz 8); Haskell shipped normal order.
- **Module 01, chapter 23 (the unification table)** — every row sits on this
  chapter's two axes: lazy vs strict, typed vs untyped.

## Quiz

1. In Python, `def IF(c, t, e): return t if c else e` cannot take a divergent
   branch: `IF(True, loop(), 1)` hangs *before IF is entered*. In one
   sentence each: why does `IF = λc.λt.λe. c t e` survive a divergent branch,
   and which evaluation-order property of Python is responsible?

2. What invariant does capture-avoiding substitution protect? Define free
   variables in one line, and state exactly what changes about a term's
   meaning when naive substitution lets a binder swallow a free variable of
   the argument.

3. Predict: which of these reaches a normal form under *normal order*, and
   what is it? (a) `(λx.λy. y) Ω` — (b) `(λx.λy. x) Ω (λa.λb.a)`, where
   `Ω = (λx. x x) (λx. x x)`.

4. Predict: `ADD = λm.λn.λf.λx. m f (n f x)`, `MUL = λm.λn.λf. m (n f)`. How
   many `f` applications are in the normal form of `ADD 2 3`, and of
   `MUL 3 2`? One sentence: why is `MUL` the shorter definition?

5. Derive: reduce `(λy.λx. y x) (λz. x)` with capture-avoiding substitution
   (the argument's `x` is free). Give the honest result and the naive
   non-renaming result, and name the function each computes.

6. Diagnose:

   ```python
   def add_tag(v, tag=[]):     # called as add_tag(1); add_tag(2)
       tag.append(v)
       return tag              # second call returns [1, 2] -- why?
   ```

   Explain in evaluation-order terms — when exactly does Python evaluate
   `tag=[]`, and how does that differ from what β-reduction does to an
   argument?

7. Diagnose: in Haskell, `foldl (+) 0 [1 .. 10^7]` can exhaust memory on tens
   of millions of live thunks while `foldl' (+) 0 [1 .. 10^7]` runs in
   constant heap. In λ terms, what does the accumulator grow into, and what
   does `foldl'` change?

8. Cross-chapter (module 01, chapter 18 — the elders): early Lisp was
   dynamically scoped — a free variable in a function resolved against the
   caller's bindings at call time. Explain why dynamic scoping is this
   chapter's capture bug promoted to a language rule, and what lexical
   scoping (Scheme, 1975) corresponds to in substitution semantics.

<details>
<summary>Answers</summary>

1. **Concept.** The λ `IF` survives because normal-order application
   substitutes branch arguments *unreduced*: `IF TRUE K Ω` puts Ω into the
   dead else-position, and `TRUE` discards it without entering it (5 steps
   in the lab). Python is applicative/strict: every argument is evaluated to
   a value *before* the call fires, so `loop()` runs regardless — which is
   why `if`, `&&`, and `?:` must be syntax, not functions, in strict
   languages.

2. **Concept.** Free variables are the occurrences no enclosing λ binds
   (`FV(λx. t) = FV(t) − {x}`) — the term's references to outside binders.
   The invariant: substitution must not change *which binder each occurrence
   points to*. Capture re-points a free variable of the argument at a binder
   inside the body, silently changing the program's meaning — the chapter's
   free `y` ends up bound to the inner `λy` instead of the outside world.

3. **Predict.** (a) `λy.y` in one step: the root β fires first, Ω is
   substituted unreduced, and the body never mentions `x` — Ω is discarded
   without running. (b) Diverges: `x` *is* used, so Ω is substituted into the
   body and eventually forced as the result. Laziness defers work; it does
   not delete work the answer depends on.

4. **Predict.** `ADD 2 3` → five `f`s (f² composed after f³); `MUL 3 2` →
   six. `MUL` is shorter because multiplication is composition of the
   numerals themselves — `m (n f)` hands the function `n f` to `m`,
   `(f³)² = f⁶` — while addition must compose *iterated functions* at the
   value level, which costs the extra binder and application.

5. **Derive.** Honest (rename the inner binder — the argument `λz. x` has
   `x` free, and the body binds `x` over the landing zone):
   `(λy.λx'. y x') (λz. x) →β λx'. (λz. x) x' →β λx'. x` — a constant
   function returning the *free* `x`. Naive: `λx. (λz. x) x →β λx. x` — the
   identity. Capture turned "return the outer x" into "return my argument";
   the naive answer is the more familiar function, which is why the bug is
   hard to spot.

6. **Diagnose.** Python evaluates the default expression **once, at `def`
   time** — the extreme strict position: β-reduction would re-create the
   argument at each application, but Python pre-evaluates it and closes over
   the resulting cell, so every call without an explicit `tag` shares one
   list; hence `[1, 2]`. The fix (`tag=None; if tag is None: tag = []`)
   restores per-application creation by hand.

7. **Diagnose.** Normal order defers each `(+)` redex, so the accumulator
   grows into one unevaluated term `((((0+1)+2)+…) + 10⁷` — a 10-million-deep
   chain of live, boxed thunks (sharing must keep them for the final demand),
   unreleasable by the GC. `foldl'` *forces* the accumulator at every step:
   one applicative-order reduction smuggled into the lazy language. The
   program always terminated; the memory for deferred redexes killed it.

8. **Cross-chapter.** Dynamic scoping makes every free variable re-bind to
   whoever calls the function — the caller's environment plays the
   substitution site, so a free `y` can be swallowed by a caller's `y` exactly
   as in naive substitution, at language scale: the same function computes
   different things per call context. Lexical scoping is capture-avoidance as
   a language guarantee — each free variable points at the binder textually
   enclosing it at definition, and environment frames (α-renaming) keep
   callers from swallowing it. Scheme (1975) restored the λ-calculus
   discipline to the Lisp family; Common Lisp kept both and made you choose
   per function.

</details>
