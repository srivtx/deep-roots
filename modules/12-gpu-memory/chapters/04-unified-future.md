# Chapter 04 — Unified memory & the future

> **The 30-second version.** Unified memory (UM) gives CPU and GPU one
> pointer space, and the mechanism is one you own: the page fault — module 01
> chapter 03's demand paging, reused at device scale. It is a convenience,
> not a fast path: a fault costs ~µs–ms per page, so you hint with
> `cudaMemPrefetchAsync` and `cudaMemAdvise` when you know the affinity. The
> integrated pole (Apple Silicon; consoles since 2013) deletes the copy — one
> pool, one fabric — but shares one bandwidth number (M1 ~68GB/s, M4 Max
> ~546GB/s vs discrete HBM 2–3+TB/s) and replaces the VRAM wall with a
> system-wide footprint limit. CXL and RDMA grow the tower to rack scale:
> memory as a network endpoint, and the closing table — module 01 chapter
> 02's hierarchy, fully grown.

## One pointer space, and the fault that moves a page

Chapter 03 ended with the discrete shape: two pools (host DRAM, device HBM),
two MMUs, one expensive bridge — the `cudaMemcpy` you write to cross PCIe.
Unified memory changes the *programming model*, not the hardware:
`cudaMallocManaged` returns one pointer valid on both sides. Derive what that
requires: one address, two MMUs, one physical copy of the page at a time — so
when the "other" side touches it, something must move the bytes. That
something is the fault handler:

```text
  DISCRETE (ch03)                     UNIFIED / MANAGED (this chapter)

  +-----------+ cudaMemcpy +-----+    +-----------+ GMMU fault +-----+
  | CPU + DRAM|<---------->|GPU  |    | CPU + DRAM|<--------->|GPU  |
  | host malloc| PCIe4 x16 |HBM |    | one pointer p ------>|HBM  |
  +-----------+ ~25 GB/s  +-----+    | valid both sides     +-----+
                                      2MB pages, ~us-ms per page
```

The mechanism is module 01 chapter 03's demand paging, *literally reused*.
There, a fault meant "the page is on disk — bring it in." Here it means "the
page is in the other machine's memory — bring it over the bus." What changed
is the unit (4KB → 2MB, or 64KB for some mappings) and the transport (disk →
PCIe/NVLink). The solution's shape — trap, locate, transfer, fix up page
tables, resume — is identical, because the problem is: an address is valid
but its bytes live somewhere slower.

Prefetch is the manual transmission on top: `cudaMemPrefetchAsync(p, bytes,
device)` moves pages *before* they fault, at streaming speed. `cudaMemAdvise`
is the policy hint — `SetReadMostly` lets both sides *replicate* read-heavy
pages instead of migrating them, `SetPreferredLocation` pins a page to one
side, `SetAccessedBy` pre-maps it. They are hints, not commands: the runtime
may ignore them.

## The performance reality: what a fault costs

A single 2MB page fault is: trap into the driver, pin the page, DMA 2MB over
the bus, update the GMMU page tables, restart the faulting warp. Bandwidth
alone is 2MB / 25GB/s ≈ 80µs (PCIe 4.0 x16, A100-class); fixed overheads push
per-page cost to ~µs–ms.

Scale it up. 1GB of managed memory = 512 pages of 2MB. Streaming that 1GB
over PCIe costs 40ms. Fault-driven first-touch — a kernel coldly walking the
whole buffer — commonly lands at 100–400ms (A100-class, CUDA 12.x; a Colab T4
on PCIe 3.0 runs ~2x slower). That ~2.5–10x over pure streaming is the
*convenience tax*: UM removes the correctness burden of copies, not their
cost.

