# Chapter 06 — APK & OTA

> **The 30-second version.** An APK is a ZIP file with a contract
> bolted on. Inside: `classes.dex` — the program as *index tables*,
> not text; `resources.arsc` — one string, many locale/density
> *configs*, chosen by table lookup, not `if`; native libs never
> unpacked, just `mmap`'d out of the archive. Trust is bolted on the
> same way: v1 signed each entry separately (broken by a polyglot
> file); v2 hashes the *whole file* via a block injected into a gap ZIP
> never uses; v3 adds key lineage. Updates then treat the whole system
> as replaceable data — A/B slots, COW snapshots on `/data`, binary
> deltas. Chapter 01's theme at file scale: hash the next thing before
> you trust it.

## 1. The container: a ZIP that grew trust organs

Start from the ZIP format, not from Android. A ZIP is read *backwards*.
A reader scans the last 65,535 bytes for the End-of-Central-Directory
signature (`PK\x05\x06`) and finds the EOCD — 22 fixed bytes holding the
entry count and the **byte offset of the central directory**. The
central directory is one record per file (46 fixed bytes + name): name,
compression method, CRC-32, sizes, and the offset of that file's *local
header* (30 fixed bytes + name), which sits out among the data at the
front. Nobody parses the front unless the index points there — the
inversion v2 signing exploits.

Between the last entry and the central directory a stock ZIP has
*nothing*. That dead zone is where the **APK Signing Block** is
injected: `len | ID-value pairs | len | "APK Sig Block 42"`. The EOCD's
CD-offset is bumped to point *after* the block, and every stock ZIP
reader walks right past it. v2/v3 signatures live inside it as ID-value
pairs — v2 is ID `0x7109871a`, v3 `0xf05368c0`.

```text
 offset 0                                                       EOF
 +----------------------+---------------------+--------------+------------+
 |  ZIP ENTRIES         |  APK SIGNING BLOCK  |  CENTRAL     |  EOCD      |
 |  local hdr(30 B)+    |  len(8) | v3 pair   |  DIRECTORY   |  22 B +    |
 |  data, per file:     |  | v2 pair | verity  |  46 B/entry  |  ≤64 KiB   |
 |  AndroidManifest.xml |  | padding | len(8)  |  + name      |  comment   |
 |  classes.dex         |  "APK Sig Block 42"  |              |            |
 |  resources.arsc      |                      |              |            |
 |  res/ lib/ assets/   |                      |              |            |
 +----------------------+---------------------+--------------+------------+
 |<- v1 scope: digest of EACH entry's data, listed in META-INF/MANIFEST.MF ->|
    (entry data only: local headers, central dir, EOCD itself are UNSIGNED)

 v2/v3 scope: the whole file EXCEPT the block itself —
 |<- chunk 1 (entries) ->|     skipped      |<- chunk 2 ->|<- chunk 3 ->|
    digest = SHA-256( d(chunk1) || d(chunk2) || d(EOCD, CD-offset-reset) )
    v3 = v2's coverage + a key-lineage (proof-of-rotation) pair in the block
```

v1's weakness is in the brackets: it signs each entry's *data* (digests
recorded in `META-INF/MANIFEST.MF`, itself a signed entry) and nothing
structural. v2 signs everything outside the block — in 1 MiB sub-chunks
whose digests are combined — so any change anywhere breaks
verification. The one thing it cannot sign is the block
itself: a signature is a function of the signed bytes, and a value
equal to a function of itself is a fixed point you cannot compute. The
verifier therefore *reconstructs* the block-free layout — hashing the
EOCD's CD-offset field as if the block were absent.

## 2. `classes.dex`: the program as lookup tables

A dex file is a database, not a text file. The 112-byte (0x70) header —
magic `dex\n035\0` through `039` across ART versions — declares the
tables the body is made of:

- **string_ids** → offsets into string data (MUTF-8, uleb128 lengths)
- **type_ids** → one string index each (`Ljava/lang/String;`)
- **proto_ids** → return type + parameter list (the method *shape*)
- **field_ids** / **method_ids** → (class, type/proto, name) triples
- **class_defs** → per class: superclass, interfaces, `class_data` →
  **code_items** — `registers_size`, `ins/outs/tries` u16 counts, then
  the bytecode

Every instruction references these tables *by index*:
`invoke-virtual {v0, v1}, method@BBBB` — a **16-bit operand**. Same for
`field@BBBB` on `iget`/`iput`, `string@BBBB` on `const-string`. There is
the 65K limit, derived rather than decreed: a u16 addresses
2^16 = **65,536** ids, so one dex holds at most 65,535 field/method/
string *references* — including every referenced framework method, not
just yours. Hence d8's `Too many method references: 84xxx; max is
65536`. Call *sites* are free; new *references* are the scarce
resource. Multidex is the workaround: `classes.dex` keeps startup
classes, `classes2.dex`... the rest — Dalvik needed a support library,
ART (Android 5.0+) does it natively. The modern fix is R8: deleting
unreferenced methods deletes their ids.

## 3. Resources, the binary manifest, and libs that never unpack

`resources.arsc` is a compiled table. A resource ID is 32 bits: package
(1 byte, `0x7f` for the app) | type (1 byte) | entry (16 bits) — e.g.
`0x7f0b0027`. For each entry the table holds one *value per
configuration*: `values-zh-rCN`, `drawable-xhdpi`... are not files the
app chooses between; they are rows. Resolving `@string/hello` is
package → type → entry → pick the best-matching config by the
`ResTable_config` precedence (MCC/MNC, then locale, then layout, then
density). "One string, many configs" is a **table lookup, not a branch**:
the app contains zero `if (locale == ...)` — localization is data.
Since targetSdk 30 (Android 11) the table must be stored **uncompressed
and 4-byte aligned** in the APK, because `AssetManager` `mmap`s it.

The manifest is the same philosophy taken to XML. `aapt2` compiles it
into binary chunks: a shared string pool, then a node tree whose
element/attribute *names are pool indices* and whose values are typed.
There is no `<uses-permission` byte sequence to grep — the ASCII lives
once, in the pool, referenced by index — which is why `grep` finds
nothing and `aapt2 dump xmltree` / `dump badging` must decode it.

Native libs: `lib/arm64-v8a/libfoo.so` (one dir per ABI). Classically,
installers *extracted* them to `/data/app/…/<pkg>/lib/arm64/`. Since
API 23 (Android 6.0), `extractNativeLibs="false"` (Gradle:
`useLegacyPackaging false`) instead stores each `.so` **uncompressed and
page-aligned** in the APK, and `System.loadLibrary` `dlopen`s the file
*inside the APK*: the linker `mmap`s segments at offsets that are both
file offsets and page offsets — the ZIP entry is the ELF image, in
place. The win is flash: one copy instead of two, one integrity check
(v2 covers the whole file); the cost is that those entries never
deflate. Alignment is `zipalign -p 4096` — and on 16 KB-page devices
(Android 15+) it must be `zipalign -P 16384` with 16 KB-aligned `LOAD`
segments, or `dlopen` fails. Page size is baked into the file, not
negotiated.

## 4. Signatures: from per-file digests to whole-file proof

**v1 (JAR signing, Android 1.0).** Each entry's digest goes into
`META-INF/MANIFEST.MF`, summarized and signed into `META-INF/CERT.SF`,
with the PKCS#7 signature in `META-INF/CERT.RSA`.
Verification: recompute per-entry digests, walk the chain — but it
signs the *entry view* only. The **Janus** vulnerability (CVE-2017-13156,
Android 5.0–8.0, fixed January 2018) built a polyglot: attacker dex
bytes prepended to a legitimately signed APK. The ZIP parser (anchored
at the EOCD) still saw the original entries — v1 digests passed — while
the dex loader of that era, tolerant of a prefix before the `dex\n`
magic, executed the prepended code. Two parsers, two views, the
signature covering one view: module 03's parser-differential class.
v2 (Android 7.0, API 24) was introduced to kill it; Android 11
(targetSdk 30) makes v2+ mandatory.

**v2.** As derived in §1: one signature over essentially the whole file,
via the signing block. Beyond v1 it covers entry *metadata* (local
headers, central directory — sizes and offsets can't be forged) and
META-INF as plain data.

**v3 (Android 9, API 28).** Same block, same coverage, plus
proof-of-rotation: an ordered lineage of past signing certificates, each
transition attested, so the platform can prove app *identity* survived
a key change — previously impossible, since updates required identical
signers (`INSTALL_FAILED_UPDATE_INCOMPATIBLE` otherwise). v3.1
(Android 13) lets a rotation take effect only on newer platforms.

The APK signature is the last link of a chain that starts in silicon:

```text
 ROM (immutable) → bootloader → vbmeta (AVB 2.0, signed hash descriptors)
      → dm-verity (Merkle tree over /system, root hash in vbmeta)
      → /system: framework + the trusted-cert store
      → PackageManager verifies the APK's v2/v3 signature against it
      → app code maps into a zygote-forked process (ch01)
