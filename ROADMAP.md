# deep-roots roadmap

One program: go to the root layer of every field of computer science, from first
principles, and stay there until the field's insiders would nod. Modules are built in
dependency order — each one is a root of the ones after it.

**All seventeen modules are now LIVE** — 107 chapters, ~375,000 words, per-chapter quizzes,
labs, and seventeen final exams. The stack is complete from transistors to distributed
systems.

## Status legend

**LIVE** — fully written, quizzed, and published · **QUEUED** — specified, not yet
written

---

## Module 01 — memory models of programming languages · **LIVE**

The root of the roots. Every language answers four questions — *what is a value, who owns
it, who frees it, what safety is guaranteed* — and every field downstream (security,
compilation, mobile, databases, concurrency) is a consequence of those answers. Twenty-three
chapters: silicon → hierarchy → virtual memory → stack → heap → object layout → assembly →
sixteen languages → allocators → hardware memory model → memory security → mobile →
unification. Start [here](modules/01-memory-models/README.md).

## Module 02 — memory security & exploitation · **LIVE**

The dark mirror of module 01: every memory-model decision is an attack surface. Stack
smashing and canaries; heap metadata attacks (tcache poisoning, House-of-* lore);
use-after-free as the modern bug class; ROP and code reuse; the mitigation ladder
(ASLR/DEP/RELRO/CFI/PAC/MTE); Rowhammer and speculative cache side channels.
**7 chapters + final exam.** Start [here](modules/02-memory-security/README.md).

## Module 03 — software bugs: the taxonomy of failure · **LIVE**

The bug classes every senior engineer knows by scar tissue: memory corruption family,
race family (data races, TOCTOU, deadlocks, priority inversion), numeric family
(overflow, promotion traps, float money bugs, 2038), state family — and the machinery
that finds them: fuzzing, sanitizers, bisect. **5 chapters + final exam.** Start
[here](modules/03-software-bugs/README.md).

## Module 04 — decompilation & reverse engineering · **LIVE**

Reading what compilers leave behind. ELF/PE/Mach-O file formats from bytes up; the
loader (PLT/GOT, RELRO, interposition); compiler idioms as fingerprints; vtable and
RTTI reconstruction for C++; decompilers (Ghidra/IDA/Binary Ninja) — what they can and
cannot recover; anti-analysis and the unpacking playbook. **6 chapters + final exam.**
Start [here](modules/04-decompilation-re/README.md).

## Module 05 — Android internals · **LIVE**

Zygote fork and the boot image; ART's object model and concurrent copying GC; Binder
from the memory-map perspective (the 1MB buffer); lmkd and PSI-driven process kills;
the per-app sandbox (UID, SELinux, seccomp); APK packaging, dex, signatures, and A/B
OTA. **6 chapters + final exam.** Start [here](modules/05-android-internals/README.md).

## Module 06 — iOS internals · **LIVE**

XNU memory management (16K pages, VM maps, zones), Jetsam bands and the compressor;
Mach ports and message ownership; XPC and privilege separation; dyld launch closures
and the shared cache (the biggest COW artifact you own); code signing as page-in
validation; PAC. **5 chapters + final exam.** Start
[here](modules/06-ios-internals/README.md).

## Module 07 — desktop OS internals (Windows / Linux / macOS) · **LIVE**

