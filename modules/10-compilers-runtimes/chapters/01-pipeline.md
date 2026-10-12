# Chapter 01 — The pipeline

> **The 30-second version.** A compiler is a relay race between representations.
> Text becomes tokens (no memory meaning), tokens become a
> tree (shape, no addresses), the tree becomes an *intermediate representation*
> where names turn into values or explicit memory operations, and codegen picks
> registers and stack slots and emits the prologue — the
> exact moment module 01 ch04's stack frame comes into existence. "-O2 ate my
> variable" is promotion, not deletion: a pass turns locals into single-assignment
> values that need no memory.

## From text to graph: why the stages exist

Nobody writes text-to-machine-code in one step. The input is unstructured,
context-sensitive text; the output is a register machine — structured, finite,
address-based. One transformation would be untestable, so the journey is split — text,
tokens, tree, graph (the IR), machine — one stage per question:

- **Lexer** — bytes to tokens. `int` is 3 characters that become one keyword token;
  the stream for `int x = 0;` knows nothing about storage. *No memory semantics at this stage* — and almost no state.
- **Parser** — tokens to a tree, via *recursive descent*: one function per grammar
  rule. The hard question is precedence: in `a + b * c`, who owns `b`? A *Pratt parser* answers with a table: each operator gets a numeric binding
power; the expression loop parses a left operand, peeks the next operator, and
recurses only when the peeked power is higher. C's 15 precedence levels (C17)
become one ~20-line loop plus a table. The AST has scope
*syntax* — but no addresses: a "block" is a tree node, not a stack region.

Memory ideas first appear only past the parser; before the IR boundary it is
string and tree surgery — which is why beginner bugs (syntax errors, precedence
surprises) never touch this module's memory story.

## The IR: N×M economics and a typed GOTO form

Front ends need linguists; back ends need hardware engineers. Straight
compilation from every language to every target costs N×M complete compilers —
for N = 6 front ends and M = 5 targets, 30 pairings, each demanding both
expertises. One shared IR, and you write N front ends + M back ends: 11
components — why Rust, Swift, and Julia rent LLVM's back ends instead of
writing their own.

LLVM IR is the concrete contract:

- **Typed**: every value carries a first-class type (`i32`, `ptr`, `float`).
- **GOTO form**: a function is a list of *basic blocks* — maximal straight-line
  sequences — each ending in exactly one *terminator* (`br`, `ret`,
  `switch`). Jumps target block labels, never a block's middle — control flow is
  an explicit graph.
- **Infinite virtual registers**: `%0`, `%1`, … — "only 16 registers" is deliberately not the IR's problem, but codegen's (ch03).
- **Memory is explicit**: there are no "variables": a stack local is an
  `alloca` ("claim N bytes on my frame") plus `load`/`store`, and every memory
  access is a visible instruction.

```llvm
; LLVM IR shape (LLVM 19 LangRef syntax; illustrative, not a dump)
define i32 @classify_sum(ptr readonly %a, i32 %n, i32 %limit) {
entry:
  %a.addr = alloca ptr                  ; at -O0 locals live in IR-visible memory
  store ptr %a, ptr %a.addr
  br label %loop.header                 ; blocks end in terminators...
loop.header:
  %acc = phi i32 [ 0, %entry ], [ %acc.next, %body.merge ]   ; ...and merge in phis
}
```

The AST is *lowered* into this, not translated — sugar dies here. `for` and
`while` become the same loop skeleton, `x += 1` and `x = x + 1` the same add,
and many languages' constructs collapse into one ~50-instruction vocabulary, so
every optimizer pass is written once. GCC makes the same decisions in different
clothes: its IR is **GIMPLE** (three-address code, basic blocks, gotos), which
you dump below.

## SSA and the phi node: variables become values

