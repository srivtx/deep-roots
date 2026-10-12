# Chapter 03 — Mach ports & XPC

> **The 30-second version.** Two processes cannot name each other's memory
> (module 01 ch03), so IPC is a memory problem. A Mach port is a message
> queue that lives *in the kernel*; the right to use
> it (send, receive, send-once, port set) is an entry in a per-task table you
> can only acquire by being handed one — an unforgeable capability. Messages
> are a 24-byte header plus typed descriptors; an out-of-line (OOL)
> descriptor is memory the kernel copies — or, page-aligned, *moves* — into
> the receiver's space, transferring page ownership. MIG compiles interface
> definitions into RPC stubs (module 05 ch03's AIDL pattern, again), and XPC
> wraps it all: launchd-bootstrapped services, typed dictionaries, a
> kernel-stamped audit token naming the caller. Rights are reference-counted
> kernel objects, so dangling rights are impossible by construction — an
> entire use-after-free family (module 03 ch02) dies here.

## The referee problem: why the queue lives in the kernel

Two tasks share zero memory (module 06 ch01: a task is a VM map, threads,
and a port table), so anything A tells B crosses through the one address
space both trust — the kernel. A pipe or socket (the BSD half of XNU) makes
the crossing with two copies. Copying solves neither identity nor authority:

- **Identity.** If "who sent this" is a field the sender wrote, the answer
  is a lie; only the kernel witnessed the call.
- **Authority.** "Check a uid on every operation" is *ambient* authority:
  permission derives from who the process is, not what it was handed. If B
  holds power and A can talk B into exercising it, B is a confused deputy —
  and the check runs *before* the use, a check-to-use window to race.

Mach's answer (CMU, mid-1980s; inherited by XNU essentially unchanged):
make the queue a kernel object. A **port** is a kernel-protected message
queue with a bounded backlog, and one trap — `mach_msg` — sends, receives,
and does timed variants of both. It is a Mach trap, not a BSD syscall
(module 06 ch01's two number spaces). The kernel referees queueing, identity
(it stamps the sender into the message), and teardown.

## A right, not a name: the capability derivation

User code refers to a port by a `mach_port_name_t` — a 32-bit index into
*your task's* port table; the kernel entry behind it says which right you
hold. Why this is a capability, not a permission string:

1. **You cannot manufacture a table entry.** Entries arise three ways:
   inherited at spawn, inserted when a message *carrying a right* is
   delivered to you, or created by `mach_port_allocate`. Guessing a name
   achieves nothing: no entry, no right, `mach_msg` fails with
   `MACH_SEND_INVALID_DESTINATION`.
2. **Every use is validated against the table.** The permission check and
   the capability are the same object — no separate check to race.
   Possession *is* the permission.
3. **The table is kernel memory**, which your process cannot write (module
   01 ch03's page tables are the premise). The handle is an index, not a
   pointer — exactly what makes it unforgeable.

The right kinds (XNU as of macOS 14 / iOS 17, arm64):

- **Send right** — enqueue; any number of holders, reference counted.
- **Receive right** — dequeue; exactly *one* holder per port, ever. Holding
  it *is* being the service.
- **Send-once right** — enqueue exactly one message, then self-destructs;
  the reply mechanism, derived below.
- **Port set** — a container of receive rights; one blocking receive
  returns the first message from any member, so one thread watches many
  services.
- **Dead name** — what a right becomes when the port dies: inert but
  observable — how you learn a peer is gone.

The load-bearing mechanism: **you can send a send right inside a message.**
The kernel parses the descriptor, bumps the reference count, inserts a fresh
right into the receiver's table. That one operation is capability
delegation — launchd, XPC, and every handshake below is this move repeated.

## The message: 24 bytes of header and descriptors that change hands

A message is a fixed header, an optional descriptor list, and inline data.
On a 64-bit Apple platform (XNU, macOS 14) the header is 24 bytes — six
32-bit words, *no* 64-bit fields, because ports are names, not pointers.

```text
offset  field              size   meaning
------  -----------------  -----  ----------------------------------
  0     msgh_bits          4 B    descriptor types; complex flag
  4     msgh_size          4 B    whole message size in bytes
  8     msgh_remote_port   4 B    destination (send) / reply (rcv)
 12     msgh_local_port    4 B    reply port / sender
 16     msgh_voucher_port  4 B    vouchers (10.9 era); usually NULL
 20     msgh_id            4 B    operation number (MIG subsystem)
------  -----------------  -----  ----------------------------------
 total                      24 B  (six 32-bit words, no 64-bit fields)
```

If `msgh_bits` marks the message *complex*, the body is a typed descriptor
list the kernel must parse — Binder's offsets-table idea (module 05 ch03): a
wire format the kernel only half-parses. A **port descriptor** moves a right
to the receiver; an **OOL ports descriptor** moves an array of them; an
**OOL memory descriptor** is where IPC meets memory lifecycle.

Inline data is copied into a kernel message object (`ipc_kmsg`, from module
06 ch01's kalloc zones) at send and into the receiver's buffer at receive —
two copies, fine for kilobytes. An OOL descriptor instead names a range in
the *sender's* space, and at send time the kernel wraps those pages in a
`vm_map_copy` object:

```text
     SENDER (task A)            KERNEL                      RECEIVER (task B)
+-------------------+  +------------------------+  +--------------------------+
| mach_msg(SEND)    |  | port 0x2f03: the queue |  | thread blocked in        |
|  header 24 B      |->| [msg A][msg B][...]    |->| mach_msg(RCV)            |
|  remote = 0x2f03  |  | one kmsg each          |  | header+descriptors copied|
|  local  = 0x33    |  | (kalloc-backed)        |  | into the rcv buffer      |
|  (send-once reply |  +------------------------+  +--------------------------+
|   right)          |  | OOL 64 KB descriptor:  |  | OOL 64 KB re-appears at  |
| OOL 64 KB at      |  | COPY: pages copied     |  | a kernel-chosen address, |
|  0x10400000       |  | ONCE into kernel mem;  |  | MAPPED into B's map.     |
| sender's pages    |  | aligned+deallocate:    |  | B OWNS them and must     |
| UNTOUCHED (COPY)  |  | pages MOVE, 0 copies   |  | vm_deallocate() them.    |
| or GONE           |  +------------------------+  +--------------------------+
+-------------------+
                       last send right dies -> NO_SENDERS notification
                       to the receive-right holder
```

The receiver's descriptor comes back with a *new* address: fresh pages
mapped into its map, which the receiver owns and must `vm_deallocate` —
ownership transfer, not sharing. With the `deallocate` bit set and the range
page-aligned, the kernel *moves* the pages: zero copies, the sender's mapping
vanishes, the pages reappear in the receiver — the ownership semantics Rust
and Erlang give message passing (module 09 ch04), built from module 01
ch03's page-mapping machinery.

The cost ledger: inline = two copies of few bytes; OOL = one copy (or zero)
plus remapping plus receiver-owned teardown. The theoretical inline ceiling,
`MACH_MSG_SIZE_MAX`, is just under 2 GiB (XNU), but kmsg bodies come from
kalloc and the idiom is kilobytes inline, pages out-of-line — the same ledger
as Binder: sockets 2, Binder 1 (into a mapped pool), Mach inline 2, Mach OOL
1-or-0 plus remapping.

## MIG: the stub generator you already met as AIDL

Raw `mach_msg` means hand-built headers, descriptor lists, reply ports,
error paths. MIG (Mach Interface Generator) compiles a `.defs` interface into
C: client stubs that marshal arguments and await the reply, plus a
server-side dispatch table and a `mach_msg_server` receive loop; the
routine's number rides in `msgh_id`.

Two disassembly recognition keys (module 04's skill): MIG **reply IDs are
request ID + 100** — a rule from the CMU server-writer's guide that XNU's
generated code still follows — and server routines are emitted with an `_X`
prefix, dispatched from a per-subsystem table; `mig_get_reply_port` and
`MIG_TYPE_ERROR` complete the fingerprint. It is exactly AIDL's architecture
(module 05 ch03): IDL in, stubs plus a dispatch switch out.

## Port death, no-more-senders, and the bug class that dies here

When a task exits, the kernel walks its port table and drops every right
(every right is a reference-counted kernel object). Two notifications turn
that bookkeeping into application-visible lifecycle:

- **`MACH_NOTIFY_NO_SENDERS`** — delivered to the receive-right holder when
  the last send right dies. Not default: you arm it with
  `mach_port_request_notification`, and it carries the port's *make-send
  count* (monotone count of send rights ever created) so a late arming is
  detectable — arm before publishing the right via launchd. This is how a
  service notices it has no clients left.
- **Dead name** — when a port is destroyed, every right still held on it
  converts to a dead name; a client holding a send right to a crashed
  service learns of the death this way, and its next send returns
  `MACH_SEND_INVALID_DESTINATION`.

Send-once closes the reply loop: each MIG request carries a fresh send-once
right to a reply port; the server's one reply consumes it, and a server
death turns the client's right into a dead name — completion-or-death with
no polling.

The payoff: *dangling rights are impossible.* Module 03 ch02's
use-after-free is a pointer that outlives its object, so the memory is reused
and the stale handle resolves to live-but-wrong data. Here the handle is an
index into a kernel table validated on every use; a dropped right is an entry
the kernel removed. A stale name is either invalid (clean error) or — after
slot recycling — a name the *kernel* reassigned to a new right: you cannot
manufacture "a valid right to an object I shouldn't reach." The UAF family is
not patched here; it is defined out of existence. What survives is logic —
who *you* delegated rights to — which is why iOS exploit chains start with
logic bugs, not memory bugs.

## XPC: launchd, typed dictionaries, and the audit token

XPC (libxpc) is the user-space wrapper, every piece a thin layer over the
machinery above:

- **Bootstrapping.** launchd (pid 1) holds the receive rights. Every task
  inherits a *bootstrap port* (a task special port) from its parent. A
  daemon's plist carries a `MachServices` dictionary: launchd creates the
  named port, holds the receive right, spawns the daemon on demand, and
  hands the receive right over at `bootstrap_check_in` — with the first
  client message already waiting. Clients get a send right via
  `bootstrap_look_up(bootstrap_port, "name")` — spawn-on-first-lookup,
  servicemanager's role (module 05 ch03). Lookups are scoped to *domains* —
  system (LaunchDaemons) versus per-login GUI (LaunchAgents) on macOS; a
  service in one is invisible from the other.
- **Serialization.** An XPC message is a typed tree — `xpc_dictionary` of
  `xpc_string`, `xpc_int64`, `xpc_bool`, `xpc_data`, `xpc_uuid`,
  `xpc_array`: JSON with typed atoms. Underneath, libxpc builds a Mach
  message, and large `xpc_data` buffers ride OOL descriptors.
- **Caller identity.** The **audit token** is 32 bytes — eight 32-bit words:
  pid, pid-version (guards pid reuse), real and effective uid/gid, audit
  session id, a version word, and (macOS 12.3 / iOS 15.4+) an eighth word of
  kernel-validated code-signing identity. It is *not* in the message body:
  the kernel stamps it into the trailer of every Mach message received with
  the audit trailer requested — the sender cannot forge what it cannot
  write.

The pattern this enables is privilege separation: a large untrusted UI app
plus a small XPC daemon holding the entitlement or the root access — the
*small trusted core*, whose attack surface is its handlers, whose first job
per message is to check the audit token against a code-signing requirement
(`SecCodeCopyGuestWithAttributes`). The capability model authenticates the
*connection*; what you do with that is still your policy — XPC bugs are
almost always policy bugs.

## What insiders know

- `mach_msg_header_t` is 24 bytes on 64-bit Apple platforms — six 32-bit
  words including `msgh_voucher_port`, which nearly everyone leaves as
  `MACH_VOUCHER_NULL` (the OS X 10.9-era voucher mechanism). Ports staying
  32-bit *names* on LP64 is the unforgeability story in miniature.
- MIG reply IDs are request ID + 100; server routines carry the `_X`
  prefix; the per-subsystem dispatch table plus `mach_msg_server` and
  `MIG_TYPE_ERROR` are your MIG fingerprint in disassembly.
- `xpc_connection_get_audit_token` returns the token of the *most recently
  received message*, so it changes per message. Authorize inside the handler,
  never once at setup — the classic XPC TOCTOU.
- `MACH_NOTIFY_NO_SENDERS` must be armed, and it carries the make-send
  count precisely so you can detect arming late — arm before publishing the
  right through launchd.
- On iOS, `task_for_pid` on another process needs an entitlement (debug
  builds get `get-task-allow`), so Mach-level debugging of *other* apps is
  Mac-only (module 06 ch05 signs that gate).

## See it yourself

On a Mac (commands from macOS 14, arm64; if you have no Mac, read this as a
documented walkthrough — output fragments abridged, field names as shipped).

**1. launchd's service table.**

```bash
sudo launchctl print system/com.apple.securityd
```

```text
        domain = system
        mach services = {
                "com.apple.securityd" => 0 users
        }
```

The `mach services` block is the `MachServices` registration — the
bootstrap name whose receive right launchd owns or has handed off.
`launchctl print gui/$(id -u)` shows the per-login GUI domain.

**2. A minimal XPC service, anatomized.** An XPC bundle's `Info.plist` is
nearly empty on purpose:

```xml
<dict>
    <key>CFBundleIdentifier</key><string>com.acme.thumbnailer</string>
    <key>CFBundlePackageType</key><string>XPC!</string>
    <key>XPCService</key>
    <dict>
        <key>ServiceType</key><string>Application</string>
        <key>RunLoopType</key><string>dispatch_main</string>
    </dict>
</dict>
```

`XPC!` tells the loader this is a service, not an app; `RunLoopType:
dispatch_main` says the service's main is a GCD run loop (the
`EVFILT_MACHPORT` receive loop). The launchd side registers the name:

```xml
<key>MachServices</key>
<dict><key>com.acme.thumbnailer</key><true/></dict>
```

That key is the bootstrap registration — the name launchd answers
`bootstrap_look_up` for; find live examples with
`plutil -p /System/Library/LaunchDaemons/*.plist`.

**3. Ports on a live process.**

```bash
sudo lsmp -p 1                          # launchd: hundreds of receive rights
lsmp -p $(pgrep -x Finder)
lsof -p $(pgrep -x Finder) | grep KQUEUE
```

```text
$ lsmp -p 501                     # abridged
  port name    rights
    0x103      send               <- client rights: just names
    0x2f03     recv               <- the one and only receive right
    0x3503     send-once
    0x400a     dead name
```

`lsmp` shows the port table with the right kind per name — the capability
table made visible. In `lsof`, the KQUEUE rows are the `EVFILT_MACHPORT`
watchers GCD uses to drain port sets — the receive loop, in fd-table form.

## How to use this knowledge

- **Privilege-separation design.** Put the entitlement in the smallest XPC
  daemon that can hold it; keep the UI app sandboxed. Design the API for
  hostile callers, and gate every handler on a per-message audit-token check
  against a code-signing requirement — not on the existence of a
  connection.
- **IPC debugging.** "Connection invalidated" is a dead-name notification:
  the peer's receive right is gone. Work the ladder: did the service crash
  (launchd relaunches it — reconnect, don't cache endpoints), is the name
  registered (`mach services` in `launchctl print`), and are you in the
  *same bootstrap domain*?
- **Performance decisions.** Run the copy ledger before shipping payloads:
  kilobytes inline, pages OOL, aligned-plus-deallocate for zero-copy
  handoffs. A 2 MB `xpc_data` per keystroke is a self-inflicted battery drain.
- **Reverse engineering.** MIG fingerprints (`_X` routines, reply ID + 100,
  subsystem tables) let you name a server's API from a dump before reading a
  handler — and the audit-token check (or its absence) is the first thing to
  audit in any XPC daemon. "Why do iOS exploit
  chains start with logic bugs?" — because this layer made dangling rights
  impossible, leaving policy mistakes in capability *delegation*.

## Connections

- **Module 05 ch03 (Binder)** — the copies-counting derivation repeats one
  for one; AIDL ↔ MIG, servicemanager ↔ launchd, Parcel offsets table ↔
  Mach descriptor list; Binder's one copy into a mapped pool versus Mach's
  OOL remap.
- **Module 01 ch03 (virtual memory)** — OOL messaging *is* the VM machinery
  (page mapping, `vm_map_copy`, deallocation) reused as transport.
- **Module 09 ch04 (actors & channels)** — a port is an actor mailbox;
  send-once and OOL transfer are message-passing-as-ownership-transfer;
  port sets are the selective-receive problem in kernel clothing.
- **Module 03 ch02 (UAF family)** — kernel-table validation plus
  reference-counted rights define dangling rights out of existence: "every
  pointer needs an owner," enforced by the kernel.
- **Module 06 ch01** — tasks own port tables; **module 06 ch05** — the
  audit token's eighth word and the code-signing machinery behind it.

## Quiz

1. On a 64-bit Mac (XNU, macOS 14), how many bytes is `mach_msg_header_t`,
   why doesn't it grow on LP64, and which field would you remove to recover
   the 20-byte pre-voucher layout — breaking what?
2. Task A sends task B a 64 KB buffer as an OOL descriptor with plain COPY
   semantics. B crashes after the message is queued but before receiving it.
   What happens to the 64 KB, and who ran the code that decided that?
3. An XPC handler reads a `"uid"` integer from the message dictionary and,
   if it equals 0, performs the privileged operation. Name the bug, the
   threat, and the fix.
4. A daemon calls `bootstrap_look_up` for a helper on every request but
   never calls `mach_port_deallocate` on the returned send right. What
   grows, what never fires, and why is this worse than a normal leak?
5. Why does MIG build its reply port as a send-once right rather than a
   plain send right? Give the security reason and the lifecycle reason.
6. "Checking a uid on every operation" and "holding a send right" both
   control access. What does the first permit that the second makes
   structurally impossible? Use the confused deputy and the check-to-use
   window.
7. In a kernel disassembly you find `_Xhost_info`, a table of function
   pointers indexed by message ID, and replies whose IDs are exactly 100
   more than the requests. What generated this code, and what is the table?
8. Compare three transports for a 4 MB payload: a POSIX socket, a Binder
   transaction (module 05 ch03), and a Mach OOL descriptor with
   page-aligned `deallocate`. Count copies, name who frees, and say which
   hits a hard size limit first.

<details>
<summary>Answers</summary>

1. **24 bytes** — six 32-bit words: `msgh_bits`, `msgh_size`,
   `msgh_remote_port`, `msgh_local_port`, `msgh_voucher_port`, `msgh_id`. No
   LP64 growth because ports are 32-bit *names* — indices into the per-task
   port table, never pointers; an index, not an address, is why it cannot be
   forged. Removing `msgh_voucher_port` recovers the 20-byte pre-voucher
   layout, breaking voucher users and the header ABI.
2. The kernel. B's teardown destroys its port table; a destroyed receive
   right destroys its queued messages, and destroying a message destroys its
   descriptors — the `vm_map_copy` is released, pages freed, no user-space
   code involved: the kernel is the reference counter of trust.
3. The bug: authenticating from *message contents* — self-attested
   identity. Anyone who can look up the name gets a send right and can
   claim uid 0. Fix: read identity from the **audit token** the
   kernel stamped into the trailer (pid, pid-version, uids; macOS 12.3+ the
   code-signing word), and verify the calling *code* against a signing
   requirement (SecCode/SecTask) — inside the handler, since
   `xpc_connection_get_audit_token` reflects only the latest message.
4. What grows: kernel memory — each lookup creates a new send right (a
   reference-count bump plus a kalloc-backed table entry), leaked per
   request. What never fires: `MACH_NOTIFY_NO_SENDERS` — the leaked rights
   keep the count above zero, so the helper never learns "no clients left."
   Worse than a userspace leak: charged to kernel memory, and it pins
   someone else's lifecycle.
5. Security: a send-once right is usable exactly once, so a stolen or
   replayed reply port cannot be spammed or reused. Lifecycle: consumed by
   the reply, or turned into a dead name when the server dies — an automatic
   completion-or-death signal, which is how timeouts work with no polling.
6. Ambient authority keeps re-deriving permission from the caller's
   identity per operation, and the power stays addressable by anyone passing
   the identity check — a deceived privileged process (confused deputy) acts
   on the attacker's behalf, and an operation approved at check time can
   differ at use time. A capability holder cannot even *name* a resource it
   was never handed: no table entry, no check to fool, no window to race.
   Capabilities authenticate the channel, not intent — the audit-token
   policy is still yours.
7. MIG, the Mach Interface Generator, which compiled a `.defs` subsystem
   (here `host_info`) into client stubs and a server: `_X`-prefixed
   routines, a per-subsystem dispatch table indexed by `msgh_id`, reply IDs =
   request ID + 100 — the demultiplexer `mach_msg_server` feeds with dequeued
   requests.
8. Socket: two copies (into a kernel buffer, out to the receiver); the
   receiver frees its own buffer. Binder: one copy — the parcel lands in the
   receiver's pre-mapped ~1 MiB pool, so 4 MB *dies as
   `TransactionTooLargeException`* (module 05 ch03); the pool limit is the
   first wall. Mach OOL, page-aligned with `deallocate`: zero copies — the
   pages are *moved* into the receiver's map and must be `vm_deallocate`d
   (or leak until exit). The ~2 GiB inline ceiling is irrelevant: the
   payload never rides inline.
</details>

