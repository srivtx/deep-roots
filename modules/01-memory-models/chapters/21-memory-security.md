# Chapter 21 — Memory security: how every model fails

> **The 30-second version.** Every memory-model decision you have studied in this
> module is also an attack-surface decision. A stack frame *places a return address
> next to a local buffer*; a heap allocator *stores metadata bytes adjacent to your
> data*; an object layout *puts a vtable pointer at offset 0*; a managed runtime
> protects the language but is itself written in C++. That is why memory-safety bugs
> are roughly 70% of severe CVEs (Microsoft MSRC and Google Chromium both reported
> ~70% in 2019), and why the entire mitigation ladder — canaries, NX, ASLR, PIE,
> RELRO, CFI, MTE — exists: each rung was built *because the rung below it fell*.
> This chapter walks that ladder in order, then goes past it: heap metadata attacks,
> use-after-free, Rowhammer, and speculative side channels. It is the bridge to
> Module 02 (cybersecurity). Attack mechanics are shown for one reason: this is how
> defenders, compiler writers, and allocator authors actually think.

## 1. The stakes: four primitive failures

Strip away the CVE names and the exploit write-ups, and almost every memory-safety
> vulnerability in history is one of four primitive failures:

| Primitive failure | One-line definition | Where this module built its precondition |
|---|---|---|
| **Overflow** (write past the end) | You write more bytes than the destination owns, into whatever sits after it. | The stack buffer sitting under the saved RIP (ch04); the heap chunk with a usable-size limit (ch05, ch19). |
| **Out-of-bounds** (read) | You read past the end and *exfiltrate* what was there. | Pointer arithmetic and array decay with no bounds knowledge (ch08). |
| **Dangling** (lifetime error) | A pointer outlives its target; freed memory is reused. | `free()` returning bytes to a bin (ch05), RAII scope vs heap lifetime (ch09). |
| **Type confusion** (interpretation error) | Bytes are reinterpreted as the wrong type. | Object layout: vtable pointers at offset 0, no runtime tags in C++ (ch06); V8 map pointers at offset 0 (ch15). |

The industry's bug taxonomy says the same thing with numbers. MITRE's perennial
CWE Top 25 is dominated by: **CWE-787** (out-of-bounds write), **CWE-125**
(out-of-bounds read — Heartbleed in 2014 was an OOB *read* that leaked TLS private
keys), **CWE-416** (use-after-free), **CWE-415** (double free). Microsoft's MSRC
(Matt Miller, BlueHat 2019) and Google's Chromium team (2019) each measured roughly
**70% of their severe vulnerabilities as memory-safety bugs**. One decade of that
data is why Chrome, Windows, and Android are rewriting core components in
memory-safe languages — and why this chapter exists.

Two things to keep in frame:

1. **Every primitive has a chapter-number precondition.** Nothing here is magic:
   the attack is always "the layout fact from chapter N, plus a missing check."
2. **Every mitigation below was derived, not invented.** Each one exists because a
   specific, named failure mode defeated what came before it. That derivation
   chain is the chapter's spine.

## 2. The stack: one overwrite, and the ladder it built

Chapter 04 gave you the frame. The classic attack is pure layout knowledge:

```
   main's frame ...                          higher addresses
   +------------------------------------+
   | saved RIP (return address)  8 B    |   <- written by CALL; used by RET
   +------------------------------------+
   | saved RBP                   8 B    |   <- pushed by the prologue
   +------------------------------------+
   | canary                      8 B    |   <- only if -fstack-protector* (gcc)
   +------------------------------------+
   | char buf[64]                       |   <- the attacker's target
   |  "AAAA..."                         |   <- strcpy writes UP, toward saved RIP
   +------------------------------------+   <- RSP points here (lower addresses)
```

The stack grows *down*, but `strcpy` copies *up*: byte 65 of the input lands on the
saved RBP, byte 73 on the saved RIP. When the function returns, `RET` pops the
attacker's bytes into RIP. That is "stack smashing" (Phrack 49, 1996) and it is
still the first thing every security course teaches, because it is pure Chapter 04.

```c
/* VULNERABLE: the shape of every CWE-787 since 1988 */
void greet(const char *name) {
    char buf[64];
    strcpy(buf, name);          /* writes strlen(name)+1 bytes; buf owns 64 */
}

/* FIXED: the destination's size is a parameter of the operation, not a hope */
void greet_fixed(const char *name) {
    char buf[64];
    if (strlen(name) >= sizeof buf) { fprintf(stderr, "too long\n"); return; }
    strcpy(buf, name);          /* now provably in bounds */
}
```

The naive 1996 payload put *shellcode on the stack* and pointed RIP at it. That is
the failure that generated the entire mitigation ladder, one rung at a time:

1. **Stack canaries / SSP** (StackSmash Protection, `gcc -fstack-protector-strong`).
   The prologue copies a secret 8-byte value (from `fs:0x28` on Linux x86-64) into
   the frame above `buf`; the epilogue compares before returning. Mismatch calls
   `__stack_chk_fail` and the process aborts. *Derived from:* the overwrite always
   crosses the canary on its way to RIP. *Bypasses:* leak the canary first
   (an OOB *read* — the failures compose!), or corrupt locals/pointers *below* the
   canary without touching it (overwrite a function pointer argument), or skip the
   epilogue entirely (longjmp/exception paths).
