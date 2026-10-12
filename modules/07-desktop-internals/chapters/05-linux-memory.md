# Chapter 05 — Linux: the memory machinery

> **The 30-second version.** Linux hands every process a generous fiction — a
> huge address space it may never back — and keeps the real books in `/proc`.
> `smaps` prices each mapping: RSS double-counts shared pages, PSS splits them
> fairly, `Private_Dirty` is the irreducible heap bill. The page cache quietly
> spends spare RAM as a disk cache; THP and slab are the kernel's own
> optimization layers over the 4 kB page; overcommit and the OOM killer break
> the promise when RAM runs out. All outputs below:
> kernel 5.10.134, x86_64, 4 GiB RAM, no swap.

## What a mapping costs: the smaps anatomy

Chapter 04's `ld.so` built the address space; `/proc/self/maps` lists it — one
line per VMA (virtual memory area): range, permissions (`rwx` + `p`/`s`),
offset, backing file. Each VMA's price is in `/proc/self/smaps` — a *real* entry,
a shell's heap, dissected:

```text
55f666ce6000-55f666d07000 rw-p 00000000 00:00 0  [heap]
Size:          132 kB   <- virtual: what the VMA could hold
Rss:            32 kB   <- 4 kB pages actually in physical RAM
Pss:            32 kB   <- private + shared/N (here all private)
Shared_Clean:    0 kB   <- file-backed pages others map too
Shared_Dirty:    0 kB   <- shared pages someone wrote (COW leftovers)
Private_Clean:   0 kB   <- your file-backed pages, untouched
Private_Dirty:  32 kB   <- YOUR written/anon pages: nobody else can claim these
Anonymous:      32 kB   <- no file behind it (heap lives here)
AnonHugePages:  0 kB   <- how much is 2 MB huge pages
VmFlags: rd wr mr mw me ac
```

A page in RAM is counted in the `Rss` of *every* process mapping it — that is
what *shared* means. The real `libc.so.6` text mapping here shows `Rss: 160 kB`,
`Pss: 16 kB`: ten processes share it, so PSS charges each 160/10. PSS =
private + shared/N, per VMA — hence insiders plan capacity from PSS:
sum-of-RSS **exceeds** physical RAM (shared pages double-counted); sum-of-PSS ≈
the true user-space footprint, the number a container budget should carry.

`Private_Dirty` is the sharpest number: anonymous *and written* pages — the
real cost of heap. malloc can promise a gigabyte; until you *write* each page
it costs nothing, and after the first touch only freeing or swap-out reduces
the bill.

## The page cache: the RAM you didn't budget for

A file's second read is fast because the kernel keeps a 4 kB page of RAM per
touched file offset — the **page cache**. `read()` copies cache→heap;
`write()` copies heap→cache and marks the page *dirty*. Nothing has hit the disk
yet:

```text
Cached:   2804236 kB   <- 68% of RAM is page cache
Dirty:          0 kB
```

Writing 192 MB moved `Cached` 204036 → 400860 kB — into RAM, not my process.

Writeback has two thresholds (here: `dirty_background_ratio = 10`,
`dirty_ratio = 20`):

1. Dirty > **10% of RAM** → flusher threads write in the background.
2. Dirty > **20%** → the *writer itself* blocks in `balance_dirty_pages()` — the
   real mechanism behind "my writer stalls every few seconds," not disk
   slowness.

`fsync(fd)` flushes dirty data **and metadata** — size, mtime, block allocation.
`fdatasync(fd)` flushes data plus only metadata needed to retrieve it: a
size-extending append forces the size update anyway, an in-place overwrite does
not — why databases rewriting fixed-size records prefer it. Neither flushes the
*directory* of a new file — "file appears" durability needs an fsync on the
directory fd.

The full flow:

