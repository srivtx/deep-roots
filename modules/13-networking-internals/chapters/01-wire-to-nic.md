# Chapter 01 — Wire, NIC, and the DMA ring

> **The 30-second version.** A wire carries voltage, not bits: the receiver
> must recover the sender's *clock* from the signal itself, which forces a
> line code — Manchester (10BASE-T, 50% overhead), 4B/5B (100BASE-TX, 25%),
> 64b/66b (10GBASE-R, 3%) — redundancy moved from every bit to a block
> header plus scrambling. Bits are framed into Ethernet's 64-to-1,518-byte
> shape: the 64-byte minimum is 1980s collision arithmetic (512 bit-times
> of slot time), the 1,500-byte MTU a 1980 RAM decision you still pay 2.5%
> header tax on. The core: the NIC is a PCIe bus master that DMA-frames
> *directly into host RAM* through a fixed descriptor ring — a
> producer-consumer queue shared between device and CPU, cursors published
> by MMIO doorbell writes — the exact structure io_uring (chapter 04) and
> the virtio vring (module 15 chapter 03) reuse. Per-packet interrupts
> cannot survive 14.88 Mpps, so NAPI polls under load; RSS hashes each flow
> onto one per-CPU ring pair; TSO/GRO offloads make the socket API a lie
> the wire never tells. Packets land in memory before any CPU knows they
> exist.

## Clocks out of copper: why the wire cannot speak raw bits

A twisted pair carries a voltage level that changes over time; the
receiver must answer two questions per bit: *what level*, and *exactly
when to sample*. The "when" is the clock, and a second clock-carrying pair
would skew over meters of copper — so the clock must ride in the data. The
code must be *self-clocking*.

Raw NRZ (1 = high, 0 = low) fails twice. A long run of one value is a
flat line with zero edges, and the recovered clock freewheels: a 100 ppm
oscillator error accumulates a full bit-time of drift in 10,000
uninterrupted bits, after which every sample lands in the wrong cell.
And Ethernet links are transformer-coupled — an AC path, so a
DC-unbalanced stream shifts the baseline until 0 and 1 blur together.

```text
bit:        1       1       1       1       0
NRZ:      +---------------------------------+-------+
          | V high: 4 bit-times, ZERO      | V low |
          | edges -> the clock drifts      |
Manchester: bit XOR clock -> one edge INSIDE every bit cell
           (1 = low-then-high, 0 = high-then-low)
```

Manchester (10BASE-T, IEEE 802.3i, 1990) fixes both by spending redundancy
per bit: two level periods per bit, so 10 Mbps becomes 20 Mbaud — 50% of
the signaling budget carries no data. Fine at 10 Mbps, ruinous at 100, so
the ladder climbs:

```text
encoding ladder: line symbols spent per data bit

Manchester  10BASE-T   802.3i (1990)   2 level periods/bit   eff.  50%
4B/5B       100BASE-TX 802.3u (1995)   5 code bits/4 data B  eff.  80%  (+25%)
64b/66b     10GBASE-R  802.3ae (2002)  2 sync bits/64-bit B  eff. 96.97%(+3.03%)
```

4B/5B (from FDDI) maps each 4-bit nibble onto one of 16 chosen 5-bit
code groups — chosen so runs stay short and transition density stays
high; the other 16 groups become control symbols (idle, start-of-frame).
Five line bits per four data bits: 80% code efficiency, i.e. 25%
overhead — the same 5:4 ratio, quoted both ways. 100BASE-TX serializes
those code bits at 125 Mbaud (NRZI over MLT-3) to carry 100 Mbps. At
10 Gbps per-nibble coding is intolerable, so 64b/66b changes the bet: a
2-bit sync header on each 64-bit block, then *scramble* the payload
(self-synchronous, polynomial 1 + x^39 + x^58). Scrambling guarantees
density not per block but statistically — and that weaker guarantee is
what lets overhead fall to 3.03%: 10.3125 Gbaud carries 10 Gbps.

