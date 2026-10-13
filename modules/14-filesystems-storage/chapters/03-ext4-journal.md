# Chapter 03 — ext4 and the journaling lineage

> **The 30-second version.** A file system is a persistent heap with a naming scheme, and the medium underneath it lies in two ways: it can only overwrite whole blocks (each ~10 ms on a spinning disk), and a crash mid-write leaves a block half-old and half-new — the torn write from module 11 chapter 01, same physics. ext2 solved the allocator half: block groups that keep a file's inode next to its data, bitmaps for free space, directories as plain files of (name, inode) pairs. It left crash consistency to fsck, a whole-filesystem walk that cost minutes per terabyte. ext4 closed that gap with the same move as module 11 chapter 04's databases — a write-ahead log: metadata lands in a circular journal first, a commit block makes it real, replay is O(journal), not O(filesystem). Along the way the pointer-per-block trees became 12-byte extents (one covers 128 MiB) and allocation moved from write time to writeback time, so the allocator finally sees the whole file before placing it. XFS took the parallel branch — B-trees everywhere, one lock per allocation group — and it is why RHEL defaults to XFS for big volumes.

## The three-part contract: allocator, index, crash story

Strip a file system to what the hardware forces. From module 14 chapter 01, the disk's rules: reads and writes happen in whole blocks (512 B logical, 4 KiB physical); a random block access on a 7,200 rpm HDD costs ~12 ms seek plus ~4.17 ms average rotation; and power can vanish between any two block writes. That last rule creates the *torn-write window*: a block being overwritten when power dies can end up half old bytes and half new bytes, and the disk offers no way to make one block write atomic. It is exactly the torn write module 11 chapter 01 derived for database pages — the device did not get more honest, only the software above it got more careful.

So any on-disk file system must deliver three things at once:

1. **An allocator** — find free blocks for a 4 KB write, in a structure that itself lives on the disk and survives crashes.
2. **A metadata index** — the name tree and each file's block map, both persistent, both updated on every write.
3. **A crash story** — after power loss at *any* instruction, the first two must be repairable to a mountable state where no file points at garbage.

The first two are module 01 chapter 05's allocator problem on a slow, block-shaped heap. The third is the new one, and it is brutal because one logical update is *many physical block writes*: appending 4 KB can touch the data block, the inode's size, a block bitmap, the group's free counts, and the directory's mtime — six updates, a crash point between each pair, 2^6 interleavings, most of them corrupting (bitmap says free, inode says allocated; size grew, extent not yet written). ext2 let any interleaving happen and repaired afterward; the journaling lineage made the interleaving irrelevant.

## ext2: block groups and the bitmap heap

ext2 (1993, Remy Card) is the base design everything in this lineage upgrades. Its first idea is *locality*: seek time dominates, so metadata should sit near the data it describes. The disk is cut into **block groups**, each sized by a derivation you can check: the group's free-space bookkeeping is one **block bitmap**, and a bitmap must fit in one block. With 4 KiB blocks that is 4,096 bytes × 8 bits = **32,768 blocks per group = 128 MiB**. The inode bitmap needs only 1 KiB (8,192 inodes at the default ratio of one per 16 KiB of file system), padded to a block. The inode table — 8,192 × 256 B = 2 MiB — and then data:

```text
one block group = 32,768 blocks x 4 KiB = 128 MiB  (sized so ONE bitmap block covers it)

 blk 0        1             2              3              4 .. 515        516 ...
+---------+-----------+--------------+--------------+-----------------------------+
| super-  | group     | block        | inode        | inode table                 | data blocks
| block   | descs     | bitmap       | bitmap       | 8,192 inodes x 256 B = 2MiB | (files land here;
| (copy)  | (copy)    | 4 KiB =      | 1 KiB used,  |         inodes 12, 13, ...  |  the allocator aims
|         |           | 32,768 bits  | padded       |         live NEAR these     |  for same-group data)
+---------+-----------+--------------+--------------+-----------------------------+
        every Nth group also keeps a backup copy of the superblock (sparse_super)
```

