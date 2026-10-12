# Chapter 04 — dyld & the shared cache

> **The 30-second version.** Every iOS process wants hundreds of megabytes of system
> frameworks. Linking them per launch — resolving symbols, writing fixed addresses
> into data pages — costs CPU on *every* launch and dirties a private copy of that
> data in *every* process. So Apple moved the work to OS-build time: all system
> dylibs are linked against each other once, signed, and packed into one file — the
> dyld shared cache, ~1-3 GB per architecture slice on modern iOS — mapped
> copy-on-write into every process, so code and (via chained fixups and kernel
> page-in linking) even fixed-up data pages are shared system-wide. dyld3/dyld4 add
> the launch closure, a precomputed "map these, initialize these, call this" plan,
> so a warm launch does no symbol resolution: module 01 ch03's fork-COW payoff at
> gigabyte scale.

## The arithmetic that forces a shared cache

Start from what a process *demands*: a minimal iOS app links `Foundation`, which
transitively pulls `CoreFoundation`, `libobjc`, `libdispatch`, `libsystem_c`,
`libsystem_kernel`, and so on — a real app has 100+ system dylibs before running its
own code (module 04 ch01's `LC_LOAD_DYLIB` list is the visible tip).

Run the naive loader from module 04 ch02: each dylib a separate file; per launch,
mmap each one, parse load commands, resolve undefined symbols against dependency
symbol tables — which live on disk in `__LINKEDIT` (dyld2 did exactly this: on-disk
symbol loading, every launch). Two costs:

1. **CPU, per launch.** Resolution across 100+ dylibs, re-derived identically by
   every process, every launch.
2. **RAM, per process — the killer.** Read-only `__TEXT` would be shared anyway: two
   processes mapping the same file `r-x` share physical pages (module 01 ch03). The
   problem is `__DATA`: framework globals are full of pointers to other frameworks —
   class refs, vtables. Linking writes resolved addresses *into those pages*, and a
   write to a mapped file page is a copy-on-write break: a private, dirty page. Even
   ~200 MB of dirtied system `__DATA` x 50 resident processes = 10 GB on a 6 GB
   device, under a kill-first memory policy (chapter 02).

State the constraint and the fix writes itself: *linking system dylibs against each
other has an identical result in every process, so it must happen once.* Apple does
it at **OS build time**: all system dylibs are placed at fixed offsets in one file,
fixed up against each other, and signed as a unit. At runtime the file is page-mapped
into every process — pre-linked, pre-signed, page-mapped; nothing per-process remains
except the mapping.

## One file, three payloads: what is inside the cache

The cache is not "a folder of frameworks"; it is one (or a few) file(s):

- **Text pages** — every system framework's `__TEXT`, merged, 16 KB-aligned (module
  06 ch01), inter-dylib calls pre-bound.
- **Data pages** — every framework's `__DATA`, pointers stored not as final values
  but as *chained fixup entries* (below), so they fix lazily.
- **Closure metadata** — pre-built launch closures for system apps, cache-wide symbol
  information, and one code signature covering every page (module 06 ch05).

Sizing: **iOS 17, arm64: ~1-3 GB per architecture slice** (one slice per device;
varies by device class and OS version). The macOS 14 arm64e cache totals ~1.5 GB
across a main file plus numbered `.subcache` files under
`/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/` (macOS 13+; iOS 16+:
`/System/Library/Caches/dyld/`). Rosetta Macs add an x86_64 cache.

The cache is built *unslid* — every encoded pointer is relative to the cache base.
At boot it is mapped at one slide, **the same in every process** (re-randomized per
boot, iOS 17). Hold that invariant; it makes this diagram work:

```text
        ONE file on disk (iOS 17, arm64: ~1-3 GB per architecture slice)
        /System/Library/Caches/dyld/dyld_shared_cache_arm64  (+ subcaches)
        +----------------+----------------+--------------------------+
        | __TEXT         | __DATA         | closures for system apps |
        | all framework  | all framework  | + cache symbol info +    |
        | code, merged & | globals,       | per-page signature       |
        | pre-linked     | stored AS      | hashes: ONE signature    |
        |                | CHAIN ENTRIES  | covers the entire file   |
        +-------+--------+-------+--------+--------------------------+
                |                |
      map r-x,  |                |  a process FAULTS on a data page:
      no writes |                |  kernel page-in linking: apply the
                |                |  fixup chain, insert the FIXED page
                v                v  into the cache's VM object
        +---------------------------------------+
        |           physical pages (RAM)        |
        | text: read-only, ONE copy system-wide |
        | fixed data pages: ONE copy each,      |  ... until a process
        | shared by every process ...           |  WRITES a global ->
        +---+-------------+-------------+-------+  COW: private copy
            |             |             |
            v             v             v
        process A     process B     process C ...
        (1 PTE per 16 KB mapped per process; zero RAM for untouched
         pages; zero private pages for read-only users)
```

