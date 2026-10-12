# Chapter 01 — Three process models

> **The 30-second version.** "Process" is a design decision, and the three
> desktop kernels made three different ones. Windows builds processes from
> object-manager objects — an EPROCESS owning ETHREADs, files, tokens,
> sections — all refcounted, reachable only via per-process handle
> tables. Linux makes the *task* (`task_struct`) the atom: every thread is a
> full task, and a "process" is merely a group of tasks sharing address
> space, fd table, and signal state via `clone()` flags — a thread is a
> process that shares. macOS splits the role: a Mach task (address space,
> port table, threads) wearing a BSD proc (pid, fds, signals). From them
> fall handles vs fds with opposite inheritance defaults, PID reuse, job
> objects vs cgroups — and the numbers that predict memory death: commit
> charge, RSS+swap, phys_footprint.

## 1. One abstraction, three kernels

Any kernel promising each program a private machine must invent an address
space (module 01 ch03's page tables made policy), execution units, a table
of owned resources, an identity, and a rationing envelope. The three kernels
disagree only about the *unit of construction*.

**Windows: everything is an object.** [Windows 10/11] NT's object manager
gives every kernel resource — process, thread, file, section, token —
one shape: header plus body. The header (`OBJECT_HEADER`) carries a reference
count, a type pointer, an optional name, and a security descriptor with an
ACL; the body is the EPROCESS, the ETHREAD, the FILE_OBJECT. A process is
just another object that owns a *handle table* (section 2) — user space
never sees pointers, only handles.

**Linux: the task is the atom; threads are not a separate kind.** [Linux,
2.6 through 6.x] One `task_struct` (a few KB, plus a 16 KB kernel stack on
x86-64) per thread — nothing else exists. A "process" is a
*thread group*: CLONE_THREAD tasks share the thread-group id (`tgid`);
`getpid()` reports the tgid, `gettid()` the per-task pid. The inversion: on
Windows a
thread is an object inside a process object; on Linux a thread *is* a
process that shares — NPTL's `pthread_create` passes `CLONE_VM |
CLONE_FILES | CLONE_SIGHAND | CLONE_THREAD | CLONE_SYSVSEM | CLONE_SETTLS`,
and `fork()` is the degenerate clone that shares nothing.

**macOS: one entity, two structs.** [XNU; module 06 ch01] The Mach task is
a resource container — vm_map, port table, threads; the BSD `proc` layers
the POSIX facts on top — pid, fds, signal state. `task->bsd_info` points at
the proc; `proc->p_task` points back.

Three shapes, side by side:

```text
      WINDOWS (NT)               LINUX                       macOS (XNU)
+----------------------+  +----------------------+  +--------------------------+
| THE UNIT             |  | THE UNIT             |  | THE UNITS (two structs)  |
| EPROCESS object =    |  | task_struct,         |  | Mach task = vm_map +     |
| process; ETHREAD     |  | ONE PER THREAD.      |  | port table + threads;    |
| object = thread.     |  | "Process" = the      |  | BSD proc = pid,          |
| Everything else is   |  | thread GROUP (tgid). |  | filedesc, signal state.  |
| an object too: file, |  | Sharing is clone()   |  | task->bsd_info <->       |
| token, section,      |  | flags: CLONE_VM |    |  | proc->p_task. One        |
| event.               |  | CLONE_FILES | ...    |  | entity, two views.       |
+----------------------+  +----------------------+  +--------------------------+
| THE REFERENCE        |  | THE REFERENCE        |  | THE REFERENCES           |
| HANDLE = slot<<2 +   |  | fd = small index     |  | fd (BSD, like Linux)     |
| flag bits -> entry   |  | into files_struct    |  | + Mach port rights       |
| in PER-PROCESS       |  | fdtable -> struct    |  | (ports are NOT           |
| handle table ->      |  | file = open file     |  | inherited across fork,   |
| object-manager       |  | description          |  | module 06 ch03)          |
| object: refcount +   |  | (refcount + offset   |  |                          |
| ACL + access mask    |  | + mode)              |  |                          |
+----------------------+  +----------------------+  +--------------------------+
| THE ENVELOPE         |  | THE ENVELOPE         |  | THE ENVELOPE             |
| job object (memory   |  | cgroups RATION the   |  | Jetsam tier + QoS class  |
| limit, CPU rate,     |  | world, namespaces    |  | (kill by policy, module  |
| kill-on-close). PID  |  | FAKE it. PID         |  | 06 ch02); footprint      |
| = CID-table slot ->  |  | sequential, wraps    |  | highwater mark per task. |
| multiple of 4, can   |  | at pid_max.          |  | PID sequential, wraps    |
| be reused at once.   |  |                      |  | at 99999 (kern.pid_max). |
+----------------------+  +----------------------+  +--------------------------+
```

**PID reuse is where the designs leak.** [Windows] PIDs are slots in the CID
table (`PspCidTable`) — itself a handle table — so PIDs are multiples of 4
(Idle holds 0, System 4), and a freed slot can go to the next `CreateProcess`
*immediately*. [Linux] PIDs are sequential, wrapping at
`/proc/sys/kernel/pid_max` (32768 upstream, 4194304 under systemd on
64-bit), so a number repeats only after the whole space wraps. [macOS] PIDs
climb to 99999 (`kern.pid_max`) then wrap. A captured PID goes stale in
minutes on Windows, hours on the others.

## 2. Handles vs file descriptors

Both answer one question: how does user code refer to a kernel thing
without holding a kernel pointer? They differ in what the integer indexes
and what rights travel with it.

An **fd is a pointer to an open-file description.** [POSIX] The integer
indexes `files_struct`'s fd table, an array of pointers to `struct file` —
one *open-file description* per `open()` call: a refcount, the shared
offset (`f_pos`), the status flags (`O_APPEND` lives here), the access mode. `fork()` copies the pointer array and bumps
each refcount, so parent and child share one description — one offset —
which is why `O_APPEND` exists: the offset belongs to the description, not
the file. Rights are thin: permissions were checked once, at `open()`.

A **handle is a slot in a process-local table pointing at a kernel object.**
[Windows] The handle *value* is the slot index shifted left two bits plus
flag bits — why handles and PIDs are multiples of 4. The entry holds the
object pointer and a 32-bit **granted-access mask** frozen at open time and
re-checked on every call: a handle is a capability narrowed to what you
requested, and `DuplicateHandle` can narrow it further when handing it to
another process. POSIX has no equivalent (`SCM_RIGHTS`, over a Unix socket,
is the closest).

**The difference that bites is the inheritance default — the two OSes chose
opposite poles.** [POSIX] `fork()` inherits *all* fds; opting out is per-fd
(`O_CLOEXEC`, applied at `execve`) — the bug class is *leakage by default*:
the exec'd child keeps your listening socket. [Windows] `CreateProcess` inherits almost *nothing* (`bInheritHandles`
defaults to FALSE), and even with TRUE only handles marked inheritable —
`SECURITY_ATTRIBUTES.bInheritHandle` at creation, or `SetHandleInformation`
later — reach the child. The bug class is *lockdown by default*: "the
child can't use the file I opened." [macOS] The BSD side is POSIX; Mach
port rights never cross `fork()` — they move as messages (module 06 ch03).

## 3. Rations and fakes: job objects, namespaces, cgroups

A **job object** [Windows] is one kernel object grouping a process and its
descendants (absent `CREATE_BREAKAWAY_FROM_JOB`) under group limits:
job-wide and per-process memory caps, CPU rate control, and
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` — close the last handle and the
kernel kills the whole tree. Jobs nest since Windows 8: the cgroup analog,
earlier and coarser.

The **Linux envelope** splits the same job in two — the container story in
one paragraph. *Namespaces fake the world*: a pid namespace renumbers
tasks so the container's init looks like pid 1; mount + overlayfs give it a
private-looking root; a network namespace a private stack. *Cgroups v2
ration it*: `memory.max`, `cpu.max`, `pids.max`. A container runtime is
unshare (fake), cgroup writes (ration), exec. [macOS] XNU's envelope is
per-task in-kernel: QoS classes and footprint highwater marks, enforced by
policy killing, not throttling (module 06 ch02).

## 4. Memory accounting: which number predicts the kill

Each OS lies about memory differently, because each promises it
differently.

**Windows charges the promise.** [Windows] `VirtualAlloc(MEM_RESERVE)`
carves address space free; `MEM_COMMIT` charges the commit limit
*immediately* — reserved-but-charged — untouched pages or not. The limit is
RAM plus all pagefiles; the next commit past it fails with
`ERROR_COMMITMENT_LIMIT`, at *promise time*, not first touch.

**Linux promises almost nothing and bills at touch.** [Linux] Default
overcommit: a mapping costs nothing until a page faults in — VMA `Size` is
opinion, `Committed_AS` advisory. Out of memory, the OOM killer ranks
victims by badness ≈ RSS + swap (+ page-table bytes on recent kernels),
nudged ±1000 by `oom_score_adj`. RSS double-counts shared; **PSS** (private
+ shared/N) is the fair number — but no killer reads PSS; it exists for
capacity planning.

**macOS compresses first, then kills by footprint.** [XNU]
**phys_footprint** is the Jetsam number (module 06 ch02): dirty pages plus
the compressed copies of evacuated pages, plus IOKit and
purgeable-nonvolatile bytes. RSS is *not* the criterion — compressed pages
have left RSS but remain in footprint.

| accounting question | Windows | Linux | macOS |
|---|---|---|---|
| reserved address space (free?) | MEM_RESERVE, free | VMA `Size`, free | vmmap VSIZE, free |
| promise accounting | **commit charge**, at MEM_COMMIT | `Committed_AS` (advisory only) | none |
| resident, double-counts shared | Working Set | RSS | RSDNT |
| fair share of shared | "Shareable" WS (no PSS analog) | **PSS** | not exposed |
| true private cost | Private Bytes | `Private_Dirty` | DIRTY + compressed |
| **predicts the kill** | **commit charge vs commit limit** (RAM+pagefiles) | global: **RSS+swap**; cgroup: `memory.current` vs `memory.max` | **phys_footprint** vs highwater tiers |

## 5. The CPU side, briefly

The same envelope has a CPU side — memory-adjacent because what rations
pages also rations time slices.

**Windows: 32 priorities, boost-and-decay.** [Windows 10/11] 1-15 variable
class, 16-31 real-time (never boosted — a spinner there starves the mouse),
0 held by the zero-page thread. A quantum is 2 clock ticks on
client SKUs (Server gets 12 — Windows Internals, 7th ed.): boosts give the
foreground process's GUI threads +2 and a stretched quantum (the classic 3x
`Win32PrioritySeparation`), disk I/O completion +1, decaying one step per
quantum end.

