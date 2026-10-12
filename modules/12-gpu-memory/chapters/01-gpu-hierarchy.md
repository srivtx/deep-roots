# Chapter 01 — The GPU memory hierarchy

> **The 30-second version.** A CPU hides memory latency with hardware — caches,
> branch prediction, out-of-order execution — so one thread rarely stalls. A GPU
> inverts the design: tiny threads, thousands per chip, and whenever one stalls on
> memory, switch to another. Latency becomes a cost you *hide with parallelism* — a
> 400-cycle load needs ~32 warps in flight so someone is always computing. Registers
> become the scheduling budget, shared memory is SRAM you budget by hand, L2 the
> device-wide coherence point, HBM DRAM rebuilt for bandwidth — 40x a desktop's.
> Module 01 ch02's tower, same physics, rewired.

## The inversion: buy throughput, pay latency

A stalled CPU core is wasted silicon, so a core spends its area *preventing* stalls:
predictors, an out-of-order window, three cache levels (module 01 ch02) — hundreds
of thousands of transistors per thread, serving one or two threads. The GPU's
wager: stop preventing stalls, *tolerate* them — spend that area on lanes, a huge
register file, and thousands of threads.

The arithmetic. A global load costs 400–800 cycles (Ampere-class). One warp issues
the load and sits: alone, the SM idles 399 of 400 cycles — 0.25% of peak. An
Ampere-class SM has 4 warp schedulers, each issuing one warp-instruction per cycle
and hosting up to 16 resident warps (64 per SM). Little's law:

```
warps needed  >=  load latency / instructions between dependent loads
              >=  400 cycles / 25 instructions  =  16 warps per scheduler
```

That is exactly the Ampere maximum: a global load every ~25 instructions is
break-even *at full occupancy*; one every 8 instructions needs 50 warps per
scheduler and is unhideable at any occupancy (the lab prints this table). The
"~32 warps in flight" rule of thumb is the same division at a 12.5-instruction
ratio. Where does the in-flight state live? Registers — there is no per-thread
stack to park a context (module 10 ch05: PTX has no SysV-style caller/callee-saved
table), so a warp "context switch" is the scheduler picking another ready warp;
the stalled warp's state never moves. That is why the register file, not the
caches, is the biggest SRAM on a GPU.

## SIMT: the warp, the SM, and the price of control simplicity

The unit of scheduling is the **warp**: 32 threads sharing one program counter, one
fetch, one decode — 32 lanes executing one instruction together; a wide SIMD unit
the programming model politely disguises as threads.

**Divergence is the price, paid in serialization.** If a warp's 32 threads take
different sides of an `if`, the hardware cannot issue one instruction for 32 lanes —
so it executes both paths with the other's lanes masked off, and reconverges at the
merge point; a 32-way switch runs at 1/32 warp efficiency. The payoff: divergence
does the accounting, so control needs no prediction, no speculation, no per-lane
reorder — the scheduler is a ready-list plus a scoreboard, and the transistor budget
goes to lanes and registers.

