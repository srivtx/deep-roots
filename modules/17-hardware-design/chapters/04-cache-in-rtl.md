# Chapter 04 — The cache in RTL: a tiny CPU's memory system

> **The 30-second version.** Module 01 chapter 02 handed you the cache as
> a box with a hit ratio; this chapter builds the box. It starts at the
> 6-transistor SRAM cell — two cross-coupled inverters (chapter 02's latch,
> nose-to-tail) plus two access transistors — and shows why the bitline's
> RC, growing as the square root of its length, is the constraint that
> sizes L1 at 32-64 KB. Then the cache itself: a tag SRAM, a data SRAM, a
> comparator, and a six-state miss machine, in 104 lines of Verilog you can
> simulate on a laptop. The write path costs exactly one honest conditional
> — the write buffer's bypass check, without which fills race drains and
> lines go stale. On the far side of the bus, DRAM becomes a Python timing
> model — activate (tRCD ~14 ns), column (tCAS ~14 ns), refresh taxing the
> bus 4.5%, DDR4-3200's 25.6 GB/s — and the memory controller becomes an
> out-of-order engine whose queue depth is the difference between 2.3 and
> 17.3 GB/s on the same wires.

## One bit, six transistors

Chapter 02 built a latch from two inverters connected nose-to-tail: each
drives the other's input, so the pair has two stable states and holds a
bit indefinitely (it is static — no refresh, unlike module 01 chapter 01's
1-transistor-1-capacitor DRAM cell). The SRAM cell adds exactly two more
transistors, both NMOS access transistors, whose gates share a wordline:

```text
                    wordline WL  (one per row; HI = connect this cell)
      ────────────────────────│──────────────────────────────
                               │
                     ┌─────────┴─────────┐
                     │                   │
                  T5 access           T6 access
   bitline BL ●──────┤                   ├──────● bitline BL_bar
                     │                   │
  (precharge BOTH    ● Q            Q_bar ●     (precharge BOTH to HI,
   to HI, release)   │                   │       then release)
              ┌──────┴──────┐     ┌──────┴──────┐
              │   INV A     │     │   INV B     │
              │  in: Q_bar  │     │   in: Q     │
              │  out: Q     │     │  out: Q_bar │
              └─────────────┘     └─────────────┘
                  └── nose-to-tail: the ch02 cross-coupled latch ──┘
```

**Read.** Precharge BL and BL_bar both to HI, release them, then assert
the wordline. The cell now fights one bitline: if Q = 0, INV A's pull-down
path (through T5) drags BL toward 0 while Q_bar = 1 leaves BL_bar floating
HI. A differential sense amplifier — itself a cross-coupled pair, the same
latch a third time — watches the two lines and resolves as soon as a small
voltage split (tens of millivolts) appears. Differential sensing is why
the read is fast: you race two identical lines instead of waiting for one
to reach a threshold.

**The read time is the bitline's RC.** The bitline is one long wire that
hangs off *every* cell in its column — 128 rows means 128 diffusion
capacitances plus the wire's own — and the winning cell must drag it down
by the sense amp's margin. That is the whole story of "big SRAMs are
slow": the delay is not in the cell, it is in the column wire the cell is
asked to discharge. Double the rows and you roughly double the bitline
capacitance; but delay does not double, it grows by about the square root,
because the wire is a *distributed* RC line (resistance and capacitance
spread along it), and a distributed line's delay scales with length
squared only when lumped, with length when driver-dominated, and with the
square root in between — the regime long bitlines live in. So 2x the
length is about sqrt(2) = 1.41, i.e. **+41% access time per doubling of
rows**, the number to carry into the cache-sizing argument below.

**Write is the asymmetry.** To write, you drive BL/BL_bar *hard* (full
swing, not a gentle precharge) so the access transistors overpower the
cell's own inverters and flip it. Reading must never do that: during a
read, Q and the access transistor form a potential pull-down that fights
the cell's stored state. The fix is sizing — the inverter's pull-down
transistor must be stronger than the access transistor by the classic
"cell ratio" (a beta ratio around 1.5-2) so the stored bit survives the
read. But strengthening the pull-down weakens the write (which *wants* the
access transistor to win). The insider summary: **SRAM cells are sized
for stability, not speed** — every 6T cell is a negotiated truce between
read-stability and write-margin, fixed once at design time, and it is why
foundries publish per-node SRAM cell sizes as headline metrics.

The cost of all this: 6 transistors and ~8-12 metal/poly contacts per bit,
versus DRAM's 1 transistor + 1 capacitor. The transistor ratio is 6:1;
the *manufactured* area gap is larger — a high-density 6T cell is about
0.021 µm² on a 5 nm-class node (the number chapter 05 returns to), while
recent DDR4/DDR5 cells sit near 0.0013-0.0018 µm² (4F²-class layouts,
node-dependent) — call it an honest 10-15x. That ratio is the root of the
whole hierarchy: 6T is fast and sits next to the ALU in 32-64 KB blobs;
1T1C is dense and sits far away in gigabytes.

## The same cell with more doors: the register file

Before the cache, one detour that pays off later: the register file is
this same 6T cell with extra access transistor pairs — one pair per port.
A classic RISC file needs 3 ports: **2 read + 1 write (2R1W)** per cycle,
because one ALU instruction reads two source registers and one
destination. The two read ports are what feed the ALU's two operand muxes
— and module 02 chapter 02's forwarding muxes are exactly the logic that
sits *around* this file, steering results back into those ports.

Ports are brutally expensive: each port adds roughly one more wordline
and two more bitlines *per cell*, and the area cost compounds — rule of
thumb is +40-60% area per port, making a 2R1W file about 2.3x the area of
a 1R1W file of the same capacity (approximations, but they order
correctly). Now scale the issue width: a 6-wide machine wants ~8 read
ports, so the file balloons, and each new port slows the bitlines it
touches (more wire, more capacitance — the sqrt story again). The escape
hatch is the **bypass network**: instead of an enormous multiported blob,
results are forwarded around the file through wires and muxes. Rough
industry folklore, honestly marked as an approximation: *the register
file plus its bypass network is on the order of half a wide core's area*
— the datapath surrounds a memory, not the other way around.

## Two SRAMs and a comparator: the cache in RTL

