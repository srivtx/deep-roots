# Chapter 03 — Interrupts and DMA: the asynchronous memory

> **The 30-second version.** On a microcontroller, "asynchronous" is not
> a thread library — it is hardware writing your memory while your
> program is between two instructions. An interrupt is a hardware
> callback: the NVIC latches a *pending* bit, the core auto-saves 8
> words of frame, and your C function runs 12 cycles after the event.
> A DMA controller is a second CPU with a 4-register instruction set
> that overwrites your RAM at bus speed. Module 09's concurrency
> results return with no scheduler underneath: the only
> synchronization is what you build with `BASEPRI`, barriers, and
> data-structure shape.

## 1. The NVIC: a callback table wired into silicon

A device pulls its IRQ line; the NVIC — memory-mapped at
`0xE000E100`, in the system region chapter 01 drew — sets one bit in
the pending register. Pending latches: it stays set even when the
interrupt is disabled, so enabling a mask hours later delivers a stale
event. The core acts only when pending = 1, enable = 1, and the
priority comparison wins. Each interrupt has three bits of state, one
register each:

```text
enable   (ISER/ICER)  -- software gates the source
pending  (ISPR/ICPR)  -- hardware latched a request; software clears it
active   (IABR)       -- set from exception entry to exception return
```

The *priority* registers (`NVIC_IPR`, `0xE000E400`, one byte per
interrupt) decide nesting. The byte is a lie on every real core:
ARMv7-M lets implementations wire 3–8 bits; ARMv6-M (M0) fixes 2.
STM32F4's Cortex-M4 implements **4 bits = 16 levels** — the unused low
bits read as zero, so every CMSIS header shifts priorities left by 4
and `0x10` and `0x11` are the *same* priority on that part. Lower
number = more urgent; 0 beats 15. Only *preemption-priority* bits
decide who interrupts whom — the grouping split that bites in "What
insiders know."

## 2. Exception entry: 8 words, one magic LR, 12 cycles

When the core accepts an interrupt it does in hardware what module 01
chapter 04 made the compiler do: pushes a frame. Exactly **8 words**,
and the choice is derived, not arbitrary: `r0, r1, r2, r3, r12, LR, PC,
xPSR` is the AAPCS *caller-saved* set. Hardware saving exactly those
means an ISR is an ordinary C function — no `interrupt` attribute, no
push-all prologue, no `iret` (contrast x86). SP -= 32, LR :=
EXC_RETURN; if the interrupted code ran on PSP, the core *switches to
MSP* — chapter 02's two-stack split, activated by hardware, and the
RTOS trick of chapter 04 depends on it.

```text
   SP+0x1C  xPSR   flags, incl. the T(humb) bit      higher addresses
   SP+0x18  PC     return address -- where main resumes
   SP+0x14  LR     r14 at the moment of interruption
   SP+0x10  r12                  \  the AAPCS caller-saved set:
   SP+0x0C  r3                    | "free to trash" -- an ISR is a
   SP+0x08  r2                    | plain C function
   SP+0x04  r1                    |
   SP+0x00  r0    first argument /  lower addresses (MSP grows down)
```

LR is overwritten with **EXC_RETURN**, whose bit 2 selects the return
stack: **0xFFFFFFF9** = "Thread mode, restore MSP"; **0xFFFFFFFD** =
"Thread mode, restore PSP." `bx lr` on it triggers exception return: pop 8
words, switch stacks, restore xPSR — and every stack-overflow, RTOS,
or lazy-FPU bug in this module is debugged by reading exactly these 8
words (plus 17 more FPU words on M4F/M7, gated by bit 4 — ch04).

The timing is a promise you can budget: **12 cycles** from assertion to
the first ISR instruction, at zero wait states, and 12 to return; two
hardware optimizations make back-to-back interrupts cheap:

```text
 normal:     ISR-A return (pop 8) then ISR-B entry (push 8) = 24 wasted
 tail-chain: pop cancelled, stacked frame reused, B entered = 6 cycles
 late arrival: higher-priority B lands while A's frame is still stacking:
               hardware refetches B's vector, B runs first, A tail-chains
```