Module 01 ch03's fork-COW picture at ~500,000x scale — plus one upgrade fork doesn't
have: *fixed* data pages are shared too, not just clean ones.

## From symbol-hunting to a plan on disk: dyld2 to dyld3/dyld4

dyld is the userspace dynamic loader (`/usr/lib/dyld`, named by `LC_LOAD_DYLINKER`).
Three generations, three answers to "when is the launch plan computed?"

**dyld2** (macOS 10.6–12 / iOS 15 lineage): *at every launch* — parse each dylib's
load commands, build the dependency graph, resolve every undefined symbol from
on-disk symbol tables, rebase, run initializers. Only the file bytes are cached (OS
page cache); the plan is recomputed per process, per launch.

**dyld3** (iOS 12, 2018; system apps first): *once, off the launch path*. Compute
the whole plan — which images (UUIDs), which cache ranges to map where, initializer
order, pre-resolved symbol addresses — and **serialize it**: the *launch closure*, a
flat buffer that is literally "map these, initialize these, call this." OS apps'
closures are pre-built into the cache; third-party apps get one computed at install
or first launch and cached. At launch dyld *validates* (UUIDs — plus mtimes on macOS
— must match) and *executes*: zero symbol resolution.

**dyld4** (iOS 16 / macOS 13, 2022): one dyld, both engines — cached closure when it
validates, classic engine as fallback, plus just-in-time closures for what dyld3
punted on, like `dlopen`.

The **closure-invalidation rule**: the closure is keyed to an exact image set. An OS
update changes the cache's UUID; re-signing or replacing an embedded framework
changes its UUID/mtime — either discards the closure, and the next launch pays the
build cost. `dlopen` is invalidation *by construction*: the plan covers a fixed image
set, so loading anything outside it forces the slow path right then (dyld4 builds a
just-in-time closure for it). An app that dlopens the same plugin every startup
never gets the fast path for it — file that away for the quiz.

## Chained fixups: rebasing becomes lazy

The old encoding, **rebase/bind opcodes** (`LC_DYLD_INFO`): a *stream* in
`__LINKEDIT` — "enter page P, apply N fixups of type T at these offsets" — that dyld
interprets, writing fixed pointers into `__DATA`. Two flaws: bytes in `__LINKEDIT`
per fixup, and the stream is *sequential* — page 47 cannot be fixed without walking
from the start. Fixing touches every page with a pointer, up front.

