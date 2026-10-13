# Chapter 05 — Special file systems and the unified picture

> **The 30-second version.** Half the file systems on a running Linux box
> have no disk. procfs and sysfs are kernel memory rendered through
> open/read/seek — reading `/proc/meminfo` calls a C function at read time,
> which is why its `stat` size is 0: the content does not exist until you
> ask. tmpfs is page-cache pages with no backing block device; overlayfs is
> two real file systems presenting one merged tree (the container engine's
> foundation); FUSE moves the file system into a userspace daemon; NFS moves
> it to another machine. What unifies them is chapter 02's VFS trio —
> fd, dentry, inode — which each of them implements. The chapter closes the
> module with one `write(fd)` walked from the fd table through the dirty
> page, ext4's allocator, the block layer, the NVMe queue, and the FTL down
> to the NAND page, every arrow tagged with the chapter that explained it —
> plus the reclaim loop that walks back up when memory runs short.

## Kernel memory wearing a file costume: procfs and sysfs

Start from a strange observation. `cat /proc/meminfo` prints roughly 50
lines (~1.7 KB of text on Linux 6.x). `stat -c %s /proc/meminfo` prints
`0`. Both are true, because **procfs files contain no bytes anywhere** —
no on-disk or in-RAM blob holds that text. What exists is a registration:
"the file called `meminfo` is produced by *this function*." When you
`read()` it, the kernel calls the function, which formats the current
values of kernel variables into a buffer, and copies that buffer to you.
The machinery is `seq_file`, in the kernel since the Linux 2.4 era (2001):
each procfs file registers a `show()` callback that emits lines via
`seq_printf`, and the read path serializes calls to it, handling buffering
and `lseek`:

```c
/* fs/proc/meminfo.c, Linux 6.x, heavily simplified — /proc/meminfo IS this function */
static int meminfo_proc_show(struct seq_file *m, void *v)
{
    /* each "line of the file" is a call, executed at read time,
       against whatever the counters say RIGHT NOW */
    seq_printf(m, "MemFree:       %8lu kB\n",
               global_node_page_state(NR_FREE_PAGES));
    return 0;
}
```

A second read seconds apart yields different text (`MemFree` changed) —
the "content" is a function of wall-clock time, so a fixed `st_size`
would be a lie anyway. This is why `stat` reports 0, and why `strace cat
/proc/meminfo` shows an `fstat` returning `st_size=0` while the very next
`read` returns a ~1,600-byte payload.

The 1970s Unix insight is an **interface pattern**: the kernel needed an
inspection API, and instead of inventing syscalls it reused the 4 that
already existed (open/read/seek/close) — so every generic tool written
over 40 years (`cat`, `grep`, `python`, shell redirection) works on kernel
state with zero new code. The lineage runs from "Processes as Files"
(V8 Unix, 1984) through Plan 9, and the pattern repeats wherever a big
system needs an inspection surface: Android's `dumpsys` (module 05) is
the same idea with a binder RPC instead of a read syscall.

And the pattern is bidirectional: **writes are tuning**. `/proc/sys/` is
the sysctl interface — every knob from the earlier modules
(`vm.dirty_ratio`, `vm.swappiness`, `kernel.sched_child_runs_first`) is
literally a file whose `write()` handler copies parsed bytes into a kernel
variable. `echo 3 > /proc/sys/vm/drop_caches` is a write syscall mutating
kernel memory. There is no other API; the file *is* the API.

