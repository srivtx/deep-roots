# Chapter 04 — Inference serving: the KV-cache machine

> **The 30-second version.** Training's law was "fit the states on N
> GPUs"; serving's law is "store every token's attention state and never
> free it early." Each token needs its K and V in every layer, so a
> Llama-7B-class model costs **512 KiB of KV per token**: one 2048-token
> sequence is 1 GB, 32 concurrent sequences are 32 GB — the cache dwarfs
> the 14 GB of weights it serves. Serving is therefore a memory manager
> wearing a model: **PagedAttention** re-invents the page table (16-token
> blocks, COW prefixes, refcounts, preempt-and-swap — module 01 ch03,
> verbatim), **continuous batching** makes the GPU an iteration-level
> allocator, and decode's arithmetic intensity is **below 1** — batching
> and quantization are ways to move along that roofline.

## The KV cache: attention's memory bill, derived

Attention at position *t* needs every position ≤ *t*'s key and value —
in **every layer**, each layer keeping its own projections. Recomputing
them per step re-runs a prefill pass per token; storing them trades
quadratic compute for linear memory. One formula prices the trade:

```text
per-token KV bytes = 2 × n_layers × n_kv_heads × head_dim × dtype_bytes
                     ^K and V        ^heads that keep a KV
```

For the Llama-7B class (32 layers, MHA, 32 heads × 128 = 4096 dim,
fp16): 2 × 32 × 4096 × 2 B = **512 KiB per token**. The ladder: a
2048-token context is **1 GB per sequence**; 32 concurrent 2048-context
sequences hold **32 GB of KV** — 2.3× the **14 GB** of fp16 weights
served. Weights are paid once per GPU; KV is paid per concurrent
sequence-token — the real serving floor:

| model (fp16 KV) | per token | 2k ctx | 8k ctx | 32k ctx | 128k ctx | weights |
|---|---|---|---|---|---|---|
| 7B MHA (llama-2-7B: 32L, 32 kvh) | 512 KiB | 1.0 GB | 4 GB | 16 GB | 64 GB | 14 GB |
| 7B GQA (llama-3-8B: 32L, 8 kvh) | 128 KiB | 0.25 GB | 1 GB | 4 GB | 16 GB | 16 GB |
| 70B GQA (llama-2-70B: 80L, 8 kvh) | 320 KiB | 0.6 GB | 2.5 GB | 10 GB | 40 GB | 140 GB |
| 400B-class MoE GQA (60L, 8 kvh) | 240 KiB | 0.5 GB | 1.9 GB | 7.5 GB | 30 GB | 800 GB |

**MHA → MQA → GQA** is the architecture answering the bill: nothing
forces a KV head per *query* head. Share each KV head across 4 query
heads (GQA) and n_kv_heads drops 32 → 8: Llama-3-8B pays 2 × 32 × 8 ×
128 × 2 B = **128 KiB/token — a 4× cut** (8k context: 4 GB → 1 GB). One
shared head (MQA) is the full **32×**: 16 KiB/token, 128 MiB per 8k
sequence. Redo the 70B arithmetic: 80 layers × 8 kv heads = 320
KiB/token, where MHA (80L × 64 heads) would be 2.5 MiB — an **8× cut**
that llama-2-70B and nearly every 2024+ model took. The quality cliff:
GQA-8 measures within noise of MHA, retrofitted MQA costs a visible
perplexity slice, and the frontier's next step (DeepSeek's MLA)
compresses KV into a shared latent, ~70 KiB/token — compression is
chosen at training time; serving inherits it as a constant.

## Paged KV: virtual memory for tokens

The naïve layout gives each sequence one contiguous KV buffer sized for
its *maximum* context: a 200-token generation under a 2048 cap wastes
90% of the reservation, and a 24 GiB pool admits pool / 1.5 GiB ≈ 16-32
sequences whatever the real lengths — the reservation-vs-fragmentation
trade of every allocator chapter, now with gigabyte objects.

vLLM's **PagedAttention** (Kwon et al., SOSP 2023) answers with the
answer this program has drawn four times: **indirection**. KV lives in
fixed **16-token blocks** (the 7B MHA block is 16 × 512 KiB = 8 MiB);
each sequence owns a **block table** mapping logical positions to
physical blocks:

```text
 sequence s (logical positions)      block table            physical KV pool
+----+----+----+----+----+---+      +------+-----+------+   +------------------+
| 0..15 | 16..31 | 32..47 | ... --> | blk7 | blk3| blk9 |-->| block 3: 16 tok  |
+----+----+----+----+----+---+      | ref 2 | ref 1| ref 1|  | block 7: 16 tok  |
  token 41 -> slot 41 = table[2],      (m01 ch03's page table,     | block 9: ...    |
  one block per 16 positions            verbatim: logical          +------------------+
                                          position -> physical
                                          block = VPN -> PFN)
```

Same problem — a shared, growing, fragmented heap of live objects that
cannot be compacted — so the same mechanism. The payoffs, exactly as in
module 01 ch03:

- **External fragmentation dies.** Waste drops from ~90% reservation
  slack to ≤ 15 tail tokens per sequence (≤ 2% in the lab below); the
  paper measures 2-4× throughput over contiguous-reservation servers.
- **Copy-on-write prefixes.** 100 requests flooding in behind the same
  2 KiB system prompt (≈ 512 tokens) would duplicate 100 × 512 × 512 KiB
  = **25 GiB** of identical KV. Paged, they map the same 32 shared
  blocks and the pool pays **256 MiB once** — 24.75 GiB saved, 99% —
  with prefix blocks **refcounted exactly like module 01 ch14's
  PyObject**: each finisher decrements, the block frees at zero. Beam
  search and branchy sampling fork the way `fork()` does — child tables
  alias the parent's, the first divergent token allocates a private
  block (COW).
- **State becomes movable.** A contiguous buffer cannot be partially
  evicted; a block table can — swap out the private blocks, reload
  later *anywhere*.

The thesis, one final time: **paged KV = virtual memory for tokens.**
The page table's job was decoupling logical contiguity from physical
placement so a shared heap could grow, share, and move; attention's KV
is that heap, and the serving stack re-derived demand paging from
first principles within three years of taking LLM serving seriously.

## Continuous batching: the server as an allocator

The static batch has a tail problem that is pure arithmetic. Admit 32
sequences; 31 finish at ~50 tokens, one generates 2000. The batch runs
to the straggler: 2000 iterations × 32 slots = **64,000 slot-steps paid
for 3,550 useful tokens — 5.5% utilization**. Finished slots idle
because the batch is the unit of scheduling.

**Iteration-level (continuous) batching** demotes the unit to the step:
finished sequences release slot *and blocks* the step they finish,
waiting requests refill instantly. The scheduler becomes an allocator:
the running set is the heap, tokens are the allocation stream, the
free-block watermark is admission control.

The wrinkle: requests have two phases with opposite shapes. **Prefill**
ingests the whole prompt in one parallel bulk pass — compute-bound
(2 × params × prompt FLOPs), happiest as one big matmul. **Decode**
emits one token per sequence per step — memory-bound, happiest spread
wide. Batched naively, one request's 32k-token prefill stalls
everyone's decode — so **chunked prefill** slices prompts into
~2k-token chunks interleaved one-per-iteration with decode, trading
slightly longer total prefill for bounded per-step jitter (a flag in
vLLM/SGLang).

## The decode step on the roofline — and Little's law

Three paragraphs of queueing theory, because the pool is a queueing
system.

First: **L = λW**. If requests arrive at rate λ and each stays W seconds
(prefill + generation), then L = λW sequences are resident *right now*,
each holding context × kv-per-token bytes. The KV pool is a Little's-law
buffer: its size buys L, and L is the batch size B — nothing else about
the server matters as much, because—

Second: **the decode step is a memory stream, not a computation.** One
step for B sequences reads the weights once (14 GB) plus every resident
sequence's whole KV (B × 1 GB at 2k context) to emit B tokens: FLOPs ≈
2 × 7e9 × B against ≥ 15e9 bytes — arithmetic intensity at B=1 ≈ **0.93
FLOP/byte, under 1**, versus the A100 ridge 312 TFLOPS / 2 TB/s = 156.
Deep on the memory-bound slope. Batching is the cure **because weights
amortize across the batch and KV never does** — the 14 GB are read once
for all B sequences (cost-per-token = HBM-read / B for weights); each
sequence's KV is private:

| B (7B, 2k ctx) | weights/token | KV/token | total | tok/s aggregate | per-stream |
|---|---|---|---|---|---|
| 1 | 14 GB | 1 GB | 15 GB | 133 | 133 |
| 8 | 1.75 GB | 1 GB | 2.75 GB | 727 | 91 |
| 32 | 0.44 GB | 1 GB | 1.44 GB | 1,391 | 43 |
| 64 | 0.22 GB | 1 GB | 1.22 GB | 1,641 | 26 |

