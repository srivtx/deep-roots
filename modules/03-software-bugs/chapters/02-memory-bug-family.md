# Chapter 02 — The memory-bug family

> **The 30-second version.** Module 01 ch21 said memory safety fails four
> ways; this chapter is the same physics read as a code-review checklist.
> Every memory bug is one sentence: *some bytes were used outside the size
> and lifetime their owner granted.* From that invariant derive
> the whole family — overflow, off-by-one, use-after-free, double-free,
> uninitialized read, escaped pointer — plus the managed-language cousins (C++
> iterator invalidation, Java static-collection leaks, Python `__del__`
> cycles). Each has a shape you can see in a diff, a sanitizer that catches it,
> and a CVE-shaped twin that shipped. The rule that survives all
> of them: every pointer-shaped thing needs a named owner of its lifetime.

## 1. One invariant, six violations

Memory is a flat byte array; ownership and lifetimes are conventions on top
(module 01 ch03–05): a stack frame owns its locals for a call (ch04), a heap
chunk its `malloc`ed bytes until one `free` (ch05), an array its indices
`0..n-1`. None of it is machine-enforced. The invariant the programmer must
maintain:

> **Every byte access is inside one owner's granted region, inside that
> owner's lifetime, and reads a value someone actually wrote.**

Violate the region → overflow or off-by-one. Violate the lifetime →
use-after-free, double-free, the escape. Violate "actually wrote" → the
uninitialized read. That is the entire family, derived, not memorized:

| Shape | Violation | What physically happens | Detector (→ ch05) | CVE-shaped twin |
|---|---|---|---|---|
| Overflow (write) | region | bytes land in the neighbor's chunk/frame | ASan redzones | Morris worm, 1988 (`fingerd`/`gets`) |
| Out-of-bounds (read) | region | neighbor's bytes are *exfiltrated* | ASan | Heartbleed, CVE-2014-0160 |
| Off-by-one | region, by exactly 1 | one byte past the fence | UBSan `-fsanitize=bounds` | NULL-byte heap poisoning (module 02 ch04) |
| Use-after-free | lifetime | access into a *re-allocated* chunk | ASan quarantine | CVE-2019-5786 (Chrome `FileReader`) |
| Double-free | lifetime, twice | same chunk enters two free lists | ASan; glibc ≥2.29 tcache key | apt CVE-2020-3810 |
| Uninitialized read | "was written" | leftovers from a previous tenant | MSan / Valgrind | kernel stack info-leaks (KMSAN) |
| Escape (return of local) | lifetime | pointer into a dead stack frame | `-Wreturn-local-addr` warning | `asctime()` static returns |

MSRC and Chromium 2019 both measured roughly **70% of severe
vulnerabilities as memory-safety bugs** (module 01 ch21) — this table from
the outside. The rest of the chapter is its rows, one diff at a time.

## 2. Writing past the end: overflow, and the off-by-one that guards it

`strcpy(dst, src)` writes `strlen(src) + 1` bytes — the `+1` is the NUL —
knowing nothing about what `dst` owns. The bug is the definition of the
function:

```c
/* VULNERABLE (CWE-787): the destination's size is not an input */
void greet(const char *name) {
    char buf[64];
    strcpy(buf, name);      /* writes strlen(name)+1; buf owns 64 */
}
```

The obvious fix is `strncpy` — where the trap list starts:

```c
char user[8];
strncpy(user, "Alexander", 8);  /* copies EXACTLY 8 bytes of a 9-char string */
/* Trap 1: no NUL is written when strlen(src) >= n. strlen(user) then walks
   past user[7] into whatever the neighbor stores.                    */
/* Trap 2: when strlen(src) < n, strncpy ZERO-PADS the rest of dst —
   memset-sized cost on big buffers, a real hotspot.                 */
```

Trap 1 is the dangerous one: a "bounded" copy that builds a non-terminated
string, so the *next* string call overflows on your behalf. Silent truncation
is the same call's data-side CVE shape: a truncated identifier still compares
unequal and skips a permission check. The honest pattern makes the size an
input to a *decision*:

