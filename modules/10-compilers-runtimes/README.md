# Module 10 — Compilers & language runtime internals

> Module 01 studied the memory models; this module studies the factory that
> manufactures them. Where does "the stack frame" actually get decided? Why did
> your local variable disappear at -O2? Who inserts the GC's write barrier, and
> why is it *that* instruction and not the one before? Compilers are the machine
> that turns your source into the memory picture — read their minds.

**5 chapters · one final exam · prerequisite: module 01 chapters 04 (stack), 06
(object layout), 07 (assembly), 10 (Rust — for layout reordering), 12/15 (JVM/V8
GCs as consumers); module 04 ch03 (reading disassembly) is directly reused.**

## How to study it

Chapter 01 is the pipeline end-to-end; 02 is the optimization catalog that
*changes your memory behavior*; 03 is register allocation — the birthplace of
the stack frame; 04 is the GC-facing compiler work (escape analysis, write
barriers, safepoints); 05 is the ABI and layout decisions. Labs use
clang/gcc/LLVM tools (`-emit-llvm`, `-fverbose-asm`, opt remarks) — the compiler
becomes observable.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The pipeline](chapters/01-pipeline.md) | Lex → parse → AST → IR → SSA → codegen: where every memory decision is made. |
| 02 | [Memory-shaping optimizations](chapters/02-optimizations-memory.md) | SROA, alias analysis, LICM, DSE — and UB as optimization license. |
| 03 | [Register allocation](chapters/03-register-allocation.md) | Graph coloring vs linear scan; spills; where the frame layout is born. |
| 04 | [Escape analysis, barriers, safepoints](chapters/04-escape-barriers-safepoints.md) | The compiler's GC duties — and deopt as "uncompiling". |
| 05 | [ABI & layout decisions](chapters/05-abi-headers.md) | Mangling, calling conventions, vtable emission, field reordering. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the pipeline.** The stages with what each does to *memory
ideas*: lexer (tokens — no memory semantics), parser (recursive descent,
precedence — Pratt tables in one paragraph), AST → lowering to IR (why IR
exists: N languages × M targets → N+M — the diamond economics). LLVM IR as
the concrete example: typed GOTO form (basic blocks, terminator instructions),
*SSA as the big idea* — every variable assigned exactly once; the phi node
demystified (a phi = "at this merge point, the value is either x or y
depending on which edge you arrived on" — the register-merger at control-flow
joins; an ASCII CFG with a phi in it). Passes: analysis vs transform, the
pass-pipeline concept (`opt -O2` as a function composition). Codegen: instruction
selection (DAG/pattern matching), scheduling, register allocation (→ ch03),
prologue/epilogue emission — *the moment module 01 ch04's stack frame comes
into existence* (the chapter's payoff sentence). LTO (IR serialized into the
binary — cross-module optimization). The debugging knobs: -emit-llvm, opt
passes -debug-pass=Executions... (use `-debug-pass-manager`-era equivalents;
mark the version). Lab: a 20-line C function through clang -O0 vs -O2 IR;
watch locals become SSA values, watch them vanish; `llvm-dis` reading.

**Chapter 02 — memory-shaping optimizations.** The catalog that changes
observable memory behavior: SROA (scalar replacement of aggregates — stack
structs dissolved into individual SSA values: "the stack destructor"; the
mem2reg pass as the canonical first mover — derive why debug builds have
variables and release builds don't, closing module 03's "-O0 vs -O2
uninitialized" mystery). Alias analysis (may-alias vs must-alias, points-to
sketch, why C's `restrict` and Rust's `&mut` uniqueness are *aliasing
contracts* that license optimization — the deep unification: Rust's borrow
checker is an alias analysis that runs at compile time and can't be wrong).
Load/store optimizations: redundant-load elimination, store forwarding, DSE
(dead stores — including the "zero the dead buffer" patterns security wants
and compilers remove — the memset-removal issue and -ftrivial-auto-var-
initization as the counter). LICM (loop-invariant code motion — the alias
and safety conditions that make it legal). The as-if rule and UB as
optimization *license*: the signed-overflow story (assume no overflow →
optimize as-if → the loop that iterates backward... the notorious
`x+1 > x` always-true case), the null-deref-then-use case (the check
"provably" dead after the deref), the Linux kernel's compiler barrier
`barrier()` and why volatile is not a memory model (module 09 ch01
connection). Lab: reproduce one UB-driven optimization surprise in clang,
read the opt remarks (`-Rpass=...`).

**Chapter 03 — register allocation.** The problem from first principles:
infinite virtual registers, ~16 physical ones; live ranges (a variable is
"live" from def to last use — the live-range derivation with a small CFG).
Graph coloring: interference graph (nodes = live ranges, edges = overlap),
k-coloring (k = register count) — NP-hard, so heuristic (spill-cost = use
frequency × spill weight). Spilling: a live range loses its register → a
stack slot (the spill slot = the counterexample to module 01 ch04's "locals
are free" — *register pressure is the tax*). Coalescing (killing mov
instructions by giving source/target the same register — the interference
conflict). Linear scan: the JIT's choice (one pass over a sorted interval
list — faster allocation, ~10% worse code: V8/HotSpot/C1's tradeoff
derived). Callee vs caller-saved as an ABI-forced interference decision.
Frame layout emission: alignment (16 bytes — why: SSE alignment contract),
spill slots, saved registers, the final frame diagram annotated (module 01
ch04's diagram, now *derived* rather than asserted — the module's payoff
moment). The insider's observation tools: `-fverbose-asm`, LLVM's
`-regalloc=...` debug dumps, the "watch a loop spill at -O3 but fit at -O2"
phenomenon. Lab: a function with 10/16/20 live variables; watch spill
count explode; find the spill slots in the frame via the disassembly.

