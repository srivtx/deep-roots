# Chapter 03 — The race family

> **The 30-second version.** Memory bugs corrupt one thread's view of the heap; race
> bugs corrupt the *ordering* between threads. Two facts generate the whole family:
> the scheduler interleaves threads at times you cannot observe, and the hardware
> memory model (module 01 ch20) reorders operations even within one thread. Those two
> facts derive data races, TOCTOU, deadlock, livelock, starvation, priority inversion,
> and lost wakeup — each with its fix and its detector (ThreadSanitizer). Every fix
> makes the same move: make the check and the act one atomic step.

## 1. The mechanism: interleaving plus reordering

Single-threaded code gets a property for free: lines run in source order, and you
read what the last line wrote. Threads delete that property twice. First, the
scheduler: two threads of three steps each can interleave 20 ways;
six steps each, 924 ways. The OS owes you no order, yet correctness may silently
depend on the subset you tested under. Chapter 01 called this an
*invisible input*: time changes the output without changing the code — the Heisenbug
property, and the reason these bugs vanish under a debugger.

Second, the memory model. Even with the interleaving pinned, you don't get source
order: store buffers let a core's later loads pass its earlier stores, and the compiler
may reorder or cache in registers if the single-threaded result is unchanged. Module
01 ch20 derived this — two threads can both read 0 after both wrote 1. "It looks
sequential" is not a guarantee the machine ever made.

## 2. Data race vs race condition: two different diseases

A **data race** is a language-rules term: two accesses to the same location, at least
one a write, from different threads, with nothing synchronizing them. In C and C++
(C11 §5.1.2.4) that is *undefined behavior* — not "a wrong value" but anything: torn
reads, stale values, loads hoisted out of loops. "Benign data race" is a bet that the
compiler and CPU stay polite forever; it loses on 32-bit platforms (see "insiders").

A **race condition** is a logic-level term: correctness depends on the interleaving —
you can have one with zero data races (check-then-act with each half locked in a
separate critical section). The dangerous bug is having both.

The canonical shape is **check-then-act**, lazy initialization:

```c++
// C++ — broken double-checked locking. Do not ship.
static Widget* instance;              // plain pointer, not atomic
std::mutex m;

Widget* get() {
    if (instance == nullptr) {            // CHECK: unlocked read (data race)
        std::lock_guard<std::mutex> g(m);
        if (instance == nullptr)          // re-check inside the lock
            instance = new Widget();      // ACT: allocate, construct, publish
    }
    return instance;
}
```

Two failure modes: (1) the unlocked check is a data race, hence UB; (2) even granting
sequential semantics, `new Widget()` is three events — allocate, construct, publish —
and the compiler/CPU may publish the pointer *before* the constructor's writes, so
thread B's fast path returns a half-built object. The fix in one paragraph: make
publication itself ordered — `std::atomic<Widget*>` with a release store to publish
and an acquire load on the fast path; Java's `volatile` does the same since JEP 133.
*Why* release/acquire suffices is module 09 ch01–02's proof; here you need only the
rule: one synchronization edge between the write and the read, and the race is gone.

Detector: TSan — it convicts on *order*, not outcomes, reporting both unsynchronized
stacks even when the count is right (chapter 05: happens-before via vector clocks,
~5–15x slowdown).

## 3. TOCTOU: the filesystem is a shared variable

Put the two threads in separate *processes* and the shared variable becomes the
filesystem: a path is a name the whole machine can rebind between any two of your
syscalls — **TOCTOU**, time-of-check to time-of-use:

```
  your process                            attacker's process
  +------------------+                  +--------------------------+
  | lstat("/tmp/x")  | "regular file,   | mv /tmp/x /tmp/stolen    |
  |                  |  12 bytes, safe" | ln -s /etc/passwd /tmp/x |
  | open("/tmp/x")   |  <- follows      +--------------------------+
  | read()           |     the SYMLINK now
  +------------------+
        |                    |
        v                    v
   CHECK answered        USE sees a
   the OLD file          DIFFERENT file

   (the window between the two syscalls is unbounded)
```

The classic exploit: privileged code lstats a path, decides it is safe to write, and
the attacker swaps in a symlink to `/etc/passwd` during the window: the check passed,
the act hit a different object.

