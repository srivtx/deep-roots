# Module 05 — Android internals

> Android is an OS designed by memory constraints: the zygote exists because forks
> are cheaper than boots, Binder exists because one copy is cheaper than two, lmkd
> exists because there is no swap, and ART's whole object model is tuned for the
> memory class the device was assigned at birth. Every subsystem here is module 01
> applied to a phone.

**6 chapters · one final exam · prerequisite: module 01 chapters 03 (COW/virtual
memory), 12 (Java/JVM object model — compare as you go), 14 (GC alternatives),
19 (allocators), 22 (mobile memory).**

## How to study it

Read in order: boot (how a process universe appears), ART (what an "object" is
here), Binder (how processes talk), lmkd (how processes die), the sandbox (why
processes can't hurt each other), APK/OTA (how it all ships and updates). Labs use
adb against an emulator or device — every claim checkable with dumpsys / procfs.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [Boot & the Zygote](chapters/01-boot-zygote.md) | Bootloader → kernel → init → Zygote: fork as the app-launch primitive. |
| 02 | [ART: objects & GC](chapters/02-art-gc.md) | dex→JIT→AOT, ART heap spaces, the concurrent copying collector. |
| 03 | [Binder](chapters/03-binder.md) | The one-copy IPC: mmap'd buffers, parcels, thread pools. |
| 04 | [Lifecycle & lmkd](chapters/04-lifecycle-lmkd.md) | oom_score_adj bands, PSI-driven kills, cached-process budgets. |
| 05 | [The sandbox](chapters/05-sandbox.md) | UID-per-app, SELinux domains, seccomp filters, permission memory. |
| 06 | [APK & OTA](chapters/06-apk-ota.md) | Zip/dex/resources, signature schemes, A/B updates, dm-verity. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — boot & the Zygote.** The chain: bootloader → (verified boot: AVB,
dm-verity — defer depth to ch06) → kernel → init parsing .rc files (services,
on-boot triggers — the declarative boot as a data problem) → Zygote. What Zygote
preloads (framework classes, common resources, JIT profiles) and *why fork is the
app-launch primitive*: COW page-sharing of a warm heap (module 01 ch03 payoff —
derive the memory savings: N apps sharing framework pages dirty-0), fork+specialize
into an app process (app_process, the socket-triggered fork model), USAP
(unspecialized app process pool) as pre-fork optimization. system_server as the
big sibling (what lives there: AMS, PMS, WMS). Boot time anatomy (where the
seconds go — the insider's mental budget). Lab: `adb shell ps` showing the zygote
parentage of every app; `dmesg` boot timeline; `dumpsys meminfo` for one app with
the zygote-shared accounting explained.

**Chapter 02 — ART.** The compilation ladder: dex bytecode → interpreter → JIT
(with profile) → AOT speed-profile (the CloudProfiles/Baseline Profiles twist:
profiles ship with apps). Object layout: the object header (class word 4 bytes
compressed refs + lock/monitor word — the 8-16 byte header story; compressed
references: 4-byte oops in a 4GB heap reservation — module 01 ch12 comparison).
Heap spaces: ImageSpace (zygote-mapped, never moved), allocation space
(AllocationSpace), large-object space (the threshold: 3-byte... state the
kLargeObjectThreshold for primitive arrays: 3KB page-aligned — mark the ART
version), the RosAlloc/region allocators. The Concurrent Copying (CC) collector
as the deep dive: read barrier at the object-header level (the forwarding-
pointer/Brooks-pointer style trick — how a moving collector runs concurrently with
mutators), compaction vs pinning, GC pauses in the µs-ms range (the number that
makes Java-on-mobile viable). Heap size = the ActivityManager memory class per
device (dalvik.vm.heapgrowthlimit — the per-device birthright from the intro).
Lab: `dumpsys meminfo`, `getprop | grep dalvik.vm`, a GC log read (logcat
`art GC` lines with pause times).

**Chapter 03 — Binder.** Why Binder exists: compare the copy economics of sockets
(2 copies) vs shared memory (0 copies but synchronization hell) vs Binder (1 copy
into the receiver's mmap'd buffer pool). The machinery: /dev/binder, the per-
process mmap'd transaction buffer (**1MB per process — the root of the fabled
TransactionTooLargeException**; the async space being half of it), Parcel
serialization (the flat wire format sketch: length-prefixed primitives, the
FLAT_BINDER offsets table), fd passing via a separate object array (why fds ride
along the buffer, not inside it). AIDL → generated Java/C++ stubs (the
onTransact switch — recognize the shape from module 04's switch-table idiom).
The thread pool: binder threads (default max 15... state the platform default)
and the deadlock lore (a synchronous binder call that waits on a thread that
needs a 16th binder thread). servicemanager as the root namespace (handle 0),
death notifications. Lab: `cat /proc/binder/stats` (or /dev/binderfs), a
TransactionTooLargeException reproduced with a 2MB bundle, `dumpsys activity
services` showing the binder wiring.

**Chapter 04 — lifecycle & lmkd.** The design premise: no swap, so memory
pressure = kill (module 01 ch03's OOM philosophy at OS scale). oom_score_adj
bands as policy: native (-1000), system (-900), persistent (-800), foreground
(0), visible/perceptible (100-200), cached (900+), the empty-process budget
(~32 cached processes... state the AOSP constant). lmkd: the migration from the
in-kernel lowmemorykiller driver to userspace lmkd, and PSI (pressure stall
information, kernel 4.14+) as its trigger — why pressure-based beats free-
memory-based (the workingset metric: refault rate as the "are pages being
usefully used" oracle). The kill decision flow (pressure level → kill the highest
adj with the lowest... the score arithmetic). Process death and restart semantics
(what the user perceives). The app freezer (cgroup v2 cgroup.freeze — frozen
cached processes cost 0 CPU, the modern efficiency layer). zram as the one
concession to swap (compressed RAM as a ratio lever). Lab: `dumpsys activity
processes` showing adj values, `cat /proc/pressure/memory`, setting a device's
cached-process limit and watching kills.

**Chapter 05 — the sandbox.** The app sandbox as an old UNIX idea applied
radically: every app = a distinct UNIX UID (AID_APP_START 10000 — derive the
consequences: files, sockets, /proc visibility all become per-app). SELinux:
domains per app (untrusted_app), the policy loading path (seapp_contexts
keyed by uid/seinfo), neverallow assertions as compile-time policy proof, MLS
categories isolating apps from each other. seccomp-bpf: the syscall filter
installed on zygote-forked apps (which syscall families are banned for apps —
the insider's list: e.g. some ptrace/socket options), how the BPF filter
actually executes in-kernel (a tiny program in a register — the same BPF VM
idea that eBPF generalized). Permissions: install-time vs runtime, signature
permissions, the mapping to SELinux + UID + keystore. App data dirs (the
secdatalayout mangling), external storage (FUSE/sdcardfs translation — the
permission illusion of "shared" storage). Keystore/KeyMint keys as
hardware-bound secrets (StrongBox). Attack-surface reasoning: what a compromised
app can reach (the chapter's payoff: draw the boundary map). Lab: `ls -Z` on
data dirs, `dmesg | grep avc` denials, `id` in `adb shell run-as`.

**Chapter 06 — APK & OTA.** APK anatomy: the zip container (EOCD, central
directory, local headers — enough to understand the v2 signing block's injection
point *between* central directory and files), classes*.dex (the dex format:
header, string/type/proto/field/method id tables, class_defs, code_items — and
the 65K method-reference limit that birthed multidex: derive why the field/
method id index is u16), resources.arsc (config-qualified resource tables, why
"one string, many configs" is a table lookup not a branch), native libs
(lib/arm64-v8a, extractNativeLibs=false — mmap from the APK), the manifest as
compiled binary XML (why you can't grep it — aapt2 decodes). Signatures: v1 JAR
(per-entry META-INF digests — the Janus vulnerability class it enabled), v2
(whole-file, over the APK Signing Block), v3 (proof-of-rotation key lineage),
and how they compose with the platform's verified boot (ROM → AVB → dm-verity
→ APK signature — the chain of trust as a memory-integrity story). A/B
(virtual) partitions and snapshot-based updates, incremental OTA delta
compression. Lab: `unzip -l` structure walk, `apksigner verify --print-certs`,
`aapt2 dump`, dex header bytes in `xxd`.

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **Android performance engineering**: you can read meminfo/dumpsys the way
  insiders do — knowing *which numbers are lies* (shared pages, cache, dalvik
  accounting).
- **Crash triage**: OOM kills, TransactionTooLarge, ANR — the memory story behind
  each, diagnosed from logs alone.
- **Security review**: the sandbox boundary map as an attack-surface checklist.
- **The bridge to module 06** (iOS does the same problems differently: Jetsam
  instead of lmkd, XPC instead of Binder) and **module 07** (the desktop kernels
  underneath both).
