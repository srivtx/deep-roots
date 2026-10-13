# Chapter 05 — The heap: the everything-else machine

> **The 30-second version.** The stack handles lifetimes that nest; everything else —
> objects that outlive their creating function, sizes unknown until runtime — needs a
> different machine: a pool of blocks that die in unknowable order. That pool is the
> heap, and its two hard problems are *which freed bytes satisfy the next request*
> (allocation policy) and *what happens when no contiguous bytes can* (fragmentation).
> Every language in Part II is a different answer to one question: who manages this
> machine? Chapter 19 goes inside the allocators; this chapter builds the problem they
> solve.

## The heap problem, stated from first principles

Write down what the stack could not do: a function creates a buffer that must survive
its return (return the data, not the pointer to a dead frame — Chapter 04); a parser
discovers at runtime that the message contains 13,000 items; a cache holds objects for
hours with no call-graph relationship to each other. The common shape: **allocation
and deallocation are not nested, not predictable, and not compile-time-known.**

So the heap is born as a contract, not a mechanism: `malloc(n)` returns a pointer to
at least n usable bytes; `free(p)` releases a previous result; between them, nobody
moves the block (stability — pointers must stay valid; this single promise is what
makes compaction impossible later). The C standard says almost nothing else. As
always with C (Chapter 08), the contract is minimal and the consequences are yours.

## What malloc/free must secretly do

The interface hides three engineering duties:

1. **Bookkeeping**: `free(p)` receives a pointer *value* only — no size. The block's
   size (and neighbors' sizes) must be recoverable from memory adjacent to the block
   itself. In glibc, a header word sits 8 bytes *before* your data: this is why
   `malloc(1)` really consumes 32 bytes (Chapter 19 draws the whole chunk), and why
   one-byte overflows corrupt *the next block's header* — the bridge to Chapter 21's
   heap attacks.
2. **Reuse policy**: freed bytes accumulate; the next `malloc(n)` must find a fit.
   Free lists, size-class bins, best-fit/exact-fit — Chapter 19 is this topic in
   depth. The design goal is that typical malloc/free cost ~tens of nanoseconds,
   which is only achievable if the search is O(1) by construction (per-size-class
   lists), not a search at all.
3. **OS interface**: the heap itself is made of pages (Chapter 03). Two syscalls
   grow it: `brk`/`sbrk` extends the classic heap region upward (small blocks), and
   `mmap` carves fresh mappings for big blocks (glibc default threshold: 128 KB —
   dynamic, see Chapter 19). Watch it happen:

```c
#include <stdio.h>
#include <stdlib.h>
int main(void) {
    char *a = malloc(1000);           // from the brk arena (no syscall)
    char *b = malloc(50 * 1024 * 1024);  // 50 MB — mmap, its own map entry
    printf("a=%p b=%p\n", a, b);
    // /proc/self/maps: 'a' lives in [heap]; 'b' is a standalone rw-p region
    return 0;
}
```

   Run under `strace ./a.out 2>&1 | grep -E 'brk|mmap'` and see exactly one big
   `mmap` — plus, if you like, the page faults at first touch (Chapter 03's lesson:
   the mmap is free; the memory is not).

## The three costs of heap allocation

The price is not one number; it is three independent taxes, and knowing *which tax*
you are paying is the whole diagnostic skill:

- **The allocation tax**: metadata + policy work per call — cheap per se (tcache-era
   glibc: tens of ns), deadly at *rate*: an allocation loop at 100 MB/s dominates
   caches (Chapter 02) and, in GC languages, drives collector cadence (Chapters 12,
   15, 22: "GC pressure" — allocation rate *is* the GC's workload).
- **The locality tax**: heap blocks scatter across lines and DRAM rows (Chapters 01,
   02). Node-based structures pay per hop. Allocators partially fight back — bump
   allocation packs neighbors, size classes cluster similar blocks — but heap
   *order is program order*, and program order is often adversarial.
- **The fragmentation tax**: the slow killer, below.

## Fragmentation, the slow killer

**External fragmentation**: free memory exists in total, but not *contiguously* —
10 MB free in 64 KB holes cannot satisfy a 1 MB request. **Internal fragmentation**:
the request was 25 bytes; the size class rounds to 32 (Chapter 19) — 7 bytes lost
inside the block. Internal is a rounding tax you can measure; external is a topology
disease you can only watch grow.

The mechanism of external fragmentation is *lifetime interleaving*: allocate blocks
A, B, C adjacent; free A and C; B (still live) fences the freed space into two
holes. Long-running servers churn mixed sizes at mixed lifetimes for weeks, and the
heap *ratchets*: holes too small for current traffic accumulate between survivors.
RSS creeps up though every malloc/free pair is correct — the classic "we have no
leaks but memory grows" ticket.

**Why can't the allocator just compact?** — the deepest fact on this page: compaction
means *moving blocks*, which means *finding and updating every pointer to them*.
Only possible if the system can enumerate all pointers. C and C++ cannot (pointers
live untyped in registers, unions, files — Chapter 04's last quiz question). GC
languages can, because the runtime *does* know where every reference lives — that is
why Java's copying collectors and V8's scavenger (Chapters 12, 15) defragment as a
side effect of collection, and malloc-worlds cannot. Fragmentation is not an
implementation oversight; it is the price of raw pointers.

The partial mitigations, all in Chapter 19: size classes (bound the hole sizes),
arenas (shard the topology), and the application-level escape — *lifetime design*.

## Lifetime classes: the design tool this chapter earns you

Diagnosis beats treatment: match each allocation's *real* lifetime class to the right
machine, and most heap problems dissolve:

| Lifetime class | Example | Right tool | Why |
|---|---|---|---|
| Nest with the call | temporaries | the stack (Ch 04) | free by construction |
| One batch/frame | parse tree, request-scoped | **arena/bump allocator** | `arena_reset()` frees thousands in one instruction |
| Fixed pool | connections, particles | **object pool** | no churn, stable addresses |
| Unpredictable | caches, session state | general heap (+GC if the language has one) | the honest case |

The arena row is the insider's favorite: allocate thousands of nodes with `arena_alloc
(n)` (a pointer bump — Chapter 19 shows a 15-line implementation), then drop the
*whole tree* with one `arena_free`. Compilers, game frame-heaps, request handlers,
 parsers: allocation rate collapses to ~zero, fragmentation becomes structurally
impossible (the arena is contiguous), and the code gets *simpler* — no destructor
plumbing (Rust and Zig turned this into a language feature — Chapters 10, 18).

## The multithreaded heap

One global free list = one lock = every allocating thread serialized (and false
sharing on the list head — Chapter 02). Every modern allocator answers with
**per-thread caches**: glibc's tcache, Java's TLABs (Chapter 12), Go's per-P mcache
(Chapter 11), tcmalloc's thread caches (Chapter 19). The pattern is universal
because the physics is: allocate locally, refill in batches from the shared pool,
never contend on the hot path. Consequence worth knowing: per-thread caches mean
*frees can pile up in a dying thread's cache* and memory appears "leaked" until the
thread exits — a real production confusion (Chapter 19's diagnostics).

## What insiders know

- **`malloc_usable_size`** (glibc) prints the real block size — instant
  metadata-overhead intuition: `malloc(1)` → 24 usable / 32 consumed on x86-64
  glibc 2.35.
- **free(NULL) is defined no-op**; `free` of an interior pointer (`p+8`) or a stack
  address aborts with the glibc message every C veteran has met (`free(): invalid
  pointer`).
- **"RSS never drops" has three stacked reasons**: freed blocks stay in allocator
  free-lists (not returned to the OS), arenas release to the OS only when *fully*
  empty (Chapter 19), and the OS page cache holds the rest (Chapter 03).
  `malloc_trim(0)` / `MALLOC_ARENA_MAX` are the glibc levers; pymalloc arenas behave
  the same (Chapter 14).
- **glibc opens up to `8 × ncores` arenas** — a 64-core box can run 512 arenas and
  *look* like a 5x RSS leak. `MALLOC_ARENA_MAX=2` is the standard big-server fix.
- **`MALLOC_PERTURB_=`** fills freed/fresh blocks with complementary byte patterns
  (0xa5/0x5a) — the cheapest use-after-free detector that exists, Chapter 08's lab
  uses it.
- **The two-syscall map**: small = brk, large = mmap(128 KB threshold, *dynamic*: it
  adapts upward when mmap'd blocks get freed — Chapter 19's "why run 2 behaved
  differently" war story).
- **Zeroing is your job in C, the allocator's in GC-land**: `calloc` can exploit
  the zero-page trick (Chapter 03: fresh anonymous pages are already zero — so a
  *big* calloc can be lazier than malloc+memset).

## See it yourself

The syscall boundary, the metadata, and the "leak that isn't":

```console
$ strace -e trace=brk,mmap ./alloc_demo   # watch small vs large growth
$ cat /proc/self/maps | grep heap          # the brk region, by name
```

```c
#include <malloc.h>
#include <stdio.h>
int main(void) {
    void *p = malloc(1);
    printf("usable=%zu\n", malloc_usable_size(p));   // 24 (glibc 2.35, x86-64)
    free(malloc(64)); free(malloc(64));              // tcache now holds both
    // LIFO reuse: the next two malloc(64)s return the SAME addresses
    void *a = malloc(64), *b = malloc(64);
    printf("a=%p b=%p\n", a, b);
    return 0;
}
```

And the fragmentation ratchet in one program: allocate 10,000 × 1 KB; free every
*other* one; then request 2 MB. Total free space ≈ 5 MB, but the 2 MB request goes
to `mmap` (fresh pages) because the arena's holes can't hold it — RSS rose though
"free" succeeded. Now you have reproduced, in twelve lines, the ticket that takes
production teams a week.

## How to use this knowledge

- **Memory triage methodology**: first ask *which tax* — leak (RSS grows with
  no plateau), fragmentation (RSS grows to a plateau and stalls), or retention
  (live references you forgot: caches, listeners — Chapter 15's closure traps).
  Each has a different tool: heap profiler vs allocator stats vs a heap dump.
- **Design with lifetime classes**: arenas and pools at the boundaries where
  lifetimes are actually known — the biggest single memory win most programs have
  available (and a latency win: Chapter 02 loves contiguity).
- **Rate discipline in GC languages**: allocation-rate graphs (Chapter 22's jank
  story) are this chapter's "allocation tax" wearing platform clothes — the fix is
  fewer, larger, longer-lived allocations, i.e., heap hygiene.
- **Reading Chapter 19 with purpose**: bins, tcache, and arenas are now *answers to
  problems you have personally felt* — metadata recovery, O(1) reuse, and topology
  management.

## Connections

- **Chapter 03** — the heap's pages, brk/mmap, first-touch faults, OOM as the
  last resort.
- **Chapter 04** — the division of labor: nesting lifetimes vs everything else.
- **Chapter 08 / 09** — the C/C++ contracts and their failure modes; RAII as
  lifetime discipline.
- **Chapter 10** — Rust's ownership: the compile-time answer to "who frees."
- **Chapter 11 / 12 / 13 / 14 / 15** — every runtime's allocator: mcache, TLAB,
  CLR gen0, pymalloc, V8 new space — all Chapter 05 problems with Part II solutions.
- **Chapter 19** — the allocators in depth: this chapter's three duties, done for
  real.
- **Chapter 21** — the chunk header as attack surface; tcache poisoning.

*Forward links — where the rest of the program picks this chapter up:*

- **Module 11 ch02 (B-trees)** — the disk as the new heap: page splits are the allocator's fragmentation math at 4 KiB granularity.
- **Module 14 ch02 (VFS and allocators)** — file systems are allocators that outlive the process — ext4's block bitmap is this chapter's free list, persisted.
- **Module 22 ch01 (the tensor)** — PyTorch's caching allocator is the arena flavor of this chapter's taxonomy, with 512-byte blocks and a fragmented OOM.
- **Module 19 ch04 (GC as proof search)** — the bump-pointer nursery is allocation with no per-object cost because free space itself is the proof.

## Quiz

1. **Concept.** Why must `free()` accept a bare pointer with no size, and what does
   that force the allocator to do?
2. **Concept.** Why can the JVM compact its heap while glibc cannot? Which
   promise made at the start of this chapter forbids it in C?
3. **Predict the number.** x86-64 glibc 2.35: total bytes consumed by
   `malloc(1) + malloc(24) + malloc(25)` (three separate calls)?
4. **Predict the behavior.** A program loops `p = malloc(1 MB); free(p);` 10,000
   times. RSS over time: flat, or what? Now the same loop with
   `p = malloc(200 KB); free(p);` — same or different, and why?
5. **Bug diagnosis.** RSS climbs 3 GB over two weeks; the heap profiler shows no
   leak (all blocks freed); `malloc_stats` shows the arena growing. Diagnosis?
6. **Bug diagnosis.** A 64-core server's RSS is 5x the sum of live objects.
   `MALLOC_ARENA_MAX=2` fixes it. What was the mechanism?
7. **Bug diagnosis.** A parsing workload runs 8x slower than a hand-rolled parser
   with the same algorithm; both use ~equal instructions. The slow one mallocs one
   node per token. What two Chapter taxes are being paid?
8. **Cross-chapter.** The Go runtime starts goroutines with tiny stacks (Chapter 11
   / 04) and still needs a heap: name the lifetime class stacks cannot serve, and
   the per-thread structure Go uses to serve it cheaply.

<details>
<summary>Answers</summary>

1. Because the C interface (Chapter 08) was designed to pass the minimum
   information — a pointer identifies a block uniquely, and callers would have to
   track sizes otherwise (with bugs when they lie). It forces the allocator to store
   the block's size in *metadata adjacent to the block* (glibc: the word at p−8,
   plus neighbor sizes at p−8's flags) — recoverable from the pointer alone. That
   adjacency is also Chapter 21's attack surface: overflow the block, hit the
   metadata.
2. The JVM's runtime can enumerate every live reference (GC roots, typed object
   graphs — Chapter 12), so it can move objects and *rewrite* the references. C
   promised block stability and cannot enumerate raw pointers (registers, unions,
   foreign code) — the allocator cannot know where to rewrite, so the best it can do
   is manage topology with size classes (Chapter 19).
3. 32 + 32 + 48 = 112 bytes. glibc's minimum chunk is 32 (24 usable) on x86-64
   (request 1 and 24 both fit in 24 usable); 25 rounds up to the next 16-multiple
   chunk (40 usable, 48 total). One third of the memory is metadata/rounding — the
   internal-fragmentation tax in miniature.
4. 1 MB loops: RSS flat *after the first iterations* — 1 MB > 128 KB mmap
   threshold, each cycle mmaps and munmaps fresh pages (note the *dynamic*
   threshold caveat in Chapter 19). 200 KB loops: the first free raises the
   threshold; subsequent cycles serve from the brk arena, so RSS settles at ~one
   block's worth and stays — but the arena *top* never shrinks until trim. The two
   loops differ exactly at the brk/mmap boundary.
5. External fragmentation: mixed-size blocks at interleaved lifetimes ratchet holes
   between survivors; freed-but-unreturned arena pages keep RSS growing. Confirm
   with `malloc_stats`/`malloc_info` (bin-level hole inventory) and the fix is
   Chapter 19's arsenal: size-class awareness, arenas cap, or (real fix) an
   arena/pool for the churny lifetimes.
6. glibc opens up to 8 × 64 = 512 arenas; each arena grabbed its own sbrk/mmap
   growth and caches freed chunks per-thread. The *sum of arenas' free lists* was
   ~5x live data. Capping arenas trades some contention (more lock traffic) for
   bounded topology — the standard big-server compromise.
7. The allocation tax (per-node malloc = policy + metadata work at high rate) and
   the locality tax (nodes scattered across lines/rows — every traversal is a
   Chapter 02 miss). The hand-rolled parser almost certainly used one arena of
   contiguous nodes: one bump per token, cache-perfect traversal — the lifetime-
   class table in action.
8. Lifetimes that outlive the goroutine's current call (or exceed its tiny stack in
   size): anything created in one function and used after it returns. Go serves it
   with the per-P mcache over the span/size-class heap — Chapter 11's version of
   this chapter's per-thread-cache pattern.

</details>