Now the centerpiece. Take the module 01 chapter 02 address anatomy —
`tag | index | offset` — and make it wires. For a direct-mapped cache of
128 lines x 32 B (4 KB) on a 32-bit address bus:

```text
     cpu_addr[31:0] = [ tag 31:12 | index 11:5 | offset 4:0 ]
                              |          |          |
                              |          |          └─ [4:3] beat, [2] word
                              |          |
                +-------------+          +-------------+
                v (20 bits)               v (7 bits: 1 of 128 lines)
      +---------------------+   +------------------------+
      | TAG array (SRAM)    |   | DATA array (SRAM)      |
      | 128 x 20 b + valid  |   | 128 lines x 4 x 8 B    |
      +----------+----------+   +-----------+------------+
                 | rd_tag (20 b)           | rd_beat (64 b)
                 v                         |
        tag == rd_tag  AND  valid[idx]     |  offset picks
                 |                          |  8 B beat + 4 B word
                 +--------> hit ----------->+----> word mux -> cpu_rdata
                 |
                 (miss -> the FSM below)
```

The lookup's critical path is one chain, and it is the entire reason L1
exists in the shape it does: **read the tag SRAM and the data SRAM in
parallel, compare the tag, mux the word out**. In the 5-stage pipeline of
chapter 02, the load's address is ready at the end of EX; the cache must
answer during MEM — one cycle. So this chain has a fixed time budget,
measured in the fanout-of-4 delays (FO4) of chapter 01: a cycle is some
tens of FO4 (design-style dependent, ~40-80 for a full cycle including
latch overhead — treat it as a budget, not a constant), and the SRAM
read, the comparator, and the mux must fit inside the MEM slice of it.

**That budget, not capacity, sizes L1.** Grow the cache 8x (4 KB to 32 KB,
128 to 1024 rows) and the sqrt rule says the bitline adds sqrt(8) = 2.8x
— the compare path still fits a 4-5 cycle load-to-use (measured on
Skylake-class x86: L1d 32 KB, 8-way, 4-5 cycle hit; L2 ~12-14 cycles; L3
~40-50). Grow it to 1 MB direct-mapped — 32768 lines — and the bitline is
sqrt(256) = 16x the toy's, blowing the cycle's FO4 budget: every load in
the machine now pays extra cycles, hit or miss. The sweet spot where the
arrays still answer in ~4-5 cycles is 32-64 KB, which is why L1d is that
size across Intel and AMD cores (Apple's M1-class cores ship a 128 KB L1d
at 3-4 cycle load-to-use — the exception that proves the budget is a
choice, bought with a wider, less clock-hungry pipeline).

Associativity is more of the same currency. A 2-way cache needs *two*
tag compares in parallel plus a mux that picks the way after the compare
— one more mux delay on the very path that must fit the cycle. The
textbook rule that falls out: **L1 is timing-bound (2-4 way in the
textbook; 6-8 way in real parts, kept fast by way prediction — predict
which way will hit, skip the mux delay, verify after and eat ~1 cycle on
a misprediction), while L2/L3 are miss-bound (8-16 way, because their
latency is already tens of cycles and conflict misses cost more than the
mux)**:

| level | size (typical 2020s x86) | associativity | hit latency | bound by |
|-------|--------------------------|---------------|-------------|----------|
| L1d   | 32-64 KB                 | 6-8 way + way prediction | 4-5 cycles | the cycle's FO4 budget |
| L2    | 512 KB-1 MB              | 8-way class   | ~12-14 cycles | miss latency |
| L3    | 8-64 MB                  | 16-way class  | ~40-50 cycles | conflict misses |

Module 01 chapter 02 told you the associativity-per-level pattern; this
is *why*, in gate delays.

Here is the whole cache — lookup, miss FSM, write buffer — as runnable
RTL (104 lines; the ~80 the module README promised, plus comments that
earn their keep). It is the ch03 subset: wires for structure, one clocked
always block for state:

