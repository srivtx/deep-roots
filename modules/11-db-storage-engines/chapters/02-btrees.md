# Chapter 02 — B-trees

> **The 30-second version.** A disk read costs ~100 µs (SSD) and moves at
> least a page, so a disk search structure is judged by one number: page
> reads per lookup. A balanced binary tree over a billion keys needs
> log2(10^9) ≈ 30 reads; a tree whose nodes are 16 KiB pages holding ~1,000
> keys needs 3-4. That fanout — page size divided by entry size — is the
> entire invention. The rest is node engineering: InnoDB packs a page as a
> linked list of records between two sentinels, hangs a sparse directory
> off the page end so the ragged list can still be binary-searched, splits
> pages 50-50 for random keys but ~90-10 for appends, and stores primary
> keys in secondary leaves — why a secondary lookup is *two* descents
> unless the index covers the query.

## The argument in one division: log2(N) versus log_F(N)

Chapter 01's cost model: one page, one I/O, ~100 µs on SSD. A lookup costs
one page read per node visited, and a balanced BST over 10^9 keys visits
log2(10^9) ≈ 30 nodes — comparisons are free next to one I/O; the binary
tree loses because *every level costs a page*.

So amortize: fill each page with as many routing entries as fit. An
interior entry is a separator key plus a child page number — 8 bytes
(BIGINT) + 6 bytes + ~2 of bookkeeping, ~16 bytes per child:

```text
fanout F = page size / entry size = 16,384 B / 16 B ≈ 1,000 children
leaf capacity L ≈ 16,384 B / ~20 B ≈ 800 entries (secondary index leaf)

capacity at height h = F^(h-1) × L:
h = 3 -> 1,000² × 800 ≈ 8 × 10^8 entries   (3 page reads)
h = 4 (worst case, 50% fill) -> ≥ 10^9      (4 page reads)
```

Three reads versus thirty — a ~10x gap that *is* the argument; the
logarithm's base, normally a footnote, is here the whole design. The top
of the tree is nearly free to keep hot: root plus second level is 1 +
1,000 pages ≈ 16 MiB, so the buffer pool (module 01 ch02's tower, applied
to disk) pins two levels and a typical lookup costs **one** real read.
Two consequences: nodes must stay roughly half-full (depth depends on
*effective* fanout; InnoDB's minimum fill of half a page keeps worst-case
fanout ~500, MySQL 8.0), and anything that shrinks entries buys depth —
every 2x cut in entry size is a level at a billion rows.

## One 16 KiB page, dissected

InnoDB's index page (MySQL 8.0, COMPACT records) is the node anatomy for
most B-trees:

```text
byte 0                                                          byte 16384
+-----------+---------+---------+----------------+-------+-------+---------+
| FIL hdr   | infimum | supremum| user records   | free  | page  | FIL     |
| 38 B      | 13 B    | 13 B    | ...grow =====> | space | dir   | trailer |
+-----------+---------+---------+----------------+-------+=======+---------+
0           38        99       112 125 ...      (shrinks) <====grow 16376 16384

FIL header (38 B): checksum 4 | page no 4 | PREV 4 | NEXT 4 | page LSN 8 |
  FIL_PAGE_TYPE 2 (17855 = index page) | flush LSN 8 | tablespace id 4
  PREV/NEXT: leaves DOUBLY LINKED -- the range-scan highway (§6)
  page LSN: ch04's WAL watermark ("flush only if log is durable past this")
FIL trailer (last 8 B): checksum + low 32 bits of the page LSN

one user record (COMPACT):          the logical chain, in every page:
+-----------------+---------------+  infimum -> r1 -> r2 -> ... -> rN -> supremum
| header 5 B      | key fields    |  ("before all keys"/"after all keys";
| info 4b|owned 4b| (+ PK, if     |   fixed offsets 99/112; real 13 B records)
| heap# 13b|type 3b|  secondary)  |
| next-record 2 B-+-> next key    |
+-----------------+---------------+
page directory (grows leftward from trailer): 2 B slots, each owning 4-8
records (owned count = the 4-bit field in that record's header)
```

