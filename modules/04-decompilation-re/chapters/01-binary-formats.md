# Chapter 01 — Binary formats from bytes up

> **The 30-second version.** An executable is a structured file with **two
> audiences**. The loader reads *program headers*: "map these ranges at these
> addresses, with these permissions, then jump." Linkers and analysis tools read
> *section headers* — named innards (`.text`, `.data`, `.symtab`). The loader
> never needs the names; the linker never mmaps. ELF, PE, and
> Mach-O are three dialects of one idea: magic bytes announce the format; a memory
> map serves the loader; a table of contents, the tools.

## The first sixteen bytes decide everything

A loader handed an arbitrary file must decide, cheaply, whether and how to run it.
That forces a fixed **signature** at a fixed offset: `7f 45 4c 46` — `\x7fELF`.
The next 12 bytes (`e_ident`) fix *how to parse everything after*:

```text
offset:   0    1    2    3    4    5    6    7         8 .. 15
        +----+----+----+----+----+----+----+----+----------------------+
bytes:  | 7f | 45 | 4c | 46 | 02 | 01 | 01 | 00 | 00 00 00 00 00 00 00 |
        +----+----+----+----+----+----+----+----+----------------------+
          \x7f   E    L    F   class data ver  OSABI     padding
                                  64    LE  v1  SysV
```

- **Byte 4, class** (`02` = ELF64): fixes every later field's size — 8-byte
  addresses, 56-byte program headers, 64-byte section headers.
- **Byte 5, data** (`01` = little-endian): byte order of every multi-byte integer.
- **Byte 7, OSABI** — `00` = System V. Linux toolchains emit 0 and the kernel
  ignores it; FreeBSD "brands" foreign binaries here.

