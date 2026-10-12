# Module 11 — Database storage engines

> The disk is the new heap: pages are the chunks, the buffer pool is the
> allocator's cache, B-trees and LSM trees are two answers to fragmentation,
> MVCC is copy-on-write at row scale, and the WAL is the write barrier made
> durable. Every storage-engine concept is a module 01 concept, one tower level
> down — and the derivation is the point.

**5 chapters · one final exam · prerequisite: module 01 chapters 02 (the
hierarchy), 03 (COW/virtual memory), 05 (the heap), 12 (generational GC — the
LSM analogy lands on it); module 10 ch04 (write barriers) makes ch04 click.**

## How to study it

Chapter 01 is the physics contract (disks, fsync, pages); 02-03 are the two
great page-organization dynasties (B-trees vs LSM); 04 is durability (WAL,
recovery); 05 is concurrency (MVCC). PostgreSQL and MySQL/InnoDB are the
running concrete examples (open, documented, inspectable) with RocksDB/
LevelDB as the LSM reference. Labs: a real database you can crash and inspect.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The disk contract](chapters/01-disk-contract.md) | Sectors, pages, fsync, torn writes — the physics of durable memory. |
| 02 | [B-trees](chapters/02-btrees.md) | Fanout derived, node anatomy, splits, clustered vs secondary — the read-optimal tree. |
| 03 | [LSM trees](chapters/03-lsm-trees.md) | Memtables, levels, compaction, blooms — the write-optimal tree, aka "GC for disk". |
| 04 | [WAL & crash recovery](chapters/04-wal-recovery.md) | Log-as-truth, LSNs, group commit, the redo/undo walk — fsync as write barrier. |
| 05 | [MVCC](chapters/05-mvcc.md) | Snapshots, version chains, write skew, purge — COW at row scale. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the disk contract.** The tower's new bottom (module 01 ch02
extended): NVMe ~20-100µs, SATA SSD ~100µs, HDD ~10ms — and the *throughput*
axis (IOPS vs sequential bandwidth: HDD 100-200 IOPS vs ~200MB/s sequential —
the random/sequential gap that explains every storage-engine design decision).
SSD internals enough to matter: pages (~4-16KB) and erase blocks (~256KB-4MB),
FTL remapping, write amplification (WAF: the update-in-place tax — *why
databases care*: random 8KB updates on SSDs trigger internal GC; the insight
that "random writes are expensive" survived the media change). The fsync
contract: what it actually guarantees (durable-at-power-loss), the enterprise
vs consumer SSD difference (power-loss protection capacitors — the insider
fact that changes your database's fsync latency by 10x), the torn-write
reality (a 16KB page write against 4KB sectors can half-land — the
atomicity unit is the *sector*, not your page). O_DIRECT vs page cache
(double buffering: when each is right — Postgres' default reasoning vs the
redo... the preallocation patterns). The database page as the universal
atom: anatomy (header with page number, checksum, LSN; trailer; free-space
management — the "page-in/page-out" lifecycle), page checksums as torn-page
detectors. The chapter's thesis formalized: a storage engine = an allocator
with a durability contract (module 01 ch05's free lists re-imagined over
fixed pages — free space maps as the disk free list). Lab: `strace` a
`CREATE TABLE` + insert and find the fsync calls; `io_uring`/`fio`
sequential-vs-random measurement; observe page sizes (pg page size 8KB,
InnoDB 16KB).

**Chapter 02 — B-trees.** Why not binary trees: derive fanout (a 16KB node
with 8-byte keys + child pointers ≈ ~1000 children → depth 3 reaches a
billion rows; the comparison: a balanced BST at log2(10^9) ≈ 30 disk
reads — the 10x gap that *is* the argument). Node anatomy (InnoDB
concrete): the 16KB page, FIL header/trailer, the user-record linked list
with infimum/supremum sentinel markers (insider detail: records are *linked
within the page*, enabling the next-key locking that lock-free... that
row-locking exploits), the page directory (sparse slot array for binary
search within a page — the two-level search: directory → walk). Splits:
the 50-50 vs 90-10 policies (sequential-key inserts and the right-most-page
optimization — why monotonically increasing keys are the B-tree's favorite
workload), page splits as fragmentation source. Secondary indexes: the
row-locator decision (InnoDB secondary indexes store the *primary key*
as the pointer — the insider trap: a secondary-key lookup = two tree
descents; the covering-index escape), PostgreSQL's HeapTuple TIDs (index
→ heap indirection, HOT updates as the update-path optimization).
Prefix compression (suffix truncation). Concurrency on the tree: latch
crabbing (lock-couple descent: hold parent until child is "safe" — the
protocol derived), and the optimisticRead... optimistic descent variants.
Range scans as the B-tree's birthright (leaf pages doubly-linked — the
sequential-scan mechanism). Lab: build a tiny B-tree on disk in Python
(fanout 16) and measure depth vs row count; inspect a real InnoDB page
(innochecksum / page-dumping or the documented layout walkthrough).

