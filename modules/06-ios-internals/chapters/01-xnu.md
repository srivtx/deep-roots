# Chapter 01 — XNU: the kernel's memory picture

> **The 30-second version.** XNU ("X is Not Unix") is three systems fused: CMU's
> Mach (tasks, threads, VM, ports) + a 4.4BSD line (processes, files, sockets,
> signals) + IOKit (C++ drivers). The unit that matters for memory is the Mach
> *task* — a container of one vm map, one port table, and threads — wearing a BSD
> *proc* as a costume: one entity, both `pid` and `task_for_pid`. The vm map lists
> policy records (range + permissions + backing VM object); module 01 ch03's
> page tables are the mechanism underneath. Apple Silicon runs 16K pages (4K on
> Intel Macs and in Rosetta), kernel memory lives in kalloc zones, and kernel
> entry has two number spaces: positive BSD syscalls, negative Mach traps.

## 1. One kernel, three ancestors

iOS and macOS run the same kernel: XNU, open-sourced by Apple and versioned with
Darwin (XNU 10063.x ships in macOS 14 Sonoma / iOS 17, Darwin 23). The name
survives in the architecture: not a Unix kernel, but it serves a Unix API.

The parts: Mach 3.0 was CMU's late-1980s microkernel research — message-passing
IPC, memory as *objects* served by pagers, a minimal kernel. Clean but slow:
every cross-component call became a message round-trip. NeXT's fix (inherited by
Apple in 1997) collocated the BSD server *inside* the kernel — keep the
abstractions, drop the message hop. iOS is the same kernel with different
policies on top; hence a Mac is a fine lab machine.

```text
  user space:   your app + libSystem + frameworks (the shared cache, ch04)
  ------------------------------------------------------------ syscall / trap boundary
  BSD layer:    procs, pids, VFS, sockets, signals            (bsd/)
  Mach core:    tasks, threads, vm maps, VM objects, ports    (osfmk/)
  IOKit:        C++ drivers, IOMemoryDescriptor, user clients (iokit/)
  ------------------------------------------------------------ hardware: MMU, 16K pages, PAC
```

Not strictly layered: BSD calls into Mach for memory work, and user space
reaches drivers *through Mach ports* (ch03). IOKit's C++ is restricted — no
exceptions, no RTTI, manual retain/release on `OSObject`.

## 2. The task: one entity, two views

Mach's word for "process" is deliberate: a **task** is a *resource container*,
never the thing that executes. It owns exactly three things — a **vm map** (the
address space, section 3), an **ipc space** (port rights, ch03), and **threads**
(the execution units: registers and stacks, nothing else). One task, N threads.

The BSD half answers "what does POSIX need?" with a `struct proc`: pid, file
descriptors, credentials, signal state. The insider's picture is **one entity,
two structs**: the task (`osfmk/kern/task.h`) carries `bsd_info` pointing at
the `proc`, the `proc` (`bsd/sys/proc.h`) carries `p_task` back —
`proc_task(p)` / `task_proc(t)` are the everyday translations. `launchd`, pid 1,
is the first pair.

```text
        one entity (e.g. pid 1234)
        +--------------------- task -----------------------+
        | vm_map    *map        address space (section 3) |
        | ipc_space *itk        port rights (ch03)         |
        | queue      threads    1..N execution units       |
        | bsd_info --------+                                |
        +------------------|-------------------------------+
                           v
        +--------------------- proc -----------------------+
        | pid_t     p_pid       1234                       |
        | filedesc *p_fd        open file descriptors      |
        | task      *p_task ---+---> back to the task      |
        +---------------------|----------------------------+
```

Both views are load-bearing: Mach is the kernel's native currency, BSD the
API's (`kill`, `wait`, every shell tool). One fork() chain crosses
`bsd/kern_fork.c` into osfmk and back — why panic traces show both
vocabularies in one backtrace. The bridge from user space is the Mach trap
`task_for_pid` (trap 26, gated by `taskgated` on macOS, entitlements on iOS):
a privilege, because a task port is a master key to a process's memory.

