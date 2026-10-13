# Module 04 final exam — decompilation & reverse engineering

Twelve questions across all six chapters. Closed book. Every answer is
worked — the derivation is the point. Scoring: 11+ = you own this module;
8-10 = solid, revisit the flagged chapters; <8 = the quizzes below each
chapter are the study path.

**Part I — the file and the loader (chapters 01-02)**

1. **Concept.** An ELF carries two tables of contents — program headers and
   section headers. Who reads which, what breaks when each is deleted, and
   why is "readelf complains but the program runs" the classic packed-binary
   signature rather than corruption? Tie the four `PT_LOAD`s to the
   `/proc/PID/maps` permission rows of module 01 ch03.

2. **Derive.** With this PE section table and `ImageBase = 0x140000000`, an
   imported function's thunk sits at RVA `0x8E2C`. Which section owns it,
   what is the file offset, and the VA at the preferred base? Which answer
   survives a rebased load?

   ```text
   name    VirtAddr  VirtSize  RawSize  RawPtr
   .text   0x1000    0x5A48    0x5C00   0x400
   .rdata  0x7000    0x2A80    0x2C00   0x6400
   .data   0xA000    0x1530    0x1600   0x9000
   ```

3. **Derive — read the dance.** File bytes: `printf@plt` at `0x1040` opens
   `jmp QWORD PTR [GOT 0x4008]`; initial `GOT[printf] = 0x1046`; there sits
   `push 0x1`, then `jmp PLT0`, which pushes the link_map and jumps
   `_dl_runtime_resolve`. Trace the first call, the resolver's write, every
   later call, and why the initial slot must point at the `push` in the
   same PLT entry — what would pointing at the `jmp` (or at `printf`
   itself) do? What `LD_DEBUG=bindings` line separates lazy from
   `LD_BIND_NOW=1`?

4. **Bug diagnosis (cross-chapter).** A writeup claims code execution by
   overwriting `printf@got` in target A (Debian-default gcc: `GNU_RELRO`,
   no `BIND_NOW`) and target B (`-z relro -z now`). For each: does the write
   land on a writable page? Why does `/proc/PID/maps` show *five* file-backed
   rows for four `PT_LOAD`s — which header makes the extra `r--p` row, and
   where does `.got.plt` sit in each build? Name the five-second
   confirmation.

**Part II — reading the code (chapters 03-04)**

5. **Derive.** From a stripped `-O2` binary, a pointer arrives in `rdi` and
   these accesses appear (plus a loop whose closing test is shown). Recover
   the struct: each field's offset, size, and type; the padding and why;
   `sizeof`.

   ```asm
   movsxd     rax, DWORD PTR [rdi]
   cvttsd2si  ecx, QWORD PTR [rdi+0x8]
   add        rax, QWORD PTR [rdi+0x10]
   movsx      rdx, BYTE PTR [rdi+0x18]
   mov        rdi, QWORD PTR [rdi+0x30]
   test       rdi, rdi
   jne        .loop
   ```

6. **Concept — name the idioms.** Three switches in one `-O2` binary:
   (A) cases 0-5, each a distinct block of logic; (B) cases 0-5 returning
   10, 21, 32, 43, 54, 65; (C) cases 0, 100, 1000, 100000. Predict each
   dispatch structure with its byte-level evidence, state the cost model,
   and explain why the jump-table doorman must be `ja` (unsigned) rather
   than `jg`.

7. **Derive.** Decode by hand: `_ZNK3net9TCPSocket4sendEPKvm` and
   `_ZTVN3net9TCPSocketE`. Then: an object stores its vptr at offset 0 —
   which address (relative to the vtable symbol) does it hold, what two
   words precede it, and what does the second let you recover for the whole
   binary?

8. **Bug diagnosis.** At `-O0` a derived-class constructor writes the vptr
   twice: the base constructor stores vtable-for-Base, then the derived
   overwrites it. The stripped `-O2` build shows one vptr store and no
   base-constructor call. A teammate concludes "not actually derived — the
   two-writes fingerprint is broken." Diagnose what the optimizer did, why
   it is legal, and what still proves the derivation.

**Part III — the toolchain and the adversary (chapters 05-06)**

9. **Concept.** State the two theorem-grade limits of decompilation, prove
   each in one sentence, and explain why `goto` in decompiler output is a
   design decision, not a bug.

10. **Bug diagnosis.** A decompilation shows `local_14` as a `char *`
    before a loop and an `int` counter after — values apparently
    clobbering. The source reads `for (int i = 0; i < n; i++) total +=
    abs(a[i]);`, but the decompilation is a guard plus a `do-while` walking
    a pointer to an end pointer — no `i` anywhere. Diagnose both readings;
    what experiment confirms the first?

