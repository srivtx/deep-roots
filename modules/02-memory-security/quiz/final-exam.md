# Module 02 final exam — memory security & exploitation

Twelve questions across all seven chapters. Closed book. Every answer is
worked, not just stated — the derivations are the point. Scoring: 11+ = you
own this module; 8-10 = solid, revisit the chapters your misses cluster in;
<8 = the chapter quizzes are the study path.

**Part I — the attacker's model and the stack (chapters 01-02)**

1. *(concept)* A bug bounty report reads: "missing bounds check found in an
   image-parsing helper — critical vulnerability, ship a patch today." Using
   the terminology contract, derive what the report has *not* established —
   then explain programmable severity: why the same unchecked copy can be
   "low: DoS" this year and critical next year with no code change.

2. *(predict)* A pre-forking daemon (one fork per connection, never execs) is
   built with `-fstack-protector-strong`. QA reports: "an attacker triggered
   stack-smashing aborts in three children, then gave up — the canary appears
   unbreakable." What is wrong with that conclusion? Compute the worst-case
   and expected child-crash counts to recover the canary, state why the first
   canary byte is free, and name the process-architecture fix.

3. *(predict)* A protected build places `char buf[64]` at `[rbp-0x50]` with
   the canary at `[rbp-0x8]`. Compute (a) the bytes from `buf`'s start to the
   first canary byte, and (b) the bytes to the return address. Then predict
   (c) an 80-character `strcpy` (81 bytes written) — signal, exit code, and
   whether the corrupted return address is ever loaded; and (d) a
   72-character one (73 bytes written) — what is detected, and which CI tool
   catches what the canary cannot.

**Part II — code reuse and the heap (chapters 03-04)**

4. *(diagnose)* A ret2libc exploit crashes mid-libc: SIGSEGV,
   `si_code = SI_KERNEL` (128), `fault_addr = (nil)`, `rip` pointing at a
   `movaps`. Diagnose the failure, derive the parity arithmetic that produced
   it, and give the one-gadget fix.

5. *(derive)* An overflow gives you exactly 16 controlled bytes: the saved
   RBP and the saved RIP of the victim's frame. Derive the two-word chain
   that turns this into a full ROP stack in a sprayed buffer — name the
   gadget, walk each instruction, and state the code-review conclusion this
   forces.

6. *(predict)* glibc 2.35, x86-64: `malloc(0x68)` nine times into `t[0..8]`,
   then `free(t[0])` through `free(t[7])`. Which chunk lands in the fastbin,
   what do the next three `malloc(0x68)` calls return, and on which malloc
   does `t[7]` finally come back? Why is the boundary exactly seven?

7. *(diagnose)* glibc 2.34: an exploit overwrites a freed chunk's tcache
   `next` with the raw, 16-byte-aligned target `0x404060` and calls `malloc`
   twice; the process aborts *inside malloc*, not at the target. Diagnose the
   mistake, give the correct stored value, and state what the exploit
   additionally had to own for that value.

**Part III — use-after-free and the ladder (chapters 05-06)**

8. *(diagnose, cross-module)* A class declares its members
   `Toolbar toolbar_; Canvas canvas_;` — and `~Toolbar()` calls
   `canvas_.flush()`. The crash lands in `~Toolbar`. Explain the mechanism
   using module 01 chapter 09's destruction rule read adversarially, and give
   the fix as a member-ordering decision.

9. *(concept)* Name the invariant each of canary, NX, and ASLR defends; then
   derive, one sentence each, why a use-after-free violates none of them.
   Finally, name the first ladder rung that touches the *bug itself* rather
   than the payload, and give its per-attempt success probability.

10. *(predict)* A stale pointer to a freed 32-byte object on an MTE system in
    sync mode; every attempt runs in a fresh process. Compute the expected
    number of attempts, the cost of each failure, and derive why Arm chose 4
    tag bits over 8 — show both sides of the trade.

**Part IV — corruption-free attacks and the whole chain (chapter 07 + cross-chapter)**

11. *(predict)* A Flush+Reload trace over a left-to-right square-and-multiply
    RSA implementation (2048-bit exponent, top bit always 1) logs 3,145
    executions of the loop body, of which 2,047 are the always-executed square
    step. How many 1-bits does the exponent have? Why does a multiply-always
    implementation leak nothing to this exact trace?

