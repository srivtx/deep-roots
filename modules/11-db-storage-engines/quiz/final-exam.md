# Module 11 final exam — database storage engines

Twelve questions across all five chapters. Closed book. Every answer is worked, not
just stated — the derivations are the point. Scoring: 11+ = you own this module;
8-10 = solid, revisit the flagged chapters; <8 = the chapter quizzes below each
chapter are the study path.

**Part I — the contract and the read-optimal tree (chapters 01-02)**

1. State precisely what `fsync()` guarantees on returning 0, two things it does
   *not* guarantee for a 16 KB page written just before it, and the hardware
   feature that makes two otherwise-identical SSDs differ ~10x in fsync latency.

2. A 40 GB table on an HDD doing ~100 random IOPS and ~200 MB/s sequential; 8 KB
   pages. Compute the full-scan time for sequential layout, for rows scattered
   on random pages, and the ratio; name the module 01 chapter it re-states.

3. A B+ tree: 16 KiB pages, 16-byte interior entries, leaves of 500 entries,
   guaranteed minimum fill 1/2. (a) Guaranteed maximum page reads
   for a point lookup at 10^9 entries? (b) The primary key becomes a
   36-byte random UUID — recompute the worst case, and name two costs beyond
   depth. (c) A balanced BST over the same keys: how many reads, and where does
   the gap physically come from?

4. InnoDB, `users(id BIGINT PRIMARY KEY, email, name, city)`, secondary index on
   `(email)`; query `SELECT name FROM users WHERE email = ?`. Count the full tree
   descents, say what changes with an index on `(email, name)`, and what breaks
   if secondary leaves stored physical record pointers instead of primary keys.

**Part II — the write-optimal tree (chapter 03)**

5. Derive the bloom-filter false-positive rate at 12 bits per key (optimal k),
   then at a halved 5 bits per key. With ~12 worst-case probes per absent-key
   point read, how many wasted data-block reads does each budget buy? Show the
   derivation, not a remembered number.

6. Write latency is fine for hours, then p99 sawtooths to 800 ms; during spikes
   `num-files-at-level0` = 9, `actual-delayed-write-rate` nonzero,
   `estimate-pending-compaction-bytes` growing. Diagnose to mechanism; name two
   knobs and what each trades.

7. Map the LSM onto module 01 ch12's generational heap (memtable, flush, L0/L1,
   deep levels, compaction, dead versions, tombstone), name one structural
   difference, and state what each ancestry — LSM vs the B-tree's free-list
   allocator — predicts about write-amp and space-amp.

**Part III — durability (chapter 04)**

8. Flushed LSN = 8,000; dirty pages needing eviction now: P7 (page LSN 6,200),
   P12 (7,850), P21 (8,500), P30 (11,900). Which flush now, which are blocked,
   in what order? Twenty transactions reach their commit point during the forced
   log flush — what do they get, at what cost?

9. InnoDB on ext4, 4 KB sectors, 16 KB pages, doublewrite ON. Power dies mid
   page-flush; after reboot, page 47's checksum fails. Which write tore, where is
   the intact copy, what does recovery do, and why is doublewrite
   folklore-disabled on ZFS?

10. Module 10 ch04's card-table barrier and the WAL rule are the same discipline.
    Name the two players and the ordering each enforces, and the one-word
    difference in what each protects.

**Part IV — concurrency (chapter 05)**

11. Read view: `low_water = 100`, `high_water = 121`, `active[] = {100, 118}`.
    Chain, newest → oldest: trx 118 (current, clustered index), trx 105, trx
    100, trx 80 (undo). (a) Which version does the reader
    return, by which steps? (b) T118 commits 1 ms later; the same
    REPEATABLE READ transaction repeats the query — what changes? (c) A different
    session, READ COMMITTED, queries after T118's commit with T100 still running
    — what does *it* return? (d) Did T118's writer ever wait for this reader?

12. PostgreSQL: disk climbs though the workload is 95% DELETEs; one
    `idle in transaction` pid with a 4-day-old `xact_start`; a hot table's scans
    are 3x slower; autovacuum never catches up. Give the mechanism chain from pid
    to symptom, and the fix.

<details>
<summary>Answers</summary>

