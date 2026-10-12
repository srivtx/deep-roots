# Chapter 05 — How bugs are found

> **The 30-second version.** Bugs hide because input space grows exponentially
> while tests cover a linear slice (ch01). Fuzzing turns bug-finding into an
> *optimizer*: generate inputs, keep the ones that reach new edges, mutate
> them, repeat. A sanitizer is the *oracle* — it flags a memory-safety
> violation even when nothing crashes, because Heartbleed never crashed. When
> a bug ships anyway, `git bisect` finds the committing moment in log(N)
> questions.

## 1. The search problem, inverted

Chapter 01 derived the coverage gap: paths multiply as 2^branches — a parser
with 30 decision points has ~10^9 paths — while tests sample a linear slice.
The inversion defining modern bug finding: stop enumerating inputs, start
*searching*. A search needs a **generator** (random bytes, mutations, grammar
synthesis) and an **oracle** (crash, sanitizer report, hang).

Pure random generation fails for a derivable reason. A decoder guarding its
deepest branch behind the 4-byte header `AA 55 10 80` is hit by a blind
generator with probability (1/256)^4 = 2^-32 ≈ 1 in 4.3 billion — five days
at 10,000 inputs/second — and real protocols stack magic numbers, lengths,
and checksums, compounding the odds. The fix is not a better random
generator. The fix is feedback.

## 2. The feedback loop: fuzzing as an optimizer

Take a seed corpus, run it, record which *edges* of the control-flow graph it
reaches. Mutate. If the mutant reaches an edge no corpus entry owns, keep
it — a stepping stone. Otherwise discard.

```
        seed corpus
            |
            v
   +------------------+      candidate input
   |  mutate / splice |----------------------+
   +------------------+                      |
            ^                                 v
            |                     +-----------------------+
   retained: reached              |   execute the target   |
   a new edge                  +-->|   (in-process, fast)  |
            |                  |   +-----------------------+
   +---------+----------+     |        |            |
   | corpus (coverage-  |     |   new edge?       crash / sanitizer
   | deduped, minimized)|<----+        |           report?
   +--------------------+      no -> discard     yes -> save input, stop
```

This is hill-climbing (a (1+1) evolutionary strategy): fitness = new edges,
population = corpus, operators = bit flips, splices, block repeats. The magic
header costing 2^32 blind trials now costs hundreds: the corpus retains the
input that found `0xAA`, so the next mutation only needs `0x55` next to it.
Each byte is discovered once and never lost — that is the whole trick.

**Why edges, not statements.** Two derivations:

- *Granularity.* In `if (magic == 0xAA && version == 2) { open_session(); }`,
  `&&` short-circuits: with wrong magic, `version` is never evaluated.
  Statement coverage counts the `if` line as covered either way —
  wrong-magic and wrong-version inputs are indistinguishable to it. The
  compiler emits separate basic blocks for the two tests, so the edge census
  distinguishes three outcomes: first failed; first passed, second failed;
  both passed.
- *Saturation.* Statement coverage saturates after one valid input — fitness
  maxed, no gradient left, the optimizer degenerates to blind search. Edge
  coverage keeps distinguishing *how* control flowed: loop exit by condition,
  by `break`, by `return` — three separate edges.

**Three species of generator.** *Mutational* (AFL, libFuzzer): mutate a
corpus — best when valid inputs exist. *Generative*: synthesize from a model
— best when the format rejects nearly everything random. *Grammar-aware*
(libprotobuf-mutator, Fuzzilli for JavaScript): the operator itself moves
subtrees and adjusts length fields consistently with the bytes they describe.

## 3. The two engines: libFuzzer and AFL/AFL++

**libFuzzer** runs the target in the fuzzer's own process. You write one
function — the fuzz-target contract:

```c
// fuzz_target.cc — the entire contract
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    // deterministic: same input, same behavior; no globals left dirty
    // never exit() on success; a bug must be observable (crash or sanitizer)
    if (size < 4) return 0;          // tolerate all sizes, including 0
    consume_packet(data, size);      // the code under test
    return 0;
}
```

