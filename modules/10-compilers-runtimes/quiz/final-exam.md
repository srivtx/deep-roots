# Module 10 final exam — compilers & runtime internals

Twelve questions across all five chapters. Closed book. Every answer is
worked, not stated. Scoring: 11+ = you own this module; 8-10 = solid,
revisit the flagged chapters; <8 = the chapter quizzes are the study path.

**Part I — the pipeline and its passes (ch 01-02)**

1. **(Concept)** Six language front ends, five target architectures:
   compute the component count with and without a shared IR, name the two
   costs of the bargain, and say why Go built its own compiler anyway.

2. **(Predict, cross-module 01 ch04)**

    ```c
    int pick(int c, int x, int y) {
        int r;
        if (c) r = x * 2; else r = y + 3;
        return r + 1;
    }
    ```

    At `-O2` (gcc 14.2), predict: (a) how many inputs `r`'s phi has, and
    whether `c` gets one; (b) what mem2reg does to `r`'s storage; (c) the
    final code's shape, branch included; (d) the frame size, and which
    stage decides it.

3. **(Diagnosis)** At `-O2`, gdb reports `acc` as "value has been optimized
   out"; a colleague files "the optimizer deleted my variable." Name the
   pass, state what happened, say why `-O0` shows every local (allocator
   and policy), and give two recoveries short of an `-O0` rebuild.

4. **(Diagnosis)** Production crash:

    ```c
    int get_status(sensor *s) {
        log_line("raw=%d", s->raw);   /* line 3: crash lands HERE */
        if (s == NULL) return -1;     /* line 4: "we check!"      */
        return s->raw;
    }
    ```

    The core faults at line 3 with no null check compiled — gcc 14.2
    `-O2` emits `movl (%rdi), %eax; ret` here. Why is the check dead, why
    is `-Wall` silent, what restores it, and which tools catch the shape?

5. **(Concept)** Distinguish *may-alias* from *must-alias*, naming one
   optimization each licenses or blocks. Then unify C's `restrict` and
   Rust's `&mut` in one sentence, including what LLVM sees on the Rust
   side.

**Part II — register allocation (ch 03)**

6. **(Predict/derive)** Straight-line body:

    ```c
    long t1 = a + b;    /* 1 */
    long t2 = b + c;    /* 2 */
    long t3 = a - c;    /* 3 */
    long t4 = t1 * t2;  /* 4 */
    long t5 = t3 + t4;  /* 5 */
    return t4 - t5;     /* 6 */
    ```

    (a) Draw the live ranges, state the courtesy rule, build the graph. (b) Minimum k, with a coloring. (c) With k = 2 one
    range must spill: rank a range used twice at loop depth 2 against one
    used fifty times at top level — which wins, by what factor?

7. **(Diagnosis)** An image kernel ran 82 ns/pixel at `-O2`; after
   `clang -O3`, 94 ns/pixel, with `movq %rax, -48(%rsp)` plus a matching
   load inside the hot loop. What happened, and two source-level fixes?
   The twist: a leaf sibling at `-O2` keeps five values in stack slots yet
   emits no `subq` — where do they live, and why is that legal?

**Part III — the compiler's GC duties (ch 04)**

8. **(Predict)** (a) Go — predict the `-gcflags='-m'` remark for each
   function, and for `c` say whose allocation the remark triggers:

    ```go
    var sink *int

    func a() *int  { x := 1; return &x }
    func b(p *int) { *p = 7 }
    func c(p *int) { sink = p }
    ```

    (b) Java (JDK 21, 64-bit, compressed oops): a 100M-iteration loop of
    `new Point(int, int)`, every instance escaping — bytes allocated, and
    what fills each object? Same loop, no escaping store — bytes, and
    which compiler?

9. **(Concept + diagnosis)** (a) The card-marking barrier is emitted
   *after* the pointer store: what failure would marking *before* cause,
   what invariant does store-then-mark protect, and which barrier style
   runs before the store? (b) A Java 21 service stalls 650 ms; GC log:
   6 ms pause, `Reaching safepoint: 640000000 ns`. What class of
   bug, why is the GC innocent, and why do loops poll on the backedge?
   (c) Its safepoint-sampled profiler calls one modest loop 90% of the
   program, contradicting wall clock — name the artifact and fix.

**Part IV — the ABI and its taxes (ch 05)**

