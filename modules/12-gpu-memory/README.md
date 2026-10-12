# Module 12 — GPU & AI accelerator memory

> The tower gets a new wing: registers by the hundred thousand, shared memory
> you budget by hand, HBM stacks with bandwidth that dwarfs DRAM, and an
> allocator that lies to you about what "out of memory" means. GPU memory is
> module 01 chapter 02's hierarchy with the numbers changed and the rules
> rewritten — coalescing is cache lines, occupancy is the hierarchy in
> scheduling form, and CUDA OOM is a fragmentation story you already know.

**4 chapters · one final exam · prerequisite: module 01 chapters 01 (silicon),
02 (the hierarchy — mandatory), 03 (page tables — reused at GPU scale), 19
(allocators); module 10 ch05 (ABI) is referenced once.**

## How to study it

Chapter 01 builds the hardware hierarchy; 02 is the performance model
(coalescing, tiling, roofline); 03 is allocation and the real anatomy of
"CUDA out of memory"; 04 is unified memory, Apple Silicon, and where the
tower is heading. Labs: any CUDA-capable GPU (Colab free tier works) or —
for ch01/02 — a CPU-side simulation of the memory-model rules; numbers are
marked per-GPU generation.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The GPU memory hierarchy](chapters/01-gpu-hierarchy.md) | Registers, shared memory, L2, HBM — SIMT and the bandwidth-first design. |
| 02 | [Coalescing & the roofline](chapters/02-coalescing-roofline.md) | Warp memory access as cache-line physics; tiling; compute-vs-memory bound. |
| 03 | [Allocation & the real OOM](chapters/03-allocation-oom.md) | GMMU, the caching allocator, fragmentation — why free ≥ request still fails. |
| 04 | [Unified memory & the future](chapters/04-unified-future.md) | Managed memory, Apple UMA, CXL — memory as a network endpoint. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the GPU memory hierarchy.** The design inversion: CPUs optimize
*latency* (big caches, branch prediction, out-of-order), GPUs optimize
*throughput* (tiny per-thread state, massive thread-level parallelism to hide
latency — derive: if a load takes 400 cycles, keep ~32 warps in flight so
someone is always computing; the latency-hiding arithmetic). SIMT: the warp
(32 threads executing one instruction stream; divergence serializes — the
payoff of the control-flow simplicity), the SM (streaming multiprocessor) as
a self-contained compute village. The hierarchy bottom-up, with numbers per
generation (mark them: e.g., Ampere-class): registers (up to 255/thread,
64K 32-bit registers per SM — the occupancy math: 255 regs × 32 threads
means fewer resident warps — the register budget *is* the scheduling
budget), shared memory (on-chip SRAM, 48-228KB per SM, configurable trade
against L1 capacity — you choose the split with `cudaFuncSetAttribute`;
bank conflicts: 32 banks × 4 bytes, the stride-32 access pathology derived —
which access patterns serialize and why), L2 (40-50MB, device-wide — the
coherence point), global memory (HBM: the bandwidth story — A100 2.0TB/s,
H100 3.35TB/s HBM3 vs a desktop DRAM's ~50GB/s: where the 40x comes from —
many stacked dies × wide interfaces), constant/texture cache roles (the
broadcast access pattern each serves). The latency table (registers ~1
cycle, shared ~20-30, L2 ~200, global ~400-800 — the GPU's own tower next
to module 01 ch02's: same physics, different wiring). Lab: the classic
shared-vs-global matrix transpose timing; a bank-conflict reproduction
(strided vs padded shared access).

**Chapter 02 — coalescing & the roofline.** Coalescing = cache-line behavior
at warp scale, derived: a warp issues memory requests per *thread*; the MMU
coalesces addresses that fall in the same sector — the granularity insider
fact: **32-byte sectors**, 4 per 128B cache line; a fully-coalesced f4 load
(32 threads × 4B contiguous = 128B = 4 sectors = 1 transaction); derive the
transaction count for stride 1/2/4 (the arithmetic in a table — the chapter's
first payoff), misalignment penalties, and why f4 beats 4× f1 (fewer sectors
per warp). SoA vs AoS derived (a struct-of-arrays float load = perfectly
coalesced; array-of-structs stride-4 load = 4x transactions — the layout
decision *is* a memory-hierarchy decision, module 01 ch06 at warp scale).
Shared-memory tiling: the canonical tiled matmul (block into 16×16 tiles,
cooperative load into shared, compute from shared — the arithmetic-intensity
rise derived: naive matmul = O(N³) loads per O(N³) FMAs... the per-FMA byte
count with and without tiling), the bank-conflict-aware tile layout
(padding to +1). The roofline model: the FLOP:byte ratio vs the machine's
FLOPS:bandwidth ridge point (e.g., an A100: 19.5 TFLOP/s FP32 vs 2.0TB/s
≈ 9.75 FLOP/byte — derive which kernels are memory-bound by arithmetic
intensity, and why "GPU the accelerator" is only true for compute-dense
kernels). Occupancy as latency-hiding capacity: resident warps vs the
register/shared limits (the `cudaOccupancyMaxActiveBlocksPerMultiprocessor`
calculation reproduced by hand for one kernel). Async copy (`cp.async`:
SMEM bypass of the register round-trip on Ampere+ — the pipeline insight).
Lab: a stride sweep (1/2/4/8) with timings per stride; the tiled-vs-naive
matmul measured; a roofline plot for 3 toy kernels (compute where the
ridge crosses).

**Chapter 03 — allocation & the real OOM.** The GPU has an MMU: per-process
virtual address spaces, page tables at device scale (module 01 ch03's
mechanism — reused, not re-derived; the interesting deltas: page sizes
2MB/64KB, a *physical* memory owned by one process-agnostic driver; peer
mappings). cudaMalloc mechanics: driver-level VA reservation + physical
backing at 2MB granularity (why sub-allocations round: a 64B tensor page
alloc... the allocator's page granularity tax), VA space is plentiful
(48-bit+) but *physical* is the scarce resource — the first insider
correction: "out of memory" usually means out of *contiguous policy
satisfiable* memory, not out of bytes. The caching allocator (PyTorch's,
as the worked example): the block-rounding ladder (512B → 1MB... then 2MB
increments; segregated free lists per size class — module 01 ch19's
segregated-fit, recognized), split/remainder blocks, `empty_cache()`
semantics (returns unused blocks to the driver; *does not* defragment —
the classic misuse), reserved vs allocated (nvidia-smi shows *reserved*
— why the number disagrees with your tensor sum), expandable_segments
(the modern virtual-memory-based fix: grow segments via VA extension
instead of new cudaMallocs — flag the config: `PYTORCH_CUDA_ALLOC_CONF=
expandable_segments:True`). The real OOM causes, as a diagnostic
flowchart: (1) fragmentation (reserved-but-unallocatable — the memory
picture drawn), (2) other processes (nvidia-smi process list; MPS
sharing), (3) NCCL communication buffers (the 2× rank-size... the
allreduce scratch), (4) CUDA graph memory pools (private pools owned by
captured graphs), (5) cuDNN workspaces (the algo-choice memory), (6)
the driver/kernel reserve. Fragmentation arithmetic: why "free ≥ request
fails" with 2MB-granularity splits (derive a worked failure example from
first principles — module 01 ch05's fragmentation lesson, replayed on
the GPU). Lab: reproduce a fragmentation OOM (alternating tensor sizes,
free, then request one large); show expandable_segments fixing it;
nvidia-smi + `torch.cuda.memory_summary` read line by line.

**Chapter 04 — unified memory & the future.** Unified / managed memory
(UM): one pointer space across CPU+GPU; the mechanism = GPU page faults
(the GMMU fault handler migrates pages on demand — module 01 ch03's
demand paging, literally reused at device scale; prefetch hints
(`cudaMemPrefetchAsync`) as the manual transmission, `cudaMemAdvise`
as the policy hint), thrashing detection (the hardware counters →
migration advisor loop — the insider's "let the runtime learn your
affinity" story), and the performance reality (fault-driven migration
costs ~µs-ms per page — UM is a *convenience*, not a fast path; the
numbers that make explicit copies still worth writing). Apple unified
memory (the integrated pole): one LPDDR pool, one fabric — the discrete
tax (PCIe copies both directions) vanishes; but *total* bandwidth is
shared between CPU and GPU (M1 ~68GB/s, M3 Max ~300GB/s, M4 Max ~546GB/s
— vs discrete HBM 2-3+TB/s: the throughput tradeoff stated honestly;
the insider design rule: integrated wins on data-local pipelines,
discrete wins on compute-dense ones), and the OOM difference: no VRAM
wall — the system-wide footprint limit (Jetsam/logic from module 06
ch02 returns) — "unified memory" = *one number to budget*. Consoles as
the origin story (Xbox/PlayStation unified GDDR since 2013 — the
first mainstream UMA machines). CXL as the next tower level: memory as
a network-attached device (CXL 2/3 type-3 memory: shared pools,
latency-added (~+100-200ns) — the "disaggregated memory" datacenter
story: memory becomes a *rack-scale* resource; RDMA as the current
version: remote memory with ~1-2µs latency — the latency ladder
extended: registers → SMEM → L2 → HBM → NVLink peer → CXL → RDMA).
The closing table (the module's and arguably the program's payoff):
one row per level of the *combined* CPU+GPU+network memory tower with
latency, bandwidth, capacity, and *who manages it* — module 01 ch02's
tower, fully grown. Lab: a UM program with and without prefetch
timed; the peak-bandwidth arithmetic for an integrated vs discrete
design compared on paper for one workload.

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **CUDA performance literacy**: coalescing, occupancy, roofline — the three
  numbers that explain 90% of kernel speed.
- **OOM debugging**: the real flowchart — fragmentation, NCCL, graphs,
  workspaces — diagnosed from `memory_summary` and nvidia-smi, not superstition.
- **Hardware judgment**: integrated vs discrete, prefetch vs fault, CXL on
  the horizon — procurement and architecture opinions with derivation behind
  them.
- **The completed tower**: registers to RDMA in one table — module 01's
  hierarchy, fully grown up.
