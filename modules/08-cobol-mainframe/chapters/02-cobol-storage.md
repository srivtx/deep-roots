# Chapter 02 — The storage sections

> **The 30-second version.** COBOL's Data Division has three storage sections, each
> answering one question: *who owns these bytes, and when are they born?* WORKING-STORAGE
> is static — one copy at first activation, persisting across every CALL: the C analog
> of a global, `VALUE` applied exactly once. LOCAL-STORAGE is automatic — a fresh,
> reinitialized copy per invocation: the stack-frame analog, and why recursion is opt-in
> since 1985. LINKAGE allocates nothing — an addressability contract over the *caller's*
> bytes; reading a LINKAGE item whose address was never set is the classic S0C4.
> Underneath is a real allocator (LE cell pools, malloc): there is a heap; the language
> just refuses to show it.

## One question, three answers: who owns these bytes?

Strip any language to the metal: "memory management" is three decisions per
variable — *when is storage allocated, when released, who may touch it in between?*
COBOL's 1959 designers enumerated the useful answers and made each a section of the
Data Division:

```text
COBOL section        C analog                      where the bytes live
-----------------   ---------------------------   ----------------------------------
WORKING-STORAGE     int calls = 0; at file scope  one static copy, made when the
                    (a global)                    program activates; lives for the
                                                  run unit. VALUE applied ONCE.
LOCAL-STORAGE       int x; inside a function      a fresh copy per invocation,
                    (an automatic local)          released at GOBACK. VALUE applied
                                                  EVERY invocation. (COBOL-85.)
LINKAGE             int *p parameter, used        NO storage. A list of addresses
                    without copying               into memory someone else owns:
                                                  the USING contract.
```

The sections differ in *allocation mechanics*, not wording, so picture them as mechanics:

```text
  THE RUN UNIT (one process / one LE enclave / one GnuCOBOL executable)
+------------------------------------------------------------------------+
|  loaded program image                                                  |
|  +-----------------------+   <- allocated ONCE, at first activation    |
|  | WORKING-STORAGE       |      persists across every CALL             |
|  |                       |      C analog: global in .data / .bss       |
|  |  WS-CALLS  [0000]     |      VALUE clauses: a load-time event       |
|  +-----------------------+                                             |
|                                                                        |
|  the runtime's storage machinery (LE on z/OS, libc under GnuCOBOL)     |
|  +-----------------------+   <- segments acquired from the OS via      |
|  | cell pools / malloc   |      GETMAIN (z/OS) or malloc (Linux):      |
|  |                       |      THERE IS A HEAP. The language just     |
|  |   +---------------+   |      refuses to name it in the source.      |
|  |   | LS frame #1   |<-+--- one block per CALL invocation            |
|  |   +---------------+   |    VALUE re-applied each time               |
|  |   | LS frame #2   |<-+--- the "stack" analog: fresh automatic      |
|  |   +---------------+   |    storage; DSAs on z/OS, malloc'd here     |
|  |   | LS frame #3   |<-+--- (recursion = more frames)                |
|  |   +---------------+   |                                             |
|  +-----------------------+                                             |
+------------------------------------------------------------------------+
         ^                ^                  ^
         |                |                  |
    LINKAGE item:    LINKAGE item:      LINKAGE item:
    address of WS    address of a       address of anything
    (caller global)  caller frame       the caller owns
    --------- no box of its own: a LENS aimed at someone else's ---------
                box via the USING address list
```

A C programmer knows all three mechanics under other names; COBOL's addition: the
*source text declares which one you get* — nothing is allocated silently.

## WORKING-STORAGE: signed at load, VALUEd once

WORKING-STORAGE is the model's default bet: sizes and offsets fixed at compile time,
storage materialized when the program first becomes active in the run unit — for a
load-once subprogram, at load. One copy, ever; call it a thousand times, the same
bytes answer. The semantics that trip people up:

1. `VALUE` clauses are applied **once**, at first activation — a load-time event, like
   a C global's initializer landing in `.data`.
2. Every subsequent CALL **reuses the bytes as the last invocation left them**: no
   reinitialization, ever.
