# Chapter 02 — VFS: files, inodes, and the page cache

> **The 30-second version.** When your program calls `write(fd, buf, n)`, "the file" is four kernel objects: the fd is an index into a per-process table, pointing at a `struct file` (which owns the offset and flags), which hangs off a dentry (a cached *name*), which points at the inode (the *identity*: number, size, block map). The VFS is the dispatch layer — each inode carries its own function-pointer tables — fronting ext4, tmpfs, and NFS with the same five syscalls. Between your process and the disk sits the page cache, a per-inode XArray of 4 KB pages keyed by offset, so a `read()` is usually a memory copy and a `write()` is a copy plus a dirty bit — the real I/O happens later, in the background, unless you demand it with `fsync`. Every surprising file behavior — interleaved reads after `fork`, "free memory is 0," writes that stall seconds later, zero-length files after a crash — is one of those objects, or that cache, behaving exactly as designed.

## One file, four objects

Derive the objects from the problem: one file, many processes at once, independent positions, no per-syscall path re-resolution, and a dozen file systems behind one API. That decomposes into: *a per-process handle* (the fd), *a shared open-instance* (`struct file`), *a cached name* (the dentry), and *the thing itself* (the inode).

```
  process A                       process B (forked child of A)
+------------------+             +------------------+
| fd table         |             | fd table         |
| [0] [1] [2] [3]..|             | [0] [1] [2] [3]..|
+------|-----------+             +------|-----------+
       | fd 3                            | fd 3
       └────────────┐          ┌─────────┘
                    ▼          ▼
          +----------------------------------+  f_count = 2
          | struct file                      |  ONE shared offset
          |   f_pos     = 5,242,880          |  (fork/dup share THIS)
          |   f_flags   = O_WRONLY|O_APPEND  |
          |   f_count   = 2                  |  (each open() makes a NEW one)
          |   f_op  ──► ext4 file_operations |  dispatch table
          +--------------|-------------------+
                         │ f_path
                         ▼
          +---------------------------+
          | dentry  "app.log"         | the NAME (cached)
          |   d_parent ──► "log"      |
          |             ──► "var"     |
          +------------|-------------+
                       │ d_inode
                       ▼
          +------------------------------+
          | inode 131074                 | the IDENTITY
          |   i_mode   = 0100644         |
          |   i_size   = 10,485,760      |
          |   i_nlink  = 1               |
          |   i_mapping ──► page cache   | the DATA cache
          |   i_op / i_fop: method ptrs  | per-filesystem dispatch
          +------------------------------+
```

**The fd** is the smallest free index into the per-process fd table (POSIX: `open` returns the lowest unused one — why redirection tricks like `2>&1` work). The table is born with 64 entries on 64-bit Linux 6.x and grows on demand, capped by `RLIMIT_NOFILE` (soft 1024 on most distributions). fds 0, 1, 2 are only convention — the shell wires them before `exec`.

**The `struct file`** is the open *instance*: offset (`f_pos`, a signed 64-bit `loff_t`), flags, mode, and a refcount. Module 01 chapter 04 met its fork semantics already: `fork()` copies the child's fd *table entries* and bumps `f_count` — both processes share one `struct file`, therefore **one offset**. Two processes reading one inherited fd interleave, each consuming what the other fetched. Two processes that each called `open()` have two `struct file`s with independent offsets, so each reads the whole file; `dup`/`dup2` reproduce the shared case inside one process. `O_APPEND` overrides the offset rule: each write ignores `f_pos` and lands atomically at `i_size`, under the inode lock — the property that makes multi-writer log appends sane.

**The dentry** is the name cache: one object per path component ("/var/log/app.log" = 4 linked dentries). Resolution is a directory lookup per component — real I/O on a cold cache; dentries cache the result so the second `open` skips it. Chapter 05's lab shows their true memory price in `/proc/slabinfo`.

