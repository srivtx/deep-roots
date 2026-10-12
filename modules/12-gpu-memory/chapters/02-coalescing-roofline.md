# Chapter 02 — Coalescing & the roofline

> **The 30-second version.** A kernel is slow or fast because of *traffic*,
> not instructions. A warp's (32 threads') load is rounded up to 32-byte
> **sectors**: a stride-1 float load moves 128 bytes in 4 sectors; a stride-4
> load moves the same 128 useful bytes in 16 sectors — 4x the traffic for
> identical math. Coalescing is module (01:02)'s cache-line story at warp
> scale: it decides layout (SoA beats AoS), tiling (shared memory buys
> arithmetic intensity), and — via the roofline's one division, peak-FLOPS /
> bandwidth — whether a GPU accelerates your kernel at all.

## One warp, one instruction, thirty-two addresses

SIMT (Chapter 01): 32 threads execute *one* load instruction — naively 32
separate addresses, absurd for a DRAM path that moves bursts, not crumbs.
Between warp and L1 sits a **coalescer**: it merges this instruction's
addresses that fall in the same 32-byte **sector** (the L1/DRAM granularity on
every CUDA GPU since Kepler, CC 3.5; 4 sectors per 128-byte line,
Ampere-class). One merged 128-byte wavefront = one **transaction**.

Derive the best case: one `float` per thread, contiguous, base aligned —
addresses `0, 4, ..., 124`, payload 128 bytes, exactly 4 sectors: a
*perfectly coalesced* load:

```
 warp loads a[tid], one 4-byte float each, stride 1, base aligned:

   thread    0    1    2    3    4    5    6    7         8  ...  31
   address   0    4    8   12   16   20   24   28        32  ... 124
             |<------- sector 0, 32 B ------->|<--- sector 1 --->| ...
             |<-------- 128 B = 4 sectors = 1 transaction -------->|

 same warp, element stride 4 (a[4*tid]): addresses 0, 16, 32, ..., 496 —
 every 32 B sector now holds two threads' 4-byte floats: 8 B used, 24 dead.
 16 sectors: 512 B moved, 128 B useful = 25%.
```

The stride multiplies the address *span*, and the coalescer pays for every
sector the span crosses. One warp-wide f4 (4-byte) load, element stride S:

| element stride | span | sectors | transactions | payload | efficiency |
|---|---|---|---|---|---|
| 1 | 128 B | 4 | 1 | 128 B | 100% |
| 2 | 252 B | 8 | 2 | 128 B | 50% |
| 4 | 500 B | 16 | 4 | 128 B | 25% |
| 8 | 996 B | 32 | 8 | 128 B | 12.5% |

The law: **DRAM traffic = 32 bytes x sectors touched.** Stride 8 puts each
thread in its own sector — 8x the traffic of stride 1 for the same 128 useful
bytes and instruction count.

Two refinements. **Misalignment:** a base offset by 4 bytes (sliced one
element off the front) shifts the stride-1 addresses to `4..131` — 5 sector
boundaries: +1 sector, +25% traffic, forever. `cudaMalloc` returns
256-byte-aligned pointers; *views and offsets* misalign you. **Why one f4
load beats four f1 loads:** assembling a float from an interleaved byte array
(`b[4*tid + j]`), each warp-wide 1-byte instruction spreads 32 addresses
across a ~125 B span — 4 sectors — for 32 useful bytes; four instructions =
16 sectors vs the f4 load's 4. Same payload, 4x traffic: the coalescer merges
*within one instruction*, never across. (Four f1 loads of 32 *contiguous*
bytes each touch just 4 sectors — the tax is per-instruction address spread.)

## The layout decision is a memory decision

A particle system, two layouts. **Array of structs** (AoS):

```c
struct Particle { float x, y, z, w; };   // warp reads p[tid].x -> 16*tid
Particle p[N];
```

The warp wants only `.x` — a 16-byte stride, the stride-4 row scaled by the
struct: 16 sectors, 4 transactions, 25% efficiency. You haul `.y`, `.z`, `.w`
past the memory system and never use them. **Struct of arrays** (SoA):

```c
float px[N];                              // warp reads px[tid] -> 4*tid
```

4 sectors, 1 transaction, 100%.

