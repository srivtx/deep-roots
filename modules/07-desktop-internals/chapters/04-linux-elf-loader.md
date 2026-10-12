# Chapter 04 — Linux: ELF & the loader

> **The 30-second version.** `execve` on a dynamically linked Linux binary runs
> two programs, not one. The kernel maps your ELF's loadable segments, reads
> `PT_INTERP` — a header naming a *second* ELF, `/lib64/ld-linux-x86-64.so.2` —
> maps that too, and jumps to *its* entry point. That loader, `ld.so`, is userspace
> code that finishes the job: map every library, zero BSS, apply relocations
> (rebase first, resolve symbols second), run initializers, then jump to `main`.
> The kernel also pre-maps two regions no file backs — the vDSO (kernel *code*
> called directly, so `clock_gettime` costs ~15 ns, not a ~100 ns syscall) and the
> vvar data page, read with a seqlock. `dlopen`/`dlmopen` replay the machinery at
> runtime; `LD_DEBUG=files,libs` traces it. Numbers: glibc 2.41 (Debian 13),
> kernel 5.10.134, x86-64.

## One exec, two programs

The kernel's ELF loader (`load_elf_binary` in `fs/binfmt_elf.c`) does less than
you'd guess. It maps the main binary's `PT_LOAD` segments with `mmap`, maps the
interpreter named in `PT_INTERP` the same way, maps the vDSO and vvar pages, and
builds the initial stack: `argc`/`argv`/`envp` plus the auxiliary vector. Then it
sets the instruction pointer to the *interpreter's* entry — the first userspace
instruction executed belongs to `ld.so`, not to you. The program's entry point
travels in the auxv, for `ld.so` to jump to later:

```console
$ ./demo &            # demo: malloc(100); printf("hello\n"); sleep(1)
$ cat /proc/$!/auxv | auxv_decode
AT_BASE: 0x7f9b8e919000        <- ld.so's load base
AT_ENTRY: 0x55e5bd2b1080       <- main binary's e_entry, for later
AT_SYSINFO_EHDR: 0x7ffce31ec000  <- vDSO base (matches [vdso] in maps, same run)
```

Why hand the hard half to userspace? Everything after mapping is *policy* — which
`libc.so.6`, in what order, with what interposition rules. Policy changes faster
than kernels; a userspace loader is traceable (`LD_DEBUG`) and preloadable
(`LD_PRELOAD`) without a reboot. The cost: `ld.so` must bootstrap itself. It is a
PIE mapped at an unknown address, so its first instructions are hand-written
position-independent code that relocates itself before touching a single global.

```console
$ readelf -lW hi
  INTERP  0x000394 0x394 ... [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]
  LOAD    0x002dd0 0x3dd0 0x250  0x258  RW   0x1000   <- the RW one
```

Static linking deletes this handoff: no `PT_INTERP`, kernel jumps straight to
`e_entry`. Measured here, the same `hi.c` is 16,008 bytes dynamic and 758,440
bytes static (gcc 14.2, glibc 2.41) — and that is the *small* price. The real
ones: no page-cache sharing of libc code across the thousands of processes mapping
it; every libc security fix means relinking every binary (the Heartbleed relink
storm); and static glibc still tries to `dlopen` NSS plugins for `getpwnam`, which
fails exactly in the minimal containers where people ship static binaries. Static
buys hermetic startup; it rents compatibility back with interest.

## Mapping a segment: the congruence rule

`mmap` is the only tool, and it works in whole pages: a file page
`[4096k, 4096k+4096)` maps onto a virtual page `[4096m, 4096m+4096)` so that file
byte `4096k+j` appears at address `4096m+j`. Demand that segment byte
`p_offset + i` land at `p_vaddr + i` for every `i`. The byte at `p_offset` sits
at intra-page offset `p_offset mod 4096` in the file's page, but must appear at
intra-page offset `p_vaddr mod 4096` in the virtual page. Those must be equal, or
the mapping would need to start mid-page. Hence the rule every linker must obey:

**`p_offset ≡ p_vaddr (mod page size)`** — congruence, not equality. The
difference `p_vaddr - p_offset` may be any multiple of 4096 (here `0x3dd0 -
0x2dd0 = 0x1000`, a one-page skew). It generalizes to `p_align`: congruent mod
2 MB for huge-page segments — one reason they stay rare.