11. **Bug diagnosis — identify the packing.** Suspect ELF triage: 3 program
    headers, 0 sections; an `RW` `LOAD` with `FileSiz 0x1000`, `MemSiz
    0x40428`; an `R E` `LOAD` at `0x405000`; entry `0x4055e0`; no
    `PT_DYNAMIC`, no `PT_INTERP`; the `R E` segment's entropy is 7.5
    bits/byte; the program still prints via `printf`. What is it, what is
    each segment for, how do libc calls resolve with no loader, what one
    property distinguishes "packed" (recoverable) from "encrypted" (not),
    and what does legit UPX leave that malware strips?

12. **Analysis — the playbook.** You must stop a packed binary exactly at
    the original entry point. Give two field methods with the mechanical
    reason each works, why you cannot simply search the file for the OEP's
    address, and what must happen before the dump is analyzable.

<details>
<summary>Answers</summary>

**Part I**

1. The kernel and `ld.so` read program headers only; linkers and tools
   (readelf, gdb, Ghidra) read section headers. Delete the section table:
   still runs, tool-hostile (`sstrip`, packers); delete program headers:
   nothing loads it. No contradiction: a section either intersects a
   segment (loaded) or is linker-only (`.symtab`). "readelf complains,
   program runs" is *headerless*, not corrupt — packers keep the minimal
   honest headers and replace the payload (ch06). The four `PT_LOAD`s are
   four `mmap`s — the `r--p`/`r-xp`/`r--p`/`rw-p` rows of module 01 ch03.
   (ch01, ch06)

2. `0x7000 <= 0x8E2C < 0x9A80` (`0x7000+0x2A80`), so `.rdata` owns it.
   Offset = `RawPtr + (RVA − VirtAddr) = 0x6400 + (0x8E2C − 0x7000) =
   0x822C`. Preferred VA = `0x140000000 + 0x8E2C = 0x140008E2C`. ASLR moves
   the VA; the **RVA** — address minus ImageBase — is the stable identity.
   (ch01)

