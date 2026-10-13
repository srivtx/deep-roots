# Chapter 01 — From transistors to gates

> **The 30-second version.** A MOSFET is a voltage-controlled switch: the
> gate is a capacitor over the channel, and voltage above a threshold
> creates a conductive path from source to drain. NMOS conducts when its
> gate is high, PMOS when low — the complementarity of the dopings. One of
> each in series between VDD and GND, gates tied: the CMOS inverter, in
> which either steady state leaves one switch open — no DC path, static
> power is leakage only, a guarantee that died at 90 nm. Stack the switches
> as networks — 2 PMOS in parallel over 2 NMOS in series — and you get the
> NAND, 4 transistors, the cheapest 2-input gate, because CMOS is naturally
> inverting. Delay is RC: the 50% crossing sits at 0.69·RC, FO4 (~10-20 ps
> at 5-7 nm-class nodes) is the industry's unit of work, and a 3 GHz cycle
> is 15-20 FO4. Power is one equation, P = α·C·V²·f + V·I_leak: the V² term
> built DVFS, leakage ended the free gigahertz, both forced the multicore
> pivot of 2004. The clock is chapter 02's "data structure for time",
> bought with skew and a metastability MTBF cliff.

## A voltage opens a channel: the MOSFET, and exactly where we stop

Module 01 chapter 01 gave you doped silicon and DRAM's capacitor-stored
bits. One floor down: what is a transistor, at the minimum depth that
still explains logic? The answer this module keeps: **a voltage-controlled
switch.** The gate terminal is a metal plate on a thin oxide — a capacitor,
the channel beneath as its other plate. Voltage on the gate pushes a field
through the oxide; past a threshold **Vt** (roughly 0.2-0.4 V on modern
logic processes, node-dependent) a conductive channel bridges source and
drain. Below Vt the bridge is gone and only a nA-scale leak seeps. Above
Vt the switch closes; below, it opens.

```text
            gate terminal
                 |
           =============     gate plate  <- the capacitor's "top plate"
           -------------     thin oxide  <- the dielectric (nm-scale)
        +---------------------+
        |                     |
      source                drain
       (n+)                  (n+)        <- doped wells in a p-type body
        \____   channel   ____/
              (exists only when
               V_gate > Vt)
```

The two dopings give the two species: n-type wells in a p-type body (NMOS)
form a channel when the gate is **high** — on at 1; p-type wells in an
n-type body (PMOS) form one when the gate is **low** — on at 0. One is the
photographic negative of the other, and that complementarity is the most
load-bearing fact in this chapter.

Honesty checkpoint: below this — depletion regions, velocity saturation,
why FinFETs exist — this module deliberately does not go. Chapters 02-05
derive correctly at the switch abstraction; deeper physics explains why Vt
is what it is and changes none of the arithmetic. Process-dependent numbers
get marked (5 nm-era-style), not derived.

## The complementary pair: CMOS buys zero standing current

One PMOS from VDD, one NMOS to GND, gates tied, output at the middle: the
inverter, the smallest complete thought in logic.

```text
   in = 0                               in = 1
  ----------                            ----------
     VDD                                   VDD
      |                                     |
   [ PMOS ] closed                       [ PMOS ] open
      |                                     |
  out o------> out = 1                out o------> out = 0
      |                                     |
   [ NMOS ] open                        [ NMOS ] closed
      |                                     |
     GND                                   GND
```

Input high: NMOS conducts, PMOS off, output pulled to 0. Input low: PMOS
conducts, NMOS off, output pulled to 1. The key property: **in either
steady state exactly one transistor is on — no DC path from VDD to GND.**
Current flows only while the output capacitance swings; static power is
the off-transistor's leakage alone.

