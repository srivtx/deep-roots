# Module 21 final exam — formal verification

Twelve questions in five parts, one part per chapter: three each on
chapters 01 and 02, two each on chapters 03, 04, and 05. Closed book;
every answer is worked, not stated — the derivations are the point.
Scoring: 11+ = you own this module; 8-10 = solid, revisit the flagged
chapters; <8 = the chapter quizzes are the study path.

**Part I — what a proof is (chapter 01)**

1. **Derive.** The loop below must exit with p = 2^n:

   ```
   i := n;  p := 1;
   while i > 0 do
       p := p * 2;
       i := i - 1
   od
   ```

   Supply the invariant in the sum-loop's shape (one arithmetic fact
   tying p to i and n, one range fact), then discharge all three
   obligations — initiation, consecution (naming the recurrence that
   is load-bearing), termination (name the variant, show it strictly
   decreases) — and state exactly what the while rule hands back at
   exit, and nothing more.

2. **Diagnose.** Two incidents at a proof-assistant vendor, one week
   apart. (a) A 50,000-line tactic library is found to derive `False`
   on pathological goals; thousands of customers proved theorems with
   it. Must any theorem the kernel already *accepted* be re-proved?
   (b) A kernel bug — the guard checker admits a false fixpoint (the
   Coq 2018-2021 class) — is found and fixed in days. What must
   happen to the proofs that used the buggy rule, and what property
   of the stored artifact makes the repair cheap?

3. **Predict.** A 4,000-line C component climbs the specification
   ladder: types, then defensive pre/post contracts, then a full
   functional spec with loop invariants, then machine-checked proof.
   Predict the line count at each rung, say where Dafny's binary
   search sits (three lines of logic, ten of contract), and give the
   one-sentence reason the top rung's multiplier is seL4's economics
   in miniature.

**Part II — model checking and the explosion (chapter 02)**

4. **Predict.** A distributed-lock spec: 4 nodes with 10 local states
   each, one 24-bit ballot counter, 9 Boolean flags. (a) The raw
   state product. (b) The visited-set at 32 B per state, compared
   with a 64 GiB laptop and the 12-16 TiB 8-socket server ceiling.
   (c) A teammate offers "compress to 8 B per state and buy a 2 TiB
   machine" — does the frontier move, and what would actually move
   it? (d) At 10^6 states/s, how long is the sweep even if the RAM
   were free?

5. **Predict.** The chapter's BDD shape, f = z ∧ (x1 ∨ x2 ∨ x3 ∨ x4),
   variable order x1 x2 x3 x4 z. (a) Truth-table rows. (b) BDD nodes
   — derive the count. (c) The compression ratio, and both numbers
   at n = 20 OR-variables. (d) Two-bit equality (x1 ≡ y1) ∧ (x2 ≡
   y2): why does the interleaved order x1 y1 x2 y2 stay linear while
   the blocked order x1 x2 y1 y2 blows up — what does interleaving
   let the DAG share, and what is the blocked order forced to
   remember?

6. **Diagnose.** QA blocks a SAT-based check: "2,000 variables, 8,600
   clauses — that's α ≈ 4.3, right at the 4.26 knife edge. It'll
   never finish." The solve takes 40 milliseconds. Name the two
   errors, say what α ≈ 4.26 is a statement *about*, what CDCL eats
   that the random model lacks, and the cheap observation you make
   before blaming density next time.

**Part III — the verified kernel (chapter 03)**

7. **Diagnose.** A datasheet reads: "INTEGRITY-178B — the *verified*
   kernel, certified DO-178C DAL A, flying for two decades." Two
   claims are being blurred; separate them. What does DAL A certify
   (name its evidence kinds) versus what is a theorem like seL4's
   (checkable by whom, holding what)? Then place seL4's 2023 EAL6+
   evaluation honestly: what did the theorem make cheap, and what
   does the certificate still add that no proof can?

8. **Concept (cross-chapter).** Nobody ever proved Linux's C, yet
   the monolith camp runs the most-used formally specified artifact
   in existence. Name it, its language, and its checking tool; say
   what maintainers do with it daily and what is actually
   machine-checked; and close the two-camps argument — what did
   each side win?

**Part IV — the borrow checker's fragment (chapter 04)**

