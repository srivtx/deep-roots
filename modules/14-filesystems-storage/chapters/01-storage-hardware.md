# Chapter 01 — Storage hardware from first principles

> **The 30-second version.** A file system is an allocator, and every allocator's
> shape comes from its medium. On a spinning disk the cost of an access is
> mechanical — seek (8-12 ms average on a 7,200 rpm SATA drive) plus half a rotation
> (4.17 ms) — capping random 4-KiB reads at roughly 60-80 IOPS per spindle, the
> number that killed databases on hard drives. Flash's cost is structural: a
> floating-gate cell only programs bits 1-to-0 at page granularity (4-16 KiB) and
> only resets by erasing its entire block (64-256 pages), so every SSD runs a
> firmware garbage collector — the flash translation layer — a log-structured
> allocator that charges write amplification (1.5-3x in the good case, 64x in the
> worst). And because a flash controller is internally parallel (8-16 channels),
> NVMe offers deep queues — 64K entries where AHCI had 32. Every later chapter —
> inodes, journals, copy-on-write trees — answers the numbers in this one.

## The platter: latency is geometry

A hard disk is a geometry problem you can hear. A 3.5-inch drive stacks 1-8 platters,
both surfaces of each magnetized, each surface read by its own head, all heads riding
one actuator arm. Data lives in concentric tracks divided into sectors — 512 B each
historically — and the drive only reads or writes whole sectors. Outer tracks have
about 2x the circumference of inner ones and carry proportionally more sectors
(zoned bit recording: ~1,600 sectors per inner track versus ~3,000 outer on a 1-2 TB
7,200 rpm drive). The drive translates the geometry away and presents linear logical
block addresses; the address is a courtesy, the mechanics are what you pay.

```text
        actuator pivot --- all heads (2-16) move together as one arm
              |
              v   +--------------------------------------------------+
                  | track 0   [sec][sec][sec][sec][sec]... ~3,000 sectors
   outer, longer | track 1   [sec][sec][sec][sec]...
   circumference | ...
   = more sectors| track 99  [sec][sec][sec]...           ~1,600 sectors
                  |           spindle: 7,200 rpm -> one rotation = 8.33 ms
                  +--------------------------------------------------+
                     average wait for the wanted sector to come around: 8.33/2 = 4.17 ms
```

One random read costs three sequential things:

1. **Seek** — move the arm and settle on the track: 8-12 ms average on a 7,200 rpm
   SATA drive (~1 ms track-to-track, 15-20 ms full stroke), assuming a uniformly
   random target.
2. **Rotational latency** — wait for the wanted sector: 7,200 rpm is one rotation
   per 8.33 ms, average wait half of that, 4.17 ms.
3. **Transfer** — the bytes stream by at the outer track's 150-250 MB/s, so a 4 KiB
   read costs 4 KiB / 200 MB/s = 0.02 ms.

```text
random 4 KiB read  =  seek   + rotation + transfer
                   =  12 ms  + 4.17 ms  + 0.02 ms   (7,200 rpm SATA)
                   =  16.19 ms
IOPS ceiling       =  1000 / 16.19 = 62 IOPS         (queue depth 1)
```

With an 8 ms seek it is 83 IOPS. That is the entire budget: a random-4-KiB reader
gets 60-83 IOPS per spindle, forever. With a full queue of 32 commands the drive
reorders them into an elevator sweep and reaches ~100-200 IOPS — the ceiling moves,
the physics does not.

The punishment for ignoring geometry is the sequential-to-random ratio: the same
disk reads *adjacent* 4 KiB blocks at 150-250 MB/s = 37,000-61,000 reads/s —
500-1,000x the random rate. One device, two personalities, separated only by
locality; defragmenters, log-structured file systems, LSM-trees, and readahead all
exist to convert one into the other. One corollary insiders carry: a 15,000 rpm
SAS drive rotates in 4 ms (2 ms average) and seeks in 3.4-4 ms, so
1000/(3.5 + 2 + 0.02) = 181 IOPS — 3x the IOPS at several times the price, the
whole pre-flash SAN arithmetic, occasionally sweetened by short-stroking the outer
10-20% of tracks for another measured 1.5-2x.