```verilog
/* cache.v - direct-mapped write-through cache: 128 lines x 32 B (4 KB).
   Address: tag[31:12] | index[11:5] | offset[4:0].  CPU: 32-bit words,
   req/ready handshake.  Memory: 64-bit bus, 4-beat (8 B) burst fills,
   1-beat writes.  Policy: write-through, write-around, 1-entry buffer. */
module cache(
    input  wire        clk, rst, cpu_req, cpu_wr,
    input  wire [31:0] cpu_addr, cpu_wdata,
    output reg  [31:0] cpu_rdata, mem_addr, mem_wdata,
    output reg         cpu_ready, mem_req, mem_we,
    input  wire        mem_beat,           // data strobe (4x read, 1x write)
    input  wire [63:0] mem_rdata);
    wire [6:0]  idx  = cpu_addr[11:5];     // 7 index bits -> 128 lines
    wire [19:0] tag  = cpu_addr[31:12];    // 20 tag bits
    wire [1:0]  beat = cpu_addr[4:3];      // which 8 B beat of the line
    wire        half = cpu_addr[2];        // which 4 B word of the beat
    reg [19:0]  tag_arr [0:127];           // the two SRAMs + valid bits
    reg [63:0]  data_arr [0:127][0:3];     // (inferred memory, ch03)
    reg [127:0] valid;
    // THE critical path: parallel SRAM reads -> compare -> mux, as wires.
    wire [19:0]  rd_tag  = tag_arr[idx];
    wire [63:0]  rd_beat = data_arr[idx][beat];
    wire         hit     = valid[idx] && (rd_tag == tag);
    wire [31:0]  hit_dat = half ? rd_beat[63:32] : rd_beat[31:0];
    reg         wb_v;                      // 1-entry write buffer:
    reg [31:0]  wb_addr, wb_wdata;         // write-through's shock absorber
    wire wb_hit = wb_v && (wb_addr[31:5] == cpu_addr[31:5]);
    // miss FSM: IDLE -> REQ -> WAIT -> FILL (4 beats) -> REFILL -> RESUME
    localparam S_IDLE=3'd0, S_REQ=3'd1, S_WAIT=3'd2, S_FILL=3'd3, S_REF=3'd4, S_RES=3'd5;
    reg [2:0]  state;
    reg [6:0]  f_idx;                      // latched index and tag
    reg [19:0] f_tag;                      // of the missing line
    reg [1:0]  beat_cnt;
    reg [63:0] fb [0:3];                   // fill buffer: 4 x 8 B
    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE; valid <= 128'd0; cpu_ready <= 1'b0;
            mem_req <= 1'b0; mem_we <= 1'b0; wb_v <= 1'b0;
        end else begin
            cpu_ready <= 1'b0;             // default: not done this cycle
            case (state)
            S_IDLE:
                // Drain when idle, when a write needs the buffer, or when a
                // pending write targets the SAME line - a fill racing the
                // drain installs pre-write data (the write-buffer bug).
                if (wb_v && (!cpu_req || cpu_wr || wb_hit)) begin
                    state <= S_REQ;        // S_REQ drives the write
                end else if (cpu_req && !cpu_ready && !wb_hit) begin
                    if (hit) begin
                        if (cpu_wr) begin  // write hit: array AND buffer
                            data_arr[idx][beat] <= half
                                ? {cpu_wdata, rd_beat[31:0]}
                                : {rd_beat[63:32], cpu_wdata};
                            wb_addr <= cpu_addr; wb_wdata <= cpu_wdata; wb_v <= 1'b1;
                        end
                        cpu_rdata <= hit_dat;  // HIT: answered in 1 cycle
                        cpu_ready <= 1'b1;
                    end else if (cpu_wr) begin
                        wb_addr <= cpu_addr;   // write miss: write-around,
                        wb_wdata <= cpu_wdata; // straight to the buffer
                        wb_v <= 1'b1; cpu_ready <= 1'b1;
                    end else begin
                        f_idx <= idx;          // read miss: start the fill
                        f_tag <= tag; state <= S_REQ;
                    end
                end
            S_REQ: begin                    // drive the memory command bus
                if (mem_we) begin
                    mem_addr <= wb_addr;    // drain: the buffered write
                    mem_wdata <= wb_wdata;
                end else
                    mem_addr <= {f_tag, f_idx, 5'b0};  // fill: line base
                mem_req <= 1'b1; state <= S_WAIT;
            end
            S_WAIT:                         // wait for the bus's first beat
                if (mem_beat) begin
                    mem_req <= 1'b0;
                    if (mem_we) begin       // write drained
                        mem_we <= 1'b0; wb_v <= 1'b0; state <= S_IDLE;
                    end else begin          // beat 0 of the burst
                        fb[0] <= mem_rdata; beat_cnt <= 2'd1; state <= S_FILL;
                    end
                end
            S_FILL:                         // beats 1..3
                if (mem_beat) begin
                    fb[beat_cnt] <= mem_rdata;
                    if (beat_cnt == 2'd3) state <= S_REF;
                    beat_cnt <= beat_cnt + 2'd1;
                end
            S_REF: begin                    // REFILL: commit the line
                tag_arr[f_idx] <= f_tag; data_arr[f_idx][0] <= fb[0];
                data_arr[f_idx][1] <= fb[1]; data_arr[f_idx][2] <= fb[2];
                data_arr[f_idx][3] <= fb[3]; valid[f_idx] <= 1'b1;
                state <= S_RES;
            end
            S_RES: begin                    // RESUME: replay the request
                cpu_rdata <= hit_dat;       // it hits now - valid was set
                cpu_ready <= 1'b1;          // unfreeze the CPU
                state <= S_IDLE;
            end
            default: state <= S_IDLE;
            endcase
        end
    end
endmodule
```

## The miss is a structural hazard wearing a state machine

Read the miss path as a pipeline story, not a cache story. When the
lookup misses, the MEM stage cannot produce data, so the CPU must *freeze
the PC* and hold the request — and that machinery is nothing new: it is
chapter 02's hazard logic, reused verbatim. A cache miss is precisely a
**structural hazard on the memory port**: the port is busy serving the
fill, so the stage that needs it stalls. The testbench's handshake
(CPU holds `cpu_req`/`cpu_addr` until `cpu_ready`) is the same contract.

The stall's duration is owned by a six-state machine:

```text
   cpu_req ---- hit? ---- yes: cpu_rdata + cpu_ready (1 cycle)
                 |
                 no
                 v
        FREEZE the CPU (the ch02 stall machinery:
        a structural hazard on the memory port)
                 |
                 v
   +---------------------------------------------+
   | latch index+tag  (IDLE)                     |
   | REQ:   drive mem_req + the line base addr   |
   | WAIT:  hold until the bus's first beat      |
   | FILL:  beats 0..3 of 8 B -> fb[0..3] (32 B) |
   | REFILL: commit tag + 32 B, set valid        |
   | RESUME: replay the request - it now hits,   |
   |         pulse cpu_ready, unfreeze           |
   +---------------------------------------------+
```

Why a *burst* of four 8 B beats? The bus width/cycle trade: a 64-bit bus
fills a 32 B line in 4 beats; a 32-byte-wide bus would do it in 1 beat but
costs 4x the pins and wires — and pins, not transistors, are what a chip
can afford least. DRAM made the same choice at its scale (the 8n prefetch
below). On this toy bus (100 MHz, 4-cycle latency, back-to-back beats), a
hit is answered in 1 cycle and a miss costs 13 end-to-end: 1 accept, 1 to
drive the bus, 4 of latency, 4 beats, 1 commit, 1 resume, 1 observe. That
13 is the number that makes the hit ratio matter: with a 60% hit rate on
the chapter's test trace, the average access is 0.6 x 1 + 0.4 x 13 =
5.8 cycles — the miss path dominates everything.

Note the elegance in `S_RES`: the machine does not have a special
"reply with fill data" path. It sets `valid` in `S_REF`, returns to the
same lookup, and the *same request now hits*. RESUME is literally a
replay.

## Writing: through, back, and the buffer in between

**Write-through, naively, is a disaster.** Every store, hit or miss, must
also go to memory — and if the CPU waits for the bus on every store, a
write-heavy loop stalls on the port the way a miss does. The fix is the
**write buffer**: accept the write into the cache (or post it, on a
write-around miss), drop it in a small FIFO, and let it drain to memory
whenever the bus is idle. The CPU stops waiting for writes; the bus
schedules them.

But the buffer plants a bug with a name: **write-buffer coherency**. A
pending write to line X and an incoming *fill* for line X are two buses
to the same data. If the fill issues first and the drain lands after,
memory serves the fill the pre-write bytes, the cache installs a **stale
line**, the buffered write later lands in memory — and now cache and
memory disagree, forever, with hits serving the old value. The fix is one
compare: on every request, check the buffer for a same-line entry
(`wb_hit`) and stall until it drains. In the listing it is the
`!wb_hit` term plus the drain condition — one line of RTL suppressing an
entire bug class that first silicon of nearly every write-through cache
has rediscovered.

