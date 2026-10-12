# Chapter 03 — Code reuse: ROP and friends

> **The 30-second version.** NX made the stack and heap non-executable, killing
> the classic "inject shellcode, jump to it" exploit — but not one executable
> instruction from the process. Every `ret` is an indirect jump whose target and
> "program counter" (`rsp`) the attacker controls after a stack overflow. Chaining
> existing fragments — gadgets — turns the whole address space into an instruction
> palette: ret2libc, ROP, SROP, JOP are one insight at different scales. Every
> mitigation since (ASLR, CFI, CET shadow stack) answers this one observation.

## NX broke the payload, not the plan

Chapter 02 ended with control of the saved return address; the 1996 answer was to
fill the buffer with machine code and point the saved RIP into it. Data Execution
Prevention (AMD NX / Intel XD; Windows XP SP2 2004, OpenBSD W^X 2003) marks stack
and heap non-executable at the page-table level — the MMU refuses instruction
fetches there, and injected code faults on its first byte (module 01 ch03).

Re-derive the attacker's options from the instruction set alone. The constraint
is only: *rip may not enter non-executable pages* — everything else (program,
libraries, vdso, JIT regions; a stock glibc 2.41 is 2.0 MB) stays executable.
The problem was never "I need new code"; it was "I need to compute with the code
already mapped." The `ret` instruction is the loophole: it performs
`rip ← [rsp]; rsp += 8` — an indirect jump whose target and
sequencing state are both *data the attacker wrote*. The timeline followed: Solar
Designer's return-into-libc (1997), Shacham's formalization of ROP (2007), and a
counter-count that has run ever since.

The honest statement of NX's effect: it deletes code *injection* and leaves code
*reuse* fully intact — everything in this chapter is code reuse.

## ret2libc: one function, one argument, one trap

The minimal reuse: return into one useful libc function — canonically
`system("sh")`: one call, one argument, a shell.

Three addresses plus the ABI from module 01 ch07. `system`'s first argument
travels in `rdi`: point it at a `"/bin/sh"` string — libc contains one (glibc 2.27:
offset 0x1b3e9a; per-build, find it with `strings -tx libc.so.6 | grep /bin/sh`)
— using a `pop rdi; ret` gadget. The chain on the stack, byte-exact (`S` = the
saved-RIP slot the overflow controls):

```
 higher addresses
|  0x00000000deadbeef            | S+32 : fake return address (never reached)
+--------------------------------+
|  &system      (base + 0x4f4e0) | S+24 : callee
+--------------------------------+
|  &"/bin/sh"   (base + 0x1b3e9a)| S+16 : the word `pop rdi` will consume
+--------------------------------+
|  &pop-rdi-ret (base + 0x2155f) | S+8  : gadget 1
+--------------------------------+
|  &ret         (base + 0x8aa)   | S    : overwritten saved RIP — alignment sled
+--------------------------------+       (offsets: glibc 2.27 — re-derive per target;
                                          the *shape* is universal)
```

Then the trap — the most famous crash in exploitation folklore. The SysV x86-64 ABI
requires `rsp % 16 == 0` at the *call site*, so after `call` pushes the 8-byte return
address, a function entry sees `rsp % 16 == 8`. Compiled code may use 16-byte-aligned
SSE accesses — `movaps` faults on a non-16-aligned address, unlike its tolerant
sibling `movups` — at offsets computed from that assumption. You entered `system`
with `ret`, not `call`, and each `ret` shifted `rsp` by 8: whether `system` starts
at `rsp ≡ 8` or `rsp ≡ 0` is pure chain-length parity. Wrong parity,
and the first `movaps` on the path faults — the exploit dies *after* doing everything
else right.

