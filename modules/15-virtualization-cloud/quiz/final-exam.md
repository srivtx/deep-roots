# Module 15 final exam — Virtualization, containers & the cloud

Twelve questions across all five chapters. Closed book; every answer is
worked, not stated — the derivations are the point. Scoring: 11+ = you own
this module; 8-10 = solid, revisit the flagged chapters; <8 = the chapter
quizzes are the study path.

**Part I — the trap boundary and the second page table (chapters 01-02)**

1. **Diagnose.** A 2003 design doc proposes running an unmodified Windows
   guest by parking its kernel at ring 1 on an unmodified Pentium III:
   "ring 0 for the VMM, ring 1 for the guest — the four rings exist for
   exactly this." The prototype boots, runs two hours, then corrupts a
   kernel data structure mid-critical-section. Name the instruction that
   almost certainly did it, state exactly what the metal did with it, and
   explain why no software anywhere was notified. Which 1974 theorem does
   the design violate, and what one mechanism does VT-x add that restores
   it?

2. **Predict.** x86-64, 4-level guest paging, 4-level EPT, no TLB hits, no
   paging-structure caches. (a) Derive, access by access, the memory
   references for one guest load of a 4 KB page — data access included.
   (b) The same load when the guest maps the region with a 2 MB huge page
   (3-entry guest walk) and the EPT covers the whole region with a 1 GB
   leaf (2-entry EPT walk). (c) Why does translating an EPT entry's own
   address need no third page table — and what happens to the walk count
   if it did?

3. **Diagnose.** A 96 GB host sells 16 guests at 8 GB each — 128 GB
   promised, 1.33x overcommit, inside the 1.05-1.5x band. At 02:00 each
   guest's working set settles at 6.5 GB. (a) Does the node survive
   unaided? (b) With KSM merging 15% of resident guest memory? (c) The
   security review vetoes KSM for this tier: which mechanism do they
   fear, what observation in the attack loop says "the victim touched
   this line," and which knob is *not* a defense? (d) KSM off, balloon
   inflated at 03:00: what does each guest kernel do to satisfy it, and
   what is the named disaster it risks?

**Part II — devices: the ring contract and the ladder (chapter 03)**

4. **Predict.** A guest pushes 64-byte packets through QEMU's emulated
   e1000 on a 2.6 GHz vCPU: 12 register accesses per bidirectional packet
   (TX tail poke, RX buffer post, interrupt mask/unmask, status reads),
   each a VM exit of 1,200 cycles, plus 8,000 cycles of QEMU userspace
   decode and emulate (context switches on top). (a) Cycles per packet
   and the per-core packet ceiling. (b) How many times below a 10 GbE
   NIC's 64-byte line rate is that? (c) One sentence each: what property
   of the emulated contract causes the exit count, and what design
   change does virtio make to amortize the exit?

5. **Predict.** A split virtqueue has queue size 512. (a) The descriptor
   table's byte size. (b) The avail ring's size, `used_event` included.
   (c) The used ring's size, `avail_event` included. (d) The driver has
   posted 1,300 heads in total (`avail.idx = 1300`): which ring slot
   holds the latest head, and what does the homegrown-device bug look
   like? (e) The guest kicks, and the device later interrupts: which of
   the two doorbells costs a VM exit, and why can the other one not?

6. **Diagnose.** Ops live-migrates a 32 GB guest; `calc-dirty-rate`
   reports 2.4 GB/s of dirtied memory, the link sustains 800 MB/s
   effective. (a) Derive why pre-copy never converges. (b) The guest's
   hot path is an SR-IOV VF — what does that add to the drain plan, and
   what is the standard escape? (c) Name the three escape valves for the
   convergence problem and price each in one sentence, including the
   failure mode of the most radical one.

**Part III — containers: views, bills, and the shared kernel (chapter 04)**

7. **Diagnose.** A JVM service in a pod with `memory.max` = 2 GiB and
   `memory.swap.max` = 0 dies overnight: exit code 137, no stack trace,
   no `OutOfMemoryError`, logs stopping mid-line. (a) Derive the exit
   code. (b) Which kernel component produced it, how did it choose its
   victim, and which file proves the cause? (c) Why did the JVM never
   get the chance to throw? (d) `memory.events` shows the `high` counter
   climbing for hours before the kill — what was the app experiencing
   during those hours?

