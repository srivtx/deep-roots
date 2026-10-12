# Chapter 03 — Windows: the NT heap

> **The 30-second version.** Every `HeapAlloc` is served by a two-tier machine
> in ntdll: a **back end** that owns real address space — segments reserved at
> 1 MB granularity, committed page by page, chopped into blocks and knitted
> back together by coalescing — and a **front end**, the **LFH** (Low
> Fragmentation Heap), which intercepts *hot* sizes and serves them from
> pre-carved same-size containers that fragmentation cannot strand. It wakes
> only after a size class has been hit about 17 times, and never above 16 KB
> (0x4000). Between the tiers sits an 8-byte header per block — since Windows
> 8 it is **XOR-encoded with a per-heap key**, because two decades of heap
> exploitation taught Microsoft what happens to plaintext metadata. When it
> breaks: pageheap (`gflags ... +hpa`), `!heap -s -a`, UMDH diffs.

## The exchange, again — and why it needs two tiers

Module 01 ch19 derived every allocator from one trade: the OS sells only 4 KB
pages (module 01 ch03), programs want 16-byte nodes, and the allocator makes
change while promising cheap reuse, bounded waste, and metadata hostile to
forgery. The NT heap — a per-process `RtlAllocateHeap`, born with NT 3.1 — is
the Windows answer, and like glibc's ptmalloc it is two machines bolted
together: a general back end plus a fast-path front end. glibc pairs `tcache`
with an arena; Windows pairs the LFH with the segment manager. tcache is
per-thread and always on; the LFH is per-heap and **activated by heat** —
only sizes that prove themselves get the fast path.

```text
        malloc(n) -> HeapAlloc -> RtlAllocateHeap
                        |
        round (n + 8 B header) up to a 16-B multiple     (x64; 8-B on x86)
                        |
        +---------------+----------------+
        |                                |
  n <= 0x4000 AND class hot      n > 0x4000, or class cold
  (17+ hits)                     (counter < 17)
        |                                |
 +------v-------+  LFH miss    +---v---------------------------+
 | FRONT END    | -----------> | BACK END                      |
 | (LFH)       |  fall through| segment: 1 MB (0x100000)      |
 |             |              | reserved, 8 KB committed;     |
 | bucket ladder (x64):       | pages commit on demand; UCR   |
 |  0x10 0x20 0x30 ... 0x1f0  | list tracks uncommitted holes |
 |  0x200 ... step widens ...  | decommit returns idle pages   |
 |  ceiling 0x4000 (16 KB)    |                               |
 |                            | free chunks: size-indexed    |
 | per bucket: UserBlock      | free lists (ListHints),       |
 | containers of same-size    | coalesced with neighbors      |
 | chunks; busy bitmap;       |                               |
 | per-CPU affinity (Win8+)   | > VirtualMemoryThreshold:    |
 | random slot order (Win8+,  | own VirtualAlloc region +    |
 | anti-UAF)                  | fatter header, randomized    |
 +----------------------------+ placement (Win8+)            |
                              +-------------------------------+
```

Two properties deserve derivation. **Why a front end:** a general best-fit
allocator must search and split, and its free space *fragments* — free
100-byte holes scatter between live blocks until a 1 KB request fails with
1 MB free. A bucket of same-size chunks is immune by construction: every hole
in it is an exact fit for the class, so external fragmentation inside a bucket
is zero and waste is bounded by the class step.

## The back end: segments, free lists, and the decommit bargain

`RtlCreateHeap` (the engine under `HeapCreate`) reserves its first **segment:
1 MB (0x100000) of address space with only 8 KB (0x2000) committed** on a
default Windows 10 x64 heap — both numbers live in `_HEAP` as
`SegmentReserve`/`SegmentCommit`, so they are policy, not law. The heap grows
by committing pages on demand, adds new ~1 MB segments as they fill, and walks
a **UCR list (uncommitted ranges)** to know which holes have no backing —
module 01 ch03's demand paging bought wholesale: reserve is free, commit is
the real cost, the same bargain as Linux's anonymous brk.

