# Chapter 16 — Swift and Objective-C: the compiler counts (ARC)

> **The 30-second version.** Swift and Objective-C manage the heap with *reference counting* — Python's mechanism (Chapter 14) — but the counting is done by the **compiler at compile time**, with C++'s zero-runtime-system philosophy (Chapter 09): every strong pointer copy gets an inserted `retain`, every drop an inserted `release`, exactly like RAII's scope-exit destructor calls. No collector process, no pauses, and objects die at a *deterministic* moment: the last release. The price: cycles leak unless broken by hand with `weak`, and every pointer bump costs instructions. Around that core sits forty years of Apple engineering — an 8-byte header whose spare bits hide the refcount, pools that defer death one runloop turn, side tables that zero weak references, values that copy-on-write — and a kernel that kills your process, with no exception to catch, when you exceed its memory budget.

## The compiler counts: ARC between Python and C++

Chapter 14 taught the mechanism: every object carries a count, references increment it, drops decrement it, zero means *deallocate now*. Chapter 09 taught the philosophy: if the compiler sees every construction and destruction site, it can emit the cleanup calls itself — that is why a C++ destructor call costs nothing at run time. ARC (Automatic Reference Counting, Clang 3.0 / Xcode 4.2, 2011) is the intersection:

- **Python (Chapter 14):** the *interpreter* executes `Py_INCREF`/`Py_DECREF` at run time, plus a cycle detector.
- **C++ (Chapter 09):** *you* typed the counting — `shared_ptr`'s copy constructor is library code doing the atomic increment.
- **ARC:** the *compiler* proves where references begin and end and inserts the retain/release pairs into the machine code before the program runs. Nobody types them; no runtime hunts garbage.

The whole system in one example, with the inserted calls written as comments:

```swift
final class Room {
    var name: String
    init(name: String) { self.name = name }
    deinit { print("Room \(name) freed") }
}

func audit() {
    let kitchen = Room(name: "kitchen")  // strong count = 1 (you own it)
    let pantry  = kitchen                // INSERTED: objc_retain / swift_retain -> count 2
    print(pantry.name)                   // last use of pantry
}                                        // INSERTED: release x2 -> deinit runs HERE,
                                         // on this thread, at this instruction
```

One nuance against C++: a destructor runs at the closing brace (Chapter 09); Swift's optimizer may release the moment a variable's *last use* is over, often earlier. The guarantee is "after last use, no later than scope exit" — the exact point is the compiler's choice (quiz question 4).

**What ARC buys.** Nothing corresponds to Chapter 12's GC threads or Chapter 15's scavenger — no pauses, no write barriers, no allocation-rate hitches. Destruction is deterministic: `deinit` runs at the last release, on whatever thread performed it, so file handles and camera sessions close at a moment you can observe with a `print` — versus Chapter 12's finalizers, which run "eventually, maybe never."

**What ARC costs.** First, **cycles leak** — no cycle detector exists (quiz question 8), so a parent and child referencing each other strongly survive until the process dies; the escape hatch is `weak`, and unbroken cycles are the most common bug class in iOS code (section 4). Second, **every pointer bump costs instructions**: a strong copy is an atomic increment — one `ldadda` on arm64, a few cycles hot, hundreds when two cores ping-pong the refcount word (Chapter 02's false sharing, live). A tracing collector pays nothing per pointer and everything in pauses; ARC pays always-but-tiny. You only choose *when* you pay.

History in three sentences: Objective-C had manual retain/release from the NeXT era; Apple tried an optional garbage collector on the Mac (macOS 10.5, 2007) and never shipped it on iOS; ARC landed in 2011 and the collector was deprecated. Swift (2014) was born with ARC and never had another mode.

## One word of header: the Objective-C object, and a free retain

Every Objective-C class descends from `NSObject`, and every instance begins with the same 8 bytes on 64-bit: the **`isa` pointer**, naming the object's class — Chapter 06's type word, one field instead of C++'s per-object vtable (method lookup is a runtime hash through the class). The word is nearly free real estate: an arm64 class pointer needs only ~33 bits, because user-space addresses stop at `0x1000000000`. Since the 64-bit iPhone 5s (iOS 7, 2013), the runtime packs the rest with live data — the **non-pointer isa**.

```text
The isa word — arm64, 64-bit, Apple objc4 (widely documented iOS layout;
field WIDTHS are private and have shifted across runtime releases — the
SHAPE is the lesson). 8 bytes total:

 63 62...............44 43 42 41......36 35.............3 2 1 0
+---+-------------------+---+---+---------+---------------+-+-+-+
| T |  extra_rc (19 b)  | S | W | magic(6)| shiftcls (33) |C|A|N|
+---+-------------------+---+---+---------+---------------+-+-+-+
  |   |                   |   |   |         |               | | |
  |   |                   |   |   |         |               | | +-- N = 1: packed isa
  |   |                   |   |   |         |               | +---- A: has associated objects
  |   |                   |   |   |         |               +------ C: has a C++ destructor
  |   |                   |   |   |         +---------------------- class pointer, << 3
  |   |                   |   |   |                 (mask 0x0000000ffffffff8)
  |   |                   |   |   +---------------------------------- 6 debugger magic bits
  |   |                   |   +-------------------------------------- W: weakly_referenced (side
  |   |                   |          table holds weak slots for it)
  |   |                   +------------------------------------------ S: refcount spilled to
  |   |                                                    the side table
  |   +-------------------------------------------------------------- strong count MINUS 1 (19 bits,
  |                                                                  524,287 max): retain is ONE
  |                                                                  atomic add on THIS word
  +------------------------------------------------------------------ tagged-pointer flag (Chapter 06
                                                                     tag bits, 1-bit form; tagged
                                                                     objects are not heap objects)
```

Three consequences:

- **The refcount lives in the isa.** `retain` is a single atomic add to a word the object already owns — no second counter, no lookup, no lock; only 19-bit overflow spills to the side table (bit S). Hitting zero triggers `dealloc`.
- **The header is 8 bytes; the minimum instance is 16.** Apple's malloc hands out 16-byte-aligned chunks (Chapter 05's quantum rules), so a zero-ivar `NSObject` subclass is 16 bytes: 8 live isa + 8 alignment filler.
- **Pure Swift classes spend a different 16.** A Swift instance starts with a 16-byte `HeapObject` header (Swift 5.9, 64-bit): an 8-byte *metadata pointer* (it plays the isa and doubles as the ObjC isa for interop) plus an 8-byte word packing a 32-bit strong count, a 32-bit unowned count, and flags. Weak references always live outside the object (section 4). Both worlds bottom out at 16 bytes — composed differently: ObjC crams the count *into* the class word; Swift refuses and pays a second word.