Module (01:06)'s layout decision, re-judged: a CPU's consumer is one core and
the atom is the 64-byte line (AoS wins for whole-struct access); a GPU's
consumer is a *warp* — 32 threads wanting the same field of 32 consecutive
elements — so field-contiguous layout wins by struct-size / 4. If the kernel
touches *all* fields, L1 (192 KB/SM, Ampere-class) caches the siblings and
DRAM bytes equalize — but each instruction still *requests* 16 sectors, and
the single-field case has no defense. SoA, always.

## Tiling: buying arithmetic intensity with shared memory

Coalescing fixed how bytes move; tiling fixes how many are needed. Canonical
case: matrix multiply. Naive `C = A*B` — each thread computes one output
element, `acc += A[row*N+k] * B[k*N+col]` — pulls 2 floats from global per
FMA (2 FLOP), nothing reused: AI = 2 FLOP / 8 B = **0.25 FLOP/byte**.

Tile it: each block owns a 16x16 output tile, stages 16x16 tiles of A and B
into **shared memory** cooperatively, computes from shared:

```c
__global__ void tiled16(const float* A, const float* B, float* C, int N) {
    __shared__ float a[16][16+1], b[16][16+1];       // +1 word: bank pad, below
    int row = 16*blockIdx.y + threadIdx.y, col = 16*blockIdx.x + threadIdx.x;
    float acc = 0.f;
    for (int k = 0; k < N; k += 16) {
        a[threadIdx.y][threadIdx.x] = A[row*N + k + threadIdx.x]; // stride-1 warp
        b[threadIdx.y][threadIdx.x] = B[(k+threadIdx.y)*N + col]; // stride-1 warp
        __syncthreads();
        for (int i = 0; i < 16; i++)
            acc += a[threadIdx.y][i] * b[i][threadIdx.x];         // SMEM, not HBM
        __syncthreads();
    }
    C[row*N + col] = acc;
}
```

Both staging loads are coalesced (consecutive `threadIdx.x` = consecutive
addresses). The arithmetic: per k-step the block loads 2 x 16 x 16 floats =
2,048 bytes and does 256 threads x 16 = 4,096 FMAs — **0.5 bytes per FMA**;
each staged byte is reused by the 16 threads sharing its row or column. For
a T x T tile: bytes per FMA = 8/T, i.e. AI = T/4:

| tile T | bytes per FMA | AI (FLOP/byte) | A100 attainable (2.0 TB/s) |
|---|---|---|---|
| none | 8 | 0.25 | 0.5 TFLOP/s |
| 16 | 0.5 | 4 | 8.0 TFLOP/s |
| 32 | 0.25 | 8 | 16.0 TFLOP/s |
| 64 | 0.125 | 16 | 19.5 TFLOP/s = peak |

Tiling converts HBM traffic (~500-cycle round trips, Chapter 01) into shared
memory traffic (~30 cycles) by amortizing one load over T reuses.

One trap lives in staging. Shared memory is 32 banks x 4 bytes; a warp
reading one word per thread is conflict-free only if the 32 addresses hit 32
distinct banks. A column walk of a square `[32][32]` tile is a 32-word-stride
walk: `32*i mod 32 = 0` — all threads hit bank 0, serialized into 32 rounds.
Pad the row by one word (~3% more shared memory) and columns walk at stride
33: `33*i mod 32 = i` — every thread its own bank, one round instead of 32,
for ~3% more shared memory. (Trap: padding by four words, `[32][36]`, gives
`36*i mod 32 = 4i` — 4-way conflicts return.)

## The roofline: is the GPU even helping?

The **roofline model** turns traffic accounting into one verdict. A kernel's
AI = FLOP executed / bytes moved from DRAM; the machine offers two ceilings —
HBM bandwidth (A100 80GB SXM: 2.0 TB/s) and peak FP32 (19.5 TFLOP/s
non-tensor) — and a kernel cannot exceed either:

```
 attainable FLOP/s = min( AI x 2.0 TB/s , 19.5 TFLOP/s )
```