Carved from committed pages are **blocks**, each prefixed by an 8-byte
`_HEAP_ENTRY`. Free blocks sit on doubly linked **free lists** indexed by
size, with "ListHints" caching the last-found spot; freeing coalesces a block
with its neighbors (the next header records `PreviousSize`, so neighbors are
found without a scan). The 1990s folklore that birthed the LFH lives here:
NT 3.x/4-era applications churned these lists into dust — free space trapped
between live blocks, allocation slowing as lists grew, large requests failing
with plenty of total free memory.

The back end also shrinks: a coalesced free block larger than
`DeCommitFreeBlockThreshold` (~1 KB on a Windows 10 x64 default heap), once
total free passes `DeCommitTotalFreeThreshold` (~4 KB), has tail pages
returned via `VirtualFree(MEM_DECOMMIT)`. Hence `!heap -s` shows **Reserve**
flat at megabytes while **Commit** rises and falls with the workload: the heap
keeps its address space and gives back the memory. Escape hatch: requests
above the heap's `VirtualMemoryThreshold` — **0x7F000 (508 KB) as set by
`RtlCreateHeap` on 32-bit Windows 7/8** — never touch the free lists; they get
a dedicated `VirtualAlloc` region, a fatter `_HEAP_VIRTUAL_ALLOC_ENTRY`
header, and (Windows 8+) randomized size and offset.

## The front end: the LFH, its activation counter, and the ladder

The LFH is not a separate heap — Microsoft's docs say it plainly: it is a
*policy* the heap manager applies, automatically since Windows Vista, to sizes
that earn it. Activation is per size class, and the mechanism (verified against
Windows 8 back-end code, functionally the same on 7) is beautifully cheap: an
array of 16-bit counters, `FrontEndHeapUsageData`, one per size. Each
allocation of a size adds **0x21 (33)** — chosen so 17 additions push the low
five bits past 0x10. The test is literally `(count & 0x1F) > 0x10`, so the
**17th allocation of the same size flips that class onto the LFH**; a separate
`count > 0xFF00` test promotes slow accumulators. Frees run the counter down.