Then the header proper: `e_type` (2 = EXEC, fixed base; 3 = DYN, position-independent
— modern Linux builds), `e_machine` (0x3e = x86-64), and **`e_entry`** — the virtual
address the kernel jumps to once the image is mapped. Here `e_entry = 0x1050` — *not*
`main` but `_start`, the libc bootstrap that calls `__libc_start_main(main, argc,
argv, ...)` (chapter 03 makes this a recipe for finding `main`). Two offsets finish:
`e_phoff` (64, right after this 64-byte header) and `e_shoff` (15104, near the
file's end) — the layout encodes who matters more to the loader.

## Two tables, one file

Two programs consume this file:

- The **loader** (kernel + `ld.so`) thinks in pages: "map `[vaddr, vaddr+memsz)` from
  file bytes `[off, off+sz)`, with these permissions." It wants few, coarse,
  page-aligned ranges — **program headers** (segments).
- The **linker** — and strip, gdb, readelf, Ghidra — thinks in *named* chunks: resolve
  against `.symtab`, relocate `.data`/`.got`, set `.debug_info` aside. It wants many,
  small, labeled ranges — **section headers**.

Same bytes, two tables — the load-bearing insight of this module:

```text
                    ONE FILE on disk (PIE ELF64, gcc 14.2.0, x86-64)
+--------------------------------------------------------------------------+
| 0x0000: ELF header (e_phoff=64, e_shoff=15104, e_entry=0x1050)           |
| 0x0040: PROGRAM HEADERS  <- loader's view: what to mmap                  |
|         ...file bytes...                                                 |
| 0x3b00: SECTION HEADERS <- linker's view: named innards                  |
+--------------------------------------------------------------------------+

   PROGRAM HEADERS (loader)                SECTION HEADERS (linker/tools)
   "map this, these perms"                 "these are the named parts"

   PT_INTERP  "/lib64/ld-linux-x86-64.so.2"   .interp
   PT_LOAD #1  R    vaddr 0x0000  filesz 0x628
                                       <- .gnu.hash .dynsym .dynstr .rela.*
   PT_LOAD #2  R E  vaddr 0x1000  filesz 0x19d
                                       <- .init .plt .text .fini
   PT_LOAD #3  R    vaddr 0x2000  filesz 0x12c
                                       <- .rodata .eh_frame
   PT_LOAD #4  RW   vaddr 0x3dd0  filesz 0x24c  memsz 0x370
                                       <- .init_array .dynamic .got
                                          .got.plt .data .bss
          ^-- memsz > filesz = .bss: zero file bytes; the loader
             zero-fills the tail (it never touches disk)
   PT_DYNAMIC      vaddr 0x3de0       <- .dynamic (ld.so's to-do list)

   .symtab .strtab .debug_* .comment   <- no segment: never loaded,
                                          linker/tools only
```

Three consequences:

**Segments are permission boundaries.** Four `PT_LOAD`s = four `mmap` calls = the
four `/proc/PID/maps` lines from module 01 ch03 (`r--p`, `r-xp`, `r--p`, `rw-p`). A
segment holds many sections, but sections with different permissions cannot share one
— that is why `.text` (R E) and `.rodata` (R) sit in different `PT_LOAD`s, aligned to
0x1000 (note `0x2dd0 → 0x3dd0`: offset and address agree modulo the page size).

**`.bss` is a lie the file never tells.** `.bss` has type `SHT_NOBITS` and appears in
`PT_LOAD #4` as `memsz > filesz`: 288 bytes of `zeroed[64]` occupy *zero file bytes* —
the loader maps the tail anonymous-and-zero. BSS is free on disk, and its first write
is a page fault (module 01 ch03).

**Section headers are optional — really.** The kernel and `ld.so` read only the ELF
header and program headers. Delete the section table (`sstrip` does exactly that) and
the binary still runs — it just mystifies readelf. A "broken" section table in a
file that executes fine is not corruption; it is a hint.

## Symbols: the names that almost survive

Every name in a binary lives in a symbol table as fixed-size records. ELF64's
`Elf64_Sym` is 24 bytes:

```text
+---------+---------+---------+---------+---------------+---------------+
| st_name | st_info | st_other| st_shndx|   st_value    |    st_size    |
|  4 B    |  1 B    |  1 B    |  2 B    |     8 B       |     8 B       |
+---------+---------+---------+---------+---------------+---------------+
  offset into    bind<<4     visibility owning    address (0 for UND)
  .strtab       | type                          section
              LOCAL/GLOBAL/WEAK, OBJECT/FUNC/SECTION
```

From the real build: `counter` (LOCAL, OBJECT, `0x4018`, `.data`), `main`
(GLOBAL, FUNC, `.text`), `printf` (GLOBAL, FUNC, `UND`, value 0 — defined by
libc). Binding says who may see it; type says code or data; value is the address
(0 for imports). `WEAK` (2) — "override me if a strong
definition appears" — is what makes interposition possible.

There are *two* symbol tables:

- **`.dynsym`** — imports and exports, the minimum dynamic linking needs; the loader
  reads it at startup, so it can never be removed. Ours has two entries: `printf` and
  `__libc_start_main`.
- **`.symtab`** — everything the linker knew: all globals, all `static` symbols, plus
  `.strtab` and the DWARF `.debug_*` sections.

`strip` deletes `.symtab`, `.strtab`, `.debug_*` — *nothing* the loader maps, so
*nothing* that runs. Real numbers (binutils 2.44, this build): 17,472 → 14,472 bytes
(−17%), 37 sections → 29. But `strings` still shows every string literal (`.rodata`
stays), and `.dynsym` still names `printf`. Stripped means "local names and debug info
gone" — never "anonymous".

## PE: a DOS fossil with a Windows engine

Windows executables still start with a 16-bit 1981 DOS header: bytes `4d 5a` — "MZ",
Mark Zbikowski's initials. It survives because Windows NT shipped in 1993 into a
DOS-and-Windows-3.x world: run from DOS, it should print the stub's message ("This
program cannot be run in DOS mode") rather than execute garbage. The MZ signature
doubles as the executable marker every tool checks first; `e_lfanew` at offset
`0x3C` is the escape hatch to the real header.

At `e_lfanew` sits `PE\0\0`, then a 20-byte COFF header (`Machine` `0x8664` =
AMD64), then the "optional" header — mandatory for images. PE32+ magic `0x20B`;
fields that matter: `AddressOfEntryPoint` (an **RVA**, not a file offset),
`ImageBase` (`0x140000000` typical for x64), `SectionAlignment = 0x1000` (pages),
`FileAlignment = 0x200` (512 — sector heritage), and the 16-entry **DataDirectory**
array (8 bytes each: RVA + size) indexing exports, imports, the IAT. Sections
follow in a 40-byte-per-entry table like ELF's — but only *one*: loader and
linker share the same rows.

An **RVA** (relative virtual address) is "address minus `ImageBase`" — where the byte
sits *after* the loader maps the module (which may rebase it). RVA-to-file-offset is
the one piece of PE arithmetic you must do by hand:

```text
section table (typical MSVC x64 build):
  name    VirtAddr  VirtSize  RawSize  RawPtr
  .text   0x1000    0x5A48    0x5C00   0x400
  .rdata  0x7000    0x2A80    0x2C00   0x6400
  .data   0xA000    0x1530    0x1600   0x9000

RVA 0x1234  →  falls in .text (0x1000 <= 0x1234 < 0x1000+0x5A48)
file offset = RawPtr + (RVA - VirtAddr)
            = 0x400 + (0x1234 - 0x1000) = 0x400 + 0x234 = 0x634
```

(If `VirtSize > RawSize`, the tail has no file backing — the loader zero-fills it:
PE's `.bss`.) The lab proves this math on a real file.

**Imports (IAT)**: one descriptor per DLL, each with two parallel thunk arrays — one
listing function-name RVAs, one being the **Import Address Table**. On disk both hold
names; at load time the loader overwrites the IAT slots with real function addresses,
and every call goes through those slots — which is why packed malware must
reconstruct a fake IAT (chapter 06). **Exports (EAT)**: the mirror image — a names
array, an ordinals array, a function-RVA array; look up by name, index by ordinal.
Kernel32's exports are mostly *forwarders* into ntdll.

## Mach-O: the same physics in Apple's dialect

macOS files start with `0xFEEDFACF` (`MH_MAGIC_64`) — but in the file the bytes read
`cf fa ed fe`, little-endian — read the bytes, not the constant. The header gives
`cputype`, `filetype` (2 = executable, 6 = dylib), `ncmds`, `sizeofcmds` — then a
*list of load commands* instead of ELF's two tables. There is only one view: an
`LC_SEGMENT_64` carries its mapping info (`vmaddr`, `vmsize`, `fileoff`, `filesize`)
*and* its sections inline — the linker's names live inside the loader's structures.
Three segments cover a typical executable:

```text
__TEXT      R X   code, stubs, string literals (__TEXT,__text / __cstring)
__DATA      R W   globals (__DATA,__data), zero-fill (__DATA,__bss, filesize 0),
                  pointer fixups (__DATA_CONST on modern builds)
__LINKEDIT  R     the junk drawer: symbol table, string table, code signature,
                  rebase/bind opcodes — all addressed by file offset
```

`__LINKEDIT` is the insight: Mach-O parks *everything* symbolic in one never-executed
segment, addressed by offsets named in other load commands (`LC_SYMTAB` etc.). The
entry point is a load command too — `LC_MAIN` carries `entryoff`, a **file offset**,
where ELF's `e_entry` is a virtual address.

**Universal ("fat") binaries** glue several Mach-Os into one file: a big-endian
header with magic `0xCAFEBABE` and a count, then 20-byte `fat_arch` records
(`cputype`, `cpusubtype`, `offset`, `size`, `align`) — one per architecture, and
big-endian *regardless of target*. dyld scans for the host's `cputype`, or picks the
x86_64 slice for Rosetta on Apple Silicon.

## What insiders know

- **The `e_ident` bytes insiders actually check**: byte 4 (class) and byte 5
  (endianness) decide every subsequent parse. Byte 7 (OSABI) is almost always `00` on
  Linux builds because the kernel ignores it; FreeBSD "brands" binaries there
  (9 = FreeBSD) and rejects mismatches.
- **`strip` arithmetic** (binutils 2.44, this build): 17,472 → 14,472 bytes, 37 → 29
  sections, `size` output byte-identical. It removes `.symtab`/`.strtab`/`.debug_*`
  only — it *cannot* remove anything the loader maps: `.dynsym`, `.rela`, `.got`, or
  any `.rodata` string. Stripped is not anonymous.
- **A running binary does not need its section headers.** `sstrip`-style tools zero
  `e_shoff` and the file still executes. "readelf complains, program runs" is the
  classic packed-binary signature, not corruption.
- **Why the DOS header survives** in PE: 1993 deployment grace — a PE run from DOS
  prints "This program cannot be run in DOS mode" instead of executing garbage, and
  `e_lfanew` (offset `0x3C`) lets MZ and PE coexist. `FileAlignment = 0x200` leaves
  zero padding around raw section data — a standard malware stash.
- **`readelf`'s "Section to Segment mapping" is computed, not stored** — derived by
  intersecting each section's `[addr, addr+size)` with each segment's; the file itself
  has no arrows.
- **Magic numbers worth memorizing**: `7f 45 4c 46` (ELF), `4d 5a` "MZ" + `PE\0\0`,
  `cf fa ed fe` (Mach-O 64), `ca fe ba be` (fat header, big-endian — shared with Java
  class files; `file` disambiguates).

## See it yourself

All output is real (binutils 2.44, gcc 14.2.0, x86-64, Debian). Build the specimen
— it touches `.data`, `.bss`, `.rodata` on purpose:

```c
static int counter = 7;         /* .data: initialized, writable   */
static int zeroed[64];          /* .bss:  no file bytes at all    */
static const char msg[] = "hi"; /* .rodata: read-only             */
int helper(int x) { return x + counter; }
int main(void) { printf("%s\n", msg, helper(3)); zeroed[0] = 1; return 0; }
```

```console
$ gcc -O0 -g tiny.c -o tiny
$ xxd tiny | head -1     # (no xxd? od -A x -t x1z tiny | head -1)
00000000: 7f45 4c46 0201 0100 0000 0000 0000 0000  .ELF............
```

The contract, by hand: `7f 45 4c 46`, class `02`, data `01`, OSABI `00`. `readelf -h`:

```console
$ readelf -h tiny
  Class:                             ELF64
  Data:                              2's complement, little endian
  Type:                              DYN (PIE — position-independent)
  Entry point address:               0x1050
  Start of program headers:          64 (bytes into file)
  Start of section headers:          15104 (bytes into file)
```

`objdump -f` — the one-line triage view:

```console
$ objdump -f tiny
tiny:     file format elf64-x86-64
architecture: i386:x86-64, flags 0x00000150: HAS_SYMS, DYNAMIC, D_PAGED
start address 0x0000000000001050
```

The two tables — `readelf -lW` prints program headers plus the derived mapping:

```console
$ readelf -lW tiny
  Type           Offset    VirtAddr   FileSiz  MemSiz   Flg Align
  INTERP         0x000394  0x0000394  0x00001c 0x00001c R   0x1
      [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]
  LOAD           0x000000  0x0000000  0x000628 0x000628 R   0x1000
  LOAD           0x001000  0x0001000  0x00019d 0x00019d R E 0x1000
  LOAD           0x002000  0x0002000  0x00012c 0x00012c R   0x1000
  LOAD           0x002dd0  0x0003dd0  0x00024c 0x000370 RW  0x1000
  GNU_RELRO      0x002dd0  0x0003dd0  0x000230 0x000230 R   0x1
 Section to Segment mapping:
  Segment Sections...
   03     .init .plt .text .fini
   05     .init_array .fini_array .dynamic .got .got.plt .data .bss
```

The RW `LOAD`'s `MemSiz` (0x370) exceeds `FileSiz` (0x24c) by 0x124 — that tail is
`.bss` (288 bytes) plus padding, zero-filled at load; `GNU_RELRO` overlaps the RW
segment's front (chapter 02). `readelf -SW` is the linker's view:

```console
$ readelf -SW tiny
  [14] .text    PROGBITS  0000000000001050 001050 000142 AX
  [16] .rodata  PROGBITS  0000000000002000 002000 00000c  A
  [25] .data    PROGBITS  0000000000004008 003008 000014 WA
  [26] .bss     NOBITS    0000000000004020 00301c 000120 WA 32
  [34] .symtab  SYMTAB    0000000000000000 0033e0 0003c0
$ readelf -sW tiny | rg 'counter|main|printf'
    12: 0000000000004018  4 OBJECT  LOCAL  DEFAULT  25 counter
    34: 000000000000114d  69 FUNC   GLOBAL  DEFAULT  14 main
     3: 0000000000000000  0 FUNC   GLOBAL  DEFAULT  UND printf@GLIBC_2.2.5
```

`counter` sits at `0x4018` in section 25 (`.data`), `printf` is undefined with a
version tag. Strip and compare:

```console
$ cp tiny tiny.stripped && strip tiny.stripped
$ ls -l tiny tiny.stripped
-rwxr-xr-x 1 z z 17472 tiny
-rwxr-xr-x 1 z z 14472 tiny.stripped          # -3000 bytes (-17%)
$ size tiny tiny.stripped        # identical: strip touched nothing mapped
$ readelf -h tiny.stripped | rg 'section headers'
  Number of section headers:         29         # was 37
```

Both still run — `.dynsym` keeps `printf` alive. Finally, PE by hand: the builder
writes a minimal PE32+ (section table as above) and drops a marker at the file
bytes for RVA `0x1234`:

```python
# make_mini_pe.py — 36 KB skeleton PE64, section table as above
import struct
sections = [  # name, vsize, vaddr, rawsize, rawptr
    (b".text\0\0\0", 0x5A48, 0x1000, 0x5C00, 0x400),
    (b".rdata\0\0",  0x2A80, 0x7000, 0x2C00, 0x6400),
    (b".data\0\0\0", 0x1530, 0xA000, 0x1600, 0x9000)]
out = bytearray(0x9000)
out[0:2] = b"MZ"                                  # DOS magic
out[0x3C:0x40] = struct.pack("<I", 0x80)          # e_lfanew
out[0x80:0x84] = b"PE\0\0"                        # PE signature
out[0x84:0x98] = struct.pack("<HHIIIHH", 0x8664, 3, 0, 0, 0, 0xF0, 0x22)  # COFF
opt = bytearray(0xF0)                             # optional hdr, PE32+
struct.pack_into("<H",  opt, 0,  0x20B)           # magic
struct.pack_into("<I",  opt, 16, 0x1234)          # AddressOfEntryPoint (RVA!)
struct.pack_into("<Q",  opt, 24, 0x140000000)     # ImageBase
struct.pack_into("<II", opt, 32, 0x1000, 0x200)   # Section/FileAlignment
out[0x98:0x188] = opt
pos = 0x188                                       # section table
for name, vs, va, rs, rp in sections:
    out[pos:pos+40] = name + struct.pack("<IIIIIIHHI", vs, va, rs, rp, 0,0,0,0,0)
    pos += 40
out[0x634:0x644] = b"ENTRYPOINT_BYTES"            # bytes for RVA 0x1234
open("mini.exe", "wb").write(out)
```

```console
$ python3 make_mini_pe.py
$ od -A x -t x1z mini.exe -j 0x630 -N 16    # jump straight to our computed offset
000630 00 00 00 00 45 4e 54 52 59 50 4f 49 4e 54 5f 42  >....ENTRYPOINT_B>
```

`0x400 + (0x1234 − 0x1000) = 0x634` — the bytes sit exactly where the arithmetic
said. The loader maps `.text` at `ImageBase + 0x1000` and jumps to
`ImageBase + 0x1234`.

## How to use this knowledge

- **Crash triage with no symbols.** A stripped-binary crash hands you a bare
  address. You cannot `addr2line` it, but `readelf -SW` minus the PIE base at
  least names the section — "in `.plt`" (lazy-binding stub, chapter 02) vs "in
  `.text`" is a real fork in the investigation.
- **Size engineering.** Initialized globals cost file bytes (`.data`);
  zero-initialized cost none (`.bss`, `MemSiz` only). Dropping `= {0}` from a big
  buffer is free bytes; `strip` buys ~17% when debug info is dispensable.
- **Malware first sixty seconds.** Magic bytes tell you the family; an entry point
  outside the executable segment, a deleted section table, or imports of 2 DLLs and
  4 functions all say "packed" before any disassembly (chapter 06).

## Connections

- **Module 01 ch03 (virtual memory)**: each `PT_LOAD` is one `mmap` — the four
  `/proc/self/maps` permission lines there were this segment table all along; `.bss`
  zero-fill was demand paging.
- **Module 01 ch06 (object layout)**: vtables live in `.data.rel.ro`/`.rodata`;
  chapter 04 finds them without symbols via this reasoning.
- **Module 04 ch02-06**: ch02 executes the loader's half (`PT_DYNAMIC`, `.got`,
  relocations, RELRO); ch03 locates `main` from `e_entry`; ch04 reads vtables out of
  the sections mapped here; ch05's decompilers parse these three formats; ch06 breaks
  and rebuilds them (IAT reconstruction).
- **Module 05 ch06 (APK & OTA)**: Android's "binary formats" are ZIP + DEX — same
  discipline, different magic (`dex\n035\0`).
- **Module 06 ch04 (dyld & the shared cache)**: `__LINKEDIT` at planet scale —
  thousands of Mach-Os pre-linked into one COW-mapped file.
- **Module 07 ch02/04**: the Windows and Linux loader chapters, built directly on the
  PE and ELF structures decoded here.

## Quiz

1. **Concept.** Why can one file carry two tables of contents — program headers and
   section headers — without contradicting each other? Who reads which, and what breaks
   if each is deleted?
2. **Predict the number.** An RW `PT_LOAD` has `p_vaddr = 0x3dd0`, `p_filesz = 0x24c`,
   `p_memsz = 0x370`. How many bytes does the loader supply that exist nowhere in the
   file, and from where?
3. **Predict the number.** With the lab's section table (`.rdata` VirtAddr 0x7000,
   RawPtr 0x6400) and `ImageBase = 0x140000000`: an imported function's thunk sits at
   RVA `0x7A34`. What file offset, and what load-time VA?
