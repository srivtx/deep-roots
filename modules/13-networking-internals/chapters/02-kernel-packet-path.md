# Chapter 02 — The kernel packet path

> **The 30-second version.** Chapter 01 ended with the frame landed in RAM by
> DMA and NAPI deciding to poll instead of interrupting. This chapter follows
> the bytes the rest of the way: the kernel wraps them in a single allocation
> — the `sk_buff` — shaped so headers are *pushed* and payloads appended by
> moving pointers, never by copying the payload; netfilter looks at packets
> through refcounted *clones*, tracks every flow in a hash table sized from
> RAM, and drops *new* connections when that table fills; the socket lookup
> is another hash; the socket's receive queue is an skb list charged by
> *truesize* — the bytes the kernel actually allocated, not the bytes you
> sent; and on the way out, the qdisc and BQL decide how much memory is
> allowed to stand in front of the wire. One shape (the ring) and two ideas
> (pointer arithmetic, refcounting) explain the whole path — and XDP exists
> precisely to skip all of it.

## One allocation, four pointers: deriving the packet buffer

A packet is the worst-shaped object a memory system can be handed. On the
transmit side it *grows downward*: the socket writes payload, then TCP
prepends 20 B, IP prepends 20 B, Ethernet prepends 14 B — and a tunnel adds
another 20-70 B on top of that. On the receive side it *shrinks*: each layer
strips its header as the packet climbs. The naive design — allocate exactly
what each layer needs — reallocates and copies the payload 2-3 times per
direction. The Linux design is one allocation with slack on both ends and
four pointers into it:

```text
    struct sk_buff  (~232 B, Linux 6.x x86-64; lives in skbuff_head_cache)
    holds the pointers and metadata — never the packet bytes:

        head ──┐
       data ──┼──┐         ┌── tail        ┌── end
             │   │         │               │
             ▼   ▼         ▼               ▼
   ┌────────────┬─────────────────┬─────────┬───────────────────────────┐
   │  headroom  │   linear data   │ tailroom│   skb_shared_info          │
   │  64-512 B  │  headers+payload│  0-...  │   ~320 B + frags[17]       │
   │            │  so far         │         │   (scatter-gather parts)   │
   └────────────┴─────────────────┴─────────┴───────────────────────────┘
      ▲data        ▲data      ▲tail           end marks the allocation's
      skb_push(h): skb_pull(h): skb_put(a):    last byte: shared_info is
      data -= h    data += h    tail += a      BUILT INTO the allocation

     truesize (what memory accounting charges) =
          the whole block above + the 232-B struct
```

The headroom is reserved on purpose. On RX the driver reserves
`NET_IP_ALIGN` (2 B) + `NET_SKB_PAD` (max(32, L1_CACHE_BYTES) = 64 B
on x86-64), GRO adds 256 B more (`GRO_HEADROOM`); stacked encapsulation
pushes TX reservations toward 512 B. `skb_push(14)` prepends an Ethernet
header by *decrementing* `data` by 14 and writing 14 fresh bytes into space
already paid for — O(header), never O(payload). `skb_pull(20)` strips the
IP header on receive by *incrementing* `data`: the header is abandoned in
headroom, not freed. "Allocate exact" loses because it would copy the
payload 2-3 times per direction to save 64-512 B of slack per packet.

Two struct fields do the accounting:

```c
struct sk_buff {              /* trimmed to what this chapter needs */
    unsigned int len, data_len;   /* len = linear + frags; data_len = frags only */
    unsigned char *head, *data,   /* the four pointers into ONE allocation   */
                  *tail, *end;
    struct sock  *sk;             /* NULL until the socket lookup fills it in */
    void (*destructor)(struct sk_buff *);  /* e.g. sock_wfree: returns wmem credit */
    /* ... ~232 B total, Linux 6.x x86-64; drifts a few B per release */
};
```

`len` counts the packet's bytes; `data_len` counts how many of those live
*outside* the linear area, in `skb_shared_info` — a struct built into the
tail of the same block, past `end`:

```c
struct skb_shared_info {   /* at the very end of the head allocation, past `end` */
    __u8   nr_frags;                    /* frags[] entries in use (max 17) */
    __u16  gso_size;                    /* MSS a TSO "super-packet" is sliced into */
    struct sk_buff *frag_list;          /* GRO can chain whole skbs here */
    skb_frag_t     frags[17];           /* 17 max with 4-KiB pages; each 16 B: */
};                                      /* { page ptr, offset, length } */
```

This is scatter-gather, derived: a 64-KiB GRO-merged packet is one skb with
16 page fragments (16 x 4,096 B = 65,536 B of payload in pages, headers in
the small linear area), not a 64-KiB contiguous block. The kernel never
copies the payload to make it contiguous — it carries pointers.

Now the number insiders argue about: **`truesize` vs `len`**. When a packet
is queued on a socket, the kernel charges the socket's memory counter by
`skb->truesize`, and `truesize` is defined (Linux 6.x, 64-B cache lines) as
roughly:

```text
truesize = SKB_DATA_ALIGN(linear bytes incl. headroom)   /* rounds to 64 */
         + SKB_DATA_ALIGN(sizeof(struct sk_buff) ≈ 232)  /* → 256 */
         + SKB_DATA_ALIGN(sizeof(struct skb_shared_info) ≈ 320)
```

A 182-B frame (14+20+20+128), fully linear and reserved with 66 B of RX
headroom, charges 256 + 256 + 320 = **832 B — 6.5x the payload it carries**.
The same GRO'd 64-KiB packet charges ~66 KiB — about
1.01x, because full pages waste nothing. Socket memory accounting is honest
about *allocations*, not payloads, and every buffer-sizing argument about
`sk_rcvbuf` is secretly an argument about this multiplier.

## Look, don't own: cloning a packet you must not copy

Two subsystems need to *look* at packets without becoming their owners:
`tcpdump` (every AF_PACKET socket gets every frame) and netfilter hooks.
Copying the payload for each would be O(packets x bytes) — fatal. The
solution is module 01 chapter 14's PyObject pattern applied to a buffer:

- **`skb_clone()`** allocates only a new ~232-B struct from
  `skbuff_head_cache` and shares the data: the refcount in
  `skb_shared_info` (the `dataref`) is incremented, the payload is touched
  zero times. The clone's `data` pointer can be pushed and pulled freely —
  pointer moves don't write shared bytes.
- **`pskb_copy()`** copies the head allocation *and* the linear data (so a
  writer can modify headers or pull bytes down), but still shares the page
  fragments — the cheap 80% stays shared.
- **`skb_copy()`** copies everything, frags included.

The rule falls out of the shapes: everyone who merely *reads* takes a
clone; the first one who *writes* to the shared region pays for the copy.
So `tcpdump` on a busy link costs roughly one 232-B struct allocation per
packet (per-CPU slab-cached — module 07 ch05's skbuff_head_cache) plus the
copy into the AF_PACKET mmap ring (TPACKET_V3 — yes, another ring), not a
payload copy per listener.

## The receive path: a pipeline of hash lookups and drop points

Walk one packet from the wire to the socket. Chapter 01's part: the NIC
DMAs the frame into a page, posts the descriptor, NAPI's poll loop (budget
64 packets per poll) harvests it. Then:

```text
   RX wire (ch01: DMA ring → NAPI poll)
     │
     ▼
   XDP hook (driver-level, BEFORE any skb exists — section 5)
     │
     ▼
   netif_receive_skb  ← first function that owns a real sk_buff
     │
     ▼
   PREROUTING hook ──► conntrack: hash the 5-tuple
     │                  {proto, src ip, src port, dst ip, dst port} (+ zone)
     │                  nf_conntrack_max entries; FULL ⇒ drop new flows
     ▼
   ROUTING ── for this host? ── no ──► FORWARD ──► POSTROUTING ──► wire
     │ yes
     ▼
   INPUT hook
     │
     ▼
   SOCKET LOOKUP: established hash on the 4-tuple → struct sock
     │   (miss + SYN ⇒ the listener's SYN queue, not a child socket)
     ▼
   sk's receive queue: a LIST of skbs
     │   sk_rmem_alloc += skb->truesize;  over sk_rcvbuf ⇒ drop, sk_drops++
     ▼
   sk_data_ready() → wake the waiter (ch04's epoll fires HERE)
```