Hot classes are served from **UserBlock containers** — `1 << PageShift`-byte
slabs (capped at 0x78000, Windows 8 32-bit) carved entirely into same-size
chunks, tracked by a one-bit-per-chunk busy bitmap. Windows 8 added two
behaviors: **allocation affinity** — per-logical-processor subsegment arrays
(the TEB's `HeapVirtualAffinity` picks one), so a hot bucket doesn't serialize
threads — and **randomized slot order**, which demolished the "predict the
next chunk" step of every pre-2012 use-after-free groom.

The ladder (sizes round *up* into the next class), per version:

```text
classic NT heap (x64, Vista .. Win11, still the default for desktop apps):
    classes step 16 B at the bottom (forced: chunks are 16-B multiples),
    the step widens as classes grow; hard ceiling at 0x4000 (16 KB).
    Above 0x4000: back end only, always.

segment heap (Windows 10, 1709/1809 era onward; default for UWP/MSIX apps,
    opt-in otherwise; classic desktop apps keep the NT heap as of Win11):
    16-B classes up to 0x200 (512 B), then the half-power ladder
    0x200, 0x300, 0x400, 0x600, 0x800, 0xC00, 0x1000, 0x1800, ...
    ceiling again 0x4000.
```

`_LFH_HEAP` carries at most **129 buckets** (Windows 8, 32-bit;
`Buckets[129]`) — why the ladder widens with size instead of tracking every
16-byte multiple to the ceiling.

## The armored header: why your metadata is XOR soup

Every block — front end or back end — is prefixed with 8 bytes of metadata,
the `_HEAP_ENTRY`, at `user_pointer - 8` (x64, classic NT heap,
Vista..Win11): `Size:2B` (granularity units — 16 B on x64, 8 B on x86, so
`malloc(3)` on x64 is one 16-byte chunk, `Size = 1`), `Flags:1B` (bit 0 is
**busy**), `SmallTagIndex:1B`; then `PreviousSize:2B`, `SegmentOffset:1B`,
`UnusedBytes:1B` — the next header's `PreviousSize` is how free finds its
neighbors without a scan.

Since Windows 8 those bytes are not what they seem: `_HEAP` stores an
`Encoding` key (an entire `_HEAP_ENTRY`-shaped template) plus an
`EncodeFlagMask` gate; headers are written XOR'd with the key and decoded on
every legitimate access. LFH chunks go further — the first 4 bytes are
`heap_entry ^ subsegment_ptr ^ global_LFH_key ^ (chunk_address >> 3)`
(Windows 8, 32-bit), so a forged header is valid *only at one address*.

The why is module 02 ch04's curriculum. The classic heap exploit is a metadata
overwrite: overflow chunk A into chunk B's header, corrupt B's size or
free-list links, and make `free()`/unlink perform your write. Windows paid
that tuition in public — **safe unlinking landed in Windows XP SP2**
(`Blink->Flink == Flink->Blink`, or abort), free-list pointers were
XOR-obfuscated in Vista, and Windows 8 added full header encoding,
`RtlpLogHeapFailure` diagnostics, the `Heap == Header` check (freeing a fake
`_HEAP` — Ben Hawkes' technique — dies there), randomized LFH slot order, and
randomized virtual allocations.

## Two allocation worlds: your heap, and the kernel's handle tables

Not everything that looks heap-allocated is in your heap. The USER objects
behind `HWND`/`HMENU` — a window's `WND` record, its class, its menus — live in
the **desktop heap**, a win32k-owned heap shared per desktop and mapped
read-only into each process. An `HWND` is not a pointer: it is a **16-bit index
into the desktop's handle table plus a 16-bit generation counter** (reuse bumps
the generation, so stale handles fail rather than alias). A process-heap
overflow therefore can never forge a window object — that object is a
handle-table lookup into a different heap in a different range.

## The instrumented heap: debug flags, pageheap, and the fill patterns

`_HEAP.ForceFlags` (set by gflags or Application Verifier) carries the heap's
own debug features — tail checking, free checking, tagging. Above that sits
**pageheap**, the big hammer:

- `gflags /p /enable app.exe /full` — **full pageheap**: every allocation is
  page-aligned, user data ending exactly at a page boundary against a
  `PAGE_NOACCESS` guard page. An overflow faults *at the store instruction
  that wrote past the end*, not at free. Cost: roughly one extra committed
  page per allocation — the docs warn it can exhaust memory.
- `gflags /p /enable app.exe` — **standard pageheap**: fill patterns after
  each block, checked when the block is freed. Cheaper, later.
- `gflags /i app.exe +hpa` — the same switch in flag form (`/size` spares the
  small allocations); `gflags /p` lists what's on (`traces` = standard,
  `full traces` = full). Settings are registry-persistent and bind only at
  process start: enable, then *restart the process*.

The fill patterns are folklore every Windows debugger reads by sight:
**0xbaadf00d** = freshly heap-allocated memory nobody wrote;
**0xfeeeffee** = memory after `HeapFree`; **0xabababab** = no-man's-land
padding after a block; **0xdeadbeef** = the ancestral poison value. One layer
down, the MSVC debug CRT paints 0xcd (freshly malloc'd), 0xdd (freed), 0xfd
(guard fences). Which layer painted the bytes names the culprit allocator:
0xfeeeffee in a dump means the OS heap already freed that block
(use-after-free, module 03 ch02).

## What insiders know

- **The 17-allocation LFH activation is exact and mechanical**: the counter
  adds 0x21 per allocation, the test is `(count & 0x1F) > 0x10`, so the 17th
  net allocation of a size flips it onto the LFH; `count > 0xFF00` is the slow
  cumulative path (Windows 7/8 mechanism, carried into 10/11).
- **The encoded header means a raw dump lies to you**: an `_HEAP_ENTRY` dump
  on Windows 8+ is garbage until XOR'd with `_HEAP.Encoding` (+0x080 on a
  Win10 x64 `_HEAP`); LFH chunks also fold in the subsegment pointer, a
  global LFH key, and `chunk_address >> 3`.