8. **Predict.** A cgroup has `cpu.max = 30000 100000` and runs a
   6-thread busy loop on an otherwise idle 6-core host. (a) Describe one
   100 ms period from the cgroup's point of view. (b) Which two
   `cpu.stat` counters record it, and at what rates? (c) `top` inside
   the container shows ~30% total CPU while the host is 90% idle —
   reconcile the two, and give the latency-histogram signature that
   confirms the diagnosis.

9. **Diagnose.** A Dockerfile downloads a 900 MB model in one layer; a
   later layer deletes it after converting the model into a 4 GB tree of
   small files; at runtime the app appends one row to one of those
   4 GB files. (a) "I deleted the 900 MB file — why is the image still
   over 900 MB?" (b) The first append takes minutes and the node's disk
   under `/var/lib/containers` grows by 4 GB — what happened? (c) State
   the fix as a rule.

**Part IV — the cloud computer: oversubscription and the isolation poles (chapter 05)**

10. **Predict.** A 16-core SMT2 host — 32 hardware threads — runs 12
    instances at 4 vCPUs each: 48 vCPUs, all CPU-saturated. (a) Derive
    the fair share per vCPU and the `st` the guests report. (b) One of
    your vCPUs lands beside a stranger's memory-bound vCPU on the same
    physical core; your thread there is compute-bound — what does it
    lose, and why is `st` silent about it? (c) What does a pair of
    vCPUs on one core actually deliver at full saturation, and why is
    that a discount the spec page never prints?

11. **Predict.** A 500 GiB gp2 volume (baseline 3 IOPS/GiB, burst cap
    3,000 IOPS, full 5,400,000-credit bucket) serves an application
    averaging 2,000 IOPS. (a) What can it sustain forever? (b) How long
    until the clamp, from a full bucket? (c) What does the application
    experience at the clamp, and what will the monitoring ticket say?
    (d) State the law that generalizes to the t2.small's 12
    CPU-credits/hour.

12. **Concept (cross-module).** A platform must run strangers' code. One
    engineer argues for plain containers: "a container exposes ~400
    syscalls; a VM exposes ~50,000 instruction encodings — the smaller
    surface wins." Attack the argument with the counting refinement the
    chapter adds. Then match workload to pole, with numbers: a
    latency-critical web service in an `accept()` storm; a CPU-bound
    batch job with almost no syscalls. Last, give the two divisions
    behind Lambda's choice of Firecracker over both containers and QEMU.

<details>
<summary>Answers</summary>

**Part I**

1. **`POPF` — the instruction that executes and lies.** The guest kernel,
   believing itself at ring 0, enters its critical section by pushing a
   flags word with IF=0 and executing `popf` to disable interrupts. At
   CPL 1 the CPU executes the pop with *no fault* but silently drops the
   attempts to change IF and IOPL; the remaining bits pop normally.
   Interrupts were never off — a timer interrupt lands mid-section and
   shared state corrupts. No software was notified because `popf` is
   **sensitive without being privileged**: no trap fires, so the VMM
   never learns it should have emulated a disabled interrupt flag (its
   sibling `SGDT` fails the theorem the other way — unprivileged,
   untrapped, and truthful, the "red pill" detection). The design
   violates Popek and Goldberg (1974): an architecture is virtualizable
   iff every sensitive instruction is privileged. Ring 1 half-satisfies
   it — privileged instructions like `lgdt` *do* trap there — but the
   unprivileged-sensitive families slip through both directions. VT-x
   restores the theorem with one mechanism: VMX non-root, where the
   guest kernel runs at ring 0 but every sensitive event causes a **VM
   exit** with a reason code, and the hypervisor emulates the effect on
   virtual state (a virtual IF it actually honors) before VMRESUME.

