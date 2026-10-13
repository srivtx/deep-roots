# Module 14 — File systems & storage

> The disk is the heap that outlives the process. A file system is an
> allocator with a naming scheme and a crash-recovery story: the FTL
> inside your SSD is a garbage-collecting log-structured allocator in
> firmware, the page cache is the "heap in front of the disk", ext4's
> journal is the same WAL idea as module 11's databases, and Btrfs/ZFS
> are copy-on-write collectors. Module 01's allocator taxonomy (ch05,
> ch19) and module 11's durability contract (ch01, ch04) are the roots —
> this module replays them at disk scale.

**5 chapters · one final exam · prerequisite: module 01 chapters 03
(page cache = demand paging from files), 05 (allocators), 19
(jemalloc/slab — the FTL and allocators return); module 11 chapters 01
(fsync, torn writes — reused), 02 (B-trees — reused for extents), 04
(WAL/journal — reused directly).**

## How to study it

Chapter 01 is the hardware (HDD geometry, SSD FTL as a GC, NVMe queues);
02 is the kernel side every program touches (VFS, inodes, the page
cache, fsync); 03-04 are the on-disk designs (ext4's journaling lineage;
the CoW trio Btrfs/ZFS/APFS); 05 is the special file systems that ARE
the OS (procfs, tmpfs, overlayfs) plus the unified end-to-end picture.
Labs: any Linux box (debugfs, fio, /proc/meminfo, mount); macOS labs
marked (APFS).

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [Storage hardware from first principles](chapters/01-storage-hardware.md) | HDD geometry, NAND pages, the FTL as a garbage collector, NVMe queues — the medium's rules. |
| 02 | [VFS: files, inodes, and the page cache](chapters/02-vfs-page-cache.md) | fd → file → inode, the page cache as the heap in front of the disk, fsync decoded. |
| 03 | [ext4 and the journaling lineage](chapters/03-ext4-journal.md) | Block groups, extents, the JBD2 journal — the WAL idea at file-system scale. |
| 04 | [The copy-on-write lineage: Btrfs, ZFS, APFS](chapters/04-cow-lineage.md) | Never overwrite a live block: snapshots for free, checksums, and the write-amplification price. |
| 05 | [Special file systems and the unified picture](chapters/05-special-filesystems.md) | procfs, tmpfs, overlayfs, FUSE — and the fd-to-block path drawn end to end. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — storage hardware from first principles.** The HDD from
geometry: platters, tracks, sectors; the seek (~8-12 ms average on a
7,200 rpm SATA drive) + rotational latency (4.17 ms average = half of
the 8.33 ms rotation) + transfer (a 7,200 rpm drive's outer track ~150-250
MB/s) — derive the random-4KB-IOPS ceiling (1000/(12+4.17+~0.02) ≈ 60
IOPS — the number that explains why databases die on spinning disks);
the sector as the atomic unit (512 B logical, 4 KiB physical — the
512e alignment tax: an unaligned 4 KB read on 512e becomes two physical
reads, the partition-alignment folklore). The SSD from NAND physics:
a floating-gate cell; pages (4-16 KiB — the read/write unit) and blocks
(64-256 pages — the *erase* unit); erase-before-write (a NAND cell
must be erased to 1s before it can be programmed to 0s — the constraint
that creates the entire FTL); the write-amplification arithmetic
(derive: to overwrite one 4 KiB page in a 100% full block, the FTL must
read the other 63 pages, write them elsewhere, erase the block — WAF
= 64 for that write in the worst case; real-world WAF 1.5-3 with TRIM,
5-10+ when full or cold — the "SSD slows down when 90% full" mechanism
in two sentences); TRIM (the OS announces dead blocks so the FTL's GC
doesn't copy garbage); SLC cache (fresh blocks run in fast SLC mode
until the drive fills — the first 20 GB are 3× faster than the rest —
the benchmark trap); endurance arithmetic (TLC ~1,000-3,000 P/E cycles
per cell; a 1 TB drive at 600 TBW = 600 full writes... derive the TBW
from P/E × capacity × 1/WAF); read-disturb and the scrubber. The FTL as
a *log-structured allocator with a garbage collector* (the mapping
table: logical page → physical page; the table is a B-tree/DRAM-cached
structure; the FTL's GC = module 11's LSM compaction and module 01
ch14's cycle detector: three systems, one idea — name all three). NVMe:
the queue design (up to 64 K queues × up to 64 K entries each, vs
AHCI's 1 queue of 32 — because flash is parallel: the controller has
8-16 channels and needs thousands of in-flight commands to saturate
them; the queue is *deep* because the device is parallel); PCIe lanes
(Gen3 ×4 ≈ 3.5 GB/s, Gen4 ×4 ≈ 7, Gen5 ×4 ≈ 14 — the ladder); the
latency floor (~20-100 µs NVMe vs ~10 ms HDD — the 100-500× gap; place
all of it in module 01 ch02's tower as the new bottom rows). The
insider's price of queue depth: queue depth 32 vs 1 on an NVMe drive =
the 10× throughput difference (the fio `iodepth` parameter — why
synchronous workloads can't have it). Lab: `smartctl -a` (the wear
indicators: Percentage Used, Data Units Written — decode line by line),
`nvme list` / `lsblk -o NAME,PHY-SEC,LOG-SEC,ALIGNMENT` (the 4K
alignment check), fio the same drive with randread 4k iodepth=1 vs 32
(the orders of magnitude, measured — the lab's payoff), `hdparm -t` for
the old world.

**Chapter 02 — VFS: files, inodes, and the page cache.** The object
trio drawn: the fd (an index into the per-process fd table → `struct
file`) → the dentry (the *name* cache: a path component) → the inode
(the identity: the thing with a number, mode, size, and a pointer into
the file system); `dup`/`fork` share one `struct file` (the refcount
and the *shared file offset* — module 01 ch04's fork semantics return:
why two processes reading one inherited fd interleave), each `open`
creates a new `struct file` with its own offset. The inode as the
"object header" of a persistent heap: the data blocks are the payload,
the inode is the metadata (12... no — ext4: extents; ch03); hard links
= two dentries, one inode, the link count; symlinks (a dentry with a
string payload — the traversal rules; the insider's `readlink -f` and
the dangling symlink); directories are files of (name → inode) pairs
(the tree structure of the *namespace* is data inside inodes — nothing
special at the disk level; `.` and `..` as real entries; the hard-link
loop prevention: the link count on directories). The page cache as the
heap in front of the disk: every `read()`/`write()` on a cached file
touches pages indexed by (inode, offset) in the XArray (formerly radix
tree); a read = a page-cache hit (no I/O) or a fault-in (4 KB or
readahead window from disk); a write = dirty the page (the I/O happens
*later*) — derive the consequences: (1) "free memory is wasted memory"
(Cached grows until reclaim — why `free -h` shows buff/cache eating
everything), (2) write latency is a lie (a 1 GB write "completes" in
memory — the flush happens in the background or at fsync; the
`vm.dirty_ratio` (default 20% of RAM) and dirty_bytes throttle: when
the dirty fraction crosses it, the *writer* is forced into writeback —
the "my process stalls randomly on writes" incident class), (3) reclaim
pressure: clean pages drop free (a re-read later), dirty pages must be
written back before dropping — under memory pressure the system does
I/O it never asked for (the OOM-through-writeback stall). fsync vs
fdatasync vs sync vs syncfs: what each guarantees (fsync: data + the
metadata needed to find it — size, indirect... extent updates; fdatasync
skips mtime — 2-5× cheaper on some file systems; the ordering contract
= the drive must have flushed its volatile cache: the FLUSH/FUA
command; the drive lying: the capacitor-less "volatile write cache"
era, the firmware bugs that ate journals — the insider reason
`fio`-measured fsync latency varies 100× across "enterprise" drives);
the ext4 delayed-allocation + fsync data-loss bug class (the 2006-2009
"ext4 ate my files after crash" story: the file was created, written,
closed — but never fsynced: the rename could land *before* the data —
the auto_da_alloc fix — the lesson: the *ordering* of two files'
updates needs fsync on the directory too). O_DIRECT: DMA to user pages
(the page cache bypassed; the alignment requirements — 512/4K-aligned
buffers and offsets, else EINVAL; why databases want it: they own their
cache (module 11's buffer pool IS the page-cache replacement) and hate
double caching; the O_DIRECT + buffered read coherence hole — the
insider's "don't mix them on the same file"); readahead (the sequential
detection: 3-4 sequential hits → window doubling up to
read_ahead_kb=128; the backwards read: no readahead — the "reverse
video scrubbing is slow" folklore). mmap: file-backed pages (module 01
ch03's demand paging, exactly — the page fault on a mapped file reads
the page from disk; MAP_PRIVATE = COW on write, MAP_SHARED = dirty
writeback; the first-touch cost: a fault per 4 KB — the ~1-2 µs ×
count arithmetic; when mmap beats read: many small random accesses to
a big file, one big sequential read = read wins (syscall amortization
vs fault amortization — the crossover derived). Lab: `/proc/meminfo`
(Cached, Dirty, Writeback fields read over time), the dirty-page
lifecycle observed (write 500 MB, watch Dirty spike then drain —
`grep -E 'Dirty|Writeback' /proc/meminfo` in a loop), fsync latency
measured (Python: open/write/fsync loop vs close-only — the ms vs µs
gap), `vmtouch` or `/proc/PID/pagemap`-lite (which pages of a file are
cached), `cat /proc/PID/fdinfo/3` (pos and flags of a live fd),
`posix_fadvise` demo'd or documented.

**Chapter 03 — ext4 and the journaling lineage.** Start from the
problem a file system must solve: an on-disk allocator + a metadata
index + crash consistency, on a medium that can only overwrite in
whole blocks, at 10 ms each, with a torn-write window (a crash
mid-write leaves a block half-old/half-new — module 11 ch01's torn
write, same physics). ext2 as the base design: block groups (the
locality design: the inode table of a group sits near that group's data
blocks — a file's inode and its data are allocated from the same group;
the ~128 MB groups), the block bitmap + inode bitmap (free-space
management as a bitmap — contrast module 01 ch05's free-list bins:
which fragmentation behaviors each produces), the directory file
(linear entries (name, inode) pairs; the 1-2 MB-dir performance wall
and the htree (B-tree-indexed directories) fix in ext3/ext4). ext4's
two big upgrades: extents (ext2/3's indirect block tree drawn: for a
1 GB file, 4 KB blocks → 262,144 blocks → the 3-level indirect tree of
pointers (each 1 KB block of pointers holds 256), read of byte
1,073,741,813 walks 3 extra blocks — the latency tax at every file
tail; the extent: one 12-byte (start_block, length) pair covers up to
128 MB contiguous — a 1 GB file = 8 extents; the on-flash 4KiB-tree
extent index), and delayed allocation (ext3 allocated blocks at write
time → physical layout mirrored the write order; ext4 waits until
writeback → the allocator sees the whole file → near-contiguous
placement; the cost: the data-loss window of ch02's story — written
bytes with no block yet assigned). The journal (JBD2): the torn-write
solution as a WAL (module 11 ch04's ARIES idea, stated as the same
idea): all metadata updates first written to a log area (a circular
ring of 4 KB blocks), a *commit block* (with a CRC) closes the
transaction, then checkpointing moves updates into place; replay at
mount: walk to the last valid commit — recovery in O(journal size), not
O(file system size) — why fsck died (the ext2 era: 20-minute boots on
1 TB, the ext4 journal replay: seconds); the three journal modes
(journal = data+metadata into the log (safe, 2× writes), ordered =
data written before the metadata that points to it is committed (the
default — the ordering is the guarantee, the data is not journaled),
writeback = only metadata journaled, no ordering (fastest, the
after-crash file can contain stale/garbage blocks — the "use at your
own risk" folklore)); the journal size trade (default ~128 MB or 4 s of
writes — the `fs.journal.commit_interval`, 5 s default: the maximum
dirt that can vanish on power loss, stated as a number). XFS as the
sibling: allocation groups (parallel block/inode allocation across a
big device), B-trees for free space *and* extents and inode tables
(module 11 ch02's B-tree fanout returns as the XFS index), the
delayed-allocation pioneer, the online defrag (xfs_fsr); why RHEL
defaults to XFS for big storage and ext4 for OS disks. The allocator
taxonomy across file systems as a table (bitmap vs B-tree free space;
per-CPU locking (ext4's per-group locks, XFS's per-AG). Lab: `dumpe2fs
-h /dev/...` (read: block size, groups, journal size, features — every
line decoded), `debugfs` (the inode walk: `stat <12>` shows the extents
of an inode; `blocks <12>` the block list; the `lsdel` deleted-inode
list — the forensic power), `filefrag -v` on a small and a big file
(the extent list: contiguous vs not), `xfs_info` (AGs and B-tree
sizes), an fsync-vs-journal timing demo, the crash story documented
(loop-mount + `dmsetup` create a flaky device... or documented as
"crash a VM mid-copy, watch the replay seconds").

**Chapter 04 — the copy-on-write lineage: Btrfs, ZFS, APFS.** The CoW
derivation: journaling (ext4) accepts overwrite-in-place and repairs
afterward; the alternative is *never overwrite a live block* — every
write allocates fresh blocks, builds the new metadata pointing to them,
and flips one *root pointer* atomically (a single block write becomes
the commit — module 11's WAL insight inverted: instead of logging the
change, make the change itself atomic). Derive the consequences, good:
snapshots are free (an old root pointer = a complete frozen file
system — 100 snapshots cost 0 writes; ZFS/btrfs send/receive and every
Time Machine/camera roll build on it), crash consistency without a
journal (the half-written new tree is garbage behind an unpublished root
— replay = "use the old root", instant), checksums as a first-class
citizen (the data's checksum is stored in the block's parent — the
whole-path verify: bit rot detection (the measured reality: CERN's 2007
study found ~1 corrupt file per ~1,600... the classic numbers: ~10⁻¹⁵
BER scaling to real arrays — cite honestly as "studies report one
silent corruption per thousands of files per year on large arrays"));
and the costs: write amplification (every overwrite of a 4 KB block
rewrites the block *and* every metadata block on the path to the root
(Btrfs's tree depth 2-3 — the small-file overwrite tax; the "CoW file
systems are slow on databases" mechanism stated with numbers:
fixed-size random overwrites on ZFS vs ext4: 2-5× slower), free-space
fragmentation (the allocator can't place things contiguously when
freedom is scattered — the ZFS "don't fill past 80-90%" rule: the
metaslab allocator's free-space B-trees degrade, allocation CPU spikes
(the "ZFS goes weird at 90% full" folklore as a mechanism)). Btrfs: COW
B-trees (module 11 ch02's B-tree + module 01 ch03's COW, named), the
subvolume (a tree root with its own ID — not a directory, not a
partition; the third thing), reflink copy (copy a 10 GB file as
metadata: the extent-reference count — "instant clone, 0 bytes
copied"), data checksums (crc32c default; the "scrub" walk),
transparent compression (zstd: the 2:1 typical text ratio, the "store
more, read faster" surprise (decompression outruns disk)), the RAID
write-hole history (RAID5/6 was marked "unstable" for a decade: the
parity write isn't atomic across devices; the 2021 "raid-stripe-tree"
era finally addressing it — insider history). ZFS: the pool (all disks
one storage pool; datasets carved out — "the volume is a file inside
the pool"), the ARC (the adaptive replacement cache: a hit-rate-driven
mix of MRU/MFU — the real page-cache replacement algorithm, contrast
Linux's LRU list pairs; the L2ARC/SLOG tiers (the SLOG: a small fast
device that only takes ZIL writes (the synchronous-write intent log —
sync writes land in the ZIL then CoW later — the "fsync accelerator";
the SLOG is a write cache, never a read cache — the #1 misunderstanding,
and its loss = no data loss, only sync-write latency back to disk —
the two insider facts)), recordsize (the default 128 KiB: fine for
files, terrible for databases — the 16 KiB advice for PostgreSQL, the
same block-size-vs-page-size matching as module 11's buffer pool), the
scrub (scheduled full-tree checksum walk — the only way to find bit rot
*before* you read it), RAIDZ, send/receive. APFS: containers and
volume trees (a "volume" is a tree in a shared container's space —
no fixed size! the flexible container), per-inode copy-on-write
(cloning), snapshots (the Time Machine building block), the checksum
decision (metadata checksummed, data NOT — the deliberate trade:
Apple judged per-block data checksums too expensive for flash-era
latency and flash's own FTL ECC "good enough" — the insider design
argument, criticized and defended), no journal (the root-pointer atomic
commit *is* the consistency story), the fusion drive pairing. The
closing comparison table: journaling vs CoW (write cost per overwrite,
snapshot cost, crash recovery, checksum story, fragmentation risk, the
"which for database / which for bulk / which for OS disk" guidance).
Lab: btrfs (if available: `mkfs.btrfs` on a loop file — subvolume
create, snapshot, write to a file, `btrfs filesystem du` shows the
snapshot sharing 0 extra bytes; `compsize` the compression ratios;
else documented), ZFS-on-Linux documented walk (zpool create, zfs set
recordsize, zdb lite), the macOS `diskutil apfs` (documented), the
CoW-vs-journal write cost measured on btrfs vs ext4 if both available
(fio randwrite: the 2-5× gap), `cp --reflink=always` vs
`--reflink=auto` (the instant copy, timed).

**Chapter 05 — special file systems and the unified picture.** procfs
as kernel memory rendered as files (the read of `/proc/meminfo` is a C
function called at read time — `seq_file`; the file's `stat` size is 0
because the content doesn't exist until you ask; the "everything is a
file" as an *interface pattern*: the kernel exposes state through
open/read/seek — the 1970s insight that an inspection API needs no new
syscalls (contrast: dumpsys on Android (module 05) — the same pattern,
different name); the writes (`/proc/sys/...`): the sysctl interface is
file writes into kernel memory — the tuning knobs from every chapter of
modules 01-13 are literally `write()` calls). sysfs (the device model
as a tree of kobjects — one file per attribute; udev's rule engine
listening on it). tmpfs: RAM as a file system (pages in the page cache
with no backing block device; `df` shows it consuming memory; the
swap interplay: tmpfs pages *can* swap — the "tmpfs is free until it
isn't" budget rule; `/dev/shm` the default 50%-of-RAM cap; the CI
story: /tmp as tmpfs makes builds fast until the 8 GB artifact OOMs the
box). overlayfs (the container file system — module 15 ch04's
foundation laid here): the lower layer (read-only image), the upper
(work) layer, the merged view; **copy-up** (the first write to a
lower-layer file copies the whole file up — editing a 100 MB file in a
container = a 100 MB copy; the "containers are cheap until you edit
big files" bill), whiteouts (a deletion in the upper layer hides the
lower one — implemented as a character device entry in the upper
layer's directory), the multi-lowerdir stack (image layers: each
Docker layer = one lowerdir — the O(1) vs O(n) debate: lookups walk
the layers (the worst-case path cost; dentry caching hides most of it).
FUSE (the file system in userspace: the kernel routes each op to a
daemon over /dev/fuse — every read is a round trip through a process
(2 context switches minimum; the "sshfs is 10-50× slower than NFS for
small files" arithmetic; why FUSE still wins: the userspace file
system ecosystem (s3fs, sshfs, gitfs) — the kernel's extensibility
answer; the insider: FUSE + the page cache can double-buffer). NFS
(the cross-machine file system: stateful-ish v3 vs the v4 state model;
the attribute cache (the 60 s freshness defaults — "the other machine
deleted the file 10 s ago but ls still shows it" — the coherence
budget; the LOOKUP storm: `ls -l` on a 1,000-file dir = 1,000+ RPCs
(READDIR then per-file GETATTR... and READDIRPLUS trade), the close-to-open
consistency model stated plainly: NFS caches aggressively between opens
— it is not a shared disk, and the programs that assumed it was are
the war stories). The unified end-to-end picture (the module's payoff
diagram): `write(fd)` → the fd table → the `struct file` → the dentry
→ the inode → the page cache page (now dirty) → writeback → the file
system's block allocator → the block layer (the io scheduler: none for
NVMe, mq-deadline for SATA) → the NVMe queue pair → the controller →
the FTL → the NAND page — every arrow annotated with which chapter
explained it; plus the reclaim path (memory pressure → writeback or
drop → the kswapd cycle) drawn as the return loop. Lab: `strace cat
/proc/meminfo` (open/read/seek — no file content at open time),
`stat /proc/meminfo` (size 0 — the proof), `mount -t tmpfs -o size=64m
tmpf /tmp/test` + a dd until ENOSPC (the memory-budget demonstration),
`findmnt -T /etc/passwd` (the mount tree lookup), `cat /proc/slabinfo
| grep -E 'dentry|inode'` (the dentry cache's real memory cost),
`echo 3 > /proc/sys/vm/drop_caches` (before/after a cold read timing —
the honest demo of what the cache buys), the overlayfs demo
(mount -t overlay with a lowerdir/upperdir, edit a lower file, watch
the upperdir's file appear and `du` it).

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **The durability contract in full**: fsync, torn writes, journal
  replay, the 5-second dirt window — you can reason about data loss
  instead of hoping.
- **Storage performance engineering**: IOPS arithmetic, queue depth,
  the FTL's write amplification, the page cache's lies — the numbers
  behind every "the database is slow" incident.
- **File system judgment**: journaling vs CoW, recordsize and block
  sizing, when reflink/O_DIRECT/mmap — with the crossover arithmetic.
- **The container groundwork**: overlayfs copy-up and whiteouts —
  module 15's file-system half, already owned.
