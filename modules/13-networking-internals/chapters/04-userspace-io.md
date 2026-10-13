# Chapter 04 — Userspace I/O: read, epoll, io_uring

> **The 30-second version.** A blocking `read()` is two copies deep — the NIC DMAs the packet into a kernel `sk_buff`, the syscall copies the bytes into your buffer — and the wait is worse than the copies: one thread per connection, 8 MB of stack each. `select`/`poll` re-scan every descriptor every call (O(n), twice — your loop plus the kernel's); `epoll` inverts it: register once, a kernel callback links ready fds onto a ready list, and `epoll_wait` returns only those — O(1) to wait, O(ready) to report. `io_uring` finishes the arc: requests and completions become producer-consumer rings shared with the kernel, so submitting work is a memory write, not a syscall — chapter 01's DMA ring, one layer up. Zero-copy deletes the copies by passing page *references* instead of bytes.

## The copy ladder: what a blocking read() really moves

Chapter 02 ended with data in the right place: the socket's receive queue holds an `sk_buff`. **What does it cost your process to get those bytes?** The honest ladder for the echo round trip:

```text
        echo round trip: 4 copies, 2 syscalls
        (every arrow that touches the CPU is a cost you pay)

  wire --> NIC ====DMA (1)====> skb on socket rcv queue   [kernel memory]
                                   |
                        read()  -> syscall #1
                                   |
                             CPU copy (2) -> user buf    [your memory]
                                   |
                        write() -> syscall #2
                                   |
                             CPU copy (3) -> skb on snd queue
                                   |
                             ====DMA (4)====> NIC --> wire
```

One `read()` is **1 syscall and 2 copies** — the DMA that filled the kernel buffer, the CPU copy into yours (count the hidden kernel entries — the interrupt that deposited the data, the first-touch page fault — and each direction crosses twice). The round trip is what matters: **4 copies, 2 syscalls per request**, none negotiable while your buffer sits between two kernel buffers. The file variant of rung 1 is the page cache (module 07 ch05). A syscall costs ~100-500 ns on modern x86-64 — paid per operation, per connection, for zero bytes moved.

The worse half is the *wait*. Blocking I/O means one thread per connection:

```c
/* block_echo.c — the pre-2002 shape: one thread per connection. */
void *worker(void *arg) {
    int c = (int)(intptr_t)arg;
    char buf[4096];
    for (;;) {
        ssize_t r = read(c, buf, sizeof buf);  /* sleeps in the kernel until data lands */
        if (r <= 0) break;                     /* EOF or error: this thread exits */
        if (write(c, buf, r) != r) break;      /* partial-write case elided: teaching stub */
    }
    close(c);
    return 0;
}
/* main loop: accept() -> pthread_create(worker) -> pthread_detach(), forever */
```

Module 01 ch04's arithmetic returns: glibc/Linux default thread stack is 8 MB (`ulimit -s`). Ten thousand mostly-idle connections: **10,000 threads, 80 GB of *virtual* address space** — harmless on 64-bit (128 TB of VA), RSS only touched pages, a few KB per thread (~100-300 MB total). What kills first is the context-switch machine gun: ~1-2 µs direct per switch on a 2020s server, plus cache pollution that can dwarf it — every idle read sleeps, every arriving byte wakes. Two problems, then, and they are different: the **copies** (per byte) and the **wait** (per connection). Epoll fixes the wait and touches the copies not at all.

## Deleting rungs: zero-copy as reference-passing

One rung exists only because of an API decision: your buffer. The page-cache page *is* the data; your buffer is a second copy that exists only because `read()`'s signature demands one. Zero-copy removes the rung by passing page *references*.

**`sendfile(out, in, off, count)`** (Linux 2.2, 1999) is provable from its signature alone — no user pointer anywhere:

```c
int out = accept(lfd, 0, 0);
int in  = open("file.bin", O_RDONLY);
for (;;) {
    ssize_t n = sendfile(out, in, NULL, 1 << 20);  /* 1 MB at a time, no user buffer at all */
    if (n <= 0) break;
}
```

Fast path: the kernel attaches page-cache pages to the socket's send queue as skb fragments (chapter 02's scatter-gather machinery); the NIC DMAs straight out of page-cache pages. CPU copies: **zero** (disk still DMAs into the page cache once, if cold). Modern `sendfile` runs on the **`splice`** machinery (2.6.17, 2006): a Linux pipe is not a byte channel but an array of `pipe_buffer` structs — each a `{page, offset, len, ops}` reference to a refcounted page. `splice(in -> pipe -> out)` moves references; `tee()` duplicates them.

