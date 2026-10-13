# Module 19 final exam — Programming language theory & type systems

Twelve questions in five parts — three on the minimal machine (chapter 01),
three on types as theorems (chapter 02), two on memory safety as a theorem
(03), two on GC as proof search (04), two on the limits (05). Closed book;
every answer is worked, not stated — the derivations are the point. Scoring:
11+ = you own this module; 8-10 = solid, revisit the flagged chapters; <8 =
the chapter quizzes are the study path.

**Part I — the minimal machine (chapter 01)**

1. **Predict.** With `TRUE = λa.λb.a`, `FALSE = λa.λb.b`, define
   `AND = λp.λq. p q FALSE` and `OR = λp.λq. p TRUE q`, and let
   `Ω = (λx.x x)(λx.x x)`. For each of `AND FALSE Ω`, `AND TRUE Ω`,
   `OR TRUE Ω`: does normal order terminate? Give the normal form with a
   short trace, then state in one sentence which argument position each
   operator is strict in. Finally: why must `&&` in C be syntax rather
   than a function?

2. **Diagnose.** A log scanner ships v1 and is refactored to v2 "for
   consistency"; after the change the service hangs on a live tail where
   v1 answered in milliseconds:

   ```python
   hit = any(is_bad(l) for l in tail_log(sys.stdin))   # v1: returns fast
   lines = [l for l in tail_log(sys.stdin)]            # v2: hangs here
   hit = any(is_bad(l) for l in lines)
   ```

   In evaluation-order terms: which redexes moved, from what position to
   what position? What is `any`'s short-circuit in λ terms (name the
   chapter-01 shape)? What property of the argument does v2 wait for that
   Ω also never acquires?

3. **Derive.** Strict languages cannot ship `Y`; they ship `Z`.
   (a) Unfold `Y g` one β-step, then show that evaluating the result under
   applicative order never applies `g` — trace two more steps and name the
   regrowing pattern. (b) Unfold `Z g` one step, where
   `Z = λf.(λx.f (λv.x x v))(λx.f (λv.x x v))`, and show that `g` now
   receives a value. (c) Which conversion is the `λv` wrapper an instance
   of, and why does call-by-value need it?

**Part II — types as theorems (chapter 02)**

4. **Derive.** Prove preservation for the `if` construct in full: given
   `⊢ if t₁ then t₂ else t₃ : T` by rule T-If, with premise derivations
   D₁ : `⊢ t₁ : Bool`, D₂ : `⊢ t₂ : T`, D₃ : `⊢ t₃ : T`, and
   `if t₁ then t₂ else t₃ → t'`, case on the three evaluation rules and
   show `⊢ t' : T` each time — naming where the induction hypothesis is
   used, where a premise does the work alone, and where T-If's
   branches-have-the-same-type premise is load-bearing.

5. **Derive.** (a) Derive the function-subtyping rule in three lines from
   what callers may pass and what they need back: exactly when is
   `S₁→S₂ <: T₁→T₂`? (b) Apply it to records: work the store that makes
   depth subtyping on a *mutable* field unsound — alias an
   `Employee {birthday: RichDate}` as a `Person {birthday: Date}`, write a
   plain `Date` through the narrow view, then use the wide view — and say
   which two positions a mutable field occupies at once. (c) One
   sentence: why does C#'s `IEnumerable<out T>` escape the problem?

6. **Diagnose.** A Java library ships `static <T> T first(List<T> xs)`
   documented as "first element, or null if empty"; callers de-reference
   and NPE. Diagnose with (a) parametricity — enumerate what a genuine
   inhabitant of `∀α. List<α> → α` can compute, and what `null` therefore
   is; (b) why no runtime check catches it — what erasure did to the
   proof, and which chapter-02 soundness hole `null` is; (c) the honest
   return type, and what it re-introduces from module 01 ch06.

**Part III — memory safety as a theorem (chapter 03)**