Three details are load-bearing. **Records are linked within the page, and
physical order is allocation order:** a record inserted between two
neighbors is carved from the free gap and spliced in via 2-byte
next-pointers — the chain, not byte addresses, is the truth. A page is a
tiny heap arena (module 01 ch05, one level down): carve, mark-delete,
compact later. **The sentinels make gaps first-class:** every record,
sentinels included, has a predecessor, so "the gap before row r" is always
defined — even before the first and after the last row. InnoDB's next-key
locking (record + preceding gap, REPEATABLE READ's phantom blocker, ch05)
is implemented as locks on chain positions; no separate gap structure
exists. **The directory exists because you cannot binary search a ragged
list:** records are variable-length and unordered, and binary search needs
a sorted array of fixed-width entries. So InnoDB keeps a sparse one —
2-byte slots pointing at every 4th-8th record; page lookup is
binary-search ~n/6 slots, then walk ≤ 8 next-pointers. A dense directory
would rewrite on every insert; the owned-count nibble rebalances slots one
record at a time.

## Splits: where the B-tree pays rent

Insert into a full leaf and something gives: allocate a new page, move half
(or most) of the records, insert a separator into the parent — which may
itself split. The split policy is where fragmentation is born. **50-50**
(textbook default; InnoDB's choice for mid-page inserts): under random
insertion, steady-state fill converges to ln 2 ≈ 69%. **90-10** (the append
optimization): when the new key sorts after every key in the page, leave
~90% behind and open a nearly empty right page for the coming appends
(MySQL 8.0, `btr_page_split_and_insert` in
`storage/innobase/btr/btr0cur.cc`). The lab builds the same 100,000 rows
three ways: **12,500 leaves at 53.3% fill** (sequential keys, 50-50),
**7,143 leaves at 93.3%** (sequential, 90-10), **9,446 at 70.6%**
(random, 50-50) — 1.75x the leaf count between the first two, for
identical data.

Random fill lands at 70.6% — theory says ln 2 ≈ 69.3%. But note the first
row: sequential keys with naive 50-50 splits give the *worst* fill (53%),
because every frozen left page is stuck at half forever. The 90-10 policy
rescues the B-tree's favorite workload, monotonically increasing keys, and
appends concentrate insertion at the rightmost leaf — every descent goes
right, that page stays hot in the buffer pool, no random page is dirtied.
This is why `BIGINT AUTO_INCREMENT` inserts so much faster than random
UUIDs (quiz 3). Deletion runs the film backward: dead records leave holes,
and pages below `MERGE_THRESHOLD` (default 50, MySQL 5.7+) try to merge
with a neighbor.

## Secondary indexes: the locator decision

**InnoDB: the clustered primary key.** The PK index *is* the table — its
leaves hold full rows; a secondary leaf holds `(secondary key, primary
key)`. Why not a page/record pointer? Rows *move* — splits, merges,
compaction relocate records — and a physical pointer would dangle. The
price is the insider trap: `SELECT name FROM users WHERE email = ?` with an
index on (email) is a full descent down the email tree, then a *second
full descent* down the PK tree to fetch the row. The escape is the
**covering index**: if every column the query touches is in the secondary
index — its columns plus the PK riding in every leaf — one descent
suffices, and EXPLAIN shows `Using index`. The flip side: the PK is
duplicated into *every* secondary leaf, so a fat 16-byte UUID PK inflates
every index on the table.

**PostgreSQL: the heap first.** Tables are heaps (8 KiB pages); *every*
index stores 6-byte TIDs (4-byte page + 2-byte line offset) into the heap.
The update-path optimization is **HOT** (Heap-Only Tuple, PostgreSQL 8.3+):
if an UPDATE changes no indexed column *and* the new version fits on the
same page, the new tuple chains via `ctid` and *no index is touched*.
Break either condition and every index gains an entry per update — index
bloat. The knob is heap `fillfactor` (default 100; set ~85-90 on
update-heavy tables).

## Shorter keys, safer climbs

**Suffix truncation.** An interior separator only *routes*: keys ≤ it go
left, the rest right. It needs just enough bytes to discriminate the left
subtree's maximum from the right's minimum — a shared prefix is dead
weight. PostgreSQL's nbtree truncates high keys this way; Oracle and MyISAM
ship key prefix compression. The payoff routes back to §1: bytes saved per
entry are fanout, and fanout is depth.

**Latch crabbing.** The obvious protocol — latch the root and hold the
whole path until done — serializes all readers behind one writer. The
derived protocol is lock coupling: acquire the child's latch, then release
the parent **only when the child is "safe"** — for an insert, when the
child has room, so this insert cannot split it. A split propagates a
separator into the parent, so an unsafe child means the parent may be
written: keep it latched. Splits, merges, and height changes are *SMOs*
(structure modification operations), the events every B-tree concurrency
protocol is organized around.

**Optimistic descent.** Splits are rare under appends, so pay for pessimism
only when it happens: descend latching nothing (read, copy, release),
attempt the leaf insert, and if the leaf lacks room — or anything changed —
*restart* in crabbing mode. InnoDB runs exactly this shape
(`btr_cur_optimistic_insert`, retry via `btr_cur_pessimistic_insert`).
PostgreSQL's nbtree is a Lehman-Yao *B-link tree*: every page stores a
right link plus its high key, so a stale reader that lands on a just-split
page compares the high key and walks right — readers never block writers.
Assume the world is quiet, verify at the leaf, recover cheaply if not —
module 03 ch01's "the world changed under me" family, as a protocol.

## Range scans: the birthright

Leaves are doubly linked (`FIL_PAGE_PREV`/`FIL_PAGE_NEXT`, header bytes
8-16), so a range scan is one descent plus a walk: descend to the first
key in range, hop NEXT pointers until past the end. Scanning 10,001 rows
at ~14 rows per leaf (the lab's 93% fill) is a 5-page descent plus ~715
leaf hops ≈ 720 page reads — versus 10,001 descents row-at-a-time. The
walk is why `ORDER BY indexed_col LIMIT 100` needs no sort and stops after
a few pages, in either direction; and it is *almost* sequential I/O
(ch01's random-vs-sequential gap) in proportion to how unfragmented the
tree is.

## What insiders know
- **The fanout arithmetic, memorized:** 16 KiB / ~16 B per interior entry
  ≈ 1,000 children; at the guaranteed 50% fill ≈ 500; height 3-4 covers
  10^8 to 10^10 rows; root + level two ≈ 16 MiB, always cached — a cold
  point lookup is 1-2 real reads.
- **The two-descents trap:** `WHERE secondary_key = ?` fetching other
  columns costs a secondary descent *plus* a clustered descent in InnoDB;
  the fix is the covering index, and PK columns come free in every
  secondary leaf.
- **The 90-10 split and rightmost hot page:** monotonic keys concentrate
  inserts on one hot leaf and leave pages ~90% full (measured 93.3% vs
  53.3% — 1.75x the leaf count for identical data); random keys (UUIDv4)
  get mid-page 50-50 splits at ~69% fill.
- **HOT updates (PostgreSQL):** zero index writes when an update changes
  no indexed column and fits on the same page; default `fillfactor` 100
  quietly breaks the second condition; symptom — indexes growing 3x while
  the heap stays flat.
- **The page directory's 4-8 records per slot:** 2 bytes per slot, minimum
  2 slots, owned-count nibble in each record header — the two-level search
  mirrors the tree outside the page: ragged data needs a fixed-width array
  to binary search.
- **Next-key locking is a linked-list operation:** infimum/supremum chain
  positions are the lockable objects; phantom protection at REPEATABLE
  READ needs no auxiliary gap structure because the record chain *is* the
  gap structure.

## See it yourself

Part 1 — build a real B+ tree on disk and measure it (CPython 3.12, ~20 s).
Nodes are 256-byte blocks — 4-byte header, fifteen 8-byte key slots,
sixteen 8-byte child slots — fanout 16 by construction, §1's arithmetic
with the decimal point moved. The split policy is switchable so the 90-10
story is watchable:

```python
#!/usr/bin/env python3
# A B+ tree in a file: 256 B nodes, 15 keys max -> fanout 16.
# Split policy: 'mid' = naive 50-50, 'right' = ~90-10 (InnoDB-style append).
import struct, random, bisect, os, math

BLK, MAXK = 256, 15                  # node block size, keys per node
HDR = struct.Struct('<?H')           # leaf? + nkeys
Q = struct.Struct('<Q')              # 8 B slots: keys, pointers
KB = 8 + 8 * MAXK                    # children base (leaves: next-leaf ptr)
P = 'right'

class Tree:
    def __init__(s, path):
        s.f = open(path, 'wb+'); s.rd = 0; s.splits = 0
        s.f.write(BLK * b'\0')       # block 0 reserved: offset 0 means NULL
        s.root = s.alloc(); s.height = 1
    def alloc(s):
        s.f.seek(0, 2); off = s.f.tell(); s.f.write(BLK * b'\0')
        s.put(off, 1, [], []); return off
    def get(s, off):                 # one node = one counted page read
        s.rd += 1; s.f.seek(off); b = s.f.read(BLK)
        leaf, n = HDR.unpack_from(b, 0)
        keys = [Q.unpack_from(b, 8 + 8*i)[0] for i in range(n)]
        kids = [Q.unpack_from(b, KB + 8*i)[0] for i in range(n+1)]
        return leaf, keys, kids, Q.unpack_from(b, KB)[0]
    def put(s, off, leaf, keys, kids, nxt=0):
        b = bytearray(BLK); HDR.pack_into(b, 0, leaf, len(keys))
        for i, k in enumerate(keys): Q.pack_into(b, 8 + 8*i, k)
        Q.pack_into(b, KB, nxt)
        if not leaf:
            for i, c in enumerate(kids): Q.pack_into(b, KB + 8*i, c)
        s.f.seek(off); s.f.write(b)
    def _ins(s, off, key):           # returns None or (separator, new page)
        leaf, keys, kids, nxt = s.get(off)
        if leaf:
            keys.insert(bisect.bisect_left(keys, key), key)
        else:
            i = bisect.bisect_right(keys, key)
            r = s._ins(kids[i], key)
            if not r: return None
            keys.insert(i, r[0]); kids.insert(i + 1, r[1])
        if len(keys) <= MAXK:
            s.put(off, leaf, keys, kids, nxt); return None
        s.splits += 1                # overflow: split this node
        at = len(keys)*9//10 if (P == 'right' and leaf and keys[-1] == key) else len(keys)//2
        if leaf:                     # leaf: copy first key of right page up
            L, R, sep = keys[:at], keys[at:], keys[at]
            roff = s.alloc(); s.put(off, 1, L, [], roff); s.put(roff, 1, R, [], nxt)
        else:                        # interior: move separator up
            L, R, sep = keys[:at], keys[at+1:], keys[at]
            roff = s.alloc(); s.put(off, 0, L, kids[:at+1]); s.put(roff, 0, R, kids[at+1:])
        return (sep, roff)
    def ins(s, key):
        r = s._ins(s.root, key)
        if r:                        # root split: the tree grows one level
            sep, roff, old = r[0], r[1], s.root
            s.root = s.alloc(); s.put(s.root, 0, [sep], [old, roff]); s.height += 1
    def find(s, key):
        off = s.root
        while True:
            leaf, keys, kids, nxt = s.get(off)
            if leaf: return key in keys
            off = kids[bisect.bisect_right(keys, key)]
    def stats(s):                    # walk the leaf chain: count + fill
        off, n, tot = s.root, 0, 0
        leaf, keys, kids, nxt = s.get(off)
        while not leaf:
            off = kids[0]; leaf, keys, kids, nxt = s.get(off)
        while off:
            n += 1; tot += len(keys); off = nxt
            if off: leaf, keys, kids, nxt = s.get(off)
        return n, 100.0 * tot / (n * MAXK)

def build(n, order, policy):
    global P; P = policy
    if os.path.exists('bt.bin'): os.unlink('bt.bin')
    t = Tree('bt.bin')
    for k in (range(n) if order == 'seq' else random.sample(range(10*n), n)):
        t.ins(k)
    return t

print(f'{"rows":>7} {"height":>6} {"reads/probe":>11} {"log2":>4} {"log16":>5}')
for n in (100, 1_000, 10_000, 100_000, 300_000):
    t = build(n, 'seq', 'right'); t.rd = 0
    for k in random.sample(range(n), min(200, n)): t.find(k)
    print(f'{n:>7} {t.height:>6} {t.rd/min(200,n):>11.1f} '
          f'{math.ceil(math.log2(n)):>4} {math.ceil(math.log(n, 16)):>5}')
print('same 100k rows, three histories:')
for order, pol, lab in (('seq', 'mid', 'sequential + 50-50'),
                        ('seq', 'right', 'sequential + 90-10'),
                        ('random', 'mid', 'random + 50-50')):
    t = build(100_000, order, pol)
    leaves, fill = t.stats()
    print(f'{lab:<18} leaves={leaves:>6} fill={fill:4.1f}% '
          f'file={os.path.getsize("bt.bin")//1024:>5} KiB splits={t.splits}')
```

Output (CPython 3.12, one run; fill stable to ~±0.5%, leaf counts to ~1%):

```text
   rows height reads/probe log2 log16
    100      2         2.0    7     2
   1000      3         3.0   10     3
  10000      4         4.0   14     4
 100000      5         5.0   17     5
 300000      6         6.0   19     5
same 100k rows, three histories:
sequential + 50-50 leaves= 12500 fill=53.3% file= 3516 KiB splits=14057
sequential + 90-10 leaves=  7143 fill=93.3% file= 2008 KiB splits= 8029
random + 50-50     leaves=  9446 fill=70.6% file= 2589 KiB splits=10352
```

What to notice: **reads/probe equals height, exactly** — one page read
per level, §1's cost model made visible. The tree gains a level per
~9-16x rows, not per 2x like a binary tree (compare log2 to log16). At
300,000 rows height is 6 while log16 says 5, because *this toy splits
interior nodes 50-50*: effective interior fanout ~9, not 16 — the
"fanout" in the depth formula is always the fill-adjusted one. The
three-history block is §3's argument, measured.

Part 2 — inspect a real InnoDB page (**documented walkthrough, not run
here** — no MySQL in this sandbox; commands and fields are from the MySQL
8.0 manual and the innodb_ruby project, not live output):

```bash
mysql -e "CREATE TABLE t (id BIGINT PRIMARY KEY AUTO_INCREMENT,
                          pad VARCHAR(200)) ENGINE=InnoDB;
           INSERT INTO t SELECT NULL, 'x' FROM seq_1_to_10000;"
mysql -e "SELECT @@innodb_page_size;"     # 16384
innochecksum --page-type-summary t.ibd    # pages by FIL_PAGE_TYPE
innochecksum --page=4 t.ibd               # decode page 4's FIL header
gem install innodb_ruby                   # third-party teaching tool
innodb_space -f t.ibd -p 4 page-dump | head -40
```

In the `innochecksum` dump look for `FIL_PAGE_OFFSET` (page number) and
`FIL_PAGE_TYPE` = 17855. In `innodb_space`'s page-dump you should recognize
§2: `records:` starting with `infimum` and `supremum`, per-record `next`
offsets forming the chain, and a `directory:` of 2-byte slots — one per
4-8 records, minimum two.

## How to use this knowledge

- **Choose primary keys with the mechanism in mind** (InnoDB): short and
  monotonic — `BIGINT AUTO_INCREMENT` or a time-ordered ID — gets the
  rightmost hot page, 90-10 splits, ~93% fill, slim secondary leaves.
- **Design covering indexes for hot queries:** add the selected columns
  and check EXPLAIN for `Using index`; PK columns are already in the leaf.
  Each extra index is a whole tree that splits and re-fills — cover the
  query, don't collect columns.
- **Budget latency from height:** warm levels are ~µs, each cold level one
  page read (~100 µs SSD, ~10 ms HDD); a nested-loop join does
  outer-rows × descents — multiply before you run it.
- **Diagnose "table 2x bigger than its data":** measure fill (page-walk as
  above, `pgstattuple` in PostgreSQL); ~69% means random-insert or non-HOT
  history; a rebuild resets to ~93% only if future inserts are sequential.
- **Keep PostgreSQL updates HOT:** don't index columns that change every
  update (counters, last_seen), and drop heap `fillfactor` to ~85-90 on
  churn-heavy tables.

## Connections

- **Module 01 ch02 (the hierarchy):** read-per-level is the latency tower
  applied to trees; root + level two = 16 MiB makes "the index top lives
  in cache" a derivable budget.
- **Module 01 ch05 (the heap):** an index page is a fixed-size arena —
  carve, mark-delete, compact; the page directory is the arena's
  bookkeeping inverted.
- **Module 11 ch01 (the disk contract):** page-size I/O, checksums, and
  the random-vs-sequential gap are why leaf linking and 90-10 splits pay;
  16 KiB (InnoDB) vs 8 KiB (PostgreSQL) is a tunable, not a truth.
- **Module 11 ch03 (LSM trees):** B-tree = update-in-place dynasty, LSM =
  copy-forward dynasty; splits (write-amp, 69% fill) vs compaction
  (write-amp, space-amp) is the same trade with axes swapped.
- **Module 11 ch04 (WAL & recovery):** the FIL header's page LSN anchors
  the flush rule; a split is 2-3 logged page writes, doublewrite-protected
  against ch01's torn pages.
- **Module 11 ch05 (MVCC):** next-key locks ride this chapter's record
  chain; purge of dead entries is what lets MERGE_THRESHOLD merges fire.
- **Module 03 ch01 (bugs from first principles):** optimistic descent and
  the B-link right-walk are "the world changed under me" races converted
  into verify-and-recover protocols.

## Quiz

1. **Derive the depth.** A B+ tree has 16 KiB pages, interior entries of
   exactly 16 bytes (key + child page number), and leaves holding 200
   entries each. Pages may fall to 2/3 full at worst. What is the
   guaranteed maximum number of page reads for a point lookup in a
   10^9-entry index?

2. **Predict the layout.** On one InnoDB page you insert keys 100, 200,
   300 (in that order), then delete 200, then insert 150. Where does
   150's record physically sit relative to 100's and 300's, what is the
   chain from infimum to supremum, and where is 200's record?

3. **Diagnose the slowdown.** An InnoDB table with a random UUIDv4
   `CHAR(36)` primary key plus three secondary indexes ingests rows at 1/5
   the rate of the identical table with `BIGINT AUTO_INCREMENT`, and its
   file is ~40% larger at the same row count. Explain both numbers.

4. **Diagnose the bloat.** A PostgreSQL table updated hourly has a stable
   heap size, but all four indexes tripled in two weeks. Which two HOT
   conditions are failing, what is the likely culprit, and what are the
   two fixes?

5. **Concept.** Why does InnoDB store primary-key values in secondary
   leaves instead of page/record pointers — what breaks with pointers, and
   what does the choice cost on every non-covered lookup?

6. **Concept.** Why is the page directory sparse (one slot per 4-8
   records) rather than one slot per record? Derive from: slots are fixed
   2 bytes, records are variable-length, inserts land mid-page.

7. **Predict the number.** With the lab's parameters (15 keys per node
   max, 90-10 leaf splits → ~93% leaf fill, but 50-50 interior splits),
   what height does a 250,000-key tree reach, and why does that not match
   ceil(log16(250,000)) = 4?

8. **Cross-chapter.** Take the 10^9-entry index of question 1 (fanout
   ~1,000, four levels, 16 KiB pages). How much RAM do the root and second
   level need, and how many *physical* reads does a typical lookup cost
   with those levels cached? Connect to module 01 ch02.

<details>
<summary>Answers</summary>

1. Worst-case fanout F = (16,384/16) × 2/3 ≈ 682. Capacity at height h =
   F^(h-1) × 200: h = 3 gives 682² × 200 ≈ 9.3 × 10^7 (too small); h = 4
   gives 682³ × 200 ≈ 6.3 × 10^10. **Guaranteed maximum: 4 page reads**
   (typical fill makes it 3). Guarantees use *minimum* fanout — the tree
   is never shallower than average-fill arithmetic promises.

2. 150's record is carved from the free gap between the record region and
   the page directory — physically *after* 300's record (allocation
   order), not between 100 and 300. The chain is
   `infimum -> 100 -> 150 -> 300 -> supremum`, spliced with 2-byte
   next-pointers. 200's bytes are still on the page, header-marked deleted
   and bypassed; they become reusable only after purge. This
   physical/logical split is why the page directory must exist.

3. Random keys land at a random leaf per insert: no rightmost hot page
   (every descent may fault a cold page), mid-page 50-50 splits (fill →
   ~69%, hence ~40% more bytes: 100/70.5 ≈ 1.42), and splits dirty random
   pages the buffer pool never keeps. The 36-byte PK is also duplicated
   into all three secondary leaves, shrinking their fanout and deepening
   every tree. (§3, §4.)

4. HOT needs (a) no indexed column modified and (b) the new version on
   the same page. Stable heap + growing indexes says updates are non-HOT:
   an indexed, frequently-updated column (status, counter, last_seen) is
   the classic culprit, or the page is full (default fillfactor 100).
   Fixes: un-index that column, and/or set fillfactor ~85-90 and rewrite.
   Each non-HOT update adds a TID entry to *every* index; old entries
   linger until vacuum.

5. Rows physically move — splits, merges, compaction — so stored pointers
   would dangle and need cascading repair on every SMO. The PK is a
   stable logical locator. The cost: two full descents per non-covered
   secondary lookup, plus PK bytes taxed onto every secondary leaf.

6. A dense directory shifts O(n) slot bytes on every mid-page insert;
   sparseness costs ~8 pointer walks after a log2(n/6) binary search —
   logarithmic search but linear maintenance, versus constant maintenance
   and a bounded walk. The owned-count nibble absorbs inserts
   incrementally: a slot takes records until its count hits 8, then a new
   slot appears. The directory also stays small enough to live in the
   page's tail.

7. Height 6. Leaves: 250,000/14 ≈ 17,900. Interior nodes split 50-50, so
   effective interior fanout is ~9, not 16: ~2,000, then ~220, then ~25
   interior pages above the leaves — and ~25 exceeds one root page's 16
   children, so the root splits and height goes 5 → 6. ceil(log16) = 4
   assumes every node at full fanout; depth is governed by effective,
   fill-adjusted fanout — the lab-side view of answer 1.

8. Root = 1 page; second level = 1,000 × 16 KiB ≈ 16 MiB — both pinned in
   RAM out of a 10^9-entry index. A lookup then touches level 3 (10^6
   pages ≈ 16 GiB, mostly uncached) and one leaf: typically **1-2
   physical reads**. That is module 01 ch02's tower in action — each
   level is ~F times smaller than the one below, so the hot top of a
   B-tree is exponentially small by design, and the cache exploits it for
   free.

</details>
