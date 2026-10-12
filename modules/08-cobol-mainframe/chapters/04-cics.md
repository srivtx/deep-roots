# Chapter 04 — CICS: the transaction heap

> **The 30-second version.** COBOL was born without a heap — then the
> 1960s arrived with thousands of users per machine and no way to give each
> an OS process. CICS (first shipped 1968) is the answer: one process
> multiplexing thousands of *tasks* over a small set of threads, sharing one
> copy of each program between them. That choice forces everything else:
> programs must be *quasi-reentrant* (no dirty WORKING-STORAGE between
> tasks), per-task storage comes from `GETMAIN`/`FREEMAIN`, and hardware
> storage keys — not the language — catch the wild writes: the heap COBOL
> pretended not to have, plus the memory-safety net C never got.

## A thousand users, one process

Start from economics, not product. In 1968 a big machine has a few MB of
storage; an OS process is expensive state. Airline reservations wants 5,000
agents at 3270 terminals: 5,000 processes impossible, 5,000 serial users
absurd (each agent *thinks* for seconds between keystrokes). The design
writes itself:

- One process — a **CICS region** — owns everything.
- Each request becomes a **TASK**: an instance of a **transaction** (the
  type). A task is a control block, scratch area, screen buffer — a few KB,
  not a few MB; it runs for milliseconds and ends.
- The task is a *quasi-process*: identity and private storage, but no
  address space. A task **borrows a TCB** (a z/OS thread), runs the
  program's instructions, gives it back. Classic CICS runs almost all
  transactions on one OS thread that IBM literally names the **QR TCB** —
  the *quasi-reentrant* TCB; CICS schedules inside it, and z/OS never sees
  your transaction.

```text
        one CICS region = one z/OS address space (one OS process)
+---------------------------------------------------------------------+
| CICS nucleus: dispatch tables, pools, control blocks        [key 0]  |
|                                                                     |
|  programs loaded ONCE, shared by every task (read-mostly):         |
|   +-------------+  +-------------+  +-------------+                 |
|   |   PAYUPDT   |  |   BALINQ    |  |   MENU01    | <- ONE copy    |
|   | (quasi-     |  |             |  |             |    per program |
|   |  reentrant) |  +-------------+  +-------------+                 |
|   +-------------+                                                     |
|        ^            ^            ^   borrow/return at each           |
|   +----+----+  +----+----+  +----+----+  EXEC CICS command          |
|   | task 114|  | task 327|  | task 341|                             |
|   | EIB     |  | EIB     |  | EIB     | <- per-task storage [key 8] |
|   | DSA     |  | DSA     |  | DSA     |   (GETMAIN'd, auto-freed)   |
|   | TIOA    |  | COMMAREA|  | ...     |                             |
|   +---------+  +---------+  +---------+                             |
|        \            \            \                                  |
|         ======== QR TCB (the loan desk for threads) ============    |
+---------------------------------------------------------------------+
```

Two users running the *same* transaction run the *same program bytes* in
one process, interleaved — keep a total in WORKING-STORAGE, and task 327's
total and task 114's total are **the same bytes**. CICS therefore demands
**quasi-reentrancy**: re-enterable *at EXEC CICS command boundaries*, where
CICS may suspend you and dispatch another task into the same program.
Per-task state lives in the COMMAREA (per-task storage received through
LINKAGE, capped at 32 KB in classic CICS TS) or in GETMAIN'd storage —
never in WORKING-STORAGE. Chapter 02 gave the semantics; here, "initialized
once at load" becomes a production incident.

## One copy of the program, 40 years before Zygote

Why load PAYUPDT once and share it? The arithmetic that killed 5,000
processes: 200 live programs × one 50 KB copy is 10 MB; × 5,000 tasks is
250 MB. Sharing collapses that to one copy each — *read-mostly*: code
bytes don't change, so nobody needs per-task pages of them; program text
becomes a shared read-only asset, only data private.

