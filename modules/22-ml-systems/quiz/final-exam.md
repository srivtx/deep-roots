# Module 22 final exam — ML systems & training infrastructure

Twelve questions in five parts — three on the tensor (chapter 01), three on
the autograd tape (chapter 02), and two each on sharding (chapter 03),
inference serving (chapter 04), and collectives (chapter 05). Closed book;
every answer is worked, not stated — the derivations are the point, and the
arithmetic is the engineering. Scoring: 11+ = you own this module; 8-10 =
solid, revisit the flagged chapters; <8 = the chapter quizzes are the
study path.

**Part I — the tensor: strides, layouts, dtypes (chapter 01)**

1. **Diagnose.** A 40-block conv stack trains at a peak of ~7.6 GiB of
   activations. After a refactor the peak doubles to ~15.3 GiB — no layer
   was added, no error fired. Every block runs
   `x.transpose(1, 3).reshape(B, C*H*W)` on activations `[32, 512, 56, 56]`
   fp32 that arrive NCHW-contiguous, and each block's input stays live for
   backward. (a) The stride tuples before and after the transpose, and the
   merge-condition check that forces a copy. (b) The hidden-copy bytes per
   block and for the whole stack. (c) The one-line assertion that catches
   it, and the fix.

2. **Derive.** A conv layer's inner channel loop sums C = 192 channels at
   one spatial offset; activations are fp16, lines are 64 B, spatial is
   14×14. (a) NCHW: the channel step in bytes, lines fetched, bytes
   fetched, useful bytes, line occupancy. (b) NHWC: the same numbers.
   (c) The reuse factor, and why it is 32 here when the chapter's fp32
   case produced 16. (d) In what sense is `channels_last` a
   metadata-only relayout — what changes, and what never does?

3. **Predict.** A serving region reboots after a power incident: each node
   mmaps a 70 GB fp16 safetensors checkpoint from local NVMe (7 GB/s).
   (a) Cold-start time per node, and why the second load on the same box
   takes ~1 s. (b) The same checkpoint served in 4-bit. (c) A monitoring
   feature keeps a 4 MB `.narrow()` view of one 70 GB mmap'd tensor alive
   indefinitely — what is pinned, and what is the one-line fix?

**Part II — the tape: activations and resident state (chapter 02)**

4. **Predict.** You want to train a 7B model on ONE 80 GB GPU with
   mixed-precision Adam. (a) The resident state in bytes/param and total
   (fp32 master weights + fp16 compute copy + fp16 grads + fp32 m, v).
   (b) The same stack with an 8-bit optimizer (int8 m, v with block
   scales, fp32 master). (c) How much HBM remains for activations, how
   many tokens that buys at ~4.8 MiB/token (fp16, 32 layers), and what
   the arithmetic concludes the real wall is.

5. **Derive.** From the chapter's 12-layer activation table (2,464 MB per
   layer at batch 8×1024 fp32; the attention rows are scores 512 +
   softmax probabilities 512 + dropout mask 128 MB; the logits/CE head is
   3.07 GiB; fp16 weights are 1.32 GiB): (a) per-layer and total
   activation memory under flash attention, which never materializes
   those three rows. (b) The peak including the head. (c) What the ~22×
   rule becomes. (d) Why exactly those rows are quadratic in sequence
   length while every other row is linear.

6. **Diagnose.** A loop accumulates 8 micro-batches per optimizer step:
   `loss.backward()` × 8, then `optimizer.step()` — with no `zero_grad`
   anywhere. The first optimizer step behaves; the loss then degrades and
   diverges, and the logged global grad-norm climbs roughly linearly with
   optimizer step count. Name the mechanism, derive the magnitude of the
   k-th step's update, explain why no `retain_graph` is needed, and give
   the fix — including why `set_to_none=True` is the right form of it.

**Part III — sharding the unshardable (chapter 03)**

7. **Derive.** A 70B-parameter model in mixed-precision Adam (16 B/param
   of training state), 80 GB GPUs, 30 GB per GPU of activations, 5 GB
   runtime. (a) The total state and plain-DP per-GPU memory. (b) Per-GPU
   memory under ZeRO-1, ZeRO-2, and ZeRO-3 at N = 8. (c) The minimum N at
   which ZeRO-3 fits, and the per-GPU budget there. (d) One sentence on
   ZeRO-3's communication price, and why tensor parallel — not pipeline
   parallel — is the axis you add inside the node.