One architecture per pair: Windows (PE, TEB/PEB, LoaderLock, the NT heap and LFH),
Linux (ELF, vDSO, slab, page cache, overcommit, the OOM killer's arithmetic), macOS
(Mach VM, purgeable memory, unified memory) — the page-table knowledge of module 01
ch03 becomes three concrete implementations, compared in one final table.
**6 chapters + final exam.** Start [here](modules/07-desktop-internals/README.md).

## Module 08 — COBOL & the mainframe world · **LIVE**

The fixed-record universe deep dive: the z-machine (PSW, storage keys, addressing
modes), WORKING-STORAGE/LOCAL-STORAGE/LINKAGE with exact semantics, PICTURE as a
byte-level type system (COMP-3 layout arithmetic by hand), CICS transaction memory
(GETMAIN, quasi-reentrancy, storage protection), and why the world's money still runs
on a memory model with no heap. **5 chapters + final exam.** Start
[here](modules/08-cobol-mainframe/README.md).

## Module 09 — concurrency & distributed memory · **LIVE**

From the hardware memory model to happens-before in distributed systems: the
language memory orderings, the lock zoo (futexes, PI, lockdep), lock-free (CAS, ABA,
hazard pointers, epochs, RCU), actors and channels as ownership transfer, and the
distributed analog — Lamport/vector clocks, quorums, Raft, CRDTs, PACELC.
**5 chapters + final exam.** Start
[here](modules/09-concurrency-distributed/README.md).

## Module 10 — compilers & language runtime internals · **LIVE**

Where the memory models of module 01 are manufactured: the pipeline and SSA, the
optimizations that change memory behavior (SROA, alias analysis, UB-as-license),
register allocation as the birthplace of the stack frame, escape analysis and write
barriers and safepoints (the compiler's GC duties), and the ABI: mangling, calling
conventions, vtable emission, layout decisions. **5 chapters + final exam.** Start
[here](modules/10-compilers-runtimes/README.md).

## Module 11 — database storage engines · **LIVE**

The disk is the new heap: the durability contract (fsync, torn writes, the SSD page
anatomy), B-trees (fanout derived, InnoDB page anatomy), LSM trees as generational GC
for disk, WAL and crash recovery (ARIES, group commit, doublewrite), MVCC as
copy-on-write at row scale. **5 chapters + final exam.** Start
[here](modules/11-db-storage-engines/README.md).

## Module 12 — GPU & AI accelerator memory · **LIVE**

The tower's new wing: the GPU memory hierarchy (registers, shared memory banks, L2,
HBM), coalescing as cache-line physics and the roofline model, allocation and the real
anatomy of "CUDA out of memory" (the caching allocator, fragmentation), unified
memory, Apple Silicon, and CXL — closing with the full register-to-RDMA tower table.
**4 chapters + final exam.** Start [here](modules/12-gpu-memory/README.md).

## Module 13 — networking internals · **LIVE**

A packet is memory in motion: encoding and the Ethernet frame, the NIC's DMA
ring (packets land in RAM before the CPU knows), sk_buff anatomy and conntrack,
TCP as two buffer machines (windows, congestion control, bufferbloat), the
userspace I/O ladder (epoll's ready list, io_uring's shared rings, zero-copy), and
QUIC/RDMA as the network becoming memory. **5 chapters + final exam.** Start
[here](modules/13-networking-internals/README.md).

## Module 14 — file systems & storage · **LIVE**

The disk is the heap that outlives the process: HDD geometry and the SSD's FTL (a
garbage-collecting log-structured allocator in firmware), the VFS trio and the page
cache (fsync decoded), ext4's journal as the WAL idea at disk scale, the copy-on-write
lineage (Btrfs, ZFS, APFS), and the special file systems that ARE the OS (procfs,
tmpfs, overlayfs) — ending with the fd-to-NAND path drawn end to end.
**5 chapters + final exam.** Start [here](modules/14-filesystems-storage/README.md).

## Module 15 — virtualization, containers & the cloud · **LIVE**

A VM is a process that thinks it's a computer: Popek-Goldberg and VT-x, EPT and the
second page table (with ballooning as a garbage collector for VMs and KSM's
side-channel cost), virtio's rings and live migration's convergence arithmetic,
containers as namespaced processes (cgroups, overlayfs, seccomp, escape economics),
and the cloud as one computer (oversubscription arithmetic, Firecracker, the
who-owns-the-bytes table). **5 chapters + final exam.** Start
[here](modules/15-virtualization-cloud/README.md).

## Module 16 — browser engine internals · **LIVE**

The operating system that won: process-per-site and the broker sandbox, the DOM
as a heap graph (Oilpan and the two-collector wrapper problem, the detached-node
leak derived), the rendering pipeline as memory traffic (the 16.6 ms budget, layout
thrashing), the page's memory (leak taxonomy, workers, WASM's guard pages), and the
security boundary (SOP, site isolation, Spectre's browser chapter).
**5 chapters + final exam.** Start [here](modules/16-browser-internals/README.md).

## Module 17 — digital hardware: from transistors to silicon · **LIVE**

The root under the root: CMOS and the power equation, flip-flops and the pipeline
(hazards, forwarding, the Pentium 4 story), RTL as the other discipline (the FIFO
ring in ~40 lines of Verilog), the cache born in RTL (the 6T cell, the direct-mapped
cache, DRAM timing), and the economics that decide why your CPU looks the way it
does (yield = e^(−D·A), chiplets, EUV). **5 chapters + final exam.** Start
[here](modules/17-hardware-design/README.md).

---

## Build order reasoning

Memory first because: security bugs *are* memory bugs (02, 03); decompilation reads
stack frames and vtables (04); mobile OSes are memory-driven schedulers (05–07); COBOL is
a memory model (08); distributed consistency is memory-model theory at scale (09);
compilers *manufacture* memory models (10); databases are disk-scaled memory hierarchies
(11); accelerators are a new hierarchy level (12). The second round went outward and
downward: networking is buffers in motion (13); file systems are allocators that
outlive the process (14); virtualization is the memory illusion sold as a product
(15); the browser is the OS everyone actually runs (16); and hardware is the physics
under every claim the program ever made (17). One root, seventeen trees.

## What could come after

The program is extensible by design; the named frontiers now: cryptography internals
(the math, the timing side channels, the implementation bugs), programming language
theory & type systems (why Rust's borrow checker is a theorem), embedded & firmware
(the world of no MMU), formal verification (the machines that prove kernels), and ML
systems/training infrastructure (the distributed memory machine at datacenter scale).
The method stays the same: first principles down, insider facts on the table, labs you
can run, quizzes with teeth.
