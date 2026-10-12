# Chapter 17 — COBOL: the memory model with no heap

> **The 30-second version.** COBOL took the opposite bet from every language in
> chapters 08–16: nothing is allocated at run time. Every variable's address, size,
> and byte layout is fixed at compile and load — storage is a **completed contract**
> before the first instruction runs. No per-call frame, no heap, no garbage collector.
> The Data Division *is* a memory map: `PICTURE` describes exact bytes, 01-level
> records concatenate them with no padding, and one layout doubles as file record,
> screen field, and punch card image. It buys total determinism and exact decimal
> arithmetic — the trade sixty years of settlement runs kept accepting.

## The bet: every byte is decided before the program runs

COBOL's four answers are the most extreme in Part II:

| Question | COBOL's answer (COBOL 85) |
|----------|---------------------------|
| What is a value? | A fixed byte field; size, offset, layout declared in the Data Division. |
| Who owns it? | The program: WORKING-STORAGE, allocated once at load. |
| Who frees it? | Nobody: nothing is allocated after load (pre-2002). |
| What safety? | Layout safety by construction; no pointers. |

The bet is **static storage**: the program is mapped at load, WORKING-STORAGE is
carved at compile-time offsets, and the program thereafter only reads and writes
those bytes. A call costs a branch, not a frame with fresh locals — the classic
mainframe cost is saving 16 registers into a pre-allocated 72-byte save area. The
buy:

- **Determinism.** No allocation order, no GC pause, no compaction, no address ever
  changes: the millionth record costs what the first did.
- **Zero allocation cost.** No allocator in the loop, nothing to fragment — chapter
  05's catalog of costs never applies.
- **Layout == file record == screen field == punch card.** The Data Division descends
  from 80-column cards, and the compiler makes memory match the *media*: `FD` record,
  CICS screen field, and WORKING-STORAGE group can share one PICTURE — copying between
  them is a raw byte move, no serialization.

There is no dynamic allocation at all until COBOL 2002's `ALLOCATE` verb, met below.

## Three storages, one of them yours: WORKING-STORAGE, LOCAL-STORAGE, LINKAGE

The difference between the three storage sections is **who owns the bytes**:

- **WORKING-STORAGE** — allocated once at load, alive until the run-unit ends. A
  subprogram called 10,000 times sees the *same* bytes: values persist between
  calls, `VALUE` clauses run once, at load. `CANCEL` releases a called program's
  storage; the next `CALL` re-initializes it.
- **LOCAL-STORAGE** (COBOL 85) — fresh bytes per invocation, freed on return: a
  frame's locals from ch04, `VALUE` re-applied each call.
- **LINKAGE** — the caller's storage. The callee allocates nothing; LINKAGE items
  describe bytes the caller owns, bound by `PROCEDURE DIVISION USING` at
  `CALL ... BY REFERENCE`. The callee describes; it never owns.

```cobol
       IDENTIFICATION DIVISION.
       PROGRAM-ID. ADD-TAX.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-RATE      PIC S9(3)V9(4) COMP-3 VALUE 0.0825.  *> 4 bytes, once, forever
       LINKAGE SECTION.
       01  LK-AMOUNT    PIC S9(9)V99    COMP-3.              *> 0 bytes allocated here
       PROCEDURE DIVISION USING LK-AMOUNT.                   *> bound to caller's bytes
           COMPUTE LK-AMOUNT ROUNDED = LK-AMOUNT * (1 + WS-RATE)
           GOBACK.
       END PROGRAM ADD-TAX.
```

The caller says `CALL "ADD-TAX" USING BY REFERENCE WS-TOTAL` — the default protocol —
passing the *address* of `WS-TOTAL` in a register: the C pointer idea (ch08), but the
pointee's shape is declared in the Data Division. Who owns what:

```
   one run-unit (a batch step)                 ADD-TAX subprogram's view:
 ┌──────────────────────────────────────┐
 │ MAIN                                 │      WORKING-STORAGE  WS-RATE  4 bytes
 │  WORKING-STORAGE                     │      └ callee-owned; one allocation at
 │   WS-TOTAL  PIC S9(9)V99 COMP-3      │        load; same bytes every CALL
 │   CALL "ADD-TAX" USING WS-TOTAL      │
 │    │                                 │      LOCAL-STORAGE   (COBOL 85)
 └────┼─────────────────────────────────┘      └ fresh per CALL (a frame's locals)
      │  BY REFERENCE = pass the address
      ▼                                       LINKAGE  LK-AMOUNT  0 bytes
   WS-TOTAL's bytes ───────────────────────►  └ alias for MAIN's WS-TOTAL; the
                                                  callee owns nothing
```

