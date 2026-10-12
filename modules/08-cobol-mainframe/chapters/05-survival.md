# Chapter 05 — Why it survives

> **The 30-second version.** Three stacked reasons, only the first technical.
> Economically, one frame's I/O subsystem moves transaction volume a
> warehouse of commodity servers would envy — IBM's numbers, marked as
> vendor claims. Semantically, the code cannot leave: 40 years of REDEFINES
> and microfiche put the *specification* of the world's financial records in
> running programs, not documents — "just rewrite it" is archaeology, not
> translation. Structurally, the people who could leave are retiring, and the
> scarce skill is not COBOL — it is the platform. Final insight: COBOL is
> module 01's control experiment — a world with no heap, where whole bug
> classes cannot exist — and its own bug classes grew in the vacated space.
> Bugs move; they never disappear.

## The economics, stated honestly

**MIPS** — millions of instructions per second — is a planner's heuristic
inherited from VAX-era benchmark marketing; IBM publishes no MIPS figures.
Large z16-class frames are quoted in the tens of thousands to roughly
200,000 MIPS, but insiders lease **MSUs** (millions of service units per
hour) — the unit on the invoice and the unit z/OS Workload Manager governs.

What the machine sells is I/O: dozens to hundreds of FICON channels
at 16 Gb/s for DASD, OSA adapters at 10/25/100 GbE, and (since z15) NVMe
flash in the I/O path. IBM's 2019 z15 announcement claimed "capacity to
process over 1 trillion transactions per day" — a vendor claim; no workload
definition published. Same bucket: "99.999% availability" and "70% of
enterprise transactions run on mainframes" — vendor folklore, terms never
defined. The real mechanism: a Parallel Sysplex shares one DB2 for z/OS
data-sharing group across frames through a coupling facility — the database
stays unified while I/O bandwidth scales, the shard-free architecture priced
by the frame.

Then the folklore. Every modernization meeting hears
**~220–800 billion lines of COBOL in daily service** and **"trillions of
dollars per day"**. Provenance, as far as traced: Gartner estimated
~180 billion lines in use in 1997; 220 billion entered circulation through
Micro Focus-commissioned surveys (2016–2017); 800 billion circulates in
consultant decks with no discoverable methodology; and "95% of ATM
transactions" / "$2–3 trillion per day" trace to the same lineage, amplified
by a 2017 Reuters story. Insiders quote them for scale and privately discount
them: nobody ever counted the lines, and every figure came from someone
selling something. At high volume the frame is competitive per transaction
(vendor-argued); below it, expensive. The *engineering* reasons for survival
are elsewhere.

## The maintenance trap: O(1) records, semantics on microfiche

Module 01 ch17 made the bet precise; here its payoff lands. Every variable's
address, size, and layout is fixed at compile time — a field reference is
one static offset, and the millionth record costs what the first did. Zero
allocation means nothing to fragment; zero deallocation means no
use-after-free or double-free; no collector means no GC pause inside a
settlement run. Fixed records are O(1) everything: maintenance cost is
*low*. That is the trap.

The same decision destroyed the data's self-description. A record on DASD or
tape is a strip of bytes with no metadata, no schema, no tags. `REDEFINES`
(module 01 ch17; this module ch03) gives one region two or three *declared*
interpretations, and the live one is decided by whatever program touches it:

```
 01  WS-BLOB.                          offset 0, 8 bytes, no metadata
 +0                  +6                +8
 +----+----+----+----+----+----+----+----+
 | '0'| '0'| '1'| '2'| '3'| '4'| 'X'| 'Y'|  the bytes (ASCII shown)
 +----+----+----+----+----+----+----+----+
 | view 1: PIC X(8)        "001234XY"  |  a text tag? a key?
 | view 2: PIC 9(6)  + PIC X(2)        |  account 001234, check "XY"
 +-------------------------+------------+
 no byte says which view is live: the answer lives in *other* programs
```

Multiply by 40 years, thousands of copybooks, and the EBCDIC code-page wars
of ch01, and the insider's summary is: **the spec is gone.** Every byte's
meaning lives in running code, in copybooks, in microfiche, in the memory of
whoever retired in 2011. This — not nostalgia — is why "just rewrite it"
fails at the estimate stage: the rewrite must *reconstruct* a lost
specification from its only complete artifact, the running system.
Translation is cheap; archaeology is the cost.

## The modernization menu

Four strategies; the tree maps cost (rising downward) and semantic risk
(rising rightward):

