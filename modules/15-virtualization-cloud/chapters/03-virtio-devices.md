# Chapter 03 — Devices: virtio, vhost, SR-IOV

> **The 30-second version.** Chapter 01 gave the CPU a guest mode, chapter 02 gave memory a second page table; devices are the third illusion, and the naive version is slow: emulate a real NIC (QEMU's e1000) and every register the guest's driver pokes is an EPT violation, a ~1,000-2,000-cycle VM exit, and a detour into a QEMU userspace thread — one core caps around 50,000-100,000 packets/sec, 150-1,500x under line rate. virtio's answer: stop pretending. The guest runs a driver that knows it is virtual and shares a *ring* with the "device" — a descriptor table plus avail and used rings, all in the guest's own RAM — so whoever services the ring (a QEMU thread, a vhost kernel thread, or, with SR-IOV, the NIC's DMA engine behind an IOMMU's third page table) moves data by reading and writing guest memory directly. This chapter climbs that ladder, then spends the winnings on live migration — which works precisely because the rings make device state mostly "guest memory," and which SR-IOV breaks, the trade that shapes cloud tiering.

## One packet, thirty exits: the price of pretending

The obvious design is honesty in the other direction: don't change the guest, *pretend to be hardware*. QEMU models Intel's e1000 NIC; an unmodified guest boots its stock e1000 driver and talks to the "card" exactly as to silicon. Zero guest changes is why emulation still owns rescue ISOs and anything without a virtio driver.

Now price it. The e1000 driver converses through MMIO registers (TDT, RDT, ICR — transmit/receive descriptor-tail pointers, interrupt cause) and DMA (the card reads descriptor rings and packet buffers from guest RAM). Chapter 02 derived the trap in channel 1: the e1000's register block is mapped into the guest's "physical" space with EPT entries marked non-present, so every register access is an EPT violation — a VM exit of ~1,000-2,000 cycles, chapter 01's number and the one that drives this chapter.

Count the exits for one bidirectional 64-byte packet: TDT poke, interrupt mask/unmask, ICR acknowledge, RDT post, status reads — realistically 10-20 register accesses, each a full exit-and-return handled by a QEMU *userspace* thread that decodes and emulates the register. Add 2 context switches per userspace exit plus the packet copy: 30,000-80,000 cycles per packet. On a 3 GHz vCPU that is roughly 40,000-100,000 packets/sec per core. A 10 GbE NIC does 64-byte line rate at 14.88 Mpps (module 13 ch01); 100 GbE does 148.8 Mpps. Emulation is 150x-1,500x slower — two to three orders of magnitude, the number that motivates this chapter. The diagnosis is precise: per-register traps times register chatter the guest driver was never designed to minimize.

The fix is not to emulate harder. It is to change the contract.

## virtio: drop the costume, keep the contract

Paravirtualization is chapter 01's CPU move applied to devices: the *guest cooperates*, booting a driver that knows it talks to a hypervisor. The device appears on the PCI bus with vendor ID 0x1af4; legacy device IDs occupy 0x1000-0x103f (net = 0x1000, blk = 0x1001), while modern virtio 1.x IDs are 0x1040 plus the spec's device type (network = type 1, so modern virtio-net shows as 0x1041). Linux grew the drivers in 2.6.25 (2008). Every vendor invented one — vmxnet3 (VMware), netfront (Xen), hv_netvsc (Hyper-V) — and virtio won the standardization war (virtio 1.0, OASIS, 2016; packed rings in 1.1, 2019).

The contract has two moving parts: **shared memory for data, doorbells for signals**. All data — packet bytes, disk blocks — lives in the guest's own RAM; the device, whoever implements it, reads and writes that memory *directly through the guest's memory map*. No trap is needed to move payload bytes. Chapter 02's balloon driver was secretly family: a virtio device whose "data" is control messages asking the guest for pages back.

The two doorbells are profoundly asymmetric:

- **The kick** (guest to device: "work posted") is a 2-byte write to a notify register in a PCI BAR — a *guest instruction touching trapped memory*: a PIO-instruction exit, or an EPT violation for an MMIO BAR (chapter 02). Observing a store the vCPU just executed requires trapping it — a VM exit by construction. What ioeventfd changes is *who handles it*: KVM recognizes "this write matches a registered eventfd," signals it, and re-enters the guest — QEMU never runs; the exit shrinks to an in-kernel detour.
- **The interrupt** (device to guest: "work done") costs *no exit*. The device writes the used ring (plain guest RAM) then raises the queue's MSI-X vector by writing an *irqfd* — an eventfd KVM has bound to that vector. KVM latches the vector in the virtual local APIC and delivers it at the next vmentry, whatever the vCPU was doing. Guest-generated events must trap to be seen; host-generated events are state you push in. That asymmetry is half of why virtio is cheap.

Before rings run, driver and device negotiate feature bits (EVENT_IDX = bit 29 for doorbell suppression, VERSION_1 = bit 32 for the 1.0 layout) — the mechanism that kept virtio compatible across 15+ years without breaking 2009 guests.

## The vring, byte by byte: the ring is the shared memory

Derive the structure instead of quoting the spec. (1) No copying per buffer into a hypercall, or every packet pays a trap — buffers live in shared guest memory. (2) Operation is asynchronous and batched — work queues up without round trips. (3) Memory is bounded — a fixed-size ring with wraparound. (4) Neither side spins — two doorbells. Four requirements, and you have reinvented the virtqueue: descriptor table, avail ring, used ring. Here is one transmit queue with 256 entries (queue size is a power of two, at most 32768 per virtio 1.x; QEMU's virtio-net default is 256):

```text
one virtqueue, qsz = 256 — all addresses are guest-physical (GPA)

GPA 0x1000  DESCRIPTOR TABLE — qsz * 16 = 4096 B
            +0x0000  desc[0]:
                       +0x00  le64 addr = 0x53000   -> GPA of a packet buffer
                       +0x08  le32 len  = 1514      -> length of that buffer
                       +0x0c  le16 flags= 0x0002    -> 1=NEXT  2=WRITE  4=INDIRECT
                       +0x0e  le16 next = 1         -> desc[1] if NEXT is set
            +0x0010  desc[1] ...                    -> 16 B each
            ...
            +0x0FF0  desc[255]                      -> chained descs = scatter-gather

GPA 0x2000  AVAIL RING — 2 + 2 + 2*qsz + 2 = 518 B  (guest WRITES, device reads)
            +0x000  le16 flags = 0                  (bit 1 = NO_INTERRUPT)
            +0x002  le16 idx   = 8                  free-running count of heads
                                                      posted; position = idx mod 256
            +0x004  le16 ring[0..255]:              head descriptor ids
                      ring[0..7]  = the 8 heads posted so far
                      ring[8..255]= free slots
            +0x204  le16 used_event = 5             (EVENT_IDX: "no interrupt until
                                                      used.idx passes 5")

GPA 0x3000  USED RING — 4 + 4 + 8*qsz + 4 = 2060 B  (device WRITES, guest reads)
            +0x000  le32 flags = 0                  (bit 1 = NO_NOTIFY)
            +0x004  le32 idx   = 5                  free-running count of heads done
            +0x008  used[0..255], 2 words each:
                        le32 id  = 4                which head completed
                        le32 len = 1514             bytes actually transferred
            +0x808  le32 avail_event = 8            (EVENT_IDX: "no kick until
                                                      avail.idx passes 8")

the packet itself: GPA 0x53000, 1514 B — an ordinary guest buffer,
referenced only by descriptor 0's addr/len

total span: 0x3000 + 2060 - 0x1000 = 10252 B — the whole queue fits in 11 KB
```

The handshake: the guest fills descriptors (chaining several for scatter-gather — header plus data, linked by `next`), writes the *head* id into `avail.ring[idx mod 256]`, increments `avail.idx`, kicks once. The device reads heads in order, DMAs (or memcpys — same contract) each `addr`/`len`, then writes `id` and the byte count into `used.ring[used.idx mod 256]`, increments `used.idx`, raises the interrupt; the guest's handler frees buffers and recycles descriptors. Batching falls out free — 8 descriptors, one kick; 8 completions, one interrupt — and `used_event`/`avail_event` (EVENT_IDX, bit 29) suppress the other side's doorbell until an index threshold, the same lever as NIC interrupt moderation.

Two invariants are load-bearing. Single-writer discipline: the guest writes descriptors and avail, never used; the device writes used, never avail — no locks, one writer per structure. And everything the device reads (ids, addrs, lens) is **guest-written memory parsed by privileged host code**: the device must bounds-check every `id < qsz` and every `addr/len`, because a malicious guest hands it attacker-controlled bytes. The descriptor table is the security boundary — every virtio escape bug lives there. One wart you meet when implementing a device: the avail ring's words are 16-bit, the used ring's 32-bit — a 2008 spec asymmetry that makes used entries 8 B, not 4.

Say the lineage out loud, because it is one idea with three names: a NIC's DMA descriptor rings (module 13 ch01), io_uring's SQ/CQ rings (module 13 ch04), and this vring. Fixed-size power-of-two arena, free-running indices, two doorbells, one writer per structure — the vring is the NIC ring with the hardware removed; io_uring is the vring moved into the kernel for processes instead of VMs.

## The implementation ladder: userspace thread, kernel thread, silicon

The ring is a *contract*, not an implementation — its superpower. Anyone who can map the guest's memory can be the device. The performance story is moving the ring's servant off the slow path, one rung at a time:

```text
WHO SERVICES THE RING (sending one burst of packets)

rung 1  QEMU userspace (virtio, or e1000 emulation)
   guest kick: PIO/MMIO write
     -> VM exit (~1,000-2,000 cycles)
     -> KVM returns to a QEMU userspace thread (2 context switches)
     -> QEMU walks the vring via its GPA->host map, memcpys the packets,
        write()s them to /dev/tap (one more switch)
   completion: tap wakes QEMU, QEMU writes the used ring, signals irqfd

rung 2  vhost-net (the dataplane is a kernel thread)
   guest kick: PIO/MMIO write
     -> VM exit, but KVM sees a registered ioeventfd: signal the eventfd,
        re-enter the guest. QEMU never runs. No context switch.
     -> the vhost kernel thread (has the guest's RAM mapped) wakes,
        reads the vring, moves the packet through an in-kernel tap socket
   completion: vhost writes the used ring, writes the irqfd
     -> KVM injects MSI-X. The packet never leaves the kernel.

rung 3  SR-IOV (the dataplane is silicon — next section)
   guest VF driver writes descriptors into the NIC's own rings
     -> an ordinary store to a BAR: no exit, no hypervisor anywhere
   completion: the VF raises MSI-X straight into the guest
```

Rung 1 is QEMU's default and already 10-50x better than e1000, because the ring amortizes the exit: one kick and one interrupt per *batch* instead of 10-20 traps per *packet*. The remaining tax: each kick exits and round-trips a userspace thread that calls back into the kernel for the tap.

Rung 2, vhost-net (Linux kernel module, 2010), deletes userspace from the data path. At startup QEMU hands over the rings via `/dev/vhost-net` — `VHOST_SET_OWNER`, `VHOST_SET_MEM_TABLE` (the GPA-to-host map), per queue `VHOST_SET_VRING_NUM/ADDR/BASE`, `VHOST_SET_VRING_KICK` (an ioeventfd), `VHOST_SET_VRING_CALL` (an irqfd), `VHOST_NET_SET_BACKEND` (bind the tap). Then: a kick exits, KVM signals the ioeventfd and re-enters; the eventfd wakes a dedicated vhost kernel thread (pinned away from the vCPUs); that thread — same vring contract — moves packets between guest RAM and the tap socket entirely in kernel space, completing with a used-ring write plus an irqfd write that KVM turns into an MSI-X injection. The exit per kick becomes an in-kernel detour; the completion path never exits the kernel. Typical gain over userspace virtio: 3-5x. QEMU stays as the *control plane* — configuration, PCI emulation, live migration, virtio-net's control queue (MAC and offload changes): data plane in the kernel, control plane in userspace.

Rung 2.5, vhost-user, points the move sideways: the dataplane in *another userspace process* — Open vSwitch or a DPDK app — controlled over a UNIX socket with the rings mapped in. Poll mode deletes even the kick's exit, at the price of a spinning core — the same doorbells-vs-spinning trade io_uring's SQPOLL makes one level down.

## SR-IOV and the third page table

The ladder's endgame: stop servicing the guest's network at all — hand it real hardware. SR-IOV (Single Root I/O Virtualization, PCI-SIG, 2007) lets one Physical Function carve itself into Virtual Functions: VFs are genuine PCI functions with their own descriptor queues, BARs, and MSI-X vectors — up to 256 per PF by the spec's ceiling (real NICs expose 16-128; Intel's 82599 did 64). A VF is assigned via VFIO: the host binds it to `vfio-pci`, QEMU opens `/dev/vfio/N`, and the guest boots its *unmodified vendor driver* against the VF. Performance is native — zero exits in the data path, DMA at line rate, poll-mode drivers in the guest push latency to tens of microseconds. The 14.88 Mpps gap is closed completely.

But the guest's driver writes buffer addresses it believes are physical, and if the VF's DMA engine took them literally, a malicious guest would DMA over the host kernel. For the PC's first ~30 years (ISA 1981 until Intel VT-d chipsets in 2008) that was no hypothetical: any card could DMA anywhere, and FireWire was the demo — the IEEE 1394 OHCI "physical response unit" let any peer DMA host RAM directly, which passerby laptops used in 2008 to patch the login check on locked Windows and macOS machines. Physical DMA was a *supported feature* for a decade before anyone treated it as an attack.

The IOMMU (Intel VT-d, AMD-Vi) is the fix — literally a third page table. Count them: the guest's own page table (guest-virtual to guest-physical, walked by the CPU in guest mode), the EPT (guest-physical to host-physical, walked by the MMU, chapter 02), and the IOMMU's tables (I/O virtual addresses — *iova* — to host-physical, walked by the device's DMA engine; module 12 ch03's GMMU is the same invention grown for GPUs). QEMU's VFIO layer maps iova = guest-physical for exactly that VM's pages, so the guest keeps writing "physical" addresses and a third translator quietly reinterprets them — while *confining* the device: DMA outside the mapped pages faults. That confinement is the device isolation the PC lacked for 30 years, and what makes handing hardware to an untrusted guest survivable. Note the asymmetry: the CPU's path walks two tables, the device's one — device traffic doesn't compound chapter 02's nested-walk costs.

The costs are operational. VF configuration belongs to the PF driver: creating VFs is a sysfs write (`echo 8 > /sys/bus/pci/devices/<PF>/sriov_numvfs`, bounded by `sriov_totalvfs`). And the big one: **live migration breaks**. The VF's queue state, DMA mappings, and interrupt config live in *silicon on the source host*; the target cannot conjure an identical function mid-flight — at cutover the datapath vanishes. Hence the architecture rule stated bluntly: **no SR-IOV for the migratable tier**. The escape hatch is the bond pattern (Linux's net_failover driver, 2018-2019): pair the VF with a virtio "standby" NIC; before migration, flip the datapath onto virtio, migrate, flip onto the target's VF. Native speed when static, migratable when needed — switching rungs on demand.

