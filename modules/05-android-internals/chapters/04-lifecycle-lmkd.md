# Chapter 04 — Lifecycle & lmkd

> **The 30-second version.** A phone has no disk swap — flash writes are
> slow, wear the media, and cost battery — so when RAM runs out, the reclaim
> ladder ends not in paging but in a kill. Android makes that tolerable by
> turning "who dies first" into policy: every process carries an
> `oom_score_adj` from −1000 (never kill) to 999 (kill first), assigned by
> ActivityManager from lifecycle state — top activity 0, foreground service
> ~200, cached process 900+. A userspace daemon, **lmkd**, watches pressure,
> not free memory: PSI (kernel 4.14+) measures the *time* tasks lose to
> memory stalls; workingset refault flags reclaim evicting pages that come
> right back. When pressure crosses a threshold (70 ms of stall per 1 s
> window, by default), lmkd kills the highest-adj, largest-RSS victim and
> repeats until pressure subsides. Cached processes are also frozen (cgroup
> v2 — 0 CPU) and squeezed through zram (compressed RAM, ~3:1): everything
> here exists to make killing cheap, rare, and invisible.

## No swap, therefore kill

Desktop Linux treats the OOM killer as an emergency exit (module 01 ch03):
the reclaim ladder runs drop-clean-pages → shrink caches → swap → and only
then does the kernel score processes for death. Android amputates the middle:
flash writes burn endurance, a random flash read costs hundreds of µs to
milliseconds (a 10 ms page fault is an ANR in the making), and the I/O costs
battery. So the ladder becomes: reclaim caches → compress what zram holds →
**kill a process**.

On a desktop, "the OOM killer fired" is an incident; on Android it is
Tuesday — cached processes die thousands of times a day, and the user must
never notice. Three mechanisms buy that:

1. **Importance is decided before pressure exists.** The number comes from
   lifecycle state, not heap size or CPU use; pressure merely *executes* the
   ranking.
2. **Relaunch is cheap** (module 05 ch01): a dead app re-forks from the warm
   Zygote — framework pages already resident and shared — so cold start is
   hundreds of ms, not tens of seconds.
3. **State survives by contract.** Recents keeps the task with a snapshot;
   the relaunched activity gets its `onSaveInstanceState` bundle back;
   sticky services restart.

Android's process lifecycle is a *garbage collector for processes*:
reachability = user-visible importance, collected under pressure, lost state
pushed into the app's serialization duties.

## oom_score_adj: importance as a single integer

The kernel already has the slot: `/proc/<pid>/oom_score_adj` runs −1000
(never pick) to +1000 (kill first). Desktop Linux leaves it near 0 and
computes "badness" at kill time; Android inverts the split: **the kernel
keeps the priority, userspace owns the policy.** AMS (in system_server)
computes importance — which activity is on top, who binds whom, which
services run, who was used last — maps it to a band, and writes it into the
process's `/proc` entry on every transition: `onPause`/`onStop` are not
just callbacks, they are *sensor readings* that move the process between
bands.

The bands (AOSP `ProcessList.java`; classic values, Android 8-era):

```text
 protected side (kernel will not pick these)          killable first
 adj     band               who lives there                     example
 -1000   NATIVE             not managed by AMS                 surfaceflinger, audioserver
  -900   SYSTEM             the framework server                system_server (AMS, WMS, PMS)
  -800   PERSISTENT         persistent apps + their services    com.android.phone
     0   FOREGROUND         top activity                        your app, on screen
  0..99  VISIBLE+layer      visible but not top                 split-screen peer, behind-dialog
   200   PERCEPTIBLE        user sees or hears it               foreground-service music, bound-by-FG
   300   BACKUP / 250 LOW   lower-priority visible work
   400   HEAVY_WEIGHT       heavy-weight app backgrounded
   500   SERVICE            long-idle started service           sync adapter work
   600   HOME               the launcher                        com.android.launcher3
   700   PREVIOUS_APP       the app you just left               keeps "back" instant
   800   SERVICE_B          recent started services             leftover background work
 900-999 CACHED             invisible, no live components       everything else — budget: 32
```

Two version notes: pre-9, *visible* was its own band at 100 and perceptible
200 (the "100–200" still quoted); Android 9 merged visible into 0 with
*layer offsets* 0–99, so an activity one layer behind the top survives
longer than one three deep. And cached is a slope, not a cliff: each cached
process gets its own sub-slot in 900–999, more recently used nearer 900 —
recency buys a few points of survival, nothing more.