**Linux: fair queuing, by virtual time, now by deadline.** CFS (2.6.23,
2007) ran the task with the least `vruntime`, weighted by nice — weight 1024
at nice 0, ~×1.25 per level, nice 19 (weight 15) earning ~1.5% of a nice-0
peer — the leftmost red-black tree node. EEVDF (6.6, late 2023) adds
virtual deadlines from each task's lag, so latency-sensitive short-slice
tasks run before their raw fair share.

**macOS: QoS classes as the single dial.** [XNU] `QOS_CLASS_USER_INTERACTIVE`,
`USER_INITIATED`, `DEFAULT`, `UTILITY`, `BACKGROUND` map onto Mach scheduler
bands — and onto Jetsam ordering: background-QoS work loses its pages to
the compressor first (module 06 ch02). One knob, both resources.

## What insiders know

- **PID reuse semantics differ from "immediately" to "after wrapping the
  whole space":** Windows hands a freed CID-table slot to the next
  CreateProcess; Linux wraps at pid_max (32768 vanilla, 4194304 under
  systemd on 64-bit) before repeating a number; macOS climbs to 99999 then
  wraps. Linux 5.3+ has the race-free fix: `pidfd_open()` +
  `pidfd_send_signal()` — the pidfd pins the struct pid, so a reused number
  can't redirect the kill.