The classic trap falls straight out of the diagram: WORKING-STORAGE persists across
calls *by design*, so an accumulator that is never reset double-counts on the second
`CALL` — put that state in LOCAL-STORAGE.

## PICTURE is a type system at byte granularity

`PICTURE` is a storage-size specification, not a format string: every symbol claims
bytes or nibbles, and `USAGE` picks the encoding.

| USAGE | Encoding | Size rule |
|-------|----------|-----------|
| `DISPLAY` (default) | 1 byte per character; EBCDIC on z/OS, ASCII on GnuCOBOL | `PIC 9(4)` = 4 B |
| `COMP`/`COMP-4`/`BINARY` | fixed binary, word-aligned per `SYNCHRONIZED` | 1–4 digits: 2 B; 5–9: 4; 10–18: 8 |
| `COMP-3`/`PACKED-DECIMAL` | 2 digits per byte + sign nibble | `floor(digits/2) + 1` B |
| `COMP-1`, `COMP-2` | floats; hex float on old S/390, IEEE-754 on z/Architecture and GnuCOBOL | 4, 8 B |

The banking workhorse is COMP-3. `PIC S9(7) COMP-3` holding +0123456 packs 7 digit
nibbles and 1 sign nibble into 4 bytes:

```
   PIC S9(7) COMP-3 = value +0123456, sign nibble S

   byte 0        byte 1        byte 2        byte 3
   +---------+   +---------+   +---------+   +---------+
   | 0 | 1   |   | 2 | 3   |   | 4 | 5   |   | 6 | C   |
   +---------+   +---------+   +---------+   +---------+
     hi  lo        hi  lo        hi  lo        hi  lo
    digit digit digit digit digit digit digit   sign

   hex: 01 23 45 6C          size = floor(7 / 2) + 1 = 4 bytes
   sign nibble: C = positive, D = negative, F = unsigned
   (same nibble values on z/OS and GnuCOBOL 3.x)
```

A negative value flips the last nibble to `D`: -1234567 is `12 34 56 7D`. An odd
digit count pads with a leading zero nibble, so unsigned 987654 in `PIC 9(6)` packs
to `09 87 65 4F`. Group items are *plain concatenation* of their children — the
group's size is the exact sum:

```
01  WS-CUST-REC.                                    total 36 bytes
    05  C-ID        PIC 9(7)      COMP-3.
    05  C-NAME      PIC X(24).
    05  C-BALANCE   PIC S9(11)V99 COMP-3.           V costs 0 bytes (implied point)
    05  C-STATUS    PIC X.
        88  STATUS-ACTIVE  VALUE "A".               costs 0 bytes (see below)

 offset  size  item        PICTURE            the bytes on z/OS (EBCDIC)
 ------  ----  ----------  -----------------  --------------------------------
      0     4  C-ID       9(7) COMP-3        01 23 45 6F   unsigned -> F nibble
      4    24  C-NAME     X(24)              D4 E4 E4 ...  ("MUN"...)
     28     7  C-BALANCE  S9(11)V99 COMP-3   00 00 00 12 34 56 7C   +12345.67
     35     1  C-STATUS   X                  C1            EBCDIC "A"
 ------  ----
      0    36  group      byte-for-byte sum; no padding; offsets frozen at compile
```

Two numbers anchor the type system. COBOL 85 caps an item at **18 digits** (IBM
Enterprise COBOL 6.x raises this to 31 with `ARITH(EXTEND)`), and the digits exist for
banking: the S/360 (1964) shipped a packed-decimal instruction set — ZAP, AP, SP, MP,
DP — so `PIC S9(11)V99 COMP-3` counts cents in exact base-10 and money never passes
through binary. Ch01/02 showed why: IEEE-754 cannot represent 0.01 exactly, and a
32-bit binary cents counter overflows past $21.47 million, while 13 COMP-3 digits
count to $99,999,999,999.99 exactly. Hence decimal COBOL — and `float` nearly absent
from ledgers.