2. **NX / DEP** (no-execute / data-execution-prevention). Mark stack and heap
   `rw-`, not `rwx`. OpenBSD shipped W^X in 2003; Windows XP SP2 shipped DEP in
   2004. *Derived from:* shellcode on the stack is now data, so it faults. *Bypass:*
   **ret2libc** — don't inject code, *call existing code*: point RIP at `system()`
   with a crafted argument.
3. **ASLR** (address-space layout randomization). Chapter 03 built the virtual
   memory map; ASLR randomizes the *base addresses* of the stack, heap, and shared
   libraries per run. *Derived from:* ret2libc needs libc's address. *Bypass:* an
   info leak that reveals one pointer (then offsets are known — Chapter 03's layout
   is deterministic once you have one anchor), or brute-force when entropy is small
   (32-bit systems: ~19–24 bits of library entropy, brute-forceable; 64-bit: 47-bit
   VA space, not brute-forceable).
4. **PIE** (position-independent executable). ASLR originally randomized
   libraries, stack, and heap — but not the main binary. PIE makes the executable
   itself a loadable at a random base (default on Ubuntu since 17.10). *Bypass:* a
   code-pointer leak defeats it, same as above.
5. **RELRO** (relocation read-only). The GOT (Chapter 08's PLT/GOT dispatch) was
   writable so lazy binding could fill it in — which made "overwrite a GOT entry,
   redirect a library call" the laziest arbitrary-write target in history.
   **Partial RELRO** keeps `.got.plt` writable; **full RELRO** (`-Wl,-z,relro,-z,now`)
   resolves every symbol at startup and mprotects the whole GOT read-only. *Derived
   from:* the GOT overwrite. Modern folklore is blunt: *GOT overwrite is dead, use
   full RELRO and stop worrying about it.*

The pattern to internalize: **mitigation n is always a response to the bypass of
mitigation n−1.** And bypasses of mitigation n compose the *earlier* primitives
(canary leak = OOB read; arbitrary write = overflow + no RELRO). Attackers do not
break the ladder; they climb around it.

## 3. ROP: when code injection died, attackers began quoting the program

NX + ASLR + PIE left one thing intact: the attacker still controls the saved RIP
and everything on the stack *after* it. The answer — **ROP**, return-oriented
programming — is to never inject code at all, only *reuse* fragments already in
memory. A **gadget** is any short instruction sequence ending in `ret`. Because
`ret` pops the next stack slot into RIP, a chain of gadgets is just a chain of
addresses on the stack — the CPU walks it for you:

```
   ROP chain (conceptual): call system("/bin/sh") with gadgets, not injected code

   new saved RIP slot ->  +--------------------------------------+
   (after overflow)       | addr of gadget:  pop rdi ; ret      |   <- gadget 1
                          +--------------------------------------+
                          | addr of "/bin/sh" string in libc    |   <- popped into RDI
                          +--------------------------------------+
                          | addr of gadget:  pop rsi ; ret      |   <- gadget 2
                          +--------------------------------------+
                          | 0x0                                  |   <- RSI = 0
                          +--------------------------------------+
                          | addr of system@libc                  |   <- gadget 3 is a
                          +--------------------------------------+      whole function
```

Why x86 is gadget-rich and RISC is not: x86 instructions are **variable-length**
(1–15 bytes), so a gadget can start at *any unaligned byte* — the same code bytes
decode into different instructions when entered mid-stream. An ARM64 instruction
is a fixed 4 bytes, so gadgets exist only at instruction boundaries and the
population is far sparser (ARM64 compensates with its dense `ret`/`blr` patterns
and x30 register, and with gadgets that use `ldp`/`ldr` to load multiple registers
before `ret`).

One more concept: the **stack pivot**. If your overflow is in a *heap* buffer, the
ROP chain must live there — so the first gadget doesn't pop an argument, it moves
RSP itself (`pop rsp ; ret`, `leave ; ret`, `xchg eax, esp`). After the pivot, the
CPU is executing your heap data as a ROP chain.

The mitigation response, again derived: if ROP dispatches through the stack, then

- **CFI** (control-flow integrity, `clang -fsanitize=cfi`; Linux kernel CFI)
  pre-computes the set of *legal* indirect targets and inserts checks so an
  overwritten vtable or function pointer can only land somewhere type-correct.
- **Shadow stacks** (Intel CET, shipping since Tiger Lake in 2020; also
  `-fcf-protection`) keep a second, tamper-proof copy of every return address;
  `ret` compares the two and traps on mismatch — a poisoned stack alone no longer
  redirects control flow.
- **PAC** (ARM pointer authentication, ARMv8.3, Apple A12 in 2018) *signs* return
  addresses with a key, so a forged address fails its signature check at `ret`.

And the honest framing the field actually uses: this is an **arms race, not a
solved problem**. Shadow stacks pushed attackers from ROP toward JOP/COP
(jump/call-oriented programming, which dispatch through function pointers instead
of the stack), and gadget sets shrink but never reach zero. Every rung raises
cost; none closes the category. The endgame that actually changes the economics is
the one from Part II of this module: stop shipping memory-unsafe C/C++ in the
first place.

## 4. The heap: allocator metadata is the attack surface

Chapter 19 gave you glibc's chunk anatomy. Look at it as an attacker now:

```
   in-use chunk, 64-bit glibc (2.35):  user pointer points to +0x10
   +0x00  prev_size   8 B   | meaningful only if PREV_INUSE of THIS chunk is clear
   +0x08  size        8 B   | size | flags: bit0 PREV_INUSE, bit1 IS_MMAPPED, bit2 NON_MAIN_ARENA
   +0x10  user data ...     | malloc() returns this address; free() writes a tcache
   -0x10  ...               |   `next` pointer and `key` INTO these bytes on free
```

The design truth that follows: **`free()` stores freelist metadata *inside the
chunk you just gave back*.** Metadata adjacent to (or inside) attacker-writable
bytes is not a glibc bug — it is the inherent trade of in-band metadata, and it is
the attack surface of nearly every classic heap exploit.

**Unsafe unlink (historical).** When adjacent free chunks consolidate,
`malloc`/`free` unlinks a chunk from its doubly linked bin, which historically
performed two writes through attacker-influenced pointers
(`P->fd->bk = P->bk; P->bk->fd = P->fd`) — a free "write-what-where." glibc
(~2.3.4, 2004) added **safe unlinking**, two sanity checks that must both hold
before any write happens:

```c
if (P->fd->bk != P || P->bk->fd != P)   /* the two safe-unlink checks:
    malloc_printerr("corrupted double-linked list");  /* fd and bk must point
    /* back at P. Faking a chunk means faking BOTH links consistently. */
```

Forged `fd`/`bk` now have to cross-reference each other back to P — which
neutralized the naive version and moved attackers to the fast paths.

**Fastbin/tcache dup and poisoning.** tcache (glibc 2.26+) is a per-thread cache
of singly linked free chunks per size (64 bins, up to 7 chunks each, 2.31–2.35
defaults). A double free can insert the *same chunk twice*, and a corrupted `next`
pointer makes the *next-next* `malloc` return an arbitrary address:

```
   tcache bin for size 0x40, glibc 2.31 (before Safe-Linking):

   entries[size 0x40] --> chunk A --> A AGAIN --> chunk B      (A freed twice)

   then, with A at the head, a UAF/overflow writes A->next = TARGET-0x10:

   entries[size 0x40] --> chunk A --> [fake "chunk" at TARGET-0x10]
                                        malloc #2 returns TARGET
                                        (classic target: __free_hook, pre-2.34,
                                        or a stack canary / vtable object)
```

```c
/* VULNERABLE: the tcache dup of every CTF, and of real CVEs */
char *p = malloc(0x38);
free(p);                 /* p -> tcache[0x40] */
/* ... an overflow or UAF overwrite of p's key field goes here (see below) */
free(p);                 /* p -> tcache[0x40] AGAIN: the list now has p twice */

/* FIXED: nulling after free is the C idiom; unique_ptr is the C++ one */
free(p); p = NULL;       /* a NULL double-free is an instant, safe crash */
```

glibc 2.29 (2019) added a `key` field check to catch the direct double free
(`free(): double free detected in tcache 2`) — and since the key lives *inside the
freed chunk's user bytes*, the era's standard bypass became: use your overflow/UAF
to overwrite the key, then free again. That is the whole lesson of this section in
one anecdote: **the check and the bytes it guards are neighbors, so the defense is
itself attack surface.** glibc 2.32 (2020) added **Safe-Linking**: the stored
`next` pointer is XOR-encoded as `stored = next ^ (slot_address >> 12)`. Forging a
`next` pointer now requires knowing *where your chunk is* (a heap leak) — an
arbitrary constant in that field decodes to garbage, and an unaligned result trips
`malloc(): unaligned tcache chunk detected`.

Folklore one-liners (the "House of" family — you will meet them properly in
Module 02): **House of Force** overwrote the top chunk's size to make a later
`malloc` return an arbitrary address (dead since glibc 2.29's top-chunk size
check); **House of Spirit** frees a *fake* chunk planted on the stack or in
globals so a later `malloc` hands the attacker that address; **House of Einherjar**
uses a single NUL byte to force consolidation *backwards* into a fake chunk.

That NUL byte deserves its own paragraph, because it is the purest expression of
"one byte is enough":

```c
/* VULNERABLE: the "poison null byte" (off-by-one) — writes exactly one NUL too many */
size_t req = malloc_usable_size(chunk);   /* attacker-aligned to chunk size 0x110 */
memcpy(chunk, src, req);                  /* fills 0x108 user bytes exactly      */
chunk[req] = '\0';                        /* the NUL lands on the NEXT chunk's size LSB */
/* 0x111 (size 0x110 | PREV_INUSE) becomes 0x100: PREV_INUSE cleared + size shrunk.
   The next free() now consolidates with a "previous" chunk the attacker prepared. */
```

Clearing PREV_INUSE makes `free()` trust the *previous* chunk's (forged)
`prev_size` and consolidate — producing overlapping chunks, the raw material of
most heap exploits. The folklore rule it earned: **no NUL-byte write near chunk
metadata is safe, ever.** The fix is the same as section 2: the size of the write
must be derived from the destination, not from the data.

## 5. Use-after-free: the modern bug class — and the managed-language mirage

**CWE-416 use-after-free** is the top of the modern bug lists, and it is a pure
lifetime error: an object dies (freed to the allocator), a *stale reference*
survives, and the slot gets reallocated — often with attacker-controlled content.
In browsers the pattern is boringly consistent: a DOM object is freed when a tab
or document tears down, an event fires later and a handler still holds the pointer.

```cpp
// VULNERABLE: the shape of a browser UAF, in five lines
Node* cache = document->root();      // engine caches a raw pointer
document->close();                   // frees the Node (Ch09: lifetime ended)
// ... attacker code runs, allocator reuses those bytes for a string/typed array ...
cache->ref();                        // executes bytes the attacker chose

// FIXED: lifetime tied to ownership, not to a raw pointer (Ch09/Ch10)
std::unique_ptr<Node> cache = document->takeRoot();   // ownership, not access
// or: std::shared_ptr<Node> cache;  cache = document->root();  // refcount keeps it alive
```

Why does a UAF so often become *code execution* rather than a mere crash? Because
of what Chapter 06 put at offset 0: **the vtable pointer.** The attack composes
three of the four primitives:

1. **Heap spray** (an overflow-free cousin): allocate megabytes of attacker data
   in a predictable address range, so the *next* reallocation reuses the freed
   slot and the surrounding pages are yours.
2. The stale call goes through the object's vtable pointer — which now points
   into your sprayed data at a fake vtable (Chapter 06's layout, used against you).
3. **Type confusion** finishes it: a stale pointer to type A reinterpreted as
   type B (V8's equivalent: a stale object whose *map pointer*, Chapter 15, has
   been swapped to a different hidden class — one map says "I am a float array",
   the other says "I am an object array"). In engines, the canonical primitive
   pair is exactly that: an array-of-floats/array-of-objects confusion yields
   `addrof` (leak an object's address) and `fakeobj` (craft an object at a chosen
   address) — from there, "arbitrary read/write" is a few lines.

Now the uncomfortable part for Part II of this module. **Chapters 12–15 eliminated
memory corruption *in the language* — and it did not eliminate it from the
system.** JavaScript, Java, and C# programs are memory-safe; the *engine* under
them is a multi-million-line C++ program with raw pointers, custom allocators,
JITs, and FFI. The GC manages the language's objects; it does not manage the
engine's internal pointers, DOM bindings, or JIT metadata. The failure modes are
engine-shaped: **JIT code spraying** (compile attacker-influenced code into
RWX JIT pages and jump into the middle of it — JIT spray), **typed-array
confusion** (a `Float64Array` whose backing store pointer got corrupted gives
arbitrary R/W from script), and FFI/native extensions that punch straight back
into C. Java's `sun.misc.Unsafe` and .NET's `unsafe` blocks are the same hole,
self-inflicted.

The lesson, stated precisely: **memory safety is a property of the whole stack —
language + engine + libraries + kernel — not a checkbox a language grants.** That
is why the industry's actual migration target is Rust (Chapter 10): the only
mainstream systems language whose *compile-time* model (ownership + borrow
checker) makes the four primitive failures type errors instead of runtime
behavior. Be equally precise about its limits: `unsafe` blocks, FFI boundaries,
and raw-pointer code inside Rust itself (a large share of historical Rust CVEs
live in `std`'s unsafe internals) remain the escape hatches — the fence is real,
the gates exist.

## 6. The defense stack: sanitizers, hardware tags, and physics

### Sanitizers: finding it before the attacker does

If the attack surface is "writes near the ends of objects," then make the ends
*loud*. **AddressSanitizer (ASan**, `gcc`/`clang -fsanitize=address`) rings
every allocation — stack, heap, and global — with **redzones**: poisoned bytes
that trap on any access. Picture Chapter 19's chunk layout with the user data
unchanged but fenced: a left redzone before the header, a right redzone after
the usable size. It catches section 2's overflow and section 4's poison
null byte at the *first byte*, not at the crash three functions later.

The machinery is elegant and worth deriving: you cannot check every byte
cheaply, so ASan keeps **shadow memory** — a second mapping where **every 8
application bytes compress to 1 shadow byte**:

```
   app memory:      0x602000000010  0x...18  0x...20   (8-byte granules)
                        |              |         |
   shadow addr:     (addr >> 3) + 0x7fff8000      (Linux x86-64, clang)
                        |              |         |
   shadow byte:        0x00           0x03      0xfa
                       all 8 bytes    first 3   heap left redzone (0xfa);
                       addressable    valid     0xfb = right redzone,
                                                0xfd = freed (quarantined)
```

Every instrumented load/store computes one shift, one add, one byte compare —
cheap enough for CI, too slow (and too memory-hungry: **1/8 of RAM for the shadow,
plus redzones, roughly 2x–3x slowdown**) for production. The **UAF quarantine**
completes the trick: freed chunks are held (default ~256 MiB, per the ASan
allocator) instead of being reused, so a stale dereference hits the `0xfd` poison
instead of silently reading a reallocated object — that is how ASan turns
CWE-416, the bug class that *usually works fine in testing*, into a deterministic
report.

Its siblings, one line each: **MSan** shadow-tracks *initializedness* and traps
on reads of uninitialized bytes (CWE-457); **UBSan** traps undefined behavior
itself — signed overflow, misaligned access, bad vtable dispatch — at the
operation that causes it. And the delivery mechanism is **fuzzing**: AFL++/libFuzzer
mutating inputs in a loop, ASan/UBSan instrumented, is the industry default
(OSS-Fuzz runs exactly that stack against hundreds of projects). The old world
found these bugs with valgrind in production, at 10–50x slowdown; the current
world finds them with **ASan in CI** — same detection, hours earlier, before the
release train instead of after the incident.

### Hardware: MTE and PAC

Software checks per access are slow. **ARM MTE** (memory tagging extension)
moves the check into the CPU: memory is tagged in **4-bit tags per 16-byte
granule**, pointers carry a matching tag in their top byte (which ARM64 already
ignored — TBI), and the hardware compares pointer tag vs. memory tag on every
access. Tag mismatch = trap. UAF and OOB become detectable *at the instruction
that commits them*, with near-zero software cost. Adoption: the **Pixel 8 (2023)
was the first consumer MTE hardware**; Android enables it per-process. Why is
detection **~90%, not 100%**? Four bits = 16 possible tags, so a freed-and-reused
object has a **1/16 (~6%) chance of colliding with the stale pointer's tag** —
a mismatched access that slips through. MTE raises the cost of every exploit and
catches the sloppy majority; it does not close the category.

**PAC** gets one line here (it appeared in section 3): signed return addresses
make stack-ROP fail at `ret` unless the attacker also forges signatures.

### Physics: Rowhammer and the speculative side channel

The bottom of the stack bites back. **Rowhammer** (Kim et al., 2015) is DRAM
physics as attack: a DRAM cell is a capacitor on an accessed row (Chapter 01);
activating one row *thousands of times per second* leaks charge into adjacent
rows and flips their bits — corruption with **no software bug at all**. The 2015
result flipped bits from an unprivileged userspace process; later work (VU
Amsterdam's **TRRespass**, 2020; **Blacksmith**, 2022) showed the industry's
defense — **TRR**, targeted row refresh, which refreshes neighbors of
hammered rows — could itself be evaded with many-sided, non-uniform hammer
patterns. Mitigations, all partial: TRR, more frequent refresh (each refresh
pass costs measurable power — the phone's battery pays), and **ECC as a partial
fix** (ECC turns silent flips into correctable-or-detected events, but ECC-bypass
results exist). Rowhammer is the honest endpoint of this module: the memory
*model* was never the only surface — the memory *physics* is one too.

One paragraph for the class that redefined the field. **Spectre/Meltdown (2018)**
are "memory as a side channel": speculative execution runs a branch the CPU
should not have taken, transiently loads a secret byte, and uses it to touch one
of two cache lines; Chapter 02's hierarchy then betrays the choice through timing
(a cache hit is ~1 ns, a miss ~100 ns — the fastest yes/no oracle in computing).
The architectural state is rolled back, the cache state is not. The consequence
for the definition of "memory safety": a program can be **fully correct, fully
memory-safe, and still leak its secrets** — safety of *writes* (integrity) was
never enough; the industry now also has to defend *reads and timing*.
Confidentiality joined the threat model, and that is Chapter 02's material
becoming security physics.

## What insiders know

- **ASan's real cost** is 1/8 of RAM for the shadow (plus redzones per allocation,
  minimum 16 B each, and a default ~256 MiB UAF quarantine), ~2–3x runtime, and —
  the part that bites huge C++ binaries — startup work: `__asan_register_globals`
  registers every global's redzone before `main`, which can add seconds to
  processes with hundreds of thousands of globals.
- **glibc 2.29–2.35 tcache double-free check and its standard bypass**: the `key`
  field in the freed chunk's user bytes; overwriting it (via the same overflow
  that caused the bug) makes the second `free` pass. The moral insiders repeat:
  metadata adjacent to user data is attack surface, even when the metadata *is*
  the defense.
- **"GOT overwrite is dead"** — under full RELRO (`-Wl,-z,relro,-z,now`) the GOT
  is resolved at startup and read-only; the modern write targets are function
  pointers in `.data`/heap objects, `__free_hook`/`__malloc_hook` (removed in
  glibc 2.34), and vtables.
- **The canary's first byte is 0x00 on purpose**: `strcpy`-family overwrites copy
  until *their* terminating NUL, so an all-bytes canary would be copied by a
  chained strcpy and leaked back; a 0x00 low byte stops the copy mid-canary.
- **checksec output reading** (pwntools or checksec.sh): four fields that summarize
  this chapter's ladder — `Canary` (`Yes` = SSP), `NX` (`Yes` = DEP), `PIE`
  (`Yes` = randomized binary), `RELRO` (`Full` beats `Partial`: writable GOT is
  the difference). A binary reading `No Canary / NX Yes / No PIE / Partial` is a
  2004 binary wearing 2023 libraries.
- **"No NUL-byte write into chunk metadata is safe"** — the poison-null-byte
  family: clearing PREV_INUSE (bit 0 of `size`) is one bit of corruption and has
  spawned three decades of consolidation exploits; safe-unlink and Safe-Linking
  constrain but do not repeal the rule.
- **Safe-Linking arithmetic** (glibc 2.32+): `stored = next ^ (slot >> 12)`;
  decoding needs the *slot's own address*, so tcache poisoning without a heap
  leak lands on `malloc(): unaligned tcache chunk detected` rather than your
  target.

## See it yourself

The lab is defensive: watch each mitigation catch the same 200-byte overflow.

```bash
cat > vuln.c <<'EOF'
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
    char buf[64];
    if (argc > 1) strcpy(buf, argv[1]);   /* CWE-787: writes up to strlen+1 bytes */
    printf("ok: %s\n", buf);
    return 0;
}
EOF

# 1. No protection: watch the hijack itself (gcc 12+/clang, x86-64 Linux)
gcc -g -O0 -fno-stack-protector -no-pie -o vuln_raw vuln.c
./vuln_raw "$(python3 -c "print('A'*200)")"
#   Segmentation fault (core dumped)      <- RIP = 0x4141414141414141

# 2. Canary: the abort named in the chapter
gcc -g -O0 -fstack-protector-strong -o vuln_ssp vuln.c
./vuln_ssp "$(python3 -c "print('A'*200)")"
#   *** stack smashing detected ***: terminated   (SIGABRT from __stack_chk_fail)
#   The write itself still happened; the exit check is what caught it.

# 3. ASan: the first-byte report, with the redzone decoded
gcc -g -O0 -fsanitize=address -o vuln_asan vuln.c
./vuln_asan "$(python3 -c "print('A'*200)")"
#   ERROR: AddressSanitizer: stack-buffer-overflow on address ...
#   WRITE of size 201 at ... thread T0
#   Address ... is located in stack of thread T0 at offset 64 in frame
#     #0 ... main ...  'buf' (64 bytes)
#   [32, 96) 'buf'  ... [96, 144) '<array frame 48 bytes>' ...  <- offsets into the
#   frame; the shadow says 0x00 for [32,96) and 0xfa/0xfb redzone right after.

# 4. checksec: read the ladder of the binary, not the source
#   (pip install pwntools; or any checksec.sh)
checksec --file=vuln_ssp
#   RELRO:  Partial  | Canary:  Yes  | NX:  Yes  | PIE:  No ...
#   (Partial because the default link used lazy binding; add -Wl,-z,now for Full.)

# 5. gdb: the $rsp/$rip relationship that explains the whole chapter
gdb -q ./vuln_raw
(gdb) run AAAA...                  (paste 200 A's, or: run $(python3 -c "print('A'*200)"))
(gdb) info registers rip rsp rbp
#   rip  0x4141414141414141  <- ret popped the attacker's bytes
#   rsp  0x7ffc...           <- points INTO the stack right below the dead RIP:
#   (gdb) x/4gx $rsp         <-  this is where a ROP chain would be sitting
```

What to notice: (2) proves the canary is an *exit* check — the corruption is
already committed; (3) proves ASan reports at the *first* bad byte, with the
shadow byte's meaning in the report; (5) shows control flow and the future ROP
chain living at `$rsp` — one `RET` away from each other. Optional MTE note: ARM
MTE can be exercised without Pixel hardware via `qemu-aarch64` (QEMU 7.2+ with
`-machine mte=on` under `qemu-system-aarch64`, or user-mode with tag support) and
a `clang -fsanitize=memtag` build — nice to see, not required.

## How to use this knowledge

- **Debugging any C/C++ crash**: run the failing input under
  `-fsanitize=address,undefined` before reading a core dump — an offset-into-
  redzone report replaces an hour of staring at a corrupted frame (Chapter 04's
  gdb skills still read the frame; ASan tells you *which write* broke it).
- **Code review reflexes**: any `strcpy`/`memcpy`/`sprintf` whose length comes
  from the *data* instead of the *destination* is section 2 waiting to happen;
  any `free` followed by reachable pointer use is section 5; any raw pointer
  cached across a callback/event is a browser-style UAF.
- **Hardening your own builds**: `-fstack-protector-strong -D_FORTIFY_SOURCE=2
  -Wl,-z,relro,-z,now -pie` is the four-rung ladder in flags; ASan+UBSan in CI
  plus a fuzzer on your parsers is the delivery mechanism. checksec the result.
- **Reading CVEs like an insider**: "heap-based buffer overflow in the
  allocator" now decomposes into a primitive (overflow), a target (chunk
  metadata), and a technique (poisoned freelist → arbitrary write); "UAF in the
  DOM" decomposes into lifetime error + reallocation + type confusion.
- **Interview answers that land**: why NX didn't end exploitation (ROP quotes
  existing code); why ASLR didn't (info leaks compose with the OOB-read
  primitive); why 70% of CVEs are memory-safety bugs (the four primitives are
  layout facts, not coding slips); why the field's long-term answer is language
  migration, not another rung on the ladder.

