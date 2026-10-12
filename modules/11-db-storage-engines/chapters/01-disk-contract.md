# Chapter 01 — The disk contract

> **The 30-second version.** Module 01 built a tower of memory; this module adds
> its basement. Below DRAM sit three media — NVMe, SATA SSD, HDD — and each hands
> you a *contract*: this much latency, this much random-I/O throughput, this much
> sequential bandwidth, and this little atomicity. `write()` only moves bytes into
> a RAM cache; `fsync()` is the only promise that survives a power cut — and even
> it is atomic only per *sector*, so your database's 16 KB page can half-land.
> Every storage engine — B-tree, LSM, WAL — is one machine: an allocator over
> fixed-size pages (module 01's heap, one tower level down) with a durability
> contract bolted on. This chapter is the fine print.

## The tower grows a basement

Module 01 ch02 ended the tower at "disk." Disk is not one thing, and its table
gains a new column — random IOPS — where every storage-engine design decision
in this module lives:

```
level        typical size     latency            random 4K IOPS      sequential
---------------------------------------------------------------------------------------------
registers    16-32/core       0                  --                 --
L1 I/D       32-64 KB each    ~4 cycles (1 ns)   --                 --
L2           256 KB - 2 MB    ~12 cycles (4 ns)  --                 --
L3           8 - 64 MB        ~40 cycles (13 ns) --                 --
DRAM         8 - 256 GB       ~100 ns            --                 tens of GB/s
NVMe SSD     0.5 - 8 TB       ~20-100 us         10^5 - 10^6 (QD32+) 1 - 7 GB/s
SATA SSD     0.25 - 4 TB      ~100 us            ~10^4 - 10^5        ~0.55 GB/s
HDD          1 - 20 TB        ~10 ms             100 - 200           100-250 MB/s
```

Three observations.

**Latency keeps its 10x rhythm.** DRAM ~100 ns, NVMe ~20-100 us, HDD ~10 ms —
on module 01's human scale (DRAM = 1 s) an NVMe trip is 3-15 minutes, an HDD
seek about 28 hours. The lesson (cross levels rarely, cache aggressively)
survives unchanged.

**Throughput is a second, independent axis.** Latency prices *one* access;
bandwidth is what you get when accesses are adjacent or many are in flight —
the HDD is the extreme case, and the arithmetic is worth doing once:

- Random: one seek + rotational delay ≈ 10 ms → 1 / 0.01 s = **100 IOPS**. At 4 KB
  each, that is 0.4 MB/s.
- Sequential: the head stays on track and the platter streams by → **~200 MB/s**
  (7200 rpm drives, outer tracks; 100-250 MB/s by zone).

Same device, **~500x** between random 4 KB and sequential traffic; on an SSD the
*cause* changes but the gap only shrinks to ~10-60x (measured in the lab below).
Every chapter that follows responds to this gap: B-trees keep related records on
one page (ch02), LSM trees convert random updates into sequential appends (ch03),
the WAL turns page writes into one sequential stream (ch04).

**Spec-sheet IOPS lie in the footnote.** NVMe drives advertise 10^5-10^6 IOPS
only at queue depth 32-128. At QD1 — a single-threaded loop of dependent
reads — even a good NVMe drive does ~10-70K IOPS; 500K at QD64 still means
~128 us per request. (Module 01 ch02's "latency is not throughput,"
now with a disk attached.)

## Inside the SSD: why random writes stayed expensive

An HDD is slow at random I/O mechanically: move the arm, wait for the platter.
NAND has no arm — so why does the gap survive? Flash imposes two rules: you can
only *program* (write) a page, never overwrite one, and you can only *erase* at
the coarser *erase-block* granularity — a NAND page is ~4-16 KB, an erase block
~256 KB - 4 MB, dozens to hundreds of pages. To "overwrite" anything, the
drive's firmware — the **FTL, flash translation layer** — must remap: write your
data to a fresh page, update the logical-to-physical map, mark the old page
*invalid*:

```
logical block (what you write)      physical NAND (what exists)
-----------------------------      ---------------------------
LBA 1000 ──────────────────┐        erase block N (256 KB - 4 MB)
LBA 1001 ──────┐           │      ┌─────────────────────────────┐
               ▼           ▼      │ pg0 pg1 pg2 ... pg63        │
        [FTL map: LBA→page]       │  v   i   v  ...  i          │
                                 │  v = valid   i = invalid    │
new write of LBA 1000:            │  erase only whole blocks    │
  goes to erase block M, pg7      └─────────────────────────────┘
  old pg2 becomes garbage         block N: 1 page valid, 63 garbage
                                   -> GC: copy the 1, erase all 64
```

Garbage collection is where the tax lands. When blocks fill with invalid pages,
the FTL copies the still-valid ones elsewhere and erases. The ratio of NAND
actually written to bytes the host sent is the **write amplification factor
(WAF)**. Sequential overwrites are nearly free (whole blocks invalidate
together: WAF ~1). Random 4-8 KB updates scatter one valid page per erase
block, so GC rewrites almost everything: WAF ~3-10x typical, 20x+ pathological
(nearly-full drive, no TRIM). A 16 KB page write is 1-4 NAND programs, but it
invalidates 1-4 pages *elsewhere*, whose blocks' GC turn will come. So "random
writes are expensive" survived the media change with a new mechanism
(erase-block GC instead of seek) and a smaller constant: random update-in-place
(B-tree page rewrites) pays the WAF tax; sequential runs (LSM flushes, WAL
appends) are WAF ~1. That asymmetry — not raw speed — is why ch03 exists.

## fsync: what the promise actually is

`write(fd, buf, 4096)` copies bytes into the kernel page cache and returns —
nothing is on disk; lose power now and the data is gone. The page cache batches,
reorders, and coalesces I/O — a write-back cache (module 07 ch05), and
write-back caches need a flush point:

```c
write(fd, page, 16384);   /* bytes now in the page cache, that is all */
fsync(fd);                /* force dirty pages of fd to the *device*
                             and wait until the device says: durable */
```

The contract, precisely: **after `fsync()` returns 0, the data survives power
loss.** Not "is in the drive's RAM" — drives keep their own volatile write-back
caches, so fsync pushes through those too (a cache-flush command; the drive
must program NAND before acknowledging). Two fine-print clauses matter more
than the headline:

**Clause 1 — atomicity is per sector, not per call.** Disks commit at sector
granularity (512 B on 512e, 4 KB on 4Kn "Advanced Format"). A 16 KB page write
is four 4 KB sector writes; power can fail between them, leaving a *torn page* —
half old, half new. fsync makes data durable; it never made it atomic. (Page
checksums exist for exactly this.)

**Clause 2 — fsync speed depends on hardware you didn't know you bought.** A
consumer SSD has a volatile cache and no reserve power, so on flush it must
program NAND (~1-10 ms worst case) before answering. An enterprise SSD carries
**power-loss-protection capacitors** sized to write out its cache after the plug
is pulled — so it safely acknowledges the flush the instant data hits its
capacitor-backed DRAM: fsync latency ~0.1 ms. Same OS, same syscall, ~10x
difference, purely from capacitors. This explains a large share of "our commits
got slow on cheaper disks" tickets, and why benchmarks that never fsync
say nothing about database performance.

**Where O_DIRECT fits.** Normal I/O copies your buffer → page cache → device,
and the page cache *caches* on top of your database's own buffer pool — double
copy, double cache. `O_DIRECT` bypasses the page cache (buffer, offset, and
length must be aligned to the device's logical block size, or `EINVAL`).
PostgreSQL's default is buffered I/O: it already manages its own buffer pool,
and the kernel cache is a deliberate second layer (checkpoint smoothing,
scans). InnoDB production configs typically set `innodb_flush_method=O_DIRECT`
for data files, because the buffer pool makes the kernel cache pure double
buffering. The rule: page cache + fsync is right for anything sequential or
small (redo/WAL); O_DIRECT is right when you cache the data yourself.

## The database page: the universal atom

Every storage engine allocates in fixed-size pages — the disk version of module
01 ch05's heap chunks: PostgreSQL 8 KB (`BLCKSZ 8192`), InnoDB 16 KB
(`innodb_page_size`, default 16384). The page is the unit of I/O, of B-tree
nodes (ch02) and SST blocks (ch03) — and of tearing, which is why its anatomy
is defensive:

```
byte 0                                                    8192/16384
+-------------+-----------------+-----------------+---------+
| header      | items/directory | free space      | trailer |
| page# or ID | (records, index | (grows as two   | checksum|
| checksum 4B |  within page)   |  bump pointers  | + LSN   |
| LSN 8-16B   |                 |  toward middle) |         |
+-------------+-----------------+-----------------+---------+
       ^         (PG: pd_lower ->        <- pd_upper)     ^
   torn-write detector #1                              detector #2
```

- **Header**: page number or ID (self-identifying — a page must know *who it
  is* even after being copied elsewhere), a **checksum** over the page, and an
  **LSN** (the last log record applied; ch04 makes this the WAL rule).
- **Trailer**: a second checksum/LSN copy at the *far end* — a tear kills some
  *sectors*, and one sector cannot reach both ends of an 8-16 KB page. Header
  and trailer disagree — torn page, detected.
- **Free space**: PostgreSQL's page keeps two bump pointers — items grow down
  from the front, tuple bytes grow up from the back, free space is the
  shrinking gap in the middle. Module 01 ch05's bump allocator, re-implemented
  *inside every page*, 8 KB at a time.

The lifecycle: a page is **paged in** from disk into the buffer pool (a malloc
from the pool's free list — ch05 again), modified, marked dirty, and
**paged out** by eviction/checkpoint as one unit, checksum verified on every
read (PostgreSQL `data checksums`, InnoDB `innodb_checksums`). A checksum
failure at startup — "page verification failed" — is clause 1 happening to
you.

## The thesis, formalized

Strip a storage engine to its skeleton and you get module 01 ch05's heap
allocator, one tower level down:

| module 01 ch05 (RAM heap) | storage engine (disk) |
|---|---|
| fixed-size chunks from the OS | fixed-size pages from the disk |
| free lists of free chunks | free-space maps / extent bitmaps |
| malloc = pop free list | page-in = read page, mark in pool |
| free = push free list | page-out / delete = mark page free |
| fragmentation: free-but-unusable holes | fragmentation: half-empty pages |
| (no contract — RAM forgets nothing) | **durability contract: checksums, LSN, fsync** |

The one genuinely new thing the disk adds is the contract: RAM never forgets what
you wrote between calls; disk *can* — at sector granularity, mid-page. Everything
else is the allocator's classic fight (locality, fragmentation, batching), priced
in milliseconds instead of nanoseconds: ch02 builds the read-optimal allocator
(B-tree), ch03 the write-optimal one (LSM — module 01 ch12's generational GC
replayed on flash), ch04 the contract's enforcer (the WAL), ch05 copy-on-write at
row scale.

## What insiders know

- **Enterprise vs consumer fsync is capacitors, not NAND.** Power-loss-protection
  (PLP) drives acknowledge flushes from capacitor-backed cache: fsync ~0.1 ms;
  consumer drives must program flash first: ~1-10 ms. Same spec sheet otherwise —
  *the* reason a $400 enterprise SSD beats a $120 consumer one for a database.
- **The torn-write unit is the sector** (512 B on 512e, 4 KB on 4Kn), never your
  page — a 16 KB InnoDB page is 4 tearable sectors, an 8 KB PG page is 16 on 512e.
  InnoDB's doublewrite buffer and PG's checksums exist *only* because of this.
- **Page sizes to recite: PostgreSQL 8 KB, InnoDB 16 KB** (both compiled-in
  defaults; InnoDB's settable at server creation, PG's at build). Why: big enough
  to amortize per-I/O cost and give B-trees fanout (ch02), small enough that a
  half-empty page doesn't waste a seek's worth of bandwidth.
- **PostgreSQL defaults to buffered I/O + fsync; InnoDB configs typically say
  `innodb_flush_method=O_DIRECT`** for data files — the buffer pool makes the page
  cache redundant double buffering. WAL/redo stays sequential in both — the
  page cache's best case. O_DIRECT demands aligned
  buffers/lengths/offsets — unaligned → `EINVAL` (the lab shows it).
- **WAF is a field number**: random 4-8 KB update-in-place runs WAF ~3-10 (20+
  on a full drive without TRIM), sequential ~1. Endurance is rated in
  drive-writes-per-day precisely because *host* bytes ≠ *NAND* bytes.
- **Spec-sheet IOPS assume queue depth**: 1M IOPS is QD128; the same drive at
  QD1 does 10-70K. Always ask "at what queue depth?" — the disk version of
  module 01 ch02's "latency is not throughput."

## See it yourself

Three labs, run on plain Debian 13 (kernel 5.10, gcc 14.2); fragments from my
run — your numbers will differ, the *ratios* are the lesson.

**1. Watch fsync happen.** Ten lines of C:

```c
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
int main(void) {
    int fd = open("db.dat", O_WRONLY|O_CREAT|O_TRUNC, 0644);
    char page[4096]; memset(page, 'A', sizeof page);
    write(fd, page, sizeof page);   /* -> page cache only, returns instantly */
    fsync(fd);                      /* -> the actual durability wait */
    close(fd); puts("done");
}
```

Trace it:

```console
$ gcc -O2 -o fsyncdemo fsyncdemo.c && strace -e trace=openat,write,fsync,fdatasync,close ./fsyncdemo
openat(AT_FDCWD, "db.dat", O_WRONLY|O_CREAT|O_TRUNC, 0644) = 3
write(3, "AAAA...", 4096)                = 4096
fsync(3)                                = 0
close(3)                                = 0
```

Notice what is *missing*: no `lseek`, no `read`, and the `write` returns before
any hardware is involved — all the waiting is packed into `fsync` (`strace -T`
shows ~10 us vs millisecond scale). That asymmetry is why chapter 04 invents
group commit.

**2. Measure the gap yourself.** Sequential vs random on the same file:

```console
$ dd if=/dev/zero of=/tmp/dd-seq.bin bs=1M count=512 conv=fdatasync
512+0 records in
512+0 records out
536870912 bytes (537 MB, 512 MiB) copied, 0.228297 s, 2.4 GB/s
```

Now random 4 KB reads, through the page cache and with `O_DIRECT`
(page-aligned buffer required):

```console
$ python3 - <<'EOF'
import os, random, time, mmap
rnd = random.Random(42)
offs = [rnd.randrange(0, 512*1024*1024//4096)*4096 for _ in range(5000)]
f = open('/tmp/dd-seq.bin','rb', buffering=0)
t0 = time.perf_counter()
for o in offs: f.seek(o); f.read(4096)
print(f"buffered : {5000/(time.perf_counter()-t0):,.0f} IOPS")
fd = os.open('/tmp/dd-seq.bin', os.O_DIRECT)
buf = mmap.mmap(-1, 4096)          # page-aligned 4K buffer
t0 = time.perf_counter()
for o in offs: os.preadv(fd, [buf], o)
print(f"O_DIRECT : {5000/(time.perf_counter()-t0):,.0f} IOPS")
EOF
buffered : 495,809 IOPS
O_DIRECT : 8,701 IOPS
```

Read the three numbers as one sentence: sequential 2.4 **GB/s**; single-queue
random ~36 **MB/s** (~8,700 IOPS); page-cache random half a million IOPS — the
file fit in RAM, so you measured the *cache*, not the device: precisely the
double buffering an `O_DIRECT` database avoids. The ~65x sequential-vs-random
gap came with no seek mechanism anywhere. The professional tool is `fio
--rw=randread --bs=4k --iodepth=1 --direct=1`: compare `iops`/`bw` at QD1 vs
QD32 to see the queue-depth footnote in your own data. Swap the `mmap`-aligned
buffer for a plain `bytearray` and you will meet `EINVAL` personally.

**3. Page sizes are pinned facts.** PostgreSQL's is a compile constant
(17.11 here, Debian 13 `libpq-dev`):

```console
$ grep -m1 define.BLCKSZ /usr/include/postgresql/pg_config.h
#define BLCKSZ 8192
```

On a running cluster: `pg_controldata "$PGDATA" | grep -i 'block size'` →
`Database block size: 8192`. InnoDB: documented default 16 KB —
`innodb_page_size = 16384` (MySQL 8.0 manual; runtime: `SHOW VARIABLES LIKE
'innodb_page_size'`). Notice 16 KB is exactly 4 x 4 KB sectors — 4 chances to
tear, hence ch04's doublewrite.

## How to use this knowledge

- **The slow-commit ticket.** "INSERTs take 10 ms each" is almost never plan
  choice — it is one fsync per commit on a consumer SSD. Diagnose with
  `strace -T`, confirm with the drive model's PLP status, fix with hardware
  or ch04's group commit.
- **Post-crash triage.** A database refusing to start over a checksum failure
  after power loss is *clause 1 happening on schedule*. Knowing the atomic unit
  is the sector tells you why it is always whole pages, never "half a row."
- **Workload-to-media arithmetic.** Divide before blaming the disk: 200 MB/s
  sequential is 25,000 8 KB pages/s; 100 random IOPS is 12.5 pages/s on an
  HDD — 2,000x. That division tells you whether a plan's page-touch pattern
  is viable on your storage.
- **Benchmark literacy.** Any storage number you are sold: which queue depth?
  block size? fsync in the loop? WAF counted? The four dials vendors turn.
- **Interview answers with teeth**: why pages are 8/16 KB (amortization +
  fanout), why fsync latency is hardware-dependent (capacitors), why LSMs won
  on flash (WAF, not latency).

## Connections

- **Module 01 ch02** — the tower this chapter extends; latency-vs-throughput
  reappears as IOPS-vs-bandwidth and QD1-vs-QD32.
- **Module 01 ch05** — the heap allocator: chunks→pages, free lists→free-space
  maps, bump pointers inside every PG page.
- **Module 01 ch03 / ch12** — COW and generational GC: raw material for ch05's
  MVCC and ch03's LSM-as-GC.
- **Module 07 ch05** — the kernel page cache from the OS side: the layer
  `O_DIRECT` skips.
- **Module 11 ch02-05** — B-trees (pages as nodes), LSM (sequential as
  religion), WAL (fsync as write barrier), MVCC (COW at row scale).
- **Module 12** — the GPU tower's sibling: HBM/VRAM in the DRAM role, the same
  latency/throughput/batching tradeoffs one architecture over.

## Quiz

1. **Concept.** NAND has no seek time. Explain — mechanism, not folklore — why
   random 4-8 KB writes are still ~10-60x worse than sequential writes on an SSD.
2. **Concept.** State precisely what `fsync()` guarantees, and two things it does
   *not* guarantee for a 16 KB page written just before it.
3. **Predict the number.** An HDD averages 10 ms per random access and streams
   ~200 MB/s sequentially. Compute (a) max random IOPS, (b) 8 KB pages/s
   sequential, (c) the ratio — and what that ratio implies for a scan touching
   rows in random page order.
4. **Predict the number.** Your NVMe drive: 7 GB/s sequential, 70,000 random 4K
   IOPS at QD1. A job writes 10,000 random 4 KB updates vs the same 40 MB as
   one sequential stream. Roughly how long does each take?
5. **Predict the output.** You open lab 2's file with `O_DIRECT` and try to
   read a 4093-byte chunk at an offset like 8190. What does the OS return, and
   why?
6. **Bug diagnosis.** A team migrates its database from an enterprise SSD to a
   "faster" consumer NVMe (2x the advertised IOPS). Commit latency rises from
   0.4 ms to 4 ms; read benchmarks are unchanged. What changed, physically?
7. **Bug diagnosis.** After a power cut, PostgreSQL restarts and reports
   `invalid page in relation ... checksum failure` on exactly one 8 KB page.
   Explain the failure at sector granularity, and why the header/trailer pair
   detected it.
8. **Cross-chapter.** Your fio run reports 900K random-read IOPS; your single
   thread doing dependent page reads sees 9K. Which module 01 ch02 principle
   explains the 100x, and what hardware mechanism keeps 100 requests in
   flight at once?

<details>
<summary>Answers</summary>

1. Flash can only program a fresh page and only erase whole erase blocks
   (~256 KB - 4 MB). A random 4-8 KB update invalidates a page *somewhere in a
   mostly valid block*; the FTL's GC must copy that block's survivors and erase
   it, writing far more NAND than the host sent — WAF ~3-10x random, ~1
   sequential. The *mechanism* moved (seek arm → erase-block GC); the economics
   survived.
2. It guarantees the file's dirty data (and enough metadata to reach it) is on
   non-volatile media when it returns 0. It does not guarantee (a) atomicity —
   the 16 KB page is 4 x 4 KB sectors and any subset can land (torn page) — or
   (b) anything about other files' unflushed data; each fsync covers its own
   fd.
3. (a) 1 / 0.01 s = 100 IOPS; (b) 200 MB/s / 8 KB = 25,000 pages/s; (c) 250x.
   A scan in random page order pays 100 IOPS where the sequential layout pays
   ~25,000 pages/s of bandwidth — the 250x *is* the case for keeping related
   rows on shared pages (ch02).
4. Random: the host-facing limit is 70,000 IOPS at QD1 → 10,000/70,000 ≈ 0.14 s
   (WAF burns endurance, not host latency).
   Sequential: 40 MB / 7 GB/s ≈ 5.7 ms — ~25x for sequential.
5. `O_DIRECT` requires offset, length, *and* buffer aligned to the logical
   block size. A 4093-byte length fails with `EINVAL` — you will see
   `pread(...) = -1 EINVAL`. A geometry contract enforced at the syscall
   boundary (hence the aligned `mmap` in my lab).
6. fsync latency. The consumer drive's volatile cache has no power-loss
   protection, so each flush (one per commit, ch04) must program NAND before
   acknowledging: ~ms-scale. The enterprise drive's capacitors let it
   acknowledge from cache: ~0.1 ms. Advertised IOPS never measured fsync;
   commits do.
7. Power failed mid-write: of the page's 4 sectors (16 on a 512e drive), some
   were programmed and some not — the atomic unit is the sector, so the page
   landed half-old, half-new. The checksum lives in header and trailer at
   opposite ends; a sector-scale tear cannot corrupt both, so recomputation
   mismatched the stored value — rejected, not silently trusted.
8. Latency is not throughput: 900K IOPS needs ~100 commands outstanding; a
   dependent-read thread has QD1 and pays full latency (900K/9K ≈ 100 in
   flight). The mechanism: command *queueing* (NCQ, NVMe queue slots) — the
   same pipelining that lets DRAM bandwidth beat DRAM latency.

</details>
