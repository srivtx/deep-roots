# Chapter 05 — Code signing & PAC

> **The 30-second version.** iOS trusts nothing it did not verify from silicon
> up: a boot ROM, burned into the chip, checks iBoot; iBoot checks the
> kernel; the kernel — via AMFI — checks every executable it loads. The
> signature is not one seal: every 16 KB code page has its SHA-256 hash in a
> per-binary table, signed by a certificate chaining to the root key in the
> ROM, and the kernel checks each page's hash *at page-in* — altered code
> never materializes; module 01 ch03's demand paging fused with an integrity
> check. Entitlements are the signed permission slips; PAC (arm64e) signs
> pointers so `ret` verifies return addresses. Injection dead, ROP dead:
> chains start with a logic bug.

## The root that cannot be patched

Any chain of verification needs a *first* verifier you accept without proof.
Software cannot verify itself at rest: whatever checked the bytes could be
rewritten, and the rewritten checker would approve rewritten code. The regress
stops only at hardware that cannot change after fabrication. Apple burns its
Root CA public key into the **boot ROM** — mask ROM: read-only, burned in, the
immutable root — and each stage verifies the next before handing over the CPU:

```text
 boot ROM (mask ROM, burned at fabrication) -- the immutable root
 |  holds: hash of Apple Root CA public key -- unpatchable by construction
 |  checks: signature chain of next stage -> Root CA; rollback counters
 v
 LLB / iBoot          (A9-era: ROM -> LLB -> iBoot; A10+: ROM -> iBoot)
 |  checks: kernelcache signature, device tree; refuses downgrades
 |  hands kernel a STATIC TRUST CACHE: CDHashes of known platform binaries
 v
 XNU kernel (arm64e)  +  AMFI (AppleMobileFileIntegrity)
 |  AMFI: MACF policy module -- hooks exec, page-in, task ports
 v
 every exec, every fault on a code page:
      SHA-256(page) == code-directory slot ?  -> page materializes
      else                                    -> process dies (SIGKILL)
```

Nothing verifies the ROM — trusted by construction, which is why the chain
starts at silicon. Two details make it more than ceremony: each stage enforces
**rollback protection** (anti-downgrade counters), so an attacker cannot boot a
signed but years-old, known-vulnerable firmware; and the kernel does not
re-run the certificate chain per binary — iBoot hands it a **static trust
cache** of CDHashes AMFI consults in O(1), with loadable trust caches covering
OS content added later (cryptexes — chapter 04).

## The signature is a hash table of pages

A Mach-O carries `LC_CODE_SIGNATURE` (load command 0x1d) pointing into
`__LINKEDIT` at a **superblob** — magic `0xfade0cc0` — holding typed blobs:

- the **CodeDirectory** (magic `0xfade0c02`): one **SHA-256 (32-byte) hash slot
  per page** of code and read-only data — the directory records its page size
  (4 KB or 16 KB); Apple Silicon platform binaries use 16 KB (chapter 01);
- the **entitlements** blob, DER-encoded (magic `0xfade7c51`) — one canonical
  encoding, no parser ambiguity;
- the **CMS blob**: a PKCS#7/CMS detached signature over the CodeDirectory's
  hash, made with a certificate chaining up to Apple Root CA;
- for bundles, `_CodeSignature/CodeResources`: hashes of every resource file
  and embedded framework — the "Sealed Resources" crash reports mention.

Derive the layering: the CMS signature proves *who vouched* — one RSA
verification; the CodeDirectory proves *the pages*, one slot each. A
whole-file digest would force verifying the entire binary before running any
of it; per-page slots let the kernel check lazily — which is where virtual
memory walks in:

```text
 __LINKEDIT superblob:
 +---------------------------------------------------------------+
 | CodeDirectory: | h[0] | h[1] | h[2] | ... | h[N-1] |  32 B each|
 |  1 SHA-256 slot per 16 KB page of __TEXT                     |
 +---------------+------+------+------+------+--------+---------+
 | CMS (PKCS#7): RSA signature over SHA-256(CodeDirectory)      |
 |   cert: "Software Signing" <- Apple Code Signing CA <- Root   |
 | Entitlements (DER): hashed into the CD's special slots       |
 +---------------------------------------------------------------+
             |  CDHash = SHA-256(CodeDirectory): THE identity
             |  of this exact binary; trust caches key on it
             v
   text page k faults in:  SHA-256(bytes) == h[k] ?  allow : kill
```

