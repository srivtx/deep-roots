# Chapter 01 — The z-machine

> **The 30-second version.** A mainframe is not a fast PC; it is a machine
> whose design *objective* is uninterrupted service, paid for in hardware:
> frames of drawers, hot-spare processors, RAID-like memory, a service
> network that orders its own parts. On that metal sits z/Architecture:
> 16 general registers; three addressing modes whose boundaries — the 16 MB
> *line* and the 2 GB *bar* — still decide where every COBOL byte may live;
> the Program Status Word, encoding condition code, addressing mode,
> instruction address, interruption masks, and the protection key insiders
> read first in any dump; 4-bit storage keys per 4 KB page that protected
> memory before virtual memory; and EBCDIC, a punch-card alphabet that is
> not byte-compatible with ASCII — the landmine under every "just convert
> it" migration.

## A machine whose objective is uptime

Start with the objective; every strange fact here follows from it. Consumer
hardware optimizes cost per performance unit — a part fails, you replace it
and eat the downtime. Mainframe hardware optimizes *availability* and treats
failure as an engineering constant, not an accident. An IBM z16 is one or
more refrigerator-sized cabinets — *frames* — holding processor drawers (CPC
drawers: cores plus memory) and I/O drawers. The redundancy is layered:

- **Hot-spare CPUs.** The machine carries spare processor units (PUs) that do
  nothing until a core fails; a transient fault is first *retried*
  (checkpoint, re-execute), and a core that keeps failing is fenced off
  while firmware configures a spare into the partition in seconds. The
  folklore of every z shop: the machine *calls home* — IBM's Electronic
  Service Agent opens the ticket automatically — so the first human to learn
  your CPU failed is often the IBM technician arriving with the part.
  Folklore, but folklore about a documented mechanism.
- **RAID-like memory.** Since z13 (2015), memory is protected by RAIM — a
  redundant array of independent memory: spare bits recover a failing chip, a
  hot-spare DIMM replaces a failing one, partitions keep running. Add
  redundant clocks, dual power feeds, concurrent maintenance (swap a drawer
  while LPARs run), and the uptime folklore — systems measured in *years* —
  becomes accounting: failures happen; they never reach the workload.
  Reliability is the *objective*, not the by-product.

## Sixteen registers and three sizes of address

z/Architecture gives a program **16 general registers**, R0–R15: 32 bits
wide in 24- and 31-bit addressing modes, full 64-bit registers in 64-bit
mode. Sixteen — not eight, not thirty-two — is a 1964 System/360 decision
that never changed, and your COBOL compiler lives inside it: `CALL ... BY
REFERENCE` passes parameter addresses in registers (module 01 ch17's
72-byte save area rides the same convention). Insider footnote: R0 cannot
be a base register in 24/31-bit address arithmetic — base 0 means "no
base".

Registers hold addresses, and addresses come in three sizes — the most
consequential fact in this module:

```text
 One z/Architecture address space: which code can address which bytes

 0xFFFFFFFFFFFFFFFF +------------------------------------------------+
                     |  AMODE 64 only — "above the bar"               |
                     |  64-bit registers and instructions; big        |
                     |  buffers. A 31-bit program cannot even NAME    |
                     |  these addresses: its effective addresses      |
                     |  ignore bits 32-63.                            |
                     |                                                |
 0x0000000080000000 |================ THE 2GB BAR ==================| <- 2^31
                     |  AMODE 31 — "below the bar" (0 .. 2GB-1)      |
                     |  31-bit registers; where z/OS itself and     |
                     |  nearly all COBOL programs live today        |
                     |                                                |
 0x0000000010000000 |- - - - - - - - THE 16MB LINE - - - - - - - - -| <- 2^24
                     |  AMODE 24 — 0 .. 16MB-1                       |
                     |  S/360 (1964) heritage: RMODE 24 modules,    |
                     |  common areas 24-bit code can address       |
                     |                                                |
 0x0000000000000000 +------------------------------------------------+  PSA at 0
```

