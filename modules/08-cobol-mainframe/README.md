# Module 08 — COBOL & the mainframe world

> The memory model with no heap, no garbage collector, and no malloc: fixed
> records carved out at compile time, storage sections with exact semantics,
> a type system written in PICTURE clauses, and a machine whose protection model
> predates virtual memory as you know it. The world's money still runs on this —
> and almost nobody under 50 can read it. Build it from the metal up.

**5 chapters · one final exam · prerequisite: module 01 chapter 17 (COBOL's memory
model) — this module is that chapter expanded to working depth. GnuCOBOL makes
every lab runnable on a laptop.**

## How to study it

Chapter 01 is the machine (z/Architecture — where your bytes actually live);
chapters 02-03 are the language's storage semantics and the PICTURE byte-layout
system (compute layouts by hand, verify in the debugger); chapter 04 is CICS
(the transaction world that owns the mainframe "heap"); chapter 05 is why it all
survives and what migration actually risks. Labs: GnuCOBOL (`cobc`) for language
chapters; the layout labs are pure hand computation + verification.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The z-machine](chapters/01-z-machine.md) | z/Architecture, addressing modes, storage keys, PSW — the metal under COBOL. |
| 02 | [The storage sections](chapters/02-cobol-storage.md) | WORKING-STORAGE, LOCAL-STORAGE, LINKAGE — allocation with exact semantics. |
| 03 | [PICTURE & layout arithmetic](chapters/03-picture-layout.md) | PICTURE as a byte-level type system: COMP, COMP-3, REDEFINES, OCCURS. |
| 04 | [CICS: the transaction heap](chapters/04-cics.md) | GETMAIN/FREEMAIN, storage protection, quasi-reentrancy, above/below the line. |
| 05 | [Why it survives](chapters/05-survival.md) | The economics, the migration trap, what 60 years of fixed records taught everyone. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the z-machine.** What a mainframe physically is: frames/drawers,
hot-spare CPUs that engage on failure (the "call home before you notice" folklore),
memory with lockstep/RAID-like redundancy — reliability as the design *objective*
(vs consumer hardware's cost objective). z/Architecture essentials for the memory
model: 16 general registers; the three addressing modes — 24-bit (the 16MB line:
why 1964's 24-bit heritage persisted into the 1990s via common areas and AMODE
switching), 31-bit (the "below the bar" world), 64-bit (the "2GB bar" and above)
— draw the address-space diagram with the line and the bar. The PSW (program
status word) as the mother of all registers: what it encodes (condition code,
addressing mode, instruction address, interruption masks) and why insiders read
it first in any dump. Storage keys: a 4-bit key per 4KB page, matching the PSW's
key — protection *before* virtual memory, still active (why the PSW key vs page
key mismatch is the S0C4 abend inside CICS — set up chapter 04). Address spaces,
dataspaces, and cross-memory access (the ASN/access-register mode sketch). EBCDIC
vs ASCII: the code-page reality, translation tables, why "just convert to UTF-8"
destroys PICTURE-level record parsing (field lengths are in *bytes*, and EBCDIC
is not byte-compatible — the migration landmine previewed for ch05). PR/SM LPARs
→ z/VM → today's containerization lineage (the insight: IBM partitioned hardware
before anyone else virtualized it). Lab: sysinfo on a real system (or the
documented walkthrough); DUMP reading excerpt with the PSW located and decoded.