- **Inheritance defaults are inverted, and so are the bug reports:** POSIX
  leaks by default (every fd crosses fork; `O_CLOEXEC` opts out at exec —
  hence leaked listen sockets and hung pipe readers); Windows locks down by
  default (the per-handle inherit bit *and* `bInheritHandles` must both
  agree). Wrinkle: fd numbers survive fork unchanged; `DuplicateHandle`
  yields a different value.
- **Which accounting number predicts OOM, per OS:** Windows — commit charge
  vs commit limit; death is at promise time. Linux — the global killer ranks
  on RSS+swap (never PSS); a cgroup OOM is `memory.current` hitting
  `memory.max`. macOS — phys_footprint, i.e. dirty+compressed; RSS misses
  everything the compressor took.
- **The kill-a-tree trick on Windows:** create a job object with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, assign your process, spawn children,
  close the last handle — the tree dies: the honest analog of killing a
  cgroup.
- **A Windows handle-count leak is an object-refcount leak:** Task
  Manager's "Handles" column counts *every* object-manager type (events,
  keys, sections), while `ls /proc/PID/fd | wc -l` counts only open-file
  descriptions — not comparable across platforms.

## See it yourself

[linux] Runs on any Linux box — fd as index:

```console
$ ls -l /proc/self/fd
total 0
lrwx------ 1 me me 64 Sep  1 10:11 0 -> /dev/pts/2
lrwx------ 1 me me 64 Sep  1 10:11 3 -> 'pipe:[1903421]'
lrwx------ 1 me me 64 Sep  1 10:11 4 -> /proc/self/fd
```