- **The fill-pattern cheat sheet**: 0xbaadf00d allocated-unwritten,
  0xfeeeffee freed-by-OS-heap, 0xabababab post-block padding; 0xcd/0xdd/0xfd
  are the MSVC debug CRT (new/freed/fences).
- **One !heap command per question**: `!heap -s` (Reserv/Commit/Free; Flags
  0x00008002 = LFH, modern WinDbg adds a FrontEnd column, 2 = LFH);
  `!heap -stat -h <h>` (size histogram — the leak detector);
  `!heap -flt s <size>` (one size); `!heap -x <addr>` (which heap and front
  end own an address); `!heap -a <h>` = `-h -f -m`, everything, slow;
  `!heap -triage` — per Microsoft's docs the *only* mode that validates LFH
  corruption.
- **Full pageheap costs about a page per allocation** — restrict with `/size`
  or fall back to standard pageheap when full blows up the process; settings
  survive reboots and bind only at process start. And `!heap -stat` sizes are
  granularity units, not bytes: ×16 on x64 (×8 x86), so a "size 0x4f0" line
  means 0x4f0 × 16 bytes per block.
- **The 0x4000 (16 KB) LFH ceiling has held since Vista**, segment heap
  included; above it you talk to the back end, above ~0x7F000 (32-bit
  Win7/8) to `VirtualAlloc` itself.

## See it yourself

**No Windows box was available while writing this chapter** (this sandbox runs
Linux, no wine), so the walkthroughs below are *documented* sessions — commands,
flags, and expected output fragments, version-marked and reconstructed from tool
documentation and published dumps, not a live run. Reproduce them on any
Windows 10/11 VM with WinDbg and the Debugging Tools (free in the Windows SDK).

**1. A `!heap -s` session, step by step** (documented walkthrough):

```c
/* heaplab.c — cl /W4 /Zi heaplab.c   (Windows 10/11, x64) */
#include <windows.h>

int main(void) {
    /* warm one size class: 17+ allocations flips it onto the LFH */
    void *hot[20];
    for (int i = 0; i < 20; i++)
        hot[i] = HeapAlloc(GetProcessHeap(), 0, 0x30);   /* 48 B class */

    void *cold = HeapAlloc(GetProcessHeap(), 0, 0x1234); /* cold size */

    DebugBreak();                     /* stop here for WinDbg */
    for (int i = 0; i < 20; i++) HeapFree(GetProcessHeap(), 0, hot[i]);
    HeapFree(GetProcessHeap(), 0, cold);
    return 0;
}
```

Under WinDbg (`windbg heaplab.exe`), at the `DebugBreak` run `!heap -s`.
Expected shape (Windows 10 x64; numbers differ):

```text
0:001> !heap -s
Heap     Flags   Reserv  Commit  Virt   Free  List   UC  Ext  FrontEnd
00550000 00000002     1 MB   148k   1 MB    13k     5     1      2   (LFH)
```

`Flags` 0x00000002 is `HEAP_GROWABLE`; the high bit 0x00008002 (or FrontEnd
reading 2) is the LFH announcing itself. Then `!heap -stat -h 00550000` — the
size histogram, with the 0x30 class at ~20 blocks (sizes are granularity
units; ×16 for bytes on x64). Free everything, `DebugBreak()` again, rerun
`!heap -s`: **Commit drops, Reserve does not** — the decommit policy at work.
`!heap -x <hot[0]>` reports the owning heap and front end; the 0x1234
allocation never appears in an LFH bucket — cold sizes don't get one.

