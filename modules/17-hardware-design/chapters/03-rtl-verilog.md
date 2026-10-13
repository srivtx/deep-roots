# Chapter 03 — RTL: the other discipline

> **The 30-second version.** A software language tells a machine what to do
> *next*; a hardware description language tells a factory what circuit to
> *build*. Every line of Verilog is hardware that exists all at once, in
> parallel, forever — there is no "after line 7" for line 8 to wait in.
> From that inversion: a small grammar (modules, `wire` vs `reg`, two
> kinds of `always`); two assignment operators (`=` computes now, `<=`
> remembers until the clock edge — the ch02 flip-flop as punctuation);
> four-state logic where X is a *diagnostic*; and a synchronous FIFO in
> ~40 lines whose full/empty ambiguity (N+1 occupancies, N pointer
> values) is dissolved by one extra pointer bit — the same ring
> discipline as the DMA ring, the vring, and io_uring. Then what
> synthesis does with your text, and why "passes in simulation" is
> weaker than "works in silicon."

## The lines are not order: text that describes a machine

In RTL the text describes a circuit that exists all at once: every
`assign` and every `always` block is a live circuit, and synthesis reads
the whole description to *infer* gates and registers. Four software
intuitions must die, each a real bug class.

**"The loop executes."** A `for` in RTL is *unrolled hardware*: N
iterations mean N copies of the body, built before the machine runs.

```verilog
// This loop does not "run". It DESCRIBES four gates that exist forever.
always @(*) begin
    for (i = 0; i < 4; i = i + 1)
        out[i] = in[i] ^ key[i];   // 4 XOR gates, parallel, 0 sequence
end
```

The trip count must be known at elaboration; a bound from a *runtime
signal* cannot synthesize — gates cannot be grown after manufacturing.

**"The variable holds."** Verilog's `reg` is a historical naming
disaster: *maybe* a flip-flop, maybe a wire, decided only by how it is
driven.

```verilog
reg [7:0] a, b;
always @(*)          a = x + y;    // this reg is an ADDER (combinational)
always @(posedge clk) b <= x + y; // this reg is 8 FLIP-FLOPS
```

**"Two drivers is caught by the compiler."** In C two writes are
ordered; in RTL they are two circuits fighting over one wire:

```verilog
assign y = a & b;   // driver 1
assign y = c | d;   // driver 2: disagreement -> X in sim, a SHORT in silicon
```

Two continuous `assign`s on one net give X — contention. Sneakier: two
`always` blocks writing one `reg` — simulators accept it silently and
pick an arbitrary winner; synthesis *rejects* it as a multiple-driver
error. Simulation is the permissive one, the inverse of the software
intuition.

**"The simulator is the truth."** Sim passes, silicon fails:
X-optimism, uninitialized memory, masked clock crossings (insiders'
section). The simulator is a fast model, not an oracle.

## Two assignments, one rule: `=` computes, `<=` remembers