The last `PT_LOAD` shows the second half, BSS:
`p_memsz (0x258) > p_filesz (0x250)`. The 8 extra bytes must be zero — C promises
zero-initialized globals — but they land in a page whose file bytes are padding,
so the loader zeroes explicitly:

```text
 two mmap calls (vaddr - file skew = +0x1000, congruent mod 4096):

 file page 2 [0x2000,0x3000) ──mmap──▶ vaddr page 3 [0x3000,0x4000)  r--p after RELRO
    [0x2dd0,0x3000) seg bytes              [0x3dd0,0x4000) .data.rel.ro, .init_array
 file page 3 [0x3000,0x4000) ──mmap──▶ vaddr page 4 [0x4000,0x5000)  rw-p
    [0x3000,0x3020) seg bytes              [0x4000,0x4020) .data, .got tail
    [0x3020,0x4000) file padding           [0x4020,0x4028) .bss, 8 B ← memset(0)

 p_memsz - p_filesz = 8 bytes of BSS inside an already-mapped page → memset only.
 A BSS crossing the page end would get whole MAP_ANONYMOUS zero pages —
 anonymous mappings are zero by construction.
```

The kernel does this for the main binary; `ld.so` repeats it for every library
and every later `dlopen`. The trailing `GNU_RELRO` header then tells the loader
to `mprotect` `[0x3dd0,0x4000)` read-only *after* relocation — hence the `r--p`
page below the `rw-p` one in `/proc/self/maps` (module 04 ch02: a read-only GOT
closes the overwrite-hijack path).

## Rebasing first, resolving second

Once every object is mapped, `ld.so` fixes addresses in two phases. Phase one
needs *no symbol lookup*. Every `.rela.dyn` entry of type `R_X86_64_RELATIVE` is
just "the addend plus the load bias belongs at `r_offset`":
`*(base + r_offset) = base + r_addend` — one tight loop re-points every address
constant in a PIE's `.data`. The linker sorts these to the front of `.rela.dyn`
and emits `DT_RELACOUNT` so the loader skips all symbol machinery for them —
above, `RELACOUNT 3`. glibc 2.41's own `libc.so.6` uses the newer form, `DT_RELR`
— a packed bitmap at 8 bytes per word instead of 24 per entry (280 bytes total) —
and builds with `-z now` (`FLAGS: BIND_NOW`), leaving nothing lazy. Phase two is
everything involving a symbol name, and it walks the *scope*, in this order:

```text
 symbol lookup for a reference from the main program (glibc's one global scope):

   1. LD_PRELOAD objects, in the order listed in the variable
   2. the main executable
   3. DT_NEEDED entries, breadth-first: level 1 = direct deps, level 2 = their deps...
   4. --- not found in scope? now it is a *file* problem: ---
      /etc/ld.so.cache   (ldconfig's soname→path table, one mmap, binary search)
   5. fallback dirs /lib, /usr/lib
```

First definition wins — that single rule *is* interposition: a preloaded object
sits at position 1, so its `malloc` is found before libc's ever is. Library *file*
search is a separate ladder (`DT_RPATH`, `LD_LIBRARY_PATH`, `DT_RUNPATH`, cache,
defaults) — skipped in secure-execution mode (setuid), which ignores `LD_PRELOAD`
and `LD_LIBRARY_PATH` entirely.

The escape hatch is `-Bsymbolic` (link-time, per library): references *within* the
DSO to its own definitions are resolved at static link time — internal calls skip
the PLT; data references become relative relocations. Edge one: startup
gets cheaper — fewer relocations, zero internal lookups. Edge two: interposition
*into* that library is now impossible. glibc's `libc.so.6` is deliberately *not*
built that way so `malloc` stays interposable (the lab proves it: libc-internal
`printf` allocations flow through the shim). `-Bsymbolic-functions` takes only the
function half, leaving data like `errno` interposable — the compromise most
libraries that adopt it use; musl goes the other way.

## The vDSO: kernel code in your address space

A syscall costs 60–150 ns on modern x86-64 (mode switch, entry checks, Spectre
mitigations) against ~15 ns for a call — yet every program reads the time
constantly. The kernel's answer: map its own code into your address space. Two
regions appear at exec that no file backs:

```text
 (real /proc/PID/maps for ./demo, kernel 5.10.134, x86-64)

 0x5568c46b9000  [heap]        rw-p   132 KB   anon, brk (ch 05's arena)
 0x7ffd42313000  [stack]       rw-p   132 KB   anon, grows down
 0x7ffd42363000  [vvar]        r--p    16 KB   kernel DATA page — no file
 0x7ffd42367000  [vdso]        r-xp     8 KB   kernel CODE page — no file
 0xffffffffff600000 [vsyscall] r-xp    4 KB    legacy page, trap-and-emulated
        (libc and ld-linux mappings sit in between, file-backed, shared)
```

`AT_SYSINFO_EHDR` (0x7ffce31ec000 above) points at the vDSO — a tiny ELF shared
object *in memory*: glibc parses its symbol table at startup and binds
`clock_gettime`, `gettimeofday`, `time`, `getcpu` to the `__vdso_*` symbols if
present, falling back to syscalls otherwise. The data lives one page down in
`vvar`: the clocksource's `mult`/`shift`, wall-clock and monotonic bases, a seq
counter. Reading it is a **seqlock** protocol — the insider's point: *a fast time
read is a shared-memory algorithm, not a call*:

```c
 // vvar read protocol (vsyscall_gtod_data, x86-64 kernel 5.10; the vdso does this)
 unsigned seq;
 do {
     seq = gtod->seq;            // 1. even = stable, odd = writer in progress
     if (seq & 1) continue;      //    retry — readers never block the writer
     rmb();                      // 2. data reads must not move above the seq read
     sec  = gtod->wall_time_sec + ...;  // (mult/shift arithmetic on the TSC)
     rmb();                      // 3. and must complete before we re-check
 } while (gtod->seq != seq);     // 4. version moved → the data was torn: retry
```

The writer is the kernel's clock-update path (each tick, or a clocksource
change): bump `seq` odd, write fields, bump even. Readers retry; nobody waits —
two loads of one counter replace a syscall, the same trick as a lock-free ring
buffer (module 01 ch20's memory ordering, in kernel clothing). A shared
read-only page cannot differ per process, so time namespaces give each namespace
a private (COW) vvar with its own clock offsets — why newer kernels also map a
`[vvar_vclock]` region. The `[vsyscall]` page at `0xffffffffff600000` is the
1999 ancestor; this kernel maps it and *emulates* its calls by trapping — alive
only for ancient static binaries.

## dlopen, dlmopen, and the dlclose rumor

Everything above also runs on demand. `dlopen("libplug.so", flags)` maps segments,
processes relocations, runs initializers — the same code paths, recursively
resolving the object's `DT_NEEDED` tree. Two flags matter for symbols:
`RTLD_LOCAL` (default) keeps the library's symbols invisible to the global scope
— only the handle's `dlsym` and its dependents see them; `RTLD_GLOBAL` publishes
them for later `dlopen`s and `dlsym(RTLD_DEFAULT, ...)`. `RTLD_LAZY/NOW` replays
the lazy-binding choice per library.

`dlclose` decrements a per-object reference count and unmaps at zero — on paper.
The folklore: it usually doesn't unload. Dependencies hold references (a plugin
linking libpng pins libpng), so the count rarely reaches zero; and even at zero,
unloading is the *dangerous* path — another thread's cached function pointer or
a key destructor then dereferences unmapped memory. The JVM, CPython and dotnet
load native extensions and almost never `dlclose`, by policy. Treat `dlclose` as
a hint, never a correctness mechanism.

`dlmopen` is the same call with a namespace ID. Each namespace has its own
link-map list and dependency tree — 16 maximum (`DL_NNS`) — and a library
`dlmopen`ed into a fresh namespace drags its *own* dependency
copies along:

```console
$ ./ns        # dlmopen(LM_ID_NEWLM, "libc.so.6", RTLD_NOW); compare mallocs
default-ns malloc: 0x7ff4b28998d0
newlm-ns malloc:  0x7ff4b26a28d0
same libc? NO - two copies
```

Two libcs, two `errno`s, two mallocs — the isolation, and exactly how `LD_AUDIT`
modules load without polluting the program's namespace. The trap is symmetric: a
pointer allocated by namespace 2's malloc and freed by namespace 1's corrupts a
heap it doesn't belong to (module 01 ch19's arena problem, one API call later).
Only discipline crosses namespace boundaries.