## Connections

- **Ch01** — the DRAM capacitor cell is the precondition for Rowhammer: physics
  below every abstraction.
- **Ch02** — the cache hierarchy and its hit/miss timing delta is the oracle
  every speculative side channel speaks.
- **Ch03** — virtual memory's fixed layout is what ASLR randomizes; one leaked
  pointer re-derives the rest deterministically.
- **Ch04** — frames, prologues, and the saved RIP: the entire stack-smashing
  story is one chapter-04 diagram plus a missing length check.
- **Ch05 / Ch19** — free lists, bins, chunk headers, tcache: the heap attack
  surface, defined.
- **Ch06** — vtable-at-offset-0 is why UAF + heap spray becomes RIP control;
  padding/tag bits become confusion primitives.
- **Ch08 / Ch09** — the explicit and RAII models are where all four primitive
  failures live unchecked; `unique_ptr`/`shared_ptr` are the lifetime fixes.
- **Ch10** — Rust's borrow checker compiles sections 2–5's bug classes into type
  errors; its `unsafe`/FFI gates are the honest asterisk.
- **Ch12–Ch15** — the managed runtimes: safe languages on unsafe engines, and
  the V8 map/hidden-class machinery behind engine type confusion.
- **Ch22** — mobile memory in practice: MTE on Pixel 8, PAC on Apple silicon,
  process-level hardening as the deployment story.