The worst case is thrashing: host and device alternately touch the *same*
pages, every touch faults, and the bus carries pages back and forth — the
demand-paging pathology of a working set exceeding its fast level, replayed
across a 25GB/s link. NVIDIA's answer is the advisor loop: the GMMU's
hardware fault/migration counters are exposed to profilers (Nsight Systems'
unified-memory track, Nsight Compute's memory-workload analysis), so you
profile which pages thrash, add an `advise` or `prefetch`, re-profile. The
insider framing: *let the runtime learn your affinity* — you can still beat
the learner: you know the pattern, it only infers it.

The verdict, derived: host-born data consumed once — UM with a prefetch is
nearly free and cleaner than the copy version. Data ping-ponged per iteration
— explicit staging buffers still win, because the bytes move once, at
streaming speed, instead of a fault per page per direction.

## The integrated pole: one pool, one fabric, one budget

Now delete the bridge. Apple Silicon puts the GPU on the same LPDDR package
and fabric: one pool, one memory controller, one bandwidth number. The PCIe
tax doesn't get faster — it *vanishes*; there is no copy because both sides
reach the same bytes.

But one pool means one budget. Total bandwidth is *shared* between CPU and
GPU: M1 ≈ 68GB/s, M3 Max ≈ 300GB/s, M4 Max ≈ 546GB/s (Apple, per-chip; M2
Ultra widens the fabric across two dies to ~800GB/s). Discrete accelerators
sit at A100 2.0TB/s, H100 3.35TB/s (HBM3), B200 ~8TB/s (HBM3e) — 6–10x more,
from stacked dies and wide interfaces (chapter 01). State the trade honestly:
integrated wins on *data-local* pipelines (touch bytes once, modest
arithmetic per byte — copy elimination dominates), discrete wins on
*compute-dense* ones (many passes per byte — bandwidth and tensor cores
dominate). Lab 3 turns this into roofline arithmetic.

The OOM story also inverts. Chapter 03's discrete OOM was "no
policy-satisfiable contiguous VRAM" — a fragmentation tale with two budgets.
On Apple there is no VRAM wall: the limit is the system-wide *footprint*,
enforced by module 06 chapter 02's Jetsam logic — the OS tracks your dirty
bytes and kills by band. "Unified memory" therefore means *one number to
budget*: model + activations + OS all draw from the same 64GB, and macOS may
swap machine-wide under pressure (performance collapses long before the kill
does).

The origin story predates Apple by a decade: the 2013 consoles. Xbox One and
PlayStation 4 shipped 8GB of unified GDDR5 — PS4 at ~176GB/s, Xbox One's DDR3
at ~68GB/s with a 32MB ESRAM sidecar — the first mainstream UMA machines,
chosen because a fixed-hardware game pipeline (physics on CPU, shading on GPU,
one framebuffer) has no budget for a copy. UMA was a console decision before
it was a laptop feature.

## Memory as a network endpoint: CXL and RDMA

Module 09 chapter 05 ends at "memory becomes a network endpoint." Its two
concrete forms:

**RDMA is the current version.** A NIC with verbs reads and writes *remote*
pinned memory without disturbing the remote CPU. Hardware latency is ~1–2µs
(2–5µs through software), and a 100–400GbE link moves 12.5–50GB/s — how
large-model training shards parameters today: each rank's HBM is one tier of
a cluster-scale pool.

**CXL is the next tower level.** CXL (2.0/3.x, on PCIe 5/6 physical) is a
cache-coherent protocol for *type-3 memory devices*: an expander box or a
rack-scale DRAM pool the host sees as memory. The insider number is the added
latency — roughly +100–200ns over local DRAM on 2024-era platforms — with
per-port bandwidth bounded by the link (~64GB/s theoretical, x16 Gen5). That
latency is *DRAM-ish, not DRAM*: fine for capacity — cold pages, indexes,
second-level caches — wrong for hot rows. The disaggregated-memory story is
the capacity column: memory becomes a rack-scale resource, allocated by a
fabric manager and tiered by OS daemons between local DRAM and the pool —
hot data local, cold data remote, the tower's rule one level up, with the OS
doing for CXL what the L2 does for HBM.

## The completed tower

Every level obeys the same physics — bigger, slower, further from compute —
plus one new axis: *who manages it*.

```text
+--------------+-----------------+------------------+-------------------+-----------------------------+
| Level        | Latency (approx)| Bandwidth        | Capacity          | Who manages it              |
|              | (Ampere-class,  |                  | (typical)         |                             |
|              | order of mag.)  |                  |                   |                             |
+--------------+-----------------+------------------+-------------------+-----------------------------+
| registers    | ~1 cycle        | 1 op/cycle/thread| 64K x 32b per SM  | the compiler (ptxas        |
|              |                 |                  | (~256KB/SM)       | reg allocation = occupancy) |
+--------------+-----------------+------------------+-------------------+-----------------------------+
| SMEM / L1    | ~20-30 cycles   | ~128B/clk/SM     | 48-228KB per SM   | YOU: cudaFuncSetAttribute  |
|              |                 |                  | (split vs L1)     | + the kernel author         |
+--------------+-----------------+------------------+-------------------+-----------------------------+
| L2           | ~200 cycles     | multi-TB/s agg.  | 40-50MB device    | hardware (the coherence    |
|              |                 |                  |                   | point, ch01)                |
+--------------+-----------------+------------------+-------------------+-----------------------------+
| HBM          | ~400-800 cycles | A100 2.0TB/s,    | 40-180GB          | driver + caching allocator  |
|              | (~300-600ns)    | H100 3.35TB/s,   |                   | (ch03) + the GMMU (2MB pg)  |
|              |                 | B200 ~8TB/s      |                   |                             |
+--------------+-----------------+------------------+-------------------+-----------------------------+
| NVLink peer  | ~1-2 us         | 600GB/s (NVLink3)| peers' HBM:       | NCCL / runtime, via driver  |
| (GPU->GPU)   |                 | 900GB/s (4),     | ~0.5-1TB per node | peer mappings; you opt in   |
|              |                 | 1.8TB/s (5)      |                   |                             |
+--------------+-----------------+------------------+-------------------+-----------------------------+
| CXL type-3   | DRAM +100-200ns | ~64GB/s per x16  | TBs, rack-scale   | OS + fabric manager +       |
| (pool)       | (~350ns total)  | Gen5 port        | shared            | tiering daemons             |
+--------------+-----------------+------------------+-------------------+-----------------------------+
| RDMA remote  | ~1-2 us hw      | 12.5-50GB/s per  | cluster-scale     | YOUR APP: verbs, pinned     |
| memory       | (2-5 us s/w)    | 100-400GbE NIC   | TBs-PBs           | regions, queues; the NIC    |
+--------------+-----------------+------------------+-------------------+-----------------------------+
```

Read the last column top to bottom: compiler → you → hardware → driver →
runtime/OS → application — each manager further from the silicon and slower
to react, which is why the levels above it exist. This is module 01 chapter
02's tower, fully grown: seven levels, seven managers, one machine.

## What insiders know

- **The fault-migration cost, quantified.** A 2MB page fault is ~80µs of
  bandwidth (PCIe 4.0 x16, ~25GB/s effective) *plus* driver and page-table
  overhead — µs-to-ms per page. 1GB first-touch ≈ 512 faults ≈ 100–400ms
  observed (A100-class, CUDA 12.x) vs 40ms streamed. UM is a convenience,
  not a fast path; explicit copies stay worth writing.
- **The prefetch-vs-fault rule.** Prefetch wins only when affinity is stable
  and known: streaming phases get `cudaMemPrefetchAsync`, cold/one-shot
  touches can just fault. If access is genuinely interleaved, prefetching
  adds a second copy on top of the thrash. And `cudaMemAdviseSetReadMostly`
  converts migration into *replication* — the flag insiders reach for on
  read-only weights.
- **The shared-bandwidth contention rule.** On Apple UMA the one number is
  *one number*: CPU and GPU streaming concurrently each get a fraction —
  GPU-only benchmarks can drop ~30–50% when the CPU streams simultaneously
  (M-series, varies by chip and OS). Budget an integrated machine as one
  pipe, never two.
- **The M-series bandwidth ladder.** M1 ≈ 68GB/s, M2 ≈ 100GB/s, M3 Max ≈
  300GB/s, M4 Max ≈ 546GB/s, M2 Ultra ≈ 800GB/s (Apple, per-chip) — against
  discrete HBM at 2–3+TB/s (A100/H100) and up to ~8TB/s (B200, HBM3e).
- **CXL's added latency is the whole design constraint.** +100–200ns (CXL
  2.0/3.x, type-3) means pool memory is "DRAM-ish," not DRAM. Anything on
  the critical path goes in local DRAM; the pool is the tier for cold pages
  and capacity — the L2-to-HBM rule, one level up.