That range has a **budget**: 32 cached processes
(`DEFAULT_MAX_CACHED_PROCESSES = 32`, `ActivityManagerConstants.java`,
Android 10–14; tunable via DeviceConfig key
`activity_manager_constants/max_cached_processes`). Cached *and empty*
processes (no live components, kept as a relaunch cache) share it. When the
33rd goes cached, AMS kills the least-recently-used one itself — budget
enforcement is ordinary housekeeping. Low-RAM devices
(`ro.config.low_ram=true`) run smaller.

One-sentence derivation: **survivability ∝ how much the user would notice
the process vanish.** An audio glitch is noticed; a dead background tab
isn't, so long as relaunch looks instant.

## lmkd: from free-memory thresholds to pressure

The first kill switch lived *in the kernel*: the
`drivers/staging/android/lowmemorykiller` driver, a shrinker callback with
two tunable arrays (`/sys/module/lowmemorykiller/parameters/{minfree,adj}`).
When free pages fell below `minfree[i]`, it killed the process with the
highest `oom_score_adj` above `adj[i]`. Simple, cheap — and broken three
ways:

1. **Free memory stopped meaning anything.** A kernel with zram and an eager
   page cache *wants* free memory near zero — free is wasted RAM. Static
   thresholds misfired early or never, and every vendor forked its own
   numbers.
2. **Policy was frozen in kernel space.** Improving importance ordering
   meant rebuilding the kernel, not updating the framework.
3. **Levels can't see duration.** 50 MB free for 3 ms is nothing; 50 MB for
   800 ms is a dropped frame budget. A threshold reports a level, not time
   spent in distress.

Android 9 moved the killer to userspace — **lmkd**, an init-launched
critical daemon (`system/memory/lmkd`). AMS registers each process (pid +
adj) over a socket; lmkd owns only the watch and the kill. Android 10 gave
it the right sensor: **PSI, Pressure Stall Information** (upstream Linux
4.20; backported to Android common 4.14+).

PSI answers the only question a user feels: *how much time is the system
losing to memory?* `/proc/pressure/memory` reports two lines:

```text
some avg10=0.12 avg60=0.08 avg300=0.05 total=481234567
full avg10=0.00 avg60=0.00 avg300=0.00 total=0
```

`some` is the percentage of time *at least one* task was stalled on memory;
`full` is the time *all non-idle* tasks were stalled at once — the machine
effectively stopped. Averages are over 10/60/300 s, `total` is cumulative
µs. This measures the symptom (jank, ANR) directly: PSI says "reclaim is
not keeping up"; free memory says "the kernel is using RAM as a cache,"
which it always is.

PSI events are *edge-triggered*: write `"some 70000 1000000"` to
`/proc/pressure/memory` and a poll on it wakes when 70 ms of `some`-stall
accumulate in a 1 s window. lmkd arms two triggers by default — **partial**
at 70 ms/1 s, **critical** at 700 ms/1 s (`ro.lmk.psi_partial_stall_ms`,
`ro.lmk.psi_complete_stall_ms`, Android 10–14) — then cross-checks two
oracles:

- **zram fill** — a nearly-full zram (default gate `ro.lmk.swap_util_max`,
  ~80%) means compression has stopped helping.
- **workingset refault rate** — from `/proc/vmstat`, `workingset_refault`
  (evicted pages that fault right back in) over activations — the "are
  pages usefully used?" oracle. High refault means reclaim is evicting *the
  wrong pages*: memory is full of working set, not garbage, and the honest
  responses are kill or uncompress, not evict harder. Above ~60% counts as
  thrashing (`ro.lmk.thrashing_limit`, Android 10+).

The kill decision, then:

```text
  PSI event: "some 70ms in 1s" (or 700ms critical)
        │
        v
  lmkd wakes, reads /proc/meminfo + vmstat + zram fill
        │
        v
  classify pressure level ──> adj floor for candidates
        │
        v
  victim = argmax over registered procs of:
        1. oom_score_adj          (kill the least important first)
        2. tie-break: RSS          (free the most pages per kill)
        3. Android 12+: per-process zram swap-in cost (cold = cheaper)
        │
        v
  SIGKILL victim → notify AMS → re-read pressure ──> still high? repeat
```

That arithmetic is the whole policy: **adj dominates, RSS breaks ties, and
on newer builds cold, compressed pages make a process a better victim than
a smaller one whose pages keep refaulting** — "better" meaning pages freed
per unit of importance destroyed. Note what is *absent*: no heap size, no
CPU usage, no age. lmkd is the executor, not the judge.