```text
       user heap (anonymous pages)            page cache (file pages)
  +------------------------------+    +------------------------------------+
  | malloc'd, memset'd pages     |    | 4 kB page per file offset          |
  | -> Private_Dirty in smaps    |    | -> Shared_Clean / Shared_Dirty     |
  +---------------+--------------+    +------+-----------------------+------+
                  |                        | read(): copy to heap   | write():
        anonymous fault                     v                       mark DIRTY
                  |                   page stays cached            |
          [ 4 kB page or 2 MB THP ]                                     |
                  v                                                    v
        +--------------------------+        (1) dirty > 10% RAM: flushers write
        | free / reclaim pool     |<----------- (2) dirty > 20%: WRITER throttled
        | (buddy allocator,       |
        |  /proc/buddyinfo)       |
        +------+-------------------+---+  O_DIRECT: read/write skips the cache
               | swap out          ^ page-in
               v                   |
        +-----------------------------+-----+
        | swap: disk | zram | zswap(LZ4)    |  <- compressed tier
        +-----------------------------------+
```

`O_DIRECT` bypasses the cache (alignment required: 512 B or 4 kB). The database
argument (module 11 ch01): a DB engine runs its *own* buffer pool, so the kernel
cache is double buffering — an extra copy, extra RAM, dirty spikes; hence
PostgreSQL/Oracle open data files O_DIRECT. For everyone else the cache is free:
`MemFree` looks tiny while `MemAvailable` (3.4 GB here) counts cache as
reclaimable.

## Transparent huge pages: TLB reach vs granularity

Module 01 ch02's payoff: the TLB holds ~1536–2048 entries for 4 kB pages —
~8 MB of reach at best. A 1 GB heap is 262,144 pages; every TLB miss is a
4-level page-table walk (~4 memory references). A 2 MB transparent huge page
(THP) maps 512 pages with one entry: the same heap becomes 512 entries, and the
walk is 3 levels deep.

THP upgrades anonymous VMAs behind your back: faults allocate 2 MB when alignment
allows (`thp_fault_alloc`), and the kernel thread `khugepaged` collapses
4 kB-filled 2 MB regions (`thp_collapse_alloc`). Policy here:

```text
/sys/kernel/mm/transparent_hugepage/enabled:  [always] madvise never
/sys/kernel/mm/transparent_hugepage/defrag:   always defer defer+madvise madvise [never]
```

Touching 768 MB by memset (lab below) left `AnonHugePages: 260096 kB` of 788 MB
resident — a third: malloc's mmap chunks are page- not 2 MB-aligned, and
fragments break collapse.

The cost made THP infamous: a 2 MB page needs 2 MB *contiguous* physical memory,
so under fragmentation the kernel runs synchronous **compaction** — migrating
live pages to build a free 2 MB run — *inside your allocation latency*. The
Redis folklore (antirez, 2014): sub-millisecond ops with occasional 10–100 ms
spikes — three orders of magnitude — traced to THP defrag. Latency-sensitive
servers set `never`/`madvise`; Redis disables THP itself via
`prctl(PR_SET_THP_DISABLE)` (kernel 3.15+). Reserve `MADV_HUGEPAGE` for dense
regions (JVM heaps, analytics).

## slab/slub: the kernel's own allocator

The kernel allocates millions of same-shaped objects — `dentry`s, `inode`s,
`task_struct`s, network buffers — plus arbitrary `kmalloc(n)`, so it keeps
per-object-type caches: **slab** (kernel 2.2, Solaris lineage), replaced as
default by **SLUB** in 2.6.23 — per-CPU lists of slabs (contiguous pages of
same-sized objects). Same shape as glibc's tcache over arenas (module 01
ch19), one layer down.

The generic `kmalloc` size ladder on 4 kB-page x86_64, kernel 5.10:

```text
8  16  32  64  96  128  192  256  512  1024  2048  4096  8192  (bytes)
```

Everything is a power of two except **96 and 192** — the kmalloc-192 lore:
profiling when SLUB landed showed allocations clustering at 65–96 B and
129–192 B; 160 B in a 256 B bucket wastes ~96 B, so two odd classes were added,
roughly halving fragmentation for the most common sizes. Above 8 kB, `kmalloc`
falls to the page allocator.

Read it live: `/proc/slabinfo` (mode 0400, root-only):

```text
slabinfo - version: 2.1
# name            active_objs num_objs objsize objperslab pagesperslab
kmalloc-192         10638      10752      192       21         1
```

