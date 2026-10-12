# Chapter 04 — WAL & crash recovery

> **The 30-second version.** A database must survive a crash with every
> acknowledged commit intact and every half-finished transaction gone. The
> pages are big, random, torn-prone; the log is small, sequential,
> append-only. The fix is an inversion: make the **log the truth**, demote
> the **pages to a rebuildable cache**. Changes are logged and made durable
> first; pages update lazily, under one rule — a page may not reach disk
> before the log records describing it do: a *write barrier* (module 10
> ch04) in a durability uniform. Recovery replays forward (redo), then
> erases backward what never committed (undo); group commit — many
> transactions sharing one fsync — makes it cheap.

## The inversion: the log is the truth, the pages are a cache

One transaction touches scattered pages: heap, two B-tree leaves, metadata.
Chapter 01's physics: the disk's atomicity unit is the *sector* (512 B–4
KB), not your page (PostgreSQL 8 KB, InnoDB 16 KB) — a mid-transaction
crash leaves a state that never legally existed: row updated, index not.
Shadow paging — copy every touched page, flip one root pointer: COW at
database scale (module 01 ch03) — lost to ~2x write amplification and
shattered locality. Production lineage chose **write-ahead logging**:
record the *description* of each change in an append-only log, make *that*
durable, let the pages lag.

Derive why the log wins: "Insert row (5, 'bob') into page 47" is tens of
bytes; the pages it dirties are 8–16 KB each. The log is sequential — the
one place a disk streams (ch01: ~200 MB/s sequential vs 100–200 IOPS random
on HDD) — and *sector-atomic*: a record that fits in one sector cannot
half-land the way a 16 KB page over 4 KB sectors can. The cheapest durable
artifact is the description, not the data. Hence the insider's mantra:

> **The log is the truth; the pages are a cache of the log's consequences.**

A lost dirty page costs nothing — redo re-applies the log to it. A lost
log record breaks a promise already reported to a user. The phrase traces
to Gray & Reuter's *Transaction Processing* (1993); the machinery is
Mohan's **ARIES** (IBM, 1992), below.

## LSNs and the WAL rule: a write barrier for durability

Ordering comes from a **log sequence number (LSN)** — a monotone value
stamped on every record: InnoDB (MySQL 8.0) an 8-byte byte-offset into the
redo stream; PostgreSQL 16 a 64-bit `XLogRecPtr`, printed as hex like
`0/16B3748`. Two more LSNs close the loop:

- **Page LSN** — each page header stamps the LSN of the last record
  applied to it (PostgreSQL `pd_lsn`, 8 bytes at page offset 0; InnoDB
  `FIL_PAGE_LSN`, 8 bytes at offset 16 of the 16 KB page).
- **Flushed LSN** — how far the log is actually durable (written +
  fsynced).

Then the **WAL rule**:

> A page may be flushed to disk only if `page_LSN ≤ flushed_LSN`.

```text
  log (durable through LSN 4000)         buffer pool (RAM)           disk (a cache!)
  +------+------ ... -----+------+       +----------------------+
  | 3800 |   ...          | 4000 | <--   | page 12   LSN 3600   |  dirty, evict me
  +------+------ ... -----+------+ last  +----------------------+
                                flushed  | page 47   LSN 5200   |  dirty, evict me
                                         +----------------------+
  WAL rule:  flush(P)  <=>  page_LSN(P) <= flushed_LSN

  page 12:  3600 <= 4000  ->  OK; its whole history is already durable.
  page 47:  5200 >  4000  ->  BLOCKED: first force the log (write + fsync
                              records 4000..5200), THEN flush page 47.
  If page 47 landed first and the machine died: a page whose only
  record is gone -> effect with no explanation, unrecoverable.
```

Note the shape. Module 10 ch04's card-table **write barrier**: a pointer
store may not become *visible* to the GC before the card is dirtied, or
the collector reasons from a stale world. The WAL rule: a page flush may
not become *durable* before its log records are, or the recoverer faces a
page with no explanation. Same concept — a happens-before edge at a store,
with **durability** standing in for **visibility**, protecting an
asynchronous reader from effect-without-record.

## Physiological logging: the record format that makes replay possible

What does one record say? Three spots on a spectrum:

- **Pure logical** — "INSERT INTO users VALUES (5,'bob')". Tiny, but replay
  needs the whole query engine and must work on whatever page layout
  exists at recovery time — whether an index split has been replayed yet
  changes where the row goes: it re-derives location, so mid-recovery
  state makes it fragile.