Create a file and the kernel picks a group, finds a free inode in that group's table, then — the payoff — allocates that file's *data blocks from the same group*: the inode and the data are one seek apart instead of a whole platter apart. A group's fixed metadata occupies ~515 of 32,768 blocks ≈ 1.6% — the source of the "~3% formatting tax" folklore once journal and backup copies are added.

**Bitmaps vs free lists.** ext2's free-space structure is a flat bitmap — the opposite of module 01 chapter 05's free-list bins (bin per size class, pop in O(1)). A bitmap answers "is block n free?" in O(1) and "find a free block near n" with a short scan; it cannot report sizes, but allocating *near a given block* is trivial — exactly what a seek-bound disk wants — and fragmentation only makes it slower, never wrong. A segregated free list answers "give me k bytes" in O(1) with no notion of *where*: place files anywhere, pay a seek per block. On disk, contiguity beats asymptotics; in DRAM the reverse.

**Directories are just files.** An ext2 directory is a regular file whose payload is an array of (inode, name length, name) records, padded to 4 B boundaries, plus the `.` and `..` entries as *real records* pointing at themselves and the parent. The namespace tree — the thing you think of as "the file system" — is data stored *inside* inodes, nothing special at the disk level. Chapter 02's hard links follow directly: two directory records, one inode, one link count.

The wall: lookup is a **linear scan** of those records. A directory grown to 1-2 MB — a 1990s mail or Usenet spool, the classic case — means reading hundreds of 4 KiB blocks and string-comparing every record. The fix, added to ext3 in the 2.6-era kernel (2003) and carried into ext4, is **htree**: once a directory's linear entries overflow one block, a B-tree index (hashed filename → block) is built inside the directory file itself, turning lookup from O(entries) into O(log blocks). The `dir_index` feature flag is on by default — pre-htree ext2 directories are why the "huge directory = slow ls" folklore exists.

## From pointer forests to extents

ext2's per-file block map is the inode's 60-byte `i_block` array: 15 four-byte block pointers — 12 direct, then one indirect, one double-indirect, one triple-indirect. With 4 KiB blocks and 4-byte pointers, each pointer block holds 4,096/4 = 1,024 pointers:

```text
inode.i_block (60 B = 15 x 4 B)          reach (4 KiB blocks)     extra reads to reach a byte
[0..11]   direct                 12 blocks = 48 KiB             0
[12]  --> 1 pointer block        1,024 blocks = 4 MiB           1 (the pointer block)
[13]  --> top ptr block          1,024^2 = 1,048,576 blocks     2 (top + one second-level)
             |-> 1,024 ptr blocks            = 4 GiB
[14]  --> top -> mid -> bottom   1,024^3 ~ 1.07 B blocks        3 (three pointer blocks)
                                        (classic ext2/3 ceiling: ~4 TiB addressed,
                                         i_size semantics capped real files lower)
```