## The 01 record: concatenation as contract, REDEFINES as sanctioned punning

Chapter 06 showed C structs padding fields to alignment. A COBOL 01 record is
stronger: **no padding, ever** — offset = running sum of the elementary sizes, because
the compiler's job is to make memory match the record on tape; slack bytes would be a
bug, not an optimization. The exception: `SYNCHRONIZED` COMP items force word
alignment and *insert slack bytes* — which is why SYNC never appears in a file record
layout, where the padding would shift every later field. Folklore: "SYNC in
WORKING-STORAGE, never in the FILE SECTION."

**REDEFINES** is COBOL's union — one offset, several interpretations:

```cobol
01  WS-MSG-AREA.
    05  MSG-LENGTH     PIC 9(4)  COMP.             *> offset 0, 2 bytes
    05  MSG-PAYLOAD    PIC X(76).                  *> offset 2, 76 bytes
    05  MSG-ACCT-VIEW  REDEFINES  MSG-PAYLOAD.     *> same offset 2, same 76 bytes
        10  ACCT-NUM     PIC X(12).
        10  ACCT-BAL     PIC S9(11)V99  COMP-3.    *> 7 bytes
        10  ACCT-FILLER  PIC X(57).                *> 12 + 7 + 57 = 76
```

Write the payload as characters, read it back as an account record — *safe* punning,
the thing C's strict-aliasing rules (ch08) make undefined. The C compiler doesn't know
two pointers alias, so it may cache a load across the store; the COBOL compiler
*knows* — both views are statically declared in one Data Division and provably share
bytes, so it is forbidden to assume they don't. In COBOL, aliasing is *declared*, and
the layout is the contract: the strict-aliasing war never starts. (IBM Enterprise
COBOL relaxes the no-larger rule at 01 level for alternate record layouts.)

**OCCURS** declares a fixed array with fixed bounds, and the price is paid at load:
`PIC X(40) OCCURS 10000` costs 400,000 bytes whether you use 3 items or 10,000 —
tables are budgets, which is why old programs have tables sized to the business day.
With bounds checking off by default (IBM Enterprise COBOL 6.x default `NOSRRANGE`),
subscript 10,001 writes into the next field: the ch21 lesson reaches COBOL too.

**OCCURS DEPENDING ON (ODO)** is the variable-length-record trick — a table whose
current element count is itself a data item:

```cobol
01  WS-ORDER-REC.
    05  ORD-ID         PIC 9(7)  COMP-3.           *> offset 0
    05  ORD-DATE       PIC X(8).                   *> offset 4
    05  ORD-ITEM-COUNT PIC 9(3)  COMP.             *> offset 12; control field must
                                                    *>   PRECEDE the table
    05  ORD-ITEMS      OCCURS 500 TIMES
                      DEPENDING ON ORD-ITEM-COUNT.
        10  ITEM-SKU   PIC X(8).                   *> 11 bytes per element
        10  ITEM-QTY   PIC S9(5) COMP-3.
    05  ORD-TOTAL      PIC S9(11)V99  COMP-3.      *> offset 14 + 11 x count: SHIFTS
```