## Live migration: moving a program that will not stop writing

Migration is the payoff for everything above: move a running VM between hosts — drains, load balance, power — with downtime of tens of milliseconds. State to move: guest RAM (16 GB here), vCPU state (VMCS fields, chapter 01), and *device state*. RAM is the hard part because the guest keeps writing it while you copy.

**Pre-copy** is the classic loop. Pass 1: bulk-stream guest RAM to the target while the guest runs — 16 GB over a 10 Gbps link is about 16 s at ~1 GB/s effective. Meanwhile KVM enables *dirty logging*: the EPT write bits for guest RAM are cleared, so the guest's writes cause EPT violations (chapter 02's MMIO trap, weaponized differently — there it caught device-register writes, here the guest writing its own memory), each fault marking a dirty bitmap bit before the write goes through. Pass 2 copies only pages dirtied during pass 1; pass 3 what was dirtied during pass 2 — each pass smaller, *if* the guest dirties slower than the link copies.

Convergence is one inequality. A pass copying set S over bandwidth B takes S/B seconds, during which the guest dirties at rate d; the next set is roughly S·(d/B). If d < B the dirty set decays geometrically to zero; if d ≥ B it never shrinks. Worked example: 16 GB guest, dirty rate 5 GB/s (a build daemon cycling all its memory every ~3 s), 10 Gbps link ≈ 1 GB/s effective. Ratio 5 > 1: after the 16 s first pass, every page has been rewritten — the next pass is 16 GB again, forever. **It never converges.** The same guest with a W^X-shaped workload (code pages written once at boot, a few hundred MB/s of heap churn) decays at ratio ~0.3 and is ready in 2-3 passes. "What's the dirty rate versus the link?" is the insider's *first* triage question on a stuck migration — QEMU 6.0+ has `calc-dirty-rate` to measure it live. Escape valves: auto-converge (throttle the vCPUs — manufacturing chapter 01's steal time — until d < B), XBZRLE (delta-compression), or post-copy. The **cutover**: pause the vCPUs, copy the final dirty set plus device state, resume on the target — downtime = final-set/B, which must fit the limit you set (QEMU's default: 30 ms).

**Post-copy** inverts the order: resume the guest on the target *first*. Every un-transferred page faults — an EPT violation on the target, resolved by fetching that 4 KB page over the network from the source. No convergence problem, near-instant cutover — and an unrecoverable failure mode: if the network dies, the VM dies, because most of its memory exists only at the source (which must keep it pinned). Insiders price it in one sentence: *the VM's memory IS the network*. Fleets use it for huge, dirty guests that pre-copy can never converge.

Last piece: **what even is the state of a disk controller?** For an emulated e1000: hundreds of registers, timer state, in-flight DMA — order-sensitive internal state QEMU serializes as a per-device "vmstate" blob. For virtio: queue addresses, feature bits, a status byte — a few hundred bytes — because the *data* lives in the guest's rings, self-describing, in guest RAM. That is the quiet reason virtio's clean model made migration practical at fleet scale. The ladder complicates it once: a vhost thread's state (ring positions, in-kernel socket) can't cross hosts, so at migration start QEMU stops vhost, reads the ring state back (`VHOST_GET_VRING_BASE`), migrates as plain userspace virtio, re-attaches vhost on the target — with a visible 3-5x network dip for the duration. SR-IOV's state is in silicon and cannot be serialized at all — which is precisely why it cannot migrate.

## What insiders know

- **PCI IDs are a compatibility oracle.** `lspci -nn` showing `1af4:1000` means legacy virtio; `1af4:1041` means modern virtio-1.x net. Pre-2009 guests and rescue ISOs boot only on `1af4:1000` or a full e1000 — the one task emulation still earns. Windows guests need the separate virtio-win driver disk; forgetting it is the classic Windows-on-KVM first-week mistake.
- **`idx` is free-running; the position is `idx & (qsz-1)`.** The counters run 0-65535 and wrap; the homegrown-device bug is subscripting the 256-entry table with `idx` directly — out of bounds within the first 256 operations. Power-of-two sizes exist so wraparound is a mask, not a division.
- **EVENT_IDX (bit 29) is the interrupt-moderation bit.** Without it, a guest at 1 Mpps burns a doorbell storm — one interrupt per completion at the finest granularity. `used_event`/`avail_event` are the same lever as NIC coalescing, and the same tuning question.
- **The migration vhost dip is expected, not a bug.** vhost state can't cross hosts, so the dataplane falls back to userspace for the migration's duration — a 3-5x network slowdown on every migrated vhost guest. Migrate in low-traffic windows.
- **`sriov_numvfs` is THE knob.** `echo N > /sys/bus/pci/devices/<PF>/sriov_numvfs`, bounded by the device's `sriov_totalvfs`; the PF driver owns VF MACs and VLAN filters. An EINVAL usually means you asked for more than TotalVFs or the PF driver caps it lower.
- **One virtqueue pair per vCPU is the multi-queue setup.** `ethtool -l eth0` shows the combined channels; VIRTIO_NET_F_MQ lets `ethtool -L eth0 combined N` change them live. `/proc/interrupts` names the vectors `virtio0-input.0`, `virtio0-output.0` — a single-queue guest at 10 Gbps bottlenecks a vCPU in softirq before the wire does.

## See it yourself

Most readers of this module are *inside* a VM right now — that is the lab.

```console
$ systemd-detect-virt
kvm                        # "kvm" = KVM guest, "qemu" = TCG emulation, "none" = bare metal
$ lspci | grep -i -E 'virtio|sriov'
00:03.0 Ethernet controller: Red Hat, Inc. Virtio network device
$ lspci -nn | grep -i virtio
00:03.0 Ethernet controller [0200]: Red Hat, Inc. Virtio network device [1af4:1041]
```

`1af4:1041` is modern virtio-1.x; `1af4:1000` would be legacy (older QEMU or a compat flag). No virtio line at all: check what `lspci` *does* show — an emulated model, or a passed-through VF.

```console
$ ethtool -i eth0
driver: virtio_net
version: 1.0.0
bus-info: 0000:00:03.0
$ ls /sys/bus/pci/devices/0000:00:03.0/virtio0/
$ ls /sys/bus/virtio/devices/
virtio0  virtio1  virtio2  virtio3
$ grep virtio /proc/interrupts
 27: ...  virtio0-config
 28: ...  virtio0-input.0
 29: ...  virtio0-output.0
$ modinfo virtio_net | head -n 4
filename:   /lib/modules/.../virtio_net.ko
version:    1.0.0
```

What to notice: the PCI function *contains* a virtio device (the `virtio0` subdirectory — PCI is just the transport), and each queue pair has its own MSI-X vector line. You are reading the contract from the guest end; on the host side those doorbells are `KVM_IOEVENTFD`s.

With a host that has KVM, run a tiny guest with virtio (user networking needs no root) and watch QEMU bind the doorbells:

```console
$ strace -f -e trace=ioctl,eventfd2 qemu-system-x86_64 -enable-kvm -m 1G \
    -netdev user,id=n0 -device virtio-net-pci,netdev=n0 -drive file=d.qcow2 2> qemu.log
$ grep -cE 'KVM_IOEVENTFD|KVM_IRQFD' qemu.log
6
```

Each `KVM_IOEVENTFD` is a kick register turned into an eventfd; each `KVM_IRQFD` an interrupt vector. Add a tap device with `vhost=on` (needs root) and the log grows the rung-2 handover: `VHOST_SET_OWNER`, `VHOST_SET_MEM_TABLE`, `VHOST_SET_VRING_ADDR/BASE/KICK/CALL`, `VHOST_NET_SET_BACKEND`. Recent strace decodes these names; older versions print raw `_IOC` numbers — then the `eventfd2` count is the fallback signal.

The paper lab — migration arithmetic, worked. 16 GB guest, dirty rate 5 GB/s, 10 Gbps link ≈ 1 GB/s effective. First pass: 16 GB / 1 GB/s = 16 s, during which the guest dirties up to 5 GB/s x 16 s = 80 GB — but only 16 GB exist, so every page is dirty again; pass 2 copies 16 GB; the sequence never shrinks. Does it converge? **No.** Pre-copy needs d < B: this guest needs a ~50 Gbps effective link to make progress, auto-converge throttling its vCPUs below ~1 GB/s of dirt, or post-copy with its partition risk. Ratio, threshold, remedy — the whole triage in three lines.

## How to use this knowledge

- **The "network is slow in this VM" runbook.** Identify the rung first: `ethtool -i` / `lspci -k` (virtio_net vs an emulated driver). Emulated means per-packet exits — confirm with `kvm_stat`'s exit-reason counts (chapter 01's tool) under load. Virtio but slow: check `ethtool -l` — one queue pair on a 16-vCPU guest bottlenecks a core in softirq; raise it with `ethtool -L`.
- **Fleet tiering and migration feasibility.** The migratable tier runs virtio + vhost and eats the 3-5x dip during migrations; the pinned latency tier gets SR-IOV and drains by restart — or runs the net_failover bond to buy both. Before any drain, measure the dirty rate (`calc-dirty-rate`) and divide by effective link bandwidth: a ratio above 1 tells you *before* the 2 a.m. window that pre-copy will never converge — and which lever (throttle, compress, post-copy, restart) fits the SLA.
- **Interview whiteboards you can now draw cold:** the e1000 ceiling derivation (~50k-100k pps per core, 150-1,500x off line rate); the vring with byte offsets; the three page tables (guest PT, EPT, IOMMU) and who walks each — the CPU walks two, the device one.
- **Reading device-CVE writeups.** A virtio descriptor table is guest-written memory parsed by privileged host code — a missing bounds check on `id` or `len` is a guest-to-host escape primitive. When the next QEMU or vhost CVE lands, you know which invariant failed and which ring word was trusted.

