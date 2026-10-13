# Chapter 23 — The unification: one table, every language, every payoff

> **The 30-second version.** Twenty-two chapters ago, memory was a mystery; now it
> is a design space with named axes. Every language is a point in that space,
> chosen under constraints you can reconstruct. This chapter compresses the whole
> module into one comparison table, traces the connections forward into every
> queued module, and turns the knowledge into a working toolkit: the profilers,
> the flags, the practice path, and the questions that separate people who have
> this module from people who don't.

## The four questions, one last time

Every language's memory model is its answers to four questions — you can now
interview any language in four sentences:

1. **What is a value?** (unboxed bytes / boxed header+payload / tagged word —
   Chapter 06)
2. **Who owns it?** (the programmer / the scope / the last owner / the collector /
   nobody, it's static — Chapters 08-18)
3. **Who frees it, and when?** (explicitly / at scope exit / on drop / tracing /
   refcount / never — the whole Part II story)
4. **What is promised about safety?** (nothing / compile-time aliasing guarantees /
   runtime bounds checks / managed + unsafe edges — Chapters 08, 10, 21)

## The grand table

Read a row, then a column — the table is both a reference and a map of design
trade-offs spanning sixty years:

| Language | Value repr. | Header | Ownership / reclamation | Per-int cost* | Safety floor | Where freed bytes go | Killer chapter detail |
|---|---|---|---|---|---|---|---|
| **C** | unboxed | none | manual `free` | 4 B | none (UB) | allocator free-lists | metadata at p−8 (ch08, 19) |
| **C++** | unboxed + RAII | vptr only (virtuals) | scope/dtor; smart ptrs | 4 B | none, patterns help | dtor → free-lists | make_shared one block (ch09) |
| **Rust** | unboxed, moves | none | ownership; drops at scope | 4 B | compile-time (safe code) | drop glue → allocator | niche: `Option<&T>`=8B (ch10) |
| **Go** | mostly value | none | GC (tricolor, concurrent) | 4 B (int), iface boxes | GC + bounds checks | size-class spans | escape analysis (ch11) |
| **Java** | boxed | 12 B (compressed) | GC, generational | 16 B (Integer) | GC + checks + JIT | eden→survivors→old | compressed-oops 32GB cliff (ch12) |
| **C#** | values *or* boxed | 8 B (boxed) | GC, gen + LOH | 4 B struct / 24 B boxed | GC + checks | gen0 bump → gen2/LOH | LOH 85,000B, never compacts (ch13) |
| **Python** | boxed PyObject | 16 B (refcnt+type) | refcount + cycle GC | 28 B | never crash, always boxed | pymalloc pools/arenas | 510-ish `is` artifacts (ch14) |
| **JS (V8)** | Smi / heap obj | 8 B map word | GC scavenger+mark-compact | 8 B (Smi word) | GC + checks | semispaces → old space | hidden-class transitions (ch15) |
| **Swift/ObjC** | value + ARC refs | isa (8 B, packed) | compiler-inserted refcounts | 4 B Int / 16 B String SSO | ARC + bounds checks | refcount 0 → free | weak zeroing via side tables (ch16) |
| **COBOL** | fixed record bytes | none | static, never freed | PIC bytes | compile-time layout | nothing is ever freed | COMP-3 nibbles (ch17) |
| **Fortran (classic)** | static + COMMON | none | static | 4 B | compile-time | nothing is ever freed | column-major (ch18) |
| **Lisp** | tagged cons | tag bits | GC (invented here) | 2-word cons | GC | semispace copy (1959) | half-machine GC (ch18) |
| **Haskell** | boxed thunks | header | generational GC | ~24 B/thunk | GC, purity | copying + compaction | space leaks (ch18) |
| **Erlang** | boxed per-process | header | per-process copying GC | boxed | share-nothing isolation | process death = free | >64B binaries refcounted (ch18) |
| **Zig** | unboxed | none | manual via passed allocator | 4 B | explicit + arenas | your allocator, greppable | `defer arena.deinit()` (ch18) |

\* 64-bit, order-of-magnitude, platform-per-chapter caveats apply — every chapter
states its exact numbers.

Three columns deserve a second read. **Per-int cost**: the 4→28-byte spread between
C and Python is the entire "interpreted vs compiled" performance conversation in
one number — it is representation (Chapter 06), not magic. **Where freed bytes go**:
"never" (COBOL/Fortran) through "free-lists" (C world) to "copied and compacted"
(GC world) — that axis *is* the fragmentation story of Chapter 05. **Killer detail**:
each language's most distinctive memory fact, the one that identifies a practitioner
who has actually been inside.

## The derivation web: how the models descend from the machine

The module's spine, redrawn as one lineage:

```
silicon (ch01) → hierarchy (ch02) → virtual memory (ch03)
        │                                    │
   stack for nested lifetimes (ch04)    heap for everything else (ch05)
        │                                    │
        └────────── object layout (ch06) ────┘
                         │
                assembly ground truth (ch07)
                         │
   ┌──────────┬─────────┴──────┬──────────────┬─────────────┐
   manual      RAII       ownership      GC-family     refcount-family
   (C ch08)   (C++ ch09) (Rust ch10)  (Go/Java/C#/JS) (Python/Swift)
   └──── static family: COBOL/Fortran — no heap at all (ch17, ch18)
                         │
       allocators under everything (ch19) → hardware memory model (ch20)
                         │
        failure = security surface (ch21) + platform economics (ch22)
```

Every arrow is a *forced* move, not a fashion: GC exists because tracing beats
refcount cycles (Chapter 14); compaction exists only where pointers are enumerable
(Chapters 05, 12); ownership exists because compile-time is the only time you get
safety for free (Chapter 10); mobile killing exists because swap can't (Chapter 22).
If you can redraw this figure from memory, the field is yours.

## The toolbox: see every model with its own eyes

The labs across the module, condensed into one cheat-sheet:

| Question | Tool | Chapter lab |
|---|---|---|
| Where do my bytes live? (C) | `/proc/self/maps`, `strace -e brk,mmap` | 03, 05, 08 |
| Is it a leak, fragmentation, or retention? | RSS-vs-heap-profiler method; `malloc_stats`; `MALLOC_ARENA_MAX` | 05, 19 |
| Catch UB before prod | `-fsanitize=address,undefined` in CI | 08, 21 |
| Did the compiler escape-allocate it? (Go) | `go build -gcflags='-m'` | 11 |
| Why is GC running? (JVM) | `-Xlog:gc*`, JOL, `jmap -histo` | 12 |
| Object sizes? (CLR) | `dotnet-counters`, `GC.GetGeneration` | 13 |
| Who retains this? (Python) | `tracemalloc`, `gc.get_referrers` | 14 |
| Heap graph + retainers? (JS) | DevTools heap snapshot, Retainers panel | 15 |
| Retain cycles? (Swift) | Xcode Memory Graph debugger | 16 |
| Cache behavior, false sharing | `perf stat`, `perf c2c`, `alignas(64)` | 02, 20 |
| Concurrency memory bugs | ThreadSanitizer; litmus-test harnesses | 20 |
| Exploit-readiness of a binary | `checksec`, gdb, ROP gadget scanners | 21 |
| Mobile footprint truth | `dumpsys meminfo` (PSS) / MetricKit jetsam events | 22 |
| The frame in the flesh | gdb `disas`, `info frame`, `x/8gx $rsp` | 04, 07 |

The meta-lesson: *every* memory question has an instrument. Teams that debug by
staring at code are choosing blindness in a field where the truth is one command
away.

## How to practice (the path that makes it permanent)

Reading fades; building stays. Ranked by leverage:

1. **Write a bump allocator** (15 lines — Chapter 19). Then a pool allocator. Then
   a size-class free-list. You will never again find allocators mysterious, and
   Chapters 11-15's runtime internals become *your* design decisions.
2. **Write a toy tracing GC in C or Rust**: 2 semispaces, 512 objects, one root
   array, Cheney's scan (Chapter 12's diagram is the whole algorithm). You have
   now built what JVM/V8/ART run daily — and Chapter 15's forwarding-pointer map
   word will click forever.
