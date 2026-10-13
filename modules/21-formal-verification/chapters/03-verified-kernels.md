# Chapter 03 — The verified kernel

> **The 30-second version.** Between 2004 and 2009, a team at NICTA
> proved — machine-checked, in Isabelle/HOL — that the seL4
> microkernel's ~8,700 lines of C (plus ~600 of assembly) *always* behave
> exactly as its specification says. The proof took ~20-25 person-years,
> ~200k lines of script then (past ~400k now), found ~320 bugs before
> release, and, amortized across DARPA programs, turned the industry's
> most expensive bug class into a one-time line item. The fine print:
> the theorem says what the kernel *does*, not that it is *correct* —
> spec, compiler, the 600 asm lines, and hardware sit outside it.

## 1. The artifact: what was actually proven

The lineage: Liedtke's L4 family (1990s) — kernels small enough to audit,
fast enough to use; seL4 is NICTA's 2004-onward descendant with one twist:
prove the layers instead of only reviewing them, finished 2009 (Klein et
al., SOSP 2009 — functional correctness of a complete general-purpose
kernel, a first).

**Functional correctness** here is precise: the C implementation is proven
to *refine* an abstract specification — every behavior the C can exhibit is
a behavior the spec permits. No buffer overflow, no off-by-one, no
spec-violating state — not on tested paths, on *all* paths. The tower has
three layers joined by two theorems:

```
+--------------------------------------------------------------+
|  abstract specification    ~10k lines Isabelle, human-written |
|  "what the kernel should do"                                 |
+--------------------------------------------------------------+
            ^ THEOREM 1: refinement (forward simulation):
            |   every Haskell-model step is allowed by the spec
+--------------------------------------------------------------+
|  executable specification  Haskell model, runnable            |
+--------------------------------------------------------------+
            ^ THEOREM 2: refinement (forward simulation again):
            |   every C step is allowed by the Haskell model
+--------------------------------------------------------------+
|  C implementation   ~8,700 lines C + ~600 lines asm           |
+--------------------------------------------------------------+
```

The team ran the Haskell layer as a tested, executable prototype, then wrote
the C against it; the refinements compose — C ⊑ Haskell ⊑ abstract — and
their composition is the functional correctness theorem.

One component of theorem 2 earns every verification person's raised
brow: **the C parser.** Isabelle needs the C *as a term* — the
l4v tree includes a C-to-Isabelle parser that produces the syntax tree the
proof is actually about. The parser is trusted infrastructure, not a
verified component: mis-translate C, and the theorem is about a different
program than the one you compiled. This is the CompCert-style semantics
question in its seL4 form — *is the C semantics in the proof the same C
your compiler implements?* The seL4 resolution: a small, scrutinized
parser, a careful C subset, and the endgame — a verified compiler closing
that rung — is chapter 05's CompCert story.

## 2. The economics: proof as a line item, and the quiet bombshell

The field's economics as a table:

| line item | number (honest ranges) |
| --- | --- |
| kernel implementation | ~8,700 lines C + ~600 asm (2009; ~10k C today) |
| proof tree (l4v) | ~200k lines Isabelle in 2009; ~370k-400k+ today |
| proof effort | ~20 person-years of proof, ~25 total with design/code |
| cost | low single-digit US$ millions |
| proof:code ratio | the field's shorthand: ~100:1; raw arithmetic 400k / 9.3k ≈ 40:1 — honest range 40-100:1 depending on what you count. Measure it in the lab |
| the alternative | Heartbleed-class cleanup: post-incident estimates land at US$100M-scale *per event* — and the next CVE bills again |

The business case is the last row: proof is a **one-time cost**;
vulnerabilities are a **recurring cost**. A kernel proofed once in 2009 is
reused across projects — DARPA's HACMS the flagship — with no re-proof per
product: a theorem does not expire when you port the *system* built on it.
Heartbleed is the comparison the industry understands: one missing bounds
check in widely-audited C, and the world re-keyed itself. Against that,
"25 person-years, once" is cheap for the artifact class that matters.

