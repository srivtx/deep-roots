# Module 05 final exam — Android internals

Twelve questions across all six chapters. Closed book; answers are worked,
not stated — the explanations are the point. Scoring: 11+ = you own this
module; 8-10 = solid, revisit the flagged chapters; <8 = the chapter quizzes
are the study path.

**Part I — boot & ART (chapters 01-02)**

1. *(derive, cross-module 01 ch03)* Zygote's preloaded framework footprint is
   F = 128 MB; 24 forked apps each dirty d = 2 MB of private COW copies.
   Compute total physical RAM for the naive JVM-per-app design and the
   zygote design. A change then makes a 16 MB preloaded region write-hot;
   per-app dirty memory rises to 14 MB. Recompute, name the broken
   invariant, and give the `dumpsys meminfo` column that shows the damage.

2. *(concept)* AMS triggers every app fork through `/dev/socket/zygote`, a
   plain UNIX socket init created — not through Binder. Give two independent
   reasons: bootstrap ordering, and what `fork()` does to multi-threaded IPC
   state.

3. *(derive, cross-module 01 ch12)* A class has two reference fields and one
   int. Compute per-object size and the footprint of one million instances
   with ART's compressed references on and off. State the decode formula,
   why ART has no 32 GB cliff where HotSpot does, and the header arithmetic
   behind 8 vs HotSpot's 12 bytes.

4. *(diagnosis)* A news app throws `OutOfMemoryError` after 20 minutes of
   image-heavy scrolling. The profiler shows large-object space growing
   monotonically, allocation space flat, GC pauses sub-ms. What is leaking,
   why can compaction never reclaim it, what would an object-graph leak show
   instead, and what is the fix class?

**Part II — talking & dying (chapters 03-04)**

5. *(derive)* (a) An AIDL method takes one `String` of 2,000 ASCII chars,
   marshalled with `writeString` (String16, 64-bit): how many bytes on the
   wire? (b) A lone oneway call carrying a 600 KB byte array, nothing else
   in flight — does it succeed? (c) Three concurrent synchronous calls of
   350 KB each into one service: how many land?

6. *(diagnosis)* A client queries a service whose reply is 1,350,213 bytes.
   The client crashes with `TransactionTooLargeException` in
   `IQueryService$Stub$Proxy`; the *service* logs
   `!!! FAILED BINDER TRANSACTION !!! (parcel size = 1350213)`. The team
   argues the client has 400 MB free. Explain whose pool and budget failed,
   why the crash lands in the client's stack, and the fix.

7. *(diagnosis)* An ANR trace shows the main thread and all 15 `(binder)`
   threads parked in `transactNative`; the peer's binder threads are each
   parked in an outbound synchronous `transact` back into the ANR'd app.
   What is everyone waiting for, why can't the driver fix it, and what design
   rule keeps the framework out of this shape?

8. *(derive)* lmkd must free memory now. Candidates: P (adj 935, RSS
   120 MB), Q (adj 906, RSS 400 MB), R (adj 200, RSS 500 MB, foreground
   music service), S (adj 912, RSS 90 MB). Give the kill order, explain why
   R's 500 MB never enters the arithmetic, and state the refinement newer
   builds add.

9. *(diagnosis)* A messaging app runs its file-sync on a plain background
   thread. Logcat shows
   `ActivityManager: Killing 5112:com.msg/u0a37 (adj 906): cached #17`.
   (a) Why was an actively working process at adj 906? (b) Which kill path
   fired, and what would the other path log? (c) Give the
   two-part fix and the ch01 fact that makes the kill cheap to recover
   from.

**Part III — walls & shipping (chapters 05-06)**

10. *(concept)* An attacker has full RCE in app A (uid 10145,
    `untrusted_app`). Three attempts: (a) read app B's private file,
    accidentally `chmod 0644` — the uid wall failed open; (b) call
    `userfaultfd` to groom the kernel heap; (c) `ptrace` app C's process.
    For each: which wall answers, with what verdict?

11. *(derive)* Your dex has 65,400 method-id references. A feature adds 180
    call sites to already-referenced methods and 140 references to new
    ones. Does the build succeed? If not, what exactly does d8 report, why
    do the 180 call sites cost nothing, and what are the two standard ways
    out?

