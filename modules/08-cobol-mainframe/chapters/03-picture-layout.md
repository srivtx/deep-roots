# Chapter 03 — PICTURE & layout arithmetic

> **The 30-second version.** Every PICTURE clause is a purchase order for bytes, signed
> at compile time: DISPLAY buys one byte per character; COMP buys 2, 4, or 8 bytes by a
> derivable grid; COMP-3 buys `digits / 2 + 1` bytes of packed decimal with a sign
> nibble; REDEFINES buys nothing — it renames bytes already bought; OCCURS multiplies
> the bill; 88-levels cost zero. You will compute every offset and size by hand, then
> prove it with a program that prints addresses.

## A type system that bills by the byte

COBOL 1959 predates hidden layouts: the declaration *is* the memory map, and every
PICTURE symbol has a byte price. `PIC 9(8)` is not a format string; it is a contract
for exactly 8 bytes, fixed at compile time. Two symbols cost nothing: `V` (the implied
point in `S9(3)V9`) exists only in the compiler's arithmetic — a rule for interpreting
digits, never a byte — and `S` rides in a byte you already paid for. So `PIC S9(3)V9`
is 4 digits, 4 bytes, point included in neither.

Default `USAGE IS DISPLAY` buys one byte per character; on z/OS the characters are
EBCDIC, digits `0xF0`–`0xF9` — a **zone** nibble `F` over the digit. A signed DISPLAY
number overpunches the sign into the zone of the *last* byte:

```text
PIC S999 DISPLAY, value -123, z/OS EBCDIC:

   byte 0     byte 1     byte 2
  +---------+ +---------+ +---------+
  | 1 | 0 | | | 2 | 0 | | | 3 | 1 | |   nibble pairs: zone / digit
  +---------+ +---------+ +---------+
    F   1       F   2       D   3     <- last byte's zone IS the sign

  sign nibbles:  C = +,  D = - (debit),  F = unsigned
  +123 = F1 F2 C3    -123 = F1 F2 D3    unsigned 123 = F1 F2 F3
```

Read `F1 F2 C3` as a raw integer and you get 0xF1F2C3 = 15,856,323, not 123: the zones
are pollution. So `ADD`/`COMPUTE` on zoned data must strip zones and decode the sign
first — on z/OS, `PACK` in, packed arithmetic, `ED`/`EDMK` out — a hidden translation
tax that makes DISPLAY the slowest numeric usage. Bypass the compiler (raw bytes
through a REDEFINES) and the abend is `S0C7`, *data exception*: a decimal instruction
handed a non-digit.

## The binary packing grid

`COMP` (BINARY) stores a base-2 integer. The rule everyone memorizes — 1–4 digits →
2 bytes, 5–9 → 4, 10–18 → 8 — is the smallest binary size that *covers* the decimal
digit count, top bit reserved for `S`:

| PICTURE digits | COMP bytes | Why |
|---|---|---|
| 1–4 | 2 | 2^15 − 1 = 32,767 covers 9,999; it does not cover 99,999 |
| 5–9 | 4 | 2^31 − 1 = 2,147,483,647 covers 999,999,999; not 9,999,999,999 |
| 10–18 | 8 | 2^63 − 1 ≈ 9.22 × 10^18 covers 10^18 − 1 |

A minimal-covering grid, nothing arbitrary. z/OS Enterprise COBOL 6.x and GnuCOBOL 3.2
(x86-64 Linux, default `binary-size 1-2-4-8`) agree on all three rows; below 3 digits
they diverge, GnuCOBOL's default spending 1 byte on a 1–2-digit item.

Binary arithmetic is native — one instruction — but alignment costs. `SYNC` asks for
the item's natural boundary (halfword/fullword/doubleword for 2/4/8 bytes), and z/OS
honors it by inserting **slack bytes** *before* the item, which count toward the
group's size:

```cobol
05  HDR-TAG  PIC X(3).              *> offset 0, 3 bytes
05  HDR-NUM  PIC S9(9) COMP SYNC.   *> offset 3 -> 1 slack byte -> offset 4, 4 bytes
*> group = 8 bytes: one byte exists only because of SYNC, invisible in source
```

