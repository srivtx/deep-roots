# Chapter 04 — Containers: the namespaced process

> **The 30-second version.** A container is not a lightweight VM. It is a
> normal process — same syscalls, same page tables, same kernel — whose
> *views* of kernel-global resources have been swapped: 8 namespaces replace
> the pid counter, mount table, network stack, uid map (and 4
> more) with per-process instances; cgroups set the bills (memory ends
> in a kill at `memory.max`, CPU ends in a freeze at `cpu.max`); overlayfs
> builds a private root filesystem from read-only image layers; seccomp,
> capabilities and LSM profiles shrink the set of doors. Nothing new was invented: a runtime is `clone()` with 6-8 flags, mounts,
> and cgroupfs writes. The security truth follows: the kernel is *shared*,
> so the wall is one kernel 0-day wide — the difference from the VM of
> chapters 01-03.

## One mechanism: swap the pointers

Chapters 01-03 built a fake computer: a guest CPU mode (VT-x), a second page
table (EPT), virtual devices (virtio rings). A container skips all three.
Mechanically, `docker run` is `clone()` with a handful of `CLONE_NEW*`
flags, a `pivot_root()`, and a cgroup assignment.

Module 01 chapter 04 derived that a process is a data structure the kernel
walks. That struct also holds an `nsproxy` — pointers to namespace objects
(the user namespace lives in `cred` instead — same idea, different struct). The kernel's virtualization-on-the-cheap pattern is one move,
repeated 8 times: **globals become per-task tables.** The pid counter, the
mount table, the network stack, the hostname, the uid checks — each was one
global structure; each is now a table with a refcount, and each process's
pointer aims either at the *initial* (host) instance or at a private copy.
A container is the same data structure with different pointers:

```text
        process A (your shell)                process B ("the container")
+------------------------------+       +------------------------------+
| struct task_struct           |       | struct task_struct           |
|   nsproxy ----+              |       |   nsproxy ----+              |
+---------------|--------------+       +---------------|--------------+
                |                                      |
+---------------v---------------------+  +--------------v-----------------------+
| pid_ns  = global pid counter        |  | pid_ns  = fresh counter (init = 1)     |
| mnt_ns  = global mount table        |  | mnt_ns  = private copy of the tree    |
| net_ns  = global network stack      |  | net_ns  = fresh stack + one veth      |
| user_ns = initial uid map (cred)    |  | user_ns= "0 -> 100000" map (cred)     |
+-------------------------------------+  +---------------------------------------+
  one kernel, one scheduler, one syscall table -- only the POINTERS differ
```

Two syscalls are the doors between worlds: `unshare(flags)` creates fresh
namespaces and moves the caller in; `setns(fd, flags)` joins an existing one
via `/proc/<pid>/ns/`. Docker, Podman and runc are orchestration on
top. Stated precisely: **a container is a process whose kernel-resource
views are swapped** — not a machine.

## The eight views, one per global table

Each namespace virtualizes exactly one global (merge kernels marked — a
20-year accretion, not a design):

- **mount** (`CLONE_NEWNS`, Linux 2.4.19) — a copy of the mount table. A
  container's "rootfs" is just this: the runtime mounts the image at a
  scratch directory, `pivot_root()`s into it, unmounts the old root; the
  same table makes `/proc` and `/sys` per-container.
- **pid** (`CLONE_NEWPID`, 2.6.24) — a fresh pid counter: the
  container's init is PID 1 in its namespace and 30,142 to the host; the
  `NSpid:` line in `/proc/<pid>/status` shows the chain, one number per
  nesting level, outermost first. PID 1 is where containers get weird: the
  kernel skips *default* fatal signal dispositions for PID 1 (an unhandled
  SIGTERM is ignored, not fatal), and PID 1 must `wait()` for reparented
  orphans or zombies accumulate. When PID 1 of a namespace exits, the
  kernel SIGKILLs every other process in it — the "your container dies
  differently when the app crashes" folklore.
- **net** (`CLONE_NEWNET`, 2.6.29) — a private network stack: interfaces,
  routes, iptables rules, sockets, `/proc/net`. The wire out is a
  **veth pair** — a virtual crossover cable: one end moved into the
  container (renamed `eth0`), the other plugged into a host bridge, NAT to
  the physical NIC. `lo` in a new netns starts *DOWN*.
- **uts** (`CLONE_NEWUTS`, 2.6.19) — hostname and domainname, nothing else.
- **ipc** (`CLONE_NEWIPC`, 2.6.19) — SysV shared memory/semaphores and POSIX
  message queues (`/dev/mqueue`); shm sysctls are per-namespace.