12. *(concept, cross-chapter)* (a) How does Android update the system it is
    running from — the slots, and where the "inactive slot" lives on a
    Virtual A/B device? (b) Power is pulled mid-update: what boots next,
    and why? (c) Why does the OTA pipeline need Puffin
    instead of plain bsdiff? (d) Where do the APK verifier's keys live, and
    what keeps those bytes from changing after boot?

<details>
<summary>Answers</summary>

**Part I**

1. Naive: every app pays a private warm JVM: **N × F = 24 × 128 = 3,072
   MB**. Zygote: `fork()` copies page tables, not pages — **F + N·d =
   128 + 48 = 176 MB**, ~17× (ch01 §3; module 01 ch03). Write-hot region:
   **128 + 24 × 14 = 464 MB** — the ratio collapses to ~6.6×. The broken
   invariant is the *read-mostly discipline*: apps must write few framework
   pages — ART allocates post-fork objects away from the preloaded heap for
   this reason (ch02). The damage shows as **Private Dirty** — the COW
   copies; shared pages stay dirty-0 (ch01).

2. **Ordering:** AMS lives in system_server, zygote's *first fork* — the
   fork client cannot exist before zygote does; the trigger must predate
   the IPC universe it populates. **Fork safety:** binder state is
   process-global and multi-threaded (thread pool, mmap'd pool, handle
   tables), and `fork()` clones only the calling thread: forking mid-binder
   hands the child locks whose owners don't exist there (ch01 §4). Hence an
   init-created socket (mode 660 root:system), a single-threaded zygote in
   `accept()`, binder joining the child only after fork+specialize (ch03).

3. On: 8-byte header + 2 × 4-byte refs + 4-byte int = 20 → **24 bytes**
   (8-byte alignment). Off: 8 + 2 × 8 + 4 = 28 → **32 bytes**; a million
   instances: **24 MB vs 32 MB (25% saved)**. ART reserves one 4 GB-aligned
   window and stores each reference's **low 32 bits** — decode
   `window_base | stored32`, no shift. HotSpot stores `address >> 3`,
   buying 32 GB of reach, hence its cliff where references widen to 8
   bytes; ART's `heapgrowthlimit` caps heaps in the hundreds of MB, so the
   window never binds (ch02; module 01 ch12). Header: **class 4 + lock 4 =
   8 bytes** — the lock word does quadruple duty (thin lock, fat lock,
   identity hash, GC forwarding); HotSpot pays 12.

4. **Large primitive arrays retained in large-object space** — `byte[]`
   decode/serialization buffers. LOS admits only reference-free primitive
   arrays ≥ 12 KB (`kLargeObjectThreshold = 3 × 4 KB`); LOS objects are
   page-mapped and **never moved or compacted** (freed only by unmap), so
   retention is permanent page-granular cost no collection fixes. An
   object-graph leak would show **allocation-space growth with LOS flat**;
   the space split is the first-pass classifier (ch02; module 01 ch19).
   Fix: downsample, stream, and reuse the big buffers.

**Part II**

5. **(a)** int32 length prefix (4, in UTF-16 units) + 2,000 × 2 = 4,000
   UTF-16LE bytes + 16-bit NUL (2) = 4,006, padded → **4,008 bytes**.
   **(b)** No: oneway transactions together may not exceed **pool/2 =
   520,192 B**; 600 KB = 614,400 exceeds it even with the pool empty; the
   budget protects sync callers from oneway starvation. **(c)** Two:
   3 × 350 KB = 1,075,200 B > **BINDER_VM_SIZE = 1,040,384 B** (1 MiB − 2
   pages). The pool is per-process, the *receiver's*, shared by all in
   flight; the third fails though each call alone fits (ch03).

6. The reply is itself a binder transaction whose receiver is the *client*:
   it must fit the **client's own 1 MiB mmap'd pool**; 1,350,213 >
   1,040,384 → `BR_FAILED_REPLY`. The service's log marks its failed
   reply; the exception surfaces in the client's proxy — code the
   service's author never wrote (ch03). Free RAM is irrelevant: the limit
   is per-process buffer geometry. Fix: paginate the query, or ship bytes
   via `SharedMemory` — only a 24-byte `BINDER_TYPE_FD` rides the parcel;
   the payload never crosses Binder (ch03).

