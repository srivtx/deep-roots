# Chapter 04 — Actors & channels

> **The 30-second version.** Shared memory needs synchronization because N
> threads can touch the same bytes. Third move: stop sharing — put a queue
> between the threads and *transfer ownership* of every message; data with
> exactly one owner cannot race, by construction (module 01 ch10's Rust
> ownership, promoted to architecture). Then the queue itself is the
> subject: rings, two locks, cache lines, bounded vs unbounded
> (backpressure is *load control* — the best queue says no), Erlang's
> per-process heaps, Go's channels, Rust's moves, and delivery semantics —
> where "exactly-once delivery" is impossible.

## 1. The inversion: don't share — hand over

Mutexes (chapter 02) serialize shared memory, lock-free structures
(chapter 03) tolerate it, the memory model (chapter 01) defines what it
means. Message passing attacks the premise: with exactly one owner per
datum at every instant, "two threads write the same location" is not a
bug you prevent — it is a statement the architecture cannot express. The
queue is the escrow agent:

- The producer builds the message freely — nobody else can reach it, so
  construction needs no ordering discipline.
- `send` transfers ownership and manufactures a happens-before edge:
  send → receive is one of chapter 01's synchronizes-with edges, so the
  receiver sees everything done before the send.
- The consumer now owns the message: mutates it freely, drops or forwards
  it.

Erlang upholds single-ownership by discipline plus immutability, Go by
convention, Rust at compile time (section 5). Shared memory bought safety
with synchronization *cost*; this buys it with *latency and copying* — a
deliberate trade.

## 2. Inside the queue: the MPMC design space

A multiple-producer, multiple-consumer (MPMC) queue has two axes.

**Storage.** An *array ring buffer*: one contiguous allocation of capacity
C (a power of two, so `index &= C-1` replaces division), two indices —
head where consumers read, tail where producers write: cache-friendly,
naturally bounded. A *linked list*: growable, but a malloc per message,
pointer-chasing reads, and chapter 03's reclamation problem if lock-free.
Production queues are rings; the linked list survives inside "unbounded"
abstractions — a hint, not a compliment.

**Coordination.** The classic blocking answer is the two-lock queue
(Michael & Scott, PODC 1996): a tail lock for producers, a head lock for
consumers — each side serializes with itself, but the two groups *never
wait on each other*. Java's `LinkedBlockingQueue` is literally this design
(`putLock`/`takeLock`); `ArrayBlockingQueue` uses one `ReentrantLock` for
both ends and pays when both are busy. Go's channel: a single mutex
(section 5).

```
        producers P1..Pk                        consumers C1..Cm
           |     |     |                           ^     ^     ^
           v     v     v  (lock + advance TAIL)    |     |     |  (lock + advance HEAD)

     +----------------------+          +----------------------+
     |  tail (8 B)          |          |  head (8 B)          |
     |  + 56 B padding      |          |  + 56 B padding      |
     +----------------------+          +----------------------+
      CACHELINE A (64 B, x86-64):       CACHELINE B (64 B, x86-64):
      bounced ONLY among producers      bounced ONLY among consumers

           |                                                     ^
           v  writes land at the tail                            |  reads leave at the head
     +-----+-----------------------------------------------------+-----+
     | free |  A  |  B  |  C  |  D  |  E  | free | free | free  | ... |
     +-----+-----------------------------------------------------+-----+
               ^                                              ^
               head: next slot to READ                        tail: next slot to WRITE
               (A is the oldest message)                      (just past newest, E)
```

**Why the padding.** A cache line is 64 B on x86-64 (128 B on Apple
M-series, 2023+). If `head` and `tail` — 8 B each — share one line, every
producer's `tail` update invalidates every consumer's cached `head`, and
vice versa: each operation pays a ~100 ns inter-core transfer (module 01
ch20) the locks never charged you for. Padded, producers ping-pong line A
while consumers ping-pong line B — the producer-consumer cacheline dance,
sides never touching. Chapter 03's false sharing, now the default state
rather than a bug you hunt.

## 3. Bounded vs unbounded: backpressure is load control