## One sector, two sizes: the 512e alignment tax

The sector is the atomic unit of the disk interface: a write lands entirely or not
at all, which is where torn-write reasoning starts (chapter 03). Around 2010-2011
the industry moved to 4 KiB physical sectors ("Advanced Format") because bigger
sectors waste less platter on servo gaps and ECC. Drives that still speak 512 B on
the wire are **512e** — each physical 4 KiB sector emulates 8 logical ones — while
**4Kn** drives expose 4 KiB directly.

The tax: a 4 KiB software structure not aligned to the drive's 4 KiB *physical*
sectors splits every operation. The infamous case is LBA 63: classic CHS-era
partitioning placed the first partition at logical sector 63 = byte 32,256, and
32,256 mod 4,096 = 3,584, so the partition begins 3,584 bytes into physical sector
7. Every 4 KiB file-system block on that partition straddles two physical sectors:
one read becomes two, and one 4 KiB *write* becomes a read-modify-write of both
physical sectors — 2 reads plus 2 writes to service 1 write, a device-level write
amplification of 2x plus RMW latency, on every block, forever.

Hence the folklore: the number 63 is why 512e drives were slow, and every modern
partitioning tool has aligned to 1 MiB (sector 2048) since ~2010, because 1 MiB is
divisible by 4,096 and by every block size in current use. Cloned disk images
still carry the bug — alignment lives in the partition table, and `dd` copies it
faithfully.

## NAND: bits held in an electrically isolated trap

A flash cell is a transistor with a second gate — the floating gate — suspended in
oxide between the control gate and the channel. Programming pushes electrons onto
it by quantum tunneling; the trapped charge shifts the cell's threshold voltage; a
read biases the control gate and checks whether the channel conducts. The gate is
insulated, so the charge persists unpowered for years (retention specs: 1-3 years
for worn cells, 10+ for fresh ones). A bit as a physical quantity of electrons —
that is the entire "non-volatile" trick.

Bits per cell: SLC 1 bit (2 threshold levels), MLC 2 (4), TLC 3 (8), QLC 4 (16).
More levels means narrower voltage windows: slower pulse-and-verify programming and
faster oxide wear. The ladder, in program/erase (P/E) cycles per cell:

| cell type | bits | levels | P/E cycles (spec class) |
|-----------|------|--------|-------------------------|
| SLC        | 1    | 2      | 50,000-100,000          |
| MLC        | 2    | 4      | 3,000-10,000            |
| TLC        | 3    | 8      | 1,000-3,000             |
| QLC        | 4    | 16     | 300-1,000               |

Two unit sizes, and the asymmetry between them creates everything else: the
**page** is the read and program unit, 4-16 KiB (16 KiB typical of current TLC/QLC);
the **block** is the erase unit, 64-256 pages (1-4 MiB at 16 KiB pages).

```text
   one NAND block = the ERASE unit (64-256 pages; 1-4 MiB at 16 KiB pages)
  +--------+--------+--------+---------------------------+---------+
  | page 0 | page 1 | page 2 | ...                       | page 63 |
  | 16 KiB | 16 KiB | 16 KiB |                           | 16 KiB  |
  +--------+--------+--------+---------------------------+---------+
    read unit:  one page         program unit: one page, bits 1 -> 0 ONLY
    erase:      the WHOLE block, every bit reset to 1 -- neighbors included
```

The physics rule: programming only drives bits from 1 to 0; restoring a 1 requires
an erase, and erase granularity is the entire block. Overwriting one page in place
would mean erasing its block first, destroying the 63-255 neighboring pages of
still-valid data. This single constraint — erase-before-write, at block granularity,
beneath page-grained writes — is *why* every SSD contains a garbage-collecting
allocator in firmware. The rest of this chapter is derived from it.