**Write-back flips the default.** Instead of pushing every store to
memory, keep the modified line in the cache and mark it with a **dirty
bit**; write it back only on eviction. The payoff: repeated writes to one
line cost zero bus traffic. The price is the miss path: evicting a dirty
victim means write-back *then* fill. With this chapter's DRAM constants,
a clean miss is ~52 ns core-side (10 controller + 14 precharge + 14
activate + 14 column); the write-back adds its own ~44.5 ns (precharge,
activate, column, 2.5 ns of bus) ahead of the fill — total ~96.5 ns, a
1.85x, which every practitioner rounds to "a dirty miss costs about
double." In the RTL lab below, exercise (a) makes you watch it happen in
cycles (13 -> ~21 on the toy bus).

The dirty bit is also the seed of coherence. In a single core it just
means "memory is stale, I am the owner." The moment a *second* cache
shares that memory, "I am the owner" becomes a protocol state: MESI's
Modified state is exactly this bit promoted to a machine with rules about
who may read what (module 01 chapter 20 builds that machine; note that
its write-back requirement is why the dirty bit had to exist first).
Write-through, for contrast, buys trivially snooping-friendly coherence
at the price of bus traffic — the WT-vs-WB choice is really "bandwidth
now vs protocol complexity later."

## The far side of the bus: DRAM as a timing model

Module 01 chapter 01 built the DRAM cell (1T1C, leaking, refreshed). Its
organization *above* the cell is a hierarchy of banks, each containing
rows, each row a few thousand columns. The timing model that matters:

- **A row must be activated before it can be read**: `ACT` opens the row
  into the bank's sense amps (tRCD ~14 ns at DDR4-3200 CL22 — 22 clocks
  of 0.625 ns), and only then can a `READ` column command fire (tCAS,
  ~14 ns more).
- The open row *stays* in the sense amps — the "page". A column access
  to the open row (a **row hit** / page hit) skips both delays: just the
  column command. The chapter's model gives a page hit ~24 ns core-side
  (10 ns controller overhead + tCAS) vs ~52 ns for a page miss — the
  numbers the field rounds to "~30 vs ~50 ns," before the ~20-30 ns of
  L3/interconnect/queueing that pushes a measured end-to-end cache-miss
  load on a DDR4 desktop to roughly 70-90 ns.
- **The memory controller is an out-of-order engine for DRAM.** It holds
  a reorder window of pending requests and reorders them to exploit open
  rows and idle banks — the single most underrated block on a modern CPU.
- **Refresh taxes everything.** Every cell leaks; the chip must rewrite
  all rows within the 64 ms retention window, via a REFRESH command every
  tREFI = 7.8 µs (8192 per 64 ms), each stealing tRFC = 350 ns on an
  8 Gb die: 350/7800 = **4.5% of the bus, even when idle**. (tRFC grows
  with die capacity — 480 ns at 16 Gb.)
- **Bursts and the 8n prefetch.** The DRAM array core is slow (hundreds
  of MHz — dense capacitive digit lines), while the interface runs at
  gigatransfers. DDR's reconciliation: per column command the core moves
  8 words internally at its own pace and the interface serializes them
  at 8x the rate. The naming decodes cleanly: **DDR4-3200 = 3.2 GT/s per
  pin x 8 B (a 64-bit channel) = 25.6 GB/s**, which is exactly what the
  module name PC4-25600 states in bytes.

The chapter's third artifact is that model as code — one rank, 8 banks,
closed-loop queue depth, greedy open-row scheduling, refresh included
(real output, run with Python 3.12; constants above):

```python
#!/usr/bin/env python3
# dram_timing.py - a one-rank, 8-bank DDR4-style timing model.
# Constants for DDR4-3200 CL22 (0.625 ns command clock), 8 Gb die:
#   tRP = tRCD = tCAS = 14 ns (22 clocks), tRAS = 32.5 ns (52 clocks),
#   tCCD = 2.5 ns (= 64 B per burst on a 64-bit channel: the bus floor),
#   controller overhead 10 ns/request, refresh tREFI = 7.8 us, tRFC = 350 ns.
import random, heapq

T_RP, T_RCD, T_CAS, T_RAS, T_CCD = 14.0, 14.0, 14.0, 32.5, 2.5
T_CTRL, T_REFI, T_RFC = 10.0, 7800.0, 350.0
BANKS = 8
next_ref = 0.0

def blocked(t):
    # refresh steals the bank: tRFC of dead time every tREFI
    global next_ref
    while next_ref <= t:
        if t < next_ref + T_RFC:
            t = next_ref + T_RFC
        next_ref += T_REFI
    return t

def simulate(addrs, Q):
    """Closed loop: Q requests in flight (the memory controller's reorder
    window).  The pick policy is the OoO trick: serve the first queued
    request that hits a bank's open row, else the queue head."""
    open_row = [None] * BANKS   # the row held in each bank's sense amps
    cmd_free = [0.0] * BANKS    # bank can accept its next command at
    pre_free = [0.0] * BANKS    # a PRECHARGE specifically at (after tRAS)
    bus_free = done = 0.0
    inflight = []               # completion times of the Q in-flight reqs
    queue, nxt, hits, n = list(addrs[:Q]), Q, 0, 0
    while n < len(addrs):
        pick = 0
        for j, a in enumerate(queue):         # prefer an open-row hit
            if a >> 16 == open_row[(a >> 13) & 7]:
                pick = j
                break
        a = queue.pop(pick)
        if nxt < len(addrs):
            queue.append(addrs[nxt]); nxt += 1
        gate = heapq.heappop(inflight) if len(inflight) == Q else 0.0
        b, row = (a >> 13) & 7, a >> 16      # address map: col | bank | row
        if open_row[b] != row:               # page miss: PRE, ACT, CAS
            t = blocked(max(gate, cmd_free[b], pre_free[b]))
            act = t + T_RP
            cas = act + T_RCD
            pre_free[b] = act + T_RAS        # the row must stay open tRAS
        else:                                # page hit: CAS alone
            hits += 1
            cas = blocked(max(gate, cmd_free[b]))
        cmd_free[b] = cas + T_CCD
        open_row[b] = row
        s = max(bus_free, cas + T_CAS)       # data rides the bus 2.5 ns
        done = max(done, s + T_CCD + T_CTRL)
        bus_free = s + T_CCD
        heapq.heappush(inflight, done)
        n += 1
    return done, hits, n

print("DDR4-3200: 3.2 GT/s/pin x 8 B = 25.6 GB/s per 64-bit channel (PC4-25600)")
print("page hit  (row open):   %4.1f ns  (tCTRL %.0f + tCAS %.0f)"
      % (T_CTRL + T_CAS, T_CTRL, T_CAS))
print("page miss (row closed): %4.1f ns  (+ tRP %.0f + tRCD %.0f)"
      % (T_CTRL + T_RP + T_RCD + T_CAS, T_RP, T_RCD))
print("refresh tax: tRFC/tREFI = %.0f/%.0f = %.1f%% of the bus, always"
      % (T_RFC, T_REFI, 100.0 * T_RFC / T_REFI))
print()
rng = random.Random(7)
N = 8192
hot = [(rng.randrange(512 * 1024 // 64)) * 64 for _ in range(N)]  # 512 KB set
print("random 512 KB working set (n=%d, 64 B reads):" % N)
print("  queue   ns/req    GB/s   row-hit")
for Q in (1, 2, 4, 8, 16, 32):
    next_ref = 0.0
    span, hits, n = simulate(hot, Q)
    print("  %5d  %7.1f  %6.1f   %4.1f%%"
          % (Q, span / n, 64.0 * n / span, 100.0 * hits / n))
stream = [i * 64 for i in range(N)]
for Q in (1, 8):
    next_ref = 0.0
    span, hits, n = simulate(stream, Q)
    print("stream Q=%d: %5.1f ns/req -> %4.1f GB/s (row-hit %.1f%%)"
          % (Q, span / n, 64.0 * N / span, 100.0 * hits / n))
```

