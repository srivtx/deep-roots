# deep-roots chapter conventions

Every chapter in this module follows one template. The template is the contract between
the reader and the program: you always know where the labs are, where the quiz is, and
where the insider knowledge lives.

## The template

```markdown
# Chapter NN — Title

> **The 30-second version.** One paragraph, plain language, the whole chapter in
> miniature. A reader who stops here still leaves with the idea.

## <the chapter's own section flow — 3 to 6 sections, named for the argument>

The core teaching. First principles: every abstraction is *derived*, not asserted.
Code examples (short, annotated) and at least one ASCII diagram per chapter:

```
+-------------------+------------------+------------------+
| refcount (8 B)    | type ptr (8 B)   | payload ...      |   <- annotate bytes
+-------------------+------------------+------------------+
```

## What insiders know

Five or more bullets — the specifics only people inside this field use daily:
exact sizes, thresholds, flags, failure modes, folklore, the interview trap.
No generic advice. If a bullet could appear in any chapter, delete it.

## See it yourself

A lab: real commands with the flags that matter, what the output looks like, and
what to notice. The reader must be able to reproduce it on a laptop
(gdb, /proc, compiler flags, devtools, python -c ...). No screenshots required,
commands + expected output fragments only.

## How to use this knowledge

Three to five concrete payoffs: debugging scenarios, performance decisions,
design choices, interview answers, incident war stories the reader can now explain.

## Connections

Explicit links, by chapter number, to other chapters in this module and to the
queued modules in ROADMAP.md. One line each: *what* transfers.

## Quiz

Eight questions, mixed styles:
- at least 2 concept questions
- at least 2 "predict the output / the layout / the number" questions
- at least 2 bug-diagnosis questions ("why does this leak / crash / race?")
- at least 1 question that connects to another chapter

Answers go in a sibling section, hidden behind a details tag:

<details>
<summary>Answers</summary>

1. **...** — full worked explanation, not just the letter.
...
</details>
```

## Style rules

- Markdown, GitHub-flavored. Raw HTML allowed only for `<details>`/`<summary>`.
- No emojis. No filler. Every sentence either teaches, derives, or connects.
- Specific numbers are good: "8-byte header", "starts at 2 KB and grows by copying",
  "survivor ratio 8:1:1". Vague words ("fast", "large") must be followed by a number.
- Code blocks name the language. Comments in code teach.
- ASCII diagrams inside fenced code blocks, byte-level annotated where relevant.
- Truthfulness: if a number varies by platform/version, say which one you mean
  (e.g. "CPython 3.11, 64-bit", "glibc 2.35", "V8 as of 2024").
- Length: 2,500–4,000 words. Depth beats breadth, but the content checklist for the
  chapter is mandatory — everything on it must appear.

## Quiz question quality bar

A bad question: "What is the JVM?" A good question: "This 12-element Java array of
`long` lives on the heap. How many bytes does the *object* occupy in total on a 64-bit
JVM with compressed oops, and where do the 12 elements live relative to the header?"
Quizzes are for *catching false intuitions*, not for attendance.
