# Chapter 05 — Collectives and the datacenter computer

> **The 30-second version.** A 25,000-GPU training run is one computer:
> "RAM" is 25,000 separate HBMs, the buses are InfiniBand rails, and the
> central instruction is not arithmetic but the collective all-reduce.
> The ring algorithm sums N gradients while each link carries only
> 2(N−1)/N × data — bandwidth-optimal (per-link cost stops growing with
> N), latency-suboptimal (2(N−1) serialized steps; trees win small,
> rings win big, NCCL chooses). Under it sits RDMA — module 13's NIC
> reborn as a DMA engine reading HBM directly: "remote memory as a NUMA
> node that is very far away." Over it sit the synchrony tax (one
> straggler of 25,000 stalls every step) and the failure arithmetic (~14
> GPUs die per day), so the job checkpoints every ~30 minutes exactly as
> a 1980s computer swapped a too-big-for-RAM program: checkpoint = swap,
> the fleet = spinning disk, HBM = RAM — same shapes, 10⁹× the bytes.

## The fabric is the tower's new bottom half

Module 01 chapter 02 built the tower from registers to DRAM; the
datacenter bolts four floors underneath. These numbers (H100-class,
2024, per-direction bandwidths) drive every decision here:

| hop | latency | bandwidth |
|---|---|---|
| NVLink / NVSwitch (8-GPU domain) | ~1–2 µs | 900 GB/s bisection |
| PCIe gen5 x16 | ~2 µs | ~64 GB/s |
| InfiniBand NDR, RDMA verbs | ~1–2 µs | 400 Gb/s = 50 GB/s per NIC |
| Ethernet RoCE | ~2–5 µs | 25–100 Gb/s per NIC |
| same-rail hop (one ToR switch) | ~2 µs | ~50 GB/s |
| cross-rail hop (via spine) | 5–20 µs | ~5 GB/s |
| kernel TCP (no bypass) | ~20–50 µs | a latency floor, not bandwidth |

Two facts dominate: the ~2 µs fabric floor is 10³× DRAM latency (a
collective must amortize it over big messages or die), and bandwidth
collapses 10× when you leave your rail — which forces the topology.

**Rail-optimized design.** Give every GPU its own NIC; in every node,
GPU i's NIC plugs into top-of-rack switch *i* — the "rail." Rail 0's ToR
hosts GPU 0 of every node in the block, so same-index cross-node traffic
takes one switch hop, never a spine. The fat-tree alternative buys
uniform any-to-any reachability but must oversubscribe its 2-tier/
3-tier CLOS spine at 2:1–8:1 — any-to-any means everyone shares a
fraction of line rate. Cross-rail traffic (GPU 3 → GPU 7) climbs to the
spine and pays ~5 GB/s instead of ~50 GB/s.

Here the quip "the network is a shared cache with weird associativity"
becomes precise: finite bisection bandwidth = the cache's capacity; the
GPU-index-to-rail mapping = the set-index function, rail-aligned traffic
= direct-mapped hit, cross-rail traffic = conflict miss serviced by the
slow spine; the scheduler's topology-aware placement = the indexing
policy. A non-rail-aligned job thrashes exactly as a bad stride thrashes
a set-associative cache.

**Placement is an allocator problem, and it fragments the fleet.** A 25k
job must be gang-scheduled — all ranks start together or not at all;
rank 0 is already waiting inside its first all-reduce — *and*
rail-aligned. Bin-packing such jobs leaves holes: real fleets run at
60–80% occupancy, and the stranded FLOPs are paid for but unusable.
Capacity planning follows: to deliver 25,000 rail-aligned GPUs at 70%
occupancy, install ~36,000 — module 01 chapter 19's fragmentation
problem at fleet scale.

## Four collectives and one identity

