# deep-roots roadmap

One program: go to the root layer of every field of computer science, from first
principles, and stay there until the field's insiders would nod. Modules are built in
dependency order — each one is a root of the ones after it.

**All twenty-two modules are now LIVE** — 132 chapters, ~476,000 words, per-chapter
quizzes, labs, and twenty-two final exams — connected by 1,100+ explicit cross-module
references (see the connections map on the site). The stack is complete from
transistors to the datacenter, and the ring is closed: the theory that explains the
languages, the proofs behind the promises, and the machines that train the models.

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
unification. [Start here](modules/01-memory-models/README.md).

## Module 02 — memory security & exploitation · **LIVE**

The dark mirror of module 01: every memory-model decision is an attack surface. Stack
smashing and canaries; heap metadata attacks (tcache poisoning, House-of-* lore);
use-after-free as the modern bug class; ROP and code reuse; the mitigation ladder
(ASLR/DEP/RELRO/CFI/PAC/MTE); Rowhammer and speculative cache side channels.
**7 chapters + final exam.** [Start here](modules/02-memory-security/README.md).

## Module 03 — software bugs: the taxonomy of failure · **LIVE**

The bug classes every senior engineer knows by scar tissue: memory corruption family,
race family (data races, TOCTOU, deadlocks, priority inversion), numeric family
(overflow, promotion traps, float money bugs, 2038), state family — and the machinery
that finds them: fuzzing, sanitizers, bisect. **5 chapters + final exam.** [Start here](modules/03-software-bugs/README.md).

## Module 04 — decompilation & reverse engineering · **LIVE**

Reading what compilers leave behind. ELF/PE/Mach-O file formats from bytes up; the
loader (PLT/GOT, RELRO, interposition); compiler idioms as fingerprints; vtable and
RTTI reconstruction for C++; decompilers (Ghidra/IDA/Binary Ninja) — what they can and
cannot recover; anti-analysis and the unpacking playbook. **6 chapters + final exam.**
[Start here](modules/04-decompilation-re/README.md).

## Module 05 — Android internals · **LIVE**

Zygote fork and the boot image; ART's object model and concurrent copying GC; Binder
from the memory-map perspective (the 1MB buffer); lmkd and PSI-driven process kills;
the per-app sandbox (UID, SELinux, seccomp); APK packaging, dex, signatures, and A/B
OTA. **6 chapters + final exam.** [Start here](modules/05-android-internals/README.md).

## Module 06 — iOS internals · **LIVE**

XNU memory management (16K pages, VM maps, zones), Jetsam bands and the compressor;
Mach ports and message ownership; XPC and privilege separation; dyld launch closures
and the shared cache (the biggest COW artifact you own); code signing as page-in
validation; PAC. **5 chapters + final exam.** [Start here](modules/06-ios-internals/README.md).

## Module 07 — desktop OS internals (Windows / Linux / macOS) · **LIVE**