## The frame, byte by byte: a 64-byte minimum written by collisions

```text
+-----------+--------+------------+------------+-----------+-----------+------+
| preamble  | SFD    | dst MAC    | src MAC    | EtherType | payload   | FCS  |
| 7 B       | 1 B    | 6 B        | 6 B        | 2 B       | 46-1500 B | 4 B  |
| 7 x 0x55  | 0xAB   |            |            | 0x0800    |           |CRC-32|
+-----------+--------+------------+------------+-----------+-----------+------+
|<- not part of the frame; never CRC'd, never counted ->|
                        |<------ the frame: 64 - 1518 B ------>|
                         (802.1Q VLAN tag adds 4 B: max 1522)
```

The preamble is 7 bytes of alternating 1010 — a square wave that gives
the clock-recovery PLL edges to lock onto; the 8th byte flips its final
bits to 0xAB (10101011), the Start Frame Delimiter: "the next bit is
dst MAC's most significant bit." The FCS is a CRC-32 (polynomial
0x04C11DB7, IEEE 802.3) over dst MAC through payload; the NIC verifies
and strips it. Each frame is followed by the interframe gap, 96
bit-times = 12 byte-times of silence. The frame proper runs 64 to 1,518
bytes — and the *minimum* is the number that needs deriving.

Original Ethernet was a shared coax bus where two stations could
transmit at once (CSMA/CD: talk, and *detect collisions while talking*).
Detection only works if you are still transmitting when the collision's
noise returns. Worst case: your first bit is nearly at the far end when
the far station — whose carrier sense still says "idle" because your
signal hasn't arrived — starts its own frame:

```text
station A ==== 2,500 m coax (5-4-3 rule: 5 segments, 4 repeaters) ==== station B
  t=0     A starts; wavefront travels ~5 ns/m -> 12.5 us to cross
  t=12.5- B's carrier sense still says idle; B starts; COLLISION at B
  t=25    garbage arrives back at A -- A must STILL be keying to see it
          => frame duration >= round trip + repeaters + 32-bit jam
          => slot time = 512 bit-times = 51.2 us at 10 Mbps
          => minimum frame = 512 bits = 64 B (dst MAC through FCS)
```

A 10-Mbps shared segment could span 2,500 m precisely *because* every
frame was forced to outlast the round trip. A payload smaller than 46 B
is padded to 64 B in hardware; a 10-byte TCP payload rides inside 84
wire bytes (8 preamble + 64 frame + 12 gap).

Switched full-duplex links cannot collide, yet every NIC still emits and
requires the 64 B minimum: compatibility with mixed segments, half-duplex
fallback, and four decades of parsers that assume it. The switch, once
it replaced the bus, became a memory machine: every arriving frame
updates a learning table — (src MAC, ingress port) — and triggers a dst
MAC lookup. The address space is 2^48, so a linear scan is hopeless:
1,024 entries x 1.5 ns per compare = 1.5 µs per lookup, ~651,000
lookups/s per core, or 23 cores of pure MAC lookup for one 10 Gbps
port's 14.88 Mpps. Hardware switches therefore use CAM/TCAM —
content-addressable memory matching the 48-bit key plus VLAN against
*all* entries in parallel, one access cycle; software switches (Open
vSwitch) use hashed flow tables — module 01's cache-versus-scan trade
again. A dst-MAC miss is not an error: the switch *floods* the frame out
all other ports, and the reply teaches it where that MAC lives. Entries
age out after 300 s (IEEE 802.1D default) — a learning table is a cache,
which is why a control-plane flap briefly doubles a switch's traffic.

## MTU 1500: the 1980 header tax and the jumbo gamble

The 1,500-byte payload limit is derived from no physical constant. It
was a 1980 compromise among buffer RAM cost, interrupt rate, and fair
sharing on 10 Mbps coax — frozen into the DIX spec, then into IEEE
802.3's 1,518-byte maximum frame. Every link since, up to 400 Gbps,
kept it.