sysfs is the same idea aimed at the device model. The kernel organizes
devices as a tree of kobjects; sysfs exposes that tree as directories, with
**one file per attribute** (`/sys/class/block/nvme0n1/size` holds the
device's sector count as ASCII). Reads call attribute `show` callbacks,
writes call `store` callbacks — the seq-file shape with a narrower
contract. udev's rule engine is the consumer: the kernel announces
hotplug events on a netlink socket, udev's rules match against sysfs
attributes (`ATTRS{serial}=="..."`) and adjust `/dev` (devtmpfs creates
the bare node; udev adds permissions and symlinks). Modern Linux hotplug
is grep-like matching over a synthetic file system.

## tmpfs: the page cache with the disk amputated

Chapter 02 taught the page cache as "the heap in front of the disk":
every page belongs to some (inode, offset). tmpfs asks the follow-up:
**what if there is no disk, and the cache is the whole file system?** A
tmpfs inode's data pages are page-cache pages that are dirty by
construction and will *never* be written back — no backing block device,
no writeback path, no `Dirty` accounting (they count as `Shmem` in
`/proc/meminfo`, not `Cached`).

Three consequences, each with a number:

1. **`df` on tmpfs reports a memory budget, not a device.** The `size=`
   mount option caps it; the default (per `tmpfs(5)`) is **half of RAM**,
   and `/dev/shm` — the POSIX shared-memory mount — ships with exactly
   that cap on mainstream distros. Distro-managed `/tmp` tmpfs (systemd's
   `tmp.mount`) varies by distribution, commonly 10-50% of RAM: check with
   `findmnt -no SIZE /tmp`.
2. **tmpfs pages can swap.** They are not pinned: under memory pressure
   they are evicted to *swap* — the only "disk" a tmpfs page will ever
   touch. With no swap configured (a common CI-box setup), tmpfs pages
   are effectively unmovable RAM — "free until it isn't": free while
   empty, real memory the moment you fill it.
3. **ENOSPC from tmpfs is a memory error wearing a disk error's clothes.**
   The classic incident: a CI box (16 GB RAM, no swap) mounts `/tmp` as
   tmpfs; the build writes an 8 GB artifact there; the OOM killer shoots
   the linker mid-run while `df -h /` reports 500 GB free. We diagnose
   exactly this in the quiz.

## overlayfs: one tree out of many, and the copy-up bill

Containers need a file system with a strange shape: many read-only image
layers plus one writable per-container layer, merged into one view.
overlayfs (mainline Linux 3.18, 2014; multiple lower layers since 4.0,
2015) is that merge. It mounts a **lowerdir stack** (read-only, top of the
stack wins), one **upperdir** plus a **workdir** (both on the same
writable file system, used for renames and pending copy-ups), and exposes
a **merged** view. Lookup walks the layers top-down and returns the first
hit; the namespace looks like one tree. Two mechanisms carry all the weight:

**Copy-up.** The first write to a file that lives in a lower layer cannot
modify it — it is read-only and shared with every container using that
image. Instead the whole file is copied into the upperdir (data, mode,
ownership, xattrs, ACLs), and *then* the write is applied to the upper
copy. Editing 1 byte of a 100 MB file in a container costs a ~100 MB copy.
This is the "containers are cheap until you edit big files" bill, and
module 15 ch04's container foundation: a database writing into a file
baked into its image layer pays copy-up on first write.

**Whiteouts.** Deleting a lower-layer file can't touch the lower layer
either, so the upper layer gets a marker that hides it: a **character
device entry with major 0, minor 0** in the upper directory. (Directory-wide
hiding uses the xattr `trusted.overlay.opaque` instead.) One `rm` in a
container of a file that appears in 3 image layers is: delete from one
layer, whiteout in the other two — one user action, multiple layer
effects.

The multi-layer stack raises the cost question: is a path lookup O(1) or
O(n)? Worst case, resolving one component consults every layer. In steady
state the dentry cache merges the layers, so hot paths never pay the walk —
but a cold `find /` over a 20-layer image pays per-component per-layer
lookups. Dentry caching hides most of the cost; it does not remove the
O(n layers) worst case.

## File systems that leave the kernel: FUSE and NFS

VFS is a set of operations (lookup, read, write, readdir...) that the
kernel calls. Nothing in that contract says the *implementation* must be
in the kernel. Two escape routes matter.

**FUSE** (mainline Linux 2.6.14, 2005) puts the implementation in a
userspace daemon. The kernel's FUSE module forwards each VFS operation to
the daemon over `/dev/fuse`: your `read()` on an sshfs file parks the
request, the kernel switches to the daemon (context switch 1), the daemon
replies, the kernel switches back to your process (context switch 2).
**Minimum 2 context switches per operation** — roughly 1-2 µs each on
modern x86-64 hardware — before the daemon does anything useful. The
arithmetic of "sshfs is 10-50× slower than NFS for small files" is
exactly this: NFS's operation is one in-kernel RPC; sshfs is a FUSE round
trip plus sftp plus ssh encryption, all in userspace. Yet FUSE wins the
ecosystem argument: s3fs, sshfs, gitfs, and historically ntfs-3g (the
NTFS driver most distros shipped for years) exist because a FUSE daemon
is ordinary userspace — isolated, killable, no kernel bugs. The insider
cost: **double buffering** — the kernel still runs its page cache in front
of the FUSE file while the daemon buffers its own copy, so a big
sequential read can hold 2× the data in RAM; and since Linux 3.15 (2014)
FUSE's `writeback_cache` mode makes writes reach the daemon only later —
write ordering crosses a process boundary.

**NFS** moves the implementation to another machine, and its trouble is
that a network is not a disk. v3 (RFC 1813, 1995) is stateless —
operations address opaque file handles, locking is a bolt-on side
protocol (NLM) famous for its failure modes. v4 inverted this: stateful
opens, share reservations, and delegations where the server grants a
client exclusive caching rights. The model to state plainly is
**close-to-open consistency**: on `open`, the client revalidates what it
cached; a modification becomes visible to other clients when the writer
closes (data flushed, attributes updated). *Between* opens, the client
caches aggressively — NFS is a cache with a protocol, not a shared disk,
and every war story about two processes appending to one file over NFS
and losing each other's writes is the price of assuming otherwise.

Two numbers make the caching concrete. The Linux client's attribute
cache holds attributes between **3 s (acregmin) and 60 s (acregmax)** by
default — which is precisely why "the other machine deleted the file 10 s
ago but `ls` still shows it" happens: the cached attributes were younger
than the timeout, so no revalidation RPC went out. And the **LOOKUP
storm**: `ls -l` on a 1,000-file directory needs each file's attributes,
but v3's READDIR returns only names and file IDs — so the client issues
1 READDIR plus **1,000 GETATTR RPCs** (~1,001 round trips minimum, each
paying network latency). v3's READDIRPLUS returns attributes with the
listing, collapsing the storm to a few chunked calls — at the cost of
per-entry attribute work on the server, heavy enough that some
servers/clients turn it off. No free setting, only a trade you can name.

## One write, end to end: the module in a single diagram

Everything the module built, in one picture. Follow a single
`write(fd, buf, 4096)` on an ext4 file on an NVMe SSD; each box is a
chapter's territory, each arrow the interface between them:

```text
write(fd, buf, 4096)                          the syscall
  |  [1] trap into the kernel                 ~100-300 ns of mode switch
  v
+--------------------------------------------------------------+
| fd table: fd 3 --> struct file { pos, flags, refcount }      |  ch02
| (fork/dup share this struct; a fresh open() gets a new one)  |
+--------------------------------------------------------------+
  |  [2] f_path, resolved through the dentry cache
  v
+--------------------------------------------------------------+
| dentry: "notes.txt" — the NAME, cached in the 'dentry' slab  |  ch02
+--------------------------------------------------------------+
  |  [3] d_inode
  v
+--------------------------------------------------------------+
| inode: ino 12345 — identity: size, extent-tree root          |  ch02
| (what is INSIDE it on disk: ch03's extents)                  |
+--------------------------------------------------------------+
  |  [4] page-cache lookup: (inode, offset) in the XArray
  v
+--------------------------------------------------------------+
| page-cache page, 4 KiB, now DIRTY                            |  ch02
| *** write() RETURNS HERE — zero I/O has happened ***         |
+--------------------------------------------------------------+
  |  [5] writeback, LATER: fsync, or dirty pages crossing       |  ch02
  |      vm.dirty_ratio (default 20% of RAM), or 30 s age,      |
  |      or memory-pressure reclaim
  v
+--------------------------------------------------------------+
| ext4: delayed allocation — the extent covering these bytes   |  ch03
| is chosen NOW, at writeback, with the file's full shape      |
| visible; JBD2 logs the metadata first, commit block + CRC    |  ch03 = module 11 ch04's WAL
+--------------------------------------------------------------+
  |  [6] a bio: (target device, logical block, the dirty page)
  v
+--------------------------------------------------------------+
| block layer: merging + I/O scheduler — 'none' on NVMe,       |  ch01
| 'mq-deadline' on SATA (Linux 5.x-6.x defaults)               |
+--------------------------------------------------------------+
  |  [7] write to the NVMe submission-queue tail doorbell (MMIO) | ch01
  |      queue pair, up to 64 K entries deep
  v
+--------------------------------------------------------------+
| NVMe controller: fetches the command, runs 8-16 NAND         |  ch01
| channels in parallel
+--------------------------------------------------------------+
  |  [8] logical block address through the FTL's mapping table
  v
+--------------------------------------------------------------+
| FTL: LBA -> physical page; may garbage-collect a block       |  ch01
| (WAF: this 4 KiB write can cost 64 KiB+ of NAND programming) |
+--------------------------------------------------------------+
  |  [9] program
  v
NAND page (4-16 KiB) programmed; erase only per 64-256-page block   ch01
```

The diagram's most important feature is where `write()` returns: at the
dirty page, arrow [4]. Everything below runs on the kernel's schedule,
not your process's — chapter 02's "write latency is a lie" is now a
location in a picture you can point at. And the diagram has a return
path — the **reclaim loop**, the feedback edge that makes it a cycle:

```text
        memory pressure: free RAM below the watermark
          |
          v
        kswapd wakes (or reclaim runs in the writer's own context)
          |
          |   for each page the LRU scan selects:
          |     clean page  -> DROP it; a later read faults it back in    ch02
          |     dirty page  -> force it down arrows [5]..[9] NOW          ch02
          |     tmpfs page  -> SWAP-OUT: its only disk is swap            this chapter
          v
        the system performs I/O that no process requested — the
        ch02 "process stalls randomly on writes" incident and the
        OOM-adjacent freeze, now seen as this diagram's feedback edge
```

That is the unified picture: VFS (ch02) is the interface; ext4 (ch03)
and the CoW trio (ch04) are swappable implementations of its lower half;
the block layer and device (ch01) are swappable hardware; and this
chapter's special file systems are implementations whose "lower half" is
nothing (tmpfs), another process (FUSE), or another machine (NFS).

## What insiders know

- `/proc/meminfo` has `st_size` 0 but ~1.7 KB of content on Linux 6.x;
  code that preallocates buffers from `st_size` mishandles all of procfs —
  read until EOF. And reading `/proc/PID/*` of another user's process
  needs `PTRACE_MODE_READ` (root or same uid): docker-exec's "permission
  denied on /proc/1/..." is this, not corruption.
- tmpfs defaults: unspecified `size=` means half of RAM (`tmpfs(5)`);
  `/dev/shm` ships at that 50% cap; distro `/tmp` tmpfs is commonly
  10-50% of RAM — verify with `findmnt -no SIZE /tmp` before planning
  artifacts around it. tmpfs `ENOSPC` is a memory-budget error; the
  smoking-gun counter is `Shmem` in `/proc/meminfo`, and the pages can
  only go to swap, never to a file system.
- overlayfs whiteouts are character devices (major 0, minor 0) in the
  upperdir — `tar` an upperdir naively and you archive junk device nodes;
  `rm` of a file present in N layers performs N layer operations (delete
  or whiteout each). Copy-up also copies xattrs/ACLs: a 100 MB file with
  SELinux labels costs slightly more than 100 MB, not less.
- The FUSE floor is 2 context switches plus a `/dev/fuse` read/write per
  operation — a perfect daemon still caps small-file metadata rates far
  below ext4; with `writeback_cache` (Linux 3.15+) writes look fast until
  fsync, which pays the FUSE round trip for every dirty page at once.
- NFS client knobs (Linux): `acregmin` 3 s, `acregmax` 60 s, `acdirmax`
  60 s; `noac` is widely believed to give coherence but only forces
  attribute revalidation — the data cache still applies between opens.
- `findmnt -T <anypath>` answers "which mount owns this file" — the first
  question of every "wrong file system" debugging session (is
  `/etc/hosts` the real one or the bind-mounted one?).

## See it yourself

All of these run on any Linux box; `sudo` for the mounts and
`drop_caches`.

```console
$ strace cat /proc/meminfo            # the procfs read, decoded
openat(AT_FDCWD, "/proc/meminfo", O_RDONLY) = 3
fstat(3, {st_mode=0, st_size=0, ...}) = 0     # <- size 0, content ~1.7 KB
read(3, "MemTotal:       16000000 kB\nMemF"..., 131072) = 1696
read(3, "", 131072)                     = 0    # EOF
```

Notice: `open` reads nothing, `fstat` says 0 bytes, `read` returns
~1,700. The content was manufactured between the two. Same proof without
strace — `stat -c %s /proc/meminfo` prints `0`:

```console
$ stat -c '%s bytes' /proc/meminfo
0 bytes
```

The tmpfs memory budget, felt directly:

```sh
mkdir /tmp/t && sudo mount -t tmpfs -o size=64m tmpf /tmp/t
df -h /tmp/t                  # Size 64M, Used 0 — a RAM budget, not a disk
dd if=/dev/zero of=/tmp/t/f bs=1M count=60    # succeeds; Used -> ~60M
dd if=/dev/zero of=/tmp/t/g bs=1M count=60    # dies at the 64 MiB cap:
# dd: error writing '/tmp/t/g': No space left on device
grep -E 'Shmem|MemAvailable' /proc/meminfo    # Shmem grew by ~60 MB
sudo umount /tmp/t
```

The mount tree and the caches' memory cost:

```sh
findmnt -T /etc/passwd         # which mount owns this file (SOURCE + FSTYPE)
sudo cat /proc/slabinfo | grep -E 'dentry|inode'   # dentry + ext4_inode_cache:
# real kernel memory — the name cache costs MBs on a box with many files
```

The honest page-cache demo — what the cache buys, before and after:

```sh
dd if=/dev/urandom of=/var/tmp/probe.bin bs=1M count=100  # a 100 MB file
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches   # 3 = pagecache + dentries + inodes
time cat /var/tmp/probe.bin > /dev/null            # cold: from the device
time cat /var/tmp/probe.bin > /dev/null            # warm: from memory
```

Expect the cold read to be several times slower than the warm one on an
NVMe SSD (typical: tens of ms vs single-digit ms for 100 MB) and 10-100×
slower on an HDD (~1 s vs ~0.1 s, drive-dependent) — the second `cat`
performs no device I/O at all.

The overlayfs copy-up bill, observed:

```sh
mkdir -p lower/etc upper work merged
echo "v1" > lower/etc/app.conf
sudo mount -t overlay overlay \
  -o lowerdir=$PWD/lower,upperdir=$PWD/upper,workdir=$PWD/work merged
cat merged/etc/app.conf            # v1 — served from the lower layer
ls upper/etc/                      # empty — nothing copied yet
echo "v2" > merged/etc/app.conf    # THE WRITE: copy-up of the whole file
ls -l upper/etc/app.conf           # now it exists up here
du -sb upper                       # ~ the full file size, not 2 bytes
sudo umount merged
```

## How to use this knowledge

- **"No space left" that `df` contradicts**: run `findmnt -T <path>` first.
  tmpfs cap, overlay upperdir full, or inode exhaustion in upper — three
  ENOSPCs that `df -h /` will never show you; `Shmem` in `/proc/meminfo`
  is the tmpfs audit trail.
- **Container and image design**: never bake the big *mutable* file into
  an image layer — the database, the model checkpoint, the build cache
  (100 MB+ each, one copy-up per container per first write). Volumes and
  bind mounts exist precisely to escape copy-up.
- **NFS application reviews**: put the contract in the design doc —
  "revalidate at open, flush at close" — and reject code that assumes two
  writers on one NFS file stay coherent. Budget the `ls -l` storm (1,000
  files ≈ 1,001+ RPCs) before putting a 100,000-file directory on NFS.
- **Monitoring-agent cost accounting**: every procfs read executes kernel
  code and formats text — 1,000 files polled every 10 s is 100 reads/s of
  serialization; poll at 10-60 s for anything that is a gauge, not an
  event.
- **Reading any storage incident through the diagram**: "slow writes"
  has exactly 9 arrows to check — dirty-page pileup at [4]-[5], journal
  stall in the ext4 box, scheduler/queue depth at [6]-[7], FTL write
  amplification at [8]. The diagram is the debugging checklist; walk it
  top to bottom.

## Connections

- **Module 14 ch01** — the bottom four boxes: scheduler defaults, NVMe
  queue pairs, the FTL's mapping table and WAF, NAND page/block geometry.
- **Module 14 ch02** — the top half: fd/struct file/dentry/inode, the
  dirty page, writeback triggers, fsync, dirty_ratio — plus tmpfs's pages
  feeding that chapter's reclaim machinery.
- **Module 14 ch03** — the ext4 box: delayed allocation at writeback and
  the JBD2 WAL; swapping that box for ch04's CoW root-pointer flip is the
  journaling-vs-CoW comparison made spatial.
- **Module 14 ch04** — the alternative lower half: the same arrows [5]-[9]
  with a Btrfs/ZFS allocator behind them.
- **Module 11 ch01/ch04** — fsync and the WAL: arrows [5]-[6] are the
  durability contract; the journal is ARIES's idea at file-system scale.
- **Module 01 ch03/ch05/ch19** — the page cache as demand paging; the
  allocator taxonomy: tmpfs is a cache with no eviction target but swap,
  and the FTL below it is a garbage-collecting log allocator.
- **Module 05** — dumpsys on Android: the procfs interface pattern with a
  binder transport.
- **Module 15 ch04** — overlayfs copy-up, whiteouts, and layer stacks are
  this chapter's direct handoff: the container file-system half, pre-built.

## Quiz

1. `stat -c %s /proc/meminfo` prints `0`, yet `cat /proc/meminfo` prints
   ~50 lines. Where do the bytes come from, when are they produced, and
   why would reporting a real size actually be wrong?

2. You run `mount -t tmpfs -o size=64m tmpf /tmp/t` on a 16 GB RAM box
   with 500 GB free on the root ext4. Predict: (a) what `df -h /tmp/t`
   shows immediately; (b) whether writing a 60 MB file succeeds, and
   which `/proc/meminfo` counter moves; (c) what happens on the next
   write and the exact error; (d) how many bytes of that data ever touch
   a disk.

3. A CI box has 16 GB RAM, no swap, and `/tmp` on tmpfs. A build writes
   an 8 GB artifact to `/tmp`; the OOM killer kills the linker mid-build.
   The on-call engineer insists "df showed 500 GB free." Diagnose the
   actual budget that was exceeded, the counter that proves it, and the
   one-line fix.

4. A Docker image bakes a 100 MB config file into a layer. A container
   edits 1 byte of it. Predict the write volume on the container's
   writable layer and explain the mechanism step by step — where do the
   bytes come from and why can't the edit land in place?

5. An NFSv3 client with cold caches runs `ls -l` on a directory holding
   exactly 1,000 files. Count the minimum RPCs (name the operations), and
   name the v3 feature that collapses the storm — plus the cost it
   imposes instead.

6. Machine A deletes a file from an NFS export. Machine B ran `ls` 5 s
   before the deletion and again 10 s after; B still lists the file.
   Diagnose: which cache, which default timeouts, and what event would
   eventually make B see the truth?

7. A team mounts their S3 bucket with s3fs (FUSE). After big sequential
   reads, the s3fs process's RSS grows to multiple GB; meanwhile files
   written by another client appear "minutes late or not at all" for
   readers on this box. Diagnose both symptoms in terms of FUSE's
   caching design.

8. Trace `write(fd, buf, 4096)` on an ext4-over-NVMe file through every
   box of this chapter's end-to-end diagram, naming the module 14 chapter
   that explained each stage — then describe the reclaim loop that fires
   when memory pressure arrives before writeback does.

<details><summary>Answers</summary>

1. **The bytes are manufactured at read time.** Procfs files have no
   stored content: the read path calls the file's `seq_file` `show()`
   callback (for `/proc/meminfo`, a function in `fs/proc/meminfo.c`),
   which formats the current values of kernel variables into a buffer
   copied to the user. A real `st_size` is wrong two ways: computing it
   requires generating the content (a full read on every `stat`), and the
   content is a function of time — `MemFree` changes between any two reads,
   so a cached size is stale the instant it is written. The honest value
   for "content produced on demand" is 0.

2. **(a)** `Size 64M`, `Used 0`, type `tmpfs` — a memory budget, not a
   device. **(b)** The 60 MB write succeeds: 60 MB of page-cache pages with
   no backing file, and `Shmem` in `/proc/meminfo` climbs by ~60 MB (tmpfs
   pages count as Shmem, not Dirty — no writeback target). **(c)** The next
   write crosses the 64 MiB `size=` cap and gets `ENOSPC` (`dd: error
   writing '...': No space left on device`) even though the root ext4 has
   500 GB free. **(d)** Zero bytes touch any file system — tmpfs has no
   backing block device; the only disk a tmpfs page can reach is swap.

3. **The exceeded budget is RAM, not disk.** `/tmp` as tmpfs makes the
   8 GB artifact 8 GB of `Shmem` pages that cannot be dropped (they are
   dirty by construction) and cannot be swapped (no swap). With the
   build's own working set (commonly 1-3 GB) on top, free memory
   collapses; the OOM killer picks the largest-RSS process — the linker.
   Proof: `grep Shmem /proc/meminfo` (≈8 GB and climbing) and `df -h
   /tmp` near its cap, while `df -h /` measured an irrelevant budget.
   Fix: write artifacts to a disk-backed path, or size the tmpfs cap
   deliberately.

4. **~100 MB.** The file lives in a read-only lower layer shared with
   every container from that image, so the edit cannot land in place.
   overlayfs **copy-up** fires: the entire file is copied into the
   container's writable upperdir — data plus metadata (mode, ownership,
   xattrs/ACLs, so slightly over 100 MB) — and only then is the 1-byte
   modification applied to the upper copy. The bill is paid once per file
   per container; the fix is a volume/bind mount for big mutable files,
   which bypasses the layer stack.

