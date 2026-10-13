# Chapter 05 — Firmware security: the root of the chain

> **The 30-second version.** Every trust decision in modules 02,
> 05, 06, and 07 assumes somebody verified the first instruction
> that ran; this chapter is where that happens — on hardware the
> attacker physically owns. A $10 UART adapter reads your debug
> port, a $300 glitch rig skips your signature check, a $100k
> decap rig reads the key off the die: defense is economics before
> cryptography. Secure boot is chained signatures anchored in
> memory that cannot change, with an eFuse version counter making
> downgrades physically impossible; updates are a 2-phase commit
> across two flash banks so power loss at any of four points still
> boots a valid image.

## The attacker owns the board: the cost ladder that writes your threat model

This attacker has your board on a bench with a soldering iron and a
scope, so the threat model starts with a price list:

```
rung  attack                          gear                    cost
----  ------------------------------  ----------------------  --------
 0    read an open UART/SWD port      USB-UART adapter           $10
 1    voltage/clock glitch,           ChipWhisperer-Lite        $300
      first-try power analysis
 2    reliable fault injection        EMFI probe or laser     $1-10k
      station, lab DPA setup
 3    decap, FIB microprobe, read     focused ion beam +     $100k+
      the die, probe the buses        SEM
```

Rung 0 buys your debug prints; rung 1 a skipped branch; rung 2
faults on demand; rung 3 your masks' contents. **The fleet
externality re-prices the ladder**: a $100k decap that extracts a
key baked into 500,000 units costs 20 cents per unit and owns all
of them. The device's value sets the bar for attacking the
*device*; the key's blast radius sets the bar for attacking the
*key* — the sentence that explains per-device provisioning below.

## Secure boot: chained signatures anchored in memory that cannot change

Chapter 02's boot timeline was twelve trusting steps — nothing
checked *whose* bytes they were. Secure boot adds one idea, repeated
as a chain:

```
   the immutable root              each link = one verify + one jump
+---------------+  public-key hash burned in mask ROM / OTP
| mask ROM      |--- verify sig(stage1, k_boot) ---> stage 1
| cannot patch  |    + measure H(stage1) into the chain
+---------------+
                      stage 1 -- verify sig(stage2, k_app),
                                 version v_n >= OTP c? -- jump --> app
```

**The root must be immutable** — mask ROM (burned into the die at
the fab) or an OTP/eFuse cell holding a *hash* of the public key
(32 bytes is the whole budget; the key never lives in writable
memory). If the root hash sat in rewritable flash, one flash write
(rung 0) substitutes the attacker's key and signs his own chain.
**Stage N verifies N+1 over the image plus its metadata** — load
address, size, version — so a signed image can't be relocated or
relabeled.

**Anti-rollback is one-way arithmetic.** The image header carries
version `v`; an OTP counter holds `c`; the bootloader refuses
`v < c`. The counter is burn-only bits — nothing un-burns, which
is why it can't live in flash (rewritable: the downgrade rewrites
it too). The attack this kills needs no forgery — the attacker
flashes a *genuinely signed* old `v3` from your own archive, whose
bug was fixed in `v7` because it was a clean remote-code-execution
hole. The fuse counter is the only mechanism that can say no.

**The measurement chain is a hash chain — name the pattern.** Before
each jump a stage appends the next stage's hash to a running record,
`PCR_new = H(PCR_old || H(next))` — a TPM's extend operation, or
your own 32-byte boot-measurement register in retained RAM,
reportable to your server on first connect. Identify the structure,
because this is the third time the program builds it: **TLS 1.3's
transcript hash (module 18 chapter 04) — every flight hashed into
the running transcript before the Finished MAC seals it — and the
Merkle journal of copy-on-write file systems (module 14 chapter 04)
— each block's checksum living in its freshly written parent,
committed by one root-pointer write.** Three systems, one invariant:
*the record only grows, what was hashed is never re-read, and any
mismatch fails closed* — TLS aborts the handshake, the CoW root is
never published, secure boot refuses to jump. Append-then-commit is
a pattern, not a protocol feature.