The user sees no dialog, no crash report, no tombstone. The binder driver
notices the `/dev/binder` fd dying and delivers death notifications
(module 05 ch03); AMS removes the record, keeps the Recents snapshot, and
schedules restarts by component semantics — `START_STICKY` services restart
with a null Intent, `START_NOT_STICKY` services do not, jobs reschedule.

## Death, the freezer, and zram's one concession

Between "alive" and "killed" Android now has a third state: **frozen**
(Android 11+ on cgroup v2 kernels; upstream freezer since 5.3). Writing `1`
to `/sys/fs/cgroup/uid_<uid>/pid_<pid>/cgroup.freeze` stops the process at
the kernel boundary — scheduled off the CPU, *unable* to run: no timers,
no broadcasts, no binder thread churn, no wakelocks. Frozen cached
processes cost 0 CPU — most of a cached app's residual battery drain, gone.
The sharp edge is binder: a transaction aimed at a frozen process must not
silently wait, so libbinder gained freeze handling (`BINDER_FREEZE`,
`BR_FROZEN_REPLY`, Android 12) and AMS thaws before delivering anything
that matters. Frozen does not change adj — still a kill candidate —
freezing only stops the *running* cost.

And the one concession to swap: **zram** — a block device whose backing
store is RAM. Pages written to it are compressed (lz4/zstd); "swap" never
touches flash, so there is no wear and the worst-case fault is a decompress,
not disk I/O. It is a ratio lever: at a typical ~3:1, 2 GiB of zram holds
2 GiB of pages in ~667 MiB of RAM. Android commits hard — `vm.swappiness`
pinned at 100 (Android 9–11), 160 on Pixel-class builds since Android 12,
versus the desktop default 60 — and lmkd treats zram saturation as a
pressure input. zram buys ~1.3 GiB of headroom on a 4 GiB phone and delays
each kill; when pressure still wins, the answer remains kill.

## What insiders know

- **The daemon logs under the tag `lowmemorykiller`** — the kernel driver it
  replaced is gone, but LOG_TAG was kept for filter compatibility. `adb
  logcat -s lowmemorykiller` is *the* kill-feed.
- **The cached budget is 32** (`DEFAULT_MAX_CACHED_PROCESSES`,
  `ActivityManagerConstants.java`, Android 10–14), live-tunable via
  `device_config put activity_manager_constants max_cached_processes N` —
  the standard trick for faking a low-RAM device.
- **PSI's two lines differ**: `some` high with `full` zero is one task
  thrashing (often one app, not the system); `full` nonzero means the whole
  CPU stopped on memory — an emergency. Triggers: partial 70 ms/1 s,
  critical 700 ms (`ro.lmk.psi_partial_stall_ms` /
  `ro.lmk.psi_complete_stall_ms`).
- **A foreground service holds adj ~200, below the 900 cliff** — that, not
  "being in use", is what keeps music playing under pressure. It's also why
  Android 14 requires declared FGS types: the adj perk was abused as a
  keep-alive.
- **Frozen ≠ killed**: check `/sys/fs/cgroup/uid_*/pid_*/cgroup.freeze`
  before diagnosing "background work stopped with no crash" — 1 means 0 CPU,
  timers included, with the process still alive in Recents.

## See it yourself

*Documented walkthrough — no device was attached at write time; fragments
show AOSP 14 output shape, expect label drift. Any API 34+ emulator
reproduces them.*

```sh
# 1. The kernel-side truth (any Android):
adb shell pidof com.android.example.app     # e.g. 5678
adb shell cat /proc/5678/oom_score_adj
# with the app on screen:
0
# after Home + ~10 s (AMS demotes on the cached transition):
906
```

```sh
# 2. AMS's scoreboard:
adb shell dumpsys activity oom | grep -m5 "adj="
#  Proc # 0: adj=   0 ... # 1287:com.android.example/u0a123 (top-activity)
#  Proc # 3: adj= 200 ... # 3112:com.music.player/u0a88 (fgs-service)
#  Proc # 8: adj= 906 ... # 5678:com.android.example/u0a123 (cached)
```

The same numbers also live inside each ProcessRecord block of
`dumpsys activity processes` — `oom` is just the condensed view.

The two kill lines (wording drifts by version — grep the tag, not the
sentence):

```text
I ActivityManager: Killing 7789:com.browser/u0a51 (adj 906): cached #17
I lowmemorykiller: Killing 'com.android.example' (5678) to free 118204kB rss
```

`(adj 906): cached #17` is the *budget* path — #17 is the cached slot; the
lmkd line is the *pressure* path.

