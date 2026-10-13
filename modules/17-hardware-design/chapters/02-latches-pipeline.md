# Chapter 02 — State and the pipeline

> **The 30-second version.** Chapter 01 gave us gates with delay but no
> memory: combinational logic is a function of its inputs, and when the
> inputs leave, the output forgets. This chapter builds the missing piece
> from logic itself — two inverters wired in a loop bite each other's tail
> and hold one bit; gate that loop and you get a latch; stack two latches
> with opposite enables and you get the edge-triggered flip-flop, the
> component that converts a continuum of voltages into discrete cycle
> numbers ("a clock is a data structure for time," ch01). Flip-flops bolted
> onto a combinational blob cut it into pipeline stages: f_max =
> 1/(D/k + t_overhead), throughput one instruction per cycle, latency
> slightly *worse*. The price is three hazards — data, control, structural —
> paid for with forwarding (wires + a 3-input mux), a 2-bit predictor that
> turns out to score ~90% on real-shaped streams and 0% on alternating ones,
> and the split L1i/L1d. The Pentium 4's 31 stages is what happens when one
> term of this chapter's equation is optimized while ch01's power equation
> is ignored; Core is the correction.

## A loop that bites its tail: memory from logic

Start with the one thing combinational logic cannot do: hold. Wire the
output of an inverter into the input of a second inverter, and the second
inverter's output back into the first, and inspect the loop:

```text
        +---------+            +---------+
   +--->|  NOT-A  |----------->|  NOT-B  |---+
   |    +---------+            +---------+   |
   |                                           |
   +---------------- the loop -----------------+

   two inverters, each driving the other's input
   stable point 1: node a = 1, node b = 0
   stable point 2: node a = 0, node b = 1
```

Why exactly two stable points? In steady state each inverter *forces* its
output to the opposite of its input. Walk the possibilities: if a = 1, then
NOT-B drives b = 0, which lets NOT-A keep a = 1 — self-consistent. If
a = 0, then b = 1, and NOT-B's output drives a = 0 — also self-consistent.
Every other assignment contradicts at least one gate, and the contradiction
propagates around the ring until it lands on one of the two fixed points.
Poke the loop (a stray charge flips a node) and the gates push it back:
this is the **bistability** that stores a bit. It holds its state the way a
spinning flywheel holds its position — not by being latched in place, but
because any perturbation costs energy that the loop immediately spends
returning to a stable point. That is the deepest trick in the chapter:
memory is not a new component class, it is *feedback* around components we
already had.

To write the bit, you must overpower the loop. The SR latch replaces the
inverters with NOR gates, whose spare inputs become controls:

```text
      +-----------+   Qbar    +-----------+
  S -->|  NOR-A   |----------->|  NOR-B   |<-- R
      |  in:S,Q  |            | in:R,Qbar|
      +-----^-----+            +-----|-----+
            |                        |
            +--------- Q -----------+
          (NOR-B's output Q loops back into NOR-A's second input)
```

Verify the wiring once on paper -- it is the whole latch. Assert S = 1:
a 1 into a NOR wins, so NOR-A's output Qbar goes to 0. Now NOR-B sees
R = 0 and Qbar = 0, so it outputs 1: Q = 1 -- SET. That high Q feeds
back into NOR-A and holds Qbar down even after S drops: the loop is now
storing the write. R = 1 is the mirror image. The four rows:

```text
   S  R |  Q  Qbar |  meaning
  ------+----------+---------------------------------------------
   0  0 |  hold    |  the loop reigns: memory
   1  0 |  1  0    |  SET   (S=1 -> Qbar=0 -> Q=1 -> Q holds Qbar)
   0  1 |  0  1    |  RESET (mirror)
   1  1 |  0  0    |  FORBIDDEN: both outputs agree
```

The forbidden row is the important one: with S = R = 1 each gate has a 1
at an input, so both NOR outputs are 0 and **Q and Qbar agree** -- the
pair stops being complementary. Worse, those levels are held *by the inputs*, not by
the loop: this latch stores a level, and driving both control inputs writes
both nodes at once, which is nonsense for an element whose entire job is
one bit as *two complementary levels*. Release S and R simultaneously and
the loop must re-resolve from Q=Qbar=0 by racing: both NORs see all-zeros,
both outputs start to rise, each rising output kills the other's rise — the
final state is decided by transistor mismatch and noise, and the loop can
hang half-way (metastability, the ch01 CDC villain, in miniature).

Add one gate per control and you get the **gated (transparent) latch**: an
enable input; while enable is high, D passes straight through to Q; while
low, Q freezes. Usefulness is obvious; the flaw is the same word —
*transparent*. D is allowed to change *while enable is high*, so Q changes
with it, mid-cycle, as many times as D does. The latch sees the whole
half-cycle, not a moment.

## One moment per cycle: the master-slave contract

The fix for transparency is architectural, not electrical: put two gated
latches in series with *inverted* enables — the **master-slave
flip-flop**. The master (enable = NOT CLK) is open during the low phase;
the slave (enable = CLK) during the high phase:

```text
   D -->[ master: enable = NOT CLK ]-- m -->[ slave: enable = CLK ]--> Q

   CLK    ____/^^^^^\____/^^^^^\____
                 ^                    rising edges
```