- **Pure physical** — "here is the complete 16 KB after-image of page 47".
  Replay is trivially correct (overwrite; idempotent by construction),
  but the win is gone: 100–500x more bytes per change — the write
  amplification you fled returns as log volume.
- **Physiological** — InnoDB's choice (Mohan's coinage): *physical page
  identification + logical operation on that page*: "apply to page 47:
  insert record (5,'bob')". The page number stays stable across splits
  (chapter 02), and the operation lands in the page's own structures —
  valid at replay time *because replay repeats history in order*: each
  page is in exactly the state the record expects.

The design also buys what recovery leans on: **idempotent redo**. The guard
is the page LSN: applying a redo record with LSN `L` first checks
`page_LSN(P) ≥ L`; if true, *skip*. The requirement: every redo operation
*repeatable* — no "increment a counter", no absolute offsets; each record
self-contained, landing identically twice. (PostgreSQL 16 logs nearer the
physical end and insures torn pages differently, below.)

## Redo and undo: ARIES in one page

Crash: the fsync'd log prefix is intact, the buffer pool is gone. **ARIES**
(*Algorithms for Recovery and Isolation Exploiting Semantics*, Mohan et
al., ACM TODS 1992) runs three passes:

```text
  crash!  log timeline (LSNs grow rightward),  * = commit record
  --| T1: BEGIN UPDATE UPDATE COMMIT* | T2: BEGIN UPDATE UPDATE | T3: BEGIN UPDATE |--
    ^checkpoint                         ^last flushed LSN (durable to here)

  1. ANALYSIS  from checkpoint: rebuild who was running (losers = T2, T3)
               and which pages were dirty (the flush list).
  2. REDO      from checkpoint-LSN forward, apply EVERYTHING, including
               T2/T3's uncommitted edits ("repeat history"): physiological
               replay must proceed in exact order to keep pages
               structurally consistent; the page-LSN check keeps it
               idempotent.
  3. UNDO      walk BACKWARD from the log's end: for each loser apply the
               inverse of each update, logging it as a CLR (Compensation
               Log Record) -- redo-only, undoNext-linked, so a crash
               DURING undo resumes where it left off: undo is crash-safe.
```

Redo repeats history *including work that will be undone* — the price of
in-order, structurally sound replay, and cheap: sequential I/O. Undo is
itself logged — without CLRs, a crash during recovery could loop forever.
PostgreSQL's twist: no undo — aborted tuples stay on disk, masked by
snapshots, collected by VACUUM (chapter 05); InnoDB erases during
recovery, PostgreSQL masks and garbage-collects.

## The commit point: one fsync, many transactions

A transaction is durably committed when the **log flush covering its
commit record completes** — not at its last page edit, not when the record
reaches the in-memory log buffer. Before that flush, "committed" is a
rumor; after it, redo resurrects the transaction.

So commit latency ≈ fsync latency: consumer NVMe ~1–2 ms; an enterprise
SSD with power-loss-protection capacitors ~50–100 µs (ch01's 10x insider
gap). One naive client gets `1/fsync_time` commits/s — a 10 ms fsync is a
**100 commits/s ceiling**, CPU speed irrelevant.

**Group commit** breaks it: N transactions reaching the commit point while
one fsync is in flight all attach to it — one flush, N durable commits.

- Throughput: `N/fsync_time` — 8 concurrent committers on that 10 ms disk
  = 800/s.
- Latency: each still waits ~one fsync epoch — you amortize the only
  expensive operation on the commit path.