So the *architectural* latency is bounded — but your code can destroy
the bound. The real worst case is `T_cpsid_max + 12 + wait_states`:
every `cpsid i` section is a window in which nothing, however urgent,
runs. A 2,500-cycle critical section at 120 MHz is a 20.9 µs latency
floor *you chose*. Measure your longest mask window (GPIO toggle) and
budget that number, not 12.

## 3. BASEPRI is the priority-ceiling protocol — the proof

Module 09 chapter 02 fixed priority inversion with protocol: a task
entering a critical section for resource R *raises its priority to the
ceiling of R* — the highest priority of any task that could also lock
R — bounding blocking, preventing deadlock, letting unrelated
high-priority work preempt. The Cortex-M implements that protocol in
one register write, line by line:

```text
   software PCP (module 09 ch02)       Cortex-M (this chapter)
   task priority                  <->  NVIC preemption priority (low = urgent)
   raise to ceiling(R)            <->  BASEPRI := best priority among
                                        the IRQs sharing R
   scheduler re-dispatch on exit  <->  exception return re-evaluates pending
   blocked tasks, prio-ordered    <->  NVIC pending bits, priority-sorted pop
```

`BASEPRI` (v7-M only) masks every interrupt whose priority *number*
is `>= BASEPRI` — everything at or below the ceiling you just set, and
nothing else. Writing `BASEPRI = 0x40` before touching a ring shared
with the priority-0x40 UART ISR blocks exactly the UART and anything
slower; a priority-0x20 motor-control interrupt still preempts you.
`PRIMASK` (`cpsid i`) masks *everything* — module 09's "big kernel
lock," the ceiling of the entire machine — losing PCP's central
guarantee: *blocking bounded by the resource, not the whole system*.
Every unrelated deadline pays for your ring.

The proof moment: "never in an ISR" is not folklore, it is the
protocol. `HAL_Delay` in an ISR deadlocks *by construction* when
SysTick's priority is lower than yours (derived below); `printf`
blocks; `malloc` takes non-reentrant locks — each is a task spinning
at the ceiling of a resource while the unblocker is the one masked.

## 4. The shared-memory contract: volatile, DMB, TOCTOU

The C memory model has no ISR rule: `main` and `SysTick_Handler` are
not threads — no `std::atomic`, no happens-between. Whatever
synchronization exists is what you build. `volatile`'s limits:

- It forces the compiler to *emit* each access and to keep
  volatile-to-volatile ordering (C11 5.1.2.3) — and nothing else: not
  the CPU reordering plain accesses around them, not atomicity beyond
  one aligned word.
- Device memory (the `0x40000000` region, per the ARM architecture
  reference) is *strongly ordered* — but your `memset` of a DMA buffer
  is **normal memory**: nothing stops the compiler sinking it past the
  volatile "start DMA" register write.
- The register-scale **TOCTOU** of module 03 ch03:
  `if (UART->SR & TXE) UART->DR = c;` is check-then-act on a shared
  register; an ISR (or DMA) filling DR inside the window makes the
  stale check lie — overrun, duplicated byte. Same fix shape as
  `O_EXCL`: make the contender impossible — single-writer ownership
  or masking around the window.

So when is `__asm volatile("dmb")` genuinely required? Three windows,
all ordering plain memory against a mode or device boundary — the
arming pattern, the return path, and the sleep path:

```c
memset(dma_desc, 0, sizeof desc);   /* plain writes: reorderable        */
__DMB();                            /* NOW they're ordered, device next */
DMA->CR |= GO;                      /* the arming write (device)        */
```

- **Arming DMA**: descriptor writes must land before the start bit
  reaches the controller. On in-order M3/M4 the barrier mostly stops
  the *compiler*; on the M7's write buffer it is load-bearing.