At a rising edge the master's enable drops — its door shuts — and the
slave's opens. The value frozen *between* the two latches at that instant
is what escapes to Q. For the rest of the cycle D can twitch all it wants:
the master is closed, m is frozen, Q is stuck at the frozen value. So
**exactly one moment per cycle — the rising edge — can admit a new value**.
Derive it as the door rule: *lock the inner door before opening the outer
one.* A value cannot walk D-to-Q in one visit because when the outer door
(slave) opens, the inner one (master) has already closed behind the value
that's inside. (Real designs buffer the two enables through inverter
chains sized so the master's transmission gate provably closes first —
the internal "race-through" hazard is why the enables are not just one
inverter apart.)

This is the whole contract, drawn as waveforms:

```text
                 1 cycle                1 cycle
             <--------------->      <--------------->
CLK          ______/^^^^^^^^^\________/^^^^^^^^^\________
                    ^edge 1                   ^edge 2
D             ______/^^^\____\vvv_______________________
               (rises before     (twitches mid-high-phase:
                edge 1)           harmless to the FF)
latch Q       ______/^^^\____\vvv_______________________   transparent while
                                                              CLK is high
FF Q          _____________/^^^^^^^^^^^^^^^^^^\vvvvv______   moves ONLY just
                                                              after an edge,
                                                              to the value D
                                                              held AT it
```

The single positive latch forwards D's twitch mid-cycle; the flip-flop
ignores it. That immunity is why every synchronous design standardized on
edge-triggered flops: with a global edge as the only legal moment of
change, a continuum of analog voltages becomes discrete cycle numbers, and
"which cycle" becomes a checkable, countable quantity.

The edge buys discreteness and charges rent: the **setup and hold**
constraints, both derived from the master's transparency. Setup: the
master is transparent until the edge, so D must be settled and stable for
**t_su** *before* the edge, or the master samples a moving value. Hold: the
master's internal switch takes nonzero time to close, so D must stay
stable for **t_h** *after* the edge, or the still-closing master catches
D's next value too. Between two flops connected by logic of delay
t_logic, both constraints combine with the launch flop's clock-to-Q delay
**t_c2q** into the two inequalities that govern all synchronous design:

```text
   SETUP:  T_clk >= t_c2q + t_logic(max) + t_su        (+ skew, worst way)
   HOLD:   t_c2q + t_logic(min) >= t_h                  (+ skew, worst way)
   f_max  = 1 / (t_c2q + t_logic + t_su)
```

