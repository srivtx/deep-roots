# Chapter 01 — The attacker's model

> **The 30-second version.** Module 01 taught you what memory *is*; this module
> teaches what happens when the wrong bytes get into it. An exploit is a chain:
> a **bug** (a missing check) yields a **primitive** (a memory operation —
> overwrite N bytes here, read M there), a **technique** turns the primitive
> into control, and the **impact** is what the attacker gains. Memory corruption
> is the crown jewel of bug classes because arbitrary read + arbitrary write is
> a skeleton key that works on any C/C++ program. This chapter builds the
> threat model — assets, boundaries, capabilities — walks the 40-year arms
> race, and fixes the five terms (bug, vulnerability, exploit, payload,
> primitive) the module speaks.

## 1. Assets, trust boundaries, attacker capabilities

A process is a flat address space (module 01 ch03) full of bytes. Sort them by
what the *machine* does with them, and the attacker's three assets fall out:

1. **Control data** — bytes the CPU fetches as instructions or dereferences as
   addresses: return addresses (ch04), function pointers in `.data` (ch08),
   vptrs at offset 0 (ch06), GOT entries (ch07).
2. **Trusted metadata** — bytes the program believes without checking:
   lengths, indices, type tags, a uid, a refcount — read by the program as
   decisions, written by the attacker as levers.
3. **Secrets** — keys, tokens, messages. Reading them is the whole attack;
   nothing needs corrupting.

The CIA triad is that table in disguise: read secrets — confidentiality;
rewrite control or metadata — integrity; corrupt randomly — availability.
Impact = byte class × damage.

Boundaries: page tables enforce isolation only at *privilege transitions* —
syscall entry, cross-process access. Inside one process there are no walls:
pages are writable per their PTE flags, and the CPU executes whatever it
fetches. The only doors are ones the OS opens — `recv()`, `read()`, IPC,
file opens. A **trust boundary** is any line across which less-trusted bytes
become readable by more-trusted code: network→daemon, file→parser,
other-uid→setuid, JavaScript→engine (ch15). The exploit never breaks a door;
it smuggles *meaning* through one — data that arrives as input and, one bug
later, is read as control data.

```text
        [ network: completely untrusted bytes ]
              |  (1) recv() — the legal door: bytes enter as *data*
              v
   +--------------------------------------------------------------+
   |  process address space (ch03: private, flat, no walls)       |
   |                                                              |
   |   parse() frame            heap                  .data       |
   |   [len:2][buf:64][fp:8]    [chunks + metadata]    [fn ptrs]  |
   |      (2) BUG lands          (3) PRIMITIVE buys     (4) IMPACT |
   |          here                   here                   here  |
   +--------------------------------------------------------------+
              |  (5) the door swings back OUT under attacker control
              v
        [ exfiltrated keys / a spawned shell ]
```

The last variable is **attacker capability** — what the adversary can already
do. It prices the vulnerability:

| Capability | Position | Implication |
|---|---|---|
| Remote, zero-click | Parses unsolicited data: preview, VOIP ring | Maximum value; CVSS `AV:N, UI:N`; the WhatsApp class |
| Remote, one-click | Opens your file, visits your URL | Still `AV:N`, user cooperates once |
| Local, unprivileged | Other account, same machine | Setuid and local daemons; `AV:L` halves the CVSS base |
| Same process | JavaScript in your renderer sandbox (ch15) | Bug needed is only a *sandbox escape* |
| Adjacent / physical | Rowhammer, DMA, cache probing | No bytes corrupted — chapter 07 |

This module is one sentence applied six ways: **the attacker crosses a trust
boundary with bytes; a memory-safety bug converts them into control data.**

## 2. The chain: bug → primitive → technique → impact

Exploit engineering is four separable skills, with separate markets — stages
(2) through (4) in the diagram above, plus the payoff. A worked example at
every hop — the program you will run in the lab. A daemon reads a 2-byte
length, then copies that many bytes into an 8-byte field:

```c
struct Rec { char name[8]; void (*fp)(void); };  /* fp sits 8 bytes above name */
uint16_t len; char buf[512];
fread(&len, 2, 1, stdin);                        /* attacker writes this field  */
fread(buf, 1, len > sizeof buf ? sizeof buf : len, stdin);
memcpy(r.name, buf, len);                        /* BUG: len never checked vs 8 */
r.fp();                                          /* PRIMITIVE lands here        */
```

