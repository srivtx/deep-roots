# Chapter 03 — LSM trees

> **The 30-second version.** Chapter 01's physics said random writes are expensive
> and sequential writes cheap; chapter 02's B-tree still lands every update on a
> scattered leaf page. The LSM (log-structured merge) tree finishes the inversion:
> buffer writes in a sorted in-memory table, flush it as an immutable sorted file,
> merge the files in the background. Every write becomes an append; reads must then
> search several sorted runs (bloom filters make most probes free); dead versions
> accumulate until compaction merges them away. The whole structure is generational
> GC replayed on disk — memtable = nursery, levels = generations, compaction =
> mark-compact — "GC for disk". Numbers below are LevelDB 1.23 constants and
> RocksDB 8.x defaults, marked where they differ.

## 1. Turning random writes into sequential ones: the LSM bet

Chapter 01's gap is three orders of magnitude: an HDD does ~100-200 random IOPS but
~200 MB/s sequentially; on an SSD, random 8 KB updates wake the FTL's own garbage
collector. Chapter 02's B-tree hides some of that behind the buffer pool, but a
leaf-page update eventually evicts to a *random* offset, and half-full pages tax
every insert with splits. The question that produces LSM trees: *what if a write
never touched disk until it could be written sequentially?*

Derivation, step by step:

1. Buffer updates in memory, sorted. A flush then emits one large *sorted run* — a
   single sequential write. Random → sequential: solved.
2. But memory is volatile, so every update is first appended to a write-ahead log
   (chapter 04 owns that machinery). The memtable is now disposable: crash → replay.
3. Sorted runs accumulate, and a read must check every run that could hold the key.
   So merge overlapping runs in the background — a k-way merge-join, the merge phase
   of an external sort. Merging is the *deferred cost* of the cheap write.

The whole architecture (O'Neil et al., 1996; industrialized by Bigtable,
LevelDB, RocksDB):

```
 client PUT ──▶ [WAL append]──▶ [ memtable ]  RAM, skiplist, sorted
                     │              │          (LevelDB: ~4 MB; RocksDB: 64 MB)
                     │              │ full → frozen "immutable memtable"
   (crash? replay    │              ▼
    the log from     │        [ FLUSH: one sequential write ]
     scratch)        │              ▼
                    │        [ L0 SSTs: 2 MB each, OVERLAPPING ]
                    │              │   every flush covers the whole key range,
                    │              │   so L0 files overlap — all are probed
                    │              │   on a read
                    │              ▼ trigger (LevelDB: 4 files at L0)
                    │        [ COMPACTION: k-way merge-join of overlapping runs ]
                    │              ▼
                    │        [ L1: 10 MB, NON-overlapping ]  size ratio ~10x
                    │        [ L2: 100 MB ] [ L3: 1 GB ] ... L6: ~1 TB
                    └────────── durable answer lives in the WAL, not the pages
```

The bet: *pay later, in batch, sequentially* — instead of *now, random, per write*.

## 2. The anatomy: WAL, memtable, SST, levels

**The memtable is a skiplist — a deliberate choice.** A hash table makes inserts O(1)
but cannot iterate in key order, and a flush must emit a *sorted* run. A red-black
tree iterates sorted but *rebalances*: rotations rewrite pointers inside existing
nodes, so a concurrent reader needs a latch on every node it crosses. A skiplist
(LevelDB: height ≤ 12, branch probability 1/4) never mutates a published node:
insertion CAS-installs one new node's forward pointers; existing nodes are untouched.
One writer plus unlimited lock-free readers, no rebalancing, O(log n) expected.

When the memtable fills (RocksDB 8.x default `write_buffer_size` = 64 MB), it is
frozen, a new one rotates in, and the frozen one is flushed to disk as an **SST**
(sorted string table):

```
+---------------------+  ┐
| data block 0        |  │  KV pairs, key-sorted, ~4 KB per block,
| data block 1        |  │  prefix-compressed (LevelDB: restart point
| ...                 |  │  every 16 keys so a block can binary-search)
+---------------------+  │
| filter block        |  │  bloom filters, 10 bits/key, "present?" answers:
| (bloom, 10 bits/key)│  │  NO = definitely absent (skip the data blocks)
+---------------------+  │
| index block         |  │  first key of each data block → file offset
+---------------------+  │  (kept in memory for the open SST)
| footer, 48 B        |  │  index+filter handles + magic 0xdb4775248b80fb57
+---------------------+  ┘  (LevelDB 1.23)
```

A cold point lookup in one SST costs footer (cached) → index → one data block = 2
reads; a bloom "no" costs 1 filter read. SSTs are *immutable*: no torn pages
(chapter 01), no in-place concurrency, no free-list bookkeeping.