Build: `clang -g -O1 -fsanitize=fuzzer,address fuzz_target.cc parser.cc`. No
fork or exec per input, so small targets reach 10^4–10^6+ execs/second (order
of magnitude; small C++ parser, 2023-era x86-64 laptop) — at the price of
isolation, since one corrupted heap can corrupt the fuzzer itself (libFuzzer
answers with per-input malloc accounting and an exit-time LeakSanitizer
pass). Coverage comes from compiler instrumentation: a counter per edge, plus
*value profile* — `__sanitizer_cov_trace_cmp` hooks record the constants your
code compares input bytes against, and libFuzzer mutates toward those values.
That is how it *synthesizes* `AA 55` magic instead of waiting 2^32 trials.

**AFL/AFL++** run out-of-process. Instrumented binaries (`afl-clang-fast`)
give each basic block a random ID `cur` and increment `shared_map[cur ^
prev]` per taken edge, with `prev = cur >> 1` — a 64 KiB (2^16) bitmap with
logarithmic hit-count buckets (1,000 vs 1,005 loop iterations is the *same*
signal). The forkserver is the cost trick: the target execs once, stops after
dynamic linking but before `main()`, then forks per input — a fork (~0.1–1
ms) is 10–100x cheaper than exec plus relocation for a big binary. Typical
throughput 10^2–10^4 execs/second: slower than libFuzzer, but a crashing
child cannot kill the fuzzer, and it handles targets whose state cannot be
reset.

**Corpus hygiene** — dedupe by coverage (`afl-cmin`, libFuzzer `-merge=1`);
an input adding no new edge is dead weight; minimize keepers; re-merge after
code changes, because which input covers which edge *shifts* as branches are
added; a 10-entry corpus that walks the grammar beats 10,000 random blobs.
**Dictionaries** (AFL `-x dict.txt`, libFuzzer `-dict=`) inject format tokens
into mutations — grammar knowledge at a flat cost.

## 4. The oracle: sanitizers from the inside

A crash is a weak oracle. An out-of-bounds *read* doesn't crash — it hands
adjacent memory to whoever asked (Heartbleed); an OOB *write* into padding
doesn't either; races almost never crash on demand. Sanitizers observe the
*mechanism*, not the symptom: the compiler rewrites each memory access or
arithmetic operation into "operation plus check", turning latent corruption
into an immediate, attributed report.

**ASan (AddressSanitizer).** The insight: you cannot cheaply check *bounds*,
but you can cheaply check *poisoning*. Reserve a shadow copy of the address
space where 1 shadow byte describes 8 application bytes — a 1:8 mapping
(scale 3, because 2^3 = 8). Classic static x86-64 Linux layout:

```
 shadow(addr) = (addr >> 3) + 0x7fff8000

 app       0x7f1234567890
 addr>>3   0x 0FE2468ACF12       (divide by 8: 1 shadow byte per 8 bytes)
 +offset   0x FE2C68A4F12        (the shadow byte describing this address)

 app    [0x00007fff8000...0x00008fff6fff shadows low app memory]
        [0x02008fff7000...0x10007fff7fff shadows high app memory]
```

Why this shape? Application space is 47 bits (128 TiB, x86-64 Linux), so
one-eighth of it — 16 TiB of shadow — must live *inside* the same address
space; the offset makes one linear formula cover both app segments, and the
unmapped gap between them catches wild pointers. (Modern 57-bit-VA kernels
force a runtime-relocated shadow — gcc 14.2 on this box picked 0x6f4351fa0000
— but the `>> 3` scale never changes.)