That is the whole "why batching works" derivation: the B=64 row pays
**64× less weight traffic per token**, and the asymptote is the **KV
floor** — cost per token approaches the KV read alone (1 GB → ~2,048
tok/s at 2 TB/s), which no batch amortizes. Keeping the GPU
"memory-bound-full" means keeping L — hence λ, hence the pool — in the
table's lower rows.

Third: **the queue and the two clocks.** Response time = queue wait + W,
and W degrades as L grows (per-stream tok/s falls with B): bigger batch,
cheaper tokens, longer queue — so the metric is **goodput**, requests
per second *meeting the SLO*, not raw throughput. And Little's law
appears once more, in the form TCP lives by: BDP = bandwidth × RTT is
L = λW with bytes — module 13 ch03's buffer autotuning grows the socket
buffer toward it precisely the way the pool's watermark grows L toward
"weights amortized, HBM saturated."

## Goodput, deadlines, and the reclaimer reborn

The SLO has two clocks — TTFT (prompt to first token) and TPOT
(per-token time while streaming) — and an SLO-aware scheduler holds a
**deadline** per request, admits only requests whose predicted W fits,
and demotes or rejects the rest rather than poison the batch. That
admission control is the only thing standing between you and the pool's
failure mode: **exhaustion deadlock**. Admit until free blocks are gone;
every resident sequence still needs one more block to finish; nobody
finishes, so no block frees; the GPU serves nothing (a lab tweak
reproduces it). Two mechanisms break it, both lifted from the OS: the
**watermark** (admit only if free blocks cover prompt plus slack —
conservative, and the source of under-utilization) and **preemption**:
evict a victim — newest, or SLO-doomed — by swapping its private KV to
CPU DRAM and re-queueing it, or dropping state and recomputing from the
prompt on resume (vLLM has both modes; swap costs a ~64 GB/s PCIe round
trip, recompute costs a prefill — the choice is swappiness).

That is module 07 ch04's page daemon, structure for structure: low/high
watermarks, background eviction of cold state to a slower tier (DRAM
plays disk; 2 TB/s HBM plays RAM), recompute-as-refault, admission as
overcommit policy. The paged-attention allocator **is** the OS's memory
manager — the memory manager has become the scheduler's core.

## Quantization: the only lever that touches every byte

Batching amortizes weight reads; **quantization shrinks the reads
themselves** — the only lever that helps the B=1 request, the B=256
node, and the KV pool at once. **W8A8** (per-channel scales, int8 GEMM ≈
2× fp16 tensor-core throughput, half the weight bytes) trips on
activation outliers: beyond ~2.7B params, ~0.1% of hidden dimensions run
~100× typical magnitude, and no per-tensor scale holds both ranges.
**LLM.int8()** splits the matmul — outlier columns in fp16, the rest
int8 — near-lossless (ΔPPL ≈ 0.01-0.05) at the cost of two GEMMs.
**4-bit weight-only** (GPTQ's second-order layer-wise solve; AWQ's
activation-aware salient-channel protection) uses group-128 scales at
**~0.5 B/param** — the moment: a 70B model drops to **35 GB and fits on
one 80 GB A100** where fp16 needed two GPUs. Honest cost: perplexity
deltas of **0.01-0.1** on Llama-family evals (method-dependent; 7B
suffers before 70B). Activations stay 8/16-bit — they hold the outliers
— hence W4A16, not W4A4.

Why this is *the* lever: the decode step's roofline position was AI < 1
because the denominator was bytes. Shrink weights 14 GB → 3.5 GB and
B=1 moves 0.93 → 3.11 FLOP/B, B=32 from 9.7 → 12.6 — the same
computation slides right along the memory-bound slope. Quantization is
"move down the precision axis to move along the roofline," the closing
figure:

```text
 achievable TFLOPS
 312 |----------------\______________   ridge = 312 TFLOPS / 2 TB/s = 156 FLOP/B
     |                 \      compute-bound plateau
     |                  \
     |                   \        (w4, B=32)  12.6 -> 1803 tok/s
     |                    \   *  (fp16, B=32) 9.7 -> 1391 tok/s
     |                     \ *    (w4, B=1)    3.1 -> 444 tok/s
     |                     *      (fp16, B=1)  0.9 -> 133 tok/s
     +-----+-----+-----+---+------+-----> arithmetic intensity (FLOP/B)
     0.9   3.1         9.7  12.6   156      slope = 2 TB/s (memory-bound)
```

