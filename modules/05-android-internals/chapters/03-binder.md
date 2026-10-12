# Chapter 03 — Binder

> **The 30-second version.** Every Android process is a separate address space
> (module 01 ch03), so "processes talking" is a memory problem. A socket costs
> two copies — one into the kernel, one out. Shared memory costs zero copies but
> leaves you to build the referee. Binder is the deliberate middle: each process
> mmaps one ~1 MiB kernel-owned pool, and a transaction is a *single* copy from
> the sender's userspace straight into pages of the **receiver's** mapping,
> where the receiver reads it in place. Parcels are length-prefixed bytes plus
> an offsets table of kernel-fixup records (binder objects, file descriptors),
> so the driver rewrites per-process numbers without parsing your payload. AIDL
> compiles to a dispatch switch, up to 15 binder threads drain the queue, handle
> 0 names servicemanager — and that 1 MiB pool, shared by everything in flight,
> is why a 2 MB Intent dies as `TransactionTooLargeException`.

## Two copies, zero copies, one copy

Two processes cannot name each other's memory: virtual addresses are
per-process translations (module 01 ch03). Any byte that crosses must pass
through the one address space both trust — the kernel. Count copies.

A pipe or socket: `write(fd, p)` triggers `copy_from_user` into an `sk_buff`;
the receiver's `read()` triggers `copy_to_user` into its own buffer. Two
copies, the second into memory that didn't exist before — fine for a shell,
brutal for a UI thread making thousands of RPCs per second.

Shared memory (`shm`/`ashmem`): zero copies — both processes map the same
physical pages. But now you own everything the socket stack did: where a
message *starts*, who signals availability, what stops a malicious peer from
scribbling mid-struct. Zero copies bought a synchronization and trust problem
with no referee: copying is dumb and safe, sharing is fast and lawless.

Binder's insight: the kernel can do the copy *into shared pages*. At startup,
every process's `ProcessState` opens `/dev/binder` and mmaps a transaction
pool — `BINDER_VM_SIZE = 1 MiB − 2 pages = 1,040,384 B` with 4 KiB pages (AOSP
frameworks/native, Android 14): the fabled "1 MB", slightly under. When A
calls B, the driver does one `copy_from_user` of A's parcel, landing the bytes
**directly into pages of B's already-mapped pool**, queues the transaction, and
wakes one of B's binder threads. B's `IPCThreadState` wraps the parcel *in
place* — no second copy, because the kernel wrote into memory B can already
see. One copy, with the kernel as referee for queueing, identity, and teardown.

```
        SOCKETS / PIPES: 2 copies             BINDER: 1 copy
        ============================          ================================

 sender (userspace)   kernel            receiver (userspace)
 +----------------+ copy #1: copy_  +-----------+ copy #2: copy_  +-----------+
 | write(fd, p)   | from_user ----->| sk_buff   | to_user -------> | read(fd,q)|
 +----------------+                 +-----------+                  +-----------+

 sender (userspace)            kernel                receiver (userspace)
 +----------------+  the ONE   +----------------+   +------------------------------+
 | ioctl(/dev/    |  copy:     | binder driver  |   | 1 MiB mmap'd txn pool       |
 | binder, BINDER_| copy_from_ | lands bytes    |   | (1,040,384 B = 1 MiB - 2    |
 | WRITE_READ,    | user ----- | DIRECTLY in    |-->| pages; AOSP, 4 KiB pages,   |
 | Parcel)        |            | the receiver's |   | Android 14)                 |
 +----------------+            | pool pages     |   +------------------------------+
  calling thread parks          +----------------+   | [sync txns pending][free]   |
  in the ioctl until             | wake one of     |  async budget: at most half  |
  the reply                      | B's binder      |  the pool = 520,192 B        |
                                 | threads         +------------------------------+
                                                      receiver reads the Parcel IN
                                                      PLACE through its own
                                                      mapping: copy #2 never
                                                      happens
```

Two budget rules, kernel policy not knobs (binder driver, 5.15-era, Android 14):