## What insiders know

- **The congruence rule has teeth**: `p_offset ≢ p_vaddr (mod 4096)` is not a
  warning — the loader refuses the object ("cannot handle file with load bias").
  Hand-crafting ELF segments (module 04 ch01) means re-deriving this rule or
  failing.
- **The vvar seqlock is where time comes from**: `strace -c ./demo` shows *zero*
  `clock_gettime` syscalls — the call never traps. A tight clock loop showing
  ~100 ns outliers means seqlock retries against the tick, not a "slow syscall".
- **-Bsymbolic's double edge**: it deletes internal lookups (faster startup) and
  deletes interposition *into* that library — glibc declines it precisely so
  your preload can replace `malloc`; a `-Bsymbolic`'d libc (musl's model) cannot
  be instrumented this way.
- **LD_DEBUG has sections beyond files/libs**: `bindings` prints every relocation
  decision ("binding file ./demo to libc.so.6: normal symbol `malloc`") — the
  interposition debugger; `statistics` prints cycles and counts (this box: 67,869
  loader cycles, 1,024 (1.5%) in relocation, 96 relocations, 3 relative).
- **dlmopen isolation cuts both ways**: a new namespace gets its own libc — own
  malloc, own `errno` (the isolation `LD_AUDIT` relies on), and any pointer
  crossing the boundary is a latent heap-corruption bug (the lab's two `malloc`
  addresses).
- **`ldd` is the loader in disguise**: it runs the program's interpreter with
  `--list` — `linux-vdso.so.1` shows up as a "library" with no file. `ldd` on an
  untrusted binary is code execution, not inspection.
- **`DT_RELR` is the new `DT_RELACOUNT`**: packed relative relocations, 8 bytes
  per word instead of 24 per entry — glibc ≥ 2.36 reads it, binutils
  `-z pack-relative-relocs` writes it, Debian 13's libc carries 280 bytes.

## See it yourself

All real, captured on this box (Debian 13, glibc 2.41, kernel 5.10.134).

**1. The pre-`main` timeline, traced.** Build the two-liner (`malloc(100);
printf("hello\n"); free; sleep`) and let the loader narrate:

```console
$ LD_DEBUG=files,libs ./demo 2>&1 | grep -v '^$' | head -14
     16118: file=libc.so.6 [0];  needed by ./demo [0]
     16118:  search cache=/etc/ld.so.cache
     16118: file=libc.so.6 [0];  generating link map
     16118:   dynamic: 0x7f7531f12940  base: 0x7f7531d2e000  size: 0x1f3e50
     16118: calling init: /lib64/ld-linux-x86-64.so.2
     16118: calling init: /lib/x86_64-linux-gnu/libc.so.6
     16118: initialize program: ./demo
     16118: transferring control: ./demo
```

Read it as a timeline: cache hit, link map, *init in dependency order (loader,
libc, program)*, then the handoff.

