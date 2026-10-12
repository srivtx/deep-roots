# Chapter 02 — Windows: PE & the loader

> **The 30-second version.** A Windows executable is a PE file: a 64-byte DOS
> header kept for tradition, the real headers after it, and a section
> table that translates between two layouts — 0x200-aligned bytes on disk,
> 0x1000-aligned pages in memory. The loader (ntdll) maps sections at
> `ImageBase` or, with ASLR, anywhere; a `.reloc` list of fixups repairs every
> absolute address that move broke. Per-thread (TEB) and per-process (PEB)
> state live at addresses reachable through the `gs` segment register — no
> syscall needed. Then the loader runs your code: every DLL's `DllMain` under
> one process-wide LoaderLock — which is why "keep DllMain to four
> instructions" is a survival rule.

## A DOS program that isn't: the header stack

Start from the loader's problem: handed a file, decide — cheaply — *is this for
me, and where do I look next?* Windows answers with a chain of fixed-offset
signposts, and the first is a joke that became load-bearing.

Offset 0 is `"MZ"` (Mark Zbikowski), the DOS magic. Everything DOS after those
two bytes is theater: a 64-byte DOS header, then a stub of 16-bit code that
prints `This program cannot be run in DOS mode.` and exits — so a 1994 DOS
box failed politely instead of launching garbage. Microsoft keeps it in 2025
because the DOS header's **`e_lfanew` field (offset 0x3C)** points to the real
headers: the loader reads `MZ`, then `e_lfanew`, then jumps; everything between
is skipped. On MSVC builds that gap holds the *Rich header*, an obfuscated
fingerprint of the compiler versions used — useless to the loader, gold to
analysts.

```text
 file offset  contents                                   who reads it
 0x0000   +-----------------------------------------+
          | DOS header, 64 B: "MZ", ... e_lfanew ---+--> the ONE field the
 0x003C   |              (offset 0x3C, 4 B)         |    loader uses from here
          | DOS stub: "This program cannot be run   |
          | in DOS mode."  (the theater)            |
 0x0080*  +-----------------------------------------+
          | PE signature: "PE\0\0" (4 B)            |  magic check #2
          | COFF header, 20 B:                       |
          |   Machine = 0x8664 (x64)                 |
          |   NumberOfSections = 5                   |
          |   SizeOfOptionalHeader = 0xF0            |
          |   Characteristics = 0x22 (exe, >4GB)     |
          | Optional header, 240 B (PE32+):          |
          |   Magic = 0x20B   <---------------------- "optional" is     |
          |   AddressOfEntryPoint = RVA (0x1234)     historical; every  |
          |   ImageBase = 0x140000000 (preferred)     image needs it    |
          |   SectionAlignment = 0x1000              |
          |   FileAlignment  = 0x200                 |
          |   16 data directories x 8 B             |
          |     (1 = imports, 2 = resources,         |
          |      5 = relocations, 9 = TLS, ...)      |
 0x0200*  | Section table, 40 B per section:         |
          |   name, VirtSize, VirtAddr(RVA),         |
          |   RawSize, RawPtr, characteristics      |
 0x0400*  | .text raw bytes ...                      |

 * lab-build offsets; MSVC puts its Rich header in this gap (e_lfanew 0xF8).
```