Each instrumented access costs two extra instructions: compute the shadow
address, load its byte, compare. `0x00` = all 8 bytes addressable;
`0x01–0x07` = first k only; `0xf1/0xf3` stack left/right redzones;
`0xfa/0xfb` heap redzones; `0xfd` freed memory. The allocator and compiler
poison the zones around every object — redzones flanking each buffer, freed
chunks quarantined (~256 MB default, x86-64, clang 18-era) before reuse. So
when `strcpy` writes byte 9 of an 8-byte buffer, the next store loads `0xf3`
and aborts *at the faulting line* — not three functions later when the
corruption detonates — and a use-after-free hits `0xfd` while the chunk is
still quarantined. Budget, per the sanitizer docs: ~2x slowdown, ~3x memory
— the *cheap* one.

**UBSan (UndefinedBehaviorSanitizer).** No shadow; a check per operation:
shift count < bit width, no signed-overflow, float→int conversion in range,
null/alignment checks. ~1.1–1.3x slowdown; integer checks near-free. This is
the runtime mirror of module 10 ch02: the optimizer's UB license, checked one
operation at a time. Ariane 5 (module 03 ch04) died on a 64-bit float→16-bit
int overflow; UBSan's `float-cast-overflow` is exactly that line.

**TSan (ThreadSanitizer).** Detects races by computing *happens-before*.
Each thread carries a vector clock (one epoch per thread); mutexes, atomics,
and thread create/join merge clocks into happens-before edges. Every 8-byte
word gets 4 shadow slots recording which (thread, epoch) last read and wrote
it. A race = two accesses to one word, at least one a write, with **no
happens-before path between them** — module 01 ch20's "it looks sequential"
lie, mechanically checked. Cost ~5–15x slowdown, 5–10x memory (64-bit, clang
docs). TSan cannot combine with ASan — both claim the shadow.

**MSan (MemorySanitizer).** Tracks *uninitialized bits*: 1 shadow bit per
data bit (1:1). Copies propagate shadow; arithmetic ORs input shadows;
branching on dirty shadow reports. Hence MSan demands *fully instrumented
builds including libc++*: an uninstrumented `memcpy` moves bytes but not
shadow, so the destination's shadow becomes a lie in either direction (false
"clean" → missed bug; stale dirt → false report). ASan tolerates partial
instrumentation; MSan does not.

**Static analysis** completes the toolbox, with a tradeoff derived from
Rice's theorem: no tool can be sound (miss nothing) *and* precise (report
only real bugs). Coverity, CodeQL, Infer, and clang-tidy choose precision —
"no false positives" — which by the theorem means they silently miss bugs.
Use it for shapes cheap to express syntactically, sanitizers for what only
manifests at runtime, fuzzing for what nobody wrote a rule for.

## 5. Locating the regression: git bisect

Given a failing input: which commit introduced it? Each test run on one
checkout answers one yes/no question — one bit — so identifying one commit
among N suspects takes ceil(log2(N)) runs: 12 commits → at most 4; 1,000 →
10; 100,000 → 17. Mechanics: `git bisect start`, mark a bad and a good
endpoint; git checks out the midpoint; you answer `git bisect good` or
`bad`; the range halves. `git bisect run ./test.sh` automates it. Two
assumptions are load-bearing: the predicate is *monotone* (bug introduced
once, never self-healed, intermediate commits build) and the test is
deterministic — a flaky test turns bisection into a random walk.

## 6. The post-mortem reading list

- **Therac-25 (1985–87; Leveson & Turner, IEEE Computer 1993).** A race
  between the keyboard handler and the treatment table could fire the
  electron beam at full current with the table mispositioned. The software
  came from earlier Therac models, whose *hardware* interlocks had masked
  these same latent races; removing the interlocks without re-validating the
  software un-masked them. Lesson: field experience does not transfer when
  you remove the layer that was hiding the fault (ch03).