## Where the key lives: OTP bits, sign-in-place chips, and a memory map split by one address bit

The signature scheme is the easy part; where the anchor physically
lives is the real question — bytes, buses, address bits:

```
option              capacity    what's stored     the attack that gets it
------------------  ---------   ---------------   -----------------------
OTP / eFuse bits    32-256 B    hash of pub key   glitch the compare
(burn-once, die)    (some 1KB)  or an AES key     (not the storage)
secure element      key never   private key       attack the SE's own
(ATECC608, I2C,     leaves      sealed inside     silicon (rung 3)
~$0.60 BOM)         the chip    (sign-in-place)
TrustZone-M (SAU)   map         key in SRAM/      fault injection, RDP
                    regions     flash; NS world   bypass, decap
                                cannot address
```

The **secure element** is the strongest practical answer: an I2C
chip (ATECC608 class) generates its P-256 keypair at provisioning
and never exposes the private half — the µC sends a digest over
the bus and gets a signature back (**sign-in-place**). The key
exists on no bus, in no RAM, in no flash; extraction means
attacking the SE's own silicon, a rung-3 problem. **TrustZone-M is
the free option, and its mechanism is pure chapter 01: it
partitions the memory map by address bits.** The SAU (Security
Attribution Unit — 8 regions, the MPU's shape again) marks ranges
secure or non-secure, and bit 28 of the address *is* the NS bit:
non-secure code addressing `0x0000_0004` hits the non-secure alias
of a region whose secure view lives at `0x1000_0004`. Every bus
transaction carries its NS bit, decided before the transaction —
no lookup, no world switch, no flush: the gates cost ~zero latency
where MMU-world equivalents trap. Non-secure code cannot even
*address* the secure half — except through **non-secure-callable
(NSC) gates**: small regions of `SG` (secure gate) instructions,
the only legal entry points.

```
       bit 28 = NS: one address bit splits the map
0x00000000 +----------------------------------------+
           | NON-SECURE view (NS=1 on the bus)     |  app code, app
           |  app SRAM, app peripherals            |  radios, blobs
           |   +-------------------+               |
           |   | NSC gate region   | [SG] sign()    |  the ONLY doors:
           |   |                   | [SG] counter() |  named entries
           |   +-------------------+               |
0x10000000 +----------------------------------------+
           | SECURE view (NS=0)                     |  keys, crypto,
           |  same silicon, NS code can't address   |  version counter
           +----------------------------------------+
```

That is **the broker pattern of module 16 chapter 01 at the µC
scale, drawn**: the browser's JS can't touch the C++ process's
memory — it calls named gates (the DOM, the bindings); here the
NS world can't address the key's bytes — it calls gates you wrote.
Both turn "read the secret" into "ask a gate that refuses."

**Crypto performance honesty.** A bare 40 MHz core pays real
seconds per verify, and the numbers pick the algorithm: RSA-2048
verify at e=65537 — the *fast* RSA direction — is **~4-8 s** with
a plain bignum library (a tuned one reaches ~300 ms; ask *which
library* before quoting a number); ECC P-256 verify is **~0.5-1
s**; Ed25519 software **~0.5 s**. The operands explain it: RSA
multiplies 2,048-bit numbers, ECC's field numbers are 256 bits —
one-sixteenth the operand. That is why boot chains are ECC, and
why ROMs **verify-once-then-cache-trust**: pay at boot, run stage
1 trusted from RAM — re-verifying per call would put seconds on
operations that need microseconds. The volume path is cheaper
still: an HMAC-SHA256 chain with a per-device AES key verifies
128 KB in **tens of milliseconds** — but a symmetric key
extracted from one device would forge updates for the fleet, so
each device gets its own key and the server signs per-device.

## Updates as a 2-phase commit: the A/B banks and four power cuts

Flash can't be overwritten in place (erase is block-granular,
50 ms to 1 s per sector — module 14's physics), so mid-update
power loss would leave half old image, half new: a brick. The A/B
scheme is the atomic-update pattern at kilobyte scale — *literally*
Android's seamless-update design (module 05 chapter 06). Two
banks: A runs `v7`, B receives `v8`; the surviving state is a
marker — which bank to boot — written as two CRC'd slots
`{generation, bank, CRC}`, picked by highest *valid* generation
(torn writes fail the CRC by design):

```
  A: v7 (running)
-- [erase B] -- [write v8 to B] -- [PREPARE] -- [COMMIT] -- [B boots] -- [confirm]
                   |               write         erase          app runs   fuse
                   |               gen6->B       old slot       (trial)    burn
                   CUT 1           in SPARE      (the flip)                  |
                   torn B          torn marker   two valid marks         CUT 4
                                  (CUT 2)       (CUT 3)               unconfirmed
```

Walk every cut — what is on disk, what the bootloader does:

- **CUT 1 (mid-write of v8 into B):** A = signed `v7`, B = torn,
  markers unchanged (`gen5 -> A`). B is garbage behind an
  unpublished pointer; boots A, restarts. Harmless by construction:
  *the pointer never moves until the target is complete and signed.*
- **CUT 2 (mid-write of the PREPARE marker):** B = complete signed
  `v8`; one valid marker, one torn slot. Boots A; the update re-runs
  step 2 only.
- **CUT 3 (mid-flip):** both slots valid — `gen5 -> A`,
  `gen6 -> B` — or, erase done, only `gen6 -> B`. Highest
  generation wins; boots B, complete and signed. **The flip is
  atomic by policy, not by hardware**: no single write is atomic,
  but the tie-break rule resolves every intermediate state to a
  valid image. The write order is load-bearing — erase-old-*then*-
  write-new leaves a cut with *zero* valid markers, the one
  unrecoverable state; this is module 14 chapter 03's journal
  ordering.
- **CUT 4 (B's first boot, before the app confirms):** B runs in
  *trial* mode — N boots to confirm; if the app never does, the
  bootloader re-points the marker at A. Rollback is still legal
  here *by design*.

The marker pair **is a 2-phase commit**: PREPARE (`gen6 -> B`,
both banks intact) and COMMIT (old marker erased). Between them,
complete or abort are both safe — both banks hold complete signed
images. Only after a *confirmed* boot does the bootloader burn the
anti-rollback eFuse to `c = 8` — **burn only on confirmed success**,
never at download time, or a lemon update becomes permanent.

**Delta-update economics.** The transfer window is the vulnerable
window: radio on, mid-update, the attacker's best moment. The
arithmetic: 128 KiB = 131,072 B. BLE's quoted 20 kB/s is the PHY
ceiling (247-byte MTUs plus data length extension); a legacy OTA
profile — 20-byte chunk, per-chunk CRC and ack, one chunk per
~75 ms connection event — sustains ~267 B/s. 131,072 ÷ 267 ≈
**491 s ≈ 8 minutes** for the full image; a bsdiff-style delta at
~5-10% is 6-13 KB — **seconds** on the same link (sub-second at
the modern ceiling, tens of seconds at the legacy rate). That
10-60× window shrink is why consumer OTA is delta-based. But
bsdiff wants a workspace — old image, patch stream, decompression
state, output page — and **a 64 KB patch workspace does not fit
in 32 KB of RAM, with no allocator to save it** (module 01: the
heap is absent; a hard wall). Two designs survive: *in-place*
flash patching (apply the delta one page at a time between banks
— workspace one 2-4 KB page, cost a flash-block-aware patch
format) or *external-RAM staging* (a serial PSRAM chip holds the
workspace, cost a part, pins, and a verify pass). The memory
constraint, not the crypto, drives the update architecture.

## The debug port: the backdoor you soldered on yourself

SWD needs two pins and a $10 probe, and out of reset it grants
attach/halt/dump: stop the core, read all RAM and flash,
single-step your crypto. An open port makes everything above
irrelevant — signature checks run on a core the attacker can
pause, and keys in RAM are visible at halt. The lock is a
debug-unit option byte, STM32's RDP being the canonical taxonomy:
**level 0** — open (development). **Level 1** — debug reads
blocked, but reversible via mass erase, and the level with the
folklore: *connect-under-reset* attacks (hold the core in reset,
where protections haven't been applied, on families whose option-
byte load races the attach) and **glitch-to-downgrade** stories —
a voltage transient during option-byte load convinces the chip
it's level 0, then the probe dumps everything. Treat level 1 as
"stops rung 0, delays rung 1." **Level 2** — RDP permanent,
debug fused off, *no way back*. The bricking trade, stated
plainly: a boot-looping field unit at level 2 cannot be rescued or
diagnosed, only replaced. Hence the practice: ship locked, keep
unlocked dev units, make the SKU split a BOM decision, not a
late-night panic.

## Glitches, power traces, and the blobs above your silicon

**Fault injection attacks the check, not the crypto.** A voltage
or clock glitch — a nanoseconds-wide dip at the right cycle —
makes the core skip an instruction, the classic target being a
signature check's conditional branch: the comparison is corrupted
or the branch doesn't execute, and control falls through to the
jump. The rig is a ChipWhisperer at **$300**; professional
EMFI/laser stations ($1-10k) buy repeatability. The
countermeasures are code, and cheap: **check twice** — verify,
branch, verify again, branch again — with the second check
re-deriving the boolean independently, so a glitch corrupting the
first result can't satisfy both; plus **fault-detecting control
flow**, module 02 chapter 06's CFI shrunk to the branch scale: a
token walked through registers and checked after the jump, so a
skipped branch lands in a state the next check rejects. The lab
below quantifies the difference.

**Side channels at device scale.** A 10k-trace DPA campaign
(10,000 power captures of AES rounds under known plaintexts,
correlated against hypothetical key-schedule bytes — module 18
chapter 03's attack measured at the scope instead of the cache)
recovers an unprotected AES key. Countermeasures: **masking**
(split every sensitive value into randomized shares — 2-4× code
size and cycles) and **hiding** (shuffled operations, dummy rounds
— flatter traces at a latency cost). The consumer exposure is the
**batch attack**: one capture during one known-plaintext session —
your device encrypting a known handshake value — yields the key,
and the same key ships in every unit: the cost-ladder multiplier
again, answered only by per-device keys.

**The supply chain above the silicon.** Your image contains code
you cannot read: vendor SDK blobs — the radio stack, the
SoftDevice-class precompiled BLE controller, HAL `.a` files —
hundreds of KB running with DMA and higher priority than your
app. The honest fact: most embedded builds are **not reproducible**
(toolchain + SDK + blob versions + build-host state; the pinned-
toolchain, hermetic-container discipline is arriving as CISA's
2023-2024 SBOM requirements land on firmware). The signature
proves what the image is, not who built it or from what — that gap
is module 21's formal side.

## What insiders know

- **The mask ROM cannot be patched — ever.** Its bugs are silicon
  recalls: Apple's checkm8 lived in the boot ROM, unpatchable on
  every affected device forever; ESP32's secure-boot v1 RSA check
  fell to a public VCC-glitch bypass (2019) that cannot be fixed,
  only superseded — the only fix is a new chip revision.
- **The default-key pandemic.** The same RSA/AES key in every unit
  of a product family — the camera/router-fleet story repeats
  quarterly. One $300 glitch or $100k decap later, the key signs
  malicious "updates" for every unit ever made: owning one device
  owns the fleet. The discipline is per-device keys; the hobby
  contrast is ESP32's closed boot ROM vs the Pico's boot2 — 252
  open bytes you can read and replace.
- **The factory sees your keys.** Per-device provisioning means
  the contract manufacturer's line injects each key — into what?
  An HSM at the station, keys generated *inside* it, so operators
  and logs see key handles, never bytes. A plan where the key
  transits a CSV file is a fleet-key CVE waiting for its number.
- **The anti-rollback fuse is a one-way business decision.** The
  war story: fuses burned at v10; v10 drains batteries, and its
  radio bug blocks receiving v11 on some units — bricks, because
  physics forbids shipping the known-good v9. Policy that
  survives: burn only on security-critical releases, keep version
  slack, gate the burn behind a human sign-off.
- **"Secure boot without a secure element is DRM for your own
  firmware."** With an on-die key you can't extract, it's
  anti-tamper for the customer's benefit; with a flash-resident
  key the manufacturer can extract, it's a lock on who may
  publish updates — the exact right-to-repair fight. Decide which
  you're building before marketing does.

## See it yourself

**Lab 1 — sign a 4 KB image, write the bootloader's verifier.**
Needs `python3` and `pycryptodome`.

```python
# sign.py — keypair, image, and the signed blob (128-byte header)
import struct, os
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa
from Crypto.Hash import SHA256
sk = ECC.generate(curve='ed25519')
open('dev_key.pem','wb').write(sk.export_key(format='PEM').encode())
open('dev_pub.pem','wb').write(
    sk.public_key().export_key(format='PEM').encode())
image = os.urandom(4096)                             # "the firmware"
hdr12 = struct.pack('<4sII', b'IMG1', 8, len(image)) # magic|ver|size
sig = eddsa.new(sk, 'rfc8032').sign(                 # signs the DIGEST:
    SHA256.new(hdr12 + image).digest())              # bootloaders stream
blob = hdr12 + b'\x00'*4 + sig + b'\x00'*48 + image  # 128-byte header
open('fw_v8.bin','wb').write(blob)
```

The verifier — the ~30 lines a bootloader actually runs. The µC
can't hold the image in RAM: it streams SHA-256 over flash, then
verifies the 32-byte digest.
```python
# verify.py
import struct, sys
from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa
def bootloader_check(blob, pub, otp_version):
    magic, version, size = struct.unpack_from('<4sII', blob, 0)
    sig, image = blob[16:80], blob[128:128+size]
    if magic != b'IMG1':    return 'refuse: not an image'
    if len(image) != size:  return 'refuse: size lies'
    if version < otp_version:
        return f'refuse: rollback (v{version} < OTP {otp_version})'
    digest = SHA256.new(blob[:12] + image).digest()
    try:
        eddsa.new(pub, 'rfc8032').verify(digest, sig)
        return f'OK: v{version} authentic -> jump'
    except ValueError:
        return 'refuse: FORGERY (bad signature)'
pub = ECC.import_key(open('dev_pub.pem','rb').read())
print(bootloader_check(open(sys.argv[1],'rb').read(),
                       pub, int(sys.argv[2])))
```

```console
$ python3 sign.py && python3 verify.py fw_v8.bin 7
OK: v8 authentic -> jump
$ python3 verify.py fw_bad.bin 7      # fw_v8.bin with byte 1000 flipped
refuse: FORGERY (bad signature)
```

Flip one byte anywhere — payload or header — and the digest moves:
refusal. A relabeled version dies earlier, at the version gate (the
signature covers the header, so the label can't be forged anyway).

**Lab 2 — the 2-phase marker, power cuts at all four points.**
The marker is a 12-byte CRC'd record; the bootloader picks the
highest-generation valid marker; the update walks the four steps
and power strikes inside each:

```python
# ab_sim.py — A/B update as 2-phase commit, cuts at 4 points
import struct, zlib
ERASED = b'\xff' * 16
pack = lambda g, b: struct.pack('<II', g, b) + \
    struct.pack('<I', zlib.crc32(struct.pack('<II', g, b)) & 0xffffffff)
def read_mark(slot):                    # torn write -> CRC fails
    if slot == ERASED: return None
    body, crc = slot[:8], struct.unpack('<I', slot[8:12])[0]
    return (struct.unpack('<II', body)
            if zlib.crc32(body) & 0xffffffff == crc else None)
def boot(disk):
    marks = [m for m in (read_mark(disk['mark0']),
                         read_mark(disk['mark1'])) if m]
    return max(marks) if marks else None      # highest generation
def update(disk, v8, cut):
    disk['bankB'] = v8 if cut != 1 else v8[:2048]    # 1: write B
    if cut == 1: return
    old_gen, _ = boot(disk)
    spare = 'mark1' if read_mark(disk['mark0']) else 'mark0'
    new = pack(old_gen + 1, 1)                       # 2: PREPARE
    disk[spare] = new[:8] + b'\xff'*8 if cut == 2 else new
    if cut == 2: return
    disk['mark0' if spare == 'mark1' else 'mark1'] = ERASED  # 3: COMMIT
    if cut == 3: return
    if cut == 4: return                              # 4: pre-confirm
    disk['fuse'] = 8                                 # burn AFTER confirm
disk = {'bankA': b'A-v7'*999, 'bankB': ERASED,
        'mark0': pack(5, 0), 'mark1': ERASED, 'fuse': 7}
v8 = b'B-v8'*999
for cut in (1, 2, 3, 4):
    d = dict(disk); update(d, v8, cut)
    print(f'cut {cut}: fuse=v{d["fuse"]} -> boot ' + 'AB'[boot(d)[1]])
for c in (1, 2, 3, 4):                # anti-brick invariant
    d = dict(disk); update(d, v8, c); assert boot(d)
print('assert: every cut leaves a bootable bank')
```

```console
$ python3 ab_sim.py
cut 1: fuse=v7 -> boot A
cut 2: fuse=v7 -> boot A
cut 3: fuse=v7 -> boot B
cut 4: fuse=v7 -> boot B
assert: every cut leaves a bootable bank
```

Cuts 1-2 boot the *old* image, cut 3 the *new* one, cut 4
new-but-unconfirmed — a trial budget can still revert to A because
the fuse hasn't burned. Then break it on purpose: swap steps 2 and
3 (erase-old *first*), cut between them, and `boot()` returns
`None` — the zero-marker gap that bricks the device. The write
order is a design rule, not a style choice.

**Lab 3 — the glitch paper-lab: check-once vs check-twice.** The
control flow under attack:

```
 check-once                      check-twice
 -----------                     -----------
 verify()                        verify()
 if (!ok) halt();  <- glitch     if (!ok) halt();    <- glitch 1
 jump(entry);                    ok2 = verify();      (re-derived)
                                 if (!ok2) halt();   <- glitch 2
                                 jump(entry);
```

One glitch skips one instruction. Model a 200-cycle check sequence,
each branch occupying one cycle, one glitch landing randomly per
boot:

```python
# glitch_sim.py — one skipped instruction vs two checks
import random
random.seed(20)
N = 200
pwned = lambda checks, pulses: all(
    c in {random.randrange(N) for _ in range(pulses)} for c in checks)
for name, checks in [('check-once  ', (100,)),
                     ('check-twice ', (100, 180))]:
    wins = sum(pwned(checks, 1) for _ in range(10000))
    print(f'{name} 1 glitch/boot: {wins:5d}/10000 boots pwned')
```

```console
$ python3 glitch_sim.py
check-once   1 glitch/boot:    67/10000 boots pwned
check-twice  1 glitch/boot:     0/10000 boots pwned
```

Check-once loses ~0.7% of boots per attempt — a patient attacker
with a $300 rig and a reset line owns it within hundreds of boots.
Check-twice never loses to one glitch: both branches must be
skipped in the same boot (two pulses land on both only with
probability ~(1/200)²). The caveats matter as much: the checks sit
80 cycles apart so one *wide* glitch can't cover both, and `ok2`
is re-derived by a second verify, not copied. Hardware-optional: a
ChipWhisperer-Lite plus the public XMEGA tutorials recover an AES
key by CPA in an afternoon — 10k traces is the conservative
campaign number.

## How to use this knowledge

- **Datasheet triage.** Read any MCU security chapter with four
  questions: what is the immutable root, who signs stage 2, what is
  the atomic update primitive, what does the lock bit *cost* you
  (RDP level 2 = no field diagnosis, ever). Any "nothing" answer
  is the device's actual posture.
- **Pre-production checklist.** Debug lock set; per-device keys
  provisioned through an HSM at the CM (never a CSV); fuse-burn
  policy written and signed off; update server signs per-device.
- **Incident reading.** "Same key in every unit" = one extraction
  owns the fleet; "unpatchable boot ROM bug" = recall, not update;
  "RDP level 1" in a pen-test report = delayed, not stopped.
- **Design review.** For every interleaving of power loss and the
  two marker writes, does the bootloader resolve to a valid image?
  Draw the four cut points and make the reviewer point at each.

## Connections

- **Module 20 ch01** — the memory map TrustZone-M splits at bit 28
  is that map with one bit repurposed.
- **Module 20 ch02** — the twelve-step boot timeline being signed
  here: boot ROM, MSP-from-address-zero, the vector table.
- **Module 20 ch04** — the RTOS runs on top of this chain: its
  guarantees inherit whatever boot proved (and didn't).
- **Module 18 ch03** — the key-storage and bignum cost tables: every
  verify-second here is an operation count from that chapter; its
  side-channel catalog becomes the device-scale 10k-trace DPA.
- **Module 05 ch06 + module 06 ch05** — the rich descendants:
  Android's A/B seamless updates and AVB's vbmeta/dm-verity are
  this chapter's bank scheme and measurement chain at gigabyte
  scale; iOS's boot ROM → iBoot → AMFI chain is the same ladder
  with a TEE on top.
- **Module 02** — the attack catalog: the glitch is its control-flow
  hijack at the branch scale; check-twice and fault-detecting CFI
  are its mitigation ladder shrunk.
- **Module 07 ch03 (the macOS boot-chain material, landed in the
  live tree as m07 ch06)** — the desktop world's mirror and its
  orphan repair: SIP and the signed-kernel row of the three-kernel
  table had no upstream chapter deriving *why* a chain of trust
  exists; this is that chapter, and m07's comparison can now point
  back at a source.

## Quiz

1. **Concept.** Derive why the chain's root must live in memory
   that cannot change (mask ROM or OTP). What breaks if the root
   public-key *hash* sits in rewritable flash, and which rung of
   the cost ladder performs the break?
2. **Concept.** TrustZone-M decides an access's security state
   from one address bit before the bus transaction. Explain how
   the NS bit makes non-secure code unable to even *address* the
   key, what NSC gate regions are for, and why this is a broker
   pattern rather than a protection ring.
3. **Predict the number.** A 40 MHz µC pays ~6 s per RSA-2048
   verify and ~0.75 s per ECC P-256 verify. (a) Compute boot
   latency for a three-stage all-RSA chain vs all-ECC. (b) What
   does the ratio say about operand size, and why do ROMs
   verify-once-then-cache-trust rather than re-verify per call?
4. **Predict the number.** A BLE device updates a 128 KiB image;
   the legacy OTA profile sustains ~267 B/s. (a) How long is the
   vulnerable window for the full image? (b) A bsdiff delta at 7%
   is what size, and how long is its window? (c) The patch engine
   wants a 64 KB workspace on a 32 KB RAM part — name the two
   surviving designs and what each costs.
5. **Predict the timeline (power-loss walk).** The v7→v8 update
   is at: step 2 complete (`gen6 -> B` written to the spare slot),
   and power dies mid-erase of the old marker in step 3. What is
   on disk, what does the bootloader do at next boot, and what
   single design change would turn this exact cut into a brick?
6. **Diagnose.** 50,000 units ship with one shared RSA update key
   and RDP level 1. A researcher extracts the key from one unit
   with a $300 glitch rig and publishes a signed malicious
   "update." Give the fleet-externality arithmetic, and the two
   design changes that would have contained it to one unit.
7. **Diagnose.** Fielded units update v9 → v10, the app crashes on
   v10, and instead of reverting, every unit boot-loops forever.
   The A/B trial logic was implemented as designed. What was
   burned too early, and what ordering rule between "confirm" and
   "burn" would have saved the fleet?
8. **Cross-chapter.** The boot measurement chain, TLS 1.3's
   transcript hash (module 18 chapter 04), and the Merkle journal
   (module 14 chapter 04) are one pattern. Name the two shared
   invariants and what each system does on mismatch — and why all
   three answers are the same design decision.

<details>
<summary>Answers</summary>

1. With the root hash in rewritable flash, one flash write
   substitutes the attacker's key hash; he signs his own stage 1
   and every verification below passes. The break is rung 0, $10
   — an open debug port writes flash directly. Immutability turns
   the anchor from data into physics.
2. Bit 28 is the NS bit: non-secure code emitting `0x0000_0004`
   produces a transaction with NS=1 that secure regions never
   match — the key's bytes are unreachable addresses, not
   protected ones. NSC regions hold `SG` instructions, the only
   legal NS→secure entries, so the secure world exposes named
   functions instead of memory. That is the broker pattern
   (module 16 ch01 — JS reaching the C++ process only via DOM
   bindings), not a ring: no privilege machinery, just a call
   through a gate.
3. (a) RSA: 3 × 6 s = **18 s**; ECC: 3 × 0.75 s = **2.25 s**.
   (b) RSA-2048 multiplies 2,048-bit operands (a 64×64-word school
   book); ECC's field numbers are 256 bits, one-sixteenth the
   operand — at 40 MHz, seconds versus fractions. Verify-once-
   then-cache: the ROM pays at boot and stage 1 runs trusted;
   re-verifying per call would put seconds on operations that need
   microseconds.
4. (a) 131,072 ÷ 267 ≈ **491 s ≈ 8 minutes**. (b) 7% of 128 KiB ≈
   9.2 KB → 9,216 ÷ 267 ≈ **35 s** (sub-second at the 20 kB/s PHY
   ceiling). (c) *In-place flash patching* — apply the delta one
   page at a time between banks, workspace one 2-4 KB page, cost a
   flash-block-aware patch format; *external-RAM staging* — a
   PSRAM chip holds the 64 KB workspace, cost a part, pins, and a
   verify pass. The 32 KB wall is physics; only these two route
   around it.
5. On disk: A = signed v7; B = complete signed v8; spare slot =
   valid `gen6 -> B`; old slot mid- or fully erased (erased is
   simply ignored). The bootloader keeps the valid markers, picks
   the highest generation — `gen6 -> B` — and boots the verified
   v8: the flip resolves by tie-break rule, not by an atomic
   write. The brick: ordering the flip erase-old-*then*-write-new
   leaves this same cut with zero valid markers — nothing to
   boot.
6. The key's value is the fleet's: ~$330 of gear and one unit buys
   a signing key controlling 50,000 units — under a cent per unit.
   Containment: per-device keys, provisioned at the CM inside an
   HSM so the key never transits a file — blast radius one unit;
   and RDP level 2 (or no debug port on the shipping SKU) removes
   the $300 rung, pushing extraction to rung 3.
7. The anti-rollback eFuse was burned to c = 10 at download (or at
   v10's first boot, before the app's confirm). When v10 crashes
   and the trial budget reverts the marker to A, the bootloader
   refuses v9 (9 < 10): the one-way counter now forbids the only
   good image — a boot loop with no exit. The rule: burn only
   after *confirmed* app boot, never on download or first boot.
8. Invariant one: the record only grows — each stage, transcript
   flight, or freshly written parent is hashed into the running
   value, never re-read. Invariant two: one commit seals everything
   — the root-pointer write, the Finished MAC, the marker flip. On
   mismatch: TLS aborts the handshake, the CoW root is never
   published, secure boot refuses to jump. All three fail closed:
   each system's value is a final check that is cheap and
   impossible to forge after the commit.

</details>