- **Module 02 (cybersecurity & exploitation, queued)** — this chapter is its
  prerequisite: every primitive here becomes a lab there.
- **Module 03 (software bugs, queued)** — the sanitizer/fuzzing pipeline shown
  here is that module's process-layer case study; **Module 04
  (decompilation, queued)** — gadget hunting and reading stripped binaries is
  applied ROP.

## Quiz

1. *(concept)* The x86-64 Linux canary is 8 bytes, and its **low byte is 0x00 by
   design**. Explain the specific overwrite primitive this defeats, and name the
   remaining bypass that leaks the other 7 bytes anyway.
2. *(concept)* ARM MTE detects UAF and OOB in hardware at ~90% effectiveness,
   not 100%. Derive the number: where does the residual failure come from, and
   why is it probabilistic rather than a design flaw?
3. *(predict)* A binary has: canary `Yes`, NX `Yes`, ASLR+PIE `Yes`, **RELRO
   `Partial`**. An attacker has one heap overflow that grants an arbitrary 8-byte
   write and a separate OOB *read*. Walk the exploit: which mitigation stops
   which step, and what is the cheapest path to code execution that this
   configuration leaves open?
4. *(predict)* glibc 2.35, tcache: `p = malloc(0x38); free(p); free(p);` — what
   exact error aborts the process, which field triggered it, and what does the
   attacker need to change *between* the two frees for the second one to succeed?