The lines cross at the **ridge point** = 19.5 / 2.0 = **9.75 FLOP/byte**.
Left of it the FLOPs idle and the kernel is *memory-bound* — speed is
bandwidth / bytes, full stop. Right of it the kernel is *compute-bound* and
traffic no longer matters. Place three kernels (A100 80GB SXM, FP32): **K1, saxpy** — 2 FLOP / 12 B
(read x, read y, write y) — AI 0.17, 0.33 TFLOP/s = **1.7% of peak**; **K2,
naive matmul** — AI 0.25, 0.5 TFLOP/s, 2.6% of peak; **K3, tiled T=64** —
AI 16, capped at 19.5 TFLOP/s, compute-bound.

```
 attainable FP32 TFLOP/s (log scale)
 19.5 |                              .------------------.  K3
      |                         .----'                 tiled T=64, AI=16:
      |                    .----'                       FLOPs are the ceiling
      |               .----'
  8.0 |          .---'   <-- K3' tiled T=32, AI=8: 20% short of the ridge
  4.0 |      .---'
      |   .---'        slope = 2.0 TB/s: attainable = AI x BW
  0.5 |.--'  K2 = naive matmul, AI=0.25
 0.33 |  K1 = saxpy (y = a*x+y), AI = 2 FLOP / 12 B = 0.17
      +----+----+----+----+----+----+----+----+----+--->  arithmetic intensity
        0.17 0.25 0.5   1    2    4    8  9.75  16      (FLOP/byte, log)
                                     ^
                             ridge = 19.5 / 2.0 = 9.75
```

- **K1, saxpy** (diagram): 0.33 TFLOP/s of 19.5 = 1.7% of peak — a
  bandwidth-powered crawl.

So "the GPU, the accelerator" needs a qualifier. A memory-bound kernel can
never beat a CPU by more than the *bandwidth ratio* (2.0 TB/s vs ~50-100 GB/s
desktop DRAM, ~20-40x). A compute-dense kernel wins by the FLOP ratio — and
with tensor cores (A100 FP16: 312 TFLOP/s) the ridge is 312/2.0 = 156
FLOP/byte, almost nothing is compute-bound, and kernel engineering becomes
data staging. That is why shared memory, `cp.async`, and TMA exist.

## Occupancy: latency-hiding as a budget, and the async pipeline

Even the memory-bound ceiling needs work in flight: a global load takes
~400-800 cycles (Chapter 01) and the issuing warp stalls, so the SM needs
other resident warps to issue from. Occupancy is the *capacity* for latency
hiding — not speed itself.

The budget is three divisions. Take `tiled16`: 256 threads/block (8 warps),
40 registers/thread (`nvcc -Xptxas -v`), ~2.2 KB shared/block (two `[16][17]`
tiles). Per SM, Ampere-class A100: 65,536 32-bit registers, 64 warp slots,
and the shared-memory carveout you chose (say 64 KB of the 192 KB L1/SMEM
pool):

```
 registers : 65536 / (40 x 256) = 6.4  -> 6 blocks
 warp slots: 64 / 8                = 8 blocks
 shared   : 65536 / 2200          = 30 blocks
 limit = min = 6 blocks = 48 warps of 64 = 75% occupancy
```

That is all `cudaOccupancyMaxActiveBlocksPerMultiprocessor` does — the same
three divisions, by hand. Pathology at the end: 255 registers/thread with
32-thread blocks gives 65536/(255 x 32) = 8 blocks x 1 warp = 12.5%
occupancy ("the register budget is the scheduling budget," Chapter 01).
Middle trap: `__launch_bounds__` forcing registers down raises occupancy but
spills to *local memory* — DRAM-backed traffic.

Finally, **async copy** (`cp.async`, CC 8.0+/Ampere). The staging loop
`gmem -> registers -> shared` parks each tile's bytes in warp registers while
the copy completes, serializing load and compute. `cp.async` moves 4, 8, or
16 bytes per thread global-to-shared *directly*, registers untouched — so you
can double-buffer: while the block computes tile k, the copy engine fills
tile k+1 (`cp.async.wait_group` + `__syncthreads()` gates the swap). Latency
is hidden *structurally* (by overlap), not *statistically* (by occupancy),
and the ~16 registers a staging path consumed are freed — often worth more
than the overlap. Hopper (CC 9.0) generalizes it to TMA.

## What insiders know