**The inode** is the identity — the thing with a number, mode, size, link count, and a pointer into its file system's block layout. Inode numbers are unique only *within* one file system, which is why hard links cannot cross a mount point (`link(2)` returns `EXDEV`). The VFS's whole trick lives here: `i_op`/`i_fop` are per-inode function-pointer tables, so `read()` on an ext4 inode calls ext4's code and on a tmpfs inode calls tmpfs's — the C-vtable idea of module 01, and how one syscall API fronts N file systems.

## The inode is an object header; names are pointers

Treat the disk as a persistent heap: the data blocks are the payload, and the inode is the metadata header in front of it — module 01 chapter 05's allocator headers, but for a heap that survives processes. ext4 gives each inode 256 bytes by default: mode, size, 4 timestamps, link count, and the block map — in ext4 a list of *extents*, `(start_block, length)` pairs, not the old indirect pointer tree (chapter 03 derives why).

From that framing, the two "name" behaviors fall out:

**Hard links are two pointers, one object.** `ln a b` creates a second dentry aimed at the same inode and increments `i_nlink` — `stat -c '%h'` shows 2. There is no "original": mode, size, and contents live in the inode, so both names share them equally. `rm` is really `unlink()`: it decrements `i_nlink` and removes one dentry. The object is freed — blocks returned — only when `i_nlink` hits 0 *and* the last open fd closes (`i_count`). That conjunction is the "I deleted the 20 GB log but `df` didn't drop" incident: the daemon's fd keeps the inode — and its blocks — alive. `lsof +L1` finds exactly these.

**A symlink is a file whose payload is a path string.** On ext4, a target ≤ 60 bytes is stored *inline in the inode* — the fast symlink; longer targets spill to a 4 KB block. `open()` traverses them automatically: resolving `/a/b` where `b` is a symlink restarts resolution at the target, nested traversal capped at 40 links before `ELOOP` (Linux 6.x). A dangling symlink still `lstat`s fine, but `open` fails with `ENOENT` — the name exists, the target doesn't. `readlink -f` resolves every component to a canonical real path.

**Directories are files whose payload is (name → inode) records.** Nothing is special at the disk level: `ls /var` is a `read()` of /var's data blocks. An ext4 record is an 8-byte header — 4-byte inode number, 2-byte record length, 1-byte name length, 1-byte file type — plus the name padded to a 4-byte boundary. `.` and `..` are *real records* pointing at the directory itself and its parent — that is why `ls -la` shows them. The namespace tree is data stored inside inodes, a graph in the heap's payload walked by a resolver. Two rules protect that walk: hard links to directories are forbidden (`EPERM`), because a cycle would loop path resolution and `fsck` forever; and the directory's `i_nlink` encodes the tree — a fresh directory has nlink 2 (its name in the parent plus its own `.`), each immediate subdirectory's `..` adds 1, so `stat -c '%h' /d` = 2 + subdirectory count on ext4-style file systems. (btrfs reports 1 for every directory regardless — a monitoring trap, not a bug.)

## The page cache: a heap in front of the disk

Derive it from chapter 01's latency ladder: RAM answers in ~100 ns, NVMe in ~20-100 µs, HDD in ~10 ms — any byte read twice justifies a cache. Linux's answer is the page cache: per inode, an `address_space` holding 4 KB pages keyed by file offset (page index = offset / 4096), stored in an XArray — the structure that replaced the radix tree in kernel 4.20.

The paths through it:

- **read** = find page (inode, offset). Hit: copy to the user buffer, no I/O, done in microseconds. Miss: allocate the page, submit a 4 KB read (or a readahead window), sleep on the page lock until I/O completes, then copy.
- **write** = find-or-allocate the page, `copy_from_user` into it, set `PG_dirty`, return. That's all. On ext4 the blocks are not even chosen yet — delayed allocation defers that to writeback time (chapter 03).

So the moment `write()` returns, nothing has touched the disk. The dirty page's full lifecycle:

