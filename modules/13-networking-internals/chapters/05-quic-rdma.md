# Chapter 05 — QUIC, RDMA, and the network as memory

> **The 30-second version.** QUIC (RFC 9000, 2021) is TCP rebuilt where middleboxes cannot touch it: as a UDP payload, in userspace. Two parents: HTTP/2's head-of-line jam (one lost 1,400 B segment stalls all 100 multiplexed streams — chapter 03's lesson) and TCP's ossified option space (NATs and firewalls froze it; only a UDP payload still evolves, because UDP passes everywhere). QUIC's answer: streams as independent buffer pairs (a loss stalls only the streams whose frames rode that packet), connection IDs that outlive the 4-tuple (the connection survives an IP change), TLS 1.3 folded inside (transport options encrypted, hundreds of bytes versus TCP's 40 B budget), and 0-RTT resumption — replayable by any on-path recorder, hence idempotent-only. The bill is userspace: no kernel offloads, ~2x the CPU per byte of kernel TCP in 2019-era deployments, tamed to ~1.2-1.5x by UDP GSO batching. RDMA removes the CPUs entirely: memory registration builds a page table in the NIC, and a one-sided RDMA WRITE has the initiator's NIC write the target's RAM through a pre-shared 32-bit key — the remote CPU executes 0 instructions. The module closes with the transport taxonomy and the completed latency ladder.

## Two parents: a jammed multiplexer and a frozen protocol

**Parent 1: the jam.** HTTP/2 (RFC 7540, 2015) multiplexed many requests over one TCP connection to amortize the handshake — and inherited chapter 03's worst property: TCP delivers one ordered byte stream, so a lost segment stalls everything *after* the gap, no matter which stream those bytes belong to:

```text
  HTTP/2 over TCP: one byte stream, one reassembly point

  streams 0, 4, 8 ... 99 --frames--> [ TCP byte stream ] --segments--> wire
                                            |
                                     [ TCP reassembly queue ]
                                            |
        a 1,400 B segment is lost at byte offset 1,204,352:
        delivery to ALL 100 streams halts at that gap until
        the retransmission lands (~1-2 RTT = 40-80 ms on a 40 ms path).
        The video frame, the API call, the favicon: all parked.
```

The multiplexer knows which bytes belong to which request; the transport enforcing order does not. That information asymmetry *is* head-of-line blocking. The fix must make the transport stream-aware.

**Parent 2: the freeze.** "Make TCP stream-aware" has an obvious implementation — new TCP options, or a new transport protocol number. Both were tried, and the record is the insiders' ossification story:

- TCP options die in the field: Honda et al. ("Is It Still Possible to Extend TCP?", IMC 2011) measured middleboxes stripping even harmless unknown options; TCP Fast Open (RFC 7413, 2014) shipped a fallback path for exactly this reason; MPTCP (RFC 8684, 2020) carries a fallback bit for the same one.
- New protocol numbers die at the NAT: SCTP (RFC 2960, 2000) offered multistreaming and multi-homing — nearly the QUIC feature list, two decades early — but runs as IP protocol 132, which consumer NATs and firewalls drop; WebRTC had to smuggle it as SCTP-inside-DTLS-inside-UDP.

The general law is the insider phrase **the ossification forcing function**: middleboxes optimize for the protocols that exist, so only traffic they pass *opaquely* can still evolve — a UDP payload. UDP has no options, nothing to validate, nothing to break; after the first packet opens the NAT pinhole, arbitrary bytes flow. QUIC colonizes the last deployable ground, and section 3 is the rent.

## The redesign: streams, connection IDs, and crypto inside the payload

QUIC's header fits in your head: a 1 B flags byte, a 4 B version, DCID and SCID (each a 1 B length plus 0-20 B of connection ID), then frames. Everything else — ack logic, flow control, loss recovery, the handshake itself — is frames inside an always-encrypted payload. Three decisions carry the chapter.

**Streams are independent buffer pairs.** A stream is a pair of byte-offset buffers with its own flow-control window. Stream IDs are 62 bits; the low 2 bits encode direction and originator (0x00 client-bidi, 0x01 server-bidi, 0x02 client-uni, 0x03 server-uni). The receiver reassembles per stream by offset and grants credit with MAX_STREAM_DATA frames; a connection-wide MAX_DATA caps the total. Loss detection is per packet number, but the *damage* is per stream:

```text
  QUIC: one connection, N independent reassembly points

  stream 0 (control)     stream 4 (video)      stream 8 (API)
      |                      |                     |
  [ rcv buf, offsets ]  [ rcv buf, offsets ]  [ rcv buf, offsets ]
  MAX_STREAM_DATA: 64 KB  3 MB                128 KB
      |                      |                     |
  a lost packet carried frames for streams 4 and 8 ONLY:
  those two stall ~1 RTT; stream 0's data still delivers.
      \                     |                    /
       `------ ACK frames: ranges of packet numbers ------'
                          |
              [ connection window: MAX_DATA, e.g. 8 MB ]
                          |
                   UDP payload --> wire
```

One lost packet stalls the streams whose frames were inside it — typically 1-3 on a busy connection — while the other 97 deliver; the information asymmetry that jammed HTTP/2 is gone. Three sub-decisions follow from the buffer-pairs view: ACK frames carry *ranges* (SACK is not an option in QUIC, it is the format — chapter 03's scoreboard becomes the native ACK); the receiver acknowledges at least every 2nd ack-eliciting packet (every 3rd with ECN counts), holding ACKs at most max_ack_delay, default 25 ms; and loss recovery (RFC 9002) is time-based — a probe timeout, no dupack counting — with retransmissions always riding a *new* packet with a new, higher packet number, so reordering can never masquerade as loss the way a triple-dupack can in TCP.

**The connection ID replaces the 4-tuple.** Chapter 02's kernel identifies a TCP connection by the 4-tuple — it *is* the socket-lookup hash key. QUIC's identity is the CID, 0-20 B in every packet header, chosen by the receiver: your laptop roams Wi-Fi to LTE, the 4-tuple evaporates, and the connection does not. The client sends on the new path; the server, recognizing the CID, issues PATH_CHALLENGE (8 B random), the client echoes PATH_RESPONSE, and the connection continues with per-path congestion state reset. NEW_CONNECTION_ID rotates identifiers (Retire Prior To retires old ones), defeating linkability by network observers.

**Crypto is inside the transport.** TLS 1.3 (RFC 8446, 2018) records ride in QUIC handshake frames, so transport and crypto handshakes share round trips: 1 RTT to first application data, versus 2 RTT for TCP connect plus TLS 1.3. Transport parameters — QUIC's TCP-options — ride encrypted: hundreds of bytes of TLVs with no fixed ceiling versus TCP's 40 B option budget, and no middlebox can strip what it cannot parse. One honesty note: Initial packets are "encrypted" with keys derived from a public version salt plus the DCID (RFC 9001) — obfuscation, not confidentiality, so middleboxes cannot read or mangle parameters mid-flight. Real secrecy starts at the handshake proper.

**0-RTT: the replayable fast lane.** On resumption the first flight carries the handshake *and* early data — application bytes in packet 1, 0 extra RTT. The danger, derived: that flight was recorded by every on-path recorder (a passive tap suffices) and replays verbatim minutes later; the server processes it again unless it defends. Hence the insider rule: **0-RTT data must be idempotent-only** — safe-method GET semantics, nothing that decrements inventory or charges a card. Servers can reject the lane; TLS surfaces this as early-data rejection, libraries as `early_data_rejected`, and a correct client retransmits at 1-RTT. Ticket anti-replay interacts badly with load-balancer ticket pools — the idempotency rule, not the tickets, is the real boundary.

**HTTP/3 (RFC 9114, 2022) is the payoff**: HTTP/2's multiplexing over a transport that cannot jam across streams, plus migration and 0-RTT. Firefox 91 (2021) enabled it by default; Chrome's default-on wave followed in 2022.

## The userspace tax: paying the kernel's bills from your own process

QUIC lives in your process. Chapter 02's receive path — NAPI, `sk_buff` allocation, GRO merging, socket queues — and chapter 03's offloads do not run for it. The kernel sees UDP: checksum validation, socket demux, roughly it. No TSO: your process builds every packet's bytes itself. No GRO on the payload: packets arrive one datagram at a time. Every packet costs a `sendmsg`/`recvmmsg` syscall plus userspace TLS-record processing.

The ledger from the first deployments: **~2x the CPU per byte of kernel TCP** (Google's 2019-era reports; varies by CPU and NIC). A ~2x tax on the world's busiest web servers is a deployment blocker, so 2019-2023 is the story of buying the offloads back:

- **GSO batching** (UDP_SEGMENT, Linux 4.18, 2018): build ~64 KB of QUIC packets in one userspace buffer, hand it over in one syscall, and the kernel slices it into ~48 MTU-sized datagrams, fabricating each UDP/IP header and checksumming in bulk. Cloudflare's 2019 writeup ("Accelerating UDP packet transmission for QUIC") made this the standard trick; quiche, lsquic, ngtcp2, and msquic all batch this way.

```c
/* The one-line version of the trick that halved the userspace tax. */
uint16_t seg = 1350;              /* bytes per outgoing UDP datagram     */
setsockopt(fd, SOL_UDP, UDP_SEGMENT, &seg, sizeof seg);
/* now sendmsg() one 64 KB buffer; the kernel slices + checksums it,
   and the NIC sees ~48 normal datagrams (GSO on the TX side). */
```

- **Receive-side batching** (`recvmmsg`, GRO for UDP on one flow, multiple packets per wakeup) plus AES-GCM with AES-NI/VAES brought production stacks to **~1.2-1.5x kernel TCP by 2023** (reported ranges — hardware-dependent).
- The chapter 04 lesson applies: DPDK-style full bypass exists for QUIC too, but almost nobody pays it — GSO-batched userspace on kernel UDP stays within ~1.5x while keeping routing, firewalling, and conntrack, the chapter 02 ecosystem your ops team already monitors.

The tax never fully disappears; it is the price of the freeze: you moved the stack into userspace *because that was the only deployable place*, and the deployable place has no offloads.

## RDMA: memory registration, and the remote CPU that never runs

QUIC removed the ossified protocol but kept the kernel in the data path. RDMA removes the kernel from the data path — then removes the *remote CPU* too. The lineage: InfiniBand (IBTA 1999, spec 1.0 in 2000) made network endpoints memory regions, not sockets; RoCE v1 (2010) carried it on raw Ethernet (ethertype 0x8915); RoCE v2 (2014) wrapped it in UDP/IP — port 4795, the IANA-registered `roce-2` port — so it routes. The API is "verbs" (libibverbs), user-space from the start.

**The Queue Pair is chapter 01's ring again.** A QP is a pair of work queues: you post a Work Request (WR — a descriptor: opcode, local address, length, key, remote address), then ring a doorbell register MMIO-mapped from the NIC's BAR into your process. The NIC DMAs the descriptor and — the point — the *payload* directly from your registered memory; completions appear as CQEs on a Completion Queue you poll. A QP walks RESET → INIT → RTR (ready-to-receive) → RTS, with setup exchanged out-of-band. Producer-consumer ring, MMIO doorbell, async completion: the exact shape of the DMA ring (ch01), io_uring's SQ/CQ (ch04), and the virtio vring (module 15 ch03) — the fourth member of the family.

**Memory registration is the permission model — and the picture worth drawing.** A NIC that DMAs arbitrary process memory would be a security hole, so the verbs API requires you to *register* (pin + translate) any buffer the NIC will touch:

```text
  memory registration: the NIC gets its own page table for your buffer

  process memory (1 GB, scattered 4 KB user pages)    inside the NIC
  +------------------------------+                 +------------------------+
  | page @ 0x00007f2a1000        |---.             | MR (memory region)     |
  | page @ 0x000055b3c000        |---.|  pin +     |  vaddr -> iova         |
  | ... 262,144 pages total ...  |---'|  translate  |  262,144 x 4 KB walk   |
  | page @ 0x00007f9f1000        |---'             |                        |
  +------------------------------+                 |  lkey 0x51f3a2c1       |
                                                   |  rkey 0x9d2e77b0       |
      CPU: owns the pages, runs 0 instructions     +-----------+------------+
      during the transfer                                      |
                                          rkey + address shared OUT-OF-BAND
                                          (a two-sided SEND, or TCP, or your
                                           own config protocol) with the peer
                                                               |
   peer's NIC: RDMA WRITE  remote_addr=0x00007f9f1000  len=64  key=rkey
        -> key checks the MR -> translation walk -> DMA engine writes the pages
   target process: 0 instructions, 0 syscalls, 0 CPU copies. The bytes simply
   appear in its memory, cache-coherent, at 1-3 us one-way latency.
```

Two 32-bit keys: the **lkey** authorizes *your own* NIC's access (every WR references it); the **rkey** authorizes *remote* NICs. The peer can touch only what you registered and shared the rkey for — registration is the whole security boundary, because the kernel (its socket accounting, its `ss -m` visibility, its permission checks) is not in the loop. This is module 01 ch03's pinned-page DMA discipline, and module 12 ch03's GMMU reborn as an explicit API.

Registration is not free: pinning plus NIC-side table population runs at order 1-20 GB/s depending on NIC generation and page size (honest range; 2 MB/1 GB giant-page MRs cut entries 512x-262,144x). Real programs therefore register long-lived pools once and sub-allocate; newer (2015+) NICs add on-demand paging (ODP) — the NIC takes page faults against registered ranges, the GMMU analogy made literal.

**One-sided verbs are memory as a network service.** RDMA WRITE and RDMA READ need only the rkey, address, and length on the initiator's side; the target CPU is not executing, not scheduled, not even aware. Two-sided verbs (SEND/RECV — a mailbox the receiver must post into) handle the control plane, including rkey handoff. One-sided scales the way sockets cannot: a key-value store answers an RDMA READ without spending an instruction per request — which is why NVMe-oF (NVMe over Fabrics, 2016) and the low-latency datastores of the 100 Gbps era (EDR 100 / HDR 200 / NDR 400 Gbps per port) live on verbs. It composes with module 12: GPUDirect RDMA has the NIC DMA into pinned GPU memory, so gradients cross the network without touching host RAM — which is why NCCL builds its ring allreduce over RDMA: each of N GPUs sends 2(N-1)/N x the tensor bytes (N=8: 1.75x), the ring running at its slowest NIC — the arithmetic behind 8x 400 Gbps nodes.

**The folklore: "RDMA is brittle."** InfiniBand assumes the fabric drops almost nothing (RC QPs retransmit, but as a last resort, not a design center). On Ethernet, "almost nothing" must be *manufactured* with PFC (IEEE 802.1Qbb, per-priority pause, 8 classes): switches pause upstream instead of dropping. Pausing is contagious — a 40:1 incast fills the victim's buffers, its switch pauses upstream, those pause further upstream: the **PFC storm**; if the pause graph cycles, PFC deadlock arrives, and the watchdog's only fix is flapping a link. Meanwhile a switch that drops a small fraction of RoCE traffic drives RC retransmission into a corner — throughput collapses to a few percent while nothing "fails": the **black hole**. Production RoCE runs DCQCN (SIGCOMM 2015: switches ECN-mark, NICs back off rates) plus tuned PFC plus the watchdog. The insider's summary: lossless Ethernet is a pause-based illusion, and a mis-tuned fabric *silently degrades to retransmit storms* rather than erroring loudly.

## The closing ledger: one table, one completed tower

Module 13's arc — DMA ring (ch01), kernel path (ch02), TCP's windows (ch03), userspace I/O (ch04), QUIC and RDMA (here) — ends in two artifacts. First, the transport taxonomy, chosen with three questions (how many connections? how many bytes? what latency budget?):

| transport | CPU copies per byte (round trip) | syscalls per op | kernel in data path | added latency, same-DC | CPU cost |
|---|---|---|---|---|---|
| blocking read+write | 2 | 2 | yes | ~10 us host RTT | high, per byte |
| select / poll | 2 | 2 + O(n) scans/wakeup | yes | + scans | high |
| epoll | 2 | ~1 per wakeup | yes | same | medium |
| io_uring (SQPOLL, fixed bufs) | 0-1 | 0 steady state | yes | ~2-5 us | low-medium |
| DPDK / AF_XDP | 0 | 0 (poll loop) | no | ~1-5 us | 1 pinned core at 100% |
| QUIC (userspace over UDP) | 1 + AES-GCM | 1 per GSO batch | yes (UDP) | + ~1-3 us per packet | 1.2-1.5x kernel TCP (2023) |
| RDMA one-sided verbs | 0 | 0 (MMIO doorbell) | no | 1-3 us one-way | ~0 on both ends |

Second, the completed tower — module 01 ch02's ladder with the network rows this module earned:

```text
  the completed latency ladder (typical 2020s server + datacenter;
  every number is +-2x across hardware and distance):

  register / ALU         ~0.3 ns    |
  L1 data cache          ~1 ns      |  4 cycles at 4 GHz
  L2                     ~3-4 ns    |
  L3                     ~20-40 ns  |
  DRAM                   ~80-100 ns |  same socket
  same-host RTT          ~10 us     |  socketpair, 2 syscalls
  cross-host RDMA RTT    ~2-6 us    |  RoCE, in-rack   <-- beats NVMe below
  local NVMe read        ~80 us     |  queue depth 1
  same-DC TCP RTT        ~50 us     |  kernel stacks, 2 hosts
  cross-continent RTT    ~80 ms     |  New York - London
```

Read the two inversions — the module's final lesson. A cross-host RDMA READ (~2-6 us round trip) beats a *local* NVMe read (~80 us): the network to someone else's DIMMs is faster than your own disk. And cross-host RDMA (~2-6 us) beats the same-host socket round trip (~10 us): two syscall crossings cost more than leaving the machine. Memory stops being a property of *this* box and becomes an address reachable at DRAM-plus-epsilon prices — the one-giant-address-space bet underneath NVMe-oF, GPUDirect, and disaggregated memory, and the end state the module's opening line promised: the network as a memory machine.

## What insiders know

- Initial-packet "encryption" derives from a public version salt plus the DCID (RFC 9001) — obfuscation, not secrecy, so middleboxes cannot parse or mangle parameters they cannot read. Version Negotiation packets are the one middlebox-visible event; insiders watch them to measure ossification in the wild.
- The CID is a routing header *you design*: clusters encode POP/rack/server in its first bytes so any stateless L4 balancer routes migration packets. Hash-the-4-tuple balancers send a migrated connection to a wrong backend, which must answer an unknown CID with a Stateless Reset (16-byte token, RFC 9000 section 10.3) — the client reports a connection *error*, not a migration: the classic "QUIC dies on every Wi-Fi-to-LTE switch" incident, and a balancer bug, not a QUIC bug.
- The 1,200 B floor: first-flight datagrams must be at least 1,200 B and are padded — doubling as the path-MTU probe. PMTU discovery uses DPLPMTUD (RFC 8899) probes, because ICMP Fragmentation-Needed is exactly the packet middleboxes eat (ch01's black-hole folklore, again).
- ACK policy: at least every 2nd ack-eliciting packet (3rd with ECN counts), max_ack_delay default 25 ms, ACK frames carry ranges. No dupack counting exists to fool, and packet numbers only increase, so a reordering burst cannot be misread as loss.
- RoCE v2 rides UDP port 4795 — the insider's `tcpdump` filter is `udp port 4795`. "RDMA over Ethernet" means InfiniBand transport headers inside UDP/IP, and the whole lossless-Ethernet apparatus (PFC, ECN/DCQCN, watchdogs) exists to keep RC retransmission from ever engaging.
- The registration economics: pinning plus NIC table population runs at order 1-20 GB/s; giant-page MRs (2 MB/1 GB) cut entries 512x-262,144x; ODP trades pin time for NIC page faults. Per-request registration instead of a pool shows up in p99 — quiz question 6.
- The interview trap, both directions: "QUIC is on UDP so it is unreliable" — wrong; QUIC implements retransmission, flow control, and congestion control (Reno/CUBIC/BBR ports of chapter 03); UDP contributes a port number and a checksum. And "RDMA is fast" is not an answer — say *which* copies, *which* CPUs, *which* latency: 0 CPU copies, 0 remote instructions, 1-3 us one-way.

## See it yourself

Three labs: the first needs any network; the second, only a laptop; the third works without RDMA hardware (absence is data too).

```bash
# Lab 1, rung 0: raw RTT, no transport at all
ping -c 5 example.com
```

```python
# handshake_ladder.py -- Python 3.8+; the connect ladder, measured.
import socket, ssl, time

HOST = "example.com"  # any HTTPS host you are allowed to touch

def t(label, fn):
    t0 = time.perf_counter()
    fn()
    print(f"{label:28s} {(time.perf_counter() - t0) * 1000:7.1f} ms")

t("TCP connect (1 RTT)",
  lambda: socket.create_connection((HOST, 443)).close())

def tls13():
    ctx = ssl.create_default_context()   # negotiates TLS 1.3 if the server does
    s = ctx.wrap_socket(socket.create_connection((HOST, 443)),
                        server_hostname=HOST)
    print("  (TLS version:", s.version(), ")")
    s.close()
t("TCP + TLS 1.3 (2 RTT)", tls13)
```

```text
# one run against a ~20 ms RTT host; expect variation, the ratio is the point:
ping rtt avg                  20.1 ms
TCP connect (1 RTT)           20.9 ms
  (TLS version: TLSv1.3 )
TCP + TLS 1.3 (2 RTT)         41.7 ms
```

```bash
# QUIC handshake, if you have an HTTP/3 curl: curl --version | grep -i http3
curl --http3-only -w 'udp_socket=%{time_connect} quic_handshake=%{time_appconnect}\n' \
     -o /dev/null https://cloudflare-quic.com
# time_connect is the UDP socket (no round trip at all);
# time_appconnect is the completed QUIC handshake: ~1 RTT, half of TCP+TLS 1.3.
# Run twice from the same session: resumption may put you in 0-RTT territory.
```

```python
# loopback_ladder.py -- 1 MB over TCP loopback, three ways (Python 3.8+).
# Loopback removes the wire; what differs is CPU copies and syscalls.
import mmap, os, socket, threading, time

MB = 1 << 20
payload = os.urandom(MB)
with open("/tmp/mb.bin", "wb") as f:
    f.write(payload)

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0)); srv.listen(1)
a = socket.create_connection(srv.getsockname())
b, _ = srv.accept()

