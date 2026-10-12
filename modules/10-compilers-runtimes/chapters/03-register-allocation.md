# Chapter 03 — Register allocation

> **The 30-second version.** After optimization your program names as
> many variables as it likes, but the CPU offers only ~16 general
> registers. Register allocation resolves that scarcity: compute when each
> value is needed (its *live range*), build a graph of which ranges overlap
> (*interference*), and assign registers by coloring that graph. Ranges
> that lose the coloring get a *spill slot* — a stack address — and that
> is the moment the stack frame's contents are decided.

## The arithmetic that forces the problem

At the end of the pipeline (Chapter 01), SSA has given every value its own
name — a *virtual register* — and the optimizer was encouraged to invent
as many as it liked; a function easily holds 50–500. Now count homes:
x86-64 (System V AMD64, the platform marked throughout) has 16 general
registers, `rsp` is spoken for, so **15 are allocatable** — 14 with a
frame pointer. ARM64: 31, several ABI-reserved. Two values can share a
register only if never needed *simultaneously*; everything below is the
machinery for finding that sharing and pricing the failures.

## Live ranges: deriving when a value must exist

A value needs a register only while *live*: from its definition to its
**last use**. The subtlety: "last use" is not knowable reading forward
from the def — liveness is a **backward** dataflow, from each use back to
the defs that feed it.

```c
long f(long x) {
    long a = x + 1;          // a born
    long b = x * 2;          // b born; a still live (used on both paths)
    if (x > 0) return a + b; // a, b die on this path
    a = a + 3;               // a redefined — a fresh live range
    return a - b;            // a, b die on this path
}
```

```text
        ┌───────────────────────────────┐
        │ B1:  a = x+1 ;  b = x*2       │   live-in(B1)  = {x}
        │      if (x > 0)               │   live-out(B1) = {a, b}
        └──────────┬──────────┬─────────┘
                true        false
        ┌─────────┴────┐ ┌───┴───────────┐
        │ B2:          │ │ B3: a = a+3   │   a's live range spans the whole
        │ return a+b   │ │ return a-b    │   diamond: born in B1, dying in
        └──────────────┘ └───────────────┘   B2 and B3. So does b's.
```

Running liveness backward: `b` is live-in to B3, hence live-out of B1;
`a` likewise on both arms. Between `b = x*2` and the branch, **a and b are
live at the same point** — they *interfere*: no single register serves
both. `x` dies at `x > 0`; its register is immediately recyclable. The
payoff: interference is a property of *overlapping lifetimes*, not
textual proximity — a loop accumulator interferes with everything born
inside the loop, however briefly.

## Graph coloring: the map-coloring solution and its price

One straight-line example serves the rest of the chapter:

```c
long t1 = a + b;
long t2 = c + d;
long t3 = t1 * t2;    // t1, t2 die HERE — their registers free at this line
long t4 = e + f;
long t5 = t3 - t4;
return t3 + t4 + t5;
```

```text
line:     1    2    3    4    5    6
t1       [══════]                       def 1, last use 3
t2            [══════]                  def 2, last use 4
t3                 [════════════════]   def 3, last use 6
t4                      [═══════════]   def 4, last use 6
t5                            [════]   def 5, last use 6
```

Two ranges interfere when their intervals overlap (one courtesy rule: a
value defined on the line another dies may reuse its register — no edge).
The interference graph — nodes are live ranges, edges are overlaps — is
colored with k colors, k = allocatable registers:

```text
   t1 ────────── t2
   (R1)         (R2)
                  │  t2–t3
            ┌──── t3 ─────┐
           (R1)           │  t3–t4
            │  t3–t5      │
           t5 ────────── t4
           (R3)  t5–t4   (R2)
```

k = 3 suffices — `t3` reuses R1 because `t1` is dead and no t1–t3 edge
exists. With k = 2, `t5`'s neighbors take R1 and R2: something must
spill. But k-coloring is **NP-complete** for k ≥ 3, and a compiler runs
it millions of times per build, so production allocators (Chaitin 1981;
Briggs' *optimistic coloring* 1994; LLVM's `greedy`, default since ~2014)
use heuristics: simplify low-degree nodes, color greedily on the way
back, and when stuck pick a **spill victim by cost** — a spilled range
pays store+load at each use:

```text
spill cost(r) ≈ Σ (uses and defs of r) × frequency-of-that-point
```

Frequency comes from block counts and **loop depth**: classically a use in
a depth-n loop counts as ~10ⁿ top-level uses — a range used twice in a
triply-nested loop (~2,000) beats one used fifty times at top level (50)
for the right to a register. This one line of arithmetic explains most
real spill decisions.