```
 write(fd, buf, 4096) at offset 4,096,000
   │  copy_from_user into cache page index 1000
   ▼
 page marked PG_dirty  ──► write() RETURNS here (~µs)
   │
   │   ...later: writeback cadence is 5 s (vm.dirty_writeback_centisecs=500),
   │   a page may sit dirty up to 30 s (vm.dirty_expire_centisecs=3000)
   ▼
 flusher thread wakes when dirty exceeds 10% of RAM
   (vm.dirty_background_ratio=10)          [background threshold]
   │   ext4 now resolves delayed allocation -> real block numbers
   ▼
 PG_dirty ──► PG_writeback  (in flight; /proc/meminfo: Writeback)
   │
   ▼ device acknowledges
 page is clean but STAYS CACHED (a future read is a hit)
   │
   ▼ under memory pressure: clean page dropped, no I/O (re-read if touched again);
     dirty page CANNOT be dropped - it must be written back first
 ─────────────────────────────────────────────────────────────
 fsync(fd) = force this entire ladder NOW, for this inode,
             then FLUSH the device's own write cache
```

Three consequences:

**1. "Free memory is wasted memory."** The kernel fills RAM with cached pages and returns them only under pressure, so `free -h` shows buff/cache eating everything and "free" near 0 forever — not a leak, the design. Read the "available" column: what reclaim can actually yield.

**2. Write latency is a lie.** A 1 GB sequence of `write()` calls completes at memcpy speed — well under a second — while the flush happens up to 30 s later. The throttle is `vm.dirty_ratio` (default 20% of RAM; `vm.dirty_bytes` overrides absolutely, default 0): crossing it drafts the *writing process itself* into writeback, blocking inside `balance_dirty_pages()` until the level falls. On a 64 GB box that is 12.8 GB of rope — the "my bulk writer randomly stalls in D state" incident.

**3. Reclaim turns into I/O the system never asked for.** Under memory pressure, clean cache pages drop free (cost: a later re-read); dirty pages must be written back before they can drop. A merely-memory-hungry box starts emitting disk writes no process issued, and in the worst case the OOM killer fires while kswapd still grinds through gigabytes of stale dirt. "The disk is slow" is often a memory problem wearing a disk costume.

## fsync decoded: what "the write succeeded" has to mean

The ladder of durability: `write()` = "in RAM." Writeback = "left RAM." Neither survives power loss. `fsync(fd)` is the contract that the bytes are on the medium, and it does four jobs: (1) write out every dirty page of that inode; (2) write the metadata needed to *find* the data — size and extent updates, which on ext4 also forces delayed allocation to resolve right now; (3) push the metadata through the journal (chapter 03); (4) issue a cache-flush command to the device — NVMe `FLUSH`, SATA `FLUSH CACHE` (or the FUA bit per write) — so the drive's volatile DRAM buffer actually drains to the media. `fsync` returning 0 means the device acknowledged it.

The family: **fdatasync** skips metadata not needed for retrieval (mtime and friends) — 2-5× cheaper on some file systems, though on ext4's default ordered mode the journal commit dominates. **sync(2)** flushes the whole system and, per its Linux man page, may return before all data is out; **syncfs(2)** is the per-filesystem variant that waits.

**The rung everyone forgets is rung (4), and drives have lied about it.** In the 2000s, consumer drives shipped volatile write caches and some silently ignored `FLUSH CACHE` — the firmware scandals that ate file-system journals. Today the honest version of the same physics: an SSD's DRAM write cache is volatile unless the drive carries power-loss protection (supercapacitors). A PLP drive reports its cache as non-volatile, so `FLUSH` is nearly a no-op — fsync costs tens of microseconds; a consumer drive must actually push DRAM to NAND, costing milliseconds. That is the insider reason fio-measured fsync latency varies by 10-100× across drives all labeled "enterprise": some have the capacitors, some don't. Check the volatile-write-cache bit (`nvme id-ctrl`, VWC), not the label.

