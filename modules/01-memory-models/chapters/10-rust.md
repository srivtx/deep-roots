# Chapter 10 — Rust: ownership, borrows, and the third way

> **The 30-second version.** C frees by hand (ch08); C++ offers RAII but a silent
> type system (ch09); the GC languages hire a runtime (ch11–ch16). Rust's third way:
> make *who owns each value* something the compiler proves. Every value has exactly
> one owner; assignment moves; access borrows. Break the aliasing × mutation
> invariant and you get a compile error, not a CVE. The tracking costs zero at run
> time — no GC thread, no refcount unless you ask. Safe Rust can still *leak* (Rc
> cycles, `Box::leak`), but it cannot *corrupt*.

## The third way: ownership as a compile-time fact

Part II's four questions — value, owner, freer, safety — get unusual answers here.
C: bytes you own and free, no safety (ch08). The GC family — Java's generational
heap is the archetype (ch12): references into a runtime-owned heap, where safety
costs a collector that can find every pointer. Rust:

- A **value** is laid out like C laid it: integers, structs, arrays inline, no
  per-object header (ch06).
- **Ownership** is a compile-time fact: every value has exactly one owner at every
  point in the program.
- **Freeing** happens at the owner's scope end: the compiler inserts destructor
  calls there, which free the memory — RAII from ch09, enforced language-wide. You
  cannot forget to free a buffer, because you were never the one doing it.
- **Safety** follows: use-after-free, double-free, dangling pointers are *type
  errors* here, caught before the program runs.

The cost model is the crucial claim: ownership is a *static* analysis — no GC, no
tracing, no runtime that knows where pointers live, and the same destructor calls a
C++ compiler emits (ch09). The price is compile-time borrow-checker errors: debug at
build time instead of at 3 a.m.

## Affine by default: moves are free, copies are earned

Chapter 09 introduced affine types — values usable *at most* once. Rust makes that
the default for every type that owns something:

```rust
fn main() {
    let a = String::from("hello"); // a OWNS: stack header (ptr, cap, len) + heap bytes
    let b = a;                     // MOVE: header memcpy'd to b; binding `a` is dead
    // println!("{}", a);          // error[E0382]: use of moved value: `a`
    println!("{}", b);

    let x = 5_i32;                 // i32 is Copy
    let y = x;                     // bitwise copy; x remains perfectly usable
    println!("{} {}", x, y);       // copies are opt-in via the Copy trait
}
```

A move compiles to the same instructions as a copy — often nothing after
optimization. The difference is what the compiler *believes*: `a` refers to nothing
now, and using it is E0382.

Contrast C++ (ch09): `auto b = std::move(a)` leaves `a` alive in a "valid but
unspecified state" — a zombie, because `a` is still an object whose destructor must
run. Rust moves the whole ownership: the source binding dies, and using it is a
compile error, never a silent zombie.

The `Copy` rule is mechanical:

- **`Copy` requires**: every field `Copy`, and no `Drop` implementation.
- **What is `Copy`**: integers, floats, `bool`, `char`, `&T`, tuples/arrays of `Copy`,
  fn pointers — plain data, memcpy-complete.
- **What is not, and why**: `String`, `Vec<T>`, `Box<T>`, `Rc<T>`, `File` — each owns
  a resource with a destructor. A bitwise copy would make *two owners of one
  buffer*, and both destructors would free it: the double-free of ch08.

## Borrows and lifetimes: proving the aliasing × mutation invariant

Ownership alone would be unusable — no function could look at a value without
taking it. Borrowing is the escape: a temporary, checked view, in two mutually
exclusive kinds:

| You hold | Count | Reads | Writes |
|---|---|---|---|
| `&T` shared | any number, simultaneously | yes | no |
| `&mut T` unique | exactly one | yes | yes |

The invariant is the XOR: at any moment a value has either many readers or exactly
one writer — never a writer with live readers, never two writers:

```
aliasing  x  mutation  =  what every memory-safety bug needs

  use-after-free  = read a freed object    -> reader outlived owner
  data race       = 2 threads, >= 1 write  -> two "writers"
  iterator inval. = mutate while reading   -> writer + reader
```

A **data race** is precisely "two accesses to one location, at least one a write, no
synchronization" (ch20 has the hardware half). The invariant makes that combination
*unrepresentable*: `&mut T` cannot coexist with any other borrow. Add `Send`/`Sync`
and a data race in safe Rust requires `unsafe` to even attempt.