**Derive the line's persistence.** System/360 (1964) chose 24-bit addresses;
16 MB seemed infinite. By the late 1970s it wasn't, and 370-XA (1983)
extended to 31 bits — 2 GB. But old 24-bit binaries had to keep running, so
programs acquired attributes they still carry: **AMODE** (which address
sizes their instructions generate: 24, 31, 64) and **RMODE** (where the
module must load: 24 = below the line, ANY = anywhere below the bar). A
31-bit program calls a 24-bit program only through mode-switching stubs;
storage both must share — z/OS common areas like CSA — had to stay below the
line, *consuming* the bytes the extension was meant to add. Converting the
installed base took a decade; some code never moved. When z/Architecture
(z900, 2000) extended to 64 bits, the lesson was applied: 31-bit mode kept
intact as "below the bar," and 64-bit is a separate *execution mode*, not
merely bigger addresses.

## The PSW: the register you read first

The Program Status Word is the mother of all registers: program counter,
flags, and privilege/protection state in one doubleword (two in 64-bit mode;
z/Architecture, current through z16). Fields that matter here (24/31-bit
format):

```text
  64-bit PSW, 31-bit mode (z/Architecture; bits numbered from 0, MSB first)

  bit  0     PER mask               (1 = debug trapping on)
  bit  5     DAT mode               (1 = addresses go through translation)
  bits 6-7   I/O and external interruption masks (are interrupts delivered?)
  bits 8-11  KEY                    <- the storage-protection key, below
  bit  13    machine-check mask     bit 14 WAIT   bit 15 PROBLEM state
  bits 16-17 address-space control  (primary / AR / secondary / home)
  bits 18-19 CONDITION CODE         bits 20-23 program mask (overflow traps)
  bit  32    addressing mode: 0 = 24-bit, 1 = 31-bit (bit 31 = 1: 64-bit)
  bits 33-63 INSTRUCTION ADDRESS    (bits 40-63 in 24-bit mode; 64-bit mode
                                     doubles the PSW and extends it to bit 127)
```

Why insiders read it first: before touching a register you learn what kind
of failure you have — problem or supervisor state, virtual or real storage,
24/31/64-bit code, which storage key was in force, where the failing
instruction was. And — the surprise — after a program interruption, bits
16-31 of the old PSW are overwritten with the 16-bit **interruption code**:
`0004` = protection (your S0C4), `0005` = addressing (wild address, no
translation), `0006` = specification. The dump's "PSW AT TIME OF ERROR" is
the state frozen at the fault, abend code included — and the slots those old
PSWs occupy, at the bottom of the prefix area, are a 1964 layout z16 still
honors.

## Storage keys: protection from 1964, still firing in 2024

Virtual memory arrived here in the early 1970s. Memory *protection* is
older: System/360 (1964) attached a **storage key** to every block of
absolute storage — 2 KB blocks then, 4 KB frames now. A key is 7 bits: a
4-bit **access key**, a fetch bit, plus reference and change bits the page
stealer uses to find victims.

The rule is one sentence: *every storage access carries the PSW's key (bits
8-11), and it must equal the page's key — or either must be zero.* Key 0 is
the master key: supervisor-state z/OS runs key 0 and bypasses checking. No
page tables, no virtual addresses — the check applies to the **absolute
frame** the access lands on, after any translation, per instruction, for
sixty years. Storage keys predate DAT and stayed underneath it; they are
active on every z16 today.

Here is the CICS story chapter 04 completes. A CICS region is one address
space multiplexing thousands of transactions; for most of its history
everything in it ran PSW key 8, so a wild write corrupted whatever it hit —
silently. Enable CICS storage protection and the region splits storage by
key: CICS's own storage key 8, task storage GETMAIN'd key 9. Now the same
wild write — a program at PSW key 9 storing into a frame keyed 8 — is
stopped *by the hardware* before a byte lands: interruption code 4, z/OS
abend S0C4, CICS reporting a storage violation. Most S0C4s inside CICS are
not missing pages; they are **key mismatches** the PSW names for you.