**Levels.** L0 files overlap because each flush covers the whole key range. Compaction
into L1 re-partitions the key space so each L1+ file owns a disjoint range — a key
can live in at most one file per level. Levels grow by ~10× (LevelDB: L1 = 10 MB,
then ×10 per level → L6 ≈ 1 TB), which is the survivor-space arithmetic from module
01 ch12: small, hot generations at the top; big, cold, stable ones at the bottom.

## 3. Compaction: the merge debt and the amplification triangle

Every cheap write borrowed work from the future. The debt is (a) dead versions — the
old copies of every updated key — and (b) run count, the read's tax. Compaction pays
it with a merge-join: newest version wins, older versions drop, tombstones (delete
markers) swallow the versions beneath them. Two repayment schedules exist:

- **Leveled** (LevelDB, RocksDB default): an over-budget level picks one file,
  merges it with the ~10 files it overlaps in the next level, and writes the result
  back there. Each key is rewritten once per level it passes through, and a 2 MB
  L0 run landing in a 20 MB L1 rewrites all ~22 MB — the ×10 ratio is the
  write-amplification engine. Measured write-amp: **20-30×** (RocksDB 8.x docs'
  own figure). Reward: at most one run per key range per level.
- **Sized-tiered** (Cassandra size-tiered, RocksDB "universal"): tiers pile up
  same-size runs; when enough accumulate (Cassandra default: 4), they merge into
  one bigger run at the next tier. Each merge touches only the runs being merged,
  so write-amp stays near **1-5×**. Cost: many overlapping runs alive at once, and
  dead versions survive until their tier merges.

Draw the tradeoff — this diagram *is* the chapter:

```
                read-amp LOW  (a point read probes few runs)
                          ▲
                          │      ● LEVELED:  ~1 probe per level (6-7 max,
                          │                  blooms make most probes free);
                          │                  write-amp 20-30x, space-amp ~1.1x
                          │
                          │
   space-amp LOW ─────────┼────────────────── write-amp LOW
   LEVELED ~1.1x          │                  TIERED ~1-5x
   (1 live copy/key       │        ● TIERED: probe every overlapping run
   + in-flight merge)     │                  (dozens without blooms);
                          │                  space-amp ~2x, capped by
                          │                  RocksDB max_size_amplification_
                          │                  percent = 200 (≈ 2x live data)
                          │        ○ NO COMPACTION: write-amp 1x, but
                          │          space-amp and read-amp grow unbounded
                          │          — the lazy corner
```

Compaction is where you *slide* on the triangle; the corners are unreachable.

**Compaction storms.** If ingest outruns merge throughput, debt grows, and the
engine's escape valve is *backpressure on writers*: LevelDB slows writes ~1 ms each
at 8 L0 files and blocks them at 12; RocksDB has slow and stop triggers on L0 file
count, memtable count, and pending compaction bytes. The symptom is p99 latency —
writes stall for hundreds of ms while background merges chew through debt. The
gauges insiders watch: `rocksdb.estimate-pending-compaction-bytes` (the debt),
`rocksdb.num-files-at-level0` against the trigger, a nonzero `rocksdb.actual-
delayed-write-rate` ("throttled *right now*"), `rocksdb.is-write-stopped`, and the
`Stalls(count):` line in the LOG's periodic DB-stats dump. Leveled's tax is
write-amp (SSD endurance, CPU, stalls); tiered's is space-amp (disk and read-amp).

## 4. Reading it back: level probes and the bloom filter

A point read walks newest → oldest: memtable → immutable memtables → every L0 file
(they overlap) → at most one file per L1+ level. Worst-case arithmetic for a 1 TB
DB (LevelDB sizing): 2 memtables + 4 L0 files + 6 levels ≈ **12 probes** for a key
that isn't there. That is the read-amp bill — unless most probes are free. They are:
the filter block.

**Deriving the bloom filter.** The filter is m bits with k hash functions over n
keys. After n insertions, a specific bit is still 0 with probability
(1 − 1/m)^{kn} ≈ e^{−kn/m}; a *non-member* slips through only if all k of its
positions are set:

    FP ≈ (1 − e^{−kn/m})^k

Minimize over k: k = ln 2 · m/n ≈ 0.7 × bits-per-key — at the optimum the filter is
half full and the rate collapses to the asymptotic bound:

    FP ≈ 0.6185^(m/n)