10,752 slots, 10,638 in use, 192 B each, 21 objects per 1-page slab (21 × 192 =
4032 B, 60 B lost — the waste the class bounds). SLUB also **merges**
same-size caches (your custom 192 B cache *is* kmalloc-192), saving memory but
destroying type isolation; `slab_nomerge` disables it — why hardened kernels
boot with it (module 02 explains the cross-cache attacks). Under pressure,
caches register **shrinkers**: reclaim calls `shrink_slab()` and dentry/inode
caches evict cold objects; `echo 2 > /proc/sys/vm/drop_caches` just runs them
by hand.

## Overcommit and the OOM killer

mmap is a promise to pay later. `/proc/sys/vm/overcommit_memory` selects the
promise policy:

- **0 (heuristic, default):** refuse only "obvious" overcommits — a single
  allocation bigger than RAM+swap — while the total drifts.
- **1 (always):** never refuse.
- **2 (strict):** every mapping is charged to `Committed_AS`; new charges fail
  with ENOMEM past `CommitLimit = RAM × overcommit_ratio/100 + swap`.

Here: `4138560 kB × 50% + 0 = 2069280 kB` — exactly the CommitLimit in
`/proc/meminfo`. Mode 0 in action: a sleeping Python mapped 2 GB untouched
(`VmRSS 10188 kB`); `Committed_AS` jumped to `3527948 kB`, 1.5 GB past the
limit — and the mmap succeeded. Only mode 2 enforces.

**mlock interacts twice.** Locked pages are unevictable (OOM reports print
`mlocked:`), permanently shrinking reclaim headroom; in mode 2 they hold their
charge forever. Unprivileged users are capped by `RLIMIT_MEMLOCK`, classically
64 KiB (`ulimit -l` → 64 here).

When the promise comes due and no page can be reclaimed, the **OOM killer**
fires. The old 2.6 formula scored `total_vm` plus CPU-time penalties — folklore
says it favored long-running, mostly idle processes. Since 2.6.36 (kernel 5.10):

```text
points = rss + swap_entries + page_table_bytes/4096     (all in 4 kB pages)
score  = points * 1000 / total_pages_of_the_domain       (0..1000, printed in
          + oom_score_adj (a 3% root discount applies)      /proc/PID/oom_score)
```

`/proc/PID/oom_score_adj` (−1000..+1000) is the human override: −1000 makes a
task effectively unkillable, +1000 the designated victim. The table below shows
this box's daemons (kata-agent, juicefs, ossfs) all at −1000 — a real system
protecting itself.

The choreography, from a genuine kill: a page fault enters
`__alloc_pages_slowpath`, reclaim fails, `out_of_memory()` calls
`dump_header()` (the dmesg report), picks a victim, and `oom_kill_process()`
sends **SIGKILL** — also to siblings sharing the same mm, since only the last
frees the pages; the faulting task then retries. The **memory-cgroup** variant:
v1 can disable the kill (`memory.oom_control: oom_kill_disable 1`) so tasks
*block* while a supervisor — notified via `cgroup.event_control`/eventfd —
frees memory or kills something itself; v2 exposes pollable `memory.events`
(`oom`/`oom_kill`) and `memory.oom.group`. Userspace daemons (earlyoom,
systemd-oomd) watch PSI and kill gracefully before the SIGKILL roulette.

The insider's table — real lines, after demanding 8 GB on this 4 GiB box:

```text
[17095.468760] alloc invoked oom-killer: gfp_mask=0x100dca(...), order=0, oom_score_adj=0
[17095.469053] Tasks state (memory values in pages):
[17095.469053] [ pid  ]   uid  tgid total_vm      rss pgtables_bytes swapents oom_score_adj name
[17095.469078] [ 23939]  1001 23939   918156   883172    7135232        0             0 alloc
[17095.469079] oom-kill:constraint=CONSTRAINT_NONE,...,global_oom,task_memcg=/k8s.io/...
[17095.469091] Out of memory: Killed process 23939 (alloc) total-vm:3672624kB, anon-rss:3531024kB, ...
```

| Column | Meaning | alloc's value decoded |
|---|---|---|
| `total_vm` | virtual size, **pages** | 918156 × 4 kB = 3672624 kB (matches total-vm) |
| `rss` | resident pages, **pages** | 883172 × 4 kB ≈ 3.4 GB — the badness driver |
| `pgtables_bytes` | page tables, **bytes** | 7135232 ≈ 8 B of page table per 4 kB page |
| `swapents` | swapped-out pages | 0 — SwapTotal is 0 here |
| `oom_score_adj` | human override | 0 — unprotected |