The everyday form is E0502:

```rust
fn main() {
    let mut v = vec![1, 2, 3];
    let first = &v[0];     // shared borrow of v begins
    v.push(4);             // error[E0502]: cannot borrow `v` as mutable
                           //   because it is also borrowed as immutable
    println!("{}", first); // borrow still live HERE (its last use)
}
```

Why illegal: `push` may reallocate (ch05), leaving `first` dangling. The checker
doesn't know about realloc — it enforces the simpler invariant that covers it.

**NLL (non-lexical lifetimes)**, stable since Rust 1.31 (2018 edition), keeps this
tolerable: a borrow runs from creation to *last use*, not to the end of its lexical
block. Print `first` before `v.push(4)` and it compiles — the reader is dead when the
writer arrives. (Pre-2018 Rust checked scopes, not uses; NLL is the fix.)

### Lifetimes are constraints, not directives

The `'a` in `fn longest<'a>(x: &'a str, y: &'a str) -> &'a str` extends nothing. It
is a *constraint* for the solver: "the output is valid for some region `'a` where
both inputs are." The checker *proves* every call site against it and *infers* the
regions; you annotate only signatures, where inference is ambiguous. In a body you
cannot tell the compiler anything about lifetimes — you can only fail its proof. Two
codes, two failure modes:

- **E0106, missing lifetime specifier** — signature ambiguity: `fn f(a: &str,
  b: &str) -> &str`. Which input backs the output? You must name the relationship;
  each call site is then checked.
- **E0597, borrowed value does not live long enough** — body-level dangling, with an
  anatomy worth memorizing:

```rust
fn main() {
    let r;
    {
        let x = 42;
        r = &x;
    }                 // x is dropped HERE
    println!("{}", r);
}
```

```
error[E0597]: `x` does not live long enough
 --> src/main.rs:6:13
  |
5 |         let x = 42;
  |             - binding `x` declared here
6 |         r = &x;
  |             ^^ borrowed value does not live long enough
7 |     }
  |     - `x` dropped here while still borrowed
8 |     println!("{}", r);
  |                    - borrow later used here
```
*(rustc 1.7x; exact wording drifts between versions.)*

Read it as a three-point story: **span 1** the borrow is created, **span 2** the
owner dies, **span 3** the borrow is *used* — the error is that 3 > 2. The pipe
diagram is the whole theory of dangling pointers, drawn by the compiler, and
`rustc --explain E0597` prints a tutorial on this error class. `--explain` works
for every `error[Exxxx]` code and is the toolchain's most underused feature.

## The heap is opt-in: Box, Vec, and storage in the niches

Rust is stack-first: nothing is boxed implicitly — the heap is opt-in.

```rust
use std::mem::size_of;

fn main() {
    let b: Box<u8> = Box::new(7);               // 1 byte on the heap, pointed at
    assert_eq!(size_of::<Box<u8>>(), 8);        // the Box itself is ONE pointer
    assert_eq!(size_of::<Option<Box<u8>>>(), 8); // ...and so is Option of it
    assert_eq!(size_of::<Vec<u8>>(), 24);       // ptr + cap + len, 3 machine words
}
```

`Box<T>` is the minimal heap type — one owner, one pointer, destructor frees the
pointee — and `size_of::<Box<T>>() == 8` on 64-bit for *every* `T` from `u8` to a
4 KB struct: the `unique_ptr` of ch09 with the arithmetic in the type system.
`Vec<T>` is the ch06 triple in the flesh:

```
   Vec<T>: 24 bytes (on the stack usually; repr(Rust) — sizes observed on
   rustc 1.7x, 64-bit; typical, not an API guarantee)

   +----------------+   +----------------------+----------------------+
   | ptr    (8 B)   |   | 0   1   2   3   4    |   spare slots        |
   | cap    (8 B)   |-->| [T] [T] [T] ...      |   ...up to cap       |
   | len    (8 B)   |   +----------------------+----------------------+
   +----------------+      ^-- heap block: cap * size_of::<T>() bytes; first len
                             slots initialized (ch05: malloc'd, not calloc'd)
```

Same 24 bytes as `String` — a `String` *is* a `Vec<u8>` with a UTF-8 invariant. `push`
with `len == cap` doubles the capacity (realloc-or-move, amortized O(1) — ch05), and
the destructor frees the buffer exactly once, because there is exactly one owner.

### The niche trick: enums that cost nothing