That pad byte is the classic hand-computation error: your table says 3, the machine
says 4. z/OS always pads; x86 builds may skip it (unaligned access is legal there) —
same copybook, two layouts. The lab measures what your compiler did.

## Two digits per byte: why money is packed

A digit 0–9 needs 4 bits — half a byte — and **COMP-3** (packed decimal) spends both
halves: two digits per byte plus the sign — same C/D/F conventions — in the final
nibble:

```text
PIC S9(7) COMP-3, value +0123456, exactly 4 bytes:

 +--------+ +--------+ +--------+ +--------+
 | 0 | 1 | | 2 | 3 | | 4 | 5 | | 6 | C |   7 digits + 1 sign nibble
 +--------+ +--------+ +--------+ +--------+   = 8 nibbles = 4 bytes exactly

 -0123456 ends ...6D;  unsigned 0123456 ends ...6F
```

The formula falls out: *d* digits + one sign nibble = *d* + 1 nibbles, so bytes =
`floor(d / 2) + 1`. `S9(9)V99 COMP-3` has 11 digits (`V` free): `floor(11/2) + 1 = 6`
bytes for what zoned spells across 11.

Why did banking standardize on this and never leave? COMP-3 is decimal, not
binary-fractional. Module 03 chapter 04 showed `0.1 + 0.2 == 0.3` is `False` in every
IEEE-754 float64 — 0.1 is an infinite binary fraction — while a `V99` field stores
exact cents that add to the penny across decades of nightly batch. COBOL was specified
in 1959 with decimal as a *native* type — the float money bug class was solved before
computing had a name for it — and S/360 (1964) shipped the hardware (`ZAP`/`AP`/`SP`/
`CP`, still executing on z/Architecture). 11 digits in 6 bytes is also near half the
storage and I/O of zoned at billions of records.

`COMP-1`/`COMP-2` are native float — 4 and 8 bytes — rare in banking for that drift
reason, and a portability landmine: z/OS formats them as hexadecimal float (HFP),
GnuCOBOL/x86 as IEEE-754 — same PICTURE, *different bits*. Rule zero: floats never ride
in legacy record layouts.

## REDEFINES: two names for the same bytes

One address, two interpretations — a union, except COBOL had it in 1959 and no tag bit
ever accompanies it:

```cobol
05  TXN-AMT    PIC S9(9)V99 COMP-3.        *> 6 bytes at offset 20
05  TXN-AMT-X  REDEFINES TXN-AMT PIC X(6). *> the SAME 6 bytes, a byte view
```

The redefining item starts at the subject's offset and does **not** advance the record
— no bytes are bought. Rules: no `VALUE` on the redefining item (`88`-levels under it
are the one exception); below 01 it may not be *longer* than its subject; no
variable-size (ODO) items may participate — the window must be fixed; only condition
names and FILLER may sit between subject and redefiner.

The insight: which view is *live* — amount or message? — exists nowhere in storage,
only in the code paths that touched it. Forty years of REDEFINES leaves data with no
self-describing schema — every byte's meaning lives in code and microfiche. Automated
translation cannot recover which view was live: the root of chapter 05's migration
trap.

## OCCURS, the moving wall, and the 88 that owns no bytes

`OCCURS n TIMES` is a fixed table: element size × count, billed once at compile time.
`X(2) OCCURS 5` is a 10-byte block with 2-byte stride — `TXN-CODES(3)` is one address
add at run time, zero allocation, zero pointer chasing.

`OCCURS ... DEPENDING ON` (ODO) is the variable-length record mechanism: the count is
*read from a field in the same record* —

```cobol
01  BATCH-RECORD.
    05  BATCH-ID      PIC 9(6).        *> offset 0, 6 bytes
    05  BATCH-CNT     PIC 9(3).        *> offset 6, 3 bytes - the count lives IN the data
    05  BATCH-ENTRY   PIC X(40) OCCURS 1 TO 250 TIMES
                    DEPENDING ON BATCH-CNT.   *> offset 9, 40 x cnt bytes
    05  BATCH-TRAILER PIC X(8).        *> offset 9 + 40*cnt - THE TRAILER MOVES
```