One architecture per pair: Windows (PE, TEB/PEB, LoaderLock, the NT heap and LFH),
Linux (ELF, vDSO, slab, page cache, overcommit, the OOM killer's arithmetic), macOS
(Mach VM, purgeable memory, unified memory) — the page-table knowledge of module 01
ch03 becomes three concrete implementations, compared in one final table.
**6 chapters + final exam.** [Start here](modules/07-desktop-internals/README.md).

## Module 08 — COBOL & the mainframe world · **LIVE**

The fixed-record universe deep dive: the z-machine (PSW, storage keys, addressing
modes), WORKING-STORAGE/LOCAL-STORAGE/LINKAGE with exact semantics, PICTURE as a
byte-level type system (COMP-3 layout arithmetic by hand), CICS transaction memory
(GETMAIN, quasi-reentrancy, storage protection), and why the world's money still runs
on a memory model with no heap. **5 chapters + final exam.** [Start here](modules/08-cobol-mainframe/README.md).

## Module 09 — concurrency & distributed memory · **LIVE**

From the hardware memory model to happens-before in distributed systems: the
language memory orderings, the lock zoo (futexes, PI, lockdep), lock-free (CAS, ABA,
hazard pointers, epochs, RCU), actors and channels as ownership transfer, and the
distributed analog — Lamport/vector clocks, quorums, Raft, CRDTs, PACELC.
**5 chapters + final exam.** [Start here](modules/09-concurrency-distributed/README.md).

## Module 10 — compilers & language runtime internals · **LIVE**

Where the memory models of module 01 are manufactured: the pipeline and SSA, the
optimizations that change memory behavior (SROA, alias analysis, UB-as-license),
register allocation as the birthplace of the stack frame, escape analysis and write
barriers and safepoints (the compiler's GC duties), and the ABI: mangling, calling
conventions, vtable emission, layout decisions. **5 chapters + final exam.** [Start here](modules/10-compilers-runtimes/README.md).

## Module 11 — database storage engines · **LIVE**

The disk is the new heap: the durability contract (fsync, torn writes, the SSD page
anatomy), B-trees (fanout derived, InnoDB page anatomy), LSM trees as generational GC
for disk, WAL and crash recovery (ARIES, group commit, doublewrite), MVCC as
copy-on-write at row scale. **5 chapters + final exam.** [Start here](modules/11-db-storage-engines/README.md).

## Module 12 — GPU & AI accelerator memory · **LIVE**

The tower's new wing: the GPU memory hierarchy (registers, shared memory banks, L2,
HBM), coalescing as cache-line physics and the roofline model, allocation and the real
anatomy of "CUDA out of memory" (the caching allocator, fragmentation), unified
memory, Apple Silicon, and CXL — closing with the full register-to-RDMA tower table.
**4 chapters + final exam.** [Start here](modules/12-gpu-memory/README.md).

## Module 13 — networking internals · **LIVE**

A packet is memory in motion: encoding and the Ethernet frame, the NIC's DMA
ring (packets land in RAM before the CPU knows), sk_buff anatomy and conntrack,
TCP as two buffer machines (windows, congestion control, bufferbloat), the
userspace I/O ladder (epoll's ready list, io_uring's shared rings, zero-copy), and
QUIC/RDMA as the network becoming memory. **5 chapters + final exam.** [Start here](modules/13-networking-internals/README.md).

## Module 14 — file systems & storage · **LIVE**

The disk is the heap that outlives the process: HDD geometry and the SSD's FTL (a
garbage-collecting log-structured allocator in firmware), the VFS trio and the page
cache (fsync decoded), ext4's journal as the WAL idea at disk scale, the copy-on-write
lineage (Btrfs, ZFS, APFS), and the special file systems that ARE the OS (procfs,
tmpfs, overlayfs) — ending with the fd-to-NAND path drawn end to end.
**5 chapters + final exam.** [Start here](modules/14-filesystems-storage/README.md).

## Module 15 — virtualization, containers & the cloud · **LIVE**

A VM is a process that thinks it's a computer: Popek-Goldberg and VT-x, EPT and the
second page table (with ballooning as a garbage collector for VMs and KSM's
side-channel cost), virtio's rings and live migration's convergence arithmetic,
containers as namespaced processes (cgroups, overlayfs, seccomp, escape economics),
and the cloud as one computer (oversubscription arithmetic, Firecracker, the
who-owns-the-bytes table). **5 chapters + final exam.** [Start here](modules/15-virtualization-cloud/README.md).

## Module 16 — browser engine internals · **LIVE**

