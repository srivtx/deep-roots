# Module 14 final exam — File systems & storage

Twelve questions across the module's five chapters. Closed book; every
answer is worked, not stated — the derivations are the point. Scoring:
11+ = you own this module; 8-10 = solid, revisit the flagged chapters;
<8 = the chapter quizzes are the study path.

**Part I — the medium's arithmetic (chapter 01)**

1. **Predict.** A service must sustain 50,000 random 4-KiB read IOPS.
   Derive the fleet size for 7,200 rpm SATA spindles (12 ms average
   seek, 200 MB/s outer track) and for 15,000 rpm SAS spindles
   (3.5 ms seek), then give the single-device answer — and the
   precondition the application must satisfy before that one device
   delivers anything near its number.

2. **Diagnose.** Two identical 2 TB TLC drives run the same nightly
   300 GB backup. The drive at 30% full starts at 2.5 GB/s and settles
   at 900 MB/s; the drive at 93% full bursts only briefly, then
   collapses to 150 MB/s — slower than a 7,200 rpm HDD reads. Nothing
   is failing on either drive. Name both mechanisms with their numbers,
   and the one fix that addresses them together.

3. **Derive.** (a) Derive, from NAND physics, why an FTL can never
   overwrite a logical page in place — and name the object that
   constraint forces into existence, with its DRAM price for a 1 TB
   drive of 4 KiB pages. (b) A 100%-full TLC drive, 16 KiB pages,
   128 pages per erase block, every page valid, TRIM never issued, no
   spare blocks: the host overwrites one logical page. Count the flash
   page-writes, the WAF, and the NAND bytes. (c) The drive is rated
   600 TBW at WAF 2.5. A cache workload pins WAF at 8 — what is the
   real host-write budget?

**Part II — objects, cache, and the fsync contract (chapter 02)**

4. **Diagnose.** A 100 GB volume: `df -h /` says 100% used, but
   `du -sx /` accounts for only 61 GB. A log daemon has been up for
   weeks, and rotation is a nightly `rm` of the old log. Where are the
   other ~39 GB, which two counts must both reach zero before they
   free, why can the held space still grow — and why is `du`
   structurally blind to any of it?

5. **Predict.** A single-threaded importer writes 40 GB to a fresh file
   on a 32 GB RAM server with every default (`vm.dirty_ratio` 20,
   `vm.dirty_background_ratio` 10, writeback cadence 5 s, page age
   30 s). Predict the run: how the first ~3 GB feel, at what Dirty
   level the writer stalls and inside which kernel function, and the
   standard fix with its trade.

6. **Diagnose.** A config saver does: write a temp file,
   `fsync(tempfd)`, `rename(temp, config)`. After a power cut the OLD
   config is back. The developer protests: "I fsynced before the
   rename!" What exactly did that fsync make durable, what did it not
   touch, why is `close()` never a substitute — and what is the
   complete recipe?

**Part III — the journaling lineage (chapter 03)**

7. **Predict.** A 3 GiB file is written in one streaming pass,
   perfectly contiguous, 4 KiB blocks — once on ext4, once on ext3.
   For each: how many records or pointer blocks map the file, does the
   map fit inside the inode, and what does reading the file's last KiB
   cost in extra block reads before the data arrives?

8. **Diagnose.** A mail server runs ext4 mounted `data=writeback` "for
   speed." After a crash, recovered mailboxes contain long stretches of
   a build tarball deleted the previous day. Derive the exact sequence
   writeback permits, why `data=ordered` — the default — structurally
   cannot produce it, why writeback is faster anyway, and where
   `data=journal` sits on the same axis.

**Part IV — the copy-on-write lineage (chapter 04)**

9. **Predict.** One small random overwrite of an existing file:
   (a) 4 KiB on Btrfs at tree depth 2, then at depth 3 (16 KiB nodes,
   plus the rotated superblock); (b) 8 KiB on OpenZFS defaults —
   recordsize 128 KiB, ARC miss, fsync; (c) 4 KiB on ext4 ordered
   mode. Derive each byte count and amplification factor — then name
   the two reasons this write shape nevertheless suits the flash
   underneath.