**The ext4 data-loss story (the 2006-2009 bug class, peaking 2008-2009).** An application saves its config: writes the new file, closes it — no fsync — then renames it over the old one. With delayed allocation the data sat only in the page cache with *no blocks assigned*; the rename is pure metadata, journaled, committed within ext4's 5 s journal interval. Power fails after the rename commits but before writeback: the rename landed, the old content is gone, the new content never reached the disk — the file comes back zero-length. KDE session files were the famous casualty. The kernel fix was `auto_da_alloc` (default since 2.6.30): ext4 recognizes the rename-over pattern and forces allocation/writeback before the rename commits. The portable lesson: when the *ordering* of two updates matters — new content, then the name that points at it — the code must be `write temp → fsync(temp fd) → rename → fsync(directory fd)`. The last fsync, on a directory fd, makes the rename itself durable, and it is the step most production code omits. And note: close() is not in the recipe — close flushes nothing, by POSIX design.

## Three ways around the cache: O_DIRECT, readahead, mmap

**O_DIRECT** routes the I/O around the page cache: the device DMAs (nearly) directly into the user buffer. The price is alignment — file offset, transfer length, and buffer address must all be multiples of the logical block size, 512 at minimum and 4,096 to be safe; violations return `EINVAL` (use `posix_memalign`). Why databases want it: module 11 chapter 02's buffer pool *is* a page-cache replacement — the kernel cache would hold the same pages twice (a RAM tax) and add writeback-stall latency the DB cannot control. The trap: **do not mix O_DIRECT and buffered I/O on the same file**. An O_DIRECT write does not portably update or invalidate the cached pages, so a buffered reader can serve stale bytes. If you must mix, `posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED)` to evict first.

**Readahead** is the cache betting on the future: the kernel's on-demand heuristic detects 3-4 sequential hits and doubles the prefetch window, up to `read_ahead_kb` (default 128 KB per backing device, Linux 6.x). It only fires *forward* — a backwards read gets zero readahead, every 4 KB a cold fault: the mechanism behind the folklore that scrubbing a video in reverse is slow. `posix_fadvise` steers it: `POSIX_FADV_SEQUENTIAL` (read aggressively), `POSIX_FADV_WILLNEED` (prefetch now), `POSIX_FADV_RANDOM` (stop the doubling; random misses would thrash the window).

**mmap** is module 01 chapter 03's demand paging, pointed at a file: the first touch of a cold page is a major fault that reads the 4 KB from disk and installs the mapping. `MAP_PRIVATE`: a write triggers copy-on-write into an anonymous page — the file is never modified. `MAP_SHARED`: a write dirties the page-cache page itself, flowing through the same writeback ladder as any `write()` — and the mapped page *is* the page-cache page, so `read()`, `write()`, and `mmap` on one file share one physical copy in RAM. The cost is a fault per 4 KB, ~1-2 µs each: touching 1 GB cold = 262,144 faults ≈ 0.25-0.5 s of pure fault overhead, while a sequential `read()` loop with a 1 MB buffer spends 1,024 syscalls plus one big memcpy — syscall amortization beats fault amortization, so **sequential whole-file access: `read()` wins**. mmap wins the other cases: sparse access to a huge file (touch 4 KB every 4 MB of a 10 GB file = 2,560 faults; `pread`-style reads drag 128 KB of readahead per 4 KB — ~32× the bytes moved), and repeated small random access to an already-cached file (each warm hit is a TLB-level access, ~100 ns, versus ~1 µs of syscall per `pread`).

## What insiders know

