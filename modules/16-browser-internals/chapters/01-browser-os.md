# Chapter 01 — The browser as an operating system

> **The 30-second version.** A tab runs code written by strangers, delivered over a network, with no install step — an operating system's job description, minus the honesty. Chrome's 2008 answer was to build a process topology instead of a better program: one privileged broker (the browser process) owning the screen, the disk, and the sockets; many unprivileged renderers, each holding one site's DOM and JavaScript behind a seccomp/seatbelt sandbox; a GPU process quarantined because kernel drivers cannot be fixed; and Mojo, a capability-passing IPC fabric where "who may talk to whom" is a question of handle ownership, not policy. The cost is arithmetic: every renderer pays a ~30-50 MB private floor (2024-era desktop Chrome, about:blank), so 40 tabs of distinct sites bill roughly 2 GB. Site isolation (Chrome 67-77, the Spectre response) moved the boundary from tab to site for a further 10-20% RAM tax. The RAM complaints are the sandbox's invoice.

## One crash kills the kingdom: the single-process era as an OS history lesson

Start with what a browser was in 2006: one C++ process. Netscape's descendants (Firefox through the mid-2010s), IE6/IE7, Opera — every tab was a thread in one address space, and NPAPI plug-ins like Flash ran *in-process* too. That design is legitimate if your components are correct. Derive what happens when they are not: a process is the unit of *fault isolation* (an address space that dies together), a thread is not. One heap corruption in tab 38 — a freed `nsIContent` walked later, a plug-in writing past a buffer — takes down all 40 tabs and the session, because the failure modes of a shared address space are *unioned*.

Put a number on it. Say each tab, over a long browsing session, has a 1% independent chance of hitting a fatal bug (browser-style crash: heap corruption, plug-in fault, third-party code). With 40 tabs sharing one address space, the probability the *session* dies is the probability that *at least one* tab hits its bug:

```text
P(session dies) = 1 - (1 - 0.01)^40 = 1 - 0.669 = 0.331  ~ 33%
```

One process turns forty small risks into one coin-flip-shaped risk. This, not a marketing slide, is the 2006-2008 pain that motivated Chrome's launch design: the September 2008 comic and design docs framed the browser as multi-process *from version 1* (September 2, 2008). IE8 (2009) split the frame process from tab processes, though tabs shared processes above a cap; Firefox held out until the Electrolysis rollout of 2016-2017. The OS analogy is earned, not asserted: a browser needs a scheduler (the event loop, chapter 03), allocators (three heaps per page — chapters 02 and 04), device drivers (the network stack, the GPU), and the one thing 2006 browsers lacked — a *kernel/user boundary* between trusted core and untrusted payload. That boundary is a process boundary, and the rest of this chapter is its price list.

## Fault isolation, priced: the 40-tab arithmetic

Process-per-tab (2008) buys the 33%-vs-1% win with the most honest currency in systems: address space. A second process duplicates the *state* even where it shares the *bytes*. Re-derive the floor from module 01 chapter 03's page-fault machinery:

- Code pages (Blink, V8, Skia, ICU's data tables) are file-backed and mapped copy-on-write. Forty renderers mapping the same shared library text share one physical copy via the page cache. So code is not the multiplier.
- But every mapping carries per-process *page tables*, and every renderer dirties its own pages: COW faults on writable data, per-process allocator metadata (V8 heap page headers, PartitionAlloc buckets), its own thread stacks, its own ICU/Skia lazily-initialized state. Dirty pages are private by definition.

Measure it and you get the floor: a minimal renderer — one showing `about:blank` — costs ~30-50 MB private memory on 2024-era 64-bit desktop Chrome (Linux/Windows; the figure moves a few MB per release). That is the price of entry before the page does anything — now the headline arithmetic:

```text
40 tabs, all distinct sites, minimal renderers:
  40 x ~40 MB (renderers)      = 1600 MB
  + ~200 MB  (browser process) ~ 
  + ~100 MB  (GPU process)     ~  1.9-2.0 GB total
  + ~40 MB   (network service) ~
```

"Chrome eats RAM" is true and correct: the memory is the sandbox's bill, itemized exactly like an OS's — fault isolation costs address space. The floor does *not* include the page's data (JS heap, DOM, decoded images), which rides on top and is chapter 04's subject. And the sharing that saves you: 40 tabs of the *same* site cost less than 40 distinct sites, because same-site renderers can be consolidated (under memory pressure, mostly on Android) and the file-backed code is already shared.

## The boundary moves again: from tab to site (2018-2019)

Process-per-tab has a hole you can state in one sentence: the sandbox isolates *syscalls*, not *speculation*. Module 02 chapter 07 derived Spectre: a renderer that shares one address space with a cross-site document can mount cache-timing side-channel reads against that document's data, from inside the sandbox, without one syscall being out of policy. The fix is not a faster sandbox — it is never letting cross-site documents share an address space at all, *even inside one tab*. That is site isolation, and it changed the boundary's unit:

- **Chrome 67 (June 2018, desktop):** full site isolation — cross-site documents get different renderer processes, including in the same tab.
- **Chrome 77 (September 2019, Android):** broader rollout, but gated on device RAM — low-RAM phones still get only partial isolation (a curated list of high-value sites), because the RAM tax is regressive on small devices.

The unit is the **site-instance**, not the tab and not the origin. A *site* is scheme plus eTLD+1 (the registrable domain): `mail.google.com` and `accounts.google.com` are the same site and will share a renderer; `mail.google.com` and `evil.example` are different sites and will not, even when the second is a 300-pixel iframe inside the first. An iframe whose content lives in another process is an **OOPIF** — out-of-process iframe — and post-2018 a busy page is a small distributed system: the frame tree spans processes, coordinated through the browser process. The cost was measured, not guessed: ~10-20% more total memory for typical browsing in Google's 2018 published numbers, plus more processes per tab (the "one Gmail tab = 6 processes" decode is below). Chapter 05 pays off that bill; here it is only booked honestly: the Spectre response cost 10-20% of the RAM budget, and it was worth it.

## The process zoo: least privilege drawn as a topology

Now draw the machine this argument produces. Least privilege stops being a checklist and becomes a topology: every power gets its own crash domain, and the arrows are the whole security policy.

```text
                THE BROWSER PROCESS (the broker: pid 1 of the family)
                powers: windowing/UI, disk (profile, cache), privilege.
                The only process allowed to say "yes". Its death = everything.
               +---------------------------------------------------+
               |  UI + tab strip + omnibox | policy + IPC umpiring |
               +----+-----------------+-------------------+--------+
                    |                 |                   |
       Mojo pipe    |    Mojo pipe    |    Mojo pipe      |  (an arrow is a
       (brokered    |   (brokered)    |   (brokered)      |   capability: its
        requests    |                 |                   |   endpoint must be
        only)       |                 |                   |   HANDED to you)
                    v                 v                   v
    +---------------------+  +----------------------+  +----------------------+
    | RENDERER            |  | RENDERER             |  | GPU PROCESS          |
    | site-a.com          |  | site-b.com           |  | sandboxed, but the   |
    | DOM + V8 + style +  |  | DOM + V8 + style +   |  | ONLY process that    |
    | layout. Seccomp:    |  | layout. Seccomp:     |  | talks to the kernel  |
    | no disk, no socket, |  | no disk, no socket,  |  | GPU driver. Death =  |
    | no fork. Dies =     |  | no fork. Dies =      |  | black flash, then    |
    | one "Aw, Snap" tab. |  | one "Aw, Snap" tab.  |  | auto-restart.        |
    +----------+----------+  +----------------------+  +-----------^----------+
               |                                                  |
               | rasterized tiles as SHARED-MEMORY buffers        |
               | (zero-copy: ~14 MB per 2560x1440 RGBA tile,      |
               |  never a Mojo message payload)                   |
               +--------------------------------------------------+
    +---------------------+  +--------------------------+  +-------------------+
    | NETWORK SERVICE     |  | UTILITY PROCESSES        |  | EXTENSION process |
    | own process, own    |  | (audio, data decoding,   |  | user-consented,   |
    | seccomp profile;    |  | storage...): one power = |  | broad chrome.*    |
    | renderers reach the |  | one process = one seccomp|  | APIs. A DIFFERENT |
    | network only via    |  | profile                  |  | threat model.     |
    | pipes they were     |  +--------------------------+  +-------------------+
    | handed              |
    +---------------------+
```

Walk the zoo, because each box exists for a reason you can now derive:

**The browser process** is module 15 chapter 04's broker at web scale. It owns the powers — windowing, the profile on disk, process creation — and every privileged request from below must cross it. On Linux it applies the sandbox to children (seccomp-BPF plus namespaces); on Windows, restricted tokens and job objects; on macOS, Seatbelt profiles. It is also a single point of failure, which is why Chromium's *service-ification* project (2017-2019) has been peeling powers out of it.

**Renderer processes** are the untrusted payload: the DOM, V8, style, layout — everything derived from network bytes. The sandbox's promise is stated in the negative: no disk, no sockets, no `fork()`. A renderer exploit buys the attacker a sandboxed process and nothing else; escaping needs a second bug (chapter 05's rule).