9. **Diagnose.** `size_of::<Option<&u32>>() == 8`. Separately, an
   unsafe FFI shim writes the byte 0x02 into a field that safe code
   later reads as a `bool`; the release build misbehaves, debug
   works. (a) Derive the 8 bytes — the niche argument, and why it is
   sound. (b) Split the guarantees: what the type system proves
   versus what unsafe code must maintain; give the validity facts
   for `bool`, references, and `char`. (c) Where does the UB begin —
   the write or the read — and why did debug survive? (d) The honest
   type for uninitialized memory, and the name of the whole
   distinction.

10. **Diagnose.** The async state machine — the fragment's hardest
    customer:

    ```rust
    async fn read_frame(socket: &mut Stream) -> Frame {
        let mut buf = [0u8; 1024];
        let n = socket.read(&mut buf).await;
        let view = &buf[..n];            // borrows buf
        let frame = parse(view).await;   // view live across an await
        frame
    }
    ```

    (a) What does the async transform do to locals live across
    `.await`? (b) Why do `buf` and `view` as two fields make the
    generated Future self-referential, and what does moving it
    dangle (module 01 ch04)? (c) The Tofte-Talpin assumption that
    suspension breaks — mid-function. (d) The fragment's way out:
    `Pin` as what kind of proposition, with its safe derivation and
    its admit hole. (e) Why can the fragment not bless the
    self-reference directly?

**Part V — verified compilers and crypto (chapter 05)**

11. **Enumerate.** A HACMS-style drone: unverified mission software
    confined to one compartment, flight control in another; the seL4
    kernel beneath them, built with CompCert; the ELF shipped from a
    reproducible build onto a COTS Arm board. Draw the complete TCB
    top to bottom, marking every rung proven / human-reviewed /
    tested / assumed — including CompCert's own nested trust base —
    and name the module 19 chapter whose shape this stack
    instantiates.

12. **Predict.** The economics of paying. (a) seL4 cost ~25
    person-years, once: what does the theorem cost per year at 15
    years of reuse? (b) One Heartbleed-class incident runs ~$100M;
    at $200k per fully loaded person-year, how many person-years is
    that — how many seL4 proofs? (c) Score the four-condition
    checklist (safety-critical, high-assurance, long-lived, reused;
    at least three of four pays) for: your team's CRUD endpoint, an
    X25519 library shipping in a browser, a one-off migration
    script. (d) One sentence: why does "verification is the
    expensive option" invert the accounting?

<details>
<summary>Answers</summary>

**Part I**

