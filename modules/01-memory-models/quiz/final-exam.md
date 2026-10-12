# Module 01 final exam — memory models from first principles

Thirty questions across all twenty-three chapters. Closed book. Every answer is
worked, not just stated — the explanations are the point. Scoring: 26+ = you own
this module; 20-25 = solid, revisit the flagged chapters; <20 = the quiz answers
below each chapter are the study path.

**Part I — the machine (chapters 01-07)**

1. Why does the DRAM burst size determine the cache line size?
2. `struct { char a; long b; char c; }` on x86-64: size and padding map?
3. A 3 GHz CPU, DRAM 100 ns: how many cycles is one miss? Why does sequential
   streaming still run near bandwidth?
4. Page fault → SIGSEGV: what decides it, and name two fault outcomes that are
   *not* errors.
5. fork() copies what, and what does the child's first write cost?
6. Why is stack allocation one instruction, and what exactly does `leave` do?
7. `int *dangle(void){ int x=1; return &x; }` — why does it "work" once and break
   the second time?
8. What are caller-saved vs callee-saved registers, and why does the split exist?
9. `free(p)` takes no size. Where is the size, and what does that adjacency cost
   (two answers: perf and security)?
10. Why can the JVM compact its heap while glibc cannot?

**Part II — the languages (chapters 08-18)**

11. C: what legally happens to `if (a+b < a)` at `-O2`, and why?
12. C++: why can a lingering `weak_ptr` pin a `make_shared` allocation after the
    object died?
13. Rust: why is `Option<Box<u8>>` 8 bytes, and what is this optimization called?
14. Go: a closure captures a local by reference — what two allocation decisions
    did the compiler just make?
15. JVM: heap at 32 GB vs 31 GB with compressed oops — what changes, and why is
    the smaller heap sometimes *better*?
16. CLR: `new double[10_000]` — which generation, and why? (Careful.)
17. Python: `a = 257; b = 257` in one line vs two files — what does `a is b`
    print in each, and why?
18. V8: two objects, properties added in different order — same hidden class?
    What is the runtime consequence?
19. Swift: why can `weak` references zero themselves when Rust's `Rc::Weak`
    cannot?
20. COBOL: how many bytes is `PIC S9(7) COMP-3`, and where is the sign?
21. Erlang: a process holds references to ten 100-byte binaries vs ten 100 KB
    binaries — what's the retention difference?
22. Name the language that passes its allocator *as a parameter* and why that
    makes allocation greppable.

**Part III — the deep end (chapters 19-22)**

23. `malloc(1)` on glibc 2.35 x86-64: total chunk size? `malloc(100000)`: brk or
    mmap? `malloc(200000)` freed then repeated: what *silently changes*?
24. SB litmus test on x86 vs ARM: which values can both threads see, and which
    architectural property makes x86's answer narrower?
25. What does `volatile` guarantee in C — atomicity, ordering, or neither? Same
    question for Java.
26. What is Safe-Linking, and which chapter-19 fact made it necessary?
27. ASan: what is shadow memory, what is its overhead, and how does it catch
    use-after-free *after* the free?
28. Why does an iOS memory-mapped 500 MB read-only asset cost ~0 footprint while
    the same bytes in a `Data` cost 500 MB?
29. Android: why does every app share a boot image, and which Chapter 03
    mechanism is it?
30. One sentence each: MTE, false sharing, the canary's zero byte, dirty vs
    clean pages, generational hypothesis.

<details>
<summary>Answers</summary>

**Part I**

1. The hierarchy's unit of transfer should match the wire's: DRAM delivers 8×64-bit
   bursts = 64 bytes; making the cache line the burst width means one miss = one
   transfer, no partial-line waste or re-fetch (ch01/02).
2. 24 bytes: a@0, pad 1-7, b@8, c@16, pad 17-23 — trailing pad so the struct size
   is a multiple of its largest member alignment (8), keeping arrays aligned
   (ch01/06).
3. ~300 cycles. Streaming wins on *throughput* (not latency): the memory system
   pipelines many outstanding misses (MLP) and the prefetcher stays ahead —
   latency is hidden by parallelism (ch02).
4. The kernel's fault handler policy decides. Not-errors: zero-fill on first
   touch, swap/file readback, COW break (ch03).
5. The page *tables* only; all pages shared read-only/COW. The child's first
   write to a shared page faults and copies that one page (typically 4 KB) (ch03).
6. Frame size is compile-time known, so claim = `sub rsp, N` (one instruction).
   `leave` = `mov rsp, rbp; pop rbp` — restores rsp *past* all locals and
   restores the caller's frame pointer: batch deallocation (ch04).
7. The frame is freed but not erased: first use often still reads the old bits;
   an intervening call's frame reuses and overwrites the slot before the second
   use. UB because the standard guarantees neither outcome (ch04/08).
8. Callee-saved: the callee preserves them across the call (push/pop in
   prologue); caller-saved: the caller must spill around calls. The split lets
   leaf functions use caller-saved registers with zero save/restore cost —
   splitting the burden by who actually needs continuity (ch04/07).
9. In allocator metadata adjacent to the block (glibc: size word at p−8). Perf:
   the 32-byte minimum chunk for a 1-byte request (~24-31 wasted bytes).
   Security: overflow of user data corrupts the *next* chunk's metadata — the
   precondition of heap exploitation (ch08/19/21).
10. The JVM can enumerate every live reference (typed object graph + GC roots),
    so it can move objects and rewrite pointers. C's raw pointers hide in
    registers/unions/foreign code — unfindable, so blocks must stay put
    (ch05/12).

