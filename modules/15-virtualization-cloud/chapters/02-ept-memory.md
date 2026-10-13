# Chapter 02 — Memory virtualization: EPT and the second page table

> **The 30-second version.** A guest OS owns a page table mapping guest
> virtual to guest "physical" addresses; the hypervisor owns the real
> mapping to host RAM. Composing the two naively costs 24 translation
> memory accesses for one guest load in the worst case (4 guest-table
> reads, each itself needing a 4-access EPT walk, plus 4 for the final
> guest-physical address) — survivable only because the TLB caches the
> composed result. Software first dodged it with shadow page tables (one
> merged table, synced by write-protecting the guest's own — the era
> when guest kernel builds crawled); hardware then did it properly:
> EPT/NPT, the MMU walks both tables, misses cost about 2× native, and
> large pages (2 MB / 1 GB) plus TLB tags (VPID/ASID) claw the rest
> back. The same non-present EPT bit turns MMIO registers into
> page faults — a device write is a VM exit. It all serves one economic
> reason: overcommit. The promised 8 GB is a ceiling, not a reservation,
> reclaimed by a balloon (a garbage collector that asks the program to
> enumerate its garbage), KSM (copy-on-write across VMs — and a side
> channel), or, worst case, host swap.

## Two owners, one address: the composition problem

Module 01 ch03 derived the page table as the kernel's answer to "who
owns which bytes." Virtualization breaks that ownership: inside a VM
there are **two kernels with two opinions about one address**:

- The **guest kernel** owns a page table mapping *guest virtual* (GVA)
  to *guest physical* (GPA) — CR3, 4 levels of 512 entries, 9 index
  bits per level, 4 KB pages, module 01 ch03's machinery — and it
  believes GPAs are RAM.
- The **hypervisor** owns the real mapping: the guest's "RAM" is an
  `mmap` in a QEMU process (ch01), and it must enforce
  *GPA → host physical* (HPA) without trusting the guest.

One guest load, `mov rax, [rbx]`, now needs GVA → GPA then GPA → HPA:
two translations, and the MMU was designed to perform one. Every fix
below answers "how do you compose two page tables" — and the answer
decides whether guest memory runs at native speed or a third of it.

## Counting the naive walk: 24 translations for one load

Before the fixes, count the naive composition: x86-64, 4-level guest
paging, 4-level EPT (Intel 2008; AMD's NPT/RVI, Barcelona 2007,
structurally identical). The MMU reads the guest's PML4E, PDPTE, PDE,
PTE — 4 accesses — but each entry lives at a **guest-physical**
address, untouchable without an EPT translation: 4 more each. The
PTE's payload, the data's GPA, needs its own walk: 4 more.

```text
ONE guest load, uncached worst case — x86-64, 4-level guest
paging + 4-level EPT (no TLB, no paging-structure caches).

  guest virtual address (48-bit)
      |
      |  MMU begins the GUEST walk at guest CR3
      |
      |  [ 1] read guest PML4E   (a GPA)
      |           +-- EPT walk for that address: [ 2][ 3][ 4][ 5]
      |  [ 6] read guest PDPTE   (a GPA)
      |           +-- EPT walk:                   [ 7][ 8][ 9][10]
      |  [11] read guest PDE     (a GPA)
      |           +-- EPT walk:                   [12][13][14][15]
      |  [16] read guest PTE     (a GPA)
      |           +-- EPT walk:                   [17][18][19][20]
      |
      v   PTE payload: guest physical address of the DATA
      |
      |  the GPA is still not RAM — the EPT translates it:
      |                                    [21][22][23][24]
      v   host physical address, finally
      |
  [25] the data access the CPU wanted in the first place
```

**24 translation accesses plus 1 data access = 25 memory references for
one guest load** (the honest range, depending on page sizes and leaf
levels, is roughly 20-30). Native code needs 4. If every bracketed
number were a DRAM round trip (~100 ns), one load would cost ~2.4 µs —
a doubly pessimistic bound, since walk reads mostly hit cache, but it
states the stakes: a naive composition is unusable.

