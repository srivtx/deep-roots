# Chapter 05 — Verified compilers and crypto

> **The 30-second version.** Chapters 01-04 built the machines that prove;
> here are the two bottom-of-stack artifacts that carry those proofs.
> CompCert is a C compiler proven in Coq: for any program whose C behavior
> is *defined*, its assembly can only exhibit behavior the source already
> allowed — one theorem composed across ~11 verified passes from Clight to
> Asm. HACL* is a crypto library whose
> Curve25519, P-256, ChaCha20 and Poly1305 arithmetic is proven in F* and
> extracted to C that Firefox shipped. Both come with an honest bill:
> CompCert's ~600 kLOC of Coq at proof:code ~10-15:1 versus seL4's ~100:1
> (the lesson: the more state-machine-like the artifact, the cheaper the
> proof), a fuzzer's field day that CompCert survived, and a trust stack
> that still ends at the assembler, the extractor, the silicon, and your
> intent.

## The theorem: assembly that cannot invent behavior

Drawn carefully, CompCert's correctness statement (Leroy et al.,
2005–2020, x86/ARM/PowerPC/RISC-V back-ends) is a **refinement with a
hypothesis**:

```
for every C program S:
  if S's execution is free of undefined behavior (in CompCert's C model)
  then Behaviors(Asm) ⊆ Behaviors(S)
```

Three clauses earn their ink. **The direction of the subset**: the compiled
program may do *less* than the source (fewer observable steps) but never
*more* — no output, store, or syscall the source semantics could not
produce: "no invented behavior" is the safety property you want from a
compiler. **The hypothesis**: programs that execute undefined behavior
are outside the theorem — the compiler owes nothing to a program whose
semantics is "anything goes." **The behaviors**: observable events only —
I/O calls, volatile accesses, termination mode. Two billion internal
register moves are not observable; one `write(2)` is.

The proof is not one monolith but ~11 verified translations (≈20 counting
each RTL optimization as its own pass, CompCert 3.x), each proven by a
**forward simulation**: every observable target step is matched by a
source step. Simulation is transitive, so the pass proofs *compose*:
Clight → Cminor → RTL → LTL → Mach → Asm glues into one end-to-end
theorem — the compiler as a tower of verified transformations, one
proof-check per arrow:

```
   C source
     │  cpp (external preprocessor, unverified)
     │  C parser (OCaml, unverified edge)
     ▼
  Clight ──✓── C#minor ──✓── Cminor ──✓── CminorSel ──✓── RTL
 (loops and    (expressions  (no loops,    (instruction    (CFG over
  memory ops    flattened,    no switch —    selection,      virtual regs;
  simplified)   no goto)      pure control   arch-specific   const-prop, CSE,
                              trees)         ops chosen)     DCE run HERE ✓)
                                                                │
  Asm    ◄──✓── Mach ◄──✓── Linear ◄──✓── LTL ◄───────────────┘
 (assembly (stack layout, (CFG linearized (physical registers,
  text)     calling       into an ordered  spill slots,
            conventions)  instruction list) frame layout)
     │
     ▼  external assembler + linker (gas/ld, unverified)
  ELF binary
```

Each ✓ is a machine-checked simulation proof. CompCert's register
allocator, scheduler, and every RTL optimization are *inside* the ✓'s —
exactly where production compilers' wrong-code bugs live.

**The alternative trade: translation validation.** Instead of proving the
compiler, write an *unverified* compiler plus a small **verified checker**
that inspects each input/output pair after the fact and accepts or rejects.
Yocto (Tristan & Leroy) validated LLVM optimization runs this way; RockSalt
(PLDI 2012) verified a ~4.5 kLOC validator checking the x86-64 code of
Microsoft's unverified .NET JIT against its MSIL source. The trade: prove
a small, state-machine-like checker once (CompCert's own lesson applied to
itself), but the checker must be *complete enough* to accept good
compilations or the pipeline stalls. Verified compiler = one big proof,
all programs, forever; translation validation = one small proof, per
compilation, with a rejection risk. The shipped artifact chose the first.

## The bill: 600,000 lines of Coq for a 10:1 ratio

