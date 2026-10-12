# deep-roots roadmap

One program: go to the root layer of every field of computer science, from first
principles, and stay there until the field's insiders would nod. Modules are built in
dependency order — each one is a root of the ones after it.

## Status legend

**LIVE** — fully written, quizzed, and published · **QUEUED** — specified below, not yet
written · **SEEDED** — chapter plan exists, build order after that

---

## Module 01 — memory models of programming languages · **LIVE**

The root of the roots. Every language answers four questions — *what is a value, who owns
it, who frees it, what safety is guaranteed* — and every field downstream (security,
compilation, mobile, databases, concurrency) is a consequence of those answers. Twenty-three
chapters: silicon → hierarchy → virtual memory → stack → heap → object layout → assembly →
sixteen languages → allocators → hardware memory model → memory security → mobile →
unification. Start [here](modules/01-memory-models/README.md).

## Module 02 — memory security & exploitation · QUEUED

The dark mirror of module 01: every memory-model decision is an attack surface.
Stack smashing and canaries; heap metadata attacks (tcache poisoning, House-of-* lore);
use-after-free as the modern bug class; ROP and code reuse; ASLR/DEP/RELRO/CFI/MTE as the
mitigation ladder; Rowhammer and speculative cache side channels. Prerequisite: module 01
chapters 04–06, 19, 21.

## Module 03 — software bugs: the taxonomy of failure · QUEUED

The bug classes every senior engineer knows by scar tissue: memory corruption family,
race family (data races, TOCTOU, deadlocks, livelocks), numeric family (overflow,
float traps, i-parser/i18n), state family (inconsistent snapshots, retry storms),
and the process layer (how fuzzing, sanitizers, and bisect actually find them).

## Module 04 — decompilation & reverse engineering · QUEUED

Reading what compilers leave behind. ELF/PE/Mach-O file formats from bytes up; calling
conventions recovered from prologues; vtable and RTTI reconstruction for C++; recognizing
allocator and CRT runtime signatures; decompilers (Ghidra/IDA/Binary Ninja) — what they can
and cannot recover; anti-analysis; from module 01's assembly reference model to reading
production binaries cold.

## Module 05 — Android internals · QUEUED

Zygote fork and the boot image; ART's object model and GC (from module 01 ch22); Binder
from the memory-map perspective; app process priority and lmkd; the HAL/NDK layer; APK
packaging and dex; how a patch becomes an OTA. The memory module is the prerequisite
because Android's entire design is memory-driven.

## Module 06 — iOS internals · QUEUED

XNU memory management, Jetsam bands, the VM compressor; Mach ports and message
ownership; ARC across the language/ObjC-runtime boundary; app lifecycle as memory
management; DYLD and the shared cache (the biggest COW artifact you own); code signing
as a memory trust chain.

## Module 07 — desktop OS internals (Windows / Linux / macOS) · QUEUED

One architecture per part: Windows (PE, TEB/PEB, heap manager, LFH, registry
virtualization), Linux (ELF, vdso, slab, page cache, OOM killer), macOS (Mach VM,Jetsam
overlap, shared cache, unified memory). The page table knowledge from module 01 ch03
becomes three concrete implementations.

## Module 08 — COBOL & the mainframe world · QUEUED

The fixed-record universe deep dive: WORKING-STORAGE/LOCAL-STORAGE/LINKAGE, PICTURE as a
byte-level type system, REDEFINES as unions, CICS transaction memory, AMODE 31→64,
and why the world's money still runs on a memory model with no heap. Builds on module 01
ch17.

## Module 09 — concurrency & distributed memory · QUEUED

From the hardware memory model (module 01 ch20) to happens-before in distributed systems:
locks, lock-free, epochs, RCU; CRDTs and message passing as the distributed analog of the
single-machine rules; why "memory" and "network" converge on the same consistency problems.

## Module 10 — compilers & language runtime internals · QUEUED

Where the memory models of module 01 are manufactured: AST→IR→SSA, register allocation
as the stack's origin, escape analysis (Go/Java/JIT) from the compiler's side, GC
barrier insertion, object header layout choices by compiler authors.

## Module 11 — database storage engines · QUEUED

The disk is the new heap: B-trees, LSM trees, page management, buffer pools as cache
hierarchy (module 01 ch02 at disk scale), MVCC as copy-on-write (module 01 ch03 again),
and WAL as the durable analog of the write barrier.

## Module 12 — GPU & AI accelerator memory · QUEUED

Unified vs discrete memory, HBM stacks, the CUDA memory hierarchy (global/shared/
registers/constant), memory coalescing as cache lines (ch02 again), allocator arenas in
training frameworks, and why "out of memory" on a GPU is a different physical event.

---

## Build order reasoning

Memory first because: security bugs *are* memory bugs (02, 03); decompilation reads
stack frames and vtables (04); mobile OSes are memory-driven schedulers (05–07); COBOL is
a memory model (08); distributed consistency is memory-model theory at scale (09);
compilers *manufacture* memory models (10); databases are disk-scaled memory hierarchies
(11); accelerators are a new hierarchy level (12). One root, twelve trees.