Everything *after* an ODO table shifts with the count: `BATCH-CNT = 3` puts the trailer
at 9 + 120 = 129 and the record at 137 bytes; 7 puts them at 289 and 297. The count is
a serialization header — the record describes its own length, 1959-style.

But the wall moves only because the program moves it. WORKING-STORAGE allocates the
*maximum* — 10,017 bytes regardless of the count — and nothing enforces `cnt ≤ 250`:
subscript checking is off by default on z/OS, and counts arrive from untrusted input.
`cnt = 251` walks entries past the record into the next WORKING-STORAGE item — module
01 chapter 08's unchecked array, reborn with opt-in guardrails. The file side is
equally manual: the FD must say `RECORD VARYING`, and the WRITE length is what you
compute. Every length check around ODO is hand-written — the overflow surface of the
mainframe world.

Last clause, zero bytes: the 88-level condition name.

```cobol
05  TXN-STATUS  PIC X.
    88  STATUS-OK       VALUE "0".
    88  STATUS-DISPUTED VALUE "1" "D".
```

`IF STATUS-DISPUTED` compiles to a comparison of `TXN-STATUS` against those literals —
no storage, no dispatch. An 88 adds zero bytes (module 08 chapter 02 proved it with
addresses): the poor man's enum, and the only place a name and value can legally meet
inside a union.

## What insiders know

- **The COMP grid is a minimal-covering grid**: 2 bytes because 9,999 < 32,767 =
  2^15 − 1; 4 because 999,999,999 < 2^31 − 1; 8 because 10^18 − 1 < 2^63 − 1. Deriving
  it live is the fastest credibility signal in a legacy interview.
- **Zoned sign nibbles on sight**: C = plus, D = minus, F = unsigned — and F is *not*
  zero; comparisons treat it as positive. GnuCOBOL 3.x ASCII builds mirror the scheme
  with zones 3/7: `−123` in `PIC S999` is hex `31 32 73` — why ASCII dumps of
  negative data look like typos.
- **The REDEFINES VALUE rule**: no VALUE on the redefining item (88s excepted), and
  below 01 no redefiner longer than its subject — one of the few spots COBOL
  hard-rejects at compile time instead of corrupting at run time.
- **ODO shifts, storage does not**: the area is allocated at the declared maximum
  (10,017 bytes in the lab record) while the *record* is only as long as the count says;
  `FUNCTION LENGTH` of an ODO group returns different things on different compilers —
  why production code tracks record length by hand.
- **Why banking loves COMP-3, four countable ways**: exact decimal arithmetic (no
  0.1 + 0.2 drift), 2 digits per byte ≈ half the I/O of zoned, one-instruction (`UNPK`)
  conversion to display, and native hardware (`AP`/`ZAP`) since S/360 in 1964. `V99`
  is the industry's default money scale.

## See it yourself

The core skill: a 15-field record, every offset and size by hand, then verified with
GnuCOBOL — rules applied visibly, offsets accumulated left to right, REDEFINES sharing
an offset without advancing:

```text
byte   0 +------------+ TXN-ID      9(8)   =  8 B                  [0-7]
byte   8 +------------+ TXN-ACCT    9(10)  = 10 B                 8+10=18
byte  18 +------------+ TXN-TYPE    X(2)   =  2 B                18+2=20
byte  20 +------------+ TXN-AMT     S9(9)V99 COMP-3: floor(11/2)+1 = 6 B
  (same 20)            TXN-AMT-X    REDEFINES: 0 new bytes, no advance
byte  26 +------------+ TXN-BAL     S9(11)V99 COMP-3: floor(13/2)+1 = 7 B
byte  33 +------------+ TXN-FEE     S9(3)V9 COMP-3: floor(4/2)+1 = 3 B
byte  36 +------------+ TXN-SEQ     S9(4)  COMP: 1-4 digits -> 2 B
byte  38 +------------+ TXN-BRANCH  S9(5)  COMP: 5-9 digits -> 4 B
byte  42 +------------+ TXN-CNT     S9(9)  COMP: 5-9 digits -> 4 B
byte  46 +------------+ TXN-STATUS  X(1)   =  1 B                 46+1=47
byte  47 +------------+ TXN-ENTRY   S9(4)V999 COMP-3: floor(7/2)+1 = 4 B
byte  51 +------------+ TXN-DATE    9(8)   =  8 B                 51+8=59
byte  59 +------------+ TXN-DESC    X(12)  = 12 B                59+12=71
byte  71 +------------+ TXN-CODES   X(2) OCCURS 5 = 2 B x 5 = 10 B
byte  81 +------------+ total: 71 + 10 = 81 bytes
```