1. On return 0, the fd's dirty data — and enough metadata to reach it — is on
   non-volatile media, pushed through the drive's own volatile cache. Not
   guaranteed: atomicity — the disk commits at sector granularity, so the 16 KB
   page is four 4 KB sector writes and any subset can land (a torn page); and
   scope — fsync covers only its own fd. The ~10x: power-loss-protection
   capacitors. An enterprise SSD acknowledges the flush from capacitor-backed
   cache (~0.1 ms); a consumer drive must program NAND first (~1-10 ms). Commit
   latency ≈ fsync latency (ch04), so capacitors are a commit-path feature in a
   storage spec sheet (ch01).

2. 40 GB / 8 KB ≈ 5.2M pages. Sequential: 200 MB/s ≈ 25,000 pages/s → ~205 s.
   Random: 100 pages/s → ~52,000 s ≈ 14.5 hours. Ratio ≈ 250x: random access
   priced per trip, sequential by bandwidth — module 01 ch02's "latency is not
   throughput," the pressure behind clustered leaves (ch02), sequential
   flushes (ch03), the append-only log (ch04). On SSDs the gap survives at
   ~10-60x via FTL erase-block GC, not the seek arm (ch01).

3. (a) Worst-case fanout F = (16,384 / 16) × 1/2 = 512; capacity(h) =
   F^(h-1) × 500. h = 3 → 512² × 500 ≈ 1.3 × 10^8, too small; h = 4 → 512³ × 500
   ≈ 6.7 × 10^10. **Guaranteed maximum: 4 page reads** — guarantees use minimum
   fill; the tree is never shallower than its emptiest pages promise. (b) The
   interior entry becomes ~44 bytes → F ≈ 186; h = 4 capacity ≈ 186³ × 500 ≈
   3.2 × 10^9 — still 4 reads, but headroom collapses from ~67x to ~3x, and past
   ~3 billion rows the tree gains a level. Beyond depth: the fat PK is duplicated
   into every secondary leaf (shrinking their fanout), and random keys force
   50-50 splits — ~69% fill, ~40% more bytes, no rightmost hot page (ch02).
   (c) log2(10^9) ≈ 30: comparisons are free in-cache next to a
   ~100 µs page read, one per level — the logarithm's base is the whole design
   (ch02).

4. (a) Two full descents: the email tree (its leaf holds (email, id)), then the
   clustered PK tree for the row. (b) `(email, name)` carries every queried
   column — name, email, and the PK riding free in every secondary leaf — so one
   descent; EXPLAIN shows `Using index`. (c) Rows physically move — splits,
   merges, compaction — so record pointers would dangle and need cascading
   repair on every structure modification; the PK is a stable logical locator.
   Tax: PK bytes in every secondary leaf, plus a second descent per non-covered
   lookup (ch02).

5. After n insertions a bit is still 0 with probability (1 − 1/m)^{kn} ≈
   e^{−kn/m}; a non-member passes only if all k probes hit set bits:
   FP ≈ (1 − e^{−kn/m})^k, minimized at k = ln 2 · m/n, where the filter is half
   full and FP ≈ 0.6185^(m/n). 12 bits/key: k = 0.693 × 12 ≈ 8, FP ≈
   0.6185^12 ≈ 0.3%. 5 bits/key: k ≈ 3, FP ≈ 0.6185^5 ≈ 9% — the rate is
   exponential in bits, so halving the budget is a ~30x worse FP. An absent key
   worst-case probes ~12 structures (2 memtables + 4 overlapping L0 files + 6
   levels): ~12 × 0.003 ≈ 0.04 wasted data-block reads at 12 bits vs ~12 × 0.09 ≈
   1.1 at 5 — point reads degrade from ~1-2 I/Os to ~2-3 for 0.9 bytes/key
   (ch03).

6. Leveled-compaction debt: ingest outran merge throughput; L0 piled past the
   slow trigger (8 files — per-write ~ms delays; stop at 12) while the debt
   metric grows. Writers are throttled deliberately — backpressure bounding
   read-amp, since each overlapping L0 run is another probe on every future
   read; the spikes are merges chewing debt, not lock contention. Knobs:
   `max_background_compactions`/compaction threads (merge throughput for CPU and
   I/O); bigger `write_buffer_size` (fewer, larger flushes); raised L0 triggers
   (write latency for read-amp); or universal/tiered compaction — write-amp 1-5x
   instead of 20-30x, paid at ~2x space-amp (ch03).

