# Chapter 02 — Linking & loading

> **The 30-second version.** `main` is not where a program begins. Between
> `exec()` and `main`, the kernel hands control to a *userspace* program —
> `ld.so` — which maps the segments page-aligned, zeroes BSS, repairs every
> address ASLR invalidated (relocations), resolves imports through the GOT/PLT,
> runs initializers, then jumps to your entry point. Lazy binding, the
> resolution order, `LD_PRELOAD` interposition, and `dlopen` namespaces make
> shared libraries possible — and an attack surface.

## exec(): the handoff to a userspace loader

Program headers are the *loader's* view of the ELF file (Chapter 01's
two-tables insight). After `execve` verifies the `\x7fELF` magic, the kernel
maps exactly one thing — **`PT_INTERP`**, the string `/lib64/ld-linux-x86-64.so.2`
(28 bytes at file offset 0x394 in our demo) — and gives it control. The loader
has no interpreter, so the recursion ends: one kernel-mapped program
maps everything else, guided by the auxiliary vector (`AT_PHDR`, `AT_ENTRY`,
`AT_RANDOM`). Relocation and symbol resolution are policy, not mechanism —
they live in userspace, where they can be traced. Reference: `ld.so` 2.41
(glibc 2.41, Debian 13, x86-64).

The mapping itself is pure Module 01 Chapter 03: one `mmap` per `PT_LOAD` (the
demo has four: `R`, `R E`, `R`, `RW`). It only works because the linker keeps
the congruence rule **`p_offset ≡ p_vaddr (mod 4096)`**: file page N must land
on virtual page N, or one page of address space would need bytes from two file
pages, and one-mmap-per-segment collapses.

One detail is the BSS. The `RW` load carries `filesz 0x260, memsz 0x268`: the
last 8 bytes (`.bss`, `NOBITS`, at 0x4030) do not exist in the file. The loader
maps the page anyway and the kernel's zero page fills the tail — BSS zeroing
costs no I/O; the first *write* is what faults in a real page.

Then `e_entry` (0x1080) — *not* `main` but `_start`, which calls
`__libc_start_main` (an eager `GLOB_DAT` import), which runs
`.init_array` constructors, calls `main`, and on return runs `.fini_array` and
`exit`. Everything here happens inside that chain.

## Relocations: the repair bill ASLR sends you

