# Chapter 04 — The RTOS: a scheduler in 10 KB

> **The 30-second version.** Chapter 03 ended with a superloop, one
> stack, and an ISR that had to be short because nothing could preempt
> it. An RTOS spends ~10 KB of flash removing that constraint: every
> task gets its own stack, and SysTick/PendSV swap them for ~150
> cycles per millisecond — 0.15% of a 100 MHz M4. The rest is data
> structures you can read: task = stack + TCB; queue = chapter 03's
> SPSC ring fused to a blocked-task list; scheduler = a bitmap and a
> CLZ; heap = dlmalloc shrunk. The T in RTOS is made true only by
> rate-monotonic arithmetic you do yourself — the kernel ships
> mechanisms, never guarantees.

## The preemption decision: where latency is manufactured

Cooperative multitasking is what a superloop already is: tasks call
`taskYIELD()` where they choose. Every task's worst-case latency is
then the longest section between yields *anywhere*: a display task
pushing a 320×240×16-bit frame over SPI at 10 MHz is 153,600 bytes =
1,228,800 bits = **123 ms** — every other task now has ≥123 ms
latency. Ten I²C reads at 400 kHz is 0.65 ms: fine under a 5 ms
deadline, fatal under 500 µs. Cooperative latency is a property of
your source code, re-derived by every edit, invisible in review.

Preemption moves the number into hardware: SysTick fires every
quantum (1-10 ms, one config line) and interrupts any task
mid-instruction. What it costs, honestly:

1. SysTick's handler never switches — it sets PendSV pending and
   returns.
2. PendSV runs at the *lowest* exception priority (0xFF): a switch
   can never interrupt an active ISR.
3. Exception entry is half free: hardware stacks the basic frame —
   R0-R3, R12, LR, PC, xPSR = 8 words.
4. PendSV pushes R4-R11 (8 more), stores the stack top in the
   TCB, picks the next, restores: **~100-200 cycles** on an M4.

On an M4F the FPU nearly doubles the worst case, and the hardware
hedged. `CONTROL.FPCA` is set the first time a task runs a
floating-point instruction. Clear: no FP state — a 2-cycle footnote
on entry. Set: hardware *reserves* 17 words (s0-s15 + FPSCR) but
doesn't write them — **lazy stacking**: space claimed, copying
deferred. The port pushes s16-s31. Crossing the FP boundary on a
switch costs 100+ cycles and a frame **17-33 words** deeper (17 = the
reservation; 33 = reservation + s16-s31 + alignment). Which world a
TCB belongs to travels in `EXC_RETURN` bit 4, saved with the frame.

Now the trade the whole industry makes, inline. 1 kHz tick, every
tick preempts, 150 cycles per switch: 1,000 × 150 = 150,000 cycles/s
— **0.15%** of a 100 MHz M4, **0.015%** of a 1 GHz-class core; even
a 10 kHz tick is 1.5%. Preemption was never expensive in cycles — the
costs are power (tickless idle, below) and ISR jitter. The decision:
trust code review to bound 123 ms, or pay 0.15%?

## A task is a stack plus a TCB

Everything an RTOS *is* reduces to this pair. Decode a real call:

```c
xTaskCreate(vSensorTask, /* entry: void f(void*), and it NEVER returns */
            "sensor",    /* name: for crash reports and trace only */
            128,         /* usStackDepth: in WORDS (StackType_t), not bytes */
            pvArgs,      /* the one void* the entry receives */
            2,           /* priority: larger = more urgent; 0 is idle */
            NULL);       /* out: TaskHandle_t*, NULL if unused */
```

The trap is the third argument: `128` is `StackType_t` units —
**words**: 128 × 4 = **512 bytes** on any 32-bit core, not 128.
`configMINIMAL_STACK_SIZE` is words too (the idle task's stack,
typically 128 on CM3/CM4). Size "128 bytes" and the task dies on
its first `printf` — this has caught everyone. TCB and stack both
come from the kernel heap (`configTOTAL_HEAP_SIZE`): ~90 B of TCB +
the stack + an 8 B header — the RAM ledger is knowable to the byte:

```
   TCB (~90 B, from heap_4)              task stack (128 words = 512 B)
+--------------------------------+      +------------------------------+ high
| pxTopOfStack  ----------------|-----> | live SP: saved R4-R11        |
| pxStack, xStackEnd  ----------|----   | (s16-s31 if an FP task)      |
| pcTaskName "sensor"           |       |   ... frames grow downward   |
| uxPriority 2  uxBasePriority 2|       | vvvvvvvvvvvvvvvvvvvvvvvvvv   |
| xStateListItem  -> ready/etc. |       | A5 A5 A5 A5  <- 0xA5 fill,   |
| xEventListItem  -> a wait list|       |   high-water mark counts this |
+--------------------------------+      +------------------------------+ low
```

Two list items per TCB are the concurrency model: exactly one *state*
list (ready, delayed, suspended) via `xStateListItem`, at most one
*object* wait-list via `xEventListItem` — block and unblock are two
O(1) moves.

**Stack-overflow checking** has two modes, one statistical, one
hardware:

- *Method 1, the pattern canary.* The kernel fills the stack with
  `0xA5` (`tskSTACK_FILL_BYTE`) at creation; each switch checks the
  last 16 bytes for `0xA5A5A5A5`. The stack grows down into the fill,
  so an overflow smashes it and the next switch notices
  (`configCHECK_FOR_STACK_OVERFLOW` 1 checks the pattern; 2 also the
  SP).
- *Method 2, the MPU guard.* Chapter 01's regions put an unwritable
  region below the stack: the offending *write itself* faults
  (MemManage), before data is lost.

| | Method 1: 0xA5 canary | Method 2: MPU guard |
|---|---|---|
| detects at | next context switch | the faulting write |
| latency | up to one tick | 0 |
| certainty | certain, post-hoc | hardware fault, immediate |
| cost | free | 1 region + 32 B alignment per task (~1-2% RAM) |
| works on M0+ | yes | no (no MPU) |

Both objects come from `configTOTAL_HEAP_SIZE` — a static array
(`ucHeap`): the "heap" is just `.bss`. Which allocator serves it is a
philosophy:

- **heap_1**: `pvPortMalloc` only, no free, ~30 lines. The
  discipline: *allocation is bring-up only* — create every task,
  queue, and mutex in `main()` before `vTaskStartScheduler()`, then
  the heap is frozen: fragmentation impossible, OOM at boot where you
  can see it.
- **heap_4**: first-fit over a single free list that *coalesces*
  adjacent free blocks on every free (8-byte `BlockLink_t` header:
  next pointer + size). Module 01 chapter 19's dlmalloc lineage
  shrunk: first-fit and address-ordered coalescing kept, size-class
  bins dropped — tens-of-KB arena, few long-lived objects. ~200
  readable lines: the allocator to read first.

Runtime create/`vTaskDelete` needs heap_4 and its fragmentation risk;
`xTaskCreateStatic` (since v9) gets heap_1's guarantees with the
linker script as bookkeeper — memory is a budget. The scheduler also
creates one task you didn't ask for: **idle**, priority 0,
`configMINIMAL_STACK_SIZE` — guaranteed to run when nothing else is
ready. Chapter 03's power loop lands in its hook:
`vApplicationIdleHook` is the WFI home, one line inside the OS.

## The queue: an SPSC ring that learned to block

Chapter 03's ring had one writer, one reader, no locks by
construction. The queue generalizes it to MPMC by fusing the ring
with two blocked-task lists:

```
 xQueueCreate(8, sizeof(uint32_t))
+--------------------------------------+     xTasksWaitingToReceive (empty):
| pcHead    [0][1][2][3][4][5][6][7]   |       prio 3: TCB-G -> TCB-H   (sorted)
| pcReadFrom ^       pcWriteTo |        |       prio 1: TCB-K
| uxLength 8   uxItemSize 4            |     xTasksWaitingToSend (full):
| xTasksWaitingToSend   -> prio 2: TCB-S|       prio 2: TCB-S
| xTasksWaitingToReceive -> list above |       prio 0: TCB-T
+--------------------------------------+
```