- **The sector, not the byte, is the unit.** Traffic is charged in 32-byte
  sectors (CUDA since CC 3.5; 4 per 128 B line, Ampere). A 1-byte read and a
  32-byte read from one sector cost identical DRAM traffic: "bytes moved"
  means `32 x sectors`, never payload.
- **The stride table lives in hardware counters.** `ncu --metrics
  l1tex__t_sectors_pipe_lsu_mem_global_op_ld.sum` counts sectors;
  `l1tex__average_t_sectors_per_request_pipe_lsu_mem_global_op_ld.ratio` is
  the table above: 4.0 = perfectly coalesced f4, 32.0 = one thread per sector.
- **Ridge arithmetic per generation (FP32, SXM):** V100 15.7/0.9 = 17.4,
  A100-80GB 19.5/2.0 = 9.75 (12.6 on the 40GB's 1.55 TB/s), H100 51/3.35 =
  15.2. Against tensor cores (A100 FP16: 312 TFLOP/s) the ridge is 156 —
  post-Ampere kernel work is staging, not math.
- **SoA at framework scale: channels-last.** NHWC puts C innermost so a
  warp's channel loads are contiguous; `torch.channels_last` is a coalescing
  decision, not a storage preference (NCHW makes C a strided walk).
- **cp.async's minimum quantum is 4 bytes per thread** (4, 8, or 16). You
  cannot async-copy a byte, and the win is two-fold: overlap *and* registers
  freed by deleting the LDG/STS round trip — measure the register savings
  first.

## See it yourself

Labs 1-2 are CPU-side integer arithmetic; lab 3 needs a GPU.

**Lab 1 — the stride table, computed.** Distinct 32-byte sectors for strides
1/2/4/8, plus the misalignment tax:

```bash
python3 - <<'EOF'
import math
WARP, ES = 32, 4                       # 32 threads, 4-byte float loads
def sectors(stride, offset=0):
    addrs = [offset + t * ES * stride for t in range(WARP)]
    return len({a >> 5 for a in addrs})     # a // 32 = sector id
print("stride  sectors  transactions  bytes-touched  payload  efficiency")
for s in (1, 2, 4, 8):
    sec = sectors(s); tr = math.ceil(sec / 4)
    print(f"{s:6d}  {sec:7d}  {tr:12d}  {sec*32:12d}  {128:7d}  "
          f"{100*128/(sec*32):6.1f}%")
EOF
```

Output (reproduced):

```
stride  sectors  transactions  bytes-touched  payload  efficiency
     1        4             1           128      128   100.0%
     2        8             2           256      128    50.0%
     4       16             4           512      128    25.0%
     8       32             8          1024      128    12.5%
```

The efficiency column is why a stride-4 kernel runs 4x slower on identical
instructions.

**Lab 2 — the roofline for three toy kernels** (swap `PEAK/BW` for your GPU):

```bash
python3 - <<'EOF'
PEAK, BW = 19.5, 2.0                   # A100 80GB SXM: TFLOP/s, TB/s
print(f"ridge = {PEAK}/{BW} = {PEAK/BW:.2f} FLOP/byte\n")
for name, fl, by in [("saxpy", 2, 12), ("naive matmul", 2, 8),
                     ("tiled matmul T=64", 2*64*64, 8*64)]:
    ai = fl / by; att = min(ai * BW, PEAK)
    print(f"{name:20s} AI={ai:6.2f}  attainable={att:6.2f} TFLOP/s  "
          f"{'memory-bound' if ai < PEAK/BW else 'compute-bound'}")
EOF
```

Output (reproduced):

```
ridge = 19.5/2.0 = 9.75 FLOP/byte

saxpy               AI=  0.17  attainable=  0.33 TFLOP/s  memory-bound
naive matmul        AI=  0.25  attainable=  0.50 TFLOP/s  memory-bound
tiled matmul T=64   AI= 16.00  attainable= 19.50 TFLOP/s  compute-bound
```

For a picture: the same three (AI, attainable) pairs on log-log axes
(`plt.loglog`), a bandwidth-slope line, and a `hlines(PEAK)` ceiling.

**Lab 3 — if a GPU is available (Ampere-class; Colab free tier works).**
Marked: needs hardware; not run in the sandbox this chapter was written in —
numbers below are the arithmetic's predictions, not measurements.

```bash
# stride sweep: kernel does v[tid] = a[tid*S] for S in 1,2,4,8; time each
nvcc -O3 -arch=sm_80 stride_sweep.cu -o stride_sweep && ./stride_sweep
# expect wall time ~ 1 : 2 : 4 : 8 (DRAM-resident array: traffic-bound)
ncu --metrics l1tex__t_sectors_pipe_lsu_mem_global_op_ld.sum \
     l1tex__average_t_sectors_per_request_pipe_lsu_mem_global_op_ld.ratio \
     ./stride_sweep        # expect sectors/request = 4, 8, 16, 32

# tiled-vs-naive matmul (tiled16 above vs a naive kernel):
nvcc -O3 -arch=sm_80 matmul.cu -o matmul && ./matmul
# expect ~10x+ at N=4096 (AI 0.25 -> 4 climbs the slope)
```

If sectors-per-request reads 4/8/16/32, you have measured Lab 1's table in
hardware; a 4.0 turning into 5.0 means misalignment or L2 hits.

## How to use this knowledge

- **Two-minute triage of a slow kernel.** Count FLOP and DRAM bytes in the
  loop body, divide, compare to the ridge (9.75, A100 FP32). AI 0.3 =
  memory-bound — math tuning cannot help, fix traffic. AI 15 = compute-bound
  — stop reorganizing memory.
- **Read `ncu`'s sectors-per-request first.** 4.0 = coalesced; 32.0 = one
  thread per sector (a stride bug); 5.0 = misaligned base. The number names
  the fix: indexing, layout, or offset.
- **Choose SoA for anything warp-consumed.** Feature tables, particles,
  channels: the 4x sector bill for AoS single-field access is decided once,
  paid every launch.
- **Explain a regression after an unroll.** `nvcc -Xptxas -v` shows registers
  40 -> 72, occupancy 75% -> 50%: spills to local memory (DRAM-backed) or
  lost latency hiding — you can now name which.
- **Answer "will a GPU help?" with one division.** Left of the ridge, the
  ceiling is the bandwidth ratio (~20-40x over a desktop); right of it, the
  FLOP ratio (~100x with tensor cores). SAXPY-like pipelines never become
  "AI accelerators."

## Connections

- **Module (01:02), the cache line** — the payoff: coalescing *is* cache-line
  behavior; the sector is the warp's line granularity, and the stride-4 25%
  is the same arithmetic as 4-bytes-of-64 on a CPU.
- **Module (01:06), object layout** — SoA-vs-AoS re-judged by a 32-thread
  consumer; struct padding and alignment rules (module (10:05)'s ABI view)
  are why those strides exist.
- **Module (12:01), the hierarchy** — the latency table (shared ~30, HBM
  ~400-800 cycles) and the register budget behind the occupancy arithmetic.
- **Module (12:03), allocation & OOM** — the misaligned views that cost the
  5th sector (or the float4 fault) come from slicing allocator-returned
  blocks; the caching allocator's offsets are an alignment actor.
- **Module (12:04), unified memory** — the roofline with the other machine's
  numbers: an integrated M4 Max (~546 GB/s) moves the ridge and shrinks the
  memory-bound ceiling ~4x; compute-dense kernels don't care.

## Quiz

1. A warp executes `float v = a[tid * 8];` — one f4 load per thread, element
   stride 8, base aligned. Derive sectors, transactions (128 B units),
   payload, efficiency; describe the access shape.
2. `struct P { float x, y, z, w; };` — a warp loads `p[tid].x` from an AoS
   array. Sectors and transactions, versus SoA `xs[tid]`? If the kernel loads
   `.y`, `.z`, `.w` too, does the AoS DRAM bill stay 4x?
3. An activation kernel reads one float, writes one float, does 20 FLOP of
   `tanh`-ish math per element. A teammate calls it compute-bound. Place it
   on the A100 FP32 roofline: AI, attainable FLOP/s, percent of peak,
   verdict.
4. A kernel ran for months on scalar `float` loads. Someone switches it to
   `float4` over a tensor that is a *view* starting one element in (`x[1:]`
   reinterpreted); it now dies instantly with a CUDA "misaligned address"
   error. Diagnose the condition and the fix.
5. A shared-memory transpose is barely faster than the naive version. The
   tile is `__shared__ float t[32][32];`, written `t[threadIdx.x][threadIdx.y]`
   and read back column-wise. Pathology and one-line fix?
6. Autotuning a matmul raised registers/thread 40 -> 72 (256-thread blocks,
   A100): occupancy fell 75% -> 50%, yet the kernel got 12% *faster*. Why
   didn't lower occupancy mean lower speed?
7. Moving the same 128 bytes per warp: one f4 load touches 4 sectors, but
   four f1 loads with bytes interleaved at 4-byte spacing touch 16. State the
   invariant, and a layout where four f1 loads touch only 4 sectors.
8. Cross to module (01:02): a CPU walks a `float` array with element stride
   4. What fraction of each 64-byte cache line is used, which number in this
   chapter's warp-side table is the same fraction, and why is it the *same*
   number?

<details>
<summary>Answers</summary>

1. **32 sectors, 8 transactions, 128 B payload, 12.5%.** Addresses
   `0, 32, ..., 992` — each thread's 4 bytes in its own sector (span 996 B
   -> 32 sectors = 8 x 128 B transactions). One thread per sector: the worst
   coalescing, 8x stride-1 traffic for identical math.
2. **16 sectors / 4 transactions vs 4 sectors / 1.** `.x` at a 16-byte
   stride spans 500 B -> 16 sectors, 25%; SoA is `4*tid` -> 4 sectors, 100%.
   With all four fields loaded, the DRAM bill does *not* stay 4x: the sibling
   instructions hit the same 16 sectors, which L1 (192 KB/SM) serves as hits.
   What stays 4x is the per-instruction request count — and the single-field
   case (12 of 16 bytes paid for, never used) is why SoA wins.
3. **AI = 20 FLOP / 8 B = 2.5 FLOP/byte — memory-bound.** Attainable =
   2.5 x 2.0 TB/s = 5.0 TFLOP/s = 26% of peak. "Many FLOP per element" is not
   high AI — the roofline divides by the 8 B, and 2.5 < the 9.75 ridge.
   Cheaper tanh changes nothing; halving bytes doubles speed.
4. **16-byte alignment is a hardware precondition for 16-byte vector
   loads.** The view's base is allocation base + 4 B, so `float4` addresses
   are 4 mod 16; the load unit refuses and the fault kills the whole kernel
   (no partial execution). Fix: keep scalar loads (a stride-1 f4 load is
   already 4 sectors — the optimum; float4 buys issue bandwidth, not
   traffic), copy into an aligned buffer, or vector-load the aligned
   remainder. Silent variant: a 4-byte-offset scalar path costs the 5th sector.
5. **A 32-way bank conflict: 32 serialized rounds per warp access.**
   32 banks x 4 B; the column read is a 32-word-stride walk, `32*i mod 32 =
   0` — all threads hit bank 0. Fix: `__shared__ float t[32][33];` — stride
   33 gives `33*i mod 32 = i`, a bank per thread, one round, ~3% shared-memory
   cost. (Trap: `[32][36]` gives `36*i mod 32 = 4i` — 4-way conflicts.)
6. **Occupancy is latency-hiding capacity, not throughput.** 50% is 32
   resident warps per SM — enough to cover ~500-cycle HBM latency for this
   kernel's mix. The extra registers bought per-warp ILP (deeper unrolling
   kept values in registers instead of re-loading), and the kernel's high AI
   means FLOP issue, not latency, is the ceiling. Occupancy hurts only when
   warps starve the memory pipe.
7. **Invariant: the coalescer merges only addresses from one instruction,
   and charges 32 B per sector any address touches.** Four f1 instructions
   each spread 32 one-byte addresses across a ~125 B span — 4 sectors each,
   16 total; the f4 instruction's 32 addresses pack one 128 B span — 4
   sectors. Cheap f1 layout: 32 *contiguous* bytes per instruction (a planar
   array) — 1 sector each, 4 total.
8. **16 of 64 bytes = 25% — the stride-4 row: 128 B useful of 512 B
   touched.** Both machines round traffic up to the transfer granularity:
   the CPU's 64 B line, the GPU's 32 B sector. The address pattern is
   machine-independent; only the granularity changes — coalescing *is*
   cache-line behavior, the chapter's thesis in one number.
</details>