8. **Predict.** QLoRA fine-tunes that same 70B model on ONE 48 GB GPU:
   base frozen in 4-bit (0.5 B/param), adapters at 0.4% of the parameter
   count in fp16, Adam with fp32 m, v and fp32 master on the adapters
   only, fp16 adapter gradients. (a) Every resident component and the
   total. (b) The compression vs full fine-tuning. (c) What the paged
   optimizer does when activations spike. (d) The principle, in one line.

**Part IV — the KV-cache machine (chapter 04)**

9. **Derive.** A 70B-class serving node: one 80 GB A100, weights in 4-bit
   (35 GB), 5 GB runtime, GQA model (80 layers, 8 KV heads, head_dim 128,
   fp16 KV, 16-token blocks). (a) Per-token KV bytes. (b) The KV pool and
   the block count. (c) Concurrent 4k-context sequences; then at 8k.
   (d) With a 1,024-token system prompt shared copy-on-write by requests
   that run 4k tokens total: the new capacity and the prefix bytes saved.
   (e) The same pool with fp8 KV.

10. **Derive.** Decode on a 7B fp16 model at 2k context on an A100
    (2 TB/s HBM, 312 TFLOP/s fp16): one step reads the 14 GB of weights
    once plus each resident sequence's 1 GB of KV to emit B tokens.
    (a) Arithmetic intensity at B = 1 and B = 64, each against the ridge
    point. (b) Aggregate tok/s at both. (c) The KV-floor asymptote: what
    per-token cost approaches as B grows, and the ceiling it sets.
    (d) With 4-bit weights (3.5 GB): the new intensities, and what each
    lever — batching vs quantization — actually moves.

**Part V — collectives and the datacenter (chapter 05)**

11. **Derive.** N GPUs in a unidirectional ring (GPU i sends only to GPU
    i+1 mod N), each holding a D-byte gradient tensor, split into N
    chunks of D/N. (a) Walk the reduce-scatter phase — what every link
    carries at each of the N−1 steps, and where a chunk ends up — then
    the all-gather phase, and total the bytes per link. (b) The N → ∞
    limit and what it means. (c) Apply: a 7B model's fp16 gradients
    (14 GB) on an 8-GPU NVLink domain at 400 GB/s per link — bytes per
    link and completion time. (d) What the ring pays for this, and what
    NCCL does about it.

12. **Predict.** A 16,384-GPU synchronous data-parallel run: each GPU
    independently has a 0.05% chance per step of a 2×-slow step (thermal,
    congestion). (a) Expected stragglers per step. (b) P(no straggler) —
    and what the fleet's step time therefore is. (c) At a nominal 2,000
    tok/s/GPU, what the tail costs in tokens/day, and why buying more
    GPUs cannot fix it. (d) The mitigation ladder, one line per rung,
    and the one insight all rungs share.

<details>
<summary>Answers</summary>

**Part I**