```c
/* FIXED: check-then-copy */
if (strlen(name) >= sizeof buf) { return -1; }
strcpy(buf, name);
```

The off-by-one is the overflow's minimal form; derive it as a fencepost: 8
elements have valid indices `0..7` — 8 slots between 9 fenceposts — and a
loop written `i <= 8` visits the ninth fencepost as a slot:

```text
        index:    0     1     2     3     4     5     6     7   |  8
                 +-----+-----+-----+-----+-----+-----+-----+-----+---------+
   int v[8]:     |  1  |  1  |  1  |  1  |  1  |  1  |  1  |  1  | guard?? |
                 +-----+-----+-----+-----+-----+-----+-----+-----+---------+
                   ^--- owned by v ----------------^  ^-- v[8]: NOT yours
                                                          (ASan redzone)
```

The standard is precise: you may *form* the one-past-the-end pointer
(`v + 8`) — end-iterators depend on it — but never *dereference* it. In the
heap, the extra byte often lands on the next chunk's size field — the
"off-by-one NULL-byte overwrite" that module 02 ch04 turns into an exploit
primitive.

## 3. Lifetimes out of order: use-after-free and double-free

`free(p)` says *these bytes now belong to the allocator* (module 01 ch05,
ch19). They do not vanish: glibc 2.41 files small chunks into a per-thread
tcache bin within microseconds, and the next same-size `malloc` pops the same
address back out — owned by someone else. A UAF is not "read garbage" but
"read the *next tenant's* data," or write their function pointer. Hence the
exploit-chain opener (module 02 ch05).

Two review shapes dominate. First, the observer/callback pattern — an object
holds raw pointers it promises to notify, and one listener dies first:

```cpp
class Session {                          // the publisher
    std::vector<Listener*> listeners_;   // raw = "I do NOT own these"
public:
    void fire() { for (auto* l : listeners_) l->on_event(this); }
};
// Bug: a listener forgets to unregister in ~Listener();
// the next fire() calls into freed memory: heap-use-after-free.
```

Second, destructor-triggered notification: a destructor fires events while
its neighbors are *also* being destroyed (a registry clearing at
static-destruction time), after the receiver's lifetime ended. Both are
intermittent — crashing only when the allocator reuses the freed bytes before
the next `fire()`.

The double-free is the error-path bug, because happy paths are tested and
error paths are not:

```c
int reload(Entry *e, const char *src) {
    free(e->name);               /* release the old buffer first */
    char *fresh = malloc(strlen(src) + 1);
    if (!fresh) return -1;       /* BUG: e->name still holds the OLD,
                                     already-freed pointer        */
    strcpy(fresh, src);
    e->name = fresh;             /* fixes the dangling copy —
                                     skipped on the error path    */
    return 0;
}
/* Later cleanup: free(e->name) -> second free of the same chunk. */
```

The bug lives in the gap between "free" and "reassign": every early return
leaves a dangling copy. ASan catches it in testing by *quarantining* freed
chunks instead of reusing them.

## 4. Reading what was never written: uninitialized data and the escape

The uninitialized read never leaves your own frame, and it is the one that
changes with optimization level — why "works at -O0, breaks at -O2" is
folklore. The mechanism is stack reuse (module 01 ch04): at `-O0`, locals
live at fixed frame offsets, so an uninitialized `x` reads what the *previous*
call at that depth left; at `-O2`, `x` gets a register never written, and the
compiler — same UB clause — may optimize the read into anything:

```c
static int f(void) {
    int x;              /* never initialized */
    return x + 1;       /* UB: read of an indeterminate value */
}
static void dirty(int a, int b) { int junk = a * b; (void)junk; }
int main(void) {
    dirty(41, 1);       /* leaves 41 in this stack region */
    printf("%d\n", f());
}
```

Verified on GCC 14.2.0 (Debian, x86-64): `-O0` prints **42** (frame reuse),
`-O2` prints **1** (register held 0) — no answer is correct under undefined
behavior. The production version is the info-leak: kernel structs copied to
userspace with uninitialized padding leak stack contents (KMSAN in-kernel;
userspace needs MSan on *every* library — ch05).

The escape is the mirror: the value *was* written, to a frame that no longer
exists.