**Chapter 03 — LSM trees.** The write-path derivation: random updates are
expensive (ch01's conclusion) → convert random to sequential → accumulate
in memory, flush sorted runs, merge later. The architecture: WAL → memtable
(a skiplist — why: lock-friendly concurrent reads, no rebalancing — the
insider's answer), flush → SST (sorted string table: data blocks + index
block + bloom filter blocks + footer), levels (L0 overlapping, L1+ non-
overlapping, size ratio ~10x), compaction (merge-join of overlapping runs:
leveled — merge into the next level; sized-tiered — merge same-size runs;
the read-amplification vs write-amplification vs space-amplification
triangle drawn as the chapter's central diagram with concrete numbers
per strategy). Read path: memtable → bloom filters → level probes (the
read-amp arithmetic: worst case = one probe per level), bloom filters
derived (m bits, k hashes: 10 bits/key ≈ 1% FP — the math shown, why
bigger filters live in the block cache). Compaction storms: background
debt → p99 latency spikes (the operational insider pain: the RocksDB
`compaction-stalled` metric), leveled compaction's write-amp tax vs
tiered's space-amp tax. The GC unification (the module's payoff):
memtable = nursery, levels = generations, compaction = mark-compact
(module 01 ch12's generational heap, one tower level down — dead
versions are "collected" by compaction), and the contrasting analogy:
B-trees = free-list allocator with update-in-place (the two module 01
dynasties, replayed on disk). When each wins: the decision table
(write-heavy/point-read/range-scan/space-amplification-tolerance).
Lab: RocksDB/LevelDB `sst_dump` walkthrough; bloom-filter FP rate measured
in 20 lines of Python; a writes-then-compaction timeline watched via the
DB's stats endpoint.

**Chapter 04 — WAL & crash recovery.** The core inversion: the log is the
truth, the pages are a cache that can be rebuilt (the insider's mantra —
derive it: pages are write-amplified and torn-prone, so make the *small,
sequential, atomic* log the record). LSN anatomy (monotone log sequence
numbers; page LSNs: each page stamps the LSN applied to it — WAL rule:
page flush only if its LSN ≤ the flushed log LSN — the write-barrier
discipline drawn as a diagram; the connection to module 10 ch04's write
barriers: *same concept, durability instead of visibility*). Physiological
logging (the InnoDB choice: physical page + logical operation — why pure
physical is bulky and pure logical can't replay onto a different page
layout). Redo vs undo: ARIES in one page (analysis: where was the crash
in the log; redo: repeat history *idempotently* — the physiological
design's replay guarantee; undo: roll back uncommitted transactions via
backward log traversal; the CLRs — compensation log records that make
undo itself crash-safe). The commit protocol: the commit record's flush
point = the moment the transaction becomes durable (group commit: many
transactions share one fsync — the latency-vs-throughput optimization
that is *the* database tuning lever; the pipelining variants). Checkpoints
(fuzzy: the checkpoint LSN bounds redo start; everything else is
derivable). Torn-page insurance: InnoDB's doublewrite buffer (write the
16KB page to a scratch area first — a partial page tear hits the scratch
copy, not the tree; the page is restorable from log + a clean copy —
connect ch01's torn-write physics). Lab: kill -9 a Postgres under load
and read the recovery log; measure group-commit batching under concurrency
(pg bench with sync_commit... fsync toggles); inspect InnoDB doublewrite
statistics.

**Chapter 05 — MVCC.** The problem: writers block readers (locks = the
 serialization tax) → give every reader a *snapshot* (readers see a
consistent point-in-time view, writers write — concurrency without
conflict; derive why "snapshot" is the right abstraction: it's COW at
row granularity — module 01 ch03's fork() copy-on-write, one scale
down). InnoDB concrete: row headers carry trx_id (last-modifying
transaction) + roll_pointer (into the undo log — the version chain:
current row → older versions; draw the chain), the read view (the
visibility oracle: the snapshot's list of active transactions; the
visibility rule for a row version: created-by-committed-before-snapshot
= visible, otherwise chase the roll pointer — the 5-step decision
sketch insiders recite). PostgreSQL concrete: tuple headers with
xmin/xmax (a delete stamps xmax — the "delete is a write" insight),
the visibility map, HOT updates. The hidden costs: version-chain
traversal (long-running readers = the enemy: purge cannot advance past
the oldest snapshot — the classic production incident: a forgotten
transaction pins the undo log, history bloats, disk fills — the
Postgres "long-running transaction" alarm every DBA sets), purge/ vacuum
= garbage collection (the insider's exact mapping: vacuum is a GC;
long transactions = GC roots; bloat = fragmentation). Isolation levels
as snapshot policy: RC (fresh snapshot per statement) vs RR (per
transaction), the anomaly menu (dirty read / non-repeatable read /
phantom / lost update / write skew — write skew derived with the
on-call-doctors example), serializable snapshots (Postgres SSI: the
rw-antidependency detection — dangerous structure detection in a
dependency graph — the insider implementation, sketched). Lab: open a
REPEATABLE READ transaction, modify rows in another, observe the
snapshot; inspect version chains (page-level or `txid_current`
experiments); reproduce write skew under RR and watch serializable
catch it.

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **Schema and query design with mechanism in mind**: covering indexes, hot
  updates, secondary-key pointer costs — decisions you can derive.
- **Production incident fluency**: bloat, purge lag, compaction stalls,
  group-commit latency — the tickets with memory-model roots.
- **The unified view**: allocators, GC, write barriers, COW — module 01's
  cast list, re-assembled on disk.
