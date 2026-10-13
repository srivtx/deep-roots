# Module 12 final exam — GPU & accelerator memory

Twelve questions, three per chapter. Closed book; every answer is worked, not
stated — the derivations are the point. Scoring: 11+ = you own this module;
8-10 = solid, revisit the flagged chapters; <8 = the chapter quizzes are the
study path.

**Part I — the hierarchy (chapter 01)**

1. **Predict.** A kernel compiles to 80 registers per thread on an
   Ampere-class SM: 65,536 32-bit registers, 64 warp slots, 4 warp
   schedulers with 16 resident warps each. Derive resident warps,
   occupancy, and warps per scheduler, then decide whether a 400-cycle
   global load issued once per 16 instructions can be hidden — at this
   occupancy, and at *any* occupancy.

2. **Predict.** A warp reads 32-bit shared memory at word strides 8, 12,
   and 15 (`smem[lane * S]`). For each: how many serialized passes,
   justified by the gcd rule? Which is fastest, and what is the general
   fix for a conflicted stride?

3. **Diagnose.** A filter kernel branches per thread: `if (v[tid] > 0) {
   two global loads + 300 math instructions } else { one load + 8
   instructions }`. Every warp splits ~50/50. The profile: 75% occupancy,
   32 registers/thread, top stall "long scoreboard", issue efficiency
   ~50%. Trace the mechanism — what divergence does to issue efficiency
   and to the stall — and why 75% occupancy cannot buy either back.

**Part II — coalescing & the roofline (chapter 02)**

4. **Predict.** `struct V { float x, y, z; };` — a warp loads `v[tid].x`
   from an AoS array, base aligned. Derive sectors, transactions (128 B
   units), bytes touched, payload, efficiency; compare with SoA `xs[tid]`;
   and explain why the multiplier is 3x when a four-field struct gives 4x.

5. **Predict.** A kernel does 8 FLOP per element and moves 16 B per
   element (three float reads, one write) on an A100 (19.5 TFLOP/s FP32,
   2.0 TB/s HBM). Give arithmetic intensity, attainable FLOP/s, percent
   of peak, verdict. What minimum AI reaches 50% of peak? A teammate
   proposes halving the FLOP count — what does the roofline say that
   buys?

6. **Diagnose.** A column-reduction kernel computes `s[tid] += a[r*4096 +
   tid]`: 4096 columns, a warp owns 32 of them, the loop walks rows. `ncu`
   reports sectors-per-request 32.0; the kernel runs ~8x slower than its
   row-sum sibling. Diagnose the access shape, derive the traffic, and
   give the one-sentence fix.

**Part III — allocation & the real OOM (chapter 03)**

7. **Derive.** A 60 MiB toy device, 20 MiB segments. Alternating requests
   of 5 MiB + 1 B (→ 6 MiB blocks) and 3 MiB + 1 B (→ 4 MiB blocks) fill
   three segments, each [6][4][6][4] MiB; then every 4 MiB block is freed.
   An 8 MiB request arrives. (a) The rounded request and pool? (b) Derive,
   rule by rule, why it fails though free = 24 MiB ≥ 8 MiB. (c) Why does
   the same request succeed under `expandable_segments:True`?

8. **Diagnose.** A training job on a 40 GiB card dies: "Tried to allocate
   512.00 MiB; 3.1 GiB is reserved by PyTorch but unallocated."
   `memory_summary`: reserved 39.6 GiB, allocated 36.5 GiB, inactive
   splits 210, OOM events 5. The developer calls
   `torch.cuda.empty_cache()` — 0 MiB released — and retries twice.
   Identify the cause, explain why `empty_cache()` was guaranteed to
   release nothing, and give the fix ladder in cost order.

9. **Concept.** A model holds 10,000 tensors of 64 KiB each. What do they
   cost through raw `cudaMalloc`, what through the caching allocator, and
   why does the caching allocator exist? Name the module 01 allocator it
   replays and the two taxes it removes.

**Part IV — unified memory & the future (chapter 04)**

10. **Concept (cross-module).** Module 01 ch02's tower was managed by
    hardware (caches) plus one OS level (DRAM). The completed tower's
    manager column reads compiler → kernel author → hardware → driver
    → runtime/OS → application — each manager further from the silicon.
    Why must the manager get further away as levels slow down, and why
    is that why each faster level exists?

