# Chapter 09 — C++: lifetime is scope (RAII)

> **The 30-second version.** C++ starts from C's memory model — same pointers, same stack frames (Chapter 04), same heap machinery (Chapter 05), same layouts (Chapter 06) — and changes one thing: *who is responsible for cleanup*. RAII ("resource acquisition is initialization") ties every resource to an object, and every object's lifetime to its scope: the closing brace of a block becomes a compiler-generated sequence of destructor calls in reverse construction order. Because scopes exit even when an exception tears the stack apart, cleanup is deterministic and impossible to forget. Smart pointers, move semantics, guaranteed copy elision, and the rule of zero all fall out of that one decision. There is no garbage collector — and after this chapter you will rarely miss one.

## Scope is a destructor call

Chapter 04 ended on the fact all of C++ stands on: the compiler knows, statically, the layout of every stack frame, so it knows the exact instant each local dies — the closing brace, when the frame pops. C (Chapter 08) threw that knowledge away and made `free` your problem. C++ cashes it in: **the destructor is a function the compiler calls for you at scope exit, for every fully constructed local, no matter how the scope exits.**

RAII in three sentences: acquire the resource in the constructor (open the file, take the lock, allocate the buffer); release it in the destructor; never touch the release by hand again. "Resource" means anything that must be given back — memory, file descriptors, mutexes, transactions — and the name describes the acquisition, but the consequence is better read backwards: **lifetime is scope**. Two ordering rules are hard parts of the design, both derivable from dependency:

- Locals destruct in reverse construction order — `b` was constructed after `a`, so `b` may hold pointers into `a`, so `b` must die first.
- Members destruct in reverse declaration order, *after* the class's own destructor body runs. Same argument, one level down.

```cpp
#include <cstdio>

struct Tracer {
    const char* name;
    explicit Tracer(const char* n) : name(n) { std::printf("ctor %s\n", name); }
    ~Tracer()                                { std::printf("dtor %s\n", name); }
};

int main() {
    Tracer a{"a"};
    {
        Tracer b{"b"};
        Tracer c{"c"};
    }              // compiler emits ~c(); ~b(); right here — reverse order
    Tracer d{"d"};
}                  // then ~d(); ~a();
```

```text
ctor a
ctor b
ctor c
dtor c
dtor b
ctor d
dtor d
dtor a
```

Now the constraint that *forced* this design: **exceptions**. When a `throw` fires, the runtime unwinds the stack frame by frame hunting for a matching `catch`, and in every frame it must run the destructors of all fully constructed locals — immediately, deterministically, reverse order. There is no collector to sweep the garbage later; this language deliberately has none (Chapters 12 and 14 walk the other road). A language with exceptions therefore *must* bind destruction to scope, or every function between `throw` and `catch` leaks everything it holds. It is also why C++ never grew Java's `finally`: RAII subsumes it — cleanup is a property of the *type*, written once, not of every call site, written forever. Python's `with`, Go's `defer`, and Rust's drop scopes (Chapter 10) descend from this argument.