81 bytes, one overlap, no slack (no SYNC). The verifier re-declares the same record —
save as `layoutlab.cob` (GnuCOBOL 3.x, 64-bit, free format):

```cobol
identification division.
program-id. layoutlab.
data division.
working-storage section.
01 txn-record.
   05 txn-id     pic 9(8).
   05 txn-acct   pic 9(10).
   05 txn-type   pic x(2).
   05 txn-amt    pic s9(9)v99 comp-3.
   05 txn-amt-x  redefines txn-amt pic x(6).
   05 txn-bal    pic s9(11)v99 comp-3.
   05 txn-fee    pic s9(3)v9 comp-3.
   05 txn-seq    pic s9(4) comp.
   05 txn-branch pic s9(5) comp.
   05 txn-cnt    pic s9(9) comp.
   05 txn-status pic x.
   05 txn-entry  pic s9(4)v999 comp-3.
   05 txn-date   pic 9(8).
   05 txn-desc   pic x(12).
   05 txn-codes  pic x(2) occurs 5 times.
01 batch-record.
   05 batch-id   pic 9(6).
   05 batch-cnt  pic 9(3).
   05 batch-entry pic x(40) occurs 1 to 250 times
                 depending on batch-cnt.
   05 batch-trailer pic x(8).
01 base-ptr usage pointer.
01 base-num redefines base-ptr pic 9(18) comp-5.
01 fld-ptr  usage pointer.
01 fld-num  redefines fld-ptr  pic 9(18) comp-5.
01 off      pic zzz9.
01 running  pic zzz9.
procedure division.
main.
   display "TXN-RECORD total bytes: " function length(txn-record)
   move 0 to running
   display "TXN-ID     " running " " function length(txn-id)
   add function length(txn-id) to running
   display "TXN-ACCT   " running " " function length(txn-acct)
   add function length(txn-acct) to running
   display "TXN-TYPE   " running " " function length(txn-type)
   add function length(txn-type) to running
   display "TXN-AMT    " running " " function length(txn-amt)
   display "TXN-AMT-X  " running " " function length(txn-amt-x)
   add function length(txn-amt) to running
   display "TXN-BAL    " running " " function length(txn-bal)
   add function length(txn-bal) to running
   display "TXN-FEE    " running " " function length(txn-fee)
   add function length(txn-fee) to running
   display "TXN-SEQ    " running " " function length(txn-seq)
   add function length(txn-seq) to running
   display "TXN-BRANCH " running " " function length(txn-branch)
   add function length(txn-branch) to running
   display "TXN-CNT    " running " " function length(txn-cnt)
   add function length(txn-cnt) to running
   display "TXN-STATUS " running " " function length(txn-status)
   add function length(txn-status) to running
   display "TXN-ENTRY  " running " " function length(txn-entry)
   add function length(txn-entry) to running
   display "TXN-DATE   " running " " function length(txn-date)
   add function length(txn-date) to running
   display "TXN-DESC   " running " " function length(txn-desc)
   add function length(txn-desc) to running
   display "TXN-CODES  " running " " function length(txn-codes)
   add function length(txn-codes) to running
   *> independent check: real addresses, not accumulation
   set base-ptr to address of txn-record
   set fld-ptr  to address of txn-amt
   compute off = fld-num - base-num
   display "ADDRESS-OF check: TXN-AMT offset " off
   display "BATCH-RECORD: fields after an ODO table shift"
   set base-ptr to address of batch-record
   move 3 to batch-cnt
   set fld-ptr to address of batch-trailer
   compute off = fld-num - base-num
   display "batch-cnt = 3 -> trailer offset " off
   move 7 to batch-cnt
   set fld-ptr to address of batch-trailer
   compute off = fld-num - base-num
   display "batch-cnt = 7 -> trailer offset " off
   display "length(batch-record) now: " function length(batch-record)
   stop run.
```