The tax is easy to derive. A TCP packet carries 14 B Ethernet + 20 B
IPv4 + 20 B TCP = 38 B of header: 2.53% of a full 1,500-byte payload
(3.87% counting the invisible 8 B preamble + 12 B gap). Small packets
pay multiples of that — a 20 ms G.711 voice sample is 160 B in 54 B of
headers: 234 wire bytes to move 160 B, 68% efficiency. Every real
network mixes both sizes.

Jumbo frames attack the tax: at 9,000 B payload the header share drops
to 38/9000 = 0.42% — a 6.0x cut, and the reason datacenter fabrics run
9,000 B. The gamble: jumbo must hold end to end — one non-jumbo hop
means fragmentation (DF bit clear) or a black hole (DF set). TCP sets DF
by default on modern stacks (Linux 6.x) to do Path MTU Discovery
(RFC 1191): a router that must shrink a DF-set packet drops it and
returns ICMP type 3 code 4 "fragmentation needed" with the next-hop MTU,
and the sender lowers its segment size. The folklore black hole: a
firewall that filters that ICMP. SYN and ACK get through (they are
~60-90 B, under every MTU), the handshake completes, the page loads —
and the first full-MSS segment vanishes with no ICMP to explain it: the
connection stalls mid-stream, retransmit after retransmit, forever.
That "site loads but the POST hangs" signature is the classic
tunnel/VPN incident; the fixes are permitting ICMP 3/4 and MSS clamping
on the tunnel endpoints.

## The ring: packets land in RAM before the CPU knows

Now the core. The NIC must DMA bytes *somewhere*, at times the CPU
cannot predict, into buffers the CPU must later find. Every real NIC
converged on one solution: the driver pre-allocates a fixed array of
*descriptors* in DMA-coherent memory; each descriptor is one promise —
"here is a buffer's physical address and length; write here; report
back."

Why physical addresses? The NIC is a PCIe bus master that walks bus
addresses, not the CPU's virtual page tables — it has no MMU (module 01
chapter 03's return of DMA). With an IOMMU active, descriptors carry
IOVAs the IOMMU translates to physical — protection against a wild
device, at the price of a mapping per buffer. The descriptor (e1000
legacy RX format, the clearest teaching example) is 16 bytes:

```text
+----------------------------------+---------+---------+------------------+
| buffer physical/bus address  8 B | len 2 B | csum 2B | status   4 B    |
+----------------------------------+---------+---------+------------------+
                                                            ^ bit 0 = DD
                                                      (Descriptor Done)
```

The ring is that fixed array — a power-of-two entry count (256 to
4,096 typical; the e1000 family hard-caps it at 64 KiB = 4,096 x 16 B) —
treated as circular, each RX entry paired with a pre-allocated receive
buffer (the igb/ixgbe family splits 4 KiB pages in half: 2 KiB per
buffer). Circular, not a linked list: bounded memory, constant stride —
hardware prefetches descriptor N+1 while working on N, and the wrap is
free arithmetic (index AND (N-1)). A bump allocator with two
cursors:

```text
 RX ring in host RAM (N = 8 shown; real rings 256-4096 entries)

     NIC's cursor: HEAD (RDH)               driver's cursor: TAIL (RDT)
             |                                          |
             v                                          v
   +-------+-------+-------+-------+-------+-------+-------+-------+
   |  0    |  1    |  2    |  3    |  4    |  5    |  6    |  7    |
   | DD=1  | DD=1  | DD=1  | DD=0  | DD=0  | DD=0  | DD=0  | DD=1  |
   +-------+-------+-------+-------+-------+-------+-------+-------+
       |       |       |       ^       ^       ^       ^       ^
       v       v       v       |  NIC fills these next, in order,  |
   +-------+-------+-------+  |  stops dead at TAIL: no buffer   |
   |2 KiB  |2 KiB  |2 KiB  |  |  posted = frame DROPPED silently |
   |buffer |buffer |buffer |  +-----------------------------------+
   +-------+-------+-------+
     ^ frame bytes are DMA'd here by the NIC, BEFORE any CPU
       instruction knows the packet exists. NIC = producer;
       driver = consumer: harvest, then refill. (7 wraps to 0)
```

