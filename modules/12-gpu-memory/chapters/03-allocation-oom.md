# Chapter 03 — Allocation & the real OOM

> **The 30-second version.** The GPU has an MMU — per-process virtual address
> spaces, page tables, one process-agnostic driver owning the physical HBM —
> module 01 ch03's machine with three deltas: 2 MiB pages, one owner, peer
> mappings. `cudaMalloc` is slow and page-granular, so PyTorch layers a caching
> allocator on top — module 01 ch19's segregated-fit, replayed. Hence the real
> OOM: "out of memory" almost never means out of bytes; it means out of
> *contiguous, policy-satisfiable* memory — free bytes fenced into holes by
> live tensors. Fix ladder: steady tensor shapes, `expandable_segments`, and
> never-as-a-defragmenter `empty_cache()`.

## The GPU has an MMU: ch03's machine, three deltas

Module 01 ch03 built the machine — per-process address spaces, page tables,
TLB, demand paging. The GPU runs it unchanged except three deltas
(Ampere/Hopper, CUDA 12.x):

1. **Pages are 64 KiB and 2 MiB** (CPU: 4 KiB) — same reason as huge pages: one
   TLB entry covers 2 MiB, not 512 4 KiB pages. A 64-byte tensor never buys a
   64-byte mapping; the smallest currency unit is a page.
2. **One owner.** Physical HBM belongs to the kernel driver; every process's
   page tables point into one process-agnostic pool. Per-process accounting is
   the driver's bookkeeping (what `nvidia-smi` prints), not yours to compute.
3. **Peer mappings.** A page-table entry can point off-device: with NVLink,
   GPU 0 maps GPU 1's HBM (`cudaDeviceEnablePeerAccess`) — the walk crosses
   the wire, a preview of ch04.

```text
 process A VA (49-bit, Ampere/Hopper)      GPU page tables (walked by the GMMU)
 [0x7f0a00000000, +16 MiB) ---->  PTE -> 2 MiB page ----> physical HBM page 0x41
                                  PTE -> 2 MiB page ----> physical HBM page 0x7c
                                  PTE -> 2 MiB page ----> PEER: GPU 1 HBM (NVLink)
 process B VA (49-bit)   ...its own tables; same physical pool, one driver owner
```

The driving asymmetry: the virtual side is 48-bit-plus (hundreds of TiB); the
physical side is a 40–80 GiB island. VA is not scarce. Physical HBM is.

## cudaMalloc: one expensive, page-granular call

`cudaMalloc(n)` returns a VA range backed by physical pages at 2 MiB granularity
(Ampere/Hopper, CUDA 12.x). Two taxes: 3 MiB + 1 B of request becomes 4 MiB of
mappings (sub-page requests pay worst), and each call costs tens of µs to ms
and can synchronize the device (order-of-magnitude, CUDA 12.x). A training step
allocates thousands of tensors — nobody calls the driver per tensor, exactly as
malloc shields the kernel (module 01 ch05).

First insider correction, derived: the driver can almost always find the
*bytes* — physical pages may scatter, since each 2 MiB page maps independently.
**"Out of memory" usually means out of contiguous, policy-satisfiable memory**:
contiguous in one segment, right pool, right size class, splittable under the
split rule. Those adjectives are the difference between a 3 a.m. crash and a
boring run.

## The caching allocator: ch19 replayed at device scale

PyTorch's CUDACachingAllocator (`c10/cuda/CUDACachingAllocator.cpp`, PyTorch
2.5) is ch19's segregated-fit, recognized, not reinvented:

- **The rounding ladder.** Every request rounds to a 512 B multiple
  (`kMinBlockSize`). Requests ≤ 1 MiB go to the **small pool**, served from
  **2 MiB segments**; requests > 1 MiB round to a **2 MiB multiple** and go to
  the **large pool**, served from **20 MiB segments** (`kLargeBuffer`). 512 B
  steps to 1 MiB, then 2 MiB steps forever: 1.5 MiB of data costs a 2 MiB
  block; 2.4 MiB costs 4 MiB.