The trap: the table's memory columns are *pages*, the final line is *kB*.

**zram/zswap** fill the diagram's bottom tier: zswap is a compressed in-RAM
pool in front of real disk swap (`/sys/module/zswap/parameters/enabled` = `N`
here); zram *is* the swap device — RAM compressing itself, ~2:1–3:1 with LZ4.
Android ships zram as its only swap (module 05 ch04).

## What insiders know

- **RSS is for one process; PSS is for a fleet.** Sum-of-RSS double-counts
  shared pages; sum-of-PSS is the footprint a sizing spreadsheet needs;
  `Private_Dirty` never shrinks by sharing. Poll `smaps_rollup` (4.14+).
- **Overcommit arithmetic you can recite:** mode 2 limit = RAM × ratio/100 +
  swap; on 4 GiB/50%/no-swap that is exactly 2069280 kB. DBA folklore: mode 1
  on database servers (huge shared buffers at boot); mode 2 on accounting-strict
  HPC/embedded boxes.
- **kmalloc-192 is the only non-power-of-two in the ladder** (with 96) because
  the measured average kernel allocation sat just under 200 B; 160 B in a 256 B
  bucket wastes 37%. SLUB merges same-size caches — `slab_nomerge` is the
  hardened-kernel flag.
- **OOM badness includes your page tables** (5.10: rss + swapents +
  pgtables/4096): a sparse 100 GB mmap costs page tables even untouched — the
  trap behind "swapents=0 but the daemon died anyway." −1000 adj is
  infrastructure's shield — read the table before blaming the kernel.
- **THP latency folklore:** `always` + `defrag` = sync compaction inside your
  allocation path; Redis's 2014 spikes were exactly this. Latency-sensitive:
  `madvise`/`never` + `prctl(PR_SET_THP_DISABLE)`; dense-heap:
  `MADV_HUGEPAGE`. Here: `thp_fault_alloc 923`, `thp_fault_fallback 347164`
  — fallback is normal.
- **fsync vs fdatasync:** fdatasync skips mtime/ctime but *cannot* skip a size
  update — appending makes them equal, overwriting makes fdatasync strictly
  cheaper. Neither syncs the parent directory.
- **PSI `some` vs `full`:** `some` = at least one task stalled on memory; `full`
  = every non-idle task stalled. Sustained `full` > 0 is the early-warning
  dial — what lmkd and systemd-oomd watch.

## See it yourself

All captured on kernel 5.10.134, x86_64, 4 GiB RAM, no swap.

**1. The whole process, priced in one read.** `cat /proc/self/smaps_rollup`
(kernel 4.14):

```text
Rss:                1820 kB     <- resident, shared pages fully counted
Pss:                 426 kB     <- private + shared/N — the fair share
Pss_Anon:            104 kB     Pss_File:  322 kB   Pss_Shmem:   0 kB
Shared_Clean:       1672 kB     <- libc/ld/vdso pages, read-only, shared
Shared_Dirty:          0 kB     Private_Clean:  44 kB
Private_Dirty:       104 kB     <- the heap bill: only 104 kB is ours
Anonymous:           104 kB     AnonHugePages:   0 kB
Swap:                  0 kB     SwapPss:         0 kB
```

RSS 1820 vs PSS 426: 1672 kB of libc/loader pages shared with ~five neighbors;
irreducible cost `Private_Dirty: 104 kB`.

**2. Watch an allocation become RSS.** The core of `alloc.c`:

```c
/* alloc.c: touch argv[1] MB in 256 MB steps; after each step, print
   VmRSS from /proc/self/status plus the smaps_rollup fields. */
char *p = malloc(256 << 20);
memset(p, 1, 256 << 20);   /* first touch = real RSS */
```

Real output, after 256 MB and 768 MB:

```text
after  VmRSS:  263468 kB   Rss: 263732 kB   Private_Dirty: 262264 kB  AnonHugePages: 239616 kB
after  VmRSS:  787980 kB   Rss: 788220 kB   Private_Dirty: 786560 kB  AnonHugePages: 260096 kB
```

`VmRSS` tracks touches, not mallocs; `Private_Dirty` ≈ VmRSS (all anonymous);
THP covers a third — 2 MB alignment, live.

