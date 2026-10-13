# Chapter 05 — From RTL to silicon: the economics of hardware

> **The 30-second version.** Chapters 03 and 04 left you with verified RTL. This chapter is what happens next: you rent a compiler (Synopsys/Cadence tools), rent a fabricator (TSMC: an N5-class wafer runs $13,000-17,000 by public estimates), and the fabricator rents the one machine that can print it (ASML: the sole EUV supplier, at $150-200 M per machine). The node name is a density class, not a size — and the density stopped helping your caches, because the 6T cell from chapter 04 shrinks 5% per node while logic gains 1.6×. Between your netlist and GDS sit place-and-route, 20-30 ECO iterations, and a test discipline software never meets: scan chains that turn the chip into one shiftable register, and memory BIST your CPU runs on every boot — the ~100 ms cold-start pause. Then the one equation that prices everything: **Y = e^(−D₀·A)** — defects are a spatial Poisson process, so at early-ramp defect density (1.0/cm²) a 100 mm² die yields 37% ($46/good die) and a 500 mm² die yields 0.67% ($12,577/good die). The industry's answers — chiplets (AMD's "yield hack that became an architecture"), CoWoS packaging, binning (the SKU list is the yield curve's product) — are this arithmetic wearing an architecture costume. Hardware is a compiler for money: the RTL is a program whose output is a cost structure.

## The machine that builds the machine

You have RTL. You do not have a fab, and you never will: a leading-edge fab costs $15-20 B each (TSMC's Arizona complex: $65 B committed across 3 fabs by its 2024 expansion). The industry solved this by splitting into four layers, and the money flows one way while silicon flows the other:

```text
+--------------------------------------------------------------+
|  FABLESS DESIGNERS                                           |
|  Apple, AMD, NVIDIA, Qualcomm, MediaTek: they own the RTL    |
|  pay: design NRE (hundreds of $M for a flagship-class part)  |
|       + IP licenses (Arm cores, interface PHYs, $0.5-5 M/blk)|
+----------------------+---------------------------------------+
|  EDA                  |  the tool rents: Synopsys + Cadence  |
|  (the compilers)      |  ~$10 B/yr combined (FY2023: ~$6+4B) |
+----------------------+---------------------------------------+
|  FOUNDRIES            |  the wafer rents: TSMC, Samsung,     |
|                       |  Intel; N5-class wafer $13-17 k,     |
|                       |  N3-class near $20 k (public est.)   |
+----------------------+---------------------------------------+
|  EQUIPMENT            |  ASML: the sole EUV supplier;        |
|                       |  $150-200 M/machine (High-NA ~$380 M)|
|                       |  Applied Materials, Lam, KLA, TEL    |
|                       |  around it                           |
+----------------------+---------------------------------------+
   money flows up, masks flow down (an N5-class full set:
   on the order of $10-15 M, estimates vary), and the bottom
   layer is a single-source chokepoint.
```

The chokepoint deserves plain words. EUV lithography prints the finest layers with 13.5 nm light, made by firing a tin droplet *twice* with a CO₂ laser — a pre-pulse flattens the droplet, the main pulse (~25 kW-class) turns it into plasma — 50,000 times per second. ASML (Veldhoven, with Zeiss optics and Trumpf lasers) is the only company on Earth that builds these machines, and the geopolitical era made the monopoly explicit: from 2019 onward the Dutch government never granted an EUV export license to China, and the 2022 US rules extended controls into advanced DUV — the 2019-2021 export-control era is the single-source chokepoint, stated plainly.

The numbers that matter to you as a designer: the wafer is a fixed cost (~$10,000/wafer is the round number this chapter's table uses so the arithmetic stays in your head; treat real leading-node prices as $13,000-20,000), and the mask set + NRE are *sunk* before the first wafer runs. Everything from here is about how many good dies each wafer surrenders.

## "3 nanometers" of what, exactly?

Node names were once physical: until the mid-1990s a "250 nm" node meant the DRAM half-pitch was 250 nm. Then scaling decoupled from the label, and the names became marketing for a *density class*. A real TSMC N5 process: contacted gate pitch ~48 nm, tightest metal pitch ~30 nm. Nothing on the die is 5 nm — not the gate, not the metal, not the fin. The insider's rule: **compare foundries by logic density (MTr/mm²) and by high-density SRAM cell size (µm²), never by node name.** TSMC's own figures: N5 ≈ 137 MTr/mm²; N3 ≈ 215 MTr/mm² — roughly 1.57×, which is what "one node" now buys.

The SRAM cell is where the story turns. TSMC's published HD 6T cells: N7 0.027 µm² → N5 0.021 µm² → N3 0.0199 µm². From N5 to N3, logic density improved ~1.57× and the SRAM cell shrank **5%**. The cell from chapter 04 — two cross-coupled inverters plus two pass gates — is an analog circuit in disguise: its read stability and write margin depend on transistor strength ratios, and the weak pull-up/pass-device sizes do not scale with the fin pitch. Past N5, "SRAM scaling is dead" is the insider's flat statement, with an architectural consequence: a big-core die spends 30-60% of its area in SRAM (L2, L3, tags, TLBs), so the node's density gain lands on the compute sliver. Run the arithmetic for a die that is 50% SRAM: the same design occupies 0.5/1.05 + 0.4/1.6 + 0.1/1.6 ≈ 0.79 of the area — a 1.27× shrink, not 1.57×. This is why each generation's cache grows a few percent where its ALU count doubles, and why accelerators (small control, huge regular math arrays) extract full node value.

## The back half of the flow: floorplan, route, ECO, signoff

Chapter 03's synthesis ended at a netlist of standard cells. The back half turns it into geometry, in five stages, each with a named failure mode:

1. **Floorplan** — fix the die outline, place the big macros (your SRAMs from chapter 04, PHYs, PLLs), assign pin locations, build the power grid. A macro in the wrong corner poisons every later stage.
2. **Place and route** — place ~10⁷-10⁸ standard cells, then wire them on a 14-16 layer metal stack (N5-class). The recurring villain is **congestion**: a region needs more wires than it has tracks, so routes detour around it and their RC delay (chapter 01's wire model) explodes.
3. **The ECO loop** — engineering change orders: edit-re-route-re-time, iterated. Folklore says 20-30 iterations for a big chip; a chip that closes in 10 is either small or lucky.
4. **Clock tree synthesis** — chapter 01's H-tree at die scale: buffer chains sized so every flip-flop's clock arrives within a few tens of picoseconds of skew. Skew eats the setup budget chapter 02 derived, one gate at a time.
5. **Signoff** — three gates against the foundry's rulebook. **DRC** (design rule check: thousands of geometric rules at N5-class). **LVS** (layout-vs-schematic: the netlist extracted from your drawn metal equals the netlist you synthesized). **ERC** (electrical rule check).

DRC hides the rule that best shows this world's character: the **antenna rule**. During plasma etch, a long metal line connected only to a gate has no discharge path and collects charge — enough to punch through the gate oxide and kill the transistor. The fixes are antenna diodes (a diffusion tied to the net to bleed the charge) or jumpering (break the violating layer, hop up, come back down). A rule that sounds like superstition is etch physics. When signoff passes, out goes GDSII, and the foundry builds the masks — the $10-15 M check from the diagram.

## Design for test: the discipline software never meets

Software ships with a debugger; silicon ships blind — you cannot attach gdb to 3 billion transistors. **DFT** (design-for-test) is the industry's answer, and it is built into the RTL you wrote in chapter 03 whether you noticed or not.

**Scan chains.** Every functional flip-flop becomes a scan flip-flop: a mux in front of D that selects either the functional input or the output of the previous flop in a chain. In test mode the muxes flip, and the whole chip becomes one (or dozens of parallel) giant shift register:

```text
   scan_in ----+                                            +---- scan_out
               |                                            |
            +--[--]+   Q    +--[--]+   Q    +--[--]+   Q    |
            |  mux |---+--->|  mux |---+--->|  mux |---+--> ...
   func D --+>     |        |      |        |      |
            +------+        +------+        +------+
              ^                ^                ^
        (each mux: functional D vs chain input, selected by scan_en)

   test sequence:  N shift clocks (10-50 MHz) to load a state,
                   ONE at-speed functional clock (capture),
                   N shift clocks to unload and compare.
```

Shift in any state, pulse one clock, shift out the result: the chip is now a deterministic FSM tester, and every combinational cloud between flops is fully controllable and observable. The fault model is **stuck-at**: a wire welded to 0 or to 1. Fault coverage = fraction of stuck-at faults your patterns distinguish; **ATPG** (automatic test-pattern generation) tools grind out the patterns; the signoff bar is **99%+ stuck-at coverage** (of testable faults — redundant logic is waived). The price: scan costs ~5-15% area and a mux delay on every register.

**Memory BIST.** Your SRAM macros (chapter 04's 6T arrays) are too dense to observe through scan, so each macro gets a tiny built-in state machine that runs **march tests** — patterns applied to every cell in a fixed address order. The classic March C−, in the standard notation, is a sequence of march elements:

```c
/* March C- (one of the standard BIST march algorithms), C-style:
   each element applies its ops to every cell, moving up (^) or
   down (v) the address order; w0 = write 0, r1 = read expect 1. */
march_C_minus() {
    w0;                         /* U(up+down): write all zeros        */
    for (a = min; a <= max; a++) { r0; w1; }   /* ascending: read 0, write 1 */
    for (a = min; a <= max; a++) { r1; w0; }   /* ascending: read 1, write 0 */
    for (a = max; a >= min; a--) { r0; w1; }   /* descending                */
    for (a = max; a >= min; a--) { r1; w0; }   /* descending                */
    r0;                                        /* up+down: read all zeros   */
}
```

The alternating address directions are not decoration: an ascending write followed by an ascending read catches **coupling faults** (writing cell n flips neighbor n+1), the direction reversals catch **address faults** (the decoder maps two addresses to one cell — the second write corrupts the first read's expectation), and plain r/w catches **stuck bits**. Your CPU, your phone, your SSD controller run these marches at *every* power-on — plus DRAM training — and that is the 100 ms-class black-screen pause on a cold boot. It is the deep ancestor of the PC POST: the pause before the logo is DFT, not laziness. (DFT's payoff in the next section: **known-good-die** screening is what makes multi-die packages shippable.)

## Y = e^(−D₀·A): the exponential that prices every die

Here is the chapter's quantitative core, derived, not asserted. Model manufacturing defects as a *spatial Poisson process* with density D₀ per mm²: scattered independently. The number of defects landing inside a region of area A follows the Poisson distribution, so the probability of *zero* defects is

**Y = e^(−D₀·A)**  — the yield, because one defect kills a die.

Units discipline first, because this is where every yield argument goes wrong: D₀ in defects/mm² means 1.0/cm² = 0.01/mm². A 500 mm² die at D₀ = 0.1/cm² has λ = D₀·A = 0.5 — not 5, not 50. Treat D₀ as a regime, not a constant (each foundry's real number is a trade secret): **early ramp of a leading node ≈ 1.0/cm²; mature ≈ 0.1/cm²** — public-order figures.

Now the wafer. A 300 mm wafer's circle is π·150² = 70,686 mm²; the edge is junk (process control margin), so subtract a 3 mm exclusion ring → π·147² = 67,887 mm² usable; rectangle-in-circle packing for large dies costs ~13% → ~59,060 mm² effective. That is **118 gross 500 mm² dies**:

```text
                 .---.---.---.
              .-[ X ]---[   ]-.                rim squares are partial
           .-[   ]---[ X ]---[   ]-.            dies: the circle cuts
         .-[   ]---[   ]---[   ]---[   ]        them -> scrap
        .-[   ]---[ X ]---[   ]---[   ]--.
        .-[   ]---[   ]---[   ]---[ X ]--.      X = a defect lands here:
         .-[   ]---[   ]---[   ]---[   ]        that whole die is dead,
           .-[ X ]---[   ]---[   ]-.            its 499 clean mm² wasted
              .-[   ]---[   ]-.
                 '---'---'---'
```

The cost model is one line: **cost per good die = wafer cost / (gross dies × Y)**. Run it (this is the chapter's calculator, inline — the full lab is below):

```python
import math
gross = lambda a: int(math.pi * 147**2 * 0.87 / a)   # 300 mm wafer, 3 mm edge
Y     = lambda d0, a: math.exp(-d0 * a)             # d0 in defects/mm^2
for a in (100, 200, 500):        # early ramp: d0 = 0.01/mm^2 (1.0/cm^2)
    print(f"{a:3d} mm^2: gross {gross(a):4d}  Y {Y(0.01,a)*100:6.2f}%  "
          f"${10000 / (gross(a) * Y(0.01, a)):8,.0f} per good die")
```

```console
100 mm^2: gross  590  Y  36.79%  $      46 per good die
200 mm^2: gross  295  Y  13.53%  $     250 per good die
500 mm^2: gross  118  Y   0.67%  $  12,577 per good die
```

Five times the area bought 54.6 times fewer good dies — and that ratio is exact, not a coincidence: at fixed D₀, Y₁/Y₂ = e^(D₀·(A₂−A₁)) = e^(0.01×400) = e⁴ = 54.6. **Area is superlinearly expensive; that is why big chips are exponentially expensive.** The $12,577 is the early-ramp trough — the months when a flagship GPU is unobtainium. As the node matures, D₀ falls ~10× to 0.1/cm², and the same 500 mm² die yields e^(−0.5) = 60.7%, costing $140. One design, one fab, a 90× cost swing from defect density alone.

Two honesty notes. Poisson assumes unclustered defects; real ones cluster (a scratch kills its neighbors together), so the negative-binomial Y = (1 + D₀A/α)^(−α) with clustering parameter α ≈ 2-4 fits ramp data better — at D₀ = 1/cm², A = 5 cm², α = 2 it gives 8.2% instead of 0.67%; Poisson is the pessimistic bound, and the lesson survives every model: area hurts superlinearly. And the "one defect kills the die" rule has exceptions — redundancy — which is the next section.

## Cut the die: chiplets, packaging, and the architecture that money wrote

**The reticle limit.** A lithography exposure field is 26 × 33 mm = 858 mm²; ~850 mm² is the practical maximum for one die. Monolithic designs are capped there. (The exception that proves the rule: Cerebras' WSE-3, 2024 — 46,225 mm², 4 trillion transistors, built by *stitching* exposures across an entire wafer.)

**Chiplets as yield arithmetic.** Take the 500 mm² flagship and cut it into 8 × 62.5 mm². First, the trap: Poisson is memoryless over area — if all 8 dielets must come from the same wafer neighborhood, the package's survival probability is e^(−D₀·8·62.5) = e^(−D₀A), *identical* to the monolithic die. The win is the pool: good dielets are fungible. A defect now kills 62.5 mm² of silicon instead of 500, and any 8 good dielets — from anywhere on any wafer — make a package. At early-ramp D₀ = 0.01/mm²: 944 gross dielets × 53.5% = 505 good ones = 63.2 packages per wafer → $158 of silicon + a $50-150 advanced package → **~$258 per package vs $12,577 monolithic — a 49× swing**. At mature D₀ = 0.001/mm² each dielet yields 93.9%, a same-neighborhood set survives 0.94⁸ = 60.7% — exactly the monolithic number, memoryless again — and the split *loses* ($190 vs $140): the package premium buys nothing when yields are high. Chiplets are not a religion; they are a D₀-and-area question. The lab below finds the break-even: at D₀ = 0.15/cm² and a $100 package, the 2-way split wins once the monolithic die passes **~622 mm²** — inside the reticle cap, in exactly the regime where flagship parts live. AMD's Zen 2 (2019) made this mainstream: an N7 CCD of ~74 mm² beside a 12LP-class I/O die of ~125 mm², scaling core count without scaling die area — *the chiplet is a yield hack that became an architecture.*

**Packaging as bandwidth.** The dies sit on a **CoWoS**-style (chip-on-wafer-on-substrate) silicon interposer — ~65 nm-class passive wiring, with **TSVs** (through-silicon vias, ~10 µm through a die thinned to 50-100 µm) down to the substrate; **hybrid bonding** (Cu-Cu pads welded directly, ~6-9 µm pitch, no bumps) is the 3D path beyond it. Module 12's HBM returns here as a *packaging* story:

```text
      +--------+   +---------+   +---------+
      |  GPU   |   |  HBM3   |   |  HBM3   |   dies, uBumps @ 40-55 um
      |  die   |   |  stack  |   |  stack  |   pitch, 50-100 um thick
      +--------+   +---------+   +---------+
   =============================================  silicon interposer
        |<-- ~0.5-2 mm die-to-die wiring -->|       (CoWoS); TSVs
   =============================================       punch through it
         |  BGA @ 0.4-0.8 mm pitch, PCB traces in the cm class
   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~  package substrate
```

HBM3's 1024-bit bus runs at up to 819 GB/s per stack (6.4 Gb/s/pin) because the stacks sit *µm-to-mm* from the GPU on silicon, not cm away on fiberglass. The distance back-check is pure chapter-01 physics: per 12 mm of package edge, an interposer fits 267 bumps at 45 µm pitch vs 20 balls at 0.6 mm PCB pitch — **13× the pins**; and driving 50 mm of PCB (~9.5 pF total) at 1.35 V costs ~8.7 pJ/bit vs ~0.48 pJ/bit for 2 mm on interposer (~0.8 pF, 1.1 V) — **~18× the energy**. An H100's 5 stacks × 670 GB/s = 3.35 TB/s (B200: 8 TB/s) is a packaging achievement. The counter-example on the same shelf: GDDR6X drives 21 Gb/s/pin × 384 bits ≈ 1 TB/s (RTX 4090: 1008 GB/s) over PCB traces — fewer pins, pushed harder, at roughly 2× the energy per bit.

**Binning: the yield curve becomes the SKU list.** A GPU is an array of 100+ identical small cores because a dead core is binned out, while a dead big core is a dead die. Salvage is scheduled, not accidental: AD102 (608.5 mm², TSMC 4N) is designed with 144 SMs, and the RTX 4090 ships 128 — 11% harvested. The PS3's Cell processor carried 8 SPEs with 7 enabled per die. The lab computes the 4-core CPU version: at D₀ = 0.4/cm² with 20 mm² cores, 67% of dies ship as 4-core, 22% as 3-core, 3% as 2-core. *The SKU list is the yield curve's product.*

And the module's last table — the economics→architecture feedback loop, closed:

| The architecture you know | The money that wrote it |
|---|---|
| GPU = hundreds of small identical cores | salvage/binning (AD102: 144→128 SM); regular floorplans route fast |
| Chiplets (Zen 2 onward) | Y = e^(−D₀·A) + the 850 mm² reticle cap |
| HBM beside the GPU (CoWoS) | 13× pins/mm and ~18× less energy/bit than PCB |
| Accelerators at the ridge point | FLOP/$ and FLOP/W (module 12 ch02's ridge as a budget line) |
| CXL and pooled memory | SRAM's stall → disaggregate the part whose cost curve diverged from logic's |

The endgame claim, now earned: **hardware is a compiler for money.** The RTL you learned to write in chapter 03 is a program whose output is not waveforms — it is a cost structure: die area that sets yield, core count that sets the SKU mix, memory placement that sets packaging. Module 01 ch01 said memory is shaped by physics; this chapter adds the second half — shaped by yield arithmetic and µm-vs-cm packaging. Every module of this program is a consequence of these five chapters' physics and economics.

## What insiders know

- **Never compare nodes by name.** Compare logic density (TSMC: N5 ≈ 137 MTr/mm², N3 ≈ 215) and HD SRAM cell size (N7 0.027 → N5 0.021 → N3 0.0199 µm²). The "3 nm" is a marketing label; a "5 nm" node's gate pitch is ~48 nm and metal pitch ~30 nm.
- **The SRAM wall.** Past N5 the 6T cell shrinks ~5% per node while logic gains ~1.6× — on a cache-heavy die that converts to a ~1.25× overall shrink. This is why L3 sizes crawl while compute doubles, and it is the real argument behind accelerators.
- **The yield-table unit trap.** D₀ = 0.1/cm² is 0.001/mm²; a 500 mm² die then has λ = 0.5, not 5. Check any yield claim you meet against e^(−λ) before repeating it — mixing mm² and cm² inflates yield loss by 10× or 100×.
- **The DFT signoff bars.** ≥99% stuck-at coverage from ATPG (of testable faults), every functional flop in a scan chain, per-macro march coverage for every SRAM; scan costs ~5-15% area. A chip without these numbers is a chip you cannot ship.
- **The cold boot pause is BIST.** Memory BIST plus DRAM training is the 100 ms-class black screen on every power-on — every phone, every PC, every power cycle. POST is its descendant, not its ancestor.
- **The vocabulary that gates credibility:** reticle ≈ 850 mm² (26 × 33 mm field), CoWoS, TSV (~10 µm through a thinned die), hybrid bonding (Cu-Cu, ~6-9 µm pitch), KGD (known-good die — the screen that makes chiplets shippable).
- **The ECO folklore.** 20-30 place-route-timing iterations for a big chip is normal; congestion at 90%+ track utilization is where timing goes to die. A schedule that assumes the floorplan is right the first time has never met a router.

## See it yourself

The whole chapter is a paper lab — every claim above is reproducible with ~40 lines of Python on any laptop. Save this as `yield.py` and run it:

```python
import math
R, EDGE, ETA, WAFER, PKG = 150.0, 3.0, 0.87, 10_000.0, 100.0
USABLE = math.pi * (R - EDGE) ** 2          # 300 mm wafer, 3 mm edge exclusion
gross = lambda a: int(USABLE * ETA / a)     # gross dies (approx, large dies)
Y     = lambda d0, a: math.exp(-d0 * a)     # Poisson yield; d0 in defects/mm^2

print(f"usable circle {USABLE:,.0f} mm^2; gross(500 mm^2) = {gross(500)}")
print("\n[1] cost per good die, D0 = 0.01/mm^2 (1.0/cm^2, early ramp):")
for a in (100, 200, 500):
    g, y = gross(a), Y(0.01, a)
    print(f"    {a:3d} mm^2: gross {g:4d}  Y {y*100:6.2f}%  "
          f"${WAFER/(g*y):8,.0f}")
print("\n[2] the 500 mm^2 GPU as 8 x 62.5 mm^2 chiplets, same D0:")
g, y, chips = gross(62.5), Y(0.01, 62.5), gross(62.5) * Y(0.01, 62.5) / 8
print(f"    each: gross {g}  Y {y*100:.1f}%   same-neighborhood all-8-clean: "
      f"{y**8*100:.2f}%")
print(f"    pool: {chips:.1f} packages/wafer  ->  "
      f"${WAFER/chips + PKG:,.0f}/package (incl ${PKG:.0f} pkg)")
print("\n[3] same two designs at mature D0 = 0.001/mm^2 (0.1/cm^2):")
print(f"    monolithic: Y {Y(0.001,500)*100:.1f}%  "
      f"${WAFER/(gross(500)*Y(0.001,500)):,.0f}")
ych, chips = Y(0.001, 62.5), gross(62.5) * Y(0.001, 62.5) / 8
print(f"    chiplets:   each {ych*100:.1f}%  0.94^8 = {ych**8*100:.1f}%  "
      f"${WAFER/chips + PKG:,.0f}  <- package premium now loses")
print("\n[4] 2-way split break-even, D0 = 0.0015/mm^2 (0.15/cm^2):")
d0 = 0.0015
mono  = lambda a: WAFER / (gross(a) * Y(d0, a))
split = lambda a: 2 * WAFER / (gross(a/2) * Y(d0, a/2)) + PKG
for a in (400, 500, 600, 700, 850):
    print(f"    {a:3d} mm^2: mono ${mono(a):5,.0f}  split ${split(a):5,.0f}"
          f"   {'SPLIT' if split(a) < mono(a) else 'MONO'} wins")
lo, hi = 100.0, 850.0
for _ in range(50):
    mid = (lo + hi) / 2
    if split(mid) < mono(mid): hi = mid
    else: lo = mid
print(f"    break-even: ~{(lo+hi)/2:.0f} mm^2")
print("\n[5] SKU harvesting: 4-core die, cores 20 mm^2, D0 = 0.004/mm^2:")
q = 1 - math.exp(-0.004 * 20)               # per-core kill probability
ok = math.exp(-0.004 * 20)                  # uncore survives
from math import comb
tot = 0.0
for dead in range(3):
    p = comb(4, dead) * q**dead * (1-q)**(4-dead) * ok
    tot += p
    print(f"    {4-dead}-core SKU: {p*100:5.2f}% of all dies")
print(f"    scrap tail (<=1-core or dead uncore): {(1-tot)*100:.2f}%")
print("\n[6] HBM distance back-check (per 12 mm package edge):")
print(f"    interposer uBGA @ 45 um pitch: {12000/45:.0f} pins/edge")
print(f"    PCB BGA       @ 0.6 mm pitch:  {12000/600:.0f} balls/edge")
c_pcb, c_int = 50*0.14 + 2.5, 2*0.25 + 0.3  # pF: trace + pads/driver
print(f"    E/bit: PCB 9.5 pF @1.35 V = {0.5*c_pcb*1.35**2:.1f} pJ; "
      f"interposer 0.8 pF @1.1 V = {0.5*c_int*1.1**2:.2f} pJ")
```

```console
$ python3 yield.py
usable circle 67,887 mm^2; gross(500 mm^2) = 118

[1] cost per good die, D0 = 0.01/mm^2 (1.0/cm^2, early ramp):
    100 mm^2: gross  590  Y  36.79%  $      46
    200 mm^2: gross  295  Y  13.53%  $     250
    500 mm^2: gross  118  Y   0.67%  $  12,577

[2] the 500 mm^2 GPU as 8 x 62.5 mm^2 chiplets, same D0:
    each: gross 944  Y 53.5%   same-neighborhood all-8-clean: 0.67%
    pool: 63.2 packages/wafer  ->  $258/package (incl $100 pkg)

[3] same two designs at mature D0 = 0.001/mm^2 (0.1/cm^2):
    monolithic: Y 60.7%  $140
    chiplets:   each 93.9%  0.94^8 = 60.7%  $190  <- package premium now loses

[4] 2-way split break-even, D0 = 0.0015/mm^2 (0.15/cm^2):
    400 mm^2: mono $  124  split $  192   MONO wins
    500 mm^2: mono $  179  split $  223   MONO wins
    600 mm^2: mono $  251  split $  260   MONO wins
    700 mm^2: mono $  340  split $  301   SPLIT wins
    850 mm^2: mono $  519  split $  374   SPLIT wins
    break-even: ~622 mm^2

[5] SKU harvesting: 4-core die, cores 20 mm^2, D0 = 0.004/mm^2:
    4-core SKU: 67.03% of all dies
    3-core SKU: 22.33% of all dies
    2-core SKU:  2.79% of all dies
    scrap tail (<=1-core or dead uncore): 7.85%

[6] HBM distance back-check (per 12 mm package edge):
    interposer uBGA @ 45 um pitch: 267 pins/edge
    PCB BGA       @ 0.6 mm pitch:  20 balls/edge
    E/bit: PCB 9.5 pF @1.35 V = 8.7 pJ; interposer 0.8 pF @1.1 V = 0.48 pJ
```

What to notice: [2] prints the memoryless trap (all-8-clean = 0.67%, the monolithic number) *and* the pool fix (63.2 packages/wafer) — that pair is the chiplet argument. [3] prints 60.7% twice, for monolithic and 0.94⁸ — the same property at mature D₀, where the split loses on the package premium. [4]'s break-even (~622 mm²) sits *below* the 850 mm² reticle cap: the split wins for merely huge dies, not just physically impossible ones. `ETA = 0.87` and `$10,000`/wafer are marked approximations — swap in your own and rerun; the exponentials dominate everything else. Extend it: add the negative-binomial yield from section 5 and watch the 49× chiplet win shrink to ~8× — clustering is why reality is kinder than Poisson.

## How to use this knowledge

- **Read a chip announcement critically.** "Built on 3 nm, 20% more cache": look up the HD cell size (0.0199 µm², N3) and compute the real cache-area gain (5%). Density claims are for logic; your workload's cache didn't scale.
- **Settle the monolithic-vs-chiplet argument with arithmetic, not taste.** The question reduces to three numbers — D₀, die area, package cost — and the script above. At 0.15/cm² and $100 packaging the answer is 622 mm²; recompute for your regime.
- **Debug the boot.** A blank screen for ~100-150 ms on every cold power-on before the logo is not broken — it is memory BIST and DRAM training. A part that shifts scan patterns but fails functional tests points at a test-mode strap or a stuck scan bit; a part that passes at-speed BIST but fails function points at timing (congestion), not manufacturing.
- **Answer the interview trio.** "Why are GPUs arrays of small cores?" — salvage and binning; a dead core is a binned SKU, a dead big core is a dead die. "Why chiplets?" — Y = e^(−D₀·A) against the 850 mm² reticle cap. "Why is HBM fast?" — 1024 pins at 45 µm pitch beat 20 balls at 0.6 mm; bandwidth is a packaging achievement.
- **Follow the industrial news with the physics in hand.** EUV export controls (one supplier, 13.5 nm, $150-200 M/machine), the 2023 CoWoS capacity crunch (the interposer, not the GPU, throttled AI supply), wafer-price hikes: each story is one layer of the diagram at the top of this chapter.

## Connections

- **Chapter 01** — the H-tree you derived is clock tree synthesis at die scale; the RC wire-delay model is what congestion's detours multiply.
- **Chapter 02** — the flip-flop becomes the scan flip-flop; the pipeline whose state you can now shift in, clock once, and shift out is the ATPG test vehicle.
- **Chapter 03** — synthesis was the front half of the flow; this chapter is the back half, from floorplan to GDS. Your FIFO ring, as a macro, is exactly what memory BIST marches through.
- **Chapter 04** — the 6T cell's 0.021 µm² is the number that stopped scaling and reshaped die maps; the cache you built in RTL is the SRAM-share of the die that the node myth obscures.
- **Module 01 ch01** — "memory is shaped by physics" gains its economics half: yield arithmetic and packaging distance.
- **Module 01 ch02** — the memory-hierarchy table now has a cost column: every level's size is a yield-and-area decision, not just a timing one.
- **Module 12 ch01** — HBM's 3.35 TB/s returns here as the CoWoS/µbump story: the bandwidth is a packaging achievement.
- **Module 12 ch02** — the roofline's ridge point reread as a budget line: accelerators win at the FLOP/$ and FLOP/W maximum.
- **Module 12 ch04** — CXL and pooled memory as the system-bus future of the SRAM stall: disaggregate the expensive thing whose curve diverged.

## Quiz

1. Derive Y = e^(−D₀·A) from the spatial Poisson process. Then explain, with the exponent, why 5× the die area cost 54.6× the yield in this chapter's table — and why the ratio is exactly e⁴, not an accident of the numbers chosen.
2. A 300 mm wafer costs $10,000. A die is 200 mm², and the fab is at early ramp with D₀ = 0.01 defects/mm². Compute the yield, the expected good dies per wafer (590 gross dies per wafer at 100 mm² — scale accordingly), and the cost per good die.
3. A 4-core CPU die is in production; each core has an independent 10% probability of containing a defect that kills it (and the uncore always works). What fraction of dies ship as the 4-core SKU, and what fraction as the 3-core SKU?
4. Signoff DRC reports ~4,000 antenna violations concentrated on long M5-M7 nets after the router's final pass. What physical mechanism produced them, and what are the two standard fixes?
5. A 380 mm² SoC passed static timing analysis at 2.0 GHz. First silicon passes scan test but fails functional tests above 1.4 GHz, and every failing path crosses the northeast quadrant, where the congestion map shows 92% track utilization. Diagnose the failure and name two fixes at different stages of the flow.
6. A phone's display shows nothing for ~150 ms after the power button on every cold boot, then boots normally. Nothing is wrong with the screen or the OS. What is running during that window, and what is its software-world descendant on a PC?
7. A foundry announces a new node with "1.6× density." You look up the HD SRAM cell: 0.021 µm², unchanged from the previous node. What does the insider's rule tell you a cache-heavy design actually gains, and why did the cell stall while logic density moved?
8. (Cross-chapter) Your next design is 60% L3 cache by area (chapter 04's 6T arrays). The new node offers 1.6× logic density and the HD SRAM cell shrinks 5%. What is the total die-area shrink for the same design — and using module 12 ch02's ridge-point argument, why does this push architects toward accelerators?

<details>
<summary>Answers</summary>

1. **Derivation and the e⁴ ratio.** Defects are a spatial Poisson process with density D₀ per mm²: the count in area A follows P(k) = (D₀A)^k · e^(−D₀·A) / k!, and a die survives only if k = 0, so Y = e^(−D₀·A). For the ratio: at fixed D₀, Y₁/Y₂ = e^(D₀·(A₂−A₁)) = e^(0.01·400) = e⁴ = 54.6 — exact, not an accident. The yield penalty for area is exponential in the area *difference* times D₀, which is why big (500 mm²-class) dies are exponentially expensive and why the industry's structural answers (chiplets, binning) are all ways of shrinking effective A.

2. **Y = 13.5%, ~40 good dies, ~$250.** λ = D₀·A = 0.01 × 200 = 2, so Y = e⁻² = 13.53%. Gross dies scale with usable area: 100 mm² gave 590, so 200 mm² gives 59,060/200 ≈ 295 gross. Good dies = 295 × 0.1353 ≈ 40, so each good die costs $10,000/40 = **$250**. The trap avoided: D₀ was given in defects/mm²; had it been quoted per cm² (0.01/mm² = 1.0/cm²), skipping the conversion gives λ = 0.02 or 200 instead of 2.

3. **4-core: 65.6%; 3-core: 29.2%.** Dead cores are Binomial(4, 0.1): P(all alive) = 0.9⁴ = 0.6561; P(exactly 1 dead) = 4 × 0.1 × 0.9³ = 0.2916. The 2-core SKU gets 4.86%; the tail is scrap or fused-off parts. This is the lab's binomial-with-independent-defects approximation — real defect *clustering* correlates dead cores, so measured 4-core fractions run higher.

4. **Plasma-etch charging; fixes: antenna diodes and jumpering.** During etch, a metal line whose only connection is a gate has no discharge path; it accumulates charge until the voltage can punch through the gate oxide — long upper-layer nets accumulate the most, hence the M5-M7 concentration. Fix one: insert an antenna diode (a diffusion tied to the net) to bleed charge to substrate. Fix two: jumper — hop the long span to a higher layer so no single layer accumulates a dangerous metal-area-to-gate-area ratio. The rule sounds like superstition; the mechanism is etch physics.

5. **Congestion detours added real RC that pre-route timing models underestimated.** Scan passes (no manufacturing defect), but at-speed paths through the 92%-utilized quadrant fail: routes that couldn't get tracks detoured, and their extra length is extra RC delay (chapter 01's wire model) the STA run never saw. Fixes at two stages: (a) floorplan/place — spread the macros, move the pin field, pull local utilization toward 70-80%; (b) post-route ECO — retime/repipeline the failing paths, buffer, or re-route. This is why the folklore says 20-30 ECO iterations on a big chip: the router is a physics engine the timing model only approximates.

6. **Memory BIST and DRAM training; the descendant is POST.** Every power-on, each SRAM macro's built-in self-test marches its patterns (stuck bits, coupling, address faults) and the DRAM interface trains its delays — the 100 ms-class cold-boot window. On a PC, the power-on self test is this hardware discipline's direct descendant: the black-screen pause is DFT doing its job, not the OS being slow.

7. **~1.25× overall, not 1.6×.** The insider's rule: compare HD SRAM cell µm² and MTr/mm², not node names. An unchanged 0.021 µm² cell means the cache — 30-60% of a big-core die — didn't shrink; the 1.6× lands on the logic sliver. The cell stalls because the 6T cell is an analog circuit: read stability and write margin depend on the strength ratios of its pull-up, pull-down, and pass transistors, and the weak devices don't scale with the fin pitch. "SRAM scaling is dead" past N5 is the consequence — which is why L3 sizes crawl while compute doubles.

8. **Shrink factor ≈ 0.82 — only 1.22×.** The cache's 60% shrinks by 1.05×: 0.6/1.05 = 0.571 of the die. The compute's 40% shrinks by 1.6×: 0.4/1.6 = 0.25. Total 0.821 — a 1.22× gain versus the 1.6× headline. The accelerator push is module 12 ch02's roofline as economics: a node's gift buys 1.6× on logic but 1.05× on cache, so FLOP/$ and FLOP/W at the ridge point improve by spending it on regular math arrays (fed by module 12 ch01's HBM — which this chapter showed is a packaging achievement). Economics picks the architecture — hardware is a compiler for money.

</details>
