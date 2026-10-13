# Chapter 03 — TCP: the buffer machine

> **The 30-second version.** TCP is one invariant: any byte handed to the network may be destroyed in transit, so the sender keeps a safety copy of every byte until the receiver proves custody — the proof is the ACK. Everything else is buffer arithmetic on that invariant: the receive window is free space in the receiver's buffer, advertised in every segment; congestion control is the sender's private model of router-queue memory it cannot see; autotuning grows both socket buffers toward the bandwidth-delay product (BDP); bufferbloat is a router's queue memory converted into your latency; SACK and RACK repair the bookkeeping when loss gets ugly. Four buffers, one invariant, every header field an index or a bound.

## Two buffers, one invariant: what each end must remember

Start one layer down. IP is *memoryless*: routers forward best-effort and keep no copy, so any packet can be dropped. Whatever retransmission TCP does must be **end-to-end**, and the copy it retransmits from must live at one of the two places that have memory — the endpoints. That fact fixes the send buffer's design:

**A send-buffer byte can be freed if and only if its sequence number is below the highest ACKed byte.** The only observable proof of custody is an ACK covering it, so the send buffer is a *retransmission safety copy* whose live region is exactly the unACKed data. This also explains the API's most common block: a blocking `send()` returns only after the kernel copies your bytes into the send buffer; the copy lands only in freed space; freed space appears only when ACKs arrive — at whatever rate the receiver and the network actually drain. Your write speed is slaved to the **ack-clock** — Van Jacobson's *self-clocking* (1988; born of the October 1986 LBL-to-Berkeley collapse from 32 kbps to 40 bps).

The receiver's mirror: TCP promises *in-order* delivery, so the receive buffer is a **reassembly queue**. Bytes below `RCV.NXT` are contiguous and deliverable to `read()`; segments above a hole are parked in an out-of-order (OOO) list indexed by sequence number. The ACK number the receiver sends is exactly `RCV.NXT` — the bottom of the first hole. Both buffers at one instant (numbers in bytes):

```
text
        SENDER                                                     RECEIVER
        send buffer, 32,000 B (safety copy)                        receive buffer, 48,000 B (reassembly queue)

        +--------------+----------------------+---------------+    +------------------------+--------------------+
        | FREED        | SENT, UNACKED        | WRITTEN,      |    | IN-ORDER, UNREAD       | FREE SPACE         |
        | 0 .. 6,000   | 6,000 .. 22,000      | UNSENT        |    | 14,000 .. 22,000       | 33,448 B           |
        | (ACK proved  | (in flight: on the   | 22,000 ..     |    | + OOO PARKED:          | (this is the       |
        |  custody)    |  wire or in a        | 32,000        |    | 23,448 .. 30,000       |  advertised rwnd)  |
        |              |  router queue)       |               |    | (hole: 22,000..23,448 |                    |
        |              |                      |               |    |  was lost)             |                    |
        +--------------+----------------------+---------------+    +------------------------+--------------------+
             ^ SND.UNA                        ^ SND.NXT                 ^ RCV.NXT = 22,000
               = 6,000                        = 22,000                  (the ACK number it sends)

        app --send()--> copies into UNSENT   segments 6,000..22,000 -->   wire / router queues
        app <--read()-- drains IN-ORDER       <-- segments arrive out of order, with a hole

        <-------- pure ACK: ack = 22,000, rwnd = 33,448, SACK 23,448-30,000 -------- (reverse direction)
```

One small ACK does **two memory-management jobs at once**: on the sender it frees buffer space (the garbage collector); toward the sender it reports the receiver's free capacity (the credit return) — flow control is nearly free because it rides segments sent anyway. Here the occupied region (8,000 + 6,552 = 14,552 B) leaves 33,448 B free, exactly the advertised window.

## Flow control: the window is someone else's free memory

The sender could trivially overrun the receiver's 48,000 B, so the receiver publishes occupancy through a channel that already exists: every segment carries a mandatory 16-bit **Window** field, which the receiver fills with *free bytes in my receive buffer right now*. Sender-side memory usage (UNACKED) is thereby matched to receiver-side capacity (FREE) — flow control in its entirety.