```c
char *label(void) {
    char buf[16];       /* owned by label()'s frame (ch04) */
    snprintf(buf, sizeof buf, "hello");
    return buf;         /* frame dies at return; pointer dangles */
}
```

GCC has warned for decades (`-Wreturn-local-addr`, on by default) — the
warning never to be silenced with a cast: the returned address points into
stack memory the *next call anywhere* overwrites. Its civilized cousin,
returning a static buffer (POSIX `asctime()`), trades the crash for a data
race between `ctime()` callers. Same root cause: no owner holds the
lifetime.

## 5. Managed runtimes inherit the family: C++, Java, Python shapes

The invariant does not disappear under a garbage collector; it mutates.

**C++: iterator invalidation is the off-by-one at scale.** `push_back`
amortizes growth by capacity doubling (module 01 ch19: 1→2→4→8…), and
doubling means *reallocate and copy* — new buffer, new addresses:

```cpp
std::vector<int> v{1, 2, 3, 4};
int *first = &v[0];         // points into the current buffer
for (int i = 0; i < 4; i++)
    v.push_back(i);         // insert #5: capacity 4 -> 8, buffer MOVES
// first now dangles: invalid by the STANDARD, not just the allocator —
// and invisible without annotations, since the old buffer looks intact.
```

Review rule: *if `push_back` triggers reallocation, every iterator, pointer,
and reference into the vector is dead*; check `v.capacity()`, or `reserve()`
explicitly. Since the language (not the allocator) declares them dead, only
ASan container annotations or `_GLIBCXX_ASSERTIONS` catch the dangling use in
tests.

**Java: static collections are immortal, so their contents are too.** No GC
collects an object reachable from a `static` field. The classic leak is a
listener registry:

```java
static final Map<String, Listener> REGISTRY = new HashMap<>();
// every redeploy registers a new Listener holding the new ClassLoader;
// the old loader can never be collected
```

Redeploying app servers die of this — not the heap but *Metaspace*: each
leaked listener pins a leaked `ClassLoader`, and Metaspace is *unbounded* by
default (set `-XX:MaxMetaspaceSize` to fail loudly). No sanitizer exists for
this class; the detector is a heap dump plus dominator-tree analysis (jcmd,
Eclipse MAT); the fix is unregistering on teardown, or weak references.

**Python: refcounting cannot see cycles, and `__del__` used to make them
permanent.** CPython's primary collector is a refcount (module 01 ch14); two
objects referencing each other sit at refcount 1 forever, so a tracing GC
(three generations, thresholds 700/10/10 — ch14) breaks cycles.
Before PEP 442 (Python 3.4), an object with `__del__` inside a cycle was
*uncollectable* — no safe finalizer order — parked in `gc.garbage`, which
only grew. PEP 442 made such cycles collectable, so on CPython 3.12 they
*are* collected — though `__del__` can still *resurrect* an object (store
`self` somewhere). Detectors: `gc` debugging flags, `gc.garbage`, `objgraph`.
A `__del__` is a lifetime contract; contracts need owners.

## 6. The reviewer's heuristic: every pointer needs an owner

Walk any diff and ask one question per pointer-shaped thing — pointer,
reference, iterator, index, handle, callback registration:
*who owns this lifetime, by name?* The six shapes are the six ways the answer
goes missing:

- **Overflow / off-by-one** — the *region*'s owner is unknown to the
  operation (`strcpy`, `<=`): make the size an input to a decision.
- **Use-after-free** — the *target*'s owner changed silently (observer lists,
  late destructors): unregistration must be as visible as registration.
- **Double-free** — ownership *transferred* but a stale copy survived an
  error path: null-out at the free site, or free in exactly one place.
- **Uninitialized** — the owner never wrote the value: initialize, or let
  MSan prove you wrong.
- **Escape** — the owning frame ended while the pointer lived on: never
  silence `-Wreturn-local-addr`.
- **Managed variants** — the owner is a static field, a reallocated buffer,
  or an invisible cycle: same question, new vocabulary.

This is why Rust's borrow checker (module 01 ch10) is a memory-bug
*exterminator*: it makes the heuristic machine-checked.

