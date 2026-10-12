# Chapter 01 — Boot & the Zygote

> **The 30-second version.** Press power and a chain fires: immutable ROM
> code verifies the bootloader, the bootloader verifies the system image,
> the kernel demand-pages itself in, and init — PID 1 — boots services as
> a *data* problem, parsing `.rc` text instead of running a script. One
> of those services is the Zygote: a JVM that loads thousands of
> framework classes once, then never launches an app the naive way again.
> Every app process is a `fork()` of that warm process — copy-on-write
> hands each app the whole framework for a page-table copy, charging RAM
> only for pages the app actually writes. system_server is the first,
> privileged sibling, and most of your boot animation is the framework
> paying its startup cost once: module 01 ch03's COW promoted to an OS
> design decision.

## 1. The chain: six stages, six memory tricks

At power-on the CPU fetches its first instruction from a fixed address —
baseline: mask ROM, a few KB burned in at the factory. Unpatchable,
which is the point: nothing precedes it, so it is the root of trust. Its
one job — load the next stage and check its signature — is the rule that
repeats up the chain, *hash the next stage before you trust it*: the
bootloader verifies the OS image via Android Verified Boot 2.0 (AVB,
since Android 8.0) — hashes in `vbmeta` — and dm-verity keeps checking
the system partitions' blocks at runtime. Chapter 06 owns the
cryptography; here only the shape matters — every stage does one small
load-and-check.

The kernel (Linux 6.1 on Android 14) contributes demand paging, module
01 ch03: pages materialize only on fault. Then init (PID 1) runs, and
from here on boot is a data problem — the last two links supply this
chapter's two tricks, *pay once* and *copy-on-write*.

```text
[ ROM ]     mask ROM, immutable. Trick: nothing precedes it — trust is
   |        silicon.
   v
[ bootloader ]  ABL / U-Boot class. Trick: hash before trust — vbmeta
   |        (AVB 2.0, Android 8+), dm-verity from here on (ch06).
   v
[ kernel ]  Linux 6.x (Android 14). Trick: demand paging — pages appear
   |        on fault (module 01 ch03).
   v
[ init ]    PID 1. Trick: boot as DATA — .rc text parsed into a service
   |        table + trigger rules; no boot script.
   v
[ zygote ]  app_process64 --zygote. Trick: pay once — boot image +
   |        ~3-4k classes + resources preloaded; forks system_server,
   |        parks in accept().
   v
[ app ]     fork + specialize. Trick: COW — inherits the warm heap
            read-only, shared until written. An app never exec()s.
```

## 2. init: the boot sequence as a data problem

init is a plain C program whose behavior is almost entirely not coded:
it is compiled at boot from text — `/init.rc` plus every
`/system/etc/init/*.rc` and `/vendor/etc/init/*.rc` (Treble, Android 8+).
Three shapes matter:

```text
# schematic .rc — the idiom every Android boot file uses (AOSP syntax)

service zygote /system/bin/app_process64 -Xzygote /system/bin \
    --zygote --start-system-server     # a service = executable + policy
    class core                         # which start wave it belongs to
    user root                          # uid it runs as
    socket zygote stream 660 root system   # init creates /dev/socket/zygote
    critical                           # restart policy lives HERE, as data

on property:sys.boot_completed=1       # an action = trigger + commands
    stop bootanimation                 # fires whenever that property is set
```

