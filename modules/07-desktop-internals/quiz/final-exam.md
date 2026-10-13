# Module 07 final exam — desktop OS internals

Twelve questions across all six chapters. Closed book. Every answer is worked,
not just stated — the explanations are the point. Scoring: 11+ = you own this
module; 8-10 = solid, revisit the flagged chapters; <8 = the chapter quizzes
below each chapter are the study path.

**Part I — the frame and Windows (chapters 01-03)**

1. POSIX leaks by default; Windows locks down by default. Name the mechanism
   behind each slogan — what crosses `fork()` uninvited, what `CreateProcess`
   refuses to carry — the bug class each produces, and the one-line fix.

2. A Windows box has 8 GB RAM and an 8 GB pagefile. A program reserves 64 GB,
   commits 12 GB (both succeed), touches 1 GB, runs for days — then commits
   8 GB more and the call fails. Which resource ran out, at what limit, with
   which error, and why with ~7 GB of RAM free and a 1 GB working set? Where
   does death move on Linux under default overcommit?

3. A PE file carries this section table (`SectionAlignment` 0x1000,
   `FileAlignment` 0x200, preferred base 0x140000000):

    ```text
     section   VirtSize  VirtAddr  RawSize   RawPtr
     .text     0x1A2B0   0x1000    0x1A400   0x400
     .rdata    0x09C10   0x1C000   0x09E00   0x1A800
     .data     0x01530   0x26000   0x1200    0x24600
     .rsrc     0x00890   0x28000   0xA00     0x25800
     .reloc    0x00B40   0x29000   0xC00     0x26200
    ```

   (a) A crash report names the faulting instruction's RVA: 0x1A5F0. Its file
   offset — and its VA if ASLR loads the image at 0x7FF600000000?
   (b) A `static char g_scratch[0x120]` sits at RVA 0x27400. How many of its
   bytes come from the file, and where do the rest come from?

4. A service EXE loads plugin.dll, whose `DllMain(PROCESS_ATTACH)` creates a
   worker thread, then blocks on the event the worker signals after parsing
   its config. The service hangs at startup, 0% CPU; a dump shows one thread
   in `DllMain` and a second that never reached its start address. Explain
   the exact circular wait — naming the lock — and give the fix in one line.

5. A crash lands in `RtlFreeHeap` (heap corruption) after hours. You dump
   the 8 bytes at `p − 8` and read `Size = 0x35E7`, `Flags = 0xBB` —
   nonsense. Is the heap that corrupted? What are you actually reading, what
   decodes it, and which attack class made Microsoft armor it up?

**Part II — Linux (chapters 04-05)**

6. Your `LD_PRELOAD` malloc counter prints `malloc called 2 times` for a
   program doing one `malloc(100)` and one `printf`. (a) What was the second
   allocation, and what does its capture prove about how `libc.so.6` was
   linked? (b) Predict the count if the program first calls
   `setvbuf(stdout, NULL, _IONBF, 0)`. (c) Predict what happens on a
   setuid-root build of the same program.

7. Five processes map the same 800 MB read-only file and touch every page;
   each also holds 150 MB of touched private anonymous heap; nothing is
   swapped. A sixth holds 900 MB of private touched heap, nothing shared.
   Give each process's RSS, PSS, and `Private_Dirty`; the machine's true
   footprint; and rank all six by the OOM killer's number —
   including the surprise.

8. A `[heap]` VMA reads `Size: 264 MB, Rss: 260 MB, Private_Dirty: 260 MB,
   Anonymous: 260 MB`. The program `free()`s 250 MB and idles; minutes later,
   on a box with no swap, the same VMA reads `Size: 264 MB, Rss: 12 MB`.
   Where did 248 MB go — which call returned them, why did `Size` not move,
   and what does the next touch cost?

9. From an OOM report:

    ```text
    [ pid  ]  uid  tgid total_vm    rss pgtables_bytes swapents oom_score_adj name
    [  812] 1000   812  918156  883172       7135232        0         -1000 postgres
    [10551] 1001 10551   20480   16384        131072        0             0 worker
    [  233]    0   233   40960   12288        98304         0             0 node_exporter
    ```

   Decode the units (one column lies), compute each process's badness
   points, name the victim — and why the largest RSS did not die, and why
   `swapents` is zero.

**Part III — macOS and the three kernels (chapter 06, cross-chapter)**

10. On a 16 GB Mac, `footprint` reports `Phys footprint: 1.2 GB` and
    `Rss: 6.4 GB` for one process; Rss summed over all processes is ~40 GB.
    Where are the missing 5.2 GB, why do the sums exceed RAM, which number
    does memorystatus consult — and why does RSS under-report worst
    exactly when pressure is highest?