```console
$ python3 dram_timing.py
DDR4-3200: 3.2 GT/s/pin x 8 B = 25.6 GB/s per 64-bit channel (PC4-25600)
page hit  (row open):   24.0 ns  (tCTRL 10 + tCAS 14)
page miss (row closed): 52.0 ns  (+ tRP 14 + tRCD 14)
refresh tax: tRFC/tREFI = 350/7800 = 4.5% of the bus, always

random 512 KB working set (n=8192, 64 B reads):
  queue   ns/req    GB/s   row-hit
      1     53.4     1.2   12.0%
      2     28.2     2.3   13.5%
      4     16.0     4.0   16.7%
      8     10.2     6.2   22.4%
     16      6.9     9.3   32.5%
     32      4.3    14.8   47.7%
stream Q=1:  27.9 ns/req ->  2.3 GB/s (row-hit 99.2%)
stream Q=8:   3.7 ns/req -> 17.3 GB/s (row-hit 99.2%)
```

Read the shape, not just the numbers. A single outstanding request pays
the full 52-53 ns (page misses, no overlap): 1.2 GB/s from a channel
whose name promises 25.6. Depth buys parallelism: with 8 in flight the
banks overlap, 32 in flight lets the scheduler find open rows (row-hit
47.7%), and throughput climbs 12x on identical wires. The stream rows are
the other half of the lesson: sequential access is a row-hit machine
(99.2% — one miss per row per bank), so a stream at depth 8 reaches
17.3 GB/s while the *same* stream at depth 1 gets 2.3 GB/s — latency
hidden by queueing, nothing else. This is the latency-bandwidth curve the
whole program keeps meeting: it is the x-axis of module 12 chapter 02's
roofline. The Q=1 random point is also why one core chasing pointers
around a 512 KB region cannot feed itself, and why the controller's
reorder window folklore says "hidden performance lives in the MC" —
bank-conflict-aware scheduling is a second reorder engine most
programmers never think about.

## What insiders know

- **"L1's size is a timing decision, not a capacity decision"** — the
  most quotable claim in cache design. 32-64 KB is where the bitline's
  sqrt-scaled delay still fits the MEM stage's FO4 slice; a 1 MB L1 in
  the same style would be sqrt(256) = 16x slower per access and every
  load would pay it. (The corollary: "why not 1 MB of L1" is answered in
  gate delay arithmetic, not dollars.)
- **SRAM cells are sized for stability, not speed** — the 6T cell's
  pull-down vs access transistor ratio (beta ~1.5-2) exists to keep reads
  from flipping the stored bit; the write path then fights that sizing.
  Foundries advertise per-node cell sizes (0.021 µm² at 5 nm-class HD)
  because that number gates how much L2 fits.
- **The write-buffer coherency check is the bug class that ships.** The
  fix is one address compare in the fill path; the bug is a stale line
  that survives every test that doesn't do write-miss-then-read-miss to
  the same line, in that order.
- **The memory controller is an OoO engine with a reorder window** of
  16-64+ requests; its scheduler prefers open rows and idle banks. The
  folklore is literal: single-thread performance lost to bank conflicts
  is "hidden in the MC."
- **Refresh is a permanent 3-5% tax** (4.5% in the model: 350 ns every
  7.8 µs on an 8 Gb die, worse at 16 Gb), and it does not care that your
  workload is idle — the DRAM is refreshing rows regardless.
- **The memory wall is visible to the naked eye.** Die-shot analyses put
  the L3 at about half of big x86 dies; for Apple's M1 (2020-2021
  community die analyses), the memory system — L1s, L2, fabric, and
  controllers — is roughly 60% of the chip, while the ALUs are a sliver.
  When someone asks why chips look like this, the answer is on the photo.
- **Register files + bypass networks are the stealth cost of width**: 8
  read ports on a 6-wide core, +40-60% area per port, "half the core" as
  the honestly-approximate folklore for file+bypass together.

## See it yourself

The lab is three files: `cache.v` above, the testbench below, and
`dram_timing.py` above. Verilog simulation needs `iverilog` (chapter 03's
lab: `apt install iverilog` on Debian/Ubuntu; this writing sandbox
couldn't install it, so the Verilog output below is hand-traced — the
trace is the point, and any laptop regenerates it):

