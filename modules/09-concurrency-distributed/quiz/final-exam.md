# Module 09 final exam — concurrency & distributed memory

Twelve questions, all five chapters. Closed book; the derivations are
the point. Scoring: 11+ = you own it; 8-10 = solid, revisit the
flagged chapters; <8 = the chapter quizzes are the path.

**Part I — happens-before and locks (chapters 01-02)**

1. **Concept.** State the SC-for-DRF theorem and its contrapositive:
   three race observations no interleaving of your source produces,
   and why "benign data race" is a lie.

2. **Predict (module 01 ch20).** Store buffering: T1 `x=1; r1=y`, T2
   `y=1; r2=x`. (a) Under `relaxed`, which `(r1,r2)` are legal? (b)
   Under release/acquire, is `(0,0)` legal — why doesn't the pair
   help? (c) What forbids it, at what x86-64 cost, and what does the
   instruction do to the store buffer?

3. **Predict (the ladder).** The lab measured 4.0 ns uncontended and
   ~383 ns contended (2 threads, 2 cores). (a) 3 threads on those
   cores: what happens, which rung? (b) The three rungs, with
   numbers. (c) Why does TAS make oversubscription worse where MCS
   does not?

4. **Derive (futex).** A holds the lock (word = 1); B and C fail the
   CAS and sleep in `FUTEX_WAIT` (word = 2). (a) Trace word values
   and syscalls from A's `unlock()` to B holding the lock — where is
   C, what does B write, and why? (b) Construct the lost wakeup the
   kernel's re-check prevents.

**Part II — lock-free and message passing (chapters 03-04)**

5. **Concept.** What does an RCU reader *not* do that a hazard-pointer
   reader must? Why does that keep read throughput flat as cores are
   added? Name the two writer-side bills and the third cost that shows
   as RSS growth in write spikes.

6. **Diagnosis.** A Treiber stack corrupts every few days: the core
   shows `head` in a chunk tcache-freed and re-served 200 ns later.
   Name the failure, derive the interleaving (A→B→C), and
   prescribe the two fixes, one per half.

7. **Predict.** Epoch reclamation: global 40, observed {17, 40, 40}.
   Retired at epochs 16 and 17 — freed or deferred? Which thread is
   the bottleneck, what does it pin, and what happens when it
   re-enters?

8. **Diagnosis.** A Java service OOMs at peak; the heap dump shows
   millions of tasks in `Executors.newFixedThreadPool(8)`'s queue.
   Trace the mechanism, why rejection never fires, and the replacement
   plus the rule for sizing its bound.

9. **Concept.** State the invariant that makes an ownership-transfer
   queue race-free by construction, and what must hold of message and
   send. Then derive the fork that kills "exactly-once delivery" on a
   lossy channel, and name the honest feature.

**Part III — the network as memory (chapter 05)**

10. **Predict.** A is at V = [3, 0] (Lamport 3); B at V = [2, 3]
    (Lamport 4). What does the vector comparison decide, what do the
    scalars appear to say, and why is that order fake? Which module 03
    tool computes the same predicate per access pair?

11. **Derive.** A 6-node store runs W = 4, R = 2. Compute the
    read-write overlap, name the anomaly, give the minimal read-side
    fix. Then show, on 5 nodes, why Raft's "an acked entry survives
    every failover" is the same pigeonhole arithmetic.

12. **Diagnosis (module 11 ch05).** An LWW-resolved replicated column
    loses ~0.1% of updates during blips — no errors, no detector
    trips. Give the two-write mechanism, why nothing fires, and the
    fix ladder. The DBA says single-node Postgres never loses these:
    MVCC aborts the later writer. Why can't the distributed store?

<details>
<summary>Answers</summary>

**Part I**

1. **Theorem:** if all conflicting ordinary accesses are
   happens-before ordered (program order ∪ synchronizes-with), every
   execution is indistinguishable from a sequential interleaving
   (ch01). A race is UB, so the two stacked reorderers (compiler, CPU)
   may produce what no interleaving does: **torn reads** (a 64-bit
   value as two 32-bit halves of *different* writes); **hoisted
   loads** (`while (!stop)` on a plain `bool` becomes one load — spin
   forever at `-O2`); **invented stores** (`if (flag) x=2; else x=1;`
   legally becomes `x=2; if (!flag) x=1;`, so `x==2` appears with
   `flag==0`). "Benign" fails: a racy read is still one the compiler
   may hoist, cache, or transform; UB does not grade on intent
   (ch01 §4).