**2. Interpose `malloc` (module 04 ch02's counter, now with the mechanism).**

```c
// shim.c
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stddef.h>
#include <stdio.h>
static long count;
static void *(*real_malloc)(size_t);
void *malloc(size_t n) {
    if (!real_malloc) real_malloc = dlsym(RTLD_NEXT, "malloc");
    count++;
    return real_malloc(n);
}
__attribute__((destructor)) static void report(void) {
    fprintf(stderr, "malloc called %ld times\n", count);
}
```

```console
$ gcc -shared -fPIC -o shim.so shim.c
$ LD_PRELOAD=./shim.so ./demo
hello
malloc called 2 times
```

The mechanism is position 1 of the scope: the shim's `malloc` is found before
libc's, and `dlsym(RTLD_NEXT, ...)` reaches *the next definition after us* —
libc's. The second count is `printf`'s 4,096-byte stdout buffer, allocated lazily
on first write by libc-internal code *through our shim* — proof that glibc is
not `-Bsymbolic`'d.

**3. `ldd`, and the maps with origins labeled.**

```console
$ ldd ./demo
        linux-vdso.so.1 (0x00007ffe71d84000)      <- "library" with no file
        libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007fd8b587d000)
        /lib64/ld-linux-x86-64.so.2 (0x00007fd8b5a87000)
$ ./demo & sleep 0.3; grep -E 'demo|libc|ld-linux|\[' /proc/$!/maps
559324970000-559324973000 r--p/r-xp/r--p  pg 0-2    /tmp/m07lab/demo  <- LOADs 1-3
559324973000-559324974000 r--p 00002000 ... /tmp/m07lab/demo   <- LOAD 4 pg 1: RELRO'd
559324974000-559324975000 rw-p 00003000 ... /tmp/m07lab/demo   <- LOAD 4 pg 2: .data+.bss
559325df2000-559325e13000 rw-p 00000000 00:00 0    [heap]      <- anon, brk
7f2054dc0000-7f2054fb4000 (r--p/r-xp/r--p/r--p/rw-p) libc.so.6 + anon .bss tail
7ffd1b4a7000-7ffd1b4c8000 rw-p 00000000 00:00 0    [stack]
7ffd1b55d000-7ffd1b561000 r--p 00000000 00:00 0    [vvar]      <- 16 KB kernel data
7ffd1b561000-7ffd1b563000 r-xp 00000000 00:00 0    [vdso]      <- 8 KB kernel code
ffffffffff600000-ffffffffff601000 r-xp 00000000 0  [vsyscall]  <- emulated legacy
```

Check the arithmetic you derived: LOAD 4's pages are `r--p` at file offset 0x2000
and `rw-p` at 0x3000 — the +0x1000 skew and the RELRO split. Then
`cat /proc/$!/auxv`: `AT_SYSINFO_EHDR` equals the `[vdso]` line, by construction.

**4. Two libcs in one process (optional).** The `ns.c` dlmopen program above
(build with `-ldl`): run it and watch
`dlsym(h, "malloc") != dlsym(RTLD_DEFAULT, "malloc")`.

## How to use this knowledge

- **Startup latency triage**: `LD_DEBUG=statistics` separates mapping from
  relocation cost (67,869 cycles here — trivial; a plugin-laden app can spend
  milliseconds). Relocations dominating → `-Bsymbolic-functions` or fewer
  preloads; load cost dominating → too many `DT_NEEDED`s, or a giant library
  needing `-fvisibility=hidden`.
- **Debugging "wrong function called"**: `LD_DEBUG=bindings` names the winner of
  every lookup. "My preload doesn't take effect" has three causes you can now
  enumerate: setuid (`AT_SECURE`), `-Bsymbolic` in the caller, or a reference
  resolved at static link time in the main binary.
- **Plugin architecture decisions**: `dlopen` + `RTLD_LOCAL` by default,
  `RTLD_GLOBAL` only for a deliberate ABI surface; never free across a `dlmopen`
  boundary; never design correctness around `dlclose` actually unmapping.
- **Time in benchmarks**: know when `clock_gettime` is a call (vDSO, ~15 ns) and
  when it's a syscall (sandboxes that seccomp it into `clock_gettime64`) — a
  5–10× swing on the most-called function in many profiles.

## Connections

- **Module 01 ch03 (virtual memory)**: the vDSO/vvar pair is `mmap`'s
  file-caching idea inverted — kernel-owned physical pages mapped into every
  address space, no disk; the congruence rule is "mmap works in pages" applied
  twice.
- **Module 04 ch02 (linking & loading)**: the RE twin — PLT/GOT lazy binding and
  RELRO derived there from the disassembly side; this chapter is the same
  mechanism from the loader's side, and the malloc shim is that lab finally
  explained.
- **Module 07 ch02 (Windows PE & the loader)**: compare the division of labor —
  Windows' loader lives *in* the kernel (ntdll as its user-mode shadow, LoaderLock
  serializing DllMain), Linux delegates to userspace `ld.so`; same search-order
  folklore, different trust boundary.
- **Module 11 ch01 (the disk contract)**: databases bypass the page cache with
  `O_DIRECT` to own their caching; vvar is the opposite — a page with *no* file
  at all. Both meet at "a mapped page is just memory until someone defines its
  backing."

## Quiz

1. Derive it, don't recall it: why must `p_offset ≡ p_vaddr (mod 4096)` hold for
   every `PT_LOAD`, and what breaks if a crafted ELF violates it?