10. **(Predict/derive)** System V AMD64 — state the arrival registers for:
    (a) `f(int, double, long)`; (b) `g(struct { double d; long n; }, int)`;
    (c) a call with seven `long` arguments — where does the last land?
    (d) a function returning
    `struct { char name[24]; long total; }` (32 bytes) — how does the value
    come back?

11. **(Cross-module 04 ch04)** In a stripped Linux binary, a read-only
    32-byte region reads `0, ptr, ptr, ptr`: the first `ptr` leads to a
    structure containing `"7Derived"`, the other two to functions, and
    objects of some class carry a first member equal to region address +
    16. Identify the region and each slot, count the virtual functions,
    explain the +16, and say how `"7Derived"` survives stripping.

12. **(Diagnosis)** A ctypes loop calling `lib.add2(1, 2)` costs 342
    ns/call; the same loop in C costs 0.6 ns, a pure-Python call ~53 ns —
    the boundary costs more than the interpreter. Trace where the
    nanoseconds go, and give the architectural fix.

<details>
<summary>Answers</summary>

**Part I**

1. Without a shared IR: 6 × 5 = **30 complete compilers**. With one:
   6 front ends + 5 back ends = **11 components** — why Rust, Swift,
   and Julia sit on LLVM. Cost one: the IR is a *common denominator* —
   language-specific wins (Rust's aliasing, Go's escape facts) leak away.
   Cost two: it becomes a *stability contract*; changing it is a
   coordinated event. Go kept its own compiler because escape analysis
   needs source-level facts LLVM does not represent. (ch01)