## The FTL: a log-structured allocator with a garbage collector

The firmware's contract with the OS is the 1970s disk contract: linear LBAs,
randomly overwritable, indefinitely. The medium's rules: program only fresh pages,
erase only whole blocks, wear all blocks evenly. The reconciliation is indirection.
The **flash translation layer (FTL)** keeps a map from logical page address (LPA) to
physical page address (PPA) — a B-tree or hybrid page-granularity table cached in
DRAM. A write does not touch wherever that logical page used to live; it programs a
page in the current write block, updates the map, and the old page becomes garbage.
Writes append, the map is the index, stale pages pile up behind it: a log-structured
allocator by definition.

```text
   what the OS writes           the FTL map (DRAM, ~4 B per page)      what exists in NAND
  +----------------+     +----------------------------+     +------------------------+
  | LBA 16384      |---->| LPA 16384 -> PPA 8217      |---->| block 128, page 25     |
  | LBA 16385      |     | LPA 16385 -> PPA 8218      |     | block 128, page 26     |
  | LBA 16386      |     | LPA 16386 -> PPA 9100      |     | block 141, page 12     |
  | (overwrite it) |     | old 16386 entry -> garbage |     | block 128, page 40:    |
  |                |     | (GC food)                  |     |  superseded, not freed |
  +----------------+     +----------------------------+     +------------------------+
     dense, sequential          logical -> physical            sparse: writes append
     (the 1970s contract)       rebuilt at power-on            into fresh blocks (a log)
```

The map is not small: 1 TB of 4 KiB pages is 268 million entries, about 1 GB of
DRAM at 4 B each — why 1 TB consumer drives carry 1 GB of DRAM, and why DRAM-less
drives stutter under random access. It is checkpointed to flash and rebuilt at
power-on, which is why a large SSD can spend seconds recovering after power loss.

**The garbage collector.** To recycle a block — erase it and recover its pages —
every still-valid page in it must first be copied elsewhere. Best case the block is
mostly garbage: copy 4 of 64 pages, erase, collect 60 free pages. Worst case, derive
it: the drive is 100% full, every page in a 64-page block is valid, TRIM was never
issued, and the host overwrites one page. The FTL must read the other 63 valid
pages, write all 63 to fresh pages, write the new version of page 64, then erase the
old block: 64 flash page-writes to service one host page-write, **WAF = 64** for
that write. Real drives with TRIM run 1.5-3 in mixed workloads; a 90%-full or
TRIM-less drive runs 5-10+. That is the "SSD slows down when it's 90% full"
mechanism in two sentences: past ~90% occupancy almost every block the GC recycles
is mostly valid, so each host write drags 5-10 flash writes behind it, and the
drive's internal bandwidth saturates before yours does.

**TRIM** is the OS holding up its side. When a file is deleted, the file system
(chapter 03) knows its blocks are dead — the disk contract has no "delete," so the
map still shows them valid and the GC faithfully copies garbage around forever. The
TRIM command (ATA TRIM, NVMe Deallocate) reports dead LPAs so the FTL can drop the
map entries. Linux issues it per-delete (`discard` mount option) or in a weekly
batch (`fstrim.timer`, the systemd default on most distributions).

Three systems, one idea — name all three, because the program meets each again: the
**FTL's garbage collector**, an **LSM-tree's compaction** (module 11), and the
**tracing collector's mark-and-sweep reclamation** (module 01 ch14's cycle
detector). All three append to a log-like structure, accumulate dead entries, and
reclaim by identifying the live subset, copying it to fresh space, and freeing the
old region wholesale — paying write amplification to buy free space and contiguity.
The FTL is also the disk-scale sibling of module 01 ch05's allocator taxonomy and
ch19's jemalloc/slab designs.

## The drive's hidden budget: SLC cache, TBW, read disturb