Why doesn't the recursion go deeper — why doesn't reading an EPT entry's
own address need a third table? The **EPT pointer (EPTP) is a
host-physical address**, and hardware treats reads of EPT-internal
entries as already-physical: the recursion is cut by fiat at one level
of indirection per privilege boundary — *each new owner of memory buys
one level, not a tower.*

The escape hatch is the TLB: a hit resolves in about 1 cycle and
caches the **composed** translation (GVA straight to HPA), so the
25-access worst case happens only on misses. Everything else below is
one of three jobs: keep the hit rate high, keep misses cheap, don't
flush on VM switches.

## The two fixes: the shadow that lies, and the EPT that composes

**Fix one — shadow page tables (software, 1999-2008).** If the CPU
can't walk two tables, give it one that lies: the hypervisor *merges*
both translations into one real page table (shadow entry =
compose(guest PTE, GPA mapping)) and points guest CR3 at it — the CPU
walks one table at native cost. The bill comes due on *correctness*:
the guest writes to its page tables constantly, and every write
desynchronizes the shadow, so the hypervisor **write-protects the
guest's page-table pages** — the next guest PTE write faults, exits
(1,000-2,000 cycles, ch01's number), and the shadow is re-synced.
PTE-churning workloads — `make -j16` forks thousands of processes,
each fork and COW fault writing PTEs — pay an exit per protected write: "shadow paging makes guest kernel
builds slow" (an era-remembered 5-10× on PTE-churning phases; the
per-exit mechanism is exact).

**Fix two — EPT/NPT (hardware, Intel 2008 / AMD 2007).** Teach the MMU
to perform the composition: the guest's tables are walked first
(GVA → GPA), and every guest-physical result — including each
page-table read — is translated through the EPT (GPA → HPA) *by
hardware*. No shadows, no write-protection, no re-sync: guest PTE
writes become ordinary memory writes, 0 exits. The trade is the miss
path — in practice ~2× native's miss cost (measured 1.5-2×, depending
on how much of the walk the caches absorb).

An EPT leaf entry, read once — its bits are ch03's whole I/O story in
miniature:

```text
EPT leaf entry, 8 bytes (Intel, 4-level EPT, one 4 KB page):

  bit  0      read            bits 3-5   memory type (0 = uncacheable)
  bit  1      write           bit  7     large page (2 MB leaf one level
  bit  2      execute                      up, 1 GB two levels up)
  bit  8      accessed        bit  9     dirty
  bits 12-51  host physical frame number

  r=0, w=0, x=0  ==  "no mapping here at all"
```

Two mitigations do the heavy lifting on the miss path:

- **EPT large pages.** A 2 MB EPT entry covers what 512 separate 4 KB
  entries would; a 1 GB entry, 512×512. Fewer levels per miss, far
  fewer TLB entries for the same memory. The catch: it needs the
  guest-physical region backed by a 2 MB-aligned contiguous host
  mapping — one of the *real* jobs of transparent huge pages inside
  guests. Guest THP is not (only) about the guest's TLB; it enables the
  host's EPT large pages.
- **VPID / ASID.** TLB entries are tagged with the VM's identity — a
  16-bit VPID on Intel, the ASID on AMD — so switching VMs no longer
  flushes the TLB. Without tags, every vCPU handoff — and every
  exit/re-entry, which is *most of them* — throws away the composed
  translations; the next few thousand accesses each pay the composed
  walk. Hence the lore of "why single-vCPU pinning mattered":
  pre-VPID, pinning the vCPU and its QEMU thread to one core kept that
  core's deeper caches warm and the flush tax rarer. With VPID the
  entries survive the switch; invalidation becomes surgical (INVVPID).

## A device's registers are a page fault: MMIO behind the EPT