Optimizations ask *where was this defined, and who reads it?* — mutation makes
that hard: the lab's `acc` has three assignments, so uses have three reaching
definitions. **SSA — static single assignment — kills the problem at the root:
each assignment creates a new name.** `acc` becomes `acc_8`, then `acc_17`,
`acc_18`, `acc_19`; every use has exactly one definition, so def→use edges become
single arrows a pass can walk blindly. Renaming is free — except at control-flow *merges*, where two names both hold
"the current acc." SSA's answer: a new instruction, the **phi node** — this
sentence made executable:

> At this merge point, the value is **x** if you arrived on this edge, **y** if you
> arrived on that edge.

A phi is a register-merger at a control-flow join — a programmable mux. The lab
function's loop, as the CFG the IR contains:

```text
                  entry  (bb 2)
                  acc_8 = 0;  i_9 = 0
                        |
                        |  value of acc on this edge: acc_8 (= 0)
                        v
        +--------------------------------+
        |  loop header (bb 10)           |<-----------------+
        |                                |                   |
        |  # acc_5 = PHI <acc_8(2),      |   value of acc on |
        |                 acc_4(9)>      |   THIS back edge: |
        |  # i_6   = PHI <i_9(2),        |   acc_4 (updated) |
        |                 i_20(9)>       |                   |
        +----------------+---------------+                   |
                         | i_6 < n ?                         |
              +---- no ---+--- yes ----+                     |
              v                         v                    |
        return acc_5              loop body (bb 4)            |
                                   v_15 = a[i_6]             |
                                   if (v_15 < 0)             |
                              yes /          \ no            |
                                 v            v              |
                     acc_19 = acc_5+v_15   acc_17 =          |
                     (v negative)           acc_5 + v_15     |
                                 |            |              |
                                 +-----+------+              |
                                       v                     |
                              merge block (bb 9)            |
                        # acc_4 = PHI <acc_19(5),            |
                                        acc_17(8)>           |
                        i_20 = i_6 + 1                       |
                                       |                     |
                                       +---------------------+
              
```

GCC spells this `# acc_4 = PHI <acc_19(5), acc_17(8)>`; LLVM: `%m = phi i32 [ %a, %then ], [ %b, %else ]`.

Two subtleties. First, **the phi is a fiction**: no CPU has a phi instruction;
codegen lowers it into register pairs plus a conditional move — in the lab, the
three-way merge phi becomes a single `cmovl`. Second, the pass connecting SSA to
*your variables*: at -O0, IR locals are memory (`alloca` + load/store in LLVM;
GIMPLE names for stack slots in GCC). The **mem2reg** pass (LLVM) / SSA
construction (GCC) promotes each load/store pattern to an SSA value — the moment "a variable"
becomes "a value" and -O2 locals stop existing as memory. When mem2reg succeeds,
nothing is left for the stack frame to hold.

## Passes: analysis vs transform, `-O2` as function composition

"Optimizing" means running functions over the IR. Two kinds: **transforms** rewrite IR (inline a call, delete a dead store, hoist an invariant); **analyses** compute facts (dominators, liveness) and never appear in the output file — they only license transforms. Analyses are cached and *invalidated*: a transform that edits the CFG burns the dominance cache, and the next consumer recomputes.

An optimization level is a fixed *pipeline* — a function composition: `-O2` is
fₙ ∘ ⋯ ∘ f₁, and `opt -O2 < in.ll > out.ll` is literally that function application.
GCC runs the same idea: for the lab function, GCC 14.2 executes **140 tree-level
passes at -O2 versus 29 at -O0** (counted via `-fdump-tree-all`; the -O0 passes
are mostly *lowering*, not optimization). "No optimization" means fewer passes, never no pipeline. What -O2 passes buy,
beyond speed, is *canonicalization* — one standard shape per construct, so later
passes match patterns instead of spellings.

## Codegen: selection, scheduling, allocation — the birth of the frame

