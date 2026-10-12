# Chapter 05 — Decompilers

> **The 30-second version.** A decompiler runs the compiler backwards — not by
> "uncompiling," which is impossible, but by *re-deriving* an equivalent
> program from the bytes: disassemble, lift to a clean IR (p-code, microcode,
> BNIL), run data-flow analysis, recover variables, guess types, re-structure
> the CFG into if/while. Structure comes back — call graph, frame sizes,
> constants, vtable calls — because structure is *semantics*. Names, comments,
> macros, and templates never come back because they never were. The output is
> a *witness* — a program with the same behavior — not *the* program, and
> reading it well means knowing where the heuristics guessed.

## Inverting a many-to-one map

Compiling is a function from source programs to byte sequences, and that
function is many-to-one: rename every variable, expand a macro, inline a
`static` helper — the bytes do not move. So the inverse "take bytes, produce
the source" has no unique answer, and no algorithm can pick the right one,
because nothing in the output distinguishes the candidates. Renaming alone is
a semantics-preserving bijection: `user_count` and `local_18` compile to the
same register moves.

That fact organizes the field. A decompiler cannot aim for *the*
source; it can only aim for *a* program whose observable behavior matches the
bytes. Everything recoverable is recoverable because it constrains behavior;
everything lost was erased precisely when it stopped affecting behavior. The
pipeline below is the shortest derivable path from bytes to a readable,
behavior-equivalent program.

## The pipeline: lift, analyze, structure

```text
  bytes in        THE DECOMPILER PIPELINE (Ghidra/IDA/Binary Ninja)      C-shaped text
 (ELF .text)                                                                  (a witness)

+---------+    +-----------+    +-----------------+    +----------------+    +----------------+
| 8b 45   |--> | DISASM:   |--> | LIFT to IR:     |--> | DATA-FLOW:     |--> | STRUCTURING:   |
| 10 89 5c|    | recursive |    | p-code /        |    | def-use chains |    | CFG -> if/else |
| 24 fc ..|    | descent   |    | microcode /     |    | const propag.  |    | while; render  |
+---------+    +-----------+    | BNIL            |    | alias analysis |    | variables      |
               follow calls,   +-----------------+    +----------------+    +----------------+
               mark code/data     ^        |                ^      |
                                   |        v                |      v
                                   |   one simple op per     |   VARIABLE RECOVERY:
                                   |   step: flags folded,   |   stack slots + registers
                                   |   mem ops explicit,     |   coalesced into locals
                                   |   registers -> SSA      |   + TYPE PROPAGATION
                                   |   values (varnodes)     |      (feeds back upward)
```

**Disassembly** comes first (chapter 03's recursive-descent recipe: follow the
calls, decode what is reached). The result is still unusable for reasoning:
x86-64 has flags as hidden side effects — `cmp` writes 6 bits of state that a
later `jcc` silently reads — plus memory operands buried inside arithmetic and
one register storage with four meanings (`al`, `ax`, `eax`, `rax`).

**Lifting** fixes that: each native instruction becomes a sequence of RISC-like
IR operations that expose every implicit effect:

- **Ghidra p-code** (Ghidra 11.x): each instruction becomes one or more ops,
  each reading at most one memory location; state lives in *varnodes* —
  (address space, offset, size) triples, effectively infinite registers.
  `add rax, [rbx]` becomes LOAD, INT_ADD, STORE.
- **IDA microcode** (Hex-Rays, IDA Pro 8/9): the same idea, refined through a
  ladder of maturity levels (generated → optimized → mature); decompiler
  plugins can rewrite the IR at each level before final C generation.
- **Binary Ninja BNIL**: three exposed levels — LLIL (1:1 lift), MLIL
  (registers become SSA values, flag idioms folded into single comparisons),
  HLIL (structured, typed) — stepping between them is a UI action.

The lift is where `cmp eax, 5` followed by `jg` collapses into one `INT_LESS`
feeding a branch, and where `movsx`/`movzx` expose the signedness the machine
was carrying implicitly.

**Data-flow analysis** then asks the classical questions: where each value is
defined, which uses it reaches, what constants fold through, which stack slots
alias — an optimizing compiler's analysis machinery, run in reverse, to
*understand* instead of to *transform*.

**Variable recovery** coalesces the IR's per-register and per-slot values into
source-like variables: `[rbp-0x18]` becomes `local_18`. But the compiler's
register allocator already ran: two slots that never hold simultaneously-live
values at `-O1` and above are often merged into one — and the decompiler
faithfully reports one variable where the source had two.