One flag for module 05 readers: **there is no zygote on iOS.** Sandbox policy
denies third-party apps `fork()` — every app is `posix_spawn`ed fresh, nothing
pre-warms a shared heap. The "pay once" job the zygote does with COW, iOS does
with the shared cache (ch04): frameworks are mapped, not inherited.

## 3. The vm map: policy, not mechanism

Derive it from the question the kernel must answer per access: *if this task
touches this address, what should happen?* The page tables of module 01 ch03
answer a narrower one — *how does the MMU translate this page right now?* — and
hardware format allows no field for which pager backs a region or what fork
does with it. So XNU (like Linux with its VMAs) keeps a per-task **vm map**: a
tree of **vm_map_entry** records:

```text
vm_map_entry (one per region, page-aligned bounds):
  vme_start .. vme_end       e.g. 0x102d00000 - 0x102d07000
  protection / max_protection    rw- / rw-
  inheritance                 SHARE | COPY | NOTHING  (what fork does)
  wired_count                 pages locked in place (mlock)
  object + offset             the backing VM object, and where in it
```

An entry is policy: ranges, rights, inheritance, backing. The **pmap** (the
task's page tables) is a cache of the map's decisions — derivable, rebuilt on
fault — which is why userspace tools can print the map while page tables stay
invisible: the answer to the interview trap *the vm map is not the page table.*
The PTE says "page 7 → frame F, read-only"; the entry says "this range is a COW
inheritance of that object."

Entries point into the second layer, **VM objects**. A *named object* has a
pager — a vnode pager for file mappings, the shared cache's objects (ch04), or
the compressor pager for anonymous memory on iOS (ch02). A **shadow object** is
the COW bookkeeping: when fork splits a COPY-inherit entry, XNU interposes a
shadow between the old object and the forked entries; both sides then see the
original pages read-only, and the first write copies the page into the writer's
side of the shadow — module 01 ch03's PTE-level COW (refcount > 1 → copy),
recorded at the object layer. The three layers, one diagram:

```text
LAYER 1: vm map (policy, 1/task)     LAYER 2: VM objects (sharing)   LAYER 3: physical
"this range has these rights"        refcounted, pager-backed        pages (vm_page)

task A (parent)                      O = anonymous object            +-----------+
+------------------------------+     pager: compressor (ch02)      | frame F2  |
| entry 0x1000-0x9000          |     refcount: shared read-only    | vm_page   |
|       rw, inherit COPY       |     resident: F1 F2 F3            | refct 2   |
|       object: O -------------|-----+             ^  ^            +-----------+
+------------------------------+     |             |  |  A reads F2 (read-only)
                                     |             |  |
task B (child, after fork)          |  shadow S:   |  +-- B's read of F2 falls
+------------------------------+    |  +---------------------+     through S into O
| entry 0x1000-0x9000 rw       |    |  | backing object: O   |
|       object: S (shadow) ----|----+  | refcount: 1         |     +-----------+
+------------------------------+       | resident: F5        |     | frame F5  |
                                      +---------------------+     | vm_page   |
  B writes the page at 0x2000:                                   | refct 1   |
  fault (PTE RO), page lives in O (refct 2) -> allocate F5,     +-----------+
  copy F2 into it, record F5 in shadow S, map F5 rw into B's
  pmap ONLY.  A still reads F2; one entry, two objects.
```

Two more shapes: an entry may point at no object at all — a **null mapping**;
that is all a stack guard page is, one 16K hole of `---` permissions (module 01
ch04's guard as kernel policy). And a map can contain a **submap**: the shared
cache is one submap per task, so a single vm_map_entry buys every system
framework — region counts stay in the hundreds, not thousands (ch04).

## 4. Sixteen kilobytes at a time

The number: `hw.pagesize` is **16384 on Apple Silicon** — and iOS on 64-bit ARM
since the first arm64 devices (A7, 2013) — versus **4096 on Intel Macs**. The
wrinkle: Rosetta 2 processes also run 4K, because x86-64 binaries assume 4K —
one kernel serving two page sizes at once, alone among mainstream kernels.
Detect your side: `sysctl -n sysctl.proc_translated` (1 inside Rosetta). Why
bigger? Two pieces of arithmetic.

**TLB reach.** Coverage = entries × page size: a 64-entry L2 TLB covers 256 KB
at 4K and 1 MB at 16K (Apple's real TLB sizes are unpublished; the 4x is
exact). Whatever fits under the TLB now fits 4x more of itself — and a phone
running one foreground app with contiguous frameworks (ch04) is the common
case.

**Page-table density.** ARM64, 48-bit VA, per-granule split:

```text
4K pages:   [ 9 | 9 | 9 | 9 | 12 ]     4 levels, 512-entry tables, 4K each
16K pages:  [ 1 | 11 | 11 | 11 | 14 ]  L0 holds just TWO entries; tables 16K
```

An L3 table covers 512 × 4K = 2 MB at 4K, but 2048 × 16K = 32 MB at 16K.
Densely mapping 1 GB costs 512 tables × 4K = **2 MB of page tables** at 4K
versus 32 × 16K = **512 KB** at 16K — 4x denser.

**The cost is internal fragmentation.** The smallest unit of allocation,
protection, and transfer quadruples: touch one byte in a sparse structure and
you dirty 16K, not 4K — average waste on sparse regions is half a page per
touch. Mobile tolerates it because the dominant mappings — framework text, heap
arenas, the cache — are dense. A bet on density, and it won.

**The compatibility story** is where the bugs live. Code hardcoding 4K — `addr
& ~0xfff` masks, structs packed to 4096 bytes, buffers aligned for direct disk
I/O — misbehaves only on Apple Silicon, only on some inputs. Apple's
mitigations: the arm64 linker aligns Mach-O segments to 16K (`segalign 0x4000`,
module 04 ch01), and Rosetta keeps the 4K fiction so unmodified x86 binaries
run. Your code is what sits between those safety nets.

## 5. Kernel memory is rationed in zones

The kernel cannot `malloc` from the machinery it is still building. XNU's
answer is **kalloc**: size-class zones — `kalloc.16`, `kalloc.32`, up past
`kalloc.65536` in powers of two, plus intermediate sizes in recent XNU — each
carving fixed-size elements from pages handed up by the VM layer. Specialized
zones ride the same allocator (`zalloc`): every `task`, `thread`, `proc`, port,
and VM object is itself a zone element. Watch them live: `sudo zprint`.

**Zone exhaustion is a panic class.** When a zone's refill fails — zone map
full, or no memory left at that depth — `zalloc` panics by design (`zalloc:
zone exhausted`). Why kill the kernel instead of returning an error? Callers at
that depth cannot fail: allocating a thread's kernel stack mid-fork has no
roll-back path, and a kernel on degraded internals can no longer enforce its
invariants. For contrast: userspace allocation fails softly (NULL), and
userspace *pressure* is handled by killing a process (ch02) — three escalation
levels, one root cause.

**The security saga.** Zones were an exploitation accelerant: free lists handed
the most-recently-freed element straight back (deterministic LIFO reuse), and
one zone holds whatever objects match the size class — so a use-after-free
could be reclaimed with attacker bytes of any same-size type. SockPuppet
(CVE-2019-8605, iOS 12) was exactly that in the AWDL subsystem. The hardening
came in stages: randomized free lists (macOS 10.15 / iOS 13 era),
`zone_require` assertions (iOS 15), then **kalloc_type** (iOS 16 / macOS 13,
2022): type-segregated zones, per-boot randomized, so a freed `struct foo` is
only reclaimable by another `struct foo` of the same class — cross-type
reclaim primitives die there. Apple's security blog walks SockPuppet through
an iOS 16 kernel and shows the chain breaking stage by stage ("What if we had
the SockPuppet vulnerability in iOS 16?", May 2023).

## 6. Two number spaces, one instruction

Every user-to-kernel entry on Apple arm64 is the same instruction — `svc #0x80`
— with the operation number in `x16` (x86-64: `syscall`, number in `rax`). The
kernel branches on the **sign**:

- **positive** → BSD syscall, indexed by `bsd/sys/syscalls.master` (4.4BSD
  numbering survives: 1 `exit`, 3 `read`, 4 `write`, 5 `open`);
- **negative** → **Mach trap**, indexed by osfmk's `mach_trap_table`.

The traps you meet in real code: `mach_msg` is trap 25 — on arm64, `mov x16,
#0xffffffe7` (sign-extended −25) — `task_for_pid` is 26, and the `psynch_*`
concurrency fast traps also live in the Mach space. The split persists: Mach
traps are the kernel's RPC surface — message-shaped, type-checked by MIG stubs
(ch03) — while BSD calls are the flat, errno-returning POSIX surface. One
instruction, two protocols.

A gift to anyone reading disassembly (module 04): the sign of the number in
`x16`/`rax` classifies a stub instantly. `mov x16, #4; svc #0x80` is
`write(2)`; a −25 loop is a `mach_msg` RPC pump — where one process hands data
to another, hence where you hunt attack surface (ch03).

## What insiders know

- **One kernel, two page sizes, live at once.** Native arm64: 16K; Rosetta
  x86-64: 4K. `sysctl -n sysctl.proc_translated` says which side of the line a
  shell is on; `hw.pagesize` differs across it — the fastest terminal demo of
  the compat story.
- **The proc/task duality shows in every fork-path backtrace** — frames
  alternate between `bsd/` and osfmk; panic logs name both vocabularies for
  one entity. `task_for_pid` (trap 26) is *gated* (`taskgated`, entitlements)
  because a task port is read/write access to a process's whole address space.
- **kalloc zone names appear verbatim in panics** (`kalloc.512 exhausted`) and
  in `sudo zprint`. The size class gives the victim's size — often enough to
  identify the structure and the bug before any debugger opens.
- **Mach traps are negative numbers hiding in plain sight.** `mach_msg` = −25,
  `task_for_pid` = −26; the `psynch_*` concurrency traps are Mach-space too —
  which surprises everyone who assumes anything POSIX-flavored is a BSD number.
- **What `vmmap` shows first is the header — and the header is the good part:**
  name, pid, and **Physical footprint**, the number Jetsam actually kills on
  (ch02). Most people scroll past it and never come back. The shared cache is
  one submap per process — a single vm_map_entry stands in for the whole
  multi-GB artifact.
- **Count threads by counting `STACK GUARD` rows** in `vmmap` — each is a 16K
  null mapping (`SM=NUL`, `---/rwx`) on Apple Silicon, one per thread.

## See it yourself

Documented walkthrough — written from macOS 14 (arm64); no Mac was at hand
while writing, so outputs below are representative and trimmed, and addresses
differ every boot (ASLR). Commands run unmodified on macOS 12+.

```console
$ sysctl -n hw.pagesize
16384                                     # 4096 on Intel Macs
$ sysctl -n sysctl.proc_translated
0                                         # in a Rosetta shell this prints 1

$ vmmap $(pgrep -x Finder) | head -25
Process:       Finder [531]
Parent:        launchd [1]
Physical footprint:       374.5M     <-- the Jetsam number (ch02): read this first
REGION TYPE              START - END     [ VSIZE RSDNT DIRTY SWAP] PRT/MAX SHRMOD DETAIL

__DATA                  180470000-18047c000 [ 48K  48K  40K  0K] rw-/rw- SM=COW  dyld_shared_cache_arm64e
__TEXT                  1803a1000-180470000 [828K 828K    0K  0K] r-x/r-x SM=COW  dyld_shared_cache_arm64e
MALLOC_TINY             2b10000000-2b12000000 [  2M 512K 512K  0K] rw-/rw- SM=PRV
MALLOC_SMALL            2b30000000-2b30800000 [512K 128K 128K  0K] rw-/rw- SM=PRV
STACK GUARD             12ff04000-12ff08000 [ 16K    0K    0K  0K] ---/rwx SM=NUL  ... for thread 0
Stack                   12ff08000-12ff87000 [508K   24K   24K  0K] rw-/rwx SM=PRV  thread 0
```

What to notice: (1) the columns — VSIZE is reserved virtual (the policy
layer's opinion), RSDNT/DIRTY the physical truth, SM=COW copy-on-write shared,
SM=PRV private, SM=NUL a guard. (2) the shared cache (grep it, read the span):

```console
$ vmmap $(pgrep -x Finder) | grep "dyld_shared_cache" | head -3
__TEXT     180204000-1803a1000  [ ...] r-x/r-x SM=COW  /var/db/dyld/dyld_shared_cache_arm64e/...
```

— a contiguous multi-GB block in the low half of the address space, all
`SM=COW`, `r-x` on text: one physical copy of every framework page,
system-wide (ch04). (3) the malloc zones — `MALLOC_TINY`/`MALLOC_SMALL`/
`MALLOC_LARGE` are libmalloc's arenas (module 01 ch05, ch22): user-space
allocators sit on kernel maps like any other mapping. (4) stack guards, one
per thread:

```console
$ vmmap $(pgrep -x Finder) | grep -c "STACK GUARD"
17
```

(5) the kernel's own side: `sudo zprint | head -25` lists live zones — find
`kalloc.512`, ask which structure fits. (6) `mmap` 64 MB anonymous, untouched:
the `VM_ALLOCATE 64M` row shows RSDNT/DIRTY near zero; touch every 16K and all
three columns read 64M — module 01 ch03's demand-paging argument in one row.

## How to use this knowledge

- **Triage crash reports by subsystem.** A panic naming a `kalloc` zone is a
  kernel memory bug (section 5); a Jetsam kill is ch02 policy; a guard-page
  `SIGSEGV` is a user-side overflow (module 01 ch04) — you know which chapter
  to open before reading the log.
- **Audit x86-to-arm64 ports for 4K assumptions.** Grep for `0xfff`, `4096`,
  `PAGE_SIZE` near masks and page-packed records; test native *and* under
  Rosetta — the two disagree about the page size.
- **Read disassembly faster (module 04).** The sign of the syscall number in
  `x16`/`rax` selects the table and the subsystem; −25 loops mark IPC pumps,
  and IPC pumps mark trust boundaries — where a bug becomes an exploit (ch03).
- **Explain the no-zygote design in interviews.** iOS denies apps `fork()`, so
  warm state cannot be inherited by COW; instead every framework is pre-linked
  into one COW-mapped cache (ch04) — same economics as Android's zygote (pay
  once), different mechanism: a trust-model choice, not folklore.

## Connections

- **Module 01 ch03 (page tables/COW)** — the PTE walk and refcount mechanics
  run under everything here; the shadow object is the object-layer record of
  the COW fault you derived there; the null-mapping guard page is ch04's stack
  guard promoted to kernel policy.
- **Module 05 ch01-02 (Android boot, ART)** — the contrast frame: the zygote's
  fork+COW warm start versus iOS's no-fork + shared cache; lmkd previews
  Jetsam. Same problems, opposite philosophies.
- **Module 06 ch02** — the Physical footprint number from the vmmap header is
  the currency Jetsam kills on, and the compressor pager is the anonymous
  object's pager. **ch03** — the task's other half, the port table. **ch04** —
  the submap and shadow machinery at 1-3 GB scale. **ch05** — the page-in
  moment, where VM fuses with signatures.
- **Module 07 ch06 (macOS deep dive)** — the same XNU machinery desktop-side:
  dynamic pager and swap (absent on iOS), purgeable tiers, unified memory, and
  the three-kernel comparison table.

## Quiz

1. *(concept)* A colleague shrugs: "XNU is just BSD with a Mach VM bolted on."
   Give two concrete behaviors that break under that model, using the
   structures of section 2.
2. *(concept)* A vm_map_entry and a PTE both describe memory. Name two things
   an entry records that a PTE cannot, and say why the hardware format forbids
   them.
3. *(predict the numbers)* A 64-entry L2 TLB and 1 GB of dense ARM64 mapping
   (48-bit VA): give the TLB coverage at 4K and 16K pages, the page-table
   bytes at 4K and 16K, and the factor both answers share — why?
4. *(predict the layout)* Task B is forked from A and writes one byte in a
   page of shared object O (refcount 2). How many VM objects serve B's entry
   afterwards, which holds the dirty page, and by how much does B's `vmmap`
   DIRTY grow on Apple Silicon?
5. *(bug diagnosis)* Ported x86 code masks pointers with `~0xfff` and copies
   exactly 4096 bytes per "page" from a mapped file; on an M-series Mac it
   sometimes misses data, sometimes reads a neighboring region. Why only on
   Apple Silicon, and why only some files?
6. *(bug diagnosis)* A fork bomb on a jailbroken iPhone ends in a panic
   reading `zalloc: zone kalloc.512 exhausted` rather than failed `fork()`
   calls. Why does the kernel choose death over an error return, and what does
   the zone name tell you?
7. *(bug diagnosis, module 04)* arm64 disassembly: `mov x16, #0xffffffe7 ;
   svc #0x80`, inside a loop copying from a shared buffer to a reply area.
   What operation is this, which table services it, and why is the loop a
   trust boundary?
8. *(cross-module, module 01 ch03)* `vmmap` shows shared cache `__TEXT` as
   `r-x`, `SM=COW`, mapped into 60 processes. How many physical copies of a
   given text page exist, and why does that entry never grow a shadow object —
   what would have to happen first?

<details>
<summary>Answers</summary>

1. **Two views, one entity.** (a) Every "process" is a Mach task wearing a
   proc (`task->bsd_info` ↔ `proc->p_task`); one fork chain crosses `bsd/` and
   `osfmk/` frames — a Unix-only reading of a trace is half-blind. (b) Ports
   are Mach: IPC, `task_for_pid`, and the driver channel all run through Mach
   ports.
2. **Inheritance (SHARE/COPY/NOTHING), the backing VM object + offset, and
   max_protection — none of which a PTE has.** PTEs are fixed by hardware: one
   page's translation plus a few state bits, no field for "which pager backs
   this region." Hence the policy layer, pmap as its rebuildable cache.
3. **TLB: 256 KB at 4K, 1 MB at 16K. Tables:** 4K — an L3 table covers 2 MB,
   so 1 GB needs 512 × 4K = 2 MB; 16K — an L3 table covers 32 MB, so 1 GB
   needs 32 × 16K = 512 KB. Both improve by the **same 4x**: each TLB entry
   and each PTE covers 4x more VA at the same per-entry cost.
4. **Two objects — shadow S in front, O as backing; the dirty page lives in
   the shadow.** The write faults on a read-only PTE; the kernel finds the page
   in O (refcount 2 > 1), copies it to a fresh frame, records it in S, and
   maps it read-write into B's pmap only. DIRTY grows **16 KB** — one Apple
   Silicon page.
5. **A page-size assumption, half-poisoned.** On Apple Silicon the page is
   16K: `~0xfff` rounds to a 4K boundary that is not a page boundary, and
   4096-byte copies read a quarter of each page. It "works" under Rosetta (4K
   there) and where data sits in the first 4K of each 16K page —
   position-dependent failures, the hardcoded-page-size signature. Fix:
   `getpagesize()`, never a literal.
6. **Fail-stop by design; the zone name is a fingerprint.** Callers deep in
   fork/spawn cannot roll back a missing element, and a kernel on degraded
   internals cannot enforce its invariants — so XNU panics loudly. `kalloc.512`
   says 512-byte elements — task/proc/thread-adjacent structures —
   process-creation pressure or a leak.
7. **`mach_msg` — Mach trap 25** (0xffffffe7 is sign-extended −25), serviced
   by osfmk's `mach_trap_table`, not `syscalls.master`. It is the RPC surface —
   one process handing typed data to another through the kernel — so a bug
   crosses a trust boundary; module 04 audits `mach_msg` pumps first (ch03).
8. **One physical copy, no shadow ever.** COW needs a write fault, which `r-x`
   text cannot take: all 60 processes' PTEs point at the same read-only frame
   (refcount 60, DIRTY 0). A shadow would require a *write* to those pages —
   doubly forbidden: the mapping is read-only, and page hashes are validated
   at page-in (ch05): altered code never materializes.

</details>
