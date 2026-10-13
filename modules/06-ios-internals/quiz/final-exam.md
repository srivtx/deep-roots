# Module 06 final exam — iOS internals

Twelve questions across all five chapters, plus the module 01/05 comparisons
the module is framed against. Closed book; answers are worked, not stated —
the derivations are the point. Scoring: 11+ = you own this module; 8-10 =
solid, revisit the chapters your misses cluster in; <8 = the chapter quizzes
are the study path.

**Part I — the kernel and the kill policy (chapters 01-02)**

1. A Mach task owns exactly three things; a thread owns none of them. Name
   the three, name what a thread brings instead, and derive why
   `task_for_pid` is entitlement-gated from what a task port is worth.

2. A phone maps 2 GB of dense framework text. For a 48-entry TLB, give the
   coverage at 4K and at 16K pages; for the mapping itself, give the
   page-table bytes at 4K and at 16K. State the factor both answers share,
   and the cost that buys it.

3. An app holds 120 MB of resident dirty anonymous memory and 30 MB of
   touched clean file-backed pages. The compressor later folds 48 MB of the
   now-cold dirty pages into a 12 MB store. Give RSS, phys_footprint, and
   the RAM the kernel got back — then name which number the foreground
   band's limit checks.

4. "Volatile purgeable memory is dirty by content and clean by promise."
   Explain the three-tier footprint economy behind that sentence, and derive
   why a 200 MB purgeable image cache can sit near zero footprint while a
   200 MB decoded bitmap cannot.

5. A user reports the app "crashes" while visible. The only artifact is a
   `JetsamEvent-*.ips`: `bug_type` 210, `pageSize` 16384. In the `processes`
   array, Music shows `rpages` 300,000 and no `reason`; your app shows
   `rpages` 260,000, `states ["frontmost"]`, and `reason
   "per-process-limit"`. Crash or kill? Who died, why did the bigger claim
   survive, and what do you fix first?

**Part II — IPC and the launch path (chapters 03-04)**

6. "Checking a uid on every operation" and "holding a send right" both
   control access. What does ambient authority permit that a capability
   makes structurally impossible — use the confused deputy and the
   check-to-use window — and what does the capability *not* give you, which
   the audit token exists to supply?

7. Your camera app hands a 12 MB photo to an XPC thumbnail service on every
   shot. A teammate proposes a plain TCP socket "for portability"; your
   Android counterpart says Binder would move it in one copy. For socket,
   Binder, and a page-aligned OOL descriptor with `deallocate` set: count
   the copies, name who frees, and name the first hard wall each hits.

8. Sixty processes each fault, read-only, on the same three shared-cache
   data pages; the kernel fixes each at page-in into the cache's VM object.
   Count the fixed physical pages. Then process A writes a global on page 2,
   and process B writes a global on page 2 too. Recount — and state the
   invariant that makes your first count possible.

9. A TestFlight build's first launch after every update takes 2.1 s; the
   second takes 380 ms. The team files "regression: dyld is slow," attaching
   a Mac profile captured with `DYLD_PRINT_STATISTICS=1` (180 ms pre-main).
   Diagnose the user's number and the profiler's number — what was
   invalidated, and why did the instrument lie?

**Part III — trust, and the two-platform view (chapter 05 + the comparisons)**

10. A stack overflow in an arm64e *system* process offers four moves: (a) a
    ret-to-libc chain through a corrupted saved LR; (b) replay of a leaked,
    correctly signed return address; (c) a jump to a legitimate function
    entry with controlled arguments; (d) blind-forged return addresses over
    1,000 crash-loop restarts. Dead or alive, each, and why? Why does the
    same overflow still work in a third-party app's own code?

11. A build script copies a codec dylib into the app bundle *after* the
    `codesign` phase. On macOS, with `disable-library-validation`, the app
    runs; on iOS the process is `killed: 9` at first launch. What was
    missing from the signature, which two checks did iOS run that macOS was
    told to skip, and what is the pipeline fix?