The working grammar is five constructs: `module` with a port list (a
reusable circuit's boundary); `wire` for nets driven continuously, `reg`
for things driven from `always` blocks (syntax, not hardware);
`assign` = continuous, combinational, live at every instant;
`always @(*)` = combinational, drive with blocking `=`;
`always @(posedge clk)` = sequential, drive with nonblocking `<=`.

**Blocking `=` in combinational blocks, nonblocking `<=` in clocked
blocks — mixing them in one block is a firing offense** (lint tools
escalate it). The classic bug that makes the rule real:

```verilog
// swap_bug.v — the same two statements, two semantics.
`timescale 1ns/1ns
module swap_bug;
    reg clk = 0;
    always #5 clk = ~clk;
    reg [7:0] a = 8'hAA, b = 8'h55;   // blocking  '=' (BAD)
    reg [7:0] p = 8'hAA, q = 8'h55;   // nonblocking '<=' (GOOD)

    always @(posedge clk) begin
        b = a;    // blocking: b becomes AA *now*, in this round...
        a = b;    // ...so this reads the NEW b: a <- AA. No swap: both AA.
    end

    always @(posedge clk) begin
        q <= p;   // nonblocking: reads see the OLD values (AA, 55);
        p <= q;   // writes land after the block. Real swap: p=55, q=AA.
    end

    initial begin
        @(posedge clk); #1;
        $display("blocking '=': a=%02h b=%02h", a, b);   // aa aa
        $display("nonblocking: p=%02h q=%02h", p, q);    // 55 aa
        $finish;
    end
endmodule
```

The mechanism is the simulator's **event queue**: time advances in
evaluation rounds, *delta cycles*, no time passing between rounds.

```
 posedge clk at t = 5 ns
 ------------------------------------------------------------------ time ->
 round 0 (evaluate):  blocks run; reads see values from t < 5.
                      'b = a': b flips 55 -> AA IN ROUND 0, so 'a = b'
                      sees the new b.
                      'q <= p', 'p <= q': reads see old AA/55; the
                      WRITES are queued, not done.
 round 1 (NBA):       queued nonblocking writes land: q <- AA, p <- 55.
 (postponed):         $display sees the settled values.
```

`<=` schedules the write into the next round — the flip-flop's
contract: the value becomes visible only *after* the edge. Blocking `=`
in a clocked block models a logic chain resolving within one edge;
every later statement sees the update, and your "two registers"
collapse into one. The rule is not style: `<=` is the only assignment
whose semantics match the hardware you meant.

Last piece: **four-state logic**. Every bit is 0, 1, X (unknown), or Z
(high impedance). X is born of uninitialized state, contention, or
out-of-range selects (`mem[11]` in an 8-slot memory). Insider stance:
**X is a diagnostic, not an error** — silicon has no X, a real wire has
a real voltage, so an X that quietly *vanishes* in gate-level sim is a
bug hiding. Related: `initial` blocks are *simulation-only* — FPGA
flows honor them as bitstream power-up values, ASIC flows ignore them.

## The ring in silicon: a FIFO in ~40 lines

The program keeps meeting one structure: the DMA ring (module 13), the
vring (module 15), io_uring (module 13 ch04). Hardware got there first,
in ~40 lines:

```
 index (ptr[2:0]):   0      1      2      3      4      5      6      7
                  +------+------+------+------+------+------+------+------+
                  |  01  |  02  |  03  |  04  |  05  |      |      |      |
                  +------+------+------+------+------+------+------+------+
                    stale   ^                                    ^
                  (popped) rptr = 001                            wptr = 101
                            next read: slot 1                    next write: slot 5

   slot 7 + 1 wraps to slot 0: +1 is mod 8 on the low 3 pointer bits.
   occupancy = wptr - rptr = 101 - 001 = 4 beats: 02 03 04 05.
```

```verilog
// fifo_sync.v — synchronous FIFO in ~40 lines. Verilog-2001.
// DEPTH = 2**ADDR_W slots, one clock, pointers carry 1 extra bit.
module fifo_sync #(
    parameter ADDR_W = 3,              // 3 ring bits -> 8 slots
    parameter DEPTH  = 8               // must equal 2**ADDR_W
)(
    input  wire       clk,
    input  wire       rst,             // synchronous, active high
    input  wire       wr_en,
    input  wire [7:0] wdata,
    input  wire       rd_en,
    output wire [7:0] rdata,           // showahead: head when !empty
    output wire       full,
    output wire       empty
);
    reg [7:0] mem [0:DEPTH-1];         // storage: 8 words of 8 bits
    reg [ADDR_W:0] wptr, rptr;         // 4 bits each: 3 ring + 1 wrap

    // flags: pure functions of the pointers (combinational, no state)
    assign empty = (wptr == rptr);                    // all 4 bits equal
    assign full  = (wptr[ADDR_W] != rptr[ADDR_W])     // wrap bit differs
               && (wptr[ADDR_W-1:0] == rptr[ADDR_W-1:0]); // ring bits equal

    assign rdata = mem[rptr[ADDR_W-1:0]]; // unregistered read of the head

    always @(posedge clk) begin        // THE sequential block: '<=' only
        if (rst) begin
            wptr <= 0;
            rptr <= 0;
        end else begin
            if (wr_en && !full)  wptr <= wptr + 1'b1; // wraps mod 16
            if (rd_en && !empty) rptr <= rptr + 1'b1; // ring bits mod 8
        end
    end

    // memory ONLY here: one process per signal (no multi-driver fight)
    always @(posedge clk)
        if (wr_en && !full)
            mem[wptr[ADDR_W-1:0]] <= wdata;
