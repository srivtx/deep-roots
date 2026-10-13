# Module 22 — ML systems & training infrastructure: the distributed memory machine

> The largest programs ever run are not programs at all — they are
> memory choreography. When a frontier model trains, ~25,000 GPUs
> perform a synchronized dance in which each GPU's 80-192 GB of HBM
> holds a shard of a object that does not fit anywhere in whole,
> gradient tensors are AllReduced across a fabric moving ~400 Gb/s per
> NIC, and a single straggler GPU slows all 25,000 (the synchronous
> training lockstep). Every concept from this program returns at
> datacenter scale: the latency tower of module 01 becomes a
> interconnect topology, the allocator of module 01 ch19 becomes
> PyTorch's caching allocator, the ring reductions of module 13
> become NCCL's collectives, the copy-on-write of module 01 ch03
> becomes ZeRO's sharded states, and inference's KV cache is
> literally a heap of (virtual) tokens that must not be freed early.
> This is the module where "a datacenter is one computer" stops
> being a metaphor and becomes an engineering discipline.

**5 chapters · one final exam · prerequisite: module 01 chapters 02
(the tower — extended to the fabric), 05 (the heap — the caching
allocator), 12 (GPU memory — the hardware this module choreographs),
19 (allocators); module 13 ch03-05 (networking: buffers, rings,
RDMA) and module 09 ch03 (lock-free/collective algorithms)
referenced, not required.**

## How to study it

