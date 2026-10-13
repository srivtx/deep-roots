# Module 17 — Digital hardware: from transistors to silicon

> The root under the root. Every tower in this program — caches, DRAM,
> GPUs, NICs — is built from the same three ideas: a transistor is a
> switch, a clock turns switches into a pipeline, and the pipeline is
> disciplined memory. Here we build them: CMOS gates from switch pairs,
> flip-flops from cross-coupled inverters, a FIFO ring in ~40 lines of
> Verilog (the same ring as the DMA ring, the vring, and io_uring —
> hardware got there first), a direct-mapped cache in RTL, and the
> economics that decide why your CPU looks the way it does. When
> module 01 said "the cache line explains everything", this module
> shows the cache being *born*.

**5 chapters · one final exam · prerequisite: module 01 chapters 01
(silicon, DRAM — extended downward), 02 (the hierarchy, caches —
rebuilt in RTL in ch04); module 12 (the GPU's memory machinery —
referenced in ch05's economics); no Verilog experience needed (ch03
teaches the subset).**

## How to study it

Chapter 01 is the physics-to-gates layer (CMOS, delay, power); 02 is
state and the pipeline (flip-flops, timing, hazards, the 5-stage
reference model); 03 is the RTL discipline (Verilog as a *description*
language — the paradigm shift from software); 04 is the payoff (an SRAM
cell, a cache in RTL, the DRAM timing model); 05 is from RTL to chips
(the foundry model, yield arithmetic, chiplets — why hardware is a
compiler for money). Labs: iverilog if installable (`apt install
iverilog` — chapter 03's lab), else the inline simulation code and
paper labs; every derivation runnable in your head or a Python script.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [From transistors to gates](chapters/01-transistors-gates.md) | MOSFETs, CMOS, RC delay, the power equation — the physics under every claim in this program. |
| 02 | [State and the pipeline](chapters/02-latches-pipeline.md) | Flip-flops from first principles, setup/hold, hazards, the 5-stage CPU. |
| 03 | [RTL: the other discipline](chapters/03-rtl-verilog.md) | Verilog as description not program — the FIFO ring in ~40 lines. |
| 04 | [The cache in RTL](chapters/04-cache-in-rtl.md) | The 6T cell, a direct-mapped cache in RTL, DRAM timing — module 01 ch02, rebuilt. |
| 05 | [From RTL to silicon: the economics of hardware](chapters/05-silicon-economics.md) | The foundry model, yield arithmetic, chiplets — why hardware decisions are money decisions. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — from transistors to gates.** The MOSFET as a
voltage-controlled switch: the gate is a capacitor over a channel
(field effect: gate voltage above a threshold creates a conductive
channel between source and drain); NMOS conducts when gate is high,
PMOS when low (the complementarity of the dopings — n-channel vs
p-channel; do NOT go deeper into semiconductor physics: stay at the
switch abstraction, say so explicitly). CMOS as the complementary
pair (draw the inverter: a PMOS from VDD and an NMOS to GND, gates
tied: input high → NMOS conducts, PMOS off → output pulled to 0; input
low → PMOS conducts → output pulled to 1; the key property: **in
either steady state, one transistor is off — no DC path from VDD to
GND, so static power is ~leakage only** (the off-transistor's
nA-scale leak — the number that nonetheless became the problem at
90 nm and below: the leakage current per gate ~nA-scale × 10⁹
transistors = tens of watts — why clock gating and power gating
exist, the dark silicon era named). The gate as a switch network:
the NAND (2 PMOS in parallel up top, 2 NMOS in series below: any 0
input pulls output high — 4 transistors; the NOR mirrored: 4
transistors; AND = NAND + inverter = 6 — **NAND and NOR are the
cheapest 2-input gates (4 transistors each)**, the reason NAND flash
and NAND logic share a name... no — state the real reason "NAND
denser" is folklore-for-cheap; CMOS is naturally inverting (NAND/NOR/
NOT cheap, AND/OR cost an extra inverter). The transmission gate (an
NMOS+PMOS in parallel: a passable switch for both logic levels —
pass-transistor logic's existence, one sentence). **Delay as RC**
(the whole chapter's physics in one idea): a gate's output drives
capacitance (the fanout gates' input caps + the wire's cap); the
charge time τ = RC → the delay ≈ 0.69RC per stage (the exponential
charging curve drawn: V(t) = VDD(1−e^−t/RC); the 50% crossing at
0.69RC — derive it); the drive-strength story: a bigger transistor
(switch) has lower on-resistance → drives the same cap faster, but
its own gate is a bigger cap → *someone upstream pays* — the
logical-effort optimum: each stage should drive ~2.7-4× its input
capacitance (the FO4 rule: a fanout-of-4 inverter chain is the
"standard unit of work" — "this pipeline stage is 15 FO4" is how
insiders state budgets; a modern cycle is ~15-20 FO4 (~10-20 ps per
FO4 at 5-7 nm... keep it: a 3 GHz cycle = 333 ps ≈ 15-20 FO4 — the
cycle-time arithmetic that explains why GHz stopped climbing (a
deeper pipeline = fewer FO4 per stage = higher clock, but the
mispredict penalty grows — ch02's Intel P4 story foreshadowed).
**Power = C·V²·f** (derive: charging a cap to V stores ½CV², the
supply delivers CV² per cycle (the ½ lost as heat in the charging
resistance — the physics of the loss); dynamic power = α·C·V²·f (α
the activity factor); the V² is *the* lever: 1.2 V → 0.9 V = 44% less
dynamic power for the same clock — the entire DVFS industry from one
exponent (name it); the total: P = αCV²f + V·I_leak (dynamic +
leakage: the two-term equation that ran the whole 2000s
(why multicore happened: the "power wall" — a 2004-era 4 GHz
single-core planar chip couldn't be cooled; two slower cores at lower
V beat it within the same watts — the industry pivot stated as this
equation's consequence, connecting to module 09's and 12's
parallelism). Voltage, temperature, frequency as a triangle: lower V
→ slower gates (the transistor's drive current drops; the timing
margin shrinks — the PVT corner model (the foundry signs off timing
at "slow 0.72 V 125 °C" style corners; chips ship with margin, and
binning (the same design sold at 3.5/4.0/4.5 GHz by corner) is the
margin's business model; the enthusiast's undervolting as the
consumer-side arbitrage of that margin — insider folklore, stated
with the honesty). The clock: why synchronous design won (the
alternative (asynchronous/self-timed logic) exists (the async ARM
research chips), but verifiability chose the clock: one global tick
turns a continuum of analog states into discrete cycle numbers —
"a clock is a data structure for time" (the phrasing the module
will reuse in ch02); the clock tree (the H-tree of buffers delivering
the tick to every flip-flop: the skew (arrival-time differences
(~10s of ps) eats into the cycle budget — the skew budget in the
timing equation of ch02; the PLL (the clock multiplied from a quartz
reference (100 MHz × 40 = 4 GHz), the clock domains + the CDC
problem (a signal crossing domains samples at an arbitrary phase →
metastability (the flip-flop caught mid-transition can hang at
half-voltage — the synchronizer: two flip-flops in series (the first
may go metastable, resolves before the second samples — the
probability arithmetic: MTBF = e^tr/τ / (fclk·fdata) — the 2-flop
sync's MTBF in years stated with real numbers (the design rule "1
bit at a time, then a handshaking gray code" for buses (a multi-bit
crossing can tear (each bit resolves independently: the Gray-code
counter trick (only one bit changes per step — the FIFO's
async-pointer trick that returns in ch03). Lab: a paper lab + a
Python gate simulator: the chapter ships a ~50-line event-driven
simulator (gates as functions with delays — the code inline), the
exercises: build an inverter chain of 4, sweep the input frequency,
find where it stops propagating (the RC-limit observed in software);
compute the dynamic power of a 1 GHz, 1.0 V, 10 fF-loaded, α=0.1
gate (the worked number: 1 mW) and scale to 10⁹ gates (the thermal
wall reached on paper); the FO4 measurement in the simulator (the
fanout-4 chain's period).

**Chapter 02 — state and the pipeline.** Memory from logic (the
deepest trick in the chapter): the cross-coupled inverter pair (two
inverters in a loop: each drives the other's input — the loop has two
stable states (0→1 and 1→0) = one bit (the bistability drawn with the
"flywheel" analogy (it holds its state the way a spinning wheel holds
its position); the SR latch from NOR gates (S=1 sets, R=1 resets;
the S=R=1 forbidden state — why both-asserted is nonsense: both
outputs agree and the *level*, not the edge, is what's stored); the
gated (transparent) latch (the enable: D passes through while enable
is high — transparency is the hazard: D changing mid-enable changes
Q (the "the latch sees the whole half-cycle" problem); the
master-slave edge-triggered flip-flop: two gated latches in series
with inverted enables (the master open on low phase, slave on high —
the data frozen between them: **exactly one moment per cycle (the
rising edge) can change the output** — the trick derived as "lock
the inner door before opening the outer one"). Timing: the setup and
hold constraints derived from the master latch's transparency
(setup: D must be stable t_su before the edge (the master must
sample the settled value); hold: D must stay stable t_h after (the
master's transparency window must close before D moves); the
insider's asymmetry: **setup violations are fixed by slowing the
clock; hold violations cannot be** (hold compares two *arrival
skews* with no clock period in the equation — a hold failure at 100
MHz fails at 1 Hz; the fixes are hold-buffer insertion (extra delay
gates) or, if missed, the silicon respin (the "hold bugs are the
expensive kind" folklore); the max frequency: f_max = 1/(t_c2q +
t_logic + t_su) — the critical path as the register-to-register race;
slack (the margin: negative slack = a path that misses; the static
timing analysis (STA) tool's report: every path checked against the
clock (why "timing closure" is a whole engineering discipline, ch05).
**Pipelining derived**: the combinational blob (one add = 3 ns can't
run at 1 GHz (f_max = 333 MHz); cut it into 3 stages with flip-flops
(each stage ~1 ns: f_max → ~1 GHz (minus the register overhead: the
c2q + su per stage — the overhead is why you don't cut into 100
stages (the diminishing returns curve drawn: the k-stage pipeline's
f_max = 1/(D/k + overhead) → the optimal k derivation); the
throughput/latency trade stated precisely (a 3-stage pipe: latency =
3 × stage time (slightly *worse* than the blob), throughput = one
result *per cycle* (3× the blob at the same clock... carefully: the
same max clock rises with pipelining; the worked numeric example in
a small table). Hazards (the price of the pipeline): data hazards
(RAW: the add's result needed by the very next instruction — the
naive stall (the bubble: the wasted cycle; **forwarding** (the
bypass network: the ALU's *output* routed back to its input before
the writeback stage — the 3-input mux added to the ALU inputs (drawn;
the load-use case: even forwarding can't fix the data that arrives
at cycle 4 being needed at cycle 3 (the one mandatory stall — why
compilers schedule loads early (the lesson's software echo), control
hazards (the branch decides the fetch's direction — the wrong-path
fetch: 2-3 wasted slots on a taken branch in the 5-stage; the fixes
laddered: predict-not-taken (the cheap default), the 2-bit
saturating counter (the 00-01-11-10 state machine: the accuracy
numbers (the alternating-pattern T,N,T,N = 0% correct (the counter
hunts), the 90%-local-pattern = ~90% (the folklore "branches are
~90% predictable" is *this* measurement), the BTB (the branch target
buffer: a cache of PC → target — module 01 ch02's tag/index machinery
literally reused for *predicting* the PC (the "the branch predictor
is a cache of the future" phrasing), the mispredict penalty (flush
the younger stages: the penalty = the pipeline depth — the
**Pentium 4 story** (the 20-31-stage Willamette/Prescott design: 3.8
GHz marketed, ~39-cycle mispredict penalty, the power wall
(`C·V²·f` from ch01 with 31 stages of flip-flops = the C term
exploded), Intel's pivot to Core (fewer, wider stages) — the
causality chain stated in full (it's the industry's clearest
"equations decided the product" story); branch prediction today (the
TAGE/perceptron engines: history-based prediction (the "the
predictor learns loops (the 99% accuracy on regular code — the
"silent speculator" framing: the modern CPU executes dozens of
instructions past a branch and throws away the wrong ones — module
01 ch20's speculation + module 02 ch07's side-channel villain is
this exact machinery (one line forward-reference)), structural
hazards (one memory port, two users (the fetch and the load: the
I-cache/D-cache split as the structural fix — *why L1i and L1d are
separate* (module 01 ch02 said "why two L1s" in one line; here is
the derivation). The 5-stage RISC-V/MIPS reference model drawn in
full ASCII (IF/ID/EX/MEM/WB, the forwarding paths, the hazard
detection bubble, the PC+4 chain; the modern out-of-order existence
proof (the ROB as a reorder buffer: instructions *complete* out of
order but *retire* in order (the architectural state only advances
at retire — the speculation safety valve; the register renaming
(the physical register file + the map table: 2-4× the architectural
registers to break false dependencies; the store buffer (module 01
ch20's MESI machinery literally is this — the pointer back to the
hardware memory model). Insiders: the 2-flop synchronizer's MTBF
formula with a worked "why you always see pairs, never singles", the
"the pipeline stage is 15-20 FO4" budget rule, the P4 story (above),
the "the branch predictor is the single most performance-critical
cache in the machine" (the mispredict at 15-20 cycles × 20% branches
= the arithmetic that made prediction the #1 research area
1995-2010), the clock skew's share of the cycle budget (10-20% on a
bad clock tree — why CTS is its own EDA step, ch05). Lab: the Python
branch-predictor experiment (the 2-bit counter simulated over
patterned streams (T/T/T/N, the alternating, the 90/10 random: the
accuracy table produced — the lab produces the chapter's folklore
numbers); the pipeline arithmetic on paper (the 3-stage f_max with
register overhead 0.2 ns, the 10-stage version, the optimum found);
the simulator from ch01 extended: a 5-stage pipeline with a RAW
hazard and forwarding (the code inline: the observable bubble vs
forwarded cycle counts).

**Chapter 03 — RTL: the other discipline.** The paradigm shift (the
chapter's first third — the software engineer's #1 hurdle): HDL code
*describes hardware that exists in parallel*, not steps that execute
in sequence (every `assign` and every `always` block is a circuit
that exists *simultaneously*; the "lines" are not order — the
synthesis tool reads your description and infers gates + registers;
the litany of software intuitions that must die: "the loop executes"
(a `for` in RTL = *unrolled hardware* (N iterations = N copies of
the body); "the variable holds" (a `reg` is not a variable (it's
*maybe* a flip-flop (if written on a clock edge) or just a wire (if
only combinationally driven) — the historical naming disaster of
Verilog's `reg`); "two drivers is an error the compiler catches"
(two `always` blocks driving the same signal = a fight at runtime
(x-propagation in sim, a short in silicon) — the classic
"multi-driver" bug; "the simulator is the truth" (simulation passes,
silicon fails: the X-propagation and the race conditions — the
blocking-assignment race below). The Verilog subset for the working
engineer (the chapter's second third — a deliberately small grammar):
`module`/ports, `wire` vs `reg`, the `assign` (continuous:
combinational), `always @(*)` (combinational, use `=` blocking),
`always @(posedge clk)` (sequential, use `<=` nonblocking), and THE
classic bug derived: blocking vs nonblocking in a clocked block (the
counter: `always @(posedge clk) begin b = a; a = b; end` (blocking:
both get the same value — a swap that doesn't work (the sequential
semantics of `=` executing in order within the block!) vs `b <= a;
a <= b;` (nonblocking: both sample the *old* values (the "schedule
all reads, then all writes" semantics — the swap that works; the
rule: **`<=` in sequential blocks, `=` in combinational; mixing them
in the same block is a firing offense** (the insider's lint rules:
`always @(posedge)` + `=` → the linter's default error); the
simulator's event queue (the delta-cycle model: the simulator
evaluates in rounds (nonblocking writes land in the *next* round —
the reason nonblocking models the flip-flop correctly (the value
only becomes visible "after the edge"); the four-state logic (0/1/X/Z
(X = unknown (uninitialized, contention, out-of-range (the X is a
*diagnostic*, not an error — the "X-propagation" folklore: silicon
doesn't have X (it has a real voltage), so an X that vanishes in
gate-level sim is a bug hiding; the `initial` block's simulation-only
status). The design pattern that matters most: **the FIFO** (the
chapter's worked artifact — the same ring the whole program has been
meeting): the synchronous FIFO in ~40 lines, built in front of the
reader (the storage (a reg array — the BRAM inference note), the
pointers (read/write incrementing mod N), the full/empty problem
derived (the 4-slot FIFO's pointers wrap: full = write caught up to
read = the *same* pointer values as empty — the classic ambiguity
(N+1 states, N pointer values: the impossibility arithmetic; the two
fixes: the extra count register (a state counter: simple, costs
registers) or **the one-extra-bit pointer trick** (the pointers hold
N+1 states: the wrap bit disambiguates — full = (w == r except the
top bit differs; empty = (w == r entirely — the bit trick drawn;
the async FIFO (the two-clock cousin: the CDC problem from ch01 —
the pointers crossed between domains must be **Gray-coded** (only
one bit changes per increment → a torn read (sampling mid-change)
still reads a *valid* (if stale) pointer — never a garbage value;
the Gray-code FIFO as the standard IP block (the one every team
buys vs builds (the insider's "async FIFOs are where careers go to
die" folklore; the showahead/skid-buffer variants (the skid buffer:
the pipeline-safe register slice that lets a pipeline stage accept
back-to-back beats (the ready/valid handshake (the valid/ready
protocol (AXI-style) — the stall propagation: a stage that stalls
must hold its output (the skid buffer's extra register decouples
the hold from the upstream's ability to advance — the 2-deep skid's
throughput proof: 100% beats accepted even with 1-cycle bubbles in
ready). Synthesis: what the tool does (the RTL → generic gates →
technology mapping (the standard-cell library: the NAND2_X1's
area/delay numbers (the liberty file), → placement (the die map) →
routing (the metal layers) — the timing report read (the setup
check per path (the slack table: the critical path named (the
"this design's critical path: the ALU's carry chain at −0.4 ns
slack" — the fix ladder (restructure the RTL / retime (move the
registers (the retiming transform: the same machine, registers
moved across logic — the mathematical equivalence that saves the
redesign), the area/delay/power report triangle); the FPGA vs ASIC
paths (the FPGA: the RTL mapped to LUTs (the **6-input LUT as a
64-bit ROM** (any 6-input boolean function (one LUT = the 64-entry
truth table programmed by the bitstream (the elegance: *no
technology library needed* — the function *is* the cell; the fabric:
LUT + flip-flop + carry chains (the fast carry path (the dedicated
carry wire that makes adders fast (the 32-bit add in one LUT level +
carry propagation (~1-2 ns) — the FPGA's arithmetic secret; the
ASIC: the standard cells (the NAND2_X1 at ~0.5 µm², the DFF at
~2 µm², the total gate count → the mm² estimate (the synthesis
report's area number → the floorplanning start); the two economics
foreshadowed (ch05's payoff). Insiders: the lint rules that survive
(`default` in every case (the latch inference trap: a missing
`default` in an `always @(*)` infers a *latch* (the comb block that
remembers — the "your combinational logic has state" bug (the
synthesis warning (the latch inferred) that linters escalate to
errors; the reset strategy (sync vs async reset: the async reset's
removal-timing hazard (the reset release must be synchronous or the
flops come out of reset on different edges — the reset
synchronizer pattern (the 2-flop from ch01 again)), the clock
crossing checklist (every crossing documented: 1-bit-synchronizer /
gray-code / async-FIFO / handshake — the four sanctioned patterns),
the "sim passes, chip fails" top-3 (X-optimism (the sim resolved X
to a lucky 0), the uninitialized RAM (the sim's 0s vs silicon's
random), the CDC (the race the sim's deterministic scheduling
masked). Lab: **install and use iverilog** (the apt/pip route shown;
if unavailable, the chapter's testbenches are given with their
*expected* output so the reader can desk-check): the FIFO built,
testbenched (the self-checking testbench pattern (the golden-vector
compare — the discipline that scales), the VCD dumped and read (the
waveform as text (the `#end` trace explained line by line), the
blocking-vs-nonblocking bug reproduced (the broken swap counter and
the fixed one, both simulated, both outputs shown), the latch
inference reproduced (the missing `default` → the warning read).

**Chapter 04 — the cache in RTL: a tiny CPU's memory system.** The
SRAM cell (the 6T cell drawn: two cross-coupled inverters (the
ch02 latch!) + two access transistors; the read: precharge the
bitlines, assert the wordline, the cell pulls one bitline down (the
differential sense amplifier races to resolve (the read time = the
RC of the bitline (the long line = many cells' worth of capacitance
(the bitline's length is why big SRAMs are slow — the derive:
doubling rows ≈ doubling bitline C ≈ +40% access time (the sqrt
scaling); the write: drive the bitlines hard enough to *overpower*
the cell's inverters (the write margin — the asymmetry with read
(reading must not flip it: the read-stability constraint (the
cell ratio problem: the pull-down vs access transistor sizing —
the "SRAM cells are sized for stability, not speed" insider fact);
why 6T SRAM costs: 6 transistors vs DRAM's 1T1C (module 01 ch01's
DRAM cell returns: the 6:1 area ratio → L1's 32-64 KB vs DRAM's
GBs (the same trade at opposite ends); the **register file** as the
multiported SRAM (2 read + 1 write ports per cycle (the two read
ports feed the ALU's two operands (module 02 ch02's forwarding mux
is what sits *around* this file); the ports' cost: each port ≈
+1 wordline + 2 bitlines ≈ +40-60% area (the 2R1W file ~2.3× a 1R1W
— why wide-issue CPUs' register files balloon (the 8-read file of a
6-wide machine: the area arithmetic (the bypass network's wires
around it (the "the register file + bypass is half the core's
area" insider claim, honestly marked as an approximation). The
direct-mapped cache in RTL (the chapter's centerpiece artifact —
the ~80-line Verilog given inline, built as the design narrative:
the tag array + data array (two SRAMs), the index/offset split
(module 01 ch02's address anatomy: `addr[11:5]` index, `addr[4:0]`
offset for a 128-line × 32 B cache), the lookup's critical path
drawn as the RTL timing story: SRAM read (the tag + data in
parallel) → the comparator (tag match) → the output mux — **this
path must fit in one cycle's stage budget (the "L1 is timed to hit
in the pipeline's MEM stage" — the constraint that *sizes* L1
(bigger L1 = slower bitlines = the cycle's FO4 budget blown: the
derivation of the 32-64 KB L1 sweet spot (why not 1 MB L1: the
sqrt scaling of the bitline RC vs the cycle's fixed budget), the
miss path: the stall (freeze the PC (the pipeline's hazard
machinery from ch02 reused verbatim — the cache miss *is* a
structural hazard on the memory port), the miss FSM (IDLE →
REQ → WAIT (the memory bus's ready) → FILL (the burst: the 4-beat
fill of a 32 B line (the bus width/cycle trade) → REFILL the data
array + set valid → RESUME; the write path: write-through + the
write buffer (the naive WT's problem: every write stalls the bus
(the write buffer absorbs (the read-over-write hazard in the buffer
(the bypass check (the insider's "write buffer coherency" bug
class) vs write-back + the dirty bit (the fill/evict path: a dirty
evict = write-back the victim *then* fill (the miss penalty
doubles on dirty lines — the arithmetic), the MESI preview (the
dirty bit is the M state's ancestor (module 01 ch20's protocol
needs write-back — the pointer back). Associativity as mux cost
(the 2-way: two tag compares + the hit mux (the +1 mux delay on
the critical path (the way-prediction trick (predict the way,
check later — the misprediction's penalty vs the saved cycle) — the
design table: associativity vs hit latency vs conflict misses (the
"2-4 way for L1 (timing-bound), 8-16 way for L2/L3 (miss-bound)" —
the different *reasons* per level stated (this is the module 01
ch02 insight, now with the RTL timing behind it). The DRAM timing
model (the ch's third artifact, in Python): the bank/row/column
hierarchy (the row (the sense-amp "page": activate (tRCD ~14 ns)
then column (tCAS ~14 ns) — the **row hit** (the open-page hit: the
column access alone (the ~30 ns "page hit" vs the ~50 ns miss (the
page-hit rate as the memory-controller's optimization target (the
scheduler: reorder pending requests to exploit open rows (the
memory controller is an out-of-order engine for DRAM (the modern
CPU's most underrated block); the refresh (tREFI: every cell leaks
(module 01 ch01's capacitor) — the ~64 ms all-rows refresh cycle
steals ~3-5% of bandwidth (the number for the "why does idle DRAM
still burn bandwidth" question; the burst/prefetch (DDR's 8n: the
core reads 8 words internally at its slow clock, the IO serializes
at 8× (the naming decoded: DDR4-3200 = 3.2 GT/s × 8 B = 25.6 GB/s
per 64-bit channel (PC4-25600's name now derivable by the reader);
the lab's model computes random vs sequential access latency
(the 30/50 ns split + the queue-depth throughput curve (the
latency-bandwidth curve the whole program keeps meeting (module 12
ch02's roofline's x-axis is this). Insiders: the "L1's size is a
*timing* decision, not a capacity decision" (the single most
quotable claim), the way-prediction trick, the write-buffer
coherency bug class, the memory controller as an OoO engine (the
bank-conflict scheduler folklore ("the MC's reorder window is where
hidden performance lives"), the refresh tax (~3-5%), the die-photo
argument (the L3 is half the die, the ALUs a sliver — the "the
memory wall is visible to the naked eye" payoff (cite the public
die-shot analyses (the Apple M1's ~60% memory-system share as the
modern datapoint). Lab: the cache's Verilog given (the 80-line
listing + the self-checking testbench: a hit/miss trace (the hit
ratio printed), the reader modifies it: (a) add the dirty-bit
write-back (the miss penalty visibly doubles on evictions), (b)
make it 2-way (the conflict-miss count drops: the measured
miss-rate delta on a small access trace); the Python DRAM model
(the row-hit vs miss latencies, the queue-depth sweep — the graphs
sketched from the printed output).

**Chapter 05 — from RTL to silicon: the economics of hardware.** The
foundry model (the industry's structure in one diagram: fabless
designers (Apple/AMD/NVIDIA/Qualcomm) → EDA vendors (Synopsys/
Cadence: the tool rents) → foundries (TSMC/Samsung/Intel) → the
equipment layer (ASML: the *sole* EUV lithography supplier — the
13.5 nm light source (the laser-produced plasma: a tin droplet hit
twice by a CO₂ laser 50,000 times/second — the ~$150-200 M per
machine, the single-source chokepoint stated plainly (the 2019-
2021 export-control era); the process node *myth* (the "3 nm" is a
marketing label (the transistor density per node label now diverges
from any physical feature (the public numbers: a "5 nm" node's
gate pitch ~48 nm, metal pitch ~30 nm — nothing is 5 nm; the node
names track a density *class* (the historical connection to the
half-pitch and its death (the insider's "compare by density and SRAM
cell size, not node names" rule (the SRAM cell size's awkward truth:
it barely scales past ~5 nm (the 6T cell from ch04 at ~0.02 µm² —
the reason caches stopped shrinking with logic (the "SRAM scaling is
dead" problem (the cache-dominant die's consequence: the density
gains land on the compute sliver — the accelerator-economics
connection). The design flow's back half (what ch03's synthesis
started): floorplanning (the blocks placed (the macro placement
(the place-and-route tools (the congestion problem (the routing
channels' traffic jam (the ECO loop: the engineer's
edit-re-route-re-time iterations (the 20-30 iteration folklore for
a big chip), clock tree synthesis (ch01's H-tree at scale: the
buffers sized so the skew lands in budget), signoff (DRC (the
foundry's geometric rulebook (the antenna rule (a metal line
charging during etch can fry a gate — the antenna diodes (the
rules that sound like superstition but are physics), LVS
(layout-vs-schematic: the drawn circuit matches the netlist), ERC;
DFT (the whole discipline the software world never meets: **scan
chains** (the flip-flops strung into shift registers for test: in
test mode you *shift in* a state, pulse one clock, *shift out* the
result — the entire chip becomes a giant deterministic FSM tester
(the stuck-at fault model (a wire stuck at 0/1: the fault coverage %
(the ATPG tools' job: patterns that distinguish stuck-at faults (the
"99% stuck-at coverage" signoff bar), **memory BIST** (the built-in
self-test: each SRAM gets a tiny state machine that marches test
patterns through it (the march algorithms (the C-style march
(x(w);r(x);w(~x)... the fault classes caught (stuck bits, coupling,
address faults — the insider's "your CPU runs memory BIST on every
boot" (the boot-time cost in the 100 ms class (part of every
power-on (the POST's deep ancestor — the reason a cold boot's
black-screen pause exists). **Yield and cost arithmetic** (the
chapter's quantitative core, everything on paper): the Poisson
defect model: **Y = e^(−D₀·A)** (D₀ the defect density per cm², A
the die area — derived from first principles (defects as a spatial
Poisson process: the probability a *given* area is clean is
e^−λ; the worked table: D₀ = 0.1/cm² (a mature node): a 100 mm²
die → Y = e^−1.0 = 37% (a 500 mm² die (a big GPU) → Y = e^−5 = 0.7%!
(the 53× yield ratio for 5× the area — **why big chips are
exponentially expensive** (the cost per *good* die = (wafer cost /
gross dies × yield) — the full worked example: a 300 mm wafer's
~70,685 mm², at $10,000/wafer (a leading-node ballpark), a 500 mm²
reticle-limited die: ~118 gross dies × 0.7% yield = **~$12,000 per
good die** (the flagship-GPU cost structure derived on paper; the
**chiplet** solution derived: cut the 500 mm² into 8 × 62 mm² dies:
each at Y = e^−0.062 ≈ 94% (the 8-chip package's yield ≈ 0.94⁸ ≈
61% (the packaging cost added (~$50-150 per advanced package) vs
the yield saved — the AMD Zen 2-4 architecture as this arithmetic's
product (the "the chiplet is a yield hack that became an
architecture" phrasing; the reticle limit (~850 mm² (the
lithography field's physical max) — why monolithic dies capped; the
advanced packaging (CoWoS (the interposer that carries the HBM
stacks next to the GPU (module 12 ch01's HBM returns as a
*packaging* story (the 3D stacking (the hybrid bonding (the
TSV (the through-silicon via: the die's wires that go *through* it
(the HBM's 1024-bit buses exist because the stacks are bonded
(hundreds of µm apart, not cm — the distance argument for the
bandwidth (the physics closes the loop: module 12's 2-3 TB/s HBM is
a packaging achievement). The closing economics→architecture
feedback loop (the module's last table): why GPUs are arrays of
small repeatable cores (yield (a dead core is binned out, a dead
big-core is a dead die: the salvage/binning model (the GPU's
1064-of-1080-cores SKU as yield *harvesting* — the insider's "the
SKU list is the yield curve's product"), why accelerators win
(the roofline as economics: FLOP/$ and FLOP/W (module 12 ch02's
ridge point as a *budget* line), why memory stopped scaling
(SRAM's stall + the HBM path + CXL (module 12 ch04) as the system
bus's future (the disaggregation argument: pooling the expensive
thing whose yield/size/cost curve diverged from logic's), and the
hardware-software co-design endgame (the "hardware is a compiler
for money: the RTL is a program whose output is a cost structure"
closing claim, tied back to module 01 ch01's "memory is shaped by
physics" and forward to "every module of this program was a
consequence of these 5 chapters' physics and economics"). Insiders:
the node-name rule (density and SRAM size, not nm), the SRAM-scaling
wall (the cache-die-share consequence), the binning-as-harvesting
model, the DFT trio's signoff bars (stuck-at 99%+, the march
coverage per macro), the reticle/CoWoS/TSV vocabulary, the "cold
boot waits for memory BIST" fact. Lab: all paper, all runnable in a
script: the yield calculator (Python: wafer cost, die size, D₀ →
cost per good die — the chapter's table reproduced and extended:
find the die size where a 2-chiplet split beats monolithic at D₀ =
0.15), the wafer-map (the gross-die packing (the circle-packing
loss at the wafer edge (the edge-exclusion 3 mm), the SKU
harvesting model (given a defect density and a 4-core die, what % of
dies yield 4/3/2/1 cores (the binomial-with-clusters approximation:
the SKU mix computed), the HBM bandwidth-vs-distance back-check
(the µm vs cm argument's arithmetic).

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **The tower's foundation**: L1's size as a timing decision, the
  register file's port costs, the SRAM/DRAM area split — module 01
  ch02's table, now *derived from silicon upward*.
- **RTL literacy**: you can read a small Verilog design, spot the
  blocking/nonblocking bug and the inferred latch, and understand
  what synthesis actually does.
- **Hardware judgment**: pipeline economics, PVT corners, the
  foundry vocabulary — you can follow a chip announcement or an
  industrial-engineering story (EUV, chiplets, binning) critically.
- **The completed program**: physics → economics → architecture →
  OS → language → security — the full stack, closed.
