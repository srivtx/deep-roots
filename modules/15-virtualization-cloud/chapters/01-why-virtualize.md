# Chapter 01 — The virtualization problem and the CPU's new mode

> **The 30-second version.** An OS multiplexes untrusted programs because
> the CPU enforces a trap boundary: user code runs at full speed, but the
> moment it touches shared machine state the instruction traps and the
> kernel arbitrates. A hypervisor needs the same deal one level down —
> multiplexing untrusted *operating systems* — but a kernel lives at
> ring 0, where nothing traps: boot two OSes at ring 0 and the first `hlt`
> or page-table write bricks the machine. Popek and Goldberg proved in
> 1974 that virtualization is clean exactly when every *sensitive*
> instruction traps; x86 broke that rule for 20 years (`SGDT` runs
> unprivileged and tells the truth, `POPF` runs and silently lies), which
> built three industries: VMware rewrote the guest's code, Xen rewrote the
> guest kernel, and Intel/AMD fixed the CPU — VT-x (2005) / AMD-V (2006)
> added a real "guest mode" (VMX non-root) whose exit machinery — a 4 KB
> control block per vCPU, exit reason codes, ~1,000–2,000-cycle round
> trips — is the substrate under every VM and cloud instance you have
> ever used.

## One trap boundary short of a hypervisor

An OS multiplexes untrusted programs cheaply because the hardware splits
the world with a trap boundary: user code runs at ring 3 at full native
speed, but the moment it touches machine-wide state (`hlt`, `lgdt`,
`mov cr3, rax`, `wrmsr`, port I/O) the instruction faults instead of
executing, control jumps to ring 0, and the kernel decides what "really"
happened. The expensive event is rare — user code touches shared state
maybe once per few thousand instructions.

Invert the trust. A hypervisor's customer is not an untrusted program; it
is an untrusted *operating system*. An OS executes privileged instructions
constantly: every context switch writes CR3 (module 01 ch03), every idle
loop executes `hlt`, boot is `lgdt`/`lidt`/`wrmsr`. A kernel insists on
ring 0 — that is what "kernel" means to the CPU — and there is one ring 0
per core, one GDTR, one IDTR, one CR3.

Run two kernels at ring 0 and it dies fast: kernel B's boot writes CR3
with its own page-table root, which the CPU honors *without trapping* —
the real translation for the whole machine is now B's, kernel A vanishes
from the address space, and A's next fault walks B's tables into a triple
fault. Or B's idle path `hlt`s and parks the core with interrupts routed
through a dead vector table. Not a bug in either kernel: ring 0 is not a
place, it *is* the boundary, and code at it has no more-privileged level
to trap to. No traps, no arbitration, no multiplexing. The obvious dodge —
x86 has 4 rings, so park the guest kernel at ring 1 — almost works: at
ring 1 the guest's privileged instructions *do* trap. Whether that
suffices is a theorem with a hole in it.

## The 1974 theorem: trap-and-emulate is enough

Popek and Goldberg, "Formal Requirements for Virtualizable Third
Generation Architectures" (CACM, July 1974), asked when a machine can host
a virtual machine monitor (VMM) and required three properties:
**fidelity** (guest code, including a whole OS, behaves indistinguishably
from bare metal, timing aside), **efficiency** (innocent instructions
execute directly on the real CPU — nothing is interpreted on the fast
path), and **resource control** (the VMM, not the guest, owns every shared
resource).

Their theorem sorts instructions into privileged (trap at CPL > 0: `lgdt`,
`hlt`, `mov cr3`), **sensitive** (read, write, or depend on machine-wide
state — page-table roots, interrupt flags, descriptor tables; what a VMM
must never let slip past unnoticed), and harmless (everything else). An
architecture is virtualizable **iff all sensitive instructions are
privileged** — iff every sensitive instruction traps. When that holds, the
VMM's job is almost boring: run the guest unmodified at a ring below the
hypervisor, let harmless code execute naked, and treat each trap as a
message — *the guest tried to touch shared state*. The VMM emulates the
effect on **virtual state** (a fake GDT in guest memory, a virtual CR3
recorded in the VMM, a software pending-interrupt bit) and resumes the
guest, which never knows. It is the syscall contract one level down: trap
= "untrusted code touched something shared", hypervisor = the arbiter.