4. **Concept.** A fully stripped Linux binary still calls `printf` correctly, and
   `strings` on it still shows every format string. Explain both facts in terms of
   what `strip` may and may not remove.
5. **Bug diagnosis.** `readelf -S mystery.bin` reports a broken section table, yet
   `./mystery.bin` runs perfectly. Is the file corrupt? Why does it still execute?
6. **Bug diagnosis.** A program fails with `cannot execute binary file: Exec format
   error`. `file` says `ELF 32-bit LSB executable, ARM, EABI5`; the machine is x86-64.
   Which two header fields did the kernel read before rejecting it, and which
   `e_ident` byte had to be right first?
7. **Bug diagnosis (PE).** An analyst dumps a running process's module out of memory
   to disk; PE tools then fail on its section table. What did the dump destroy about
   the RVA→file-offset relationship?
8. **Cross-chapter (module 01 ch03).** That chapter's `/proc/self/maps` showed an
   `r--p` line *between* the `r-xp` and `rw-p` lines the four `PT_LOAD`s here predict.
   Which program-header feature accounts for it, and which chapter of this module
   explains it?

<details>
<summary>Answers</summary>

1. **Two views of one byte layout, with disjoint readers.** The kernel and `ld.so`
   read program headers, never section headers; linkers and tools read section
   headers. Delete the section table: still runs, but tool-hostile. Delete the
   program headers: nothing can load the file. No contradiction is possible — a
   section's `[sh_addr, sh_addr+sh_size)` either intersects a segment (loaded) or does
   not (linker-only, like `.symtab`).