**The SLC cache.** A TLC cell can be programmed as if it were SLC — 1 bit, 2 of its
8 levels — which is fast (wide voltage window, few pulse rounds) and low-wear.
Consumer drives reserve 12-100 GB (typically 20-40 GB on a 1 TB TLC model) as a
pseudo-SLC write cache: the first gigabytes land at 1.5-3.5 GB/s. When the cache
fills, the controller must fold it — read each SLC page and rewrite it as TLC —
while admitting new writes, and sustained throughput falls to the native TLC rate:
roughly 300-900 MB/s on 1-2 TB consumer TLC, 100-160 MB/s on QLC models — some QLC
drives, once full, write slower than a 7,200 rpm HDD reads (150-250 MB/s). The
cache is also dynamically carved from free NAND, so a 90%-full drive has almost
none left: burst and sustained rates collapse together. This is the benchmark trap
— any write test smaller than the cache measures the cache: a 10 GB synthetic run
on a 1 TB TLC drive reports 2.5 GB/s of a drive that sustains ~0.5 GB/s.

**Endurance arithmetic.** The NAND write budget is capacity x P/E cycles; what the
host gets to write is that divided by write amplification:

```text
TBW = capacity x P/E cycles / WAF
    = 1 TB x 1,500 (TLC) / 2.5 (WAF with TRIM)
    = 600 TB of host writes
```

That is exactly how a 1 TB consumer TLC drive gets its 600 TBW rating — 600
full-drive overwrites — and the derivation shows the levers: QLC's 300-1,000 P/E
cycles per cell is why 1 TB QLC drives are rated 150-200 TBW, and enterprise drives
buy spare area and spec 1 DWPD (one drive write per day for 5 years = 1,825 TBW on
1 TB). In the other direction: at 20 GB/day of host writes, 600 TBW is 30,000 days —
consumer endurance rarely kills consumer drives. It kills write caches.

**Read disturb.** Reading a page requires biasing the *other* cells in its string
with a pass voltage, and each read injects a few stray electrons into their floating
gates. One read is noise; 100,000-1,000,000 reads of the same page (vendor-spec
territory, honestly ranging by NAND generation) shift neighbors' thresholds enough
to misread. Reads are therefore not free. The firmware keeps per-block read
counters, and when one nears the threshold the **scrubber** relocates the whole
block — reads it, rewrites it elsewhere, erases the original — before errors
surface (the LDPC/BCH ECC layer absorbs small shifts meanwhile). Consequences: a
read-mostly dataset still consumes P/E cycles, slowly, and worn cells leak faster,
so cold archives on flash eventually need rewriting. The drive does this
invisibly — which is why you should not assume it.

## NVMe: deep queues because the device is parallel

The last first principle: a flash controller is a parallel machine. A modern
controller drives 8-16 independent NAND channels — each channel 1-4 chips, each
chip 1-2 dies, each die 2-4 commandable planes — hundreds of units that each
service a ~50-100 µs page read. One unit delivers ~13,000 reads/s; hundreds of
units want on the order of a million commands per second to stay busy.

AHCI, the SATA protocol of 2004, offers 1 queue of 32 commands (Native Command
Queuing) — perfectly matched to one mechanical head, where 32 pending requests is
plenty to reorder into an elevator sweep, and perfectly wrong for flash: 32 commands
can occupy at most 32 of several hundred parallel units. SATA's 6 Gbps link
(~600 MB/s usable) is a second ceiling flash passed around 2010. NVMe's design
answer is queue depth and count: up to 64 KiB queues, each holding up to 64 KiB
commands (the NVMe 2.0-era headline; earlier revisions already allowed thousands),
each submission/completion pair pinnable to one CPU core, lock-free, with per-queue
MSI-X interrupt routing. **The queue is deep because the device is parallel.** The
transport ladder underneath is PCIe: Gen3 x4 ≈ 3.5 GB/s, Gen4 x4 ≈ 7 GB/s,
Gen5 x4 ≈ 14 GB/s (after encoding and protocol overhead), versus SATA's 0.6 GB/s.