Run-time cost: nothing. Destructor calls are emitted at compile time against known frame addresses (Chapter 04's fixed offsets) — no runtime discovery of "what is alive", unlike a tracing GC (Chapters 12, 15). The price is type-system discipline — the rest of this chapter.

## new and delete: malloc with constructors bolted on

`new`/`delete` sit *on top of* the Chapter 05 machinery — the default `operator new` on Linux (libstdc++ as of GCC 13) is a thin wrapper over `malloc` — but add three things:

1. **Types, sizes, constructors.** `new Widget` requests exactly `sizeof(Widget)` bytes and runs the constructor in that memory; `delete p` runs the destructor, then returns the bytes. `malloc(24)` gives you 24 anonymous bytes — the object-ness of Chapter 06 is your own convention. `new std::string[10]` requests `320 + 8` bytes (libstdc++: 32-byte strings plus an 8-byte cookie, below) — the *type* computes the size, not you.
2. **Failure mode.** `malloc` returns `NULL`; `new` throws `std::bad_alloc` (the C-minded `new (std::nothrow) T` returns null).
3. **Per-type dispatch.** `operator new` is overloadable — per class or globally — so one type's allocations can be routed into the Chapter 19 arena without touching anything else. `malloc` is one global function; C has nowhere to hang per-type policy.

```cpp
#include <cstdio>
#include <cstdlib>
#include <new>

struct Widget {
    double payload[4];                                   // 32 bytes
    static void* operator new(std::size_t n) {
        std::printf("Widget::operator new(%zu)\n", n);   // prints 32: the TYPE chose the size
        void* p = std::malloc(n);
        if (!p) throw std::bad_alloc{};
        return p;
    }
    static void operator delete(void* p) noexcept { std::free(p); }
};
// new Widget;   -> operator new(32), then the ctor runs IN the returned bytes
// delete w;     -> ~Widget, then operator delete(w)
```

The split is the point: allocation is *policy* (overridable, per-type); construction and destruction are the *type's business*, always paired. Chapter 08's "the one who allocates frees with the matching function" had no compiler help at all.

**Array-ness is allocation metadata, so mismatching it is undefined behavior.** `new Foo[8]` does not just request `8 * sizeof(Foo)` bytes. `delete[]` must know how many destructors to run, and the count is not in the objects — so for a type with a non-trivial destructor the compiler writes an 8-byte *cookie* (Itanium C++ ABI, GCC 13/Clang as of 2024) *before* the pointer you receive, in the same bookkeeping neighborhood as Chapter 05's chunk headers. The failures write themselves:

- `delete p` where `p = new Foo[8]`: one destructor runs (seven skipped), then `operator delete` receives a pointer *offset* by the cookie from the allocation start. glibc's `free()` reads a "chunk header" at `p - 8`, finds the cookie where the size field belongs, and aborts with `free(): invalid size` — otherwise Chapter 21 would call it an attack surface.
- `delete[] p` where `p = new Foo`: the runtime reads a "cookie" that is actually some other allocation's bytes and calls the destructor a garbage number of times.
- The compiler *must* know array-ness at the `delete` site because the count lives in the allocation's metadata (Chapter 05), not in the type, not in the pointer.

If `Foo`'s destructor is trivial, no cookie is needed and the mismatch "works" on glibc — still UB, now invisible, one refactor away from a crash.

## The pointers that clean up after themselves

`std::unique_ptr<T>` is the smallest RAII wrapper: one pointer, one rule — the scope that owns it deletes it.

```cpp
#include <cstdio>
#include <memory>

struct Node { int v; ~Node() { std::printf("~Node(%d)\n", v); } };

static_assert(sizeof(std::unique_ptr<Node>) == sizeof(Node*));  // 8 == 8, zero overhead

void f() {
    auto n = std::make_unique<Node>(42);  // acquisition = initialization
    // ... anything here may throw ...
}                                         // ~Node + operator delete HERE, guaranteed
```

How can one pointer also "remember" how to delete? The deleter is a *template parameter*, and the default (`default_delete<T>`) is an empty struct stored via the **empty-base trick**: the deleter is a base class of the pointer wrapper, and empty base classes add zero bytes (EBO, derived below). Zero-cost holds only while the deleter is stateless: `unique_ptr<T, void(*)(T*)>` is 16 bytes, 40 on libstdc++ with a `std::function<void(T*)>` deleter. Copying is disabled (two owners would double-free); moving transfers the pointer and nulls the source. One owner, and the owner is a scope.

`std::shared_ptr<T>` answers a different question — *who is last out?* — with manual reference counting: the mechanism Python automates (Chapter 14), but deterministic and without a cycle detector. The pointer itself is 16 bytes, always: `[T* obj] [control-block*]`. The counts live in a heap-allocated **control block**, and the block is where the design lives:

- **strong count** — how many `shared_ptr`s own the object; starts at 1.
- **weak count** — how many `weak_ptr`s are watching, plus 1 reservation held by the strong count itself; starts at 1.
- **deleter storage** — how to destroy the object; zero bytes for the empty default (EBO again).
- Does the block contain the object's vptr? **No — never.** The block is not the object; if `T` is polymorphic, *T's* vptr sits in *T*'s bytes, wherever they are. (Honest detail: libstdc++ and libc++ both give the block *its own* vptr, because the block's dispose hooks are virtual — that vptr belongs to the block's type, not to yours. Strong + weak + deleter is the concept; the block's own vptr is a stdlib's business.)

```text
std::shared_ptr<Widget> sp          sizeof(sp) == 16 (x86-64)
+-----------------+-----------------+
| obj ptr         | block ptr       |
+-----------------+-----------------+
        |               |
        |               v
        |    ONE allocation: make_shared<Widget> (libstdc++ as of GCC 13,
        |    Widget is 8 bytes of payload)
        |    +----------------+------------+------------+-----------------+
        |    | vptr  8 B      | strong 4 B | weak   4 B | Widget  8 B     |
        |    | (the block's   | = 1        | = 1        | (obj ptr aims   |
        |    |  own hooks)    |            |            |  HERE, mid-     |
        |    |                |            |            |  allocation)    |
        |    +----------------+------------+------------+-----------------+
        |    |<-- control block header (16 B) -->|<-- the object ------->|
        |    request: 24 B -> glibc 2.35: one 32 B chunk (Chapter 05)
        '------------------------------------------------------------------->
```

`std::make_shared<Widget>(...)` is **one** `operator new` for object *and* block — a 24-byte request served from one 32-byte glibc chunk, counts and object cache-adjacent. The two-step form, `shared_ptr<Widget>(new Widget)`, is **two** allocations: the object (8-byte request, 32-byte chunk) plus a block that also stores the raw object pointer (24-byte request, 32-byte chunk) — two malloc calls, 64 bytes, two strangers in the cache. `make_shared` halves the traffic.

Why **two** counts? Because `weak_ptr` exists. A `weak_ptr` (16 bytes too) holds only a block pointer — it observes without owning. When the strong count hits 0, the object's destructor runs *immediately* (deterministic — the anti-GC), but the **block must survive** every `weak_ptr`: a weak pointer must always have a valid count to consult, or checking `expired()` would itself be a use-after-free — on the *count*. So the weak count pins the block, and only when it reaches 0 is the block freed. Three consequences worth memorizing:

- A `weak_ptr` can dangle the *object* (that's its job — `lock()` then atomically fails and returns null) but **never the block**. That asymmetry is the feature.
- With `make_shared`, object and block share one allocation, so a lingering `weak_ptr` pins the *whole* chunk: the destructor ran, the payload is logically dead, the bytes stay mapped until the last watcher dies. "RSS stuck at high-water mark after we cleared the cache" is this, more often than not.
- The counts are lock-free atomics (x86-64): copying `shared_ptr`s across threads costs a real atomic inc/dec, and the final decrement publishes with release semantics — Chapter 20 arriving early.

## Moving: steal the guts, not the bytes

Copy semantics duplicate: a `vector` copy allocates a second buffer and copies every element — right for values, catastrophic for returning a 1 MB buffer. **Move semantics** add a question the compiler can ask: *may I treat this object as expiring?* If yes, the answer is not "copy the bytes" but **steal the guts, leave the source valid-but-empty**. The `vector` move constructor copies the three pointers and nulls the source's — three loads, three stores, zero allocations (libstdc++ GCC 13, libc++ LLVM 17, MSVC VS 2022, for the stateless default allocator).

The most useful fact about `std::move`: **it is only a cast.** It compiles to `static_cast<T&&>(x)` — no code runs at the move itself, no bytes move, no function is called. It is a compile-time annotation: "treat `x` as expiring." Whether anything *happens* depends on whether the receiver has a constructor overload that cares: `int y = std::move(x);` is a plain 4-byte copy. The work happens inside the move constructor that the cast *selects*.

Moved-from conventions, precisely: standard-library objects are left **valid but unspecified** — operations without preconditions (`clear`, `assign`, `size`, destruction) are safe. In practice every major stdlib leaves moved-from containers *empty* (vector: three null pointers, capacity 0), so reuse right after a move is routine. The one type with a *guaranteed* moved-from state is `unique_ptr`: null.

Then the compiler removes the move entirely. A function returning by value gets its return slot in the *caller's* frame, its address passed as a hidden argument (Chapter 04's `sret` convention), and **return value optimization** constructs the returned object directly in that slot — the callee never owns a copy, so there is nothing to move. C++17 makes this mandatory for prvalues ("guaranteed copy elision"):

```cpp
Widget make() { return Widget{7}; }        // C++17: constructed IN the caller's slot

struct NoCopy {
    NoCopy(int);
    NoCopy(const NoCopy&) = delete;        // copy deleted
    NoCopy(NoCopy&&) = delete;             // move ALSO deleted
};
NoCopy make2() { return NoCopy{7}; }       // still fine: no copy AND no move ever happens
```

`make2` compiles because nothing is elided — there was nothing to elide; the object is born in the caller's slot. NRVO (returning a *named* local) remains an optional as-if optimization, not a guarantee. Practical order of cheapness: guaranteed elision > move > copy — since C++17, "return big things by value" stopped being a performance topic.

## What these objects look like in bytes

Chapter 06 derived vptr/vtable for any dynamically dispatched language. Here is the concrete C++ edition (Itanium ABI — GCC 13/Clang, x86-64; MSVC differs in details):

```text
struct Shape { virtual double area() const; virtual ~Shape(); double x, y; };
struct Circle : Shape { double r; };        // sizeof(Circle) == 32 = vptr 8 + x 8 + y 8 + r 8

  c (on the stack, say)                     vtable for Circle (exactly ONE per class,
  +----------------+------------+           in .data.rel.ro — read-only after
  | vptr (8 B)     | x  y  r    |           relocation, Chapter 03)
  |      .         | 24 B       |
  +----------------+------------+           +----------------------------------+
       |                                   | offset-to-top: 0                 |
       |                                   | typeinfo ptr -> "Circle" RTTI    |
       '---------------------------------->+----------------------------------+
                                           | &Circle::~Circle (complete)      |
                                           | &Circle::~Circle (deleting)      |
                                           | &Circle::area                    |
                                           +----------------------------------+
                                              plain function pointers, at
                                              compile-time-known slot indices
```

Every `Circle` carries the same 8-byte vptr; the table is one per class, not per object. The cost of `c.area()` versus a direct call: load vptr, load the function pointer from its slot, jump indirectly — two *dependent* loads (each a potential miss, ~100 cycles cold — Chapter 02) and, worse, an indirect branch the compiler cannot resolve statically, so **no inlining** and everything inlining would have removed. The real price of virtual is not two extra instructions; it is the lost optimizer knowledge. Module 04 (queued) rebuilds C++ classes in stripped binaries from exactly these tables.

**Why is `sizeof(empty struct) == 1`?** Objects must have distinct addresses — `&a == &b` for two live objects is illegal — so even an object with no data occupies one byte. An *empty base* may share the derived object's address, though: `struct E{}; struct D : E { int x; };` gives `sizeof(D) == 4`. That is the **empty base optimization (EBO)** — the load-bearing trick behind zero-size deleters, zero-size allocators, and compressed pairs. For *members*, C++20 adds `[[no_unique_address]]` (MSVC: `[[msvc::no_unique_address]]`) to get the same sharing.

**`std::string` SSO.** A 5-character string should not cost a malloc, so every stdlib's string is a *union* with two personalities — short: inline buffer; long: heap pointer + size + capacity — and the folklore is how each smuggles the discriminator and length into spare bits:

```text
libstdc++ (GCC 13, x86-64): sizeof(std::string) == 32, 15 chars inline

  SHORT (<= 15 chars, ZERO allocations):
  +--------------+--------------+-----------------------------+
  | ptr (8 B)    | length (8 B) | 16-byte union: 15 chars +   |
  | points HERE  | = 5          | NUL, padding                |
  +--------------+--------------+-----------------------------+
   0              8              16                            32
  discriminator: ptr == address of its own inline buffer

  LONG: the same 32 bytes read as | heap ptr | length | capacity (8 of the 16) |

libc++ (LLVM 17, x86-64): sizeof == 24, 22 chars inline — length packed into one
byte, a long/short bit flipped inside the cap field. Smaller object, MORE
capacity. MSVC: 15 chars inline, different packing again.
```

**`std::vector`** is exactly three pointers — 24 bytes, x86-64, all three major stdlibs:

```text
std::vector<int> v                        sizeof(v) == 24

    v.begin()       v.end()               v.end_of_storage()
       |              |                        |
       v              v                        v
       +----+----+----+----+-------------------+
       | 10 | 20 | 30 | 40 |    (unused)       |   heap buffer
       +---------------------------------------+
       |<-- size() = 4 -->|
       |<------ capacity() = 8 --------------->|
```

No size field is stored — size and capacity are pointer differences. On `push_back` with `end == end_of_storage`, the vector allocates a new buffer (libstdc++/libc++ double the capacity; MSVC grows 1.5x), *moves* every element, destroys the old ones, and frees the old buffer to the Chapter 05 free list. The "iterator invalidation rules" from every textbook now stop being fine print and become what they always were — **memory relocation rules**: any operation that reallocates physically moves every element, so every pointer, reference, and iterator into the old buffer aims at freed memory. `reserve()` is not a style preference; it is the tool that prevents relocation.

## The rule of zero, and the escape hatches

The **rule of three** (C++98): write any of destructor, copy constructor, copy assignment and you almost certainly need all three — you are hand-managing a resource, and the missing one is a leak or a double-free. The **rule of five** (C++11) adds move constructor and move assignment. The **rule of zero** is modern practice: write *none* of them — give the class RAII members and let the compiler's generated functions do memberwise destruction, copy, and move:

```cpp
class Session {                 // rule of 0: owns nothing raw
    std::unique_ptr<Conn> conn; // deletes itself
    std::string         name;   // SSO or heap, frees itself
public:
    // no destructor, no copy/move: all five generated, all five correct
};
```

Copying is naturally *deleted* (unique_ptr is not copyable — one owner), which is usually the correct semantics anyway. Review translation: raw `new`/`delete` in a class body is a smell; hand-written fives are where self-assignment bugs and double-frees have lived for 30 years.

Sometimes you must separate *object lifetime* from *storage lifetime* — that is placement new. `operator new(size_t)` takes a size; **placement new takes a pointer** and constructs an object in memory you already own. Its inverse is not `delete` but `std::destroy_at` (C++20), which runs the destructor without freeing anything:

```cpp
alignas(Widget) char buf[sizeof(Widget)];  // my bytes, my lifetime
Widget* w = new (buf) Widget{args...};     // construct ONLY — nothing was allocated
// ... use w ...
std::destroy_at(w);                        // destruct ONLY — buf is still mine
```

This is the seam under `std::pmr` (C++17): `std::pmr::memory_resource` is an abstract *allocator* interface — `allocate(bytes, alignment)`, `deallocate`, `is_equal` — and `std::pmr::vector<int>` routes its growth allocations through your resource instead of global `new`. Chapter 19's arena, reached from C++ as a library feature:

```cpp
#include <memory_resource>

class FrameArena : public std::pmr::memory_resource {   // bump allocator, freed in one shot
    char   buf[1024];
    size_t off = 0;
    void* do_allocate(size_t n, size_t a) override {
        off = (off + a - 1) & ~(a - 1);        // round up to alignment
        if (off + n > sizeof(buf)) throw std::bad_alloc{};
        void* p = buf + off;
        off += n;                              // the "free list" is one integer
        return p;
    }
    void  do_deallocate(void*, size_t, size_t) override {}   // per-object frees: no-ops
    bool  do_is_equal(const std::pmr::memory_resource& o) const noexcept override {
        return this == &o;
    }
};

void render(FrameArena& frame) {
    std::pmr::vector<int> scratch(&frame);     // growth comes from the arena: bumps
    scratch.push_back(7);
}   // vector's destructor STILL RUNS: elements destroyed (deterministic, RAII),
    // then "deallocate" no-ops back to the arena — storage dies with the frame
```

The two axes are now independent: object lifetime (ctors/dtors, still scope-bound) and storage lifetime (the arena, freed wholesale per frame). Games and compilers build whole pipelines on this split.

The last trap is the **static initialization order fiasco (SIOF)**. Globals with *dynamic* initializers (initializers that run code) initialize in unspecified order across translation units — the linker lays each TU's `.init_array` (run by the loader before `main`, Chapter 03) in link order, and link order is nobody's contract:

```cpp
// settings.cpp — TU A
std::string g_suffix = load_suffix();          // dynamic initializer: code before main

// registry.cpp — TU B
extern std::string g_suffix;
std::vector<std::string> g_paths{ build_paths(g_suffix) };   // ALSO dynamic — order? UNSPECIFIED
```

If TU B's initializer runs first, `g_paths` is built from a `g_suffix` that has not been constructed. With libstdc++'s string, the zeroed `.bss` (Chapter 03) makes it look merely empty — a silently wrong prefix. Make the victim a `std::map` and you get the signature symptom: **a segfault before `main()` prints a single line** — `~map` runs on an uninitialized tree when B's global is destroyed. C (Chapter 08) cannot have this bug, structurally: all C initializers are link-time constants in `.data`/`.rodata` — no code runs, no order to get wrong.

Two fixes. First, the function-local static: initialization moves to first use, and the call graph — not the linker — defines the order:

```cpp
const std::string& suffix() {
    static std::string s = load_suffix();   // runs on first pass, thread-safely
    return s;
}
```

"Thread-safely" is a C++11 guarantee ("magic statics"): on the Itanium ABI (GCC/Clang) the compiler emits an 8-byte guard variable in `.bss` and wraps the init in `__cxa_guard_acquire`/`__cxa_guard_release`; after the first call, every entry is one cheap load of the guard byte. Second, the **nifty counter** — how libstdc++ bootstraps `<iostream>`: the header declares a tiny `Init` class whose constructor bumps a count; every TU including `<iostream>` instantiates one static `Init` object; the *first* constructed runs the real setup (std::cout alive), the *last* destroyed flushes. Header inclusion becomes an initialization vote — order-independent by construction.

## What insiders know

- **A moved-from vector is never dead.** The standard says valid-but-unspecified; the practice: on libstdc++ (GCC 13), libc++ (LLVM 17), and MSVC (VS 2022) the move constructor steals the three pointers and nulls the source — empty, capacity 0, immediately reusable. The only moved-from object with a *guaranteed* state is `unique_ptr`: null.
- **`std::vector<bool>` is not a vector of bools.** It is a bitfield: one bit per element, `operator[]` returns a *proxy* object, `&v[0]` cannot exist, there is no `data()`. Known broken since the 1998 standard, unfixable without breaking every shipped binary's ABI — so WG21 keeps it. Use `std::vector<char>` or `std::bitset` when you need addresses.
- **SSO capacity is a stdlib fingerprint.** 15 chars in 32 bytes (libstdc++), 22 chars in 24 bytes (libc++), 15 on MSVC. A one-line `sizeof(std::string)` print identifies the implementation — smaller object yet *larger* inline capacity is the libc++ party trick, paid for in bit-packing.
- **Function-local statics are guard-variable code.** 8-byte guard in `.bss`, `__cxa_guard_acquire`/`__cxa_guard_release` on the Itanium ABI (GCC/Clang, C++11 "magic statics"). After first initialization the fast path is one load of the guard byte — a static inside a hot-loop-called function stays nearly free.
- **The passkey idiom** turns an API invariant into a compile-time check: give the token type private constructors and a `friend` declaration, so only the authorized factory can mint tokens; everyone else can *pass* them, never *fabricate* them. "Only `Connection` may construct `Session`" — enforced by the type system, at zero run-time cost.
- **Lifetime extension has a trapdoor.** `const std::string& r = std::string("x");` extends the temporary's lifetime to `r`'s — safe. `const std::string& r = getBox().label;` does **not**: extension applies only to a temporary bound *directly*; `label` is a reference *returned by a member call*, the `Box` dies at the end of the full expression, and `r` dangles. A top source of "works in debug, garbage in release."
- **A stray `weak_ptr` pins a whole `make_shared` allocation.** The object's destructor ran; the bytes — object *and* block, one chunk — stay mapped until the last watcher dies. "Why is RSS not dropping after we released the cache?" — count the `weak_ptr`s, not the `shared_ptr`s.
- **Zero-cost holds only while the deleter is stateless.** `sizeof(unique_ptr<T>)` is 8 with the default deleter, 16 with a function pointer, 40 on libstdc++ with a `std::function` deleter. In a 10-million-edge graph, 40-byte smart pointers are 320 MB of deleter payload.

## See it yourself

Reproduces on a laptop with g++ (GCC 13 here) and clang++ (LLVM 17, libc++).

```bash
mkdir ch09 && cd ch09
cat > probe.cpp <<'EOF'
#include <cstdio>
#include <memory>
#include <string>
#include <vector>

struct Big { char blob[4096]; };
static_assert(sizeof(std::unique_ptr<Big>) == sizeof(Big*), "one pointer, no more");

int main() {
    std::printf("sizeof(unique_ptr<Big>) = %zu\n", sizeof(std::unique_ptr<Big>));
    std::printf("sizeof(shared_ptr<Big>) = %zu\n", sizeof(std::shared_ptr<Big>));
    std::printf("sizeof(string)          = %zu\n", sizeof(std::string));
    std::printf("sizeof(vector<int>)     = %zu\n", sizeof(std::vector<int>));

    std::string s = "short";       // 5 chars
    std::string l = "this string is definitely longer than any inline buffer";
    auto inside = [](const std::string& x) {    // 1 = data() inside the object (SSO)
        return (const char*)&x <= x.data() && x.data() < (const char*)(&x + 1);
    };
    std::printf("short string inline?   %d\n", inside(s));
    std::printf("long  string inline?   %d\n", inside(l));
}
EOF
g++ -std=c++17 -O0 -g probe.cpp -o probe && ./probe
clang++ -std=c++17 -stdlib=libc++ -O0 -g probe.cpp -o probe-libcxx && ./probe-libcxx
```

Expected, x86-64 — the libstdc++ column then the libc++ comparison:

```text
                         libstdc++ (GCC 13)    libc++ (LLVM 17)
sizeof(unique_ptr<Big>) = 8                     8
sizeof(shared_ptr<Big>) = 16                    16
sizeof(string)          = 32                    24     <- the fingerprint
sizeof(vector<int>)     = 24                    24
short string inline?   1                       1      (data() points into the object)
long  string inline?   0                       0      (data() went to the heap)
```

Destructor order, including the exception path that forced the design:

```cpp
// unwind.cpp
#include <cstdio>
struct T { const char* n; explicit T(const char* s):n(s){} ~T(){ std::printf("dtor %s\n", n);} };
int main() {
    T a{"a"};
    try {
        T b{"b"};
        throw 1;              // unwind: ~b runs NOW, while hunting for the handler
    } catch (int) {
        std::printf("caught\n");
    }
    std::printf("after catch\n");
}                             // ~a runs at scope exit
```

```bash
g++ -std=c++17 unwind.cpp -o unwind && ./unwind
# dtor b        <- unwinding destroyed b deterministically
# caught
# after catch
# dtor a
```

Finally, watch the tools catch what the compiler cannot:

```cpp
// leak.cpp — two bugs on purpose; fix one at a time
struct Leak { char pad[64]; };
int main() {
    new Leak[1024];           // BUG 1: pointer dropped -> 64 KB leaked
    Leak* p = new Leak[8];    // BUG 2: array-new, scalar-delete
    delete p;                 //      -> cookie mismatch, heap corruption
}
```

```bash
g++ -std=c++17 -g leak.cpp -o leak && valgrind --leak-check=full ./leak
#   ==NNNN== 65,536 bytes in 1 blocks are definitely lost
#   ... by 0x...: operator new[](unsigned long)
#   (or the glibc abort: free(): invalid size  — the cookie read as a chunk header, ch05)

g++ -std=c++17 -g -fsanitize=address leak.cpp -o leak-asan && ./leak-asan
#   AddressSanitizer: new-delete-type-mismatch ... allocated 576 bytes (8*64 + 8-byte cookie)
#   ...freed 64 bytes in a single delete — the sizes betray the array-ness
#   LeakSanitizer: detected memory leaks — 64 KB definitely lost
```

What to notice: ASan's `new-delete-type-mismatch` prints both sizes, and the 8-byte difference *is* the Itanium ABI cookie — Chapter 05's metadata, visible through a sanitizer. The leak stack ends at `operator new[]` because that is where allocation bottomed out — you now know why.

## How to use this knowledge

- **Reading sanitizer reports.** `new-delete-type-mismatch`, `free(): invalid size`, leaks whose stacks end at `operator new` — map each to its mechanism (cookie, interior pointer, dropped pointer) instead of guessing.
- **Performance decisions.** `make_shared` halves allocation count and improves locality; `reserve()` before a `push_back` loop prevents both the 2x-growth relocations and the invalidation they cause; strings under 15/22 chars never touch the allocator — a profiler showing zero `malloc` traffic there is not lying.
- **Design and review.** The rule of zero is a review rule: a hand-written destructor is a class proposing to hand-manage a resource — make it justify that. Stateful deleters are 8-32 bytes per pointer; in bulk data structures that is a design decision, not a detail.
- **Incidents you can now explain.** "Crash before main" (SIOF); "RSS never drops after clear()" (weak_ptrs pinning make_shared chunks); "works in debug, garbage in release" (a reference to a member of a returned temporary).
- **Interview answers with derivations, not folklore.** Why two counts in a shared_ptr; why `sizeof(unique_ptr)` is 8; why `vector<bool>` breaks `&v[0]`; why C++17 made return-by-value free.

## Connections

- **Chapter 04 (the stack):** scope exit is a frame pop; destructor calls are emitted at compile time against known offsets; sret is why elided returns construct directly in the caller's slot.
- **Chapter 05 (the heap):** chunk headers, minimum sizes, and free lists explain the new[] cookie, the interior-pointer crash, and make_shared's one-chunk saving.
- **Chapter 06 (object layout):** vptr/vtable and tag bits derived there; this chapter showed the concrete Itanium ABI layout, EBO, and the SSO union.
- **Chapter 08 (C):** the explicit model new/delete sit on — and why C cannot have SIOF (no dynamic initializers, no `.init_array` code).
- **Chapter 03 (virtual memory):** `.bss` zeroing and the loader-run `.init_array` are the machinery SIOF and the guard-variable fix live in.
- **Chapter 10 (Rust):** moves become compiler-enforced (affine types) — "valid but unspecified" turns into "cannot be used after move."
- **Chapter 14 (Python):** shared_ptr is manual refcounting; weak_ptr is the manual cycle-breaker; Python automates both, plus a cycle detector.
- **Chapter 16 (Swift/Obj-C):** ARC is the compiler writing your shared_ptr copies and releases for you.
- **Chapter 19 (allocators):** placement new + pmr is how arena and frame allocators become usable C++ library code.
- **Chapter 20 (hardware memory model):** the atomic strong/weak counts, and why the final decrement needs release semantics.
- **Chapter 21 (memory security):** new/delete mismatches and the weak-pinned use-after-free are entry points for the attacks covered there.
- **Module 04 (decompilation, queued):** vtable + RTTI reconstruction of C++ classes starts from this chapter's diagram.

*Forward links — where the rest of the program picks this chapter up:*

- **Module 02 ch05 (use-after-free)** — the destructor that runs twice and the object that dies once: C++ lifetimes are where the modern bug class lives.
- **Module 19 ch03 (the safety spectrum)** — RAII is scope-based affine intuition without an aliasing proof — shared_ptr is the runtime patch for the missing theorem.
- **Module 04 ch04 (vtable reconstruction)** — the virtual dispatch this chapter emits is what a reverse engineer rebuilds: RTTI and the vtable walk.

## Quiz

1. Your teammate wants `finally` blocks in C++ and proposes wrapping every cleanup in `try { ... } catch(...) { cleanup(); throw; }`. In terms of stack unwinding, explain why the language instead bound destructors to scope — and name one guarantee a scope-bound destructor gives that a hand-written cleanup call cannot.

2. ```cpp
   int x = 5;
   int y = std::move(x);
   std::vector<int> v = make();     // returns a 1000-element vector
   auto w = std::move(v);
   ```
   What machine code runs at `std::move(x)` itself, and what is `y`? After the second move, what are `v`'s three pointers on libstdc++ (GCC 13), and which operations are guaranteed safe on `v` by the standard?

3. Predict the numbers, x86-64: `sizeof` of an empty struct, `std::unique_ptr<Empty>`, `std::shared_ptr<Empty>`, `std::string`, `std::vector<int>` — first for libstdc++ (GCC 13), then for libc++ (LLVM 17). Which number changes, and in which direction does the inline string capacity move?

4. `struct Point3 { double x, y, z; };` (24 bytes). `auto sp = std::make_shared<Point3>();` on libstdc++ (GCC 13), glibc 2.35: how many bytes does the single underlying allocation request, and how large is the glibc chunk? What exactly happens inside the block when the last `shared_ptr` dies while one `weak_ptr` survives — and in the two-allocation form (`shared_ptr(new Point3)`), how many glibc chunks does construction cost at minimum?

5. Predict the output:
   ```cpp
   #include <cstdio>
   struct M { const char* n; M(const char* s):n(s){ std::printf("%s\n", s); }
              ~M(){ std::printf("~%s\n", n); } };
   struct C { M a{"a"}, b{"b"}; ~C(){ std::printf("~C\n"); } };
   void f() { C c; M m{"m"}; return; }
   int main() { f(); std::printf("done\n"); }
   ```

6. ```cpp
   struct Foo { ~Foo() { /* non-trivial */ } };
   Foo* f = new Foo[8];
   delete f;      // glibc aborts: free(): invalid size
   ```
   Diagnose with Chapter 05 and this chapter: where is the element count stored, what did `delete f` do before calling the allocator, and why exactly is the pointer handed to `free()` wrong? Bonus: why does the crash sometimes *disappear* when `Foo`'s destructor is made trivial?

7. ```cpp
   std::vector<std::string> v;
   v.reserve(2);
   const std::string* first = &v.emplace_back("a");
   v.emplace_back("b");
   v.emplace_back("c");
   std::printf("%s\n", first->c_str());
   ```
   Why is `first` dangling after the third `emplace_back`, in memory terms? What did the vector physically do at that moment, and what is the fix?

8. A release build segfaults before `main()`; the debug build works. TU B has a global `std::map<std::string, Config>` initialized from a global `std::string` defined in TU A. Name the failure, explain why C (Chapter 08) structurally cannot have it, and give both fixes — including what the guard variable does in the function-local-static fix, and which libstdc++ header feature historically used the other fix.

<details>
<summary>Answers</summary>

1. **Concept.** When a `throw` unwinds, every frame between `throw` and `catch` must release its resources *deterministically, while the frames are being popped* — there is no GC to clean up later. A hand-written cleanup call only runs on paths the author remembered: early returns, the tenth exception, the next maintainer. A scope-bound destructor runs on *every* exit path — fall-through, `return`, `break`, `throw` — because the compiler emits it at the closing brace for every fully constructed local, in reverse construction order. It also covers members and temporaries, which no manual call at the call site touches.

2. **Concept/predict.** `std::move(x)` compiles to a cast (`static_cast<int&&>`) — zero instructions; `y` is 5, copied. For the vector, the cast *selects* the move constructor, and that is the code that runs: it copies `v`'s three pointers into `w` and nulls `v`'s — `begin == end == end_of_storage == nullptr`, so `size() == 0`, `capacity() == 0` (libstdc++ GCC 13; libc++ and MSVC do the same for the stateless allocator). The standard guarantees only valid-but-unspecified, so operations without preconditions — `clear()`, `assign()`, `push_back()`, `size()`, destruction — are safe; reading element values is not. Convention: assume empty, reuse freely.

3. **Predict.** libstdc++: 1, 8, 16, 32, 24. libc++: 1, 8, 16, 24, 24 — only `sizeof(std::string)` changes (32 → 24), while the inline capacity moves *up*, from 15 to 22 characters. Empty struct is 1 byte because distinct objects need distinct addresses; unique_ptr is one pointer because the empty deleter is an empty base (EBO); shared_ptr is two pointers (object + control block); vector is begin/end/end_of_storage.

4. **Predict.** The block header is 16 bytes (vptr 8 for the block's own virtual hooks, strong 4, weak 4) plus the 24-byte Point3: one 40-byte request. glibc 2.35 x86-64: 40 + 8 = 48, already a multiple of 16, so a 48-byte chunk. When the last `shared_ptr` dies: `~Point3` runs immediately (strong → 0), the strong count's hold on the block is released (weak 2 → 1), and the block — dead object still inside — stays allocated while the surviving `weak_ptr`'s count is 1. Two-allocation form: the object needs a 24-byte request (32-byte chunk), and the block a 24-byte request (16-byte header + 8-byte pointer to the separate object; 32-byte chunk): **two chunks, 64 bytes minimum**, versus one 48-byte chunk and one malloc call.

5. **Predict the output.**
   ```text
   a
   b
   m
   ~m
   ~C
   ~b
   ~a
   done
   ```
   Construction: `C`'s members in declaration order (`a`, `b`), then `m`. Destruction of `f`'s locals in reverse construction order: `~m` first, then `~C` — whose *body* prints `~C` before its members are destroyed — then members in reverse declaration order (`~b`, `~a`). `done` prints after `f` returns.

6. **Bug-diagnosis (cross-chapter: 05).** The count 8 is in the 8-byte array cookie the compiler wrote *in front of* the returned pointer (Itanium ABI), alongside Chapter 05's chunk bookkeeping. `delete f` first ran exactly one `~Foo` (seven skipped), then called `operator delete(f)`. But `f` is 8 bytes past the allocation start — an *interior pointer*. glibc's `free()` reads its chunk header at `f - 8`, finds the cookie (the value 8) where the size field belongs, and aborts with `free(): invalid size` rather than corrupt its bins. `delete[] f` would read the cookie, run 8 destructors, back up past the cookie to the true chunk start, and free correctly. With a trivial destructor the compiler elides the cookie — the pointer equals the allocation start and the mismatch happens to "work" (still UB, now silent until a refactor makes the destructor non-trivial).

7. **Bug-diagnosis.** After `reserve(2)`, capacity is 2. The third `emplace_back` finds `end == end_of_storage`: it allocates a new buffer (capacity 4 on libstdc++/libc++, 3 on MSVC's 1.5x growth), *moves* the two strings, destroys them in the old buffer, and frees it. `first` pointed into that old buffer — now a free-list chunk (Chapter 05) that the next allocation can reuse — so `first->c_str()` reads freed memory: garbage, stale "a", or a crash. Iterator/pointer invalidation is exactly this relocation. Fix: `v.reserve(3)` (or an upper bound) before taking addresses — or store the index, never the pointer.

8. **Bug-diagnosis (cross-chapter: 08, 03).** The static initialization order fiasco: both globals have dynamic initializers, the linker lays each TU's `.init_array` in link order, and the loader runs them before `main` (Chapter 03) in unspecified order across TUs. If TU B's initializer runs first, the map is populated from a `std::string` that exists (its `.bss` is zeroed) but has not run its constructor; when B's map is destroyed, its nodes hold garbage — the pre-`main` segfault. C cannot have SIOF: all its initializers are link-time constants in `.data`/`.rodata`; no code runs in any order. Fix 1 — function-local static: `const std::string& suffix() { static std::string s = load_suffix(); return s; }` — initialization moves to first call, ordering follows the call graph, and the Itanium ABI makes it thread-safe with an 8-byte guard variable in `.bss`: `__cxa_guard_acquire` serializes the first init, `__cxa_guard_release` marks it done, later calls pay one load of the guard byte. Fix 2 — the nifty counter: libstdc++'s `<iostream>` declares a static `std::ios_base::Init` object in every including TU; the first constructed runs the real setup (std::cout alive), the last destroyed flushes — header inclusion becomes an order-independent initialization vote.

</details>