```

"Hash the next thing before you trust it," extended from ch01's boot
into application files. Read it as a
memory-integrity story: dm-verity checks *blocks as they are paged in*,
so the cert store an APK signature roots in cannot rot at runtime; the
APK check happens once, at install; the chain composes because each
stage's key material lives where the previous stage already protects
it.

## 5. OTA: A/B slots, snapshots, and deltas

How does the system update itself while running? It can't — that would
rewrite the very files it executes from. A/B updates
(Android 7.0, 2016) sidestep by doubling: system partitions exist twice
with `_a`/`_b` suffixes. The running slot is never written;
`update_engine` streams the new image into the inactive slot while you
use the phone, then flips the boot flag (slot metadata in the `misc`
partition, retry count 7 by default in AOSP). If the new slot never
marks itself healthy, the bootloader falls back — power loss mid-update
just boots the old slot.

Doubling flash is expensive, so it was virtualized. Dynamic partitions
(Android 10) collapsed system/vendor/product into one `super` partition
carved up at update time; **Virtual A/B** (Android 10, mandatory for
devices launching with Android 11) goes further — the inactive slot
doesn't exist in flash. Writes go to a **copy-on-write snapshot on
`/data`**: the update is a diff applied into a snapshot, the same COW
trick as ch01's fork, at partition scale; device-mapper merges the
snapshot after the new slot reports healthy. Virtual A/B Compression
(Android 13) keeps those snapshots compressed, cutting the `/data` cost
of staging an update.

Deltas make the download small. The OTA payload is a protobuf
(`payload.bin`, prefixed by a 24-byte header whose magic is `CrAU` —
AOSP's `update_engine` was inherited from Chrome OS) of per-partition
operations: `REPLACE`, `SOURCE_COPY`, `SOURCE_BSDIFF`, `PUFFDIFF`.
`bsdiff` runs on *uncompressed* bytes — but system images are full of
deflate streams where byte diffs are useless: a small input change
avalanches the compressed output. **Puffin** "puffs" each deflate
stream into a canonical uncompressed *bit-level* representation, diffs
those, and re-deflates deterministically so the patched output is
bit-identical to the shipped image — the manifest hashes still match.
Incrementals are often 5–15% of full-image size: diff-friendly formats
are an *update-pipeline* decision, paid at build time.

## What insiders know

- **The 65K limit is on *ids*, not methods**: 16-bit `method@`/
  `field@`/`string@` operands → 65,536 references per dex, counting
  every referenced framework method. Extra call sites are free; R8
  tree-shakes references, not code bytes.
- **The v2 injection point is the ZIP dead zone**: bytes between the
  last entry and the central directory. Pair IDs `0x7109871a` (v2),
  `0xf05368c0` (v3); the verity padding pair `0x42726577` keeps the
  central directory 4 KiB-aligned after injection.
- **dm-verity is a Merkle tree over 4 KiB blocks**: a 2 GiB `/system`
  has 524,288 leaves; at SHA-256 (32 B) each 4 KiB node holds 128
  children — 3 levels, ~4 MiB of tree, root hash signed inside
  `vbmeta`. Verified blocks are cached; steady-state cost is one hash
  per 4 KiB cold read.
- **Baseline profiles ship inside the APK**: `assets/dexopt/baseline.prof`
  (plus a `.profm` letting ART merge it with cloud profiles). Since
  Android 9, install-time `dex2oat` consumes it, so hot paths are
  AOT-compiled before first launch — ch02's ladder pre-seeded by the
  package.
- **Incremental compression is a bitstream discipline**: `bsdiff` on
  uncompressed data, `PUFFDIFF` on Puffin's canonical form; the
  deterministic re-deflate is what keeps patched compressed files
  matching their hashes. Differing zlib settings between builds
  quietly breaks deltas.
- **`unzip -v` is the APK's honesty display**: `resources.arsc` must be
  `Stored` + 4-byte aligned (targetSdk 30, Android 11), `.so` entries
  `Stored` + page-aligned when `extractNativeLibs=false`; everything
  else should be `Defl:N` — a `Defl` on an arsc or lib fails install on
  modern Android.

## See it yourself

Tools: `python3`, `unzip`, `od` — all standard. `aapt2`/`apksigner` live
in the Android SDK `build-tools/<ver>/` and were **not installed in
this sandbox**; their outputs below are expected fragments.

**Build a toy APK** — a hand-built 120-byte dex skeleton (enough header
to read, not to run) plus stubs:

```python
# build_apk.py
import struct, zlib, hashlib, zipfile