**Latency and the tower.** A queue-depth-1 read on NVMe completes in ~20-100 µs
(60-90 µs typical for consumer Gen3/Gen4: PCIe round trips plus controller plus
NAND). An HDD access is ~10 ms. The gap is 100-500x. Module 01 ch02's tower of
latencies gets two new bottom rows:

```text
   operation            typical time     step from the row above
   ------------------   --------------   -------------------------
   L1 / registers       ~1 ns            (module 01 ch02's tower)
   DRAM access          ~100 ns          100x the cache row
   ---- module 01's tower ended here; storage extends it: ----
   NVMe 4 KiB read      ~20-100 µs       200-1,000x DRAM
   HDD 4 KiB read       ~8-12 ms         100-500x NVMe = ~100,000x DRAM
```

Everything in chapter 02 — the page cache, readahead, "free memory is wasted
memory" — exists to turn those bottom rows into top-row hits.

**Queue depth, priced.** At queue depth 1, throughput is 1/latency: 75 µs per read
means 13,000 IOPS means 51 MB/s of 4 KiB reads — from a device marketed at a
million IOPS. At depth 32 there are 32 commands in flight, enough to occupy many
channels and planes at once: 150,000-400,000 IOPS on a consumer Gen3/Gen4 drive —
a 10-30x difference (10x is the conservative floor; measure yours). This is fio's
`iodepth` parameter, and it is why synchronous workloads cannot have it: an
application whose logic is issue-read, wait, respond, issue-next is queue depth 1
*by construction* — each I/O must complete before the program can issue the next.
Databases escape with many concurrent threads; Linux 5.1+ added io_uring so one
thread can keep thousands of submissions in flight.

## What insiders know

- **63 is the misalignment number**: LBA 63 = byte 32,256 = 3,584 bytes into a 4 KiB
  physical sector, so on 512e every OS block splits across two physical sectors and
  every write is an RMW. Cloned old images still carry it; the 1-MiB (sector 2048)
  default fixed it in ~2010.
- **NVMe SMART "Data Units" are 512,000 bytes each** — decimal, not 512 x 1024:
  "Data Units Written: 28,341,013" is 14.5 TB, not 14.7 TB.
- **Percentage Used is a vendor estimate, not a gauge.** It folds in the drive's own
  measured write amplification, and the formula is unpublished; drives routinely
  outlive 100%. Treat it as the trend line, Data Units Written as the host-side fact.
- **The SLC cache shrinks as the drive fills** (it is carved from free NAND): a 90%
  full 1 TB TLC drive has lost roughly an order of magnitude of cache, so burst rate
  and sustained rate fall *together* — one symptom, two mechanisms.
- **QD1 IOPS = 1000/latency_ms — the spec-sheet lie detector.** A drive advertising
  1,000,000 IOPS (at QD128) delivers 10-20k at QD1; the synchronous application
  feels only the second number.
- **Any write benchmark under ~20 GB measures the SLC cache** (12-100 GB on consumer
  TLC): steady-state testing means fill the drive first, then time a run larger than
  any cache.
- **TRIM is batched weekly by default** (`fstrim.timer`); per-delete `discard` costs
  IOPS on delete-heavy workloads, which is why the batch default won.

## See it yourself

Any Linux box with `smartmontools`, `nvme-cli`, and `fio` (sudo for all four).

**1. The drive's confession — decode the wear lines one by one:**

```console
$ sudo smartctl -a /dev/nvme0
...
Percentage Used: 3%
Data Units Written: 28,341,013 [14.5 TB]
Data Units Read:    81,004,667 [41.4 TB]
Available Spare:     100%
```

Data Units Written x 512,000 B = host bytes written (28,341,013 x 512,000 ≈ 14.5 TB).
Cross-check Percentage Used against your TBW math: 14.5 TB of a 600 TBW rating is
2.4%; the drive says 3% — the difference is the controller folding its internal WAF
into the estimate. On SATA: attribute 241 Total_LBAs_Written (x 512) and the vendor
wearout attribute.