**Chapter 02 — the storage sections.** The three sections with *exact* semantics,
each contrasted with the familiar: WORKING-STORAGE = static storage (allocated at
program load, persists across calls — the C analog is a global; initialization
via VALUE clauses happens once at first entry), LOCAL-STORAGE = automatic storage
(the stack analog — allocated per invocation, reinitialized; why recursive
programs require it and COBOL-85 made recursion opt-in), LINKAGE = caller-owned
storage (no allocation at all — parameter-passing only; the USING contract and
why reading a LINKAGE item before the caller sets it is the classic S0C4).
Level numbers as a tree: 01 group, 49/66/77/88 special levels (77 = standalone
elementary from the punched-card era; 88 = condition names — *no storage at all*,
a compile-time comparison — the insider's "88s are the closest COBOL gets to
enums"). The FILLER trap (unnamed slack bytes — uninitialized by convention).
Initialization cost as the historical design driver (why old COBOL avoids
reinitializing: the CPU was the scarce resource, and VALUE-init of big tables
was visible on the meter). Language Environment (LE) storage underneath: cell
pools and the CEE storage model (GETMAIN under the covers — the "there is a
heap, the language just refuses to show it to you" punchline; GnuCOBOL maps
this onto malloc — the lab's transparency moment). Lab: a GnuCOBOL program
printing addresses of WORKING-STORAGE vs LOCAL-STORAGE items across recursive
calls; observe static vs stack placement with a debugger or by printing
addresses.

**Chapter 03 — PICTURE & layout arithmetic.** PICTURE as a byte-level type
system — derive, don't memorize: DISPLAY (default): one byte per character;
zoned decimal = EBCDIC digits with the sign in the zone of the *last* byte
(sign conventions: C=+, D=-, F=unsigned — the reason adding raw zoned
"numbers" produces garbage and INSPECT/COMPUTE must convert). COMP/BINARY:
binary sized by digit count (1-4 digits → 2 bytes, 5-9 → 4, 10-18 → 8 — the
rules that look arbitrary until you see them as a packing grid; SYNC/aligned
variants and the SLA alignment padding). COMP-3 packed decimal: two digits per
byte, sign nibble at the end (C/D/F) — compute record sizes by hand; why
banking loves it (exact decimal arithmetic — the 0.1+0.2 lesson from module 03
ch04, solved in 1959). COMP-1/2 (native float — rare in banking for the same
reason). REDEFINES as unions: same offset, different interpretation (the rules:
must not contain VALUE on the redefining item except 88s, no variable-size
redefinition — and the *insight*: REDEFINES is the field where 40 years of
programmer intent evaporates for any automated translation — set up ch05).
OCCURS: fixed tables (compute the byte stride; ODO — OCCURS DEPENDING ON — as
the variable-length record mechanism: fields after an ODO field shift with the
count, and every length check is *manual*, the classic overflow surface).
88-level condition names (compile-time comparisons; the "poor man's enum").
The chapter's core skill: given a record description, compute every offset and
size by hand — then verify with a GnuCOBOL program that lays out the same
record and prints addresses. Lab: exactly that — a 15-field record with
DISPLAY/COMP/COMP-3/REDEFINES/OCCURS, hand-computed table vs program-verified
table.

**Chapter 04 — CICS.** What CICS is: a transaction monitor that multiplexes
thousands of users across a bounded set of OS processes (the transaction =
quasi-process: a TASK borrows a TCB, runs your program, gives it back —
programs must be QUASI-REENTRANT: no dirty WORKING-STORAGE between tasks,
which forces the storage discipline that follows). Program loading: CICS loads
programs once and shares them across tasks (the same COW-style sharing logic
as Android's Zygote — module 05 ch01's pattern on 1969 hardware: the insight
that the *same* memory economics produced the same design twice, 40 years
apart). The storage that tasks need dynamically: GETMAIN/SET (the mainframe
malloc) and FREEMAIN — above/below the 16MB line variants (the line returns as
a real allocation constraint: old code's 31-bit assumptions), storage
protection keys (user key 8 vs CICS key storage — the S0C4 "storage
violation" as CICS's memory-safety net: it *catches* the wild write that C
would have let through — the historical irony to derive), subpools and the
DSA hierarchy, task-lifecycle cleanup (CICS frees task storage automatically
on task end — the anti-leak design; leaks = GETMAIN'd storage held past task
end in long-running storage class). The EIB (EXEC Interface Block) as the
per-task control record. TS queues and auxiliary storage (spill-to-disk
data sharing). The reliability folklore: 0-downtime (rolling CICS region
upgrades), RBO, and the "never reboot" culture (the uptime numbers).
Lab: GnuCOBOL simulation of a quasi-reentrant program failing across
simulated tasks (static storage carrying state between invocations —
reproduce the classic production bug class on a laptop).

**Chapter 05 — why it survives.** The economics honestly stated: the transaction
throughput a modern mainframe delivers (per-frame MIPS and the I/O subsystem
that makes databases crawl to it — state IBM's public claims and mark them as
vendor claims), the ~800 billion lines of COBOL in daily service and the
"trillions of dollars per day" folklore (state the commonly cited estimates,
flag their provenance — the insider's skepticism about the magic numbers).
The maintenance trap: fixed records = O(1) everything (zero allocation, zero
fragmentation, zero GC — module 01 ch17's payoff finalized), but the *semantic*
record is the problem: 40 years of REDEFINES means the data has no
self-describing schema (every byte's meaning lives in code and microfiche —
the real reason "just rewrite it" fails: the spec is gone). The modernization
menu: wrapping (z/OS Connect / CICS REST-JSON facades — the majority strategy),
rehosting (Linux + GnuCOBOL/Micro Focus — works when you can recompile),
rewriting (the graveyard: cite the classic failed public migrations and their
costs), AI-assisted migration (2020s: why LLMs struggle here — the semantics
live in REDEFINES and 88-levels, not in syntax). The skills cliff: the
retirement demographic, why "COBOL is easy to learn, mainframe engineering is
not" (the job is the platform: JCL, VSAM, CICS, RACF, dump reading). The final
insight: COBOL is the control experiment for module 01 — a world with no heap,
where whole bug classes (UAF, fragmentation, GC pauses) *cannot exist* — and
its own bug classes (ODO overflow, REDEFINES aliasing, dirty storage) grew in
the vacated space. Bugs move; they never disappear.

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **Reading a mainframe job posting without flinching**: storage sections,
  ABENDs, GETMAIN, above/below the line — decoded, not mystified.
- **Data-migration literacy**: you can explain *why* COBOL→anything is a
  semantic archaeology project, not a translation task.
- **The deepest perspective on module 01**: a fully worked alternative memory
  model — what it buys (O(1) records, entire bug classes eliminated) and what
  it costs (schema-less data, manual everything).