- **Bug.** `len` is never compared to `sizeof r.name` — one missing
  comparison. Every bug in this module is one unchecked invariant from module
  01: a size, a lifetime, a type.
- **Primitive.** "Write up to 511 attacker-chosen bytes starting at `r.name`
  — full control of the adjacent 8-byte code pointer." The canonical
  primitives are **arbitrary read** and **arbitrary write**
  ("write-what-where," in CVE prose); real bugs first grant limited versions
  (linear, off-by-N, one-byte), and a technique's first job is upgrading
  them.
- **Technique.** Send `len = 16`: 8 junk bytes plus the address of a function
  you prefer. At `r.fp()` the call goes through attacker-chosen bytes — a
  control-flow hijack from perfectly legal operations. Under NX + ASLR it
  grows: leak an address (beat ASLR), aim at *existing* code (beat NX) —
  chapter 03's ROP.
- **Impact.** In the lab, a printed line; in production: execution as the
  daemon's uid, the TLS keys elsewhere in the address space, a sandbox
  escape. Same four hops — only the payload differs.

The shape scales to famous incidents. Heartbleed (CVE-2014-0160, OpenSSL
1.0.1–1.0.1f, April 2014): the heartbeat trusts its own 2-byte length (bug)
→ out-of-bounds *read* of up to 65,535 bytes of adjacent heap (primitive) →
malformed heartbeats in a loop (technique) → private TLS keys disclosed
(impact). No write primitive, so impact caps at confidentiality — "just a
leak" ≠ "just a write." WhatsApp (CVE-2019-3568, NSO Group, fixed May 2019):
VOIP overflow → write primitive → a crafted call → code execution on a phone
whose owner did *nothing* — zero-click, the boundary crossed on the ring
itself.

## 3. Why memory corruption is the crown jewel

Compare families by what the attacker gets. A **logic bug** — auth bypass,
wrong price — grants one wrong answer from the program's own vocabulary:
bounded; you are still inside the program doing program things. A
**memory-safety bug** hands over the machine *underneath* it:

1. Arbitrary write makes some bytes *somewhere* attacker-chosen. A GOT entry,
   a vptr at offset 0 (ch06), the `is_admin` byte — all just bytes at
   addresses.