A refinement: **coalescing**. Copies (`mov rax, rbx`) and SSA renames
create values that are one number under two names; if source and target
share a register, the move vanishes. The conflict: merging two nodes gives
the merged node the *union* of both neighborhoods — coalescing can create
interference that forces a spill where the `mov` was cheap. Chaitin
coalesced aggressively and paid; Briggs' *conservative* rule merges only
when the merged node provably keeps a free color. (Modern x86 cores
even eliminate register-to-register `mov`s in the rename stage — but only
those.)

## Spilling: the counterexample to "locals are free"

Module 01 ch04 derived the frame and asserted "locals are free" —
allocation was one `sub rsp, N`, deallocation one `leave`. Register
allocation supplies the asterisk. A **spilled** live range's home is a
spill slot in the frame, and every use becomes a memory access. The
frame's bytes are mostly *not* the programmer's named locals — at `-O2`
those live in registers until they die. The frame is dominated by
**allocator decisions**: spill slots, saved registers, alignment padding.
Module 01 drew the frame's shape; this chapter shows who fills it.

Spilling is not all-or-nothing: modern allocators **split live ranges**,
keeping a value in a register through the hot loop and storing it to its
slot only across the cold edge where pressure peaks. LLVM's `greedy`
allocator is built around splitting; Chaitin-era allocators spilled whole
ranges and re-ran coloring, which is why they scaled badly.

Two regimes, now explained: **`-O0` uses a trivial allocator**
(LLVM's `regalloc=fast`) — every variable gets its own frame
slot, always; that is *why* `-O0` frames show every local in gdb and why
uninitialized-read bugs differ across optimization levels (module 03
ch02). **`-O2`/`-O3` spill only under pressure** — and the threshold is
tiny: 15 registers minus the 6–8 a loop needs for pointers, counters,
strides leaves ~7–9 free homes. The lab shows 10 simultaneously-live
scalars already spilling; 20 spills hard.

## Linear scan: the JIT's bargain

Graph coloring inspects the whole function's interference — right for
ahead-of-time compilers, where compile time amortizes over every future
run. A JIT cannot say that: compilation happens *while the user waits*,
often for methods that run a handful of times. HotSpot's C1 and V8's
TurboFan (as of 2024) run **linear scan** (Poletto & Sarkar, 1999)
instead. Return to the interval diagram: sort ranges by start, walk once,
keep an *active* list of ranges holding registers, and when none is free,
evict the active range with the **latest end** — it blocks a register
longest. One pass, no graph, no re-runs.

The price: intervals only *approximate* interference. In straight-line
code overlap is interference, but control flow breaks the equivalence — a
range that looks short on the number line can be live *around a loop
backedge*, and the scan cannot see the cold paths a graph allocator
exploits for splitting. The measured cost, and this module's number:
**~10% worse code** — a few percent more spilled values in typical
functions, worse in register-bound kernels — for allocation an order of
magnitude faster. That is the tradeoff, and why HotSpot tiers: C1
for warmup latency, C2's graph coloring for peak throughput.

## From colors to bytes: the frame is emitted

The ABI pre-answers one interference question: what does a `call`
destroy? System V AMD64: every caller-saved register
(`rax, rcx, rdx, rsi, rdi, r8-r11`); the callee-saved set
(`rbx, rbp, r12-r15`) survives. To the allocator, **a call is a clobber
of the caller-saved class** — ABI-forced interference between your value
and the call. Three escape routes, all real costs: (1) home the value in a
callee-saved register, paying `push`/`pop` once per function; (2) spill
around the call, 2 memory ops per call per value; (3) split the range so
it isn't live across the call. A loop making one call per iteration with
six live values takes route 1 for all six — `rbx, rbp, r12-r15` is the
ABI's complete callee-saved set; the decision reads straight off the
prologue.

Then the prologue is emitted — Module 01 ch04's diagram *derived* rather
than asserted. The real frame of the lab's 20-live-variable function
(gcc 14.2.0, x86-64, `-O2`), each region labeled with its decision-maker:

```text
        higher addresses
+------------------------------------+  ← caller's rsp ≡ 0 (mod 16) — the
|  outgoing stack args (7th+, if any)|     16-byte call-site contract
+------------------------------------+  ← CALL pushed return address:
|  return address        (8 B)       |     entry rsp ≡ 8 (mod 16)
+------------------------------------+
|  saved rbx, rbp, r12-r15  (48 B)   |  ← REGALLOC: values homed in callee-
+------------------------------------+     saved regs — route 1 above
|  spill slots: 21 × 8 = 168 B       |  ← REGALLOC: live ranges that lost
|                                    |     their color — the "locals are
+------------------------------------+     free" tax bill, priced per access
|  alignment padding      (16 B)     |  ← prologue emitter: restore rsp ≡ 0
+------------------------------------+     (mod 16) so this frame may CALL
        lower addresses  ← rsp after `subq $184, %rsp` — ONE instruction
```

Why 16 bytes? System V AMD64 requires `rsp ≡ 0 (mod 16)` immediately
before every `call`, because callees (and the kernel, in signal frames)
store SSE values with `movaps`/`movdqa`, which fault on misaligned
addresses. The emitter rounds the claim so entry offset + pushes + `sub`
lands on a boundary: 8 + 48 + 184 = 240 ≡ 0 (mod 16). When alignment is
unknowable at compile time (varargs, `alloca`) you see the blunt
instrument: `andq $-16, %rsp`.

## What insiders know

- **The 16-byte rule is a call-site contract, not a frame rule**: a leaf
  that never calls and stores no SSE values needs no padding — hence
  odd-sized tiny frames, and misaligned-`movaps` crashes surfacing only
  in varargs or alloca-heavy callees.
- **Why JITs chose linear scan**: allocation is a large fraction of
  backend compile time, and JIT compile time is latency on a thread you
  are paying for. C1 exists to make tier-1 compiles cheap; C2's graph
  coloring runs only on proven-hot methods.
- **Coalescing fights spilling**: merging a `mov`'s endpoints unions
  their interferences; Briggs' conservative merge test is the standard
  compromise. Chaitin-style aggressive coalescing can *add* instructions
  by triggering spills costlier than the moves it removed.
- **Spill weights are loop-depth-driven**: uses weighted ~10ⁿ at nesting
  depth n. GCC's IRA dump prints the arithmetic in the open —
  `potential spill: pri=56, cost=5032` — `cost` is the
  frequency-weighted estimate the heuristic ranks victims by.
- **The observation flags**: `gcc -fverbose-asm` annotates spills as
  offsets from `%sfp`, GCC's virtual frame pointer; `gcc -fdump-rtl-all`
  drops the coloring-stage dumps (`*.r.ira`, `*.r.reload`); LLVM needs
  `llc -debug-only=regalloc` (an assertions build) or
  `-print-after=greedy`; `llc -regalloc=fast` shows the `-O0` allocator.
- **The vectorizer changes k**: 20 live `long`s "can't fit" 15 GPRs, but
  the auto-vectorizer packs two per XMM register — 20 scalars become 10
  XMM values and the pressure vanishes. Pressure arithmetic on x86-64
  must count the 16 XMM registers too.
- **GCC's `-O3` does not unroll by default** (that is `-funroll-loops`,
  or clang, which does unroll at `-O3`). The folklore "loop spills at
  -O3 but fit at -O2" is real on clang; on GCC 14 add `-funroll-loops`
  (lab below).

