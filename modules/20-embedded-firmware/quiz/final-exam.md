# Module 20 final exam — Embedded & firmware: the world with no MMU

Twelve questions in five parts: three on chapter 01 (the no-MMU world),
three on chapter 02 (bare metal and the linker script), two on chapter 03
(interrupts and DMA), two on chapter 04 (the RTOS), two on chapter 05
(firmware security) — 3+3+2+2+2. Closed book;
every answer is worked, not stated — the derivations are the point.
Scoring: 11+ = you own this module; 8-10 = solid, revisit the flagged
chapters; <8 = the chapter quizzes are the study path.

**Part I — the no-MMU world (chapter 01)**

1. **Derive.** A Cortex-M4 with its 8 MPU regions must support,
   statically: all 512 KiB of flash (RO+X), a 16 KiB kernel-data block
   (RW+XN), two unprivileged tasks each with its own 2 KiB stack
   (RW+XN) plus a 32-byte guard region below it, one 2 KiB DMA-buffer
   region (RW), and the flash-controller registers privileged-only.
   Count the regions and derive what the design cannot afford.
   Compute the exact `MPU_RASR` word for (a) one task's stack at
   `0x20008000` and (b) its guard at `0x20007FE0` — and name the two
   standard escapes when a third task is added.

2. **Diagnose.** Working F407 firmware is ported to an nRF52840 with
   the peripheral `#define`s copied unchanged. The product
   hard-faults "at random" — hours apart, always on a rare logging
   path — and the fault frame's stacked PC names a function that had
   already returned. Occasionally there is no fault at all, just a
   garbage sample. SCB->CFSR (0xE000ED28) reads BusFault with
   IMPRECISERR set. Derive the mechanism, both symptoms, and the fix
   that is not "add retries."

3. **Diagnose.** A meter counts pulses on one flag and keeps time on
   another, in one read-to-clear status register:

   ```c
   if (IRQ_STATUS & FLAG_PULSE) {
       if (IRQ_STATUS & FLAG_2MS_TICK) handle_tick();
       handle_pulse();
   }
   ```

   Field data: the tick handler runs far under rate whenever pulse
   traffic is heavy, individual pulses vanish now and then — and the
   anomaly never reproduces under the debugger. Derive both losses,
   why single-stepping makes it vanish, and write the correct two
   lines.

**Part II — bare metal: the linker script is the memory model (chapter 02)**

4. **Predict the number (RAM budget from the `.map`).** The map
   excerpts: `.data` VMA `0x20000000` size `0x1C0` (LMA in flash);
   `.ramfunc` VMA next, size `0x120` (`>RAM AT> FLASH`); `.bss`
   (NOLOAD) size `0x5B40`; `._user_heap_stack` reserves
   `_Min_Heap_Size = 0x200` and `_Min_Stack_Size = 0x400`; the
   theorem is `ASSERT(_end + 0x600 <= _estack)` with
   `_estack = 0x20008000`, RAM = 32 KiB. (a) RAM committed by the
   theorem. (b) Headroom. (c) The team adds `static uint8_t
   rx_ring[6144];` and `static uint8_t dma_buf[2048];` — does the
   build link, and if not, by how much? (d) Worst-case interrupt
   nesting is 2-deep, each handler's own frame is 40 B, the deepest
   thread-mode call tree is 880 B: audit the 0x400 reservation and
   give the sizing verdict.

5. **Predict.** A flash-burn routine is marked
   `__attribute__((section(".ramfunc")))`; the script places
   `.ramfunc > RAM AT> FLASH` and emits `_siramfunc/_sramfunc/
   _eramfunc` — but the startup code is the stock vendor file, whose
   copy loop moves only `.data`. What is at the routine's address the
   first time it is called, what does the call do (a) on real silicon
   and (b) under QEMU, and what is the two-line fix?

6. **Diagnose.** To reclaim flash the team builds with
   `-ffunction-sections -Wl,--gc-sections`. The image shrinks by
   9 KiB and the board hard-faults before reaching `main`; the map
   shows no `.isr_vector`. Derive why the linker's reachability
   analysis drops a table that hardware reads, what word[0] of the
   new image now is and what the core does with it, and the one-word
   fix.

**Part III — interrupts and DMA (chapter 03)**

