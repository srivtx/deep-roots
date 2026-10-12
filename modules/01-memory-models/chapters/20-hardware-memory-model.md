# Chapter 20 — The hardware memory model: why cores disagree about memory

> **The 30-second version.** Chapter 02 promised that caches keep their copies of
> memory *coherent*. That promise is true — and far weaker than it sounds. Between each
> core and its cache sits a **store buffer** (plus other reordering machinery) whose job
> is to keep the core fed while writes drain. The consequence: two cores can observe the
> same two writes in opposite orders, and the classic two-thread handshake can end with
> both sides reading 0. That is not a malfunction — it is the documented contract
> between hardware and software, the *memory model* of the machine. x86-64 makes one
> narrow promise (TSO: stores become visible in order, but loads may pass them); ARM
> and POWER make almost none, trading ordering for power. Software rebuilds the order
> it needs with fences and acquire/release atomics, and the **DRF-SC theorem** is what
> makes every program from Chapters 08–18 portable across both worlds. This chapter is
> the hardware truth underneath every "memory model" those languages promised you.

## 1. The store buffer: where sequential consistency dies

Chapter 02 ended with MESI: a coherence protocol that guarantees no core ever sees a
stale or torn view of *one address*. So how can two threads disagree about the order
of two writes? Derive it, one performance decision at a time.