Rust enums are the tagged unions of ch06 plus one refinement that changes the
economics: the compiler hunts *niches* — bit patterns a type can never produce — and
parks the discriminant in them. `&T` is never null, so all-zeroes is a free place to
say "no value": `Option<&T>` is the **same size** as `&T`, 8 bytes, with `None`
*being* the null pattern — documented as a *guarantee* for `Option<&T>`,
`Option<&mut T>`, `Option<Box<T>>`, `Option<NonNull<T>>`, and fn pointers. The
concrete number:

```rust
assert_eq!(size_of::<Box<u8>>(), 8);          // a pointer
assert_eq!(size_of::<Option<Box<u8>>>(), 8);  // STILL a pointer — tag rides the niche
```

Compare C++'s `std::optional<unique_ptr<uint8_t>>`: pointer + engaged flag + padding
= 16 bytes (ch06, ch09). Rust's optionality lives *inside* the pointer — null was
the billion-dollar mistake, so Rust deletes it and reclaims its bit pattern as the
`None` tag.

## Sharing: Rc, Arc, and the poor man's GC

One owner per value is the law — until data is genuinely shared: two parents, a cache
hit twice, a tree node in a map. Then Rust offers *explicit* reference counting, met
in ch09 as `shared_ptr`: `Rc<T>` (single-threaded) and `Arc<T>` (atomic) wrap the
value in a heap block with inline counts.

```
   Rc<String> handle                    heap block (the RcBox<String> allocation)
   (one machine word)   +---------------+----------------+-------------------------+
   +----------------+   | strong: usize | weak:   usize  | String payload (24 B)   |
   | ptr  (8 B)     |-->|        (8 B)  |         (8 B)  | ptr | cap | len | "..." |
   +----------------+   +---------------+----------------+-------------------------+
   handle: 8 B             RcBox<String> total: 16 B header + 24 B payload = 40 B

   handle.clone()      -> strong += 1     (Rc: plain add; Arc: atomic add)
   last drop           -> strong == 0: payload dropped; weak == 0: block freed
   Rc::downgrade()     -> weak   += 1: keeps the BLOCK alive, not the value
```

The counts are *inline with the value* — one allocation, unlike Python's per-object
refcount word (ch14). Cloning an `Rc` never touches the payload: one integer add, one
pointer copy. `Rc` uses *plain* increments and is `!Send`/`!Sync` (ship one to a
thread: E0277) — two threads doing `strong += 1` on one word is itself a data race
(ch20). `Arc` uses atomic read-modify-writes, each a cache-line ping-pong (ch02).
That one word, atomics or not, is the whole `Rc`/`Arc` difference — chosen
*explicitly*, unlike Swift's compiler-inserted ARC (ch16): refcounting is never
ambient; a GC-ish feel is assembled:

```rust
use std::rc::{Rc, RefCell};

fn main() {
    let shared = Rc::new(RefCell::new(vec![1, 2, 3]));
    let a = Rc::clone(&shared);      // strong: 1 -> 2, no payload copied
    let b = Rc::clone(&shared);      // strong: 2 -> 3
    a.borrow_mut().push(4);          // run-time writer check; released at
                                     // end of statement
    println!("{:?}", shared.borrow()); // [1, 2, 3, 4]; strong hits 0 at scope
}                                      // end -> vec dropped, block freed
```

`Rc<RefCell<T>>` is the famous **"poor man's GC"**: any number of owners, mutation on
demand, values die with the last handle. `RefCell` is needed because `Rc<T>` hands
out `&T` — shared, immutable — while mutation demands exclusivity; interior
mutability resolves that by *relocating the check*:

- **`Cell<T>`** for `Copy` types: no checking — `get`/`set` are memcpys through
  `UnsafeCell`; `Cell<i32>` is as fast as a plain `i32`.