7. **Derive.** Suppose `&mut T` were covariant in `T`, so `&mut &'long i32`
   coerced to `&mut &'short i32` and back. Both programs below must end in
   a use-after-free with no `unsafe` block and no cast anywhere.
   (a) *Shortening, write side* — complete `plant`, then name the line
   that plants the dangling reference and the line that reads it:

   ```rust
   fn plant(r: &mut &'long i32, s: &'short i32) { /* coercion + store */ }
   fn main() {
       let long_lived = 42;                      // outlives everything below
       let mut slot: &'long i32 = &long_lived;   // slot's type promises 'long
       { let temp = 7;
         plant(&mut slot, &temp); }              // temp dies at this brace
       println!("{}", slot);                     // the read
   }
   ```

   (b) *Lengthening, read side* — construct the symmetric program in which
   `&mut &'short i32` coerced to `&mut &'long i32` lets a short reference
   escape its region *through* the slot: fill in the coercion-and-read in
   `escape` and the body of the block, and mark the plant and read lines:

   ```rust
   fn escape(r: &mut &'short i32) -> &'long i32 { /* coercion + read */ }
   fn main() {
       let dangling: &'long i32 = {
           let temp = 7;
           /* declare slot; plant; call escape */
       };                                        // temp dies at this brace
       println!("{}", dangling);                 // the read
   }
   ```

   (c) State the rule both programs force. (d) Why is the identical
   coercion on a *shared* `& &'long i32` safe — and which chapter-02
   runtime exception is that rule the compile-time version of?

8. **Diagnose.** Place Zig on the chapter's safety spectrum, justifying
   each clause from its design: allocations go through explicit allocator
   parameters with manual frees; slices carry lengths and indexing is
   bounds-checked in Debug and ReleaseSafe but the checks are compiled out
   in ReleaseFast; there is no GC and no aliasing or lifetime proof; C
   interop is a first-class feature. For each of the four clauses (a-d):
   who proves it, when — or nobody? Which two existing spectrum rows sit
   nearest, and how does Zig differ from each? What is ReleaseFast
   choosing, in chapter 05's proof-schedule frame? Which mitigation-ladder
   rungs belong in a Zig CI?

**Part IV — GC as proof search (chapter 04)**

9. **Derive.** A JVM service: 12 GiB live heap; one marking thread
   sustains 400 MB/s. (a) The serial full-collection pause? (b) With 16
   parallel markers, ideally? (c) The SLO is 200 ms — verdict, and what
   wall do additional threads hit? (d) The two structural moves the field
   uses instead? (e) The nursery is 1 GiB with 8 MiB of survivors;
   evacuation runs at 2 GiB/s — the young-collection pause, and why does
   the answer not contain the 1 GiB? (f) Card table: 512-byte cards, one
   byte each — table size, fraction of the heap, and the cost of one
   stray old-to-young store.

10. **Diagnose.** A 2013-era Java service on CMS: under traffic spikes the
    GC log shows `concurrent mode failure` and pauses jump from ~100 ms
    to 8 s; `jstat -gcutil` shows the old generation (O) pinned near 100.
    What exactly failed, in proof terms? Why is the fallback the worst
    possible collection — which number from question 9 does it
    re-create? Which structural property of CMS, connected to the
    address proof, made failure inevitable? What did G1 change — and
    what does the write barrier keep correct but never make fast?

**Part V — what types cannot do (chapter 05)**

11. **Predict.** A static proof costs 90 s once per release; the
    equivalent runtime check costs 20 ns per execution. (a) Break-even
    execution count? (b) A nightly batch job run for ten years: which
    schedule wins, by roughly how much? (c) A service at 4,000 calls/s:
    when does it cross the break-even, and what does one year of dynamic
    proof cost against the 90 s? (d) Only 30% of the code's branches ever
    execute in production — how does that change each side's bill, and
    which side is pre-paying for exactly the paths you hope never run?

12. **Diagnose.** A rover-control team must choose: (A) Rust plus a
    hardware watchdog, or (B) a total language (termination-checked,
    Agda-style) with the control loop as codata. For each: what does the
    type system prove, and what is left for the watchdog, the fuel
    argument, or the runtime? Why can't (B) write the loop as ordinary
    recursion — what does lying look like, and what does codata's
    productivity promise? Which impossibility are both responding to,
    and which two more does neither touch?

<details>
<summary>Answers</summary>

**Part I**