The insider's asymmetry lives in those two lines. The setup inequality
*contains* the clock period T_clk — so a setup violation is healed by a
bigger period: slow the clock, ship the part at a lower bin (ch01's
binning business). The hold inequality contains **no period at all**: it
compares two arrival skews launched by the same edge. A hold failure at
2 GHz fails identically at 800 MHz, or at 1 Hz. The only fixes are
physical: insert delay (hold buffers) on the too-fast path, or — if the
bug is found after tapeout — respin the silicon. "Hold bugs are the
expensive kind" is not folklore, it is this equation: a full mask set runs
on the order of $1.5-2M at 28 nm and $10M+ at a 5 nm-class node
(industry-quoted ranges; the economics are ch05's subject). Static timing
analysis (STA) exists to never find one late: every register-to-register
path checked against both inequalities, **slack** = actual margin
(negative slack = a path that misses), "timing closure" as a whole
engineering discipline because a chip with one negative-slack path is a
paperweight.

## Cutting the blob: the pipeline derived

Take a combinational datapath — the whole ALU-plus-operand-mux path, call
it D = 3 ns in a mid-1990s-scale process (any process works; D is just a
number in FO4 units, ~10-15 FO4, ch01). One add per pass-through:

```text
   f_max = 1/3 ns = 333 MHz.  A 1 GHz clock is arithmetically unreachable.
```

Now cut the blob with flip-flops into k stages. Each stage carries a slice
of the logic (D/k) plus the register overhead — t_c2q on launch, t_su on
capture, call it t = 0.2 ns:

```text
   cycle(k) = D/k + t          f_max(k) = 1/(D/k + t)
```

The trade stated precisely, with numbers:

```text
   machine          cycle     throughput        latency of one add
   ---------------  -------   ---------------   -------------------
   blob (k=1)       3.0 ns    333 M/s           3.0 ns
   3-stage pipe     1.2 ns    833 M/s           3.6 ns  (k * stage)
```

Throughput rises ~2.5× (one result *per cycle*, and the cycle shrank);
latency is *worse* by exactly the register tax: latency = k·(D/k + t) =
D + k·t. Pipelining never makes any single item faster — it makes the
*next* item start before the current one finishes. This exact trade
returns in module 12: GPUs are the latency-tolerant extreme.

Why not cut into 100 stages? Two costs. First, each stage pays t = 0.2 ns
of overhead, so f_max asymptotes at 1/t = 5 GHz while latency grows as
D + k·t — diminishing returns by construction. Second, deeper pipelines
flush deeper on surprises (next section). Put both in one performance
model — b = 20% of instructions are branches, the predictor misses m = 5%
of them, and a miss flushes ~k cycles:

```text
   P(k) = f_max(k) / CPI(k)  =  k / ((D + k·t) · (1 + b·m·k))
```

Maximize: dP/dk = 0 collapses (the k³ terms cancel cleanly) to

```text
   k* = sqrt( D / (b·m·t) )  =  sqrt( 3 / (0.2 · 0.05 · 0.2) )  ≈ 39
```

There *is* an optimum, and it is a balance point: shallower wastes clock
headroom, deeper hands the savings to the flush penalty. (This toy model
omits power — the omission is precisely the Pentium 4's story below.)

## The three bills: hazards

Pipelining overlaps instructions, and overlapped instructions interfere.
Three ways, three payments.

**Data hazards (RAW).** Instruction 2 reads the register instruction 1 is
still computing — the classic add-then-sub pair. The naive fix is a
**stall**: freeze the younger instruction in decode, inject a bubble
(a nop) into the pipe. Bubbles are pure waste. The real fix is
**forwarding** (bypass): the ALU's result physically exists at the ALU's
*output* at the end of EX — route it back around to the ALU's *inputs* for
the next instruction, one stage before the register file would have
delivered it. The cost on the datapath is two 3-input muxes per operand:

```text
   each ALU operand is fed by a 3-input mux:

                       +--------+
   RF read (from ID)->| 0      |
   EX/MEM forward  -->| 1  3:1 |--> operand --> ALU
   MEM/WB forward  -->| 2      |
                       +--------+
   select: compare this instruction's source registers against the
   destination registers parked in EX/MEM and MEM/WB; the *nearest*
   producer wins (EX/MEM beats MEM/WB beats the RF)
```

One case forwarding cannot fix: **load-use**. The load's data exists at
the *end* of MEM (cycle 4 in a 5-stage pipe); a consumer immediately
behind needs it at the *start* of its EX — also cycle 4. No wire can
deliver a value to the start of a cycle in which it is produced at the
end. This is the one mandatory stall of the classic 5-stage: one bubble.
Its software echo: compilers schedule loads as early as possible above
their consumers, precisely so this bubble is filled with useful work —
when you see GCC or LLVM hoist a load three statements up, this is why.

**Control hazards.** The branch decides where fetch should have gone —
but fetch already went. On a taken branch in the 5-stage model (branch
resolved in EX), 2 wrong-path instructions are in flight (3 if resolution
waits for MEM). The fixes ladder up in cost. Predict-not-taken: the cheap
default, right by construction on every fall-through. The **2-bit
saturating counter**: one 2-bit state per branch, stepped by outcomes —

```text
   NOT-TAKEN moves LEFT one square <----------------
   TAKEN moves RIGHT one square     ---------------->

   +---------+     +---------+     +---------+     +---------+
   | 00 SNT  |<--->| 01 WNT  |<--->| 10 WT   |<--->| 11 ST   |
   +---------+     +---------+     +---------+     +---------+
     predict NOT-TAKEN               predict TAKEN
   (saturating: stepping past 00 or 11 stays put)

   hysteresis: from a strong square, ONE contrary outcome only demotes
   you to weak -- the verdict survives a single anomaly (the loop exit)
```

Accuracy, measured over patterned streams (the lab runs this): a 9-taken-
1-not loop scores 90% (the counter sits strong-taken; only each exit
misses); a 90/10 biased stream scores ~88-90%; the alternating T,N,T,N
stream scores **0% from a weak start** — the counter hunts between 01 and
10, one step behind the pattern forever (from a strong start it manages
50%). The folklore "branches are ~90% predictable" *is* this measurement
on real-shaped streams; the alternating failure is the blind spot that
history-based predictors (TAGE, perceptrons) exist to fix — they correlate
each branch with tens-to-thousands of past branch outcomes, reaching
~95-99% on regular code (CBP-trace-suite numbers, marked honestly: exact
figures vary by benchmark mix). The **BTB** (branch target buffer) solves
the other half — *where* — as a cache of PC → target, reusing module 01
ch02's tag/index machinery verbatim, but pointed at the *future* instead
of the past: the branch predictor is a cache of the future.

**Mispredict penalty = pipeline depth** (plus refill). The flush costs
everything younger. Deep pipes therefore multiply the miss rate into a
dominant term: at b = 20% branches, ~17-cycle penalty on a
Skylake-class core (measured 15-20), the CPI math is 0.2 × 0.05 × 17 ≈
0.17 lost cycles per instruction at 95% accuracy — and 0.2 × 0.10 × 17 ≈
0.34 if accuracy slips 5 points. That arithmetic made branch prediction
the #1 performance research area of 1995-2010. And it is the
**Pentium 4 story** in one causality chain, the industry's clearest
"equations decided the product" case. Willamette (2000): 20 stages
(roughly double the P6 line's 10-12), launched at 1.4-1.5 GHz. Prescott
(2004): 31 stages, 3.8 GHz flagship, ~115 W TDP, ~39-cycle mispredict
penalty (literature estimates run 30-39). Each term is one chapter's
equation: f_max = 1/(D/k + t) paid off with k; the flush penalty ∝ k ate
the IPC; and ch01's P = α·C·V²·f closed the pincer, because 31 stages of
full-width pipeline registers is 31 banks of flip-flops — the C term
exploded — and the V cuts needed to hold power dissipation slow every
gate (the PVT triangle), stealing back clock the depth had bought. Tejas
(the planned ~5+ GHz successor) was cancelled in 2004; Intel pivoted to
the Pentium-M/Core lineage: Conroe (2006), 14 stages, 4-wide, 2.4-2.93
GHz, 65-75 W, beating the 3.8 GHz Prescott by roughly 1.5-2× on integer
workloads (benchmark-dependent). Fewer, wider stages won. Meanwhile the
speculation machinery became the **silent speculator**: a modern CPU runs
dozens of instructions past a predicted branch and discards the wrong
ones on a miss — the engine module 01 ch20 describes, and the exact
mechanism module 02 ch07's side-channel villain abuses (the wrong-path
work touches caches before being flushed).

**Structural hazards.** One resource, two simultaneous users: the 5-stage
pipe wants memory in IF (every cycle — one instruction per cycle) and in
MEM (loads+stores, roughly a quarter to a third of dynamic instructions).
One port cannot serve both without stalls. The fix is the split everyone
meets in module 01 ch02 as a fact — **separate L1i and L1d** — here
derived: two caches, two ports, no contention. The cost is coherence
between them (self-modifying code; the serializing sync x86 requires
after writing code) — why the split is a real design trade, not a
freebie.

## The 5-stage reference machine, drawn whole

Everything above assembled into the classic model (Patterson-Hennessy
textbook; the MIPS R3000's organization — 5 stages, ~20-40 MHz, 1988):

```text
   PC+4 chain: IF computes PC+4 once; the value rides the pipe inside
   every stage register, because the branch in EX -- two cycles later --
   still needs ITS OWN instruction's PC+4 (and jal needs it as the return
   address).  PC+4 is data shipped alongside the instruction, not
   something recomputed downstream.

   +---------+    +---------+    +---------+    +---------+    +---------+
   |   IF    |    |   ID    |    |   EX    |    |   MEM   |    |   WB    |
   | I$ port |    | decode, |    | ALU,    |    | D$ port |    | RF      |
   | fetch @ |--->| RF read |--->| branch  |--->| (loads, |--->| write,  |
   | PC      |    | (2nd 1/ |    | resolve |    |  stores)|    | retire  |
   | PC<=+4  |    |  2 cyc) |    |         |    |         |    |         |
   +---------+    +---------+    +---------+    +---------+    +---------+
        ^              |   ^         ^   ^          |
        |              |   |         |   |          |
        |              |   |         |   |          +--- MEM/WB forward --+
        |              |   |         |   |          |  (result 2 cycles   |
        |              |   |         |   |          |   old, 1 cycle from |
        |              |   |         |   |          |   being in the RF)  |
        |              |   |         |   +----------+--- EX/MEM forward   |
        |              |   |         |     (last cycle's ALU result)      |
        |              |   |         |                                      |
        |              |   |         +---------- both mux into the 3-input |
        |              |   |                    operand muxes drawn above  |
        |              |   +--- RF output (no bypass needed) --------------+
        |              |
        |              v
        |        +-------------+    load in EX with a consumer in ID?
        |        | hazard unit |--> freeze IF/ID, inject a bubble into EX
        |        +-------------+    (the ONE mandatory stall)
        |
        +--- PC redirect: branch resolved in EX (or a later-stage predictor
             correction) -> flush everything younger: IF, ID, EX
             = 2-3 slots gone on a taken branch, ~depth on a mispredict
```

This is the in-order reference. Modern out-of-order cores keep the same
five *jobs* but break the in-order straitjacket with three mechanisms,
each an existence proof of a hazard fix taken to its limit. The **reorder
buffer (ROB)**: instructions *complete* out of order but *retire* in
order, and architectural state only advances at retire — the speculation
safety valve, since a flush just discards unretired work (a Skylake-class
ROB holds ~224 entries; Sunny Cove, 2019, ~352). **Register renaming**:
a physical register file plus a map table, sized 2-4× the architectural
set (a Skylake-class core: ~180 integer physical registers against 16
architectural — order-of-magnitude figures that vary by microarchitecture),
breaks false write-after-write and write-after-read dependencies so stale
names never force stalls. The **store buffer**: writes wait in a queue
until retire, then commit through the coherence machinery — module 01
ch20's MESI protocol is literally this buffer's drain path.

## What insiders know

- **The 2-flop synchronizer's MTBF arithmetic, worked.** MTBF =
  e^(tr/τ)/(f_clk·f_data). With f_clk = 100 MHz, f_data = 1 MHz, τ ≈ 100 ps
  (order of magnitude for a 90-65 nm flip-flop; it shrinks with process):
  a single flop given tr = 2 ns of resolve time yields e^40/10¹⁴ ≈ 2.4·10³
  s ≈ **39 minutes** between metastable upsets. The second flop of the
  pair buys a full period of resolve time: tr ≈ 10 ns → e^100/10¹⁴ ≈
  10²² years — hundreds of billions of times the universe's 1.4·10¹⁰. The
  exponential
  in tr/τ is why you always see pairs, never singles: one flop is a
  coin-flip on a 40-minute timer; two is geology.
- **"This pipeline stage is 15-20 FO4."** Insiders state cycle budgets in
  fanout-of-4 delays, not picoseconds, because FO4 scales with process. A
  3 GHz cycle is 333 ps, which lands at ~17-22 ps per FO4 across 15-20 of
  them — the arithmetic that explains why 5 GHz single cores stopped being
  a roadmap item.
- **The P4 numbers card.** Willamette 20 stages (2000, 1.4-1.5 GHz);
  Prescott 31 stages, 3.8 GHz, ~115 W TDP, ~39-cycle mispredict penalty;
  Tejas cancelled 2004; Conroe 14 stages, 2.93 GHz top SKU, 65-75 W,
  ~1.5-2× the integer performance. One row of numbers, three chapters of
  equations.
- **The branch predictor is the single most performance-critical cache in
  the machine.** 20% branches × (1 − accuracy) × 15-20 cycle penalty: a
  5-point accuracy slip doubles the waste (0.17 → 0.34 lost CPI). Nothing
  else in the memory hierarchy has a per-miss cost this large — which is
  why prediction, not caching, was the research frontier of 1995-2010.
- **Clock skew eats 10-20% of the cycle budget on a bad clock tree** —
  it is skew *plus* jitter that STA signs off against, and why clock-tree
  synthesis (CTS) is its own EDA step before routing (ch05): a 3 GHz
  cycle has only ~333 ps to lose, and 30-60 ps of skew is a silent 10-18%
  tax on every path in the chip.
- **Hold bugs are the expensive kind.** A setup miss costs a speed bin; a
  hold miss costs a respin (the mask arithmetic above). Teams run
  hold-fixing passes at *every* PVT corner precisely because the hold
  inequality has no clock-period escape hatch.

## See it yourself

Three experiments, each a Python file, each producing one of this
chapter's folklore numbers. Python 3.8+, no dependencies.

**1. The 2-bit branch predictor.** The saturating counter simulated over
patterned streams — this lab is the source of the chapter's 90% / 0%
numbers:

```python
# two_bit.py -- the 2-bit saturating counter over branch streams.
import random

def two_bit(stream, start):
    # states 0..3 = 00,01,10,11: 0 strong-NOT-taken ... 3 strong-TAKEN
    state, hits = start, 0
    for taken in stream:
        pred = state >= 2               # top half of the counter = TAKEN
        hits += (pred == taken)
        # saturating update: step toward what happened, clamp at the ends
        state = min(state + 1, 3) if taken else max(state - 1, 0)
    return hits / len(stream)

loop9 = [i % 10 != 9 for i in range(1000)]   # 9 taken + 1 not, x 100
tttn  = [i % 4  != 3 for i in range(1000)]   # T,T,T,N repeated
alt   = [i % 2  == 0 for i in range(1000)]   # T,N,T,N alternating
rng   = random.Random(7)
bias  = [rng.random() < 0.9 for i in range(1000)]   # 90% taken, random

print(f"{'stream':22s}{'init':>7s}{'accuracy':>10s}")
print(f"{'loop 9T + 1N x100':22s}{'weak':>7s}{two_bit(loop9, 1):>10.1%}")
print(f"{'T,T,T,N x250':22s}{'weak':>7s}{two_bit(tttn, 1):>10.1%}")
print(f"{'alternating T,N':22s}{'weak':>7s}{two_bit(alt, 1):>10.1%}")
print(f"{'alternating T,N':22s}{'strong':>7s}{two_bit(alt, 3):>10.1%}")
print(f"{'biased 90/10 random':22s}{'weak':>7s}{two_bit(bias, 1):>10.1%}")
```

```console
$ python3 two_bit.py
stream                   init  accuracy
loop 9T + 1N x100        weak     89.9%
T,T,T,N x250             weak     74.9%
alternating T,N          weak      0.0%
alternating T,N        strong     50.0%
biased 90/10 random      weak     88.4%
```

What to notice: the loop hits 90% minus warm-up (the counter needs one
miss per exit, unavoidable); the alternating stream from a weak start is
**0.0%** — the counter lives in 01/10, one step behind the pattern
forever (the T,T,T,N short loop only manages 74.9%: one miss per 4); the
biased random stream tracks its own taken-rate (~88% here; the predictor
says TAKEN essentially always after warm-up, so accuracy = the stream's
taken fraction, seed-dependent in the 88-91% band). Real code is mostly
loops and biased branches — hence "~90% predictable."

**2. The pipeline arithmetic on paper.** The 3-stage f_max with 0.2 ns
register overhead, the 10-stage version, and the optimum the derivation
promised:

```python
# sweep.py -- where does a deeper pipeline stop paying?
D, t, b, m = 3.0, 0.2, 0.20, 0.05        # ns, ns, branch fraction, miss rate
print(f"{'k':>3s} {'f_max (MHz)':>12s} {'CPI':>6s} {'M instr/s':>10s}")
for k in (1, 3, 5, 10, 15, 20, 30, 39, 50):
    f   = 1e3 / (D / k + t)              # cycle = D/k + t ns  -> MHz
    cpi = 1 + b * m * k                  # flush penalty grows with depth
    print(f"{k:3d} {f:12.0f} {cpi:6.2f} {f / cpi:10.0f}")
print(f"k* = sqrt(D/(b*m*t)) = {(D/(b*m*t))**0.5:.1f}")
```

```console
$ python3 sweep.py
  k  f_max (MHz)    CPI  M instr/s
  1          312   1.01        309
  3          833   1.03        809
  5         1250   1.05       1190
 10         2000   1.10       1818
 15         2500   1.15       2174
 20         2857   1.20       2381
 30         3333   1.30       2564
 39         3611   1.39       2598
 50         3846   1.50       2564
k* = sqrt(D/(b*m*t)) = 38.7
```

What to notice: k = 1 gives 312 MHz — the *registered* blob; the pure
combinational 333 MHz from the text shows even one boundary of flops
costs 6%. f_max is still climbing at k = 50, but performance peaks at
k ≈ 39 and declines: the flush term wins. Now add the power term this
model omits, ask which side of 39 a 31-stage chip sits on, and you have
re-derived Intel's 2004.

**3. The 5-stage pipeline with a RAW hazard and forwarding.** The
chapter's model as a cycle-by-cycle simulator — the observable is the
bubble vs the forwarded cycle count:

```python
# pipe5.py -- bubble vs bypass, cycle by cycle. In-order, single-issue.
# RF written in WB (first half of cycle), read in ID (second half):
# write-then-read in the SAME cycle is legal.

PROG = [
    dict(asm="ld  x1, 0(x2)",  dst="x1", src=("x2",),      ld=True),
    dict(asm="add x3, x1, x4", dst="x3", src=("x1", "x4"), ld=False),
    dict(asm="sub x5, x3, x6", dst="x5", src=("x3", "x6"), ld=False),
    dict(asm="or  x7, x8, x9", dst="x7", src=("x8", "x9"), ld=False),
]

def run(prog, fwd):
    t = []                                  # stage-entry cycle per instr
    for i, ins in enumerate(prog):
        IF = 1 if i == 0 else t[i-1]["IF"] + 1
        ID = IF + 1 if i == 0 else max(IF + 1, t[i-1]["EX"])
        if fwd:
            EX = ID + 1
            if i:                           # one ALU: in-order limit
                EX = max(EX, t[i-1]["EX"] + 1)
            for src in ins["src"]:          # nearest producer of operand
                for j in reversed(range(i)):
                    if prog[j]["dst"] == src:
                        p = t[j]
                        # load: data at END of MEM; ALU: END of EX
                        ready = (p["MEM"] if prog[j]["ld"] else p["EX"]) + 1
                        EX = max(EX, ready)
                        break
        else:                               # no bypass: ID's RF read must
            leave = ID                      # wait for the producer's WB
            for src in ins["src"]:
                for j in reversed(range(i)):
                    if prog[j]["dst"] == src:
                        leave = max(leave, t[j]["WB"])
                        break
            EX = leave + 1
            if i:
                EX = max(EX, t[i-1]["EX"] + 1)
        t.append(dict(IF=IF, ID=ID, EX=EX, MEM=EX + 1, WB=EX + 2))
    return t

def render(prog, t):
    n = t[-1]["WB"]
    rows = ["cycle          " + "".join(f"{c:<3d}" for c in range(1, n + 1))]
    for i, e in enumerate(t):
        row = []
        for c in range(1, n + 1):
            if   c < e["IF"]:  row.append("   ")   # not fetched yet
            elif c < e["ID"]:  row.append("IF ")
            elif c == e["ID"]: row.append("ID ")
            elif c < e["EX"]:  row.append("** ")   # held in decode: stall
            elif c < e["MEM"]: row.append("EX ")
            elif c < e["WB"]:  row.append("ME ")
            elif c == e["WB"]: row.append("WB ")
            else:              row.append("   ")   # retired
        rows.append(f'{prog[i]["asm"]:16s}' + "".join(row))
    return "\n".join(rows)

for fwd in (False, True):
    t = run(PROG, fwd)
    ideal = 5 + (len(PROG) - 1)             # fill + drain of a clean pipe
    print(f"forwarding {'ON ' if fwd else 'OFF'}: {t[-1]['WB']} cycles "
          f"(ideal {ideal} + {t[-1]['WB'] - ideal} lost)")
    print(render(PROG, t))
    print()
```

```console
$ python3 pipe5.py
forwarding OFF: 12 cycles (ideal 8 + 4 lost)
cycle          1  2  3  4  5  6  7  8  9  10 11 12
ld  x1, 0(x2)   IF ID EX ME WB
add x3, x1, x4     IF ID ** ** EX ME WB
sub x5, x3, x6        IF IF IF ID ** ** EX ME WB
or  x7, x8, x9           IF IF IF IF IF ID EX ME WB

forwarding ON : 9 cycles (ideal 8 + 1 lost)
cycle          1  2  3  4  5  6  7  8  9
ld  x1, 0(x2)   IF ID EX ME WB
add x3, x1, x4     IF ID ** EX ME WB
sub x5, x3, x6        IF IF ID EX ME WB
or  x7, x8, x9           IF IF ID EX ME WB
```

What to notice: with forwarding off, the dependent `add` waits in decode
(`**`) for the load's WB at cycle 5, then `sub` waits for `add`'s WB — 4
lost cycles. With forwarding on, the *only* stall left in the whole chart
is `add`'s single `**` at cycle 4: the load-use bubble, the one no bypass
can fix. `sub` reads `add`'s fresh result with zero stalls — the EX/MEM
forward path plus the ALU's own occupancy line it up exactly. One bubble
versus four, for two muxes per operand.

## How to use this knowledge

- **Read branch counters like an insider.** `perf stat -e
  branches,branch-misses,instructions ./bench` hands you the lost-CPI
  arithmetic: branch fraction × miss rate × ~15-20 cycle penalty
  (Skylake-class). A 10% miss rate ≈ 0.2 × 0.10 × 17 ≈ 0.34 lost cycles
  per instruction — often worth more than any cache tweak. The classic
  fix is making the *data* predictable (sort-then-filter), not the branch
  fewer.
- **Diagnose load-use stalls in your own hot loops.** `objdump -d` the
  loop; a load whose consumer is the very next instruction is 1 bubble
  per iteration. The fix is the compilers' own: hoist independent work
  into the gap — and stop defeating `-O2`'s scheduling with `volatile`
  or aliasing that blocks the hoist.
- **Answer the hardware interview traps with the equations.** "Why can't
  you fix hold by slowing the clock?" — the hold inequality has no period
  in it. "Why a 2-flop synchronizer and not 1?" — e^(tr/τ) arithmetic.
  "Why did the P4 fail?" — f_max vs penalty vs αCV²f, three terms, one
  product decision.
- **Review RTL and board designs for CDC like a hardware engineer.** Any
  signal crossing clock domains gets a 2-flop synchronizer (single bit)
  or a Gray-coded pointer handshake (multi-bit — ch03's async FIFO does
  exactly this). "It worked in simulation" is the sentence that precedes
  the field crash every time, because metastability is analog, and
  simulators don't do analog.
- **Discount clock-speed marketing on sight.** GHz is one term of one
  equation — a 3.8 GHz Prescott losing to a 2.4 GHz Conroe is the whole
  lesson, pre-taught. Compare perf and perf/W at your workload.

## Connections

- **Module 17 ch01** — the flip-flop is the customer for everything there:
  FO4 budgets, C·V²·f, clock-tree skew, the metastability MTBF formula.
- **Module 17 ch03** — nonblocking `<=` *is* the master-slave edge written
  as a language rule; the async FIFO's pointers are this chapter's Gray
  code.
- **Module 17 ch04** — the 6T SRAM cell: the same bistability at 6
  transistors, density traded against speed — the cache's existence.
- **Module 17 ch05** — negative slack is money: timing closure, CTS as
  its own EDA step, the $10M-class hold respin.
- **Module 01 ch02** — the L1i/L1d split and the BTB: this chapter
  derives the one-line facts stated there; the tag/index gates are the
  same.
- **Module 01 ch20** — ROB retire discipline, the store buffer draining
  through MESI: this chapter's machinery, formalized as the memory model.
- **Module 02 ch07** — Spectre is the silent speculator's wrong-path
  cache footprints before the flush; you now know the flush itself.
- **Module 12** — GPUs skip out-of-order and hide latency in thousands of
  threads: this chapter's throughput-vs-latency trade at the extreme.

## Quiz

1. In the SR latch built from two NOR gates, S and R are both asserted
   (S = R = 1). What do Q and Qbar read, why is that state called
   forbidden, and what physically happens if S and R are released at the
   same instant?

2. STA reports a setup violation of 0.12 ns on one path and a hold
   violation of 0.03 ns on another, both at the target 2 GHz. The team
   drops the clock to 500 MHz. Which violation is now certainly gone,
   which is certainly still there, and what single structural feature of
   the two inequalities explains the difference?

3. A datapath is a 12 ns combinational blob; flip-flop overhead
   (t_c2q + t_su) is 0.5 ns. Compute f_max for the blob cut into k = 4
   stages, the latency of one operation through the 4-stage pipe versus
   through the blob, and the f_max asymptote as k → ∞.

4. A loop branch executes taken 9 times, then not-taken once, repeated
   forever; a 2-bit saturating counter (00/01/10/11, saturating at the
   ends) tracks it, starting from strongly-taken (11). What is the
   steady-state prediction accuracy, and what exactly is the one
   recurring mistake?

5. In the pipe5 simulation above with forwarding ON (9 cycles), which
   instruction stalls, for how many cycles, and why does `sub` — which
   reads the register `add` just wrote — get through with zero stalls?

6. A prototype fails intermittently at 2 GHz. Engineering drops the clock
   to 500 MHz, then 100 MHz: the failure rate does not change at all.
   Diagnose the class of timing bug, explain why the clock period cannot
   be in the relevant inequality, and name the two fixes in the order a
   team would try them.

7. An asynchronous status line toggling at 10 MHz is sampled by a single
   flip-flop inside a 200 MHz domain (metastability τ ≈ 50 ps, resolve
   time before the next logic level ≈ 2 ns). Field units crash roughly
   every 2 minutes of continuous operation. What is failing, what does
   the MTBF formula predict, and what is the standard fix and its cost?

8. Using exactly two equations — f_max = 1/(D/k + t_overhead) from this
   chapter and P = α·C·V²·f + V·I_leak from ch01 — explain why Prescott's
   31 stages at 3.8 GHz was a losing design while Conroe's 14 stages at
   2.93 GHz won, and identify which term each equation contributed.

<details><summary>Answers</summary>

1. **Both outputs read 0.** With S = 1, the S-gate (inputs S and Q,
   output Qbar) is forced to 0 regardless of the loop; with R = 1, the
   R-gate (inputs R and Qbar, output Q) is forced to 0; so Q = Qbar = 0
   and the pair is no longer complementary. It is forbidden
   because this latch stores a *level*, and the storage concept itself is
   "one bit as two complementary levels": driving both nodes at once
   writes no valid bit, and those levels are being held by the inputs,
   not by the feedback loop. On simultaneous release, the loop must
   re-resolve from Q = Qbar = 0: both NORs momentarily see all-zero
   inputs, both outputs begin to rise, and each rise suppresses the
   other — the final state is decided by transistor mismatch and noise
   (a race), and the loop can hang metastable before settling. The
   recoverable inputs are the other three rows, where the loop is closed
   around a valid complementary pair.

2. **The setup violation is gone; the hold violation is still there.**
   The setup inequality is T_clk ≥ t_c2q + t_logic(max) + t_su: the clock
   period is a term, so quadrupling T from 500 ps to 2000 ps adds 1500 ps
   of margin — 0.12 ns is trivially healed. The hold inequality is
   t_c2q + t_logic(min) ≥ t_h + (skew, worst way): every quantity in it
   is a *difference of arrival times around a single edge*. No clock
   period appears, because the launch and capture edges are the same
   edge (or adjacent edges) however far apart cycles are — slowing the
   clock stretches the wait *between* edges, not the arrival *skews
   within* one. A hold failure at 2 GHz fails at 1 Hz.

3. **k = 4: f_max = 1/(12/4 + 0.5) = 1/3.5 ns ≈ 286 MHz.** Latency
   through the pipe = 4 stages × 3.5 ns = 14 ns, versus 12 ns through the
   blob — 17% worse, and always worse in general: latency = D + k·t, so
   every added stage adds one register overhead to every operation's
   total transit. Throughput, the compensating win, is one result per
   3.5 ns (286 M/s) versus one per 12 ns (83 M/s) — 3.4×. As k → ∞,
   f_max → 1/t_overhead = 1/0.5 ns = 2 GHz: the registers, not the logic,
   become the clock. That asymptote is why "just add stages" has a
   ceiling, and the hazard term (question 8) is why the practical optimum
   is below it.

4. **90% — 9 correct out of every 10 — with one miss per loop exit.**
   Steady state: the counter sits at 11 (strongly-taken) and predicts
   TAKEN. The 9 taken iterations are all predicted correctly (the counter
   saturates at 11, no further motion). The 10th branch is not-taken:
   predicted TAKEN, wrong — the counter demotes to 10. The next
   iteration's first branch is taken: from 10 the prediction is *still*
   TAKEN (the top half of the counter), so it is predicted correctly and
   the counter climbs back to 11. Exactly one miss per 10 branches = 90%.
   The recurring mistake is structural, not statistical: the 2-bit
   counter cannot know the loop is about to exit, because it sees only
   this branch's own recent outcomes. (From a weak start it costs one
   extra warm-up miss over 100 loops: 89.9%, matching the lab.) The
   alternating T,N stream is the same structure turned fatal: from a weak
   state the counter lives in 01/10 and every prediction is wrong — 0%.

5. **`add` stalls exactly 1 cycle (the `**` at cycle 4): the load-use
   bubble.** `ld`'s data exists at the *end* of MEM (cycle 4), but
   `add`'s EX would need it at the *start* of cycle 4 — forwarding routes
   data through wires, it cannot move a value earlier than the cycle in
   which it is produced. So the hazard unit freezes IF/ID one cycle and
   `add`'s EX happens at cycle 5, fed by the MEM/WB forward (`ld` is in
   WB at cycle 5). `sub` needs no stall even though it reads `add`'s
   result: `add` occupies EX at cycle 5, so the in-order single ALU
   already forbids `sub` from EX before cycle 6 — and at cycle 6 the
   EX/MEM forward is holding exactly the result `add` produced at the
   end of cycle 5. Dependency satisfied by the same cycle the hardware
   was structurally forced to wait for. Zero *extra* stall.

6. **A hold violation.** Setup margin scales with the period; the hold
   race — launch t_c2q + minimum logic delay versus capture t_h, plus any
   skew making the capture edge arrive early — is between arrival skews
   around one edge. No T_clk anywhere, so 2 GHz, 500 MHz, and 100 MHz
   fail identically (a hold bug fails at 1 Hz). Fix order: (1) hold
   buffers (delay gates) on the too-fast paths — cheap, automated, run
   at every PVT corner; (2) after tapeout, a metal-layer respin: on the
   order of $1.5-2M in masks at 28 nm plus months of schedule (ch05).

7. **Metastability.** The async line's transitions arrive at arbitrary
   phase; some violate the sampling flop's setup/hold window, and the
   flop's output can hang at an intermediate voltage before resolving
   randomly. The MTBF formula MTBF = e^(tr/τ)/(f_clk · f_data) predicts:
   e^(2 ns / 50 ps) / (2·10⁸ × 10⁷) = e^40 / 2·10¹⁵ ≈ 2.4·10¹⁷ / 2·10¹⁵ ≈
   120 s — about one upset per 2 minutes, matching the field reports. The
   fix is the 2-flop synchronizer: the first flop may go metastable, but
   it gets a nearly full clock period (~4.5-5 ns) to resolve before the
   second flop samples: e^(5 ns/50 ps)/2·10¹⁵ = e^100/2·10¹⁵ ≈ 10²⁸ s ≈
   10²⁰ years. The cost is one cycle of latency on that one signal —
   which is why the pair is a universal idiom and the single flop is a
   firing offense. (Multi-bit crossings additionally need Gray coding —
   ch03.)

8. **Equation 1 (this chapter) paid off, then billed.** f_max =
   1/(D/k + t): 20 → 31 stages cut D/k, and the clock climbed from the
   P6 line's ~1 GHz to 3.8 GHz — as promised. But this chapter's hazard
   arithmetic bills flush ∝ depth: ~39-cycle penalty × ~20% branches ×
   ~5-10% miss rate ≈ 0.4-0.8 lost cycles per instruction — the IPC the
   depth squandered. **Equation 2 (ch01) closed the trap.** Every stage
   boundary is a flop bank as wide as the datapath: 31 versus 14
   boundaries roughly doubles the pipeline-register C, while f rose 4×;
   holding P = α·C·V²·f at ~115 W forced V down, and lower V slows every
   gate (the PVT triangle), stealing back clock the depth had bought.
   Tejas (~5+ GHz) was cancelled; Conroe's 14 wide stages at 2.4-2.93 GHz
   and 65-75 W beat Prescott by ~1.5-2× on integer work. The P4 is one
   equation optimized while the other is ignored; Core is the joint
   solution.

</details>