endmodule
```

Read with ch02's eyes: storage and pointers are flip-flops; the flag
equations are combinational logic feeding the enables. On an FPGA, a
`reg` array with synchronous write infers a memory macro — synchronous
read too would infer block RAM (BRAM); the *unregistered* read here
(the "showahead"/first-word-fall-through style) maps to distributed LUT
RAM on Xilinx parts. In an ASIC flow the array becomes an SRAM macro
plus read muxes: same text, different memory compiler per target.

The one deep problem — the reason this artifact is famous: **full and
empty have identical pointers.** A FIFO with N slots has N+1 legal
occupancies (0..N), but a naive N-value pointer pair encodes the gap
`(w - r) mod N`, only N values. N+1 states pigeonholed into N values:
two must collide, at both ends:

```
 DEPTH = 4, naive 2-bit pointers:

   EMPTY: w = 00, r = 00              FULL: w = 00, r = 00
          (0 pushes)                        (4 pushes: 00->01->10->11->00)
          identical pointer values. No boolean function of (w, r) alone
          can separate them: the information is not there.

 the fix, 3-bit pointers (2 ring + 1 wrap bit):
   empty = (w == r)                          e.g. 000 vs 000
   full  = w[2] != r[2] && w[1:0] == r[1:0]  e.g. 100 vs 000
```

Two standard fixes, both in production everywhere. **A count register**:
occupancy counter 0..N — for DEPTH=8 that is 9 values, 4 flip-flops,
plus an adder on the flag path. **The one-extra-bit pointer trick**
(used above): each pointer is ADDR_W+1 bits, counting 0..2N-1 and
wrapping mod 2N; the top bit records how many times this pointer has
wrapped past the other. Empty: all bits equal. Full: ring bits equal,
wrap bit differs — 2 flip-flops total, and the flags are comparators.
This is the scheme in Cummings' SNUG 2002 FIFO papers and the industry
default. Note what the extra bit really is: the pointers are now
*unreduced counters* (mod 2N), occupancy is just `w - r` — software
rings use the same trick magnified (quiz 8).

## Two clocks, one handshake: Gray pointers, skid buffers, bought IP

When write and read live in different clock domains, the pointers must
cross — chapter 01's CDC problem. The read domain needs wptr to decide
"empty," passing it through a 2-flop synchronizer. The hazard: a
multi-bit value sampled mid-change can *tear* — some bits old, some
new: a garbage pointer that never existed.

The fix is **Gray coding**: increments in which exactly one bit
changes. Binary 3 (`011`) to 4 (`100`) changes 3 bits; a torn sample
could read `111` — a pointer apparently 4 ahead, telling the read side
data exists that does not, so it reads *unwritten* memory. In Gray, a
mid-change sample catches the single changing bit old or new — old
pointer or new pointer, both valid, never a third value:

```
 count   binary   gray     bit that changed
   0      000     000        -
   1      001     001       bit 0
   2      010     011       bit 1
   3      011     010       bit 0
   4      100     110       bit 2     <- only this bit flips, 3 -> 4
   5      101     111       bit 0
