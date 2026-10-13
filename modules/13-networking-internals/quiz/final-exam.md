# Module 13 final exam — Networking internals: the stack as a memory machine

Twelve questions across the module's five chapters. Closed book; every answer
is worked, not stated — the derivations are the point. Scoring: 11+ = you own
this module; 8-10 = solid, revisit the flagged chapters; <8 = the chapter
quizzes are the study path.

**Part I — wire, frame, and the DMA ring (chapter 01)**

1. **Predict.** A 40 Gbps monitoring port absorbs a 2 ms microburst of
   minimum-size frames. Derive the line rate in packets per second, the bytes
   the burst offers, the bytes a stock 256-entry RX ring of 2 KiB buffers
   absorbs, and the shortfall ratio. Where do the missing frames go, which
   single counter moves, why does `netstat -s` stay clean — and which two
   commands do you run first?

2. **Diagnose.** A SerDes team proposes deleting the line code on a new
   100 Gbps-per-lane link: "raw bits, NRZ — the receiver is clean, and we
   save the 3%." Name the two failure modes of raw NRZ, derive from a
   100 ppm oscillator error how few uninterrupted same-value bits it takes
   to break sampling, and explain what guarantee Manchester buys at 50%
   overhead that 64b/66b deliberately weakens to 3.03% — then derive the
   baud rate the lane actually runs, and what Manchester would have
   demanded at the same data rate.