Every box after NAPI is a memory structure, and every one has a bound with
a drop counter behind it. The two that fill production boxes:

**Conntrack is a hash table with a RAM-sized ceiling.** Each flow is one
entry (~300 B) keyed by the 5-tuple — or 5-tuple plus zone when
`nf_conntrack` zones are in use, which is how identical-looking tenants
coexist (VRFs, overlapping address space). `net.netfilter.nf_conntrack_max`
defaults to 4 x the bucket count, and the bucket count scales with RAM
(powers of two; expect roughly 262,144-524,288 max on an 8-16 GB host,
Linux 6.x with 4-KiB pages — the formula has drifted across releases, so
read the value, not a blog post). When it fills, the kernel logs —
rate-limited, so grep the whole dmesg — `nf_conntrack: table full, dropping
packet`, and the blast radius is surgical: *established* flows match an
existing entry and keep working; anything needing a *new* entry dies. That
asymmetry is the classic production incident: the monitoring agent loses
connectivity while existing SSH sessions look healthy. Entries are
garbage-collected by timeout — 432,000 s (five days) for an established TCP
flow, 120 s for TIME_WAIT — which is why a table can stay full overnight
after one busy hour.

**The socket lookup is a second hash.** Established connections hash the
4-tuple (local ip/port, remote ip/port) into the established hash; a miss
with a SYN targets a listening socket, whose SYN queue is bounded by
`tcp_max_syn_backlog` (mainline default 1024) and whose completed-connection
accept queue is bounded by the smaller of the `listen()` backlog argument
and `somaxconn` (4,096 since Linux 5.4; 128 before). Full SYN/accept queues
count in `TcpExt:ListenDrops` / `ListenOverflows`. Chapter 04 tells that
story fully.