## What insiders know

- `strncpy`'s `n` counts *destination* bytes; `strncat`'s `n` counts *source*
  chars and **always** NUL-terminates — the asymmetry bites everyone once.
  When `strlen(src) < n`, strncpy zero-fills the rest: 4 KiB of memset for a
  5-char copy.
- GCC's `-Wstringop-truncation` (in `-Wall` since GCC 8) fires on the exact
  truncating-`strncpy` shape; GCC 14 adds `-Wuse-after-free`, flagging `p`
  used after `free(p)` *within one function* — neither warning catches the
  cross-function UAF.
- `std::vector` invalidation: `push_back` invalidates everything iff it
  reallocates (watch `capacity()`); middle `insert`/`erase` invalidates
  always, even without growth; `std::deque::insert` kills all iterators but
  keeps references to end elements.
- glibc 2.26 introduced tcache (per-thread, 64 bins, 7 chunks each — module
  01 ch19); glibc 2.29 added the double-free `key` check, so production often
  catches the fast double-free with `free(): double free detected in tcache
  2` — ASan still catches the ones tcache misses.
- ASan catches UAF only because it *quarantines* freed chunks (default ~256
  MB held back from reuse); without it, the bug would look like ordinary
  corruption. And ASan's default does *not* check stack-use-after-return —
  set `ASAN_OPTIONS=detect_stack_use_after_return=1`, the mode that turns
  escape-the-local into a caught red-zone read.
- Uninitialized reads are invisible to ASan and UBSan — the bits pass every
  bounds check; only MSan (bit-propagation tracking, all-instrumented
  builds) or Valgrind see them. The #1 "sanitizers say clean but the bug is
  real" surprise.

## See it yourself

All commands run on **GCC 14.2.0 (Debian 14.2.0-19), glibc 2.41, x86-64**;
outputs are real fragments. Type up two sources first
(`escape.c` and `uninit.c` are complete in section 4):

```c
/* offbyone.c */
#include <stdio.h>
int sum(void) {
    int v[8] = {1,1,1,1,1,1,1,1};   /* 8 valid indices: 0..7 */
    int total = 0;
    for (int i = 0; i <= 8; i++)    /* BUG: <= reads v[8], one past the end */
        total += v[i];
    return total;
}
int main(void) { printf("total=%d\n", sum()); return 0; }
```

```c
/* uaf.c */
#include <stdlib.h>
#include <string.h>
int main(void) {
    char *name = malloc(8);     /* heap chunk: 8 usable bytes */
    strcpy(name, "aa");
    free(name);                /* chunk returns to tcache — pointer dangles */
    size_t n = strlen(name);   /* use-after-free read */
    free(name);                /* double-free: the error-path finish */
    return (int)n;
}
```

**1. The off-by-one — the compiler is silent, the sanitizers are not.**

```console
$ gcc -Wall -Wextra -c offbyone.c -o /dev/null
        (no warnings: -Wall cannot see i <= 8 as wrong)
$ gcc -g -fsanitize=address,undefined offbyone.c -o offbyone && ./offbyone
offbyone.c:6:19: runtime error: index 8 out of bounds for type 'int [8]'
=================================================================
==25112==ERROR: AddressSanitizer: stack-buffer-overflow on address ...
READ of size 4 at 0x7fa4cf200040 thread T0
    #0 ... in sum offbyone.c:6
    #1 ... in main offbyone.c:9
```

Note the division of labor: UBSan names the *rule* broken, ASan the *memory*
touched — both from one build, which is why
`-fsanitize=address,undefined` is the lab default (machinery: ch05).

**2. The escape — the one the compiler catches for free.**

```console
$ gcc -Wall -Wextra -c escape.c -o /dev/null
escape.c: In function 'label':
escape.c:5:12: warning: function returns address of local variable
      [-Wreturn-local-addr]
```

No sanitizer needed; on by default, never to be cast away. (To *see* the
corruption: `ASAN_OPTIONS=detect_stack_use_after_return=1` on the ASan build.)

**3. Use-after-free and the double-free — ASan's signature report.**