```
 receiver's pool: ONE per process, shared by ALL in-flight transactions
 (not 1 MiB *per call* — the mistake everyone makes):

 +---------------------------------------------------------------------------+
 | [sync txn pending] [sync txn pending] [ free ..........................]  |
 +---------------------------------------------------------------------------+
   async (oneway) budget = pool / 2 = 520,192 B: outstanding oneway
   transactions together may never exceed half the pool, so oneway spam
   cannot starve sync callers. A budget, not a partition: oneway bytes still
   come out of the same free space.
```

Synchronous transactions may use the whole free pool, but each holds its
allocation until the reply is consumed — so four concurrent 300 KB calls
(1.2 MB) into one process fail while each alone "fits". Either budget blown
→ `BR_FAILED_REPLY` → Java's `TransactionTooLargeException`, a name that
undersells it: too large for the receiver's *shared* pool, not for a per-call
limit.

Since Treble (Android 8.0) there are three devices, three namespaces —
`/dev/binder` (framework), `/dev/hwbinder` (HAL), `/dev/vndbinder` (vendor) —
same driver, separate handle universes.

## Parcels: a wire format the kernel only half-parses

A `Parcel` is a byte stream of length-prefixed, 4-byte-aligned primitives:
`writeInt` is a raw 4 bytes; `writeString` writes an `int32` length (in UTF-16
code units), the UTF-16LE bytes, a 16-bit NUL terminator, then pads to a
4-byte boundary (libbinder `String16` format, 64-bit, Android 14). Alignment
buys cheap field access; length prefixes buy skip-ability without a schema.

The kernel must not understand any of it — except where *it* has work: binder
references and file descriptors both need fixups. Each travels as a
`flat_binder_object` (24 B on 64-bit) embedded *somewhere* in your bytes, and
the parcel carries an **offsets table** after the data — 8-byte `binder_size_t`
offsets, one per object — so the driver rewrites exactly those records without
parsing your payload:

```
 one Parcel on the wire (simplified; sizes in bytes, 64-bit):

 +0        +4          +4+2*len   +2             pad-to-4
 +---------+-----------+----------+--------------+-----------
 | int32   | int32     | len x    | 00 00        | 2 B pad   |
 | len=2   | len=3     | UTF-16LE | NUL          | if needed |
 +---------+-----------+----------+--------------+-----------
   writeInt(2)   writeString("abc"): 4 + 6 + 2 = 12 B, no pad needed

   ... more length-prefixed primitives; the driver never parses these ...

 +--------------------------------------------------------------+
 | offsets_count | binder_size_t offsets[0..n-1], 8 B each      | <- fixup
 +--------------------------------------------------------------+    table
 at payload offset offsets[0] sits a flat_binder_object (24 B, 64-bit):
 +--------------+--------------+-----------------+-----------------+
 | type u32     | flags u32    | handle / ptr u64| cookie u64      |
 | BINDER_TYPE_ | priority     | KERNEL REWRITES | aux pointer     |
 | HANDLE/FD/...| bits         | this field      | (local Binder*) |
 +--------------+--------------+-----------------+-----------------+
```

### Why fds ride alongside the buffer, never inside it

An fd *number* is an index into the current process's private file table.
Write `int fd = 37` into the payload and the receiver reads 37 — an index into
*its own, unrelated* table. So the fd travels as a `BINDER_TYPE_FD` flat
object: the sender's kernel does `fget(37)` to pin the `struct file`;
delivery installs a *new* fd number in the **receiver's** table
(`get_unused_fd` + `fd_install`). Numbers are per-process; only the kernel
can translate them — the `SCM_RIGHTS` choreography. Hence
`SharedMemory`/ashmem for big data: bytes through a shared region, only the
fd (24 B) through Binder.

