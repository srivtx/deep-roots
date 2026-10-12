# Module 02 — Memory security & exploitation

> The dark mirror of module 01. Every memory-model decision — where a value lives,
> who owns it, what the metadata looks like — is an attack surface. This module
> reads chapters 04–06, 19, and 21 of module 01 *adversarially*: what an attacker
> does with the same knowledge a performance engineer uses.

**7 chapters · one final exam · prerequisite: module 01, especially chapters 04 (the
stack), 05 (the heap), 06 (object layout), 19 (allocators), 21 (memory security).**

## How to study it

Read in order: the first chapter builds the attacker's model, the middle five are the
weapon classes (stack, code-reuse, heap, use-after-free, the mitigation ladder), the
last is the class of attacks that corrupt nothing (side channels). Labs use gcc/gdb,
Python, and a Linux box — deliberately the same toolchain as module 01. Everything is
framed as *defensive engineering*: you cannot reason about mitigations you have never
built mentally from the attacking side.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [The attacker's model](chapters/01-attackers-model.md) | Trust boundaries, privilege, and the bug → primitive → exploit → impact chain. |
| 02 | [Stack exploitation](chapters/02-stack-exploitation.md) | Smashing the stack, canaries and their bypasses, off-by-one lore. |
| 03 | [Code reuse: ROP and friends](chapters/03-code-reuse.md) | DEP ends code injection; ROP, ret2libc, SROP, JOP answer it. |
| 04 | [Heap exploitation](chapters/04-heap-exploitation.md) | glibc metadata as an attack surface: tcache, fastbins, House-of-* lore. |
| 05 | [Use-after-free](chapters/05-use-after-free.md) | The modern #1 bug class: reclaim, confuse, control. |
| 06 | [The mitigation ladder](chapters/06-mitigation-ladder.md) | NX, ASLR, RELRO, CFI, PAC, MTE — the arms-race ledger with costs. |
| 07 | [Side channels](chapters/07-side-channels.md) | Cache timing, Spectre-class, Rowhammer — attacks that corrupt nothing. |

## Mandatory content per chapter (the writer's checklist)

**Chapter 01 — the attacker's model.** Threat model from first principles: assets,
trust boundaries, attacker capabilities. The bug → memory-safety primitive → exploit
technique → impact chain, with a worked example at each hop. Why memory corruption is
the crown jewel (arbitrary read/write ≈ game over; contrast with logic bugs). The
historical arms race as a timeline: 1988 Morris worm → 1996 Aleph One "Smashing the
Stack for Fun and Profit" → stack canaries (StackGuard, 1998) → W^X/NX (2003-2004) →
ASLR (2004-2005) → ROP (2007+) → CFI/MTE (2015-2020s). Attack surface as a
*memory-map* concept (which regions are reachable). The terminology contract: bug vs
vulnerability vs exploit vs payload vs primitive.

**Chapter 02 — stack exploitation.** Overflow into saved RIP with a real annotated
frame diagram (locals → canary → saved RBP → return address → args). A minimal
vulnerable C program and what gcc actually emits (note: modern gcc defaults include
canaries + PIE — show `-fno-stack-protector -no-pie` for the classic, then re-enable
and show the defense). Canary design specifics: glibc's terminator bytes (why the
canary starts with a NUL — string functions stop), fork() inherits the canary (the
crash-resets-canary trap), canary stored in TLS (tcbhead_t) and the large-overflow
path to overwrite it. Off-by-one overwriting the saved RBP's low byte on x86-32 (the
classic "poison NULL byte") and why x86-64 changed the game. GDB lab: watch the
canary check instruction sequence (`mov rax, fs:0x28` / `xor` / `jne __stack_chk_fail`).

**Chapter 03 — code reuse.** Why DEP/NX killed shellcode injection but not the
attacker: return-to-libc (`system("/bin/sh")` with the alignment gotcha on x86-64:
stack alignment to 16 bytes for movaps). ROP from first principles: gadgets = any
`ret`-terminated instruction sequence, chains as a weird little program, gadget
hunting (ropper/ROPgadget), ret-sleds for alignment. Stack pivots (`pop rsp`,
`xchg rax,rsp`) when you control only a small overflow. SROP: sigreturn as a
one-gadget "set all registers" syscall. JOP/COP as forward-edge variants. The
counter-mitigations: CFI (forward-edge checks), Intel CET shadow stack, and why ROP
persists anyway (gadget abundance in libc alone). Numbers: gadget counts in a typical
libc (order of magnitude, marked as tool-version dependent).