The node budget closes the accounting for a 70B A100 node: 35 GB 4-bit
weights + 40 GB KV pool + 5 GB runtime (activations, CUDA-graph arena,
cuDNN workspaces) = **80 GB = one A100, full**. That pool buys 64
concurrent 2k-context sequences (0.62 GB each) or 16 at 8k — the pool,
not the FLOPs, sizes the product.

**Speculative decoding** spends pool on speed, in five lines: (1) a
0.5-1B draft (~0.5-1 GB resident) autoregressively proposes k=4 tokens;
(2) the target verifies all four in *one* prefill-shaped parallel pass
(tree attention shares the verification); (3) the accepted prefix is
emitted, the first mismatch discards the rest — output is the target's
distribution, no drift; (4) at chat accept rates (~70-80%) that is ~2.5
tokens per target step: **2-3× decode throughput**; (5) the bill is the
draft's residence plus wasted draft compute on rejected tails.

## What insiders know

- **fp8 KV is the live frontier.** `--kv-cache-dtype fp8` doubles the
  pool for free bytes; the hard part is scale discipline — per-head
  (even per-channel) scale factors, as DeepSeek-V2/V3 train in and
  TensorRT-LLM/SGLang serve — because outlier KV magnitudes sit in
  specific heads. As of 2024-2025 builds: near-lossless to ~8k,
  long-context quality still being chased. GQA shrank the *shape*; fp8
  shrinks the *dtype*.
- **Why vLLM "warms up":** the decode step is captured as a CUDA graph —
  one replay replaces hundreds of kernel launches (~5-10 µs each, real
  money in a 20 ms step). Graphs demand static shapes, so the server
  pads to a lattice of captured batch sizes and *reserves the arena the
  capture allocated* — which is why `# GPU blocks` drops after startup,
  and why `enforce_eager=True` (graphs off) reports more blocks but
  serves ~35% less.
- **TTFT vs TPOT are different products.** TTFT ≈ tokenize (1-5 ms) +
  queue (0-100 ms) + prefill (10-500 ms); under ~200 ms the first token
  feels instant. TPOT: 5 tok/s ≈ human reading speed (~225 wpm) — fine
  prose, painful code; 50 tok/s outruns reading and feels like
  autocomplete. The perception cliff sits between; goodput budgets both
  clocks.
- **Long context is a compute product, not a memory product.** Prefill
  costs 2 × params × context: 2 × 7e9 × 131072 ≈ 1.8e15 FLOPs ≈ **6 s
  at 300 effective TFLOPS** for one 128k request on a 7B. Hence prefix
  caching: chat traffic behind a fixed system prompt skips ~90% of
  prefill FLOPs at realistic hit rates — the hit rate *is* the
  economics.
- **The MoE all-experts-resident law.** A 400B-class MoE activates
  ~30-40B params per token (top-2-of-128 routing) but you buy HBM for
  **all 400B** (~800 GB fp16): params you don't use still cost DRAM.
  Hot-expert skew wrecks naive expert parallelism; offloading experts
  to CPU DRAM is a PCIe gamble (~64 GB/s vs 2 TB/s) that pays only with
  big batches and accurate prefetch — the training-time
  load-balancing loss echoes as serving-side skew.

## See it yourself

**Lab A — the KV arithmetic on paper, then in python.** Derive 512
KiB/token by hand, then check and extend to the GQA and MoE rows:

```python
# kv_calc.py — per-token KV = 2 * layers * kv_heads * head_dim * dtype
MODELS = {"7B MHA": (32, 32, 128, 2), "7B GQA": (32, 8, 128, 2),
          "70B GQA": (80, 8, 128, 2), "400B MoE": (60, 8, 128, 2)}
for c in (2048, 8192, 32768, 131072):
    row = [f"{2*L*H*D*dt*c/2**30:5.1f}GB" for (L, H, D, dt) in MODELS.values()]
    print(f"{c//1024:>4}k ctx: " + "  ".join(row))
```

```console
$ python3 kv_calc.py
   2k ctx:   1.0GB    0.2GB    0.6GB    0.5GB
   8k ctx:   4.0GB    1.0GB    2.5GB    1.9GB
  32k ctx:  16.0GB    4.0GB   10.0GB    7.5GB
 128k ctx:  64.0GB   16.0GB   40.0GB   30.0GB
```