- **Ariane 5 flight 501 (June 4, 1996; Lion inquiry).** A 64-bit float→16-bit
  signed int conversion of horizontal bias overflowed; the unhandled Ada
  exception halted the inertial reference, and the *identical* software on
  the redundant unit failed identically — common-mode defeat of redundancy.
  The code was inherited from Ariane 4 without re-validating its input
  domain. Lesson: reuse without re-deriving assumptions is a bet (ch04;
  UBSan catches the class in one run).
- **Heartbleed (CVE-2014-0160, April 2014).** The memory picture:

  ```
  heartbeat request buffer (heap)        response (heap)
  +--------+--------+-------+---+        allocated as 1+2+N bytes:
  | type=1 | len=N  | 16 B  | ? |        sized by the SAME attacker claim N
  +--------+--------+-------+---+              |
   actual payload: 16 bytes                    |
                      `--- memcpy(dst, src, N) --> reads N bytes from a
                           19-byte allocation: 64,519 bytes past the end
  ```

  OpenSSL trusted the claimed length N (up to 64 KiB) over the received
  length; since the destination was allocated from the same N, the *write*
  stayed in bounds — no corruption, no crash, just an OOB *read* leaking
  heap neighbors: session keys, private-key material. Lesson: a crash-only
  oracle is blind to the most profitable bug class in history; this bug is
  why OSS-Fuzz exists (Google, 2016–; ~36,000 bugs and ~8,900 security
  issues across ~550 projects by its 2021 five-year retrospective).
- **CVE-2023-4863 — libwebp, September 2023.** Heap OOB *write* in the WebP
  Huffman-table builder, exploited in the wild (BLASTPASS iMessage chain,
  per Citizen Lab/Google TAG reporting) before the fix in libwebp 1.3.2 /
  Chrome 116.0.5845.187. The vulnerable shape, visible in fix commit
  webmproject/libwebp 902bc91: a fixed stack table `HuffmanCode table[1 <<
  LENGTHS_TABLE_BITS]` filled to a size derived from the file — ch02's
  bounds-overflow shape in the most-deployed image codec on earth, and the
  same "validate first, then allocate exactly" fix as this chapter's lab.
  Lesson: a transitive dependency is only as fuzzed as its least-fuzzed leaf.

## What insiders know

- **The shadow formula** is `(addr >> 3) + 0x7fff8000` (classic static
  x86-64 Linux); the 1:8 scale is invariant, the offset is not — 57-bit-VA
  kernels force a runtime-relocated shadow. Compute one by hand and you can
  read any report's "shadow bytes around" block, bracket `[f3]` included.
- **Slowdown budgets (sanitizer docs, 64-bit, clang/gcc 2024-era):** ASan
  ~2x, ~3x memory; UBSan ~1.1–1.3x (integer subset near-free); TSan ~5–15x,
  5–10x memory; MSan ~3x. The fuzzing default is
  `-fsanitize=fuzzer,address,undefined`; TSan is a separate build (shadow
  conflict).
- **Corpus hygiene rules that bite:** merge and dedupe on every significant
  code change (the edge-to-input mapping drifts); a corpus is an optimizer's
  *population*, not an archive — an entry that owns no edge is deleted; and
  re-run old crashing inputs against new builds before celebrating a fix.
- **Why MSan needs full instrumentation:** shadow propagation is a global
  dataflow property; one uninstrumented memcpy or libc++ call drops or
  fabricates shadow bits — false negatives *and* false positives, hence the
  pre-built MSan-instrumented libc++ distributions.
- **Throughput ladder (orders of magnitude, small C++ targets, 2023
  laptop):** exec-per-input ~10–10^2/s; AFL forkserver ~10^2–10^4/s;
  libFuzzer in-process 10^4–10^6/s. Pick isolation when state cannot reset,
  speed otherwise.
- **The interview trap:** "we have 100% coverage" — of what? Statements
  saturate at one input; SQLite's team (sqlite.org docs) pursues 100% MC/DC
  *branch* coverage because statement coverage is a vanity metric for a
  fuzzer's fitness function.

## See it yourself

Labs ran on this box: gcc 14.2.0 (Debian), Python 3.12, git 2.47.
clang/libFuzzer was unavailable, so Lab 1 uses the Python equivalent. Each
condition sits on its own line because `sys.settrace` reports line events
only — line coverage with branch-level resolution.

**Lab 1 — a 45-line coverage-guided fuzzer catching a real bug.** The target
has the CVE-2023-4863 shape: a trusted length leading to an unchecked read.

```python
# fuzz.py — coverage-guided fuzzer
import random, sys