- **user** (`CLONE_NEWUSER`, 3.8) — the uid/gid *map*: who "root" is — the
  namespace that makes fake root safe, drawn:
- **cgroup** (`CLONE_NEWCGROUP`, 4.6) — relativizes `/proc/self/cgroup`
  paths so the container sees `/`, not the host's bookkeeping path.
- **time** (`CLONE_NEWTIME`, 5.6) — the newest: fixed offsets for
  `CLOCK_MONOTONIC`/`CLOCK_BOOTTIME` only (CRIU's restore of pending
  sleeps); `CLOCK_REALTIME` is not virtualized.

```text
            /proc/self/uid_map:   "0   100000   65536"

     inside the user namespace               outside (the host)
     uid 0 ........... uid 65535    maps to    uid 100000 ........ uid 165535
     "root": full capability set               an unprivileged nobody --
     *within this namespace*                   caps here are worth nothing
```

Uid 0 inside a user namespace holds all capabilities *in that namespace* —
it can mount, set the hostname — while anything crossing the boundary is
checked against the *outside* identity, uid 100000. Rootless
Podman runs on this trick with a one-line map (`0 1000 1`).

## Namespaces hide; cgroups charge

Namespaces are the *perception* half; cgroups are the *accounting* half —
what it may consume. Cgroup v1 (2007) let each controller live on its own
hierarchy — a task could be in `memory/` on one tree and `cpu/` on another,
so ownership became unattributable. Cgroup v2 (merged 4.5, 2016) mandates
**one unified hierarchy** with every controller on it: one path, one owner —
why systemd 240+ (2019), Docker 20.10 (2021) and Kubernetes 1.25 (2022) all
completed the flip.

The memory controller is the one that kills. `memory.current` grows as pages
are charged to the cgroup; two thresholds sit above it:

```text
   a page allocation (mmap growth / brk / fault-in)
        |
        v
   charge -> memory.current grows ............... toward the limits
        |
   memory.high reached?  ---yes---> hard reclaim: the ALLOCATING task is made
        |                              to evict pages itself. The app sees
        |                              LATENCY, not death (memory.events
        |                              counts "high"; memory.pressure = PSI)
   memory.max  reached?  ---yes---> OOM inside the cgroup: kernel picks the
        |                              highest-badness task *inside this
        |                              cgroup only* and SIGKILLs it (module
        |                              07 ch05's arithmetic, scoped).
        v                              memory.events counts "oom_kill".
                                      The dead app's exit code: 137
```

`memory.high` is the humane limit — the kernel reclaims aggressively (the
app slows, refaults in `memory.stat`) but nothing dies. `memory.max` is the
wall — the OOM killer's candidates restricted to the cgroup.
`memory.swap.max` caps anonymous pages in swap; with `swap.max 0` the
cgroup hits `memory.max` that much sooner. **Exit code 137 = 128 + 9
(SIGKILL)** — THE forensic number: the shell reports 137; the proof is
`memory.events`' `oom_kill` counter, not a traceback (the process died
mid-allocation, never reaching `MemoryError`).

The CPU controller is the one that stalls. `cpu.max` takes
`"quota period"` in microseconds, implementing CFS bandwidth control: the
cgroup's tasks may burn `quota` of CPU per `period`, and the **default
period is 100 ms** (100,000 µs — systemd's `CPUQuota=40%` writes
`cpu.max = 40000 100000`). Exceed it and the kernel *throttles*: every
runnable task in the cgroup is dequeued until the period boundary refills
the quota. The incident-class arithmetic: a single-threaded app with
a 0.9 CPU quota runs 90 ms and freezes 10 ms every period — the literal
"10 ms stall per 100 ms." Threads amplify it: 4 threads with a 40 ms quota
burn it in 10 ms of wall time, then the whole cgroup sleeps 90 of every
100 ms — and since the quota is shared, one spinning thread freezes its
siblings. The record is in `cpu.stat`: `usage_usec` (CPU actually used),
`nr_periods` (boundaries elapsed, ~10 per second), `nr_throttled` (periods
in which the quota ran out) and `throttled_usec` (total time FROZEN —
accumulates, never resets).

`nr_throttled` climbing at `nr_periods`' rate means the cgroup is fully
walled — the insider's first check when the complaint is "the container is
slow but the host is idle." For latency-sensitive work `cpu.weight`
(1-10000, default 100) is the softer tool: it shares rather than walls —
you get your proportion, never a freeze.

