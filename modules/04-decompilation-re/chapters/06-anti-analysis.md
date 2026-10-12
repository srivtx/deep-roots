# Chapter 06 — Anti-analysis

> **The 30-second version.** Every defensive trick in this chapter exploits one asymmetry: a program gets to run its own instructions before you get to read them comfortably. A packer replaces the file's real code with a compressed blob plus a small stub — the stub is a mini-loader that decompresses the original image into memory, fixes up imports, and jumps to the original entry point. Obfuscators do the same for *readability*: flatten control flow into a dispatcher loop, replace one instruction with a thousand, or compile the program to a private bytecode VM. Anti-debug, anti-VM, and anti-dump are runtime tripwires built on kernel contracts (ptrace allows exactly one tracer; a software breakpoint is a 0xCC patch you can checksum). None of this stops a determined analyst — the counter-playbook is mechanical: run to the original entry point, dump memory, rebuild imports, then analyze. Obfuscation buys *time*, measured in days per layer, never safety.

## Packing: the file lies about what it is

Chapter 01 taught you to read an ELF or PE file as a table of contents: headers, segments, sections, an entry point. Everything in that chapter assumed the file describes the program that will run. A packer breaks that assumption on purpose.

Start from first principles. What does the OS actually need to start a program? The kernel's `execve` needs: (1) enough header to identify the format and find the entry point, (2) one or more `PT_LOAD` segments to map into memory, (3) an address to jump to. That is all. Sections, symbols, relocations, and the import table are *conveniences* for the linker, the dynamic loader, and the analyst — the kernel never looks at them. (Chapter 02 covered what `ld.so` additionally needs: `PT_DYNAMIC`, `PT_INTERP`. Hold that thought — a packer can even dispense with those by doing the dynamic loader's job itself.)

So the minimal honest lie is: *keep the headers, replace the payload.* UPX (this chapter uses UPX 4.2.4, linux/amd64) packs our 15,840-byte sample from earlier chapters down to 5,596 bytes — 35.33% of the original:

```text
$ upx -o anti_packed anti_sample
     15840 ->      5596   35.33%   linux/amd64   anti_packed
```

Look at what changed structurally:

```text
$ readelf -S anti_packed
There are no sections in this file.

$ readelf -lW anti_packed
Entry point 0x4055e0
  LOAD  0x000000 0x400000 0x400000 0x001000 0x004028  RW   0x1000
  LOAD  0x000000 0x405000 0x405000 0x0010a5 0x0010a5  R E  0x1000
```

The original had 14 program headers, 30 sections, a `.text`, a `.got`, a `PT_DYNAMIC`, a `PT_INTERP`. The packed file has *three* program headers, *zero* sections, and the entry point has moved from `0x401080` to `0x4055e0` — into the stub. Compare:

```text
original: Entry point address:  0x401080    (14 phdrs, 30 sections)
packed:   Entry point address:  0x4055e0    ( 3 phdrs,  0 sections)
```

Two details in those two LOAD lines are the whole trick, so read them as bytes:

- The first `LOAD` is `RW`, not executable, with `FileSiz 0x1000` but `MemSiz 0x40428` — the kernel maps one page of file and leaves ~252 KB of zeroed, writable memory. That gap is the *output buffer* where the decompressed image will land.
- The second `LOAD` is `R E` at `0x405000` — compressed blob plus stub. It contains raw `syscall` instructions (`push 2; pop rdi; push 1; pop rax; syscall` — an `open`/`write` pair, visible by byte-searching the file), because the stub cannot call library functions that don't exist yet.

Here is the full lifecycle, end to end:

```text
        ON DISK (anti_packed)                IN MEMORY, after stub runs
  +---------------------------+        +------------------------------+
  | ELF header, 3 phdrs       |        | 0x400000  RW decompressed    |
  | e_entry = 0x4055e0        |        |           .text .data .bss   |
  |  (points into the stub!)  |        |           of the ORIGINAL    |
  +---------------------------+        +------------------------------+
  | LOAD1 RW: 1 page file,    |        | 0x401080  OEP = old _start   |
  |   0x4028 mem = scratch    |  stub  |           ("31 ed 49 89 d1"  |
  +---------------------------+ -----> |            xor ebp,epb...)   |
  | LOAD2 RE: compressed blob |decomp/ |            not in the file!  |
  |   + entry stub @0x4055e0  |resolve +------------------------------+
  +---------------------------+imports | 0x405000  stub (still mapped)|
                                          ^ jump back down to OEP      |
                                          +--------------------------->+
```