11. **Diagnose.** A pipeline allocates 2 GiB of managed memory, the *host*
    initializes every page, then a kernel streams it once. The kernel
    phase takes 786 ms; the explicit-copy version totals ~90 ms. Nsight's
    unified-memory track shows ~1,024 faults. Derive the per-fault
    overhead, name the mechanism and its module 01 chapter, then give the
    one-line fix and the better fix.

12. **Concept (cross-module).** Three things a CUDA-trained engineer gets
    wrong on Apple Silicon (module 07 ch06: Mach VM, Jetsam): why there
    is no "CUDA out of memory" and what limits memory instead; why a
    GPU-only benchmark drops 30-50% when the CPU streams simultaneously;
    and which machine — M4 Max or A100 — wins an 8 GiB host-born
    streaming workload, with numbers.

<details>
<summary>Answers</summary>

**Part I**

1. 80 regs × 32 = 2,560 registers per warp; 65,536 / 2,560 = 25.6 → **25
   resident warps** (800 threads) = **39%** of the 64-warp ceiling, ~6 per
   scheduler. Hiding needs warps ≥ latency / instructions between loads =
   400 / 16 = **25 per scheduler**. Six is nowhere near; even 100%
   occupancy supplies 16 — the ratio is unhideable at *any* occupancy.
   The fix is tiling or ILP, not more warps — ch01's ratio test, why
   ch02's tiling exists.

2. Lane *i* hits bank `(stride * i) mod 32`; passes = gcd(stride, 32).
   Stride 8: gcd(8, 32) = 8 → **8 passes**. Stride 12: gcd(12, 32) = 4 →
   **4 passes** — stride 12 beats stride 8. Stride 15: gcd(15, 32) = 1 →
   all banks distinct → **1 pass**, fastest. Odd strides are
   conflict-free; the fix for a conflicted stride is padding +1 (stride
   33), not reordering (ch01).

