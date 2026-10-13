# Chapter 01 — The memory map is wired into silicon

> **The 30-second version.** A $0.50-$5 microcontroller — ~30 billion units
> a year, ~20:1 over the PC/phone world — has no MMU, and that absence
> rewires everything you learned. The 4 GB address space is not
> built by page tables at boot; it is wired into the chip's address
> decoder: flash at 0x08000000, SRAM at 0x20000000, peripherals at
> 0x40000000, the interrupt controller at 0xE000E100 — physical, public,
> identical on every unit forever. ASLR is not missing but *impossible*: an
> address in the firmware image is the thing. Protection collapses to the
> MPU's 8-16 regions, "page fault" becomes "bus fault," and 2-128 KiB RAM
> budgets make static allocation the discipline, `printf` a budget line,
> `volatile` load-bearing syntax.

## Thirty billion computers a year, and not one page table

For $0.50-$5 you buy a Cortex-M0+ chip at 48-133 MHz (a Pi
Pico's RP2040: two M0+ cores at 133 MHz, ~$0.70) with 2-128 KiB SRAM,
16-512 KiB flash, no MMU, no cache or a small one, no OS. The numbers
invert the world you learned: **~30 billion microcontrollers ship per
year** (recent industry counts: ~25-30 B) versus **~300 million PCs**
(Gartner 2023-2024: ~240-260 M). Add ~1.2 B smartphones and embedded
still outnumbers general-purpose computing ~20:1. Your car carries ~100, a
washing machine several, every power meter, keyboard, SSD controller, and
earbud one — modules 01-17's "computer" is the *minority architecture*.

Now cross off module 01's machinery:

- **No page tables (module 01 ch03).** The CPU puts physical addresses
  straight on the bus — one space, visible to every instruction,
  peripheral, and DMA engine.
- **No ASLR, no per-process spaces.** Nothing remaps anything, ever.
- **No swap, no demand paging.** RAM is all there is; flash is not a
  paging backend.
- **No OS between tasks (module 01 ch07).** Your `main` loop *is* the OS —
  until chapter 04's 10 KB scheduler.
- **The heap is optional (module 01 ch05).** Most disciplined firmware
  never calls `malloc`; the linker script is the allocator.

Module 01's questions with the safety net removed; module 02's attacks
with the defenses removed — both get sharper, not poorer.

## The map: a data structure wired into silicon

On a PC the memory map is a *negotiation*: firmware inventories RAM, the
OS builds page tables, the MMU translates. On a µC the map is a datasheet
page — the interconnect's address decoder routes 0x080xxxxx to flash,
0x200xxxxx to SRAM, 0x400xxxxx to peripherals, 0xE000xxxx to the core.
The address bus *is* the routing table, frozen at the fab. The
STM32F407 (Cortex-M4, 1 MiB flash, 128 KiB SRAM), as RM0090 section 2.3
prints it:

```
  STM32F407 memory map — 4 GB, physical, public, per-datasheet
  (every address readable by every load/store in the system)

0xE0000000 ┌─────────────────────────────────────────────────────────┐
           │ ARM system region — belongs to the CORE, not ST:        │
           │   SysTick  0xE000E010   the tick timer                  │
           │   NVIC     0xE000E100   interrupt controller (ch03)     │
           │   SCB      0xE000ED00   CPUID, fault status, VTOR       │
           │   MPU      0xE000ED90   this chapter's registers        │
0xDFFFFFFF ├─────────────────────────────────────────────────────────┤
           │ reserved holes: reading here faults (per-SOC behavior)  │
0x40000000 ├─────────────────────────────────────────────────────────┤
           │ peripherals, in bus order:                             │
           │   APB1 0x40000000  USART2/3, I2C, timers, DAC           │
           │   APB2 0x40010000  USART1, SPI1, ADC, EXTI              │
           │   AHB1 0x40020000  GPIOA at 0x40020000, GPIOB +0x400 …  │
0x3FFFFFFF ├─────────────────────────────────────────────────────────┤
           │ reserved hole                                          │
0x2001C000 │ SRAM2/3: 16 KiB — .data/.bss/stacks continue here       │
0x20000000 │ SRAM1: 112 KiB — the .data/.bss/stack/heap home         │
0x1FFFFFFF ├─────────────────────────────────────────────────────────┤
           │ reserved hole                                          │
0x1FFF0000 │ system memory: ST's boot ROM + OTP (UID, factory cal)   │
0x10000000 │ CCM RAM: 64 KiB, core-only — DMA CANNOT reach it        │
0x0FFFFFFF ├─────────────────────────────────────────────────────────┤
           │ reserved hole                                          │
0x08000000 │ FLASH: up to 1 MiB — .isr_vector/.text/.rodata,         │
           │                      .data's initial values             │
0x00000000 │ alias: whatever the BOOT pins select maps here          │
           │   (BOOT0=0 flash | BOOT0=1 boot ROM | SRAM for debug)   │
           └─────────────────────────────────────────────────────────┘
```

Three properties matter more than the contents.

**The fixedness is the feature.** A peripheral's address is a
*compile-time constant*: `GPIOA` is 0x40020000 in every F407 program
ever written — vendor headers are `#define` files over this bar. The
linker script (chapter 02) places your sections at exact addresses
inside it, and the `.bin` must run unmodified on every unit, forever.
Chapter 05's updates work by *swapping banks*, not relocating.

**The map is per-family, not per-ARM.** Two Cortex-M4s — ST's F407 and
Nordic's nRF52840 — agree on one line: 0xE0000000. The core's system
region is ARM-standard; the rest is the vendor's wiring. "Which chip,
which revision?" opens every embedded bug report.

**Even the stack pointer comes from this map.** Hardware loads the
initial MSP from 0x00000000 and the reset vector from 0x00000004 — both
plain words in flash. The *stack pointer is data in a file*; chapter 02
works that fact until it feels natural.

## No ASLR: an address is the thing

Module 01 ch03 built virtual memory as one indirection — VA → page table
→ PA — with the OS rewriting the mapping at `exec()` and `mmap()`: that
rewriting *is* ASLR. Remove the indirection and randomization has nothing
to operate on: every address is fixed by the linker at build time,
identical on every device.

Recompute module 02 ch02's attack economics: the x86-64 target cost the
attacker three defenses — a canary to reproduce (leak first), PIE/ASLR to
defeat (~28 bits to leak or brute-force), NX to route around (ROP). On a
bare Cortex-M4:

- **The target address is free.** The attacker reads it from the same
  image you flash — via the debug port unless chapter 05's locks are set.
  No leak, no guess: entropy is *zero*; the return-oriented attacker
  needs no info leak.
- **The buffer-overflow primitive is unchanged.** Module 01 ch04's frame —
  locals below, return address above, no runtime bounds — is identical;
  `read(0, buf, 256)` overflows a 64-byte local exactly as in module 02's
  lab.
- **DEP is *weaker*, not merely absent.** The ARMv7-M default attribute
  map marks 0x40000000 and above execute-never — but SRAM is **executable
  by default**: injected bytes on the stack just run. 1996's executable
  stack, in silicon, out of the box.
- Stack canaries? `gcc -fstack-protector` works on ARM, but vendor SDK
  builds frequently omit it — and without ASLR, a leaked canary is
  replayable forever.

On a µC the defense budget is one bit — the MPU's XN for the stack —
instead of DEP+ASLR+canary. The next section spends it well.

## The MPU: the page table's poor cousin

The MPU is what survives when you delete the MMU. A page table is
billions of 4 KiB translations, per-process spaces, demand paging, COW;
the MPU is **8 or 16 regions** (read `MPU_TYPE` at 0xE000ED90 for your
core's count — Cortex-M4s commonly have 8), each power-of-two-sized,
minimum 32 bytes, base aligned to its size, five meaningful permission
bits. That is the whole protection state.

The programming model is three registers:

```
MPU_RNR   0xE000ED98   select the region RBAR/RASR will program
MPU_RBAR  0xE000ED9C   region base: ADDR[31:5] | VALID | REGION[3:0]
MPU_RASR  0xE000EDA0   XN[28] | AP[27:24] | TEX/C/B[21:16] | SRD[15:8] |
                        SIZE[7:1] | EN[0]   (SIZE = log2(bytes)-1)
```

One worked configuration — a 256-byte task stack, read-write,
execute-never:

```c
/* ARMv7-M: the MPU is programmed by ordinary memory-mapped stores. */
#define MPU_CTRL  (*(volatile unsigned *)0xE000ED94u)
#define MPU_RNR   (*(volatile unsigned *)0xE000ED98u)
#define MPU_RBAR  (*(volatile unsigned *)0xE000ED9Cu)
#define MPU_RASR  (*(volatile unsigned *)0xE000EDA0u)

void mpu_task_stack(unsigned n, unsigned base /* 0x20000800, 256B-aligned */)
{
    MPU_RNR  = n;
    MPU_RBAR = base;
    /* XN=1 | AP=3 (RW from any privilege) | SIZE=7 (2^8 = 256 B) | ENABLE */
    MPU_RASR = (1u << 28) | (3u << 24) | (7u << 1) | 1u;   /* 0x1300000F */
    MPU_CTRL = 1u | 4u;  /* enable + PRIVDEFENA: privileged keeps the
                            default map; unprivileged sees ONLY regions
                            — the RTOS "task wall" */
    __asm volatile ("dsb; isb");   /* effective from here on */
}
```

That one word is the task's entire "page-table entry." Rewriting
`MPU_RNR/RBAR/RASR` at each context switch — a few stores — is the RTOS's
page-table switch (chapter 04). Contrast honestly: 4 KiB pages per
process, remapped at fault time, versus 8-16 regions of one space,
rewritten at context switch. Poor cousin, but a cousin.

**What the MPU gives you:**

- **Task walls.** Unprivileged code plus regions for its stack and data:
  a wild pointer from one task faults instead of corrupting a neighbor —
  the "process" abstraction rebuilt from 16 slots.
- **Write-protection.** Mark calibration tables and `.rodata` RO (AP=5),
  and — the brick-saver — the flash controller's registers
  privileged-only, so unprivileged code cannot erase the firmware it runs
  (chapter 05 builds on this).
- **Guard regions.** The 32-byte no-go zone under each task stack: a
  region with AP=0 (`MPU_RASR = (4u << 1) | 1u`) makes the *first* stray
  write fault, faulting PC included in the exception frame. This is
  FreeRTOS's third stack-overflow check: `configCHECK_FOR_STACK_OVERFLOW`
  method 1 verifies the 0xA5A5A5A5 fill at the stack's low end at each
  context switch; method 2 compares the saved stack pointer against the
  bounds; the MPU guard is the precise method 3, catching between
  switches what sampling misses.

**What it cannot give you:** demand paging, copy-on-write, swap. There is
no fault-then-fill mechanism and nothing to fill *from* — flash is the
program, not a backing store. The error channel is the exception table
(chapter 02): an MPU violation raises a **MemManage fault** (status in
SCB->CFSR at 0xE000ED28), a wild address in a reserved hole a
**BusFault**, unhandled either escalates to **HardFault** — and the only
sane policy is log-and-reset (the watchdog reboots, chapter 03). Where an
MMU turns faults into progress, the MPU turns them into diagnoses.

## The RAM budget revolution

Re-derive every size at kilobyte scale. A Linux thread gets 8 MB of
virtual stack; a FreeRTOS **task stack is 256-1024 bytes** — the deepest
call chain plus worst-case interrupt nesting (an 8-word, 32-byte hardware
frame per level) must *fit* there, measured by pattern-fill or `-fstack-usage`. Six tasks
at 512 B is 3 KiB, a third of a small part.

The discipline is **static allocation**: every buffer is a global or
`static` array sized at link time, and `.bss` — zero-initialized globals,
no file bytes — *is* your allocation mechanism. "Allocation failure"
becomes a link error (chapter 02's `ASSERT`), caught by CI, not at 3 a.m.
The heap, if it exists, is a static pool behind newlib's `_sbrk` shim;
module 01 ch05's fragmentation math shrunk 1000x explains the ban — a
2 KiB hole in an 8 KiB pool wastes 25% of the machine. Dynamic loading
disappears with it: module 01 ch03's shared libraries and PLT/GOT have no
per-process space to load *into* — firmware is position-*fixed*, absolute
addresses burned in at link time.

Language-level consequences, with the numbers that matter:

- **C++ exceptions off**: `-fno-exceptions -fno-rtti` saves **~20-50 KiB**
  on a typical build (`.ARM.exidx` unwind tables alone run 2-4% of flash,
  plus personality routines and type info) — up to 10% of a 512 KiB part.
- **`printf` is a budget line.** Full newlib `printf` with `%f` pulls in
  `_dtoa` and malloc: **~4-12 KiB** — the folklore "hello world costs 8
  KB." The honest accounting is **newlib-nano** (`--specs=nano.specs`):
  integer-only ~1-2 KiB, floats opt-in via `-u _printf_float`. Teams
  write 30-line integer formatters because of this table.

Why so little RAM? Physics. SRAM's 6T cell (module 17 ch04) — six
transistors, no refresh, 0 wait states — costs ~50-100x DRAM's area per
bit: instant, refresh-free memory in exchange for kilobytes. Flash is
dense but *slow*: the F407 needs **wait states scaled to clock** — 0 at
≤30 MHz, 5 at 168 MHz — so a flash fetch can cost 6 cycles where SRAM
costs 1 (ST's ART cache hides much of it for linear code; branchy ISRs
feel it). Hence the **ramfunc trick**: copy hot code to SRAM at boot —

```c
__attribute__((section(".ramfunc")))   /* not a builtin: YOUR section,  */
void flash_erase_burn(void) { ... }    /* placed by YOUR linker script   */
                                       /* (.ramfunc >RAM AT> FLASH),     */
                                       /* copied like .data at startup   */
```

— the *same copy loop* that initializes `.data` moves the function's
bytes from its flash load address to its SRAM run address before `main`;
from then on it runs at zero wait states. Chapter 02 teaches this
mechanism properly.

Power closes the budget. Stop modes keep SRAM alive at µA-scale current
(1-10 µA on STM32L parts, tens on an F4) while killing core, PLLs, and
peripherals; standby goes lower by *killing SRAM*. State that must
survive — a state-machine enum, a counter, a CRC — lives in the VBAT
domain: the RTC and **20-80 bytes of backup registers** (80 B on an F407)
on a coin cell, the machine's whole persistence layer. Deep sleep here is
not "suspend-to-RAM with a kernel": the RAM is the only thing alive — and
if it isn't, the backup registers were.

## volatile is load-bearing

Every peripheral interaction is a load or store to this map. The idiom:

```c
volatile uint32_t *const UART_DR = (uint32_t *)0x4000C000u;
```

Read it right to left: `uint32_t` (bus width), `volatile` (the value
changes without your program writing — it's hardware), `const` (the
address never changes — it's silicon). Drop the middle word and the
compiler is *correct* to break you: the as-if rule permits caching a load
when no observable behavior changes, and the C abstract machine sees an
object only your code touches, so

```c
uint32_t *const SR = (uint32_t *)0x4000C000u;   /* missing volatile */
while (!(*SR & RXNE)) ;                          /* hangs at -O2 */
```

compiles to one load, hoisted out of the loop, then an infinite branch —
the flag flips in silicon, the loop never re-reads it. This is the *same
UB family as module 09 ch01's data races*: an object modified by an agent
outside the C abstract machine (another thread without synchronization,
a signal handler, DMA, the peripheral itself). The compiler's "nothing
else writes this" proof is false for a reason the machine model cannot
see; `volatile` declares the channel — the MMIO sibling of atomics. It is
*not* a memory barrier (ordering needs `dmb`/compiler barriers; DMA
coherency is chapter 03's problem).

The same family produces the classic **W1C lost-interrupt bug**. Status
registers are often write-1-to-clear — the ack is writing a 1 to the
handled bit. So:

```c
#define IRQ_STATUS  (*(volatile uint32_t *)0x40011000u)  /* write-1-clear */

void uart_irq_handler(void) {
    if (IRQ_STATUS & FLAG_UART_RX) {
        IRQ_STATUS |= FLAG_UART_RX;   /* BUG: read-modify-write ack */
        handle_rx();
    }
}
```

Expand `|=` by hand: **load** — under load it returns
`FLAG_UART_RX | FLAG_TIMER` (both pending); **or**; **store** — and every
1 written *clears* its flag. The store wrote both bits, so both cleared;
only one was handled: the timer interrupt is gone — precisely when the
two coincide. One statement, two flags consumed; the
side effect came from the *read* half. Correct ack:
`IRQ_STATUS = FLAG_UART_RX;`. The derivation exposes a sibling trap: on
read-to-clear registers, evaluating `IRQ_STATUS & FLAG_A` twice
(in the `if`, then the body) eats a flag per read — so a status
register is read **once into a local**, then tested. Debuggers violate
this for you: the memory window's refresh *is* a read, and flags
evaporate while you watch — the "bug disappears when I single-step"
signature.

## What insiders know

- **Holes fault, they don't read zero.** Touch a reserved region and you
  get a BusFault — often *imprecise*, reported some instructions after
  the offending access, so fault forensics can point one call too late.
  Per-SOC behavior varies: some decode gaps return garbage or the last
  bus value instead.
- **SRAM is executable out of the box.** The ARMv7-M default attribute
  map XN's only 0x40000000 and above; SRAM and flash stay executable
  until the MPU says otherwise. Vendors ship MPU examples; almost nobody
  enables them — module 02's 1996 economics persist by default, in
  silicon.
- **Errata sheets are required reading.** Silicon has bugs, documented
  per *revision*: ST's F4 errata is the famous example ("don't pair DMA
  stream X with peripheral Y," I2C flags that need a settle delay). Read
  DBGMCU_IDCODE (0xE0042000) for the revision; "add three NOPs after
  that enable" folklore is usually an errata, compressed.
- **The smart-peripheral era.** On modern M4/M7 parts the CPU is a
  minority busybody: UARTs carry 16-32 byte FIFOs with dedicated DMA
  engines, timers trigger ADCs whose samples DMA writes to memory, and
  peripheral-to-peripheral DMA paths move data without the core. "CPU
  load" is often 5% of the data movement.
- **Cortex-M vs Cortex-A is a memory-model commitment.** M profile: no
  MMU, hardware auto-saves 8 registers on interrupt entry (12-cycle
  latency, tail-chaining) — a µArchitecture *for* ISRs. A profile: MMU,
  caches, virtual memory — modules 05-07's world; R sits between. The
  "profile" taxonomy is ARM's market segmentation of this exact
  question.

## See it yourself

**Lab A (paper): read a real map.** Download ST's RM0090 (STM32F4
reference manual), section 2.3, and map a program onto the
bar: `.isr_vector`/`.text` → 0x08000000, `.data`/`.bss`/stacks →
0x20000000, USART2 → 0x40004400, NVIC → 0xE000E100, MPU → 0xE000ED90.
Then `xxd firmware.bin | head -1` on any STM32 image: the first 8 bytes
are the initial SP and reset vector, little-endian — an F407 with 128 KiB
RAM shows `0020 0200` (0x20020000) then a 0x0800xxxx entry. You are
reading the silicon's boot contract out of a file.

**Lab B (runnable anywhere with gcc): the sections chapter 02 will
place.**

```console
$ cat > tiny.c <<'EOF'
int counter;                 /* .bss  */
int limit = 100;             /* .data */
const char banner[] = "hi";  /* .rodata */
void tick(void) { counter++; }  /* .text */
int main(void) { tick(); return limit; }
EOF
$ gcc -O0 -o tiny tiny.c
$ readelf -SW tiny | grep -E ' \.text| \.rodata| \.data| \.bss'
  [14] .text       PROGBITS  0000000000001050 001050 00012b 00  AX ...
  [16] .rodata     PROGBITS  0000000000002000 002000 00000a 00   A ...
  [25] .data       PROGBITS  0000000000004008 003008 000014 00  WA ...
  [26] .bss        NOBITS    000000000000401c 00301c 00000c 00  WA ...
```

(Verified on gcc 14, x86-64, Debian 13.) `.text` is `AX` (executable),
`.rodata` `A` (read-only), `.data`/`.bss` `WA` (writable). The crucial
line is `NOBITS`: `.bss` occupies **zero file bytes** — someone must zero
it at runtime, and chapter 02 shows that someone is *your* startup code.
On the laptop the addresses are PIE-randomized at load; on a µC the same
sections land at fixed flash/SRAM addresses, placed by the linker
script.

**Lab C (if the toolchain exists): QEMU a Cortex-M4.** With
`qemu-system-arm` and `arm-none-eabi-gcc`:

```console
$ arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -specs=rdimon.specs \
    -nostartfiles -Ttext=0 -Wl,-e=main hello.c -o hello.elf
$ qemu-system-arm -M mps2-an385 -cpu cortex-m4 -nographic -kernel hello.elf \
    -semihosting-config enable=on,target=native
Hello from the no-MMU world
```

where `hello.c` is a `printf` plus `for(;;)`. The mps2-an385's map is
this chapter's bar; semihosting is QEMU's stand-in for a UART. If the
tools are absent, Labs A-B carry the chapter — the map is identical
either way.

## How to use this knowledge

- **Day one on embedded code**: open the linker script and `.map` file
  before the source — RAM budget, stack sizes, whether anyone enabled the
  MPU, readable in five minutes before a line of logic.
- **Crash triage**: a hard-faulting µC is readable if you know the bar —
  SCB->CFSR at 0xE000ED28 says MemManage vs BusFault, the stacked frame
  gives the faulting PC, the address names the hole or region.
- **Security review**: firmware parsing remote input (UART, BLE, CAN)
  with no MPU regions is module 02's 1996 target at a fixed
  address. Recommend: XN on SRAM, unprivileged task walls,
  write-protected config — three regions, ~20 lines.
- **Interview answers**: "why is there no ASLR on a µC?" and "what does
  the MPU give up versus an MMU?" separate memorizers from people who
  can re-derive what virtual memory was *for*.

## Connections

- **Chapter 02 (same module)**: the linker script is this map as a source
  file; the `.ramfunc`/`.data` copy loop is its mechanism; the vector
  table's first two words are Lab A's boot contract.
- **Chapter 03 (same module)**: the NVIC at 0xE000E100 becomes an
  entry-point table; the volatile/W1C traps return with hardware as the
  writer.
- **Chapter 04 (same module)**: the RTOS — task stacks, MPU region
  swapping as "page-table switching," `configCHECK_FOR_STACK_OVERFLOW`
  in production.
- **Chapter 05 (same module)**: fixed addresses make firmware extraction
  trivial; secure boot, flash write-protection, and debug-port locking
  are this world's defenses.
- **Module 01 ch03 (virtual memory — removed)**: reread the page-table
  walk and check off what this world deleted; the MPU is
  the surviving fragment.
- **Module 01 ch05 (heap — absent)**: the fragmentation math, shrunk
  1000x, is why static allocation is discipline, not preference.
- **Module 17 ch03 (peripherals as RTL)**: the 0x4000xxxx words are RTL
  modules on a bus; module 17 ch04's 6T cell is why SRAM is small and
  fast.
- **Module 02 ch02 (stack exploitation)**: the attack economics without
  ASLR — zero entropy, executable SRAM, one XN bit as DEP.
- **Module 09 ch01 (happens-before)**: `volatile` as the MMIO member of
  the data-race family — same UB, same fix shape: declare the writer.

## Quiz

1. **Concept.** "This chip has no ASLR" is a category error, not a
   missing feature. What exactly is absent that ASLR would need — and
   what would you have to add to randomize anything?
2. **Concept.** A cached MMIO read in a polling loop is UB in the same
   *family* as module 09's data races. Name the family, explain why the
   optimization is legal inside the C abstract machine, and what
   `volatile` declares.
3. **Derive.** Program an MPU region covering a 1 KiB task
   stack at 0x20001000: read-write, execute-never, enabled, no
   subregion disables. Compute the exact `MPU_RASR` word (hex), state
   the base's alignment constraint, and say which register writes come
   first.
4. **Derive (budget).** An 8 KiB SRAM part must hold: `.data` 1 KiB,
   `.bss` 4 KiB, six 512 B task stacks, a 1.5 KiB main+ISR
   stack, and a static message pool. Does a 2 KiB pool fit? If not,
   give two levers and their costs.
5. **Predict (layout).** From Lab B's program: which of `counter`,
   `limit`, `banner`, `tick` occupy flash bytes in the image, and how
   many bytes does each cost in the file? What changes for these four
   on a µC build?
6. **Diagnose.** A product's timer interrupts vanish — but only under
   heavy UART traffic. The ISR contains
   `IRQ_STATUS |= FLAG_UART_RX;`. Derive the loss mechanism and write
   the correct line.
7. **Diagnose.** This poll hangs forever at `-O2`, works at `-O0`:
   `uint32_t *const SR = (uint32_t *)0x4000C000; while (!(*SR & RXNE));`
   What did the compiler do, why is it allowed, and what is the
   one-word fix plus its limits?
8. **Cross-chapter.** Replay module 02 ch02's 64-byte-buffer/256-byte-
   read overflow on a bare Cortex-M4, default memory map, no MPU
   configuration. Which of module 02's three defenses (canary,
   ASLR, NX) still stands, and what is the cheapest silicon
   countermeasure this chapter teaches?

<details>
<summary>Answers</summary>

1. **The VA→PA mapping is absent.** ASLR randomizes where the loader
   places images in a *virtual* space, then points page tables at the
   result; with no MMU there is one physical space wired by the decoder,
   addresses fixed by the linker and burned into flash. Randomizing would
   need a relocating loader, but firmware is position-*fixed* — absolute
   addresses compiled in, no relocation metadata. Entropy is zero; spend
   the effort on chapter 05's flash locks.

2. **The family is "object modified outside the C abstract machine."**
   The as-if rule lets the compiler cache a load when no *observable*
   behavior can change; the abstract machine sees an object only this
   code touches, so hoisting is legal — the proof is false only because a
   peripheral (or DMA, or another thread) also writes it, which the
   machine model cannot express. A data race is the same false proof
   with a thread as the hidden writer. `volatile` declares the
   modification channel (as atomics declare the concurrent one); it does
   *not* order other accesses — that needs barriers (chapter 03).

3. 1 KiB = 2^10, so SIZE = 10-1 = 9; XN (bit 28) = 0x10000000; AP=3
   (RW any privilege) = 0x03000000; SRD=0; ENABLE=1. Sum:
   **0x13000013**. The base must be size-aligned — 0x20001000 is
   1 KiB-aligned, valid. Before it: `MPU_RNR` (region
   number) and `MPU_RBAR = 0x20001000`; after it: `MPU_CTRL`, then
   `dsb; isb` so it applies before the next fetch.

4. Sum: 1024 + 4096 + 3072 + 1536 = 9728 B, so
   8192 - 9728 = **-1536 B: the 2 KiB pool does not fit; you are 1.5
   KiB over.** Levers: cut task stacks to 384 B (saves 768 B, re-verify
   worst-case nesting) and shrink the pool to 512 B (saves
   1536 B, the protocol must chunk messages). The *process* answer:
   chapter 02's linker `ASSERT` should have refused the link —
   failure is a build event here.

5. Flash-file bytes: `tick` → `.text` (a handful);
   `limit` → `.data` (4 file bytes — the initial value travels in
   flash, 4 RAM bytes at runtime); `banner` → `.rodata` (3 bytes:
   "hi" + NUL, flash only); `counter` → `.bss`, **zero file bytes**
   (NOBITS) — startup zeroes its 4 RAM bytes. On a µC the same sections
   sit at fixed addresses, `.data`'s load address in flash and run
   address in RAM — the LMA/VMA split chapter 02 teaches.

6. **The read half consumed the second flag.** `|=` is load → or →
   store: the load returns `FLAG_UART_RX | FLAG_TIMER` (both pending —
   "under load"); the OR keeps both set; the store writes 1s,
   and W1C clears *every* written 1 — both flags — while only the UART
   bit is handled. The timer's pending bit dies unhandled: a lost
   interrupt, correlated with load. Correct ack:
   `IRQ_STATUS = FLAG_UART_RX;` — never a read-modify-write. (On
   read-to-clear registers even the `if`'s read eats flags: read once
   into a local, then test bits.)

7. **The load was hoisted.** At `-O2` the compiler emits one `ldr`,
   tests it, and loops on the cached register — legal by the as-if
   rule, since the abstract machine has no other writer; the flag flips
   in hardware after the single read, so the loop spins on a stale zero.
   The fix is `volatile` on the pointee
   (`volatile uint32_t *const SR`). Limits: `volatile` forces re-reads
   but grants no atomicity and no ordering of *other* accesses —
   DMA-visible buffers still need barriers (chapter 03).

8. **Only the canary could stand — and typically isn't there.**
   No ASLR: the return target is fixed in the image, zero entropy, no
   leak needed — module 02's costliest step vanishes. No NX: the default
   map leaves SRAM executable, so injected stack bytes run directly — no
   ROP detour even needed. Canaries exist on ARM (`-fstack-protector`)
   but vendor builds usually omit them, and without ASLR a leaked canary
   is replayable forever. Cheapest countermeasure: one MPU region — the
   stack as RW+XN (`0x1300000F` for 256 B) — faults the payload at first
   fetch; add unprivileged task walls and most of module 02's defense
   budget returns for a dozen lines.

</details>