Read it as a database, not a script: `service` records declare what can
run, with restart policy, sockets, uid, and SELinux context as
attributes; `on <trigger>` records are pattern rules over the property
store. Boot order *emerges from data dependencies*
(services start in class waves: `core`, `main`, `late_start`), and a
vendor adds a daemon by shipping a `.rc` file, not by editing code.
Failure policy is data too: a `critical` service crashing more than 4
times within 4 minutes (AOSP init's constants) reboots the device —
zygote has it (see question 8).

The stanza is real (trimmed from AOSP's `init.zygote64.rc`, Android 14).
The `socket` line matters most: **init, not zygote, creates
`/dev/socket/zygote`** before the service runs — the fork trigger of
section 4.

## 3. Zygote: pay the framework's cost once

`app_process` is a small native binary whose job is to become a JVM —
link ART, enter the runtime. Started with `--zygote`, it pays the
expensive half of "Android boots" once:

- **Maps the boot image** (`/system/framework/boot.art`/`boot.oat`) —
  the framework AOT-compiled at build/OTA time, guided by the boot-image
  profile shipped with the image (Android 10+; pipeline in ch02).
- **`preloadClasses()`** — static initializers for every class in
  `frameworks/base/preloaded-classes`: ~3,000–4,000 entries (Android 14
  AOSP), hand-curated like an ABI.
- **`preloadResources()`** — framework-res's common drawables and color
  state lists, plus native libraries (`android`, `compiler_rt`,
  `jnigraphics`) and font/graphics setup.

Pixel-class hardware pays ~3–6 s for this (Android 14; 10+ s a decade
ago) — one payment. Derive why it must be paid this way:

**The naive alternative.** Launch each app as a fresh JVM: parse the dex
of thousands of framework classes, verify it, execute every `<clinit>` —
CPU-seconds per app, plus a *private* copy of the result. Call the
warmed framework footprint F — order 100 MB, 64-bit Android 14
(illustrative). N running apps cost N × F of RAM plus seconds of CPU per
launch.

**The zygote world.** `fork()` copies page *tables*, not pages: every
writable zygote page goes read-only in both processes (the COW bit), and
frames stay shared until someone writes. Framework pages are *read-mostly* in apps — they
call framework code far more than they scribble on framework data — so
the steady state is:

```text
             zygote after preload: the warm heap (F ~ 100 MB, illustrative)
        +----------------------------------------------------------------+
        | boot.oat code pages    file-backed, execute     [CLEAN]       |
        | boot.art image space   file-backed, read-only   [CLEAN]       |
        | preloaded heap objects dirtied during preload   [DIRTY]       |
        +----------------------------------------------------------------+
             | fork()      | fork()        | fork()          | fork()
             v             v               v                 v
           app A         app B           app C  ...         app N
        PTE: R+COW       R+COW           R+COW              R+COW
             |                             |
        writes 2 pages                writes 1 page
        COW fault: kernel copies just those frames -> A's private RW copies

        physical RAM  =  F  +  (pages actually written, summed over apps)
        launch latency =  a page-table copy: single-digit milliseconds
```

With N = 20 apps each dirtying ~2 MB of copies, the tally is F + N·d ≈
100 + 40 = **140 MB** versus the naive N·F = **2,000 MB** — an order of
magnitude, widening with N. The shared pages sit at **dirty-0 per app**:
each app's RSS reports the whole inherited framework, private-dirty
stays near zero, and proportional set size (PSS) charges each app
1/sharers — the accounting the lab shows in `dumpsys`.

Two consequences carry the rest of the module:

1. **Fork, never exec.** `exec()` is a bulk unmap-then-map (module 01
   ch03): it would erase the warm heap. App processes are the mainstream
   world's only fork-without-exec processes — the app's dex loads
   *in-process* after the fork, mmap'd from the APK; there is no app
   binary to analyze cold, and code is `boot.oat` plus the APK's dex.
2. **The read-mostly discipline.** The savings exist only while apps
   write few framework pages. ART cooperates: the preloaded heap becomes
   a separate **ZygoteSpace**, so post-fork allocations go elsewhere and
   never trip COW on preloaded objects (chapter 02 unpacks the layout).

## 4. Launching an app: the socket, the specialize step, and the pool

With zygote parked in `accept()`, who pulls the trigger? Not the user,
not init — **system_server**, through the socket init created.

**The socket protocol.** The ActivityManagerService (AMS), in
system_server, holds the other end of the socket. To start an app it
writes an argv-shaped request: uid/gids, `seInfo`, niceName, APK
classpath, target SDK, rlimits. Zygote's select loop
(`ZygoteServer.runSelectLoop`) accepts and
forks; the child immediately runs `Zygote.forkAndSpecialize` — the
specialize step, before any app code runs: drop from root to the app
uid, enter the app's SELinux domain, install its seccomp filter
(Android 8+), set up mounts and namespaces (chapter 05's sandbox,
applied at launch) — then load the app's dex in-process and call
`ActivityThread.main()`, writing its pid back on the socket (fork-then-
specialize is forced: exec after would erase the heap).

**The fork is the dangerous half.** `fork()` clones only the *calling*
thread. Zygote is a multi-threaded JVM — a lock held by another thread
at the fork instant stays locked forever in the child, whose
owner does not exist there: a deadlock factory. So ART quiesces the
runtime before every fork (`ZygoteHooks.preFork`: stop JIT/GC threads,
run a GC) and the child re-arms its threads after (`postForkChild`) —
children also fork from a tidy heap.

**USAP: the pool.** The select loop serializes — one fork per accept —
and mass launches (post-OTA app restores) make the queue visible.
Android 10 added the **Unspecialized App Process (USAP) pool**: pre-forked
children, forked *but not specialized*, each parked on its own
session socket. AMS's `UsapPoolConnector` writes the request into the
pool; whichever USAP reads it specializes, so specializations run in
parallel instead of queuing. The pool is tiny by design, capped at 3 by
AOSP constants.

**system_server, the big sibling.** Zygote's *first* fork
(`--start-system-server`) is not an app: it specializes to uid 1000
(`system`) in the `system_server` SELinux domain — what users
mean by "Android itself." Over a hundred services start in
phases (`SystemServer.run()`): ActivityManagerService (AMS — process
lifecycle, the sole ordinary client of the zygote socket),
PackageManagerService (PMS — the installed-app database),
WindowManagerService (WMS — what is on screen), plus power, input,
notification. If system_server dies, the framework "soft
reboots": zygote goes down with it, init restarts the critical service,
the boot animation replays, and the preload is paid again — a framework
restart without a kernel reboot.

## 5. Where the seconds go: a boot-time budget

Boot time is a memory story twice over: the framework's heap is being
paid for, and the user sees nothing until enough exists. The
instrumentation ships on every device — `boot_progress_*` markers in the
events log, stamped in ms of kernel uptime:

```console
$ adb logcat -b events -d | grep boot_progress
01-01 00:00:08.234  412  412 I boot_progress_start: 8234         # zygote up
01-01 00:00:08.511  412  412 I boot_progress_preload_start: 8511 # preload begins
01-01 00:00:12.995  412  412 I boot_progress_preload_end: 12995  # 4.5 s of preload
01-01 00:00:13.248  863  863 I boot_progress_system_run: 13248   # system_server
01-01 00:00:26.806  863  863 I boot_progress_ams_ready: 26806    # 13.6 s of services
01-01 00:00:30.112  863  863 I boot_progress_enable_screen: 30112 # animation ends
```

Read as a budget (Pixel-class, Android 14; numbers vary by build):

| window | who is on stage | typical cost |
|---|---|---|
| ROM + bootloader | hash chain, radio init | 1–3 s — *invisible to all uptime logs* |
| kernel | drivers, demand paging | 1–2 s |
| init | parse .rc, `class core` wave | ~0.5 s |
| zygote preload | thousands of classes + resources | 3–6 s |
| system_server | 100+ services in phases | 10–15 s — **the biggest slice** |

The insider's model: *post-kernel boot = one preload plus one
system_server init.* The bootloader's seconds are a blind spot — kernel
uptime starts at kernel entry, so treat the first `dmesg` timestamp as
zero.

## What insiders know

- **What the preload actually is**: a git-reviewed list
  (`frameworks/base/preloaded-classes`, ~3,000–4,000 entries, Android 14),
  the `preload_drawables`/`preload_color_state_lists` resource arrays,
  three native libraries, and the AOT boot image built from a boot-image
  profile (Android 10+). Classes whose `<clinit>` has side effects —
  threads, sockets, registries — are preload poison.
- **The fork trigger is a socket**: `/dev/socket/zygote`,
  created by *init* (mode 660 root:system); the protocol is a
  newline-separated argv array, the reply the child's pid. A 32-bit
  secondary zygote (`getprop ro.zygote` → `zygote64_32` kin) adds
  `/dev/socket/zygote_secondary`.
- **USAP mechanics**: pre-forked, unspecialized children parked on their
  own session sockets (Android 10+); AMS writes into the pool and
  whichever USAP reads it specializes — parallelism, not latency magic.
  Pool capped at 3 (AOSP constants), refilled as it drains.
- **The quiesce is the real fork trick**: `fork()` in a multithreaded
  program is only safe if no other thread holds a lock — ART stops its
  JIT and GC threads and runs a GC before every fork, so children are
  born single-threaded from a compact heap. A `<clinit>` that starts a
  thread breaks the invariant: the thread doesn't survive fork, but its
  locks do.
- **system_server is one process with a hundred jobs**: uid 1000, first
  zygote fork, hosts AMS/PMS/WMS plus the long tail; its crash
  soft-reboots the framework (section 4), and 4 crashes in 4 minutes
  escalates to a full reboot via the `critical` policy.
- **Apps never exec()**: the only mainstream OS where app creation is
  pure fork — no standalone app binary to reverse-engineer; code is
  boot.oat (shared) plus the APK's dex, mapped after the fork.

## See it yourself

Fragments below are a **documented walkthrough** (Pixel-class, Android 14
/ API 34, userdebug — no live device was attached for this transcript;
numbers will differ, any emulator API 30+ reproduces the shapes). On user
builds `dmesg` is restricted; the events log and `dumpsys` work
everywhere.

**1. Every app's parent is a zygote** — the parentage claim, checked:

```console
$ adb shell ps -A -o PID,PPID,NAME | grep -E 'PID|init$|zygote|system_server|com.android'
   PID  PPID NAME
     1     0 init
   412     1 zygote64                    <- child of init, from the .rc service
   863   412 system_server               <- zygote's FIRST fork
  1204   412 com.android.chrome          <- an ordinary app: PPID = 412
```

`getprop ro.zygote` prints `zygote64` (or `zygote64_32` — `ps` then also
shows a plain `zygote`), and `getprop init.svc.zygote` prints init's
live state: `running` or `restarting` — restart policy, visible as a
property.

**2. The boot timeline.** Section 5's markers are the framework half;
the kernel half is `dmesg`, timestamped in seconds since kernel entry:

```console
$ adb shell dmesg | grep -E 'Linux version|Freeing unused' | head -2
[    0.000000] Linux version 6.1.75-android14-... (kernel entry = t0)
```

Note the blind spot: anything before `0.000000`. Both clocks are kernel
uptime — they line up, and the two big framework windows (preload ~4.5 s,
system_server ~13.6 s) are the budget.

**3. Zygote-shared accounting in one app:**

```console
$ adb shell dumpsys meminfo com.android.settings
                   Pss  Private  Private      Rss
                 Total    Dirty    Clean    Total
 Native Heap     18934    18931        0    18940
 Dalvik Heap      9412     4330        0    41230   <- RSS >> Pss: zygote-shared
     .art mmap    3876       68     3808    62466   <- boot image: shared clean
      TOTAL      51234    41000        0   240000
```

(Trimmed; real output adds rows and a `SwapPss` column.) The columns
tell the COW story: `.art mmap`'s 62 MB RSS vs ~4 MB PSS is the boot
image — file-backed, mapped into every process, charged 1/sharers per
app. `Dalvik Heap`'s gap is the preloaded heap tail shared with zygote
and every forked app; its Private Dirty (4 MB) is what *this app wrote* —
the COW copies.

## How to use this knowledge

- **App-launch triage.** Slow cold start splits cleanly: fork/USAP
  serialization (mass-fork storms in the events log), system_server
  stalls (the AMS markers), or the app's own post-fork class loading —
  each lives in a different log.
- **Read meminfo like an insider.** "This app uses 40 MB" is RSS-speak;
  the defensible number is PSS plus private dirty, and the gap between
  them is exactly the zygote sharing derived above.
- **The interview derivation.** "Why is an Android app a fork?" —
  naive JVM-per-app costs N × F in CPU-seconds and RAM; fork + COW costs
  F once and page-table copies forever after, and exec would erase the
  one thing you paid for.

## Connections

- **Module 01 ch03 (virtual memory/COW)** — its fork/COW section
  promoted to an OS-launch primitive; the PTE/refcount mechanics run
  under every claim here.
- **Module 05 ch02 (ART)** — the boot image, image space, and
  ZygoteSpace get their full treatment; ch03 (Binder) — how
  AMS's request travels to zygote's socket; ch04 (lmkd) — what happens
  to these forked processes under pressure; ch05 (sandbox) — the
  uid/SELinux/seccomp specialize step; ch06 (APK/OTA) — the AVB/dm-verity
  chain deferred from section 1.
- **Module 06 ch04 (dyld & the shared cache)** — the warm-start
  parallel: iOS pre-links every framework into one COW-mapped cache with
  precomputed launch closures; Android warms via preload + fork. Same
  economics, different mechanism.
- **Module 08 ch04 (CICS)** — the deepest root: programs loaded once and
  shared across transactions, with a quasi-reentrancy rule that *is* the
  read-mostly discipline, on 1969 hardware.
- **Module 04 ch01 (binary formats)** — why there is no app binary to
  analyze cold: code = boot.oat + mmap'd dex, not an exec'd image.

## Quiz

1. *(concept)* Why is `fork()` — not `exec()`, not "start a fresh JVM" —
   the app-launch primitive on Android? Derive it from what each
   operation does to the warm heap.
2. *(concept)* Why does `preloadClasses()` run once per boot rather than
   once per app, and what property must a class have to be eligible for
   the preload list?
3. *(predict the number)* A 4 KiB page in zygote's preloaded heap,
   dirtied during preload, is mapped by zygote plus three forked apps.
   Give each process's PSS contribution before any write and after app A
   writes it, plus the physical page count.
4. *(predict the timeline)* Given `boot_progress_start: 8234`,
   `boot_progress_preload_end: 12995`, `boot_progress_system_run: 13248`,
   `boot_progress_ams_ready: 26806` — where did the largest slice of
   framework boot time go, and which process was executing during it?
5. *(bug diagnosis)* A framework class is added to the preload list; its
   `<clinit>` starts a background thread that populates a static cache.
   Apps hang waiting for that cache; logcat shows the thread only in
   zygote, in no app process. Explain both.
6. *(bug diagnosis)* Right after an OTA first boot, 30 apps restore in a
   burst; every launch in the burst pays an extra ~50–100 ms even though
   zygote is healthy and a lone fork measures single-digit ms. What
   serializes them?
7. *(cross-module, module 01 ch03)* An app's write to a shared framework
   page faults a COW copy, yet zygote's RSS does not grow. In PTE terms,
   what did the kernel do, and why did the other 19 apps sharing the
   frame not notice?
8. *(bug diagnosis, cross-module)* You `kill` system_server on a
   userdebug device. What does the user see over the next ~20 seconds,
   what does init do, and why is no app data lost? What changes at 4
   crashes in 4 minutes?

<details>
<summary>Answers</summary>

1. **Fork keeps the heap; exec erases it.** The warm heap — thousands of
   initialized classes plus the mapped boot image — is the only expensive
   asset. `exec()` is an unmap-then-map: it discards those
   pages, forcing a re-parse, re-verify, and re-run of every `<clinit>`
   — CPU-seconds plus a private ~100 MB copy per app; a fresh JVM has
   the same cost without even the fork. `fork()` copies only page
   tables, and COW leaves the framework shared read-only.
2. **Amortization, then eligibility.** Once per boot is the difference
   between paying F and N × F. A class qualifies only if its static
   initialization is side-effect-free — no started threads, opened
   files, or global registration — because every forked app inherits
   that state verbatim, and threads and locks do not survive `fork()`
   (Q5).
3. **Before:** the frame is read-only+COW in 4 processes; PSS charges
   4 KiB/4 = **1 KiB each**. **After A writes:** the fault handler sees
   refcount 4 > 1, copies the page to a fresh frame, maps A's PTE to the
   copy read-write, and drops the old frame's refcount to 3. A now pays
   **4 KiB private**; zygote, B, and C pay 4/3 ≈ **1.33 KiB**. Two
   physical frames serve 4 processes; A's copy is exactly what "Private
   Dirty" means.
4. **system_server service init: 26,806 − 13,248 = 13,558 ms**, the
   largest window, spent inside system_server (zygote's first fork, uid
   1000) starting 100+ services. Preload (≈4.5 s) is the
   runner-up. Rule: post-kernel boot = one preload plus one
   system_server init.
5. **Threads do not survive `fork()`.** The `<clinit>` ran once, in
   zygote, before any fork — the thread exists only there (hence the
   logcat absence in apps). Apps inherit the Thread *object* and empty
   cache, but no live thread: the cache never populates; waiters hang.
   Worse, a lock the thread held at a fork instant is
   inherited locked forever — its owner never existed in the child. Hence
   ART's pre-fork quiesce, and the side-effect-free rule for preloads.
6. **Zygote's accept loop is single-threaded.** Fork-and-specialize
   serializes through one select loop, each fork paying the runtime
   quiesce + GC. USAP (Android 10+) exists for exactly this — a few
   specializations in parallel — but the pool is capped at 3, so a
   30-app burst still queues, ~50–100 ms per launch.
7. **Only the writer's PTE changes.** The write faulted on a read-only
   PTE; the kernel saw the COW marking,
   refcount > 1, allocated a fresh frame, copied the 4 KiB into it, and
   rewrote only *this* process's PTE — new frame, writable. The other 19
   apps' PTEs still point at the original frame,
   read-only, refcount merely decremented — they fault on nothing.
   Zygote lost no frame, so its RSS is unchanged.
8. **A soft reboot.** The UI freezes and the boot animation replays:
   system_server's death takes zygote down; init sees its `critical`
   service gone, restarts it, and zygote re-pays the preload before
   re-forking system_server (~20 s class). No data is
   lost — the kernel never rebooted; app data lives in `/data`. But 4
   crashes within 4 minutes trips the `critical` threshold, and init
   reboots the entire device.
</details>