ASLR (Module 01 Chapter 03) loads the image at a random base, so every absolute
pointer the linker wrote is now wrong by the same offset — the load bias. The
linker cannot fix them (it doesn't know the base), so it leaves a repair list:
the relocation records.

```console
$ objdump -R demo | head -3
OFFSET           TYPE              VALUE
0000000000003dd0 R_X86_64_RELATIVE  *ABS*+0x0000000000001160
0000000000003dd8 R_X86_64_RELATIVE  *ABS*+0x0000000000001120
```

`R_X86_64_RELATIVE` is the whole ASLR-fixup mechanism in one line of
arithmetic: **`*slot = load_bias + addend`**. First record: the 8 bytes at
image offset 0x3dd0 are stale; write `B + 0x1160` there, where `B` is where you
actually loaded me. Position-independence is not compiled-in magic; it is
this deferred repair.

Cross-image pointers need more: the linker doesn't know *which* libc, at
*which* address, `printf` comes from. Two shapes cover imports:

- **`R_X86_64_GLOB_DAT`** (5 records, `.got` 0x3fc0–0x3fdf): resolved
  *eagerly* at startup into the 8-byte slot; used for address-taken imports
  like `__libc_start_main`.
- **`R_X86_64_JUMP_SLOT`** (4 records, `.got.plt` 0x4000–0x401f): the call
  slots, resolved *lazily* — the next section's entire subject.

The two sections meet different fates. `.got` lies inside the `GNU_RELRO` range
`[0x3dd0, 0x4000)`, which the loader `mprotect`s read-only after relocating.
The `.got.plt` slots start at 0x4000, *just past* that end — deliberately
writable, because lazy binding writes to them at runtime. Partial vs full RELRO
is exactly whether this boundary survives.

## The PLT/GOT dance: binding a symbol on first call

Resolving 4 imports is free; real programs import hundreds, and half are never
called. The lazy scheme defers each binding to the first call, via two
linker-emitted structures:

- **PLT** (0x1020): 16-byte stubs — `free@plt` 0x1030, `printf@plt`
  0x1040, `malloc@plt` 0x1050, `sleep@plt` 0x1060 — plus the special entry
  `PLT0`.
- **GOT.PLT** (0x3fe8): 8-byte slots; `printf`'s is 0x4008. Three reserved
  entries first: `GOT[0]` = `&_DYNAMIC`, `GOT[1]` = link_map, `GOT[2]` =
  resolver (the last two filled by `ld.so` at load).

The trick: the file's initial `GOT[printf]` value is **0x1046** — not `printf`,
but the second instruction *inside `printf@plt`*.

```text
FIRST call — the resolver dance                    EVERY LATER CALL — two instructions

  caller: call printf@plt                            caller: call printf@plt
        |                                                  |
        v                                                  v
 0x1040 <printf@plt>:  jmp *GOT[printf]           0x1040 <printf@plt>: jmp *GOT[printf]
        |                                                  |
        v                                                  v
 0x4008 GOT[printf] == 0x1046  <- INITIAL:         0x4008 GOT[printf] == &printf  <- PATCHED
        |   points BACK at the push inside                  |
        |   this same PLT entry (not the jmp)               v
        v                                             printf() in libc
 0x1046: push $0x1        <- my JUMP_SLOT index
        |
 0x104b: jmp PLT0
        |
        v
 PLT0 (0x1020): push [GOT+8]     <- link_map
                jmp  *[GOT+16]    <- _dl_runtime_resolve
        |
        v
 resolver: look up "printf" in the link_map's
 scope, WRITE &printf into slot 0x4008, jump
 to it as if the call had been direct
```

Walk the first call: the stub jumps *indirectly* through the GOT slot, which
bounces control back to the stub's own `push $reloc_index` (`printf`'s is 1);
that index rides the stack into `PLT0`, which pushes the link_map (the
loader's object database) and jumps into `_dl_runtime_resolve`. The resolver
looks up `printf`, **rewrites the GOT slot**, and enters the real function.
One-time cost: ~5 instructions plus a lookup; steady state: `jmp *GOT`.

`LD_DEBUG=bindings ./demo` shows `printf` resolving *after* `transferring
control`; rerun it with `LD_BIND_NOW=1` and the same binding line moves
*before* — lazy versus eager, on one line each.

`-fno-plt` is the modern alternative: the compiler emits
`call *printf@GOTPCREL(%rip)` — an indirect call straight through the GOT slot,
no stub, no dance. The price: the slot must be settled before the first call,
so `-fno-plt` is only attractive paired with eager binding (`-z now`) — both
shapes are instantly recognizable in disassembly (Module 04 Chapter 03).

## Resolution order: interposition as feature and attack

The resolver walks a *scope* whose order is a contract every Linux program
silently relies on:

```text
  1. LD_PRELOAD objects          <- ahead of everything
  2. the executable's .dynsym    <- your definitions win over libraries'
  3. DT_NEEDED dependencies,     <- breadth-first: libc, then its needs
     in load order
  ---- symbol scope above | file search below ----
  DT_RPATH -> LD_LIBRARY_PATH -> DT_RUNPATH -> /etc/ld.so.cache
  -> default trusted dirs (/lib, /usr/lib, multiarch variants)
```

Rule 1 is load-bearing: **anything in `LD_PRELOAD` replaces any library
symbol**. Interposition cuts both ways:

- **Instrumentation**: a preloaded shim wraps `malloc` with a counter (the lab
  below) without rebuilding the program — every malloc profiler and
  `tcmalloc`/`jemalloc` drop-in is this rule. (Setuid binaries: `ld.so` sees
  `AT_SECURE` and ignores `LD_*`.)
- **Attack surface**: the lazy GOT hands the attacker a writable function
  pointer the program will call *for you*: overwrite `printf@got` with
  `system`, and the next `printf(user_string)` is a shell — the
  GOT-overwrite family, with `ret2dlresolve` its lazy sibling (Module 02
  Chapter 06).

The fix closes the writable window: **full RELRO** — `-z relro -z now` — binds
every symbol eagerly at startup, then `mprotect`s the *whole* GOT, `.got.plt`
included, read-only. Cost: a few startup milliseconds; gain: the classic write
target dies — post-full-RELRO exploits pivot to hooks, `exit` handlers, and
`FILE` structures (Module 02 Chapter 06's ledger). Debian 13's gcc 14
default: *partial* — `GNU_RELRO` present, no `BIND_NOW` — so 0x4000 stays
writable.

## The loader as a library: dlopen, dlmopen, static, vDSO

`dlopen`/`dlsym` are this same machinery at runtime: map, relocate, resolve,
run `.init_array`, return a handle. Two flags matter:

- **`RTLD_LOCAL`** (default): the DSO's symbols are invisible to later
  resolutions — it cannot accidentally satisfy another module's imports.
- **`RTLD_GLOBAL`**: its symbols join the global scope — required for C++
  `typeinfo` unification across DSOs (a `catch` in one matching a `throw` in
  another).

`dlmopen(LM_ID_NEWLM, ...)`
loads into a fresh *namespace* (up to 16, `DL_NSS`) with its own libc — real
isolation for plugin/audit scenarios, and a real trap (two allocators; see the
quiz).

**Static linking** trades all of this away: one self-contained file, no
resolution, no startup relocations, and no interposition *possible* — exactly
why some proprietary and most malicious builds go static. The costs: every
libc security fix means a relink, and glibc static is not fully static — NSS
symbols like `getpwnam` still `dlopen` modules at runtime (glibc warns about
it).

**vDSO/vvar**: the two kernel-created mappings in every process — `[vvar]`
(`r--p`, 4 pages here) and `[vdso]` (`r-xp`, 2 pages). The kernel maps *code
and data into userspace* so `clock_gettime` runs without a syscall (Module 01
Chapter 03's page machinery used in reverse). `ld.so` treats it as just another
object; `ldd` shows it as `linux-vdso.so.1`. Module 07
Chapter 04 dissects the vvar seqlock protocol.

## What insiders know

- **RELRO partial vs full**: full RELRO is literally `BIND_NOW` in
  `.dynamic` plus `GNU_RELRO` covering `.got.plt`. Check with
  `readelf -dW binary | grep BIND_NOW` (present = full) and
  `readelf -lW | grep GNU_RELRO` (absent = no RELRO at all — the real
  finding). Debian 13 gcc 14 default: partial; hardened builds add `-z now`.
- **The resolution order has an escape hatch**: `-Bsymbolic` (or
  `-Bsymbolic-functions`) binds a DSO's internal references to itself at link
  time — faster (no lookups), and it breaks interposition of *internal* calls.
  A `LD_PRELOAD` shim that "doesn't work" often lost to this flag.
- **The four `LD_DEBUG` sections people actually use**: `bindings` (which
  symbol resolved where — proof of laziness by line ordering), `files,libs`
  (the map/dependency trace), `statistics` (relocation counts — 96 for our
  trivial demo, 100 final), and `symbols` (the full search trace; enormous on
  real programs). Output: stderr, pid-prefixed. `all` is the firehose.
- **Why `-fno-plt` broke interposition workflows**: the call no longer passes
  through a 16-byte stub, so the classic hook — overwrite the stub's first
  bytes with a `jmp` — has no code to patch, and lazy binding has no
  trampoline to land on. Symbol-level `LD_PRELOAD` still works; *PLT-patching*
  instrumentation does not.
- **`ldd` runs the loader, not a parser** — `ld.so` in
  `LD_TRACE_LOADED_OBJECTS=1` mode, executing the startup path. The man
  page's "never run it on untrusted executables" is literal: a crafted
  ELF can run code under `ldd`.
- **The dlmopen two-libc trap**: a namespace gets its *own* libc — own malloc
  arenas, own TLS. Passing a heap pointer across a namespace boundary
  (allocate in plugin, free in host) corrupts a heap the receiving libc
  cannot name. Symptom: crashes far from the crime scene, only when the
  plugin is isolated.
- **`linux-vdso.so.1` is not a file**: no such path exists on disk — it is
  the kernel's mapped code reported through the loader's interface. Its
  address changes per *boot*, not per exec — subtle when correlating pointers
  across dumps.

## See it yourself

All numbers from glibc 2.41 / `ld.so` 2.41, Debian 13, x86-64; your addresses
will differ (ASLR working).

**1. Watch the loader work:**

```c
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(void) {
    char *s = malloc(100);  printf("hello\n");  free(s);
    sleep(2);               // window to read /proc/PID/maps
    return 0;
}
```

```console
$ gcc -o demo demo.c
$ LD_DEBUG=files,libs ./demo 2>&1 | head -10
     25032: file=libc.so.6 [0];  needed by ./demo [0]
     25032:  search cache=/etc/ld.so.cache
     25032:   trying file=/lib/x86_64-linux-gnu/libc.so.6
     25032: file=libc.so.6 [0];  generating link map
     25032:   dynamic: 0x7f2aed6b0940  base: 0x7f2aed4cc000  size: 0x1f3e50
     25032: calling init: /lib64/ld-linux-x86-64.so.2
     25032: calling init: /lib/x86_64-linux-gnu/libc.so.6
     25032: transferring control: ./demo
```

That sequence *is* the pre-`main` timeline.

**2. Interpose `malloc` with a counting shim.** Fifteen lines:

```c
// shim.c
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stddef.h>   // size_t
#include <stdio.h>
static long count;                    // the number we care about
static void *(*real_malloc)(size_t);  // resolved on first call
void *malloc(size_t n) {              // our replacement: count, then forward
    if (!real_malloc) real_malloc = dlsym(RTLD_NEXT, "malloc");
    count++;
    return real_malloc(n);
}
__attribute__((destructor)) static void report(void) {  // runs at exit
    fprintf(stderr, "malloc called %ld times\n", count);
}
```

```console
$ gcc -shared -fPIC -o shim.so shim.c      # glibc >= 2.34: no -ldl needed
$ LD_PRELOAD=./shim.so ./demo
hello
malloc called 2 times
```

Why **2**, when the program calls `malloc` once? The second is `printf`'s
lazily-allocated stdout buffer (4,096 bytes when stdout is a pipe — allocated
through *our* `malloc` on first write).

**3. `ldd`, and the GOT's "before" state:**

```console
$ ldd ./demo
        linux-vdso.so.1 (0x00007ffc0a579000)      <- kernel code, no file
        libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f326fe7e000)
        /lib64/ld-linux-x86-64.so.2 (0x00007f3270088000)
$ objdump -s -j .got.plt demo | tail -2
 4008 46100000 00000000 56100000 00000000  F.......V.......
 4018 66100000 00000000                    f.......
```

Little-endian: slot 0x4008 (`printf`) holds 0x1046 — the `push` inside
`printf@plt`, the dance's "before" half, frozen in the file.

**4. The memory picture after load:**

```console
$ ./demo & sleep 0.3; PID=$!; grep demo /proc/$PID/maps
55e33024e000-55e33024f000 r--p 00000000 ... /tmp/demo   <- ELF headers
55e33024f000-55e330250000 r-xp 00001000 ... /tmp/demo   <- .text (PT_LOAD R E)
55e330250000-55e330251000 r--p 00002000 ... /tmp/demo   <- .rodata/.eh_frame
55e330251000-55e330252000 r--p 00002000 ... /tmp/demo   <- RELRO: .got mprotect'ed RO
55e330252000-55e330253000 rw-p 00003000 ... /tmp/demo   <- .data/.bss (8 B past file)
$ grep -E 'vvar|vdso' /proc/$PID/maps
7ffd8557d000-7ffd85581000 r--p 00000000 00:00  [vvar]   <- kernel data pages
7ffd85581000-7ffd85583000 r-xp 00000000 00:00  [vdso]  <- kernel code pages
```

Five file-backed rows for four `PT_LOAD`s: the extra `r--p` row is
`GNU_RELRO` after the loader's `mprotect` — partial RELRO, visible — and
`.got.plt` (0x4000+) sits in the still-writable `rw-p` row.

## How to use this knowledge

- **Triage a stripped ELF**: `.rela.plt`/`.got.plt` are a function-level
  summary of what a binary touches — our 4-entry list *is* the demo's entire
  external API. Read it right after Chapter 01's headers.
- **Settle "which code ran" and startup costs**: `LD_DEBUG=bindings` answers
  "libc's `malloc` or jemalloc's"; `LD_DEBUG=statistics` gives relocation
  counts; `-z now` trades lazy-binding time for RELRO hardness; each plugin
  `dlopen` costs this entire chapter again.
- **Read exploits correctly**: a GOT write that segfaults is full RELRO doing
  its job — check `BIND_NOW` before believing a write-up's "arbitrary write"
  claims (Module 02 Chapter 06's ledger).

## Connections

- **Module 01 Chapter 03** — page-aligned `PT_LOAD` mapping, BSS zero-fill via
  the zero page, ASLR's load bias as *the reason relocations exist*.
- **Module 02 Chapter 06** — GOT overwrite and `ret2dlresolve` against the
  lazy slot; full RELRO's rung on the mitigation ladder.
- **Module 04 Chapter 01** — the two-tables payoff: `PT_LOAD`/`PT_INTERP`
  (loader view) and `.got`/`.rela.plt` (linker view), one file.
- **Module 04 Chapter 03** — `jmp *disp(%rip)` and `call *sym@GOTPCREL(%rip)`
  in disassembly are this chapter's GOT indirection, with and without the PLT.
- **Module 06 Chapter 04** — dyld solves the same repair bill with chained
  fixups stored *in* the data pages, making rebase lazy per faulted page —
  the contrast behind both loaders' startup profiles.
- **Module 07 Chapter 02** — the Windows sibling: PE relocations and the IAT
  play the GOT's role; LoaderLock is that loader's init-order hazard.
- **Module 07 Chapter 04** — this loader at full depth: `DT_RELCOUNT`,
  `-Bsymbolic`, the vvar seqlock, `dlmopen` in practice.

## Quiz

1. **Concept.** Why must `p_offset ≡ p_vaddr (mod 4096)` hold for every
   `PT_LOAD`? What breaks, concretely, if a linker violated it?
2. **Concept.** Why can the linker not write `printf`'s final address into the
   GOT slot at link time — what *two* kinds of ignorance does it have?
3. **Predict the number.** A PIE binary's `RW` load has `filesz 0x260, memsz
   0x268`, vaddr 0x3dd0. How many bytes of BSS exist, at which address, and
   what supplies them on first read and first write?
4. **Predict the output.** The lab shim is preloaded onto a program that calls
   `malloc` once and `printf` once, stdout to a pipe. What count prints, and
   where does the extra call originate?
5. **Bug diagnosis.** A colleague's shim crashes with stack exhaustion during
   startup. Their `malloc` calls `dlsym` on *every* call, and they also
   interpose `calloc`. What is the loop, and what are the two standard fixes?
6. **Bug diagnosis.** An exploit writes `system`'s address to `printf@got` in
   a binary built with `-z relro -z now` and segfaults on the write itself.
   What happened, and how do you confirm it in five seconds?
7. **Bug diagnosis.** A plugin loaded via `dlmopen(LM_ID_NEWLM, ...)` returns
   a `malloc`ed buffer to the host program, which `free`s it. The heap corrupts
   — but only when the plugin runs in its own namespace. Why?
8. **Cross-chapter.** dyld (Module 06 Chapter 04) uses chained fixups instead
   of a relocation list; both compute "base + addend". Name the concrete
   advantage dyld's format buys on iOS, and the startup-profile difference
   versus `ld.so`.

<details>
<summary>Answers</summary>

1. Each segment is one `mmap`, which can only place *file page N* at
   *address-space page N*; congruence keeps every image page backed by exactly
   one file page, or the loader must map page-by-page with split offsets. The
   kernel's page-granularity contract (Module 01 Chapter 03) leaks into
   `p_align` (Module 04 Chapter 01).
2. (a) *Which* object satisfies `printf`: `DT_NEEDED` carries a name, and the
   resolution order (LD_PRELOAD ahead of libc) is decided at load time. (b)
   *Where* it lands: ASLR. `R_X86_64_RELATIVE` fixes only the intra-image half
   of (b).
3. 8 bytes at image_base + 0x4030 (`memsz - filesz = 8`). First read: the
   kernel's shared zero page — no I/O. First write: the COW fault materializes
   a private zeroed page — Module 01 Chapter 03's demand paging, invoked by
   the loader.
4. 2: the program's `malloc(100)`, plus `printf`'s first write allocating the
   4,096-byte stdout buffer through the *interposed* `malloc` (glibc, stdout
   fully buffered to a pipe). The report's timing proves the
   `exit`/`.fini_array` step.
5. `dlsym` itself allocates (glibc error bookkeeping can go through `calloc`);
   the interposed `calloc` calls `dlsym` — unbounded recursion before any user
   allocation. Fixes: resolve once into a static and never re-enter (the lab
   shim's pattern); or serve pre-resolution allocations from a small static
   bootstrap arena — the canonical malloc-shim pattern.
6. `-z now` bound every JUMP_SLOT eagerly, then the loader `mprotect`ed the
   whole GOT — `.got.plt` included — read-only; the write hit an `r--p` page.
   Confirm with `readelf -dW binary | grep BIND_NOW` (present = full).
   Post-RELRO, attackers pivot to `exit` handlers and `FILE` structures
   (Module 02 Chapter 06's ledger).
7. The namespace got its *own copy of libc* — own arenas, own heap metadata.
   The host's `free` reads the plugin-libc's bytes with the *host* libc's
   chunk headers. In the default namespace both are the same glibc, so the bug
   hides; fix: never pass ownership across a namespace boundary.
8. Chained fixups store each fixup *in place* in the data pages, threaded
   through the pointers' high bits. dyld fixes only faulted pages, and
   untouched pages stay COW-shared with the shared cache — with 1–3 GB of
   frameworks on iOS, that laziness is the product. `ld.so` walks its whole
   list eagerly before `main`. Same arithmetic, opposite accounting.

</details>