**`MSG_ZEROCOPY`** runs the trick the other way — the *user's* pages become the source:

```c
int one = 1;
setsockopt(s, SOL_SOCKET, SO_ZEROCOPY, &one, sizeof one);
send(s, big, len, MSG_ZEROCOPY);   /* kernel pins big[]'s pages; NIC DMAs from user memory */
recvmsg(s, &msg, MSG_ERRQUEUE);    /* completion arrives later, on the ERROR queue */
```

The kernel pins your pages — chapter 01's ownership rule again: a buffer posted to the device belongs to the device until it comes back. Hence the completion notification is *mandatory*: `send()` returns before the hardware has read your bytes, and the buffer may not be reused until the error-queue notification clears the range. The crossover: pinning and tracking pages costs more than one CPU copy of a small buffer — kernel developers' 2017 measurements on 10 Gbps-class NICs put the break-even around **10 KB per send**. And on TCP a sharper rule follows from chapter 03: the send buffer is a *retransmission safety copy*, so your pinned pages are released not when the NIC finishes but when the data is **ACKed** — completions fire at ACK time, not DMA time. Quiz question 7 lives here.

## From scanning to callbacks: how the ready list is built

Fixing the wait means one thread watching many connections. `select` (1983-era BSD) and `poll` share one design: **re-ask the kernel about every fd, every call.**

- `select`: the `fd_set` is a bitmap — 1,024 bits = 128 bytes in glibc (`FD_SETSIZE = 1024`) — copied in and written out per call; the kernel walks fds 0..nfds-1 checking readiness, then your loop walks them again with `FD_ISSET`: two O(n) scans per wakeup.
- `poll`: an array of 8-byte `pollfd`s, no 1,024 cap — but still copied in and out and scanned kernel-side and user-side every call: at 10,000 connections, ~80 KB each way per wakeup, and last call's registration buys nothing — the kernel has no memory of what you asked before.

`epoll` (Linux 2.5.44, 2002; kqueue, FreeBSD 4.1, 2000, is the BSD cousin) makes one design change: **split registration from waiting.** `epoll_ctl(ADD)` registers an fd *once*, planting a callback; `epoll_wait` returns only descriptors that are actually ready. Derive the mechanism — the part worth drawing:

```text
 data lands on the wire
   -> NIC DMA + NAPI poll               [ch01]
   -> skb queued on the socket rcv queue [ch02]
   -> sk->sk_data_ready(sk) called      [sock_def_readable]
   -> wake_up() on sk->sk_wq            [the socket's own wait queue]
        -> ep_poll_callback(epi)        [epoll_ctl(ADD) installed this]
             -> epi spliced onto ep->rdllist    [O(1): list insertion]
             -> epoll_wait woken (it sleeps on ep->wq, not on any socket)
 epoll_wait: copies at most `maxevents` ready entries to user space  [O(ready)]
```

No scan ever happens: the arriving packet *pushes* the fd onto the ready list. Wait O(1), report O(ready). The C10K problem (Dan Kegel, 1999) died here — nginx-era demonstrations run ~100,000 mostly-idle connections on one epoll loop, each costing an epitem and a wait-queue entry, not a thread, not a scan.

**Level-triggered vs edge-triggered** is the semantics of that list. LT (default) answers "is it ready *now*?" — a still-readable socket is re-reported on every `epoll_wait` until drained. Safe, but two traps: fully servicing one busy fd starves the rest (LT keeps re-reporting it), and every wakeup re-reports everything. ET answers "did it *transition*?" — reported **once per event** — and the contract that makes it correct:

```c
/* ET: the event means "data arrived", not "some data remains". Drain or lose. */
for (;;) {
    ssize_t r = read(fd, buf, sizeof buf);
    if (r < 0) { if (errno == EAGAIN) break; /* drained: done until the NEXT edge */ close(fd); break; }
    if (r == 0) { close(fd); break; }        /* EOF */
    handle(buf, r);
}
```

Miss the drain-to-`EAGAIN` loop and leftover data sits unread forever — no new edge comes, because no new *arrival* came. A top-three epoll bug; nginx's pattern (ET + nonblocking + drain + move on) keeps one loop fair across 100k fds. `EPOLLONESHOT` extends it for threads: one event wakes exactly one worker, the fd silent until re-armed with `EPOLL_CTL_MOD`. `EPOLLEXCLUSIVE` (Linux 4.5+) stops the thundering herd when multiple epoll sets share one listen fd.

**The accept side has queues of its own.** A listening socket holds two: the SYN queue (half-open connections — SYN seen, ACK pending; bounded by `tcp_max_syn_backlog`, distro-tuned, typically 128-2,046) and the accept queue (completed handshakes waiting for `accept()`, bounded by `min(backlog, net.core.somaxconn)` — 128 before Linux 5.4, 4,096 since). When the accept queue fills, Linux *drops the final ACK* and retransmits the SYN-ACK: clients see slow connects, not errors — the silent production symptom. A SYN flood attacks the SYN queue; **SYN cookies** are the derived countermeasure — store no state at all. The SYN-ACK's ISN is a MAC over the 4-tuple plus a time counter, with the MSS folded in as an index into a small table (Linux's scheme; bit layout varies by kernel). The client's ACK returns `seq-1` = the cookie; recompute, verify, materialize the socket — no queue entry ever existed, so no flood fills one. The price: SYN-time options (window scaling, SACK) are not carried. Hence `tcp_syncookies = 1` (default): cookies only on overflow, never by choice.

## io_uring: the DMA ring's idea, applied to syscalls

Even a perfect epoll loop pays one `epoll_wait` per wakeup plus `read`/`write` per event — each 100-500 ns of transition for zero bytes moved; at ~1M operations/second, a third of the budget. `io_uring` (Linux 5.1, 2019, Jens Axboe) removes the transitions by making the *queue itself* the interface — chapter 01's DMA ring, moved up a layer:

```text
 two rings, mmap'd by BOTH kernel and userspace
 (io_uring_setup() returns an fd; the memory behind it is mapped twice)

 SQ — submissions        user is PRODUCER, kernel is CONSUMER
 +--------+--------+--------+--------+   the index ring holds u32s into
 |idx 0   |idx 1   |idx 2   | ...    |   the SQE array (one per submission)
 +--------+--------+--------+--------+
  ^ head: kernel consumes    ^ tail: user appends
     each SQE = 64 B:  opcode u8 | flags u8 | ioprio u16 | fd i32 |
                      addr/off/len (union, u64 x2) | user_data u64 | ...

 CQ — completions        kernel is PRODUCER, user is CONSUMER
 +---------------------+---------------------+-----
 | user_data (u64)     | res (s32) | flags   | ...   16 B per CQE
 +---------------------+---------------------+-----
  ^ head: user advances ("cqe_seen")   ^ tail: kernel writes results
```

Both sides see both rings, so head and tail are communicated by *writing memory*. Submitting a read: fill an SQE, bump the SQ tail — no syscall yet; one `io_uring_enter()` flushes a whole batch, and completions return as CQEs whose `res` is exactly what `read()` would have returned, tagged with your `user_data`. The CQ ring defaults to 2x the SQ size; on real overflow, completions spill to a kernel-side list (since Linux 5.5) — drain or lose them.

**SQPOLL** is the endgame: `IORING_SETUP_SQPOLL` starts a kernel thread that polls the SQ ring itself — submissions need no syscall, not even the batch flush. The lineage is now explicit: DMA descriptor ring with an MMIO doorbell (ch01) -> virtio vring, the same ring between VM and host (module 15 ch03, queued) -> io_uring's SQ/CQ between user and kernel, doorbell replaced by a polling kernel thread. Three instances of one idea: *shared-memory producer-consumer rings with the doorbell optimized away.* Two add-ons matter: **registered buffers** (`io_uring_register(IORING_REGISTER_BUFFERS)`) pin your buffers once, then `READ_FIXED`/`WRITE_FIXED` opcodes use them — MSG_ZEROCOPY's per-send pin paid once at setup. **Linked SQEs** (`IOSQE_IO_LINK`) chain operations — read linked to write fires the write the instant the read completes, no round trip through userspace. The honest liburing example (raw-syscall io_uring is ~150 lines of setup):