```

One property completes the argument: **stale is the safe direction.**
Each domain decides using a stale copy of the other's pointer (2-3
cycles old — synchronizer latency). A stale write pointer makes the
read side *under*-estimate available data — it stalls a beat too long,
never a wrong read; a stale read pointer delays a write rather than
overwriting unread data. The async FIFO is correct *because* its
information is out of date in the conservative direction. Hence the
folklore: "async FIFOs are where careers go to die" — it looks like
the sync version plus a few lines, and every line is a correctness
argument, so teams with tapeout deadlines buy it as IP.

Within one domain, pipelines are kept fed by the **ready/valid
handshake** (AXI-style): transfer happens when both are high; the AMBA
AXI spec (ARM IHI 0022) requires `valid` to not wait for `ready`, while
`ready` may wait for `valid`. The failure mode is **stall propagation**:
a stalled stage holds its output, and if its `ready` is a combinational
function of downstream's `ready`, the stall races back up the pipeline
— a ready-chain through every stage, the long-path timing disaster
(ch02's cycle budget spent on handshakes instead of work). The **skid
buffer** breaks the chain with one extra register: output + skid
register, capacity 2 beats, with `ready` a function of local state only
("skid empty") — no combinational path crosses the stage. The
throughput arithmetic: a 1-cycle bubble in downstream `ready` costs
nothing — the in-flight beat parks in the skid, drains next cycle,
100% of beats accepted; longer stalls fill the 2-beat capacity, then
correctly backpressure upstream. Price: +1 cycle latency and 2
registers, paid wherever a ready-chain threatens timing closure.

## From description to gates: what synthesis does with your text

Between your `.v` file and a chip is a compiler with a physical backend:

```
 RTL (.v)
   | elaboration (modules expanded, parameters resolved, loops unrolled)
   v
 generic gates -> technology mapping: standard cells from the .liberty
   v            (per cell: area, delay, capacitance, power)
 placement (cells get x,y on the die map) -> routing (metal layers)
   v
 signed-off netlist (timing, power, DRC checked)
```

The **liberty file** is the cell library's price list: a `NAND2_X1` in
a 28 nm-class library occupies roughly 0.5 um^2 (0.4-1.0 um^2 across
libraries and drive strengths), a DFF about 2 um^2. The synthesis
report's total cell area over available die area is the first floorplan
sanity check — gate count to mm^2 is this multiplication.

The report you are judged on is **timing**. Per register-to-register
path the tool checks setup: arrival before the next edge, with margin
(slack). Negative slack means the cycle does not close:

```text
 Path 1: REG b/5 -> NAND2 -> XOR -> 32b CARRY chain -> REG sum/0
   setup slack: -0.4 ns   (VIOLATED)   <- the critical path
 Path 2: REG a/2 -> mux -> REG b/1
   setup slack:  1.1 ns   (MET)