2. (a) **All four, `(0,0)` included**: each core's store parks in its
   store buffer while its own next load (different address) passes
   it: TSO's one hole (module 01 ch20). (b) **Yes.** The edge
   materializes only when the acquire load *reads the value the
   release store wrote* (read-from condition, ch01); in `(0,0)` both
   loads read the initial 0 — no edge either way. (c) **`seq_cst`**:
   one total order S; whichever store is first in S, the other load
   follows its own store, hence the first, and reads 1. On x86-64
   release/acquire are plain `mov`; the seq_cst store is a locked
   `xchg`, ~10–20 ns, stalling the core until its store buffer drains
   — `mfence` semantics at the MESI level (module 01 ch20).

3. (a) **Worse — toward 1–10 µs per op**: one thread is always
   descheduled, eventually a lock *holder* mid-critical-section;
   waiters block in `FUTEX_WAIT` until its timeslice returns: the
   context-switch cliff (ch02). (b) Rung one: a locked op on a
   locally-owned line, ~5–20 ns. Rung two: the lock word's line
   ping-pongs between cores, ~40–100 ns per transfer; the ~383 ns adds
   a WAIT/WAKE pair (fair handoffs sleep). Rung three: syscall plus
   context switch, 1–10 µs. (c) TAS spinners hammer the lock word, each
   locked op an RFO invalidating the line (~100 ns per bounce); the
   preempted holder fights the storm even to release, and a full core
   burns atomics while the only progress-capable thread sleeps. MCS
   waiters spin on private flag lines; release is one store to the
   successor: O(1), no storm (ch02).

4. (a) A's unlock atomically exchanges 2 → 0, sees old 2, and issues
   `FUTEX_WAKE(word, 1)`: one syscall, waking exactly B; **C stays
   asleep**. B retries; CAS 0 → 1 succeeds, but C is still queued so
   B **writes 2, not 1** — the waiters hint must survive, or B's own
   unlock (old == 1) would skip the WAKE and C sleeps forever.
   B's unlock sees old 2, wakes C (ch02). (b) Without the re-check: B
   reads 2 and decides to sleep; A's unlock lands between B's check
   and B's enqueue — the WAKE finds nobody, then B sleeps on a word
   now 0: a wakeup lost forever, a cycle-free deadlock. The re-check
   inside the enqueue makes check-and-sleep atomic; mismatch returns
   `EAGAIN` (ch02).

**Part II**

5. The hazard reader must **publish** — a store into a shared
   per-thread slot plus a validation re-read. The RCU reader publishes
   nothing: `rcu_read_lock` is no atomic, no barrier, so readers touch
   no shared writable line, generate no coherence traffic, cannot
   invalidate each other: read throughput flat in core count, the only
   zero-reader-cost scheme (ch03).
   Writers pay the ms-scale grace period (`synchronize_rcu` ~25–60 ms)
   and a copy per update. The third cost is memory: retired versions
   live until their grace period completes (pinned bytes = write
   rate × grace period) — RSS growth during write spikes; it drains
   when quiet, not a leak (ch03).

6. **ABA, the allocator-reuse flavor** (ch03). T1 reads `h = A`,
   `n = B`, stalls. T2 pops A (CAS A→B, free A), pops B (CAS B→C,
   free B), then pushes N4 at A's address (free lists are LIFO per
   size class: tcache, module 01 ch19). T1's `CAS(head, A, B)`
   **succeeds**: the guard compares addresses, N4 *is* at A's address,
   and the retired B becomes head — `B->next` arbitrary, C
   unreachable, B's payload returns twice. Fixes, one per half:
   **identity**, a tagged pointer (48-bit pointer + 16-bit generation)
   so a returning address fails the guard; **lifetime**, hazard
   pointers, epochs, or RCU, so a node a reader might dereference
   cannot be freed. The tag guards the CAS; the scheme guards the
   `h->next` read (ch03).