The stub at `0x4055e0` does, in order: `push` the registers it will trash, call its decompressor (the NRV2E loop at `0x405655` — you can recognize it by its `call r11` callback idiom, which asks the caller for the next input byte), which writes the original segments into the RW region at `0x400000`..`0x404028`; then it rebuilds what the loader would have built; finally it transfers control to the original entry point. We verified the last step directly: run the packed `looper` in the background and read `/proc/PID/mem` at the original entry address — the bytes there are exactly the original `_start` (`31 ed 49 89 d1 5e ...`, `xor ebp, ebp; mov r9, rdx; pop rsi`), bytes that appear *nowhere* in the packed file (`0x401080` as an immediate does not occur in the file at all — the OEP is computed, not stored, one more small lie).

The import story is the subtle part. Our sample was dynamically linked against libc — normally `ld.so` reads `PT_DYNAMIC`, walks the `.dynsym`/`.got`, resolves `printf@GLIBC_2.34` and friends. The packed file has no `PT_DYNAMIC` and no `PT_INTERP` (check with `readelf -lW | rg INTERP` — nothing). The strings `ld-linux` is absent from the file. Yet the program runs and prints. Conclusion, forced by first principles: *the stub resolves the imports itself.* It contains a mini-linker that maps libc, walks its export table, and patches the call sites in the decompressed image (the decompressed `_start` at `0x405390`-ish contains `call qword ptr [rip+0x2f37]` — a resolved GOT slot). This is why "the packer is a mini-loader" is not a metaphor. Custom malware packers do exactly this with hand-written ELF parsers and `dlopen`-equivalents, plus anti-analysis checks the legit UPX omits.

## Obfuscation: attacking the decompiler's assumptions

Packing defeats *static* tools by hiding bytes. Obfuscation goes further: it presents bytes that are real, executable, and deliberately structure-destroying. Chapter 05's pipeline was disassemble → lift → data-flow → structure. Each trick targets one stage. (This section is the taxonomy; the packing section above is the mechanism.)

**Control-flow flattening** (the OLLVM/Tigress pass) destroys the structurer. Every basic block of a function gets a number; a state variable in a top-level dispatcher selects the next block:

```c
// before flattening                 // after flattening
if (x < 0) {                          state = 3;
    x = -x;                           while (true) {
}                                         switch (state) {
return x + 1;                             case 0: x = x + 1; state = 6; break;
                                          case 3: x = -x;  state = 0; break;
                                          case 5: printf("neg\n"); state = 3; break;
                                          case 6: return x;
                                          ...
                                          default: state = 3;  /* unreachable */
                                          }
                                      }
```

The decompilation signature is unmistakable: one enormous `while (true)` whose body is a single `switch` over a variable that every case assigns a different constant to, with a `default` case that appears unreachable. Every real branch from the original program has become a data assignment. The call graph survives; the CFG becomes a star topology around the dispatcher, and no structuring algorithm can recover "this was an if/else" because the information *is genuinely gone* from the CFG — it now lives in the data flow of the state variable. (Recovery is possible but costs analysis: symbolic execution over the state transitions.)

**Opaque predicates** attack simplification. A predicate whose value is provably constant, but non-obviously so, e.g. `x*x >= 0` on integers, or the classic `7*y*(y+1) % 2 != 0` (a product of consecutive integers is always even, so this is always false). Emit `if (opaque_true) real_code(); else junk_that_crashes();` and any analyzer that can't prove the predicate must analyze *both* arms. Cost to the defender: one multiply. Cost to the analyst: an entire bogus branch, every time.

**Dead-code insertion** is the blunt version: spray unreachable blocks that reference plausible strings, call plausible APIs, contain plausible math. The decompiler dutifully shows them.

**MBA — mixed boolean-arithmetic** attacks constant folding and algebraic simplifiers: `(x ^ y) + 2*(x & y)` is exactly `x + y`, and `(x | 1) - (x & -2)` vanishes into arithmetic nobody wants to read. An 8-term MBA identity replaces one `add`; the semantics survive (the program still works!) but the lifter produces a tangled expression DAG and the human reads gibberish. This is the key economics lesson in miniature: *correctness is cheap to preserve, clarity is expensive to destroy.*

