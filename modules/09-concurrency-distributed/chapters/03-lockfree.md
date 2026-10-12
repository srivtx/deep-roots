# Chapter 03 — Lock-free

> **The 30-second version.** Lock-free replaces "one thread owns the
> critical section" with "anyone may attempt; one attempt atomically
> wins; losers retry." The primitive is compare-and-swap, the idiom is
> the CAS loop, the payoff is progress: no lock-holder can be preempted
> and stall everyone. But CAS compares *addresses*, not identity — once
> the allocator reuses an address, "same pointer" is a lie (ABA). And
> even with ABA fixed, a thread can be *reading* a node another thread
> already unlinked — so THE central problem is not ordering (chapter 01
> solved that) but **memory reclamation**: when is it safe to free?

## 1. Why lock-free: three guarantees, three bills

The vocabulary is a ladder. **Obstruction-free**: a thread running solo
makes progress. **Lock-free**: *some* thread completes in bounded steps —
the system always progresses. **Wait-free**: *every* thread completes in
bounded steps. The Treiber stack below is lock-free, not wait-free: a
specific thread can fail its CAS unboundedly while others win.

What the rung buys:

- **No lock-holder preemption cliff.** Chapter 02's poison case: 3
  threads on 2 cores, the holder preempted mid-critical-section, every
  waiter blocked for a timeslice (~ms). A preempted lock-free thread just
  has a half-finished CAS attempt — a failed attempt blocks nobody.