You have seen this design in [module 05 chapter
01](../../05-android-internals/chapters/01-boot-zygote.md): Android preloads the
Zygote process; every app forks from it, sharing the preloaded pages
copy-on-write. CICS is the same economics — share the expensive common
pages, privatize the per-instance bytes — on late-1960s hardware with no
fork() and no COW, 40 years before Zygote. Mechanisms differ (CICS shares
*between tasks in one address space*; Zygote between *processes via the
MMU*), but the insight is identical: when RAM is scarce, the common part of
every instance exists once.

Sharing's cost is the reentrancy contract, paid on every task switch, in
every program, forever — hence the discipline's name, compile option
(RENT), and command support for refreshing a live program (NEWCOPY, below).

## The heap CICS built: GETMAIN, the line, the DSA

COBOL records are compile-time-known (chapter 02); tasks are not:
unknowable count, transaction-length lifetime. That
is the heap problem of [module 01 chapter
05](../../01-memory-models/chapters/05-the-heap.md) — allocation not
nested, not predictable — so CICS grows a heap API on the MVS storage
manager:

```cobol
       EXEC CICS GETMAIN
            SET(WS-BUF-PTR)          *> the "malloc"
            FLENGTH(WS-BUF-LEN)      *> size in bytes
            LOC(ANY)                 *> or LOC(BELOW) -- see the line
       END-EXEC.

       EXEC CICS FREEMAIN
            DATA(WS-BUF-PTR)         *> pointer only, like free()
       END-EXEC.
```