**The GPU process** exists because of one unsolvable fact: kernel GPU drivers are not securable. They were not written to face hostile input, and two decades of driver CVEs plus browser telemetry (Windows display drivers sat in top crash buckets for years) say they will not be fixed. You cannot sandbox your way out of a kernel bug, so the unfixable code gets its own crash domain and the renderers send it commands over IPC. Separate process since roughly 2011 (Chrome 10-era).

**Utility and service processes** are the service-ification split: the network service became its own process in the 2018 era (Chrome 66-70), and audio, storage, and data decoding followed. The pattern is "least privilege as topology": each power is a process with its own seccomp profile, so compromising the audio service buys you... audio.

**Extension processes** are renderers with a different threat model. They are still sandboxed-ish processes, but they hold `chrome.tabs`, `webRequest`, and friends — capabilities no web page gets — *by user consent*. The threat is not sandbox escape; it is the user installing the attacker. Manifest V3 (rolled out 2021-2023) exists to shrink exactly that API surface.

## Mojo: capability pipes, zero-copy bytes

The arrows in the diagram are Mojo, Chromium's IPC fabric (introduced 2014, dominant by 2017, and as of 2024 essentially all cross-process Chrome IPC). Two ideas, both derived from module 13 chapter 04 and module 15 chapter 04.

**Idea one: a pipe endpoint is a capability.** Mojo's primitive is the *message pipe* — two endpoints, messages flow between them. Endpoints are handles, and handles move only by being *sent in a message* or *handed out at process creation*. You can only talk to someone if someone gave you their pipe. That is the object-capability model in production: policy ("may renderer R use the network?") is replaced by possession (R holds a `URLLoaderFactory` pipe scoped to R's origin, or it holds nothing). The native ancestor is `SCM_RIGHTS` FD passing over a Unix socket — module 13 ch04 — where a capability is a kernel-table reference you can obtain only by transfer, never by naming. Interfaces are declared in an IDL:

```mojom
// shape trimmed from Chromium's network.mojom (illustrative, not verbatim)
module network.mojom;

interface URLLoader {
  // A renderer holds one of these per request it is allowed to make.
  // The pipe itself IS the permission: no pipe, no network.
  FollowRedirect(uint32 remotes_count);
  SetPriority(RequestPriority priority, int32 intra_priority_value);
};

interface URLLoaderFactory {
  // The browser process hands a factory to a renderer, scoped to that
  // renderer's site; what it mints is only what the policy allowed.
  CreateLoaderAndStart(pending_receiver<URLLoader> loader,
                       int32 request_id, uint32 options,
                       UrlRequest request,
                       pending_remote<URLLoaderClient> client);
};
```

**Idea two: messages are for control, shared memory is for payload.** The compositor hot path would be absurd over message copies: one 2560x1440 RGBA tile is ~14 MB, and at 60 fps you would be memcpy'ing hundreds of MB per second just to hand pixels to the GPU process. Instead the raster result lives in a shared-memory or native buffer (`SharedImage`/`GpuMemoryBuffer` in 2024-era Chrome), and the Mojo message carries only the *handle* — module 13 ch04's zero-copy idea, applied inside one machine. In modern Chrome raster work usually executes inside the GPU process itself; the tile buffers still cross as shared mappings, never as payload.

One more guarantee rides the same boundary: **one V8 isolate per renderer (at minimum)**. Each renderer's JavaScript heaps are separated *by the process boundary* — the guarantee module 15 gave virtual machines, delivered at JavaScript-object granularity. A page never shares an isolate with another site's page; within one page, workers get their own isolates as threads (chapter 04).

## Anatomy of the 30-50 MB floor

Decompose the floor you priced, because Chrome's Task Manager (Shift-Esc) shows you these pieces. A renderer's memory has two shelves:

- **File-backed, physically shared** (mapped COW from disk): Blink (DOM/layout/style code), V8's engine code, Skia (raster), ICU's data tables (tens of MB on disk, mmapped lazily). Forty renderers share one physical copy through the page cache. Task Manager counts this *proportionally* (a fraction per process), which is why the "Memory footprint" column (2024-era naming; older builds split "Memory"/"Private") does not sum naively across rows.
- **Private, per-process** (the floor): page tables and COW'd data pages, V8 heap metadata and stacks, the renderer's own allocator arenas, thread stacks, per-heap initialization state.

On top of the floor rides the *page's* data — the JS heap, the DOM tree (chapter 02's Oilpan heap), style and layout structures, decoded images, compositor layers — which is why a Gmail renderer reads ~150-300 MB in the Task Manager while an `about:blank` renderer reads ~20-50 MB on the same 2024-era build. The lab below makes you measure both.

## What insiders know

- **The crash taxonomy, decoded from symptoms.** Renderer death: one "Aw, Snap" sad tab, reload spawns a fresh renderer. Browser process death: everything, session restore on relaunch. GPU process death: the screen flashes black or freezes for a fraction of a second, then *comes back without reloading any tab* — the renderers never died; the browser respawns the GPU process and re-submits. That is the entire answer to "why did my screen go black and come back."
- **The "one Gmail tab = 6 processes" decode.** Browser + GPU + network service + the tab's renderer (`mail.google.com`) + one or two OOPIF renderers for cross-site iframes (embedded video, sign-in frames) + an extension process (password manager, blocker). Six is normal; it is site isolation working, not a leak.
- **The honest RAM defense.** Fault isolation costs address space; process count is the bill. ~40 MB private per minimal renderer times tab count, plus 10-20% for site isolation (Google's 2018 measurements). Anyone can re-derive the 40-tab ~2 GB figure with a task manager.
- **The Zygote trade (Linux).** Chrome keeps a pre-warmed, pre-sandboxed template process that forks renderers on demand — process creation without re-exec, sandbox applied *before* the first fork so every child inherits it. The cost: `fork()` inherits the zygote's address-space layout, so all renderers in a family share library addresses — one info leak defeats ASLR family-wide. The mitigation history is per-fork re-randomization of what the browser controls (V8 cage and PartitionAlloc region seeds; fresh namespaces), while inherited library layout remains a documented Linux-specific trade. Windows has no `fork()` — each renderer is `CreateProcess`d, code shared via the page cache; macOS uses `posix_spawn` plus a Seatbelt profile. Neither has a zygote.
- **`--process-per-site` and `--single-process` exist as flags** — QA and memory-constrained Android builds use them — and each drops a guarantee: `--process-per-site` merges all instances of a site into one renderer (fault isolation and JS coherence now span tabs); `--single-process` puts the broker and the payload in one address space (never ship).
- **Android's partial isolation.** Low-RAM devices get site isolation only for a curated allowlist of high-value sites; a "same renderer despite cross-site iframe" report on a phone may be policy, not a bug.
- **The interview trap.** "Chrome uses process-per-tab" was true for 2008-2017 and is wrong after: the unit is the *site-instance* (site plus frame-tree context), and same-site instances may be consolidated under memory pressure. Saying "tab" where you mean "site" dates you to before Spectre.

## See it yourself
Everything below runs on any 2024-era Chrome/Chromium desktop build. Ten minutes, no screenshots — read the numbers off the tool.

1. **The zoo from the outside (bash, any Linux box):**

```bash
pgrep -c chrome          # process count, one number
# or the classic form without pgrep's process-name matching:
ps aux | grep -c [c]hrome   # the [] stops grep from counting itself
```

With ~5 real tabs expect roughly 10-20 processes on desktop (2024-era Chrome, Linux). The count exceeds tabs+1 because of the GPU, network, and utility boxes in the diagram.

2. **The process tree, named (type it in the omnibox):** open `chrome://process-internals`. Each row names its type: `Browser`, `GPU Process`, `Network Service`, `Renderer`, `Utility`, `Extension`. Match rows to the diagram, one per box.

3. **The sandbox, per process:** open `chrome://sandbox` (Linux). Look for per-process lines of the shape `Renderer Sandbox: Layer 1 Seccomp-BPF` — layer 1 is the syscall filter (module 15 ch04's machinery, live). The browser process itself shows no seccomp layer: the broker must keep its powers.

4. **The floor vs the page (the best 2 minutes here):** press `Shift-Esc` (Chrome's Task Manager). Open a tab showing `about:blank` and note its renderer row: ~20-50 MB footprint. Now open `https://mail.google.com` (or any heavy site) and find its renderer: ~150-300 MB. The difference is the page's three heaps (JS, DOM, images/layers) riding on the ~40 MB floor. Sort by the memory column; note the `Extension` rows — user-consented capabilities, not leaks.

5. **The crash taxonomy, safely:** kill a *renderer* from the same Task Manager (select its row, "End process") — one sad tab, other tabs untouched. That is fault isolation as a user-level experiment: 1 tab, not 33% of your session.

## How to use this knowledge

- **Triage a field report in one question.** "Everything went black for a second then came back, no reload" = GPU process restart; "the tab showed a sad face" = renderer; "the whole browser vanished" = browser process. You now know which log (`chrome://gpu`, `chrome://crashes`) to pull.
- **Size automation fleets.** Headless Chrome running N isolated contexts (Playwright/Puppeteer) costs roughly the renderer floor times context count plus the fixed zoo: budget ~50-100 MB per context before page data (2024-era, order-of-magnitude), and your forecasts stop surprising you.
- **Explain the laptop fan.** 40 tabs of 40 distinct sites is ~2 GB by floor arithmetic alone; telling a user "close duplicate sites, not duplicate tabs" is now derived — consolidation and shared code pages make same-site duplicates cheaper.
- **Read a Chrome CVE like a native.** The first thing a write-up tells you is the process the bug lives in. Renderer bug: sandbox escape still needed (chapter 05's two-bug rule). Browser-process bug: game over, that's the broker. GPU process: driver-adjacent crash domain.
- **Win the design argument.** Anyone proposing "let's put the untrusted parser in a thread of the trusted process" can be answered with the 2006 arithmetic: `1-(1-c)^N` turns N small failure probabilities into one large one.

## Connections

- **Module 16 ch02** — the DOM/Oilpan heap that lives inside the renderers introduced here; the two-collector problem is a *within-one-box* story.
- **Module 16 ch03** — the 16.6 ms/60 fps pipeline whose tile traffic crosses the shared-memory arrow drawn in this chapter's diagram.
- **Module 16 ch04** — the per-page memory anatomy (three heaps, workers, WASM) that rides on top of the ~40 MB floor priced here.
- **Module 16 ch05** — why site isolation's 10-20% RAM tax was worth paying: SOP as isolation policy and the two-bug escape rule.
- **Module 01 ch03** — page faults and COW: the machinery behind "code is shared, dirty pages are private."
- **Module 01 ch15** — V8's heap: the isolate-per-renderer is that heap's process home.
- **Module 02 ch07** — Spectre: the side channel that moved the boundary from tab to site in Chrome 67.
- **Module 13 ch04** — epoll/IPC patterns: `SCM_RIGHTS` FD passing and zero-copy shared buffers are Mojo's direct ancestors.
- **Module 15 ch04** — seccomp and capabilities: the broker model you just saw live in `chrome://sandbox`.

## Quiz

1. A 2007 single-process browser holds 40 tabs. Over a long session, each tab independently has a 1% chance of hitting a fatal bug. Compute the probability the whole session dies, and the same probability after moving to process-per-tab.
2. One tab shows `site1.com`, embedding two cross-site iframes: `ads.net` and `pay.example`. A second tab shows `site2.com`, embedding `ads.net` again (same site as the first tab's ad iframe). Under full desktop site isolation (Chrome 67+ behavior), how many *renderer* processes are involved at minimum, and why is the answer not 5?
3. Arithmetic check: 40 tabs of 40 distinct sites, each minimal renderer ~40 MB private, browser process ~200 MB, GPU ~100 MB, network service ~40 MB. What is the total, and which term dominates? What changes if the 40 tabs are all the *same* site?
4. Why did Chrome put GPU work in its own process rather than letting seccomp'd renderers call the kernel GPU driver directly? Name the property of the driver that forces this design.
5. A user reports: "the screen went black for about half a second, then everything came back — none of my tabs reloaded." Which process died, and why did no tab reload?
6. On 2024 desktop Chrome with default settings, an engineer finds an iframe sharing a renderer with the top-level page: the top-level page is `mail.google.com`, the iframe is `accounts.google.com`. Is site isolation broken? Answer precisely.
7. A QA rig launches Chrome with `--process-per-site` to "save memory" for a 60-tab soak test across 12 sites. Mid-run, a crash in one tab takes out several other tabs, and two tabs' `localStorage` writes interleave. What guarantee did the flag drop, and what is the correct unit it violated?
8. Cross-chapter: in Mojo, a renderer can only talk to the network service if the browser process handed it a `URLLoaderFactory` pipe endpoint. Which mechanism from module 13 ch04 is the direct native ancestor of this "possession, not policy" rule, and why does it make reviewing a new IPC interface mostly a question of "who gets handed this endpoint?"

<details>
<summary>Answers</summary>

1. **~33% vs ~1%.** Single process: the session survives only if *no* tab hits its bug: `P = (0.99)^40 = 0.669`, so `P(dies) = 0.331`. Process-per-tab: each tab's 1% is contained — a tab crash costs one reload, and the session dies only with the browser process itself. That containment is the 2008 design: fault probabilities no longer *union* — `1-(1-c)^N` grows toward 1 with N; c stays c.
2. **4.** The naive count is 5 (two pages plus three iframes, one process each). The correct minimum is 4, and the discount comes from the *second tab's* `ads.net` iframe: site isolation keys processes on the **site**, not on the frame and not on the tab, so the two `ads.net` iframes — same site, different tabs — may share one renderer (Chrome does reuse same-site renderers). The four processes: `site1.com`, `site2.com`, `pay.example`, and one shared `ads.net`. Frames do not each get a process; sites do.
3. **~1.9-2.0 GB; the renderer term dominates (1600 of 1940 MB, ~82%).** `40 x 40 = 1600`, plus 200 + 100 + 40 = 1940 MB. The renderer floor is the fault-isolation bill, and it scales linearly with distinct sites. All same site: renderers may be consolidated (under memory pressure, mostly on Android), so the count drops toward a handful and the total toward a few hundred MB — the file-backed code was already shared. Same-site duplicates are cheap; distinct sites are what you pay for.
4. **The kernel driver cannot be fixed, so it must be quarantined.** Seccomp filters a process's *syscalls*, but a renderer legitimately needs GPU ioctls to render — and those ioctls run kernel code that was never written to face hostile arguments (two decades of driver CVEs; Windows display drivers were top browser-crash buckets for years). A sandbox cannot repair a kernel bug. So the design moves the unfixable code into its own crash domain: renderers proxy commands to the GPU process, and a driver exploit or driver crash kills one sandboxed GPU process that the browser respawns — never the renderers, never the broker.
5. **The GPU process died; the renderers never did.** Compositing (turning raster results into screen frames) is downstream of the GPU process, so its death shows as dropped/black frames until the browser process respawns it and re-submits. A tab *reload* is what happens when its renderer dies — but the renderers and their DOM/JS state survived untouched, so no navigation occurred. "Black flash, no reload" is the signature of a GPU-process crash.
6. **No — isolation is per-site, and these are the same site.** A *site* is scheme plus eTLD+1 (the registrable domain): `mail.google.com` and `accounts.google.com` both bottom out at `google.com`, so site isolation deliberately *allows* them to share a renderer. The unit is the site-instance, not the origin. (Finding `evil.example` in there would be a bug — or a low-RAM Android device on the partial-isolation allowlist, or a policy/flag disabling isolation.)
7. **The flag dropped per-instance fault and state isolation; the violated unit is the site-instance.** `--process-per-site` reuses one renderer for *all* instances of a site: all 5 tabs of `site7.com` share one address space, so one crash kills them together (the 2006 union-of-risks problem, reintroduced per-site), and their `localStorage`/JS state share one process's heap and event loop, so writes interleave across tabs. Site isolation's contract is a process per site-*instance* (site plus frame-tree context); consolidation is only an explicitly-managed memory-pressure trade.
8. **`SCM_RIGHTS` file-descriptor passing over a Unix domain socket.** A descriptor is a reference into a kernel table that you can obtain only by inheritance or explicit transfer — never by naming a target — so "can I talk to X?" reduces to "was I given the handle to X?" Mojo endpoints are that object-capability rule moved into userspace, so reviewing a new IPC interface is mostly asking *who is handed this endpoint, and what can they do with it* — the capability's reach is the policy. (Module 15 ch04's seccomp broker is the same shape: the broker's FDs are the permissions.)

</details>
