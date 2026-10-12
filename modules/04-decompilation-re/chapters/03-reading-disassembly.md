# Chapter 03 — Reading disassembly

> **The 30-second version.** A binary is the output of a *known machine* (a compiler,
> at a fixed optimization level) running against a *known contract* (the SysV ABI) —
> so it has reproducible shapes, not noise. Learn the shapes: the `-O0` prologue,
> the `-O2` frameless leaf, the `[rbx+0x18]` field load, the jump table behind its
> `cmp/ja` doorman, the `lea …,[rip+…]` of position-independent code — a
> stripped binary becomes readable text. Method: find `main`, follow the
> argument registers, annotate as you go.

## Idioms, not instructions

A compiler answers the same questions for every function — where locals live,
who saves which registers, how control flow is lowered — so it emits the same
*skeletons*: match one, recover the source shape, read only the leftovers.

Why this must work: the ABI (module 01 chapter 07) fixes register roles
(`rdi…r9` arguments, `rax` return, `rbx/rbp/r12–r15` callee-saved, 16-byte `rsp`
alignment at calls); each level is a fixed policy — `-O0`: every variable in a
stack slot; `-O2`: registers, folding, inlining. Same input + same flags + same
compiler → same output. The shapes are *forced*, not stylistic. Read by loop:
fingerprint → hypothesis → verify → annotate, keeping a file of names
(`sub_1180_probably_sum`, `[rdi+0x10]` = `score`) that compounds.

## First move: find `main`, then follow the registers

A stripped binary has no `main` symbol, but startup always hands it to libc in the
same register: `e_entry` (chapter 01) points at `_start`, whose job is to call
`__libc_start_main(main, argc, argv, …)` — `main` is *argument one*. Verified on
the lab binary (gcc 14.2.0, Debian 13, x86-64):

```asm
    10c4:  lea    rdi,[rip+0xffffffffffffff95]   # 1060 <main>   <- arg 1
    10cb:  call   QWORD PTR [rip+0x2eef]  # 3fc0 <__libc_start_main@GLIBC_2.34>
```

Whatever `lea rdi,[rip−0x6b]` computes (0x10cb − 0x6b = 0x1060) *is* `main` —
objdump resolves it in the comment, symbols or no symbols. Note the call shape:
`call QWORD PTR [rip+…]` is a GOT-indirect call, chapter 02's eager `GLOB_DAT`
import with no PLT stub.

Then follow argument registers forward: at `main`'s top, `edi` = `argc`, `rsi` =
`argv`, and every call site's staged arguments constrain the callee's signature —
that is the whole methodology; the rest is vocabulary.

## Prologues and epilogues: the two fingerprints first

`-O0` builds a textbook frame for every non-leaf, identically:

```asm
; gcc 14.2.0 -O0 — main's opening
    push   rbp                     ; save caller's frame pointer
    mov    rbp,rsp                 ; establish mine
    push   r13                     ; callee-saved — see the call boundary below
    push   rbx
    sub    rsp,0x48                ; claim locals; keep rsp 16-byte aligned
    mov    DWORD PTR [rbp-0x54],edi  ; spill argc — -O0 spills EVERYTHING
```

Epilogue, exact mirror: `add rsp,0x48; pop rbx; pop r13; pop rbp; ret`.
Matched → `-O0`: every local is `[rbp−k]`, arguments 7+ at `[rbp+0x10]` and up
(module 01 chapter 07). `-O2` on the same source abolishes the frame:

```asm
; gcc 14.2.0 -O2 — same main
    sub    rsp,0x8                 ; the WHOLE frame: alignment only
    mov    esi,0x64                ; arg 2 = 100, folded at compile time
    call   1030 <printf@plt>
    add    rsp,0x8
    ret
```

Three fingerprints: no `rbp` at all (frame-pointer elimination, the `-O2`
default); locals never touch memory; `esi` receives a *constant* — two whole
functions were folded away (they became 100 and 3; run the binary). A leaf may
skip the frame and scratch in the red zone; the epilogue is `add rsp,N; ret` —
or bare `ret`.

`leave` (one byte, 0xC9) is `mov rsp,rbp; pop rbp`. gcc 14 x86-64 emits the
explicit `pop rbp` form; `leave` marks 32-bit gcc, older binaries, hand-written
assembly. Either way: *this function used `rbp` as a frame pointer*.