Codegen has three sub-problems. **Instruction selection** matches IR patterns
to machine instructions — `a[i]` with i32 elements becomes the addressing mode
`(%rdi,%rax,4)`; add-plus-shift collapses into `lea`. **Scheduling** orders
instructions to hide latency and reorders blocks. **Register allocation** maps
infinite virtual registers onto x86-64's 16 GPRs (14 usable); the losers become
**spill slots** — ch03's whole subject.

Then this module's moment. **Prologue/epilogue emission** — GCC's
`pro_and_epilogue` pass, LLVM's `PrologEpilogInserter` — is where module 01 ch04's
stack frame comes into existence: after allocation has counted the spills, the pass
emits `push rbp / mov rbp, rsp / sub $N, rsp`, and N is the spill count, which no
earlier stage could know. The frame you memorized in module 01 is *decided* here — and if allocation needs nothing and the function is a leaf, it is born with size zero; the lab function at -O2 has exactly that non-frame.

## LTO and the debugging knobs

The pipeline runs per *translation unit* — C's model — so it stops at file
boundaries: you cannot inline across files without their IR. **LTO** serializes
the IR into the object: `gcc -flto` wraps GIMPLE
bytecode into `gnu.lto_*` ELF sections (an LTO object has *no `.text` at all*);
`clang -flto` embeds bitcode (`.llvm.lto` in clang 15+, `__llvm.lto` before). At link time the linker plugin hands the *merged, whole-program IR* back
to the compiler, and the same pipeline runs again. The binary thus ships the compiler's blueprints; LTO links need a
plugin-aware linker and a matching compiler version — the linker is now a
compiler, and linking gets slower.

The knobs that make every stage observable (versions matter):

- `clang -S -emit-llvm` / `llvm-dis`: IR as text — GCC twin `-fdump-tree-gimple`.
- `opt -S -passes='default<O2>' -debug-pass-manager`: prints each pass as it runs — the `-debug-pass=Executions` flag from pre-2020 blog posts died with LLVM's legacy pass manager; new-PM flags work in LLVM 15+ — GCC twins `-fdump-tree-all` and `-fopt-info-all`.
- `-fverbose-asm`: assembly annotated with source lines and IR names (clang, GCC).
- `gcc -fdump-rtl-*`: the codegen stages, including `pro_and_epilogue`.

## What insiders know

- **The diamond is measured in person-years.** 30 full compilers versus 11
  components is why rustc, Swift, and Julia all sit on LLVM. The tax: your
  semantics must fit the shared IR — Rust's borrow checker emits metadata LLVM
  ignores; Go built its own compiler to keep escape analysis in-house.
- **mem2reg runs before anything you think of as optimization.** In opt's
  `default<O1>`+ pipelines it sits in the first canonicalization round, so every
  later pass sees clean SSA — "-O0 has variables, -O2 doesn't" is decided in the
  pipeline's first minutes. GCC's twins: SSA construction (`022t.ssa`) and the
  SRA family (`038t.esra`); GCC 14.2 even builds SSA at -O0, then immediately
  releases it (`056t.release_ssa`).
- **The frame decision is almost the last thing that happens.** Spill slots are
  assigned during register allocation (GCC's IRA/LRA, LLVM's greedy allocator);
  only then does `pro_and_epilogue` / `PrologEpilogInserter` fix the frame size.
  Ask "how big is this frame?" at the AST: the compiler doesn't know yet.
- **Pass-pipeline debug flags are a version trap.** LLVM: `-debug-pass-manager`
  with `-passes='default<O2>'` (LLVM 15+); legacy `-debug-pass=Executions` is
  gone. GCC: `-fdump-tree-all` — dump filenames embed the pass index
  (`pipeline.c.022t.ssa`); the *name* is stable, the index moves with flags.
- **LTO objects are IR wearing an ELF hat.** `gcc -flto` output contains
  `gnu.lto_.symc` / `gnu.lto_.main` sections and no code; `nm` without the plugin
  shows nothing useful. `lto-dump` (GCC) and `llvm-dis` on extracted bitcode read
  it back — an object's optimization potential sits in IR until final link.