Ten bits per key → 0.6185^10 ≈ 0.008 — **about 1%**, which is why 10 bits/key is the
shipped default (LevelDB rounds k to 0.69 × 10 = 6 hashes; measured ≈ 0.85%).
Doubling to 20 bits/key buys 0.0067%: false positives fall *exponentially* in filter
size. And 10 bits = 1.25 bytes per key against a ~100-byte entry — the filter is ~1%
of the SST. But 1% of a 64 GB SST is ~640 MB of bits, far too big to re-read from
disk per probe — which is why **big filters live in the block cache**: modern RocksDB
builds one bloom per whole SST ("full filter") or per partition, pinned in cache, so
a negative probe never touches the data blocks. Of the 12 worst-case probes, ~11 are
now cached bit-tests; only the level holding the key reads index + one data block.
Point reads cost ~1-2 I/Os.

Deletes, meanwhile, are *writes*: a tombstone masks lower-level copies and can only
be discarded when compaction proves nothing older survives beneath it — hence the
classic ticket "I deleted half the table and disk usage didn't move".

## 5. GC for disk: the unification and the decision table

Set module 01 ch12's generational heap beside this chapter — the mapping is exact:

| LSM (this chapter) | JVM generational heap (OpenJDK 17) |
|---|---|
| memtable | eden/nursery — newest versions, most die here |
| flush = sorted copy to L0 | minor GC evacuation of eden |
| L0/L1 (small, ×10 sizing) | survivor spaces (8:1:1) |
| L1..L6 | old generation, tenured by level |
| compaction merge | mark-compact of old gen |
| dead versions dropped mid-merge | unreachable objects reclaimed |
| tombstone (delete marker) | the program calling `free()`, honored later |

One structural difference: liveness is decided by *recency* (newest version wins
the merge) not by tracing from roots, and readers never pause — the "GC pause" is
paid by writers as a compaction stall. Chapter 02's B-tree, meanwhile, is module 01
ch05's *other* dynasty: update-in-place with free-space management is a free-list
allocator (FSM/vacuum = the free list; page splits = fragmentation). The
allocator/GC feud of module 01, replayed on disk, is the B-tree/LSM feud here.

So when does each win?

| Workload | Winner | Why |
|---|---|---|
| Write-heavy (logs, telemetry, time series) | LSM | sequential appends, write-amp is a tunable budget |
| Point-read heavy, low write rate | B-tree | one descent, no versions, no background storms |
| Range-scan heavy | B-tree | clustered leaves; LSM scans must merge runs, skip dead versions |
| Space-constrained / steady p99 | B-tree | space-amp ~1x; no compaction stalls |
| Mixed, write-dominated, bloom-friendly keys | LSM | ~1-2 I/O reads, tiny filters, sequential writes |

## What insiders know

- **The bloom arithmetic you can recite**: FP ≈ 0.6185^(bits/key). 10 bits → 0.8%,
  20 bits → 0.0067%, and k = 0.69 × bits-per-key is LevelDB's truncation (10 bits →
  k=6, not the theoretical 7).
- **The compaction-stall gauges, by name** (RocksDB 8.x): `estimate-pending-
  compaction-bytes` is the debt; `num-files-at-level0` vs. the 4-file trigger;
  nonzero `actual-delayed-write-rate` = throttled now; `is-write-stopped` = the
  world has stopped. LevelDB's ladder is 4/8/12 L0 files (trigger/slow/stop).
- **Why a skiplist for the memtable**: insert touches only the new node (CAS on
  forward pointers, p = 1/4, height 12) — no rotation ever rewrites a published
  node, so readers are latch-free; a hash table can't produce the sorted run a
  flush needs.
- **Leveled vs tiered in practice**: leveled (RocksDB default) unless write-amp
  dominates — do the SSD-endurance sum: 1 TB/day ingest × 20-30× write-amp ≈
  20-30 TB/day of flash writes; on a 1-DWPD 1 TB drive that's over budget. Then you
  want universal compaction, retuning `max_size_amplification_percent` (default
  200 ≈ 2× live data) to keep the disk from filling.
- **The GC mapping, one line**: memtable = nursery, flush = minor GC, levels =
  generations, compaction = mark-compact, tombstone = deferred `free()`.
- **The delete trap**: `DELETE` writes a tombstone; space returns only when that
  tombstone merges past the deepest level still holding a copy of the key — mass
  deletes need an explicit `CompactRange` (RocksDB) to reclaim disk promptly.
- **Immutable SSTs dodge chapter 01's torn-page problem**: no page is rewritten
  in place, so there is no half-landed state — SSTs swap atomically in the manifest.

## See it yourself

**Lab A — measure the ~1% claim (CPython 3.12).** Twenty lines: 10 bits/key,
k = 7, 10,000 members, 100,000 non-members:

```python
import hashlib, math
N, BITS_PER_KEY, K = 10_000, 10, 7          # 10k keys, 10 bits/key, 7 hashes
M = N * BITS_PER_KEY                        # 100,000-bit filter (~12 KB)
filt = bytearray((M + 7) // 8)

def positions(key):                         # Kirsch-Mitzenmacher double hashing
    h = hashlib.sha256(key.encode()).digest()
    h1, h2 = int.from_bytes(h[:8], "big"), int.from_bytes(h[8:16], "big")
    return [(h1 + i * h2) % M for i in range(K)]

for i in range(N):                          # insert the members
    for p in positions(f"key-{i}"):
        filt[p >> 3] |= 1 << (p & 7)

fp, TRIALS = 0, 100_000                     # probe NON-members only
for i in range(N, N + TRIALS):
    if all(filt[p >> 3] >> (p & 7) & 1 for p in positions(f"key-{i}")):
        fp += 1

print(f"measured: {fp / TRIALS:.4%}")       # ~0.83%
print(f"theory  : {(1 - math.exp(-K / BITS_PER_KEY))**K:.4%}")
```

My run: `measured: 0.8330%`, `theory: 0.8194%` — the asymptotic bound 0.6185^10 ≈
0.815%: double hashing costs almost nothing. Set `BITS_PER_KEY = 20` and watch
~0.007% — exponential in bits.

**Lab B — sst_dump and the compaction timeline** *(marked: documented walkthrough —
RocksDB is not installed in this environment; per RocksDB 8.x docs, runnable on
any laptop with a RocksDB build)*:

```console
$ ./db_bench --benchmarks=fillrandom --num=5000000 --db=/tmp/lsm \
    --statistics=1 --stats_dump_period_sec=2
$ grep -A4 "Compaction Stats" /tmp/lsm/LOG
** Compaction Stats [default] **
Level  Files  Size(MB) Score Read(GB) Rn(MB/s) Write(GB) ...
  L0      3        2    1.50 ...
  L1     12       19    0.80 ...
```

Watch L0's `Files` column climb 1→4, snap to 0 as a compaction fires, and bytes
cascade L0→L1→L2: the write path of section 1, live. `Score ≥ 1.0` means "level's
size budget exceeded — compaction due". Then inspect one sorted run:

```console
$ ./sst_dump --file=/tmp/lsm/000123.sst --command=scan --output_hex
Sst file format: block-based table
'key000001' seq:4242, type:1 => 'value...'
$ ./sst_dump --file=/tmp/lsm/000123.sst --command=check
[OK] 000123.sst  # blocks inspected, 0 errors
```

`--command=check` verifies every checksum — section 2's immutability claim is
testable, not folklore. The `seq:` field is the sequence number: the *version
stamp* the merge uses to pick the winner.

## How to use this knowledge

- **Provisioning arithmetic**: multiply expected ingest by write-amp (20-30×
  leveled) and compare with the SSD's DWPD rating — one sum that prevents the
  classic "my flash died in a year" design.
- **The "disk didn't shrink after DELETE" ticket**: explain it (dead versions +
  tombstone depth) and fix it (`CompactRange` / bottom-level compaction) instead of
  re-running the DELETE and hoping.
- **p99 sawtooth diagnosis**: spikes correlated with L0 file count and pending-
  compaction-bytes are compaction stalls, not lock contention — reach for the
  trigger/threads/rate-limiter knobs, not the query planner.
- **Engine selection**: etcd chose a B+tree (bbolt) for read-heavy config;
  Cassandra/TiKV/CockroachDB chose LSM for write-heavy data — both decisions
  reconstruct from the triangle, not from fashion.
- **The interview whiteboard**: "derive the bloom FP rate" is a storage-engine
  staple; you can do it from (1 − 1/m)^{kn}, not from memory.

## Connections