3. **Refcount with a cycle detector**: Python's algorithm, 100 lines (Chapter 14's
   `gc_refs` subtraction). Watch it find the cycle your manual refcounts leaked.
4. **Break each thing once**: overflow a canary, double-free into tcache, false-
   share a counter, race a data-race litmus, leak a closure over a DOM node —
   each in a sandbox, each *observed with its instrument* (Chapter 21's defensive
   labs). Security understanding at this level is just memory-model knowledge
   pointed the other way.
5. **Read one real crash per week** with gdb + the frame grammar (Chapters 04, 07).
   In a quarter you will be the person on the team who reads core dumps.

## The forward connections: why this was module 01

Every queued module is this module wearing another costume:

- **Module 02 (memory security)** — Chapter 21 is its first act: every exploit is
  a chapter 04-06 layout fact plus a chapter 19 metadata fact.
- **Module 03 (software bugs)** — the memory bug families *are* Chapters 05, 08,
  20's failure modes; the race family is Chapter 20.
- **Module 04 (decompilation & RE)** — Chapter 07's grammar, industrialized:
  prologues, vtables, sret, calling conventions *are* how binaries are read.
- **Module 05/06 (Android/iOS internals)** — Chapter 22 in full: zygote COW,
  ART's collector, Jetsam's bands, the compressor.