The cycle, RX direction, step by step:

```text
 CPU / driver                            NIC (PCIe bus master)
 1. allocate buffer; write desc[5]:
    addr = <bus addr>, len = 2048            |
 2. doorbell: MMIO store to the NIC's        |
    RDT register: "tail = 5"  ~~~~~~~~~~~~>  | strongly ordered write:
                                               | publishes step 1's stores
                                               3. DMA: frame bytes -> *addr
                                                  DMA: status, length
                                                  -> desc[5], set DD=1
 4. NAPI poll reads desc[5]: DD=1  <~~~~~~~~~ DMA writeback
 5. wrap buffer in an skb; goto 1 with a fresh buffer
```

Two details carry the design. The descriptor is written by *both* sides
via DMA — request half by the driver, status half by the NIC — so the
ring is genuinely shared memory, not a mailbox. And the doorbell: the
driver publishes its cursor with a strongly ordered (uncacheable) MMIO
store of the new tail index into a NIC register — the barrier that
flushes every descriptor written before it. Each doorbell costs
hundreds of ns, which is why drivers batch refills and ring once.

TX is the mirror image: the driver *produces* — fills descriptors with
the skb's data-fragment addresses plus control bits, bumps the TX tail
register (the doorbell) — and the NIC consumes, DMAs the bytes out, and
writebacks status. Same ring, roles swapped.

Say it explicitly, because it is the module's through-line: **this is a
single-producer/single-consumer shared-memory ring with cursors
published through a doorbell — the exact structure of io_uring's SQ/CQ
(chapter 04, where the doorbell becomes a syscall, then disappears
under SQPOLL) and of virtio's vring (module 15 chapter 03, where it
crosses the VM boundary and the doorbell becomes a virtqueue kick).**
AF_XDP and DPDK use the same shape to hand packets to userspace; NVMe
uses it for storage queues.

The ring size is the driver's one big dial (typical defaults 256 to
4,096 entries; check `ethtool -g`). On RX, a burst that outruns the
driver's refills overruns HEAD into TAIL, and every frame arriving with
no posted buffer is dropped — *silently*: no error, no retransmit for
UDP, no log line, only a counter (`rx_missed` on the e1000e/igb family;
names vary by driver). 256 entries x 2 KiB buffers = 512 KiB of burst
absorption; a 5 ms microburst at 10 Gbps is 6.25 MB — 12x the ring, so
it drops. On TX the dial trades the other way: a deep hardware queue
is *standing latency* (a queue is memory — chapter 03's bufferbloat
lesson), which is why chapter 02's BQL throttles the TX ring to about
1-2 ms of bytes.

## Interrupts, polling, and the offload lies

The ring moves data without the CPU, but the CPU must still be *told* —
and per-packet interrupts cannot work. A minimum frame occupies 84 wire
bytes (8 + 64 + 12), so 10 Gbps of smallest-possible frames is
10^10 / 672 = 14.88 Mpps. One interrupt costs on the order of 1-2 µs on
modern x86 (entry, exit, pipeline flush; measured range 0.5-5 µs by
platform). At 14.88 Mpps that is 14.9-29.8 CPU-seconds per second of
traffic — 15 to 30 cores doing nothing but being interrupted, before a
byte is processed.