The quiet bombshell, the number that decides whether verification is
philosophy or engineering: **the proof found ~160 implementation bugs and
~160 specification bugs, before release, in the most scrutinized
microkernel of its era.** The bugs the proof surfaced were the ones tests
do not reach: initialization-order bugs in boot code (run once per
machine, on the path every audit skims), rare interleavings in the IPC
fast path, arithmetic corner cases at message-size boundaries. The ~160
spec bugs are the other half of the lesson: writing down what you meant,
precisely enough to *prove*, found that you didn't mean what you wrote.
Verification was the team's best debugging tool — module 03's "the bug is
where the assumption is," mechanized: a machine that hunts assumptions
exhaustively.

## 3. What the theorem does NOT say — the honesty section, in full

A verification engineer who cannot recite this section is selling
something. Four rungs are outside the theorem:

**(1) The spec is not proven correct.** The theorem says the C matches the
spec; it says nothing about the spec matching what you *want* — "what does
a kernel want to do" is a human question. The abstract spec is ~10k lines
of human-reviewed Isabelle — the remaining gap: a spec bug means the theorem
faithfully proves the kernel does the wrong thing. Proof transfers trust; it
does not create it.

**(2) The compiler and the ~600 assembly lines escape.** The proof covers C
*source*; the binary is what runs. A miscompiling gcc produces a correct
proof about the wrong artifact. And ~600 lines of hand-written assembly —
kernel entry/exit, context switch, some fast paths — sit outside the proof
entirely, human-reviewed only. The compiler rung's endgame is CompCert
(chapter 05); the asm rung is kept small enough to audit by reading — why
seL4 held it at ~600 lines.

Draw the full stack with the proof covering the middle — keep this
diagram:

```
+----------------------------------------------------------+
| middleware / apps        UNPROVEN (by design)            |
+----------------------------------------------------------+
| abstract spec            HUMAN-REVIEWED (~10k lines)     | <- the gap that remains
+==========================================================+
|          >>> THE THEOREM COVERS THIS MIDDLE <<<          |
|  C kernel: every behavior refines the abstract spec      |
|  ~8,700 lines, machine-checked in Isabelle/HOL           |
+==========================================================+
| ~600 lines asm           UNPROVEN (hand-written)         |
+----------------------------------------------------------+
| compiler (gcc/clang)     ASSUMED (CompCert: ch05)        |
+----------------------------------------------------------+
| hardware                 ASSUMED FAULT-FREE              |
+----------------------------------------------------------+
```

Module 19 chapter 05's "unsafe kernel with a safe membrane" shape returns
at OS scale — a thin unproven boundary around a proven core, the membrane
this time made of a theorem.

**(3) Timing is out of scope.** Functional correctness has no clock: no
worst-case execution time (WCET), no latency or jitter bounds. For the
real-time world (module 20) that is a separate discipline: static WCET
analysis on the binary, plus seL4's *time protection* (the MCS
mixed-criticality variant) bounding interference so one compartment cannot
leak timing to another. The functional theorem and the timing story are two
artifacts; only the first is celebrated.

**(4) The hardware is assumed.** No Rowhammer (module 02: flipping DRAM bits
from a co-tenant), no fault injection, no Bellcore-style attack (module 18
ch03). From 2019 on the seL4 community has published on glitch attacks —
voltage and clock glitching to make a proven kernel skip an instruction —
and their honest conclusion: the answer is *not more proof*; it is
glitch-detection hardware, redundancy, and re-drawing the trust boundary
below the silicon. A verified kernel on a glitchable chip is a proof about
a machine that no longer exists.

## 4. Inside the proof: the invariant, the capability, and the commute

Two proof styles hold the tower up.

**Invariants** hold of *every reachable state*. The crown jewel of seL4's
invariant work is its page-table theorem: **every frame mapped into an
address space is backed by a live capability.** No sequence of syscalls —
grant, revoke, map, CSpace surgery — can produce a state where a page table
points at a frame whose owner no longer holds the right to it. Untyped
memory cannot be aliased into two address spaces by construction; the
invariant *is* the proof's memory-safety story (the `Inv` theories run to
hundreds of pages in l4v).