```console
$ gcc -Wall -Wextra -c uaf.c -o /dev/null
uaf.c:7:16: warning: pointer 'name' used after 'free' [-Wuse-after-free]
$ gcc -g -fsanitize=address uaf.c -o uaf && ./uaf
==25302==ERROR: AddressSanitizer: heap-use-after-free on address ...
READ of size 2 ... in strlen
0x502000000010 is located 0 bytes inside of 8-byte region [...]
freed by thread T0 here:
    #0 ... in free
    #1 ... in main uaf.c:6
previously allocated by thread T0 here:
    #1 ... in main uaf.c:4
```

The three-stack report (use, free, alloc sites) is the whole investigation in
one message; the follow-on `free(name)` triggers `attempting double-free`.
GCC 14 already flagged the intra-function UAF at `-Wall`; the sanitizer's
edge is the cross-function variants.

**4. The uninitialized read — the one that needs -O0 vs -O2.**

```console
$ gcc -O0 uninit.c -o u0 && ./u0
42                          # f()'s frame reused dirty()'s slot (ch04)
$ gcc -O2 uninit.c -o u2 && ./u2
1                           # register garbage; UB, not a "different answer"
$ gcc -O2 -fsanitize=undefined uninit.c -o uub && ./uub
1                           # UBSan does NOT catch uninitialized reads
```

Neither output is correct. The last line also shows the wrong sanitizer
choice: only MSan sees this class (ch05).

## How to use this knowledge

- **Code review as shape-matching.** When a diff adds a raw pointer field, a
  callback registration, a `strncpy`, an early return after a `free`, or a
  `static` collection, name the family and the failure mode *before* the bug
  exists — ask the owner question.
- **Picking the detector in a second.** Corruption of a neighbor → ASan.
  Breaks only under `-O2` → suspected uninitialized read → MSan, not UBSan.
  Intermittent event-path crash → UAF → ASan quarantine. A `tcache 2`
  production message → the glibc ≥2.29 guard caught a double-free; audit every
  error path.
- **Debugging "works on my machine."** Different allocator or optimization
  level → different layout and registers → different manifestation of the same
  UB; the -O0/-O2 experiment is the two-machine story in one binary.
- **The interview answer with teeth.** "How do you prevent memory bugs?" Not
  "be careful": *name an owner for every lifetime, make sizes inputs to
  decisions, run ASan+UBSan in CI, and know which class each detector
  misses.*

## Connections

- **Module 01 ch05 / ch19** — what `free` actually does (bins, tcache) is why
  a UAF reads the *next tenant's* bytes and why vector growth reallocates.
- **Module 01 ch04** — frame layout and stack reuse are the mechanism behind
  the -O0 uninitialized value and the escaped local.
- **Module 01 ch21** — the four primitive failures; this chapter re-derives
  them as review shapes with detectors attached.
- **Module 02 ch02 / ch04 / ch05** — the exploitation view: off-by-one
  NULL-byte primitive, glibc metadata attacks, UAF reallocation to a
  controlled type; defense requires the offense.
- **Module 03 ch05** — how ASan's shadow memory, UBSan's checks, MSan's bit
  propagation, and quarantine actually work.
- **Module 10 ch02** — UB as the compiler's optimization license: why an
  uninitialized read can "become" any value the optimizer prefers.
- **Module 01 ch14** — CPython's refcount-plus-generations machinery; why
  cycles plus `__del__` need a tracing GC.

## Quiz

1. **(Concept)** State the single invariant from which all six shapes in this
   chapter derive, and give the one-line violation each of use-after-free and
   the uninitialized read commits against it.
2. **(Concept)** Why does the C standard allow *forming* `v + 8` for
   `int v[8]` while forbidding `v[8]`? What does the standard need that
   pointer for?
3. **(Predict)** `char user[8]; strncpy(user, "Alexander", 8);` — describe the
   exact contents of `user`, then predict what `strlen(user)` does and why.
4. **(Predict)** The `dirty()`/`f()` program of section 4 prints 42 at `-O0`
   and 1 at `-O2` (GCC 14.2.0, x86-64). Explain both numbers; which (if
   either) is correct?