- **Module 01 ch12 (Java's generational heap)** — the payoff analogy: nursery/
  survivors/old-gen map onto memtable/L0/L1+/deep levels, minor GC onto flush,
  mark-compact onto compaction. Same tower, one level down.
- **Module 11 ch01** — the random-vs-sequential gap this design converts;
  immutability as the torn-page escape.
- **Module 11 ch02** — the rival dynasty: update-in-place + free-space management
  = module 01 ch05's free-list allocator; the point-read/range-scan champion.
- **Module 11 ch04** — the WAL that makes the memtable disposable; replay =
  rebuild the nursery.
- **Module 11 ch05** — MVCC version chains are the same multiversioning at row
  scale; purge/vacuum there plays compaction's role here.
- **Module 07 ch05 (Linux memory machinery)** — the memtable flush is dirty-page
  writeback: the dirty-ratio triggers are the L0 file triggers; backpressure has
  the same shape one tower level down.

## Quiz

1. A colleague proposes replacing the memtable's skiplist with a hash table "because
   O(1) beats O(log n)". Give the two mechanism-level reasons this breaks the design.
2. A bloom filter for an SST is provisioned at 8 bits per key with the optimal k.
   Compute k and the expected false-positive rate. Show the derivation, not a
   remembered number.
3. LevelDB sizing: L1 = 10 MB, ×10 per level, 4 overlapping 2 MB SSTs sit at L0, plus
   2 memtables. For a point read of a key that exists nowhere, what is the worst-case
   number of structure probes (bloom checks), and how many block reads if all blooms
   answer "no"?
4. Write latency is fine for hours, then p99 spikes to 800 ms in a sawtooth; during
   spikes `num-files-at-level0` reads 9 and `actual-delayed-write-rate` is nonzero.
   Diagnose the mechanism and name two knobs that address it.
5. You `DELETE` 90% of a 100 GB table in one transaction. An hour later `du` shows
   97 GB. Nothing is broken — where did the bytes go?
6. A bulk ingest runs 10× faster than normal and writes stop outright for seconds
   at a time (`is-write-stopped` = true). Why does the engine *choose* to stop
   writers rather than let them run at, say, half speed?
7. Map each LSM element onto the OpenJDK generational heap of module 01 ch12
   (memtable, flush, levels, compaction, dead versions), and name one way the two
   collectors differ structurally.
8. Chapter 02 argued a B-tree is "a free-list allocator with pages". Which module 01
   memory-management concept is an LSM tree, and what does that ancestry *predict*
   about each engine's write-amp and space-amp?

<details>
<summary>Answers</summary>

1. **A hash table can't iterate in key order** — the flush must emit one sorted
   run, else every flush pays a full sort and the merge-join loses its sorted
   inputs. And it kills *range* reads, which the sorted structure serves for free.
2. Optimal k = ln 2 · m/n = 0.693 × 8 ≈ **5.5, so k = 5 or 6**. At the optimum the
   filter is half full: FP ≈ 0.6185^(m/n) = 0.6185^8 ≈ **2.2%**. Derivation: a bit
   is still clear with probability (1 − 1/m)^{kn} ≈ e^{−kn/m}; a non-member
   false-positives when all k probes hit set bits, (1 − e^{−kn/m})^k; minimized
   over k at k = ln 2 · (m/n), where the fill is 1/2, giving (1/2)^k = 0.6185^(m/n).
3. 2 memtables + 4 L0 files (overlapping: *all* probed) + 6 levels × 1 file
   (non-overlapping: at most one each) = **12 bloom checks**. If every bloom says
   "no", **zero data blocks are read** — negative probes are cached bit-tests.
4. **Compaction debt → write stall**: ingest outran merge throughput; L0 crossed
   the slow trigger (8 files) and writes took per-write delays. Knobs:
   `max_background_compactions`, the L0 slow/stop triggers, bigger memtables, an
   ingest rate limit — or universal compaction to trade space-amp for write-amp.
5. The `DELETE` wrote tombstones; the ~90 GB of *dead versions* is still on disk,
   and a tombstone frees space only when compaction merges it past the deepest
   level still holding a copy of the key. Waiting may work eventually;
   `CompactRange` / bottom-level compaction reclaims it now: delete marks,
   compaction collects — the GC analogy in one ticket.
6. Because unbounded L0 growth makes every future read probe an unbounded number
   of overlapping runs: the engine trades write availability for a hard read-amp
   bound — backpressure, same shape as the kernel's dirty-ratio writeback throttle
   (module 07 ch05). If merges are saturated, half-speed writes still grow the
   debt; stopping shrinks it.
7. memtable = eden/nursery; flush = minor-GC evacuation; L0/L1 = survivors, deeper
   levels = old gen (×10 sizing ≈ tenuring); compaction = mark-compact of old gen;
   dead versions = unreachable objects, dropped mid-merge. Structural difference:
   liveness is recency (newest version wins), decided by the merge rule — no
   tracing from roots — and the pause lands on writers (stalls), not readers.
8. A **copying/compacting collector** (module 01 ch12 lineage): never update in
   place, copy live data forward during compaction, drop the rest. That ancestry
   predicts high write-amp (every live version copied level by level) with
   near-zero fragmentation — space-amp ≈ 1.1× — versus the B-tree free-list
   lineage: update-in-place, low write-amp, perpetual fragmentation and
   free-space bookkeeping.

</details>
