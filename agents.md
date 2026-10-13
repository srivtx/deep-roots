# deep-roots — automatic session ledger

The same discipline as srivtx/p-rick: every work session appends what it did, when, and
how long it took, so nobody has to remember or ask. Human sessions (if any) follow the
same format.

---

## Session 001 — 2026-10-13 · the pivot: module 01 (memory models) created and shipped

- **Task:** pivot from the p-rick research program to a new learning repo; create
  `srivtx/deep-roots`; write module 01 (memory models of programming languages) from
  first principles — 23 chapters, per-chapter quizzes, 30-question final exam,
  interactive web quiz, full roadmap for modules 02-12, dual-theme site on GitHub
  Pages in the p-rick family style.
- **Agents:** main orchestrator (Super Z / GLM) + 15 chapter-writing subagents in
  three waves (C/C++/Rust/Go/Java · C#/Python/JS/Swift-ObjC/COBOL ·
  elders/allocators/hardware-memory-model/security/mobile).
- **Content:** chapters 01-07 (the machine: silicon, hierarchy, virtual memory, stack,
  heap, object layout, assembly) and 23 (unification) + final exam written by the main
  agent; chapters 08-22 written by subagents against a mandatory-content checklist and
  the chapter template in `modules/01-memory-models/CONVENTIONS.md` (~84k words total,
  16 languages, 190+ quiz questions, per-chapter labs).
- **Site:** `tools/build_site.py` (custom markdown→HTML pipeline) generates 23 chapter
  pages + final-exam + index + roadmap + sitemap/robots/404/og-image; `quiz.html` is a
  hand-written 24-question interactive quiz (scored, explanations, progress). Dual
  theme (warm paper/navy, gold, Playfair + Inter) adapted from srivtx/p-rick.
- **QA:** tag-balance validation across all 27 generated pages (0 issues); markdown
  artifact scan (fixed nested-italic-bold, `**O**wned`-style letter emphasis, and
  "2004."-as-list-marker bugs in the converter); link-path bug found via headless
  browser + VLM screenshot review (chapters/ pages referenced `assets/` without
  `../` — fixed, re-verified styled rendering, quiz interaction flow, theme toggle);
  zero console errors.
- **Timed-out subagents:** the Go (roots-2-d) and COBOL (roots-3-e) agents hit the
  session deadline after writing complete files (all sections + quiz + answers
  verified present) — only their final reports were lost; the C# agent (roots-3-a)
  was retried successfully.
- **Result:** pushed to `main`, Pages enabled, live at
  <https://srivtx.github.io/deep-roots/>.

---

## Session 002 — 2026-10-13 · the expansion: modules 02-12, all fields, first principles

- **Task:** extend the program from one module to twelve: write modules 02-12
  (memory security & exploitation, software bugs, decompilation & RE, Android,
  iOS, desktop OS internals, COBOL & mainframe, concurrency & distributed memory,
  compilers & runtimes, database storage engines, GPU & accelerator memory) from
  first principles at insider depth, with quizzes, labs, and final exams; extend
  the site to a 12-module program; push.
- **Agents:** main orchestrator (Super Z / GLM) + ~75 chapter/exam-writing
  subagents in waves. Main agent wrote the 11 module specs (READMEs with
  per-chapter mandatory-content checklists) that served as the subagent
  contracts; every chapter subagent followed the CONVENTIONS.md template
  (30-second version, derived sections, What insiders know, See it yourself,
  How to use this knowledge, Connections, 8-question quiz with worked answers).
- **Content:** 59 new chapters (modules 02-12) + 11 final exams (12 questions
  each, worked answers). Total program: 82 chapters, ~266,000 words, 59 chapter
  quizzes + 12 final exams. Labs run on Linux/gcc/gdb/Python (captured real
  output, platform-marked); Windows/macOS/iOS/Android/COBOL labs are honest
  documented walkthroughs where the platform is unavailable, marked as such.