```

A -0.4 ns violation on the ALU's carry chain means the 32-bit add does
not fit. The fix ladder, in the order teams climb it: (1) **restructure
the RTL** — pipelined or carry-select adder, change the description;
(2) **retime** — move existing registers across combinational logic so
the same machine (mathematically equivalent; Leiserson-Saxe 1991)
rebalances its stages; (3) only then compromise the spec — slower
clock, more latency. Reports come as a triangle — area, delay, power —
and improving one usually costs another.

Two targets make it concrete. **FPGA:** RTL maps to look-up tables, and
a modern 6-input LUT (Xilinx 7-series onward) is literally a 64-bit
ROM: any 6-input Boolean function in one cell, its 64-entry truth table
programmed by the bitstream — no technology library needed, because
*the function is the cell*. Dedicated carry chains make a 32-bit add
run in about one LUT level plus carry propagation, ~1-2 ns on a
mid-range 2010s-2020s part. **ASIC:** the standard cells above, mm^2
from the area report. Two economics foreshadowed for ch05: FPGA = ~0
NRE, high per-unit cost; ASIC = NRE in the millions before unit one (a
28 nm mask set is commonly cited at ~$1-2M) and low per-unit cost.

## What insiders know

- **The missing-`default` latch trap.** An `always @(*)` `case` with no
  `default` infers a *latch* when no arm matches: synthesis prints
  `Warning: Latch inferred for signal ...` (yosys, trimmed), and
  production lint escalates it to an error. Your "combinational" block
  silently has state; sim just holds the old value, so vectors that
  never hit the missing arm pass. Insiders put `default` in every
  `case`.
- **Reset strategy is about release, not assertion.** Asynchronous
  reset asserts cleanly any time, but if *released* near the clock
  edge, different flops exit reset on different edges (recovery/removal
  timing): a state machine starting in two personalities. Fix: the
  reset synchronizer — ch01's 2-flop pattern, synchronizing reset
  *deassertion*.
- **The CDC checklist.** Every signal crossing domains gets one of four
  documented treatments: single-bit 2-flop synchronizer, Gray-coded
  counter, async FIFO, or request/acknowledge handshake. A crossing
  with none of the four is a review-blocking defect.
- **"Sim passes, chip fails" top 3.** (1) X-optimism: an `if (x)` with
  x = X cannot take the true arm, so sim falls into the `else` — if
  that is the branch you tested, the bug walks through masked. (2)
  Uninitialized memory: sim starts arrays at X or 0 (tool-dependent),
  silicon SRAM powers up random. (3) CDC races masked by the
  simulator's deterministic delta-cycle ordering — the race exists,
  your simulator always resolves it the same lucky way; reordering
  `always` blocks and re-running is a poor person's race detector.
- **Lint rules that survive every project.** Blocking `=` inside
  `always @(posedge clk)` is Verilator's BLKSEQ-class warning and a
  default error in most production lint configs. The multi-driver
  check is the other non-negotiable: one process per signal.
- **`initial` blocks are sim-only.** FPGA flows honor them (power-up
  values in the bitstream); ASIC flows ignore them — which is why
  "works on the FPGA, hangs on the ASIC" so often means an unreset
  state machine that powered up friendly on one and hostile on the
  other.
- **Buy the async FIFO.** Gray pointers, synchronizer latency, and
  full/empty in two domains have enough corners that standard practice
  is to license the block and spend the career capital elsewhere.

## See it yourself

Install Icarus Verilog (~2 MB, no license): on Debian/Ubuntu,
`apt install iverilog` (version 11 or 12; any release of the last 5
years runs this lab). Save the FIFO above as `fifo_sync.v`, the
testbench below as `tb_fifo.v`, then:

```console
$ iverilog -o fifo_sim tb_fifo.v fifo_sync.v && vvp fifo_sim
fill 8 : f=1 e=0 w=1000 r=0000
push 9 : dropped (w=1000 f=1)
pop 3  : f=0 e=0 w=1000 r=0011
wrap   : f=0 e=1 w=1011 r=1011
ALL CHECKS PASSED
```

(Output desk-checked against the code above; f=full, e=empty, w/r are
the 4-bit pointers.) This is the self-checking pattern — golden vectors
in, compare every pop, count errors, one verdict — the discipline that
scales to 50,000-test regressions; eyeballing waves does not.

```verilog
// tb_fifo.v — self-checking: golden vectors, error count, VCD trace.
`timescale 1ns/1ns
module tb_fifo;
    reg        clk = 0, rst = 1, wr_en = 0, rd_en = 0;
    reg  [7:0] wdata = 0;
    wire [7:0] rdata;
    wire       full, empty;
    integer    errors = 0, i;

    fifo_sync #(.ADDR_W(3), .DEPTH(8)) dut (
        .clk(clk), .rst(rst), .wr_en(wr_en), .wdata(wdata),
        .rd_en(rd_en), .rdata(rdata), .full(full), .empty(empty));

    always #5 clk = ~clk;               // 10 ns period = 100 MHz

    task push(input [7:0] v);           // one write beat
        begin @(negedge clk) wr_en = 1; wdata = v;
              @(negedge clk) wr_en = 0; end
    endtask

    task pop_check(input [7:0] golden); // one read beat + compare
        begin @(negedge clk) rd_en = 1; #1;  // #1: let rdata settle
              if (rdata !== golden) begin
                  errors = errors + 1;
                  $display("FAIL: popped %02h, expected %02h", rdata, golden);
              end
              @(negedge clk) rd_en = 0; end
    endtask

    initial begin
        $dumpfile("fifo.vcd"); $dumpvars(0, tb_fifo);
        @(negedge clk) rst = 0;

        for (i = 1; i <= 8; i = i + 1) push(i);       // golden 01..08
        $display("fill 8 : f=%b e=%b w=%b r=%b", full, empty, dut.wptr, dut.rptr);
        push(8'h09);                                   // full: dropped
        $display("push 9 : dropped (w=%b f=%b)", dut.wptr, full);
        for (i = 1; i <= 3; i = i + 1) pop_check(i);  // golden 01..03
        $display("pop 3  : f=%b e=%b w=%b r=%b", full, empty, dut.wptr, dut.rptr);
        for (i = 4; i <= 8; i = i + 1) pop_check(i);  // golden 04..08
        push(8'h0A); push(8'h0B); push(8'h0C);        // wrap the ring bits
        pop_check(8'h0A); pop_check(8'h0B); pop_check(8'h0C);
        $display("wrap   : f=%b e=%b w=%b r=%b", full, empty, dut.wptr, dut.rptr);

        if (errors == 0) $display("ALL CHECKS PASSED");
        else             $display("%0d CHECKS FAILED", errors);
        $finish;
    end
endmodule
```

