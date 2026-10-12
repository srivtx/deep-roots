# Chapter 02 — Jetsam & the compressor

> **The 30-second version.** iOS has no disk swap, so when RAM runs short the system has
> two moves: **compress** cold dirty pages into a smaller store that stays in RAM (the
> kernel's compressor pager — CPU cycles for memory), and **kill** whole processes
> (Jetsam). Kills follow a fixed ladder of priority bands, bottom-up, largest footprint
> first within a band; processes with private caps ("highwater marks") die for exceeding
> those alone. The number that decides your fate is **phys_footprint** — dirty plus
> compressed pages — not RSS: compressing a 16 KB page to 4 KB frees 12 KB, and
> footprint, unlike RSS, keeps the surviving 4 KB on your bill. Your only warning is an
> advisory memory warning; the kill is a SIGKILL recorded in a JetsamEvent report.

## No swap: deriving the two valves

**Flash wears out.** Consumer TLC NAND tolerates ~10^3 program/erase cycles per cell,
and write amplification means a "4 KB write" moves more than 4 KB (controllers
rewrite whole erase blocks). Desktop swap streams anonymous pages through that budget
for hours; on a phone, that is hardware lifetime spent to avoid killing a background
process.

**Every page moved costs joules.** A swap-out is a DRAM read plus a flash write; a
swap-in is the reverse, both through the controller's queues. On battery, memory
traffic is energy (module 01 ch22); compressing in RAM costs microseconds of CPU and
no flash traffic.

**Flash is slow where it hurts.** A compressed page faults back in microseconds; a
swapped page returns in milliseconds. Under pressure that difference is jank or a
watchdog timeout.

So iOS has never had a swap file (iPhone OS 1 through iOS 17; macOS keeps one —
different physics and budgets). Module 01 ch03's ledger: every physical page is
**file-backed** (recoverable from a file) or **anonymous** (lives only in RAM). Three
reclaim moves remain:

1. **Drop clean file-backed pages** — nearly free; the next access re-faults the page
   from the file. Demand paging, working as designed.
2. **Compress cold dirty pages** — keep the data, shrink its RAM cost.
3. **Evict a process** — SIGKILL it; *all* its pages vanish at once.

Moves 1–2 are the first pressure valve: reclaim without a victim. Move 3 is the second
valve — not reclamation but *scheduling*: deciding which process is cheapest to lose.

## The compressor: a pager that trades CPU cycles for RAM

The compressor is a **pager** in the Mach VM sense (module 06 ch01's VM objects): a
cold dirty page's data is encoded and parked in kernel-owned **compressed segments**
still in RAM; the original page is freed. A later touch faults and decompresses it
back — to the process, just a slower fault. No API, no signal, no callback.

Facts worth carrying (XNU source, macOS 14 / iOS 17 era):

- Compression is **per VM page**, not a stream: each 16 KB page (4 KB on Intel Macs)
  is coded independently.
- The scheme is **hybrid and adaptive**: WKdm where it compresses well, an LZ4 variant
  elsewhere (`osfmk/vm/vm_compressor_algorithms.c`). WKdm — a dictionary coder built
  for memory pages — has separate 4 KB and 16 KB code paths.
- Real workloads shrink 2–3x (pages are mostly zeros, pointers, and repeats); the
  design arrived in iOS 7 / OS X 10.9 Mavericks (2013) and has not changed shape
  since. A thrashing store — pages compressed, faulted, compressed again — is a
  Jetsam trigger in itself.

### The accounting puzzle: compression *creates* memory

The trap. Compress one 16 KB cold dirty page to 4 KB:

```
                         before                      after
  process's dirty set    16 KB (page P, resident)    0 KB
  compressor store       0 KB                        4 KB (P encoded, kernel-owned)
  physical RAM spent     16 KB                       4 KB     <- 12 KB CREATED
  process RSS            16 KB                       0 KB     <- "all 16 KB freed!"
  process phys_footprint 16 KB                       4 KB     <- the honest ledger
```

Three ledgers, three answers. **Physical RAM**: 12 KB came back — compression *creates*
free memory. **RSS**: the 16 KB is gone — P no longer maps into the process's page
tables, and the 4 KB copy sits in the kernel's pool, unattributed to any process's
RSS; an RSS-only tool overstates the win by 4 KB per page. **phys_footprint**: keeps
the 4 KB charged to the owning process — correct, since if that process dies, the
bytes are freed too. The folded pages are still *your* pages; the number that
predicts your death is **phys_footprint, never RSS**. Its recipe, from the ledgers
`kern_memorystatus.c`:

```text
phys_footprint = internal               (dirty anonymous pages, resident)
               + internal_compressed    (your pages, at compressed size)
               + purgeable_nonvolatile  (+ its compressed share)
               + alternate_accounting   (+ compressed; graphics-adjacent)
               + iokit_mapped           (mapped in by drivers)
               + page_table             (your own page tables)
```

Excluded: clean file-backed pages (droppable) and *volatile* purgeable pages (droppable
by contract, below). And **Apple Neural Engine** buffers go to a separate
`neural_nofootprint` ledger — ML workloads get a break the kill arithmetic never sees.

## Jetsam: the band ladder

The kernel subsystem is **memorystatus** (`bsd/kern/kern_memorystatus.c`); "Jetsam" is
the name on the reports. Every process sits in one **priority band**; under global
pressure the kernel walks the ladder from the bottom and kills until pressure clears.
Some processes also carry a personal cap — a **highwater mark** — and die for exceeding
it regardless of global conditions. The whole policy in one picture (bands from
`bsd/sys/kern_memorystatus.h`, modern XNU, macOS 14 / iOS 17 era; numbers get rescaled
between releases, the order never is):

```text
        memory pressure rising
                    │
                    ▼
 ┌──────────────────────────────────┐   band  who lives there
 │ VALVE 1 — reclaim, no victim     │  ─────  ─────────────────────────────
 │   clean file-backed pages: DROP  │    999  kernel_task, launchd —
 │     (re-fault from file: ~free)  │         INTERNAL, exempt from jetsam
 │   cold dirty pages: COMPRESS     │    210  JETSAM_PRIORITY_MAX
 │     16 KB → 4-6 KB, stays in RAM │    190  critical system, telephony
 │     (the compressor pager)       │    180  important daemons (system
 └──────────────────────────────────┘         default)
                    │                         170  executive helpers
                    │  pressure still high    160  Home — SpringBoard
                    ▼                         150  Apple drivers / 130 Conductor
 ┌──────────────────────────────────┐   120  audio & CarPlay apps — must
 │ VALVE 2 — kill, band by band:    │         keep streaming
 │   walk UP from band 0, largest   │   100  YOUR APP while foreground
 │   footprint dies first within a  │    90  foreground support — the
 │   band (aging bands: oldest      │         visible app's extensions
 │   first). Kill = SIGKILL, plus   │    80  UI support
 │   one JetsamEvent report.        │    75  freezer (freezes apps' pages)
 └──────────────────────────────────┘    50  phone
   personal-cap escape hatch:            40  background agents (mail sync)
   highwater victims die at their        30  background apps — suspended
   own mark, ladder position            20  aging band 2 (opportunistic)
   irrelevant.                          10  aging band 1 (idle-deferred)
                                          0  idle — no-deadline daemons
```

**Global triggers.** The kernel tracks a pressure level — normal, warn, urgent,
critical — with free-page thresholds computed at boot from total RAM (why the same app
survives on 6 GB and dies on 3 GB). Below threshold, the memorystatus thread kills from
the lowest occupied band upward; within a band the bucket is sorted by footprint
(`JETSAM_SORT_FOOTPRINT`), biggest hog first — except the idle/aging bands, age-ordered,
oldest first. Compressor thrash and file-cache thrash (`fc-thrashing`) trigger it too.

**Per-process triggers.** Highwater-marked processes — app extensions, small daemons —
are checked individually; one over its own mark dies with reason `highwater` *even on
an otherwise idle system*. Foreground apps that cross their band's resident limit die
with `per-process-limit`. Same hammer, different trigger arithmetic.

**The kill** is a `SIGKILL` (signal 9): no exception, no handler, no stack — and the
kernel first snapshots *every* process with its page counts, so the report is a
census, not just an obituary. The reason vocabulary (`kern_memorystatus.c`):

```text
highwater               exceeded the process's own fixed mark
per-process-limit       crossed the resident limit for its band
vm-pageshortage         system-wide: not enough free pages
vm-compressor-thrashing the compressor is thrashing
vm-compressor-space-shortage  the compressed store itself is full
fc-thrashing            file cache thrashing
sustained-memory-pressure   pressure that lasts, not spikes
low-swap                macOS only: swap nearly exhausted
zone-map-exhaustion     kernel zone map full (module 06 ch01)
(also: jettisoned, vnode-limit, proc-thrashing, idle-exit)
```

## The escape hatches user space actually gets

**Memory warnings — the chance to yield before the hammer.** When pressure crosses the
warn level, user space learns through a dispatch primitive, not a signal:

```c
/* C: watch the pressure level yourself, no UIKit required */
dispatch_source_t s = dispatch_source_create(
    DISPATCH_SOURCE_TYPE_MEMORYPRESSURE, 0,
    DISPATCH_MEMORYPRESSURE_WARN | DISPATCH_MEMORYPRESSURE_CRITICAL,
    dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0));
dispatch_source_set_event_handler(s, ^{ /* drop caches, save state, NOW */ });
dispatch_resume(s);
```

UIKit repackages this as `UIViewController.didReceiveMemoryWarning()` (plus the classic
`applicationDidReceiveMemoryWarning()`); `UISceneDelegate`, for all its life-cycle
callbacks, has *no* memory-warning method of its own. Warnings are advisory — keep
allocating and you die anyway, a *suspended* app cannot run any handler — and
`per-process-limit` kills can arrive with no warning at all. Treat a warning as your
last voluntary act, not a negotiation.

**Purgeable memory — the three-tier economy.** Module 01 ch03's two ledgers plus a
contract: memory the app declares re-creatable.

```text
tier                 examples                       footprint?  under pressure
clean (file-backed)  mmapped assets, code,              no      dropped, re-faulted
                     never-written file pages                    from the file
volatile purgeable   NSPurgeableData, ImageIO         no(*)     dropped silently;
                     caches, re-creatable buffers                app told on next
nonvolatile purgeable/ base maps you re-marked          yes     compressed or
dirty (anonymous)    Swift arrays, decoded images              killed with you
                     and other live objects

(*) kernel keeps volatile pages in 8 age groups (NUM_VOLATILE_GROUPS = 8,
    osfmk/vm/vm_purgeable.c) and drops the oldest group first.
```

The API is `mach_vm_purgable_control` (`NSPurgeableData` is the friendly wrapper): mark
a range volatile when its contents are disposable; re-mark it nonvolatile later and the
call tells you whether the kernel purged it meanwhile — regenerate or proceed. Volatile
purgeable is the only dirty memory that costs you nothing: dirty by content, clean by
promise.

**os_proc_available_memory — how much runway do I have?**

```c
#include <libproc.h>
uint64_t runway = os_proc_available_memory();  /* bytes; iOS 13+ */
```

It answers "how much can phys_footprint grow before I hit my limit" — the number the
Xcode gauge's dirty + compressed bar is drawn against. For scale: extensions get caps
in the tens of MB; a foreground app on a 6 GB iPhone routinely lives at 3–5 GB
(module 01 ch22); the `com.apple.developer.kernel.increased-memory-limit` entitlement
(iOS 13.4+) lifts the cap on high-RAM devices.

## Three killers, one problem

| | **Jetsam** (iOS) | **lmkd** (Android) | **OOM killer** (Linux desktop) |
|---|---|---|---|
| swap | none, ever | zram (compressed RAM disk) | real disk swap |
| first valve | compressor + clean drop | zram + cached-process budget | swap out anonymous pages |
| killer lives in | the kernel (memorystatus) | user space (lmkd daemon) | the kernel |
| victim ranking | fixed bands (0–210) + per-process highwater marks | `oom_score_adj` bands, set by activity manager | per-victim "badness" score at kill time |
| trigger | free-page thresholds per pressure level; compressor state | PSI — time stalled in reclaim (kernel 4.14+) | watermarks, on allocation failure |
| notice | memory warnings (advisory) | `onTrimMemory` callbacks (advisory) | none until SIGKILL |

## What insiders know

- **The name map and the scale**: reports say "Jetsam," kernel source says
  `memorystatus` (`bsd/kern/kern_memorystatus.c`). `JETSAM_PRIORITY_INTERNAL = 999`
  puts `kernel_task` and `launchd` beyond the ladder; old blog posts quote a classic
  0–21 scale, modern XNU uses 0–210 — the order is the stable API.
- **phys_footprint's exact ledger**: internal + internal_compressed +
  purgeable_nonvolatile (+ compressed) + alternate_accounting (+ compressed) +
  iokit_mapped + page_table — plus Neural Engine buffers deliberately charged to
  `neural_nofootprint`: ANE traffic is invisible to the kill arithmetic.
- **Highwater kills are sysctl-gated**: `kern.memorystatus_highwater_enabled` turns the
  per-process-cap path on or off. `highwater` is the fixed personal mark;
  `per-process-limit` is the band's resident limit.
- **JetsamEvent reports are `bug_type` 210** (app crashes are 309). They land as
  `JetsamEvent-YYYY-MM-DD-HHMMSS.ips`: Settings > Privacy > Analytics Data, Xcode >
  Devices and Simulators, or `/var/mobile/Library/Logs/CrashReporter/` on a
  dev-unlocked phone. In production, MetricKit's `MXAppExitMetric` (iOS 14+) reports
  the exits without log-file access.
- **The report's useful fields**: `pageSize` (16,384 on 16K devices — check before page
  math), `largestProcess`, and a `processes` array with `name`, `uuid`, `rpages`,
  `lifetimeMax`, `states`, `frontmost` per entry — and *only the jettisoned process*
  carries `reason`. Apple's rule: if the victim is not your app, your "crash" was not
  memory pressure.
- **Volatile purgeable is free footprint**: 8 age groups, oldest dropped first; the
  compressor picks WKdm or LZ4 per page, with WKdm's 16 KB path existing because of
  module 06 ch01's 16 KB pages.

## See it yourself

**On any Mac (same XNU core; Apple Silicon Macs even share the 16 KB page size).**

Footprint versus RSS, in your own shell — the whole accounting chapter in two commands:

```text
$ vmmap --summary $$ | grep -i footprint
Physical footprint:          37.4M
Physical footprint (peak):   38.1M
$ ps -o rss= -p $$
    9184
```

`$$` is the shell's pid. RSS says ~9 MB, footprint 37 MB; the gap is compressed pages,
page tables, and IOKit mappings RSS refuses to attribute to you — and the
Jetsam-relevant number is the *bigger* one. To watch the pressure machinery itself:
`sudo memory_pressure -l warn|critical` forces a pressure level (watch Activity
Monitor's memory-pressure graph go yellow/red and a memory-hungry app's compressed
share climb — valve 1 at work; macOS then also swaps, which iOS never does, so kills
are rarer), and `/usr/bin/footprint <pid>` prints the same accounting by category —
the CLI twin of the Xcode gauge.

**A JetsamEvent walkthrough (documented — no Mac needed).** You need a real report
from a device or Organizer; here is the reading discipline on a representative one
(field names per Apple's Xcode docs and XNU's `kern_memorystatus.c`; Apple does not
freeze this schema, so treat the JSON as version-dependent):

```text
JetsamEvent-2024-05-21-161012.ips
header: { "bug_type":"210", "os_version":"iPhone OS 17.4 (21E219)", ... }
body:   { "pageSize":16384, "largestProcess":"SkyViewer", "processes":[
          { "name":"SkyViewer", "uuid":"8F...", "pid":531, "rpages":226000,
            "lifetimeMax":231000, "states":["frontmost"],
            "reason":"per-process-limit", "killed":... },
          { "name":"Music", "rpages":96000, ... },   <- no reason: not the victim
          ... one entry per process on the device ... ] }
```

Read it in this order: (1) `bug_type` 210 — a kill report, not a crash; *no thread
backtraces*, by design. (2) `pageSize` × `rpages` = 16,384 × 226,000 ≈ 3.5 GB claimed.
(3) Only the victim has `reason` — `per-process-limit` means the app crossed its own
band limit while frontmost. (4) `largestProcess` confirms it was the biggest claim on
the device. (5) `lifetimeMax` (peak footprint) says leak ratchet or one-shot spike.
Had the reason been `vm-pageshortage` with the victim in band 30, the verdict flips:
background roulette, not your bug — the difference between filing "OOM-killed by
policy" and hunting a crash that never happened.

## How to use this knowledge

- **Triage the bug report before blaming code.** "App vanished with no stack while
  visible" is the textbook Jetsam signature. Pull Analytics Data, find
  `JetsamEvent-*.ips`, and check whether your bundle id is the entry with `reason` —
  if not, the crash is elsewhere; if yes, the reason string chooses your next step.
- **Let the reason string pick the fix.** `per-process-limit` on a frontmost app →
  your footprint is the bug: mapped files instead of copied assets (module 01 ch03),
  volatile purgeable caches. `highwater` in an extension → the tens-of-MB cap is not
  negotiable; move the work and buffers back to the host app. `vm-pageshortage` while
  backgrounded → persist state before suspension so relaunch is cheap.
- **Size caches with the right number.** Budget against `os_proc_available_memory()`
  and the dirty + compressed gauge, never RSS — an RSS-based limit looks safe
  precisely when it is not: compressed pages are invisible to RSS and still count
  against you.

## Connections

- **Module 01 ch03 (virtual memory)** — the file-backed/anonymous ledger and demand
  paging underpin every move here: clean-drop is re-fault, purgeable is a contract
  over those ledgers, malloc-is-free-until-touched is why `os_proc_available_memory`
  ignores untouched allocations.
- **Module 01 ch22 (mobile memory)** — this chapter is that one's kernel half: the
  dirty-page economy, the OS-as-GC framing, the ARC-keeps-footprint-low payoff.
- **Module 05 ch04 (lifecycle & lmkd)** — the same derivation on the other platform:
  `oom_score_adj` bands versus Jetsam bands, PSI stall-times versus free-page
  thresholds, user-space killer versus kernel killer.
- **Module 06 ch01 (XNU)** — the VM map, VM objects, and 16 KB pages the compressor
  walks page-by-page; kalloc zones return as `zone-map-exhaustion`.
- **Module 07 ch05 (Linux memory machinery)** — the table's third column: swap,
  watermarks, the OOM killer's victim-scoring arithmetic.
- **Module 07 ch06 (macOS & the comparison)** — purgeable memory's Mac history,
  unified memory, and where swap re-enters (`frozen_to_swap`, `low-swap`).

## Quiz

1. **Derive it.** Name the three physical facts that make disk swap the wrong default
   on iOS, and the two valves that remain. Why is one "reclamation" and the other
   "scheduling"?

2. **Predict the numbers.** An iOS app (16 KB pages) mmaps an 800 MB read-only asset
   and touches 20 MB; it allocates and touches 100 MB of anonymous memory. The kernel
   later compresses 40 MB of now-cold anonymous pages down to 10 MB. What are the app's
   RSS and phys_footprint, and which one predicts the kill?

3. **Concept.** Compressing a 16 KB page to 4 KB "creates" 12 KB of free memory. What
   do RSS, physical RAM, and phys_footprint each report for that operation — and why
   does an RSS-only tool systematically overstate the benefit?

4. **Predict + connect.** `os_proc_available_memory()` returns 500 MB. The code calls
   `malloc(200 * 1024 * 1024)` without touching it. What does the call return right
   after — and which two earlier chapters explain the mechanism and the structure
   behind that?

5. **Diagnosis.** A user reports the app "just vanishes" opening the star chart. The
   logs show a `JetsamEvent-*.ips`, `bug_type` 210; your app (`frontmost`, `rpages`
   226,000 × 16,384) is the only entry with `reason`: `per-process-limit`. Was this a
   crash? What crossed what, and what do you fix first?

6. **Diagnosis.** A custom keyboard extension dies seconds into heavy typing: reason
   `highwater`, page math ~64 MB. The containing app is alive at 1.5 GB. Why did the
   *small* process die while the *big* one lived — and why did that need no
   system-wide pressure?

7. **Cross-chapter.** Your Jetsam reason is `vm-pageshortage`; your Android
   counterpart's log shows lmkd firing on PSI. What does each trigger measure, and why
   do both mobile kernels refuse the desktop OOM killer's victim-scoring (module 05
   ch04, module 07 ch05)?

