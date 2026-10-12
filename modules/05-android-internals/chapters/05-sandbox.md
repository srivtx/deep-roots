# Chapter 05 — The sandbox

> **The 30-second version.** Android's sandbox is three old UNIX ideas
> stacked. Every installed app gets its own uid (first app uid: 10000), so
> the kernel's 1974 owner checks quarantine files, sockets, and processes
> per app — for free. SELinux adds a second kernel wall: every app process
> runs in the `untrusted_app` domain, and per-app MLS categories stop apps
> reading each other even when the file *type* is shared. seccomp then
> shrinks the syscall surface itself — a filter program, installed during
> zygote specialization and irremovable, rules on every syscall before it
> executes. Permissions, "shared" storage, and the Keystore are policy
> layers riding on those walls. The payoff is a boundary map: what a fully
> compromised app can actually reach. Reference platform: Android 14
> (AOSP 14), kernel 6.1, arm64.

## 1. One app, one uid: 1974's idea at phone scale

Android did not invent the app sandbox; it inherited the oldest one there
is. In a 1974 timesharing system every process carries a numeric identity,
and the kernel compares identities on every `open()`, `bind()`, and
`kill()`. The radical move was the *assignment policy*: one uid per human
became one uid per app. First app uid is 10000 (`AID_APP_START` in
`android_filesystem_config.h`), last is 19999 (`AID_APP_END`); everything
below 10000 is reserved for the platform (root 0, `system` 1000, `shell`
2000). Secondary users multiply out: uid = userId × 100000
(`AID_USER_OFFSET`) + appId, so the same app on user 10 is 1010155,
printed `u10_a155`.

Derive the consequences — none of them is new code, just the 1974 check
with a new assignment table:

- **Files.** `/data/user/0/com.example.one` is owned `10145:10145`, mode
  0771 (`drwxrwx--x`). Another app's `open()` dies with `EACCES` in the
  DAC check, before SELinux is even consulted.
- **Processes.** `kill()` and `ptrace()` on a foreign uid fail (`EPERM`,
  `ESRCH`); `/proc/<pid>` of another app hides its details — procfs gating
  is uid-aware.
- **Sockets.** Filesystem UNIX sockets carry file permissions, so a bound
  socket path belongs to one uid.

What pure-uid DAC cannot fence: anything deliberately world-accessible —
`/dev` nodes, `/proc`, and above all the *system daemons*, privileged
processes behind Binder. The interesting data moved off the filesystem;
the 1974 wall needs a second wall behind it.

## 2. SELinux: one domain, a thousand partitions

SELinux is a second kernel permission hook that consults *labels*, not
uids. Every process carries an scontext (`u:r:untrusted_app:s0:c23,c256`),
every file a tcontext; policy is a table of `allow source target:class
permissions` rules with default-deny — no matching allow, no access,
logged as an `avc: denied` line. Android 5.0 made this wall mandatory
for apps.

Policy is **compiled once, at boot**: init merges the platform and vendor
`.cil` files (Treble split) into one binary policy, loaded into
`/sys/fs/selinux`, never reloaded. Apps never modify policy — per-app
confinement is *labeling*, not policy change.

