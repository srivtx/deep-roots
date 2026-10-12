# Chapter 02 — Locks

> **The 30-second version.** A lock is one word in memory plus a protocol
> about who may proceed. The protocol's cost is a ladder with three rungs: an
> uncontended atomic op (~5-20 ns, a cache-coherence transaction), a contended
> one (the line ping-pongs between cores, ~100 ns per transfer), and blocking
> (syscall + context switch, 1-10 µs — the cliff). Every lock design here —
> spinlocks, tickets, queued locks, the Linux futex, the glibc mutex on top
> of it — answers one question: *how do I stay on the cheapest rung that is
> still correct, and fall off it only when I must?*

## The cost ladder, derived

Strip a mutex to its physical minimum: mutual exclusion means one word that
says who owns a critical section, and an acquisition that cannot interleave
with another. On x86-64 that primitive is a locked read-modify-write such as
`lock cmpxchg`. What does one cost?

First rung — nobody else is interested. The `lock` prefix serializes the
pipeline (~15-20 cycles on Skylake-class cores) and must run with the target
cache line in Exclusive/Modified state. If the line is already yours, that is
the whole cost: ~4-5 ns for a lock+unlock pair (measured below on this 2-vCPU
box: 4.0 ns, glibc 2.41, Debian 13, kernel 5.10). If another core holds a
shared copy, the op issues a read-for-ownership (RFO): invalidate broadcast
plus transfer, tens of ns. "Just an atomic op" is never free — it is a
coherence transaction, module 01 ch20's MESI made billable.

Second rung — two cores want the word. Each atomic op must move the line to
the issuing core: invalidate, transfer, ~40-100 ns per bounce (two-socket
systems pay more). The line does not settle until one side stops issuing
locked ops.

Third rung — you give up the CPU. A contended thread that sleeps pays a
syscall (futex entry, ~100-200 ns), the scheduler, and a context switch:
1-10 µs direct, plus the indirect tax — the L1/TLB working set evicted by
whatever runs next. This is the cliff every lock design avoids falling off.
Keep the ladder in mind: 5-20 ns / ~100 ns / 1-10 µs, three orders of
magnitude end to end.

## Spinlocks: test-and-set, tickets, queues

**Test-and-set (TAS).** Acquire: `xchg 1 -> word` (atomic), spin while the
word is 1, release: store 0. Uncontended cost: one atomic op, first rung.
Now 3 threads on 2 cores — one core oversubscribed. T1 holds the lock and
runs its critical section; T2, on the other core, spins issuing `xchg`, each
one an RFO that invalidates core 0's copy and drags the line over. T1's
release store must fight the spinners for the line:

```
 core 0                              core 1
 -----                               -----
 T1 holds lock, in critical sec      T2 spins: xchg %eax, lock  (atomic)
                                       each xchg = RFO: invalidate
 T1 unlock: mov $0, lock  <---------   core 0's copy, steal the line,
   ...must first re-acquire the         ~100 ns per bounce
    line that T2 keeps stealing
```

Two costs. The measured one: the release itself is delayed by spinner
traffic — handoff latency grows with the number of spinners. The structural
one: the scheduler preempts T1 mid-critical-section (oversubscription
guarantees it). T1 is descheduled but still holds the lock; a full core
burns atomic ops while the only thread that can make progress is not
running. Hence the kernel's rule: `spin_lock` disables preemption, and you
must not sleep holding one.

**Ticket locks: fairness as a counter.** Lamport's bakery — take a number,
wait for the display — hardware-accelerated. Two counters: `next_ticket` and
`now_serving`. Acquire: `fetch_and_add 1` on `next_ticket`; spin *reading*
`now_serving` until it equals your ticket; release: `fetch_and_add 1` on
`now_serving`. Two properties follow. Plain reads of `now_serving` keep the
line Shared on every core — the spin generates no traffic; only the release
increment invalidates once. And order is FIFO: a thread waits at most N-1
critical sections — bounded wait, no starvation, the property TAS lacks, at
the same one atomic per acquisition. Linux's x86 spinlock used tickets for
years for exactly this fairness.

**Queued locks (MCS): spin on a private line.** The insight that solved
spinlock scalability: stop spinning on the lock word. Each waiter owns a node
on its own stack — `{ flag: 1, next: ptr }`. Enqueue is an atomic swap of the
queue's tail pointer; the waiter then spins reading *its own* `flag` — a
line no other core ever writes, cached locally at zero coherence cost.
Release: walk your `next` pointer once, store 0 into the successor's flag —
that store is the *only* traffic per handoff. O(1) line transfers instead of
TAS's O(waiters) storm, and the release never competes with spinners. The
idea powers Linux's qspinlock (per-CPU queue nodes, since ~2014) and the
optimistic-spin queue (osq).

