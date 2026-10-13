# Module 15 — Virtualization, containers & the cloud

> A VM is a process that thinks it's a computer. The trick is three
> nested illusions: the CPU grows a "guest mode" (VT-x), memory grows a
> second page table (EPT), and devices become shared-memory rings
> (virtio). Containers skip all three and just give one process a
> private view of the kernel's global tables. The cloud is the endpoint:
> your "machine" is a process in someone's scheduler — and every layer
> of the stack is answerable to one question, module 01's question:
> *who owns which bytes, and who moves them*.

**5 chapters · one final exam · prerequisite: module 01 chapters 03
(page tables — directly extended), 04 (the process as a data structure —
extended), 06 (object layout + refcounting); module 07 ch04-06 (the
Linux kernel's memory); module 14 ch05 (overlayfs — reused by
containers).**

## How to study it

Chapter 01 is the CPU problem (why x86 resisted virtualization, and how
VT-x solved it); 02 is memory (EPT's two-level translation, ballooning,
KSM); 03 is devices (virtio's rings, vhost, SR-IOV, live migration);
04 is containers (namespaces, cgroups, overlayfs, the shared-kernel
truth); 05 is the cloud as one computer (oversubscription arithmetic,
Firecracker, the who-owns-the-bytes table). Labs: a Linux host with
KVM if available (or we ARE the guest — that's itself the lab), plus
unshare/systemd-run which run anywhere.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The virtualization problem and the CPU's new mode](chapters/01-why-virtualize.md) | Popek-Goldberg, the x86 holdout, VT-x — why the CPU grew a guest mode. |
| 02 | [Memory virtualization: EPT and the second page table](chapters/02-ept-memory.md) | Nested translation's cost, ballooning, KSM — overcommit as a garbage collector. |
| 03 | [Devices: virtio, vhost, SR-IOV](chapters/03-virtio-devices.md) | Emulation → paravirtual rings → kernel dataplanes → passthrough, and live migration. |
| 04 | [Containers: the namespaced process](chapters/04-containers.md) | Namespaces, cgroups, overlayfs, seccomp — the sandbox that shares a kernel. |
| 05 | [The cloud as a computer](chapters/05-cloud-computer.md) | Oversubscription arithmetic, Firecracker, gVisor — the who-owns-the-bytes table. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the virtualization problem and the CPU's new mode.** The
problem stated from first principles: the OS multiplexes untrusted
*programs* by trapping privileged instructions; a hypervisor must
multiplex untrusted *operating systems* — but an OS executes privileged
instructions constantly, and if you just run two OSes at ring 0, the
first `hlt`/page-table write bricks the machine. The clean theory:
Popek-Goldberg (1974) — virtualization is efficient iff all
*resource-controlling* instructions trap; the guest can then be run
unmodified with trap-and-emulate (the trap tells the hypervisor "the
guest tried to touch shared state" → emulate the effect on virtual
state). The x86 holdout: the architecture violated the rule until 2005 —
the canonical example: `SGDT` (store GDT register) is *sensitive* (it
reveals the real GDT address, which lets a guest detect it's not on
bare metal... more precisely: it does NOT trap when run at ring > 0,
but its output differs by privilege — the trap-and-emulate
assumption "sensitive ⇒ traps" fails); a subtler one: `POPF` at ring 3
silently *ignores* the IF bit instead of trapping (the guest thinks it
disabled interrupts; it didn't) — name both, they are THE textbook
examples. The three workarounds, each a business: (1) binary
translation (VMware 1999-2008: scan the guest code at runtime, *replace*
the offending instructions with trap-able calls (a "recompiled" hot
path cache; the insight: only ~1% of instructions needed rewriting —
which is why it worked); (2) paravirtualization (Xen 2003: don't fix
the CPU, fix the *guest* — patch the guest kernel so it never issues
the offending instructions, it *cooperates* (hypercalls instead); the
cost: modified kernels, which is why Linux gained pv_ops and Windows
wouldn't); (3) hardware: VT-x (Intel 2005) / AMD-V (2006) — the CPU
gains a "guest mode" with explicit entry (VMLAUNCH/VMRESUME) and exit
(VM exit: every sensitive event now traps *into the hypervisor* with a
reason code). The machinery drawn: the VMCS (the 4 KB control block
per vCPU: guest-state area, host-state area, the exit reason, the exit
qualification — on exit the CPU loads the host state and the
hypervisor reads the reason; the launch/exit as the world's most
expensive function call: ~1,000-2,000 cycles per round trip — THE
number that drives all of ch03's design). Ring -1 folklore (the
hypervisor isn't really "ring -1" — VT-x is a mode switch, not a ring —
but the folklore is a useful lie; the truth: VMX root / VMX non-root).
The taxonomy that isn't: type-1 vs type-2 (bare-metal vs hosted) as
marketing — KVM: a Linux kernel module where the *host kernel itself*
is the hypervisor and each VM is a normal userspace process (QEMU) that
calls into it — draw that architecture (the vCPU = a thread in a
process; the ioctl(KVM_RUN) loop = "run the guest until it exits");
Xen, Hyper-V, ESXi each positioned in one sentence. The insider's
vocabulary: exit reasons (EPT violation, I/O instruction, CPUID, halt,
interrupt window — the ones that dominate production traces), the
steal-time accounting (`st` in top — a vCPU waiting for a physical CPU
is charged there — the noisy-neighbor's signature, returns in ch05).
Lab: `grep -cE 'vmx|svm' /proc/cpuinfo` (the CPU flag), `lsmod | grep
kvm`, `dmesg | grep -i kvm` (the enablement lines), kvm_stat or
`perf stat -e kvm:kvm_exit` (exit reasons live — if we're on a VM or
bare metal, run the honest one available), `lscpu | grep -i virt`; the
QEMU quick start documented (qemu-system-x86_64 -enable-kvm ... with
the one-line "what each flag does").

**Chapter 02 — memory virtualization: EPT and the second page table.**
The problem: the guest owns a page table (guest VA → guest "physical"),
but the hypervisor owns the real one (host VA → host physical) — a
guest memory access now needs *two* translations, and the CPU's MMU
only knows one walk. The naive composition drawn: translating one
guest VA touches 4 (guest PTE walk) + 4 (each guest PTE read is itself
a host address needing an EPT walk) ≈ 24+ memory accesses in the worst
case — derive the count explicitly with the page-table structure of
module 01 ch03 (the 5-level... keep 4-level for clarity: 4 guest levels
× (1 access + its own 4-level translation) — the ~20-30 number; the
cached walk (the TLB) is the only thing making this viable: a TLB hit
is still one cycle, so the *entire* cost concentrates on TLB misses).
The two fixes, historically: (1) shadow page tables (pre-VT-x era and
long after in VMware/KVM: the hypervisor *merges* guest+host tables
into one real table the CPU walks normally — the cost: the shadow
must be kept in sync with the guest's writes to ITS page tables, so
the hypervisor write-protects guest page-table pages and re-syncs on
fault — the "shadow paging makes guest kernel builds slow" era), (2)
EPT/NPT (hardware walks both tables: the guest page table first, then
the EPT for each resulting guest-physical address — the combined walk
in hardware, ~2× the miss cost of native but no sync games; the big
mitigations: EPT large pages (2 MB / 1 GB EPT entries collapse the
translation of huge mappings — one of THP's real jobs in guests), and
VPID/ASID (TLB entries tagged with the VM's ID: a VM switch no longer
flushes the TLB — without VPID, every vCPU handoff pays the cold-TLB
tax on the next few thousand accesses — the insider's "why
single-vCPU pinning mattered"). MMIO as a trap: the EPT marks device
MMIO regions as non-present → an EPT *violation* = a VM exit = QEMU
emulates the device register write — derive: "a device's registers are
a page fault" (the unified sentence that also explains module 13's
doorbells and module 17's memory-mapped peripherals). Memory
overcommit — the economic reason for all of it: the hypervisor sells
more RAM than it has (the guest's promised 8 GB is a *ceiling*, not a
reservation — module 01 ch03's overcommit lesson replayed one level
up); the three tools: (1) ballooning (a driver inside the guest that
*allocates and frees on command*: the hypervisor asks for 2 GB back,
the balloon driver mallocs 2 GB inside the guest, the guest's own
kernel frees pages to the balloon, the hypervisor COW-zeros those
guest-physical pages and reassigns them to someone else — the design
irony named: this is a **garbage collector for VMs**, module 01
ch14/ch15's cycle collector inverted: the collector asks the *program*
to enumerate its garbage; the failure mode: the guest has nothing to
give (its page cache is needed; the guest swaps inside itself — the
double-paging disaster), (2) page sharing/KSM (the kernel scans
identical pages across VMs (content hashing → byte compare → merge
into one COW page: module 01 ch03's copy-on-write, applied across
*virtual machines*; the measured reality: 100 Windows guests booting
the same image share 40-70% of memory; the cost: the scan's CPU (a
kernel thread ksmd, tunable pages_to_scan), the security problem: the
FLUSH+RELOAD side channel via cross-VM shared pages (module 02 ch07
returns — this is how one tenant reads another tenant's memory from a
shared cache line: the dedup made them physically adjacent; the
mitigation: /sys/kernel/mm/ksm/merge_across_nodes=0 and the
"security folks disable KSM" policy), (3) the last resort: host swap
(virtualize physical memory as swap — the catastrophic latency cliff,
the "never let a fleet node hit it" runbook rule). vNUMA: a 2-socket
guest on a 1-socket host runs at ~60% on NUMA-local workloads (the
guest's memory is local to *a* socket, its scheduler's node affinity is
a fiction — the vcpu pinning + numad story); nested virtualization
(EPT-on-EPT: the L1 hypervisor's EPT manipulations trap to L0 — the
"exit on exit" recursion, why it works and costs ~2× on TLB misses).
Lab: `grep -c ept /proc/cpuinfo`... the honest flag check (`lscpu |
grep -i -E 'ept|npt'` may not print — use /proc/cpuinfo flags), `cat
/sys/kernel/mm/transparent_hugepage/enabled`, the balloon driver's
presence (`lsmod | grep virtio_balloon` inside a VM), KSM knobs
(`cat /sys/kernel/mm/ksm/pages_shared` — observe zeroes when not
merged), free -h in guest vs host for the same VM (the overcommit
visible), the EPT-walk arithmetic done on paper as the "lab you can
always run".

**Chapter 03 — devices: virtio, vhost, SR-IOV.** The naive design:
emulate real hardware (QEMU's e1000: the guest boots unmodified
drivers; every MMIO register access = an EPT violation = a VM exit
(~1,000-2,000 cycles) = a QEMU userspace thread's emulation — a packet
costs tens of exits; derive the ceiling: ~50k-100k packets/sec per
core, vs a bare-metal NIC's 14.88 Mpps (ch01 of module 13) — three
orders of magnitude, the number that motivates the whole chapter).
virtio as the paravirtual contract: the guest *knows* it's virtual (a
special PCI device ID; a driver that speaks to the hypervisor, not to
pretend-hardware); the core data structure — the **vring** drawn byte
by byte: a descriptor table (16 B descriptors: addr, len, flags, next —
pointing at guest-physical I/O buffers *the hypervisor translates via
the guest's memory map*), the avail ring (the guest publishes "these
descriptors are ready"), the used ring (the device publishes "these
are done"); the two notifications: the *kick* (guest → device: "avail
updated" — written to a PCI register: a VM exit or an ioeventfd) and
the *interrupt* (device → guest: "used updated" — an injected MSI-X:
no exit needed); the point: **all data lives in the guest's memory and
the device (whoever implements it) reads/writes it directly — the ring
IS the shared memory** (the design lineage: the NIC's DMA ring (module
13 ch01) → io_uring (module 13 ch04) → the vring — one idea, three
names, say it explicitly). The implementation ladder, each rung moving
the work out of the slow path: (1) QEMU-in-userspace (the default:
each kick exits, QEMU's virtio-net thread does the packet work and
calls the kernel's tap device; costs: 2 context switches + the exit
per burst), (2) vhost-net (the dataplane moved into a *kernel thread*:
the kick lands in an ioeventfd, the vhost kernel thread reads the vring
directly and talks to the NIC — QEMU (userspace) is completely out of
the hot path; the IRQ injection via irqfd: the packet's completion
never exits the kernel — the throughput jump ~3-5×), (3) SR-IOV
(hardware passthrough: the NIC exposes a PF + VFs (virtual functions —
real hardware queue pairs assigned to VMs; the VF's DMA writes
*directly into guest memory* through the IOMMU — a third page table!
(the device's own VA→PA translation (iova): module 12 ch03's GMMU
cousin; the device can't be trusted with raw physical addresses — the
IOMMU confines a device to its owner's pages: the "device isolation"
the PC had lacked for 30 years); performance: native, zero exits; the
cost: live migration breaks (the VF's state lives in the NIC, and the
guest talks to real hardware you can't move mid-flight), and the
cloud's "no SR-IOV for the migratable tier" policy — the trade stated
as an architecture rule). Live migration as the crown jewel: pre-copy
(the loop: iteratively copy guest RAM while the guest runs; the EPT is
write-protected per pass — mark pages read-only, the guest's writes
cause EPT violations, dirty pages re-enter the next pass; convergence:
dirty-rate vs copy-bandwidth — if the guest dirties 4 GB/s over a
1 GB/s link, migration *never converges* (the W^X workload migrates in
one pass; a memory-churning build daemon doesn't — the insider's
first triage question); the cutover: stop the vCPUs, copy the final
dirty set + device state, resume on the target (the downtime budget:
tens of ms); post-copy (copy-on-demand: resume the guest first, every
un-transferred page is a fault fetched over the network — faster
cutover, catastrophic if the network dies: the "the VM's memory IS the
network" endpoint — the risk trade), the device state problem (what
even is "the state of a disk controller" — the reason virtio's clean
model made migration practical). Lab: the vring observed
(`ls /sys/bus/pci/devices/*/virtio*/` if in a VM, `ethtool -i` shows
virtio_net), an ioeventfd awareness demo (strace a QEMU... documented),
the migration arithmetic on paper (a 16 GB guest, 5 GB/s dirty rate
vs 10 Gbps link: does it converge? — the worked answer), `lspci | grep
-i -E 'virtio|sriov'`, `modinfo virtio_net | head`.

**Chapter 04 — containers: the namespaced process.** The reframe: a
container is not a lightweight VM — it is a *normal process* whose
kernel-resource views are swapped. The 8 namespaces (CLONE_NEW*): mount
(a private mount table — the "rootfs" of a container is just a pivot_root
into a directory), pid (a fresh pid counter: the container's init is
"pid 1" in its namespace, but 30,142 to the host — the NSpid line in
/proc/self/status shows both; the pid-1 responsibilities that make
containers weird (no signal defaulting, zombie reaping — the "why your
container dies when the app crashes differently" folklore), net (a
private network stack: interfaces, routes, iptables, /proc/net — the
veth pair as the wire (a virtual crossover cable: one end in the
container, one in the host's bridge), uts (hostname), ipc (SysV/POSIX
queues), user (the uid map: "root inside = uid 100000 outside" — the
map drawn, the reason containers can fake root safely), cgroup (the
cgroup namespace), time (the newest). The kernel's "globals become
per-task tables" pattern named: every namespace is the same move (the
module 01 ch03 lesson "the process is a data structure the kernel
walks" — a container is the same data structure with different
pointers). cgroups as the accounting half: v1's controller-per-hierarchy
mess vs v2's unified hierarchy (the "why v2" story in 3 sentences);
the memory controller: memory.high (throttle — the kernel's reclaim
works the cgroup hard: the app sees latency, not death) vs memory.max
(the OOM inside the cgroup: the kernel kills a *task inside the
container* (the OOM killer's arithmetic from module 07 ch05 scoped to
the cgroup); the exit code 137 = 128+9 (SIGKILL) — THE forensic
number), memory.swap.max; the CPU controller: cpu.max = quota/period
(the CFS bandwidth: a cgroup that exhausts its quota is *throttled*
until the next period — the 100 ms default period and the "10 ms stall
per 100 ms" incident class: cat cpu.stat's nr_throttled/throttled_usec
— the insider's first check when "the container is slow but the host
is idle"). The container file system: the image = layers of tarballs
(a layer is a diff: files + a manifest), the running rootfs = an
overlayfs mount (module 14 ch05 reused explicitly: lowerdirs = the
image layers, upperdir = the container's writes; copy-up on first
write: editing a 100 MB file in a container copies 100 MB — the
"containers are free until you edit big files" bill), the whiteout
delete). The security truth: **the kernel is shared** — a container
escape is one kernel 0-day (the host's kernel: every CVE in modules
02-03 is an escape path), the defenses layered: seccomp-bpf (a syscall
allowlist enforced in the kernel (the default container profile:
allows ~300 of ~400 syscalls — the blocked tail (modules, kexec,
bpf itself...) — the denied call returns EPERM (or ENOSYS: glibc's
fallback path story — why blocking gets messy), capabilities (the
privilege bits: root's powers split into ~40 CAP_* bits, the container
drops the bounding set (no CAP_SYS_ADMIN: no mounting, no module
loading — the "container root ≠ host root" sentence made precise),
AppArmor/SELinux (module 05's MAC model returns: the container's
profile confines it to its own files/sockets), and the escape case
studies named (runc CVE-2019-5736: the /proc/self/exe overwrite — the
"the runtime itself was the attack surface" lesson; Dirty Pipe (2022):
a kernel pipe bug → container escape → the module 03 ch02-style bug
anatomy one-liner). The honest comparison table: container vs VM vs
process (boot time ms vs s, memory overhead ~0 vs ~100-200 MB vs 0,
isolation: syscall interface vs CPU instruction set vs none, density,
the escape cost: one 0-day vs a chain of hypervisor+kernel bugs — the
"attack surface is an API surface" argument (the VM's interface is
~50k machine instructions + a handful of exits; the container's
interface is ~400 syscalls — the smaller surface is the stronger wall,
and it's the VM's). Lab: `unshare -Urmpf bash` (the hand-made
container: watch pid 1, /, and uid map change — the 4 lines that prove
a container is just clone flags), `readlink /proc/self/ns/*`,
`cat /proc/self/cgroup`, `systemd-run --scope -p MemoryMax=100M
python3 -c 'a="x"*10**9'` (the OOM observed, exit 137 — the
measurable), `grep -E 'NSpid|Seccomp|Cap' /proc/self/status`, the
copy-up demo (an overlayfs mount by hand: lowerdir/upperdir/workdir,
edit a lower file, `du` the upper), docker/podman optional and marked.

**Chapter 05 — the cloud as a computer.** The reframe: a datacenter is
one machine (the warehouse-scale computer view): the fabric as a
backplane (spine-leaf, the 3:1-ish oversubscription ratios — the honest
engineering, not the brochure's "non-blocking"), the cluster as a
motherboard (the rack's ToR switch as a northbridge, the PDU as the
power rail), the fleet scheduler as the kernel's process table (the
bin-packing scheduler: a placement decision per second across ~10⁵
machines — "your instance is a job in a globally distributed epoll
loop" (the module 01 ch04 process-is-a-struct sentence, scaled)).
Oversubscription arithmetic — the cloud's economic engine, each
resource with its honest ratios: CPU (SMT: 2 vCPUs = 1 physical core's
~1.2-1.3× (not 2×) of throughput — the SMT sibling's interference
(one sibling's memory-bound stream slows the other's compute-bound
loop 20-40%: the shared L1/L2 of module 01 ch02 biting one level up);
the steal-time signature (`st` in top/vmstat — the hypervisor ran
someone else: the noisy neighbor's fingerprint; cpu.weight as the
contested share), memory (the fleet's 1.05-1.5× policies; ballooning
at fleet scale (the "memory pressure storm" folklore: a node under
pressure balloons its guests, the guests' internal page caches thrash,
latency spikes — the runbook rule: bursty-memory tenants and
oversubscribed nodes don't mix), the MCR metric (memory compression
ratio) as the cheaper lever), network (per-VM bandwidth caps via
token buckets shaping at the hypervisor; the "10 Gbps instance on a
shared 100 Gbps host" arithmetic), disk (the shared-storage noisy
neighbor: the burst-credit economics (the t2.small CPU-credits story
generalized: io² "IO credits" — the token bucket's steady-state
arithmetic: the burst rate × the refill rate = the sustained rate —
the billing curve decoded), the local-NVMe vs network-volume trade
(EBS: replicated, snapshot-able, ~100-200 µs away (the module 14 ch01
NVMe latency + the network hop); local NVMe: ~20-50 µs, dies with the
host — the stateless-service architecture consequence). Firecracker as
the case study (AWS's 2018 microVM monitor: the design brief from
Lambda's threat model (multi-tenant *untrusted* code, 125 ms cold
start, ~thousands of VMs per host); the cuts: KVM's interface stripped
to a ~20-vsyscall device model (no PCIe, no USB, no display: just
virtio-net/blk + a serial console), a seccomp+jailed VMM, memory
overhead per microVM ~<10 MB, no live migration (Lambda's snapshot
instead: the VM's memory + devices frozen to a file, resumed across
the fleet — the fork trick scaled), and the honesty: Firecracker is
*less* general than QEMU on purpose (the security-vs-flexibility
trade stated as numbers: QEMU's ~2 MLOC attack surface vs
Firecracker's ~50 kLOC). gVisor as the alternative pole (the
userspace kernel: a sentry that intercepts the container's syscalls
and *implements* them in Go (the file system, the network stack
(user-space TCP/IP), the page tables (user-space mm) — the cost
measured: the syscall trap + the sentry's work = 2-10× syscall
latency (the "accept() storm" benchmarks), the payoff: the kernel
attack surface shrinks to the few syscalls the sentry itself needs
(the "API surface is attack surface" argument again — ch04's table
extended). The closing table — **who owns the bytes** at every layer:
process (its address space, the kernel's malloc), container (the
namespace views + cgroup limits + overlay upper), VM (its guest
physical memory = a QEMU mmap + EPT + the host's accounting), the
hypervisor (the real page tables + the host kernel's slab/heap), the
host (the DIMMs + ECC — the last line of defense against the bit
flips of module 01 ch01), each row's fault domain, blast radius, and
escape cost — the whole program in one table. Lab: the noisy-neighbor
arithmetic on paper (three worked scenarios), `top`'s st column
decoded (run two CPU hogs inside... measurable if a VM; documented
elsewhere), systemd-run the cgroup demo again with CPUQuota (the
throttle visible: two hogs, one capped), /proc/self/status's NSpid
(the pid namespace arithmetic), the burst-credit curve sketched and
worked from a real spec sheet.

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **The VM's real architecture**: VT-x, EPT, virtio rings — you can
  read a hypervisor's design doc (or a CVE writeup) without flinching.
- **Container literacy**: namespaces, cgroup throttling, overlayfs
  copy-up, escape economics — the "is it safe" and "why is it slow"
  conversations, answered with numbers.
- **Cloud judgment**: oversubscription ratios, burst credits, steal
  time — what you're actually buying, and how to see it from inside.
- **The completed stack**: process → container → VM → hypervisor →
  host — one table, every layer's memory and every layer's blast
  radius.