5. *(diagnose)* A reviewer finds: `Node* n = new Node; auto a = std::shared_ptr<Node>(n);
   auto b = std::shared_ptr<Node>(n);` — the object is later destroyed twice, and
   the second destructor runs on reallocated memory. What is the bug class (CWE
   number), why doesn't `shared_ptr`'s refcount save it, and what is the fix?
6. *(diagnose)* A browser crashes only under a specific sequence: close a tab
   (DOM node freed), run a script that allocates thousands of typed arrays, then
   fire a pending event whose handler touches a cached node pointer. Under ASan,
   the report says the access hit `0xfd` shadow. Name the bug, name the technique
   the script is performing, and say what the handler's virtual call would have
   dispatched through if ASan had not intervened.
7. *(cross-chapter)* Using Chapter 06's object layout: a UAF'ed C++ object sits
   at address 0x5555_5555_9000 and the attacker has sprayed the heap with
   controlled data. Which field of the dead object is the pivot, at which offset,
   and what exactly must the attacker place in the sprayed data so that
   `obj->vfunc()` transfers control?
8. *(predict, cross-chapter)* glibc 2.32+: the head tcache chunk's `next` field
   is overwritten with the constant 0x4141414141414141 (the attacker knows no
   addresses). Two `malloc`s of that size happen. What pointer does the allocator
   actually compute on the second one, and what does glibc most likely print?