## See it yourself

No clang on this machine, so the lab runs the GCC twins — every fragment is
real, **GCC 14.2.0 (Debian 14.2.0-19), x86-64 Linux**. The function:

```c
/* pipeline.c — 20 lines, four locals, three merge points */
int classify_sum(const int *a, int n, int limit) {
    int acc = 0;                      /* local 1: accumulator   */
    int i = 0;                        /* local 2: loop counter  */
    if (n <= 0) {                     /* early out: 1 merge     */
        return 0;
    }
    while (i < n) {                   /* back edge -> phis      */
        int v = a[i];                 /* local 3: load          */
        if (v < 0) {                  /* merge 1: if/else       */
            acc = acc + v;
        } else if (v > limit) {       /* merge 2                */
            acc = acc + limit;
        } else {
            acc = acc + v;
        }
        i = i + 1;
    }
    int result = acc;                 /* local 4: pure rename   */
    return result;
}
```

**Step 1 — the IR at -O0: locals are names, control is gotos.**

```sh
gcc -O0 -fdump-tree-gimple -c pipeline.c    # -> pipeline.c.006t.gimple
```

```text
;; pipeline.c.006t.gimple (excerpt)
int classify_sum (const int * a, int n, int limit)
{
  int D.2783;  int acc;  int i;  int result;    <- every local DECLARED
  if (n <= 0) goto <D.2781>; else goto <D.2782>;  <- while already lowered to
  <D.2779>:                                          labels + gotos
    v = *(a + (long unsigned) i * 4);             <- the load is explicit
```

**Step 2 — SSA at -O2: the phis appear.**

```sh
gcc -O2 -fdump-tree-ssa -c pipeline.c     # -> pipeline.c.022t.ssa
```

```text
;; pipeline.c.022t.ssa (excerpt)
<bb 10> :
  # acc_5 = PHI <acc_8(2), acc_4(9)>    <- acc is acc_8 on the entry edge,
  # i_6   = PHI <i_9(2), i_20(9)>       acc_4 on the back edge
<bb 9> :
  # acc_4 = PHI <acc_19(5), acc_18(7), acc_17(8)>  <- the 3-way merge phi
  i_20 = i_6 + 1;
```

**Step 3 — after the pipeline: the locals vanish.**

```sh
gcc -O2 -fdump-tree-optimized -c pipeline.c  # -> pipeline.c.265t.optimized
```

```text
;; pipeline.c.265t.optimized (excerpt)
  <bb 4> :
  # acc_28 = PHI <0(3), acc_37(9)>             <- "acc = 0" folded INTO the phi
  # ivtmp.8_43 = PHI <ivtmp.8_20(3), ivtmp.8_27(9)>   <- i became a pointer walk
  v_33 = MEM[(const int *)_19];                <- v is a VALUE now, not a slot
```

`i` and `result` no longer exist as variables: `i` became `ivtmp.8` (a pointer
advancing 4 bytes per iteration), `result` folded into the return merge.

**Step 4 — the memory contrast.**

```sh
gcc -O0 -S -fverbose-asm -o O0.s pipeline.c
gcc -O2 -S -fverbose-asm -o O2.s pipeline.c
```

At -O0 the prologue is `pushq %rbp; movq %rsp, %rbp` — no `subq`, because a
leaf parks its five slots in the 128-byte red zone below `rsp` (x86-64 SysV) —
then spills every local: `movq %rdi, -24(%rbp) # a`, `movl $0, -4(%rbp) # acc`,
`i`/`v`/`result` at -8/-16/-12.