2. (a) The MMU reads the guest PML4E, PDPTE, PDE, PTE — 4 accesses —
   but each entry lives at a guest-physical address, untouchable without
   a 4-access EPT walk: 4 × 4 = 16. The PTE's payload — the data's GPA —
   needs its own 4-access walk: +4. **24 translation accesses + 1 data
   access = 25 memory references for one guest load**; native code needs
   4. (b) The 2 MB guest page makes the guest walk 3 entries (PML4E,
   PDPTE, PDE-as-leaf); each is a GPA translated by an EPT whose leaf
   here is 1 GB — a 2-entry walk: 3 × (1 + 2) = 9. The data's GPA gets
   the same 2-entry walk: +2. The data access: +1. **12** — versus 25 at
   4 KB/4 KB and 16 at 2 MB/2 MB; the gap, paid on every TLB miss, is
   why guest huge pages are really an EPT-large-pages feature. (c) The
   EPT pointer (EPTP) is a **host-physical address**, and hardware
   treats reads of EPT-internal entries as already-physical — the
   recursion is cut by fiat at one indirection per privilege boundary.
   If it weren't, each EPT-internal read would need a third-table walk,
   and that a fourth — 4 × (1 + 4 × (1 + 4 × ...)) — and no finite walk
   would terminate.

3. (a) Touched: 16 × 6.5 = **104 GB against 96 — 8 GB short**: unaided,
   the node slides toward host swap — RAM's ~100 ns design point against
   tens of microseconds from NVMe (~100-300x) or milliseconds of disk
   (~10,000x), a step function, not a slope — or balloons guests that
   have nothing to give. (b) Footprint = 104 × 0.85 = **88.4 GB — it
   fits**, with 7.6 GB headroom; a fleet riding on a dedup ratio is why
   KSM is a policy debate, not a footnote. (c) KSM merges byte-identical
   pages across VMs into one write-protected shared page — two tenants
   on one physical cache line, the FLUSH+RELOAD precondition (module 02
   ch07). Attack loop: `clflush` the line, let the victim run, reload
   and time — a **fast** reload means the victim touched the line
   between flush and reload; the touched-set across the page leaks the
   execution path (Yarom and Falkner, 2014, an RSA private key from a
   co-located Xen VM). `merge_across_nodes=0` only restricts merging to
   one NUMA node — a locality knob, not a defense; the honest mitigation
   is KSM off (`run=0`). (d) The balloon driver allocates guest kernel
   memory on command and hands the GPAs back; QEMU
   `madvise(MADV_DONTNEED)`s those pages and the host frames return to
   the free list for other guests. The guest did the hard part — it
   chose the victims, page cache first — and if it is truly tight it
   swaps *inside itself* to satisfy the balloon, each page hitting the
   virtual disk twice on the way back: the **double paging disaster**,
   why ballooning is a policy tool, not a free lunch.

**Part II**

4. (a) 12 × 1,200 = 14,400 exit cycles + 8,000 emulation = **22,400
   cycles per packet**; 2.6 × 10^9 / 2.24 × 10^4 ≈ **116,000 packets per
   second per core** (the context switches push it lower). (b) 14.88
   Mpps / 0.116 Mpps ≈ **128x below 10 GbE line rate** — about 1,300x
   below 100 GbE's 148.8 Mpps: the chapter's 150-1,500x band, derived.
   (c) The e1000 contract is register conversation: the driver pokes
   MMIO registers, and each poke is a store into an EPT-non-present
   region — an EPT violation, an exit, a detour into a QEMU userspace
   thread. virtio changes the contract, not the emulator: all data lives
   in shared guest RAM so payload bytes never trap, and the two
   doorbells amortize — one kick per *batch* of descriptors, one
   interrupt per batch of completions — so the 1,000-2,000-cycle exit
   is paid per batch, not per register.