## The futex: the fast path is not a syscall

A blocking lock needs somewhere to sleep — historically a kernel call on
every acquisition. The futex (fast userspace mutex, Linux 2.6, 2003) inverts
that: *the kernel is only involved when there is actual waiting.* The lock
is any aligned 32-bit word in your own memory. The protocol (Ulrich
Drepper's "Futexes Are Tricky", 2011) has three states: **0** unlocked,
**1** locked with no waiters, **2** locked with waiters:

```
        userspace (no syscall)                 kernel
 ┌───────────────────────────────┐    ┌─────────────────────────────────┐
 │  lock word (4 B, 4-aligned)   │    │  futex hash bucket (hash of     │
 │                               │    │  uaddr, bucket lock-protected)  │
 │   0 = unlocked                │    │                                 │
 │   1 = locked, no waiters      │    │   wait queue:   T3 ──► T7       │
 │   2 = locked, has waiters     │    │   (sleeping tasks, no spinners) │
 └───────────────────────────────┘    └─────────────────────────────────┘
  T2 lock:   CAS 0 -> 1  succeeds.   DONE.  No syscall. (~4 ns measured)
  T3 lock:   CAS 0 -> 1 fails.  Write 2, then
             FUTEX_WAIT(word, 2) ─────►  kernel re-reads the word; if it
                                        still == 2, sleep in the queue.
                                        If it changed: EAGAIN, retry.
  T2 unlock: atomic exchange word -> 0.
             old value 1?  DONE, no syscall.
             old value 2?  FUTEX_WAKE(word, 1) ─────► wake exactly one
             waiter (T3).  T3 retries its CAS 0 -> 1; since T7 still
             waits, T3 writes 2, not 1 — the waiters hint survives.
```

Why the kernel re-checks the word: it closes the race between "I decided to
sleep" and "I am asleep." If T2's unlock lands after T3's check but before
T3 sleeps, the wakeup would be lost forever — a deadlock with no cycle in it.
Comparing `*word == 2` inside the critical section that enqueues you makes
check-and-sleep atomic; on mismatch `FUTEX_WAIT` returns `EAGAIN` and you
loop. That re-check is the load-bearing seam of the protocol.

glibc's `pthread_mutex_lock` (glibc 2.41, this box) *is* this protocol; the
mutex struct adds type/robust flags, an owner TID for error-checking
(`EDEADLK` on self-lock), a recursion count, and for robust mutexes a link
in the thread's *robust list* (registered via `set_robust_list`) — when the
holder dies, the kernel walks that list, marks each word
`FUTEX_OWNER_DIED` (0x40000000), and waiters get `EOWNERDEAD` instead of
sleeping until heat death.

**Priority-inheritance futexes.** A PI mutex encodes the word differently:
bits 0-29 = owner TID, bit 31 = `FUTEX_WAITERS` (0x80000000), bit 30 =
`FUTEX_OWNER_DIED`. `FUTEX_LOCK_PI` makes the kernel manage an `rt_mutex`:
waiters sit in a priority-sorted list, and when you block, the kernel boosts
the owner's priority to yours, walking the chain (the owner may itself wait
on another lock); a closed cycle returns `EDEADLK`. This is Mars Pathfinder
(module 03 ch03) at mechanism level: VxWorks 1997, a low-priority task
holding a bus mutex a high-priority task needed, a medium-priority task
preempting the holder, the watchdog resetting the lander. The remote fix was
enabling priority inheritance on that mutex; `FUTEX_LOCK_PI` is the same
surgery, per lock, in Linux userspace.

**Adaptive spinning: the rung between.** Sleeping immediately is wasteful if
the holder releases in 200 ns; spinning forever is fatal if the holder is
descheduled. The compromise, both places: spin *only while the holder is
running*. glibc's `PTHREAD_MUTEX_ADAPTIVE_NP` spins up to ~100 iterations
(`max_adaptive_count`) before `FUTEX_WAIT`. The kernel's `mutex_lock` polls
the owner task's `on_cpu` flag and abandons the spin the instant it is not
set — you never spin through a context switch. The spinners queue via osq,
an MCS-style structure, so the spinning doesn't reproduce the TAS storm.

## Deadlock as an engineering problem