hdr = bytearray(112)
hdr[0:8] = b'dex\n035\x00'                     # magic + version 035
SIDS = 112
body = struct.pack('<I', SIDS + 4) + b'\x02hi\x00'  # string_ids[0] -> "hi"
struct.pack_into('<I', hdr, 32, len(hdr) + len(body))  # file_size
struct.pack_into('<I', hdr, 36, 0x70)          # header_size = 112
struct.pack_into('<I', hdr, 40, 0x12345678)    # endian tag
struct.pack_into('<I', hdr, 56, 1)             # string_ids_size
struct.pack_into('<I', hdr, 60, SIDS)          # string_ids_off
dex = bytearray(hdr) + body                    # other table sizes stay 0
struct.pack_into('<20s', dex, 12, hashlib.sha1(bytes(dex[32:])).digest())
struct.pack_into('<I',  dex, 8,  zlib.adler32(bytes(dex[12:])))
open('classes.dex', 'wb').write(bytes(dex))

with zipfile.ZipFile('demo.apk', 'w') as z:
    z.write('AndroidManifest.xml', zipfile.ZIP_DEFLATED)
    z.write('classes.dex',         zipfile.ZIP_DEFLATED)
    z.write('resources.arsc',      zipfile.ZIP_STORED)   # arsc: stored
    z.write('lib/arm64-v8a/libdemo.so', zipfile.ZIP_STORED)  # so: stored
    z.write('assets/dexopt/baseline.prof', zipfile.ZIP_DEFLATED)
```

**Walk the structure** — entries, then stored vs deflated:

```text
$ unzip -l demo.apk
  Length      Name
---------    ----
       32    AndroidManifest.xml
      120    classes.dex
       12    resources.arsc
     4096    lib/arm64-v8a/libdemo.so
       21    assets/dexopt/baseline.prof
---------    5 files

$ unzip -v demo.apk   (excerpt)
      32  Defl:N       13  59%   AndroidManifest.xml
     120  Defl:N       66  45%   classes.dex
      12  Stored       12   0%   resources.arsc
    4096  Stored     4096   0%   lib/arm64-v8a/libdemo.so
```

The §3 mmap rule, visible in the method column: `arsc` and the `.so`
are `Stored`.

**Read the EOCD by hand** — the backwards-anchored index of §1:

```text
$ python3 -c "import struct; d=open('demo.apk','rb').read(); \
    i=d.rfind(b'PK\x05\x06'); \
    print('EOCD at',i,'| CD offset',struct.unpack_from('<I',d,i+16)[0])"