2. **0x124 = 292 bytes** (`p_memsz − p_filesz`), mapped as anonymous zero pages and
   never read from disk. Here that region is `.bss` (288 bytes, the `zeroed[64]`
   array) plus padding; its first write is a demand-zero page fault
   (module 01 ch03).
3. **Offset 0x6E34; VA 0x140007A34.** `0x7000 <= 0x7A34 < 0x9A80` puts it in
   `.rdata`: `0x6400 + (0x7A34 − 0x7000) = 0x6E34`. At the preferred `ImageBase`,
   VA = `0x140000000 + 0x7A34` (ASLR may rebase; the RVA is the stable name).
4. **`strip` may only remove bytes no program header maps.** It deletes
   `.symtab`/`.strtab`/`.debug_*` — linker-only data. It cannot remove `.dynsym`/
   `.dynstr` (the loader resolves imports through them — where `printf` lives) or
   `.rodata` (mapped by the R `PT_LOAD` — the format strings). Local and debug names
   go; imports and string constants never do.
5. **Not corrupt — headerless.** Section headers are runtime-irrelevant: the kernel
   maps `PT_LOAD`s, `ld.so` reads `PT_DYNAMIC`, nobody reads `e_shoff`. `sstrip`-style
   tools and packers produce exactly this signature. Analyze with program headers
   alone (or rebuild headers from a memory dump — chapter 06).
6. **It read `e_ident[EI_CLASS]` (byte 4: 32-bit) to size the header, then
   `e_machine` (ARM = 40), and returned ENOEXEC.** Byte 5 (`EI_DATA`) had to say
   little-endian before `e_machine` could be parsed at all. Fix: `qemu-arm` or
   recompiling — the header was honest; the host speaks another architecture.
7. **In memory the module is laid out at `SectionAlignment` (0x1000); on disk at
   `FileAlignment` (0x200).** A memory dump is a memory image — every section sits at
   its RVA, so `RawPtr + (RVA − VirtAddr)` no longer describes the bytes. Rebuilding
   means rewriting the section table to the memory layout (Scylla-style, chapter 06).
8. **`PT_GNU_RELRO`** covers the front of the RW `PT_LOAD` (`.init_array`,
   `.dynamic`, `.got`): after relocation `ld.so` `mprotect`s those pages read-only,
   and `/proc/PID/maps` shows the split as an extra `r--p` line. Chapter 02: partial
   vs full RELRO is exactly "is `.got.plt` inside the RELRO region or not."

</details>