A producer arrives and the queue is full. Three designs:

1. **Unbounded** — grow; nobody is told "no".
2. **Bounded, block** — the producer waits: *backpressure*.
3. **Bounded, refuse** — drop or raise: *load shedding*, visible and countable.

Unbounded looks friendly and is a trap: under overload, arrivals exceed
service (slow consumer: contention, a GC pause, a sick disk) and the
queue grows without bound — a *load* problem converted into a *memory*
problem. This is OOM-by-queue, the standard 3 a.m. incident: the service
dies holding gigabytes of *somebody else's work*, and the OOM killer's
victim (picked by `oom_score` — biggest RSS, often the neighboring
database) is frequently not the service that queued it.

So: **a queue is load control, and the best queue says no.** Bounded plus
an explicit policy turns overload into waiting that propagates upstream
(backpressure — the source slows) or into visible refusals (which routers
act on). Unbounded turns the same overload into silent, delayed death. And
capacity is a latency commitment — Little's law, depth = arrival rate ×
wait: 2,000 msg/s buffered 100 ms is 200 messages. A "big buffer for
safety" is a big latency plus a slower OOM.

## 4. Erlang: one heap, one mailbox, one crash

Erlang made the inversion an operating system. A *process* (actor) is a
private heap, a mailbox, and code. Initial heap: 233 words ≈ 1.9 KB
(Erlang/OTP 26, 64-bit). No shared heap: messages are **copied** into the
receiver's heap (one exception: binaries over 64 B live on a shared
refcounted heap and pass by reference).

The payoff is module 01 ch18's: **garbage collection is per actor** —
each process runs its own copying collector (module 01 ch12's algorithm,
minus the shared heap), touching only that actor's heap: pauses are
microseconds and *local*. A million actors, each GC a private micro-event,
never a global stop-the-world; the copy on send is that isolation's price,
paid on purpose.

The mailbox is the *only* interface: send is asynchronous; `receive`
matches selectively from the front (O(depth) per match). An actor that
never matches its own messages accumulates an unbounded mailbox — section
3's pathology in Erlang clothing; the production metric is
`process_info(Pid, message_queue_len)`.

**Let-it-crash** completes the design: an actor's state is private and
rebuildable, so the failure policy is "crash the actor; the supervisor
restarts it from clean inputs" — a bug corrupts one actor, never the VM.
And the BEAM scheduler *preempts*: a process runs 4,000 reductions
(1 reduction ≈ 1 function call; OTP 26 default) then yields — a
busy-looping actor cannot starve the supervisor that would restart it.

## 5. Go and Rust: the discipline, in the language

**Go.** A channel is one heap object, `hchan` (runtime/chan.go, Go 1.22):

```
 hchan  (one mutex guards the whole thing):
 +-------+--------+---------+----------------------------------+
 | lock  | qcount | buf  -->| ring: dataqsiz * elemsize bytes |
 +-------+--------+---------+----------------------------------+
   closed, elemtype, elemsize    sendx/recvx (ring indices)
                                 sendq/recvq: FIFO wait lists of *sudog*
```

`make(chan T, n)` allocates the ring as one flat array of `n * sizeof(T)`;
pointer-free element types get hchan + ring fused into one allocation.
Fast path, buffered, space free: lock, copy to `buf + sendx*elemsize`,
advance the index (wrapping at `dataqsiz`), unlock — tens of ns. Slow
path: the goroutine parks, its **sudog** — the runtime's "g is waiting
here for this element" ticket — joins `sendq`/`recvq`; the other side
copies and calls `goready`.

For an **unbuffered** channel (`make(chan T)`: `dataqsiz` 0, `buf` nil) the
buffer never exists — a send with no waiting receiver parks the sender, and
the eventual receiver copies the value *directly from the sender's stack*:
a rendezvous, not a container — which is why an unbuffered send is a
synchronization event, at a park/wake (hundreds of ns) per message.