`GETMAIN` is malloc with two mainframe twists. Returned storage is
**dirty** — the previous owner's bytes — unless you ask for initialization
(`INITPIC`, CICS's calloc). And the address space has a wall in it. The
**16MB line** (0x01000000) from [module 08 chapter
01](../chapters/01-z-machine.md) is a real allocation constraint:
`LOC(BELOW)` returns storage addressable by 24-bit code (AMODE 24, the 1964
heritage); `LOC(ANY)` may return an address above the line, valid only to
AMODE-31 programs. Old code holds addresses in fields or registers that
silently truncate bit 24 and up — hand a `LOC(ANY)` pointer to AMODE-24
code and the store lands 16MB below where you think. Sixty-year-old
assumptions, enforced at every GETMAIN. (CICS TS 6.x, the 2020s release,
extends the pattern past the 2GB "bar" for 64-bit storage.)

Every GETMAIN block is a **DSA** (Dynamic Storage Area) with a prefix
header — owner, size, subpool — chained into a hierarchy CICS can walk. A
**subpool** is the MVS mechanism bundling attributes (storage key, location,
fetch-protection) with a request; CICS uses different subpools for user-key
task scratch, CICS-key control storage, and long-lived shared storage. And
the malloc déjà vu: FREEMAIN needs no size because the DSA prefix carries
it, exactly like `free()` reading the glibc chunk header before your
pointer ([module 01 chapter
19](../../01-memory-models/chapters/19-allocators-deep.md)).

## Keys: the safety net C never had

Now the reversal of C's lesson. A task runs with PSW
key 8 (user key); CICS's own storage — queues, control blocks, the
dispatcher — is CICS-key (key 0). The 4-bit keys on every 4KB page
([module 08 chapter 01](../chapters/01-z-machine.md)) are checked *by
hardware, on every store*. A task that runs off the end of its DSA and
writes into CICS-key storage takes a protection exception immediately:
interruption code 4, the S0C4 program check.

CICS catches it (with `STGPROT=YES`, the SIT parameter enabling key
separation): storage-violation report, transaction dump, the *task* abends
— the user sees ASRA — and the region keeps running. In C, the same
overwrite into the next malloc chunk stops nothing; the corruption
surfaces later, in someone else's data.

The irony, derived: CICS runs thousands of users' code in *one process*,
so it cannot afford a flat trust domain — untrusted task code must be
fenced from the monitor by the only thing below the OS, the hardware keys.
Unix made the opposite trade: one process per client, protection = the
process boundary. Result: the 1969 design has *finer-grained* memory safety
inside one address space than the C program on your laptop in 2025 — the
S0C4 mainframers fear is what C programmers call "undefined behavior, no
symptoms", except it *stops*.

## Task death: the garbage collector, the EIB, and what survives

A task ends and CICS frees everything the task owns: every task DSA,
the terminal buffer, LE storage. Per-task storage **cannot leak past task
end by construction** — a garbage collector with process granularity and
zero cost, no tracing, no pauses; ownership dies with the task. The heap's
original sin (lifetimes not nested, module 01 ch05) is solved by *making
lifetimes nested by force*.

What leaks is storage that deliberately outlives the task: GETMAIN
`SHARED`, or CICS-key storage — free those yourself, or the region grows
until short-on-storage. The other classic leak isn't memory: **temporary
storage queues** (`WRITEQ TS QUEUE('BALANCES') FROM(...)`) hold shared
scratch; `MAIN` keeps them in the region's pool, `AUXILIARY` spills them to
a VSAM data set on DASD — disk as the unbounded backing store for
inter-task data sharing, with a `DELETEQ TS` someone must issue.
Abandoned AUXILIARY queues grow the data set for months unnoticed.

The EIB ties the task together: the **EXEC Interface Block** (copybook
`DFHEIBLK`, one per task, automatically addressable) is the task's control
record — `EIBTRNID` (transaction id), `EIBTASKN` (task number: match a dump
to a ticket), `EIBCALEN` (COMMAREA length; zero = first entry of a
pseudo-conversation), `EIBRESP`/`EIBRESP2` (last command's return codes).
The per-task register file: the real registers belong to the borrowed TCB,
not you.

This machinery is why the folklore says CICS never comes down. Programs are
swapped in a live region (`CEMT SET PROGRAM(PAYUPDT) NEWCOPY`; `PHASEIN`
for a lazier swap), resources installed online (CEDA/RDO), upgrades rolled
region-by-region: drain a region's transactions (sysplex cohorts take the
traffic), stop it, maintain, restart, repeat — no user sees an outage. If a
region dies, emergency restart backs out the in-flight transactions'
uncommitted work before reopening the files — old hands call the
roll-back-out "RBO" — so a crash costs seconds, not integrity. Regions
accumulate months of uptime, z/OS LPARs years; operators treat an IPL the
way a Unix admin treats a kernel panic. (Folklore with a solid core:
continuous availability was the design goal.)

## What insiders know

- **Quasi-reentrant means "reenterable at command boundaries"** — the
  precise contract, and the interview trap: not "reentrant" (arbitrary
  instruction), not "serializable". WORKING-STORAGE carries nothing between
  tasks or is INITIALIZE'd at entry; per-task state lives in the COMMAREA
  (32 KB cap) or GETMAIN'd storage. Since Language Environment (early
  1990s), RENT programs get task-scoped static storage in the LE enclave —
  the pre-LE bug below is why the rule survives.
- **ASRA is the S0C4 wearing a task costume**: the user sees ASRA; in the
  dump it is S0C4, PSW key 8, a store into CICS-key storage — storage
  violation; `STGPROT=YES` is the enabling SIT parameter.
- **The 16MB line is a live allocation decision, not history**:
  `LOC(ANY)` vs `LOC(BELOW)` on every GETMAIN — AMODE-24 callers truncate
  high-order bits, so an above-the-line pointer in old code writes 16MB
  low; the keys catch it only if the victim page is CICS-key.
- **GETMAIN hands you dirty bytes by default** — no zeroing without
  `INITPIC`; with FILLER (chapter 02), this is the mainframe face of
  [module 03 chapter
  02](../../03-software-bugs/chapters/02-memory-bug-family.md).
- **Task-end auto-cleanup covers only task storage.** The real leaks:
  `SHARED` GETMAINs never FREEMAIN'd, CICS-key storage, AUXILIARY TS queues
  never deleted — watch storage statistics and TS data set size, not
  abend count.
- **The QR TCB is workhorse and bottleneck** — transactions queue for one
  OS thread; Java/DB2 offloads to open TCBs (modern CICS TS); "queued for
  the QR TCB" is a real diagnosis.