8. **Diagnosis.** You implement `didReceiveMemoryWarning` to purge caches; the handler
   ran, and the app still died 400 ms later with reason `per-process-limit`. Give two
   distinct reasons the warning could not have saved it.

<details>
<summary>Answers</summary>

1. **Flash wear, joules, latency.** (a) Consumer NAND tolerates ~10^3 program/erase
   cycles per cell — swap spends that budget continuously. (b) Every swapped page
   costs a storage read/write pair plus write amplification — energy on a battery.
   (c) Swapped pages fault back in milliseconds versus microseconds compressed —
   pressure becomes jank. Valves: *compress* cold dirty pages (reclamation — the
   data survives, only its size changes) and *kill* processes (scheduling — rank by
   importance, delete the cheapest, take all its pages).

2. **RSS 80 MB, phys_footprint 70 MB.** RSS = 20 MB touched clean pages + 60 MB
   resident dirty; the compressed 40 MB left the page tables, and the copies sit in
   the kernel's pool, unattributed. Footprint = 60 MB dirty + 10 MB compressed =
   70 MB; the untouched 780 MB never counted (touching costs, allocating does not),
   and the clean 20 MB is excluded (droppable, re-readable). Footprint is what the
   band limit checks; RSS predicts nothing.

3. **Physical RAM −12 KB** (net free). **RSS −16 KB** (the page left the resident set;
   the 4 KB copy belongs to no process's RSS). **phys_footprint −12 KB** (`internal`
   16 KB → `internal_compressed` 4 KB). An RSS-only tool reads "16 KB freed" and
   misses the 4 KB still owed — megabytes of invisible liability system-wide, exactly
   the debt the kill settles. Footprint keeps the copy charged because killing the
   process frees it.