The `/tmp` lore: `/tmp` is world-writable (mode 1777; the sticky bit stops only
deletion of others' files), so any name you did not create is attacker-controlled.
The fixes collapse check and act into one step:

- **Create atomically**: `open(path, O_CREAT|O_EXCL, 0600)` fails if the name exists —
  the existence check and the creation are one kernel operation, no window. glibc's
  `mkstemp()` adds a random name; `mkdtemp()` does directories.
- **Open first, fstat the fd**: the descriptor points at the object; later rebinding
  the name changes nothing.
- **Refuse indirection**: `open(path, O_NOFOLLOW)` (Linux, BSD) fails if the final
  component is a symlink.

No tool flags cross-process TOCTOU (TSan sees threads only) — review catches it:
grep `lstat`/`access` followed by `open`/`unlink`/`chmod` on one path.

## 4. Deadlock: the four Coffman conditions, derived

Locks remove data races by forbidding interleavings — and thereby create the
possibility of removing *all* interleavings. Derive the necessary conditions with two
threads and two mutexes:

1. **Mutual exclusion** — a mutex is held by one thread at a time, or nothing here is
   interesting.
2. **Hold-and-wait** — a thread holds one mutex while asking for another (with empty
   hands no cycle can form).
3. **No preemption** — you cannot wrest a mutex from its holder (otherwise the cycle
   breaks from outside).
4. **Circular wait** — a ring of threads, each holding what the next wants.

Coffman, Elphick, and Shoshani stated these in 1971; the derivation shows they are
*jointly necessary* — break any one and a cycle can never close, so deadlock is
impossible. The engineering move is to break the condition you can control globally:
**circular wait**, via **lock ordering** — number every lock, require ascending
acquisition, and A→B in one function versus B→A in another becomes a coding error,
not a hang. (With no global order, acquire all locks in one bundle instead, starving
hold-and-wait.)

Dijkstra's dining philosophers (1965), each taking his left fork, then reaching for
his right:

```text
dining philosophers — 5 forks = 5 mutexes, every philosopher grabs LEFT first:

                P1
              /    \
         F1  /      \  F2
           v        v
      +------+    +------+
      | fork |    | fork |
      +------+    +------+
           ^        ^
            \      /
         F5  \    /  F3
             v    v
       P5 +--------+ P3
          | each holds one fork,
          | each waits forever     <-- ring of waiting closes:
          +--------+                  CIRCULAR WAIT, all 4 conditions met
```

All five take the left fork "simultaneously": every fork held, every philosopher
blocked forever. The textbook fix — odd philosophers take left-then-right, even ones
right-then-left — is lock ordering in costume; the minority breaks the circle.

Deadlock is not the only way waiting goes wrong — livelock and starvation are distinct
outcomes, not synonyms:

- **Livelock**: threads run at full speed and make no progress — each detects the
  conflict, backs off, retries, collides again (100% CPU, 0% output). Randomized
  backoff exists to break exactly this symmetry.
- **Starvation**: a runnable thread the scheduler effectively never picks — the lock
  repeatedly grabbed by new arrivals before the queued waiter is served ("barging" in
  pthreads folklore). Fair/FIFO ticket mutexes fix it; a watchdog cannot tell "slow"
  from "starved" until it has already fired.

Detectors: TSan reports "lock-order-inversion (potential deadlock)" when it sees the
same lock pair acquired in both orders anywhere in a run — before any hang. A
post-mortem hang is read from all stacks (`gdb -p <pid>`; `thread apply all bt`):
the Coffman ring is threads parked in `__pthread_mutex_lock`.

## 5. Priority inversion: Mars Pathfinder, 1997

Priorities promise the important thread runs first; a mutex can silently break that
promise. The textbook accident is the Mars Pathfinder lander (July 1997), whose
flight software ran VxWorks on a radiation-hardened RAD6000 (PowerPC-derived,
~20 MHz). A **high-priority bus-management task** drove the spacecraft bus; a
**low-priority ASI/MET task** (meteorological data) touched the same bus through the
same mutex; a **medium-priority communications task** ran long bursts. The schedule
landed like this:

1. The low-priority task acquires the mutex.
2. The high-priority task wakes, preempts it, then blocks on the mutex — its fate
   tied to the low task's.
3. The medium-priority communications task preempts the *low* task — the holder.
4. So the high task is blocked by a medium task through a lock held by a low task:
   **priority inversion** — the *unbounded* kind, since the stall lasts as long as the
   medium task's whim, a party unrelated to the lock.
5. The high task misses its deadline; the watchdog resets the spacecraft — on Mars,
   every few days, losing science data each time, but never crashing, which is why it
   kept calling home and JPL could debug it remotely.

The detail interviews actually probe: **the fix was a flag, not a redesign.** VxWorks
mutexes support **priority inheritance** (creation flag `SEM_INVERSION_SAFE`): the
holder temporarily inherits the priority of its highest-priority waiter — the low task
becomes high, preempts the medium task, releases the mutex, drops back. Pathfinder's
mutex had been created without it. JPL reproduced the failure on a ground replica
using the spacecraft's own onboard trace logs, then **uplinked a short C program that
ran on Mars and flipped the mutex into inheritance mode** — no reboot, no
restructuring. The lesson: know which of your primitives have the knob — Linux's is
`PTHREAD_PRIO_INHERIT` (`pthread_mutexattr_setprotocol`).

## 6. Lost wakeup: why the predicate is a `while`

The last family member needs no adversary — a standards document causes it. A
condition variable is how a thread sleeps until "something changed":

```c
// C11/pthreads — the waiting side MUST re-check inside a loop
pthread_mutex_lock(&m);
while (!data_ready) {              // while, never if
    pthread_cond_wait(&cv, &m);    // atomically: unlock, sleep, relock
}
consume();
pthread_mutex_unlock(&m);
```

Two distinct bugs both force the `while`. **Spurious wakeup**: POSIX explicitly permits
`pthread_cond_wait` to return with nobody having signaled — demanded by implementers
so condvars can be built on Linux futexes without wake-machinery races (glibc 2.x:
`pthread_cond_signal` is a `futex(FUTEX_WAKE)` underneath). With `if`, a spurious
return makes you consume data that does not exist. **Lost wakeup**: the signaler may
finish *before* the waiter arrives — the state changed, the signal was consumed, and
an `if` sleeps through the very event it waited for. The loop re-checks the predicate
— the *state*, not the notification. Signals are edge events that may be lost or
forged; the predicate is the level, and only levels are truth. That is the
while-not-if rule, one of the few places a standard mandates defensive code for a
derivable reason.

## What insiders know

- **The Coffman conditions are a code-review checklist, in order**: scan
  hold-and-wait (a lock taken while holding another), no-preemption (retry loops
  around lock attempts), then hunt circular wait — the only condition fixable with one
  global rule (lock ordering). Linux lockdep and TSan's "lock-order-inversion"
  reports mechanize this scan.
- **The Pathfinder fix was a flag**: VxWorks `SEM_INVERSION_SAFE` (priority
  inheritance) flipped on by a C program uplinked to Mars, 1997, after the failure was
  reproduced on a ground replica from the craft's own trace logs. The interview
  version: "unbounded priority inversion, watchdog reset, inheritance as the fix."
- **`O_EXCL` is the one-flag TOCTOU fix**: `open(path, O_CREAT|O_EXCL, 0600)` makes
  existence-check and create a single kernel operation; `mkstemp()` adds the random
  name; `O_NOFOLLOW` refuses the final symlink; `O_TMPFILE` (Linux 3.11+) hands you an
  unnamed inode the attacker cannot even name.
- **The predicate is `while`, never `if`** — not style: POSIX allows spurious wakeups
  (so futex-based condvars stay race-free), and a signal is an edge, not a level — it
  can be lost before you sleep. Every `pthread_cond_wait` return must re-read the
  level.
- **TSan convicts on order, not outcome**: it flags the unsynchronized pair with both
  stacks even when the counter came out correct — "ran clean under TSan" means "no
  racy pair executed together," so run it on interleaving-rich tests. Cost: ~5–15x
  slowdown, 5–10x memory (TSan v2; chapter 05).
- **"Benign data race" loses on 32-bit**: an aligned 64-bit load on x86-64 is one
  instruction, but the same `int64_t` on 32-bit ARM/x86 is two loads — the torn value
  appears only on the smaller machine. UB by C11 §5.1.2.4 on both.

## See it yourself

**Lab 1 — a data race, caught red-handed.**

```bash
# race.cpp — two threads, one unsynchronized counter
cat > race.cpp <<'EOF'
#include <thread>
long long counter = 0;                  // plain (non-atomic) global
void hammer() {
    for (int i = 0; i < 100000; ++i) counter++;   // read-modify-write, no lock
}
int main() {
    std::thread a(hammer), b(hammer);
    a.join(); b.join();
    return counter == 200000 ? 0 : 1;   // silently wrong when the race fires
}
EOF
clang++ -fsanitize=thread -g -O1 -std=c++17 -pthread race.cpp -o race  # g++ 12+ identical
./race ; echo "exit=$?"
```

It prints `exit=66` (TSan's default failure exit) and:

```
WARNING: ThreadSanitizer: data race (pid=30449)
  Read of size 8 at 0x...88 by thread T2:
    #0 hammer() race.cpp:4
  Previous write of size 8 at 0x...88 by thread T1:
    #0 hammer() race.cpp:4
  Location is global 'counter' of size 8
SUMMARY: ThreadSanitizer: data race race.cpp:4 in hammer()
```

What to notice: both stacks are the *same line* — `counter++` is both the read
and the write; the race is reported even though this run's count was correct; and one
line fixes it — `std::atomic<long long> counter;` — making the report disappear.

**Lab 2 — the TOCTOU window, live** (`mv` + `ln -s`):

```bash
mkdir -p /tmp/toctou && echo "salary_data" > /tmp/toctou/target.txt
cat > checker.py <<'EOF'
import os, stat, time
path = "/tmp/toctou/target.txt"
st = os.lstat(path)                     # STEP 1: inspect the path
if not stat.S_ISREG(st.st_mode): raise SystemExit("refusing")
print(f"[checker] lstat: regular file, {st.st_size} bytes -- safe")
time.sleep(2)                           # the window (any scheduling gap)
print(f"[checker] opened and read: {open(path).read()!r}")
EOF
python3 checker.py & sleep 0.2
mv /tmp/toctou/target.txt /tmp/toctou/stolen.txt
ln -s /etc/passwd /tmp/toctou/target.txt   # attacker rebinds the name
wait
```

Observed output:

```
[checker] lstat: regular file, 12 bytes -- safe
[checker] opened and read: 'root:x:0:0:root:/root:/bin/bash\n...'
```

The checker *verified* a 12-byte regular file, then read `/etc/passwd` through a
symlink. Fix it — `os.open(path, os.O_RDONLY | os.O_NOFOLLOW)`, or open-then-`fstat`
the fd — rerun, and the attack fails with `OSError: [Errno 40] Too many levels of
symbolic links` instead of silently succeeding. The window didn't shrink; it was
removed.

## How to use this knowledge

- **Review as mechanism matching**: `lstat`-then-`open`, check-then-act outside a
  lock, two locks in opposite order in sibling functions, `if` around a `wait` — each
  shape names a bug and its fix on sight.
- **The right detector, chosen not guessed**: wrong values under load → TSan (needs
  the racy threads to actually run); a hung process → `gdb -p`, all stacks, look for
  the Coffman ring; intermittent failures in a privileged daemon → grep the TOCTOU
  pattern.
- **Design leverage**: collapse check+act with the primitives built for it — `O_EXCL`,
  `std::call_once`, compare-and-swap (module 09), open-then-fstat — and impose a lock
  order on day one; retrofitting one costs an incident.
- **Incident and interview fluency**: derive Pathfinder instead of reciting it, and
  explain why the fix was cheap — the primitive already had the knob.

## Connections

- **Module 01 ch20** — the root of the family: store buffers and reordering are *why*
  "it looks sequential" fails; this chapter is ch20 read as a bug catalog.
- **Module 03 ch01** — the Heisenbug: timing as an invisible input is this family's
  elusiveness.
- **Module 03 ch05** — TSan's insides: vector clocks, happens-before, why
  lock-order-inversion warnings fire before the hang.
- **Module 09 ch01–02** (queued) — the depth deferred: acquire/release proven; why
  double-checked locking needs exactly them.
- **Module 05 ch03** (queued) — Binder threads: one process, a thread pool, and every
  bug here reachable from a Binder callback.

## Quiz

1. Two threads each increment a plain (non-`atomic`) `int` 1,000,000 times; the final
   value is 1,999,998. Data race, race condition, both, or neither — and which
   *document* makes one of those words apply?

2. A colleague ships a "benign data race": a plain, aligned `int64_t` progress counter
   no thread branches on. Give the 32-bit counterexample and the standard's verdict.

3. *Predict:* `pthread_cond_wait` returns and the waiter immediately consumes a message
   from an empty queue. Name both mechanisms and the defending construct.

4. *Predict the output:* in Lab 2, the attacker's `ln -s` lands between the checker's
   `lstat` and its `open(path).read()`. What do the printed lines show — and which
   `open` flag turns the outcome into an error?

5. *Diagnose:* this code deadlocks a few times per million runs. Why? Name the exact
   Coffman condition your fix removes.

   ```c
   void transfer(A, B)     { lock(A); lock(B); move(); unlock(B); unlock(A); }
   void transfer_rev(A, B) { lock(B); lock(A); move(); unlock(A); unlock(B); }
   ```

6. *Diagnose:* Pathfinder's mutex was held by a **low**-priority task and wanted by a
   **high**-priority one, yet the high task stalled for the duration of a **medium**
   task's work. Why didn't priority scheduling just run the high task — and what made
   the stall *unbounded* rather than bounded?

7. *Diagnose:* a C++ service pegs all 16 cores at 100% CPU with zero log output.
   Deadlock, livelock, or starvation — and what from `top` plus `gdb -p` distinguishes
   them?

8. *Cross-chapter:* module 01 ch20's SB litmus test has both threads read 0 after both
   wrote 1, with no incoherent line anywhere. How does that explain why the unlocked
   read in broken double-checked locking (§2) can return a half-built object even when
   the interleaving looks right?

<details>
<summary>Answers</summary>

1. **Both.** The off-by-two value proves lost updates — increments are
   read-modify-write, and interleavings drop updates: a race *condition* (outcome
   depends on interleaving). But the two unsynchronized accesses (one a write) to one
   location are also a **data race**, and the *language standard* — C11
   §5.1.2.4, the C++ memory model — makes that term mean undefined behavior: any
   behavior is permitted, torn reads included.

2. **Alignment buys nothing on 32-bit**: an aligned `int64_t` load on x86-64 is one
   instruction, but on 32-bit x86/ARM the compiler emits two 32-bit loads — a writer
   landing between them leaves the reader with a value that never existed whole.
   Verdict: a data race, therefore UB on both platforms; "benign" bets on compiler
   kindness, not a machine guarantee.

3. **Mechanism 1 — spurious wakeup**: POSIX permits `pthread_cond_wait` to return with
   no signal, so implementations (glibc, over `futex`) stay race-free. **Mechanism 2 —
   lost wakeup**: the signaler fired before the waiter slept — the return is
   legitimate, but the level changed and changed back. The construct: the predicate
   loop `while (queue_empty()) wait(...)` re-reads the state after every return, so
   forged and lost edges are both harmless.

4. **The first line reports a 12-byte regular file ("safe"); the second prints the
   contents of `/etc/passwd`** — the check answered the old file, the use followed the
   symlink. Fix: open with `O_NOFOLLOW` — it fails with `ELOOP` (errno 40) instead of
   silently reading the target: the follow-or-fail decision happens inside one kernel
   call.

5. **Circular wait** (condition 4): thread 1 runs `transfer(A,B)` (holds A, wants B),
   thread 2 runs `transfer_rev(A,B)` (holds B, wants A) — the ring closes, and
   no-preemption means nothing breaks it. The fix: a global lock *order* — always
   lock `min(A,B)` before `max(A,B)` in both functions. It removes exactly circular
   wait; that suffices, because with a total order the holds-wants graph can never
   contain a cycle.

6. **The high task was blocked, not runnable** — priority scheduling only ranks
   *runnable* threads. Once it blocks, the low-priority owner would run next — except
   the medium task, higher than the low owner, preempts the holder, so the mutex is
   never released. The stall is **unbounded**: its length is set by the medium task's
   workload, unrelated to the lock. Priority inheritance — the low owner inherits the
   high waiter's priority — bounds it to the low task's critical section.

7. **Livelock.** Deadlock threads are *blocked* (those cores idle); starvation means
   one thread starves while others progress (uneven CPU). All cores at 100% with zero
   progress is threads *running* — retry/backoff loops colliding; `gdb -p` shows all
   stacks in retry loops (around `try_lock` + backoff), none asleep in
   `__pthread_mutex_lock`.

8. **Store buffers publish the pointer before the constructor's writes become
   visible.** SB shows a thread's writes can reach another core out of program order.
   In the broken double check, the constructor's field stores and the pointer store are
   ordinary writes: the pointer can drain before the fields, or the reader can see the
   fields after the pointer — so the unlocked check reads stale or zero fields. No
   single address was incoherent, only the order *across* addresses, SB's exact
   lesson; the fix is an acquire/release edge (`std::atomic`), not "check more often."

</details>
