# Module 03 — Software bugs: the taxonomy of failure

> Every bug is a deviation between specification and behavior, and every bug class
> has a *mechanism* — a small number of root causes that regenerate the same
> failures forever. Senior engineers know these classes by scar tissue. This module
> derives them from first principles instead.

**5 chapters · one final exam · prerequisite: module 01 (the whole arc), module 02
chapters 02 & 05 (the exploitation view of memory bugs).**

## How to study it

Chapter 01 builds the theory (what a bug *is*); chapters 02–04 are the four great
families — memory, concurrency, numeric/parsing, state — each derived from its
mechanism, each illustrated with real code shapes you will meet in code review;
chapter 05 is the machinery that finds them (fuzzing, sanitizers, bisection) and how
that machinery actually works inside. Every family chapter cross-references module
01/02: the memory family is module 01 ch21 read as a code-review checklist.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The bug from first principles](chapters/01-bugs-from-first-principles.md) | Spec vs behavior, state-space explosion, the four families, cost curves. |
| 02 | [The memory-bug family](chapters/02-memory-bug-family.md) | Overflow, UAF, double-free, uninitialized — their shapes in real diffs. |
| 03 | [The race family](chapters/03-race-family.md) | Data races, TOCTOU, deadlock, livelock, priority inversion. |
| 04 | [The numeric & parsing family](chapters/04-numeric-parse-family.md) | Overflow, promotion traps, float money bugs, encoding, dates. |
| 05 | [How bugs are found](chapters/05-finding-bugs.md) | Fuzzing from first principles, sanitizers inside, bisect, post-mortems. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the bug from first principles.** Definition: a bug is divergence
between intended behavior (spec — often unwritten!) and actual behavior; from that,
derive why bugs cluster (state space grows exponentially with paths; tests cover a
linear slice — the coverage argument with a concrete count for a 5-branch function).
Classification by *mechanism*, not symptom: the four families (memory, concurrency,
numeric/parsing, state) with a defining property each. Heisenbugs: why concurrency
bugs hide (timing = an invisible input). "Works on my machine" derived as a
memory-model statement (different allocator → different layout → different failure;
ties to module 01 ch19). The cost curve (Boehm's defect-cost escalation), defect
density as a managed metric, and the iceberg ratio (found vs shipped). The vocabulary
contract: fault vs error vs failure (the dependability-community distinction).

**Chapter 02 — the memory-bug family.** The shapes as they appear in code review,
each with a short annotated snippet: bounds overflow (strcpy vs the strncpy traps —
no NUL guarantee, silent truncation), off-by-one (`<=` on bounds, fencepost
derivation), use-after-free (callback/observer patterns, destructor-triggered
notifications), double-free (error paths), uninitialized reads (why -O0 vs -O2
differs — stack reuse; module 01 ch04 payoff), and the escape (returning pointers
to locals — the compiler warning you must not ignore). Language-specific traps:
C++ iterator invalidation on vector growth (the capacity-doubling connection to
module 01 ch19), Java static-collection leaks, Python reference cycles + __del__
interactions (module 01 ch14). For each: the sanitizer that catches it (→ ch05) and
the CVE-shaped real-world version. The reviewer's heuristic: lifetime of every
pointer-shaped thing must have an owner.

**Chapter 03 — the race family.** Data race (undefined by language rules) vs race
condition (logic-level interleaving) — the distinction with the classic
check-then-act (lazy init double-check, why the naive version breaks and the
volatile/memory-order fix in one paragraph, deferring depth to module 09). TOCTOU
with filesystem paths (lstat then open — symlink attacks; the /tmp file-creation
lore and O_EXCL as the fix). Deadlock: the four Coffman conditions derived, lock
ordering discipline, the dining philosophers as the canonical diagram. Livelock and
starvation as distinct outcomes. Priority inversion: the Mars Pathfinder 1997
story in full (mutex + priority inheritance as the fix — this is the story
interviews ask). Lost wakeup (condition-variable predicate loops — why spurious
wakeups force `while`). Each bug paired with its detector (TSan → ch05) and its
memory-model root (module 01 ch20: reordering is why "it looks sequential" fails).

**Chapter 04 — the numeric & parsing family.** Integer overflow: signed = UB (the
compiler's license — module 10 ch02 connection), unsigned = wrap; the promotion
rules trap (u16+u16 promotes to int; the u16*u16 overflow that never triggers a
warning); historical disasters as derivation exercises: Ariane 5 (1996, float→int
conversion), Patriot missile (1991, 0.1 accumulation), and modern CVE shapes (size
computations `a*b*c` in malloc). Float traps: money in binary floats (0.1+0.2),
NaN != NaN (the ArrayList.remove trap), -0.0, comparing with ==. Parsing traps:
sscanf %d overflow behavior, locale/encoding (mojibake, UTF-8 truncation mid-
multi-byte — why string slicing by bytes is a bug), identifier/length limits. Date/
time: timezones, DST transitions (the 23-hour day), leap seconds (the 61-second
minute), and Unix 2038 (why signed 32-bit time_t ends in January 2038 — derive it).
Each with a reproduce-it snippet.

**Chapter 05 — how bugs are found.** Fuzzing from first principles: random input →
coverage feedback → mutate guided by edges (the loop is an optimizer; derive why
edge coverage beats statement coverage); mutational vs generative vs
grammar-aware; libFuzzer (in-process, the fuzz target contract, value profile) and
AFL/AFL++ (compile-time instrumentation, the forkserver); dictionary/corpus
hygiene; sanitizer = the oracle. Sanitizers inside: ASan (shadow memory 1:8
mapping — derive the shadow formula `addr >> 3 + offset`; redzones; ~2x slowdown
and ~3x memory), UBSan (compile-time checks per operation), TSan (happens-before
via vector clocks — ~5-15x), MSan (tracks uninitialized bit propagation; the reason
it needs all-instrumented builds). Static analysis: soundness vs precision (why
"no false positives" trades to missed bugs). git bisect mechanics (the log(N)
interrogation — derive). Post-mortem reading list: 3-5 legendary write-ups (Therac-
25, Ariane, the Heartbleed memory picture, one modern fuzzer-found CVE) with what
each teaches.

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **Code review as mechanism matching**: you see the shape, you name the family,
  you predict the failure mode before it ships.
- **The right detector for every family**: ASan vs TSan vs UBSan vs fuzzing —
  chosen, not guessed.
- **War-story fluency**: Pathfinder, Ariane, Patriot, Heartbleen-class analysis —
  you can *derive* what went wrong, not recite it.
- **The bridge to module 04** (these bugs as they appear in binaries) and
  **module 09** (the race family at full depth).
