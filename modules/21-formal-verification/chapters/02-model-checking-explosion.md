# Chapter 02 — Model checking and the state explosion

> **The 30-second version.** Chapter 01 made proofs expensive: a human
> invents the invariant, the kernel checks it. This is the automatic half:
> a program is a state machine, "the lock is never held twice" is a claim
> about every reachable state, and "every reachable state" is a graph
> search. Model checking is that search — exhaustive, dumb as a shovel —
> and it dies by arithmetic: the state count is the product of every
> variable's domain, and 40 booleans at 32 bytes each is 32 TiB of RAM.
> BDDs, SAT, SMT, and bounded checking are tricks for not materializing
> that product; the payoff is industrial — a $475 million division bug,
> Amazon's S3 replication protocol, every driver in the Windows WDK.

## What you are allowed to say: temporal logic from G and F up

A tester gives you one run; real specifications claim things about all runs
forever, and propositional logic cannot phrase them. Temporal logic adds
four operators over positions of an infinite run, each with a real spec for
the lock checked below:

- **G** ("always"): `G ¬(p0crit ∧ p1crit)` — never both critical. Safety.
- **F** ("eventually"): `G (p0wait → F p0crit)` — a wait is later granted.
  Liveness.
- **X** ("next"): `G (p0wait → X (p0crit ∨ p1crit))` — one step after P0
  asks, somebody holds the lock.
- **U** ("until"): `G (p0wait → (¬p0crit U grant0))` — P0 stays out until
  the grant, which eventually comes (`F p` is `true U p`).

The discipline's workhorse is the pair, safety ∧ liveness:

```text
G ¬(both in CS)  ∧  G (request → F enter)
```

The halves fail differently, and that drives every algorithm here: a
safety violation is a *finite* path to a bad state (reachability,
memory-bound); a liveness violation is an *infinite* path, which a finite
graph realizes only as a cycle — a lasso of prefix plus loop.

**LTL vs CTL: linear or branching.** LTL judges each infinite run
separately: every run a film strip. CTL quantifies over the tree of runs at
each state: `AG EF restart` = along **A**ll branches, **A**lways there
**E**xists a branch that **F**inally reaches restart — recoverability,
which no single strip can say (it needs a choice between futures).
Conversely LTL's `FG p` ("each run eventually settles into p") has no CTL
equivalent — the nearest, `AF AG p`, demands a *common* convergence state,
strictly stronger. The logics are *incomparable* (Emerson–Halpern; stated,
not derived); CTL* subsumes both:

```text
                  s0            LTL: one film strip per infinite path,
                /-  \-\          judged alone; "FG p" = every strip
             a /      \ b        eventually stays p.
             v          v
           s1            s2      CTL: quantifiers AT the nodes; "AG EF p"
          / \            / \     = from EVERY node, SOME forward path
        a/   \b ...    a/   \b   hits p -- a tree property no strip sees.
       v      v        v      v
```

## The machine: Kripke structures, and the explosion as RAM arithmetic

The model is a **Kripke structure**: states, transitions, and a labeling
mapping each state to the atomic propositions true there. The teaching
model, a 2-process lock: P0 with three locations {o out, w waiting, c
critical}, P1 coarse with two {o, c} — P1's request+acquire is one atomic
step, so it plays the process that keeps winning races. Raw product
3 × 2 = **6 states**, drawn fully:

```text
                   P1 out                P1 critical
              +---------------+    +---------------+
      P0 out | s0 (o,o)      |    | s3 (o,c)      |
             | both idle     |    | P1 in CS      |
             +---------------+    +---------------+
      P0 wait| s1 (w,o)      |    | s4 (w,c)      |
             | P0 wants in   |    | P0 queued     |
             +---------------+    +---------------+
      P0 crit| s2 (c,o)      |    | s5 (c,c)      |
             | P0 in CS      |    | BOTH IN CS    |
             +---------------+    +---------------+
  transitions:  s0->s1 P0 requests         s1->s2 grant P0
                s0->s3 P1 request+grab     s1->s4 P1 SNATCHES the free
                s2->s0 P0 releases               lock (unfair: P0 waits)
                s3->s0 P1 releases         s3->s4 P0 requests behind P1
                s4->s1 P1 releases, NO handoff to P0
                s5: no incoming edge -- the ghost: in the raw product,
                forbidden by the safety property, never reached.
```

Deliberately unfair: safety holds (s5 unreachable), liveness fails (P1 can
cycle s1→s4→s1 forever). That split is the pedagogy.