## The rootfs is an overlay: the copy-up bill

A container image is layers of tarballs plus a manifest: each layer is a
*diff* — files added or replaced, whiteouts for deletions — and the
manifest lists the order (`docker history` prints it). Layers are
read-only and shared across every container of that image; only the top is
private.

At run time the runtime mounts an **overlayfs** with the image layers
stacked as `lowerdir`s (leftmost = topmost, same convention as `$PATH`) and
a per-container `upperdir` (plus a `workdir` for atomic renames). That is
module 14 chapter 05's machinery reused verbatim — with its cost structure
attached: **copy-up**. The first write to a lower-layer file copies
the *entire file* into the upperdir first. Editing a 100 MB file that
shipped in the image copies 100 MB before your edit starts — "containers
are free until you edit big files," module 01 chapter 03's copy-on-write at
file granularity. Deleting a lower file never touches the image: overlayfs
writes a **whiteout** — a character device node, major/minor 0:0 — into the
upperdir to hide the lower entry (why `rm` in a Dockerfile layer never
shrinks an image).

```text
   what the process sees              the real directories on the host
  +---------------------+   merged  +-- lowerdir: .../overlay/L2 (top)
  |  /etc  /usr  /home  | <-------- +-- lowerdir: .../overlay/L1 (base)
  +---------------------+   view    +-- upperdir: .../diff  <- YOUR writes
  first write to /etc/foo                +-- workdir:  overlayfs scratch
     -> whole file copied lower -> upper (100 MB file = 100 MB copy)
```

## One kernel, four walls, and the honest table

The security truth, unsoftened: **the kernel is shared.** A VM escape needs
a hypervisor bug; a container escape needs one bug in the kernel *every
container and the host already trust* — every CVE from modules 02 and 03 is
an escape path. Four defenses layer on that one wall:

- **seccomp-bpf** — a syscall allowlist enforced by a BPF filter in the
  kernel, installed before the app runs (`Seccomp: 2` means filter mode).
  The default container profile allows roughly 300 of the ~350 syscalls on
  x86-64 Linux 6.x (the count grows each release — call it ~300 of
  ~400). The blocked tail is the dangerous tail: module loading, `kexec`,
  raw `bpf(2)`, `ptrace`... Denied calls return `EPERM` — except a few
  returning `ENOSYS` so libraries fall back: `clone3` is the famous case
  (glibc's `pthread_create` retries with old `clone`). Blind blocking
  breaks here: the program sees "not implemented," not "not permitted."
- **capabilities** — root's powers split into ~40 `CAP_*` bits (41 as of
  kernel 6.5; cap 40 is `CAP_CHECKPOINT_RESTORE`). Containers drop the
  *bounding set*: Docker's default keeps 14 of the 41 — decode
  `CapEff: 00000000a80425fb` with `capsh --decode` and out come exactly
  chown, dac_override, fowner, fsetid, kill, setgid, setuid, setpcap,
  net_bind_service, net_raw, sys_chroot, mknod, audit_write, setfcap. No
  `CAP_SYS_ADMIN` means no mounting and no module loading — the precise
  sense of "container root ≠ host root."
- **AppArmor/SELinux** — module 05's mandatory access control, returned: the
  container runs under a profile (`docker-default` on Ubuntu/Debian,
  `container_t` on RHEL) that confines it to its own files and sockets even
  past every other check.
- **user namespaces** — the uid map: even a full compromise of "root"
  inside maps to nobody outside.

Both named escapes attack the same insight — the wall is only as good as
the code behind the doors. **runc CVE-2019-5736 (February 2019):** a
malicious container opened `/proc/self/exe` while the runtime was exec'ing
into it — the link pointed at the *host's runc binary* — and overwrote it;
the next `docker exec` ran the attacker's bytes as root. The runtime itself
was the attack surface. **Dirty Pipe (CVE-2022-0847,
kernels 5.8-5.16.10):** module 03 ch02's anatomy in one line — `splice()`
from a file into a pipe left the stale `PIPE_BUF_FLAG_CAN_MERGE` flag set,
so a following `write()` merged attacker bytes into the page cache of a
read-only file. Overwrite `/etc/passwd` for root, or a host binary for
escape — using only `pipe`, `splice`, `write`, all allowed by every
default seccomp profile.