**2. gflags +hpa catching a deliberate overflow** (documented narrative): enable
`gflags /p /enable heapovf.exe /full`, restart the process, and let the program
do `char *p = malloc(16); memset(p, 0x41, 17);`. Expected: an access violation
**at the memset's last store**, one byte past the allocation — full pageheap
placed the 16 user bytes against a `PAGE_NOACCESS` guard page. Without
pageheap, that 17th byte lands in the next chunk's header, which the XOR
decoding turns into garbage that surfaces much later as a corruption failure
in `RtlFreeHeap`. `!heap -p -a <address>` prints the block details; disable
with `gflags /p /disable heapovf.exe`.

**3. UMDH for leaks**: enable the stack-trace database
(`gflags /i bloat.exe +ust`), restart, then `umdh -p:PID -f:snap1.log`
(baseline), exercise the leak, `umdh -p:PID -f:snap2.log`, and
`umdh snap1.log snap2.log -f:diff.txt`. The diff's top lines are
`+ <bytes> <count> <allocating stack>` — read top-down: that's your leak.

## How to use this knowledge

- **"Is it a leak?"** — `!heap -s` before and after a workload cycle: Reserve
  flat + Commit oscillating = healthy decommit; Commit ratcheting + one size
  class climbing in `!heap -stat -h` = a leak, and UMDH diffs name the stack.
- **"Heap corruption detected"** — the crash site is the *victim*; full
  pageheap plus a restart moves the crash to the offending write.