- **`RefCell<T>`** for any `T`: an `isize` borrow flag — 0 un-borrowed, positive =
  live `&T` readers, −1 = one `&mut T` writer. Violations *panic* ("already
  mutably borrowed: BorrowError", as of 1.7x) instead of failing to compile.

The chapter's thesis in miniature: the invariant is *the same* — the borrow checker
proves it statically, `RefCell` checks it dynamically, and E0502 becomes a 3 a.m.
panic. Threads repeat the move: `Arc<Mutex<T>>` relocates the check into lock
contention.

## The edges: unsafe, leaks, and what safe really promises

**`unsafe`** is the acknowledged escape hatch. Inside an `unsafe` block you may
dereference raw pointers (`*const T`/`*mut T` — thin, unchecked, 8 bytes, no
lifetimes), call unsafe functions, implement unsafe traits — taking on the obligation
the checker normally carries: aliasing rules, no data races, no dangling. Those rules
have a formal model the ecosystem actually runs: **Stacked Borrows** and its
successor **Tree Borrows** (Ralf Jung et al.) define when a raw-pointer-derived
access *invalidates* aliasing references, and **Miri** (`cargo +nightly miri run`)
interprets unsafe code under that model, flagging undefined behavior real hardware
lets slip. Well-shaped codebases keep unsafe a thin shell — data structure, FFI — wrapped in
a safe API; the shell gets the review hours.

**Leaks are safe.** Rust guarantees no memory *unsafety* (use-after-free,
double-free, dangling, races) — not absence of leaks. Three ways to leak in safe
code:

1. **Rc cycles**: two values holding each other's strong count stay at 1 forever;
   destructors never run. Python built a cycle detector for this (ch14); Rust's
   answer is `Rc::downgrade` → `Weak<T>` — parent strong, child weak.
2. **`Box::leak(x)`**: deliberately returns `&'static mut T`, never dropped — the
   standard move for once-per-process config tables and arenas.
3. **`std::mem::forget(x)`**: consumes the value, runs no destructor. Safe on
   purpose since Rust 1.0 — safe leaks demonstrably exist (item 1), so the library
   does not pretend destructors are guaranteed, and safe APIs are written
   leak-tolerant.

**The security payoff** is why the industry cares. Microsoft's MSRC (BlueHat 2019,
~a decade of CVE data): roughly **70% of Microsoft's CVEs are memory-safety bugs**.
Google Chromium (2020): **at least 70% of serious security bugs** are memory-safety
bugs. Overflow, use-after-free, type confusion — the exploit starter pack of ch21 —
is what the borrow checker makes unrepresentable in safe code. That ~70% is the
ceiling disciplined C/C++ teams cannot get under, and why Android, Windows, and
Chrome keep rewriting attack surface into Rust. Safe Rust eliminates the whole class
in safe code; the `unsafe` shell is where auditing concentrates (ch21).

## What insiders know

- **`Copy` is a marker trait with no methods and a hard rule: no `Drop`.** Nothing
  *runs* for a copy — the compiler just doesn't invalidate the source. Any type with
  a destructor is disqualified: `Cell<i32>` is `Copy`, `RefCell<i32>` is not (memcpy
  would duplicate live borrow state). `&T` is `Copy`; `&mut T` is not — exclusivity
  is not copyable.
- **`Rc` is single-threaded to the bone** — plain `+= 1`, `!Send`/`!Sync`; `Arc` is
  the same layout with atomic RMWs. The internal weak count is stored **one higher**
  than `Rc::weak_count()` reports — the extra 1 belongs to the strong side (as of
  1.7x).
- **`Box<T>` is `Clone` only when `T: Clone`** — the clone deep-copies the payload.
  `Rc<T>` is `Clone` for *any* `T`: it bumps the count. Same operator, different
  cost — `Rc<String>` clone is one integer; `Box<String>` clone is a heap round-trip.
- **`&[T]` and `&str` are fat pointers — 16 bytes = (ptr, len)**, the ch06 slice.
  But `&[T; 1000]` is *thin* (8 B): the length is in the type. `&dyn Trait` is
  (ptr, vtable), also 16 — why `&[u8]` beats `&Vec<u8>` in signatures.
- **`Cow<'a, str>`** (clone-on-write) is the borrow-vs-own enum:
  `Borrowed(&'a str) | Owned(String)`, 32 bytes on 64-bit (observed 1.7x) —
  zero-copy until you mutate. Ch03's copy-on-write pages, per-value.
- **Drop order: locals in reverse declaration order — struct *fields* in declaration
  order.** The local rule matches C++ (ch09); the field rule is the *opposite* (C++
  destroys members in reverse). Port a drop-sensitive C++ struct and this bites.
- **`Pin` exists because of self-referential structs.** An `async` future's locals
  share one allocation, and some hold pointers to *other fields of it* — move it and
  they dangle. `Pin<Box<T>>` forbids the move, which is why async Rust leaks `Pin`
  into everyone's signatures.

## See it yourself