```asm
;; O2.s: no frame — locals live in registers
classify_sum:
        leaq    (%rdi,%rsi,4), %r8      # loop end = a + n*4
.L6:
        movl    (%rdi), %ecx    # v
.L3:
        leal    (%rax,%rdx), %esi       # acc+limit
        addl    %ecx, %eax      # v
        cmpl    %ecx, %edx      # v, limit
        cmovl   %esi, %eax      # <- the 3-way merge phi: ONE conditional move
        addq    $4, %rdi        # ivtmp.8: i is gone, the pointer walks
        cmpq    %rdi, %r8
        jne     .L6
.L1:
        ret                                  ; no popq, no leave: frame never existed
```

**Step 5 — the frame decision (RTL).**

```sh
gcc -O0 -fdump-rtl-pro_and_epilogue -c pipeline.c  # -> pipeline.c.322r.pro_and_epilogue
```

```text
;; pipeline.c.322r.pro_and_epilogue (excerpt)
(insn/f 84 ... (set (mem:DI (pre_dec:DI (reg/f:DI 7 sp)))  ; rsp -= 8, THEN
        (reg/f:DI 6 bp)) "pipeline.c":1                    ; store rbp: PUSH rbp
(insn/f 85 ... (set (reg/f:DI 6 bp) (reg/f:DI 7 sp)))      ; rbp = rsp```

That `pre_dec` is the frame's first 8 bytes being born as a side effect of a
store. Add a call to make it non-leaf and you get the classic `pushq
%rbp; movq %rsp, %rbp; subq $32, %rsp` — 32, not 24, to keep `rsp` 16-byte
aligned (verified, same compiler).

**With clang instead** (documented equivalents, not run here): `clang -S
-emit-llvm -O0 pipeline.c` yields `pipeline.ll` with an `alloca`/`store` per
local, phis at `-O2`; `llvm-dis pipeline.bc` turns bitcode into text; `opt -S
-passes='default<O2>' -debug-pass-manager pipeline.ll` prints every pass.

## How to use this knowledge

- **When gdb says `value has been optimized out`**: mem2reg promoted the local
  and regalloc put it in a register or folded it away. Rebuild with `-Og`, GCC's
  debug-friendly level, or read `-fverbose-asm` to find the carrying register.