- **Priority-inversion immunity.** A lock has an owner, and an owner can
  be low-priority (Mars Pathfinder, chapter 02's PI futex). A CAS attempt
  has no owner: nothing to invert, no inheritance protocol to enable.
- **Signal safety.** A mutex in a signal handler deadlocks if the
  interrupted thread holds it (async-signal-unsafe). A CAS loop on one
  word is handler-callable; the kernel uses lock-free paths exactly
  where sleeping is illegal (NMI, interrupt context).

The three bills: **complexity** (sections 2–4); **retries** — contention
doesn't queue, it wastes work: N-1 threads fail per winner, and the mutex
wins (section 5's number); and **reclamation** — the next two sections.

## 2. The CAS loop, and the bug it was born with

Compare-and-swap, `CAS(addr, expected, desired)`: atomically, if `*addr ==
expected`, store `desired` and report success; else fail. On x86-64: one
instruction, `lock cmpxchg` (~15–20 cycles, Skylake-class). Every
lock-free structure is one idiom — **read** the word, **compute** the new
world as if it still held, **CAS** to publish, **retry** with the value
the failed CAS refreshed. Applied to a stack (Treiber, 1986): `push(n)`
sets `n->next = head` and CASes head to `n`; `pop()` reads head `h`, reads
`h->next` `n`, CASes head to `n`. Full code in the lab.

Now derive the bug. The CAS guard asks "is head still *equal* to `h`?" —
an *address* comparison, and the world may change as long as head's
address returns to `h`: the allocator frees a node and hands its address
to a new allocation (glibc's tcache is LIFO per size class, module 01
ch19), or the same node object is popped and pushed again:

```text
ABA, step by step (the lab's "break" mode; stack A -> B -> C):

 step   T1 (victim)                  T2 (attacker)         head     pool
 -----  ---------------------------  --------------------  -------  ------
   1    h = A ; n = h->next = B                             A->B->C  {}
        [flag-forced "preemption" here]
   2                                 pop: CAS(A,B); free A  B->C     {A}
   3                                 pop: CAS(B,C); free B  C        {A,B}
   4                                 push: pool hands out  N4->C     {B}
                                     A's slot -> NEW node N4
   5    CAS(head, A, B)                                    B (!)     {B}
        head == A?  N4 IS at A's address -> "yes":
        installs the stale, retired B
   6    node-5 reuses B's slot; its      head -> node-5 -> node-5 -> ...
        next = head = itself             node-3 (C) unreachable
```

The deeper point: **pointer equality is not identity.** Even in a GC
language, the same-object flavor (pop A, pop B, push A) still passes the
guard — B's payload comes back twice. "ABA" names the sequence head
observes: A, (not A), A.

Three fixes:

- **Tagged pointers** (generation counters): compare {address,
  generation} instead of the address; every successful CAS bumps the
  generation, so a returning address fails the guard. Costs a wider
  atomic: 16-byte `cmpxchg16b` (x86-64, `-mcx16` + libatomic), or the
  lab's 48-bit pointer + 16-bit generation in one word.
- **LL/SC** (load-linked/store-conditional): the linked load arms a
  monitor; the conditional store fails if *any* write hit the address
  since, not merely if the value returned — ABA-free by construction
  (ARM64 `ldxr`/`stxr`). But pairs fail spuriously, and ARMv8.1's LSE
  atomics reintroduced real CAS: porting x86 lock-free code to ARM is
  where ABA bugs wake up.
- **DCAS** (CAS over two locations) never went mainstream;
  `cmpxchg16b` is its 16-byte single-location descendant.

The tag fixes the guard, not line 1 of the loop: `h->next` dereferences
`h` between load and CAS — if another thread freed `h` in that window,
the read itself is a use-after-free, tag or no tag. Identity and
lifetime are separate problems; the second is bigger.

## 3. THE problem: when can you free a node readers might hold?

Locked code never asks this: exclusion means nobody is inside when you
unlink and free. Lock-free code removed exclusion, so the *loser* of a
CAS was legitimately reading the nodes it lost on — free them anyway and
every section-2 algorithm is a use-after-free generator. The three
industrial answers share one shape: *defer* the free until provably
nobody holds the node.

**Hazard pointers** (Michael, 2004). Each thread owns a few published
slots (1–2 typical): pointers saying "I am reading this node." To protect
`p`: publish `haz = p`, then *validate* — re-read head; it must still
equal `p` (publishing after a removal is the racy window; validation
closes it). A remover CASes the node out and appends it to a per-thread
**retire list** instead of freeing: a **scan** reads every thread's
slots — O(N_threads × K) loads — frees each retired node in no slot,
defers the rest to the next scan.

**Epochs.** A global epoch counter advances as writers retire; a thread
entering a read section publishes the global epoch it *observes*; a node
retired at epoch R is freed when `min_observed > R` over all threads —
everyone has since entered a later read section, so no live reader
started before the retirement. One atomic load+store per critical
section, the free decision O(N_threads) epoch reads. The bill: one
parked thread pins an entire epoch's retires — latency is governed by
the minimum, always.

**RCU** (read-copy-update), the Linux workhorse (thousands of call
sites): make readers publish **nothing**. `rcu_read_lock()` compiles to
no atomic, no barrier (at most a preemption-count bump). A writer copies
the structure, updates the copy, publishes the new pointer with one
release store, and retires the old version to be freed after a **grace
period** — proof every CPU passed a *quiescent point* (context switch,
idle, return to userspace), so no reader still holds the old pointer.
`synchronize_rcu()` blocks ~25–60 ms on small quiet systems (kernel 5.x,
order-of-magnitude; load and config swing it); `call_rcu()` turns the
wait into a callback.

Why RCU owns read-mostly workloads: readers write no shared memory, so
they generate **no coherence transaction at all** — read throughput is
flat in core count, the only zero-reader-cost scheme of the three. The
bills land on writers: ms reclamation latency, a copy per update, and
memory pinning — every retired version lives until its grace period
completes, so pending reclamations grow with write rate × grace period —
the "memory cost of RCU" you see as RSS growth during write spikes.

## 4. The push/pop asymmetry, and the line you share by accident

A pushed node has two parts: its *fields* and the *head pointer* that
announces them — chapter 01's message-passing litmus with a pointer for
the flag, so the edges are directional:

- **Push pays release.** The pusher writes the node's fields, *then*
  publishes via the head CAS; release forbids the field writes from
  sinking below the publication. With a relaxed CAS, a popper on weak
  hardware can acquire head == node while the fields are still invisible.
- **Pop pays acquire.** The popper loads head and immediately
  dereferences `h->next` — the load must synchronize with the pusher's
  release CAS, which happens exactly when it reads the value that CAS
  wrote (the read-from condition, chapter 01). The pop CAS itself is
  `acq_rel`: acquire for the unlink, release ordering the retirement
  against later scanners.

The compression: **release on push, acquire on pop — the edge lives with
the pointer, and each side pays only its direction.** `seq_cst` buys a
total order nothing here depends on (no Dekker-shaped handshake);
`relaxed` on both sides is a torn publication. On x86-64 both orderings
are free (plain `mov` — TSO); on ARM64 they are real — code "verified
on Intel" ships ordering bugs to ARM.

False sharing, revisited with a CAS budget. Per-thread state is the
point of lock-free design — hazard slots, counters, retire lists — and
two threads' atomics one line apart are one coherence domain short of
one variable. A `fetch_add` needs the line Exclusive/Modified (chapter
20): shared means invalidate + transfer per op, ~40–100 ns, versus ~5 ns
local (5.1 ns measured, this box). The fix is 56 bytes of padding to the
64-byte line — why the kernel's `per_cpu` data gives each CPU its own
line. (This 2-vCPU VM shows ~20–26 ns flat across all layouts — the
vCPUs evidently share a physical core's caches; verify on bare metal.)

## 5. When NOT to go lock-free: the honest table

| Your situation | Use | Why |
|---|---|---|
| Read-mostly, hot reads | **RCU** | Zero reader cost, flat read scaling; ms reclamation and copy-on-write acceptable |
| Write-heavy, small, contended | **Mutex or shard** | The CAS retry storm wastes the losers — measured: mutex 1.5–1.9x faster (2 threads, this box) |
| Low contention, simple invariant | **Mutex** | 4 ns uncontended (chapter 02); a bug surface you can reason about |
| GC language (Java, Go, JS) | **Lock-free, cheaply** | The GC *is* your epoch system — reclamation is free; tags still needed for same-object ABA |
| Signal handlers, NMI, no-sleep paths | **Atomics / lock-free** | No owner to deadlock against; bounded work |
| Hard per-thread latency bound | **Rethink** | Wait-free is research-grade; shard or use channels (ch04) |

The meta-rule: lock-free is a *progress guarantee*, not a performance
tier. Reach for it for the guarantee or RCU's read scalability — never
"because locks are slow" (measure: `perf lock`, chapter 02).

## What insiders know

- **The asymmetry rule**: release on push, acquire on pop; the pop CAS
  is `acq_rel` (acquire for the unlink, release for the retirement);
  counters are relaxed. Nothing stack-shaped needs `seq_cst` — no
  Dekker handshake exists.
- **RCU's zero-reader-cost trick**: readers touch no shared writable
  line — no atomic, no barrier, nothing to invalidate. All bills are
  writer-side: `synchronize_rcu()` ~25–60 ms (expedited ~1–5 ms, for
  IPIs), plus memory pinned by pending reclamations (write rate ×
  grace period).
- **Hazard-pointer scan cost**: O(N_threads × K) slot reads, amortized —
  real libraries (folly, liburcu) scan per ~64–128 retirements, not per
  retire. Publication is one relaxed store; the *validation* re-read is
  what makes it sound.
- **False-sharing padding is a number, not a style**: 64-byte lines;
  cross-core transfer ~40–100 ns (chapter 02's rung two) vs ~5 ns local
  (5.1 ns measured, this box). `alignas(64)` hazard slots, counters,
  retire lists — or `per_cpu`, the kernel's systematic version.
- **When NOT to**: contended small structures (measured: Treiber 196–238
  ns vs mutex 121–134 ns per push+pop pair, 2 threads, this box); code
  untested on the weak hardware it targets; anything whose reclamation
  you haven't designed — that's the hard part.

## See it yourself

One program, three modes: `break` (plain pointer CAS + naive free — ABA
forced), `fix` (tagged word), `hazards` (retire lists + printed scan
decisions). Debian 13, g++ (Debian 14.2.0-19) 14.2.0, kernel 5.10.134,
2-vCPU Intel Xeon VM. The elided harness (~15 lines per mode) forces the
interleaving with release/acquire flags standing in for preemption:
victim reads `h`, `h->next`, parks; attacker pops A, pops B, pushes;
victim attempts the stale CAS.

```cpp
// treiber.cpp -- build: g++ -O2 -std=c++17 -pthread treiber.cpp -o treiber
// usage: ./treiber break | fix | hazards   (includes: atomic, thread,
// cstdio, cstring, vector)
struct Node { Node* next; long val; };

// 8-slot pool = malloc/free made observable: free returns the slot, reuse
// hands out the lowest free slot -- what a tcache bin does (M01 ch19).
static Node pool[8];  static bool used[8];
static Node* pool_take(long val) {
    for (int i = 0; i < 8; i++) if (!used[i])
        { used[i] = true; pool[i].val = val; pool[i].next = nullptr;
          return &pool[i]; }
    return nullptr;
}
static void pool_free(Node* n) { used[n - pool] = false; }

// ---- plain stack: CAS on the raw pointer, naive immediate free (BREAKABLE)
static std::atomic<Node*> phead{nullptr};
// push(n): n->next = head; CAS(head -> n, release); retry; prints its slot.
static Node* pop_plain() {
    for (;;) {
        Node* h = phead.load(std::memory_order_acquire);
        if (!h) return nullptr;
        Node* n = h->next;                 // plain read: can go stale
        if (phead.compare_exchange_weak(h, n,
              std::memory_order_acq_rel, std::memory_order_acquire)) {
            pool_free(h);                  // naive: freed immediately
            printf("  POP  node-%ld -> RETIRED, freed now\n", h->val);
            return h;
        }                                  // failed CAS refreshed h: retry
    }
}

// ---- tagged stack: 48-bit pointer + 16-bit generation in one word.
// x86-64 user VAs < 2^47, so bits 48-63 are spare. NOT portable to
// 52-bit VAs (ARM64 LVA, LA57) -- there use a 16-byte CAS instead.
static std::atomic<uint64_t> tw{0};
static Node* decode(uint64_t w) { return (Node*)(uintptr_t)(w & 0x0000FFFFFFFFFFFFULL); }
static uint64_t gen(uint64_t w) { return w >> 48; }
static uint64_t encode(Node* p, uint64_t t) { return (uint64_t)(uintptr_t)p | (t << 48); }
static Node* pop_tag(std::vector<Node*>* retire) {
    for (;;) {
        uint64_t w = tw.load(std::memory_order_acquire);
        Node* p = decode(w);
        if (!p) return nullptr;
        Node* n = p->next;                 // safe only if p cannot be freed yet
        if (tw.compare_exchange_weak(w, encode(n, gen(w) + 1),
              std::memory_order_acq_rel, std::memory_order_acquire)) {
            if (retire) { retire->push_back(p);
                printf("  POP  node-%ld -> RETIRED (deferred)\n", p->val); }
            return p;                      // rejected: w refreshed, retry
        }
    }
}

// ---- hazard pointers: 1 slot per thread, retire lists, scans that print
static Node* haz[2];                       // "I am reading this node"
static std::vector<Node*> retired[2];
static void scan_free(int tid, const char* when) {
    std::vector<Node*> keep;
    for (Node* r : retired[tid])
        if (haz[0] == r || haz[1] == r) {
            keep.push_back(r);
            printf("  SCAN(%s): node-%ld DEFERRED\n", when, r->val);
        } else {
            pool_free(r);
            printf("  SCAN(%s): node-%ld FREED\n", when, r->val);
        }
    retired[tid].swap(keep);
}
```

`fix` repeats the dance on the tagged word; `hazards` has a slow reader
publish `haz[0]` + validate while a drainer pops all 5 nodes into
`retired[1]` and scans twice, either side of the slot's release. Output:

```console
$ g++ -O2 -std=c++17 -pthread treiber.cpp -o treiber
$ ./treiber break
  POP  node-1 -> RETIRED, freed now
  POP  node-2 -> RETIRED, freed now
  PUSH node-4 at slot 0
T1: CAS(A -> B) SUCCEEDED: stale B installed as head
main: node-5 allocated at B's recycled address
  PUSH node-5 at slot 1
traversal: node-5 -> node-5 -> node-5 -> node-5 -> (self-loop; node-3 unreachable)
$ ./treiber fix
T2: pop -> node-1
T2: pop -> node-2
T2: re-push node-1 (same object, gen advanced)
T1: CAS REJECTED: expected gen 3, current gen 6 -> ABA defeated, retry
T1: retry pop -> node-1
$ ./treiber hazards
  POP  node-1 -> RETIRED (deferred)          ... nodes 2-5 likewise
  SCAN(first): node-1 DEFERRED
  SCAN(first): node-2 FREED                  ... nodes 3-5 likewise
  SCAN(final): node-1 FREED
```

What to notice: in `break`, A and B are freed, both addresses recycled,
and T1's CAS passes the *address* guard — the traversal self-loops,
node-3 lost. In `fix`, the guard fails on the *generation* (3 vs 6 —
T2's three CASes); the retry pops correctly. In `hazards`, 2 slot reads
decide 5 nodes' fate; the deferred node flips to FREED only after its
holder's release. Then delete `pool_free(h)` from `pop_plain` and re-run
`break`: ABA vanishes — and a leak appears. That edit *is* the
reclamation problem.

## How to use this knowledge

- **Diagnosing a rare lock-free crash**: head pointing into a freed
  chunk plus an allocator that reuses addresses is ABA until proven
  otherwise — and you can name both halves (identity: tag; lifetime:
  hazard/epoch/RCU) instead of "add more barriers."
- **Reading kernel code**: `rcu_read_lock` / `synchronize_rcu` /
  `call_rcu` now parse as the grace-period protocol — readers are free,
  the free is ms-late, and RSS growth during write spikes means pending
  reclamations, not a leak.
- **Performance triage**: before replacing a mutex, measure (`perf lock`,
  chapter 02) and consult section 5 — the mutex won here; if reads
  dominate, the answer is RCU, not a fancier CAS.

## Connections

- **Module 01 ch19** — allocator internals: tcache LIFO reuse turns
  "freed" into "same address again" — ABA's other half.
- **Module 01 ch20** — the MESI transactions every CAS bills you for,
  and the 64-byte line that makes padding necessary.
- **Module 09 ch01** — push/pop is the message-passing litmus with head
  as the flag; the read-from condition is why pop's acquire works.
- **Module 09 ch02** — the cost ladder this chapter's numbers are priced
  on; the PI futex, the inversion lock-free sidesteps.
- **Module 09 ch04** — ownership transfer over channels: reclamation
  becomes someone else's problem by construction.
- **Module 03 ch03** — the race family as a bug class; Pathfinder at
  mechanism level.
- **Module 12 ch01** — the GPU analog: a warp's atomics to one location
  serialize at the L2 — the retry storm at warp scale.

## Quiz

1. **Concept.** Under sustained contention, one specific thread's Treiber
   pop can fail unboundedly. Which guarantee does the stack provide, which
   stronger one exists, and what does the stronger one require?

2. **Concept.** What does an RCU reader *not* do that a hazard-pointer
   reader must — and why does that keep read throughput flat as cores are
   added? Name the two writer-side costs paying for it.

3. **Predict.** Stack A→B→C. T1 reads `h = A`, `n = B`, and stalls. T2 pops
   A, pops B, then pushes a *new* node the allocator places at A's
   address, next = C. T1 runs `CAS(head, A, B)`. What does the CAS do,
   what is head afterwards, and name two corruptions the traversal can
   exhibit.

4. **Predict.** Epoch scheme: global epoch 40, observed epochs {12, 40,
   40}. Node retired at epoch 11 — freed or deferred? And at 12? Which
   thread is the bottleneck, and what does it pin?

5. **Bug diagnosis.** A production lock-free stack crashes every few
   days. The core shows `head` pointing into a freed chunk; allocator logs
   show that chunk freed via tcache and re-served 200 ns later. Name the
   failure and give two independent fixes, one per half.

6. **Bug diagnosis.** A lock-free list pushes nodes with a *relaxed* CAS,
   fields written just before the push. Poppers rarely see zeroed fields
   on ARM64, never on x86-64. Which edge is missing, why does x86-64 hide
   the bug, and what ordering pair fixes it?

7. **Bug diagnosis.** A hazard-pointer service's RSS climbs during
   traffic spikes and drains in quiet periods; the retire lists grow.
   Diagnose and name two mitigations.

8. **Cross-module (ch02, module 03 ch03).** Mars Pathfinder inverted
   priorities because a lock has an *owner*. Why can a CAS attempt not be
   priority-inverted, and what failure replaces inversion for a
   low-priority thread under contention?

<details>
<summary>Answers</summary>

1. **Lock-free** — the system always progresses (some thread's CAS
   succeeds) while an individual thread can starve. **Wait-free** bounds
   *each* thread's steps, which requires algorithms with no retry loops
   at all (helping schemes, slot arrays) — costlier and rare.

2. **The RCU reader publishes nothing** — no slot store, no atomic, no
   barrier — so it writes no shared cache line and generates no
   coherence traffic: readers cannot slow each other at any core count.
   Writers pay the ms-scale grace period and memory pinned by pending
   reclamations, plus a copy per update.

3. **The CAS succeeds** — the guard compares addresses, and the new node
   occupies A's address. Head becomes the retired node B. Corruptions:
   (a) use-after-free — B's memory may serve another allocation, so
   `B->next` is arbitrary; (b) duplication — B's payload returns a
   second time; C may be lost (the lab's `break` run: self-loop).

4. Epoch 11: **freed** — `min_observed = 12 > 11`. Epoch 12:
   **deferred** — strict `>`, and thread 1 sits *at* 12, so a reader
   that started then might still hold the node. Thread 1 pins the whole
   epoch-12 retire set: reclamation waits on the slowest observer.

5. **ABA, the allocator-reuse flavor.** Identity half: a tagged pointer
   (48-bit pointer + 16-bit generation, or 16-byte `cmpxchg16b`) so the
   returning address fails the guard. Lifetime half: a reclamation
   scheme — hazard pointers, epochs, RCU — so a node a reader might
   touch cannot be freed or reused. Production needs both: the scheme
   protects the `h->next` dereference, the tag the guard.

6. Missing: the **release half of the push CAS**. The popper's acquire
   load of head then pairs with nothing — no read-from edge, no
   visibility of the field writes, so the node can read
   half-initialized. x86-64 hides it because TSO already orders stores;
   ARM64 does not. Fix: push CAS `release`, pop load `acquire`.

7. **Retire-list growth, not a leak**: deferred nodes accumulate while a
   hazard is held or between scans; pinned memory = retirement rate ×
   deferral time — spiking with traffic, draining when quiet. Mitigate
   by scanning more often (costs the O(N_threads) scan) or bounding the
   list with backpressure to writers.

8. A lock has an owner — the kernel boosted the low-priority holder via
   the PI futex's chain. A CAS attempt owns nothing: no holder to
   preempt, nothing to boost. The replacement failure is
   **starvation/livelock**: the low-priority thread's CAS attempts fail
   indefinitely while others win the same word — system-wide progress
   holds, per-thread does not.

</details>
