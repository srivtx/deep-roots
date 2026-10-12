# Chapter 05 — Distributed memory

> **The 30-second version.** Take what modules 01–09 assumed — one memory,
> one bus, one fate — and put it on a network. Nodes fail *partially*: the
> other node exists and doesn't, and you can't tell which; a message costs
> ~1 ms, not ~1 ns. Causality survives — Lamport clocks export
> happens-before as a per-node counter, vector clocks make "concurrent"
> decidable at O(N) cost. Durability survives as quorums (R + W > N) and
> as consensus — in Raft the log *is* the distributed memory, the commit
> index the write barrier, the majority its durability. Availability
> survives as CRDTs: commuting operations that converge under
> any reorder.

## 1. The break: three assumptions die at once

A mutex on one machine (chapter 02) rests on three assumptions you never
state: **one fate** (lock, data, and waiter share a kernel), **one bus**
(MESI's agreed truth per cache line, ~100 ns answers), and **one clock**
("which write came first" was answerable). Distribute the state, and all
three die.

**Partial failure** is the cruelest: a silent peer could be crashed,
slow, or fine with your packet lost in transit — a timeout cannot
distinguish the three. The other node exists *and* doesn't, and you
can't even tell which. **Latency** kills the bus by three orders of
magnitude per step of module 01 chapter 02's tower:

```
 register ~0.4 ns | L1 ~1 ns | L2 ~4 ns | L3 ~15 ns | DRAM ~100 ns
   -> futex syscall 1-10 us (ch02)  -> one RPC hop, same LAN, ~0.5-1 ms
   -> cross-region RTT 30-80 ms (public cloud inter-region, 2024)
 every arrow = ~1000x -- and best-effort IP can also just LOSE things.
```

And the clock dies worst: NTP holds clocks within milliseconds while
correctness cares about "who saw what" — you need an ordering that never
consults the clock.

## 2. Exporting happens-before: Lamport clocks, then vector clocks

Chapter 01's happens-before imports almost unchanged: program order
within a node, **send → receive** across nodes. How do we *label*
events so the relation is computable? Lamport's 1978 answer: a counter
per node. Increment on every local event; stamp every message;
on receive, `c = max(c, stamp) + 1`. The theorem: `a → b` implies
`L(a) < L(b)` — one-way only. The scalar cannot say whether two
stamps are causally ordered or unrelated: Lamport clocks capture
ordering but are **blind to concurrency**.

**Vector clocks** (Mattern, Fidge, 1988–89) pay O(N) to close the gap:
each node carries an N-vector, bumps its own slot on every event, ships
the whole vector per message, and the receiver takes the componentwise
max. Compare vectors A and B: `A ≤ B` componentwise ⇒ A happened-before
B; `B ≤ A` ⇒ the reverse; **neither dominates ⇒ concurrent** — the same
"did these two race?" predicate TSan computes per access pair (module 03
ch05). The price: N counters per event and per message — 8 bytes per
slot, so 100 nodes pay 800 bytes of metadata per message, and comparison
is O(N). Production systems prune — and the pruning itself was a
bug farm.

## 3. Replication: the read/write set grows up, and R + W > N

Data on exactly one node dies with it, so replicate: N replicas turn
every logical write into a physical write-set, every read into a
read-set — chapter 01's owner-to-read/write-sets move, networked. How
many of each must succeed before you answer the client? Call them **W**
and **R**, the write and read quorums.

Pigeonhole: **any read quorum of R and write quorum of W share at least
R + W − N nodes.** The useful middle is **R + W > N**: the shared node
must hold the last committed write, so the reader can find it — picking
the newest version by *vector clock*, section 2. N = 3, R = W = 2 (the
Dynamo paper default): overlap 1; writes survive one dead replica, reads stay fresh with one
slow one. Cassandra's default `ONE`/`ONE` at RF = 3: R + W = 2 ≤ 3 —
**no overlap, stale reads legal** — traded for latency: the
consistency-latency exchange rate, one equation.

Leader-follower logs are the structured special case: one replica is
the entry point (chapter 04's single-owner principle, networked),
appending to a log followers apply in order.

## 4. Consensus: the linearizable write

Quorums replicate; they don't decide — two clients writing different
values to overlapping quorums, or a write a majority saw but nobody was
told "done", need one agreed sequence: **consensus**, the linearizable
write. Its cleanest expression is Raft.

**Raft in one page.** Time divides into **terms** (monotonic); each has
at most one leader, elected by majority vote with randomized timeouts
(paper: 10–500 ms; etcd 3.4+: 100 ms heartbeat, 1000 ms election). The
leader owns **the log**: entries, each stamped with the term that wrote
it. A client write appends an entry; the leader replicates it; once a
**majority** stores it, the leader advances the **commit index** — the
barrier below which the log is *the* truth, durable against any
minority crash — and only then acks. Failures compare logs: voters
refuse less up-to-date candidates; followers refuse entries
mismatching their prefix (**log matching**). The log is the distributed memory; the commit index
the write barrier; the majority its durability. Watch:

```
 5 nodes (S1..S5).  [t:i] = entry at index i, written in term t.  CI = commit index.

 t0  term 1, S1 leader, CI = 3:
     S1 [1:1][1:2][1:3][1:4][1:5]   <- entries 4,5 reached ONLY S2 (2/5)
     S2 [1:1][1:2][1:3][1:4][1:5]      -> not a majority -> NOT committed
     S3 [1:1][1:2][1:3]                -> no ack was ever sent for 4,5
     S4 [1:1][1:2][1:3]
     S5 [1:1][1:2][1:3]             <- all five: CI = 3

 t1  S1 CRASHES. Timers fire (etcd default: 1 s no heartbeat); S3
     stands for election, term 2.

 t2  Up-to-date check, S3's last entry (term 1, index 3) vs each voter's:
     S4, S5 same -> grant. S2's log is longer -> S2 refuses -- but 3/5
     is already a majority. S3 leads term 2. (Leaders don't need the
     longest log, just one a majority accepts as most up-to-date.)

 t3  Client writes x=7. S3 appends [2:4], replicates to 3/5, advances
     CI 3 -> 4, acks. [1:1..1:3] commit too, via log matching.

 t4  S2 (and a restarted S1) resync. AppendEntries at prevLogIndex=3:
     S2's [1:4] mismatches -> nextIndex backs up -> S2 TRUNCATES
     [1:4][1:5], takes the term-2 log. Uncommitted entries vanish --
     legal: never committed, never acked. THE COMMIT INDEX IS THE
     DURABILITY BARRIER.
```

**Why not two-phase commit?** A coordinator broadcasts PREPARE, everyone
locks and votes yes, it broadcasts COMMIT. The death window: the
coordinator dies *after* collecting the yes votes, and participants hold
locks in "prepared" — unable to commit or abort — until its successor
digs through its logs. Consensus's superiority is structural: **no
single node's memory decides the outcome** — the majority *is* the
decision, so any surviving quorum can finish it.

## 5. Commutative memory: CRDTs, and the LWW pathology

Consensus buys linearizability by blocking: without a quorum, writes
wait (CP). The other CAP fork accepts writes everywhere and reconciles
later — workable only if reconciliation is order-independent. That is a
**CRDT**: a replicated type whose operations commute, so reordering any
two converges to the same state — the strongest availability you can
buy.

- **G-Counter**: each node increments only its own slot; merge =
  componentwise max; value = sum. Increments commute because addition
  does — a vector clock *worn as data*.
- **PN-Counter**: two G-counters, P and N; value = sum(P) − sum(N).
- **OR-Set** (observed-remove set): `add(x)` stores a **unique tag** —
  (x, uuid); `remove(x)` writes a **tombstone** for every (x, tag) the
  remover could *see*; membership = tags without tombstones. The
  concurrent add/remove resolves by construction: A adds x (new tag)
  while B removes x (killing only tags B observed) — the new tag
  survives, **add-wins**, because the remove never saw the tag it would
  need to kill. The tombstone *is* the remove; an unobserved add isn't
  in its kill list.

The pathology is the shortcut people reach for instead of CRDT design:
**last-write-wins** on timestamps. With millisecond NTP drift and
out-of-order delivery, the earlier-arriving newer write is
silently discarded — LWW doesn't delay data, it *eats* it, no error,
forever; the Jepsen Cassandra and Elasticsearch findings were both LWW
underneath.

## 6. The menu, PACELC, Jepsen, and the circle closing

Everything above is one menu, ordered by the **anomaly** each level
forbids:

```
 strong +------------------------------------------------------+
  | LINEARIZABLE  forbids: stale read after ack.  Write   |
  | acked at real time t => every read after t sees it.   |
  | Real time consulted (Raft ReadIndex, etcd linearizable |
  | reads) = sequential consistency for a SINGLE location. |
  | ----------------------------------------------------- |
  | SEQUENTIAL    forbids: clients observing the same     |
  | writes in opposite orders. One total order explains   |
  | all; real time ignored. = SC, the interleaving        |
  | fiction a mutex gave you (module 01 ch20).            |
  | ----------------------------------------------------- |
  | CAUSAL        forbids: seeing the reply before the    |
  | post. Happens-before respected; concurrent writes may |
  | still order differently per client.                   |
  | ----------------------------------------------------- |
  | READ-YOUR-WRITES  forbids: your own write bouncing    |
  | back at you (sticky sessions fix it; others'          |
  | staleness is fine).                                   |
  v ----------------------------------------------------- |
 weak | EVENTUAL    forbids: disagreeing forever.          |
      | "Converge soon" -- by CRDT or by sync.             |
      +------------------------------------------------------+
```

Each step down is an anomaly you accept; each step up, real-time
ordering you pay for. **CAP**: under partition, pick availability or
consistency. **PACELC** (Abadi, 2012) adds the honest clause: *Else* —
no partition — you still pay **latency or consistency**: a linearizable
read must contact a majority, a `ONE` read doesn't. The partition didn't
create the price; it itemized it.

**Jepsen** (Kyle Kingsbury, 2013–) is the distributed TSan: run a
database under client load while a *nemesis* partitions nodes
(`iptables`), kills processes, skews clocks; then check the history
against a formal model — a litmus test, automated, at cluster scale.
Famous verdicts: 2013 Redis-with-Sentinel split-brained and lost 56% of
acknowledged writes in a majority partition; 2013 MongoDB
returned writes a deposed primary had acknowledged, then rolled back;
2014 Elasticsearch's LWW mappings silently dropped updates. Vendor
consistency claims are hypotheses; Jepsen is the experiment — Knossos
the formal-method checker.

And the circle closes. Chapter 01's store-buffering litmus is the
one-machine shape of "stale read after ack". A mutex gave you
sequential consistency for a critical section, Raft gives it for one
log; a **linearizable register is sequentially-consistent behavior for
a single location, with real time respected**. The store buffer became
the network, the fence became the quorum, MESI's one agreed truth per
line became consensus — the memory model, one scale up, where module 01
chapter 20 said it started.

## What insiders know

- **Vector clocks cost O(N) everywhere**: N slots per event, componentwise
  max per receive, 8 bytes per slot. Riak 1.x answered with pruning knobs
  (`small_vclock`/`big_vclock`, order of 50/100 entries) — and pruning
  bugs birthed "sibling explosions": one key, thousands of versions.
- **The R + W > N arithmetic is exact, not a vibe**: overlap =
  R + W − N ≥ 1. N = 3, R = W = 2 (Dynamo default) survives one dead
  replica; Cassandra's default `ONE`/`ONE` at RF = 3 gives overlap 0 —
  the number behind the marketing; *sloppy* quorums (hinted handoff) can
  violate even that.
- **LWW is a data-eater and it ships enabled**: a node 50 ms behind on
  clock discards every newer racing write, silently. NTP holds a healthy
  LAN to ~1–10 ms, cloud outliers hundreds of ms — "just use timestamps"
  is how acknowledged writes vanish.
- **Jepsen culture**: partition with `iptables`, skew clocks, kill
  primaries, model-check the observed history with Knossos. The 2013
  canon — Redis+Sentinel losing 56% of acked writes, MongoDB's
  acknowledged-then-rolled-back writes — made "Jepsen-verified" table
  stakes for CP claims.
- **Quorum overlap is why the guarantees stack**: the majority
  intersection that guarantees read freshness also guarantees Raft's
  election safety — two terms' majorities must share a node, and that
  voter saw the later term and refuses the stale leader. One pigeonhole
  argument, reused at every layer.

## See it yourself

**Lab A — a two-node vector-clock playground.** Two nodes, local
events, one message, then parallel events — the case where Lamport scalars and
vector clocks *disagree*:

```python
# vclock.py -- two-node vector-clock playground (CPython 3.11+)
def le(A, B): return all(a <= b for a, b in zip(A, B))   # componentwise <=

def relate(A, B):
    if le(A, B): return "A -> B (happens-before)"
    if le(B, A): return "B -> A (happens-before)"
    return "CONCURRENT (no causal order either way)"

class Node:
    def __init__(s, me): s.me, s.v, s.l = me, [0, 0], 0  # v: vector, l: Lamport
    def local(s):
        s.v[s.me] += 1; s.l += 1; return tuple(s.v), s.l
    def send(s):
        s.v[s.me] += 1; s.l += 1; return tuple(s.v), s.l
    def recv(s, mv, ml):                       # max-on-receive, both clocks
        s.v = [max(a, b) for a, b in zip(s.v, mv)]; s.v[s.me] += 1
        s.l = max(s.l, ml) + 1; return tuple(s.v), s.l

A, B = Node(0), Node(1)
a1 = A.local(); b1 = B.local()     # independent local events
mv, ml = A.send()                  # A -> B (one message)
b2 = B.recv(mv, ml)                # the causal edge, imported
a2 = A.local(); b3 = B.local()     # more parallel local events

for x, y in [(a1, b2), (a2, b3), (a1, b1)]:
    print(f"V={list(x[0])} L={x[1]}  vs  V={list(y[0])} L={y[1]}: {relate(x[0], y[0])}")
```

```text
$ python3 vclock.py
V=[1, 0] L=1  vs  V=[2, 2] L=3: A -> B (happens-before)
V=[3, 0] L=3  vs  V=[2, 3] L=4: CONCURRENT (no causal order either way)
V=[1, 0] L=1  vs  V=[0, 1] L=1: CONCURRENT (no causal order either way)
```

Notice line 2: the Lamport scalars say L=3 < L=4 — an *ordering that
does not exist*; the split vectors [3,0] vs [2,3] are what a race looks
like — the predicate TSan computes per access pair.

**Lab B — a Raft failover, traced by hand.** Work the section-4 diagram,
answering before the annotations: (1) Why is entry 5 not lost when S2
truncates it? *Never committed: 2/5 replicas,
no ack.* (2) Why do S4/S5 elect S3 when S2's log is longer? *Up-to-date
is (term, length); 3/5 is a majority without S2.* (3) What commits as
CI moves 3 → 4? *The prefix [1:1..1:3] too, via log matching.* (4) Where's the linearizability? *The ack for x=7 exists only once a
majority stores the entry, and any future leader's majority must
intersect it.* For a live one: a 3-member etcd (v3.5, docker) shows
term and commit index in `etcdctl endpoint status -w table` — `RAFT TERM` /
`RAFT INDEX`; kill the leader and watch the term tick up.

## How to use this knowledge

- **Debugging "impossible" data loss.** Writes vanish in a replicated
  store — run the tree: was the ack issued (quorum / commit index)? If
  yes, suspect rollback bugs (Jepsen's MongoDB); if no, suspect LWW and
  clock skew; if versions were involved, suspect dropped siblings.
- **Choosing consistency levels with arithmetic.** Know N, compute
  R + W − N, say the overlap out loud: `ONE`/`ONE` at RF = 3 is overlap
  0 — legal staleness; `QUORUM`/`QUORUM` is overlap 1 at double the
  latency. PACELC's "else", priced.
- **Designing for availability on purpose.** If your operations commute
  (counters, set adds, merges), a CRDT gives write availability *under
  partition* with guaranteed convergence — the one thing CAP says you
  can't otherwise buy.

## Connections

- **Module 01 chapter 20** — the origin: store buffers and SB's (0,0)
  are the one-machine stale-read-after-ack; MESI's per-line truth became
  consensus; this chapter is that one, three orders up.
- **Module 09 chapter 01** — happens-before is the relation both clocks
  encode; send → receive is Lamport's cross-node rule, verbatim.
- **Module 09 chapter 02** — the mutex bought one agreed order; Raft is
  the same purchase at cluster scale, election timeouts its contention
  backoff.
- **Module 09 chapter 03** — reclamation discipline returns as
  tombstone GC in OR-Sets: when may you forget a remove?
- **Module 09 chapter 04** — at-least-once delivery is why AppendEntries
  must tolerate duplicates; the mailbox is the lab's channel.
- **Module 11 chapters 04/05** — the durability cousins: the WAL is
  log-as-truth with fsync as the barrier; MVCC version chains are the
  single-machine siblings — versions kept, conflicts decided.
- **Module 05 chapter 03 / Module 06 chapter 03** — Binder and Mach/XPC,
  the platform IPC layers where the "network" is one copy away and
  partial failure reappears (dead proxies, dropped one-ways).

## Quiz

1. **Concept.** A request times out. Name the three peer states, why a
   longer timeout cannot distinguish them, and what this does to
   dead-or-alive as fact.

2. **Concept.** Lamport clocks guarantee `a → b ⟹ L(a) < L(b)`. Why is
   the converse's failure the reason vector clocks exist, and what
   becomes decidable at O(N) cost?

3. **Predict the guarantee.** A 5-node store runs R = 2, W = 2. Compute
   the read-write overlap, name the anomaly now legal, and give the
   minimal fix.

4. **Predict the output.** Before running the lab: A is at V = [3, 0],
   B at V = [2, 3]. What does `relate` print, and what would the Lamport
   scalars print?

5. **Bug diagnosis.** An LWW-resolved column loses ~0.1% of writes
   during a network blip; no errors logged, no failure detector fires.
   Give the exact mechanism — two writes, one clock.

6. **Bug diagnosis.** 2PC: the coordinator dies after all participants
   vote PREPARE-yes. What do participants hold, for how long, and what
   structural property of Raft closes the window?

7. **Bug diagnosis.** From the section-4 trace: the term-2 leader
   truncates S2's [1:4] and [1:5]; a teammate files "data loss in our
   Raft cluster." Refute it with the commit index and the ack rule.

8. **Cross-module (module 01 ch20 / 09 ch01).** Map store-buffering
   onto this chapter — the two private buffers, the missing fence, the
   (0,0) outcome — and name the rung that forbids it, and the weaker
   rung that permits it.

<details>
<summary>Answers</summary>

1. **Crashed, slow, or fine-with-lost-traffic.** A timeout measures *my*
   absence of an answer, conflating peer, network, and load state;
   lengthening it only trades detection latency against accuracy. One
   machine has a kernel that knows a thread is runnable or not;
   distributed, "alive" is a per-observer opinion — so systems *agree*
   on fates (terms, leases, quorums) rather than discover them.

2. **`L(a) < L(b)` does not imply `a → b`** — the scalar collapses
   "caused" and "merely later-numbered" into one order, hiding
   concurrency. Vector clocks make concurrency itself decidable: neither
   `A ≤ B` nor `B ≤ A` proves the third state, "concurrent" — the race
   predicate, at the cost of N counters per event.

3. **Overlap = 2 + 2 − 5 = −1, i.e. 0 shared nodes.** A read's R = 2
   nodes can both sit in the 3 nodes the write's W = 2 never touched —
   stale reads after completed writes are legal. Minimal fix: R = 3
   (or W = 4), overlap ≥ 1, at one extra node per read — PACELC's
   "else" priced.

4. **`CONCURRENT (no causal order either way)`** — A's slot 0 is 3 > 2
   while its slot 1 is 0 < 3, so neither vector dominates. The Lamport
   scalars print L = 3 < L = 4, which *looks* ordered — the trap:
   scalars manufacture order where none exists. (Lab line 3 is the other
   half: equal scalars, equally silent.)

5. **Two clients write the same key nearly simultaneously; the write
   with the genuinely newer value carries an older timestamp because
   its node's clock is behind.** LWW resolves to the "newer" timestamp
   and silently discards the actual later write — as a normal update,
   so no error or detector fires. Fix ladder: client timestamps →
   version vectors → CRDT value types.

6. **Participants hold locks and in-doubt prepared state until a new
   coordinator recovers the decision from the old one's log — blocked
   for failover time, potentially minutes.** Raft's structural
   difference: the decision lives in a majority of replicas — any surviving majority *is* the outcome, and the window
   closes by election.

7. **Nothing was lost, because nothing was promised.** Entries 4 and 5
   existed on 2 of 5 nodes — below the majority barrier — so CI never
   advanced past 3; no client was acked. Raft's contract: acked ⇒
   committed ⇒ on a majority ⇒ survives any failover. The real bug is
   client code treating "accepted by the old leader" as "durable".

8. **Each core's store buffer ↔ each node's private, unsynchronized
   state; the missing fence ↔ the missing quorum/consensus round; the
   (0,0) outcome ↔ a read that misses an acknowledged write.** The rung
   forbidding it is **linearizable** — real time respected: ack at t
   forces reads after t to see it. *Sequential* permits it: SC ignores
   real time, as the interleaving fiction let the stale interleaving
   stand.

</details>