- **Consoles are the origin story.** Unified GDDR5 since 2013 (PS4 8GB at
  ~176GB/s; Xbox One DDR3 ~68GB/s + 32MB ESRAM) — devs wrote against UMA a
  decade before it was a laptop marketing term.

## See it yourself

All three labs run from a laptop CPU; the UM lab needs CUDA hardware only for
*your* numbers (documented: A100-class, CUDA 12.x; a Colab T4 reproduces them
within ~2x).

**Lab 1 — peak-bandwidth arithmetic: integrated vs discrete, on paper.**
One workload: stream-read 8GB once. Two machines: an M4 Max (546GB/s, one
pool) and an A100 box (HBM 2.0TB/s, host DRAM over PCIe 4.0 x16 at ~25GB/s
effective). The only question that matters: *where is the data born?*

```bash
python3 - <<'PY'
GB = 1e9
print("integrated M4 Max (546 GB/s)     : %6.1f ms" % (1e3*8*GB/(546*GB)))
print("discrete A100, data born on GPU  : %6.1f ms" % (1e3*8*GB/(2_000*GB)))
print("discrete A100, data born on host : %6.1f ms" %
      (1e3*(8*GB/(25*GB) + 8*GB/(2_000*GB))))   # copy + compute
PY
```

```text
integrated M4 Max (546 GB/s)     :   14.7 ms
discrete A100, data born on GPU  :    4.0 ms
discrete A100, data born on host :  324.0 ms
```