```c
/* iouring_read.c — one READ through the shared rings. liburing-dev required. */
#include <liburing.h>
#include <stdio.h>
#include <fcntl.h>
int main(void) {
    struct io_uring ring;
    io_uring_queue_init(16, &ring, 0);          /* 16 SQEs; CQ defaults to 32 */

    int fd = open("iouring_read.c", O_RDONLY);
    char buf[4096] __attribute__((aligned(16)));

    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, fd, buf, sizeof buf, 0);  /* opcode, fd, addr, len -> 64 B */
    sqe->user_data = 42;

    io_uring_submit(&ring);                     /* ONE syscall, even for a full batch */

    struct io_uring_cqe *cqe;
    io_uring_wait_cqe(&ring, &cqe);             /* returns a pointer into the CQ ring */
    printf("read %d bytes, user_data=%llu\n", cqe->res,
           (unsigned long long)cqe->user_data); /* res == what read() would return */
    io_uring_cqe_seen(&ring, cqe);              /* advance CQ head: a memory write */
    return 0;
}
```

**Decode the "0 syscalls" claim.** True in the steady state on the data path: submission is a memory write, completion consumption a memory read. What still costs: setup and registration; the SQPOLL thread burning one full core while it polls, and after `sq_thread_idle` ms of silence it sleeps — the next submission then needs `io_uring_enter(IORING_ENTER_SQ_WAKEUP)`, so sparse traffic plus SQPOLL is the worst of both worlds; waiting for CQEs is spinning or `IORING_ENTER_GETEVENTS`; some opcodes run on kernel `io-wq` workers; and completions still cost the kernel their interrupt/softirq work. io_uring removed the *transitions*, not the work.

**The fan-fear.** From 2019 to 2023 io_uring was syzbot's most productive syscall surface — a steady stream of use-after-free and refcount bugs, several used in the era's sandbox escapes. Google removed io_uring from Chrome and ChromeOS in early 2023; Android followed within the year; Docker's default seccomp allowlist omits the `io_uring_*` family, so containers deny it unless the profile is extended. Module 16 ch05 (queued) lands this as its browser-sandbox lesson: every fast path cut into the kernel is new attack surface.

## Leaving the kernel behind: DPDK, AF_XDP, and the decision table

If transitions are the tax, the last rung removes the collector: **kernel bypass.** DPDK puts a userspace poll-mode driver (PMD) in charge of the NIC, packet pools in hugepages (2 MB or 1 GB), and one pinned core spinning on each RX queue — no interrupts, no syscalls, no `sk_buff`, no page faults on the data path. Recall chapter 01's number: 14.88 Mpps at 10 Gbps with 64-byte frames; the kernel stack does ~1-2 Mpps per core, XDP ~5 Mpps (ch02), DPDK line rate. The bill: you have reimplemented a network stack (or adopted one — mTCP, F-Stack) with no conntrack, no qdisc, no debugging help — and the polling core is 100% CPU whether traffic flows or not: ch01's NAPI hybrid, deliberately undone. **AF_XDP** is the middle path: an XDP program in the driver (ch02) redirects frames into **UMEM** — memory registered once, shared kernel/user — through four rings (fill, completion, RX, TX; the ring idea again); the kernel keeps the control path, the data path is ring-to-ring — less raw throughput than DPDK, a fraction of the work.

Every rung is now on the table — choose by load *shape*, not fashion (orders of magnitude, one core, 2020s hardware):