The numbers, honestly dated (CompCert 3.x, ~2020): **~600 kLOC of Coq** —
of which the executable algorithms are ~40-50 kLOC (what OCaml extraction
emits), plus ~15 kLOC of C runtime. The other ~90% is specs, invariants,
and proof script. Proof:code ratio ≈ **10-15:1**. Next to chapter 03's
seL4 — ~10 kLOC of C under ~370-400 kLOC of Isabelle at release, past 1M
with the later proof stack, ~20-25 person-years, the headline **~100:1** —
same proof assistants, same decade, ~7-10× the proof-per-code. Why?

**The more state-machine-like the artifact, the cheaper the proof.** A
compiler pass is a *function* from one tree/graph to another: no time, no
concurrency, no resource limits — the proof obligation is a structural
induction over the input shape. A kernel is a *state machine over unbounded
time*: invariants surviving every interleaving of every interrupt, memory
relationships persisting across thousands of steps. CompCert proves what a
transformation *is*; seL4 proves what a system *remains* — worth an order
of magnitude alone. (Both beat the "proofs cost 1000:1" intuition; chapter
01's spec cost, 1-5×, is the floor.)

**The field's best advertisement: Yang et al., PLDI 2011.** Their random
program generators (Csmith, EMI — mutations kept equivalent on chosen
inputs) fed GCC, LLVM, and Open64 millions of tests: **hundreds of
wrong-code bugs**, dozens per compiler, rising with optimization level.
CompCert in the same harness: **~0 wrong-code bugs attributable to its
verified passes** — the few defects traced to the unverified front-end.
Same harness, same inputs: the verified passes are exactly where the
other compilers' bugs were. (Csmith generates *UB-free* programs precisely
so "your program had UB" cannot excuse a found miscompilation.)

## What the proof does not cover: the TCB, one more time

The proof covers the passes, **not the pipeline's edges**. Module 19
ch05's TCB stack, materialized on a real artifact:

```
+------------------------------------------------------------+
| C source you write                                         |
+------------------------------------------------------------+
| cpp + C parser (→ Clight AST)         unverified front-end |
+==================== PROOF STARTS HERE =====================+
| Clight→C#minor→Cminor→CminorSel→RTL→LTL→Linear→Mach→Asm   |
|        ~11 passes, each a forward simulation ✓ in Coq      |
+===================== PROOF ENDS HERE ======================+
| .s text → assembler + linker (gas/ld)     unverified edge  |
| runtime library (~15 kLOC of C)           unverified edge  |
+------------------------------------------------------------+
|          the proof itself sits on a base:                  |
| Coq kernel (~30 kLOC, LCF-style, module 21 ch01)  TRUSTED  |
| Coq→OCaml extraction + OCaml compiler + runtime   TRUSTED  |
| the C semantics model (the theorem is about this) TRUSTED  |
+------------------------------------------------------------+
```