| | process | container | VM |
|---|---|---|---|
| what starts | `fork()` + `execve()` | `clone()` + `pivot_root()` + cgroup attach | VMLAUNCH into a guest OS |
| start time | ~0.05-1 ms | ~5-50 ms (`unshare`) to 0.2-1 s (`docker run`) | seconds to a minute (full guest); 125 ms microVM (ch05) |
| memory overhead | 0 | ~0 (layers shared as page cache) | ~100-200 MB guest-OS floor (tens of MB if trimmed) |
| isolation interface | none | ~350-400 syscalls | ~50,000 instruction encodings + ~60 exit reasons |
| host code behind it | the kernel, same as you | the whole ~30 MLOC kernel (6.x) — *shared with the host* | only the exit handlers (QEMU ~2 MLOC, Firecracker ~50 kLOC — ch05) |
| typical density | thousands/host | hundreds/host | tens/host (thousands of microVMs) |
| escape cost | no wall | one kernel 0-day | a chain: guest kernel bug + hypervisor bug |

The "attack surface is an API surface" argument needs one refinement: raw
counting misleads. A VM's ~50,000 instruction encodings dwarf a container's
~400 syscalls — but instructions execute in silicon and never touch host
software; the host code a guest reaches is the handful of exit reasons
handled by the VMM. A container's syscalls *all* land in ~30 million lines
of kernel C the host also depends on. Count the doors that reach host code,
and the smaller surface is the stronger wall — the VM's.

## What insiders know

- Read exit codes as `128 + signal`: **137** = SIGKILL (almost always the
  cgroup OOM), **143** = SIGTERM (graceful stop). Confirm 137 with
  `memory.events`' `oom_kill` — `dmesg` is usually unavailable inside a
  container (`kernel.dmesg_restrict=1`).
- The throttling signature is *period-quantized*: stalls land on the 100 ms
  grid, so latency histograms grow teeth at exact 100 ms multiples.
- `/proc`'s filtering is fixed at mount time — `ps` inside `unshare -p`
  still lists host processes until `unshare --mount-proc` remounts it.
- A whiteout is a character device `0,0` in the upperdir — `ls -l` on the
  overlay's `diff/` shows `c`-typed nodes: deletions stored as files.
- In a fresh netns, `lo` is DOWN; every runtime's first network act is
  `ip link set lo up` — "network unreachable" after `unshare -n` is this.

## See it yourself

Everything below runs with coreutils alone; Docker/Podman is optional and
marked.

**1. Build a container with no container software.**

```console
$ unshare -Urmpf bash
#  -U new user ns   -r map your uid to 0 inside   -m new mount ns
#  -p new pid ns    -f fork: the new shell becomes pid 1 of it
bash# id                            # proof 1: uid=0(root) -- fake root
bash# echo $$                       # proof 2: 1 -- fresh pid counter
bash# cat /proc/self/uid_map        # proof 3: 0  1000  1 -- the map (your uid)
bash# grep NSpid /proc/self/status  # proof 4: NSpid: 1 -- seen from HERE
```

Four lines, four clone flags — the entire container trick. (If `-U` fails
with `Operation not permitted`, your distro gates user namespaces — Ubuntu
24.04's AppArmor policy, Debian's `kernel.unprivileged_userns_clone=0` —
retry as root without `-Ur`.) From a second terminal,
`grep NSpid /proc/<pid>/status` prints `NSpid: 30142 1`, and
`readlink /proc/self/ns/mnt` differs between the two shells — different
nsfs inode = different namespace. We never called `pivot_root`, so `/` is
still the host's; a real runtime would mount the image's overlay and pivot
into it.

**2. Read your cgroup.**

```console
$ cat /proc/self/cgroup
0::/user.slice/user-1000.slice/session-3.scope
```

The single `0::` line is v2's unified hierarchy (v1 printed one line per
controller, `11:blkio:/...`); inside Docker it reads
`0::/system.slice/docker-<id>.scope`, or `0::/` under a cgroup namespace.

**3. Get killed by the memory controller.**

```console
$ sudo systemd-run --scope -p MemoryMax=100M python3 -c 'a="x"*10**9'; echo "exit=$?"
exit=137
$ sudo cat /sys/fs/cgroup/system.slice/run-*.scope/memory.events | tail -n 2
oom 5
oom_kill 1
```