11. Same physics, three OOM dialects. For each kernel: name the number that
    predicts memory death, the moment death arrives, and the failure the
    programmer sees. Which two kernels can kill a process whose every
    `malloc` succeeded — and which one refuses a promise instead of killing?

12. Interposition folklore, three loaders. (a) Why does planting
    `kernel32.dll` beside a victim EXE do nothing, while planting
    `version.dll` there executes code? (b) What is the Linux analog, where in
    the lookup order does it sit, and which two mechanisms switch it off?
    (c) What is the macOS analog — why does vendoring a runtime the shared
    cache already provides break `dynamic_cast`? Close with one line per OS:
    the mitigation rung each kernel bets on.

<details>
<summary>Answers</summary>

**Part I**

1. **POSIX:** `fork()` copies the whole fd pointer array — each fd points at
   a shared open-file description — so every fd crosses uninvited; opt-out
   is `O_CLOEXEC` at `execve`. Bug class: leakage — the exec'd child
   keeps the listening socket and the port cannot rebind (`EADDRINUSE`).
   Fix: `fcntl(fd, F_SETFD, FD_CLOEXEC)`. **Windows:** `CreateProcess`
   inherits almost nothing: `bInheritHandles` defaults FALSE, and even TRUE
   passes only handles marked `bInheritHandle`. Bug class: lockdown — a
   handle value handed to the child indexes the *parent's* table
   (`ERROR_INVALID_HANDLE`). Fix: mark it inheritable, or `DuplicateHandle` —
   which also narrows the granted-access mask, which POSIX cannot (ch01).

2. **The commit ledger, not RAM.** The limit is RAM + pagefiles = 16 GB;
   `MEM_COMMIT` charges it at promise time, untouched pages or not. 1 GB
   resident and ~7 GB free are irrelevant — 20 GB of promises exceeds 16 GB,
   so `VirtualAlloc` returns NULL, `ERROR_COMMITMENT_LIMIT`: demand paging
   inverted into an accounting rule (ch01). Watch Committed Bytes vs Commit
   Limit, never the working set. **Linux:** every mapping succeeds (mode 0
   refuses only obvious overcommits; `Committed_AS` is advisory); death moves
   to *first touch* — the fault finds nothing reclaimable, badness is scored,
   SIGKILL mid-computation (ch05).

3. (a) `0x1000 <= 0x1A5F0 < 0x1B400` → `.text` (extent = VirtAddr +
   max(VirtSize, RawSize) = 0x1000 + 0x1A400); offset =
   `0x400 + (0x1A5F0 − 0x1000)` = **0x199F0**, file-backed (ends 0x1A800).
   VA = **0x7FF60001A5F0**; at the preferred base
   0x14001A5F0 — the RVA is the invariant (ch02). (b) **Zero.** `.data`'s
   raw bytes cover [0x26000, 0x27200); 0x27400 is past them but inside the
   virtual extent (ends 0x27530; 0x27400 + 0x120 fits) — the loader
   zero-fills: Windows has no `.bss`, the bss being `.data`'s zero tail,
   visible only as VirtualSize > SizeOfRawData (ch02).

4. **A wait-for cycle through LoaderLock.** The `DllMain` thread holds
   `ntdll!LdrpLoaderLock` and waits on the worker's event. The worker cannot
   reach its start address: thread startup must deliver `DLL_THREAD_ATTACH`
   to every module that skipped `DisableThreadLibraryCalls` — under
   LoaderLock, held by the waiting thread. Holder waits on waiter, waiter on
   the lock: both parked, hence 0% CPU. Fix: never wait in `DllMain` —
   create the thread and return. "DllMain should be about four
   instructions" (ch02).

5. **Encoded metadata, not corruption.** Since Windows 8, `_HEAP_ENTRY`
   bytes are XOR'd with the per-heap `Encoding` template (+0x080 in a Win10
   x64 `_HEAP`); LFH chunks also fold in the subsegment pointer, a global LFH
   key, and `chunk_address >> 3` — raw dumps are garbage by design. Decode
   first; corruption is the *decoded* values failing validation
   (`RtlpLogHeapFailure`). The armor answers the metadata-overwrite attack —
   overflow into the next header, forge size or free-list links, make
   `free()`'s unlink perform your write: safe unlinking (XP SP2), XOR'd
   pointers (Vista), header encoding (Win8); the address term kills replay
   forgery elsewhere even given the keys (ch03).

**Part II**