**3. Fragmentation: /proc/buddyinfo.** Column n = free blocks of order n
(4 kB × 2^n), eleven columns to 4 MB:

```text
Node 0, zone   Normal  13  777  689  326  244  439  173  48  14  2  34
```

Read right-to-left: 34 free 4 MB blocks but only 2 at 2 MB — the in-between
orders fragmented away. High-order blocks feed THP collapse; a tail of zeros
with a fat left edge is fragmentation's signature.

**4. Memory pressure: /proc/pressure/memory** (PSI, kernel 4.20+):

```text
some avg10=0.00 avg60=0.04 avg300=0.06 total=538714
full avg10=0.00 avg60=0.02 avg300=0.04 total=484016
```

Captured minutes after the OOM experiment: avg10 clean, avg60/avg300 still show
the stall — % of time tasks waited on memory over 10 s/1 min/5 min; `total` is
cumulative microseconds. Sustained nonzero `full` = thrashing before anything
dies.

**5. Controlled OOM, and the cgroup twist.** This sandbox is itself a 4 GiB
cgroup (`memory.limit_in_bytes` → `4294967296`). Demand more than exists:

```text
$ ./alloc 8192
after  VmRSS:  3409664 kB ... AnonHugePages:  485376 kB
Killed                                    <- SIGKILL from the OOM killer
$ echo $?                                 -> 137 (128 + 9 = SIGKILL)
$ cat /sys/fs/cgroup/memory/memory.oom_control
oom_kill_disable 0   under_oom 0   oom_kill 1        <- was 0 before the run
```