## One machine, many spaces — and the LPAR lineage

An address space is what DAT translates: primary, secondary, home, selected
by PSW bits 16-17. A **dataspace** is up to 2 GB of real storage attached to
an address space outside its normal translation — a scratch arena for big
tables, shareable between spaces. **Cross-memory** access is how a program
reaches *another* address space: the 16 access registers AR0–AR15 hold ALET
tokens selecting a space (address spaces are numbered — ASNs — and
authorized per program), the PSW's address-space control switches to
access-register mode, and data references follow the AR instead of the
primary space.

Zoom out and the same idea is in hardware. PR/SM (1988) runs several
**LPARs** — logical partitions — on one physical machine, the hypervisor in
firmware partitioning processors, memory, and I/O with hard isolation. z/VM,
a 1972 descendant of the 1967 CP/CMS systems, virtualizes the whole machine
and can run inside an LPAR. IBM partitioned hardware before anyone
virtualized it: VMware's hypervisor (2001) and the cgroups/namespaces
containers of module 07 re-invented the LPAR-vs-z/VM split decades later.

## EBCDIC: the alphabet under your records

COBOL bytes on z/OS are EBCDIC, and EBCDIC is not "ASCII with different
values" — it is a different *history*. Derive it from the 80-column punch
card: a character was one zone punch (rows 12, 11, or 0) plus one digit
punch (rows 1-9), and an EBCDIC byte *is* that column: high nibble = zone
(12 → `C`, 11 → `B`, 0 → `A`, digits-only → `F`), low nibble = digit row.
`A` is the 12-1 punch, so `'A' = 0xC1`; digits carry no zone, `'0'..'9' =
0xF0..0xF9`. The consequences are byte-level, which is where COBOL lives:

- **Not byte-compatible with ASCII.** `A` is 0x41 in ASCII, 0xC1 in EBCDIC.
  Code comparing raw bytes, hashing records, or sorting by byte order
  silently changes behavior across a conversion.
- **Different sort order, with holes.** EBCDIC digits (0xF0...) sort *above*
  letters; lowercase occupies 0x81... with gaps (0x89 jumps to 0x91).
- **Code pages, plural.** No single EBCDIC: US text is code page 037,
  international 500, plus national variants — even EBCDIC-to-EBCDIC needs a
  translation table, and EBCDIC's pages disagree about *letters*, not
  accents.
- **PICTURE fields are byte contracts.** `PIC X(10)` is 10 bytes; `PIC S9(5)
  COMP-3` is 3 bytes of packed nibbles (module 08 ch03). "Just convert the
  file to UTF-8" treats every byte as character data: COMP-3 and binary
  fields are destroyed outright, non-ASCII characters expand to 2-4 UTF-8
  bytes and shift every offset after them, and even text fields survive only
  in *length* — values change, and with them every comparison above. (Zoned
  decimal keeps its sign in the zone nibble of the last byte — `C`=+,
  `D`=−, `F`=unsigned — so a signed `5` is 0xC5, which an EBCDIC text viewer
  displays as `E`.)

Chapter 05 prices this landmine; the point here is that on this machine,
*the alphabet is part of the memory model*.

## What insiders know

- **A dump's PSW decodes in eight bytes**: bits 8-11 key, bit 15 problem
  state, bit 32 addressing mode (24 vs 31; bit 31 set = 64-bit), bits 33-63
  failing instruction address, and bits 16-31 of the *old* PSW hold the
  interruption code — `0004` there is literally your S0C4.
- **S0C4 vs S0C5 is a diagnosis, not a detail**: 4 = protection (frame
  exists, key mismatch — think CICS key 8/9); 5 = addressing (no valid
  translation — wild pointer). One hex digit separates "wrong key" from
  "no such page."