fd 4 is the directory `ls` itself just opened — the table is visible to the
process it describes. Threads are tasks:

```console
$ python3 -c 'import threading,time
exec("[threading.Thread(target=time.sleep, args=(60,)).start() for _ in range(3)]"); time.sleep(60)' &
[1] 8821
$ ps -L -p 8821
  PID   LWP TTY      STAT  TIME COMMAND
 8821  8821 pts/2    Sl+   0:00 python3 -c ...
 8821  8822 pts/2    Sl+   0:00 python3 -c ...
 8821  8823 pts/2    Sl+   0:00 python3 -c ...
 8821  8824 pts/2    Sl+   0:00 python3 -c ...
```

One PID (the tgid), four LWP values — four `task_struct`s; `ls
/proc/8821/task/` prints the same numbers: `ps` showed thread groups, `ps
-L` the kernel's actual unit. The handle-count analog:

```console
$ ls /proc/$$/fd | wc -l
5
$ grep FDSize /proc/$$/status
FDSize: 64
```

`FDSize` is the fd table's capacity (a power of two).

[windows] Documented from Process Explorer (Sysinternals, Windows 10/11):
View → Select Columns → Process tab → *Handle Count*, then View → Lower Pane
View → *Handles*, and click a process:

```text
Type       Handle  Access    Name
File       0x4dc   0x1201f9  \Device\HarddiskVolume4\Users\me\AppData\...
Event      0x5e0   0x1f0003  \Sessions\1\BaseNamedObjects\...
Section    0x588   0x120089  \KnownDlls\kernel32.dll
```