The report ends `Out of memory: Killed process 23939 (alloc) ...
anon-rss:3531024kB` — and the ledgers disagree on *who* fired: dmesg says
`global_oom` (the kernel sees the micro-VM's physical RAM) while the container's
`oom_kill` went 0 → 1. For a *pure* memcg OOM on a cgroup v2 box you
administer:

```text
systemd-run --scope -p MemoryMax=200M /tmp/alloc 1024
```

and look for `constraint=CONSTRAINT_MEMCG` plus `memory.events` `oom_kill 1`.
Don't run the 8 GB experiment on a machine you care about.

## How to use this knowledge

- **Capacity planning:** size from sum-of-PSS + slab, never RSS sums; check
  `MemAvailable` (not `MemFree`); alert on PSI `full` trends — minutes of
  warning before the OOM killer.
- **OOM forensics from logs:** when a server "just died," pull `dmesg`/
  `journalctl -k`: the `invoked oom-killer` line names the faulting task, the
  table gives the victim (rss in *pages*) and `oom_score_adj` — −1000 means
  something else was supposed to die; `CONSTRAINT_MEMCG` = the container's
  limit, `CONSTRAINT_NONE` = the host.
- **Latency engineering:** bursty writers hitting dirty_ratio stalls get
  smaller, more frequent fsyncs; latency-critical daemons get THP
  `madvise`/`never`; dense heaps get `MADV_HUGEPAGE`.
- **Leak triage:** sample `Private_Dirty` over time — a monotonic climb is a
  real leak; climbing RSS with flat `Private_Dirty` is page cache, i.e.
  harmless. (Interviews: why 100 MB `MemFree` doesn't mean OOM; why malloc
  succeeds and the process dies mid-computation — overcommit + first-touch.)

## Connections

- **Module 01 ch02** — the TLB arithmetic THP cashes in: one entry maps 2 MB
  instead of 512 × 4 kB.
- **Module 01 ch03** — VMAs and page tables made visible: every smaps line is
  one VMA.
- **Module 01 ch19** — allocator design: slab is the kernel's answer to the
  same size-class problem glibc solves with tcache bins.
- **Module 07 ch04** — the loader built these mappings: `[vdso]`, file-backed
  text, the anonymous BSS you now price.
- **Module 11 ch01** — the page cache from the DB engine's side: buffer pools,
  O_DIRECT, fsync strategy.
- **Module 05 ch04** — lmkd vs OOM killer: Android kills by importance rank on
  PSI pressure before RAM runs out; the desktop waits and kills by badness.

## Quiz

1. Ten processes map the same 100 MB read-only file and touch all of it; each
   has 20 MB of private touched heap. Give each's Rss, Pss, Private_Dirty, and
   the machine's true footprint.
2. `smaps` for one VMA shows `Size: 132 kB, Rss: 32 kB, Private_Dirty: 32 kB,
   Anonymous: 32 kB`. The program frees and touches nothing more, yet minutes
   later the VMA reads `Rss: 8 kB`. Where did the other 24 kB go on a box with
   no swap?
3. 8 GB RAM, `overcommit_ratio 50`, 4 GB swap, mode 2. Compute CommitLimit.
   Two processes each mmap 4 GB untouched: does the second succeed?
4. From the OOM table: `[ 23939] 1001 23939 918156 883172 7135232 0 0 alloc`.
   Convert `rss` and `pgtables_bytes` to human units; why is `swapents` 0?
5. Redis shows p99.9 = 40 ms on 0.3 ms-average operations; THP `enabled` is
   `[always]`. Which kernel thread, doing what, and where is the time paid?
6. A daemon is killed; the log shows `constraint=CONSTRAINT_MEMCG` while
   `free -m` shows 40 GB free. Where is the limit, and which file do you read
   next?
7. Monitoring shows `some avg60=4.2` and `full avg60=0.0` for an hour. Is the
   box in trouble? Which number moves first if it worsens, and what does it
   mean?
8. Module 05 ch04's lmkd and this chapter's OOM killer both end in SIGKILL.
   Name two decisions that differ (when they kill; how they pick the victim)
   and one shared mechanism.

<details>
<summary>Answers</summary>

1. **Rss 120 MB** (shared counted fully in each); **Pss 30 MB** (100/10 + 20
   private); **Private_Dirty 20 MB** (heap only; the file is Shared_Clean).
   Footprint: 10 × 30 = 300 MB — PSS as the capacity number.
2. glibc trimmed the heap: free() returned the arena's top to the kernel — via
   brk shrink (shrinks `Size`) or, here with `Size` unchanged,
   `MADV_DONTNEED`. No swap = nowhere to write them and the data was logically
   free, so the kernel discards the pages to the buddy pool; next touch faults a
   fresh zero page. Freed pages stop costing when *returned*, not when malloc
   forgets them.
3. CommitLimit = 8 GB × 50% + 4 GB = 8 GB. The first 4 GB mmap succeeds; the
   second brings the charge to exactly 8 GB — it succeeds only if nothing else
   holds a charge, never true in practice (everything else committed something),
   so it fails with ENOMEM at mmap time. Strict mode's contract: fail up front
   instead of an OOM kill later.
4. rss 883172 is in 4 kB **pages** → 3532688 kB ≈ 3.4 GB (the final line's
   `anon-rss`). pgtables_bytes is already bytes: 7135232 ≈ 7 MB, ~8 B per
   mapped 4 kB page. swapents 0: SwapTotal is 0 — swap adds nothing to
   badness.
5. khugepaged (or the fault path under `always`) promotes Redis's anon pages to
   2 MB, which needs 2 MB physically contiguous; fragmented memory forces
   synchronous compaction — migrating live pages — inside the Redis thread's
   fault path. The ~40 ms tail over the 0.3 ms mean is compaction latency. Fix:
   `never`/`madvise` or `prctl(PR_SET_THP_DISABLE)` (Redis 6.x+).
6. The limit is the memory cgroup's, not the host's: the cgroup exhausted its
   memory.max (v2) / limit_in_bytes (v1) while host RAM was free. Next: v2
   `memory.events` (`oom`, `oom_kill`) or v1 `memory.oom_control` — in k8s,
   the pod's memory limit.
7. Not yet: `some` = at least one task stalled (reclaim in progress); `full`
   0.0 = no interval with *every* non-idle task stalled. `full` rises first if
   it worsens — the box-wide thrash signal lmkd and systemd-oomd act on;
   sustained `full` precedes livelock and OOM.
8. Shared: SIGKILL, and both read kernel pressure bookkeeping (lmkd literally
   reads PSI). *When:* lmkd kills proactively on pressure while memory remains;
   the kernel reacts after an allocation failed. *Victim:* lmkd ranks by
   importance (oom_adj hierarchy); the kernel scores badness (RSS + swap +
   page tables, + oom_score_adj) — importance-blind by default.

</details>