```sh
# 3. Pressure, live:
adb shell cat /proc/pressure/memory
# some avg10=0.12 avg60=0.08 avg300=0.05 total=481234567
# full avg10=0.00 avg60=0.00 avg300=0.00 total=0
adb shell cat /proc/vmstat | grep -E "workingset_(refault|activate)"
# workingset_refault_anon 531223
# workingset_activate_anon 118734
```

```sh
# 4. Cached budget + build overrides (empty = default in force):
adb shell getprop | grep -iE 'lmk|low_ram'
# [ro.config.low_ram]: [false]
# [ro.lmk.psi_partial_stall_ms]: []
adb shell device_config put activity_manager_constants max_cached_processes 6
adb logcat -s lowmemorykiller ActivityManager   # launch >6 apps, watch kills
adb shell device_config delete activity_manager_constants max_cached_processes
```

```sh
# 5. The freezer (uid = 10000 + appId, so u0a123 -> 10123; module 05 ch05):
adb shell cat /sys/fs/cgroup/uid_10123/pid_5678/cgroup.freeze
# 1        <- frozen: 0 CPU, still listed in Recents, still killable

# 6. zram's ratio (mm_stat: orig_data_size  mem_used_total  ...):
adb shell cat /proc/swaps
# /dev/block/zram0  partition  1535996  512436  5
adb shell cat /sys/block/zram0/mm_stat
# 790511616 263137280 0 263137280 ...
# 790 MB of pages held in 263 MB of RAM  ->  3.0:1
```

For deterministic testing: `adb shell am kill com.android.example` kills
*only if* the process is cached — use it to test your
`onSaveInstanceState` round-trip exactly as an lmkd kill would.

## How to use this knowledge

- **OOM-kill triage from logs alone.** An `ActivityManager: Killing ...
  (adj N)` line with adj below 200 on a non-foreground process is a *policy*
  bug — something visible was demoted, and the reason string says which
  sensor failed. A `lowmemorykiller: ... to free NkB` line at adj 900+ is
  the system working as designed; the fix belongs in the app, not the
  build.
- **Decide foreground-service vs. cached deliberately.** The 200/900 cliff
  is the whole decision. Ongoing user-perceived work (audio, navigation, a
  transfer the user started) justifies adj 200 plus the freezer exemption;
  background bookkeeping does not — and Android 12+ gates starting FGS from
  the background, so "promote on a timer" no longer works. Design for
  being killed: persist, restart, reschedule.
- **Build a low-RAM repro rig.** `device_config put
  activity_manager_constants max_cached_processes 6` turns any flagship
  into a budget device for cached-process churn; combine with `am kill`
  for deterministic death tests in CI.
- **Read PSI as a leading indicator.** `some` avg10 spiking with `full`
  still zero says reclaim is losing before users file ANRs; workingset
  refault climbing says the footprint just crossed the useful-cache line.
  And a relaunch to the home screen instead of the user's deep screen is
  the downstream symptom: an lmkd kill plus a missing persist path.

## Connections

- **Module 01 ch03 (virtual memory & OOM)** — the reclaim ladder Android
  amputated: no swap tier, so the OOM step became routine; COW/Zygote is
  what makes the kills cheap to recover from.