Both wait lists are *priority-ordered* (`vListInsert`; FIFO within a
priority) — the highest-priority waiter is the head, so wake order is
dispatch order. Send on a full queue: the sender's `xEventListItem`
links into `xTasksWaitingToSend`, its `xStateListItem` moves to the
delayed list with the timeout, and the scheduler picks someone else —
the "sleep until data" the superloop faked with a state flag. A
receive that frees a slot reverses it: unlink TCB-S's event item,
move its state item to `pxReadyTasksLists[2]`, and if 2 outranks the
current task, switch *now*.

This is module 07 chapter 04's material at 1/1000th the code: a Linux
pipe is a ring buffer plus a waitqueue — two subsystems; an RTOS
queue is the same two halves **fused into one ~40-line object**, no
layers to spare. One shape, both ways: the RTOS wears it whole, the
desktop OS splits it apart.

**From an ISR, half the object is off-limits.** An interrupt cannot
block, so the ISR-safe family (`xQueueSendFromISR` et al.) only does
the wake-surgery half, plus one deferral trick:

```c
void UART_ISR(void) {
    uint32_t byte = UART->DR;               /* 1: grab the ephemeral data */
    BaseType_t woken = pdFALSE;
    xQueueSendFromISR(rxq, &byte, &woken);  /* 2: 4-byte copy + list surgery */
    portYIELD_FROM_ISR(woken);              /* 3: pend PendSV, return into it */
}
```

If the woken receiver outranks the interrupted task, `woken` is set;
`portYIELD_FROM_ISR` pends PendSV and the ISR's *exception return*
tail-chains into the switch — deferral costs one context switch,
~150 cycles, at exit. Doing the work in the ISR instead: copying a
2 KB DMA buffer in-handler is 2,000+ cycles with everything
lower-priority locked out, *plus* the eventual switch; posting a
4-byte pointer is ~40 + 150. Plain return vs `portYIELD_FROM_ISR` is
the latency knob: plain return waits for the next natural switch (up
to a tick); yield runs now, for 150 cycles.

**Software timers are one task.** Every callback of every timer
executes inside the timer daemon ("Tmr Svc", priority
`configTIMER_TASK_PRIORITY`), fed by a command queue. Derive the
rule: a single task means *every callback is delayed by every other*
— a 100 ms timer drifts to 140 ms whenever a sibling burns 40 ms on
a slow SPI transfer, and one blocking callback delays every timer by
the block duration. Never block in a timer callback; if it needs to
sleep, it is a task a timer should signal.

## Priority inheritance: the Pathfinder fix and its limits

Set up the trap: low-priority L takes a mutex, high-priority H blocks
on it, medium M preempts L. H now waits on M — a task the mutex never
agreed to involve; M can run for hours. FreeRTOS's fix: when H blocks,
the kernel rewrites the owner's `uxPriority` to H's priority
(`uxBasePriority` remembers home; both restored on give) — so L,
momentarily the most urgent task, finishes its critical section
immediately. The stronger protocol is the ceiling: `uxPriorityCeiling`
(the POSIX flavor, VxWorks folklore) boosts the owner to a *fixed*
ceiling at acquire time — before anyone blocks — which also makes
chained inversion and deadlock impossible, at the price of an
offline ceiling table.

The canonical story is module 03 chapter 05's, retold with the
mechanism. Mars Pathfinder, 1997: the lander's RAD6000 ran VxWorks. A
low-priority meteorological task published on the shared "information
bus" while holding its mutex; a medium-priority communications task
ran long; the high-priority bus-management task blocked on the mutex
and starved behind M, and the watchdog reset the spacecraft every few
days. JPL reproduced it under tracing; the fix was flipping that one
semaphore to priority inheritance (VxWorks folklore: `t_priorityBoost`)
— a flag change, uplinked. The feature had existed all along,
unenabled. That is the whole lesson: the mechanism that saves you is
a config bit, and the failure is silent.

