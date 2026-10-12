# Chapter 06 — macOS & the grand comparison

> **The 30-second version.** macOS runs iOS's XNU kernel with desktop
> physics: a disk exists, so pressure walks a longer ladder
> before anyone dies — drop clean file pages, drop *volatile purgeable*
> pages, compress cold dirty pages, write compressed pages to on-demand
> swapfiles, and only then kill the largest-footprint process. The Mach vm
> map, the purgeable contract, and the dyld shared cache are module 06's
> machinery reused at desktop scale. The new thing is unified memory on
> Apple Silicon: CPU and GPU share one LPDDR pool and one bandwidth
> number (~68 GB/s on an M1), so the copy tax vanishes but a contention
> rule appears — and the "VRAM is full" failure class is replaced by
> footprint and wired limits. It ends with the three-kernel comparison
> table.

## 1. The Mach vm map, desktop edition

Module 06 ch01 derived XNU's picture; nothing here re-derives it: a Mach
**task** (one vm map, one port space, N threads) wearing a BSD **proc**;
the vm map is a tree of **vm_map_entry** policy records; the **pmap** —
module 01 ch03's page tables — is a cache of the map's decisions, which is
why `vmmap` can print the map while page tables stay invisible. The
desktop deltas:

- **Page size.** 16,384 B on Apple Silicon (native), 4,096 B on Intel
  Macs and in Rosetta (module 06 ch01) — check the slice before page
  math.
- **The pager mix.** Each VM object names a pager: the **vnode pager**
  (file-backed, re-faultable), the **compressor pager** (cold dirty pages
  compressed in RAM), and swap for what the compressor gives up. iOS ships
  the first two and forbids the third (module 06 ch02's flash-wear and
  joules argument); a desktop is wall-powered with a bigger SSD budget, so
  macOS ships all three.
- **fork() exists.** The iOS sandbox denies it to apps; macOS allows it —
  module 01 ch03's COW shadow objects are live on the desktop.

## 2. Purgeable memory: a contract, not a state

Module 01 ch03's page ledger: every page is file-backed (re-creatable) or
anonymous (RAM-only). A decoded thumbnail is anonymous
*and* re-creatable — dirty by content, clean by promise. No ledger column
covers that, so the kernel adds one: mark a range **purgeable** —
"discard this instead of compressing or swapping it." `NSPurgeableData`
wraps the Mach trap `mach_vm_purgable_control` on a `vm_allocate`'d range
with `VM_FLAGS_PURGABLE`. Three access states:

```text
  NONVOLATILE --mark volatile--> VOLATILE --kernel pressure--> EMPTY
  charged to                     zero footprint charge;      object discarded;
  phys_footprint                 ages through 8 groups       next fault delivers
  (purgeable_nonvolatile)        (oldest dropped first)      fresh ZERO pages
```

The API semantics:

- **Volatile** pages are reclaim candidates *now*: no signal, no swap-out,
  no second chance — the bottom rung of the volatility ladder, the
  most-volatile purgeable tier (the L4 tier in Apple's docs).
- **Empty** is the state *after* a purge. Accessing an emptied range
  neither crashes nor restores data: faults deliver zero-filled pages —
  500 MB of cache becomes 500 MB of zeros with no warning.
- **Nonvolatile** is the locked state: charged to phys_footprint,
  protected like ordinary dirty memory. The transitions carry the
  loss-detection contract — re-marking tells you whether the kernel purged
  meanwhile.

```objc
// Objective-C, macOS 13: the contract in five lines
NSPurgeableData *thumbs = [NSPurgeableData dataWithLength:1 << 20]; // 1 MiB
fill(thumbs.mutableBytes);          // contents valid now (nonvolatile)
[thumbs endContentAccess];          // VOLATILE: free footprint, at risk
// ... hours later, maybe after pressure events ...
if (![thumbs beginContentAccess])   // NO = kernel discarded it (EMPTY)
    refill(thumbs.mutableBytes);   // must regenerate before reading
```

`beginContentAccess` returning `NO` is the *only* notification you get.
XNU keeps volatile pages in 8 age groups (`NUM_VOLATILE_GROUPS = 8`,
`osfmk/vm/vm_purgeable.c`), oldest dropped first (module 06 ch02): purge
order roughly LRU, reclaim cost zero.

## 3. The pressure ladder: wired, compress, swap, kill

The ladder, ordered by what each valve costs:

1. **Drop clean file pages** — free; the next access re-faults from file.
2. **Drop volatile purgeable pages** — free; the contract pre-authorized
   the loss.
3. **Compress cold dirty pages** — CPU for RAM: 16 KB → ~4 KB frees 12 KB
   for microseconds.
4. **Swap compressed pages to disk** — I/O: milliseconds on the fault-back
   path.
5. **Kill** — memorystatus picks a victim: the "out of application
   memory" dialog, then a SIGKILL to the largest-footprint process. Same
   memorystatus code as iOS Jetsam (module 06 ch02) with a longer ladder
   in front.

**Wired memory is absent from the ladder by definition.** Kernel data
structures, IOKit mappings, DMA buffers, page tables — pages whose
physical addresses are baked into something the kernel or a device holds
— can never fault, so they can never be compressed or swapped.
`vm_stat`'s "pages wired down" is the irreducible floor. XNU caps *user*
wiring with two sysctls, `vm.user_wire_limit` (per task) and
`vm.global_user_wire_limit` (system-wide, a fraction of RAM by default):
mlock past the global limit fails with `ENOMEM` even with free memory
showing.

**The swap machinery.** Through macOS 10.14 a user-space `dynamic_pager`
daemon owned the swapfiles; the work moved into the kernel across
10.15–11, and modern macOS has no `dynamic_pager` process. Swapfiles
materialize on demand under `/System/Volumes/VM/` (`/private/var/vm/`
pre-Catalina): `swapfile0` in the 1 GB class on macOS 13 Apple Silicon
(older releases started at 64 MB and doubled), then `swapfile1`, … while
pressure persists, each ≥ the last. No swap
partition — swap costs disk only while it exists, and files are deleted
as pressure fades. A nearly-full disk blocks swapfile growth: one way a
Mac runs "out of application memory" with RAM to spare.

**phys_footprint on the desktop.** The ledger is unchanged from module 06
ch02: internal + internal_compressed + purgeable_nonvolatile + iokit
mapped + page tables. On iOS it decides every Jetsam kill; on macOS no
per-app cap exists — it decides only the last-resort victim — but it is
still the honest number: RSS double-counts the shared cache's resident
pages in *every* process that links it.

## 4. The shared cache at desktop scale

Module 06 ch04 derived why the cache exists (100+ system dylibs per app;
per-launch resolution is CPU, per-process fixed-up data pages is RAM; link
once at OS-build time, map copy-on-write) and how (chained fixups, launch
closures). Reuse it; the desktop deltas:

- **Apps link the cache *and* load their own dylibs.** An iOS app's every
  dependency is in the cache; a macOS app additionally has embedded
  `.framework` bundles, plugins, and homebrew libraries — dyld does real
  per-launch work for those: map, verify signature, process fixups,
  outside the cache's precomputed plan.
- **On Apple Silicon (macOS 11+) all code must be signed**, ad-hoc at
  minimum — unlike Intel Macs.
- **Two slices can be resident.** The cache is per-architecture: Rosetta
  processes map the x86_64 slice while native processes hold arm64e. On
  macOS 11+ the cache lives in the cryptex:
  `/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/`.

## 5. Unified memory: one number, one pipe

The discrete-GPU arrangement (deep dive in module 12 ch04): CPU RAM here,
GPU VRAM there, joined by PCIe (~32 GB/s for 4.0 x16); every upload and
readback is a *copy*, and VRAM exhaustion is its own failure class — the
CUDA "out of memory" error while RAM remains. Apple Silicon removes the
split — a two-line diff:

```text
DISCRETE                                UNIFIED (Apple Silicon, M-series)
+------+   PCIe 4.0 x16 ~32 GB/s       +------+
| CPU  |<---------->+-------+           | CPU  +---+
+------+  copies    |VRAM 8-|           +------+   |    one fabric
  DDR 64-100+ GB/s  |  24 GB|           | GPU  +---+-------------> LPDDR pool
  (CPU only)        +-------+           +------+   |              ~68.2 GB/s (M1)
                                     | NE    +---+   CPU+GPU+NE CONTEND
                                     +------+         for the SAME bandwidth
```

Consequences:

- **The copy tax vanishes.** CPU and GPU address the same memory; a Metal
  buffer or `IOSurface` serves both with no staging upload.
- **Total bandwidth is shared — the contention rule.** The M1's LPDDR4X
  gives ~68.2 GB/s (128-bit at 4266 MT/s; Apple spec sheet, 2020) and
  everything draws from it: CPU, GPU, Neural Engine, display. A GPU
  streaming 55 GB/s leaves the CPU 13 GB/s — memory *latency* degrades
  though memory *size* is fine. Later chips raise the number (M2 ~100,
  M1 Max ~400, M1 Ultra ~800, M4 base ~120 GB/s; launch specs), but the
  rule is invariant: budget *bandwidth* like a second capacity.
- **Memory size is one number.** No "16 GB RAM + 8 GB VRAM" split to
  misallocate; the GPU borrows what the CPU doesn't use.
- **The VRAM-OOM class disappears — a different OOM takes over.** No
  allocation fails because "video memory" is full, since there is none;
  instead the footprint limit, the wired limit, and the memorystatus kill
  — section 3's ladder. Failure moves from "allocation
  error" to "SIGKILL" (module 12 ch04: CUDA vs Metal).

## 6. The grand comparison

The payoff — same physics (module 01 ch03), three dialects:

```text
+--------------+---------------------------+----------------------------+------------------------------+
|              | Windows (NT)              | Linux                      | macOS (XNU + dyld)           |
+--------------+---------------------------+----------------------------+------------------------------+
| process      | object-manager objects +  | task_struct; a thread is a | Mach task (vm map + ports +  |
| model        | handle tables; job        | task sharing CLONE_VM;     | threads) in a BSD proc (mod  |
|              | objects (ch01)            | namespaces + cgroups       | 06 ch01)                     |
+--------------+---------------------------+----------------------------+------------------------------+
| handle / fd  | handle: index into        | fd: pointer to open-file   | BSD fds for files; Mach port |
|              | process-local table of    | description; inherited by  | rights for IPC; ports never  |
|              | kernel objects; not       | fork unless FD_CLOEXEC     | inherited across fork (ch01) |
|              | inherited by default      | (ch01)                     |                              |
+--------------+---------------------------+----------------------------+------------------------------+
| loader       | kernel + ntdll: PE,       | ld.so: ELF, PT_INTERP,     | dyld: Mach-O, shared cache,  |
|              | known-DLLs, LoaderLock    | vDSO, LD_PRELOAD (ch04)    | closures; plus per-app       |
|              | (ch02)                    |                            | dylibs (sec 4)               |
+--------------+---------------------------+----------------------------+------------------------------+
| default heap | NT heap: 1 MB segments,   | glibc malloc: brk + mmap,  | libmalloc zones:             |
|              | encoded headers, LFH      | tcache + arenas (mod 01    | nano/tiny/small/large        |
|              | buckets (ch03)            | ch19)                      | magazines over vm_allocate   |
+--------------+---------------------------+----------------------------+------------------------------+
| allocator    | LFH front end; Segment    | ptmalloc default;          | magazine allocator; nano     |
|              | Heap opt-in (ch03)        | mimalloc/jemalloc opt-in   | allocator <= 255 B on Apple  |
|              |                           |                            | Silicon                      |
+--------------+---------------------------+----------------------------+------------------------------+
| OOM policy   | no killer: commit limit   | OOM killer: badness score  | memorystatus kill when swap  |
|              | fails the malloc          | -> SIGKILL at alloc        | can't grow; largest          |
|              | (ERROR_COMMITMENT_LIMIT,  | failure; cgroup variant    | footprint dies; dialog first |
|              | ch01)                     | (ch05)                     | (sec 3)                      |
+--------------+---------------------------+----------------------------+------------------------------+
| swap         | pagefile.sys backs        | swap partition/file +      | on-demand swapfiles under    |
|              | commit; growable,         | zram; swappiness (ch05)    | /System/Volumes/VM; never    |
|              | disableable               |                            | preallocated; compressor     |
|              |                           |                            | first; deleted when pressure |
|              |                           |                            | eases                        |
+--------------+---------------------------+----------------------------+------------------------------+
| signatures   | Authenticode optional for | none at exec by default;   | all arm64 code must be       |
|              | user code, required for   | signed kernel modules;     | signed (ad-hoc counts);      |
|              | drivers; VBS/HVCI (mod 02 | SELinux + KASLR (mod 02    | notarized if downloaded; SIP |
|              | ch06)                     | ch06)                      | + PAC (mod 02 ch06)          |
+--------------+---------------------------+----------------------------+------------------------------+
```

**Which OS is which mitigation** (module 02 ch06's ladder): Windows bets
on the hypervisor — VBS/HVCI keep code integrity above a compromised
kernel; Linux on policy plus randomness — SELinux confines, KASLR hides;
macOS on hardware binding — SIP makes system files immutable even to
root, and PAC (arm64e) puts an authentication code on every function
pointer, so code-reuse needs a leak *and* a forgery. Same ladder,
different rungs.

## What insiders know

- **Purgeable tiers by the numbers**: 8 age groups (`NUM_VOLATILE_GROUPS
  = 8`, `osfmk/vm/vm_purgeable.c`), oldest dropped first; volatile costs
  zero footprint, `purgeable_nonvolatile` is fully charged; post-purge
  reads fault *zero* pages — silence, not signal.
- **The wired limits are sysctls**: `vm.user_wire_limit` (per task) and
  `vm.global_user_wire_limit` (system-wide, a fraction of RAM by default).
  mlock of 15 GB on a 16 GB Mac fails with ENOMEM despite free RAM — check
  the sysctl first.
- **The shared-bandwidth contention rule**: CPU, GPU, and Neural Engine
  draw from one number (M1 ~68.2, M1 Max ~400, M1 Ultra ~800 GB/s; launch
  specs) — a GPU at 90% of the budget visibly degrades CPU memory
  latency. Budget bandwidth as a second capacity.
- **Swapfile behavior**: no `dynamic_pager` on modern macOS (gone since
  10.15–11); swapfiles appear under `/System/Volumes/VM/` only under
  pressure, each ≥ the last, *deleted* when pressure fades — a full data
  volume blocks growth and produces the "out of application memory"
  dialog with RAM free.
- **phys_footprint exists on the desktop**: `/usr/bin/footprint` prints
  it per process, but with no per-app cap it decides only the
  last-resort victim; RSS overcounts by gigabytes — every process includes
  the shared cache's resident pages.

## See it yourself

This sandbox has no Mac: the fragments below are documented shapes for
macOS 13 (Ventura), Apple Silicon, 16 GB — exact numbers are
illustrative. Linux equivalents from chapter 05 follow each lab.

**1. Your address space, region by region** (the vm map of section 1):

```text
$ vmmap $$ | head -30        # $$ = the shell's pid
__TEXT            0000000104a6c000-... [  96K] r-x ...
__DATA            0000000104a84000-... [  16K] rw- ...
====> shared cache <====
__TEXT            0000000180000000-... [~404M] r-x ... dyld_shared_cache_arm64e
__DATA            ...
MALLOC            0000000104c00000-... [  2M] rw- ... DefaultMallocZone
VM_ALLOCATE       0000000106c40000-... [  4K] rw- ... (large malloc tier)
```

The shared-cache rows dwarf the binary's own sections; `MALLOC*`
regions are libmalloc's zones; there is no `[heap]` line — an ELF-ism.
Linux contrast: `/proc/self/maps` labels file/vdso/heap/stack (ch04's lab).

**2. RSS versus footprint:**

```text
$ footprint $$ | head
    Phys footprint:        118.3 MB
    Rss:                   402.7 MB        <- shared cache counted again
$ memory_pressure
    The system has 2097152 pages (16 GB) ...
    ... free ... wired ... purgeable ... compressed ...
$ sysctl vm.user_wire_limit vm.global_user_wire_limit vm.compressor_mode
$ ls -lh /System/Volumes/VM/     # swapfiles appear only under pressure
```

The Rss-vs-footprint gap is the shared cache working — the macOS twin of
PSS-vs-RSS from ch05's `smaps_rollup` (Private_Dirty + swap = footprint's
"internal" ledger).

**3. Watch a purge** (needs a Mac): mark an `NSPurgeableData` volatile,
run a memory hog — the next `beginContentAccess` returns NO, the data
zeros. Linux contrast: anonymous pages go to swap or zram (ch05), never
to zero; purgeable is a contract Linux offers only for *file* pages.

## How to use this knowledge

- **Diagnose the "out of application memory" dialog**: compressor + swap
  exhausted, or swapfile growth blocked by a full disk — and why malloc
  never returned NULL: the kill is the failure mode.
- **Port caching code correctly**: a Linux LRU cache or Android's
  `onTrimMemory` pattern maps to `NSPurgeableData` — let the kernel drop
  the oldest of 8 age groups instead of shipping an eviction heuristic
  blind to pressure.
- **Plan GPU workloads by bandwidth, not bytes**: the single ~68-400 GB/s
  number must cover CPU + GPU + Neural Engine; tile the working set
  rather than assuming a separate VRAM pipe.
- **Read a Mac's memory honestly**: `footprint` over RSS; `vm_stat`'s
  wired line as the floor; swapfile count as the pressure gauge — and
  answer "commit vs RSS vs footprint" (ch01's table) with the macOS
  twist: the number only bites at kill time.

## Connections

- **Module 07 ch01** — the commit/RSS/footprint frame and the
  handles-vs-fds-vs-ports comparison.
- **Module 07 ch02-03, ch04-05** — the Windows and Linux pairs: the
  table's left and middle columns, plus the lab's smaps contrast.
- **Module 06 ch02** — jetsam and the compressor derivation, reused:
  the purgeable economy and footprint ledger.
- **Module 06 ch04** — the shared-cache arithmetic and closures, reused;
  this chapter adds only the desktop deltas.
- **Module 01 ch02-03** — the bandwidth hierarchy behind the contention
  rule; the page ledgers and COW every valve manipulates.
- **Module 02 ch06** — the mitigation ladder per platform (VBS/HVCI,
  SELinux/KASLR, SIP/PAC).
- **Module 12 ch04** — unified memory deep dive: GPU MMUs, residency,
  CUDA-vs-Metal failure classes.

## Quiz

1. A photo app keeps a 400 MB cache of decoded thumbnails in
   `NSPurgeableData`, marked volatile. Overnight the Mac hits memory
   pressure twice; in the morning the app calls `beginContentAccess` —
   what does it return in the two possible worlds, and what does the
   memory contain in each?

2. Concept: why can wired memory never be a valve in the pressure ladder,
   while even the compressor and the swapfile are? What physically
   distinguishes the pages?

3. Predict: an M1 MacBook (8 GB, ~68.2 GB/s) runs a Metal shader streaming
   ~55 GB/s while its CPU threads want ~30 GB/s. Footprint stays modest —
   what happens, and why doesn't free RAM fix it?

4. Predict: a 16 GB macOS 14 machine with 5 GB free touches 30 GB of
   dirty pages in a loop. Walk the ladder — which valves engage, what
   appears in `/System/Volumes/VM/`, and what does the user see last?

5. Bug diagnosis: an image tool switched its tile cache to
   `NSPurgeableData`; customers report "random black tiles" after the Mac
   sleeps; the code reads `mutableBytes` after `endContentAccess`. What is
   the bug and the two-line fix?

6. Bug diagnosis: a Linux benchmark port mlocks a 15 GB file mapping on a
   16 GB Mac. `mlock` fails with ENOMEM while Activity Monitor shows 10 GB
   free. Give the reason and the sysctl that proves it.

7. Bug diagnosis (cross-chapter): a cross-platform C++ app vendors its own
   runtime dylib on macOS while linking system frameworks from the shared
   cache. Users see intermittent `dynamic_cast` failures and ODR crashes
   that never occur on Linux. Why does macOS produce *two*
   runtimes where Linux produces one? (Ch04's loader row; module 04 ch02.)

8. Concept (cross-chapter): match each mitigation to its platform and
   mechanism — VBS/HVCI, SELinux, KASLR, SIP, PAC — then explain why PAC
   is the odd one out: what does it change that the other four do not?
   (Module 02 ch06.)

<details>
<summary>Answers</summary>

1. **Two worlds, one return each.** If the kernel never reclaimed: YES,
   data intact. If it did: the object entered EMPTY — the call returns NO
   and the memory contains **zeros** (faults deliver fresh zero pages).
   The return value is the *only* notification. Check NO, regenerate
   before any read.

2. **Wired pages have their physical addresses baked in.** Kernel data
   structures, IOKit/DMA mappings, and page tables are referenced by
   physical address by code or hardware that cannot take a fault — and
   moving a page requires faulting it back elsewhere. Every other
   page is addressed *virtually* through the pmap, a rebuildable cache of
   the vm map (module 06 ch01).

3. **Bandwidth contention, not capacity.** Both draw from the same
   ~68.2 GB/s LPDDR: 55 + 30 = 85 GB/s of demand against ~68 of supply.
   Neither side fails an allocation; both get *slower* — GPU throughput
   sags, CPU memory latency rises as requests queue behind GPU traffic.
   Free RAM is irrelevant: the pipe is the scarce resource (module 12 ch04).

4. **The ladder in order**: clean file pages drop; volatile purgeable
   drops; the compressor takes cold dirty pages; swapfiles appear under
   `/System/Volumes/VM/` and grow — but only ~5 GB is free, so growth
   stalls well before absorbing 30 GB. Compressor and swap
   exhausted → memorystatus SIGKILLs the largest-footprint process and the
   user sees the "out of application memory" dialog. malloc never
   returned NULL: the kill *is* the OOM policy.

5. **Reading volatile memory without locking it.** `endContentAccess`
   left the cache volatile; pressure (sleep is a classic trigger) let the
   kernel discard it to EMPTY. The code read `mutableBytes` directly —
   post-purge faults deliver zeros, which decode as black tiles. Fix:
   gate every access on `beginContentAccess` (regenerate on NO), re-mark
   volatile only when done.

6. **The global user-wire limit.** XNU caps user-wireable pages
   system-wide: `vm.global_user_wire_limit` defaults to a fraction of RAM
   (~14-15 GB here), and mlock past it returns ENOMEM *while free RAM
   exists* — free pages aren't the constraint; the ceiling is.
   `sysctl vm.global_user_wire_limit` prints it.

7. **The cache plus your own copy = two type hierarchies.** On Linux the
   vendored runtime and the system's resolve into one symbol namespace
   (module 07 ch04) — one `typeinfo` per class. On macOS the system runtime
   is pre-linked in the shared cache while the vendored dylib loads
   separately (section 4's delta): two runtimes, each with its own
   `typeinfo` for the "same" class, so `dynamic_cast` and exception
   matching across the boundary fail — an ODR violation the ABI cannot
   see. Don't vendor a runtime the cache provides.

8. **The mapping**: VBS/HVCI — Windows, hypervisor-protected code
   integrity; SELinux — Linux, mandatory access control; KASLR — Linux,
   address-space randomization; SIP — macOS, system-file immutability even
   to root; PAC — macOS arm64e, pointer authentication. **PAC is the odd
   one out**: the other four are software-enforced policy or layout; PAC
   changes the *pointer format itself* — every code pointer carries a
   cryptographic signature keyed to its address, so an attacker needs a
   leak *and* a forgery. It hardens the ABI, not the policy.
</details>