7. **Derive.** The 16-line SPSC ring is ported with three changes,
   reviewed as "the same queue, generalized": `put` is now called
   from two ISRs (UART and ADC), both `__DMB()`s are deleted ("the
   M4 is in-order"), and the indices become signed `int` ("cleaner
   comparisons"). Derive the ring's safety conditions from first
   principles — one writer per index above all — then map each change
   onto the condition it violates and the field symptom it produces,
   and compute when the signed version first corrupts if the producer
   side runs at 1,000 puts/s.

8. **Predict the number.** Audio out on a 96 MHz M4: 48 kHz stereo
   16-bit, DMA ping-pong with 512-byte half-buffers, the TC ISR
   re-arming the finished half. The system's longest
   interrupt-disabled window is 2,200 cycles; a higher-urgency flash
   write can run 3 ms. (a) Bytes/s. (b) The TC period and its re-arm
   deadline. (c) The disabled window in µs — is it the problem? (d)
   The flash write: glitch or not, in bytes? (e) Bytes to ride out
   5 ms of jitter; and the cost of a 200 ms safety margin (ping-pong,
   ×2) as a fraction of a 128 KiB part.

**Part IV — the RTOS (chapter 04)**

9. **Predict the number (the heap is a config flag).** FreeRTOS: five
   application tasks at `usStackDepth` 128 plus the idle task
   (`configMINIMAL_STACK_SIZE` 128), one queue of 16 items × 4 bytes
   (queue object 76 B), TCBs ~90 B, 8 B allocation header per heap_4
   block. (a) Derive the bytes and the minimum
   `configTOTAL_HEAP_SIZE`. (b) Where do those bytes live at link
   time? (c) A reviewer says "128 is plenty — each task uses only 64
   bytes." What does the third argument of `xTaskCreate` actually
   say, and what happens to a task given 64 with a 300 B call chain?
   (d) What does `xTaskCreateStatic` buy that heap_4 cannot?

10. **Predict.** Three periodic tasks: sensor 4 ms / WCET 1 ms,
    control 12 ms / 3 ms, telemetry 24 ms / 9 ms. (a) Compute U and
    the Liu-Layland bound for n = 3 — verdict? (b) The periods are
    harmonic: what changes, and what is the honest verdict? (c) The
    fusion update measures 13 ms: recompute U, and construct which
    deadline is missed first, by how much. (d) In all three cases,
    what did the kernel itself guarantee?

**Part V — firmware security (chapter 05)**

11. **Walk the timeline (power loss through the A/B state machine).**
    The v7→v8 update writes the new image to bank B, then performs
    two marker operations: PREPARE (write `gen6 -> B` into the spare
    slot) and COMMIT (erase the old `gen5 -> A` marker); markers are
    CRC'd `{generation, bank}` records and the bootloader picks the
    highest *valid* generation. (a) Walk all four power-cut points of
    the correct order: what is on disk, what the bootloader picks,
    and whether rollback is still legal. (b) A reviewer proposes
    erasing the old marker *first* ("the spare slot is wasted"). Walk
    the reordered sequence and identify every state from which no
    valid marker survives. (c) Where does the anti-rollback eFuse
    burn in this sequence, and why exactly there?

12. **Predict the number.** A boot ROM's signature check is a
    200-cycle sequence; the go/no-go branch occupies one cycle. A
    $300 glitch rig lands one randomly placed skipped instruction
    per boot; a reset-boot cycle costs 5 s. (a) Check-once: the
    per-boot win probability and the expected time to own the
    device. (b) Check-twice, the second check 80 cycles later and
    re-derived: why one glitch cannot win, and the expected time.
    (c) Which rung of the cost ladder is this, what does the
    countermeasure cost, and why must the second check re-derive its
    boolean rather than copy it?

<details>
<summary>Answers</summary>

**Part I**

1. Count: flash 1 + kernel 1 + two task stacks + two guards + DMA 1
   + flash-controller 1 = **8 — the entire budget, zero spare.** The
   design can afford exactly two guarded tasks; a third task, a
   second non-cacheable buffer, or an .rodata write-protect each
   costs something already spent — 8-16 regions is a *scarce*
   protection state, not a page table. (a) 2 KiB = 2^11 so
   SIZE = 10; XN (bit 28, 0x10000000) | AP = 3 (0x03000000) |
   (10 << 1) | ENABLE = **0x13000015**; 0x20008000 is 2 KiB-aligned,
   valid. (b) 32 B = 2^5 so SIZE = 4, AP = 0, enabled:
   (4 << 1) | 1 = **0x00000009**; 0x20007FE0 is 32-byte-aligned and
   abuts the stack base, so the *first* stray down-growing write
   faults — MemManage, faulting PC stacked in the frame, ch01's
   precise method-3 overflow check. Escapes for a third task: alias
   two tasks into one shared RW region (isolation halved), or
   reprogram per-task regions at every context switch —
   FreeRTOS-MPU's arithmetic: 3 fixed globals + 3-4 regions
   rewritten per switch, tens of cycles folded into the ~150-cycle
   switch (ch04).

2. The `#define`s are F407 wiring; on the nRF part that range is a
   **reserved hole** — the map is per-family, not per-ARM (only
   0xE0000000, the ARM system region, is standard). Loads/stores
   into a hole raise a BusFault, and the write buffer makes it
   **imprecise**: IMPRECISERR means the fault surfaces some
   instructions *after* the offending access, so the stacked PC names
   the function that already returned — fault forensics one call too
   late. The no-fault/garbage runs are the per-SOC escape: some
   decode gaps return the last bus value or garbage instead of
   faulting. "Hours apart" because only a rare logging path touches
   the register. Fix: per-family vendor headers, never copied
   addresses — "which chip, which revision?" — and keep the
   log-and-reset policy for the residual.

3. The register is **read-to-clear: every read consumes every
   pending flag.** Read #1 (the pulse test) already clears a
   simultaneously pending tick flag, so the inner test can never see
   it — `handle_tick` loses every tick coinciding with a pulse, hence
   the under-rate that tracks pulse traffic. Read #2 (the tick test)
   clears whatever latched *between* the two reads — including a
   fresh pulse bit that no branch ever tests: the vanished pulses.
   Two reads, one handled flag each at best — the side effect lives
   in the read half. The debugger: the watch window's refresh *is* a
   read, consuming flags while you watch, so the interleaving you are
   observing never occurs free-running — ch01's "flags evaporate
   while you watch / the bug disappears when I single-step"
   signature. Fix — read once into a local, test bits on the local:

   ```c
   uint32_t st = IRQ_STATUS;              /* the ONE consuming read */
   if (st & FLAG_2MS_TICK) handle_tick();
   if (st & FLAG_PULSE)    handle_pulse();
   ```

**Part II**

4. Static sum = 0x1C0 + 0x120 + 0x5B40 = 0x5E20, so
   `_end = 0x20005E20`. (a) Committed = `_end + 0x600` =
   **0x20006420 = 25,632 B**. (b) Headroom = 0x20008000 − 0x20006420
   = **0x1BE0 = 7,136 B**. (c) 6,144 + 2,048 = 8,192 B > 7,136:
   **the build refuses to link, short by 1,056 B** — the ASSERT is a
   theorem over the closed world of static allocations, and the
   binary is unproducible: oversubscription cannot *ship* (module
   19's soundness in five seconds). The arrays shrink or RAM grows;
   "it fit yesterday" is not a state you may occupy. (d) Per nesting
   level: 32 B auto-stacked frame + 40 B handler = 72 B; × 2 levels =
   144 B; + 880 B deepest thread tree = **1,024 B — exactly the 0x400
   reservation: zero margin.** Verdict: the reservation is a claim
   and the nesting arithmetic is its audit — run the 0xDEADBEEF
   pattern fill under worst case, scan for the high-water mark, and
   write measured + 25% (1,280 → 0x500) into `_Min_Stack_Size`.

5. Nothing copies the routine: at first call its VMA in SRAM holds
   power-up garbage (real silicon) or QEMU's zero-fill — the bytes
   exist only at the LMA in flash. (a) On silicon the call lands on
   random bytes: undefined instructions or garbage execution,
   HardFault or silent corruption. (b) Under QEMU the zeroed SRAM
   decodes 0x0000 as a valid `lsls`-class instruction and the PC
   slides through a NOP sled until it hits real code — "works in the
   emulator, dies in the field," the same zero-fill masking that
   hides a deleted `.bss` loop. Fix — the copy loop `.data` already
   uses, duplicated for the ramfunc symbols:

   ```c
   uint32_t *src = &_siramfunc, *dst = &_sramfunc;
   while (dst < &_eramfunc) *dst++ = *src++;   /* LMA -> VMA, again */
   ```

   One routine, two homes, reconciled at rung 7 — ch01's ramfunc
   trick is ch02's LMA/VMA mechanism with a section name of your
   choosing.

6. **No instruction references the vector table** — hardware, "an
   agent outside the ELF," is its only reader, and gc-sections
   garbage-collects unreferenced input sections. With the table
   gone, the image begins with `.text`: word[0] — which fetch
   hardware loads into MSP at reset — is now **instruction bits**, a
   garbage stack pointer; word[1] "PC" is not a handler entry, so the
   core jumps into mid-code on a corrupt stack and HardFaults before
   the first C instruction. The fix is one word: **`KEEP(*(.isr_vector))`**
   in the script — the annotation that an agent outside the ELF
   reads this; the C-array version's `__attribute__((used))` is the
   same annotation. The rule generalizes: anything whose only reader
   is not the linker's reachability graph (hardware tables, DMA
   descriptors nothing calls) is one build away from vanishing.

**Part III**

7. The safety conditions, each with the failure it prevents: (1)
   **single writer per index** — `head` written only by the producer
   side, `tail` only by the consumer; (2) data published *before* the
   index (put's DMB — release); (3) index read *before* the data in
   get (acquire pairing); (4) wrap-safe unsigned arithmetic —
   `h - t == N`, never a signed comparison; (5) the index is one
   aligned 32-bit store, v6-M's only atomicity promise. The port:
   **two producers** = two writers of `head` — both ISRs read the
   same `h`, both write `buf[h & 63]`, one byte overwrites the other
   and an increment is lost: corruption exactly when the ISRs
   coincide, and no barrier fixes a design error (one ISR must own
   `head`, or the index needs ldrex/strex). **DMB removal** breaks
   (2) and (3): on an in-order M4 the barrier mostly stops the
   *compiler*, which may sink the data store past `head = h + 1`
   (consumer reads a fresh index over stale bytes — over-read) and
   float the tail store above the slot load (producer over-writes
   mid-read — the checksum-divergence flavor of lab 2). **Signed
   indices** break (4): at 1,000 puts/s the 2^31 wrap arrives after
   2^31/10^3 ≈ 2,147,484 s ≈ **24.9 days** of uptime, then `h - t`
   goes negative, full and empty both lie, and the ring floods or
   wedges — the "perfect for a month in the field" signature.

8. (a) 48,000 × 2 ch × 2 B = **192,000 B/s**. (b) 512 B ÷ 192,000
   B/s = **2.67 ms per half** — with the other half already armed,
   the TC ISR's re-arm deadline is one half-period, ≈ 2.67 ms.
   (c) 2,200 + 12 cycles = 2,212 ÷ 96 MHz = **23 µs = 0.9% of the
   deadline — the disabled window is not the problem**; the thing
   that outranks you is. (d) 3 ms > 2.67 ms: the pipeline runs dry
   by (3 − 2.67) ms × 192,000 B/s ≈ **64 B = 16 stereo frames of
   silence — the audible click**. (e) 5 ms × 192,000 = **960 B
   pre-buffered**; a 200 ms margin, ping-pong ×2, is 200 ms ×
   192,000 × 2 = 76,800 B ≈ **59% of a 128 KiB part** —
   "audio-capable" is a RAM-size class, not a CPU-speed class.
   Verdict: correctness here is function + timing; fix the priority
   structure (flash write below the TC ISR, or chunk the write),
   don't buy RAM to hide a scheduling bug.

**Part IV**

9. (a) Per task: TCB 90 + 8 header = 98; stack 128 words = 512 + 8 =
   520; ≈ 618 B. Six tasks (five + idle) = 3,708; queue = 64 storage
   + 76 object + 8 = 148 → **3,856 B, so `configTOTAL_HEAP_SIZE` =
   4 KiB** with ~6% headroom. (b) The heap is `ucHeap[configTOTAL_
   HEAP_SIZE]` — a static array in **`.bss`**: zero file bytes,
   counted by ch02's ASSERT theorem, and OOM strikes at boot (create
   every task and queue in `main` before `vTaskStartScheduler`)
   where you can see it. The "heap" is a config flag, not a
   subsystem. (c) The third argument is `usStackDepth` in
   **`StackType_t` words — 128 words = 512 B, not 128 B.** Given 64,
   the task owns 256 B; a 300 B call chain overflows downward,
   smashing the 0xA5 fill — method 1's canary fires at the *next
   context switch*, naming the task in the overflow hook one switch
   late (the MPU guard faults the write itself). (d)
   `xTaskCreateStatic` puts stacks and TCBs in your `.bss` with
   sizes the linker proves: heap_1's "allocation is bring-up only"
   discipline enforced by the linker script instead of convention —
   fragmentation becomes a link error, i.e. impossible.

10. (a) U = 1/4 + 3/12 + 9/24 = 0.25 + 0.25 + 0.375 = **0.875**;
    Liu-Layland for n = 3 is 3(2^(1/3) − 1) = **0.780**. 0.875 >
    0.780: the sufficient test *fails* — the bound cannot prove this
    set. (b) The periods divide (4 | 12 | 24): **harmonic, and the
    bound relaxes to 1.0** — 0.875 is schedulable with margin 0.125.
    Ship it; the bound is sufficient, not necessary, and harmonic
    sets are exactly where the tight answer is U ≤ 1. (c) U =
    0.25 + 0.25 + 13/24 = **1.042 > 1.0 — demand exceeds supply, a
    miss is certain.** RMA priority = 1/period: sensor > control >
    telemetry. Timeline from t = 0: the 1 ms sensor job every 4 ms
    and the 3 ms control job every 12 ms consume 12 of the first
    24 ms, leaving telemetry 12 of its 13 — **its first deadline
    (t = 24) is missed by 5 ms** (it finishes at t = 29, after the
    sensor and control jobs released at 24 are served), and job 2
    starts 5 ms behind with U > 1: the backlog grows monotonically
    while sensor and control still meet theirs. (d) Nothing: the
    kernel sells bounded *mechanisms* — the ~150-cycle switch, O(1)
    CLZ dispatch, priority-ordered wake — never deadlines. "Real-time"
    is offline arithmetic against measured WCETs; the datasheet's
    word is marketing.

**Part V**

11. (a) **CUT 1** (mid-write of v8 into B): A = signed v7, B = torn,
    markers unchanged (`gen5 -> A`) → boots A, update restarts —
    harmless by construction: *the pointer never moves until the
    target is complete and signed.* **CUT 2** (torn PREPARE): B =
    complete signed v8; the spare slot's marker is torn (CRC fails,
    ignored), the old `gen5 -> A` is valid → boots A; the update
    re-runs step 2 only. **CUT 3** (mid-erase of the old marker):
    both markers valid — or erase done, only `gen6 -> B` → boots B,
    complete and signed. No single flash write is atomic; **the
    tie-break rule — highest valid generation — resolves every
    intermediate state to a valid image**: the flip is atomic by
    policy, not by hardware. **CUT 4** (B booted, unconfirmed): B
    runs in *trial* mode — N boots to confirm, else the bootloader
    re-points at A. Rollback is legal at every cut: the fuse has not
    burned and bank A still holds v7. (b) Erase-old-first: a cut
    *during the erase* leaves the only marker half-erased → **zero
    valid markers**; and if the following write-new is itself cut,
    the sole slot holds a torn marker → **zero again**. The reorder
    opens a two-cut window with no valid marker — the one
    unrecoverable state, a brick. The correct order's invariant is
    "at least one valid marker at every instant" — module 14 ch03's
    journal ordering: the write order is a design rule, not a style
    choice. (c) The eFuse burns to c = 8 **only after the app
    confirms a successful boot of v8** — never at download or first
    boot: burn early and a lemon update makes the trial's legal
    rollback impossible, because the one-way counter then refuses
    the only good image — the boot-looping fleet.

12. (a) One glitch must land on the single branch cycle: p = 1/200
    = **0.5% per boot** → ~200 boots × 5 s ≈ **17 minutes** of
    resetting (the chapter's simulation: 67/10,000 = 0.67%, same
    order). (b) Both branch cycles — say 100 and 180 — must be
    skipped in the *same* boot: (1/200)² = 2.5 × 10⁻⁵ → ~40,000
    boots × 5 s ≈ **2.3 days of uninterrupted glitching**, and the
    80-cycle spacing means one *wide* glitch cannot span both
    checks. The attack economics flipped for the price of a second
    verify. (c) Rung 1 — the $300 ChipWhisperer; the countermeasure
    is *code*: check-twice plus fault-detecting control flow (a
    token walked through registers and checked after the jump —
    module 02's CFI shrunk to the branch scale), tens of bytes,
    ~zero runtime. The second check must **re-derive** its boolean:
    a copied `ok2` lives in the register one glitch corrupts — both
    branches then test the same corrupted value and both "pass";
    only an independent re-verification is unsatisfiable by a single
    corrupted result. And note the target: the *check*, not the
    crypto — fault injection buys a skipped branch, never a
    forgery.

</details>