```verilog
/* tb.v - self-checking testbench: golden-vector compare (ch03's pattern) */
`timescale 1ns/1ps
module tb;
    reg clk = 0, rst = 1, cpu_req = 0, cpu_wr = 0;
    reg  [31:0] cpu_addr = 0, cpu_wdata = 0;
    wire [31:0] cpu_rdata, mem_addr, mem_wdata;
    wire cpu_ready, mem_req, mem_we;
    reg  mem_beat = 0;  reg [63:0] mem_rdata = 0;
    integer hits = 0, misses = 0, errors = 0, i;
    reg h;

    reg [31:0] golden [0:16383];            // the 64 KB "DRAM"

    cache dut(.clk(clk), .rst(rst),
              .cpu_req(cpu_req), .cpu_wr(cpu_wr),
              .cpu_addr(cpu_addr), .cpu_wdata(cpu_wdata),
              .cpu_rdata(cpu_rdata), .cpu_ready(cpu_ready),
              .mem_req(mem_req), .mem_we(mem_we),
              .mem_addr(mem_addr), .mem_wdata(mem_wdata),
              .mem_beat(mem_beat), .mem_rdata(mem_rdata));

    always #5 clk = ~clk;                   // 100 MHz toy bus

    // behavioral DRAM: 3 idle cycles, then 4 read beats / 1 write beat
    reg busy = 0;  reg [1:0] lat = 0, bcnt = 0;  integer base;
    always @(posedge clk) begin
        mem_beat <= 1'b0;
        if (mem_req && !busy) begin
            busy <= 1'b1; lat <= 3; bcnt <= 0; base = mem_addr[31:2];
        end else if (busy && lat != 0) lat <= lat - 1;
        else if (busy) begin
            mem_beat <= 1'b1;
            if (mem_we) begin               // drain: one beat writes DRAM
                golden[base] <= mem_wdata;
                busy <= 1'b0;
            end else begin                  // fill: 4 beats of 8 B
                mem_rdata <= {golden[base+2*bcnt+1], golden[base+2*bcnt]};
                if (bcnt == 3) busy <= 1'b0; else bcnt <= bcnt + 1;
            end
        end
    end

    task cpu_read(input [31:0] a);
    begin
        @(negedge clk); cpu_req = 1; cpu_wr = 0; cpu_addr = a;
        #1 h = dut.hit;                     // the lookup, sampled combinationally
        @(posedge clk);
        while (!cpu_ready) @(posedge clk);
        if (h) hits = hits + 1; else misses = misses + 1;
        $display("read  %h  %s  data=%h", a, h ? "HIT " : "MISS", cpu_rdata);
        if (cpu_rdata !== golden[a[31:2]]) begin
            errors = errors + 1;
            $display("      ERROR: expected %h", golden[a[31:2]]);
        end
        cpu_req = 0;
    end
    endtask

    task cpu_write(input [31:0] a, input [31:0] d);
    begin
        @(negedge clk); cpu_req = 1; cpu_wr = 1; cpu_addr = a; cpu_wdata = d;
        @(posedge clk);
        while (!cpu_ready) @(posedge clk);
        $display("write %h  data=%h", a, d);
        cpu_req = 0; cpu_wr = 0;
    end
    endtask

    initial begin
        for (i = 0; i < 16384; i = i + 1) golden[i] = i * 3 + 7;
        $dumpfile("cache.vcd"); $dumpvars(0, tb);
        repeat (3) @(posedge clk);
        @(negedge clk); rst = 0;
        $display("--- cold reads: 16 words, 2 lines ---");
        for (i = 0; i < 16; i = i + 1) cpu_read(32'h00000100 + 4*i);
        $display("--- re-read: all should hit ---");
        for (i = 0; i < 16; i = i + 1) cpu_read(32'h00000100 + 4*i);
        $display("--- conflict: same index, different tags ---");
        for (i = 0; i < 8; i = i + 1) begin
            cpu_read(32'h00000100);         // index 8, tag 0
            cpu_read(32'h00002100);         // index 8, tag 2
        end
        $display("--- write path, end to end ---");
        cpu_write(32'h00000100, 32'hDEADBEEF);  // miss: write-around
        cpu_read(32'h00000100);             // forces drain, then fill
        cpu_write(32'h00000100, 32'hCAFED00D);  // hit: array + buffer
        cpu_read(32'h00002100);             // evict 0x100's line
        cpu_read(32'h00000100);             // refill from DRAM: CAFED00D
        repeat (10) @(posedge clk);
        $display("reads=%0d hits=%0d misses=%0d errors=%0d hit_ratio=%0.1f%%",
                 hits+misses, hits, misses, errors, 100.0*hits/(hits+misses));
        if (errors == 0) $display("PASS: every read matched the golden DRAM");
        else             $display("FAIL: %0d mismatches", errors);
        $finish;
    end
endmodule
```

```console
$ iverilog -g2001 -o cache_sim cache.v tb.v && vvp cache_sim
--- cold reads: 16 words, 2 lines ---
read  00000100  MISS  data=000000c7
read  00000104  HIT   data=000000ca
read  00000108  HIT   data=000000cd
...                                     <- 12 more: HITs through 0x11c
read  00000120  MISS  data=000000df    <- second line: another fill
read  00000124  HIT   data=000000e2
--- re-read: all should hit ---
read  00000100  HIT   data=000000c7
...                                     <- 16 HITs, data unchanged
--- conflict: same index, different tags ---
read  00000100  HIT   data=000000c7     <- still resident from above
read  00002100  MISS  data=000018c7     <- evicts 0x100's line
read  00000100  MISS  data=000000c7     <- evicts it back: thrash
read  00002100  MISS  data=000018c7
...                                     <- 12 more alternating MISSes
--- write path, end to end ---
write 00000100  data=deadbeef
read  00000100  MISS  data=deadbeef     <- drain-first, then fill
write 00000100  data=cafed00d
read  00002100  MISS  data=000018c7     <- evicts 0x100 (write drains)
read  00000100  MISS  data=cafed00d     <- refill from DRAM: proof of WT
reads=51 hits=31 misses=20 errors=0 hit_ratio=60.8%
PASS: every read matched the golden DRAM
```

The golden-vector check is the discipline from chapter 03: every read
compares against `golden[]`, which the behavioral DRAM updates on write
drains — so the last `cafed00d` proves the write survived the round trip
buffer -> bus -> DRAM -> fill. The conflict block is the direct-mapped
pathology in miniature: two addresses, index 8, tags 0 and 2, one line of
storage — 15 of 16 alternating reads miss.

Two modifications, both measured, not eyeballed:

(a) **Add the dirty bit and write-back.** Give each line a `dirty` reg,
set it in the write path, skip the write buffer; on eviction (in `S_REF`,
before the fill), if the victim is dirty, run a write-back burst first.
Watch the miss penalty on the toy bus go from 13 to ~21 cycles when the
victim is dirty — and watch the write-heavy trace's *bus traffic* drop,
because write hits now touch memory only at eviction.

(b) **Make it 2-way.** Duplicate the tag array and the compare, add the
way-select mux and a victim pick (random or LRU-approximate). Re-run the
conflict block: the alternating 0x100/0x2100 reads now hit (two lines per
index), and the miss count on that block drops from 15 to 1-2. Then feed
both designs the same 64-read trace of your own and report the miss-rate
delta — that number is the entire associativity argument, measured on
your own laptop.

And run the DRAM model: `python3 dram_timing.py` (output above, verbatim).
Change the working-set size in `hot` from 512 KB to 16 MB and watch the
row-hit column collapse (fewer requests share open rows) — the curve
moves with locality, which is the point of plotting it.

## How to use this knowledge

- **The 2x-slowdown diagnosis.** Two hot arrays whose bases differ by
  exactly 4 KB (or any multiple of the L1 way-size stride) can thrash one
  set in a direct-mapped or low-associativity cache. You can now *prove*
  it on paper: compute the index bits of both addresses (chapter 01's
  anatomy plus this chapter's `addr[11:5]` arithmetic), see they collide,
  and fix it with a 32 B pad or an array-of-structs regrouping — the same
  fix the RTL lab's exercise (b) measures.
- **Datasheet reading.** "DDR4-3200 22-22-22-52, PC4-25600" is now fully
  decoded: 22 clocks = 13.75 ns per RAS-to-CAS delay, 3.2 GT/s x 8 B =
  25.6 GB/s, refresh steals 4.5%, and random access at low queue depth
  will see ~1-2 GB/s of it while streams with deep queues see 17+ GB/s.
  You can predict benchmark results from the sticker.
- **The interview answer that separates people.** "Why is L1 32 KB?" The
  wrong answer talks about capacity and hit rates. The right one derives
  it: the bitline's sqrt-RC vs the MEM stage's fixed FO4 budget — a
  timing decision that capacity arguments cannot touch.
- **Bug review in async software.** The write-buffer coherency hazard has
  a direct software echo: any system with a pending-write queue (async
  runtimes, database write-behind caches, io_uring submission queues)
  needs the same bypass check — check the queue before serving a read.
  You now recognize the shape on sight, and can name the failure: stale
  read after reordering.
- **Why "just add more memory bandwidth" fails.** The model shows single
  streams at queue depth 1 getting 2.3 GB/s from a 25.6 GB/s channel.
  More bandwidth without more *concurrency* (more in-flight misses, more
  parallelism) buys nothing — which is why GPUs (module 12) and servers
  pair wide memory systems with enormous queues of outstanding requests.

## Connections

- **Module 17 chapter 01** — the FO4 delay unit and wire RC used to budget
  the critical path; the access transistor is chapter 01's NMOS switch,
  used twice more per cell.
- **Module 17 chapter 02** — the cross-coupled inverter pair is that
  chapter's latch; the miss stall is that chapter's hazard machinery
  (the miss is a structural hazard on the memory port); the 5-stage
  model's MEM stage is the cycle the lookup must fit.
- **Module 17 chapter 03** — the Verilog subset, the nonblocking-assign
  discipline (quiz question 6 is a blocking-vs-nonblocking bug), the
  golden-vector testbench pattern, and memory inference from `reg`
  arrays.
- **Module 17 chapter 05** — the 0.021 µm² cell size becomes the
  economics argument: SRAM stops scaling, so caches stop shrinking, so
  die area flows to the memory system (the 60% M1 figure).
- **Module 01 chapter 01** — the 1T1C DRAM cell at the other end of the
  6:1 (really 10-15x manufactured) area gap.
- **Module 01 chapter 02** — the address anatomy rebuilt as wires
  (`addr[11:5]`, `addr[4:0]`), and the hierarchy-level table whose
  per-level associativity now has gate-delay reasons attached.
- **Module 01 chapter 20** — MESI: the dirty bit is the M state's
  single-core ancestor; write-back is a prerequisite for the protocol.
- **Module 02 chapter 02** — the forwarding muxes that wrap this
  chapter's 2R1W register file.
- **Module 12 chapter 02** — the roofline's x-axis is this chapter's
  latency-bandwidth curve, plotted at GPU scale (HBM instead of DDR4).

## Quiz

1. A designer doubles the rows of an SRAM array (same line size, 2x
   capacity) and reports access time grew only ~41%, not 2x. Reconstruct
   the argument: why does bitline delay scale closer to the square root
   of length than linearly, and which property of the wire makes the
   sqrt regime valid?

2. A DIMM sticker reads PC4-25600. (a) Derive the channel's peak bytes
   per second from the name alone. (b) Given tRFC = 350 ns and
   tREFI = 7.8 µs, what fraction of that peak can traffic actually use,
   and why does making the system idle not recover it?

3. In the chapter's cache, line 0x00000100 is resident and a testbench
   issues: read 0x00000100, read 0x00002100, read 0x00000100. For each
   address give its index and tag, and predict the hit/miss pattern.

4. Using the chapter's DRAM constants (tRP = tRCD = tCAS = 14 ns,
   controller overhead 10 ns, 2.5 ns of bus per 64 B): a clean miss in a
   write-back cache costs ~52 ns core-side. What does a miss that evicts
   a dirty victim cost, and what is the ratio to a clean miss?

5. A colleague removes the `wb_hit` term from the cache's drain condition
   and its `!wb_hit` guard, arguing the write buffer drains quickly
   anyway. Trace the sequence: write miss to 0x100 (write-around, write
   lands in the buffer), then a read of 0x100. What does the read return,
   and how do the cache and DRAM disagree afterward?

6. Another colleague deletes the `!cpu_ready` term from the accept
   condition in `S_IDLE`, arguing it is redundant because `cpu_ready` is
   cleared by default every cycle. Back-to-back requests now fail
   intermittently with stale data. Explain the failure using
   nonblocking-assignment semantics.

7. Why does L1 stop at modest associativity (6-8 way in real parts, with
   way prediction) while L3 happily runs 16-way? Name the different
   constraint that binds each level, and what way prediction actually
   buys.

8. Chapter-level single core: what does the dirty bit alone buy, and what
   specifically breaks the moment a second cache shares the same memory
   (which MESI state, from module 01 chapter 20, is the promoted dirty
   bit)? Why does write-through make the multi-cache problem easier at a
   bandwidth price?

<details>
<summary>Answers</summary>

1. **The sqrt argument.** The bitline is a distributed RC line: its
   resistance and capacitance are spread along its length, not lumped at
   one node. For a lumped load, delay ~ R*C and doubling rows (doubling
   C) doubles delay. For a distributed line driven at one end, the
   Elmore-style delay of a step propagating along it grows with the
   square of length when the wire's own RC dominates, and with length
   when the driver dominates; a long bitline with 128-1024 cell loads
   sits between, closer to the sqrt regime. Hence 2x rows -> 2x C ->
   ~sqrt(2) = 1.41x, i.e. +41%. The assumption: the wire's distributed
   RC (not the sense amp or the cell) sets the read time, which is
   exactly what the sense-amplifier design ensures.

2. **(a)** PC4-25600 names 25,600 (million) bytes/second: 25.6 GB/s =
   3.2 GT/s per pin x 8 B per transfer on a 64-bit channel, i.e. the
   sticker is DDR4-3200 in byte units. **(b)** 350 ns every 7800 ns =
   4.49%, so sustained usable peak is ~24.4 GB/s. Idleness does not
   recover it because refresh is autonomous: the DRAM must rewrite every
   row within the 64 ms retention window regardless of traffic, so the
   bus and banks are simply unavailable during tRFC — an idle DRAM still
   burns ~4.5% of its bandwidth on itself.

3. **0x00000100**: index = addr[11:5] = (0x100 >> 5) & 0x7F = 8, tag =
   0x100 >> 12 = 0. **0x00002100**: (0x2100 >> 5) & 0x7F = 264 & 0x7F =
   8, tag = 0x2100 >> 12 = 2. Same index, different tags — direct-mapped
   means one line per index. Pattern: read 0x100 HIT (resident), read
   0x2100 MISS (fills, evicts tag 0), read 0x100 MISS (fills, evicts tag
   2). The pair thrashes: 2 of 3 accesses miss, and alternating forever
   is 100% miss after the first HIT — the measured 15-of-16 in the lab.

4. **~96.5 ns, a 1.85x ratio ("about double").** Clean miss: 10 (controller)
   + 14 (tRP precharge the victim's row... the bank state) + 14 (tRCD
   activate) + 14 (tCAS) = 52 ns. Dirty eviction inserts a write-back
   ahead of the fill: precharge + activate + tCAS + 2.5 bus = 44.5 ns,
   then the normal 52 ns fill, total 96.5 ns. The write-back and the
   fill both pay the full bank latency because the victim write and the
   incoming fill are to different rows. On the toy RTL bus the same
   effect shows as 13 -> ~21 cycles in lab exercise (a).

5. **The read returns the pre-write value, and memory ends up newer than
   the cache.** Without the check, the read of 0x100 misses (write-around
   never allocated it), the FSM issues the fill immediately, and the
   behavioral DRAM serves the *old* bytes — the buffered write has not
   drained. The line installs stale data, the read completes with the
   stale value, and only afterward does the background drain write the
   new value into DRAM. Now DRAM holds the new bytes, the cache holds
   the old bytes, and every subsequent read *hits* the stale line and
   never consults memory again: a silent, persistent divergence. The one
   line `wb_hit` (compare the buffer's line address against the request,
   stall or drain first) closes the entire class.

6. **The ready pulse double-fires across the NBA boundary.** When a hit
   is accepted at edge E, `cpu_ready <= 1` is a nonblocking assignment:
   it becomes *visible* only after edge E. At edge E+1 the always block
   re-evaluates with `cpu_ready`'s old value still 1 (it is cleared by
   the default assignment at E+1, applied after E+1). Without the
   `!cpu_ready` guard, the still-held request (the testbench only drops
   `cpu_req` after observing ready) is accepted a *second* time at E+1,
   producing a second ready pulse. The next task then observes a stale
   `cpu_ready` = 1 the moment it raises its request, exits its wait loop
   without waiting, and reads the previous request's `cpu_rdata` —
   stale data, intermittently, only on back-to-back requests. The guard
   costs one term and makes re-acceptance impossible for exactly the one
   cycle the old pulse is still visible; it is the handshake twin of
   ch03's blocking-vs-nonblocking rule: *old* values are what a posedge
   sees.

7. **L1 is timing-bound; L3 is miss-bound.** L1's lookup path must fit
   the MEM stage's slice of the cycle, and each extra way adds a tag
   compare in parallel (cheap) plus a way-select mux *after* the compare
   (one more delay on the critical path). At L3's ~40-50 cycle latency,
   one more mux delay is noise, but a conflict miss costs a DRAM trip —
   so L3 buys associativity to reduce conflicts (16-way-class). Way
   prediction lets real L1s run 6-8 ways without paying the mux every
   cycle: predict the way (a few bits of history/hash), read only that
   way's data early, verify the tag after; a correct prediction hides
   the mux, a misprediction costs ~1 extra cycle — trading a rare
   latency penalty for a cheaper every-cycle path (and, in real parts,
   big power savings: one way's bitlines switch instead of eight).

8. **Single core, dirty bit buys**: skipped write-backs on clean
   evictions (bandwidth) and write coalescing — repeated writes to a line
   cost zero bus traffic until eviction. **The break**: with a second
   cache on the same memory, "memory is stale, I hold the new value" is
   no longer a private fact — the other cache can hold the same line
   clean and read it from memory, getting the stale bytes. The dirty bit
   must be promoted to a protocol: MESI's **Modified** state (module 01
   chapter 20), where the owner must invalidate or write back before
   anyone else reads. Write-through sidesteps the protocol's writeback
   machinery — memory is always current, so a snooping cache can read
   freely — at the price of every store consuming bus bandwidth (the
   write-buffer chapter you just read is the tax collection system).

</details>
