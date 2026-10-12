# Chapter 05 — Use-after-free

> **The 30-second version.** A use-after-free is a bug in *time*, not in space: an object is freed while pointers to it survive, and the allocator hands the same bytes to the next same-size request. If the attacker makes that request — a heap spray of controlled bytes — the next use of the stale pointer operates on attacker data. In C++ the first 8 bytes of a polymorphic object are the vptr, so a virtual call through reclaimed memory is an indirect call into attacker-chosen code. Browsers, with millions of C++ objects living script-driven lifetimes, are the natural habitat — which is why use-after-free tops the modern bug charts while canaries, NX, and ASLR look away: they defend spatial and control-flow invariants, and this bug breaks a *lifetime* invariant none of them models. Every mitigation narrows the odds; ownership removes the compile-time subset outright.

## A bug the classic ladder cannot see

Chapters 02–04 attacked *spatial* corruption — overwrite saved RIP, ROP past NX, poison glibc's chunk metadata — and the ladder grew to match: canaries guard a fixed location, NX guards which pages execute, ASLR hides addresses from a stolen control flow.

A use-after-free writes nothing illegal anywhere. The `delete` is legal; the chunk returns to a free list in perfect health; the later use is a normal read of a mapped address through a pointer whose validity the language never tracked. The broken invariant is *temporal* — "this pointer points at a live object" — and no C or C++ runtime maintains it. glibc catches double-frees (the tcache `key`, chapter 04) but is blind to a late use: by the stale read, the bytes legitimately belong to a new owner. The ladder priced out spatial corruption; the flagship targets then moved to the one memory model where temporal errors are epidemic.

A 1998 daemon is C, flat buffers, a process per connection, a few dozen structs dying with the connection. A 2018 browser is a C++ object graph of millions of live objects in one long-lived process — plus the decisive ingredient: *JavaScript can destroy almost any object at almost any moment* (close a tab, remove a node, drop a reference). RAII (module 01, chapter 09) ties lifetime to *scope*: the closing brace is the destructor call. A DOM node's lifetime is not a scope; it ends on an *event* at an arbitrary stack depth, while observers, callbacks, and posted tasks hold references no brace ever owned. Marked by source: MSRC reported ~70% of its CVEs are memory-safety bugs (BlueHat 2019, 2006–2018 data); Chromium's reporting lands in the same range, and within the renderer's memory-safety slice, use-after-free has for years been the largest single subclass.

## The three-step pattern: free, reclaim, confuse

A dangling pointer alone is a crash. An exploit needs determinism — control over *what the freed bytes contain* at the instant of the stale use — hence three steps, and every browser write-up is a concrete instantiation of them:

1. **Free** — trigger the victim's destruction while a reference survives. This is the bug; everything after is engineering.
2. **Reclaim** — allocate into the freed slot with attacker-chosen contents. The allocator's contract helps: same size class, prompt allocation, and (glibc tcache, chapter 04) LIFO order that returns the most recently freed chunk of that size first.
3. **Confuse** — use the stale pointer. The old code now executes against the new, attacker-shaped contents — a type confusion. If the use is a virtual call, the replacement's first 8 bytes are read as a vptr, and the call lands wherever they say.

The whole pattern in one minimal C++ program — the exact source the lab runs:

```cpp
// uaf.cpp -- the three-step pattern in miniature (module 02, chapter 05)
#include <cstdio>

struct Session {                       // the victim: polymorphic, 16 bytes
    virtual void ping() { std::printf("legit Session::ping\n"); }
    virtual ~Session() = default;      // virtual dtor: keep the demo one-bug-clean
    char pad[8]{};                     // sizeof(Session) == 16: vptr + pad
};

struct Evil {                          // the replacement: attacker bytes, 24 bytes
    void** fake_vtable;                // lands exactly on Session's vptr (offset 0)
    long payload[2]{};
};

static void attacker_fn() {            // where the stale call must land
    std::printf("!!! stale call landed in attacker-chosen code\n");
}

static void* fake_vt[4] = {            // fake vtable in .data: every slot points
    (void*)&attacker_fn, (void*)&attacker_fn,
    (void*)&attacker_fn, (void*)&attacker_fn
};

int main() {
    Session* registry[1];              // raw, non-owning back-reference
    registry[0] = new Session();       // step 0: live object, registered

    delete registry[0];                // step 1: FREE -- registry[0] goes stale

#ifndef NO_SPRAY
    Evil* e = new Evil();              // step 2: SPRAY -- same chunk size (32 B)
    e->fake_vtable = fake_vt;          //   reclaims the slot; offset 0 is now an
                                       //   attacker-chosen vptr
    std::printf("victim slot = %p, replacement = %p\n",
                (void*)registry[0], (void*)e);
#endif

    registry[0]->ping();               // step 3: stale virtual call through the
}                                      //   reclaimed, attacker-chosen slot
```