1. `AND FALSE Ω` → `(λq. FALSE q FALSE) Ω` → `FALSE Ω FALSE` →
   `(λb.b) FALSE` → **FALSE** — Ω was substituted into `FALSE`'s argument
   slot and discarded without being entered. `AND TRUE Ω` →
   `TRUE Ω FALSE` → `(λb.Ω) FALSE` → **Ω: diverges** — `TRUE` returns its
   first argument, so the body *uses* q, and the substituted Ω becomes the
   result and reduces forever. `OR TRUE Ω` → `TRUE TRUE Ω` →
   `(λb.TRUE) Ω` → **TRUE**, Ω discarded at the last step. Strictness:
   **AND is strict in its second argument exactly when the first is TRUE;
   OR exactly when the first is FALSE** — short-circuiting is a property
   of the encoded operators, not special syntax. It only holds under
   normal order: strict languages evaluate every argument before the
   call fires, so an `and(f, g)` function would run `g` regardless — why
   `&&`, `?:`, and `if` must be syntax in C-family languages (ch01).

2. The refactor moved the `is_bad(l)` redexes from a **substituted,
   demanded-lazily position to an evaluated-before-the-call position**.
   v1: the generator hands `any` one element at a time; `any` stops at the
   first truthy value, so every later `is_bad(l)` redex is dead-branch
   material, never fired — question 1's `AND FALSE Ω` shape, with `any`'s
   short-circuit as the `IF` consumer. v2: Python is applicative order —
   the argument `[l for l in tail_log(...)]` must reduce to a **value**
   before `any` is even entered, and an infinite tail never becomes one:
   exactly the property Ω never acquires. The fix is to pass the
   generator, not the materialized list — diagnose hangs by asking
   *which redex moved* (ch01).

3. (a) `Y g →β (λx. g (x x)) (λx. g (x x))` — call that argument A. A is a
   λ, hence a value, so the application fires: `→β g (A A)`. Now
   applicative order demands the *argument* `A A` be a value before `g`
   runs; `A A →β g (A A)` — each evaluation of the argument regrows the
   same application: **the argument is its own reduct, Ω-shaped; `g` is
   never called**. (b) `Z g →β (λx. g (λv.x x v)) B` (B the second half),
   which fires to `g (λv. B B v)` — the argument is a λ, **a value**:
   `g` is applied immediately. When `g`'s body invokes that thunk on a
   value `v`, `B B v →β g (λv. B B v) v` — one unfolding per recursive
   call, on demand. (c) **η-expansion** (`λv. x x v ≡ x x`,
   `v ∉ FV(x x)`): it delays self-application from "now" to "when the
   recursion is demanded" — exactly what call-by-value needs, why
   OCaml's and Scala's `fix` are Z, not Y (ch01).

**Part II**

4. Case on the step rule:
   - **E-IfTrue** (`t₁ = true`, `t' = t₂`): D₂ is already a derivation of
     `⊢ t₂ : T` — the conclusion, directly. **No IH needed**: the step
     *selects* an already-typed premise.
   - **E-IfFalse**: symmetric; D₃ supplies `⊢ t₃ : T`.
   - **E-If** (congruence, `t₁ → t₁'`): **IH on D₁** gives
     `⊢ t₁' : Bool`; re-apply T-If with D₂, D₃ unchanged:
     `⊢ if t₁' then t₂ else t₃ : T` — preserved.
   The same-type premise is load-bearing in the first two cases: the
   term has type T, the step yields t₂ (or t₃), and only the premise
   guarantees the selected branch *has* type T — drop it and E-IfTrue
   steps to an arbitrary-typed branch with nothing checked: preservation
   dead. The moral matches the application case: work rules select
   premises, congruence rules need the IH (ch02's Wright–Felleisen
   section).