3. First call: `call printf@plt` → the stub's `jmp` reads `GOT[printf] =
   0x1046` → control bounces back into the same stub onto `push 0x1` — the
   JUMP_SLOT's relocation index rides the stack → `jmp PLT0` → push
   link_map → `_dl_runtime_resolve`, which looks up `printf`, **writes
   `&printf` into slot 0x4008**, and enters it. Later calls: the
   stub's `jmp` reads the patched slot — two instructions. The slot must
   initially point at the `push`: that back-pointer turns a data indirection
   into a resolver invocation, stacking the index. At the `jmp` it would
   loop forever; at `printf` it is impossible — the linker knows neither
   *which* object satisfies the symbol (resolution order) nor *where* it
   lands (ASLR). Lazy: the binding line traces after "transferring control";
   `LD_BIND_NOW=1` moves it before. (ch02)

4. A: the write lands — partial RELRO ends at `0x4000`, and `.got.plt`'s
   JUMP_SLOTs (`0x4000+`) sit in the still-writable `rw-p` row: that
   writable function pointer is the GOT-overwrite family (module 02 ch06).
   B: the write segfaults — `BIND_NOW` resolved every slot eagerly, then
   the loader `mprotect`ed the whole GOT, `.got.plt` included, read-only:
   full RELRO working. The extra `r--p` row is `PT_GNU_RELRO`'s `mprotect`
   over the front of the RW `PT_LOAD` (module 01 ch03). Confirm:
   `readelf -dW binary | grep BIND_NOW` — present = full. (ch02;
   module 01 ch03; module 02 ch06)

**Part II**

5. `0x00`: 4-byte signed int (`movsxd`). `0x04`: 4 bytes padding —
   inferred: the double at `0x08` needs 8-byte alignment and nothing
   touches `0x04`. `0x08`: IEEE double (`cvttsd2si`). `0x10`: 8-byte
   signed integer (the qword `add`). `0x18`: a signed byte in a byte array
   — the next touched offset is `0x30`, so 24 bytes: `char name[24]`.
   `0x30`: an 8-byte self-pointer — the loop reloads `rdi` from
   `[rdi+0x30]` and tests it: the induction variable is the pointer itself
   (a list walk). `sizeof = 0x38 = 56`; the untouched gap is padding proof
   (module 01 ch07). (ch03)

6. (A) Jump table: `cmp edi,0x5; ja default; movsxd rax,[rdx+rdi*4];
   add rax,rdx; jmp rax` — bounds check, table in `.rodata`, 4-byte
   base-relative displacements (zero relocations under ASLR). `ja`, not
   `jg`: a negative `x` must reach `default` — unsigned, −1 reads as
   ~4 billion; `jg` would pass negatives into the table, reading out of
   bounds. (B) No dispatch: the results are linear in
   the case value (`10+11x`), so gcc emits the closed form — two `lea`s
   behind the guard; absence of a table is not absence of a switch. (C)
   Comparison tree: `cmp/je/jg` in binary-search arrangement — four cases
   over 100,001 slots waste a table. The table beats ~10 bytes of
   compare-and-branch per case only when labels are dense. (ch03)

7. First: `_Z` + `N` (nested) + `K` (const member) + `3net 9TCPSocket
   4send` + `E` + `PKvm` (pointer to const void; unsigned long) =
   **`net::TCPSocket::send(void const*, unsigned long) const`** — return
   types are not encoded. Second: `TV` = "vtable for" + nested
   `3net 9TCPSocket` = **`vtable for net::TCPSocket`**. The vptr holds the
   **address point**, vtable+0x10; the two preceding words are the
   offset-to-top (0 for a primary base) and the **typeinfo pointer** — and
   each typeinfo's vptr discriminator (`__si_class_type_info` = single
   inheritance, `__vmi_…` = multiple) makes the whole inheritance DAG
   recoverable from `.data.rel.ro` without executing anything. (ch04)

8. `-O2` **inlined the base constructor**, and with no virtual call between
   the two stores the intermediate (base) vptr store is dead — dead-store
   elimination removes it. Legal because that value is observable only if
   something reads the vptr while the base constructor runs (a virtual call
   there must dispatch to the *base's* version — why `-O0` keeps both
   writes). `s->area()` may even be devirtualized — the compiler just wrote
   that vptr. The derivation is still provable via RTTI: the vtable's
   typeinfo slot at `vptr−0x08` targets `__si_class_type_info` with a base
   pointer at `+0x10` — the DAG survives optimization in `.data.rel.ro`
   (module 01 ch06; `-fno-rtti` zeroes the slot). (ch04; ch03)

**Part III**

9. (1) **Names are unrecoverable**: renaming every variable is a
   semantics-preserving bijection — infinitely many sources compile to the
   same bytes, so no algorithm can select the original; `uVar1` is the
   theorem, not a failure. (2) **Perfect structuring is impossible**:
   goto-free C generates exactly the reducible CFGs; irreducible CFGs exist
   (computed `goto`s, state machines), and structuring them requires node
   splitting, which can blow up exponentially — every structurer
   compromises. `goto` in the output is that compromise made visible: the
   honest limit. (ch05)

10. First: **variable merge error** — at `-O1`+ two source variables with
    disjoint live ranges share one stack slot, and the decompiler
    faithfully reports one; confirm by widening `local_14`'s type and
    watching the uses split into two consistent groups. Second: the
    induction variable `i` became address arithmetic — `lea` for the end
    pointer, `add rdi,4` for the increment, a pointer `cmp` for the
    condition — so `i` no longer exists in the dataflow, and a decompiler
    can only name what the dataflow contains: the same erasure theorem as
    names. (ch05; ch03)

11. **Packed (UPX-family).** The `RW` load is the output buffer: one file
    page plus `MemSiz − FileSiz` ≈ 252 KB of demand-zero memory — where the
    stub decompresses the original image. The `R E` load is the compressed
    blob plus entry stub (the moved `e_entry` points into it — the stub is
    a mini-loader: decompress, fix imports, jump to OEP). No
    `PT_INTERP`/`PT_DYNAMIC`, yet `printf` resolves — the stub resolves
    imports itself: maps libc, walks its exports, patches the decompressed
    call sites. Entropy: normal x86 code runs ~6.0-6.5 bits/byte;
    ≥7.0 means compressed or encrypted. The distinction: **compression is
    keyless** — run to OEP and recover; encryption needs its key first.
    Legit UPX leaves `UPX!` magic and the `$Info` string; real packers strip
    or fake them. (ch06; ch01-02)

12. (1) **Hardware write watchpoint on the OEP page** (`watch
    *(int*)0x401080`): the decompressor *must* write `_start`'s bytes
    there, so it fires exactly when memory first holds the
    unpacked program. (2) **The stack-restore trick**: the stub pushes a
    marker state at entry; break when `rsp` unwinds past it — or on the
    stub's tail, where it pops its registers and `ret`s into the original
    `_start`. The OEP cannot be grepped because it is
    **computed, not stored** — `0x401080` as an immediate appears nowhere
    in the packed file. Afterwards: dump `/proc/PID/mem`, then rebuild
    imports — the in-memory GOT holds *resolved addresses*, not symbol
    references: redo the linker's job (rebuild `.dynsym`/`.got`,
    Scylla-style) before chapters 03-05 apply. (ch06; ch01-02)

</details>