2. (a) One phi with **two inputs** — `r = PHI <x*2, y+3>`; `c` gets none:
   parameters are assigned once; SSA mints names only for assignments.
   (b) mem2reg promoted the alloca/load/store pattern to SSA values — `r`
   is a value, zero stack slots left. (c) If-conversion: both arms
   compute unconditionally, one `cmov` selects, the branch vanishes;
   `r + 1` folds into the arms. (d)
   **Zero bytes**: no spills and a leaf, so `pro_and_epilogue` emits
   nothing; the only frame bytes are the caller's 8-byte return address
   (module 01 ch04's minimal frame). (ch01)

3. **Promotion, not deletion.** mem2reg / SSA construction turned the
   local's load/store pattern into SSA values that register allocation
   kept in registers or folded — the value exists, but there is no
   *storage* to print. `-O0` shows everything because its trivial
   allocator (`regalloc=fast`) gives every value its own frame slot,
   always. Recoveries: recompile with `-Og`, or find the carrying register
   via `-fverbose-asm` + `info registers`. (ch01, ch03)

4. The dereference runs first in dataflow: a null `s` at line 3
   is already UB, so the optimizer assumes it never happens — `s` is
   provably non-null afterwards, the line-4 branch is dead under the
   as-if rule, collapsing the function to `movl (%rdi), %eax; ret`.
   `-Wall` is silent because nothing is violated (gcc 14.2.0). Fix: check
   before *any* dereference; in review, look for derefs lexically above
   null checks. clang's static
   analyzer and gcc `-fanalyzer` catch it. (ch02)

5. **May-alias** — "cannot prove they differ" — *blocks* redundant-load
   elimination, LICM, and vectorization, because a store through `q`
   might invalidate what was assumed about `p`. **Must-alias** — provably
   same object and offset — licenses store *forwarding*: the load becomes
   the stored value, no round-trip. Unification: **Rust's borrow checker
   is an alias analysis that runs at compile time and cannot be wrong** —
   `&mut` lowers with LLVM's `noalias` attribute, giving the optimizer
   for free what C gets only where someone wrote `restrict`. (ch02)

**Part II**

6. (a) Ranges: t1 [1,4], t2 [2,4], t3 [3,5], t4 [4,6], t5 [5,6]. Courtesy
   rule: a value defined where another dies reuses its register — no
   edge. Edges: t1–t2, t1–t3, t2–t3, t3–t4, t4–t5; *no* t1–t4, t2–t4,
   or t3–t5 (birth line = death line).

    ```text
       t1 ---- t2
         \    /
          t3 ---- t4 ---- t5
    ```

   (b) The triangle forces **k = 3**: t1 = R1, t2 = R2, t3 = R3; t4 takes
   R1 (t1 is dead), t5 takes R2. (c) Spill cost ≈ uses × frequency, loop
   depth weighting a level-n use as ~10ⁿ: 2 × 10² = 200 vs 50 — the loop
   range wins by **4×**. (ch03)

7. `-O3` unrolled the loop (clang unrolls at `-O3`; GCC needs
   `-funroll-loops`), raising simultaneously-live values; an
   accumulator's live range now spans the hot loop, lost its color, and
   pays store + load per iteration — ~12 ns/pixel of L1 round-trips.
   Fixes: tile to ~8 live accumulators, or keep that unit at `-O2`. The
   twist: the leaf's five slots live in
   the **128-byte red zone** below `rsp` — leaves may spill there free
   (5 × 8 = 40 bytes fits), so the prologue claims zero and the slots are
   invisible to frame walks (module 01 ch04). (ch03)

**Part III**

8. (a) `a`: `moved to heap: x`. `b`: `p does not escape` — only the
   pointee is written; callers may pass stack addresses. `c`: `leaking
   param: p` — stored into a global; the
   allocation triggers at the *call site*: callers heap-allocate their
   argument. (b) Escaping: 100M × 24 B = **2,400,000,000 bytes** — 8 B
   mark word + 4 B compressed klass + 2 × 4 B fields, rounded to 24 by
   alignment. No escaping store: **0 bytes** — C2's scalar replacement
   dissolved every Point into registers (escape analysis is SROA for a
   GC language); C1 and the interpreter still allocate: a compile-time
   decision, not a language guarantee. (ch04, ch02)

9. (a) Mark first and there is a window where the card is dirty but the
   pointer is not yet in the object: a concurrent collector scans the
   card, finds no young pointer, and moves on; the store lands and the
   edge is forever unrecorded — the next minor GC frees a live young
   object. Store-then-mark enforces "card dirty ⇒ store
   visible". **SATB** runs before the store: it protects
   the *old* value — overwriting `p.f` can destroy the only path to an
   object, unrecoverable afterwards — logging it into a buffer.
   (b) **Time-to-safepoint, not GC**: 6 ms of GC work, 640 ms of waiting
   for one thread to reach a poll — a loop without a poll. Loops poll on
   the backedge because straight-line code returns in bounded time but a
   hot loop may not return for hours; unbounded residence holds the
   quorum hostage. (c) **Safepoint bias**: samples land where threads
   stop — at polls on backedges — overweighting loops; use a signal-based
   sampler (async-profiler). (ch04)

**Part IV**

10. (a) `edi`, `xmm0`, `rsi` — the INTEGER and SSE banks fill
    independently. (b) First-8-bytes classification: each 8-byte chunk
    separately — double → `xmm0`, long → `rdi`; then `int` → `rsi`: one
    argument, two banks. (c) Six longs fill `rdi, rsi, rdx, rcx, r8, r9`;
    the **7th is the first to pay stack traffic**, at `[rsp+8]` on entry,
    `rsp` 16-byte aligned at the call. (d) Over 16 bytes the return goes
    through memory: hidden destination pointer as the first argument in
    `rdi`; the callee writes there, returns the address in `rax`.
    (ch05)

11. A **vtable** — `_ZTV7Derived` in `.data.rel.ro`. Slot 0:
    **offset-to-top** (0 — primary base). Slot 1: the **typeinfo
    pointer** (`_ZTI7Derived`). Slots 2-3: the **two virtual functions**,
    declaration order. The vptr is region + 16 because the **address
    point** skips the 16-byte prologue — calls index from the first slot;
    `vptr[-1]`/`vptr[-2]` reach the metadata: module 01 ch06's layout;
    module 04 ch04 recovers this object stripped.
    `"7Derived"` is an `_ZTS` typeinfo-name string in `.rodata` —
    length-prefixed data, not a symbol: stripping removes symbol names,
    not referenced bytes. (ch05)

12. The FFI call is an expedition: ctypes converts each Python `int`
    into a C `int` and boxes the result into a fresh Python object;
    runtimes switch stacks and state; cgo/JNI pin or copy references
    against a moving GC. The cluster — **marshalling copies, stack
    switches, pinning** — dwarfs the 0.6 ns `call`. Fix: **batch across
    the boundary** — move the loop into C so one crossing does a million
    units of work; pass pointers, not per-call conversions. (ch05)

</details>