- **Segregated free lists per size class**, per pool: allocation is an
  exact-size hit or a best-fit-by-size search — no global walk. Ch19's whole
  point.
- **Split and remainder.** A free block splits *only if the remainder is itself
  legal* (≥ 512 B small pool, ≥ 2 MiB large pool); a freed 5 MiB block serving
  a 4 MiB request is handed over whole — 1 MiB internal fragmentation, by
  policy.
- **Pools never mix.** A 900 KiB request will not carve a large-pool free
  block; GiB of large-pool free space cannot save a small-pool allocation.

The critical vocabulary is three numbers and their guaranteed disagreement:

| number | meaning | who reports it |
|---|---|---|
| **requested** | sum of `numel() * element_size` | your mental math |
| **allocated** | sum of *rounded* block sizes | `memory_summary` |
| **reserved** | sum of *segment* sizes held via cudaMalloc | `nvidia-smi` (+ context) |

`reserved ≥ allocated ≥ requested`, always. `nvidia-smi` shows reserved plus a
CUDA context (hundreds of MiB, CUDA 12.x) — why it disagrees with your tensor
sum, and why 30 GiB of tensors OOMs a 40 GiB card. `empty_cache()` releases
segments that are **entirely free** to the driver; it moves, merges, and
defragments nothing inside partially-live segments — those are pinned by their
live blocks. The lab shows it releasing 0 MiB at the moment of failure: the
classic misuse.

The modern fix is **`expandable_segments`**
(`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`, PyTorch 2.1+, opt-in as of
2.5): one large VA arena, with 2 MiB pages mapped/unmapped on demand (VMM APIs
`cuMemCreate`/`cuMemMap`/`cuMemUnmap`, CUDA 10.2+). A freed block's pages
*unmap* — physical memory returns to the driver immediately — and a later large
request maps fresh pages into a fresh contiguous VA run. Contiguity becomes a
choice made in VA space, not an accident of physical placement; the rounding
tax remains.

## Fragmentation arithmetic: free ≥ request, and it still fails

The chapter in one picture — the 40 MiB device the lab builds. Fill it with
alternating tensors, four of 3 MiB + 1 B (4 MiB blocks) and four of 5 MiB + 1 B
(6 MiB blocks), then free the 4 MiB side:

```text
 "CUDA out of memory" anatomy (40 MiB toy device)
 device physical 40 MiB:  nvidia-smi says 40 MiB used = RESERVED, not allocated
+---------------------------------------------------------------+----
| SEGMENT 0 = one cudaMalloc VA reservation, 20 MiB              | ...
| +--------+----------+--------+----------+--------+----------+ |
| | A 4MiB | B 6MiB   | A 4MiB | B 6MiB   | A 4MiB | B 6MiB   | |
| |  FREE  |   LIVE   |  FREE  |   LIVE   |  FREE  |   LIVE   | |
| +--------+----------+--------+----------+--------+----------+ |
|   hole     tensor     hole     tensor     hole     tensor     |
+---------------------------------------------------------------+----
 request: 8 MiB -> rounded 8 MiB (large pool). Needs ONE contiguous span
 inside ONE segment:
        [  4F  ][  6L  ][  4F  ][  6L  ]     6L = live, fences the holes
               ^^^^^^^^^^^^^^^^^^  the span would have to cross a live tensor
 totals: free = 16 MiB  >=  request = 8 MiB  ->  OOM anyway
```