`select` is the multi-channel poll: `runtime.selectgo` shuffles the case
order pseudo-randomly (starvation avoidance: "which case wins when both
are ready" is deliberately *not* the source order), locks all channels,
parks the goroutine on every case at once; first ready wins. The Go trap
that keeps TSan (module 03 ch05) employed: channels transfer *values*, and
pointer values alias — send a map, keep using it sender-side, and you have
re-created shared memory.

**Rust.** `Sender<T>::send(t)` takes `t` *by value*; touch `t` afterwards
and the compiler rejects the program (`error[E0382]: use of moved value`).
Module 01 ch10's ownership rule, enforced at the queue: after the
transfer, only the receiver can touch the message. Crossbeam makes the
bounded decision an API choice — `bounded(n)` vs `unbounded()` —
recommending bounded under load (section 3).

## 6. What the queue promises: delivery semantics

- **At-most-once**: send, never retry. Loss possible, duplication impossible.
- **At-least-once**: retry until acked. No loss, but duplicates — the ack
  can be lost after delivery succeeded.
- **"Exactly-once delivery"**: derive the impossibility. The sender's
  only proof of delivery is a reply over the *same lossy channel*. If that
  reply is lost: retry (duplicate) or not (loss). No third option — the
  two-generals argument in one paragraph.

What *is* achievable is **exactly-once effect**: at-least-once delivery
plus a deduping receiver (idempotency keys, sequence windows) and
idempotent handlers. Kafka's "exactly-once" is exactly this: an
idempotent producer (PID + per-partition sequence numbers) plus
transactions — dedup and atomic commit, not delivery magic. Chapter 05
rebuilds this at network scale, where the same argument dictates
consensus; the fix is the same: make redelivery harmless.

**Backpressure must propagate.** Blocking sends on a bounded queue give
backpressure between two stages; in a pipeline, every boundary needs it or
the first unbounded buffer in the chain becomes the one that OOMs.
Reactive streams (spec 2014–15; `java.util.concurrent.Flow` since Java 9)
make demand explicit: `Subscriber.request(n)` is credit the consumer
grants upstream, wired end to end by Akka Streams / Project Reactor.
RabbitMQ's version: a node crossing its memory high watermark (a
configurable fraction of RAM, ~0.5–0.6 default, version-dependent)
*blocks publishers* rather than die. Always: the queue says no, the no
travels upstream, the source slows. That is load control.

## What insiders know

- **OOM-by-queue lives in the defaults.** CPython's `queue.Queue()` is
  unbounded unless you pass `maxsize`; Java's
  `Executors.newFixedThreadPool` backs onto a `LinkedBlockingQueue` of
  capacity `Integer.MAX_VALUE` — why many codebases ban the `Executors`
  factories for `ThreadPoolExecutor` with a bounded queue plus
  `RejectedExecutionHandler`. Go is the counterexample: channels are
  always bounded — capacity is part of the type; no unbounded channel in
  the stdlib, by decision, not omission.
- **`hchan` is one struct + one mutex** (Go 1.22): `qcount`, `dataqsiz`,
  `buf` ring pointer, `sendx`/`recvx`, `elemtype`, two `waitq` lists of
  `sudog`s. Unbuffered sends copy sender-stack → receiver-stack directly —
  the value never lives in the channel. `select`'s pseudo-random case
  choice is in the Go spec.
- **Erlang's GC story has one shared exception**: per-process heaps (233
  initial words, OTP 26, 64-bit) make pauses micro and local, but binaries
  > 64 B live on a shared refcounted heap, and mailboxes are unbounded —
  `message_queue_len` is the number Erlang ops people alert on.
- **"Exactly-once" in vendor material means dedup, not delivery.** The
  delivery claim is impossible (the ack-loss fork); Kafka's exactly-once =
  PID + per-partition sequence numbers + transactions = receiving-side
  idempotence, made cheaper. Interview trap: the follow-up to "we have
  exactly-once" is always "what dedups on the receiving side?"
- **Backpressure is load control at the boundary**: Reactive Streams'
  `request(n)` credit and RabbitMQ's publisher-blocking watermark are the
  same decision — refuse, propagate, slow the source. One hidden unbounded
  buffer in a pipeline is an OOM scheduled for peak hour.