IBM ran whole guest OSes by trap-and-emulate on the System/360 Model 67
(CP-67, 1967; VM/370 productized 1972) — Popek and Goldberg wrote down why
it worked. x86 then failed their test for two decades.

## Two instructions held x86 hostage

x86 gained rings with the 286 (1982) and rings-plus-paging with the 386
(1985) — and until 2005 it violated the theorem, because two instruction
families are *sensitive without being privileged*. These are THE textbook
examples; name both.

**`SGDT` — store GDT register.** Writes the real GDT's limit and base
into a memory operand (6 bytes in protected mode, 10 in long mode:
2-byte limit + 8-byte base). It executes at *any* privilege level, no
trap — ring 3 code can run it. Yet its output is machine-global state:
the true linear address of the one real GDT. In trap-and-emulate, the VMM
intercepts `lgdt` (privileged: it traps, the VMM installs a *virtual*
GDT) but never sees `sgdt` execute, so the guest reads the real address
unmediated. Fidelity dies two ways: detection — compare GDTR's value
against what bare metal would show and conclude "I'm virtualized" (the
"red pill" trick, 2004, used sibling instruction `sidt` this way against
VMware) — and correctness, since the guest computes from descriptor-table
numbers the VMM never sanctioned. The load form behaves; the store form is
the traitor.

**`POPF` — pop flags.** Pops EFLAGS from the stack. At CPL 0 it sets IF
(the interrupt flag) and IOPL — resource-controlling state. At any CPL > 0
the CPU silently *drops* attempts to change IF and IOPL: no trap, no
exception, the remaining bits pop normally. A guest kernel at ring 1 that
pushes a flags word with IF=0 and executes `popf` to disable interrupts
gets nothing — IF is unchanged. The guest *believes* interrupts are off,
enters its critical section, and a timer interrupt lands mid-way: silent
corruption, with no trap to tell the VMM it should have emulated a
disabled interrupt flag. The assumption "sensitive ⇒ traps" fails twice,
differently: `sgdt` executes and tells the truth; `popf` executes and
lies. On paper x86 was not virtualizable — until someone changed the code,
the guest, or the CPU.

## Three businesses, then the CPU was fixed

Each workaround makes "sensitive ⇒ trap" true again, and each was a
company.