10. **Predict.** A 2 TiB Btrfs subvolume carries one read-only
    snapshot. Since the snapshot, the live subvolume has *overwritten*
    5 GB of existing files — nothing appended, nothing deleted.
    (a) You delete the snapshot: how much space frees, and why?
    (b) A second team, identical setup except their live tree only
    *appended* 10 GB after the snapshot, deletes theirs and nothing
    frees. Derive both outcomes from the refcount rule.

**Part V — special file systems and the one-write picture (chapter 05)**

11. **Diagnose.** A Docker image bakes a 100 MB model file into a
    layer; a container edits 1 byte of it at startup, and the writable
    layer — and boot time — balloons. Predict the write volume and walk
    the mechanism: where the bytes come from, why the edit cannot land
    in place, what *deleting* that file would do instead, and the fix.
    Name the module 01 ch03 primitive this replays, and its chapter 04
    cousin.

12. **Concept (cross-chapter).** "The file system writes your bytes to
    the disk." Attack the claim with the module's one-write diagram:
    walk `write(fd, buf, 4096)` on an ext4-over-NVMe file box by box,
    tagging each with its module 14 chapter; state exactly where
    `write()` returns and the four events that can push the page down
    later; then walk the reclaim loop that fires when memory pressure
    arrives before writeback does — including which pages take the
    swap branch.

<details>
<summary>Answers</summary>

**Part I**

1. 7,200 rpm: rotation 8.33 ms, average wait 4.17; seek 12; transfer
   4 KiB / 200 MB/s = 0.02 → **16.19 ms** → 1000/16.19 = **62 IOPS per
   spindle** → 50,000/62 ≈ **807 spindles**. 15,000 rpm: rotation 4 ms,
   average wait 2; 3.5 + 2 + 0.02 = 5.52 ms → **181 IOPS** → **277
   spindles**. Or one NVMe drive at QD32 (150,000-400,000 IOPS) — *if*
   the queue is actually deep: a synchronous issue-read-wait-respond
   program is QD1 by construction and gets 1/75 µs ≈ **13,300 IOPS** —
   a quarter of the target from a million-IOPS drive. Depth requires
   many threads or io_uring (Linux 5.1+); a 32-deep queue on the HDD
   only buys the elevator's 100-200 IOPS — reordering cannot fix
   mechanics (ch01).

2. Mechanism one, the **pseudo-SLC cache**: consumer TLC reserves
   12-100 GB programmed as fast single-bit cells, and it is carved from
   *free NAND* — the 93%-full drive has lost roughly an order of
   magnitude of it, so burst and sustained rates collapse together.
   Mechanism two, **GC write amplification**: past ~90% occupancy the
   blocks the garbage collector recycles are mostly valid, so each host
   write drags 5-10 flash writes behind it (1.5-3 on a healthy drive)
   and internal bandwidth saturates before the host's — 900 MB/s ÷ ~6
   ≈ the 150 observed. One fix for both: leave 10-20% of the drive
   unpartitioned (overprovisioning) or buy enterprise spare — mostly
   garbage blocks let the GC erase cheaply and leave the cache its
   room (ch01).