One more entry fingerprint: `endbr64` (`f3 0f 1e fa`), CET's indirect-branch
landing pad — "an indirect jump may land here." Stock Debian gcc 14.2.0 emits none;
`/bin/ls`, built with distro hardening, carries 254. Seeing `endbr64` means the
build passed `-fcf-protection` — and you can *count* them to census a stripped
binary (lab below).

## Structs live in the addressing modes

C's `p->field` compiles to base register + constant displacement, and the
displacement is the field offset — so collecting displacements recovers a struct's
entire layout. gcc 14.2.0 `-O2` on two functions consuming a 48-byte `struct rec`:

```asm
; long fingerprint(struct rec *r) — r arrives in rdi and never leaves it
    movsx  rdx,BYTE PTR [rdi+0x18]      ; signed byte  at offset 0x18
    movsxd rax,DWORD PTR [rdi+0x8]      ; signed dword at offset 0x08
    add    rax,QWORD PTR [rdi]          ; qword        at offset 0x00
; int sum_scores(struct rec *head)
    cvttsd2si edx,QWORD PTR [rdi+0x10]  ; a DOUBLE lives at offset 0x10
```

Each operand adds a constraint: `movsxd` proves a signed 32-bit field;
`cvttsd2si` an 8-byte IEEE double; a qword from 0x28 assigned into the *same
register that walks the list* proves a self-pointer — a linked list. At `-O0` the
same offsets appear wrapped in noise (pointer reloaded from `[rbp−0x8]` per
access); when the pointer must survive a call it lives in a callee-saved register —
the classic textbook form `mov rax,QWORD PTR [rbx+0x18]`: field at offset 0x18 =
24. Accumulate the constraints and you have the header file — gaps included:
nothing ever touching 0x0c–0x0f *is* the padding proof (the double at 0x10 needs
8-byte alignment):

```text
  rdi ──► 0x00 ┌────────────────────────────────────────────────┐
              │ id    8 B  signed     add     rax,QWORD [rdi]    │
         0x08 │ flags 4 B  signed     movsxd  rax,DWORD [rdi+8] │
         0x0c │ PAD   4 B  (inferred; aligns double to 8 B)   │
         0x10 │ score 8 B  double     cvttsd2si edx,QWORD[rdi+16]│
         0x18 │ name 16 B  byte array movzx   al,BYTE  [rax+0x18]│
         0x28 │ next  8 B  self-ptr    mov     rdi,QWORD[rdi+0x28]│─► next node or 0
              └────────────────────────────────────────────────┘
               sizeof(struct rec) = 0x30 = 48 bytes
```


## Loops: from `dec/jnz` to counters that vanish

The legacy idiom — `dec ecx; jnz .loop` — counts down in place, flags set by
`dec`, no separate `cmp`; it marks DOS/Windows-era and hand-written code. gcc 14
prefers `add`/`sub reg,imm`, and the modern shape is more radical: **the counter
vanishes**. gcc 14.2.0 `-O2` on module 01 chapter 07's `scale_sum`:

```asm
scale_sum:
    imul   edi,esi            ; t = x*k — in place, never stored
    test   edx,edx            ; entry guard: n == 0 ?
    jle    .done
    lea    ecx,[rdx+rdi*1]    ; trip count t+n, computed ONCE
.Loop:  lea    eax,[rax+rdi*2+0x1]  ; two iterations' work (unrolled)
    add    edi,0x2            ; i += 2 — i IS the loop
    cmp    edi,ecx            ; vs trip count
    jne    .Loop
```

No frame, no memory; `i` lives in `edi`, compared against a pre-computed trip
count. The fingerprints: a **rotated loop** (condition at the bottom, entry guard
for the zero-trip case), an **induction variable** bumped by `add reg,imm`, a
closing `cmp/jcc`. In list walks the induction variable is the pointer itself —
`mov rdi,[rdi+0x28]; test rdi,rdi; jne`. `-O0` is the opposite: guard re-loaded
from `[rbp−k]` each pass, `jmp` to the condition at entry, `add DWORD PTR
[rbp-0xc],1` as the increment.