**Business 1: fix the code — binary translation (VMware, 1999–2008).**
Founded 1998, first product 1999: don't wait for Intel. At runtime, scan
the guest's kernel-mode instruction stream, find the offending
instructions, and *replace* them with trap-able calls into VMM helpers
that produce the virtual answer (`sgdt` becomes "store the *fake* GDT
address"). Cache the translated blocks — a "recompiled" hot path — so each
kernel loop pays translation once, then runs at native speed. Guest
*user-mode* code is never translated at all: harmless by definition, it
runs naked at ring 3. The insight that made the business: only ~1% of
dynamically executed guest instructions needed rewriting (VMware's
measurements — sensitive instructions are rare even in kernel code, which
is mostly ordinary arithmetic).

**Business 2: fix the guest — paravirtualization (Xen, 2003).** Cambridge's
Xen (SOSP 2003) patched the guest kernel's source so it never issues the
offending instructions: a cooperating kernel makes *hypercalls* — the
syscall instruction aimed at the hypervisor — to request page-table
updates, interrupt control, and CPU information. No traps on the fast path
because the guest never does anything trap-worthy. Performance excellent;
the cost: a modified kernel. Linux grew `pv_ops` (mainline since 2.6.23,
2007) so one source tree boots native or paravirtualized — but Microsoft
would never ship a Windows patched for someone else's hypervisor, so
paravirtual Windows never existed. That asymmetry shaped Xen's market.

**Business 3: fix the CPU — VT-x (Intel, 2005) / AMD-V (2006).** Intel's
VMX gives the CPU a second mode bit, orthogonal to the rings: **VMX root**
(the hypervisor's world) and **VMX non-root** (the guest's world). In
non-root the guest kernel runs *at ring 0* — its instructions behave
exactly as on metal — but every sensitive event now causes a **VM exit**:
instead of taking effect silently, it transfers control to VMX root with
a reason code attached. Entering is explicit: `VMLAUNCH` (first entry) and
`VMRESUME` (every later entry). Popek-Goldberg now holds on x86: sensitive
⇒ exit. AMD-V (2006) is the mirror — "guest mode," different names, same
idea.

An honest footnote: first-generation VT-x chips (2005–2007) often *lost*
to binary translation — Adams and Agesen measured it (ASPLOS 2006): BT
trapped almost nothing, while early VT-x paid ~1,000+ cycles per exit.
VMware kept BT as default until roughly 2008–2009, when cheaper exits and
EPT (Nehalem, 2008–2009) tipped the balance. Hardware won, but not on day
one.

## Inside the exit: the VMCS, the world's most expensive function call

A syscall saves a couple of registers and jumps. A VM exit must preserve
*everything* — general registers, segment registers, RIP, RSP, RFLAGS,
control registers — and hand the hypervisor an arbitrated view of what
happened. The vehicle is the **VMCS**: a 4 KB control block (max size
published in MSR `IA32_VMX_BASIC`; 4 KB on every shipping x86 CPU), one
per vCPU, allocated by the hypervisor. It is the argument struct *and* the
return value of the world's most expensive function call:

```text
┌───────────────────────────────────────────────────────────────────────┐
│ VMX root — the hypervisor (KVM kernel code, CPL 0 in root mode)      │
│   prepare VMCS: guest RIP/RSP, CR3, execution controls               │
│   VMLAUNCH ──────────────────────────────────────────────────┐       │
└───────────────────────────────────────────────────────────────┼───────┘
                              CPU loads the guest-state area      │
│ (RIP, RSP, RFLAGS, segment regs, CR3...)                        ▼
┌───────────────────────────────────────────────────────────────────────┐
│ VMX non-root — the guest (kernel at CPL 0, apps at CPL 3)            │
│   runs at full native speed, unmodified, until something sensitive:  │
│   HLT · CPUID · IN/OUT · MOV CR3 · a page the EPT maps oddly         │
└───────────────────────────────────────────────────────────────┬───────┘
                               VM EXIT:                             │
│  — CPU saves all guest state → guest-state area                   ▼
┌───────────────────────────────────────────────────────────────────────┐
│ VMX root — same vCPU thread, now executing the hypervisor            │
│  switch (vmcs_read16(EXIT_REASON)) {                                 │
│    case 10: CPUID         → synthesize the virtual CPU's answer      │
│    case 43: HLT           → park the vCPU thread until an IRQ        │
│    case 12: EPT violation → fix the mapping, or hand MMIO to QEMU    │
│  }                                                                   │
│  VMRESUME ───────────────────────────────────────────────────►       │
└───────────────────────────────────────────────────────────────────────┘
One full lap: ~1,000–2,000 CPU cycles (≈ 0.3–0.7 µs at 3 GHz), before
the handler does any work.
```

The named parts are the VMCS's working areas: the **guest-state area**
(saved on exit, loaded on entry), the **host-state area** (loaded on
exit — where the hypervisor resumes, right after its VMLAUNCH/VMRESUME),
execution-control fields (which events exit at all), and the
exit-information fields: the 16-bit **exit reason** and the 64-bit
**exit qualification**, a reason-specific payload (for a page-fault exception
exit it carries the faulting linear address; for an EPT violation the
guest-physical address arrives in a dedicated field). On exit the CPU
loads the host state, the hypervisor reads the reason, emulates or fixes,
and re-enters with VMRESUME.

That lap cost is THE number of this module: a plain syscall is ~100–300
cycles; a VM-exit round trip is ~1,000–2,000 (honest range — varies by CPU
generation and by what you count; handler work goes on top). It is the
budget that drives every design in chapter 03: devices were reinvented to
move more data per exit.

Worth memorizing, because you will read them in every production trace:
**12 = EPT violation** (memory; chapter 02), **30 = I/O instruction**
(devices; chapter 03), **10 = CPUID** (the hypervisor owns the guest's
CPU identity), **43 = HLT** (idle), **7 = interrupt window** (the guest
just enabled interrupts and the hypervisor has one queued), **0/1 =
exception/external interrupt**. Numbering per Intel SDM Appendix B,
stable for over a decade.

A folklore correction: people say "the hypervisor runs at ring −1." No
such ring exists — rings are CPL 0–3, and VT-x adds a bit *orthogonal* to
them. The guest OS runs at ring 0 *inside non-root*; the hypervisor's code
runs at ring 0 *inside root*. The folklore survives because it compresses
a true ordering — control drops one level below the guest's own ring 0 —
but the mechanism is a mode switch with a 4 KB state block, not a ring.
The greppable truth: VMX root / VMX non-root.

## KVM, and the taxonomy that never was

The marketing taxonomy sorts hypervisors into **type-1** ("bare-metal":
ESXi, Xen, Hyper-V — booted first, owns the metal) and **type-2**
("hosted": an application on Windows/macOS, like VirtualBox). It pretends
to be about mechanism; it isn't — both use the same VMCS, exits, and mode
bit. The real question is *which code sits in VMX root*. KVM is the proof
the taxonomy is marketing.

**KVM** (Avi Kivity, Qumranet; mainline Linux 2.6.20, February 2007) makes
the *host kernel itself* the hypervisor: a loadable module pair — `kvm.ko`
(core, exposing `/dev/kvm`) plus `kvm_intel.ko`/`kvm_amd.ko` (VMCS and
exits) — runs in VMX root inside a normal Linux kernel, so everything
Linux already does (scheduling, page cache, NIC drivers) becomes the
hypervisor's supporting cast. Each VM is an ordinary userspace process —
**QEMU** — that mmaps the guest's RAM and creates one thread per vCPU. A
vCPU is a thread in a process: module 01 ch04's `task_struct`, scheduled
by the host like any other thread, and its whole life is the
`ioctl(KVM_RUN)` loop — "run the guest until it exits":

```c
/* QEMU's vCPU loop, reduced to the bones (Linux KVM interface) */
for (;;) {
    ioctl(vcpu_fd, KVM_RUN);   /* enter VMX non-root; returns ONLY on a VM exit */
    switch (run->exit_reason) {
    case KVM_EXIT_IO:    /* guest executed IN/OUT: run->io has port, size, dir */
        emulate_register(run->io);        /* the device model lives in QEMU   */
        break;
    case KVM_EXIT_MMIO:  /* guest wrote a register mapped in guest RAM space  */
        emulate_mmio(run->mmio.phys_addr, run->mmio.data);
        break;
    case KVM_EXIT_SHUTDOWN:
        return;          /* triple fault — the guest is beyond saving         */
    }
}   /* loop: ioctl(KVM_RUN) again → VMRESUME → the guest continues */
```

KVM handles what it can *in kernel* — CPUID, HLT, interrupt injection,
EPT faults: one round trip and straight back into the guest. What it
can't handle — emulating a device register — returns to QEMU, which
emulates and re-enters: same exit plus 2 context switches. That split
line — which exits are served in-kernel versus bounced to userspace — is
the whole performance story of chapter 03.

```text
   userspace (one process per VM)    kernel (the host kernel IS the
┌────────────────────────────┐       hypervisor, in VMX root)     hardware
│ QEMU process               │      ┌───────────────────────┐   ┌─────────┐
│  vCPU thread 0 ─ioctl(KVM_RUN)──► │ kvm.ko     (/dev/kvm) │   │ VT-x CPU│
│  vCPU thread 1 ─ioctl(KVM_RUN)──► │ kvm_intel.ko(VMCS,ex) │VMX│ VMCS:   │
│  main thread (devices,VNC) │  ◄── │ scheduler: 2 task_    │ r/│ 4 KB    │
│                            │ exit │ structs + page cache, │n-r│ per     │
│  guest RAM = one mmap      │ only │ NIC driver — the hyper-│oot│ vCPU    │
│  (anonymous, = -m 512)     │      │ visor's cast          │   │         │
└────────────────────────────┘      └───────────────────────┘   └─────────┘
     ▲          │ QEMU emulates the device (2 context switches),
     └──────────┘ then calls KVM_RUN again → VMRESUME
```

Positioning the others, one sentence each. **Xen** (2003): a
micro-hypervisor booted first; drivers live in *dom0*, a privileged Linux
guest; customers run as *domU*. **Hyper-V** (Microsoft, 2008, Windows
Server 2008): the hypervisor sits *under* Windows; drivers live in the
"root partition," a Windows with hypervisor-granted privileges. **ESXi**
(ESX 2001, ESXi 2007): VMware's own kernel (vmkernel) with drivers in a
management world — your infrastructure is a VM on top of it. KVM: the host
kernel is the hypervisor, each VM a userspace process — honestly neither
type, which is why vendors who did or didn't own a general-purpose OS
marketed the taxonomy so hard.

Last, the accounting: **steal time**. When a vCPU thread is runnable but
the host scheduler runs someone else — another VM's vCPU on that physical
core — the hypervisor records the nanoseconds on a per-vCPU page shared
with the guest kernel (Linux's paravirt steal-time, guest opt-in, since
~Linux 3.5, 2012). The guest's `top`/`vmstat` report it as the `st`
column, 0–100%: a vCPU waiting for a physical CPU is charged there. On
your laptop: 0. On a contended cheap cloud instance: 10–40% (provider- and
tier-dependent) — the noisy neighbor's fingerprint, which chapter 05 picks
up as the core of oversubscription arithmetic.

## What insiders know

- **Exit-reason distributions are fingerprints.** A mostly idle guest's
  trace is dominated by HLT (43), external interrupt (1), and
  interrupt-window (7); an I/O-heavy guest shows EPT violation (12) and
  I/O instruction (30); a CPUID (10) storm at millions per second is
  almost always guest *user-space software*, not infrastructure.
- **The 1,000–2,000-cycle figure is CPU-side only.** KVM's in-kernel
  handler adds roughly 1,000–10,000 more cycles depending on reason (CPUID
  near the floor; a guest CR write forces an MMU context rebuild), and a
  userspace bounce for device emulation adds 2 context switches on top.
- **VMLAUNCH vs VMRESUME is not cosmetic.** VMRESUME assumes this VMCS is
  the logical CPU's current one and was previously launched; after VMCLEAR
  you must VMLAUNCH again. Mixing them up in a homegrown VMM yields
  VMfailValid — silent non-entry, caught only via the VM-instruction error
  field. The current-VMCS pointer is per-logical-CPU, so a vCPU thread
  migrating between physical cores pays a VMPTRLD each time it lands.
- **Steal time is guest-opt-in.** The accounting page is donated by the
  guest kernel over a paravirt handshake; a guest without paravirt support
  shows `st 0` while being genuinely starved. Zero steal does not prove
  fairness — check dmesg for the paravirt enablement lines first.
- **The hypervisor owns CPUID's answer.** Reason 10 exists because KVM
  synthesizes the guest's CPU identity: hiding `vmx` (no nested
  virtualization), masking features, setting the vendor string. `grep vmx
  /proc/cpuinfo` printing 0 inside a cloud VM says nothing about the
  physical chip — it says the hypervisor chose not to tell you.