Re-derive every transition by hand: 8 pushes put `wptr` at 1000 (not
000 — the wrap bit), the 9th push bounces off `full`, 3 pops leave
occupancy 5 with `full` deasserted because the *ring bits* now differ,
and the wrap phase proves the ring bits wrapped while the arithmetic
stayed consistent.

**Read the waveform as text.** The testbench dumped `fifo.vcd`. Viewers
exist (`gtkwave fifo.vcd`), but read it as text — a waveform is a log
of changes (trimmed; single-character IDs vary by tool):

```text
$timescale 1ns $end
$scope module tb_fifo $end
$var reg 1 ! clk $end
$scope module dut $end
$var reg 4 " wptr $end
$upscope $end
$enddefinitions $end
#0
$dumpvars
0!
bxxxx "
$end
#5
1!
#45
b0001 "
```

Anatomy: each signal gets an ID (`!`, `"`); `#45` = at 45 ns; `0!`/`1!`
toggle the clock; `b0001 "` sets `wptr`; `bxxxx` is the uninitialized
X, exactly where the four-state model predicts. Only *changes* are
logged.

**Reproduce the two canonical bugs.** Run `swap_bug.v` from section 2
(`iverilog -o swap_sim swap_bug.v && vvp swap_sim`): the broken pair
prints `a=aa b=aa`, the fixed pair `p=55 q=aa`. For the latch, save the
missing-`default` `case` from the insiders' bullet as `latch_trap.v`
and, with yosys installed (`apt install yosys`), run
`yosys -p 'read_verilog latch_trap.v; hierarchy; proc'` — the output
includes (yosys 0.23+, trimmed) `Warning: Latch inferred for signal
'\latch_trap.y' ...`. Icarus stays silent: a simulator has no opinion
on what your combinational block infers; the synthesis warning is where
the bug announces itself.

## How to use this knowledge

- **Datasheets decode.** "RX FIFO 128 bytes, almost-full watermark at
  112" = 128-slot ring, extra-bit pointers, watermark 16 beats below
  full because the write side's view of the read pointer is 2-3 cycles
  stale — the margin absorbs in-flight writes during synchronizer
  latency. You can size that margin yourself instead of trusting it.