12. *(cross-chapter)* Chapter 01's chain is bug → primitive → technique →
    impact. (a) Which link do side channels break, and why does that make
    "zero vulnerabilities" compatible with full compromise? (b) The chapter
    06 ladder assumes memory corruption; name two rung families invented in
    response to chapter 07's attacks, each matched to the attack it answers.
    (c) One sentence: why is "close the timing channel" asymptotically
    impossible?

<details>
<summary>Answers</summary>

**Part I**

1. A missing check is a **bug** — a defect that exists whether or not anyone
   notices. Not yet established: a **vulnerability**, i.e. reachability across
   a trust boundary with attacker-controlled input — a fact about the call
   graph plus the boundary, not the code; a helper no untrusted input reaches
   has no attacker until refactoring moves it under a parser (ch01). Nor a
   **primitive** (which memory operation it yields — overwrite N bytes
   where?) or the **exploit** spending it. Programmable severity: a logic
   bug's impact is fixed at report time; a write primitive's is decided later
   by aim — badly aimed it crashes (QA: "DoS, low"), well aimed it hijacks.
   Input bytes at the crash site, or crash distance scaling with input
   length, upgrade severity mechanically (ch01).

2. The conclusion assumes a crash re-randomizes the canary. It does not:
   `fork` copies the parent's TLS verbatim — only `exec` consults
   `AT_RANDOM` (ch02). The parent keeps forking children with the *same*
   canary, so the attacker is inside a finite search, not a lottery. Worst
   case: the low byte is known (glibc's terminator, always `0x00`), leaving
   7 unknown bytes × 255 wrong values = **1,785 crashes**; expected ≈
   7 × 127.5 ≈ **893** — the "entropy" bypass flavor (ch02/ch06). Fix:
   fork **plus exec** per connection, the inetd shape, so each connection
   re-rolls `AT_RANDOM`.

3. (a) 0x50 − 0x8 = **72** bytes (64 buffer + 8 alignment slack). (b) 0x50 +
   8 = **88** bytes (buffer + slack + canary + saved RBP). (c) The 81 bytes
   cross all eight canary bytes (offsets 72-79): the epilogue compare fails,
   `__stack_chk_fail` aborts — **SIGABRT**, exit **134**, "stack smashing
   detected"; the corrupted saved RIP is never loaded. (d) The 73 bytes put
   the terminating NUL on the canary's low byte — `0x00` over `0x00`:
   nothing detected, the bug stays latent. The canary compares eight specific
   bytes at return only; ASan's redzones check every byte past the 64-byte
   object and flag the first — why CI needs ASan, not just hardening flags
   (ch02).

**Part II**

4. The callee entered with `rsp % 16 == 0` instead of the ABI-required 8, so
   its first 16-byte-aligned SSE access faulted — a #GP, which Linux reports
   as SIGSEGV with `si_code = SI_KERNEL` and no fault address (ch03).
   Derivation: the ABI guarantees `rsp ≡ 8 (mod 16)` at entry because a
   16-aligned call site pushes an 8-byte return address. A chain entered by
   `ret` has no call: after k returns from slot S, the callee sees
   `rsp = S + 8k`; if that is ≡ 0 (mod 16), every `movaps` at ABI-computed
   offsets faults. Each `ret` shifts `rsp` by 8 — fix: prepend one bare `ret`
   gadget; parity flips, no registers change (ch03).

5. Write **saved RBP = &fake_stack** (a sprayed buffer filled through a
   second channel) and **saved RIP = &(leave; ret)**. Walk it: the victim's
   `leave` sets `rsp` to the saved-RBP slot; its `pop rbp` loads the
   attacker's `&fake_stack`, and `ret` jumps to the gadget. The gadget's
   `leave` runs `mov rsp, rbp` — **rsp now lives in the sprayed buffer**;
   `pop rbp` consumes its first word; `ret` pops the first real gadget, and
   the chain runs from attacker-filled memory (`pop rsp; ret` and
   `xchg rax, rsp` are the alternatives when other registers are controlled).
   Conclusion: 16 controlled bytes + any pivot + any filled buffer = a
   complete ROP precondition — "too small to matter" is a review error, not
   a mitigation (ch03).

6. `t[7]` goes to the **fastbin**; the next three `malloc`s return
   **`t[6]`, `t[5]`, `t[4]`** — tcache is a LIFO stack, last freed first
   (ch04). `t[7]` returns only on the **eighth** malloc, after the seven
   tcache entries drain. The boundary is `tcache_count`'s default of **7 per
   bin** — a check boundary, not just a knob: the 8th same-size free bypasses
   tcache and meets the fastbin's weaker, head-only double-free check — why
   grooming sequences count frees (ch04).

7. **Safe-linking was ignored.** Since glibc 2.32 the stored `next` is
   mangled — `PROTECT_PTR(pos, ptr) = (pos >> 12) ^ ptr`, `pos` being the
   slot's own address — so the raw `0x404060` decodes to a garbage pointer
   and the pop aborts inside malloc. Correct stored value:
   `0x404060 ^ (pos >> 12)` — which requires knowing `pos`, i.e. **a heap
   leak** (the key is 12 bits of the slot's own address). The target must
   also stay 16-byte aligned or the pop dies as `unaligned tcache chunk
   detected`. On glibc 2.26-2.31 the raw value would have worked — vintage
   is part of the diagnosis (ch04).

**Part III**

8. Members destruct in **reverse declaration order** (module 01 ch09):
   `canvas_`, declared second, dies *first*; `toolbar_`, declared first, dies
   *last* — so `~Toolbar()` runs after `~Canvas()` and calls `canvas_.flush()`
   on a destructed member: a temporal bug in teardown order, ch05's
   destructor-triggered destruction shape at member scope. The dependency
   arrow points the wrong way — a destructor using a member that dies
   earlier. Fix by ordering: **declare `toolbar_` after `canvas_`**, so it is
   destroyed first, while `canvas_` is still alive; or use an explicit
   two-phase shutdown (ch05).

9. The **canary** guards the saved return path of instrumented frames (a
   fixed location); **NX** guards which pages may execute; **ASLR** guards
   address knowledge for an attacker who already controls flow (ch05). A
   use-after-free violates none: the free is legal, the chunk healthy, and
   the stale use a normal read of a mapped address through a pointer whose
   validity nothing tracks — the invariant broken is *temporal* (ch05). The
   first rung that touches the bug rather than the payload is **MTE** (ch06):
   freed memory is retagged, and a stale access survives only on a tag match
   — probability **1/16** per fresh process.

10. Geometric with p = 1/16: expected **16 attempts**. Each failure is a
    SIGSEGV (`SEGV_MTESERR`) that kills the process — loud, logged, and the
    tags re-roll, so failures accumulate no knowledge (ch06). The trade:
    4-bit tags on 16-byte granules cost 4/128 = **3.125%** of memory as
    metadata and give 1/16 per attempt; 8-bit tags would give 1/256 but
    double the floor to **6.25%** of RAM. Arm picked 4 bits as the knee of
    the memory-vs-resistance curve; sync mode's crash-per-miss (converting
    ~94% of naive UAF attempts into reports) does the rest (ch06).

**Part IV**

11. Left-to-right square-and-multiply squares once per remaining bit — 2,047
    squares for a 2048-bit exponent whose top bit is 1 — and multiplies once
    per *additional* 1-bit: 3,145 − 2,047 = 1,098 multiplies, so the
    popcount is **1,099** (multiplies = popcount − 1). Multiply-always
    executes the multiply every iteration with a dummy operand when the bit
    is 0: a fixed 2 × 2,047 loop body, so this access-pattern trace carries
    **zero bits** — the touched lines are data-independent (blinding
    additionally randomizes the exponent per run) (ch07).

12. (a) The **first link — the bug**. A side channel's transmitter needs no
    bug and sends nothing voluntarily: the "vulnerability" is that hardware
    performance depends on inputs — impact with no memory-safety violation
    at all, which is why ch07 sits outside ch01's chain and ch06's ladder
    (ch07). (b) Any two: **KPTI** (two page tables per process) answers
    Meltdown's transient reads of the always-mapped kernel; **retpolines**
    answer Spectre v2's branch-target injection; **constant-time code**
    answers cache-timing key recovery (Kocher 1996, Flush+Reload); **CAT**
    answers shared-L3 attacks (ch06/ch07). (c) Timing is a function of the
    work done; only a program whose work is independent of its secrets leaks
    nothing, and removing the *sharing* means not co-locating attacker and
    victim — the cloud's economic premise (ch07).

</details>