Four edges to stare at. **The extraction**: the proofs are about Coq
functions; the compiler you *run* is OCaml from Coq's unverified
extraction and an unverified OCaml compiler. **The assembler/linker**:
the proof ends at Asm text; `.s` to ELF is gas, ld, and the loader.
**The front-end**: cpp and the parser produce Clight — a parsing bug
puts you outside the theorem. **The C semantics**: the theorem relates
behavior to *CompCert's C model*, a human-written Coq artifact; where it
disagrees with ISO C, the theorem is about the wrong language (the model
deliberately defines some behaviors ISO C leaves undefined, to cover
more real programs). Same four-sided shape as seL4's C-parser story in
chapter 03; the answer is the same: the base is *small and stable* (de
Bruijn), the edges are *tested* (Yang et al's harness), not proven.

**The next rung down, and the proof tower's top floor.** CompCert's RISC-V
port consumes the **Sail RISC-V ISA model** — the official formal spec,
one Sail source generating Isabelle, Coq, HOL4 and Lean versions, so spec
and proofs share one source of truth. ISA-level information-flow proofs
live there too (CHERI's capability safety, proven in Isabelle, 2019-2020).
Above the ISA, MIT's **Kami** framework (Bluespec + Coq) has produced
verified RISC-V core pipelines — proofs at **RTL**, module 17 ch02's
pipelined state machines proven to implement the ISA. The tower's
top floor: F*/Low* proves C (HACL*), Coq proves Clight→Asm
(CompCert), Sail+Kami prove RTL→ISA. Below RTL: synthesis,
place-and-route, the fab, the errata — module 17 ch05's economics: a
fabricated Kami core is a proof about a *model* of the foundry; the
silicon has no theorem on it.

## HACL*: the crypto library that proves its arithmetic

**HACL*** (Bhargavan et al., CCS 2017; the
INRIA/Microsoft Everest effort) is Curve25519, P-256, ChaCha20, Poly1305,
SHA-2 and friends, written in **F*** with the **Low*** subset, proven
functionally correct, extracted to readable C by **KreMLin** — no malloc,
no unbounded loops, compilable by any compiler, CompCert included.

What the proofs prove, on Curve25519: the field is integers mod 2^255-19,
but the machine has 64-bit words, so arithmetic is limb arrays, partial
products, and **carries**. The F* spec is literally `Z → Z → Z mod p` —
the schoolbook meaning. Each limb operation must be proven to *equal* its
mathematical result, which needs the **carry-chain bounds**: every
intermediate sum of limb products shown to fit its 2^64 (or 2^128)
container — lemmas bounding carries, so no silent wraparound, ever.
Across ~50 field and group operations: implementation is the minority of
the lines, specs and lemmas the mass. P-256 is the same story with worse
constants; ChaCha20 and Poly1305 are ARX proofs against the RFC 7539/8439
definitions.

**The constant-time honesty — the tested/proven spectrum, drawn.** This
is where HACL* must not be oversold:

- **Proven**: functional correctness — the math is right, all inputs, once.
- **Enforced by typing**: Low* separates *public* and *secret* machine
  integers; the F* type checker rejects branch conditions and array
  indices computed from secrets — about the generated C's shape, not the
  binary.
- **Proven for specific assembly**: **Vale** (IEEE S&P 2018) proves asm
  implementations (AES-NI, Poly1305, ChaCha20) equivalent to their specs,
  with constant-time proven as an operational property for those modules;
  **CT-verify** (USENIX Sec 2016) uses Frama-C's WP engine to prove C has
  no secret-dependent branches *or addresses*.
- **Tested**: dudect and ctgrind — BoringSSL/OpenSSL's verification-
  adjacent tooling — statistical timing tests and valgrind taint checks
  in CI: constant-time *claimed and measured*, not proven.
- **Still open**: composition (the protocol layer above), the C compiler
  re-optimizing the extracted code (why the C is written
  optimization-resistant and *tested post-compile*), and the
  microarchitecture (Spectre, SMT — module 18 ch05's cache side channels,
  below every proof here).

**Everest, dated honestly.** The milestone is real but partial: miTLS-era
work verified TLS record-layer and handshake *models* (2014-2017);
HACL*/EverCrypt (2020) delivered the linkable verified crypto providers.
A complete end-to-end *verified TLS implementation* a browser
could adopt wholesale is still open as of 2025 — progress, no shipped
drop-in. Deployed: the Everest project reports verified code in
**Firefox** (NSS) and pieces in **Windows and Azure** (2020-2024). The
headline moment: Firefox 57 (November 2017), NSS's X25519 replaced by
HACL*-extracted code — verified field arithmetic in hundreds of millions
of browsers, more of the catalog landing later.

**The performance bill** — "proofs cost 10×" died here. Representative
ratios against hand-tuned baselines (x86-64, HACL*/EverCrypt papers;
treat as the window, not constants — your CPU will differ):

| Primitive        | Hand-tuned baseline   | Verified vs. baseline |
|------------------|-----------------------|-----------------------|
| X25519 scalar mult | fastest asm (Sandy2x-class) | ~1.05–1.15× |
| P-256 ECDH/sign  | OpenSSL assembly      | ~1.2–1.3×             |
| ChaCha20         | OpenSSL assembly      | ~1.1–1.2×             |
| Poly1305         | fastest assembly      | ~1.05×                |

Within 1.05–1.3× of hand-tuned with proofs attached: verification cost
has moved from "unaffordable" to "a rounding error."

## The honest boundary and the economics of paying

Stated plainly, what remains unproven in 2026 — for every artifact in
this book:

- **Hardware.** Rowhammer flips bits from across the address space;
  glitching and EM injection change control flow — module 17's physics
  is below every theorem here.
- **Supply chain.** The binary you run is not the source you read. The
  bridge, if built, is **reproducible builds** (bit-identical rebuilds
  anyone can check) and provenance frameworks like **SLSA** — *verifiable*
  attestations (signed claims about who built what from what), not a
  *verified* build process. The gap between those words is the gap
  between evidence and proof; marketing walks it freely.
- **Specifications.** seL4's verification found **~160 spec bugs** before
  shipping — caught because the proof forced the spec to be written
  completely. The formal-methods slogan: *"we proved the code matches the
  spec; the spec matches your intent is your problem."* The proof
  protects the implementation from itself, not the requirements from you.
- **Everything above the verified unit.** The protocol layer, the
  composition, the humans.

**When to pay: the four-condition checklist.** Verification amortizes
when at least three hold: (1) **safety-critical** — failure kills people
or missions; (2) **high-assurance** — adversaries hunt your bugs (crypto,
kernels); (3) **long-lived** — a decade or more of maintenance, so the
proof keeps paying; (4) **reused** — many consumers divide the cost.
The amortization arithmetic that makes the 100:1 pill swallowable:

| Project | One-time cost        | Reused across        | Per-year at 15-yr reuse |
|---------|----------------------|----------------------|-------------------------|
| seL4    | ~20-25 person-years  | every downstream kernel user | ~1.5 py |
| CompCert| ~10-15 py (est.)     | every compiled C program      | <1 py |
| HACL*   | Everest-scale, multi-team | Firefox, Windows, Azure, every consumer | divided by all |

The alternative column: one Heartbleed-class incident — module 18 ch05
priced it at 17% of the encrypted web — costs more than any line in that
table. Verification is not the expensive option; it is the option whose
price is *up front and printed*.

**The future, stated as a bet.** LLM-assisted proving (2024-2025):
AlphaProof's IMO-silver Lean proofs, copilots, autoformalization. The bet
— a bet, not a fact — is that 100:1 becomes 10:1 by outsourcing the
*routine* proof lines (sledgehammer folklore is the precedent:
automation already eats the routine 80%). What nobody claims the machines
eat soon: invariant discovery, spec writing, taste. Hold any "LLMs make
verification free" claim to this chapter's standards: which theorem,
checked by what kernel, on which TCB.

## What insiders know

- **The extraction trust question.** Coq→OCaml extraction is ~10 kLOC of
  unverified translator plus an unverified OCaml compiler; it has had real
  bugs — CompCert's binary is *tested* software that *emits* proven
  software. seL4's mirror: the C-to-Isabelle parser — the theorem relates
  the parser's Simpl output to the spec, "the proof is about the parser's
  output," hand-audited. The RISC-V Sail model partly collapses this
  class: one spec source shared by CompCert, the proofs, and the
  architecture tests.
- **Sledgehammer folklore.** In a well-watered Isabelle development,
  ~80% of routine proof steps fall to the hammer (E, Z3, Vampire, CVC);
  the other ~20% — inductions, invariant discovery — is ~80% of the
  effort. Papers never price the maintenance: change the spec and the
  proof edits are measured in weeks, not lines.
- **The verified-GC story closes module 19 ch04.** The verified-GC line
  (Microsoft Research, ~2008-2010: RISCY-GC, Verve's Boogie-proven
  runtime) proved the collector correct *against the mutator's
  invariants*: "if every mutator pointer store executes the write
  barrier, then at every safepoint the snapshot invariant holds and the
  root scan is complete." The write barrier you met as an instruction cost
  in module 19 ch04 reappears as the consecution step of a Hoare-logic
  proof — *justified by a lemma* — and ch04's frame rule keeps that proof
  per-object, not per-heap.
- **The UB caveat is the interview trap.** "CompCert proved it cannot
  miscompile" is false as stated; the theorem needs a defined-behavior
  source. The EMI compilers miscompiled *UB-free* programs — that is what
  made it evidence. The candidate who states the theorem *with its
  hypothesis* passes; the one who doesn't hasn't read it.
- **Proof density is a per-dependency-closure number.** `Curve25519.fst`
  may be 15% executable lines, but it leans on the whole `Lib`/`Mem`
  closure — the honest ratio counts everything the file's proofs consume.
  Vendors quoting 2:1 quote the file; the field quotes the closure.
- **The Yang et al numbers move.** "Hundreds of bugs" is the across-
  compiler total (GCC, LLVM, Open64); per campaign, dozens of confirmed
  wrong-code bugs each. CompCert's clean sheet is "zero in the verified
  passes" — not "zero bugs": front-end and pretty-printer corners did
  defect.

## See it yourself

**Lab A — run the verified compiler (or read its pass list).**

```console
$ sudo apt install opam            # then: opam init; eval $(opam env)
$ opam install -y compcert         # pulls Flocq, Menhir; builds ccomp
$ cat > sum.c <<'EOF'
#include <stdio.h>
int sum(int n) { int s = 0; for (int i = 1; i <= n; i++) s += i; return s; }
int main(void) { printf("%d\n", sum(100)); return 0; }
EOF
$ ccomp -o sum sum.c && ./sum       # the "it's a real compiler" moment
5050
$ ccomp -dall sum.c                 # dump EVERY intermediate language
$ gcc -O2 -S sum.c && clang -O2 -S sum.c   # compare the mortals
```

What to notice: `-dall` writes one file per stage (`sum.clight`,
`sum.cminor`, `sum.rtl`, `sum.ltl`, `sum.mach`, `sum.s`) — count them and
walk the diagram by hand; the paper version, if opam is not an option,
is the CompCert manual's pass list. The asm is conservative (between gcc
`-O0` and `-O2`). The *moment* needs no paper: it compiled, it ran,
every arrow had a proof.

**Lab B (mandatory, free) — measure the proof:code ratio of real verified
crypto.**

```console
$ git clone --depth 1 https://github.com/project-everest/hacl-star
$ cd hacl-star
$ wc -l code/Curve25519*.fst code/Chacha20*.fst code/Poly1305*.fst
$ wc -l code/Lib*.fst code/Mem.fst        # the closure starts here
$ grep -c "^let " code/Curve25519*.fst    # executable definitions
$ grep -c "^lemma \|lemma " code/Curve25519*.fst code/Lib*.fst   # proofs
```

Expect (2023-2024 tree; counts drift): each primitive file 0.5-3 kLOC,
implementation lines a small minority, the `Lib`/spec closure dwarfing
the primitives. Compute `let`-lines / total over the closure — the real
proof:code ratio of shipped verified crypto, your number, not a paper's.
Then look at `vale/`, the assembly-level proofs.

**Lab C — close the tested/proven spectrum with your own measurement.**
Module 18 ch05's dudect harness, re-run. Python's `pow(g, e, p)` does
windowed exponentiation on big integers: its cost depends on the
exponent's Hamming weight — a leak, measurable at your desk:

```python
import time, random, math
P = (1 << 255) - 19                          # Curve25519's field prime
def bench(e):
    t0 = time.perf_counter_ns(); pow(3, e, P); return time.perf_counter_ns() - t0
A = [bench((1 << 200) + 3) for _ in range(2000)]          # Hamming weight 2
B = [bench(random.randrange(1 << 255)) for _ in range(2000)]  # weight ~128
ma, mb = sum(A)/len(A), sum(B)/len(B)
va = sum((x-ma)**2 for x in A)/(len(A)-1); vb = sum((x-mb)**2 for x in B)/(len(B)-1)
t = (ma-mb)/math.sqrt(va/len(A) + vb/len(B))
print("t = %.1f  (|t| > 5: the classes are distinguishable)" % t)
```

Run it a few times: `t` lands far past ±5 — the fixed low-weight class
is *faster* (≈126 fewer big-integer multiplies), the secret is readable
from the timings. The same harness against the `cryptography` library's
Ed25519 (fixed vs. random message, 2,000 signs each): the t-statistic
falls into the noise band. Read the result honestly: the library is
*tested* constant-time (engineered, measured, dudect-passing on your
machine); HACL*-class code is *typed-and-proven* at the source level,
Vale-proven for specific asm. Your measurement can only ever establish
the first rung — now you know which rung any "constant-time" claim
sits on. That was the point of the ladder.

## How to use this knowledge

- **Reading verification claims.** Any "verified" in a datasheet or
  proposal: ask for the theorem statement (with its hypothesis), the TCB
  list, and what is merely tested — three questions you now own.
- **Deciding whether to pay.** Apply the four-condition checklist: a
  crypto library or a compiler scores 4; your CRUD endpoint scores 1 —
  the correct answer, not a failure.
- **Compiler-bug triage.** A defect that appears only at `-O2`: reduce
  with Csmith or creduce first — and if you *need* the bug to not
  exist, `ccomp` is a real, installable answer with a theorem where the
  bug class lived.
- **Supply-chain audits.** A vendor waving SLSA: *verifiable*
  provenance (signed claims, checkable) ≠ *verified* build (proof).
  Reproducible builds + diffoscope are the honest bridge to the artifact
  you ship.
- **Interviews.** State CompCert's theorem with its UB hypothesis and
  subset direction; quote 10-15:1 vs ~100:1 with the state-machine lesson;
  know where the proof tower stops.

## Connections

- **Module 21 chapter 01** — the Coq kernel and LCF architecture
  CompCert's 600 kLOC of *untrusted* proof lean on: ~30 kLOC of checker is
  why "the proof is a file" beats "trust the tool."
- **Module 21 chapter 02** — the automatic bottom of this tower: model
  checkers and SMT solvers hammer the routine goals; the state-explosion
  budget is why these proofs are hand-built.
- **Module 21 chapter 03** — seL4's ~100:1 economics and the compiler-bug
  hole in its trust base; this chapter closes it.
- **Module 21 chapter 04** — the frame rule is the workhorse of every
  proof here; the verified-GC story hands module 19 ch04's write barrier
  its justification lemma.
- **Module 10 chapter 01** (mandatory) — the compiler's proof mirror:
  Clight/Cminor/RTL/LTL is module 10's frontend-IR-backend ladder walked
  with theorems on the arrows; the wp calculus and the compiler walk the
  same control-flow graph, one emitting formulas, one emitting code.
- **Module 18 chapter 05** (mandatory) — the constant-time spectrum: its
  dudect/ctgrind harness is this chapter's lab C; its side-channel
  incidents are what the proven/typed/tested ladder is for.
- **Module 17 chapter 02** (mandatory) — the proof tower's top floor:
  Kami's verified RISC-V cores are module 17's pipelines proven against
  the Sail ISA; below RTL, ch05's silicon economics and errata take
  over.
- **Module 04 chapter 05** (mandatory) — the same wall from the RE side:
  a stripped binary is unreadable, and you cannot prove what you cannot
  read; decompilers approximate, reproducible builds bridge, and this
  chapter's proofs never reach the bytes without them.

## Quiz

1. **Concept.** State CompCert's correctness theorem precisely enough to
   expose its hypothesis and the direction of its behavior subset. Why
   must the subset point *that* way, and what could a compiler get away
   with under the reverse inclusion?
2. **Concept.** CompCert's proof:code ratio is ~10-15:1; seL4's is ~100:1.
   Name the artifact property that drives this factor, and the two
   dimensions a compiler's passes lack that a kernel has in abundance.
3. **Predict the number.** CompCert 3.x: ~600 kLOC of Coq; trusted base
   roughly: Coq kernel ~30 kLOC, extraction ~10 kLOC, parser/front edge
   ~5 kLOC, C runtime ~15 kLOC. GCC is ~1.5 MLOC, essentially all
   trusted. Compute (a) CompCert's trusted total, (b) how many times
   smaller than GCC's, (c) what the other ~540 kLOC are and why they
   need not be trusted.
4. **Predict the behavior.** In lab C, class A times `pow(3, (1<<200)+3,
   P)` and class B times `pow(3, random 255-bit, P)`. Which class has
   the lower mean, roughly how many big-integer multiplies separate
   them, and what Welch t-statistic verdict do you expect?
5. **Predict the behavior.** A C program executes signed-integer overflow;
   at `-O2` GCC miscompiles it, and a different compiler produces assembly
   whose behavior differs from any reading of the source. Is CompCert's
   theorem refuted if CompCert produced that assembly? Which clause
   decides, and how did Yang et al. design around this excuse?
6. **Bug diagnosis.** A crypto library passes dudect on the vendor's test
   machine; a month later a Flush+Reload attacker recovers keys from a
   table lookup indexed by a secret byte. Which rung of the spectrum
   failed, what two things did dudect not exercise, and which tool would
   have caught it?
7. **Bug diagnosis.** A vendor advertises "our verified compiler cannot
   produce incorrect object code." Diagnose: name at least three holes,
   one from the theorem's hypothesis and two from the TCB edges.
8. **Cross-chapter.** You are handed a stripped ELF binary whose vendor
   claims "compiled from verified components." From module 04 chapter
   05's side of the wall: what can you *not* establish about the bytes,
   and which practice from this chapter is the only bridge — and what
   exactly does it give you (and not give you)?

<details>
<summary>Answers</summary>

1. **Statement**: for every C program S free of undefined behavior (in
   CompCert's C model), `Beh(Asm) ⊆ Beh(S)`. The direction is *refinement*:
   the compiled program may do less but never more — no output, store, or
   termination mode the source could not produce; every observable
   wrong-code bug is a theorem violation. Reversed, the theorem would
   guarantee the assembly exhibits *all* the source's behaviors — a
   compiler could "correctly" add outputs: completeness, not safety.
2. **State-machine-likeness.** A compiler pass is a synchronous function
   from one tree/graph to another — obligations are structural inductions.
   The two dimensions a kernel has and a pass lacks: **unbounded time**
   (invariants surviving every interleaving) and **concurrency/state**
   (interrupts, shared-memory relationships across thousands of steps).
   Hence ~10-15:1 vs ~100:1.
3. (a) 30 + 10 + 5 + 15 = **~60 kLOC trusted**. (b) 1.5M / 60k ≈ **~25×
   smaller** than GCC's. (c) The other ~540 kLOC are specs, invariants,
   and proof script — *untrusted* by the de Bruijn criterion: a bug there
   cannot forge a theorem, because the Coq kernel checks every proof
   object — the entire point of small-checker-big-prover.
4. Class A (Hamming weight 2) is **faster**. Square-and-multiply performs
   ~255 squarings regardless plus roughly one multiply per set bit:
   A ≈ 2-3 multiplies, B ≈ ~128 — **~125 fewer big-integer multiplies**,
   each a growing operand-size cost. Expect `t` strongly negative,
   |t| far beyond 5: distinguishable — that is the leak.
5. **Not refuted.** The hypothesis — defined behavior — is violated;
   CompCert owes the program nothing. Yang et al. generated UB-free
   programs (Csmith by construction, EMI by keeping mutations equivalent
   on tested inputs) precisely so "your program had UB" could not excuse
   the hundreds of GCC/LLVM/Open64 miscompilations they found — which is
   why CompCert's zero wrong-code result on the same inputs is evidence,
   and the vendor-slogan version (Q7) is a trap.
6. The **tested** rung failed — dudect is a statistical timing check. It
   did not exercise (1) **secret-dependent memory addresses**: warm caches
   on the vendor's machine hid the access pattern that Flush+Reload's
   attacker-controlled eviction exposes; (2) **attacker control of
   microarchitectural state** — dudect samples under benign conditions.
   CT-verify (Frama-C/WP proof that no branch or *address* depends on
   secrets) would have rejected the table lookup outright; ctgrind flags
   secret-dependent addresses; Vale pins constant-time for specific asm.
7. Hole 1 — **the UB hypothesis**: programs executing undefined behavior
   (signed overflow, strict-aliasing, out-of-bounds) are outside the
   theorem. Hole 2 — **the TCB edges**: extraction and the OCaml compiler
   are unverified, and the external assembler/linker turn proven Asm
   *text* into unproven bytes. Hole 3 — **the front-end and C model**:
   the theorem is about CompCert's C semantics and its Clight input, not
   your text or the ISO corners — plus the spec-intent gap: a perfect
   compiler only refines the semantics it was given.
8. From the RE side you cannot prove *anything* about the bytes: module
   04 ch05's decompilers give plausible, lossy approximations — you cannot
   recover the source that was proven, so no F*/Coq theorem reaches the
   binary ("you can't prove what you can't read"). The bridge is
   **reproducible builds**: rebuild from the claimed source with a fixed
   toolchain, compare bit-for-bit (diffoscope). That gives *verifiability*
   — the bytes match the source, conditional on the build environment —
   not a *proof* of the build itself: the toolchain remains trusted
   (SLSA's "verifiable, not verified"). The proof tower ends at the
   source; the binary is bridged, never proven.

</details>