What inheritance does *not* fix: deadlock. Two tasks, two mutexes,
opposite acquisition order — boosting the owners strengthens the
cycle rather than breaking it. The cure is a discipline, not a
mechanism: **acquisition order**, module 09 chapter 02's lock-ordering
rule at µC scale — a global order on every mutex, assigned at design
time, never reversed. Keep critical sections short: a boosted owner
still serializes every waiter through L's code.

## The scheduler in one word: a bitmap, a CLZ, and per-priority lists

Dispatch is one lookup: *which ready task has the highest priority?*
O(1) no matter how many tasks exist:

```
 FreeRTOS (CM3/CM4 port)                       Linux O(1) runqueue (module 07 ch04)
 uxReadyPriorities = 0x00000092                per-CPU rq: two arrays, active+expired
   bit 7, 4, 1 set                               140 priority lists each
   31 30 ... 8 7 6 5 4 3 2 1 0                   140-bit bitmap per array
    .  .     . 1 . . . 1 . . 1 .                 sched_find_first_bit(map)
              |       |     |
              v       v     v                    epoch ends: swap(active, expired)
  prio 7: [TCB-A]<->[TCB-B]   <- runs            RT 0-99 / nice 100-139
  prio 4: [TCB-C]
  prio 1: [TCB-D]<->[TCB-E]   round-robin within a level = head to tail
  CLZ(0x92) = 23; 31 - 23 = 7 -> level 7, head
```

One 32-bit word holds one bit per priority level — **32 levels per
word** (fast path: `configMAX_PRIORITIES` ≤ 32). Each set bit points
at a per-priority ready list; the winner is the highest set bit,
sold by the ISA as **one instruction: CLZ**, one cycle. Module 17
chapter 03's RTL returning: "count zeros" earns transistors for
priority encoding (plus FP normalization). Round-robin within a
level moves the head to the tail (`configUSE_TIME_SLICING`).

Drawn side by side, the lineage is the point: Linux's O(1) scheduler
(2.5/2.6, 2002-2007) is the *same shape* — bitmap, one list per
priority — at 140 levels, per CPU, with an active/expired fairness
swap. And **the RTOS got there first**: VxWorks- and QNX-class bitmap
schedulers date to the 1980s. Linux dropped the shape for CFS in
2.6.23 (2007) — desktop priorities are dynamic; FreeRTOS keeps it —
embedded priorities are fixed: no epochs, one word. Module 07 chapter
04's runqueue and this bitmap are one idea at 10⁵× the scale, named
both ways.

**O(1) dispatch answers "who," never "whether."** For the other
question, rate-monotonic analysis, one page:

1. Fixed priorities, periodic tasks: priority = 1/period (shortest
   first) — RMA, the assignment your bitmap encodes.
2. Utilization U = Σ Cᵢ/Tᵢ. Liu-Layland: schedulable if
   **U ≤ n(2^(1/n) − 1)**.
3. The bound's logic in five lines: the worst case is the *critical
   instant* — all jobs release together; the lowest task's response
   then satisfies R = Cₙ + Σᵢ ⌈R/Tᵢ⌉ Cᵢ (each higher task interferes
   ⌈R/Tᵢ⌉ times inside R); the tightest bad case puts that fixed point
   on the deadline; solving the worst alignment gives the bound. As
   n → ∞, n(2^(1/n)−1) → **ln 2 ≈ 0.693** (2^(1/n) = e^(ln2/n)).
   Harmonic periods relax the bound to 1.0.

n: 1 → 1.00, 2 → 0.828, 3 → 0.780, 5 → 0.743, ∞ → 0.693.

Worked example, WCETs measured (not guessed):

| task | period | WCET | RMA priority |
|---|---|---|---|
| sensor | 10 ms | 2 ms | 3 (highest) |
| control | 20 ms | 4 ms | 2 |
| telemetry | 50 ms | 12 ms | 1 |