- **NEWCOPY semantics**: `NEWCOPY` loads the fresh copy once current tasks
  drain; `PHASEIN` swaps without the wait. A fix deployed without an outage
  is this one command.

## See it yourself

No mainframe required — the bug class reproduces in one GnuCOBOL file,
mapped 1:1 onto the region diagram: the driver is the region, `CALL`
without `CANCEL` is the shared program copy (WORKING-STORAGE persists
across calls — pre-LE semantics), and the block passed `USING` is the
per-task COMMAREA. Availability here: `command -v cobc` → **not installed**
(no compiler in this sandbox; `apt install gnucobol`). The program is plain
COBOL-85; the output is deterministic, hand-verified against a C mirror.
Run `cobc -x cicslab.cob && ./cicslab` to see it live.

```cobol
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CICSLAB.
      * The "region": loads TRANPGM once; three tasks share the copy.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-TASK             PIC 9(4) VALUE ZERO.
       01  WS-ACCT-TABLE.
           05  FILLER          PIC X(4) VALUE "A001".
           05  FILLER          PIC 9(3) VALUE 100.
           05  FILLER          PIC X(4) VALUE "A002".
           05  FILLER          PIC 9(3) VALUE 250.
           05  FILLER          PIC X(4) VALUE "A003".
           05  FILLER          PIC 9(3) VALUE 040.
       01  WS-TAB REDEFINES WS-ACCT-TABLE.
           05  WS-ENTRY OCCURS 3 TIMES.
               10  WS-ACC-NO   PIC X(4).
               10  WS-AMT      PIC 9(3).
       01  WS-COMMAREA.                *> per-task storage (the COMMAREA)
           05  CA-ACC-NO       PIC X(4).
           05  CA-AMOUNT       PIC 9(3).
           05  CA-TOTAL        PIC 9(9).
       PROCEDURE DIVISION.
       MAIN-PARA.
           DISPLAY "CICS simulator: TRANPGM loaded ONCE, shared by tasks"
           PERFORM VARYING WS-TASK FROM 1 BY 1 UNTIL WS-TASK > 3
               MOVE WS-ACC-NO(WS-TASK) TO CA-ACC-NO
               MOVE WS-AMT(WS-TASK)   TO CA-AMOUNT
               MOVE ZERO               TO CA-TOTAL
               CALL "TRANPGM" USING WS-COMMAREA
               DISPLAY "TASK " WS-TASK " ACCT " CA-ACC-NO
                     " input=" CA-AMOUNT
                     " got=" CA-TOTAL
           END-PERFORM
           STOP RUN.
       END PROGRAM CICSLAB.

       IDENTIFICATION DIVISION.
       PROGRAM-ID. TRANPGM.
      * The transaction program. ONE WORKING-STORAGE copy, shared
      * by every task: VALUE clauses fire once, at "load".
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-RUN-TOTAL        PIC 9(9) VALUE ZERO.
       LINKAGE SECTION.
       01  DFHCOMMAREA.
           05  CA-ACC-NO       PIC X(4).
           05  CA-AMOUNT       PIC 9(3).
           05  CA-TOTAL        PIC 9(9).
       PROCEDURE DIVISION USING DFHCOMMAREA.
       TRAN-ENTRY.
      * THE BUG: WS-RUN-TOTAL is never reinitialized. Its VALUE
      * ZERO already fired -- at program load, before task 1.
           ADD CA-AMOUNT TO WS-RUN-TOTAL
           MOVE WS-RUN-TOTAL TO CA-TOTAL
           GOBACK.
       END PROGRAM TRANPGM.
```

Output:

```text
CICS simulator: TRANPGM loaded ONCE, shared by tasks
TASK 0001 ACCT A001 input=100 got=000000100
TASK 0002 ACCT A002 input=250 got=000000350
TASK 0003 ACCT A003 input=040 got=000000390
```

Task 1 entered with the fresh copy — *correct by luck*; task 2's customer
is billed task 1's total too; task 3 sees the whole history. Two fixes,
both one line:

- `INITIALIZE WS-RUN-TOTAL` at `TRAN-ENTRY` — the classic discipline;
  each `got=` becomes that task's input.
- `CANCEL "TRANPGM"` after each task — the program back at loaded state:
  effectively what LE gives a RENT program per task, and why the bug class
  aged out after ~1991.

Notice: the failure needs *sharing plus sequencing*, not concurrency —
why single-user test regions never reproduce it. The real commands, for a
real region (CICS TS):

```cobol
       EXEC CICS RETURN
            TRANSID('PAYU')
            COMMAREA(WS-COMMAREA)
       END-EXEC.
      * pseudo-conversation: end the task, state survives in the commarea;
      * next input re-enters PAYU with it. First entry of the chain
      * is detected with:  IF EIBCALEN = 0  ...

       EXEC CICS WRITEQ TS
            QUEUE('BAL' || CA-ACC-NO)
            FROM(WS-REC)
            LENGTH(LENGTH OF WS-REC)
            AUXILIARY
       END-EXEC.
      * inter-task sharing, spilled to DASD; DELETEQ TS reclaims it.
```

On a live region, `CEMT INQUIRE PROGRAM(PAYUPDT)` answers with the
program's status (enabled, usage count); `CEMT SET PROGRAM(PAYUPDT)
NEWCOPY` moves the next task onto the current library version — no stop.
The storage-violation report on an ASRA names the keys, the faulting PSW,
and the DSA chain: an ASan report produced by hardware, in 1969.

## How to use this knowledge

- **"Wrong data for the wrong customer" reports**: intermittent
  multi-customer bleed with no abend means shared dirty storage — check
  compile options (RENT?) and entry-path initialization before touching
  business logic; the lab is your repro.
- **Reading an ASRA dump**: find the PSW, read key and AMODE (chapter
  01's skill), find the store's target in the DSA chain — off-by-one past a
  COMMAREA, or 24-bit truncation of a `LOC(ANY)` pointer?
- **Leak triage**: region storage growing monotonically for weeks is
  nobody's GC problem (there is none) — audit `SHARED` GETMAINs, CICS-key
  GETMAINs, TS queues without deleters.
- **Interview answer that lands**: CICS multiplexes thousands of tasks over
  one address space — quasi-reentrant programs, GETMAIN storage under the
  16MB-line constraint, hardware keys catching wild writes as S0C4: the
  memory-safety net C never had.

## Connections

- **Module 01 ch05 (the heap)**: GETMAIN/FREEMAIN is malloc/free with owner
  tags; task-end cleanup is what tracing GCs approximate.
- **Module 01 ch17 (COBOL's memory model)**: the no-heap language meets the
  environment that forces a heap on it.
- **Module 01 ch19 (allocators)**: the DSA prefix mirrors glibc's chunk
  header.
- **Module 05 ch01 (Zygote)**: share-the-common, privatize-the-per-instance
  — same economics, 1968 vs ~2008, no fork vs COW fork.
- **Module 08 ch02 (storage sections)**: WORKING-STORAGE's static semantics
  are why quasi-reentrancy bites; LOCAL-STORAGE is the per-invocation fix.
- **Module 08 ch03 (PICTURE & layout)**: records written into GETMAIN'd
  buffers — an ODO miscount writes past the DSA and meets the keys.
- **Module 08 ch05 (why it survives)**: reliability folklore as a product
  feature migration must not quietly lose.
- **Module 03 ch02 (uninitialized memory)**: dirty GETMAIN bytes and FILLER
  are the mainframe branch of that bug class.

## Quiz

1. An airline runs 5,000 concurrent agents on one CICS region. Why not
   5,000 z/OS processes, and what contract does that choice impose on every
   program in the region?
2. `GETMAIN SET(P) FLENGTH(40000) LOC(ANY)` returns `P = X'01800000'`. The
   caller is compiled AMODE 24 and stores 8 bytes at offset 0 through a
   24-bit register. Predict the address written, and what decides whether
   anyone notices.
3. In the lab program, a fourth task arrives with `input=17` after the
   three tasks shown. What does its `got=` field display, and why that
   number?
4. Production incident: a balance-inquiry transaction intermittently shows
   amounts belonging to other customers. No abends. Single-user test never
   reproduces it; it appears only under peak load; it vanishes after a
   region restart and creeps back. Diagnose the mechanism and give the two
   one-line fixes.
5. A login transaction issues `GETMAIN SHARED FLENGTH(1024)` and FREEMAINs
   it only on clean logoff; 30% of users just close their browser. Predict
   the region's storage profile over a month, and why task-end cleanup does
   not save you.
6. A transaction with `STGPROT=YES` abends ASRA; the dump shows PSW
   key 8, a store, and a target in CICS-key storage. What event produced
   the ASRA, and why would the equivalent C bug produce *no* symptom?
7. Android's Zygote (module 05 ch01) and CICS's program loading solve the
   same problem with the same shape. State the shared economics, and the one
   structural difference in how the two systems keep instances from
   corrupting the shared copy.
8. `FREEMAIN` takes only a pointer — no length, like `free()`. What does
   CICS keep, and where, that makes this sufficient? Name the glibc
   structure playing the same role (module 01 ch19), and one field CICS's
   version records that glibc's does not.

<details>
<summary>Answers</summary>

1. **Process state is too expensive to multiply by users** — address space,
   control blocks, kernel bookkeeping per agent at 1968 prices, versus a
   few-KB task block on a bounded set of TCBs. The imposed
   contract is *quasi-reentrancy*: one shared copy of each program means a
   program must be re-enterable at every EXEC CICS command boundary, with
   no per-task state in WORKING-STORAGE.
2. **X'00800000'** — the 24-bit register drops bit 24 and up, so the store
   lands exactly 16MB below the target. The storage keys decide whether
   anyone notices: a writable user-key page means silent corruption of
   another task's data; a CICS-key page means a protection exception, an
   ASRA, and a storage-violation dump. Fix the class, not the instance.
3. **`got=000000407`** — 100 + 250 + 40 + 17. `WS-RUN-TOTAL` is one shared
   copy whose VALUE ZERO fired once, at load; every task accumulates into
   it, so the fourth task reads the whole history of the program copy.
4. **Dirty shared WORKING-STORAGE.** Peak load interleaves tasks through
   the same program copy, so task B reads task A's residue; single-user
   test cannot sequence two tasks; a restart re-fires the VALUE clauses —
   hence vanishes, then creeps back. Fixes: INITIALIZE the state-carrying
   items at entry, or keep per-task state in the COMMAREA or GETMAIN'd
   storage. (Post-LE: verify the program is compiled RENT.)
5. **Monotonic growth — roughly 0.3 × logins × 1 KB per day, never
   reclaimed — into short-on-storage.** Task-end cleanup frees only
   task-owned storage; `SHARED` deliberately survives task end, so a
   forgotten FREEMAIN is a region-lifetime leak. Fix: FREEMAIN from an
   abend/exit handler, or a terminal-keyed TS queue with an expiry sweeper.
6. The store ran with PSW key 8 into a CICS-key page; hardware raised
   interruption code 4 (the S0C4); CICS trapped it, wrote the report and
   dump, abended the task as ASRA, and the region survived. In C the same
   pointer writes into the next chunk — no keys in a flat address space —
   so it lands silently and surfaces as someone else's crash, later.
7. **RAM is scarce, so the read-mostly common pages exist once** — Zygote
   preloads the runtime for every app; CICS loads each program once per
   task. Difference: Zygote isolates instances with the MMU
   (COW-private copies per fork — protection for free); CICS has no fork,
   so isolation is a programming discipline backed by storage keys.
8. CICS keeps each block's **DSA prefix** — a header recording size,
   subpool, and *owner task*, chained into the DSA hierarchy; FREEMAIN
   reads it, task-end cleanup walks it. The glibc parallel is the chunk
   header (size + flags, 8-16 bytes before user data on 64-bit glibc).
   CICS's extra field is **ownership** — what makes automatic task-end
   reclamation possible; glibc chunks have no owner.

</details>
