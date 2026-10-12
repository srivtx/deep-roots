# Chapter 05 — MVCC

> **The 30-second version.** Locks put readers and writers in one queue, and that queue is the throughput ceiling. MVCC's move: never let a reader look at the bytes a writer is changing — hand every reader a *snapshot*, a consistent cut of the database at one instant, while writers create new row versions elsewhere. A row stops being a place and becomes a *chain of versions* plus a rule for choosing one; the rule is a short list of transaction ids. That is copy-on-write (Module 01, Chapter 03) at row granularity. The cost: dead versions pile up until a collector frees them, and the collector cannot pass the oldest snapshot — vacuum is a garbage collector, long transactions its roots. Isolation levels are snapshot policy.

## Locks are a serialization tax

Chapter 02's latches live for microseconds per page; two-phase locking holds *transaction* locks for the transaction's whole life — correct but brutal. A 50 ms analytical scan pins shared row locks for 50 ms, so every writer to those rows queues; one uncommitted writer makes every reader wait for commit. On a 16 KB InnoDB page holding ~100 rows, one hot row's writer can block readers of 99 innocent neighbors. Drop the locks and you buy speed plus garbage: dirty reads, non-repeatable reads, phantoms, lost updates.

The fix writes itself: readers and writers conflict only because they touch the *same physical bytes*. Give each write its own bytes — a new version of the row rather than an in-place mutation — and the conflict evaporates. Readers never block writers; writers never block readers (new versions stay invisible until the rules say otherwise). Only writer-vs-writer overlap remains, checked at write time, not by queuing.

## The snapshot is copy-on-write, one scale down

`fork()` (Module 01, Chapter 03) solved this at page granularity: parent and child share every physical frame through PTEs marked read-only + copy-on-write; the child's write faults, the kernel copies one page, the parent never notices — what you see is decided by *your page table*, not by the frame. MVCC is the same design shrunk to rows:

- physical frame ↔ row version (index page or undo log)
- RO + COW PTE bits ↔ the version's header: which transaction created it
- write fault, page copied ↔ update writes a new version
- a process's page table ↔ a transaction's snapshot (**read view**)
- frame's refcount hits 0 ↔ no view reaches the version, purge frees it

A snapshot is not a copy of the data — that would be fork's naive cost. It is the row-scale page table: a *ruler* that says which versions are yours to see. "Snapshot" is the right abstraction because it is COW's sharing rule, one level down.

## InnoDB: the version chain and the read view

