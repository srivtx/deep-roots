# Chapter 07 — Assembly: the reference model

> **The 30-second version.** Every memory model in this module — from C's naked
> pointers to V8's hidden classes — compiles down to the same thing: instructions
> moving bytes between registers and stack slots. If you can read that final layer,
> no language can hide memory from you, and a whole profession unlocks: reading
> crashes, verifying what the compiler did, and eventually decompilation (Module 04).
> This chapter is the minimal complete grammar: registers, the call sequence, a real
> frame, and the conventions that make binaries *legible*.

## The register file: your only workspace

x86-64 gives the compiler 16 general registers. Learn them by *role*, not name —

```
rsp    stack pointer — the Chapter 04 machine, in hardware
rbp    frame pointer (when used) — the stable origin for locals
rdi rsi rdx rcx r8 r9     argument registers (SysV: args 1-6)
rax     return value; rdx:rdx pair for 16-byte returns
rbx r12 r13 r14 r15 rbp   callee-saved — a function must give these back unchanged
```

ARM64 (the phone world — and increasingly the server world) is *cleaner*: `x0-x7`
arguments, `x0` return, `x29` frame pointer, `x30` (LR) the return-address register,
`sp` with a hard rule that it stays 16-byte aligned. Notice what ARM64 *lacks*: no
red zone, and pushes/pops move two registers at a time (`stp x29, x30, [sp, -16]!`
— the entire prologue in one instruction).

The deep point: registers are Chapter 02's tower top — the only storage with zero
memory latency. Compilers are register allocators first and instruction schedulers
second; "spilling" (a local living in `[rbp-8]` instead of a register) is the
fallback, not the plan. The number of registers is thus a language-design fact:
ARM64's 31 registers are why its calling convention passes more arguments in
registers, which is why deep call chains spill less, which is... the whole tower
participating in the ABI.

## One call, end to end

```c
int add3(int a, int b, int c) { return a + b + c; }
int caller(void)              { return add3(10, 20, 30); }
```

Compiled x86-64 (gcc, -O1, SysV):

```asm
add3:
        lea     eax, [rdi+rsi]      ; a + b            — one instruction, no memory
        add     eax, edx            ; + c → eax (return reg)
        ret                         ; rip ← [rsp], rsp += 8

caller:
        mov     edi, 10             ; arg1
        mov     esi, 20             ; arg2
        mov     edx, 30             ; arg3
        call    add3                ; push return addr; rip ← add3
        ret                         ; result already in eax
```

Read `call` and `ret` as pure Chapter 04 machinery: `call` pushes the return
address and jumps; `ret` pops it into the instruction pointer. The stack's *only*
obligatory role in every ABI is carrying that one word per call. A leaf function
(one that calls nothing) needs *zero* stack — no frame, no prologue: `add3` above
never touched `rsp` beyond the hardware's implicit return-address push. This is why
leaf-heavy code (math kernels, lexers) runs at astonishing stack efficiency, and
why the red zone (Chapter 04) exists — leaf scratch *below* rsp, free.

## A real frame, with locals and a spill

```c
int scale_sum(int x, int k, int n) {
    int t = x * k;
    int acc = 0;
    for (int i = 0; i < n; i++) acc += t + i;
    return acc;
}
```

```asm
scale_sum:
        push    rbp                     ; ── prologue ──
        mov     rbp, rsp
        ; (at -O0) sub rsp, 16          ; claim locals: t=[rbp-4], acc=[rbp-8], i=[rbp-12]
        mov     eax, edi                ; x
        imul    eax, esi                ; t = x*k
        mov     DWORD PTR [rbp-4], eax  ; "t" = a named stack slot
        mov     DWORD PTR [rbp-8], 0    ; acc = 0
        mov     DWORD PTR [rbp-12], 0   ; i = 0
.loop:  mov     eax, [rbp-12]           ; load i
        cmp     eax, edx                ; i < n ?
        jge     .done
        mov     eax, [rbp-4]            ; load t
        add     eax, [rbp-12]           ; t + i
        add     DWORD PTR [rbp-8], eax  ; acc +=
        add     DWORD PTR [rbp-12], 1   ; i++
        jmp     .loop
.done:  mov     eax, [rbp-8]            ; return acc
        leave                           ; ── epilogue: rsp=rbp; pop rbp
        ret
```

Everything Chapter 04 promised is on screen: one `sub` claims all locals; every
variable is `[rbp − k]`; `leave` releases the frame in one stroke. Now compile the
*same function* with `-O2` and watch the locals disappear into registers (i in a
register, acc in eax, the frame gone entirely — `ret` from a stackless leaf). That
gap *is* the optimizer, and `-O0` "debuggability" is literally "please store
everything in memory so debuggers can find it."

## Where the ABI's corners show

- **Structs too big for rax**: return a 40-byte struct and the caller passes a
  *hidden first argument* — a pointer to caller-reserved space (x86-64 SysV: RDI;
  ARM64: x8). The callee writes the result there and returns the pointer; the
  convention is called **sret**. You see it in disassembly as the callee treating
  `rdi` as an output pointer it never read arguments from.