7. Epoch 16: **freed** — min(observed) = 17 > 16, so every thread
   entered a later read section; no live reader started before it.
   Epoch 17: **deferred** — the comparison is strict, and thread 1
   sits *at* 17, a reader that may still hold the node.
   Thread 1 is the bottleneck: one parked thread pins the whole
   epoch-17 retire set; reclamation waits on the minimum, always
   (ch03). When it re-enters it publishes the current global (40),
   min_observed jumps, and everything retired at epoch ≤ 39 frees at
   once.

8. **Overload converted into memory.** A slow-consumer event makes
   arrivals exceed service; `newFixedThreadPool`'s queue is an
   unbounded `LinkedBlockingQueue` (capacity `Integer.MAX_VALUE`) —
   nobody can say no, and the queue grows until the OOM: a load
   problem converted into memory (ch04). Rejection never fires: an
   unbounded queue's `offer` always succeeds, so the handler is never
   reachable.
   Replacement: `ThreadPoolExecutor` with a bounded `BlockingQueue`
   plus an explicit overflow handler (abort / caller-runs / discard).
   Sizing: Little's law — capacity = arrival rate × tolerable wait;
   2,000 tasks/s at 100 ms is 200 slots; "a million, to be safe" is a
   million of latency plus a slower OOM (ch04).

9. **Invariant: exactly one owner per datum at every instant** —
   before the send only the producer can reach the message, after the
   receive only the consumer (ch04). It holds when the message has no
   sender-reachable aliases (Erlang: copy plus immutability; Rust:
   moves, module 01 ch10; a sent Go pointer re-creates sharing) and
   the send manufactures the send→receive synchronizes-with edge, so
   the receiver sees every pre-send write (ch01). The fork: delivery's
   only proof is a reply on the *same lossy channel*. Ack arrives: one
   delivery. Ack lost: "delivered, ack
   lost" is indistinguishable from "never delivered" — retry risks a
   duplicate, not retrying loses it. No third option (two-generals).
   The honest feature is **exactly-once effect**: at-least-once plus
   receiving-side dedup plus idempotent handlers — Kafka's
   "exactly-once" exactly (ch04).

**Part III**

10. **Concurrent.** Neither vector dominates (A's slot 0 is 3 > 2,
    slot 1 is 0 < 3), so no causal order either way (ch05). The
    scalars print 3 < 4, which *looks* happened-before; but Lamport
    clocks guarantee only a → b ⟹ L(a) < L(b), never the converse — the
    scalar collapses "caused" with "later-numbered,"
    manufacturing an order that doesn't exist. The vector predicate
    (neither A ≤ B nor B ≤ A) is the race predicate TSan computes per
    access pair (module 03 ch05). The price: O(N) — N slots per event
    and message, 8 bytes each (ch05).

11. **Overlap = R + W − N = 2 + 4 − 6 = 0**: a read quorum of 2 can
    sit exactly in the 2 nodes the write's 4 never touched — **stale
    reads after completed writes are legal** (ch05). Minimal read-side
    fix: R = 3 → overlap 3 + 4 − 6 = 1, at one extra node per read:
    PACELC's "else," priced. Raft on 5 nodes: a majority is 3; any two
    majorities overlap 3 + 3 − 5 = 1 — the same pigeonhole. An
    acked entry lives on a majority; the winner took a majority of
    up-to-date voters, the majorities intersect, and log matching
    forces the new leader's log to match that voter's prefix —
    every future leader carries every acked entry (ch05 §4).

12. **Two writes, one clock behind.** Two clients write the same key
    simultaneously; the newer value is stamped by a node whose clock
    lags (NTP drift: ~1–10 ms on a healthy LAN, hundreds for cloud
    outliers). LWW resolves to the *later timestamp* — the older
    value's stamp — and silently discards the newer write as a normal
    update: no error, no detector, data gone forever — LWW doesn't
    delay data, it eats it (Jepsen's Cassandra verdicts) (ch05). Fix
    ladder: client timestamps → version vectors (detect the
    concurrency, don't fake an order) → commuting CRDTs.
    The MVCC contrast (module 11 ch05): Postgres keeps both row
    versions, and one serialization authority (snapshot rules plus the
    commit-time conflict check) decides; the later writer aborts,
    nothing lost. The distributed store has no such point: disjoint
    quorums commit both, nobody left to abort. The decision must
    live in the merge — commutative by design (a CRDT: any reorder
    converges) or lossy (LWW: one survives).

</details>
