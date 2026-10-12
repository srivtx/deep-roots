# Chapter 04 — The stack: the shape of call lifetimes

> **The 30-second version.** Function calls nest and return in reverse order — last
> called, first returned. That is exactly LIFO, so a machine region that grows on call
> and shrinks on return is the *perfect* representation of the call graph's lifetime
> structure. That region is the stack, and "allocation" on it is one instruction:
> moving the stack pointer. This chapter builds the frame from the call sequence
> upward, so that Chapter 07's assembly, Chapter 08's C bugs, and Chapter 21's stack
> smashing all read as the same diagram.

## Why the stack exists (derive it, don't memorize it)

Suppose you must design storage for local variables. Locals have a birth (function
entry), a death (function return), and a shape: while `f` calls `g` calls `h`, `h`'s
locals must die before `g`'s, which die before `f`'s. Lifetimes *nest*. Two properties
follow. First, the youngest lifetime is always the next to die — a last-in-first-out
region with a single moving pointer tracks everything with **zero bookkeeping**. No
per-object header, no free list, no GC metadata — the pointer's position *is* the
allocation state. Second, the size of every function's locals is known at compile
time, so the compiler can pre-compute each frame's size and emit entry code that
claims space in one instruction.

Contrast with the heap (Chapter 05), which exists precisely because real programs also
contain lifetimes that do *not* nest — objects that outlive the function that created
them, and sizes unknown until runtime. Stack = the nested-lifetime machine; heap =
the everything-else machine; every language in Part II is a different treaty for
dividing labor between them.

## The frame: what one function call must store

A function needs, at minimum: the **return address** (where to resume in the caller),
a home for its **locals and spilled values**, saved **callee-saved registers** (below),
and **outgoing arguments** that don't fit in registers. Together these bytes are the
*stack frame*. On x86-64 Linux (System V ABI), a frame looks like:

```
        higher addresses
+--------------------------------+  ← caller's outgoing args (7th+ argument)
|  arg7, arg8 ...                |
+--------------------------------+  ← return address (pushed by CALL)
|  saved rbp  (caller's FP)      |  ← rbp points here after prologue
+--------------------------------+
|  callee-saved regs (rbx, r12..)|
+--------------------------------+
|  locals, spill slots           |   the frame proper — sub rsp, N claims
|  ...                           |   all of this in ONE instruction
|  [128-byte red zone below rsp] |   leaf-function scratch (x86-64 only)
+--------------------------------+  ← rsp (stack pointer) — always moves
        lower addresses
```

The **prologue** and **epilogue** bracket every non-trivial function:

```asm
; int scale(int x, int k) { int t = x * k; return t + 1; }
push rbp              ; save caller's frame pointer
mov  rbp, rsp         ; establish my frame pointer
sub  rsp, 16          ; CLAIM 16 bytes for locals — this IS stack allocation
mov  eax, edi         ; x
imul eax, esi         ; x * k   (k was passed in esi)
add  eax, 1           ; + 1
mov  DWORD PTR [rbp-4], eax   ; spill t to its slot: "t" == [rbp-4]
leave                ; mov rsp, rbp; pop rbp — DEALLOCATION, one instruction
ret                  ; pop return address into rip
```

Read the epilogue carefully: `leave` moves `rsp` *past* every local in one shot. Stack
deallocation is not per-object — it is *per-frame*, batched, free. This is the whole
magic of "local variables are automatic": allocation was `sub rsp, N`; freeing is
`leave`. The only costs left are initialization and the push/pop traffic of the
call sequence itself.

## Calling conventions: the contract between callers