That leakage is the number that came back to collect. An off gate leaks
nA-scale current (1-20 nA at 90 nm-class processes; Vt- and
temperature-dependent): 10 nA × 10⁹ gates × 1.2 V ≈ 12 W of doing nothing,
and 20 nA buys tens of watts. Before ~2000 leakage was under 1% of chip
power; by 90 nm (2004-2006) it was 25-40% on hot designs
(design-dependent, worse hot). The answers, readable in any datasheet:
**clock gating** — stop toggling the clock to idle blocks, zeroing the
dynamic term α·C·V²·f — and **power gating** — cut VDD with a sleep
transistor, killing V·I_leak. The endpoint is **dark silicon**:
Esmaeilzadeh et al. (ISCA 2011) projected 8 nm-era designs could afford to
power under half the area they can print: transistors you cannot afford to
turn on.

## Switch networks and the price list: why NAND costs 4 and AND costs 6

A CMOS gate is two switch networks: a pull-up (PMOS) to VDD, a pull-down
(NMOS) to GND. Complementarity makes the networks duals — PMOS in
parallel wherever NMOS are in series — guaranteeing exactly one network
closed for every input combination. The function falls out of the wiring.
The NAND:

```text
                VDD
                 |
        +--------+--------+
        |                 |
    a --o--[P1]       b --o--[P2]     PMOS pair in PARALLEL:
        |                 |               any 0 input closes one,
        +--------+--------+               pulls OUT high
                 |
               OUT ----------------->   out = 0  only when a=1 AND b=1
                 |
             [ N2 ] <-- gate = b
                 |
             [ N1 ] <-- gate = a        NMOS pair in SERIES:
                 |                       both 1 to pull OUT low
                GND
```

Read it as switches: a=0, b=1 closes P1 — output pulled to VDD, the NMOS
chain broken at N1 regardless; a=1, b=1 opens both PMOS, closes both NMOS
— output pulled to GND. NOT(a AND b), in **4 transistors**. The NOR
mirrors it (2 PMOS in series, 2 NMOS in parallel; also 4). The price list:

| gate | transistors | why |
|------|-------------|-----|
| NOT  | 2 | one pair |
| NAND2, NOR2 | 4 | the natural switch networks |
| AND2, OR2 | 6 | NAND/NOR + inverter |
| XOR2 | 12 static, 6 with transmission gates | the parity tax |

**CMOS is naturally inverting** — NAND, NOR, NOT are the cheap shapes;
AND/OR pay 2 transistors and one extra 0.69·RC for the trailing inverter.
Now kill the folklore: "NAND flash is dense because NAND gates are
cheap." NAND flash is named for its array topology — cells strung in
series, the same series-switch shape as the NAND's NMOS stack — and is
dense because the string shares contacts (versus one per cell in the
parallel NOR array). Two facts share a name for the same geometric
reason; "NAND is denser" about gates conflates a flash story with a
transistor count.

One more shape: an NMOS alone passes a strong 0 but a weak 1 (topping out
near VDD − Vt); a PMOS passes a strong 1 and a weak 0. In parallel, driven
by complementary signals, each covers the other's weak level: the
**transmission gate**, a switch passing both logic levels — the trick
behind pass-transistor logic's cheaper muxes and XOR (a 2:1 mux: 6 with
transmission gates, ~12 static), and behind chapter 04's SRAM write path.

## Delay is RC: 0.69, FO4, and the end of free gigahertz

Gate delay is not the transistor "being slow" — it is the transistor
charging a capacitor through a resistance. The output drives every fanout
gate's input pin (~0.1-0.3 fF each at 7 nm-class nodes) plus the wire —
~0.15-0.25 fF/µm on intermediate metal, so a 200 µm route adds ~40 fF,
more than the pins it connects. The conducting transistor is a kΩ-scale
resistor. RC into a capacitor gives the exponential curve:

```text
 VDD |                        .-----------------   V(t) = VDD(1 - e^(-t/RC))
     |                  , ' - '
     |              , '
 VDD/2 - - - - x- - - - - - - - - - - - - - - -     x: V crosses 50%
     |        ,'    :                             at t = RC·ln(2) = 0.69RC
     |     ,'      :
   0 +--'---------:--------------------------> t
     0            tau = RC   (63% at one tau)
```