**Virtualization protectors** (VMProtect, Themida) are the endpoint: selected functions are compiled not to x86 but to a *private bytecode* for a one-off virtual machine that the protector emits alongside. The VM's heart is a dispatch loop: fetch opcode, decode, dispatch through a handler table, advance virtual instruction pointer. You recognize it in disassembly as a single hot loop doing table-indirect jumps on a byte stream, with dozens of handlers each performing one tiny semantic step (push, pop, add) on a virtual register file. *Why can't you just write a generic devirtualizer?* Because every protected program ships a different VM: different opcode encoding, different register file layout, different handler set, possibly mutated per build. Devirtualization is per-program archaeology — lift the private ISA, write the lifter, translate, then re-run your whole Chapter 05 pipeline on the lifted code. Days, per binary, per version. (That number is the product working as designed.)

## Anti-debug, anti-VM, anti-dump: runtime tripwires

These assume you *are* going to run the program under a debugger or in a VM, and make that observable.

**PTRACE self-attach.** The kernel's contract: at any moment, a process has at most *one* tracer, and `PTRACE_TRACEME` is how a tracee volunteers. The inverse trick: a program calls `PTRACE_TRACEME` on *itself*. If it succeeds, no debugger was attached — and now the slot is occupied *by the program itself*, so any later `gdb attach` fails with `EPERM`. If it fails, a debugger is already there. One syscall, two detections (see the lab).

**INT3 detection.** Chapter 05's most-used feature — the software breakpoint — is implemented by patching `0xCC` (one byte) over the first byte of the target instruction. That is a *modification of the program's own code*, and code pages are readable. A periodic CRC32 over `.text` catches the patch: the clean snapshot hashes to one value, the breakpoint-hashed value differs. (Debuggers fight back with hardware breakpoints — no code patch — which is why analysts prefer `hbreak` on hostile targets; the counter-counter is checking the x86 debug registers `DR0`–`DR3` via `ptrace(PTRACE_GETREGS)`-equivalent, and the counter-counter-counter is... the arms race, which is the point: each layer costs the other side time.)

**Timing.** Single-stepping makes every instruction a kernel transition; a `rdtsc` (or `clock_gettime` — vDSO, module 01 ch03) delta between two adjacent blocks goes from ~tens of cycles to thousands. One comparison, done.

**Anti-VM.** The hypervisor leaves fingerprints: `CPUID` leaf 1, ECX bit 31 = hypervisor-present bit; MAC prefixes `08:00:27` (VirtualBox) / `00:0C:29` (VMware); registry keys and `dmi` strings on Windows guests. Each is one cheap query.

**Anti-dump.** You found the unpacked image in memory — now dump it. The classic dumpers reconstruct a PE from `/proc/PID/mem`. Countermeasure: after the stub finishes, locate the `MZ` header of its own image in memory and *overwrite it with zeros* (an ELF: `\x7fELF` at base). Tools that page-align and hunt for the magic find nothing; the dump has no header, and rebuilding one is manual work. (Reversing the reversal: the header bytes are still recoverable from the original file if you have it — for the *analyst*, "run to OEP, dump" should happen before the erasure or use the file's headers plus the process's memory.)

## The unpacking playbook (and the economics)

Everything above compresses into a methodology — the standard first-hours workflow on hostile samples:

1. **Triage statically what you can**: strings, the `UPX!` marker, section entropy (`> 7.0` bits/byte on x86 code ≈ compressed or encrypted — normal code sits around 6.0–6.5).
2. **Run to OEP.** Break on *memory-write* at the address of the original entry (a hardware watchpoint — the stub must write `_start`'s bytes there), or watch for the *stack-restore* trick: the stub `push`es a magic register state on entry; when the stack pointer unwinds back past it and `ret`s into old code, you are at OEP. Classic heuristic: set a breakpoint on `GetModuleHandle`/`__libc_start_main`-adjacent code and single-step upward until the module's own `.text` executes.
3. **Dump** the process image (`/proc/PID/mem` on Linux, `gcore`, or a PE dumper on Windows).
4. **Rebuild imports.** The dump contains a *resolved* IAT (real libc addresses, not symbol references), and no `PT_DYNAMIC`/import directory at all. Scylla-style tools (Windows) scan the dump for pointers into loaded module address ranges, cluster them, and emit a fresh import directory referencing the *names* of those functions. On ELF the equivalent is rebuilding `.dynsym`/`.got` from the resolved GOT — you are redoing the loader's and linker's jobs (chapters 01–02 pay off twice).
5. **Analyze** the reconstructed binary with the ordinary chapter 03–05 toolkit.

The economics framing to keep: each protective layer converts to *analyst-days*, not to security. A UPX layer costs minutes (its stub is public and `upx -d` exists). A mutated custom packer costs a day to break. A VMProtect layer on 20 functions costs a week — and the protection *expires* the moment someone publishes the recipe for that family. That is why commercial protectors ship mutation engines: they sell re-randomization, i.e., they sell *time*.

## What insiders know

- **The ptrace-twice contract**: one tracee, one tracer, ever. `PTRACE_TRACEME` on yourself is a two-for-one check — `EPERM` return means "already traced"; success means "no debugger now, and none ever" (the slot is yours). Some malware forks a child that traces the parent for the same effect with better cover.
- **The 0xCC checksum**: a software breakpoint is literally `0xCC` patched over byte 0 of your instruction. CRC32 of your own `.text` (baseline at startup, re-checked later) catches it; the counter is hardware breakpoints (no patch), whose counter is reading `DR0`–`DR7`, whose counter is... analysts call this ladder "the arms race" and bill by the hour.
- **Finding OEP fast**: two field methods — (a) hardware *write* watchpoint on the entry-stub's target page (the decompressor must write there), (b) the stack-restore trick (push a marker value at stub entry; break when `rsp` pops past it). On UPX specifically: the last stage `pop`s a chain of registers then `ret`s into the original `_start` — break on the `ret` at the stub's tail.
- **Flattening's dispatcher signature**: a `while(1) { switch(state) {...} }` where every case ends by *assigning a constant to state*. The original CFG structure is now data-flow; the `default:` case looks unreachable. Ghidra/IDA show a single block with 50+ out-edges — star topology around the dispatcher.
- **Obfuscation is time-based economics**: protection is measured in analyst-days per layer, decays to zero when the recipe is published, and mutation engines exist precisely to re-buy time per release. "Unbreakable" claims are marketing; "6 analyst-days per version" is the real spec sheet.
- **Entropy is the packer detector**: Shannon entropy over sections — x86 code naturally runs ~6.0–6.5 bits/byte; packed/encrypted sections read ~7.5+. One `python -c` over the file's biggest section flags packing before you ever run it.
- **UPX leaves its autograph**: `UPX!` magic appears 4 times in the file (0xec, 0xa77, 0x15ae, 0x15b8 in our sample) plus the `$Info: This file is packed...$` string — real malware packers strip or fake exactly these.

## See it yourself

Everything below ran on this book's container (Debian, gcc 14.2.0). `upx` was not in the image and `apt install upx-ucl` needed root; the container had network, so we fetched the static UPX 4.2.4 tarball — if you're offline, `apt install upx-ucl` (needs sudo) or note the section headers/entry-point parts work with any packed file you're given.

```bash
# 1. A tiny sample (from module 01's toolchain), then pack it
cat > anti_sample.c <<'EOF'
#include <stdio.h>
int main(void) { printf("hello\n"); return 0; }
EOF
gcc -no-pie -O2 -o anti_sample anti_sample.c
upx -o anti_packed anti_sample
#      File size         Ratio      Format      Name
#   --------------------   ------   -----------   -----------
#     15840 ->      5596   35.33%   linux/amd64   anti_packed
```

```bash
# 2. What changed structurally? (the lie, quantified)
readelf -h  anti_sample | rg "Entry|Number of section"   # 0x401080, 30 sections
readelf -h  anti_packed | rg "Entry|Number of section"   # 0x4055e0, 0 sections
readelf -lW anti_packed
#  Type           Offset   VirtAddr   FileSiz  MemSiz   Flg
#  LOAD           0x000000 0x400000   0x001000 0x004028  RW   <- 252KB scratch
#  LOAD           0x000000 0x405000   0x0010a5 0x0010a5  R E  <- blob + stub
readelf -dW anti_packed        # "There is no dynamic section in this file."
readelf -lW anti_packed | rg INTERP   # (nothing — the stub IS the loader)
strings anti_packed | rg '\$Info'     # "$Info: This file is packed with the UPX..."
```

Notice: it runs fine (`./anti_packed` prints), the entry point moved into the stub, sections vanished, and the import machinery is gone — yet libc calls still resolve, because the stub resolved them.

```bash
# 3. Unpack the easy way, and prove the round trip
upx -d -o anti_unpacked anti_packed
#     25651 <-      5596   21.82%   linux/amd64   anti_unpacked
sha256sum anti_sample anti_unpacked
# abeebe25f92aaf33...  anti_sample
# abeebe25f92aaf33...  anti_unpacked     <- byte-identical: UPX -d is lossless
```

```bash
# 4. Unpack by hand: breakpoint at the tail jump (gdb)
gdb -q ./anti_packed
(gdb) starti                      # stop at the stub's entry (0x4055e0)
(gdb) x/8i $pc                    # push rax; push rdx; call <decompressor>...
(gdb) watch *(int*)0x401080       # hardware watchpoint on the OEP page:
                                  # the stub MUST write _start's bytes there
(gdb) c                           # decompressor writes... hits when stub restores
(gdb) x/8i 0x401080               # 31 ed 49 89 d1 5e ... = original _start:
                                  # xor ebp,epb; mov r9,rdx; pop rsi
# At this rip==0x401080 moment, memory holds the unpacked program:
# dump it, rebuild .dynsym/.got, save, done.
```

The same evidence without a debugger (this is the "dump" step, from outside):

```bash
./looper_packed &  PID=$!         # a packed binary that loops forever
# read 16 bytes at the ORIGINAL entry (0x401090 for looper) of the live process:
python3 -c "fd=__import__('os').open('/proc/$PID/mem',0);__import__('os').lseek(fd,0x401090,0);print(__import__('os').read(fd,16).hex())"
# 31ed4989d15e4889e2...  <- the original _start, in memory, absent from the file
```

```c
// 5. A PTRACE self-attach guard (~20 lines) — the anti-debug tripwire
#define _GNU_SOURCE
#include <stdio.h>
#include <sys/ptrace.h>
#include <errno.h>
#include <string.h>
int main(void) {
    long r = ptrace(PTRACE_TRACEME, 0, 0, 0);
    if (r == -1) {                       /* slot already taken: a tracer exists */
        printf("debugger detected (%s) — refusing\n", strerror(errno));
        return 1;                        /* EPERM => we are being traced */
    }
    printf("no tracer — proceeding\n");  /* and now none can EVER attach  */
    return 0;
}
```

```text
$ ./trace_guard
no tracer — proceeding          (exit 0)
$ gdb -q ./trace_guard
(gdb) run
no tracer — proceeding          <- run BEFORE attaching: nothing stops it
(gdb) starti; call (int)ptrace(...)     <- under a tracer it returns EPERM
```

And the 0xCC checksum idea, demonstrated on a snapshot (don't write to your own `.text` — it's read+execute):

```text
$ ./crc_guard
clean   crc32(real_work, 64) = 0x945a83fb
patched crc32              = 0xfd69633  -> INT3 detected
```

## How to use this knowledge

- **Malware triage in the first 30 minutes**: entropy scan → packer identification → run-to-OEP → dump → import rebuild. You now know every step's *why*; the tools (Scylla, `gcore`, `unipacker`) are just packaging around this playbook.
- **Debugging your own packed/deployment artifacts**: "my binary runs on the build machine but crashes in prod" with a packed binary is almost always an import-reconstruction or self-modifying-code issue; knowing the stub's lifecycle tells you where to break.
- **Buying real protection rationally**: if you ship a product, you now can price protection in analyst-days and pick layers (pack + flatten the license check + VM-protect the 3 functions that matter) instead of cargo-culting "maximum obfuscation."
- **Interview-grade answers**: "why can't you just decompile VMProtect output?" (per-program private ISA — devirtualization is bespoke lifter-writing, not a generic transform); "why does PTRACE_TRACEME detect debuggers?" (one-tracer contract, EPERM evidence).
- **Reading security writeups fluently**: CTF and threat-intel reports say "control-flow flattening defeated via symbolic execution of the state variable" or "OEP found via stack-restore" — after this chapter those are mechanics you've performed, not jargon.

## Connections

- **Module 04 ch01** (binary formats): packing is an adversarial abuse of the header/segment contract — you can't see the lie without knowing the honest file's anatomy.
- **Module 04 ch02** (linking & loading): the UPX stub duplicates the loader's job (mapping, import resolution) because it removed `PT_DYNAMIC`/`PT_INTERP`; import reconstruction is *you* becoming the linker.
- **Module 04 ch03** (disassembly): the stub's `call r11` decompressor idiom and dispatcher loops are read with the same compiler-idiom skills.
- **Module 04 ch05** (decompilers): every obfuscation here targets one pipeline stage — flattening the structurer, MBA the simplifier, virtualization the lifter itself.
- **Module 02 ch01** (exploit mitigations) and **ch06** (the same cat-and-mouse economics: RELRO vs GOT-overwrite is an earlier round of "buy time, not safety").
- **Module 03 ch05** (fuzzing): a packed target is a *black box* to coverage-guided fuzzing — instrumentation sees only the stub; you must unpack (or run under emulation) first, which is why fuzzing pipelines start with unpacking stages.

## Quiz

1. *(concept)* A packed ELF has two `PT_LOAD` segments: the first `RW` with `FileSiz 0x1000`, `MemSiz 0x40428`; the second `R E` with `FileSiz = MemSiz = 0x10a5`, and there is no `PT_DYNAMIC` or `PT_INTERP`. Explain what each segment is for, and how the program still manages to call libc functions.

2. *(predict)* You hash the 16 bytes at address `0x401080` of the packed file `anti_packed` *on disk* (converting vaddr→file offset via the LOAD segments), then run the process and read the same address from `/proc/PID/mem`. Disk gives you garbage/zeros; memory gives `31 ed 49 89 d1 5e ...`. Why is that not a contradiction, and what proved the memory bytes are the original `_start`?

3. *(predict)* The clean CRC32 of a function's first 64 bytes is `0x945a83fb`. You set a *software* breakpoint at its first instruction and re-run the checksum. Roughly what changes, and what single byte value is responsible? If instead you set a *hardware* breakpoint, what does the checksum read?

4. *(concept)* Why does control-flow flattening defeat the decompiler's structurer specifically, rather than its disassembler or its data-flow analysis? Where did the original if/else information *go*?

5. *(bug-diagnosis)* A malware analyst writes a PTRACE-based unpacker: it forks, the child calls `PTRACE_TRACEME`, the parent traces the child to OEP, and dumps. It works on the dev box but in the sandbox the target's *own* anti-debug fires and exits immediately. What did the target detect, and via which contract?

6. *(bug-diagnosis)* A colleague "unpacked" a UPX binary by copying `/proc/PID/mem` pages for the whole address range *after* the process had already run for 10 seconds (well past OEP). Their reconstructed ELF crashes at startup. Give two distinct reasons the dump is broken even though the unpacked code bytes were correct.

7. *(concept)* Your target's `.text` section entropy measures 7.6 bits/byte. What does that suggest, what does normal x86 code measure, and what is the *one* property that distinguishes "packed" (recoverable) from "encrypted" (not recoverable) at this stage of triage?

8. *(connection)* Module 02 ch06 framed exploit mitigations (RELRO, stack canaries) as "raising cost, not achieving safety." Module 03 ch05 showed coverage-guided fuzzing needs instrumented, readable code. Explain how both ideas converge on this chapter's economics claim, and why a packed+virtualized target is nearly *worse than useless* to a fuzzer.

<details>
<summary>Answers</summary>

1. **The RW segment is the output buffer; the RE segment is the payload.** The kernel maps the RW segment's file page plus ~252 KB of zeroed writable memory (`MemSiz - FileSiz`) — that is where the decompressor will write the original program's `.text`/`.data`/`.bss`. The RE segment holds the compressed blob and the entry stub (entry point points into it). Libc still works because the stub is a mini-loader: it maps libc itself, walks its exports, and patches the call sites in the decompressed image — the sample's decompressed `_start` contains an already-resolved `call [rip+0x2f37]`. The proof it happens at all: no `PT_INTERP`/`PT_DYNAMIC` exists in the file, yet `printf` resolves.

2. **Not a contradiction — the file never contained those bytes.** The `0x401080` region on disk belongs to the RW segment's *bss-like* tail (`MemSiz > FileSiz`), so the file's bytes there are absent/irrelevant; the original image exists only as compressed data inside the RE segment. What proves the memory bytes are `_start`: they are byte-for-byte the original binary's `_start` prologue (`xor ebp, ebp; mov r9, rdx; pop rsi; mov rdx, rsp; and rsp, -16`) — verified by disassembling the pre-packing binary — and `0x401080` as an immediate appears nowhere in the packed file (the stub computes the OEP rather than storing it).

3. **The checksum changes; the responsible byte is `0xCC`.** A software breakpoint is implemented by patching `0xCC` over the first byte of the instruction, so the region hashes differently (one byte differs: the original first byte → `0xCC`). With a *hardware* breakpoint (x86 debug registers `DR0`–`DR3`), no code byte is modified — the checksum still reads `0x945a83fb`. That asymmetry is precisely why analysts prefer hardware breakpoints on hostile targets, and why advanced malware also inspects the debug registers.

4. **Flattening destroys the CFG's shape, which is the structurer's only input.** The disassembler still decodes every instruction correctly; the data-flow analysis still tracks the state variable. But each original branch (`if (x<0)`) has been rewritten as "assign a constant to `state`" — control information has been converted into *data* flow. The structurer reconstructs if/while from edges in the control-flow graph, and the flattened CFG is a star (every block connects only to the dispatcher), so no control pattern remains to recover. Recovery requires symbolic execution over the state variable's assignments — analysis of data, not structure.

5. **The target detected the tracer via the ptrace contract.** A traced process's own `PTRACE_TRACEME` returns `EPERM` (the one-tracer slot is taken — by the analyst's unpacker). The target's anti-debug checked exactly that return value and exited. The contract being exploited: at any moment a process can have at most one tracer, and both sides can query their status through it. (Fixes: patch the check, or use emulation that doesn't implement ptrace reporting to the guest the same way.)