Everything after the table sits at a *run-time variable offset* — `ORD-TOTAL` moves 11
bytes per counted item. Hence the famous restrictions: the control field must precede
the table and be set before the variable part is touched; `REDEFINES` is forbidden on
and after the variable part (offsets aren't constants); and a variable-length file
must *carry the length* — a z/OS V-format record stores a 4-byte Record Descriptor
Word (2-byte binary length + 2 reserved) ahead of the data, and the `FD` declares
`RECORD VARYING FROM 14 TO 5521`. Miss the count and you read a shifted record — the
S0C7 in the quiz.

**88-level condition names** are the zero-byte boolean. `88 STATUS-ACTIVE VALUE "A"`
stores nothing — it is a compile-time *view* of `C-STATUS`'s value: `IF STATUS-ACTIVE`
compiles to a 1-byte compare against `C1` (EBCDIC "A"), and `SET STATUS-ACTIVE TO
TRUE` writes that byte. Python's `True` is a 28-byte heap object (ch14); COBOL never
allocates a boolean.

## The PROCEDURE DIVISION has no frames

Classic control flow is `PERFORM`: run this paragraph (or this one `THRU` that one)
and come back. Paragraphs and sections are labels in one flat body of code, and they
**share storage** — no frame per PERFORM, no lexical scope, no locals: any state a
paragraph needs is WORKING-STORAGE, visible to every other paragraph. The machine
cost is a branch and a saved return address; the model cost is that reentrancy is
impossible *by construction*, and COBOL 85 enforces it: **a program may not call
itself, directly or indirectly**. A recursive activation would alias its own
WORKING-STORAGE, and the compiler has no machinery to grow a fresh copy per level —
there is no frame machinery to grow (ch04 built it; COBOL 85 never did).

COBOL 2002 added the opt-in: `PROGRAM-ID. QUOTA IS RECURSIVE.` LOCAL-STORAGE then
allocates per invocation on a real runtime stack — the ch04 frame, bought deliberately
— while WORKING-STORAGE stays shared: invariants in WS, per-level state in LS.
GnuCOBOL 3.x accepts the attribute; mainframe shops historically restructured
recursion into iteration with an explicit table in WORKING-STORAGE ("the table is
your stack"). The same release admitted the heap: COBOL 2002's `ALLOCATE` returns a
pointer you can aim at a LINKAGE item via `SET ADDRESS OF` — opt-in dynamic storage,
43 years after the first COBOL.

## Below the line, below the bar: the machine that hosts the bet

Two z/OS storage boundaries have names old enough to vote, and both are address-width
stories.

**The 16 MB line.** MVS/370 descended from the S/360, whose addresses were 24 bits
even though registers were 32 — 2^24 = 16,777,216 bytes was the entire addressable
world, and programs grew comfortable storing flags in the high byte of "addresses."
When S/370-XA extended addressing to 31 bits in 1983 (2 GB), the top bit became the
AMODE switch, and everything still running 24-bit — old programs, old control blocks,
channel programs — had to keep its data *below the line*: a fossil of 1964 hardware
economics.

**The bar.** The 31-bit ceiling itself, 2 GB, persists as "the bar," because z/OS COBOL
still overwhelmingly runs in 31-bit AMODE even on 64-bit z/Architecture machines
(z900, 2000). **AMODE** is how wide the running code addresses (24, 31, 64); **RMODE**
is where the module may *load* (below the line, or anywhere below the bar). IBM
Enterprise COBOL dropped 24-bit support at V5: a modern 6.x compile is typically
AMODE(31), RMODE(31) — free to load above the line, confined below the bar.

Above the program sits the **Language Environment (LE)**, the shared runtime for
COBOL, PL/I, and C on z/OS since the early 1990s. LE owns the *real* stack
(LOCAL-STORAGE frames, CALL BY VALUE temporaries) and the LE heap, served by
`CEEGTST`/`CEEFRST` — what a COBOL 2002 `ALLOCATE` talks to. Below LE sits the OS
allocator: `GETMAIN`, whose `LOC=BELOW`/`LOC=ANY` picks which side of the line and bar
storage comes from — the same layering as ch05's `malloc` → `brk`/`mmap`, the
machinery of ch19, which the classic COBOL program never touches.

**Why the model survived 60 years of banking.** Determinism: a settlement run cannot
tolerate a collector pausing mid-batch, and static storage has no collector to pause.
Auditability: every byte has a stable identity, address, and PICTURE, so a dump from
1974 and one from today read identically — traces are reproducible because state never
moves. Fixity of records: billions of fixed-layout files circulate through the world's
clearing systems (NACHA ACH files are still 94-byte records); the *data outlives the
language*. "COBOL programs leak nothing because they allocate nothing" — the ch05–ch16
catalog of leaks, pauses, and dangling references is opted out of, paid for in fixed
budgets.

**Reading a storage dump** is the elder skill. On abend you get a dump (SYSUDUMP, or
LE's CEEDUMP via the `CEE3DMP` service); the compiler listing, built with `LIST`,
carries the storage map with every item's offset, size, and PICTURE. Walk the dump's
hex side with your offsets — EBCDIC digits are `F0`–`F9`, so `F1 F2 F3 C4` reads as
123 with a positive sign; COMP-3 decodes nibble by nibble, sign last. It works
*because* nothing ever moved: the offset true at compile time is true in the dump.

## What insiders know

- **COMP-3 sign nibble conventions: C positive, D negative, F unsigned** — same
  values on z/OS and GnuCOBOL. Dump readers decode packed fields from the right,
  sign nibble last; a byte that isn't decimal at all raises S0C7.
- **The 16 MB line and the bar are an AMODE story, not a size story.** AMODE = how
  wide your code addresses; RMODE = where the module may load. The line survived 1983
  because the high byte of 32-bit registers had been flag storage for 20 years.
- **PERFORM THRU discipline is forced by the storage model.** Control can fall into
  any paragraph — no frames, no lexical scope — so a performed range always ends at a
  bare `EXIT` paragraph (`PERFORM CALC-TAX THRU CALC-TAX-EXIT`): the only structure
  the model gives you.
- **MOVE CORRESPONDING is a layout operation, not a data-driven copy.** The compiler
  resolves the name matches between two groups at compile time and emits a fixed
  sequence of elementary moves; FILLER, 88-levels, and REDEFINES children never
  correspond — runtime cost is exactly those moves.
- **"COBOL programs leak nothing because they allocate nothing."** Pre-2002 there is
  no leak, no double-free, no use-after-free — the ch21 heap-attack surface is
  absent, and the price is that OCCURS bounds are the whole memory budget.
- **CICS transaction isolation is WORKING-STORAGE copy semantics.** CICS keeps
  quasi-reentrant programs resident and hands each concurrent *task* its own copy of
  WORKING-STORAGE — the copy is the isolation — while values persist between
  invocations unless you `INITIALIZE`: the classic dirty-WS bug.
- **The 18-digit limit is versioned:** a COBOL 85 hard cap; IBM Enterprise COBOL 6.x
  allows 31 with `ARITH(EXTEND)` — data moved between the two discovers the cap by
  truncation.

## See it yourself

Install GnuCOBOL (`sudo apt install gnucobol`, or `brew install gnucobol`; 3.1.2 or
later — check `cobc --version`). Save as `sizes.cob`; `-free` matters because
fixed-format source wants columns 8–72:

```cobol
       IDENTIFICATION DIVISION.
       PROGRAM-ID. SIZES.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-CUST-REC.
           05  C-ID        PIC 9(7)      COMP-3.
           05  C-NAME      PIC X(24).
           05  C-BALANCE   PIC S9(11)V99 COMP-3.   *> 13 digits, V costs 0 bytes
           05  C-STATUS    PIC X.
               88  STATUS-ACTIVE  VALUE "A".       *> 0 bytes: a view of C-STATUS
           05  C-VISITS    PIC S9(4)    COMP.
           05  C-RATE      COMP-1.
       PROCEDURE DIVISION.
           MOVE 123456 TO C-ID
           DISPLAY "C-ID        " FUNCTION LENGTH(C-ID)
           DISPLAY "C-NAME      " FUNCTION LENGTH(C-NAME)
           DISPLAY "C-BALANCE   " FUNCTION LENGTH(C-BALANCE)
           DISPLAY "C-STATUS    " FUNCTION LENGTH(C-STATUS)
           DISPLAY "C-VISITS    " FUNCTION LENGTH(C-VISITS)
           DISPLAY "C-RATE      " FUNCTION LENGTH(C-RATE)
           DISPLAY "WS-CUST-REC " LENGTH OF WS-CUST-REC
           GOBACK.
```

```
$ cobc -x -free sizes.cob && ./sizes
C-ID        04
C-NAME      24
C-BALANCE   07
C-STATUS    01
C-VISITS    02
C-RATE      04
WS-CUST-REC 42
```

The group is 42 = 4 + 24 + 7 + 1 + 2 + 4, the exact sum — no padding, unlike the C
struct of ch06. `LENGTH OF` is the COBOL 85 special register, `FUNCTION LENGTH` the
1989 intrinsic; both are compile-time constants. Try `LENGTH OF STATUS-ACTIVE` and
the compiler refuses: an 88-level has no storage — that is the feature. Now prove
COMP-3's bytes with `pack.cob`:

```cobol
       IDENTIFICATION DIVISION.
       PROGRAM-ID. PACKER.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT PACKED-FILE ASSIGN TO "packed.bin"
               ORGANIZATION IS SEQUENTIAL.
       DATA DIVISION.
       FILE SECTION.
       FD  PACKED-FILE.
       01  PACKED-REC.
           05  PR-POS   PIC S9(7) COMP-3.
           05  PR-NEG   PIC S9(7) COMP-3.
           05  PR-UNS   PIC 9(6)  COMP-3.
       PROCEDURE DIVISION.
           OPEN OUTPUT PACKED-FILE
           MOVE  123456  TO PR-POS
           MOVE -1234567 TO PR-NEG
           MOVE  987654  TO PR-UNS
           WRITE PACKED-REC
           CLOSE PACKED-FILE
           GOBACK.
```

```
$ cobc -x -free pack.cob && ./pack
$ od -A d -t x1 packed.bin
0000000 01 23 45 6c 12 34 56 7d 09 87 65 4f
0000012
```

Read it against the nibble diagram: +123456 → `01 23 45 6c` (sign C), -1234567 →
`12 34 56 7d` (D), unsigned 987654 → `09 87 65 4f` (pad nibble, F). Twelve bytes, no
length prefix, no alignment, no metadata — the record *is* the file, the chapter in
one hexdump. (GnuCOBOL 3.x writes standard C/D/F sign nibbles; `-fsign` only affects
zoned DISPLAY signs.) For the real thing, IBM Z Open Editor (VS Code) with Zowe CLI
submits the same source to IBM Enterprise COBOL 6.x on z/OS and shows a genuine
storage map.

## How to use this knowledge

- **Diagnose the classic abends.** S0C7 (data exception) = decimal field holding
  non-decimal bytes — a shifted layout or stale ODO count; S0C6 (specification) =
  binary field off its word boundary; S878 = GETMAIN failed: below the line or below
  the bar?
- **Compute storage on paper.** Total a program's WORKING-STORAGE and predict a batch
  job's footprint from the Data Division — no other language here gives you that.
- **Interview answers that land.** "Why do banks still run COBOL?" is not nostalgia:
  determinism, exact decimal, fixed records (the data outlives the language). "How
  many bytes is `PIC S9(7) COMP-3`?" (four) is a real screen.
- **Read the world's financial files.** NACHA records, fixed-width clearing formats,
  ISO 8583-style fields: modern systems emitting them are writing COBOL layouts;
  PICTURE-thinking (offset, length, encoding, sign) is the decoder ring.
- **Explain the war story:** a job grows a table from 50,000 to 400,000 elements and
  starts failing with storage abends — not a leak, but the *declared budget* crossing
  what remained below the bar. The fix is Data Division arithmetic, not a profiler.

## Connections

- **ch01/02** — why 0.01 is inexact in IEEE-754 and exact in packed decimal.
- **ch04** — the frame COBOL 85 lacks; LOCAL-STORAGE and `IS RECURSIVE` are opting
  back in.
- **ch05** — the heap COBOL skips; `ALLOCATE`/LE heap/`GETMAIN` reintroduce it.
- **ch06** — struct padding vs 01-record concatenation.
- **ch08** — REDEFINES as *declared* aliasing vs C's unknown aliasing.
- **ch12–16** — GC, RC, ARC manage what COBOL never allocated.
- **ch18** — Fortran COMMON and EQUIVALENCE: the other static-layout tradition.
- **ch19** — LE heap and GETMAIN as a real allocator stack.
- **ch21** — no use-after-free surface; unchecked subscripts still corrupt.
- **ROADMAP** — the queued mainframe module builds on this chapter.

## Quiz

1. COBOL 85 forbids a program from calling itself, directly or indirectly. Give the
   storage-model reason, and state what COBOL 2002 `IS RECURSIVE` changes.
2. Two C pointers to the same memory can be a strict-aliasing catastrophe (ch08); two
   COBOL names for the same bytes (`REDEFINES`) never are. Why — what does each
   compiler know at compile time?
3. `PIC S9(7) COMP-3` contains -1234567. How many bytes does the field occupy, and
   what are they in hex?
4. `01 R. 05 A PIC X(3). 05 B PIC 9(4) COMP-3. 05 C PIC S9(4) COMP. 05 D COMP-1.`
   What is `LENGTH OF R`, at what offsets do B, C, and D start, and where would a C
   compiler (ch06) place a 2-byte field like C after the 3-byte A?
5. `IF STATUS-ACTIVE` runs where `88 STATUS-ACTIVE VALUE "A"` hangs under a `PIC X`
   field. How many bytes does the condition name allocate, what does the test compile
   to, and what does the equivalent cost in Python (ch14)?
6. A batch job reads variable-length order records into the ODO record from this
   chapter, never sets `ORD-ITEM-COUNT` before referencing `ORD-TOTAL`, and abends
   S0C7 on record 4. Why? Give two fixes.
7. A subprogram increments a WORKING-STORAGE counter and is `CALL`ed twice in one
   run-unit; the total comes out doubled, with no bug visible in the called program.
   What did the programmer assume, and what are the three standard repairs?
8. Name each allocator in the chain from a COBOL 2002 `ALLOCATE` on z/OS under
   Language Environment down to storage, and the parallel chain for `malloc` on Linux
   (ch05).

<details>
<summary>Answers</summary>

1. **Static storage can't be re-entered.** WORKING-STORAGE exists once, at fixed
   offsets; a recursive activation would read and write the *same* bytes as its
   caller, and COBOL 85 has no frame machinery to grow a fresh copy (ch04 is what it
   lacks). `IS RECURSIVE` (COBOL 2002) allocates LOCAL-STORAGE per invocation on a
   runtime stack: private locals per level, WORKING-STORAGE shared.
2. **Declared vs unknown aliasing.** C can't prove two pointers don't alias, so
   violations are undefined and the optimizer may cache loads across stores; a
   `REDEFINES` pair is declared in one Data Division, so the compiler *knows* the
   names share bytes and may not cache one view across a write to the other. The
   layout is the contract.
3. **4 bytes: `12 34 56 7D`.** Seven digits + one sign nibble = 8 nibbles = 4 bytes
   (`floor(7/2) + 1`). Digits pack left to right (1,2 / 3,4 / 5,6); the low nibble
   of the last byte is the sign — D negative (C positive, F unsigned).
4. **12 bytes; B at 3, C at 6, D at 8.** A is 3 bytes; `PIC 9(4) COMP-3` is 4 digits
   + sign nibble = 5 nibbles, padded to 3 bytes; `S9(4) COMP` is a 2-byte halfword;
   COMP-1 is 4. Offsets are the running sum — no padding. C aligns a 2-byte field to
   a 2-byte boundary: after a 3-byte array it pads 1 byte and places it at offset 4
   (ch06); COBOL places C at 6.
5. **Zero bytes; a 1-byte compare; 28 bytes.** The 88-level is a compile-time view of
   the parent field: the test compiles to one compare of `C-STATUS` against `"A"`
   (`C1` EBCDIC, `41` ASCII); `SET ... TO TRUE` writes that byte. Python's `True` is
   a 28-byte heap object (ch14); the condition allocates nothing because the field
   already exists.
6. **A shifted record is read.** `ORD-TOTAL`'s offset is `14 + 11 × ORD-ITEM-COUNT`;
   a stale count (500 from the previous record, or the initialized max) makes the
   reference land past the short record, in WORKING-STORAGE bytes that decode as
   packed data with non-decimal nibbles — S0C7. Fixes: set the control field from
   the record's carried length (the V-format RDW or your own) before touching the
   variable part, or declare the `FD` `RECORD VARYING ... DEPENDING ON` so the
   system sets the length.
7. **He assumed locals; he got a static.** WORKING-STORAGE is allocated once per
   load and persists across every `CALL` in the run-unit — `VALUE` ran only at load,
   so the counter kept call one's value. Repairs: move the per-call state to
   LOCAL-STORAGE (fresh copy, `VALUE` re-applied each call); reset at entry
   (`INITIALIZE` or `MOVE 0`); or `CANCEL` so the next `CALL` re-runs `VALUE`.
8. **Three layers each.** COBOL: `ALLOCATE` → LE heap service (`CEEGTST`, honoring
   the BELOW/ANY options) → `GETMAIN` (LOC=BELOW/LOC=ANY against line and bar) →
   storage. Linux: `malloc` → the C library allocator (bins, arenas — ch05) →
   `brk`/`mmap` → the page allocator. Same shape, 40 years apart — chapter 19's
   machinery.

</details>