Do the arithmetic for a 1 GiB file: 1,073,741,824 / 4,096 = 262,144 blocks. Blocks 0-11 are direct, 12-1,035 hang off the single-indirect block, the remaining 261,108 live under double indirection — 256 second-level pointer blocks plus the top one, ~1 MiB of pure pointers per gigabyte of file. A read of byte 1,073,741,813 (the file's last KiB) walks the top-level pointer block, then one second-level pointer block, then the data: **2 extra block reads before the data arrives** — 2 × ~12 ms ≈ 24 ms of pointer chasing per tail access on an HDD, on every access, forever. The historical sting was worse: early Linux defaulted ext2 to 1 KiB blocks, where a pointer block holds only 256 pointers, double indirection tops out at 64 MiB, and a 1 GiB file's tail sits under *triple* indirection — 3 extra reads, ~36 ms. And the scheme cannot tell "these 10,000 blocks are physically adjacent" from "scattered across the disk": 262,144 individual pointers record position, never adjacency.

ext4's fix replaces pointers with **extents**: a 12-byte record `(logical_start: u32, physical_start: u48, length: u16 in blocks)`. The length field is 16 bits with the top bit reserved for the "unwritten" preallocation mark, so one extent covers up to 32,768 blocks = **128 MiB of contiguous space**. A perfectly contiguous 1 GiB file is **8 extents — 96 bytes — instead of 1 MiB of pointer blocks**. The extents live in a shallow tree rooted in the inode's same 60-byte `i_block` area: a 12-byte header plus 4 × 12-byte entries, so up to 4 extents (512 MiB contiguous) need *zero* extra metadata blocks; bigger files grow depth-1 index nodes, each 4 KiB block holding ~340 entries. The tail read of that 1 GiB file becomes: inode → one extent-index block → data. One extra read instead of two (or three).

## Delayed allocation: the allocator finally sees the whole file

The second ext4 upgrade is about *when* blocks get chosen. ext3 allocated a physical block the moment `write()` was called — so the physical order of a file mirrored the *order of the writes*, interleaved with whatever else the system was doing. Write a log file while a database flushes beside it and the two files' blocks alternate across the disk: seek-per-read forever after.

ext4 instead **delays allocation until writeback**. The write lands in the page cache (chapter 02), the inode records only "size grew," and nothing on disk says where those bytes will live. When the kernel flusher — or an fsync — pushes the dirty pages, the allocator sees the *whole* set of dirty data and can place it as one near-contiguous run: a 1 GiB streaming write becomes a handful of extents where ext3 would produce thousands of scattered blocks. XFS pioneered the technique on IRIX; ext4 adopted it in 2.6.23-era work (2007-2009).

The cost is exactly the data-loss window chapter 02 told as the "ext4 ate my files" story (2006-2009): between `write()` returning and writeback, the bytes exist only in RAM, with no blocks assigned. `write(); close(); rename()` with no fsync could commit the rename's metadata while the data had no home yet — after a crash, the renamed file was zero bytes. The `auto_da_alloc` heuristic (default since 2.6.30, 2009) forces allocation on a rename-over-existing pattern precisely to close the common case; the real fix remains: fsync the file, and fsync the directory, *before* relying on the rename.

## JBD2: a write-ahead log at file-system scale

Now the crash story. Module 11 chapter 04 derived the WAL idea for databases: never mutate the on-disk structures directly — describe the change in a log first, stamp a commit record, then apply. ext4 embeds exactly that machinery as **JBD2** (the Journaling Block Device, second version; ext3 used JBD).

**The transaction flow.** Every metadata change — inode update, bitmap flip, directory entry — is staged into a running transaction in RAM. Every 5 s (the `commit=` mount option's default), when the journal ring is nearly full, or when fsync forces it, the transaction is written to the journal: a reserved region of the disk, a circular ring of 4 KiB blocks, default **128 MiB** for file systems past ~4 GiB (e2fsprogs sizes it from a ladder topping out at 32,768 blocks; smaller volumes get proportionally less):

```text
        JBD2 commit  (journal = circular ring of 4 KiB blocks, e.g. 128 MiB)

  metadata change --> running txn (RAM): stage old+new copies of each meta block
  trigger: 5 s timer (commit=5) | journal nearly full | fsync()
      1. write descriptor block  -> [desc: txn 571, lists the next n blocks]
      2. write metadata blocks   -> [meta: inode 12 new copy][meta: bitmap][meta: dir]
      3. CACHE FLUSH             -> the log so far must be on the platter/NAND
      4. write commit block      -> [commit: txn 571, seq + CRC when journal_checksum is on]
      5. CACHE FLUSH             -> commit block durable == the transaction "happened"
                                   checkpointing: txn's blocks written to their home
                                   positions in the fs proper --> [inode][bitmap][dir]
                                   journal space reusable once checkpointed
```

**Replay.** At mount, the kernel walks the ring and replays every transaction with a valid commit block, ignoring the possibly-torn tail — recovery is O(journal): 128 MiB of log plus checkpoint writes, seconds. ext2's fsck instead walked every inode, bitmap, and directory of the whole volume to reconstruct consistency — the folklore 20 minutes per TB on 2000s-era hardware, per crash, on every server, which is why journals won. (fsck still exists for real corruption; it just stopped being part of every boot.)

The commit-block ordering also dissolves the 2^6 interleaving problem from section 1: bitmap, inode, and directory updates now land *together*, inside one transaction whose commit makes all of them real at once, before any of them is checkpointed into place.

**Three modes — what gets journaled.** The `data=` mount option decides whether file *data* also goes through the log:

- `data=journal`: data blocks and metadata both into the journal. Safest — a crash can lose only the final seconds of the log — but every byte is written twice (log, then checkpoint home), so large writes cost ~2x the I/O.
- `data=ordered` (the ext4 default): only metadata is journaled, but at commit time JBD2 first forces *all dirty data blocks* of the affected files out to their final locations, then commits the metadata that points at them. The ordering is the guarantee: whatever the metadata says after recovery, the blocks it points at already hold that file's data — you may lose the tail of a file (its size update never committed), but a file can never contain another file's bytes.
- `data=writeback`: only metadata journaled, no data ordering. Fastest, and the dangerous one: metadata can commit pointing at freshly allocated blocks whose data was never written — after a crash, a mailbox can contain the bytes of a previously deleted file. Distributions that shipped writeback for speed in the 2000s generated exactly those bug reports.

**The numbers insiders quote.** The default commit interval is 5 s: up to 5 seconds of completed-but-uncommitted updates (plus whatever the drive's volatile cache was hiding — chapter 02) can vanish on power loss. `commit=1` narrows it to ~1 s at the price of up to 5x more commit traffic. Journal size ~128 MiB means a sustained write burst of metadata exceeding that ring between checkpoints forces the writers to *wait for checkpointing* — the "journal wraps under metadata storm" stall. And fsync on ext4 is priced as: data flush (ordered mode) + one forced journal commit + two cache flushes ≈ millisecond scale on NVMe, 10-30 ms on HDD.

## XFS, the sibling — and the allocator taxonomy

ext4 is not the only lineage. XFS (SGI, 1993, ported to Linux 2001) took the same three-part contract and answered every index question with B-trees — module 11 chapter 02's fanout arithmetic returns as the design principle. The volume is split into **allocation groups**, each a self-contained slice with its own allocation state and locks: 4 AGs let 4 CPUs allocate concurrently (a default mkfs.xfs on a typical server volume reports `agcount=4`, more on multi-TB ones). Inside each AG, free space is indexed by *two* B+trees — keyed by block number and by extent *size* — so "find the best-fit free extent" is a tree descent, not a bitmap scan. Per-inode block maps and inode allocation are B+trees too. The linear-directory wall never existed here; XFS directories were B-trees from day one.

XFS also pioneered delayed allocation (ext4 borrowed it), shipped the first credible online defragmenter — `xfs_fsr`, which rewrites a fragmented file contiguously by copying to a temp file and atomically swapping the extent maps — and parallelizes across AGs for many-files-at-once workloads (tar extraction, mail servers, HDFS datanodes) that ext4's per-group design serializes more. RHEL has defaulted to XFS since RHEL 7 (2014) and recommends it for big storage; Debian/Ubuntu default to ext4 for OS disks, where repair tooling and 25-year compatibility dominate. The judgment is parallel allocation at scale versus the conservative default.

The allocator taxonomy, one table:

| file system | free-space index | file block map | directory index | allocation locking | crash story |
|---|---|---|---|---|---|
| ext2 | per-group bitmaps | 12 direct + 3-level pointer tree | linear (name, inode) scan | per-group | fsck: O(fs), minutes/TB |
| ext3 | per-group bitmaps | pointer tree + journal | htree (B-tree of hashed names) | per-group | JBD journal, O(journal) |
| ext4 | bitmaps + mballoc buddy cache | extent tree (12 B extents, up to 128 MiB each) | htree | per-block-group locks | JBD2 + delayed allocation |
| XFS | two B+trees per AG (by-size, by-block) | per-inode extent B+tree | B+tree | per-AG locks | journal (WAL, same idea) |
| Btrfs / ZFS | CoW allocators, free space in trees/metaslabs | extent ref-count trees | B-trees | per-CPU + range locks | root-pointer flip (chapter 04) |

Chapter 04 covers that last row: the journaling lineage *accepts* overwrite-in-place and repairs after the crash; the copy-on-write lineage never overwrites a live block at all, making the commit a single atomic root-pointer write — the WAL insight inverted.

## What insiders know

- A file with 4 or fewer extents (≤ 512 MiB contiguous) has its entire block map in the inode itself — zero metadata reads beyond the inode. `filefrag -v` shows 1-3 extents on a fresh streaming write, hundreds on a long-lived log file. The count *is* the fragmentation diagnosis.
- The journal is 128 MiB *no matter the file system size* (past ~4 GiB): ~0.01% of a 10 TiB volume. It bounds commit latency, not capacity — a metadata burst that outruns checkpointing wraps the ring and stalls writers, the signature of the `commit=600` misconfiguration copied from stale tuning guides.
- You cannot remount your way between `data=` modes — the mode is fixed at mount time (the `tune2fs -o journal_data` hint only sets the *next* mount's default). Some admins still mount a *separate* small ext4 volume `data=journal` for databases-in-files: the log turns random 4 KB metadata updates into sequential log writes, worth the 2x data cost only there.
- `debugfs -R "lsdel" <device>` lists inodes deleted but not yet overwritten — the window behind ext3-era "undelete" folklore: the blocks are free but their bytes survive until reallocated, and the listing is only trustworthy on an unmounted image.
- The `dir_index`/htree threshold is one directory block: linear below ~4 KiB of entries, B-tree above. The residual slow-directory case today is a directory that predates the flag or was built by a tool writing entries in hash order.
- ext4's on-disk checksums (`metadata_csum`) cover metadata — journal and inode tables — but not file *data*; a rotted data block reads back silently wrong. That gap is chapter 04's whole argument for checksum-first file systems.

## See it yourself

Any Linux box with sudo (or a VM); ~15 minutes. Everything below is read-only except the loop mount.

```bash
# 1. Read your own ext4 superblock — every line decoded:
sudo dumpe2fs -h /dev/nvme0n1p2 | grep -E 'Block size|blocks per group|Inodes per group|Inode size|Journal|Filesystem features'
```

```text
Filesystem features:  has_journal ext_attr resize_inode dir_index filetype extent
                      64bit flex_bg sparse_super large_file huge_file dir_nlink
                      extra_isize metadata_csum     <- journal (JBD2), extents, htree dirs, checksums: all visible
Block size:           4096                          <- do the derivation: 4096 x 8 bits = 32,768 blocks/group
Blocks per group:     32768                         <- = 128 MiB per group
Inodes per group:     8192                          <- 128 MiB / 16 KiB per inode, the default ratio
Inode size:           256                           <- 256 B x 8,192 = 2 MiB inode table per group
Journal size:         128M                          <- the 32,768-block ladder ceiling (e2fsprogs wording varies)
```

```bash
# 2. Watch one inode's extents (root's reserved inode 12 is often a journal; pick a real file):
sudo debugfs -R "stat <13>" /dev/nvme0n1p2        # inode 13: usually /lost+found or an early file
sudo debugfs -R "blocks <13>" /dev/nvme0n1p2      # the raw block list
sudo debugfs -R "lsdel" /dev/nvme0n1p2            # deleted-but-not-reclaimed inodes (forensics window)
```

```text
Inode: 13   Type: regular    Mode:  0644   Flags: 0x80000 (extent)
Size: 1073741824   Blocks: 2097152
EXTENTS:
(0-32767): 352256-385023, (32768-65535): 385024-417791, ...
```

Each `(logical range): physical range` line is one 12-byte extent record; a length of 32,768 blocks is the 128 MiB maximum. `Flags: 0x80000` is the "this inode uses extents" bit — the flag a pre-ext4 inode lacks.

```bash
# 3. Measure fragmentation on a small file vs a big streaming file:
filefrag -v /etc/passwd
dd if=/dev/zero of=/var/tmp/big.bin bs=1M count=1024 status=none && filefrag -v /var/tmp/big.bin
```

```text
File size is 1073741824 (262144 blocks of 4096 bytes)
 ext:     logical_offset:        physical_offset: length:   flags:
   0:        0.. 32767:   5242880.. 5275647:  32768:
   1:    32768.. 65535:   5275648.. 5308415:  32768:   5275648:
   ...
   8 physical extents found                          <- a 1 GiB file, 8 extents, thanks to delayed allocation
```

`/etc/passwd` shows a single extent; the streamed 1 GiB file should land in a handful (contrast a file rewritten for months by a long-running app — the "needs e4defrag" shape). On XFS boxes, `xfs_info /mountpoint` prints `agcount=` and `agsize=` — count your parallel allocators.

```bash
# 4. Price fsync on ext4 (the data flush + journal commit + cache flush):
truncate -s 512M /tmp/lab.img && mkfs.ext4 -q /tmp/lab.img && sudo mkdir -p /mnt/lab
sudo mount -o loop /tmp/lab.img /mnt/lab
python3 - <<'EOF'
import os, time
for name, do_sync in (("write-only", False), ("write+fsync", True)):
    fd = os.open("/mnt/lab/probe", os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o644)
    t0 = time.perf_counter()
    for _ in range(200):
        os.write(fd, b"x" * 4096)
        if do_sync: os.fsync(fd)
    print(f"{name:>11}: {(time.perf_counter()-t0)/200*1e3:7.3f} ms/op")
    os.close(fd)
EOF
```

Expect write-only around 0.01 ms (page cache only — chapter 02's "write latency is a lie") versus fsync 0.5-3 ms on NVMe or 10-30 ms on HDD: the difference *is* one ordered-mode data flush plus one JBD2 commit plus two cache flushes.

```bash
# 5. The crash story, documented (needs a disposable VM — pull the plug, don't shut down):
#    inside the VM:  dd if=/dev/urandom of=/src.bin bs=1M count=2048;  cp /src.bin /data/dst.bin &
#    host:           virsh destroy vmname          # mid-copy
#    next boot:      dmesg | grep -i ext4
```

```text
EXT4-fs (vda1): recovery complete        # journal replay of the last <=5 s of transactions
                                          # (kernel wording varies by version) — seconds, not fsck minutes
```

The file copy either shows its committed prefix or its pre-crash size — never another file's blocks. That boundary is `data=ordered` doing its one job.

## How to use this knowledge

- **Post-crash triage**: "recovery complete" in dmesg is the O(128 MiB) journal replay finishing — resist running fsck on a merely-power-lost ext4 box; replay already did the repair. But if the box ran `data=writeback`, treat post-crash file *contents* as suspect and restore rather than trust the bytes.
- **Predict fsync latency before buying hardware**: on ext4, fsync = ordered-mode data write + one journal commit + two cache flushes, so a drive with slow or dishonest flush commands dominates the total (chapter 02's 100x spread across "enterprise" drives). Test with `fio --fsync=1` on the actual device; check `commit=` if the loss budget is tighter than 5 s.
- **Fragmentation forensics**: a file that got slow over months is usually a scattered extent list — `filefrag -v` gives the count and, from the physical offsets, whether `e4defrag` (or XFS's `xfs_fsr`, or a copy-rewrite) will help. Near-contiguous-but-split extents defragment cheaply; extents interleaved with a hot neighbor do not.
- **Choose by workload shape**, not folklore: ext4's block-group locality and repair tooling for OS disks; XFS's per-AG parallel allocation and size-keyed B+trees for many-small-files and multi-TB volumes (the RHEL default reasoning). Metadata storms (millions of creates per minute) should make you think "journal ring wrap" — and measure.
- **Whiteboard the traps**: why a 1 GiB contiguous ext4 file needs exactly 8 extents, why ext3 tail reads paid 2-3 extra block reads, why recovery is O(journal) while fsck was O(fs) — three derivations that are what "knows file systems" actually means in an interview.

## Connections

- **Module 14 ch01** — the 12 ms seek and the torn-write window are the physics this whole chapter budgets against; the FTL's log-structure is the same "write sequentially, repair later" move JBD2 makes.
- **Module 14 ch02** — the page cache's dirty pages are what delayed allocation defers *from*; the fsync contract and the auto_da_alloc rename story are the direct continuation.
- **Module 14 ch04** — the CoW lineage inverts this chapter's commit: instead of logging the change, make the change itself atomic via a root-pointer flip.
- **Module 14 ch05** — the unified write path: `write(fd)` down through this chapter's allocator into the block layer and ch01's queue pairs.
- **Module 11 ch04** — the WAL/ARIES derivation; JBD2's descriptor/commit blocks are log records and a commit record, one layer down the stack.
- **Module 11 ch02** — B-tree fanout arithmetic returns as XFS's every index and ext4's htree.
- **Module 01 ch05 / ch19** — the allocator taxonomy (bins vs bitmaps) and the slab/jemalloc locality arguments, replayed at disk scale.
- **Module 15 (queued)** — overlayfs on ext4: the block allocator underneath every container image layer.

## Quiz

1. **Concept.** Explain, from the commit-block mechanism, why ext4's crash recovery is O(journal size) while ext2's fsck was O(file system size) — and why that difference killed boot-time fsck on 1 TiB volumes.

2. **Concept.** A program writes 1 GiB with plain `write()` calls, never calls fsync, and the machine loses power 3 s after the last `write()` returns. The volume is ext4 with default settings. Exactly what is guaranteed about that file after reboot, and what is not?

3. **Predict the layout.** A fresh 1 GiB file on ext4 (4 KiB blocks) is written contiguously in one streaming pass. How many extents describe it, do those extents fit in the inode's 60-byte `i_block` region, and if not, how many on-disk metadata blocks does the extent tree require?

4. **Predict the number.** Same 1 GiB file, but on ext3 (4 KiB blocks, classic indirect trees). A process reads byte offset 1,073,741,813 — how many *extra* block reads happen before the data block, and how does the answer change if the file system was built with 1 KiB blocks?

5. **Diagnose a bug.** An editor saves its config with the write-temp-file-then-rename pattern, calls no fsync, and the machine crashes immediately after the rename. After reboot the config file exists but is 0 bytes. Which two mechanisms combined to eat the file, which kernel heuristic now covers the common case, and what is the correct fix?

6. **Diagnose a bug.** A mail server runs ext4 mounted `data=writeback` for "speed." After a crash, several recovered mailbox files contain large stretches of a video file that was deleted the day before. Explain precisely why writeback permits this and why `data=ordered` — the default — structurally cannot.

7. **Diagnose / predict the number.** An admin adds `commit=60` to a busy ext4 volume's mount options to "batch journal traffic and reduce disk load." Quantify the new data-loss window relative to the default, and name the second failure mode the larger interval invites on a metadata-heavy workload.

8. **Cross-chapter.** JBD2 writes the commit block and calls the transaction durable. Connect this to module 11 chapter 04's WAL rule and chapter 02's drive-cache story: what must happen *physically* before that commit block counts as durable, and which two mechanisms in the Linux stack provide it?

<details><summary>Answers</summary>

1. **O(journal) vs O(fs).** Replay is a linear walk of the 128 MiB ring, reapplying every transaction with a valid commit block and stopping at the first hole — a torn tail has no commit, so it is discarded wholesale. Cost is proportional to the log: seconds. fsck has no log to trust, so it reconstructs consistency from first principles — every inode, both bitmaps, and every directory of the 1 TiB volume, cross-checked — the folklore 20 minutes per TB on 2000s hardware, on every boot after every crash. The commit block converts "prove the whole disk consistent" into "replay the last 5 s" — the same collapse module 11 ch04's commit record buys a database.

2. **Guaranteed: no foreign bytes. Not guaranteed: the data.** In `data=ordered` the only promise is ordering: any metadata that commits points at data blocks flushed *first*. After reboot the file is either absent (the size update never committed — the 5 s default interval had not fired, and the drive cache may have hidden even flushed writes, per ch02), a truncated prefix, or complete. What can never appear is another file's blocks — that interleaving is what ordered mode's data-before-metadata flush rules out. Durability remains fsync's alone (ch02): the journal is a *consistency* mechanism, not a durability one, and 3 s < 5 s makes "short or absent" the realistic outcome.

3. **8 extents; no; 1 metadata block.** 1 GiB / 128 MiB max extent = 8 extents. The inode's 60-byte `i_block` holds a 12-byte header + 4 × 12-byte entries = 4 extents maximum, so 8 does not fit. The tree grows to depth 1: the inode holds 4 extent-index entries (also 12 bytes each), each pointing to a 4 KiB leaf block holding the header + up to ~340 extents. Eight extents fit in a single leaf, so the whole map costs exactly 1 on-disk metadata block — versus ext3's ~1 MiB of pointer blocks for the same file.

4. **2 extra reads at 4 KiB; 3 at 1 KiB.** With 4 KiB blocks and 4-byte pointers, each pointer block holds 1,024 pointers: 12 direct + 1,024 single-indirect + 1,048,576 double-indirect blocks. Byte 1,073,741,813 is in block 262,143 of 262,144 — inside double indirection (everything past block 1,035 is), so the walk is: read the top-level pointer block, read one second-level pointer block, then the data block: 2 extra reads, ~24 ms of pointer chasing on a 12 ms-seek HDD. With 1 KiB blocks, a pointer block holds only 256 pointers: single indirect covers 256 blocks, double covers 65,536 (64 MiB) — so a 1 GiB file (1,048,576 blocks) needs *triple* indirection for its tail: 3 extra reads, ~36 ms. The historical default block size made the common case the worst case.

5. **Delayed allocation + metadata-before-data; auto_da_alloc; fsync file and directory first.** The temp file's bytes sat in the page cache with no blocks assigned (delayed allocation), while the rename — a pure metadata operation — could be journaled and committed before any data flush: crash between the two and the new name points at an inode with size 0, the 2006-2009 "ext4 ate my files" episode from ch02. The `auto_da_alloc` heuristic (default since 2.6.30) forces allocation and flushing when a rename replaces an existing file, closing the common save-file pattern. The fix is the durability contract: fsync the temp file, rename, then fsync the *directory* — the rename itself is data in a directory file, not an automatically ordered side effect.

6. **Metadata commits pointing at never-written blocks.** In writeback, only metadata is journaled and nothing orders the data writes. The mailbox grew; the allocator assigned it freshly freed blocks (the deleted video's); the inode's extent update was journaled and committed *before those blocks were ever written*; power died. Replay restored the metadata faithfully — extents pointing at blocks still holding the video's old bytes. Ordered mode structurally forbids this: at commit, JBD2 forces every dirty *data* block of files whose metadata is in the transaction out to disk before the metadata is written into the journal — committed pointers never lead unwritten data. The extra flush is the price, which is why writeback is faster and "use at your own risk."

7. **60 s window, 12x; and the wrap stall.** The commit interval bounds how much acknowledged-but-uncommitted state can vanish: default 5 s, now up to 60 s — 12x the loss exposure for every non-fsynced write (fsync callers force commits regardless). Second failure: ~12x more accumulated transactions between commits; on a metadata-storm workload (millions of file creates) the 128 MiB ring fills before commits drain it, and once the journal wraps, writers are throttled into waiting for checkpointing — the "fs got randomly slow after I tuned it" incident, now with a mechanism attached.

8. **The commit block must be on stable storage — platter or NAND, not the drive's DRAM.** Module 11 ch04's WAL rule: the commit record is not durable until everything before it — the log blocks *and* the commit record itself — is on stable storage; a commit visible only in the drive's volatile cache plus a torn tail means recovery trusts a transaction whose log blocks never landed. The stack closes this in two places: JBD2 issues explicit cache-flush barriers (the FLUSH CACHE command ch02 decodes for fsync) before writing the commit block and again after, so the ordering survives the volatile write cache; and the drive honors flushes honestly only if it is not a capacitor-less, flush-lying device of the pre-2006 era (or a buggy descendant). Same rule two layers down: database log record, file system commit block, both hostage to the disk's honesty.

</details>