The labeling path: at install scan, PackageManager maps the APK's signing
cert through `mac_permissions.xml` (compiled from `keys.conf` at build) to
a short string called **seinfo** (`platform`, `media`, `default`, ...).
When the zygote forks an app (chapter 01's fork+specialize), libselinux
looks the child up in `seapp_contexts`
(`/system/etc/selinux/plat_seapp_contexts`, first match wins), keyed on
uid and seinfo:

```text
# plat_seapp_contexts (schematic, AOSP 14)
user=_app isPrivApp=true  domain=priv_app   type=privapp_data_file levelFrom=all
user=_app seinfo=platform domain=platform_app type=app_data_file levelFrom=all
user=_app                  domain=untrusted_app type=app_data_file levelFrom=all
```

Because an app process *never exec()s* (chapter 01), the zygote cannot use
the normal exec-time domain transition; it performs a **dynamic transition**
(`dyntransition`) on the child — a privilege policy grants to zygote alone
and `neverallow`s everywhere else.

Both apps in the last two lines run as `untrusted_app` and own files typed
`app_data_file` — and untrusted_app is allowed to read app_data_file. Type
equality would be a hole, so `levelFrom=all` stamps the process with an
**MLS category pair** derived from the appId, and installd stamps the data
dir with the same pair:

```text
process:  u:r:untrusted_app:s0:c23,c256     app A (appId 10145)
file B:   u:object_r:app_data_file:s0:c7,c257   app B (appId 10107)
```

A multi-category constraint requires the reader's category set to match
the file's, so A's `open()` is denied — at the same kernel hook, same
`avc: denied` log shape, one layer down. One domain plus categories
partitions the app population without a thousand types.

`neverallow` is the compile-time conscience of all this: assertions the
policy *build* must prove, e.g. (paraphrasing AOSP `app.te`):

```text
# if ANY allow rule anywhere ever grants this, the build dies:
neverallow { appdomain -platform_app } platform_app_data_file:file open;
```

Thousands of such lines rule out entire bug classes before a device
boots — enforcement at runtime, *proof* at compile time.

## 3. seccomp: a packet filter for syscalls

The third wall guards no resource — it shrinks the kernel API itself.
Since Android 9 (2018), every zygote-forked app runs under a seccomp-BPF
filter, installed in the child during specialization (after the uid drop,
before any app bytecode runs).

The mechanism is anachronistic on purpose: seccomp reuses the **1992 BSD
packet-filter VM** — a short, loop-free program of load/compare/jump
instructions — and just changes what the "packet" is: a 64-byte
`struct seccomp_data` snapshot of the syscall:

```c
struct seccomp_data {   // 64 bytes, built by the kernel at syscall entry
    int   nr;                    // syscall number
    __u32 arch;                  // AUDIT_ARCH_ARM64, so filters are per-ABI
    __u64 instruction_pointer;   // who called
    __u64 args[6];               // the six syscall arguments
};
```

The kernel runs the program (interpreted or JIT-compiled) on *every*
syscall; the verdict is ALLOW, ERRNO(n), TRAP (delivered as SIGSYS, signal
31), or KILL. Bounded, loop-free, reading only those 64 bytes, it is safe
per-syscall — the same BPF VM idea eBPF later generalized to 64-bit
registers, maps, and a verifier.

The app deny list (AOSP 14, `system/seccomp/policy-*.stpl`) is the
insider's list: `bpf`, `userfaultfd`, `perf_event_open`, `swapon`/
`swapoff`, and (jointly with SELinux) the kernel-keyring trio `add_key` /
`request_key` / `keyctl`. The arm64 table has roughly 300 entries
(Linux 6.1); the filter removes the families behind kernel subsystems apps
must never touch. `ptrace` and raw/most-netlink socket families are
blocked by the *SELinux* layer instead — each wall blocks what it can
express cheaply: seccomp speaks syscall numbers, SELinux speaks labels.

Filters are a **one-way ratchet**: inherited across `fork()`, they can only
be intersected with more filter, never removed. An app may install
*stricter* filters — Chromium stacks a second on its renderer inside
Android's — but nothing relaxes the zygote's. A heap-grooming exploit that
wants `userfaultfd` gets `EPERM`, and no child it spawns will fare better.

## 4. Permissions: one prompt, three enforcement points

A manifest `uses-permission` is a request; *enforcement* is spread across
three points, and knowing which point answers which permission is the
whole game.

**Install-time (normal) permissions** mostly never touch the kernel — with
one exception: a legacy set maps to UNIX supplementary groups. `INTERNET`
puts the process in group 3003 (`inet`) at fork — visible in `run-as ...
id`. An app without it fails socket creation with `EPERM`
(`SocketException: socket failed: EPERM (1)`) — the 1974 group mechanism
as a permission bit. External-storage groups live in the shared block at
50000 (`AID_SHARED_GID_START`).

**Runtime (dangerous) permissions** — camera, location, since Android 6.0
— are enforced *at the far end of Binder*: the service in
system_server/mediaprovider calls `checkCallingPermission` per request.
The kernel never sees the permission; the dialog just mutates a grant
record in `/data/system`.

**Signature permissions** are gated by identity: granted only if the
requester is signed with the same key as the definer. Platform-signed apps
get `seinfo=platform`, land in `platform_app` via seapp_contexts — a
permission implemented as an SELinux *domain*. Privileged permissions
ride `/system/etc/permissions/privapp-permissions-*.xml` allowlists —
a build-time `neverallow` in the Java world.

## 5. Where the bytes live: installd, FUSE, and the secure world

App-private storage is a layout mangling performed by **installd** (root,
SELinux domain `installd`) at install and first run: what the app sees as
`/data/data/com.example.one` is really `/data/user/0/...` (`/data/data`
is a symlink to `/data/user/0`), plus a device-encrypted twin at
`/data/user_de/0/...` readable *before* first unlock (FBE "de" storage —
why your alarm rings pre-unlock). installd does the mkdir, chowns to
`10xxx:10xxx`, mode 0771, and labels the dir `app_data_file` with the
app's category pair. No path into another app's dir survives DAC.

External storage is the illusion in the stack. `/storage/emulated/0` is
not a partition: it is `/data/media/0` (owned `media_rw`) viewed through a
translation layer — historically the sdcardfs kernel filesystem, retired
across Android 11–13 in favor of a userspace **FUSE daemon** fronted by
MediaProvider. The layer re-owns every file *per viewer*: the same photo
`stat()`s as owned by whichever app is looking. Modes are synthesized, so
`chmod 0600` there never reaches a real inode. Since scoped storage
(Android 11), the access decision for "shared" files is a row in
MediaProvider's database: file permissions as SQL. It *looks* like a FAT
filesystem from 1990; it is a policy engine wearing one.

The Keystore is where the sandbox stops and silicon begins. An app's
`generateKey` call goes to keystore2 (Android 12+), which forwards to the
KeyMint HAL running in the TEE — or on **StrongBox**, a separate
secure-element CPU (first shipped as the Titan M in Pixel 3, 2018) with
its own core, storage, and rate limiting. Key material never enters app
RAM; the app holds a reference, and keystore2 checks the caller's uid on
every operation: data-dir privacy extended into hardware.

## 6. The boundary map

Assemble the walls and answer the payoff question: an attacker has full
RCE in app A (appId 10145, `untrusted_app`, all its permissions). What can
the code actually touch?

```text
              what a fully compromised app can reach
+---------------------------------------------------------------------+
| seccomp-BPF filter (installed at zygote specialization, inherited)  |
|   blocks: bpf, userfaultfd, perf_event_open, swapon, keyring trio   |
|   -> kernel subsystems behind banned syscalls are unreachable       |
|  +---------------------------------------------------------------+  |
|  | UNIX uid 10145 (AID_APP_START 10000 <= uid < 20000)           |  |
|  |   blocks: other apps' files/sockets/processes (DAC EACCES),   |  |
|  |   kill/ptrace of foreign uids, ~all of /system as a writer    |  |
|  |  +----------------------------------------------------------+ |  |
|  |  | SELinux domain + MLS categories                           | |  |
|  |  |   u:r:untrusted_app:s0:c23,c256                           | |  |
|  |  |   blocks: /dev nodes, procfs, exec of system binaries,    | |  |
|  |  |   ptrace, raw sockets; category mismatch blocks app B's  | |  |
|  |  |   app_data_file even if B chmods it 0644                 | |  |
|  |  |  +----------------------------------------------------+  | |  |
|  |  |  | what SURVIVES (reachable through the walls)         |  | |  |
|  |  |  |   own app_data_file dirs (CE + DE twins)            |  | |  |
|  |  |  |   Binder calls to any service that will answer      |  | |  |
|  |  |  |   network (if INTERNET -> gid 3003)                 |  | |  |
|  |  |  |   scoped external storage via FUSE/MediaProvider    |  | |  |
|  |  |  |   Keystore/KeyMint operations by reference          |  | |  |
|  |  |  |   shared zygote COW pages (read-only, no secrets)   |  | |  |
|  |  |  +----------------------------------------------------+  | |  |
|  |  +----------------------------------------------------------+ |  |
|  +---------------------------------------------------------------+  |
+---------------------------------------------------------------------+
```

Read the map as an attack-surface checklist: the kernel walls are the
strong part; the soft part is the *binder surface* — every system service
that takes app calls is code with far more privilege, gated by per-call
`checkCallingPermission`. Real-world Android privescs are overwhelmingly
"service trusted the caller's parcel" bugs, not sandbox escapes. The
sandbox makes the *file system* tiny and forces the interesting traffic
through auditable chokepoints.

## What insiders know

- **Uid arithmetic is a table you keep in your head**: apps live in
  10000–19999 (`AID_APP_START`/`AID_APP_END`); multi-user is
  `uid = userId * 100000 + appId` (user 10's app 10155 = 1010155);
  external-storage groups sit in the shared block from 50000. The
  framework package `android` occupies 10000 (`u0_a0`), so real apps start
  at `u0_a1`. AppIds are recycled after uninstall — correlating logs
  across reinstall is a known trap.
- **The app seccomp deny list** (AOSP 14): `bpf`, `userfaultfd`,
  `perf_event_open`, `swapon`/`swapoff`, keyring trio. Note *what is not
  there*: `ptrace` and raw/most-netlink sockets are denied by SELinux
  for `untrusted_app`, not by the filter — the lists split by what each
  layer expresses cheaply.
- **seapp_contexts is consulted twice**: by the zygote (process label, via
  the `dyntransition` policy reserves for zygote) and by installd (file
  label on the data dir). First match wins, and the vendor file can
  override the platform one — a Treble-era surprise when debugging a
  wrong domain.
- **neverallow is the quiet superpower**: entire permission-grant classes
  are impossible because the policy build fails if any allow rule
  contradicts an assertion — security review happens at compile time,
  before any device sees the change.
- **FUSE storage semantics**: ownership on `/storage/emulated` is
  synthesized per-calling-app — the same file `stat()`s differently
  depending on who asks; chmod/chown never reach the underlying
  media_rw inode; the true ACL is MediaProvider's database row.
- **run-as is the honest window**: requires a *debuggable* app, drops you
  into the real uid and groups (3003/9997/50xxx reveal the app's
  permission-gid state instantly).

## See it yourself

Any device or emulator with a debuggable app installed (an AVD with the
Google APIs image accepts `adb root`; outputs below are from a stock
userdebug image, Android 14 / kernel 6.1 / arm64).

```text
$ adb shell run-as com.example.debug id
uid=10147(u0_a147) gid=10147(u0_a147)
  groups=10147(u0_a147),3003(inet),9997(everybody),50147(u0_a147_cache)
```

Read it: uid 10147 is in the 10000 block; 3003 = INTERNET granted; 9997 =
`everybody` (shared storage); 50147 = the app's external-storage cache
group (shared block, 50000+).

```text
$ adb root && adb shell ls -Z /data/user/0 | head -3
u:object_r:app_data_file:s0:c23,c256  com.example.one
u:object_r:app_data_file:s0:c7,c257   com.example.two
u:object_r:app_data_file:s0:c512,c768 com.example.three
```

Same *type* (`app_data_file`) everywhere; the category pairs differ per
app — section 2's partition, visible as data. Pair with `ls -l
/data/user/0`, which shows the `u0_a45`-style owner for each.

```text
$ adb shell run-as com.example.debug cat /proc/self/attr/current
u:r:untrusted_app:s0:c23,c256
$ adb shell run-as com.example.debug sh -c 'grep -E "Uid|Seccomp" /proc/self/status'
Uid:    10147   10147   10147   10147
Seccomp:    2
Seccomp_filters:    1
```

`Seccomp: 2` is `SECCOMP_MODE_FILTER` — the zygote's program is active;
`Seccomp_filters:` (kernel 5.13+) counts installed filters.

```text
$ adb shell dmesg | grep avc | tail -1
type=1400 audit(1830572.439:527): avc:  denied { open } for
  comm="CameraThrea" path="/data/misc/cameraserver" dev="dm-11" ino=21
  scontext=u:r:untrusted_app:s0:c23,c256
  tcontext=u:object_r:cameraserver_data_file:s0 tclass=file permissive=0
```

Read any denial the same way: `scontext` = who (domain + categories),
`tcontext` = the label touched, `tclass` = class, `permissive=0` =
*enforced* (`1` = logged-but-allowed). `dmesg` needs a userdebug build —
on user builds denials surface only in logcat's events buffer. No device?
Treat this section as a documented walkthrough: every command is
replayable when you get one.

## How to use this knowledge

- **Attack-surface review.** For any app, draw the boundary map with its
  actual uid, domain, groups, and permission set — then enumerate the
  "reachable" set: exposed binder surfaces, granted permissions, native
  code's syscall habits. That set, not the manifest, is the review output.
- **Permission debugging by layer.** `EACCES` says DAC (uid/ownership);
  `EPERM` on a syscall says seccomp or a capability neverallow; an
  `avc: denied` line says SELinux — three failure modes, three fixes,
  told apart from one log line.
- **Storage bug triage.** "chmod didn't work", "wrong owner",
  "MediaStore still lists it" — one cause: FUSE translation over
  `/data/media` with MediaProvider as the real ACL. Debug the provider's
  grants, not file modes on `/storage/emulated`.
- **Incident explanation.** "Why can't a compromised app read my photos"
  now has a full answer — uid ownership, MLS category mismatch,
  seapp_contexts labeling, MediaProvider rows — four sentences, each
  naming a real layer.

## Connections

- **Module 01 ch03 (virtual memory as isolation)** — the address-space
  wall is the layer *under* these walls: zygote COW pages are shared
  across mutually hostile uids — memory sharing without data sharing.
- **Module 02 ch01 (trust boundaries)** — the runtime-permission dialog
  is a *declared* consent boundary; this chapter's kernel walls are
  *enforced* ones; the two meet at `checkCallingPermission`.
- **Module 05 ch01 (boot & the Zygote)** — UID assignment happens in the
  specialize step of fork: `setresuid`, the `dyntransition`, and the
  seccomp prctl are why a fork of a root-warm process lands in a
  3-walled sandbox before its first bytecode.
- **Module 05 ch03 (Binder)** — the one door deliberately left open
  through section 6's map; the post-sandbox attack surface.
- **Module 07 ch05 (desktop kernels)** — same Linux kernel, opposite
  defaults: one uid per human, SELinux permissive or off, no mandatory
  seccomp — Chrome ships its own sandbox to compensate.

## Quiz

1. App B's private file is accidentally `chmod 0644` — the DAC layer
   fails open. App A (both run as `untrusted_app`, files typed
   `app_data_file`) still cannot read it. Which mechanism stops A, and
   where is it checked?
2. An app with appId 10155 is installed for secondary user 10. Compute
   the process uid and the owner string `ls -l` will print for its data
   dir.
3. Two builds of one app differ only in manifest. `run-as <pkg> id` on
   build 1 shows `3003(inet)` in groups; build 2 does not. Which manifest
   line differs, and what does build 2's first `new Socket()` throw?
4. A file-manager app (granted `MANAGE_EXTERNAL_STORAGE`) runs
   `chmod 0600` on `/storage/emulated/0/secret.txt`, yet a media app can
   still read the file through MediaStore. Diagnose.
5. Logcat shows `avc: denied { open } ... scontext=u:r:untrusted_app
   tcontext=u:object_r:net_data_file:s0 tclass=file permissive=0`. The
   developer adds `ACCESS_NETWORK_STATE` and retests. Diagnose both the
   original attempt and why the fix fails.
6. A kernel 0-day lands in the `userfaultfd` subsystem, and an exploit
   needs that syscall for heap grooming. Which wall answers, with what
   verdict, and why does forking a child not help the attacker?
7. An app process never `exec()`s (module 05 ch01). Trace where and how
   its uid, SELinux label, and seccomp filter get applied, and name the
   transition type SELinux uses *because* there is no exec.
8. On desktop Linux (module 07 ch05) browser tabs all run as the human's
   uid. Name which of this chapter's walls are missing by default on
   that desktop, and what the browser itself must therefore ship.

<details>
<summary>Answers</summary>

1. **The MLS/MCS category constraint.** B's file label carries B's
   category pair (`s0:c7,c257`), A's process carries A's (`s0:c23,c256`);
   the policy requires the reader's categories to match the file's,
   checked in the kernel's SELinux permission hook — the same hook that
   enforces type rules, logging `avc: denied { read } ... tcontext=
   u:object_r:app_data_file:s0:c7,c257 ... permissive=0`. The giveaway is
   the category mismatch on an otherwise-allowed type. `chmod` was never
   the last word.

2. **uid 1010155, printed `u10_a155`.** uid = userId × 100000
   (`AID_USER_OFFSET`) + appId = 10 × 100000 + 10155. bionic renders it
   `u10_a155`; the dir is `/data/user/10/<pkg>`, with the same CE/DE
   twins as user 0.

3. **The `<uses-permission android:name="android.permission.INTERNET"/>`
   line.** INTERNET is a normal, install-time permission mapping to
   supplementary gid 3003 at fork; build 2 lacks it, so its first socket
   creation throws `SocketException: socket failed: EPERM (1)`.

4. **The chmod was theater.** `/storage/emulated/0` is a FUSE view over
   `/data/media/0` owned by `media_rw`; ownership and mode are synthesized
   per-viewing app and never reach a real inode — access is decided by
   MediaProvider's database rows. The fix is per-row/media grants, or
   moving secrets into the app's own `app_data_file` dir.

5. **A type-enforcement denial, not a permission gap.** The app tried to
   `open()` a path under `/data/misc/net` (`net_data_file`);
   `untrusted_app` has no allow for that type — by design, guarded by
   `neverallow`. No Java permission maps to that label, so
   `ACCESS_NETWORK_STATE` (binder-checked, service-side) cannot change
   the verdict. The fix is the API path — ConnectivityManager over
   Binder — not the filesystem.

6. **seccomp answers with ERRNO — `userfaultfd` returns `EPERM`.** It is
   on the app deny list, so the filter's verdict fires at syscall entry,
   before the kernel touches the subsystem. Forking is useless: filters
   are a one-way ratchet — children inherit and can only intersect with
   stricter ones.

7. **All three are applied in the forked child during zygote
   specialization, before any app code runs**: drop to the assigned uid
   (`setresuid` plus the permission groups), apply the SELinux label via
   a **dynamic transition** (`dyntransition`) — used precisely because
   there is no exec — then install the seccomp filter via
   `prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER)`.

8. **Missing: per-principal uid, enforcing SELinux, mandatory seccomp.**
   Desktop Linux defaults to one uid per human, SELinux permissive or
   absent, no syscall filter — three of the four walls are absent, so
   Chrome ships its own sandbox (seccomp, setuid helpers) to approximate
   what Android grants every app for free.

</details>