```
        THE MODERNIZATION MENU (decision tree)
        cost rises DOWNWARD, semantic risk rises RIGHTWARD

 START: revenue-bearing COBOL, still clearing every night
  |
  +-- Q1: complete, still-compilable source for everything?
  |      |
  |      +-- no (lost copybooks, loadlib only) ---> [1] WRAP
  |      |                                          z/OS Connect / CICS REST
  |      |                                          cost: $    risk: *
  |      +-- yes
  |            |
  |            +-- Q2: deep CICS / VSAM / IMS / RACF entanglement?
  |                  |
  |                  +-- yes --> [1] WRAP now; carve services out later
  |                  |
  |                  +-- no --> Q3: multi-year parallel-run budget?
  |                        |
  |                        +-- yes --> [4] AI-ASSIST / [3] REWRITE (big-bang)
  |                        |          cost: $$$$$   risk: **** to *****+
  |                        |          LLM drafts Java; humans reconcile
  |                        |          what the model cannot see
  |                        |
  |                        +-- no --> [2] REHOST
  |                                   Linux + GnuCOBOL / Micro Focus;
  |                                   recompile, re-test, replace the
  |                                   platform services yourself
  |                                   cost: $$$   risk: **
```

**[1] Wrapping — the majority strategy.** A REST/JSON facade in front of
untouched transactions, via **z/OS Connect Enterprise Edition** (V3.x as of
2024) or **CICS REST/URIMAP support** (CICS TS 5.5+; tightened in 6.1).
Nothing recompiles; the dark semantics stay where they were; so does the
MSU bill. Insiders call it lipstick on the dinosaur, then recommend it:
reversible, cheap, unblocks every frontend team while the decision is
deferred. What the wrap does *not* do: retire migration risk — it publishes
one chosen view of the redefined bytes as a JSON schema, freezing the
ambiguity into an API contract.

**[2] Rehosting** — Linux on x86 or z/Linux — works when Q1 and Q2 came out
favorable: source compiles, platform surface small. GnuCOBOL 3.x is the free
path (it compiles the language, not the platform); Micro Focus (OpenText
since 2023) Enterprise Server sells CICS/IMS API-subset emulators and a JCL
batch runtime, which is why serious rehosts buy it. The costs: re-testing
everything, plus EBCDIC→ASCII conversion — field lengths are in bytes, so
every PICTURE breaks if the encoding shifts (ch01).

**[3] Rewriting — the graveyard.** Public tombstones: TSB 2018, leaving the
shared Lloyds mainframe estate — 1.9 million customers locked out for days,
~£330 million by TSB's own disclosure plus a £48.65 million FCA/PRA fine
(2022); the Slaughter and May review found the migrated data was never
properly tested. LSE Taurus, 1993: a settlement replacement written off at
~£75 million before running live. The FAA's Advanced Automation System,
cancelled in 1994 after roughly $2.5 billion. Counterpoint: Capital One
exited its data centers to AWS between 2015 and 2020 — big migrations *can*
succeed, given ~a decade, a younger portfolio, unpublished cost. Both share
the mechanism: recovering the missing spec and reconciling against it, in
parallel, for years.

**[4] AI-assisted migration (2020s)** attacks the draft labor; syntax is the
easy 20%. Public corpora contain orders of magnitude less COBOL than Python
(COBOL is hundredths of a percent on public code hosting); the semantics
that matter live where models cannot look — REDEFINES views with no runtime
tags, 88-levels that are control flow wearing a data costume, ODO counts
whose length rules exist only in code, and load-bearing bugs (dirty
WORKING-STORAGE some downstream program has depended on since 1994). IBM's
watsonx Code Assistant for Z (GA 2024) is the flagship attempt: a pipeline —
analyze, refactor, transform to Java, validate — with vendor capability
claims and thin public evidence. LLMs produce *plausible* Java whose
differences from the COBOL are invisible without a reconciliation
harness — and the harness is the line item nobody budgets.

## The skills cliff

The demographics are real and the numbers soft: "average COBOL programmer
is 55" comes from the same vendor-survey lineage as the 220-billion-lines
figure — unfalsifiable, but directionally right in every shop's org chart.
The public demonstration was April 2020: New Jersey's governor announced on
live television that the state needed COBOL volunteers, because
unemployment systems built in the 1970s–80s were drowning. Note the
mechanism insiders saw: capacity limits, not code decay — fixed OCCURS
tables sized for 1970s caseloads (module 01 ch17), the memory
model deciding social policy 45 years later.

