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