The identification this program has been building toward since module 19
chapter 03: **a capability system is a runtime type system for memory.**
A capability is a proof object in kernel state — an unforgeable token that
*is* the right to touch a specific object. Grant is subtyping, revoke is
going out of scope, and the affine discipline (a cap moved to a child is
gone from the parent — caps are linear, not copyable) is module 19
chapter 03's affine types, enforced at runtime instead of compile time.
The kernel's entire state space is type-correct by theorem. The
identification continues into silicon: CHERI (Cambridge's CHERI/CTSRD
lineage, out of Capsicum's sandboxing work) implements capabilities *in
the ISA* — tagged, bounded, 128-bit pointers that make out-of-bounds
access a hardware fault. CHERI is seL4's runtime type system pushed into
the instruction set: the software proof and the hardware mechanism are
the same idea at different layers.

**Refinement** is the tower's mortar, proved by *forward simulation*: a
correspondence relation `R` between concrete and abstract states, with the
obligation that each concrete step is matched by an abstract one:

```
                    R  (correspondence relation)
        c  ────────────────────────────────▶  a      c: concrete state
        │                                      │     a: abstract state
        │ one C step                          │ ∃ abstract step
        ▼                                      ▼     (stuttering allowed)
        c' ─────────────────────────────────▶  a'

  obligation:  R(c,a) ∧ (c → c')  ⟹  ∃ a'. (a → a') ∧ R(c', a')
```

The diagram commutes in the "there exists" reading: the concrete machine may
take small steps — or *stutter*, leaving the abstract state unchanged while
the C grinds through one operation — but it can never step somewhere the
abstract machine cannot follow; that is why the diagram *is* the proof's
shape. Invariant proofs say "all reachable states are well-formed";
simulation proofs say "all behavior is accounted for." l4v uses both, and
its directory names map onto the diagram (the lab walks it).

## 5. The second act: drones, certificates, and the monolith's answer

**The HACMS drone.** DARPA's HACMS (2011-2015, a program measured in the
tens of millions of dollars) was verification's amortization proof: Boeing
helicopter and drone platforms retrofitted with an seL4-based separation
architecture — the *verified* kernel at the bottom, *unverified* middleware
and mission software in compartments above. Isolation is done by
compartmentalization: each component holds only the capabilities it was
granted, and the kernel is *proven* to enforce that. Red-team exercises
planted malware in mission-app compartments; it could not reach flight
control — not because the middleware was proven (it was not), but because
the compartment boundaries and capability distribution were. Prove the
small bottom once; let containment (module 15, now with teeth) handle the
unprovable top.

**Certification is not proof.** The honest paragraph: DO-178C DAL A —
avionics's highest assurance level — certifies *process*: reviews, MC/DC
structural coverage, traceability, independent verification — evidence about
how you worked, machine-checkable nowhere. A theorem is evidence about what
the artifact *is*, checkable by anyone holding the proof and the checker.
Marketing blurs them: Green Hills' INTEGRITY-178B,
certified to DAL A and flown for two decades, is a *certified but not
formally verified* kernel, and its advertising has let readers hear
"proven." In 2023 the seL4 Foundation ran seL4 through a Common Criteria
evaluation to EAL6+ — the first general-purpose kernel at that level — and
the honest reading: the *theorem made the paperwork cheap* (EAL6+ demands
formal design artifacts; seL4 had a formal *implementation*), while the
certificate still adds what a proof cannot: independent eyes on the target
and delivery chain.

The comparison table the field deserves:

| system | the claim | what backs it | what it is *not* |
| --- | --- | --- | --- |
| seL4 | functional correctness | Isabelle refinement proof, ~400k-line l4v tree, 20+ py | spec, compiler, asm, hardware all outside |
| CertiKOS (Yale) | verified concurrent kernel | Coq, *compositional* certified abstraction layers — prove layers, stack them | trade: composability and re-proof per configuration vs seL4's monolithic one-shot; smaller API |
| INTEGRITY-178B | certified | DO-178C DAL A, CC evaluations — process evidence, tests, reviews | no machine-checked theorem anywhere |
| Linux | none claimed | engineering at the largest scale | (see below) |

**The Linux side — the field escaped the lab.** The two camps are
"microkernel-with-proof" and "monolith-with-knowledge," but the monolith
quietly built the most-used formally specified artifact in existence: the
Linux kernel memory model (LKMM), written in the Cat language,
machine-checked with herd7 against litmus tests (Alglave, Maranget,
McKenney, Parri, Stern; formalized 2017; lives in `tools/memory-model`).
Maintainers run litmus tests — "may this reordering happen?" — against a
formally defined concurrency model, daily, in the normal flow of kernel
development. Nobody proved Linux's C; what happened is the kernel's
*concurrency specification* became a machine-checked artifact, with the
data-race-freedom arguments for core synchronization (RCU, locking
discipline) made against it. The monolith uses a verified-model workflow
as ordinary tooling while the proven kernel flies in drones — both camps
won something.

**SPARK: the subset discipline, and component-level economics.** The Ada
world took the other door: restrict the language until proof is cheap. SPARK
is Ada's provable subset — classically no pointers, no aliasing (modern
SPARK adds Rust-like ownership-checked pointers) — with flow analysis and
proof obligations run through SMT solvers on every build. The flight-software
pattern (NASA cubesat guidance work, JPL IRAD-style research among the cited
cases): prove the guidance component, ~10-100x proof effort at component
scale — one to two orders cheaper than a kernel. The sizing rule: *proof
scales with the size of the thing you refuse to get wrong.*

## What insiders know

- **Proofs are code, so they get CI, pinning, and review.** The l4v tree
  regenerates in the release pipeline; the exact Isabelle/Pure version is
  pinned (proofs depend on library internals — simp sets, tactic behavior —
  that change between releases). A proof diff is a *semantic* diff in the
  theorem: shrink an `Inv` theory by 300 lines and you may have weakened an
  invariant — proof diffs get release-notes discipline, not rubber stamps.
- **The repo is the textbook.** In `seL4/l4v`, the directory names *are*
  the commuting diagram: `ASpec` (abstract), `ExecSpec` (Haskell model),
  `CSpec` (parser output), `CRefine` (C-level refinement); invariants live
  in the `Inv` theories. The largest formally verified artifact in
  existence, browsable like documentation.
- **Career honesty.** The "verification engineer" market is small — hundreds
  of jobs worldwide, not thousands: AdaCore, Galois, AWS Automated Reasoning,
  the seL4 ecosystem. The transferable skill is *specification writing* —
  deciding what to promise — the senior skill in any language.
- **The Rust-for-verification debate, placed.** "Rust gives memory safety
  free" vs "the borrow checker proves a weaker theorem than functional
  correctness" — both right; the answer is composition: unverified Rust
  *confined by* verified seL4 (the Rust-on-seL4 line), verified Rust where
  it counts (Verus), CakeML as the other verified-language line. Type
  systems and theorems are layers, not rivals.
- **The bugs-found number is the sales number.** Insiders quote ~160 + ~160
  to executives, not the 400k line count — "proving found real bugs in the
  most-reviewed kernel alive" is the sentence that lands.

## See it yourself

All three labs are free — no Isabelle, no build.

**Lab 1: the repo walk — measure the ratio yourself.**