- **Clearing an interrupt then returning**: clear the peripheral flag,
  `__DSB()`, then return — else the write may not reach the device
  before exception return and the handler re-enters ("spurious
  re-interrupt").
- **Sleep**: NVIC enable, `__DSB()`, `__WFI()` — else the WFI executes
  before the enable reaches the NVIC and you sleep through your own
  wake source. The interrupt enable/disable window is the same rule
  with PRIMASK instead of a device register.

The pattern that is *not* a fix: the double-checked volatile read —
read a flag, decide, act. Between read and act anything can
interleave; volatile gave you freshness, not exclusion. Only removing
the window fixes it.

## 5. The single-writer law and the 16-line SPSC ring

The design law that survives everything: **every shared variable has
exactly one writer** — ISR-owns or main-owns, never both. One
variable, one writer, one aligned 32-bit word, and module 03's race
family evaporates. The canonical artifact is the lock-free SPSC ring —
the *same structure* as module 13 ch01's NIC DMA descriptor ring and
io_uring: a fixed array, two published indices, one per side.

```c
/* The 16-line SPSC ring: producer = ISR, consumer = main. */
#define N 64u                                  /* power of two          */
static uint8_t  buf[N];
static volatile uint32_t head;                 /* producer writes ONLY  */
static volatile uint32_t tail;                 /* consumer writes ONLY  */

int put(uint8_t b) {                           /* called from the ISR   */
    uint32_t h = head;
    if (h - tail == N) return -1;              /* full (wrap-safe)      */
    buf[h & (N-1)] = b;
    __DMB();                                   /* publish data, THEN idx*/
    head = h + 1;
    return 0;
}
int get(uint8_t *b) {                          /* called from main      */
    uint32_t t = tail;
    if (t == head) return -1;                  /* empty                 */
    *b = buf[t & (N-1)];
    __DMB();                                   /* read data, THEN free  */
    tail = t + 1;
    return 0;
}
```

```text
        producer (ISR) pushes ->       consumer (main) pops ->
                     head -- written by the ISR, read by main
   +----+----+----+--v-+----+----+----+--^---+----+----+
   |    |    |    | D |  E |  F |          |    |    |   letters = data
   +----+----+----+--|-+----+----+----+----^----+----+----+
                     |                     tail -- written by main,
                     +-- data flows this way ->  read by the ISR
   free slots = [head, tail)  in unsigned arithmetic, wrapping 2^32
```

The safety conditions are module 09 ch03's release/acquire discipline
re-applied at register scale — the quiz question to derive cold:
(1) **single writer per index**; (2) **data published before the
index** (`put`'s DMB — release); (3) **index read before the data** in
`get` (acquire pairing); (4) **wrap-safe arithmetic** — `h - t == N`
unsigned, never signed `head > tail`; (5) the index store is one
aligned 32-bit word — where the hardware contract enters.

That last condition is the misfire that kills the ring on a real
core: the M0 has no `ldrex/strex`, and v6-M's only atomicity promise
is the single aligned 32-bit access. A 32-bit head is safe; a
**64-bit** value is two stores, and the ISR *will* interleave:

```c
volatile uint64_t g_ms;                 /* ISR: g_ms++ (two stores)     */
uint64_t now = g_ms;                    /* main: two loads -> can tear */
/* seed g_ms = 0xFFFFFFF0; at the 2^32 wrap the ISR stores low:=0
 * then high:=1. main can read them in the opposite order:
 *   old high, new low -> time went BACKWARD by ~49.7 days
 *   new high, old low -> time jumped AHEAD by ~49.7 days   */
```

Fixes: a seqlock-style double-read (low, high, re-read low, retry); a
32-bit tick with wrap-safe subtraction; on M3+, `ldrex/strex`. What is
available where — the porting table:

```text
 instruction   encoding      M0/M0+ (v6-M)   M3/M4/M7 (v7-M)
 ldrex/strex   32-bit T2     absent          present
 ldrd/strd     32-bit T2     absent          present (aligned doubleword
                                              single-copy atomic on M3/M4
                                              by TRM -- not architectural)
 dmb/dsb/isb   32-bit        present         present
 BASEPRI       -             absent          present
```

## 6. DMA: the second CPU you didn't budget for

DMA is an independent bus master whose entire instruction set is four
registers — source, destination, count, control — plus a start bit and
a done interrupt (the PL230 in the MPS2, or STM32):

```c
/* The 4-register programming model (PL230 names; STM32 mapped). */
DMA->SRC = (uint32_t)src;              /* STM32 DMA_SxPAR or SxM0AR */
DMA->DST = (uint32_t)dst;              /* STM32 DMA_SxM0AR or SxPAR */
DMA->CTR = len | W8 | MINC | TCIE;     /* STM32 SxNDTR + SxCR bits  */
DMA->CTR |= GO;                        /* armed -> busy             */
```

The transaction lifecycle — armed → busy → done → ISR:

```text
 CPU                              DMA controller
  |-- write 4 registers, set GO ->|  armed
  |   CPU walks away, runs main    |  busy: bus master, read/write
  |<-- done: status + IRQ to NVIC --|  (free CPU cycles -- the payoff)
       TC ISR: consume buffer, re-arm the other half (ping-pong)
```

**Cache coherence (M7, mandatory bug).** With the D-cache on, CPU
and DMA are two writers of the same RAM through different paths. The
discipline is directional: **invalidate-before-receive** (discard
stale lines so the CPU reads what DMA wrote), **clean-before-send**
(push dirty lines so DMA reads what the CPU wrote). The trap: a line
is **32 bytes** and your buffer is not:

```c
/* M7, D-cache ON. THE BUG: 16-byte buffer, not 32-B-line aligned. */
uint8_t  cfg;              /* live flag, same line as rx              */
uint8_t  rx[16];           /* linker places it right after cfg        */

void dma_rx_start(void) {
    /* line 0x2000_0000..1F holds BOTH cfg (offset 0) and rx (0x10);
       the ROUNDED invalidate discards the WHOLE 32-byte line.       */
    SCB_InvalidateDCache_by_Addr((uint32_t *)rx, sizeof rx);
    DMA->SRC = (uint32_t)rx;  DMA->CTR = 16;  DMA->CTR |= GO;
    /* cfg's dirty cached copy vanished with the line: cfg reads its
       OLD RAM value after the transfer — weeks on the bench before
       the line is dirty at the right moment.                         */
}
/* FIX: __attribute__((aligned(32))) rx[32]; whole-line lengths; or a
   non-cacheable MPU region (ch01) and no ritual at all.             */
```

**Physical addresses.** No MMU means the identity map — the pointer
you have *is* the bus address (contrast module 15's `dma_map` under an
IOMMU). The corner cases are reachability: STM32F4's 64 KiB CCM RAM is
CPU-only — DMA pointed there simply fails — and on H7 most DMAs
cannot touch DTCM.

**The stack-allocated-buffer catastrophe.** Module 01 chapter 04's
lifetime bug reborn as hardware: arm DMA on an automatic buffer and
return.

```c
void sensor_poll(void) {
    uint8_t buf[64];
    start_dma(buf, 64);     /* armed; transfer still running... */
}                           /* ...while this frame dies.        */
```

DMA writes into a returned stack frame — the locals of whatever runs
next; corruption lands much later, in unrelated code. The fix
generalizes "heap is banned" to "stack is banned": DMA buffers are
`static` or pool-allocated. The rule *is* module 19 chapter 03's
borrow checker, worn as hardware discipline:

```text
 CPU (owner)                  DMA (the one &mut borrower)
   static uint8_t buf[N]
   start_dma(&buf, N) ------->  borrow begins
   MUST NOT touch buf           busy
   transfer-complete IRQ <----- borrow ends; buf is the CPU's again
```

While DMA holds the buffer, the CPU touching it is a second `&mut` —
even a read re-populates the line you invalidated. One exclusive
borrower, region contained by the borrow: E0515 with silicon
consequences.

**Ping-pong and the glitch arithmetic.** Arm the next transfer while
the current runs — the TC ISR re-arms the just-finished half, a
one-buffer-deep pipeline. Fail, and the math is unforgiving: CD audio
is 44,100 samples/s × 2 channels × 2 bytes = **176,400 B/s**. A 20 ms
ISR delay — a long disabled window, a higher-priority handler —
consumes 3,528 B = 882 stereo frames of silence: an audible click.
Five ms of jitter demands 882 B pre-buffered; 200 ms × 2 halves =
70.5 KiB, over half a 128 KiB part. Correctness here is *function +
timing* — the real-time definition, hard vs soft distinguished only
by what the missed deadline costs.

**Bus contention honesty.** DMA's payoff is not free: CPU and DMA are
masters on the same matrix, and buffers in the same SRAM bank
serialize — a DMA burst stalls the CPU's fetches. Bigger parts split
banks for exactly this: on STM32H7, DMA buffers in SRAM1, stack and
locals in SRAM2 (or DTCM, which DMA can't reach — contention-free by
unreachability). The memory map of chapter 01, used as a *performance*
tool.

## What insiders know

- **The priority-grouping footgun.** `AIRCR.PRIGROUP` splits the
  priority bits into preemption (top) and sub-priority (bottom). On a
  4-bit part, grouping "0 preempt / 4 sub" makes *all 16 levels
  sub-priority-equal*: nothing preempts anything; pending interrupts
  merely queue. The support-forum classic: "my interrupts stopped
  nesting after someone added a HAL init call" — ST's HAL defaults to
  `NVIC_PRIORITYGROUP_4` (16/0); one call to grouping 0 kills nesting
  silently.
- **WFI and SEVONPEND.** The idle loop is `__DSB(); __WFI();` — sleep
  until any enabled interrupt. With `SCR.SEVONPEND` set, a *masked or
  disabled* interrupt going pending still sets the event register: you
  wake without executing any handler — the wake-and-poll pattern. A
  disabled-but-pending source still drains the battery.
- **Semihosting exit.** A bare-metal QEMU program ends cleanly with
  `mov r0,#0x18; ldr r1,=0x20026; bkpt 0xAB` (SYS_EXIT /
  ADP_Stopped_ApplicationExit). On real silicon without a debugger,
  that `BKPT` is a HardFault — emulator-only, and every lab here ends
  with it.
- **`IRQn = 0` is not EXTI0.** CMSIS numbers system exceptions −15..−1
  (`SysTick = -1`), so the vector-table slot is `IRQn + 16` — the "−16
  offset" folklore. Peripheral numbering is per-family: on STM32F1,
  `EXTI0_IRQn = 6`, `TIM3_IRQn = 29` — not 3. Copy the vendor's
  header, never intuition.
- **SysTick arithmetic.** The 24-bit counter reloads at 0, so the
  period is `RVR + 1`: 72 MHz core → `RVR = 71999` → 1.000 ms; the
  24-bit ceiling caps a tick at 2^24/72 MHz ≈ 233 ms — longer periods
  count in the handler. Off-by-one: `HAL_GetTick()` timeouts 1 cycle
  wrong (nobody notices) or 1 ms wrong (everybody notices).
- **HAL_Delay-in-ISR deadlock, derived.** `HAL_Delay` spins on
  `uwTick`, incremented by `SysTick_Handler`. If your ISR's priority
  beats SysTick's (lower number), the tick cannot preempt its own
  consumer: `uwTick` freezes, you spin forever — self-inflicted
  priority inversion with the roles reversed. Fix: SysTick at
  priority 0, or never wait in a handler.

## See it yourself

**Lab 1 — SysTick in QEMU (ch02's program, extended).** Chapter 02's
minimal program, plus a timer — asynchronous execution takes over.

```c
#include <stdint.h>
typedef struct { uint32_t CTRL, LOAD, VAL, CALIB; } SysTick_t;
#define SYST  ((SysTick_t *)0xE000E010)
#define CLK   25000000u                    /* QEMU mps2-an385 clock */

extern void Reset_Handler(void), SysTick_Handler(void);
__attribute__((section(".vectors")))
void (* const vectors[])(void) = {
    (void (*)(void))0x20010000,  /* 0: initial SP (top of SRAM)       */
    Reset_Handler,               /* 1: reset                          */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,   /* 2-14: unused           */
    SysTick_Handler,             /* 15: SysTick — the clock we added  */
};

volatile uint32_t ticks, loops; /* ticks: ISR writes; loops: main  */
void SysTick_Handler(void) { ticks++; }    /* the 3-line handler     */

static void wr0(const char *s) {          /* semihosting SYS_WRITE0  */
    register uint32_t r0 __asm("r0") = 0x04, r1 __asm("r1") = (uint32_t)s;
    __asm volatile("bkpt 0xab" : "+r"(r0) : "r"(r1) : "memory");
}
static void wr_u32(uint32_t v) {          /* decimal print           */
    char s[11]; int i = 10; s[i] = 0;
    do { s[--i] = '0' + v % 10; v /= 10; } while (v);
    wr0(&s[i]);
}
void Reset_Handler(void) {
    SYST->LOAD = CLK / 1000 - 1;           /* 24999 -> 1 ms          */
    SYST->VAL  = 0;
    SYST->CTRL = 7;                        /* ENABLE|TICKINT|CLKSRC  */
    uint32_t last = 0;
    while (ticks < 5) {
        loops++;                           /* the measured main loop */
        if (ticks != last) {
            last = ticks;
            wr0("tick "); wr_u32(ticks);
            wr0("  loops "); wr_u32(loops); wr0("\n");
        }
    }
    wr0("done\n");
    register uint32_t r0 __asm("r0") = 0x18, r1 __asm("r1") = 0x20026;
    __asm volatile("bkpt 0xab" : "+r"(r0) : "r"(r1) : "memory"); /* exit */
}
```

Same 3-line linker script as chapter 02 (flash `0x00000000`, RAM
`0x20000000`). Build and run:

```console
$ arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -nostdlib -T ld.ld systick.c -o s.elf
$ qemu-system-arm -M mps2-an385 -cpu cortex-m4 -nographic \
    -semihosting-config enable=on,target=native -kernel s.elf
tick 1  loops 1946117
tick 2  loops 3892340
tick 5  loops 9730991
done
```

Your numbers will differ — the invariant won't: `ticks` advances
only inside the handler, `loops` only in main, and the ratio *is*
"main-loop iterations per tick": the handler runs every 25,000
cycles without main calling anything.

**Lab 2 — stress the ring, then break it.** SysTick at 100 µs
(`LOAD = CLK/10000 - 1`) is the producer — a xorshift PRNG in the ISR
pushes 0–3 bytes per tick; main pops and feeds a rolling checksum.
Run to 10^6 operations, then break it: in `get`, move the `tail` store
*above* the data read (widen the window with no-ops between them):

```text
OK: 1000000 ops, 2986124 bytes, checksum matched, 0 drops
-- break: tail published before the slot is read --
CORRUPT at op 4211: got 0x37, expected 0xC9
```

The manifestation, documented: the ISR fires in the widened window
between your `tail` store and your slot read; the producer sees the
slot free and over-writes it mid-read — you read *its* byte, the
stream desynchronizes, the checksum diverges within a few thousand
ops, every run. The mirror break (`put` publishing `head` before the
data write) shows the over-read flavor: unwritten slots received.
The *order* of two statements was the only thing making the queue
correct.

**Lab 3 — the M7 cache-line paper lab.** QEMU's M4 has no D-cache, so
trace section 6's bug on paper: draw `0x2000_0000..0x1F`, place `cfg`
at offset 0 and `rx[16]` at 0x10, then walk through
`SCB_InvalidateDCache_by_Addr(rx, 16)`: address rounds *down*, length
*up* — line boundaries always win. Mark the line dirty with `cfg = 1`
(cached, RAM still 0), invalidate, read `cfg`: 0. Apply the fix
(32-byte alignment + whole line, or a non-cacheable MPU region) and
re-run: the invalidate touches only the buffer's own lines. Those two
traces are the review you will one day run on someone's driver.

**Lab 4 — the audio arithmetic (with answers).** Stereo 16-bit at
44.1 kHz: (a) bytes per second? (b) buffer bytes to ride out 5 ms of
re-arm jitter? (c) a 200 ms safety margin, ping-pong (×2)? (d) fraction
of a 128 KiB part? — 176,400 B/s; 882 B; 70,560 B; 55%. Re-do it for
24-bit 96 kHz stereo (576,000 B/s) and watch the margin double twice:
once for the width, once for the rate. "Audio-capable" is a RAM-size
class, not a CPU-speed class.

## How to use this knowledge

- **Debugging the unexplained:** "a variable randomly reverts to an
  old value" now has a ranked hypothesis list — a 32-byte cache-line
  invalidate (M7), a DMA write into a returned stack frame, a 64-bit
  tear at the 2^32 wrap. Check the map file for adjacency before
  blaming silicon. Latency budgets: quote `max(cpsid-window) + 12 +
  wait-states`, measured by GPIO toggle, and defend it in review like
  a database engineer defends a p99.
- **Design reviews:** reject any shared variable with two writers on
  sight; ask of every DMA buffer "who holds the `&mut` right now?";
  ask of every `dmb` "which two accesses is this ordering?" — a
  barrier you can't explain is a barrier you don't need.

## Connections

- **Module 20 ch01** — the memory map as performance tool: SRAM
  banks, DTCM unreachability, non-cacheable MPU regions.
- **Module 20 ch02** — the vector table/startup code these labs
  extend; MSP/PSP activated by EXC_RETURN; 8-word frame × nesting
  feeds the stack-sizing arithmetic.
- **Module 20 ch04** — each task a PSP, ISRs share the MSP; FreeRTOS
  queues are this ring generalized to MPMC; the RTOS mutex's ceiling
  is BASEPRI plus software.
- **Module 09 ch02** — the priority-ceiling protocol: BASEPRI is that
  protocol as a hardware register (the proof moment).
- **Module 09 ch03** — release-on-publish/acquire-on-consume is the
  ring's safety proof; ldrex/strex is LL/SC at register scale.
- **Module 13 ch01** — the NIC's DMA descriptor ring is this ring at
  40 Gbps; io_uring is the same shape in userspace.
- **Module 19 ch03** — the buffer handoff to DMA *is* the `&mut`
  borrow: one exclusive writer, region contained by the lifetime.
- **Module 03 ch03** — TOCTOU at register scale: check-then-act on a
  status register, fixed by ownership, not volatile.

## Quiz

1. **Concept.** Why does Cortex-M hardware stack exactly `r0-r3, r12,
   LR, PC, xPSR` — and what does that let an ISR *not* have that an
   x86 handler needs?
2. **Concept.** Why is `PRIMASK` module 09's "big kernel lock" while
   `BASEPRI` is the priority-ceiling protocol? Which PCP guarantee does
   the PRIMASK critical section lose?
3. **Derive.** The 16-line SPSC ring is correct on a single-core M4.
   Derive its safety conditions — each with the failure it prevents —
   and state what breaks if the consumer publishes `tail` *before*
   reading the slot.
4. **Predict the number.** A 48 MHz Cortex-M0 must interrupt every
   250 µs via SysTick. Give RVR, and the longest tick the 24-bit
   counter allows at that clock.
5. **Predict the number.** Your longest `cpsid i` section is 2,500
   cycles on a 120 MHz M4, zero wait states. Worst-case latency of your
   highest-priority interrupt — and what dominates it?
6. **Diagnose.** An M0 logger's `uint64_t` millisecond timestamps
   occasionally land ~49.7 days in the past. The ISR does `g_ms++`,
   main does `uint64_t now = g_ms`. Explain the mechanism and give two
   correct fixes.
7. **Diagnose.** On an M7 with D-cache, a driver adds
   `SCB_InvalidateDCache_by_Addr(&rx, 16)` before each DMA receive.
   Weeks later, a global placed just before `rx` starts reverting to
   old values. Why, with the address arithmetic?
8. **Cross-chapter.** `start_dma(buf, n)` from this chapter and
   `let r = &mut x;` from module 19 ch03 name the same rule. Which
   Rust error class is the stack-allocated DMA buffer, and what "borrow
   region" must the CPU respect while DMA is busy?

<details>
<summary>Answers</summary>

1. **...** That set is the AAPCS *caller-saved* set — the registers
   a function may trash without saving. Hardware saving exactly them
   means the interrupted code is protected *and* the ISR is a plain C
   function: no `interrupt` attribute, no push-all prologue, no
   `iret` — `bx lr` with EXC_RETURN returns. x86 pushed only flags,
   CS, IP, so its handler must save everything the compiler might
   have live; the M-profile frame is the ABI itself.
2. **...** PCP: entering a critical section for R raises you to
   ceiling(R) — the highest priority of any *contender for R* — so
   blocking is bounded by R's contenders and unrelated work still
   preempts. `BASEPRI = p` masks exactly interrupts with priority
   number >= p: the ceiling of the resource shared with the priority-p
   ISR. `PRIMASK` masks everything — the ceiling of the whole machine
   — losing the bounding guarantee: any unrelated deadline is blocked
   for the section's full duration. (PRIMASK is the only choice on
   M0.)
3. **...** Five conditions: (1) *single writer per index* — `head`
   only in the ISR, `tail` only in main; two writers is a lost-update
   race. (2) *Data published before the index* (`put`'s DMB): without
   it the consumer sees the new `head` with old slot bytes. (3) *Index
   read before data* in `get` (acquire pairing). (4) *Wrap-safe
   arithmetic* — `h - t == N` unsigned; signed `head > tail` wedges or
   floods at the 2^32 wrap. (5) *One aligned 32-bit index store* — the
   v6-M atomicity promise; a 64-bit index tears. The asked break —
   `tail` published before the slot read: the producer observes the
   slot free and over-writes it mid-read, so the consumer receives the
   *producer's new* byte: desynchronization, checksum divergence
   (lab 2).
4. **...** Period = RVR + 1, so RVR = 48e6 × 250e-6 − 1 = **11,999**;
   max = 2^24 / 48e6 ≈ **349.5 ms** — longer ticks must count in the
   handler.
5. **...** 2,500 + 12 = **2,512 cycles = 20.93 µs**. The 12-cycle
   entry is 0.5% of it: *your own critical section* dominates. Latency
   is what you disable, not what the core adds.
6. **...** `g_ms++` compiles to two 32-bit stores (low then high,
   little-endian); the read is two loads. v6-M promises atomicity only
   for one aligned word (no ldrex/strex). At the 2^32 wrap the ISR
   stores low := 0 then high += 1; main reading between them gets (old
   high, new low) ≈ 49.7 days in the past, or (new high, old low) 49.7
   days ahead. Fixes: seqlock-style double-read (low, high, re-low,
   retry) or a 32-bit tick with wrap-safe subtraction.
7. **...** A line is 32 bytes and the invalidate rounds address
   *down*, length *up*. `rx` at 0x2000_0010 shares line
   0x2000_0000..1F with the global at 0x2000_0004; the invalidate
   discards the whole line, the global's dirty cached copy dies with
   it, and the next read reloads stale RAM. Fix:
   `__attribute__((aligned(32)))`, whole-line lengths, or a
   non-cacheable MPU region for DMA buffers.
8. **...** E0515-class — a loan escaping its frame: `start_dma(buf,
   n)` with `buf` automatic hands out a region whose lifetime ends at
   return while the borrower (DMA) still holds it; Rust rejects the
   analogous code with "cannot return reference to local variable."
   The *borrow region* is the buffer: the CPU must neither read nor
   write it (one `&mut` — even a read re-caches the line on M7); the
   borrow ends at the transfer-complete ISR.

</details>