def decode(buf):                    # packet = magic(2) tag(1) len(1) payload
    if len(buf) < 4:
        return None
    if buf[0] != 0xAA:
        return None
    if buf[1] != 0x55:
        return None
    tag = buf[2]
    n = buf[3]
    if tag == 0x10:
        if n == 0x80:
            # BUG: trusts n; the payload may be empty
            return buf[4 + 2]       # IndexError when payload < 3 bytes
    return None

covered = set()
def tracer(frame, event, arg):
    if event == "line" and frame.f_code.co_name == "decode":
        covered.add(frame.f_lineno)         # our edge proxy: lines
    return tracer

def mutate(b):
    b = bytearray(b); op = random.randrange(3)
    if op == 0 and b:                       # bit flip
        b[random.randrange(len(b))] ^= 1 << random.randrange(8)
    elif op == 1:                           # insert a byte
        b[random.randrange(len(b) + 1):0] = bytes([random.randrange(256)])
    elif b:                                 # truncate
        del b[random.randrange(len(b)):]
    return bytes(b)

random.seed(7)
corpus = [bytes([0xAA, 0x55, 0x00, 0x00]), b"\x00" * 8]   # 2 seeds
corpus_cov = [frozenset()]; execs = 0
sys.settrace(tracer)
while True:
    execs += 1
    cand = mutate(corpus[random.randrange(len(corpus))])
    covered.clear()
    try:
        decode(cand)
    except Exception as e:
        sys.settrace(None)
        print(f"CRASH after {execs} execs: {type(e).__name__}: {e}")
        print(f"crashing input: {cand.hex()}")
        print(f"corpus size at crash: {len(corpus)} (started with 2)")
        print(f"lines covered: {sorted(covered)}")
        break
    key = frozenset(covered)
    if key and key not in corpus_cov:       # keep inputs with new coverage
        corpus.append(cand); corpus_cov.append(key)
```

```text
$ python3 fuzz.py
CRASH after 1245 execs: IndexError: index out of range
crashing input: aa551080
corpus size at crash: 7 (started with 2)
lines covered: [5, 7, 9, 11, 12, 13, 14, 16]
```

The crashing input is exactly the 4 magic bytes — blind chance is 2^-32 ≈
4.3 billion; the loop paid 1,245. The corpus grew 2 → 7, one entry per newly
discovered line, and `lines covered` is the path to the bug. A blind variant
(fresh random bytes, no retention, same seed) ran 1,000,000 execs: no crash.
With clang, the contract is `LLVMFuzzerTestOneInput` and `clang -g -O1
-fsanitize=fuzzer,address` replaces the whole harness.

**Lab 2 — the redzone report.**

```c
/* oob.c */
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
    char name[8];
    strcpy(name, argv[1]);   /* writes strlen+1 bytes; name owns 8 */
    printf("hello %s\n", name);
    return 0;
}
```

```text
$ gcc -g -O0 -fsanitize=address -o oob oob.c && ./oob 0123456789ABCDEF
==24870==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7f285db00028
WRITE of size 17 at 0x7f285db00028 thread T0
    #1 0x5557ed90b25d in main /tmp/buglab/oob.c:5
  This frame has 1 object(s):
    [32, 40) 'name' (line 4) <== Memory access at offset 40 overflows this variable