`BINDER_TYPE_HANDLE` gets the same treatment: your process-local `Binder*`
becomes a small integer handle in the peer's kernel-managed ref table. Handles
are capabilities — holding one is the *only* way to address that object, and
the driver (plus SELinux binder hooks, stamped with the sender's uid/pid)
polices every hop.

## AIDL: a switch table you have already met

Nobody writes parcels by hand: describe the interface in AIDL and the
generator emits both sides — a `Stub` with an `onTransact` dispatch switch,
and a `Proxy` that packs and unpacks. It is the exact idiom of module 04 ch02's
loader walking relocation records `switch (r_type)`: one case per record type,
data-driven dispatch over a wire format. Here the record type is the
transaction code:

```java
// AIDL-generated, trimmed: com/example/IVersionService.java (Android 14)
public static abstract class Stub extends Binder implements IVersionService {
    static final int TRANSACTION_getVersion = IBinder.FIRST_CALL_TRANSACTION + 0;

    @Override
    public boolean onTransact(int code, Parcel data, Parcel reply, int flags) {
        switch (code) {                       // the dispatch table
            case TRANSACTION_getVersion: {
                data.enforceInterface(DESCRIPTOR);   // catch version skew
                int result = this.getVersion();      // your real method
                reply.writeNoException();
                reply.writeInt(result);
                return true;
            }
        }
        return super.onTransact(code, data, reply, flags);
    }
}
```

Codes start at `FIRST_CALL_TRANSACTION` (1) and increment per method in
declaration order — reordering AIDL methods is a wire compatibility break.
The proxy is the mirror: allocate parcels, `writeInterfaceToken`, marshal,
one `mRemote.transact(code, data, reply, 0)`, unmarshal. Oneway passes
`FLAG_ONEWAY`, skips the reply, returns immediately — the async budget now
applies to your bytes.

## The thread pool and the sixteenth thread

Copy economics solved delivery; someone must *run* the code. At pool start
(`ProcessState::startThreadPool`, plus the main thread's `BC_ENTER_LOOPER`),
one thread sits in the binder loop. When a transaction arrives with every
looped thread busy, the driver sends `BR_SPAWN_LOOPER` and userspace registers
another — capped at `DEFAULT_MAX_BINDER_THREADS =
15` (frameworks/native, Android 14). Main + 15 = **16 inbound binder
handlers per process**, fixed.

Now the deadlock lore. A synchronous binder call *consumes* its calling
thread — it parks inside the `ioctl` until the reply. Say all 15 binder
threads each handle a request whose handler makes a synchronous call *out* to
system_server, and those remote calls synchronously call back *in*. The
inbound transaction needs a 17th looped thread; the driver would spawn one,
but 15 is the cap. Every thread waits on a reply that can never be
delivered — deadlock, no lock in sight. Which is why `setThreadPoolMaxThreadCount()`
exists and framework callbacks are oneway.

## Handle 0: the root namespace and death notifications

Every process's handle table starts empty except one entry: **handle 0**, the
context manager — `servicemanager`, an init-started process that won the
kernel's `BINDER_SET_CONTEXT_MGR` ioctl (SELinux-gated). It is the root
namespace, a flat string→object map ("activity", "window", "package"):
`ServiceManager.getService("activity")` is just a parcel to handle 0; the
reply's `BINDER_TYPE_HANDLE` is installed as a new handle in *your* table.
Every AMS/WMS/PMS call descends from one such lookup — module 05 ch01's
system_server, reached this way.

Handles are references, so they dangle: the peer process can die. `linkToDeath`
(`BC_REQUEST_DEATH_NOTIFICATION`) registers interest; when the owner's
`/dev/binder` fd closes and its last ref drops, the kernel queues
`BR_DEAD_BINDER` to every recipient — Java surfaces it as `binderDied()` on a
`DeathRecipient`. That is the whole mechanism behind a stale cached service
proxy: no polling — the kernel knew, because it owns the refcounts.

## What insiders know

- **The 1 MiB is per *process* — and it's the receiver's.** `BINDER_VM_SIZE` =
  1 MiB − 2 pages = 1,040,384 B (AOSP, Android 14, 4 KiB pages);
  `TransactionTooLargeException` blames the sender but measures the *callee's*
  shared pool — a system service fails on payloads your own process could send
  itself.
- **The async half rule.** Outstanding oneway transactions together may not
  exceed pool/2 = 520,192 B (kernel binder driver, 5.15-era). Log spam of
  dropped oneway calls with no exception is this budget, not the full 1 MiB.
- **15 binder threads, +1 main, hard cap.** `DEFAULT_MAX_BINDER_THREADS = 15`;
  the 16th-thread starvation deadlock above is the lore. ANR traces show it
  as every "(binder)" thread parked in `transactNative`.
- **fds are the side channel that works.** Numbers are per-process indexes;
  only `BINDER_TYPE_FD` fixups (kernel `fget`/`fd_install`, like `SCM_RIGHTS`)
  make them portable — hence `SharedMemory` for bulk data, and a leaked fd
  is a genuine sandbox crossing (ch05 previews this).
- **Handle 0 is the only pre-installed handle in every process**, and only
  `servicemanager` may hold it (`BINDER_SET_CONTEXT_MGR`, SELinux-gated).
  `adb shell service list` is literally a dump of that root namespace.
- **Every binder call is one `ioctl(BINDER_WRITE_READ)`** — watch real IPC
  traffic in `strace` as a stream of them; write and read batches fused into
  one syscall is a big part of the speed.
- **The reply crashes elsewhere.** When a pending transaction fails, the
  classic symptom is `E/JavaBinder: !!! FAILED BINDER TRANSACTION !!! (parcel
  size = N)` on the *callee* and a crash in code the callee never wrote.

## See it yourself

No device was attached while writing this: the fragments below are documented
walkthroughs from AOSP sources (Android 14, API 34, kernel 5.15). Shapes are
right; your counts will differ. Everything but the root-only stats files runs
on any device.

**1. The driver's own accounting.** Older kernels expose `/proc/binder/`;
Android 11+ devices usually mount **binderfs** instead:

```console
$ adb shell ls /dev/binderfs/            # or: adb root on a userdebug build
binder  binder-control  hwbinder  vndbinder
$ adb shell cat /dev/binderfs/binder/stats    # root: mode 0400; older: /proc/binder/stats
binder stats:
BC_INCREFS 23341, BC_ACQUIRE 26102, BC_ENTER_LOOPER 1204, ...
BC_TRANSACTION 1845537, BC_REPLY 1842903, ...
proc
  1660 system_server
    ...
```

Notice `BC_TRANSACTION` vs `BC_REPLY` (roughly equal — most calls are
synchronous) and the per-process entries: exactly the processes holding
`/dev/binder` open. And the file nobody reads at 2 a.m. but should —
`binder/failed_transaction_log`, the kernel's timestamped, sized ledger of
every `BR_FAILED_REPLY`.

**2. Reproduce the 1 MiB wall.** No permissions, any device:

```kotlin
// MainActivity.kt — one intent, 2 MiB of payload, one binder transaction
val blob = ByteArray(2 * 1024 * 1024) { 'A'.code.toByte() }  // 2 MiB
val intent = Intent(this, TargetActivity::class.java)
    .putExtra("blob", blob)          // marshalled into the intent's Parcel
startActivity(intent)                // -> IActivityTaskManager.startActivity()
```

The transaction needs 2 MiB in a 1,040,384 B pool; you get the classic pair:

```text
E/JavaBinder: !!! FAILED BINDER TRANSACTION !!!  (parcel size = 2097772)
E/AndroidRuntime: FATAL EXCEPTION: main
  android.os.TransactionTooLargeException: data parcel size 2097772 bytes
      at android.os.BinderProxy.transactNative(Native Method)
      at android.os.BinderProxy.transact(BinderProxy.java:601)
      at android.app.IActivityTaskManager$Stub$Proxy.startActivity(...)
```

The parcel size is slightly over 2 MiB — length prefixes, the offsets table,
and the interface token ride along. Try 500 KB (it succeeds: a lone sync
transaction owns the whole pool), then four concurrent 300 KB transactions —
the pool is shared by everything in flight, so the fourth dies.

**3. Userspace's view of the wiring.**

```console
$ adb shell dumpsys activity services com.example.app
ACTIVITY MANAGER SERVICES (dumpsys activity services)
* ServiceRecord{9c1f7e u0 com.example.app/.Remote}
    intent={cmp=com.example.app/.Remote}
    packageName=com.example.app processName=com.example.app:remote
  * Client app=ProcessRecord{7a2b1c u0 a123 com.example.client/com.example.client}
```

Each `ServiceRecord` is a `BINDER_TYPE_HANDLE` in clothing: the
`* Client app=` lines are binder refs other processes hold, and `:remote`
means those refs cross a process boundary. Run it before and after killing
the service process — you are watching handle/ref lifecycle from 10,000 feet.

## How to use this knowledge

- **Design IPC payloads against the shared pool, not the per-call size.**
  Budget a few hundred KB total across everything in flight to that process;
  ship bulk data by fd (`SharedMemory`). The failure to design away is a
  burst, not a size.
- **Crash triage from logs alone.** A `TransactionTooLargeException` whose
  stack frames you don't recognize means a *reply* or pending transaction
  failed — `!!! FAILED BINDER TRANSACTION !!!` and `failed_transaction_log`
  tell you which side and size.
- **Read ANR traces quantitatively.** Count threads named `(binder)` parked
  in `transactNative` — 15 of them plus a nested sync call is the
  sixteenth-thread deadlock in an ANR costume.
- **Whiteboard the interview answer.** "Why is Binder fast?" — one copy, not
  two: receiver-mmap'd pool, kernel `copy_from_user` lands in place, dispatch
  fused into one `ioctl`. The 1 MiB number and its provenance
  (`BINDER_VM_SIZE`) is the credibility flourish.
- **Debug IPC with the kernel's ledger, not printf.** `binderfs` stats and
  the failed-transaction log see every process at once; printf in two
  processes sees a race-free fiction.

## Connections

- **Module 01 ch03 (virtual memory)** — the one-copy trick *is* mmap: kernel
  writes a page through its mapping, receiver reads it through another
  mapping of the same physical page.
- **Module 04 ch02 (linking & loading)** — `onTransact`'s code switch is the
  same idiom as the loader's `switch (r_type)`: dispatch over a wire format,
  with only the fixup records structured.
- **Module 05 ch01 (boot & the Zygote)** — the launch path deliberately uses
  a UNIX *socket*, not Binder — binder threads, mmaps, and handle tables are
  not fork-safe; binder joins after fork+specialize.
- **Module 06 ch03 (Mach ports & XPC)** — the comparison pair: both give
  kernel-mediated capabilities (handle tables vs port rights); Mach messages
  *transfer* rights with ownership semantics, Binder holds kernel refcounts
  with death notification instead.
- **Module 09 ch04 (actors & channels)** — Android's actor feel (`Handler`,
  `MessageQueue`, main-thread discipline) rides on oneway binder transactions:
  an async channel whose only backpressure is the 520,192 B budget.

## Quiz

1. The pool could live in the *sender's* mmap instead: copy from sender
   userspace to sender kernel pages, then a second copy to the receiver. What
   does the receiver-side placement eliminate, and how can the kernel write
   into pages the receiver maps?
2. An AIDL method takes one `String` of 1,000 ASCII chars, marshalled with
   `writeString` (libbinder `String16`, 64-bit, Android 14). How many bytes
   does the string occupy on the wire?
3. An app makes a single **oneway** call carrying a 700 KB byte array;
   nothing else is in flight and 700 KB < 1 MiB. It fails anyway. Which
   budget, what size, and what is it protecting?
4. A media app sends four concurrent synchronous AIDL calls of 300 KB each
   (from four threads) into a service process. The fourth throws
   `TransactionTooLargeException`; each call is under every single-call limit
   you can find. Diagnose.
5. Process P's 15 binder threads each execute a request whose handler makes a
   *synchronous* call out to Q, and each of those calls at Q synchronously
   calls back into P. Explain the stall and why the driver cannot rescue it.
6. A developer writes `parcel.writeInt(fd)`; the peer's `readInt` returns a
   number that fails in every `read()` the peer attempts. Why, and what
   mechanism makes fds work instead?
7. The receiver never `read()`s the transaction bytes, yet its loads return
   kernel-written data. Name the module 01 ch03 mechanism making one physical
   page visible under two mappings, and state which side ever dirties the
   pages.
8. In Mach (module 06 ch03), a message can *transfer* a port send right into
   the receiver. What is Binder's analogue when you pass a binder object, and
   what differs about who tracks liveness?

<details>
<summary>Answers</summary>

1. **Receiver-side placement eliminates copy #2.** A sender-side pool means
   kernel pages the receiver cannot address, so delivery needs a
   `copy_to_user` into receiver memory — the socket fate. With the pool in the
   receiver's mapping, the single `copy_from_user` lands in physical pages the
   receiver's page tables already translate; the kernel writes through its own
   alias, the receiver wraps the bytes in place. Only the kernel's copy dirties
   the pages.

2. **2,008 bytes.** int32 prefix (4) counts UTF-16 units; 1,000 ASCII chars =
   2,000 bytes of UTF-16LE; 16-bit NUL terminator (2) → 2,006, padded to 4
   → **2,008**. The traps: 1 byte per char, or a forgotten terminator.

3. **The async budget: pool/2 = 520,192 B** (kernel `binder_alloc`). 700 KB >
   520 KB, so the driver refuses it even with the pool otherwise empty; Java
   typically surfaces the failed oneway call as `DeadObjectException`, or
   loses it silently. The rule protects sync callers — oneway spam can never
   take more than half the pool, so a sync call always has somewhere to land.

4. **The pool is shared by all in-flight transactions to that process.**
   4 × 300 KB = 1.2 MB > 1,040,384 B: the fourth reservation fails though each
   call is individually fine. And the pool belongs to the *receiver* — "my app
   is under 1 MB" was never the test. Fix: serialize, chunk, or move the
   bytes behind an fd (`SharedMemory`).

5. **Sixteenth-thread starvation.** A sync call consumes its calling thread
   until the reply. All 15 binder threads (plus a busy main thread) are parked
   in outbound `transact`, each waiting on a reply Q only produces by calling
   back *into* P — which needs an inbound looped thread. The driver would
   spawn thread 16, but the cap is 15, so it queues: everyone waits, nobody
   runs. Hence nested synchronous IPC is forbidden in framework code and
   callbacks are oneway.

6. **fd numbers are per-process table indexes.** `37` names entry 37 of the
   *sender's* file table; in the peer it names something unrelated (often
   nothing — EBADF). Fds work via a `BINDER_TYPE_FD` object: the sender's
   kernel resolves it with `fget()` to the shared `struct file`; delivery
   installs a fresh fd in the receiver (`get_unused_fd` +
   `fd_install`). The number changes, the file doesn't — `SCM_RIGHTS`
   choreography.

7. **mmap: one physical page under two mappings.** The driver maps pool pages
   into the receiver's address space (module 01 ch03 — the page is the unit
   of sharing); the kernel writes through its mapping, the receiver reads
   through its own. Only the kernel's delivery copy dirties the pages — the
   receiver only reads, so no COW, no copy: the data is simply already there.

8. **Analogue: writing a binder object into a parcel** emits a
   `BINDER_TYPE_HANDLE` fixup; the driver installs a new, refcounted handle
   in the receiver's ref table — a capability appears on the peer.
   Difference: Mach moves *ownership* with the message (the sender may lose
   the right); Binder keeps both sides as independent refs in one kernel
   table, deriving liveness from the owner's `/dev/binder` fd closing — the
   death-notification path.

</details>