3. **Diagnose.** An intrusion-detection appliance on the *sending* host of a
   busy HTTPS server raises an alert: TCP segments of 65,540 B ("someone
   configured a 64 KiB MTU — possible covert channel") with invalid
   checksums. A capture taken on the receiver shows only 1,514 B frames
   with valid checksums. Explain all three observations, derive how many
   wire frames one such segment becomes at MSS 1,460, and state what the
   CPU paid versus what the NIC fabricated. What do you run before
   attaching a pcap to a bug report?

**Part II — the kernel packet path (chapter 02)**

4. **Predict.** A game server receives TCP packets carrying 200 B of payload
   each — 254-B frames, fully linear, reserved with 66 B of RX headroom by
   the `napi_alloc_skb` path. Work the three-term truesize formula (Linux
   6.x, x86-64, 64-B cache lines), give the ratio to payload, and compare
   with the GRO-merged 64-KiB packet's ~1.01x. What does the multiplier do
   to `sk_rcvbuf` pressure, and which two escape hatches exist?

5. **Diagnose.** At 09:02 a NAT box stops accepting new SSH and health-check
   connections; sessions established before 09:00 keep working. `dmesg`
   contains one line: `nf_conntrack: table full, dropping packet`. The box
   churns 5,000 short-lived TCP connections per second. Explain the
   asymmetry, derive the steady-state table entries with the default
   timeouts and the RAM they cost, and give the immediate mitigation and
   the durable fix — including what raising only `nf_conntrack_max` costs
   you.

**Part III — TCP: the buffer machine (chapter 03)**

6. **Diagnose.** A replication link runs 25 Gbps over a 40 ms RTT path and
   measures ~800 Mbps. An admin "fixes" it by setting `SO_SNDBUF` = 8 MB on
   the sender — and throughput falls to ~43 Mbps. Derive all three numbers
   (the BDP, the autotuned ceiling, the post-"fix" ceiling), explain the
   two mechanisms the admin triggered, and give the correct fix.

7. **Predict.** A transatlantic path: RTT 80 ms, MSS 1,500 B, random loss
   p = 10⁻⁴, line rate 10 Gbps. Derive the Mathis ceiling and the
   undershoot factor vs. line rate. A colleague proposes a route with half
   the RTT; another proposes improving loss 4x. Which buys more, and why
   is the answer the whole lesson of the equation?

8. **Diagnose.** A branch office uplink is 35 Mbps; the router's FIFO holds
   5 MB. During backups the video call freezes and ping RTT climbs to
   ~1.1 s. Derive the standing latency (Little's law), name whose memory is
   full, explain why loss-based TCP *fills* it, why buying 2x bandwidth
   does not fix the call — and the class of fix that does, including the
   quantity it measures instead of queue-fullness.

**Part IV — userspace I/O (chapter 04)**

9. **Predict.** An echo server (blocking `read` + `write`, one
   already-open connection) serves 10,000 pipelined 200-B requests; ignore
   `accept`. Count the syscalls and the data-path copies the kernel
   performs. Then the same bytes served from a file with `sendfile`: which
   numbers change, which two transfers never change — and what does
   switching the event loop to epoll change about any of it?

10. **Diagnose.** A proxy migrated to edge-triggered epoll handles each
    EPOLLIN event with a single `read(fd, buf, 16384)`. Small-request
    clients work; a client that sends a 64 KB burst hangs after the first
    response, and a capture shows ~48 KB sitting in the server's kernel
    receive queue forever. Explain the mechanism, why removing `EPOLLET`
    would have masked it, and the contract the correct handler follows.

**Part V — QUIC, RDMA, and the network as memory (chapter 05)**

11. **Predict.** An HTTP/2 page load over a 40 ms path multiplexes 100
    streams on one TCP connection; one 1,400-B segment of the 64-KB
    in-flight window is lost. How many streams stall, and for how long?
    The same loss under HTTP/3, where the lost packet carried frames for 3
    streams: how many stall, and what do the other 97 do? Then the design
    question: why did the fix have to ride a UDP payload rather than a new
    TCP option or a new IP protocol number — cite the record.

12. **Concept (cross-chapter).** The module's two closing artifacts. (a)
    Sort with numbers: L1 hit, DRAM access, same-host socketpair round
    trip, in-rack RDMA READ round trip, local NVMe read (queue depth 1),
    same-DC TCP RTT, New York-London RTT — then state the two inversions
    and what each implies for systems design. (b) Name the one structure
    that appears as the NIC descriptor ring (ch01), io_uring's SQ/CQ
    (ch04), the RDMA queue pair (ch05), and the virtio vring (module 15
    ch03): give its shape, and what plays the doorbell's role in each of
    the four.

<details>
<summary>Answers</summary>

**Part I**

1. Minimum frame = 64 B, so one frame costs 8 B preamble + 64 B frame +
   12 B gap = **84 wire bytes = 672 bits**: 4 × 10¹⁰ / 672 = **59.5 Mpps**.
   The burst offers 40 Gbps × 2 ms = **10 MB**; the ring absorbs 256 ×
   2 KiB = **512 KiB** — **~19x short** (119,000 frames offered, at most
   256 posted buffers can catch them). Frames arriving with no posted
   descriptor are dropped *in the NIC, silently*: no error, no log, no UDP
   retransmit — and the kernel never saw them, which is exactly why
   `netstat -s` is clean. Only `ethtool -S`'s `rx_missed` counts them
   (names vary by driver). Run `ethtool -S eth0 | grep -i miss` and
   `ethtool -g eth0` first. Note that even `ethtool -G rx 4096` (8 MiB) is
   still short of 10 MB — the fix is the dial *plus* queues (RSS) or
   shaping, because one queue means one CPU's NAPI budget is the ceiling.
   The 64-B minimum itself is collision arithmetic: slot time 512
   bit-times, so a 2,500 m round trip plus jam returns while the sender is
   still keying (ch01).

2. Raw NRZ fails twice: a long run of one value is a flat line with zero
   edges, and the recovered clock freewheels — 100 ppm = 10⁻⁴ relative
   error, so **one full bit-time of drift in 1/10⁻⁴ = 10,000 uninterrupted
   bits**, exactly what a flat run supplies; and a DC-unbalanced stream
   shifts the baseline through the transformer's AC coupling until 0 and 1
   blur together. Manchester buys a **guaranteed edge inside every bit
   cell** — drift cannot accumulate — at 2 level periods per bit (50%).
   64b/66b weakens the guarantee *on purpose*: 2 sync bits per 64-bit
   block, then scrambling (self-synchronous, 1 + x³⁹ + x⁵⁸) supplies
   transition density *statistically* — redundancy moved from every bit to
   a block header, which is what lets overhead fall from 50% through
   4B/5B's 25% to **3.03%**. The lane runs 100 × 66/64 = **103.125 Gbaud**
   to carry 100 Gbps; Manchester at the same rate would demand **200
   Gbaud** — the 10BASE-T tax scaled up. Every speed step in Ethernet's
   history was bought by weakening the per-bit guarantee, never by
   deleting it (ch01).

3. The sender's capture sits *before* the driver, and records the API's
   lie, not the wire's truth. TSO handed the NIC one ~64-KiB skb
   (`GSO_MAX_SIZE` 65,536 = 54 B of headers + 65,482 B of payload) —
   the capture shows it as **65,535-65,542 B across drivers** (65,540 is
   the classic figure), with `CHECKSUM_PARTIAL` stubs the NIC had not yet
   filled: "invalid" checksums that existed only on the host. At MSS
   1,460, the NIC slices that skb into 44 full segments plus a 1,242-B
   tail = **45 wire frames**, fabricating each segment's IP id, TCP
   sequence numbers, and checksums in hardware — which is why the
   receiver's after-the-wire capture shows 1,514 B frames (14 + 1,500)
   with valid checksums. The CPU did **one** header and one stack
   traversal for 45 wire packets — the whole point of the offload. Before
   attaching a pcap to a bug report: `ethtool -K eth0 tso off gro off
   tx-checksumming off` — and expect throughput to drop when the CPU
   inherits the work (ch01).

**Part II**

4. truesize = SKB_DATA_ALIGN(254 + 66) + SKB_DATA_ALIGN(232) +
   SKB_DATA_ALIGN(320) = 320 + 256 + 320 = **896 B for 200 B of payload —
   4.5x** (`napi_alloc_skb` path, Linux 6.x, x86-64; the struct and
   shared-info sizes drift a few bytes per release, and a driver that
   wraps a whole page via `build_skb` charges the full page — the lesson
   is the multiplier). The GRO-merged 64-KiB packet is the mirror image:
   its payload lives in 16 full 4-KiB page frags, full pages waste
   nothing, so ~66 KiB is charged ≈ **1.01x**. `sk_rmem_alloc` charges
   *allocations*, not payloads, so a small-packet server hits `sk_rcvbuf`
   pressure **~4-6.5x sooner than payload math predicts** — visible as
   `ss -m`'s `r` sitting far above the app's unread bytes, and `d`
   climbing once it tips. Escape hatches: GRO (merge before accounting
   hurts) and, at the extreme, XDP/AF_XDP, which never builds the skb at
   all — which is why public resolvers run them (ch02).

5. **The asymmetry is lookup vs. insert.** Established flows match an
   existing conntrack entry — a hash lookup, no allocation — and pass;
   only packets that would create a *new* entry hit the full table and are
   dropped. Sessions opened before 09:00 are lookups; new SSH attempts
   and health checks are inserts. The log line is rate-limited, hence
   exactly one — grep the whole dmesg. Arithmetic: steady state = arrival
   rate × lifetime = 5,000 × 120 s (the TIME_WAIT default,
   `nf_conntrack_tcp_timeout_time_wait`) = **600,000 entries** — past the
   262,144-524,288 default `nf_conntrack_max` on an 8-16 GB host — at
   ~300 B each ≈ **180 MB of RAM**. Immediate mitigation:
   `sysctl -w net.netfilter.nf_conntrack_max=...` (runtime-writable).
   Durable fix: size `nf_conntrack.hashsize` (buckets) at boot *plus* the
   max, or `nft`'s `notrack` for the chatty flows. Raising only the max
   lengthens the hash chains against unchanged buckets — you trade drops
   for hash-chain CPU (ch02).

**Part III**

6. BDP = 25 Gbps × 40 ms = **125 MB** of in-flight bytes needed. The stock
   `tcp_wmem` ceiling is 4,194,304 B, and in-flight = rate × RTT, so the
   autotuned cap is 4,194,304 / 0.04 ≈ **800 Mbps** (~30x under line rate;
   `ss -ti` shows `sndbuf_limited` pegged, Send-Q pinned at 4 MB). The
   admin triggered two mechanisms at once: setting `SO_SNDBUF`
   **disables autotuning** for that socket, and the kernel **doubles**
   the request (headroom for per-skb overhead — the truesize idea, ch02)
   but clamps at `net.core.wmem_max` = **212,992 B**. The socket now owns
   212,992 / 0.04 ≈ 5.3 MB/s ≈ **43 Mbps** — ~19x *worse* than doing
   nothing. Correct fix: raise `net.ipv4.tcp_wmem`'s third value toward
   the BDP (and `tcp_rmem` on the receiver — the ceiling migrates through
   the four buffers), and never hand-set per-socket buffers on long-fat
   paths. The chapter's lab moves exactly this dial: 4 MB → 64 MB lifts
   the transfer 1.4-2.5x (ch03).

7. rate ≤ 1.22 × MSS / (RTT × √p) = 1.22 × 1500 / (0.08 × 10⁻²) =
   1,830 / 8×10⁻⁴ = **2.29 MB/s ≈ 18.3 Mbps**. The pipe carries 1,250
   MB/s, so the undershoot is ≈ **546x** — losing one packet in 10,000
   caps a 10 Gbps link at 0.2% of itself. Halving the RTT: ×2 → 36.6
   Mbps. Improving loss 4x: √4 = 2 → 36.6 Mbps. **Identical** — √p and
   RTT multiply against you, so the two knobs trade one-for-one at 4:1.
   That exchange rate is the equation's whole lesson: on a long lossy
   path the ceiling is the path, not the hosts, and `ss -ti`'s signature
   (retrans climbing, cwnd pinned small) tells you to look at the path
   (ch03).

8. W = L/λ: 5,000,000 × 8 / 35,000,000 = **~1.14 s of standing latency** —
   the ping climb, exactly. Whose memory is full: the *router's queue*.
   DRAM got cheap (2003-2008), vendors shipped huge FIFOs to avoid drops,
   and loss-based TCP reacts only when the queue *overflows* — so the
   equilibrium is a full queue: maximum utilization at maximum standing
   latency, with the backup's packets FIFO-ordering ahead of the call's
   (bufferbloat, Gettys & Nichols 2011). Buying 2x bandwidth halves it to
   ~0.57 s — still a dead call: **bandwidth buys rate, queue management
   buys latency**. The fix class is AQM that measures *time-in-queue*,
   not queue-fullness: CoDel drops or ECN-marks a packet whose sojourn
   exceeded a 5 ms target persistently across a ~100 ms interval;
   `fq_codel`/CAKE ("SQM" on home routers) returns ping to single-digit
   milliseconds at nearly zero throughput cost. BQL is the same law one
   level down — the driver ring capped at ~1-2 ms of line-rate bytes
   (ch03; ch02).

**Part IV**

9. Per request: `read` = 1 syscall + 2 copies (the NIC DMA into the skb,
   the CPU copy skb→user); `write` = 1 syscall + 2 copies (CPU
   user→skb, DMA skb→NIC). Totals: **20,000 syscalls and 40,000
   data-path copies**; at ~100-500 ns per transition the syscalls alone
   are milliseconds per 10,000 requests — and at ~10⁶ ops/s they eat a
   third of the CPU budget for zero bytes moved. With `sendfile`, the CPU
   copies drop to **zero** on the fast path (page-cache pages attach to
   the socket's send queue as skb fragments) and the syscall count falls
   to one per chunk. Never changed: the **two DMAs per round trip** — the
   wire must still be fed — and the per-packet skb work. Zero-copy
   deletes CPU copies, never DMAs. Epoll changes *neither* number: it
   fixes the wait (register once; a kernel callback splices ready fds
   onto the ready list — O(1) to wait, O(ready) to report), while
   sendfile/MSG_ZEROCOPY fix the copies — two orthogonal axes, and a
   proxy moved to epoll that still does `read`+`write` still pays 4
   copies per request (ch04).

10. **The leftover 48 KB generates no new edge.** The 64 KB burst was one
    arrival — one transition, one wakeup. The handler read 16,384 B and
    returned; 49,152 B remain in the kernel receive queue, and because no
    *new* data arrives, no transition ever fires and the fd never returns
    to the ready list — the capture's unread bytes, stalled forever.
    Removing `EPOLLET` masks it: level-triggered re-asks "is it ready
    *now?*" on every `epoll_wait`, so the still-readable socket is
    re-reported and even the single-read handler drains eventually (at
    the price of re-report churn and busy-fd starvation). The ET
    contract: a nonblocking fd plus **drain to `EAGAIN`** — loop `read`
    until it returns EAGAIN, then wait for the *next* edge. This is the
    top-three epoll bug, and nginx's pattern (ET + nonblocking + drain +
    move on) is what keeps one loop fair across 100k fds (ch04).

**Part V**

11. Over TCP, the lost segment opens a gap in the *single ordered byte
    stream*: every stream's frames above the gap are undeliverable until
    the retransmission lands, so **all 100 streams stall for ~1-2 RTT =
    40-80 ms** — the multiplexer knows which bytes belong to which
    request; the transport enforcing order does not, and that information
    asymmetry is head-of-line blocking. Under HTTP/3, the lost packet
    carried frames for **3 streams: those stall ~1 RTT, and the other 97
    reassemble by their own per-stream offsets and deliver** — the
    retransmission rides a *new* packet with a new, higher packet number,
    so reordering can never masquerade as loss (no dupack counting
    exists to fool). Why a UDP payload: the record says new IP protocol
    numbers die at the NAT (SCTP, protocol 132, never crossed the public
    Internet — WebRTC had to smuggle it inside DTLS-in-UDP) and new TCP
    options die in the field (Honda et al., IMC 2011, measured
    middleboxes mangling unknown options; TCP Fast Open and MPTCP both
    shipped fallback paths for exactly this). The **ossification forcing
    function**: middleboxes optimize for the protocols that already
    exist, so only traffic they pass *opaquely* can still evolve. The
    rent: userspace, no kernel offloads — ~2x the CPU per byte of kernel
    TCP in 2019-era deployments, tamed to ~1.2-1.5x by UDP GSO batching
    (ch05).

12. (a) **L1 ~1 ns < DRAM ~80-100 ns < in-rack RDMA RTT ~2-6 µs <
    same-host socketpair RTT ~10 µs < same-DC TCP RTT ~50 µs < local NVMe
    (queue depth 1) ~80 µs < New York-London ~80 ms.** Inversion one:
    cross-host RDMA beats a *local* NVMe read — someone else's DRAM over
    the network is faster than your own disk, the economic base of
    NVMe-oF and disaggregated storage. Inversion two: cross-host RDMA
    beats the same-host socket round trip — two syscall crossings cost
    more than leaving the machine, so kernel bypass pays even before
    bandwidth does. Memory stops being a property of *this* box and
    becomes an address reachable at DRAM-plus-epsilon prices. (b) The
    structure: a **fixed, power-of-two array of descriptors in memory
    shared by a single producer and a single consumer, two cursors, and
    a doorbell**. Instances and doorbells: the NIC's RX/TX descriptor
    ring — a strongly ordered MMIO store of the new tail index
    (hundreds of ns, so drivers batch refills and ring once); io_uring's
    SQ/CQ — the `io_uring_enter` syscall, which SQPOLL's kernel polling
    thread deletes exactly as NAPI deleted the interrupt; the RDMA queue
    pair — an MMIO write into the NIC's BAR-mapped doorbell register,
    completions polled as CQEs; virtio's vring — the virtqueue kick.
    Four appearances of one idea: memory as the interface between
    producer and consumer, with the doorbell optimized away — the
    module's through-line, and the sense in which the whole stack is a
    memory machine (ch01, ch04, ch05; module 15 ch03).

</details>