Same two machines, opposite winners — the whole argument in three lines.

**Lab 2 — UM with and without prefetch (documented commands; CUDA 12.x,
A100-class PCIe 4.0 x16).** Allocate 1GB managed, then either let the kernel
first-touch it, or prefetch it first.

```c
// um.c — managed memory: fault-first-touch vs prefetch (CUDA 12.x)
size_t n = 1ull<<30; int *p;
cudaMallocManaged(&p, n);                 // one pointer, both sides
for (size_t i = 0; i < n/sizeof(int); i += 4096)  // HOST first touch:
    p[i] = 1;                             // pages start in host DRAM
if (getenv("PREFETCH"))
    cudaMemPrefetchAsync(p, n, 0);        // manual transmission: move now
kernel<<<...>>>(p, n);                    // device touch: faults (or not)
```

```bash
nvcc -arch=native -o um um.c        # build
./um                                # variant A: fault-driven first touch
PREFETCH=1 ./um                     # variant B: prefetched
```

Documented output (A100-class; magnitudes are the point):

```text
variant A (faults)     : prefetch      --     kernel  387 ms
variant B (PREFETCH=1) : prefetch     41 ms   kernel   12 ms
```

Check the arithmetic: variant B's prefetch is 1GB/25GB/s ≈ 40ms — pure
streaming, as predicted. Variant A's 387ms ≈ 9x that, ≈ 756µs per page: fault
overhead *on top of* the same bytes. That ratio is the convenience tax,
measured.

**Lab 3 — roofline: one kernel, two machines.** Kernel arithmetic intensity
30 FLOP/byte over 1GB. M3 Max: 300GB/s, ~14.2 TFLOP/s FP32
(vendor-unpublished, community-measured). A100: 2.0TB/s, 19.5 TFLOP/s FP32.

```bash
python3 - <<'PY'
ai, bytes = 30, 1e9
for name, bw, tf in [("M3 Max", 300e9, 14.2e12), ("A100", 2.0e12, 19.5e12)]:
    t_mem = bytes/bw; t_cmp = ai*bytes/tf
    bound = "memory" if t_mem > t_cmp else "compute"
    print("%-7s t_mem=%6.2f ms  t_comp=%6.2f ms  -> %s-bound" %
          (name, 1e3*t_mem, 1e3*t_cmp, bound))
PY
```