- **RTL review gets a four-question litany.** Where do loops unroll
  to? Which `reg`s are flops, which are wires? Who drives each signal?
  Which signals cross domains, and with which of the four sanctioned
  patterns? Fifteen minutes of that catches the bug classes that cost
  tapeouts.
- **Board bring-up triage.** When the FPGA image that simulated clean
  misbehaves on the bench, run the top-3 in order: X resolved
  optimistically in sim; uninitialized state (did every FSM get a
  reset?); CDC (reorder the `always` blocks and re-run — if the answer
  changes, you found a race).
- **Pipeline performance arguments.** A ready-chain that fails timing
  is not fixed by more synthesis effort: insert a skid buffer, pay the
  +1 cycle, keep 100% throughput through 1-cycle bubbles — and know
  why that beats slowing the clock 20%.

## Connections

- **17 ch01** — the 2-flop synchronizer and CDC vocabulary, applied
  here to FIFO pointers.
- **17 ch02** — the flip-flop that `<=` models; the pipeline stages the
  skid buffer feeds; the FO4 stage budget the slack table cashes out.
- **17 ch04** — the cache's tag and data arrays are this `reg`-array
  memory pattern at cache scale.
- **17 ch05** — the area numbers here (0.5 um^2 NAND, 2 um^2 DFF)
  become the mm^2, yield, and chiplet arithmetic there.
- **01 ch02** — the cache line this module rebuilds from transistors
  in ch04.
- **09 ch03** — the software ring's head/tail is the same N+1-state
  problem; 09 ch01's happens-before is the memory-model form of the
  delta cycle's ordering contract.
- **13 ch04 / 15 ch03** — io_uring and the vring: unreduced 32-bit
  head/tail indices, the one-extra-bit trick with 2^32/N wrap bits.

## Quiz

1. A colleague writes `for (i = 0; i < n; i = i + 1) acc = acc +
   data[i];` inside `always @(*)`, with `n` a runtime input. What does
   synthesis say, and what hardware does the *fixed* loop (`i < 4`)
   become?

2. Reset a `fifo_sync` with DEPTH=8 (4-bit pointers), then 8 pushes and
   3 pops. Give `wptr`, `rptr` (binary), `full`, `empty`, and
   occupancy — and say why occupancy is not 3.

3. In `swap_bug.v`, reorder the blocking pair to `a = b; b = a;`
   (start a=AA, b=55). What do `a` and `b` hold after one clock edge,
   and what general fact about blocking assignments does either
   ordering show?

4. A design simulates correctly for every regression vector. On the
   FPGA, an output occasionally holds a stale value for a cycle when a
   2-bit select equals 2'b11. The block is a `case` over that select
   with arms 00, 01, 10 and no `default`. Name the inferred hardware,
   the warning in the synthesis log, and why the regression missed it.

5. A refactor leaves two `always @(posedge clk)` blocks both assigning
   `rptr` with `<=` (one writes 0 on reset, one increments). What does
   the simulator do, what does synthesis do, and what would the
   circuit do if it could be built?

6. For a FIFO with N=16 slots: count the occupancies to distinguish,
   the values a naive 4-bit pair's gap `(w - r) mod 16` can take, and
   why that proves full and empty are inseparable. Give the full and
   empty equations for the 5-bit fix.

7. An async FIFO's write pointer, Gray-coded, changes 010 -> 110. List
   every value the read domain's synchronized copy can take, and why
   neither causes a wrong *read* — then give a torn read possible if
   the pointer were binary 011 -> 100.

8. io_uring's submission ring keeps head and tail as full 32-bit
   indices and masks only to index memory (`idx & (SQ_ENTRIES - 1)`),
   per module 13 ch04. Which of this chapter's two fixes is that, what
   became of the extra bit, and why does the ambiguity never arise
   even after many wraps?