| Load shape | Rung that wins | Why |
|---|---|---|
| under ~500 connections, simplicity first | blocking `read` + threads | copies identical; threads are cheap at this count |
| 1k-100k mostly-idle connections | epoll (LT or ET) | wait is O(1), report O(ready); 1 syscall per batch |
| 100k-1M connections | epoll + `SO_REUSEPORT`, one loop per core | shard accept and ready-list load across cores |
| small ops at 10^5-10^6+/s (storage, RPC, proxies) | io_uring + registered buffers (+ SQPOLL only when hot) | amortizes transitions to zero on the data path |
| line-rate packets, Mpps per box | DPDK / AF_XDP | kernel and `sk_buff` leave the path entirely |
| large sequential payloads, any conn count | `sendfile` / `splice` / MSG_ZEROCOPY (>10 KB) | deletes the CPU copies, orthogonal to conn count |

## What insiders know

- `ss -lnt` on a listening socket: **Recv-Q is the current accept-queue depth, Send-Q is the backlog you passed to `listen()`** (capped by `somaxconn`). One glance tells you a server that "mysteriously" stops accepting at load forgot to raise its backlog — and that nginx's famous default backlog of 511 fought `somaxconn` 128 for years before Linux 5.4.
- `epoll_ctl(ADD)` on a *regular file* fails with `EPERM` — epoll only registers fds that can block; regular files are always ready. "Why can't I epoll my log file" is this, not a bug.
- epoll and zero-copy fix different axes: event loops fix the *wait*, `sendfile`/MSG_ZEROCOPY the *copies*. A proxy migrated to epoll that still does `read`+`write` still pays 4 copies per request — the gain came from thread removal alone.
- The MSG_ZEROCOPY completion is ACK-time on TCP, DMA-time on UDP — debugging a "send isn't done" stall on a zerocopy socket is a chapter 03 job (`ss -ti`, unacked bytes), not a chapter 04 one.
- io_uring's SQE is 64 B and CQE 16 B, fixed by the UAPI since 5.1. Ring sizing is arithmetic, not tuning: 256 SQEs = 16 KB of SQEs, 512 CQEs = 8 KB, plus the index rings — all mapped, all deliberately cache-line-sized.
- `FD_SETSIZE` is a glibc compile-time constant; redefining it before `<sys/select.h>` really does grow your `fd_set`s — but `select` still copies the whole bitmap twice per call. The fix for the 1,024 wall is `poll`/`epoll`, not a bigger bitmap.

## See it yourself

One Linux box, no root. Verified on Linux 6.x; outputs trimmed.

**1. The syscall tax, counted.** The ~40-line epoll ET echo skeleton — compile, run, hit it with `nc localhost 5000`:

```c
/* epoll_echo.c — edge-triggered echo: one process, everything nonblocking.
   build: gcc -O2 epoll_echo.c -o epoll_echo */
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <arpa/inet.h>
#include <sys/epoll.h>

static void nb(int fd) { int f = fcntl(fd, F_GETFL, 0); fcntl(fd, F_SETFL, f | O_NONBLOCK); }

int main(void) {
    int lfd = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in a = {0};
    a.sin_family = AF_INET; a.sin_port = htons(5000); a.sin_addr.s_addr = INADDR_ANY;
    bind(lfd, (struct sockaddr *)&a, sizeof a); listen(lfd, 512); nb(lfd);

    int ep = epoll_create1(0);
    struct epoll_event ev = { .events = EPOLLIN, .data.fd = lfd };
    epoll_ctl(ep, EPOLL_CTL_ADD, lfd, &ev);          /* listen fd: LT is fine */

    for (;;) {
        struct epoll_event out[64];
        int n = epoll_wait(ep, out, 64, -1);         /* O(ready): returns only real events */
        for (int i = 0; i < n; i++) {
            if (out[i].data.fd == lfd) {
                for (;;) {                           /* drain accepts too */
                    int c = accept4(lfd, 0, 0, SOCK_NONBLOCK);
                    if (c < 0) break;
                    struct epoll_event ce = { .events = EPOLLIN | EPOLLET, .data.fd = c };
                    epoll_ctl(ep, EPOLL_CTL_ADD, c, &ce);
                }
            } else {
                for (;;) {                           /* ET: MUST drain to EAGAIN */
                    char buf[4096];
                    ssize_t r = read(out[i].data.fd, buf, sizeof buf);
                    if (r > 0) { if (write(out[i].data.fd, buf, r) < r) {} } /* stub: see note */
                    else break;                      /* EAGAIN (drained), EOF, or error */
                }
            }
        }
    }
}
/* note: production must loop write() to EAGAIN and buffer partial writes —
   this stub is for syscall-counting only. */
```