5. **Minimum ~1,001 RPCs**: 1 READDIR (chunked — realistically 1-8 calls
   for 1,000 entries) returning names plus file IDs, then one GETATTR per
   file for the `-l` attributes — 1,000 GETATTRs — plus a few LOOKUPs for
   the path itself. Each round trip pays full network latency, hence a
   cold `ls -l` on a big NFS directory takes seconds. **READDIRPLUS**
   (v3) returns attributes with the listing, collapsing the 1,000 GETATTRs
   — at the cost of per-entry attribute work on the server, which is why
   some servers or clients disable it for huge directories.

6. **The client-side attribute cache.** B's cached directory and file
   attributes are revalidated only when older than the timeout window —
   Linux defaults: `acdirmax` 60 s for directories, `acregmin`/`acregmax`
   3-60 s for files. B's entries were seconds old, well inside the window,
   so the post-deletion `ls` served stale cache with zero RPCs sent. The
   truth arrives when the entry ages out (up to 60 s), or on a trigger: B
   opens the file (close-to-open forces revalidation), the entry is
   evicted, or the admin mounts with `actimeo=0` (staleness traded for a
   GETATTR storm). NFS caches aggressively *between* opens — a cache, not
   a shared disk.

7. **Two caches, both policy-driven.** Symptom 1 (RSS): FUSE does not
   disable the kernel page cache — the box caches the S3 files' pages in
   kernel memory *and* s3fs buffers data in its own process, so sequential
   reads leave roughly 2× the data resident: FUSE's double-buffering made
   visible. Symptom 2 (staleness): FUSE coherence is timeout policy, not
   protocol — entry/attribute validity expires after configured seconds,
   and in `writeback_cache` mode writes sit in the page cache before the
   daemon even sees them, while other clients' writes land in S3 unseen.
   Fix: shorter timeout options and re-open to revalidate — the same
   close-to-open discipline as NFS.