The **SM (streaming multiprocessor)** is where warps live: a self-contained compute
village, 108 of them on an A100 (Ampere-class). Inside one: the 4 schedulers, 64
FP32 + 64 INT32 lanes, 4 tensor cores, the 64K × 32-bit register file (256 KB), a
192 KB L1/shared pool, constant and texture caches, load/store units — everything
but L2 and HBM, the shared roads between villages. The grid maps *blocks* to SMs;
a block stays pinned to one SM with village-local resources — shared memory (the
block's private scratchpad, invisible outside it) and `__syncthreads()`.

## The tower, rebuilt: registers and the occupancy math

The two towers side by side (GPU numbers Ampere-class, A100 80 GB SXM; CPU side
from module 01 ch02):

```
CPU tower (module 01 ch02)              GPU tower (Ampere-class A100, per SM unless noted)
─────────────────────────────────       ─────────────────────────────────────────────────────
level        size          latency      level        size                  latency      bandwidth
registers    16-32 /core   ~0 cycle     registers    64K x 32-bit / SM     ~1 cycle     --
L1           32-64 KB      ~4           shared mem   <= 164 KB / SM        ~20-30 cyc   ~128 B/cyc/SM
L2 (per core) 0.25-2 MB    ~12                        (one 192 KB pool with L1: YOU pick the split)
L3 (shared)   8-64 MB      ~40           L2           40 MB, all SMs        ~200 cyc     ~5 TB/s
DRAM          8-256 GB     ~200-400     HBM2e        80 GB                 400-800 cyc  2.0 TB/s

same physics (~10x latency steps, SRAM dear, DRAM far), different wiring:
the CPU hides the bottom rows with caches and prediction; the GPU hides them with warps.
```

**Registers first, because they are the scheduling budget.** An Ampere-class SM has
65,536 32-bit registers and hosts at most 2,048 threads (64 warps), registers
allocated per warp with a compute-capability-dependent rounding (64 per warp on the
A100's CC 8.0):

```
regs/thread   regs/warp    warps resident   threads    occupancy
    32           1,024           64           2,048      100%
    64           2,048           32           1,024       50%
   128           4,096           16             512       25%
   255           8,160            8             256      12.5%
```

The last row is the cliff: at 255 registers only 8 warps fit — a 400-cycle load
then needs an independent instruction per 50 cycles from each surviving warp, which
ordinary kernels lack. The register budget *is* the parallelism budget. The knobs
(`__launch_bounds__`, `maxrregcount`) cap the compiler at the price of spills to
"local memory" — per-thread DRAM space cached through L1, despite the name.

## Shared memory: 32 banks and a split you choose

**Shared memory** is `__shared__` SRAM, private to a block, ~20–30 cycles — a
scratchpad: no lines, no tags, no eviction. Per SM (marked): 48 KB (Kepler) →
96 KB (Turing) → 192 KB unified L1/shared pool, ~164 KB shared max (Ampere A100;
~163 KB per block) → 228 KB (Hopper). On Ampere-class, L1 and shared are *one
pool* and you pick the carveout —
`cudaFuncSetAttribute(k, cudaFuncAttributePreferredSharedMemoryCarveout, pct)` plus
`cudaFuncAttributeMaxDynamicSharedMemorySize` beyond the 48 KB static default —
though the driver guesses the split from kernel attributes, and its guess is often
wrong for tile-heavy kernels.

The physics of this level is the **bank**:

```
shared memory = 32 banks x 4 bytes (Ampere-class); one row = 128 bytes

bank:       0     1     2     3   ...  30    31
word 0   [ w0  ][ w1  ][ w2  ][ w3  ]...[ w30 ][ w31 ]
word 1   [ w32 ][ w33 ][ w34 ][ w35 ]...[ w62 ][ w63 ]
            bank(word) = word % 32     (word = byte address / 4)

one pass: all 32 lanes -> 32 distinct banks (or the SAME address: broadcast, free)
conflict: 2+ lanes, different words, same bank -> that many serialized passes
```

Derive which patterns serialize: lane `i` reads word `stride * i`, landing in bank
`(stride * i) mod 32`; all 32 lanes hit distinct banks exactly when
`gcd(stride, 32) = 1` — i.e. when the stride (in 4-byte words) is **odd**:

- **stride 1**: banks 0..31 — one pass; sequential is free. **stride 2, 4, 16**:
  that many lanes per bank, that many passes.
- **stride 32** (a column of a 32×32 float tile): `(32i) mod 32 = 0` for every lane
  — all 32 in bank 0: a **32-way conflict, 32 passes, 32x slower**, zero
  instructions changed.
- **stride 33**: `gcd(33, 32) = 1` — conflict-free. Hence the folklore *pad your
  shared tiles +1*: 32×32 becomes 33×33, column stride 33, conflicts gone for 4%
  wasted SRAM. (A `double` at stride 32 is only a 16-way conflict — 64-bit lanes
  span two banks.)

## L2, HBM, and where the 40x comes from

**L2** is one cache for the whole device — 40 MB on an A100, 50 MB on an H100
(Ampere/Hopper-class) — shared by all 108 SMs, ~200 cycles, and the *coherence
point*: atomics resolve here, every SM's view of memory meets the others' in this
one place (ch02 measures it, ch03 allocates below it) — one shared level, no MESI
problem, no coherence protocol.

**Global memory is HBM**, and the bandwidth arithmetic is the chapter's payoff. A
desktop DIMM (DDR5-6400, one 64-bit channel): 64 pins × 6.4 Gb/s = **51.2 GB/s**.
An A100 80 GB: 5 HBM2e stacks × 1024 pins × 3.2 Gb/s per pin = 5 × 1024 × 3.2 / 8 =
**~2.0 TB/s**. Same formula; the 40x is *width*: 5,120 data pins versus 64 — an
80x interface at about half the per-pin rate (80x × ½ ≈ 40x), affordable because
each HBM stack is 8–12 DRAM dies bonded vertically through silicon (TSVs) onto a
logic die — short wires, low clock, many lanes; five stacks also give 80 GB. Per
generation (marked, SXM-class): V100 0.9 TB/s HBM2, A100 2.0 TB/s HBM2e, H100
3.35 TB/s HBM3, B200 8 TB/s HBM3e. Latency stays 400–800 cycles through every
generation — HBM buys bandwidth, never speed, which only matters to a machine that
hides latency with warps.

Two small caches serve *broadcast-shaped* access. The **constant cache** fronts
64 KB of read-only device memory (`__constant__` — kernel parameters ride it): all
32 lanes reading the *same* address are served in one cycle, one fetch for 32
threads; diverge the addresses and it serializes — so it is for scalars everyone
reads, never per-thread data. The **texture cache** serves the other shape:
spatially local 2D windows with hardware addressing (clamp, filter, interpolate).

## What insiders know

- **The occupancy math is one division**: 65,536 registers per SM ÷ (regs/thread ×
  32) = resident warps — the answer *is* your latency-hiding capacity; 255 regs →
  8 warps → 12.5% (Ampere-class). `nvcc -Xptxas -v` prints the register count per
  kernel before you profile.
- **The SMEM/L1 split is yours**: one 192 KB pool per SM (Ampere-class), ~163 KB
  usable shared *per block* via `cudaFuncAttributeMaxDynamicSharedMemorySize`;
  48 KB is the static default, and the driver's carveout guess is often wrong for
  tile-heavy kernels.
- **Bank rule in one line**: stride (in 4-byte words) odd ⇒ conflict-free
  (`gcd(stride, 32) = 1`); same address ⇒ broadcast, free; stride 32 ⇒ 32-way
  conflict. The fix is padding +1, not reordering.
- **HBM bandwidth per generation** (SXM-class): V100 0.9 TB/s HBM2, A100 2.0 HBM2e,
  H100 3.35 HBM3, B200 8 TB/s HBM3e — each jump is more stacks or pins, not faster
  pins; latency stays 400–800 cycles throughout.
- **The latency-hiding break-even**: 16 warps/scheduler × 25 instructions ≈ 400
  cycles of cover (Ampere). A global load every 8 instructions needs 50
  warps/scheduler — unhideable at *any* occupancy; that ratio is why tiling exists
  (module 12 ch02).
- **"Local memory" is a lie**: spills go to DRAM-backed, L1-cached, per-thread
  slots — a spill-free 255-register compile can be slower than a spilling
  32-register one.

## See it yourself

Labs 1–2 need no GPU — they simulate the memory-model *rules* and print the counts
a profiler would report; Lab 3 needs one (Colab free tier works), marked as such.

**Lab 1 — bank conflicts, simulated.** 32 banks × 4 B, one distinct address per
bank per pass — pure arithmetic:

```python
def shared_passes(stride):
    """One warp reads smem[lane*stride] in 4-byte words; bank(word) = word % 32;
    one pass serves one distinct address per bank."""
    per_bank = {}
    for lane in range(32):
        word = (stride * lane) % (32 * 4096)      # 4096-word scratchpad
        per_bank.setdefault(word % 32, set()).add(word)
    return max(len(s) for s in per_bank.values())  # worst-lane serialization

for s in [0, 1, 2, 4, 16, 31, 32, 33]:
    print(f"stride {s:2d} words -> {shared_passes(s):2d} pass(es)")
```

```text
stride  0 words ->  1 pass(es)   broadcast: same address, free
stride  1 words ->  1            banks 0..31, all distinct
stride  2 words ->  2
stride  4 words ->  4
stride 16 words -> 16
stride 31 words ->  1            gcd(31,32)=1
stride 32 words -> 32            <-- the pathology: all lanes, bank 0
stride 33 words ->  1            gcd(33,32)=1: the +1 pad fix
```

**Lab 2 — the latency-hiding arithmetic, worked:**

```python
# Ampere-class (A100): 16 resident warps/scheduler, 1 warp-inst/cycle each.
LEVELS = [("shared mem", 25), ("L2", 200), ("global (HBM)", 500)]
for name, lat in LEVELS:
    for ratio in (8, 32):              # one long-latency load per N instructions
        warps = lat / ratio
        verdict = "yes" if warps <= 16 else "NO -- unhideable at this ratio"
        print(f"{name:14s} {lat:3d} cyc, 1 load per {ratio:2d} inst "
              f"-> {warps:5.1f} warps/scheduler  {verdict}")
```

```text
L2             200 cyc, 1 load per  8 inst ->  25.0 warps/scheduler  NO -- unhideable at this ratio
global (HBM)   500 cyc, 1 load per  8 inst ->  62.5 warps/scheduler  NO -- unhideable at this ratio
global (HBM)   500 cyc, 1 load per 32 inst ->  15.6 warps/scheduler  yes
```

The verdict rows are a design instruction: a global load every 8 instructions is
unhideable even at 100% occupancy — HBM loads must be 1-in-32 events, with shared
or L1 in between. That is what tiling buys (ch02).

**Lab 3 — the real thing (needs a GPU).** Ask the hardware for this chapter's
numbers:

```console
$ nvidia-smi --query-gpu=name,memory.type,memory.total --format=csv
name, memory.type, memory.total [MiB]
Tesla T4, GDDR6, 15109 MiB              # Colab free tier (Turing-class)

$ ./deviceQuery | grep -iE "warp|register|shared|L2|global"    # CUDA samples
  Total number of registers per SM:           65536
  Total shared memory per SM:                 65536 B   # T4; A100 reports ~164 KB of its 192 KB pool
  L2 cache size:                              6291456 B # 6 MB; A100 reports 40 MB
```

A T4 reports smaller (Turing-class) numbers — same shape per row, hence the
generation marks. Then your register count:

```console
$ nvcc -Xptxas -v -arch=sm_75 transpose.cu
ptxas info : Compiling entry function '_Z9transposePfS_ii' for 'sm_75'
ptxas info : Used 24 registers, 0 stack, 8192 bytes smem, 368 bytes cmem
```

`24 registers` → 768 regs/warp → 65,536 / 768 = 85 warps by registers alone; the
8,192 B of shared memory per block caps blocks per SM here — two ptxas lines are
the occupancy calculator in miniature.

## How to use this knowledge

- **Kernel triage, in order**: `nvcc -Xptxas -v` → the 65,536 division → warps
  resident → the latency table → *then* blame the algorithm. Most "the GPU is slow
  at my code" incidents end at step two: 12.5% occupancy from a 255-register
  compile.
- **Launch-failure arithmetic**: "too many resources requested for launch" means a
  block asked for more shared memory or threads than an SM has — computable from
  `deviceQuery` numbers.
- **Code-review reflexes**: pad shared tiles +1 (the stride-32 disease); grid-wide
  read-only scalars belong in `__constant__`; per-thread arrays indexed by
  `threadIdx` belong in registers, not local memory.
- **Interview answers that derive instead of recite**: where HBM's 40x comes from
  (width, not pin speed), why occupancy is one division, why divergence serializes
  and what the trade bought.

## Connections

- **Module 01 ch01** — the silicon physics (SRAM area cost, DRAM precharge) sizing
  every level of this tower.
- **Module 01 ch02** — the CPU tower beside this one; its "latency is not
  throughput" bullet is the design inversion, made literal.
- **Module 12 ch02** — coalescing is the cache-line story at warp scale; the
  roofline uses the 2.0 TB/s and occupancy numbers derived here.
- **Module 12 ch03** — the allocator that fills the HBM level; the real anatomy of
  "out of memory".
- **Module 12 ch04** — unified memory: the tower grows network levels (NVLink,
  CXL, RDMA).
- **Module 10 ch05** — the ABI divergence: PTX's 255 flat registers, no SysV
  register table, no per-thread stack.
- **Module 07 ch06** — Apple's unified memory as the integrated pole; ch04 takes
  it fully.

## Quiz

1. **Concept.** A modern CPU core spends hundreds of thousands of transistors per
   thread on out-of-order machinery. From the latency-hiding arithmetic, why can a
   GPU delete all of it — and what must it hold instead?
2. **Concept.** What does "the warp diverges" cost, why is the cost called
   *serialization*, and what design payoff makes it a deliberate trade?
3. **Predict the number (occupancy).** A kernel compiles to 64 registers per thread
   on an Ampere-class SM (65,536 32-bit registers, 2,048-thread maximum). How many
   warps are resident, and what fraction of maximum occupancy? Same two numbers at
   128 registers.
4. **Predict the number (banks).** A warp reads `smem[lane * 16]` (32-bit
   elements): how many serialized passes? And `smem[lane * 17]`? Justify with the
   gcd rule.
5. **Predict the number (bandwidth).** Compute the bandwidth of one 64-bit
   DDR5-6400 channel and of an A100 (5 HBM2e stacks × 1024 pins at 3.2 Gb/s/pin);
   the ratio, and where it comes from.
6. **Bug diagnosis.** A team "optimized" a shared-memory tile from 33×33 to 32×32
   floats to reclaim the 4% wasted SRAM. The kernel slowed ~30x with identical
   instruction counts. What happened?
7. **Bug diagnosis.** `__launch_bounds__` caps a kernel at 32 registers per thread;
   ptxas now reports "spills to local memory" — yet the kernel gets *faster*.
   Explain with the occupancy math and what "local memory" actually is.
8. **Cross-chapter.** Module 01 ch02's insiders say "latency is not throughput" for
   CPUs: outstanding misses pipelined by the memory system. State the GPU's version,
   name the two hardware structures supplying the outstanding work, and say which
   one module 12 ch02 measures.

<details>
<summary>Answers</summary>

1. The OoO window finds independent work *within one thread*; a GPU finds it
   *across threads*: warps needed ≥ latency / instructions-between-loads — 400/25 =
   16 per scheduler, exactly Ampere's maximum, so 64 warps per SM out-stall any
   reorder window. It holds the per-warp state instead: the 64K × 32-bit register
   file keeps every stalled warp resident — there is nowhere else to park it.
2. The 32 lanes share one PC, so a both-sides `if` issues two instruction streams —
   each path with the other's lanes masked, reconverging at the merge point:
   serialization. The payoff: no branch prediction, no speculation, no per-lane
   control state; the scheduler is a ready-list and the transistor budget moves to
   lanes and registers — the programmer pays for irregular control flow so the
   hardware does not.
3. 64 regs → 2,048 registers per warp → 65,536 / 2,048 = 32 warps = 1,024 threads =
   **50% of the 64-warp maximum**. At 128 regs: 4,096 per warp → **16 warps, 25%**.
   Doubling per-thread state halves the threads that fit — the register file is the
   scheduling budget.
4. Stride 16: `(16·lane) mod 32` is 0 for even lanes, 16 for odd — 16 lanes on each
   of two banks: **16 passes**. Stride 17: `gcd(17,32)=1`, all banks distinct:
   **1 pass**. Odd strides are conflict-free.
5. DIMM: 64 × 6.4 Gb/s / 8 = **51.2 GB/s**. A100: 5 × 1024 × 3.2 / 8 = **~2.0
   TB/s**. Ratio 40x, from *width*: 5,120 data pins vs 64 (80x the interface) at
   about half the per-pin rate — affordable because HBM stacks are short-wired,
   vertically bonded dies, not DIMM traces.
6. The 32×32 tile made the column stride exactly 32 words: every lane reading a
   column lands on `(32·lane) mod 32 = 0` — all 32 on bank 0, 32 serialized passes
   per access instead of 1. The 33-wide tile (stride 33, coprime with 32) was
   conflict-free; the "wasted" 4% was buying 32x on every column read — module 01
   ch02's row-major/column-major lesson, replayed in SRAM.
7. Occupancy rose: 32 regs → 64 resident warps (100%) vs 8 (12.5%). "Local memory"
   is per-thread DRAM space cached through L1, so a spill costs one warp a
   400–800-cycle event — but 16 warps/scheduler × 25 instructions now cover every
   400-cycle load in the kernel. Latency-hiding capacity beat per-thread latency;
   invisible without the 65,536 division.
8. "Latency is not throughput — provided enough warps are resident": throughput is
   warps × issue rate, independent of each load's 400–800 cycles. The outstanding
   work is supplied by the **register file** (every resident warp's context held
   across its stall) and the **scheduler's ready-list/scoreboard**. Module 12 ch02
   measures the bytes side — whether the in-flight warps use the 2.0 TB/s at all
   (coalescing, roofline).

</details>