The implementations are pipelines: a leader takes the log mutex, copies
its tail plus waiters' commit records into one write, releases the mutex,
then holds only the *fsync mutex* while the flush runs — writers keep
appending and the next batch queues (PostgreSQL's commit-flush queue;
InnoDB's log-flush leader, same dance). The knobs, with their loss
windows:

- `synchronous_commit = off` (PostgreSQL 16; default `on`): return before
  the flush — the WAL writer flushes every `wal_writer_delay` (200 ms
  default), bounding loss to ~3x that; you lose durability latency, never
  consistency.
- `innodb_flush_log_at_trx_commit = 2` (MySQL 8.0; default `1`): write
  per commit, fsync ~1/s — survives a mysqld crash (the OS cache holds
  the bytes), loses ≤ 1 s of commits on power loss.

Batch geometry + fsync policy is *the* database tuning lever: it decides
commit throughput before any query-level optimization matters.

## Checkpoints and torn-page insurance

**Checkpoints bound recovery.** Without them, redo starts at LSN 0 forever
and the log never truncates. A checkpoint asserts: every page dirty as of
LSN C is on disk — redo starts at C, log before C is recyclable. Real
checkpoints are *fuzzy*: InnoDB (MySQL 8.0) flushes the dirty list in
the background while writes continue and advances the checkpoint when
pre-C stragglers drain, force-flushing when redo capacity
(`innodb_redo_log_capacity`, default 100 MB) runs low.
PostgreSQL 16 checkpoints every `checkpoint_timeout` (5 min default) or
`max_wal_size`, spreading writes via `checkpoint_completion_target`
(0.9). Crash-recovery time is proportional to log bytes since the last
checkpoint — the "restart took 20 minutes" ticket is a redo-distance
question.

**Torn pages are the residual hole.** The WAL rule guarantees the log
*explains* every page — but physiological redo applies operations to a
page whose starting state must be valid. Chapter 01: a 16 KB page over
4 KB sectors can half-land; half-old/half-new never legally existed, its
checksum fails, and replaying diffs cannot reconstruct what was computed
against a whole page. Two insurance designs:

- **PostgreSQL**: `full_page_writes = on` (default) — the first
  modification of each page after a checkpoint logs the *entire page
  image* into the WAL, so a torn page is replaceable wholesale from the
  log. Cost: WAL bloat while pages dirtied since the checkpoint go
  unwritten.
- **InnoDB**: the **doublewrite buffer** — 2 MB scratch (128 × 16 KB
  pages, MySQL 8.0 default `ON`). Flush path: write pages to scratch,
  fsync, then write to their real tree locations. A tear now hits the
  *second* write; the scratch copy is intact, so recovery copies scratch
  → tree and applies redo. Cost: every flushed page written twice —
  page-flush write amplification 2x, cheap because the scratch writes
  are sequential. On CoW filesystems (ZFS, btrfs) pages are never
  overwritten in place, so doublewrite is redundant there and commonly
  disabled.

## What insiders know

- **The mantra's lineage**: "the log is the truth" traces to Gray & Reuter
  (1993); the machinery is Mohan's ARIES (IBM, 1992) — "repeat history"
  is literally ARIES' slogan. The losing alternative, shadow paging, was
  COW at database scale, dead within a prototype.
- **Why physiological, in one sentence**: pure physical pays 16 KB per
  change; pure logical can't find its target when page layouts shift
  mid-recovery; "page + operation" pins the target and keeps the record
  100–500x smaller than the pages it describes.
- **Group-commit tuning is a hardware question**: a power-loss-protected
  SSD makes fsync ~50–100 µs, collapsing the case for
  `synchronous_commit=off` / `flush_log_at_trx_commit=2`; consumer NVMe
  (~1–2 ms fsync) trades a bounded loss window (200 ms–1 s) for
  multiples of throughput — audit *which* SSD before touching the knob.
- **Doublewrite's real cost**: 2x on page-flush bandwidth (sequential, so
  ~2 MB per batch), zero on the commit path — pure waste on CoW
  filesystems, the classic "disable it on ZFS" folklore. PostgreSQL's
  `full_page_writes` is the same insurance paid in WAL volume — visible
  as WAL spikes right after every checkpoint.
- **Idempotent redo's hard requirement**: every redo op repeatable, with
  the page LSN as dedup guard (`skip if page_LSN ≥ record_LSN`). A redo
  op with side conditions ("only if slot empty") or non-repeatable
  effects (counters, absolute offsets) silently breaks
  crash-during-recovery.

## See it yourself

No PostgreSQL here (no `psql`/`pgbench`); the labs use SQLite 3.53.1
(Python 3.12.15) on Linux 5.10. With PG 16 at home: `pgbench -c 8 -T 60
bench`, `kill -9` the postmaster, restart, and the server log reads
`database system was not properly shut down; automatic recovery in
progress`, `redo starts at 0/...`, `redo done at 0/...`.

**Lab 1 — kill -9 a database mid-transaction (WAL mode).**

```bash
cd /tmp/wallab && cat > crasher.py <<'EOF'
import sqlite3, time
db = sqlite3.connect("crash.db")
db.execute("PRAGMA journal_mode=WAL")        # redo-style: log is the truth
db.execute("PRAGMA synchronous=FULL")        # fsync at every commit point
db.execute("BEGIN")                          # committed transaction
db.execute("CREATE TABLE t(i INTEGER PRIMARY KEY, v TEXT)")
db.executemany("INSERT INTO t VALUES(?,?)", [(i, "x"*40) for i in range(500)])
db.commit()
db.execute("BEGIN IMMEDIATE")                # doomed: never commits
db.executemany("INSERT INTO t VALUES(?,?)", [(i, "y"*40) for i in range(500, 800)])
print("300 uncommitted rows; waiting for SIGKILL", flush=True)
time.sleep(30)
EOF
python3 crasher.py & sleep 2; kill -9 %1; ls -la crash.db*
```

