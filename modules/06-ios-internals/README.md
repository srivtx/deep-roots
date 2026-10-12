# Module 06 — iOS internals

> iOS is a memory-distrust machine: the kernel validates every page it executes
> (code signing at page-in), the OS kills rather than swaps (Jetsam), the system
> frameworks live in one giant COW artifact (the shared cache), and pointers
> themselves carry authentication (PAC). Where Android optimizes for cooperation,
> iOS optimizes for *verifiable trust* — and it is all memory machinery.

**5 chapters · one final exam · prerequisite: module 01 chapters 03 (virtual
memory/COW), 05 (allocators), 16 (ARC/ObjC runtime), 22 (mobile memory); module
05 as running comparison.**

## How to study it

Read in order: XNU's memory foundations, the kill-first memory policy (Jetsam),
IPC as capability passing (Mach ports/XPC), the launch path (dyld + shared
cache), and the trust chain that holds it all together (signing, PAC). Labs: a
Mac is ideal (same XNU/dyld core, commands work directly); iOS-device equivalents
noted where they differ.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [XNU: the kernel's memory picture](chapters/01-xnu.md) | Mach + BSD, tasks and threads, VM maps, 16K pages. |
| 02 | [Jetsam & the compressor](chapters/02-jetsam.md) | No swap: compression or death — the band table and the pressure policy. |
| 03 | [Mach ports & XPC](chapters/03-mach-ports-xpc.md) | Ports as capabilities, messages with ownership, XPC services. |
| 04 | [dyld & the shared cache](chapters/04-dyld-shared-cache.md) | Launch closures, chained fixups, the biggest COW artifact you own. |
| 05 | [Code signing & PAC](chapters/05-code-signing-pac.md) | Page-in validation, entitlements, AMFI, authenticated pointers. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — XNU.** What XNU is: Mach (tasks, threads, VM, IPC) + BSD (processes,
VFS, sockets, signals) + IOKit (drivers) — the layered picture and the translation
between Mach tasks and BSD pids (one structure, two views — the insider's `proc`
↔ `task` mapping). Task = the resource container (VM map + port rights + threads);
thread = the execution unit. The VM map: per-task structure of vm_map_entries
(regions: address range + permissions + inherited VM object) — *not* the raw page
tables (module 01 ch03's page tables are the mechanism under this policy layer —
draw the three-layer diagram: vm_map → VM objects (memory objects, shadow objects
for COW) → physical pages). The 16K page size on Apple Silicon (vs 4K Intel Macs):
why bigger pages (TLB reach, the tradeoff math with page-table memory), and the
app-compatibility story. Kernel memory zones (kalloc zones — and why zone
exhaustion/failure is a kernel panic class; the zone security hardening saga).
Mach traps vs BSD syscalls (number-space differences — recognizable in module 04
disassembly). Lab (macOS): `vmmap <pid>` — read the regions, find the shared
cache range, the malloc zones, the stack guard pages.

**Chapter 02 — Jetsam & the compressor.** The design premise: iOS has no swap
because flash swap = battery + wear (derive it) — so the two pressure valves are
compression and killing. The compressor: a dedicated kernel pager that moves cold
pages into a compressed in-RAM store (LZ-style per-VM-page compression), and the
resulting accounting puzzle (compressing a 16K page to 4K *creates* memory —
know which metric counts it: phys_footprint, not RSS). Jetsam: the priority band
table (idle ↔ critical, the per-band kill order: idle > foreground in reverse —
and the *per-process highwater marks* some apps get), the global vs per-process
pressure triggers, and the kill event trail (jetsamEvent logs — the insider's
crash-analysis input: how to read one and distinguish a Jetsam kill from a crash).
User-space notifications: memory warnings as the *chance to yield before the
hammer* (didReceiveMemoryWarning / scene didReceiveMemoryWarning), the purgeable
memory tiers (dirty vs clean pages; clean = file-backed = free to drop, the
mapped-file insight from module 01 ch03 at work). os_proc_available_memory as the
"how much runway do I have" API. Comparison table: Jetsam vs Android lmkd vs
desktop OOM killer (module 05 ch04 + module 07 connections). Lab: `memory_pressure`
command, footprint vs RSS in Activity Monitor / `footprint` tool, a jetsam event
log walkthrough.

**Chapter 03 — Mach ports & XPC.** The port abstraction: a kernel-protected
message queue; a *right* to a port (send right, receive right, port set) is an
unforgeable capability — derive why capability = memory-safe permission (vs
checking a uid string on every operation). Messages: fixed-size header + typed
descriptors; out-of-line (OOL) memory = the kernel copies/transfers pages into
the receiver's space (ownership transfer semantics — this is where IPC meets
memory lifecycle; the 8GB... no: state the VM messaging limits and the descriptor
copying cost). MIG as the RPC stub-generator (like AIDL — module 05 ch03 pattern
repeats; recognize mig-generated code in a kernel disassembly). Port death and
no-more-senders notifications as lifecycle cleanup (the "when the last right
goes away" trigger — ties to module 03's UAF family: dangling rights are
*impossible* by construction, which kills a whole bug class). XPC: the modern
user-space wrapper (connections over launchd-bootstrapped services, typed
dictionaries, the audit token as the kernel-attested caller identity — why XPC
services are the privilege-separation workhorse: the daemon pattern with a
small trusted core). Lab: `launchctl` listing XPC services, a minimal XPC
service's Info.plist anatomy, lsof showing Mach ports as kqueues.

