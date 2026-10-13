# deep-roots — computer science from first principles

> Every technical field has a **root layer** that its practitioners know and everyone else
> waves away. This repo goes down and stays there.

**deep-roots** is a learning program, not a tutorial dump. Each module takes one field of
computer science and rebuilds it from first principles — the physics, the hardware, the
data structures, the design decisions and their consequences — so that what other people
memorize, we *derive*. The first module is the root of them all: **the memory models of
programming languages**. Sixteen more modules build on it — security, bugs, decompilation,
Android, iOS, desktop kernels, COBOL, concurrency, compilers, databases, GPUs, networking,
file systems, virtualization, browsers, hardware — because every one of them is a
consequence of how programs store and reclaim bytes, all the way down to the transistors.

**Site (dark mode + light mode): <https://srivtx.github.io/deep-roots/>** ·
[Interactive quiz](https://srivtx.github.io/deep-roots/quiz.html) ·
[The roadmap](ROADMAP.md)

---

## The program — 17 modules, all live

**107 chapters · ~375,000 words · 84 chapter quizzes (8 questions each) · 17 final exams.**

| Module | Field | Chapters |
|--------|-------|----------|
| 01 | [Memory models, from silicon to GC](modules/01-memory-models/README.md) | 23 |
| 02 | [Memory security & exploitation](modules/02-memory-security/README.md) | 7 |
| 03 | [Software bugs: the taxonomy of failure](modules/03-software-bugs/README.md) | 5 |
| 04 | [Decompilation & reverse engineering](modules/04-decompilation-re/README.md) | 6 |
| 05 | [Android internals](modules/05-android-internals/README.md) | 6 |
| 06 | [iOS internals](modules/06-ios-internals/README.md) | 5 |
| 07 | [Desktop OS internals: Windows / Linux / macOS](modules/07-desktop-internals/README.md) | 6 |
| 08 | [COBOL & the mainframe world](modules/08-cobol-mainframe/README.md) | 5 |
| 09 | [Concurrency & distributed memory](modules/09-concurrency-distributed/README.md) | 5 |
| 10 | [Compilers & runtime internals](modules/10-compilers-runtimes/README.md) | 5 |
| 11 | [Database storage engines](modules/11-db-storage-engines/README.md) | 5 |
| 12 | [GPU & AI accelerator memory](modules/12-gpu-memory/README.md) | 4 |
| 13 | [Networking internals](modules/13-networking-internals/README.md) | 5 |
| 14 | [File systems & storage](modules/14-filesystems-storage/README.md) | 5 |
| 15 | [Virtualization, containers & the cloud](modules/15-virtualization-cloud/README.md) | 5 |
| 16 | [Browser engine internals](modules/16-browser-internals/README.md) | 5 |
| 17 | [Digital hardware: from transistors to silicon](modules/17-hardware-design/README.md) | 5 |

Every module follows the same chapter template (see
[CONVENTIONS.md](modules/01-memory-models/CONVENTIONS.md)): a 30-second version, teaching
sections that *derive* rather than assert, **What insiders know**, a **See it yourself**
lab with real commands, **How to use this knowledge**, explicit **Connections** — and an
**8-question quiz** with worked answers, plus a module final exam.

## Module 01 — Memory models, from silicon to GC

Twenty-three chapters in five parts. Every chapter ends with a quiz, a
*see-it-yourself* lab, and the connections to the rest of the field.

### Part I — the machine (first principles)

| # | Chapter | What you will actually understand afterwards |
|---|---------|----------------------------------------------|
| 01 | [Memory from silicon](modules/01-memory-models/chapters/01-memory-from-silicon.md) | DRAM cells, addresses, words, endianness, alignment — why memory is shaped the way it is. |
| 02 | [The hierarchy](modules/01-memory-models/chapters/02-the-hierarchy.md) | Registers, cache lines, associativity, locality, the latency numbers that explain everything. |
| 03 | [Virtual memory](modules/01-memory-models/chapters/03-virtual-memory.md) | Page tables, TLB, demand paging, copy-on-write, ASLR, swap and OOM — the OS's grand illusion. |
| 04 | [The stack](modules/01-memory-models/chapters/04-the-stack.md) | Frames, prologues, calling conventions, red zones — why local variables are free. |
| 05 | [The heap](modules/01-memory-models/chapters/05-the-heap.md) | Free lists, bins, fragmentation, `sbrk`/`mmap` — why dynamic memory costs. |
| 06 | [Object layout](modules/01-memory-models/chapters/06-object-layout.md) | Padding, vtables, boxing, tag bits — what a "value" really looks like in RAM. |
| 07 | [Assembly: the reference model](modules/01-memory-models/chapters/07-assembly-reference-model.md) | x86-64 and ARM64 stack discipline — the ground truth every language compiles to. |

### Part II — the languages (each answers four questions: what is a value, who owns it, who frees it, what safety)

| # | Chapter | Model |
|---|---------|-------|
| 08 | [C](modules/01-memory-models/chapters/08-c.md) | Explicit: you own everything, you free everything. |
| 09 | [C++](modules/01-memory-models/chapters/09-cpp.md) | RAII: lifetime is scope. |
| 10 | [Rust](modules/01-memory-models/chapters/10-rust.md) | Ownership: affine types + the borrow checker. |
| 11 | [Go](modules/01-memory-models/chapters/11-go.md) | Escape analysis + concurrent GC. |
| 12 | [Java](modules/01-memory-models/chapters/12-java-jvm.md) | The generational JVM heap. |
| 13 | [C# / .NET](modules/01-memory-models/chapters/13-csharp-dotnet.md) | The CLR: value types + generational GC. |
| 14 | [Python](modules/01-memory-models/chapters/14-python.md) | Refcounting + a cycle detector. |
| 15 | [JavaScript / V8](modules/01-memory-models/chapters/15-javascript-v8.md) | Hidden classes + a generational scavenger. |
| 16 | [Swift & Objective-C](modules/01-memory-models/chapters/16-swift-objc.md) | ARC: the compiler inserts the refcounts. |
| 17 | [COBOL](modules/01-memory-models/chapters/17-cobol.md) | Fixed records: no heap, ever. |
| 18 | [The elders](modules/01-memory-models/chapters/18-the-elders.md) | Fortran, Lisp, Haskell, Erlang, Ada, Zig — where every idea was invented. |

### Part III — the deep end

| # | Chapter | Model |
|---|---------|-------|
| 19 | [Allocators](modules/01-memory-models/chapters/19-allocators-deep.md) | dlmalloc bins, tcmalloc, jemalloc, slab, arenas — the machinery under `malloc`. |
| 20 | [The hardware memory model](modules/01-memory-models/chapters/20-hardware-memory-model.md) | MESI, store buffers, atomics, fences, false sharing — why cores disagree about memory. |
| 21 | [Memory security](modules/01-memory-models/chapters/21-memory-security.md) | Overflows, use-after-free, heap attacks, ROP, Rowhammer — how every model fails. |
| 22 | [Mobile memory](modules/01-memory-models/chapters/22-mobile-memory.md) | Android's zygote + ART GC; iOS's Jetsam + compression. |

### Part IV — the payoffs

| # | Chapter | What it gives you |
|---|---------|------------------|
| 23 | [The unification](modules/01-memory-models/chapters/23-unification.md) | One table for every language, the connections to every future module, the tools, the practice path. |
| — | [Final exam](modules/01-memory-models/quiz/final-exam.md) | 30 questions across the whole module, with full explanations. |

## The method

1. **First principles, downward.** Each module starts at the physical layer and builds up —
   no fact is allowed to be memorized if it can be derived.
2. **The insider test.** Every chapter has a *What insiders know* section: the specifics that
   only people inside that field use daily — sizes, thresholds, flags, failure modes, folklore.
3. **See it yourself.** Every chapter has a lab: real commands, real output, nothing that
   requires trusting the author.
4. **Connections or it didn't happen.** Every chapter ends with explicit links to other
   chapters, future modules, and the practical payoff (debugging, performance, security,
   interviews).
5. **Quizzes with teeth.** Questions that predict output, diagnose a bug, or expose a false
   intuition — with worked answers.
6. **Honesty about scope.** Where a claim is simplified for the path from first principles,
   the chapter says so.

## Repository layout

```
deep-roots/
├── index.html, quiz.html, chapters/, roadmap.html   # the site (GitHub Pages)
├── m02/ ... m17/                                    # per-module site pages (02-17)
├── assets/                                          # dual-theme styles, theme.js, favicon
├── modules/
│   ├── 01-memory-models/                            # LIVE — 23 chapters + final exam
│   │   ├── chapters/                                # markdown sources
│   │   └── quiz/                                    # final exam (interactive: /quiz.html)
│   ├── 02-memory-security/                          # LIVE — 7 chapters + exam
│   ├── 03-software-bugs/                            # LIVE — 5 chapters + exam
│   ├── 04-decompilation-re/                         # LIVE — 6 chapters + exam
│   ├── 05-android-internals/                        # LIVE — 6 chapters + exam
│   ├── 06-ios-internals/                            # LIVE — 5 chapters + exam
│   ├── 07-desktop-internals/                        # LIVE — 6 chapters + exam
│   ├── 08-cobol-mainframe/                          # LIVE — 5 chapters + exam
│   ├── 09-concurrency-distributed/                  # LIVE — 5 chapters + exam
│   ├── 10-compilers-runtimes/                       # LIVE — 5 chapters + exam
│   ├── 11-db-storage-engines/                       # LIVE — 5 chapters + exam
│   ├── 12-gpu-memory/                               # LIVE — 4 chapters + exam
│   ├── 13-networking-internals/                    # LIVE — 5 chapters + exam
│   ├── 14-filesystems-storage/                     # LIVE — 5 chapters + exam
│   ├── 15-virtualization-cloud/                    # LIVE — 5 chapters + exam
│   ├── 16-browser-internals/                       # LIVE — 5 chapters + exam
│   └── 17-hardware-design/                          # LIVE — 5 chapters + exam
├── tools/build_site.py                              # markdown → styled HTML pipeline
├── agents.md                                        # automatic session ledger
└── README.md
```

## The program ledger

Work is tracked in [`agents.md`](agents.md) — the same automatic session-ledger discipline as
[srivtx/p-rick](https://github.com/srivtx/p-rick), so nobody has to remember or ask how
anything was made.

## License

MIT — see [LICENSE](LICENSE). Use it, teach it, fork it.
