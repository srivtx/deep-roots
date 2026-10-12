# Module 01 — Memory models: from silicon to GC

> The root of the roots. Every programming language answers four questions — *what
> is a value, who owns it, who frees it, what safety is guaranteed* — and every
> field downstream (security, decompilation, mobile, databases, concurrency) is a
> consequence of those answers. This module rebuilds the whole subject from the
> physics up.

**23 chapters · ~100,000 words · 16 languages · 190+ quiz questions · 1 final exam.**

## How to study it

Read in order the first time; the chapters are an argument, not a reference:

- **Part I (ch 01-07), the machine** — where every abstraction is *derived*:
  silicon, caches, virtual memory, the stack, the heap, object layout, and the
  assembly layer that no language can hide behind.
- **Part II (ch 08-18), the languages** — sixteen memory models, each positioned
  by what it chose to automate and what it demands from you: C, C++, Rust, Go,
  Java, C#, Python, JavaScript, Swift/ObjC, COBOL, and the elders (Fortran, Lisp,
  Haskell, Erlang, Ada, Zig).
- **Part III (ch 19-22), the deep end** — allocators, the hardware memory model,
  memory security, mobile memory.
- **Part IV (ch 23 + exam)** — the grand comparison table, the toolkit, the
  practice path, and the connections into every future module.

Prerequisites: none beyond a working knowledge of any one programming language.
Time: ~15-25 minutes per chapter; the labs (each chapter's *See it yourself*)
assume a Linux box or WSL with gcc/gdb, plus Python, Node, and a browser for the
language chapters.

## The chapters

| # | Chapter | One line |
|---|---------|----------|
| 01 | [Memory from silicon](chapters/01-memory-from-silicon.md) | Capacitors, rows and bursts, alignment, endianness — why memory is shaped the way it is. |
| 02 | [The hierarchy](chapters/02-the-hierarchy.md) | The latency tower, the 64-byte line, locality — the performance physics behind everything. |
| 03 | [Virtual memory](chapters/03-virtual-memory.md) | Page tables, TLB, demand paging, COW, ASLR, swap — the grand illusion. |
| 04 | [The stack](chapters/04-the-stack.md) | Frames, prologues, calling conventions — why local variables are free. |
| 05 | [The heap](chapters/05-the-heap.md) | The everything-else machine: metadata, the three taxes, fragmentation. |
| 06 | [Object layout](chapters/06-object-layout.md) | Unboxed/boxed/tagged, headers, vtables, fat pointers — what a value really is. |
| 07 | [Assembly: the reference model](chapters/07-assembly-reference-model.md) | x86-64 and ARM64 ground truth — the layer no language can hide. |
| 08 | [C](chapters/08-c.md) | The explicit model: you own it, you free it, the language promises nothing. |
| 09 | [C++](chapters/09-cpp.md) | RAII: lifetime is scope; smart pointers as ownership arithmetic. |
| 10 | [Rust](chapters/10-rust.md) | Ownership and borrows: safety at compile time, the third way. |
| 11 | [Go](chapters/11-go.md) | Escape analysis, moving goroutine stacks, the concurrent GC. |
| 12 | [Java](chapters/12-java-jvm.md) | The generational JVM heap: headers, TLABs, collectors, the 32 GB cliff. |
| 13 | [C# / .NET](chapters/13-csharp-dotnet.md) | Value types on a GC heap: boxing, LOH, spans. |
| 14 | [Python](chapters/14-python.md) | Everything is a PyObject: refcounts, the cycle detector, pymalloc. |
| 15 | [JavaScript / V8](chapters/15-javascript-v8.md) | Hidden classes, the scavenger, the leak-hunting graph. |
| 16 | [Swift & Objective-C](chapters/16-swift-objc.md) | ARC: the compiler counts; side tables, COW, no swap. |
| 17 | [COBOL](chapters/17-cobol.md) | The memory model with no heap: PICTURE bytes, fixed records, why banking runs on it. |
| 18 | [The elders](chapters/18-the-elders.md) | Fortran, Lisp, Haskell, Erlang, Ada, Zig — where every idea was invented. |
| 19 | [Allocators](chapters/19-allocators-deep.md) | dlmalloc bins, tcache, tcmalloc, jemalloc, slab — the machinery under malloc. |
| 20 | [The hardware memory model](chapters/20-hardware-memory-model.md) | MESI, store buffers, atomics, fences — why cores disagree. |
| 21 | [Memory security](chapters/21-memory-security.md) | Overflows, UAF, heap attacks, ROP, Rowhammer — how every model fails. |
| 22 | [Mobile memory](chapters/22-mobile-memory.md) | Zygote, ART GC, Jetsam, compression — where the OS is the GC. |
| 23 | [The unification](chapters/23-unification.md) | One table for every language, the toolkit, the practice path. |

**Final exam:** [quiz/final-exam.md](quiz/final-exam.md) — 30 questions, worked
answers. Interactive version: [the site's quiz page](https://srivtx.github.io/deep-roots/quiz.html).

## The discipline (see [CONVENTIONS.md](CONVENTIONS.md))

Every chapter: a 30-second version, teaching sections that *derive* rather than
assert, a **What insiders know** section (the specifics practitioners actually
use), a **See it yourself** lab with real commands, **How to use this knowledge**,
explicit **Connections**, and an **8-question quiz** with worked answers behind a
`<details>` tag. Numbers are platform/version-marked. If a fact can be derived, it
is derived; if it must be memorized, it is flagged as such.

## What this module buys you

- **Performance debugging as physics**: which tower level, which tax, which
  instrument — the questions that organize every memory ticket.
- **Security fluency**: the entire Chapter 21 attack surface is Chapters 04-06,
  19 read adversarially; Module 02 builds directly here.
- **Polyglot competence**: the grand table lets you interview any new language in
  four questions and predict its failure modes before you write code.
- **The root view of the roadmap**: modules 02-12 (security, decompilation,
  mobile, COBOL, concurrency, compilers, databases, GPU) are this module's
  consequences — see [the roadmap](../../ROADMAP.md).