1. **I ≡ p = 2^(n−i) ∧ 0 ≤ i ≤ n.** Initiation: `i := n, p := 1`
   gives p = 1 = 2^0 = 2^(n−n), and 0 ≤ n ≤ n (n ≥ 0 is the caller's
   obligation). Consecution, under the guard i > 0: before the body
   p = 2^(n−i); after `p := p * 2` it is 2^(n−i+1); after `i := i − 1`
   the new exponent n − (i−1) = n−i+1 matches — the load-bearing
   recurrence is **2^(k+1) = 2·2^k**, exponentiation's recursive
   definition, playing the role Σ's `Σ_{k<i+1} = Σ_{k<i} + a[i]`
   played in the sum loop. The range survives: i > 0 gives i−1 ≥ 0,
   and i−1 ≤ i ≤ n. Termination: variant **v = i** — the guard
   supplies v ≥ 1, each iteration decrements it by 1, ℕ is
   well-founded. Exit: the while rule hands back your own invariant
   plus the negated guard — I ∧ ¬(i > 0) = `p = 2^n ∧ i = 0`, and
   nothing else: facts absent from I cannot appear in the conclusion
   (ch01's rule; the weak-invariant lesson in reverse).

2. (a) **Nothing — the tactic layer is untrusted by design.** LCF's
   move: `thm` is an abstract datatype with no public constructor;
   only the kernel's rules return one. Tactics *propose* terms, the
   kernel disposes — a tactic that mis-derives `False` on
   pathological goals wastes CPU there and nothing else; it cannot
   forge a theorem. Every theorem the kernel accepted was
   term-checked at `Qed`, so it stands. Trust sits in the checker,
   never the search. (b) The kernel is the TCB as a line count
   (Isabelle ~2,500, Coq ~30,000): a guard-checker escape is an
   implementation bug in a rule — the Coq 2018-2021 class. Proofs
   whose terms pass through the buggy rule must be re-checked, and
   the repair is cheap because the stored artifact is the **proof
   term** — the λ-term, Curry-Howard; the tactic script was already
   discarded at `Qed`. Fix the kernel, re-run the checker:
   `coqchk` re-typechecks a whole development without loading any
   tactic code. The 2000-2020 kernel-bug list is ~a handful
   precisely because the surface is that small — and once fixed,
   every old proof re-checks from its stored term (ch01's de Bruijn
   criterion: proofs as files, checkable independently of the
   prover).

3. Types at ~0.1-0.3×: **400-1,200 lines.** Defensive contracts at
   ~0.5-1×: **2,000-4,000.** Full functional spec with loop
   invariants at 1-5×: **4,000-20,000.** Machine-checked proof at
   10-40×: **40,000-160,000 lines.** Binary search's ten contract
   lines over three of logic ≈ 3.3× — the full-spec rung (1-5×);
   "the spec is 3× longer than the program" is normal, the chapter's
   own count. The miniature: 10-40× is seL4's raw 400k proof lines
   over ~9.3k C+asm ≈ **40:1** (the ~100:1 headline counts the grown
   tree with specs and generated artifacts) — the spec is the floor,
   the proof is the mass, and that ratio is why you prove the
   guidance component or the crypto primitive, not the whole app
   (ch03's sizing rule, previewed).

**Part II**

4. (a) 10^4 × 2^24 × 2^9 = 10^4 × 2^33 ≈ 2^46.3 ≈ **8.6 × 10^13
   states** (10^4 ≈ 2^13.3). (b) × 32 B = 2^51.3 B ≈ **2.4 PiB** —
   a ~46-boolean problem: between the chapter's 40-boolean 32 TiB
   "never" and 50-boolean 32 PiB, about 40,000× over the 64 GiB
   laptop and ~200× over the 12-16 TiB 8-socket ceiling. No machine
   on earth. (c) 8 B per state buys exactly **one bit** — 2.75 ×
   10^15 → 1.4 × 10^15 B, still ~50× past the ceiling; the 2 TiB
   box moves nothing. The constant is negotiable, the exponent is
   not: shaving bytes-per-state cannot help — the exponent eats any
   constant. What moves the frontier is removing *variables*:
   declare the 4 nodes symmetric (divides the space by ~4! ≈ 24,
   buys 4.6 bits), and bound the ballot counter to the values the
   property actually reads (24 bits → 3 bits buys 21 bits, a factor
   of two million). Both re-run the multiplication; neither touches
   the property. (d) 8.6 × 10^13 ÷ 10^6 states/s ≈ 8.6 × 10^7 s ≈
   **2.7 years** — patience kills the sweep even where RAM cannot
   (ch02's spine: states × bytes-per-state versus bytes-of-RAM, then
   time).

5. (a) 5 variables → 2^5 = **32 rows**. (b) The OR chain: x1's
   1-edge is the T terminal and its 0-edge x2; …; x4's 0-edge is
   the all-false path, which still must test z — 4 tests + 1
   z-test + 2 terminals = **7 nodes**. (c) 32/7 ≈ **4.6×**; at
   n = 20: **23 nodes against 2,097,152 rows** (~91,000×) — the
   chapter's generalization, n + 3 nodes versus 2^(n+1) rows.
   (d) Interleaved, each (xi, yi) pair resolves before the next is
   tested, so every path that keeps "equal so far" reaches the
   *same* suffix sub-DAG — the partial state is one bit of summary,
   3n + 2 nodes (8 at n = 2). Blocked, the x-tests run first and the
   residual after k x-tests is one of 2^k distinct functions of the
   y's — the DAG must remember the whole x-pattern: 10 nodes at
   n = 2, ~n·2^n at n bits. The same shape is the ripple-carry
   adder's linear-vs-exponential carries (interleave a0 b0 a1 b1…),
   and the multiplier is the honest limit — 2^Ω(n) under *every*
   order (Bryant 1991): when no order saves you, change the
   representation. Canonicity is why f ≡ g is one root compare —
   but only under a shared order.

6. Error one: **α ≈ 4.26 is a statement about *random* 3-SAT** — the
   density at which near-certainly-satisfiable flips to
   near-certainly-unsat, with the hard instances packed at the edge;
   quoting it about a structured instance is memorizing the plot
   without the caption — the chapter's interview trap. Error two:
   the arithmetic (8,600/2,000 = 4.3) is right, the *population* is
   wrong — real instances sit off the curve: 8-queens runs α ≈ 18
   and solves in milliseconds. CDCL eats structure — clustering,
   symmetry, intent: learned clauses and VSIDS's recency heap
   exploit *recurring* conflicts, which random instances never
   produce. The cheap observation next time: run it and watch the
   solver's own telemetry — restarts, clause counts, conflict
   statistics — an instance genuinely drifting toward its hard
   regime shows it there long before any timeout; α predicted
   nothing (ch02's easy-hard-easy curve and its caption).

**Part III**

7. **Certification is process evidence; a theorem is artifact
   evidence.** DO-178C DAL A certifies *how you worked*: reviews,
   MC/DC structural coverage, traceability, independent
   verification — machine-checkable nowhere. seL4's theorem is a
   claim about *what the artifact is*: every behavior of the ~10k
   lines of C refines the abstract spec — re-checkable by anyone
   holding the proof, the l4v tree, and Isabelle (whose ~2,500-line
   kernel is the checker). INTEGRITY-178B is certified but not
   formally verified: no machine-checked theorem anywhere, and the
   advertising has let readers hear "proven." seL4's 2023 Common
   Criteria EAL6+ — the first general-purpose kernel at that level —
   is the honest composite: the theorem made the paperwork *cheap*
   (EAL6+ demands formal design artifacts; seL4 showed up with a
   formal *implementation*), while the certificate still adds what a
   proof cannot: independent eyes on the target and the delivery
   chain. Read "verified" in any datasheet as asking which rung
   (ch03's vendor-claim triage).

8. The **Linux kernel memory model (LKMM)**, written in the **Cat**
   language, machine-checked with **herd7** (Alglave, Maranget,
   McKenney, Parri, Stern; formalized 2017; lives in
   `tools/memory-model`). Maintainers run litmus tests — "may this
   reordering happen?" — against a formally defined concurrency
   model, daily, in the ordinary flow of kernel development. What is
   machine-checked is the *concurrency specification*, not the C:
   the data-race-freedom arguments for core synchronization (RCU,
   the locking discipline) are made against it. So the camps both
   won: the microkernel-with-proof flies in drones — seL4's
   refinement theorem at the bottom, compartmentalization above;
   the monolith-with-knowledge made its concurrency spec a
   machine-checked artifact used as ordinary tooling — the field
   escaped the lab, each side holding a piece (ch03's Linux
   paragraph).

**Part IV**

9. (a) The **niche**: `None` *is* the null bit pattern — and a valid
   `&u32` is never null, so no live value collides with the
   discriminant and the tag is free: 8 bytes, no tag word. The same
   bet for `Box`, `NonZero`, and fn pointers. (b) The type system
   proves **safety**: no UB in safe code, given the library's
   invariants hold. **Validity** is the bit-pattern obligation
   unsafe code must maintain: a `bool` is 0x00 or 0x01 — the same
   eight bits are a valid `u8` and an invalid `bool`, validity is
   type-relative; references must be non-null and aligned; `char` ≤
   0x10FFFF; uninitialized memory must not be read as typed data.
   (c) **The read.** Writing 0x02 into the field is legal bytes; the
   UB begins when it is read as a `bool` — and the layout
   optimizations (the niche itself, branch elimination on the
   discriminant) are sound only because validity is upheld: debug
   codegen happened not to exploit the invalid pattern, release did.
   (d) `MaybeUninit` is the honest type for uninitialized memory;
   the distinction is the RustBelt **validity-versus-safety** line
   (ch04's insiders).

10. (a) `async fn` compiles to a state machine: each `.await` is a
    suspend point, and every local live across it becomes a *field*
    of the generated `Future` — `buf`, `n`, `view`, `socket` are all
    fields now. (b) `view = &buf[..n]` is a reference field pointing
    into a *sibling* field: the struct is self-referential. Moving
    the state machine relocates `buf` without rewriting `view` — the
    interior pointer dangles (module 01 ch04: a reference is an
    address into a frame; move the frame, the address is stale).
    (c) Tofte-Talpin **stack order**: a frame's values die with its
    frame, regions nest like frames. Suspension breaks it
    *mid-function* — a suspended future is a frame outliving its
    function, with live ranges crossing the suspend point into a
    compiler-generated heap struct whose region variables the
    programmer never names. (d) `Pin<P>` is a **ghost proposition**
    — "this value will not move again before drop," no runtime
    content, pure obligation carried in the types: `!Unpin` marks
    the types that depend on it, `Box::pin` is the safe derivation
    (the heap move before pinning is the last move), and
    `Pin::new_unchecked` is the admit hole. (e) The fragment is
    permission arithmetic on syntax — region variables, outlives
    edges, a least fixed point — with no formula language to *state*
    "this field points into that sibling field and the move
    discipline preserves it." Stating that needs `x ↦ e`, the
    points-to assertion: full separation logic, the undecidable
    part the fragment dropped to stay decidable. So the language
    ships the obligation as a type-level proposition instead
    (ch04's async section; module 19 ch03's Pin, returning with its
    proof-theory reading).

**Part V**

11. Top to bottom: **(1) mission software** — unverified, by design,
    confined to its compartment; **(2) flight-control and
    middleware apps** — unproven; isolation is done by
    compartmentalization, each component holding only the
    capabilities it was granted — the *capability distribution* is
    what separates them. **(3) the seL4 C kernel (~8,700-10k
    lines)** — *proven*: functional correctness, every behavior
    refines the abstract spec, machine-checked in Isabelle. **(4)
    the abstract specification** — *human-reviewed*, ~10k lines:
    the gap that remains. **(5) ~600 lines of hand-written
    assembly** (entry/exit, context switch, fast paths) —
    *unproven*, audited by reading, kept small on purpose. **(6)
    CompCert** — the ~11 passes Clight→Asm are *proven* (each a
    forward simulation, UB-free source hypothesis, Beh(Asm) ⊆
    Beh(source)), but its own trust base is nested: the Coq kernel
    (~30 kLOC, LCF-style — trusted, small by design); the C
    semantics model (*trusted* — the theorem is about this model,
    not ISO C verbatim); the Coq→OCaml extraction plus the OCaml
    compiler (*unverified* — the binary is tested software that
    emits proven software); the cpp + C parser front-end
    (*unverified edge*: a parsing bug puts you outside the theorem);
    the external assembler/linker and ~15 kLOC C runtime
    (*unverified edges* — the proof ends at Asm text). The edges
    are *tested* — Yang et al's Csmith/EMI harness is the
    discipline — not proven. **(7) the COTS Arm board** — *assumed
    fault-free*: Rowhammer, voltage/clock glitching, EM injection
    sit below every theorem above; the seL4 community's honest
    answer is glitch-detection hardware and redundancy, not more
    proof. **(8) the delivery chain** — *verifiable, not verified*:
    reproducible builds (bit-identical rebuilds, diffoscope) bridge
    the shipped bytes back to the source; SLSA-style attestation is
    signed claims, not a proof of the build. This instantiates
    module 19 chapter 05's "unsafe kernel with a safe membrane" at
    full-system scale: a thin unproven/assumed boundary wrapped
    around a proven core, the membrane made of a theorem plus
    capabilities.

12. (a) 25 py ÷ 15 yr ≈ **1.7 person-years per year, total** — and
    the theorem does not expire when you port the system built on
    it (DARPA HACMS reused it across platforms with no re-proof),
    so the per-consumer figure keeps falling. (b) $100M ÷ $200k =
    **500 person-years ≈ 20 seL4-scale proofs per incident** — and
    the next CVE bills again, while the proof billed once.
    (c) CRUD endpoint: long-lived at best — **1/4, do not pay**
    (the chapter's own scoring; the correct answer, not a failure).
    X25519 in a browser: adversaries hunt it, decade-plus lifetime,
    hundreds of millions of consumers, and failure silently leaks
    keys — **4/4, pay** ("a crypto library or a compiler scores 4").
    One-off migration script: **0/4** — delete it when it finishes.
    (d) Verification's price is *up front and printed*; the
    alternative's price is unbilled until the incident — the option
    that looks free is the one with the unbounded tail (ch05's
    boundary-and-economics close).

</details>