Compare with vLLM's startup line `# GPU blocks: N` (block = 16 tokens):
the naive formula says (0.9 × 40 GB − 14 GB weights) / 8 MiB ≈ 2,816
blocks for llama-2-7B fp16 on an A100-40GB; vLLM prints ~5-10% fewer —
the gap is activation buffers plus the CUDA-graph capture arena, and it
*grows* with graphs on (version-dependent; expect drift).

**Lab B — the PagedAttention simulator.** Block tables, COW prefix
sharing, preemption-and-swap, utilization metrics, and the contiguous
comparison — pure python, must run, must show the capacity gain:

```python
#!/usr/bin/env python3
"""PagedAttention simulator: contiguous reservation vs paged blocks + COW."""
import math, random
random.seed(7)

BLOCK, SYS_PROMPT = 16, 512            # tokens/block; shared system prompt
MAX_NEW = 1024                         # typical conservative client setting
POOL, NREQ, MAX_SEQS = 3072, 400, 256   # 24 GiB pool; vLLM max-num-seqs
TOK_BYTES, GB = 512 * 1024, 2**30      # per-token KV, 7B MHA

def gen_lens(n):                       # chat mix: Q&A, tasks, stragglers
    return [random.randint(32, 200)  if (r := random.random()) < 0.60 else
            random.randint(200, 700) if r < 0.90 else
            random.randint(700, 2000) for _ in range(n)]

def nblocks(t): return math.ceil(t / BLOCK)
lens = gen_lens(NREQ)

# ---- policy 1: contiguous reservation ----
res = nblocks(SYS_PROMPT + MAX_NEW)        # 96 blocks = 1.5 GiB/seq
cap_c = POOL // res                       # resident capacity
batches = math.ceil(NREQ / cap_c)
res_GiB = batches * cap_c * res * BLOCK * TOK_BYTES / GB
real_GiB = sum(SYS_PROMPT + l for l in lens) * TOK_BYTES / GB
paid   = sum(cap_c * (max(l for l in lens[i:i+cap_c]) + 1)
             for i in range(0, NREQ, cap_c))  # runs to its straggler
useful = sum(l + 1 for l in lens)
print(f"[contiguous] {res} blocks/seq reserved -> capacity {cap_c} concurrent, "
      f"pool util {100*real_GiB/res_GiB:.0f}% ({real_GiB:.0f}/{res_GiB:.0f} GiB), "
      f"slot-steps {100*useful/paid:.1f}% (static-batch tail)")

# ---- policy 2: paged + COW + preempt-and-swap ----
class BlockPool:                    # refcounted blocks (m01 ch14)
    def __init__(s, n): s.refcnt, s.free = [0] * n, n
    def alloc(s):
        pid = s.refcnt.index(0); s.refcnt[pid] = 1; s.free -= 1; return pid
    def incref(s, pid): s.refcnt[pid] += 1
    def put(s, pid):
        s.refcnt[pid] -= 1
        if s.refcnt[pid] == 0: s.free += 1     # dies at refcount zero

pool = BlockPool(POOL)
sys_tab = [pool.alloc() for _ in range(nblocks(SYS_PROMPT))]  # ONE copy

class Seq:                          # the block table: logical->physical
    def __init__(s, rid, gen):
        s.id, s.gen, s.tok = rid, gen, SYS_PROMPT
        s.tab = sys_tab[:]          # COW: prefix shared, not copied
        for pid in s.tab: pool.incref(pid)

seqs, swapped, waiting = [], [], list(range(NREQ))
swaps = steps = peak = peakb = 0

while waiting or seqs or swapped:
    while waiting and pool.free >= 1 and len(seqs) < MAX_SEQS:  # admit
        r = waiting.pop(0); seqs.append(Seq(r, lens[r]))
    while swapped and pool.free >= nblocks(swapped[0].tok) - nblocks(SYS_PROMPT):
        v = swapped.pop(0)          # swap-in: back from CPU DRAM
        for _ in range(nblocks(v.tok) - nblocks(SYS_PROMPT)):
            v.tab.append(pool.alloc())  # to NEW physical blocks
        seqs.append(v)
    for s in list(seqs):            # one decode iteration
        if s.tok % BLOCK == 0:        # needs its next block now
            if pool.free == 0:         # exhausted: preempt
                v = max(seqs, key=lambda x: x.tok)  # most progress
                for pid in v.tab[len(sys_tab):]: pool.put(pid)
                v.tab = v.tab[:len(sys_tab)]
                seqs.remove(v); swapped.append(v); swaps += 1
                continue
            s.tab.append(pool.alloc())
        s.tok += 1
        if s.tok >= SYS_PROMPT + s.gen and s in seqs:  # finished
            for pid in s.tab: pool.put(pid)
            seqs.remove(s)
    steps += 1
    peak, peakb = max(peak, len(seqs)), max(peakb, POOL - pool.free)

mean_gen = sum(lens) / NREQ
cap_p  = (POOL - nblocks(SYS_PROMPT)) // nblocks(mean_gen)  # COW: prompt free
saved  = (NREQ - 1) * nblocks(SYS_PROMPT)
print(f"[paged] capacity ~{cap_p} (mean gen {mean_gen:.0f} tok) -> "
      f"{cap_p/cap_c:.1f}x; peak {peak} resident, {peakb}/{POOL} blocks, "
      f"{steps} iters, {swaps} swaps")
print(f"[cow] 1 x {nblocks(SYS_PROMPT)}-block prefix, {NREQ} requests "
      f"(ref {pool.refcnt[sys_tab[0]]}): {saved} blocks = "
      f"{saved*BLOCK*TOK_BYTES/GB:.0f} GiB saved, frag <= "
      f"{(BLOCK-1)/2/mean_gen*100:.1f}%")
```

