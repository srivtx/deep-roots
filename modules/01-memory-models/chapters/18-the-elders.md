# Chapter 18 — The elders: where every memory idea was invented

> **The 30-second version.** Chapters 08–17 walked the modern menagerie forward; this
> chapter walks it backwards. Every model you just studied was *invented* by a short
> list of elder languages, under constraint: core memory at order a dollar a bit, no
> virtual memory, one CPU. Fortran (1957) gave you the static layout COBOL inherited
> (ch17), plus COMMON and EQUIVALENCE — the manual, byte-level module and union
> systems. Lisp (1958–60) invented dynamic memory itself and, because hand-managed
> free lists were hopeless against symbolic data, garbage collection. Haskell made
> laziness an allocation policy: every suspended expression is a heap object. Erlang
> made isolation a heap policy: one small heap per process, messages copied between
> them. Pascal, Ada, and Zig kept the allocator visible — passed, named, greppable.
> Part II is this chapter's descendants.

## Fortran 1957: static, because memory was the budget

Fortran (1957, IBM 704) is the *original* static model — COBOL took the same bet two
years later for the same reason. The 704: 36-bit words, at most 32K words of core,
roughly a dollar a bit — RAM priced like a building. No virtual memory (ch03
doesn't exist yet), no heap (ch05), and locals are not frames on a stack (ch04): **each variable gets one fixed home at load time and keeps it for the
whole run.**

| Question | Fortran's answer (Fortran 66/77) |
|----------|----------------------------------|
| What is a value? | A fixed cell: INTEGER, REAL, arrays at compile-time offsets. |
| Who owns it? | The program: one static area, one activation per procedure. |
| Who frees it? | Nobody — nothing is allocated after load. |
| What safety? | None on memory: subscripts and COMMON are your problem. |

The model *derives* the famous rule: **recursion is forbidden** (until Fortran 90's
`RECURSIVE`). A recursive call needs a second fresh copy of the callee's locals — a
stack — and one-home-per-variable cannot supply it. And the trap runs the other way
too: a Fortran 90+ local with an initializer (`INTEGER :: COUNT = 0`) **acquires the
SAVE attribute implicitly** — static, persisting across calls, exactly like COBOL's
WORKING-STORAGE (ch17), unlike a C local.

### COMMON and EQUIVALENCE: modules and unions, by hand

```fortran
      SUBROUTINE GRID(NPTS)
C     Fortran 77 fixed form: statements in columns 7-72
      INTEGER NPTS
      REAL BUF(1000)
      COMMON /SHARED/ BUF
C     every COMMON /SHARED/ maps onto the SAME bytes: a module
C     system made of one name plus overlay discipline
      REAL SNGL
      EQUIVALENCE (BUF(1), SNGL)
C     SNGL aliases BUF(1)'s bytes: the untagged union (ch06) --
C     no tag, no check, reinterpreting is total and silent
      END
```

The standard lays COMMON out sequentially **with no padding**. Mismatch two
routines' declarations — one says `INTEGER N, X(2)`, the next declares a `REAL*8`
over the same slot — and the second routine reads the first's bytes through a
different type at a possibly misaligned offset: silent corruption on tolerant
hardware, a bus error on strict-alignment targets. gfortran's `-falign-commons`
patches the alignment half; nothing checks the type-punning half. This is the
COMMON-block alignment bug of greybeard porting war stories.

### Column-major, because cards and columns

Fortran arrays are **column-major**: `(i, j)` of an `n x n` array sits at
`base + (j-1)*n + (i-1)` — the *first* index is stride 1. Both cited reasons are
period-correct: a deck of punch cards is a column-major table (a field = the same
columns on every card), and in the linear algebra Fortran translated, a column *is*
a vector. It is now a performance law (ch02):

```fortran
    ! Fortran 90: ALLOCATABLE -- dynamic memory, 33 years late
    real(8), allocatable :: a(:, :)   ! declaration, not a size
    allocate(a(n, n))                 ! builds the descriptor ("dope vector"):
                                      ! base, extents, strides
    do j = 1, n                       ! GOOD: inner loop = first index = stride 1
      do i = 1, n                     ! 8 consecutive elements per cache line
        s = s + a(i, j)
      end do
    end do
    do i = 1, n                       ! BAD: inner loop = second index,
      do j = 1, n                     ! stride = n*8 bytes: each access hits a
        s = s + a(i, j)               ! fresh cache line, uses one element
      end do
    end do
    deallocate(a)                     ! ALLOCATABLEs auto-free at scope exit
                                      ! (F95+) -- designed lifetime, unlike
                                      ! POINTER arrays
```

`ALLOCATABLE` (Fortran 90) is chapter 05's late arrival here: the array is a dope
vector, and because the compiler knows an ALLOCATABLE has no aliases, it optimizes
it better than POINTER arrays. The lab times both loop orders; the row-major
traversal of column-major data is the most common Fortran performance bug, and the
same bug lives in MATLAB and Julia, which inherited the layout.

## Lisp 1958–60: dynamic data, and the collector that made it fit

McCarthy's group (1958–60; "Recursive Functions of Symbolic Expressions...", 1960)
needed memory that changes shape at run time: symbols and lists built and re-wired
while the program runs. The unit is the **cons cell** — two fields, `car` and `cdr`,
each a pointer:

```
 the original cell (IBM 704): ONE 36-bit word, halved:
   +--------+----------------+------+----------------+
   | prefix |   DECREMENT    | tag  |    ADDRESS     |
   +--------+----------------+------+----------------+
   CDR = "Contents of the Decrement Register" (15 b)
   CAR = "Contents of the Address Register"    (15 b)
   -- the word's two halves WERE the two pointers; the names stuck

 modern cell, 64-bit (teaching model: 2 words; real Schemes add a
 header word so the collector knows size and shape):

        +---------------------+---------------------+
        |   car  ------------>|   cdr  ------------>|    8 B + 8 B
        +---------------------+---------------------+

 (A . B):  car -> A,  cdr -> B            the list (A B C):

   +-->+--------+--------+    +--------+--------+    +--------+--------+
       | car: A | cdr: --+--->| car: B | cdr: --+--->| car: C | cdr: nil|
       +--------+--------+    +--------+--------+    +--------+--------+
```

*Derive* why GC had to be invented here. Programs build cells by the million: every
`(cons x y)` allocates, every rewrite abandons the old cell. Stack discipline (ch04)
can't manage this — lifetimes don't nest (a returned list outlives its frame; two
variables share one tail), and a manual free list requires knowing when a cell is no
longer *reachable*, a property of the whole pointer graph, visible nowhere in the
program. Hand-managed free lists were hopeless within months. So reclamation had to
become a machine question: find the cells reachable from the roots, reclaim the
rest. Garbage collection. As McCarthy tells it in his 1979 *History of Lisp*, the
trigger was a debugger: the stepping program they were building wouldn't fit in the
machine alongside what it debugged, and hand reclamation couldn't free the abandoned
cells — automatic collection made the debugger fit.

**Stop-and-copy** (Minsky, ~1961–63) was the early answer: stop the program, walk
the reachable cells, copy each into a fresh region, leave the old region as one
giant free block — no free list, no fragmentation, allocation becomes a pointer
bump (ch05's best case). Cheney's 1970 refinement — two
pointers, scan and free, breadth-first — is exactly V8's scavenger (ch15). The term
**"GC pause"** is this era's word: one CPU, one program — when the collector ran,
everything stopped. The pause wasn't a failure mode; it was the mechanism, and
ch11/ch12's concurrent and generational engineering is decades of shrinking
inherited pain. The 1962 cost was half your memory: **Lisp 1.5 on the IBM 7090
literally split the machine in half** — program in one half of the 32K words, the
collector's copy space in the other. A 50% memory tax to have dynamic memory at
all; ch15's semispaces and ch12's survivor spaces are that split, shrunk.

Two more inventions live here. Reference counting was tried in these years (Collins
proposed it for list processing in 1960; the MIT line used it briefly) and dropped:
a cell whose cdr points back to itself keeps its own count nonzero forever — cycles
defeat counts, which is why Python (ch14) needed a cycle detector and Swift (ch16)
makes you break cycles by hand. And **Scheme (1975) put recursion on a real stack
and mandated proper tail calls** — a call in tail position reuses the caller's frame
(ch04), so a loop written as recursion runs in constant stack:

```scheme
;; Scheme: the whole memory story in five lines
(define (iota n acc)
  (if (= n 0)
      acc                          ; the answer is cons cells
      (iota (- n 1) (cons n acc)))) ; TAIL call: this frame is REUSED --
                                   ; constant stack; each (cons n acc) = 1
                                   ; new cell, previous acc now garbage
(display (iota 3 '()))              ; (3 2 1): 3 live cells, 2 dead ones
```

Allocate a cell, abandon the last one, bump and drop — the *intended* rhythm of GC
languages; ch12's "objects die young" is the same rhythm at JVM scale.

## Haskell: laziness is an allocation policy

Haskell (GHC as of 9.x) makes one design decision — laziness — and it lands
entirely on the heap: **every unevaluated expression is a heap object, a thunk: a
closure pointer plus the captured free variables:**

```
 the thunk for   let z = f x y in ... :

        +------------------+------------------+------------------+
        |  info ptr (code) |   x (free var)   |   y (free var)   |
        +------------------+------------------+------------------+
              8 B                 8 B                8 B
        forced: jump to info code, read fields, compute, overwrite
        the cell with (a redirect to) the result -- "updating" the
        thunk so later forces are free
```

`let s = sum xs` is one allocation *now*, before anything is computed. Insiders
state it as a law: **lazy programs allocate like imperative ones allocate** —
idiomatic lazy Haskell performs an allocation per value produced, the same order as the
imperative equivalent; what differs is *when* (at suspension, not production) and
*whether* (never, if the value goes unforced).
Rates look terrifying — tens of GB/s — because allocation is a bump into a
per-capability nursery; the totals are ordinary. The pathology is the thunk
*tower*: `foldl (+) 0 [1..n]` builds n suspended additions before the first one
runs — ~24 bytes each, so `n = 10^8` parks ~2.4 GB of unevaluated `+` on the heap.
The fight-back toolkit: `foldl'`/`seq`/bang patterns, then strict fields, then
`{-# UNPACK #-}`, which pastes raw words into the constructor:

```haskell
-- GHC as of 9.x
data P = P Int Int          -- boxed: cell with 2 pointers to 2 boxed Ints
                           -- (each a 3-word heap object): 5 objects total
data Q = Q {-# UNPACK #-} !Int
           {-# UNPACK #-} !Int
                           -- strict + unpacked: two raw words inside the
                           -- constructor: one object, no indirections
```

That split is boxed `Int` (pointer to a heap cell — boxing, ch06) versus `Int#`
from `GHC.Prim`: a raw machine word living in registers and frames, never on the GC
heap, never thunked. GHC's collector descends from the Lisp side of this chapter: a
generational **copying** collector (two generations by default), block-structured
heap (4 KB blocks), minor GCs copying live young data out of the nurseries, and
near-block-sized objects parked in a large-object area — marked, never copied.

Two insider terms separate the parallel from the lazy: a **thunk** is a suspended
computation on the heap; a **spark** is a pointer to a thunk parked in a
per-capability circular buffer awaiting possible parallel evaluation
(`Control.Parallel`/strategies). Sparks cost one pool slot, not heap; when their
thunk is already evaluated they *fizzle* (counted in `+RTS -s`).

## Erlang/BEAM: isolation is per-process heaps

BEAM (numbers: BEAM/OTP 26, 64-bit) answers telephone-switch fault tolerance by
making the *heap* the unit of isolation. Every process owns a private heap, created
small — default initial heap **233 words (1,864 B)** — growing and shrinking only
for itself, along a Fibonacci-like ladder (233, 373, 593, 937... ~x1.6 per step).
A process's GC is a Lisp-style copying collection over that heap alone: when it
runs, *that one process stops*; everyone else keeps executing.

Message passing **copies** the message body between heaps. Derive why, from the two
properties the designers wanted: (1) *isolation* — no pointer crosses a process
boundary, so a buggy or runaway process cannot corrupt another's data, and the
failure model stays "this process dies, everything else is unharmed"; (2)
*per-process GC* — a collector may only touch memory one process owns, so no
global stop, no write barriers, no remembered sets (ch12's generational apparatus
exists to patch the sharing BEAM forbade). The one exception: **binaries above 64
bytes live off-heap, reference-counted; the process heap holds only a tiny
ProcBin reference** — which is all that copies:

```
 BEAM/OTP 26 (64-bit): share-nothing, except the big bytes:

  process A heap       process B heap         binary area (refcounted)
  +----------------+   +----------------+     +-----------------------+
  | terms...       |   | terms...       |     |  1 MB binary          |
  | ProcBin --+    | A!|  ProcBin --+   |     |  refcount: 2         |
  +-----------|----+ Ms+-----------|----+     +-----------^-----------+
              |  g (only the small ProcBin references copy;  the 1 MB
              +--------------- binary refcount ---------------+  never
                                                       moves or copies)
```

The payoff in one sentence: **"let it crash" is a memory strategy.** A process that
leaks — heap growing without bound, mailbox filling with garbage — is not a
debugger problem but a lifecycle event: the supervisor kills and restarts it, and
the fresh process starts over at a 233-word heap. Leaks die with their process.
Chapter 22 is that sentence at OS scale: iOS's Jetsam and Android's low-memory
killer "let it crash" whole apps, because a killed process is the one reclaim
strategy that cannot leak.

```erlang
%% BEAM/OTP 26: the memory model in one loop
loop() ->
    receive
        {From, Msg} ->            % Msg was COPIED into this process's heap
            From ! {self(), Msg}, % and copies out again: no aliasing, no
            loop();               % shared mutable state, ever
        stop -> ok
    end.
%% erlang:process_info(self(), memory) on an idle process
%%   -> {memory, 2688}          (233-word floor + stack)
```

## Pascal, Ada, Zig: the allocator made visible

The third tradition never went collector-first: it kept allocation a visible,
named operation, and spent fifty years refining what "visible" means.

**Pascal (Wirth, 1970)** gave the heap manual discipline: `NEW(p)` allocates one
object of p's static type, `DISPOSE(p)` frees it. The famous dangling-dispose
problem: `Dispose(p)` frees the block but leaves `p` pointing at it — no nil-ing,
no checker — so `p^` afterwards is ch08/ch21's use-after-free, twenty years before
the term. The folklore is worse: several implementations shipped `Dispose` as a
no-op — correct results, monotonically growing heap. The era's real discipline was
Turbo Pascal's `Mark`/`Release`: `Mark(h)` records the heap position, `Release(h)`
rolls the heap back to it — **an arena: ch05's frame allocation as a language
feature**, and the direct ancestor of Zig's idiom below.

**Ada (Ada 95+)** made the danger opt-in and spelled out. Access types are typed
pointers; freeing goes through a generic you instantiate per (type, access type)
pair:

```ada
-- Ada: the escape hatch is named, typed, instantiated per use
type Node;
type Node_Ptr is access Node;    -- can point ONLY at Nodes
type Node is record
    Val  : Integer;
    Next : Node_Ptr;
end record;

procedure Free is
  new Ada.Unchecked_Deallocation(Node, Node_Ptr);
-- Free(P) exists only behind this declaration whose name says
-- "Unchecked"; shops add pragma Restrictions (No_Allocators, ...) per
-- unit to ban allocation outright. Storage pools attach per access
-- type:  for Node_Ptr'Storage_Pool use Pool;  every allocation draws
-- from Pool -- designed fine-grained control, in practice almost
-- never used.
```

Three philosophies, one fear: C (ch08) made danger the default with no checker; Ada
made safety the default with every escape hatch *named* — you cannot free
anonymously, "Unchecked" is in every call site's ancestry; Rust (ch10) made the
*checked* state the default for everything and quarantines the escape into
`unsafe` blocks the compiler can enumerate.

**Zig (0.13/0.14 — the young elder)** finishes the line: the allocator is an
explicit first-class parameter. No hidden global allocator exists; every allocating
call, including the standard library's, takes a `std.mem.Allocator`:

```zig
const std = @import("std");

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator{};
    const a = gpa.allocator();            // allocator value: pointer +
                                          // vtable (alloc, free, resize)
    var arena = std.heap.ArenaAllocator.init(a);
    defer arena.deinit();                 // Turbo Pascal's Release: free
                                          // EVERYTHING at once -- ch05's
                                          // arena, first-class
    const xs = try arena.allocator().alloc(u32, 1000);
    var list = std.ArrayList(u32).init(arena.allocator());
    defer list.deinit();
    try list.append(42);                  // the list allocates only via
    _ = xs;                               // the allocator YOU handed it
}
```

Two properties fall out. First, **an allocation is greppable**: `rg "\.alloc\("` in
a Zig codebase finds every allocation site, because no runtime allocates behind
your back — contrast Java's TLAB (ch12), Python's `PyObject_Malloc` (ch14), Go's
escape analysis (ch11), GHC's thunks (above). Second, `comptime`: generic
parameters are compile-time, so `ArrayList(u32)` is monomorphized to a concrete
layout before codegen — zero runtime cost of generics (like C++ templates, ch09),
unlike ch14's boxed-everything. What this buys over Pascal's `NEW` is choice at the
call site: the same `init` runs against a general-purpose allocator, an arena, or
a pool, with the memory behavior visible in the signature.

## The through-line: every modern idea has an address

Read the constraint, then the invention, then the descendant. Core memory at
order a dollar a bit and no virtual memory made Fortran and COBOL declare every
byte up front — static storage (ch17, and every embedded data section since).
Memory poverty plus symbolic data whose lifetimes no discipline could track made
Lisp invent the heap's *management*: reference counting was tried and
dropped for cycles, tracing was kept — every collector in ch11, ch12, ch13, ch15
descends from the 1960 list work. One CPU made the collector a total pause, and
four decades of engineering went into shrinking that inherited pause. Telephony's
fault tolerance made Erlang trade copying for isolation; mobile OSes (ch22) made
the same trade at process scale. The academic line that hated the collector's
nondeterminism — Wadler's *Linear types can change the world!* (1990), Turner's
*Elementary strong functional programming* (1995) — proposed making ownership and
lifetime type-system facts instead: that is Rust (ch10) wearing its citations.
The map, as one lineage: **static (Fortran, COBOL ch17) → manual heap (Pascal's
NEW/DISPOSE → C's malloc/free, ch08, with RAII ch09 as scope discipline over it) →
reference counting (Collins 1960, used briefly in early MIT Lisp; ch14 Python,
ch16 Swift ARC) → tracing GC (McCarthy 1960, Minsky's copying collector; ch11 Go,
ch12 Java, ch13 CLR, ch15 V8) → ownership (Wadler/Turner's linear types; ch10
Rust).** Nothing in chapters 08–17 is a new idea; they are distribution decisions
for ideas that all existed by 1995, most by 1965.

## What insiders know

- **COMMON mismatch is type-punning at the linker.** The standard lays out COMMON
  sequentially, no padding: `INTEGER N; REAL X(2); REAL*8 D` puts D at offset 12 —
  misaligned for 8-byte loads. gfortran's `-falign-commons` pads it; no compiler
  checks that two routines' declarations agree, and the bug only reproduces on
  strict-alignment hardware.
- **Fortran's initialized locals are static.** Fortran 90+: `INTEGER :: N = 0`
  acquires SAVE — persists across calls. Fortran 77 said the *opposite* surprise:
  without SAVE the value became undefined between calls. Both surprise C
  programmers, in opposite directions.
- **ALLOCATABLE arrays carry a dope vector** — base, extents, strides — and get
  no-alias optimizations POINTER arrays don't; the descriptor is exactly what
  crosses the Fortran/numpy (f2py) boundary.
- **CAR/CDR are register names.** The 704's 36-bit word had 15-bit address and
  decrement fields; a cons pair was one word halved: *Contents of the Address
  Register* / *Contents of the Decrement Register*.
- **Lisp 1.5 ran on half a machine** — program in one half of the 7090's 32K
  words, the collector's copy space in the other: the original 50% memory tax
  on dynamic memory. And McCarthy's GC origin story is a debugger story (History
  of Lisp, 1979): the stepping program wouldn't fit alongside what it debugged;
  automatic reclamation made it fit.
- **BEAM's binary threshold is 64 bytes.** At or below, a heap binary copies with
  the message like any term; above, the bytes are off-heap and refcounted, and
  only a tiny ProcBin reference moves — a process holding a few small references
  can retain gigabytes.
- **GHC's `-fno-cse` exists for memory, not speed.** CSE merges two identical
  subexpressions into one shared thunk — unintended sharing that retains (and
  serializes evaluation of) structures you meant to compute twice and drop.
  Killing the sharing costs duplicate work and saves the heap.
- **Erlang heap sizes climb a Fibonacci-like ladder** (233, 373, 593, 937 words,
  ~x1.6 per step, not x2), and an idle process reports ~2.7 KB total (BEAM/OTP
  26, 64-bit) — the arithmetic behind "millions of processes".

## See it yourself

Three labs, one per elder with a package-manager toolchain.

**1. gfortran: the column-major tax.** Install `gfortran` (14.x here), save the
loop-order program as `col.f90`:

```console
$ gfortran -O2 col.f90 -o col && ./col
fast  (j outer, i inner):    0.42 s
slow  (i outer, j inner):    3.11 s
```

Representative x86-64 Linux numbers; absolute times vary, the ~7x ratio is ch02's
cache-line argument made visible. Also compile a COMMON boundary overrun (a write
past `BUF(1000)`) with `-fcheck=bounds` and watch the runtime trap fire — then
note that *nothing* fires for a mismatched COMMON declaration, only for
subscripts.

**2. GHCi: count allocations per expression.** Install `ghc` (9.x), run `ghci`,
make the runtime count bytes:

```console
Prelude> :set +s
Prelude> let xs = [1 .. 10^7 :: Int] in foldl  (+) 0 xs
(1.24 secs, 240,019,224 bytes)
Prelude> let xs = [1 .. 10^7 :: Int] in foldl' (+) 0 xs
(0.09 secs,    169,472 bytes)
```

`foldl`'s accumulator is a tower of thunks: ~24 B x 10^7 ≈ 240 MB of suspended
additions (`foldl'` forces each step and allocates almost nothing). That delta is
laziness-as-allocation in one number. GHCi is unoptimized; at `-O2` strictness
analysis often does this for you — which is why such leaks surface only in
production profiling.

**3. erl: the price of per-process heaps.** Install `erlang` (OTP 26), run `erl`:

```console
1> erlang:process_info(self(), memory).
{memory,2688}
2> Ps = [spawn(fun() -> receive _ -> ok end end) || _ <- lists:seq(1,10000)].
3> lists:sum([begin {memory,M} = erlang:process_info(P, memory), M end || P <- Ps]).
28162864
```

Ten thousand processes, ~2.8 KB each, ~28 MB: the 233-word floor times ten
thousand. Extend it: spawn 100,000 and watch the total scale linearly;
`erlang:garbage_collect(P)` one process and see no other process's `reductions`
move — the per-process stop, live. Send yourself a 10,000-byte binary and
`process_info(self(), binary)` shows the off-heap refcounted entry that never
copied into your heap.

## How to use this knowledge

- **Diagnose "Fortran is slow" and its descendants.** MATLAB, Julia, and f2py-wrapped
  numeric Python inherited column-major layout; a wrong-order loop nest costs 5-10x
  (the lab), and the fix is loop interchange, never hardware.
- **Port legacy scientific code safely.** When you meet COMMON and EQUIVALENCE in a
  1970s codebase, the failure mode is *declared layouts disagreeing* — check the
  declarations routine by routine before touching anything at runtime.
- **Answer "where was GC invented?" with the story, not the word.** McCarthy,
  1958-60, a debugger that wouldn't fit, a half-machine split for copy space; the
  follow-up — "why doesn't BEAM ever stop the world?" — is the share-nothing
  derivation, and it lands in interviews.
- **Pick leak strategy by unit of isolation.** BEAM's "leaks die with the process"
  and mobile's "leaks die with the app" (ch22) tell you when restarting beats
  fixing: when the leak's owner is a lifecycle unit. Java/JS services lack that
  unit, so they get heap dumps and profilers instead.
- **Design services on the arena idiom.** One request = one arena, `defer
  arena.deinit()` at the end — Turbo Pascal's Mark/Release idea, now the standard
  shape for per-request memory in Zig servers (and why ch19's allocators live as
  a library layer).

## Connections

- **ch02** — the column-major loop-order tax is cache lines, derived and timed here.
- **ch04** — Fortran's forbidden recursion = no frames; Scheme's tail calls = frame
  reuse; SAVE-locals vs frame locals.
- **ch05** — free lists vs Minsky's copying; Mark/Release and Zig's arena are frame
  allocation as language feature.
- **ch06** — EQUIVALENCE as the untagged union; GHC's boxed `Int` vs `Int#`.
- **ch08/09** — Pascal's NEW/DISPOSE as C's malloc/free ancestor; RAII as scope
  discipline over the same heap.
- **ch10** — Ada's named unchecked deallocation vs Rust's default-checked
  ownership; Wadler/Turner as Rust's citation trail.
- **ch11/12/13/15** — every collector: Minsky's stop-and-copy, Cheney's scan/free
  pointers, the Lisp 1.5 semispace split.
- **ch14/16** — the RC branch: Collins 1960, tried and dropped for cycles.
- **ch17** — the static branch: Fortran had the completed-contract model first.
- **ch22** — "let it crash" as memory strategy, at OS scale.
- **ch23** — the unification table is this chapter's through-line, systematized.
- **ROADMAP** — the queued functional-programming and mainframe modules start here.

*Forward links — where the rest of the program picks this chapter up:*

- **Module 19 ch01 (the lambda calculus)** — Lisp and Haskell's root: the three-conversion machine that every elder language is sugar on.
- **Module 09 ch04 (actors and channels)** — Erlang's model in the concurrency lineage: ownership transfer as a message.
- **Module 21 ch01 (the LCF birth)** — ML was built to host a proof checker — the elders list contains the birth of both the language and the verifier.

## Quiz

1. Fortran through Fortran 77 forbids recursive procedures outright. Give the
   storage-model reason, and say what Fortran 90's `RECURSIVE` keyword must add to
   lift the ban.
2. A BEAM process sends two messages: one containing a 50-byte binary, one
   containing a 50,000-byte binary. Which bytes actually get copied between the
   heaps, what does the receiver's heap hold for the large one, and who is allowed
   to free those bytes?
3. In a 64-bit Scheme where a cons cell costs 24 bytes (header + car + cdr), how
   many bytes do the cons cells of `(list 'A 'B 'C)` occupy, what is in the final
   cell's cdr, and how many *new* cells does `(append (list 'A 'B) (list 'C 'D))`
   allocate — of how many in the result?
4. A COMMON block is declared `COMMON /BLK/ N, X, D` with `INTEGER N`, `REAL X(2)`,
   `REAL*8 D`. Per the standard, at what byte offsets do `X` and `D` sit, where
   would the equivalent C struct (ch06) put `D`, and which compiler flag exists for
   exactly this gap?
5. A Fortran 90 subroutine contains `INTEGER :: COUNT = 0` and `COUNT = COUNT + 1`
   and is called five times, printing 1, 2, 3, 4, 5. The C programmer who wrote it
   expected five 1s. What rule did he miss, and what does the Fortran 77 rule say
   happens instead?
6. In GHCi (no `-O`), `let xs = [1 .. 10^7 :: Int] in foldl (+) 0 xs` reports about
   240,000,000 bytes allocated for the fold. What objects are those bytes, roughly
   how big is each, and give two fixes — one at the call site, one the optimizing
   compiler applies at `-O2`.
7. An Erlang logging process keeps a small map of the last 500 requests, each
   message having contained a 1 MB binary. The process's `memory` reads 2 MB, but
   the node's binary memory reads ~500 MB. Why is the big memory not in the
   process's heap, who is retaining it, and name two repairs.
8. V8's scavenger (ch15) and the JVM's young generation (ch12) both copy live young
   objects between two spaces. Name the elder whose machine layout they inherit,
   the 1970 refinement both use to do the copying, and the one structural change
   that stops them paying that elder's 50% memory tax.

<details>
<summary>Answers</summary>

1. **One activation per procedure.** Every variable has exactly one static home; a
   recursive call would need a second fresh copy of the callee's locals while the
   first still lives — a stack of frames (ch04), which static allocation can't
   provide. `RECURSIVE` (Fortran 90) opts a procedure into frame-based locals;
   recursion becomes stack discipline, and non-RECURSIVE code keeps the static —
   and more optimizable — layout.
2. **The 50 bytes copy; the 50 KB doesn't.** Binaries at or below 64 bytes are heap
   binaries — ordinary term data, copied with the message. Above 64 bytes the bytes
   live off-heap, refcounted; the copy moves only a small ProcBin reference into
   the receiver's heap. Those bytes are freed when the last reference disappears —
   refcount drop when a referencing process dies or drops the ref — never by the
   owning process's GC.
3. **72 bytes; nil; 2 of 4.** Three cells at 24 B = 72 B; the last cdr holds the nil
   pointer. `append` copies the *first* list's spine (2 fresh cells for A and B)
   and *shares* the second argument's structure: 4 cells in the result, only 2
   newly allocated. That sharing is also why manual freeing by traversal is wrong
   in Lisps — two owners, one tail.
4. **X at 4, D at 12; C puts D at 16.** The standard adds no padding: N (4 B) then
   X (8 B) run sequentially, so D starts at 12 — not 8-byte aligned. C inserts 4
   padding bytes to align the double at 16 (ch06). gfortran's `-falign-commons`
   is the flag for the COMMON-side gap; nothing checks the type-mismatch side.
5. **Initialization implies SAVE (Fortran 90+).** An initialized local acquires the
   SAVE attribute — one static cell, `= 0` runs once at load, the value persists
   across calls like COBOL WORKING-STORAGE (ch17). Fortran 77 said the opposite
   surprise: without SAVE, an initialized local became *undefined* after return —
   relying on it across calls was illegal.
6. **A tower of 10^7 suspended additions.** `foldl` pairs lazily: each step builds
   a thunk for `(acc + x)` — a closure object of ~24 bytes (info pointer +
   accumulator + element) — and no addition runs until the final force, so
   ~10^7 x 24 B ≈ 240 MB of thunks accumulates. Fixes: `foldl'` (or `seq`/bang
   patterns) forces the accumulator per step; at `-O2` GHC's strictness analysis
   proves the accumulator strict and never builds the tower.
7. **Refc binaries are retained by tiny on-heap references.** Each 1 MB binary is
   off-heap and refcounted (> 64 bytes); the process heap holds only small
   references inside its 2 MB map — but a reference is enough to hold the
   refcount above zero, so ~500 MB of binary area stays live. Repairs: drop the
   references (truncate/delete the map, keep only extracted small fields), or
   GC/`erlang:hibernate` the process to prove the refs die; copying a < 64 B
   fragment out (`binary:part`) breaks the link at intake.
8. **Lisp 1.5's split machine; Cheney 1970; nurseries are small.** The two spaces
   are the elder's program-half and copy-half — but Lisp 1.5 dedicated *half the
   machine* to copy space. Both modern collectors use Cheney's breadth-first copy
   (scan/free pointers), and instead of splitting memory 50/50 they confine the
   young generation to a small fraction of the heap (V8's semispaces, the JVM's
   8:1:1 survivor spaces, ch12), paying the copying tax only on short-lived
   objects.

</details>
