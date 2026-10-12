#!/usr/bin/env python3
"""deep-roots site builder: chapters + final exam + index + roadmap + sitemap.
Markdown (GFM subset) -> styled HTML, dual theme, p-rick family identity."""
import os
import re
import html as html_mod

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD = os.path.join(BASE, 'modules', '01-memory-models')
OUT = os.path.join(BASE, 'chapters')
SITE = 'https://srivtx.github.io/deep-roots'
BUILD_DATE = '2026-10-13'

PARTS = {
    1: 'Part I · the machine', 2: 'Part I · the machine', 3: 'Part I · the machine',
    4: 'Part I · the machine', 5: 'Part I · the machine', 6: 'Part I · the machine',
    7: 'Part I · the machine',
    8: 'Part II · the languages', 9: 'Part II · the languages', 10: 'Part II · the languages',
    11: 'Part II · the languages', 12: 'Part II · the languages', 13: 'Part II · the languages',
    14: 'Part II · the languages', 15: 'Part II · the languages', 16: 'Part II · the languages',
    17: 'Part II · the languages', 18: 'Part II · the languages',
    19: 'Part III · the deep end', 20: 'Part III · the deep end',
    21: 'Part III · the deep end', 22: 'Part III · the deep end',
    23: 'Part IV · the payoffs',
}

CHAPTERS = [
    (1, 'memory-from-silicon', 'DRAM cells, addresses, alignment, endianness — why memory is shaped the way it is.'),
    (2, 'the-hierarchy', 'The latency tower, the 64-byte line, locality — the physics behind all performance.'),
    (3, 'virtual-memory', 'Page tables, TLB, demand paging, COW, ASLR, swap — the OS grand illusion.'),
    (4, 'the-stack', 'Frames, prologues, calling conventions — why local variables are free.'),
    (5, 'the-heap', 'The everything-else machine: metadata, the three taxes, fragmentation.'),
    (6, 'object-layout', 'Unboxed/boxed/tagged, headers, vtables, fat pointers — what a value really is.'),
    (7, 'assembly-reference-model', 'x86-64 and ARM64 ground truth — the layer no language can hide.'),
    (8, 'c', 'The explicit model: you own it, you free it, the language promises nothing.'),
    (9, 'cpp', 'RAII: lifetime is scope; smart pointers as ownership arithmetic.'),
    (10, 'rust', 'Ownership and borrows: safety at compile time — the third way.'),
    (11, 'go', 'Escape analysis, moving goroutine stacks, the concurrent GC.'),
    (12, 'java-jvm', 'The generational JVM heap: headers, TLABs, collectors, the 32 GB cliff.'),
    (13, 'csharp-dotnet', 'Value types on a GC heap: boxing, LOH, spans.'),
    (14, 'python', 'Everything is a PyObject: refcounts, the cycle detector, pymalloc.'),
    (15, 'javascript-v8', 'Hidden classes, the scavenger, the leak-hunting heap graph.'),
    (16, 'swift-objc', 'ARC: the compiler counts; side tables, COW, no swap.'),
    (17, 'cobol', 'The memory model with no heap: PICTURE bytes, fixed records, why banking runs on it.'),
    (18, 'the-elders', 'Fortran, Lisp, Haskell, Erlang, Ada, Zig — where every idea was invented.'),
    (19, 'allocators-deep', 'dlmalloc bins, tcache, tcmalloc, jemalloc, slab — the machinery under malloc.'),
    (20, 'hardware-memory-model', 'MESI, store buffers, atomics, fences — why cores disagree about memory.'),
    (21, 'memory-security', 'Overflows, UAF, heap attacks, ROP, Rowhammer — how every model fails.'),
    (22, 'mobile-memory', 'Zygote, ART GC, Jetsam, compression — where the OS is the GC.'),
    (23, 'unification', 'One table for every language, the toolkit, the practice path.'),
]