- **Site:** `tools/build_site.py` extended to a multi-module builder: modules
  02-12 render to `m02/`..`m12/` (per-module chapter pages + landing pages +
  final exams; module 01 keeps its legacy `chapters/` URLs), program grid on the
  home page, sitemap extended to 100+ URLs, header nav gains a Modules link.
  Two converter bugs found by QA and fixed: list-continuation lines swallowing
  `</details>` when no blank line preceded it (7 chapters affected), and
  repo-relative `.md` links in one chapter converted to the house plain-text
  chapter-reference convention. Full QA: 107 generated pages, tag-balance and
  link checks, 0 issues.
- **Subagent timeouts:** 16 of ~75 subagents hit their session deadline with
  reports lost but files fully written (verified by structure grep + word count
  + marker checks after each wave) — the same failure mode as session 001,
  handled the same way: verify the artifact, trust the artifact.
- **Result:** all 12 modules LIVE, committed and pushed to `main`:
  <https://srivtx.github.io/deep-roots/>.

- **Deploy note (postscript):** the first legacy Pages build of the expanded
  site errored (Jekyll choked; module 10 ch03 contains template-like `{{`
  braces); fixed by adding `.nojekyll` (raw static serving). Site verified
  live across all 12 modules (33-page URL sweep, 0 failures; module 01's
  legacy URLs untouched). A `deploy.yml` Actions workflow is also committed
  as the optional modern path if Pages is ever switched to workflow builds.

---

## Session 003 — 2026-10-13 · the deepening round: modules 13-17, the stack completed

- **Task:** the second expansion round: cover the roadmap's named frontiers —
  networking internals, file systems, virtualization/containers/cloud, browser
  engines, digital hardware — at the same first-principles, insider depth as
  modules 01-12, with quizzes, labs, and final exams; extend the site to a
  17-module program; push.
- **Agents:** main orchestrator (Super Z / GLM) + 30 chapter/exam-writing
  subagents in five waves + one retry. Main agent wrote the 5 module specs
  (READMEs with per-chapter mandatory-content checklists — the subagent
  contracts) and extended `tools/build_site.py` to a 17-module builder
  (MODULES_META/ROADMAP_MODULES for m13-m17, program grid, facts, sitemap).
- **Content:** 25 new chapters (5 per module: DMA rings/sk_buff/TCP buffers/
  epoll-io_uring/QUIC-RDMA; storage hardware/VFS/page cache/ext4 journal/CoW
  lineages/special filesystems; VT-x/EPT/virtio/containers/cloud-as-computer;
  process model/Oilpan two-collector problem/render pipeline/page memory/
  security boundary; transistors/pipelining/RTL/the cache in RTL/silicon
  economics) + 5 final exams (12 questions each, worked answers). Total
  program: 107 chapters, ~375,000 words, 84 chapter quizzes + 17 final
  exams. Labs stay runnable: ethtool/ss/strace/unshare/systemd-run/
  debugfs/fio/DevTools/iverilog-optional, with paper labs for yield and
  BDP arithmetic.
- **Site:** modules 13-17 render to m13/..m17/; 144 generated pages; program
  grid + roadmap + sitemap extended (172 URLs). README/ROADMAP updated to
  the 17-module program ledger with new word/chapter counts.
- **QA:** corrected-regex structure check over all 30 new markdown files
  (exact quiz counts: 8 per chapter, 12 per exam; matched details tags; zero
  tabs); tag-balance + internal-link validation across all 144 pages —
  0 issues.
- **Subagent timeouts:** 4 of 30 hit the session deadline with reports lost
  but files fully written on disk (13-01, 13-03, 15-04, 17-04) — verified by
  structure grep + word count, the session-002 protocol: trust the artifact.
  13-05 was retried successfully.
- **Result:** pushed to `main`, 17 modules LIVE at
  <https://srivtx.github.io/deep-roots/>.

---

## Session 004 — 2026-10-14 · the connection round: modules 18-22 + the cross-reference retrofit

- **Task:** the third expansion round, with a quality-and-connections mandate:
  (a) write the roadmap's five named frontier modules — cryptography internals,
  PL theory & type systems, embedded & firmware, formal verification, ML systems —
  at the same first-principles, insider depth; (b) audit the whole program for
  what's missing and misconnected; (c) repair the connection debt and make the
  cross-reference graph a first-class artifact of the program; (d) push.