Two consequences need machinery. First, **the window can hit zero**: the app stops reading, the receiver advertises rwnd = 0, the sender must stop. Derive the deadlock: only a window-update ACK can re-open the window, but ACKs ride the same lossy IP as data — lose that one update and both sides wait forever, the receiver silent (nothing to send), the sender forbidden to ask. The fix is the **persist timer**: the sender keeps probing, and RFC 793 lets each probe carry 1 byte past the zero window (the byte at `SND.NXT`) precisely so the receiver must answer with the current window; each successful probe advances the ACK number by exactly 1 byte. The receiver cooperates via RFC 1122's silly-window-syndrome rule: never advertise increases smaller than `min(MSS, rcvbuf/2)`.

Second, **the field is 16 bits, capping the window at 65,535 B**. The window must cover the BDP — the bytes in existence between the hosts while the ack-clock makes one round: in-flight = rate × RTT. At 40 ms RTT, 64 KiB caps a connection at 65,536 / 0.04 ≈ **13.1 Mbps** regardless of the pipe; on a fat pipe, 10 Gbps × 40 ms = 50 MB in flight — a 64 KiB window starves the link ~800×. RFC 1323 (1992) window scaling fixes it: in the SYN — *only* the SYN, a rigidity that matters in ch05 — each side sends a shift count, 0-14, applied to every window field thereafter. Scale 14 tops out at 65,535 × 2^14 ≈ 1.07 GB; Linux, with a ~6 MB receive-buffer ceiling, typically negotiates shift 7 (65,535 × 128 ≈ 8.4 MB). The header still says 16 bits; both ends multiply.

## Congestion control: a model of memory the sender cannot see

Flow control bounds the sender by the *receiver's* RAM. But the middle of the network also has memory — router queues — and no router will ever report its occupancy. Congestion control is the sender's **private model of memory it cannot observe**, held in `cwnd`; the real send bound is `min(rwnd, cwnd)` — two limits, two different memories, one comparison.

The model is inferred from two signals: ACKs arriving (the network delivers) and loss (a queue overflowed). **Slow start** grows it fast: the initial window was 1 segment for decades, 2 in RFC 2581, and 10 × MSS (≈ 14.6 KB) since RFC 6928 (2011) — Linux defaults to 10. Each ACK frees a new slot, so cwnd doubles per RTT: 10 → 20 → 40 → 80 → 160 → 320 segments in 5 RTT (200 ms at 40 ms), until ssthresh hands over to congestion avoidance. **AIMD** is the steady state: +1 MSS per RTT, cut to ½ on loss. The sawtooth has a computable price — the Mathis equation (SIGCOMM 1997): cwnd cycles W → W/2, one loss per cycle, about (3/4)·W² packets delivered per cycle, so p ≈ 4/(3W²) and rate = W·MSS/RTT, which rearranges to:

```
text
rate <= (1.22 * MSS) / (RTT * sqrt(p))          (Mathis 1997; C ~ 1.22 for the simple model)
```

Work it on a fat pipe: MSS 1500 B, RTT 40 ms, p = one loss per million (10⁻⁶): rate ≈ 1.22 × 1500 / (0.04 × 0.001) ≈ 45.75 MB/s ≈ **366 Mbps**. A 10 Gbps link reduced to 3.7% of itself by losing one packet in a million — the number that gave the equation its name.