- **Module 05 ch02 (ART's heap)** — ART's collector governs memory *inside*
  one process; lmkd governs the set of processes. A pristine heap never
  protects a cached app; `heapgrowthlimit` keeps one app from forcing kills
  of the others.
- **Module 05 ch03 (Binder)** — death notifications deliver the kill news;
  the freezer's `BINDER_FREEZE` handshake is binder meeting cgroups.
- **Module 06 ch02 (the Jetsam comparison)** — same premise (no swap),
  different scoreboard: iOS memorystatus/Jetsam bands vs `oom_score_adj`.
- **Module 07 ch05 (the Linux OOM killer)** — the desktop counterpart:
  badness computed at kill time, watermark-triggered, per-zone — compare who
  owns the priority and why Android split it out.

## Quiz

1. Desktop Linux reaches its OOM killer rarely and treats it as an
   incident; Android kills cached processes continuously and users don't
   notice. Name the mechanisms that make routine killing acceptable, and
   derive why each is necessary.
2. Why exactly is "free memory below N MB" a broken kill trigger on a
   zram-equipped phone, while "70 ms of some-stall per 1 s window" is sound?
   What does each metric claim to measure?
3. lmkd must free memory now. Candidates: A (adj 906, RSS 300 MB), B
   (adj 912, RSS 90 MB), C (adj 700, RSS 500 MB). Which dies first under
   classic ordering, and why doesn't the 500 MB C die despite freeing the
   most?
4. `/proc/pressure/memory` reads
   `some avg10=7.5 avg60=2.0 avg300=0.1 total=...`. lmkd's partial trigger
   is armed as "70 ms of some-stall in a 1 s window". Does the trigger
   fire? Show the arithmetic.
5. zram `mm_stat` shows `orig_data_size` = 768 MiB, `mem_used_total` =
   256 MiB; the device configures a 2 GiB zram. What does it hold at this
   ratio, what does it cost in RAM, and what is the net capacity gain?
6. A user reports: "podcasts stop when I browse the web; when I reopen
   the app it's back on the episode list, not my playback position."
   Logcat shows
   `ActivityManager: Killing 5678:com.pod/u0a45 (adj 906): cached #14`.
   Diagnose the causal chain and give the two-part fix.
7. An app's 15-minute periodic work silently stops on an idle device. No
   crash, no kill line, the app still appears in Recents.
   `cat /sys/fs/cgroup/uid_10456/pid_2311/cgroup.freeze` prints `1`. What
   state is the process in, and why did the work stop?
8. (Module 05 ch02 + this chapter) Your team cut the app's ART heap 40% by
   tuning the concurrent copying collector, yet it is still killed at
   adj 906 at the same moments as before. Why didn't the GC change the kill
   behavior, and what *would* change it?

<details>
<summary>Answers</summary>

1. **Relaunch must be cheap, state must survive, the ranking must
   pre-exist.** Zygote fork (module 05 ch01) recreates the process in
   hundreds of ms — framework pages already resident and COW-shared;
   Recents snapshots plus the `onSaveInstanceState` bundle make death look
   like a relaunch at the same screen; and the adj ladder is computed
   *before* pressure exists, so the kill is policy, not panic. Remove any
   one and a kill becomes user-visible.

2. **Free memory is a level; stall time is a rate.** With zram the kernel
   keeps free memory near zero *by design* — anon pages compress, file pages
   cache — so "below threshold" is the steady state, not distress. PSI
   measures what users feel: time tasks spend blocked in reclaim (`some` =
   at least one task, `full` = all non-idle). 70 ms per second says reclaim
   is not keeping up — pressure's definition — regardless of how much is
   cached or compressed.

3. **B.** Classic ordering is adj descending: 912 > 906, so the 90 MB
   process dies before the 300 MB one — importance dominates, RSS only
   breaks ties. C (previous-app, 700) is not a candidate: the system won't
   trade instant back-navigation for 500 MB. The Android 12+ refinement: a
   huge *cold* process can out-rank a slightly higher-adj small one — pages
   freed per unit of importance destroyed is the real objective.

4. **It fires.** `avg10=7.5` means 7.5% of the last 10 s was some-stall =
   75 ms per second on average; the trigger needs 70 ms in a 1 s window,
   so a window at the average already exceeds it. `avg10=1.0` (10 ms/s)
   would not fire; lmkd triggers on short windows — `avg60`/`avg300` are
   context, not triggers.

5. **Holds 2 GiB of pages, costs ~667 MiB, nets ~1.33 GiB.** The observed
   ratio is 768/256 = 3.0:1; disksize caps what zram *stores* (2 GiB of
   pages), while the physical cost is that data compressed (2 GiB ÷ 3).
   Net extra effective RAM, paid in CPU per compress/decompress — and when
   zram saturates (`swap_util_max`), the answer is still kill.

6. **No foreground service, so playback didn't anchor its importance.**
   Behind the browser it went invisible → cached (adj 906); under pressure
   or the 32-process budget it was killed (cached slot #14). Restart was a
   cold Zygote fork with only the saved bundle; playback position lived in
   RAM, so relaunch shows the episode list. Fix 1: a typed foreground
   service while playing (adj ~200, freezer-exempt). Fix 2: persist position
   on every transition — the FGS delays the kill, persistence removes the
   sting.

7. **Frozen, not dead.** `cgroup.freeze=1` parks the process at the kernel
   boundary: 0 CPU, so timers and scheduled work never run, while the
   process record, adj, and Recents entry remain. It resumes only when
   something unfreezes it (AMS on an importance transition, or the user).
   The fix: don't rely on periodic timers in a cached process — use
   JobScheduler/WorkManager within the app's standby window.

8. **lmkd ranks processes, not heaps.** The kill was budget or pressure
   enforcement on a *cached* process — importance class plus system
   pressure; the app's internal heap hygiene is invisible to both. A
   smaller RSS shifts tie-breaks and total pressure only marginally. What
   would change it: lifecycle policy — a foreground service or visible
   interaction moves the process below the 900 cliff — or persistence so
   the kill is harmless. The collector optimizes module 05 ch02's
   scoreboard; the kill follows this chapter's.

</details>