`MemoryMax=100M` writes `memory.max = 104857600`; the 1 GB string crosses
it; the cgroup OOM killer SIGKILLs Python mid-allocation; the shell reports
128+9; `oom_kill: 1` proves it was the kernel. (If `systemd-run --user`
refuses the property, your systemd doesn't delegate memory — use `sudo`.)

**4. The doors, checked.**

```console
$ grep -E 'NSpid|Seccomp|Cap' /proc/self/status
NSpid: 30142
Seccomp: 0            (or 2: systemd filters some user sessions)
CapEff: 000001ffffffffff   (full root set; decode with capsh --decode)
```

**5. Pay the copy-up bill by hand.**

```console
$ sudo mkdir -p /tmp/ov/{lower,upper,work,merged}
$ sudo dd if=/dev/urandom of=/tmp/ov/lower/big.bin bs=1M count=100
$ sudo mount -t overlay \
    -o lowerdir=/tmp/ov/lower,upperdir=/tmp/ov/upper,workdir=/tmp/ov/work \
    /tmp/ov/merged
$ sudo sh -c 'echo x >> /tmp/ov/merged/big.bin'    # first write
$ sudo du -sh /tmp/ov/upper                        # ~100 MB: whole file copied up
$ sudo cat /tmp/ov/lower/big.bin | wc -c           # unchanged: 104857600
$ sudo rm /tmp/ov/merged/big.bin
$ sudo ls -l /tmp/ov/upper/                        # c--------- 0,0 node: the whiteout
```

(Inside the step-1 shell, no `sudo` needed on kernel 5.13+, which allows
user-ns overlay mounts.)

**6. Optional, marked — real runtimes:** `docker run --rm alpine sh -c
'grep -E "NSpid|Seccomp|CapEff" /proc/self/status; cat /proc/self/uid_map'`
shows `Seccomp: 2`, `CapEff: 00000000a80425fb` (the 14 default caps), and
no uid_map (Docker's default uses no user namespace); rootless Podman's
`uid_map` shows a real one.

## How to use this knowledge

- **The "slow but idle" triage.** Before profiling anything, read the
  cgroup's `cpu.stat` (`nr_throttled`, `throttled_usec`) and
  `memory.pressure`/`memory.stat` (high-throttle refaults). Thirty seconds
  of file reads replaces an hour of flame graphs — frozen time is invisible
  to in-container profilers: it is spent *off-CPU*.
- **Exit-code forensics.** A pod that dies with 137 and no stack trace was
  killed by the cgroup OOM killer mid-allocation — check `memory.events`
  before blaming the code, and *which* limit (high vs max vs
  swap.max) before raising anything.
- **Quota vs weight.** For latency-sensitive services prefer `cpu.weight`
  (proportional share, never freezes) over `cpu.max` (period-quantized
  stalls); quotas suit batch work where neighbors matter more than your p99.
- **Image and volume economics.** Big mutable data belongs in volumes, not
  image layers: every first edit pays a full copy-up, and deletions leave
  whiteouts that keep the image fat — the multi-stage-build argument,
  stated mechanically.
- **The trust decision.** Untrusted code on a shared kernel is one 0-day
  from the host — why multi-tenant platforms pick VMs, microVMs (ch05) or
  gVisor; the API-surface table is the interview-grade "container vs VM"
  answer.

## Connections

- **Module 01 ch04** — the process as a data structure: the
  `nsproxy`/`cred` pointers *are* the container.
- **Module 01 ch03** — copy-up is COW at file granularity; the uid map is a
  translation table for privilege.
- **Module 07 ch05** — the OOM killer's arithmetic, scoped to the memcg;
  exit 137 is its signature.
- **Module 14 ch05** — overlayfs internals: this chapter is that machinery's
  biggest customer.
- **Module 05** — AppArmor/SELinux: the container profile is MAC scoped per
  container, one layer up.
- **Modules 02-03** — every kernel memory bug is a container escape path;
  Dirty Pipe is module 03 ch02-shaped (an unreset flag on a shared
  structure).
- **Module 15 ch01-03** — what containers skip: no VMCS round trips
  (~1,000-2,000 cycles), no EPT walk, no virtio rings — why they start in
  milliseconds.
- **Module 15 ch05** — the answers to the one-kernel problem: gVisor's
  userspace kernel, Firecracker's 125 ms VM wall, the who-owns-the-bytes
  table.

## Quiz

1. A teammate says: "So a container is just a lightweight VM." Give the
   two-sentence correction, and name the one component both share that makes
   a container's blast radius *larger* than a VM's.

2. A container runs `python app.py` as PID 1. Kubernetes sends SIGTERM;
   the process ignores it; 30 s later it is SIGKILLed
   mid-write. Why did SIGTERM do nothing — and why does the same signal stop
   `nginx` cleanly?

3. On the host, a container's init has PID 30,142. Predict what
   `grep NSpid /proc/30142/status` prints from a host shell and what
   `grep NSpid /proc/self/status` prints *inside* the container — and why
   they differ.

4. You run `sudo systemd-run --scope -p MemoryMax=100M python3 -c
   'a="x"*10**9'` followed by `echo $?`. What prints, what produced it, and
   which file proves the cause?

5. A cgroup has `cpu.max = 40000 100000` and runs a 4-thread busy loop on
   an idle 4-core host. Describe the cgroup's experience during one 100 ms
   period, and the two `cpu.stat` counters that record it.

6. "The container is slow but the host is idle; `top` inside shows only 40%
   CPU." Give the first file you read and the numbers you expect if the
   diagnosis is CFS throttling.

7. A job edits an 8 GB file that shipped in the image. On a VM it took 90 s;
   on Kubernetes the first run takes 4 minutes and the node's disk
   under `/var/lib/containers` grows by 8 GB. What happened, and what is the
   fix?

8. Dirty Pipe (CVE-2022-0847) escapes containers using `pipe`, `splice`, and
   `write` — all in Docker's default seccomp allowlist. Why is that not a
   seccomp failure? Which module 03 ch02-style property explains
   it, and what defense actually stops it?

<details><summary>Answers</summary>

1. **A swapped-view process, sharing the kernel.** A container is a normal
   process whose namespace views are swapped — no guest CPU mode, no second
   page table, no device model — talking to the host kernel through the
   same ~350 syscalls as the host's own processes. The shared
   component is the *kernel*: one kernel 0-day is simultaneously an escape
   from every container and a compromise of the host; a VM escape must
   cross into the far smaller VMM.

2. **PID 1 has no default signal dispositions.** The kernel skips default
   fatal dispositions for PID 1: a terminating signal with no handler is
   ignored, not fatal. Python installs no SIGTERM handler, so
   Kubernetes waits out `terminationGracePeriodSeconds` (default 30 s) then
   delivers SIGKILL mid-write; the nginx master installs a handler that
   drains connections. Fix: `docker --init` / tini as PID 1, or a handler
   in the app.

3. **Reader-relative listing.** Host shell: `NSpid: 30142 1` — one number
   per nesting level, outermost first. Inside: `NSpid: 1`. NSpid is relative
   to the reader: an ancestor-level reader sees the full chain, a reader
   inside sees only its own level down.

4. **`exit=137`.** `MemoryMax=100M` sets `memory.max`; the 1 GB string's
   page charges cross it; the cgroup-scoped OOM killer SIGKILLs Python
   mid-allocation — the shell reports 128+9 = 137. Python never raised
   `MemoryError` — it died before the allocator could fail. Proof:
   `memory.events` shows `oom_kill 1`.

5. **10 ms of running, 90 ms frozen.** The 4 threads burn the 40 ms quota
   in 10 ms of wall time; the kernel dequeues every runnable task for the
   remaining 90 ms. `nr_throttled` increments once per
   period (~10/s); `throttled_usec` accumulates ~90,000 µs per period and
   never resets.

6. **`cpu.stat` in the cgroup directory** (for a pod,
   `/sys/fs/cgroup/.../<pod>/cpu.stat`). Expect `nr_throttled` near
   `nr_periods` — e.g. 30 periods, 27 throttled after 3 s — and
   `throttled_usec` growing at roughly (period − quota) per period (~60 ms
   per 100 ms for a 0.4-CPU quota, single-threaded). The CFS wall, not the
   app, is the bottleneck; fix: higher quota or `cpu.weight`.

7. **Overlayfs copy-up.** The file lives in a read-only image layer; the
   first write copies the whole 8 GB file into the container's upperdir
   before the edit starts — 8 GB of I/O, 8 GB of disk. Module 01 ch03's
   copy-on-write at file granularity, via module 14 ch05's machinery. Fix:
   big mutable data in a volume or bind mount.

8. **Seccomp gates doors, not what runs behind them.** The filter decides
   which syscalls may be issued; `pipe`/`splice`/`write` are allowed — the
   bug is in the kernel's implementation: the splice path left the
   stale `PIPE_BUF_FLAG_CAN_MERGE` flag on the pipe buffer (kernel 5.8+),
   so the following write merged attacker data into the page cache of a
   read-only file. Module 03 ch02's shape: one unreset flag on a shared
   structure, reachable through a legal API. Only the kernel patch stops it;
   the structural defense is not sharing the kernel — the VM wall or gVisor
   (ch05).

</details>