**2. The 4 KiB alignment check:**

```console
$ lsblk -o NAME,PHY-SEC,LOG-SEC,ALIGNMENT
NAME    PHY-SEC LOG-SEC ALIGNMENT
sda         4096     512         0
nvme0n1p1   4096     512         0
```

PHY-SEC is the physical sector, LOG-SEC what the OS sees: 4096/512 = 512e (the RMW
tax on misalignment), 4096/4096 = 4Kn, 512/512 = a pre-2011 platter or an NVMe
drive formatted for 512 B LBAs. ALIGNMENT 0 means each partition starts on a
physical-sector boundary — anything nonzero is the tax. `nvme list` adds the
namespace format ("512 B / 512 B" = current/preferred LBA size).

**3. Queue depth, measured — the lab's payoff (use a spare or unmounted partition;
never write-test anything you like):**

```console
$ sudo fio --name=q1 --filename=/dev/nvme0n1p2 --direct=1 \
    --rw=randread --bs=4k --ioengine=libaio --iodepth=1 \
    --runtime=30 --time_based --group_reporting
  read: IOPS=16.5k, BW=64.4MiB/s (67.5MB/s)
$ sudo fio --name=q32 --filename=/dev/nvme0n1p2 --direct=1 \
    --rw=randread --bs=4k --ioengine=libaio --iodepth=32 \
    --runtime=30 --time_based --group_reporting
  read: IOPS=311k, BW=1215MiB/s (1274MB/s)
```

Expect QD1 of 10-20k IOPS and QD32 of 150,000-400,000 on a consumer Gen3/Gen4
drive — a 10-30x ratio from one flag. `--direct=1` is mandatory: without it you
measure the page cache (chapter 02 shows why). On an HDD, both depths give ~60-100
IOPS — a queue cannot fix mechanics.

**4. The old world, for calibration:**

```console
$ sudo hdparm -t /dev/sda
Timing buffered disk reads: 314 MB in  3.01 seconds = 104.32 MB/sec
```

Expect 80-140 MB/s on a 5,400 rpm 2.5-inch drive, 150-250 MB/s on a 7,200 rpm
3.5-inch — the sequential rate, held against the ~62 IOPS random ceiling from
section 1: one device, 500-1,000x apart, felt.

## How to use this knowledge

- **Capacity planning arithmetic**: a synchronous random-4-KiB workload on HDD caps
  at 60-80 IOPS per spindle (100-200 with a deep queue). "The database needs 10,000
  IOPS" means ~150 spindles of 7,200 rpm disk, ~60 of 15k, or one NVMe drive at
  QD32 — one derivation prices the decision.
- **Qualifying SSDs honestly**: derive TBW as capacity x P/E / WAF, then divide by
  your daily host-write rate (the Data Units Written delta is the input metric). An
  800 GB/day cache on a 200 TBW QLC drive is a 250-day component, not a 5-year one.
- **Benchmark reading**: dismiss any write number produced by fewer than 20 GB of
  writes or on a drive that is not near full. The 10x SLC-versus-TLC gap is the gap
  between review scores and backup windows.
- **Explaining and fixing the 90%-full slowdown**: keep 10-20% of the drive
  unpartitioned (overprovisioning) or buy enterprise spare — both give the GC mostly
  garbage blocks to erase, cutting WAF from 5-10 toward 1.5-3.
- **Monitoring that catches death early**: alert on Percentage Used crossing 80-90%
  and Available Spare dropping, not on SMART's overall-health word alone — the wear
  lines move weeks before the verdict does.

## Connections

- **Module 14 ch02** — the page cache exists because of this chapter's tower: hits
  ~100 ns, misses 20 µs-10 ms; fsync's cost is a flush into this FTL.
- **Module 14 ch03** — ext4's journal exists because sector-atomic writes can tear
  mid-crash; its 5 s commit interval is sized against these latencies.