**The receive queue is where truesize bites.** The queue is a linked list
of skbs (`next`/`prev` — the struct's first two fields exist for exactly
this). Each queued skb charges `sk_rmem_alloc` by its truesize; if the sum
would exceed `sk_rcvbuf` (TCP default 128 KiB, autotuned up to 6 MB on
Linux), the packet is dropped and `sk_drops` — the `d` field of
`ss -m` — counts it. TCP tries to stop traffic *before* this point by
shrinking the advertised window (chapter 03's job); the drop is what happens
when data arrives anyway, past a receiver that stopped reading.

## The transmit path: the queue is a policy, not a bucket

Transmit is the receive story with the arrows reversed: the mirror netfilter
hooks fire here — OUTPUT after routing, POSTROUTING on the way to the device
— completing the 5-hook set (PREROUTING, INPUT, FORWARD, OUTPUT, POSTROUTING)
— plus one structure the receive path doesn't have, a *policy* between the
stack and the hardware:

```text
   app write() → TCP send buffer (ch03: retransmission safety copy)
        │   segments built as skbs (GSO: one 64-KiB skb, 16 frags)
        ▼
   qdisc  (pfifo_fast: 3 bands; fq: ~1024 per-flow FIFOs + pacing)
        │   BQL pauses the qdisc at ~1-2 ms of line-rate bytes in the device
        ▼
   driver TX ring (ch01)  ← dma_map_page(frag) → iova → descriptor → doorbell
```

The qdisc queues packets *before* the driver sees them. `pfifo_fast`, the
compiled-in default on Linux 6.x (`net.core.default_qdisc`; multiqueue NICs
get `mq`, one child per TX queue), is 3 priority bands selected from the
packet's TOS bits. `fq` instead keeps ~1024 per-flow FIFOs and *paces* each
flow at the socket's `pacing_rate` — which is why early BBR documentation
said "BBR requires fq": a controller that computes a sending rate needs a
smooth clock into the hardware, not a burst. (`fq_codel`, OpenWrt's default
since 15.05, is the same queueing with CoDel's latency-signaling drop —
chapter 03.)

Below the qdisc sits the driver's TX ring from chapter 01 — and the
bufferbloat lesson at its smallest scale. A 4,096-entry TX ring whose
descriptors point at ~1,500-B buffers can hold ~6 MB of standing bytes. At
10 Mbps that is 4.9 *seconds* of queue delay; at 1 Gbps, 49 ms. BQL (byte
queue limits, the `dql` library, integrated since ~3.5-3.6 in most drivers)
fixes this: it tracks how many bytes the driver has handed the hardware
without a completion, and pauses the qdisc once that exceeds roughly 1-2 ms
of line-rate bytes — a few KB on a slow link, a few hundred KB on a 10-Gbps
one. The ring becomes a *memory pool* of posted buffers, not a queue; the
queue lives in the qdisc where policy can see it.

The last step is the DMA map: each frag's `page` is translated to a device
address — an *iova* — through the IOMMU (module 01 ch03's translation story
running in the other direction). With an Intel VT-d IOMMU active, a
`dma_map_page` costs hundreds of nanoseconds (Linux 6.x amortizes it through
per-queue IOVA caches; the swiotlb *bounce copy* people dread only happens
for buffers the device cannot address directly). On TX completion the
driver's interrupt frees the skb — the destructor returns the wmem credit,
the frags' pages go back to the page pool — and posts the descriptor for
reuse.

## XDP: deleting the skb to save the packet

Look at the receive-path diagram and count what a packet costs before
anyone looks at its payload: the head allocation, the 232-B struct, the
shared-info init, the conntrack hash walk, the route lookup, the socket
hash walk, the refcounts. With 64-B packets that per-packet tax dominates:
the full path tops out around 1-2 Mpps per core (commodity x86, one
~3-4 GHz core) — chapter 01's 14.88-Mpps worst case never had a chance.

XDP's answer is structural: eBPF programs attached at the *driver's* RX
point, running on the raw page before an skb is allocated at all. The
verifier is the enabling technology — it statically proves, before the
program ever runs, that every memory access is in-bounds, that loops are
bounded, that execution terminates (up to 1 M verified instructions since
Linux 5.2) — so the kernel can run unprivileged-looking code on raw packet
data at interrupt-adjacent priority. An `XDP_DROP` is just "put the page
back in the pool": no skb was ever built. Measured drop rates run about
5 Mpps per core even on unremarkable drivers and ~20 Mpps on tuned ones
(Linux 5.x/6.x, x86), versus the 1-2 Mpps the full stack manages — the gap
*is* the skb tax. `XDP_TX` reflects the page back out; `XDP_REDIRECT` to
`AF_XDP` hands the frame to userspace through — inevitably — a pair of
descriptor rings plus a shared memory region (the `umem`) that kernel and
user both map. Chapter 01's DMA ring, chapter 04's io_uring, AF_XDP: one
shape, three appearances.

## What insiders know

- **`ss -m`'s `r` field counts truesize, not payload bytes.** A socket that
  received 128 KiB of payload shows an `r` inflated 1.01-6.5x depending on
  packet size and GRO; drivers that wrap a whole 4-KiB page via `build_skb`
  charge the full page. Every "rcvbuf looks full but the app only read X"
  confusion is this multiplier.
- **The conntrack-full playbook**: established flows survive, new flows
  drop, the dmesg line is rate-limited (grep it), `nf_conntrack_max` is
  runtime-writable but buckets are best sized at boot (the `nf_conntrack.hashsize`
  module parameter) — raising max without buckets trades drops for hash-chain
  CPU. Entries cost ~300 B each: 1M flows ≈ 300 MB. Escape hatch for chatty
  UDP: `nft`'s `notrack` statement in prerouting.
- **Drop triage is a ladder, and each rung has one counter**: NIC ring
  (`ethtool -S`, `rx_missed`/`rx_no_buffer`) → per-CPU backlog
  (`/proc/net/softnet_stat` column 2; `netdev_max_backlog` default 1000) →
  IP input (`IpInDiscards`, nstat) → socket (`ss -m`'s `d`, `ListenDrops`).
  `netstat -s | grep -i drop` is the aggregate triage; `dropwatch -l kas`
  or a `bpftrace` on `tracepoint:skb:kfree_skb` names the exact code site —
  and since Linux 5.17 that tracepoint carries a `reason` enum.
- **BQL made "shrink the TX ring" folklore obsolete.** On any driver with
  byte queue limits (most since ~3.6), the ring is a buffer pool and BQL
  caps standing bytes at ~1-2 ms of line rate; tuning
  `ethtool -G tx` mostly changes the pool's refill cadence, not latency.
- **`pskb_may_pull()` before reading past the linear area** is the
  decade-old driver and netfilter-module bug: GRO'd packets keep only the
  headers linear; `data + offset` past `tail`-minus-frags is garbage or a
  panic. If your hook reads payload, pull first.
- **pfifo_fast is still the compiled default** (`net.core.default_qdisc`),
  which means a stock server kernel has 3 dumb TOS bands in front of its
  10-Gbps NIC until someone sets `fq` or `fq_codel` — one sysctl, and BBR
  deployments that skip it send in bursts on older kernels.

## See it yourself

All of this is readable on any Linux box. Ten minutes, no hardware needed.

**1. Socket memory, the truesize way.** Start any transfer (an
`iperf3 -s` on another host, or just stream something), then:

```console
$ ss -tm state established | head -6
...
skmem:(r128512,rb131072,t0,tb46080,f2560,w0,o0,bl0,d3)
```

Decode: `r` = bytes charged to the receive queue (truesize units), `rb` =
`sk_rcvbuf`, `t`/`tb` = the send side, `f` = headroom before the bound,
`bl` = backlog (accept) queue length, `d` = `sk_drops`. Notice `r` sits well
above the app's "unread" payload — the truesize multiplier, visible.

**2. System-wide socket memory.**

```console
$ cat /proc/net/sockstat
sockets: used 248
TCP: inuse 42 orphan 0 tw 12 alloc 45 mem 3
UDP: inuse 13 mem 0
```

`mem 3` is *pages* — 3 x 4 KiB = 12 KiB across all TCP sockets; this is
where `tcp_mem`-family global pressure (the third accounting layer, in
pages scaled from RAM at boot) reads from.

**3. The TcpExt counters that should be zero.**

```console
$ awk '/^TcpExt:/ { if (!h) { for (i=2;i<=NF;i++) n[i]=$i; h=1 }
      else { for (i=2;i<=NF;i++) if (n[i] ~ /ListenDrops|ListenOverflows|
      TCPRcvCollapsed|PruneCalled/) print n[i] "=" $i } }' /proc/net/netstat
```

(One line, unwrapped; or `nstat -az | grep -E 'ListenDrop|RcvCollapsed|Prune'`.)
`ListenDrops`/`ListenOverflows` = SYN/accept queues full; `PruneCalled` and
`TCPRcvCollapsed` = TCP had to *drop already-received data* from the receive
queue under memory pressure — the truesize chapter's failure mode. All four
at 0 on a healthy box is the baseline; nonzero values are incidents.

**4. Conntrack occupancy vs ceiling.**

```console
$ cat /proc/sys/net/netfilter/nf_conntrack_count /proc/sys/net/netfilter/nf_conntrack_max
1834
262144
```

(Root, and `conntrack-tools` installed: `conntrack -L | wc -l` is the
equivalent.) On a NAT box or container host this ratio is a capacity
number: past ~70% you are one busy hour from the table-full incident.

**5. Drops mapped to code.** If `dropwatch` is packaged (Debian/Ubuntu:
`apt install dropwatch`):

```console
$ sudo dropwatch -l kas
dropwatch> start
...
619 drops at tcp_v4_rcv+0x...
```

If not, `sudo bpftrace -e 'tracepoint:skb:kfree_skb { @[args->reason] = count(); }'`
(Linux 5.17+) aggregates by drop reason instead of site.

**6. Before/after on a real transfer.**

```console
$ nstat -az > /tmp/net.before
$ iperf3 -c <server> -t 1 > /dev/null; sleep 2
$ nstat -az > /tmp/net.after
$ diff /tmp/net.before /tmp/net.after | grep -E '^[<>]' | grep -iE 'Segs|Drop' | head
< Tcp: OutSegs 1234567
> Tcp: OutSegs 1236192
```

What to notice: the segment counters jump by tens of thousands (a 1-s
gigabit transfer is ~78,000 max-size segments) while the drop-family
counters move by zero — and now you know which boxes of the receive-path
diagram each zero corresponds to.

## How to use this knowledge

- **"We're losing packets" now has a search order**: ring → backlog →
  conntrack → socket, one counter each, five minutes from symptom to
  culprit. The `d` field of `ss -m` and column 2 of `softnet_stat` are the
  two that separate "the kernel dropped it" from "the app was too slow".
- **Sizing a NAT/firewall box** is arithmetic, not folklore: peak new flows
  per second x average entry lifetime (120 s for TIME_WAIT churn, 5 days
  for established idle) x ~300 B per entry, checked against
  `nf_conntrack_max` — and raise the buckets at boot, not just the max.
- **Latency work at the egress** is the BQL + qdisc pair: the standing
  queue is bounded in *time* (~1-2 ms) by BQL and in *fairness* by
  fq/fq_codel; "reduce latency" on a modern driver means checking the
  qdisc, not shrinking rings.
- **Small-packet servers** (DNS at 100k qps, game servers) live on the bad
  side of the truesize multiplier: 128-B payloads charge ~6x, so rcvbuf
  pressure arrives ~6x sooner than payload math suggests — and the escape
  hatches are GRO (merge before accounting hurts) and, at the extreme,
  XDP/AF_XDP, which is precisely why public resolvers run them.
- **Reading any kernel networking code** — a driver, an nftables module, a
  TCP patch — starts from this chapter's vocabulary: four pointers, one
  allocation, truesize, clones, hash lookups. Everything else is detail.

## Connections

- **Chapter 01** (same module): the DMA ring and NAPI's 64-poll budget are
  the producer half of this chapter's consumer; XDP's "before the skb"
  position is measured against chapter 01's ring, and AF_XDP's descriptor
  rings are that ring moved to the socket boundary.
- **Chapter 03**: `sk_rcvbuf` becomes the advertised window, the receive
  queue becomes the OOO reassembly queue, and `PruneCalled` /
  `TCPRcvCollapsed` get their full story — windows are buffers.
- **Chapter 04**: `sk_data_ready` at the bottom of the receive path is the
  callback that links a socket onto epoll's ready list; the accept queue is
  `listen()`'s side of the SYN story; io_uring's shared rings are AF_XDP's
  rings with syscalls instead of DMA.
- **Chapter 05**: QUIC owns none of these structures — userspace
  reimplements the queue and the lookup as ordinary heap objects, which is
  both its cost and its freedom.
- **Module 01 ch03** (virtual memory): the IOMMU/iova translation on the TX
  path is the DMA mapping story's other direction.
- **Module 01 ch14** (Python): `skb_clone`'s `dataref` is PyObject
  refcounting — shared payload, copied header-of-object.
- **Module 07 ch05** (Linux memory machinery): `skbuff_head_cache` and the
  page-fragment allocator are slab-cache citizens you can watch in
  `/proc/slabinfo`.
- **Module 15 ch03** (virtio): the vring is this chapter's descriptor ring
  under a hypervisor — the guest's "NIC" is the same producer-consumer
  contract.

## Quiz

1. Transmitting a TCP segment prepends a 20-B IP header and a 14-B
   Ethernet header to payload the socket already wrote. Explain why, with
   a headroom-reserving `sk_buff`, the cost is O(header) instead of
   O(payload) — and state exactly what `skb_push()` changes in memory.

2. A TCP packet carrying 128 B of payload arrives as a fully linear
   182-B frame (14+20+20+128), reserved with 66 B of RX headroom by the
   `napi_alloc_skb` path. How many bytes does the socket's
   `sk_rmem_alloc` grow when this packet is queued — and what is the
   ratio to the payload? Work the three-term formula (Linux 6.x,
   x86-64, 64-B cache lines).

3. A scanner opens 100 new TCP connections per second against a host and
   closes each immediately; every other workload is idle. With default
   conntrack timeouts (Linux 6.x), roughly how does
   `/proc/sys/net/netfilter/nf_conntrack_count` evolve over the first
   10 minutes, and what number does it settle at?

4. At 02:14 a load balancer starts failing health checks to one backend
   while the established SSH session to that same box keeps responding.
   `dmesg` contains `nf_conntrack: table full, dropping packet` (once).
   Explain the asymmetry, give the immediate mitigation and the durable
   fix, and state what the durable fix costs in RAM at 1M entries.

5. A file transfer is slow. On the receiver, `ss -tm` shows
   `skmem:(r131072,rb131072,...,d1987)` with `d` still growing; on the
   sender, `ss -ti` (chapter 03's command) shows a window collapsed
   toward 0 with climbing retransmit counts. The kernel's aggregate drop
   counters are zero. Diagnose: where are the drops happening, and what
   is the application-level cause?

6. A kernel module hooks netfilter INPUT and reads bytes 54 through 118
   (the first 64 B of payload) directly from `skb->data` on every TCP
   packet. It works perfectly on interactive traffic; during large
   downloads it reads garbage and occasionally oopses. Why — and what
   one call fixes it?

7. Trace one TCP SYN from wire to accept queue, naming: the structure it
   sits in at the moment the NIC finishes (chapter 01), the two hash
   lookups and one policy hook it passes (this chapter), the queue it
   lands in before the third ACK arrives, and the queue it lands in
   after — with the sysctl that bounds each queue.

8. On identical hardware (one ~3.5 GHz x86 core), an `XDP_DROP` program
   discards roughly 5 Mpps while the full receive path manages roughly
   1-2 Mpps. Name at least three specific things the XDP path *never
   does* that account for the gap, and explain what property of the
   verifier makes running raw packet code at this point acceptable.

<details><summary>Answers</summary>

1. **A pointer decrement, then 34 B of fresh header bytes.** The headroom
   (64-512 B) was reserved at allocation time precisely so that
   `skb_push(h)` only does `data -= h` (and `len += h`): the 14-B and 20-B
   headers are *written into already-owned memory* in front of the payload.
   The payload is never touched — no realloc, no copy. Allocate-exact would
   grow the block downward per layer, copying the payload 2-3 times per
   direction to save 64-512 B of slack; packets vastly outnumber layers
   touched, so slack wins.

2. **~832 B, a ~6.5x multiplier.** truesize = SKB_DATA_ALIGN(182 + 66) +
   SKB_DATA_ALIGN(232) + SKB_DATA_ALIGN(320) = 256 + 256 + 320 = 832.
   (Formula for the `napi_alloc_skb` path, Linux 6.x, x86-64; the struct
   and shared-info sizes drift a few bytes per release, and a driver that
   wraps a whole 4-KiB page via `build_skb` charges the full page — the
   *lesson* is the multiplier: socket memory accounting charges
   allocations, and small packets carry a ~4-6.5x tax. GRO'd traffic is
   the mirror image: 64 KiB in 16 full page-frags charges ~1.01x.)

3. **Climbs roughly linearly for ~2 minutes, then plateaus near 12,000.**
   Each closed connection leaves a conntrack entry in TIME_WAIT state,
   whose default timeout is 120 s (nf_conntrack_tcp_timeout_time_wait).
   Steady state = arrival rate x lifetime = 100 x 120 = 12,000 entries
   (plus a handful of transients in other states). The same arithmetic at
   10,000 conn/s gives 1.2M entries — past default max on most hosts,
   which is exactly how scanners trip the table-full incident.

4. **The asymmetry is lookup-vs-insert.** Established flows hit an
   existing conntrack entry (a hash lookup, no allocation) and pass; only
   packets that would create a *new* entry hit the full table and are
   dropped — health checks open new connections constantly, an SSH
   session established at 01:00 does not. The log line is rate-limited,
   hence appearing once. Immediate mitigation:
   `sysctl -w net.netfilter.nf_conntrack_max=1048576` (runtime-writable;
   hash chains lengthen since buckets were not raised). Durable fix: size
   `nf_conntrack.hashsize` at boot (buckets) plus max, or remove the noisy
   flows from tracking with an `nft` `notrack` rule. Cost at 1M entries:
   ~300 B each ≈ 300 MB of RAM plus the bucket array.

5. **The drop is at the socket's receive queue; the cause is a receiver
   that stopped draining it.** The aggregate counters are zero because
   `sk_drops` is per-socket — the `d` field in `ss -m` — and 1,987 and
   climbing means skbs arrived past a full `sk_rcvbuf` and were dropped
   there. The sender's evidence corroborates: the collapsed window means
   the receiver's TCP advertises no room (chapter 03's flow control doing
   its job), and retransmits re-send data whose ACKs never came. Application
   level: the reader loop (single-threaded epoll, a blocked upstream write)
   is slower than the arrival rate; or the path BDP exceeds autotuning's
   6-MB ceiling. Fixes: drain faster / parallelize the reader, or raise
   `tcp_rmem` max for long-fat paths — chapter 03's BDP arithmetic decides.

6. **Nonlinear data: after GRO, the payload is not behind `data`.** Large
   downloads arrive as GRO-merged skbs with only the headers (~tens of
   bytes) in the linear area and the payload in page frags beyond
   `tail`. `skb->data + 54`..`data + 118` reads whatever follows the
   linear area — shared-info/padding garbage — or walks off the
   allocation and oopses. Interactive traffic (small, fully linear
   frames) happens to work, which is why the bug ships. The fix is
   `pskb_may_pull(skb, 118)` (which copies frag bytes into the linear
   area on demand) before any payload read — or `skb_linearize()` if the
   whole packet is needed.

7. **Ring → hash → hash → SYN queue → accept queue.** (1) The frame DMAs
   into a page posted on the NIC's RX descriptor ring (chapter 01); NAPI's
   poll (budget 64) builds the skb. (2) PREROUTING: the conntrack hash on
   the 5-tuple inserts a NEW entry (bounded by `nf_conntrack_max`).
   (3) Routing decides "local", INPUT passes, and the *established-hash*
   lookup on the 4-tuple misses — landing on the listener, so the SYN sits
   on the SYN queue (bounded by `tcp_max_syn_backlog`, mainline default
   1024). After the handshake, the child socket enters the established hash
   and the completed connection sits on the accept queue (bounded by
   min(backlog argument, `somaxconn` = 4,096 since 5.4)), where `accept()` —
   or chapter 04's epoll loop — picks it up.

8. **XDP never allocates the skb, never consults the tables, never
   refcounts.** Concretely: (a) no head allocation, no ~232-B struct from
   skbuff_head_cache, no skb_shared_info init — the program runs on the
   raw DMA'd page, and a drop is just "return the page to the pool";
   (b) no conntrack hash walk, no routing lookup, no established-hash
   socket lookup — the program's own code replaces all three;
   (c) no clone/refcount traffic and no queueing (XDP_DROP bypasses even
   the qdisc story). The verifier makes this acceptable by proving, before
   the program runs, that every access is bounds-checked against the
   packet's real extent, that loops terminate (bounded iterations, up to
   1 M verified instructions since 5.2), and that no arbitrary pointer is
   dereferenced — so untrusted-adjacent code executes at driver RX
   priority without endangering the kernel. Hence ~5 Mpps/core (up to
   ~20 on tuned drivers) versus ~1-2 Mpps for the full path.

</details>