<details>
<summary>Answers</summary>

1. **The 0x00 low byte breaks strcpy-chain canary leaks.** `strcpy` copies until
   *its* NUL; with a fully random canary, a string overwrite copies the canary
   into an attacker-visible buffer, and re-overflowing with the leaked value
   restores it — check passes, RIP still hijacked. A leading 0x00 makes the
   *canary itself* the terminating NUL, so the copy stops at byte 0 and never
   reveals bytes 1–7. The surviving bypass: a format-string or OOB-read bug
   elsewhere that prints raw stack memory (the primitive failures compose — an
   OOB read defeats a write defense), or overwriting frame locals *below* the
   canary (e.g., a function pointer variable) and never touching it, or avoiding
   the checked epilogue via longjmp.
2. **Tag collision, 4 bits wide.** MTE assigns a 4-bit tag per 16-byte granule —
   16 possible values. On free, the runtime re-randomizes the memory tag; a stale
   pointer retains the old tag. The stale access is caught iff the new tag
   differs — a **1/16 (~6%) chance the tags collide** and the access slips
   through as a false negative. It is probabilistic because tags are hints, not
   identities: with 16 tags there is no way to make every live object globally
   unique. ~90%+ detection is the aggregate over randomized reuse, hence MTE is
   a cost-raiser, not a category-killer.