```console
$ python3 paged_sim.py
[contiguous] 96 blocks/seq reserved -> capacity 32 concurrent, pool util 55% (171/312 GiB), slot-steps 19.8% (static-batch tail)
[paged] capacity ~132 (mean gen 364 tok) -> 4.1x; peak 256 resident, 3063/3072 blocks, 2149 iters, 2 swaps
[cow] 1 x 32-block prefix, 400 requests (ref 1): 12768 blocks = 100 GiB saved, frag <= 2.1%
```

Read the three results: **4.1× capacity** at realistic mixed lengths
(inside the vLLM paper's 2-4×), ~55% of contiguous reservation bought
by nothing, ~20% slot utilization for the static-batch tail. Tweaks:
raise `MAX_NEW` (contiguous capacity collapses; paged does not move);
set all lens to 2000 (paged converges to contiguous — fragmentation
relief needs length *variance*); delete the preempt branch and watch
the pool exhaust at step ~128 with every resident stalled at a block
boundary — the deadlock; add a 4-beam fork aliasing one parent's `tab`
to watch refcount 5 → 1.

**Lab C — the real thing, on CPU.** Watch the law in RSS (0.5B-class
fallback if a 7B doesn't fit your RAM — same law, ~15× smaller
constants; documented):

```console
$ pip install transformers accelerate
$ python3 - <<'EOF'
import resource
from transformers import AutoModelForCausalLM, AutoTokenizer
tok = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")
for dt in ("float32", "bfloat16"):          # the dtype lever, on CPU
    m = AutoModelForCausalLM.from_pretrained(
        "Qwen/Qwen2.5-0.5B-Instruct", torch_dtype=dt).eval()
    print(dt, "RSS GiB:",
          resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20)
    m.generate(**tok("number theory is", return_tensors="pt"),
               max_new_tokens=256, do_sample=False)
    print("  after 256 tokens:",
          resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20)
    del m
EOF
```

What to notice: the fp32→bf16 RSS delta is the bytes-per-param law in
your own process (bitsandbytes' int8 load needs CUDA; on CPU, dtype
halving is the same 2× lever in miniature), and the post-generate RSS
step is the KV cache charging layers×2×ctx×dim×dtype bytes — rerun with
`max_new_tokens=2048` and watch it scale linearly: the 512 KiB/token
law at 1/17th scale.

**Lab D — place decode on the roofline.** Compute a decode step's
arithmetic intensity, plot B=1 vs B=32, then the 4-bit shift:

```python
# roofline.py -- decode steps on the A100 roofline (7B model, 2k ctx)
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
PARAMS, KV = 7e9, 1.0e9                       # weights; KV per seq @2k
BW, PEAK = 2.0e12, 312e12                     # HBM B/s, fp16 FLOP/s
def step(B, w):
    rd = w + B * KV                           # bytes read in one step
    return 2 * PARAMS * B / rd, BW * B / rd   # AI, aggregate tok/s
for B in (1, 32):
    for w in (14e9, 3.5e9):                   # fp16 vs 4-bit weights
        ai, t = step(B, w); print(f"B={B} w={w/1e9:.1f}GB: AI {ai:.2f}, {t:.0f} tok/s")
ai = [i/100 for i in range(1, 2000)]
plt.loglog(ai, [min(PEAK, a*BW)/1e12 for a in ai], "k-")   # the roofline
for B, w, c in ((1,14e9,"r"), (32,14e9,"b"), (1,3.5e9,"orange"), (32,3.5e9,"g")):
    a, _ = step(B, w); plt.loglog([a],[min(PEAK, a*BW)/1e12], "o", color=c)
plt.xlabel("arithmetic intensity (FLOP/byte)"); plt.ylabel("TFLOPS")
plt.savefig("decode_roofline.png", dpi=120)
```

Read the figure: all four points sit on the memory-bound slope, far
left of the 156 FLOP/B ridge; B=32 sits ~10× right of B=1 (batching),
the 4-bit points ~1.3× right of fp16 (quantization) — two mechanisms,
one direction. The printed tok/s match the chapter's table; the plot is
the closing diagram, produced and read.

## How to use this knowledge

- **Capacity planning**: concurrency = (HBM − weights − runtime) /
  (ctx × kv_per_token) — compute it before quoting a token rate, then
  check the regime: KV-floor-bound (long contexts, small model; fix
  with GQA/fp8-KV) or weight-stream-bound (short contexts, big model;
  fix with 4-bit/batching).
- **The "we need 2× the GPUs" request** is usually a KV-cache story in
  disguise: run the 512 KiB/token ladder on real context and
  concurrency before buying FLOPs — the answer is often paged
  allocation, prefix caching, or fp8 KV, i.e. free capacity.
- **Incident triage**: throughput collapse with rising queue depth →
  pool exhaustion and preemption thrash (swap counts, watermark hits);
  p99 TTFT regression after a long-context client → chunked prefill;
  int4 slower than expected → compute-bound prefill, not decode.
- **Interviews and paper-reading**: derive per-token KV bytes, the B=64
  amortization, and the KV floor on a whiteboard — and check whether a
  serving paper's throughput claim sits at a batch size where the KV
  floor already ate the win.

## Connections

- **Chapter 01** — KV blocks are tensors with a layout: 16 × heads ×
  head_dim tiling is ch01's coalescing story applied to attention's
  reads.
- **Chapter 02** — autograd freed intermediates; inference is the
  mirror: the one activation you must *never* free early.
- **Chapter 03** — ZeRO's COW sharded states meet their serving twin:
  shared-prefix blocks are the same refcounted storage.
- **Chapter 05** — the 80 GB node budget becomes the fabric's problem:
  TP/EP communication and the datacenter as one computer.
- **Module 01 ch03** — *the thesis identification*: paged KV = virtual
  memory for tokens; page table → block table, page → 16-token block,
  COW fork → beam fork, swap → preempt-to-DRAM. Same problem (a shared
  growing heap), same solution (indirection).
- **Module 07 ch04** — the reclaimer's kinship: watermarks, background
  eviction to a slower tier, recompute-as-refault — the page daemon's
  playbook is the KV scheduler's preemption policy.
- **Module 13 ch03** — *repairs the orphan*: TCP buffer autotuning grows
  rcvbuf toward BDP = rate × delay, which is Little's law L = λW in
  bytes; the KV pool's watermark grows resident sequences toward the
  same product, and the advertised window's credit = the block pool's
  free-block accounting.
- **Module 11 ch04** — swap-out writes live state to a slower tier in
  bounded increments and reloads on resume: the WAL/checkpoint shape
  (persist what must survive, trade write cost against stall) minus the
  crash-ordering requirement — here latency is the only durability.

## Quiz

1. **Concept.** Why must the KV cache exist — what does storing K and V
   per layer buy, and what would the alternative cost per decode step?
2. **Concept.** In a decode step, why do weight reads amortize across
   the batch while KV reads never do, and what asymptote does that set
   for aggregate tokens/s as B → ∞?
3. **Derive (capacity).** A 70B-class model: 80 layers, 8 KV heads
   (GQA), head_dim 128, fp16. (i) Per-token KV bytes. (ii) KV for one
   4096-token sequence. (iii) Concurrent 4096-context sequences in a
   40 GiB pool. (iv) Same pool with fp8 KV.
4. **Derive (COW).** 100 concurrent requests share a 1024-token system
   prompt on a 7B MHA model (512 KiB/token). Compute the KV bytes
   duplicated without prefix sharing, with it, and the savings.
5. **Derive (utilization).** A static batch of 32 sequences: 31 finish
   at 50 tokens, one generates 2000. Compute slot-steps paid, useful
   slot-steps, and utilization — and what continuous batching changes.
6. **Diagnose.** A server admits requests whenever ≥ 1 free KV block
   exists and never preempts. Under a flood of long-generation
   requests, throughput falls to zero, GPU idle, queue full. Name the
   mechanism and the two OS-borrowed fixes.
7. **Diagnose.** After `enforce_eager=True` (CUDA graphs off), the
   server reports ~10% *more* KV blocks but ~35% lower decode
   throughput. Explain both numbers.
8. **Cross-chapter.** Map paged KV onto module 01 ch03's virtual memory
   term by term (six pairs), and name the one mechanism both worlds
   needed for the same reason.

<details>
<summary>Answers</summary>

1. Attention at each new token needs every past token's K and V in
   every layer. Storing them makes each decode step linear in context
   *bytes*; recomputing re-runs the prompt's forward pass (quadratic
   FLOPs, a full prefill) for every emitted token. A time–space trade:
   2 × layers × kv-heads × head_dim × dtype bytes per token, held until
   the sequence ends.