Before 2011, all of it was manual — **MRC**. The rules were learnable; the discipline was brutal. The canonical trap explains the next section:

```objc
// MRC, pre-2011: returning an object has two wrong answers and one right one
- (NSString *)badCrash {                     // wrong answer #1: crash
    NSString *s = [[NSString alloc] initWithFormat:@"%@", self.name];
    [s release];            // count hits 0: the object dies RIGHT HERE
    return s;               // caller gets a dangling pointer (Chapter 21 fodder)
}
// wrong answer #2: return s with no release at all — the caller forgets
// the +1, and it leaks on every call
- (NSString *)right {
    NSString *s = [[NSString alloc] initWithFormat:@"%@", self.name];
    return [s autorelease]; // release DEFERRED, not skipped: the pool balances
                            // the +1 one turn later. Caller: use, never release.
}
```

MRC ran on naming conventions made law: methods beginning `alloc`, `new`, `copy`, `mutableCopy` return a +1 you must release; everything else returns +0 you must not. ARC turned the convention into compiler-enforced rules — it reads selector families and emits the matching calls, and still honors those families at interop boundaries, which is why Cocoa names are load-bearing.

## Autorelease: dying exactly one runloop turn later

`autorelease` says: "release this object later, on my behalf." It exists because of the return bind — a function returning an object cannot know whether the caller will keep it, and the caller cannot know whether it owns the result. The answer is a **deferred release**: hand the object to an **autorelease pool**, a queue drained at a well-defined future moment.

The mechanism (objc4) is a page-structured stack — Chapter 04's stack rebuilt in the heap, one per thread:

```text
thread-local pool stack:

  page (4,096 bytes)                page (4,096 bytes)
+------------------------------+  +------------------------------+
| header: magic, thread id,    |  |  ~500 more slots ...         |
|   parent/child page links    |<-+------------------------------+
| ~500 slots of (object ptr)   |  (doubly-linked pages; the "hot
| [ POOL_BOUNDARY = nil ] <---+   page" cached in TLS)
| slot slot slot ...           |
+------------------------------+
  push = objc_autoreleasePoolPush(): a nil boundary token lands in the page
  pop  = objc_autoreleasePoolPop(token): send -release to every object
         above the token, top-down; possibly freeing thousands in one sweep
```

Each page is 4,096 bytes with a ~56-byte header (magic, thread, page links) and roughly 500 object slots; overflow links another page.

**Who calls pop on iOS: the runloop.** The main thread wraps *every event* — each touch, timer fire, layout pass, network callback — in a push/pop pair, draining before the thread sleeps (`kCFRunLoopBeforeWaiting`). That is the "later" in autorelease: **one runloop turn**. Objects autoreleased during your event handler die together when the handler returns.

Under ARC, `autorelease` has not gone away; it went into two hiding places:

1. **Return values across the ObjC boundary.** A callee returning a +0 object emits `objc_autoreleaseReturnValue`; the caller emits `objc_retainAutoreleasedReturnValue`. The pair calls each other's bluff through thread-local storage: the callee stashes its return address *instead of* autoreleasing; the caller checks TLS, recognizes the site, and skips the retain — the pool round trip collapses to two loads, which is why ARC returns are nearly free.
2. **Loops that create many temporaries.** One event handler allocating 100,000 autoreleased objects keeps them all alive until the *event ends* — RSS climbs in a line that on iOS ends at a Jetsam kill (section 6). Drain per iteration instead:

```objc
for (NSDictionary *row in rows) {      // 100,000 rows, ONE runloop event
    @autoreleasepool {                 // push: boundary token
        Template *t = [self.template copy];
        NSAttributedString *s = [self render:row withTemplate:t];
        [self emit:s];
    }                                  // pop: releases t, s, and every
}                                      // autoreleased byte of render:, NOW
```

Swift's spelling is `autoreleasepool { ... }` — same push/pop, worth reaching for in tight loops over ObjC-heavy APIs (image decoding, JSON, CoreData). Pure Swift loops of value types have nothing to drain: ARC releases at last use, immediately.