Linux's answer (NAPI, merged in the 2.5/2.6 era) is a hybrid that
adapts by load. First frame in a quiet period: the interrupt fires, the
driver schedules a poll and *masks* its interrupt. From then on a
softirq (`NET_RX`) polls the ring directly — up to 64 packets per poll
call (the NAPI weight), up to 300 packets or 8,000 µs per softirq round
(Linux 6.x `netdev_budget`, `netdev_budget_usecs`). When a poll finds
the ring drained, the driver re-arms the interrupt. Low rate:
interrupt latency and per-packet service. High rate: polling amortizes
to a few hundred ns per packet, and drops concentrate in the ring while
the CPU drains at its own pace — the livelock that killed
pure-interrupt designs cannot happen, because work processed is
proportional to CPU spent, never to traffic offered.

One ring means one CPU, and one CPU cannot absorb 14.88 Mpps of *any*
processing — so multi-queue NICs (RSS, Receive Side Scaling) ship 4 to
16 queue pairs (up to 128 on big server NICs), one RX ring + one TX
ring + one MSI-X vector each. The NIC hashes every packet's 4-tuple
(src IP, dst IP, src port, dst port; 2-tuple fallback for non-TCP) with
a Toeplitz hash (40-byte key; the standard test key is published in the
Microsoft RSS spec), and the hash indexes an indirection table
(default 128 entries) that picks the queue. Per-flow affinity is the
point: every packet of one TCP flow lands on one ring, one interrupt,
one CPU — per-flow state stays cache-hot with no locks. The cost is the
elephant-flow trap: one 10 Gbps flow is one hot core no matter how many
queues you bought.

The last piece: on the fast path, the socket API is a lie told by
hardware offloads. Checksum offload: the stack writes the pseudo-header
partial sum into the header field, marks the skb `CHECKSUM_PARTIAL`,
and the NIC inserts the final TCP/UDP checksum on transmit. 
Scatter-gather: the payload need not be contiguous — descriptors point
at each page fragment. TSO (TCP Segmentation Offload) is the big one:
the kernel hands the NIC a single ~64 KiB skb (`GSO_MAX_SIZE` 65,536),
and the NIC slices it into MSS-sized segments (1,460 B for MTU 1500:
44 full segments plus a tail), fabricating each segment's IP and TCP
headers, sequence numbers, and checksums in hardware:

```text
 kernel -> ONE skb (what the CPU processed: 1 header, 1 traversal):
 +14+20+20+------------- ~64 KiB of TCP payload ---------------+
 |Eth|IP |TCP|  bytes 0 .. 65481                                |
 +---+---+---+-------------------------------------------------+
               \  TSO slicing in hardware (MSS = 1460 B)
                v
 +---+---+---+-----------------+   x 44 segments, headers made
 |Eth|IP |TCP| seq = 0          |   by the NIC (id, seq, cksum)
 +---+---+---+-----------------+   final short segment:
                                    seq = 44 x 1460, 1,242 B
```

The CPU does one packet's worth of protocol work for 45 wire packets.
GRO (Generic Receive Offload) is the inverse on the way in: same-flow
segments are merged into one large skb (a frag list, chapter 02's
anatomy) before the stack sees them, so 45 header parses become 1.
The API's semantics are preserved — that is why the lies are safe —
but *observers* get fooled: a tcpdump on the sending host shows ~64
KiB frames that never existed on any wire, and checksums that look
invalid because they are `CHECKSUM_PARTIAL` stubs.

## What insiders know