2. In the `LD_DEBUG=files` trace, `transferring control: ./demo` is the last
   line. Whose instruction executes first in this process's lifetime, and
   why is it not `main`'s?
3. A segment has `p_vaddr = 0x3dd0`, `p_filesz = 0x250`, `p_memsz = 0x258`.
   Predict: how many BSS bytes, at which addresses, and how many *extra* `mmap`
   calls for them?
4. The shim prints `malloc called 2 times` for one `malloc(100)` plus one
   `printf`. Predict the count if the program first calls
   `setvbuf(stdout, NULL, _IONBF, 0)`, and explain the difference.
5. A plugin is loaded with `dlmopen(LM_ID_NEWLM, ...)`, allocates a string with
   its own `strdup`, and returns it to the host program, which `free`s it. The
   host crashes in `free` hours later with heap corruption. Diagnose.
6. You run `LD_PRELOAD=./shim.so ./demo` on a setuid-root build of `demo`; the
   counter never prints but the program works. Why?
7. (Module 01 ch03) The vDSO code page is identical in every process on the
   machine; vvar can become private. Name the memory-management mechanism for
   each case, and what forces vvar to go private.
8. A library `L` is linked with `-Bsymbolic-functions` and both defines and
   internally calls `open()`. You preload a wrapper to log every `open`. Which
   calls get logged — from `main`, from inside `L`, both, or neither — and why
   does the "functions only" half matter for `errno`?

<details>
<summary>Answers</summary>

1. `mmap` maps whole file pages to whole virtual pages with a fixed intra-page
   correspondence: file byte `4096k+j` appears at `4096m+j`. The segment demands
   `p_offset + i ↦ p_vaddr + i` for all `i`; at `i = 0` the intra-page offsets
   `p_offset mod 4096` and `p_vaddr mod 4096` must be equal, or the segment would
   start mid-page. A violating ELF places bytes at wrong intra-page offsets —
   the loader refuses it ("cannot handle file with load bias"). The
   linker enforces the rule by construction, allowing any page-multiple skew.
2. The first userspace instruction is in `ld.so`'s hand-written self-relocation
   stub: `execve` sets the entry point to the interpreter's, not the program's.
   Only `ld.so` can finish loading; the program's entry travels as `AT_ENTRY`.
   `main` runs last, after mapping, relocation, and initializers.
3. `p_memsz - p_filesz = 8` bytes of BSS, at `[0x4020, 0x4028)`. **Zero** extra
   mmap calls: the BSS ends inside the page already mapped file-backed
   (`[0x4000, 0x5000)`), so the loader zeroes it and the file padding to page end
   with `memset`. Anonymous zero mappings appear only per *whole page* of
   BSS beyond the mapped page.
4. **1**. Unbuffered stdout never allocates the 4,096-byte buffer, so only the
   explicit `malloc(100)` flows through the shim. The original second count was
   `printf`'s lazy buffer allocation — by libc-internal code *through the
   preloaded malloc*, proving libc's internal calls are not `-Bsymbolic`-bound.
5. The dlmopen'd plugin has its *own* libc (two distinct `malloc` addresses,
   proven in the lab), so the string lives in namespace 2's heap. The host's
   `free` — namespace 1's glibc — interprets those bytes as its own chunk/tcache
   metadata and writes to them; the crash surfaces when the poisoned freelist is
   next walked. Fix: a `plugin_free`, or one shared allocator.
6. Setuid execution sets `AT_SECURE`; secure-execution mode ignores `LD_PRELOAD`
   (and `LD_LIBRARY_PATH`, `LD_DEBUG`), because honoring them would let anyone
   interpose code into a root process. The shim is never mapped.
7. Code page: one set of kernel physical pages mapped into every address space —
   shared, read-execute, module 01 ch03's "many virtual addresses, one physical
   page." vvar private is copy-on-write: a process whose time namespace has
   shifted clocks must see different data, so the kernel COWs the page.
8. Logged: calls from `main` and other libraries — the preload sits at
   position 1 of the scope. Not logged: `L`'s internal calls, resolved at static
   link time to `L`'s own `open` — no PLT, no lookup, no interposition. The
   "functions only" half matters because data references stay interposable:
   `L`'s `errno` still resolves dynamically to the shared per-thread `errno`;
   a full `-Bsymbolic` would privatize the wrong storage.

</details>