3. (a) Programming drives bits only 1-to-0 at page granularity;
   restoring a 1 requires an erase, and erase granularity is the whole
   block — an in-place overwrite would destroy the 127 neighboring
   valid pages. So the new version lands in a *fresh* page and
   something must remember which physical page answers for a logical
   address: the **LPA-to-PPA map**, ~4 B per page — 1 TB / 4 KiB =
   268M entries ≈ **1 GiB of DRAM**, why 1 TB drives carry 1 GB and
   DRAM-less ones stutter on random access. (b) Copy the 127
   still-valid pages, write the new version, erase: **128 flash
   page-writes** for one host page — **WAF 128**, and 128 × 16 KiB =
   **2 MiB** of NAND for a 16 KiB write (the chapter's 64x corner is
   the 64-page-block case; worst-case WAF equals pages per block).
   (c) TBW = capacity × P/E / WAF: 1 TB × 1,500 / 8 = **187.5 TB** of
   host writes, not 600 — each unit of amplification drains the P/E
   budget on a schedule the host-write calendar cannot see (Percentage
   Used can: it folds in the drive's measured WAF) (ch01).

**Part II**

4. The ~39 GB live in **deleted-but-open files**. `rm` is `unlink()`:
   the dentry is removed and `i_nlink` drops to 0 — but the daemon's
   fd still references the inode (`i_count` ≥ 1), and blocks are freed
   only when **`i_nlink` = 0 AND the last fd closes**. The daemon keeps
   appending into the now-nameless inode, so the held 39 GB can still
   *grow*. `du` walks names, and these blocks' only pointer is a
   process's fd table. `lsof +L1` lists exactly the link-count-0 files
   still held open; the fix is restarting (or signaling) the daemon,
   or rotating by truncation instead of unlink (ch02).

5. The first ~3.2 GB (the 10% background threshold) complete at memcpy
   speed — `write()` returns from the dirty page, zero I/O. Background
   flushers wake at 3.2 GB and drain concurrently; when Dirty crosses
   **20% of 32 GB = 6.4 GB**, the writer itself is drafted into
   synchronous writeback inside **`balance_dirty_pages()`** — D state,
   blocked in `write()` in device-rate-sized bursts until the level
   falls, repeatedly across a 40 GB run (pages may legally sit dirty
   30 s; flusher cadence 5 s). Fix: `vm.dirty_bytes`, with
   `dirty_background_bytes` lower (tens to hundreds of MB) — writeback
   starts early and throttling stays brief. The trade: small frequent
   stalls bought against rare multi-second freezes — cadence for tail
   latency (ch02).

6. The fsync made the *file* durable: its data blocks plus the
   metadata needed to find them (size and extent updates — delayed
   allocation resolved right now) plus the device-cache flush. It
   touched nothing about the **rename** — that is directory data,
   journaled metadata that commits within ext4's 5 s interval. Power
   died inside the window: replay restored the old directory entry, so
   the old name points at the old content and the fsynced new blocks
   are an orphan no name reaches. `close()` flushes nothing by POSIX
   design — it is never a durability tool. The recipe: `write temp →
   fsync(tempfd) → rename → fsync(dirfd of the final path)` — the last
   step makes the rename itself durable, and it is the one most
   production code omits. (`auto_da_alloc` covers the never-fsynced
   zero-length case, not this one) (ch02; ch03's commit interval).

**Part III**

7. ext4: 3 GiB / 128 MiB per extent = **24 extents** — 288 B of
   records. The inode's 60-byte `i_block` holds a 12-B header plus
   4 × 12-B entries — **4 extents maximum** — so the tree grows one
   level: a single 4 KiB leaf block (each holds ~340 extents) carries
   all 24. Map cost: **1 metadata block**; the tail read walks inode →
   leaf → data = **1 extra read**. ext3: 786,432 blocks — 12 direct +
   1,024 single-indirect, the remaining 785,396 under double
   indirection: 767 second-level pointer blocks plus the top one =
   **768 pointer blocks ≈ 3 MiB**, 768x the ext4 map, and the same
   tail read pays top plus one second-level = **2 extra reads** ≈
   24 ms of pointer chasing at 12 ms per HDD access — every tail
   access, forever (ch03).

8. Writeback journals metadata only, with **no data ordering**: the
   mailbox grew, the allocator handed it the tarball's freshly freed
   blocks, the extent update was journaled and **committed before
   those blocks were ever written**; power died; replay faithfully
   restored pointers to blocks still holding the tarball's old bytes.
   Ordered mode structurally cannot produce this: at commit, JBD2
   forces every dirty *data* block of the transaction's files to its
   final location *before* the metadata enters the journal — committed
   pointers never lead unwritten data; you may lose a file's tail, you
   can never gain another file's bytes. Writeback is faster precisely
   because it skips that forced flush. `data=journal` is the far end
   of the axis — data rides the log too, a crash can cost only the
   final seconds of log, but every byte is written twice (~2x I/O),
   worth it only for special volumes (ch03).

**Part IV**

9. (a) Btrfs, depth 2: 4 KiB data + 16 KiB leaf + 16 KiB root + 4 KiB
   superblock = **40 KiB = 10x**; depth 3 adds one 16 KiB internal
   node: **56 KiB = 14x** (both before compression and csum-tree
   updates). (b) ZFS: CoW rewrites whole records — the 8 KiB page
   forces **read 128 KiB + write 128 KiB** (plus the metadata path and
   a small ZIL record so fsync can return) = **16x write amplification
   and 16x the read traffic**. (c) ext4 ordered: the 4 KiB data block
   plus 2-3 journal blocks — O(1) metadata per update. That arithmetic
   is the measured 2-5x gap for fixed random overwrites at scale. The
   coda: CoW *likes* flash — fresh-block writes never trigger the
   FTL's read-modify-erase cycle, and freed extents pair with TRIM;
   the insult is two log-structured allocators now hiding each other's
   assumption of contiguity (ch04; ch01).

10. (a) **~5 GB frees.** The overwrites allocated fresh blocks for the
    live tree; the *old* 5 GB of blocks stayed valid because the
    snapshot's tree still points at them — reference count 1, the
    snapshot their only holder. Deleting the snapshot drops those
    counts to 0: freed (the snapshot's own footprint is a few KiB of
    metadata). (b) **~0 frees.** The appended 10 GB belongs to the
    live tree alone — the snapshot predates it and never referenced
    it — and every block the snapshot *does* hold is still shared with
    the live tree (count 2 → 1). The rule: a snapshot is a saved root
    pointer plus a refcount on every extent it reaches; creating one
    costs 0 data bytes, and deleting one gives back only what churn —
    overwrites and deletes after the snapshot — made exclusively its
    own (ch04).

**Part V**

11. **~100 MB, slightly over** — data plus mode, ownership, and
    xattrs/ACLs. The file lives in a read-only lower layer shared with
    every container from that image, so the edit cannot land in place:
    overlayfs **copy-up** hoists the entire file into the container's
    upperdir, and only then applies the byte — a 1-byte edit billed at
    whole-file granularity, once per container per file. Deleting the
    file could not touch the lower layer either: the upperdir gets a
    **whiteout** — a character-device entry, major 0, minor 0 — that
    hides the name (one `rm` of a file present in N layers performs N
    layer operations). Fix: a volume or bind mount for the big mutable
    file, bypassing the layer stack. The primitive is module 01 ch03's
    `MAP_PRIVATE` never-modify-the-shared-original; chapter 04's
    reflink is the same rule at extent granularity (ch05; ch04).

12. fd 3 indexes the fd table to a `struct file` (position, flags,
    refcount) — **ch02**; the path resolves through the dentry cache
    (the name) — **ch02**; the dentry points at the inode (identity,
    extent-tree root — on-disk layout from **ch03**); the 4 KiB page
    at (inode, offset) is marked dirty and **`write()` returns here —
    zero I/O has happened**. The four pushes: fsync; Dirty crossing
    `vm.dirty_ratio` (20% of RAM); the 30 s age against the 5 s
    writeback cadence; memory-pressure reclaim. Then ext4 resolves
    delayed allocation and JBD2 logs the metadata first — **ch03**; a
    bio (device, LBA, page) enters the block layer (`none` scheduler
    on NVMe) — **ch01**; the submission-queue doorbell hands it to the
    controller's 8-16 parallel NAND channels — **ch01**; the FTL maps
    LBA to physical page, perhaps garbage-collecting a block —
    **ch01**; the NAND page is programmed, erasable only per
    64-256-page block — **ch01**. Reclaim: kswapd scans the LRUs —
    clean pages drop free, dirty pages are forced down this same
    pipeline, and **tmpfs pages go to swap, their only disk** —
    **ch05**. The claim dies at the dirty page: nine arrows run below
    `write()`'s return, each owned by a chapter — the module in one
    picture (ch05).

</details>