Who passes arguments where? Who saves which registers? The answers are the **ABI**
(application binary interface) — without a shared convention, your object files
cannot call mine. System V x86-64: the first six integer/pointer arguments travel in
registers `rdi, rsi, rdx, rcx, r8, r9`; the rest go on the stack; return value in
`rax` (small structs: `rax:rdx`; bigger: the caller passes a hidden pointer —
Chapter 07's `sret`). Floating point uses `xmm0-7`. ARM64's convention: `x0-x7` for
args, `x0` return, `x30` (LR) holds the return address, and `x29` is the frame
pointer.

The register split that matters for performance intuition: **caller-saved vs
callee-saved**. Callee-saved registers (`rbx, rbp, r12-r15` on x86-64; `x19-x28` on
ARM64) may be used by a function only if it saves and restores them — the callee
*owns* their continuity. Caller-saved (everything else) may be obliterated by any
call. Why the split? Leaf functions (which call nobody) can use caller-saved
registers freely and save nothing; functions that call a lot push the burden onto
the callers' register allocator instead. One rule, two savings, chosen by graph
coloring economics.

## The frame pointer: the price of being lost

`rbp`/`x29` points at a fixed spot in the frame. With it, a debugger can walk frames:
each saved-old-rbp at [rbp] links to the parent frame; the return address sits at
[rbp+8] — a linked list through the stack, built by the prologue, giving backtraces
for free. Compilers can omit frame pointers (`-fomit-frame-pointer`, saving one
register and two instructions) and find frames by *computed* offsets instead —
slightly faster, dramatically harder to unwind. The industry swung to omit for years,
then profiled itself into regret: modern distros and browsers are re-enabling frame
pointers because stack sampling (and postmortem crash triage) is worth the 1% cost.
On ARM64 the frame record chain is effectively mandatory culture — `x29` linked list
always there.

## Growth, limits, and overflow

The stack lives at the top of the address space (Chapter 03's map) and grows
*downward* — toward the heap growing upward, maximizing the runway between them.
Default limits: 8 MB on Linux (`ulimit -s`; JVM threads default 512 KB-1 MB,
pthread-created threads can be anything; goroutines start at single-digit KB —
Chapter 11). The kernel maps a **guard page** below the stack (a virtual-memory trap
— Chapter 03): run into it and you die with SIGSEGV, the classic "stack overflow"
from unbounded recursion.

The subtle security chapter-hook: deep recursion does not hit the guard page politely
in huge steps — a frame that allocates 100 KB can *jump past* the guard page into
unrelated mappings and keep writing. The fix is **stack probing** (`-fstack-clash-
protection`): the prologue touches each page as it descends, guaranteeing the guard
page gets its chance. Note also the **red zone** (the 128 bytes below `rsp` that leaf
functions may use without moving `rsp`): signal handlers and interrupts that land
between instructions can clobber it, which is why kernel code compiles with
`-mno-red-zone`.

## Dynamic frames: VLAs and alloca

C99 let locals have runtime sizes (`int a[n];`) — implemented by a `sub rsp, computed`
in the prologue, and C's `alloca()` does the same inside a body. Fast (no heap!), and
dangerous: nothing checks that `n` is sane, so `alloca(1<<30)` walks straight into the
guard page. The Linux kernel banned VLAs outright in 2018 (every kernel frame size
must be statically provable — the stack is a scarce, shared, safety-critical
resource). This is a taste of Chapter 08's larger lesson: C gives you the machine's
freedom and the machine's teeth.

## What a local variable actually is

By now you can say it precisely: **a local is a compile-time-named byte range in the
current frame.** `&t` compiles to `lea rax, [rbp-4]` — the address is arithmetic on
the frame pointer. And now two famous phenomena make sense: (1) the dangling pointer
to a local — return its address, the `leave` batch-frees the whole frame, the bytes
still exist (nothing zeroes them) but belong to whoever's frame lands there next
(Chapter 08's UB gallery); (2) the debugger's "value optimized out" — at `-O2` the
variable may never get a slot at all, living in a register that the compiler moves
and retires; debug info describes a fiction the optimizer declined to build.

## What insiders know

- **The backtrace is a data structure**: `backtrace()`/libunwind walk the
  saved-rbp chain (or DWARF's `.eh_frame` when pointers were omitted); a corrupted
  saved rbp is why hacked or smashed stacks produce garbage traces (Chapter 21).
- **Tail calls are frame reuse**: `return f(x)` compiled as `jmp f` (not `call`)
  overwrites the current frame with the callee's — Scheme's loops (Chapter 18) and
  some recursion patterns run in constant stack for exactly this reason.
- **Stack size is a real production dial**: thread-per-request servers with 256 KB
  stacks meet 300 KB driver frames; deep template instantiation in C++, JSON
  parsers recursing on adversarial input (parser DoS — a CVE family), and recursive
  `toString`s are all stack-budget bugs, not "logic" bugs.
- **The canary's home** is just above saved rbp, between locals and the return
  address (`fs:0x28` via TLS — Chapter 21 shows the actual instructions): the only
  place where an overflow *must* pass through a secret to reach control data.
- **Stack frames are never zeroed on reuse** — the classic info-leak: a password
  buffered in a frame is still readable in the "dead" bytes when the next function's
  frame overlaps it without initializing. (`-ftrivial-auto-var-init` exists to stop
  exactly this.)
- **goroutines made the whole design rethink** (Chapter 11): a 2 KB stack cannot
  hold deep C-style frames, so Go *moves* the whole stack when it grows — impossible
  for native threads precisely because C code holds raw pointers into stack frames
  that the language cannot find and fix.

## See it yourself

Read a real frame with gdb — this is the exact skill Chapter 21 and Module 04 build
on:

```console
$ gcc -g -O0 frames.c && gdb ./a.out
(gdb) break scale
(gdb) run
(gdb) info frame        # frame at 0x7ffd...: rbp, saved regs, locals range
(gdb) x/8gx $rsp        # raw frame words: saved regs, locals
(gdb) bt                # the rbp chain as names
(gdb) disas scale       # find the prologue: push rbp; mov rbp,rsp; sub rsp,N
```

And overflow one, honestly:

```c
void f(int depth) { char pad[1024]; pad[0] = 1; f(depth + 1); }
int main(void) { f(0); }        # 8 MB / ~1 KB frames ≈ ~8000 frames then SIGSEGV
# run under gdb: "Program received signal SIGSEGV" — rsp in the guard-page range
# compile with -fsanitize=address: "stack-overflow ... stack size 8192 KB"
```

`ulimit -s` prints your limit; raise/lower it and watch the death depth change — the
stack is a *quota*, enforced by the guard page, a Chapter 03 virtual-memory device.

## How to use this knowledge

- **Crash triage**: a core dump's `rsp`/`rip` relationship tells you *where* you died
  in the tower; "rip near rsp in unmapped territory" = stack exhaustion; "rip = a
  byte pattern you recognize" = smashed return address (Chapter 21).
- **Recursion decisions**: recursion depth × frame size is a real product budget;
  this arithmetic tells you when to convert to iteration before production does.
- **Reading any disassembly or decompiled code** (Module 04): prologues, `leave`,
  `sret` slots, and the ABI register roles are the grammar of every binary you will
  ever read.
- **Concurrency intuition**: thread stack sizes, why context switches are cheap (swap
  rsp, save regs — no copying), and why fibers/coroutines (and goroutines) are "just
  stacks we can move."

## Connections

- **Chapter 03** — the stack as a map region, the guard page, limits as quotas.
- **Chapter 05** — the heap: what to do when lifetimes don't nest; malloc for the
  everything-else machine.
- **Chapter 07** — full assembly treatment: `sret`, varargs, `-O0` vs `-O2` frames.
- **Chapter 08 / 09** — dangling-local pointers, RAII's scope = frame lifetime.
- **Chapter 11 / 12 / 14** — goroutine stacks that move, JVM frames and -Xss,
  Python's recursion limit bridging C frames and interpreter frames.
- **Chapter 21** — smashing the frame: canaries, ROP gadgets *live at frame scale*.

## Quiz

1. **Concept.** Derive the stack from the shape of call lifetimes: state the property
   of function lifetimes that makes LIFO storage the zero-bookkeeping design.
2. **Concept.** Why is stack allocation one instruction while heap allocation needs a
   whole allocator (Chapter 05)? What does the compiler know at compile time that
   makes this possible?
3. **Predict the number.** Linux default stack 8 MB; `void f(){char b[4096]; f();}`
   — roughly how deep before SIGSEGV? Which Chapter 03 mechanism delivers the signal?
4. **Predict the behavior.** `int* f(){ int x=1; return &x; }` — the caller stores
   the pointer and dereferences it twice. Why might this "work" once and corrupt on
   the second use?
5. **Predict the output.** In gdb you see `x/1gx $rbp` = `0x55d5f4a07bc0` and
   `x/1gx $rbp+8` = `0x55d5f4a03412`. What are these two values to the current
   frame?
6. **Bug diagnosis.** A recursive-descent JSON parser crashes on a 10 MB deeply
   nested input at depth ~30,000 but not on 9 MB. What is the arithmetic, and name
   two fixes (one at the parser, one at the platform).
7. **Bug diagnosis.** A multithreaded service crashes at startup with SIGSEGV in
   `memcpy` called from a third-party library, only when thread stack size is set to
   256 KB. What is the diagnosis?
8. **Cross-chapter.** Go cannot keep C-style fixed stacks *and* support thousands of
   goroutines. Which mechanism does Chapter 11 use instead, and why is it impossible
   in C?

<details>
<summary>Answers</summary>

1. Call lifetimes *nest*: while `f → g → h` runs, h's locals must die first and f's
   last — the youngest allocation is always the next freed. A region with one moving
   pointer records the entire allocation state with no metadata: the pointer's
   position *is* the state; batch-claim (sub rsp) and batch-release (leave) are
   each one instruction.
2. The compiler knows each frame's exact size and layout at compile time, so claim
   and release are pointer arithmetic. The heap serves sizes unknown until runtime
   and lifetimes that don't nest, so it needs per-block metadata, reuse policy, and
   freelist/bins machinery (Chapter 05/19) to decide *which* freed bytes satisfy the
   *next* request.
3. Each recursion consumes one 4096-byte buffer plus ~48-64 bytes of frame overhead
   → ~8,388,608/4160 ≈ ~2,000 frames (a few thousand with compiler-optimized frames).
   The guard page below the stack — an unmapped page from Chapter 03's virtual-memory
   kit — raises SIGSEGV when rsp descends into it.
4. The frame is batch-freed but not erased: `x`'s bytes still hold 1 until some later
   call's frame reuses them. The first dereference often still reads 1 (nothing
   overwrote it yet); by the second, an intervening call's locals have overwritten
   the slot. Classic dangling-pointer nondeterminism — UB in C (Chapter 08) precisely
   because the standard refuses to promise which outcome occurs.
5. [rbp] holds the *saved caller's rbp* (parent frame pointer — one link of the
   backtrace chain) and [rbp+8] holds the *return address* into the caller. Those two
   words are the frame's connection to its past; overwriting them is exactly the
   target of Chapter 21's stack smashing.
6. ~30,000 × ~270 bytes/frame ≈ 8.1 MB — past the 8 MB quota. Fix at parser: impose
   a depth limit or convert to an explicit-stack loop (the parser owns its lifetime
   discipline). Fix at platform: raise `ulimit -s` (or thread stack size) — a quota,
   not a law; the right fix is the parser's, because unbounded recursion on
   adversarial input is a DoS vector regardless of quota.
7. The library's call chain (its frames plus large stack-locals or an alloca-style
   buffer) exceeds 256 KB at some depth; `memcpy` writing into a local buffer walks
   past the guard page. Diagnose by SIGSEGV address ≈ thread stack region top minus
   256 KB; fix by raising that thread's stack size — some libraries publish their
   stack requirement for exactly this reason.
8. Grow-by-copying: Go starts goroutine stacks tiny and, when the guard hits, copies
   the whole live stack to a larger region and *rewrites every pointer into it*
   (compiler metadata knows where they are). Impossible in C because raw pointers
   hide in untyped registers, unions, and foreign code — the language cannot find,
   let alone fix, pointers into a moved stack.

</details>