Rule by rule: **(1)** exact size class — no 8 MiB free block exists, the lists
hold 4 MiB blocks only; **(2)** best fit ≥ 8 MiB — largest hole is 4 MiB, fail;
**(3)** coalesce — every hole's neighbors are live 6 MiB blocks, and adjacent
*free* blocks would merge but no two free blocks are adjacent: module 01 ch05's
lifetime interleaving, the no-move promise fencing the holes; **(4)** cross
segments — never, they are separate VA reservations; **(5)** new segment — a
fresh 20 MiB cudaMalloc against a device already 40/40 reserved: physical
exhaustion, at the driver boundary. Free: 16 MiB. Request: 8 MiB. Failure — no
byte was used up by the failing request; the bytes were *stranded* by geometry
and policy. Scale it up (24 GiB card, 20–200 MiB holes, a 500 MiB request,
thousands of segments) and you have the production OOM: "3.1 GiB is reserved by
PyTorch but unallocated."

## The real OOM: six causes

Fragmentation is cause #1 of six; the flowchart in *How to use this knowledge*
walks the elimination:

1. **Fragmentation** — reserved-but-unallocatable, as derived above; diagnosed
   by the "reserved but unallocated" line and a high reserved/allocated ratio.
2. **Other processes** — the driver's pool is shared: `nvidia-smi
   --query-compute-apps=pid,process_name,used_memory` lists every co-tenant; a
   stale notebook or 3 a.m. cron job holds GiB. With MPS (multi-process
   service) clients share one server context — cheaper than a context each,
   but memory is attributed to the server process.
3. **NCCL communication buffers** — every communicator in distributed training
   holds allreduce scratch: send and receive buffers per channel (2 × per
   channel), tens to hundreds of MiB (NCCL 2.x, scales with channels and GPU
   count), until process exit. Un-destroyed communicators are a slow leak with
   a networking-shaped cause.
4. **CUDA graph private pools** — capture records the *addresses* of graph
   intermediates, so the allocator pins them in a pool owned by the graph; it
   stays reserved while the graph object lives, even if the tensors were
   "deleted."
5. **cuDNN workspaces** — conv algorithm choice carries workspace memory: with
   `torch.backends.cudnn.benchmark=True` the fastest algorithm may want hundreds
   of MiB (FFT/Winograd variants, PyTorch 2.x) — transient, but it spikes
   reserved exactly when you were near the edge.
6. **Driver/kernel reserve** — ECC, display, and driver structures eat memory
   before your first tensor: 0.5–1 GiB at boot on a 24 GiB GeForce card, a few
   percent more with ECC on datacenter cards (card- and CUDA-version
   dependent). Your real budget is total minus this reserve.

## What insiders know

- **nvidia-smi's per-process number is reserved + context, not your tensors.**
  The chain requested < allocated < reserved explains every "numbers disagree"
  mystery: first gap = the rounding tax summed, second = segment slack, then
  the context (hundreds of MiB, CUDA 12.x).
- **The size-class ladder, exactly** (PyTorch 2.5): 512 B multiples to 1 MiB →
  small pool, 2 MiB segments; above 1 MiB, 2 MiB multiples → large pool, 20 MiB
  segments; large-pool split only when the remainder ≥ 2 MiB; pools never mix —
  a 900 KiB request starves next to a GiB of large-pool holes.
- **`empty_cache()` returns fully-free segments to the driver; it does not
  defragment and cannot help the OOM you are staring at** — every segment
  holds a live block, which is what made it an OOM. Its real job: handing
  memory to co-tenants, at the cost of a device sync.
- **The OOM message is the diagnosis** (PyTorch 2.x): "N GiB is reserved by
  PyTorch but unallocated" = fragmentation (cause 1); "Process P has X GiB
  memory in use" = co-tenants (cause 2); neither line and near-zero free = card
  genuinely full — causes 3–6 by elimination.
- **`expandable_segments:True`** is the highest-leverage flag for fragmentation
  OOMs (PyTorch 2.1+): map/unmap 2 MiB pages in one VA arena instead of
  accumulating fixed cudaMalloc segments. Verify CUDA-graph-capture and NCCL
  interop notes for your exact release.
- **The six-cause triage order** — fragmentation → other processes → NCCL →
  graph pools → cuDNN workspaces → driver reserve. Insiders spend the first
  two minutes on the error message and the process list, not on reinstalling.

## See it yourself

All you need is Python (CPython 3.11 here; no GPU) — the allocator's policy,
faithfully compressed, so you can *watch* the failure and its arithmetic:

```python
# toy_alloc.py -- PyTorch's CUDACachingAllocator replayed on CPU (policy from
# c10/cuda/CUDACachingAllocator.cpp, PyTorch 2.5): small pool <= 1 MiB, 512 B
# rounding, 2 MiB segments; large pool, 2 MiB rounding, 20 MiB segments;
# size-class free lists; split only if the remainder is legal; adjacent frees
# coalesce; empty_cache() drops fully-free segments.
MB = 1 << 20