## Switch: jump tables vs comparison trees

`switch` has two lowerings, chosen by arithmetic: **dense** labels (a compact range
where the table beats ~10 bytes of compare-and-branch per case) get a jump table.
gcc 14.2.0 `-O2`, six cases, verified:

```asm
tally:
    cmp    edi,0x5                ; the doorman: range check FIRST
    ja     .cold_default          ; ja (unsigned): negatives also fail
    lea    rdx,[rip+0xd8c]        # 2010   ; table base, rip-relative
    mov    edi,edi                ; zero-extend the index
    movsxd rax,DWORD PTR [rdx+rdi*4]  ; entry = table[x] — 4 bytes each
    add    rax,rdx                ; target = table + entry (relative!)
    notrack jmp rax               ; dispatch
```

Four fingerprints, all load-bearing: the **bounds check before dispatch** (`ja`,
not `jg` — a negative `x` must reach `default`, not read out of bounds); the
**table in `.rodata`**, rip-relative; entries as **4-byte signed displacements**,
`target = base + entry` — correct under ASLR with *zero* relocations (8-byte
absolute entries would each need one; chapter 02's load-bias problem dodged by
construction); and `notrack jmp rax`, which exempts the dispatch from CET's
indirect-branch checking — in a CET binary, near-proof of a switch. Decode one
table entry by hand:

```text
   x in [0,5]                                  .rodata at 0x2010
   ----------                                  ----------------------
   0 ── index 0 ──► entry -0xd70 ┐   0x2010: 90 f2 ff ff   (-0xd70)
   1 ── index 1 ──► entry -0xd68 ┐│   0x2014: 98 f2 ff ff   (-0xd68)
   ...                                        (four more entries)
   5 ── index 5 ──► entry -0xd78 ┐│   0x2024: 88 f2 ff ff   (-0xd78)
   x > 5 or x < 0 ──► .cold_default
                                 │
        target = 0x2010 + entry ─┘
        0x2010 - 0xd70 = 0x12a0:  lea eax,[rsi+0x1]   ; case 0: return y+1
        0x2010 - 0xd68 = 0x12a8:  lea eax,[rsi*2]     ; case 1: return y*2
```

**Sparse** labels make the table waste, so you get a comparison tree: `cmp/je/jg`
in binary-search arrangement over the case constants (our `sparse()`: a je/jg
search over 0x3e8 and 0x186a0). Telling them apart: a memory load feeding an
indirect jump = table; a pile of `cmp reg,imm` = tree. One trap:
dense cases with *linear* results (10, 21, 32, …) produce **no dispatch at all** —
gcc closed the form `10+11x` and emitted two `lea`s. Absence of a table is not
absence of a switch.

## Position independence and the call boundary

**PIC/PIE code** contains no absolute code or data address: `lea
rax,[rip+0xe32]` for tables, `lea rdi,[rip+0xfb6]` for string literals, `call QWORD
PTR [rip+…]` or `call printf@plt` for imports (chapter 02's two shapes:
GOT-indirect is `-fno-plt`, the PLT stub lazy binding). An absolute address
(`mov edi,0x402010`) means non-PIE — check `e_type`: `EXEC` vs `DYN`. Debian gcc
defaults to PIE, so expect rip-relative operands — objdump's comments resolve
their targets for free.

Last, the **callee/caller-save boundary** — module 01 chapter 07's payoff, now as
a reading tool. `rbx, rbp, r12–r15` must survive every call; `rax, rcx, rdx, rsi,
rdi, r8–r11` may be destroyed by any call. So *a value live across a call is pushed in the prologue or re-computed after
it — never assumed to survive.* Our `-O0` `main` needed three call results at once, staged them in
`r13, r12, rbx` — and the prologue grew `push r13; push rbx` to match. Read a
push-set as a **liveness map**: `push rbx` before a loop means something precious
crosses the call inside it — spill slots and post-call reloads are the same
decision (register allocation, module 10 chapter 03) seen from other angles.

## What insiders know