ROADMAP_MODULES = [
    ('M02', 'Memory security &amp; exploitation', 'QUEUED', 'The dark mirror of module 01: canaries, tcache poisoning, ROP, MTE, Rowhammer — every design decision as attack surface.'),
    ('M03', 'Software bugs: the taxonomy of failure', 'QUEUED', 'The bug classes senior engineers know by scar: memory, race, numeric, state — and how fuzzing and sanitizers actually find them.'),
    ('M04', 'Decompilation &amp; reverse engineering', 'QUEUED', 'Reading what compilers leave behind: file formats from bytes up, calling conventions, vtable reconstruction, Ghidra/IDA.'),
    ('M05', 'Android internals', 'QUEUED', 'Zygote and COW, ART object model and GC, Binder, lmkd, OTA — an OS designed by memory constraints.'),
    ('M06', 'iOS internals', 'QUEUED', 'XNU VM, Jetsam bands, the compressor, Mach ports, DYLD shared cache, code signing as memory trust.'),
    ('M07', 'Desktop OS internals', 'QUEUED', 'Windows (TEB/PEB, heap manager, LFH), Linux (ELF, vdso, slab, OOM), macOS (Mach VM, shared cache) — three concrete pagetable worlds.'),
    ('M08', 'COBOL &amp; the mainframe world', 'QUEUED', 'The fixed-record universe deep dive: storage sections, PICTURE, CICS, AMODE — why the world\u2019s money runs on no heap.'),
    ('M09', 'Concurrency &amp; distributed memory', 'QUEUED', 'From the hardware memory model to happens-before at scale: lock-free, RCU, CRDTs — memory and network converge.'),
    ('M10', 'Compilers &amp; runtime internals', 'QUEUED', 'Where memory models are manufactured: register allocation, escape analysis, barrier insertion, layout choices.'),
    ('M11', 'Database storage engines', 'QUEUED', 'The disk is the new heap: B-trees, LSM, buffer pools as hierarchy, MVCC as copy-on-write.'),
    ('M12', 'GPU &amp; AI accelerator memory', 'QUEUED', 'HBM, the CUDA hierarchy, coalescing as cache lines, allocator arenas in training — a new level of the tower.'),
]

NO_FLASH = (
    "<script>(function(){try{var t=localStorage.getItem('roots-theme');"
    "if(!t){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}"
    "document.documentElement.setAttribute('data-theme',t);}"
    "catch(e){document.documentElement.setAttribute('data-theme','dark');}})();</script>"
)

HEAD = """<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta property="og:type" content="{ogtype}">
<meta property="og:site_name" content="deep-roots learning program">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{og_url}">
{og_image_block}
<link rel="icon" href="{icon}" type="image/svg+xml">
{noflash}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{css}">
"""

HEADER = """<header class="site-header">
  <a class="brand" href="{root}index.html"><span class="brand-mark">deep-roots</span><span class="brand-sub">first principles</span></a>
  <div class="header-right">
    <nav><a href="{root}index.html#module-01">Module 01</a><a href="{root}quiz.html">Quiz</a><a href="{root}roadmap.html">Roadmap</a><a href="{root}index.html#about">About</a><a href="https://github.com/srivtx/deep-roots">GitHub</a></nav>
    <button class="theme-toggle" aria-label="Toggle dark / light mode" title="Toggle dark / light mode">
      <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
      <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
    </button>
  </div>
</header>"""

FOOTER = """<footer class="site-footer">
  <div class="footer-inner">
    <div>
      <div class="f-brand">deep-roots</div>
      <div class="f-note">
        computer science from first principles &middot; the root knowledge each field keeps
        to itself &middot; memory models first, everything else after &middot;
        dark and light, like a good reading room
      </div>
    </div>
    <div class="footer-links">
      <a href="{root}index.html">Home</a>
      <a href="{root}index.html#module-01">Module 01</a>
      <a href="{root}quiz.html">Quiz</a>
      <a href="{root}roadmap.html">Roadmap</a>
      <a href="https://github.com/srivtx/deep-roots">GitHub</a>
      <a href="https://github.com/srivtx/deep-roots/blob/main/agents.md">agents.md</a>
    </div>
  </div>
</footer>
<script src="{root}assets/theme.js"></script>"""


# ---------------- markdown -> html ----------------

def esc(s):
    return html_mod.escape(s, quote=False)


def inline(s):
    s = esc(s)
    # inline code spans (protect their contents from further transforms)
    codes = []

    def stash_code(m):
        codes.append(m.group(1))
        return '\x00CODE%d\x00' % (len(codes) - 1)
    s = re.sub(r'`([^`]+)`', stash_code, s)
    s = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', r'<a href="\2">\1</a>', s)
    # bold: allow single-* italics inside; don't match exponent-style 2**31
    s = re.sub(r'(?<![\w*])\*\*(?!\s)(.+?)(?<!\s)\*\*(?!\*)', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)', r'<em>\1</em>', s)
    for i, c in enumerate(codes):
        s = s.replace('\x00CODE%d\x00' % i, '<code>' + c + '</code>')
    return s


