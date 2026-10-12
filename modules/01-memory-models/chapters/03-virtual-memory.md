# Chapter 03 — Virtual memory: the grand illusion

> **The 30-second version.** Every pointer your program uses is a *fiction*. The CPU,
> together with the OS, translates each virtual address to a physical one on every
> access. The illusion buys three things: isolation (processes cannot see each other),
> sparsity (address space is cheap, RAM is not), and laziness (pages appear on demand,
> are shared copy-on-write, and can be evicted to disk). Fork's speed, ASLR's security,
> mmap's tricks, the OOM killer's decisions, and mobile's "the OS is the GC" (Chapter
> 22) are all features of this one indirection.

## The problem: three facts that can't coexist

1. Every process wants to feel like it owns memory starting at a nice fixed address.
2. Processes must not be able to read or write each other's memory.
3. RAM (a few GB) is much smaller than the sum of what running programs *declare* they
   need, and physical memory fragments when programs load and exit.

No flat physical addressing satisfies all three. The fix is one level of indirection —
the oldest trick in systems: **virtual → physical translation per process**. Each
process gets its own *virtual address space* (the fiction), and a per-process mapping
(the page tables) decides which virtual pages currently exist and where they live.

## Pages and the page table

Translation happens in units of **pages** — 4 KB on all mainstream systems (x86-64 and
ARM64 both support 2 MB / 1 GB huge pages too). A page is the OS's unit of currency:
map it, unmap it, protect it, evict it, share it.

The mapping is a *sparse radix tree*. On x86-64 with 4 KB pages it is 4 levels
(PGD → PUD → PMD → PTE); each entry is 8 bytes and covers a fixed span:

```
48-bit virtual address:
| 9 bits | 9 bits | 9 bits | 9 bits | 12 bits |
   PGD      PUD      PMD      PTE    offset-in-page

Level    entry covers          entries per table
PTE      4 KB   (one page)     512
PMD      2 MB                  512
PUD      1 GB                  512
PGD      512 GB                512
```

Each PTE holds the physical frame number plus permission/state bits: present, writable,
user/supervisor, no-execute (NX), accessed, dirty. The **accessed/dirty bits are set by
hardware** — free telemetry the OS's eviction algorithms read (more below).

Walking 4 levels per access would be ruinous, so translations are cached in the **TLB**
(Chapter 02: small — this is why huge pages exist, each TLB entry covering 2 MB instead
of 4 KB). On a TLB miss the MMU walks the tree in hardware. On a PTE that is *not
present*, the CPU raises a **page fault** — and here is where the OS takes over.

## The page fault is a programming model

A page fault is not an error; it is an *event the OS handles however it likes*:

- **First touch of anonymous memory** → map in a fresh zero page (which is why
  `calloc`-ed or BSS memory is zero and why its first *write* is what actually costs).