Under load (`yes hello | nc localhost 5000` in a loop), measure:

```console
$ strace -c -p $(pidof epoll_echo)
% time     seconds  usecs/call     calls    errors syscall
------     ----------- ---------- --------- --------- ----------------
 62.00      0.091947           2     45087           epoll_wait
 23.31      0.034511           1     40201           read
 14.69      0.021772           1     39855           write
```

The shape is the finding: `epoll_wait` once per batch, then raw `read`/`write` per event — the ET drain loop means no syscall per "is there more?" probe. A select-based server shows the same three names with near-identical counts — and per-call times that grow linearly with fd count, because a full `strace` (not `-c`) prints the entire fd list that `select` copied and scanned on every line. The count column ties; the time column does not.

**2. Zero-copy, visible in the signature.** `strace` a `read`+`write` copier and a `sendfile` copier of the same file over the same socket:

```console
$ strace -o /tmp/a.txt ./copy_rw   # read(3, "..."..., 131072) = 131072   <- names a user buffer
                                     # write(4, "..."..., 131072) = 131072
$ strace -o /tmp/b.txt ./copy_sf   # sendfile(4, 3, [0], 2147483647) = 131072  <- no buffer anywhere
```

The copier's read/write arguments *name a user pointer* — that pointer is the copy; `sendfile`'s argument list has nowhere for one to be, and under `strace -c` it halves the syscall count per 128 KB chunk.

**3. The accept queue, live.**

```console
$ ss -lnt | rg 5000
LISTEN 0  512  0.0.0.0:5000 0.0.0.0:*    # Recv-Q=0 (queue empty), Send-Q=512 (backlog)
$ sysctl net.core.somaxconn net.ipv4.tcp_syncookies net.ipv4.tcp_max_syn_backlog
net.core.somaxconn = 4096                 # since Linux 5.4; was 128
net.ipv4.tcp_syncookies = 1               # 1 = cookies only when the SYN queue overflows
net.ipv4.tcp_max_syn_backlog = 2048       # distro-tuned; stock kernels sit lower
```

Burst connections faster than the server accepts (`seq 2000 | xargs -P 200 -I{} nc -z localhost 5000 &`) and Recv-Q climbs toward Send-Q — the number clients pay for as silent connect latency.

**4. io_uring, honestly.** CPython has no stdlib io_uring binding (as of 3.12), so the lab is the liburing program from section 4. Check `ls /usr/include/liburing.h` — absent on minimal boxes: `apt-get install liburing-dev`, compile with `gcc iouring_read.c -luring -o iouring_read`. Installing nothing, `grep io_uring /proc/kallsyms | head -3` at least confirms the kernel carries the machinery.

## How to use this knowledge