5. **(Predict, cross-module)** On CPython 3.12, two objects reference each
   other and both define `__del__`. Are they collected? What would the answer
   have been on CPython 3.3, and where would the objects sit?
6. **(Diagnosis)** A `Session` keeps `std::vector<Listener*>` and fires
   events in its destructor. The crash log shows heap-use-after-free *only
   under heavy load*; ASan's "freed by" stack points to `~Listener`. Name the
   pattern; why does load make it visible?
7. **(Diagnosis)** C++ code keeps `int *p = &v[0];` on a `std::vector<int>`
   with 4 elements and capacity 4, then does 5 `push_back` calls. When exactly does `p` become invalid, and why may `*p` still *appear* to work?
8. **(Diagnosis, cross-module)** The `reload()` error path of section 3:
   explain why it manifests only when `malloc` fails, why glibc 2.41 may catch
   it with `free(): double free detected in tcache 2`, and which single
   restructuring kills the class.

<details>
<summary>Answers</summary>

1. **Invariant:** every byte access must be inside one owner's granted
   region, inside that owner's lifetime, and read a value actually written.
   Use-after-free violates *lifetime*: access after ownership returned to the
   allocator, likely re-granted to a new tenant. The uninitialized read
   violates *was-written*: bytes in-bounds and alive but never stored, so the
   read yields a previous tenant's leftovers — reuse, not corruption.
2. The one-past-the-end pointer makes C's pointer model workable: the end
   sentinel for iteration (`p != v + n`) and the anchor of pointer subtraction
   (`v + n - v == n`). Forming it accesses no memory; dereferencing it *would*
   touch bytes outside the object, so that alone is undefined. 8 slots need 9
   valid addresses to bound them.
3. `user` holds the 8 bytes `A l e x a n d e r` and **no NUL** — `strncpy`
   terminates only when `strlen(src) < n`, and here `strlen("Alexander") ==
   9`. `strlen(user)` reads past `user[7]` until it finds a zero: an out-of-bounds
   read with attacker-influenced length — the Heartbleed class, minus the
   network.
4. At `-O0`, locals live at fixed frame offsets; `f`'s `x` lands on `dirty`'s
   `junk` (41), so `x + 1` prints **42** — frame reuse (module 01 ch04) in the
   act. At `-O2`, `x` lives in a register holding 0, printing 1. Neither is correct: the read is undefined behavior,
   so any value — including one the optimizer *chooses* (module 10 ch02) — is
   permitted.
5. On CPython 3.12 (PEP 442, 3.4+): yes, the cycle detector collects them,
   each `__del__` at most once, in a defined order. On 3.3 the finalizer made
   the cycle *uncollectable* — no safe order — so both objects sat in
   `gc.garbage` until interpreter exit. Refcounting + cycles + finalizers is
   exactly where a tracing GC earns its keep (module 01 ch14).
6. Destructor-triggered notification to a partially destroyed observer: the
   listener died before the publisher. Under light load the freed chunk sits
   in tcache with bytes intact, so the call *appears* to work; under load,
   allocations reuse the chunk (tcache: per-thread, 7 deep — module 01 ch19),
   the callback pointer is overwritten, and the call crashes. Intermittency is
   allocator timing; ASan makes it deterministic via quarantine.
7. Capacity 4, size 4: the first `push_back` grows to 8 and moves the buffer,
   so `p` is invalid immediately (inserts 2–4 fit; the fifth grows 8→16).
   `*p` may still appear to work because the freed old buffer is intact until
   a new tenant claims it: invalid *by the standard* is not yet *corrupt in
   memory* — why only ASan annotations or `_GLIBCXX_ASSERTIONS` catch it.
8. On the happy path `e->name = fresh` overwrites the dangling copy first;
   only the `malloc`-failure return skips it, leaving `e->name` dangling for
   cleanup to double-free. glibc 2.29+'s tcache key detects a chunk pushed
   twice into a tcache bin — only while it is still in tcache (7 chunks, same
   thread); a migrated chunk corrupts free-list metadata instead (module 02
   ch04). Class-killer: transfer ownership atomically — assign the new pointer
   before freeing the old (or null-out at the free site), so no early return
   observes a dangling copy.

</details>