**The arithmetic — derived slowly, because it is the chapter's spine.** A
state is a valuation of the variables; the space is the *product* of the
domains (the lock: 3 × 2 = 6). n booleans give 2^n states, and the checker
stores a visited-set so BFS knows when it is done — call it 32 B per
state, a fingerprint plus bookkeeping; the constant is negotiable, the
exponent is not:

- **10 booleans**: 1,024 states × 32 B = 2^15 = **32 KiB** — L2 cache.
- **20 booleans**: 2^20 ≈ 1M states × 2^5 B = 2^25 = **32 MiB** — any
  laptop. A toy.
- **30 booleans**: 2^30 ≈ 1.07G states × 2^5 B = 2^35 = **32 GiB** — a
  big server's DIMMs, hours of checking, borderline.
- **40 booleans**: 2^40 ≈ 1.1T states × 2^5 B = 2^45 = **32 TiB** — no
  machine on earth (2025's 8-socket servers top out near 12–16 TiB).
  Never fits: shaving bytes-per-state cannot help; the exponent eats any
  constant.
- **50, 60 booleans**: 32 PiB, 32 EiB — the storage of the species.

Time at a TLC-class 10^6 states/s: 2^30 ≈ 18 minutes, 2^40 ≈ 13 days,
2^50 ≈ 36 years — but RAM kills first, and ten more booleans is ×1,024 on
both axes. A realistic protocol — 12 processes × 5 local states, two
16-bit counters, 6 flags — is 5^12 × 2^32 × 2^6 ≈ 2^58 states before you
write one transition. The state explosion is a heap-out-of-memory error
with the multiplication written on it; the frontier of the whole field is
that one multiplication: states × bytes-per-state versus bytes-of-RAM.

## The algorithms: reachability, the product automaton, and the lasso

**Safety is a graph search.** `G ¬bad` holds iff no reachable state
carries the bad labeling: BFS from the initial state. The entire
algorithm; you write it in the lab.

**Liveness is a cycle search in a product.** An infinite run violates
`G (w → F c)` only by eventually never granting — by entering a cycle that
never grants. Negate the property (automata come easier for negations):
"eventually w, and never c after" as a 3-state **Büchi automaton**:

```text
   q0 --- w and not c ---> q1 (accepting)   a run accepts iff it takes
   ^  \                        \            the q0->q1 edge, then stays
   |   \ anything              \ not c      in q1 forever
   +---- (waiting to guess)      -- c: run dies
```

The **product automaton** pairs model states with automaton states:
6 × 3 = 18 conceivable, BFS reaches 7. A run **accepts** iff it visits
accepting states infinitely often — **Büchi acceptance**, which on a
finite graph means an accepting state on a reachable cycle. Here:
(s1,q1) → (s4,q1) → (s1,q1) — P0 waiting forever while P1 snatches and
releases. The classical emptiness check is the **nested DFS** of
Courcoubetis–Vardi–Wolper–Wolper — two stacks, ten lines:

```python
def dfs1(s):                  # stack1: reachable states
    push1(s); mark1(s)
    for t in succ(s):
        if not marked1(t): dfs1(t)
    if accepting(s): dfs2(s)  # postorder: s's subtree is done

def dfs2(s):                  # stack2: hunt a path back to stack1
    push2(s); mark2(s)
    for t in succ(s):
        if on_stack1(t): REPORT_LASSO(stack1, stack2)
        if not marked2(t): dfs2(t)
    pop2()
```

The trick is the postorder call: dfs2 starts only after everything
reachable from an accepting state is explored, so an edge back into the
live stack1 is exactly a cycle through that state. Two linear passes over
the visited set — the explicit checker's complete kit.

**The counterexample is a trace.** The tool prints the lasso with
transition names — the failing test you did not have to write. The daily
TLA+/TLC workflow: check, read the lasso, see that the model forgot the
fairness assumption (or the design forgot the handoff), refine, re-check —
minutes per iteration, which is why practitioners call the model checker a
design debugger, not a courtroom.

## BDDs: truth tables with the repetition squeezed out

The explicit checker stores states; the symbolic one stores the *function*
"is this state reachable?" A truth table over n variables is 2^n rows —
the same explosion. A **binary decision diagram** reorganizes the table as
a DAG of binary tests, reduction rules doing the work: merge identical
terminals, merge isomorphic tests, delete a test whose two edges coincide.
Worked example, order x, y, z, `f = z ∧ (x ∨ y)`:

```text
   x y z | f            truth table: 8 rows
   0 0 0 | 0                    x
   0 0 1 | 0                  0/ \1
   0 1 0 | 0                  /   \
   0 1 1 | 1                 y     |       BDD: 5 nodes
   1 0 0 | 0               0/ \1  |       (3 tests + 2 terminals)
   1 0 1 | 1               /   \  |
   1 1 0 | 0              F     \ |
   1 1 1 | 1                   (z)  <-- ONE node, TWO parents:
                                0/ \1    reached from x=1 AND y=1
                               [F] [T]
```

Five nodes for eight rows, the sharing structural: the z-test has two
parents, and when x = 1 the y-test is skipped (`f|_{x=1} = z`; y is a
don't care). Generalize to `z ∧ (x1 ∨ … ∨ xn)`: 2^(n+1) table rows, n + 3
BDD nodes — at n = 20, 23 nodes against 2,097,152 rows. Structured
functions get O(n) BDDs; unstructured ones saturate near 2^n/n. Three
properties make it a data structure:

- **Canonicity (Bryant's theorem).** Given a fixed variable order, the
  reduced BDD of a function is unique — so `f ≡ g?` is one root-pointer
  compare, O(1), against a 2^n-row diff. This is why hardware equivalence
  checking is a product you can buy.
- **The ITE operator.** Every Boolean operation is
  `ite(f, g, h) = if f then g else h`, recursive with memoization; AND, OR,
  NOT are special cases. Symbolic reachability is BFS whose frontier is a
  BDD and whose step is one apply.
- **Variable order is destiny.** Interleaved order a0 b0 a1 b1 …: an
  n-bit ripple-carry adder's carries have linear BDDs; blocked (all a's,
  then all b's): the same functions blow up exponentially. And for some
  functions no order helps — the famous bad case: the multiplier's middle
  bit needs 2^Ω(n) nodes under every order (Bryant 1991). Hence
  **sifting** — walk each variable through all positions by adjacent
  swaps, keep the best, rerun on growth — and the folklore honesty:
  *ordering is an art*; when the multiplier blows up, change the
  representation, not the order.

## From SAT to SMT to bounds: CDCL as a memory machine

**SAT**: given clauses, is there an assignment making all true? 3-SAT is
NP-complete — stated once, then tempered: CDCL solves structured instances
with millions of variables, and its engine reads best as a memory system.

- **Watched literals (the lazy clause store).** Each clause nominates two
  *watched* literals; a per-literal occurrence list maps literals to the
  clauses watching them. The **invariant**: a clause is inspected only
  when one of its watches becomes false. Then: other watch true →
  satisfied, nothing to do; else scan for a non-false non-watched literal
  → found, *move the watch* (clause stays armed); not found → the other
  watch is the only hope → **unit propagation** — unless already false:
  **conflict**. One assignment costs only the falsified literal's
  occurrence list, not the clause count — with a million learned clauses,
  O(1)-ish versus O(N) per propagation.
- **The clause DB is a cache, not a truth.** Each conflict teaches one
  clause (1-UIP); the DB grows unboundedly, so solvers score clauses
  (activity, or LBD — literal blocks distance) and periodically delete the
  bottom half. Soundness never depended on learned clauses — only search
  guidance does.
- **Restarts + the activity heap.** At conflict thresholds the solver
  discards the assignment and restarts on **Luby's universal sequence**
  (1,1,2,1,1,2,4,… × a base like 100 conflicts — MiniSat 2.2's default).
  Branching picks the most *active* variable: **VSIDS** bumps activity on
  every literal in a conflict clause and decays all activities ×0.95 per
  conflict — *lazily*: bump amounts grow by one multiply, old activities
  decay only relatively, no O(n) sweep — and the champion lives in a
  **binary heap** (module 01 ch05's heap, back as a weapon: bump =
  sift-up, decision = pop-max, O(log n) each). Decay makes the heap a
  recency window on recent conflicts: the solver re-greeds toward whatever
  is fighting back.
- **The phase transition.** Random 3-SAT with α = clauses/variables is
  near-certainly satisfiable below α ≈ 4.26, unsat above, hard instances
  packed at the knife edge — the easy-hard-easy curve, with a
  search-vs-decision asymmetry: below, greedy walks find solutions; well
  above, propagation collapses the formula; at the edge both flail. Real
  instances sit off the curve: 12-queens is 2,608 clauses over 144
  variables (α ≈ 18), "hard" by random-instance intuition, solved in
  milliseconds — structure (clustering, symmetry, intent) is what CDCL
  eats. Random 3-SAT is the physicist's model of the disease, not the
  disease.

**SMT: SAT plus theories, DPLL(T).** Program properties are not Boolean:
`a[i] = a[j] ⇒ i = j` is equality and arrays. The solver runs the CDCL
core over the propositional skeleton (each theory atom one Boolean) and
consults **theory solvers**; the **Nelson–Oppen combination** lets
signature-disjoint, stably-infinite theories cooperate — purify into
per-theory parts, exchange (disjunctions of) equalities between shared
variables at each fixpoint:

```text
      +---------------------------+
      |       CDCL SAT core       |  skeleton: p := (x = y),
      |  watches / VSIDS / learn  |  q := f(x) != f(y), ...
      +--------+----------+------+
   partial skeleton |          | theory lemmas, implied atoms, conflicts
   assignment (atoms) v          ^
      +---------------------------+
      |   DPLL(T) theory solver   |  EUF | arrays | arithmetic | ...
      |   Nelson-Oppen: exchange  |  theories share only the shared
      |   equalities on the       |  variables' equalities
      |   shared variables        |
      +---------------------------+
```

The theories that matter for programs:

- **EUF (equality + uninterpreted functions).** `x = y, f(x) ≠ f(y)` is
  unsat: `f` is a function — equal inputs, equal outputs. The algorithm is
  **congruence closure**: build the term DAG, merge asserted equalities
  with **union-find** (path compression as the *decision procedure*), and
  when argument-classes merge, merge their `f`-applications too
  (congruence) to fixpoint; contradiction = terms asserted distinct
  sharing a class. Near-linear.
- **Arrays.** McCarthy's axioms as rewrites —
  `select(store(a,i,e), j) = e` if `i = j`, else `select(a, j)` — plus
  extensionality.
- **Difference logic** (constraints `x − y ≤ c`): each constraint is an
  edge `y → x` of weight c; unsat iff the graph has a **negative cycle**.
  `x−y ≤ 1, y−z ≤ 2, z−x ≤ −4`:

```text
        1             2          around the cycle: 1 + 2 - 4 = -1 < 0;
    y -----> x    z -----> y     the constraints imply 0 <= -1: UNSAT.
    ^        |                   The solver is Floyd-Warshall /
    |   -4   |                   Bellman-Ford finding the negative cycle
    x <------z                   -- shortest paths as a decision procedure.
```

**BMC: unroll and hand to SAT.** Concede the explosion, buy depth: unroll
the transition relation k steps as one formula —
`I(s0) ∧ T(s0,s1) ∧ … ∧ T(s_{k−1},s_k) ∧ ¬(P(s0) ∧ … ∧ P(s_k))` — and
call the solver. Size is linear in k (each step a copy of T); a bug within
k steps is found in seconds, and the satisfying assignment *is* the
counterexample trace. The bound bites the other way: a bug whose shallowest
witness sits at depth 10^6 needs a million copies of T and a search beyond
any solver — and "no counterexample within k" proves nothing about step
k+1: the **complete-vs-bounded** distinction. The ladder up is
**k-induction** — base case (P on the first k states) plus inductive step
(k consecutive P-states imply P at k+1), sound once strengthened with
auxiliary invariants; loop invariants are the k = 1 case. The modern
unbounded engine is **IC3/PDR**, in three sentences: maintain frames
F0 ⊇ F1 ⊇ …, each an over-approximation of the states reachable in ≤ i
steps as learned clauses; when a frame state reaches a bad state in one
step, generalize it into a clause, add it to earlier frames, and
recursively prove its pre-states unreachable; when a frame converges with
its successor, the frames are an inductive invariant — the property holds
for all time. The state of the art inside industrial model checkers.

## What it bought: S3, SDV, and the $475 million division bug

- **Intel Pentium FDIV, 1994 — the incident that created the industry.**
  Five entries missing from the radix-4 SRT divider's PLA lookup table;
  about 1 division in 9 billion went wrong from roughly the fifth
  significant digit on. Thomas Nicely, computing twin-prime reciprocals,
  noticed before Intel's tests did; the recall cost **$475 million**
  (Intel's 1994 charge). Intel's response was structural — a formal
  verification group, and within years model checking in every EDA
  vendor's flow at $0.3–0.5M per seat per year: one FDIV paid for the
  industry's tools for a decade. Moral, one line: the license is cheaper
  than the recall, and you only learn which by having the bug.
- **Microsoft SLAM/SDV, 2000s — model checking as a product.** SLAM (Ball
  and Rajamani) abstracted C drivers to boolean programs, checked API
  usage rules, refined on counterexamples (CEGAR — this chapter's
  counterexample loop, industrialized), and shipped as the **Static Driver
  Verifier in the Windows Driver Kit** from the Vista era: overnight checks
  against roughly 90 usage rules, finding real defects in Microsoft's own
  drivers.
- **Amazon TLA+/TLC, 2014–15 — the S3 replication race.** The CACM 2015
  report ("How Amazon Web Services Uses Formal Methods") covers TLA+ on
  ten major systems; for **S3**, TLC explored the model of a
  block-replication change and surfaced a race in which two components
  could both conclude they owned a block — data-loss class, reachable only
  when a disk failed inside a narrow timing window a test cluster would
  essentially never hit. Cost: engineer-weeks. The counterfactual is
  public: the *unrelated* four-hour S3 outage of 2017 was estimated at
  ~$150M across S&P 500 companies. By the early 2020s AWS cites **450+
  TLA+ specifications**. Person-weeks against nine-figure incidents — the
  economic argument in one row.

## What insiders know

- **TLA+ is executable pseudocode — Lamport's own position.** The spec's
  audience is the designers in the design meeting, not the provers: written
  to be read by humans and executed by TLC, in that order. **PlusCal** is
  the proof: write the algorithm once as C-like pseudocode and the
  translator expands it into TLA+ no one reads — "write the algorithm
  twice" is the honest description, and the expansion is what gets checked.
- **TLC's scaling school is "just buy RAM."** A comfortable run sweeps
  ~10^8–10^9 states on a 16–64 GB laptop; past that, distributed TLC
  parallelizes the visited-set across a cluster — the first move is more
  workers, not smarter encodings. **Apalache** is the counter-school:
  SMT-based, trading explicit states for proof-style encodings when the
  product is hopeless.
- **The counterexample is the failing test you didn't write.** The
  production workflow is hybrid: check the design at spec level, test the
  implementation at code level; the bridge is the **refinement mapping** —
  the implementation's spec proves correctness by simulating the abstract
  spec (a simulation relation plus gluing invariants), so what you tested
  and what you proved stay connected.
- **SMT in CI: pin the version, budget the timeout.** Folklore numbers:
  ~30 minutes per obligation, and *no monotonicity* — a solver upgrade can
  flip three proofs to `unknown` with zero changes to your code, because
  incomplete heuristics are allowed to get unluckier. Pin the solver
  version like a lockfile (z3 4.8.x vs 5.x is a real CI incident class);
  treat bumps as reviewable changes.
- **The "no true Scotsman" diagnosis.** A timeout cannot distinguish
  *hard* from *misconstrained* from *wrong theory*. The folklore order:
  shuffle assertion order (timing-out vs immediate = luck, not depth),
  drop to SAT-only to bisect the theory, demand the unsat core, try
  another solver — before rewriting anything.
- **The phase transition is an interview trap.** α ≈ 4.26 is a statement
  about *random* 3-SAT; quoting it about a structured instance (α = 18,
  solved in milliseconds) means you memorized the plot without the caption.

## See it yourself

All labs verified on CPython 3.12, z3 5.1.0.

**Lab 1 — a 40-line explicit model checker** (the whole chapter in one
file): the 6-state lock, both properties, the counterexample printed:

```python
# mc.py -- explicit-state model checker, module 21 ch02
# 6-state lock: P0 in {o,w,c}, P1 in {o,c} -> 3*2 = 6 states
from collections import deque
S = {0:"(o,o)", 1:"(w,o)", 2:"(c,o)", 3:"(o,c)", 4:"(w,c)", 5:"(c,c)"}
T = {0:[1,3], 1:[2,4], 2:[0], 3:[0,4], 4:[1], 5:[]}
w0, c0, c1 = (lambda s: s in (1,4)), (lambda s: s in (2,5)), (lambda s: s in (3,4,5))

# safety: G !(P0crit and P1crit) == no reachable bad state (BFS)
seen, q = {0}, deque([0])
while q:
    for t in T[q.popleft()]:
        if t not in seen: seen.add(t); q.append(t)
bad = [s for s in seen if c0(s) and c1(s)]
print("safety  G !both-critical:", ("FAIL " + str(bad)) if bad else "PASS",
      f"({len(seen)}/6 states reachable; s5=(c,c) never reached)")

# liveness: G(P0wait -> F P0crit) == no lasso avoiding P0crit
def path(src, dst, ok):             # shortest path through ok-states
    par, q = {src: None}, deque([src])
    while q:
        s = q.popleft()
        for t in T[s]:
            if t in ok and t not in par: par[t] = s; q.append(t)
    if dst not in par: return None
    out = []
    while dst is not None: out.append(dst); dst = par[dst]
    return out[::-1]

ok = set(s for s in S if not c0(s))
for x in sorted(seen):              # lasso = prefix 0->x + cycle
    if c0(x): continue              # cycle avoids P0-critical
    for y in T[x]:
        if y in ok and (cyc := path(y, x, ok)) and any(w0(s) for s in [x] + cyc):
            pre = path(0, x, set(S))
            print("liveness G (wait -> F enter): FAIL -- counterexample lasso")
            print("  prefix:", " -> ".join(f"s{s}{S[s]}" for s in pre))
            print("  loop:  ", " -> ".join(f"s{s}{S[s]}" for s in [x] + cyc), "(repeats forever)")
            raise SystemExit
print("liveness G (wait -> F enter): PASS")
```

```console
$ python3 mc.py
safety  G !both-critical: PASS (5/6 states reachable; s5=(c,c) never reached)
liveness G (wait -> F enter): FAIL -- counterexample lasso
  prefix: s0(o,o) -> s1(w,o)
  loop:   s1(w,o) -> s4(w,c) -> s1(w,o) (repeats forever)
```

BFS proved safety in eight lines; the lasso search is a hand-rolled nested
DFS. Change s4's exit to a handoff (`4:[2]`) and both properties pass —
that edit is the refine-and-recheck loop.

**Lab 2 — 8-queens as SAT, the growth measured**
(`python3 -m pip install z3-solver`):

```python
# queens_sat.py -- n-queens as SAT: one Bool per square, pairwise conflicts
import time
from z3 import *

def queens(n):
    x = {(r, c): Bool(f"x{r}_{c}") for r in range(n) for c in range(n)}
    s = Solver()
    for r in range(n): s.add(Or([x[r, c] for c in range(n)]))   # >= 1 per row
    for a in x:
        for b in x:                        # each conflicting pair once:
            if a < b and (a[0] == b[0] or a[1] == b[1]
                          or abs(a[0] - b[0]) == abs(a[1] - b[1])):
                s.add(Or(Not(x[a]), Not(x[b])))                  # at-most-one
    t0 = time.perf_counter(); res = s.check()
    return res, time.perf_counter() - t0, len(s.assertions())

for n in (8, 12):
    res, dt, ncl = queens(n)
    print(f"n={n:2d}: {res}  {dt*1000:6.1f} ms  {ncl} clauses")
```

```console
$ python3 queens_sat.py
n= 8: sat     1.4 ms    736 clauses
n=12: sat     4.1 ms   2608 clauses
```

Clauses grew 3.5×, time 3×; n = 16 and n = 20 here measured 6,336 clauses
/ 24.8 ms and 12,560 / 81.3 ms. The caption is the lesson: α ≈ 18 is
"over-constrained hard" by random-instance intuition, and CDCL eats it —
structure is not density.

**Lab 3 — watched-literal DPLL on paper.** Five clauses, initial watches
underlined:

```text
C1: (¬a ∨ c ∨ d)   watches ¬a, c      C4: (¬d ∨ e)      watches ¬d, e
C2: (¬a ∨ b)       watches ¬a, b      C5: (¬d ∨ ¬e ∨ a) watches ¬d, ¬e
C3: (¬b ∨ ¬c)      watches ¬b, ¬c
```

Trace four steps, writing the occurrence list consulted each time:

| step | assignment | falsified | visited | action |
|------|-----------|-----------|---------|--------|
| 1 | decide a = T | ¬a | C1, C2 (2/5) | C1: move watch ¬a→d; C2: no spare (binary) → **unit** b = T |
| 2 | propagate b = T | ¬b | C3 (1/5) | C2 watches b = true: satisfied, frozen; C3: **unit** c = F |
| 3 | propagate c = F | c | C1 (1/5) | other watch d unassigned, spares false → **unit** d = T |
| 4 | propagate d = T | ¬d | C4, C5 (2/5) | C4: **unit** e = T; C5: spare a true → move watch ¬d→a, satisfied forever |

Total clause visits: 6; scanning all clauses per assignment would visit
4 × 5 = 20. At a million learned clauses, the invariant — a clause is
inspected only when a watched literal dies — *is* the data structure.

**Lab 4 — BMC's bound arithmetic, felt**:

```python
# bmc.py -- bounded model checking: a 3-bit counter unrolled k steps
import time
from z3 import *

def bmc(k):
    c = [BitVec(f"c{i}", 3) for i in range(k + 1)]     # states c0..ck
    s = Solver()
    s.add(c[0] == 0)                                   # initial condition
    for i in range(k): s.add(c[i + 1] == c[i] + 1)     # unrolled transition
    s.add(Or([c[i] == 0 for i in range(1, k + 1)]))    # NOT "never wraps"
    t0 = time.perf_counter(); res = s.check()
    print(f"k={k:3d}: {res}  {(time.perf_counter() - t0) * 1000:6.1f} ms  "
          f"{len(s.assertions())} constraints")
    if res == sat:
        m = s.model()
        j = next(i for i in range(1, k + 1) if m.eval(c[i]).as_long() == 0)
        print("       wrap at step", j, ":",
              [m.eval(c[i]).as_long() for i in range(j + 1)])

for k in (7, 8, 10, 64):
    bmc(k)
```

```console
$ python3 bmc.py
k=  7: unsat    0.4 ms    9 constraints
k=  8: sat      0.3 ms   10 constraints
       wrap at step 8 : [0, 1, 2, 3, 4, 5, 6, 7, 0]
k= 10: sat      0.3 ms   12 constraints    (same witness: wrap at step 8)
k= 64: sat      0.6 ms   66 constraints    (same witness: wrap at step 8)
```

At k = 7 "never wraps" is boundedly true (0..7, no zero yet); at k = 8 the
wrap is *proved false* with a full witness. k = 64 costs 66 constraints —
linear in k, as promised — and still finishes sub-millisecond, honestly:
because the bug is shallow. Had the property been true, k = 64 would answer
"no counterexample ≤ 64" and nothing more; proving "never" is k-induction's
or IC3's job, not BMC's.

## How to use this knowledge

- **Whiteboard protocol triage.** N components × M local states × message
  domains: multiply before anyone writes a config. 10 workers × 6 states ×
  two 8-value slots is 6^10 × 64 ≈ 3.9 × 10^9 states ≈ 124 GiB of
  visited-set — dead on a laptop, alive on a cluster. Thirty seconds of
  arithmetic decides the tool.
- **Pick the tool by the exponent.** Explicit TLC for ~10^8–10^9 states;
  SPIN-class with symmetry and partial-order reduction one order further;
  BDDs for structured functions with a knowable order; SAT/BMC for "a bug
  within k steps"; SMT for arrays, equalities, arithmetic; IC3-class when
  you need unbounded proofs without a human invariant writer.
- **CI discipline for solvers.** Pin the version in the lockfile, budget
  timeouts per obligation, log conflicts, restarts, clause counts — a
  solver suddenly restarting ten times more is telling you the instance
  drifted toward its hard regime before anything times out.
- **Reading incident reports.** "Found by a model checker before
  deployment" (S3) and "found by a customer's spreadsheet" (FDIV) are the
  same sentence: the reachable-state graph versus one unlucky run.
- **Interview fluency.** Watched-literal cost, VSIDS's heap and lazy decay,
  α ≈ 4.26 and why it spares structured instances, BDD canonicity = O(1)
  equivalence: the standard probe set.

## Connections

- **Module 21 chapter 01** — the other half of the field: there the human
  invents the invariant and the LCF kernel checks it; here the machine
  invents nothing and enumerates everything. Trust inverts: a kernel small
  enough to audit versus a checker simple enough to rewrite in 40 lines.
- **Module 21 chapter 03** — seL4's person-decade proofs versus this
  chapter's person-week checks: when each is the right price for the
  guarantee.
- **Module 21 chapter 04** — the borrow checker is separation logic: the
  heap invariant a human would write, compiled into a type system; this
  chapter is why that automation was the field's dream.
- **Module 21 chapter 05** — verified compilers and crypto: BMC at bounded
  depth *tests* what IC3 and proof assistants *prove* — the constant-time
  spectrum's two ends.
- **Module 09 chapter 01** — the state explosion versus the concurrency
  tools: stress tests, race detectors, and fuzzers *sample* the product
  space this chapter enumerates; determinism is what the memory budget
  buys.
- **Module 17 chapter 03** — the BDD/SAT engines as circuits: the same
  watched literals and activity heaps verifying RTL — the tools FDIV
  bought.
- **Module 01 chapter 05** — the heap-size frontier arithmetic: VSIDS's
  binary heap and the 32 MiB / 32 TiB multiplication are the same argument
  (a data structure is a memory budget) made rigorous.

## Quiz

1. **Concept.** State Bryant's canonicity theorem precisely, explain why a
   fixed variable order is a precondition, and why comparing the roots of
   two BDDs built under *different* orders is meaningless even if each is
   individually reduced.
2. **Concept.** `AG EF restart` is CTL-only; `FG p` is LTL-only. Give the
   one-sentence argument (film strips vs. trees) for each direction of the
   incomparability.
3. **Predict the RAM.** A protocol model has 26 booleans; the visited-set
   costs 32 B per state. Derive the memory needed, and how many additional
   booleans fit before a 16 GiB laptop is exhausted.
4. **Predict the bound.** The BMC lab runs on a 4-bit counter with the same
   "never wraps" property. At which k does `sat` first appear, and what is
   the witness? At k one less, what does the tool prove — and what does it
   *not* prove?
5. **Predict the count.** The 8-queens pairwise SAT encoding measured 736
   clauses at n = 8 and 2,608 at n = 12. Predict n = 16 under cubic
   scaling, reconcile with the measured 6,336, and say which part of the
   encoding carries the discrepancy.
6. **Diagnose.** A TLA+ spec has 10 worker processes with 6 local states
   each and two shared 8-value message slots. TLC on a 16 GB laptop dies
   around 2 × 10^8 states, hours in. Compute the raw product; did TLC die
   of RAM or of patience? Give two model-level (not hardware) fixes.
7. **Diagnose.** CI pinned z3 4.8.x proves 12/12 SMT obligations in 4
   minutes. A base-image bump silently moves to 5.x; now 3 obligations
   return `unknown` after the 30-minute timeout, source unchanged. What
   property of SMT solvers is responsible? Give the two process fixes and
   one debugging move before anyone rewrites a query.
8. **Cross-chapter.** VSIDS keeps branching candidates in the binary heap
   of module 01 chapter 05. Name the two heap operations CDCL performs
   around one conflict and their costs, and explain how the ×0.95
   per-conflict decay is O(1) rather than an O(n) sweep.

<details>
<summary>Answers</summary>

1. Given a fixed total variable order and the three reduction rules (merge
   identical terminals, merge isomorphic tests, delete tests whose edges
   coincide), every Boolean function has exactly one reduced BDD. So
   equivalence is one root-pointer compare — O(1) — after building both
   under that order, versus a 2^n-row diff. Reduction is relative to the
   order: the same function under two orders yields different minimal DAGs
   (the adder's linear vs. exponential shapes), so cross-order root
   comparison is not well-posed — equivalence checkers standardize the
   order first.
2. `AG EF restart` alternates quantifiers between tree levels (A at every
   node, E below); an LTL formula sees one strip and cannot choose between
   futures. `FG p` lets each strip settle privately; CTL's nearest attempt,
   `AF AG p`, forces a common convergence state after which all branches
   behave — strictly stronger. Neither subsumes the other; CTL* has both
   powers.
3. States = 2^26 = 67,108,864; memory = 2^26 × 2^5 = 2^31 B = **2 GiB**,
   which fits. 16 GiB = 2^34 B allows 2^34/2^5 = 2^29 states: **3 more
   booleans** (each doubles). One line: n booleans cost 2^(n+5) bytes.
4. A 4-bit counter holds 0..15 and first returns to 0 at step 16, so k = 16
   is the first `sat`, witness `[0, 1, …, 15, 0]`. At k = 15 the tool
   reports `unsat` for the negated property — "no wrap within 15 steps" —
   proving nothing about step 16 or beyond: bounded `unsat` claims only
   the first k steps, and "never wraps" is exactly what BMC cannot say
   without k-induction/IC3.
5. Cubic scaling: (16/8)^3 = ×8, so ~5,900 predicted against 6,336
   measured (×8.6). The encoding is three families with different cubic
   constants — rows + columns exactly n²(n−1), diagonals
   2·Σ_k C(n−|k|,2) with leading term 2n³/3 — plus n negligible
   at-least-one clauses, so ×8 scaling is approximate; the diagonals carry
   the drift.
6. 6^10 × 8 × 8 ≈ 3.87 × 10^9 states; at 32 B that is ~124 GiB of
   visited-set plus TLC's per-state overhead, so a 16 GB laptop dies
   around 2–4 × 10^8 states — **RAM, not patience** (time would be ~1
   hour at 10^6 states/s if the set fit). Fixes: declare the 10 workers
   symmetric in TLC (interchangeable; divides the space by roughly 10!),
   and shrink a domain — collapse the 8-value message slots to the values
   the property reads, or merge local states the spec never distinguishes.
   Both re-run the multiplication; neither touches the property.
7. SMT decision procedures are incomplete heuristics with **no monotonicity
   guarantee across versions** — 5.x is neither better nor worse in
   general, only differently lucky on your obligations, and `unknown`
   after a timeout is an allowed answer. Fixes: pin the solver version in
   the lockfile and make dependency bumps visible (never silent
   base-image drift); keep the timeout budget explicit. Debugging move:
   reproduce with the old binary to bisect solver vs. query and read the
   statistics/unsat core — often the new version merely ordered theory
   branching unluckily (shuffling assertions is the cheap test).
8. Bump: every literal in the learned conflict clause gets an
   increase-key — sift-up, O(log n) (unassigned ones re-insert, O(log n)).
   Decision: pop the max-activity unassigned variable — delete-max,
   O(log n). Decay is lazy: instead of multiplying n activities, the solver
   scales the global bump increment up by 1/0.95 per conflict, so new
   bumps outweigh old ones — relative decay, one multiply, no sweep. The
   heap becomes a recency window on recent conflicts, which is the point
   of VSIDS.

</details>