2. The weights are read **once per step for the whole batch** — the
   same 14 GB serves B sequences, so weight cost per token is 14 GB / B
   (64× less at B=64). Each sequence's KV is **private**: B sequences
   read B × ctx × kv-per-token bytes every step, growing *with* B. As
   B → ∞ the weight term vanishes and cost per token approaches the KV
   read alone — the KV floor (1 GB/token here, ~2,048 tok/s at 2 TB/s).
3. (i) 2 × 80 × 8 × 128 × 2 B = 327,680 B = **320 KiB**. (ii) 4096 ×
   320 KiB = **1.25 GiB**. (iii) 40 GiB / 1.25 GiB = **32** sequences
   (floor; block slack makes ~31-32). (iv) fp8 halves per-token bytes →
   640 MiB/sequence → **64** sequences — the fp8-KV pool-doubling
   trick as policy.
4. Duplicated: 100 × 1024 × 512 KiB = **50 GiB** of identical KV.
   Shared: one copy, 512 MiB, refcounted until the last request
   finishes. Savings: **49.5 GiB, 99%** — concurrency is now sized by
   *unique* tokens, not total tokens.
5. Paid: the batch runs to the straggler — 2000 iterations × 32 slots
   = **64,000 slot-steps**. Useful: 31 × 50 + 2000 = **3,550**.
   Utilization **5.5%**; 60,450 slot-steps are tail waste. Continuous
   batching reclaims each finished slot at the finishing iteration, so
   paid slot-steps track useful tokens — the straggler costs only its
   own slot.