- **Ring-full drops are invisible to everything except `ethtool -S`.**
  No error, no log, no retransmit for UDP; the e1000e/igb family counts
  them in `rx_missed` (the MPC register lineage), other drivers in
  `rx_no_buffer` or folded into `rx_dropped` — names vary by driver,
  and virtio_net (your VM's "NIC") exposes almost nothing.
- **A capture on a modern host tells three offload lies at once**:
  ~64 KiB frames (TSO/GRO — commonly shown as 65,535-65,542 B), bogus
  TX checksums (`CHECKSUM_PARTIAL` stubs), and stripped VLAN tags.
  Before filing a bug with a pcap, run `ethtool -K eth0 tso off gro off
  tx-checksumming off` — and expect throughput to drop when the CPU
  inherits the work.
- **"Line rate" for small frames is a pps number, not a bps number**:
  84 wire bytes per minimum frame gives 14.88 Mpps at 10 Gbps and
  148.8 Mpps at 100 Gbps; switch ASIC datasheets quote exactly this
  arithmetic. A 25 Gbps NIC can be hopeless at 18.6 Mpps of 64 B DNS.
- **Ring arithmetic lands in cache terms**: 256 entries x 16 B = 4 KiB
  — L1-resident control plane; 4,096 x 16 B = 64 KiB is not, and
  refill sweeps start evicting. The e1000 family's 64 KiB ring cap is
  a hardware-generation memory of exactly this.
- **`netdev_budget` caps softirq fairness, not throughput**: Linux 6.x
  allows 300 packets / 8,000 µs per round per CPU, then reschedules —
  a busy core shows drops in `/proc/net/softnet_stat` (column 2, the
  times-scheduled-out count) before anywhere else.

## See it yourself

Any Linux box with a real NIC (in a VM the driver is virtio_net —
commands still run, counter names shrink; that variance is itself the
lesson). Fragments below are from an e1000e-class driver; **names vary
by driver**.

```console
$ ethtool -g eth0                  # ring sizes: the one big dial
Ring parameters for eth0:
Pre-set maximums:
RX:             4070   RX Mini:      0   RX Jumbo:     4070
Current hardware settings:
RX:              256   RX Mini:      0   RX Jumbo:        0
```

Default 256 = 512 KiB of RX burst absorption with 2 KiB buffers. Now
the offload flags and the queues:

```console
$ ethtool -k eth0 | grep -E 'tcp-segmentation|generic-receive|checksum'
tcp-segmentation-offload: on          # TSO
generic-receive-offload: on           # GRO
$ ethtool -l eth0                     # RSS: one ring pair per channel
Channel parameters for eth0:
Current: 1
$ cat /proc/interrupts | grep eth0    # one line per queue + CPU mask
 41: ... eth0
$ ls /sys/class/net/eth0/queues       # (or: cat /sys/class/net/*/queues)
rx-0  tx-0                            # confirm: one queue pair
```

Now watch the silent drops under load — the payoff of the chapter:

```console
$ ethtool -S eth0 | grep -iE 'miss|drop|no_buf|discard'
rx_missed: 0
$ ping -f -s 1472 10.0.0.2            # line-rate-ish burst, needs root
$ ethtool -S eth0 | grep -iE 'miss|drop'
rx_missed: 173                        # frames arrived, ring had nothing
```

The count climbs only while offered rate exceeds what one CPU on one
queue drains — and `netstat -s | grep -i drop` may show nothing,
because the kernel never saw those frames. Try `nstat -az` before and
after a 10-second `iperf3` delta-style, then grow the ring
(`ethtool -G eth0 rx 1024`) and repeat. Also: `ethtool -c eth0`
(interrupt coalescing) and `ethtool -x eth0` (Toeplitz key and the
128-entry indirection table) on any multi-queue NIC.

## How to use this knowledge

- **A 60-second loss triage that names the owner.** `ethtool -S` ring
  counters (`rx_missed`) = bursts bigger than the ring — fix with ring
  size, queues, or shaping. `rx_crc_errors` = PHY/cable — fix
  hardware. `netstat -s` stack drops = socket buffers, conntrack,
  backlog — chapter 02's territory. Three counters, three different
  pages in the on-call runbook.
- **Capacity planning in pps, not bps.** Convert traffic to packets per
  second before buying or tuning (14.88 Mpps at 10 Gbps minimum-size;
  divide by your real average frame size). Then check queue count
  against core count — one queue means one CPU's NAPI budget is your
  ceiling, and RSS cannot split a single flow.
- **Honest captures.** Turn off TSO/GRO/checksum offload before
  attaching a bug report to a pcap, or you will report 64 KiB frames
  and broken checksums that exist only on your host.
- **The microburst war story, now provable.** "We lose 0.1% of UDP only
  under load" = burst beyond 512 KiB of ring, or one queue pinned to
  one CPU. You can demonstrate it from `ethtool -S` deltas alone, and
  you know which knob (`-G`, `-l`, affinity masks) moves which number.
- **Steal the shape.** Any high-throughput data path you design wants
  pre-allocated buffers, a power-of-two descriptor ring, two cursors,
  a strongly ordered doorbell, and consumer-side refills. io_uring,
  virtio, AF_XDP, DPDK, and NVMe all converged here.

## Connections

- **Module 01 chapter 03 (virtual memory)** — why descriptors carry
  physical/bus addresses, not virtual ones, and what the IOMMU adds
  (translation plus containment of a wild device).
- **Module 01 chapter 02 (the tower)** — the wire's latency floor: ~5 ns
  per meter of copper.
- **Chapter 02 (the kernel packet path)** — the buffer the ring
  delivered becomes an `sk_buff` with headroom and a frag list; BQL
  throttles the TX ring you just met.
- **Chapter 03 (TCP: the buffer machine)** —
  queue-occupancy-is-latency, the law that makes deep TX rings harmful.
- **Chapter 04 (userspace I/O)** — io_uring is this ring with syscalls
  in the doorbell's place; SQPOLL removes the doorbell exactly as NAPI
  removed the interrupt.
- **Module 15 chapter 03 (virtio)** — the vring across the VM boundary;
  the kick is the doorbell, the guest's descriptors the same promises.

## Quiz

1. A receiver's clock-recovery oscillator drifts 100 ppm. How many
   uninterrupted same-value NRZ bits accumulate one full bit-time of
   sampling error? Why does Manchester make that irrelevant, and what
   does it cost on 10BASE-T?
2. A switch's MAC table holds 1,024 entries. At 1.5 ns per compare, how
   many lookups/s can one CPU core do, and how many cores of pure MAC
   lookup does one 10 Gbps port at minimum frame rate require? What
   hardware structure removes the scan?
3. An application sends TCP segments with 10 bytes of payload each.
   Give the exact bytes on the wire for one such segment (preamble
   through interframe gap) and the wire efficiency for the payload.
4. A hypothetical 100 GbE host uses one interrupt per packet and one
   queue. At 1.5 µs per interrupt, how many cores does the interrupt
   load alone consume at minimum-frame line rate?
5. A UDP telemetry stream loses 0.1% of packets, only during 5 ms
   bursts. `netstat -s` shows no receive drops. The NIC is 10 Gbps,
   the RX ring is 256 entries of 2 KiB buffers. Where did the packets
   go, what is the arithmetic, and which two commands do you run
   first?
6. A site behind a corporate VPN: small requests all work, but any
   upload larger than ~1,400 B stalls forever at the same point. TCP on
   Linux 6.x sets DF. Diagnose the mechanism — why did the handshake
   succeed? — and name two fixes.
7. tcpdump on the *sender* shows TCP frames of 65,540 B with invalid
   checksums; tcpdump on the *receiver* shows 1,514 B frames with valid
   checksums. Nobody is tampering. Explain both captures.
8. The TX descriptor holds the buffer's *physical* address, and the
   driver rings the doorbell with an MMIO store. Connect this to
   module 01 chapter 03: why can't the descriptor hold a virtual
   address, and what changes when an IOMMU is enabled? Then name the
   two later structures in this program that reuse the exact ring
   design, and what plays the doorbell's role in each.

<details><summary>Answers</summary>

1. **10,000 bits.** 100 ppm is a relative error of 1e-4, so one full
   bit-time of drift accumulates in 1/1e-4 = 10,000 uninterrupted bits
   — exactly what a flat NRZ run supplies. Manchester's guaranteed
   mid-bit edge re-syncs the PLL every bit, so drift never accumulates.
   Cost: 2 level periods per bit, so 10 Mbps becomes 20 Mbaud — 50% of
   the signaling budget spent on timing.

2. **~651,000 lookups/s per core; ~23 cores.** 1,024 x 1.5 ns =
   1.536 µs per lookup; 1 / 1.536 µs = 651,041 lookups/s. One 10 Gbps
   port at minimum frames = 10^10 / 672 = 14.88 Mpps; 14.88e6 /
   651,041 = 22.9 cores. CAM/TCAM removes the scan: every entry is
   matched against the 48-bit destination MAC + VLAN simultaneously,
   in one access cycle — parallelism as a data structure.

3. **84 wire bytes carrying 10 payload bytes (11.9%).** Frame = 14 +
   10 + 4 = 28 B, below the 64 B minimum, so the NIC appends 36 B of
   padding. On the wire: 8 B preamble (7 x 0x55 + 0xAB) + 64 B frame +
   12 B gap = 84 B. Efficiency = 10/84 = 11.9%; the same 10 B inside a
   1,460-B payload segment would cost 38/1500 = 2.53%.

4. **~224 cores.** 100 Gbps minimum frames = 10^11 / 672 = 148.8 Mpps;
   148.8e6 x 1.5 µs = 223.2 CPU-seconds per second of traffic — 224
   cores of pure interrupts before a byte is processed. This is the
   arithmetic that forces NAPI's poll mode and RSS's per-queue
   spreading.

5. **Dropped in the NIC, silently, counter-only.** The burst offers
   5 ms x 10 Gbps = 6.25 MB; the ring absorbs 256 x 2 KiB = 512 KiB —
   12x short, so frames arriving with no posted descriptor vanish: no
   error, no kernel visibility (hence clean `netstat -s`), no UDP
   retransmit. Run `ethtool -S eth0 | grep -i miss` (the `rx_missed`
   counter; names vary by driver) and `ethtool -g eth0` to confirm
   256. Fix: grow the ring (`ethtool -G eth0 rx 4096` = 8 MiB) and
   spread queues.

6. **Path-MTU black hole: DF set, ICMP type 3 code 4 filtered.** The
   handshake succeeded because SYN, SYN-ACK, and ACK are ~60-90 B —
   under every hop's MTU. The first full-MSS (~1,460 B payload)
   segment exceeds the VPN's inner MTU; the router must drop it (DF
   forbids fragmentation) and returns ICMP "fragmentation needed" with
   the next-hop MTU — but the firewall drops that ICMP, so the sender
   never learns and retransmits the same oversize segment forever.
   Fixes: permit ICMP 3/4; clamp the advertised MSS on the tunnel
   endpoints (Linux `--clamp-mss-to-pmtu`).