6. (a) `printf`'s 4,096-byte stdout buffer, allocated lazily on first write
   by libc-internal code — *through the shim*: glibc is deliberately not
   built `-Bsymbolic`, so its internal calls resolve through the global scope
   (ch04). (b) **1** —
   unbuffered stdout never allocates it. (c) Setuid sets `AT_SECURE`;
   secure mode ignores `LD_PRELOAD` — the shim is never mapped, no counter
   (ch04).

7. Each of the five: **RSS 950 MB** (800 shared counted fully + 150
   private), **PSS 310 MB** (800/5 + 150), **Private_Dirty 150 MB** (the
   file is Shared_Clean). The sixth: all three **900 MB**. True footprint =
   800 + 5 × 150 + 900 = **2,450 MB** — the PSS sum reproduces it
   (5 × 310 + 900); naive RSS-summing claims 5,650 MB. The killer ranks on
   RSS + swap: **950 each against 900** — the surprise: all five mappers
   outrank the hog; RSS double-counts their shared pages. No killer
   reads PSS; it is the capacity number (ch01, ch05).

8. **glibc returned them to the kernel.** The `free()`s released 250 MB into
   the arena; past the trim threshold glibc handed the tail back — `Size`
   unchanged, so via `MADV_DONTNEED`, not `brk` shrink. No swap, data
   logically dead: the kernel discards the anonymous pages to the free pool.
   The next touch faults a fresh zero page — demand-paging cost, and zeros,
   not old contents. Pages stop costing when *returned*, not when malloc
   forgets them (ch05). (On a file mapping, that drop is page-cache reclaim;
   the next touch re-faults from disk.)

9. **The units lie:** every memory column is 4 kB pages except
   `pgtables_bytes` (bytes) — postgres rss = 883,172 pages ≈ 3.4 GB, pgtables
   = 7,135,232 B = 1,742 pages. Points = rss + swapents + pgtables/4096:
   **postgres 884,914; worker 16,416; node_exporter 12,312**; score =
   points × 1000 / total pages + `oom_score_adj`. postgres's −1000 clamps it
   to zero — the infrastructure shield. Victim: **worker** — 1/54th of
   postgres's badness; read `oom_score_adj` before blaming the kernel.
   `swapents` is zero because SwapTotal is 0. `total_vm` is a decoy — the
   modern formula ignores it (ch05).

**Part III**

10. **The shared dyld cache.** The 5.2 GB are the cache's resident pages,
    counted in the RSS of every process that links them — question 7's
    double-counting, with no PSS exposed to correct it; hence ~40 GB summed
    on 16 GB. memorystatus consults **phys_footprint**: dirty pages +
    compressed copies of evacuated pages + purgeable-nonvolatile + IOKit +
    page tables. That ledger is why RSS under-reports worst under pressure:
    compression removes pages from RSS but keeps them in footprint — RSS
    300 MB / footprint 500 MB = ~200 MB compressed and still billing (ch06,
    ch01).

11. **Windows:** commit charge vs commit limit (RAM + pagefiles); death at
    *promise time* — `VirtualAlloc` itself fails `ERROR_COMMITMENT_LIMIT`;
    no killer exists (ch01). **Linux:** badness =
    RSS + swap + page-table pages, nudged by `oom_score_adj`; death at first
    touch, SIGKILL at the faulting allocation; the cgroup variant swaps the
    domain for `memory.max` (ch05). **macOS:** phys_footprint against the
    memorystatus limits, only after the ladder — drop clean, drop purgeable,
    compress, swap — is exhausted; then the dialog and SIGKILL, and malloc
    never returned NULL (ch06). Linux and macOS kill processes whose every
    malloc succeeded; Windows refuses the promise instead of killing.

12. (a) `kernel32.dll` is a KnownDLL: resolved by base name to the
    pre-created section in `\KnownDlls` — disk never consulted, the planted
    copy inert; `version.dll` is not, so it resolves through the search
    order, whose *first* directory is the EXE's own folder (ch02, module 02
    ch06). (b) `LD_PRELOAD` objects sit at position 1 of the symbol scope,
    ahead of the main executable; switched off by secure execution
    (setuid → `AT_SECURE`) and by `-Bsymbolic`, which binds the library's
    internal references at link time (ch04). (c) The system runtime is
    pre-linked into the shared cache while the vendored copy loads
    separately — two `typeinfo`s for the "same" class, so `dynamic_cast`
    fails across the boundary: an ODR violation the ABI cannot see. Linux
    resolves both into one namespace (ch06). **Mitigation rungs:** Windows —
    the hypervisor (VBS/HVCI); Linux — policy plus randomness (SELinux,
    KASLR); macOS — hardware binding (SIP, PAC) (ch06).

</details>