The Coffman conditions (mutual exclusion, hold-and-wait, no preemption,
circular wait) are usually taught as theory; as engineering they are a
checklist of things to break *on purpose*: give up ME with lock-free designs
(module 09 ch03), release and retry instead of holding, or — the break that
scales — forbid circular wait with a **lock ordering hierarchy**: a written
global order ("A before B before C", parent-before-child) that makes a cycle
impossible. One page of documentation, zero runtime — the cheapest Coffman
break available.

The kernel enforces its own discipline with **lockdep** (CONFIG_PROVE_LOCKING):
every acquisition of B while holding A adds the edge A->B to a directed graph
over lock *classes* (each lock's class is assigned when it is initialized).
An edge that would complete a cycle triggers a report with full stack traces
at the *first* occurrence — before the deadlock ever schedules simultaneously.
lockdep also tracks usage per context (process, softirq, hardirq), catching a
sleeping lock acquired in irq context. The cost — every lock op updates the
graph, double-digit % overhead — is why distros ship it only in debug
kernels, and why the famous dmesg line exists: "turning off the locking
correctness validator" when the graph exceeds its limits.

**Lock granularity** has a classic U-curve. One big lock: no ordering
problems, but every acquisition is contended rung-two traffic. Many small
locks: contention drops, but the ordering hierarchy grows roughly with the
number of *pairs* of locks that ever nest — quadratic complexity in the thing
you are worst at verifying. And at the fine end, adjacent lock words in one
64-byte line are *false sharing* (module 01 ch20) — logically uncontended
locks bouncing a line they don't logically share; `alignas(64)` each lock.

The insider's tuning heuristics, in order: **measure before restructuring**
(`perf lock record ./app && perf lock report`; or `/proc/lock_stat` with
CONFIG_LOCK_STAT — per class: contention count, wait/hold times, and the call
site where threads blocked). **Prefer sharding to spin tuning**: N per-CPU or
per-bucket locks with no nesting destroy contention without adding a single
ordering rule, whereas tuning spin counts moves you between rungs of a
ladder the topology already chose.

## What insiders know

- The three-state word (0/1/2) has a sticky subtlety: a fresh acquirer that
  CASes 0->1 while old waiters sleep writes *1*, and its unlock (old==1)
  skips the `FUTEX_WAKE` — safe only because each waiter's expected-value 2
  makes the kernel's re-read return `EAGAIN` and force a retry. The protocol
  is closed under interleavings *because of* that re-check.
- PI futex word encoding: owner TID in bits 0-29, `FUTEX_WAITERS` bit 31
  (0x80000000), `FUTEX_OWNER_DIED` bit 30 (0x40000000); cycles in the boost
  chain come back as `EDEADLK` from `FUTEX_LOCK_PI` — deadlock detection
  before the deadlock.
- Cost ladder, platform-marked: uncontended lock+unlock 4.0 ns (glibc 2.41,
  this 2-vCPU VM, gcc -O2); contended 2-thread on 2 cores ~350-390 ns — and
  that ~90x is *with* futex syscalls in it, because a fair 2-core handoff
  degenerates into a FUTEX_WAIT/FUTEX_WAKE pair; cross-core line transfer
  ~40-100 ns; context switch 1-10 µs.
- lockdep reports the *potential* deadlock on the first violating order —
  the cycle need never have actually scheduled. Its overhead (every
  acquisition updates the class graph) is why it is debug-kernel only, and why
  it self-disables ("turning off the locking correctness validator") when
  the graph exceeds its chain limits.
- Adaptive spinning stops on owner-not-running, not on timeout alone: glibc
  caps `PTHREAD_MUTEX_ADAPTIVE_NP` at ~100 spins (`max_adaptive_count`),
  while the kernel's optimistic spin polls the owner's `on_cpu` flag —
  neither will spin through a context switch.
- The kernel reuses the MCS insight twice: qspinlock (per-CPU queue nodes,
  merged ~2014, replacing the x86 ticket lock) and osq, backing
  `mutex_lock`'s adaptive path. "Spin on a private line" is the most-ported
  idea in lock design.

## See it yourself

Both labs ran on this box: Debian 13, glibc 2.41, kernel 5.10.134, 2 vCPU,
gcc 14.2. Your numbers will differ; the *ratio* is the lesson.

**Lab 1 — the ladder in one number:**

```c
// mutex_bench.c   build: gcc -O2 -pthread mutex_bench.c -o mutex_bench
static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
// phase 1: 1 thread, 10M lock+unlock, time each
// phase 2: 2 threads hammering the same lock for 2 s, count total ops
```

```console
$ ./mutex_bench
uncontended:    4.0 ns per lock+unlock  (10000000 iters, 1 thread)
contended:    383.6 ns per lock+unlock  (10428576 ops in 2 s, 2 threads)
slowdown:       95.9x
```

What to notice: 4 ns is rung one — two atomics on a locally-owned line, no
syscall. ~380 ns is not pure ping-pong: glibc's default mutex does not spin,
so each fair handoff on 2 cores is a `FUTEX_WAIT` + `FUTEX_WAKE` pair. Rebuild
with `PTHREAD_MUTEX_ADAPTIVE_NP` (the number drops), then run 3 threads on
2 cores and watch the context-switch cliff take over.

**Lab 2 — a deliberate AB/BA deadlock, diagnosed.**

```c
// deadlock.c   build: gcc -O0 -g -pthread deadlock.c -o deadlock
// t1: lock(A); sleep 200ms; lock(B)      t2: lock(B); sleep; lock(A)
```

```console
$ ./deadlock &        # prints "t1: locked A", "t2: locked B", then silence
$ PID=$(pgrep -x deadlock)
$ for t in /proc/$PID/task/*; do \
    printf 'tid %-6s wchan=%-22s ' $(basename $t) $(cat $t/wchan); \
    grep -m1 '^State' $t/status; done
tid 6238   wchan=futex_wait_queue_me    State:  S (sleeping)
tid 6240   wchan=futex_wait_queue_me    State:  S (sleeping)
tid 6241   wchan=futex_wait_queue_me    State:  S (sleeping)
```

All three threads (main is stuck in `pthread_join`'s futex) report
`futex_wait_queue_me` — asleep in the kernel's futex wait queue is the
signature of a lock that will never come. `/proc/$PID/task/*/stack` gives
the kernel backtrace but needs root (`cat` here: Permission denied). With
gdb (absent from this sandbox; frames are the canonical glibc shape):

```gdb
$ gdb -p $PID
(gdb) thread apply all bt
Thread 2:  #0 futex_wait  #1 __pthread_mutex_lock  #2 t1_fn  deadlock.c:14
Thread 3:  #0 futex_wait  #1 __pthread_mutex_lock  #2 t2_fn  deadlock.c:23
```

Two frames holding opposite locks and wanting the other's — the cycle reads
off the page. Test the fix: make t2 take A first; the program terminates.

## How to use this knowledge

- **A hung service triage**: every thread `State: S` with `wchan` =
  `futex_wait_queue_me` means lock wait or deadlock — `thread apply all bt`,
  look for the cycle across frames before touching anything else.
- **Performance blaming**: "~90x under contention, and the fix is topology,
  not the lock implementation" — check `perf lock` / `/proc/lock_stat` first;
  shard the contended lock before rewriting anything clever.
- **Design reviews**: demand the lock ordering hierarchy as a document; any
  nested acquisition not derivable from it is a latent deadlock — what
  lockdep flags at first occurrence.
- **Interview seniority**: "why does an uncontended pthread mutex cost no
  syscall?" — the futex word dance, three states, kernel re-check — is a
  complete answer at a level most candidates never reach.

## Connections

- **Module 01 ch20** — the MESI/cache-line machinery the cost ladder is
  billed in: RFO, invalidate, transfer.
- **Module 03 ch03** — the race-family chapter's Mars Pathfinder inversion,
  re-derived here at mechanism level (`rt_mutex` plist, boost chain).
- **Module 09 ch01** — lock/unlock as acquire/release edges: the pair that
  makes SC-for-DRF hold for every locked program.
- **Module 09 ch03** — when locks lose: hazard pointers, epochs, RCU — and
  the honest decision table for leaving this chapter behind.
- **Module 09 ch04** — the alternative architecture: transfer ownership over
  a channel instead of sharing memory behind a lock.
- **Module 05 ch03** — the binder 16th-thread deadlock: circular wait over
  *thread-pool slots*, no lock in sight.

## Quiz

1. An uncontended `pthread_mutex_lock`+`unlock` pair measured 4.0 ns; a plain
   local increment takes under 1 ns. Where do the extra nanoseconds go — no
   other core touches the mutex?

2. Two waiters queue on an MCS lock. Count the cross-core cache-line
   transfers for one lock handoff, and compare with a TAS lock where 5
   threads spin. Why does the TAS release store get *slower* as threads are
   added?

3. Thread A holds the lock (word == 1). Threads B and C call `lock()`, both
   end up sleeping in `FUTEX_WAIT` with the word == 2. A calls `unlock()`.
   Trace the word's values and syscalls through B acquiring, and state where
   C is while B holds it.

4. The lab box (2 cores) shows ~380 ns per contended op with 2 threads. You
   rerun with 3 threads on the same 2 cores. Predict what happens to the
   number and name the ladder rung responsible.

5. A production process is hung; `/proc/PID/task/*/wchan` shows all threads
   in `futex_wait_queue_me`. Backtraces: T1 holds mutex M1 and is blocked on
   M2; T2 holds M2 and is blocked on M1. Which Coffman conditions are
   satisfied, what is the standard fix, and *when* would lockdep have caught
   it?

6. Two processes share a mutex in `shm` (process-shared, but a *normal*,
   non-robust type). The process holding it is killed with `SIGKILL`. What
   happens to the other process's waiters, and which mutex feature exists
   precisely for this failure?

7. If `pthread_mutex_unlock` were compiled as a *relaxed* store instead of a
   release-store, give an interleaving where a critical section's data writes
   are lost to the next acquirer. Which edge from chapter 01 is destroyed?

8. A struct declares `pthread_mutex_t a, b;` back to back, 40 bytes each on
   a 64-byte-line machine. Two threads on two cores lock `a` and `b`
   respectively — different mutexes, zero logical contention — yet the
   benchmark shows a large slowdown. Diagnose and prescribe the fix.

<details>
<summary>Answers</summary>

1. **The atomic op is a coherence transaction, not just an instruction.**
   `lock cmpxchg` serializes the pipeline (~15-20 cycles) and needs the line
   in E/M state; if another core holds a copy, it upgrades via RFO
   (~40-100 ns) even with no contention *right now*. A plain increment needs
   no exclusive ownership.

2. **MCS: two transfers** — the enqueue atomic on the tail pointer, plus the
   releaser's single write to the successor's flag; the spinning itself is
   free (each waiter polls a line only its eventual releaser writes).
   **TAS: O(waiters) and worsening** — every spinner's atomic is an RFO that
   invalidates all copies, so with 5 spinners the line bounces continuously
   at ~100 ns per transfer, and the release store, also to that line,
   repeatedly loses arbitration to locked ops.

3. A's `unlock()` atomically exchanges 2 -> 0, sees old value 2, and issues
   `FUTEX_WAKE(word, 1)` — one syscall, waking exactly *one* waiter, B; C
   stays asleep. B retries, its CAS 0 -> 1 succeeds, but since C is still
   queued, B writes 2 — so the "has waiters" hint survives. When B unlocks
   with old value 2, it wakes C. Key point: one wake per unlock; the word
   returns to 0 only at release.

4. It gets *worse* — toward microseconds per op. With 3 threads on 2 cores
   one thread is always descheduled; sooner or later it is a *lock holder*
   preempted mid-critical-section, and the waiters then block in `FUTEX_WAIT`
   until the holder is rescheduled: the context-switch rung (1-10 µs) enters
   the critical path, and throughput per op can drop below the 2-thread
   case — why oversubscription is poison for spin-based designs.

5. All four: mutual exclusion (each mutex is exclusive), hold-and-wait (each
   holds one while requesting the other), no preemption (a mutex cannot be
   forcibly taken), circular wait (T1->M2->T2->M1->T1). The fix breaks
   circular wait: a global ordering hierarchy — both threads take M1 before
   M2 — makes a cycle impossible. lockdep would have flagged it at the
   *first* M2->M1 acquisition in the system's history, long before both
   orders ever ran concurrently: it reports *potential* deadlocks, not
   actual ones.

6. The waiters sleep forever: `SIGKILL` runs no cleanup, so nobody issues
   the `FUTEX_WAKE`, and a normal mutex's word still reads "locked." The
   feature built for this is the **robust mutex** (`PTHREAD_MUTEX_ROBUST`):
   the holder registers locks in a robust list (`set_robust_list`); on death
   the kernel marks each word `FUTEX_OWNER_DIED` and wakes waiters, who get
   `EOWNERDEAD` and can repair or abandon the state.

7. T1 runs `data = 42;` then unlock as a relaxed store; the CPU may make the
   unlocked word globally visible *before* `data = 42` (module 01 ch20's
   store buffer). T2's acquire-load of the word then succeeds and reads
   `data == 0` — the mutex excludes T2 but no longer *publishes* T1's writes
   from the critical section. The destroyed edge is the release-store ->
   acquire-load synchronizes-with edge that makes SC-for-DRF (ch01) hold.

8. False sharing: `a` and `b` sit in the same 64-byte cache line, so atomic
   ops on *different* mutexes RFO the *same* line — every acquisition bounces
   it cross-core (~40-100 ns per transfer, the ~90x-class slowdown of true
   contention), and no locking bug exists. Fix: `alignas(64)` each mutex
   (or 56 bytes of padding) — module 01 ch20's remedy applied to lock words.

</details>