- **The crash at 1,025 clients.** A legacy daemon corrupts state exactly when fd numbers cross 1,024, no error from `select` — it is `FD_SET` writing past the 128-byte bitmap in *userspace*, silently; the fix is `poll`/`epoll`, not a bigger array.
- **The 100% core that isn't a bug.** One pegged core, `ps` showing a kernel thread (`iou-sqp-*`, name varies) or a DPDK poll thread — by design: a polling core trading CPU for latency. Budget it, pin it, or drop SQPOLL/PMD for sparse traffic.
- **Picking an event model with the decision table.** Connections, operations/second, payload size — three axes, and only the third is fixed by zero-copy. Most "we need io_uring" proposals are 500-connection services that need none of this.
- **Reading a zerocopy stall.** A TCP MSG_ZEROCOPY sender wedged waiting for completions is almost always unACKed data (chapter 03's retransmission machine): `ss -ti` shows the unacked bytes; completions arrive when the ACKs do.
- **The interview derivation.** "Why is epoll O(1)?" — draw the wait-queue callback splicing an epitem onto the ready list when `sk_data_ready` fires; it separates reciting from knowing.

## Connections

- **Chapter 01** built the DMA descriptor ring — producer, consumer, doorbell. io_uring's SQ/CQ is that ring one abstraction up, SQPOLL replacing the MMIO doorbell; NAPI's interrupt-vs-poll trade reappears as `sq_thread_idle`.
- **Chapter 02** ended the receive path at the socket's skb queue — rung 1 of this ladder; XDP/AF_XDP's rings point the same idea at packets, and skb frags are what sendfile's page references attach to.
- **Chapter 03** derived the send buffer as a retransmission safety copy — that is *why* `write()` blocks on a full snd queue and why TCP MSG_ZEROCOPY completions wait for ACKs; your drain-to-EAGAIN rate is the receiver's rwnd drain rate in disguise.
- **Chapter 05** takes the bypass logic to its ends: QUIC rebuilds TCP in userspace (every syscall this chapter eliminated becomes the app's own), and RDMA's registered memory is registered buffers taken to hardware.
- **Module 01 ch02** (the latency tower) gains its syscall row; **module 01 ch04** supplied the 8 MB stack arithmetic; **module 07 ch05** owns the page cache and the page-pinning machinery zero-copy leans on.
- **Module 15 ch03** (queued) is virtio's vring — the ring idea in virtualization; **module 16 ch05** (queued) is the browser-sandbox chapter where the io_uring removal lands as a case study.

## Quiz

1. **Concept.** Your coworker proposes switching the event loop from level-triggered to edge-triggered epoll "for efficiency." State what question each mode answers about a descriptor, why the ET drain-to-`EAGAIN` rule exists, and the characteristic failure of each mode under a careless handler.

2. **Predict the number.** An echo server (blocking `read` + `write`, one thread) serves 50,000 pipelined 100-byte requests on one already-open connection. Count the syscalls and data-path copies the kernel performs (ignore `accept`). Then say which numbers `sendfile` would change if the payload came from a file instead of the peer — and which it would not.

3. **Diagnose.** A 15-year-old daemon runs `select()` with `FD_SET`/`FD_ISSET`. After years of flawless operation it starts crashing — or reporting garbage on unrelated fds — precisely when the process holds around 1,025 client connections. No syscall returns an error. What is the bug, in whose memory does the corruption land, and why is there no error message?

4. **Concept.** Walk the exact path from "a packet arrives on the wire" to "`epoll_wait` returns this fd": name the four kernel steps, say where the O(n) scan *would* have been in `select`, and give the work `epoll_wait` does per ready fd and per call.

5. **Predict the number.** A thread-per-connection server holds 10,000 idle connections, glibc default stacks, Linux. How much virtual address space do the stacks claim, roughly how much RSS, and what resource actually runs out first? Justify the last part.

6. **Diagnose.** An ET epoll server handles each event with a single `read(fd, buf, 8192)` — no loop. Small-request clients work perfectly; clients that send 20 KB in a burst hang after the first response, and a packet capture shows their data sitting in the server's kernel receive queue, unread, forever. Explain the mechanism, and why the same handler with `EPOLLET` removed would have worked.

7. **Cross-chapter.** The same `send(s, buf, 64*1024, MSG_ZEROCOPY)` on a UDP socket reports completion ~5 µs after the NIC's DMA finishes, but on TCP the completion can arrive tens of milliseconds later with the DMA long done. Derive the difference from chapter 03's reason the TCP send buffer exists and chapter 01's rule about who owns a buffer posted to hardware.

8. **Diagnose.** A service using `io_uring` with `IORING_SETUP_SQPOLL` does about 50 operations per second. Ops sees one CPU core pinned at 100%, eaten by a kernel thread (`iou-sqp-...`, name varies). What is burning the core, why is that unavoidable while the thread is hot, and what is the correct fix given the 50 ops/s rate?

<details><summary>Answers</summary>

1. **LT asks "is it ready *now?*"; ET asks "did it *transition* since the last report?"** LT re-reports a still-readable fd on every `epoll_wait` until drained; ET reports once per arrival. The drain rule exists because ET fires on the *edge*: if the handler reads only part of the data, the remainder generates no new edge — nothing new arrived — so reading until `EAGAIN` is the only way to know you are done. LT's careless-handler failure: fully servicing one busy fd starves the rest, because LT keeps re-reporting it. ET's: forgetting the drain loses the tail *permanently* (question 6's stall).

2. **100,000 syscalls and 200,000 copies.** Per request: `read` = 1 syscall, 2 copies (NIC DMA into the skb, CPU copy skb->user); `write` = 1 syscall, 2 copies (CPU copy user->skb, DMA skb->NIC). From a file via `sendfile`, the CPU copies drop to **zero** on the fast path (page-cache pages attach to the socket as skb frags) and the syscall count falls to one per chunk. Unchanged: the two DMAs per round trip — the wire must still be fed — and the per-packet skb work. Zero-copy deletes CPU copies, never the DMAs.

3. **`FD_SET` is an unguarded bitmask write past the end of the `fd_set` — in the program's own userspace memory.** glibc's `FD_SET(c, &set)` does `fds_bits[c/64] |= 1 << (c%64)` with no bounds check, and the set is 128 bytes (1,024 bits), so fd 1,025 writes into adjacent stack or struct memory. The kernel never objects — `select` trusts your `nfds`, and the corruption happened in glibc *before* the syscall; hence no error message. A userspace buffer overrun wearing a syscall costume; fix with `poll` or `epoll`.

4. **DMA ring post -> NAPI poll -> skb queued on the socket's rcv queue -> `sk_data_ready` -> `wake_up` on the socket's wait queue -> the `ep_poll_callback` planted by `epoll_ctl(ADD)` splices the epitem onto the ready list (O(1)) -> the sleeping `epoll_wait` wakes and copies out at most `maxevents` entries.** In `select`, those last steps are instead a kernel walk over every fd from 0 to nfds-1 — the O(n) that vanished. Per ready fd: one O(1) splice plus one 12-byte `epoll_event` copied out; per call: O(ready) copy-out — O(1) when idle, because the sleep is on the epoll's own wait queue.

5. **80 GB virtual, ~40-160 MB RSS, and the scheduler/context-switch budget goes first.** 10,000 x 8 MB = 80 GB of *virtual* address space — harmless on 64-bit Linux (128 TB per process). RSS is only touched pages, a few KB per idle thread. What breaks first is CPU scheduling: every idle thread wakes and sleeps on blocking reads, and at ~1-2 µs per switch (plus cache pollution that often dominates), the CPU spends its budget switching rather than working. The 80 GB figure is the trap answer.

6. **The leftover 12,288 bytes generate no new edge, so ET never re-reports them.** The 20 KB burst was one arrival — one edge, one wakeup. The handler read 8,192 bytes and returned; the rest sits in the kernel receive queue, and because no *new* data arrives, no transition ever fires and the fd never returns to the ready list — exactly what the capture shows. With `EPOLLET` removed, LT re-evaluates "is it ready *now*?" every `epoll_wait`: the still-readable socket is re-reported, and even the single-read handler drains eventually. The ET fix is the drain loop to `EAGAIN`.

7. **The TCP send buffer is a retransmission safety copy, so the pinned pages are not released at DMA time.** Chapter 03: bytes cannot leave the send buffer until ACKed, because loss is normal and the sender must be able to retransmit. With MSG_ZEROCOPY the pinned user pages *are* that safety copy, so the kernel holds them — and you must not reuse them — until the ACK covers the last byte; hence completion at ACK time. UDP has no retransmission obligation, so chapter 01's rule applies in its plain form: the buffer belongs to the device until the DMA returns it, and completion fires right after.

8. **The SQPOLL kernel thread, by design.** `IORING_SETUP_SQPOLL` starts a kernel thread that busy-polls the submission ring so submissions need no doorbell syscall — the trade is a whole core for zero-syscall submission, and while the thread spins it sits at 100% CPU regardless of how little work exists (it sleeps only after `sq_thread_idle` ms with no submissions). At 50 ops/s the service pays a core to save microseconds per second. Fix: drop SQPOLL and use plain `io_uring_enter` per batch, or set a small `sq_thread_idle` and accept the wake cost (`IORING_ENTER_SQ_WAKEUP`). Polling designs pay in CPU what they save in latency, breaking even only at high op rates — DPDK's core, one layer down.

</details>
