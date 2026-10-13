# Module 03 final exam — software bugs: the taxonomy of failure

Twelve questions across all five chapters. Closed book; every answer is
worked, not stated — the derivations are the point. Scoring: 11+ = you own
this module; 8-10 = solid, revisit the flagged chapters; <8 = the quiz
answers below each chapter are the study path.

**Part I — the theory (chapter 01)**

1. **(concept)** A teammate closes your ticket: "not a bug — it does
   exactly what I wrote, and it ran in production for a decade." Between
   which two sets does a bug live, why is a decade of uptime
   evidence of nothing, and what are fault, error, and failure (one line each)?

2. **(predict)** A C function has 8 independent two-way branches; your two
   tests run the all-true and all-false inputs. (a) How many paths exist, and
   what do line, branch, and path coverage report? (b) Two threads each
   execute 5 atomic blocks per loop iteration, 3 iterations, order fixed
   within each — how many interleavings? Give the formula.

**Part II — the memory family (chapter 02)**

3. **(diagnose)** Name the family, the mechanism, why it fires only when
   memory is tight, and the restructuring that kills the class:

```c
void set_name(struct user *u, const char *newname) {
    free(u->name);
    char *fresh = strdup(newname);
    if (fresh == NULL) return;        /* out of memory */
    u->name = fresh;
}
/* teardown() later calls free(u->name) for every user */
```

4. **(predict)** What does this print at `-O0` and at `-O2` (GCC 14.2,
   x86-64), why, and which sanitizer can see the bug?

```c
static int f(void) { int x; return x + 1; }
static void dirty(int a, int b) { int junk = a * b; (void)junk; }
int main(void) { dirty(20, 2); printf("%d\n", f()); }
```

5. **(diagnose, cross-module)** Name the violation against the chapter's
   invariant, what the NUL byte hits on the glibc heap, and the module 02
   ch04 primitive it enables:

```c
char *name = malloc(24);   /* exact-fit chunk, filled with 24 user bytes */
name[24] = '\0';           /* "make sure it's terminated" */
```

**Part III — the race family (chapter 03)**

6. **(concept)** Define data race and race condition, name the document that
   makes one of them undefined behavior, and give a race condition with zero
   data races. Why does a "benign" race on an aligned `int64_t` lose on
   32-bit?

7. **(diagnose)** Name both diseases, derive (via module 01 ch20) how thread
   B can receive a half-built object even when the interleaving looks
   sequential, and state the fix:

```cpp
static Widget* instance;              // plain pointer
static std::mutex m;
Widget* get() {
    if (instance == nullptr) {        // CHECK: unlocked read
        std::lock_guard<std::mutex> g(m);
        if (instance == nullptr)
            instance = new Widget();  // ACT: allocate, construct, publish
    }
    return instance;
}
```

8. **(predict)** Two threads each increment a plain `long long counter`
   1,000,000 times. (a) Derive the minimum and maximum final values. (b) A
   run prints 1,999,998 — how many updates were lost? (c) If a run prints
   exactly 2,000,000, does TSan report anything?

**Part IV — numeric & parsing (chapter 04)**

9. **(predict)** Compute or derive, exactly:
   (a) `uint16_t a = 65535, b = 65535; uint32_t c = a * b;` — the value of
       `c`, the hidden undefined behavior, and why `-Wall -Wextra -O2` is
       silent.
   (b) `int d = 0; int n = sscanf("3000000000", "%d", &d);` — `n` and `d`.
   (c) A signed 32-bit `time_t`: when does it run out, and what date appears
       one second later?

10. **(diagnose)** Two review findings — for each, name the family, the
    mechanism, and the fix:

```c
/* w = 40000, h = 27000 — both attacker-chosen */
struct px *buf = malloc(w * h * 4);
for (int i = 0; i < w * h; i++) buf[i] = parse_next();
```

```python
b = "résumé".encode()     # len(b) = ?
b[:7].decode()            # raises what?
b[:7].decode("latin-1")   # ?
```

**Part V — finding bugs (chapter 05)**