```text
M3 Max  t_mem=  3.33 ms  t_comp=  2.11 ms  -> memory-bound
A100    t_mem=  0.50 ms  t_comp=  1.54 ms  -> compute-bound
```

The *same kernel* flips regimes across machines: the integrated part is
bandwidth-limited, the discrete part compute-limited — and the discrete
machine still finishes first. Coda: on the A100's FP16 tensor-core path
(312 TFLOP/s) the ridge moves to ~156 FLOP/byte — both machines become
memory-bound, and bandwidth is the wall everywhere.

## How to use this knowledge

- **Procurement, derived not vibes.** For a data-local pipeline (records
  stream in, GPU touches each once — inference, transcoding), an M-series
  with N GB beats a discrete box whose data is born host-side: lab 1 says
  14.7ms vs 324ms, because two PCIe copies dominate everything. For
  compute-dense training, buy the VRAM number: "model + optimizer states +
  activations fit," and 2–3+TB/s HBM makes the passes fast.
- **Architecture: pick the copy strategy from the birthplace-consumption
  graph.** Host-born, GPU-consumed once → managed memory with a prefetch.
  Ping-ponged per iteration → explicit staging buffers. The advisor loop
  (counters → advise → re-profile) is the fallback when you can't predict
  your own access pattern.
- **Debugging "managed memory is slow."** Open Nsight Systems'
  unified-memory track, count migrations. Thousands of faults *after* the
  first pass means thrashing: host and device alternate on the same pages.
  Fix with `SetPreferredLocation`, a prefetch per phase, or phase-owned data.
- **Debugging "out of memory" on Apple.** It is a *footprint* limit (Jetsam
  bands, module 06 ch02), not a VRAM wall — there is no per-device memory to
  free. The lever is total footprint: smaller activations, streaming layers,
  a smaller resident model. Hunting for a "clear GPU cache" button is a
  category error the discrete world taught you.
- **The interview answer with derivation.** "Integrated vs discrete?" Two
  lines: copies over ~25GB/s vs zero copies; one ~300–546GB/s pool vs
  2–3+TB/s HBM — data-local → integrated, compute-dense → discrete, derived
  not asserted.

## Connections

- **Module 01 ch02** — the hierarchy: the closing table is that tower fully
  grown, CPU+GPU+network in one picture.
- **Module 01 ch03** — demand paging: the GMMU fault handler is that mechanism
  reused at device scale; thrashing here is swap thrash there.
- **Module 12 ch01** — the hierarchy numbers (registers, SMEM, L2, HBM) that
  populate the tower's top rows.
- **Module 12 ch02** — the roofline model lab 3 reuses, now compared *across
  machines*.
- **Module 12 ch03** — the discrete OOM contrasted with the footprint limit;
  the GMMU and 2MB pages come from there.
- **Module 06 ch02** — Jetsam and the footprint limit: unified memory's OOM is
  that logic, returned.
- **Module 07 ch06** — Apple UMA at the OS level: Mach VM and Jetsam beneath
  this chapter's bandwidth numbers.
- **Module 09 ch05** — memory as a network endpoint: RDMA and CXL are its
  concrete forms; the latency ladder it extends.

## Quiz

1. **Concept.** UM is slower than explicit copies, yet it exists. What,
   precisely, does unified memory remove — and what does it not remove?

2. **Concept.** An integrated machine has ~6x *less* memory bandwidth than a
   discrete one, yet finishes an 8GB host-born streaming workload ~20x
   faster. Explain the apparent contradiction.

3. **Predict the number.** 8GB of data, born on the host, streamed once on
   an M4 Max (546GB/s) and once on an A100 (HBM 2.0TB/s, PCIe 4.0 x16 at
   ~25GB/s effective). Give both times.

4. **Predict the number.** A kernel first-touches 1GB of managed memory in
   2MB pages. How many faults? If first-touch takes 387ms, what is the
   average cost per fault, and how does that compare to the 40ms
   pure-streaming time?