8. **Top to bottom:** fd 3 indexes the fd table to a `struct file`
   (position, flags, refcount) — **ch02**; the path resolves through the
   dentry cache to the dentry (the name) — **ch02**; the dentry points to
   the inode (identity, extent-tree root) — **ch02** with the on-disk
   extent layout from **ch03**; the write marks the page-cache page at
   (inode, offset) dirty — **ch02** — and `write()` returns *here*, before
   any I/O. Later, writeback (fsync, the 20%-of-RAM `dirty_ratio`, 30 s
   age, or reclaim) pushes the page through **ext4's delayed allocation**
   and the **JBD2 journal** (metadata logged first — the WAL) — **ch03**;
   a bio (device, LBA, page) enters the **block layer** (`none` on NVMe,
   `mq-deadline` on SATA) — **ch01**; the queue doorbell hands it to the
   **NVMe controller** (8-16 channels) — **ch01**; the **FTL** maps LBA to
   physical page (maybe GC-ing a block, WAF ≥1) — **ch01**; the **NAND
   page** is programmed — **ch01**. Reclaim loop: pressure wakes kswapd,
   which scans the LRUs — clean pages drop, dirty pages are forced down
   this same pipeline, tmpfs pages go to swap: I/O no process asked for,
   closing the feedback edge (**ch02**).
</details>