def drainer():                     # the peer: only ever plain recv()
    while b.recv(1 << 16):
        pass
threading.Thread(target=drainer, daemon=True).start()

def bench(label, fn, n=200):
    fn()                           # warmup: page cache + allocator
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    us = (time.perf_counter() - t0) / n * 1e6
    print(f"{label:26s} {us:6.0f} us/MB")

bench("send() user buffer",     lambda: a.sendall(payload))  # 1 CPU copy + 1 syscall
fd = os.open("/tmp/mb.bin", os.O_RDONLY)
bench("os.sendfile()",          lambda: os.sendfile(a.fileno(), fd, 0, MB))
mm = mmap.mmap(fd, MB)                                        # read-only mapping
bench("mmap + send()",         lambda: a.send(mm))            # 1 CPU copy (write side)
```

```text
# one run on a 2021 x86 laptop; expect up to 2x variation — the ORDER is the result:
send() user buffer            131 us/MB
mmap + send()                  98 us/MB
os.sendfile()                  74 us/MB
```

The orderings prove the ladder: `mmap` deleted the `read()` copy but `send()` still copies user pages into the skb (chapter 04); `sendfile` deleted both. The rung this ladder cannot reach without hardware is the RDMA row — 0 CPU copies *and* 0 syscalls — hence the next lab's honesty about absence:

```bash
# Lab 3: RDMA without RDMA hardware — check, do not assume
rdma link show 2>/dev/null || echo "no rdma tool (iproute2, added 2017)"
ibstat 2>/dev/null || echo "no infiniband-diags"
ls /dev/infiniband 2>/dev/null || echo "no uverbs devices: this box has no RDMA NIC"
# With hardware you would see (driver-dependent shapes):
#   0: rocep8s0f0: ... state ACTIVE physical_state LINK_UP
#   CA 'mlx5_0' ... 1 port ... rates like 100 Gb/s (EDR) / 200 Gb/s (HDR)
# On a cloud VM you can rent it: "InfiniBand" or "EFA"-class instances exist.
```

## How to use this knowledge

- **Choosing a transport** is a lookup, not a vibe: connections x bytes x latency budget against the taxonomy. An API gateway at 50,000 connections and 1 KB bodies lands on epoll or QUIC, never RDMA; a 100 Gbps replication link lands on RDMA or at least io_uring with registered buffers; a flaky-network mobile client lands on QUIC for migration and 0-RTT.
- **API design under 0-RTT**: gate state-mutating requests out of early data or give them idempotency keys — browsers restrict 0-RTT to safe methods; do the same, and handle `early_data_rejected` by retransmitting at 1-RTT.
- **Debugging changes shape when the kernel leaves**: a QUIC connection has no `ss -ti` output. Tools become the library's qlog traces and counters (plus `ss -u -a` showing the UDP socket and its ~212,992 B default `wmem`); but conntrack still tracks UDP 443, so chapter 02's table-full incident applies to QUIC floods too.
- **ML cluster arithmetic**: NCCL's ring allreduce moves 2(N-1)/N x the tensor per GPU (N=8: 1.75x) over the slowest NIC in the ring — why 8x 400 Gbps NDR ports per node exist, and GPUDirect is why the gradients never touch host RAM (module 12 ch03).
- **Incident translation**: "the app dies on every network failover" — CID-routing at the load balancer; "p99 tripled after the RDMA refactor" — per-request memory registration; "the fabric went black but nothing is down" — PFC storm or DCQCN mis-tuning.

## Connections

- **Chapter 01**: the QP is the DMA ring's shape — producer-consumer queues plus an MMIO doorbell; refill and doorbell return as WR-posting and CQE-polling.
- **Chapter 02**: QUIC skips the `sk_buff` path entirely, but conntrack still hashes its UDP 4-tuples — the table-full incident transfers; the CID replaces the 4-tuple as identity.
- **Chapter 03**: QUIC re-implements this chapter in userspace — per-stream windows are rwnd reborn, RFC 9002's loss recovery is RACK/PTO, CUBIC/BBR port unchanged; head-of-line blocking was the motivating wound.
- **Chapter 04**: UDP_SEGMENT GSO is TSO bought back by hand; MSG_ZEROCOPY's pinned pages prefigure RDMA registration; io_uring's SQPOLL and the QP doorbell are one idea at two privilege levels.
- **Module 01 ch02**: the tower is complete — the network rows are filled in above; **module 01 ch03**: pinned-page DMA is now an API you call; **module 07 ch04-05**: the kernel socket machinery you just bypassed; **module 12 ch03**: GPUDirect and NCCL close the GPU loop; **module 15 ch03**: virtio's vring joins the ring lineage.
- Next: the module's final exam — the whole stack, from wire voltage to a one-sided RDMA WRITE, as one memory machine.

## Quiz

1. **Concept.** QUIC's designers could have asked for a new IP protocol number or extended TCP's options. Using the SCTP and TCP-Fast-Open records, explain why the only deployable substrate was a UDP payload — and give "the ossification forcing function" in one sentence.
2. **Predict the numbers.** An HTTP/2 connection over TCP carries 100 active streams; RTT is 40 ms; one 1,400 B segment of a 64 KB in-flight window is lost; recovery takes ~1-2 RTT. How many streams stall, and for how long? The same loss under HTTP/3, where the lost packet carried frames for 3 streams: how many stall, and what do the other 97 do?
3. **Predict the ordering.** Sort by latency, with numbers: L1 hit, DRAM access, same-host socketpair round trip, in-rack RDMA READ round trip, local NVMe read (queue depth 1), same-DC TCP RTT, New York-London RTT. Then state the ladder's two inversions and what each implies about systems design.
4. **Diagnose a bug.** A client sends a POST that decrements inventory as 0-RTT early data on every session resumption. An on-path recorder replays the first flight 10 times over the next hour. What does the server do in the worst case, why does TLS anti-replay not fully save you, and what two changes fix it?
5. **Diagnose a bug.** A service enables QUIC connection migration, yet every Wi-Fi-to-LTE handover on mobile clients still tears the connection down — logs show a connection error, not a timeout. The cluster sits behind an L4 load balancer that hashes the 4-tuple. Explain the exact packet-level sequence that kills the connection, including the 16-byte token, and give two fixes.
6. **Diagnose a bug.** An RDMA key-value service had p99 latency of ~25 us. A refactor "fixed" a lifetime warning by moving buffer management from a registered-once 2 GB pool to `ibv_reg_mr`/`ibv_dereg_mr` around each 16 MB request buffer. p99 is now ~900 us. Why, with arithmetic, and what are the three standard fixes?
7. **Diagnose a bug.** Forty machines each push 100 Gbps of RoCE traffic at one NVMe-oF target. Throughput collapses to a few percent, no link goes down, and counters show RC retransmissions climbing. Name the mechanism at the switch (two layers of it), why the watchdog can make it worse, and the two knobs that actually prevent it.
8. **Cross-chapter.** The QP (this chapter), the DMA descriptor ring (ch01), io_uring's SQ/CQ (ch04), and the virtio vring (module 15 ch03) are the same structure. Name the shared producer-consumer shape and doorbell, two differences that matter (who the consumer is; what a completion certifies), and the buffer-ownership invariant that chapter 04's MSG_ZEROCOPY error-queue rule and this chapter's lkey rule both enforce.

<details><summary>Answers</summary>

1. **Ossification, not preference.** SCTP (IP protocol 132, RFC 2960) never crossed the public Internet: NATs and firewalls drop unknown protocol numbers, so WebRTC had to smuggle it inside DTLS-in-UDP. TCP extensions fare no better: Honda et al. (IMC 2011) measured middleboxes mangling unknown options; TCP Fast Open and MPTCP both shipped fallback paths because of it. The forcing function: middleboxes optimize for the protocols that already exist, so only traffic they pass *opaquely* — a UDP payload — can still evolve. QUIC took the one deployable ground and paid the userspace CPU tax as rent.
2. **HTTP/2: all 100 stall; QUIC: 3 stall.** Over TCP the lost segment opens a gap in the single ordered byte stream; every stream's frames above the gap are undeliverable until the retransmission lands — ~1-2 RTT, 40-80 ms of total application freeze. Over QUIC the lost packet carried frames for 3 streams: those stall ~1 RTT while the frames return in a *new* packet with a new number. The other 97 reassemble by their own offsets and deliver — the per-stream queues hold the out-of-order data, and the range-carrying ACK names the missing packet numbers.
3. **Order and numbers:** L1 ~1 ns < DRAM ~80-100 ns < in-rack RDMA RTT ~2-6 us < same-host socketpair RTT ~10 us < same-DC TCP RTT ~50 us < local NVMe ~80 us < NY-London ~80 ms. Inversion one: cross-host RDMA beats local NVMe — someone else's DRAM over the network is faster than your own disk, the economic base of NVMe-oF and disaggregated storage. Inversion two: cross-host RDMA beats the same-host socket round trip — two syscall crossings cost more than leaving the machine, which is why kernel bypass pays even before bandwidth does.
4. **Replay executes the write.** The recorded first flight replays verbatim, and a server holding a valid session ticket decrypts and *processes* the POST — up to 10 inventory decrements. Ticket anti-replay narrows but cannot close the window: ticket pools behind load balancers, and replays landing before the original, defeat single-use accounting. Fix one, server: reject non-idempotent early data (safe methods only, or answer `early_data_rejected` and force a 1-RTT retry). Fix two, client: idempotency keys on every mutating request, so a replay converges to one effect.
5. **The Stateless Reset murder.** On handover the client's IP and port change and it sends with the same CID; the balancer hashes the *4-tuple*, so the packet reaches a backend that never saw this CID. An endpoint receiving an unknown CID must answer with a Stateless Reset — a 16-byte token derived per (CID, reset key) — rather than drop, and the client reads that reset as a fatal error: migration "fails" although QUIC did everything right. Fixes: encode routing (POP/rack/server) in the CID so any balancer routes by it, or make the balancers QUIC-aware and forward on CID.
6. **Registration is rate-limited work, not a flag.** `ibv_reg_mr` pins pages and populates the NIC's translation entries at order 1-20 GB/s. 16 MB per request at the optimistic end is ~0.8 ms of setup — matching the ~900 us p99 — plus dereg cost and NIC-TLB churn. The pool amortized all that once. Fixes: (a) a registered long-lived pool with sub-allocation; (b) ODP — register without eager pinning, let the NIC take page faults; (c) giant pages — 2 MB or 1 GB MRs cut entries 512x-262,144x.
7. **PFC cascade into the retransmit corner.** Layer one: the victim's buffers fill; PFC (802.1Qbb) pauses upstream, those switches pause *their* upstreams, and the stall spreads to innocent tenants — a pause storm; if the pause graph cycles you reach PFC deadlock, and the watchdog's remedy (flapping a link) is worse than the storm. Layer two: wherever drops leak through, RC retransmission engages — never a design center — so throughput collapses to a few percent with no link-down alarm: the black hole. Real knobs: ECN thresholds on the switches feeding DCQCN rate reduction (attack the incast at the source), and lowering the incast factor (stagger writers, cap bursts).
8. **Same shape, different consumers, one invariant.** Shared shape: fixed descriptor arrays shared by producer and consumer, an MMIO doorbell (NIC BAR write, or `io_uring_enter`), and a completion queue polled for status. Difference one — the consumer: NIC hardware for the DMA ring and QP (which is why the kernel leaves RDMA's data path), the kernel itself for io_uring; that one distinction explains most of the latency and privilege rows in the taxonomy. Difference two — the completion: a CQE certifies a transfer *and* releases a specific buffer/key (with a WC status field on failure); an io_uring CQE certifies the operation's `res`. The invariant both enforce: a buffer handed to the device belongs to the device until its completion — MSG_ZEROCOPY's error-queue release (at ACK time, because the send buffer is a retransmission safety copy) and the lkey rule (valid and registered until the WR's CQE, or the DMA faults) are one ownership law at two privilege levels.

</details>
