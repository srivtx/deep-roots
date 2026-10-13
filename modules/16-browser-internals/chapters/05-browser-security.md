# Chapter 05 — The browser as a security boundary

> **The 30-second version.** An operating system protects memory with two tools: an address that says who you are, and a permission check that says what you may touch. The web rebuilt that design in software — the address is the origin, the 3-part tuple (scheme, host, port), and the same-origin policy is the permission bit checked on every DOM read, storage lookup, and network response. But for two decades that check ran *inside the renderer process* while cross-origin data sat in the same memory, and in 2018 Spectre proved that a language-level check is decoration when the hardware speculatively reads past it and the cache leaks the result. So the wall moved down a layer: site isolation puts every cross-site document in its own process (Chrome 67 desktop, mid-2018, at roughly +10-20% RAM), timer clamps blunt the side channel's stopwatch (100 µs vs 5 µs), COOP/COEP headers lock the remaining doors, and beneath it all the renderer still sits in module 15's sandbox — seccomp-bpf, brokered syscalls, no file opens. Which is why a modern browser exploit takes two bugs: one for the renderer, one for the cage.

## The origin tuple: an address space the web invented

Start from the OS analogy and derive the policy instead of quoting it. A process is safe because the MMU checks every memory access against page permissions; nobody wrote a check inside `memcpy` asking "is this destination really yours?" The web needed the same thing for documents, because a page at `bank.example` was about to be loaded alongside pages from `evil.example` in the same program. The designers' answer: give every document an *address* and check every cross-address access at runtime.

The address is the origin, and it is exactly a 3-part tuple:

```
origin = ( scheme , host , port )

(https, bank.example, 443)   -- default port 443 elided when written: https://bank.example
(http , bank.example, 443)   -- DIFFERENT origin: scheme differs (two "machines")
(https, bank.example, 8443)  -- DIFFERENT origin: port differs
(https, login.bank.example, 443) -- DIFFERENT origin: host is an exact string match,
                                     -- never a domain-suffix match
```

Three derivation consequences that escape most readers: a scheme difference is as strong as a host difference (HTTP and HTTPS versions of one site are mutually foreign); the host comparison is literal string equality after the DNS name is canonicalized — no wildcarding, ever; and the port is part of identity, which is why a dev server on :3000 cannot read the login session your main site stored for :443.

Now place SOP in the stack of walls the browser actually runs. Each layer isolates a different thing, and the security of the top layer depends on the ones below it:

```
+--------------------------------------------------------------------------+
| OS kernel + seccomp / AppContainer / seatbelt      isolates SYSCALLS     |
|   "this process may not open files, raw sockets, or fork escapes"        |
+--------------------------------------------------------------------------+
| Process boundary (site isolation, Chrome 67+)     isolates ADDRESS SPACE |
|   "cross-SITE bytes are never mapped into this renderer at all"          |
+--------------------------------------------------------------------------+
| Same-origin policy                                 isolates OBJECT ACCESS|
|   "this JS realm may not read that DOM, storage, or response body"       |
|   -- enforced as a runtime check inside a property getter                |
+--------------------------------------------------------------------------+
| Storage + cache keying                             isolates STATE       |
|   "the maps are keyed by origin, and (site, top-site) for 3rd parties"   |
+--------------------------------------------------------------------------+

        same process => same L1/L2 cache  =>  Spectre's 2018 lesson:
        the top layer is only as strong as the layer beneath it
```

The tuple is clean now, but three legacy features spent decades punching holes in it, and each one is worth knowing as a case study in how a relaxation becomes a liability:

- **`document.domain`** — two documents whose hosts share a parent domain could both *declare themselves* members of that parent (`document.domain = "example.com"`), erasing the host difference between `a.example.com` and `b.example.com`. The intent was 1990s-era convenience for companies that split themselves across subdomains; the cost was that compromising *any one subdomain* (a static file server, a forgotten CMS) let attacker code relax into the family trust. It took a multi-year funeral to remove: console warnings from Chrome 115 (2023), the setter dead by the Chrome 120 era, with a deprecation trial as the hospice for holdouts. The case-study lesson: a one-line relaxation shipped in 1996 can cost 5+ years of deprecation machinery to take back.
- **`file://` pages get an opaque origin** — serialized as the string `null` — meaning a file you open locally often cannot even read a sibling file. Historically inconsistent across engines (some treated all `file:` pages as same-origin with each other), now normalized toward opaque: no origin at all is safer than an origin anyone can share.
- **`blob:` URLs inherit** — a blob URL is `blob:https://example.com/<uuid>`: the origin rides inside the URL, so a blob created by a page keeps that page's origin wherever it travels, and `URL.createObjectURL` cannot be used to launder data into a different origin's trust domain.