## See it yourself

Platform: gcc 14.2.0 (Debian), x86-64. Generate functions with
10/16/20 live accumulators:

```console
$ python3 -c "
for n in (10,16,20):
    d=', '.join(f's{i}=0' for i in range(n))
    a='\n        '.join(f's{i} += a[{i}*n+i];' for i in range(n))
    r=' + '.join(f's{i}' for i in range(n))
    open(f'v{n}.c','w').write(f'void sink(long);\nlong sum{n}(const long *a, int n) {{\n    long {d};\n    for (int i=0;i<n;i++) {{\n        {a}\n    }}\n    sink(n);\n    return {r};\n}}\n')
"
```

Plain `-O2` first: the mov-to-stack count is **zero** even at n=20 — the
vectorizer packed everything into XMM registers (bullet above). Force the
scalar story the chapter derived, compile the emitted (non-leaf) files
plus leaf versions with the `sink(n)` call deleted, and measure the frame
claim and spill slots in each `.s`:

```console
$ for n in 10 16 20; do gcc -O2 -fno-tree-vectorize -S v$n.c; done
```

| n  | leaf: `subq $N,%rsp` | leaf slots | non-leaf: `subq $N,%rsp` | non-leaf slots |
|----|----------------------|------------|--------------------------|----------------|
| 10 | (none)               | 5, `-8…-40(%rsp)`  | 56  | 5  |
| 16 | (none)               | 14, `-8…-112(%rsp)`| 120 | 13 |
| 20 | 40                   | 19, red zone blown | 184 | 21 |