**Chapter 04 — escape analysis, write barriers, safepoints.** The compiler's
duties to the GC. Escape analysis: Go's version (does `&x` outlive the
function? → heap else stack; the two-criterion... parameter-escape flow;
`-gcflags=-m` reading — the insider's favorite flag; the "leaking param"
remarks decoded), HotSpot C2's version (scalar replacement — objects
*dissolved* into registers even in a GC language: escape analysis is SROA
for Java — the unification with ch02). Write barriers: generational GCs
need old→new pointer stores recorded; the compiler inserts the check at
*every potentially-intergenerational store* (the card-marking protocol:
512-byte cards, a byte map, the store becomes store-then-mark — the exact
LLVM/GC-insertion insight: `__write_barrier` placement *after* the store,
why; SATB (snapshot-at-the-beginning) vs incremental (module 01 ch12/15's
collectors, now seen from the factory floor). Safepoints: the GC needs
every thread at a known state (all references on the stack mapped) —
compiled code polls (the poll page / poll word on a backedge or return —
why loops poll; time-to-safepoint as the latency insiders obsess over;
the safepoint-bias profiler artifact — profiles sample at polls, skewing
results toward loops: the insider's measurement trap). Deoptimization:
speculative assumptions (profiled types, inlining) must be escapable —
deopt = "uncompile this frame back to interpreter state" (the frame
carries extra metadata for exactly this — module 04's RE payoff: JIT
frames look weird because they're *abortable*). Lab: Go `-gcflags='-m'`
escape decisions; a Java program where scalar replacement visibly removes
allocation (JFR/allocation profiling).

**Chapter 05 — ABI & layout decisions.** The contracts compilers own across
translation-unit and language boundaries. Name mangling: Itanium C++ ABI
(the _ZN... grammar reprise from module 04 ch04 — now from the *emitter's*
side: what mangling must encode: namespaces, templates, CV-qualifiers) vs
MSVC's ?...@ scheme; `extern "C"` as the universal escape hatch (why the
C ABI is the lingua franca of all FFI). Calling conventions as contracts:
System V AMD64 (6 integer registers rdi/rsi/rdx/rcx/r8/r9 + 8 XMM
floats; the first-8-bytes-in-register struct classification algorithm —
small structs split across registers; return in rax:rdx; the stack-argument
spillover and 16-byte alignment), the ARM64 variant (x0-x7 — the uniformity
insight), why Go abandoned plan9... the Go ABI0→ABIInternal story (register
passing finally, 2021). C++ ABI internals: vtable emission (Itanium layout:
offset-to-top, typeinfo, virtuals; the construction vtables and VTT —
why base constructors need their own vtables; thunks for covariant returns
and virtual inheritance), the ABI stability freeze (why C++ compilers
can't reorder struct fields: the ABI *is* the layout, the layout *is* the
promise — versus Rust, which *does* reorder fields (Nicholas... the
`repr(Rust)` reordering: padding minimization, the provenance of the
idea) and requires `repr(C)` to promise layout). Layout decisions with
their flags: `#pragma pack` (the 1-byte packing and its misalignment cost
on x86 — a byte-level diagram of the slowdown), bitfields (the
implementation-defined boundary folklore). Cross-language FFI tax: cgo/JNI/
ctypes transitions (stack switches, pinning, marshalling copies — the
Python `ctypes` call is ~100x a native call: the numbers), COM as the
historical ABI (vtable-only: the minimal ABI that lets any language talk
to any other — module 04's vtable recovery and this chapter's vtable
emission are the same object seen twice). Lab: a C++ class compiled under
Itanium ABI — dump the vtable, verify the layout by hand against the
spec's rules; a packed-vs-unpacked struct timed; a ctypes call benchmarked
against its native twin.

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **Debugging optimized binaries**: you know why your variable "vanished",
  why the null check disappeared, and why gdb needs -O0 — and what to do
  when you can't have it.
- **Performance engineering at the source level**: struct layout, spill
  pressure, escape analysis, write barriers — the levers you actually pull.
- **GC runtime literacy**: card marks, safepoints, deopt — you can read a
  GC log and a JIT profile as machinery, not weather.
- **The factory floor of module 01**: every language chapter's memory model
  now has a visible manufacturer.