7. **Sixteenth-thread starvation.** A synchronous call consumes its calling
   thread until the reply. All 15 pool threads
   (`DEFAULT_MAX_BINDER_THREADS = 15`) plus main are parked in
   `transactNative`, each waiting on a reply that needs the peer to call
   *back into* the app — which needs a 16th looped thread. The driver would
   spawn one (`BR_SPAWN_LOOPER`), but 15 is a hard cap: everyone waits on a
   delivery the kernel cannot make. Main parked in a binder ioctl past
   input dispatch is the ANR. The rule: **no nested synchronous
   IPC** — framework callbacks are oneway (ch03).

8. **P (935), then S (912), then Q (906).** adj first, RSS as tie-breaker:
   importance dominates, so S's 90 MB dies before Q's 400 MB. **R never
   enters the arithmetic:** adj 200 is the perceptible band, below the 900
   cached cliff — a foreground service anchors importance independently of
   RSS; the system won't trade audible playback for 500 MB (ch04). No
   heap, CPU, or age appears: lmkd executes the ranking AMS wrote. Newer
   refinement (Android 12+): per-process zram swap-in cost — a large *cold*
   process can out-rank a slightly higher-adj small one.

9. **(a)** adj comes from lifecycle *components* (visible activity,
   foreground service, bound service), never CPU or threads: a bare
   `Thread` registers no importance, so `onStop` demoted it to the cached
   band (900–999, slot 906). **(b)** The budget path: `(adj 906): cached
   #17` is AMS evicting the LRU cached process past
   `DEFAULT_MAX_CACHED_PROCESSES = 32`; the pressure path logs under the
   `lowmemorykiller` tag — `Killing ... to free NkB rss` — after a PSI
   trigger (70 ms some-stall/s). **(c)** Fix 1: a typed foreground service
   while syncing (adj ~200, below the cliff, freezer-exempt). Fix 2: design
   for death — WorkManager plus persisted progress. Relaunch is cheap: a
   zygote fork over resident COW-shared pages (ch01); state survives only
   if serialized (ch04).

**Part III**

10. **(a) SELinux MLS categories.** Labels come from `seapp_contexts`
    (`levelFrom=all`): A runs `s0:c23,c256`, B's file carries `s0:c7,c257`;
    the category constraint requires matching sets, checked at the kernel's
    SELinux hook: `avc: denied { read } ... permissive=0`. **(b)
    seccomp-BPF, verdict ERRNO → EPERM**, at syscall entry, before the
    kernel touches the subsystem: `userfaultfd` is on the app deny list;
    filters are a one-way ratchet, inherited across fork, only
    intersectable with stricter. **(c) The uid wall first:** ptrace of a
    foreign uid dies `EPERM` in the owner check (SELinux denies it for
    `untrusted_app` besides). What survives all three: the binder surface,
    the map's soft part (ch05).

11. **Fails: 65,400 + 140 = 65,540 > 65,536**; d8 reports `Too many method
    references: 65540; max is 65536`. The 180 call sites cost nothing
    because `invoke-virtual {v0, v1}, method@BBBB` carries a **16-bit index
    into the method_ids table**; new call sites reuse existing entries, ids
    dedupe. New *references* consume the u16 address space (2^16 = 65,536),
    counting every referenced framework method, not just yours (ch06 §2).
    Ways out: **R8** (deleting unreferenced methods deletes their ids) or
    **multidex**: `classes.dex` keeps startup classes, `classes2.dex` the
    rest (native in ART since 5.0).

12. **(a)** A/B doubles the system partitions (`_a`/`_b`); the running slot
    is never written — `update_engine` streams the new image into the
    inactive slot, then flips the boot flag in `misc` (retry count 7;
    unhealthy slots fall back). Virtual A/B (Android 11+) drops the
    physical second slot: the diff applies into a **copy-on-write snapshot
    on /data** (ch01's fork trick at partition scale), merged by
    device-mapper once healthy. **(b)** The old slot: never written, so it
    boots; half-applied snapshot data is discarded — power loss mid-update
    is a non-event. **(c)** bsdiff works on uncompressed bytes; deflate
    streams avalanche under small input changes. Puffin "puffs" each stream
    to a canonical bit-level form, diffs *those*, re-deflates
    deterministically — the patched file is bit-identical, manifest hashes
    match. **(d)** The platform cert store on `/system`; **dm-verity** — a
    Merkle tree over its 4 KiB blocks, root hash signed in `vbmeta` via
    AVB, chained to the ROM — checks blocks *as they are paged in*; the
    store cannot rot at runtime (ch06 §4, ch01 §1). ROM → AVB → dm-verity →
    certs → APK → fork: hash the next thing before you trust it.

</details>
