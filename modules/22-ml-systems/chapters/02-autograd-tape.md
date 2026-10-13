# Chapter 02 — Autograd: the tape as a heap

> **The 30-second version.** Backpropagation's algorithm is textbook; its
> *memory discipline* is what production ML engineering actually is. Every
> forward op appends an entry to a Wengert list — the tape — recording the op,
> its operands' (pointer, shape), and the tensors backward will need. They
> cannot be freed until backward runs: the tape is a heap whose liveness is
> written by the chain rule, and a 12-layer transformer at batch 8×1024 holds
> ~29 GiB of activations, ~20× its fp16 weights. Checkpointing, the backward's
> allocation storm, mixed precision, and Adam's state are its consequences.

## The Wengert list: a ledger you write by existing

Chapter 01 gave us the tensor as (data_ptr, shape, dtype, strides) over a
refcounted storage; autograd adds one mechanism: **every op appends an entry
to a linear record** — the Wengert list, or tape — and that entry keeps heap
references alive. An entry is (op, operands, saved-for-backward tensors).
Here is a real 5-op tape, the computation our lab trains:

```text
THE TAPE for  L = sum( relu(x@W1 + b) @ W2 )
entry  op      operand A (ptr, shape)       operand B (ptr, shape)       saved for backward
-----  ------  ---------------------------  ---------------------------  ------------------------
  0    @       x  (0x7f21a000, [64,1])      W1 (0x7f21a100, [1,64])      x, W1    -> out t0
  1    +       t0 (0x7f21c000, [64,64])     b  (0x7f21a180, [64])        shapes  -> out t1
  2    relu    t1 (0x7f21d000, [64,64])     --                           t2      -> out t2
  3    @       t2 (0x7f21e000, [64,64])     W2 (0x7f21a200, [64,1])      t2, W2  -> out t3
  4    sum     t3 (0x7f21f000, [64,1])      --                           --      -> out L
```

Read entry 3, the matmul: backward needs *both inputs* (dX = dY@Wᵀ, dW =
Xᵀ@dY), so the entry pins t2 and W2; entry 1, the add, saves *nothing*. The
chapter's first law falls out — the **memory blow-up rule**: backward needs
forward's *inputs*, so the sum over ops of saved-tensor bytes is the
activation memory. Per-op cost table (fp32; PyTorch 2.x eager):

| op | saves for backward | bytes/el | why the vjp needs it |
|---|---|---|---|
| add | nothing (shapes if broadcast) | 0 | dL/da = dL/dz; a sum if broadcast |
| mul | both operands | 2× | d(a·b) = b·da |
| matmul | both operands | 2× | dX = dY@Wᵀ, dW = Xᵀ@dY |
| relu | output | 1× | mask from the sign of the output |
| softmax | output | 1× | J = diag(p) − ppᵀ |
| dropout | **bool mask, full tensor** | 1 B/el | the randomness is not reconstructible |
| layer norm | input + mean + rstd | ~1× | normalization's Jacobian |

## The 12-layer ledger: activation memory computed to the byte

Fix the reference model: GPT-2-class, 12 layers, hidden d=2048 (16 heads),
MLP 4d, vocab 50,257, tied embeddings → **707 M parameters** (fp32 2.64 GiB,
fp16 1.32 GiB). The unit of account: the residual-stream tensor,
2048×2048×4 B = **16 MB per layer** (64 MB at batch×seq = 8×1024 = 8192
tokens). Every line is a saved tensor from the per-op table, fp32 and bool,
MB = MiB:

| saved tensor (per layer) | shape | @2048 tok | @8×1024 |
|---|---|---|---|
| residual stream in (x) | [T, 2048] | 16 | 64 |
| LN1 out + mean/rstd | [T, 2048] | 16 | 64 |
| Q, K, V | 3×[T, 2048] | 48 | 192 |
| attention scores | [8,16,1024,1024] | 128 | 512 |
| softmax probabilities | [8,16,1024,1024] | 128 | 512 |
| dropout mask on probs (bool) | [8,16,1024,1024] | 32 | 128 |
| context (P@V) | [T, 2048] | 16 | 64 |
| proj out | [T, 2048] | 16 | 64 |
| dropout mask (bool) | [T, 2048] | 4 | 16 |
| dropout out | [T, 2048] | 16 | 64 |
| post-attn residual (LN2 in) | [T, 2048] | 16 | 64 |
| LN2 out | [T, 2048] | 16 | 64 |
| fc1 out | [T, 8192] | 64 | 256 |
| gelu out | [T, 8192] | 64 | 256 |
| fc2 out + dropout out | 2×[T, 2048] | 32 | 128 |
| dropout mask (bool) | [T, 2048] | 4 | 16 |
| **per-layer total** | | **616** | **2,464** |
| **× 12 layers** | | 7,406 | **28.9 GiB** |
| head: logits + CE log-softmax | [8,1024,50257] ×2 | 785 | 3.07 GiB |
| **peak, no checkpointing** | | | **≈32 GiB** |

The quoted ratio: 28.9 GiB is **~22× the fp16 weight bytes** — the "~20×
rule" — or ~11× the fp32 parameter bytes: a *shape function, not a
constant* (activation bytes = tokens × 2,464 MB/layer). Ask "20× of *what*"
before repeating it. Three consequences:

- **Per-token cost: 308 KiB/layer, 3.6 MiB/token** net-wide (fp32); each
  1024-token sample is a 3.5 GiB *step* on the batch-size dial. With ~11 GiB
  resident (below), a 16 GB card fits batch 1 (4.0 GiB) and OOMs at batch 2;
  a 12 GB card dies at batch 1 — "batch-size 1 crashes, batch-size 2 OOMs"
  is this staircase, not bad luck.
- **The attention rows dominate**: scores + probs + mask = 47% of the layer.
  Flash attention never materializes them — per-layer 1,312 MB, peak ~18 GiB:
  the biggest software lever, before any checkpointing.
- **K and V are the KV cache** — 2 of the 17 rows, freed after their layer's
  backward in training, kept for all layers at inference: chapter 04's heap
  of tokens, planted.

## Checkpointing: trading FLOPs for bytes

The tape's entries are choices, not fate: saved tensors can be *dropped* and
re-derived by recomputing the op during backward. Formalize: checkpoint every
k layers — store the L/k boundary inputs (b bytes each), recompute k layers
live (a bytes):

```text
peak(k) = (L/k)·b + k·a          minimize over k
```

With b = a (uniform MLP), k* = √L and peak = 2√L·a = **O(√L × per-layer)** —
the textbook result (Chen et al. 2016): one extra forward per step (3F → 4F,
backward ≈ 2F) buys sublinear memory at 30-40% compute overhead. Our
transformer breaks the symmetry: b = 64 MB, a = 2,464 MB, so
k* = √(L·b/a) = √(12/38.5) = **0.56 → clamp to every layer** — why
Megatron-LM's classic schedule checkpoints *every* transformer layer, not
every √L. The Pareto at batch 8×1024 (peaks include the 3.07 GiB head):

| schedule | peak activations | step time |
|---|---|---|
| never checkpoint (k = L) | 31.9 GiB | 3F (1.00×) |
| every √L ≈ 3 layers | 10.5 GiB | 4F (1.33×) |
| every layer (k = 1) | 6.2 GiB | 4F (1.33×) |

The trap: every layer is recomputed once in *any* k < L schedule, so time
is constant — the √L point is dominated. √L is optimal only while boundary
cost ≈ layer cost.

**Selective checkpointing inverts the policy.** Instead of "store boundaries,
recompute everything inside," rank ops by *bytes saved per recompute FLOP*:
recompute the expensive-to-store, cheap-to-recompute attention block (47% of
bytes, a few percent of FLOPs); save the residual stream, QKV, and MLP —
Megatron's selective recomputation (Korthikanti et al. 2022): ~5× activation
cut at ~5% overhead. The policy formalized: choose store/recompute per op to
minimize peak under a time budget — an integer program Checkmate (Jain et
al. 2021) solves optimally. And one op is unrecomputable by *math* but
recomputable by *engineering*: the dropout mask is a function of the RNG
state, which `torch.utils.checkpoint` restores (`preserve_rng_state=True`,
the default) — the "random" mask replays bit-exact.