- **More than 6 arguments**: args 7+ travel on the *caller's* stack — the caller
  writes `[rsp+0], [rsp+8]...` *before* `call`; the callee addresses them as
  `[rbp+16]` (past saved rbp and the return address). The frame diagram in
  Chapter 04 is the map of exactly this.
- **Varargs (`printf`)**: the caller sets `al` to the number of vector registers
  used (so the callee can save the xmm state cheaply). Reading a `printf` call site
  and seeing `mov al, 2` is this corner — the ABI refusing to make the common case
  pay for the rare one.
- **The canary, in the flesh** (Chapter 21's full story, here as layout): a
  function with a local buffer gets `mov rax, QWORD PTR fs:0x28` in the prologue,
  the value stored just above the saved registers, and a check + `__stack_chk_fail`
  in the epilogue. `fs` is the *thread-local storage base* — itself a Chapter 03
  page-table/MMU story (the kernel maps a per-thread TLS region and sets the
  segment base). The "stack canary" is thus *three chapters in four instructions*.

## Reading binaries like text

The professional loop, which Module 04 turns into a full discipline:

```console
$ objdump -d --no-show-raw-insn /bin/true | head -40    # any binary reads like this
$ gcc -O2 -c api.c && objdump -d api.o | less            # your own code, check it
   ; questions you can now answer:
   ;  - did it inline my call?        (no `call` — the body is right there)
   ;  - did it vectorize my loop?     (ymm/zmm registers, vmovdqu)
   ;  - where's my bounds check?      (cmp + jae before the indexed load)
   ;  - is this a leaf?               (no frame, no push chain)
$ gdb ./a.out
   (gdb) disas scale_sum        # same view, but with symbols
   (gdb) info registers rdi rsi rdx
   (gdb) x/8gx $rsp             # the frame as raw words — Chapter 04's diagram, live
```

Symbols are the reason source-level debugging works at all: `-g` embeds the
compiler's own map (which slot is `t`, where the prologue ends). Strip it and you
are doing Module 04's job — recovering names from conventions (prologues are
signatures; `fs:0x28` means "has a canary"; `endbr64` means CET is enabled —
Chapter 21's hardware mitigations, visible as one instruction).

## What insiders know

- **`-O0` is a *mode*, not a baseline**: it deliberately stores every variable to
  memory (that's the "optimized out" debugger pain — Chapter 04) and disables the
  inlining/vectorizing that shapes production behavior. Never performance-test
  `-O0`; never debug `Release -O2` without the ABI grammar of this chapter.
- **Tail calls read as `jmp`**: a `call` followed immediately by nothing vs a
  `jmp` into another function's entry = constant-stack recursion (Chapter 04/18).
  Scheme compilers emit nothing *but* this shape for loops.
- **`endbr64`** at function entries is Intel CET's shadow-stack landing pad —
  Chapter 21's ROP mitigation, visible at the top of nearly every modern
  disassembly.
- **PLT/GOT**: calls to shared libraries bounce through a stub (PLT) and a
  patchable function-pointer table (GOT) — the memory *and* the security story
  (RELRO hardens exactly this table — Chapter 21) of dynamic linking.
- **Calling convention as fingerprint**: decompilers infer function signatures from
  register usage (who reads rdi first = takes arg1); exploit writers find `sret`
  and varargs sites the same way. The ABI is the interface between this chapter
  and Module 04.
- **`lea` is free arithmetic**: `lea rax, [rbp-24]` is "address-of" — the compiler
  also uses it as a 3-operand add machine. When you see `lea` with no memory
  access intent, that's not a pointer, it's fused arithmetic — a hint you're
  reading optimized code.

## See it yourself

The complete lab is ten minutes:

```console
$ cat > tiny.c <<'EOF'
int scale(int x, int k) { return x * k + 1; }
int framey(int a, int b) { int t[16]; t[0]=a; t[1]=b; return t[0]+t[1]; }
EOF
$ gcc -O0 -S tiny.c -o - | sed -n '/scale:/,/ret/p'    # the frame, fully built
$ gcc -O2 -S tiny.c -o - | sed -n '/scale:/,/ret/p'    # the frame, gone
```

Then in gdb: `break framey`, `run`, `disas`, `info frame`, `x/4gx $rsp` — and match
what you see against Chapter 04's frame diagram, slot by slot. Do this once and
you have permanently de-mystified "what the compiler did to my code."

## How to use this knowledge

- **Verify, don't believe**: does the compiler inline your hot getter? Did the
  bounds check survive? Is the struct passed by value (copied per call) or by
  pointer? The disassembly answers in 30 seconds, without benchmarks.
- **Crash triage fluency**: any SIGSEGV report is now readable — `rip` pointing
  into unmapped space, `rsp` near the guard page (Chapter 04), `fs:0x28` aborts
  (canary hit — Chapter 21, you are being *attacked or corrupted*).
- **Performance work**: the inner-loop grammar (registers vs spills, vector width,
  unrolling) is the only ground truth about hot code; profilers approximate, this
  is the code.
- **The on-ramp to reversing**: Module 04's decompilation discipline is this
  chapter scaled up with tools (Ghidra/IDA) — the ABI, prologues, and calling
  conventions you just learned are exactly what those tools automate.

## Connections

- **Chapter 02** — registers as the tower's top; spills as tower trips.
- **Chapter 04** — every `push`/`sub`/`leave`/`ret` here is that chapter's diagram.
- **Chapter 03** — `fs` base, guard-page faults, vdso: the virtual-memory features
  the assembly names.
- **Chapter 08** — UB observed at machine level (the deleted overflow check).
- **Chapter 09** — vtables in the flesh; `sret` as the return-of-big-object rule.
- **Chapter 21** — canaries (`fs:0x28`), ROP (every `ret` is a gadget), `endbr64`,
  GOT/RELRO.
- **Module 04 (queued)** — decompilation = this chapter, industrialized.

## Quiz

1. **Concept.** Why does a leaf function need no stack frame at all? Which
   instruction still implicitly uses the stack?
2. **Concept.** What is `sret`, and what problem does it solve for 40-byte return
   values?
3. **Predict the output.** In the `scale_sum` disassembly above, where is the
   variable `t` at `-O0`, and what is it at `-O2`?
4. **Predict the behavior.** A function's prologue contains `mov rax,
   QWORD PTR fs:0x28` and its epilogue `xor`s something before `leave`. What
   language-level feature is being protected, and what is at `fs:0x28`?
5. **Predict the number.** On x86-64 SysV, `int f(struct Big b)` where Big is 48
   bytes: how is `b` passed? Now `int g(struct Sml s)` where Sml is 16 bytes
   (two ints)?
6. **Bug diagnosis.** A crash shows `rip = 0x4141414141414141`. What happened, in
   terms of this chapter's instructions, and which Chapter 04 diagram did the
   attacker overwrite?
7. **Bug diagnosis.** Recursion that "should" run in constant stack (a `return
   g(...)` tail call) blows the stack anyway. What did the compiler emit instead
   of `jmp`, and what would you check in the disassembly?
8. **Cross-chapter.** Why can ARM64's `stp x29, x30, [sp,#-16]!` be the whole
   prologue while x86-64 needs multiple instructions for the same frame — and
   which chapter's property of the ISA (register count, push/pop design) explains
   it?

<details>
<summary>Answers</summary>

1. A leaf calls nobody, so it never needs to save a return address for anyone else
   or pass outgoing stack arguments; its locals can live in registers (or the red
   zone). The `call` that *invoked* it still pushed its return address — `ret`
   always pops one word of stack. Stack usage: exactly 8 bytes, imposed by the
   caller.
2. The System V return-value convention handles at most two registers (rax:rdx, 16
   bytes). For anything bigger, the caller *pre-allocates* space and passes its
   address as a hidden first argument (rdi / x8); the callee writes the struct there
   and returns the pointer. It solves "return by value" without copying through a
   temp the callee can't address — the caller owns the destination, so the copy
   happens directly into the caller's frame.
3. At `-O0`: `[rbp-4]` — a named stack slot (memory). At `-O2`: a register (eax or
   wherever the allocator puts it), possibly never existing as a location at all —
   computed and consumed in flight. Same source, different *existence*: this is the
   "optimized out" debugger phenomenon.
4. The stack canary: the function has a local array (likely) whose overflow toward
   the saved return address must be detected. `fs:0x28` is the per-thread canary
   value in thread-local storage (the `fs` segment base — a virtual-memory/thread
   feature from Chapter 03); the epilogue re-reads and compares, and a mismatch
   jumps to `__stack_chk_fail` (abort). Chapter 21: the canary's low byte is 0x00.
5. `Big` (48B): too large for registers → the caller copies it to its *outgoing
   stack area* and passes a pointer (by-value semantics, memory mechanism).
   `Sml` (16B): fits the two-register rule → passed in rdi:rsi (each 8-byte half
   in a register) — no memory touch at all.
6. `0x41` is ASCII 'A': a buffer of user input overwrote the saved return address
   (the `[rbp+8]` slot from Chapter 04's frame diagram) with attacker bytes; `ret`
   popped it into `rip` and the CPU fetched an instruction from 0x4141... —
   unmapped, so the *fetch* faults. The classic stack-smashing signature, and the
   moment the canary from Q4 was supposed to catch it.
7. It emitted `call` (pushing return addresses: linear stack growth) — or added
   work after the call preventing tail-call optimization. Check the disassembly for
   `jmp` at the tail vs `call`, and for instructions that still read locals after
   the recursive call (the optimizer cannot tail-call if the frame must stay
   alive). Stack-use analysis: Chapter 04's quota arithmetic on the emitted shape.
8. ARM64's paired `stp` with writeback stores *two registers* (frame pointer and
   return address) and adjusts `sp` in one instruction — the ISA was designed
   (RISC, 31 registers, load/store architecture) so frames are one or two
   instructions; x86-64 inherits one-push-at-a-time semantics and CISC history,
   needing `push rbp; mov rbp,rsp; sub rsp,N` (three). ISA register/push design
   (Chapter 02's register-file economics) is what makes the frame cost visible at
   instruction granularity.

</details>
