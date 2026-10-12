# Chapter 02 — The hierarchy: latency is the tax, locality is the loophole

> **The 30-second version.** Memory is a tall tower: registers at the top, L1/L2/L3
> caches, DRAM, then disk — each level bigger and ~10x slower than the one above it.
> The tower exists because of Chapter 01's physics (fast is expensive, big is slow).
> What makes the tower livable is *locality*: programs reuse a small working set
> (temporal locality) and touch nearby bytes together (spatial locality). Nearly every
> performance question in this book — cache lines, false sharing, struct layout, GC
> behavior, why arrays beat lists, why your scroll janks — is one question: *how well
> does this code use the tower?*

## The tower, derived

If DRAM were as fast as registers, we would not need this chapter. It is not — a DRAM
trip is ~200-400 cycles (Chapter 01). But SRAM is ~6x the area per bit, so a CPU with
hundreds of megabytes of register-speed memory is physically absurd. The engineering
answer is a series of increasingly large, increasingly slow caches, each holding a
*subset* of the level below:

```
level        typical size        typical latency (cycles)    who owns it
─────────────────────────────────────────────────────────────────────
registers    16-32 per core      0                           your code (compiler)
L1 I/D       32-64 KB each       ~4 (≈1 ns)                  per-core, private
L2           256 KB - 2 MB       ~12 (≈4 ns)                 per-core, private
L3           8 - 64 MB           ~40 (≈13 ns)                shared across cores
DRAM         8 - 256 GB          ~200-400 (60-100 ns)        shared, far away
disk/SSD     TBs                 25,000 - 10,000,000         swap (Chapter 03)
```

Two properties of this table organize the rest of the module. First, the *ratio* between
adjacent levels is roughly 4-10x — a miss at any level costs about a trip to the level
below, so the cost of a memory access depends almost entirely on which level it stops
at. Second, the numbers are the single most useful performance table in computing.
Scaled to human terms, if one register access took 1 second: L1 ≈ 4 s, L2 ≈ 12 s,
L3 ≈ 40 s, DRAM ≈ 200 s (3+ minutes), an SSD page ≈ 4 days, a disk seek ≈ 4 months.
Programs are fast or slow not by the number of instructions but by *which of these
delays they trigger*.

## The cache line: the atom of the hierarchy

Caches do not store individual bytes; they store **64-byte lines** (matched to the DRAM
burst — Chapter 01). When you read one byte, the hardware fetches the whole line and
keeps it. This is why:

- **Array traversal is fast**: touching `a[0]` pays the DRAM trip for a line, and
  `a[1]..a[15]` (for 4-byte ints) ride along free.
- **Row-major vs column-major matters**: C stores 2D arrays row-by-row. Walking
  column-first touches a new line every element (64 bytes fetched, 4 used: 6% useful);
  walking row-first uses 100% of every line. The classic demo is in the lab below —
  routinely a 5-10x difference on a large matrix, with identical instruction counts.
- **One far byte is a full line**: `x = p->flag` where `p` is randomly scattered pays a
  DRAM (or L3) round trip for 64 bytes to read 1. This is why linked lists, pointer
  forests, and per-node heap allocation (Chapter 05) are cache-hostile: their *moral*
  data size is small, their *physical* footprint is huge.

## Set-associativity: where a line can live

With gigabytes below and kilobytes above, a cache must choose a mapping. If any line
can go anywhere (fully associative), lookup hardware is expensive; if a line has
exactly one home (direct-mapped), two hot addresses that hash to the same slot evict
each other *endlessly* (conflict misses). The compromise is **set-associativity**: the
address's middle bits pick a *set*, and within that set the line may live in any of N
ways (L1 is typically 8-way, L2 8-16 way).

```
physical/virtual address of a byte:
| tag (identifies the line) | set index (which set) | offset (byte within line, 6 bits) |
```

The lookup: index into the set, compare the tags of the N ways in parallel, hit or
miss. Replacement within the set is pseudo-LRU or RRIP. The insider consequence: power
of two + stride pathologies — iterating an array with stride 4096 bytes on a cache with
the same set structure maps *every* access to one set: 16 lines fill it and the rest of
the cache sits idle. Real bug, real production slowdowns, invisible to instruction
profilers (see the lab).

## Locality, the loophole

The tower is bearable because programs are repetitive:

- **Temporal locality**: the 90/10 rule — roughly 90% of accesses touch 10% of data
  (loops, hot structs, the same few objects). The cache keeps what you just used
  because you will probably use it again.
- **Spatial locality**: if you touched byte N you will likely touch N+1..N+63. The line
  fetch banks on it.