class OOM(Exception): pass

class Block:
    def __init__(s, seg, off, size):
        s.seg, s.off, s.size, s.free, s.req = seg, off, size, True, 0
    def __str__(s):
        return (f"seg{s.seg}[{s.off//MB}..{(s.off+s.size)//MB})M "
                f"{s.size//MB}M {'FREE' if s.free else 'LIVE'}")

def round_size(n):
    if n <= MB:  return (n + 511) // 512 * 512       # 512 B ladder
    return (n + 2*MB - 1) // (2*MB) * (2*MB)         # 2 MiB ladder

class Sim:
    def __init__(s, device):
        s.device, s.reserved = device, 0
        s.requested = s.allocated = 0                # the disagreeing sums
        s.segments, s.free_lists = [], {}            # 1 segment = 1 cudaMalloc

    def _cudaMalloc(s, size):                        # the driver boundary
        if s.reserved + size > s.device:
            free = sum(b.size for l in s.free_lists.values() for b in l)
            big = max((b.size for l in s.free_lists.values() for b in l), default=0)
            raise OOM(f"CUDA out of memory. Tried to allocate {size//MB} MiB "
                      f"(device {s.device//MB} MiB; {s.reserved//MB} MiB reserved; "
                      f"{free//MB} MiB is reserved but unallocated; "
                      f"largest free block {big//MB} MiB)")
        s.reserved += size
        s.segments.append([Block(len(s.segments), 0, size)])
        s._add(s.segments[-1][0])

    def _add(s, b):  s.free_lists.setdefault(b.size, []).append(b)
    def _rem(s, b):
        s.free_lists[b.size].remove(b)
        if not s.free_lists[b.size]: del s.free_lists[b.size]

    def _find(s, want):
        small = want <= MB                           # pools never mix
        if want in s.free_lists:
            for b in s.free_lists[want]:
                if (b.size <= MB) == small: return b
        c = [b for l in s.free_lists.values() for b in l
             if b.size >= want and (b.size <= MB) == small]
        return min(c, key=lambda b: b.size) if c else None

    def _split(s, b, want):
        rem = b.size - want
        if rem >= (512 if want <= MB else 2*MB):     # remainder must be legal
            tail = Block(b.seg, b.off + want, rem)
            s.segments[b.seg].append(tail); s._add(tail)
            b.size = want                            # split; remainder recycled
        # else: whole block handed over -> internal fragmentation, no split

    def alloc(s, n):
        want = round_size(n)
        b = s._find(want)
        if b is None:
            s._cudaMalloc(2*MB if want <= MB else max(20*MB, want))  # OOM born here
            b = s._find(want)
        s._rem(b); s._split(b, want); b.free = False; b.req = n
        s.requested += n; s.allocated += b.size
        return b

    def free(s, b):
        b.free = True
        s.allocated -= b.size; s.requested -= b.req
        seg = s.segments[b.seg]
        while True:                                  # coalesce adjacent frees
            nbr = next((o for o in seg if o is not b and o.free and
                        (o.off + o.size == b.off or b.off + b.size == o.off)), None)
            if nbr is None: break
            s._rem(nbr)
            lo, hi = (nbr, b) if nbr.off < b.off else (b, nbr)
            lo.size += hi.size; seg.remove(hi); b = lo
        s._add(b)

    def empty_cache(s):                              # fully-free segments only
        rel = 0
        for seg, blocks in enumerate(s.segments):
            if blocks and all(x.free for x in blocks):
                rel += blocks[0].size
                for b in blocks: s._rem(b)
                s.segments[seg] = []
        s.reserved -= rel
        return rel