Reproducible with a stock `rustup` install (stable, Rust 2021 edition; outputs
observed on rustc 1.7x, 64-bit — prose drifts between versions, error codes do not).

**1. Sizes, no faith required.** `cargo new sizes && cd sizes`, replace
`src/main.rs`:

```rust
use std::mem::size_of;
use std::rc::Rc;

fn main() {
    println!("{}", size_of::<Box<u8>>());          // 8
    println!("{}", size_of::<Option<Box<u8>>>());  // 8   <- niche: same as the Box
    println!("{}", size_of::<Vec<u8>>());          // 24  <- ptr + cap + len
    println!("{}", size_of::<Option<Vec<u8>>>());  // 24  <- niche strikes again
    println!("{}", size_of::<&[u8]>());            // 16  <- fat pointer
    println!("{}", size_of::<&[u8; 100]>());       // 8   <- thin: len in the type
    println!("{}", size_of::<Rc<u8>>());           // 8   <- the handle
}
```

`cargo run`. Notice which numbers `Option` fails to change — each is a niche you
just verified.

**2. Meet E0382 and E0502.** Put this in `src/main.rs`:

```rust
fn main() {
    let s = String::from("hello");
    let t = s;
    println!("{}", s);      // E0382

    let mut v = vec![1, 2, 3];
    let first = &v[0];
    v.push(4);              // E0502
    println!("{}", first);
}
```

`cargo build 2>&1 | head -40`, then `rustc --explain E0382 | less`. In E0382's
output, find "value moved here" (at `let t = s;`) and "value used here" (the
`println!`) — the three-point story again. Delete the two offending lines one at a
time: each error vanishes independently, two separate proofs failing.

**3. (Optional, deeper)** `cargo +nightly miri setup && cargo +nightly miri run` on
a project with a deliberately bad `unsafe` block — Miri names the undefined behavior
that release-mode hardware would have let sail through.

## How to use this knowledge

- **Errors as a diagnosis map.** E0382: an owner moved — find the move span. E0502:
  aliasing × mutation — find the other borrower's *last use*; the fix is often
  reordering two lines. E0597: the pipe diagram *is* the fix. Run `--explain` before
  Stack Overflow.
- **Performance choices with numbers.** `&str` (16 B) vs `String` (24 B + heap) vs
  `Rc<str>` (8 B handle + heap + counts) is a decision about memcpy, allocation, and
  atomic counts — derivable, not folklore. Prefer borrows at API boundaries;
  `Rc`/`Arc` only when ownership truly is shared.
- **A design smell detector.** `Rc<RefCell<T>>` in a tree or graph means "shared and
  mutable": find the cycle, consider `Weak` children. The threaded version is
  `Arc<Mutex<T>>` under contention (ch02, ch20).
- **Security review triage, and the interview answer.** "It's Rust, we're
  memory-safe" — what sits outside the safe subset? Count `unsafe` blocks
  (`cargo geiger`), run Miri in CI on them (ch21). And "what does the borrow checker
  check?" — *the aliasing × mutation invariant over regions computed from last use
  (NLL)*, incidentally the exact precondition of a data race.

## Connections

- **ch04 (the stack)** — ownership's destructor calls live in the frame epilogue;
  drops are a scope fact, not a runtime event.
- **ch05 (the heap)** — `Vec`'s doubling growth and `Rc`'s one-block layout are
  allocator mechanics (bins, realloc-vs-move).
- **ch06 (object layout)** — tagged unions, fat pointers, niches reclaimed:
  `Option<&T> == &T` is the payoff.
- **ch08 (C)** — every rule here is compile-time C discipline; E0382 is the
  double-free caught earlier.
- **ch09 (C++)** — RAII language-wide; moves without the zombie; `Arc` vs
  `shared_ptr`, identical inline counts.
- **ch11–ch16** — GC runtimes buy safety; Rust wagers the type system can pay.
  Swift's ARC (ch16) is the closest cousin.
- **ch20** — the borrow invariant is the software half of the data race; `Arc`'s
  atomics the hardware half.
- **ch21** — the ~70% CVE statistic, the classes safe Rust retires, auditing the
  `unsafe` shell.
- **ch23** — Rust anchors the compile-time-enforcement column; region inference goes
  to the queued compilers module (ROADMAP.md).
- **Queued cybersecurity module** — exploits needing memory unsafety, and attacking
  safe-Rust targets.

*Forward links — where the rest of the program picks this chapter up:*