7. **The sender's capture sees the API, not the wire.** TSO handed the
   NIC one ~64 KiB skb, and the pre-driver capture point recorded that
   super-packet — ~65,540 B is the classic figure (65,535-65,542 B
   across drivers). The checksums are `CHECKSUM_PARTIAL` stubs not yet
   filled. The NIC fabricated the real 1,514 B wire frames — headers,
   sequence numbers, checksums — in hardware; the receiver's capture
   sits after the wire, so it shows the truth. Capture on both ends,
   and know your offloads.

8. **The NIC has no page table; later, the same ring twice.** A
   virtual address is meaningful only through the CPU's page tables,
   which the NIC's DMA engine never consults — it puts bus addresses
   on PCIe that the memory controller resolves directly, so the
   descriptor must carry a physical (bus) address from the DMA mapping
   API. With an IOMMU, descriptors carry IOVAs the IOMMU translates at
   the PCIe boundary — an extra translation plus protection (a buggy
   NIC can only touch registered mappings). The same ring returns as
   io_uring's SQ/CQ in chapter 04 (doorbell = the `io_uring_enter`
   syscall, which SQPOLL eliminates by kernel-thread polling) and as
   virtio's vring in module 15 chapter 03 (doorbell = the virtqueue
   kick, an MMIO write into the virtual device).

</details>