- **Prologue/epilogue fingerprints, by level.** `-O0`: `push rbp; mov rbp,rsp;
  [callee-saved pushes]; sub rsp,N` (N sized to keep `rsp` 16-aligned) and its
  mirror `add rsp,N; pop …; pop rbp; ret`; every local at `[rbp−k]`. `-O2`: frame
  pointer gone, frame is `sub rsp,8`-or-less or nothing, leaves may use only the
  128-byte red zone. `leave` (0xC9) = `mov rsp,rbp; pop rbp` in one byte — 32-bit
  gcc and hand-written code; rare in x86-64 gcc output.
- **`endbr64` is a build-flag tell, not a compiler default.** `f3 0f 1e fa` is the
  CET landing pad. Stock Debian gcc 14.2.0 emits none; `/bin/ls` (distro hardening
  via dpkg-buildflags) has 254; force it with `-fcf-protection=full`. And `notrack
  jmp reg` deliberately *exempts* jump-table dispatch from IBT — in a CET binary it
  is close to proof of a switch.
- **Spotting PIC in one glance**: every reference is rip-relative, and objdump's
  trailing comment resolves each target. Any absolute constant address = non-PIE
  (`e_type EXEC`) or handwritten. Under PIE, jump-table entries are 4-byte relative
  displacements — absolute 8-byte entries would cost one runtime relocation each;
  relative ones cost zero.
- **The push-set is a liveness probe.** Only `rbx, rbp, r12–r15` are pushed by
  SysV compiler code (a `push rsi` means a different ABI or unusual code); a reload
  after `call` is the caller-save tax, a `push rbx` the callee-save tax.
- **Jump-table recognition checklist**: (1) unsigned `cmp/ja` range guard, default
  often out-of-lined into a `.cold` section; (2) scaled `.rodata` load
  (`[table+idx*4]` for PIE-relative entries, `[idx*8]` for absolute pointers);
  (3) `jmp reg` / `notrack jmp reg`; (4) decode one entry before trusting the
  table's extent.
- **Alignment nops are function boundaries.** Runs of `nop DWORD PTR [rax+0x0]`,
  `xchg ax,ax`, or `cs nop` after a `ret` are 16-byte padding — in a stripped
  binary they mark function *starts*, which is how you segment `.text` when you
  cannot count `endbr64`.

## See it yourself

Real output, gcc 14.2.0 (Debian) and GNU objdump (binutils 2.44), x86-64, Debian 13.
Write `shapes.c` with the 48-byte `struct rec` (`long id; int flags; double score;
char name[16]; struct rec *next;`), `fingerprint` (every field), `sum_scores` (list
walk), `classify` (dense switch, constant cases), `sparse` (cases 0, 100, 1000,
100000), `tally` (dense switch, distinct code per case), and a `main` calling all.

```console
$ gcc -O0 -o shapes0 shapes.c
$ gcc -O2 -fcf-protection=full -o shapes2 shapes.c     # CET on, to see endbr64
$ objdump -d -M intel --no-show-raw-insn shapes2 | less
```

Map each construct to its two shapes — the table is the exercise; the answers are
this chapter's code blocks:

| C construct | function | -O0 shape | -O2 shape |
|---|---|---|---|
| prologue | any | `push rbp; mov rbp,rsp; push r13…; sub rsp,0x48` | `sub rsp,0x8` or none |
| struct field | `fingerprint` | reload `[rbp-0x8]`, then `movzx …,[rax+0x18]` | `movsx rdx,[rdi+0x18]` |
| loop | `sum_scores` | guard re-checked at `[rbp-0x10]` each pass | `mov rdi,[rdi+0x28]; test; jne` |
| switch (dense) | `tally` | jump table (same lowering!) | `cmp/ja` + relative table + `notrack jmp` |
| switch (sparse) | `sparse` | sequential `cmp/je` | binary-search `jg/je` tree |
| call boundary | `main` | results in `r13/r12/rbx`, epilogue pops | folded constants `esi=0x64, edx=3` |

Both levels lower the dense switch to a jump table (`-O0` re-derives the table
address twice); module 01 chapter 07's `scale_sum` is the same drill, smaller.

**The census, on the stripped binary:**

```console
$ cp shapes2 shapes2.stripped && strip shapes2.stripped
$ nm shapes2 | grep -c ' [Tt] '                 # ground truth
14
$ objdump -T shapes2.stripped | tail -n +3      # what survives strip: imports
0000000000000000  DF *UND*  GLIBC_2.34  __libc_start_main
0000000000000000  DF *UND*  GLIBC_2.2.5 printf
...                                            (4 weak rows)
$ objdump -d shapes2.stripped | grep -c endbr64 # census heuristic
8
```