**Part II**

11. The check compiles away: signed overflow is UB, so the optimizer may assume
    `a+b < a` simplifies to `b < 0` and delete the addition. Correct checks:
    `b > INT_MAX - a` or `__builtin_add_overflow` (ch08).
12. `make_shared` allocates object + control block as ONE chunk; a surviving
    weak count keeps the whole allocation (both strong and weak counts live in
    the block) — the block can't be freed until weak hits zero too. Two-allocation
    shared_ptr doesn't have this coupling (ch09).
13. Box is a non-null pointer; `None` is encoded as the null pointer pattern
    that a valid Box can never hold — the "niche"/null-pointer optimization.
    Optionality is free (ch10).
14. The captured local must outlive the frame → it escapes to the heap; the
    closure itself is heap-allocated (a Context-ized box holding the captured
    reference). Check with `-gcflags='-m'` (ch11).
15. 32 GB is past the compressed-oops reach (32-bit shifted refs decode to
    2^32×8 = 32 GB): references become 8-byte, every object grows ~4-8 bytes —
    total heap *capacity* can drop going from 31→32 GB, so -Xmx31g can hold more
    objects than -Xmx32g (ch12).
16. Not LOH: the double[]-specific rule died with .NET Core; the 85,000-byte
    threshold applies (10,000×8 = 80,000 < 85,000) — gen0. The trap: on .NET
    Framework, `double[]` ≥ 1000 elements went to LOH (ch13).
17. One line: True — the compiler folded both literals into one constant pool
    entry (plus the −5..256 cache covers 257? No — 257 is out of range; the
    one-line case is constant *dedup*). Two separate compilations: False —
    distinct constants, distinct objects. (And `is` with literals warns in
    3.12+ because you're testing dedup artifacts, not equality) (ch14).
18. Different hidden classes: property-add order builds different transition
    paths, so the two objects carry different maps. Consequence: the call site
    becomes polymorphic/megamorphic — inline caches miss, optimization dies
    (ch15).
19. Swift/ObjC weak slots live in a global side table outside the object; dealloc
    zeroes them under lock before the memory is released. Rust's weak count
    lives inside the Rc block itself — the dying object's own memory — so no
    outside writer can nil the reference; a live Weak pins the block instead
    (ch16/10).
20. 4 bytes: 7 digits → ⌈(7+1)/2⌉ = 4 nibbles of packed decimal; the sign
    occupies the last nibble (C=+, D=−, F=unsigned) (ch17).
21. Binaries >64 bytes are refcounted and live off-heap, *shared* between
    processes — ten references to one 100 KB binary retain one copy; 100-byte
    binaries are copied into each process's heap (ch18).
22. Zig: every allocating function takes `std.mem.Allocator` — no hidden global,
    so "which heap, whose lifetime" is visible at the call site, and arena
    lifetimes are one `defer deinit` (ch18).

**Part III**

23. 32 bytes (24 usable). 100,000 < 131,072 threshold → brk arena. After the
    200,000-byte mmap'd chunk is freed, the dynamic threshold *rises* to
    ~200,000 — subsequent identical requests come from brk: "run 2 behaved
    differently" (ch19).
24. Both threads can read 0 (both stores still buffered) — allowed on both
    architectures; x86-TSO additionally forbids the other three reorderings
    (only store→load reorders), while ARM may also show store→store and
    load→load reorderings across addresses. The store buffer is the mechanism;
    x86's drains in order (ch20).
25. C `volatile`: neither atomicity nor ordering — it only forbids
    caching/merging of the *single* access in registers (compiler visibility),
    which is a device-register contract, not a thread contract. Java
    `volatile`: sequential consistency for volatile accesses — reads/writes are
    ordered and atomic (JSR-133). Same keyword, different universes (ch20).
26. glibc 2.32's tcache/fastbin pointer mangling: freed-chunk `fd` pointers are
    stored XOR'd with their own address >> 12, so an attacker who overwrites a
    freelist pointer with a plain value fails the decode — it was necessary
    because chunk metadata sits adjacent to attacker-writable data (ch19/21).
27. Shadow = 1 byte per 8 bytes of heap (1/8 overhead), encoding each
    8-byte granule's poison state (redzones 0xfa, freed 0xfd). UAF after free:
    freed memory goes to a quarantine and is poisoned, so the *stale access
    itself* is the violation caught at the exact instruction (ch21).
28. mmap'd file pages are file-backed *clean* pages: evictable, re-faulted from
    NAND — jetsam doesn't bill them. `Data` copies are anonymous *dirty* pages:
    must stay resident (or be compressed) — 500 MB of footprint. Clean vs dirty
    is the iOS memory ledger (ch22/03).
29. The zygote: one process preloads the boot image/framework; every app is
    forked from it, sharing those pages copy-on-write. The mechanism is COW
    from Chapter 03 — sharing without copying, copying only on write (ch22).
30. MTE: 4-bit tag per 16-byte granule, hardware-checked — catches UAF/OOB at
    the CPU. False sharing: two cores writing different variables in one line —
    MESI ping-pong. The canary's zero byte: the low byte is 0x00 so
    strcpy-family overflows terminate inside the canary instead of copying it
    out. Dirty vs clean: must-stay-resident vs evictable-again page classes
    (the mobile memory currency). Generational hypothesis: most objects die
    young — the bet that funds copying nurseries (ch12/20/21/22).

</details>
