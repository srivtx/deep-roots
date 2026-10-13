# Module 13 — Networking internals: the stack as a memory machine

> A packet is memory in motion. Every layer of the network stack is a
> buffer-management decision: the NIC's DMA ring is a producer-consumer
> queue in host RAM, the `sk_buff` is a refcounted scatter-gather object,
> TCP is two state machines that manage buffer occupancy in two places at
> once, epoll/io_uring is the shared-memory ring idea applied to
> syscalls, and RDMA is literally remote memory with the CPUs taken out.
> Module 01's question — *where do the bytes live, and who moves them* —
> asked all the way down the wire.

**5 chapters · one final exam · prerequisite: module 01 chapters 02
(the tower), 03 (virtual memory — DMA and mmap return), 05 (the heap —
buffer management returns); module 07 ch04-05 (the Linux kernel's memory
side) is referenced, not required.**

## How to study it

Chapter 01 goes from the wire to the DMA ring (where a packet *lands*
before the CPU knows it exists); 02 is the kernel's receive/transmit
path as a memory story (`sk_buff` anatomy, conntrack as a hash table,
qdisc as a queue policy); 03 is TCP derived as a buffer machine (the
window arithmetic, congestion control as sender-side network memory,
bufferbloat as queue occupancy); 04 is the userspace I/O evolution
(epoll's ready list, io_uring's shared rings, zero-copy); 05 closes on
QUIC (TCP rebuilt in userspace) and RDMA (memory as a network endpoint).
Labs: any Linux box (the real commands: `ethtool`, `ss`, `strace`,
`/proc/net`); platform marked where it varies.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [Wire, NIC, and the DMA ring](chapters/01-wire-to-nic.md) | Encoding, the Ethernet frame, and the descriptor ring — packets land in RAM before the CPU knows. |
| 02 | [The kernel packet path](chapters/02-kernel-packet-path.md) | `sk_buff` anatomy, netfilter/conntrack, qdisc, XDP — the receive path as a memory story. |
| 03 | [TCP: the buffer machine](chapters/03-tcp-buffer-machine.md) | Flow control, congestion control, BDP arithmetic, bufferbloat — windows are buffers. |
| 04 | [Userspace I/O: read, epoll, io_uring](chapters/04-userspace-io.md) | The copy ladder, the ready list, shared-memory rings — eliminating syscalls one by one. |
| 05 | [QUIC, RDMA, and the network as memory](chapters/05-quic-rdma.md) | TCP rebuilt in userspace; memory registration and one-sided RDMA — the extended latency ladder. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — wire, NIC, and the DMA ring.** Start from physics: a wire
carries voltage, and the receiver must recover the *clock* from the
signal — derive why line coding exists (NRZ loses sync on long runs →
Manchester (10BASE-T's 50% overhead) → 4B/5B (100BASE, 25% overhead...
actually 80%) → 64b/66b (10GBASE+, 3% overhead) — the encoding
efficiency ladder). The Ethernet frame drawn byte by byte: preamble (8 B,
the last byte 0xAB is SFD), dst MAC 6 B, src MAC 6 B, EtherType 2 B,
payload 46-1500 B, FCS/CRC32 4 B; the minimum-64 B frame explained from
collision detection (a 10 Mbps shared-bus segment must be longer than
the signal's round trip — the slot time of 512 bit-times; why the
minimum frame exists even though switched networks don't collide —
compatibility); MTU 1500 as an arbitrary 1980 number that now costs the
world (derive the per-packet header tax: 1,500 B payload vs 38 B
overhead (14+20+20) = 2.5%; a jumbo 9,000 B frame cuts the per-byte
overhead ~6× but breaks mixed networks — the path-MTU black hole
folklore). The MAC learning table as a hash cache (48-bit addresses
scanned linearly would be hopeless; the switch keeps a CAM/hash). The
core: the NIC's DMA descriptor rings — TX and RX rings as fixed arrays
of descriptors (each: a physical address + length + control bits), the
NIC is the *producer* on RX (DMAs the frame into a pre-allocated buffer,
then posts the descriptor), the driver is the consumer (refills with new
buffers); the head/tail pointers communicated through MMIO doorbell
registers — draw the ring, the descriptors, the buffers, and both
pointers; this is a producer-consumer shared-memory queue, the same
structure as io_uring (ch04) and the virtio vring (module 15 ch03) — say
so. Interrupt strategy: derive the storm (10 Gbps of 64 B frames = 14.88
Mpps → one interrupt per packet is millions of interrupts/sec, each a
~1-2 µs pipeline flush) → NAPI: interrupt → then switch to *polling* on
a softirq budget (packets per poll ~64) while traffic is heavy, re-arm
interrupts when the ring drains — the hybrid is an adaptive
interrupt/poll crossover. RSS (multi-queue NICs: one ring pair per CPU
core, the toeplitz hash over the 4-tuple spreads flows — the flow
affinity insight). Offloads as API lies: checksum offload (the stack
marks `CHECKSUM_PARTIAL`, the NIC fills it), scatter-gather, TSO (the
kernel hands the NIC a 64 KB "super-packet", the NIC slices it into
MSS-sized segments — the per-segment headers are fabricated in
hardware), GRO on receive (the inverse merge). Insiders: the ring size
(`ethtool -G`: typical defaults 256-4,096) is a latency/loss trade; a
burst larger than the ring drops silently (no error, counter only). Lab:
`ethtool -S eth0` (drop counters read line by line), `ethtool -g` (ring
sizes), `ethtool -l` (RSS queues), `ethtool -k` (offload flags),
`/proc/interrupts` (one line per queue, the affinity mask), `cat
/sys/class/net/*/queues` — mark: names vary by driver.