Derive the 50% crossing once and own it: VDD(1 − e^(−t/RC)) = VDD/2, so
e^(−t/RC) = 1/2, so t = RC·ln 2 = 0.693·RC. Every timing analyzer and PVT
corner below is this equation dressed up.

Drive strength is where design lives. A transistor 2× wider has ~½ the
on-resistance — it drives the same load ~2× faster — but its own gate pin
is ~2× the capacitance, so *whoever drives it pays 2×*. The
logical-effort calculus (Sutherland, Sproull, Harris, 1999) puts the
optimum at each stage driving **2.7-4× its own input capacitance**: 2.7 =
e^1 is the zero-parasitic result, real parasitics push toward 4. That
fixes the industry's unit of work: the **FO4 delay** — one inverter
driving 4 identical ones — ~10-20 ps at 5-7 nm-class nodes
(foundry-dependent). Insiders say "this adder is 15 FO4", not "3 ns": FO4
ports across nodes because it is normalized to the silicon's own speed.

Cycle-time arithmetic explains a decade of history. A 3 GHz clock is a
333 ps period: at ~20 ps per FO4, **~17 FO4 per cycle** — budgets are
quoted as 15-20 FO4, covering logic, wires, skew, and margin. More GHz
means fewer FO4 per stage, less logic per stage, a *deeper pipeline*: the
Pentium 4 — Willamette's 20 stages, Prescott's 31, clocks to 3.8 GHz —
until the power equation below killed the successor (Tejas, cancelled
2004), while a branch mispredict flushed ~20-39 mostly-empty stages.
Chapter 02 formalizes that trade; here the lesson is that GHz stopped
climbing because 15-20 FO4 is what a *coolable* cycle buys, not because
transistors stopped switching.

## The two-term bill: αCV²f + V·I_leak, and why multicore happened

Derive the dynamic term like the 0.69. Charge output capacitance C from 0
to VDD through the pull-up resistance: the supply moves Q = C·VDD, so it
delivers energy **C·VDD²**; the capacitor stores ½·C·VDD²; the other
½·C·VDD² burns in the resistance — *independent of the resistance value*
(the physics of the loss). Discharge dumps the stored half as heat too.
So every 0→1 transition costs the supply CV². With the fraction of cycles
in which a node makes a 0→1 transition — the **activity factor α**,
0.01-0.2 in real logic — the chip's bill is the equation that ran the
2000s:

```text
P  =  α·C·V²·f  +  V·I_leak
     ^^^^^^^^^^     ^^^^^^^^
     dynamic:       static:
     transitions    the off-switch leak
     per second     (nA-scale × 10⁹, section 2)
```

The dynamic term's **V² is the lever**: 1.2 V → 0.9 V drops dynamic power
to (0.9/1.2)² = 0.5625 of its old self — a 44% cut for the same clock and
logic, from one exponent. That fact is the entire **DVFS** industry
(dynamic voltage and frequency scaling): your laptop's governor drops V
and f together, because lower V means slower gates — the triangle below —
so frequency must follow voltage down.

The bill is also why your CPU has cores. The 2004 plan was a 4 GHz+ single
core, and planar physics said no: the required voltage and leakage could
not be cooled at sane cost — the **power wall**. The levers: V² says lower
voltage (f follows); α says gate the idle; leakage says a
multibillion-transistor single core pays nA-scale rent on everything. The
industry took the intersection: two slower cores at lower V beat one fast
core *within the same watts*, with software supplying the parallelism —
module 09's reason for existing, and module 12's GPU bet (throughput per
watt, thousands of simpler cores), played out.

Voltage, temperature, frequency are a triangle. Lower V → lower drive
current → each 0.69·RC stretches → margin shrinks; heat makes both worse.
Foundries force it with **PVT corners**: signoff at the *worst*
combinations — "slow transistors, 0.72 V, 125 °C junction"-style, values
node- and foundry-dependent — not the typical one. Ships have margin, and
**binning** is that margin's business model: one design, corner-sorted,
sold as 3.5/4.0/4.5 GHz SKUs. The enthusiast's undervolt is the
consumer-side arbitrage of the same margin — harvesting slack the foundry
made the vendor leave — and it works until a 0.72 V-class, 110 °C-class
day finds you, which is why undervolting folklore and warranties disagree.