- **Storage keys check the absolute frame, after DAT, per access**: 4-bit
  key + fetch + reference + change bits per 4 KB frame (2 KB blocks in the
  S/370 era); the reference/change bits rank the page stealer's victims.
- **Hot-spare PUs and RAIM are the availability numbers**: a failing core is
  fenced and a spare configured in within seconds; memory since z13 (2015)
  recovers a failing chip and swaps DIMMs concurrently; "the machine called
  IBM before we noticed" is folklore *about a documented mechanism*.
- **The CICS S0C4 trap**: with storage protection on, task code at PSW key 9
  writing CICS key 8 storage faults immediately — the abend is the safety net
  *working*. The bug it reports usually ran silently for years with
  protection off, which is why "it worked in test" means little there
  (chapter 04).
- **The EBCDIC conversion trap bites at three levels**: byte values (0xC1 vs
  0x41) break comparisons and hashing; sort order (digits above letters)
  reorders output; and UTF-8 expansion shifts offsets while "text"
  conversion destroys COMP-3/COMP fields outright.
- **AMODE truncation fails silently**: an AMODE 24 program handed 0x10284000
  accesses 0x00284000 — no trap, wrong bytes. Check the module's
  AMODE/RMODE before blaming its data.

## See it yourself

z/Architecture hardware is not rentable by the hour; Labs 1-2 are documented
walkthroughs (decode on paper), Lab 3 runs on any laptop.

**Lab 1 — decode a PSW from a dump excerpt.** Arrangement as an SVC dump
prints it; values constructed for decoding practice:

```text
IEA995I SYMPTOM DUMP OUTPUT 1
SYSTEM COMPLETION CODE=0C4    REASON CODE=00000004
 PSW AT TIME OF ERROR  078D0004 8123456A   ILC 02  INTC 0004
 ...
```

Decode `078D0004 8123456A` against the PSW map, nibble by nibble:

- byte 0 = `07` = 0000 0111: DAT on (bit 5), I/O and external masks on,
  PER off — an enabled problem program.
- byte 1 = `8D` = 1000 1101: **bits 8-11 = 1000 → key 8**; bit 13
  machine-check mask on; bit 14 wait=0; **bit 15 problem state**.
- bits 16-31 = `0004`: the interruption code — protection exception, the 0C4
  itself, frozen into the old PSW.
- bit 32 = 1 (top bit of `81...`): **31-bit mode**; bits 33-63 = `0123456A`,
  the failing instruction's address — above 0x1000000, unreachable by 24-bit
  code.