The reframing truth: **COBOL is easy to learn; mainframe engineering is
not.** The language is readable in days — MOVE, PERFORM, PICTURE. The job is
the platform: JCL (a mispunched DD card kills the batch window), VSAM (KSDS
control intervals), CICS (ch04), RACF, DB2 for z/OS, SDSF/ISPF, WLM — and
the elder skill, dump reading: PSW first (ch01), EBCDIC and COMP-3 nibble by
nibble (ch03). A two-week COBOL course makes someone who can read the
program but not run, debug, or deploy it. Proposals budgeting for "COBOL
developers" but no CICS systems programmers are already wrong — the scarce
skill is missing from the estimate.

## The control experiment: bugs move, they never disappear

Module 03 classifies bugs by mechanism but can only *observe* families. The
mainframe is the closest computer science has to a controlled trial: swap
the memory model, hold the workload constant, watch which families vanish.

Families that **cannot exist** in classic (pre-2002) COBOL: use-after-free
and double-free (nothing is ever freed — module 01 ch05's catalog opted out
of); fragmentation (no allocator — ch19); GC pauses (no collector —
ch12–16); strict-aliasing UB (aliasing is *declared* in one Data Division —
ch08).

Families that **grew in the vacated space**: ODO overflow, where a stale
count shifts every later field and lands an S0C7 (ch03); REDEFINES
cross-view corruption, where writing through one view silently changes what
the other reads; dirty storage, WORKING-STORAGE persisting between CICS
tasks — the quasi-reentrancy bug of ch04; subscript overrun writing past an
OCCURS budget into the next field (default NOSRRANGE); and the biggest
COBOL-native pandemic, Y2K — two-digit `PIC 9(6)` dates — with remediation
estimated at $300–600 billion (Gartner-lineage estimate; the true number is
unknowable).

The ledger balances: a language that eliminated the heap's bug catalog grew
its own in the vacated space. Choosing a memory model never chooses whether
you have bugs — only *which* bugs. Bugs move; they never disappear.

## What insiders know

- **The provenance problem with the big numbers.** 220 billion lines →
  Micro Focus surveys, 2016–2017 (Gartner said 180 billion in 1997);
  800 billion → consultant decks, no methodology; "$2–3 trillion/day" and
  "95% of ATM transactions" → the same lineage via a 2017 Reuters story.
  Insiders quote them for scale, never for budget.
- **REDEFINES is what actually breaks automated translation.** The compiler
  *knows* the aliasing (both views in one Data Division); a
  translator must assign one type to a region with two or three live
  interpretations chosen at run time by *other* programs — plus FILLER slack
  and 88-levels with no target-language analog.
- **The graveyard is real but specific.** TSB 2018 (~£330M + £48.65M fine,
  untested migrated data), Taurus 1993 (~£75M, never live), FAA AAS 1994
  (~$2.5B). They prove big-bang rewrites of live settlement systems fail —
  not that leaving is impossible (Capital One, 2015–2020).
- **"COBOL easy, mainframe hard" is a hiring filter.** Shops interview for
  JCL, VSAM, CICS, RACF, and dump reading; the language is a two-week
  on-ramp. Proposals that staff the first and not the second have
  mis-priced the skills cliff.
- **IBM's claims file.** "1 trillion transactions/day" (z15 announcement,
  2019), "99.999% availability," "70% of enterprise transactions" — vendor
  claims, no published methodology. The real capacity numbers in the
  building are MSU ratings, WLM policy, and the invoice.
- **Pricing is a modernization battlefield.** Mobile Workload Pricing,
  subcapacity, and collocated-application pricing keep wrapped mainframes
  competitive in TCO spreadsheets — insiders read each new scheme as IBM
  defending the wrap strategy.

## See it yourself

**Lab 1 — the ambiguity a translator faces.** Install GnuCOBOL 3.1.2+
(`sudo apt install gnucobol` or `brew install gnucobol`; it is *not*
preinstalled on most laptops — check `cobc --version`). No mainframe needed.
Save as `ambig.cob`:

```cobol
       IDENTIFICATION DIVISION.
       PROGRAM-ID. AMBIG.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-BLOB.
      *    view 1: the same 8 bytes as text
           05  BLOB-AS-TEXT   PIC X(8).
      *    view 2: the same 8 bytes as a record (offset 0, size 8)
           05  BLOB-AS-STRUCT REDEFINES BLOB-AS-TEXT.
               10  ACCT-NUM   PIC 9(6).
               10  ACCT-CHECK PIC X(2).
       PROCEDURE DIVISION.
           MOVE "001234XY" TO BLOB-AS-TEXT
           DISPLAY "as text  : [" BLOB-AS-TEXT "]"
           DISPLAY "as struct: acct=[" ACCT-NUM "] check=[" ACCT-CHECK "]"
           MOVE 987654 TO ACCT-NUM
           DISPLAY "as text  : [" BLOB-AS-TEXT "]"
           GOBACK.
```