Every data-structure decision in this module is a locality statement. Contiguous
arrays (Go slices, `std::vector`, Java arrays, NumPy buffers) have perfect spatial
locality; node-based structures (linked lists, trees per-node-allocated, Python's
dict-of-dicts, DOM trees — Chapter 15's leak-hunting) have perfect *anti*-locality.
GC allocations (Chapters 11, 12, 15) get part of this for free: a bump allocator packs
same-age objects adjacently, which is why a young-generation copy (Chapter 12) also
*defragments the cache*.

## The TLB is also a cache (and it is scarce)

Chapter 03 translates every virtual address through page tables. The results are cached
in the **TLB** — and the TLB is small: order 64-1536 entries, each typically covering
one 4 KB page. Covering 1 GB with 4 KB pages takes ~262,144 entries: impossible. This
is why **huge pages** (2 MB, 1 GB) exist — one TLB entry covers 2 MB, and databases,
JVMs (with `-XX:+UseLargePages`), and mobile platforms (Chapter 22) all care intensely.
A *TLB miss* is a page-table walk (up to 4 extra memory accesses on x86-64 — which
themselves are cached in the paging-structure caches). The hierarchy is fractal: caches
for your data, caches for your translations, caches for the caches.

## Prefetching: guessing your future

Hardware prefetchers watch your access stream, detect strides, and fetch lines *before*
you ask. Sequential array scans run at near-DRAM-bandwidth not because each access is
magic but because the prefetcher stays a few lines ahead. Pointer-chasing defeats it
(the next address is data-dependent). Software prefetch (`__builtin_prefetch`,
`prefetcht0`) exists for patterns the hardware cannot guess — mostly in database and
JIT engines, and rarely worth it in application code. The discipline instead: *shape
your data so the hardware can guess* — contiguous, forward, one stream at a time.

## Multicore, in one paragraph

Each core has private L1/L2; L3 is shared. Two cores editing *different variables that
share a 64-byte line* force the line to ping-pong between their private caches under
the coherence protocol (MESI — Chapter 20 takes this fully). That is **false sharing**,
and it turns a "we added threads, it got slower" bug into a chapter of its own. The
fix (pad/align hot per-thread data to 64-byte boundaries — `alignas(64)` in C/C++,
`@Contended` in Java) is pure Chapter 01+02 arithmetic.

## What insiders know

- **The latency table is the profession's crib sheet** (sizes and cycles above; Jeff
  Dean's "Latency Numbers Every Programmer Should Know" popularized the human-scale
  version). Interviewers use it; profilers assume it.
- **Latency is not throughput**: sequential streaming hits ~full DRAM bandwidth (tens
  of GB/s) despite ~100 ns latency, because the memory system pipelines many
  outstanding misses. Pointer chasing gets ~one miss at a time: ~10-100x slower for
  the same bytes moved.
- **`perf stat -e cache-misses,LLC-load-misses`** is the 30-second truth serum; `perf
  c2c` *names the false-sharing line*.
- **Way prediction**: L1 caches guess the way so a hit costs ~4 cycles despite 8-way
  associativity; a mispredict adds one cycle.
- **Non-temporal stores** (`movntdq`, used by big `memset`/`memcpy`) write around the
  cache — a 4 GB memset should not evict your 256 KB working set.
- **Caches do not cache cold-to-warm, they cache warm-to-warm**: the first touch of
  any page also pays a page fault (Chapter 03) — often the *real* cost of "touching a
  big buffer once."

## See it yourself

Two experiments, ten minutes, laptop-only. First, locality:

```c
#include <stdio.h>
#include <time.h>
#define N 8192
static int m[N][N];
int main(void) {
    clock_t t0 = clock();
    long s = 0;
    for (int i = 0; i < N; i++)          // row-major: consecutive bytes
        for (int j = 0; j < N; j++) s += m[i][j];
    clock_t t1 = clock();
    for (int j = 0; j < N; j++)          // column-major: 32 KB stride
        for (int i = 0; i < N; i++) s += m[i][j];
    clock_t t2 = clock();
    printf("row=%ldms col=%ldms (sum=%ld)\n",
           (t1-t0)*1000/CLOCKS_PER_SEC, (t2-t1)*1000/CLOCKS_PER_SEC, s);
    return 0;
}
```

Typical result on x86-64: row-major ~25 ms, column-major ~150-250 ms — 6-10x for
identical work. The difference is pure Chapter 02: every column step lands in a new
line. (Bonus: swap the loop order of the *write* pattern and watch `perf stat -e
cache-misses` confirm the story.)

Second, hardware counters:

```console
$ perf stat -e cache-references,cache-misses,L1-dcache-load-misses ./a.out
```

Find your machine's available events with `perf list | grep -i cache`. On macOS, use
Instruments' Counters template instead — same underlying PMU.

## How to use this knowledge

- **Data-structure choice** is now a cache decision: prefer contiguous + indexed
  (arrays, `std::vector`, column tables) over node-based structures for hot paths;
  this single rule explains most "rewrite it in C" folklore — often the rewrite just
  fixed locality.
- **Hot-struct design**: Chapter 06 shows 20-30% struct-size (hence cache-occupancy)
  wins from field reordering alone.