## Where SOP runs: the getter, the handshake, the keyed map

The policy is not a firewall between processes. It is a set of runtime checks at three specific choke points — and knowing the exact choke point is what lets you predict the *observable failure mode*, which is what you debug.

**DOM access: an origin check inside a property getter.** When your script holds a reference to a cross-origin window or iframe, the engine hands you a `WindowProxy` wrapper whose property list has been cut down to a 13-entry whitelist (HTML spec cross-origin properties, the MDN "cross-origin window API" list): `window`, `self`, `location`, `postMessage`, `closed`, `length`, `frames`, `top`, `opener`, `parent`, `close`, `focus`, `blur`. Of these, `location` is the strange one: **write-only** — you may *set* `crossWindow.location` or `location.href` (navigating someone is allowed) but reading `href` throws. Everything not on the list is an access check that fires *inside the getter*, and the two failure modes are deliberately different:

```js
// Two paths to "the same" document, two different observable outcomes.
const frame = document.querySelector("iframe");       // cross-origin iframe
console.log(frame.contentDocument);                   // null — silently empty
console.log(frame.contentWindow.document);            // throws SecurityError
```

`contentDocument` is a getter on the *iframe element in your document*; the spec pins it to return `null` when the child's document is cross-origin — a silent empty. `contentWindow.document` goes through the *cross-origin WindowProxy*, whose getter throws (`Uncaught DOMException: Blocked a frame with origin "https://yours.com" from accessing a cross-origin frame`). Same policy, two enforcement points, two failure shapes — this asymmetry confuses every developer the first time they hit it, and it is the engine doing a live origin comparison on a property read, roughly the cost of a `strcmp` plus a security-decision function call on every cross-origin access.

**Network reads: CORS, the opt-out protocol.** A same-origin `fetch` reads the response freely. A cross-origin response is delivered but *withheld from script* until the server opts in via the handshake: the response must carry `Access-Control-Allow-Origin` matching the requester (exact origin or `*`). Whether the request even goes out directly depends on "simple" classification: GET/HEAD/POST with a `Content-Type` of `application/x-www-form-urlencoded`, `multipart/form-data`, or `text/plain` is simple; anything else — `application/json`, a `PUT`, a custom header like `X-Api-Version` — triggers the **preflight**: an `OPTIONS` request carrying `Access-Control-Request-Method`/`-Headers` that the server must answer before the real request is sent. One rule bites every beginner, and it is worth memorizing as an absolute:

```js
// The insider rule: '*' never matches a credentialed request.
await fetch("https://api.example/me", { credentials: "include" });
// api.example replies:  Access-Control-Allow-Origin: *
// Result in the console: "Access to fetch ... has been blocked by CORS policy:
//   The value of 'Access-Control-Allow-Origin' must not be the wildcard '*'
//   when the request's credentials mode is 'include'."
// The only fix: echo the EXACT origin plus 'Access-Control-Allow-Credentials: true'.
```

The reasoning is anti-CSRF arithmetic: a wildcard would let any page on the web read an authenticated response, because `credentials: "include"` (or ambient cookies) attaches the victim's authority to the attacker-initiated request.