Shadow bytes around the buggy address:
=>0x7f285db00000: f1 f1 f1 f1 00[f3]f3 f3 00 00 00 00 00 00 00 00
```

Read the shadow row as a map: `f1` left redzone, `00` the 8 owned bytes of
`name`, `f3` right redzone — the bracketed `f3` is the first shadow byte
`strcpy` touched past the end ("WRITE of size 17" = 16 chars + NUL).

**Lab 3 — git bisect, log(N) in the flesh.** Twelve commits; `scale.py`
works until commit 6 introduces a division by zero; `test.sh` runs it and
exits 0/1.

```text
$ git bisect start
$ git bisect bad HEAD
$ git bisect good $(git rev-list --max-parents=0 HEAD)
$ git bisect run ./test.sh
bisect found first bad commit
$ git bisect log
# bad:  [87f37f23] commit 12
# good: [4ea45e64] commit 1
# bad:  [6e17af2f] commit 6
# good: [094811ea] commit 3
# good: [47010537] commit 5
# first bad commit: [6e17af2f] commit 6
```

The run checked out commits 6, 3, and 5 — three questions for twelve commits
(ceil(log2 12) = 4 would be the worst case). `git bisect reset` to exit.

## How to use this knowledge

- **Choose the detector by family:** memory bugs → ASan + libFuzzer; races →
  TSan under a load generator that actually interleaves (ch03); numeric
  traps → UBSan; formats → grammar-aware fuzzing; unwritten rules →
  corpus-guided fuzzing with the sanitizer stack as oracle.
- **Reproduce before you debug:** rebuild with
  `-fsanitize=address,undefined` and rerun the failing input — converting
  distant corruption into an at-the-line report routinely saves hours.
- **Triage "we fuzz" claims:** what oracle (crash-only misses
  Heartbleed-class bugs), what coverage metric (statement vs edge vs
  MC/DC), is the corpus deduped and re-merged?
- **Incident order:** capture the input, reproduce under sanitizers, bisect
  to the commit, *then* form theories — log(N) questions beat any
  hypothesis.

## Connections

- **Module 01 ch21** — ASan's redzones and MSan's shadow exist to observe
  the four primitive memory failures; the mitigation ladder detects at
  runtime, sanitizers at test time.
- **Module 02 ch04–05** — an ASan report is the heap-exploitation chapters'
  bug from the defender's side, caught before allocator grooming begins.
- **Module 03 ch02–04** — this chapter opened the detectors those chapters
  named: ch02's shapes → ASan/MSan, ch03's races → TSan, ch04's traps →
  UBSan.
- **Module 04 ch06** — binary-only targets have nowhere to insert coverage
  counters; that is why packed binaries starve fuzzers of feedback.
- **Module 10 ch02** — UB as the optimizer's license; UBSan is that license
  checked at runtime, one operation at a time.

## Quiz

1. *(concept)* Derive why edge coverage beats statement coverage as a
   fuzzer's fitness function, using `if (magic == 0xAA && version == 2)` as
   the example. What does the optimizer lose when fitness saturates?
2. *(concept)* Why is "crash" an insufficient oracle, and what property of
   CVE-2014-0160 makes it the canonical proof? Which sanitizer property
   closes the gap?
3. *(predict)* On the classic static x86-64 ASan layout, what is the shadow
   address of application address 0x7f1234567890? Which shadow byte marks a
   stack right redzone, and what does 0x03 mean?
4. *(predict)* A regression lies among 1,024 commits between known-good and
   known-bad endpoints. How many test runs does bisection need at most, and
   which two properties must the test have?
5. *(diagnose)* Your fuzz target crashes reliably under
   `-fsanitize=fuzzer,address`, but the plain `-O2` build runs the same
   input for hours without visible failure. Give two mechanisms, and say
   which build you report.
6. *(diagnose)* An MSan build reports a use of uninitialized value that
   traces into a third-party static library linked unmodified. Is the
   report trustworthy? Derive why MSan — unlike ASan — cannot tolerate
   partial instrumentation.
7. *(cross-chapter)* Connect module 03 ch04's Ariane 5 post-mortem to this
   chapter: which UBSan check, at which operation, would have caught it,
   and why did hardware redundancy not count as an oracle?
8. *(predict)* Lab 1's fuzzer found `aa551080` in 1,245 execs. Derive the
   blind fuzzer's expected exec count, explain the gap via corpus retention,
   and map the covered line numbers to the stepping stones.

<details>
<summary>Answers</summary>

1. **Short-circuit granularity + saturation.** `&&` compiles to two basic
   blocks: with wrong magic, `version` is never evaluated, yet the `if`
   *statement* still counts as covered — the two inputs are
   indistinguishable to statement coverage, while the edge census sees three
   outcomes (first failed; first passed, second failed; both passed). Once
   one input touches every statement, statement fitness is maxed: zero
   gradient, blind search. Edges keep a gradient — loop exit by condition
   vs `break` vs `return` are distinct edges.
2. **OOB reads are silent.** Heartbleed's destination was allocated from the
   same attacker-controlled N as the copy, so the write stayed in bounds —
   no crash; the *read* ran ~64 KB past a 19-byte allocation, leaking
   secrets. ASan observes the mechanism, not the symptom: poisoned redzones
   abort on the OOB read with the address and stack.
3. **shadow = (0x7f1234567890 >> 3) + 0x7fff8000 = 0xfe2468acf12 +
   0x7fff8000 = 0xfe2c68a4f12.** Stack right redzone = `0xf3` (left `0xf1`,
   heap `0xfa/0xfb`, freed `0xfd`). `0x03` = only the first 3 of the 8
   application bytes mapped by that shadow byte are addressable.
4. **10 runs.** Each run yields one bit; one of 1,024 = 2^10 commits needs
   ceil(log2 1024) = 10. The test must be (a) monotone — every commit after
   the regression fails, none before (single introduction, never
   self-healed, intermediate commits build) — and (b) deterministic; a
   flaky predicate turns the halving into a random walk.
5. **Two mechanisms.** (a) The bug is an OOB read or a write into padding —
   silent at `-O2`, fatal against ASan's poisoned redzones (Heartbleed's
   shape). (b) The instrumented allocator's redzones and free-quarantine
   turn silent UAF or double-free corruption into `0xfd`/`0xfa` aborts.
   Report the sanitizer build: it makes the corruption deterministic and
   at-the-line; the plain build's "no failure" means *not observed*, not
   *absent*.
6. **Not trustworthy as-is.** MSan's 1:1 bit-level shadow must propagate
   through every load, store, copy, and arithmetic op; an uninstrumented
   library moves bytes without moving shadow — its output's shadow is
   stale (false reports, as here) and dirt passed in is dropped (false
   negatives). ASan degrades gracefully (it checks only instrumented
   accesses); MSan is a global dataflow property — build the library with
   MSan, or use pre-built MSan libc++.
7. **`float-cast-overflow`.** The failing operation: 64-bit float→16-bit
   signed int of horizontal bias (a value near 32768 exceeding 32767); UBSan
   instruments that exact conversion and reports at the line on the first
   run. Redundancy was not an oracle because both SRIs ran *identical*
   software — a common-mode fault fails every replica on the same input;
   replication adds fault tolerance, never fault *detection*.
8. **~2^32 ≈ 4.3 billion** — the blind generator must emit `AA 55 10 80` as
   the first four bytes, (1/256)^4. The gap is retention: once an input
   passes the `buf[0] != 0xAA` test (line 7's branch), partial progress is
   never lost, so each magic byte costs a few hundred mutations instead of
   a fresh compounded 1/256. The stepping stones are the covered lines [5,
   7, 9, 11, 12, 13, 14, 16]: length check (5), magic bytes (7, 9), tag
   load (11), len load (12), tag match (13), the rare extended-length
   branch (14), and the unchecked read (16) — the bug's own line.

</details>