Chained fixups (`LC_DYLD_CHAINED_FIXUPS`; shared caches since the dyld3 era, app
binaries via the Xcode 13 linker's `-fixup_chains`, 2021) invert the storage: **the
fixup record lives in the data page, in the pointer slot itself.** In the unslid
image a future pointer's high bits are unused; they carry the chain — bind/rebase
flag, offset of the *next* fixup in the page, and the target. The fixer repairs one
slot, hops to `next`, never leaving the page. Exact arm64 app layout, format
`DYLD_CHAINED_PTR_64` (dyld's `mach-o/fixup-chains.h`; bit 63 = MSB):

```text
   one 8-byte slot in __DATA, BEFORE fixing -- a rebase entry:

    bit:   63   62.............51 50......44 43.....36 35.................0
         +----+-----------------+----------+-----------+-------------------+
         |bind|  next, 12 bits  | reserved | high8, 8  |  target, 36 bits  |
         | =0 |  x 4 = byte off | 7 zeros  | bits of   |  link-time address|
         |    |  to NEXT fixup  |          | the final |  of the target    |
         |    |  in this page   |          | pointer   |  (image < 64 GB)  |
         +----+-----------------+----------+-----------+-------------------+

   bind=1 re-reads the slot: ordinal (24 bits, index into the imports
   table) | addend (8) | next (12) -- symbol names appear once each,
   in the imports table, not once per fixup. After the fixer runs, these
   same 8 bytes ARE the real, runnable 64-bit pointer.
```

The arithmetic is not an accident: 12 bits x 4-byte stride = 16,384 bytes — *exactly*
one Apple Silicon page, so a chain can never cross one; pages with no fixups are
marked `0xFFFF` in `page_start[]` and never touched.

The payoff: fixup data that lives *in the page being fixed* means a fixer holding
only that faulted page can do the work — impossible with a global stream. So XNU
grew **page-in linking**: dyld hands the kernel the cache's fixup-start tables (dyld's
header comment on `dyld_chained_starts_in_segment`: "passed down to the kernel for
page-in linking"), and when a process faults on a cache data page, the kernel applies
the chain and inserts the fixed page into the cache's VM object — which backs every
process. The slide is identical everywhere, so one fixed page is valid for all of
them: the system pays per page *once*, not per process, and untouched pages never
materialize.

The insider's compression: **rebase is now lazy**. dyld2 paid a full rebase pass at
launch, eagerly, per process; the modern world pays per fault, system-wide, shared.
App binaries get the same encoding (dyld walks their chains at launch, dirtying only
pages with fixups), and `LC_DYLD_EXPORTS_TRIE` replaces the classic symbol table — a
chained-fixups binary needs no `LC_SYMTAB` for exports, which is why stripped modern
binaries look "empty" to old tools.

## One launch, end to end, with a budget

Tap an icon; the chain (iOS 17; order-of-magnitude timings, mid-range iPhone, warm
cache — your numbers differ, the *shape* won't):

| step | what happens | rough cost |
|------|--------------|-----------|
| spawn | launchd `posix_spawn`s the app — no fork from a warm parent; iOS processes start cold, unlike module 05's Zygote | 1-5 ms |
| exec | kernel maps the binary, validates signature + entitlements (ch05), jumps to `LC_LOAD_DYLINKER` | a few ms |
| closure | dyld finds the cached closure, validates UUIDs/mtimes | < 1 ms |
| map | cache dylibs: PTE manipulation only — resident pages cost no I/O | a few ms |
| fixups | app binary's chains walked (only fixup pages dirty); cache data pages fix *on fault*, later | 1-10 ms warm |
| init | libSystem init, then per image bottom-up: ObjC `+load`, then `mod_init_func` / C++ ctors | 20-150+ ms — yours |
| main | `main` → `UIApplicationMain` → first frame | app-specific |

Two things to internalize: pre-main is dominated by *your* initializers, not dyld's
mechanics — hence Apple's advice to count dylibs and audit `+load`. And first
launches are a different regime: after install, update, or OS update, the closure is
rebuilt (hundreds of ms) and page-ins are cold — "slow only the first time" is this
table paying its one-time costs.

`+load` deserves precision, because "before main" bugs live here. Per image:
dependencies first, then — for each image — the ObjC runtime's registered callbacks
fire (`+load`, via dyld's notification hooks), then that image's own `mod_init_func`
constructors, all before `main`. A crash inside `+load` has dyld frames on the stack,
not `main` — and the run order was written in the closure before your process existed.

## What insiders know

- **Cache sizes**: iOS 17, arm64: ~1-3 GB per slice, built per device class; macOS 14
  arm64e: ~1.5 GB total across the main cache plus numbered `.subcache` files in
  `/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/` (macOS 13+). Rosetta
  Macs add an x86_64 cache.
- **The closure-invalidation rule**: closures are keyed to an exact image set (UUIDs;
  mtimes on macOS). OS update, re-sign, or a swapped embedded framework discards one.
  On macOS, *any* `DYLD_*` environment variable also disables closure use for that
  process — `DYLD_PRINT_STATISTICS=1` measures the fallback path, not your users' path.
- **Chained fixups vs rebase opcodes**: opcodes are a sequential `__LINKEDIT` stream,
  fixable only from the start; chains store the metadata in the data page itself —
  `__LINKEDIT` shrinks to a fixups header plus a small imports table, and any single
  page is fixable in isolation, the property that made kernel page-in linking
  possible.
- **Where `+load` runs**: inside dyld's initializer phase, per image, dependency
  (bottom-up) order, immediately before that image's `mod_init_func`, all before
  `main`. It is not "early main" — it is late dyld.
- **"Rebase is now lazy"**: with the starts tables handed to the kernel, cache data
  pages are fixed at page-in and the fixed page is *shared across all processes*
  (uniform per-boot slide). A cache data page is paid once system-wide on first fault
  — never per process, and never for pages nobody reads.
- **The RE consequence**: a dumped app binary contains no framework code — UIKit,
  Foundation, even `objc_msgSend` live only in the cache. `class-dump` on a dumped app
  shows your classes and stops; extract cache dylibs with
  `dyld_shared_cache_util -extract`, and resolve app imports as *ordinals*, not
  symbol names.

## See it yourself

On a Mac (macOS 13+; fragments below are abridged — shapes, not bytes — from a macOS
14 / arm64e machine). **No Mac? Treat this as a documented walkthrough**: commands
and output shapes are as you would see them.

```bash
# 1. The artifact: GB-scale, split into subcaches.
ls -lh /System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/
#   dyld_shared_cache_arm64e             ~700M-1G
#   dyld_shared_cache_arm64e.1.subcache  ~100-300M  (x several)
#   -> ~1.5 GB total.

# 2. Your own binary is already chained-fixup-encoded.
printf '#include <stdio.h>\nint main(){puts("hi");}\n' > /tmp/h.c
clang /tmp/h.c -o /tmp/h
otool -l /tmp/h | grep -E 'LC_DYLD|LC_LOAD_DYL' | head
#   cmd LC_LOAD_DYLINKER          (the handoff to /usr/lib/dyld)
#   cmd LC_DYLD_CHAINED_FIXUPS    (the chain tables -- replaces rebase opcodes)
#   cmd LC_DYLD_EXPORTS_TRIE      (exports; no classic symbol table needed)

# 3. The cache mapped into live processes -- same address in both.
sleep 60 &  P1=$!; sleep 60 &  P2=$!
vmmap $P1 | grep -i 'dyld shared' | head -4
vmmap $P2 | grep -i 'dyld shared' | head -4
#   __TEXT   00007fff80000000-00007fffbc800000 r--/r-x  900M+
#   __DATA   00007fffbc800000-...   (several hundred MB)
#   -> SAME start address in both: the per-boot uniform slide.
#   -> mapped, not loaded; most of it never faults.

# 4. First vs second launch: the one-time costs are visible.
for i in 1 2 3; do /usr/bin/time /tmp/h > /dev/null; done
#   first: ~30-80 ms (closure + cold page-ins for a fresh binary)
#   rest:  ~10-20 ms (validated closure, warm pages)
#   The gap grows with dylib count, and is huge right after an OS
#   update, when every closure in the system is stale at once.
```

## How to use this knowledge

- **Launch-time engineering.** Audit your `LC_LOAD_DYLIB` count (each dylib adds
  closure, mapping, and initializer work) and move code out of `+load`. Measure
  pre-main with `os_signpost` or XCTest launch metrics — *not*
  `DYLD_PRINT_STATISTICS`, which drops you onto the closure-less fallback path.
- **Diagnosing "slow only on first launch"** after install or update: closure rebuild
  + cold page-ins — the one-time column of the budget table, not a code regression.
- **RE on iOS binaries.** A dumped app is a stub plus ordinals: resolve imports
  against the *cache*, extract cache dylibs with `dyld_shared_cache_util -extract`
  before class-dumping, and expect exports-trie-only binaries to defeat tools that
  require `LC_SYMTAB`.
- **Memory triage.** `vmmap` showing ~2 GB of "dyld shared cache" in a process is not
  a leak: those pages are shared and clean. What predicts a Jetsam kill (chapter 02)
  is dirty + compressed memory — `phys_footprint` — not mapped size. And when an
  interviewer asks how sixty processes share gigabytes of frameworks: pre-linked
  once into a signed file, mapped COW, kernel-fixed data pages under one uniform
  slide, precomputed closures — per-process cost collapses to page tables.

## Connections

- **Module 01 ch03 (virtual memory/COW)** — that chapter's payoff at 10^9-byte scale:
  file-backed mapping, COW, and "the fault handler is where policy lives" (here the
  policy is *apply the fixup chain*).
- **Module 04 ch01-02 (formats, loader)** — Mach-O load commands and the userspace
  loader meet their biggest artifact: `LC_DYLD_CHAINED_FIXUPS` is ch02's relocation
  story re-encoded; the closure is ch02's "loader plan" made into a file.
- **Module 05 ch01 (Zygote)** — the warm-start twins: Android amortizes into a live
  process (fork + COW of already-dirtied pages); iOS amortizes into a file
  (OS-build-time pre-linking, pages clean until a real write). Same problem,
  opposite half of the fork/COW machinery.
- **Module 06 ch02 (Jetsam)** — cache pages are clean and file-backed: memory the
  system reclaims for free, unlike the dirty pages that get you killed.
- **Module 06 ch05 (signing & PAC)** — "pre-signed" is load-bearing: one signature
  for the whole cache lets the kernel validate at page-in — demand paging fused with
  the trust chain.

## Quiz

1. Per-launch linking of system frameworks fails for two *independent* reasons. Name
   both, with the arithmetic that makes each fatal on a 6 GB phone.
2. An arm64 binary (format `DYLD_CHAINED_PTR_64`) has a rebase slot encoded as
   `0x002800000001B510`. Decode: bind flag, target, and where the next fixup sits
   relative to this slot.
3. Eighty processes each fault on the *same* three cache data pages, read-only. How
   many fixed physical pages exist system-wide? Then one process writes a global on
   the second page — total now?
4. Why must the shared-cache slide be identical in every process for kernel page-in
   linking to save RAM? What would per-process slides cost? (Module 01 ch03.)
5. A developer profiles launch on macOS with `env DYLD_PRINT_STATISTICS=1` and files
   a bug: "dyld takes 180 ms." Production `os_signpost` shows ~40 ms pre-main. What is
   wrong with the measurement?
6. An app `dlopen`s a bundled analytics plugin during startup, every launch. Why will
   that plugin never launch via the pre-built closure path, and what are two fixes?
7. An analyst dumps an iOS app from memory, runs `strings` and `class-dump` on it, and
   concludes "thin shell — all logic must be server-side." What did they misread?
8. Compare Android's Zygote (module 05 ch01) with the shared cache: which cost is
   amortized into a *process* and which into a *file*, which design shares *dirtied*
   data pages, and why does iOS spawn app processes cold instead of forking a warm
   one?

<details>
<summary>Answers</summary>

1. **CPU**: symbol resolution across 100+ dylibs, recomputed per process per launch.
   **RAM**: fixing pointers writes into `__DATA`; each write is a COW break — private
   dirty pages. Even 200 MB x 50 processes = 10 GB on a 6 GB device. Text sharing
   was never the problem; per-process `__DATA` is.
2. Bit 63 = 0 → **rebase**. `target` = value & 0xF_FFFF_FFFF (36 bits) = 0x0001B510.
   `next` = (value >> 51) & 0xFFF = 5; stride 4 → the next entry sits **20 bytes
   (0x14) after this slot**, in the same 16 KiB page. `high8` = (value >> 36) & 0xFF
   = 0. After fixing, the same 8 bytes hold the real pointer (target + slide, top
   bits from `high8`).
3. **3** — the kernel fixes each page once at page-in and inserts it into the cache's
   VM object; the uniform slide makes one copy valid for all 80. After the write:
   **4** — COW gives the writer a private page 2; the other 79 still share the fixed
   original.
4. The kernel shares a fixed page through the VM object only if its contents are valid
   for *every* mapper. Per-process slides mean each process needs its own private
   fixed copy — question 1's arithmetic again. The uniform slide turns a would-be
   private COW write into a shared, pay-once operation (module 01 ch03's COW
   machinery, pointed at reads).
5. On macOS, any `DYLD_*` variable disables closure use for that process — the
   instrument changed the experiment. The 180 ms is the fallback doing full symbol
   resolution, a path users never take; 40 ms is the closure path. Close it as a
   measurement artifact.
6. The closure is a plan for a fixed image set computed before the process ran; the
   plugin is not in it, so dyld resolves it via the slow path *every* launch. Fixes:
   link it as an embedded framework so it enters the closure, or defer `dlopen`
   until after first frame.
7. They forgot where framework code lives: on iOS, UIKit/Foundation/libSystem exist
   only in the cache, and the app binary holds the app's code plus ordinal imports.
   A thin dump is the *normal* shape of an iOS app; the "missing" logic is in the
   cache (extract with `dyld_shared_cache_util -extract`), not necessarily a server.
8. Zygote amortizes into a **live process**: apps fork from it and inherit warmed,
   *already-dirtied* pages via COW — the sharing includes the dirt. The cache
   amortizes into a **file**: pre-linked at OS build, mapped COW, data pages fixed by
   the kernel and shared only while clean. iOS spawns cold (launchd +
   `posix_spawn`) and relies on file-based amortization, keeping every process
   independently killable (chapter 02's model).

</details>
