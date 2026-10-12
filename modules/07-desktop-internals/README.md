# Module 07 — Desktop OS internals: Windows, Linux, macOS

> Three kernels, one problem: turn one physical memory into thousands of private
> ones. Windows (objects and handles), Linux (tasks and cgroups), macOS (Mach
> tasks and zones) each implement the same page-table physics from module 01
> chapter 03 — but with different loaders, different heaps, and different
> failure folklore. This module makes the three concrete, side by side.

**6 chapters · one final exam · prerequisite: module 01 chapters 03 (virtual
memory), 04 (stack), 05 (heap), 19 (allocators); modules 05-06 sharpen the
comparisons; module 04 ch01-02 (formats/loader) is directly reused.**

## How to study it

Chapter 01 frames the comparison (what "process" and "memory accounting" mean in
each kernel); chapters 02-03 are the Windows pair (loader + heap); chapters 04-05
the Linux pair (loader + memory machinery); chapter 06 macOS plus the unified
comparison table. Linux labs run anywhere; Windows labs need a Windows box or VM
(WinDbg); macOS labs run on a Mac.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [Three process models](chapters/01-process-triptych.md) | Handles vs fds vs ports; commit vs RSS vs footprint — the comparison frame. |
| 02 | [Windows: PE & the loader](chapters/02-windows-pe-loader.md) | PE internals, PEB/TEB, relocation, LoaderLock, the DLL order folklore. |
| 03 | [Windows: the NT heap](chapters/03-windows-heap.md) | Front end (LFH) vs back end, encoded headers, bucket sizes, pageheap. |
| 04 | [Linux: ELF & the loader](chapters/04-linux-elf-loader.md) | ld.so, vDSO/vvar, symbol order, dlmopen — what runs before main. |
| 05 | [Linux: the memory machinery](chapters/05-linux-memory.md) | smaps, page cache, THP, slab, overcommit, the OOM killer's arithmetic. |
| 06 | [macOS & the comparison](chapters/06-macos-comparison.md) | Mach VM, purgeable memory, unified memory — and the three-kernel table. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — three process models.** The process as each kernel defines it:
Windows = object-manager objects + handle tables (PID reuse semantics, handle
inheritance, job objects as the cgroup analog); Linux = task_struct (threads are
tasks sharing CLONE_VM — the "thread is a process that shares" inversion) +
namespaces + cgroups (the container story in one paragraph: namespaces fake the
world, cgroups ration it); macOS = Mach task (from module 06 ch01) wearing a BSD
proc. Handles vs file descriptors: the reference-counting and permission
semantics (fd = a pointer to an open-file description; handle = an entry in a
process-local table into kernel objects — the differences that bite: fd
inheritance vs handle inheritance). Memory accounting as the insider
minefield: commit charge (Windows: reserved-but-charged) vs RSS vs PSS vs
phys_footprint (macOS) — one table, and the "which number predicts OOM" answer
per OS. Scheduling compared briefly (Windows 32 priorities + quantum boosts,
Linux CFS→EEVDF, macOS QoS classes) — memory-adjacent only, as the CPU side of
the same resource-isolation story.

**Chapter 02 — Windows: PE & the loader.** PE anatomy with the why: DOS header +
stub (why Microsoft keeps it: backwards compatibility theater — a 64-byte stub
that prints "This program cannot be run in DOS mode"), the PE signature, COFF
header, optional header (the magic 0x20b for PE32+), data directories. RVA→file
offset conversion *done by hand* on a real example (the section-table lookup —
the skill every module 04 RE person needs on Windows). Sections: .text/.rdata/
.data/.rsrc/.reloc; /DYNAMICBASE + .reloc entries (IMAGE_REL_BASED_DIR64) as
ASLR's enabler (connect module 02 ch06). PEB and TEB: where they live (TEB at
gs:0x30 / gs base on x64), what insiders read from them (TEB: stack base/limit
— the source of stack probing/_chkstk; PEB: ProcessParameters, the loader's
in-progress flag BeingDebugged, Ldr module lists). DLL loading order and the
 folklore (known-DLLs, search-order hijacking as the classic attack; SetDefault
DllDirectories). LoaderLock: the D-list initialization serialization — why
doing complex work in DllMain deadlocks or crashes (the insider's rule: DllMain
should be ~four instructions; the horror stories). Wow64 sketch (x86 emulation
on x64: two ntdlls, the syscall translation layer). Lab: dumpbin /headers,
`!peb` in WinDbg, breaking on DllMain.