```text
$ cobc -x -free ambig.cob && ./ambig
as text  : [001234XY]
as struct: acct=[001234] check=[XY]
as text  : [987654XY]
```

Three lines, whole chapter: one 8-byte region, two readings, both valid.
Line 2 read account 001234 plus check tag "XY"; line 3 shows the write
through *view 2* silently changed what any *view 1* program now reads. A
translator must pick `String` or `long`+`String` — whichever it picks breaks
whichever program used the other reading. Add a
`PIC S9(7) COMP-3` view and the same MOVE would be a data exception (S0C7 on
z/OS): the ambiguity is load-bearing, and there is no ground truth to
recover.

**Lab 2 — documented walkthrough: the z/OS Connect / CICS REST facade**
(not laptop-runnable; the wrap step as of CICS TS 5.6/6.1 and z/OS Connect
EE V3.x):

1. *CICS side.* The program is a COMMAREA transaction, quasi-reentrant
   (ch04). Define a `URIMAP` (CEDA or CICSPlex API): path `/accounts/{id}`,
   USAGE(PIPELINE), bound to the transaction. CICS's JSON tooling (the
   DFHJS2LS/DFHLS2JS lineage) generates the JSON↔buffer mapping from the
   COMMAREA copybook — and struggles: it wants *one* record layout, and
   REDEFINES plus ODO force hand-edited mappings. The archaeology problem
   is structural, even in IBM's tooling.
2. *z/OS Connect side.* Author a BAM (API metadata: REST operations, JSON
   schemas); point each operation at the CICS program and its copybook;
   generate the Service Archive (SAR); deploy to the Liberty-based z/OS
   Connect server, which invokes CICS over IPIC.
3. *What to notice.* No COBOL recompiled. JSON arrives, marshals into the
   01-level COMMAREA, the transaction runs untouched, the reply marshals
   back. The MSU bill is unchanged; the facade froze *one* interpretation
   of the redefined bytes into a published schema — that decision, not the
   plumbing, is where the risk lives.

## How to use this knowledge

- **Read a modernization proposal critically in one pass.** Find the line
  item for semantic recovery — copybook archaeology, REDEFINES resolution,
  ODO length rules, dirty-state inventory. Absent? The estimate is fiction:
  translation is the cheap 20%; archaeology is the cost.
- **Identify which menu item it really is.** Ask what happens to the MSU
  bill (a wrap keeps it), what the parallel-run plan is (TSB died on
  untested migrated data), and whether "AI-assisted" includes a
  reconciliation harness or just generated code and hope.
- **Discount the big numbers on cue.** When a deck says 800 billion lines
  and $3 trillion a day, ask for the methodology, then restate the decision
  without them. A case that needs folklore is a weak case.
- **Answer the interview question at insider depth.** "Why does COBOL
  survive?" is not nostalgia: O(1) fixed records with no heap failure modes
  (module 01 ch17), semantics that exist only in running code, a platform
  skills cliff — and skepticism about every headline number in the story.

## Connections

- **Module 01 ch17** — the no-heap model finalized: this chapter is its
  economic epilogue; the O(1) payoff and the schema-less cost are one
  design decision seen from both sides.
- **Module 08 ch01–ch04** — ch01's EBCDIC/PICTURE landmine is the rehosting
  cost; ch02's storage sections are the O(1) mechanics; ch03's
  REDEFINES/ODO are the blockers every translator hits; ch04's CICS is
  where the wrap facades and the dirty-storage bug class attach.
- **Module 03** — the bug-family taxonomy as a natural experiment: COBOL is
  the control arm where the heap families were removed and the vacated
  space colonized (ODO overflow, REDEFINES aliasing, dirty storage).
- **Module 11** — the durable-data cousin: fixed-record files are
  durability without self-description (schema in code), versus storage
  engines whose pages and catalogs carry their own meaning.

## Quiz

1. A consulting deck claims "800 billion lines of COBOL run $3 trillion of
   daily commerce." Give the provenance chain behind both numbers and the
   insider's one-sentence correction.
2. The lab program runs `MOVE "001234XY" TO BLOB-AS-TEXT`, then
   `MOVE 987654 TO ACCT-NUM`; a *different* program displays
   `BLOB-AS-TEXT`. What does it display, and which view's write produced
   the change?