## The engine: a dependency scheduler with an allocation habit

Backward is not a tree walk — it is a **scheduler**: reverse the graph,
count each node's consumers, run a ready queue — a node is ready when its
last consumer has delivered its gradient. Every order the queue emits is a
reverse-topological order (the DFS-postorder-reversal our lab implements);
PyTorch's C++ engine is this plus per-device worker threads. Its memory
habits:

- **Leaf `.grad` buffers allocate lazily, on first use.** A parameter used
  twice gets two vjp contributions `+=`-accumulated into one buffer —
  accumulation semantics, and the *persistent* state: gradient accumulation
  over N micro-batches is N forward/backward pairs, each tape freed on exit
  while `.grad` survives, then one `optimizer.step()` per N — effective
  batch = micro-batch × N (chapter 05 returns).
- **`zero_grad(set_to_none=True)`** (flag since PyTorch 1.7, Oct 2020;
  default in the 2.x era): set `.grad = None` instead of `memset`-ing every
  parameter each step — lazy reallocation skips the memset, releases the
  buffer, skips unused parameters. The semantic dividend: **None means
  "never computed," a 0-tensor means "computed and zero"** — the distinction
  that diagnoses "why is this parameter not training" in one `print`.
- **The allocation storm.** Every vjp allocates a fresh gradient tensor
  (dX = dY@Wᵀ *is* a new 64 MB tensor per layer): a 12-layer backward is
  dozens of multi-GiB allocations in reverse order, served by the caching
  allocator (module 01 ch19's arena/block flavor) from the blocks forward
  freed — LIFO-friendly, since backward retraces the path. Peak = the
  activations plus one layer's transients.
- **Double-backward is a tape-of-tape.** With `create_graph=True`, the vjps
  themselves are recorded, so the *backward's* intermediates become saved
  tensors for a second sweep — WGAN-GP's penalty needs it, pays ~2× peak.
- **`detach()` is the cut.** A new node sharing the *same storage* (same
  data_ptr) but with no backward edges: gradient stops there. `.backward()`
  is the *destructor*: it walks the tape and frees the saved tensors on exit
  — a second backward raises "Trying to backward through the graph a second
  time... Saved intermediate values of the graph are freed... Specify
  retain_graph=True." `retain_graph=True` keeps those roots alive.
- **dgrad vs wgrad.** The matmul vjp makes two products: dX (the *data*
  gradient, transient traffic toward earlier layers) and dW (the *weight*
  gradient, accumulated into `.grad`). Data parallel AllReduces only the
  wgrads, bucketed (25 MB) and overlapped with backward — chapter 03's
  problem, planted.

## Mixed precision: the resident state and one scalar that governs it

Training memory has two halves: activations (per-forward, freed) and the
*resident* state (persists all run). Mixed precision prices the resident
state:

| component | bytes/param |
|---|---|
| fp32 master weights | 4 |
| fp16 compute copy | 2 |
| fp16 gradients | 2 |
| Adam m, v (fp32) | 8 |
| **total** | **16 B/param = 8× the fp16 model** |

The master-weight pattern is forced by fp16's 10-bit mantissa: an update
lr·g ~ 2⁻¹⁷ against a weight of 1.0 rounds away to nothing — the fp16 copy
is *compute-only*, the fp32 master takes the updates: 4+2 = 6 B/param,
"2×-plus weight memory." 
Gradients are the other crisis: fp16's smallest normal is 6.1×10⁻⁵, real
gradients live near 10⁻⁸ — they *underflow to zero*. The **loss scaler**
multiplies the loss by a scale (default 2¹⁶ = 65,536) so gradients ride the
exponent range, and unscales before the optimizer. Its state is one scalar
plus a skip counter; its policy is multiply-2/divide-2: 2,000 clean steps →
scale ×2; any inf/nan → skip the step, scale ÷2, reset the counter — powers
of two make scaling an exact exponent shift, no mantissa rounding. The
overflow check is a reduction over *every gradient byte*, fused with the
all-reduce as one extra scalar per bucket in chapter 03's distributed
scaler.

**The grad-norm's double duty.** Clipping (max norm 1.0) needs ‖g‖ — the
same reduction pass computes norm and overflow check, and the norm is *one
number that must agree across ranks*. The five-line derivation is the
chapter's cleanest example of "the math and the communication must agree" —
g sharded, g = [g⁰; g¹; …; g^(N−1)] (concatenation; ZeRO's layout):

1. ‖g‖² = gᵀg (definition)
2. = Σᵢ gᵢ², split the index set by rank: = Σ_r Σ_{i∈shard_r} gᵢ²
3. inner sum = ‖g^r‖² (each rank's local squared norm)
4. ‖g‖ = √(Σ_r ‖g^r‖²)
5. communication: one all-reduce of the N scalars ‖g^r‖² — not of the
   gradients, not of the norms: **the fused reduction carries the squares**

Sum is associative; square-root is not — the √ happens once, *after* the
reduction. The order question: **clip-then-reduce is wrong, reduce-then-clip
is right.** Local-first clipping gives each shard norm 1, so the concatenated
result has norm √N, not 1 — each rank applied a λ computed from information
no rank has. Correct: reduce the squares → global norm → one global λ → each
rank scales its shard (scaling commutes with concatenation). Honesty clause:
the identity is exact for *sharded* gradients. With DDP's
*replicated-then-averaged* gradients, no rank can know ‖ḡ‖ from local norms
(Cauchy–Schwarz gives only a bound), so torch clips *after* the all-reduce —
every rank holds the identical average and computes the identical norm
redundantly, free. The fused-squares pattern belongs to the sharded world of
chapter 03, where it replaces an all-gather of the gradient with 8 bytes per
rank.

## The optimizer as resident occupant: 4× and the 8-bit escape

Adam carries two state tensors per parameter — m and v, the moment EMAs — so
plain fp32 training holds weights + grads + m + v = **4× model bytes**
(16 B/param); a 7B model carries ~112 GB of resident state before a single
activation — chapter 03's sharding imperative is this number, not fashion.
The 8-bit optimizers (bitsandbytes) quantize m and v to int8 with
**block-wise scales**: 2,048-element blocks share one fp32 scale (absmax/127)
— state 2 B/param, the 4× state saving, resident 16 → 10 B/param. The
honesty: the moments are *quantized* — it works because m and v are smooth
averages with benign block range; the risk is outlier blocks, which the
2,048 granularity contains. The **paged optimizer** goes one rung further:
state in CUDA unified memory, paging to CPU DRAM on capacity pressure — an
implicit, fault-driven ZeRO-Offload, whose *explicit* pyramid (fp16
params/grads in HBM, fp32 masters and m/v in DRAM, pinned PCIe traffic,
NVMe third tier) is chapter 03's.

## What insiders know

- **`no_grad` vs `inference_mode` are two tiers, not synonyms.** `no_grad`
  stops *recording* — the tape is unbuilt, tensors stay ordinary.
  `inference_mode` also skips the bookkeeping: no version-counter bumps, no
  view tracking, and tensors produced inside are *inference tensors* whose
  stricter aliasing rules error at runtime if they touch autograd later. The
  "freed vs unbuilt" distinction has the same shape: after `.backward()` a
  graph is *freed* (`retain_graph=True` keeps it); under `no_grad` it is
  *unbuilt*. Both leaves show `.grad_fn is None`; only one ever had one.
- **The version counter is autograd's validity invariant — module 19
  chapter 04's GC validity returning as a 32-bit int.** Every TensorImpl
  carries a version counter; in-place ops bump it; a saved-for-backward
  tensor has its version recorded, and backward compares. Mismatch →
  `RuntimeError: one of the variables needed for gradient computation has
  been modified by an inplace operation` (newer builds append an
  anomaly-detection hint): the saved bytes are still allocated — they are
  just no longer the bytes the tape's math depends on.
- **Forward mode exists and is niche.** Dual numbers propagate a perturbation
  *with* the primal: one pass = one JVP = one *column* of J (d(all
  outputs)/d(one input)); reverse mode gives one *row* (VJP).
  Forward-over-backward powers cheap per-sample gradients
  (`torch.autograd.forward_ad`); niche because training wants ∇ of one
  scalar — reverse mode's strength — and every op needs a JVP kernel nobody
  wrote.
- **The CUDA OOM message decodes into a budget.** Fields: total / free /
  allocated-by-PyTorch / *reserved but unallocated*. **30 GB reserved /
  20 GB allocated = 10 GB of pool slack — the fragmentation metric (33%)**:
  block-splitting churn (variable batch shapes) leaves free bytes that
  cannot satisfy one contiguous request. `torch.cuda.empty_cache()`
  folklore: it releases reserved-but-unallocated blocks to the driver —
  frees no live tensor, and costs the next step re-faulting allocations.
  `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` is the structural fix.
- **Determinism vs atomics.** Backward's scatter-adds (embedding gradients
  are `index_add`s) accumulate in GPU-atomics order — nondeterministic run to
  run, bitwise different grads. Full determinism needs
  `torch.use_deterministic_algorithms(True)` *and*
  `CUBLAS_WORKSPACE_CONFIG=:4096:8` (cuBLAS's own workspace atomics), and
  costs the 10-20% band — a budget line, not a checkbox.

## See it yourself

Everything runs on a laptop; numpy is the only dependency (PyTorch CPU
variants noted where they exist).

**Lab 1 — build the tape (~60 lines), train y = x².** A Wengert-list
autograd with topological sort, lazy `.grad`, `+=` accumulation — verified
output:

```python
# mini_tape.py -- the Wengert list in 50 lines.  Run: python3 mini_tape.py
import numpy as np

def _unb(g, shape):
    # undo broadcasting: sum g down to shape (add is never free)
    while g.ndim > len(shape): g = g.sum(axis=0)
    for i, s in enumerate(shape):
        if s == 1 and g.shape[i] != 1: g = g.sum(axis=i, keepdims=True)
    return g

class T:
    # one tape node: data + backward edges [(parent, vjp), ...]
    def __init__(self, data, parents=()):
        self.data = np.asarray(data, np.float64)
        self.grad = None                  # leaf buffer: None until first use
        self.parents = list(parents)      # the tape entries this node appends

    def __add__(self, o):
        o = o if isinstance(o, T) else T(o)
        return T(self.data + o.data,
                 [(self, lambda g: _unb(g, self.data.shape)),
                  (o,     lambda g: _unb(g, o.data.shape))])
    def __mul__(self, o):
        o = o if isinstance(o, T) else T(o)
        return T(self.data * o.data,
                 [(self, lambda g: _unb(g * o.data, self.data.shape)),
                  (o,     lambda g: _unb(g * self.data, o.data.shape))])
    def matmul(self, o):
        return T(self.data @ o.data, [(self, lambda g: g @ o.data.T),
                                      (o,     lambda g: self.data.T @ g)])
    def relu(self):
        return T(np.maximum(self.data, 0.0),
                 [(self, lambda g: g * (self.data > 0))])
    def sum(self):
        return T(self.data.sum(),
                 [(self, lambda g: g * np.ones_like(self.data))])

def backward(out):                        # the engine: dependency scheduler
    order, seen = [], set()
    def visit(t):                         # DFS postorder = topological order
        if id(t) in seen: return
        seen.add(id(t))
        for p, _ in t.parents: visit(p)
        order.append(t)
    visit(out)
    out.grad = np.ones_like(out.data)     # seed: dL/dL = 1
    for t in reversed(order):             # ready queue = reverse-topological
        if t.grad is None: continue       # off the path: no gradient flows
        for p, vjp in t.parents:
            g = vjp(t.grad)               # each vjp allocates a fresh grad
            p.grad = g if p.grad is None else p.grad + g  # += accumulation

# ---- train a 2-layer net on y = x^2 ----
rng = np.random.default_rng(0)
x = T(rng.uniform(-1, 1, (64, 1)))                 # data leaf: no parents
w1, b1, w2 = (T(rng.normal(0, 0.5, s)) for s in [(1, 64), (64,), (64, 1)])
params = [w1, b1, w2]
def model_out(x):
    return ((x.matmul(w1) + b1).relu()).matmul(w2) # 5 tape ops per forward

for step in range(500):
    pred = model_out(x)
    diff = pred + (x * x) * T(-1.0)               # pred - target, in our ops
    loss = (diff * diff).sum() * T(1.0 / 64)      # mean squared error
    for p in params: p.grad = None                # zero_grad(set_to_none=True)
    backward(loss)                                # reverse-topo sweep
    for p in params: p.data -= 0.1 * p.grad       # SGD step
    if step % 100 == 0:
        print(f"step {step:3d}  loss {loss.data:9.6f}")
print(f"final max |pred - x^2| = {np.abs(model_out(x).data - x.data**2).max():.6f}")
```

```console
$ python3 mini_tape.py
step   0  loss  2.364331
step 100  loss  0.000659
step 200  loss  0.000406
step 300  loss  0.000301
step 400  loss  0.000238
final max |pred - x^2| = 0.041189
```

The `parents` lists *are* the tape; the `reversed(order)` loop *is* the
ready queue; `p.grad = None` *is* `zero_grad(set_to_none=True)`; and nothing
is ever freed — PyTorch's `.backward()` frees the graph, this engine leaves
it to the garbage collector. And `_unb` answers what broadcasting costs:
an add becomes a reduction.

**Lab 2 — the arithmetic on paper.** Rebuild the 12-layer table (every row
is one multiplication), derive the √L schedule by minimizing (L/k)·b + k·a
(k* = √(L·b/a): 3.46 uniform, 0.56 transformer), and plot the Pareto for the
three frequencies above (31.9, 10.5, 6.2 GiB vs 1.00×, 1.33×, 1.33×): the √L
point is dominated — the plot says why Megatron picked k = 1.

**Lab 3 — measure a real forward.** Wrap the forward in `tracemalloc` at
2048×64 inputs (three 2048×2048 intermediates dominate):

```python
import tracemalloc
tracemalloc.start()
pred = ((x.matmul(w1) + b1).relu()).matmul(w2)
cur, peak = tracemalloc.get_traced_memory()
```

Measured on this chapter's machine: **peak 101.1 MiB against the formula's
104.0 MiB** — the ~3% gap is temp-buffers-vs-saved-tensors bookkeeping,
honestly reported. PyTorch variant: `tracemalloc` around a forward under
`torch.no_grad()` vs without it — the difference is the tape's saved-tensor
total; expect the same order of magnitude, not the same digits.

**Lab 4 — trigger the version-counter error on purpose.** (PyTorch 2.x;
wording varies slightly by version.)

```python
import torch
x = torch.randn(4, requires_grad=True)
y = x * 2                    # tape saves x at version 0
x.add_(1)                    # in-place: version 0 -> 1
y.sum().backward()           # backward compares: 1 != 0
```

The message does not say "freed" (that is the retain_graph error, a
different invariant). The fix is not `clone()`: reorder the mutation before
the saving op, or go out-of-place (`x = x + 1`). Then break the other
invariant — a second backward — and fix it with `retain_graph=True` at the
price of keeping the roots alive.

## How to use this knowledge

- **OOM triage becomes arithmetic.** Per-token cost (3.6 MiB fp32, ~1.8
  fp16, ~0.9 flash+fp16), the resident 16 B/param, and the
  reserved/allocated gap classify any "CUDA out of memory" into staircase,
  fragmentation, or state — each with a different lever: batch size,
  allocator conf, checkpointing, 8-bit/offload.
- **Capacity planning.** "Will a 7B model train on eight 80 GB cards" is
  four multiplications: resident 16 B/param + per-token × tokens × pipeline
  depth — before chapter 03's sharding.
- **Code review shapes.** In-place ops on tensors feeding recorded ops,
  accumulation loops without `zero_grad(set_to_none=True)`, second
  `.backward()`s without `retain_graph` — latent RuntimeErrors or silent
  memory.
- **Interview answers.** "Why does training need ~20× model size?" —
  activations (~20× the fp16 weights at batch 8×1024) *on top of* the 8×
  resident state, two different tables. "Why can't you clip locally?" — the
  √N error and the five-line derivation.

## Connections

- **Chapter 01 (same module)** — the tape's entries are (data_ptr, shape)
  references to ch01's tensor object; the allocator serving the backward
  storm is module 01 ch19's arena/block flavor in production.
- **Chapter 03 (same module)** — the wgrad AllReduce and 25 MB buckets; ZeRO
  sharding of the 16 B/param stack; the distributed loss scaler; the
  ZeRO-Offload pyramid the paged optimizer foreshadows.
- **Chapter 04 (same module)** — the K and V rows of the table, kept for
  reuse instead of freed at backward: the KV cache is this tape's most
  selective survivor.
- **Chapter 05 (same module)** — gradient accumulation's effective-batch
  arithmetic scaled to the fleet's tokens/day budget.
- **Module 19 chapter 04** — the GC-theory mirror: saved-for-backward = the
  roots of a liveness proof recorded forward; `.backward()` is the sweep;
  checkpointing re-derives liveness by recomputation instead of
  reachability — liveness by construction vs by search.
- **Module 01 chapter 23** — the grand table's orphan, repaired: add one
  more row — **PyTorch Tensor** | strided view | TensorImpl header
  (ptr+shape+strides+version, ~64-72 B) | refcount *plus tape liveness*,
  freed at backward by default | 4 B/float | dtype checks, no aliasing
  checks | caching-allocator blocks → CUDA pool | the tape as a second
  lifetime (this chapter). The tensor *is* the 17th language's object model:
  C's manual freedom, Python's refcounting, and a Lisp-style root set in one
  object — ML systems as the newest point in module 01's design space.

## Quiz

1. **Concept.** Derive the chapter's first law — "backward needs forward's
   inputs" — from the chain rule, then name the two op families in the
   per-op table that break it by saving *outputs*, and why their Jacobians
   make outputs the natural choice.
2. **Concept.** A leaf never used in a graph has `.grad is None`; a parameter
   after `zero_grad(set_to_none=True)` also has `.grad is None`; after a
   backward that produced zero gradient it has a 0-tensor. What diagnostic
   does each state enable, and why is in-place zeroing weaker?
3. **Predict the number.** A 24 GB card holds the chapter's reference model
   with 11.1 GiB of resident state. Using the 12-layer table (2.41 GiB/layer,
   3.07 GiB head), what is the largest micro-batch at S=1024 that fits — and
   why does the arithmetic's next candidate die in practice?
4. **Derive.** For a uniform 12-layer stack with b = a, derive k* and the
   peak; for the transformer with b/a = 1/38.5, derive k* and the peaks at
   k = 1 and k = √L. What does each result predict about Megatron's default?
5. **Derive.** Two ranks hold gradient shards with local norms 3 and 4.
   Compute the global norm, the correct clip scale for max_norm = 1.0, and
   the norm of the result if each rank clips its own shard to 1.0 *before*
   reducing. What must the fused reduction carry, and why must the square
   root happen exactly once?
6. **Diagnose.** A training loop raises
   `RuntimeError: one of the variables needed for gradient computation has
   been modified by an inplace operation` — trace it to the version counter,
   explain why `x = x + 1` is a fix and `x.add_(1)` *before* the saving op
   is also a fix, and why `torch.no_grad()` around the mutation is *not*.
7. **Diagnose.** A job dies with "Tried to allocate 2.20 GiB... 20 GiB
   allocated, 30 GiB reserved" on a 32 GB card. Compute the fragmentation
   metric, name the mechanism behind the 10 GiB of
   reserved-but-unallocated bytes, and state what `torch.cuda.empty_cache()`
   does and does not do.
8. **Cross-chapter.** In module 19 chapter 04's terms, what kind of roots are
   the saved-for-backward tensors, what is `.backward()`'s default
   graph-freeing, and what does activation checkpointing correspond to in
   the reachability-vs-reconstruction frame?

<details>
<summary>Answers</summary>

1. The chain rule composes Jacobians whose *entries are functions of the
   operands* — d(a·b)/da = b, dX = dY@Wᵀ — so matmul/mul entries pin the
   inputs. Elementwise unary Jacobians are functions of the *output*: relu's
   mask is the sign of its output, softmax's J = diag(p) − ppᵀ needs p — so
   relu and softmax save outputs. Dropout saves neither: its vjp needs the
   mask, a third thing entirely.
2. `None` on a fresh leaf: never used in a recorded op — catches wiring bugs.
   `None` after `set_to_none`: nothing accumulated, the optimizer skips it
   (frozen-param semantics). A 0-tensor: on the path, true gradient zero.
   In-place zeroing erases the distinction (an unused param shows a stale
   0), memsets every parameter every step, and holds the buffers.
3. Free ≈ 12.9 GiB. Batches 1 and 2 (4.0 and 8.0 GiB) fit; batch 3 = 12.0
   GiB fits *on paper* with 0.9 GiB margin, but the backward's transient
   dgrads (~0.4 GiB, absent from the activation table) plus allocator
   rounding and fragmentation eat it. Batch 2 is the reliable answer; the
   arithmetic names batch 3's executioner.
4. Uniform: k* = √(L·b/a) = √12 ≈ 3.46, peak = 2√L·a ≈ 6.9a (vs 12a) —
   O(√L × per-layer). Transformer: k* = √(12/38.5) ≈ 0.56 → clamp to 1:
   peak = L·b + a = 12 + 38.5 = 50.5 units vs 133 at k = √L. Uniform stacks
   want every-√L, transformers want every layer — Megatron's default; √L is
   optimal only while boundary cost ≈ layer cost.
5. Global norm = √(3² + 4²) = 5; λ = 5 (scale by 1/5). Local-first clipping
   leaves two unit-norm shards — the concatenated gradient has norm √2, not
   1: wrong by √N, each rank using a λ no rank was entitled to. The fused
   reduction carries the *squares* (9 + 16 = 25): addition is associative,
   the square root is not — take it once, after the all-reduce.
6. Backward compares the tensor's *current* version counter against the
   version recorded when the op saved it: the mul saved x at version 0,
   `add_` bumped it to 1, 1 ≠ 0 fires. `x = x + 1` is out-of-place — a new
   tensor, saved bytes untouched; the mutation *before* the saving op means
   the tape records the post-mutation version. `no_grad()` only stops
   *recording* — the save already happened when the mul ran.
7. Fragmentation = (30 − 20)/30 ≈ 33%. The 10 GiB is the allocator's
   reserved-but-unallocated pool: variable-shaped batches split big free
   blocks, leaving free bytes that cannot satisfy one contiguous 2.2 GiB
   request. `empty_cache()` releases those blocks to the driver; it frees
   *no live tensor* and costs the next step re-cudaMalloc-ing. Expandable
   segments treat the cause.
8. The saved tensors are the root set of a *pre-computed liveness proof* —
   liveness by construction, not reachability search. `.backward()`'s
   default freeing is the sweep: roots dropped, tensors unreachable
   (`retain_graph=True` keeps them for a second sweep — the tape-of-tape
   case). Checkpointing is *reconstruction*: the tensors die and liveness
   is re-derived by recomputation — the proof's storage traded for its
   re-execution.

</details>