**Chapter 03 — Windows: the NT heap.** The layered design (front end vs back end
— the same two-tier idea as glibc's tcache + arena in module 01 ch19): Back end
= segments (reserved 1MB granularity, committed on demand), VA blocks,
coalescing and decommit policy. Front end = LFH (Low Fragmentation Heap):
activation heuristics (buckets that see enough allocations), the bucket-size
grid (sizes rounded into ~17 classes... state the granularity: 8→16→32... and
the 512-byte LFH threshold... state the actual bucket ladder on Win10/11),
allocation affinity, why LFH exists (the Windows fragmentation folklore: the
1990s NT heap's coalescing pathologies). Heap entry headers: the *encoded*
metadata (headers XOR'd with a per-heap key — why: module 02 ch04's
metadata-overwrite attacks; Windows got burned and armored up), the busy flag,
segment entry lists. Handle-table-backed allocations vs user heap. Debugging
arsenal: pageheap (gflags +hpa — every allocation gets guard pages and fill
patterns: 0xbaadf00d, 0xdeadbeef-adjacent fill-pattern folklore), !heap -s -a
in WinDbg, UMDH for leak snapshots. Lab: a small program that allocates/frees
in patterns; !heap statistics before/after; pageheap catching a deliberate
overflow.

**Chapter 04 — Linux: ELF & the loader.** ld.so as the userspace loader
(PT_INTERP → exec transfers control there first): mapping PT_LOADs (the
file-offset/page-offset alignment rule — derive why p_offset ≡ p_vaddr mod 4096
must hold), BSS zeroing via anonymous mapping beyond file end. Relocation
processing: R_X86_64_RELATIVE bulk-rebase (the DT_RELCOUNT optimization), then
symbol resolution: order = LD_PRELOAD → main binary → DT_NEEDED breadth-first
→ ld.so.cache (the -Bsymbolic / -Bsymbolic-functions escape that saves lookups
and breaks interposition). The vDSO + vvar pages (kernel-provided userspace
code: clock_gettime without a syscall; the vvar data page + seqlock read
protocol — the insider's "fast time reads are a shared-memory algorithm"),
`vdso` in /proc/self/maps. dlopen/dlsym (RTLD_LOCAL/GLOBAL, unloading and the
refcount... dlclose folklore), dlmopen namespaces (the "two different libcs in
one process" isolation). LD_DEBUG=files,libs as the loader's trace. Static
linking tradeoffs. Lab: LD_DEBUG walkthrough, LD_PRELOAD interposition (reprise
module 04 ch02's malloc counter — now with the mechanism), ldd, /proc/self/
maps labeled with each mapping's origin (file, vdso, heap, stack).

**Chapter 05 — Linux: the memory machinery.** The /proc/self/maps + smaps
anatomy (per-VMA: permissions, file, Size/Rss/Pss/Shared_Clean... — the PSS
vs RSS distinction insiders use for capacity planning; Private_Dirty as the
"real cost of heap" number). The page cache: file pages cached in RAM
(read/write path), dirty writeback (the dirty ratio triggers, the fsync/
fdatasync distinction — what each actually flushes), double-buffering vs
O_DIRECT (the database argument — module 11 connection). THP: 2MB transparent
huge pages, khugepaged collapse, MADV_HUGEPAGE/madvise policy, the TLB-miss
arithmetic (module 01 ch02 payoff), and the latency outliers THP caused (the
Redis folklore). Slab/slub allocator (kernel object caches: kmalloc-8..96,
192, and the power-of-2 sizes — the kmalloc-192 lore: its special size class;
/proc/slabinfo; shrinkers). Overcommit modes (0 heuristic, 1 always, 2 strict
commit+swap accounting — and mlock's interaction). The OOM killer: badness
scoring (the oom_score formula: total_vm... and the modern RSS+swap basis
with oom_score_adj as the human override), the kill choreography (the victim's
own oom-kill notification, SIGKILL, the memory cgroup OOM variant), how to
read the kernel log's OOM report line by line (the insider's table: the
process list with columns explained). zram/zswap as compressed-memory tiers.
Lab: smaps_rollup interpretation, allocating until OOM in a controlled
experiment, /proc/buddyinfo fragmentation reading.

**Chapter 06 — macOS & the grand comparison.** macOS memory: the Mach VM map
(from module 06, now the desktop view), purgeable memory (NSPurgeableData:
"empty" access state — the VolatileMemory tier: pages the system may reclaim
under pressure, the L4-tier API semantics), the dynamic pager & compression
(Mac *does* swap by default on desktops — unlike iOS; swapfile growth
behavior), wired memory (non-pageable: kernel + IOKit mappings; the
footprint accounting), the shared cache at desktop scale (module 06 ch04 —
reuse, don't re-derive; the interesting delta: macOS apps link the cache
*and* load their own dylibs). Unified memory on Apple Silicon: the same
LPDDR behind one fabric for CPU and GPU — derive the consequences (no
copy-GPU-CPU (the discrete-GPU tax vanishes), but total bandwidth is shared
(CPU+GPU contend: the M1's ~68GB/s story), memory "size" = one number, no
VRAM OOM class — a *different* OOM: the system-wide footprint limit + wired
limit; connect module 12). The three-kernel comparison table — the chapter's
payoff: one table comparing process model, handle/fd, loader, default heap,
allocator, OOM policy, swap behavior, signature enforcement across Windows/
Linux/macOS; then "which OS is which mitigation" (module 02 ch06 mapped per
platform: Windows VBS/HVCI vs Linux SELinux/KASLR vs macOS SIP/PAC).

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **Cross-platform memory debugging**: you know *where the numbers live* on each
  OS (smaps / vmmap / !heap) and which number answers which question.
- **Loader fluency**: DLL hijacking, LD_PRELOAD, dyld closures — the same
  mechanism in three dialects.
- **Capacity engineering**: commit vs RSS vs footprint; page cache, THP, and
  OOM arithmetic you can predict rather than react to.
- **The frame for modules 05-06**: mobile OSes are these kernels with policies
  swapped — you'll recognize every part.