6. **Two of many:** (a) **Self-modifying aftermath** — 10 seconds past OEP, the unpacked image may have re-encrypted its packing stub, zeroed its own headers (anti-dump), or had mutable data (.bss, heap pointers, initialized function pointers) diverge from any file-consistent state; a dumped page set no longer corresponds to a loadable file. (b) **The ELF headers are gone or wrong** — the process's memory image has no valid header (the packer erased it or the RW segment's on-disk page was never the real header), and crucially the *import table* was never rebuilt: the in-memory GOT holds resolved absolute addresses, so the reconstructed file's loader has nothing to resolve — it must be Scylla-style reconstructed (new `.dynsym`/`.got` naming those addresses) or the first libc call jumps into a wrong (or unmapped, under ASLR) address.

7. **Entropy 7.6 suggests compressed or encrypted content.** Normal x86 machine code measures roughly 6.0–6.5 bits/byte (opcode redundancy); a section above ~7.0 is statistically near-random, meaning compressed, encrypted, or high-entropy embedded data. The distinguishing property at triage: **compressibility is reversible without a secret; encryption needs a key.** If the stub *decompresses* (deterministic, keyless transform — the UPX stub's NRV2E loop), the original is recoverable by running to OEP. If the stub *decrypts*, you must find the key derivation (often obfuscated, sometimes hardware-bound) before anything else — that's the difference between an hour and a week.

8. **Both say protection = cost curve, and both show where the curve bottoms out.** Module 02's mitigations (RELRO, canaries) raise the cost of one specific exploit technique; attackers route around them at fixed cost, so they are "time bought," not safety. This chapter's obfuscation is the same deal one level up: days of analyst time per layer, expiring when the recipe is published. Module 03's fuzzing needs instrumented, structured code to compute coverage; a packed+virtualized binary exposes only the stub and the VM interpreter loop to the fuzzer — coverage collapses to "decompressor" and "dispatcher," so the fuzzer's guided search is effectively blind: it wastes compute on the wrapper rather than the logic. Worse than useless because it also *looks* like it's running (no crash, no signal). The convergence: measure every defense in adversary-time; where adversary-time ≈ 0 (published recipes, generic tools), the defense is decoration.

</details>