## Connections

- **Module 15 ch01** — the ~1,000-2,000-cycle exit cost is the currency this chapter spends; exit reasons are the device path's trap doors; auto-converge deliberately manufactures steal time.
- **Module 15 ch02** — the kick is that chapter's MMIO-as-page-fault trick, inverted for dirty logging; the balloon is itself a virtio device.
- **Module 15 ch04** — a container's network is a veth pair into a bridge; a VM's is a virtio ring into a tap — same wire, different depth of illusion.
- **Module 15 ch05** — Firecracker's device model is virtio-net/blk plus a serial console (~50 kLOC vs QEMU's ~2 MLOC of attack surface); its snapshot-resume is this chapter's migration frozen to a file.
- **Module 13 ch01 + ch04** — the lineage: NIC DMA rings, io_uring's SQ/CQ, the vring — one producer/consumer design, three deployments; EVENT_IDX is the same lever as interrupt moderation and SQPOLL coalescing.
- **Module 12 ch03** — the GPU's GMMU and the IOMMU are the same invention: a page table walked by the device, because devices can't be trusted with physical addresses.
- **Module 01 ch03** — three translators now stand between a program and its bytes (process PT, EPT, IOMMU); dirty logging's write-protect-and-collect-faults is the machinery of copy-on-write.

