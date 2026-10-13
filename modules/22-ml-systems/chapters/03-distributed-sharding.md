# Chapter 03 — Distributed training: sharding the unshardable

> **The 30-second version.** A frontier model's training state does not
> fit in any one GPU's memory — not by 17×, not by two orders of
> magnitude — and yet it trains. The entire discipline of distributed
> training is four different answers to one question: *what do we
> shard?* Data parallel replicates the model and shards the data (the
> first answer, the one that dies first). ZeRO shards the optimizer
> state, then the gradients, then the parameters themselves (the
> "none of the replicas needs to hold the whole thing" answer). Tensor
> parallel splits individual matmuls across GPUs (the "no single
> multiply is too big" answer). Pipeline parallel splits the layer
> stack (the "each GPU owns a slice of the depth" answer). Compose
> them and a 1-trillion-parameter object — 8 terabytes of training
> state — becomes a choreography over 25,000 GPUs that behaves, when
> it works, like one very large computer with a very large heap.

## The problem, stated in bytes

Start with the arithmetic, because everything in this chapter is a
consequence of one multiplication. A model with P parameters in
mixed-precision training carries state far beyond the weights:

```
weights (fp16/bf16)     2P bytes   — the model itself
gradients (fp16/bf16)   2P bytes   — one per parameter, during backward
optimizer m, v (fp32)   8P bytes   — Adam's two moments, in master precision
master weights (fp32)   4P bytes   — the fp32 copy the optimizer updates
------------------------------------------------------------------------
training state         ~16P bytes  (the honest 16P; the folklore "3×" is
                                     weights-only and hides the master copy)
```

Plug in the numbers. A 175-billion-parameter model (the GPT-3 class):
16 × 175e9 ≈ **2.8 TB** of training state — call it 1.4 TB if you
drop the master weights and run the optimizer in 16-bit (the
industry's actual compromise). One A100 holds 80 GB. That is a **17×
gap** before you allocate a single activation. A 1-trillion-parameter
frontier model: ~8 TB of state. A 25,000-GPU fleet holds ~2 PB of
aggregate HBM — the model's state is 0.4% of the fleet's memory. The
naive approach — replicate everything on every GPU, like plain data
parallel does — achieves that 0.4% utilization for the training state
and dies at the first `torch.cuda.oom`. The sharding imperative is
not a fashion; it is division.

The table that drives the whole chapter:

| Model | State (fp16 opt) | One A100 (80 GB) | 25k-GPU fleet (2 PB) |
|---|---|---|---|
| 7B | ~84 GB | just over — DP marginal | 0.004% |
| 70B | ~840 GB | 10× over — DP dead | 0.04% |
| 175B | ~2.1 TB | 26× over | 0.1% |
| 1T | ~12 TB | 150× over | 0.6% |

Every technique below is a different row of the same division problem.

## Answer one: data parallel — replicate the model, shard the data

Data parallel (DP) is the architecture you get by default: every GPU
holds a complete replica of the model, each takes a different mini-
batch, and the gradients are synchronized after backward. The
communication pattern is one **AllReduce** per step: each GPU ends up
with the mean of all gradients. For a model of P parameters in fp16,
that is 2P bytes moved per GPU per step — chapter 05 derives why the
ring's cost works out to 2P×(N−1)/N per link, and why this is
bandwidth-bound, not latency-bound, for large models.

The memory story of DP has two acts. First, the gradient
synchronization overlaps with backward computation: DDP buckets
gradients into groups (default 25 MB) and launches the reduction of
bucket k while bucket k+1 is still being computed by the matmuls —
the copy-engine stream and the compute stream run concurrently, two
CUDA streams doing what module 12 chapter 01 called stream
concurrency. A well-shaped model hides most of the communication this
way; a badly-shaped one (one giant bucket at the end) exposes all of
it. Second, and fatally: DP's memory floor is the *full* training
state on *every* GPU. It works beautifully to ~10B parameters on
80-GB parts, and then it doesn't work at all. There is no partial
credit. The first wall everyone hits in this field is this one, and
the error message is the now-legendary `CUDA out of memory: Tried to
allocate ...` — the same heap-exhaustion exception from module 01
chapter 05, one level up the stack.

## Answer two: ZeRO — shard the state itself

ZeRO (Zero Redundancy Optimizer) makes the observation that DP's
replication is *redundant by construction*: if every GPU in the
AllReduce ends up with the same averaged gradient, then the optimizer
state that consumes that gradient could just as well be sharded, with
each GPU updating its slice and the results broadcast when needed.
ZeRO is three stages, each sharding one more component of the 16P:

**Stage 1 — shard the optimizer state.** The Adam m and v (and the
fp32 master copy) are split N ways. Each GPU updates only its 1/N
slice of the parameters after the gradient AllReduce. Communication
changes from AllReduce to **AllGather** of the updated parameters —
roughly the same volume. Memory: the state drops from 16P to
(2P + 2P + 12P/N) per GPU.

**Stage 2 — also shard the gradients.** There is no reason for every
GPU to receive the full averaged gradient if it only owns 1/N of the
optimizer. Replace the gradient AllReduce with a **ReduceScatter**:
each GPU receives only the gradient slice it needs. Memory: 2P +
(2P + 12P)/N per GPU.

**Stage 3 — also shard the parameters.** Now no GPU holds the model.
The weights are gathered layer-by-layer on demand: before the forward
pass of layer i, an AllGather delivers layer i's parameters (and
 prefetches layer i+1's while i computes). Backward gathers them
again. Memory: (16P)/N per GPU plus the transient gathered layer.

The stage table for the 175B example at N=64 (fp16 optimizer, state
~2.1 TB):

| Stage | Per-GPU memory at N=64 | Verdict |
|---|---|---|
| DP | ~2.1 TB (replicated) | impossible |
| ZeRO-1 | ~120 GB | still impossible on 80 GB |
| ZeRO-2 | ~90 GB | marginal — no room for activations |
| ZeRO-3 | ~25 GB | fits, with room for activations |

The honesty line that the ZeRO papers themselves draw: stage 3's
communication volume is ~1.5× that of plain DP (the parameter
AllGathers in forward *and* backward), so ZeRO-3 trades bandwidth for
memory. On a fast fabric this is a good trade; on a slow one it
quietly halves your throughput. There is no free sharding.

### ZeRO-Offload: build the tower yourself

When even the GPU fleet's aggregate HBM is not the right place for
everything, the DeepSpeed line extends the same idea downward through
the memory hierarchy: the optimizer state (the 12P that nobody touches
during forward/backward) lives in **CPU DRAM**, paged further to
**NVMe** for the truly enormous runs (DeepSpeed-Infinity streams at
~7 GB/s per NVMe device). The result is a three-tier pyramid —
HBM → DRAM → NVMe — that is *constructed in software*:

```
  the pyramid you build               the pyramid you were given
  (ZeRO-Offload, ~TB scale)           (module 01 ch02, ~GB scale)

  +-----------------------+           +--------+
  | HBM   ~80 GB/GPU      |           | L1     ~48 KB   ~4 cycles   |
  |  ~900 GB/s NVLink     |           +--------+
  +-----------------------+           | L2     ~2 MB    ~14 cycles  |
  | CPU DRAM ~1 TB/node   |           +--------+
  |  ~60 GB/s PCIe        |           | DRAM   ~16 GB   ~100 ns     |
  +-----------------------+           +--------+
  | NVMe   ~7 GB/s/device |           (the cache pyramid of module 01
  +-----------------------+            ch02 — the same shape, 10^9× the
                                       bytes, and this time YOU are the
                                       architect: the prefetcher, the
                                       page policy, and the bandwidth
                                       ratios are all your problem)
```

This diagram is the module's thesis in one picture: when silicon does
not hand you a hierarchy, you build one — and the shape you build is
the shape the hardware gave you at the small scale, because the physics
(capacity vs bandwidth vs latency) is the same physics. The PCIe link
between your tiers (~25–60 GB/s) plays the role DRAM-to-L3 used to
play; the prefetch discipline (gather layer i+1 while layer i computes)
plays the role of the hardware prefetcher. Engineers who internalized
module 01 chapter 02 read ZeRO-Offload code like a familiar dialect.

## Answer three: tensor parallel — split the matmul itself

The ZeRO family shards *state*, but the activations of a single layer
must still fit on one GPU at compute time. For the biggest layers (the
 vocabulary head: hidden × 128k vocab), even one matmul's working set
can be too large, and the bandwidth of fetching sharded weights can
dominate. Tensor parallel (TP, the Megatron approach) splits the
*computation*: a matmul Y = XW is partitioned along W's columns,
W = [W1 W2 … Wn], each GPU computes Yi = XWi on the *same* X (an
AllGather of X or a broadcast), and the partial results are combined.
For the transformer's attention, the split follows the natural grain:
heads. A 96-head attention layer across 8 GPUs gives each GPU 12
self-contained heads (queries, keys, values, and the per-head softmax
all live locally) — the QKV projections are column-split by head, the
output projection is row-split to make the AllReduce that follows the
mathematically correct sum.

The communication grammar is fixed: **two AllReduces per transformer
layer** (one after the attention block, one after the MLP block),
implemented as Megatron's f/g conjugate operators — f is identity in
forward and AllReduce in backward, g the mirror. Per layer the volume
is ~2 × (batch × seq × hidden) × 2 bytes × 2 directions — for the
biggest models, tens of MB per layer per GPU, forty-plus layers,
dozens of times per second. That bandwidth requirement is exactly
NVLink-shaped: 600–900 GB/s. This is why the design law of the field
exists: **tensor parallel stays inside the node; anything that spans
nodes rides the slow fabric and must be a different parallelism.**
Hierarchy-aware parallelism — match the communication pattern to the
topology — is the one-line summary of this entire chapter.

## Answer four: pipeline parallel — split the layers

Pipeline parallel (PP) assigns contiguous ranges of layers to
different GPUs: stage 1 owns layers 0–11, stage 2 owns 12–23, and the
activation at the boundary is a point-to-point send/recv. TP moves
weights-shaped traffic at NVLink speed inside a layer; PP moves one
activation-shaped message between layers, which is small (batch × seq
× hidden × 2 B — megabytes, not gigabytes) and happens once per layer
per micro-batch. That traffic profile tolerates the slow inter-node
fabric, so PP is the axis that *spans* nodes while TP stays home.

The catch is the bubble. A naive pipeline fills one micro-batch at a
time: while stage 4 computes on micro-batch 1, stages 1–3 are idle.
The fix is to cut each batch into M micro-batches and stream them:
the classic GPipe schedule runs all M forward passes through stage k
before any backward — every micro-batch's activation is simultaneously
in flight, and activation memory scales with M. The 1F1B (one-forward-
one-backward) schedule keeps only ~depth in flight: after a warm-up
of (N_stages − 1) forwards, each stage alternates forward/backward
one micro-batch at a time, so an 8-micro-batch 4-stage pipeline looks
like this (F = forward, B = backward, · = idle):

```
stage 1:  F1 F2 F3 F4 F5 F6 F7 F8 B1 B2 B3 B4 B5 B6 B7 B8
stage 2:     F1 F2 F3 F4 F5 F6 F7 F8 B1 B2 B3 B4 B5 B6 B7 B8
stage 3:        F1 F2 F3 F4 F5 F6 F7 F8 B1 B2 B3 B4 B5 B6 B7 B8
stage 4:           F1 F2 F3 F4 F5 F6 F7 F8 B1 B2 B3 B4 B5 B6 B7 B8
          |<- warm-up ->|<-------- steady state -------->|<- drain ->|
```

Read the warm-up and drain regions: that idle time is the bubble. Its
fraction of the step is

**bubble = (N_stages − 1) / (M_micro + N_stages − 1)**

— derive it by staring at the diagram: stage 1 finishes its last
backward B8 exactly (M + N − 1) slots after it started F1, of which
only M slots per stage do real work in steady state. At 4 stages and
8 micro-batches: 3/11 ≈ 27% idle. At 8 stages and 8 micro-batches it
would be 7/15 ≈ 47% — the bubble grows with depth, so deep pipelines
need many micro-batches to amortize it. The interleaved variant
(virtual pipeline stages: each device holds several *discontiguous*
slices of the model, e.g. layers 0–3 and 32–35) halves the bubble at
the cost of more, smaller messages. Every production PP scheduler is
a variation on this one diagram.

## The five axes, placed on the topology

The full modern recipe names five parallelism axes, and each one has a
natural home in the hardware:

| Axis | What it splits | Traffic shape | Where it lives |
|---|---|---|---|
| DP | the data | gradient AllReduce, 2P/step | anywhere (slow fabric OK) |
| ZeRO | the training state | AllGather/ReduceScatter | anywhere |
| TP | individual matmuls | 2 AllReduces/layer, latency-tight | intra-node (NVLink) |
| PP | layer ranges | activation send/recv, sparse | inter-node OK |
| CP | the sequence dimension | ring AllReduces over seq | intra-node |
| EP | the MoE experts | all-to-all dispatch | anywhere with bandwidth |

A frontier training run is a 3-D (really 5-D) mesh: 8 GPUs per node
hold TP groups; nodes chain into PP stages; the DP/ZeRO replication
fills the rest of the fleet. When someone says "the model is
8-way TP, 64-way PP, 384-way DP," they are describing a specific
address for every parameter and every activation in a 25,000-GPU
address space — a memory map, the same concept module 01 chapter 03
built for a single process, one billion times larger.

## The failure catalog

Distributed training bugs are memory bugs plus communication bugs, and
they present as the worst debugging experience in software. The
catalog every practitioner accumulates:

- **The NaN hunt.** A loss goes to NaN at step 48,000. The cause is
  upstream of the symptom: the loss-scale overflowed (fp16 gradients
  hit 65504), or one rank's data loader fed a corrupted batch, or a
  transformer layer's activation overflowed in bf16. The discipline is
  to watch the *vital signs* — the gradient norm spikes before the
  loss spikes, the dynamic loss-scale's skip counter climbs before
  either — module 18 chapter 01's entropy-monitoring instinct applied
  to a training run.
- **The stale-grad hang.** A rank's collective never fires: one GPU
  entered the AllReduce and the others didn't (a dead rank, a
  data-loader stall, an exception swallowed on rank 3 only). The
  default NCCL timeout is 30 minutes — a hung job sits there,
  apparently healthy, for half an hour before the watchdog SIGTERMs
  it. Everyone learns to lower the timeout and to read the rank-order
  of the abort as the diagnosis.
- **The elastic restart.** With ~25,000 GPUs, failures arrive every
  few hours (the MTBF arithmetic lands in chapter 05). The sharded
  checkpoint of a 1024-GPU run must re-shard onto 512 GPUs for the
  restart to proceed — same state, different N, a reshuffle on load.
  The checkpoint's *format* is a memory-layout decision made under
  duress, which is why it keeps its own section in chapter 05.

And the closing arithmetic this chapter owes you — QLoRA, the
parameter-efficient endgame: freeze the base model in 4-bit (70B ×
0.5 B ≈ 35 GB), attach low-rank adapters (~0.5 GB at 0.1–1% of the
parameter count), keep gradients and optimizer state for the *adapters
only*, and page even that to CPU DRAM. A 70B fine-tune on one 48-GB
GPU, versus the naive fp32 full-fine-tune's ~2.1 TB of training state
— a **60× compression** obtained by refusing to store what you are
not updating. The full stack of this chapter, in one line: *shard
what you replicate, freeze what you don't train, page what you
rarely touch* — malloc's generational hypothesis (module 01 chapter
12) reborn at the terabyte scale.

