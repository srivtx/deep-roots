# Module 17 final exam — Digital hardware: from transistors to silicon

Twelve questions across all five chapters. Closed book; every answer is
worked, not stated — the derivations are the point. Scoring: 11+ = you own
this module; 8-10 = solid, revisit the flagged chapters; <8 = the chapter
quizzes are the study path.

**Part I — from switches to gates (chapter 01)**

1. **Predict.** A governor drops a core's supply from 1.2 V to 0.8 V at
   fixed workload and frequency. (a) By what factor does dynamic power
   fall, and which symbol of which equation produced it? (b) Why must
   the clock follow the voltage down in a real system — what happens to
   each 0.69·RC as V drops? (c) In 2004 the same equation, plus one
   term that grew up at 90 nm, killed the 4 GHz single-core roadmap and
   forced the multicore pivot. Name both terms and the two
   architectural responses.

2. **Predict.** (a) A standard-cell library quotes NAND2 at 4
   transistors and AND2 at 6. Derive both counts from the switch
   networks, derive the 0.69·RC the trailing inverter pays, and state
   the bias of CMOS the price list reveals. (b) A 2.5 GHz core on a
   ~20 ps/FO4 process: how many FO4 per cycle, and why do insiders
   budget in FO4 rather than nanoseconds? (c) Convert an 80 ns DRAM
   access to FO4 and to cycles, and give the one-sentence consequence
   for module 01's hierarchy.

3. **Diagnose.** A board samples an asynchronous 10 MHz status line
   with a single flip-flop inside a 200 MHz domain; the flop's
   metastability time constant is τ = 40 ps and the logic gives it
   tr = 2 ns to resolve. Field units crash roughly once a month. (a)
   What is failing, and what MTBF does the formula predict — does a
   month match? (b) Give the standard fix, its cost, and the new MTBF.
   (c) Why is the rule *two* flops, never one — what does the tr/τ
   exponent do? (d) Why does the same fix not extend to a 4-bit
   counter crossing, and what does?

**Part II — state and the pipeline (chapter 02)**

4. **Predict.** A 6 ns combinational datapath; flip-flop overhead
   (t_c2q + t_su) is 0.3 ns. Compute f_max for the unregistered blob
   and for a 5-stage pipeline, the latency of one operation through
   each, and the f_max asymptote as k → ∞. Then add the flush term —
   b = 20% branches, m = 5% mispredicts, penalty ≈ k cycles — and
   derive the optimum depth k*. What exactly does that optimum
   balance?

5. **Diagnose.** A prototype fails intermittently; dropping the clock
   from 2 GHz to 500 MHz to 100 MHz changes the failure rate not at
   all. On the suspect path: launch t_c2q = 70 ps, minimum logic delay
   40 ps, capture t_h = 100 ps, and the clock tree delivers the
   capture edge 30 ps early; the maximum logic delay is 350 ps and
   t_su = 60 ps. Write both inequalities with these numbers, decide
   which is violated, and explain why the clock period cannot appear
   in the guilty one. Give the fixes in the order a team tries them,
   with their costs.

6. **Predict.** A 2-bit saturating branch predictor starts at
   strongly-taken. (a) A loop branch runs taken, taken, not-taken,
   repeating forever: derive the steady-state accuracy and the
   recurring mistake. (b) An alternating taken/not-taken stream from
   a weak start scores 0% — why can't hysteresis save it, and what
   class of predictor can? (c) Prescott: 31 stages, ~39-cycle
   mispredict penalty, 20% branches. Derive the lost CPI at 5% and at
   10% miss rate, then name the two equations that cancelled Tejas.

**Part III — the RTL discipline (chapter 03)**

7. **Predict.** Start a = AA, b = BB, c = CC:

   ```verilog
   always @(posedge clk) begin
       a = b;    // blocking '=' — intent: rotate a<-b, b<-c, c<-a
       b = c;
       c = a;    // c should get the OLD a
   end
   ```

   What do a, b, c hold after one clock edge — here, and under the
   nonblocking `<=` rewrite? Explain the delta-round mechanism, then
   say what synthesis builds from the blocking text.