Devices live in memory-mapped register banks: a program writes a
register by storing into a PCI BAR's physical range. In a VM, the
guest driver makes the same store — to a GPA inside the emulated BAR.
The hypervisor leaves that region of the EPT **non-present** (r=w=x=0,
memory type uncacheable). The store faults — an *EPT violation*, a VM
exit (exit reason 48 in KVM's `vmx.h`) whose exit qualification
carries the faulting GPA and the read/write/execute bits — and QEMU's
device model emulates the register write in software.

**A device's registers are a page fault.** One sentence unifying three
chapters: the NIC doorbell of module 13 ch01 (a store to a BAR that
rings hardware), the memory-mapped peripherals of module 17 (the same
trick, no hypervisor), and ch03's virtio (the same trap, made cheap).
Why cheap matters: one exit costs 1,000-2,000 cycles, so a driver
busy-polling an MMIO status register — ~5 cycles per iteration on bare
metal — runs at ~1,000+ cycles, ~200× slower, while a QEMU thread burns
100% CPU emulating a register that never changes. Polling MMIO is
faulting in a tight loop.

## Selling the same DIMM twice: the balloon, the scanner, the cliff

It all serves one economic reason: **overcommit** — promise more RAM
than exists. A 1.05-1.5× fleet policy (ch05's honest ratios) lets a
64 GB host run 12 guests promised 8 GB each: 96 GB sold, 64 GB owned.
Module 01 ch03's overcommit lesson replays one level up: the guest
kernel's `malloc` is a ceiling too — promised-but-untouched bytes cost
nothing. The 8 GB is a *ceiling, not a reservation*. When the bet
fails, three tools remain, in descending order of politeness.

**The balloon (virtio-balloon).** A driver inside the guest that
allocates and frees memory *on command*. The flow:

```text
hypervisor                guest kernel              balloon driver
    |                                                   |
    |---- "inflate: give me 2 GB back" ---------------->|
    |                    guest allocates 2 GB (kernel memory)
    |<--- "here are the 2 GB worth of GPAs" ------------|
    |  QEMU madvise(MADV_DONTNEED)s those pages of its
    |  guest-RAM mmap -> 2 GB of host frames return to the
    |  host free list for other guests
```

The guest did the hard part: it *chose* what to evict, running its own
reclaim instead of letting the host guess. This is a **garbage
collector for VMs** — module 01 ch14/ch15's cycle collector inverted:
a tracing collector walks from roots to find garbage; the balloon asks
the *program* to enumerate its own. The failure mode is
equally inverted: when the guest has nothing to give — page cache
needed, anonymous memory hot — it swaps *inside itself* to satisfy the
balloon, and one page can hit disk twice (the guest's virtual disk is
a file on the host). The **double paging disaster** — swap inside
swap — is why ballooning is a policy tool, not a free lunch.

**KSM (Kernel Samepage Merging).** Guests running the same OS image
have byte-identical memory. The kernel thread `ksmd` scans: checksum
candidates, byte-compare real ones, merge duplicates into one
**write-protected shared page** — module 01 ch03's copy-on-write across
*virtual machines* (a write to a merged page faults and gets a private
copy). Measured reality: 100 Windows guests booting one
image share 40-70% of memory; the counters are
`/sys/kernel/mm/ksm/pages_shared` (pages in the tree) and
`pages_sharing` (pages saved). The costs: CPU for the scan (tunable:
`pages_to_scan` per pass, `sleep_millisecs` between — 100 and 200 ms on
the mainline defaults I checked; cat yours), and a security problem.
Dedup makes two tenants' pages *physically identical* — same cache line
— the enabling condition for FLUSH+RELOAD (module 02 ch07 returns):
tenant A evicts the shared line (`clflush`), waits, reloads and times it
— a fast reload means tenant B touched it in between — and the *pattern*
of touched lines across a shared page leaks secrets; Yarom and Falkner
(USENIX Security 2014) extracted an RSA private key from a co-located
Xen VM this way. The mitigation is blunt: security-conscious
multi-tenant fleets run KSM off (`run=0`); `merge_across_nodes=0` —
often cited here — only limits merging to one NUMA node, a locality
knob, not a defense.

**Host swap: the last resort.** If the balloon declined and KSM found
nothing, the host pages guest memory to host swap — physical memory
virtualized as swap. The numbers make the runbook rule ("never let a
fleet node hit it") self-evident: RAM's design point is ~100 ns; a fault
satisfied from NVMe costs tens of microseconds (~100-300×), from
spinning disk milliseconds (~10,000×). A workload engineered around
100 ns does not degrade gracefully across that 3-4 order-of-magnitude
cliff — it stops: a node sliding into host swap shows a step function
in guest latency, not a slope.

## Fictions on top of fictions: vNUMA and EPT-on-EPT

**vNUMA.** Tell a guest it has 2 sockets — 2 vNUMA nodes — and its
kernel earnestly optimizes for them: node-local allocation, per-node
affinity. On a 1-socket host that topology is fiction: the scheduler
pins threads to nodes that map to nothing and splits per-node data
across a nonexistent boundary, and NUMA-local workloads run at ~60% of
what the same host delivers with a truthful topology (rule of thumb,
NUMA-sensitive benchmarks). The fixes: give the guest a topology
matching the host, or pin vCPUs and run `numad` so the fictional nodes
land on real locality. Auto-NUMA balancing can't help — it optimizes
toward the fiction.

**Nested virtualization.** Run a hypervisor inside a VM (L1 inside L0)
and the EPT becomes guest-controlled data. L1's EPT manipulations —
writing entries, INVEPT — are VMX operations executed from L0's VMX
non-root mode, so they *trap to L0*: an exit caused by handling an
exit. L2's exits bounce twice: L2 → L0, reflected into L1, handled,
resumed — it works because every level plays the same trick on the one
below. The cost lands on TLB misses — the composed
GVA→GPA→L1-HPA→L0-HPA walk is deeper, ~2× miss cost even after L0
builds KVM's merged *shadow EPT* for L2 (shadow paging's move, one
level down). This is why a hypervisor inside your cloud VM (minikube's
KVM driver) is heavier than it looks.

## What insiders know

- **Exit reason 48 vs 49.** 48 is `EXIT_REASON_EPT_VIOLATION` (rights
  mismatch or unmapped GPA — expected, e.g. every MMIO access); 49 is
  `EPT_MISCONFIG` (reserved bits set — a *hypervisor* bug, almost
  always, not a guest one). A steady 49 stream is a bug report.
- **The flag check that tells the truth.** `lscpu` will not print
  EPT/NPT; the honest check is the flags line: `grep -c ept
  /proc/cpuinfo` (Intel) / `grep -c npt` (AMD). Reading 0 *inside a
  cloud VM is normal* — the virtual CPU model hides host capabilities.
- **A migrating VM gets slower before it moves.** Dirty tracking for
  live migration (ch03) write-protects EPT entries, which splits large
  pages — mid-migration, the TLB miss rate climbs as 2 MB mappings
  become 4 KB ones. The pre-copy perf dip is EPT fragmentation, not
  bandwidth.
- **Ballooning is invisible in the guest's process table.** Inflated
  pages are kernel memory held by the driver: `MemTotal` unchanged,
  `MemAvailable` falling, no `ps` line to blame. That divergence is the
  tell; check `dmesg | grep -i balloon`.
- **The 16-bit VPID changed scheduling.** Pre-VPID, VM switches flushed
  the TLB and the next few thousand accesses re-walked the composed
  translation — the origin of "pin the vCPU" folklore. Today the flush
  is selective (INVVPID), and pinning survives as NUMA advice, not TLB
  advice.

## See it yourself

Run what your situation allows — on a cloud VM you are the *guest*,
half the lab.

```console
# The honest capability check (Intel 'ept', AMD 'npt'):
$ grep -c ept /proc/cpuinfo
8                # EPT present: CPU count; 0 on a cloud guest
$ lscpu | grep -i -E 'ept|npt'    # usually silent — don't trust it

# Large-page policy (if you're the guest, this is an EPT knob):
$ cat /sys/kernel/mm/transparent_hugepage/enabled
always [madvise] never            # the bracket marks the active policy

# Am I ballooned? (inside any VM with virtio):
$ lsmod | grep virtio_balloon     # present = the host *can* ask for RAM
$ grep -E 'MemTotal|MemAvailable' /proc/meminfo
$ dmesg | grep -i balloon

# KSM ships off — observe the zeroes:
$ cat /sys/kernel/mm/ksm/run /sys/kernel/mm/ksm/pages_shared \
      /sys/kernel/mm/ksm/pages_sharing
0 0 0
$ cat /sys/kernel/mm/ksm/pages_to_scan /sys/kernel/mm/ksm/sleep_millisecs
100 200

# The overcommit made visible — same VM, twice:
$ free -h    # INSIDE the guest: 'total' = what was PROMISED
$ free -h    # ON the host: QEMU's RSS = what was TOUCHED

# Paper lab (always available): recompute the 25-access worst case,
# then the 2 MB / 2 MB variant — answer in the quiz.
```

What to notice: the guest reports the ceiling, the host's RSS the
truth.

## How to use this knowledge

- **"The VM is slow but the host is idle" triage.** Work the chain:
  steal time (`top`'s `st`, ch01), guest THP fragmentation (`perf
  stat -e dTLB-load-misses` in the guest), ballooning (`MemAvailable`
  falling, no process to blame), EPT large-page coverage.
- **Capacity math.** Overcommit ratios are a policy choice with a
  costable failure: at 1.5× you bet tenants touch 2/3 of their promise;
  the balloon reclaims politely, host swap does not. The 100 ns →
  tens-of-µs cliff is the design-review number.
- **Security review of a shared-tenant provider.** Ask whether KSM or
  any content-based dedup runs on the node class you're buying; if
  yes, you're renting the FLUSH+RELOAD precondition. A yes/no
  question, not a vibe.
- **Explaining the nested tax.** A hypervisor inside a cloud VM is
  heavy: composed EPT walks cost ~2× on TLB misses, and exits reflect
  through two levels.

## Connections

- **Module 15 ch01** — the VM exit (1,000-2,000 cycles) is this
  chapter's unit of spend.
- **Module 15 ch03** — every virtio kick is an EPT violation; migration
  write-protects EPT entries and splits large pages.
- **Module 15 ch04** — containers skip the second table entirely; the
  contrast makes both designs legible.
- **Module 15 ch05** — overcommit ratios (1.05-1.5×), steal time, the
  memory-pressure-storm runbook rule.
- **Module 01 ch03** — the page table and COW, extended twice: EPT is
  the same walk composed, KSM the same COW across VMs.
- **Module 01 ch14/ch15** — the cycle collector, inverted by the
  balloon: the collector that asks the program to name its garbage.
- **Module 02 ch07** — FLUSH+RELOAD returns through KSM's shared
  pages: dedup puts two tenants on one cache line.
- **Module 13 ch01** — the NIC's doorbell: the same "registers are
  addresses" illusion the EPT turns into page faults.

## Quiz

1. Translating an EPT entry's own address requires no third page table.
   Why — and if it *did* require one, what happens to the 25-access
   count?

2. A guest load hits a 2 MB guest huge page, and the EPT maps that whole
   region with a large-page entry. Uncached worst case, *including* the
   data access: how many memory accesses?

3. A 64 GB host runs 12 guests, each promised 8 GB (96 GB sold); each
   guest touches 5.5 GB at steady state. Does the host survive unaided —
   and if KSM merges 40% of resident guest memory, then?

4. On a shadow-paging hypervisor, a guest kernel build (`make -j16`)
   runs 5-10× slower than in an EPT VM on the same host. Explain the
   mechanism, and predict which *other* workloads suffer the same fate.

5. A NUMA-optimized benchmark runs at ~60% of bare-metal speed inside a
   2-socket vNUMA guest on a 1-socket host. Diagnose, and give the two
   operational fixes.

6. A guest driver busy-polls an MMIO status register; throughput
   collapses (~200×), a QEMU thread at 100% CPU. Explain with the exit
   arithmetic; name the fix ch03 builds.

7. (Cross-module.) KSM merges a page shared by two tenants' VMs.
   Describe the FLUSH+RELOAD attack this enables — which observation
   says "the victim touched this line" — and why `merge_across_nodes=0`
   is not the fix.

8. Before VPID, every VM switch flushed the TLB and the next few
   thousand accesses paid the composed walk. Explain why single-vCPU
   pinning helped anyway, and what the 16-bit VPID actually buys.

<details>
<summary>Answers</summary>

1. **The EPTP is a host-physical address.** Reads of entries *inside*
   the EPT are already physical — hardware cuts the recursion by fiat
   at one level of indirection per privilege boundary. If it didn't,
   each EPT-internal read would need a third-table walk, and that a
   fourth — the composition grows without bound (4 × (1 + 4 × (1 +
   ...))), no finite walk terminates. One owner, one
   indirection: the invariant that keeps virtualizable memory finite.

2. **16.** A 2 MB guest huge page makes the guest walk 3 entries
   (PML4E, PDPTE, PDE-leaf — the PDE *is* the leaf). Each is a GPA
   translated by an EPT whose leaf here is also 2 MB, a 3-access walk:
   3 × (1 + 3) = 12. The data's GPA gets the same 3-access walk: +3.
   The data access: +1. Total 16, versus 25 with 4 KB pages at both
   levels — the gap × every TLB miss is why THP-in-guest is an EPT
   feature.

3. **Survives only with KSM.** Touched: 12 × 5.5 GB = 66 GB against
   64 GB — 2 GB short, so unaided the node tips into host swap or must
   balloon guests with nothing to give. With 40% merged, footprint = 66 × 0.6 =
   39.6 GB — fits, ~24.4 GB headroom. A fleet riding on a dedup ratio is
   why KSM's cost and side channel are policy debates, not footnotes.

4. **Every protected PTE write is a VM exit.** Shadow paging syncs by
   write-protecting the guest's page-table pages: guest PTE write →
   fault → exit (1,000-2,000 cycles) → shadow re-sync. A kernel build
   is the pathological input — thousands of forks, COW faults,
   mmap/munmap churn: millions of PTE writes, each paying the exit tax.
   Under EPT, guest PTE writes are ordinary writes: 0 exits. The same
   fate hits anything PTE-churning — spawning build systems, JVMs
   resizing heaps — not steady daemons.

5. **The guest's NUMA topology is fiction.** The 2 vNUMA nodes
   correspond to nothing; the guest kernel optimizes for them —
   node-local allocation, per-node affinity — constraining scheduling
   and splitting per-node structures across a boundary that maps to
   one physical socket. Fixes: (a) a truthful topology (1 vNUMA node),
   or (b) pin vCPUs and run `numad` so the fictional nodes land on real
   locality. Auto-NUMA balancing optimizes toward the fiction — useless.

6. **Polling MMIO is faulting in a loop.** The EPT leaves the BAR
   non-present, so each poll read is an EPT violation → VM exit
   (1,000-2,000 cycles) → QEMU's device model emulates the read (the
   100% CPU thread). A ~5-cycle loop becomes ~1,000+ cycles per
   iteration: ~200×. Ch03's fix is a design pattern, not a faster
   emulator: devices interrupt on state change (MSI-X injection needs
   no exit), and data moves through shared rings in guest RAM so only
   the doorbell touches MMIO.

7. **Dedup creates the shared cache line; timing reads the pattern.**
   KSM put both tenants' identical copies on one physical page — same
   cache lines (module 01 ch03's COW; module 02 ch07's channel). Attack
   loop: `clflush` the target line, let the victim run, reload and
   time. A *fast* reload means the victim touched the line between
   flush and reload; slow means it didn't. The touched-set across the
   page reveals the victim's execution path — Yarom and Falkner (2014)
   recovered an RSA private key from a co-located Xen VM.
   `merge_across_nodes=0` only restricts merging to one NUMA node — a
   locality knob; tenants still share lines. Real mitigation: KSM off
   (`run=0`) or per-tenant restrictions.

8. **Pin to keep the deep caches warm; VPID to keep the translations.**
   Pre-VPID, a VM switch — and every exit/re-entry, including each MMIO
   exit — flushed the guest-usable TLB; the next few thousand accesses
   re-walked the composed translation. Pinning didn't stop the flushes,
   but kept one core's caches and paging-structure caches hot, making
   each refill cheaper and handoffs rarer. VPID tags each entry with the
   VM's 16-bit ID, so root↔non-root transitions preserve the composed
   entries — the flush becomes a surgical INVVPID of the stale
   translations. Ch01's exit cost made flushes frequent; this chapter's
   walk cost made them expensive.

</details>