## The clock: discrete time, skew, and the metastability cliff

Combinational logic just maps inputs to outputs; state needs time chopped
into steps, and hardware's answer is the clock. The alternative exists —
asynchronous, self-timed logic with completion handshakes (the AMULET
asynchronous ARM research chips) — and can be energy-efficient. It lost
anyway, for a precise reason: **verifiability**. One global tick turns a
continuum of analog waveforms into discrete cycle numbers — every
flip-flop samples at edge N, results are consumed at edge N+1, and "when
did this wire settle?" becomes "by which edge?" The phrasing chapter 02
reuses: *a clock is a data structure for time.* It costs two diseases.

Disease one: **skew**. The tick must reach every flip-flop through
repeater buffers — the **clock tree**, drawn as an H so every leaf sees
equal wire length — and still arrives with 10s of ps of spread across a
die, each ps stolen from chapter 02's cycle budget. The **PLL** multiplies
a quartz reference up (crystals are stable but 10s of MHz;
100 MHz × 40 = 4 GHz). Multiple domains — CPU, memory controller, each
with its own PLL — create the boundary where disease two lives.

```text
        quartz 100 MHz --> [PLL x40] --> 4 GHz tick
                                |
                            [buffer]
                          /          \
                     [buffer]      [buffer]        the H-tree: equal
                    /       \      /       \       wire lengths, so the
                  ...       ...  ...       ...    tick arrives together
                (10^5 - 10^7 flip-flop clock pins; the tree burns
                 20-40% of dynamic power on large designs)
```