- `cat /proc/PID/fdinfo/3` prints `pos:` and `flags:` (octal) of a live fd — the first command when a reader "is stuck": the live `f_pos` of the live `struct file`.
- Deleted-but-open files: `df` says full, `du` finds nothing. `lsof +L1` lists files with link count 0 still held open — the log "rotated" with `rm` while the daemon kept its fd.
- fsync latency is a *drive* property: power-loss protection capacitors → µs-class FLUSH; volatile cache → ms-class. Benchmark with `fio --fsync=1` before trusting the "enterprise" label.
- The durable-replace recipe: `write temp → fsync(temp) → rename → fsync(parent dirfd)`. The directory fsync is the step roughly 90% of production code omits.
- `vm.dirty_ratio` / `dirty_background_ratio` / `dirty_writeback_centisecs` / `dirty_expire_centisecs` default to 20 / 10 / 500 / 3000. On a 64 GB box, 20% is 12.8 GB of dirty rope before writers throttle — write-heavy services set `vm.dirty_bytes` (tens of MB) to buy small frequent stalls.

## See it yourself

Any Linux box. Watch the write lie live:

```console
$ grep -E '^(Cached|Dirty|Writeback):' /proc/meminfo
Cached:          4823320 kB
Dirty:               176 kB
Writeback:             0 kB

$ dd if=/dev/zero of=/tmp/blob bs=1M count=500 status=none
# in a second terminal, while/after the write:
$ for i in $(seq 10); do grep -E '^(Dirty|Writeback):' /proc/meminfo; sleep 2; done
Dirty:            512000 kB      <- the 500 MB "completed" but lives in RAM
Writeback:           0 kB
Dirty:            468000 kB      <- flusher draining it (5 s cadence)
Writeback:         44000 kB      <- in flight right now
...
```

The 500 MB `dd` finishes in a fraction of a second; `Dirty` spikes to ≈ 512,000 kB and drains over the next 5-30 s. (On a small-RAM box, 500 MB may exceed the 10% background threshold and throttle the writer mid-`dd` — itself a demonstration.)

Measure the fsync gap:

```python
#!/usr/bin/env python3
import os, time
N, DATA = 200, b"x" * 4096

def bench(do_fsync):
    tag = "fsync" if do_fsync else "close"
    path = f"/tmp/bench-{tag}.bin"
    t0 = time.perf_counter()
    for _ in range(N):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        os.write(fd, DATA)          # lands in the page cache only
        if do_fsync:
            os.fsync(fd)            # forces ladder + device FLUSH
        os.close(fd)                # note: close, by itself, flushes NOTHING
    dt = time.perf_counter() - t0
    print(f"{tag:11s}: {dt/N*1e6:9.1f} us per 4 KiB write")
    os.unlink(path)

bench(False)
bench(True)
```

Typical results: close-only 40-300 µs per write (a memcpy); fsync 0.3-2 ms on a consumer NVMe with a volatile cache, 10-20 ms on a 7,200 rpm HDD, tens of µs to ~0.2 ms on a PLP drive. That 10-100× spread is rung (4) of the ladder.

Watch the one shared offset, live:

```console
$ exec 3</usr/bin/python3          # any file; fd 3 in THIS shell
$ head -c 1048576 <&3 > /dev/null  # read 1 MB through the redirected fd
$ cat /proc/$$/fdinfo/3
pos:    1048576                    <- advanced by exactly 1 MB
flags:  0100000                    <- octal: O_RDONLY | O_LARGEFILE
```

Run the `head` again: `pos` doubles — one fd, one `struct file`, one offset.

See which pages of a file are cached (`apt install vmtouch` if missing):

```console
$ vmtouch /usr/bin/python3
           Files: 1
     Directories: 0
  Resident Pages: 3102/12242 (32%/0.99M of 47.8M)
```

Then the fadvise knobs, documented: `os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)` evicts the file's clean pages (drop, re-read, compare timings); `POSIX_FADV_WILLNEED` prefetches now. And the namespace arithmetic: `ls -li`, `stat -c 'inode %i links %h size %s' f`, then `ln f g` — links go 1 → 2 on one inode.