5. (a) 512 × 16 = **8,192 B**. (b) 2 + 2 + (2 × 512) + 2 = **1,030 B**.
   (c) 4 + 4 + (8 × 512) + 4 = **4,108 B** — used entries are 8 B
   (32-bit `id` and `len`) while the avail ring's words are 16-bit: the
   2008 spec asymmetry. (d) 1,300 mod 512 = **slot 276**: the indices
   are free-running and position = `idx & (qsz-1)`. The homegrown bug is
   subscripting the 512-entry table with `idx` directly — out of bounds
   within the first 512 operations; power-of-two queue sizes exist so
   wraparound is a mask, not a division. (e) The **kick** exits: it is a
   store the vCPU just executed to trapped memory (a PIO exit, or an EPT
   violation on an MMIO BAR — chapter 02's mechanism), and observing a
   store at the instruction that made it requires a trap; ioeventfd only
   changes *who handles it* (KVM in-kernel, guest re-entered, QEMU never
   runs). The **interrupt** cannot exit: the device writes the used ring
   (plain guest RAM) and raises the queue's MSI-X vector through an
   irqfd; KVM latches the vector in the virtual local APIC and delivers
   at the next vmentry. Guest-generated events must trap to be seen;
   host-generated events are state you push in — the asymmetry that is
   half of why virtio is cheap.

6. (a) A pass copying set S over bandwidth B takes S/B seconds, during
   which the guest dirties at rate d; the next set is roughly S·(d/B).
   Here d/B = 2,400 / 800 = **3**. Pass 1 copies 32 GB / 0.8 GB/s = 40
   s, in which the guest dirties up to 2.4 GB/s × 40 s = 96 GB — more
   than the whole RAM, so every page is dirty again: pass 2 is 32 GB
   again, forever. The insider's first triage question — *dirty rate
   versus link* — is answered before the 2 a.m. window: this guest needs
   d < 800 MB/s. (b) The VF's queue state, DMA mappings, and interrupt
   config live in *silicon on the source host*; the target cannot conjure
   an identical function, so at cutover the datapath vanishes and every
   TCP connection resets — hence the rule: no SR-IOV for the migratable
   tier. The escape is the net_failover bond — pair the VF with a virtio
   standby NIC, flip the datapath to virtio before migration, flip onto
   the target's VF after: native speed when static, migratable when
   needed. (c) **Auto-converge**: throttle the vCPUs until d < B —
   deliberately manufacturing chapter 01's steal time. **XBZRLE**:
   delta-compress the dirty pages — a guest rewriting the same pages is
   its best case. **Post-copy**: resume the guest on the target first
   and let untransferred pages fault across the network — no convergence
   problem and near-instant cutover, but the VM's memory *is* the
   network: if the link dies, the VM dies, because most of its memory
   exists only at the source.

**Part III**

7. (a) 137 = 128 + 9: the shell's encoding of death by **SIGKILL**.
   (b) The cgroup-scoped OOM killer — when page charges cross
   `memory.max`, the kernel picks the highest-badness task *inside this
   cgroup only* and SIGKILLs it. The proof is `memory.events`'
   `oom_kill` counter, not a traceback — and not `dmesg`, usually
   unavailable inside a container. (c) The process died mid-allocation:
   the charge crossed the limit at fault time and the kill arrived
   before the allocator could return failure — `OutOfMemoryError`
   requires surviving the allocation. With `swap.max` = 0 there is no
   pressure-release valve, so `memory.max` arrives that much sooner.
   (d) `memory.high` is the humane limit: hard reclaim, the *allocating*
   task made to evict pages itself — the app sees **latency, not
   death**: refaults in `memory.stat`, pressure in `memory.pressure`.
   The hours of `high` are the warning; the `oom_kill` at `max` is the
   wall the runbook says to read `memory.events` before blaming the
   code.

8. (a) The 6 threads burn the 30 ms quota in **5 ms of wall time**
   (6 threads × 5 ms), then the kernel dequeues every runnable task in
   the cgroup for the remaining **95 ms** — run 5, frozen 95, every
   period; the quota is shared, so the threads freeze together. (b)
   `nr_throttled` increments once per period (~10/s — climbing at
   `nr_periods`' rate means fully walled); `throttled_usec` accumulates
   ~95,000 µs per period and **never resets**. (c) 30 ms of CPU per
   100 ms = 0.3 cores average — exactly the ~30% in-container reading —
   while the host idles because the host's idleness and the cgroup's
   ceiling are independent variables: this wall belongs to the tenant's
   own config. The confirming signature is period-quantized: stalls land
   on the 100 ms grid, so the p99 histogram grows **teeth at exact
   100 ms multiples**. Fix: raise the quota, shorten the period for
   latency-sensitive work, or switch to `cpu.weight` — proportional
   share, never a freeze.

9. (a) Image layers are immutable diffs: `rm` of a lower-layer file
   writes a **whiteout** — a character device node, major/minor 0:0 —
   into a new layer that hides the entry. The 900 MB still ships in its
   layer; deletions never shrink an image. (b) **Copy-up**: the 4 GB
   file lives in a read-only lower layer, and the first write copies the
   *entire file* into the container's private upperdir before the edit
   starts — 4 GB of I/O and 4 GB of disk for one appended row: module 01
   ch03's copy-on-write at file granularity, running on module 14 ch05's
   overlayfs. (c) Big mutable data belongs in volumes or bind mounts,
   never image layers — and build intermediates stay outside the layer
   stack (multi-stage builds) so the image carries the result, not the
   debris.

**Part IV**

10. (a) Fair share = 32 hardware threads / 48 vCPUs = **2/3 of a thread
    each**: a saturated vCPU is runnable-but-not-running the other 1/3
    of wall time, so the guests report **st ≈ 33%** — the hypervisor's
    per-vCPU accounting (nanoseconds the vCPU was ready and unpaid-for),
    the noisy neighbor's fingerprint without identity. (b) **20-40%**:
    the stranger's memory-bound stream evicts the L1/L2 working set the
    two SMT siblings share (module 01 ch02 one level up), and `st` stays
    silent because steal measures only *scheduling* — your vCPU ran
    100% of its entitled time; the contention happened inside the core.
    The SMT sibling is the noisy neighbor you cannot prove — no column
    in `top` shows it. (c) The pair delivers **1.2-1.3x one thread's
    throughput, not 2x** — the second SMT thread only fills bubbles in
    the shared execution ports. A 16-vCPU instance is 8 cores × SMT2 =
    8 × 1.2-1.3 ≈ 9.6-10.4 core-equivalents, 60-65% of sixteen
    dedicated cores: a 35-40% discount the spec page never prints.

11. (a) **1,500 IOPS** — 3 IOPS/GiB × 500 GiB: the refill rate is the
    only rate that lasts. (b) Net drain 2,000 − 1,500 = 500 credits/s;
    5,400,000 / 500 = 10,800 s = **3 hours**. (c) Not an error — a
    cliff: the volume clamps to 1,500 while the application still
    demands 2,000, so ~500 IOPS of demand queues; queue depth grows,
    latency follows (the queue *is* the latency), and the ticket says
    "the disk got slow at hour 3" with healthy error rates throughout —
    burst clamps fail silent, their defining property. (d) **Sustained
    = refill**: the bucket is a battery, not a pipe. The t2.small's 12
    CPU-credits/hour, at 1 credit = 1 vCPU-minute, is 12 vCPU-minutes
    per hour = **20% of one core forever**, the full core only while
    the battery lasts — the same law, a different token.

12. Raw counting misleads because it counts the wrong thing:
    instructions execute **in silicon** and never touch host software —
    the host code a guest can reach is the handful of exit reasons
    handled by the VMM — while a container's ~400 syscalls *all* land in
    ~30 MLOC of kernel C the host itself depends on. Count the doors
    that reach host code and the "smaller" surface is the stronger
    wall: the VM's. That is why a kernel 0-day like Dirty Pipe escapes a
    container trivially (`pipe`/`splice`/`write` are all in the default
    seccomp allowlist — seccomp gates doors, not the code behind them)
    but breaks only a Firecracker guest's own sandbox: the guest's
    syscalls trap into *its own* untrusted kernel, never the host's.
    Matching: the `accept()` storm is **syscall-frequency-bound** —
    gVisor's intercept plus sentry work costs 2-10x per syscall, and
    frequency, not bytes, is the pain predictor — so it wants
    Firecracker (or a container, for trusted tenants); the CPU-bound
    batch fits gVisor comfortably — few syscalls, full speed. The two
    divisions behind Lambda: **~1.5-2 MLOC / ~30-50 kLOC ≈ 40x less
    untrusted-input-parsing code than QEMU** (containers already
    disqualified by the shared kernel), and **2,000 × 150 MB = 300 GB
    vs 2,000 × 5 MB = 10 GB** of per-VM overhead — plus the 125 ms boot
    — which is what lets thousands of strangers share one host.
    Firecracker and gVisor answer the same threat with opposite
    geometry: a small wall per untrusted kernel (a VM per workload), or
    the kernel's whole surface re-implemented in userspace Go.

</details>