```console
$ git clone --depth=1 https://github.com/seL4/l4v.git   # the proof tree
$ git clone --depth=1 https://github.com/seL4/seL4.git  # the kernel
$ ls l4v
ASpec/  CRefine/  CSpec/  ExecSpec/  lib/  proof/  ...
$ git -C l4v ls-files '*.thy' | xargs wc -l | tail -n1
... total            # proof lines: 300k-600k
$ git -C seL4 ls-files '*.c' | xargs wc -l | tail -n1
... total            # kernel C: ~10k
$ git -C seL4 ls-files '*.S' | xargs wc -l | tail -n1
... total            # asm: ~600-1k
$ wc -l l4v/CRefine/*.thy | tail -n1
... total            # just the C-level refinement proof
```

Notice: (1) the directory names map to this chapter's diagrams; (2) divide
the first count by the second — *your* proof:code ratio, measured, not
quoted; (3) the asm count is small *on purpose* — the unproven residue,
budgeted.

**Lab 2: read one page of the spec.** Open `l4v/ASpec/KernelState.thy`
(or the session PDF on the project page). The abstract state is one large
record, glossed here:

```isabelle
(* glossed: the kernel's whole state as one typed record *)
record state =
  kheap :: "obj_ref ⇒ kernel_object option"   (* memory as a typed map *)
  (* ...plus the capability-derivation tree, scheduling state, etc. *)
```

Notice what `kheap`'s type says: memory is not bytes, it is a *typed* total
map — the spec reads like the world's strictest type declaration, and it is
~10k such lines, all human-reviewed: rung 2 of the TCB stack, now something
you have seen.

**Lab 3 (paper): a 3-object capability system with a checker.** The crown
invariant — every mapped frame has a live capability — shrinks to a toy
that runs anywhere:

```python
# captoy.py -- 3 objects, one invariant: "every mapped frame has a live capability."
objects = {i: {"frame": f, "mapped": False} for i, f in enumerate(["fA", "fB", "fC"])}
caps = {}                                    # cap id -> (object id, rights)

def grant(oid, rights):                      # mint: only authority creates caps
    cid = max(caps, default=0) + 1
    caps[cid] = (oid, rights)
    return cid

def transfer(cid, new_rights):               # delegate: MOVE + weaken, never amplify
    oid, rights = caps.pop(cid)              # old cap dies -- the affine discipline
    assert set(new_rights) <= set(rights), "cannot amplify rights"
    return grant(oid, new_rights)

def map_frame(cid):                          # kernel op: mapping needs the right
    oid, rights = caps[cid]
    assert "M" in rights, "no mapping right"
    objects[oid]["mapped"] = True

def destroy(cid):                            # the op a buggy kernel would allow
    caps.pop(cid)

def invariant():                             # the theorem's body, as a checker
    for oid, o in objects.items():
        assert not o["mapped"] or any(c[0] == oid for c in caps.values()), \
            f"object {oid} is mapped but NO capability backs it"

c1 = grant(0, "RMW")
map_frame(c1)                                # legal: a live cap with M
invariant(); print("1: invariant holds after a legal map")
c2 = transfer(c1, "R")                       # c1 dies in the move; map stays
invariant(); print("2: still holds (c2 backs the map)")
destroy(c2)                                  # last cap on object 0 is gone
try:
    invariant()
    print("3: holds -- correct kernel")
except AssertionError as e:
    print("3: CAUGHT:", e, "-- the state seL4's proof says is unreachable")
```

Expected output: 1 and 2 print "holds"; 3 prints the CAUGHT line — object 0
mapped with no capability backing it. The checker plays the invariant proof;
`destroy` plays a kernel bug. `transfer` is module 19 chapter 03's affine
discipline, live: try `transfer(c2, "RM")` on a read-only cap and watch the
no-amplification rule fire.

## How to use this knowledge

- **Vendor-claim triage.** When a vendor says "verified," ask which rung:
  a theorem (seL4), a certificate (INTEGRITY-178B), a model (LKMM), or
  testing with "rigorous" in front of it.
- **TCB accounting.** Evaluate any seL4-based design by enumerating the
  stack — proven kernel, reviewed spec, unproven asm, assumed compiler and
  hardware, and everything *above* the kernel — before believing an
  isolation claim.
- **Debugging a verified system.** If a bug survives a proof, walk the
  unproven rungs *in order*: capability distribution and configuration
  first, then asm, compiler, hardware.