- **Performance**: sizes just past a ladder edge pay for the next class (a
  0x201 request on the segment-heap ladder pays 0x300) — batch structures
  under the edge (module 01 ch19's rounding arithmetic).
- **Exploit-side literacy** (module 02): sprays target 17+ allocations,
  post-2012 UAF grooms can't predict slot order, header forgery needs the
  per-heap key.

## Connections

- **Module 01 ch19 (allocator design patterns)** — LFH + back end = glibc's
  tcache + arena architecture; the bucket ladder is size-class segregation;
  17-hit activation is tcache's always-on policy replaced by a heat detector.
- **Module 02 ch04 (heap exploitation)** — the encoded header, safe unlinking
  (XP SP2), and the `Heap == Header` check are the counter-moves to that
  chapter's unlink/metadata-overwrite attacks.
- **Module 03 ch02 (the memory-bug family)** — 0xbaadf00d and 0xfeeeffee are
  the OS heap's fingerprints for the uninitialized-read and use-after-free
  members; pageheap converts late crashes into at-fault ones.
- **Module 07 ch05 (Linux memory machinery)** — slab/slub (kmalloc-8..96, the
  special kmalloc-192, /proc/slabinfo) vs the LFH's UserBlocks and per-LP
  affinity: same problem, kernel vs user space.

## Quiz

1. On 64-bit Windows 10 (classic NT heap), a program calls `malloc(3)` and,
   elsewhere, `malloc(18)`. How many bytes does each chunk consume *including
   its header*, and what is each `_HEAP_ENTRY`'s `Size` field?

2. A loop allocates the same 0x60-byte user size 20 times, freeing each block
   immediately after the next (never more than 2 live at once). A colleague
   claims the 0x60 class is "on the LFH by allocation #17." Is he right?
   Justify with the counter mechanism.

3. *Concept.* A bucket of same-size chunks has zero external fragmentation
   *within* the bucket — by argument, not luck. Give the argument, then
   explain why the heap as a whole can still fragment.

4. *Concept.* The Windows 8 LFH header encoding XORs in
   `(chunk_address >> 3)`. What class of forgery does that term kill, even for
   an attacker who knows the key and the subsegment pointer?

5. *Bug diagnosis.* An application crashes in `RtlFreeHeap` (heap corruption)
   after hours of correct running. With full pageheap enabled and the process
   restarted, it crashes immediately in a `strcpy` writing 16 bytes into a
   0x50-byte allocation. Explain (a) why the original crash was late, (b) why
   the pageheap crash is at the strcpy, (c) what the corrupted bytes did to
   the next chunk's header.

6. *Bug diagnosis.* `!heap -s` around a workload loop shows Commit climbing
   4 MB to 40 MB, never dropping; `!heap -stat -h` shows `size 0x4f0` with a
   block count growing ~50 per iteration. What is the program doing, and what
   are your next two commands?

7. *Cross-chapter.* "Windows' LFH is just glibc's tcache." Name two structural
   differences (ownership, activation) and one behavioral difference on a
   front-end miss, using module 01 ch19's vocabulary for the glibc side.

8. *Layout.* After a heap overflow, an attacker wants to overwrite a window
   object's `WNDPROC` by treating an `HWND` as a pointer into the process heap
   and writing through it. Why does that fail — where does the `WND` record
   live, and what is an `HWND`?

<details>
<summary>Answers</summary>

1. **16 bytes and 32 bytes; `Size` fields 1 and 2.** On x64 every chunk is a
   multiple of 16 (granularity units of 16), the 8-byte `_HEAP_ENTRY` included:
   3 + 8 = 11 → 16, `Size = 1`; 18 + 8 = 26 → 32, `Size = 2`.

2. **No.** The activation counter increments by 0x21 per allocation of the
   size and *decrements on each free*; the trigger `(count & 0x1F) > 0x10`
   needs 17 *net* live allocations of the class, and with every block freed
   immediately the counter never nets 17 — only the slow cumulative path
   (`count > 0xFF00`) could promote it.

3. **Argument:** every hole in a bucket is exactly the class size, hence a
   perfect fit for the next request of that class — nothing is "free but too
   small"; waste is bounded inside the chunk. **Whole heap:** the back-end
   free lists still serve cold sizes, large sizes, and everything before
   activation; those blocks coalesce and split against arbitrary neighbors,
   so holes between live blocks of *different* sizes can still strand.

4. **Replay forgery at a different address.** Knowing key and subsegment, an
   attacker could compute bytes that decode to a valid size/flags — but with
   the chunk's own address (>> 3) as an XOR input, those bytes decode correctly
   only *where the heap originally put them*; no one "magic header" can be
   planted anywhere.

5. **(a)** The 16th byte of the strcpy landed in the *next* chunk's
   `_HEAP_ENTRY` (a header sits immediately after the previous chunk's user
   data) and corrupted the XOR-encoded size/flags; it sat as garbage until that
   chunk was freed and `RtlFreeHeap`'s decode-and-validate failed — hours
   later, far from the perpetrator. **(b)** Full pageheap ends each allocation
   against a `PAGE_NOACCESS` guard page, so the overflow faults *at the store
   instruction* — the strcpy itself. **(c)** The corrupted byte changed the
   next chunk's decoded size/flags (bit 0 of `Flags` is busy).

6. **A size-class leak: ~50 blocks of 0x4f0 × 16 bytes (x64) allocated and
   never freed per iteration.** Next: UMDH snapshots around one iteration
   (`gflags /i bloat.exe +ust`, restart, `umdh -p:PID -f:snap1.log`, one loop,
   `umdh -p:PID -f:snap2.log`, `umdh snap1.log snap2.log -f:diff.txt`) to
   capture the allocating stack; and `!heap -flt s 0x4f0` to enumerate the
   blocks.

7. **Ownership:** tcache is *per-thread* (each thread's private bins); the LFH
   is *per-heap* — every heap object has its own front end, shared by threads,
   with per-logical-processor *affinity* softening contention. **Activation:**
   tcache is always on from the first malloc; the LFH activates per size class
   when the heat counter trips (17 net, or 0xFF00 cumulative). **On a miss:**
   glibc falls to the arena's unsorted/small/large bins and consolidation over
   brk/mmap; Windows falls to the back end's free lists and segments.

8. **It fails twice.** An `HWND` is not a pointer: it is a 16-bit index into
   the desktop's USER handle table plus a 16-bit generation counter, so
   "writing through" it dereferences a handle-table slot, not heap memory.
   And the `WND` record lives in the **desktop heap** — win32k-owned, shared
   per desktop, mapped read-only into the process — not in the process heap
   the overflow corrupted.

</details>