```bash
cobc -x -free -o layoutlab layoutlab.cob && ./layoutlab
```

Expected output fragments (hand-derived; see the status note):

```text
TXN-RECORD total bytes: 81
TXN-AMT     20 6
TXN-AMT-X   20 6
...
ADDRESS-OF check: TXN-AMT offset 20
BATCH-RECORD: fields after an ODO table shift
batch-cnt = 3 -> trailer offset 129
batch-cnt = 7 -> trailer offset 289
```

**Status note:** written where `cobc` was not installed — the fragments are
hand-computed expectations, not captured output; every number follows the rules above
and was re-derived on paper. Run it and diff. (`ADDRESS OF` on a subordinate item is a
GnuCOBOL extension — 85 restricts it to 01/77; if your compiler objects, the walk still
verifies every size.)

What to notice:

- The accumulation walk and the `ADDRESS OF` subtraction are two independent
  measurements of TXN-AMT's offset — when both say 20, your rules match your compiler.
- Trailer offsets 129/289 = `9 + 40 × cnt` — the moving wall, live: the record "grows"
  160 bytes while its allocation never changes, and the final line (current 137 or
  allocated 10,017, by compiler) is why production code never trusts LENGTH on ODO
  groups. Add `SYNC` to `TXN-CNT` and recompute by hand first: if your build honors
  alignment, offsets from 42 shift — your table, not the program, must change.

## How to use this knowledge

- **Byte-map any copybook in minutes.** Given a 40-year-old record description, produce
  the offset/size table by hand — where data-migration archaeology starts. "The
  posting date is the 8 bytes at offset 51" is when an extract script becomes
  writable.
- **Read record dumps by eye.** `01 23 45 6C` is +0123456 packed; `F1 F2 D3` is −123
  zoned; a `D` where you expected `C` means a sign convention was lost between systems
  — the number-one partner-feed bug, visible in hex.
- **Catch layout drift before it corrupts.** SYNC slack here but not there,
  `binary-size` configs, HFP-vs-IEEE COMP-1: same PICTURE, different bytes — any
  cross-platform move of raw records needs the hand table on both sides.

## Connections

- **Module 01 chapter 06** — object layout: the same byte-box discipline; SYNC slack is
  COBOL's struct padding.
- **Module 01 chapter 08** — unchecked C arrays: ODO overflow and default-off subscript
  checking, same bug class.
- **Module 01 chapter 17** — COBOL's no-heap model: this chapter is its PICTURE
  arithmetic at working depth.
- **Module 03 chapter 04** — 0.1 + 0.2 ≠ 0.3 is the disease; COMP-3 with `V` is the
  1959 cure banking adopted.
- **Module 08 chapter 02** — storage sections: where these records live, why 88s own
  zero bytes, and the FILLER slack these offsets expose.
- **Module 08 chapter 04** — CICS: records travel through GETMAIN'd buffers; manual
  record lengths meet storage protection keys.
- **Module 08 chapter 05** — REDEFINES-without-tags is why the schema is gone; ODO is
  why layouts resist naive export.

## Quiz

1. Compute the full layout by hand (no SYNC): `05 A PIC X(3). 05 B PIC S9(5) COMP.
   05 C PIC S9(7)V9 COMP-3. 05 D PIC 9(4).` — every offset, every size, the total.

2. Same record, but `05 B PIC S9(5) COMP SYNC.` on z/OS Enterprise COBOL. What changes
   in the layout, and why does the total grow by exactly one byte?

3. Derive — don't recall — why `PIC S9(7) COMP-3` is exactly 4 bytes and why
   `PIC S9(8) COMP-3` needs 5.

4. Module 03 chapter 04 showed `0.1 + 0.2 == 0.3` is `False` in IEEE-754 float64. Why
   can `PIC S9(3)V9 COMP-3` arithmetic never produce that error class, and what does it
   give up versus a float?