def slugify(s):
    s = s.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s or 'section'


def is_table_sep(line):
    body = line.strip().strip('|')
    return bool(body) and all(re.match(r'^:?-{2,}:?$', c.strip()) for c in body.split('|') if c.strip())


def md_to_html(md):
    lines = md.split('\n')
    out = []
    i = 0
    n = len(lines)

    def flush_para():
        pass

    para = []

    def close_para():
        if para:
            out.append('<p>' + inline(' '.join(para)) + '</p>')
            para.clear()

    def close_list(state):
        if state['tag']:
            out.append('</%s>' % state['tag'])
            state['tag'] = None

    list_state = {'tag': None}

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # fenced code
        if stripped.startswith('```'):
            close_para()
            close_list(list_state)
            lang = stripped[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith('```'):
                buf.append(lines[i])
                i += 1
            i += 1  # closing fence
            cls = ' class="language-%s"' % esc(lang) if lang else ''
            out.append('<pre><code%s>%s</code></pre>' % (cls, esc('\n'.join(buf))))
            continue

        # raw details/summary pass-through
        if stripped.startswith('<details') or stripped.startswith('</details') or stripped.startswith('<summary'):
            close_para()
            close_list(list_state)
            out.append(line.rstrip())
            i += 1
            continue

        # blank
        if not stripped:
            close_para()
            close_list(list_state)
            i += 1
            continue

        # headings
        m = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        if m:
            close_para()
            close_list(list_state)
            level = len(m.group(1))
            text = m.group(2)
            if level == 1:
                i += 1
                continue  # page title handled by template
            hid = ' id="%s"' % slugify(text) if level == 2 else ''
            out.append('<h%d%s>%s</h%d>' % (level, hid, inline(text), level))
            i += 1
            continue

        # hr
        if re.match(r'^-{3,}$', stripped):
            close_para()
            close_list(list_state)
            out.append('<hr>')
            i += 1
            continue

        # blockquote
        if stripped.startswith('>'):
            close_para()
            close_list(list_state)
            buf = []
            while i < n and lines[i].strip().startswith('>'):
                buf.append(lines[i].strip()[1:].lstrip())
                i += 1
            out.append('<blockquote>' + inline(' '.join(buf)) + '</blockquote>')
            continue

        # table
        if stripped.startswith('|') and i + 1 < n and is_table_sep(lines[i + 1]):
            close_para()
            close_list(list_state)
            header = [c.strip() for c in stripped.strip('|').split('|')]
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith('|'):
                rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            t = '<table><thead><tr>' + ''.join('<th>%s</th>' % inline(c) for c in header) + '</tr></thead><tbody>'
            for r in rows:
                t += '<tr>' + ''.join('<td>%s</td>' % inline(c) for c in r) + '</tr>'
            t += '</tbody></table>'
            out.append('<div class="table-wrap">' + t + '</div>')
            continue

        # lists: flat items at indent 0-1; deeper-indented lines are continuations
        # (handles years like "   2004. ..." that look like ordered markers)
        m = re.match(r'^(\s{0,1})([-*]|\d+\.)\s+(.*)$', line)
        if m:
            close_para()
            tag = 'ol' if m.group(2).endswith('.') else 'ul'
            if list_state['tag'] != tag:
                close_list(list_state)
                out.append('<%s>' % tag)
                list_state['tag'] = tag
            parts = [m.group(3)]
            i += 1
            while i < n and lines[i].strip() and not re.match(r'^\s{0,1}([-*]|\d+\.)\s+', lines[i]):
                parts.append(lines[i].strip())
                i += 1
            out.append('<li>' + inline(' '.join(parts)) + '</li>')
            continue

        # paragraph
        para.append(stripped)
        i += 1

    close_para()
    close_list(list_state)
    return '\n'.join(out)


# ---------------- page templates ----------------

def page(title, desc, body, root='', ogtype='website'):
    icon = (root or '') + 'assets/favicon.svg'
    css = (root or '') + 'assets/style.css'
    og_url = SITE + '/'
    og_image_block = ('<meta property="og:image" content="%s/og-image.png">\n'
                      '<meta name="twitter:card" content="summary_large_image">\n'
                      '<meta name="twitter:title" content="%s">\n'
                      '<meta name="twitter:description" content="%s">\n'
                      '<meta name="twitter:image" content="%s/og-image.png">'
                      % (SITE, esc(title), esc(desc), SITE)) if os.path.exists(os.path.join(BASE, 'og-image.png')) else ''
    head = HEAD.format(title=esc(title), desc=esc(desc), ogtype=ogtype,
                       og_url=esc(og_url), og_image_block=og_image_block,
                       icon=icon, noflash=NO_FLASH, css=css)
    return ('<!DOCTYPE html>\n<html lang="en" data-theme="dark">\n<head>\n' + head + '</head>\n<body>\n'
            + HEADER.format(root=root) + '\n' + body + '\n' + FOOTER.format(root=root) + '\n</body>\n</html>\n')


def chapter_page(num, slug, title, part, body, prev_item, next_item, words):
    mins = max(1, round(words / 220))
    nav = ['<nav class="chapter-nav">']
    if prev_item:
        nav.append('<a class="prev" href="%s"><span class="cn-label">previous</span><span class="cn-title">%s</span></a>'
                   % (prev_item[0], esc(prev_item[1])))
    else:
        nav.append('<span class="cn-only"></span>')
    if next_item:
        nav.append('<a class="next" href="%s"><span class="cn-label">next</span><span class="cn-title">%s</span></a>'
                   % (next_item[0], esc(next_item[1])))
    else:
        nav.append('<a class="next" href="../index.html#module-01"><span class="cn-label">next</span><span class="cn-title">Module 01 index</span></a>')
    nav.append('</nav>')
    body = ('<main class="blog-main">\n<article class="post">\n'
            '<div class="post-kicker">module 01 &middot; memory models &middot; %s</div>\n'
            '<h1 class="post-title">%s</h1>\n'
            '<div class="post-meta">chapter %02d &middot; ~%d min read &middot; deep-roots learning program</div>\n'
            '%s\n'
            '%s\n'
            '<div class="post-footer"><p>This chapter is part of <a href="../index.html">Module 01 — memory models from first principles</a>. '
            'Markdown source: <a href="https://github.com/srivtx/deep-roots/blob/main/modules/01-memory-models/chapters/%02d-%s.md">chapters/%02d-%s.md</a>. '
            'Test yourself: <a href="../quiz.html">the interactive quiz</a> &middot; take the <a href="final-exam.html">final exam</a>. '
            'The work ledger is <a href="https://github.com/srivtx/deep-roots/blob/main/agents.md">agents.md</a>.</p></div>\n'
            '</article>\n</main>'
            % (esc(part), esc(title), num, mins, body, '\n'.join(nav), num, slug, num, slug))
    return page('%s — deep-roots' % title,
                'Chapter %02d of the memory-models module: %s' % (num, title),
                body, root='../', ogtype='article')


# ---------------- build ----------------

def read_md(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def build_chapters():
    os.makedirs(OUT, exist_ok=True)
    index_manifest = []
    for idx, (num, slug, oneliner) in enumerate(CHAPTERS):
        src = os.path.join(MD, 'chapters', '%02d-%s.md' % (num, slug))
        md = read_md(src)
        m = re.match(r'^#\s+(.+)$', md.split('\n')[0])
        title = m.group(1).strip() if m else 'Chapter %02d' % num
        # strip chapter prefix from title for display niceness? keep full.
        body = md_to_html(md)
        words = len(re.sub(r'```.*?```', '', md, flags=re.S).split())
        prev_item = None
        if idx > 0:
            p = CHAPTERS[idx - 1]
            prev_item = ('ch%02d.html' % p[0], 'Ch %02d' % p[0])
        next_item = None
        if idx < len(CHAPTERS) - 1:
            p = CHAPTERS[idx + 1]
            next_item = ('ch%02d.html' % p[0], 'Ch %02d' % p[0])
        prev_title = next_title = None
        # nicer: real short titles
        def short(num_, slug_, maxlen=28):
            t = 'Ch %02d' % num_
            return t
        html_page = chapter_page(num, slug, title, PARTS[num], body, prev_item, next_item, words)
        with open(os.path.join(OUT, 'ch%02d.html' % num), 'w', encoding='utf-8') as f:
            f.write(html_page)
        index_manifest.append((num, slug, title, oneliner, words))
        print('  chapter %02d -> chapters/ch%02d.html (%d words)' % (num, num, words))

    # final exam
    md = read_md(os.path.join(MD, 'quiz', 'final-exam.md'))
    body = md_to_html(md)
    exam_page = ('<main class="blog-main">\n<article class="post">\n'
                 '<div class="post-kicker">module 01 &middot; memory models &middot; final exam</div>\n'
                 '<h1 class="post-title">Module 01 final exam</h1>\n'
                 '<div class="post-meta">30 questions &middot; every chapter &middot; worked answers behind the fold</div>\n'
                 '%s\n'
                 '<div class="post-footer"><p>Prefer clicking to folding? The <a href="../quiz.html">interactive quiz</a> '
                 'scores you as you go. Chapter list: <a href="../index.html#module-01">Module 01</a>. '
                 'Source: <a href="https://github.com/srivtx/deep-roots/blob/main/modules/01-memory-models/quiz/final-exam.md">quiz/final-exam.md</a>.</p></div>\n'
                 '</article>\n</main>' % body)
    with open(os.path.join(OUT, 'final-exam.html'), 'w', encoding='utf-8') as f:
        f.write(page('Module 01 final exam — deep-roots',
                     'Thirty questions across all twenty-three chapters, with worked answers.',
                     exam_page, root='../', ogtype='article'))
    print('  final exam -> chapters/final-exam.html')
    return index_manifest


def build_index(manifest):
    facts = [
        ('23', 'chapters'), ('16', 'languages'), ('~%dk' % (sum(w for _, _, _, _, w in manifest) // 1000), 'words'),
        ('30', 'final exam'),
    ]
    facts_html = ''.join('<div class="fact"><div class="n">%s</div><div class="l">%s</div></div>' % f for f in facts)

    part_intros = {
        'Part I · the machine': 'Where every abstraction is derived, not asserted: the physics, the tower, the illusion, and the two machines every language divides labor between. Read in order — the rest of the module is this argument.',
        'Part II · the languages': 'Sixteen memory models, each positioned by the four questions: what is a value, who owns it, who frees it, what safety. From C\u2019s explicit contract to COBOL\u2019s radical alternative.',
        'Part III · the deep end': 'The machinery under malloc, the physics of multicore disagreement, the security consequences of every design decision, and the platforms that made memory a scheduled resource.',
        'Part IV · the payoffs': 'One table for every language, the instrument for every question, the practice path, and the exam.',
    }

    sections = []
    parts_order = ['Part I · the machine', 'Part II · the languages', 'Part III · the deep end', 'Part IV · the payoffs']
    current_part = None
    rows = []
    arcs = []
    for (num, slug, title, oneliner, words) in manifest:
        part = PARTS[num]
        if part != current_part:
            if rows:
                arcs.append(rows)
                rows = []
            current_part = part
            sections.append((part, part_intros[part]))
        display = re.sub(r'^Chapter \d+ — ', '', title)
        rows.append('<div class="chapter-row">'
                    '<div class="cr-num">%02d</div>'
                    '<div class="cr-body"><h3><a href="chapters/ch%02d.html">%s</a></h3><p>%s</p></div>'
                    '<div class="cr-arrow">&rarr;</div></div>' % (num, num, esc(display), esc(oneliner)))
    if rows:
        arcs.append(rows)

    module_html = ''
    for si, (part, intro) in enumerate(sections):
        module_html += '<div class="arc-label">%s</div>\n<p class="section-intro" style="margin-bottom:20px">%s</p>\n' % (esc(part), esc(intro))
        module_html += '<div class="chapter-list">\n' + '\n'.join(arcs[si]) + '\n</div>\n'
    # final exam row
    module_html += ('<div class="arc-label">the exam</div>\n<div class="chapter-list">\n'
                    '<div class="chapter-row"><div class="cr-num">fin</div>'
                    '<div class="cr-body"><h3><a href="chapters/final-exam.html">Module 01 final exam</a></h3>'
                    '<p>Thirty questions across all twenty-three chapters, worked answers behind the fold — or take the <a href="quiz.html">interactive quiz</a>.</p></div>'
                    '<div class="cr-arrow">&rarr;</div></div>\n</div>\n')

    roadmap_cards = ''.join(
        '<article class="module-card%s"><span class="mc-id">%s</span><h3>%s</h3><p>%s</p>'
        '<span class="mc-status">%s</span></article>'
        % (' live' if mid == 'M02' else '', mid, name, desc, status)
        for mid, name, status, desc in ROADMAP_MODULES)

    body = """<section class="hero">
  <div class="kicker">a learning program, from first principles</div>
  <h1>Computer science from the <em>roots</em>, upward.</h1>
  <p class="lede">
    Every technical field has a root layer its practitioners know and everyone else waves away.
    deep-roots goes down and stays there: each module rebuilds one field from the physics up,
    so that what others memorize, we <strong>derive</strong>. Module 01 — live now — is the root of
    them all: the memory models of programming languages, from silicon to GC, C to COBOL,
    stack frames to heap exploits. <strong>What insiders know, written down.</strong>
  </p>
  <div class="hero-facts">__FACTS__</div>
</section>

<section class="section" id="module-01">
  <div class="section-head"><h2>Module 01 — memory models</h2><div class="rule"></div></div>
  <p class="section-intro">
    Twenty-three chapters in four parts. Every chapter ends with <em>What insiders know</em>,
    a <em>See it yourself</em> lab with real commands, the connections to every other chapter,
    and a quiz with worked answers. The whole module lands on one comparison table: sixteen
    languages, four questions, sixty years of design decisions.
  </p>
  __MODULE__
</section>

<section class="section" id="roadmap">
  <div class="section-head"><h2>The roadmap</h2><div class="rule"></div></div>
  <p class="section-intro">
    Memory first because everything else is a consequence: security bugs are memory bugs,
    decompilation reads stack frames and vtables, mobile OSes are memory-driven schedulers,
    COBOL is a memory model, distributed consistency is the memory model at scale.
    Eleven more modules are specified on <a href="roadmap.html">the roadmap page</a>.
  </p>
  <div class="module-grid">
__ROADMAP__
  </div>
</section>

<section class="section" id="method">
  <div class="section-head"><h2>The method</h2><div class="rule"></div></div>
  <div class="about-body">
    <p><strong>First principles, downward.</strong> Each module starts at the physical layer and builds
    up — no fact is allowed to be memorized if it can be derived. Where a number matters, the
    number is stated and the platform named; where a simplification is made, it is flagged.</p>
    <p><strong>The insider test.</strong> Every chapter has a <em>What insiders know</em> section — the
    sizes, thresholds, flags, failure modes, and folklore that people inside the field use daily.
    <strong>See it yourself</strong> labs make every claim reproducible on a laptop: /proc, gdb,
    perf, ASan, heap snapshots, dumpsys. Research that nobody can check is a diary.</p>
    <p><strong>Quizzes with teeth, connections or it didn't happen.</strong> Questions predict
    output, diagnose bugs, and attack false intuitions — with worked answers. Every chapter
    ends by naming exactly what transfers to the other chapters and to the queued modules:
    one root, twelve trees.</p>
  </div>
</section>

<section class="section" id="about">
  <div class="section-head"><h2>About</h2><div class="rule"></div></div>
  <div class="about-body">
    <p>
      <strong>deep-roots is a learning program, not a tutorial dump.</strong> It is the education
      program of the srivtx line of work — a sibling of
      <a href="https://github.com/srivtx/p-rick">srivtx/p-rick</a>, the research program on
      missing software. Where p-rick specifies software the world lacks, deep-roots rebuilds
      the knowledge most engineers lack. Both run the same discipline: work tracked in an
      automatic session ledger (<a href="https://github.com/srivtx/deep-roots/blob/main/agents.md">agents.md</a>),
      sources open, claims checkable.
    </p>
    <p>
      Everything here is open: markdown sources, this site (GitHub Pages, dark and light),
      the <a href="quiz.html">interactive quiz</a>, and the
      <a href="https://github.com/srivtx/deep-roots/blob/main/ROADMAP.md">full roadmap</a>.
      MIT-licensed — use it, teach it, fork it.
    </p>
  </div>
</section>"""

    body = (body.replace('__FACTS__', facts_html)
                .replace('__MODULE__', module_html)
                .replace('__ROADMAP__', roadmap_cards))
    with open(os.path.join(BASE, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(page('deep-roots — computer science from first principles',
                     'The deep root knowledge each field of computer science keeps to itself, '
                     'rebuilt from first principles. Module 01: the memory models of programming '
                     'languages — 23 chapters, 16 languages, labs, quizzes, one final exam.',
                     body))
    print('  index.html')


def build_roadmap():
    md = read_md(os.path.join(BASE, 'ROADMAP.md'))
    body = md_to_html(md)
    pagebody = ('<main class="page-main">\n<h1 class="page-title">The deep-roots roadmap</h1>\n'
                '<p class="page-sub">One program: go to the root layer of every field of computer science, '
                'from first principles, and stay there until the field\u2019s insiders would nod. '
                'Modules are built in dependency order — each one is a root of the ones after it.</p>\n'
                + body + '\n</main>')
    with open(os.path.join(BASE, 'roadmap.html'), 'w', encoding='utf-8') as f:
        f.write(page('The roadmap — deep-roots',
                     'Twelve modules from memory models to GPU memory: the build order and why each module is a consequence of the one before it.',
                     pagebody))
    print('  roadmap.html')


def build_misc():
    urls = [SITE + '/', SITE + '/index.html', SITE + '/quiz.html', SITE + '/roadmap.html',
            SITE + '/chapters/final-exam.html']
    for (num, slug, _oneliner) in CHAPTERS:
        urls.append(SITE + '/chapters/ch%02d.html' % num)
    urls = sorted(set(urls))
    today = BUILD_DATE
    sm = ('<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
    for u in urls:
        sm += '<url><loc>%s</loc><lastmod>%s</lastmod></url>\n' % (u, today)
    sm += '</urlset>\n'
    with open(os.path.join(BASE, 'sitemap.xml'), 'w', encoding='utf-8') as f:
        f.write(sm)
    with open(os.path.join(BASE, 'robots.txt'), 'w', encoding='utf-8') as f:
        f.write('User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n' % SITE)
    # 404
    nf = ('<main class="page-main" style="text-align:center;padding-top:120px">\n'
          '<h1 class="page-title">404</h1>\n'
          '<p class="page-sub" style="margin:0 auto 30px">This page faulted with no story the kernel was willing to tell (Chapter 03, we know).</p>\n'
          '<p><a href="index.html" style="font-weight:600">Return to the roots</a></p>\n</main>')
    with open(os.path.join(BASE, '404.html'), 'w', encoding='utf-8') as f:
        f.write(page('404 — deep-roots', 'Page not found', nf))
    print('  sitemap.xml, robots.txt, 404.html')


def build_og():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as e:
        print('  og-image skipped (%s)' % e)
        return
    W, H = 1200, 630
    img = Image.new('RGB', (W, H), (22, 32, 50))
    d = ImageDraw.Draw(img)
    gold = (179, 163, 120)
    cream = (230, 236, 242)
    muted = (136, 152, 168)
    try:
        f_big = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf', 86)
        f_it = ImageFont.truetype('/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf', 86)
        f_mid = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 30)
        f_small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 22)
    except Exception as e:
        print('  og-image skipped (fonts: %s)' % e)
        return
    # root glyph: line + branches
    d.line([(W // 2, 96), (W // 2, 200)], fill=gold, width=6)
    for dx in (-90, 90):
        d.line([(W // 2, 150), (W // 2 + dx, 236)], fill=gold, width=5)
    d.ellipse([W // 2 - 14, 82, W // 2 + 14, 110], fill=gold)
    d.text((W // 2, 130), 'deep-roots', font=f_big, fill=cream, anchor='ma')
    d.text((W // 2, 250), 'computer science', font=f_it, fill=gold, anchor='ma')
    d.text((W // 2, 320), 'from first principles', font=f_it, fill=gold, anchor='ma')
    d.text((W // 2, 430), 'module 01 · memory models of programming languages', font=f_mid, fill=muted, anchor='ma')
    d.text((W // 2, 484), '23 chapters · 16 languages · labs · quizzes · final exam', font=f_small, fill=muted, anchor='ma')
    d.line([(80, 560), (W - 80, 560)], fill=(139, 126, 90), width=2)
    d.text((W // 2, 580), 'srivtx.github.io/deep-roots', font=f_small, fill=muted, anchor='ma')
    img.save(os.path.join(BASE, 'og-image.png'))
    print('  og-image.png')


def main():
    print('building deep-roots site ->', BASE)
    manifest = build_chapters()
    build_index(manifest)
    build_roadmap()
    build_misc()
    build_og()
    total_words = sum(w for _, _, _, _, w in manifest)
    print('DONE: %d chapters, %d words total' % (len(manifest), total_words))


if __name__ == '__main__':
    main()