- **Perf debugging methodology**: before optimizing *instructions*, ask which tower
  level each hot access stops at. `perf stat` first; instruction-level tuning second.
- **GC and allocator intuition**: Chapters 11/12/15's generational designs *are*
  locality strategies; allocation rate matters because it churns the cache and the
  write barriers (Chapter 20) that maintain it.
- **Mobile**: Chapter 22's "allocation spikes cause jank" is this chapter — the GC's
  cache traffic competes with your render thread for the same shared L3 and DRAM
  bandwidth.

## Connections

- **Chapter 01** — the line size, alignment, and atomic-word facts this chapter builds
  on.
- **Chapter 03** — TLB as a translation cache; page faults on first touch.
- **Chapter 05 / 19** — allocator locality: bump allocation packs objects; free lists
  scatter them.
- **Chapter 06** — struct layout, SoA vs AoS, the 64-byte budget per hot object.
- **Chapter 12 / 15** — generational GC as a locality machine; V8's semispace flip
  compacts *and* cache-warms the survivors.
- **Chapter 20** — coherence (MESI), false sharing, and the memory model.
- **Chapter 22** — mobile: shared L3/DRAM bandwidth is the frame budget the GC steals.

## Quiz

1. **Concept.** Why does the hierarchy use ~10x steps between levels instead of two
   levels (registers + DRAM)?
2. **Concept.** Explain why a linked list traversal and an array traversal with the
   same number of nodes/elements can differ 10x in time despite executing a similar
   number of instructions.
3. **Predict the output.** A loop sums `int` elements of a 64 MB array with stride 64
   bytes (one int per line). Roughly what fraction of fetched bytes is actually used,
   and what is the best-case speedup of switching to stride 4 (contiguous)?
4. **Predict the number.** L1 hit ≈ 4 cycles, DRAM ≈ 300 cycles. A loop makes 1,000,000
   accesses, 99% L1 hits. Average cycles per access?
5. **Predict the behavior.** Two threads increment separate `long` counters declared
   side by side. Performance is *worse* than single-threaded. What is happening and
   what is the fix?
6. **Bug diagnosis.** A matrix multiply got 6x faster when the team swapped two nested
   loops. Explain exactly what changed in the memory system.
7. **Bug diagnosis.** After "upgrading" a large hash table to chained nodes, p99
   latency doubled, though operation counts were unchanged. Why?
8. **Cross-chapter.** Why does an aligned 8-byte load never tear, and which chapter's
   entire discipline depends on that fact?

<details>
<summary>Answers</summary>

1. Each cache level is sized/priced for its latency class; adding intermediate levels
   lets the common case stop high in the tower. Two levels only (registers + DRAM)
   means every non-register access pays the full ~300-cycle tax; with L1 catching
   ~95%+ of accesses, the *average* cost collapses. The 10x-step design is a cost
   gradient: SRAM per byte is tens of times more expensive than DRAM per byte, so you
   buy exactly as much of each speed as the workload's locality lets you exploit.
2. Array elements are contiguous, so each 64-byte line delivers 16 ints and the
   prefetcher can stay ahead — the loop runs near bandwidth. List nodes are
   independently heap-allocated (Chapter 05) and pointer-chained: every `next` is a
   data-dependent address, likely a new line (and a new DRAM row — Chapter 01),
   un-guessable by the prefetcher. Same instructions, 10x the tower trips.
3. One 4-byte int out of a 64-byte line: 4/64 = 6.25% useful. Contiguous stride 4 uses
   100% (16 ints per line) — best case ~16x fewer lines, though prefetch/pipeline
   effects will realize somewhat less.
4. 0.99 x 4 + 0.01 x 300 = 3.96 + 3 = ~7 cycles — notice 1% misses cost as much as
   99% of the hits combined. This asymmetry is the whole subject: misses dominate.
5. False sharing: the two counters sit in the same 64-byte line; each core's write
   invalidates the other's copy (MESI — Chapter 20) and the line ping-pongs between
   private caches. Fix: pad each counter to its own line (`alignas(64)` or a 64-byte
   stride array; Java: `@Contended`).
6. Loop order changed the access pattern of the inner loop from column-major to
   row-major (or vice versa for the language's layout): the inner operand became
   contiguous, so each line fetched feeds ~16 FMA operations instead of 1, and the
   prefetcher can stream it. Pure Chapter 02 — no algorithmic change at all.
7. Chaining allocated each bucket entry as a separate heap node: previously the open
   addressing table probed *contiguous* slots (one line = several probes); now every
   chain hop is a new random line. Same number of operations, each one costs a trip to
   L3/DRAM instead of L1.
8. Because the hardware fetches the whole aligned 64-byte line as one unit and the
   alignment guarantees the 8 bytes lie in exactly one line — no observer can see half
   of it. Chapter 20's entire lock-free discipline (and the "aligned word access is
   atomic" rule that C++/Rust atomics compile down to) is built on this.

</details>