- **Sizing proofs.** Component: 10-100x with SPARK-class tooling in CI.
  Kernel-scale: 40-100:1 and 20+ person-years. Prove the guidance code,
  the crypto primitive, the parser — not the whole app.
- **The interview answer.** "seL4: ~10k lines, ~400k lines of proof, 25
  person-years — and it still needed the spec reviewed, the compiler
  assumed, and the hardware glitch-hardened." Reciting the *limits* is
  what sounds like having read the paper.

## Connections

- **Module 21 ch01** — the LCF trust kernel: the Isabelle checker beneath
  this proof, and why 400k proof lines can be trusted.
- **Module 21 ch04** — the borrow checker as a *decidable fragment*:
  seL4's capabilities are the full-strength runtime version of Rust's
  compile-time affine types.
- **Module 21 ch05** — CompCert closes this stack's compiler rung; the
  parser question gets its endgame.
- **Module 19 ch03** — affine types as compile-time discipline; here they
  return as *kernel state*: capabilities are a runtime type system for
  memory.
- **Module 19 ch05** — the "unsafe kernel with a safe membrane" shape,
  returning at OS scale with the membrane made of a theorem.
- **Module 07 ch04** — Linux's herd7-verified memory model: the monolith's
  answer, where the *concurrency spec* is the machine-checked artifact.
- **Module 05 ch01** — mobile kernels: the L4 family's commercial phone
descendants — the proof-adjacent production world.
- **Module 02 / module 18 ch03** — Rowhammer and the Bellcore fault attack:
  the hardware rung no proof covers.
- **Module 20** — WCET and real-time: the timing proofs seL4's MCS variant
  serves.

## Quiz

1. **Concept.** seL4's headline result is "functional correctness." State
   precisely what the theorem asserts (the two refinement steps), and why
   it is *not* the claim "seL4 is correct."

2. **Concept.** The seL4 C code came from a tested Haskell prototype and
   heavy review — yet the proof found ~160 implementation bugs. What
   property of proof versus testing reaches them? Name two bug shapes
   tests structurally cannot reach.

3. **Predict the number.** Compute: (a) proof lines per line of kernel
   C+asm, using the 2009 figures (~200k proof lines vs ~9,300 C+asm);
   (b) person-days of proof per line of C (~25 person-years, 250 days per
   person-year). Then say in one sentence what the field's "~100:1"
   headline counts.

4. **Predict the output.** In the chapter's toy capability system, object
   0 is mapped and the only cap on it has rights `"R"`. (a) You call
   `map_frame` on that cap — what fires? (b) You call
   `transfer(cap, "RM")` — what fires? (c) After rights are weakened to
   `"R"`, can any sequence of `transfer` calls restore `"M"`? Why?

5. **Predict the behavior.** The Haskell model's "reply to IPC" is one
   atomic step; the C implements it as three steps (consume the reply cap,
   write the message, update scheduling state). What must the
   forward-simulation proof exhibit for each concrete step, and what may
   the abstract state do meanwhile?

6. **Diagnose.** A drone runs seL4: flight control in one compartment,
   mission app in another. Red-team malware lands in the mission app.
   The flight-control compartment's memory was never touched — the
   kernel's theorem is intact — yet the drone deviates from its mission
   plan. Where did the isolation argument break, and which layer fixes
   it?

7. **Diagnose.** Your team upgrades Isabelle on a whim; the l4v tree stops
   building with errors inside proof-library theories you never touched.
   No kernel C changed. What broke, and what maintenance discipline
   prevents it?

8. **Cross-chapter.** You deploy seL4 on a COTS Arm board, compiled with
   stock gcc, with an unverified Rust driver in the initial root task.
   Enumerate this system's complete TCB as a stack, marking each rung
   *proven / human-reviewed / assumed*, and name the module 19 chapter
   whose "unsafe kernel, safe membrane" shape this stack instantiates.

<details>
<summary>Answers</summary>