- **Audit first (the "what's missing / correctly connect them" pass):** a new
  audit script (scripts/deep_roots_audit.py, kept outside the repo) measured the
  program's cross-reference graph: 999 module-to-module citations, all resolving —
  but **module 01 (the root) had zero outbound links** (written first, never
  updated when 21 more modules appeared), **5 chapters were orphans** (zero
  inbound: m01 ch13, ch16, ch23, m07 ch03, m13 ch03), and ROADMAP.md carried 17
  broken "Start ere]" links. All fixed this session.
- **Agents:** main orchestrator (Super Z / GLM) + 36 chapter/exam-writing
  subagents in six waves (m18, m19, m20, m21, m22, retries) — subagent-report
  losses to tool timeouts ran at the session-003 rate (~40%), same protocol:
  verify the artifact on disk, trust the artifact. m22 ch03 failed three
  subagent attempts and was written directly by the main agent.
- **Content:** 25 new chapters + 5 final exams (12 questions each, worked
  answers). m18: entropy from physics, AES/modes as state machine, bignums as
  attack surface, TLS 1.3 as key erasure, the failure catalog. m19: lambda
  calculus + halting, types as theorems (progress+preservation proved), memory
  safety as a theorem (the borrow checker as affine types, the 11-language
  spectrum table), GC as proof search, the limits. m20: the memory map in
  silicon, linker scripts as the memory model (the ASSERT as soundness
  theorem), interrupts+DMA (priority ceiling = BASEPRI), the RTOS (O(1)
  bitmap scheduler lineage), firmware security (A/B as 2-phase commit).
  m21: Hoare logic + LCF (the JVM verifier as proof checker), model
  checking (state explosion as RAM arithmetic), seL4 (100:1 economics),
  the borrow checker as separation logic, CompCert/HACL*. m22: tensors,
  the autograd tape (activation accounting), ZeRO/TP/PP (1F1B bubble
  derived), the KV cache (paged attention = virtual memory), collectives
  (ring 2(N−1)/N derived, the datacenter as the swap machine).
- **The connections layer (this session's signature):** (a) the m01 retrofit —
  22 of the root module's 23 chapters gained explicit forward links into
  modules 02-22 (65 outbound refs, was zero), and ch23's forward-connections
  section was extended from 12 to 22 modules; (b) the new modules' specs
  carried **mandatory orphan-repair links** (m18 ch04→m13 ch03, m19 ch02→m01
  ch13, m18 ch05→m01 ch16, m19 ch04→m01 ch23, m20 ch05→m07 ch03) — all five
  orphans now have inbound citations; (c) `connections.html`: the program's
  dependency matrix, strongest ties, and load-bearing chapters, **generated
  from the actual chapter markdown at build time** (1,129 citations, 157
  module pairs — the graph is data, not a claim); (d) "Map" added to the
  header/footer nav, linked from README and the hero.
- **Site:** tools/build_site.py extended to a 22-module builder
  (MODULES_META/ROADMAP_MODULES for m18-m22, computed facts, xmap/xties CSS,
  BUILD_DATE bump, title double-escape fix); 180 generated pages; sitemap
  194 URLs.
- **QA:** deep_roots_qa.py over all 180 pages + 30 new markdown files — 0
  issues; deep_roots_audit.py — 1,413 cross-module refs all resolve, 0 orphan
  chapters, all 25 new chapters carry the full template contract (8Q/8A,
  matched details, all sections), 5 new exams verified (12 Q each); headless
  render check (homepage, map, m18/m20 indexes, m18/m19/m21/m22 chapters,
  m22 exam, roadmap) — 0 console/page errors; ROADMAP's 17 broken links
  repaired.
- **Result:** pushed to `main`; 22 modules, 132 chapters, ~476,000 words,
  109 chapter quizzes + 22 final exams, LIVE at
  <https://srivtx.github.io/deep-roots/> with the connections map at
  <https://srivtx.github.io/deep-roots/connections.html>.