## How to use this knowledge

- **"Free memory is 0"** is not an incident. Read `free -h`'s available column and `/proc/meminfo`'s Dirty/Writeback before paging anyone; if Dirty is huge, *that* is the story.
- **A bulk writer freezing in D state every few minutes** is `balance_dirty_pages`: Dirty crossing 20% of RAM. Fix with `vm.dirty_bytes` sized to what the device drains in ~1 s.
- **Zero-length files after a crash** on ext4: look for write-close-rename with no fsync; fix with the durable-replace recipe, directory fsync included.
- **Buying storage for a latency-critical database or log**: benchmark `fio --fsync=1` on the actual drive and demand power-loss protection; the label does not certify it.

## Connections

- Module 01 ch03 (demand paging) — mmap is that machinery with a file as backing store.
- Module 01 ch04 (fork) — the interleaved-read behavior is `struct file`'s refcount and shared offset, now drawn.
- Module 01 ch05 + ch19 (allocators) — the page cache is a heap in front of the disk, the inode its object header; this module's ch01 plays the same game in FTL firmware.
- Module 11 ch01 (fsync, torn writes) — this chapter adds the device-cache rung and FLUSH/FUA.
- Module 11 ch02 (buffer pool, B-trees) — why databases take `O_DIRECT`; the B-tree returns as ext4's extent index in ch03.
- Module 14 ch01 — the latency ladder explains why 30 s of dirt and a 5 s journal interval are tolerable.
- Module 14 ch03 — delayed allocation, JBD2, and `auto_da_alloc` complete the crash story.
- Module 14 ch05 — dentry/inode slab costs; overlayfs copy-up.
- Module 15 (queued, containers) — overlayfs builds on this chapter's VFS objects.

## Quiz

1. Two shells each run `cat movie.mkv` at the same time, and each prints the entire file. A program instead opens the file once and `fork`s; parent and child both read from that inherited fd, and they interleave — roughly alternate chunks. Name the kernel object and field that differs between the scenarios, and state the rule for how each `open()` behaves.

2. ext4-style file system, Linux 6.x. You run `mkdir /d`, then create subdirectories `a`, `b`, `c` inside it, then `rmdir /d/b`. What does `stat -c '%h' /d` report at the end, and where did each contribution to the number come from?

3. A Python script writes 300 MB to a fresh file on an 8 GB RAM box using 1 MB `write()` calls, closes the fd, and immediately greps `Dirty` in `/proc/meminfo` in a 2 s loop. Roughly what Dirty value does it see, does the writer ever stall, and how long does Dirty take to drain? Name the thresholds and cadences involved.

4. Process A opens `/shared/state` with `O_DIRECT` and overwrites a 4 KB record at offset 40960 with a new value, using a `posix_memalign`-ed buffer. Process B, doing buffered `read()`s on the same file, keeps seeing the old value. The write verifiably reached the disk. What is the mechanism, and what are two fixes?

5. On ext4, an application writes a new version of `config.ini`, closes it, and `rename()`s it over the old path — no fsync anywhere. The machine loses power 3 s after the rename. After reboot, `config.ini` exists but is zero-length. Explain the exact ordering that produced this, name the kernel fix, and give the correct code sequence.

6. A service writing a 40 GB import file on a 64 GB RAM server freezes for several seconds every few minutes, in D state, even though the disk is an NVMe that should absorb the load. `/proc/meminfo` at freeze time shows Dirty ≈ 13 GB. What code path is the process stuck in, what default knob put it there, and what is the standard fix and its trade?

7. PostgreSQL opens its data files with `O_DIRECT` rather than letting the kernel cache them. Connect this to module 11 ch02: name the two caches that would hold the same page twice, the two costs of that duplication, and the alignment trap an unaligned access into a direct-I/O fd can hit.