U = 0.20 + 0.20 + 0.24 = **0.64 ≤ 0.780** → schedulable, margin
0.14. Ship it. Then the feature lands: telemetry's fusion update
measures **34 ms**; U = 1.08 — above 100% total demand, a miss is
certain, and the timeline shows which and when:

```
ms:    0    10   20   30   40   50   60
T1 10/2  [##]  [##]  [##]  [##]  [##]  [##]        # sensor, 2 ms
T2 20/4  [####]    [####]    [####]--->[####]      # control, 4 ms
T3 50/34 [---------34 ms, preempted 5x----------]  @ deadline 50: 2 ms still owed
                                                          |
        t=50: T1 & T2 release, outrank T3's remainder ->   v
        50-52 T1, 52-56 T2, 56-58 T3 finishes -> job 1 LATE by 8 ms
        job 2 starts 8 ms behind; at U > 1 the backlog grows.
```

Note what the bound is *not*: necessary — between 0.693 and 0.780 a
set may still schedule (response-time analysis: iterate
R = Cₙ + Σ⌈R/Tᵢ⌉Cᵢ to a fixed point, compare to D). The honest
summary: **an RTOS is not real-time by itself.** The kernel sells
bounded *mechanisms* — the 150-cycle switch, the O(1) dispatch, the
priority-ordered wake — and the datasheet prints "real-time" as if
the guarantee shipped in the 10 KB. The guarantee is offline
arithmetic against measured WCETs: a tool for building real-time
systems, not one.

## Walls between tasks: FreeRTOS-MPU

Chapter 01's arithmetic returns with a job. The M4 MPU has **8
regions**, 32-byte granularity, power-of-two size and alignment. The
FreeRTOS-MPU port spends them: 3 fixed globals (flash RO+X, kernel
RAM privileged-RW, peripherals privileged-RW), leaving **3-4 per
task** — its stack (RW) plus 2-3 shared buffers. That is the scarcity
arithmetic: a task needing more windows aliases buffers into one
region — the pressure keeps µC isolation deliberately coarse.

The switch rewrites the world: entering an unprivileged task, the
port writes its regions — `RNR` (select), `RBAR` (base), `RASR`
(attrs): 3 registers × 3-4 regions ≈ a dozen `MSR`s plus memory-
system sync and pipeline discard — tens of cycles, the order of the
FPU save, folded into the 150-200 cycle switch. What it buys is
containment: the UI task's wild pointer raises a MemManage fault
naming the address and task — instead of silently overwriting the
control task's stack and shipping. Module 15 chapter 04's containment
story at 10²-10³× smaller scale:

| | Container (module 15 ch04) | FreeRTOS-MPU task |
|---|---|---|
| what's walled | a process's kernel views | a task's addresses |
| mechanism | MMU page tables per process + TLB | 8 MPU regions, rewritten per switch |
| granularity | 4 KB pages, unlimited | 32 B-4 GB, power-of-2, 8 total |
| per-switch cost | one base-register write + TLB refill | ~12+ region-register writes |
| memory cost of the walls | page tables, KB-MB per process | zero — regions are registers |
| wall failure | OOPS / signal / container killed | MemManage fault names the task |
| scale | GB RAM, GHz, 10⁴ processes | 8-512 KB RAM, 10-100 tasks |

~1 KB of protection state instead of a virtual-memory subsystem: same
idea, three orders of magnitude apart.

## What insiders know

- **Tickless idle is a power story.** At 1 kHz SysTick wakes the
  core every millisecond; WFI between ticks burns run-adjacent
  current. `configUSE_TICKLESS_IDLE` suppresses the tick in the idle
  hook and re-arms a low-power timer (RTC/LPTIM) for the next
  *deadline* (`vTaskStepTick`). A CR2032 holds 220 mAh: stop mode at
  2 µA = 110,000 h ≈ **12.5 years**; the ticking core at ~500 µA =
  18 days; waking once per 10 s for 10 mA × 2 ms averages ≈ 4 µA ≈
  **6 years** — the µA-years regime of meters and tags.