Every Handle value is a multiple of 4; the Access column is the
granted-access mask frozen at open (this section: read/execute/map, not
write); the Type column is the object-manager type list made visible. A
browser idles in the low thousands of handles; a count that only grows
under a stress loop is the leak signature (CLI: `handle64.exe -p <pid>`).

[macos] Written from macOS 14; outputs representative. `procinfo`, the
libproc front-end shipping with the developer tools, prints what
`proc_pidinfo()` returns:

```text
$ procinfo 531
  pid: 531  ppid: 1  pgid: 531  uid: 501
  session: 531  start: 39007203
  threads: 17
```

`ps -M -p 531` is the `ps -L` analog (one row per thread); `lsof -p 531 |
wc -l` approximates the fd count; `vmmap 531 | head -3`'s *Physical
footprint* header line is the macOS column of the accounting table (module
06 ch01 reprise).

## How to use this knowledge

- **Kill trees with the right tool per OS.** Windows: a job object with
  kill-on-close; Linux: kill the cgroup or process group, not a PID; macOS:
  launchd owns the lifecycle.
- **Put memory alerts on the predicting number** — commit charge vs limit
  (Windows), `memory.current` vs `memory.max` (Linux containers),
  phys_footprint (macOS). RSS is a diagnostic, never a predictor.
- **Recognize the two inheritance bugs on sight.** "Port still in use after
  restart" / "read never hits EOF" → missing `O_CLOEXEC`; "child can't use
  the parent's file" → unmarked handle or `bInheritHandles=FALSE` — one-line
  fixes each.
- **Never trust a stored PID.** Compare process start times after
  OpenProcess/kill, or use Linux pidfds (5.3+).

## Connections

- **Module 01 ch03 (page tables/COW)** — the address space under every
  diagram column; commit charge is the promise-vs-touch inversion of its
  demand paging.
- **Module 01 ch04 (the stack)** — one stack per execution unit (ETHREAD,
  task_struct, Mach thread); the 16 KB per-task kernel stack is its twin.
- **Module 05 ch01/04 (Android)** — zygote = CLONE_VM economics at scale,
  lmkd = OOM by policy on the same task_struct, cgroups as the app's
  envelope.
- **Module 06 ch01-02 (XNU, Jetsam)** — the task/proc pair compressed here,
  and the footprint ladder the macOS column predicts with; ch03 supplies
  the port table.
- **Module 07 ch02-06** — the Windows pair rides the object-manager world
  mapped here; the Linux pair supplies the fd-backed mappings and smaps/OOM
  arithmetic; ch06 folds the columns into the unified table.

## Quiz

1. *(concept)* "Linux has processes and threads, like every other OS." What
   single kernel structure implements both, what turns one into the other,
   and why does `ps -L` print the same PID in every row?
2. *(concept)* An fd and a HANDLE are both small integers standing for
   kernel state. Give two structural differences — what the number indexes,
   and what rights travel with it — and say which can be handed to another
   process with *fewer* rights than you hold.
3. *(predict the numbers)* Three Linux processes map the same 600 MB file,
   all pages resident; each holds 100 MB of private anonymous RSS, nothing
   swapped. Give each process's RSS, PSS, the total physical memory, and
   the killer's ranking number.
4. *(predict the failure, module 01 ch03)* A program calls
   `VirtualAlloc(NULL, 64 GB, MEM_COMMIT, ...)` on a Windows machine with
   8 GB RAM and an 8 GB pagefile, never touching a page. Does the call
   succeed? Where does the failure occur — and what changes on Linux with
   default overcommit?
5. *(bug diagnosis)* A Linux server restarts nightly, but every few days
   fails to rebind its listening port with `EADDRINUSE` though the old
   process is gone. The server forks helper workers and never calls
   `fcntl`. Explain the leak and the one-line fix.
6. *(bug diagnosis)* A Windows parent writes `0x2F8` — one of its own valid
   file handles — into the child's command line; the child calls
   `ReadFile((HANDLE)0x2F8, ...)` and gets `ERROR_INVALID_HANDLE`.
   Why — and the two legitimate ways to get that file open in the child?