11. **(predict)** On the classic static x86-64 ASan layout, compute the shadow
    address for 0x7f285db00028 (chapter 05's Lab 2 address). What do shadow
    values 0x00, 0x03, 0xf3, and 0xfd mean? Why is the mapping 1:8, and what
    are ASan's slowdown and memory budgets?

12. **(concept)** A team reports: "we fuzz the parser — 100% statement
    coverage, zero crashes in a week." Give the three questions that triage
    this claim, with answers. Then: 500 commits have landed since the last
    good release — how many `git bisect` runs, and what two properties must
    the test predicate have?

<details>
<summary>Answers</summary>

**Part I**

1. The bug lives between the **spec** (the intended input → behavior mappings,
   mostly unwritten) and the **behavior** the program plus its environment
   delivers; "what I wrote" pins only the second set (ch01 §1). A decade of
   uptime means the fault was never *activated* — divergence is a property of
   program plus environment, not of observation: un-activated, not verified.
   **Fault**: the defect in the code, static, cheap. **Error**: the deviated
   state an activated fault produces. **Failure**: delivered service
   deviating from spec (ch01 §5).

2. (a) 2^8 = **256 paths**. The two tests report **100% line and branch
   coverage** — every `if` taken both ways — yet walk **2/256 = 0.78%** of
   the paths: tools count arcs, never paths (ch01 §2). (b) Thread A's 5
   blocks placed among the 10 slots: C(10,5) = **252** interleavings per
   iteration; three independent iterations give 252^3 ≈ **1.6 × 10^7** —
   formula C(k1+k2, k1)^n. Tests slice this space linearly.

**Part II**

3. Memory family, lifetime violation: the **double-free on the error path**
   (ch02 §3). The gap between free and reassignment leaves a dangling copy;
   only the OOM return skips the fix-up, so teardown frees the chunk twice —
   error paths are untested; this one fires only when `strdup` fails, under
   memory pressure. ASan catches it by quarantining frees; glibc ≥ 2.29 may
   abort with `free(): double free detected in tcache 2` (the tcache key,
   module 01 ch19). Class-killer: assign the new pointer *before* freeing
   the old — no early return then observes a dangling copy.

4. `-O0` prints **41**: locals live at fixed frame offsets, and `f`'s `x`
   lands on the slot `dirty` left in `junk` (20 × 2 = 40) — stack reuse
   (module 01 ch04). `-O2` prints **anything** (commonly 1, a never-written
   register): the read is UB, so no value is correct. Family: memory — the
   invariant's "reads a value someone actually wrote" clause is violated.
   Only **MSan** sees it: the bits are in bounds and alive, so ASan and
   UBSan pass (ch02 §4, ch05 §4).

5. Memory family, the **region** violated by exactly one byte — the
   off-by-one: 24 granted bytes own indices 0..23, 24 slots between 25
   fenceposts, and `name[24]` writes the 25th (ch02 §2). The standard
   permits *forming* `name + 24` (end-iteration) but never dereferencing it.
   On glibc the byte hits the **next chunk's size field**, clearing its low
   byte and shrinking the recorded size: module 02 ch04's **NULL-byte
   poisoning** primitive, the root of overlapping-chunk exploits. ASan
   aborts on the store, redzone shadow at the line.

**Part III**

6. **Data race** (a language-rules term): two accesses to one location, at
   least one a write, different threads, nothing synchronizing — undefined
   behavior per **C11 §5.1.2.4**. **Race condition** (a logic term):
   correctness depends on the interleaving. Zero data races, still a race
   condition: check-then-act with each half in its *own* critical section —
   every access synchronized, the sequence still wrong (ch03 §2). The
   aligned `int64_t` "benign" race is one load on x86-64 but **two 32-bit
   loads** on 32-bit x86/ARM — a writer between them leaves a torn value
   that never existed; UB on both platforms regardless (ch03 §2).

7. **Both diseases.** (1) The unlocked `instance == nullptr` read is a **data
   race** — UB. (2) The shape is a **race condition**: check-then-act. The
   half-built object: `new Widget()` is three events — allocate, construct,
   publish — and per module 01 ch20, writes reach other cores out of program
   order (store buffers, the SB litmus), so the pointer can become visible
   *before* the constructor's field stores; the fast path returns a
   partially built object despite sequential-looking code (ch03 §2). Fix:
   publish through `std::atomic<Widget*>` — release store to publish,
   acquire load on the fast path; one synchronization edge kills the race
   (Java `volatile`, JEP 133). Detector: TSan.

8. (a) Minimum **1,000,000**, maximum **2,000,000**. `counter++` is
   read-modify-write; the worst interleaving is lockstep: both threads read
   the same v and write v+1, so each *pair* of increments
   advances the counter by 1: 2n increments, n advances (ch03 §1). Nothing
   forces a loss, so the max is 2n. (b) **2** lost updates. (c) **TSan
   reports the race anyway**: it convicts on order, not outcome — no
   happens-before edge between the pair means both stacks print even when
   the count is right (ch03, ch05 §4).

**Part IV**

9. (a) `c = 4,294,836,225` on the two's-complement wrap — but C11 §6.3.1.1
   promotes both operands to `int` first, and 4,294,836,225 > INT_MAX:
   **signed overflow — UB in the intermediate**, so any value is permitted.
   The `(uint32_t)` cast arrives one instruction too late; to the compiler
   it is just `int` × `int` — `-Wall -Wextra -O2` is silent, and only UBSan
   sees it (ch04). (b) `n = 1` (success reported), `d = −1,294,967,296`:
   3,000,000,000 mod 2^32, read signed. UB per the standard; glibc wraps
   silently — `strtol` is the defined tool, saturating with `errno = ERANGE`
   (ch04). (c) 2^31 − 1 = 2,147,483,647 s at ~31,556,952 s/year ≈ 68.1
   years from 1970: **2038-01-19 03:14:07 UTC**; one second later the sign
   bit flips to −2^31, reading **1901-12-13 20:45:52 UTC** (ch04).

10. (a) A **numeric** bug that *ends* as a **memory** bug. `w * h * 4` is
    `int` arithmetic: 40,000 × 27,000 = 1,080,000,000 fits, but × 4 =
    4,320,000,000 overflows INT_MAX and wraps to **25,032,704** — `malloc`
    succeeds at ~25 MB; the loop writes ~4.3 GB of pixels through it — heap
    overflow, module 02 ch04's view. The failure began in the size
    computation, not the loop; fix: check each factor in `size_t` before the
    call — `if (h != 0 && w > SIZE_MAX / h / 4) return NULL;` (ch04).
    (b) `len(b) = 8`: `é` is two bytes, twice. `b[:7]` = b'r\xc3\xa9sum\xc3'
    severs the final `é`'s lead byte from its continuation, so `.decode()`
    raises `UnicodeDecodeError ... unexpected end of data`; under `latin-1`
    it decodes to the mojibake 'rÃ©sumÃ' — the cut crashes, the mis-declared
    encoding corrupts silently. Fix: slice on characters, never bytes (ch04).

**Part V**

11. shadow = (0x7f285db00028 >> 3) + 0x7fff8000 = 0xfe50bb60005 +
    0x7fff8000 = **0xfe58bb58005** (ch05 §4). `0x00`: all 8 granule bytes
    addressable; `0x03`: only the first 3; `0xf3`: stack right redzone;
    `0xfd`: freed — quarantined, how ASan catches use-after-free after the
    free. One shadow byte describes 2^3 = 8 application bytes, so 47-bit app
    space needs a 16 TiB shadow *inside the same address space*; one linear
    formula covers both segments, the unmapped gap swallowing wild pointers.
    The `>> 3` scale never changes; the offset does (57-bit-VA kernels
    relocate it). Budget: ~2x slowdown, ~3x memory — the cheap one
    (TSan ~5-15x).

12. **(a) What is the oracle?** A crash-only oracle is blind to
    out-of-bounds *reads*: Heartbleed sized its destination from the same
    attacker-claimed N as the copy, so the write stayed in bounds while the
    read ran ~64 KB past a 19-byte allocation — no crash, only secrets. Fuzzing
    needs ASan as the oracle (ch05 §4, §6). **(b) What is the fitness
    function?** Statement coverage saturates after one valid input — zero
    gradient, blind search; edges distinguish the three outcomes of
    `if (magic == 0xAA && version == 2)` where the statement count sees one
    covered line (ch05 §2). **(c) Is the corpus healthy?** Deduped by
    coverage, re-merged after code changes (the edge-to-input mapping
    drifts), dictionary in place (ch05 §3). Bisection: ceil(log2 500) = **9
    runs**; the predicate must be **monotone** and **deterministic** — a flaky
    test turns halving into a random walk (ch05 §5).

</details>