- **Module 07 (desktop OS internals)** — Chapter 03's page tables, Chapter 19's
  slab, three times over.
- **Module 08 (COBOL & mainframe)** — Chapter 17 deepened: storage dumps, CICS,
  AMODE, the fixed-record world.
- **Module 09 (concurrency & distributed)** — Chapter 20's happens-before scaled
  to networks; CRDTs are copy-on-write's distributed grandchild.
- **Module 10 (compilers)** — the machine that *manufactures* Chapters 06-07's
  layouts: register allocation, escape analysis, barrier insertion.
- **Module 11 (databases)** — Chapter 02's hierarchy at disk scale; Chapter 03's
  COW as MVCC; Chapter 05's page management as buffer pools.
- **Module 12 (GPU/AI)** — Chapter 01-02's economics rebuilt in HBM and SMEM;
  coalescing is cache-line arithmetic for a different wire.
- **Module 13 (networking)** — the buffers *moved*: Chapter 05's allocator
  questions, the Chapter 04 stack discipline, and Chapter 02's hierarchy
  now measured in wire time — a packet is memory in motion.
- **Module 14 (file systems)** — the heap that outlives the process: Chapter 19's
  allocators persisted to disk, Chapter 03's COW as Btrfs/ZFS snapshots.
- **Module 15 (virtualization & cloud)** — Chapter 03 sold as a product: the
  second page table, ballooning as GC for VMs, oversubscription as the
  bank run model.
- **Module 16 (browsers)** — Chapter 05-06's object graphs at page scale: the
  DOM heap, two collectors, one wrapper problem, and the 16.6 ms budget.
- **Module 17 (hardware design)** — Chapters 01-03 derived from transistors:
  the cache you measured, built in RTL; the power equation behind every
  "why is it slow" this module ever answered.
- **Module 18 (cryptography)** — the one field where Chapter 02's cache line
  is a *breach* and Chapter 05's heap over-read cost a billion dollars:
  memory discipline as the security boundary.
- **Module 19 (PL theory)** — this module's table, theorized: every language
  row is a point on the guarantees frontier, every collector a proof
  search, every rule a theorem you already use.
- **Module 20 (embedded)** — this module with the safety net removed: one
  address space, the stack pointer in a file, the memory map in silicon.
- **Module 21 (formal verification)** — the machines that prove what this
  module's languages only promise: kernels, compilers, and the borrow
  checker itself, as theorems.
- **Module 22 (ML systems)** — the unification at datacenter scale: the
  tensor as the 17th object model, sharding as the 175-billion-object
  heap, the KV cache as virtual memory for tokens.

That is the entire program thesis in one paragraph: **memory is the root layer of
computer science, and every field is a branch that forgot it has roots.**

## What insiders know (the module-sized version)

- **The interview layer**: "stack vs heap," "what does `&x` compile to," "why is a
  Python int 28 bytes," "why does freeing not shrink RSS," "what does
  `volatile`/`atomic` actually order," "why can't malloc compact" — this module
  is the answer key, with numbers.
- **The debugging layer**: crash triage (04/07/21), leak-vs-fragmentation-vs-
  retention (05/19/14/15), GC forensics (11/12/13/15/22), cache pathology (02),
  race detection (20) — the five recurring memory tickets in every production
  system.
- **The design layer**: lifetime classes (05), representation choice (06),
  allocation-rate discipline (11/12/22), layout budgeting (02/06), safety
  economics (10/21) — the decisions senior engineers are actually paid to make.
- **The one-line summaries** people remember chapters by: *locals are free because
  frames are batch; malloc costs metadata because free forgot the size; GC exists
  because cycles beat refcounts; compaction needs enumerable pointers; ownership
  is aliasing×mutation at compile time; the canary sits between your bug and the
  return address; mobile OSes kill because swap can't; COBOL never allocates so
  never leaks; everything above silicon is a cache.*

## Quiz — the final eight (cross-module synthesis)

1. Order these by memory-safety strength for *engine-level* code, and justify:
   C, Rust (safe), Java, C++ with RAII + sanitizers in CI.
2. A polyglot service leaks in production: Rust core, Python orchestration, JS
   frontend graphs, a C FFI codec. Describe your triage *sequence* across the
   modules' tools.
3. Why does the same numeric workload cost ~7x more memory in Python than C?
   Answer with representation, not adjectives.
4. `fork()` is fast, zygote app-launch is fast, JVM `-Xmx` reserves huge virtual
   space, and mmap'd files cost ~nothing resident — name the single Chapter 03
   mechanism doing all four.