8. Two NVMe drives both marketed as enterprise: drive X completes 4 KB write+fsync in 40 µs, drive Y in 2 ms. Neither is lying about throughput. What physical difference explains the fsync gap, and what does the FLUSH command actually do on each?

<details><summary>Answers</summary>

1. **The `struct file` (specifically `f_pos` and `f_count`).** Two independent `open()` calls each construct a *new* `struct file` with its own offset, so both readers see the whole file. After one `open()` plus `fork()`, both processes' fd tables point at the *same* `struct file` (`f_count` = 2), so there is one shared `f_pos`: each `read` advances it for both, and the chunks interleave. `dup`/`dup2` reproduce the shared case — module 01 ch04's fork semantics made concrete.

2. **4.** Fresh directory: nlink 2 — its entry in the parent plus its own `.`. Each of a, b, c adds a `..` record pointing back at /d: 2 + 3 = 5. `rmdir /d/b` removes b's records (including its `..`): back to 4. The number is the *incoming edge count* — also why hard links to directories are forbidden (cycles).

3. **Dirty spikes to ≈ 307,200 kB; no stall; drains within ~30 s.** The writer stalls only above `vm.dirty_ratio` = 20% of 8 GB = 1.6 GB (background flushers wake at 10% ≈ 820 MB, never reached; writeback starts on the 5 s cadence). Pages may sit dirty up to 30 s (`dirty_expire_centisecs=3000`), so they drain over roughly the next 5-30 s. The write completed at memcpy speed — the point: `write()` returning said nothing about the disk.

4. **The O_DIRECT/buffered coherence hole.** The direct write went to the device without updating or portably invalidating the page-cache pages; B's buffered reads are cache hits serving the stale pre-write copy. Fix one: don't mix modes — make B read O_DIRECT too. Fix two: evict around the direct write — `posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED)` before/after, so B's next read faults in the new contents.

5. **Delayed allocation + journaled rename.** The data lived only in the page cache, no blocks reserved; close() flushes nothing. The rename is metadata-only and journaled, so within the 5 s journal commit the new name replaced the old — durably — while the new file's data had no on-disk blocks. Power loss destroys the page-cache-only data: name resolves, content gone, size 0. The kernel fix is `auto_da_alloc` (default since 2.6.30): ext4 detects the rename-over pattern and forces allocation/writeback before the rename commits. Correct sequence: `open temp → write → fsync(temp fd) → rename → fsync(dir fd of final path)` — the last step makes the rename itself durable.

6. **`balance_dirty_pages()`.** Dirty crossed `vm.dirty_ratio` (default 20% of 64 GB ≈ 12.8 GB ≈ the observed 13 GB), so the kernel drafted the writer itself into synchronous writeback until the level drops — the "stalls randomly on writes" class. Fix: set `vm.dirty_bytes` (with `dirty_background_bytes` lower, e.g. 64-256 MB) so writeback starts early and throttling is brief. Trade: more frequent, smaller flushes — you pay cadence for tail latency.

7. **The database's buffer pool and the kernel page cache would both hold the same page.** Costs: (a) RAM duplication — twice the cache for the same data, cutting effective cache size; (b) loss of control — the DB's eviction policy (module 11 ch02) knows the access pattern, while the kernel adds writeback-stall latency the DB cannot predict. Alignment trap: file offset, length, and buffer address must be multiples of the logical block size (512/4 KiB) or the syscall fails with `EINVAL` — an unaligned buffered-style read into the direct fd fails outright.

8. **Power-loss protection capacitors.** Drive X carries PLP: its DRAM write cache can drain to NAND even after power failure, so it reports the cache as non-volatile and FLUSH is essentially a no-op — fsync costs little beyond the write itself. Drive Y has a volatile cache: FLUSH forces a synchronous DRAM→NAND drain, a millisecond-class operation. Same sequential throughput, 50× different fsync latency — measure it (`fio --fsync=1`), don't infer it from the label.

</details>