```text
-rw-rw-r--  1 z z   4096  crash.db        <- main file: ONE page. The cache.
-rw-rw-r--  1 z z  41232  crash.db-wal    <- the log: 10x bigger. The truth.
-rw-rw-r--  1 z z  32768  crash.db-shm
```

```bash
python3 -c "import sqlite3; print(sqlite3.connect('file:crash.db?mode=ro', uri=True)
             .execute('SELECT count(*), max(i) FROM t').fetchone())"
# (500, 499)  -- committed rows resurrected; 0 of the 300 doomed ones
```

The sizes teach the lesson: the main file holds one 4 KB page, the WAL
41 KB. `count = 500` on read-only reopen is commit-frame visibility — the
300 doomed rows sit beyond the last commit frame (no commit record, no
durability); a later `PRAGMA wal_checkpoint(PASSIVE)` returns
`(0, 10, 10)`, pages catching up.

**Lab 2 — the same crash, undo-style** (`journal_mode=DELETE`, 1,000
uncommitted rows, `kill -9`): a hot `rj.db-journal` of before-images
(8,720 B) is replayed on reopen — the table is empty. WAL replays
committed work forward; the rollback journal erases uncommitted work
backward.

**Lab 3 — commit latency, 1 vs 8 clients.** Eight threads, 200
`BEGIN IMMEDIATE`/insert/`COMMIT` each, separate connections (SQLite
3.53.1, same sandbox):

```text
sync=FULL   clients=1  mean commit 0.008 ms  p99 0.025 ms  max 0.050 ms
sync=FULL   clients=8  mean commit 0.093 ms  p99 0.082 ms  max 54.318 ms
sync=NORMAL clients=1  mean commit 0.007 ms  p99 0.025 ms  max 0.048 ms
sync=NORMAL clients=8  mean commit 0.069 ms  p99 0.029 ms  max 33.488 ms
```

Platform honesty: this sandbox's overlay filesystem reports a 0.0003 ms
fsync (the host absorbs it), so the absolute numbers are a floor. What
survives: concurrency without batching is still a convoy — writer
serialization plus real flushes yields the 54 ms tail at 8 clients. On a
real 10 ms-fsync disk, naive commits top out at 100/s *total* regardless
of client count; group commit at 8 committers delivers 800/s.

## How to use this knowledge

- **Commit-latency tickets**: before blaming the query, locate the commit
  fsync — hardware class (PLP or not), knob (`synchronous_commit`,
  `innodb_flush_log_at_trx_commit`), offered concurrency (is there even a
  batch to group?).
- **"Restart took 20 minutes"**: that is redo distance — log bytes since
  the last checkpoint. Check checkpoint frequency (`checkpoint_timeout`,
  `max_wal_size`, `innodb_redo_log_capacity`) before suspecting hardware.
- **Corruption incidents**: InnoDB "database page corruption" + checksum
  error after power loss means torn page + bypassed insurance
  (doublewrite or `full_page_writes` off, or lying hardware). Restore
  path: clean copy + log apply.
- **Rolling your own durability** (job queues, ledgers): append-only
  records smaller than a sector, fsync as the commit point, idempotent
  replay guarded by per-entity sequence stamps — your page-LSNs.

## Connections

- **Module 01 ch03 (COW/virtual memory)** — shadow paging was fork()'s COW
  at database scale; its defeat by the log is why "append a description"
  beats "copy the world."
- **Module 10 ch04 (write barriers)** — card-table barrier and WAL rule
  are the same discipline at a store: effect never observable before its
  enabling record; visibility there, durability here.
- **Module 11 ch01 (the disk contract)** — sector atomicity, fsync
  latency classes, torn-write physics: the premises every section cashed
  in.
- **Module 11 ch05 (MVCC)** — the undo log doubles as the version store;
  roll pointers are backward log traversal at row granularity, and
  PostgreSQL's "mask, don't undo" recovery is its snapshot machinery
  reused.
- **Module 09 ch05 (consensus)** — Raft is a replicated WAL: log-as-truth
  at fleet scale, state machine as cache, commit index as flushed-LSN.

## Quiz

1. After a crash, all data pages are intact, but the last two log records
   (still in the OS page cache) were lost — and one transaction had
   already acknowledged its commit. What category of failure is this, and
   why is it worse than losing every dirty page?