3. The state persists until the run unit ends — or until `CANCEL` (or the `INITIAL`
   attribute) throws the copy away, making the next call a first activation again.

A `VALUE 0` counter doing `ADD 1 TO CTR` on entry counts *invocations*: 0001, 0002,
0003 across calls. A feature for a run tally; a bug if you assumed "VALUE 0" means
"zero on every call." It means zero *once*.

Why default to static, shared, dirty storage? Derive it from the metal: the System/360
(1964) COBOL grew up on had **no hardware stack** — a call was `BALR R14,R15`, return
address in a register, save areas chained through the caller's storage, data static
because there was nowhere else for per-call frames. (z/Architecture *still* has no
hardware stack; LE builds one in software, below.) Economics reinforced it: CPU time
was the metered resource, and VALUE-initializing a 10,000-entry table was a visible
loop on the bill every load. Initialization was made *explicit and rare* — a 1970s
cost decision fossilized into semantics. (The modern concession: Enterprise COBOL V6+
`INITCHECK` statically flags reads with no VALUE and no prior write.)

## LOCAL-STORAGE: the stack, admitted in 1985

LOCAL-STORAGE is the local variable — but COBOL only got one in 1985, twenty-six years
in. The semantics are exact: storage for every item is allocated **at each invocation**,
VALUEs applied **at each invocation**, storage unavailable when that invocation
finishes. One call, one fresh copy — automatic storage, the stack-frame analog from
module 01 ch04 in punch-card-era syntax.

The reason it exists is **recursion**. A recursive CALL enters a program while a
previous invocation is still active; if all its data is in WORKING-STORAGE, the second
invocation lands *on top of* the first's bytes — two activations sharing one box,
which is what static storage means. COBOL-74 could not express the alternative: there
was no per-invocation storage. COBOL-85 fixed that by making recursion **opt-in**, with
two parts: the `RECURSIVE` phrase on `PROGRAM-ID`, and every per-invocation datum in
LOCAL-STORAGE.

Why opt-in? Two costs. Compatibility: decades of deployed programs *depended* on
WORKING-STORAGE persisting between calls; fresh storage per CALL would change the
meaning of existing code overnight. And performance: per-invocation allocation costs
instructions — non-recursive programs would pay a frame per call for nothing. In short:
WORKING-STORAGE in a recursive program is shared by all in-flight invocations (a C
`static` in a recursive function); LOCAL-STORAGE is what makes each level *a level*.

## LINKAGE: the section that allocates nothing

LINKAGE is not storage — it is a *description of somebody else's memory*, an
addressability contract: "when I am called, an address will be provided; lay this
PICTURE over the bytes there." The contract is the USING pair:

```cobol
      CALL 'PRICE-RATE' USING BY REFERENCE CUST-RECORD
                               BY REFERENCE RATE-TABLE.

      LINKAGE SECTION.
      01  CUST-RECORD.
          05  CUST-BALANCE      PIC S9(9) COMP-3.
      01  RATE-TABLE.
          05  RATE-ROW          PIC S9(3)V9(4) COMP-3 OCCURS 50.
      PROCEDURE DIVISION USING CUST-RECORD RATE-TABLE.
```