7. *(bug diagnosis)* A Windows monitoring script runs `taskkill /PID 1234`
   when a health check fails. Two weeks later it kills an unrelated process.
   Name the OS-specific mechanism responsible, and two mitigations that
   don't trust the number.
8. *(cross-module, module 06)* A macOS report shows a process with RSS 300
   MB and phys_footprint 500 MB. Where are the missing ~200 MB, which kernel
   component put them there, and why does RSS alone never predict the kill?

<details>
<summary>Answers</summary>

1. **The task_struct, plus a clone() flag mask.** `fork()` creates a task
   sharing nothing; `pthread_create` one sharing CLONE_VM, CLONE_FILES,
   CLONE_SIGHAND, joining a thread group via CLONE_THREAD.
   A "process" is the thread group — one tgid — so `ps -L` prints the
   group's PID in every row, LWP the per-task pids. A thread is a process
   that shares.
2. **Indexing and rights.** An fd lands on a `struct file` — an open-file
   description with refcount, shared offset, and mode flags; it carries no
   permission mask, permissions checked once at open. A HANDLE is (slot<<2
   | flags) into a process-local table whose entry holds the object pointer
   *and* a 32-bit granted-access mask re-checked on every use. Narrowing:
   Windows — `DuplicateHandle` with a smaller DesiredAccess mask; POSIX has
   none (SCM_RIGHTS renumbers the fd, rights unchanged).
3. **RSS 700 MB each (600 shared + 100 private); PSS 300 MB each (100 +
   600/3); total 900 MB** — 600 shared once plus 3 × 100 private, which the
   PSS values sum to. The killer ranks on **RSS + swap** (700 each, a
   three-way tie); it never reads PSS — naive RSS-summing claims 2.1 GB.
4. **The call itself fails.** Commit limit = RAM + pagefiles = 16 GB;
   MEM_COMMIT charges it at promise time, untouched pages or not —
   `VirtualAlloc` returns NULL / `ERROR_COMMITMENT_LIMIT`: module 01 ch03's
   demand paging inverted into an accounting rule. On Linux the 64 GB mmap
   succeeds under default overcommit; the failure arrives at first touch,
   as the OOM killer's SIGKILL.
5. **An inherited listen socket, leaked into a forked worker.** Helpers
   forked while the listening fd was open inherit a copy — fds cross fork
   by default — and without FD_CLOEXEC the fd survives exec. The port stays
   bound because an orphan still holds the open-file description; the socket
   frees when the last reference drops. Fix: `fcntl(fd, F_SETFD,
   FD_CLOEXEC)` (or `accept4(..., SOCK_CLOEXEC)`).
6. **Handle tables are per-process.** `0x2F8` is a slot index in the
   *parent's* table; the child's has nothing (or something else) there —
   hence ERROR_INVALID_HANDLE, not a permission error. Routes: mark the
   handle inheritable and spawn with `bInheritHandles=TRUE`; or
   `DuplicateHandle` into the child (needs PROCESS_DUP_HANDLE), which
   narrows rights too. Usually best: let the child open the file by path.
7. **CID-table PID reuse.** Windows allocates PIDs from a handle table —
   multiples of 4, freed slots reusable immediately — so once the watched
   process exits, 1234 can belong to anyone. Mitigations: re-verify
   identity after `OpenProcess` (compare creation time or name) before
   killing; or own the lifecycle — a job object, terminated by job.
   Linux's analog has a real fix (pidfds, 5.3+).
8. **In the memory compressor.** ~200 MB of dirty pages were compressed
   and evacuated from RAM: gone from RSS, still counted in phys_footprint
   (dirty + compressed + IOKit + purgeable nonvolatile) — the number
   Jetsam's ladder compares against the task's highwater mark (module
   06 ch02). RSS under-reports exactly under the most pressure.

</details>