- **Module 19 ch03 (the safety theorem)** — the borrow checker is an affine type system: this chapter's rules are the visible surface of a machine-checked proof.
- **Module 21 ch04 (separation logic)** — the proof-theoretic reading: ownership is the decidable fragment of Reynolds' logic, NLL is constraint solving, Miri is the operational model.
- **Module 09 ch03 (lock-free)** — Send and Sync are the type system's concurrency proof rules — the compiler's answer to this chapter's 'who may share what.'

## Quiz

**1. (Concept)** Your teammate proposes making `String: Copy` "because memcpy works
on it." What breaks? State the compiler's rule for what may be `Copy`, and name one
type that is `Copy` despite holding a pointer.

**2. (Concept)** True or false: writing `fn f<'a>(x: &'a str) -> &'a str` tells the
compiler to keep the input alive longer. Justify in terms of constraints vs.
directives; where can't you write lifetime annotations at all?

**3. (Predict the numbers)** On 64-bit rustc 1.7x: `size_of` of `Box<u8>`,
`Option<Box<u8>>`, `Vec<u8>`, `Option<Vec<u8>>`, `&[u8]`, `&[u8; 7]`, and the
`Rc<u8>` handle. Bonus: how many heap bytes does one `Rc<u8>` allocate (strong +
weak + payload + padding)?

**4. (Predict the output)** `Loud(&'static str)` prints "drop {name}" in its `Drop`.
`main` declares `let _a = Loud("a"); let _b = Loud("b");` then
`let _p = Pair(Loud("x"), Loud("y"));` with `struct Pair(Loud, Loud);`. Write the
exact output lines, in order.

**5. (Bug diagnosis — does this compile?)**

```rust
fn main() {
    let s = String::from("deep");
    let t = s;
    let u = s;
    println!("{} {} {}", t, u, s);
}
```

If not: which error code, at which line, and two different fixes that both compile.

**6. (Bug diagnosis — the leak)** Two `Rc<Node>`s, `a` and `b`, with
`a.next = Some(Rc::clone(&b))` and `b.next = Some(Rc::clone(&a))` through `RefCell`.
No `Drop` printouts appear at exit. Why is nothing freed, why is this *safe*, and
what is the standard fix?

**7. (Bug diagnosis — compile time vs. run time)**

```rust
use std::cell::RefCell;
fn main() {
    let cell = RefCell::new(vec![1, 2, 3]);
    let w = cell.borrow_mut();
    let r = cell.borrow();
    println!("{:?} {:?}", w, r);
}
```

Does this compile? What happens at run time, and with what error class? Name the
compile-time error code you'd get instead if `cell` were a plain `Vec` and you wrote
the analogous `&mut` + `&` borrows.

**8. (Cross-chapter)** Sizes on 64-bit and the one-sentence *why*, for each pair:
`Option<Box<u8>>` vs `std::optional<std::unique_ptr<uint8_t>>`; then
`Rc<String>` vs `std::shared_ptr<std::string>` — what's identical in layout, what
differs in cost, and who inserts the bookkeeping in Swift's ARC (ch16) that both Rust
and C++ make you write yourself?

<details>
<summary>Answers</summary>

**1.** A bitwise `String` copy duplicates the (ptr, cap, len) header — *two owners
of one heap buffer* — and both destructors free it: the double-free of ch08. The
rule: `Copy` requires all fields `Copy` **and no `Drop`** — a destructor is a
certificate that "this value owns something a memcpy cannot duplicate." The teammate
confuses *can* memcpy with *may* memcpy. A pointer-holding `Copy` type: `&T` — it
borrows rather than owns, so copying it is free; the referend's lifetime is the
borrow checker's separate job. And `&mut T` is *not* `Copy` — exclusivity is not
copyable.

**2.** False — precisely backwards. `'a` is a **constraint** the checker solves,
not a directive: "the output is valid for some region in which the input is valid."
Every call site is *checked* against that; nothing is kept alive longer because you
wrote `'a` — a failing call site gives E0597, the constraint violated, not ignored.
Lifetime annotations cannot appear inside function *bodies* at all: bodies are
inferred; annotations live in signatures and type declarations, where inference has
no body to read.