The arithmetic, once, forever: the callee sees `rsp = S + 8·k`, `k` = number of
`ret`s executed (the victim's plus one per gadget); if `(S + 8·k) % 16 != 8`, it is
misaligned. The fix is one bare `ret` gadget (a "ret-sled" of length 1) — no
register changes, parity flips by exactly 8. The sled at `S` exists so `system`
enters at `rsp = S + 32`, `S ≡ 8 (mod 16)` giving the ABI-correct `rsp ≡ 8`.

Two details worth their weight. The crash *signature*: #GP, not a page fault — Linux
reports SIGSEGV, `si_code = SI_KERNEL` (128), `fault_addr = (nil)` (no faulting
address); "rip inside libc, no fault address" means suspect alignment, not a wild
pointer. And version drift: glibc ≥ 2.34 routes `system` through `posix_spawn`, so
the famous crash site moved — the ABI rule did not (varargs callees still spill
`xmm0–xmm7` with `movaps`; the lab makes one do it). Mark your glibc; re-derive,
don't remember.

## ROP: the stack becomes the program

ret2libc calls one function. Full ROP generalizes it: define a *gadget* as any
instruction sequence that ends in `ret` — including sequences that begin at a byte
offset *inside* another instruction's encoding (`0x5f` is both `pop rdi` and the
ModRM byte of other instructions; enter mid-instruction and the CPU decodes
`pop rdi; ret` from the middle of `mov edi, 0x5f...`). A *chain* is gadget
addresses and data words laid on the stack; each gadget's `ret` loads the next entry
and advances `rsp`. With `pop`/`mov [reg], reg`/arithmetic gadgets you can assemble
arbitrary computation — the stack is your program, `rsp` its program counter, the
"calling convention" whatever your gadgets do.

Walk a minimal execve chain (x86-64 Linux, syscall 59), watching `rsp`:

```
+----------------------------+  rsp during execution:
| [gadget: pop rax ; ret]    |  <- victim's ret:      rip←[S],      rsp=S+8
| [59]                       |  <- pop rax:           rax=59,       rsp=S+16
| [gadget: pop rdi ; ret]    |  <- ret:               rip←[S+16],  rsp=S+24
| [&"/bin/sh"]               |  <- pop rdi:           rdi→sh,      rsp=S+32
| [gadget: pop rsi ; ret]    |  <- ret:               rip←[S+32],  rsp=S+40
| [0]                        |  <- pop rsi:           rsi=0,       rsp=S+48
| [gadget: pop rdx ; ret]    |
| [0]                        |  <- ... same walk ...
| [gadget: syscall]          |  <- final ret:         rip←[S+64],  rsp=S+72
+----------------------------+     the kernel never returns to the chain — execve
                                     replaces the process image
```

Every step is one `ret` or one `pop` — the chain is *data*; the CPU executes only
existing code. ASLR (ch06) forces a libc leak first: Chapter 02's primitives are the
prerequisite, not an alternative.

Hunting gadgets is a solved tooling problem. `ROPgadget --binary libc.so.6` walks
executable segments with capstone, decoding from *every* byte offset and reporting
unique gadget strings — which is why its counts dwarf `objdump | grep -c ret`.
Filters: `--only "pop|ret"` restricts to those instructions; `--depth N`
bounds instructions per gadget (default 10 — raising it floods you with junk like
`adc byte ptr [rcx+0x415d5bd8], cl ; pop rsp ; ret`); `--badbytes "0a00"` excludes
addresses your input channel can't carry. `ropper` is the alternative engine
(`ropper --file libc.so.6 --search "pop rdi" --nocolor`); its ROPchain
auto-generation is right often enough to check, never to trust.

Measured, so you know the scale (ROPgadget v7.7, this machine, glibc 2.41, Debian
13): **102,277 unique gadgets** in the stock 2.0 MB libc — 123 sequences with
`pop rdi` (one clean `pop rdi ; ret`, offset 0x2a145), 486 `pop rsp`, 770
`add rsp`, 43 `leave ; ret`, and one bare `syscall` at 0x28505 (no `syscall ; ret`
pair in this build — chain `pop rax; ret` into it). A 20-line `-no-pie` binary
yields 61. Gadget scarcity has never been a defensive strategy.