Why is *same size class* load-bearing? `sizeof(Session)` is 16, `sizeof(Evil)` is 24; both round to a 32-byte glibc chunk (payload + 8-byte header, 32 minimum — chapter 04's arithmetic). Same bin, LIFO: `new Evil` receives *exactly* the chunk just freed. On PartitionAlloc the constraint reappears as a bucket match plus a partition match (see *What insiders know*). The bytes and the call — module 01's chapters 06 and 09, read adversarially:

```text
STEP 1: free                    STEP 2: spray to reclaim          STEP 3: stale virtual call

 Session* s = 0x...2b0          registry[0] still holds           registry[0]->ping()
 +--------------+               0x...2b0 (STALE)                     |
 | vptr    8 B  |  delete s        |                                v
 +--------------+  -> tcache,      v                        mov (%rax),%rax  <- slot+0:
 | pad (8 B)    |    32-B bin,  +---------------+             fake_vtable (attacker ptr)
 +--------------+    LIFO head  | fake_vtable  |          mov (%rax),%rdx  <- fake_vt[0]:
        |                      | (attacker    |             attacker_fn
        v                      |  pointer)    |          call *%rdx       <- the stale call
   Session vtable               +---------------+            executes ATTACKER code
   +------------------+         | payload 16 B |             with this = the slot
   | [0] ping         |         +---------------+
   | [1] ~Session     |         replacement object, same 32-B chunk:
   | [2] ~deleting    |         offset 0 is ATTACKER-CHOSEN bytes
   +------------------+         attacker's fake vtable (in .data here;
                                in a real exploit, sprayed heap):
                                +------------------+
                                | [0] attacker_fn  |  <- the call lands HERE
                                +------------------+

 p->ping() compiles to (g++ 14.2, -O0, Itanium ABI; vtable slots follow
 DECLARATION order, so ping -- declared first -- is slot [0], and the
 deleting destructor is slot [2]):
    mov  (%rax),%rax    ; vptr   = *(p + 0)      <- attacker bytes after reclaim
    mov  (%rax),%rdx    ; target = *(vptr + 0)   <- fake_vt[0] = attacker_fn
    call *%rdx          ; attacker_fn(this = p)
```

Two loads, one indirect call — after the reclaim, every address except the first is attacker-influenced. There is no type tag on the object besides those first 8 bytes, which is why replacing them replaces the type.

Three flavors of confusion, escalating in elegance:

- **Fake vtable** (the lab's route): raw attacker bytes; the vptr aims at a fake vtable of chosen addresses. Requires an indirect call.
- **Wrong-but-real object** (the browser workhorse): reclaim with a *legitimately constructed* object of another class whose fields script can set; the stale C++ pointer reads those numbers as pointers, lengths, or refcounts.
- **Data-only UAF**: the stale use is a field write or a `refcount--`, not a call — no hijack, no CFI violation.

## Where use-after-frees breed

Four shapes account for most of the real-world crop. Each shares one property: the free is triggered from *below* the code that still holds the pointer — a callback, a destructor, a queued task — control flow the holder never observes.

**Shape 1 — notify-during-destruction (observers).** The subject's destructor informs its observers, and the handlers mutate the list being iterated:

```cpp
class Tab {
    std::vector<Observer*> observers_;        // raw, non-owning
public:
    ~Tab() {
        for (Observer* o : observers_)        // BUG SHAPE: iterate while handlers
            o->onTabClosed(this);             // can unregister or destroy others --
    }                                         // observers_[i+1] may already be freed
};
```

An `onTabClosed` handler that tears down a sibling observer (or the `Tab` itself) makes the loop's *next* iteration our `registry[0]`. This is the most common UAF shape in browser codebases; Blink grew dedicated patterns for exactly it.

**Shape 2 — destructor-triggered destruction.** A destructor drops a refcount, the count hits zero, and an object the *caller* still points at dies as a side effect of leaving a scope. The member-scope version is a teardown-order bug — module 01 chapter 09's reverse-construction-order rule, read adversarially:

```cpp
class Panel {
    Toolbar toolbar_;   // declared FIRST  -> destroyed LAST  (~Toolbar runs last)
    Canvas  canvas_;    // declared SECOND -> destroyed FIRST
    // ~Toolbar() calls canvas_.flush() -- canvas_ is already destructed: UAF
};
```

Members die in reverse declaration order because a later member may depend on an earlier one; the bug is a dependency arrow pointing the wrong way.

**Shape 3 — async cancellation.** Work posted to a future tick with no ownership and no cancellation token:

```cpp
void Chart::scheduleRefresh() {
    task_runner_->PostTask(FROM_HERE,
        BindOnce(&Chart::refresh, Unretained(this)));  // BUG SHAPE: no ownership,
}                                                      // no weak handle
// Tab closes between Post and Run -> refresh() runs on a freed Chart
```

The post-to-run window is unbounded, destruction is script-triggered, and the crash reproduces "only on fast tab close" — which is why these survive QA.

**Shape 4 — DOM teardown / reentrancy during mutation.** Removing a node fires listeners; script re-enters a half-torn-down tree; finalizers run during garbage collection while script still holds handles. The name for all of it is *reentrancy during teardown*: destruction is not atomic with respect to script, so "the tree is consistent while we destroy the tree" is a load-bearing and frequently wrong invariant.

## Anatomy of a real one: CVE-2019-5782, Chrome's FileReader

A summarized public anatomy — mechanism per the public reconstructions of the bug; every line maps onto the three steps.

- **Where:** Blink's FileAPI, `FileReader`. Chrome up to 72.0.3626.119; fix shipped in 72.0.3626.121 (March 2019).
- **The free (the bug):** a `FileReader` is re-used for a second read while a reference to the first read's result buffer survives; the first result's backing allocation is freed out from under that reference — shape 4's reentrancy with an object-graph twist.
- **The reclaim (the spray):** script-driven allocations of the matching size, repeated until the freed slot holds attacker-controlled bytes. JavaScript is a *programmable allocator* with a loop — spray, trigger, check.
- **The confuse:** the stale reference now aliases script-controlled bytes; the exploit shapes them into a fake internal object and escalates to arbitrary read/write in the renderer.
- **The chain (impact):** renderer code execution is contained by the sandbox. The in-the-wild use (watering-hole activity reported by Volexity, 2019) paired it with CVE-2019-0808, a win32k bug on Windows 7, to escape — the "a modern exploit is several bugs long" story chapter 06 completes.
- **The fix:** lifetime soundness — the reference became ownership, so the free-under-a-live-handle cannot happen.

## Every defense is partial — the honest ledger

The full ladder is chapter 06; here is the UAF-specific ledger, each rung with the reason it is not the end.

**Quarantine (delayed reuse).** Park freed memory instead of recycling it, so a stale access hits bytes still marked "freed" rather than a fresh replacement. AddressSanitizer does this at detection scale: freed regions sit in a quarantine (defaults on 64-bit Linux, ASan runtime of the LLVM 17 era: 256 MB global, 1 MB thread-local) while the shadow memory keeps marking them `0xfd`, so the stale read *reports* instead of aliasing. Two partialities: it is detection, not prevention — shrink the quarantine and the exploit works again; and once a region exits quarantine and is reused, a *late* use-after-free is invisible ("passes ASan in CI, crashes in production"). 256 MB is also a QA budget, not a shipping one — production samples instead: GWP-ASan runs a small quarantine on a small fraction of users' processes (single-digit percent, Chrome's 2020s configuration), at most one report each.

**Pointer poisoning on free.** Overwrite freed bytes with garbage (ASan's optional free-fill writes `0xbe` into up to the first 4 KiB) or with fault-guaranteeing values (PartitionAlloc stores its *encoded* freelist link in the slot's first 8 bytes, so an immediate stale vtable call dereferences allocator bookkeeping, not zeros). Partial by construction: it defends only the *un-reclaimed* window — an exploit that reclaims the slot never reads the poison.

**Hardened allocator metadata.** PartitionAlloc is Chrome's structural answer: requests round up to bucket sizes (8- and 16-byte spacing in the smallest ranges, coarsening upward), slots live in slot spans inside 2 MiB super pages, freelist links are encoded and validated on pop to stay inside the slot span, and per-super-page metadata is out of band of user slots. Chapter 04's glibc playbook — poison the in-band `fd`/`bk` — mostly dies here. But it hardens the *allocator*, not the *lifetime*: a UAF whose replacement is a legitimate new object is untouched, because nothing was corrupted.

**Forward-edge CFI (vtable checks).** Clang's `-fsanitize=cfi-vcall` verifies, before an indirect virtual call, that the loaded vptr is in the bitset of vtables that exist in the compiled program. The fake-vtable route dies: sprayed heap bytes are not a registered vtable. Partial twice over: COOP ("Counterfeit Object-Oriented Programming," USENIX 2015) aims the stale call at *real* objects with *real* vtables, composing whole legitimate virtual functions as gadgets — it checks vtable provenance, not call semantics; and data-only UAFs make no indirect call, so vcall CFI never runs.

**BackupRefPtr / MiraclePtr (Chrome, rolling out since ~2022).** `raw_ptr<T>` increments a use-count piggybacking on the slot; a free while the count is nonzero crashes at the dangling dereference — in production, the first mitigation that catches UAFs in the field. Partial: the count is in-band (reachable by a malicious UAF write), and coverage requires migrating a multimillion-line codebase to `raw_ptr` — an audit-and-rewire project measured in years.

**Rust: the compile-time subset, eliminated.** Module 01 chapter 10's payoff. One owner per value; borrows carry lifetimes provably shorter than the owner's; the free happens only at owner drop. The stale-pointer program does not compile — `error[E0597]: does not live long enough` where C++ gives you a segfault. What survives, honestly: `unsafe` blocks opt out with raw pointers; FFI is invisible to the borrow checker — a C library that caches the `*mut T` you handed it will call back after the `Box` drops: same UAF, new language; and *logical* use-after-frees — bytes alive, resource closed — no memory violation, same incident.

Across the ledger: allocator- and control-flow-flavored mitigations narrow *determinism* or improve *detection*. Only ownership attacks the invariant itself.

## What insiders know

- **ASan's quarantine is the entire detection story; defaults are 256 MB global (`quarantine_size_mb`), 1 MB per-thread cache (`thread_local_quarantine_size_kb=1024`), 64-bit Linux, LLVM 17-era runtime.** Freed regions keep shadow `0xfd`, redzones `0xfa`; a UAF firing *after* quarantine expiry and reuse reads shadow `0x00` — addressable, invisible. `ASAN_OPTIONS=quarantine_size_mb=0` proves the mechanism — the report degrades to `heap-buffer-overflow`.
- **PartitionAlloc has three match conditions, not one.** The spray must hit the same *bucket* (size rounded up), the same *partition* (Chrome runs separate partitions — DOM objects and ArrayBuffer backing stores are not neighbors), and — because of PA's per-thread cache — ideally the *thread* whose cache owns the freed slot, or force a central-freelist flush.
- **The observer shapes have a canonical fix vocabulary.** "Notify while alive, then destroy" (two-phase teardown); snapshot the observer list before iterating; and Chromium's style rule that a `WeakPtrFactory` member is declared *last*, so weak handles invalidate before the members they guard destruct. A diff that moves a `WeakPtrFactory` up the member list is a latent UAF fix in reverse.
- **`base::Unretained(this)` in a posted task is a UAF with a timer on it.** The Chromium rule — `Unretained` only for synchronous, same-stack handoffs — exists because the post-to-run window is unbounded and script-triggered teardown fits inside it. Review flag, not a style nit.
- **Browser spray sizes are small and many, not big and few.** Reclaim needs bucket-exact objects: real-world sprays run 32–512-byte allocations by the tens of thousands (a few MB total). The megabyte-string sprays of the Flash era delivered *payloads*; the reclaim spray has a different job.
- **Vtable slot order is declaration order (Itanium ABI).** `ping` declared before the destructor sits in slot [0]; the destructor pair lands at the destructor's declaration position. Exploit-writing and vtable forensics (module 04) both hinge on which slot the hot virtual occupies.
- **glibc catches the double-free, never the late use.** tcache's `key` field (glibc 2.29+) aborts on the second free; nothing in glibc tracks dereferences of a handed-back chunk. Tcache's 7-per-bin LIFO — chapter 04's poisoning facts — are exactly why step 2 is *deterministic* here.
- **The interview trap: "we freed it, so it's gone."** The bytes are mapped, the chunk is healthy, the next same-size allocation gets them. A use-after-free is two legal users of one address, separated in time — no mainstream C/C++ allocator "knows" a pointer is stale.

## See it yourself

One file, three runs, all three steps — reproduced on Debian 13, g++ 14.2.0, glibc 2.41, x86-64. Save the 39-line `uaf.cpp` from "The three-step pattern" verbatim (the report's line numbers depend on it), then:

```bash
mkdir ch05uaf && cd ch05uaf
# paste uaf.cpp here
g++ -std=c++17 -g uaf.cpp -o uaf && ./uaf
```

Run one, no sanitizers — the exploit in miniature:

```text
victim slot = 0x55d9e7dca2b0, replacement = 0x55d9e7dca2b0
!!! stale call landed in attacker-chosen code
```

One address twice: the 24-byte `Evil` reclaimed the 16-byte `Session`'s 32-byte chunk, and `registry[0]->ping()` — load, load, indirect call — executed `attacker_fn`.

Run two, no spray — why spraying is not optional:

```bash
g++ -std=c++17 -g -DNO_SPRAY uaf.cpp -o uaf-nospray && ./uaf-nospray
# Segmentation fault (exit code 139)
```

The slot's first 8 bytes now hold glibc 2.41's tcache bookkeeping — a safe-linking-encoded `next` pointer — which the virtual call dereferences as a vptr. Crash, no control: the attacker's whole job is step 2.

Run three, under AddressSanitizer:

```bash
clang++ -std=c++17 -g -fsanitize=address uaf.cpp -o uaf-asan && ./uaf-asan
#   (clang was absent on the box that captured this output; g++ works identically:
g++    -std=c++17 -g -fsanitize=address uaf.cpp -o uaf-asan && ./uaf-asan
```

The report, trimmed to the frames that matter:

```text
==13113==ERROR: AddressSanitizer: heap-use-after-free on address 0x502000000010
READ of size 8 at 0x502000000010 thread T0
    #0 0x... in main uaf.cpp:38              <- THE ACCESS STACK: the stale vptr load
    ...
freed by thread T0 here:
    #0 ... operator delete(void*, unsigned long)
    #1 0x... in Session::~Session() uaf.cpp:6
    #2 0x... in main uaf.cpp:28              <- THE FREE STACK: `delete registry[0]`
    ...
previously allocated by thread T0 here:
    #0 ... operator new(unsigned long)
    #1 0x... in main uaf.cpp:26              <- THE ALLOCATION STACK
SUMMARY: AddressSanitizer: heap-use-after-free uaf.cpp:38 in main
Shadow bytes around the buggy address:
=>0x502000000000: fa fa[fd]fd fa fa fa fa ...
```

What to notice. The report *names the free stack and the access stack* — lines 28 and 38 of your source. That is the quarantine's debugging value: ASan parked the freed region and shadow-marked it (`fd` = freed, `fa` = redzone) long enough to catch the load instead of letting `Evil` reclaim it. The `READ of size 8` at offset 0 is the vptr load — the first instruction of the virtual dispatch, caught before it becomes an indirect call. Then prove the mechanism:

```bash
ASAN_OPTIONS=quarantine_size_mb=0 ./uaf-asan
# ==ERROR: AddressSanitizer: heap-buffer-overflow ... (no longer use-after-free)
```

Kill the quarantine and the classification degrades to plain out-of-bounds: the "freed" state exists only while the quarantine holds it — which is why production ships sampling (GWP-ASan) rather than 256 MB per process.

## How to use this knowledge

- **Reading CVEs.** "Use-after-free in X during Y" now parses into the anatomy: find the *free* (which event triggers Y), find who still holds the *stale* reference, and ask which rung the exploit had to beat — quarantine, CFI, or neither (data-only).
- **Code review.** The four shapes are review patterns: notify-while-iterating in a destructor, member order versus dependency direction, `Unretained`/`post` without cancellation, any callback fired during teardown. A diff adding a callback to a destructor's loop deserves the scrutiny of a diff adding `strcpy`.
- **Debugging production crashes.** A crash PC on `call *%rdx` a few loads from a heap pointer, or a crash "only on tab close," is a UAF fingerprint. Reproduce under ASan with a *large* quarantine (`ASAN_OPTIONS=quarantine_size_mb=1024`) for long-lifecycle bugs.
- **Design choices.** Make owns-observes explicit: weak handles invalidated at teardown, two-phase destruction, cancellation tokens for anything async. When crossing FFI from Rust, never hand out a `*mut T` that can outlive its owner without a contract.
- **Interview answers with derivations.** Why UAF dominates browsers (event lifetimes escape scope-RAII; mitigations attack determinism or detection, not the invariant); why CFI does not stop it (COOP, data-only); what Asan changes (the quarantine keeps the temporal window observable).

## Connections

- **Module 01, chapter 09 (C++/RAII):** the inversion studied here — scope-bound destructors are exact, but browsers put lifetimes on events, not braces; the four breeding shapes are the escape routes from scope.
- **Module 01, chapter 10 (Rust):** the payoff — the borrow checker as compile-time lifetime proof, and this chapter's ledger as the honest boundary of that proof (unsafe, FFI, logical UAF).
- **Module 01, chapters 05/06 (heap, object layout):** chunk arithmetic (16 + 8 → 32-byte minimum) and vptr-first layout are the machinery every step stands on.
- **Module 01, chapter 19 (allocators):** tcache bins and size classes — why reclaim is deterministic; PartitionAlloc's buckets and super pages as the alternative design.
- **Module 02, chapter 04 (heap exploitation):** same allocator, different attacker — tcache poisoning needs a pre-existing overflow; UAF needs no corruption at all, which is why it survived where the Houses fell.
- **Module 02, chapter 06 (the mitigation ladder):** CFI's cost and bypass ledger, MTE's tagging (freed memory retagged — a hardware quarantine at a 1/16 guess rate), and the composite stack that makes exploits five bugs long.
- **Module 03, chapter 02 (bug taxonomy, queued):** UAF's slot in the memory-corruption family tree, and how fuzzing plus sanitizers surface these bugs in practice.

## Quiz

1. The classic ladder — canaries, NX/DEP, ASLR — was built for spatial corruption. Name the invariant each defends, then explain in one sentence why a use-after-free violates *none* of them.

2. The lab runs on glibc 2.41, g++ 14.2, x86-64: `sizeof(Session)` is 16, `sizeof(Evil)` is 24. Compute the glibc chunk size for each request, name the tcache bin they share, and predict the relationship between the two addresses the program prints. Then predict what changes if `Evil` grows to 56 bytes of members.

3. In the `-DNO_SPRAY` run the program segfaults *at the virtual call*, not at the `delete`. What exactly do the first 8 bytes of the freed slot contain in glibc 2.41, and why does dereferencing them as a vptr fault?

4. Diagnose: this destructor runs, and the process crashes in the *second* loop iteration with a garbage vptr —

   ```cpp
   ~Tab() {
       for (Observer* o : observers_)    // observers_: std::vector<Observer*>
           o->onTabClosed(this);         // handler may unregister or destroy others
   }
   ```

   Explain the mechanism in terms of the chapter's three steps, and give the two canonical fixes.

5. Diagnose: a browser crash reproduces only when the user closes a tab while a background refresh task is in flight; the task was posted with `BindOnce(&Chart::refresh, Unretained(this))`. Walk the race window, name the breeding shape, and state what the posted task needed instead of `Unretained`.

6. Your fuzzing harness runs a 20-minute ASan soak and reports nothing; the same input crashes in production with a garbage vptr. Give the two quarantine-related reasons ASan can miss a late use-after-free, and one knob that improves the odds.

7. Cross-chapter, module 02 ch04: tcache poisoning and UAF reclaim both rely on the same two tcache properties. Name them, state in one sentence what the attacker controls in each — and which technique needs a pre-existing memory-corruption bug.

8. Cross-chapter, module 02 ch03/ch06: the stale virtual call is `mov (%rax),%rax; mov (%rax),%rdx; call *%rdx`. Which step does `-fsanitize=cfi-vcall` check, why does the fake-vtable exploit die — and in one sentence each, why do COOP and data-only use-after-free survive it?

<details>
<summary>Answers</summary>

1. **Canaries** guard the saved return address; **NX/DEP** guards which pages may execute; **ASLR** guards *address knowledge* for an attacker who already controls flow. A use-after-free breaks none: the free is well-formed, the chunk healthy, the stale access a normal read of a mapped address — the invariant broken is *temporal*. No canary differs, no page mis-executes, no flow is stolen.

2. Session: 16 + 8 header = 24, aligned to 16 → **32-byte chunk**; Evil: 24 + 8 = 32 → **32-byte chunk**. Same **32-byte tcache bin**; LIFO makes `new Evil` receive exactly the freed chunk — the program prints the **same address twice**. At 56 bytes: 56 + 8 = 64 → **64-byte chunk**, a different bin; the reclaim fails and the stale call reads tcache bookkeeping as a vptr. Size-class matching is step 2's load-bearing constraint.

3. glibc ≥ 2.32 stores the tcache `next` pointer in the freed chunk's first 8 bytes, **safe-linking encoded** as `(slot_address >> 12) ^ next` — with an empty bin, essentially `slot_address >> 12`, a smallish unmapped integer. The call dereferences it as a vptr → SIGSEGV: the `delete` completed legally, only the use is late.

4. Step 1 happens *inside* the loop: the first handler frees a sibling observer. `observers_[i]` for the next iteration is stale; any same-size allocation in between reclaims the slot (step 2); the next `o->onTabClosed(this)` is a virtual call through reclaimed memory (step 3). Fixes: **two-phase teardown** (notify while alive, then destroy) and **snapshot-then-iterate** (walk a copy, re-check membership).

5. The window is post-to-run: the queued task holds a raw, unowned `Chart*`. Script destroys the tab's graph inside the window — freed (1), reclaimed (2), `refresh` runs on the stale `this` (3). Shape 3: async cancellation without ownership. The task needed a weak handle validated before running, or ownership that keeps the object alive — a `WeakPtr`, never `Unretained` across an asynchronous boundary.

6. First, **quarantine expiry**: the region is parked for a bounded time (256 MB / 1 MB thread-local defaults); once it cycles out and is reused, shadow returns to addressable (`0x00`) and the late stale access reads a *valid* replacement — no report. Second, a very late UAF can alias a region freed and reused long before the stale pointer is touched, so the access reads the new owner's data as legitimate. Knob: `ASAN_OPTIONS=quarantine_size_mb=1024`; the lab showed `0` degrades the report to `heap-buffer-overflow` — the "freed" classification exists only while the quarantine holds it.

7. Both rely on **per-size-class bins** and **LIFO order**. In tcache *poisoning* the attacker controls the **free list's next pointer**, via a pre-existing overflow corrupting the in-band `next` — the allocator then returns an arbitrary address, so a corruption bug is required first. In UAF reclaim the attacker controls **the contents of the freed slot**, acquired by a legal allocation; no metadata is corrupted. That is why UAF survived where the Houses fell: nothing was ever broken.

8. `cfi-vcall` checks the **vptr between the two loads**: the value from `mov (%rax),%rax` must be in the program's bitset of real vtables before use as a table base. The fake-vtable exploit dies because a sprayed vtable pointer is not a registered vtable — the call aborts before `call *%rdx`. **COOP survives** by aiming the stale call at a *real* object with a *real* vtable — legitimate virtual functions become gadgets; CFI checks provenance, not semantics. **Data-only UAF survives** because it makes no indirect call — the stale write never reaches a vcall check.

</details>