3. The 32 lanes share one program counter, so the warp executes *both*
   paths serially with the other side masked: ~311 instructions where a
   uniform branch would issue one — efficiency halves before any memory
   effect. The taken path's loads then stall the *whole* warp
   ~400 cycles: masked lanes cannot issue, and reconvergence waits at
   the merge point — the "long scoreboard" stall. Occupancy cannot buy
   it back: 75% is 48 resident warps, but each is equally divergent.
   Fix: make the branch warp-uniform (sort or partition the data by
   side) — the hardware refuses to predict control flow by design
   (ch01's SIMT trade).

**Part II**

4. `.x` sits at byte 12·tid: addresses 0..372, span 376 B → **12 sectors,
   3 transactions, 384 B touched, 128 B payload, 33%**. SoA `xs[tid]` is
   `4·tid`: **4 sectors, 1 transaction, 100%** — a 3x traffic multiplier.
   The multiplier is span arithmetic, not struct-size / 4: a 12 B stride
   packs 2-3 threads per sector; the 16 B struct leaves two = 4x. Layout
   is a memory-hierarchy decision (ch02; module 01 ch06 at warp scale).

5. **AI = 8 FLOP / 16 B = 0.5 FLOP/byte** — far left of the 9.75 ridge
   (19.5 / 2.0). Attainable = 0.5 × 2.0 TB/s = **1.0 TFLOP/s = 5.1% of
   peak**: memory-bound, the FLOPs idle. 50% of peak needs
   AI = 9.75 / 2.0 = **4.875 FLOP/byte**. Halving the FLOP count buys
   nothing — the ceiling is bandwidth / bytes; halving the *bytes*
   doubles speed (ch02's triage division).

6. Each warp instruction reads 32 addresses 16,384 B apart: each
   thread's 4 B in its own 32 B sector — **32 sectors, 8
   transactions, 1,024 B touched for 128 B payload = 12.5%**, the stride
   table's worst row: 8x the row-sum's traffic, hence ~8x the time. Fix:
   stage coalesced — load contiguous row segments into shared memory
   (stride-1 warp loads), then accumulate columns from shared (ch02's
   tiling reflex).

**Part III**

7. (a) 8 MiB — already a 2 MiB multiple, large pool. (b) **Exact size
   class:** no 8 MiB block — the lists hold 4 MiB blocks. **Best fit ≥ 8
   MiB:** largest hole is 4 MiB. **Coalesce:** every hole is fenced by
   live 6 MiB blocks — adjacent frees would merge; none are. **Cross
   segments:** never — separate VA reservations. **New segment:** a
   fresh 20 MiB cudaMalloc against a 60/60-reserved device, refused at
   the driver boundary. Free 24 ≥ request 8; contiguous,
   policy-satisfiable space: 0 — no byte was consumed; the bytes were
   stranded by geometry and the no-move promise (ch03; module 01 ch05).
   (c) With `expandable_segments:True`, each freed 4 MiB block *unmaps
   its 2 MiB pages* — physical returns to the driver (mapped: 60 → 36
   MiB) — and the 8 MiB request maps fresh pages into a fresh contiguous
   VA run: contiguity becomes a VA choice (ch03).

8. **Cause #1, fragmentation** — the "3.1 GiB reserved but unallocated"
   line *is* the diagnosis; inactive splits 210 counts holes freed next
   to live blocks. `empty_cache()` releases only *entirely free*
   segments; at failure every segment holds a live block — which is what
   fenced the holes — so 0 MiB was structurally guaranteed. Fix ladder,
   cost order: steady tensor shapes (pad variable batches so block sizes
   repeat — free) → `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`
   (one env var) → smaller batch (throughput) → restart (last). Plan
   capacity by peak *reserved*, never the tensor sum (ch03).

9. Raw `cudaMalloc`: the 2 MiB page is the smallest currency, so each
   64 KiB tensor maps 2 MiB — 10,000 × 2 MiB = **~20 GiB for 625 MiB of
   data, a 32x tax** — plus 10,000 slow, sometimes-synchronizing driver
   calls. The caching allocator rounds to a 512 B multiple (64 KiB is
   one already — no tax), packs 32 tensors per 2 MiB small-pool segment,
   and serves repeats from size-class free lists: ~625 MiB allocated,
   ~626 MiB reserved, ~313 cudaMallocs. It is module 01 ch19's
   **segregated-fit** replayed at device scale, deleting the
   page-granularity tax and the per-call cost (ch03).

**Part IV**

10. A manager must decide, ahead of the data's arrival, what lives at
    its level — and its reaction time is bounded by its distance from
    the silicon. The compiler sees the whole program offline (registers);
    the kernel author stages tiles by hand (shared); hardware reacts in
    cycles (L2); the driver maps pages on faults in ~µs (HBM); the
    runtime/OS tiers cold pages to a CXL pool over ms; the application
    alone knows the semantics, so it owns RDMA placement. Each manager is
    too slow for the level above — so that level exists to absorb the
    latency the slower one cannot hide: a stack of progressively slower
    *managers*, each covering the one below (ch04's closing table —
    module 01 ch02's tower, fully grown).

11. 2 GiB / 2 MiB = **1,024 pages**, each faulting once as the kernel
    first touches it. Streaming the same bytes at ~25 GB/s costs 80 ms,
    so 786 − 80 ≈ 706 ms is pure overhead: **~690 µs per fault** — trap,
    pin, 2 MiB DMA over PCIe, GMMU page-table fixup, warp restart, paid
    1,024 times — ~10x streaming: the convenience tax, measured. The
    mechanism is module 01 ch03's **demand paging** (the disk became a
    PCIe link). One-line fix: `cudaMemPrefetchAsync(p, bytes, device)`
    after host init — the same bytes at streaming speed, ~90 ms total,
    matching the explicit copy with less code. Better: have the data
    *born* on the device (initialize with a kernel) — nothing ever
    crosses the bus (ch04's birthplace rule).

12. (a) No VRAM wall: one LPDDR pool, one fabric — no second per-device
    memory to exhaust. The limit is the system-wide *dirty footprint*,
    enforced by Jetsam bands on Mach VM (module 07 ch06; module 06 ch02):
    one number to budget, no "clear the GPU cache" button to hunt for.
    (b) CPU and GPU share the one LPDDR fabric — M4 Max ~546 GB/s —
    budgeted as one pipe, never two: concurrent CPU streaming takes the
    fraction a GPU-only benchmark assumed it owned — the 30-50% drop.
    (c) The M4 Max wins: 8 GiB / 546 GB/s ≈ **14.7 ms**; the A100 pays
    for the bridge the integrated design deleted — 8 GiB host-born
    crosses PCIe at ~25 GB/s (320 ms) plus 4 ms compute ≈ **324 ms**.
    Lower peak bandwidth wins because the *copy* dominated (ch04).

</details>