```

Run the fragmentation scenario:

```python
sim = Sim(device=40*MB)                        # a 40 MiB toy GPU
A, B = [], []
for _ in range(4):                             # alternating sizes: the churn
    A.append(sim.alloc(3*MB + 1))              # 3 MiB + 1 B  -> 4 MiB block
    B.append(sim.alloc(5*MB + 1))              # 5 MiB + 1 B  -> 6 MiB block
print(f"fill:  reserved={sim.reserved//MB}M allocated={sim.allocated//MB}M "
      f"requested={sim.requested//MB}M")
for a in A: sim.free(a)                        # free the 4 MiB side only
free = sum(b.size for l in sim.free_lists.values() for b in l)
big = max(b.size for l in sim.free_lists.values() for b in l)
print(f"freed: reserved={sim.reserved//MB}M allocated={sim.allocated//MB}M "
      f"requested={sim.requested//MB}M free={free//MB}M largest={big//MB}M")
print("segment 0:", " ".join(str(b) for b in sim.segments[0]))
try:
    sim.alloc(8*MB)                            # free 16 MiB >= 8 MiB asked...
except OOM as e:
    print("OOM:", e)
x = sim.alloc(3*MB + 1)                        # 4 MiB again: exact fit
print("4 MiB alloc from a hole: OK ->", x)
print("empty_cache() released:", sim.empty_cache() // MB, "MiB")
```

Actual output (CPython 3.11, line-wrapped only at the OOM message):

```text
fill:  reserved=40M allocated=40M requested=32M
freed: reserved=40M allocated=24M requested=20M free=16M largest=4M
segment 0: seg0[0..4)M 4M FREE seg0[4..10)M 6M LIVE seg0[10..14)M 4M FREE seg0[14..20)M 6M LIVE
OOM: CUDA out of memory. Tried to allocate 20 MiB (device 40 MiB; 40 MiB reserved;
     16 MiB is reserved but unallocated; largest free block 4 MiB)
4 MiB alloc from a hole: OK -> seg0[0..4)M 4M LIVE
empty_cache() released: 0 MiB
```

What to notice: (1) the rounding tax — 32 MiB requested became 40 MiB allocated;
(2) the OOM is raised at the *driver boundary* for a fresh 20 MiB segment while
the message honestly reports 16 MiB reserved-but-unallocated, largest hole 4 MiB
— free ≥ request, fail anyway; (3) a 4 MiB request *succeeds* (exact size
class, hole reuse); (4) `empty_cache()` releases **0 MiB** — both segments hold
live 6 MiB blocks, so the "defragment with empty_cache" folklore dies right
here. (Free everything and call it again: 40 MiB returns, and an 8 MiB
allocation succeeds.) Now the same workload under the essence of
`expandable_segments` — pages mapped/unmapped in one VA arena, physical returned
at free time:

```python
class SimExp:                                  # expandable_segments:True
    def __init__(s, device): s.device, s.mapped, s.va = device, 0, 0
    def alloc(s, n):
        want = round_size(n)                   # 2 MiB granularity tax remains
        if s.mapped + want > s.device: raise OOM("physical exhausted")
        s.mapped += want                       # driver maps fresh 2 MiB pages
        s.va += want                           # VA bump: endless, contiguous
        return s.va - want
    def free(s, n): s.mapped -= round_size(n)  # pages unmapped -> driver NOW

exp, As, Bs = SimExp(device=40*MB), [], []     # same workload
for _ in range(4):
    As.append(exp.alloc(3*MB + 1)); Bs.append(exp.alloc(5*MB + 1))
print("expandable fill:            mapped=%dM" % (exp.mapped//MB))
for a in As: exp.free(3*MB + 1)
print("expandable freed 4 MiB side: mapped=%dM (pages returned NOW)" % (exp.mapped//MB))
p = exp.alloc(8*MB)
print("expandable 8 MiB alloc:     OK at VA +%dM, mapped=%dM" % (p//MB, exp.mapped//MB))
```

```text
expandable fill:            mapped=40M
expandable freed 4 MiB side: mapped=24M (pages returned NOW)
expandable 8 MiB alloc:     OK at VA +40M, mapped=32M
```

The 8 MiB allocation that OOM'd before now succeeds: freed pages left at
`free()` time, and the request claimed a fresh *contiguous VA run* instead of
hunting for a contiguous hole. That is the entire trick.

Then, on any CUDA machine (documented walkthrough — field names and numbers as
of PyTorch 2.x on an A100 40 GiB, CUDA 12.2; fragments abridged, your numbers
will differ):

```bash
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True python train.py
```

```python
import torch
print(torch.cuda.memory_summary())              # the human-readable table
torch.cuda.memory_stats()["reserved_bytes.all.current"]
```

```text
---------- CUDA Memory Summary ----------
| Requested Memory (all) | 36998.12 MiB |   <- your tensor sum
| Allocated Memory (all) | 37122.50 MiB |   <- after rounding (the tax)
| Reserved Memory (all)  | 39602.00 MiB |   <- what nvidia-smi sees (before context)
| Ratio of allocated to requested memory | 1.003 |   <- internal fragmentation
| Segments          | 38 |  Inactive splits | 47 |   <- holes waiting to coalesce
| OOM events        | 2  |  cudaMalloc retries | 9  |
```

Read it in that order: requested → allocated → reserved, then the ratios and
the OOM count. `Inactive splits` is the fragmentation census — blocks that
freed next to live blocks, waiting for neighbors. A reserved/allocated ratio of
1.05 is normal churn; 1.3-plus with OOM events is cause #1 at production scale.

## How to use this knowledge

The 3 a.m. flowchart — "CUDA out of memory at night," worked top to bottom:

```text
 OOM exception in hand
 |
 +-- error says "N is reserved by PyTorch but unallocated"? --yes--> CAUSE 1
 |       fragmentation. Ladder: expandable_segments:True -> steady tensor
 |       shapes (pad variable batches) -> smaller batch -> restart (last)
 |
 +-- error / nvidia-smi --query-compute-apps shows other PIDs? --yes--> CAUSE 2
 |       co-tenant: talk to the neighbor or move; MPS to share one context
 |
 +-- distributed run, communicators never freed? ------------------> CAUSE 3
 |       NCCL scratch lives until process exit; audit communicator count
 |
 +-- graphs captured, private pools pinned by live graphs? --------> CAUSE 4
 |       drop or re-capture; reserved falls only when the graph object dies
 |
 +-- cudnn.benchmark on, convs near the edge? ---------------------> CAUSE 5
 |       budget for the chosen algo's workspace, not just tensors
 |
 +-- otherwise ----------------------------------------------------> CAUSE 6
         driver/ECC reserve: budget = total minus 0.5-1 GiB+
```

- **Read the error before touching anything.** Its two lines
  (reserved-but-unallocated; other-process usage) do the first two flowchart
  steps for you — two minutes of reading beats an hour of superstitious
  restarts.
- **Capacity-plan by peak *reserved*, not tensor sum** — peak reserved ≈
  requested + rounding tax + segment slack + inactive splits + context. Two
  models with identical tensor sums can differ by GiB in reserved; only one
  fits.
- **Order fixes by cost:** steady shapes (free) → `expandable_segments:True`
  (one env var) → smaller batch (throughput) → restart (last). `empty_cache()`
  is for handing memory to co-tenants, not for defragmenting yourself.
- **Interview whiteboard:** "free ≥ request but the allocation fails — derive
  it." Draw the alternating-block picture, name the three rules (no coalescing
  across live blocks, no crossing segments, no split below the remainder floor),
  and you have demonstrated module 01 ch05, ch19, and this chapter in 90 seconds.

## Connections

- **Module 01 ch05 (the heap):** this chapter is its replay — lifetime
  interleaving fences holes; external vs internal fragmentation; "no leaks, but
  memory grows."
- **Module 01 ch19 (allocators):** the caching allocator *is* segregated-fit —
  size classes, per-class free lists, split rules; cudaMalloc is its page heap.
- **Module 01 ch03 (page tables):** the GMMU is the same VA→physical machine —
  2 MiB/64 KiB pages, per-process tables, peer mappings off-device.
- **Module 12 ch01 (the hierarchy):** the physical side of the scarcity story —
  HBM capacity, and the stack you starve when thrashing near the ceiling.
- **Module 12 ch04 (unified memory):** the GMMU grows a fault handler that
  migrates pages across the CPU/GPU line — this chapter's machinery, extended.
- **Module 03 ch01 (bugs from first principles):** the night-only OOM is a
  *dormant fault* activated by an environment input (the co-tenant) — the code
  was always at the edge; only the environment disagreed.
- **Module 07 ch05 (Linux memory):** the OOM killer is the CPU cousin — same
  scarcity, opposite policy: Linux kills a process to keep the system alive;
  CUDA raises an exception and lets your process decide.

## Quiz

1. **Predict the block size.** For each request (PyTorch 2.5 policy), give the
   rounded block size and the pool: (a) 100 B; (b) 300,000 B; (c) 1.5 MiB;
   (d) 2.4 MiB.
2. **Derive the fragmentation failure.** The toy allocator is in this state:
   segment 0 = [4 MiB FREE][6 MiB LIVE][4 MiB FREE][6 MiB LIVE], segment 1
   identical, reserved = 40 MiB of a 40 MiB device, all four 4 MiB holes on the
   large pool's free lists. A request for 10 MiB arrives. (a) The rounded
   request? (b) Derive, rule by rule, why it fails though free = 16 MiB ≥
   10 MiB. (c) One request size that would succeed now, one that would not.
3. **Concept.** Your tensors sum to 21.0 GiB; `nvidia-smi` reports 27.4 GiB for
   your process. Where do the extra 6.4 GiB come from? Name every term.
4. **Concept.** What does `torch.cuda.empty_cache()` do, not do, and for which
   of the six OOM causes is it the correct tool?
5. **Bug diagnosis.** A job trains fine for weeks by day but OOMs only at
   night. The error reads "Process 23187 has 27.3 GiB memory in use; 1.2 GiB is
   reserved by PyTorch but unallocated." Cause? Which chapter's model predicts
   a fault that activates only at night?
6. **Bug diagnosis.** A variable-length-batch loop allocates different tensor
   sizes every step; the developer calls `empty_cache()` every batch, yet
   reserved ratchets upward to OOM. Explain the ratchet, why `empty_cache()`
   cannot stop it, and two fixes that can.
7. **Cross-chapter.** Java's copying collector defragments by moving objects
   (module 01 ch12). Why can't the CUDA caching allocator do the same to its
   segments? Which promise, made by which layer, forbids it?
8. **Bug diagnosis.** After capturing a CUDA graph, `memory_summary` shows
   reserved up by 1.4 GiB; deleting every captured tensor and calling
   `empty_cache()` barely moves it. Where is the 1.4 GiB, and who owns it?

<details>
<summary>Answers</summary>

1. **(a)** 512 B, small pool (the ladder's floor). **(b)** 300,032 B, small
   pool (rounds up to the next 512 B multiple). **(c)** 2 MiB, large pool
   (> 1 MiB rounds to the next 2 MiB multiple). **(d)** 4 MiB, large pool —
   2.4 MiB rounds past 2 MiB to the next multiple, stranding 1.6 MiB inside the
   block: the granularity tax at its worst.
2. **(a)** 10 MiB — already a 2 MiB multiple, large pool. **(b)** Exact-size
   lookup: no 10 MiB block — the lists hold only 4 MiB blocks. Best fit ≥ 10:
   largest hole is 4 MiB. Coalescing: blocked — each hole is fenced by live
   6 MiB blocks; adjacent free blocks would merge, but no two free blocks are
   adjacent. Crossing segments: impossible, separate VA reservations. New
   segment: a 20 MiB cudaMalloc against a 40/40-reserved device — the driver
   boundary refuses. Free 16 ≥ 10; contiguous policy-satisfiable space 0.
   **(c)** Succeeds: any request rounding to ≤ 4 MiB (3 MiB + 1 B → 4 MiB, an
   exact fit from a hole). Fails: anything rounding above 4 MiB — 5 MiB → 6 MiB
   finds no 6 MiB hole, and no new segment is possible.
3. Three stages: rounding to 512 B / 2 MiB blocks (allocated > requested);
   segment slack plus inactive splits — freed blocks stranded in partially-live
   segments (reserved > allocated); the CUDA context and runtime structures,
   hundreds of MiB on CUDA 12.x. 21.0 → 27.4 GiB is normal for the three
   combined; check the ratios before suspecting a leak.
4. It releases segments that are *entirely free* to the driver, dropping
   reserved, at the cost of a device sync. It does not move or merge anything —
   partially-live segments are pinned by their live blocks — so it cannot
   defragment. Correct tool for cause #2, handing memory back to co-tenants;
   useless for the OOM in progress, whose segments all hold live blocks.
5. Cause #2: PID 23187 — a night co-tenant (cron job, stale notebook) — holds
   27.3 GiB, so your allocation cannot get physical memory even though your own
   fragmentation (1.2 GiB) is minor. Module 03 ch01's model: a dormant fault
   activated by an *environment* input — the code was always at the edge; only
   the environment disagreed, and only at night. Confirm with `nvidia-smi
   --query-compute-apps`.
6. The ratchet is module 01 ch05's external fragmentation: each step's
   different tensor sizes round to different block sizes; frees leave holes
   between live blocks; over thousands of steps the holes are too small or
   wrongly sized for current traffic, so reserved creeps up though every free
   is correct. `empty_cache()` cannot help: segments containing any live block
   are never released, and mid-loop nearly every segment does. Fixes: pad/cap
   variable batches so block sizes repeat and holes get exact-fit reuse; or
   `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` so freed pages return to
   the driver immediately and contiguity is chosen in VA. (Smaller batches also
   work, by fitting under the ceiling.)
7. Compaction means moving blocks and updating every pointer to them — which
   requires enumerating all pointers. CUDA programs hold raw device pointers in
   tensors, graph-captured addresses (graphs pin exact addresses by design), and
   user-stashed `data_ptr()` values; the allocator can find and safely rewrite
   none of them. Module 01 ch05's deepest lesson, verbatim: fragmentation is
   the price of raw pointers — Java compacts because its runtime knows where
   every reference lives; the CUDA allocator cannot, because nobody does.
8. It sits in the graph's **private memory pool** (cause #4): capture must keep
   every intermediate at a stable address for replay, so the allocator pins
   those blocks in a pool owned by the *graph object*, not the tensors.
   Deleting the tensors releases only user-facing handles; the pool stays
   reserved while the graph lives, and `empty_cache()` cannot release
   partially-pinned segments. The 1.4 GiB returns only when the graph object is
   destroyed and its pool drains.

</details>