8. **Predict (cross-module).** A synchronous FIFO has DEPTH = 16
   slots: 4 ring bits plus the one extra pointer bit. (a) After
   reset, 10 pushes then 4 pops — give wptr, rptr, full, empty, and
   occupancy. (b) Ten more pushes: the exact pointer pair, and the
   flag arithmetic that asserts full. (c) Derive why the extra bit
   must exist: count the occupancies against the naive gap values,
   and write the fixed full/empty equations. (d) io_uring's
   submission ring (module 13 ch04) keeps head and tail as full
   32-bit indices — which of the two fixes is that, and why does the
   ambiguity never arise there?

9. **Diagnose.** A block's output occasionally holds a stale value
   for a cycle on a fielded product. The RTL is an `always @(*)`
   `case` over a 3-bit select with arms for 000 through 110 and no
   `default`; every regression vector passed. Name the inferred
   hardware, the synthesis log line, why simulation missed it, and
   the fix production lint enforces. Then name two other members of
   this chapter's "sim passes, silicon fails" family.

**Part IV — the cache in RTL and the price of the die (chapters 04-05)**

10. **Predict.** The chapter's direct-mapped cache: 128 lines × 32 B,
    tag[31:12] | index[11:5] | offset[4:0]; line 0x00000180 is
    resident. A trace reads 0x180, 0x2180, 0x180, 0x2180, ... (a)
    Index and tag for each address, and the hit/miss pattern with its
    cause. (b) The trace's overall hit rate is 60%; a hit answers in
    1 cycle, a miss costs 13: derive the average access time, and
    decompose the 13. (c) Does 2-way associativity fix (a) — and why
    do real L1s stop at 6-8 way with way prediction while L3 happily
    runs 16-way? (d) The roadmap wants a 1 MB direct-mapped L1 at the
    same 4-5 cycle hit: apply the bitline's sqrt rule, and say
    whether the next node rescues it.

11. **Predict.** (a) The DIMM sticker says PC4-25600: derive the peak
    from the name, and the fraction refresh leaves usable (tRFC =
    350 ns every tREFI = 7.8 µs) — why can't idleness recover it? (b)
    With controller overhead 10 ns, tRP = tRCD = tCAS = 14 ns, and
    2.5 ns of bus per 64 B: derive a clean write-back miss and one
    that evicts a dirty victim; give the ratio. (c) What does the
    dirty bit buy on a single core, and which MESI state is it
    promoted to when a second cache shares the memory? (d) What is
    write-through's shock absorber, which famous bug class does it
    plant, and what one-line compare closes it?

12. **Derive.** A 300 mm wafer costs $10,000; usable area after the
    3 mm edge ring and ~13% packing loss is ~59,060 mm². Early-ramp
    defect density D₀ = 0.01/mm². (a) For a 300 mm² die: derive the
    yield, gross dies, good dies, and cost per good die. (b) Cut the
    design into 4 × 75 mm² dielets, packaged at $100 each: derive
    packages per wafer and cost per package; then show that the
    same-neighborhood survival probability equals the monolithic
    yield exactly — and say what actually buys the win. (c) At
    mature D₀ = 0.001/mm², recompute both and explain the flip. (d)
    One sentence, with AD102's numbers, on why a GPU is an array of
    100+ identical small cores.

<details>
<summary>Answers</summary>

**Part I**

1. **(0.8/1.2)² = 0.444 — dynamic power falls to 44%, a 56% cut.** The
   lever is the squared V in the dynamic term of P = α·C·V²·f, with α,
   C, and f held fixed. But a real governor must drop f with V: lower
   supply means lower drive current, so every stage's 0.69·RC
   stretches — the voltage-temperature-frequency triangle — and a
   cycle that no longer closes forces the clock down; that coupling
   *is* DVFS. The 2004 wall: the V² term said lower voltage (f
   following), but the leakage term V·I_leak had grown — nA-scale
   leaks × 10⁹ gates × 1.2 V ≈ 12 W of doing nothing, 25-40% of power
   on hot designs by 90 nm — so the 4 GHz single core could not be
   cooled at sane cost, and two slower cores at lower V beat one fast
   core inside the same watts. The responses: **clock gating** (zero
   the α·C·V²·f term on idle blocks) and **power gating** (cut VDD,
   kill V·I_leak); dark silicon is the endpoint (ch01).