6. **KV-block pool exhaustion deadlock**: every resident sequence
   holds blocks and needs one more to finish; nobody finishes, no block
   frees, admission has already filled the pool. Fixes: (a) an
   **admission watermark** — admit only if free blocks cover the prompt
   plus slack; (b) **preemption** — evict a victim (newest or
   SLO-doomed) by swapping KV to CPU DRAM or recomputing later. The OS
   page daemon's playbook (module 07 ch04) applied to tokens.
7. Graphs capture the decode step as one replayable launch but demand
   **static shapes and a reserved arena**, allocated from the same HBM
   as the pool — disabling graphs returns those gigabytes (+10%
   blocks). But each uncaptured step pays hundreds of kernel launches
   at ~5-10 µs each, launch tax a ~10-20 ms step cannot absorb at
   moderate batch — ~35% fewer tokens/s. This is "why vLLM warms up."
8. Logical token position ↔ virtual address; block table ↔ page table;
   16-token KV block ↔ 4 KiB page; COW shared prefix (system prompt,
   beam forks) ↔ `fork()`'s copy-on-write; preempt-and-swap to CPU DRAM
   ↔ page eviction to swap; refcounted shared blocks ↔ module 01
   ch14's refcounted objects at block granularity. Both needed
   **indirection**: decoupling logical contiguity from physical
   placement so a shared growing heap can share, fork, and move.

</details>