Under the covers this is raw pointer passing, older than C: the MVS convention (which
LE's CALL conforms to) builds a list of addresses, points register 1 at it, and sets
the high bit on the last entry to mark the end. The callee resolves LINKAGE items by
walking that list; nothing is checked at run time — not count, not type, not length.
The callee can even re-aim the lens (`SET ADDRESS OF LINKAGE-ITEM TO some-pointer`)
— pointer arithmetic, as close as the language gets.

The three passing modes answer "who can write what":

- **BY REFERENCE** — the address of the caller's actual bytes; writes propagate back.
  This is `f(&x)`.
- **BY CONTENT** — the runtime copies at CALL time and passes the copy's address;
  writes die with the invocation: pass-by-value via a temporary.
- **BY VALUE** — the value itself rides in the slot (COBOL 2002; classic compilers
  passed addresses only).

Now the classic abend. Suppose the address slot is never properly filled: the caller
passed fewer arguments than USING expects (the callee resolves the *next* list entry —
the end-marker or adjacent garbage — as a data address), or passed a pointer never set
to anything real. The first statement touching that LINKAGE item dereferences a bogus
address: on z/OS, abend S0C4 — module 08 ch01's PSW/storage-key violation; under
GnuCOBOL, SIGSEGV. Module 03 ch02's uninitialized-pointer family, with the same
discipline: what address did the item resolve to, and who owed you a valid one?

A nastier sibling does *not* abend: length mismatch. Caller passes `PIC X(4)`, callee
declares `PIC X(10)` — the callee reads six bytes past the argument, into the caller's
adjacent storage. No boundary crossed, no abend; just someone else's bytes in your
field. Chapter 03's arithmetic catches that class on paper.

## Level numbers: a tree, and four levels that are not nodes

Inside the sections, data descriptions are a tree: `01` starts a record, 02–49 nest,
and the tree *is* the memory layout — a group concatenates its children with no
padding, offsets fixed at compile time (chapter 03 computes them). Four levels are
special, each specialty a storage fact:

**49.** The standard gives 01–49 no distinct semantics — 49 is just the top of the
range — but it earned a de-facto role: DB2's DCLGENs generate varying-length character
host variables as `49 LEN PIC S9(4) COMP` plus `49 TEXT PIC X(n)`, a two-part
length+data record. Every DB2 shop has thousands.

**66.** `RENAMES` draws a rectangle over pieces of an existing 01 record and names it:
a *view* — zero new bytes, a different slicing of the same storage.

**77.** A standalone elementary item belonging to no record — punched-card inheritance:
an item outside the record layout needed its own level so the compiler would not
concatenate it into anything. Dropped from the 2002 standard, still accepted everywhere,
banned by every style guide since the 1980s; `01` does the job and composes.

**88.** The condition name — insiders say it exactly: *88s are the closest COBOL gets
to enums* — and **an 88 takes no storage at all**. Not one byte. It is a compile-time
comparison:

```cobol
      05  CUST-BALANCE     PIC S9(5) COMP-3.
          88  OVERDRAWN    VALUE -99999 THRU -1.
          88  ZERO-BAL     VALUE 0.
```

This reserves only the five-digit packed field. `IF OVERDRAWN` compiles to a range test
on CUST-BALANCE's bytes; `SET OVERDRAWN TO TRUE` compiles to a MOVE that makes the test
true. No enum object exists in storage — you cannot REDEFINE an 88, pass one USING, or
find one in a dump; only its parent's bytes exist. The payoff: names for *states* —
`IF ACCOUNT-CLOSED` instead of `IF WS-STATUS = 'C' OR 'c'`.

Then **FILLER** — the unnamed node. `05 FILLER PIC X(3)` reserves three bytes you may
*never reference again*: no name means no MOVE target, no INITIALIZE (INITIALIZE skips
FILLER by definition). A WORKING-STORAGE FILLER can carry a VALUE, applied once at
load; a FILE SECTION FILLER cannot. So slack bytes keep whatever was in the buffer
before — and I/O buffers are reused record after record, so the previous record's data
rides into the next record written to disk or a database row. A documented leak class:
PII in unnamed bytes.

## Underneath: cell pools and the heap COBOL won't name

Here is the punchline that makes the chapter honest. COBOL claims no heap, no
allocator, no run-time storage — a claim about the *language surface*, not the machine.
On z/OS, IBM Language Environment (LE — the shared runtime for COBOL, PL/I, C, and
Fortran since the early 1990s) acquires every scrap via **GETMAIN**: the static storage
behind a RENT program's WORKING-STORAGE; the DSAs (dynamic storage areas) forming the
software stack that holds C frames *and* COBOL LOCAL-STORAGE; and the fixed-size
**cell pools** LE carves from GETMAIN'd segments — same-size cells, decades before
Linux called that a slab allocator. There is a heap; the language just refuses to show
it to you — and in exchange you never see a malloc fail, a pointer dangle, or a
fragmenting arena in source code.

GnuCOBOL makes the same translation onto POSIX: WORKING-STORAGE becomes static data
baked into the executable image, LOCAL-STORAGE a fresh per-invocation `malloc` block,
LINKAGE pointers into the caller. Same semantics, different placement — the lab makes
that visible.

## What insiders know

- **VALUE in WORKING-STORAGE is a load-time event, not a per-call event.** The bug has
  a name in every shop: "works on the first call, wrong on the second." Fixes, in
  order of cost: INITIALIZE/MOVE at entry, LOCAL-STORAGE, the INITIAL attribute, or
  CANCEL before re-call. INITCHECK (Enterprise COBOL V6.2+) flags the reads statically.
- **Recursion is opt-in twice:** `RECURSIVE` on PROGRAM-ID *and* per-call data in
  LOCAL-STORAGE. Any WORKING-STORAGE in a recursive program is shared by all in-flight
  invocations — the C static-in-a-recursive-function semantics, which is how you keep
  a depth counter across levels.
- **88s cost zero bytes** — compiled comparisons, not data. `SET x TO TRUE` is a
  disguised MOVE into the parent. You cannot pass an 88, REDEFINE an 88, or find one
  in a dump; when one misbehaves, you debug its parent's bytes.
- **FILLER is unreferenceable by design, therefore uninitializable in practice.**
  INITIALIZE skips it, MOVE cannot name it, its VALUE (WORKING-STORAGE only) runs once
  at load. Dirty slack bytes are a documented PII leak vector in fixed-length records —
  audit them.
- **LE cell pools are slab allocation, 1970s edition:** GETMAIN'd segments carved into
  same-size cells; LOCAL-STORAGE and C stack frames both live in DSAs on the LE storage
  stack, shared by C and COBOL in one enclave. COBOL's "no heap" is a language-surface
  statement — GETMAIN is the heap.
- **The parameter list is raw addresses with an end marker:** register 1 → address
  list, high bit set on the last entry. A USING-count mismatch makes the callee resolve
  the terminator's high-bit-laced address as a data pointer — one MOVE later, the
  classic S0C4.
- **RENT is how one load module serves thousands of users:** compiled reentrant, a
  program's WORKING-STORAGE is copied into fresh LE storage per task, so a CICS region
  shares one read-only copy of the code — why "dirty WORKING-STORAGE between tasks"
  is the production crime in CICS. Chapter 04 is that story.

## See it yourself

The lab prints the addresses of the same-named data through three recursive
invocations and watches which move and which don't. It needs GnuCOBOL:

```text
cobc --version      # want 3.1.2+ (Debian 12 / Ubuntu 22.04 package: gnucobol3;
                    #  older distros: 'gnucobol'; macOS: brew install gnucobol)
```

If `cobc` is absent: `sudo apt-get install gnucobol3`. This chapter was drafted on a
sandbox without cobc or install rights, so the output below is the *expected* shape for
GnuCOBOL 3.1.2 on 64-bit Linux — your hex will differ, the relationships will not,
because the relationships are what the standard guarantees. Save as
`02-storage-eyes.cbl`:

```cobol
       IDENTIFICATION DIVISION.
       PROGRAM-ID. DRIVER.
      * The driver owns the WORKING-STORAGE (the "global") and passes
      * its addresses to a recursive subprogram through LINKAGE.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  START-DEPTH     PIC 9(2) VALUE 1.
       01  WS-CALLS        PIC 9(4) VALUE 0.
       01  P               USAGE POINTER.
       PROCEDURE DIVISION.
           SET P TO ADDRESS OF WS-CALLS
           DISPLAY 'driver   : WS-CALLS @ ' P
           CALL 'STORAGEEYES' USING BY REFERENCE START-DEPTH
                                    BY REFERENCE WS-CALLS
           SET P TO ADDRESS OF WS-CALLS
           DISPLAY 'driver   : WS-CALLS = ' WS-CALLS
                   ' (still @ ' P ')'
           GOBACK
           .
       END PROGRAM DRIVER.

       IDENTIFICATION DIVISION.
       PROGRAM-ID. STORAGEEYES RECURSIVE.
      * RECURSIVE + all per-call data in LOCAL-STORAGE = opt-in memory model.
      * No WORKING-STORAGE here: every byte is per-invocation or borrowed.
       DATA DIVISION.
       LOCAL-STORAGE SECTION.
       01  LS-PROBE        PIC X(8) VALUE 'LS-FRESH'.
       01  LS-NEXT         PIC 9(2).
       01  P               USAGE POINTER.
       LINKAGE SECTION.
       01  DEPTH           PIC 9(2).
       01  CALLS           PIC 9(4).
       PROCEDURE DIVISION USING DEPTH CALLS.
           ADD 1 TO CALLS
           SET P TO ADDRESS OF CALLS
           DISPLAY 'depth ' DEPTH ' : CALLS=' CALLS ' @ ' P ' (caller WS)'
           SET P TO ADDRESS OF LS-PROBE
           DISPLAY 'depth ' DEPTH ' : LS-PROBE=' LS-PROBE ' @ ' P
           SET P TO ADDRESS OF DEPTH
           DISPLAY 'depth ' DEPTH ' : DEPTH  @ ' P ' (caller bytes)'
           IF DEPTH < 3
               COMPUTE LS-NEXT = DEPTH + 1
               CALL 'STORAGEEYES' USING BY REFERENCE LS-NEXT
                                        BY REFERENCE CALLS
           END-IF
           GOBACK
           .
       END PROGRAM STORAGEEYES.
```

Run it:

```text
$ cobc -x -o eyes 02-storage-eyes.cbl
$ ./eyes
driver   : WS-CALLS @ 0x00005595f0f6a012
depth 01 : CALLS=0001 @ 0x00005595f0f6a012 (caller WS)
depth 01 : LS-PROBE=LS-FRESH @ 0x00005595f0f7b1f0
depth 01 : DEPTH  @ 0x00005595f0f6a010 (caller bytes)
depth 02 : CALLS=0002 @ 0x00005595f0f6a012 (caller WS)
depth 02 : LS-PROBE=LS-FRESH @ 0x00005595f0f7b260
depth 02 : DEPTH  @ 0x00005595f0f7b1f8 (caller bytes)
depth 03 : CALLS=0003 @ 0x00005595f0f6a012 (caller WS)
depth 03 : LS-PROBE=LS-FRESH @ 0x00005595f0f7b2d0
depth 03 : DEPTH  @ 0x00005595f0f7b268 (caller bytes)
driver   : WS-CALLS = 0003 (still @ 0x00005595f0f6a012)
```

What to notice — each observation is one section's semantics made physical:

1. **The WORKING-STORAGE address never moves** (`...a012`, six lines), and its counter
   accumulates 0001 → 0002 → 0003, visible to the driver at 0003. One box, shared,
   persisting: the global. VALUE 0 applied once.
2. **The LOCAL-STORAGE address changes at every depth, by a constant stride** (0x70
   here — each activation gets an identically sized malloc block plus allocator
   overhead), and the content is 'LS-FRESH' at *every* depth: VALUE reapplied per
   invocation. Fresh box per call — the stack-frame analog, built on the malloc heap,
   not a machine stack: the standard fixes the semantics, the implementation picks
   the placement.
3. **LINKAGE addresses point into other people's boxes.** CALLS's address equals the
   driver's WORKING-STORAGE address at every depth — the callee wrote through a lens
   into the caller's static storage. DEPTH at depth 1 is the driver's START-DEPTH, two
   bytes below the counter; from depth 2 on it lands 8 bytes past the LS-PROBE address
   printed one depth earlier — inside the *previous invocation's* LOCAL-STORAGE block,
   because that frame's LS-NEXT was passed BY REFERENCE. LINKAGE allocated nothing; it
   aimed a lens at whoever called.
4. Optional: `objdump -h ./eyes` — the static WORKING-STORAGE bytes are in the
   executable image's data sections; the program never asked for them at run time.

(One GnuCOBOL extension is in play: `DISPLAY` of a `USAGE POINTER` prints its hex value.
IBM COBOL refuses; there you REDEFINE the pointer over a PIC X field to see it.)

## How to use this knowledge

- **The "works once, fails after" diagnosis.** A subprogram correct on first call and
  wrong after is a VALUE-once bug until proven otherwise: check the Data Division
  section before the logic — a dirty field at a WORKING-STORAGE offset confirms it.
- **S0C4/SIGSEGV triage for LINKAGE.** Before blaming the machine or the database,
  check the USING contract: argument count, order, lengths, whether the caller ever set
  the pointer. Most "mystery S0C4s" are one of those four.
- **Design for transactional safety.** Anything a task must not inherit from the
  previous task — user data, accumulators, flags — belongs in LOCAL-STORAGE or
  CICS-owned storage, not WORKING-STORAGE. Chapter 04 formalizes this as
  quasi-reentrancy.
- **Reading inherited performance code.** You can now explain *why* the 1978 program
  avoids initializing tables, and what each INITIALIZE costs — argued with numbers
  instead of folklore.
- **The interview answer.** "The three storage sections map exactly to static,
  automatic, and borrowed storage; recursion was opt-in in 1985 because persistence
  semantics and per-call cost had to be chosen, not imposed" — backed by this lab's
  address trace.

## Connections

- **Module 01 ch04 (the stack):** LOCAL-STORAGE is that chapter's frame; LE's DSA chain
  and GnuCOBOL's malloc blocks are software reconstructions of it on a machine with
  no hardware stack.
- **Module 01 ch05 (the heap):** LE cell pools and GETMAIN are the hidden heap —
  ch05's arena/slab ideas under a language that denies having them.
- **Module 01 ch17 (COBOL):** ch17's storage overview expanded to exact,
  lab-verifiable semantics — the same three-way split, now with addresses.
- **Module 08 ch01 (the z-machine):** the S0C4 here is ch01's PSW/storage-key machinery
  firing; the 16MB line and 2GB bar return as LE's GETMAIN boundaries.
- **Module 08 ch03 (PICTURE & layout):** the offsets inside every box in the diagram —
  and the LINKAGE length-mismatch trap — are computed byte-by-byte there.
- **Module 08 ch04 (CICS):** RENT, task storage, and "dirty WORKING-STORAGE between
  tasks" — VALUE-once semantics at thousands-of-users scale.
- **Module 03 ch02 (the memory-bug family):** the wild LINKAGE read is the
  uninitialized-pointer family; the families that *cannot* exist here (UAF,
  fragmentation, GC pauses) are module 01 ch17's payoff, stated as a taxonomy.

## Quiz

1. **Concept.** State the exact allocation and initialization semantics of
   WORKING-STORAGE across multiple CALLs of one subprogram, and what `CANCEL`
   changes.

2. **Predict.** A subprogram's only data is `01 CTR PIC 9(4) VALUE 0.`; its procedure
   does `ADD 1 TO CTR. DISPLAY CTR.` Called three times in one run unit: what do the
   three DISPLAYs print with CTR in WORKING-STORAGE? In LOCAL-STORAGE?

3. **Predict the layout.** How many bytes does this description reserve, and how many
   do the 88s contribute?

   ```cobol
   01  CUST-REC.
       05  CUST-NAME        PIC X(10).
       05  FILLER           PIC X(3).
       05  CUST-BALANCE     PIC S9(5) COMP-3.
           88  OVERDRAWN    VALUE -99999 THRU -1.
           88  ZERO-BAL     VALUE 0.
   ```

4. **Bug diagnosis.** A batch job runs a subprogram once per input record. Record 1 is
   priced correctly; records 2..N inherit record 1's leftovers in a WORKING-STORAGE
   switch declared `VALUE 'N'` that should start each record as 'N'. No abend, no
   message. What happened, and the two cheapest fixes?

5. **Bug diagnosis.** A subprogram declares `PROCEDURE DIVISION USING CUSTOMER RATE`,
   but the caller passes only CUSTOMER; the first read of RATE abends S0C4 on z/OS
   (SIGSEGV under GnuCOBOL). Explain the mechanism: what address is dereferenced, and
   why is it garbage?

6. **Concept.** Why did COBOL-85 make recursion opt-in instead of making every program
   reentrant by default? Give the two costs avoided, and the two things a program must
   do to opt in.

7. **Cross-chapter.** A C programmer reads module 01 ch04 and ch17 and concludes "COBOL
   has no stack, so its calls are unsafe." Correct the claim using module 08 ch01's
   hardware facts: what supplies per-invocation storage on z/OS and under GnuCOBOL, and
   what does the machine itself provide?

8. **Predict.** In the lab program at depth 3: what is WS-CALLS, what is LS-PROBE, and
   which printed address is guaranteed identical to the driver's WS-CALLS address?

<details>
<summary>Answers</summary>

1. **Allocated at the program's first activation in the run unit; VALUEs applied
   then, once.** Every later CALL reuses the same bytes as the previous invocation
   left them — persistence, no reinitialization — until the run unit ends. `CANCEL`
   releases the subprogram's storage (dynamically called programs only), making the
   next call a first activation again: fresh storage, VALUEs reapplied. C analog: a
   global whose initializer ran at load; CANCEL is "unload the object file."

2. **WORKING-STORAGE: 0001, 0002, 0003** — one box, VALUE 0 applied once, each call
   increments what the last call left. **LOCAL-STORAGE: 0001, 0001, 0001** — a fresh
   box per invocation, VALUE 0 applied every call. Same source line, opposite
   behavior: the section is the whole difference.

3. **16 bytes total; the 88s contribute 0.** CUST-NAME is 10 (DISPLAY: one byte per
   character), FILLER is 3 unreferenceable bytes, and S9(5) COMP-3 is 3 (five digits
   plus a sign nibble = six nibbles). The 88s are compile-time comparisons over
   CUST-BALANCE's bytes — SET OVERDRAWN TO TRUE just MOVEs a negative value in. No
   storage, ever.

4. **VALUE-once.** `VALUE 'N'` ran at the first activation only; every later record
   inherits whatever the previous record left in the switch — the "works on the first
   call, wrong after" signature. Cheapest fixes: (a) reset at entry (MOVE 'N' TO
   SWITCH, or INITIALIZE); (b) move the item to LOCAL-STORAGE. Heavier: the INITIAL
   attribute, or CANCEL before each call.