4. **Still ~500 MB.** `malloc` reserves *address space*; nothing is charged until a
   touch faults pages in — module 01 ch03's "malloc costs nothing; touching costs a
   fault." The enabler is module 06 ch01's VM map: regions backed lazily by VM
   objects, no physical page until first access. Touch all 200 MB and the answer
   drops ~200 MB — 200 MB of dirty `internal` pages.

5. **Not a crash — a policy kill.** `bug_type` 210 with no thread backtraces is the
   Jetsam signature (crash reports are 309, with stacks). 226,000 × 16,384 ≈ 3.5 GB
   claimed by a frontmost app; `per-process-limit` means phys_footprint crossed the
   *foreground band's resident limit* — no system-wide shortage needed. Fix footprint:
   mapped files instead of decoded copies (clean, ~free), volatile purgeable caches —
   the gauge's dirty + compressed columns would have shown this coming weeks earlier.

6. **Extensions carry per-process highwater marks.** The keyboard sits in
   foreground-support territory, but ~64 MB exceeded a *personal* cap checked
   independently of global pressure — `highwater` kills fire even on an idle system
   (`kern.memorystatus_highwater_enabled` gates the path). The host app's 1.5 GB is
   charged against a different, gigabyte-class limit. Caps assume extensions are
   small and short-lived; keep big buffers in the host — the extension's memory is
   its own bill.

7. **Both measure reclaim falling behind, differently.** `vm-pageshortage` is a
   *stock* signal: free pages below the pressure level's threshold (set at boot from
   RAM size) — kills are level-driven. PSI is a *flow* signal: time tasks spend
   stalled in reclaim plus refault rates, so lmkd kills before free memory runs out.
   Both refuse victim-scoring because mobile is *band policy first*: the platform
   already ranked processes (`oom_score_adj`, the Jetsam ladder), and the kernel
   kills the worst band's fattest member; the desktop synthesizes a ranking at kill
   time (badness: size, age, privilege). Mobile ranks *policy*; desktop ranks
   *victims*.

8. **Two of several.** (a) The warning and limit paths are independent: a
   `per-process-limit` kill is the *ledger* crossing the band limit, not the pressure
   level that produces warnings — it can arrive with no warning at all. (b) The kill
   is an asynchronous SIGKILL: once memorystatus decides, no user code — including a
   mid-purge handler — gets another instruction. (Credit: suspended apps cannot run
   handlers; warnings are advisory.) Make the app cheap to kill; you cannot make it
   unkillable.

</details>