12. Android's Zygote and iOS's shared cache solve the same problem with
    opposite mechanisms. Which cost is amortized into a *process* and which
    into a *file*? Which design shares *dirtied* pages? And why does iOS's
    no-fork, kill-first model require the file answer?

<details>
<summary>Answers</summary>

**Part I**

1. The task owns a **vm map**, an **ipc space** (the port-rights table), and
   **threads**; a thread brings only registers and a stack — the
   resource-container / execution-unit split (ch01). `task_for_pid` (trap 26)
   mints a *task port*: read/write over the victim's entire vm map. Ungated,
   that would turn every process's memory into ambient authority, so macOS
   gates it with `taskgated` and iOS with entitlements (ch01; the port
   machinery is ch03).

2. **TLB coverage:** 48 × 4 KB = 192 KB; 48 × 16 KB = **768 KB**. **Page
   tables:** at 4K an L3 table covers 2 MB, so 2 GB costs 1024 tables ×
   4 KB = **4 MB**; at 16K it covers 32 MB, so 64 tables × 16 KB = **1 MB**.
   Both improve by the **same 4x**: each entry covers 4x more VA at
   identical per-entry cost. The price is internal fragmentation — the unit
   of allocation, protection, and transfer quadruples; one touched byte in
   a sparse structure dirties 16 KB (ch01's bet on density).

3. **RSS = 102 MB** (72 MB resident dirty + 30 MB clean; the folded pages
   left the page tables, and the 12 MB copy is unattributed).
   **phys_footprint = 84 MB** (72 MB `internal` + 12 MB `internal_compressed`;
   clean file-backed pages are excluded — droppable, re-faultable). **RAM
   back: 36 MB** — compression *creates* memory: 12 MB spent to park data
   that cost 48 MB resident. The band limit checks **footprint**: killing
   you frees the compressed copy — the debt an RSS-only tool cannot see
   (ch02).

4. Three tiers (ch02; the ledger underneath is module 01 ch03): **clean
   file-backed** — not in footprint, dropped and re-faulted from the file;
   **volatile purgeable** — dirty by content, disposable by promise, not in
   footprint, held in 8 age groups and dropped oldest-first under pressure,
   the purge disclosed only when you re-mark; **nonvolatile/dirty** — billed
   as `internal`, compressed or killed with you. The 200 MB cache marked
   volatile carries a regeneration promise the kernel accepts as payment;
   the decoded bitmap is live anonymous data with no promise — billed, then
   compressed, then killed for.

5. **A kill, not a crash.** `bug_type` 210 is the Jetsam report — no thread
   backtraces by design (crashes are 309, with stacks). The victim is the
   entry with `reason`: your app, at 260,000 × 16,384 ≈ **4.1 GB** claimed
   while frontmost. `per-process-limit` means footprint crossed *its own
   band's* resident limit — a ledger check, not a comparison — so Music's
   larger `rpages` are irrelevant: charged to a different, bigger allowance,
   no global shortage required. Fix footprint itself: mapped assets instead
   of decoded copies, volatile purgeable caches — the dirty + compressed
   gauge showed this coming (ch02).

**Part II**

6. Ambient authority re-derives permission from *who the process is*, per
   operation: anyone passing an identity check can steer a privileged
   process into acting for them (the confused deputy), and check and use
   are separated in time — a window to race. A send right cannot even *name*
   an un-granted port: names index a kernel-owned table, entries arrive only
   by delegation or inheritance, and every `mach_msg` validates against that
   table — possession is the permission, no check to fool, no window (ch03).
   What it cannot supply is the caller's *identity*: capabilities
   authenticate the channel, not who is on it. The kernel-stamped audit
   token (pid, pid-version, uids, code-signing word) names the caller for
   the handler's policy — checked per message, since the token follows the
   latest message (ch03; the signing word is ch05).

7. **Socket: two copies** — into a kernel buffer, out again; the receiver
   frees its buffer. **Binder: one copy** into the receiver's pre-mapped
   ~1 MB pool — a 12 MB parcel dies as `TransactionTooLargeException`: the
   pool is the first wall (module 05 ch03). **Mach OOL, page-aligned with
   `deallocate`: zero copies** — the kernel *moves* the sender's pages into
   the receiver's map (the sender's mapping vanishes); the receiver owns
   them and must `vm_deallocate` or leaks them until exit. The ~2 GiB inline
   ceiling never applies: the payload rides out-of-line. Per shot, two 12 MB
   copies is a battery line too; large `xpc_data` already rides OOL (ch03).