The binary's identity is the **CDHash**. One changed byte of `__TEXT` makes
h[k] wrong, which changes the CodeDirectory, which changes the CDHash: there
is no "similar binary" — only this exact one, or not.

## The fault is the checkpoint: page-in validation

Module 01 ch03 built demand paging — pages materialize only when first
touched, because the fault handler is where policy lives. Chapter 04 used that
hook for fixup chains; code signing uses it for integrity: XNU's
`cs_validate_range` (bsd/kern/kern_cs.c) runs in the fault path for pages
backed by code-signed VM objects — hash the incoming page, compare against
the slot. Consequences, each derivable:

1. **An altered page never exists.** Not "detect tampering after the fact" —
   unsigned or altered pages simply never materialize; there is no modified
   code to run.
2. **The cost is paid once per page, on first use.** A validated page is
   marked validated in the VM object; only eviction re-checks. Pages never
   executed are never hashed — a 1-3 GB shared cache validates one faulted
   page at a time.
3. **Unsigned memory cannot become executable at all.** Write+execute
   mappings are refused outright (W^X, below), and dylibs must be signed and
   pass AMFI's **library validation** — every executable page traces to a
   signature chain or a trust cache entry.
4. **Death is loud.** A failing page marks the process CS_INVALID and the
   kernel delivers SIGKILL — the "killed: 9" of a tampered app, before any
   tampered byte executes.

The insider's compression, and this chapter's thesis: **virtual memory is the
enforcement mechanism.** Code signing on iOS is not a gate the app passes at
launch and forgets — it is a property of the memory system itself: demand
paging fused with a MAC check, so "executable" and "verified" are the same
condition.

## Entitlements: the signed permission slip

A signature answers *who wrote these bytes*; it cannot answer *what may these
bytes do*. iOS splits the question: **entitlements** are a key-value list
stored in the superblob and hashed into the CodeDirectory's special slots —
covered by the same signature as the code, inseparable from it. AMFI consults
them wherever a privilege is granted. Keys you will meet:

- `com.apple.security.get-task-allow` — permits `task_for_pid`: the
  debugger-attach right. Xcode sets it on every development build; Apple's
  distribution re-signing strips it. This key is most of "works in Debug,
  dies in Release."
- `com.apple.security.cs.allow-jit` — the macOS JIT exemption (below).
- `com.apple.security.cs.disable-library-validation` — load dylibs signed by
  *other* teams (a real security downgrade).
- `com.apple.private.*` — Apple-only; a third-party app requesting one is
  rejected at validation.

Entitlements are policy, not proof: each service granting a capability decides
which key it requires. Chapter 03 returns — a Mach port right is unforgeable,
but *who may obtain the send right* to a privileged XPC service is an
entitlement check; the audit token carries kernel-attested identity.

Third parties reach entitlements through the **provisioning profile**:
`embedded.mobileprovision`, itself a CMS blob signed by Apple, wrapping the
allowed signing certificates, the device list (development), an expiration
date, and the entitlement set the app may carry; at install, iOS checks your
certificate, entitlement subset, device, and date against it. Dev profiles
expire in days to a year by account class — an expired profile stops installed
dev builds from launching. Enterprise distribution is the same wrapper with
`ProvisionsAllDevices` and a multi-year horizon. App Store distribution
removes the profile entirely: Apple re-signs the binary, strips
`get-task-allow`, and wraps the payload in FairPlay, so App Store binaries
verify against Apple's chain, not your team's.

## PAC: pointers that prove themselves

Page-in validation guards code *at rest*; it does nothing about an attacker
who corrupts *data* and rides existing, perfectly signed code — module 02's
code-reuse attacks. Armv8.3 (2018; first consumer silicon: Apple A12, iPhone
XS) adds pointer authentication; Apple's ABI for it is **arm64e**.

Derive the goal: you cannot cheaply stop memory corruption, but you can make
*forged pointers fail at use*. The CPU holds secret keys per process (rolled
fresh at exec). Signing computes a keyed MAC over (pointer value + context)
and stores the truncated tag in the pointer's spare high bits — on A12-class
iPhones a 39-bit virtual address leaves a 24-bit field (Azad, 2019; width is
chip-dependent: a wider VA, a shorter tag). Mismatch traps immediately.