Two payoffs. The spill count explodes as the arithmetic predicts — and
in the *leaf* versions the five slots at n=10 live in the **128-byte red
zone** (module 01 ch04): the leaf's free spill attic, invisible to any
frame walk, until n=20 exhausts it. Second, all six callee-saved GPRs are
pushed in every variant: the allocator wanted every allocatable register,
and the ABI split shows up as six `pushq`s. The n=20 prologue, verbatim:

```asm
pushq   %r15                ; ┐
pushq   %r14                ; │
pushq   %r13                ; │ 48 B of callee-saved regs —
pushq   %r12                ; │ the allocator's ABI-forced route 1
pushq   %rbp                ; │
pushq   %rbx                ; ┘
subq    $184, %rsp          ; 21 spill slots + 16 B alignment, ONE instruction
...
movq    %rcx, 168(%rsp)     ; this live range just lost its register
...
addq    $184, %rsp          ; epilogue: frame dies in bulk (popq ×6, ret)
```

Read the allocator's own annotations:

```console
$ gcc -O2 -fno-tree-vectorize -fverbose-asm -S v10.c
```
```asm
# v10.c:3:     long s0=0, s1=0, ...;
        movq    %rcx, 16(%rsp)     # s0, %sfp
# v10.c:5:         s0 += a[0*n+i];
        addq    %rdi, 16(%rsp)     # tmp225, %sfp
```

`s0` gets its frame home at the definition — and pays it in the loop,
every iteration, as read-modify-write traffic to `16(%rsp)`. For the
allocator's own reasoning, dump its coloring pass, and reproduce the
"-O3 spills more" phenomenon (GCC 14 edition — add `-funroll-loops`;
`u10.c` is `v10.c` with the `sink(n)` call removed):

```console
$ gcc -O2 -fno-tree-vectorize -c -fdump-rtl-all v20.c
$ rg 'potential spill' v20.c.*.r.ira
      Pushing a52(r141,l0)(potential spill: pri=56, cost=5032)
$ gcc -O2 -fno-tree-vectorize -S u10.c                  # 5 spill slots
$ gcc -O3 -funroll-loops -fno-tree-vectorize -S u10.c   # 14 spill slots
```

Unrolling doubles the live values per loop body: five slots become
fourteen with one flag (on clang, `-O3` alone — it unrolls by default).

## How to use this knowledge

- **Hot-loop triage**: one `rg '\(%rsp\)'` over the disassembly says
  whether a hot loop pays the spill tax. The fix is source-level — fewer
  simultaneously-live values (tile a 20-accumulator kernel into blocks of
  8), not assembly surgery.
- **The mysterious "-O3 got slower"**: unrolling or inlining raised
  pressure and spilled a value `-O2` kept in a register — a five-minute
  diagnosis.
- **Debugging**: gdb's `<value optimized out>` is this chapter — no slot,
  or a split live range; and `-O0`'s see-every-variable behavior is the
  trivial allocator's every-value-gets-a-slot policy.
- **Reading binaries (module 04)**: push count and `sub rsp` size are a
  fingerprint — six pushes plus a big claim says "loop-heavy,
  register-bound body" before you read another instruction.
- **Interview-grade answer**: allocation is k-coloring an interference
  graph of live ranges; NP-complete, so greedy heuristics with weighted
  spill costs, range splitting, conservative coalescing; JITs trade ~10%
  code quality for linear-scan speed.

## Connections

- **Module 01 ch04** — the frame diagram asserted there is derived here:
  spill slots, saved registers, alignment padding are allocator
  decisions; "locals are free" gets its tax bill.
- **Module 01 ch07** — the assembly reference: prologues, epilogues, and
  ABI register roles are the surface these decisions emit.
- **Module 10 ch01** — SSA's virtual registers are this pass's input;
  phi nodes are the merges the allocator must honor at joins.
- **Module 10 ch02** — SROA dissolves stack structs into SSA values;
  DSE decides which scrubbing memsets survive.
- **Module 10 ch05** — the ABI side: argument registers, struct
  classification, the 16-byte call-site alignment contract.
- **Module 04 ch03** — reading disassembly: prologues as pressure
  fingerprints, spill slots as variable recovery.
- **Module 03 ch02** — uninitialized reads from dead frames: spill slots
  are never zeroed, so yesterday's register is tomorrow's garbage.

## Quiz

1. **Concept.** Two variables are used in the same statement; two others
   are used 200 lines apart, both inside the same loop. Which pairs
   necessarily interfere, and what property defines the edge?