1. (a) Contiguous `[32, 512, 56, 56]` fp32 has strides
   **(1,605,632, 3,136, 56, 1)**. `transpose(1, 3)` swaps shape and stride
   entries: shape `[32, 56, 56, 512]`, strides **(1,605,632, 1, 56,
   3,136)** — zero bytes moved, a view. `reshape(32, 1,605,632)` must merge
   dims 1–3, permitted only when `stride[i] == stride[i+1]·size[i+1]`:
   1 == 3,136·56 = 175,616 is false, and 56 == 3,136·512 is false —
   row-major order is unreachable in place, so `reshape` silently calls
   `contiguous()`: the hidden copy. (b) 32 × 512 × 56 × 56 × 4 B =
   205,120,896 B ≈ **196 MiB per block**; × 40 blocks = **≈7.6 GiB**. The
   original is still live (the preceding op's tape entry saved it —
   chapter 02's blow-up rule), so the copy is *additive*: peak 7.6 →
   **15.3 GiB, the "2× with no code change" signature** of an upstream
   layout change. (c) Assert `x.is_contiguous()` at the block entry (or
   compare `untyped_storage().data_ptr()` across the reshape). Fix:
   materialize the layout conversion once per step at the pipeline
   boundary — one 196 MiB copy instead of 40 — or consume the NCHW tensor
   directly and transpose the *weight*.

2. (a) NCHW: the channel step is H·W = 196 elements = **392 B**; each
   channel's 2-B fp16 lands in its own 64-B line: **192 lines, 12,288 B
   fetched, 384 B useful — 3.1% occupancy**, 32× waste, and each line's
   other 62 B belong to another channel's row 392 B away. (b) NHWC: the
   channel step is 1 element; 192 consecutive fp16 = 384 B = **6 lines,
   100% occupancy**. (c) Reuse = 12,288 / 384 = **32×** — line size ÷
   element size (64/2) once C ≥ 32; the chapter's fp32 case is 64/4 = 16.
   The factor is line arithmetic, not a property of the layout. (d)
   `channels_last` rewrites the stride tuple (the C stride becomes 1) — a
   *stride decision, not a buffer decision*; `is_contiguous(memory_format=…)`
   is a predicate over strides. `permute` gives the channels-last view
   free when the bytes already sit in that order; `.to(memory_format=…)`
   moves bytes only because the buffer was materialized in the other
   order. Tensor Cores demand NHWC-shaped fragments, which is why cuDNN's
   fast fp16/bf16 kernels are channels-last — module 12 ch02's coalescing
   rule on the C axis.

3. (a) safetensors is an 8-byte header length + a JSON map + one flat
   buffer; loading is mmap + parse, so tensors return as *views over the
   mapping* — page-cache-backed (module 14 ch03). Cold: 70 GB ÷ 7 GB/s =
   **10 s** per node; the second load hits resident pages — page-table
   setup, ~**1 s**. On a fleet reboot that 10:1 × model count is the
   user-visible outage, which is why it is an incident. (b) 4-bit: 35 GB
   ÷ 7 GB/s = **5 s** — quantization halves cold start along with
   residence. (c) A view is `(offset, strides)` over the whole
   refcounted StorageImpl, so the 4 MB `.narrow()` pins **all 70 GB** of
   the mapping (the offset-storage trick: one small slice holds the
   entire buffer). Fix: `.clone()` the 4 MB when the view must outlive
   the batch.

**Part II**

4. (a) fp32 master 4 + fp16 compute copy 2 + fp16 grads 2 + fp32 m, v 8 =
   **16 B/param × 7e9 = 112 GB > 80 GB** — plain mixed-precision Adam
   does not fit at all. (The master exists because fp16's 10-bit mantissa
   rounds an update lr·g ≈ 2⁻¹⁷ against a weight of 1.0 away to nothing —
   the fp16 copy is compute-only.) (b) 8-bit optimizer: m, v in int8 with
   2,048-element block scales = 2 B/param of state: 2 + 2 + 2 + 4 =
   **10 B/param = 70 GB**. (c) ~10 GB minus ~1 GB of context and
   workspaces ≈ **9 GiB**; at ~4.8 MiB/token that buys ≈ **1,900 tokens:
   micro-batch 1×1024 (4.9 GiB) fits, 2×1024 (9.8 GiB) OOMs** — the
   batch staircase. Conclusion: on one GPU the *resident state*, not the
   activations, is the wall — chapter 03's sharding imperative is this
   multiplication, not fashion, and the paged optimizer (state in CUDA
   unified memory, faulting to CPU DRAM) is the rung between 70 and
   112 GB.

5. (a) The three attention rows total 1,152 MB = 47% of the layer; flash
   attention tiles Q·Kᵀ into SRAM and computes the softmax online (running
   max and denominator), never writing the S×S matrices to HBM — so
   per-layer = 2,464 − 1,152 = **1,312 MB**; × 12 layers ≈ **15.4 GiB**.
   (b) + 3.07 GiB head ≈ **18.5 GiB** (from ~32 GiB — a ~42% cut, the
   biggest software lever before any checkpointing). (c) 18.5/1.32 ≈
   **14×** the fp16 weight bytes (down from ~22×); against fp32 parameter
   bytes, ~7×. (d) Those rows are `[8, 16, 1024, 1024]` — heads × seq²,
   quadratic in sequence length; every other row is `[T, 2048]`-shaped,
   linear in tokens. Long context multiplies exactly the rows flash
   attention deletes.

6. Mechanism: leaf `.grad` buffers are *persistent* state and every
   backward accumulates into them with `+=` — that is the feature when
   the accumulation window is one step, the bug when the window never
   closes. After k optimizer steps `.grad` holds the sum over 8k
   micro-batches ≈ 8k·ḡ, so the k-th update is applied with **k× the
   intended magnitude** — the effective learning rate grows linearly,
   which is the linearly climbing grad-norm and the divergence after a
   healthy first step. No `retain_graph` is needed: each micro-batch
   builds its *own* tape and `.backward()` frees it after one sweep;
   the retain error only fires when one graph is backwarded twice. Fix:
   `optimizer.zero_grad(set_to_none=True)` at the top of each window —
   it drops the buffers (lazy reallocation on next use: no memset),
   releases the bytes, and leaves `None` meaning "never computed," so
   unused parameters are skipped and wiring bugs print as `None`, not a
   stale 0.

**Part III**

7. (a) State = 16 × 70e9 = **1.12 TB**; plain DP replicates all of it:
   **1,120 GB per GPU — 14× over an 80 GB part**. (b) At N = 8:
   ZeRO-1 shards the 12 B/param optimizer block: 140 + 140 + 840/8 =
   **385 GB**. ZeRO-2 also shards gradients: 140 + (140 + 840)/8 =
   **262.5 GB**. ZeRO-3 shards everything: 1,120/8 = **140 GB — still
   over the 45 GB budget** (80 − 30 − 5). (c) N ≥ 1,120/45 = 24.9, so
   **N = 32**: 35 GB of state + 30 + 5 = 70 GB, 10 GB of margin (N = 16
   gives 70 GB of state + 35 = 105 — no). (d) ZeRO-3's parameter
   AllGathers fire in forward *and* backward, putting communication at
   ~**1.5× plain DP** — bandwidth traded for memory, a good trade only
   on a fast fabric. And at N = 8 even ZeRO-3 fails, so the missing axis
   is TP: its traffic is two latency-tight AllReduces per layer —
   NVLink-shaped — which is exactly why TP stays intra-node while PP's
   sparse, latency-tolerant activation send/recvs span nodes.

8. (a) Base 4-bit: 70e9 × 0.5 B = **35 GB** (dequantized on the fly into
   fp16 compute — read-mostly, never written). Adapters: 0.4% × 70e9 =
   280M params — fp16 weights 0.56 + fp16 grads 0.56 + fp32 master 1.12 +
   fp32 m, v 2.24 = **4.48 GB** of trainable state. Total ≈ **39.5 GB**,
   leaving ~7 GB for activations and runtime on the 48 GB card. (b) Full
   fine-tuning is 16 B/param = 1,120 GB → **~28× compression** — obtained
   by refusing to store what you are not updating. (c) The paged
   optimizer keeps m, v in CUDA unified memory, so capacity pressure
   *faults* optimizer pages to CPU DRAM — an implicit, fault-driven
   ZeRO-Offload. (d) Freeze what you don't train, page what you rarely
   touch: the frozen 4-bit base is malloc's read-mostly generation at
   terabyte scale.

**Part IV**

9. (a) 2 × 80 × 8 × 128 × 2 B = 327,680 B = **320 KiB/token** (the 2 is
   K and V; only the 8 KV heads pay, not the 64 query heads — GQA).
   (b) Pool = 80 − 35 − 5 = **40 GB**; block = 16 × 320 KiB = 5 MiB →
   **8,192 blocks**. (c) A 4k context = 1.25 GiB = 256 blocks → **32
   sequences**; 8k = 2.5 GiB → **16** — the pool, not the FLOPs, sizes
   the product. (d) The prompt is 64 blocks, shared once and refcounted
   like a PyObject; each request then needs 256 − 64 = 192 private
   blocks: (8,192 − 64)/192 = **42 concurrent (+31%)**, and the 42 × 64
   × 5 MiB = 13.1 GB of identical prefix KV collapses to **320 MiB —
   ~98% saved** (a 100-request flood would duplicate 31.25 GiB). (e) fp8
   halves per-token bytes: 2.5 MiB blocks, 16,384 of them, **64
   sequences at 4k — the pool doubles**. For contrast, MHA at 64 KV
   heads would pay 2.5 MiB/token = 10 GB per 4k sequence, 4 sequences
   in the whole pool: GQA's 8× cut is why the node works.

10. (a) AI = 2·7e9·B / (14e9 + B·1e9). B = 1: 14/15 = **0.93 FLOP/byte**;
    B = 64: 896/78 = **11.5 FLOP/byte** — both far left of the ridge
    312/2.0 = **156 FLOP/B**: deep on the memory-bound slope, a memory
    stream, not a computation. (b) B = 1: 2e12/15e9 = **133 tok/s**;
    B = 64: 64 × 2e12/78e9 = **1,641 tok/s**. (c) The 14 GB of weights
    amortize across the batch (14/B GB per token — 64× less at B = 64)
    but each sequence's 1 GB of KV is private and never amortizes: as
    B → ∞ the cost per token approaches the KV read alone, ceiling ≈
    2e12/1e9 ≈ **2,000 tok/s aggregate — the KV floor** no batch buys
    past. (d) 4-bit: B = 1 → AI = 14/4.5 = **3.11**, 444 tok/s (3.3×);
    B = 64 → 896/67.5 = 13.3, 1,896 tok/s (~1.15×). Batching slides the
    point ~12× right by amortizing weight *bytes*; quantization shrinks
    the bytes themselves — 3× for the B = 1 latency product, where
    weights are the whole denominator, and marginal at large B. Hence
    W4A16: weights take group-128 4-bit scales; activations stay 16-bit
    because they hold the ~0.1% outlier channels at ~100× magnitude
    (LLM.int8's finding).

**Part V**

11. (a) Reduce-scatter: at each of the N−1 steps, *every* link
    simultaneously carries one D/N chunk; chunk c leaves its home, hops
    rightward folding in partial sums, and after N−1 steps sits fully
    reduced at one owner — per link (N−1)·D/N bytes. All-gather: N−1
    more steps circulate the reduced shards until everyone holds all N —
    another (N−1)·D/N. Total per link: **2(N−1)/N × D**. (b) → **2D as N
    grows**: each GPU's egress converges to "send every byte twice" —
    adding GPUs adds FLOPs and memory, not per-GPU communication; the
    ring meets the Chan–Gropp–Thakur lower bound of (N−1)/N·D per phase,
    hence bandwidth-optimal. (c) 2×7/8 × 14 GB = **24.5 GB per link**;
    time = 24.5 GB ÷ 400 GB/s ≈ **61 ms**, plus 14 hops × ~1 µs = 14 µs
    — negligible: bandwidth-bound, as gradient-sized tensors always are.
    (d) The price is **latency: 2(N−1) serialized hops** — a flat
    25,000-GPU ring is 50,000 hops, hopeless for small tensors. Trees
    give O(log N) latency but the naive root bottlenecks at O(N·D) per
    link (recursive-halving/double-tree recovers both); the crossover is
    ≈ N·L·B, and NCCL's tuner picks rings above it, trees below, then
    builds hierarchy: intra-node NVLink 8-rings joined by an inter-node
    ring of *nodes* — 25,000 ranks collapse to ~3,100 ring members.

12. (a) 16,384 × 5×10⁻⁴ = **8.2 stragglers per step, expected**. (b)
    P(none) = 0.9995^16,384 ≈ e^(−16,384×0.0005) = e^−8.2 ≈ **2.7×10⁻⁴**
    — 99.97% of steps contain at least one 2× straggler. The all-reduce
    is a barrier in disguise: the step time is the max of 16,384 draws,
    and the max of that many samples *is* the far tail, every step —
    a per-GPU p99.9 of 2× median becomes the fleet's median, so the
    fleet runs at ≈ **2× a single GPU's median step**. (c) Nominal:
    16,384 × 2,000 ≈ 3.3×10⁷ tok/s ≈ 2.8×10¹² tokens/day; running at
    the tail roughly halves it to ≈ **1.4×10¹²/day**. More GPUs make it
    worse, not better: E[stragglers] = N·p scales with N and P(none) → 0
    faster — you cannot FLOP your way out of a max-of-N statistic.
    (d) Ladder, weakest assumption first: redundancy/speculative replay
    (recompute the straggler's work on a spare — idle duplicate FLOPs);
    ~1-step staleness (async-Adam proceeds with the stale gradient and
    reconciles); local SGD, N-step sync (k micro-steps on local weights
    then average — communication ÷ k, graceful degradation); 1-bit SGDM
    (quantized *stale* gradients buffered over 16–32 steps still
    converge, with momentum compensation). The shared insight:
    **synchrony is a convention the math tolerates breaking** — SGD's
    own noise already dominates bounded asynchrony.

</details>