**Chapter 04 — heap exploitation.** glibc chunk anatomy from the attacker's side
(prev_size, size with flags PREV_INUSE/IS_MMAPPED/NON_MAIN_ARENA, fd/bk, tcache
next+key) — annotated byte diagram. tcache: 7 entries per bin (default), counts
array, the key field (double-free detection) and pointer mangling (safe-linking,
glibc ≥ 2.32: `ptr ^ (addr >> 12)`), tcache poisoning → arbitrary allocation/write.
Fastbins (single-linked, size-checked), unsorted bin (the bk trick as a libc-base
leak primitive). The House-of-* lore (Force, Spirit, Orange, Einherjar — what each
abuses in one line each; mark which glibc vintages). Why hardened targets switch
allocators (jemalloc/mimalloc/PartitionAlloc — different metadata = different attack
surface; Firefox's jemalloc decision). Everything positioned as chapter 19 of module
01 re-read adversarially.

**Chapter 05 — use-after-free.** Why UAF is the dominant modern class (C++ and
browsers: object graphs + async lifetimes). The three-step exploitation pattern:
free → spray to reclaim the slot (string/array grooming) → type confusion through
the stale pointer (vtable call through reclaimed memory). A worked minimal C++
example with a vtable diagram. Where UAFs arise in real code: observer/callback
registration, refcount drops in destructors, async task cancellation, DOM teardown
(browser case study: a public Chrome/WebKit UAF write-up's anatomy, summarized).
Mitigations and why each is partial: quarantines (delayed reuse — ASan's quarantine,
Chrome's PartitionAlloc), vtable/CFI checks, pointer poisoning on free. How Rust
eliminates the compile-time subset (module 01 ch10 payoff) and what it cannot fix
(unsafe blocks, caches across FFI boundaries).

**Chapter 06 — the mitigation ladder.** One table, one ladder: canaries, NX/DEP,
ASLR (entropy bits by platform: ~28 bits x86-64 Linux, fewer on Android — state the
platform), full RELRO (GOT read-only; partial vs full), PIE, `-fstack-protector-
strong` scope, forward-edge CFI (clang's cfi-vcall), Intel CET shadow stack, ARM64e
PAC (signing return addresses — which gadgets die), MTE (4-bit tags, 16-byte
granularity, 1/16 guess rate, shipped on Android Pixel 8). For each: what it costs
(runtime %, memory), what it kills, and its known bypass history — the honest
ledger. The composite stack on a modern Android/Chrome binary as the "why exploit
chains are now five bugs long" story.

**Chapter 07 — side channels.** Attacks that corrupt no bytes: Prime+Probe,
Flush+Reload (same-cache-line requirement → shared library pages as the attack
surface), Evict+Reload. Timing measurement mechanics (`rdtsc`, `perf` — and the
noise floor problem). Spectre-class from first principles: speculation as a
"transient execution" machine that leaves cache state behind; bounds-check-bypass
sketch (poison the branch predictor, train, then read out via cache timing);
Meltdown in one paragraph (why kernel mappings existed and KPTI's fix). Rowhammer
from DRAM physics (module 01 ch01 payoff: charge leakage between adjacent rows,
double-sided hammering, why ECC/TRR are partial and TRRespass bypassed TRR). The
defender's menu: constant-time code (no secret-dependent branches or memory
addresses), cache partitioning (CAT), shared-page hardening — and why "close the
timing channel" is asymptotically impossible.

## The discipline

Identical to module 01 — see [CONVENTIONS.md](../01-memory-models/CONVENTIONS.md):
30-second version, derived teaching, ASCII diagrams, *What insiders know* (≥5
specific bullets), *See it yourself* lab, *How to use this knowledge*, *Connections*
by chapter number, 8-question quiz with worked answers behind `<details>`.

## What this module buys you

- **Defensive code review**: recognizing the overflow/UAF/spray shapes in real diffs.
- **Mitigation literacy**: reading a CVE write-up and knowing *which* rungs of the
  ladder the bug had to climb.
- **Interview-grade security fundamentals**: canary design, ROP, tcache poisoning,
  Spectre — derived, not memorized.
- **The bridge to module 03** (bug taxonomy) and **module 04** (what these look like
  in binaries you reverse).