Everything the training loop says to the fabric is one of four operations
on N GPUs each holding a D-byte tensor: **broadcast** (GPU 0's tensor
becomes everyone's — init, re-shard after restore); **reduce-scatter**
(element-wise sum, result *sharded*: GPU i keeps 1/N fully reduced — the
gradient step's true shape, since ZeRO (ch03) stores each shard on one
GPU); **all-gather** (the inverse: shards circulate until everyone holds
the full tensor — parameters rebuilt before forward); **all-reduce**
(sum everywhere, replicated). The fourth is not a fourth algorithm but
the **composition law**:

```
   AllReduce = ReduceScatter ; AllGather

   in:  G0 [w0 x0 y0 z0]     reduce-scatter       G0 [S_w  -   -   - ]
        G1 [w1 x1 y1 z1]    ----------------->     G1 [ -  S_x  -   - ]
        G2 [w2 x2 y2 z2]     (sum, keep 1 shard)   G2 [ -   -  S_y  - ]
        G3 [w3 x3 y3 z3]                            G3 [ -   -   -  S_z ]
                                                       |
   out: everyone [S_w S_x S_y S_z] <--------------- all-gather (circulate)
```

Each half moves (N−1)/N × D per GPU; both halves cost the same; every
all-reduce you will ever profile is this identity in a different graph
(NCCL's debug log prints the two phases literally).

## The ring, derived: bandwidth-optimal, latency-suboptimal

**Setup.** N GPUs in a unidirectional ring: GPU i sends only to GPU (i+1)
mod N. Split the D-byte tensor into N chunks of D/N — small enough that
a link absorbs one while the next is prepared (chunk pipelining, latency
hidden behind bandwidth).

**Phase 1, reduce-scatter (N−1 steps).** At step t (t = 0 … N−2), GPU i
sends chunk (i−1−t) mod N rightward, simultaneously receives the
same-index chunk from its left, and folds it in. Trace chunk c=0 at N=4:
GPU 1's partial hops to G2 (t=0), grows; to G3 (t=1), grows; to G0
(t=2). After N−1 steps every chunk has collected all N contributions
and sits fully reduced at one owner.

```
   reduce-scatter, N=4 — every step, ALL four links carry one chunk:
   ┌───────────────────────────────────────────────────────────────┐
   │ RS t=0:  G0 ─c3→ G1   G1 ─c0→ G2   G2 ─c1→ G3   G3 ─c2→ G0  │
   │ RS t=1:  G0 ─c2→ G1   G1 ─c3→ G2   G2 ─c0→ G3   G3 ─c1→ G0  │
   │ RS t=2:  G0 ─c1→ G1   G1 ─c2→ G2   G2 ─c3→ G3   G3 ─c0→ G0  │
   └───────────────────────────────────────────────────────────────┘
     -> G0 owns S_c0, G1 S_c1, G2 S_c2, G3 S_c3  (full sums, sharded)
   all-gather, N−1 steps — count each chunk's redundant final lap home
   and you get N; that last hop re-delivers a chunk its owner has:
   ┌───────────────────────────────────────────────────────────────┐
   │ AG t=0:  G0 ─c0→ G1   G1 ─c1→ G2   G2 ─c2→ G3   G3 ─c3→ G0  │
   │ AG t=1:  G0 ─c3→ G1   G1 ─c0→ G2   G2 ─c1→ G3   G3 ─c2→ G0  │
   │ AG t=2:  G0 ─c2→ G1   G1 ─c3→ G2   G2 ─c0→ G3   G3 ─c1→ G0  │
   └───────────────────────────────────────────────────────────────┘
     -> every GPU owns [S_c0 S_c1 S_c2 S_c3]
```

**The mandatory arithmetic.** Each step every link carries one D/N chunk;
there are 2(N−1) steps:

- bytes per link = 2(N−1) × D/N = **2(N−1)/N × D**
- latency = **2(N−1) steps × L** — serialized: each hop waits on the last

The deep property: 2(N−1)/N → 2D as N grows, *regardless of N* — each
GPU's egress converges to "send every byte twice"; adding GPUs adds
FLOPs and memory but no per-GPU communication. Chan, Gropp and Thakur
(2007) proved (N−1)/N × D per link is the reduce-scatter lower bound;
the ring meets it in both phases: **bandwidth-optimal**. The price is
**latency-suboptimality** — 2(N−1) serialized hops. A flat ring at N =
25,000 means 50,000 hops, so real systems are hierarchical: an
intra-node NVLink ring of 8, then an inter-node ring whose members are
*nodes* (two media, one ring), collapsing 25,000 ranks to ~3,100 ring
members.

**Trees are the inverse trade.** A binomial reduce tree has latency
O(log N) — 14 steps at N = 16,384 — but the naive
gather-to-root-then-broadcast tree bottlenecks the root's links at
(N−1) × D in and out: per-link traffic **O(N × D)**, growing with N
exactly where the ring stays flat. **Recursive halving / double-tree**
fixes both: partners pair, the active set halves each step (log N steps,
each moving half the data), summing to the same 2(N−1)/N × D per link —
tree latency *and* ring bandwidth. The catch: halving partners are far
apart, the pattern is not ring-shaped, and the final steps move tiny
messages, re-entering the latency regime.

**The Pareto curve and NCCL's job.** Trees win below the crossover,
rings above, crossover ≈ N × L × B (≈ 400 KB at N = 8, L = 2 µs, B =
25 GB/s — the lab measures it). NCCL's tuner picks automatically,
modeling ring, double-tree and CollNet against the *detected* topology
and message size (pluggable, `NCCL_TUNER_PLUGIN`). Ring construction is
topology-aware — NVLink-first intra-node hops plus inter-node hops per
NIC pair, ordered same-rail, with multiple **channels** (parallel rings
splitting the tensor) engaging all 8 NICs. Launch cost is the
small-message killer (~5–20 µs per empty call), so CUDA graphs capture
the step's collectives into one replay (fixed sizes, padded) — launch
overhead and kernel count gone. The one-pass fusion: gradient-clip needs
a *global* sum of squares, so compute local partial norms,
reduce-scatter the scalar, and scale on the fly inside the
reduce-scatter you were already doing.

### Cheaper bits: quantized communication — active research, not default

The ring's bytes are fp32 gradients by default; compress them and the
same ring moves less. Quantization is *biased* — 8-bit rounding
underweights small gradients — and the fix is **error feedback**: keep
the residual (g − Q(g)) in an fp32 side tensor and add it into the next
gradient before quantizing again. The error is not lost, only delayed,
and still converges under bounded conditions (Stich et al. 2018). The
residual is one gradient-sized buffer per GPU — the memory price of the
bandwidth win.

| scheme | bytes | end-to-end win | convergence cost |
|---|---|---|---|
| fp32 | 4 B/elem | baseline | none |
| bf16 cast | 2 B/elem | ~2×, free | negligible — the default |
| 8-bit + error feedback | 1 B/elem | ~2× (4× raw, minus encoding) | ~0.1% accuracy or extra epochs |
| 1-bit + error feedback | 0.125 B/elem | up to ~10× | task-dependent |

Honesty line: the bottom two rows are **active research, not defaults**
(QSGD, Alistarh et al. 2016; 1-bit SGD, Seide et al. 2014; 1-bit SGDM, Xu
et al. 2021). Frontier runs ship bf16; 8-bit rings deploy where the
fabric binds, with the 0.1%-class cost re-measured per task.

## The RDMA underbelly: the NIC is a DMA engine

Module 13 chapter 05 built the NIC's DMA engine; RDMA points it at
another machine's memory with one-sided verbs —
`ibv_post_send(IBV_WR_RDMA_READ/WRITE)` carrying (remote address, length,
**rkey**). The remote CPU is not consulted, not interrupted, may not
notice. The machinery, each piece load-bearing:

- **Registration**: DMA needs pinned pages (a fault would move data under
  the NIC's translated addresses), so memory is registered
  (`ibv_reg_mr`), yielding an **lkey** and a remote **rkey** — a
  capability passed out-of-band like a file descriptor: module 13 ch05's
  protection domain, now guarding gigabytes.
- **Queue pairs**: each connection is a QP — a send ring and a receive
  ring of pre-posted buffers, module 13 ch01's descriptor ring a final
  time. NCCL posts its rings once at init and rotates through them for
  the whole run; completions land in completion queues.
- **GPU-direct RDMA**: the NIC DMAs *HBM directly* over peer-to-peer PCIe
  — no CPU, no host staging, no bounce buffer (the GPU driver pins
  device memory; the NIC walks it through the BAR). This is why NCCL's
  gradients never transit host DRAM.

**"Remote memory as a NUMA node that is very far away"** is exact: RDMA
READ is a load from a node at ~1–2 µs with *no coherence* — those last
three words are the difference from CXL, which gives a coherent shared
address space (a true NUMA node, µs-class, short reach). RDMA's message
semantics have no cache-line ownership, no invalidation traffic, no
distance limit — coherence does not scale to 25,000 GPUs; messages do.
The same argument explains why collectives beat PGAS (UPC, Chapel, X10):
four calls whose ordering the library can reason about, versus
one-sided generality that pushes consistency back onto the programmer.

**Polling: the busy-wait core.** At a 1–2 µs budget, interrupt entry
(~10s of µs) is unaffordable, so a dedicated NCCL progress thread spins
on the completion queue at 100% of one core — watts traded for
microseconds, module 09's spinlock trade at fabric scale. Below it,
kernel bypass: verbs DMA straight into user memory, while kernel TCP
(~20–50 µs) pays syscalls, copies, and a scheduler crossing each way —
module 13 ch04's syscall-elimination argument as the network's own
ladder.

## The synchrony problem: 25,000 clocks, one step

Synchronous data-parallel training is lockstep: compute gradients,
all-reduce, apply — and **the step time is the slowest GPU's time**, the
all-reduce being a barrier in disguise. Straggler sources, each a
chapter-mate: data-loader stall (this chapter's storage tier), page-cache
miss (module 01 ch03), NUMA-remote HBM staging (the insiders trap),
thermal throttle, an ECC scrub pausing one GPU, noisy neighbor, fabric
congestion — and **incast**: the reduce step's all-to-one moment when
thousands of writers converge, switch buffers overflow, and the
completion tree collapses exactly when the collective needs it.

**Tail amplification is the killer arithmetic.** Give each GPU a 0.1%
chance of a 2× slow step (thermal, congestion). Expected stragglers per
step: 25,000 × 0.001 = **25, every step**. P(no straggler) =
0.999²⁵⁰⁰⁰ ≈ e⁻²⁵ ≈ 10⁻¹¹ — the fleet *always* runs at the tail; a
per-GPU p99.9 of 2× median becomes the fleet's median step. The max of
25,000 samples is the mean of the far tail, permanently.

**The mitigation ladder**, weakest to strongest assumption:

1. **Redundancy / speculative replay** — recompute the straggler's work
   on a spare; the cost is idle duplicate FLOPs.
2. **Staleness** — proceed with a stale gradient, reconcile later;
   async-Adam tolerates ~1-step staleness nearly free.
3. **Local SGD, N-step sync** — each GPU runs k micro-steps on local
   weights, then averages: communication drops 1/k, read by the math
   as a k-fold effective batch at coarser synchronization; convergence
   degrades gracefully with k.
4. **1-bit SGDM's window** — with momentum compensation, quantized
   *stale* gradients buffered over 16–32 steps still converge: "the
   gradient can be stale if it's cheap" (Xu et al. 2021).
   Differential-privacy training tolerates a similar 16–32 step buffer —
   an odd convergence of literatures.

All four encode one insight: **synchrony is a convention the math
tolerates breaking** — SGD's own noise already dominates one step of
asynchrony.

**Batch, lr, and the memory connection.** Doubling the batch doubles the
gradient's signal-to-noise, so the **linear scaling rule**: batch × k ⇒
lr × k, empirically stable to batch 8192 on ImageNet (Goyal et al. 2017)
— *with warmup* (ramp lr over ~5 epochs; early noisy gradients blow up
at high lr). Gradient-noise-scale theory (McCandlish et al. 2018) sets
the **batch-size ceiling**: past the noise scale, bigger batches buy
nothing — the wall that stops "just add GPUs." LAMB/LARS push further by
scaling per-layer, adaptively. The quip "the batch is a memory unit
too," made precise: activations scale with the *micro-batch* (ch02's
arithmetic — one saved tensor per layer, sized by the batch dimension),
while the global batch is **micro-batch × accumulation steps × N GPUs**.
Gradient accumulation runs k forward/backward passes accumulating
locally and all-reduces *once per window*: global 2048 = micro-batch 4 ×
accumulation 8 × 64 GPUs, with only micro-batch-4 activation memory per
GPU and one eighth the communication of reducing every pass. Two-level
memory: activations sized by what fits HBM, effective batch sized by
arithmetic.

## The tower closed: checkpoints and the 1980s inversion

The module's argument in one final table — module 01 ch02's tower,
closed:

| level | capacity (typ) | latency | bandwidth | where |
|---|---|---|---|---|
| registers | ~256 B/core | ~0.3 ns | ~30 TB/s | module 17 |
| L1–L3 SRAM | 50–100 MB/pkg | 1–40 ns | 5–20 TB/s | module 01 ch02 |
| HBM | 80–192 GB/GPU | ~0.5 µs | 3–3.4 TB/s | module 01 ch12 |
| node DRAM | 1–2 TB | ~100 ns | 0.1–0.5 TB/s | module 01 ch02 |
| the fabric | 25k GPUs ≈ 3–4 PB | 1–50 µs | 50 GB/s/NIC, rail-bound | this chapter |
| parallel FS | PBs | ~ms | ~1 TB/s aggregate | module 14 |
| cold tier (S3-class) | exabyte-class | 10s of ms | ~12.5 GB/s cross-region | module 15 |

**The data path.** Data streams from sharded datasets
(webdataset/parquet), never fully resident. The full shuffle is the
memory problem: ~10¹³ tokens ≈ 2³³ 4-KB documents, and a true permutation
of 2³³ 8-byte indices is 64 GB — too big and pointless. Distributed
shuffle is two-level: permute the *shards* (a 2¹²-scale index), then
shuffle locally within each streamed shard; the object store delivers
~100 GB/s aggregate read.

**Checkpoint economics.** A ~500B-parameter mixed-precision Adam model
carries ~16 bytes/param of state (bf16 weights + bf16 grads + fp32 master
+ two fp32 moments) ≈ **8 TB**. Raw fabric arithmetic — 8 TB ÷
(N_rails × 50 GB/s) — suggests ~10 s, but the parallel FS's ~1 TB/s is an
*aggregate* shared with every other writer, per-writer streams land at
5–10 GB/s, and metadata bites: the burst-write realistically takes
**minutes**. The failure arithmetic is why a cadence exists: per-GPU
MTBF ~5 years across 25,000 GPUs ⇒ 25,000/5 = 5,000/year = **~14
failures/day**, one every ~105 minutes. Checkpoint every 30 minutes ⇒
each failure loses ≤ 30 minutes of a months-long run — and the lab below
*derives* that optimum from write time and failure rate rather than
asserting it. Elastic restart re-shards state across whatever GPUs the
scheduler produces — which is also the preemptible/spot economics:
interruptible capacity is viable exactly when checkpoint-restore is fast
relative to the price discount. A rail's ToR switch, a rack PDU, a
spine: each a failure domain whose blast radius the restore path must
outrun.

**The historical inversion — the module's closing identification.** A
1980s computer ran a too-big-for-RAM program by paging: working set in
RAM, the rest on spinning disk, 4-KB pages moved on fault. The datacenter
trains a too-big-for-anywhere model the same way:

```
   1980s: program too big for RAM      2020s: state too fragile for one
   (module 01 ch03)                     machine's lifetime
   ┌────────┐ working set          ┌────────┐ live shards = the step's
   │  CPU   │──> in RAM            │  GPU   │──> working set (weights +
   └────────┘                      └────────┘    optimizer moments)
       │ page fault                    │ step boundary / failure
       v                               v (fleet-wide, ~every 105 min)
   ┌────────┐ swap out 4 KB       ┌────────┐ checkpoint: burst-write
   │  RAM   │<────> disk          │  HBM   │<────> 8 TB state
   └────────┘ swap in             └────────┘       (parallel FS ~1 TB/s)
       │                               │
       v                               v
   ┌────────┐ archive                ┌────────────┐ cross-region restore:
   │  tape  │                        │ S3-class   │ 100 Gbps = 8 TB in
   └────────┘                        └────────────┘ ~11 minutes
   same shapes, 10^9x the bytes: 4-KB page <-> ~GB shard,
   fault-on-touch <-> restore-on-failure, thrashing <-> checkpointing
   faster than the FS absorbs
```

Swap-in-on-fault maps to restore-on-failure; dirty pages are the modified
optimizer moments; the backing store is the parallel FS. The
identification predicts behavior: as 1980s swap made disk bandwidth a
first-class compute resource, the checkpoint tier makes the parallel
filesystem part of the training loop.

**The honest throughput number.** The de-facto fleet metric is
tokens/day/GPU: ~2,000 tokens/s/GPU effective on well-run frontier
training (30–50% MFU — straggler tax, communication fraction, occupancy,
checkpoint pauses all subtracted). 25,000 × 2,000 ≈ 5×10⁷ tok/s ≈
4×10¹² tokens/day — order-of-magnitude only; public run totals (10¹³-class
budgets over months) are inferred from power and hardware disclosures,
never measured for you. The world's largest programs are measured in
tokens.

## What insiders know

- **`NCCL_DEBUG=INFO` is your first distributed debug session, decoded.**
  NCCL prints the detected topology (NVLink/PCIe adjacency as XML), then
  the *actual ring construction* — `Channel 00: 0→1→…→7→15→…→8→0` with
  the medium annotated (`via P2P/NVLink`, `via NET/IB`), the channel
  count (parallel rings; more channels = more NICs), and the
  algorithm/proto choice per collective size. A hang plus this log
  answers "which rank waits on whom": the ring order gives each rank's
  expected first neighbor.
- **The `CUDA_VISIBLE_DEVICES` reordering trap.** The OS enumerates GPUs
  in PCIe bus order, not NVLink-physical order; if ranks disagree about
  the mapping, NCCL silently builds a cross-socket ring and host-staged
  copies land in remote-node DRAM — "GPU 3 is on the other socket," the
  30% slowdown that masquerades as a fabric problem. Read
  `nvidia-smi topo -m`: `NV#` = NVLink hops (good), `PIX` = same PCIe
  switch (fine), `SYS` = cross-socket via UPI (the killer for staging
  traffic). Fix: `numactl --cpunodebind=N --membind=N`, `taskset`, and
  let NCCL pin its progress thread.
- **Fabric-fragility diagnosis: incast, PFC, ECN.** RoCE demands a
  "lossless" fabric — PFC pause frames — and under incast the pauses
  cascade into a **PFC storm**: the fabric livelocks while counters show
  *zero packet loss*, which is why it fools people. Diagnosis:
  pause-frame and ECN-marked counters climbing in lockstep with the
  reduce step, per-NIC throughput collapsing to ~0 then recovering,
  `ibdump` captures. Tuned fix: ECN thresholds + DCQCN back-off. Insider
  reading: the cluster's congestion window is a memory hierarchy's
  admission control — backpressure deciding who gets buffer.
- **Failure domains and geographic humility.** One rail's ToR is a
  single blast radius — it takes the same GPU index on every node in the
  block, survivable only via elastic re-shard. A same-region checkpoint
  is not a backup: cross-region restore runs at backbone speed, 100 Gbps
  = 12.5 GB/s = **8 TB in ~11 minutes** — not a heartbeat. Hence the
  fire-drill discipline: periodically restore for real, in another
  region, on a different node count, or your checkpoints are a belief,
  not a recovery plan.
- **The throughput honesty.** ~2,000 tok/s/GPU and 30–50% MFU are the
  *good* numbers; a 90% MFU claim is measuring the compute microbenchmark,
  not the job with its stragglers, communication, and checkpoint pauses.
  When someone quotes fleet throughput, ask what was subtracted.

## See it yourself

**Lab 1 — the ring, simulated to the chunk (CPU, ~100 lines).** Every
step logged, bytes per link checked against the formula, then sweep
message size to watch the two regimes trade places:

```python
# ring_ar.py -- ring all-reduce simulated down to the chunk.  demo | sweep
import sys

def demo(n=4, chunk=8):
    # buf[g][c] = the partial sum GPU g holds for chunk c.
    # GPU g contributes g+1 to every chunk; true total = N(N+1)/2 = 10.
    buf = [[g + 1 for _ in range(n)] for g in range(n)]
    link = [0] * n                    # link[i] = bytes on GPU i -> (i+1)%n
    def phase(tag, rule, steps, fold):
        for t in range(steps):
            msgs = []                 # gather all sends first: the links
            for i in range(n):        # run simultaneously
                c = rule(i, t) % n
                msgs.append((i, (i + 1) % n, c, buf[i][c]))
                link[i] += chunk
            for s, d, c, v in msgs:
                buf[d][c] = buf[d][c] + v if fold else v   # add vs store
            print(f"  {tag}{t}: " + "  ".join(
                f"G{s}->G{d} c{c}={v:g}" for s, d, c, v in msgs))
    data = n * chunk
    print(f"ring all-reduce, N={n}: data={data}B = {n} chunks x {chunk}B")
    phase("RS", lambda i, t: i - 1 - t, n - 1, True)   # send (i-1-t)%N
    print(f"  -> GPU i owns the FULL sum of chunk i: "
          f"{[buf[i][i] for i in range(n)]}")
    phase("AG", lambda i, t: i - t, n - 1, False)      # send (i-t)%N
    ok = all(v == n * (n + 1) // 2 for r in buf for v in r)
    print(f"  -> the total everywhere: {ok}; steps = {2*(n-1)};"
          f" bytes/link = {link[0]} (theory 2(N-1)/N*D = {2*(n-1)*data//n})")

def sweep(n=8, L=2e-6, B=25e9):
    # t(D) = 2(N-1)L + 2(N-1)/N*D/B: latency regime vs bandwidth regime
    for D in (1e3, 4e4, 4e5, 4e6, 4e8, 4e9):
        lat, bw = 2 * (n - 1) * L, 2 * (n - 1) / n * D / B
        print(f"  {D:>8.0e}B: latency {lat*1e6:5.0f}us  "
              f"bandwidth {bw*1e6:8.0f}us  "
              f"{'latency' if lat > bw else 'bandwidth'}-bound")
    print(f"crossover ~ N*L*B = {n*L*B:.3g}B: trees below, rings above")

if __name__ == "__main__":
    sweep() if "sweep" in sys.argv else demo()
```

```console
$ python3 ring_ar.py demo
ring all-reduce, N=4: data=32B = 4 chunks x 8B
  RS0: G0->G1 c3=1  G1->G2 c0=2  G2->G3 c1=3  G3->G0 c2=4
  RS1: G0->G1 c2=5  G1->G2 c3=3  G2->G3 c0=5  G3->G0 c1=7
  RS2: G0->G1 c1=8  G1->G2 c2=7  G2->G3 c3=6  G3->G0 c0=9
  -> GPU i owns the FULL sum of chunk i: [10, 10, 10, 10]
  AG0: G0->G1 c0=10  G1->G2 c1=10  G2->G3 c2=10  G3->G0 c3=10
  AG1: G0->G1 c3=10  G1->G2 c0=10  G2->G3 c1=10  G3->G0 c2=10
  AG2: G0->G1 c2=10  G1->G2 c3=10  G2->G3 c0=10  G3->G0 c1=10
  -> the total everywhere: True; steps = 6; bytes/link = 48 (theory 2(N-1)/N*D = 48)
```

The RS trace is the derivation made executable: chunk c=0 walks
G1→G2→G3→G0 collecting 2, then 5, then 9 — each step's value is the sum
so far. Run `sweep`: the latency term (28 µs, flat) loses to the
bandwidth term at 4×10⁵ B — the crossover where trees hand off to rings.

**Lab 2 — the topology-aware ring on paper.** Two nodes, 8 GPUs each, one
NIC per GPU, GPU i's NIC on rail i (GPU 8's NIC is on rail 0 … GPU 15's
on rail 7). Naive ring in index order `0-1-…-7-8-…-15-0`: fabric hops
7→8 and 15→0 with senders on rail 7 and receivers on rail 0 — both cross
the spine: 2 cross-rail hops at ~5 GB/s, the ring bottlenecked at ~5
GB/s. NCCL's ring `0-1-…-7-15-14-…-8-0`: fabric hops 7→15 (both NICs on
rail 7 — one ToR, no spine) and 8→0 (both on rail 0): 2 same-rail hops
at ~50 GB/s. Same number of fabric crossings, **10× the effective
bandwidth** — the entire content of "topology-aware ring construction."
Draw both, count cross-rail links (2 vs 0), compute each bottleneck.

**Lab 3 — the MTBF/checkpoint calculator.** Sweep the interval, find the
optimum, discover the 30-minute rule is derived, not decreed:

```python
# mtbf.py -- overhead(T) = W/T + lam*(T/2 + R): checkpoint cost + expected
# lost work per failure (uniform failure position, restart R).
import math
GPUS, MTBF_YR, W, R = 25_000, 5.0, 240.0, 900.0
lam = GPUS / (MTBF_YR * 365.0 * 86400.0)
print(f"lam = {lam:.2e}/s = {lam*86400:.1f}/day "
      f"(mean time between failures {1/lam/60:.0f} min)")
for T in (300, 600, 900, 1800, 3600, 7200):
    ck, fl = W / T, lam * (T / 2 + R)
    print(f"T={T:>5.0f}s  ckpt {ck*100:>6.2f}%  loss {fl*100:>6.3f}%  "
          f"total {(ck+fl)*100:>6.2f}%")
print(f"optimum T* = sqrt(2W/lam) = {math.sqrt(2*W/lam)/60:.1f} min "
      f"(R shifts loss, not T*)")
```

```console
$ python3 mtbf.py
lam = 1.59e-04/s = 13.7/day (mean time between failures 105 min)
T=  300s  ckpt  80.00%  loss 16.648%  total  96.65%
T= 1800s  ckpt  13.33%  loss 28.539%  total  41.87%
T= 7200s  ckpt   3.33%  loss 71.347%  total  74.68%
optimum T* = sqrt(2W/lam) = 29.0 min (R shifts loss, not T*)
```

Plot total vs T: a U with its floor at ~30 min — 300 s thrashes the
filesystem (96% overhead), 2 h donates an hour per failure. W is the knob
you can buy down (more rails, faster FS).

**Lab 4 — the ladder's bottom rung, measured.** Two processes ping-pong 8
bytes over localhost TCP, then over a shared mmap:

```python
# rtt.py -- 8-byte ping-pong: localhost TCP vs shared memory.  tcp | shm
import socket, sys, time
from multiprocessing import Process
N, PING, ACK = 5000, 0, 8

def tcp_pair():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    def echo():
        c, _ = srv.accept()
        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # no Nagle!
        for _ in range(N):
            c.recv(8); c.send(b"X" * 8)
    p = Process(target=echo); p.start()
    c = socket.socket()
    c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    c.connect(("127.0.0.1", srv.getsockname()[1]))
    t0 = time.perf_counter()
    for _ in range(N):
        c.send(b"X" * 8); c.recv(8)
    print(f"TCP 127.0.0.1: {(time.perf_counter()-t0)/N*1e6:.1f} us/RTT")
    p.join(10)

def shm_pair():
    import mmap as M
    page = M.mmap(-1, 4096)      # anonymous MAP_SHARED, fork-inherited
    def pong():
        seen = 0
        for _ in range(N):
            while page[PING] == seen:   # busy-wait = the NIC polling model
                pass
            seen = page[PING]; page[ACK] = seen
    p = Process(target=pong); p.start()
    t0 = time.perf_counter()
    for i in range(N):
        flag = (i % 254) + 1     # never 0: the zeroed page would fake the
        page[PING] = flag        # first ACK and desync the pair
        while page[ACK] != flag:
            pass
    print(f"shared mmap: {(time.perf_counter()-t0)/N*1e6:.2f} us/RTT")
    p.join(10)

if __name__ == "__main__":
    (tcp_pair if "tcp" in sys.argv else shm_pair)()
```

Measured on one laptop-class machine: **~17 µs/RTT over TCP vs ~0.6 µs
over shared memory** — 20–30×. The chapter's TCP-vs-verbs gap (~20–50 µs
vs ~1–2 µs) is this experiment with the kernel in the path. Honest
caveat: localhost TCP is a *model* of the fabric — no wire, no switch
queueing — good for the ratio, not the absolute. Note the two encoded
traps: without `TCP_NODELAY` you measure Nagle's 40 ms, and the
zero-filled page fakes your first ACK if you let it — zero-initialized
memory lying to you is a module-wide motif.