- **Stacks are sized by measurement, 90% ceiling.**
  `uxTaskGetStackHighWaterMark(handle)` returns the minimum free
  words ever seen (the untouched 0xA5 fill). Run the worst case, then
  re-size so the mark sits ≥10% free — "why 128 words" becomes
  measured, not guessed.
- **The nastiest bug class: kernel APIs from the wrong interrupt
  level.** The *non*-FromISR API called in an ISR often "works": it
  suspends the scheduler from interrupt context and corrupts
  wake-bookkeeping under nesting — deferred, silent (or a
  `configASSERT` catch, if enabled). Same class, worse: an ISR
  *above* `configMAX_SYSCALL_INTERRUPT_PRIORITY` calling even the
  FromISR API — the kernel raises BASEPRI to the fence in critical
  sections, but an ISR more urgent than the fence is unmaskable and
  interrupts mid-surgery. Worked: `xQueueReceive` has half-unlinked a
  waiter when the ISR's send re-inserts into the torn list — later,
  dispatch walks `pxNext` into a stale TCB: a hard fault in `list.c`,
  or a task silently unlinked and never scheduled again.
- **ISRs share one stack, and almost nobody sizes it.** All handlers
  run on the MSP: budget = **8 words × (1 + max nesting) + the
  deepest handler chain** (+18 words/level with FP). Nesting 2 and a
  60-word chain: 24 + 60 = 84 words = 336 B — teams size one handler
  and forget the multiplication.
- **The fence is a BASEPRI number that runs backwards.** On Cortex-M,
  *numerically lower = more urgent*: fence 5 means priorities 5-15
  may call the kernel, 0-4 are kernel-free and must not — the
  inversion that makes "I set my UART ISR to priority 1" the bug
  above.

## See it yourself

All labs run on QEMU with FreeRTOS's MPS2 demo (AN385: Cortex-M3 at
25 MHz, no hardware needed):

```console
$ git clone --depth 1 https://github.com/FreeRTOS/FreeRTOS.git
$ cd FreeRTOS/FreeRTOS/Demo/CORTEX_MPS2_QEMU_GCC/build
$ make && qemu-system-arm -machine mps2-an385 -nographic -kernel build/RTOSDemo.out
```

- **The switch pattern.** In `main.c`: a priority-2 task printing
  after `vTaskDelay(1)`, a priority-1 task `vTaskDelayUntil` every 10
  ticks, a 500 ms software timer printing. The five config lines:
  `configCPU_CLOCK_HZ 25000000`, `configTICK_RATE_HZ 1000`,
  `configMINIMAL_STACK_SIZE 128`,
  `configTOTAL_HEAP_SIZE ((size_t)40*1024)`, `configMAX_PRIORITIES 5`.
  Expected: the low task prints once per tick, the high every 10th
  line, the timer every 500th — three "frequencies" interleaved by
  the bitmap.
- **Priority inversion, on and off.** Low takes a mutex and
  busy-loops, medium busy-loops, high wakes and blocks. Print
  `vTaskPriorityGet(NULL)` inside low while holding. With
  `xSemaphoreCreateMutex()` (`configUSE_MUTEXES 1`): low reports
  "boosted to 3" the moment high blocks — inheritance live. With
  `xSemaphoreCreateBinary()` (no inheritance) the same program
  starves high. The two logs are the deliverable.
- **Break a canary.** A task with a 64-word stack and a `char
  buf[512]` it `memset`s, with `configCHECK_FOR_STACK_OVERFLOW 2`:
  `vApplicationStackOverflowHook(task, name)` fires with the *task
  name* — the canary named the stack, one switch late.
- **RMA on paper.** Compute the chapter's table by hand, then a fresh
  set: periods 5/10/40 ms, WCETs 1/2/12 ms. Answer: U = 0.7 ≤ 0.780,
  schedulable — and at WCET 13 ms (U = 0.725) it still passes: the
  bound is not tight.

## How to use this knowledge