14 real functions, 8 `endbr64`s — the six misses are direct-only functions and
CRT helpers (`tally.cold`, `_start`, `deregister_tm_clones`, …) that IBT does not
guard. `objdump -T` is the fallback import census: 6 rows, 2 real imports — the
binary's entire external API (chapter 02's `.rela.plt`, other side). Finally,
find `main`: `readelf -h` gives `Entry point address: 0x10b0`; the code there
shows `lea rdi,[rip-0x6b]` at 0x10c4, next instruction 0x10cb, so `main` =
0x10cb − 0x6b = 0x1060 — by ABI arithmetic, from a nameless binary.

## How to use this knowledge

- **Triage crashes in stripped builds.** A core dump with addresses and no
  symbols: anchor via `e_entry` → `_start` → `main`, recover layouts from the
  addressing modes, and the faulting `[rbx+0x18]` becomes "read of `name`
  through a bogus `rec *`" — a real hypothesis.
- **Audit what the compiler did.** Bounds check survived? Getter inlined? Counter
  in a register or spilled? Thirty seconds of `objdump -d -M intel` answers
  without a benchmark — and a jump table proves your `switch` is O(1).
- **Debug optimized builds without despair.** gdb's "value optimized out" is this
  chapter's `-O2` fingerprints: the variable never had a location. Rebuild with
  `-O1 -fno-omit-frame-pointer` to get frames back.

## Connections

- **Module 01 Chapter 07** — the ABI grammar (register roles, `sret`, varargs `al`,
  `[rbp+0x10]` stack args) that every fingerprint presupposes.
- **Module 04 Chapter 01** — `e_entry` anchors the find-`main` recipe; the section
  map says where jump tables live (`.rodata`).
- **Module 04 Chapter 02** — `call QWORD PTR [rip+…]` is the eager `GLOB_DAT`
  import; relative jump-table entries are ASLR dodged by construction; `printf@plt`
  is the lazy stub.
- **Module 04 Chapter 04** — vtable dispatch is the same indirect-call
  fingerprint, with RTTI and mangling on top.
- **Module 04 Chapter 05** — decompilers automate exactly these fingerprints
  (jump-table recovery, signature inference, struct propagation); the manual
  version tells you when to distrust them.
- **Module 02 Chapter 03** — ROP gadget hunting is epilogue farming;
  `endbr64`/`notrack` are the IBT countermeasures, visible in the bytes.
- **Module 10 Chapter 03** — register allocation produced the `-O0`/`-O2` delta:
  spills, push-sets, and vanishing locals are its output log.

## Quiz

1. **Concept.** Why does an `-O2` leaf function often have no stack frame while
   the same function at `-O0` builds a full `rbp` frame — and which single stack
   obligation does even the frameless leaf still incur?
2. **Concept.** PIE jump-table entries are 4-byte signed displacements added to the
   table's address, not 8-byte absolute code addresses. Derive why: what does each
   absolute entry cost at load time, and what does the relative form cost?
3. **Predict the layout.** You recover these accesses (plus a loop that walks
   `[rdi+0x28]` into `rdi`): `movsxd rax,DWORD PTR [rdi+0x8]`,
   `cvttsd2si edx,QWORD PTR [rdi+0x10]`, `movsx rdx,BYTE PTR [rdi+0x18]`,
   `add rax,QWORD PTR [rdi]`. Give each field's offset, size, type; where is the
   padding and why; what is `sizeof`?
4. **Predict the number.** In a stripped PIE binary, `readelf -h` reports
   `e_entry 0x10b0`. At 0x10c4 you find `lea rdi,[rip+0xffffffffffffff95]` (a
   −0x6b displacement); the next instruction is at 0x10cb. What address is `main`,
   and what does the following `call QWORD PTR [rip+0x2eef]` say about the link?
5. **Predict the shape.** A switch has cases 0–5 returning 10, 21, 32, 43, 54,
   65 (an arithmetic progression), at `-O2`. What dispatch structure do you expect
   — jump table, comparison tree, or neither — and what does the body look like?