## Cycles, side tables, and capture lists

Refcounting's blind spot arrived in Chapter 14 (Python needed a whole `gc` module for it) and Chapter 09 (`shared_ptr` cycles leak until broken with `weak_ptr`). ARC has the same blind spot and *no detector*: you break cycles yourself with `weak` and `unowned` — and to know what those words cost, you need to know where weak references live.

**Weak references are NOT a counter in the object.** The design (ObjC runtime; Swift 5 has its own variant) is a **side table**: a fixed global array of 64 `SideTable` structures; each object's address hashes into exactly one. It owns the weak bookkeeping *outside* the object — which is exactly what makes zeroing possible:

```text
global: SideTables[64]  -- hash(object address) --> one SideTable

+----------------------------------------------------------+
| SideTable                                                |
|   spinlock           (all weak zeroing under ONE lock)   |
|   refcnts:  map<obj, stored count>     (overflow only)   |
|   weak_table:                                           |
|     map<obj, weak_entry_t>                              |
|       weak_entry_t: 4 inline weak-pointer slots,         |
|                     spilling to a malloc'd array         |
+----------------------------------------------------------+

dealloc(obj), in order:
  1. isa.W set?  lock the table -> write nil into EVERY slot
                 -> free the entry                     <- THE ZEROING
  2. isa.S set?  drop the spilled refcount
  3. isa.C/A?    run C++ dtor / release associations
  4. free(obj)   <- memory becomes reusable only here
```

Why this design:

- **Rust (Chapters 09/10)** keeps the weak count *inside the `Rc` control block*, the same allocation as the object. One fewer lookup — but a surviving `Weak` **pins the whole allocation**: the object's bytes stay on the heap (Chapter 09's zombie object) until the last weak dies, because the count a weak must read lives in the block being pinned. Zeroing needs an outside writer, and there is no outside.
- **Objective-C/Swift** move the slots outside. On dealloc, under the side table's spinlock, every slot is set to `nil` **before step 4 frees the memory** — no window exists in which a weak variable points to freed bytes. A weak *read* pairs the nil-check with a retain, so a reader racing dealloc either sees nil or wins the retain. The cost: a hash lookup and a lock at dealloc, plus a tombstone-sized entry while weak refs exist.
- **Python (Chapter 14)** sits between: `weakref.ref()` zeroes its slot (the wrapper goes dead, callbacks fire) — but as an opt-in wrapper object, while a raw borrowed `PyObject*` in a C extension has no machinery at all and dangles freely. Apple made weak a *storage class any variable can have*, which is why the machinery had to be a runtime-level global table.

**`unowned` vs `weak` — crash vs optional.** `weak var x: T?` is an optional: on target death the side table zeroes the slot, `x` reads `nil`, and you handle absence. `unowned let x: T` is non-optional and never zeroed: access after death is a **guaranteed fatal error** ("attempted to read an unowned reference but the object was already deallocated" — Swift 5.9 wording is close to that) — a checked crash, not a dangling read. `unowned` is cheaper (no side entry, no unwrapping) and correct only when its lifetime is *provably nested inside* the target's. Async callbacks violate that nesting constantly.

**The lazy-destroyed-object story** is how unowned bugs survive testing. A `dispatch_after` block fires two seconds after being armed; the test passes because the owner "hasn't deallocated yet" — nobody saw a crash. The owner's destruction is *lazy*: its last strong reference may sit in an autorelease pool that drains per runloop event, or on a queue that runs later. Then a release build — which schedules all of this differently — moves the dealloc across the two-second boundary, and the crash appears in the field. Destruction timing is deterministic, but *which moment* it lands on depends on pool drains and scheduling, and the two build configurations choose differently.