5. An exploit needs: a heap-adjacent metadata fact (ch19), an address-layout fact
   (ch03), a frame-layout fact (ch04), and a representation fact (ch06). Name one
   concrete fact of each and the attack phase it serves.
6. Why do Go, Java, C#, V8, and ART all reach the *same* allocator design (bump +
   size classes + per-thread caches) despite five different GCs? Derive it from
   Chapters 05+19+12.
7. One sentence each: why does (a) Python's `gc` module exist, (b) Swift's weak
  references zero themselves while Rust's cannot, (c) COBOL forbid recursion
  (pre-2002), (d) Android kill apps where desktops swap?
8. You get one hour to teach a junior "why is our p99 slow under load." Build the
   lesson plan from this module's chapters.

<details>
<summary>Answers</summary>

1. From weakest: C (nothing enforced) < C++/RAII+ASan (patterns + tooling, still
   UB-capable where patterns lapse) < Java (memory-safe at the language level, but
   engine/JNI/FFI edges corrupt — ch21's mirage) ≈ Rust safe (compile-time
   guarantee, no GC pauses, but `unsafe` and FFI edges remain). The honest ranking
   is about *where the guarantees are enforced*: nowhere < conventions + CI < a
   runtime < the type system — and every tier still leaks (retention) even when it
   cannot corrupt.
2. Measure first (RSS/PSS trend — ch03/22), then per-runtime: Rust — ASan/heaptrack
   on the core (ch08/19); Python — tracemalloc + `gc.get_referrers` for cycles and
   closures (ch14); JS — DevTools heap snapshot, Retainers panel for detached DOM
   (ch15); C FFI — valgrind + reference-leak discipline (ch08); then the cross-cut
   suspects: per-thread caches not released (ch19), allocator arena retention
   (ch05/19), and false-positive "leaks" that are fragmentation plateaus (ch05).
   The sequence is: which tax (ch05), which runtime, which instrument.
3. Representation: every int is a boxed PyObject — 16-byte header (refcount +
   type pointer) + 28 bytes total for a value C stores in 4 (ch14 vs ch08); plus
   pointer indirection per access (ch06), plus pymalloc's 16-byte size-class
   rounding (ch14), plus per-object cache-line footprint (ch02). It is header +
   indirection + rounding + cache, each a chapter, together ~7x.
4. Demand paging / mapped pages with copy-on-write: fork copies page tables not
   pages (COW sharing); zygote's boot image is a persistent COW parent (ch22);
   `-Xmx` reserves virtual, commits lazily — RSS ≠ VSZ (ch03/12); file-backed
   mappings are resident only while hot, evictable clean pages (ch03).
5. Ch19: freed chunk's size/fd fields sit adjacent to user data (tcache fd at
   chunk+16). Ch03: ASLR randomizes the PIE/mmap bases — defeat via an info leak.
   Ch04: the saved return address is at [rbp+8], past locals and the canary.
   Ch06: length is forgotten at array decay — the overflow precondition. Phases:
   the overflow (06) corrupts metadata (19), survives mitigation layout (03/04),
   and redirects control flow. This *is* Module 02's opening lecture.
6. Bump allocation is O(1) and packs locality (ch05/02); the generational bet
   (ch12) makes most objects die young, so the nursery is bump-allocated and
   swept wholesale; per-thread caches remove the shared lock (ch05/19); size
   classes make free O(1) and bound fragmentation without compaction (ch19).
   Five GCs, one conclusion: the allocator under a GC is still Chapter 05's
   machine, and the physics picks the same winner each time.
7. (a) Refcounting cannot free cycles — the gc module's mark-from-suspects
   collector does (ch14). (b) Swift's weak slots live in a global side table,
   zeroed during dealloc before the memory dies — outside the object, so an
   outside writer can nil them; Rust's weak count lives *inside* the Rc block the
   dying object occupies — nobody can zero what is about to be freed (ch16/10).
   (c) With no per-invocation stack, recursion would share WORKING-STORAGE across
   re-entries — corrupted state; LOCAL-STORAGE (a real stack) is the 2002 opt-in
   (ch17). (d) No swap on flash + battery + shared-pressure economics: eviction
   by killing priority bands is the page-replacement policy of last resort
   (ch22).
8. The plan: (i) which tower level — perf stat, cache-misses (ch02); (ii) is it
   allocation rate driving GC/collector cadence (ch11/12/22); (iii) false sharing
   on shared counters (ch20/02); (iv) lock contention masquerading as memory
   (per-thread caches — ch19); (v) frame it as the three taxes (ch05) and confirm
   with the per-runtime instrument (the toolbox table). One hour, five
   instruments, every answer verifiable — that is the module's whole promise.

</details>