3. A ch17-style table: `05 TAB-ENTRY PIC X(40) OCCURS 50000.` How many
   bytes does it occupy, where does element 50001 land under IBM Enterprise
   COBOL 6.x default `NOSRRANGE`, and why is this a budget failure rather
   than a leak?
4. A proposal says: "Wrap everything with z/OS Connect; zero mainframe
   changes." Name two things that improve, two costs that do *not*
   shrink, and the migration risk the wrap does not retire.
5. An AI migration converts
   `05 HDR-FLAGS PIC X. 88 FLAG-EURO VALUE "E".` into a Java `boolean euro`
   field. Name at least three semantic facts the translation lost.
6. Generated Java passes every unit test, but reconciliation shows some
   balances shifted by 11 bytes on short records. What COBOL mechanism did
   the translation miss, and where did its length rule live?
7. Why does this module call COBOL the control experiment for module 03?
   Name two bug families that cannot exist in classic COBOL (with the
   eliminating mechanism) and two that grew in the vacated space.
8. TSB's 2018 migration failed publicly (~£330M plus a £48.65M fine);
   Capital One exited its data centers between 2015 and 2020. What
   difference makes both consistent with this chapter's menu?

<details>
<summary>Answers</summary>

1. **Vendor-survey lineage; no count was ever performed.** 220 billion:
   Micro Focus surveys 2016–2017 (Gartner said 180 billion in 1997);
   800 billion: consultant decks, no methodology; "$2–3 trillion/day" and
   "95% of ATM transactions": the same lineage via a 2017 Reuters story.
   Correction: nobody ever counted the lines, and every figure came from
   someone selling compilers or migration — repeat for scale, never for
   budget.
2. **`[987654XY]` — view 2's write won.** The MOVE wrote digits through the
   `PIC 9(6)` view of bytes 0–5; `BLOB-AS-TEXT` is a declared view of the
   same bytes, so the text reading changed with no assignment to any text
   variable — the invisibility a translator cannot model.
3. **2,000,000 bytes (40 × 50,000); element 50001 writes into whatever
   follows the table.** OCCURS is a compile-time budget paid at load — the
   model's whole allocation contract (module 01 ch17). `NOSRRANGE` leaves
   the subscript unchecked, so the reference lands past offset 2,000,000
   and corrupts the next WORKING-STORAGE item. A leak is storage not
   returned; here storage was never dynamic — the budget was crossed (the
   2020 New Jersey story).
4. **Improved:** API access for modern consumers; reversibility — the
   riskiest asset (the logic) is untouched. **Unshrunk:** the MIPS/MSU
   bill and the platform skills requirement (CICS/VSAM/JCL engineers still
   operate what was wrapped). **Not retired:** migration risk — semantics
   stay dark; the wrap freezes *one* interpretation of the redefined bytes
   into a published schema: a decision, not a recovery.
5. **Any three of:** (a) the 88-level has *no storage* — a compile-time
   comparison of one byte, so `boolean euro` invents a nonexistent field;
   (b) `SET FLAG-EURO TO TRUE` writes the byte `"E"` — other programs can
   MOVE `"E"` directly, bypassing the "boolean"; (c) the field can hold
   values no 88 names, which a boolean cannot represent; (d) multiple 88s
   under one field are overlapping, non-exclusive views — not an enum.
6. **ODO — OCCURS DEPENDING ON.** Fields after the variable table sit at
   `base + (element size × count)` — 11 bytes per element in the ch17
   shape — so the length rule lives in code that sets the control field
   first. The rule is not in the data, so the model cannot see it; fixed
   offsets in the generated Java mean every record with an unexpected count
   reads shifted — ch03's S0C7 family in a new language.
7. **Because it removed the heap and kept the workload.** Cannot exist:
   use-after-free/double-free (nothing is ever freed pre-2002),
   fragmentation (no allocator — offsets fixed at load), GC pauses (no
   collector to pause). Grew in the vacated space: ODO overflow (a stale
   count shifts later fields — the manual length rule replaces the
   allocator's bounds), REDEFINES cross-view corruption (declared aliasing
   makes the corruption *legal*), dirty storage (static persistence across
   CICS tasks replaces lifetime bugs). Bugs move; they never disappear.
8. **TSB gambled without the harness; Capital One ran the full menu.** TSB
   attempted a big-bang platform move of live settlement transactions with
   inadequate parallel testing of migrated data — the graveyard's exact
   mechanism. Capital One spent ~five years migrating application by
   application, systems running throughout, a younger portfolio, cost never
   published. Both fit the tree: rewrites without multi-year reconciliation
   fail; survivors fund the harness — or never had the missing-spec problem.

</details>