**Captures: closures are the other half of every cycle.** A closure that escapes its scope (stored, or kept by an API) cannot borrow the stack (Chapter 04's frames die), so the compiler moves each captured variable into a heap **context object** — a reference-counted box with one field per captured variable: V8's environment design from Chapter 15, statically laid out. Each field defaults to a **strong** reference. `[weak self]` and `[unowned self]` are capture lists: they change how *one field of that box* is stored.

```text
escaping closure { [weak self] in ... }

+------------------+     +------------------------------+
| function pointer |     | context object (refcounted   |
| context ptr ------+---> | heap box — Chapter 15's      |
+------------------+     | environment, statically      |
                         | known fields:                |
                         |   [ weak self: Player? ]  ----+--> side table slot
                         |   [ other captured vars ]    |
                         +------------------------------+
```

Now the leak every iOS app ships once:

```swift
final class ScoreBoard {
    var score = 0
    private var token: NSObjectProtocol?

    func start() {
        token = NotificationCenter.default.addObserver(
            forName: .tick, object: nil, queue: nil
        ) { _ in
            self.score += 1        // the context box's field is a STRONG self
        }
    }
    deinit { print("ScoreBoard freed") }   // never prints
}
```

Walk the graph the Memory Graph debugger would draw:

```text
NotificationCenter (process-global, never deallocates)
      | strong (until removeObserver)
      v
   token --strong--> closure --strong--> ScoreBoard --strong--> token
   CYCLE: closure -> ScoreBoard -> token -> closure
```

The cruel part is the "fix" everyone writes first: "I'll remove the observer in `deinit`." `deinit` never runs — the cycle keeps the object alive, so the cleanup is unreachable. Order matters: **break the cycle first, then clean up in `deinit`:**

```swift
token = NotificationCenter.default.addObserver(
    forName: .tick, object: nil, queue: nil
) { [weak self] _ in
    guard let self = self else { return }  // weak: slot zeroed at ScoreBoard's death
    self.score += 1
}
deinit {
    if let token { NotificationCenter.default.removeObserver(token) }
    // reachable NOW: weak broke closure -> self, deinit CAN run; removing
    // the token then releases center -> token -> closure in that order
}
```

Same shape, other costumes: `Timer.scheduledTimer(withTimeInterval:repeats:) { self.tick() }` (runloop → timer → closure → self, until `invalidate()`), and delegates — which is why forty years of Cocoa convention declares every `delegate` property `weak`. A strong delegate is a cycle with a timescale of "forever."

## Value semantics: memcpy, niches, and copy-on-write

Classes are the refcounted world. Swift's structs and enums are Chapter 06's value layouts — and they change the economics completely.

**A struct assignment is a memcpy** — no refcount, no allocator, no identity; the bytes are duplicated. But the copy is **shallow**: if a field holds a reference, the memcpy duplicates the *pointer* and ARC inserts a retain for that field — two structs, one shared pointee (Chapter 06's "copy the pointer, not the pointee"):

```swift
struct Tag { var id: Int; var active: Bool }    // size 9, stride 16
var a = Tag(id: 7, active: true)
var b = a              // one 9-byte copy; no heap traffic
b.active = false       // a.active is still true: separate storages

struct ViewTag { var view: UIView; var z: Int } // view is a strong reference
var t1 = ViewTag(view: v, z: 0)
var t2 = t1            // bytes copied AND view's refcount +1: v itself is SHARED
```

**Enums are tagged unions** (Chapter 06), compiled to the layouts Chapter 10 derived for Rust: single-payload enums exploit *niches* — `Optional<SomeClass>` is exactly 8 bytes on 64-bit (Swift 5.9), `nil` reusing the all-zero pointer pattern no object can legally have, just like `Option<Box<T>>`. Payloads without spare bits get a tag byte: `enum State { case idle; case running(Double) }` has size 9, stride 16, alignment 8 — an 8-byte `Double` plus a 1-byte discriminant, padded to stride by Chapter 06's rules.

**Copy-on-write is the bridge between the two worlds.** If structs copy eagerly, `Array`, `String`, `Dictionary`, and `Set` — all structs — would memcpy megabytes per assignment. Instead each holds one reference to a reference-counted *backing store*, and copying the struct copies that pointer plus one retain (Chapter 09's `shared_ptr` move, hidden inside a value type):

```swift
var a: [Int] = [1, 2, 3]
var b = a              // O(1): struct memcpy + ONE retain of the buffer
b.append(4)            // isKnownUniquelyReferenced(&b.buffer) == false
                       // -> allocate a NEW buffer [1,2,3,4]; a never notices
a[0] = 99              // a's buffer is unique now: write in place, no allocation
```

```text
after `var b = a`                    after `b.append(4)`

a [ buf*, 3 ]---+                    a [ buf*, 3 ]----> [ RC 1 | cap 3 | 1 2 3 ]
                |                                         (untouched, now unique)
                v
      +------------------+
      | RC 2 | cap 3     |                b [ buf*, 4 ]----> [ RC 1 | cap 4 | 1 2 3 4 ]
      | 1 | 2 | 3        |                                  (NEW buffer)
      +------------------+
                ^
b [ buf*, 3 ]---+        ONE buffer, two owners, refcount 2
```

The trick hangs on `isKnownUniquelyReferenced(&buffer)`: a stdlib check that the backing store's strong count is exactly 1, run under the exclusivity a `mutating` method already guarantees. Unique → mutate in place; shared → copy, then mutate. To you it *looks* like deep value semantics; to the machine it is one retain plus a rare copy. **The trap** — hold a second handle across a loop of mutations and the copy runs every iteration:

```swift
var a = Array(repeating: 0, count: 100_000)     // 800 KB buffer
for _ in 0..<10_000 {
    let b = a          // +1: buffer shared again
    a.append(0)        // NOT unique -> copies 800 KB... EVERY iteration
    _ = b              // b's last use keeps the alias alive past the append
}
// ~8 GB of memcpy, quadratic where the code looks linear
```

The rule: never hold an alias across a mutation; copy once, on purpose. The profiler's fingerprint is malloc traffic scaling with *loop count*, not data size.

**String compresses all of this into 16 bytes.** `MemoryLayout<String>.size == 16`, always, on 64-bit Swift (5.9): two words that are either a whole string, or a pointer plus bookkeeping:

```text
Swift 5.9, 64-bit: a String is 16 bytes, two words

small string "ARC" (3 bytes, never touches the heap):
+--------------------------------+--------------------------------+
| 41 52 43 00 00 00 00 00        | 00 00 00 00 00 00 00 [disc]    |
| 'A''R''C' + zero fill          | up to 7 more bytes, then byte |
+--------------------------------+ 15: the DISCRIMINATOR — small- |
  word 0: UTF-8 bytes 0..7          string flag + isASCII flag +  |
  word 1: UTF-8 bytes 8..14         the inline count              |
                                     +--------------------------------+
  15 UTF-8 bytes inline, maximum.

large string (16+ bytes):
+--------------------------------+--------------------------------+
| pointer to _StringStorage ---->| count (bytes) + flags          |
| (heap, refcounted, UTF-8,      | (isASCII, shared/literal, ...) |
|  16-byte aligned)              |                                 |
+--------------------------------+--------------------------------+
```

Byte 15 is the discriminator — Chapter 06's tag bits deciding the meaning of the other 15 bytes. Since Swift 5 (2019), native strings are stored as **UTF-8 in place**: the count is a byte count, storage is tail-allocated after the header, and indexing is O(n), because UTF-8 code units are 1–4 bytes — byte position is not character position. UTF-8's self-synchronization (a decoder resyncs within 3 bytes) is what makes arbitrary slicing safe; the cost is that `String`'s subscript takes an opaque `String.Index`, never an integer.

One honest paragraph on why `String` is *weird*: a Swift `Character` is an **extended grapheme cluster** — a user-perceived character, not a byte, code unit, or code point. `"é"` is one Character as one scalar (U+00E9, 2 UTF-8 bytes) or as two (e + combining U+0301, 3 bytes). The family emoji is **one** Character of 7 scalars and 25 UTF-8 bytes — guaranteed heap. Consequently `s.count` (Characters) is an O(n) walk with Unicode grapheme-breaking tables, while `s.utf8.count` is O(1), a stored number; "15" in "15-byte small string" means *bytes*. That is the price of counting what humans see, and the reason `String` has no `s[i]`.

## No swap, no mercy: the Apple memory contract

Everything above runs inside a memory regime unlike a desktop's (Chapter 03's swap never appears here):

- **iOS has no swap.** Anonymous dirty pages are never written to disk — flash wear, battery, latency. File-backed code pages can be evicted and re-fetched (the bytes still exist in the app bundle); *your* data cannot. The only levers are freeing and compression.
- **Memory compression** (macOS 10.9 / iOS 7, 2013): the kernel's compressor thread squeezes cold dirty pages in place with an Apple-tuned WKdm-class algorithm — typically ~3x — and decompresses on fault.
- **Per-process limits, enforced by death.** There is no `malloc` returning NULL and no OOM exception to catch. When your footprint crosses the limit, the kernel's **Jetsam** watchdog kills the process (SIGKILL, by priority class); the crash log's termination reason reads like `perprocess-limit`. Extensions get the tightest budgets — tens of MB — foreground apps a few GB, all device- and role-dependent (Chapter 22 has the deep dive, including the memory-warning path that precedes the kill).

ARC's per-object overhead is small — a 16-byte header and an atomic add per pointer. iOS apps burn their budget by *keeping pages dirty*: a strong-held cache of decoded images, a repeating timer whose closure captured half the object graph, an observer nobody removed. Those pages count against your limit until you free them, and nothing frees them for you — which is why **`NSCache` exists next to `Dictionary`**: same dictionary-shaped API, but it evicts its contents under memory pressure (enforces `totalCostLimit`, is thread-safe, does not copy keys). A `Dictionary` holds its bytes until you say otherwise; on iOS, that is how you meet Jetsam.

## What insiders know

- **Retain is one atomic add on the isa word.** The non-pointer `isa` packs the strong count into the header on arm64 (19 bits of `extra_rc`), so `retain`/`release` touch a cache line the object already owns; the side table is an *overflow* path, hit only past ~524,287 retains or for weak bookkeeping.
- **The two 16-byte headers are different 16 bytes.** A zero-ivar `NSObject` subclass is 16 bytes (8-byte isa + 8 filler from malloc's 16-byte quantum); a propertyless pure Swift class is also 16 — metadata pointer plus a packed strong/unowned count word. `@objc`-exposed Swift classes make one header serve both runtimes.
- **`string.count` is not `string.utf8.count`.** The first walks grapheme clusters with Unicode tables — O(n); the second reads a stored byte count — O(1). The family emoji: 1 Character, 7 scalars, 25 UTF-8 bytes — always heap. Interview trap: "how many bytes is this String?" has three right answers.
- **`unowned` in async code is a latent crash.** Correct only when the closure's lifetime is provably nested in the owner's — escaping callbacks (timers, notifications, `dispatch_after`, Combine sinks) are *scheduled*, not nested. Debug and release builds move dealloc timing; the crash appears in exactly one of them.
- **Deterministic `deinit` is a debugging superpower.** A `deinit { print(...) }` tells you the exact last-release moment — versus Chapter 12's maybe-never finalizers. Two caveats: `deinit` runs on *whatever thread dropped the last reference*, and lazy destruction means a "dead" object may dealloc on the *next test* or the *next touch*.
- **Swift's "inline" refcount word can leave the object.** In Swift 5, once an object needs slow counts (weak references, overflow), its 8-byte header refcount word is *swizzled into a pointer* to a heap side cell holding strong/unowned/weak counts — the `has_sidetable_rc` escape hatch, but the header word itself changes meaning.
- **Toll-free bridging is trivia with teeth.** `CFStringRef` and `NSString` are the same object at run time; `CFRetain` on a bridged object *is* `retain`. The `CFAllocator*` first parameter all over CoreFoundation APIs is Chapter 19's pluggable-allocator idea surfacing in a 1990s API.

## See it yourself

**1. The layouts, derived from Chapter 06** (any Mac with Xcode, or a swift.org toolchain — the `MemoryLayout` lines work on Linux too):

```bash
cat > layout.swift <<'EOF'
import Foundation              // macOS: NSObject, class_getInstanceSize, malloc_size

class Empty {}                          // pure Swift class, no superclass
class ObjCRoot: NSObject {}             // ObjC-rooted

struct S { var a: UInt32; var b: UInt8 }
enum State { case idle; case running(Double) }

print("Int:    ", MemoryLayout<Int>.size, MemoryLayout<Int>.stride, MemoryLayout<Int>.alignment)
print("String: ", MemoryLayout<String>.size, MemoryLayout<String>.stride, MemoryLayout<String>.alignment)
print("S:      ", MemoryLayout<S>.size, MemoryLayout<S>.stride, MemoryLayout<S>.alignment)
print("State:  ", MemoryLayout<State>.size, MemoryLayout<State>.stride, MemoryLayout<State>.alignment)
print("Empty?: ", MemoryLayout<Empty?>.size)                 // reference + nil niche
print("Empty instance: ", class_getInstanceSize(Empty.self)) // the OBJECT, not the reference
print("ObjC instance:  ", class_getInstanceSize(ObjCRoot.self))
let e = Empty()
print("malloc_size: ", malloc_size(Unmanaged.passUnretained(e).toOpaque()))
EOF
swift layout.swift
```

```text
Int:     8 8 8
String:  16 16 8
S:       5 8 4
State:   9 16 8
Empty?:  8
Empty instance:  16
ObjC instance:   16
malloc_size:  16
```

Derive each before running (Chapter 06): `Int` is one word. `String` is the two-word representation, small or large, always 16. `S` is 4+1 bytes, no internal padding, size 5; stride rounds up to the alignment (4) → 8. `State` is an 8-byte `Double` plus a 1-byte tag → 9, stride 16 at alignment 8. `Empty?` is 8: `nil` occupies the pointer's illegal all-zero pattern (the niche — compare `Option<Box<T>>`, Chapter 10). And the trap the last lines expose: `MemoryLayout<Empty>.size` prints **8** — the *reference*, not the 16-byte instance.

**2. Deterministic death, on this thread, right now.** Append and rerun:

```swift
final class T { deinit { print("T freed") } }
do {
    let t = T()
    print("in scope")
}
print("after scope")
```

```text
in scope
T freed
after scope
```

Run the equivalent in a GC'd language (Chapters 12, 15) and the finalizer line may print late or never; here the ordering is a guarantee you can build a debugging technique on.

**3. Xcode Memory Graph: see the cycle.** Run any app under the debugger, pause it, then press the Debug Memory Graph button (bottom of the debug area, or Debug → Debug Memory Graph). The navigator lists heap objects grouped by class with live instance counts; click an instance and the graph shows its fields and *back references* — every strong holder, as arrows. A retain cycle is literally a loop: click from the closure's context box to `self`, from `self` to the token, from the token back to the closure. Xcode flags suspected leaks with a warning badge; the instance-count column is your leak meter (42 `ScoreBoard` instances after 42 navigations is a diagnosis, not a hint).

**4. Command line: leaks with allocation stacks.** No GUI needed on macOS:

```bash
MallocStackLogging=1 /path/YourApp.app/Contents/MacOS/YourApp &
leaks $!        # or: leaks YourApp
#   Process:        YourApp [1234]
#   2 leaks for 32 total leaked bytes.
#   Leak: 0x7f8b1c000400  16 bytes
#     Call stack: [allocator] ... -[ScoreBoard start] ...
```

The stack lands on the allocation site — walk up it to the closure capture. The same engine powers Instruments' Leaks template (Product → Profile), which adds a live allocations track: watch the COW trap's malloc traffic scale with loop count instead of data size.

## How to use this knowledge

- **Debugging the leak that isn't a leak yet.** "Deinit never prints" is a cycle until proven otherwise: run the Memory Graph, follow strong edges backward, and the loop is the bug. "Deinit prints *late*" is a pending pool drain or a scheduled closure — now you can say which.
- **Performance decisions.** Hold aliases across array mutations and you buy a copy per iteration — `isKnownUniquelyReferenced` explains both the trap and how to build your own COW types. Wrap ObjC-heavy loops in `autoreleasepool`. In hot paths, count refcount traffic: four `Int` fields copy free; four class references cost four atomic increments per copy.
- **Design choices.** Delegates weak by default; capture lists in every escaping closure; `NSCache` — not `Dictionary` — for anything purgable, because on iOS the alternative is Jetsam.
- **Incidents you can now explain.** The idle app killed with `perprocess-limit` (a repeating timer's closure holding dirty pages); the release-only callback crash (unowned plus lazy destruction); the "leak" that was one pool turn of autoreleased images; 8 GB of memcpy in a loop that "looked linear."
- **Interview answers with derivations.** Why ARC is not a garbage collector (no collector process, deterministic, cycles leak — Chapter 09 vs 12). Why `weak` can zero and `Rc::Weak` cannot (where the bookkeeping lives). Why `String` is 16 bytes and `count` is O(n). What the isa word packs on arm64.

## Connections

- **Chapter 09 (C++):** ARC is the compiler writing your `shared_ptr` copy/assign/destroy calls; the isa-packed count is the control block compressed into the header; libc++'s 24-byte string with 22-char SSO vs Swift's 16/15.
- **Chapter 14 (Python):** the same mechanism executed at run time by the interpreter, plus the cycle detector Swift refuses to ship — and the borrowed-reference discipline where C extensions dangle.
- **Chapter 10 (Rust):** niches are shared (`Optional` of a reference = 8 bytes on both sides); `Rc::Weak`'s in-object count vs the side table is the zeroing-vs-pinning trade.
- **Chapter 15 (JavaScript/V8):** escaping closures' context boxes are V8's environments with static layouts; non-escaping ones borrow the stack (Chapter 04); hidden classes are the dynamic twin of the isa/metadata word.
- **Chapter 12 (Java):** the anti-model — collector threads, pauses, maybe-never finalizers, versus deterministic `deinit` on the last-releasing thread.
- **Chapter 06 (object layout):** padding, tag bits, and niches derive every `MemoryLayout` number in the lab; the non-pointer isa is tag bits at production scale.
- **Chapter 05 (the heap):** the 16-byte malloc quantum explains minimum instance sizes; side-table entries and COW buffers are ordinary heap allocations.
- **Chapter 02 (the hierarchy):** refcount words are shared writable cache lines — atomic increments on hot objects ping-pong between cores.
- **Chapter 03 (virtual memory):** no swap on iOS is where the platform contract starts; compression is the kernel's only dirty-page lever this side of death.
- **Chapter 22 (mobile memory):** Jetsam priorities, the compressor, and the memory-warning path — the deep dive this chapter defers to.
- **Chapter 21 (memory security):** tagged pointers, unowned-after-death (a *checked* dangling read, not an exploitable one), and why packed isas complicate pointer forgery.
- **Module 04 (decompilation, queued):** reading Objective-C metadata and isa fields is step one of recovering class hierarchies from Apple binaries.

*Forward links — where the rest of the program picks this chapter up:*

- **Module 19 ch04 (ARC as proof)** — retain/release are the proof inlined: the compiler places the certificate, the SIL shows the instructions.
- **Module 18 ch05 (key material)** — ARC on crypto buffers: when the compiler's insertion points become the timing signal.

## Quiz

1. **Concept.** In terms of Chapters 14 and 09: what is ARC mechanically, and what did the 2011 compiler change relative to MRC? Derive the two standing costs of the design — and name the pair of costs a tracing collector (Chapter 12) pays instead.

2. **Predict the numbers** (Swift 5.9, 64-bit — derive each from Chapter 06): `MemoryLayout<String>.size`; `MemoryLayout<P?>.size` for a class `P`; `MemoryLayout<T>.size/stride/alignment` for `struct T { var a: UInt32; var b: UInt8 }`; and for `enum E { case off; case dim(Double) }`.

3. **Predict the behavior.**
   ```swift
   var a: [Int] = [1, 2, 3]
   var b = a
   b.append(4)
   a[0] = 99
   a.append(5)
   ```
   How many backing-store allocations happen in total, how many buffers are live at the end, and which operation triggered each and why?

4. **Predict the legal outputs.**
   ```swift
   final class P { deinit { print("P gone") } }
   func f() {
       let p = P()
       print("made")
       use(p)                    // p's last use
       print("after last use")
   }
   f()
   print("after f")
   ```
   Give two orderings of the four lines that are both valid, state what is *guaranteed* about where "P gone" can appear, and contrast with the C++ rule (Chapter 09) and the Java rule (Chapter 12).

5. **Bug diagnosis.** A view controller's `deinit` never runs; the Memory Graph shows `NotificationCenter -> token -> closure -> self -> token`:
   ```swift
   final class VC: UIViewController {
       private var token: NSObjectProtocol?
       override func viewDidLoad() {
           token = NotificationCenter.default.addObserver(
               forName: .refresh, object: nil, queue: nil) { _ in
               self.reload()                                   // (A)
           }
       }
       deinit {                                                // (B)
           if let token { NotificationCenter.default.removeObserver(token) }
       }
   }
   ```
   Why does (B) never fix the problem it was written for, what exactly is at (A), and what is the correct fix — including why its ordering matters?

6. **Bug diagnosis.** A field crash report: `Fatal error: Attempted to read an unowned reference but the object was already deallocated`, inside a `dispatch_after` callback; unreproducible in debug builds. Explain the lifetime nesting `unowned` requires, why an async callback violates it, and why the build configuration changes the outcome.

7. **Bug diagnosis.**
   ```swift
   var rows = Array(repeating: Row.empty, count: 50_000)
   for i in 0..<10_000 {
       let snapshot = rows
       rows[i % 50_000].flag = true
       _ = snapshot
   }
   ```
   The profiler shows malloc traffic of roughly 10,000 × 1.2 MB. What is `snapshot` doing to every mutation, what check decides this, and what are two fixes?

8. **Cross-chapter.** CPython ships a cycle detector; Swift and Objective-C ship none. Explain what each ecosystem does instead, why `Rc::Weak` (Chapters 09/10) cannot zero the weak pointer while Objective-C/Swift `weak` can — deriving the difference from *where the weak bookkeeping lives* — and why "the kernel reclaims everything eventually" is a serious answer on iOS (Chapter 22).

<details>
<summary>Answers</summary>

1. **Concept.** ARC is Python's runtime mechanism (per-object count, +1 per reference, deallocate at zero) with the increments and decrements inserted by the compiler at compile time, like C++'s scope-exit destructor calls — the MRC rules made automatic. Cost one: cycles leak — a mutual pair at count 2 never reaches zero and no detector exists; breaking cycles is manual `weak` discipline. Cost two: every pointer copy is an atomic increment (an `ldadda` on arm64; worst case a cross-core cache-line transfer, Chapter 02). A tracing collector reaches cycles naturally (unrooted means dead) and pays nothing per pointer — but it pays collector threads plus stop-the-world pauses, and nondeterministic finalization instead of a death moment you can print. You choose *when* you pay: always-but-tiny or batched-but-huge.

2. **Predict.** `String` = 16 (the two-word representation, small or large). `P?` = 8 — one word; `nil` reuses the all-zero bit pattern no legal object pointer has (the niche, exactly `Option<Box<T>>`, Chapter 10). `T` = size 5, stride 8, alignment 4: 4+1 bytes, no internal padding; stride rounds size to the max member alignment. `E` = size 9, stride 16, alignment 8: an 8-byte `Double` payload plus a 1-byte tag (a `Double` has no spare bits), padded to stride. Bonus trap: `MemoryLayout<P>.size` is 8 too — the *reference*, not the 16-byte instance.

3. **Predict.** Three allocations; two live buffers. (1) The literal allocates the first buffer. `var b = a` allocates nothing — struct copy plus one retain (RC 2). (2) `b.append(4)`: mutation while `isKnownUniquelyReferenced == false` → COW copy for `b`; `a` becomes unique again. `a[0] = 99`: in-place write, nothing allocated. (3) `a.append(5)`: `a` is unique but full (count 3, capacity 3) → growth reallocation, triggered by *capacity*, not sharing; the old buffer is freed. False intuition to catch: the copies are lazy and happen only at mutation of a shared buffer.

4. **Predict.** Ordering A (release at scope exit, -Onone): `made`, `after last use`, `P gone`, `after f`. Ordering B (release at last use, optimized): `made`, `P gone`, `after last use`, `after f`. Guaranteed: `deinit` runs after `p`'s last use and no later than scope exit — always after `made`, always before `after f`. C++ fixes the point by rule: the destructor runs at the closing brace, always (Chapter 09); Java's finalizer has no ordering guarantee and may run never (Chapter 12). Lesson: depend on the *bounds*, not the point — a `deinit` print proves death happened, it does not timestamp it to an instruction.

5. **Bug-diagnosis.** The cycle is closure → self → token → closure, with the process-global center also holding the token until `removeObserver`. Line (B) is unreachable cleanup: `deinit` runs only when the strong count hits zero, and the cycle guarantees it never does — "remove the observer in deinit" is dead code that looks like a fix. Line (A) is the strong capture: the escaping closure's heap context box stores `self` as a strong field (the default). Fix: `[weak self]` with `guard let self` — breaking closure → self lets the count reach zero, `deinit` runs, and only then does (B) execute, releasing center → token → closure in order. Break the cycle first; clean up in the now-reachable `deinit`.

6. **Bug-diagnosis.** `unowned` requires the reference's lifetime to be nested strictly inside the target's — the closure must die before the object can. A `dispatch_after` block's lifetime is *scheduled*, not nested: the delay can outlive the owner, and the block then reads an object whose `deinit` already ran; the runtime's unowned check turns that into the fatal error (a checked crash, not a dangling read). Build differences: destruction timing depends on autorelease-pool drains and queue scheduling, which differ between debug and release — in the debug build the object's lazy destruction lands *after* the callback, hiding the bug. Fixes: `[weak self]`, or cancel the stored `DispatchWorkItem` in `deinit`.

7. **Bug-diagnosis.** Every `rows[i % 50_000].flag = true` mutates a *shared* buffer: `snapshot` still holds a strong reference (its last use is after the mutation), the uniqueness check returns false, and the COW path copies the entire ~1.2 MB backing store before flipping one flag — 10,000 full copies. The check is the stdlib's `isKnownUniquelyReferenced`-style strong-count == 1 test, run under the `mutating` method's exclusivity. Fixes: don't hold the alias across the mutation (move `let snapshot = rows` out of the loop, or drop it), or build into a separate local and assign once. The law: value-semantics cost scales with *mutations while shared*, not with copies.

8. **Cross-chapter.** Apple's answer is discipline plus containment: `weak`/`unowned` capture lists and weak delegates break cycles by hand, `autoreleasepool` bounds temporary pile-ups, and any cycle that slips through is reclaimed wholesale at process death — Jetsam guarantees that death is bounded and total on iOS (Chapter 22), so "leak until exit" is a bounded failure mode. CPython instead pays a tracing cycle detector: periodic passes over container objects (Chapter 14). The weak difference is *location*: Rust keeps the weak count in the control block — the same allocation as the object — so a surviving `Weak` pins the block (the object's bytes persist until the last weak dies) and no outside writer exists to zero the pointer; `upgrade` checks the dead flag at read time instead. Objective-C/Swift store weak slots in the global side table, *outside* the object: on dealloc, under the side table's spinlock and before the memory is freed, every slot is set to `nil` — zeroing is possible precisely because the slots are not inside the dying object. Python's `weakref` is the middle design: an opt-in wrapper marked dead at target death, while raw borrowed pointers in C extensions stay unprotected.

</details>