The canonical use is the return address: an arm64e function begins `PACIBSP` —
sign LR with the B key, SP as context — and ends `AUTIBSP; RET` (module 02
ch06's rung 8). Audit module 02's gadget economy against it:

- **Dead: ret-to-anything.** Returning through a corrupted frame authenticates
  a value the attacker never had signed: ROP chains, `pop x30; ret`, ret2libc
  — the return-hijack family. A chosen LR is wrong with probability
  1 - 1/2^24.
- **Alive: signing gadgets** — legitimate functions that sign values derived
  from attacker-influenced memory (Liljestrand et al., 2019): borrow the CPU's
  own key. And **replay**: a *leaked* signed return address is pre-forged —
  copy it verbatim and `AUTIBSP` passes; information leaks still matter.
- **Alive: the forward edge.** `ret` checks backward edges only; steering to
  legitimate function entries with controlled arguments (JOP on valid call
  targets) is CFI/BTI's rung, not PAC's.
- **Alive, in principle: forgery.** Blind guessing succeeds 1/16,777,216 per
  attempt — but each miss is a loud kill, and keys re-roll at every exec, so
  attempts do not accumulate. Mitigation, not guarantee: PACMAN (Apple M1,
  2022) later showed a speculative-execution oracle recovering tags
  bit-by-bit. PAC prices the forgery; it does not make it impossible.

Apple applies signing beyond LR. In libobjc on arm64e, the **method cache** —
the per-class table of hot (selector, IMP) pairs, a favorite heap-overflow
target — stores IMPs signed, authenticated at dispatch: a rewritten bucket
traps instead of jumping. C++ **vtable pointers** in objects are signed; and
chapter 04's chained fixups carry arm64e auth flags (key + diversity), so the
kernel produces the *signed* function pointer as it fixes a data page —
page-in linking and pointer signing, same fault path.

## The composite: why iOS chains start with a logic bug

Assemble the module 02 ch06 ladder as iOS stacks it. **Page-in validation**:
no injected or altered code, ever. **W^X**: third-party iOS code cannot
create executable memory at all — JIT lives only in Apple's WebContent
process under private entitlements; on macOS with Hardened Runtime,
`allow-jit` plus `MAP_JIT` gives a region toggled RX/RW per thread via
`pthread_jit_write_protect_np`, so even entitled JITs are never RWX, and the
sanctioned way to borrow JIT is a `WKWebView`. **PAC**: forged control
pointers in system code fail at use. **Entitlements, the sandbox, and the
per-boot cache slide** (chapter 04): capability minimums and address-
blindness.

A full chain's receipt: a memory bug whose *use* survives PAC, a leak of
signed pointers, a sandbox escape, a kernel escalation — distinct bugs, each
priced (module 02's five-bug ledger). The cheapest opening left is a logic bug
that hands you a capability legitimately — why iOS exploit write-ups start in
message parsers, not buffer overflows.

## What insiders know

- **`get-task-allow` is a task-port gate, not "debug mode."** It permits
  `task_for_pid` — how debugserver attaches. Dev builds carry it;
  distribution re-signing strips it. First diagnostic for "different behavior
  in Release": diff `codesign -d --entitlements :-` on both builds.
- **The MAP_JIT story:** `mmap` with PROT_WRITE|PROT_EXEC fails; the only
  writable-then-executable path on macOS is a `MAP_JIT` region plus the
  per-thread `pthread_jit_write_protect_np(0/1)` toggle — writable for the
  JITting thread, RX for everyone else. On iOS, third parties get none of
  it; WKWebView's WebContent process holds the private entitlements. If your
  engine "needs JIT," you ship an interpreter on iOS.
- **Which PAC gadgets die:** everything that *returns through corrupted
  memory*; what survives: signing gadgets, replayed signed pointers
  (pre-forged), JOP to legitimate entries, PACMAN-style tag recovery. The
  trap: as of iOS 17/18, third-party app code is still plain arm64 — PAC
  hardens the system code around you, not your pages.
- **codesign flags insiders run:** `codesign -dv --verbose=4` (authority
  chain + CDHash; `hashes=2279+13` = 2,279 page slots + 13 special slots);
  `codesign -d --entitlements :-` (the colon decodes DER to XML);
  `--verify --deep --strict --verbose=2`. For research: `csrutil status`,
  then `nvram boot-args="amfi_get_out_of_my_way=0x1"` (SIP off) — the blunt
  instrument whose name tells you what blocked you.
- **CDHash is the identity.** Trust caches key on it; the kernel compares 32
  bytes, never your Team ID. Same source, two builds: two identities —
  sign-after-copy bugs exist because identity is total.
- **Sign last, or don't ship.** Anything copied into the bundle after
  `codesign` runs — a build script dropping a codec dylib into
  `Contents/Frameworks/` — is missing from CodeResources, fails library
  validation or the page-in check, and dies at first load. Fix the phase
  order; verify with `codesign --verify --deep --strict` before archiving.

## See it yourself

On a Mac (macOS 13+; fragments abridged — shapes, not bytes, from a macOS 14
arm64e machine). **No Mac? Treat this as a documented walkthrough**: commands
and output are as you would see them.

```bash
# 1. Dissect a platform binary's signature: the chain, and the identity.
codesign -dv --verbose=4 /bin/ls
#   Identifier=ls
#   Format=Mach-O universal (x86_64) [arm64e]
#   CodeDirectory v=20400 ... hashes=NNNN+13 location=embedded
#                     ^ page slots + 13 special slots (entitlements etc.)
#   CDHash=<64 hex chars>                <- THE identity
#   Authority=Software Signing           <- platform binaries: no Team ID
#   Authority=Apple Code Signing Certification Authority
#   Authority=Apple Root CA              <- the key whose hash is in boot ROM
#   TeamIdentifier=not set

# 2. Entitlements, decoded: DER -> XML, the signed permission slip.
codesign -d --entitlements :- /System/Applications/Safari.app
#   <key>com.apple.security.app-sandbox</key> <true/>
#   (abridged; keys vary by release -- a system app's permission list)

# 3. The biggest signed artifact on your machine (ch04): one signature,
#    validated one page-in at a time.
codesign -dv --verbose=4 \
  /System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/dyld_shared_cache_arm64e
#   Identifier=com.apple.dyld.caches...   CDHash=...
```

What to notice: `/bin/ls` has no Team ID — platform binaries authenticate via
Apple's chain and the trust cache; `hashes=NNNN+13` lets you *count the page
slots* with no extra tooling; the entitlements are DER decoded to XML — one
canonical encoding, exactly what a signature wants to hash.

## How to use this knowledge

- **Kill the "works in Debug, dies in TestFlight" class.** Diff entitlements
  between builds (`codesign -d --entitlements :-`), then check the
  provisioning profile's expiry and device list — most such bugs are
  `get-task-allow` or an expired dev profile, a signing artifact, not your
  code.
- **Build-pipeline hygiene.** Sign after every bundle mutation, then run
  `codesign --verify --deep --strict` in CI. A red build beats a tester's
  phone killing your app at first launch with an unexplained SIGKILL.
- **Architecture decisions under W^X.** No downloaded code, no app-side JIT
  on iOS: design plugins as data + interpreters, route JS through WKWebView.
  On macOS, weigh `allow-jit` + MAP_JIT against shipping an interpreter.
- **The interview answer.** "Why do iOS exploit chains start with logic
  bugs?" Walk the composite: page-in validation removes injection; W^X removes
  generated code; PAC removes forged control pointers in system code;
  entitlements and the sandbox cap what any single bug buys. Capabilities are
  priced in bugs (module 02 ch06); the cheapest opening is persuasion, not
  corruption.

## Connections

- **Module 02 ch06 (mitigation ladder)** — this chapter is iOS's stack on
  that ladder: page-in validation kills injection outright (below the ladder
  — it removes the *class*), PAC is rung 8; the five-bug ledger is the
  composite's price tag.
- **Module 06 ch04 (dyld & shared cache)** — "pre-signed" was load-bearing:
  one code directory covers the whole cache, so per-fault validation scales
  to gigabytes; arm64e chained fixups mean the kernel *signs* pointers
  during page-in linking.
- **Module 05 ch06 (APK & OTA)** — the comparison frame: Android v2/v3 signs
  the whole APK at install time (dm-verity is the system-partition analog);
  iOS verifies per page, forever, at fault time.
- **Module 01 ch03 (virtual memory)** — the payoff of "the fault handler is
  where policy lives": demand paging is the enforcement mechanism, and
  enforcement costs nothing for pages never touched.
- **Module 06 ch03 (Mach ports & XPC)** — entitlements gate who may
  *obtain* port rights; the audit token carries kernel-attested identity
  plus entitlements to every service door.

## Quiz

1. A reviewer proposes moving the boot ROM's verification logic to flash so
   boot bugs can be patched. Derive what security property dies, and why
   signing the flash image cannot repair the chain.
2. An app's arm64 `__TEXT` is 4 MB, code-directory page size 16 KB, SHA-256
   slots. How many hash slots does the code directory carry, and how many
   bytes do they occupy?
3. On a lab Mac you hex-edit one byte of a signed app's `__TEXT` (leaving
   `__LINKEDIT` untouched) and launch it. The app dies — but not at launch.
   What passed, what failed, and why does the death land where it does?
4. An app `dlopen`s a licensing module the server downloaded into its
   Documents directory. It works on macOS (with `disable-library-validation`);
   on iOS the process is killed the moment the library is first called. Why,
   and what is the design fix?
5. An attacker reads a live frame's saved return address (correctly signed)
   and later replays it verbatim; it returns cleanly. The same address
   *guessed* kills the process. Explain both outcomes from PAC's mechanism.
6. With a 24-bit PAC field, a blind forgery succeeds with probability 2^-24.
   A crash-looping victim restarts thousands of times overnight. Why doesn't
   the attacker accumulate progress toward those 16,777,216 possibilities?
7. A heap overflow overwrites an Objective-C method-cache bucket's IMP on an
   arm64e process. What happens on the next message dispatch, compared to
   the same overwrite on plain arm64, and which mechanism is responsible?
8. Chapter 04 applies fixup chains at page-in; this chapter validates page
   hashes at page-in; module 01 ch03 called the fault handler "where policy
   lives." Name the common principle, and what it predicts about the cost of
   a policy that only touches used pages.

<details>
<summary>Answers</summary>

1. **Immutability of the root verifier.** Mask ROM is trusted *by construction* —
   it cannot change after fabrication. Flash can be rewritten, so the "ROM"
   becomes replaceable bytes, and an attacker's image is as self-consistent as
   Apple's. Signing the flash image doesn't help: *someone* must verify that
   signature, with a key stored where? A writable key store re-opens the
   regress; only an unwritable key closes it.
2. **256 slots** (4 MB / 16 KB); 256 x 32 bytes = **8,192 bytes** of hash
   table, plus 13 special slots (entitlements, requirements, resources).
3. The launch-time checks — code directory, CMS over its hash, entitlements,
   trust cache — all pass: `__LINKEDIT` was untouched, so the *signature of
   the table* is intact. What broke is page k vs slot h[k], compared in
   `cs_validate_range` at first fault. Death lands at first *use* of the
   edited page — minutes in, or never if cold — a SIGKILL for CS_INVALID.
   Validation lives in the fault handler, not exec.
4. The downloaded dylib is covered by no signature: not your team's, not in
   the trust cache, absent from CodeResources. On iOS, library validation
   and the page-in check have no path that admits it — every executable page
   must trace to a signature or trust cache, so the first fault kills; on
   macOS, `disable-library-validation` relaxes exactly that check. Fix:
   ship logic as *data* the app interprets, or compile the feature in and
   toggle it with server flags — on iOS, downloaded code is not a
   distribution channel.
5. The tag is a keyed MAC of (address + context). The leaked value is a
   complete (pointer, tag) pair — pre-forged; authentication recomputes and
   matches, so `AUTIBSP` passes. The guessed address carries a wrong tag and
   matches with probability 2^-24. That asymmetry is PAC's ledger: leaks
   (replay) still convert to control flow; blind corruption does not.
6. Keys re-roll at every `exec`: each attempt is a *new* process with *new*
   keys, so guesses are independent — no partial-credit state. The attacker
   needs ~2^24 kills on average, each a loud, logged SIGKILL. A
   *same-process* oracle — a signing gadget, a PACMAN-style leak — is what
   accumulates; restart loops do not.
7. On plain arm64, the corrupted bucket dispatches straight to the
   attacker's address — the classic method-cache hijack. On arm64e, libobjc
   stores method-cache IMPs *signed*; the lookup authenticates before
   jumping, so the overwritten value fails and traps. Mechanism: pointer
   authentication — return-address signing's keyed MAC applied by
   the runtime to a structure ROP cannot reach but heap overflows can.
8. **The fault handler is the policy point**: guarantees are enforced
   lazily, at first touch, per page — validation (this chapter) and fixup
   application/signing (ch04) both ride it. A policy concerning only used
   pages costs proportionally to *use*: untouched pages pay zero, touched
   pages pay once until eviction, no upfront pass over gigabytes — while
   enforcement stays total, since no path to execution skips the fault.

</details>