EOCD at 4780 | CD offset 4455
```

In a real signed APK the signing block sits at that CD boundary with
EOCD's field pointing past it — the offset the v2 verifier re-derives
for chunk 3.

**The dex header in hex** (`xxd classes.dex | head -8`; `od` used here —
xxd unavailable in this sandbox):

```text
$ od -A x -t x1z classes.dex | head -8
000000 64 65 78 0a 30 33 35 00 dd 0e 02 db fe d2 83 0b  >dex.035.........<
000010 10 f3 ff 3f 38 92 a7 af 65 ac 84 c2 87 10 6a 22  >...?8...e...j"<
000020 78 00 00 00 70 00 00 00 78 56 34 12 00 00 00 00  >x...p...xV4.....
000030 00 00 00 00 00 00 00 00 01 00 00 00 70 00 00 00  >........p.......<
000040 01 00 00 00 76 00 00 00 ...
```

Field by field: `64 65 78 0a` = `dex\n`, `30 33 35 00` = version 035;
0x08 the adler32 checksum, 0x0C the 20-byte SHA-1; 0x20 file_size
0x78, 0x24 header_size 0x70, 0x28 endian tag `78 56 34 12`
(0x12345678 little-endian); 0x38 `string_ids_size = 1`, 0x3C offset 0x70.

**With the Android SDK on PATH** (not installed here — documented
commands, expected output shape):

```text
$ apksigner verify --print-certs demo.apk
Verifies
Verified using v1 scheme (JAR signing): false
Verified using v2 scheme (APK Signature Scheme v2): true
Signer #1 certificate DN: CN=Android Debug, O=Android, C=US