- **Not in RAM, in swap/page cache** → read it back from disk (a *major* fault, ~the
  SSD/disk row of Chapter 02's table), then resume the instruction.
- **Write to a read-only page that is COW-shared** → copy the page, map it writable
  (below).
- **Genuinely unmapped** → SIGSEGV. A segmentation fault is a page fault with no story
  the OS is willing to tell.

```c
#include <stdio.h>
#include <sys/resource.h>
int main(void) {
    struct rusage ru; getrusage(RUSAGE_SELF, &ru);
    printf("minor faults before: %ld\n", ru.ru_minflt);
    char *p = malloc(64 * 1024 * 1024);      // 64 MB — appears in maps, no RAM yet
    getrusage(RUSAGE_SELF, &ru);
    printf("after malloc: %ld\n", ru.ru_minflt);        // ~unchanged
    for (int i = 0; i < 64 * 1024 * 1024; i += 4096)    // touch one byte per page
        p[i] = 1;
    getrusage(RUSAGE_SELF, &ru);
    printf("after touch: %ld\n", ru.ru_minflt);         // +16384 — one per page
    return 0;
}
```

That is demand paging made visible: **malloc costs nothing; touching costs a fault**.
Every "allocating 32 GB worked but then crashed halfway" bug is this.

## Copy-on-write: the reason fork is fast

`fork()` duplicates a process. Naively that means copying all its memory — absurd for
a 1 GB process that will immediately `exec()`. Instead the kernel copies only the page
tables, marking every writable page read-only in *both* processes:

```
parent PTE: frame F, RO + COW      child PTE: frame F, RO + COW
              \                      /
               \                    /
                +-- physical frame F, refcount 2 --+

child writes frame F → page fault (RO!) → kernel sees COW
  → if refcount > 1: copy F to F', map child's PTE to F' RW, refcount(F)--
  → if refcount == 1: just make it writable again (no copy at all)
```

Writes are what force real copying — read-heavy children (forked servers,
`fork()`-parallel computation, and Android's entire app-launch mechanism, Chapter 22)
share almost everything. Two more COW artifacts you use daily: `exec()` is a bulk
*unmap-then-map* (no copying at all), and the Zygote's shared boot image on Android is
a COW relationship that persists for the app's whole life.

## The layout of a Linux process (the map you should be able to draw)

```console
$ cat /proc/self/maps
55d5f4a00000-55d5f4a01000 r--p 00000000  fe:1 ... /usr/bin/cat   ← text (ro, PIE base)
55d5f4a01000-55d5f4a04000 r-xp ...                                     ← code
55d5f4a04000-55d5f4a05000 r--p ...                                     ← rodata
55d5f4a05000-55d5f4a06000 r--p ...                                     ← relocated rodata
55d5f4a06000-55d5f4a07000 rw-p ...                                     ← .data
55d5f4a07000-55d5f4a28000 rw-p 00000000  00:0                          ← .bss (anon)
55d5f7c00000-55d5f7c21000 rw-p 00000000  00:0                          ← heap (brk)
7f1c2c000000-7f1c2c021000 rw-p ...                                     ← malloc arena / mmap
7f1c32000000-7f1c32197000 r--p ... /usr/lib/...libc.so.6               ← libraries
7ffd1b3a0000-7ffd1b3c1000 rw-p 00000000  00:0  [stack]                 ← stack (top)
```

The shape to remember, x86-64 Linux: **text at the bottom (PIE-randomized), then
data/bss, heap growing up (brk), shared libraries and malloc's big mmaps in the middle
(the mmap region), stack at the top growing down.** The gap between 0x55.. and 0x7f..
is the address space deliberately left unmapped — touching it is the canonical
"segmentation fault at address (nil)-ish" crash signature.

**ASLR** (address-space layout randomization) randomizes: the PIE base, the brk offset,
the mmap base, and the stack top (kernel setting `kernel.randomize_va_space`, usually
2). Without ASLR, an attacker's Chapter 21 toolkit needs only *one* leaked pointer to
compute every other address; with it, each process is a fresh shuffle. ASLR is a
page-table feature — which is exactly why a page of memory is also the unit of
exploitation-mitigation.

## Eviction: what happens when RAM runs out

Physical pages enter one of two ledgers: **file-backed** (contents = some file's bytes;
can be dropped or written back to that file) or **anonymous** (no file; contents live
only in RAM — or in swap if the OS has any). The kernel's page cache absorbs all file
I/O: reads are served from RAM when warm; writes are buffered and flushed.

When memory runs short, the kernel reclaims: file-backed pages are simply dropped (or
written back if dirty); anonymous pages go to **swap** — historically a disk partition,
on modern Android **zram** (RAM compressed in place — Chapter 22), on macOS/iOS
compressed (Chapter 22 again). Reclamation is LRU-approximating (active/inactive
lists), guided by the hardware's accessed bits. `swappiness` (0-200, default 60) is
the dial for how eagerly anonymous pages are swapped versus file cache dropped.

And when reclamation fails: the **OOM killer**. It scores every process
(`oom_score`: memory footprint × a boost for privileges) and kills the cheapest
highest-scoring victim — a policy decision that looks arbitrary until you read it as
"the page tables must be defended." Mobile platforms replaced this crude act with
banded, priority-aware killers (Chapter 22's lmkd and Jetsam), but the *principle* is
inherited from exactly this code.

## What insiders know

- **RSS vs VSZ vs PSS**: VSZ is the fiction's size (address space mapped), RSS is
  physical pages currently resident (double-counts shared pages across processes), PSS
  (proportional set size) is RSS with shared pages charged fractionally to each
  sharer — `/proc/pid/smaps` computes it; Android's `dumpsys meminfo` reports it
  (Chapter 22).
- **`madvise` is the programmer's cheat code**: `MADV_SEQUENTIAL` (aggressive
  readahead), `MADV_DONTNEED` (drop pages now — the GC world uses it to hand arenas
  back, Chapter 19), `MADV_HUGEPAGE` (THP), `MADV_WILLNEED` (prefetch a file).
- **Transparent Huge Pages are not free**: THP can *increase* memory (rounding 4 KB
  uses up to 2 MB) and khugepaged's compaction causes latency spikes — the classic
  "database wants THP off / JVM wants it explicit" folklore, both really saying:
  *a page-size change is a performance contract change*.
- **Minor vs major faults** are the cheap/expensive fault split; `getrusage`'s
  `ru_minflt/ru_majflt` are the metric.
- **`mmap` is malloc's escape hatch**: allocations over the mmap threshold (Chapter
  19: 128 KB default) skip the arena entirely — which is why a huge malloc shows up as
  a new maps row with its own protections.
- **TLB shootdowns** are the hidden cost of `mprotect`/munmap on multicores: the
  kernel must interrupt every core that ever cached that translation (IPI storm) —
  why GCs and JVMs batch their remappings.
- **Zero-cost NULL**: page 0 is deliberately unmapped so NULL dereferences *fault* —
  "null pointer" is a page-table trick, and a whole exploit family (Chapter 21's
  NULL-page mappings on old kernels) exists to un-trick it.

## See it yourself

The map, annotated by you:

```console
$ gcc -o demo demo.c && ./demo &      # any small program that sleeps
$ grep -E 'heap|stack' /proc/$!/maps
7f..000000-7f..21000 rw-p ... [heap]
7ff..00000-7ff..21000 rw-p ... [stack]
$ pmap $! | tail -3                   # same data, sized
```

Fork and COW, observed:

```c
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
int main(void) {
    char *big = malloc(1 << 30); memset(big, 1, 1 << 30);   // 1 GB, faulted in
    if (fork() == 0) { big[123] = 2; _exit(0); }            // child writes 1 byte
    wait(NULL);
    // smaps of the parent now: one page went private; 262,143 still shared
    return 0;
}
```

Watch `/proc/<pid>/smaps_rollup` (Rss, Private, Shared) for parent and child before
and after the child's write — one dirty page, gigabytes shared. That is `fork()`'s
whole business model, and (Chapter 22) the Zygote's.

## How to use this knowledge

- **Reading profilers and monitoring**: "virtual memory 40 GB" is not a leak; RSS is
  the physical truth; PSS is the fair bill. Most "memory leak" pages are actually
  page-fault or fragmentation stories (Chapter 19).
- **First-touch costs**: initialize large buffers deliberately (parallel first-touch
  for NUMA; sequential memset so Chapter 02's prefetchers can stream).
- **File I/O**: `mmap` versus read is a virtual-memory decision (page cache, madvise,
  fault behavior), not a syscall-count micro-optimization.
- **Security**: ASLR, NX (PTE bit), guard pages, and NULL-page trapping are all
  virtual-memory weapons — Chapter 21's mitigation ladder is mostly page-table
  configuration.
- **Mobile debugging**: Chapter 22's dirty/clean/compressed accounting and
  "clean pages are free" rules are this chapter wearing iOS clothes.

## Connections

- **Chapter 01** — address width (48-bit canonical split) and why page size matches
  the hierarchy's granularity.
- **Chapter 02** — TLB as a translation cache; huge pages as TLB relief.
- **Chapter 04 / 05** — the stack region and the heap's brk/mmap growth are map
  entries.
- **Chapter 08 / 19** — malloc's mmap threshold and arena release via madvise.
- **Chapter 12 / 22** — GCs and mobile OSes as power users of demand paging, COW,
  compression, and killing.
- **Chapter 21** — ASLR, NX, guard pages: the exploit-resistant configuration of the
  page tables.

## Quiz

1. **Concept.** Name the three incompatible facts that force virtual memory, and say
   which feature of the illusion solves each.
2. **Concept.** Why is a page fault a *programming model* rather than an error?
3. **Predict the number.** A program `malloc`s 256 MB and never touches it. How much
   RSS did it gain (ignoring allocator metadata)? Then it writes one byte at offset 0.
   How many page faults, and how much RSS now?
4. **Predict the output.** Parent faults in 512 MB, forks; the child reads all of it,
   writes 3 pages, exits. How many physical pages hold the data during the child's
   life (approx), and after the child exits?
5. **Bug diagnosis.** A monitoring dashboard flags a Java service for "80 GB virtual
   memory." The RSS is 3 GB and stable. What is the dashboard actually measuring, and
   is anything wrong?
6. **Bug diagnosis.** An app "randomly" gets SIGSEGV writing to a buffer it just
   malloc'ed, under memory pressure, only on a swap-enabled box. What is the sequence?
7. **Bug diagnosis.** After enabling THP, a latency-sensitive service shows periodic
   100 ms spikes. Give the mechanism.
8. **Cross-chapter.** Chapter 22 claims "the OS is the GC" on mobile. Which two
  mechanisms from this chapter are the mobile replacements for swapping?

<details>
<summary>Answers</summary>

1. (a) Fixed-looking addresses per process → each process gets its own virtual space
   with the same layout; (b) isolation → per-process page tables, permission bits
   (PTE user/supervisor, RW, NX) enforced by the MMU; (c) RAM smaller and fragmented
   versus demand → pages are mapped on demand (sparse address space), relocated
   freely (physical frames are just a pool), and evicted under pressure.
2. Because the kernel handles it as an event with a *policy*: zero-fill, swap-in,
   COW-break, file-cache fill — each resumes the faulting instruction as if nothing
   happened. Only the "no story available" case (truly unmapped/no permission)
   escalates to SIGSEGV. Managed runtimes extend the same trick (write barriers,
   card tables — Chapters 12, 20) to intercept writes.
3. Zero RSS (demand paging: malloc just extends brk or plans an mmap; no PTEs marked
   present). After one write: 1 minor page fault and 4 KB of RSS.
4. During the child's life: 512 MB of shared pages + 3 private copies ≈ 512 MB +
   12 KB physical — the shared frames are counted once, not twice (that is PSS's
   point). After exit: 512 MB + 0; the parent's originals were never copied.
5. It is measuring VSZ — the JVM reserves (maps) large virtual regions (heap
   reservation, thread stacks, code cache, metaspace caps) without committing them.
   Nothing is wrong: RSS is the physical truth; VSZ is the fiction's table of
   contents. Only growth-trends in RSS/PSS signal leaks.
6. The write faulted; the kernel tried to fault in a zero page; memory pressure made
   reclamation fail first → OOM killer selected the process (SIGKILL often reported
   as a crash) or, if swap was full, the fault could not be satisfied. The buffer was
   legal; the *system's* page supply was not — the exact scenario mobile platforms
   redesign with Jetsam/lmkd (Chapter 22).
7. khugepaged periodically *merges* 4 KB pages into 2 MB huge pages: it compacts
   physical memory (migrating pages to build contiguous 2 MB runs), which stalls
   faulting threads; also promotion can inflate RSS. The fix (as databases do):
   `madvise(MADV_NOHUGEPAGE)` or disabling THP for the service — page size is a
   performance contract.
8. Compression (zram on Android; the macOS/iOS compressor) replaces writing anonymous
  pages to disk, and killing by priority band (lmkd, Jetsam) replaces the OOM
  killer's blind scoring — both are page-table-level policies from this chapter
  specialized for flash-wear and battery constraints.

</details>
