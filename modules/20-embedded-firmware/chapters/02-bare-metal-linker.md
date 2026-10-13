# Chapter 02 — Bare metal: linker scripts are the memory model

> **The 30-second version.** On a microcontroller there is no kernel,
> no loader, no one to initialize anything — you are all three, and
> the blueprint is a build input: the **linker script**. The first
> instruction is fetched through a pointer in the first bytes of a
> file; the stack pointer is a constant the linker chose; `.data`
> lives at two addresses until your startup code copies it; `.bss` is
> zeroed because you zero it. This chapter walks that ladder, teaches
> the linker script as a small language, and ends at the payoff: an
> `ASSERT` is a machine-checked proof that your RAM budget holds —
> five seconds after you break it.

## The twelve steps from electrons to `main`

Module 01 ch04 asked "who initializes the stack?"; module 04 ch02
showed an OS loader doing that work. Here nobody does — and the
bootstrap fits one diagram. The ladder **is** the chapter:

```
 power                       the image (a file in flash)             your code
 ------
  1  POR: power-on-reset holds the core dead.
     SRAM is garbage; flash is a file.
  2  Boot ROM runs: mask ROM, a few KB, burned in the fab —
     first, because SRAM is blank and flash not yet trusted.
  3  It picks the boot source and the fetch address:
       STM32 F4: BOOT0=0 -> alias flash at 0x00000000, jump.
       RP2040/Pico: fetch 256 B from external flash in a slow
       "dumb SPI" mode: 252 B of boot2 + 4 B CRC. boot2 exists ONLY
       to reconfigure the SSI controller so flash is fast enough to
       execute from. Running from flash requires configuring the
       flash; configuring it requires running code — the 252 bytes
       break the cycle.
  4  Hardware reads word[0] @0x00000000 into MSP. That word is
     _estack — a constant the LINKER SCRIPT chose. The stack
     pointer is data in a file. (Worked slowly, below.)
  5  Hardware reads word[1] @0x00000004 into PC and jumps. No mode
     switch, no privilege change: one fetch. (x86's real-mode
     trampoline ladder, module 07 ch02, in one hop.)
 ----------------------------------------------------------------------
  6  Reset_Handler: load _sidata/_sdata/_edata (literal pool).
  7  Copy .data: flash -> SRAM. Initialized bytes have two addresses.
  8  Zero .bss: C guarantees zeroed globals; "someone" is you.
  9  SystemInit(): clocks, flash wait-states (chapter 01).
 10  __libc_init_array(): .preinit_array + .init_array — C++ ctors.
 11  main().
 12  main returns -> startup parks in for(;;). No exit(3):
     returning from main is a bug, not an event.
```

**Rung 4, worked slowly — the module's most disorienting fact.** No
instruction has executed, yet a stack pointer must be loaded. The
value is precomputed and stored *in the image*: at link time, `_estack
= ORIGIN(RAM) + LENGTH(RAM)` writes `0x20020000` into the file's first
four bytes; at reset, fetch hardware does `MSP = *(uint32_t
*)0x00000000` as a wiring-level act, before the first instruction
decodes. The stack pointer is data in a file — module 01 ch04's question,
answered by a build input.

Rung 5's entry has the Thumb bit set (LSB = 1): `0x08000101` =
Thumb code at `0x08000100`. No loader, no relocation — two words,
one jump, and you are in your own code.

## You are the loader: two addresses for every initialized byte

`Reset_Handler` is that loader, hand-written — ST's real
`startup_stm32f411xe.s`, trimmed:

```asm
Reset_Handler:
  ldr   sp, =_estack      /* belt-and-braces: hw already did it */
  movs  r1, #0            /* r1 = byte offset into .data */
  b     LoopCopyDataInit
CopyDataInit:
  ldr   r3, =_sidata      /* LMA: copy source, in flash */
  ldr   r3, [r3, r1]
  str   r3, [r0, r1]      /* store to VMA: run address */
  adds  r1, r1, #4
LoopCopyDataInit:
  ldr   r0, =_sdata       /* VMA start */
  ldr   r3, =_edata       /* VMA end   */
  adds  r2, r0, r1
  cmp   r2, r3
  bcc   CopyDataInit

  ldr   r2, =_sbss
  b     FillZeroBss
ZeroBss:
  movs  r3, #0
  str   r3, [r2]          /* C's zero-init = these 2 instrs */
  adds  r2, r2, #4
FillZeroBss:
  ldr   r3, =_ebss
  cmp   r2, r3
  bcc   ZeroBss

  bl  SystemInit          /* clocks + flash wait-states */
  bl  __libc_init_array   /* the init-array walk */
  bl  main
  bx  lr                  /* fault if main returns */
```

Why copy at all? Initialized variables must be *writable* (RAM) and
*start* non-zero (only flash survives power cycles): every `.data`
byte has two addresses — the **VMA**, where it runs (`>RAM`), and the
**LMA**, where the file places the initializer image (`AT> FLASH`).
The startup loop reconciles them:

```
      flash: the file view (LMA)               SRAM: the run view (VMA)
0x08000000 +---------------------------+ 0x20000000 +----------------------+
           | .isr_vector  (2+ words)  |            | .data                |
           | .text  .rodata           |            |   msg[] "alive\n"    |
           | .ARM.exidx  (2-4% flash) |            +----------------------+ <- _edata
           +---------------------------+ <- _sidata| .bss (NOLOAD)        |
           | .data load image:         |            |   counters, buffers  |
           |   "alive\n" ...           |            +----------------------+ <- _ebss=_end
           +---------------------------+            | (heap grows up)      |
                                                0x20020000 ---------------<- _estack
                    one byte, two homes:             (MSP grows down)     copy: _sidata->_sdata
```

`.bss` has only a VMA and no bytes in the file: `NOLOAD` emits no
initializers, and the zero loop *is* the C standard's "static storage
is zero-initialized" — the kernel's demand-zero mapping (module 07
ch02) replaced by two instructions. Delete the loop and globals are
garbage on real silicon; QEMU zero-fills SRAM and masks the bug.

Rungs 9-10 are the invisible C/C++ taxes: `SystemInit` sets clocks and
flash wait-states (past ~16 MHz, wrong values corrupt fetches —
chapter 01); `__libc_init_array` walks `.preinit_array` and
`.init_array` — newlib hooks and every C++ global constructor, a
*boot-time cost* before `main` on half-configured hardware (a
constructor touching peripherals before `SystemInit` is the classic
bring-up deadlock).

## The linker script: the memory map, written down as code

The linker script is chapter 01's memory map in *source form* — a
declarative language restating silicon so the toolchain obeys. A real,
trimmed STM32F411 script:

```
/* stm32f411.ld — the memory model of this chip, written down */
ENTRY(Reset_Handler)

_Min_Heap_Size  = 0x200;   /* 512 B — reservations */
_Min_Stack_Size = 0x400;   /* 1 KB                 */

MEMORY
{
  FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 512K
  RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
}
_estack = ORIGIN(RAM) + LENGTH(RAM);  /* rung 4's constant: word[0] */

SECTIONS
{
  .isr_vector :
  {
    . = ALIGN(4);
    KEEP(*(.isr_vector))            /* hw reads this; no code references it */
    . = ALIGN(4);
  } > FLASH                          /* must land at 0x08000000, first      */

  .text :  { *(.text*) *(.rodata*) } > FLASH
  .ARM.exidx : { *(.ARM.exidx*) } > FLASH   /* unwind tables: 2-4% of flash */

  .data :
  {
    . = ALIGN(4);
    _sdata = .;                     /* VMA: where it runs */
    *(.data*)
    . = ALIGN(4);
    _edata = .;
  } > RAM AT> FLASH                 /* LMA: where the file puts it */
  _sidata = LOADADDR(.data);        /* copy source, linker-computed */

  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    _sbss = .;
    *(.bss*) *(COMMON)
    _ebss = .;
  } > RAM

  ._user_heap_stack (NOLOAD) :      /* a FAKE section: reservation only */
  {
    . = ALIGN(8);
    _end = .;
    . = . + _Min_Heap_Size + _Min_Stack_Size;
    . = ALIGN(8);
  } > RAM

  ASSERT(_end + _Min_Heap_Size + _Min_Stack_Size <= _estack,
         "RAM overflow: .bss + heap + stack do not fit")
}
```