6. **Bug diagnosis.** An analyst counts functions in a stripped CET binary by
   counting `endbr64`. A crash address resolves to 0x1050 — no `endbr64` there,
   and the region disassembles as `cmp edi,0x5; ja 0x1050; mov eax,esi; ret`.
   What did the census miss, why no landing pad, and what other segmentation
   anchor should they have used?
7. **Bug diagnosis.** A static analyzer anchors function starts on
   `push rbp; mov rbp,rsp` and locals on `[rbp−k]`. It works on a debug build but
   finds 2 of 200 functions in an `-O2` release build, with nonsense
   "struct offsets." Name the two fingerprint changes that broke it, and a flag
   that restores readable frames short of `-O0`.
8. **Cross-chapter.** The `-O0` epilogue `add rsp,0x48; pop rbx; pop r12; pop
   r13; pop rbp; ret` is prized gadget material in module 02 chapter 03. One
   sentence each: what makes it a gadget, and how does CET's indirect-branch
   tracking blunt this usage — while leaving the jump table's `notrack jmp`
   untouched?

<details>
<summary>Answers</summary>

1. A leaf calls nobody: no outgoing stack arguments, no saved state; locals fit
   in registers, with the 128-byte red zone as scratch. The surviving obligation
   is the hardware's: the entering `call` pushed an 8-byte return address that
   `ret` must pop. `-O0` builds the frame anyway — "every variable gets a memory
   address a debugger can find."
2. An absolute 8-byte entry is a pointer the linker cannot know (ASLR picks the
   load base at exec), so each entry costs a relocation record — chapter 02's
   repair bill, one per case. The relative form stores
   `target − table_base`, a link-time constant, added at runtime by the
   `add rax,rdx` you saw: zero relocations, and 4 bytes instead of 8.
3. 0x00: 8 bytes, signed 64-bit (`id`); 0x08: 4 bytes, signed 32-bit
   (`movsxd`); 0x0c: 4 bytes padding — the double at 0x10 needs 8-byte
   alignment; 0x10: 8-byte double; 0x18: a `char` array, 16 bytes (the next
   touched offset is 0x28); 0x28: an 8-byte self-pointer walked by the loop
   (`next`). `sizeof` = 0x30 = 48.
4. `0x10cb − 0x6b = 0x1060` — `main`, argument 1 (`rdi`) to
   `__libc_start_main`. The GOT-indirect call with no PLT stub is an eagerly
   bound `GLOB_DAT` import (chapter 02's `-fno-plt`/`-z now` family): imports
   resolved before `main` ran.
5. Neither: gcc recognizes the results as a linear function of the case value
   and emits the closed form — `lea eax,[rdi+rdi*4]` (5x), `lea
   eax,[rdi+rax*2+0xa]` (11x+10), behind the `cmp edi,0x5; ja` guard. No table,
   no tree, no indirect jump: absence of a table is not absence of a switch.
6. The census missed `.cold` sections and direct-only functions. 0x1050 is
   `tally.cold` — the switch's *default* arm, out-of-lined, reached only by the
   direct `ja`; IBT requires `endbr64` only at indirect-branch targets, so a
   direct-only function legitimately has none (CRT helpers predate the CET
   flag). Better anchors: alignment-nop runs or direct `call` targets — every
   direct call names a function start.
7. (a) `-O2` omits the frame pointer (`-fomit-frame-pointer` default) — the
   `push rbp; mov rbp,rsp` anchor vanishes, frames become `sub rsp,N`-or-nothing.
   (b) Locals stop existing as `[rbp−k]` slots — they live in registers
   ("optimized out"), so offsets must come from argument and pointer registers.
   `-fno-omit-frame-pointer` (with `-O1`) restores frames without `-O0`'s memory
   traffic.
8. It is a gadget because it is a `ret`-terminated sequence popping four
   attacker-controlled stack words into registers — a ROP register-loading
   primitive from an ordinary epilogue. CET/IBT blunts it by requiring `endbr64`
   at indirect-branch destinations, so farmed entries stop being landable (shadow
   stacks police the `ret` itself); `notrack jmp` exempts jump tables — their
   targets are compiler-generated and trusted.

</details>