A store is not a single event. To write one byte, the core's cache must own the whole
64-byte line in Modified state (Chapter 02), which may mean invalidating every other
copy and waiting ~100 cycles for acknowledgments. A core issuing stores at one per
cycle would stall constantly. So hardware splits the store in two: the core appends
the write to a small FIFO **store buffer** (56 entries on a Skylake-client core, per
Agner Fog's tables) and marches on; the buffer drains to L1 asynchronously. Here is
the fateful optimization: **a load may execute while the core's own older stores are
still parked in the buffer** — the alternative, draining the buffer before every load,
would serialize the pipeline.

Now the famous litmus test, **Store Buffering (SB)** — two threads, two flags, each
writes its own and reads the other's:

```c
// SB litmus test — C11 relaxed atomics (atomicity, no ordering)
// Thread 0                          // Thread 1
x = 1;   // relaxed atomic store     y = 1;   // relaxed atomic store
r1 = y;  // relaxed atomic load      r2 = x;  // relaxed atomic load
// Question: can the program end with r1 == 0 AND r2 == 0?
// x86-64: YES.  ARMv8-A: YES.  Sequential consistency would say: impossible.
```

```
       Thread 0                             Thread 1
  +-------------------+               +-------------------+
  | store x = 1       |               | store y = 1       |
  | load  y           |               | load  x           |
  +---------+---------+               +---------+---------+
            |                                   |
            v                                   v
  +-------------------+               +-------------------+
  | store buffer of   |  x=1 parked   | store buffer of   |  y=1 parked
  | core 0            |  (not drained)| core 1            |  (not drained)
  +---------+---------+               +---------+---------+
            |                                   |
            v                                   v
     load y -> asks caches: y == 0      load x -> asks caches: x == 0
            |                                   |
            +------- both loads outran -------->+
              both cores' own buffered stores

  Result: r1 == 0 and r2 == 0. No line was ever incoherent; the ORDER
  of visibility across two lines disagreed. If this were a mutual-
  exclusion handshake (Dekker's algorithm, precisely), both threads
  would now enter the critical section.
```

Each thread's load races ahead of its own parked store, asks the caches, and gets 0.

**This is not a bug.** It is the *architectural contract*: the vendor documents which
reorderings may be observed — the hardware memory model — and everything else here is
software holding up its end. It took decades to formalize: x86-TSO was only rigorously
specified in 2010 (Sewell et al.), long after the chips shipped.

**Coherence vs. consistency — the distinction that unlocks this chapter:**

- **Coherence** (Chapter 02, MESI) is a *per-location* property: for one address, all
  cores agree on the order of writes to it. No torn values, no stale copies. SB's
  loads returned 0 because the stores had not *happened* globally yet — nobody saw a
  stale `y`.
- **The memory model** (consistency) is about *order across locations*: does everyone
  agree that `x=1` became visible before `y=1`? MESI says nothing about this, because
  all the interesting reordering happens **on the core's side of the cache** — in the
  store buffer, the load pipeline, speculation. The cache faithfully reports the order
  in which writes *arrive* at it; the core decides when they leave.

That is why "we have coherent caches" and "we have sequential consistency" (one global
interleaved order of all operations) are different claims, and why no coherence
protocol, however fancy, buys you the second one for free.

## 2. MESI: per-location truth, and nothing more

One honest paragraph, then the table. MESI (the "Illinois" protocol, Papamarcou &
Patel, 1984) tags every line with one of four states and enforces one invariant: *if
any cache holds the line Modified, all others hold it Invalid*. Reads, writes, and
snoops push the state machines around over a shared interconnect — that is Chapter
02's coherence, delivered. What MESI is *not*: a memory ordering mechanism. It has no
notion of "the store to line A happened before the store to line B" — different state
machines, different cores, fed by different store buffers.

```text
MESI — the four states of one cache line, per core (x86-64 & ARMv8-A, 64-B lines):

+-----------+-------+-----------------------------------------------------------+
| State     | line  | Meaning / what the core may do                           |
|           | clean?|                                                           |
+-----------+-------+-----------------------------------------------------------+
| Modified  | dirty | I own it exclusively; MAIN MEMORY IS STALE; on eviction  |
|           |       | I must write it back; a snoop read forces the writeback. |
| Exclusive | clean | I own it exclusively and memory matches; a store here    |
|           |       | upgrades to Modified SILENTLY (no bus traffic at all).   |
| Shared    | clean | Others may also hold copies; a store must first          |
|           |       | invalidate them all (write-with-invalidation round trip).|
| Invalid   |   -   | I hold nothing; any access starts with a miss.           |
+-----------+-------+-----------------------------------------------------------+

The transitions that matter:
  I --read miss-->  S (if another cache has it) or E (if I am the only owner)
  I --write miss--> M (all other copies invalidated first)
  S --write-->      I broadcast to others, then M   (the expensive store)
  E --write-->      M with ZERO bus messages         (the free store)
  M --snoop read--> S (line forwarded or written back; I lose dirtiness)
  M/E --eviction--> I (M writes back to memory first)
```

Notice **Exclusive**: it is why single-threaded code never notices MESI. Your line
arrives in E, stores upgrade to M silently, the interconnect stays quiet — until a
second core writes the *same* line and it starts ping-ponging M → I → M → I between
them. That is the engine of false sharing in Section 6.

**MOESI** adds an **O**wned state: one cache may hold the dirty line and *forward* it
to snoopers without writing it back first (AMD lineage). **MESIF** (Intel) adds a
**F**orward state: exactly one Shared copy is designated to answer snoops so the rest
stay silent. Both are bandwidth optimizations of the same skeleton — neither changes
anything about memory *ordering*.

## 3. The two poles: x86-TSO and the weak ARM/POWER world

Formally: a machine can preserve four orderings between two accesses A-before-B in
program order — load→load, load→store, store→load, store→store. **Sequential
consistency** preserves all four. Every real machine preserves fewer.

**x86-TSO** (the contract behind every x86-64 you'll touch; Sewell et al. 2010,
matching Intel SDM Vol. 3 Ch. 8) preserves three:

- Loads are not reordered with loads; stores are not reordered with stores; the store
  buffer drains in FIFO order.
- The one reordering: **a load may be satisfied before an older store drains** —
  Section 1's store buffer. That is the *entire* gap between TSO and SC, and it is
  exactly enough to break SB/Dekker.
- Locked instructions (`lock cmpxchg`, `xchg` with memory) and `mfence` are full
  barriers.

**ARMv8-A / POWER** (weak models; the AArch64 model formalized by Alglave, Maranget,
Tautschnig 2014, in Arm ARM DDI 0487) preserve none by default:

- Loads can be satisfied *early* by speculation and re-checked later; stores can be
  merged and held in write queues until an arbitrary visibility point — two stores to
  different lines can become visible in either order, differently per core.
- What they *do* honor: **dependencies**. An address dependency (load a pointer, then
  dereference it) orders the two loads in the architecture, on both ARMv8 and POWER.
  That gift is load-bearing for lock-free code, as Section 4's MP test shows.
- Ordering is opt-in, per instruction: `ldar`/`stlr` (acquire/release), `dmb`/`dsb`
  barriers, POWER's `.acq`/`.rel` suffixed accesses.

Why the split? **Physics, power, and complexity budget — not malice.** A strong model
constrains every future microarchitecture forever: global store order costs ordering
checkpoints and a little serialization on every memory op. In a 150-watt server core
that is a rounding error next to the out-of-order engine it feeds; in a 3-watt phone
core it is a direct tax on performance-per-watt, and letting stores coalesce and
loads fire speculatively wins battery life. History compounds it: x86's contract
froze in the 1980s when "lock" was literally a bus lock, and Intel pays to keep that
promise; ARM is licensed into everything from in-order microcontrollers to Apple's
M-series, and a weak contract leaves every licensee free to build whatever is
cheapest. Strong models are backwards compatibility you can bill for; weak models
are freedom.

## 4. Litmus tests: interrogating the machine

You cannot interview a CPU about its beliefs, so the field invented **litmus tests**:
tiny two-thread programs plus a question — "is this final state allowed?" Run the
program millions of times with both threads released at once; if the "impossible"
outcome appears, the architecture allows the reordering. That is how the formal
models were validated against real chips. Tools: `herd7` explores outcomes
*symbolically* against a formal model; `litmus7` (herdtools7/diy7,
Alglave–Maranget–McKenna) runs them on real hardware; and a plain C loop catches the
big ones yourself (lab below).

Three canonical tests, three verdicts:

```c
// (a) SB — Store Buffering: ALLOWED on x86-64, ALLOWED on ARMv8-A
//     (the Section 1 test; both threads can read 0).
//     THE reason Dekker's algorithm needs a fence even on Intel.

// (b) MP — Message Passing, the pattern of ALL concurrent code:
// Thread 0                      // Thread 1
msg = 42;                       while (flag == 0) ;   // spin
flag = 1;                       r = msg;              // read AFTER flag
// Can r == 0 while flag == 1?
//   x86-64: FORBIDDEN — TSO orders the two stores AND the two loads,
//           so msg's store must be visible before flag's.
//   ARMv8-A plain version: ALLOWED — the loads can reorder, the
//           consumer can see flag==1 with msg==0. This is the exact
//           shape of "worked on my x86 box, fails on the ARM server."
//   ARMv8-A with an address dependency (flag HOLDS the pointer:
//           r1 = flag; r2 = *(int **)r1) — FORBIDDEN, same as x86.
//   With release/acquire atomics (Section 5) — FORBIDDEN on both.

// (c) WO — Write Ordering (two stores, one observer):
// Thread 0                      // Thread 1
x = 1;                           r1 = y;
y = 1;                           r2 = x;
// Can r1 == 1 && r2 == 0 (y visible, x not yet)?
//   x86-64: FORBIDDEN — the store buffer is FIFO, x drains before y.
//   ARMv8-A: ALLOWED — stores to different lines can become visible
//           in either order per observer.
```

```text
+--------------------------------+--------------------+--------------------+
| Litmus test                    | x86-64 (TSO)       | ARMv8-A (weak)     |
+--------------------------------+--------------------+--------------------+
| SB  (store;load, 2 cores)      | ALLOWED            | ALLOWED            |
| MP  plain (store;load, 2)      | forbidden          | ALLOWED            |
| MP  with address dependency    | forbidden          | forbidden          |
| MP  with acquire/release       | forbidden          | forbidden          |
| WO  (store;store, observer)    | forbidden          | ALLOWED            |
+--------------------------------+--------------------+--------------------+
```

Read the MP rows twice — they carry the chapter. Message passing is *the* pattern of
concurrent programming (every channel, every flag, every RCU read), and the plain
version is what "works on x86, fails on Graviton" looks like. The two *forbidden on
both* rows are how the world actually programs around it: **address dependencies**
(publish a pointer, consume by dereferencing — the ordering rides the data, zero
instructions) and **acquire/release atomics** (Section 5). That "forbidden on both"
is why data-dependency programming works everywhere — and why Linux's RCU is built
almost entirely out of pointers.

A minimal harness, so the method is concrete:

```c
// how litmus results are actually gathered (schematic)
for (long i = 0; i < N; i++) {                 // N = 1e6..1e9
    x = 0; y = 0;                             // reset both locations
    spawn(thread0); spawn(thread1);            // each spins on a start flag
    go = 1;                                   // release both at once
    join(); join();
    violations += (r1 == 0 && r2 == 0);        // count the "impossible"
}
// violations > 0          => the architecture allows the reordering.
// violations == 0 after 1e9 => strong evidence, not proof — only the
//   vendor's contract proves a "forbidden". litmus7 tightens the odds
//   thousands-fold by keeping both threads spinning (no spawn/join
//   per round), which is why it catches what naive loops miss.
```

## 5. The fixes: fences, atomics, happens-before

**Hardware's own patch: the fence (barrier).** A fence tells the core: *nothing after
me becomes visible until everything before me has.* On x86-64 (where you'll least need
it): `mfence` is the full fence — it closes the store→load hole that SB exploits;
`sfence` orders stores only (needed after non-temporal/WC stores — insiders);
`lfence` orders loads and mostly appears serializing `rdtsc` pairs. On ARMv8-A:
`dmb` (data memory barrier; `dmb ish` is the kernel's system-wide flavor) is the
general fence; `dsb` waits for *completion* (cache ops, WFI — far more expensive);
`isb` flushes the instruction pipeline. **Cost:** a fence is a pipeline drain, order
10–100+ cycles; a locked RMW (`lock cmpxchg`) is ~20 cycles uncontended and is itself
a full barrier. Rule of thumb: a fence in a hot loop can cost more than the work
around it — which is why compilers and libraries go to absurd lengths to emit none.
The software contract below formalizes exactly those lengths.

**The software contract: C11/C++11 atomics.** C11 (N1570) and C++11 (N3337), built on
Boehm–Adve 2008, gave the languages a memory model with three everyday strengths:

```c
// The workhorse: release-store PUBLISHES, acquire-load SUBSCRIBES (C11)
_Atomic int flag = 0;        // the data itself can stay a plain variable
int msg = 0;                // ONE producer, ONE consumer: no race, by contract

// Producer                                    // Consumer
msg = 42;  // plain write                      while (atomic_load_explicit(
atomic_store_explicit(&flag, 1,                  &flag, memory_order_acquire) == 0)
    memory_order_release);                       ;
                                              r = msg;  // GUARANTEED 42 on
                                                        // x86, ARM, POWER, all
```

Derive it from MP: the pattern *is* the MP test. The release store forbids the
producer's earlier `msg` write from being reordered after the flag — it "releases"
everything before it when it becomes visible. The acquire load forbids the consumer's
later reads from being satisfied before it — it "acquires" everything the release
published. Two instructions meet in the middle and create a guaranteed channel; this
is the memory-model half of every mutex, channel, and `Once` in Chapters 08–18. The
idiom to burn in: **lock-guard shape** — release when you leave the private section,
acquire when you enter it.

The three strengths, as costs:

- `memory_order_seq_cst` — the default, and the expensive one: all seq_cst operations
  agree on one global order. Costs one `mfence` or locked `xchg` per store on x86-64
  (~20+ cycles); `ldar`/`stlr` on ARMv8. Needed only when threads *compare notes*
  about ordering they didn't synchronize over (Dekker/Peterson, the IRIW family — two
  observers must agree on the order of two independent writers).
- `memory_order_acquire` / `release` — the workhorse: one-directional pairing (above);
  compiles to **plain `mov` on x86-64** and `ldar`/`stlr` on ARMv8.
- `memory_order_relaxed` — atomicity only, no order: the SB and WO tests above used
  it, and it is legal on both poles. (`memory_order_consume` was designed for POWER
  dependency tracking; compilers broke it so thoroughly that everyone treats it as
  `acquire` — avoid it.)

**The volatile trap (Chapter 08, now with teeth).** C's `volatile` means *don't cache
this access in a register, don't elide it* — nothing more. It is **not atomic** and
**orders nothing**: a plain store after a volatile store can still be reordered past
it by compiler or CPU, and a 64-bit read can tear on a 32-bit core. Using it for
inter-thread flags is a data race — undefined behavior (Chapter 08) with a track
record of working until it doesn't. **Java's `volatile`** (Chapter 12) is the opposite
extreme: since JSR-133 (Manson–Pugh–Adve, 2004, JDK 1.5) it means sequentially
consistent visibility. Same keyword, opposite contracts, because both languages
reached for the C keyword in the 1990s before anyone knew what threads require, and
C# (Chapter 13) split the difference at acquire/release. Three languages, one
keyword, three models — check the language before you trust the word.

**Happens-before: the portable abstraction.** All of this compiles down to one
relation (Lamport, 1978): **A happens-before B** if A precedes B in program order on
one thread, or A synchronizes-with B across threads (mutex release → mutex acquire,
thread `join`, atomic release-store → acquire-load that reads it, channel send →
receive — Chapter 11 built Go's version of exactly this). The rule: *if A
happens-before B, then B is guaranteed to observe A's writes.* Fences and
acquire/release are the machinery; happens-before is the interface every language
model sells you (Rust Ch. 10, Go Ch. 11, Java's JMM Ch. 12).

On top of it sits **the theorem that makes Chapters 08–18 portable**:

> **DRF-SC (data-race-free sequential consistency).** If a program has no data races,
> it executes as if sequentially consistent — on x86, on ARM, on POWER. Adve–Hill 1990
> (hardware level), Boehm–Adve 2008 (C/C++), and the JMM guarantees it for Java.

A *data race* is two accesses to the same location, at least one a write, on different
threads, with no happens-before edge between them. In C/C++/Rust a data race is
**undefined behavior** (Chapter 08's license for the optimizer; Chapter 10's safe Rust
outlaws it at compile time via `Send`/`Sync`, which is why the borrow checker is so
obsessed with sharing). In Java it is *defined but evil*: accesses stay atomic (no
tearing), but the compiler and CPU may keep serving a stale value indefinitely. Write
race-free code and you never think about TSO or ARMv8 — your language's locks and
atomics carry the DRF-SC warranty. Break it and you are back in Section 1, reading
store-buffer diagrams to explain a production incident.

**How you will actually meet this.** Almost nobody writes fences. The Linux kernel
does — `Documentation/memory-barriers.txt` (Howells, McKenney) is the canonical field
manual, and its crown jewel is the bounded-queue pattern: the producer owns `head`,
the consumer owns `tail`, each index published with release semantics, so the other
side's reads of the ring entries are ordered for free — this chapter's MP pairing in
a kernel costume, and the same shape lives in every channel, mutex, and RCU read
path. Most programmers meet the memory model pre-paid: **a mutex is a seq_cst-shaped
pair of barriers** (release on unlock, acquire on lock, plus an atomic RMW on the
lock word) — which is why lock-protected code is simply correct everywhere, and why
"add more locks" fixes ordering bugs without anyone knowing why. Low-latency
programming (Chapter 19's allocator fast paths, lock-free queues) is the art of
deleting locks while keeping exactly the release→acquire edges the queue pattern
proves you need.

## 6. False sharing: MESI's revenge on your data layout

Ordering is not the only way cores disagree about memory. Section 2's state machine is
*per-line*, and a line is 64 bytes (`getconf LEVEL1_DCACHE_LINESIZE`; Apple M-series
uses 128). Two *different* variables in one line are, to MESI, **the same line** — so
two threads writing two different variables fight over cache state as if they were
writing one variable:

```c
// pad.c — the false-sharing benchmark
// gcc -O2 -pthread pad.c && ./a.out
struct plain  { long a, b; };              // 16 B: a and b share ONE 64-B line
struct padded {                             // two lines, two private states
    alignas(64) long a;                    // C11/C++11: place at a 64-B boundary
    alignas(64) long b;
};
// Thread A: 200,000,000 increments of .a   Thread B: same on .b
// Typical result on a 4-core x86-64, threads pinned to different physical
// cores (taskset -c 0,2): plain runs ~3-6x SLOWER than padded.
// Nothing in the C memory model is violated — the race is on MESI's turf.
```

```
False sharing: two INDEPENDENT variables, one 64-byte line, two cores

  t0: line X [ a | b ] = Modified on core 0
  t1: core 1 stores b -> snoop -> line X invalidated on core 0
      line X = Modified on core 1 ... core 0's next a-store invalidates it
  t2: line X bounces M -> I -> M -> I, ~100+ cycles per bounce,
      for variables that NEVER touch each other

  Both threads queue at the MESI window; throughput collapses while
  the caches stay perfectly coherent the whole time.
```

Each bounce is a cross-core round trip on the order of 100 cycles (Chapter 02's
latency table: ~40 cycles to another core's L1, worse across sockets). The program is
data-race-free, correct, and slow — a pure performance bug one level below the
language. This is the standard answer to "my concurrent queue is slow and it's not
the locks": head and tail indexes sitting in the same line (Chapter 02 warned you the
line is the unit of transfer; this is that warning with two writers attached).

Fixes, per language: `alignas(64)` in C/C++ (or `std::hardware_destructive_interference_size`,
C++17 — 64 on mainstream x86); `@Contended` in Java (JEP 142; pads 128 bytes by
default, user classes need `-XX:-RestrictContended`); a manual `[64]byte` pad field
in Go; padded slots in a `SharedArrayBuffer` in JS (Chapter 15's Atomics users do
exactly this). Note the Java/LMAX-Disruptor habit of padding to *128* bytes:
hyperthread siblings share an L1, so adjacent-line effects outlive the 64-byte rule.

## What insiders know

- **x86 is TSO, not SC — and the corners are where it bites.** (a) SB itself:
  Dekker/Peterson fail without `mfence`, and that is *allowed* on x86. (b)
  Non-temporal stores (`movntdq` to write-combining memory) are **outside TSO** —
  unordered with ordinary stores, which is why every memcpy using NT stores ends in
  `sfence`. (c) Self- and cross-modifying code: Intel SDM 8.1.3 prescribes a
  fences-plus-serializing-instruction ritual, and the kernel's `text_poke` +
  `sync_core` exists because instruction fetch doesn't play by the store-buffer rules.
- **On x86-64, acquire loads and release stores compile to plain `mov`** — the
  lock-free fast path costs nothing beyond the compiler barrier. The heavyweights are
  seq_cst *stores* (`xchg` with implicit lock, ~20 cycles — compilers switched from
  `mov+mfence` to `xchg` because it was faster) and locked RMWs. The folklore
  "atomics are slow" comes from measuring only locked RMWs.
- **On ARMv8-A, `ldar`/`stlr` are SC-strength**: all LDAR/STLR accesses are mutually
  totally ordered — *stronger* than release/acquire needs. ARMv8.3-RCPC added
  `ldapr` specifically to let hardware relax it: a whole instruction invented to claw
  back performance the C++ abstraction over-specified.
- **Dekker's algorithm (attributed to T. J. Dekker, ~1962; written up in Dijkstra's
  1965 "Cooperating Sequential Processes") and Peterson's (1981) are SC-era
  algorithms.** They need a full fence on weak models and an `mfence` even on x86. On
  Intel parts the store-buffer drain window is so narrow that unfenced builds can run
  *years* without losing — then the code is ported to an ARM phone or recompiled so
  the flag lives in a register, and it fails in an afternoon. "Works on my Intel" is
  a portability trap with a five-decade track record.
- **Memory-ordering bugs are heisenbugs with known cures.** `printf` is a
  barrier-heavy, lock-owning syscall that orders and flushes far more than your bug
  needs — add one and the symptom vanishes; `-O0` removes the register caching that
  was half the bug. Debug with ThreadSanitizer (`-fsanitize=thread`, expect 5–10x
  slowdown) and litmus tools, never with prints.
- **In the Linux kernel, `smp_store_release`/`smp_load_acquire` are plain `mov` on
  x86** — the hardware barrier there is only a compiler barrier, because TSO already
  provides release/acquire. `smp_mb()` is `mfence` on x86-64, `dmb ish` on ARM64.
  `Documentation/memory-barriers.txt` names the store-buffer, invalidation-queue, and
  snoop culprits explicitly — this chapter, 10,000 words longer, in a kernel tree.
- **The `volatile` collision is an interview three-for-one.** C `volatile`: no
  ordering, no atomicity (Chapter 08). Java `volatile`: sequentially consistent
  visibility, JSR-133, 2004 (Chapter 12). C# `volatile`: acquire/release only —
  *not* SC — so an SB pattern in C# is broken even though the keyword "sounded
  strongest." Same word, three contracts, one 1990s naming accident.

## See it yourself

**Lab 1 — run the SB litmus test (C, pthreads, atomic vs. volatile).**

```c
// sb.c — Store Buffering litmus, two variants
// gcc -O2 -std=c11 -pthread sb.c -o sb && ./sb
#include <stdio.h>
#include <stdatomic.h>
#include <pthread.h>

static _Atomic int x, y, go;
static volatile int vx, vy;          // C volatile: atomic? no. ordered? no.
static int r1a, r2a, r1v, r2v;       // results: atomic / volatile

static void *t0(void *_) {
    while (!atomic_load_explicit(&go, memory_order_acquire)) ; // spin till GO
    atomic_store_explicit(&x, 1, memory_order_relaxed);
    r1a = atomic_load_explicit(&y, memory_order_relaxed);
    vx = 1;                          // volatile pair: still no ordering —
    r1v = vy;                        // and technically a data race (UB)
    return 0;
}
static void *t1(void *_) {
    while (!atomic_load_explicit(&go, memory_order_acquire)) ;
    atomic_store_explicit(&y, 1, memory_order_relaxed);
    r2a = atomic_load_explicit(&x, memory_order_relaxed);
    vy = 1;
    r2v = vx;
    return 0;
}

int main(void) {
    long sb_a = 0, sb_v = 0;
    const long N = 100000;
    for (long i = 0; i < N; i++) {
        x = 0; y = 0; vx = 0; vy = 0; atomic_store(&go, 0);
        pthread_t a, b;
        pthread_create(&a, 0, t0, 0); pthread_create(&b, 0, t1, 0);
        atomic_store(&go, 1);        // release both threads together
        pthread_join(a, 0); pthread_join(b, 0);
        sb_a += (r1a == 0 && r2a == 0);   // SB outcome with atomics
        sb_v += (r1v == 0 && r2v == 0);   // SB outcome with volatile
    }
    printf("SB (relaxed atomics): %ld / %ld\n", sb_a, N);
    printf("SB (volatile)       : %ld / %ld\n", sb_v, N);
    return 0;
}
```

What to expect, honestly: on x86-64 the SB outcome is *architecturally allowed* but
the window is a handful of cycles — you may see 0 hits in 100k rounds or a burst of
them; pin to physical cores (`taskset -c 0,2 ./sb`) and raise N. The MP and WO tests
will **never** fire on x86 — the contract forbids them. For those you genuinely need
weak hardware: a Raspberry Pi 4/5, an AWS Graviton or Oracle Ampere free-tier VM, or
a phone via Termux — there, plain loads reorder and the volatile variant lights up
immediately. On x86-only hardware the honest statement is: SB may show itself, the
rest will not, and "didn't fire" is evidence, never proof (Section 4). For serious
work, `litmus7` runs the same test with persistent spinning threads and proper round
counting.

**Lab 2 — false sharing, before and after.** Build `pad.c` from Section 6 with both
structs; time each with `perf stat -e task-clock,cycles ./a.out`. Then let the kernel
point at the culprit: `sudo perf c2c record -- ./a.out` followed by
`perf c2c report --stdio` (perf from Linux 4.14+); look for the **HITM** column —
"loads that hit a line Modified in another core's cache" — one address, two hot
8-byte offsets. Check your line size first: `getconf LEVEL1_DCACHE_LINESIZE`
(64 on mainstream x86-64/ARMv8-A; Apple M-series prints 128 — pad accordingly).

**Lab 3 — watch the compiler hold up its end.** `gcc -O2 -S` the MP snippet with the
acquire spin loop and read the assembly (Chapter 07 skills): on x86-64 you will find
plain `mov` everywhere — no fences, because TSO plus the C11 contract already
suffice. Compile the same file for `-arch arm64` (or on the Pi) and the loads become
`ldar`. The abstraction is exactly one instruction thick — that is the whole design.

## How to use this knowledge

- **Bug triage with a new eye.** "Thread B saw the flag set but read stale data" is
  no longer mysterious: it is the plain-MP outcome, and the fix is one release store
  plus one acquire load — or a mutex, which is those two things plus an RMW.
- **Performance review.** `seq_cst` everywhere in a hot path pays ~20 cycles per
  store on x86 for order nobody consumes; downgrade publish sites to release/acquire,
  leave seq_cst to the algorithms that compare notes. Before blaming locks for a slow
  queue, run `perf c2c` and check the line layout of head and tail.
- **Portability audits.** Lock-free code validated only on x86 was validated against
  TSO, not reality; run it on a Graviton instance and under ThreadSanitizer before
  you believe it.
- **Interview answers with teeth.** "Why is C's volatile not a synchronization
  primitive, and what is it in Java?" — you can now answer with the three contracts,
  the JSR-133 date, and the SB test.
- **Reading kernel and runtime sources.** `smp_store_release`, the `head`/`tail`
  dance in every ring buffer, RCU's pointer publication — you now recognize all of it
  as Section 5's acquire/release pairing in costume.

## Connections

- **Chapter 02** — the coherence MESI implements, and the 64-byte line both false
  sharing and cache behavior live on.
- **Chapter 07** — disassembly fluency: seeing `mfence`, `lock cmpxchg`, `ldar`, and
  plain `mov` in compiler output is the fastest way to trust Sections 4–5.
- **Chapter 08 (C)** — UB as optimizer license; the volatile trap is UB's most common
  concurrent disguise, and C11 atomics are the repair kit.
- **Chapter 09 (C++)** — `std::atomic` and `hardware_destructive_interference_size`
  are this chapter's contract in library clothing.
- **Chapter 10 (Rust)** — `Send`/`Sync` as compile-time DRF-SC enforcement: safe Rust
  refuses to compile the data race the theorem requires you not to have.
- **Chapter 11 (Go)** — happens-before edges around channels, and the padding Go
  queue authors use; one memory model, same hardware underneath.
- **Chapter 12 (Java)** — JSR-133 and the JMM: the language-level formalization this
  chapter's happens-before section describes, plus `@Contended`.
- **Chapter 13 (C#)** — the third `volatile` contract (acquire/release only): same
  keyword, weaker promise than Java's.
- **Chapter 15 (JavaScript)** — `SharedArrayBuffer` + `Atomics`: the web grew a
  C11-shaped memory model in 2017; `Atomics` acquire loads are Section 5 verbatim.
- **Chapter 19 (allocators)** — per-thread caches dodge false sharing by giving each
  core its own lines; the fast path is an Exclusive-state free lunch.
- **Chapter 22 (mobile)** — why every phone is a weak-model machine, and why
  cross-platform apps feel this chapter first.
- **Queued security module** — TOCTOU races and Rowhammer are "cores disagree about
  memory" turned into an attack instead of a bug.

## Quiz

1. *(concept)* A machine has perfect cache coherence — every core agrees on the
   order of writes to any single address. Explain, in terms of *where the reordering
   hardware lives*, why such a machine can still allow the SB outcome (both threads
   read 0). What exactly did MESI never promise?

2. *(predict)* The SB test from Section 1 (relaxed atomics; each thread writes its
   own flag, then reads the other's). Which of the four final outcomes
   `(r1, r2) ∈ {(0,0), (0,1), (1,0), (1,1)}` are architecturally allowed on
   (a) x86-64 and (b) ARMv8-A?

3. *(predict)* The WO test: Thread 0 does `x=1; y=1;` (relaxed); Thread 1 does
   `r1=y; r2=x;` (relaxed). Can Thread 1 end with `r1==1 && r2==0` on x86-64? On
   ARMv8-A? Name the one-line change to `y`'s store and load that makes the outcome
   impossible on both, and what instructions that change compiles to on each
   architecture.

4. *(bug diagnosis)* A C programmer shares a flag between threads like this:
   `volatile int ready; ... data = 42; ready = 1;` (producer) and
   `while (!ready) ; r = data;` (consumer). Diagnose *two independent defects* — one
   the compiler can cause even on a single-threaded-looking core, one the hardware
   causes on ARM — plus the standard's verdict on the whole program.

5. *(bug diagnosis)* A single-producer/single-consumer ring queue stores the item,
   then increments its `tail` index with `memory_order_relaxed`; the consumer reads
   `tail` with `relaxed`, then reads the item. It passed a week of testing on an
   x86-64 build server and fails about once per 10^8 operations on an ARMv8 cloud
   instance. Explain precisely why x86 saved it, what ARM did, and the minimal fix.

6. *(bug diagnosis)* `struct stats { long hits; long misses; };` — two threads
   increment `hits` and `misses` respectively, 200M times each, on different cores.
   The padded version runs 4x faster, TSAN reports nothing, there is no data race.
   What is the machine doing with the cache line, in MESI terms, and what does
   `perf c2c` show that proves it?

7. *(concept)* Why is `memory_order_seq_cst` called "the expensive default" on
   x86-64 — what does a seq_cst store cost relative to a release store, and why is
   plain publish/subscribe (MP) perfectly correct *without* it? Name one class of
   algorithm that genuinely needs the full seq_cst guarantee.

8. *(cross-chapter)* Chapters 08, 12, and 13 each define a keyword spelled
   `volatile`. State what each guarantees for cross-thread visibility, name the
   standardization effort that fixed Java's version, and explain what Java still
   does *not* guarantee about a racing read, despite its strong `volatile`.

<details>
<summary>Answers</summary>

1. **MESI is per-location; the memory model is cross-location.** The reordering
   hardware — store buffers, the load pipeline, speculation — sits *between the core
   and its cache*, on the core's side of the coherence protocol. In SB, each store
   parks in its own core's store buffer while the thread's load bypasses it and asks
   the caches; the caches answer honestly (0) because the stores have not *reached*
   the cache system yet. MESI guarantees no core ever sees a stale or torn copy of
   *one address* (coherence); it never promised any ordering between writes to
   *different* addresses (consistency). Two variables means you have left MESI's
   jurisdiction and entered the memory model's.

2. All four outcomes are allowed on **both** architectures. `(0,1)` and `(1,0)` are
   the ordinary serial interleavings; `(1,1)` happens if both writes drain before
   both loads run; and `(0,0)` — the outcome sequential consistency would forbid — is
   allowed because a load passing the core's own older store is TSO's one reordering
   (and one of many on ARMv8). Relaxed atomics give atomicity per location, never
   ordering across locations; only seq_cst or fences remove `(0,0)`.

3. **x86-64: forbidden. ARMv8-A: allowed.** On x86 the store buffer is FIFO, so
   `x=1` is globally visible before `y=1`; if Thread 1 saw `y==1`, then `x==1` was
   already visible, and its loads are not reordered with each other. On ARM, the two
   stores can become visible in either order, so Thread 1 can read the newer `y`
   and the older `x`. The fix: store `y` with `memory_order_release` and load `y`
   with `memory_order_acquire` — on x86-64 both compile to plain `mov` (TSO already
   forbids the reordering; the annotation is free insurance), on ARMv8 they become
   `stlr`/`ldar`.

4. Defect one, **compiler**: `volatile` only orders accesses *to volatile objects*;
   the plain `data = 42` store can be sunk below `ready = 1` (or `r = data` hoisted
   out of the loop) — `-O2` will do this, and the program breaks even on one core.
   Defect two, **hardware**: even compiled as written, ARMv8 may reorder the two
   stores (WO is allowed) and the consumer's two loads, so the consumer can see
   `ready == 1` with `data == 0` — the plain-MP allowed outcome. The standard's
   verdict: a data race on `data` → **undefined behavior** in C11. The repair is
   C11 atomics: `atomic_store(&ready, 1, memory_order_release)` and an acquire
   load, leaving `data` plain — exactly the Section 5 idiom.

5. This is plain MP with `tail` playing the flag. On **x86-64**, TSO orders the
   producer's store→store (item before `tail`) and the consumer's load→load (`tail`
   before item), so the broken outcome is architecturally impossible — the x86 test
   could not have failed even though the code was wrong. On **ARMv8**, all four
   orderings are negotiable: the `tail` store can become visible before the item
   store, and the item load can be satisfied before the `tail` load — a tiny window
   both ways, hence ~1 in 10^8. Fix: producer's `tail` store → `release`
   (publishes the item), consumer's `tail` load → `acquire` (subscribes). Cost on
   x86: zero extra instructions; on ARM: `stlr`/`ldar`.

6. `hits` and `misses` are 8 bytes each inside one 64-byte line. Each thread's store
   requires the line in Modified state, so MESI invalidates the other core's copy on
   every increment and the line ping-pongs **M → I → M → I** once per write, each
   bounce a ~100-cycle cross-core round trip — while the *program* is perfectly
   race-free, so TSAN is correctly silent: this is a cache-level bug, invisible to
   the language. `perf c2c` reports it as **HITM** events (hits on a line Modified
   in another cache) concentrated on one address with two hot 8-byte offsets. The
   fix is `alignas(64)` (or C++17's `hardware_destructive_interference_size`):
   each counter gets a private line and each core a stable Exclusive/Modified
   state.

7. A seq_cst store on x86-64 compiles to `xchg` (implicit `lock`) or `mov + mfence`
   — roughly 20+ cycles, with a store-buffer drain — while a release store is a
   plain `mov`, because TSO already orders stores. MP doesn't need a total order:
   producer and consumer *synchronize directly* (release→acquire edge), so a
   happens-before chain already proves the consumer sees the data. What needs
   seq_cst is any algorithm where threads reason about ordering they did **not**
   synchronize over: mutual exclusion by symmetric flags (Dekker/Peterson) and the
   IRIW family, where two observers must agree on the order of two independent
   writers. No pairwise acquire/release edge can express "everyone sees the same
   order"; only the total order or explicit fences can.

8. **C (Chapter 08)**: `volatile` guarantees only that accesses are not elided or
   cached in registers — no atomicity, no cross-thread ordering, no memory-model
   standing. **Java (Chapter 12)**: `volatile` is sequentially consistent — a write
   synchronizes-with every later read; fixed by **JSR-133** (Manson, Pugh, Adve;
   JDK 1.5, 2004), which rebuilt the JMM on happens-before. **C# (Chapter 13)**:
   acquire/release only — half of Java's promise, and still enough to be an SB
   trap. What Java still does not guarantee: *liveness/fairness* — the spec lets a
   racing read stay stale indefinitely (it must eventually make writes visible but
   never bounds *when*, and a tight loop on a non-volatile variable may never
   re-read memory at all). In practice HotSpot volatiles are plain loads/stores on
   x86 (free via TSO) and barrier-backed on ARM, so unbounded staleness is
   vanishingly rare — permitted, not observed.

</details>