- **Interview answers.** "What does a context switch cost?" — 8 + 8
  words, 100-200 cycles on M4, 33 more with the FPU, 0.15% at 1 kHz.
  "Why is dispatch O(1)?" — one bitmap word plus CLZ.
- **Debugging the silent class.** Random faults in `list.c`, or a
  task that stops being scheduled: audit ISR priorities against the
  fence and call sites for the FromISR family. It reproduces only
  with the scheduler mid-surgery.
- **Design numbers.** heap_1 + create-in-main vs heap_4 + runtime
  create is a stated trade; stack budgets come from high-water marks;
  guard regions cost ~1-2% RAM and name silent corruption.
- **Vendor claims.** "Real-time" means bounded mechanisms; ask for
  WCET measurements and run RMA — Pathfinder wasn't fixed by a better
  kernel, just a flag finally set.

## Connections

- **Module 20 chapter 01** — the MPU region arithmetic the guard and
  MPU-tasks spend.
- **Module 20 chapter 02** — the linker script placing `ucHeap`; the
  MSP every ISR shares.
- **Module 20 chapter 03** — the WFI loop that becomes the idle hook;
  the SPSC ring that grows into the queue.
- **Module 20 chapter 05** — the single address space that makes
  MPU-task walls the only containment.
- **Module 07 chapter 04** — the O(1) runqueue: FreeRTOS's bitmap is
  its shape — the RTOS wore it first, Linux proved it scales, CFS
  retired it; the queue repeats the lineage, waitqueue + ring fused
  here, split there.