2. **4 vs 6 transistors, and 20 FO4 vs 4,000.** The NAND is the
   natural switch network: 2 PMOS in parallel (any 0 input closes
   one, pulling OUT high) over 2 NMOS in series (both 1 to pull OUT
   low) — complementarity guarantees exactly one closed network per
   input, 4 transistors. AND = NAND + inverter = 6, paying 2
   transistors and one extra 0.69·RC, derived from the 50%
   condition: VDD(1 − e^(−t/RC)) = VDD/2 → e^(−t/RC) = 1/2 → t =
   RC·ln 2. The bias: **CMOS is naturally inverting** — NOT, NAND,
   NOR are the minimal shapes; AND/OR buy the trailing inversion.
   FO4 is one inverter driving 4 identical ones (the logical-effort
   optimum of 2.7-4× per stage), and it ports across nodes because
   it is normalized to the silicon's own speed — "15 FO4" travels,
   "300 ps" does not. A 2.5 GHz cycle is 400 ps ≈ **20 FO4** at
   ~20 ps each; an 80 ns DRAM access is **4,000 FO4 = 200 cycles** —
   thousands of FO4 against a 15-20 FO4 per-cycle budget, so the
   hierarchy exists to keep most accesses on chip (ch01; module 01
   ch02).

3. **Metastability — and the month is exactly what the formula
   predicts: MTBF ≈ 2.6×10⁶ s ≈ 30 days.** The async line's
   transitions arrive at arbitrary phase; some land in the sampling
   flop's setup/hold window, and its cross-coupled pair can balance
   at half-voltage, resolving after an unbounded, exponentially
   distributed time. MTBF = e^(tr/τ)/(f_clk·f_data) =
   e^(2 ns/40 ps)/(2×10⁸ · 10⁷) = e^50/2×10¹⁵ ≈ 5.2×10²¹/2×10¹⁵ ≈
   2.6×10⁶ s — about one upset per month, matching the field. The
   fix is the 2-flop synchronizer: the first flop may go metastable,
   but it gets a nearly full period (≈ 4.5 ns) to resolve before the
   second samples — e^112.5/2×10¹⁵ ≈ 3.6×10³³ s, geology; the cost
   is one cycle of latency on that one signal. The exponent tr/τ is
   the whole rule: extra resolution time multiplies MTBF by
   e^(Δtr/τ), which is why one flop is a coin-flip on a 30-day timer
   and two is a different universe (ch01: τ halved once swung 0.86 s
   to 7.7×10¹⁵ years). But the pair fixes one bit: a 4-bit counter
   tears, because bits resolve independently — on 7→8, binary
   0b0111→0b1000 changes four bits at once, and a mid-change sample
   can read 0b1111 or 0b0000, values the counter never held. Cross
   one bit at a time (a handshake flag, itself two-flop
   synchronized) and move the counter in Gray code — 0b0100→0b1100,
   one bit per increment: a torn sample is old or new, never a third
   value (ch01; ch03's FIFO).

**Part II**

4. **167 vs 667 MHz; latency 6 vs 7.5 ns; asymptote 3.33 GHz; k* ≈ 45.**
   Blob: f_max = 1/6 ns = **167 MHz**. Five stages: cycle = D/k + t =
   6/5 + 0.3 = 1.5 ns → **667 MHz**, one result per cycle = 4.0× the
   throughput. Latency through the pipe = k·(D/k + t) = 5 × 1.5 =
   **7.5 ns vs 6 ns** — worse by exactly k·t: pipelining never makes
   any single item faster; it starts the *next* item before the
   current one finishes (module 12's GPUs are the latency-tolerant
   extreme). As k → ∞ the cycle approaches t, so f_max → 1/t =
   **3.33 GHz**: the registers, not the logic, become the clock.
   With flushes, P(k) = k/((D + k·t)·(1 + b·m·k)); setting dP/dk = 0
   collapses to k* = sqrt(D/(b·m·t)) = sqrt(6/(0.2·0.05·0.3)) =
   sqrt(2000) ≈ **45** — shallower wastes clock headroom, deeper
   hands the savings to the flush penalty. The optimum balances the
   two; the Pentium 4 sat past it with the power term omitted (ch02).

5. **A hold violation of 20 ps — and no period anywhere in the
   inequality.** SETUP: T_clk ≥ t_c2q + t_logic(max) + t_su → 500 ps
   ≥ 70 + 350 + 60 = 480 — met by 20 ps. HOLD: t_c2q + t_logic(min)
   ≥ t_h + skew → 70 + 40 = 110 vs 100 + 30 = 130 — **violated by
   20 ps**. The setup inequality contains T_clk, so a bigger period
   heals it — a setup miss costs a speed bin (ch01's binning). The
   hold inequality compares arrival skews around *one* edge: launch
   and capture are the same edge however far apart cycles are, so 2
   GHz, 500 MHz, and 1 Hz fail identically. Fix order: (1) hold
   buffers — delay elements on the too-fast paths, cheap and
   automated, re-run at every PVT corner; (2) if found after
   tapeout, a respin — masks run $1.5-2M at 28 nm and $10M+ at a
   5 nm-class node, plus months. "Hold bugs are the expensive kind"
   is this equation; STA exists to check every register-to-register
   path against both so none is found late (ch02).

6. **66.7%; the blind spot is structural; 0.39 lost CPI.** (a) From
   strongly-taken (11): the two takens are predicted correctly (the
   counter saturates, no motion); the not-taken is predicted taken —
   wrong, demote to 10; the next taken is *still* predicted taken
   (10 is the top half of the counter) and climbs back to 11. Two
   right, one wrong per three = **66.7%**; the recurring mistake is
   the exit, and it is unfixable at this order: the counter sees
   only this branch's own last outcomes and cannot know the loop is
   about to end. (b) Alternating from a weak start: the counter
   lives in 01/10, one step behind the pattern forever — 0% (50%
   from a strong start); hysteresis is exactly what keeps it behind,
   since one contrary outcome only demotes strong to weak. History
   predictors (TAGE, perceptrons) correlate each branch with
   tens-to-thousands of past outcomes and reach 95-99%. (c) 0.2 ×
   0.05 × 39 ≈ **0.39 lost CPI**; a 5-point accuracy slip doubles it
   (0.2 × 0.10 × 39 ≈ 0.78) — that sensitivity made prediction the
   research frontier of 1995-2010. The two equations: f_max =
   1/(D/k + t) bought the GHz with depth while the flush term ∝ k
   ate the IPC, and P = α·C·V²·f + V·I_leak closed the pincer — 31
   stage-register banks inflated C, 3.8 GHz inflated f, holding
   ~115 W forced V down, and the V-T-f triangle stole back the clock
   the depth had bought. Tejas was cancelled in 2004; Conroe's 14
   wide stages at 2.93 GHz won by ~1.5-2× on integer work (ch02).

**Part III**

7. **Blocking: (BB, CC, BB). Nonblocking: (BB, CC, AA).** In round 0
   of the delta cycle, `a = b` lands immediately, so `c = a` reads
   the *new* a — old b copied twice, and AA is lost: later
   statements see earlier blocking updates, so no ordering of these
   statements makes a rotation. Nonblocking reads see pre-edge
   values and queue the writes into the next round (the NBA
   region): a ← old b, b ← old c, c ← old a — a true rotation, which
   is the flip-flop's contract, a value becoming visible only
   *after* the edge. Synthesis reads the blocking text as wires: a
   and c are both driven by old b, so three "registers" collapse
   into two — you described combinational logic, not storage. Lint
   escalates blocking `=` inside `always @(posedge clk)` (Verilator's
   BLKSEQ class) as a firing offense precisely because the hardware
   you meant is not recoverable from the text you wrote (ch03).

8. **wptr = 01010, rptr = 00100, occupancy 6; full at 10100 vs
   00100.** (a) Ten pushes, four pops: wptr = 10 = `01010`, rptr = 4
   = `00100`. full = 0 (the wrap bits are both 0, and the ring bits
   differ); empty = 0 (the pointers differ); occupancy = w − r = 6 —
   not 4, which counts pops, not beats waiting. (b) Ten more pushes:
   wptr = 20 = `10100`. full asserts on **wrap bit differs, ring
   bits equal**: w[4] = 1 vs r[4] = 0, and w[3:0] = r[3:0] = `0100` —
   occupancy 16 = DEPTH. (c) N = 16 slots has **17 legal occupancies
   (0..16)**; a naive 4-bit pair encodes only the gap (w − r) mod 16
   — 16 values. Pigeonhole: gap 0 must carry both occupancy 0 and
   occupancy 16, so no function of 4-bit pointers can separate full
   from empty — the information is not there. The 5-bit pointers
   count mod 32: **empty = (w == r); full = w[4] != r[4] && w[3:0]
   == r[3:0]**. (d) io_uring is the one-extra-bit trick generalized:
   head and tail are unreduced 32-bit counters, so the "extra bit"
   became 2³²/N wrap bits and tail − head *is* the occupancy for
   every value up to 2³¹ — full and empty are raw comparisons,
   unambiguous at any wrap count, because mod-2³² arithmetic absorbs
   wraps the way the 5th bit absorbed the 16-deep FIFO's. Silicon
   pays flip-flops linearly in counter width, so hardware stops at
   one extra bit; a 32-bit field is free in software, so the rings
   take the whole counter (ch03; module 13 ch04).

9. **An inferred latch; "Latch inferred for signal ..."; the vectors
   never drove 111.** With no matching arm and no `default`, the only
   semantics available is "output keeps its value" — a
   level-sensitive latch, state smuggled into a "combinational"
   block, and the stale output appears exactly where the fielded
   select hits 111. Synthesis prints the latch warning; simulation
   honors the hold *silently*, so every regression vector that
   avoids 111 passes. Fix: `default` in every `case` — production
   lint escalates the warning to an error because the bug walks
   through masked tests. Family members: (1) X-optimism — `if (x)`
   with x = X cannot take the true arm, so a bug falls through the
   tested `else`; (2) uninitialized memory — sim starts arrays at X
   or 0 (tool-dependent) while silicon SRAM powers up random, and
   `initial` blocks honored as FPGA bitstream power-up values are
   ignored by ASIC flows ("works on the FPGA, hangs on the ASIC");
   also CDC races masked by the simulator's deterministic delta-cycle
   ordering — the race exists, your simulator just always resolves
   it the same lucky way (ch03).

**Part IV**

10. **Same index, different tags: 100% miss after the first; 5.8
    cycles average.** 0x180: index = addr[11:5] = (0x180 >> 5) &
    0x7F = 12, tag = 0x180 >> 12 = 0. 0x2180: (0x2180 >> 5) & 0x7F =
    268 & 0x7F = 12, tag = 2. Same index, different tags, and
    direct-mapped keeps one line per index: HIT (resident), MISS
    (fills, evicts tag 0), MISS (fills, evicts tag 2), ... —
    alternating forever is 100% miss after the first hit, the lab's
    15-of-16 thrash. (b) 0.6 × 1 + 0.4 × 13 = **5.8 cycles** — the
    miss path dominates everything. The 13: 1 accept, 1 to drive the
    bus, 4 of bus latency, 4 fill beats, 1 commit (REFILL), 1
    resume, 1 observe. It is ch02's stall machinery reused verbatim:
    a miss is a structural hazard on the memory port that freezes
    the CPU; and S_RES needs no special reply path — valid was set
    in S_REF, so the *same request now hits*: RESUME is a replay.
    (c) 2-way fixes the thrash — tags 0 and 2 co-reside at index 12
    — but each extra way adds a parallel tag compare (cheap) plus a
    way-select mux *after* the compare, on the very path that must
    fit the MEM slice of the cycle. L1 is timing-bound: 2-4 way in
    the textbook, 6-8 way in real parts kept fast by way prediction
    (read only the predicted way early, verify after, ~1 cycle on a
    misprediction); L2/L3 are miss-bound — at ~12-14 and ~40-50
    cycles of latency, one more mux delay is noise while a conflict
    miss costs a DRAM trip, so they buy 8-16 way. (d) 1 MB
    direct-mapped is 32,768 lines = 256× the rows; the bitline is a
    distributed RC line, so delay grows ~sqrt(length): sqrt(256) =
    **16× the access time** — every load in the machine pays extra
    cycles, hit or miss. L1's size is a timing decision, not a
    capacity decision: the sweet spot where the arrays answer in
    ~4-5 cycles is 32-64 KB (Apple's 128 KB L1d is the exception,
    bought with a wider, less clock-hungry pipeline). Nor does the
    next node rescue it — logic gains ~1.6× but the HD 6T cell
    shrinks ~5%, so the rows, and their bitlines, are what they are
    (ch04; ch05's SRAM wall).

11. **25.6 GB/s minus 4.5%; 52 ns vs 96.5 ns = 1.85×.** (a) PC4-25600
    names the bytes: DDR4-3200 is 3.2 GT/s per pin × 8 B per transfer
    on a 64-bit channel = **25.6 GB/s**. Refresh steals tRFC = 350 ns
    every tREFI = 7.8 µs: 350/7800 = **4.5%** → ~24.4 GB/s usable.
    Idling cannot recover it because refresh is autonomous: every row
    must be rewritten within the 64 ms retention window — 8,192
    refreshes — so the bus and banks are simply unavailable during
    tRFC regardless of traffic; an idle DRAM still burns ~4.5% of
    its bandwidth on itself. (b) Clean miss: 10 (controller) + 14
    (tRP, precharge) + 14 (tRCD, activate) + 14 (tCAS, column) =
    **52 ns**. A dirty victim inserts its own write-back — 14 + 14 +
    14 + 2.5 = 44.5 ns — ahead of the fill, both paying full bank
    latency because victim and target are different rows: total
    **96.5 ns = 1.85×**, which practitioners round to "a dirty miss
    costs about double" (the toy RTL bus shows 13 → ~21 cycles). (c)
    Single core: repeated writes to one line cost zero bus traffic
    until eviction, and clean evictions skip the write-back
    entirely. With a second cache on the same memory, "memory is
    stale, I hold the new value" stops being a private fact — the
    bit is promoted to MESI's **Modified** state (module 01 ch20),
    where the owner must write back or invalidate before anyone else
    reads. (d) The write buffer: the CPU stops waiting for stores,
    and the bus schedules the drains. The bug class it plants is
    write-buffer coherency: a pending write to line X racing an
    incoming fill for line X installs a stale line — memory serves
    the fill the pre-write bytes, the drain lands after, and now
    memory is *newer* than the cache while every later read hits the
    stale line forever. One compare closes the class: check the
    buffer's line address against every request (`wb_hit`) and stall
    or drain first — the test that catches it is write-miss-then-
    read-miss to the same line, in that order (ch04).

12. **$1,025 monolithic vs ~$208 in pieces — but only at early-ramp
    D₀.** (a) λ = D₀·A = 0.01 × 300 = 3, so Y = e⁻³ = **4.98%**.
    Gross dies: 59,060/300 ≈ 196; good ≈ 196 × 0.0498 ≈ 9.8; cost =
    $10,000/9.8 ≈ **$1,025 per good die**. (b) Dielet: λ = 0.01 ×
    75 = 0.75, Y = e⁻⁰·⁷⁵ = 47.2%; gross = 59,060/75 ≈ 787; good ≈
    787 × 0.472 ≈ 372 dielets → **~93 packages per wafer**, silicon ≈
    $10,000/93 ≈ $108, plus the $100 package ≈ **$208/package — a
    4.9× win**. The trap: if all 4 dielets had to come from the same
    wafer neighborhood, survival = 0.472⁴ = e⁻³ = 4.98% — *exactly*
    the monolithic yield, because Poisson is memoryless over area:
    D₀·(4 × 75) = D₀·300. The win is the pool: a defect now kills
    75 mm² of silicon instead of 300, and any 4 good dielets — from
    anywhere on any wafer — make a package; fungibility, not
    probability, is the lever. (c) Mature D₀ = 0.001/mm²: the
    monolithic die has λ = 0.3, Y = 74.1%, cost = $10,000/(196 ×
    0.741) ≈ **$69**; each dielet yields e⁻⁰·⁰⁷⁵ = 92.8%, giving
    787 × 0.928/4 ≈ 183 packages → $55 of silicon + $100 = **$155 — the
    split loses**: the package premium buys nothing when yields are
    high. Chiplets are a D₀-and-area question, not a religion; at
    0.15/cm² and $100 packages the break-even sits near 622 mm² —
    inside the 850 mm² reticle cap, exactly where flagship parts
    live. (d) A dead small core is a binned SKU; a dead big core is
    a dead die: AD102 is designed with 144 SMs and the RTX 4090
    ships 128 — 11% harvested, salvage scheduled in advance. *The
    SKU list is the yield curve's product* (ch05).

</details>