8. **Three fixed pages.** Each faulted page is fixed once — the kernel
   applies the chained fixups and inserts the page into the cache's VM
   object, which backs every mapper — and the **uniform per-boot slide** is
   the invariant that makes one fixed copy valid for all sixty:
   per-process slides would force a private fixed copy each — the 200 MB ×
   N `__DATA` arithmetic again (ch04). After the two writes: **five** — A
   and B each take a private COW copy of page 2, the fixed original still
   serving the other fifty-eight; pages 1 and 3 stay single copies (ch04;
   the fault is module 01 ch03's).

9. Neither number is a dyld regression. The 2.1 s is the **first-launch
   regime**: the update re-signed or replaced images, so the cached launch
   closure — keyed to an exact image set by UUID — was discarded; that
   launch paid the closure rebuild plus cold page-ins, the launch budget's
   one-time column (ch04). The 380 ms second launch is the steady state:
   closure validates, pages map warm. The 180 ms is an artifact: on macOS
   any `DYLD_*` environment variable disables closure use for that process,
   so the instrument measured the fallback doing full symbol resolution — a
   path users never take (ch04). If pre-main still hurts, audit `+load` and
   dylib count.

**Part III**

10. (a) **Dead** — `PACIBSP` signed LR (B key, SP as context); the chain's
    `AUTIBSP; RET` authenticates a value nobody signed and fails with
    probability 1 − 2^−24. (b) **Alive** — a leaked signed return address is
    pre-forged: the (pointer, tag) pair is complete, and replay passes;
    leaks still convert to control flow. (c) **Alive** — `ret` guards the
    backward edge only; steering to legitimate entries with controlled
    arguments is the forward edge, CFI/BTI's rung. (d) **Dead** — keys
    re-roll at every `exec`, so 1,000 restarts are 1,000 independent 2^−24
    attempts, no partial credit, each a loud logged kill; progress needs a
    same-process oracle — a signing gadget or a PACMAN-style leak. The same
    overflow works in a third-party app because app code is still plain
    arm64: PAC hardens the system code *around* your pages, not yours
    (ch05).

11. **Missing: the dylib was in no signature.** Copied in after `codesign`
    ran, it appears in no `_CodeSignature/CodeResources` (sealed-resources)
    hashes, and no CodeDirectory covers its pages. On iOS, **library
    validation** requires every executable page to trace to a signature
    chain or trust-cache entry — the check `disable-library-validation`
    tells macOS to skip — and **page-in validation** (`cs_validate_range`,
    in the fault path) hashes the first faulted page, finds no matching
    slot, marks the process CS_INVALID, and SIGKILLs: unsigned code never
    materializes, so death lands at first use, not exec. Fix: sign after
    every bundle mutation; gate CI on `codesign --verify --deep --strict`
    (ch05).

12. **Zygote: amortized into a process.** Android's apps `fork` from one
    warm parent and inherit its *already-dirtied* pages via COW — the
    sharing includes the dirt (module 05 ch01; module 01 ch03's fork-COW
    pointed at warm state). **Shared cache: amortized into a file** —
    pre-linked and pre-signed at OS build, mapped COW, data pages
    kernel-fixed and shared only *while clean*; a write is a private copy
    (ch04). The kill model forces the file answer: iOS denies third-party
    `fork()` and ranks every process for death by band (ch02) — a warm
    shared parent would be un-killable state every victim takes down with
    it, while the file's pages are clean, reclaimable for free, untouched
    by any death. Same pay-once economics, opposite halves of the COW
    machinery.

</details>
