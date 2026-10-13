# Module 08 final exam — COBOL & the mainframe world

Twelve questions, all five chapters, closed book; answers are worked.
Scoring: 11+ = you own this module; 8-10 = solid, revisit the
flagged chapters; <8 = the chapter quizzes are the study path.

**Part I — the machine and its storage (chapters 01-02)**

1. *(concept)* z/Architecture twice "just widened" addresses and twice paid.
   (a) What does it mechanically mean that a 31-bit program "cannot even
   name" storage above the 2 GB bar? (b) Why did 370-XA (1983) force z/OS
   common areas such as CSA below the 16 MB line, and what did that do to
   the extension's new bytes?

2. *(predict the decode)* A dump shows `PSW AT TIME OF ERROR 079D0005
   81008040`, `INTC 0005`. Decode it: DAT state, storage key, problem or
   supervisor state, addressing mode, failing address relative to the
   16 MB line, interruption code, abend. What does that code rule
   out before any trace?

3. *(concept)* The three storage sections each answer one question: *who
   owns these bytes, and when are they born?* Give the three answers with C
   analogs and each section's exact VALUE semantics; derive the static
   default from one 1964 hardware fact and one 1970s economics fact; name
   COBOL-85's two-part recursion opt-in.

4. *(diagnosis)* A partner audit finds other customers' data in your
   fixed-length extract records. The copybook has `05 FILLER PIC X(4)`
   between two named fields; the program MOVEs only named fields before
   each WRITE and runs `INITIALIZE EXTRACT-RECORD` at entry. Where do the
   four bytes come from, why does nothing fix them, and what is the
   cheapest fix?

**Part II — the byte-level type system (chapter 03)**

5. *(compute the layout)* Byte-map this record: every offset, every size,
   the total. How many bytes do the 88s add? With subscript checking off,
   which field(s) does `CLAIM-REGIONS(7)` hit?

   ```cobol
   01  CLAIM-RECORD.
       05  CLAIM-ID        PIC 9(7).
       05  CLAIM-CODE      PIC X(3).
       05  CLAIM-AMT       PIC S9(7)V99 COMP-3.
       05  CLAIM-AMT-X     REDEFINES CLAIM-AMT PIC X(5).
       05  CLAIM-QTY       PIC S9(4) COMP.
       05  CLAIM-TAX       PIC S9(3)V9 COMP-3.
       05  CLAIM-REGIONS   PIC X(2) OCCURS 6 TIMES.
       05  CLAIM-STATUS    PIC X.
           88  CLAIM-PAID  VALUE "P" "Y".
   ```

6. *(compute the layout)* Moving wall: give the element stride; the offsets
   of INV-DATE and INV-TOTAL and the record's length at INV-CNT = 3 and 12;
   the bytes WORKING-STORAGE allocates; and which bytes land outside the
   record when a feed sets INV-CNT = 41.

   ```cobol
   01  INVOICE-RECORD.
       05  INV-KEY     PIC X(6).
       05  INV-CNT     PIC 9(3).
       05  INV-LINE    PIC S9(5)V99 COMP-3 OCCURS 1 TO 40 TIMES
                      DEPENDING ON INV-CNT.
       05  INV-DATE    PIC 9(8).
       05  INV-TOTAL   PIC S9(9) COMP.
   ```

**Part III — the transaction world and the long game (chapters 04-05)**

7. *(cross-module, derive)* An airline region: 200 live programs averaging
   50 KB, 5,000 concurrent agents. (a) Show the arithmetic that forces one
   copy of each program. (b) State the insight module 05 ch01's Zygote
   shares with it. (c) Name the structural difference in how the two
   systems keep instances from corrupting the shared copy, and the contract
   CICS charges every program.

8. *(diagnosis)* A CICS transaction abends ASRA. The dump: PSW key 9,
   problem state, 31-bit mode; interruption code 0004; a store into
   CICS-key storage; STGPROT=YES. Give the mechanism: which two keys,
   compared where and when, what the hardware did to the store. Why ten
   years without the abend? What would the wild write do in C?

9. *(diagnosis)* A balance inquiry intermittently shows other customers'
   amounts. No abends; the single-user test region never reproduces it; it
   appears only at peak load; it vanishes after a region restart and creeps
   back. Diagnose, give the two one-line fixes, and name the compile option
   that retires the bug class post-1991.

10. *(diagnosis)* A rehost proposal: recompile the batch estate under
    GnuCOBOL on Linux, copy the data sets, run an EBCDIC-to-ASCII text
    conversion over the files. Name at least three byte-level things the
    plan destroys or silently changes (which fields each hits), and the one
    rule that prevents all three.

11. *(concept)* An estate audit: complete, still-compilable source; pure
    batch (VSAM, no CICS, no IMS); no multi-year parallel-run budget. Walk
    the modernization menu to a branch, and name two costs the "recompile
    and go" pitch leaves out.