- **Module 14 ch04** — Btrfs/ZFS/APFS replay the FTL's log-structure and
  reclaim-by-copy at file-system scale; ZFS's "don't fill past 90%" is this WAF
  curve in a different costume.
- **Module 14 ch05** — the end-to-end path ends in an NVMe queue pair and an FTL GC
  decision; the scheduler choice (none for NVMe) follows from the device reordering
  better than the host.
- **Module 01 ch02** — the latency tower: NVMe (~50 µs) and HDD (~10 ms) are the new
  bottom rows, 500x and ~100,000x below DRAM.
- **Module 01 ch05, ch19** — the allocator taxonomy: the FTL is a log-structured
  allocator with a GC, disk-scale sibling of jemalloc's runs and slabs.
- **Module 01 ch14** — the cycle detector: the FTL's GC is the same family —
  reclaim by identifying live data and copying it.
- **Module 11 ch01, ch04** — fsync, torn writes, and the WAL: the journal is the
  file system's log exactly as the FTL's write block is a mini-log.
- **Module 11's LSM material** — compaction is the FTL's GC wearing a database hat:
  three systems, one idea, now named on sight.
- **Module 15 ch04 (queued)** — overlayfs copy-up is another write-amplification
  bill; this chapter's arithmetic prices it in advance.

## Quiz

1. An SSD's firmware must keep a logical-to-physical page map. Why can it not simply
   write each logical page in place, updating the same physical page forever? Derive
   the constraint that forces the map to exist.
2. A 15,000 rpm SAS drive has an average seek of 3.5 ms and 200 MB/s outer-track
   transfer. Compute the queue-depth-1 random 4 KiB IOPS ceiling, showing all three
   latency terms.
3. A 512e drive (4 KiB physical, 512 B logical) has one partition starting at LBA
   63, hosting a file system with 4 KiB blocks. How many physical sectors does one
   file-system block read touch, and what does writing that block cost the drive in
   reads and writes?
4. A TLC drive has 16 KiB pages, 64 pages per block, is 100% full with every page
   valid, has never seen TRIM, and holds no spare blocks. The host overwrites one
   16 KiB logical page. Count the flash page-writes the FTL performs and state the
   write amplification factor.
5. A new 1 TB SSD benchmarks at 2.4 GB/s writes with a 10 GB test file. The first
   full backup (400 GB) runs at 400 MB/s, and its last 100 GB runs slower than the
   middle. Diagnose all three numbers.
6. fio reports 310,000 IOPS for 4 KiB random reads at `iodepth=32` on the production
   NVMe drive. The production application — one thread: issue read, wait, respond,
   then issue the next — measures about 13,000 IOPS. Nothing is broken. Explain the
   gap and name one application-level fix.
7. A log-cache appliance writes 800 GB/day of host traffic to a 1 TB QLC drive rated
   200 TBW. How many days until the rating is exhausted, what should monitoring
   watch instead of the calendar, and why might reality land earlier than the
   arithmetic?
8. Name the three systems — one from this chapter, one from module 11, one from
   module 01 ch14 — that share the design "identify live data, copy it to fresh
   space, free the old region wholesale." Then place NVMe (~50 µs) and HDD (~10 ms)
   into module 01 ch02's latency tower, with the multiplier from DRAM's ~100 ns for
   each.

<details><summary>Answers</summary>

1. **In-place writes are physically impossible.** Programming only drives bits
   1-to-0; restoring a 1 requires an erase, and erase granularity is the whole
   64-256-page block. Overwriting page 25 in place would erase the block, destroying
   its 63-255 valid neighbors, then rewrite all of them — WAF equal to
   pages-per-block on every overwrite, plus concentrated wear. So the new version
   must land in a *fresh* page, and something must remember which physical page now
   answers for a given logical address. That remembering is the map: a consequence
   of erase-before-write, not a feature.

