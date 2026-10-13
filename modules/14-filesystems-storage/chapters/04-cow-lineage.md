# Chapter 04 — The copy-on-write lineage: Btrfs, ZFS, APFS

> **The 30-second version.** Chapter 03's answer to torn writes was a journal: overwrite in place, log the intent first, replay after the crash. There is a second answer that deletes the repair step: never overwrite a live block. Every write lands in free space, the metadata above it is rebuilt to point at the new blocks, and the change goes live with one atomic root-pointer write — a single 4 KiB superblock write *is* the commit: module 11's WAL inverted, making the change itself atomic instead of logging it. Three features fall out for free: snapshots (a saved root pointer is a complete frozen file system — 100 of them cost 0 data bytes), crash recovery in one read (a half-written tree is garbage behind an unpublished root), and whole-path checksums (a block's checksum is stored in its freshly written parent, so the tree is a Merkle tree a scrub can verify). The price lands on every overwrite: a 4 KiB write drags its 16 KiB ancestors up the tree (10-14x amplification on Btrfs), a random 8 KiB write on ZFS's 128 KiB records pays 16x read-modify-write, and fragmented free space past 80-90% full degrades the allocator. Btrfs, ZFS, and APFS are three implementations of that one rule, with three answers to "which blocks get checksummed."

## Inverting the journal: the commit becomes one pointer

Ext4 (chapter 03) made a deal with the medium: overwriting in place is cheap, torn writes happen, so write a redo log — the journal, ~128 MB by default, committed every 5 s — and repair at mount by replaying. The deal works, but recovery is O(journal size) and every metadata change pays a second write into the log.

The copy-on-write alternative changes the deal: **any block reachable from the published root is immutable.** To change a file you allocate fresh blocks for the data, write a fresh copy of the leaf that describes the file (now pointing at the new data), write fresh copies of the nodes above that leaf, and finally write a new superblock that points at the new root with a higher generation number. That superblock write is the commit — the only step that must be atomic, and it is one 4 KiB block, the smallest unit the device writes semi-reliably (the torn-write window of chapter 01 still exists; the checksums and the copies below absorb it).

```text
 STEP 1 — the write lands in FREE space; nothing published changes:

 super{root=@A, gen=1000}   <-- still THE published filesystem
        |
        v
   [ A ] root node, 16 KiB                       new blocks, unreachable until step 2:
   /    \                                          +--[ D ] 4 KiB: the new file data
 [X]    [Y] leaves, 16 KiB                       /
  |      \                           [ Y2 ] 16 KiB: copy of Y, f's extent now -> D
  |       +--> f's extents          /
  |                      [ B ] 16 KiB: copy of A, Y swapped for Y2
  |
  +-- X's subtree: shared by old and new trees, 0 writes

 STEP 2 — the commit, the only atomic step:

 super{root=@B, gen=1001}   <-- ONE 4 KiB write = the whole commit

 Crash between the steps? B, Y2, D are garbage behind an unpublished root.
 Recovery: read the superblock, mount the newest valid generation. Done.
 Snapshot: keep super{root=@A, gen=1000} alive after step 2 -> a frozen
 filesystem for 0 data bytes.
```

The atomic-write plumbing differs per system but the idea is identical: Btrfs rotates among its superblock copies (parked at offsets 64 KiB, 64 MiB, and 256 GiB into the device — the first two always exist, the third on devices larger than 256 GiB) so one torn write can never kill the only copy; ZFS writes a new uberblock — a few hundred bytes — into a ring of 4 KiB slots in four labels (two at each end of every disk), and at import it picks the slot with the highest transaction number whose checksum verifies. Recovery is O(1): read labels, pick a root, mount. Ext4's replay is O(journal size) — seconds; ext2's fsck was O(file system) — the 20-minute boots on 1 TB from chapter 03.

This is module 11 chapter 04's write-ahead log, inverted. A database says "log the change now, apply it later." A CoW file system says "build the new version off to the side, publish it with one pointer write — there is nothing to replay." It is the persistent-data-structure trick (path copying into an immutable tree), already met in RAM as module 01 chapter 03's `MAP_PRIVATE` copy-on-write: never modify the shared original, build a private branch.

## What never-overwriting buys: 0-byte snapshots, one-read recovery, whole-path checksums

**Snapshots are retention, not copying.** A snapshot is a name for an old root pointer plus a reference count on every extent it reaches. Creating one writes a few KiB of metadata — the data cost is exactly 0 bytes, which is why 100 snapshots of a 2 TiB pool cost the same as 1, and why file systems that would never attempt "copy 2 TiB" hand you an hourly snapshot. Time Machine's local snapshots and the camera-roll "restore yesterday's version" features sit on this primitive. `zfs send` and `btrfs send` extend it across machines: diff two roots, stream only the blocks that differ — an offsite backup of a 2 TiB dataset with 10 GB of daily churn moves 10 GB, not 2 TiB.

**Recovery is one read.** Note what is *missing*: no journal area to size, no replay code, no commit interval defining the dirt window. There is still a dirt window (data acknowledged to the page cache but not yet in a published root — chapter 02's story, unchanged), and for *synchronous* writes ZFS reintroduces a small log anyway, the ZIL, in section 5 below.

**Checksums become structural.** In an in-place file system, a block's checksum has an awkward home: inside the block it covers, rewritten and torn with it. CoW solves the placement: the parent is being freshly written anyway, so it can carry each child's checksum. ZFS does this literally — every block pointer (address, size, birth transaction, checksum) lives in the parent node, making the tree a Merkle tree anchored at the uberblock. Btrfs hybridizes: metadata nodes self-checksum in their headers, while data checksums live in a dedicated csum tree keyed by extent, one crc32c per 4 KiB. Either way every read is verifiable end to end, and a **scrub** — a scheduled walk of every path, `btrfs scrub` / `zpool scrub` — is the only mechanism that finds bit rot *before* a read hits it. The measured reality of rot: HDD datasheets quote one uncorrectable read error per 10^14-10^15 bits (one per ~12-125 TB read), and array-scale studies report silent corruption on the order of one file per a few thousand per year — CERN's 2007-2009 multi-petabyte measurements are the classic citation; treat the rate as a range, not a constant. Without whole-path checksums, that rot is invisible forever.

Repair, though, is not free: a scrub can only *fix* what has redundancy (a mirror, RAID1, RAIDZ). On a single-device Btrfs or ZFS pool, scrub detects and reports; the corrupted extent still dies. Checksums buy detection; redundancy buys survival.

## What it costs: every small write drags its ancestors

The bill arrives on overwrite. Run the arithmetic for a 4 KiB in-place overwrite of a file on Btrfs, with the file tree at the common depth for a moderate volume (a root node over leaves — depth 2; large volumes reach depth 3):

- 4 KiB: the new data block (fresh allocation, of course)
- 16 KiB: the new leaf (Btrfs node size, the default)
- 16 KiB: the new root node
- 4 KiB: the rotated superblock copy

Total: ~40 KiB of writes to publish a 4 KiB change — 10x amplification at depth 2, ~56 KiB (14x) at depth 3, before compression and before checksum-tree updates. Ext4's ordered mode pays roughly the data block plus 2-3 journal blocks — O(1) metadata per update, versus O(tree depth) full-block rewrites. That is the small-file overwrite tax, and it is the mechanism behind the folklore that CoW file systems are bad database substrates: fixed-size random overwrites on ZFS versus ext4 measure 2-5x slower (the fio job in the lab below reproduces it on real hardware).

ZFS adds a second amplifier: the **recordsize**. ZFS allocates variable-size records up to a per-dataset maximum, 128 KiB by default. A random 8 KiB write inside a 128 KiB record cannot partially overwrite — CoW rewrites whole blocks — so the pool must read the entire 128 KiB record, splice in the 8 KiB, and write a new 128 KiB record: 16x write amplification and 16x the read traffic, per logical page. (This is quiz question 3, and the PostgreSQL story in the next section.)

The third cost is **fragmentation**, and it compounds. Overwrites must go wherever free space is; over years, freedom is scattered, so files shred — a long-lived Btrfs file that started as 3 extents can show thousands after sustained overwrites, and every read fans out. Worse, the *allocator* degrades: ZFS tracks free space in per-disk metaslabs (range trees of free segments); as the pool fills past ~80%, segments are small and numerous, the extent search lengthens, and allocation CPU spikes while write latency goes erratic — the "ZFS goes weird at 90% full" folklore, now a mechanism. The folk rules: stay under 80% full on older OpenZFS, 90% on 0.8+ where the metaslab code was rewritten. Btrfs has its own version of the spiral — ENOSPC from exhausted metadata chunks while `df` still shows data space free — and the fix, `btrfs balance`, is in the lab.

One synergy and one insult from chapter 01: CoW *likes* flash — fresh-block writes avoid the FTL's read-modify-erase cycle (the WAF-64 worst case), and CoW's freed blocks pair with TRIM. But the stacking is real too: the file system fragments logically, then the FTL fragments physically — two log-structured allocators, each hiding the other's assumption of contiguity.

## Btrfs: everything is a B-tree

The name is the design: B-tree file system. Every metadata structure — file trees, extent back-references, the checksum tree, free space, device mapping — is a copy-on-write B-tree, which is exactly module 11 chapter 02's B-tree married to module 01 chapter 03's copy-on-write. Nodes are 16 KiB by default; the root tree maps subvolume IDs to tree roots.

**The subvolume is the third thing.** Not a directory (it can be mounted on its own, snapshotted, and deleted as one atomic subtree), not a partition (it has no size and no dedicated space — all subvolumes draw from the same pool). It is a named tree root with its own ID: 5 for the top-level subvolume, and 256 and up for every subvolume or snapshot you create. Snapshots are just read-only or writable subvolumes that start life sharing 100% of another subvolume's extents.

**Reflink is per-file cloning.** `cp --reflink=always` clones one file by bumping the extent reference counts: a 10 GB copy writes 0 data bytes and returns in milliseconds. `btrfs filesystem du` makes the sharing visible as *exclusive* versus *shared* bytes — the lab proves the snapshot claim with it. The same refcounting is why deleting a snapshot frees nothing until all other holders of an extent are gone (quiz question 1).

**Checksums**: crc32c is the default precisely because x86 CPUs have had a hardware CRC32C instruction since SSE4.2 (2008) — the per-4-KiB checksum costs ~nothing on the hot path; xxhash64, sha256, and blake2b arrived in the kernel 5.5-5.6 window. **Compression** is equally first-class: zlib, lzo, or zstd (levels 1-15, default 3, via `compress=zstd:3`). Text-like data typically compresses ~2:1, and the counterintuitive result is that *reads get faster*: zstd decompression runs on the order of 1-3 GB/s per core, which outruns a 7,200 rpm HDD (150-250 MB/s outer track) and even a SATA SSD (550 MB/s). The `compress=` heuristic skips incompressible extents (quiz question 4); `compress-force=` tries everything.

**The RAID history** is the insider scar tissue. Btrfs's parity RAID (5/6) writes the parity update non-atomically across devices — the write hole at the file-system layer — and was marked "unstable" for roughly a decade; the raid-stripe-tree design, posted as patchsets from 2021 and merged as experimental in Linux 6.14 (March 2025), finally records each stripe in a B-tree so a crash mid-parity-write can be repaired rather than trusted. Ten years of "don't use btrfs RAID5" in every operations channel, in one sentence.

## ZFS: the pool, the two caches, and the recordsize trap

**The pool inverts the volume.** All vdevs — single disks, mirror pairs, RAIDZ groups — merge into one pool of blocks, and datasets (file systems) and zvols (block devices) are carved from it with no fixed size unless you set a quota. A zvol is implemented as a file inside the pool: "the volume is a file inside the pool." A full pool fails everyone at once — the sharing is the feature and the blast radius.

**The ARC is the page cache done properly.** Chapter 02's page cache is the heap in front of the disk; ZFS brings its own replacement policy, the Adaptive Replacement Cache (Megiddo and Modha, FAST 2003): an MRU list and an MFU list, each with a ghost list tracking evicted blocks — ghost hits automatically shift the MRU/MFU split. Linux's page cache keeps its active/inactive LRU pair (recency only, hand-tuned) — the contrast worth naming. Above the ARC sit two device tiers with opposite jobs: **L2ARC** is a warm read cache on SSD, filled from ARC evictions, persistent across reboots since OpenZFS 0.8; the **SLOG** is the opposite of what people assume — see below.

**The ZIL is the fsync accelerator, and the SLOG misunderstandings are legendary.** Only synchronous writes (fsync, O_DSYNC, NFS replies, database commits) touch the ZIL — the ZFS Intent Log. A sync write is recorded as an intent (data embedded in the log for small writes, or written straight to its final location with only a pointer logged for large ones), the record is made durable, fsync returns — and the real copy-on-write happens later, at the next transaction group commit (TXG; `zfs_txg_timeout` defaults to 5 s). This is a textbook WAL from module 11 chapter 04 — quiz question 8 makes you map it. Two insider facts about the SLOG, the device that hosts the ZIL: (1) it is a write cache, *never* a read cache — its contents are read only once, at pool import after a crash. (2) Two failure cases: if the SLOG dies while the system keeps running, no data is lost (pending records are still in RAM; the next TXG writes them); if the *machine* crashes and the SLOG is lost at that moment, the acknowledged sync writes that lived only in the ZIL are gone — which is why real SLOGs are mirrored, and why the sizing folklore is ~2x (peak sync-write rate x 5 s): 100 MB/s of sync writes wants ~1 GB, mirrored.

**recordsize is the knob everyone gets wrong.** The 128 KiB default is right for large sequential files and catastrophic for fixed-page random workloads: an 8 KiB page write pays the 16x read-modify-write from section 3, and a random 8 KiB *read* drags 128 KiB through the ARC. PostgreSQL writes 8 KiB pages (BLCKSZ = 8192), so the folk advice is recordsize = 8 KiB (exact page match) or 16 KiB (two pages — this module's number); the principle is block-size-matches-page-size, the same matching rule as module 11's buffer pool. The migration gotcha: **recordsize applies to newly written records only** — set it on the dataset *before* loading data; existing records keep their old size until rewritten.

**RAIDZ** closes the write hole that Btrfs lived with for a decade: every write is a full-stripe write of variable width (data plus 1, 2, or 3 parity blocks, RAIDZ1/2/3), committed as one CoW transaction, so there is never a half-updated parity block to repair. The tax is on the read side: a random record read touches the whole stripe. **Scrub** and **resilver** are the maintenance loop — `zpool scrub` monthly is the folk cron — and `zfs send -I snapA snapB | zfs recv` is the delta-backup primitive.

## APFS: the checksum Apple chose not to write

Apple replaced HFS+ in 2017 with a CoW design cut for flash and phones. **A container holds multiple "volumes," but a volume is just a tree in the container's shared space** — no fixed size (quotas optional), space allocated thin from one pool. That flexible container is also what makes Fusion drives work: one container spanning an SSD and an HDD, with hot files migrating toward the SSD tier.

**Cloning is per-inode CoW.** The `clonefile()` system call (macOS 10.12+), Finder's Duplicate, and `cp -c` produce a 0-byte copy of a file — the same "bump the refcount" move as Btrfs reflink, one level of granularity coarser than ZFS's block sharing. **Snapshots** are volume-level root pointers; Time Machine's local snapshots (hourly, thinned automatically under space pressure — deleted snapshot space reappears as "purgeable") are the visible product.

**No journal.** The consistency story is the checkpoint flip from section 1: APFS commits by atomically swapping checkpoint superblocks and picks the newest valid one at mount. The on-disk format carries a small vestigial journal area left over from the design, but metadata updates do not ride a redo log — the flip is the whole story, the same recipe as Btrfs and ZFS.

**The decision people argue about: metadata is checksummed (Fletcher-family checksums in object headers), data blocks are not.** The argument for: a per-block data checksum means a second write per data block (the checksum or its parent must be written with the data — the whole CoW metadata path from section 3), paid on every write against phone flash where every write is endurance and latency; and NAND already carries strong per-page ECC inside the FTL, so the medium itself is covered. The argument against: everything *outside* the NAND — controller DRAM, cables, host bugs — is exactly where silent corruption lives, and APFS is blind to it forever. Quiz question 7 is the incident that separates the positions: a deliberate engineering trade, criticized and defended in the same breath.

The chapter's contract, closed as a table — the journaling lineage of chapter 03 against the CoW lineage of this one:

| | ext4 (journaling, ch. 03) | Btrfs / OpenZFS 2.x | APFS |
|---|---|---|---|
| Cost of a 4 KiB fixed overwrite | data block + ~2-3 journal blocks (O(1) metadata) | data + 16 KiB x tree depth + root: 10-14x | data + metadata path (same CoW physics) |
| Snapshot | not a feature (LVM snapshots aside) | 0 data bytes, unlimited count, send/receive deltas | 0 data bytes (Time Machine) |
| Crash recovery | journal replay, O(journal size), seconds | read newest valid root: 1 read, no replay | read newest valid checkpoint: 1 read |
| Checksum story | metadata CRC (metadata_csum); data unchecked | data + metadata, whole path, scrubbable, repairable with redundancy | metadata only — data unchecked by design |
| Fragmentation risk | moderate (delayed allocation mitigates) | grows with overwrite churn; 80-90% full cliff | grows (same physics) |
| Choose it for | databases, OS disks, page-random writes | snapshots, backups, reflink clones, compressible bulk; databases only with recordsize tuned | the macOS default; device ECC is the integrity story |

## What insiders know

- The three dirt windows, as numbers: ext4 commits its journal every 5 s (`commit_interval`, chapter 03); ZFS flushes a transaction group every 5 s (`zfs_txg_timeout`); Btrfs defaults to a 30 s commit interval (`commit=` mount option). Three different defaults for "how much acknowledged-but-unpublished work can vanish."
- Btrfs data checksums do not live in the file tree at all — a separate per-file-system csum tree holds one crc32c per 4 KiB of data, shared across subvolumes' extents via back-references. Metadata nodes self-checksum in their own headers.
- Scrub detects; redundancy repairs. On a single-device Btrfs or ZFS pool, `btrfs scrub` and `zpool scrub` report hundreds of errors and fix zero — the checksum can only tell you the extent is dead. Mirrors, RAID1, RAIDZ are the repair path.
- Btrfs ENOSPC while `df` shows free space is the classic ops incident: data chunks have room, metadata chunks are exhausted (the `df` number only knows about data). `btrfs fi usage` shows the chunk split; `btrfs balance start -musage=50` reclaims it. The ZFS 90% cliff and this are the same physics with different plumbing.
- `recordsize` (ZFS) and node size (Btrfs, `mkfs.btrfs -n`) are set-at-write-time properties: ZFS applies recordsize to new records only — tune the dataset before loading data, not after the slowdown ticket arrives.
- SLOG sizing folklore: roughly 2x (peak sync-write MB/s) x 5 s TXG window, mirrored; 10-16 GB covers all but the most fsync-critical workloads, and the device is never read while the system is up.
- `cp -c` on macOS and `cp --reflink=always` on Linux/Btrfs are the user-space doors to the same primitive; GNU cp's `--reflink=auto` (the quiet default when `--reflink` is passed) silently falls back to a full copy on non-CoW file systems — the copy that "finished instantly" on ext4 didn't.

## See it yourself

The full lab needs root and 3 GB of disk, all inside a loop file. On a box without btrfs, the ZFS and APFS walks below are the documented half.

```console
$ truncate -s 2G /tmp/cow.img
$ mkfs.btrfs -L cow /tmp/cow.img
$ sudo mount -o loop,compress=zstd:3 /tmp/cow.img /mnt/cow
$ sudo btrfs subvolume create /mnt/cow/sv
$ sudo dd if=/dev/urandom of=/mnt/cow/sv/rnd   bs=1M count=100 status=none
$ sudo dd if=/dev/zero     of=/mnt/cow/sv/zeros bs=1M count=100 status=none
$ sudo btrfs subvolume snapshot /mnt/cow/sv /mnt/cow/snap
$ sudo dd if=/dev/urandom of=/mnt/cow/sv/rnd bs=1M count=10 oflag=append conv=notrunc status=none
$ sudo btrfs filesystem du -s /mnt/cow/sv /mnt/cow/snap
```

Expected (abridged; btrfs-progs 6.x on a 2 GiB image — your KiB values will differ slightly, the shape will not):

```text
     Total   Exclusive  Set shared  Unshared
 110.00MiB   10.00MiB   100.00MiB   10.00MiB  /mnt/cow/sv
 100.00MiB    0.00B     100.00MiB    0.00B    /mnt/cow/snap
```

Read it as the refcount table: the snapshot holds 100 MiB, 0 of it exclusive — it shares every block with the live subvolume, whose 10 MiB exclusive is the append after the snapshot. That is "snapshots cost 0 bytes," measured.

The reflink timing and the compression report:

```console
$ time cp --reflink=always /mnt/cow/sv/rnd /mnt/cow/sv/rnd.clone
0.05s ...        # 0 data bytes: extent refcount bump only
$ time cp --reflink=never /mnt/cow/sv/rnd /mnt/cow/sv/rnd.copy
1.7s ...         # 100 MiB actually written
$ cp --reflink=always /etc/passwd /tmp/p    # on ext4 or tmpfs
cp: cannot clone ... Operation not supported  # the feature test: ENOTSUP on non-CoW
$ sudo compsize /mnt/cow/sv
Type  Perc  Disk usage  Uncompressed  Refs  Total
zstd  0.2%  ~0.3MiB     100.00MiB     1     100.00MiB   <- zeros
none  100%  100.00MiB   100.00MiB     1     100.00MiB   <- random: heuristic skipped it
```

The zeros file occupies a few hundred KiB (ratio printed >200:1); the random file is stored raw because the `compress=` heuristic sampled it and declined — `compress-force=zstd:3` would try anyway and still store it raw (you cannot compress entropy).

The CoW-versus-journal write cost, if you have a real spare device (loop images shrink the gap to ~1.5-2x because the host file system absorbs the CoW cost; on real hardware expect the 2-5x of section 3):

```ini
; cow.fio — fixed-size random overwrites, CoW's worst case
[job]
filename=/mnt/scratch/f
rw=randwrite
bs=4k
size=1g
direct=1
ioengine=io_uring
iodepth=32
runtime=30
time_based=1
```

Run the identical job against an ext4 mount and a btrfs mount; compare IOPS. Watch btrfs's number collapse when the device is 90% full — section 3's cliff, measured.

The ZFS walk (documented; needs a spare disk or file vdev):

```console
# zpool create tank /dev/sdb                 # every disk becomes one pool
# zfs create tank/pg
# zfs set recordsize=16k compression=lz4 tank/pg   # set BEFORE loading data
# zfs get recordsize,compression tank/pg
NAME     PROPERTY     VALUE
tank/pg  recordsize   16K
tank/pg  compression  lz4
# zpool scrub tank                            # the whole-tree checksum walk
# zdb -L tank | head -20                      # zdb lite: on-disk truth (txg, datasets)
```

The APFS walk (macOS, read-only):

```console
$ diskutil apfs list                  # container + volume trees; no fixed volume sizes
$ diskutil apfs listSnapshots /       # Time Machine's retained root pointers
$ time cp -c big.mkv clone.mkv        # the 0-byte clone
$ time cp big.mkv full.mkv            # the real copy, for contrast
```

## How to use this knowledge

- **File system selection by write shape.** Page-random workloads (databases, WALs) on defaults: journaling wins by 2-5x, or ZFS with recordsize=8-16 KiB closes the gap. Immutable bulk plus snapshot and clone needs: CoW wins outright — a nightly "full backup" becomes a snapshot (0 bytes) plus a delta send.
- **Capacity planning with the cliff in it.** ZFS pools are planned at 80-90% maximum fill; Btrfs volumes get the same budget plus a scheduled `btrfs balance` check. The "slow and weird at 90%" incident is an allocator-degradation diagnosis, not a mystery.
- **The corrupt-after-crash triage.** "The file is the right size but a region is garbage": ask which file system. On ZFS, a read error or scrub report names the block; on APFS, nothing will ever name it — the data path is unverified by design; on ext4 writeback mode (chapter 03), the file can contain stale blocks by an equally deliberate choice. Three designs, three failure envelopes.
- **Backup architecture.** Snapshot + send/receive (Btrfs and ZFS both) turns backups from O(total data) into O(changed data) with point-in-time rollback as a free byproduct — 2 TiB versus 10 GB nightly.
- **The two interview traps, derivable in 60 seconds.** "Why are snapshots free?" (a root pointer plus refcounts) and "why is CoW slow for databases?" (10-14x metadata-path amplification plus 16x record read-modify-write) — both from section 1 and section 3 arithmetic, which is what interviewers want to see.

## Connections

- **Chapter 01**: the FTL is a log-structured allocator with a garbage collector; a CoW file system stacked on it is a second one — both avoid in-place rewrites, both need TRIM, and their two fragmentation layers multiply.
- **Chapter 02**: the page cache is the heap in front of the disk; ZFS's ARC is the same role with a hit-rate-adaptive policy instead of Linux's LRU pair, and fsync's guarantee is what the ZIL implements.
- **Chapter 03**: the journaling lineage is the competing crash-consistency design — every number in this chapter's closing table is that chapter's subject.
- **Chapter 05**: overlayfs copy-up is CoW at file granularity — the container world's version of this chapter's refcounting.
- **Module 11, ch. 01**: fsync and the durable-ack contract — the ZIL is its implementation; **ch. 02**: B-trees are Btrfs's skeleton and ZFS's metaslab index; **ch. 04**: the WAL, inverted by the root-pointer commit and replayed by the ZIL/TXG pair.
- **Module 01, ch. 03**: `MAP_PRIVATE` copy-on-write is the same never-modify-the-shared-original trick in RAM.
- **Module 15 (queued)**: container image layers and snapshot-based rollback — this chapter's refcounted-extent model is the substrate.

## Quiz

1. A 2 TiB Btrfs subvolume accumulates 100 read-only snapshots over a year. (a) How many data bytes do the 100 snapshot operations themselves write? (b) Since the newest snapshot, the live subvolume has appended 10 GB to existing files, overwriting nothing. You delete that newest snapshot — how much space frees?

2. ZFS stores each block's checksum in the *parent's* block pointer rather than inside the data block itself. Name the corruption this catches that a self-checksummed block would not, and explain why copy-on-write is what makes the parent-side placement possible.

3. OpenZFS 2.x, defaults: recordsize=128k, single-disk pool, ARC miss on the record. A database issues one random 8 KiB page write with fsync. To the nearest order of magnitude, how many bytes does the pool read and write before the fsync returns, and what is the write amplification factor versus the logical 8 KiB?

4. On Btrfs with `compress=zstd:3` mounted, you create one 100 MiB file of `/dev/urandom` output and one 100 MiB file of zeros, then run `compsize` on both. What does each file report for type and disk usage, and what would `compress-force=zstd:3` change?

5. PostgreSQL (8 KiB pages) moves from ext4 to a fresh OpenZFS 2.x pool with every default left alone. Random UPDATE throughput settles at about a third of the ext4 number, and `zpool iostat` shows ~16 bytes read for every 1 byte written. Diagnose the mechanism and give the fix — including the step people forget.

6. A Btrfs root filesystem on a 500 GB SSD at 93% full: a freshly written 200 MB file shows ~4,000 extents in `filefrag -v`, and one day `cp` fails with ENOSPC while `df -h` reports 40 GB free. What are the two distinct mechanisms, and what is the fix?

7. A Mac laptop loses power mid-write of a heavily fsynced APFS file. After reboot the file's size and mtime are correct, but a 4 KiB region in the middle is zeros. The same event on the same hardware under ZFS would be caught on the next read or scrub. Which APFS design decision explains the difference, and what is the argument for it — and the argument against?

8. Module 11 chapter 04's WAL recipe: write a redo record, make it durable, ack the client, apply to pages later, checkpoint to truncate the log. Map ZFS's synchronous-write path onto all five steps (name the ZIL, the TXG, and the uberblock flip in their roles). Then answer: why doesn't ZFS simply perform the root-pointer commit on every fsync and skip the log entirely?

<details><summary>Answers</summary>

1. **(a) 0 data bytes.** A snapshot is a new tree-root record pointing at extents already on disk; it writes a few KiB of metadata and bumps reference counts — nothing is copied, whether there is 1 snapshot or 100. **(b) ~0.** Deletion frees only extents whose reference count reaches zero. The appended 10 GB belongs to the *live* subvolume (the snapshot predates it, so it never referenced those extents), and every extent the snapshot holds is still live, because nothing was overwritten. The lesson: snapshot deletion pays only when overwrites or deletions created exclusively-held old versions.

2. A self-checksummed block verifies its own contents, not the pointer that brought you there. If metadata corruption, a lying device, or an FTL bug (chapter 01) delivers a valid block belonging to another file — self-consistent, checksum passing — the read "succeeds" with wrong bytes. With the checksum in the parent, the expected value travels a different path than the data, and each parent is checksummed by *its* parent up to the uberblock — one root of trust, so a misdirected read fails the comparison. CoW makes the placement possible because the parent is freshly written whenever the child changes: the checksum has a versioned home with no in-place update.

3. Read side: the full 128 KiB record — you cannot splice 8 KiB into half a record. Write side: a new full 128 KiB record (CoW), plus its metadata path, plus the ZIL intent record (~8-16 KiB; small sync writes are embedded) that lets fsync return early. So ~128 KiB read + ~128 KiB written per 8 KiB logical change — a 16x write amplification (128/8), worse after metadata, versus ext4's ~8 KiB data plus 2-3 journal blocks. This arithmetic at scale is the 2-5x measured gap of section 3.

4. The random file: type `none`, ~100 MiB on disk — the `compress=` heuristic samples each extent, sees no compressibility, stores it raw (compressing entropy wastes CPU and space). The zeros: type `zstd`, ~0.3 MiB — a printed ratio >200:1, since long zero runs reduce to a few bytes of length coding. `compress-force=zstd:3` disables the heuristic and attempts every extent: zeros unchanged, random attempted and still stored raw. The keeper is the read-side surprise: compressed data can read *faster*, because 1-3 GB/s of zstd decompression outruns the disk.

5. Mechanism: recordsize=128k against 8 KiB pages. Every page write is a whole-record read-modify-write (128 KiB read + 128 KiB written — the 16x of question 3, visible in the iostat ratio), and every random page read drags 128 KiB through the ARC, evicting useful pages. Fix: a dedicated dataset with `recordsize=16k` (or 8k — page match) plus `compression=lz4`, *set before loading the database*: recordsize applies only to newly written records, so tuning after the slowdown ticket arrives rescues nothing until the data is rewritten. Matched, the CoW penalty drops from the 2-5x range toward parity. A mirrored SLOG helps commit latency; it is not this fix.

6. Mechanism one — data fragmentation: at 93% full the CoW allocator finds only scattered free extents, so new files shred (200 MB / ~4,000 extents ≈ 50 KiB average, versus a handful on ext4) and every read fans out — the same physics as the ZFS 80-90% cliff, different plumbing. Mechanism two — the ENOSPC: Btrfs allocates chunks split between data and metadata; `df` sees only data usage, so exhausted metadata chunks fail while 40 GB of data space sits free. Fix: `btrfs balance start -dusage=50 -musage=50` re-flattens the chunk split and relocates fragmented data; `btrfs fi usage` shows the split. Long-term: keep it under ~80-90% full, same budget as ZFS.

7. APFS checksums metadata but not data. The checkpoint flip guarantees the *published tree* is metadata-consistent — size, timestamps, extent pointers all describe a real file — but a torn data block (the drive's volatile cache lied — chapter 02's FLUSH contract, module 11 ch. 01's lying-drive era) is published with no checksum to contradict it; ZFS would fail the read and name the block at scrub time. The argument for: a per-block data checksum is a second write per block in a CoW design, paid in flash endurance and latency, and NAND's per-page ECC already covers the medium. The argument against: the path outside the NAND (controller DRAM, cables, host bugs) is where field corruption actually lives, and APFS will never notice it.

8. **Redo record** = the ZIL intent record (the itx); **make it durable** = the ZIL blocks on the SLOG or in the pool; **ack** = fsync returns; **apply later** = the transaction group (TXG, ≤ 5 s, `zfs_txg_timeout`) performs the real copy-on-write; **checkpoint** = the TXG commit — the uberblock flip that publishes the new root and lets the group's ZIL records be discarded (log truncation). Why not flip per fsync: the flip publishes an entire tree — the full path-to-root rewrite (10-14x for one small change) plus one global commit order that serializes every writer. The ZIL is a tiny append of just the changed bytes, so the expensive commit is amortized over ≤ 5 s of everyone's writes — exactly why a database does not checkpoint per transaction (module 11 ch. 04).
</details>