**Storage: a map keyed by origin — then double-keyed.** `localStorage`, `IndexedDB`, `CacheStorage`, and the HTTP cache are all maps whose key starts with the origin. The quotas tell you the tiers: `localStorage` is the folklore-5 MB drawer (Chrome actually grants ~10 MB per origin counted in UTF-16 units; Firefox and Safari ~5 MB — engine-dependent), while `IndexedDB` is percent-of-disk territory (Chrome's quota arithmetic allows a site up to roughly 60% of disk before eviction kicks in, desktop, 120-era — the exact formula varies by version and platform). Then the modernization, driven by tracking rather than by Spectre: **third-party partitioning double-keys the map by (site, top-site)**. An iframe from `widget.example` embedded in `shop.example` now sees a *different storage world* than the same widget embedded in `news.example`. Chrome shipped full third-party storage partitioning in the 115 era (2023), with CHIPS (`Partitioned` cookie attribute) doing the same for cookies; Firefox's Total Cookie Protection landed by default in 2022. The practical payoff: cross-site tracking via a shared third-party `localStorage` jar is dead — the same-warehouse-same-shelf attack is structurally gone, and the ad industry's response (server-side joins) proves the boundary held.

**`postMessage`: the sanctioned channel.** Cross-origin code must be able to cooperate, so the platform provides exactly one controlled channel — message passing with a browser-computed origin. The code pattern is short, and the missing check is *the* classic code-review finding of web security:

```js
// child (https://widget.example) sends to its parent:
window.parent.postMessage({ token: localStorage.getItem("t") },
                          "https://shop.example");   // explicit targetOrigin, never "*"

// parent, CORRECT — verify the sender on every message:
window.addEventListener("message", (ev) => {
  if (ev.origin !== "https://widget.example") return;  // the check
  use(ev.data);
});

// parent, BUGGY — the classic missing-check finding:
window.addEventListener("message", (ev) => {
  use(ev.data);   // any origin that holds a Window reference to us owns this handler
});
```

Trust `ev.origin` — the engine computes it from the sender's realm, so it cannot be forged through the payload — and distrust everything in `ev.data`, which is attacker-shaped. The buggy version is exploitable by anyone who can *obtain a reference* to your window: an attacker page that opened you, or — the sneakier direction — any page that *embeds* you as an iframe, since `iframe.contentWindow` gives the embedder a parent-to-child postMessage line into your handler.

## Spectre's bill: the check ran in software, the data sat in hardware

Recap the attack from module 02 chapter 07 in five lines, then see why it was aimed at this chapter's subject. (1) Modern CPUs execute past branch and bounds checks speculatively. (2) The transient results touch cache lines on their way to being discarded. (3) Cache state is measurable by timing — a fast loop can tell which line is warm. (4) In 2018, a renderer process legitimately *held* other origins' data in its address space: cached cross-origin HTTP responses, cross-origin documents, same-process iframe contents. (5) Same process means same L1/L2 — so a script could speculatively read bytes it was forbidden to read and exfiltrate them through the cache timing channel. The 2018 insight in one sentence: **SOP was a language-level check while the hardware shared the data.** The check ran in a getter; the secret sat in a cache line 3 cycles away.

The defense hierarchy has four tiers, and each tier answers "where does the data live now?":

**(1) Process isolation — the full site-isolation project.** Cross-site documents simply never enter this renderer's address space. Chrome shipped it enabled for all desktop users in Chrome 67 (mid-2018) — a project ~3 years in the making, accelerated from "nice defense-in-depth" to "mandatory" in the 6 months after the January 2018 disclosure. The machinery is the OOPIF: an iframe whose content is cross-*site* gets its own renderer process, and the frame tree is split — the browser process owns the tree, each renderer owns only its assigned subtrees:

```
  Browser process (the trusted broker: owns the frame tree, the network
  stack, the UI, the disk -- the only process that syscalls freely)

     frame tree (the map)              renderer that paints it
     +--------------------+
     | https://shop.com   |  ----->  Renderer A [shop.com]
     |  +-----------------+          DOM + V8 heap + style + paint
     |  | https://ads.net |  ----->  Renderer B [ads.net]   <- an OOPIF:
     |  | (iframe)        |          its OWN DOM and V8 heap, in a
     |  +-----------------+          different address space entirely
     |  +-----------------+
     |  | https://pay.bank|  ----->  Renderer C [pay.bank]
     |  | (window.open)   |
     |  +-----------------+
     +--------------------+

  Pixels, not pointers, cross the boundary: Renderer B rasterizes its
  iframe content and the compositor in Renderer A/browser layers those
  quads/textures into one picture. "The iframe's pixels arrive by IPC."
  Measured cost: ~10-20% more RAM (Chrome's own numbers, desktop).
```

That RAM bill is the price of the 2018 lesson: the wall only counts if it is a page-table wall, and page-table walls cost an address space each.

**(2) Timer clamps — kill the stopwatch.** The side channel's resolution comes from timing granularity, so Chrome clamped `performance.now()` to **100 µs steps in ordinary (non-cross-origin-isolated) contexts** — the coarse clock. A cache hit-vs-miss signal lives in the tens-of-nanoseconds range; with a 100 µs tick, the signal drowns. Inside a `crossOriginIsolated` page the clock sharpens to **5 µs**, because (see tier 3) an isolated page has proven its process contains no other origin to spy on.

**(3) The cross-origin isolation gate: COOP and COEP.** `Cross-Origin-Opener-Policy: same-origin` severs the opener relationship for cross-origin popups — `window.opener` becomes null on both sides, closing the Spectre-via-opener path (and the reverse-tabnabbing family for free). `Cross-Origin-Embedder-Policy: require-corp` makes the loaded document promise that *every subresource* is safe to share a process with: each must be same-origin, CORS-approved, or carry `Cross-Origin-Resource-Policy: cross-origin` ("I'm cross-origin-readable"). The reward for passing the gate: `crossOriginIsolated === true`, which unlocks `SharedArrayBuffer` and the 5 µs clock. This is the "SAB waits at the gate" story: the hottest demo of 2017 (multi-threaded JS sharing memory across workers) spent ~4 years locked out — SAB shipped briefly, then from Chrome 92 (July 2021) it exists only inside cross-origin-isolated pages, because a shared-memory primitive plus a precise clock is a Spectre kit, and the gate makes you prove you are alone with your data before handing you both.

**(4) Mitigations inside the renderer.** V8 hardened generated code after 2018 — retpoline thunks for indirect calls in TurboFan output, masked index arithmetic in JIT'd array accesses — trading a few percent of throughput for transient-execution resistance. And Chrome 86 (2020) double-keyed the HTTP cache by (top-frame site, frame site), so even in-process, cross-site responses no longer share cache lines: "your cache lines are partitioned by site" — a memory-side mitigation of a memory-side leak.

## Below SOP: seccomp, the broker, and the untrusted token

Everything above assumes the renderer is honest-but-confined. The layer below assumes it is *fully compromised* — module 15 chapter 04's machinery, with the browser's specifics bolted on. If a renderer RCE is bug 1, this layer is what bug 2 has to defeat.

On Linux the renderer runs behind a **seccomp-bpf** filter that denies dozens of syscall numbers outright, and file access specifically is *brokered*: the renderer cannot `open(2)` a path. It sends a Mojo IPC message to the browser process asking for a file; the privileged parent performs the syscall, validates the path against an allowlist, and passes the file descriptor back. "The renderer can't even open a file" is literally the design — the untrusted child does IPC, the trusted parent does syscalls. On Windows the same shape uses a **restricted token** (privileges stripped from the token) plus an **integrity level**: the renderer runs at Untrusted IL — one notch *below* Low, on the Untrusted/Low/Medium/High/System ladder — so mandatory integrity control blocks it from writing anywhere medium-IL processes (including your profile) can write. On macOS it is **sandbox profiles** (the seatbelt `deny file-write*`-style rules). The GPU process is the deliberate exception: rasterization needs real device access (GL/Vulkan/ANGLE and vendor drivers), so it lives in a *looser* sandbox — the engineering judgment insiders call the least-lost-cause: isolate it in its own process, accept it needs power, make it restartable when it misbehaves, and never let its privileges flow back into renderers.

The sad outlier is extensions. Historically a power-grab: an extension could request host permissions over *all URLs*, read tabs and storage, and inject its code into every page — code that runs inside renderer processes with rights the page itself never had. Manifest V3 (the 2021-2024 rollout wave) is best read as a sandbox-tightening arc wearing a privacy costume: the **remote-code ban** (all logic must ship inside the package — no fetching new code at runtime, closing a self-updating backdoor channel), the replacement of persistent background pages with event-driven service workers (no always-on agent), and per-site opt-in for host permissions. Structurally, MV3 is what happens when you audit an API surface as if it were an IPC perimeter — because it is one.

## The two-bug rule: the real perimeter is an IDL file

Put the layers together and the economics of browser exploitation fall out. Compromising a user through Chrome requires a **chain of two 0-days**: bug 1 takes the renderer — a use-after-free in the DOM/parser heap (module 16 ch02's object graph is where these live, module 02/03's bug classes are how they fire), or a JIT miscompile turning module 10 ch03's UB license into arbitrary read/write. Bug 2 is the escape: a flaw in the sandbox itself — the broker's validation, a Mojo message the browser process mishandles, or the kernel underneath. One bug gets you the renderer; two get you the user. That is why a Chrome full chain at ZDI's Pwn2Own pays in the low six figures (order $100,000-$200,000 at the 2023-2024 events — the number moves every year and category), while a renderer-only bug is worth a fraction of that.

And once you internalize "the browser process is the privileged daemon," you see where the *real* attack surface is: not the sandbox policy but the **Mojo IDL** — the interface definition files that enumerate every message an unprivileged renderer is allowed to send to the privileged parent. The Chromium tree carries hundreds of interfaces spread across on the order of 1,000 `.mojom` files (120-era, and it grows every release). Every method in every one of those interfaces is reachable from hostile code inside the renderer. Defending that perimeter is an industrial process: ClusterFuzz — Google's continuous fuzzing farm, open-sourced in 2019 — rebuilds Chrome on tens of thousands of cores around the clock, including dedicated Mojo IPC fuzzers that mutate message fields against the validators. Two sentences of story: the fuzzers find the crashes, a triage pipeline bisects them to the offending commit, and the fix races the exploit writers. The two-bug rule is not a slogan; it is a budget line on both sides.

## What insiders know

- The `localStorage` quota is folklore-coded: "5 MB" is the number everyone quotes, but Chrome grants ~10 MB per origin (counted in UTF-16 code units) while Firefox and Safari hold near 5 MB — and `IndexedDB` is a different regime entirely (percent-of-disk, ~60% ceiling per origin on desktop Chrome before eviction, 120-era formula).
- Chrome's default is **process-per-site-instance**, not process-per-site: the unit is (site × BrowsingInstance × profile), so two same-site tabs *can* share one renderer when they are related by links/openers — a memory-vs-isolation tradeoff. `--process-per-site` and `--site-per-process` are the historical/force flags insiders use to move the dial, and on low-RAM Android the engine caps and merges renderer processes under pressure.
- COOP severs the *reference*, not the *channel*: `postMessage` between arbitrary cross-origin windows is always permitted (it carries its own origin check). `Cross-Origin-Opener-Policy: same-origin` just makes `window.opener` null so the attacker has no handle to call. The folklore "legacy window.open handshake" is exactly this: a popup you open keeps a postMessage line to you forever unless you used `noopener` (or COOP severed it).
- `document.domain`'s tombstone is a case study worth citing in design reviews: deprecated with warnings in Chrome 115 (2023), dead by the Chrome 120 era, with a deprecation trial as the transition. A one-line relaxation shipped in the 1990s cost ~5 years of removal machinery — the standard rebuttal to "it's just a small escape hatch."
- The cross-origin window whitelist is exactly 13 entries (`window`, `self`, `location`, `postMessage`, `closed`, `length`, `frames`, `top`, `opener`, `parent`, `close`, `focus`, `blur`), and `location` is **write-only**: set `href`, never read it. `contentDocument` returns `null`; `contentWindow.document` throws — two enforcement points, two shapes of failure.
- The renderer's Windows token runs at **Untrusted integrity level** — below Low — under a restricted token; on Linux the same renderer cannot `open()` a path, only request one over Mojo from the broker. Both facts fall out of one rule: the renderer is assumed compromised by default.
- `COEP: credentialless` (Chrome 96-era) is the insider's fallback when you cannot add CORP headers to third-party subresources: it strips credentials from cross-origin loads instead of demanding their cooperation — the pragmatic door when the strict one will not close.

## See it yourself

Five labs, all in DevTools on any Chrome/Chromium (120-era; the numbers below are what you should see):

**1. The gate, closed.** Open any normal site, DevTools console:

```js
crossOriginIsolated          // false
typeof SharedArrayBuffer     // "undefined" — SAB is gated, not unsupported
```

**2. The gate, opened.** Serve a two-header page from one file:

```python
# serve_isolated.py — one page, two headers, one gate opened.
import http.server

PAGE = (b"<!doctype html><meta charset='utf-8'>"
        b"<script>console.log('crossOriginIsolated =', crossOriginIsolated,"
        b" 'SAB =', typeof SharedArrayBuffer);</script>")

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")   # COOP
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp") # COEP
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

http.server.HTTPServer(("127.0.0.1", 8666), H).serve_forever()
```

Visit `http://127.0.0.1:8666/` — a page with no cross-origin subresources, so `require-corp` costs nothing here — and the console prints `crossOriginIsolated = true  SAB = function`. Same browser, same binary; two response headers moved the page across the gate.

**3. The clamp, measured.** A histogram of successive `performance.now()` deltas makes the clock policy visible:

```js
// Timer-granularity census: how coarse is this page's clock?
function census(n = 20000) {
  const counts = new Map();
  let prev = performance.now();
  for (let i = 0; i < n; i++) {
    const now = performance.now();
    const d = now - prev;
    if (d > 0) counts.set(d, (counts.get(d) || 0) + 1);
    prev = now;
  }
  return [...counts.entries()].sort((a, b) => a[0] - b[0]).slice(0, 6);
}
console.table(census());
```

Run it on the normal site from lab 1: deltas line up on **0.1, 0.2, 0.3 ms** — the 100 µs clamp; the loop spins many iterations per visible tick. Run it on the isolated page from lab 2: steps of **0.005 ms and up** — the 5 µs clock. That is the Spectre defense you can see in a histogram.

**4. The origin relationship, declared.** DevTools → Network → click any request → Request Headers. Every request now carries the Fetch Metadata set (Chrome 80+): `Sec-Fetch-Site: same-origin` on your own fetches, `cross-origin` on the CDN script, `none` on the top-level navigation typed in the URL bar; plus `Sec-Fetch-Mode: navigate|cors|no-cors` and `Sec-Fetch-Dest: document|script|empty`. The browser is annotating every request with the *origin relationship it can already prove* — which is why server-side CSRF defenses can reject state changes with `Sec-Fetch-Site: cross-origin` without any token machinery.

**5. The two failure shapes.** On any page with a cross-origin iframe (an embedded map or video will do):

```js
const f = document.querySelector("iframe");
console.log(f.contentDocument);        // null — the silent path
console.log(f.contentWindow.document); // throws: "Blocked a frame with origin
                                       //  '...' from accessing a cross-origin frame"
```

Same policy, two choke points: the element getter returns null, the WindowProxy getter throws. Both are the engine running an origin check *inside a property read* — SOP as a runtime permission bit, exactly where this chapter said it lives.

## How to use this knowledge

- **Debug CORS in one pass.** Read the console message precisely: "must not be the wildcard '*'" means credentialed request + `ACAO: *` (the insider rule); "No 'Access-Control-Allow-Origin' header" means no opt-in; a *preflight* failure means your `Content-Type: application/json` or custom header triggered `OPTIONS` and the server never answers it. Three messages, three different server-side fixes.
- **Audit `postMessage` handlers like setuid binaries.** Grep your codebase for `addEventListener("message")` and require two lines per handler: the `ev.origin` allowlist and, on senders, an explicit `targetOrigin` instead of `"*"`. The missing check is the single most common finding in real penetration tests of web apps.
- **Ship COOP/COEP deliberately.** Unlocking `SharedArrayBuffer`, wasm threads, and the 5 µs clock is two headers — but `require-corp` will break every third-party image/script that lacks CORP or CORS, so budget a subresource migration, or use `credentialless` as the stepping stone.
- **Read 0-day writeups with the layer map in hand.** When the next Chrome full-chain drop lands, you can classify each bug on sight: the UAF or JIT miscompile is bug 1 (renderer), the Mojo/broker flaw is bug 2 (escape). You will know which bug the bounty priced higher and why.
- **Answer the interview question with arithmetic, not vibes.** "Why does Chrome burn 10-20% RAM on site isolation?" Because Spectre proved a language-level check cannot protect data sharing a cache, so the wall moved to the page tables — and page-table walls cost one address space per site. Security spend you can *measure* is the whole discipline.

## Connections

- **Module 16 ch01** — the process lattice, broker, and Mojo introduced as architecture; this chapter re-reads the same machinery as an adversary would.
- **Module 16 ch02** — the DOM/Oilpan object graph is where the use-after-free bugs that fund "bug 1" of the two-bug rule actually live.
- **Module 16 ch03** — the compositing pipeline is why "the iframe's pixels arrive by IPC" costs real frames and real RAM, not just a sentence.
- **Module 16 ch04** — the worker + `SharedArrayBuffer` memory model is precisely the feature that waits behind the COOP/COEP gate.
- **Module 02 ch07** — the Spectre side-channel derivation this chapter cashed in: cache timing, speculative reads, same-process data.
- **Module 15 ch04** — seccomp-bpf, capabilities, and the broker pattern: the syscall layer this chapter's sandbox stands on.
- **Module 13 ch04** — the epoll/IPC patterns that make a broker + untrusted children architecture perform under load.
- **Module 10 ch03** — undefined behavior as the JIT's license; a miscompile is a security bug whose proof obligation you can now state.

## Quiz

1. SOP is often drawn as a wall between sites. Using the layered diagram, explain why the January 2018 Spectre disclosure invalidated that drawing — where did SOP's check actually run, and where did the protected data actually live?

2. On an ordinary Chrome page (not cross-origin-isolated) you run a tight loop recording deltas between successive `performance.now()` calls. What distinct delta values do you see in the histogram, and what changes when the same loop runs on a page served with COOP + COEP?

3. Predict the outcome: a page at `https://shop.example` runs `await fetch("https://api.example/me", { credentials: "include" })`, and `api.example` responds with `Access-Control-Allow-Origin: *` and status 200. Does the promise resolve with the JSON, reject, or something else — and why?

4. A teammate writes `const doc = iframe.contentDocument; console.log(doc)` and logs `null` with no exception; minutes later `iframe.contentWindow.document` throws a `DOMException`. Explain both behaviors — where is the origin check in each case, and why does one path fail silently while the other throws?

5. Your code uses `SharedArrayBuffer` and works in the 2017-era demo, but on Chrome 120 `SharedArrayBuffer` is `undefined` in your page. Diagnose: what is the page missing, what two headers fix it, and what does the browser give you back in exchange once they are sent?

6. A page registers `window.addEventListener("message", (ev) => { applyCoupon(ev.data.code); })` with no origin check. Describe a concrete way an attacker origin delivers a message to this handler, and the two-line fix.

7. Cross-module, module 15 ch04's broker: the renderer needs to read a font file at startup. Trace the request through the layers, and then state the two-bug rule — which two bugs, in which two layers, does an attacker need to turn a compromised renderer into code running as the user?

8. `https://accounts.example.com` and `https://mail.example.com`: same origin, same site, or neither? Under Chrome 67-era site isolation, can they share one renderer process, and why is that considered acceptable when SOP alone was not enough in 2018?

<details><summary>Answers</summary>

1. **Concept — the check's layer vs the data's layer.** SOP's check ran in software, inside the renderer process: an access check in a property getter, a CORS gate on a response, a keyed storage map — all language/binding-level decisions. The protected data (cross-origin HTTP responses, cross-site iframe contents) lived in the *same process's memory*, and same process means same L1/L2 cache. Spectre's mechanism — speculative execution past checks, transient loads warming cache lines, timing to read the warm/cold pattern — reads straight through a software check because the check never executes speculatively in a way that stops the cache side effects. The wall drawing was wrong about location: SOP was a checkpoint, not a wall; the wall had to be the page table, which is exactly what site isolation then bought (Chrome 67, +10-20% RAM).

2. **Predict the numbers.** Non-isolated page: Chrome clamps the clock to 100 µs, so deltas snap to multiples of 0.1 ms — you see 0.1, 0.2, 0.3 ms, with many loop iterations producing no visible tick at all. On the COOP+COEP page, `crossOriginIsolated` is true and the clock sharpens to 5 µs: steps of 0.005, 0.01, 0.015 ms appear. The clamp is the anti-Spectre defense: a cache hit/miss signal is tens of nanoseconds, and a 100 µs tick drowns it; the isolated page earned the finer clock by proving no other origin shares its process.

3. **Predict the output.** The promise rejects with a CORS error; the JSON never reaches script. The 200 status and the `*` header are not enough: `credentials: "include"` makes this a credentialed request, and the spec forbids `Access-Control-Allow-Origin: *` from ever matching one — a wildcard would let any page read an authenticated response. The server must echo the exact origin (`Access-Control-Allow-Origin: https://shop.example`) *and* send `Access-Control-Allow-Credentials: true`. This is the rule that bites every beginner, and the console message says it verbatim.

4. **Diagnose — two enforcement points, two failure shapes.** Both are runtime origin checks *inside property getters*. `iframe.contentDocument` is a getter on the iframe element in the parent's document; the spec pins it to return `null` when the child's document is cross-origin — the "silently empty" path, because element property reads generally prefer null over throw for this case. `contentWindow.document` goes through the cross-origin WindowProxy, whose getter enforces the 13-entry whitelist and throws a `SecurityError`/`DOMException` for anything off it. Same policy, different choke point, different observable behavior — and knowing this pair is how you avoid the classic confusion of "the check works, it just does not throw."

5. **Diagnose — the gate.** The page is not cross-origin-isolated: since Chrome 92 (July 2021), `SharedArrayBuffer` exists only inside pages where `crossOriginIsolated === true`. The 2017 demo predates the gate (SAB shipped briefly, then was pulled behind it because shared memory + a precise clock is a Spectre kit). Fix: send `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp` on the top-level document, and make every cross-origin subresource same-origin/CORS-approved/CORP-marked. In exchange, the browser returns the 5 µs timer clock, SAB, and worker-shared wasm linear memory — the multi-threaded web.

6. **Diagnose the bug — the attacker already has a reference.** Any origin that can obtain a `Window` reference to the page can postMessage it. Two concrete routes: the attacker page opens your page with `window.open` (it then holds your window and posts messages to it), or — the sneakier direction — the attacker *embeds* your page as an iframe, because the embedder's script holds `iframe.contentWindow` and can message the child. The handler trusts `ev.data` from all comers, so the attacker's `code` field is attacker-shaped input flowing into `applyCoupon`. Fix (two lines): gate the handler on `if (ev.origin !== "https://expected.example") return;` — `ev.origin` is computed by the browser from the sender's realm and cannot be forged via the payload — and on the sender side, pass an explicit `targetOrigin` rather than `"*"`.

7. **Cross-chapter — the broker and the two-bug rule.** On Linux, the renderer is under seccomp-bpf and cannot `open(2)` the font path itself: it sends a Mojo IPC message to the browser process requesting the file; the privileged browser process (the broker) validates the path against its allowlist, performs the syscall, and passes the file descriptor back over IPC. That is module 15's broker pattern with the browser's specifics: the untrusted child does IPC, the trusted parent does syscalls. The two-bug rule: bug 1 is the renderer compromise — a use-after-free in the DOM/parser heap (module 16 ch02's graph, module 02/03's bug classes) or a JIT miscompile (module 10 ch03's UB license) — yielding code execution inside the sandboxed renderer. Bug 2 is the escape: a flaw in the broker's validation, a Mojo interface the browser process mishandles, or a kernel bug under the sandbox. One bug gets you the renderer; two get you the user.

8. **Concept — origin vs site.** Neither same-origin nor same "site-origin": the hosts differ (`accounts.example.com` vs `mail.example.com`), and origin comparison is exact string match on the host — so they are different origins, and SOP applies between them in full. But they are the **same site** — site is scheme + eTLD+1 (`example.com`) — and site isolation partitions by *site*, not origin, so these two *can* share one renderer process (Chrome's default process-per-site-instance even encourages it for related tabs, to save the ~10-20% RAM that per-site processes cost). The reasoning: to exploit same-process placement you must already control code on a same-site subdomain, which requires controlling the DNS/registry of the parent domain — an adversary who owns `*.example.com` is inside the family trust already, and cache-line leakage is the least of your problems. The 2018 problem was *unrelated* origins sharing a process; same-site sharing is the accepted residual risk that keeps the RAM bill finite.

</details>