## Small overflows: stack pivots

Chapter 02's overflows were generous (200 bytes). Real bugs often give
you 8–32 bytes — room for the saved RIP, not a 9-word `execve` chain. The answer:
pivot `rsp` off the victim stack into a region you filled through another channel
(a second input copied to `.bss`, the environment block, or heap spray — ch04's
grooming).

A pivot is any gadget that loads `rsp` (or `esp`, zeroing the top half — beware)
from a register or memory you control:

```
pop rsp ; ret     rsp ← [rsp]      — the next chain word IS the new stack
leave ; ret       rsp←rbp; rbp←[rsp]  — hijack rbp first, then let the epilogue go
xchg rax, rsp     rsp ↔ rax        — 1 byte (0x94) on 32-bit, 2-3 on 64-bit
mov rsp, rdx ; ...                 — direct load, rarer, equally final
```

The classic opening: a small overflow into a *saved-RBP* slot, then
`[&leave-ret][&fake_stack]` — after `leave`, `rsp` lives in your sprayed buffer and
the chain proceeds normally. "The overflow is too small to matter" is a code-review
error: 16 controlled bytes + any pivot + any sprayed buffer = a full ROP stack.

## SROP: one syscall to set every register

ROP's cost is real: loading six registers for `execve` is six+ gadgets,
each hunted. Signal machinery offers a shortcut, derived from module 01 ch04:
to deliver a signal, the kernel builds an `rt_sigframe` *on the user stack* — a full
image of every register (23 gregs slots = 184 bytes inside a ~248-byte core; glibc's
user-side `ucontext_t` is 968 bytes with fpstate, x86-64 Linux) — jumps to the
handler, and later, on syscall 15 (`rt_sigreturn`) from the restorer, rebuilds *all*
registers from that frame. By design: the only way execution resumes transparently
after a signal.

Read that as an attacker: `rt_sigreturn` is a "load every register, including rip
and rsp, from memory at rsp" instruction. Point `rsp` at a *forged* frame, reach a
`syscall` gadget with `rax = 15`, and the kernel restores your chosen `rip`, `rsp`,
`rdi`, `rsi`, `rdx` — one gadget, no per-register loading. That is SROP (Bosman &
Bos, "Framing Signals", WOOT 2014).

```
 rsp →  forged frame (values you chose), x86-64 Linux:
+-----------------------------------+
| rt_sigreturn path / uc header     |   pretcode, flags, link, stack_t
+-----------------------------------+
| gregs[0..22]  (23 × 8 = 184 B)    |   rax=59, rdi→"/bin/sh", rsi=0, rdx=0,
|                                   |   rip→syscall gadget, rsp→anywhere safe
+-----------------------------------+
| fpregs ptr + reserved             |   (zeros suffice for execve)
+-----------------------------------+
```

"SROP is 2 syscalls" because a shell via SROP is exactly `rt_sigreturn`
(15) restoring a register set that begins the real syscall, `execve` (59) — nothing
before, nothing between: "find N gadgets" collapses to "find 2 syscalls." The
kernel restores userspace state from user memory *by design*, so this stays viable
wherever a `syscall` gadget is reachable; the 6.6+ shadow-stack design added a
restore token to signal frames to complicate forged ones.

## JOP, COP, and the counter-mitigation wall

`ret` is the *backward edge* of control flow; `call`/`jmp` through registers the
*forward edge* — and the same idea works there: JOP (jump-oriented programming;
Checkoway et al. 2010, "Return-Oriented Programming without Returns") chains
gadgets ending in `jmp reg`/`call reg`, driven by a *dispatch* gadget
(`jmp [rax + rdx*8]`-shaped) with the sequence table in memory — control never
touches the stack. COP — call-oriented programming — invokes whole *legitimate
functions* with attacker-chosen arguments through indirect call sites. The variants
matter because defenses differ per edge: a shadow stack polices only `ret`; IBT
polices only indirect branch targets.