- **Module 03 chapter 05** — the Pathfinder inversion, retold with
  the mechanism that fixes it (and the one it doesn't).
- **Module 15 chapter 04** — containers vs MPU-tasks: walls at 10³×
  the RAM — the comparison table's two columns.
- **Module 01 chapter 19** — heap_4 is dlmalloc's first-fit
  coalescing core shrunk to ~200 lines.
- **Module 09 chapter 02** — lock ordering: the discipline covering
  what inheritance cannot.
- **Module 17 chapter 03** — CLZ as RTL: the one-cycle priority
  encoder that justified an ISA corner.

## Quiz

1. **Concept.** Derive the latency contract of a cooperative RTOS:
   what single property of your source code bounds every task's
   worst-case latency, and why does adding FreeRTOS *without*
   changing that property fix nothing? What config parameter bounds
   it in the preemptive case, and at what cost per second?
2. **Concept.** The Cortex-M dispatcher finds "highest set bit" with
   CLZ in one cycle. Why is dispatch O(1) no matter how many tasks
   exist, why did the ISA spend silicon on CLZ (module 17 chapter 03),
   and why does Linux's O(1) scheduler need *two* priority arrays
   where FreeRTOS needs one word?
3. **Predict the number.** `xTaskCreate(vUart, "uart", 64, NULL, 1,
   NULL)` on a Cortex-M4. (a) How many bytes of stack does the task
   get? (b) Why is it likely to die on its first nested call chain?
   (c) With `configMINIMAL_STACK_SIZE` at 128, how big is the idle
   task's stack?
4. **Predict the number.** A 168 MHz M4F, 1 kHz tick, average 180
   cycles per switch (FP tasks). What fraction of the CPU does the
   scheduler burn? At what tick rate would that reach 5%?
5. **Predict the number.** Periodic tasks: (8 ms, 1.6 ms), (16 ms,
   3.2 ms), (64 ms, 20 ms). Compute U, the Liu-Layland bound, the RMA
   priority assignment, and the verdict.
6. **Bug diagnosis.** A product hard-faults in `list.c` every few
   days; when it doesn't fault, an unrelated task occasionally stops
   being scheduled forever. The UART ISR is at preemption priority 1
   and calls `xQueueSendFromISR`; the kernel fence
   (`configMAX_SYSCALL_INTERRUPT_PRIORITY`) is 5. Explain both
   symptoms from one mechanism, and why the lab never sees it.
7. **Bug diagnosis.** A 100 ms software timer drifts to ~140 ms
   exactly when another timer's callback runs a slow flash write; a
   10 ms timer drifts too, though its callback is 3 lines. Explain
   via the daemon, and give the fix.
8. **Cross-chapter.** Module 03 chapter 05's Pathfinder reset: name
   the three priorities in the story, the mechanism FreeRTOS applies
   the instant the high-priority task blocks, and the failure mode
   inheritance does *not* cover — plus the module 09 chapter 02
   discipline that does.

<details>
<summary>Answers</summary>

1. The longest stretch between `taskYIELD()` calls in *any* task —
   latency is a property of source, re-derived by every edit (the
   123 ms SPI frame). An RTOS with the same unyielding code inherits
   the same 123 ms: yield points are still the only switch
   opportunities. Preemption bounds it with the SysTick quantum
   (1-10 ms) plus the switch: 1,000 × 150 = 150,000 cycles/s — 0.15%
   of a 100 MHz core.
2. Task count never enters: the bitmap has one bit per *priority
   level* (32 per word) — a constant-size word — and CLZ picks the
   highest set bit in one cycle regardless of the TCBs hanging on the
   lists. CLZ exists because priority encoding (and FP normalization)
   is worth a one-cycle circuit — module 17 chapter 03. Linux needs
   active + expired arrays to demote a running task without losing it
   (an epoch swap); FreeRTOS has fixed priorities and no fairness
   budget, so one word suffices.
3. (a) 64 words × 4 B = **256 bytes**. (b) That is the whole stack: a
   `printf` family or modest call chain needs 200-400 B, and overflow
   smashes the data below — the 0xA5 canary catches it one switch
   late. (c) 128 words = **512 bytes** — `configMINIMAL_STACK_SIZE`
   is words, the same unit trap.
4. 1,000 × 180 = 180,000 ÷ 168,000,000 = **0.107% ≈ 0.11%**. 5% of
   168 MHz = 8.4 M cycles/s ÷ 180 ≈ 46,600 — a **47 kHz** tick rate,
   which no one should want.
5. U = 0.20 + 0.20 + 0.3125 = **0.7125**; bound for n = 3:
   3(2^(1/3)−1) = **0.780**. Priorities: 8 ms highest, then 16 ms,
   then 64 ms (priority = 1/period). 0.7125 ≤ 0.780 → **schedulable**
   — margin 0.067, thin enough that WCET 20 → 24 ms (U = 0.775) hits
   the edge and 26 ms (0.806) breaks it.
6. The ISR sits *above* the BASEPRI fence (numerically lower = more
   urgent), so the kernel's critical sections — which raise BASEPRI
   to 5 — cannot mask it: it interrupts list surgery mid-surgery. The
   half-unlinked waiter plus the ISR's insertion tears the circular
   list; dispatch then walks `pxNext` into a stale TCB → the
   `list.c` fault, or the victim TCB is silently unlinked → the task
   never runs again. The lab never sees it: it needs the ISR inside a
   specific switch-path window — rare at bench rates, days at
   production rates. Fix: move the ISR to priority ≥ 5, or defer the
   kernel call to a task.
7. All timer callbacks run serially in the one "Tmr Svc" daemon:
   every callback is delayed by every other. The flash-write
   callback's ~40 ms holds the daemon, so the 100 ms callback fires
   late; the 3-line callback is late for the same reason — its
   runtime is irrelevant, its queue position isn't. Fix: the slow
   work belongs in a *task* signaled by the callback, never in the
   callback itself.
8. Low = the meteorological task publishing on the bus (holding the
   mutex); medium = the communications task that preempted the
   publisher; high = the bus-management task that blocked and starved
   behind medium until the watchdog reset. FreeRTOS's mechanism when
   high blocks: *inheritance* — the owner's `uxPriority` is raised to
   the blocked task's (`uxBasePriority` remembers home), so low
   finishes at high urgency. Inheritance does not fix deadlock —
   boosted owners can still cycle on two mutexes; the cure is
   acquisition order, module 09 chapter 02's lock-ordering rule,
   never reversed.

</details>