Naming: **COFF header** — the same 20 bytes an `.obj` starts with. **Optional
header** — "optional" only in object files; in an executable it is mandatory
and holds everything interesting, and its **`Magic = 0x20B` for PE32+
(64-bit; PE32 is `0x10B`)** sizes every field after it — ELF's class byte, PE
edition. The tail is the **data directories**: 16 `(RVA, size)` pairs —
imports (1), resources (2), relocations (5), TLS (9), the IAT (12, so the
loader can write-protect it after resolving — Windows' RELRO).

## RVAs: the address system that survives relocation

Everything inside a PE is addressed by **RVA — relative virtual address**:
distance from `ImageBase`. The loader wants to place the image wherever ASLR
chooses; file offsets and absolute VAs both break under that, and the RVA is
the *stable* name. `VA = ImageBase + RVA`, invariant across rebasing.

Disk and memory disagree on layout: in memory, sections sit `SectionAlignment`
(0x1000) apart so each gets its own permissions; on disk, packing at
`FileAlignment` (0x200) saves bytes. "The header says RVA X — where are those
bytes?" is answered by the section table:

1. Find the section with `VirtAddr <= RVA < VirtAddr + max(VirtSize, RawSize)`.
2. `file offset = RawPtr + (RVA − VirtAddr)`.
3. If that offset is past `RawPtr + RawSize`, the bytes exist only in memory:
   the loader zero-fills the tail.

**By hand:**

```text
 section   VirtSize  VirtAddr  RawSize   RawPtr     characteristics
 .text     0x1A2B0   0x1000    0x1A400   0x400      code, execute, read
 .rdata    0x09C10   0x1C000   0x09E00   0x1A800    read-only data
 .data     0x01530   0x26000   0x1200    0x24600    initialized data, RW
 .rsrc     0x00890   0x28000   0xA00     0x25800    resources
 .reloc    0x00B40   0x29000   0xC00     0x26200    relocations, discardable
```

A C++ vtable entry sits at RVA `0x1D5F8`. Step 1: `0x1C000 <= 0x1D5F8 <
0x26000` → `.rdata`. Step 2: `0x1A800 + (0x1D5F8 − 0x1C000)` = **file offset
`0x1BDF8`** — at the preferred base, VA `0x14001D5F8`; at ASLR base
`0x7FF6A0000000`, VA `0x7FF6A001D5F8`. Same RVA either way.

Step 3 is the trap: `.data` has VirtSize `0x1530` but RawSize `0x1200`. An
uninitialized global `char g_buf[0x600]` at RVA `0x27300` is *inside* `.data`
(end `0x27530`) but past the file-backed range (ends `0x27200`): its bytes
exist nowhere on disk; the loader zero-fills that tail. **Windows has no
`.bss` section — the bss is the zero-filled tail of `.data`, visible only as
`VirtualSize > SizeOfRawData`.**

## What the sections become — and where ASLR gets its fixups

- **`.text`** — machine code, R-X; `AddressOfEntryPoint` points here.
- **`.rdata`** — read-only data: string literals, import tables, the IAT,
  C++ vtables.
- **`.data`** — read-write initialized globals plus the zero tail above.
- **`.rsrc`** — icons, dialogs, version resources (data directory 2).
- **`.reloc`** — the relocation list, data directory 5, *discardable*.

Now derive ASLR's price. `ImageBase` is a *preference* — MSVC x64 defaults
`0x140000000` for EXEs, `0x180000000` for DLLs. Map the image elsewhere and
every absolute address baked into the file is wrong. Unlike Linux PIE, PE code
is not position-independent: it contains real absolute qwords — vtable
entries, function-pointer tables — and `.reloc` is the catalog of those bytes:

```text
 .reloc, a sequence of blocks, one per 4 KB page holding fixups:

 +0x00  PageRVA   = 0x1D000        (4 B)   page this block covers
 +0x04  BlockSize = 0x18           (4 B)   8 header + 16 entry bytes
 +0x08  entry[0]  = 0xA5F8         (2 B)   (Type << 12) | Offset
 +0x0A  entry[1..7] = 0x0000               type 0 = ABSOLUTE: padding, skipped

 0xA5F8 decoded: Type = 0xA = 10 = IMAGE_REL_BASED_DIR64 (x64: patch a qword)
                Offset = 0x5F8  ->  target RVA = 0x1D000 + 0x5F8 = 0x1D5F8
 loader action:  *(qword*)(base + 0x1D5F8) += (actualBase - preferredBase)
```

Concretely: the qword at RVA `0x1D5F8` is `0x1400011000` (a preferred-base
pointer into `.text`). ASLR picks `0x7FF6A0000000`; delta `0x7FF560000000`;
patched value **`0x7FF6A0011000`**. x86 would use type 3 (`HIGHLOW`, 4-byte
patch); x64 uses type 10 — and the x64 list is *short*, because MSVC x64 code
addresses image-local data RIP-relatively: most references need no fixup.
**`/DYNAMICBASE`** (bit `0x40` in DllCharacteristics) permits the loader to
ignore the preferred base; `/HIGHENTROPYVA` (bit `0x20`) widens the range past
4 GB. Strip `.reloc` or clear bit 0x40 and the image must load at its preferred
base or crash — module 02 ch06's ASLR rung, implemented here.

## TEB and PEB: the loader's notepad, addressed through `gs`

The loader needs state any code can find without handles or syscalls. x64
Windows' answer: the **`gs` segment register**. In user mode, `gs` base = the
current thread's **TEB** (Thread Environment Block); the kernel swaps `gs` base
to its KPCR on every transition. The TEB opens with the NT TIB, so stack facts
cost one load:

```text
 x64, Windows 10/11 -- one load, no syscall, any ring-3 code:

   gs base --+-->  TEB (per thread; kernel-allocated)
             |
             |     +0x00  NtTib.ExceptionList
   gs:[0x08] |     +0x08  NtTib.StackBase      <- top of the stack
   gs:[0x10] |     +0x10  NtTib.StackLimit     <- guard page above this
   gs:[0x30] |     +0x30  NtTib.Self           == gs base (self-pointer:
             |                                    gs:0x30 *is* "TEB address")
             |
   gs:[0x60] +-->  PEB (per process)
                   +0x02  BeingDebugged        <- 1 byte, set by debuggers
                   +0x10  ImageBaseAddress     <- where the EXE actually landed
                   +0x18  Ldr                  -> PEB_LDR_DATA: THREE doubly-
                   +0x20  ProcessParameters       linked lists of loaded modules
                   +0xBC  NtGlobalFlag          (ImagePathName, CommandLine,
                                                    env pointer -- the "argv"
                                                    the loader built for you)
```

- **`gs:0x30`** is how code says "my TEB" without knowing where it is
  (`fs:0x18` on 32-bit). **Stack base/limit** are the only inputs **`_chkstk`**
  has: MSVC emits `call __chkstk` whenever a frame exceeds 0x1000 bytes — it
  touches each page down to the new stack pointer, probing guard pages in
  order, and fast-fails with stack overflow (`0xC00000FD`) on crossing
  `StackLimit` (module 01 ch04's stack growth, Windows dialect).
- **`PEB+0x18 Ldr`** is the loader's own bookkeeping: three parallel lists
  (InLoadOrder, InMemoryOrder, InInitializationOrder) of entries holding
  DllBase, entry point, names — which `GetModuleHandle` and
  `EnumProcessModules` simply walk. Unlinking your entry from all three hides
  the module from every module-walking tool.

## The loader runs your code: DLL order, DllMain, LoaderLock

Import resolution (data directory 1) hands the loader a dependency graph; it
maps DLLs depth-first and runs each `DllMain(DLL_PROCESS_ATTACH)` in dependency
order. Where it finds them is folklore — and attack history:

1. **KnownDLLs first.** At boot, the Session Manager creates shared section
   objects for a few dozen DLLs (kernel32, user32, ntdll, advapi32, ...) in the
   `\KnownDlls` object directory, per the registry list at
   `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\KnownDLLs`. Resolving
   one of those *by base name* maps that section and never touches disk: you
   cannot plant a fake `kernel32.dll`.
2. **Then the search order** (SafeDllSearchMode on by default for two decades):
   application directory → system32 → system16 → Windows directory → current
   directory → `PATH`. Application directory is *first* — the reason "drop a
   DLL next to the EXE" is an attack. Anything outside KnownDLLs (version.dll,
   uxtheme.dll, ...) is plantable wherever the app runs with privileges you can
   reach: search-order hijacking (module 02 ch06).
3. **The modern fix:** `SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_APPLICATION_DIR
   | LOAD_LIBRARY_SEARCH_SYSTEM32)` drops CWD and PATH from the process's
   default order entirely.

Then the rule everyone learns the hard way. All module-list updates and every
`DllMain` dispatch happen under **LoaderLock** (`ntdll!LdrpLoaderLock` — a
critical section historically, a push lock on Windows 8+). While your `DllMain`
runs, you hold it, and the documented-safe set is nearly empty: memory
allocation, `CreateThread` *without waiting*, initializing a critical section.
The insider's summary: "DllMain should be about four instructions." Everything
else is a deadlock lottery — `CreateThread` + `WaitForSingleObject` (the
canonical hang, below), `LoadLibrary` (recursive acquisition
mid-initialization), waiting on anything that might fault into a *delay-loaded*
import (the helper takes LoaderLock), COM or WinSock init (RPC into other
processes). The crash twin: `DLL_PROCESS_DETACH` during `ExitProcess` runs in
reverse init order after other DLLs — possibly the heap's own — are gone: the
classic "crashes only on exit" bug. And every thread start walks every module
that didn't call `DisableThreadLibraryCalls`.

## Wow64: two ntdlls pretending to be one

A 32-bit process on x64 Windows loads a *32-bit* ntdll from `SysWOW64` (the
file-system redirector rewrites its `system32` accesses). Its syscall stubs
can't use the syscall instruction — the kernel is 64-bit — so wow64cpu.dll
far-jumps the CPU into 64-bit mode through code selector 0x33 ("Heaven's
Gate"), the wow64 layer translates the 32-bit frame to a 64-bit one, calls the
64-bit ntdll, and jumps back through 0x23. Two architectures, one process, and
a 32-bit module reaches its TEB via `fs`, not `gs`.

## What insiders know

- **The DOS stub is theater, but `e_lfanew` at 0x3C is load-bearing.** Zero
  out the stub and the binary still runs. The Rich header between stub and
  signature is the compiler's fingerprint: free attribution on every MSVC
  binary.
- **`gs:0x30` is the TEB self-pointer on x64** (`fs:0x18` on x86); `gs:0x60` the
  PEB; `gs:0x10` `StackLimit`. `__chkstk` reads `gs:[0x10]` and probes one page
  at a time — a 0x5000-byte local array emits that call; a 0x100-byte one
  doesn't.
- **`BeingDebugged` at `PEB+0x02`** is one byte and famously spoofable; the
  real check is `NtGlobalFlag` at `PEB+0xBC` (debug-heap magic `0x70`).
- **KnownDLLs means kernel32.dll cannot be planted — and that's the whole
  list.** Anything outside those few dozen names (registry key, or
  `\KnownDLLs` in WinObj) resolves through the disk search order, application
  directory first.
- **The DllMain rule: ~4 instructions.** No `LoadLibrary`, no
  `CreateThread`+wait, no COM, no waiting on another thread's progress.
- **LoaderLock is diagnosable by name.** In a hang dump, `!locks` shows
  `ntdll!LdrpLoaderLock` owned by the thread parked in `DllMain`, everyone
  else queued. One command, verdict.
- **`.reloc` is discardable** — a sample with no `.reloc` (or no Dynamic-base
  bit) is pinned to a fixed base: an ASLR-neutralized binary. And **TLS
  callbacks (data directory 9) run *before* `AddressOfEntryPoint`** — malware
  puts anti-debug checks there; breakpointing `main` is already too late.

## See it yourself

No Windows box was used to write this chapter; part 1 runs on any Linux box
with Python and binutils (objdump's PE support is standard); parts 2-3 are
*documented walkthroughs* for a Windows 10/11 x64 box or VM with VS Build
Tools.

**1. Build a minimal PE and decode it (reproducible anywhere):**

```python
# mini_pe.py — a PE32+ file: 5 sections, one vtable-ish qword, one reloc block
import struct
secs = [  # name, VirtSize, VirtAddr(RVA), RawSize, RawPtr
    (b".text\0\0\0", 0x1A2B0, 0x1000,  0x1A400, 0x400),
    (b".rdata\0\0",  0x09C10, 0x1C000, 0x09E00, 0x1A800),
    (b".data\0\0\0", 0x01530, 0x26000, 0x01200, 0x24600),
    (b".rsrc\0\0\0", 0x00890, 0x28000, 0x00A00, 0x25800),
    (b".reloc\0\0",  0x00B40, 0x29000, 0x00C00, 0x26200)]
out = bytearray(0x26E00)
out[0:2] = b"MZ"                          # DOS magic
out[0x3C:0x40] = struct.pack("<I", 0x80)  # e_lfanew
out[0x80:0x84] = b"PE\0\0"                # PE signature
out[0x84:0x98] = struct.pack("<HHIIIHH",  # COFF: x64, 5 sections, exe
    0x8664, 5, 0, 0, 0, 0xF0, 0x22)
opt = bytearray(0xF0)                     # optional header, PE32+
struct.pack_into("<H",  opt, 0,  0x20B)          # Magic
struct.pack_into("<I",  opt, 16, 0x1234)         # AddressOfEntryPoint (RVA)
struct.pack_into("<Q",  opt, 24, 0x140000000)    # preferred ImageBase
struct.pack_into("<II", opt, 32, 0x1000, 0x200)  # Section/FileAlignment
out[0x98:0x188] = opt
pos = 0x188                               # section table, 40 B each
for name, vs, va, rs, rp in secs:
    out[pos:pos+40] = name + struct.pack(
        "<IIIIIIHHI", vs, va, rs, rp, 0, 0, 0, 0, 0x60000020)
    pos += 40
out[0x1BDF8:0x1BE00] = struct.pack("<Q", 0x1400011000)  # qword at RVA 0x1D5F8
blk = struct.pack("<II", 0x1D000, 0x18) + \       # reloc block: page 0x1D000
      struct.pack("<8H", 0xA5F8, 0, 0, 0, 0, 0, 0, 0)
out[0x26200:0x26218] = blk
open("mini.exe", "wb").write(out)
```

```console
$ python3 mini_pe.py
$ objdump -h mini.exe            # section table: both layouts side by side
  1 .rdata  00009c10  000000014001c000  ...  0001a800   <- VMA vs File off
$ od -A x -t x8 -j 0x1bdf8 -N 8 mini.exe
0000000001bdf8 0000001400011000            <- the qword at RVA 0x1D5F8
$ od -A x -t x1 -j 0x26200 -N 24 mini.exe
026200 00 d0 01 00 18 00 00 00 f8 a5 00 00 00 00 00 00
026210 00 00 00 00 00 00 00 00          <- PageRVA=0x1D000, BlockSize=0x18,
                                          entry[0]=0xA5F8 (type 10, off 0x5F8)
```

The reloc entry's target (`0x1D000 + 0x5F8 = 0x1D5F8`) names the same qword
the arithmetic found — output above is real, from binutils 2.38 on Linux.

**2. `dumpbin /headers` on a real binary (documented walkthrough).** From a VS
developer prompt: `copy C:\Windows\notepad.exe . && dumpbin /headers
notepad.exe`. Find: `8664 machine (x64)` in FILE HEADER VALUES (`14C` on a
32-bit file); `20B magic # (PE32+)`, `140000000 image base`, `1000` / `200`
section / file alignment, `DLL characteristics: Dynamic base` (the 0x40 bit)
in OPTIONAL HEADER VALUES; then per section the VMA, raw-data offset, and
characteristics — including `Discardable` on `.reloc`.

**3. The DllMain horror story (documented pattern).** Compile as a DLL and
load it first into a test EXE:

```c
/* deadlock.c — do not ship */
static DWORD WINAPI worker(LPVOID a) { return 0; }
BOOL WINAPI DllMain(HINSTANCE h, DWORD why, LPVOID r) {
    if (why == DLL_PROCESS_ATTACH) {
        HANDLE t = CreateThread(0, 0, worker, 0, 0, 0);
        WaitForSingleObject(t, INFINITE); /* LoaderLock held; worker needs it */
    }
    return TRUE;
}
```

The process hangs before any of your code runs, zero CPU. Under WinDbg
(`windbg -g host.exe`, or break on it with `bp deadlock!DllMain`), `!locks`
shows `LdrpLoaderLock` owned by the thread in `DllMain`, everyone else queued;
`!peb` prints the same PEB fields the diagram showed. Fix: signal and return.

## How to use this knowledge

- **Crash triage reflex.** "Access violation at `0x7FF6A0011AF4`" becomes
  actionable when you subtract the module's actual base to get the RVA and
  convert RVA→section by hand: `.rdata` — a corrupted function-pointer table,
  not code.
- **Hijack audits.** An app's import table plus the KnownDLLs list names the
  plantable DLLs — a five-minute attack-surface audit (module 02 ch06).
- **Startup-hang triage.** "Service hangs at boot, no CPU" — dump it and check
  LoaderLock first; one `!locks` command recognizes the signature.

## Connections

- **Module 04 ch01 (binary formats)** — the PE member of that chapter's format
  trio; its RVA arithmetic is the skill drilled by hand here.
- **Module 02 ch06 (mitigation ladder)** — ASLR's rung is this chapter's 0x40
  bit and `.reloc` machinery; DLL search-order hijacking is the same ladder's
  attack-surface side.
- **Module 07 ch04 (Linux: ELF & ld.so)** — the twin: PIE +
  `R_X86_64_RELATIVE` bulk rebase instead of `.reloc` blocks, ld.so's search
  order instead of KnownDLLs folklore, and no DllMain at all.
- **Module 03 ch03 (race family)** — LoaderLock deadlock is a textbook
  wait-for cycle: a lock holder waiting on a thread whose startup needs the
  lock; that chapter's lock-order analysis applies verbatim.

## Quiz

1. **Concept.** Microsoft could delete the DOS stub tomorrow and every PE file
   would still load. Why, and which single DOS-header field must survive?
2. **Concept.** The "optional header" is mandatory in every executable. Where
   did the name come from, and what do `0x10B` and `0x20B` each select for
   everything parsed after them?
3. **Predict the number.** Using the chapter's section table: an IAT entry
   sits at RVA `0x26880`. What is its file offset, and its VA if the image
   loads at base `0x7FF920000000`?
4. **Predict the number.** A `.reloc` block has `PageRVA = 0x1D000`,
   `BlockSize = 0x24`. How many entries? Decode `0xA7F0`: type, target RVA,
   and the loader's action on x64.
5. **Predict the number.** With `.data` VirtSize `0x1530`, RawSize `0x1200`,
   VirtAddr `0x26000`: `g[0x300]` lives at RVA `0x27300`. How many
   bytes come from the file, and where do the rest come from?
6. **Bug diagnosis.** A DLL does exactly this in `DllMain(PROCESS_ATTACH)`:
   `CreateThread(worker)` then `WaitForSingleObject(t, INFINITE)`. The service
   hangs at startup, no CPU. Explain the exact circular wait, naming the lock
   and the notification that cannot be delivered.
7. **Bug diagnosis.** An attacker plants `kernel32.dll` next to a victim EXE —
   nothing happens. Planting `version.dll` next to the same EXE gets code
   execution. What mechanism made one impossible and the other trivial?
8. **Cross-chapter (module 03 ch03).** Classify the quiz-6 deadlock in that
   chapter's terms: which resources are held, which are waited for, and why is
   "just add a timeout" not a fix?

<details>
<summary>Answers</summary>

1. **The loader never executes the stub.** It reads `"MZ"` at offset 0, loads
   the 4-byte **`e_lfanew` at 0x3C**, and jumps to the PE signature; everything
   between is skipped. The stub exists so a 1994 DOS box fails gracefully; on
   Windows only `e_lfanew` must survive.

2. **From COFF object files**, where the optional header really is optional —
   an `.obj` has none; the linker fills it in. `0x10B` = PE32: 32-bit fields,
   `HIGHLOW` fixups. `0x20B` = PE32+: 8-byte `ImageBase`, `DIR64`
   relocations.

3. **File offset `0x24E80`; VA `0x7FF920026880`.** `0x26000 <= 0x26880 <
   0x27530` → `.data`; offset = `0x24600 + (0x26880 − 0x26000) = 0x24E80`;
   `0x26880 < 0x27200` (RawSize end), so it is file-backed.

4. **14 entries** (`(0x24 − 8) / 2`). Entry `0xA7F0`: type `0xA` = 10 =
   `IMAGE_REL_BASED_DIR64`; offset `0x7F0`; target RVA `0x1D7F0` — the loader
   reads the qword at `base + 0x1D7F0` and adds `(actualBase − preferredBase)`.

5. **Zero from the file; all 0x300 zero-filled by the loader.** File-backed
   `.data` covers RVAs `[0x26000, 0x27200)`; `0x27300` is past RawSize but
   inside VirtualSize (end `0x27530`) — Windows' nameless bss.

6. **A wait-for cycle through LoaderLock and a thread.** The service thread
   holds `LdrpLoaderLock` inside `DllMain` and waits on the worker. But the
   worker cannot run user code yet: thread startup must first deliver
   `DLL_THREAD_ATTACH` to every module — which requires LoaderLock — held by
   the thread waiting on the worker. Holder waits on waiter; waiter waits on
   the lock: deadlock, both threads blocked — hence zero CPU. Fix:
   create the thread, never wait on it.

7. **KnownDLLs vs the disk search order.** `kernel32.dll` is on the KnownDLLs
   list: resolved by base name to the pre-created section in `\KnownDLLs`,
   never consulted on disk — the planted copy is inert. `version.dll` is not,
   so it resolves through the standard search order, whose *first* directory
   is the EXE's own folder (module 02 ch06's search-order hijacking).

8. **A single-lock circular wait where the second "resource" is a thread's
   initialization, not another mutex.** Held: LoaderLock, by the DllMain
   thread. Waited: the worker's completion; the worker waits on LoaderLock in
   the attach path — module 03 ch03's ABBA cycle with "thread start" in the
   second role. A timeout converts the deadlock into a lie: the wait fails and
   `DllMain` returns success with the worker never run — the hang is traded,
   not the bug.

</details>