## Quiz

1. A 3 GHz vCPU pushes 64-byte packets through QEMU's emulated e1000. Assume ~15 VM exits per bidirectional packet (TX tail poke, RX buffer post, interrupt raise/ack, status writes), ~1,500 cycles per exit round trip, and ~10,000 cycles of QEMU userspace emulation per packet. What is one core's packets/sec ceiling, and how many times below a 10 GbE NIC's 64-byte line rate (14.88 Mpps) is it?

2. The kick (guest to device) costs a VM exit; the interrupt (device to guest) costs none. Explain the asymmetry from first principles — mechanically, what is each event, and why must one trap while the other cannot?

3. A split vring has queue size 128. Compute (a) the descriptor table's byte size, (b) the avail ring's size including the `used_event` word, (c) the used ring's size including `avail_event`, and (d) in the legacy layout (descriptor table first, avail immediately after, used aligned up to a 4096-byte boundary), the total span from descriptor-table start to used-ring end.

4. An 8 GB guest dirties memory at a steady 400 MB/s; the migration link sustains 1 GB/s effective. Does pre-copy converge? Roughly how many passes until the remaining dirty set is under 30 MB, what total copy time, and what downtime does the final pass imply against QEMU's default 30 ms budget?

5. Every time ops live-migrates a vhost-backed VM, its network throughput drops from ~40 Gbps to ~8 Gbps for the migration's duration, then fully recovers. Nothing is broken. What is the mechanism?