<details><summary>Answers</summary>

1. **The tool rejects it (or forces a silent maximum).** A `for` loop
   unrolls at elaboration: N iterations, N copies of the body, so the
   trip count must be known before hardware exists — gates cannot be
   manufactured after the chip is cut. With `i < 4`: 4 copies of the
   body in parallel (4 XOR gates for the bitwise form in section 1) —
   no counter, no loop control, no sequence.

2. **wptr=1000, rptr=0011, full=0, empty=0, occupancy=5.** Each push
   increments wptr mod 16, each pop rptr mod 16: 8 and 3. Occupancy is
   the difference 1000-0011=0101b=5 — not 3, because 3 is rptr, the
   number of *pops*, not the beats waiting. full=0 because although
   the wrap bits differ, the ring bits (000 vs 011) differ too;
   empty=0 because the pointers differ.

3. **a=55, b=55 — both copies of b.** `a = b` takes 55 immediately (in
   round 0); then `b = a` reads the *new* a: 55 again. Either ordering
   leaves both variables holding one value copied twice. The general
   fact: blocking assignments execute in written order within one
   delta round, so later statements see earlier updates — you
   described a wire, not two registers, and no ordering makes a swap.

4. **An inferred latch; "Latch inferred for signal ..."; the vectors
   never drove 2'b11.** With no matching arm and no default, the only
   semantics available is "y keeps its value" — a level-sensitive
   latch, state in a "combinational" block. Synthesis prints the
   warning; simulation honors the hold silently, so vectors avoiding
   that select pass. The stale value appears only where 2'b11 actually
   occurs. Fix: `default` in every `case`.

5. **Sim: arbitrary winner every cycle, no error; synthesis:
   multiple-driver error; silicon: contention.** IEEE 1364 does not
   define the order of two nonblocking assignments to one reg from
   different processes, so each simulator resolves it its own way —
   plausibly the reset block's 0 wins every edge and the FIFO stays
   "empty" forever: a bug that looks stable but is merely arbitrary.
   Synthesis hard-errors on the multiply-driven register first. Forced
   into silicon, two drivers fight on one net — the contention current
   from section 1. Fix: one process per signal, reset and increment in
   one block with if/else priority.

6. **17 occupancies, 16 gap values, pigeonhole.** Occupancies 0..16
   inclusive = 17 states; the gap takes 16 values, and gap 0 must
   carry both occupancy 0 and occupancy 16 — two states in one
   encoding, so no function of 4-bit pointers can separate them. With
   5-bit pointers counting mod 32: empty = (w == r); full =
   w[4] != r[4] && w[3:0] == r[3:0].

7. **010 or 110 — old or new, nothing else.** Gray changes exactly one
   bit per increment (bit 2 here), so a mid-change sample reads that
   bit settled 0 or 1: two real pointers, no third value. A stale 010
   tells the read side there is *less* data than exists — it stalls a
   beat longer, never a read of unwritten memory: each domain uses the
   other's pointer in the conservative direction. Binary 011 -> 100
   changes 3 bits; a torn sample could read 111 (bit 2 new, bits 1:0
   old) — a pointer that never existed, 4 ahead, inviting 4 reads of
   unwritten slots.

8. **The one-extra-bit trick, generalized; the extra bit became
   2^32 / N wrap bits.** Unreduced counters make tail - head *be* the
   occupancy for every value up to 2^31 — the pointers distinguish
   2^32 states, vastly more than the N+1 needed, so full and empty are
   comparisons on raw indices, unambiguous at any wrap count
   (mod-2^32 arithmetic absorbs wraps, as mod-16 arithmetic absorbed
   the 8-deep FIFO's wrap bit). Hardware pays flip-flops linearly in
   counter width, so silicon stops at one extra bit; a 32-bit field is
   free in software, so io_uring and the vring take the whole counter.

</details>