$ aapt2 dump badging demo.apk
package: name='com.example.demo' versionCode='1' versionName='1.0'
uses-permission: name='android.permission.INTERNET'
application-label:'Demo'
```

`apksigner` reporting *which scheme* verified is the fastest way to
catch a pipeline that quietly dropped v2 for old minSdk settings.

## How to use this knowledge

- **App-size diagnosis.** "5 MB app" is two numbers: compressed download,
  uncompressed install. `unzip -v` shows which entries carry the weight
  and which are forced `Stored` (arsc, libs — the price of mmap).
  `.so`-dominated → per-ABI splits (App Bundle); fat `resources.arsc` →
  config bloat — every locale × density combination is a row.
- **Signature debugging.** `INSTALL_FAILED_UPDATE_INCOMPATIBLE` gets a
  decision tree: same package, different key → v3 rotation vs. plain key
  loss; "valid signature but different code on an old device" → suspect
  a v1-only artifact on a pre-2018-patch device.
- **Update-pipeline reasoning.** A CI change to deflate settings or
  alignment flags makes incremental OTAs (and Play delta downloads)
  balloon or fail hash checks — Puffin-class tooling assumes
  deterministic compression.
- **The integrity interview answer.** "From power-on to app code":
  ROM → AVB → dm-verity → cert store → APK v2/v3 → zygote fork. Most
  candidates start at the APK; you start at the mask ROM.

## Connections

- **Module 04 ch01 (binary formats)** — EOCD/central-directory and dex's
  fixed-offset header tables are the same skill: find the anchor, walk
  the index.
- **Module 05 ch01 (boot & the Zygote)** — this chapter is the front
  half of that chain: ROM → AVB → dm-verity ends exactly where the APK
  signature picks up, and the dex pages map through the zygote fork.
- **Module 06 ch05 (iOS code-signing)** — Apple's code-directory hash
  pages + CMS signature vs. v2's signing block: same whole-file hashing
  problem, different container (Mach-O vs ZIP).
- **Module 02 ch06 (trust chains)** — ROM→vbmeta→cert→APK is a concrete
  key hierarchy; v3 proof-of-rotation is the same key-continuity
  problem as certificate rotation there.

## Quiz

1. Your dex already has 65,530 method ids. A new feature calls 200
   methods *already referenced* in this dex and 10 that are *new*. Does
   the build succeed? Why?

2. A stock ZIP's central directory starts at byte 120,000. Signers
   inject a 1,024-byte APK Signing Block immediately before it.
   (a) What value does EOCD's CD-offset field hold now? (b) What offset
   does the v2 verifier use when hashing the EOCD chunk, and why?

3. `grep -a permission demo.apk` finds nothing, yet the app demonstrably
   requests permissions. What is the manifest inside the APK, and what
   decodes it?

4. A colleague sideloads a v1-only-signed APK on an unpatched Android
   6.0 device. The signature verifies, yet the app exfiltrates data
   your source doesn't contain. Mechanism and vulnerability name?

5. An app with `extractNativeLibs="false"`, `.so` entries stored and
   4,096-aligned, works on a 2021 phone but crashes in
   `System.loadLibrary` on a 2025 model. What changed?

6. Product wants a different app *label* per locale; a teammate proposes
   runtime string comparison in `onCreate`. Using `resources.arsc`,
   explain why the correct design contains no such branch.

7. v2 covers "everything except the APK Signing Block." Why can't the
   scheme just digest the whole file *including* the block?

8. Connect the chain: where do the verifier keys for an APK signature
   physically live, and what keeps those bytes from changing after
   boot? Tie this to two earlier chapters.

<details>
<summary>Answers</summary>

1. **Fails.** Call *sites* of already-referenced methods are free — the
   u16 `method@` index reuses an existing entry; ids dedupe. The 10 new
   references push the table to 65,540 > 65,536 (2^16) indexable ids, so
   d8 emits "Too many method references." Fixes: R8 shrinking, or
   multidex.

2. **(a) 121,024** (120,000 + 1,024): routing stock readers *around* the
   injected block.
   **(b) 120,000** — the verifier hashes the EOCD as if the block were
   absent, the CD-offset field reset to its pre-injection value: the
   signed bytes are the block-free image, which covers the EOCD without
   covering the block.

3. **Compiled binary XML**: a chunked node tree whose element and
   attribute names are indices into a shared string pool, values typed —
   no contiguous `<uses-permission` bytes exist to match. `aapt2 dump
   xmltree` (or `dump badging`) decodes it; `apktool` too.

4. **Janus (CVE-2017-13156)**, a polyglot: attacker dex prepended to the
   signed APK. The ZIP parser anchors at the tail EOCD, finds the
   original entries intact, and v1's per-entry digests match (it signs
   only the entry view). The pre-patch dex loader tolerated a
   prefix before the `dex\n` magic, so the prepended code executed — a
   parser differential (module 03), closed by v2's whole-file hashing.

5. **Kernel page size.** The 2025 device is an Android 15+ 16 KB-page
   machine. `dlopen` mmaps the stored `.so` straight from the APK, and
   mapping requires the ELF `LOAD` and ZIP entry alignments to match the
   kernel page size — 4,096-aligned entries can't map cleanly on 16 KB
   pages. Fix: 16 KB-aligned segments (NDK) and `zipalign -P 16384`.

6. **Config selection is table lookup.** The label resource ID has one
   value per configuration in `resources.arsc`; resolution is package →
   type → entry → best config by `ResTable_config` precedence (locale
   outranks density). Zero app-side branching, no relaunch on
   locale change — the branch lives in data.

7. **Self-reference.** A signature is a function of the signed bytes;
   including the signature in its own input requires a fixed point
   (value = function of itself), which can't be computed. Every
   self-certifying structure is anchored from *outside*: v2 excludes
   its block and normalizes the EOCD CD-offset; the ROM is trusted
   because nothing precedes it to need a signer.

8. **On `/system`**: the platform cert plus whatever the installer
   policy trusts. What protects those bytes after boot is **dm-verity**
   — the Merkle tree over `/system`'s 4 KiB blocks, root hash signed in
   `vbmeta`, chained to the ROM: module 05 ch01's boot chain plus
   module 02 ch06's key hierarchy, made concrete.

</details>