3. **Nothing stops the write; the write is redirected.** The canary does *not*
   fire — this is a *heap* overflow, not a stack frame overwrite. NX stops
   "jump to injected shellcode." ASLR+PIE stop *guessing* code addresses — but
   the OOB read leaks one pointer, which re-derives libc's base deterministically
   (Ch03: fixed offsets from any anchor). The cheapest open path: **Partial
   RELRO leaves `.got.plt` writable**, so the arbitrary 8-byte write overwrites a
   GOT entry (say, `free@got` or `exit@got`) with `system@libc` (address derived
   from the leak). The next call through that PLT slot becomes
   `system(attacker_string)`. Full RELRO (`-z now`) would close exactly this
   step; the next-cheapest paths are `__free_hook` (gone in 2.34) or a vtable in
   a heap object.
4. **`free(): double free detected in tcache 2`.** Trigger: glibc 2.29+ stores a
   per-thread `key` pointer in the freed chunk's user area; on the second free,
   `e->key == tcache` (and the chunk is found in the bin) aborts. Between the
   frees the attacker must **overwrite that key field** (via the original
   overflow/UAF — the bytes are user-addressable, which is the point of the
   moral) so the check no longer recognizes the chunk as tcache-resident. The
   chunk then enters the bin twice → dup → poisoning candidate.
