# Chapter 06 — The mitigation ladder

> **The 30-second version.** Every rung of the memory-safety ladder — canary,
> NX, ASLR, RELRO, PIE, CFI, CET, PAC, MTE — was built *because the rung below
> it fell*, and each is cheap on purpose — a 20% mitigation gets turned off by
> one performance review. This chapter walks the ladder rung by rung with the
> only ledger that matters — what it kills, what it costs, how it was beaten —
> then assembles the composite stack on a modern Chrome-on-Android target: why
> an exploit that needed **one bug in 1996 needs about five in 2024**. The bugs
> are still there (~70% of severe CVEs, MSRC/Chromium 2019); the ladder
> changed the price.

## The economics of a rung

Derive the constraints that force the ladder's shape — a mitigation deploys
*everywhere* or attackers route to the unprotected build. So each rung must
be **nearly free** (runtime costs live in the ~1% band; the one exception
here, MTE at ~4-5%, needed a decade of hardware economics), must **kill a
class, not a bug** (bugs arrive by tens of thousands per decade, mitigations
about once every two years — a rung targets a technique *family*: linear
overflow, injected code, fixed addresses, hijacked dispatch), and must **fail
loudly** (a detectable crash buys forensics even without prevention). And
bypasses come in exactly three flavors — *information* (leak the secret),
*entropy* (guess it), *structure* (change technique) — a trichotomy that
predicts every bypass below.

The thesis, derived: each rung turns a capability the attacker once got free
into one that now needs a *distinct* bug. Capabilities are priced in bugs, so
the ladder's height *is* the exploit's length:

```text
 1996 ────── 2005 ────────── 2018-2023 ───────────── 2024
 1 bug        2 bugs         3 bugs                 ~5 bugs
 one          +ASLR/PIE:     +CFI/PAC/MTE:          +sandbox+kernel:
 overflow     you need       control-flow step      Chrome on Pixel 8
              a leak         inside the lattice
```

The honest corollary: no rung fixes a bug — the ~70% memory-safety share of
severe CVEs is stable; what moved is cost per exploit and how loud failures
are. The rest of the chapter is the itemized bill.

## The lower ladder: compiler and linker hardening (1998-2014)

**Rung 1 — the stack canary** (StackGuard, Cowan et al. 1998; ProPolice 2001;
`-fstack-protector` in gcc 4.1, 2006). The epilogue compares the frame's
8-byte canary against the master in TLS (`fs:0x28` on x86-64) and calls
`__stack_chk_fail` on mismatch, making the *linear* overflow into RIP
self-defeating (frame layout: ch02). Kills: the 1996 stack smash. Costs:
8 bytes + one TLS read + one compare per protected frame, ~1% average under
`-fstack-protector-strong`. Scope: base `-fstack-protector` covers only
`char` arrays and `alloca`; `-strong` (gcc 4.9, 2014) adds arrays of *any*
type and size plus address-taken locals; `-all` is every function. Distros
default to `-strong` because base misses `int[4]`.

