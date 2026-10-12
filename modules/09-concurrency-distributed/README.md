# Module 09 — Concurrency & distributed memory

> The hardware memory model (module 01 ch20) said cores disagree about memory.
> This module follows the disagreement upward: language memory models, locks,
> lock-free structures, message passing — and then off the machine entirely,
> where the network becomes the bus, consensus becomes the write barrier, and
> every distributed-systems concept lands as *the memory model, one scale up*.

**5 chapters · one final exam · prerequisite: module 01 chapter 20 (the hardware
memory model) is mandatory; chapter 18 (Erlang) helps for ch04.**

## How to study it

Chapter 01 derives the formal machinery (happens-before, the memory-ordering
menu); 02-03 are the single-machine practice (locks, then lock-free); 04 is
message passing as the ownership-transfer alternative; 05 crosses to distributed
systems and closes the loop back to memory. Every chapter has litmus tests —
programs whose output you must predict *before* reading the answer.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [Happens-before](chapters/01-happens-before.md) | From MESI to SC-for-DRF: the language memory models and their orderings. |
| 02 | [Locks](chapters/02-locks.md) | Spinlocks, futexes, mutex internals, deadlock discipline — costs in ns. |
| 03 | [Lock-free](chapters/03-lockfree.md) | CAS, ABA, hazard pointers, epochs, RCU — and memory reclamation as THE problem. |
| 04 | [Actors & channels](chapters/04-actors-channels.md) | Message passing as ownership transfer: queues, backpressure, Erlang & Go. |
| 05 | [Distributed memory](chapters/05-distributed-memory.md) | Lamport clocks, quorums, Raft, CRDTs, CAP/PACELC — the network as memory. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — happens-before.** The layering made explicit: hardware memory
models (module 01 ch20's MESI/store-buffer view) vs *language* memory models
(what the compiler+CPU together promise your code). The SC-for-DRF theorem in
plain terms: data-race-free programs observe sequentially consistent behavior —
*and what races actually buy you beyond that* (torn reads, invented stores,
out-of-thin-air values — the Java out-of-thin-air saga and why it's unfixable
in full generality; the "benign data race is a lie" argument with a compile-
transform example). Happens-before derived as a partial order: program order
within a thread, synchronizes-with edges across threads (release-store →
acquire-load, lock/unlock, thread create/join, message send/receive). The
memory-ordering menu (relaxed / acquire / release / acq_rel / seq_cst — C++,
Rust, Java analogs) as *edge manufacturers*: which edge each ordering creates,
the litmus tests (store buffering: both threads can see stale values under
relaxed; message passing: acquire/release fixes it — draw both with ASCII
interleavings). Fences as standalone edges (mfence semantics at the MESI level —
module 01 ch20 payoff). The insider toolset: TSan as a happens-before checker
(vector-clock sketch — module 03 ch05 connection), litmus-test suites (herd/
litmus7 culture). Lab: the store-buffering test in C++ with two orderings —
run each, observe both outcomes, predict before running.

**Chapter 02 — locks.** The cost ladder derived: uncontended atomic op (~5-20ns
— cache-line ownership transfer), contended (cache-line ping-pong, ~100ns+),
context switch (1-10µs — the cliff). Spinlocks: test-and-set (the TAS ping-pong
cost with 3 threads on 2 cores derived), ticket locks (fairness: the now-serving
counter — the bakery algorithm's hardware-accelerated descendant), queued locks
(MCS: spin on a *private* cacheline — the insight that solved spinlock
scalability). The futex (Linux): the word dance — userspace fast path (atomic
compare-and-swap on the lock word; the three states 0/1/2 = unlocked/locked/
locked-with-waiters), syscall slow path only when contended (FUTEX_WAIT/
FUTEX_WAKE, the kernel's wait queue), priority-inheritance futexes (the Mars
Pathfinder fix from module 03 ch03, now at the mechanism level). glibc mutex
interns mapped onto the futex protocol. Adaptive spinning. Deadlock discipline:
the Coffman conditions revisited as *engineering* (lock ordering hierarchies,
lockdep — how the kernel's runtime validator detects order invocations... order
violations), lock granularity's classic curve (too coarse → contention; too
fine → ordering complexity). The insider's tuning heuristics: measure before
restructuring (perf lock, /proc/lock_stat), prefer sharding to spin tuning.
Lab: pthread mutex timing microbenchmark (uncontended vs contended), a
deliberate deadlock + gdb thread apply all bt diagnosis.

**Chapter 03 — lock-free.** Why lock-free at all (priority inversion immunity,
no lock-holder preemption cliff, signal-safety — and its costs: complexity,
retries, and the reclamation problem). CAS loops (compare-and-swap idiom: read
→ compute → CAS → retry; the ABA problem *derived* from memory reuse: value
returned to A but object replaced — the classic lock-free stack deletion
failure; fixes: tagged pointers (generation counters), LL/SC, double-compare-
and-swap). THE central problem — memory reclamation: when can you free a node
readers might hold? Hazard pointers (per-thread slots publishing "I'm reading
this" — the O(N_threads) scan), epochs (global epoch counter + per-thread
observed epochs — free when min_observed > retirement epoch), RCU (the Linux
workhorse: readers pay *zero* overhead — no atomics, no barriers; writers defer
reclamation to a grace period = all readers pass a quiescent point; why RCU
scales to read-mostly workloads — and the memory cost of many pending
reclamations). Memory ordering in practice: the push/pop asymmetry (release on
push, acquire on pop — derive why the direction matters: the node's fields must
be visible when the pointer is). False sharing revisited in CAS context (64B
padding to keep per-thread counters on separate lines — module 01 ch20 payoff
with numbers). When NOT to go lock-free: the honest decision table. Lab: a
lock-free stack (Treiber) with the ABA failure demonstrated via forced
scheduling, then fixed with a tag; a hazard-pointer scan understood by
instrumenting it.

**Chapter 04 — actors & channels.** The core inversion: instead of sharing
memory + synchronization, transfer ownership over a queue (data has exactly one
owner at a time — module 01 ch10's Rust ownership, implemented as an
architecture). Queue internals: the MPMC queue design space (array ring buffers
vs linked lists, the two-lock queue of Michael-Scott lineage, why consumer
caches... the producer-consumer cacheline dance — head and tail on separate
lines), bounded vs unbounded (backpressure as *load control*: what unbounded
queues do to systems under stress — the OOM-by-queue pathology, and why the
best queue is one that says no). The Erlang model: per-process heaps (module 01
ch18 payoff: GC is per-actor → pauses are micro and local; mailboxes as the
only interface; let-it-crash as supervision architecture), the BEAM scheduler
preemption. Go channels: hchan internals (the lock + circular buffer + sudog
wait queues for blocked goroutines; unbuffered = synchronous rendezvous),
select as the multi-channel poll. Rust channels as typed ownership moves
(send consumes the value — the compile-time version of what Erlang gets by
discipline). Delivery semantics vocabulary: at-most-once vs at-least-once vs
"exactly-once" (derive why exactly-once delivery is impossible without
idempotence — the receiving side dedup; connect to ch05's distributed world).
Backpressure propagation (reactive streams as the disciplined answer). Lab:
an unbounded queue OOM reproduced in miniature; the same system with a bounded
queue + explicit overflow policy; Go: buffered vs unbuffered channel timing.

**Chapter 05 — distributed memory.** The break with single-machine assumptions:
partial failure (the other node exists *and* doesn't — you can't even tell
which), no shared bus, message latency ~ms (the tower extended by three orders
of magnitude — module 01 ch02's final extension). Lamport clocks (the
happens-before relation exported: a counter per node, max-on-receive — captures
*ordering*, not causality gaps) vs vector clocks (the full causal history:
N-vector of per-node counters, why "concurrent" is now decidable — and the O(N)
cost). Replication as the read/write set expansion: quorum arithmetic (R + W >
N derivation, the read-write overlap guarantee), leader-follower logs. Consensus
as the linearizable write: Raft in one page (terms, log entries, commit index —
the log IS the distributed memory; the majority quorum as the durability
barrier), two-phase commit and why it blocks (the coordinator death window —
consensus's superiority derived). CRDTs as commutative memory: G-Counter,
PN-Counter, OR-Set (add/remove wins semantics — the tombstone story), the
insight: reorder any two operations, converge to the same state = the strongest
availability you can buy; LWW (last-write-wins) as the pathology (clock skew
eats data). The consistency menu with the anomaly each level forbids:
linearizable > sequential > causal > read-your-writes > eventual (draw it as
a lattice), PACELC (the honest CAP: even without partitions you pay
consistency-or-latency). Jepsen as the distributed TSan (black-box anomaly
hunting — the insider's testing culture; the famous Jepsen post-mortems).
The unification: a linearizable register = sequentially consistent *single
location* — distributed systems is the memory model one scale up, and module
01 ch20 is where it started. Lab: a two-node vector-clock playground (concurrent
events detected); a Raft animation/diagram walkthrough of a leader failover.

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **Race diagnosis at any scale**: single litmus tests to Jepsen anomalies —
  one framework (happens-before) covers them all.
- **Lock-free literacy without the cargo cult**: you know when RCU/hazard/
  epochs are the answer and when a plain mutex was the answer all along.
- **System design fluency**: backpressure, quorums, CRDTs — the vocabulary
  behind every "should this be async/distributed/replicated" decision.
- **The full-circle view**: MESI → happens-before → consensus is one subject.
  You can now see it.