**CUBIC** (Linux default since 2.6.19, 2007; Windows since 10 1709; Apple ~2018-2019) replaces linear regrowth with a cubic in time since last loss: W(t) = C·(t − K)³ + W_max, C = 0.4, decrease β = 717/1024 ≈ 0.70 (not Reno's ½) — concave first, sprinting back toward W_max, then convex, probing past it. The high-BDP win: W_max is a *memory of the old window*, so one loss doesn't reset ambition, while the region below W_max stays friendly to coexisting Reno flows.

**BBR** (Google, 2016) abandons loss as signal: a windowed max filter on delivery rate (bottleneck bandwidth, ~6-10 RTT) times a windowed min filter on RTT (10 s) gives the BDP, and it paces at that rate with in-flight ≈ 2×BDP — immune to the loss signal a bloated buffer pollutes (section 5). But it *must* be paced (the `fq` qdisc, or internal pacing since Linux 4.13; module since 4.9, not default), and the fairness folklore is real: BBRv1 was measured walking over loss-based flows in shallow buffers; BBRv2/v3 target that.

## Autotuning: sizing the buffers to the BDP, from inside

The window can only be as large as the buffers behind it. Pre-2003 Linux shipped fixed, small socket buffers, and long-fat transfers ran at whatever `buffer / RTT` allowed. The fix — buffer autotuning (Heffner-era work, Linux 2.6.7 timeframe, 2003-2004) — is pure memory policy, no wire change: the kernel measures the achieved rate and grows the buffer toward the BDP, the advertised window in step; contemporary long-path measurements saw roughly 2× bulk throughput. The knobs (Linux 6.x):

```
console
$ sysctl net.ipv4.tcp_rmem net.ipv4.tcp_wmem net.ipv4.tcp_moderate_rcvbuf
net.ipv4.tcp_rmem = 4096 131072 6291456      <- min, default, MAX (receive; ~6 MB ceiling)
net.ipv4.tcp_wmem = 4096 16384 4194304       <- min, default, MAX (send; ~4 MB ceiling)
net.ipv4.tcp_moderate_rcvbuf = 1             <- autotuning on
```

The third number of each triple is the ceiling autotuning won't cross — the number that quietly caps WAN throughput: 4 MB of send buffer over 40 ms supports 4,194,304 / 0.04 = 800 Mbps; over 60 ms, ~560 Mbps; filling 10 Gbps × 40 ms needs ~50 MB. A datacenter's 100 Gbps × 100 µs is only 1.25 MB — stock defaults already overshoot there; autotuning is a *long-path* tool. One landmine: setting `SO_SNDBUF`/`SO_RCVBUF` **disables** autotuning for that socket, and the kernel *doubles* your requested value (headroom for per-skb overhead — the truesize idea from ch02) while clamping at `net.core.wmem_max` (212,992 B stock). The lab shows both in 3 lines of Python.

## Bufferbloat: a queue is memory, and latency is its occupancy

Every queue in this story is memory — router, qdisc, driver ring — and Little's law (W = L/λ) prices it: standing occupancy L at drain rate λ gives waiting time W. The example that explains a decade of home misery: a 5 MB buffer on a 10 Mbps link holds 5,000,000 × 8 / 10,000,000 = **4 seconds** of standing latency — your video call's packets sit in FIFO order behind 4 seconds of bulk upload. This is bufferbloat (Gettys & Nichols, *ACM Queue*, 2011): DRAM got cheap around 2003-2008, vendors shipped huge FIFOs to avoid drops, and loss-based TCP only reacts when the queue *overflows* — so equilibrium is a full queue: maximum standing latency at full utilization. The fix philosophy: **AQM that measures time-in-queue, not queue-fullness**. CoDel (Nichols & Jacobson, 2012) drops or ECN-marks when a packet's *sojourn time* exceeded a 5 ms target persistently across a ~100 ms interval — under load the queue converges to milliseconds of occupancy. `fq_codel` (CoDel per flow, plus fair queueing) is the Linux default qdisc since 3.5 (2012); CAKE (4.19, 2018) is the home-uplink descendant; on a home router the feature is called SQM, fixing "the call freezes when someone uploads" at nearly zero throughput cost. BQL (ch02) is the same idea one level down (driver ring capped at ~1-2 ms of bytes); BBR is the same idea one level up.

## SACK, RACK, and the hole that stalls everything

The safety copy needs accurate bookkeeping of *which* bytes are missing, and the cumulative ACK is nearly blind: it says only "everything below X." Lose one segment and every later segment produces a duplicate ACK carrying no new information — one loss drains a whole window of dupACKs that tell you almost nothing. With two holes, NewReno repairs one per RTT; deeper loss falls to a full RTO (collapse to the initial window, seconds of stall). **SACK** (RFC 2018, 1996) fixes the reporting: the receiver appends the received-but-not-contiguous ranges to the ACK — up to 3 blocks of 8 B each with timestamps negotiated (4 without; the 60 B header budget minus the 12 B timestamp option). The sender folds them into its **scoreboard**, a gap bitmap over the send buffer, retransmitting only holes. **RACK** (RFC 8985, 2021; Linux default RACK-TLP since the ~4.15 era) replaces counting with time: *if a segment sent later was (S)ACKed, a still-missing segment sent an RTT or more earlier is lost* — robust to reordering, which dupACK counting misreads. Companion TLP (tail loss probe) fires one probe after ~2×SRTT of tail silence, so a lost final segment doesn't wait for a full RTO.

And the cost no sender cleverness removes: **head-of-line blocking**. In the diagram, 6,552 B are parked behind the 1,448 B hole and `read()` gets none of them — the in-order promise means one lost segment stalls delivery of everything after it, even bytes already in the receive buffer. Per-message latency collapses at 99.9% delivery. This — not throughput — is the motivation QUIC attacks with per-stream buffers (ch05).

## What insiders know

- **`ss -ti` is a whole diagnosis in one line** — decoded field-by-field in the lab. The killer pair on Linux 5.x+ is `rwnd_limited:%`/`sndbuf_limited:%`: percentages of connection life blocked by the receiver's window vs. your own send buffer. That pair alone splits "receiver too slow" from "buffer too small."
- **`tcp_retries2` = 15 (default)**: a dead peer is declared only after ~15 exponentially backed-off retransmissions — RTO doubles from a ~200 ms floor (Linux `TCP_RTO_MIN`) toward a 120 s cap, roughly 13-30 minutes. That is the "the connection lived for 20 minutes after the machine crashed" war story; production code sets `TCP_USER_TIMEOUT` (Linux 2.6.37+, milliseconds) below the load balancer's 30-90 s idle timeout.
- **TIME_WAIT is not 4 minutes on Linux**: RFC 793's textbook is 2×MSL, MSL 2 minutes (240 s); Linux hardcodes 60 s (`TCP_TIMEWAIT_LEN`); old Windows 240 s, modern 60-120 s. The rationale stands: the closer must be able to re-ACK a retransmitted FIN, and old duplicates must die before the 4-tuple is reused. Port folklore: ~28,232 ephemeral ports exist (`ip_local_port_range` 32768-60999) but per 4-tuple — exhaustion means one client address hammering one service address:port (the load-test / egress-proxy failure, not a server one). `tcp_tw_reuse=1` (with timestamps) recycles TIME_WAIT for outgoing connections; `tcp_tw_recycle` was removed in 4.12 for breaking NATed clients.
- **The zero-window probe violates the window by exactly 1 byte** (RFC 793), and SWS avoidance (RFC 1122) is why a window never creeps open one byte at a time in a capture.
- **`mss:1448` means timestamps are on**: 1500 − 20 (IP) − 20 (TCP) − 12 (RFC 1323 option) = 1448. And **iperf3 may pin the buffers you meant to measure**: `-w` sets `SO_SNDBUF`/`SO_RCVBUF` (disabling autotuning); verify what the socket actually has (`ss -m`) — half the slow-transfer tickets in history measured a benchmark's own 64 KB window.

## See it yourself

Any Linux box; root for the namespace tricks. Ten minutes.

**1. The SO_SNDBUF doubling:**

```
python
import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 100000)
print(s.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF))   # 200000: Linux doubles the request
s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
print(s.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF))   # 212992: clamped at net.core.wmem_max
```

**2. Build a 40 ms link and watch `ss -ti` during a transfer** (note `limit 10000` — netem's default 1,000-packet queue is itself below the BDP):

```
bash
sudo ip netns add far
sudo ip link add v0 type veth peer name v1
sudo ip link set v1 netns far
sudo ip addr add 10.99.0.1/24 dev v0 && sudo ip link set v0 up
sudo ip netns exec far ip addr add 10.99.0.2/24 dev v1
sudo ip netns exec far ip link set v1 up
sudo tc qdisc replace dev v0 root netem delay 40ms limit 10000
sudo ip netns exec far iperf3 -s
iperf3 -c 10.99.0.2 -t 60          # another terminal
```

While it runs, a third terminal: `ss -ti dst 10.99.0.2`. Typical fragment (Linux 6.x, CUBIC):

```
console
ESTAB 0 2621440 10.99.0.1:41624 10.99.0.2:5001
cubic wscale:7,7 rto:480 rtt:40.312/1.87 ato:40 mss:1448 cwnd:121 ssthresh:220
bytes_acked:35316544 pacing_rate 1.27Gbps delivery_rate 1.12Gbps busy:22014ms
rcv_space:65535 unacked:121 retrans:0/0
```

Field by field: `wscale:7,7` — both sides shift windows ×128 (to ~8.4 MB); `rto:480` — retransmission timeout (ms) = SRTT + 4×dev, clamped; `rtt:40.312/1.87` — smoothed RTT / mean deviation (ms); `ato:40` — delayed-ACK timer (Linux 40 ms floor); `mss:1448` — timestamps negotiated; `cwnd:121` — congestion window in segments; `ssthresh:220` — slow-start threshold; `unacked:121` — segments in flight (the UNACKED region); `pacing_rate 1.27Gbps` — pacing engine's send rate; `delivery_rate 1.12Gbps` — max recently ACKed rate, what receiver and network actually drained (BBR's metric); `busy:22014ms` — time actively sending; `rcv_space:65535` — receive autotuner's target; `retrans:0/0` — zero retransmissions, zero re-retransmitted. The `Send-Q` column (2621440) is send-buffer occupancy: unACKed + unsent, the diagram's two live regions.

**3. Watch the wmem ceiling move.** Default `tcp_wmem` max = 4 MB; 4 MB / 40 ms ≈ 800 Mbps is the prediction:

```
bash
iperf3 -c 10.99.0.2 -t 20                 # expect ~700-900 Mbps (wmem-capped; verify via ss -ti)
sudo sysctl -w 'net.ipv4.tcp_wmem=4096 16384 67108864'
iperf3 -c 10.99.0.2 -t 20                 # expect 1.4-2.5x more (netem costs CPU; ranges honest)
```

Raise `tcp_rmem` inside the netns too and the ceiling moves again — the bottleneck migrates through the four buffers, and `sndbuf_limited:%` tells you which one you just fixed.

**4. Reproduce bufferbloat, then kill it.** 10 Mbps behind a 5 MB byte-FIFO:

```
bash
sudo tc qdisc replace dev v0 root handle 1: htb default 10
sudo tc class add dev v0 parent 1: classid 1:10 htb rate 10mbit
sudo tc qdisc add dev v0 parent 1:10 handle 10: bfifo limit 5000000
iperf3 -c 10.99.0.2 -t 30 &  ping -i 0.5 10.99.0.2
```

```
console
64 bytes from 10.99.0.2: icmp_seq=2 ttl=64 time=0.41 ms     <- before the queue fills
64 bytes from 10.99.0.2: icmp_seq=40 ttl=64 time=3971.66 ms  <- 5 MB / 10 Mbps = ~4 s standing latency
```

Same memory, different eviction policy:

```
bash
sudo tc qdisc replace dev v0 parent 1:10 handle 10: fq_codel
iperf3 -c 10.99.0.2 -t 30 &  ping -i 0.5 10.99.0.2
```

Ping returns to single-digit milliseconds while iperf3 still reports ~9.5-9.9 Mbps: latency collapses, throughput survives. Cleanup: `sudo ip netns del far; sudo ip link del v0`.

**5. Retransmit counters, before/after.** `nstat -az | grep -iE 'retrans|sack|collapse'` before and after any transfer; the lines that move tell the story — `TcpRetransSegs` (total), `TcpExtTCPSackRecovery` (fast recovery via SACK), `TcpExtTCPLostRetransmit` (a retransmission itself lost — a bad path).

## How to use this knowledge

- **The slow-transfer triage tree.** During the transfer, `ss -ti`: `sndbuf_limited` pegged → your own buffer; raise `tcp_wmem` max or fix `SO_SNDBUF` (autotuning is off when you set it). `rwnd_limited` pegged → the receiver isn't draining — the bug is in *their* read loop, and no sysctl saves you. `retrans` climbing with cwnd pinned small → loss; the Mathis arithmetic gives the ceiling, so look at the path, not the hosts.
- **Capacity planning in one multiplication.** BDP = rate × RTT: 100 Gbps × 100 µs (datacenter) = 1.25 MB — stock defaults suffice; 10 Gbps × 40 ms (cross-continent) = 50 MB — plan it, because the 6 MB receive / 4 MB send autotune ceilings cap you at ~800 Mbps.
- **"Zoom freezes when I upload" is now a diagnosis.** Reproduce with ping-under-load (lab 4); the fix is AQM/SQM (fq_codel/CAKE) on the full egress — seconds of latency back at nearly zero throughput cost.
- **Ghost connections.** A peer dies hard: the connection lives another ~13-30 minutes of RTO backoff. Set `TCP_USER_TIMEOUT` below your load balancer's idle timeout, or expect connection-table exhaustion while the kernel retransmits into the void.

## Connections

- **Module 13 ch01** — the DMA ring's rule ("a buffer stays occupied until the consumer frees it") is the ACK's rule.
- **Module 13 ch02** — truesize vs len is why `rmem` pressure and the advertised window react to real allocation cost; BQL is bufferbloat at driver level.
- **Module 13 ch04** — epoll's EPOLLIN fires on the receive-queue transition this chapter's diagram describes; a full send buffer is when EPOLLOUT matters; zero-copy changes who owns the safety copy.
- **Module 13 ch05** — head-of-line blocking and SYN-only option negotiation are the two wounds QUIC was designed around; per-stream windows are this chapter's arithmetic, multiplied.
- **Module 01 ch02** — the latency tower's network rows (the 40 ms in every BDP computation here).
- **Module 15 ch03 (queued)** — virtio's vring is the ch01 producer-consumer ring between guest and host, governed by the same occupancy rule; RDMA replaces this whole machine with pre-registered memory.

## Quiz

1. A colleague proposes: "Once the NIC has DMA'd a send-buffer segment onto the wire (ch01), free the buffer slot — the network has the bytes now." Which TCP guarantee dies, what invariant does the safety copy maintain, and what does a 1-in-10,000 loss event look like to the app afterward?
2. A Reno/AIMD flow runs a 10 ms RTT path, 10 Gbps line rate, MSS 1500 B, random loss p = 10⁻⁴. Predict the Mathis ceiling, show the arithmetic, and the undershoot factor vs. line rate.
3. Both SYN packets carry window-scale shift 7. Mid-connection the receiver advertises a Window field of 20,000. How many bytes may the sender have in flight per that advertisement, and the largest window representable at shift 14?
4. A server writes a 100 KB response to a client whose app never calls `recv()`, so the advertised window falls to zero — and one early window-update ACK is lost. What mechanism prevents a permanent silent deadlock, what does the server transmit, and what does the client's ACK number do over time?
5. Diagnose the number: `iperf3` on a 10 Gbps path with 60 ms RTT reports throughput stuck near 560 Mbps. `ss -ti` shows `retrans:0/0`, cwnd in the thousands, pacing_rate near 560 Mbps, `sndbuf_limited` high, Send-Q pegged around 4 MB. Name the limiting buffer, show the arithmetic, and give the fix.
6. Your home uplink is 20 Mbps; the router's FIFO holds 1.5 MB. A backup saturates the uplink and your video call freezes. Predict the ping RTT during upload (show the arithmetic), name whose memory is full, and the class of fix — and why "buy more bandwidth" is the wrong first move.
7. An ACK performs two distinct memory-management operations on two different machines. Name both, and explain why the rwnd rides in *every* segment rather than being requested when needed — what would a request-based scheme do to the ack-clock?
8. (Cross-chapter, ch02.) A receiver's socket holds 5 MB of unread payload (`len`) across its in-order queue and OOO park, yet `ss -m` shows the socket's `rmem` around 6.5-7.5 MB. Which ch02 concept explains the gap, what does the gap consist of, and why must the window/occupancy arithmetic use the larger number?

<details><summary>Answers</summary>

1. **The retransmission guarantee dies.** The invariant: *every byte with sequence ≥ SND.UNA stays byte-for-byte recoverable until an ACK covers it* — the send buffer is the only copy of unACKed data in the universe; IP keeps none (ch01's rings recycle immediately). So a 1-in-10,000 loss means: the cumulative ACK stalls forever at the hole, the RTO fires with nothing to retransmit, and after `tcp_retries2` (15, ~13-30 min) the connection aborts — the app sees a stream stop mid-response, data destroyed in transit with no error at the moment of destruction.

2. **≈ 18.3 MB/s ≈ 146 Mbps — about 68× below line rate.** rate ≤ 1.22 × MSS / (RTT × √p) = 1.22 × 1500 / (0.01 × √10⁻⁴) = 1830 / 10⁻⁴ = 18,300,000 B/s ≈ 146 Mbps. The pipe carries 1,250 MB/s; 1250 / 18.3 ≈ 68×. Note the shape: halving RTT helps as much as a 4× loss improvement — √p and RTT multiply against you.

3. **2,560,000 B; ≈ 1.07 GB.** RFC 1323: the receiver right-shifts its true window by 7 before filling the field; the sender left-shifts on use: 20,000 × 128 = 2,560,000 B of permitted in-flight beyond SND.UNA. At shift 14: 65,535 × 2¹⁴ = 1,073,725,440 B ≈ 1.07 GB. The shift can only be negotiated in the SYN — options only exchange reliably there, exactly the ossification pressure that pushed QUIC to a fresh option space (ch05).

4. **The persist timer.** On rwnd = 0 the sender arms it and periodically sends a zero-window probe; RFC 793 lets the probe carry the single byte at SND.NXT — 1 byte *past* the zero window — so the receiver must answer. The client ACKs each probe, and since the probe byte is legal data the ACK number advances by exactly 1 byte per probe. If the client never drains, its ACKs keep advertising rwnd 0 and the probes back off exponentially until the connection is aborted (the `tcp_retries2`-style limit, ~15 attempts over 13-30 min). Without the persist timer, the lost update deadlocks both sides: receiver silent, sender forbidden to ask.

5. **The sender's send buffer (the autotuning ceiling), not the network.** Default `tcp_wmem` max = 4,194,304 B, and in-flight = rate × RTT, so the cap is 4,194,304 / 0.06 ≈ 69.9 MB/s ≈ 560 Mbps — matching pacing_rate and the pegged Send-Q. `retrans:0/0` rules out loss; huge cwnd rules out congestion control. Fix: raise `net.ipv4.tcp_wmem`'s third value (and `tcp_rmem` on the receiver — the ceiling migrates), or a large `SO_SNDBUF` knowing it disables autotuning. The BDP is 10 Gbps × 60 ms = 75 MB, so 75+ MB buffers are the target.

6. **~0.6 s of RTT; the router's queue memory is full; the fix is AQM.** 1.5 MB = 12,000,000 bits; 12,000,000 / 20,000,000 bps = 0.6 s standing latency (W = L/λ). Loss-based TCP fills the FIFO to the top because it only reacts at overflow. Buying 2× bandwidth halves the latency (still ~0.3 s — still a dead call), while AQM (CoDel sojourn signaling, fq_codel/CAKE, "SQM" in router UIs) drops the standing queue to single-digit milliseconds at almost no throughput cost. Bandwidth buys rate; only queue management buys latency.

7. **It frees sender memory and returns receiver credit.** (a) On the sender, the ACK number is a GC proof: every send-buffer byte below it becomes reusable — the FREED region's edge moves right. (b) The rwnd reports the receiver's current free bytes, so the in-flight bound tracks the receiver's drain rate. It rides every segment because the 16-bit field is mandatory header space (zero marginal cost), the value changes constantly as the app reads, and — the deep reason — the ack-clock is the pacing mechanism: a request-based scheme would insert a round trip of silence between grants, stalling the clock. Every ACK is receipt, invoice, and metronome at once.

8. **Truesize vs len (ch02).** `sk_rmem` charges each skb's *real* allocation — payload plus headroom, the `sk_buff` structure, `skb_shared_info`, allocator alignment — not payload bytes. A 1,448 B segment typically costs ~2-2.5 KB of truesize; coalescing (GRO / `tcp_try_coalesce`) can pull the ratio toward ~1.2×, hence 6.5-7.5 MB for 5 MB of len. The arithmetic must use the larger number because the physical constraint is memory, not payload: `rmem` limits, pressure checks, and the autotuner's targets all operate on real bytes. A receiver computing rwnd from len would over-advertise and OOM its host — a `tcp_mem` pressure failure, not a protocol event.

</details>