The operating system that won: process-per-site and the broker sandbox, the DOM
as a heap graph (Oilpan and the two-collector wrapper problem, the detached-node
leak derived), the rendering pipeline as memory traffic (the 16.6 ms budget, layout
thrashing), the page's memory (leak taxonomy, workers, WASM's guard pages), and the
security boundary (SOP, site isolation, Spectre's browser chapter).
**5 chapters + final exam.** [Start here](modules/16-browser-internals/README.md).

## Module 17 — digital hardware: from transistors to silicon · **LIVE**

The root under the root: CMOS and the power equation, flip-flops and the pipeline
(hazards, forwarding, the Pentium 4 story), RTL as the other discipline (the FIFO
ring in ~40 lines of Verilog), the cache born in RTL (the 6T cell, the direct-mapped
cache, DRAM timing), and the economics that decide why your CPU looks the way it
does (yield = e^(−D·A), chiplets, EUV). **5 chapters + final exam.** [Start here](modules/17-hardware-design/README.md).

## Module 18 — cryptography internals: the memory model of trust · **LIVE**

The one field where a 50-nanosecond timing difference is a security
breach: entropy harvested from physics (ring oscillators, DRAM jitter, the
Linux CRNG), AES and its modes derived as a state machine over 16-byte
blocks (ECB's penguin, CTR's two-time pad, GCM's forbidden attack), public
key as bignum memory layout (the limb count IS the leak, Bellcore faults,
the Montgomery ladder as constant-time-by-shape), TLS 1.3 as a state machine
about key erasure (the transcript hash, 0-RTT's replay trade), and the
failure catalog — Heartbleed as a heap over-read, Lucky 13 as a timing
oracle, every famous break as a memory story. **5 chapters + final exam.**
[Start here](modules/18-cryptography-internals/README.md).

## Module 19 — programming language theory & type systems: why the rules are theorems · **LIVE**

Every language rule as a theorem: the lambda calculus and the halting
problem arriving in the same box, soundness proved by induction
(progress + preservation, the Wright-Felleisen argument), null as the
billion-dollar soundness lie, parametricity as the free theorem, the
module's heart — memory safety as a theorem (the borrow checker as an
affine type system, every module-01 language placed on the proof
spectrum with its hole), GC re-derived as proof search (tri-color, write
barriers as monotonicity, the pause as proof cost), and the honest
limits — what no type system can do and why bugs survive in safe
languages. **5 chapters + final exam.**
[Start here](modules/19-pl-type-systems/README.md).

## Module 20 — embedded & firmware: the world with no MMU · **LIVE**

The larger population of computers, with the abstractions removed: the
memory map wired into silicon (no ASLR — an address IS the thing, the
MPU's 8 regions as a 1-bit type system), bare metal where the linker
script is the memory model (the stack pointer as data in a file, the
ASSERT as a compile-time proof the RAM budget holds), interrupts and DMA
as asynchronous memory (the SPSC ring at register scale, cache-line
coherence traps, the priority ceiling as a hardware register), the RTOS
in 10 KB (task stacks in words-not-bytes, the O(1) bitmap scheduler's
lineage, Mars Pathfinder), and firmware security as the root of every
chain above it (secure boot, A/B updates as 2-phase commit, the debug
port as the real backdoor). **5 chapters + final exam.**
[Start here](modules/20-embedded-firmware/README.md).

## Module 21 — formal verification: the machines that prove programs · **LIVE**

What happens when you refuse "the type system can't prove that": Hoare
logic and the LCF trust kernel (the JVM verifier as a proof checker you
already run), model checking where the state explosion problem is
literally a heap-out-of-memory error (BDDs, CDCL, SMT — the tools and
the arithmetic), the verified kernel in person (seL4: the 100:1
proof-to-code ratio, what the theorem says and doesn't), the borrow
checker re-derived as a decidable fragment of separation logic (Reynolds
to NLL to Miri), and the verified bottom of the stack (CompCert, HACL*,
the honest boundary where proof stops and hardware begins). **5
chapters + final exam.**
[Start here](modules/21-formal-verification/README.md).

## Module 22 — ML systems & training infrastructure: the distributed memory machine · **LIVE**

The largest programs ever run are memory choreography: the tensor as
memory's industrial object (strides, layouts, the storage refcount),
autograd as a tape on the heap (the activation arithmetic, checkpointing
as sqrt), distributed training as four answers to one sharding question
(DP, ZeRO, tensor parallel, pipeline parallel — the 1F1B bubble derived),
inference serving as the KV-cache machine (paged attention as virtual
memory for tokens, continuous batching as the server's allocator), and
the collectives that make 25,000 GPUs behave like one computer (the ring
AllReduce's 2(N−1)/N, the straggler problem, the datacenter as the
1980s swap machine reborn). **5 chapters + final exam.**
[Start here](modules/22-ml-systems/README.md).

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
under every claim the program ever made (17). The third round closed the ring:
cryptography is memory discipline where a cache miss is a breach (18); type systems
are the theorems the languages run on (19); embedded is the whole stack with the
safety net removed (20); verification is the machinery that proves what the other
twenty-one modules only promise (21); and ML infrastructure is the memory model
rebuilt at datacenter scale (22). One root, twenty-two trees.

## What could come after

The program is extensible by design. The third round (modules 18-22) closed the
named frontiers, and the current candidates for a fourth round: operating systems
distributed beyond one machine (the cluster as the kernel), numerical & scientific
computing (the memory model of floating point at scale), signal processing & media
(the buffer discipline of real-time audio/video), and the economics of compute (the
business layer that decides which of these machines gets built). The method stays the
same: first principles down, insider facts on the table, labs you can run, quizzes
with teeth, and every chapter connected to the roots.