12. *(cross-module)* Module 01 ch17 closed on COBOL's "completed contract."
    Price both sides: (a) two bug families that cannot exist in classic
    (pre-2002) COBOL, with the eliminating mechanism; (b) two that grew in
    the vacated space, met here; (c) why 40 years of the same design means
    "the spec is gone" — and what that does to rewrite estimates.

<details>
<summary>Answers</summary>

**Part I**

1. (a) 31-bit instructions generate 31-bit effective addresses—arithmetic on
   bits 0-30—so bits 32-63 are not expressible: above-the-bar bytes cannot
   be *named*. 64-bit is a separate execution mode (z900, 2000). (b)
   AMODE-24 binaries had to keep running and address only
   0-16 MB, so common areas like CSA stayed below the line, *consuming the
   bytes the extension added*; conversion took a decade. Residue: AMODE
   (address size generated) and RMODE (load location: 24 below the line,
   ANY below the bar). (ch01)

2. Byte 0 `07`: DAT on (bit 5), masks on. Byte 1 `9D`:
   bits 8-11 = 1001 → **key 9**; bit 15 → **problem state**. Bits 16-31 =
   `0005`: **addressing exception**. Bit 32 = 1 → **31-bit mode**; bits
   33-63 = 0x1008040 = 16,810,048 > 16,777,216 → failing instruction
   **above the 16 MB line**. Abend **S0C5**: no valid translation, a wild
   pointer. Code 5 rules out a key mismatch: code 4 would mean a mapped
   frame with the wrong key—no such page vs. wrong key. (ch01; the CICS
   half lands in question 8)