2. Rotation at 15,000 rpm = 60 s / 15,000 = 4 ms, so average rotational latency is
   2 ms; seek 3.5 ms; transfer 4 KiB / 200 MB/s = 0.02 ms. Total 3.5 + 2 + 0.02 =
   5.52 ms, so the ceiling is 1000 / 5.52 = **181 IOPS** — versus 62 for the
   7,200 rpm drive: 3x the IOPS for 2x the spin rate and a shorter seek, the entire
   pre-flash enterprise ladder.

3. LBA 63 is byte 63 x 512 = 32,256; 32,256 mod 4,096 = 3,584, so the partition
   begins 3,584 bytes into physical sector 7. Every 4 KiB file-system block
   straddles two physical sectors: one block read touches **2 physical sectors**,
   and one block write costs **2 reads + 2 writes** (read both physical sectors,
   merge the new 4 KiB, write both back) — an RMW with device-level write
   amplification of 2x on every write, indefinitely. Sector-2048 (1 MiB) alignment
   removes it because 2048 x 512 = 1,048,576 is divisible by 4,096.

4. The FTL relocates the 63 still-valid pages (63 page-writes), plus writes the new
   version of the overwritten page (1 page-write), then erases the old block:
   **64 flash page-writes** for 1 host page-write, so **WAF = 64**. In bytes: the
   host wrote 16 KiB; the NAND absorbed 64 x 16 KiB = 1 MiB. This is the corner the
   GC spends its existence steering away from — and what a 90%-full, TRIM-less
   drive drifts toward.

5. The 10 GB benchmark fit inside the pseudo-SLC cache (12-100 GB on a 1 TB TLC
   drive): writes land in fast 1-bit-per-cell mode, so 2.4 GB/s is the cache, not
   the TLC. The 400 GB backup exhausted it: the controller must fold SLC pages to
   TLC (read + rewrite) while admitting new writes, dropping to the sustained TLC
   rate — 300-900 MB/s for this class, 400 measured. The slow tail compounds two
   mechanisms: the cache is dynamically carved from free NAND and has nearly
   vanished as the drive filled, and the GC on a nearly full drive pays WAF 5-10 on
   writes behind the backup. Occupancy degrades burst rate, sustained rate, and GC
   cost at once.

6. The application is synchronous by construction: each read must complete before
   the program can issue the next, so it runs at queue depth 1 regardless of device.
   At QD1, throughput = 1/latency: 1/75 µs ≈ 13,300 IOPS — exactly what it measures.
   fio's 310,000 IOPS keeps 32 commands in flight, enough to occupy many of the
   controller's 8-16 channels and their planes. The gap is queue depth, not a fault.
   Fixes: multiple threads or connections, or io_uring (Linux 5.1+) to batch
   thousands of submissions from one thread — the escape databases use.

7. 200 TBW / 0.8 TB per day = **250 days**, about 8 months. Watch `smartctl`'s
   **Percentage Used** (the drive's own estimate, including its measured write
   amplification) and **Available Spare**, plus the daily Data Units Written delta.
   Reality can land earlier because TBW assumes the vendor's WAF: a cache workload
   on a filling drive with poor TRIM behavior can run WAF 5-10, and each unit of
   amplification drains the NAND budget (capacity x P/E cycles) faster than the
   host-write calendar predicts. Percentage Used sees that; the calendar does not.

8. The three systems: the **FTL's garbage collector** (this chapter — copies valid
   pages out of a block so the block can be erased), **LSM-tree compaction**
   (module 11 — merges live keys into new runs so old runs are dropped wholesale),
   and the **tracing/mark-sweep collector with cycle detection** (module 01 ch14 —
   marks live objects, reclaims dead regions wholesale; compacting collectors copy
   the live ones). In the tower: DRAM ~100 ns, then NVMe ~50 µs = **500x** DRAM,
   then HDD ~10 ms = **200x** NVMe, which is ~**100,000x** DRAM. Three reclaims,
   one idea; three devices, five orders of magnitude.

</details>