## How to use this knowledge

- **Your first distributed hang**: `NCCL_DEBUG=INFO`, read the ring
  construction lines, find which neighbor rank 0's channel 0 waits on —
  the dependency order, not a guess at "the network."
- **A 30% node-level regression**: `nvidia-smi topo -m` before anything
  else; `SYS` between your GPU and its NIC explains host-staging through
  the wrong socket, and `numactl` fixes what looks like a fabric bug.
- **Sizing a cluster buy**: occupancy arithmetic backward — 25,000
  usable rail-aligned GPUs at 70% occupancy is ~36,000 installed; quote
  that, and module 17 ch05's economics are yours to defend.
- **Ring vs tree vs accumulation for a job**: compute the crossover
  N × L × B, compare your gradient tensor, and you know which side of
  the Pareto curve you live on before profiling.
- **The interview answer to "why is distributed training slow?"**: the
  max-of-25,000 straggler arithmetic — one sentence, and almost nobody
  gives it.

## Connections

- **Same module**: ch01 (the ring moves contiguous tensor slices, so
  layout is chunk bandwidth); ch02 (activation arithmetic sets the
  micro-batch that accumulation multiplies); ch03 (ZeRO's sharded state
  is *why* reduce-scatter/all-gather is the training loop's real shape);
  ch04 (paged KV and checkpoints are the same paging idea on different
  tiers).
- **Module 13 ch01/ch05** (mandated): the descriptor ring and DMA engine
  return as NCCL's QP send/recv rings and RDMA's one-sided verbs at
  fabric scale — this chapter is their payoff.
- **Module 01 ch02** (mandated): the final tower, closed here with three
  new floors. **Module 01 ch03**: the swap diagram inverted here as the
  closing identification.
- **Module 17 ch05** (mandated): the silicon economics that cap the
  fleet — the 60–80% occupancy and capacity arithmetic are its
  datacenter application.
- **Module 09 ch03** (mandated): lock-free lessons — the ring's
  single-writer/single-reader slots are the SPSC queue at petabyte
  scale; the polling progress thread is the spinlock trade.
- **Module 15 ch03** (the vring's hypervisor cousin), **module 11 ch04**
  (WAL/checkpoint durability logic returns in the fire drill).

## Quiz

1. **Concept.** Explain precisely why the ring all-reduce is
   bandwidth-optimal: what quantity stays bounded as N grows, what lower
   bound does it meet, and what does the ring pay in exchange?
2. **Concept.** "The network is a shared cache with weird associativity."
   Identify the set-index function, the conflict-miss path, and the
   allocator whose placement policy sets the hit rate.
3. **Derive (the mandatory one).** N = 8 GPUs, a 1 GiB gradient tensor,
   per-link bandwidth 25 GB/s, per-link latency 2 µs. Derive the ring's
   bytes per link, step count, and completion time; then the naive
   star's (gather to root, broadcast from root) time at the root's
   single 50 GB/s NIC. What happens to each as N doubles?
4. **Derive.** A fleet of 40,000 GPUs, per-GPU MTBF 4 years, checkpoint
   write W = 180 s, restart R = 20 min. Compute failures/day, the
   optimal checkpoint interval, and expected work lost per failure at
   that interval.
5. **Derive.** 64 GPUs, activation memory fits micro-batch 4, target
   global batch 2048. Compute the accumulation steps, what scales with
   the accumulation count and what does not, and when the all-reduce
   fires.
6. **Diagnose.** An 8-GPU node trains 30% below expected
   tokens/day/GPU. `nvidia-smi topo -m` shows `SYS` between GPU 3 and
   the node's NIC, and between GPU 3 and the CPU running its rank. What
   is the mechanism, and the fix?
7. **Diagnose.** A 2,048-GPU run stalls at random steps; per-NIC
   throughput collapses to ~0 then recovers; the switch shows pause-frame
   and ECN counters climbing together and *zero* packet loss. What
   happened, and why does the "lossless" design cause it?
8. **Cross-chapter.** Map module 01 ch03's swap machinery onto this
   chapter's checkpoint pipeline: page fault → ?, backing store → ?,
   dirty page → ?, working set → ?, thrashing → ?. Then name the module
   13 ch01 structure NCCL's pre-posted per-channel buffers reuse.

<details>
<summary>Answers</summary>

1. **Per-link bytes = 2(N−1)/N × D → 2D as N grows.** Each GPU's egress
   converges to "send every byte twice," so adding GPUs adds FLOPs and
   memory but no per-GPU communication. Chan, Gropp and Thakur (2007)
   proved (N−1)/N × D per link is the reduce-scatter lower bound; the
   ring meets it in both phases. The exchange: latency = 2(N−1)
   serialized steps — O(N) hops, hopeless for small tensors at large N,
   hence trees and hierarchy.
2. The set-index function is the **GPU-index-to-rail mapping**: same-index
   GPUs share a rail — a direct-mapped hit, one ToR, ~50 GB/s. The
   conflict-miss path is cross-rail traffic forced through the
   oversubscribed spine (2:1–8:1) at ~5 GB/s. The allocator is the
   **scheduler's topology-aware gang placement**; its fragmentation is
   the fleet's 60–80% occupancy — stranded FLOPs are internal
   fragmentation.
3. Ring: bytes/link = 2×7/8 × 1 GiB = **1.75 GiB**; steps = **14**; time
   = 1.75 GiB ÷ 25 GB/s + 14 × 2 µs ≈ 74.6 ms + 28 µs ≈ **74.7 ms**.
   Star: the root's one NIC moves 7 GiB in + 7 GiB out at 50 GB/s ≈
   **301 ms**. Doubling N: the ring stays flat (2×15/16 × D ≈ 78.9 ms);
   the star's root link grows to 30 GiB → ~644 ms — the O(N × D)
   per-link cost the ring eliminated.
4. λ = 40,000/4 = 10,000/yr = **27.4/day**, one every ~53 min. T\* =
   √(2W/λ) = √(2×180 ÷ 3.17×10⁻⁴) ≈ 1,066 s ≈ **18 min** (R shifts the
   loss curve, not T\*). Expected lost work per failure = T\*/2 + R ≈ 9
   + 20 = **29 min**.
5. Accumulation = 2048 ÷ (4 × 64) = **8 steps**. Scales: global batch and
   compute per optimizer step. Does not: activation memory (still
   micro-batch 4 — ch02's per-tensor accounting), weights, optimizer
   state, and the all-reduce count — it **fires once per window**, at
   the optimizer step, on the accumulated gradient.
6. Mechanism: rank 3's GPU sits on the other socket from its NIC and CPU;
   every host-staged byte crosses UPI and lands in remote-node DRAM —
   `SYS` is a ~2× staging penalty compounding to ~30%. Fix: reorder with
   `CUDA_VISIBLE_DEVICES` so ranks and NICs share a socket (PCIe order
   is not NVLink order — check, don't assume), then pin with
   `numactl --cpunodebind=N --membind=N`.
7. **Incast-triggered PFC storm.** At the reduce step thousands of
   writers converge; switch egress buffers fill; RoCE's lossless
   requirement makes the switch pause upstream ports (PFC) rather than
   drop; pauses cascade backward and the collective livelocks — the
   completion tree collapses. "Lossless" *causes* it: dropping would
   trigger retransmission, but a paused fabric shows zero loss while
   moving zero data — the diagnosis lives in the pause/ECN counters, not
   the loss counters. Tuned fix: ECN thresholds + DCQCN back-off —
   admission control for the buffer hierarchy.
8. Page fault → **failure detection** (a rank dies; the job faults as one
   program). Backing store → **the parallel filesystem** (8 TB state, ~1
   TB/s aggregate, minutes-scale). Dirty page → **the optimizer moments
   modified since the last checkpoint**. Working set → **the live shards
   in fleet HBM**. Thrashing → **checkpointing faster than the FS
   absorbs** (the lab's 300 s row: 96% overhead). NCCL's pre-posted
   per-channel buffers are **module 13 ch01's descriptor ring** — posted
   once at init, rotated for the whole run, completions polled rather
   than interrupted.

</details>