**3.** `Box<u8>`: **8** — one pointer. `Option<Box<u8>>`: **8** — the null niche
carries `None` (documented null-pointer optimization). `Vec<u8>`: **24** — ptr, cap,
len (ch06). `Option<Vec<u8>>`: **24** (observed 1.7x) — the non-null pointer /
non-zero capacity leave a spare bit pattern for `None`. `&[u8]`: **16** — fat
pointer (ptr, len). `&[u8; 7]`: **8** — thin; the length lives in the *type*.
`Rc<u8>` handle: **8** — one pointer to the block. Bonus: the `RcBox<u8>` heap block
is `8 (strong) + 8 (weak) + 1 (u8) + 7 (padding) = 24` bytes.

**4.** Exactly:

```
drop x
drop y
drop b
drop a
```

`_p` is the last-declared local, so it drops first; *inside* `Pair`, Rust drops
fields in **declaration order** — `x` then `y`. Then `_b`, `_a` in reverse
declaration order. The trap: C++ (ch09) destroys members in *reverse* declaration
order, so the identical class prints `y, x` there.

**5.** No. `let t = s;` **moves** `s` (not `Copy`: heap buffer + destructor).
`let u = s;` then uses a moved-from binding — **error[E0382] "use of moved value:
`s`"** at that line, with "value moved here" pointing at `let t = s;`. C++ would
have compiled this into three owners of one buffer — ch09's zombie, multiplied. Fix
A — borrow: `let t = &s; let u = &s;` and drop the trailing `, s` from the
`println!`; many shared readers are legal. Fix B — `let t = s.clone();`: one heap
round-trip, two owners of two buffers, each freed once. Fix A allocates nothing and
is the idiomatic default; knowing when `clone` is *wasted* is the experienced move.

**6.** `a` holds a strong count on `b` and `b` on `a`: at exit each block's strong
count is still 1, held by the *other* node — neither reaches 0, no destructor runs,
no block returns to the allocator. A leak, not corruption: no dangling pointer, no
double free — memory simply never reclaimed. Rust guarantees no memory *unsafety*,
not absence of *leaks*, and it cannot: safe code can always construct this shape
(which is why `std::mem::forget` is a safe function). Fix: make one direction
**`Weak<T>`** via `Rc::downgrade` — `Weak` bumps only the weak count, keeping the
*block* alive, not the *value*; when the last strong handle dies the payload drops
even with `Weak`s outstanding (they see `None` from `upgrade()`). Trees: parents own
strongly, children point weakly back. Python (ch14) solved the same loop with a
runtime cycle detector; Rust makes you encode the direction — the cost of having no
runtime.

**7.** It **compiles** — `RefCell`'s borrow checking is *run time*, by design; that
is its purpose (mutation through shared handles, the base of the `Rc<RefCell<T>>`
"poor man's GC" pattern). At run time it **panics**: `borrow_mut()` set the flag to
−1 (one writer), and `cell.borrow()` cannot add a reader — thread 'main' panicked at
'already mutably borrowed: BorrowError' (as of 1.7x). The compile-time analogue on a
plain `Vec` — `&mut v` while `&v[...]` is live — is **error[E0502]**, "cannot borrow
as mutable because it is also borrowed as immutable." Same invariant, two enforcement
points: E0502 proves it at build time for free; `RefCell` pays a flag check per
access and converts the proof into a panic — that relocation *is* interior
mutability.

**8.** `Option<Box<u8>>` = **8 bytes**; `std::optional<unique_ptr<uint8_t>>` =
**16 bytes** (8-byte pointer + 1-byte `has_value` flag, padded). Rust's niche stores
`None` in the bit pattern a non-null pointer can never take — documented for
`Option<Box<T>>` — while C++'s `optional` is a tagged struct (ch06) and `unique_ptr`
offers it no niche. `Rc<String>` vs `shared_ptr<string>`: the layout idea is
near-identical — strong and weak counts **inline with the value in one allocation**
(Rust: 8 + 8 header + 24 payload) — but the costs differ. `Rc::clone` is a plain
integer increment (`Rc` is `!Send`/`!Sync`, single-threaded), while `shared_ptr`'s
counts are always atomic: single-threaded C++ pays the cache-line synchronization
Rust lets you avoid, and cross-thread Rust demands `Arc` with the same atomic cost.
Who inserts the bookkeeping: in Rust *you* write every `Rc::clone`/drop; in C++
*you* route assignments through `shared_ptr`'s copy machinery; in Swift (ch16) the
*compiler* inserts retain/release at every alias point. Same data structure, three
answers: Rust visible and optional, C++ visible and semi-implicit, Swift invisible
and mandatory.

</details>