7. Memtable = eden/nursery; flush = minor-GC evacuation; L0/L1 = survivor
   spaces; ×10 level sizing = tenuring; deeper levels = old generation;
   compaction = mark-compact; dead versions = unreachable objects; tombstone = a
   deferred `free()`. Difference: liveness by *recency* (newest
   version wins — no tracing from roots), and the pause lands on writers
   while readers never stop. Ancestry predicts: the LSM is the copying/compacting
   collector — write-amp 20-30x (live data copied level by level), space-amp
   ~1.1x; the B-tree is the free-list allocator — update in place, low write-amp,
   perpetual fragmentation (69%-fill pages, splits, vacuum) as its space tax
   (ch03, ch02, module 01 ch12).

8. P7 and P12 flush immediately: page_LSN ≤ 8,000, their history is already
   durable. P21 and P30 are blocked by the WAL rule (page_LSN ≤ flushed_LSN):
   first force the log past 11,900 — one fsync unblocks both — then write the
   pages in either order. The rule keeps no page ahead of its explanation: had
   P30 landed before records 8,001-11,900 were durable, recovery would face a
   page with no surviving description — effect without record. The 20 committers
   attach to that in-flight fsync — group commit: 20 durable commits for one
   flush, each waiting about one fsync epoch; the log force was obligatory
   anyway, so they ride it nearly free (ch04).

9. The tear hit the *second* write — from doublewrite scratch (2 MB, 128 ×
   16 KB) to the tree. The first write (batch → scratch) was fsynced complete, so
   an intact copy sits in scratch. Recovery copies the clean image into place,
   then redo continues from that page's LSN. The log alone cannot fix it:
   physiological redo needs a valid starting page, and a half-old, half-new page
   never legally existed — ch01's sector atomicity, caught by the checksum. On
   ZFS/btrfs the filesystem never overwrites in place, so mid-page tearing
   cannot happen and doublewrite's 2x write-amp is pure waste there (ch04,
   ch01).

10. Card-table barrier: the mutating thread and the GC; ordering is *card
    dirtying before the pointer store becomes visible*, so the collector never
    reasons from a stale heap. WAL rule: the page flusher and crash recovery;
    ordering is *log durability before page durability* (page_LSN ≤
    flushed_LSN), so the recoverer never meets a page whose changes have no
    record. Same discipline — a happens-before edge forced at a store,
    protecting an asynchronous observer from effect-without-record. One word: the
    barrier protects **visibility**, the WAL rule **durability** (ch04, module
    10 ch04).

11. (a) trx 118: step 3 — in active[] → invisible; follow the roll pointer.
    trx 105: below high_water, above low_water, not in active[] → step 4:
    visible. **The reader returns trx 105's version**; trx 100 and 80 are never
    reached — 100's active status is a red herring. (b) Nothing: T118 committed
    after the snapshot was fixed, so it stays in the
    frozen active[] for this transaction's life — the frozen list is what
    "repeatable" means. (c) READ COMMITTED takes a fresh view per statement:
    active[] = {100}, so trx 118 passes step 4 — the RC reader returns trx 118's
    version. Same row, same instant, different answer: the isolation level *is*
    snapshot lifetime. (d) No — the writer's new version lives in bytes the
    reader never touches; the read view, not a lock, decided (ch05).

12. The idle transaction holds the oldest snapshot, and the oldest snapshot is
    the collector's root boundary: vacuum cannot purge past it (long transaction
    = GC root, vacuum = collector, bloat = fragmentation). A DELETE is *a write*
    — it stamps t_xmax and frees no bytes — so four days of dead tuples and dead
    index entries pile up behind the frozen boundary; history the root pins
    never truncates. Heap and indexes bloat, scans read 3x the pages, disk
    climbs, queries slow and pin more — the death spiral. Fix:
    `pg_terminate_backend(pid)`, then `idle_in_transaction_session_timeout` to
    prevent recurrence. InnoDB's version of the same alarm: a climbing
    `History list length` (ch05).

</details>