1. The theorem composes two refinements: (theorem 1) every step of the
   Haskell model is allowed by the abstract specification, and (theorem 2)
   every step of the C is allowed by the Haskell model — so every C behavior
   is a behavior the abstract spec permits. It is not "seL4 is correct":
   the spec itself is only human-reviewed (~10k lines, rung 2 of the TCB
   stack), and the compiler, the ~600 asm lines, and the hardware are
   assumed. The theorem *transfers* trust to the spec; it does not create
   it.

2. Proof is universal over paths; tests sample them. The proof checks spec
   against code on *every* input and interleaving, so it finds bugs no
   reachable test covers: initialization-order bugs in boot code (run once
   per machine, the path audits skim) and rare interleavings or arithmetic
   corner cases in the IPC fast path. The ~160 spec bugs are the other
   half: writing intent precisely enough to prove exposed that the intent
   itself was wrong.

3. (a) 200,000 / 9,300 ≈ **21:1**. (b) 25 × 250 = 6,250 person-days over
   ~8,700 C lines ≈ **0.72 person-days per line**. (c) The ~100:1 headline
   counts the full current proof-side tree — scripts *plus* specs and
   generated artifacts (~400k+) — against the handwritten kernel; the
   honest range is 40-100:1, which is why the lab has you measure rather
   than quote.

4. (a) The `assert "M" in rights` fires: "no mapping right" — the checker
   rejects the operation. (b) The `assert set(new_rights) <= set(rights)`
   fires: "cannot amplify rights." (c) Never by transfer: transfer is
   move-plus-weaken over a monotonically shrinking right set — caps are
   affine (the old cap dies) and rights only decrease. Restoring M requires
   *authority* to mint a fresh cap via `grant` — why seL4's mint
   operation is the critical one to restrict.

5. For each concrete step the proof exhibits an abstract step — either a
   *stuttering* step (abstract state unchanged, R preserved) or the one
   real "reply to IPC" transition — after which R holds again:
   R(c,a) ∧ (c → c') implies ∃a'. (a → a') ∧ R(c',a'). The abstract state
   may *stutter* while the C grinds; only when the
   operation completes must a real abstract transition exist.

6. The theorem covers the kernel, not the system. The malware stayed in its
   compartment — the proof held — but compartmentalization isolates only
   what the capability distribution denies. The break is above the kernel:
   unverified middleware used *legitimate* IPC channels it was granted (a
   confused deputy), or the boot configuration minted caps too broadly —
   the kernel cannot save you from a right you handed out. The fix is the
   system layer: re-draw the capability distribution and inter-compartment
   protocol — above a proven bottom.

7. Proof bit-rot: l4v proofs depend on Isabelle *library internals* —
   definitions, simplifier rule sets, tactic behavior — that change between
   Isabelle/Pure releases, so an unpinned upgrade breaks theories unrelated
   to your change. Discipline: pin the exact Isabelle version (the l4v repo
   states which), let proof-CI regenerate the full tree on the pinned
   toolchain, and treat a proof diff as a semantic diff in the theorem —
   reviewed and recorded in release notes.

8. The stack, top to bottom: (1) the Rust driver / root task — *unproven*,
   its memory safety is the Rust type system plus review; (2) the seL4 C
   kernel (~8,700-10k lines) — *proven*: every behavior refines the abstract
   spec; (3) the abstract specification — *human-reviewed*, ~10k lines, the
   remaining gap; (4) the ~600 hand-written assembly lines — *unproven*,
   audited by reading; (5) stock gcc — *assumed correct* (CompCert's
   endgame, module 21 ch05); (6) the COTS Arm board — *assumed fault-free*
   (Rowhammer and glitch attacks, module 02). This is
   module 19 chapter 05's shape — a small unsafe/assumed kernel region
   wrapped by a safe membrane — except the membrane is a machine-checked
   theorem, and the capabilities enforcing it (module 19 ch03's affine types
   as kernel state) are the mechanism.

</details>
