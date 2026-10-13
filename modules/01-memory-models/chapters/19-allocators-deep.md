# Chapter 19 — Allocators: the machinery under malloc

> **The 30-second version.** Chapter 05 drew the heap as "a big free list" — a toy.
> Real allocators are **currency exchanges**: the OS trades only in 4096-byte pages
> (Chapter 03), programs want 16-byte nodes, and the allocator makes change while
> keeping three promises — reuse at nanosecond cost, bounded waste, and bookkeeping
> hostile code cannot easily forge. Every design in the wild is one of three shapes:
> **bump arenas** (O(1) alloc, zero metadata, no individual free), **free lists**
> (Chapter 05's model: O(list), fragmentation-prone), or **size-class segregation** —
> round every request to ~a hundred fixed classes, one free list per class, alloc and
> free both O(1). glibc's ptmalloc, tcmalloc, jemalloc, Go's heap (Chapter 11) and
> pymalloc (Chapter 14) are all the third shape. This chapter derives each one.

## The exchange: pages in, objects out

The kernel's memory manager has one denomination: the page, 4096 bytes on
x86-64/ARM64 (Chapter 03) — the TLB, permissions, and demand paging all work per
page. But a linked-list node is 16 bytes and a Python float wrapper is 24 (Chapter
06): 4096/16 = 256 objects per page. Somebody must make change — the allocator, with
three duties.

1. **Fast reuse.** A `malloc`/`free` pair must cost tens of nanoseconds, not a
   syscall — every object reused locally is one the program never asks the OS for.
2. **Low fragmentation.** Waste bounded and measurable: rounding 17 bytes to 32
   (internal waste) and stranding free holes between live blocks (external waste)
   are both failures of the exchange rate.
3. **Metadata safety.** C programs write through raw pointers (Chapter 08), so the
   bytes recording "how big is this chunk, what is it linked to" are attack surface
   (Chapter 21).

Every allocator below is a particular answer to these three duties.

## Three architectures you can derive on paper

### 1. The bump arena: allocate in one instruction

If all objects die at the same moment, individual `free` is unnecessary — nothing is
ever *individually* dead. The allocator is then one pointer:

```c
typedef struct { char *top, *end; } Arena;

void *arena_alloc(Arena *a, size_t n) {
    n = (n + 15) & ~(size_t)15;              /* 16 B alignment (Chapter 06) */
    if ((size_t)(a->end - a->top) < n)
        return NULL;                          /* full: no search, no best-fit */
    void *p = a->top;
    a->top += n;                              /* THE allocator: add + store */
    return p;                                 /* no header, no free list */
}
/* free(p) does not exist per object; free the WHOLE arena at once:
   a->top = a->start;  — one store. Zero per-object cost, ever. */
```

Fifteen lines, O(1) allocation, **zero bytes of metadata per object**, zero
fragmentation by construction. The price: no individual reclamation. Where lifetimes
are uniform this is unbeatable, which is why it keeps reappearing: the JVM's **TLAB**
(Chapter 12); V8's bumping new space (Chapter 15); per-**request arenas** in servers
(Apache's pools); per-**frame arenas** in games (below); Zig's `ArenaAllocator` plus
`deinit()` (Chapter 18).

### 2. The free list: Chapter 05, costs on the table

When objects die individually the dead must be *findable*: link them. Chapter 05's
design: `free(p)` stores the old list head in `p`'s first word; `malloc` pops the
head. Two consequences are why nobody ships this alone:

- **LIFO reuse.** The most recently freed block returns first — deterministic
  addresses (your lab below; regrettably also useful to exploit writers, Chapter 21).
- **O(list) allocation, unbounded splintering.** First-fit walks every free block:
  O(N). Freed blocks have arbitrary sizes, so `free(16); free(16); malloc(33)` cannot
  be served without splitting — free space degrades into mismatched holes.
  **External fragmentation grows without bound.**

### 3. Size-class segregation: the winning idea

The fix: stop taking requests literally. Round each request to one of ~a hundred
fixed **size classes** (16, 32, 48, ... 176, 192, 208, ... widening geometrically)
and keep **one free list per class**. Then `malloc(n)` pops class(round(n)) — O(1);
`free(p)` reads the class off the chunk header and pushes — O(1); and fragmentation
is *amortized*: waste per object is bounded by class spacing, and no free list ever
mixes sizes. Every industrial allocator of the last 25 years lives here: tcmalloc,
jemalloc, Go (Chapter 11), pymalloc (Chapter 14), glibc's fastbins/tcache, Windows'
LFH. Shared skeleton:

```text
        thread-local caches (per class, no locks)        <- tcmalloc/jemalloc/tcache
        +-----------------------------------------------------+
        | 16 B : [*][*][*]   32 B : [*][*]   144 B : [*]    |
        +-----------------------------------------------------+
              | miss: batch refill (~32 objects)          | flush: push batch down
              v                                               v
        central free list, ONE LOCK PER CLASS <----------------+
        +-----------------+  +-----------------+  +-----------------+
        | class 3 (48 B): |  | class 7 (144 B):|  | class 31 (4 KB):|
        | [obj][obj][obj] |  | [obj][obj]      |  | [obj]           |
        +-----------------+  +-----------------+  +-----------------+
              | a class runs dry / floods                    |
              v
        PAGE HEAP: spans = runs of contiguous 4 KiB pages, each labeled ONE class
        +------------------+------------------+------------------+
        | span: 4 pages    | span: 1 page     | span: 8 pages    |
        | carved into 144 B| carved into 16 B | completely free  |
        | objects          | objects          | -> returned to OS|
        +------------------+------------------+------------------+
              | page heap runs low: brk / mmap (Chapter 03)
              v
        THE OS: whole pages, no granularity below 4096 B
```

This is tcmalloc's diagram, but the layers are universal: lock-free front end,
sharded middle, page-granular back end. Now the real machines.

## ptmalloc: glibc's dlmalloc descendant, in full

glibc's `malloc` descends from Doug Lea's dlmalloc (early 1990s), extended with
**multiple arenas** by Wolfram Gloger (ptmalloc, folded into glibc around 2.3). Be
honest about the lineage: ptmalloc = dlmalloc + arenas + (much later) tcache.

### The chunk

Lea's masterstroke was zero side tables: bookkeeping lives **in the heap**, in a
16-byte header (x86-64, glibc 2.35) immediately before your data:

```text
   one glibc 2.35 chunk on x86-64, for malloc(50)
   (request 50 -> chunk size = (50+8+15) & ~15 = 64 bytes)

   p = chunk pointer                      mem = p + 16 = pointer malloc returns
   |                                      |
   v                                      v
   +----------------+----------------+---------+---------------------------+
   | prev_size (8B) | size + flags(8B)| user data (56 bytes)                 |
   +----------------+----------------+---------+---------------------------+
   |                |                | ...  next chunk's prev_size (8B)     |
   +----------------+----------------+---------+---------------------------+
                                   ^ usable = size - 8: the LAST 8 bytes of
                                     user data overlap the next chunk's
                                     prev_size (meaningful only when THIS
                                     chunk is free - it then stores our size)

   flags packed in the 3 low bits of the size word:
     P (0x1) = previous chunk in use     -> prev_size is garbage / user data
     M (0x2) = this chunk came from mmap -> free() will munmap, not bin it
     A (0x4) = chunk belongs to a non-main arena
   when FREE, the first 16 user-data bytes are overwritten with fd/bk
   (bin links) - the UAF trap of Chapters 08 and 21
```

Two numbers to burn in (glibc 2.35, 64-bit): the **minimum chunk is 32 bytes** — a
free chunk must hold 16 bytes of `fd`/`bk` plus the header — so
`malloc_usable_size(malloc(1))` is **24** (usable = size - 8). `malloc(1)` consumes
32 heap bytes: 32x overhead on the request. That is the price of in-band metadata
plus alignment.

### The bins

Freed chunks are sorted into bins by size — a cache hierarchy, not one list
(glibc 2.35, 64-bit):

```text
   free path for chunk C                                malloc path
   -------------                                         -----------

   tcache (PER THREAD: 64 bins, 7 chunks/bin,   malloc: pop from tcache bin
   chunk sizes 32..1040 B)                      -- miss? fastbins, then
        |  bin full or size > 1040               unsorted drain, then small/
        v                                        large bins, then top chunk
   fastbins: 10 slots; with the default
   max_fast=128 only 7 live (sizes 32..128,     -> pop LIFO head: O(1)
   i.e. requests <= 112 B). Singly-linked
   LIFO, NO consolidation with neighbors
        |  size > 128: goes to the arena, neighbors CONSOLIDATED first
        v
   bins[1] = unsorted bin (THE DRAIN)            -> every miss drains the ENTIRE
   every non-fast free lands here                      bin, sorting survivors
        |                                              into small/large bins
        v
   bins[2..63] = small bins (62): one bin        -> exact-fit FIFO pop: O(1)
   per 16 B step, sizes 32..1008
        |  size >= 1024
        v
   bins[64..127] = large bins (64): geometric    -> best-fit inside the range,
   ranges (1024..1280, ..1920, ..2560, .. 1 MiB+) chunks also threaded by size
                                                     via fd_nextsize/bk_nextsize
        |  nothing fits anywhere
        v
   TOP CHUNK: the free frontier at the brk edge.
   malloc splits it; free() merges adjacent free chunks into it;
   exhausted -> brk grows the arena (mmap for extra arenas)
```

Read the ladder as three caches in series:

- **tcache** (landed glibc 2.26 — in every 2.27+ system you will touch, hence the
  folklore "2.27"; the double-free key check arrived in 2.29): 64 per-thread bins,
  up to **7 chunks each**, chunk sizes to **1040 bytes**. A same-size free/malloc
  pair never touches the arena or a lock — two indexed writes. This is why
  free-then-remalloc became essentially free.
- **fastbins**: singly-linked LIFO, *no consolidation* — freed fastbin chunks do not
  merge with neighbors; that is the speed trade. As of glibc 2.35 the fastbin array
  has 10 slots but the default max fast chunk size is 128 bytes, so requests up to
  112 bytes hit 7 live bins (sizes 32-128, steps of 16). Merging is deferred to
  `malloc_consolidate`.
- **the unsorted bin as the drain**: every free above fastbin size is consolidated
  with free neighbors first (the P bit and `prev_size` exist precisely so free can
  walk backward and merge), then parked in the unsorted bin. The next malloc that
  misses tcache drains it whole, filing survivors into small/large bins and
  returning a perfect match immediately if one exists.

The **top chunk** is the free frontier at the `brk` edge: mallocs that survive the
whole bin search split it; frees adjacent to it merge into it. Only when top shrinks
past M_TRIM_THRESHOLD (128 KiB default) does glibc `brk`-shrink — often not even
then: one root of the RSS-never-drops mystery below.

### The 128 KiB cliff (and why it moves)

Requests below **M_MMAP_THRESHOLD = 128 KiB (131072 bytes, glibc 2.35 default)** come
from the arena; requests at or above it get a fresh `mmap` and the M flag — `free()`
is a direct `munmap`, pages leave RSS immediately (Chapter 08 showed the map). The
threshold is **dynamic**: freeing an mmap'd chunk raises the threshold to that size
(capped at 32 MiB on 64-bit), so a program repeatedly allocating and freeing 5 MiB
buffers stops using mmap after the first cycle — the buffers recycle inside the
arena. A real "why did run-2 RSS behavior differ from run-1" answer.

## Fragmentation, properly — and why malloc cannot compact

- **Internal fragmentation** is rounding: `malloc(17)` on glibc 2.35 x64 gets a
  32-byte chunk — 15 bytes wasted *inside* it.
- **External fragmentation** is stranding: the free bytes exist, but 200 MB of
  100-byte slivers cannot serve one 2 MB request.

The counterintuitive law, stated once: **size classes trade internal fragmentation
to bound external fragmentation.** Coarser classes waste more per object but shrink
the free-list shapes from "any size" to ~100 sizes, capping how pathological hole
layout can get. Chapter 05's exact-fit free list has zero internal waste and
unbounded external waste. The class table *is* this exchange rate.

Why not move objects together like the JVM and CLR (Chapters 12, 13)? Compaction
requires finding and rewriting **every pointer** to a moved object; GC'd runtimes
know their pointers (typed graphs, handles, stop-the-world pauses). malloc hands out
**raw, untyped addresses** (Chapter 08) — nothing, including malloc, can enumerate
the pointers into a live chunk. A C allocator can merge adjacent holes, never move
data: fragmentation in a C program is permanent residency. Huge allocations escape
entirely: past 128 KiB they are fresh mmaps outside any arena — no bins, no
neighbors, no fragmentation, but no reuse either. (.NET's 85,000-byte LOH threshold,
Chapter 13, is the same idea in GC clothing.)

## Threads changed everything: tcmalloc, jemalloc, slab, the LFH

### The contention problem

dlmalloc had one lock. Gloger's ptmalloc fix: multiple arenas — the main one at
`brk`, extras mmap'd — assigned to threads round-robin (glibc 2.3+). It worked, but
the default arena cap is 8 * ncpus on 64-bit: a 64-core box allows 512 arenas, and a
200-thread service ends up with hundreds of 64 MiB reservations — giant VSS,
scattered RSS, and lock traffic whenever assignment collides. This syndrome is why
Google and Facebook both stopped using glibc malloc on servers.

### tcmalloc (~2023)

Google's allocator is the tiered diagram, literally. Each thread gets a cache of
per-class free lists (front end), refilled in *batches* from a per-class **central
free list** — one lock per class, not per heap; contention is sharded by data, the
same lesson as Chapter 20's false sharing. The back end is the **page heap**: memory
in **spans** — runs of contiguous 4 KiB pages, each span labeled with one size class
and carved into same-size objects; empty spans return to the OS via
`madvise(MADV_DONTNEED)`. Size classes: **~88 in the classic table, up to ~170 in
2023-era builds**. A warm malloc/free pair touches one thread-local array — no lock,
no cache-line ping-pong, no arena roulette.

### jemalloc (5.x)

Jason Evans' allocator (FreeBSD's default since the early 2000s) shards further:
memory in **extents** — page-aligned runs with metadata kept *out of band* (harder
to corrupt from an overflow, contrast dlmalloc's in-band header). Work spreads over
arenas (default 4 * ncpus in 5.x), each with its own bins and extent caches; every
thread gets a tcache, and newer 5.x adds per-CPU caches. It became famous for
**measured fragmentation** — `MALLOC_CONF=stats_print:true` prints allocated vs
resident vs mapped per size class, fragmentation as a first-class metric — and for
its **wins**: Firefox 3 (2008) switched from dlmalloc and cut memory use
substantially; FreeBSD shipped it system-wide; Facebook ran its fleet on it for a
decade. The insight jemalloc proved: **sharding by thread reduces lock contention
*and* fragmentation together** — one shared free list interleaves all threads' freed
objects into mixed-lifetime soup; per-thread caches keep each thread's die-together
allocations physically together, so holes appear and vanish in batches, not slivers.

### The slab allocator (kernel territory)

Kernels don't use general allocators for hot objects — derive why: an inode is
freed and another created microseconds later, same shape, thousands per second.
Bonwick's slab (1994): per-type **object caches** — the inode cache, the dentry
cache — each pre-carving pages into fixed-shape, pre-constructed objects; free
returns to the cache's list, alloc pops it: size-class segregation where the class
is the C type. "Magazines and Vmem" (Bonwick & Adams, 2001) added per-CPU magazine
layers — batch handoff between a thread and the shared cache; tcmalloc's ancestry.
Linux, honestly in one paragraph: Linux implemented SLAB (the Bonwick design with
per-CPU arrays), then replaced it with **SLUB** (default since 2.6.23, 2007) — the
same per-CPU freelist idea with simpler metadata; SLAB was finally removed in kernel
6.8 (2024), leaving SLUB as *the* slab on Linux.

### Windows: the LFH

The NT heap had the same fragmentation disease until the **Low Fragmentation Heap** —
opt-in on XP, **default since Vista (2007)**. The LFH is the size-class idea arriving
on Windows: requests rounded into buckets, each bucket a pool of same-size slots,
served LIFO. Same law, same trade. (Windows 10 1809+ adds the "segment heap" reusing
the concepts with better page hygiene; `_msize` there is your
`malloc_usable_size`.)

## When the lifetime is known: build your own

Chapter 05 sorted heap lifetimes into classes: die-with-the-frame,
die-with-the-request, die-with-the-level, die-at-explicit-free. The rule this
chapter adds: **a custom allocator pays for itself exactly when the lifetime
structure is known — you trade generality for the deletion of bookkeeping.** The
free-list tax exists because malloc must handle *any* lifetime; a frame, request, or
level is known, so the bump arena applies. The game-developer frame heap, reset at
vsync — a real 20-line pattern:

```c
/* frame heap: all per-frame allocations (particle structs, physics scratch,
   c-string buffers) land here; nothing is ever individually freed. */
enum { FRAME_CAP = 16 << 20 };                  /* 16 MiB, tune to frame budget */
static char  frame_mem[FRAME_CAP];
static char *frame_top = frame_mem;

void *frame_alloc(size_t n) {
    n = (n + 15) & ~(size_t)15;                /* alignment, as always */
    if (frame_top + n > frame_mem + FRAME_CAP)
        return NULL;                            /* over frame budget: a BUG to
                                                   fix, not an OOM to handle */
    void *p = frame_top;
    frame_top += n;
    return p;                                   /* total cost: ~3 instructions */
}

void frame_reset(void) {                        /* at vsync, once per frame */
    memset(frame_mem, 0xD1, FRAME_CAP);         /* trash old data: any
                                                   use-after-reset is instant
                                                   garbage, not a stale read */
    frame_top = frame_mem;                      /* "free" 100k objects: 1 store */
}
```

Per-frame objects never fragment (flat again every 16.6 ms), never take a lock, and
cannot leak past one frame. Same shape at other scales: **request arenas** (one per
HTTP request, freed when the response is written), **pool allocators** (a size-class
cache for one type — the slab idea in user space), Zig arenas (Chapter 18). When
thousands of objects die at one known moment, this is not an optimization; it is the
correct data structure.

## What insiders know

- **tcache stashing.** When malloc serves a chunk from a small bin and that size's
  tcache bin is not full, glibc *stashes* the small bin's remaining chunks into the
  tcache inside the same malloc — up to 7, under the lock. Great for locality, and
  the mechanism behind the "tcache stashing unlink" attack family (Chapter 21).
- **`MALLOC_PERTURB_`** (Chapter 08's tool, allocator side): set 170 and freed
  memory fills with 0xAA while fresh allocations fill with 0x55 (the complement) —
  freed-then-reused bugs stop working *by accident* and start crashing on purpose.
  It also perturbs reuse patterns, so it doubles as fragmentation chaos-testing.
- **Fastbin/tcache reuse is LIFO and deterministic.** free(a); free(b); malloc;
  malloc returns b then a, every run (modulo ASLR base). Exploit writers rely on it
  to shape the heap; glibc 2.32's *safe-linking* mangles fd pointers precisely
  because they were too useful to attackers (Chapter 21).
- **jemalloc was a platform default twice**: FreeBSD since the early 2000s, and
  Android's system allocator from 6.0 through 10 (Android 11 switched to Scudo —
  Chapter 22). Rust shipped it as default allocator until 1.32 (2019), then switched
  to the system allocator to cut binary size.
- **Per-thread caches are cold at thread birth.** A new thread's tcache starts
  empty; its first allocations take the locked arena path. Microbenchmarks on fresh
  threads measure *warmup*, not steady state.
- **Production tuning knobs**: `GLIBC_TUNABLES=glibc.malloc.tcache_count=0` disables
  tcache entirely (last resort when chasing a double free/UAF — behavior changes
  radically); `MALLOC_ARENA_MAX=2` caps arenas on giant-core boxes;
  `MALLOC_TRIM_THRESHOLD_` / `MALLOC_MMAP_THRESHOLD_` move the cliffs above.

## See it yourself

**Lab 1 — the 24-byte floor** (glibc 2.35, x86-64):

```c
/* sizes.c */
#include <stdio.h>
#include <malloc.h>                     /* macOS: malloc_size() instead */
int main(void) {
    for (size_t n = 1; n <= 64; n += 8)
        printf("malloc(%2zu) usable=%zu\n", n, malloc_usable_size(malloc(n)));
}
```

```text
$ cc -o sizes sizes.c && ./sizes
malloc( 1) usable=24      <- 32-byte chunk: the floor
malloc(17) usable=24      <- (17+8+15)&~15 = 32
malloc(25) usable=40      <- 48-byte chunk
malloc(41) usable=56
malloc(57) usable=72      <- 80-byte chunk
```

Usable = chunk - 8, never the request; every chunk is a multiple of 16.

**Lab 2 — watch tcache LIFO with your own eyes:**

```c
/* reuse.c */
#include <stdio.h>
#include <stdlib.h>
int main(void) {
    void *a = malloc(100), *b = malloc(100), *c = malloc(100);
    printf("a=%p b=%p c=%p\n", a, b, c);
    free(a); free(b);                   /* tcache bin (112-B chunks): b -> a */
    void *d = malloc(100), *e = malloc(100);
    printf("d=%p e=%p\n", d, e);        /* d==b, e==a: LIFO, same addresses */
}
```

Run it repeatedly: `d == b`, `e == a` every run — the ASLR *base* moves, the
*layout* does not. Rerun with `GLIBC_TUNABLES=glibc.malloc.tcache_count=0`: reuse
now routes through the fastbin/arena paths, and other threads' same-size activity
can change the picture — same binary, different cache serving the pop.

**Lab 3 — the 128 KiB cliff in syscalls** (t.c: malloc 1 KiB, free, malloc 10 MiB,
free):

```text
$ strace -e brk,mmap,munmap ./t
brk(NULL)                          = 0x55e1a3a7f000   <- where is the top?
brk(0x55e1a3aa0000)                = 0x55e1a3aa0000   <- 1 KiB served from the
                                                         ARENA (no mmap for it)
mmap(NULL, 10493952, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0)
                                   = 0x7f5c8a7c9000   <- 10 MiB: OUTSIDE the
                                                         arena, page-aligned
munmap(0x7f5c8a7c9000, 10493952)   = 0                <- freed: pages gone NOW
```

The 1 KiB never becomes a syscall (tcache/arena served it); the 10 MiB is a direct
mmap/munmap pair — 10 MiB of RSS appears and vanishes in one call each.

**Bonus — jemalloc stats and arena caps via environment**:

```text
$ LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2 \
  MALLOC_CONF=stats_print:true:stats_print_opts:J ./reuse
```

Out comes the famous per-class table: allocated vs resident vs mapped, arena by
arena — measured internal fragmentation, printed at exit. Compare glibc:
`MALLOC_ARENA_MAX=1 ./reuse` gives the single-arena, always-locked world of original
dlmalloc.

## How to use this knowledge

- **Diagnose "RSS never drops."** Flat live-object count, RSS 4x and creeping:
  external fragmentation (unmergeable holes between live chunks) plus glibc's
  conservative trimming. Confirm with `malloc_stats()` or a jemalloc preload; fix
  the allocation pattern (same-size batches, arena-per-session) or the allocator —
  you cannot compact; the pointers are raw (Chapter 08).
- **Pick allocators deliberately for threaded servers**: cap arenas
  (`MALLOC_ARENA_MAX`) or preload jemalloc/tcmalloc — and know *why*: lock
  sharding plus lifetime locality, not "malloc is slow."
- **Design with lifetimes, not just sizes**: the moment you can name when a group
  of objects dies, an arena deletes the bookkeeping problem entirely. Frame heaps,
  request pools, per-level pools — the biggest allocation win available to
  C/C++/Zig (Chapter 18), and how game engines survive.
- **Debug use-after-free and double free faster**: `MALLOC_PERTURB_=170` for
  trash-on-free; `glibc.malloc.tcache_count=0` to stop tcache masking timing; read
  glibc abort messages knowing *which* layer spoke — "tcache 2" vs "invalid next
  size (fast)" vs "corrupted double-linked list" (unsorted/smallbin unlink) each
  name a different bin and bug class (Chapter 21).

## Connections

- **Chapter 03** — the page: the OS-side currency the allocator exchanges against.
- **Chapter 05** — the conceptual heap (free lists, brk/mmap, lifetime classes);
  this chapter is that skeleton with real muscles.
- **Chapter 08** — the chunk header up close, `MALLOC_PERTURB_`, and the raw-pointer
  fact that makes compaction impossible.
- **Chapter 11** — Go's runtime is this chapter's tiered diagram: mcache (thread),
  mcentral (per class), mheap (spans). Learn one, know both.
- **Chapter 12** — the JVM TLAB: a bump arena per thread; compaction works only
  because the runtime can pause and rewrite pointers.
- **Chapter 14** — pymalloc: 4 KiB pools carved into classes 8-512 B — slab
  thinking inside the interpreter, malloc as its page heap.
- **Chapter 15** — V8's semispace scavenger: bump allocation plus wholesale flip.
- **Chapter 18** — Zig's arena and allocator traits: the bump design, modern dress.
- **Chapter 20** — false sharing: why one freelist head shared by 64 threads is a
  cache-line catastrophe, and why sharding fixed contention and fragmentation.
- **Chapter 21** — in-band metadata, fastbin/tcache attacks, safe-linking: the
  security bill for dlmalloc's layout.
- **Chapter 22** — Android's jemalloc-to-Scudo switch; allocator life under Jetsam.
- **Chapter 23** — the unification table: "allocator" is one axis every language's
  memory model sits on.

*Forward links — where the rest of the program picks this chapter up:*

- **Module 02 ch04 (heap exploitation)** — tcache poisoning is this chapter's metadata as the attack surface — the bins you learned are the target list.
- **Module 22 ch01 (the caching allocator)** — PyTorch's GPU allocator is the arena/block flavor of this chapter's taxonomy, fragmentation included.
- **Module 09 ch03 (lock-free allocation)** — hazard pointers and epochs are how allocators survive threads — this chapter's free list under concurrency.

## Quiz

**Q1 (concept).** The OS deals 4096-byte pages; your program asks for 16-byte nodes.
State the allocator's three duties, and explain why "just mmap each object" fails at
least two of them.

**Q2 (predict the number).** On glibc 2.35, x86-64: how many *heap* bytes does
`malloc(1)` consume, and what does `malloc_usable_size` report? Same two questions
for `malloc(25)`.

**Q3 (predict the behavior).** A program calls `malloc(100000)` — one hundred
thousand bytes. Does glibc 2.35 serve it from the arena (brk) or a fresh mmap?
What appears in strace — and what changes after the program frees a 5 MiB mmap'd
buffer?

**Q4 (predict the output).** In reuse.c from the lab (a, b, c = three
`malloc(100)`; `free(a); free(b);` then d, e = two more `malloc(100)`), what are d
and e, and why? What changes under
`GLIBC_TUNABLES=glibc.malloc.tcache_count=0`?

**Q5 (bug diagnosis).** A long-running C++ service's object counter says 800 MB
live, but RSS is 4.5 GB and creeping. Thousands of different-sized small objects
with interleaved lifetimes. Valgrind: no leak. Restart fixes it for a month. What is
the disease, why can't the allocator cure it in place, and give two treatments.

**Q6 (bug diagnosis).** A program frees the same pointer twice in a row on glibc
2.35 and aborts: `free(): double free detected in tcache 2`. Which layer caught it,
by what mechanism, and name one scenario where the same double free is *not* caught
there and proceeds somewhere worse.

**Q7 (bug diagnosis).** A 64-core, 200-thread glibc-linked service: ~300 GB virtual
size, CPU time accumulating in `_int_malloc`, latency spikes under load. Explain the
mechanism and give two fixes with tradeoffs.

**Q8 (cross-chapter).** Name the architecture from this chapter each runtime
embeds, one line each: (a) the JVM's TLAB (Chapter 12); (b) pymalloc's pools
(Chapter 14); (c) Go's mcache/mcentral/mheap (Chapter 11); (d) why can the JVM
(Chapter 12) compact its heap while malloc never can?

<details>
<summary>Answers</summary>

1. **Duties: fast reuse, low fragmentation, metadata safety.** mmap-per-object
   fails fast reuse (a syscall is ~1-2 microseconds; a tcache pop is tens of
   nanoseconds — 50-100x); fails low fragmentation catastrophically (every 16-byte
   object burns a whole 4096-byte page: 256x internal waste, plus page-table and TLB
   pressure, Chapter 03). It is only acceptable on metadata safety — nothing
   in-band to corrupt — which is exactly why *huge* allocations do go to mmap:
   per-page overhead amortizes.

2. **malloc(1): 32 heap bytes, usable 24.** Request 1 rounds to
   `(1+8+15)&~15 = 16`, but MINSIZE = 32 (a free chunk must hold 16 bytes of fd/bk
   plus the 16-byte header). Usable = 32 - 8 = 24: the last 8 bytes of user data
   legally overlap the next chunk's `prev_size`, meaningful only while the neighbor
   is free. **malloc(25): 48-byte chunk, usable 40** — `(25+8+15)&~15 = 48`.
   (glibc 2.35, 64-bit.)

3. **From the arena via brk.** 100,000 < 131,072 — the default 128 KiB
   M_MMAP_THRESHOLD. Six digits, still under the cliff: a favorite trap. strace
   shows `brk(NULL)` then a grown `brk`, no big mmap. After the 5 MiB mmap'd chunk
   is freed, dynamic threshold adjustment raises M_MMAP_THRESHOLD to ~5 MiB (capped
   32 MiB on 64-bit), so later 5 MiB allocations come from the arena — glibc
   decided the mmap/munmap cycle was a repeated pattern worth caching in-arena.

4. **d == b and e == a.** Both requests map to 112-byte chunks whose tcache bin is
   a LIFO stack: free(a) pushes a, free(b) pushes b — stack b -> a; two pops return
   b, then a. Deterministic every run; only the ASLR base moves. With
   `tcache_count=0` the tcache is disabled and frees push the 112-byte chunks onto
   the fastbin (112 <= 128) — still LIFO, so d/e often still equal b/a, but now
   any same-size malloc/free from other threads or libc can interleave, because
   the tcache's per-thread isolation is gone.

5. **External fragmentation, plus conservative trimming.** Interleaved lifetimes of
   varied sizes strand free holes between live chunks; malloc cannot move live
   chunks to merge them — pointers into them are raw and untyped (Chapter 08), no
   compaction ever. Restart rebuilds the interleaving from scratch. Treatments: (a)
   switch allocators — jemalloc's per-thread sharding keeps die-together batches
   physically together and *measures* the fragmentation; (b) restructure allocation
   into homogeneous batches or arena-per-session. Raising trim thresholds is not a
   cure: trim only returns memory contiguous with the top chunk.

6. **The tcache caught it.** A chunk entering a tcache bin gets a per-thread random
   `key` written into its second word; `free` checks whether the chunk already
   carries this tcache's key (glibc 2.29+). Two consecutive frees: the second finds
   the key — abort. **Not caught when** the bin already holds 7 chunks: the chunk
   overflows to the fastbin/unsorted path, where the check is weaker — fasttop
   catches only a repeat *at the fastbin head*; and a UAF write between the frees
   can overwrite the key, or 7+ intervening same-size frees can flush the chunk out
   so the second free sees a "clean" chunk. Both escape routes feed Chapter 21's
   exploit techniques.

7. **Arena explosion.** Default arena cap = 8 * ncpus = 512 on 64 cores; 200
   threads get arenas round-robin and contend on each arena's mutex; every arena is
   a fresh 64 MiB mmap reservation whether fully used or not — hence ~300 GB VSS,
   scattered RSS, and lock-convoy spikes in `_int_malloc`. Fixes: (a)
   `MALLOC_ARENA_MAX=2` or 4 — one tunable caps VSS; tradeoff: more threads queue
   per arena lock; (b) preload tcmalloc or jemalloc — per-thread caches make the
   steady-state path lock-free; tradeoff: a third-party dependency and different
   behavior in core dumps.

8. **(a) TLAB = bump arena**: each Java thread bumps a thread-local pointer inside
   the shared heap; the GC frees TLABs wholesale (Chapter 12). **(b) pymalloc =
   size-class slab in user space**: 4 KiB pools carved into same-class 8-512 B
   objects, one free list per class, malloc itself as page-heap back end
   (Chapter 14). **(c) Go = the full tcmalloc tiering**: mcache per P (lock-free
   thread-local), mcentral per size class (sharded lock), mheap over page spans
   (Chapter 11). **(d) The JVM can compact because it knows where every pointer
   is** — the object graph is typed and traceable, so a stop-the-world pause can
   rewrite every reference to a moved object. malloc's pointers are raw and
   untyped (Chapter 08): it cannot even find them, so merging adjacent holes is
   its only move.

</details>