3. **WORKING-STORAGE**—static: one copy at the program's first activation,
   persisting across every CALL, VALUE applied **once** (load-time, like a
   C global's initializer). **LOCAL-STORAGE**—automatic: a fresh copy per
   invocation, VALUE applied **every** invocation, gone at GOBACK (the
   stack-frame analog). **LINKAGE**—no storage: a PICTURE over the
   caller's bytes via USING's raw address list (nothing checked);
   the pointer analog (`f(&x)`); reading an unset item is
   the classic S0C4. Static default: System/360 had **no hardware stack**
   (`BALR R14,R15`, return addresses in registers); CPU was metered, so
   initialization was explicit and rare. COBOL-85's opt-in:
   RECURSIVE on PROGRAM-ID *plus* per-invocation data in LOCAL-STORAGE.
   (ch02)

4. The **previous record's residue**. FILLER is unreferenceable by
   design—no name means no MOVE target, and INITIALIZE skips it *by
   definition*—and a FILE SECTION FILLER may not carry VALUE. Record areas
   are reused, so the prior customer's bytes ride into the next record
   written: the documented PII-in-unnamed-bytes leak. Cheapest fix: a
   group-level MOVE (`MOVE LOW-VALUES TO EXTRACT-RECORD`) fills every
   byte, FILLER included. (ch02; GETMAIN's no-zeroing default repeats the
   theme, ch04)

**Part II**

5. Left to right, REDEFINES buying nothing:

   ```text
   CLAIM-ID       9(7)     7 B @0     DISPLAY: 1 byte per character
   CLAIM-CODE     X(3)     3 B @7
   CLAIM-AMT      S9(7)V99 5 B @10    COMP-3: 9 digits (V free),
                                       floor(9/2)+1
   CLAIM-AMT-X    REDEFINES 5 B @10   same bytes, no advance
   CLAIM-QTY      S9(4)    2 B @15    COMP: 1-4 digits -> halfword
   CLAIM-TAX      S9(3)V9  3 B @17    COMP-3: floor(4/2)+1
   CLAIM-REGIONS  X(2) x 6 12 B @20
   CLAIM-STATUS   X        1 B @32
   total: 7+3+5+2+3+12+1 = 33 bytes
   ```

   The 88s add **zero bytes**: compile-time comparisons over
   CLAIM-STATUS's byte (`IF CLAIM-PAID` tests `"P"` or `"Y"`).
   `CLAIM-REGIONS(7)` addresses 20 + 2x(7-1) = 32, writing [32-33]: byte
   32 hits **CLAIM-STATUS**; byte 33 lands one past the record—silent,
   because subscript checking is off by default: module 01 ch08's
   unchecked array, reborn. (ch03)

6. INV-LINE: S9(5)V99 COMP-3 = 7 digits, floor(7/2)+1 = **4 bytes**—the
   stride. INV-KEY [0-5], INV-CNT [6-8], table at 9; every field after
   it moves with the count:

   ```text
   cnt =  3: table [9-20], INV-DATE 21 [21-28], INV-TOTAL 29 [29-32]; length 33
   cnt = 12: table [9-56], INV-DATE 57 [57-64], INV-TOTAL 65 [65-68]; length 69
   ```

   INV-TOTAL: S9(9) COMP → 4 bytes. WORKING-STORAGE allocates the
   **maximum**: 9 + 40x4 + 8 + 4 = **181 bytes**—ODO moves the record's
   *logical* length, never the allocation. At INV-CNT = 41: INV-DATE at 173
   [173-180], INV-TOTAL at 181—**[181-184] land entirely outside the
   record**, corrupting the next item: the count is data, not enforcement;
   ODO length checks are hand-written. (ch03)

**Part III**

7. (a) Shared: 200x50 KB = **10 MB**; per task x 5,000 = **250 MB**—
   impossible on a 1968 few-MB machine. Program text is read-mostly: it
   exists once. (b) Zygote: preload the runtime once,
   fork every app from it sharing pages copy-on-write—*share the expensive
   common pages, privatize the per-instance bytes*: one economics, 40
   years apart. (c) Zygote isolates instances **with the MMU**—COW makes
   writes private, free; CICS has no fork, so isolation is **discipline
   backed by storage keys**: quasi-reentrancy—re-enterable at every EXEC
   CICS command boundary, no per-task state in WORKING-STORAGE (COMMAREA,
   32 KB cap, or GETMAIN'd storage)—paid on every task switch.
   (ch04; module 05 ch01)

8. The store carried **PSW key 9** (bits 8-11); its address went through
   DAT to a **frame keyed 8**—CICS's own storage, split off by
   STGPROT=YES. The hardware compares access key with frame key after
   translation, per access: 9 ≠ 8, neither 0—the store was **suppressed
   before a byte landed**: interruption code 4, S0C4, the task abending
   ASRA, the region surviving. Ten silent years: the region ran one key
   before, so the same store *succeeded*, surfacing later in an
   unrelated transaction—the bug is old, the detection new. In C the
   overwrite lands in the next malloc chunk, stops nothing: someone else's
   crash, later—the memory-safety net C never got. (ch01, ch04)

9. **Dirty shared WORKING-STORAGE.** One program copy per region; VALUE
   clauses fired once, at load. At peak load, tasks interleave through the
   same bytes at EXEC CICS command boundaries: task B reads task A's
   residue—first task correct by luck, later tasks inherit the previous
   customer's leftovers. Single-user test cannot sequence two tasks; a
   restart re-fires the VALUEs—hence vanishes, then creeps. The lab's three
   CALLs without CANCEL printed 100, 350, 390. Fixes: INITIALIZE (or
   MOVE) the state-carrying items at entry; or per-task state in the
   COMMAREA or GETMAIN'd storage. Compile option: **RENT**, task-scoped
   static per LE enclave, retiring the class after ~1991. (ch04;
   VALUE-once mechanics, ch02)

10. (1) The character-set pass **destroys every non-DISPLAY field**: COMP-3
    balances (two digits per byte plus sign nibble) and COMP binary
    halfwords are not text—their bytes become garbage. (2) Text fields
    survive in *length* only—values change (0xC1 becomes 0x41), so
    comparisons, hashing, and sort order change: EBCDIC digits (0xF0...)
    sort above letters, ASCII below. (3) Recompiling re-lays
    records—**same PICTURE, different bytes**: GnuCOBOL's binary-size
    gives 1-2-digit COMP items 1 byte; z/OS spends 2; COMP-1/2 are
    hexadecimal float on z/OS, IEEE-754 on x86. The rule: **record lengths
    are in bytes, and a record is not text**—migrate via a
    byte-preserving, field-by-field extract from the hand-computed
    offset table. (ch01, ch03, ch05)

11. Q1 complete source → yes; Q2 CICS/IMS entanglement → no; Q3 multi-year
    parallel-run budget → no. Branch: **REHOST**—Linux plus GnuCOBOL or
    Micro Focus. Costs the pitch omits: the data conversion is
    field-by-field byte surgery, not a utility run (question 10); and the
    platform leaves with the hardware—JCL, VSAM, scheduling, the systems
    programmers ("COBOL is easy to learn, mainframe engineering is
    not"): staffing the language but not the platform mis-prices the
    skills cliff. (ch05)

12. (a) **Use-after-free and double-free**—nothing is ever freed in classic
    COBOL, so no reference can outlive its target (module 01 ch05's
    catalog). **Fragmentation**—no allocator, every offset fixed at compile
    time, no arena to fragment. (b) **ODO overflow**—the length rule is a
    data field plus hand-written checks; a stale count shifts every
    later field (question 6); **dirty storage**—WORKING-STORAGE persisting
    across CICS tasks (question 9). (c) The same decision made records
    **schema-less**: no metadata on DASD; REDEFINES gives one region
    several legal readings, and no tag says which is live—the choice lives
    in whatever program touches it next. Forty years, plus EBCDIC code
    pages and retired engineers: the spec survives only in running code and
    microfiche. A rewrite is **archaeology**—recovering the lost spec
    from its only complete artifact, the running system; TSB 2018 (~£330M
    plus a £48.65M fine, untested data) is the under-budgeted
    version. Bugs move; they never disappear. (ch05; module 01 ch17)

</details>