- **The interview trap:** "Is KVM type-1 or type-2?" The strong answer:
  the classification is vendor marketing; both use identical hardware
  machinery, and KVM's answer is "the host kernel is the hypervisor, each
  VM is a userspace process" — a statement about *who runs in VMX root*,
  the only distinction that survives contact with hardware.

## See it yourself

Everything here runs on a Linux laptop — or on the cloud VM you are
probably sitting in; the notes say which result means which world.

```console
$ grep -cE 'vmx|svm' /proc/cpuinfo
8
```

Why 8 on an 8-logical-CPU machine: `/proc/cpuinfo` prints one processor
block per logical CPU, each with a `flags` line, and `grep -c` counts
*lines* — every Intel flags line contains `vmx` (AMD's contain `svm`), so
the count equals the logical-CPU count. `0` means firmware-disabled, or —
most common — you are inside a VM whose hypervisor masks CPUID.

```console
$ lscpu | grep -i virt        # bare-metal Intel host:
Virtualization: VT-x          # AMD: AMD-V; masked cloud VM: no line

$ lsmod | grep kvm
kvm_intel   372736  0
kvm        1163264  1 kvm_intel

$ dmesg | grep -i kvm         # wording varies by kernel; representative:
... kvm: VMX is supported ... # module alive
... kvm: disabled by bios     # the classic failure: VT-x off in firmware
```

On bare metal with VMs running, watch exit reasons live:

```console
# kvm_stat (root; kernel tree tools/kvm/kvm_stat, some distro packages),
# representative 2-second fragment:
  HLT              43110   312 /s
  CPUID             8410    61 /s
  EPT_VIOLATION    66205   479 /s
  IO_INSTRUCTION    3310    24 /s

# or with perf (root, host with running VMs):
$ perf stat -e kvm:kvm_exit sleep 5
       1,204,553   kvm:kvm_exit
```

No VMs, or you are yourself a guest? The honest fallback lab is the
arithmetic: a vCPU taking 100,000 exits/s on a 3 GHz core spends
100,000 × 1,000–2,000 = 10–20% of a core just entering and exiting.

The full loop, one terminal (Debian/Ubuntu: `apt install qemu-system-x86`;
Fedora: `dnf install qemu-system-x86`; any tiny image — Alpine's "virt"
ISO, ~60 MB, is built for exactly this):

```console
$ qemu-system-x86_64 -enable-kvm -m 512 -smp 2 \
    -drive file=disk.qcow2,if=virtio,format=qcow2 \
    -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
    -cdrom alpine-virt.iso -boot d -nographic
```

One line per flag: `-enable-kvm` — open `/dev/kvm` and use VT-x, else QEMU
falls back to TCG, a pure software interpreter ~10–100× slower; `-m 512` —
512 MB guest RAM, a single anonymous host `mmap` that chapter 02's EPT
will make the guest's "physical" memory; `-smp 2` — 2 vCPUs = 2 vCPU
threads (`ps -L` on the QEMU pid shows them); `-drive ...,if=virtio` — a
paravirtual disk (chapter 03's rings), not emulated IDE; `-netdev user` +
`-device virtio-net-pci` — user-mode NAT networking, no root;
`-nographic` — serial console on your terminal. Inside the guest, run the
grep again: 0 is expected — the host masked it. You just verified CPUID
virtualization from the receiving end.

## How to use this knowledge

- **Read a KVM trace in ten seconds.** "The VM is slow": kvm_stat or
  `perf stat -e kvm:kvm_exit` gives the exit-reason distribution; EPT
  violation dominant → memory/MMIO (chapter 02); I/O instruction dominant
  → device emulation (chapter 03); CPUID storm → guest user-space
  software — file a bug against the app, not the infra.
- **Triage cloud latency.** `st` in `top` inside your instance separates
  "the physical core ran someone else's vCPU" (noisy neighbor — chapter
  05's arithmetic) from "my own code is the load" — the difference between
  buying a dedicated tier and fixing your code.
- **Debug "KVM won't start" in two commands.** `grep -c vmx
  /proc/cpuinfo` = 0 plus dmesg's "disabled by bios" → firmware toggle;
  on a cloud VM → the hypervisor doesn't expose `vmx`, so nested
  virtualization is off for that tier — no guest-side config fixes the
  host's CPUID mask.
- **Price a sandbox honestly.** Any design that makes the guest ring a
  doorbell per operation (per packet, per disk block) multiplies
  1,000–2,000 cycles by the operation rate. That arithmetic — not fashion
  — is why virtio, vhost, and SR-IOV exist (chapter 03) and why
  serverless fleets chose microVMs with ~20-device models (chapter 05).

## Connections

- **Module 01 ch03 (page tables)** — `mov cr3` is the canonical sensitive
  instruction; chapter 02 gives the guest's CR3 a second, hypervisor-owned
  page table (EPT) beneath it.
- **Module 01 ch04 (the process as a data structure)** — the vCPU is a
  thread, a `task_struct` in the host scheduler; the guest's scheduler is
  a program running inside a host-scheduled thread.
- **Module 15 ch02** — exit reason 12 (EPT violation) becomes the whole
  chapter: nested translation, MMIO-as-a-page-fault, ballooning, KSM.
- **Module 15 ch03** — the 1,000–2,000-cycle exit budget is the number
  every rung of the device ladder (virtio, vhost, SR-IOV) exists to
  amortize; exit reason 30 is where it starts.
- **Module 15 ch04** — containers delete this chapter's machinery
  entirely: one kernel, swapped namespace views, no VMCS, no mode switch.
- **Module 15 ch05** — steal time returns as the oversubscription
  arithmetic; the exit-cost budget returns as Firecracker's design brief.
- **Module 13 ch01 (NIC DMA rings)** — the shared-memory ring this module
  reuses as chapter 03's vring: one idea, three implementations.
- **Module 02 ch07** — cross-VM cache side channels: they resurface in
  chapter 02's KSM story, where dedup puts tenants on shared physical
  lines.

## Quiz

1. **Concept.** Explain precisely why `LGDT` is *not* a problem for
   trap-and-emulate while `SGDT` is. Which Popek-Goldberg property does
   each threaten, and how?

2. **Concept.** A design doc states: "the hypervisor runs at ring −1,
   below the guest kernel." Correct it precisely: what actually exists,
   what mode and privilege does the guest kernel run at, and what single
   true idea is the folklore compressing?

3. **Predict the output.** You run `grep -cE 'vmx|svm' /proc/cpuinfo` on a
   bare-metal laptop with 8 logical CPUs (Intel, VT-x enabled in
   firmware). What exactly is printed, and *why that number* and not 1 or
   0? What would it print in most cloud VMs, and why?

4. **Predict the numbers.** A host runs 64 VMs, each with 4 vCPUs.
   (a) How much physical memory do the VMCS control blocks alone consume?
   (b) A 3.0 GHz vCPU takes VM-exit round trips of 1,000–2,000 cycles:
   what is that in wall-clock time, and at 100,000 exits per second what
   fraction of one core do the exits alone burn?

5. **Diagnose.** Two kernels are booted at ring 0 on one physical machine;
   it freezes within a second of the second kernel's boot. Name the
   instruction class that froze it, state exactly what the metal did on
   the offending instruction, and explain why no software anywhere had a
   chance to intervene.

6. **Diagnose.** kvm_stat on one vCPU shows CPUID (reason 10) at ~5
   million exits/second. No unusual kernel workload is running, but one
   userspace process is pegging a core. What is that process almost
   certainly doing, why does each iteration cost an exit, and in whose
   source code is the fix?

7. **Diagnose.** Inside a cloud VM, `top` shows `%st 25` under your normal
   workload; the host administrator reports the physical machine is 40%
   idle. Reconcile: what accounting produced the 25, and give two distinct
   mechanisms that make a *runnable* vCPU wait while physical cores sit
   idle.

8. **Cross-chapter (module 01 ch04).** Your 2-vCPU guest runs a compute
   job in guest user space. In host-kernel terms (the "process is a data
   structure" view): what represents the guest's scheduler, what
   represents the guest's CPUs, and what do the guest's processes map to
   in the host kernel — if anything? What one observable number tells you
   both vCPU threads went unscheduled at the same time?

<details>
<summary>Answers</summary>

1. **`LGDT` traps; `SGDT` betrays.** `LGDT` is privileged: at CPL > 0 it
   faults, control transfers to the hypervisor, and the VMM installs a
   *virtual* GDT — resource control preserved, exactly the trap-and-emulate
   loop. `SGDT` is sensitive without being privileged: it executes at any
   CPL, no trap, and its output — the *real* GDT's linear address — is
   machine-global state the VMM never sees being read. It threatens
   **fidelity**: the guest's observable machine differs from the virtual
   one (enabling detection, e.g. the 2004 "red pill" via sibling `SIDT`),
   and a guest computing from its descriptor tables now uses numbers the
   VMM never sanctioned. The pair breaks "sensitive ⇒ privileged" from
   opposite directions: `LGDT` obeys it, `SGDT` ignores it.

2. **There is no ring −1.** Rings are CPL 0–3; VT-x adds a mode bit
   *orthogonal* to them: VMX root vs VMX non-root. The guest kernel runs
   at **ring 0 inside VMX non-root** (its instructions behave exactly as
   on bare metal until they exit); the hypervisor's code runs at **ring 0
   inside VMX root**. The true idea the folklore compresses: a strict
   one-level-down transfer still exists — when the guest does something
   sensitive, control drops below its own ring 0 — but it is a *mode
   switch with a 4 KB state block (the VMCS)*, not a new ring.

3. **Prints `8`.** `/proc/cpuinfo` has one processor block per logical
   CPU, each with a `flags` line; `grep -c` counts matching *lines*, not
   occurrences — on Intel every flags line contains `vmx` (AMD's contain
   `svm`), so the count equals the logical-CPU count. Not 1 because there
   are 8 flags lines; not 0 because VT-x is enabled. In most cloud VMs it
   prints **0**: the hypervisor virtualizes CPUID (exit reason 10) and
   masks out `vmx`, typically to disallow nested virtualization. The
   physical chip almost certainly has it; the guest was told otherwise.

4. **(a) 1 MB.** 64 × 4 = 256 VMCSes; 256 × 4 KB = 1,024 KB — pocket
   change next to the guest RAM, which is the real memory bill.
   **(b)** At 3.0 GHz one cycle ≈ 0.333 ns, so 1,000–2,000 cycles ≈
   **333–667 ns (0.3–0.7 µs)** per round trip. At 100,000 exits/s:
   100,000 × (1,000–2,000) cycles = 10⁸–2×10⁸ cycles/s on a 3×10⁹-cycle
   core = **~3–7% of one core** burned purely on entering and exiting,
   before any handler runs.

5. **Privileged instructions at ring 0 trap nowhere.** The second kernel's
   boot writes **CR3** with its own page-table root; at ring 0 the CPU
   executes the write untrapped — there is no more-privileged ring to
   fault to — so the *real* translation registers now point at kernel B's
   tables. Kernel A vanishes from the address space, its next fault walks
   B's tables, and the machine triple-faults into a reset loop; or B's
   idle `HLT` parks the physical core with interrupts vectored through
   B's IDT. No software could intervene because the multiplexing contract
   *is* the trap boundary, and both kernels were on the same side of it.

6. **A CPUID-per-iteration loop.** The process is using `CPUID` as a
   pipeline-serialization barrier around `RDTSC` — a classic
   timing/benchmark idiom — or re-querying feature bits in a hot loop.
   Every guest `CPUID` in VMX non-root unconditionally causes a VM exit
   (reason 10), because the hypervisor must own the answer (it masks
   features, hides `vmx`, presents the virtual CPU identity). Each
   iteration pays the 1,000–2,000-cycle round trip plus KVM's handling —
   easily 10–100× slower for that loop, and 5M exits/s × ~1,500 cycles
   ≈ 2.5×10⁹ cycles/s ≈ most of a 3 GHz core in exits alone. The fix is
   in **the guest application's source**: hoist CPUID out of the loop and
   cache the result, and replace the serialize-RDTSC idiom with
   `LFENCE`/`RDTSCP`. No infrastructure change helps: the hypervisor
   cannot stop owning CPUID.

7. **Steal time, defined.** The 25 came from the guest's steal accounting:
   the hypervisor records, on a per-vCPU page the guest kernel reads,
   every nanosecond the vCPU was *runnable but not scheduled*. That covers
   the noisy-neighbor case — the core ran another tenant's vCPU. But "40%
   idle" reconciles via two mechanisms that delay a runnable vCPU on an
   otherwise idle box: **(a)** a CPU bandwidth cap — a quota/weight or
   burst-credit exhaustion throttles the vCPU thread even while cores sit
   idle (chapter 05's economics), or **(b)** placement constraints — the
   vCPU thread is pinned or must co-locate by topology/NUMA, and the
   eligible cores are busy while others are free. In all cases the guest
   sees runnable-but-not-running and reports `st`; "idle host" means idle
   *cores*, not available *placement*.

8. **Scheduler = a program; CPUs = threads; processes = nothing.** The
   guest's scheduler is not a host object — it is code executing *inside*
   the two QEMU vCPU threads. Those threads are the host's `task_struct`s:
   the guest's "CPUs" are host-scheduled threads, and the host scheduler
   is the only one that decides when guest time flows. The guest's
   processes are bookkeeping inside the guest kernel (its own task
   structures in guest RAM); the host kernel has no objects for them —
   which is precisely why a VM is "a process that thinks it's a
   computer." The observable: **steal time — `st` climbs in the guest's
   `top`** whenever runnable vCPU threads wait; if both threads were
   unscheduled simultaneously, the guest registers a full-steal interval
   with its own scheduler none the wiser — it was frozen too.

</details>