Bypass history, all three flavors: *information* — leak the canary (OOB
read, format string); *entropy* — glibc's canary ends in a NUL byte, so 7
bytes are unknown, and a forking server inherits one canary in every child:
byte-at-a-time brute force costs at most 7 x 255 = **1,785 child crashes**
(ch02's trap run forward); *structure* — corrupt a pointer *below* the
buffer or ch02's off-by-one into saved RBP: data-only overflows walk around
the canary.

**Rung 2 — NX/DEP** (Linux and Windows XP SP2, both 2004). One bit of module
01 ch03's PTE — NX, bit 63 on x86-64 — makes stack and heap RW, never X.
Kills: injected shellcode in data pages. Cost: zero; the hardware consults
the PTE on every access anyway. First bypass: Shacham's ROP (CCS 2007) —
don't inject code, *reuse* the already-executable libc; any `ret`-terminated
byte sequence is a gadget, and one glibc offers ~10^4+ usable ones (ch03's
census). Lesson for every later rung: removing memory *executability* while
leaving existing code executable only prices engineering time.

**Rung 3 — ASLR** (PaX 2001; mainline Linux 2.6.12, 2005; Vista 2007; macOS
10.5 2007). Randomizes base addresses at exec and mmap — stack top, mmap
region, brk, and (with PIE) the main binary. Kills: precomputed addresses.
Cost ~0; the knob is `kernel.randomize_va_space` (0/1/2, distros ship 2).
What decides everything is **entropy in bits**:

- x86-64 Linux, kernel 6.x: ~**28 bits** for the mmap/PIE base
  (`vm.mmap_rnd_bits`, default 28, max 32); the stack gap is smaller (22
  page-granular bits).
- arm64 Android: **18 bits** until Android 12 (2021), **24 bits** since.
- 32-bit Android: on the order of **16 bits** — brute-forceable in minutes.
- Windows DLLs and the iOS/macOS dyld shared-cache slide randomize **once per
  boot** — one leak covers every process until reboot.

Bypasses: *information* — the leak became the post-2005 era's most valuable
primitive (one leaked .text pointer reveals the whole module); *entropy* —
16-bit targets die by brute force; *structure* — partial overwrites (low 12
bits are page offsets) and fork servers (one child's leak compromises all).

**Rungs 4 and 5 — PIE and RELRO.** PIE compiles ET_DYN so the main image
joins ASLR; distros made it gcc's default between 2015 and 2018 — without it,
`.text` sits at a fixed 0x400000-style base forever. Cost ≈ 1% on x86-64.
RELRO fences the GOT: module 01 ch08's PLT/GOT dispatch — every imported
function leaves a pointer the program will call *for you*. Partial RELRO
(`-z relro`, mid-2000s) protects `.got` and `.init_array` but leaves
`.got.plt` writable for lazy binding; **full RELRO** (`-z relro -z now`)
binds every symbol eagerly at startup, then makes the whole GOT read-only.
Cost: eager relocation, milliseconds (tens for large C++ binaries). Kills:
the GOT overwrite and `ret2dlresolve`. Bypass history: the write target
*moves* — vtables in `.data`, `__malloc_hook`/`__free_hook` (until glibc
2.34, Aug 2021, deleted them), `__exit_funcs`, FILE vtables (ch04's
terrain). It never removes the arbitrary-write primitive; it fences one
pasture and raises the next fence's price.

## The upper ladder: the ISA fights back (2015-2023)

**Rung 6 — forward-edge CFI** (clang `-fsanitize=cfi`, 2015-era; requires
LTO). Ch05's UAF pattern ends in a virtual call through a reclaimed slot;
CFI checks the dispatch itself — `cfi-vcall` verifies the vtable's type hash
against the expected class, `cfi-icall` verifies indirect-call targets
against their signature. Kills: cross-type vtable hijack: the forged table
must belong to the *same CFI class* or the call traps. Cost: sub-2% runtime
(Chromium 2017-2018-era telemetry) plus LTO link times — whole-program
knowledge is why it needs LTO, and why mixed C++/Rust builds waited for
cross-language CFI (clang 17-18 era, 2023-2024). Shipped: Chrome on
Android/ChromeOS since 2017-2018; kernel kCFI since 6.1 (Dec 2022). Bypass:
the *CFI downgrade* — a victim with the same signature and useful semantics,
or JOP inside legal entries. CFI is a lattice, not a wall: it bounds targets
to a type class, not intent.

**Rung 7 — Intel CET shadow stack** (spec 2016; first silicon Tiger Lake,
2020; Windows 10 2004, 2020; Linux 6.6 user shadow stacks, Oct 2023; glibc
enablement still landing, 2.41 era, 2024-2025). The CPU keeps a
second, hardware-only stack of return addresses: `call` pushes to both, `ret`
compares; mismatch raises a control-protection fault (#CP).
This kills ch03's entire return-hijack economy — ROP, ret2libc,
corrupted-frame pivots — for *every return in the process*. Cost: near-zero
on CET CPUs (a parallel hardware push) plus 8 bytes per frame. The 17-year
gap between Shacham (2007) and Linux 6.6 (2023) is the price of *runtime
cooperation*: `setjmp`/`longjmp`, C++ unwinding, and fibers must restore the
shadow stack or every legitimate unwind faults — hence the libc dependency
and thin deployment as of 2025. IBT, CET's other half,
restricts indirect branches to `ENDBR64` landing pads. Bypasses:
forward-edge JOP between legal entries is untouched; data-only attacks
ignore it.

**Rung 8 — ARM PAC** (Armv8.3-A pointer authentication; first consumer
silicon Apple A12, iPhone XS, Sept 2018; Linux arm64 rotates per-process keys
at execve, 2020-era kernels). Each function entry signs LR with a keyed MAC
of the address and SP (`PACIBSP`); the epilogue authenticates (`AUTIBSP`)
before returning, so a forged return address faults. Which gadgets die:
everything that *loads a return address from corrupted memory and returns
through it* — ROP chains, `pop x30; ret` pivots, ret2libc through overwritten
frames. What survives: JOP/COOP to legitimate entries (forward edge — BTI and CFI's
job), and *leaked* signed pointers, which are pre-forged — replay a leaked
LR as-is. Bypass history: signing-oracle gadgets that sign attacker-chosen
values (Liljestrand et al., EuroS&P 2019), and PACMAN (2022, Apple M1), a
speculative-execution oracle that recovers signatures bit-by-bit: leak, then
forge. Cost: two instructions per frame, sub-1%, zero memory. Apple runs PAC
in kernel and system code as the `arm64e` ABI — Apple's own binaries only as
of 2024; third-party iOS code runs plain arm64.

**Rung 9 — MTE** (Armv8.5-A, 2019; first consumer hardware Tensor G3, Pixel
8, Oct 2023; kernel support 5.10-5.14, 2020-2021; Android 12 APIs, 2021;
Chrome 128, Aug 2024, in its renderer on MTE hardware). This rung is
different: it tags *data*, not control flow.

```text
 AArch64 pointer (48-bit VA + MTE):        memory, per 16-byte granule:
 63      56 55                          12 11    0   +--------+-----------+
+----------+-----------------------------+--------+   | tag 4b | 16 B data |
| top byte |   virtual page number      | offset |   +--------+-----------+
| (TBI)    |  MTE tag nibble rides here |        |     one tag per granule:
+----------+-----------------------------+--------+     4 bits / 128 data bits
                                                     = 3.125% memory floor
```

Every tagged access compares pointer tag against granule tag;
mismatch faults (sync: SIGSEGV, `si_code = SEGV_MTESERR`, at the faulting
instruction, with the offending address) or is logged (async). What
it kills: the *wild access itself* — a stale pointer into a
freed-and-reclaimed slot survives only if the new occupant's tag matches: probability **1/16** per fresh process (tags re-roll per
allocation; scudo — Android's default since Android 11, 2020 — staggers
adjacent tags to break same-tag sprays). Out-of-bounds neighbors die the
same way. Cost: ~4-5% runtime in sync mode (Arm's benchmarks) — the ladder's
most expensive rung — plus the 3.125% metadata floor and granule rounding.
Honest ledger: 1/16 is a number, not a wall — a restartable victim gives an
expected 16 attempts, each failure a loud crash; sync converts ~94% of naive
UAF attempts into a report with the faulting address. It is the first rung
that *touches the bug*, not the payload.

## The ladder assembled

One table, the whole arms race — read a row, then the bypass column top to
bottom:

| Rung (born) | Lives in | What it kills | Cost | First bypass (flavor) |
|---|---|---|---|---|
| Canary (1998) | compiler | linear overflow into RIP | ~1% + 8 B/frame | leak it, brute it, walk around it (info/entropy/structure) |
| NX/DEP (2004) | PTE bit | injected shellcode | ~0 | ROP, Shacham 2007 (structure) |
| ASLR (2005) | kernel exec/mmap | precomputed addresses | ~0 | info-leak economy; brute force on 16-bit targets (info/entropy) |
| PIE (2015-18 default) | ELF ET_DYN + loader | fixed .text base | ~1% | any code-pointer leak (info) |
| Full RELRO (-z now) | linker, mprotect | GOT overwrite, ret2dlresolve | startup ms | target moves to hooks/exit/FILE (structure) |
| CFI (2015) | compiler + LTO | cross-type vtable hijack | <2% + link time | same-signature target, the "CFI downgrade" (structure) |
| CET shadow stack (2016/2020) | CPU + runtime | all return-hijack ROP | ~0 + 8 B/frame | JOP between legal entries; data-only (structure) |
| PAC (2018) | CPU keys | forged return addresses | <1% | signing oracles; leaked signed pointers; PACMAN 2022 (info) |
| MTE (2023, Pixel 8) | CPU + allocator | wild reads/writes (UAF/OOB) | ~4-5% + 3.125% mem | 1/16 per fresh process (entropy) |

And what the ladder *cannot* do — attackers read the same table you do:
**it guards control flow, not data flow.** An overflow that flips an
`is_admin` flag or corrupts a length changes no return address and tags
nothing wild — CFI, PAC, CET, and canaries are silent; MTE catches it only if
the access is out-of-bounds rather than in-bounds-but-wrong. Side channels
(ch07) corrupt nothing. The ladder raises prices; it does not repeal
economics.

## The composite stack: why one bug is no longer enough

Run `checksec` on Chrome for Android, Pixel 8-class (Android 14, Chrome
128-era, arm64): PIE with 24-bit ASLR, NX, strong canaries, full RELRO,
clang CFI in the renderer, BTI/PAC, MTE sync mode for Chrome's allocations —
and beyond the ladder, the V8 heap sandbox (rolled out 2022-2024) and the
seccomp renderer sandbox with site isolation. A working chain must buy: (1) the memory bug whose *use* survives
MTE's 1/16 and steers inside CFI's lattice — frequently two bugs (a UAF plus
a leak); (2) an info leak for ASLR/PIE if not already owned; (3) a
renderer/V8-sandbox escape (usually an IPC/mojo bug); (4) a kernel privilege
escalation. Four to five distinct vulnerabilities, chained and maintained
across patch cycles — why full mobile chains fetched ~$500k at Pwn2Own
(2023-2024) and seven figures from brokers (Zerodium-era pricing, 2019: up
to $2.5M). In 1996, one bug was root. The ladder multiplied capabilities;
capabilities are priced in bugs.

Last derivation, the auditor's opening move: **the ladder is only as strong
as the weakest toolchain in the building.** Every rung ships default-off
somewhere — embedded vendor SDKs still emit 1996 binaries (no canary, no PIE,
no RELRO). Before reading code, run `checksec`: it tells you whether you are
auditing this chapter's ladder or ch02's playground.

## What insiders know

- **The ASLR entropy ledger, by platform**: x86-64 Linux (kernel 6.x,
  `randomize_va_space=2`): 28 bits for the mmap/PIE base (`vm.mmap_rnd_bits`,
  default 28, max 32), 22 page-granular bits for the stack gap, 8 for
  compat-32 mmaps (`mmap_rnd_compat_bits`). Android arm64: 18 bits until
  Android 12 (2021), 24 since. 32-bit Android: ~16 bits, brute-forceable in
  minutes. Windows DLLs and the iOS/macOS dyld shared-cache slide: per
  **boot**, not per process — one leak covers every process until reboot.
- **checksec's three half-truths**: "Canary found" only means
  `__stack_chk_fail` is *imported* — one protected function anywhere sets it;
  "NX enabled" reads the `GNU_STACK` ELF header, not runtime page tables;
  "Full RELRO" is literally `BIND_NOW` in `.dynamic`. Insiders skip the tool
  and run `readelf -lW | grep -E 'GNU_STACK|GNU_RELRO'` and
  `readelf -dW | grep NOW`.
- **The default-on matrix**: Ubuntu 22.04 plain `gcc`: canary + NX + PIE,
  RELRO *partial* (full needs explicit `-z now`). Visual Studio 2015+:
  `/GS /DYNAMICBASE /NXCOMPAT /HIGHENTROPYVA`. Chrome: full RELRO + PIE +
  strong canary + clang CFI (Android, 2017-2018) + MTE (Chrome 128,
  Aug 2024, Pixel 8-class). Android platform: scudo (Android 11+),
  branch protection from the Android 10-12 era, 24-bit arm64 ASLR (12+).
  The gap that pays for audits: firmware toolchains — often none of the
  above.
- **MTE tag math**: 4-bit tags on 16-byte granules = 4/128 = 3.125% metadata
  floor; attacker success probability 1/16 per fresh process, expected 16
  attempts on a restartable victim, each miss a SIGSEGV/`SEGV_MTESERR`; 8-bit
  tags would give 1/256 at 6.25% memory — Arm chose 4 bits as the knee of the
  cost curve.
- **glibc 2.34 (Aug 2021) deleted `__malloc_hook`/`__free_hook`** — the
  canonical post-full-RELRO write targets died in one release. The ledger
  moved to FILE vtables (constrained by `_IO_validate_vtable` to the
  `_IO_jump_t` family — House-of-Apple lore, ch04) and `__exit_funcs`
  (mangled with the TLS pointer guard — a second secret to leak).
- **Bug pricing folklore** (how exploit authors talk about this chapter):
  ASLR/PIE = +1 leak (one .text pointer reveals the whole module); CFI = +0
  or +1/2 (same-signature victim); PAC = +0 with a signed-pointer leak, +1
  without; each sandbox layer = +1 escape. "Five bugs" = memory bug + leak +
  control-flow step + sandbox escape + kernel LPE. The interview trap: "PIE
  is ASLR" — no: PIE is the compile mode (ET_DYN) that makes the main binary
  randomizable; ASLR is the kernel shuffling, and `randomize_va_space=1` +
  non-PIE leaves `.text` fixed forever.

## See it yourself

One source file, four builds, and the ladder in a terminal:

```c
/* vuln.c — one frame with an array (canary bait) and one leaf without */
#include <string.h>
void leaf(int x) { (void)x; }          /* no array, no address taken: naked  */
void hello(const char *s) {
    char buf[16];                      /* array local: -strong protects this */
    strcpy(buf, s);                    /* the 1996 bug                       */
}
int main(int argc, char **argv) { hello(argc > 1 ? argv[1] : "x"); return 0; }
```

```bash
# 1. the 1996 build, 2. distro default, 3. the hardened ladder
gcc -fno-stack-protector -no-pie -Wl,-z,norelro,-z,execstack -o off  vuln.c
gcc                       -o def  vuln.c
gcc -fstack-protector-strong -fPIE -pie -Wl,-z,relro,-z,now -o hard vuln.c
# 4. clang CFI — without -flto it is a compile error: the LTO requirement
clang -flto -fsanitize=cfi -o cfi vuln.c   # (cfi-vcall proper needs C++; plain
                                           #  C still gets the icall checks)
```

```text
$ checksec --file=off          # classic-style output; 2.x adds Fortify columns
RELRO      Canary      NX          PIE          FILE
No RELRO   No canary   NX disabled No PIE       off    <- -z execstack => RWE
$ checksec --file=def          # Ubuntu 22.04 / gcc 11 baseline:
Partial RELRO  Canary found  NX enabled  PIE enabled  def
$ checksec --file=hard
Full RELRO     Canary found  NX enabled  PIE enabled  hard
```

Confirm in the ELF itself:

```text
$ readelf -lW off | grep -E 'GNU_STACK|GNU_RELRO'
  GNU_STACK      0x0 0x0 0x0 0x0 0x0 RW  0x10     <- RWE would mean execstack
(no GNU_RELRO line — its absence *is* the finding)
$ readelf -lW hard | grep GNU_RELRO
  GNU_RELRO      0x2dd8 0x2dd8 0x2dd8 0x227 0x227 R  0x1
$ readelf -dW hard | grep -E 'NOW|FLAGS'            # full vs partial:
  0x0000001e (FLAGS)      BIND_NOW                   # BIND_NOW = full RELRO
  0x6ffffffb (FLAGS_1)    Flags: NOW
```

Now the canary, in the emitted code — the strongest evidence is which
function does *not* get one:

```text
$ gcc -O0 -fno-stack-protector -c vuln.c -o a.o && objdump -d a.o
<leaf>:   push rbp; mov rbp,rsp; ...; ret          <- naked
<hello>:  push rbp; mov rbp,rsp; sub rsp,0x20; ... <- naked
$ gcc -O0 -fstack-protector-strong -c vuln.c -o b.o && objdump -d b.o
<leaf>:   push rbp; ...; ret                       <- STILL naked: no array,
                                                    no address taken — the
                                                    scope rule, made visible
<hello>:  ...  mov rax,QWORD PTR fs:0x28      <- canary from TLS
          ...  mov QWORD PTR [rbp-0x8],rax    <- stored below locals
          ...  xor eax,eax                    <- can't leak it via rax
          ...  mov rax,QWORD PTR [rbp-0x8]
          ...  xor rax,QWORD PTR fs:0x28      <- re-derive and compare
          ...  je   .Lok
          ...  call __stack_chk_fail          <- abort: SIGABRT
.Lok:     leave; ret
$ readelf -s b.o | grep stack_chk
  UND __stack_chk_fail                            <- the import checksec saw
```

One rung up: `gcc -fcf-protection=full -c vuln.c`, then
`objdump -d vuln.o | grep -c endbr64` — one `endbr64` landing pad per
function entry: CET's IBT half as bytes. On arm64,
`clang -mbranch-protection=standard -c vuln.c` emits `pacibsp` at entries.
And `./hard $(python3 -c 'print("A"*17)')` dies loudly with
`__stack_chk_fail` — 16 bytes survive, 17 do not.

## How to use this knowledge

- **Ship the flag line, gate it in CI.** For C/C++ production binaries:
  `-fstack-protector-strong -D_FORTIFY_SOURCE=2 -fPIE -pie
  -Wl,-z,relro,-z,now`, plus `-fcf-protection=full` (x86-64) or
  `-mbranch-protection=standard` (arm64), plus `-fsanitize=cfi` under LTO
  where the build can take it. One CI step that runs `checksec` and fails on
  regression — mitigations silently rot when a build-system rewrite drops a
  flag.
- **Read exploit write-ups with the ledger in hand.** A GOT write that
  segfaults, pivoting to a FILE-vtable target — that is full RELRO doing its
  job and losing anyway, per the bypass column. You can reconstruct *which
  rungs a chain climbed* from the write-up alone — this module's promised
  skill.
- **Triage crashes by rung.** `__stack_chk_fail` in the backtrace is not "a
  crash," it is a *caught linear stack overflow* — go straight to ch02 frame
  analysis. SIGSEGV with `SEGV_MTESERR` on Android is a genuine UAF/OOB
  touch — do not close it as flaky; the report holds the faulting address.
- **Budget and evaluate in bugs.** The ladder under CFI costs roughly 2-3%
  combined on x86-64 userland (canary ~1%, PIE ~1%, CFI <2%, the rest ~0);
  MTE's 4-5% + 3.125% is the only line item needing a meeting. And when a
  pentest delivers an RCE chain, count its bugs: one bug long means missing
  2010-era defaults (fix the build, not the code); five means state of the
  art, and the fix is memory-safe languages at the bug *source* (module 01
  ch10's payoff).

## Connections

- **Module 01 ch03** — the page table is where ASLR's randomness, NX's bit
  63, and RELRO's mprotect physically live; this ladder is a VM chapter in
  disguise. **ch21** — the ladder's first act; **ch10** — the rung that
  removes the bug instead of catching it.
- **Module 02 ch02** — canary anatomy in full (terminator NUL, TLS, the
  off-by-one that dodges it); the 1,785-crash brute force derived here.
- **Module 02 ch03** — the ROP/JOP/SROP economy NX created, and why the
  shadow stack took 17 years to answer it.
- **Module 02 ch04** — the post-RELRO write-target ledger: tcache, FILE
  vtables, exit handlers, glibc vintages.
- **Module 02 ch05** — the UAF duel: reclaim-vs-CFI lattice, reclaim-vs-MTE
  1/16, and why the three-step pattern now needs a fourth step.
- **Module 05** — where Android's defaults live: 24-bit arm64 ASLR (12),
  scudo, Pixel 8 MTE, Chrome 128. **Module 06** — iOS: arm64e PAC from A12,
  the per-boot dyld shared-cache slide, code signing as the trust chain.

## Quiz

1. Your build is non-PIE (ET_EXEC) and the box runs `randomize_va_space=1`.
   What is randomized, what is fixed, and what does the attacker get for
   free?
2. A stale pointer to a freed 32-byte object on an MTE system in sync mode;
   each attempt is a fresh process. Expected attempts to a tag match, what
   does each failure cost, and why did Arm pick 4 tag bits over 8?
3. A fork-server (identical children per request) runs glibc with stack
   canaries; the attacker overflows into the canary byte-at-a-time. Maximum
   number of child crashes, and why is the first byte free?
4. A crash report shows `SIGABRT` with `__stack_chk_fail` in the backtrace
   after a `strcpy` into a 16-byte local. Why did the process abort *before*
   the return address mattered, and what does the canary actually protect?
5. A pentest chain has an arbitrary write in a binary whose checksec reads
   "Full RELRO, PIE, NX, Canary found"; their write to `printf@got` faults.
   Why? Name two targets they reach for instead, and the extra secret one of
   them requires.
6. "The ladder guards control flow, not data flow." Give one attack where
   every rung stays silent, and say why MTE does or does not catch it.
7. Tie to module 01 ch03: which single hardware structure do ASLR and NX both
   live in, which PTE bit is NX on x86-64, and why is NX's runtime cost ~0
   *because* of that structure?
8. Order the 2024 Chrome-on-Pixel chain's five priced capabilities and match
   each to the rung or sandbox layer that forced it. Which rung first
   converted "one bug" into "two," and which rung is priced in expected
   attempts instead of bugs?

<details>
<summary>Answers</summary>

1. `randomize_va_space=1` randomizes the stack top, mmap region, and brk —
   not the main executable. A non-PIE binary's `.text` sits at link-time
   addresses (0x400000-style) forever — every gadget in the *binary itself*
   is free; randomized libc still needs a leak. PIE is the compile mode that
   closes this; ASLR is the kernel mechanism it depends on.
2. Geometric with p = 1/16: expected 16 attempts. Each failure is a SIGSEGV
   (`SEGV_MTESERR`) that kills the process — loud, logged, and it re-rolls
   the tags, so failures accumulate no knowledge. 8-bit tags would give 1/256
   per attempt but double the metadata floor to 6.25% of RAM (8/128 bits);
   Arm picked 4 bits as the memory-vs-resistance knee; sync mode's
   crash-per-miss does the rest.
3. The low canary byte is always 0x00 (glibc's terminator), so it is known.
   Seven unknown bytes remain; the crash oracle (wrong guess = child dies,
   parent reforks with the *same* inherited canary) lets you iterate each
   byte's 255 wrong values: at most 7 x 255 = 1,785 crashes, expected
   ~7 x 127.5 ≈ 893. The fork-inheritance is the bug: exec re-randomizes,
   fork does not.
4. The epilogue compared the frame's copy against TLS (`fs:0x28`) and found
   a mismatch, so `__stack_chk_fail` aborted *before* `ret` loaded the
   corrupted return address. The canary protects exactly one thing: the saved
   return path of instrumented frames. Diagnosis: a linear stack overflow
   (ch02) — and nothing *below* the canary was protected.
5. Full RELRO ran `-z now`: PLT entries eagerly bound at startup, whole GOT
   read-only — the write hits a RO page. Post-glibc-2.34 targets: FILE
   vtables (constrained to the `_IO_jump_t` family by `_IO_validate_vtable`
   — House-of-Apple, ch04) and `__exit_funcs` handlers, mangled with the TLS
   pointer guard — that route needs a *second* secret leak.
6. Example: an overflow that flips an `is_admin` boolean in `.data`, or
   corrupts a length to widen a later *legal* read. No return address,
   vtable, or indirect call changes: canary, CFI, PAC, and CET are silent.
   MTE stays silent *if* the access is in-bounds of validly tagged memory —
   tags catch wild accesses, not wrong-but-legal ones. That is why
   data-only corruption is the residual risk.
7. The page table, and its cached form the TLB. ASLR only chooses which
   *virtual* addresses to map — the walk resolves them identically
   regardless of base. NX is PTE bit 63. Cost ~0: the hardware consults the
   PTE via the TLB on every access anyway; one more bit adds no work to a
   lookup that must happen regardless.
8. (1) The memory bug whose *use* survives MTE's 1/16 and steers inside
   CFI's lattice (MTE, CFI); (2) an info leak for ASLR/PIE — the first rung
   in history to convert one bug into two (ASLR, 2005); (3) a
   CFI/PAC-compatible control step (forward edge — priced in target choice,
   often +0); (4) a renderer/V8-sandbox escape (+1); (5) a kernel LPE (+1).
   The rung priced in expected attempts rather than bugs is MTE: entropy
   (1/16 per fresh process), not structure.

</details>
