# Chapter 01 — The tensor: memory's industrial form

> **The 30-second version.** Module 01 built the array: pointer, length,
> element size. The tensor is that object grown an economy: a
> **(data_ptr, shape, dtype, strides)** four-tuple where *no element's
> address is stored — every address is computed* as
> `data_ptr + Σ index[i]·stride[i]`. That decision makes transposes, slices,
> and broadcasts free (metadata-only views over one refcounted buffer —
> module 01 ch14's PyObject pattern, verbatim), makes `flatten` of a
> transposed tensor secretly copy (stride algebra forbids the merge), and
> decides the NCHW-vs-NHWC war: a conv's inner channel loop strides `H×W` in
> NCHW — a 64 B line serves 4 B — while NHWC serves 16 channels per line:
> the 4-16× reuse factor, module 12 ch02's GPU coalescing on a different
> axis. The dtype spectrum is the memory lever; serialization
> (.pt vs safetensors) decides whether 70 GB of weights costs 10 s or 1 s
> to load. Every later chapter is choreography of these objects.

## Two objects and a buffer: the tensor's real shape

A 2×3 `float32` tensor is 6 × 4 = **24 bytes of payload**. If the tensor
were just that buffer, `t.transpose()` would need to permute bytes — and a
reshape-heavy 40-layer pipeline would spend its budget copying. The design
splits the object: a **header that describes**, a **buffer that holds**,
joined by a refcounted handle:

```
   python land                C++ land (libtorch)              the heap (malloc'd)
+------------------+    +--------------------------+    +-------------------------+
| t (THPVariable)  |--->| TensorImpl   ~64-72 B    |--->| StorageImpl  ~40-56 B   |
|  72 B (measured, |    |  shape    (2, 3)         |    |  refcount   8 B  <--- module 01
|  torch 2.14)     |    |  strides  (3, 1)         |    |                     ch14's
|  ob_refcnt  8 B  |    |  dtype    float32 (4 B)  |    |  data_ptr ----------+  PyObject
|  ob_type    8 B  |    |  storage handle ---------+    |  nbytes    24       |  pattern
|  Tensor ptr 8 B..|    |  offset   0              |    +---------------------|---+
+------------------+    +--------------------------+                          v
 t2 = t  -> same TensorImpl, refcount++ (shallow copy)   +-------------------------+
 t2.view(...) -> NEW TensorImpl, SAME StorageImpl        | 24 B: 0 1 2 3 4 5       |
                                                          +-------------------------+
```

The numbers, honestly labeled: the C++ `TensorImpl` — "the tensor" people
mean — runs **~56-72 B** of header (shape, strides, dtype, storage handle,
autograd hooks; PyTorch 2.x, 64-bit Linux); its Python wrapper measures 72 B
in `sys.getsizeof` here (torch 2.14.1+cpu, CPython 3.13); the `StorageImpl`
holding `{refcount, data_ptr, nbytes, allocator}` is ~40-56 B more. The lab
measures the whole stack behind one 0-dim tensor at **~447 B**.

The middle object is the load-bearing return: **StorageImpl is module 01
ch14's PyObject pattern, verbatim** — an 8-byte refcount, a typed payload, a
deleter at zero — except it refcounts *bytes*, not Python objects:

- **Shallow copy is free and aliasing is real.** `t2 = t` bumps a refcount.
  `v = t.view(...)` builds a *new* TensorImpl sharing the *same* StorageImpl;
  an in-place write through `v` is visible in `t` — one buffer, two
  descriptions. Not a bug vector: the mechanism later chapters exploit
  (autograd saves views of activations, ZeRO shares parameter storages).
- **The buffer dies exactly when the last view dies.** `del t` decrements;
  the allocator's `free` runs at zero.
- **Views are `(offset, strides)` over the one buffer.** The invariant of
  this chapter: every view — transpose, slice, broadcast, channels-last —
  is a data_ptr offset plus a stride tuple over existing storage. What
  cannot be so expressed must copy. The next section is the algebra.

The tiny end is where the structure becomes comedy. A 0-dim `float32`
scalar — 4 bytes of payload — measures **~447 B total on CPU**:
metadata:buffer at the tiny end is **>100:1**. On GPU it is worse by rule:
the caching allocator's minimum block is **512 B**, so a scalar's 4 B occupy
a 512-B block of HBM — the quoted "0-dim tensor still costs 512 bytes."
A `numel()==0` tensor is the degenerate case: `torch.empty(0).data_ptr()
== 0` (verified) — a null pointer that must never be dereferenced; zero-size
tensors work because loops over zero elements touch no bytes.
Batch-size-0 batches occur at every dataset tail; variable-batch padding
treats them as legal tensors.

## Views: offset + strides over one buffer

The address rule — element `(i, j, k)` lives at
`data_ptr + (i·s₀ + j·s₁ + k·s₂)·element_size` — is the entire API. A
contiguous 2×3×4 tensor has strides `(12, 4, 1)`: fastest axis 1, each outer
stride the product of inner sizes. Every standard op is stride arithmetic
on the same storage:

- `t.transpose(0,1)` — swap shape entries, swap stride entries:
  `(12,4,1) → (4,12,1)`. Zero bytes moved.
- `t[:, 1:, :]` — same strides, `storage_offset += 1·4`: a sub-tensor pins
  the *whole* block it points into.
- `t[:, :, ::2]` — strides `(12, 4, 2)`: stride is per-*element-step*;
  slicing a dim by k multiplies its stride by k.

`view` vs `reshape` vs `contiguous` fall out of one merge condition. To view
shape `A` as shape `B` without copying, adjacent dims may only be *merged*
(when `stride[i] == stride[i+1]·size[i+1]`) or *split* (the inverse) — pure
stride algebra, which is why the lab makes you predict `t.stride()` before
printing it. The canonical failure is the **flatten-of-transposed** case:

```python
import torch
t  = torch.arange(6, dtype=torch.float32).reshape(2, 3)  # strides (3, 1)
tt = t.t()                                               # shape (3,2), strides (1, 3)
tt.view(6)                       # RuntimeError: view size is not compatible
tt.flatten().untyped_storage().data_ptr() == t.untyped_storage().data_ptr()
# False — flatten silently COPIED: the merge needs stride[0] == stride[1]*size[1],
#         i.e. 1 == 3*2 — false, so row-major order is unreachable in place
```

`reshape` is the diplomatic wrapper: *view if the algebra permits, else
`contiguous()` then view* — `t.t().reshape(6)` returns a fresh buffer.
Derived rule: **reshape on a contiguous tensor is free; on permuted or
sliced strides it is a hidden copy** — and in a 40-layer forward, hidden
copies are the memory bill chapter 02 itemizes.

Broadcasting extends the algebra past the buffer's edge.
`t.unsqueeze(0).expand(B, ...)` puts stride **0** on the broadcast axis:
every logical index reads the *same physical cell*. Free and correct to
read — and **read-only by law**: writing means N logical positions racing
into one address, so in-place ops abort (`RuntimeError: unsupported
operation: more than one element of the written-to tensor refers to a
single memory location` — "...breaks the autograd story"). Plain
`e[0,0] = 5` succeeds silently, writing the one shared cell. matmul and
einsum consume stride-0 views natively — why broadcasting is free rather
than merely cheap.

## The layout war is cache physics: NCHW vs NHWC

A conv layer reads, per output position, the kernel window summed over
`(C, kh, kw)` — the channel axis is the inner sum. Put numbers on module 01
ch02's tower (L1 → L2 → DRAM) and ask which layout keeps the inner loop in
cache.

**NCHW** stores `(n, c, h, w)` C-outermost: address =
`((n·C + c)·H + h)·W + w`. Fix a spatial offset and walk the channel sum:
the step between channels is **H×W elements** — for 224×224, 50,176
elements = 200,704 B. Each channel's single `float32` (4 B) lands in its own
64 B line: **64 B fetched, 3-4 B used, 6% occupancy.** The C=64 channel loop
fetches 64 lines = 4,096 B to use 256 B — 16× waste — and each line's other
60 B belong to *another channel's row*, unneeded for another 200 KB.

**NHWC** stores `(n, h, w, c)` C-innermost: address =
`((n·H + h)·W + w)·C + c`. The channel step is **1 element**. The same C=64
loop reads 64 consecutive `float32` = 256 B = **4 lines**, 100% occupancy.
Byte-by-byte, reuse = `64 B line ÷ 4 B element = 16` when C ≥ 16; for tiny C
(RGB, C=3) only 12 of 64 B serve — a 4-5× factor, hence the honest range:
**NCHW buys 1 useful element per line, NHWC buys up to 16 — a 4-16×
inner-loop reuse factor**, matching the measured 4-16× conv speedups.

This is **module 12 chapter 02's coalescing story, same physics, different
axis**: there, a warp's 32 threads reading consecutive addresses coalesce
into 1-2 DRAM transactions while a strided warp issues 32; here, an
iteration reading consecutive addresses shares one cache line while a
strided loop burns one per element. Consecutive lanes want consecutive
addresses — the CPU's line and the GPU's 32-B sector are one rule at
different grain. The hardware's opinion settled it further: **Tensor Cores
want NHWC-shaped fragments** (8×8 tiles loaded as vectors), so cuDNN's fast
fp16/bf16 conv kernels are channels-last.

The surprise worth carrying: `channels_last` is a **metadata-only
relayout** — a stride decision, not a buffer decision:

```python
x = torch.arange(2*3*4*5, dtype=torch.float32).reshape(2,3,4,5)  # N,H,W,C contiguous
y = x.permute(0,3,1,2)      # NCHW-shaped view
print(y.stride())           # (60, 1, 20, 5)  -> C stride 1: channels-last!
y.is_contiguous(memory_format=torch.channels_last)   # True — zero bytes moved
z = torch.arange(2*3*4*5, dtype=torch.float32).reshape(2,5,3,4)  # NCHW contiguous
zc = z.to(memory_format=torch.channels_last)
print(zc.stride())          # (60, 1, 20, 5)  -- same tuple, but bytes were permuted
zc.untyped_storage().data_ptr() == z.untyped_storage().data_ptr()   # False: COPY
```
The same NCHW-shaped tensor can carry either stride tuple —
`is_contiguous(memory_format=...)` is a *predicate over strides*. `permute`
gives the channels-last view free when the bytes already sit in that order;
`.to(memory_format=...)` moves bytes only because your buffer was
materialized in the other order. "Memory format" never describes the buffer
— only the view.

## The allocator's arithmetic: 512-byte blocks and tail waste

The buffer goes to an allocator, whose rounding becomes your memory bill.
On the **GPU**, PyTorch's caching allocator
(module 01 ch19's design at CUDA scale) rounds every request to a multiple
of **512 B** — its minimum block. The arithmetic:

- 1,000-element `float32` = 4,000 B requested → **4,096 B allocated** (next
  512 multiple) — 2.4% internal fragmentation, invisible.
- A **0-dim tensor's 4 B** → 512 B block: the >100:1 ratio's GPU face, and
  why millions of scalars (per-step metrics) belong in Python floats until
  they must be tensors.
- **The tail-of-batch problem**: 10,017 rows in batches of 1,000 leaves a
  17-element last batch — 68 B → a 512-B block, **87% waste**, and a *size
  class nothing else shares*, so it fragments its own pool slot.

On the **CPU** the same story runs through glibc (module 01 ch05's chunk
arithmetic): 4,000 and 4,004 B land in the same 4,016-B chunk — the ladder
lab measures that. Two speed rules: buffers are **64-B aligned** so
vectorized loads (16-32 B) never straddle a line, and — the offset-storage
trick — **sub-tensors share blocks**: `big[1000:]` is an offset view, so the
4 MB it points into cannot be freed while the 4 KB view lives — one small
slice of a huge dataset tensor pins the whole thing forever.

## The dtype spectrum: the memory lever and the outlier channels

Strides decide *where* bytes are; dtype decides *how many* — the field's
biggest memory lever: a 70 B-parameter model is 140 GB in fp16, 70 GB in
int8, ~35 GB in 4-bit:

| dtype | bytes | layout (s/e/m) | range | precision | role |
|---|---|---|---|---|---|
| fp64 | 8 | 1/11/52 | ±1.8e308 | ~16 digits | science, double-checking |
| fp32 | 4 | 1/8/23 | ±3.4e38 | ~7 digits | the reference format |
| tf32 | 4 | 1/8/10 | ±3.4e38 | ~3 digits | A100's compat mode |
| bf16 | 2 | 1/8/7 | ±3.4e38 | ~2-3 digits | **training's winner** |
| fp16 | 2 | 1/5/10 | ±65504 | ~3 digits | inference, TC speed |
| int8 | 1 | — | -128..127 | exact | quantized inference |
| 4-bit | 0.5 | — | 16 levels | exact | edge/LLM serving |

**tf32** is the honest trick: same 4 B of storage, fewer meaningful bits —
19 bits total (sign, 8-bit exponent, 10-bit mantissa) with fp32's range. It
saves *no DRAM*; it saves multiply units (8× the fp32 rate on A100's tensor
cores). **bf16** is tf32's little sibling: fp32's 8-bit exponent with 7
mantissa bits — fp32's range at fp16-class precision. Why bf16 won training
is a dynamic-range argument runnable on CPU:

```python
torch.tensor(70000.0, dtype=torch.float16)   # tensor(inf)       — 65504 is the ceiling
torch.tensor(1e-8,    dtype=torch.float16)   # tensor(0.)        — subnormal floor is ~6e-8
torch.tensor(1e-8,    dtype=torch.bfloat16)  # tensor(1.0012e-08) — a NORMAL, all 7 bits
torch.tensor(70000.0, dtype=torch.bfloat16)  # tensor(70144.)    — mid-range, no drama
```

fp16 has 5 exponent bits: from its max (65504) down to typical late-training
gradients (~1e-8) is `log10(65504/1e-8) ≈ 12.8` decades; even the live
window a step needs — O(1) loss magnitudes down to 1e-8 gradients — is **~8
decades**, while fp16's normal span is barely 9 and its subnormal bottom
sheds mantissa bits. The workaround is the **loss-scale trick**: multiply
the loss by 2¹⁶ = 65,536 before backward (1e-8 gradients become 6.55e-4,
safely normal), divide it out of the update — and make the scale *dynamic*,
because the same 2¹⁶ that rescues 1e-8 sends any 10.0 intermediate to
6.5e5 > 65504 → inf; torch's `GradScaler` starts at 2¹⁶, halves on overflow
(skipping the step), doubles every 2,000 clean steps. bf16's 8-bit exponent
needs none of this — that, not speed, is why it took over training.

At the integer end, int8 works only if the scale fits — and real
transformers initially broke it. LLM.int8's finding (Dettmers et al., 2022):
in models ≥ ~6 B parameters, **~0.1% of hidden-dimension channels emerge as
outliers carrying up to ~100× the magnitude of the rest** — a 4,096-wide
layer has ~4 rogue channels that saturate any per-tensor scale, flattening
the other 99.9% into ~1-2 bits. The fix is **mixed-precision decomposition**:
outlier columns multiplied in fp16, the 99.9% remainder in int8 with
**per-channel scales** (weights likewise), partial products added back.
Per-channel scales work *because outlier channels are consistent across
layers, tokens, and runs* — the same dim indices light up everywhere, so a
scale set at load time stays valid for the model's life. That regularity —
plus the norm-vs-weight split (norm weights stay 16-bit; only the big linears
quantize) — is the insight that saved int8 inference. Chapter 04 spends it
at scale.

## Serialization: weights as page cache

The last boundary is the disk. `torch.save`'s `.pt` is a zip of pickled
Python objects plus raw storage blobs: loading means unzip, unpickle
(deserialization per tensor — and arbitrary code execution on untrusted
files), then **copy** blobs into fresh heap. `safetensors`
deletes every step: an 8-byte little-endian header length, a JSON map
`{"layer0.weight": {"dtype":"F32","shape":[3,4],"data_offsets":[16,64]}}`,
then one flat buffer. Loading is **mmap + parse-the-JSON**: tensors come back
as *views over the mapping* — verified in this chapter's lab: `load_file`'s
`data_ptr()` lands inside the file's own `/proc/self/maps` entry. No copy,
no deserialization, no code execution; overlapping views
are forbidden by the format.

The mmap trick means **weights are page-cache backed** — module 14 ch03
returning at full force: the OS page cache *is* the model's storage on a warm
box. First load streams pages from NVMe (~7 GB/s on gen4): 70 GB ÷ 7 GB/s =
**10 s of cold start**; later loads hit resident pages — page-table setup,
~1 s. That 10:1 is the cold-start budget of inference fleets, and why a
serving-region reboot is an incident: N models × 10 s of cold I/O per node,
all when health checks fire. Quantization compounds it — 70 GB in int8 is
35 GB is 5 s.

## What insiders know

- **`torch.compile`'s memory story is ~20-30% peak savings** on typical
  eager models (PyTorch's reported range, model-dependent) — honest
  mechanism: *fusion + reuse*. Fused ops never materialize intermediates;
  CUDA-graph pools replay one static allocation plan instead of per-op
  malloc/free churn — an allocator win dressed as a compiler win.
- **pin_memory is a DMA staging decision.** Pageable memory cannot be a
  DMA source, so H2D transfers bounce through pinned buffers anyway
  (~6 GB/s); `cudaHostAlloc`-pinned staging runs 12-25 GB/s on PCIe gen4 x16.
  `DataLoader(pin_memory=True)` + `.cuda(non_blocking=True)` is the prefetch
  double-buffering of module 13 ch01's rings, copy overlapped against
  compute. Pin too much and the OS starves — pinned pages cannot be swapped.
- **Stride-0 views are read-only by law.** `expand`/`broadcast_to` cost zero
  bytes because N logical cells alias one physical cell; in-place ops abort
  ("more than one element... refers to a single memory location"). Index
  assignment (`e[0,0] = 5`) bypasses the check and writes the shared cell.
- **`sys.getsizeof(t)` reports 72 B for any tensor** (torch 2.14, CPython
  3.13) — the Python wrapper only. A 70-GB model is, to `tracemalloc`, a
  72-B object; Python-level profilers are blind to libtorch's C++
  allocations. Measure `/proc/self/statm` instead.
- **`id(t.storage())` is a torch ≥ 2.x trap**: the `TypedStorage` wrapper is
  rebuilt per call, so two views of one buffer compare *unequal* by `id`.
  The sharing proof is `t1.untyped_storage().data_ptr() ==
  t2.untyped_storage().data_ptr()`, plus `t._base is parent`.
- **GradScaler defaults are interview-grade**: initial scale 2¹⁶, growth ×2
  every 2,000 overflow-free steps, backoff ×0.5 per overflow with the step
  *skipped*. Periodic loss flat spots at exactly the skip pattern =
  scaling-overflow signature.
- **The offset-storage trick leaks in production**: a slice view pins the
  entire backing buffer; `.narrow()` on a 4-GB mmap'd safetensors tensor
  keeps all 4 GB mapped. `.clone()` when a view must outlive a batch.

## See it yourself

All four labs run on a laptop CPU (torch 2.14, CPython 3.13; outputs are real).

**Lab 1 — the stride discovery set.** Predict each stride on paper, then
score yourself (1 point per stride, 1 per same-buffer call):

```python
import torch
t = torch.arange(24, dtype=torch.float32).reshape(2,3,4)
for name, x in [("t", t), ("t.transpose(0,1)", t.transpose(0,1)),
        ("t.transpose(1,2)", t.transpose(1,2)), ("t[:, 1:, :]", t[:, 1:, :]),
        ("t[:, :, ::2]", t[:, :, ::2]), ("t.flatten()", t.flatten()),
        ("t.reshape(4,6)", t.reshape(4,6)),
        ("t.transpose(0,1).flatten()", t.transpose(0,1).flatten()),
        ("t.permute(0,2,1)", t.permute(0,2,1)), ("t.contiguous()", t.contiguous())]:
    print(name, x.shape, x.stride(), x.storage_offset(),
          x.untyped_storage().data_ptr() == t.untyped_storage().data_ptr())
```

Answer key: `t (12,4,1)`; `transpose(0,1) (4,12,1)`;
`transpose(1,2) (12,1,4)`; slice `(12,4,1)` off 4; `::2 (12,4,2)`;
`flatten (1,)` same buffer; `reshape(4,6) (6,1)` same buffer; **
`transpose(0,1).flatten() (1,)` NEW buffer** — the surprise;
`permute(0,2,1) (12,1,4)`; `contiguous() (12,4,1)` same buffer.

**Lab 2 — the storage-sharing proof.** One buffer, many headers:

```python
a = torch.ones(1000); b = a.view(1000); c = a[10:]
a.untyped_storage().data_ptr() == b.untyped_storage().data_ptr()  # True
c.storage_offset()      # 10   (c.data_ptr() - a.data_ptr() == 40 bytes)
c._base is a            # True — the view chain is recorded
import sys; sys.getrefcount(a)   # 3: a, b's TensorImpl-share, the call
```

Delete `a` and `b`: the 4,000 B survive until `c` dies — module 01 ch14's
refcounting, applied to bytes.

**Lab 3 — the fp16 cliff, and why bf16 lives below it** (code as in the
dtype section; outputs: `inf`, `0.`, `1.0012e-08`, `70144.`). Then the
loss-scale arithmetic: `1e-8 * 65536` in fp16 → `6.55e-4` (rescued) vs
`10.0 * 65536` → `inf` — the scale's price, hence *dynamic* scaling.

**Lab 4 — the allocation-size ladder.** The README's tool is tracemalloc;
run it and meet the blind spot:

```python
import torch, tracemalloc
tracemalloc.start()
keep = [torch.empty(1000, dtype=torch.float32) for _ in range(100)]
snap = tracemalloc.take_snapshot()
print(sum(s.size for s in snap.statistics('lineno')))   # 8064 B — wrappers only!
```

100 tensors × 4,000 B = 400 KB of buffers; tracemalloc saw 8,064 B — the
72-B wrappers + list. Escalate to the virtual-size ladder
(`/proc/self/statm` column 1, pages × 4,096, fresh process per size, tensors
zeroed to touch pages), 20,000 tensors each:

| elements | buffer B | per-tensor VSZ | overhead |
|---|---|---|---|
| 0 | 0 | 437 B | 437 B (pure metadata) |
| 1 (0-dim) | 4 | 447 B | 443 B — **~112:1** |
| 10 | 40 | 490 B | 450 B |
| 100 | 400 | 824 B | 424 B |
| 1,000 | 4,000 | 4,458 B | 458 B |
| 1,001 | 4,004 | 4,455 B | 451 B — same glibc chunk as 1,000 |
| 4,096 | 16,384 | 16,786 B | 402 B |

Derived block table: the metadata floor is ~440-460 B/tensor on CPU
(wrapper + TensorImpl + StorageImpl + malloc headers); the GPU rule is
512-B minimum blocks, so the same ladder there reads 512, 512, 512 ...
4,096, 4,096, 16,384. The 1000-vs-1001 line is the rounding, observed.

## How to use this knowledge

- **OOM triage**: check stride-triggered hidden copies before blaming model
  size — `.reshape` after `.transpose` in a loop duplicates the activation;
  `is_contiguous()` is the one-line assertion that catches it. A 2×
  activation spike with "no code change" is usually an upstream layout
  change.
- **Layout decisions**: conv-heavy fp16/bf16 models → `channels_last`
  everywhere, converted once at input; the win is the 4-16× line occupancy
  and Tensor Cores refuse the slow layout anyway.
- **Serving cold-start budget**: 70 GB fp16 → 10 s cold, ~1 s warm; int8 →
  5 s / ~1 s. On a fleet reboot that ratio × model count is the user-visible
  outage; safetensors mmap keeps the warm path page-cache-cheap.
- **Precision incidents**: loss → inf at a fixed interval = GradScaler
  backoff; weights frozen while loss falls = gradient underflow (1e-8 class
  in fp16). "Switch to bf16" deletes the mechanism.

## Connections

- **Module 01 chapter 02** — the memory tower extended: the conv working
  set's L1/L2/DRAM placement is what NCHW-vs-NHWC argues about; line
  occupancy priced per byte.
- **Module 12 chapter 02** — the coalescing twin: warp lanes vs cache lines,
  same consecutive-address rule, the axis of the unit vector changed; NHWC
  satisfies both at once.
- **Module 01 chapter 14** — the refcounted object: StorageImpl is the
  PyObject pattern applied to bytes; the view lifetime chain is pure ch14
  refcounting with a deleter that frees bytes.
- **Module 13 chapter 01** — pinned ring buffers as DMA staging: pin_memory
  and prefetch overlap are those rings in DataLoader clothing.
- **Module 14 chapter 03** — the page cache returning: safetensors mmap makes
  weights page-cache-backed; warm/cold start is cache residency.
- **Module 22 chapter 02** — autograd's tape saves views of activations;
  the hidden copies itemized here become its accounting.
- **Module 22 chapters 03-04** — sharded parameters and KV caches are these
  storages at datacenter scale; quantization is the dtype lever plus the
  outlier-channel insight.
- **Module 22 chapter 05** — collectives move raw buffers across the fabric;
  layout decides coalescing there as it did here.

## Quiz

1. **Concept.** A 10⁶-element tensor is transposed in 0 ns and 0 bytes.
   From the (data_ptr, shape, dtype, strides) four-tuple and the two-object
   structure: which parts of a tensor does a view own, which does it share,
   and what single invariant must every view satisfy?
2. **Concept.** bf16 and fp16 are both 2 bytes. Derive, from the
   exponent/mantissa split, why bf16 won training while fp16 needs
   `GradScaler` machinery — name the magnitudes that break fp16 and what
   bf16's 8-bit exponent does with them.
3. **Predict.** `t = torch.arange(24).reshape(2,3,4)`. For each of
   `t.transpose(0,1)`, `t[:, :, ::2]`, `t.permute(0,2,1)`: the stride tuple,
   the storage offset, and whether `flatten()` on the result shares storage
   with `t`. Show the merge-condition check.
4. **Predict the number.** A 1×64×224×224 fp32 conv input, kernel reading all
   64 channels at one spatial offset: cache-line bytes fetched in NCHW vs
   NHWC, and the reuse factor. Then on CUDA: the allocator's block for the
   12,042,240-B request, and for a final batch of 17 elements.
5. **Predict the number.** A 0-dim fp32 CUDA scalar: payload, allocator
   block, metadata:buffer ratio counting the ~64-72 B TensorImpl and
   ~40-56 B StorageImpl. Why is `sys.getsizeof` the wrong tool, and what
   does it return?
6. **Diagnose.** fp16 training: the loss curve shows flat spots every ~2,000
   steps and an `inf` in the scale history; meanwhile late-epoch gradient
   norms measure ~1e-8 and the model stopped improving even while the loss
   looks healthy. Two distinct failure modes — identify both, with the
   arithmetic.
7. **Diagnose.** A pipeline does `x = batch[i]; x -= x.mean()` per row; the
   *second epoch* trains on different statistics than the first, no code
   change, no error. What happened, which lifetime rule caused it, and the
   one-line fix?
8. **Cross-chapter.** `a = torch.ones(1_000_000)` (4 MB), `b = a.view(-1)`,
   then `del a`. Tie the buffer's fate to module 01 chapter 14's PyObject
   refcount pattern — who holds the count, when does the deleter run, and
   which C++ object died at `del a`?

<details>
<summary>Answers</summary>

1. A view owns *new metadata only* — its own TensorImpl (shape, strides,
   offset) and Python wrapper — and shares the StorageImpl and buffer with
   the parent. Transpose swaps stride entries (`(12,4,1)→(4,12,1)`); every
   address is *computed* (`data_ptr + Σ iₖ·sₖ`), so nothing moves. Invariant:
   a view is `(offset, strides)` over the same storage — anything
   expressible that way is free; anything else must copy.
2. fp16: 5 exponent bits → max 65504, normal floor ~6.1e-5, subnormal floor
   ~6e-8. O(1) losses plus 1e-8 gradients span ~8 live decades inside the
   ~12.8-decade gap 65504→1e-8: overflow at the top (70000→inf),
   flush-to-zero at the bottom (1e-8→0.), forcing a dynamic 2¹⁶ loss scale.
   bf16: 8 exponent bits → ±3.4e38; 1e-8 is a *normal* (1.0012e-08, all 7
   mantissa bits) and 70,000 is mid-range — fp32's range at fp16-class
   precision, no scaler.
3. `transpose(0,1)`: stride `(4,12,1)`, offset 0 — flatten does **not**
   share: merge needs `stride[0] == stride[1]·size[1]` = 4 == 12·2, false.
   `t[:, :, ::2]`: stride `(12,4,2)`, offset 0 — 4 == 4·2 false. `permute(0,2,1)`:
   stride `(12,1,4)`, offset 0 — 12 == 1·3 false. (Contiguous `t`'s flatten
   shares: 12 == 4·3 true.)
4. NCHW: 64 channels × 4 B, each on its own line (channel step = H·W =
   50,176 elements) → 64 lines × 64 B = **4,096 B fetched** for 256 B used.
   NHWC: 64 consecutive fp32 = 256 B = **4 lines** → **16× reuse factor**.
   CUDA: 12,042,240 B is already a 512 multiple (512 × 23,520) → allocated
   as-is. The 17-element tail: 68 B → **512-B block, 87% waste**.
5. Payload 4 B; minimum block 512 B → 128:1 from the block alone; counting
   the ~64-72 B TensorImpl + ~40-56 B StorageImpl, the ratio tops 140:1
   (CPU-measured all-in: ~447 B ≈ 112:1). `sys.getsizeof` returns 72 — the
   THPVariable wrapper only; it and `tracemalloc` are blind to C++
   allocations.
6. Overflow: activations × the 2¹⁶ scale exceed 65504 → `inf`, GradScaler
   halves and *skips the step* → the periodic flat spots (each ~2,000-step
   climb ends in the next overflow). Underflow: at the reduced scale,
   1e-8-class gradients fall below the ~6e-8 subnormal floor and flush to
   zero → zero gradients, frozen weights, healthy-looking loss. Fix: bf16,
   or monitor the scale.
7. `batch[i]` returns a *view* (offset + strides into batch's storage);
   `x -= x.mean()` is in-place on shared bytes, so epoch 1 mutated the
   dataset and epoch 2 reads the leftovers — one StorageImpl, many
   TensorImpls, writes visible through all. Fix: `x = batch[i].clone()` (or
   out-of-place `x = x - x.mean()`).
8. `del a` dropped one reference on the TensorImpl/StorageImpl pair; `b`'s
   view holds the same StorageImpl handle, so the count stays ≥ 1 and the
   4 MB live — CPython's `ob_refcnt` pattern (ch14) with a C++ deleter
   instead of `__del__`: the allocator's `free` runs when the *last* view
   dies, not when any name does. `b` has its own TensorImpl; what must
   outlive `b` is the StorageImpl and the bytes.

</details>