- **The two-lock queue is a named artifact** (Michael & Scott, PODC 1996):
  `LinkedBlockingQueue` is its blocking form, the CAS Michael-Scott queue
  (chapter 03) its lock-free one. Head and tail get separate 64 B lines
  (128 B on Apple M-series) or the dance becomes a fight.

## See it yourself

Three labs on Linux (CPython 3.12.15 and Go 1.22-class toolchains; the
shapes reproduce, your nanoseconds won't).

**Lab 1 — the unbounded queue OOM, in miniature.** Fast producer, slow
consumer, 512 MB address-space cap so the death is cheap:

```python
# oom.py — an unbounded queue under overload, in miniature
import queue, threading, time, resource

resource.setrlimit(resource.RLIMIT_AS, (512_000_000, 512_000_000))
q = queue.Queue()                      # unbounded: nobody can say "no"

def producer():
    n = 0
    try:
        while True:
            q.put(b"x" * (1 << 20))    # 1 MiB message
            n += 1
            if n % 25 == 0:
                print(f"put={n:>5}  queued={q.qsize():>4}  in-queue={q.qsize():>3} MiB")
    except MemoryError:
        print(f"MemoryError: put={n}  queued={q.qsize()}  ~{q.qsize()} MiB of unserved work")

threading.Thread(target=producer, daemon=True).start()
while True:                            # the slow consumer
    q.get()
    time.sleep(0.01)                   # ~100 msg/s against thousands produced
```

```console
$ python3 oom.py
put=   25  queued=   24  in-queue=  24 MiB
put=   50  queued=   48  in-queue=  48 MiB
...
put=  450  queued=  437  in-queue=437 MiB
MemoryError: put=453  queued=440  ~440 MiB of unserved work
```

Growth is linear, death takes about a minute, and the memory at death
is 100% *other people's work*. Without `RLIMIT_AS`, the kernel OOM killer
ends it instead (see `dmesg`).

**Lab 2 — the same flood, a queue that says no.** Same cap and consumer;
only `maxsize=64` plus an explicit overflow policy (drop):

```python
# bounded.py — same overload, same 512 MB cap, but the queue refuses
import queue, threading, time, resource

resource.setrlimit(resource.RLIMIT_AS, (512_000_000, 512_000_000))
q = queue.Queue(maxsize=64)            # 64 x 1 MiB = 64 MiB ceiling
dropped, t0 = 0, time.time()

def producer():
    global dropped
    while time.time() - t0 < 10:
        try:
            q.put_nowait(b"x" * (1 << 20))   # overflow policy: DROP
        except queue.Full:
            dropped += 1

def consumer():
    served = 0
    while time.time() - t0 < 10:
        try:
            q.get(timeout=0.5); served += 1
        except queue.Empty:
            pass
        time.sleep(0.01)
    print(f"survived: served={served}  dropped={dropped:,} "
          f"({dropped // 1024} GiB refused)  queued={q.qsize()}/64")

threading.Thread(target=producer, daemon=True).start()
consumer()
```

```console
$ python3 bounded.py
survived: served=656  dropped=502,600 (490 GiB refused)  queued=64/64
```

Same overload, same cap: 440 MiB died vs 490 GiB *refused* — and a
survivor. Swap `put_nowait` for blocking `q.put(...)` and you get the
third policy, backpressure: a 5-second run here printed
`put=559  served=495  queued=64` — producer rate pinned to consumer rate,
nothing dropped, memory flat at the 64 MiB ceiling. Drop vs wait vs fail:
the whole overflow-policy conversation.

**Lab 3 — Go: what a buffer buys.** One million messages through one
channel, three capacities:

```go
// chanbench.go — 1 Mi messages through one channel, three capacities
package main

import (
        "fmt"
        "time"
)

func bench(capacity int) {
        const n = 1 << 20 // 1,048,576 messages
        ch := make(chan int, capacity)
        start := time.Now()
        go func() {
                for i := 0; i < n; i++ {
                        ch <- i
                }
        }()
        for i := 0; i < n; i++ {
                <-ch
        }
        d := time.Since(start)
        fmt.Printf("cap=%7d: %d msgs in %7v (%3.0f ns/msg)\n",
                capacity, n, d.Round(time.Millisecond), float64(d.Nanoseconds())/n)
}

func main() {
        bench(0)     // unbuffered: every send must rendezvous with a receive
        bench(1024)  // small buffer: absorbs bursts, still parks often
        bench(1<<20) // never fills: ring + lock, no parking
}
```

```console
$ go run chanbench.go
cap=      0: 1048576 msgs in    396ms (377 ns/msg)
cap=   1024: 1048576 msgs in    138ms (132 ns/msg)
cap=1048576: 1048576 msgs in     95ms ( 91 ns/msg)
```

Expected shape (Go 1.22, x86-64; the ratio is the result, not the
nanoseconds): unbuffered is 3–4x slower than a small buffer and ~10x
slower than a never-full one — every unbuffered message pays the park/wake
rendezvous, buffered ones pay only lock + copy. Note what the big buffer
did *not* remove: the one mutex per channel still serializes both
goroutines. Predict the ordering before you run.

## How to use this knowledge

- **Sizing bounded queues with Little's law.** Capacity = arrival rate ×
  tolerable latency: 2,000 msg/s, 100 ms tolerance → 200 slots. Challenge
  any queue sized "a million, to be safe" — a million messages of latency
  plus a slower OOM.
- **Diagnosing "leaks" that aren't.** A heap dump of millions of task
  objects held by one executor queue is overload converted to memory, not
  a leak. Check queue depth first; the fix is a bound plus a policy, not
  more heap.
- **Picking Go channel capacities.** Unbuffered when the rendezvous is the
  point; small buffer to absorb bursts; never a huge buffer "for safety" —
  it trades backpressure for invisible queueing latency.
- **Reviewing any "async" boundary.** Demand the overflow policy in
  writing: block, drop, fail, or spill. "It just queues" precedes the
  3 a.m. page.

## Connections

- **Module 01 chapter 10** — Rust ownership and moves: the type rule this
  chapter implements as architecture; `send`-consumes-the-value is E0382
  doing its job.
- **Module 01 chapter 18** — Erlang's per-process heaps: the GC payoff
  (micro, local pauses) is why message passing scales to a million actors.
- **Module 09 chapter 01** — send→receive as a synchronizes-with edge: the
  queue is an edge factory, and ownership transfer means the payload never
  needed shared-state ordering.
- **Module 09 chapter 03** — the lock-free branch of this design space:
  Michael-Scott's CAS queue, hazard pointers/epochs, false sharing with
  numbers.
- **Module 09 chapter 05** — delivery semantics at network scale:
  at-least-once + dedup becomes consensus and idempotent redelivery.
- **Module 05 chapter 03** — Binder: Android's queued IPC with a hard bound
  (the ~1 MB per-process binder buffer) — an OS-enforced queue that says
  no (`TransactionTooLargeException` is its overflow policy).
- **Module 06 chapter 03** — XPC/Mach ports: kernel-boundary message queues
  with ownership semantics — out-of-line memory is *remapped* into the
  receiver, the VM-level transfer-on-send.

## Quiz

1. **Concept.** State the invariant that makes an ownership-transfer queue
   race-free by construction, and what must be true of the message and the
   send for it to hold.

2. **Predict the output.** In Go, `chA` and `chB` both always ready:

   ```go
   for i := 0; i < 100000; i++ {
       select {
       case <-chA:
           a++
       case <-chB:
           b++
       }
       chA <- 0
       chB <- 0
   }
   ```

   A colleague predicts `a == 100000, b == 0` "because `chA`'s case is
   written first." What actually happens, and why?

3. **Predict the number.** `ch := make(chan [16]byte, 1024)`: how many
   bytes does the runtime allocate for the ring, where does the 1025th
   element land, and what property of `[16]byte` enables a single fused
   allocation?

4. **Bug diagnosis.** A Go service's memory climbs and never falls; pprof
   shows the retained objects held by goroutines parked in `chansend`.
   Each request starts a goroutine doing `results <- resp` on an unbuffered
   channel; one caller stopped reading `results` days ago. Explain the leak
   and the standard fix.

5. **Bug diagnosis.** A Java service OOMs at peak. Requests go through
   `Executors.newFixedThreadPool(8)`. Where is the memory, why does the
   pool's rejection machinery never fire, and what is the bounded
   replacement?

6. **Bug diagnosis.** A two-lock MPMC queue (one producer, one consumer)
   loses ~40% throughput versus the same queue with 56 B of padding
   between `head` and `tail` — though neither lock is contended. What is
   the cost, and why is it invisible to the profiler?

7. **Cross-module (module 01 ch10).** In Rust, `tx.send(v)` compiles but
   `v.field = 1` after it does not. What rule fired, and what does that
   purchase that Go's send — which accepts pointers happily — does not?

8. **Cross-module (chapter 05 preview).** A vendor claims "exactly-once
   delivery" from a retrying queue. Derive in two cases why that is false on
   a lossy channel, and state what the honest version needs on the receiving
   side.

<details>
<summary>Answers</summary>

1. **Single owner at every instant.** Before the send, only the producer
   can reach the message; after the receive, only the consumer. To hold,
   the message must be fully owned (no sender-reachable aliases — Erlang's
   copies/immutability, Rust's moves, Go's discipline), and the send must
   create the synchronizes-with edge so the receiver sees the sender's
   prior writes (chapter 01).

2. **`a ≈ b ≈ 50,000`.** `select` polls cases in pseudo-random order
   (`runtime.selectgo` shuffles poll and lock order) to prevent
   source-order starvation; both channels always ready, so each iteration
   is a coin flip (uniform pseudo-random choice, per the Go spec's
   "Select statements").

3. **16,384 B (16 KiB); slot 0; the element holds no pointers.** The ring
   is one flat array of `dataqsiz * elemsize` bytes; `sendx` wraps to 0 at
   `dataqsiz`. Pointer-free `[16]byte` lets `makechan` put hchan + ring in
   one malloc; pointer-carrying elements force a separate, GC-scanned
   buffer.

4. **Parked senders pin their payloads.** An unbuffered send with no
   receiver parks the goroutine and enqueues its sudog; the goroutine stack
   and the response it holds stay live — one goroutine plus payload leaked
   per abandoned request. Fix: guarantee the drain — a buffered `results`
   plus `close`, or `select { case results <- resp: case <-ctx.Done(): }`.

5. **In the work queue.** `newFixedThreadPool` uses an unbounded
   `LinkedBlockingQueue` (capacity `Integer.MAX_VALUE`): tasks accumulate,
   and an unbounded queue's `offer` never fails — so rejection never fires
   and the OOM is the queue. Replace with `ThreadPoolExecutor` plus a
   real-capacity `BlockingQueue` and set the `RejectedExecutionHandler`
   (abort / caller-runs / discard).

6. **False sharing.** Unpadded, `head` and `tail` share a 64 B line: each
   head update invalidates the producer's cached tail and vice versa —
   every operation pays an inter-core line transfer (~100 ns class).
   Memory-system time, attributed to no lock and no function; find it with
   `perf c2c`, or note that the padding fixes it.

7. **The move rule (E0382).** `send(v)` consumes `v`; ownership passes to
   the receiver, and any sender-side use afterwards fails to compile.
   Go's `send` copies the pointer value, leaving the sender free to keep
   mutating the referenced object — shared memory smuggled through a
   channel. The error buys sender-side aliasing prevention: the
   single-owner invariant by construction.

8. **Case 1: the ack arrives** — one delivery. **Case 2: the ack is lost**
   — the sender cannot distinguish "delivered, ack lost" from "never
   delivered": retry risks a duplicate, not retrying risks loss. The fork
   is unavoidable (two-generals). The honest feature: at-least-once
   delivery plus receiving-side dedup (idempotency keys, sequence windows)
   plus idempotent processing — exactly-once *effect* (chapter 05's world,
   and Kafka's actual design).

</details>