5. A writer lays out `BATCH-RECORD` (6 + 3 + 40 × cnt + 8) with the count = 8; the
   reader's spec says 3, so it computes the trailer for 3. What does the checksum
   validation actually see, and why does the OCCURS raise no error?

6. A partner feed's amount field arrives as EBCDIC `F1 F2 D3`. Your program declared
   it `PIC 999` (unsigned) and compares it `> 100`; the business swears it was negative.
   What is the true value, what does the unsigned compare likely do with the D zone,
   and what is the correct declaration?

7. A job abends `S0C7` (data exception) at `COMPUTE X = WS-FLAGS-NUM * 2`. The dump
   shows `WS-FLAGS = C1 40 40 40`, where `05 WS-FLAGS PIC X(4)` and
   `05 WS-FLAGS-NUM REDEFINES WS-FLAGS PIC 9(4)`. Give the chain from the causing MOVE
   to the abend.

8. `88 STATUS-DISPUTED VALUE "1" "D"` sits under `TXN-STATUS PIC X`. How many bytes
   does the 88 add, what does `IF STATUS-DISPUTED` expand to, and which earlier
   chapter in this module proved that storage answer with addresses?

<details>
<summary>Answers</summary>

1. **A:** offset 0, 3 bytes. **B:** offset 3, 5 digits → 4 bytes COMP (5–9 row), 3+4=7.
   **C:** offset 7, S9(7)V9 = 8 digits (`V` free), floor(8/2)+1 = 5 bytes, 7+5=12.
   **D:** offset 12, 4 bytes DISPLAY. **Total = 16 bytes.**

2. B is 4-byte binary; SYNC demands fullword alignment: 1 slack byte puts B at offset 4
   [4–7], C at 8 [8–12], D at 13 [13–16]. Total = 16 + 1 = **17 bytes** — the slack
   byte exists only in the machine, never in the source. (z/OS always honors SYNC; x86
   builds may not.)

3. S9(7): 7 digits + 1 sign nibble = 8 nibbles = **exactly 4 bytes**, no padding.
   S9(8): 8 + 1 = 9 nibbles — odd — so floor(8/2)+1 = **5 bytes**, with one pad nibble
   wasted. Odd digit counts pack perfectly; even counts force the extra byte.

4. COMP-3 stores exact decimal digits: 0.1 is the digits "1" at scale 1, and addition
   is digit-wise with carries — so 0.1 + 0.2 = 0.3 exactly. The float error exists
   because 0.1 is an infinite binary *fraction*; packed decimal never converts to
   binary fractions. It gives up range per byte and speed without decimal hardware.

5. The reader validates 8 bytes at 9 + 40×3 = 129, but the writer's cnt = 8 puts
   entries 4–8 at [129–328] and the true trailer at 329: the "checksum" is really
   bytes 129–136 of entry 4 — arbitrary data — so it fails (or matches by luck). The
   OCCURS raises nothing: count/subscript checking is off by default, so the language
   trusts the count field — why ODO length checks are hand-written.

6. True value: the D zone on the last byte means **−123**. Declared unsigned, the
   compare typically ignores the zone and evaluates the magnitude — 123 > 100 → true —
   silently flipping a negative to a positive. Correct declaration: `PIC S999`, so the
   D zone is honored.

7. Someone ran `MOVE "A" TO WS-FLAGS` — legal; C1 40 40 40 is EBCDIC "A" plus spaces.
   WS-FLAGS-NUM redefines those bytes as a DISPLAY number, and C1/40 are not digit
   bytes: COMPUTE drives decimal instructions over non-decimal data, so z/OS traps
   S0C7. REDEFINES bought no tag byte, so nothing knows which view is live; fix = a
   `NUMERIC` test first.

8. Zero bytes — an 88 is a compile-time comparison, not storage. `IF STATUS-DISPUTED`
   expands to `IF TXN-STATUS = "1" OR TXN-STATUS = "D"`. Module 08 chapter 02 proved
   it by printing addresses of items with and without 88s.

</details>