2. A teammate proposes redo records meaning "increment the row count in
   page 47's header by 1." A crash happens mid-recovery and some records
   replay twice. What property is violated, and what is the standard
   guard?
3. Page 12 has page LSN 3,600; page 47 has page LSN 5,200; the flushed
   LSN is 4,000. Both must be evicted now. What happens, in what order,
   and why does the order matter?
4. An fsync takes 10 ms; eight clients commit continuously. (a) Naive
   one-fsync-per-commit: system-wide throughput ceiling? (b) Perfect group
   commit: throughput and per-commit latency? (c) What does (a) say about
   concurrency without batching?
5. MySQL 8.0 runs with `innodb_flush_log_at_trx_commit = 2`. The mysqld
   process is `kill -9`ed; users report committed transactions vanished.
   Is the knob a sufficient explanation, or do you keep investigating?
6. InnoDB with doublewrite active on ext4 (4 KB sectors, 16 KB pages).
   Power dies during a page flush; after reboot the page's checksum
   fails. Which write tore, where is the intact copy, and what does
   recovery do?
7. Disk A writes atomically in 512 B sectors; disk B in 4 KB sectors
   (chapter 01). Both host 16 KB pages. Which produces torn pages, which
   needs doublewrite/full-page-writes, and which makes small log records
   atomic for free?
8. Module 10 ch04's card-table barrier forces "dirty the card before the
   pointer store becomes visible to the collector." Name the two players
   and the ordering for that barrier and for the WAL rule, then state the
   one-word difference in what each protects.

<details>
<summary>Answers</summary>

1. **A durability violation — the only unforgivable kind.** The promise
   was "commit record in the durable log"; losing log records breaks a
   promise already reported to the user. Dirty pages are a cache — redo
   rebuilds them for free. Likely causes: commit acknowledged before
   fsync reached stable storage (`innodb_flush_log_at_trx_commit = 2`
   plus an OS crash), or hardware lying about fsync.
2. **Idempotent redo is violated**: applying the record twice
   double-counts. The guard is the page LSN: before applying a record
   with LSN L, recovery checks `page_LSN(P) ≥ L` and skips if the page
   already absorbed it. That works only because physiological operations
   are repeatable — "set field to value" is; "increment" is not.
3. Page 12 flushes immediately: `3,600 ≤ 4,000`, its history already
   durable. Page 47 is blocked — first force the log (write + fsync) to
   ≥ 5,200, then flush. If page 47 hit disk first, a crash would leave a
   page whose changes have no surviving description: effect without
   record, exactly what the rule forbids.
4. (a) `1/10 ms` = **100 commits/s total** — clients serialize behind
   the fsync; eight clients are a queue. (b) All eight attach to one
   flush: **800 commits/s**, each ~10 ms. (c) Without batching,
   concurrency buys nothing on the fsync path; with group commit it
   converts linearly into throughput — the commit-path lever, not a
   micro-optimization.
5. **Keep investigating.** Value 2 writes the log to the OS cache per
   commit and fsyncs about once per second; `kill -9` doesn't destroy the
   kernel's copy, so the next startup reads it fine. Vanished commits
   after a *process* kill point elsewhere: a different knob in effect, or
   lying fsync. Power loss — not process loss — is where the ~1 s window
   appears.
6. The tear hit the **second write** — from doublewrite scratch (2 MB,
   128 × 16 KB) to the tree. The first write (batch → scratch + fsync)
   completed, so an intact copy sits in scratch. Recovery copies the
   clean 16 KB image into place, then continues redo from that page's
   LSN. The log alone can't fix it: physiological redo needs a valid
   starting page, and a torn page is a state that never existed.
7. **Both can tear a 16 KB page** — any atomicity unit below the page
   allows it — so both need insurance. Disk B (4 KB sectors) tears in
   chunkier shapes: 0, 4, 8, or 12 KB of 16 lands, the rest doesn't.
   Disk A (512 B) offers 32 tear points but makes a small sector-sized
   *log record* atomic for free — section 1's sector-atomicity argument.
   The real escape is a CoW filesystem, where pages are never overwritten
   in place.
8. Card-table barrier: the **mutating thread** and the **GC**; ordering
   is *card dirtying before pointer-store visibility*, protecting the
   collector's heap view. WAL rule: the **page flusher** and **crash
   recovery**; ordering is *log durability before page durability*,
   protecting the recoverer's page explanations. One word:
   **visibility** vs **durability** — the same edge, different plane.

</details>