**Chapter 02 — the kernel packet path.** The `sk_buff` drawn as a
structure diagram: one allocation (head buffer) with `head`, `data`,
`tail`, `end` pointers — the *headroom* (64-512 B) is reserved so
headers can be *pushed* (each encapsulation layer prepends its header:
`push` just moves the `data` pointer — no copy, no realloc; derive why
this beats "allocate exact"), the tailroom for appends; `skb_shared_info`
lives at the very end of the allocation (frag list: the scatter-gather
parts pointing at page fragments — a 64 KB GRO'd packet is one skb with
16 page frags, not a 64 KB contiguous block; the truesize vs len
distinction — socket memory accounting counts *truesize*, the real
allocation, not the payload bytes: an insider fact that explains rmem
behavior); `clone` (shares the data, copies the struct — refcounted:
module 01 ch14's PyObject pattern returns; why: netfilter and taps need
to *look* at a packet without copying it) vs `copy` (pskb_copy — the
data too, needed when someone *writes*). The receive path walked end to
end: driver NAPI poll → `netif_receive_skb` → (XDP hook earlier) →
netfilter hooks (the 5 hooks: PREROUTING, INPUT, FORWARD, OUTPUT,
POSTROUTING) → conntrack (the flow table as a hash keyed by the 5-tuple
/ 7-tuple with zones; the table size `net.netfilter.nf_conntrack_max`
(default scales with RAM, ~262,144 on a 16 GB host — when it fills, *new
connections drop* (log: "nf_conntrack: table full, dropping packet") —
the classic production incident), hash collisions, the GC of idle
entries (the 5-day default timeout) → routing → the socket lookup (the
established-hash: the local port + remote 4-tuple → socket; the listen
queue for SYN) → the socket's receive queue: an skb *list* bounded by
`sk_rmem` (the drop when exceeded — `tcp_mem`-family pressure). The
transmit path: qdisc (the queueing discipline *before* the driver:
pfifo_fast's 3 bands, fq's per-flow pacing — fq is BBR's pacing partner;
BQL — byte queue limits, the hardware TX ring throttled to ~1-2 ms of
bytes, because a big device queue = standing latency, the bufferbloat
lesson at driver level) → the driver maps frags for DMA (the dma_map
cost: the IOMMU bounce/map per packet — mention; the iova). XDP/eBPF:
the packet processed *before* the skb is even built (the verifier's
safety guarantees; XDP_DROP at ~5 Mpps per core vs the full stack at
~1-2 — the design insight: the skb allocation is itself a major cost, so
the fast path skips it; AF_XDP sends the packet to userspace via —
again — a ring of descriptors). Insiders: the drop points enumerated
(each has a counter: `ip_rcv` errors, backlog drops, `netstat -s |
grep -i drop` is the triage; `ss -m` shows per-socket memory; dropwatch
/ `perf trace` maps drops to code). Lab: `ss -m` output read (the
skmem line: r/rmem/w/wmem fields decoded), `/proc/net/sockstat`, `cat
/proc/net/netstat | awk` (TcpExt counters: ListenDrops,
TCPRcvCollapsed), `conntrack -L | wc` vs the max, `dropwatch -l kas`
(documented if unavailable), an `nstat -az` before/after a 1-second
iperf.

**Chapter 03 — TCP: the buffer machine.** The two buffers drawn: the
send buffer (a *retransmission safety copy* — derive: bytes can only be
freed when ACKed, so the buffer must hold everything unACKed; a blocking
`send()` blocks because the buffer is full of unACKed data — the
*ack-clock*), the receive buffer (a reassembly queue — out-of-order
segments parked by seq number, the OOO queue). Flow control derived from
the receiver's memory: the window (rwnd) is literally "free bytes in my
receive buffer" advertised in every segment; zero window → the persist
timer (keep asking; the receiver updates when it drains) → derive the
deadlock that requires the persist timer (if the window-update ACK is
lost, both sides wait forever). Window scaling (RFC 1323): why 16 bits
caps at 64 KB, and the BDP math (10 Gbps × 40 ms RTT = 50 MB in flight
— a 64 KB window starves a fat pipe by ~800×; the scale exponent to
~1 GB windows). Congestion control as *sender-side memory of the
network's state* — the network is not a party to the protocol, so the
sender infers it: slow start (double per RTT — exponential growth from
a small window; the IW: 10 segments RFC 6928, was 1), congestion
avoidance (AIMD — additive increase one MSS/RTT, multiplicative decrease
½ on loss; derive the throughput of AIMD: ~1.22×MSS/RTT×√(p) — the
Mathis equation, what it costs to lose one packet in a million on a fat
pipe: the number worked), CUBIC (Linux default since 2.6.19: the cubic
window function of time since last loss — concave growth back to the
old window then convex probing; why: friendliness to Reno flows while
scaling to high BDP), BBR (the model-based alternative: estimate
bottleneck bandwidth × min RTT (the BDP) and pace at it — immune to
bufferbloat's loss signal, but needs pacing (fq) and has the fairness
folklore). The buffers: autotuning (`tcp_moderate_rcvbuf`, `tcp_rmem`
default/max — the receive buffer grows toward the BDP on Linux up to
~6 MB; the send side likewise `tcp_wmem`) — a 2003-era Linux fix that
doubled big-transfer throughput without touching the protocol.
Bufferbloat: a queue is *memory in a router*; latency = queue occupancy
(Little's law: L = λW — a 5 MB buffer at 10 Mbps = 4 seconds of
standing latency — the number that explains "my video call freezes when
I upload"); the fix philosophy: AQM that signals *time in queue* not
queue-full (CoDel's sojourn-time drop, fq_codel as the Linux default,
CAKE); SQM on the home router. SACK (RFC 2018: the receiver reports
*which ranges* are missing — the scoreboard as a gap bitmap; without
SACK one loss drains a whole window of dupacks), RACK (time-based loss
detection — a sender-side clock, not a counting dupacks; the Linux
default RACK-TLP), head-of-line blocking (one lost segment stalls
delivery of everything after it — *the* motivation QUIC attacks, ch05).
Insiders: `ss -ti` decoded field by field (cwnd, ssthresh, rtt, retrans,
pacing_rate, delivery_rate — the diagnostic power of one command),
`tcp_retries2` (15 retries ≈ ~15-30 min before a dead peer is
declared — the "the TCP connection lived for 20 minutes after the
machine crashed" war story), TIME_WAIT (2MSL = 2×2 min on Linux — why:
the last ACK must be retransmittable and old duplicates must die; the
*local system runs out of ports* folklore: 64k ports ÷ 4-tuple —
TIME_WAIT is per-tuple, only servers with one source port exhaust; the
`tw_reuse` knob). Lab: `ss -ti` on an active transfer (every field
read), a BDP autotuning demo (transfer over a `tc netem` delayed link
with default vs maxed `tcp_wmem`), bufferbloat reproduced (`tc qdisc`
add a big fifo + `ping` under load — measure latency inflation; then
fq_codel and re-measure), `nstat` before/after for retransmits.

**Chapter 04 — userspace I/O: read, epoll, io_uring.** The copy ladder
of a traditional `read()`: DMA to the page cache, CPU copy to the user
buffer (2 copies, 2 syscalls per read; a write is the mirror; a
proxy/echo server = 4 copies + 2 syscalls per connection round trip) —
draw the ladder with the arrows. Zero-copy as copy elimination:
`sendfile()` (pages *referenced* into the socket — splice via pipe
buffers, zero CPU copies), `splice()` (the pipe as a kernel buffer
relay), MSG_ZEROCOPY (pin the user pages — the NIC DMAs straight from
user memory; the *completion* notification is required because buffers
can't be reused until the DMA finished — the async-safety insight; when
it's actually a win: >10 KB payloads on fast links, else the pin/unpin
costs more). The wait problem: blocking I/O = one thread per connection
(the stack-per-thread memory cost from module 01 ch04 returns: default
8 MB stacks × 10k connections = 80 GB of address space, though the RSS
is small — the real cost is context switching); `select` (fd_set bitmap,
FD_SETSIZE 1024 limit) / `poll` (no 1024 limit but still O(n) — scan all
fds every call; the kernel walks every fd to check readiness, twice —
user and kernel side) → `epoll` (the design insight: split registration
from waiting; `epoll_ctl` registers once, `epoll_wait` returns only the
*ready list* — the kernel maintains a per-fd callback: when data lands
(driver → sk_data_ready), the socket's wait queue fires the epoll
callback that *links the fd onto the ready list* — O(1) wait, O(ready)
return; the insider numbers: C10K was solved, Nginx at 100k conns);
level-triggered vs edge-triggered (LT: re-report until drained — safe,
polled; ET: report once per event — the user MUST drain to EAGAIN or
miss events forever; the ET+Nginx pattern, the LT starvation trap with
one busy fd; EPOLLONESHOT for multithreaded handoff). The accept side:
the SYN queue vs accept queue (backlog), SYN cookies (derived: encode
state into the ISN so no queue is needed — why flood attacks made this
default). io_uring: thesyscall-elimination design (draw the SQ and CQ
rings — producer-consumer *shared memory* mapped by both kernel and
user: submission queue entries (opcode, fd, addr, len), completion
queue entries (res, user_data); the mmap of the rings; `io_uring_enter`
with SQPOLL: a kernel thread polls the SQ so *even the submit syscall
disappears* — the endgame of the shared-ring idea that started with the
DMA ring in ch01 — the design lineage drawn explicitly); registered
buffers (fixed buffers pre-mapped — the pin cost paid once), chained
operations (LINKED_FILE... linked_sqes), the fan-fear (the security
2019... the "io_uring is banned in Chrome/Android" story — sandbox
escape surface — module 16 ch05's browser lesson noted). Kernel
bypass: DPDK (poll-mode driver: userspace owns the NIC, hugepages for
packets, pinned threads — no interrupts, no syscalls, no sk_buff; the
cost: you reimplement the stack, and one busy-poll core burns 100%),
AF_XDP in between. Insiders: when each level wins (connections vs
rate vs payload size — a decision table), the io_uring "0 syscalls"
claim decoded (what still costs). Lab: `strace -c` on a small echo
server (select vs epoll — syscall counts compared), an epoll ET echo
server skeleton in ~40 lines inline, `strace` on sendfile vs
read+write (the copy count visible in syscall args), io_uring via
`python -c` (pyring... or the liburing C example inline — document
honestly if the lib is absent).

**Chapter 05 — QUIC, RDMA, and the network as memory.** QUIC (RFC 9000,
2021) derived from its two parents: HTTP/2's head-of-line problem (one
lost TCP segment stalls *all* streams — ch03's lesson) and middlebox
ossification (the 1990s-2010s story: NATs and firewalls froze TCP's
option space — no new features deployable; the only evolvable surface
is a UDP payload, because UDP passes everything — "the ossification
forcing function" is the insider phrase). The design: streams as
*independent buffer pairs* (a loss on stream 3 stalls stream 3 only —
per-stream flow control windows, per-stream ACK ranges; the transport
now knows the app's multiplexing), integrated crypto (TLS 1.3 messages
carried in the handshake frames — no clear-text probing; transport
parameters ride the crypto), connection IDs decoupled from the 4-tuple
(a connection survives a mobile IP change — the handover story; the
4-tuple was TCP's identity, the CID is QUIC's), 0-RTT (session
resumption sends data in the first packet — derive the replay danger:
0-RTT data is replayable by any on-path recorder, so it must be
idempotent-only — the insider rule; servers reject it with
`early_data_rejected`), the userspace cost (no TCP offloads for years —
checksum, TSO, GRO all missing: the CPU tax of the first QUIC
deployments ~2× per byte of TCP; GSO-based batching as the mitigation —
the 2019-2023 story of making userspace TCP-speed), HTTP/3 as the
payoff. RDMA: the kernel-bypass endpoint (RoCE/InfiniBand; verbs: the
Queue Pair as a shared mailbox — post a Work Request, the NIC DMAs
memory directly; **memory registration** (MR): pin pages, build the
translation table *in the NIC* (its own IOMMU-like page table — module
12 ch03's GMMU cousin), the lkey/rkey; the registration cost is why
long-lived buffers are registered once), one-sided verbs (RDMA WRITE /
READ: the *remote CPU never runs* — the initiator's NIC writes the
target's memory directly through the pre-registered rkey; this is
memory as a network service — the datacenter's "one giant address
space" trend), the latency ladder extended (local NVMe ~80 µs, RDMA
READ ~1-3 µs one-way... network RTT vs RPC on top), why ML training
lives here (NCCL's ring allreduce over RDMA — module 12 ch03's NCCL
buffers return), the failure folklore (the "RDMA is brittle" story: PFC
ethernet pause storms, the "black hole" congestion; the insider:
production RoCE needs careful switch tuning or it silently degrades to
retransmit storms). The module's closing table: the full transport
taxonomy (blocking read, select, poll, epoll, io_uring, DPDK/AF_XDP,
QUIC, RDMA) × columns (copies per byte, syscalls per op, kernel in the
data path?, latency, CPU) — one table to choose a transport from, and
the latency ladder with the network levels inserted into module 01
ch02's tower (register → L1 → DRAM → NVMe → same-host RTT → cross-host
RDMA → cross-host TCP → cross-continent RTT with the ns/ms numbers).
Lab: `ping` vs TCP connect vs TLS vs QUIC handshake timing (a Python
script with `ssl` and a `aioquic` example if installed — else the
documented numbers), `ibverbs`-free: `rdma link` / `ibstat` (documented
if no RDMA hardware), the transport comparison measured on localhost:
read/write 1 MB via TCP loopback vs sendfile vs mmap+write (the copy
counts visible in timing).

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **Performance engineering literacy**: the copy ladder, epoll vs
  io_uring, zero-copy — the vocabulary behind every "why is this
  service slow" discussion.
- **Production debugging**: `ss -ti` decoded, conntrack-full incidents,
  bufferbloat diagnosis, drop-counter triage — the commands and the
  numbers they should show.
- **Design judgment**: when QUIC vs TCP, when userspace stacks, when
  RDMA — with the cost arithmetic behind each.
- **The tower completed**: the network levels of the latency ladder —
  the missing rows of module 01 ch02's table.