6. A latency-critical tenant runs with an SR-IOV VF. The host is drained via live migration; at cutover, every TCP connection through the VF resets. What died, mechanically, and what are the two standard escapes from this trade?

7. You write a test "device" — a userspace loop implementing the vring correctly but never polling and never sleeping on the kick's ioeventfd. The guest driver posts 8 descriptors and bumps `avail.idx` from 8 to 16. Your device sees nothing. Why?

8. Map the split vring onto io_uring (module 13 ch04) part by part; name the structural piece each has that the other lacks, and state the backpressure rule they share.

<details><summary>Answers</summary>

1. **~92,000 pps, ~160x below line rate.** 15 exits x 1,500 cycles = 22,500 cycles, plus 10,000 cycles of userspace emulation = 32,500 cycles per packet. 3 x 10^9 / 3.25 x 10^4 ≈ 9.2 x 10^4 packets/sec. Gap: 14.88 Mpps / 92 kpps ≈ 162x; against 100 GbE line rate (148.8 Mpps), ~1,600x — three full orders.

2. **A kick is a guest instruction; an interrupt is host-side state.** The kick is a store the vCPU executes to trapped memory (PIO exit, or EPT violation on an MMIO BAR — chapter 02's mechanism); observing a store the vCPU just executed requires surfacing it *at that instruction*, and the only mechanism is a VM exit — ioeventfd merely ensures KVM, not QEMU userspace, handles it. The interrupt flows the other way: the device writes an irqfd, KVM latches the vector in the virtual local APIC, delivery happens at the next vmentry — pushing state into the guest needs no trap, because nothing in the guest needs observing. One is observed by trapping; the other is injected.

3. **(a) 2,048 B, (b) 262 B, (c) 1,036 B, (d) 5,132 B.** (a) 128 x 16 = 2,048. (b) 2 (flags) + 2 (idx) + 2 x 128 (ring) + 2 (used_event) = 262. (c) 4 (flags) + 4 (idx) + 8 x 128 (id+len pairs) + 4 (avail_event) = 1,036. (d) desc + avail = 2,310; the used ring starts at the next 4096 boundary, offset 4,096, ending at 4,096 + 1,036 = 5,132 bytes from the descriptor-table start. The 4096 alignment is a legacy-layout requirement (modern virtio drops it).

4. **Converges: ~7 passes, ~13.3 s total, final pass ≈ 30 ms.** Decay ratio d/B = 0.4/1.0 = 0.4 < 1, so the dirty set shrinks geometrically: after pass k it is about 8 x 0.4^k GB. 8 x 0.4^k < 0.03 GB needs 0.4^k < 0.00375, i.e. k > ln(0.00375)/ln(0.4) ≈ 6.1 — 7 passes. Total copy time: 8 + 3.2 + 1.28 + 0.51 + 0.21 + 0.08 + 0.03 ≈ 13.3 s. The final ~30 MB at 1 GB/s ≈ 30 ms of cutover downtime — right at QEMU's default 30 ms budget; one more pass (~12 ms of dirt) buys margin.

5. **The vhost dataplane can't cross hosts, so it falls back one rung.** At migration start QEMU stops the vhost kernel thread and reads its state back (`VHOST_GET_VRING_BASE`) — an in-kernel thread's ring positions and socket state can't be serialized and moved. Until migration completes, QEMU services the rings in userspace, paying the rung-1 cost (exit + 2 context switches + userspace work per kick) instead of rung 2's — the 3-5x drop. It is the ladder's expected toll, which is why migrations run in low-traffic windows.

6. **The datapath's backing hardware did not move with the vCPUs.** The VF's queues, DMA mappings, and MSI-X state live in the source host's NIC silicon; the target has no identical function holding that state, so at cutover the guest's driver is talking to hardware that no longer exists — packets black-hole, TCP times out and resets. Escapes: (1) policy — the SR-IOV tier doesn't live-migrate; drain by restarting the tenant ("no SR-IOV for the migratable tier"); (2) the bond/net_failover pattern — pair the VF (primary) with a virtio standby, flip the datapath to virtio before cutover, migrate, flip onto the target's VF after.

7. **Ring entries are state, not signals.** The only event the contract guarantees the device is the kick — the write to the notify register — and the guest driver didn't send one: drivers batch (post 8 buffers, kick once), so posting descriptors plus bumping `avail.idx` produces no observable event unless the device polls. A device that neither polls (busy-poll mode, a DPDK-style optimization) nor sleeps on the kick's ioeventfd is deaf to the only doorbell the spec promises. The ring is the database; the doorbell is the interrupt.

8. **avail ↔ SQ index ring; descriptor table ↔ SQE array; used ↔ CQ ring.** The avail ring is an array of indices into a descriptor array — exactly io_uring's submission index ring pointing into the SQE array; the used ring is a fixed-size completion array published by the consumer — exactly the CQ ring. The vring's extra piece: descriptor *chaining* (scatter-gather via `next`, plus the INDIRECT second level), where io_uring has one fixed-size SQE per operation. io_uring's extra piece: the 64-bit `user_data` tag carried to completion, where the vring identifies a completion only by head descriptor id. Shared backpressure rule: the producer's free-running index minus the consumer's index must stay strictly less than the ring size — hence both are powers of two (mask with qsz-1), and both suppress the quiet-direction doorbell (EVENT_IDX's event words vs SQPOLL/coalescing).

</details>
