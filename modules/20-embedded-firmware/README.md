# Module 20 — Embedded & firmware: the world with no MMU

> Everything this program has taught you about memory — page tables,
> ASLR, swap, the heap-versus-stack split, the OS standing between
> processes — is a luxury. There is a larger population of computers
> than servers, phones, and laptops combined, and none of them have a
> page table: your car has ~100 of them, a modern washing machine has
> several, every power meter, keyboard, SSD controller, and earbud.
> This is the module where the abstractions run out: one address space,
> physical addresses visible to everyone, the stack pointer initialized
> by *data in a file*, the "heap" a static array if it exists at all,
> and the difference between a correct program and a brick being one
> `const` keyword. Module 01's questions asked with the safety net
> removed — and the security story of modules 02, 05, and 06 re-asked
> at the layer that bootstraps all of them: firmware.

**5 chapters · one final exam · prerequisite: module 01 chapters 04
(the stack — who initializes it here), 05 (the heap — mostly absent),
03 (virtual memory — the thing that's missing, and the MPU that
replaces it), 07 (the OS — replaced by a scheduler loop); modules 05-06
(mobile) are the silicon-rich cousins; module 17 ch03-04 (RTL) is
what the peripherals actually are — referenced, not required.**

## How to study it

Chapter 01 establishes the world: no MMU, the memory map as a *data
structure wired into silicon*, the MPU's region permissions as the
only protection, and how cheap RAM reshapes every decision; 02 is
bare-metal bring-up — the reset vector, the boot ROM, linker scripts
as memory's source of truth, `.data`/`.bss`/`.text` initialization,
and the startup code you must write before `main`; 03 is interrupts
and DMA — the asynchronous memory machinery, written by hand, with
the exact race conditions that module 09 proved can't be avoided;
04 is the RTOS layer — FreeRTOS as the minimal scheduler, its task
stacks, priority inversion returning from module 03, and why "the
heap" is a config flag; 05 is firmware security — secure boot as a
chain of proofs in flash, key storage in silicon, firmware updates
as atomic-bank swaps, and the attack surface of everything above it.
Labs: QEMU emulating an ARM microcontroller (`qemu-system-arm -M
mps2-an385` or similar — fully free, no hardware), plus real
cross-compilers if available (`arm-none-eabi-gcc`), with the
Raspberry Pi Pico / nRF52 / STM32 as the named real targets; every
QEMU lab must actually run and show real output.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The memory map is wired into silicon](chapters/01-no-mmu-world.md) | No page tables, physical addresses, the MPU's 8-16 regions, RAM budgets measured in kilobytes — the world the abstractions forgot. |
| 02 | [Bare metal: linker scripts are the memory model](chapters/02-bare-metal-linker.md) | Reset vector, boot ROM, `.text`/`.data`/`.bss`, the startup code before `main` — memory as a source file. |
| 03 | [Interrupts and DMA: the asynchronous memory](chapters/03-interrupts-dma.md) | NVIC priorities, ISRs as entry points, shared-memory races with hardware, DMA stealing your RAM — concurrency without an OS. |
| 04 | [The RTOS: a scheduler in 10 KB](chapters/04-rtos-memory.md) | FreeRTOS task stacks, priority inheritance, the heap as a config flag, stack-overflow checking — an OS scaled to its floor. |
| 05 | [Firmware security: the root of the chain](chapters/05-firmware-security.md) | Secure boot as flash-resident proofs, keys in OTP/efuses, atomic A/B updates, debug ports as the real backdoor — where modules 02/05/06's stories begin. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the no-MMU world.** Open with the inventory: a
$0.50-5 microcontroller (say a Cortex-M0+ at 48-133 MHz) has
2-128 KiB of RAM, 16-512 KiB of flash, no MMU, no cache (or a small
one), no OS — and ships by the *billions per year* (the honest
market numbers: ~30 billion MCUs/year vs ~300M PCs/phones — the
inversion of the world you learned). The memory map as a
**hardware data structure**: draw a real one (e.g. STM32
F4-series: 0x00000000 flash alias, 0x08000000 flash, 0x20000000
SRAM, 0x40000000 peripherals, 0xE0000000 the ARM system region with
the NVIC and SysTick — the full map drawn as an address bar with
regions annotated, this diagram is mandatory); the *fixedness* is
the feature: no ASLR because no virtualization — an address IS
the thing (connect module 01 ch03: the entire indirection layer
removed; module 02's ASLR discussion becomes meaningless — the
attack economics change, the *return-oriented* attacker needs no
info leak). The **MPU** as the page table's poor cousin: 8 or 16
regions (Cortex-M: MPU regions with base/size/attributes, 32-byte
granularity minimum, XN (execute-never) bits, RW/R/privileged-only
— the programming model shown as the real register writes:
`MPU_RNR`, `MPU_RBAS`, `MPU_RASR` with one worked configuration
(the stack region as RW+XN — and why executing from the stack
is the buffer-overflow story of module 02 replayed with a *1-bit
defense* instead of DEP+ASLR); what the MPU gives you (per-task
walls in an RTOS, write-protecting flash-resident config,
guard regions for stack-overflow detection — the 32-byte no-go
zone under each task stack, FreeRTOS's `configCHECK_FOR_STACK_
OVERFLOW` mechanism) and what it can't (no demand paging, no
COW, no swap — a "page fault" is a bus fault / hard fault: the
exception table as the error channel). **The RAM budget
revolution**: sizes re-derived at kB scale (a task stack is
256-1024 bytes, not 8 MB; the heap is optional; the *static*
allocation discipline: everything sized at link time — the
`.bss` section as your only "allocation"; the language-level
consequences: no dynamic loading (module 01 ch03's shared
libraries gone), C++ exceptions often disabled (`-fno-
exceptions` and its ~20-50 KB savings — the number matters),
the printf-family's ~4-12 KB (and tiny-printf / embedded
newlib-nano's honest accounting — the "hello world costs
8 KB" fact). SRAM's structure: the bit-cell from module 17
ch04 returns (6T SRAM needs no refresh — no DRAM refresh tax
on a µC; wait states and the flash prefetch buffer (flash at
0-5 wait states vs SRAM's 0 — the function-in-RAM trick:
copying hot code to SRAM at boot, `__attribute__((section
(".ramfunc")))` with the section copy performed by startup
code — the mechanism must be shown, it's ch02's bridge).
The clock-tree-to-power honesty: stop modes and the RAM
retention trade (deep sleep keeps SRAM at µA, kills
everything else — the state that must live in 20 bytes of
backup registers; the RTC + VBAT domain as the "persistence
layer"). Volatile and pointers as the load-bearing syntax:
every peripheral register is memory-mapped ( `volatile
uint32_t *const UART_DR = (uint32_t *)0x4000C000;` — why
volatile is *mandatory* (the compiler's cache of a value in a
register = the same UB family as module 09's data race: the
object is being modified by something outside the C abstract
machine — the DMA case is ch03's core); reading a
write-1-to-clear register twice (the classic W1C bug — a
single `if ((IRQ_STATUS & FLAG))` read consumed TWO flags,
derive the lost-interrupt bug from the side effect of the
read). Insiders: the address space's "holes" (reading a
reserved region = bus fault, not zero — the zero vs fault
per-SOC behavior; errata sheets as required reading (the
silicon bug lists — ST's errata as the famous example, the
"don't use DMA channel X with Y" folklore); the "smart
peripheral" era (the DMA-driven peripherals of modern
 Cortex-M4/M7 — UARTs with FIFOs and DMA engines
standard; the peripheral-to-peripheral DMA path that skips
the core entirely); the Cortex-M vs Cortex-A line drawn in
one paragraph (M = no MMU, µArch for ISRs; A = the MMU world
of modules 05-07 — the "profile" taxonomy as ARM's market
segmentation). Lab: QEMU an MPS2 board (`qemu-system-arm -M
mps2-an385 -kernel` a provided minimal binary OR use the
semihosting trick to run a program you compile with
`arm-none-eabi-gcc` if the toolchain is installed — the lab
must provide the exact command lines and expected output;
if the cross-compiler is unavailable, the paper lab: read
the STM32F4 reference manual's memory map page and map every
region to the program's sections); `readelf -S` on any ELF
to see the sections that ch02 will place (the lab plants
ch02).

**Chapter 02 — bare metal.** The central claim: on a µC the memory
model is *a source file* — the linker script. The boot sequence
derived end to end (the sequence IS the chapter's spine, drawn as
a 12-step timeline diagram): (1) power-on, POR circuit holds
core in reset; (2) the boot ROM (mask ROM, immutable, a few KB
— on STM32 it maps flash at 0x00000000; on the Pico: the 252-byte
boot2 stage that hand-configures SSI timings to even *read* the
external flash — the chicken-and-egg of executing from the memory
you must first configure, told properly); (3) hardware loads
MSP from address 0x00000000 (the first 4 bytes of the vector
table — **the stack pointer is data in a file**: derive that
`_estack` in the linker script is what the CPU reads, this is
the module's most disorienting fact and must be worked slowly);
(4) hardware loads the reset vector from 0x00000004 and jumps
(no privilege change, no mode switch — contrast the x86
real-mode saga of module 07); (5) `Reset_Handler` (in startup_*.s)
: copy `.data` from flash to SRAM (the initialized data's two
addresses — LMA vs VMA, the load address in flash vs run address
in RAM: draw the two views of the same section side by side,
this diagram is mandatory), (6) zero `.bss` (why C guarantees
zeroed globals — someone has to zero them, and it's you: the
loop shown in real assembly from the ST startup file); (7)
`SystemInit` (clocks, and the flash wait-states from ch01);
(8) `__libc_init_array` (C++ static constructors — the
 `.preinit_array`/`.init_array` walk; why C++ globals are a
boot-time cost); (9) `main` — at last. The linker script taught
as a real language (walk a ~30-line script from a real
 STM32 project: MEMORY{} (FLASH rx 0x8000000, RAM rwx
0x20000000 — the *linker script is where the memory map of
ch01 is written down*), SECTIONS{}: `.isr_vector` at flash
start (KEEP — why garbage collection would otherwise drop
your vector table: the linker doesn't know hardware reads it),
`.text`, `.rodata`, `.ARM.exidx` (the unwind tables — 2-4% of
flash, the C++-exceptions-off savings quantified), `.data`
 AT> FLASH (the two-address trick: `>RAM AT>FLASH` with
`_sidata = LOADADDR(.data)` — each line explained), `.bss`
(NOLOAD), `._user_heap_stack` (the fake section that *reserves*
stack+heap at link time so the .map file can prove they fit —
the `ASSERT(_estack - _sheap <= RAM_SIZE)` pattern: **the
linker script as a static analyzer**, the compile-time proof
that RAM will not be oversubscribed — this is the chapter's
payoff paragraph: an ASSERT in the linker = module 19's
soundness theorem at the kilobyte scale). The vector table
as a C array (`__attribute__((section(".isr_vector")))`
function-pointer array — the 16 system entries + the device
IRQs; the *weak alias* pattern (`Default_Handler` as the
weak default for every unused IRQ — how the startup .s does
it, and why "forgotten handler = silent hang" is the default
); relocation and position-independence honesty (why µC code
 is position-FIXED: no PIC needed, absolute addresses burned
in — contrast module 04 ch02's loader (PLT/GOT) existing
precisely because of dynamic loading; the cost table: PIC
overhead ~0-3% vs absolute). The `.map` file as the
budget document (read one: every object's contribution,
the per-function sizes — `arm-none-eabi-nm --size-sort`
's top-10 offenders; `-Wl,-Map=out.map -Wl--gc-sections`
and the `-ffunction-sections` pairing; the discipline: no
"it fit yesterday" — the map file is reviewed in PRs).
Stack discipline without an OS (MSP vs PSP: the two stack
pointers — handler mode always uses MSP, thread mode can
use PSP: the RTOS trick of ch04 planted (each task gets its
own PSP-based stack while ISRs share the MSP — the split
diagram, mandatory); main-stack sizing without a guard
 (the honest methods: fill with a pattern 0xDEADBEEF and
 measure post-run, the worst-case interrupt nesting
arithmetic: 8-word frame × nesting depth + the deepest
call tree — the two numbers that must be added). Heap
reality: `malloc` on a µC = a static pool (newlib's
 `_sbrk` shim you must write — 15 lines, shown; the
malloc-is-banned style guides: MISRA/static-allocation
cultures, why (fragmentation at kB scale is fatal,
allocation failure is a *design* error not a runtime
event — connect module 01 ch05's fragmentation math
shrunk 1000×). Insiders: the "vector table relocation"
trap (VTOR: moving the table to RAM to patch entries at
runtime — the bootloader's trick, and the errata that
bit some cores); the `__attribute__((interrupt))` vs
bare functions (the M-profile automatic state save —
x86/PUSH-all contrast from module 01 ch04: the hardware
saves 8 registers for you, the ISR's prologue is nearly
free — the architecture designed *for* interrupts);
noinline/naked functions and the stack-usage
`-fstack-usage` + `-Wl,--print-memory-usage` pairing
as the CI guards; the "it's in flash, can't debug it"
(JTAG/SWD's flash patch: the FPB comparators' hardware
breakpoints that don't modify flash — 2-6 of them,
the scarcity story); the SDRAM-on-a-Pi trick of external
RAM bring-up (init before use, the heap lives there but
 the startup code can't — the two-stage boot for RAM).
Lab: (QEMU mandatory if toolchain present, else paper)
 compile a minimal program (vector table + reset handler
that zeroes .bss, copies .data, blinks via a memory-mapped
 GPIO in the MPS2 emulator — the full 60-line listing
provided in the chapter, the exact qemu command, the
expected semihosting/UART output "alive"); read the .map
and compute: total flash, total RAM, headroom; break the
budget on purpose (add a 100 KB array, watch the linker
ASSERT fail — the error message quoted verbatim; the
"soundness violation" caught at link time, module 19's
thesis demonstrated in 5 seconds); pattern-fill a stack
in QEMU (or paper: the 0xDEADBEEF technique written up
as a procedure).

**Chapter 03 — interrupts and DMA.** The asynchronous machinery,
written with no scheduler underneath. The interrupt as a
hardware callback: the signal path (device raises IRQ line →
NVIC (the nested vectored interrupt controller: the table of
pending/enable/active bits and the *priority* registers
(NVIC_IPR, the 3-8 priority bits actually implemented on
common cores — the number matters: 16 priority levels on
M4, and the sub-priority split), the core's exception entry
(auto-stacking 8 words: xPSR, PC, LR, r0-r3, r12 — the
frame layout drawn with the EXC_RETURN magic in LR (0xFFFFFFF9/
FFFFFFFD: the value that tells the CPU which stack to return
to, the PSP/MSP switch of ch02 activated here), tail-chaining
and late-arriving interrupts (the hardware back-to-back
optimizations — 12-cycle entry vs the re-stack cost saved,
one diagram); interrupt latency defined and bounded (the
deterministic 12 cycles + the *disabled-window* worst case
(your code's `cpsid i` sections; the "never in an ISR"
rules derived from the priority mechanism: the priority
inversion of NVIC (a low-priority handler holding a lock
that a high-priority IRQ needs — the mask-based BASEPRI
solution: raising the ceiling (the PCP pattern of module
09 ch02 returning with a hardware register instead of a
scheduler: **the priority-ceiling protocol IS the BASEPRI
write — derive this correspondence explicitly, it's the
module's proof moment**). **The shared-memory contract**:
the volatile rules made precise (the C memory model says
ISRs and main are *not threads* — the only synchronization
guarantees are the ones you build: the memory barrier
instruction (`DMB` on Cortex-M, and why the volatile-
qualified access orders *within* a single ISR but NOT
device-to-core — the ARM architecture reference's rule
that device accesses are order-preserving but the
compiler/CPU may reorder around them; when `__asm
volatile("dmb")` is genuinely required (the interrupt
enable/disable window), and the *double-checked*
pattern that ISN'T (volatile read, decide, act — the
TOCTOU of module 03 ch04 returning at register scale).
The single-writer principle as the design law (structure
your driver so each shared variable has exactly one
writer — ISR-owns or main-owns; the ring buffer as the
canonical artifact (the 16-line lock-free SPSC queue
(the same structure as module 13 ch01's DMA ring and
io_uring — say so and draw the shared head/tail: one
side writes each index, the SPSC safety proof from
module 09 ch03 re-applied at register scale), the
misfire that kills it (32-bit-aligned single-word
atomicity on M0 (no ldrex/strex — 32-bit aligned
load/store is atomic, 64-bit isn't (the double-word
trap on time values — the 2-tick clock tear bug
worked as an example), M3+ has ldrex/strex (the
LL/SC of module 09 returning — and the Thumb-1 vs
Thumb-2 instruction availability table). **DMA** as the
second CPU you didn't budget for: the controller model
( channels, descriptors or registers (source, dest,
count, config: the 4-register programming model — a
real one (STM32 DMA/DMAMUX or the PL230 in the
MPS2) configured in code, the transaction lifecycle
armed→busy→done→ISR), the memory-side obligations:
**cache coherence** (M7's D-cache vs DMA: the invalidate-
before-receive / clean-before-send discipline — the
`SCB_InvalidateDCache_by_Addr` calls, the cache line
rounding trap (a buffer not line-aligned: the
invalidate that destroys adjacent live data — the
32-byte boundary arithmetic, this bug class is
mandatory with a worked example); the **physical
address** requirement (no MMU = no translation, but
with an MPU+IOMMU-class protection (the SAU/TZ-MPUs)
 the address you hand DMA must be the bus address —
the identity map that makes this trivial and the
 corner cases that don't), and the stack-allocated-
buffer catastrophe (DMA still writing into a stack
frame that returned — the lifetime bug of module 01
reborn: a scatter-gather chain pointing at an
automatic variable; the "heap is banned" rule extends
to "stack is banned" — static or pool buffers, and
why (the ownership diagram: CPU hands buffer to DMA,
CPU must not touch it until the transfer-complete
ISR — a borrowing rule, module 19 ch03's `&mut`
re-appearing as hardware discipline). The
double-buffering and ping-pong patterns (the
schedule-ahead pattern (arm the next transfer while
the current runs — the one-buffer-deep pipeline,
the audio glitch arithmetic if you fail: 44.1 kHz ×
2 ch × 2 B = 176.4 KB/s, a 20 ms ISR jitter = miss
the window — the real-time definition: correctness
= function + timing (the hard/soft real-time
distinction drawn on consequence axes). The bus
matrix contention honesty (CPU vs DMA on the same
SRAM bank: the stall cycles, the split-bank RAM
layouts of bigger MCUs (the SRAM1/SRAM2 banking on
STM32H7, placing DMA buffers in the "other" bank —
the memory map of ch01 used as a performance tool).
Insiders: the priority-group split trick (NVIC
 priority grouping: preempt vs sub — the
 grouping-register footgun where ALL interrupts
became sub-priority-equal (the "no preemption all
of a sudden" mystery, a real support-forum classic)
; the WFI loop (the idle pattern: `__WFI()` sleeps
until any interrupt — the power story of ch01's
stop-modes made concrete; the event register and
SEVONPEND (wake on pending-without-executing — the
trick for "wake but don't run the handler"));
semihosting's exit (the QEMU exit trick: BKPT 0xAB
— how labs end cleanly, the actual instruction
shown); the "IRQn = 0 is not EXTI0" (the offset
between peripheral numbers and NVIC slots on every
family's headers — the -16 offset for system
exceptions, the STM32 EXTI mapping folklore); the
SysTick as the 1 ms heartbeat (the 24-bit reload
arithmetic: 72 MHz core → reload 71999 for 1 ms —
do the arithmetic inline; the HAL tick and why
HAL_Delay in an ISR is a deadlock (the tick can't
preempt its own consumer at lower priority — derive
 the inversion). Lab (QEMU): enable a timer
interrupt (SysTick) with a 3-line handler + a main
loop counting (the program from ch02 extended —
the listing provided, the observed blink/print
pattern: "main loop 1000 iterations per tick");
build the SPSC ring in C and stress it (the
testing harness that runs the producer in a timer
ISR at random intervals, the consumer in main —
run it in QEMU for 10^6 ops, zero corruption; then
 break it on purpose: swap the head/tail update
order and watch the over-write bug appear (the
manifestation documented); the DMA paper-lab: the
cache-line misalignment bug written up (the 20-line
M7 example with the call, the bug, the fix), plus
the audio-bandwidth arithmetic as an exercise with
answers.

**Chapter 04 — the RTOS.** What 10 KB of flash buys you: a
scheduler, and the memory story of it. The cooperative vs
preemptive decision derived (cooperative: yield points in the
code, latency = longest task section (derive a real worst case);
preemptive: SysTick quantum 1-10 ms (the context switch cost
accounted honestly: the PendSV handler's ~100-200 cycles on M4
(the lazy stacking of FPU registers — the EXC_RETURN bit 4 (the
FP context save that costs 17-33 words extra; `CONTROL.FPCA` —
the 2-cycle cost when no FP is used, the 100+ when it is — the
actual RTOS tick-rate trade: 1 kHz switch rate × 150 cycles =
0.015% CPU — the number that makes preemption affordable, do it
inline). The FreeRTOS task as a stack plus a TCB (task creation
with a real call: `xTaskCreate(vTask, "name", 128, args, prio,
NULL)` — decode every argument: the 128 = *words* not bytes (the
512-byte stack on a 32-bit core — the unit trap that has caught
everyone; stack-overflow checking (the two modes: method 1 the
pattern-fill canary at the end (the 0xA5A5A5A5 check on context
switch), method 2 the MPU guard region of ch01 — the comparison
table: detection latency vs detection certainty, the ~1% RAM
cost); `configMINIMAL_STACK_SIZE`, `configTOTAL_HEAP_SIZE`
(the FreeRTOS heap_4 as the chosen allocator (a first-fit
coalescing free list — module 01 ch19's dlmalloc lineage shrunk:
the ~200-line source as the readable allocator, why heap_4
(supports free, coalesces) vs heap_1 (never frees — the
"allocation is bring-up only" discipline: the two philosophies
of embedded memory, stated as a choice with consequences); the
idle task and the priority guarantee (idle priority 0, the
`vApplicationIdleHook` as the WFI home — the power pattern
completing ch03's WFI loop). **The RTOS primitives as memory
structures**: the queue as the SPSC ring generalized (MPMC with
a blocking layer: the event list per queue — the send/recv
paths drawn as linked-list surgery (the blocked-task list:
priority-ordered, the unblock-tail chain — the same structure
as the wait queue of module 07 ch04 (Linux's wait queues) — one
paragraph making the connection explicit: an RTOS queue =
Linux's waitqueue+ring fused into 40 lines); the priority
inheritance implemented (mutex with `uxPriorityCeiling`, the
 owner temporarily elevated — the inversion scenario of
module 03 ch05 (Mars Pathfinder as the canonical story,
 retold with the RTOS mechanism that fixes it (VxWorks'
 `t_priorityBoost` folklore) and the failure it doesn't fix
(deadlock — the acquisition-order discipline, module 09's
lock-ordering rule at µC scale); the software timer (the
timer daemon task's queue — all timer callbacks run in ONE
task context: the "don't block in a timer callback" rule
derived from the serialization (every callback delayed by
every other). The scheduler as O(1)-ish list surgery (the
 priority bit-map: the 32 tasks-per-word CTZ (count-trailing-
zeros) instruction's role — the `CLZ` hardware instruction
accelerating "highest priority ready task" to one cycle
(module 17 ch03's RTL returning: this is the instruction that
justifies the ISA's odd corner; the ready-list array of
 one list per priority — the same shape as Linux's O(1)
runqueue of module 07 ch04 — draw both, name the lineage
("the RTOS got there first")). Scheduling guarantees honestly:
 rate-monotonic analysis in one page (the utilization bound
for n harmonic tasks: U ≤ n(2^{1/n}−1) → ln 2 ≈ 0.693 (derive
the bound's logic in 5 lines, apply it: 3 periodic tasks at
50/20/10 ms with measured WCETs — the schedulability verdict
computed, the miss scenario constructed (what deadline is
missed first and when — the failure demonstrated with a
timeline diagram); the "RTOS is not real-time by itself"
warning (only analysis makes it so — the vendor claim vs the
engineering reality). ISR-to-task communication (the
`FromISR` API family: `xQueueSendFromISR` with the
`pxHigherPriorityTaskWoken` mechanism (the PendSV deferral
chain: ISR marks, scheduler switches at exit — the "do minimal
work in ISR" rule made structural, the unblocking latency
arithmetic: deferred = 1 context switch ~150 cycles vs the
in-ISR cost of the full call (the numbers compared); the
`portYIELD_FROM_ISR` vs return-without-yield as the latency
knob. The MPU tasks (FreeRTOS-MPU: unprivileged tasks with
 per-task regions — the 8-region scarcity arithmetic from ch01
applied: 3-4 fixed global regions + 2-3 per-task regions = a
task switch that rewrites MPU registers (the switch cost, the
protection gained: the containment story (one buggy task
can't corrupt another's stack — the µC's version of module 15's
containers, ~1 KB of protection instead of a VM: draw the
comparison table (MMU page table vs MPU rewrite-per-switch:
the granularity/cost trade at 10^2× different scale — same
idea, this is the program's thesis repeating). Insiders: the
tickless idle (SUPPRESS the SysTick for power: the wake-on-
RTC arithmetic — the µA-years math of battery devices;
`configUSE_TICKLESS_IDLE`'s renegotiation of the time source);
the stack high-water mark (`uxTaskGetStackHighWaterMark` —
the 90%-full discipline for shipping (the "why 128 words"
becomes measured, not guessed); the abort/deferred-interrupt
trap (the ISR that calls the non-FromISR API — the silent
corruption (or configASSERT catch — the debugging story
every embedded engineer has); ISR stack sizing revisited
(MSP shared by all ISRs + nesting: the arithmetic 8 words ×
(1 + max nesting) + deepest ISR frames — the number most
teams get wrong; the `configMAX_SYSCALL_INTERRUPT_PRIORITY`
fence (ISRs above it may NOT call the kernel — the
mechanism is a BASEPRI mask, the bug is calling from a
too-high-priority ISR: the corrupted-list failure mode
worked as an example, this is the module's nastiest real
bug class). Lab (QEMU): run FreeRTOS in the MPS2 (the
port's bring-up is real: provide the 5 config lines and
the tick rate; two tasks at different priorities + the
timer, print the switch pattern (the output annotated);
build the priority-inversion demo (three tasks, low
holds mutex, high preempts and blocks, medium starves
low — enable `configUSE_MUTEXES` inheritance and show
the boost in the trace; disable it and show the
starvation (the two outputs compared = the deliverable);
overflow a task stack on purpose (method-1 canary fires:
the configASSERT backtrace shown — the "it caught it"
moment); RMA on paper: the 3-task analysis computed with
the verdict and the timeline drawn.

**Chapter 05 — firmware security.** The chain of trust from a
cold boot to your encrypted traffic — the root of modules 02/05/06
's stories (the mobile secure-boot chains of module 05/06 are this
chapter's silicon-rich descendants — say it explicitly: every
"Trusted Boot" on a phone is the pattern here, scaled up). The
threat model for things with physical access: the attacker has
the board (probing, glitching, decapping — the cost ladder:
snooping a UART ($10), fault injection ($1-10 k), chip-level
microscopy ($100 k+) — the economics table that decides what
to defend). Secure boot derived as chained signatures over
memory: the immutable root (mask ROM or OTP-fused key hash —
the "you can't change it" property as the anchor), each stage
verifies the next (signature over the image + its metadata
(the version anti-rollback counters in OTP/eFuse — the
arithmetic of state: one-way counters, the downgrade attack
it kills); the measurement chain = a hash chain (each stage
 appends the next's hash before executing — the same
structure as TLS's transcript hash of module 18 ch04 and
the Merkle journal of module 14 ch04 — one paragraph
naming all three: append-only verification is a *pattern*,
the program's thesis again). Key storage: the options table
(OTP/eFuse bits (burned, ~32-256 bytes, the readout
protections), secure elements (an I²C chip holding keys
doing sign-in-place (the ATECC608 class), TrustZone-M
(separating secure/non-secure worlds via the SAU — the
memory map of ch01 partitioned *by address bits* (the
NS bit on every access, the non-secure-callable entry
gates — the µC's version of the broker pattern of module
16 ch01, ~zero gates of latency, draw it), and the
honesty section: a bare µC with no crypto accelerator
does RSA-2048 verify in seconds (the 40 MHz number: ~
4-8 s RSA verify vs ECC P-256 ~0.5-1 s vs Ed25519
software ~0.5 s — the reason embedded boot chains are
ECC (or why ROM verify-once-then-cache-trust) — the
arithmetic matters); why symmetric (AES) HMAC chains +
a per-device key are the cheap path for volume devices
(module 18 ch02's modes returning as the practical
choice). **Firmware updates**: the A/B bank scheme as
the atomic-update pattern (two flash banks, the
bootloader pointer in a location that can be written
atomically (the double-write dance: write new image to
bank B, mark B valid (the signature + the marker), flip
 the pointer — the failure analysis: power loss at every
step (the 4-step timeline with "what's on disk" and
"what does the bootloader do" at each power-cut point
 — the paper-torn-write story of module 11 ch04/
module 14 ch03's journal replay returning: the marker
structure = a 2-phase commit; the anti-rollback fuse
burn on successful update (the trade: you can never
 downgrade even for a recall — the real policy story
(Iterate-on-fuse-burning honestly: some vendors regret
it)); the delta-update economics (the OTA bandwidth
budget (BLE device with 128 KB image over a 20 kB/s
link: ~8 minutes of vulnerable-window vs the patch's
seconds — why bsdiff-style deltas (~5-10% of full size)
 dominate consumer embedded, the decompress-in-RAM
requirement (the 64 KB patch workspace on a 32 KB-RAM
device is impossible: in-place flash algorithms or
the external-RAM two-stage — the memory constraint
drives the update design, not the crypto). The debug
backdoor problem (the honest center of embedded
security): SWD/JTAG as the universal unlock (attach,
halt, dump flash: the attack that makes all the above
irrelevant if the port is open (the lock bits
(RDP level 1/2 on STM32: level 1 = debug reads
blocked (but the "connect under reset" attacks and
the glitch-to-downgrade folklore), level 2 = the
chip is dead to debug forever — the bricking trade
stated plainly, the production decision: lock before
ship, keep level-1 on dev units (the SKU-splitting
practice); the glitch-attack reality (voltage/clock
glitching skips the signature-check branch — the
countermeasure: double-check in software (the check-
twice pattern), fault-detecting code (control-flow
integrity via random inserted checks — module 02's
CFI at the branch scale; the ChipWhisperer as the
$300 tool that democratized it — the cost table of
the threat model validated); side channels at the
firmware layer (power analysis on a key schedule
( module 18 ch03's story at the device scale: the
10 k-trace DPA attack on AES — the masking/
hiding countermeasures' performance cost, and the
"batch attack" (one capture during a known-plaintext
session = the key (the consumer-device exposure
story). The supply chain (the firmware image's
provenance: module 21's formal side (what the image
*proves* about itself vs what the supply chain proves
about who built it (reproducible builds for firmware
( toolchain pinning, the embedded build's
hermeticity problem (vendor SDKs, binary blobs in
HALs — the honesty: most firmware builds are not
reproducible today, the SBOM push (CISA's 2023-2024
requirements) landing on embedded. Insiders: the
ROM-that-can't-be-patched problem (the immutable
first stage's bugs are forever (the ROM boot ROM
CVEs that forced recall-class responses, the
microcode-vs-bootrom honesty); the "default key"
pandemic (the same RSA key in every unit of a
product family (the "private key extracted from one
device owns the fleet" class — real examples (the
camera-fleet / router-fleet stories, the key-per-
device manufacturing discipline (per-device certs
provisioned at the factory: the CM's role (the
contract-manufacturer trust problem — the "the
factory sees your keys" elephant; the anti-rollback
fuse economics (burning a fuse is a one-way business
decision (the field-failure mode where you NEED to
roll back but can't — the policy war story); the
ESP32-style closed-source boot ROM vs the Pico's
open boot2 (the philosophical split in the hobby
market); the "secure boot without a secure element
is DRM for your own firmware" honest framing (when
it's anti-tamper for the customer's benefit vs the
manufacturer's. Lab: verify a signed image with
python (create a keypair, sign a 4 KB image, write
the verifier that a bootloader would run (~30 lines:
hash the image, verify with pycryptodome/ed25519 —
the actual code, must run); implement the 2-phase
marker flip and power-cut-simulate (the lab writes
the state machine and asserts recovery at every
cut-point — the 4 cases exercised, output shown);
the glitch paper-lab: the check-once vs check-twice
comparison written up (why the second check after
the jump is the one that survives the glitch — the
control-flow diagram); (hardware-optional: the
ChipWhisperer capture of an AES round documented
from the public tutorials — the honest "if you have
the hardware" lab).

**Connections requirement.** Every chapter's Connections must
include (beyond same-module): ch01 → module 01 ch03 (virtual
memory: the removed layer), module 01 ch05 (the heap, mostly
absent), module 17 ch03 (the peripherals as RTL); ch02 →
module 01 ch04 (the stack — initialized from a file), module
04 ch02 (the loader: what a µC doesn't need), module 07 ch02
(Linux boot: the same ladder scaled up); ch03 → module 09 ch02
(priority ceiling = BASEPRI — mandatory explicit correspondence),
module 13 ch01 (the DMA ring), module 19 ch03 (volatile +
DMA as a borrowing rule); ch04 → module 07 ch04 (the O(1)
scheduler's lineage — **mandatory: this repairs m07's ch04→
 back-link, and the FreeRTOS/Linux runqueue comparison must
name both**), module 03 ch05 (Pathfinder inversion), module
15 ch04 (MPU-tasks vs containers — the scale comparison);
ch05 → module 18 ch03 (the key-storage stack), module 05 ch06
+ module 06 ch05 (Android/iOS verified boot as the rich
descendants), module 02 (the attack surface catalog), module
07 ch03 (macOS boot chain: the desktop world's mirror —
**mandatory: repairs m07 ch03's orphan status**).

**The final exam** (`quiz/final-exam.md`): 12 questions, worked
answers, 5 parts (one per chapter, 3+3+2+2+2), at least one
question must compute a stack/RAM budget from a linker map,
at least one must derive the SPSC ring's safety conditions
(one writer per index), and at least one must walk a power-
loss timeline through the A/B update state machine.