2. **Concept.** k-coloring is NP-complete, yet compilers run allocation
   millions of times per build. What do heuristics give up, and what do
   they optimize instead of optimality?
3. **Predict the number.** A leaf x86-64 function (no calls, `-O2`,
   vectorization off) keeps 10 `long` accumulators live across a loop; 5
   lose their registers. Where do those 5 homes live, and what does the
   prologue claim with `sub rsp`?
4. **Predict the layout.** The non-leaf variant pushes all six
   callee-saved registers then runs `subq $184, %rsp`. Given entry
   `rsp ≡ 8 (mod 16)`, show the prologue leaves `rsp` 16-byte aligned,
   and say why the ABI demands that before this function's `call`s.
5. **Predict the output.** `-fverbose-asm` prints
   `movq %rcx, 16(%rsp) # s0, %sfp` at `s0`'s definition and
   `addq %rdi, 16(%rsp) # tmp225, %sfp` inside the loop. What is `%sfp`,
   and what do these two instructions say happened to `s0`?
6. **Bug diagnosis.** A kernel ran 82 ns/pixel at `-O2`; after `clang -O3`
   it runs 94 ns/pixel, and disassembly now shows a `movq %rax, -48(%rsp)`
   plus matching load *inside* the hot loop. What happened? Name two
   source-level fixes.
7. **Bug diagnosis.** A penetration test recovers an API token from a core
   dump taken *after* the function holding it returned; the token was
   spilled to `152(%rsp)`. Why do the bytes outlive the frame, and who ends
   up reading them?
8. **Cross-chapter.** A loop with six live `long`s makes one call per
   iteration; the compiler pushes all six callee-saved GPRs instead of
   spilling or splitting. Derive the cost comparison that justifies it,
   and connect it to module 01 ch04's frame diagram.

<details>
<summary>Answers</summary>

1. The same-statement pair always interferes. The distant pair interferes
   only if their live ranges overlap — a loop accumulator interferes with
   everything born inside the loop. The edge is *simultaneous liveness*
   at some program point, not textual proximity.
2. They give up the minimum-spill coloring and its proof. They optimize
   speed plus a good-enough spill set: greedy coloring order, victims
   ranked by frequency-weighted cost (loop depth ~10ⁿ), live-range
   splitting to confine spills to cold regions. Extra colors are free;
   spills are not.
3. Red-zone slots at `-8(%rsp)`…`-40(%rsp)` — below `rsp`, invisible to
   any frame walk — and the prologue claims **zero** bytes: leaf functions
   may use the 128-byte red zone as spill space for free (the lab shows:
   5 slots, no `subq`).
4. Entry ≡ 8; six pushes add 48 (≡ 0 mod 16), still ≡ 8; 184 ≡ 8
   (mod 16), so after the claim `rsp ≡ 0 (mod 16)`. The ABI requires
   16-byte alignment immediately before every `call` because callees may
   execute aligned SSE stores (`movaps`/`movdqa`), which fault on
   misalignment; the callee's entry then repeats the arithmetic.
5. `%sfp` is GCC's *virtual stack frame pointer* — the anchor
   frame-relative accesses use before final emission substitutes real
   offsets. Together the lines say `s0` got the slot `16(%rsp)` at birth
   (it lost its register) and pays read-modify-write traffic there every
   iteration.
6. `-O3` unrolled or inlined the loop, raising simultaneously-live
   values; an accumulator's live range now spans the hot loop and got
   spilled, adding store+load per iteration (~12 ns/pixel of L1
   round-trips and stalls). Fixes: reduce simultaneously-live values —
   tile the kernel so ~8 accumulators are live — or keep that unit at
   `-O2` with the measurement as evidence.
7. `addq $184, %rsp` and the `popq`s move the stack pointer; nothing
   zeroes the spill slots (module 01 ch04: frames are never erased on
   reuse). The bytes sit in dead-but-mapped stack until the next call's
   frame overlaps them — becoming that function's *uninitialized read*,
   module 03 ch02's family: an info-leak that is also a correctness bug
   in the reader. Mitigate with `-ftrivial-auto-var-init=zero` or a scrub
   before return — which module 10 ch02's DSE may delete unless it is a
   `volatile` store.
8. Per iteration: spilling six values around one call costs 12 memory
   operations; splitting costs comparable moves; callee-saved homing
   costs **zero** per-iteration traffic and 12 one-time instructions (6
   `push`, 6 `pop`) — amortized over thousands of iterations. The
   saved-register region in module 01 ch04's frame diagram is this
   decision materialized: ABI-forced interference resolved once per
   function, not once per call.

</details>