**Lab 2 — walk the key-matching failure.** CICS with storage protection:
CICS frames keyed 8, task storage keyed 9, your transaction's PSW key = 9.
Trace `MOVE SPACES TO LINKAGE-ITEM` where LINKAGE-ITEM resolves into
CICS-key storage (mangled pointer — chapter 02's classic): the store carries
PSW key 9, the frame's key is 8, 9 ≠ 8 and neither is 0 → interruption code
4 → z/OS S0C4, CICS calls it a storage violation. Re-run with protection off
(all key 8): keys match, the store lands, and the corruption surfaces later
in an unrelated transaction — the reason the abend is your friend.

**Lab 3 — verify EBCDIC bytes with Python** (`cp037` = US EBCDIC):

```console
$ python3 -c "print(bytes.fromhex('C1 C2 C3').decode('cp037'))"
ABC
$ python3 -c "print('012'.encode('cp037').hex(' '))"
f0 f1 f2
$ python3 -c "print(bytes.fromhex('F0 F1 F2 C5').decode('cp037'))"
012E
```

The last line is the zoned-decimal gag: `F0 F1 F2 C5` is the record the
compiler sees as `+0125` — digit 5 with a `C` (positive) sign nibble — while
the character decoder sees the letter `E`, because 0xC5 *is* `E` in EBCDIC.

## How to use this knowledge

- **Triage an S0C4 in one minute**: PSW first — key, state, AMODE, failing
  address, interruption code. Key mismatch with a CICS region in the picture
  → dirty storage (ch04); addressing code 5 → wild pointer. You will classify
  80% of storage abends before opening a trace.
- **Read a mainframe job posting without flinching**: above/below the line,
  AMODE/RMODE 24/31/64, key 8/9, GETMAIN, cross-memory — allocation
  constraint, protection, or history you must not break, decoded on sight.
- **Scope a migration honestly** (ch05 preview): against "convert the files
  to UTF-8," name the three failure modes — packed/binary fields destroyed,
  offsets shifted by UTF-8 expansion, sort semantics changed — and price a
  byte-preserving extract pipeline instead.
- **Answer the availability interview question with mechanisms**: spare PUs
  configured in on failure, RAIM memory, concurrent maintenance, call-home
  ticketing — reliability as design *objective*, the consumer PC's objective
  stood on its head.

## Connections

- **Module 01 ch01 (memory from silicon)** — the hierarchy with redundancy as
  the objective: RAIM and spare PUs are the same physics, bought differently.
- **Module 01 ch03 (virtual memory)** — DAT vs storage keys: page-table
  permissions in the virtual world, key matching on absolute frames beneath
  it — one system from the 1970s, one from 1964, both still load-bearing.
- **Module 01 ch17 (COBOL's memory model)** — the metal under that chapter's
  "completed contract": the addresses the compiler never shows you, the line
  and bar they live under, the 4-bit check under every field.
- **Module 08 ch02-05** — ch02: which storage section lands where relative to
  the line; ch03: PICTURE byte arithmetic makes the EBCDIC trap concrete;
  ch04: the CICS key 8/9 story in full; ch05: the migration economics these
  landmines justify.
- **Module 07 (the systems you know)** — LPARs and z/VM are the ancestors of
  your hypervisors and containers; the old-PSW slots at the bottom of
  storage are the elder sibling of the x86 IDT.

## Quiz

1. *(concept)* A z16 can name 2^64 addresses. From what had to keep working
   in 1983, explain why the 16 MB line still exists — and why "just use
   64-bit mode for everything" was not, and is not, an option.
2. *(predict the decode)* An SVC dump shows `PSW AT TIME OF ERROR 079D0005
   80C31F90`. Give the storage key in force, problem or supervisor state, the
   addressing mode, the failing instruction address (above or below the 16 MB
   line?), the interruption code, and the z/OS abend it produces.
3. *(predict the numbers)* A record is `PIC X(8)` (DISPLAY), then `PIC S9(5)
   COMP-3`, then `PIC S9(4) COMP`. Compute the record's size in bytes, then
   predict what a naive "convert the file to UTF-8" pipeline leaves of each
   field.
4. *(bug diagnosis)* A CICS transaction abends S0C4; the PSW at time of error
   shows key 9, problem state, 31-bit mode; the region runs storage
   protection. Explain the exact mechanism — which two keys, compared where,
   against what — and why the program ran ten years without abending before
   protection was enabled.
5. *(bug diagnosis)* An AMODE 24 program is passed the address 0x10284000 and
   stores to it. No abend occurs. What address was actually written, and why
   did the hardware stay silent?
6. *(bug diagnosis)* After EBCDIC records are converted to ASCII, a report
   sorts differently: digit-initial records moved relative to letter-initial
   ones, and some lowercase ranges interleave differently. Give two
   byte-level reasons.
7. *(concept)* Storage keys predate virtual memory here. State what a storage
   key is attached to, when the comparison happens relative to address
   translation, and the exact rule — including the two values that bypass it.
8. *(cross-module, module 01 ch03 + ch17)* Compare a page table's protection
   with storage keys, and name which mechanism actually keeps two CICS tasks
   in one address space from corrupting each other.

<details>
<summary>Answers</summary>

1. **The line is a compatibility debt.** 370-XA (1983) had to keep
   1964-vintage binaries running while widening addresses 24→31 bits, so
   programs gained AMODE (address size their instructions generate) and RMODE
   (where they must load). Storage shared with 24-bit code — z/OS common
   areas like CSA — had to stay below the line, consuming the bytes the
   extension added; converting the installed base took a decade, and some
   code never moved. "Just use 64-bit" fails for the same reason at the next
   boundary: 64-bit is a different *execution mode*, and the 31-bit world was
   never wholesale converted.
2. **Key 9, problem state, 31-bit, below the line, code 5 → S0C5.** Byte 1 =
   `9D` = 1001 1101: bits 8-11 = 1001 = key 9; bit 15 = 1, problem state.
   Bit 32 = 1 (top bit of `80...`) → 31-bit mode; bits 33-63 = `0C31F90` =
   12,794,256, below 16,777,216 (0x1000000) — an AMODE 31 program loaded
   below the line. Bits 16-31 = `0005` → addressing exception, no valid
   translation → abend S0C5: a wild pointer, not a key mismatch.
3. **13 bytes; one field survives, two die.** `X(8)` = 8 bytes; `S9(5)
   COMP-3` = floor(5/2)+1 = 3 bytes; `S9(4) COMP` = 2 bytes (1-4 digits →
   halfword). Under naive UTF-8 conversion the DISPLAY field survives *only
   if* pure ASCII-range characters — values change (0xC1→0x41) but length
   holds; anything outside ASCII expands and shifts every later offset.
   COMP-3 (packed digit pairs plus a sign nibble) and the raw-binary COMP
   halfword are destroyed outright.
4. **Key 9 met key 8, at the frame, after translation.** With storage
   protection, CICS storage is GETMAIN'd key 8, task storage key 9; the
   transaction runs at PSW key 9 (bits 8-11). The wild store's effective
   address translated through DAT to an absolute frame, and the hardware
   compared PSW key 9 with the frame's storage key 8: unequal, neither 0, so
   the store was suppressed and interruption code 4 raised → S0C4. For the
   prior ten years everything ran key 8: the same store *succeeded*, and
   the corruption surfaced later, elsewhere. The abend is the safety net
   firing; the bug predates it.
5. **0x00284000, silently.** In 24-bit mode, effective-address formation uses
   bits 0-23 of the base-plus-displacement sum; bits 24-31 of 0x10284000 are
   dropped, so the store lands at 0x00284000 — wrong place, no interruption,
   because the page is mapped and the keys match. The fix is the module's
   AMODE/RMODE, not its data.
6. **Two orderings, both byte-level.** (1) EBCDIC digits are 0xF0-0xF9, above
   every letter; ASCII digits are 0x30-0x39, below all letters —
   digit-initial records jump from the end of the collating order to the
   front. (2) EBCDIC lowercase has internal gaps (0x89 → 0x91) with specials
   interleaved; against ASCII's contiguous a-z, byte-order comparison yields
   a different sequence, and any memcmp/hash on the old bytes no longer
   matches.
7. **Attached to the absolute frame, checked after translation, equality or
   zero.** A storage key (4-bit access key, fetch bit, reference and change
   bits) belongs to each 4 KB frame of *absolute* storage (2 KB blocks in the
   S/370 era) — it predates translation, which is why it attaches to
   physical frames, not virtual pages. The access key in effect (PSW bits
   8-11, or an access-register key) is compared once the translated address
   selects the frame: a store requires access key = storage key; a fetch
   too, unless the frame's fetch bit is set. Bypasses: access key 0 (master
   key) and storage key 0.
8. **Two systems; CICS leans on the older one.** Page-table protection is
   per-*virtual* page, consulted during DAT per address space; storage keys
   are per-*absolute* frame, compared against the PSW's key after
   translation regardless of which address space made the access. Module 01
   ch17's contract (fixed layouts, no pointers) *avoids* the pointer bugs
   page protection exists for; what keeps two CICS tasks honest in one
   address space is the key mechanism — keys 8 vs 9, hardware-checked per
   access — still the safety net under the 1969 transaction monitor
   (chapter 04).

</details>