**Chapter 04 — dyld & the shared cache.** What the shared cache *is*: a
pre-linked, pre-signed, page-mapped file containing every system framework —
dylibs fixed up against *each other* once at OS-build time, then mapped COW into
every process (derive: N processes × hundreds of MB of libSystem = why warm
launches need this; the cache is the biggest COW artifact in the system —
module 01 ch03's payoff at 1-3GB scale; state the size on a modern iOS: ~1-3GB
per architecture slice). What's inside: text pages, data pages, the closure
metadata. dyld's evolution: dyld2 (on-disk symbol loading) → dyld3/dyld4
(launch closures — a serialized plan of "map these, initialize these, call
this": precomputed at app install/first-run, validated, then launches skip
*all* symbol resolution; dlopen invalidates the closure). Chained fixups
(LC_DYLD_CHAINED_FIXUPS: instead of streams of rebase opcodes, fixup targets
stored in-place in the data pages as a linked list of 64-bit entries — the
pointer format with the high bits as chain metadata; why this made page-in
linking possible: fix only the pages you fault, and they're COW-shared... the
insider's "rebase is now lazy" insight). Launch flow end-to-end: fork/posix_
spawn → exec → dyld → closure → rebase/bind page-ins → ObjC +load and static
initializers (mod_init_func) → main — with a timing budget per step. RE
consequence: framework symbols live in the cache, not in per-app binaries
(module 04 ch01's Mach-O knowledge meets its biggest artifact). Lab (macOS):
`dyld_info`/`otool -l` on a binary, `vmmap | grep 'dyld shared'`, timing a
first vs second launch.

**Chapter 05 — code signing & PAC.** The trust chain from silicon up: boot ROM
(read-only, burned in — the immutable root) → LLB/iBoot stages → kernel with
Apple's root of trust → AMFI (AppleMobileFileIntegrity) enforcing at runtime.
Code signing anatomy: the CMS blob embedded in the binary, CodeResources
hashes for resources (the page-level detail: code pages are hashed into a hash
table per binary — and the *kernel checks page hashes at page-in* (cs_validate
on fault — unsigned/altered pages simply never materialize: module 01 ch03's
demand paging fused with a MAC check — the insider's mental model: "virtual
memory is the enforcement mechanism"). Entitlements: the signed key-value list
(what an app may do — get-task-allow, jit, etc.), provisioning profiles as the
enterprise/developer wrapper. PAC (ARM64e): pointer authentication as a
hardware feature — pointers signed with a key+context, `ret` verifies the
return-address signature (which module 02 gadgets die: ret-to-anything ≠
signed; the residual attack surface: signing gadgets, JOP on valid call
targets), ObjC method cache / vtable pointer signing on iOS, why PAC is a
*mitigation* not a guarantee (the 1/forgery-probability framing). The composite
picture for module 02 ch06's ladder: iOS stacks page-in validation + PAC +
no-W^X-violations (JIT only via special entitlement + MAP_JIT; the write-xor-
execute discipline enforced). Lab: `codesign -dv --verbose=4` on a Mac binary,
`codesign -d --entitlements`, check the code signature on the shared cache.

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **iOS memory debugging**: reading jetsam events, distinguishing OOM-kill from
  crash, phys_footprint fluency (the number that actually predicts the kill).
- **Launch-time engineering**: closures, shared cache behavior, static-init cost
  — where the milliseconds are and why.
- **Trust-model fluency**: signing, entitlements, PAC — you can explain *why*
  iOS exploit chains start with a logic bug, not a memory bug.
- **The running comparison with Android** (module 05): same problems, opposite
  philosophies — the two-reference-frame view interviewers probe for.