5. **USING is a raw address list.** The callee resolves RATE to the entry after
   CUSTOMER; the caller built only one, so that slot holds whatever follows the real
   argument — the MVS end-of-list marker (high bit set) or adjacent garbage. The read
   dereferences that as a data address, tripping module 08 ch01's protection
   machinery: S0C4 on z/OS, SIGSEGV under GnuCOBOL. Module 03 ch02's
   uninitialized-pointer family, contracted through a CALL.

6. **Compatibility and CPU.** (a) Deployed programs depended on WORKING-STORAGE
   persisting between calls — fresh storage per CALL would silently change existing
   code's meaning. (b) Per-invocation allocation costs instructions, and CPU was the
   metered resource. The opt-in: the RECURSIVE phrase on PROGRAM-ID, plus
   per-invocation data in LOCAL-STORAGE.

7. **The machine has no hardware stack, but the runtime builds one.** On z/OS, LE
   maintains DSAs (dynamic storage areas) forming a software stack where C frames and
   COBOL LOCAL-STORAGE both live, acquired via GETMAIN; under GnuCOBOL, LOCAL-STORAGE
   is a fresh malloc block per invocation. Pre-85 COBOL *source* had no
   per-invocation storage, and the metal still has no stack pointer — return addresses
   ride registers; the runtimes supply the frame machinery the language later admitted
   to. Calls are unsafe where the USING contract is broken, not because frames are
   missing.

8. **WS-CALLS = 0003** — one shared box, three increments, confirmed by the driver.
   **LS-PROBE = 'LS-FRESH'** — VALUE reapplied per invocation; depth 3's copy is as
   fresh as depth 1's. **The CALLS address at depth 3 equals the driver's WS-CALLS
   address** — the LINKAGE item resolved to the caller's static storage; it has no
   address of its own, only the one it was lent.

</details>