5. **Predict the result.** A kernel with arithmetic intensity 30 FLOP/byte
   over 1GB runs on an M3 Max (300GB/s, ~14.2 TFLOP/s) and an A100 (2.0TB/s,
   19.5 TFLOP/s). Which is memory-bound, which compute-bound, which finishes
   first?

6. **Bug diagnosis.** A UM program loops: host code refines a buffer, then a
   kernel processes it, repeat. It is 10x slower than the explicit-copy
   version, and adding `cudaMemPrefetchAsync` *before each phase* makes it
   slower still. Why?

7. **Bug diagnosis.** On a 64GB Apple machine with ~40GB free, a Metal app is
   killed as "out of memory" at a ~38GB footprint. There is no VRAM to be
   out of. What actually limited it? (Connects to another module.)

8. **Bug diagnosis.** `cudaMallocManaged` happily "allocates" 40GB on a GPU
   with 24GB of HBM, and the program runs — 20x slower than expected. What is
   the memory system doing, and why does it work at all?

<details>
<summary>Answers</summary>

1. **UM removes the correctness burden, not the cost.** It removes dual
   allocation, copy bookkeeping, and the bugs where a copy is forgotten or
   stale. It does not remove the data movement — every byte that must change
   sides still crosses the same 25GB/s link; UM just moves it via faults
   (~µs–ms per 2MB page) unless you prefetch. The bytes aren't cheaper; the
   code is smaller.

2. **The discrete machine pays for a bridge the integrated machine doesn't
   have.** Host-born data on discrete: copy 8GB at ~25GB/s (320ms) + compute
   (4ms) ≈ 324ms. Integrated: stream from the one pool at 546GB/s = 14.7ms.
   Lower *peak* bandwidth wins because the *copy* dominated the cost — and
   the integrated design deleted it. Birthplace decides (lab 1).

3. **M4 Max: 8GB / 546GB/s ≈ 14.7ms. A100: 8GB / 25GB/s + 8GB / 2.0TB/s ≈
   320ms + 4ms ≈ 324ms.** The discrete machine only wins (4ms) if the data
   is *born on the device*.

4. **512 faults** (1GB / 2MB). 387ms / 512 ≈ **756µs per fault** — ~19x the
   40ms pure-streaming time for the same bytes: trap + pin + page-table fixup
   + warp restart, paid 512 times. That measured ratio *is* the
   "convenience, not a fast path" claim.

5. **M3 Max: t_mem = 3.33ms, t_comp = 2.11ms → memory-bound. A100: t_mem =
   0.50ms, t_comp = 1.54ms → compute-bound.** The A100 finishes first
   (~2.2x) — one kernel, two regimes: the ridge differs per machine.

6. **Thrashing, and prefetch doesn't fix ping-pong — it doubles the bill.**
   Host and device alternately touch the same pages, so every phase faults
   the buffer across the bus (512 pages each way). Prefetching before each
   phase adds a *deliberate* copy on top of the faults it can't prevent —
   the data genuinely changes sides every phase, module 01 ch03's thrashing
   replayed across PCIe. Fix: phase-owned buffers — explicit staging, or
   `SetPreferredLocation` per phase.

7. **The Jetsam footprint band, not "free RAM."** The limit is the app's
   *system-wide dirty footprint*, enforced by XNU's Jetsam bands (module 06
   ch02): band ceiling, wired pressure, and competitors set a tighter limit
   than free memory suggests. No per-device OOM exists on unified memory —
   one number, enforced by the OS.

8. **Oversubscription: the fault handler pages GPU memory to host DRAM.**
   Since Pascal, managed allocations may exceed HBM; the 40GB of pages live
   in host DRAM and the GMMU faults them in and out (2MB at a time over
   ~25GB/s). It "works" because the addresses are valid and the fault handler
   is always available — the same reason swap works. It's slow because the
   working set exceeds the fast level: module 01 ch03's thrashing, with the
   slow level a PCIe link away.
</details>