2. Overwriting a *code pointer* redirects *existing* code. NX bans "execute
   my injected bytes"; it does not ban "call `system` with my argument" —
   those code bytes were placed by the linker (ch03's ret2libc, ROP).
3. Chaining redirections through `ret`-terminated fragments yields arbitrary
   computation from the process's own instructions — a "weird machine"
   (Dullien's term): corrupted state driving a real CPU.
4. Arbitrary read closes the last gap: one leaked pointer re-derives every
   base (ch03's fixed offsets, used against their designer).

Insiders' composition rule: **leak + write ≈ game over.** Neither alone is
fatal — Heartbleed was read-only, many heap bugs are write-only — but together
they work on *any* C/C++ program: that universality is the crown-jewel claim,
and what module 01 ch21's data looks like read adversarially (~70% of severe
CVEs at Microsoft MSRC and Chromium, 2019). A quieter reason: *programmable*
severity — a logic bug's impact is fixed at report time; a write primitive's
is decided later by whoever spends engineering: today a crash, QA files it
`DoS, low`; next year someone aims it (chapter 02).

## 4. The attack surface is the memory map

Module 01 ch03 had you read `/proc/pid/maps`; the attacker reads it as a menu.
The **attack surface** is the set of regions that attacker-influenced bytes
can reach — by landing in them (an overflow writes adjacent bytes), by
addressing them (a corrupted pointer), or by timing them (ch07). The map
becomes this module's table of contents:

```text
   higher addresses
   0x7ffd_xxxx_xxxx  [ stack ]   frames: locals, saved RIP, canary ..... ch02
   0x7fxx_xxxx_xxxx  [ libc ]   code + data: gadgets, system() ........ ch03
   0x7fxx_xxxx_xxxx  [ heap ]   chunk metadata, C++ objects, vptrs ..... ch04-05
   0x5555_xxxx_xxxx  [ .data ]  global fn pointers, .got/.got.plt ..... ch02-03
   0x5555_xxxx_xxxx  [ .text ]  the instruction stream (R-X under NX) ... target
   0x0000_0000_0000  [ guard ]  unmapped: any deref = SIGSEGV .......... noise
   (PIE/ASLR move every base at exec; the order is fixed)
```

Each region buys a different primitive: the stack's saved RIP and locals
(chapter 02); a heap chunk header's `size`/`fd`/`bk`/tcache fields (chapter
04); a heap object's vptr at offset 0 and length fields (chapter 05);
`.got.plt`'s one-write redirect of the next call (chapters 02–03). libc
`.text` is not writable but readable — gadgets are ammunition (chapter 03).
Kernel memory is not in your map at all: syscall bugs or chapter 07's
channels (modules 05–07).

Two defender moves follow, both *impact* engineering: **compartmentalize** —
parse untrusted input in a sandboxed child with no secrets mapped (Chrome's
renderer model), so the same primitive buys less; **shrink the map's value**
— `seccomp`, dropping privileges after `bind()`, closing doors for step (5).
Neither finds a bug; both are why modern chains need more of them (chapter
06's ledger).

## 5. Forty years of arms race

Every mitigation is a *reply* to a named attacker move, and the reply date is
the part outsiders get wrong. Chapter 06 prices each rung; the middle chapters
weaponize around each:

| Year | Attacker move | Defender rung | Residual hole |
|---|---|---|---|
| 1988 | **Morris worm**: `gets()` overflow in BSD `fingerd` + `sendmail DEBUG`; ~6,000 of ~60,000 hosts in days | Nothing yet — the event creates CERT | ch02 |
| 1996 | **Aleph One**, "Smashing the Stack for Fun and Profit" (Phrack 49, File 14): the canonical frame diagram | — | ch02 |
| 1998 | — | **StackGuard** (Cowan et al., USENIX Sec 1998): canary before the return address → ProPolice (2000) → gcc `-fstack-protector` (gcc 4.1, 2006) | Canary leak — ch02 |
| 2003–04 | Injected shellcode on the stack | **W^X/NX**: OpenBSD 3.3, AMD64 NX (2003), Windows XP SP2 DEP (2004) | Code reuse, already 6 years old — ch03 |
| 2004–05 | Jump to *known* addresses | **ASLR**: PaX (2001) → Linux 2.6.12 `randomize_va_space` (2005); Windows Vista (2007) | One leak re-derives all bases — ch03, ch06 |
| 2007+ | **ROP**: Shacham, "The Geometry of Innocent Flesh on the Bone" (CCS 2007) — any `ret`-terminated fragment is a gadget | — | ch03 |
| 2015–20s | Ever-longer chains (leak + write + bypass) | **CFI/hardware**: clang CFI (2014–15), Microsoft CFG (2014), Intel CET (2020), ARM PAC (2018), ARM MTE (Pixel 8, 2023) | ch06's honest ledger |

Read the two trends — the module's thesis. The attacker's unit of work went
from one bug (1988: overflow → shellcode → root) to a chain of four to six
(2024: renderer memory bug + infoleak + sandbox escape + kernel LPE +
persistence); the defender's, from "fix bugs" to "make each bug buy less."
Each exchange buys time and raises the attacker's next price. The 2020s rungs
(PAC, MTE) are the first to attack memory *interpretation* itself — why they
close the module (ch06).

## 6. The terminology contract

Five words, used as if everyone agreed on them. Pin them now:

| Term | Definition | Heartbleed's value |
|---|---|---|
| **Bug** | A code defect: an invariant (size, lifetime, type) not enforced. Exists whether or not anyone notices. | Trusting the heartbeat's own length field |
| **Vulnerability** | A bug *reachable* across a trust boundary with attacker-controlled input. Most bugs are not. | Present in every OpenSSL 1.0.1 server speaking TLS |
| **Primitive** | The memory-safety capability yielded, stated as memory operations. The tradable asset. | OOB read, up to 65,535 B of adjacent heap, repeatable |
| **Exploit (technique)** | The operations converting the primitive into control or disclosure. | Malformed heartbeats in a loop |
| **Payload** | What runs or gets taken *after* the technique wins; under code reuse, built from the process's own functions. | The exfiltrated keys and tickets |

Two confusions cause most misuse. *Bug ≠ vulnerability*: a check missing in
a helper no untrusted input reaches has no attacker — until refactoring
moves it under a parser; reachability is call-graph + boundary, not code.
*Exploit ≠ payload*: the ROP chain is the exploit, `system("/bin/sh")` the
delivery — "exploit in the wild" says a primitive got spent; the payload
says whether you were surveilled or botneted.

## What insiders know

- **gdb disables ASLR by default** (`set disable-randomization on`): every
  exploit that works *under the debugger* and dies in production died because
  production rolled different dice. `setarch $(uname -m) -R ./prog` reproduces
  gdb's frozen layout without gdb — the first sanity check when a chain
  "randomly" fails.
- **The gray market prices chains, not bugs.** Zerodium's 2019 published rates:
  up to $2.5M for a zero-click iOS *full chain* (RCE + escape + persistence),
  while Google's VRP paid ~$250k for comparable Chrome chains — an arbitrage
  that explains where 0-days surface instead.
- **Zero-click is a capability class.** CVE-2019-3568 (WhatsApp VOIP overflow,
  NSO Group, fixed May 2019) crossed the trust boundary on the *ring itself*.
  Anything parsing unsolicited data prices at `UI:N` and is worth an order of
  magnitude more to buyers.
- **CVE prose has a grammar; read the primitive sentence first.** MSRC-era
  descriptions literally say "allows an attacker to write to an arbitrary
  address (write-what-where condition)." The technique in a write-up is
  marketing; the primitive clause is the asset.
- **Fork freezes the dice**: a pre-forking server execs once, so every child
  shares the parent's randomized layout *and* its canary (chapter 02's trap,
  met early). Crash-and-retry against it is exhaustive search, not a lottery;
  `exec` per connection removes the guarantee.

## See it yourself

Stock Ubuntu 22.04 (gcc 11, Python 3.10+, binutils). Build the lab daemon
from section 2 — the four-link chain on 16 lines, modern defaults *off*:

```bash
mkdir chain && cd chain
cat > demo.c <<'EOF'
#include <stdio.h>
#include <string.h>
#include <stdint.h>
struct Rec { char name[8]; void (*fp)(void); };  /* fp sits 8 bytes above name */
void win(void)  { puts("[+] control transferred to win"); }
void lose(void) { puts("[-] no transfer"); }
int main(void) {
    struct Rec r = { "AAAA", lose };
    uint16_t len; char buf[512];
    fread(&len, 2, 1, stdin);       /* trust boundary: attacker's length field */
    fread(buf, 1, len > sizeof buf ? sizeof buf : len, stdin);
    memcpy(r.name, buf, len);       /* BUG: len never checked against 8        */
    r.fp();                         /* PRIMITIVE lands here                    */
    return 0;
}
EOF
gcc -O0 -fno-stack-protector -no-pie -o demo demo.c
# Ubuntu gcc defaults (canaries + PIE) disabled on purpose; ch02 re-enables them.
```

**Step 1 — inventory the surface**:

```bash
readelf -hW demo | grep 'Type:'        # EXEC = no PIE (fixed base 0x400000)
readelf -lW demo | grep GNU_STACK      # flags RW = NX on; RWE = NX off
checksec --file=demo                   # pwntools 4.x: one line per rung
```

Expected fragments (Ubuntu 22.04, gcc 11):

```text
  Type:      EXEC
  GNU_STACK      ... RW ...
  [*] 'demo'
      RELRO:    Partial RELRO
      Stack:    No canary found
      NX:       NX enabled
      PIE:      No PIE (0x400000)
```

`No canary / NX / No PIE / Partial RELRO` is a 2004 posture: stack
non-executable, everything else 1996.

**Step 2 — fire the chain** (one pipe):

```bash
nm demo | grep ' T '                   # where win() landed: 0x4011xx on this gcc
python3 -c "import struct,sys; sys.stdout.buffer.write(
    struct.pack('<H',16) + b'A'*8 + struct.pack('<Q',0x401176))" | ./demo
```

Expected (use *your* `nm` value for `win`):

```text
  0000000000401176 T win
  [+] control transferred to win
```

Mapped to the chain: the header is the trusted field (hop 1); `b'A'*8` walks
`memcpy` to `name`'s edge (hop 2); `win`'s address is the technique — under
NX, existing code, ret2libc in miniature (hop 3); the print is impact.
Chapter 02 upgrades "call win" to "own the frame."

## How to use this knowledge

- **Triage crash reports by primitive, not mood.** A fuzzer crash with the
  instruction pointer at `0x4141414141414141` is not "a DoS": input bytes
  reached control data — the DoS is the same primitive aimed badly. Translate
  the signature into a primitive sentence; severity follows mechanically.
- **Read advisories in the right order.** Find the primitive clause in the CVE
  prose, then the CVSS `AV:`/`UI:` fields — exploitability predicted before
  the vendor's reassurances. Read-only primitives cap at confidentiality;
  writes keep climbing.
- **Threat-model your service as a memory map.** Mark which regions
  untrusted bytes reach — what parses input, and what lives in its
  neighborhood — then decide where parsing *should* live. `checksec` your
  production binaries: the surface you rely on is a rung list.
- **Answer the standard interview questions from derivation**: bug vs
  vulnerability (reachability across a trust boundary); why arbitrary write
  beats NX (it redirects *existing* code pointers; NX only bans injected
  code); why ~70% of severe CVEs are memory-safety (leak + write composes
  across every C/C++ program).

## Connections

- **Module 01 ch03 (virtual memory)** — the `/proc/maps` menu and the
  leak-one-pointer-derive-all-bases arithmetic are sections 3–4 reused
  adversarially.
- **Module 01 ch04 (the stack)** — the frame layout is chapter 02's
  battlefield; our `struct Rec` was a frame in miniature.
- **Module 01 ch05 + ch19 (heap, allocators)** — chunk headers, bins, tcache
  metadata: chapter 04's attack surface.
- **Module 01 ch06 (object layout)** — vptr at offset 0 is the pivot chapter
  05's UAF turns into control; ch08 (C) supplies the unchecked `memcpy`
  family.
- **Module 01 ch10 (Rust) and ch21** — Rust compiles these bug classes into
  type errors; ch21's ladder is what chapters 02–06 expand.
- **Module 02 ch02–ch07** — ch02 takes this chapter's overwrite to the saved
  RIP; ch03 is life after control (ret2libc, ROP, SROP); ch04 is glibc heap
  metadata; ch05 the modern dominant class (reclaim → confuse → control);
  ch06 prices the ladder with bypass histories; ch07 the attacks that corrupt
  no bytes at all.
- **Queued modules** — module 03: the fuzzing/sanitizer pipeline that finds
  these bugs; module 04: gadget hunting is applied chapter 03; modules 05/06:
  where MTE and PAC ship; module 07: per-OS ASLR entropy and allocator
  differences behind ch06's numbers.

## Quiz

1. *(concept)* Map Heartbleed (CVE-2014-0160) onto the terminology contract:
   state its bug, vulnerability, primitive, technique, and payload — and the
   impact ceiling it proves for read-only primitives.
2. *(concept)* "With NX enabled, an arbitrary write is no longer game over."
   Derive why this is false: walk the syllogism from write-what-where to
   arbitrary computation, naming what each step overwrites.
3. *(predict)* In the lab daemon, `nm` reports `win` at `0x401176` and `lose`
   at `0x40119a`. The attacker sends `len = 9` with 8 junk bytes plus the
   single byte `0x76`. What prints, and why did one byte suffice?
4. *(predict)* A pre-forking server execs once at boot; each connection is a
   forked child, and a wrong guess crashes only the child (respawn ~10 ms).
   The attacker must locate libc: 28 bits of mmap entropy (x86-64 Linux). At
   100 guesses/second, what is the worst-case time to *guaranteed* success,
   and what design change makes each guess an independent lottery?
5. *(diagnose)* A user pastes a 300-character string into a desktop app and it
   crashes. QA files "low: DoS, user must misbehave." What two questions must
   you ask of the crash dump, and which answers upgrade the severity?
6. *(diagnose)* A fuzzing campaign returns 12 crashers: (a) 5 with the
   instruction pointer at `0x4141414141414141`; (b) 4 with ASan reporting
   `heap-buffer-overflow WRITE of size 8`; (c) 3 aborting inside glibc's
   `malloc` with `free(): invalid pointer`. Translate each into a primitive
   sentence and rank by what an exploit developer would pay.
7. *(cross-chapter, module 01 ch03)* Page tables give every process a private
   address space and the MMU checks every access. Why does none of that
   machinery notice the attacker rewriting the process's own return address —
   and which two hardware features (chapter 06) first check intra-process
   misuse?
8. *(cross-chapter)* Re-run Morris's 1988 `gets()` stack overflow against a
   2025 Ubuntu binary built with default gcc (canary, PIE, NX, ASLR, full
   RELRO). Name each rung that fires and the module 02 chapter that shows the
   way around it — and what that implies about the worm's 2025 equivalent.

<details>
<summary>Answers</summary>

1. **Bug:** the heartbeat handler trusts the message's own 2-byte length
   (OpenSSL 1.0.1–1.0.1f). **Vulnerability:** on every TLS-speaking server,
   reachable with attacker input. **Primitive:** OOB *read*, up to 65,535
   bytes of adjacent heap, repeatable. **Technique:** malformed heartbeats in
   a loop. **Payload:** the exfiltrated keys. No write primitive means
   nothing can be redirected — impact caps at confidentiality, still
   enormous because secrets lived in the readable neighborhood.
2. **False at step one.** (1) Write-what-where puts 8 attacker-chosen bytes
   *somewhere*: a GOT entry, a function pointer, a vptr (module 01 ch06) —
   bytes at addresses like any others. (2) NX governs *page execute
   permission*; redirecting a call to existing code — `system` with an
   attacker argument — violates no PTE flag: ret2libc (chapter 03). (3)
   Chained through `ret`-terminated fragments (ROP): arbitrary computation.
   The remaining friction, ASLR, falls to an arbitrary read — leak one
   pointer, derive the bases (module 01 ch03). Leak + write ≈ game over.
3. **`[+] control transferred to win`.** The 9-byte copy overwrites only the
   *low* byte of `fp` (`0x9a → 0x76`), leaving bytes 1–7 intact: x86-64 is
   little-endian (module 01 ch07), so the least-significant byte sits at the
   lowest address — the first byte the overflow reaches. It sufficed because
   both functions share the same 256-byte region, hence their upper seven
   address bytes: the *partial overwrite*, a primitive cheaper than it looks.
4. **≈ 31 days worst case; `exec` per connection is the fix.** Fork children
   inherit the parent's layout — the dice rolled once, at exec — so each
   failed guess eliminates a candidate: the exhaustive search finishes within
   2^28 = 268,435,456 guesses ÷ 100/s ≈ 31 days (≈ 15 on average). Re-exec
   per connection and each child rolls fresh: every guess an independent
   1-in-268-million lottery. The same inheritance is why forked children
   share the parent's *canary* — chapter 02's trap.
5. **Question 1: what is at the crash site?** If the faulting instruction
   pointer or a register equals part of the pasted string, input reached
   control data — a hijack primitive with bad aim, not a DoS. **Question 2:
   did input *bytes* land there, or only input *length*?** Crash distance
   proportional to paste length means a linear overwrite of attacker-chosen
   extent. Either answer upgrades severity: bug demo vs weapon demo is aim,
   not access.
6. **(a)** Input became the instruction pointer — a hijack primitive, but
   landing it needs a leak plus an NX-compatible technique (chapter 03).
   **(b)** A controlled 8-byte write adjacent to a heap object — the
   workhorse: with allocator determinism (module 01 ch19) it becomes vptr
   overwrite, length-field corruption, or tcache-adjacent attack (chapters
   04–05). **(c)** Corrupted chunk metadata — powerful (chapter 04's
   House-of-* territory) but needing grooming. Ranking: (b), (c), (a) — under
   ASLR+NX, PC control is worth *less* than a controlled write.
7. **The MMU checks addresses, not intent.** Page tables enforce isolation
   only at privilege boundaries — syscall entry, cross-process access —
   asking "may this privilege level touch this page?" Within one process
   every writable page is equally writable: the return address is 8 bytes on
   a stack page the process already owns, and rewriting it violates no PTE
   flag; RIP is a register the hardware fetches through (module 01 ch07).
   Return-address checking is *semantic*, and semantics live in the program,
   not the MMU. The first intra-process hardware checks: Intel CET's shadow
   stack and ARM MTE (4-bit tags per 16-byte granule) — chapter 06's final
   rungs.
8. **The one-bug worm is dead; its 2025 shape is a chain.** The overwrite
   hits the **canary** first (gcc `-fstack-protector-strong`) — bypassed via
   NUL-byte leaks or non-frame targets (chapter 02). Aiming at code hits
   **PIE + ASLR** — unknown until a leak (chapter 03). Shellcode hits **NX**
   — answered since 1997 by ret2libc/ROP (chapter 03). A GOT overwrite hits
   **full RELRO** — forcing heap/UAF routes (chapters 04–05). Morris's one
   bug owned ~10% of the 1988 Internet; today it must be packaged as leak +
   corruption + bypass: the defense made it insufficient, not impossible.

</details>