The counter-mitigation ledger, priced in full in ch06:

- **CFI (forward edge)**: clang's `-fsanitize=cfi` (2015+; `cfi-vcall` for virtual
  calls) checks every indirect target against a pre-computed set — killing generic
  JOP into mid-function addresses; kCFI (Linux 6.1, 2022) does the same in-kernel.
- **CET / shadow stack (backward edge)**: Intel CET keeps a second, kernel-managed
  stack of return addresses; every `ret` compares its popped target with the shadow
  copy — mismatch is a control-protection fault, and plain ROP dies. Status as of
  2025: `endbr64` (IBT's landing pad) already sits at function entries in CET-built
  binaries (this Debian gcc 14.2 lab build emits none; Ubuntu's gcc does), but
  the *shadow stack* remains opt-in on Linux (kernel ≥ 6.6 + glibc
  tunables; no major distro defaults it on); Windows ships hardware-enforced stack
  protection broadly on supported CPUs.

Why does ROP persist anyway? Gadget abundance: 102,277 gadgets in one stock libc,
and exploits need ~10. Partial deployment: mitigations protect processes that opt
in, on CPUs that support them; fleet-mixed vintage leaves years of overlap.
Edge coverage: the shadow stack kills ret-chains but leaves forward-edge JOP/COP;
CFI kills wild forward edges but leaves returns — until both are on everywhere,
one reuse family remains available. Mitigations raise the price of a chain (more
bugs per exploit, per ch06); they have not yet retired the technique.

## What insiders know

- **The movaps trap's exact shape**: entry `rsp % 16 == 8` (SysV x86-64); each
  `ret` shifts `rsp` by 8, so *chain parity*, not luck, decides whether the
  callee's `movaps` faults. The crash reads as SIGSEGV, `si_code = SI_KERNEL`
  (128), `fault_addr = (nil)` — a #GP, no address. In gdb: `p $rsp & 0xf` before
  your final `ret`; fix with one bare `ret` gadget.
- **Gadget counts are a measured fact, not folklore**: glibc 2.41 (Debian 13,
  2.0 MB) → 102,277 unique gadgets in 2.9 s (ROPgadget v7.7, capstone 5.0.9);
  123 `pop rdi`-bearing sequences, 486 `pop rsp`, 43 `leave ; ret`, 770 `add rsp`,
  one bare `syscall`, zero `syscall ; ret` pairs. Counts are tool/version-dependent
  — state yours.
- **Tool flags that matter**: `ROPgadget --only "pop|ret"`, `--depth N` (default
  10), `--badbytes "0a00"`; `ropper --search` with wildcards. ROPgadget's count
  beats `objdump | grep -c ret` because it decodes from every byte offset — the
  unaligned mid-instruction gadgets invisible in the disassembly you see.
- **Why SROP is "2 syscalls"**: `rt_sigreturn` (15) restores *all* registers from
  a forged ~248-byte frame (23 gregs = 184 B, x86-64 Linux); the restored set
  immediately runs `execve` (59). Prerequisites: one `syscall` gadget, a way to set
  `rax = 15`, stack control — no per-register gadget hunting.
- **CET reality check**: `endbr64` is widespread in CET-built binaries, but the
  shadow stack is opt-in on Linux as of 2025 (kernel 6.6+,
  `GLIBC_TUNABLES=glibc.cpu.hwcaps=SHSTK`; distros off); the 6.6 signal-frame
  restore token exists to blunt SROP. Windows leads in default deployment. A
  ret-chain dies under a shadow stack; JOP/COP forward-edge chains do not.
- **The "too small" overflow is a pivot away from full control**: `pop rsp; ret`
  or a hijacked `rbp` + `leave; ret` migrates `rsp` into any sprayed buffer — a
  16-byte overwrite is a complete ROP precondition, not a mitigated bug.

## See it yourself

Ten minutes; outputs below were captured on this machine (gcc 14.2, glibc 2.41,
ROPgadget v7.7; `pip install ROPgadget` if needed).

```c
/* vuln.c — the Chapter 02 bug, minimally */
#include <stdio.h>
void win(void) { puts("you reached win"); }
void greet(void) {
    char name[64];
    fgets(name, 256, stdin);          /* 256 into 64 */
    printf("hello %s\n", name);
}
int main(void) { greet(); return 0; }
```

```console
$ gcc -no-pie -fno-stack-protector -g vuln.c -o vuln
vuln.c:7:5: warning: 'fgets' writing 256 bytes into a region of size 64
                   overflows the destination [-Wstringop-overflow=]
$ ROPgadget --binary vuln | tail -2
0x000000000040100b : test rax, rax ; je 0x401012 ; call rax

Unique gadgets found: 61
$ ROPgadget --binary vuln --only "ret|pop" | tail -1
Unique gadgets found: 3
$ objdump -d vuln | rg -c "\bret\b"
10
```

Notice: 61 gadgets from 10 `ret` instructions — ROPgadget's unaligned decodes —
and no `pop rdi` in the tiny binary: argument gadgets come from libc. The frame:
`name` at `rbp-0x40` (64 B) + saved `rbp` (8 B) = **72 bytes** to the saved RIP.
Prove control:

```console
$ nm vuln | rg " win"                       # 0000000000401146 T win
$ python3 -c "import sys; sys.stdout.buffer.write(b'A'*72 + (0x401146).to_bytes(8,'little'))" \
    | stdbuf -o0 ./vuln
hello AAAA...AAAF@
you reached win
Segmentation fault
```

The crash *after* `win` is the lesson: `win`'s `ret` pops the zeros the chain left
behind — a chain must terminate somewhere safe (`exit`, or a `jmp self` gadget),
not just reach its goal. Now the alignment trap, self-built: a two-gadget chain
into `pick`, a varargs function whose prologue spills `xmm0–xmm7` with `movaps` —
the glibc mechanism, by your own hand:

```c
/* chain.c — core (add a SIGSEGV handler printing ucontext rip to see the fault) */
__attribute__((noinline)) void retg(void) { }          /* a bare `ret` gadget */
__attribute__((noinline)) double pick(int idx, ...) {  /* varargs: movaps prologue */
    va_list ap; va_start(ap, idx);
    double v = 0.0;
    for (int i = 0; i <= idx; i++) v = va_arg(ap, double);
    va_end(ap); return v;
}
int main(int argc, char **argv) {    /* inline asm: 16-align rsp, lay [pick] or
                                       [retg][pick] + a fake return address,
                                       set al != 0, `ret` into the chain */
```

```console
$ gcc -O2 -no-pie chain.c -o chain
$ ./chain
SIGSEGV: rip=0x401238 fault_addr=(nil) si_code=128      <- #GP, not a page fault
$ objdump -d chain --no-show-raw-insn | rg 401238
  401238: movaps %xmm0,-0x58(%rsp)                      <- the faulting instruction
$ ./chain fix                                            <- argv[1]: insert [retg] first
back in main
```

`[pick]` enters at `rsp % 16 == 0` and dies on the first `movaps`; prepending one
bare `ret` flips the parity and it survives — the ret2libc folklore with two
gadgets you wrote. The real thing under gdb — the classic `system("/bin/sh")` on
glibc 2.27 / Ubuntu 18.04 (offsets illustrative; re-derive per target):

```console
(gdb) b *0x40118e                  # greet's ret (find yours: disas greet)
(gdb) run < payload_bad            # [pop-rdi-ret][&sh][system]: wrong parity
(gdb) x/3gx $rsp                   # your chain, about to fire
(gdb) p $rsp & 0xf                 # $1 = 0x0 -> system will enter at rsp%16==0
(gdb) c
Program received signal SIGSEGV, Segmentation fault.
0x00007ffff7a522c1 in do_system () from libc.so.6
(gdb) x/i $pc
   => movaps XMMWORD PTR [rsp+0x50],xmm0     # the alignment crash, in the flesh
# prepend one bare `ret` gadget (payload_good) and rerun:
(gdb) run < payload_good
$ id
uid=1000(...)                      # a shell: parity 8, movaps aligned
```

On glibc ≥ 2.34 the crash site moved (`system` → `posix_spawn`), the rule did not —
`chain.c` demonstrates the mechanism on any vintage.

## How to use this knowledge

- **Crash triage**: SIGSEGV inside libc with `fault_addr = (nil)`,
  `si_code = SI_KERNEL` is a misaligned-SSE signature, not a wild pointer — check
  `$rsp & 0xf` before hunting memory corruption that isn't there.
- **Code review pricing**: an overflow is not "small" if any pivot gadget is
  reachable and any buffer you fill is large — 16 controlled bytes is a full ROP
  precondition. Fix the bug class, not the byte count.
- **Mitigation literacy**: a CVE write-up's steps now name their rungs — leak
  (beats ASLR), ret-sled (alignment), pivot (small overflow), SROP/JOP (gadget
  scarcity / shadow stack) — so you can argue what a proposed mitigation would
  and would not stop.
- **The interview answer that derives itself**: "NX doesn't stop exploitation
  because `ret` is an indirect jump whose target and sequencing state are attacker
  data; ASLR makes addresses probabilistic, not privileged; CET's shadow stack
  finally polices the backward edge — leaving the forward edge, and every
  un-migrated process, to CFI."
- **Defensive design**: own a JIT, a plugin ABI, or any runtime emitting indirect
  branches and you now know which edge your checks must cover — and why "we're
  NX-compiled" was never the whole story.

## Connections

- **Module 01 ch04 (the stack)** — every chain is that chapter's frame diagram
  weaponized: `call`/`ret` as stack machinery; SROP, the kernel's own signal
  frames turned around.
- **Module 01 ch07 (assembly)** — the ABI's 16-byte alignment, `endbr64` at
  entries, and reading disassembly are the grammar gadget hunting is written in.
- **Module 02 ch02 (stack exploitation)** — supplied the saved-RIP control and
  libc leak every chain here begins with; canary bypasses were the toll.
- **Module 02 ch06 (the mitigation ladder)** — prices ASLR, CFI, CET, PAC, MTE
  against exactly the techniques of this chapter, with bypass ledgers.
- **Module 04 ch03 (reading disassembly)** — gadget hunting *is* compiler-idiom
  reading; that chapter industrializes the eye you used on `greet`.

## Quiz

1. **Concept.** NX marks the stack and heap non-executable. Derive, from the
   semantics of `ret` alone, why this stops zero percent of saved-RIP control.
2. **Concept.** Define a gadget. Why does ROPgadget report 61 gadgets for a
   binary whose disassembly contains only 10 `ret` instructions?
3. **Predict the layout.** In `vuln.c`, `name` sits at `rbp-0x40` and the saved RIP
   at `rbp+8`: how many filler bytes reach the saved RIP? With the chain
   `[pop rdi;ret][&sh][system]` starting at saved-RIP slot `S`, `S % 16 == 8`: what
   is `rsp % 16` when `system` begins, and what does that mean if its path uses
   `movaps`?
4. **Predict the behavior.** Same chain, but now `[ret][pop rdi;ret][&sh][system]`.
   When `system` starts, what value does `rdi` hold, what does `[rsp]` contain,
   and what is `rsp % 16`?
5. **Bug diagnosis.** An exploit crashes mid-libc: SIGSEGV, `si_code = SI_KERNEL`
   (128), `fault_addr = (nil)`, `rip` pointing at a `movaps`. What happened, and
   what one-gadget change fixes it?
6. **Bug diagnosis.** The ret2win payload above prints `you reached win` and then
   segfaults. Why, in terms of the chain? How should a real chain terminate?
7. **Cross-chapter.** SROP works because the kernel builds a register image on the
   user stack when delivering a signal (module 01 ch04's machinery, used by the
   kernel). Why is a shell via SROP exactly 2 syscalls, and what three things must
   the attacker control?
8. **Bug diagnosis.** A ret-chain `[pop rdi;ret][&sh][system]` is launched
   against a process running with Intel CET's shadow stack enabled (and IBT).
   Where exactly does it fail, and which code-reuse family from this chapter
   remains available?

<details>
<summary>Answers</summary>

1. `ret` executes `rip ← [rsp]; rsp += 8`. The saved RIP is attacker data, so `rip`
   becomes an attacker-chosen address *inside already-executable pages*, and `rsp`
   a pointer into attacker data the next `ret` consumes. `ret` fetches an
   *address* from the stack, not an instruction — saved-RIP control is an indirect
   jump into the existing, deliberately-executable code base.
2. A gadget is any instruction sequence ending in `ret`, entered at *any byte
   offset* — including mid-instruction bytes (`0x5f` inside another encoding still
   decodes as `pop rdi`). ROPgadget disassembles from every offset, so one real
   `ret` can terminate many distinct gadgets; mid-instruction entries multiply
   the count far past the 10 visible `ret`s.
3. 64 + 8 = **72** filler bytes. The victim's `ret` pops the `pop rdi;ret` at `S`
   → `rsp = S+8`; `pop rdi` consumes `&sh` at `S+8` → `rsp = S+16`; the gadget's
   `ret` pops `system` at `S+16` → entry `rsp = S+24 ≡ 0 (mod 16)`. The ABI
   expects `rsp ≡ 8` — misaligned, so the callee's first `movaps` faults and the
   exploit dies.
4. `rdi` = `"/bin/sh"`'s address (`pop rdi` consumed `&sh`). `[rsp]`
   = the fake return address following `system`'s slot — the chain word at `S+32`;
   if `system` ever returns, that is where it returns to. `rsp = S+32 ≡ 8
   (mod 16)` — the prepended `ret` added 8 bytes, flipping parity to the
   ABI-correct value; the `movaps` path survives.
5. The callee's entry `rsp` was misaligned by 8, so its SSE code executed `movaps`
   on a non-16-aligned address — a general-protection fault (#GP), which Linux
   reports as SIGSEGV with `si_code = SI_KERNEL` and *no* fault address. Fix:
   prepend one bare `ret` gadget (each `ret` shifts entry `rsp` by 8).
6. `win`'s `ret` popped the next stack word — the zeros left past the overwritten
   slot — into `rip` and jumped to ~0: the chain ran off its end. Terminate
   deliberately: return into `exit`, end with a `jmp $` (harmless loop), or — like
   the execve chain — never return at all, because the syscall replaces the process
   image.
7. Syscall 1 is `rt_sigreturn` (15): with `rsp` at a forged frame it restores
   *every* register from user memory — `rip`, `rsp`, `rdi/rsi/rdx`, `rax` — and
   the restored state begins syscall 2, `execve("/bin/sh", 0, 0)` (59). The whole
   shell is sigreturn + execve. Needs: (1) a `syscall` gadget, (2) `rax = 15`
   (e.g. `pop rax; ret`), (3) stack contents for the ~248-byte frame.
8. It fails at the victim's own `ret` — the first one: the shadow stack holds the
   *real* return address the original `call` pushed; the popped value is the
   attacker's gadget; the mismatch raises a control-protection fault (IBT is
   never reached — it polices indirect branch *targets*, and a `ret` isn't one).
   What survives: forward-edge reuse — JOP (`jmp/call reg` chains driven by a
   dispatch table) and COP through legitimate indirect-call sites — since the
   shadow stack checks only returns; covering those needs CFI (ch06).

</details>