Disease two: **metastability**. A signal crossing clock domains is sampled
at arbitrary phase; sample it in the window where it crosses 50% of VDD —
the setup/hold window, chapter 02's topic — and the flip-flop's internal
cross-coupled pair can balance at half-voltage for an *indeterminate* time
before resolving. Nothing is broken: a bistable element was asked to
arbitrate an analog race, and resolution time is exponential, not bounded.
The fix is the **two-flop synchronizer**: the first flop may go
metastable, but gets a full period to resolve before the second samples.
The arithmetic (Wakerly's form) is the design rule:

```text
MTBF = e^(tr/tau) / (f_clk · f_data)

tr  = resolution time offered (period - setup; ~1.8 ns on a 2 ns clock)
tau = the flop's metastability time constant (process-dependent:
      ~10-20 ps on modern nodes, ~50 ps on a 2000s-era node)
```

Run real numbers and watch the cliff. f_clk = 500 MHz, f_data = 10 MHz,
tr = 1.8 ns. With τ = 50 ps: tr/τ = 36, e³⁶ ≈ 4×10¹⁵, MTBF ≈
4×10¹⁵/(5×10⁸ · 10⁷) ≈ **0.86 s** — fails about every second. With
τ = 20 ps: tr/τ = 90, e⁹⁰ ≈ 1.2×10³⁹, MTBF ≈ 2.4×10²³ s ≈ **7.7×10¹⁵
years** — 500,000× the age of the universe. Same circuit, τ halved, MTBF
multiplied by e^54: that exponent is why the rule is *two* flops, never
one, and why folklore says "add a flop if the MTBF is within a few orders
of magnitude of comfortable."

**Multi-bit buses** break the two-flop fix: each bit resolves
independently, so a sampled word can tear — half old, half new. The rule:
cross **one bit at a time** (a handshake flag, itself two-flop
synchronized), and move multi-bit counters in **Gray code**, where one bit
changes per increment: 7→8 is 0b0111→0b1000 in binary (four bits) but
0b0100→0b1100 in Gray (one bit) — a torn sample is old or new, never a
third value. That trick is the async FIFO pointer discipline chapter 03
implements in Verilog.

## What insiders know

- Transistor counts are the interview party trick: inverter 2, NAND2/NOR2
  4, AND2/OR2 6, XOR2 12 static or 6 with transmission gates — and the
  *reason* (parallel vs series networks, naturally inverting) is what
  interviewers probe.
- Budgets are quoted in FO4, never ns: "this pipeline stage is 15 FO4";
  ~10-20 ps per FO4 at 5-7 nm-class nodes, 333 ps per 3 GHz cycle = 15-20
  FO4, and any block above ~20 FO4 is shipping a clock compromise.
- Leakage was a rounding error until 90 nm, then 25-40% of power (hot,
  design-dependent); clock gating kills α·CV²f, power gating kills
  V·I_leak, dark silicon (>50% unaffordable area at 8 nm-class, ISCA
  2011) is the endpoint.
- "NAND is denser" folklore conflates flash array topology (series string,
  shared contacts) with the logic price list (4 transistors). Two true
  facts, one shared name, zero causal link.
- Signoff happens at PVT corners like slow/0.72 V/125 °C, not nominal;
  binning (3.5/4.0/4.5 GHz from one design) sells the margin back by
  speed grade, and undervolting harvests it personally — until the corner
  finds you.
- Metastability MTBF moves by e^(tr/τ): 500 MHz/10 MHz/1.8 ns swings from
  0.86 s (τ = 50 ps) to 7.7×10¹⁵ years (τ = 20 ps). Never cross a domain
  with one flop; never cross a bus without Gray code or a handshake.
- kΩ·fF = ps exactly (10³ Ω × 10⁻¹⁵ F = 10⁻¹² s): the head-check for any
  RC delay, and the calibration of this chapter's simulator.

## See it yourself

**Paper lab:** derive t = RC·ln 2 from the 50% condition; compute
(0.9/1.2)² = 0.5625 (the 44% DVFS cut); divide 333 ps by 20 ps (~17 FO4
per 3 GHz cycle).

**The simulator.** An event-driven gate simulator: gates are functions
with delays, time is a priority queue, delay is 0.69·R·C in code — and a
pulse narrower than a gate's delay is swallowed (the inertial rule), the
software image of "never crossed the 50% threshold". Units: kΩ·fF = ps;
a unit pin is 1 fF, a unit pull 7.2 kΩ, so one FO4 = 0.69·7.2·4 ≈ 20 ps
(5 nm-era-style; real 5-7 nm FO4 spans 10-20 ps).

```python
# gatesim.py -- an event-driven gate simulator with RC delays.
# Units: resistance kOhm, capacitance fF, time ps. kOhm*fF = ps, exactly.
# A gate's delay = 0.69 * R * C_load. Pulses shorter than a gate's delay
# are swallowed (inertial delay): "never crossed the 50% threshold".
import heapq

class Sim:
    def __init__(self):
        self.q, self.seq, self.gen = [], 0, {}
        self.net, self.fan, self.log = {}, {}, []

    def gate(self, out, fn, r, *ins):
        """One gate: output net `out`, logic fn over inputs `ins`,
        pull resistance r in kOhm (smaller r = stronger gate)."""
        for n in ins:
            self.fan.setdefault(n, []).append((out, fn, r, ins))

    def cap(self, net):                       # load on a net: 1 fF per pin
        return float(len(self.fan.get(net, [])))

    def set(self, net, v, t=0.0):             # drive an input net at time t
        self.seq += 1
        heapq.heappush(self.q, (t, self.seq, net, v, None))

    def _fire(self, g, t):                    # g's inputs just changed at t
        out, fn, r, ins = g
        v = fn(*[self.net.get(i, 0) for i in ins])
        self.gen[out] = self.gen.get(out, 0) + 1     # cancel pending output
        if v != self.net.get(out):
            self.seq += 1
            heapq.heappush(self.q, (t + 0.69 * r * self.cap(out),
                                    self.seq, out, v, self.gen[out]))

    def run(self, until=1e9):
        while self.q and self.q[0][0] <= until:
            t, _, net, v, gen = heapq.heappop(self.q)
            if gen is not None and gen != self.gen.get(net):   # cancelled
                continue
            if v == self.net.get(net):
                continue
            self.net[net] = v
            self.log.append((round(t, 2), net, v))
            for g in self.fan.get(net, []):
                self._fire(g, t)

INV  = lambda a: 1 - a
NAND = lambda a, b: 0 if (a and b) else 1
```

**Exercise 1 — the chain of 4, and where it stops.** Each stage delays
0.69·7.2·1 = 4.97 ps. Sweep the input period:

```python
from gatesim import Sim, INV

def chain4(period_ps, until=300.0):
    s = Sim()
    for out, i in [("w1","in"),("w2","w1"),("w3","w2"),("w4","w3")]:
        s.gate(out, INV, 7.2, i)
    t = 0.0
    while t <= until:                    # square wave of given period
        s.set("in", 1, t); s.set("in", 0, t + period_ps/2)
        t += period_ps
    s.run(until)
    return sum(1 for tt, n, v in s.log if n == "w4")

for T in (40, 20, 12, 10, 8):
    print(f"period {T:3d} ps (half {T//2:2d}): w4 edges {chain4(T):3d}")
```

```text
period  40 ps (half 20): w4 edges  15
period  20 ps (half 10): w4 edges  29
period  12 ps (half  6): w4 edges  48
period  10 ps (half  5): w4 edges  58
period   8 ps (half  4): w4 edges   0
```

Propagation dies between half-period 5 and 4 ps — within 1 ps of the
4.97 ps stage delay: a pulse narrower than 0.69·RC never crosses the 50%
threshold. The RC limit, observed in software; your CPU's maximum clock
is this experiment scaled up.

**Exercise 2 — measure an FO4 like silicon does** (real chips use ring
oscillators): 5 inverters in a ring, each node loaded with 4 unit pins.

```python
s = Sim()
names = [f"w{i}" for i in range(5)]
for i in range(5):
    s.gate(names[i], INV, 7.2, names[i-1])       # the ring wiring
    for k in range(3):                           # 3 dummy pins: 4 per node
        s.gate(f"pad{i}_{k}", lambda a: 0, 7.2, names[i])
s.net["w1"] = 1; s.net["w3"] = 1                 # mid-flight snapshot
s.set("w0", 1, 0.0)                              # the kick that starts it
s.run(until=900.0)
ts = [t for t, n, v in s.log if n == "w0"]
period = ts[-1] - ts[-3]
print("period =", round(period,1), "ps; FO4 = period/(2*5) =",
      round(period/10, 2), "ps")
```

```text
period = 198.7 ps; FO4 = period/(2*5) = 19.87 ps
```

The edge must lap the 5-stage ring twice to restore polarity, so period =
2·N·t_pd and FO4 = period/10 ≈ 19.9 ps — a 3 GHz cycle is 333/19.9 ≈ 17
of them.

**Exercise 3 — the thermal wall on paper.** One gate, C = 10 fF (a
moderately loaded output), V = 1.0 V, f = 1 GHz, α = 0.1:

```python
C, V, f, alpha = 10e-15, 1.0, 1e9, 0.1
P = alpha * C * V**2 * f
print(P*1e6, "uW/gate;", P*1e3*1e3, "mW per 1,000-gate block;",
      P*1e9, "W per billion gates")
```

```text
1.0 uW/gate; 1.0 mW per 1,000-gate block; 1000.0 W per billion gates
```

1 µW per gate is nothing; a billion gates at those parameters is 1,000 W
— the wall on paper, before leakage and the clock tree. Every "why is my
CPU capped at 5 GHz" reduces to this arithmetic plus section 5's second
term.

**Exercise 4 — the metastability cliff.** Evaluate
`math.exp(1.8e-9/tau)/(5e8*1e7)` for τ = 50e-12 and 20e-12: 0.86 s and
7.7×10¹⁵ years — same circuit, two processes, a factor of e^54. Repeat
with tr = 0.2 ns and watch the good τ collapse too.

**On your own machine** (Linux laptop): `grep MHz /proc/cpuinfo` idle and
under load — the DVFS governor trading V² for speed; if RAPL is exposed,
read `/sys/class/powercap/intel-rapl:0/energy_uj` twice one second apart
and subtract — your CPU's watts, measured by the chip.

## How to use this knowledge

- **Performance claims decode into FO4.** "3 GHz" is 333 ps = 15-20 FO4
  per cycle; "mispredict costs ~20 cycles" is 20 × 17 FO4 flushed. A GPU
  vendor quoting half a CPU's clocks is pricing thermal headroom.
- **The hierarchy gets units.** A DRAM access is ~100 ns ≈ 5,000 FO4 ≈
  300 cycles — one division that explains why module 01 ch02's cache
  line dominates software performance.
- **Benchmark variance stops being mysterious.** Thermal throttling at a
  ~100 °C-class junction, governor voltage steps, and corner-margin
  binning are section 5's equation running on your desk; two "identical"
  SKUs are two draws from one corner distribution.
- **Whiteboard screens.** Derive 0.69·RC, derive αCV²f from the
  ½CV²-vs-CV² bookkeeping, name why NAND is 4 transistors and AND is 6 —
  each a 90-second derivation and a standard interview question.
- **Datacenter power debugging.** A server drawing real watts at 0% load
  is not broken: leakage plus a clock tree burning 20-40% of dynamic
  (large-design range) are toggling whether or not anyone is home. The
  fix is architectural, so capacity planning owns it.

## Connections

- **Module 01 ch01 (silicon, DRAM):** this chapter is the floor under it —
  "a capacitor stores a bit" meets "a gate is a capacitor"; ch04 fuses
  them into the 6T SRAM cell.
- **Module 01 ch02 (the hierarchy):** the ~100 ns DRAM trip priced here
  at ~5,000 FO4 is why that chapter's cache line "explains everything".
- **Module 17 ch02:** setup/hold, the skew budget's slot in the timing
  equation, and the P4 mispredict penalty promised in section 4.
- **Module 17 ch03:** the Gray-code async FIFO pointers derived here
  become ~40 lines of Verilog.
- **Module 17 ch04:** cross-coupled inverters (ch02) plus this chapter's
  transmission gates build the 6T cell; DRAM timing inherits these units.
- **Module 17 ch05:** binning, corners, and margin become business models
  — yield arithmetic decides which corners exist.
- **Module 09 and module 12:** the power wall that forced multicore, and
  the GPU as the throughput-per-watt endpoint, are this chapter's
  two-term equation playing out.

## Quiz

1. In a static CMOS gate at a steady input, what current flows from VDD to
   GND, and why? What changed at 90 nm to make "static power is free"
   false — and what are the two architectural responses?

2. A library quotes a 2-input AND at 6 transistors and a 2-input NAND at
   4. Explain the difference from the switch networks (not just the
   counts), and state the general bias of CMOS it reveals.

3. A 2-input NAND has a = 0, b = 1. Name exactly which transistors conduct,
   which switch network is closed, and the output value.

4. A governor drops a core from 1.2 V to 0.9 V at constant frequency and
   workload. By what factor does dynamic power fall, and what percentage
   cut is that? Which single symbol in which equation produced it?

5. In exercise 1, the 4-inverter chain propagates 58 output edges at input
   period 10 ps and zero at period 8 ps; the stage delay is 4.97 ps.
   Explain the boundary precisely — what does the simulator's inertial
   rule model about real silicon?

6. A chip passes every test at nominal voltage and 25 °C, but field
   returns cluster in hot rooms at low supply. Give the PVT explanation,
   the signoff practice it violates, and the business practice that turns
   the same silicon's corner spread into a product lineup.

7. An asynchronous FIFO uses plain binary read/write pointers crossing
   domains through a two-flop synchronizer per bit. It works for days,
   then overflows once. Diagnose the multi-bit tear with the 7→8 binary
   transition, and name the two-part fix this chapter prescribes for bus
   crossings.

8. Module 01 chapter 02 put a DRAM access at ~100 ns and ~300 cycles. Using
   this chapter's FO4 arithmetic (~20 ps per FO4, ~17 FO4 per 3 GHz cycle),
   convert 100 ns to FO4, and explain in one sentence why that number
   forces the hierarchy module 01 built.

<details>
<summary>Answers</summary>

1. **Zero DC current — one transistor of each type is off.** The pull-up
   and pull-down are complementary duals, so exactly one network conducts
   per steady input: no VDD-to-GND path, only the off-transistors' nA
   leakage. At 90 nm, 1-20 nA per gate × 10⁹ gates × ~1.2 V reaches tens
   of watts (25-40% of power on hot designs). The responses: **clock
   gating** (zero the α·C·V²·f term for idle blocks) and **power gating**
   (cut VDD to kill V·I_leak); dark silicon is the endpoint.

2. **The NAND is the natural switch network; the AND buys an inverter.**
   NAND: 2 PMOS in parallel (any 0 input closes one, pulling the output
   high) over 2 NMOS in series (both 1 to pull low) — duality guarantees
   one closed network per input, 4 transistors. AND = NAND + inverter = 6
   transistors and one extra 0.69·RC. The bias: **CMOS is naturally
   inverting** — NOT/NAND/NOR are minimal; AND/OR pay for the trailing
   inversion.

3. **The pull-up is closed through P1; the output is 1.** a = 0 turns P1
   on (PMOS conducts when its gate is low), connecting the output to VDD;
   the parallel PMOS pair makes P2 irrelevant, and the NMOS chain is
   broken at N1 (an NMOS needs its gate high). Output = 1 = NAND(0,1),
   with no DC current drawn.

4. **Factor 0.5625 — a 44% cut (43.75%).** Dynamic power is α·C·V²·f, so
   at fixed f and workload the ratio is (0.9/1.2)² = 0.5625. The symbol is
   the squared **V** — the exponent behind the DVFS industry.

5. **A pulse must outlive one stage delay.** The input half-period is the
   pulse width at stage 1. At 5 ps it just exceeds the 4.97 ps delay, so
   the pending output event fires before the return edge cancels it and
   walks the chain; at 4 ps the next input edge arrives first, the
   simulator cancels the pending transition (the `gen` bump), and the
   pulse dies. The inertial rule models RC reality: VDD(1 − e^(−t/RC))
   never reaches the 50% threshold if the input reverses first — scaled
   up, a cycle shorter than the path's RC is a clock the logic cannot
   answer.

6. **A PVT corner escape.** Low supply cuts drive current, stretching
   every 0.69·RC; heat slows transistors further and inflates leakage.
   Nominal-condition tests (1.0 V-class, 25 °C) validate a corner the
   field never visits; signoff requires closure at the worst corner —
   slow transistors, ~0.72 V, 125 °C-style (values vary by node and
   foundry). The business practice is **binning**: one design,
   corner-sorted, sold as 3.5/4.0/4.5 GHz SKUs, so the spread becomes a
   lineup instead of a recall. Undervolting is the private version of the
   same arbitrage; the hot-room failure is its failure mode.

7. **The bus tore.** Two-flop synchronizers fix per-bit metastability, but
   pointer bits resolve independently. On 7→8, binary 0b0111→0b1000
   changes four bits at once; mid-flight sampling can deliver 0b1111 or
   0b0000 — values the counter never held — so the FIFO computes a bogus
   full/empty decision and over/underflows later (rarely enough to pass
   testing, MTBF-style). The fix: cross **one bit at a time** via a
   handshake flag (itself two-flop synchronized), and carry the pointer
   in **Gray code** — 7→8 is a single-bit change, 0b0100→0b1100 — so a
   torn sample is old or new, never a third value.

8. **~5,000 FO4.** 100 ns / 20 ps = 5,000; at ~17 FO4 per 3 GHz cycle that
   is ~300 cycles, matching module 01 ch02. A single DRAM access costs
   thousands of FO4 against a 15-20 FO4 per-cycle budget, so the hierarchy
   exists to keep most accesses on chip — module 01's cycle counts, now
   priced in the physics that produces them.

</details>