Chapter 01 re-derives the tensor as THE memory object (layouts,
strides, why NHWC vs NCHW is a cache story); 02 builds autograd as
a memory machine — the tape, the graph, why backward allocates, the
activation-memory accounting that explains the "why does training
need 3× model-size per parameter" arithmetic; 03 is the distributed
training memory: data parallel → ZeRO sharding → tensor parallel →
pipeline parallel, each derived as a different answer to "the model
doesn't fit"; 04 is inference serving: the KV cache as a paged heap
for attention, continuous batching as the server's allocator, the
quantization trade (bytes per parameter = the only lever that
scales); 05 closes with the collective stack and the datacenter as
a single memory hierarchy: NCCL rings on NVLink, the AllReduce math,
the straggler problem, and the roofline of the whole industry. Labs:
any laptop (PyTorch CPU for allocator/stride/gradient experiments,
python for the arithmetic and a mini autograd; the GPU labs are
marked "if you have CUDA" with CPU equivalents always given).

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The tensor: memory's industrial form](chapters/01-tensor-memory.md) | Strides, layouts, views vs copies, the memory-format war — the array of module 01 grown an economy. |
| 02 | [Autograd: the tape as a heap](chapters/02-autograd-tape.md) | The forward tape, the backward allocations, activation memory accounting — why training RAM obeys the 20× rule. |
| 03 | [Distributed training: sharding the unshardable](chapters/03-distributed-sharding.md) | Data parallel, ZeRO, tensor/pipeline parallel — four ways a model too big for any one HBM becomes trainable anyway. |
| 04 | [Inference serving: the KV-cache machine](chapters/04-inference-serving.md) | KV as a paged heap, continuous batching, quantization as the bytes-per-token lever — latency vs throughput vs cost. |
| 05 | [Collectives and the datacenter computer](chapters/05-collectives-datacenter.md) | AllReduce on rings and trees, NVLink vs InfiniBand, stragglers, the synchrony trade — the fabric as a memory hierarchy. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the tensor.** The tensor derived from first
principles: a strided view over a contiguous buffer (the (data_ptr,
shape, dtype, strides) tuple as THE object — draw it: a 2×3 float32
tensor = 24 bytes of heap + 56-byte TensorImpl header (PyTorch's
real numbers: the Tensor's C++ object (~64-72 B) vs the storage
(the malloc'd buffer) — the two-object structure (the shallow-copy
semantics: two tensors sharing one storage (the refcounted
Storage object (module 01 ch14's PyObject pattern returning
 verbatim — derive the shallow copy's aliasing (the view's
invariant: every view is (offset, strides) over the same buffer
(the `t.view` vs `t.reshape` vs `t.contiguous` rules derived
from the stride algebra (a view is possible iff the target
shape is expressible as a stride permutation/merge (the
flatten-of-transposed case that forces a copy (the checker:
`t.stride()` shown for real tensors (the lab makes the reader
predict strides before printing them). **The layout war as
cache physics**: NCHW vs NHWC (the conv kernel's inner loop
reads (C, H, W) — NCHW puts C outer (the inner loop strides
by H×W — a cache line fetch serves ONE pixel's channel, 3-4
bytes of a 64 B line (NHWC puts C inner: 64 B line serves
16 channels of one pixel — the 4-16× reuse factor derived
byte-by-byte with the line-occupancy arithmetic (this is
module 01 ch02's coalescing story in a new axis (module 12
ch02's GPU coalescing named explicitly: same physics, the
axis of the unit vector changed); the hardware's opinion
(Tensor Cores require NHWC-ish vectorization (the channels-
last memory-format API (torch's `channels_last` as a
*metadata-only* relayout (the stride tuple shown for the
same tensor in both formats — no bytes move, only the
view does (the honest surprise: "memory format" is a
stride decision, not a buffer decision). Padding and
alignment (the allocator's 512-byte blocks (the offset
storage trick: sub-tensors share blocks (the 16-byte-
alignment rules for vectorized loads (the tail-of-batch
problem (the last batch's 17 elements padded to 512
(how PyTorch's caching allocator rounds (the rounding
arithmetic: 1,000-element float32 = 4,000 B → rounds to
4,096 or 4,608 (the internal fragmentation at mini-scale
(the per-allocate waste measured in the lab)). The dtype
spectrum as the memory lever (the table: fp64 8B, fp32
4B, tf32 4B (19-bit mantissa (the A100's tf32 mode: the
same storage, fewer meaningful bits (the speed 8× claim's
memory-side honesty: tf32 doesn't save DRAM, it saves
multiply-units (bf16 2B (the 8-bit exponent (the range
of fp32 at the precision of fp16 — the training-stability
story (why bf16 won the training world (the gradient's
dynamic-range argument (the loss-scale trick that fp16
needs and bf16 doesn't (the scale factor's overflow
arithmetic: 65504 max vs gradients ~1e-8 (the 8-decade
gap closed by 2^(16-8) scaling)), fp16 2B, int8 1B, the
emerging 4-bit formats (the norm-vs-weight quantization
split (the outlier-channel problem (LLM.int8's finding:
0.1% outlier channels with 100× magnitude (the
mixed-precision decomposition: outliers in 16-bit, the
99.9% in 8 (the insight that saved int8 inference (the
"the outlier channels are consistent across layers"
empirical finding — the reason per-channel scales
work)). The serialization boundary (the .pt/.safetensors
formats (the zero-copy mmap loading (the safetensors
design: the header-length + JSON + flat buffer (the
mmap trick: no copy, no deserialization, the mmap'd
weights = page-cache backed (module 14 ch03's page cache
returning: the model weights warmed by the OS (the
startup-time arithmetic: 70 GB of weights from disk at
NVMe's 7 GB/s = 10 s (vs page-cache warm = ~1 s (the
cold-start latency budget of inference fleets (the
"serving region reboot" war story)). Insiders: the
`torch.compile` memory story (the compiled graph's
allocation planning (the static-buffer reuse vs eager's
per-op allocation (the ~20-30% memory savings number
(honest: it's fusion + reuse); the "0-dim tensor" is
still 512 bytes (the smallest allocation's floor (the
metadata:buffer ratio at the tiny end (a scalar's 100:1
overhead — the mini-batch world's hidden cost (the
`torch.numel()==0` edge cases (the zero-size tensor's
data_ptr semantics (the null pointer that must not be
dereferenced (the batch-size-0's real uses (the
variable-batch padding machinery)); the pin_memory
story (the staging buffer for DMA (module 13 ch01's
ring returning: the host's pinned buffers = the
GPU's DMA source (the cudaHostAlloc's page-locking
(the pageable-vs-pinned transfer rates: ~6 vs ~12-25
GB/s PCIe gen4 (the 2-4× (the staging-copy double
buffering (the prefetch overlap pattern (the
DataLoader's pin_memory=True meaning decoded)); the
`expand` vs `broadcast` semantics (the stride-0 view
( the memory-free broadcasting (the write-never rule
(the expanded tensor's write attempt = the error
(the aliasing discipline: stride-0 views are read-only
by law (the matmul's broadcast fusion (the einsum's
broadcast algebra). Lab (CPU-mandatory): the stride
discovery set (create 10 tensors (transpose, slice,
flatten, contiguous, channels-last), predict each
stride, print, score yourself — the table you produce;
the storage-sharing proof (id(t.storage()) across views
(the data_ptr offsets (the refcount chain shown
(sys.getrefcount equivalents (or t.storage().use_count
))); the dtype/range experiment (the fp16 overflow
demo: 70000 in fp16 = inf (the gradient-scale demo:
tiny gradients underflow in fp16 (the same code in
bf16 survives (the code provided, runs on CPU); the
allocation-size ladder (torch's CPU allocator via
tracemalloc: 100 tensors of size 1000 → the rounding
observed (the fragmentation measured (the block-size
table derived empirically).

**Chapter 02 — autograd as a tape.** Backpropagation re-derived as
MEMORY ENGINEERING (the reframe the chapter carries: the algorithm
is textbook; the memory discipline is what production ML
engineering actually is). The tape (the Wengert list: the linear
record of every forward op (each entry: the op, the operands' (ptr,
shape), the saved-for-backward tensors — the structure drawn as a
real 5-op tape (the entry for a matmul: the two inputs + the output
(the saved tensors' cost table: what each op saves (the memory
BLOW-UP rule: backward needs forward's inputs (the sum over ops of
saved-tensor bytes = the activation memory (the activation-table
for a 12-layer transformer (the mandatory arithmetic, itemized:
embedding output 2048×2048×4B = 16 MB per layer's residual stream
(the attention's QKV: 3× (the softmax's saved probabilities (the
dropout mask (the mask is a full bool tensor per dropout site
(the activation total for GPT-2-class: ~20× model parameters for
batch×seqlen = 8×1024 (the derivation in a table, every line
computed (the "batch-size 1 crashes, batch-size 2 OOMs" arithmetic
( the activation-per-token ratio (the KV of chapter 04 planted
here (the activation checkpointing trade (recompute vs store:
the tree of sub-graphs (the ~30-40% compute overhead buys
sqrt(layers) memory (the sqrt arithmetic: checkpoint every √L
layers → peak = O(√L × per-layer) (the classic Megatron
checkpoint schedule (the selective-checkpoint research (the
"save the cheap ones, recompute the expensive" inversion
(the per-op saved/recompute cost table (the policy question
formalized)). The backward pass as allocation storm (the
grad-of-grad problem (the double-backward's tape-of-tape (
the `.detach()` semantics (the cut: the freed tape (the
gradient-accumulation pattern: the `.backward()` frees the
graph by default (the `retain_graph=True` cost (the
accumulation loop: the optimizer.step() per N batches (the
gradient buffer as persistent storage (the dgrad-wgrad
split (the data-parallel's gradient reduce planting ch03).
The engine's memory: the topological sort of the tape (the
ready-queue as the dependency-driven scheduler (the
backward's execution order = reverse-topological (the
leaf tensors' .grad buffers allocated on first use (the
accumulation semantics: += into the same buffer (the
zero_grad(set_to_none) 2021 change (the None vs 0-tensor
distinction (the lazy allocation discipline (the micro-
optimization honestly explained: allocating zeros costs
a memset (lazy = skip it for unused params (the
"did you know zero_grad had a semantic change" trivia)).
The mixed-precision memory discipline (the master-weight
pattern: fp32 copy of every weight + the 16-bit compute
copy (the 2×-plus weight memory (the loss-scaler's state
(a scalar + the skip counter (the dynamic scale's
multiply-by-2-on-success / divide-by-2-on-overflow (the
overflow detection: the inf/nan check (the check's cost:
a reduction over every gradient (the all-reduce fused
with the check (the distributed scaler of ch03 (the
grad-norm's double duty: the clipping threshold's
arithmetic (the clip at 1.0: the norm computation's own
reduction (the "clip-then-reduce vs reduce-then-clip"
order question (the correctness subtlety of distributed
clipping (the norm's associativity (the math: global
norm = sqrt(Σ local norms²) — the fused reduction
carries the squares (the 5-line derivation, mandatory:
it's the chapter's cleanest example of "the math and
the communication must agree")). The optimizer as
resident-memory occupant (the Adam state: 2 extra
tensors per parameter (m and v (the 3×-plus total: weights
+ grads + m + v = 4× model bytes in fp32 (the 8-bit-optimizer
trick: the state in int8 with the block-wise scales (the
4× state saving (the accuracy honesty: the block-quantized
moment (the "paged optimizer" (the state that overflows to
CPU DRAM (the ZeRO-offload architecture planting ch03's
pyramid). Insiders: the `no_grad`/`inference_mode` tiers
( the inference_mode's stricter aliasing (the version-
counter mechanism: the TensorImpl's version counter (the
in-place op bumps it (the saved-tensor's check: the
"one of the variables needed for gradient computation
has been modified by an inplace operation" error
dissected (the version counter as the validity invariant
(module 19 ch04's validity returning as a 32-bit int));
the graph's "freed" vs "unbuilt" (the building-vs-recording
distinction (the forward-mode's dual numbers (the JVP's
alternative tape (the forward-over-backward for scalars-
to-vectors (the research honesty: fwd-mode's niche); the
PyTorch C++ dispatch's allocator behavior (the caching
allocator of module 01 ch19's taxonomy: torch's = the
arena/block flavor (the segfault-class OOM: the
`CUDA out of memory` message decoded (the reserved-vs-
allocated split (the `empty_cache()` folklore: it
releases reserved, not allocated (the fragmentation's
diagnosis: the reserved-allocated gap (the 30 GB
reserved / 20 GB allocated = fragmentation metric
(the block-splitting churn (the lab measures it);
the determinism trade (the atomics' non-determinism
(the scatter-add's ordering (the reproducibility
flags vs speed (the 10-20% cost (the "CUBLAS_WORKSPACE
CONFIG" environment variable trivia). Lab
(CPU-mandatory, GPU-marked): build the mini-tape (a
50-line python autograd for +, ×, matmul, relu (the
Variable class (the backward closure chain (the topological
sort (the 30-line training loop on y = x² (the reader's
own tape (the code provided, must run); the activation
arithmetic on paper (the 12-layer table computed by hand
(the checkpoint's √L schedule derived (the memory-
compute Pareto plotted for 3 checkpoint frequencies);
measure the real thing: torch CPU: tracemalloc the
forward (the saved tensors' total (compare against the
chapter's formula (the 20× rule verified (the error's
magnitude honestly reported); the version-counter demo
(the in-place error triggered on purpose (the error
message read closely (the detach-fix applied).

**Chapter 03 — distributed training.** The problem stated in bytes:
the model that doesn't fit anywhere (the arithmetic ladder, mandatory
as the chapter's opening table: a 175B-param model in fp16 = 350 GB
weights + 350 GB gradients + 700 GB Adam state = 1.4 TB "training
state" vs one A100's 80 GB (the 17× gap (a frontier 1T-class model:
~8 TB state vs a 25k-GPU fleet's 2 PB aggregate HBM (the 0.4%
utilization irony: the naive DP approach replicates everything —
the sharding imperative derived from division, not fashion). The
four architectures derived in order (each as an answer to "what do
we shard"): **data parallel** (DP: replicate the model, shard the
data (the gradient AllReduce after backward (the per-step
communication: 2× model bytes (the ring's 2(N-1)/N factor — the
ch03→ch05 plant (the DP's memory floor: the FULL model on every
GPU (DP works to ~10B params on 80 GB, then dies (the
distributed-data-parallel's overlapped buckets (the bucket's
25 MB default (the reduction's overlap with backward compute
(the copy-stream-to-reduce-stream (the two CUDA streams (the
module 12 ch01 stream concept returning at scale); **ZeRO**
(the three stages as three sharded components (stage 1:
sharded optimizer state (the m, v distributed (the 4× state
→ 4×/N (the all-gather after step (the stage-1 arithmetic
on the 175B example: 700 GB state → 700/N per GPU (stage 2:
+ sharded gradients (the reduce-scatter replaces AllReduce
(the 2× more savings (stage 3: + sharded parameters (the
prefetch of the next layer's weights during the current
layer's compute (the parameter all-gather's bandwidth
arithmetic: 350 GB / N GPUs per layer, overlapped (the
stage table: memory per GPU for the 175B example at N=64
(stage 0: >1.4 TB (impossible), 1: ~120 GB (still too big),
2: ~90 GB (marginal), 3: ~25 GB (fits with room for
activations (the honest costs: stage 3's communication
volume ~1.5× of DP (the "memory or bandwidth" trade stated
numerically (the ZeRO-Offload pyramid: the optimizer to
CPU DRAM (the PCIe bandwidth: ~25-60 GB/s vs NVLink 300-900
(the offload's step time arithmetic (the 3-tier pyramid:
HBM → DRAM → NVMe (the DeepSpeed-Infinity's NVMe paging
(the 7 GB/s NVMe streams (the 3-tier memory hierarchy of
module 01 ch02 returning as a literally-constructed tower
(the derivation of the whole pyramid as "build the tower
yourself when the silicon doesn't give you one" — this is
the module's thesis moment, drawn side-by-side with the
L1/DRAM pyramid from module 01 (the diagram is mandatory
and is the chapter's centerpiece); **tensor parallel**
(TP: split the matmul itself (the column-split: Y = XW,
W = [W1 W2] (each GPU holds a slice of columns (the
independent outputs (the all-gather OR the split-output
trick (the Megatron's attention split: the heads distributed
(the QKV per-head independence (the two all-reduces per
transformer layer (the forward's f/g conjugate operators
(the 2φ communication per layer (φ = hidden×vocab bytes
(the bandwidth-vs-computation overlap arithmetic (the TP's
scope: intra-node only (NVLink's 600-900 GB/s vs Ethernet's
25-100 (the TP's domain = 8 GPUs of one HGX board (the
"hierarchy-aware parallelism" principle: match the
parallelism axis to the fabric topology (the rule stated
as THE design law of distributed training)); **pipeline
parallel** (PP: the layers distributed (the stage's
micro-batches (the 1F1B schedule (the bubble: the idle
fraction (N_stages-1)/(M_micro + N_stages-1) — the formula
derived from the timeline diagram (the mandatory drawn
timeline: 4 stages, 8 micro-batches, the bubble's area vs
the filled area (the 1F1B's memory steady-state (the
in-flight activations bounded at depth ≈ stage count
(vs GPipe's all-micro-batches-in-flight (the interleaved
variant's bubble halving (the virtual-pipeline trick
(the per-stage virtual micro-stages (the communication:
the activation handoff (layer boundary (the point-to-point
send/recv (the inter-node link's occupancy (the PP's
tolerance of slow interconnects (why PP spans nodes while
TP stays intra-node (the 3-D mesh: DP × TP × PP as the
full datacenter model (the 5-D naming (DP, TP, PP, CP
(context parallel for long sequences), EP (expert
parallel for MoE (the LLM-era's 5 parallel axes named
and placed on the topology diagram (the SPMD-vs-MPMD
programming models (the single-device mental model
retained via DP, abandoned by TP/PP (the mental-load
honesty: TP debugging's "which device's NaN first"
folklore)). The failure modes catalog (the distributed
program's bugs are memory bugs + communication bugs (the
NaN's origin hunt (the loss-scale overflow (the stale-
grad-hang: the all-reduce that never fires (the dead-
rank diagnosis: the torch-distributed-gym's hang-vs-
crash dichotomy (the timeout defaults: 30 min NCCL
(the watchdog's SIGTERM (the "compile the whole world
twice" nondeterminism of the elastic world (the
checkpoint's distributed consistency (the sharded state
save (the resumption's re-sharding (the elastic
training's remap arithmetic (the "the checkpoint of a
1024-GPU run restarts on 512" problem (the ZeRO's
re-shuffle on load (the checkpoint's memory footprint:
the staged copy (the save's 2× peak problem (the
offload-then-serialize pattern). Insiders: the
grad-norm-vs-loss-scale monitoring duo (the training's
vital signs (the divergence's early signature: grad-
norm spike before loss spike (the window of
intervention (the "torch.compile × distributed" 2023-24
immaturity stories (the graph-break across the
collective (the fallback-to-eager (the
static-graph-era-of-collectives honesty); the
`find_unused_parameters` tax (the DDP's graph walk
(the unused-param detection's cost (the frozen-stage's
fine-tuning pattern (the LoRA's parameter- efficient
world: the adapter's 0.1-1% of params (the frozen base
(the two-rank-in-RAM problem of fine-tuning (the QLoRA
stacking: 4-bit base + 16-bit adapters + paged optimizer
(the 70B-fine-tune-on-one-48GB-GPU arithmetic — the
mandatory closing computation: the full stack's memory
table (base 4-bit: 35 GB (adapters: ~0.5 GB (grads for
adapters (optimizer state (the total vs the naive
fp32 175B×4×3 = 2.1 TB (the 60× compression of the
fine-tuning world (the numbers as the chapter's final
table)); the torchrun's world-size typography (the
LOCAL_RANK vs RANK (the per-node vs global (the
init_process_group's backend zoo (NCCL for GPU, GLOO
for CPU (the MAC-law: match the backend to the
fabric. Lab (CPU-mandatory): simulate ZeRO on paper
+ python (the 3-stage memory table computed for a
7B model at N=8 (the numbers verified against a
40-line python simulator (the code provided (the
per-GPU memory plot); the 1F1B schedule as ASCII
(the 4-stage 8-micro-batch timeline drawn (the bubble
computed (the interleave=2 variant drawn (the bubble
recomputed (the two diagrams side by side — the
deliverable); the DDP's gradient bucketing in
miniature (a python multiprocessing version: 2
"GPUs" as processes (the AllReduce via threads (the
bucket's flush timing logged (the overlap measured:
compute-only vs compute+comm (the speedup table (the
toy's honest limits stated); (GPU-marked) run
`torchrun --nproc-per-node=2` a matmul DDP snippet
(the NCCL init observed (the bucket flushes in the
profile (nsight's trace annotated).

**Chapter 04 — inference serving.** The inference world's different
law: not "fit the training" but "serve the token stream at the
lowest cost per token." The KV cache derived carefully (the
attention's memory: each token's K and V per layer (the per-token
KV bytes = 2 × n_layers × n_kv_heads × head_dim × dtype (the
mandatory arithmetic for Llama-7B-class: 32 layers × 2 (K,V) ×
4096 dim × 2 B = 512 KiB per token (the 33× total: a 2048-token
context = 1 GB of KV per sequence (the batch's KV: 32 concurrent
2048-context sequences = 32 GB — the KV cache dwarfs the weights
(7B×2B = 14 GB weights vs 32 GB KV: the modern serving floor is
a KV-cache story (the numbers table for 7B/70B/400B at 4 context
lengths (the MHA→MQA→GQA lineage as KV compression (the heads
shared: KV heads 32→8 (GQA) → 1 (MQA (the 4× and 32× KV savings
(the quality's cliff (the llama-family's actual GQA choices (the
per-model KV arithmetic redone for GQA (the savings column added
to the table)). The KV cache as a paging problem (the contiguous-
allocation naïveté: each sequence's 2 GB reserved for max-context
(the 90% waste at short generations (the fragmentation-vs-
reservation trade (vLLM's PagedAttention: the KV as 16-token
blocks (the block table = the page table of module 01 ch03
RETURNING VERBATIM: virtual sequence-logical-position → physical
block (the indirection layer's win: copy-on-write for shared
prefixes (the system prompt's prefix blocks shared across
requests (the 100-sequence request flood sharing one 2 KB system
prompt: the COW savings computed (the branch/beam-search
forking (the prefix tree of generations (the COW's refcount
per block (module 01 ch14's refcount returning at the block
level — the identification drawn explicitly: paged KV =
virtual memory for tokens, the whole mechanism of module 01
ch03 re-invented because the SAME problem (fragmentation of a
shared growing heap) demands the SAME solution (indirection)
— the program's thesis stated one final time). **Continuous
batching** as the server's allocator (the static batch's
tail-latency problem (the 32-sequence batch where one sequence
generates 2000 tokens and 31 finish at 50: the GPU idles at
31/32 completion (the utilization arithmetic (the iteration-
level scheduling: the finished slot refilled instantly (the
prefill/decode phases (the prefill's compute-bound bulk pass
(the decode's memory-bound 1-token-per-sequence step (the
two phases' conflicting batch-shape optima (the chunked-
prefill's interleaving (theScheduler's queueing theory
(a 3-paragraph Little's-Law interlude: L = λW at the token
level (the concurrency that keeps the GPU memory-bound-full
(the target: batch × KV-bandwidth = HBM bandwidth (the
"the GPU's arithmetic intensity at decode is <1: the
decode step is a memory-stream problem (module 12's
roofline returning: the decode's position ON the roofline
plot (the batch's role: raising arithmetic intensity by
reusing weights across the batch (the weights-reuse
arithmetic: 7B weights streamed once per step for B
sequences (the cost-per-token = HBM-read / B (the B=1
vs B=64 cost table: the 64× amortization (the "why
batching works" one-paragraph derivation (the
goodput-vs-batching trade (the latency's tail: the
bigger batch = cheaper tokens, longer queue (the
SLO-aware scheduler (the request's deadline (the
admission control (the KV-block pool's exhaustion =
the deadlock risk (the preemption-and-swap mechanism
(the KV's eviction to CPU DRAM (the recomputation
fallback (the block-pool's watermarks — the memory
manager reborn as the scheduler's core (the
paged-attention allocator = the OS's page daemon
( module 07 ch04's kswapd returning as the "KV
scheduler's background reclaimer" — the identification
drawn, again, explicitly). Quantization as the only
universal lever (the W8A8 serving (the per-channel
scales (the int8 GEMM's 2× throughput vs fp16 (the
AWQ/GPTQ's 4-bit weight-only (the group-size-128 scales
(the 0.5 B/param: the 70B → 35 GB (the fits-on-one-A100
moment (the quality's perplexity cost table (the honest
0.01-0.1 delta at 4-bit (the activation's outlier
problem (LLM.int8's split (the weights-vs-activations
quantization asymmetry (why activations stay 8-bit and
weights go 4-bit (the memory-stream math: the decode's
per-token weight-stream = the only thing quantization
of weights can shrink (the roofline's arithmetic-
intensity shift (the quantization = "move down the
precision axis to move along the roofline" — the
closing diagram: the roofline plot with the quantized
points drawn (the chapter's final figure). The serving
stack's memory resident set (the weights + KV pool +
activations + framework overhead (the budget table for
a 70B node: 35 GB (4-bit weights) + 40 GB (KV pool) +
5 GB (runtime/cuDNN workspaces) vs 80 GB HBM (the
placement decisions the numbers force (the spec-decode
plant (the speculative decoding's draft-verify: the
draft model's 1 GB residence (the tree-attention
verification (the 2-3× throughput at the cost of the
draft's memory — the trade's arithmetic in 5 lines).
Insiders: the KV-cache's dtype story (the fp8 KV
experiments (the per-head scale factors (the 2× KV
pool (the accuracy's frontier status honestly dated);
the CUDA-graph capture of the decode step (the launch
overhead's elimination (the static shapes' requirement
(the padding-to-max-batch discipline (the graph's
memory pool: the capture's reserved arena (the
"why vLLM warms up" (the first-token latency's
composition: the tokenization (the queue (the
prefill (the 10-500 ms range's budget table (the
TTFT vs TPOT metrics (the SLO's two clocks (the
"streaming UX" psychology (the 200 ms first token
feeling instant (the 5 tok/s vs 50 tok/s perception
curve (the honest UX numbers)); the prefill's
context-charging economics (the 128k-context request
= a 1-minute prefill on a 7B (the compute's FLOPs:
2 × params × context (the arithmetic: 2 × 7e9 × 1.3e5
≈ 1.8e15 FLOPs ≈ 6 seconds at 300 TFLOPs effective —
the "long context is a compute product, not a memory
product" inversion (the prefix-cache's re-arming (the
system-prompt block reuse across requests (the 90%
prefill savings for chat workloads (the hit-rate
economics); the MoE era's serving memory (the
experts' resident set: the 8×-active-params illusion
(the all-experts-resident cost (the expert-parallel
routing (the HBM pressure: the 400B-class MoE's
weights vs the top-2-of-128 routing (the "the params
you don't use still cost DRAM" law (the expert
offloading's PCIe gamble (the hot-expert skew (the
load-balancing loss's serving-side echo)). Lab
(CPU-mandatory): the KV arithmetic on paper + python
(the per-token KV bytes for llama-7B and 70B (GQA
versions) (the table for contexts 2k/8k/32k/128k (the
python calculator provided (the output vs the
published vLLM tables compared (the gap explained);
the paging simulator (a 150-line PagedAttention
simulator: block tables (the COW prefix sharing (the
preemption/swap policy (the utilization metrics
printed: contiguous-reservation vs paged (the
fragmentation-before/after (the simulator must run
and show the ≥3× capacity gain at realistic mixed
lengths (the code provided or written by the reader
(the honest comparison table); measure the real
thing (huggingface + transformers on CPU: the 7B
inference's RSS watched (the weights + the KV's growth
with context (the 2k-context KV measured (the int8
vs fp32 load: the RSS delta (the lab runs if the
model fits (the "if not, use the 0.5B-class" fallback
documented); the roofline plot for decode (the
arithmetic-intensity of a decode step computed (the
point placed on a matplotlib roofline (the B=1 vs
B=32 points (the quantization's shift shown (the
figure produced and read.

**Chapter 05 — collectives and the datacenter computer.** The
fabric as the memory hierarchy's new level (the latency-ladder
extended, mandatory table: NVLink hop ~1-2 µs (the NVSwitch's
900 GB/s bisection (PCIe gen5 ~64 GB/s (InfiniBand NDR 400 Gb/s =
50 GB/s per NIC (Ethernet RoCE ~25-100 Gb/s (the inter-rail
topology (the fat-tree vs the rail-optimized design (the 2-tier
vs 3-tier (the oversubscription ratios: 2:1 to 8:1 (the "the
network is a shared cache with weird associativity" quip made
precise (the cross-rail penalty: same-rail 50 GB/s vs cross-rail
5 GB/s (the placement problem: the job's GPUs must be
rail-aligned (the scheduler's topology-aware constraint (the
gang-scheduling requirement (the fragmentation of the GPU fleet
(the bin-packing (the "the datacenter's occupancy rate" (the
60-80% real-world numbers (the stranded-FLOPs problem (the
capacity planning arithmetic). The collectives (the operations
catalog: broadcast, reduce-scatter, all-gather, all-reduce (the
composition law: AllReduce = ReduceScatter + AllGather — the
identity drawn as a dataflow diagram (the ring algorithm's
bandwidth optimality: 2(N-1)/N × data per link — **the mandatory
derivation, step by step**: N GPUs in a ring, each step sends
data/N chunk to the right neighbor (after N-1 steps, each has
all partial sums (the reduce-scatter phase), after N more
steps (the all-gather), everyone has the total (the total
bytes per link = 2×(N-1)/N × data (the latency: 2(N-1) steps ×
link latency (the bandwidth-optimal, latency-suboptimal
verdict (the tree algorithm's inverse trade: latency O(log N)
( bandwidth O(N × data) per link for the naive tree (the
recursive-halving double-tree (the bandwidth-latency Pareto
curves (the ring for big tensors, tree for small (the NCCL's
automatic choice (the tuner's plugin (the topology-aware
ring construction (the NVLink-first rings (the
intra-node-8-ring + the inter-node-NIC-pairs (the "the ring
spans two media" hybrid (the chunk pipelining (the algorithmic
bandwidth vs the protocol's overhead (the ~µs-scale launch
costs (the CUDA-graph-captured collectives (the launch-
overhead elimination (the kernel-count reduction (the fused
collective: the reduce-scatter of gradients fused with the
grad-clip norm (the one-pass trick (the quantized-
communication research: the 8-bit gradient ring (the
error-feedback compensation (the memory of the residual
(the compression-accuracy trade's honest table (the 2×
bandwidth saving's 0.1% convergence cost — the Numbers
Game honestly marked as "active research, not default";
the RDMA underbelly (the NIC's DMA engine of module 13
ch05 returning at scale: the RDMA verb's one-sided
read/write (the GPU-direct RDMA: the NIC DMAs HBM directly
(no CPU staging (the pinned-memory requirement (the
registration (the memory-region keys (the rkey's protection
(the fabric as an extension of the GPU's address space (the
peer-to-peer over CXL vs RDMA (the coherent-vs-message
distinction (the one-sided semantics = "remote memory as
a numa node that is very far away" (the Simplicity of the
collective model vs the generality of one-sided (the
PGAS languages' niche (the NCCL-ring-on-RDMA stack: the
verbs' QPs (the queue pair's send/recv rings (module 13's
ring returning a final time (the completion queues (the
polling vs the interrupt (the CPU's 100% polling-spin
core (the "the dedicated progress thread" (the busy-wait
vs the energy trade (the fabric's latency floor: the
~1-2 µs RDMA (the protocol stack's tax: the TCP path's
~20-50 µs vs the verbs' ~1-2 (the kernel-bypass story
( module 13 ch04's syscall-elimination returning as the
network's own ladders)). The synchrony problem (the
synchronous training's lockstep: the slowest GPU sets the
step time (the straggler's sources: the data-loader's
stall (the page-cache miss (the NUMA-remote HBM (the
thermal throttle (the ECC scrub (the noisy neighbor
(the network's congestion (the incast (the completion-
tree's collapse (the tail-latency amplification: one
straggler in 25,000 = the whole step +ε (the 99.9%-
slowdown arithmetic (the straggler's expected count at
fleet scale (the mitigation ladder: the redundancy (the
speculative step-replay (the async-Adam's staleness (the
local-SGD's N-step sync (the staleness-vs-convergence
trade (the 1-bit-SGDM's "the gradient can be stale if
it's cheap" finding (the DP's gradient-staleness
tolerance (the 16-32 step buffers (the convergence's
robustness to asynchrony (the "the synchrony is a
convention the math tolerates breaking" insight (the
communication-avoiding optimizers (the LAMB/LARS's
layer-wise scaling (the large-minibatch training's
linear-scaling rule: lr × k (the warmup's necessity (the
gradient-noise-scale theory (the batch-size ceiling (the
2018+ batch-8192 recipes (the "the batch is a memory
unit too" quip made precise (the gradient-accumulation's
effective-batch arithmetic (the micro-batch × accumulation
= the global batch (the two-level memory: the GPU's
activation fit × the accumulation count (the formula
tied back to ch02's activation arithmetic). The
datacenter's memory hierarchy closed (the final table,
mandatory: registers (module 17 (SRAM/L1-L2-L3 (HBM
(node DRAM (the fabric (the storage tier (the cold
tier: S3-class (the training-data path: the sharded
dataset (the shuffle's memory: the index's 2^33
permutation (the distributed shuffle (the streaming
shards (the object-store's 100 GB/s aggregate read (the
checkpoint tier: the model's state paged to the parallel
filesystem (the burst-write of the 8 TB state (the
aggregate 1 TB/s of the modern parallel FS (the
checkpoint's wall-clock: 8 TB / (N_rails × 50 GB/s (the
minutes-scale number (the failure-tolerant checkpoint
cadence (the fleet's MTBF: with 25k GPUs, a failure
every few hours is EXPECTED (the arithmetic: 25,000
GPUs × 1/5-year-MTBF = ~14 failures/day (the checkpoint
every 30 min = the ≤30 min of lost work (the elastic
restart's re-shard (the job-control layer: the
preemptible-capacity reality (the spot-instance's
economics (the checkpoint-restore speed vs the instance
price (the "the training job is a memory process with
paging" — the final identification: the datacenter
trains a model like a 1980s computer ran a program too
big for RAM: checkpoint = swap, the fleet = the
spinning disk, the HBM = the RAM (the historical
inversion stated and drawn as the module's closing
diagram: the swap diagram of module 01 ch03 next to the
checkpoint-restore pipeline — same shapes, 10^9× the
bytes). Insiders: the NCCL_DEBUG=INFO lore (the ring's
construction printed (the "channel count" (the
topology's XML (the auto-tuning's logs (the reader's
first distributed debug session decoded); the
CUDA_VISIBLE_DEVICES reordering trap (the PCIe-bus
order vs the NVLink's physical order (the NUMA-node's
binding (the "GPU 3 is on the other socket" 30%
slowdown (the nvidia-smi topo -m read (the affinity's
pinning (the taskset/numactl flags (the fabric-
congestion diagnosis: the ibdump/the dstat-of-
collectives (the netlatency's incast signature (the
PFC's storm folklore (the RoCE's lossless-config
fragility (the ECN's thresholds (the "the cluster's
congestion window is a memory hierarchy's admission
control" reading); the failure-domain topology (the
rack's power (the rail's ToR switch (the blast radius
(the checkpoint's geographic humility (the cross-region
restore's bandwidth: the 100 Gbps backbone = 12.5 GB/s
= the 8 TB state in ~11 minutes (the "you don't
restore cross-region in a heartbeat" honesty (the
fire-drill discipline); the training throughput's
de-facto metric: tokens/day/GPU (the fleet's tokens/
day: the 25k × H100 × ~2000 tokens/s/GPU effective ≈
4×10^9 tok/s ≈ 10^14/day-class numbers for frontier
runs (the numbers' honesty: order-of-magnitude, the
public-training-run estimates (the "the world's
largest programs measured in tokens" closing line.
Lab (CPU-mandatory): the ring simulation (a 100-line
python ring AllReduce over N "nodes" (the step-by-step
chunk flow logged (the bytes-per-link counter compared
to the 2(N-1)/N formula (the latency-vs-bandwidth
trade measured by varying chunk size (the two regimes
observed (the crossover explained); the topology-aware
ring on paper (a 16-GPU 2-node cluster (the naive ring
vs the rail-optimized ring drawn (the cross-link count
computed for both (the improvement quantified); the
MTBF/checkpoint arithmetic (the fleet-MTBF calculator
( the 25k-GPU failure-per-day computed (the checkpoint
interval swept (the lost-work-vs-checkpoint-cost Pareto
plotted (the optimal interval found (the math of
production training's housekeeping made visible; the
latency-ladder measurement (a mini fabric: two
processes over localhost TCP vs shared memory (the
RTT measured for both (the ~10× gap (the "the local
model of the datacenter's tiers" honesty (the numbers
compared to the chapter's table).

**Connections requirement.** Every chapter's Connections must
include (beyond same-module): ch01 → module 01 ch02 (the tower
extended), module 12 ch02 (the GPU coalescing twin), module 01
ch14 (the refcounted storage = PyObject); ch02 → module 19 ch04
(the GC-theory mirror: liveness of tensors vs activation memory)
, module 01 ch23 (the grand table's allocation story —
**mandatory: m01 ch23's orphan repair, the "one more row" framing
(the tensor as the 17th language's object model)); ch03 → module
01 ch03 (COW → ZeRO/COW-prefix — mandatory identification),
module 15 ch03 (the vring/NCCL ring kinship), module 09 ch05
(the distributed-order zoo — the staleness trade); ch04 →
module 01 ch03 (paged KV = virtual memory — the module's thesis
identification, mandatory), module 07 ch04 (the reclaimer's
kinship), module 13 ch03 (the buffer autotuning's kinship —
**mandatory: repairs m13 ch03's orphan status via the KV-block
pool / window-autotuning correspondence**), module 11 ch04 (the
WAL/checkpoint resemblance — durability of training state);
ch05 → module 13 ch01/05 (the DMA ring at fabric scale, RDMA),
module 01 ch02 (the final tower), module 17 ch05 (the economics
that cap the fleet), module 09 ch03 (the lock-free lessons in
the collectives).

**The final exam** (`quiz/final-exam.md`): 12 questions, worked
answers, 5 parts (one per chapter, 3+3+2+2+2), at least one
question must compute a KV-cache/paged-attention capacity
number, at least one must derive the ring AllReduce's
2(N-1)/N bytes-per-link, and at least one must walk a ZeRO
stage-3 memory budget for a given model and GPU count.