5. (a) Use `f : S₁→S₂` where `T₁→T₂` is expected. The context passes
   `T₁` values; `f` must accept every one: **`T₁ <: S₁` — argument
   contravariant**. `f` returns `S₂`; the context needs `T₂` back:
   **`S₂ <: T₂` — result covariant**. ∴ `S₁→S₂ <: T₁→T₂ iff T₁ <: S₁ and
   T₂ <: S₂` — inputs flip, outputs don't. (b) `Employee <: Person`
   requires `RichDate <: Date` (depth subtyping). Through the Person view
   the field is writable: `personView.birthday = plainDate` typechecks —
   it is a `Date` field there. Through the Employee view,
   `emp.birthday.richOnly()` — a plain `Date` has no `richOnly`: the
   "well-typed" call detonates. A mutable field is **an input
   and an output position at once** — stored into and read from — so it
   must be invariant: the function rule applied to a field carrying both
   arrows. (c) `IEnumerable<out T>` exposes `T` only in *output* position
   — nothing consumes a `T`, so there is no store to lie through, and
   covariance is sound by the same derivation (ch02's variance section).

6. (a) A parametric `first` receives only the list — elements are opaque
   α; it cannot inspect, compare, or **construct** an α. It can only
   return one of the elements (a position-shaped move) or diverge/throw —
   in a total world the type is uninhabited, which is why honest
   list-heads return `Maybe α`. **`null` is a
   fabricated α** — a value claiming membership in every type, Hoare's
   billion-dollar lie: the theorem `∀α. List<α> → α` became provable with
   evidence that proves nothing. (b) Erasure compiled `T` to `Object`,
   so `first` returns an untagged reference and the call site's `checkcast`
   is the only proof — and **null passes every cast** (the universal
   inhabitant cannot be refuted). Reified arrays *do* catch wrong
   element types at the store (`ArrayStoreException`); erased generics
   cannot. (c) `Optional<T>` —
   the honest sum "value or absent" whose cases the caller must at least
   see; its cost is the discriminant: **module 01 ch06's tagged-value
   layout returning as the certificate** (ch02's null repair).

**Part III**

7. (a) The completion is `let shrunk: &mut &'short i32 = r; *shrunk = s;`
   — covariance would coerce `r` down, and the store writes a `&'short`
   into a slot whose declared type still says `&'long`. **Plant line:
   `*shrunk = s`.** `temp` dies at the inner brace; **read line:
   `println!("{}", slot)`** — legal, because the slot's type promises
   `'long`; it reads `&temp`: use-after-free, no cast anywhere.
   (b) The symmetric construction: `fn escape(r: &mut &'short i32) ->
   &'long i32 { let widened: &mut &'long i32 = r; *widened }` — the
   coercion at the binding, the extraction at `*widened`. Caller: inside
   the block, `let mut slot: &'short i32 = &temp;` (**the plant — a
   short-lived reference enters a channel now typed long**), then
   `escape(&mut slot)`; the block's value binds outside as
   `dangling: &'long i32`, so it may outlive `temp`'s brace; **read line:
   the final `println!`**. Shortening corrupts the slot *for
   better-informed readers*; lengthening exports the short reference
   *past its region* — both manufacture dangling pointers through the
   type's own promises. (c) **`&'a mut T` is invariant in `T`**: a
   mutable slot must accept exactly what its type says, in both
   directions, or every write becomes a runtime check. (d) A shared
   `& &'long i32` cannot be *written through* — shortening it only
   forgets precision nobody can act on; readers holding the longer type
   still read the true, longer-lived value. That is chapter 02's
   covariant-arrays theorem hoisted to compile time: **invariance is
   `ArrayStoreException` refused at the door** (ch03's variance section).

8. Clause by clause: **(a) own-access — nobody**: wild pointers survive
   to the MMU's 3-bit, 4 KiB-page check, C's row. **(b) bounds — the
   runtime, at each access, in Debug/ReleaseSafe**: slices carry lengths
   and the check fires on access — Go's row; in ReleaseFast, nobody —
   C's row again. **(c) byte-level punning — nobody, but explicit**:
   casts must be written (`@bitCast`), so the proof can only be
   suspended at greppable sites — an `unsafe`-shaped membrane without a
   compiler-held invariant list. **(d) post-lifetime — nobody**:
   manual frees make use-after-free first-class; `defer` gives
   scope-end determinism for frees you still must *remember to write* —
   RAII's intuition with no aliasing proof, C++'s row minus the refcount.
   Nearest rows: **C** — same (a), (c), (d), but Zig buys (b) the way Go
   does; and **Go** — same runtime bounds answer, but Go's GC closes
   clause (d) where Zig leaves it manual. ReleaseFast is not "a faster
   proof" — it is **the proof rescheduled to never**: check deleted,
   guarantee gone — ch04's Epsilon "no proof" row as a build mode;
   Debug vs ReleaseFast is one proof at two schedules (ch05's frame).
   CI ladder: suite in Debug (checks on), ASan for (b) and (d), MTE
   where present (15/16 caught), fuzz the C-interop membrane — that is
   the TCB (ch03).

**Part IV**

9. (a) 12 GiB = 12,288 MiB; 12,288 / 400 MB/s ≈ **31 s** stop-the-world.
   (b) 12,288 / 16 ≈ 768 MiB per marker → **~1.9 s**, ideal. (c) **9.6×
   over the 200 ms SLO**, and more threads buy little: each visit is a
   compulsory ~100 ns DRAM miss, so pointer-chasing marking is
   **miss-ladder-capped** — the memory bus, not the algorithm, limits
   the rate. (d)
   **Generational** — shrink the proof's subject: prove only the nursery,
   where 95-99% of objects are already dead and cost tracks survivors;
   **concurrent** — run the proof beside the mutator, paying a write
   barrier (CMS, G1, ZGC). (e) 8 MiB / 2 GiB/s = **4 ms**, plus root and
   dirty-card scan: a young collection *copies survivors*; the other
   ~1,020 MiB is reclaimed as the side effect of emptying the nursery —
   **cost ∝ survivor bytes, not allocation volume**, which is why the
   1 GiB appears nowhere. (f) 12 GiB / 512 B = 25,165,824 cards, one
   byte each = **24 MiB** = 24 MiB / 12 GiB = 1/512 ≈ **0.2%**
   of heap, always mapped; one stray old-to-young store dirties one card
   — a **512-byte** re-scan at the next young GC (ch04).

10. **Concurrent mode failure**: the concurrent mark-and-sweep could not
    finish before allocation filled the old generation — the proof could
    not keep pace with its subject's growth. The collector abandons the
    cycle and falls back to a **full serial stop-the-world compacting**
    collection on the already-huge heap — question 9(a)'s ~31 s at the
    worst moment, plus compaction. The structural cause: **CMS never
    compacted old space** — object identity is an address, compaction
    rewrites every pointer (forwarding pointers, Lisp-2's three passes),
    a cost CMS refused in the steady state; fragmentation accumulated
    until promotion could not fit in the holes — the O column pinned
    near 100 was the failure announced in advance. G1's redesign: ~2048
    equal regions and a **pause budget** — `-XX:MaxGCPauseMillis`
    (default 200 ms), spent by a collection-set picker that predicts
    each region's evacuation cost and evacuates only what fits (young
    plus mixed old): a pause that is a slice of the proof sized to an
    SLO, at -5-15% throughput. The write barrier (Yuasa's snapshot:
    grey the overwritten value, ~2-20 ns/store) keeps the tri-color
    invariant — **black never points to white** — the proof *correct*;
    pace is not its job, and pace is what failed (ch04).

**Part V**

11. (a) 90 s / 20 ns = **4.5 × 10⁹ executions**. (b) Ten years nightly
    ≈ 3,650 runs: dynamic pays 3,650 × 20 ns ≈ **73 µs against 90 s
    once** — dynamic wins by ~10⁶×. (c) 4,000 calls/s =
    3.5 × 10⁸/day: break-even crossed in **~13 days**; a year is
    1.26 × 10¹¹ executions paying 2,520 s ≈ **42 min versus 90 s once**
    — static wins ~28×, never billing mid-request. (d) The static bill
    already covered all paths — the 70% never executed cost the same
    90 s, since static pays for **all paths, including never-executed
    ones**; dynamic pays nothing for them, but the 30% that do run pay
    forever. The unexecuted 70% are the error handlers you hope never
    fire: **static is pre-payment for the disaster path**, failing as a
    rejected build rather than a mid-request TypeError (ch05).

12. (A) Rust proves **soundness — never stuck — hence memory safety, not
    termination**: `fn f() -> ! { f() }` typechecks (the never type
    advertises divergence); a wedged control loop violates no Rust
    theorem. The watchdog is the **termination proof scheduled in
    hardware** — "no progress by T, then reset" — the one check no type
    system can give you; the `unsafe` census remains the audit surface.
    (B) The total language makes termination a typing obligation —
    calls on **structurally smaller** arguments — so the loop cannot be
    written without lying; the lie is **fuel** (`serve n` bounds
    iterations: a watchdog moved into the type). The honest form is
    **codata**: the control loop as a coinductive process whose every
    observation delivers its next step in finite time —
    **productivity**: not "it stops," but "every step arrives." Both
    respond to **impossibility 1 (termination)**; industry chose (A):
    over-accept, pay at runtime. Neither touches **impossibility 3, the
    world** — sensor reads and bus faults cross the process boundary no
    type cages — nor **impossibility 2**: "never command a burn that
    empties the tank" is a spec, not a type — filled by tests, module
    21's machinery, or nothing (ch05; ch01's diagonalization, one last
    time).

</details>