## What insiders know

- **The two vital signs.** Log the global grad-norm and the dynamic
  loss-scale's skip count on *every* run. Divergence announces itself
  there, minutes before the loss curve shows anything — and the
  grad-norm is also your distributed-clipping instrument (global
  norm = sqrt of the sum of per-rank squared norms: the fused
  reduction carries the squares, chapter 02's derivation).
- **`find_unused_parameters=True` is a tax.** DDP's fallback for
  models with conditional execution walks the autograd graph after
  backward to find parameters that never got a gradient; on a big
  model that walk costs real seconds. The LoRA-era pattern (frozen
  base + tiny trainable adapters) hits this constantly — the fix is
  `static_graph=True` or re-thinking the module structure.
- **`LOCAL_RANK` vs `RANK`.** RANK is global across the fleet;
  LOCAL_RANK is the GPU index *on this node* — it decides which CUDA
  device you must bind to, and the mismatch (everyone grabbing device
  0) is the first bug of every distributed hello-world.
- **`torch.compile` × distributed was the 2023–24 immaturity story.**
  Graph breaks across collectives, fused CollectivesSignal ops falling
  back to eager, recompilation storms when bucket boundaries shifted.
  It works now-ish; the lesson is that the collective layer was
  designed for eager, and graph capture (chapter 04's CUDA-graph
  discipline) has to reach *into* the communication library.
- **Match the backend to the fabric.** NCCL for GPU collectives,
  Gloo for CPU debugging. The habit of forcing `gloo` locally to get
  readable stack traces instead of a wedged NCCL kernel is the
  distributed equivalent of printf debugging — unglamorous and
  load-bearing.

## See it yourself

**Lab 1 — the ZeRO simulator (paper + python).** Compute the per-GPU
memory for a 7B model at N=8 first on paper: weights 2P = 14 GB,
gradients 2P = 14 GB, Adam state 12P = 84 GB (m + v + master), 112 GB
total. Then verify with ~40 lines of python — the simulator takes
(P, N, stage) and prints the per-GPU components:

```python
P = 7e9                      # parameters
fp16 = 2; fp32 = 4
weights = P * fp16
grads   = P * fp16
adam    = P * fp32 * 2       # m and v
for stage in range(4):       # 0 = DP
    if stage == 0: w, g, o = weights, grads, adam
    if stage == 1: w, g, o = weights, grads, adam / 8
    if stage == 2: w, g, o = weights, grads / 8, adam / 8
    if stage == 3: w, g, o = weights / 8, grads / 8, adam / 8
    print(f"ZeRO-{stage}: {w/1e9:.1f} + {g/1e9:.1f} + {o/1e9:.1f} GB/GPU")
```

Run it. ZeRO-0 is the DP wall (112 GB on an 80-GB part); stage 3
lands at ~14 GB. Now change P to 175e9 and watch stage 2 go marginal
— the same numbers this chapter's tables carried, produced by your
own arithmetic.

**Lab 2 — the 1F1B timeline.** Draw the 4-stage × 8-micro-batch
schedule by hand on graph paper (or ASCII in a file). Compute the
bubble: (4−1)/(8+4−1) = 27%. Then redraw with interleave=2 (each
stage owns two discontiguous 2-layer slices, the schedule's warm-up
halves): bubble ≈ 1.5/(8+1.5) ≈ 16%. Put both diagrams side by side
and annotate every idle slot — this is the deliverable, the two
pictures that explain why production schedulers interleave.

**Lab 3 — a DDP miniature in python.** Two processes (multiprocessing
or threads + queues standing in for NCCL), a toy "model" (a 512-float
vector), the producer-consumer gradient bucketing of chapter 09's
SPSC ring, buckets of 128 floats. Log each bucket's flush time and
compute the "forward+backward" (numpy matmul) time per bucket. Measure
compute-only vs compute+comm wall time and report the overlap factor —
the toy's honest limits (queues are not NVLink) go in your lab notes,
but the *shape* of the overlap accounting is the real lesson.

**GPU lab (if available).** `torchrun --nproc-per-node=2` a DDP
matmul snippet; watch the NCCL init, set the bucket capsize to 1 MB
and read the flush pattern in the profile. On a single GPU, emulate
the second rank with a CUDA stream and the reduce with a copy — the
point is to see the bucket boundary move in the timeline.

## How to use this knowledge

- **Sizing a training run** becomes arithmetic, not folklore: write
  the state table for your P, read off the stage that fits, and check
  the communication volume against your fabric before you write a
  line of launcher code.
- **Reading a job's config** ("8×TP × 8×PP × 96×DP") becomes reading
  a memory map: you know which bytes live where, which traffic rides
  which wire, and where the next wall is.
- **Debugging a hung job**: the rank-order of the abort, the timeout
  default, the collective that never fired — the failure catalog
  above turns a 3-hour mystery into a 10-minute triage.
- **Fine-tuning decisions** (full vs LoRA vs QLoRA) collapse to one
  question: what are you *not* updating, and can you refuse to store
  it? The 60× table answers it.
- **Capacity planning** at fleet scale is this chapter's tables with
  your fleet's numbers substituted — the amortization argument that
  justifies (or kills) the cluster purchase.

## Connections

- **Chapter 01** (same module): the tensor's two-object structure is
  what gets sharded — a ZeRO slice is a (storage, offset, shape)
  decision on the same refcounted buffers.
- **Chapter 02**: the 16P state table is the autograd tape's memory
  accounting (weights, gradients, Adam's moments) made distributed;
  the fused grad-norm reduction is chapter 02's derivation running
  on the ring of chapter 05.
- **Chapter 04**: inference takes the sharded-weights idea to its
  single-GPU extreme (4-bit) and swaps training state for the KV
  cache — the same budget table shape, different resident bytes.
- **Chapter 05**: the collective algebra (ReduceScatter + AllGather,
  the ring's 2(N−1)/N) is the machinery under every AllReduce named
  here, and the straggler/lockstep analysis explains why synchronous
  training makes every slow GPU everyone's problem.
- **Module 01 ch03** (virtual memory): ZeRO-3's on-demand parameter
  gather is demand paging with the GPU as the MMU — and COW shows up
  twice: ZeRO's shared prefix logic and, in chapter 04, the shared
  system-prompt KV blocks.
- **Module 15 ch03** (virtio's vring): the NCCL ring and the virtio
  vring are the same producer-consumer shared-memory ring — one moves
  packets to a hypervisor, the other moves gradient chunks to peers;
  hardware rings, software rings, the shape is invariant.
- **Module 09 ch05** (distributed memory models): the staleness trade
  — synchronous lockstep versus stale-but-cheap gradients (local SGD,
  1-bit methods) — is the same consistency-versus-availability
  arithmetic that chapter taught for stores, applied to gradients.

## Quiz

1. **(Concept)** A team claims their 405B-parameter model needs "only
   810 GB of training memory because fp16 is 2 bytes." What did they
   forget? Enumerate the four components of the honest state table
   and give the corrected total.

2. **(Concept)** Why does tensor parallel stay inside a node while
   pipeline parallel happily spans nodes? Answer in terms of traffic
   shape (volume, frequency, latency sensitivity) per parallelism axis.

3. **(Predict/derive)** A 30B-parameter model, fp16 optimizer state
   (12 B/param total for m+v+master), 16 GPUs, 80 GB each. Compute
   per-GPU memory for plain DP, ZeRO-2, and ZeRO-3. Which stage fits
   with room for ~40 GB of activations, and what does that imply
   about the maximum viable model on this cluster with ZeRO-3 alone?

4. **(Predict/derive)** Derive the 1F1B bubble fraction
   (N−1)/(M+N−1) from the warm-up/steady-state structure of the
   schedule diagram. Then compute it for 8 stages and 32 micro-batches,
   and state what M would have to be to push the bubble under 5%.

5. **(Predict/derive)** DDP buckets gradients at 25 MB. A 7B fp16
   model produces 14 GB of gradients per step. How many buckets per
   step, and why does bucket size trade latency hiding against
   per-bucket overhead? What goes wrong at 1 MB buckets? At 500 MB?

6. **(Diagnose)** A 512-GPU ZeRO-3 job hangs at step 2,180 with no
   error for 30 minutes, then dies with an NCCL watchdog timeout.
   Rank 47's data loader log shows a stalled prefetch of a corrupted
   shard. Explain the mechanism of the hang, why the timeout is the
   *first* visible symptom, and two mitigations that would have made
   the diagnosis faster.

7. **(Diagnose)** After a node failure, your 1024-GPU job restarts
   on 512 GPUs from a sharded checkpoint and immediately produces
   NaNs, but the same checkpoint on 1024 GPUs validates fine. What
   did the re-sharding path most likely get wrong, and how would
   you design the checkpoint format so this class of bug cannot
   happen?

8. **(Cross-chapter, module 01 ch03)** A colleague describes ZeRO-3's
   parameter gather as "the GPU acting like an MMU." Make the
   correspondence precise: what plays the role of the page table, the
   page fault, demand paging, and COW? Where does the analogy break
   down?

<details><summary>Answers</summary>

1. They counted weights only. The honest table: weights 2P + gradients
   2P + Adam's m and v in fp32 8P + the fp32 master copy 4P = 16P ≈
   6.5 TB for 405B. Their 810 GB figure omits 92% of the training
   state — the optimizer dominates at scale.

2. TP's traffic is two latency-tight AllReduces *per layer* (tens of
   MB, microseconds apart, on the critical path of every matmul) —
   only NVLink's 600–900 GB/s and ~1–2 µs latency can hide that. PP's
   traffic is one activation-sized point-to-point send per layer
   boundary per micro-batch (MBs, sparse, off the critical path) —
   the slow inter-node fabric absorbs it. Traffic *shape* (frequency
   and latency-sensitivity, not just volume) decides the placement.

3. DP: 30B × 16 B = 480 GB — 6× over. ZeRO-2: weights 60 GB (not
   sharded) + (grads 60 + adam 240)/16 = 15+15 = 30 GB sharded →
   90 GB total — still doesn't fit. ZeRO-3: 480/16 = 30 GB — fits
   with 40 GB of activation room. Maximum model with ZeRO-3 on this
   cluster: 16 × (80 − 40 activations − overhead) ≈ 16 × 35 GB = 560
   GB of state = 35B parameters. Beyond that you need TP to split
   layers or PP to split depth.

4. The step occupies M + N − 1 slots (M forward slots per stage in
   steady state, N − 1 warm-up slots where the pipeline fills, N − 1
   drain where it empties — warm-up and drain overlap across stages,
   totaling N − 1 idle slots per stage). Idle fraction = (N−1)/(M+N−1).
   For 8 stages, 32 micro-batches: 7/39 ≈ 18%. For under 5%:
   (N−1)/(M+N−1) < 0.05 → M > 19(N−1) − N ≈ 151 micro-batches for
   8 stages — deep pipelines demand very many micro-batches, which
   is exactly why the interleaved schedule (halving the effective N)
   exists.

5. 14 GB / 25 MB ≈ 560 buckets per step. Large buckets: fewer
   collective launches (good — per-call overhead ~5–10 µs) but the
   reduction of a late bucket cannot start until it fully fills
   (exposed communication at the end of backward). 1 MB buckets:
   14,000 launches × overhead = overhead-dominated, the launch queue
   saturates. 500 MB buckets: only 28 — the last one alone exposes
   ~500 MB of reduce time with zero overlap. The 25 MB default is the
   empirical crossover.

6. Mechanism: rank 47's data loader stalls → its rank never enters
   the step's first collective → every other rank blocks inside NCCL
   waiting for the missing participant → the CUDA kernels of the
   collective spin (NCCL's busy-wait, chapter 05) with no error —
   a distributed deadlock, not a crash. The 30-minute NCCL watchdog
   is the *default timeout*, so the first symptom is the timeout,
   not the stall. Mitigations: lower `NCCL_TIMEOUT`/`TORCH_NCCL_HEARTBEAT`
   monitoring to minutes; a rank-health side-channel (a lightweight
   heartbeat outside the collective path) so the stalled rank
   identifies itself in the logs *before* the watchdog fires.

7. The re-shard most likely remapped parameter slices with an
   off-by-N error (1024-way slice boundaries misassembled into 512-way
   buckets) or dropped the master-weight parity — corrupted weights
   validate as finite but wrong, and one wrong layer produces NaNs
   immediately. Design fix: content-address the checkpoint (each
   shard carries a hash of its exact parameter slice; the re-sharder
   verifies hashes on load), and store a self-describing layout
   (which parameter, which slice index) instead of positional
   concatenation. The hash check converts this bug class from
   silent NaNs into a loud load-time error.

8. Page table ≈ the sharded parameter registry (which rank owns which
   slice — the AllGather schedule). Page fault ≈ the layer-not-present
   trigger that fires the AllGather prefetch. Demand paging ≈ the
   prefetch of layer i+1 while layer i computes. COW ≈ shared
   read-only parameter slices in multiple TP replicas before the
   optimizer's exclusive update window. The analogy breaks down at
   protection: an MMU enforces access permissions in hardware (a
   process *cannot* touch a unmapped page), while ZeRO's discipline
   is cooperative — a buggy rank can read a neighbor's slice with no
   fault, the same trust boundary difference module 15 drew between
   EPT isolation and container convention.

</details>