Read it as a language:

- **`MEMORY{}`** is the ch01 map restated: named regions with
  permissions and extents — silicon's contract, enforced at build
  (writable sections are *refused* in the `rx` region).
- **`.isr_vector` with `KEEP`** is load-bearing: hardware reads the
  table and no instruction references it, so `--gc-sections` would
  drop it as dead — `KEEP` annotates "an agent outside the ELF reads
  this." Forget it and the first gc-sections build hardfaults:
  word[0] is a `.text` word, MSP is instruction bits.
- **`.ARM.exidx`** is the ARM unwind table, ~8 bytes per function
  (PREL31 offset + handler word). With exceptions: 2-4% of flash plus
  the unwinder; `-fno-exceptions` (ch01's default) deletes both — the
  20-50 KB savings, seen from the linker side.
- **`.data > RAM AT> FLASH`** is the two-address trick: `>RAM` gives
  the VMA, `AT> FLASH` the LMA, and `LOADADDR(.data)` — a built-in
  *function* of the language — returns the LMA so `_sidata` stays
  exact while `.text` grows and shrinks; the startup loop never needs
  editing.
- **`.bss (NOLOAD)`** — bytes in RAM, none in the file.
- **`._user_heap_stack`** holds no input sections; it is a
  *reservation*, advancing the location counter past heap+stack so
  the `.map` accounts for them. Slow down here — the payoff:
  **the linker script is a static analyzer.** The `ASSERT` states a
  theorem — `.bss + heap + stack <= RAM` — over the closed world of
  static allocations; `ld` proves it or refuses to produce a binary
  (the refusal, verbatim, is Lab 3). Module 19 ch02 called types
  theorems and typed programs proofs: the same move at kilobyte
  scale, the linker as type checker. Nothing at runtime allocates —
  the only "allocator" here is the linker.

## The vector table as a C array — and the price of forgetting one

The table at 0x00000000 is 16 system entries (MSP, Reset, NMI,
HardFault ... SysTick at `0x3C`), then one per device interrupt. In C,
it is a function-pointer array plus the weak-alias pattern vendor
startups use:

```c
extern void _estack;              /* defined in the script, not any .c file */

void Reset_Handler(void);
void Default_Handler(void) { for (;;) {} }        /* the silent trap */
void NMI_Handler(void)        __attribute__((weak, alias("Default_Handler")));
void HardFault_Handler(void) __attribute__((weak, alias("Default_Handler")));
/* ...one line per IRQ you do NOT plan to write...                   */

__attribute__((section(".isr_vector"), used))
void (* const vector_table[])(void) = {
    (void (*)(void))&_estack,     /* 0x00: initial MSP — data in a file */
    Reset_Handler,                /* 0x04: reset vector, Thumb bit set  */
    Default_Handler,              /* 0x08: NMI (weak)                   */
    Default_Handler,              /* 0x0C: HardFault (weak)             */
    /* ... 12 more system entries, then every device IRQ ... */
};
```

Define a handler anywhere and the strong symbol wins; ignore it and
the alias stands. The trap: you enable the USART receive interrupt,
forget `USART2_IRQHandler`, a byte arrives — and PC parks in
`Default_Handler`'s empty `for(;;)`. No fault, no message: **a
forgotten handler is a silent hang by construction** (the tell: PC in
a `Default_*` function, xPSR in handler mode). Assembler spelling:
`.weak NMI_Handler` + `.thumb_set NMI_Handler,Default_Handler`.

The question module 04 ch02 forces: *where are the PLT/GOT, the
relocations, the PIC?* Nowhere — the absence is the design. Desktop
shared libraries load at runtime-chosen addresses, so references must
be indirected or fixed up at load — a permanent 0-3% tax. Here the map
is silicon-fixed and the image always at `ORIGIN(FLASH)`: addresses
are absolute, burned in at link time, and the whole "loader" is the
copy loop you read. Dynamic loading needs an MMU and a runtime
resolver (module 01 ch03); the µC has neither.

## Two stacks, a fake heap, and a budget you can prove

**MSP vs PSP.** The Cortex-M banks *two* stack pointers: handler mode
always uses MSP; thread mode can switch to PSP (`CONTROL.SPSEL`).
Bare metal ignores this; the chapter 04 RTOS does not — each task gets
a PSP stack, every ISR shares one MSP stack:

```
    thread mode (main, RTOS tasks)           handler mode (ALL ISRs)
   +----------------------------+           +----------------------------+
   | task A stack (PSP)  1 KB   |           |                            |
   +----------------------------+   IRQ     |      the ONE MSP stack     |
   | task B stack (PSP)  1 KB   |  ------>  |  shared by every handler   |
   +----------------------------+           |  sized by nesting math     |
   | idle task stack   256 B    |           |  (below) — the only number |
   +----------------------------+           |  an ISR writer must trust  |
   | CONTROL.SPSEL = 1 in tasks |           +----------------------------+
   +----------------------------+      EXC_RETURN 0xFFFFFFFD = "return to
   | main's pre-RTOS stack (MSP) |      PSP"; 0xFFFFFFF9 = "return to MSP"
   +----------------------------+      (ch03: which stack to unwind on return)
```

Payoff: MSP needs only the *nesting* budget, not per-task depth —
task stacks are sized per-task (ch04, whose overflow checker watches
each PSP).

**Sizing the main stack without a guard page** — no MMU, two honest
methods:

1. **Worst-case arithmetic.** Exception entry auto-stacks 8 words
   (r0-r3, r12, LR, PC, xPSR) = 32 B per nesting level; add the
   handler's own frame (from `-fstack-usage`) times the worst nesting
   your NVIC priorities permit; *add* the deepest thread-mode call
   tree — the worst case is "main at its deepest when interrupts nest
   on top." Two numbers, added — quiz Q4.
2. **Pattern fill, measured post-run.** Fill the unused stack with
   `0xDEADBEEF` after the `.bss` loop, run the worst case, halt, and
   scan down from `_estack` for the first surviving pattern — the
   high-water mark. Usage = `_estack` − that address; add 15-25%
   margin and *write it into `_Min_Stack_Size`*.

**The heap, if you dare.** newlib's `malloc` bottoms out in `_sbrk`,
the old Unix break interface from module 01 ch05, shrunk — *you* write
the 15 lines:

```c
/* _sbrk.c — everything malloc needs from the outside world */
#include <errno.h>
#include <sys/types.h>

extern char _end, _estack;           /* script symbols: heap start, RAM top */
static char *brk;

caddr_t _sbrk(int incr)
{
    char *prev;
    if (brk == 0) brk = &_end;
    prev = brk;
    if (brk + incr > &_estack) {     /* naive bound: ignores the stack! */
        errno = ENOMEM;
        return (caddr_t)-1;          /* malloc returns 0; who checks? */
    }
    brk += incr;                     /* one-way: free() keeps its pool */
    return (caddr_t)prev;
}
```

The shim tells the truth: the heap is a static region between `_end`
and the stack, growth is one-way, and the bound shown is naive (quiz
Q7). Hence malloc-is-banned cultures (MISRA-style, most production
firmware): module 01 ch05's fragmentation math with the arena shrunk
1000× — a 2 KB hole in a 20 KB heap is 10% permanent loss, and
allocation failure is a *design* error, not a runtime event.

**The `.map` file as budget document.** Build with `-Wl,-Map=out.map
-Wl,--print-memory-usage`, pair `-ffunction-sections` with
`-Wl,--gc-sections` so dead functions drop — and *review the map in
the PR*: "it fit yesterday" is not a state you may occupy. The review
commands:

```console
$ arm-none-eabi-nm -S --size-sort proj.elf | tail -n 6
08002f68 000005c4 t _dtoa_r     1476 B   float formatting: one printf("%f")
08003d14 0000040c t _malloc_r   1036 B   the heap, in code form
080012a4 0000030c t _svfprintf_  780 B   (shape typical of a newlib build;
                                        run it on your own .elf)
```

`--print-memory-usage` prints the one-liner pinned into the PR (exact
GNU ld 2.44 format):

```console
Memory region         Used Size  Region Size  %age Used
           FLASH:          68 B       512 KB      0.01%
             RAM:          9 KB        64 KB     14.06%
```

## What insiders know

- **The VTOR relocation trap.** The table's base is not hardwired:
  `VTOR` (0xE000ED08) can point it at RAM, where a bootloader patches
  entries at runtime. Traps: alignment must be a power of two at
  least the table size (128 B minimum on Cortex-M3), and M0/M0+ *have
  no VTOR* — relocation there needs the SYSCFG "remap SRAM to address
  0" trick. Errata constrain legal VTOR values on some cores.
- **ISRs are nearly free to *enter* — by architecture.** On x86 the
  handler first pushes everything (module 01 ch04's PUSH-all
  prologue); on M-profile the *hardware* stacks the 8-word frame
  before your first instruction, and the AAPCS rule "handlers may
  clobber r0-r3, r12" matches exactly that set — a bare C function is
  a legal ISR body; `__attribute__((interrupt))` mainly buys
  FP-register saving on M4F/M7.
- **`noinline` and `naked` are stack-budgeting tools.**
  `-fstack-usage` emits a `.su` file per function (frame size); CI
  sums the worst call path. `noinline` pins a function's contribution
  across compiler versions; `naked` (no prologue) writes C trampolines
  that cannot touch the stack before it exists.
- **`-fstack-usage` + `-Wl,--print-memory-usage` are CI guards, not
  reports.** Fail the build when FLASH `%age Used` crosses a threshold
  or a `.su` frame exceeds its budget class (ISR frames especially).
- **"It's in flash, can't debug it" — the FPB.** Debuggers cannot
  freely rewrite flash to plant breakpoints; the Flash Patch and
  Breakpoint unit's comparators trap execution *without modifying
  flash*: 6 on Cortex-M3 (4 instruction + 2 literal), 2 on M0+, none
  on M0. Beyond that count the debugger degrades or refuses.
- **External SDRAM needs a two-stage boot.** Big-RAM boards cannot run
  startup from SDRAM: the controller and its timings must be
  configured by *running* code first — boot2's chicken-and-egg, one
  level up. Flash-resident stage one configures SDRAM, then copies or
  jumps; the heap usually lives there, `Reset_Handler` never can.

## See it yourself

**Lab 0 (paper, no toolchain).** Download ST's `startup_stm32f411xe.s`
and the F411 datasheet's memory-map page; walk the 12 rungs on paper,
annotating each with the address it touches.

**Lab 1 — a 60-line world, boot to "alive".** QEMU's MPS2 AN385 is a
Cortex-M3: 4 MB at `0x00000000` (`-kernel` lands the ELF there),
4 MB SRAM at `0x20000000`, UART0 at `0x40004000` (DATA 0x0, STATE 0x4,
CTRL 0x8). The entire program:

```c
/* bare.c — everything before main, in one file. QEMU mps2-an385. */
#include <stdint.h>

extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss;  /* lab.ld symbols */

volatile uint32_t *const UART0_CTRL  = (uint32_t *)0x40004008u; /* bit0: TX en  */
volatile uint32_t *const UART0_STATE = (uint32_t *)0x40004004u; /* bit0: TX busy */
volatile uint32_t *const UART0_DATA  = (uint32_t *)0x40004000u; /* write: send  */

static char msg[] = "alive\n";   /* .data: lives in BOTH memories */

void Reset_Handler(void)
{
    uint32_t *src = &_sidata, *dst = &_sdata;
    while (dst < &_edata) *dst++ = *src++;   /* rung 7: LMA -> VMA */
    for (dst = &_sbss; dst < &_ebss; ++dst)  /* rung 8: the zero promise */
        *dst = 0;

    *UART0_CTRL = 1u;
    for (char *p = msg; *p; ++p) {           /* works only because rung 7 ran */
        while (*UART0_STATE & 1u) {}
        *UART0_DATA = (uint32_t)*p;
    }
    for (;;) __asm volatile ("wfi");         /* park; there is no exit */
}

__attribute__((section(".isr_vector"), used))
void (* const vector_table[])(void) = {
    (void (*)(void))0x20040000u,  /* 0x00: MSP = top of SRAM: data in a file */
    Reset_Handler,                /* 0x04: reset vector; Thumb bit free */
};
```

```text
/* lab.ld — the memory model of this board, written down */
MEMORY { FLASH (rx) : ORIGIN = 0x00000000, LENGTH = 4M
         RAM   (rwx): ORIGIN = 0x20000000, LENGTH = 4M }
ENTRY(Reset_Handler)
SECTIONS
{
  .isr_vector : { KEEP(*(.isr_vector)) } > FLASH   /* must sit at address 0 */
  .text   : { *(.text*) *(.rodata*) } > FLASH
  .data   : { _sdata = .; *(.data*) _edata = .; } > RAM AT> FLASH
  _sidata = LOADADDR(.data);
  .bss (NOLOAD) : { _sbss = .; *(.bss*) _ebss = .; } > RAM
}
```

```console
$ arm-none-eabi-gcc -mcpu=cortex-m3 -mthumb -nostdlib -ffreestanding \
    -ffunction-sections -Wl,--gc-sections -Wl,-Map=lab.map \
    -Wl,--print-memory-usage -T lab.ld bare.c -o bare.elf
$ qemu-system-arm -M mps2-an385 -nographic -kernel bare.elf
QEMU 8.2.2 monitor - type 'help' for more information
alive
```

(Banner first; `Ctrl-A x` quits.) Delete the copy loop: "alive"
becomes an empty line — `msg`'s bytes sit only at its LMA. Note there
is no `main`: rung 11 is wherever you say it is.

**Lab 2 — read the budget.** Rerun the build (or open `lab.map`):
find `.data`'s VMA (`0x20000000`), size (8 B — `msg` plus padding),
its `load address` in flash, and confirm `_sidata` equals that LMA.
Compute: total flash, total RAM, headroom.

**Lab 3 — break the proof.** Shrink the budget to one a µC respects
(`RAM LENGTH = 64K`, `_estack = ORIGIN(RAM) + LENGTH(RAM)`, plus the
reservation and `ASSERT` from the STM32 script), add
`static uint8_t blob[100*1024];`, relink. GNU ld refuses — exactly
this shape (captured verbatim on GNU ld 2.44; `arm-none-eabi` prints
the same text with its own path):

```console
arm-none-eabi/bin/ld: bare.elf section `.bss' will not fit in region `RAM'
arm-none-eabi/bin/ld: RAM overflow: .bss + heap + stack do not fit
arm-none-eabi/bin/ld: region `RAM' overflowed by 37888 bytes
```

Three lines: the mechanical failure, *your* ASSERT message, and the
arithmetic (100 KB blob + 1 KB reservation − 64 KB RAM = 37888). The
oversubscription is impossible to *ship* because the binary is
impossible to *produce* — module 19's soundness thesis in five seconds.

**Lab 4 — 0xDEADBEEF stack sizing, as a procedure.** (1) After the
`.bss` loop, fill from `_ebss` up to `_estack` (minus your heap) with
`0xDEADBEEF`. (2) Run the worst case: deepest call path, heaviest
interrupt load. (3) Halt (QEMU: `-S -gdb tcp::1234`; `x/1024wx
&_ebss` in gdb) and scan down from `_estack` for the first surviving
pattern word. (4) Write usage + 25% into the reservation.

## How to use this knowledge

- **Bring-up triage.** A blank board is a binary search over 12 rungs:
  `x/2wx 0` (is word[0] `_estack`?); did PC reach `Reset_Handler`? Did
  the copy run? Is PC parked in `Default_Handler`?
- **Code review shapes.** A `.bss` array without a map delta, an IRQ
  enable without a handler, a constructor before `SystemInit`, a
  `malloc` outside startup — each is a one-line comment citing a rung
  or a theorem.
- **Sizing interviews.** "How much RAM do you need?" now has a
  derivable answer — `.data` + `.bss` from the map, stack from nesting
  arithmetic or the DEADBEEF scan — and that answer, in numbers,
  separates a hobbyist from a firmware engineer.

## Connections

- **Chapter 01** — the silicon-fixed memory map written down in
  `MEMORY{}`; the `volatile` syntax the lab runs on.
- **Chapter 03** — the vector table's device IRQ entries; the 8-word
  auto-stacked frame sized here; EXC_RETURN's MSP/PSP bit, the split
  diagram's engine.
- **Chapter 04** — the RTOS puts every task on its own PSP; the
  nesting-only MSP budget derived here is why that is affordable.
- **Chapter 05** — rungs 1-3 re-asked adversarially: who signs the
  image the boot ROM jumps to?
- **Module 01 chapter 04** — the stack from a file: "who initializes
  it?" answered to the end — `_estack`, read by silicon as word[0].
- **Module 01 chapter 05** — the heap fragmentation math, shrunk
  1000× into the `_sbrk` pool.
- **Module 04 chapter 02** — the loader a µC does not need: PLT/GOT
  exist precisely because desktop libraries load at runtime-chosen
  addresses; position-FIXED code never pays the 0-3% PIC tax.
- **Module 07 chapter 02** — the same ladder scaled: a loader mapping
  sections, demand-zeroing `.bss`, running initializers before
  `main` — `Reset_Handler` with page tables.
- **Module 19 chapter 02** — the `ASSERT` as proof, the script as
  theorem environment: soundness at kilobyte scale.

## Quiz

1. **Concept.** Why is the initial stack pointer loaded from word[0]
   of the image rather than hardwired "top of RAM"? What does that buy
   the designer — and which file owns your stack size?
2. **Concept.** In terms of LMA and VMA, why does `.data` need a
   runtime copy step while `.bss` does not — and where do `.bss`'s
   "initial contents" come from?
3. **Predict the number (RAM budget from a map).** A `.map` shows
   `.data` at VMA `0x20000000` size `0xF8`; `.bss` next, size `0xA3B0`;
   then `._user_heap_stack` with `_Min_Heap_Size = 0x200`,
   `_Min_Stack_Size = 0x600`, and `ASSERT(_end + 0x800 <= _estack)`;
   `_estack = 0x20020000`, RAM = 128 KB. Compute (a) RAM committed by
   the theorem, (b) headroom, (c) the largest zero-initialized `static`
   array that still links.
4. **Predict the number (stack budget).** Exception entry auto-stacks
   8 words; each handler's own frame is 56 B per `-fstack-usage`; NVIC
   priorities permit 3-deep nesting; the deepest thread-mode call tree
   is 1,200 B. Compute the MSP budget, the reservation with 25%
   margin, and your conclusion about the ST default
   `_Min_Stack_Size = 0x400`.
5. **Predict the output.** Someone deletes the `.data` copy loop but
   keeps `static char msg[] = "alive\n"` and the print loop. What
   appears on the terminal, and where do `msg`'s bytes live in that
   broken build?
6. **Diagnose.** A board enables a UART receive interrupt; the build
   was clean. At the first received byte it stops responding — no
   fault, no output, PC spinning in `Default_Handler` with xPSR in
   handler mode. What happened, and why was it *guaranteed silent*
   by design?
7. **Diagnose.** Firmware links with RAM at 96% (reservation in
   place), passes bench tests, hardfaults in the field under peak load
   after days. The heap uses the `_sbrk` shim shown. Name the
   collision the link-time proof did *not* cover, and give the
   two-line fix.
8. **Cross-chapter.** Module 04 chapter 02 spent a chapter on PLT/GOT
   and relocation processing. Name the two properties of this
   chapter's world that make all of it unnecessary, and what those
   properties cost a desktop shared library that pays the PIC tax.

<details>
<summary>Answers</summary>

1. No instruction has executed, so no code can *compute* a stack top —
   the value must come from nonvolatile storage read by fetch
   hardware. It buys flexibility at zero runtime cost: different RAM
   sizes, RAM elsewhere, reserved top-of-RAM regions, chosen offline
   by editing one constant. Your stack size is owned by the linker
   script (`_estack`).
2. `.data` is writable (VMA in RAM) but nonzero-initialized, and only
   flash survives power cycles — the file carries the initializers at
   an LMA; startup reconciles LMA→VMA by copying. `.bss` is
   zero-initialized, and zero is a state SRAM can be *put into*
   cheaply: `NOLOAD` emits no file bytes; the zero loop manufactures
   the contents (real silicon powers up as garbage; QEMU zero-fills,
   masking a broken loop).
3. `_end = 0x200000F8 + 0xA3B0 = 0x2000A4A8`. (a) Committed =
   `_end + 0x800` = `0x2000ACA8` = **44,200 bytes**. (b) Headroom =
   `0x20020000 − 0x2000ACA8` = `0x15358` = **86,872 bytes**. (c) The
   array must keep `_end + array + 0x800 <= 0x20020000`: at most
   **86,872 bytes** (86,876 would refuse to link).
4. Per level: 32 B auto-frame + 56 B handler = 88 B; × 3 = 264 B.
   Plus the thread tree: 264 + 1,200 = **1,464 B** minimum. With 25%
   margin ≈ 1,830 B → reserve **2,048 B**. The ST default 0x400 =
   1,024 B is *below the minimum*: oversubscribed before any local
   exists.
5. An empty line. The bytes sit only at `msg`'s LMA in flash —
   nothing copies them — while the print loop reads the VMA in SRAM,
   which QEMU zero-fills: the loop stops at the first NUL, writes
   nothing. On real silicon that read returns garbage. (A `const`
   `msg` lives wholly in `.rodata` and still prints.)
6. The IRQ was enabled but no `USARTx_IRQHandler` was written, so the
   table's weak alias sent the core to `Default_Handler`'s `for(;;)`.
   Weak aliases make an unwritten handler a legal *link* (no undefined
   symbol) and a legal *execution* (an infinite loop raises no
   exception) — "forgot one" fails as a hang, not a crash. Fix: write
   the handler (strong symbol overrides) or don't enable the
   interrupt.
7. The theorem covers *static* allocations only: `.data`, `.bss`, and
   the reservations. The collision it cannot see: the heap growing up
   from `_end` (bounded by `&_estack`, the very top) meeting the MSP
   stack growing down from `_estack`. At peak load an ISR's
   auto-stacked frame lands on heap metadata, and the fault surfaces
   days later. Fix: bound `_sbrk` at `&_estack - _Min_Stack_Size` —
   the same number the script reserved.
8. (i) A **fixed load address**: the map is wired into silicon and the
   image always sits at `ORIGIN(FLASH)` — every reference resolves to
   an absolute address at link time; nothing to relocate at load.
   (ii) **No dynamic loading**: no second consumer of the code, no
   runtime symbol resolution — nothing for PLT/GOT to indirect *for*.
   A desktop shared library can assume neither: it is mapped at an
   OS-chosen address (ASLR — module 01 ch03) by a runtime loader, and
   pays the GOT/PLT indirection and its 0-3% overhead as the price.

</details>