InnoDB (MySQL 8.0) stores rows in the clustered index (Chapter 02); every record carries a hidden header with `DB_TRX_ID` (6 bytes, the last transaction to write this row) and `DB_ROLL_PTR` (7 bytes, a pointer into the **undo log**). An `UPDATE` overwrites the row *in place* in the 16 KB B-tree page — the update-in-place dynasty — after copying the old version into the undo log (Chapter 04's rollback records, re-used as history). Old versions hang backward in time via roll pointers; the newest version, committed or not, lives in the index itself.

Each reader takes a **read view** — the visibility oracle, who was running at snapshot time: `low_water` (every id below it committed before the snapshot), `high_water` (the next id not yet assigned), `active[]` (ids running at that instant). Insiders recite the 5-step decision for a version created by transaction `T`:

1. `T < low_water` → **visible** (committed before the snapshot).
2. `T >= high_water` → invisible (not born yet).
3. `T in active[]` → invisible (was mid-flight).
4. Otherwise → **visible** (started and finished before the snapshot).
5. If invisible: follow `roll_ptr` to the older version; repeat from step 1.

```text
read view fixed at snapshot time S:
low_water = 100    high_water = 121    active[] = { 100, 118 }

  clustered-index row (B-tree leaf)            undo log (rollback segment)
+-----------------------------------+
| bal = 500                         |
| trx_id = 100   -> INVISIBLE       |  step 3: 100 in active[] -- the writer
| roll_ptr --------------------------+->  still running; its dirty version
+-----------------------------------+   is parked in the index itself
              |
              v
+-----------------------------------+
| bal = 400                         |
| trx_id = 118   -> INVISIBLE       |  step 3: 118 in active[] -- commits,
| roll_ptr --------------------------+->  but AFTER S; too late for this
+-----------------------------------+   snapshot
              |
              v
+-----------------------------------+
| bal = 300                         |
| trx_id = 80    -> VISIBLE         |  step 1: 80 < low_water. READ THIS.
| roll_ptr -> older dead versions   |  purge may reclaim nothing that this
|             (not drawn)           |  or any older snapshot still needs.
+-----------------------------------+
```

The reader returns `bal = 300` although two newer physical versions exist — no lock taken, T100 never blocked.

## PostgreSQL: xmin, xmax, and "delete is a write"

PostgreSQL 16 is the append-only sibling: it never updates in place. Every version is a heap tuple with a 23-byte header (before alignment and null bitmap) whose key fields are `t_xmin` (4 bytes, inserting transaction) and `t_xmax` (4 bytes, deleting transaction; 0 = alive). An `UPDATE` appends a new tuple with `xmin = me` and stamps the old tuple's `xmax = me`; the versions chain via the old tuple's `t_ctid`, and a version is visible to snapshot S exactly when its `xmin` committed before S and its `xmax` did not. A `DELETE` is *just the stamp* — a delete is a write — and the dead tuple stays in its 8 KB page until VACUUM collects it.

Three structures keep this cheap. The **commit log (clog)** holds the verdict — 2 bits per transaction id — since `xmin` says *who* wrote, not whether it committed. The **visibility map** (2 bits per heap page since 9.6: all-visible, all-frozen) lets index-only scans skip the heap: only pages no live transaction can be mid-write on qualify. **HOT updates** attack the update tax: if no indexed column changes and the new version fits on the same page, indexes keep pointing at the old TID and the chain continues through `t_ctid` — otherwise *every* index gains an entry per update, the write-amplification behind "index bloat."

## The hidden costs: purge is garbage collection

Chains grow monotonically; nothing reclaims a dead version while any snapshot can still walk to it. InnoDB's purge threads (default 4 in MySQL 8.0) and PostgreSQL's autovacuum collect versions no live read view can reach. The GC mapping is exact, not poetic:

- **Long transactions = GC roots.** The oldest active snapshot is the root boundary; everything reachable from it is pinned.
- **Vacuum/purge = the collector** — mark-sweep over dead tuples in Postgres, truncation of undo history in InnoDB (`History list length` in `SHOW ENGINE INNODB STATUS`).
- **Bloat = fragmentation.** Postgres cannot shrink a page in place; dead tuples leave holes, scans lengthen, heap and indexes bloat.

The classic production incident: an abandoned psql session (an app that `BEGIN`s then idles, a stuck replication slot) keeps a 3-day-old snapshot alive; vacuum freezes at that boundary; history bloats; the disk fills; the oldest queries get slower, run longer, pin more — a death spiral. The long-transaction alarm every DBA sets:

```sql
-- PostgreSQL 16: the query that pays for itself
SELECT pid, state, xact_start, now() - xact_start AS age
FROM pg_stat_activity
WHERE xact_start < now() - interval '5 minutes';
```

## Isolation levels are snapshot policy

Once snapshots exist, the isolation knob is *when the read view is taken*:

| level | snapshot policy | what still goes wrong |
|---|---|---|
| READ COMMITTED | fresh view per **statement** | non-repeatable read, phantom, lost update, write skew |
| REPEATABLE READ | one view per **transaction** | write skew only |
| SERIALIZABLE | RR + conflict detection | nothing |

Postgres defaults to READ COMMITTED, MySQL/InnoDB to REPEATABLE READ. The anomaly menu: **dirty read** — impossible at every level here, since in-flight versions fail the visibility rule; **non-repeatable read** — RC only, the per-statement view moved; **phantom** — RC only in Postgres, whose RR answers range queries from the frozen past, while InnoDB's RR blocks phantoms with *next-key locks* (gap locking over Chapter 02's within-page record chains), not snapshots; **lost update** — RR aborts the later writer; and **write skew** — the hole RR leaves open.

Derive write skew with the on-call example (Fekete et al., SIGMOD 2005): at least one of doctors Alice and Bob must stay on call. Both leave, each in an RR transaction:

```text
T_A: BEGIN; SELECT sum(on_duty) -> 2   -- "Bob is on call, I can leave"
T_B: BEGIN; SELECT sum(on_duty) -> 2   -- "Alice is on call, I can leave"
T_A: UPDATE ... alice off; COMMIT;     -- writes row 'alice'
T_B: UPDATE ... bob off;   COMMIT;     -- writes row 'bob'
final: sum(on_duty) = 0                -- invariant gone
```

Each transaction *read* both rows but *wrote* a disjoint one. Snapshot isolation rejects only write-write overlap, so both commit. In any serial order the second doctor would have read `sum = 1` and stayed. RR's verdict: consistent, repeatable, wrong.

PostgreSQL SERIALIZABLE (SSI, since 9.1, from Cahill's thesis) fixes this without resurrecting locks. Committed transactions plus dependencies form a directed graph: w→w, w→r, and the crucial **rw-antidependency** — T_A read data that T_B later wrote, the doctors' shape. A serialization order exists iff the graph is acyclic, and any cycle under snapshot isolation must contain two *consecutive* rw edges. So SSI never hunts full cycles: SIREAD locks (who-read-what, summarized in shared memory) flag **dangerous structures** — `rw → pivot → rw` — and one of the three aborts with "could not serialize access due to read/write dependencies among transactions." InnoDB instead reaches serializable the old way — RR plus shared locks on reads: 2PL.

## What insiders know

- **The long-transaction alarm is the first thing a DBA wires up**: Postgres `pg_stat_activity` on `idle in transaction` (the query above); InnoDB `SHOW ENGINE INNODB STATUS` watched for a climbing `History list length`. A forgotten transaction is a 3-day GC root.
- **Vacuum is a GC — memorize the mapping**: long transactions = GC roots, dead tuples = garbage, bloat = fragmentation, autovacuum = background collector, `VACUUM FULL` = stop-the-world mark-compact.
- **RC vs RR is snapshot lifetime, and it changes locking.** InnoDB READ COMMITTED builds a read view per *statement*: a transaction can see rows mid-flight, and gap locks largely vanish, cutting lock waits.
- **Write skew's canonical shape** — overlapping reads, disjoint writes, both commit under RR (the on-call doctors). Every check-then-act invariant ("enough stock", "at most one owner") is a write-skew bug unless the level is SERIALIZABLE or the check reads `SELECT ... FOR UPDATE`.
- **SSI's dangerous structure is two consecutive rw-antidependencies through a pivot**, not a full cycle; one of the three aborts with a 40001-class error — the app's retry loop is part of the protocol.
- **InnoDB's read-view field names are a trap**: the low water mark is called `up_limit_id` and the high water mark `low_limit_id` — inverted in the source. Quote the algorithm, not the names.

## See it yourself

No server needed: SQLite's WAL mode hands every reader a snapshot (page-granular, not row-granular — same semantics). Outputs are real: SQLite 3.53.1 via Python 3.12.15, run on this machine. PostgreSQL 16 fragments are marked *documented* — no server here.

**Lab 1 — freeze a snapshot, write behind its back.** Two connections, WAL mode:

```python
A = sqlite3.connect("snap.db", isolation_level=None)   # we drive BEGIN/COMMIT
B = sqlite3.connect("snap.db", isolation_level=None)
A.execute("PRAGMA journal_mode=WAL")       # + CREATE TABLE accounts; 2 rows, bal=100
A.execute("BEGIN")                         # deferred txn: view fixed at first read
A.execute("SELECT sum(bal) FROM accounts")            # -> 200
# session B, twice: BEGIN; UPDATE ... SET bal = bal - 50; COMMIT
A.execute("SELECT sum(bal) FROM accounts")            # -> 200 still
A.execute("COMMIT")
A.execute("SELECT sum(bal) FROM accounts")            # -> 100
```

The frozen 200 is REPEATABLE READ: one read view per transaction.

**Lab 2 — write into a stale snapshot.** A opens a transaction and reads; B commits an update; A tries to write:

```text
A  BEGIN; data_version = 2
B  UPDATE t SET x=1; COMMIT;       (wal: 16512 B = 32-B header + 4 frames)
A  UPDATE t SET x=x+10 ... -> OperationalError: database is locked
    sqlite_errorcode = 517  sqlite_errorname = SQLITE_BUSY_SNAPSHOT
PRAGMA wal_checkpoint(PASSIVE) -> (busy=0, log_pages=4, checkpointed=4)
```

SQLite refuses the upgrade rather than risk a lost update — 517, "your snapshot is stale." `PRAGMA data_version` read 2 inside A's transaction, 3 after — the frozen view, measured. The checkpoint is a mini-purge: it folds WAL frames (new page versions) back into the main file once no reader needs the old ones. VACUUM shows the GC face (4 KB pages, 2000 rows × ~2 KB):

```text
page_count after insert:        2007  freelist_count: 0
page_count after DELETE half:   2007  freelist_count: 1001
page_count after VACUUM:        1004  freelist_count: 0
```

DELETE freed nothing — 1001 dead pages wait for VACUUM to compact. Garbage retention, like a heap between collections.

**Lab 3 — write skew, reproduced.** The doctors as a two-counter race: table `oncall(doc TEXT PRIMARY KEY, on_duty INTEGER)`, rows `('alice', 1)` and `('bob', 1)`, invariant "at least one on duty". Read-then-write with *no* transaction:

```python
# A and B both read: SELECT sum(on_duty) FROM oncall   ->  2 ("someone else is on call")
A.execute("UPDATE oncall SET on_duty = 0 WHERE doc = 'alice'")   # autocommit
B.execute("UPDATE oncall SET on_duty = 0 WHERE doc = 'bob'")
```

```text
A sees sum = 2 -> decides to go off duty
B sees sum = 2 -> decides to go off duty
both UPDATEs succeed; final: [('alice', 0), ('bob', 0)] sum = 0
```

Write skew, live. Wrap the same race in `BEGIN` transactions and the second writer hits the Lab 2 wall — `OperationalError: database is locked (code 517)`, final sum 1 — SQLite refuses *any* write from a stale snapshot, stricter than Postgres. PostgreSQL 16 at REPEATABLE READ (*documented*): both commit, `sum = 0`, no error — RR permits write skew by design. At `SERIALIZABLE` one session aborts:

```text
ERROR:  could not serialize access due to read/write dependencies among transactions
```

The read view itself is inspectable in Postgres: `SELECT txid_current_snapshot();` returns `xmin:xmax:xip_list`, e.g. `100:104:100,102` — literally `low_water:high_water:active[]`.

## How to use this knowledge

- **Diagnose "disk fills and queries slow down every day"**: run the long-transaction query first — one `idle in transaction` pid is usually the whole story. Kill it, watch `History list length` / bloat recover. And keep long readers off the primary: a 10-minute report is 10 minutes of uncollectable garbage.
- **Choose levels as snapshot lifetime**: Postgres RC default is right for most OLTP; check-then-act invariants (inventory, quotas, on-call) need SERIALIZABLE plus a retry loop, or `SELECT ... FOR UPDATE` on the rows the check depends on.
- **Write the retry loop**: snapshot conflicts surface as aborts (40001, deadlock, "database is locked") — a transaction that cannot be replayed is a design bug.

## Connections

- **Module 01, Chapter 03 (virtual memory / COW)** — the payoff: MVCC is `fork()`'s page-table sharing at row granularity; refcount → visibility rule, PTE → version header, COW fault → new version, free frame → purged version.
- **Module 11, Chapters 01–04** — versions live in the 16 KB InnoDB page and the 8 KB Postgres page (ch01); the B-tree leaf holds the current version, next-key locks ride its record chains (ch02); Postgres's append-and-collect heap is the LSM philosophy in miniature, vacuum its compaction (ch03); the undo log is ch04's undo re-used as history.
- **Module 09, Chapter 01 (happens-before)** — isolation is a memory-model question: which writes must a read observe? MVCC freezes a cut of the happens-before DAG instead of ordering every access.
- **Module 08, Chapter 03 (PICTURE / fixed records)** — the fixed-record world's alternative: in-place update, no versions, readers locked out — the serialization tax MVCC abolishes.

## Quiz

1. Under MVCC, a reader and a writer touch the same logical row in the same instant with no lock between them. What physical object do they *not* share, and what replaced the lock in deciding what the reader sees?

2. A read view has `low_water = 100`, `high_water = 121`, `active[] = {100, 118}`. The chain: current row (`trx_id` 100), then undo versions `trx_id` 118 and 80. Which version does the reader return — and does the answer change if T118 commits a millisecond later?

3. SQLite, WAL mode: A does `BEGIN` then a `SELECT`; B does `UPDATE ...; COMMIT;`; A then tries `UPDATE`. What error and extended result code does A get, and why is A not simply allowed to see B's value and overwrite it?

4. A PostgreSQL 16 box: disk climbs though the app mostly DELETEs; `pg_stat_activity` shows one `idle in transaction` pid with a 4-day-old `xact_start`; full scans of a hot table are 3x slower. Give the mechanism chain from pid to symptom, and the fix.

5. Two processes each run `x = SELECT counter; x += 1; UPDATE counter = x` in autocommit. After two increments the counter is 1. Name the anomaly, explain the interleaving, give two fixes.

6. In PostgreSQL, why is it literally true that "a delete is a write" — what field change does `DELETE FROM t WHERE id = 7` perform, and why must VACUUM exist as a consequence?

7. In `fork()` (Module 01, Chapter 03): the kernel marks PTEs read-only + COW, and the child's write faults and copies one page. Give the MVCC counterpart of (a) the frame's refcount, (b) the RO+COW PTE flag, (c) the copy-on-write fault, (d) the child's page table.

8. The on-call race runs in PostgreSQL REPEATABLE READ: T_A and T_B both read `sum(on_duty) = 2`, each updates its own doctor's row, both commit cleanly. Final state, why was RR silent, and at SERIALIZABLE what structure does SSI flag, and who dies?

<details>
<summary>Answers</summary>

1. **They don't share the row's bytes.** The writer creates a new version (InnoDB: overwrites the index row after pushing the old bytes to the undo log; Postgres: appends a new tuple) while the reader reads an older one. The lock is replaced by the reader's *read view* plus the visibility rule that walks versions back to one created before the snapshot.

2. **The `trx_id` 80 version.** Step 3 rejects 100 and 118 (both in `active[]`), step 1 accepts 80 (`80 < low_water`). T118 committing later changes nothing: the view was fixed at snapshot time, and 118 stays in `active[]` for this transaction's life — that frozen list is what "repeatable" *means*.

3. **`OperationalError: database is locked`, extended code 517, `SQLITE_BUSY_SNAPSHOT`.** Letting A see B's value would graft a dirty read onto a frozen view; letting A write without seeing B's update would lose B's update. Refusal is the cheap correct answer: roll back, re-read, retry.

4. **pid → oldest snapshot → frozen collector.** The idle transaction's snapshot is the boundary autovacuum cannot pass (long transaction = GC root). Every DELETE since then left a dead tuple — `xmax` stamped, bytes never freed — so garbage accumulates: heap and indexes bloat (fragmentation), scans read more pages, disk climbs. Fix: `pg_terminate_backend(pid)`, plus `idle_in_transaction_session_timeout`.

5. **Lost update.** A reads 0, B reads 0, A writes 1, B writes 1 — B's write clobbers A's because it comes from a stale read. Fix 1: one transaction with `SELECT ... FOR UPDATE` — the read itself takes the lock (the 2PL escape hatch). Fix 2: make the write relative — `UPDATE counter = counter + 1` — so no application read feeds the write.

6. **DELETE stamps the tuple's `t_xmax` with the deleting transaction's id — a field write, the only physical change** (plus the commit verdict in the clog). No bytes are freed; the tuple is dead only *logically*. VACUUM exists because the delete never removes data: dead tuples accumulate until a collector sweeps them, so in-flight snapshots can still see the "deleted" row.

7. (a) The frame's refcount ↔ the set of read views that can still reach a version — when the last moves on, the version is purgeable. (b) The RO+COW PTE bit ↔ `trx_id`/`xmin` — "not unconditionally yours to see." (c) The COW fault and page copy ↔ the writer creating a new version, not mutating in place. (d) The child's page table ↔ the read view — the per-reader mapping deciding which version each access resolves to.

8. **Final state: `sum = 0`, invariant broken.** RR was silent because the transactions wrote *disjoint* rows: snapshot isolation detects only write-write overlap. At SERIALIZABLE, SSI sees the rw-antidependencies — T_A read `bob`'s row which T_B wrote, and vice versa — two consecutive rw edges through a pivot: a dangerous structure. One of the three aborts; the retry reads `sum = 1` and the doctor stays on call.

</details>