5. **CWE-425-adjacent misuse producing CWE-415 (double free) / CWE-416 (UAF).**
   Two independent `shared_ptr` control blocks for one object = refcount 1 + 1,
   not 2: each control block believes it owns the object exclusively, so both
   destructors call `delete n` — the second delete is a **double free**, and the
   memory reallocated in between (the "destroyed on reallocated memory" symptom)
   makes it a UAF corruption too. `shared_ptr`'s refcount cannot save it because
   there is no shared control block — the count was never shared. Fix: construct
   once and copy — `auto a = std::make_shared<Node>(); auto b = a;` — or hand out
   `weak_ptr` for non-owning observers.
6. **UAF (CWE-416) + heap spray.** The freed DOM node's slot is reallocated by
   the typed-array allocations (the spray's job is to make the *reused content*
   attacker-chosen and the neighborhood predictable). ASan's `0xfd` shadow byte
   means the freed region was in the **quarantine** — held, poisoned, not yet
   reused — which is why the crash is deterministic and diagnosable. Without
   ASan, the reallocation succeeds and the handler's virtual call would dispatch
   through the **vtable pointer at offset 0** of the dead object — now pointing
   into sprayed data, i.e., a fake vtable (Ch06) under attacker control.
7. **The vptr, offset 0, pivots to the sprayed fake vtable.** Ch06: the first 8
   bytes of a polymorphic C++ object are the vtable pointer. The attacker needs:
   (a) at a chosen spray address `T`, a fake vtable — an array of function
   pointers where the *index of the called virtual function* (fixed by the class
   layout, computed at compile time) holds the desired target (e.g., a stack-
   pivot gadget); and (b) a way to write `T` into the dead object's offset 0 —
   which the heap spray/reallocation itself provides (the freed object's bytes
   are re-filled by attacker data). Then `obj->vfunc()` loads vptr = T, loads
   `T[2]` (say), and calls it: control transferred without touching the stack.
8. **A garbage address, then an abort.** Safe-Linking (glibc 2.32+) stores
   `stored = next ^ (slot >> 12)`. On `malloc` #1 the corrupted head is popped;
   on #2 the allocator decodes the attacker's constant as
   `next = 0x4141414141414141 ^ (slot >> 12)` — the XOR of the constant with the
   *unknown* high bits of the chunk's own address. Without knowing where the
   chunk lives, the result is an address the attacker did not choose; if it is
   not 16-byte-aligned, glibc prints **`malloc(): unaligned tcache chunk
   detected`** (the check shipped with Safe-Linking) and aborts. If it happens to
   be aligned, `malloc` returns a wild pointer that crashes on use. Either way:
   the constant alone is not a poisoning primitive — this check plus encoding is
   what moved the price of tcache poisoning up to "requires a heap leak first."

</details>