**Type propagation** spreads guesses outward from anchors: a `malloc(0x18)`
return is a 24-byte something; a pointer stored into an object slot then
loaded and called through is a vptr (chapter 04). Every constraint propagates
to every consumer, so one good anchor types a dozen functions.

**Structuring** turns the control-flow graph back into if/else/while. The
honest limit, in three steps: (1) goto-free C generates exactly the
*reducible* CFGs; (2) compilers occasionally emit irreducible flow (computed
`goto`, Duff's-device-shaped switches, some optimized state machines); (3) any
irreducible CFG *can* be structured by duplicating blocks (node splitting),
but duplication can blow up exponentially in the worst case. Every structurer
picks a compromise: duplicate a little, wrap the rest in `goto`, or invent a
flag-and-loop shape the source never had. Cifuentes' 1994 thesis — the founding
document of modern decompilation — already said it: perfect structuring is not
always possible, and `goto` in decompiler output is not a bug, it is the proof.

## What survives, what is gone forever

```text
   COMES BACK (behavior constrains it)        GONE FOREVER (never was behavior)
   -------------------------------------      ------------------------------------
   direct call graph     call rel32           names          renaming is free
   stack frame sizes     push rbp; sub rsp,N  comments       never compiled
   register locals       every def/use        high-level int/unsigned split
   constants             immediates + .rodata macros         erased pre-parse
   jump tables           data at fixed addr   templates      -> concrete instances
   vtable calls          vptr + table layout  inlining choices, branch pruning,
                                               which overload, NDEBUG branches
```

The right column is not "hard to recover" — it is *provably absent*. Macros
expand before parsing; a macro and its expansion are one program by the time
codegen runs. Templates stamp out concrete instances: `max<int>` and
`max<long>` compile to two unrelated functions, and the fact that they shared a
source template is gone. Compile-time decisions — the inlined helper, the
bounds check removed because the compiler proved the index in range, the
`#ifdef DEBUG` block — are erased by the same rule: once they can't change
behavior, they don't exist.

## Type guessing: heuristics all the way down

Since types are gone, decompilers reconstruct them from three families of
evidence, weakest to strongest:

1. **Sizes at call sites.** `mov edi, 0x18; call malloc` means "allocate 24
   bytes"; writes to `+0x10` and `+0x14` in that region are 4-byte fields at
   offsets 16 and 20; untouched offsets 0–15 are an array, padding, or a field
   written elsewhere. Accumulate enough offsets and a struct layout emerges —
   chapter 03 built exactly this by hand.
2. **API consumption.** A value passed to `fopen` is a `FILE *`; a 32-byte
   object passed to libstdc++ string methods matches the SSO layout of
   chapter 04 (64-bit: 15-char inline threshold, pointer/capacity/size union).
   Known-library identification (Ghidra's FID, IDA's FLIRT) imports thousands
   of such types at once.
3. **Struct-field offsets, propagated.** One call site typed, all callers
   inherit constraints. Propagation is self-reinforcing — the feature and the
   trap: one wrong signature poisons a neighborhood, which is why fixing one
   signature and watching the spread is the standard first ten minutes of a
   session.

The output labels — `undefined4`, `int *`, `Player *` — are *hypotheses with
confidence levels*, not facts. Ghidra says so literally: its default integer
type is `undefined`, an admission.

## Where decompilers lie

Four failure modes account for most wrong decompilations you will meet:

- **Variable merge/split errors.** The compiler merged two source variables
  into one stack slot (disjoint live ranges); the decompiler shows a single
  `local_18` that is a `char *` for twenty lines and an `int` after. The
  mirror image: one source variable spilled across a register *and* a stack
  slot becomes two decompiler variables with no visible relation.
- **Inlined functions.** At `-O2` the helper's boundary is gone — a
  400-line decompiled "function" is five helpers plus their caller, with no
  call edges. The call graph silently undercounts.
- **Optimized-away bounds.** Signed-overflow UB and provably-narrow ranges
  let the compiler delete checks that *exist in the source*. The decompiled
  code "has no bounds check" — and the analyst concludes the programmers never
  wrote one. Sometimes the compiler ate it: diff an `-O0` build before blaming
  anyone.
- **Indirect calls.** `call qword ptr [rax+0x18]` has no statically known
  target: no call-graph edge, no argument-type constraint, no propagation. In
  optimized C++ a large fraction of edges are virtual dispatch (vtables and
  RTTI — chapter 04 — recover those); the rest — runtime-built function tables,
  op tables in bytecode VMs (chapter 06) — need dynamic reasoning the
  decompiler does not attempt. This is *the* hole: the target set CFI would
  need is exactly the set the decompiler cannot see.

## Scripting: the headless loop

Everything above is automatable. Ghidra ships `analyzeHeadless`; IDA runs
`idat64 -A -Sscript.py`; Binary Ninja is importable (`import binaryninja`).
The canonical loop: nightly build → headless decompile → export signatures →
diff against last night. Ghidra 11.3 (2025) removed Jython in favor of
PyGhidra/Python 3. The vtable-write census from chapter 04 in about ten lines
(Ghidra 11.x; API names drift slightly between versions):

```python
# vtable_writes.py -- list every store of a vtable address (the vptr write)
listing = currentProgram.getListing()
for ins in listing.getInstructions(True):        # every instruction, in order
    if ins.getMnemonicString() in ("MOV", "MOVABS"):
        for ref in ins.getReferencesFrom():      # refs from operands
            if str(ref.getReferenceType()) == "DATA":
                # MOV [mem], offset vtable -> the vptr store pattern (ch. 04)
                print("%s: %s -> vtable at %s"
                      % (ins.getAddress(), ins, ref.getToAddress()))
```

Run it headless:

```bash
support/analyzeHeadless /tmp/ghidra-projects m04ch05 \
    -import /tmp/lab5/lab5.o -postScript vtable_writes.py
```

In IDA and Binary Ninja the equivalent hook is the function-type editor: set
a prototype once (`Y` in both) and it propagates into every caller.

## What insiders know

- Names are *provably* unrecoverable: renaming is a semantics-preserving
  bijection, so no algorithm — present or future — can pick the original.
  `uVar1` and `local_10` are not failure; they are the theorem.
- The variable-merge trap is the #1 misread: one variable serving two
  unrelated types across a loop boundary. Test by widening the type and
  watching one half of the uses break — that half was a different source
  variable.
- Indirect calls are the structural hole: in a typical optimized C++ binary
  a double-digit percentage of dispatch is virtual or function-pointer based
  (varies per binary), and the static call graph omits all of it. Never trust
  "X is never called" from a static graph.
- Diff-tool economics: BinDiff 7+ (2023) is free for non-commercial use;
  Diaphora is free (GPL) on top of IDA. Minutes of compute turn a vendor patch
  into "these 12 changed functions" — the patched bounds check *is* the
  vulnerability write-up. That is the 1-day economy, and why patch-gap windows
  are measured in days.
- Headless entry points are where professional work happens:
  `analyzeHeadless` (Ghidra), `idat -A -S` (IDA), `import binaryninja` (Binja)
  — analysis that isn't in CI doesn't happen nightly.
- The custom passes live at the IR level: IDA decompiler plugins rewrite
  microcode before C generation — how D-810-style deobfuscation passes
  un-flatten control flow inside the decompiler, not by patching bytes.
- The twin-build trick: decompile the `-O0` build for structure and
  pseudo-names, the `-O2` build for what ships. The union is closer to the
  truth than either alone.

## See it yourself

Ghidra is not in this module's toolchain, so the Ghidra part is commands plus
*described* output — run it if you have a copy; the rest reproduces anywhere
with `gcc` and `objdump` (verified here on Debian gcc 14.2.0, x86-64).

**Part 1 — Ghidra headless (commands; expected output described).**

```bash
mkdir -p /tmp/ghidra-projects /tmp/lab5
support/analyzeHeadless /tmp/ghidra-projects m04ch05 \
    -import /tmp/lab5/lab5.o -postScript vtable_writes.py
```

Expected: an import report (`Import succeeded`, `Analysis succeeded`,
function count 2) plus the script's output. In the GUI, `sum_abs` decompiles
roughly so — variable *names* will differ, the *shape* will not (described,
not run):

```c
int sum_abs(int *a, int n)
{
    int total;
    if (n < 1) {
        total = 0;
    }
    else {
        do {
            total = total + (*a < 0 ? -*a : *a);
            a = a + 1;
        } while (a != a_end);
    }
    return total;
}
```

A `do-while` with a guard — not the `for` loop that was compiled. The
structurer chose guard + do-while; the source's counter is absent for the
reason Part 2 exposes.

**Part 2 — hand-structuring the same function via objdump.**

```bash
cd /tmp/lab5
gcc -O2 -c lab5.c -o lab5.o
objdump -d --no-show-raw-insn -M intel lab5.o | sed -n '/<sum_abs>:/,/^$/p'
```

```text
0000000000000020 <sum_abs>:
  20:  test   esi,esi
  22:  jle    60 <sum_abs+0x40>          ; if (n <= 0) skip the loop
  24:  movsxd rsi,esi                    ; n sign-extended to 64-bit
  27:  xor    ecx,ecx                    ; total = 0
  29:  lea    rsi,[rdi+rsi*4]            ; end = a + n (pointer arithmetic!)
  40:  mov    edx,DWORD PTR [rdi]        ; v = *a
  42:  mov    eax,edx
  44:  neg    eax                        ; t = -v
  46:  cmovs  eax,edx                    ; if sign: t = v    (abs idiom)
  49:  add    rdi,0x4                    ; a++
  4d:  add    ecx,eax                    ; total += |v|
  4f:  cmp    rdi,rsi
  52:  jne    40 <sum_abs+0x20>          ; while (a != end)
  54:  mov    eax,ecx
  56:  ret
```

The source loop was `for (int i = 0; i < n; i++) total += abs(a[i])`. The
compiler deleted `i`: the induction variable became pointer arithmetic (`lea`
for `end`, `add rdi,4` for the increment, `cmp` of two *pointers* for the
condition). A decompiler can only emit what the dataflow supports — a pointer
walking to an end — so the index variable is unrecoverable *by construction*,
the same theorem as the names. Hand-write what a decompiler would emit —
guard, do-while, abs idiom, pointer increment — and compare with Part 1.

**Part 3 — verify the struct heuristic from call-site sizes.**

```bash
objdump -d --no-show-raw-insn -M intel lab5.o | sed -n '/<make_player>:/,/^$/p'
```

```text
0000000000000000 <make_player>:
   0:  push   rbx
   1:  mov    ebx,edi                   ; save hp (callee-saved spill)
   3:  mov    edi,0x18                  ; malloc(24)  <- struct size evidence
   8:  call   d <make_player+0xd>       ; (relocated call in a .o)
  12:  mov    DWORD PTR [rax+0x10],ebx  ; p->hp = hp       offset 16, 4 bytes
  15:  mov    DWORD PTR [rax+0x14],0x1  ; p->level = 1     offset 20, 4 bytes
  1c:  pop    rbx
  1d:  ret
```

Read it as a type inference: 24-byte allocation; 4-byte fields at 16 and 20;
offsets 0–15 untouched here → 16-byte array or padding. Candidate layout:

```text
offset  0x00 +------------------------+
             | 16 bytes (name[16]?)   |  <- inferred from the untouched gap
       0x10 +------------------------+
             | int hp                 |  <- write at +0x10
       0x14 +------------------------+
             | int level              |  <- write at +0x14
       0x18 +------------------------+  <- total 0x18 = 24 bytes
```

Verify against ground truth: `sizeof(struct Player)` is 24 on this toolchain,
which is why `0x18` appeared in `edi`. The heuristic is defeasible — that gap
could be pure padding — which is the point: it produces hypotheses you *test*,
not facts you trust.

## How to use this knowledge

- **Crash triage in stripped third-party binaries**: decompile the frames on
  the stack and read recovered locals instead of raw asm — the difference
  between minutes and hours.
- **Patch-gap defense**: diff your vendor's update against the previous
  version before reading any advisory; the changed function plus a new bounds
  check *is* the CVE, so you can prioritize mitigation before the public
  write-up lands.
- **Audit your own release builds**: decompile your own `-O2` output — if a
  bounds check you wrote is gone (proved redundant, or UB-assumed), you found
  either reassurance or a latent bug, cheaply.
- **Malware first 30 minutes**: headless decompile plus a scripted
  import/string/indirect-call census, then read chapter 06's dispatcher loops
  as the recognizable shapes they are.
- **Interviews**: "names are unrecoverable because renaming preserves
  semantics; perfect structuring is impossible because irreducible CFGs
  exist" — three sentences that beat any tool-naming answer.

## Connections

- **Module 04 Chapter 03** — compiler idioms (field offsets, loop shapes,
  jump tables) are this chapter's type-guessing evidence; the decompiler
  industrializes those fingerprints.
- **Module 04 Chapter 04** — vtables and RTTI are the strongest anchors on
  optimized C++: they patch part of the indirect-call hole and seed type
  propagation.
- **Module 04 Chapter 06** — anti-analysis targets this pipeline: flattening
  attacks the structurer, MBA expressions the simplifier, virtualization the
  lift itself.
- **Module 01 Chapter 07** — the register and calling-convention model is
  what makes signature guessing possible: argument registers are the
  ABI-encoded parameter list.
- **Module 10 Chapter 01** — compilers emit SSA with phi nodes at joins;
  variable recovery converges on the same idea from the consuming side. One
  IR concept, both directions of the pipeline.

## Quiz

1. A decompiled function recompiles and behaves identically to the original
   binary. A reviewer says "so we recovered the source." What is wrong with
   that claim, in one sentence?
2. A stripped binary's function calls `malloc` with `edi = 0x18`, then writes
   4-byte values at `+0x10` and `+0x14`, never touching `+0x0..+0xf`. Give the
   inferred struct layout, the total size, and one alternative explanation for
   the untouched 16 bytes.
3. The source contains `for (int i = 0; i < n; i++) total += abs(a[i]);` at
   `-O2`. What loop shape does the decompiler emit, what happened to `i`, and
   why can't any decompiler get `i` back?
4. A prologue is `push rbx; sub rsp, 0x48` (x86-64, SysV). How many bytes does
   the decompiler report for the frame, and why might the C source's locals
   sum to less?
5. In a decompilation, `local_18` is a `char *` in the first half of the
   function and an `int` counter after a loop; values appear to "clobber" each
   other across the loop. Diagnose: what did the compiler do, and what would
   confirm it?
6. Your decompilation of an `-O2` build shows an array write with no bounds
   check, and you are about to file "missing bounds check." What must you
   check first, and why?
7. `call qword ptr [rax+0x18]` appears in a stripped C++ binary. Name the two
   consequences for the call graph and type propagation, and what recovers
   part of it.
8. Module 10 Chapter 01: compilers build SSA with phi nodes at joins;
   decompiler variable recovery also merges values at joins. Which side's
   merge is exact, which is heuristic, and why?

<details>
<summary>Answers</summary>

1. **Equivalence is not identity.** Compiling is many-to-one — renaming,
   macros, and inlining choices erase without changing behavior — so the
   decompiler produced *a* program with matching behavior, one of infinitely
   many; the original source is not merely unknown, it is *undeterminable*.
2. A 24-byte struct: 16 untouched bytes at offset 0, 4-byte fields at 0x10 and
   0x14 — matching `char name[16]; int hp; int level` (`sizeof` is 24 on x86-64
   gcc 14.2.0, hence the `0x18`). Alternative: offsets 0–15 could be pure
   alignment padding, or fields written only in other functions; corroboration
   (a later `memcpy` of 16 bytes) is what upgrades the hypothesis.
3. A guard (`if (n <= 0)`) plus a `do-while` over a *pointer* compared against
   an end pointer. `i` became address arithmetic (`lea`, `add rdi,4`, pointer
   `cmp`): the induction variable no longer exists as a value in the dataflow,
   and a decompiler can only name what the dataflow contains — the same
   erasure theorem as names.
4. Frame = 8 (pushed `rbx`) + 0x48 (72) = 80 bytes, up to 72 attributed to
   locals. The source can sum to less: the compiler pads `sub rsp` so `rsp`
   stays 16-byte aligned per the SysV ABI at every call — some of those bytes
   are alignment slack, not variables. Frame size is an upper bound, not a
   count.
5. **Variable merge error.** At `-O1`+ the compiler put two source variables
   with disjoint live ranges in the *same* stack slot, and the decompiler
   faithfully reports one. Confirm by widening `local_18`'s type: the uses
   split into two consistent groups with no cross-dependencies — two
   variables, one slot.
6. An `-O0` (or `-fsanitize=undefined`) build of the same source. At `-O2` a
   provably-redundant or UB-triggered check is *deleted* — the source had it;
   the compiler removed it because the index was provably in range or signed
   overflow made it undefined. The UB finding may be real, but "the check
   never existed" is usually false.
7. The target is data, not code, so no static edge exists. Consequence one:
   the call graph omits this dispatch — "never called" claims are unreliable.
   Consequence two: no argument constraints propagate through the edge, so
   types degrade to guesses. If `[rax+0x18]` is a vtable slot, chapter 04's
   vtable/RTTI recovery reconstructs the candidate set; runtime-built function
   tables (chapter 06 VMs) stay dark.
8. The compiler's merge is exact: it built the phi form from a source it still
   holds. The decompiler's merge is heuristic: it must *guess* which register
   values and stack slots were one source variable, using live ranges and
   offsets as evidence — and the compiler's slot-sharing (answer 5) is exactly
   where the guess fails. Same IR idea, opposite information budget.
</details>