- **Reading production disassembly** (module 04 ch03's game): fingerprint code —
  `push rbp/mov rbp,rsp` with no `sub` means red-zone leaf, not "inlined"; a `cmov`
  cluster means a merge phi was if-converted; a pointer stepping `addq $4` with no
  counter means induction-variable replacement ran.
- **Performance decisions**: cross-file inlining needs LTO, not hope; "-O3"
  just appends passes (unrolling, vectorization); a loop that spills at -O3 but
  fits at -O2 points at ch03.


## Connections

- **Module 01 ch04 (the stack)** — the frame diagram there is *emitted* by this
  chapter's `pro_and_epilogue` pass; its size is the spill count from one stage
  earlier.
- **Module 01 ch07 (assembly)** — the lab's artifacts (`cmovl`, addressing modes, the zero-frame leaf) are that chapter's instruction set, chosen by selection.
- **Module 04 ch03 (reading disassembly)** — its compiler fingerprints are this
  chapter's outputs read backwards.
- **Module 10 ch02** — SROA, DSE, LICM, alias analysis: the transforms that
  run in this pipeline.
- **Module 10 ch03** — register allocation: the stage whose spill count sizes the
  frame this chapter's prologue emits.
- **Module 10 ch04** — escape analysis, write barriers, safepoints are passes inserted into this same pipeline.
- **Module 10 ch05** — ABI and layout: the contracts codegen honors.
- **Module 11 (database storage engines)** — none: a B-tree does not care about SSA.

## Quiz

1. Your team maintains 6 DSL front ends and must target 5 architectures. With a
   shared IR, how many compiler *components* do you write instead of 30, and what
   two costs do you pay?
2. The pass computing "which definitions reach this use" — analysis or
   transform? Why can its result never appear in the output file, and what happens after a
   transform edits the CFG?
3. Predict the IR. Given `int x = 0; if (c) x = 1; else x = 2; return x;` — how
   many inputs does the merge-point phi for `x` have, does `c` get a phi, and what
   changes if the `else` arm is deleted?
4. Predict the number. A non-leaf `-O0` GCC 14.2 (x86-64) function shows
   `pushq %rbp; movq %rsp, %rbp; subq $32, %rsp` — 32, though its slots need only
   24 bytes. Why exactly 32?
5. Bug diagnosis: at `-O2`, gdb reports `acc` as "value has been optimized
   out," and your colleague files "the optimizer deleted my variable." Name the
   pass, say what actually happened, and give two recoveries.
6. Bug diagnosis: stepping through the lab function at `-O2`, a breakpoint on
   line 12 (`acc = acc + limit;`) never fires, yet the limit branch provably
   executes. What did the pipeline do to line 12?
7. Cross-chapter (module 01 ch04): at which pipeline stage does the stack frame come into existence, why not one stage earlier, and why does the lab function at `-O2` get a frame of zero bytes?
8. Bug diagnosis: `nm foo.o` on a `-flto` object shows almost nothing, and an
   old linker rejects it outright. What is inside `foo.o`, and what does a
   working link require?

<details>
<summary>Answers</summary>

1. **11 components** (6 front ends + 5 back ends), not 30 compilers.
   Cost (a): the IR is a *common denominator* — every language's semantics must
   fit it, so language-specific wins (Rust's aliasing, Go's escape analysis) leak
   away unless the language owns its compiler. Cost (b): the IR becomes a stability contract — changing it is a coordinated
   event, not a refactor.
2. **Analysis.** It consumes IR and produces facts (def→use, dominance) and has
   no write path to the output file — it only licenses transforms.
   A CFG-editing transform **invalidates** the cached analysis — dominance is no
   longer trustworthy — so it is recomputed for the next consumer.
3. **Two inputs**: `x_4 = PHI <x_1(then), x_2(else)>`. `c` gets no phi — it is
   assigned once (a parameter); SSA mints names only for assignments. Delete the `else` arm and the phi survives with one value and one *constant*,
   `PHI <x_1, 0>` — the fall-through's `x = 0` folds into the phi as a literal.
4. **16-byte stack alignment.** At entry `rsp` is 8 mod 16 (return address);
   after `push rbp`, 0 mod 16. `subq $24` would leave it 8 mod 16 — misaligned
   for the 16-byte SSE accesses the ABI promises at calls. 24 rounds up to 32 (SysV AMD64, GCC 14.2).
5. **Promotion, not deletion.** mem2reg/SSA construction converted `acc`'s
   load/store pattern into SSA values — virtual registers, folded into `%eax`
   (the lab). Nothing is missing; there is just no *storage* to print.
   Recoveries: rebuild with `-Og` (or `-O0`), or find the carrying register via
   `-fverbose-asm` and `info registers`.
6. **If-conversion plus phi lowering.** The three-way merge phi became register
   arithmetic guarded by one `cmovl`: both arms compute their candidates
   unconditionally and the conditional move picks. Line 12 no longer exists as a branch — the choice is a data dependency now,
   so no line-12 breakpoint can fire.
7. **At prologue/epilogue emission** — GCC's `pro_and_epilogue`, LLVM's
   `PrologEpilogInserter`. Not earlier because the frame size *is* the spill
   count, register allocation's output. At `-O2` the lab function needs zero
   spills and is a leaf, so nothing is emitted: the only frame bytes are the
   8-byte return address the *caller's* `call` pushed — module 01 ch04's minimal
   frame.
8. **IR wearing an ELF hat.** `foo.o` holds serialized compiler IR in `gnu.lto_*`
   sections (GCC) or bitcode in `.llvm.lto` (clang 15+) — and no `.text`, so `nm`
   lists no code symbols. A working link needs a plugin-aware linker and a
   matching IR version: the link step *is* a compile of the merged whole-program
   IR.

</details>
