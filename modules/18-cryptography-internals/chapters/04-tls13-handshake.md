# Chapter 04 — TLS 1.3: a state machine about memory hygiene

> **The 30-second version.** TLS 1.3 (RFC 8446, August 2018) is a
> five-year post-mortem of everything TLS ≤1.2 leaked, compiled into a
> state machine. Secrets derive in a chain — early → handshake → master
> — and are *erased on schedule*, which is what forward secrecy actually
> is. Each AEAD nonce is rebuilt from a hidden counter, and
> MAC-then-encrypt is deleted, so the padding-oracle bug class is
> deleted with it. Hello → key share → transcript → erase → MAC.

## The threat model: a man in the middle, and (honestly) no quantum computer

The attacker is a **network-position MitM**: she sees every byte and can
drop, replay, reorder, inject records, and end TCP at will. She is
**not a quantum computer** — RFC 8446 says so honestly: Shor's
algorithm on a large machine breaks the classical key exchange. Hence
**harvest-now-decrypt-later**: record today's handshakes, store them
until a quantum computer exists — data private for 15 years is broken
by a machine arriving in 10. So the hybrid **X25519Kyber768** shipped
inside the ordinary `key_share` extension: X25519 public key (32 B)
concatenated with a Kyber768 encapsulation key (1,184 B) — a
1,216-byte share instead of 32. Cloudflare enabled it edge-wide in
August 2023; Chrome 124 defaulted it in April 2024, Firefox 128 in
July 2024.

## The handshake: four flights, an encrypted middle, and a wire that lies

Flights, with the key schedule growing alongside:

```
  CLIENT                                                        SERVER
  ----------------------------------------------------------------------
  flight 1 | ClientHello ---------------------------------------->
           |  key_share: X25519 pub 32 B (+Kyber768 = 1,216 B)
           |  supported_versions [1.3,1.2], suites, SNI, (psk+early_data)
           |  [ChangeCipherSpec] ----->  DUMMY: no-op, middleboxes only
           |      0 --Extract(PSK|0)--> EARLY SECRET
           |
           | <--------------------------------------  ServerHello
           |     server key_share, suite, 32-B random
           | <------ [ChangeCipherSpec]  (dummy)
           |      EARLY --Extract(DHE)--> HANDSHAKE SECRET
           |      "c hs traffic" | "s hs traffic"  (one per direction)
  ========== everything below is AEAD-encrypted =======================
  flight 2 | <---- EncryptedExtensions \  server handshake key
           | <---- Certificate             \ ~3-5 certs, 1-2 KB each
           | <---- CertificateVerify       / signs transcript hash
           | <---- Finished               /  HMAC over transcript hash
           |      HANDSHAKE --Extract(0)--> MASTER SECRET
           |
  flight 3 | [CCS] + Finished --------------> client handshake key
           | app data ---------------------->  1-RTT: one round trip
  ----------------------------------------------------------------------
  transcript = append-only log: CH | SH | EE | Cert | CertVfy | Fin ...
  h_i = Hash(messages so far) — snapshots feed Derive-Secret/Finished
```

**Encrypted middle:** the certificate and the signature — plaintext in
1.2, and the reason 1.2 needed a second round trip — ride under
handshake keys. First application data after **1 RTT** versus 1.2's
2 RTT: 100 ms on a 100 ms path. **One key per direction:** each side's
encryption key is never the other's decryption key.

**The ChangeCipherSpec dance, and the honest reason.** Those plaintext
`ChangeCipherSpec` records do nothing — in 1.2 that message meant
"switch keys now"; in 1.3 it is a costume. Draft-era measurements
(2017-2018) showed **2-8% of paths broke** — passive DPI boxes and
firewalls expecting 1.2 shapes: plaintext Certificate, a
ChangeCipherSpec before encrypted data, `0x0303` in record headers.
So Appendix D.4 ships *middlebox compatibility mode*: ClientHello
declares legacy version `0x0301` (a lie), record headers carry
`0x0303` (a lie), and both endpoints sprinkle unencrypted no-op
ChangeCipherSpec records where 1.2 had real ones. **The wire lies,
deliberately, so unpatchable middleboxes keep forwarding** — protocol
deployment is a memory problem in other people's machines.

**The downgrade sentinel.** Versions moved into the encrypted
`supported_versions` extension, so an attacker can strip it to force a
1.3-capable pair down to 1.2. The defense is a canary in a field that
already exists: a 1.3-capable server forced into 1.2 MUST write the
last 8 bytes of `ServerHello.random` as `44 4F 57 4E 47 52 44 01` —
ASCII `DOWNGRD` + `0x01` (`0x02` for 1.1 or below). A client that
offered 1.3 but got 1.2 checks and aborts. Scrubbing the sentinel means
rewriting `random`, which changes the transcript and the Finished —
the MitM would have to terminate TLS herself, outside the threat
model. Honest gap: a genuinely 1.2-only server emits random bytes.

## The key schedule and the transcript: a hash chain over an append-only log

HKDF (chapter 02) gives `Extract` (salt + input → secret) and `Expand`
(secret → labeled subkeys). TLS 1.3 chains three Extracts, each salt
from the previous stage — a **hash chain in memory**:

```
  0 --Extract(PSK or 0)--> EARLY
        |  binder keys, "c e traffic" (0-RTT) -- PSK-derived only
        +-- Derive-Secret(early, "derived", "") -> next salt
  salt --Extract(DHE)--> HANDSHAKE
        |  "c hs traffic"/"s hs traffic" -> key+iv per direction,
        |                                   encrypts EE..Finished
        +-- Derive-Secret(hs, "derived", "") -> next salt
  salt --Extract(0)--> MASTER
        |  "c ap traffic"/"s ap traffic" -> all app records
        |  "exp master" (exporter), "res master" (resumption PSK)
  traffic key  = HKDF-Expand-Label(secret, "key", "", 16/32 B)
  static_iv    = HKDF-Expand-Label(secret, "iv",  "", 12 B)
```

Labels are ASCII and domain-separated; every `Derive-Secret` also
folds in `Hash(messages so far)`.

**The transcript hash is an append-only log with a rolling digest** — a
memory object, not a formula. One hash context; each handshake message
appended as processed; each snapshot (`Derive-Secret`, `Finished`,
`CertificateVerify`) clones and finalizes it. The 32-byte digest is the
*state of the conversation so far*: cheap to carry, impossible to
rewind — module 14's journaling intuition, append-only plus a signed
root. Tamper with one byte of any earlier message and every later
snapshot changes; both ends derive different keys, and the first AEAD
record fails its tag. `Finished` is the explicit seal:
`HMAC(finished_key, transcript_hash)`.

## Forward secrecy is key erasure; 0-RTT is the replay trade

"TLS 1.3 has forward secrecy because it uses ephemeral Diffie-Hellman"
is half the truth: DH gives the *opportunity*, a **deletion schedule**
delivers it. Forward secrecy = "past captures stay dead after later
compromise" — a property of memory, not mathematics:

- **The DH private key** (32 B) erases the moment the handshake secret
  exists — it lives one round trip. TLS 1.2's RSA key transport instead
  encrypted the premaster *to the server's long-term key*: record a
  session in 2019, steal the key in 2026, decrypt the capture.
  Heartbleed (chapter 05) leaked exactly these crown-jewel secrets from
  process memory — the module 02 lesson.
- **Handshake traffic secrets** erase once both Finished messages
  verify; **application traffic secrets** erase at connection close.
- The **resumption master secret** survives inside the ticket machinery
  under a lifetime policy; **server ticket keys** rotate on a timer of
  hours, not years.

**0-RTT: the replay trade, without romance.** A resumed client may send
encrypted application data *with its ClientHello* — zero round trips.
It rides the **early traffic secret**, derived from the PSK alone: no
fresh DHE, so **no forward secrecy against PSK compromise**. And the
whole early flight is a byte string an active attacker can **replay
verbatim**. The passive eavesdropper cannot decrypt (no PSK); the
active MitM can re-send it. **The application must decide**: idempotent GET, fine; a
POST that moves money, never. Chrome encodes the rule — early data
only for idempotent GETs — and usage lands in the low single digits of
percent where enabled, by design. Appendix E.5 supplies server-side
complements, each literally a memory requirement: **single-use
tickets**, **ClientHello recording** within a time window (a
bloom-filter of recent hellos), or one-time external PSKs.

**Resumption, PSK, and the 1.2 ticket-key hole.** After the handshake
the server sends `NewSessionTicket` (encrypted, flight 2): an opaque
ticket — the resumption PSK wrapped under a server-held **ticket key**
plus a nonce. Next connection both derive resumed secrets from
`resumption_master_secret` — no certificate, 1 RTT (0 with 0-RTT).
**TLS 1.2 tickets were a forward-secrecy hole** — the ticket encrypts
the whole session state, including the DHE-derived master secret, under
a long-lived ticket key. Record the session, later steal the ticket
key, and the capture decrypts: the forward secrecy you paid a round
trip for evaporates through the back door. Real-world amplifier: CDN
edges share ticket keys in shared memory so a Frankfurt ticket resumes
in Tokyo — one edge compromise becomes fleet-wide. TLS 1.3 fixes the
shape twice: the resumption PSK is derived *per ticket* (nonce-mixed),
and resumed connections should re-run DHE (`psk_dhe_ke`). Configure
resumption PSK-only and you have rebuilt the 1.2 hole with new labels.
Ticket key rotation is the server's own erasure: rotate every few
hours and yesterday's captured tickets die.

## The record layer: framing, the nonce that never travels, one bug class deleted

Above the handshake, TLS is framing over TCP. Each record: a **5-byte
header** — content type (1 B), legacy version `0x0303` (2 B), length
(2 B) — then up to **2^14 = 16,384 bytes** of plaintext. Encrypted
records claim type 23 (`application_data`); the real type rides
*inside* the AEAD plaintext with optional zero padding — a middlebox
cannot even tell an alert from data. AEAD adds a 16-byte GCM tag:

```
  wire:  [type=23][03 03][len 2B][ ciphertext ][ tag 16 B ]
  nonce = static_iv (12 B) XOR (seq_num, 64-bit big-endian, left-padded)
          — seq 1 flips only the last byte; the nonce never travels.
```

GCM dies
horribly on nonce reuse — Joux's forbidden attack: one reused pair
leaks the authentication key H, forgery forever — so the protocol
*refuses to transmit nonces at all*. Each side keeps a 64-bit
**sequence counter per direction per key epoch**, starts it at 0
whenever a new traffic key is installed, and rebuilds each nonce as
`static_iv XOR seq` — implicit memory that must agree exactly on both
ends. **A desync fails two ways:** *decryption failure* (sender
restarted at 0, receiver expects 5 — every tag fails, `bad_record_mac`,
the polite death), or, catastrophically, *nonce reuse* (both ends, or a
retransmitting proxy, replay low counter values under the same key —
chapter 02's two-time pad and H recovery). KRACK (2017, chapter 05) was
this class in WPA2. Rekey (`KeyUpdate`) before roughly **2^24.5
records (~24 million)** per traffic key.

**Encrypt-then-MAC, and killing the bug class.** TLS 1.2's CBC suites
did MAC-then-encrypt: pad, append MAC, encrypt all. The receiver
decrypts, strips padding, *then* checks the MAC — and those paths
differ in time by ~2 µs, an oracle over a LAN: Lucky 13
(2013, chapter 05) read plaintext through that timing, and the
constant-time patches lasted years. TLS 1.3 allows **only AEAD**: the
tag covers ciphertext plus header, and must be verified before one
byte of plaintext is parsed. No padding exists on any pre-verification
path, no timing channel between "bad tag" and "bad padding" — the
second state cannot exist. Lucky 13 was not patched in 1.3; **its bug
class was removed from the grammar.** That is the lesson worth more
than the protocol: *remove the class of bug, don't fix each instance.*

## Trust as a memory walk, and a clean goodbye: chains, CT, stapling, close_notify

`Certificate` is a memory walk: ~3-5 certs, 1-2 KB of DER each, leaf
first; the client walks issuer pointers up to a root in its **trust
store** — a few hundred 1 KB blobs shipped with the OS or browser: a
cache with an update channel, nothing more.
Revocation: CRLs are dead (megabyte lists, stale by days); plain OCSP
is dead (leaks browsing history, adds a round trip, fails *open* when
the responder is unreachable — an attacker just blocks the OCSP port).
The compromise: **OCSP stapling** — the server fetches its own signed,
timestamped response and staples it into the handshake (~1-2 KB, fresh
until `nextUpdate`); **must-staple** opts the certificate in so
staple-less presentations fail. Over all of it, **Certificate
Transparency** (RFC 6962/9162): every CA-issued cert must be logged in
a public, **append-only Merkle tree** — module 14's journaling
intuition: append-only plus a signed tree head makes history
unfalsifiable. The client demands one **SCT** per chain link (a log's
signed promise, ~100 B; inclusion provable with ~log2 n hashes —
30 × 32 B in a billion-cert log), so rogue CA issuance is detectable
before it is silently trusted.

**Ending a connection is a security decision.** TCP gives a clean EOF —
FIN, queue drains, `read()` returns 0 — and a MitM can forge exactly
that at any moment; module 13 ch03's machine cannot tell an attacker's
FIN from the peer's. Without a TLS-level end marker the application
sees "the connection was fine, the data just... ended": a 10 KB
response delivered as 3 KB — payment apps have shipped that bug. TLS's
answer is `close_notify`, alert 0: each side must send it (encrypted,
record type 21) before closing its write half, and a library must
surface **EOF-without-close_notify as an error, not EOF** — Go returns
`io.ErrUnexpectedEOF`, and OpenSSL 3.0 added
`SSL_R_UNEXPECTED_EOF_WHILE_READING` for exactly this (older OpenSSL
returned a bare syscall EOF, logged as success). Half-close is legal
(`close_notify` = "done writing," the peer may continue); truncation
detection lives one layer above the layer that can be truncated.

## What insiders know

- **ECH (Encrypted ClientHello) shipped 2024-2025**, removing the last
  plaintext metadata: the ClientHello splits into an **outer** (cover
  `public_name`, junk key share, padded) and an **inner** (real SNI,
  ALPN) encrypted under the server's published ECH key. Still an IETF
  draft, not a numbered RFC — Cloudflare, Firefox, and Chrome deployed
  it anyway; DNS HTTPS records carry the ECH config, so DNS is now
  part of the TLS trust base.
- **`SSLKEYLOGFILE` is the debugging backdoor everyone runs in CI.** An
  env var from the NSS world: Firefox and Chrome honor it, `s_client`
  takes `-keylog`, and any process reading the env var gets *all*
  traffic secrets (`CLIENT_HANDSHAKE_TRAFFIC_SECRET`,
  `SERVER_TRAFFIC_SECRET_0`, ...) in a flat file Wireshark also reads.
  Grep your CI variables for it.
- **The "TLS 1.3 in 30 seconds" mnemonic: hello → key share →
  transcript → erase → MAC.** Five words — also the interview answer
  to "why does TLS 1.3 exist."
- **The ChangeCipherSpec spam has a second act:** in a HelloRetryRequest
  exchange (server asks for a different group), compat mode requires
  dummy CCS records *between the first and second ClientHello* — some
  stacks emit one before *every* encrypted record. A 2024+ IETF effort
  wants to deprecate compat mode now that middleboxes learned 1.3.
- **`supported_versions` is the real version field; every other
  "version" is set dressing** — ClientHello says `0x0301`, record
  headers say `0x0303`, both lies. Parse the extension, never the
  header. And **KeyUpdate resets the sequence counter to 0** — the one
  permitted reuse of low values, because the key changed too.

## See it yourself

**1. DevTools: live handshake metadata.** Chrome/Firefox, DevTools →
Security tab → reload any HTTPS site. Protocol "TLS 1.3"; cipher suite
(`TLS_AES_256_GCM_SHA384` or `TLS_CHACHA20_POLY1305_SHA256` — five
suites exist in 1.3 versus hundreds in 1.2's registry); key exchange
group (`X25519`, or `X25519Kyber768Draft00` — then your browser sent a
1,216-byte key share); certificate chain and SCT timestamps.

**2. `openssl s_client`: watch flights turn opaque.**

```console
$ openssl s_client -connect example.com:443 -tls1_3 -msg 2>&1 | head -60
```

Read in order: ClientHello (find `key_share` and `supported_versions`),
ServerHello — then the dump becomes opaque ciphertext blobs:
EncryptedExtensions, Certificate, CertificateVerify, Finished are
*already encrypted*, with the dummy ChangeCipherSpec in between.
Contrast `-tls1_2`: the Certificate flies by as readable structure —
1.3's most visible design change.

**3. Wireshark + SSLKEYLOGFILE: dissect a real handshake.**

```console
$ SSLKEYLOGFILE=/tmp/keys openssl s_client -connect example.com:443
$ SSLKEYLOGFILE=/tmp/keys tshark -i any -f 'tcp port 443' -w /tmp/tls.pcap
$ SSLKEYLOGFILE=/tmp/keys wireshark /tmp/tls.pcap
```

Wireshark honors the env var (3.x+) and decrypts in place. ClientHello
key_share and ServerHello stay plaintext; every record after decrypts.
Find Finished: its verify_data is the HMAC over the transcript hash,
and the `CLIENT_HANDSHAKE_TRAFFIC_SECRET` line encrypted flight 3. For
0-RTT replay, the paper version: capture a resumed connection with
early data and re-send the ClientHello + early records with
`tcpreplay` or a 20-line python socket loop — servers advertising
0-RTT process the replayed early data again unless their anti-replay
window catches it.

## How to use this knowledge

- **Debug `bad_record_mac` after any connection handoff** (load-balancer
  state transfer, zero-downtime restart, TCP migration): if key and
  counter ever travel separately, you have built the desync (polite
  death) or the nonce reuse (catastrophe).
- **Decide 0-RTT per endpoint, not per fleet.** Idempotent GET APIs may
  take the round trip; anything with side effects must refuse early
  data at the server.
- **Latency arithmetic for cold starts.** Full handshake = 1 RTT, 1.2 =
  2, resumption = 1, 0-RTT = 0 for the first payload byte: on a 100 ms
  path a 100-300 ms product decision — and the Kyber hybrid's +1.2 KB
  may cost an extra packet.
- **Truncation review item.** Code that treats a short read ending in
  clean EOF as a complete response is one attacker-FIN away from a
  payments incident. Audit for OpenSSL < 3.0's bare-EOF behavior.

## Connections

- **Module 18 chapter 01** — the ephemeral private key is 32 bytes from
  the same DRBG stream: forward secrecy is entropy harvested once and
  *never persisted*.
- **Module 18 chapter 02** — the (key, nonce) discipline and GCM's
  forbidden attack become the per-connection counter law.
- **Module 18 chapter 03** — the X25519 key share must ride a
  constant-time ladder; CertificateVerify is its signature machinery
  pointed at a transcript hash.
- **Module 18 chapter 05** — the failure stories behind these decisions:
  Heartbleed, Lucky 13, KRACK.
- **Module 13 chapter 03** — the layer *underneath*: every TLS record
  rides TCP's byte-stream machine. A 16,384-byte record is reassembled
  from whatever segments m13 ch03's receive-buffer reassembly queue
  delivers — out of order, hole by hole; the AEAD tag is checked only
  once the record is whole, so TCP reordering is invisible above the
  boundary. Two sequence-number disciplines coexist: TCP's explicit
  per-byte number, sent in every header, and TLS's implicit per-record
  counter, never transmitted. And m13 ch03's clean FIN-EOF is exactly
  the event an attacker forges to truncate: only this chapter's
  close_notify makes truncation visible, one layer above the layer
  that can be truncated. (This link repairs m13 ch03's orphan status:
  its buffer machine finally has a customer.)
- **Module 01 chapter 23** — the grand table's four questions asked of a
  traffic secret: *what is it* (32 B key + 12 B IV, heap bytes in the
  TLS library); *who owns it* (the TLS state machine — not the app,
  not the OS); *who frees it, when* (the erasure schedule — this
  chapter's argument); *what is promised* (secrecy, only if every
  layer kept its discipline). Key material is the row no language ever
  owned.

## Quiz

1. **Concept.** An audit concludes: "forward secrecy: achieved — we use
   X25519." What is missing? Define forward secrecy as this chapter
   frames it, and list the erasure points (with timing) that turn the
   key exchange into actual FS.
2. **Concept.** 0-RTT data is "encrypted but replayable." Explain the
   asymmetry: what does the passive eavesdropper lack that the active
   MitM has? What must the application enforce that no TLS layer can?
3. **Predict.** A client sends a 20,000-byte POST body in one `write()`
   on a fresh connection (AES-GCM, first application record). How many
   records go on the wire, with which sequence numbers, and — if the
   client's static_iv ends in `0xab` — how do the two nonces differ?
   Which 4 bytes can never change?
4. **Predict.** A MitM flips one byte of the ServerHello in flight (the
   cipher-suite field) and delivers everything else untouched. At which
   message does the client detect the tamper, and through which two
   memory structures does the altered byte kill the connection?
5. **Predict.** An attacker strips `supported_versions` from a
   ClientHello so a 1.3-capable server negotiates TLS 1.2. What do the
   last 8 bytes of ServerHello.random become (hex), who put them there,
   and what does the client do? What gap remains when the server is
   genuinely 1.2-only?
6. **Diagnose.** A zero-downtime restart serializes connection state:
   traffic keys restored, record counter reset to 0; the peer's counter
   is at 5. Every post-restart record fails with `bad_record_mac`.
   Explain the failure — then explain why the mirror-image bug (both
   endpoints restarting at 0 under the same key) is not a decryption
   failure but a catastrophe. Name what leaks.
7. **Diagnose.** A payment client receives 3 KB of a 10 KB response,
   then `read()` returns 0; the app logs "success." What TLS-level event
   never happened, what did the attacker do at the TCP layer, and what
   must the library and app do instead? Name the OpenSSL 3.0 error
   reason that exists for this case.
8. **Cross-chapter.** Map each TLS 1.3 design decision to the historical
   attack it kills, and where its mechanism lives here: (a) AEAD-only,
   tag verified before any parsing; (b) RSA key transport deleted,
   ephemeral DHE only; (c) the sentinel in ServerHello.random; (d)
   every Finished MACs the whole transcript. Then the TCP tie: module
   13 chapter 03 hands TLS a *clean EOF* under a truncation attack —
   why is that EOF invisible at the TCP layer, and which record type
   here makes truncation visible?

<details>
<summary>Answers</summary>

1. The sentence names an *opportunity*. Forward secrecy means recorded
   past sessions stay secret after later compromise of long-term keys —
   a property of the deletion schedule, not the group: X25519 only
   makes session keys *unrelated* to the certificate key. Erasure
   points: the DH private key right after the handshake secret is
   derived (~1 round trip); handshake traffic secrets once both
   Finished messages verify; application traffic secrets at close; the
   resumption secret under its lifetime policy; server ticket keys on
   hours-scale rotation. Persist the ephemeral key — DHE-then-hoard:
   FS on paper, none in memory.
2. The eavesdropper holds ciphertext but not the PSK, so the early
   traffic secret is underivable. The MitM cannot read it either, but
   she can *re-send* the ClientHello and early records verbatim, and
   the server will decrypt and process them again — replay is her
   capability, decryption is not. Application rule: only idempotent
   requests ride 0-RTT; anything with server-side effects waits for
   the full handshake — no protocol layer can know which of your
   requests is safe to execute twice.
3. Two records: 16,384 + 3,616 plaintext bytes; sequence numbers 0 and
   1 (a new key epoch resets the counter; handshake records used the
   *handshake* keys). Nonces `static_iv XOR 0` and
   `static_iv XOR 1`: last byte `0xab` then `0xaa`, only that byte
   differs. The top 4 bytes never change: the 64-bit counter is
   right-aligned in the 12-byte nonce, so those bytes sit above the
   counter field, XORed with zeros forever.
4. The client detects it at the **first encrypted record**
   (EncryptedExtensions) — not at Finished. Structure one: the server's
   rolling transcript digest was computed over the *original*
   ServerHello, the client's over the tampered one. Structure two: both
   then derive *different* handshake traffic secrets — Derive-Secret
   folds Hash(CH..SH) into the key schedule. The tag fails, and the
   connection dies with `bad_record_mac` before the tampered field is
   parsed. Finished is the backstop for anything surviving derivation.
5. `44 4F 57 4E 47 52 44 01` — ASCII "DOWNGRD" plus `0x01` for a drop
   to TLS 1.2 (`0x02` for 1.1 or below). The *server* wrote them: a
   1.3-capable server forced into 1.2 must self-report. The client
   checks and aborts. The MitM cannot scrub the sentinel without
   rewriting `random` — which changes the transcript and the Finished,
   forcing her to terminate TLS with a trusted certificate, outside
   the threat model. The gap: a genuinely 1.2-only server emits random
   bytes.
6. The restarted sender builds nonces `iv XOR 0,1,2...`; the receiver,
   at sequence 5, reconstructs `iv XOR 5` — nonce mismatch, tag failure
   on every record: the desync death, merely annoying. The mirror bug —
   both ends at 0 under the same key — means the *same* (key, nonce)
   pairs repeat: GCM nonce reuse, chapter 02's forbidden attack. The
   two-time pad leaks plaintext XORs, and the authentication subkey H
   is recoverable — forgery forever. Counter state and key state must
   travel together.
7. `close_notify` never happened: the peer's encrypted alert (type 21)
   that must precede a clean write-side close. At the TCP layer the
   attacker injected a FIN; m13 ch03's machine delivers a byte-exact
   stream end indistinguishable from a genuine one — TCP has no
   concept of "ended early." The library must surface
   EOF-without-close_notify as a truncation error — OpenSSL 3.0's
   `SSL_R_UNEXPECTED_EOF_WHILE_READING` exists for exactly this — and
   the application must treat any short response as a failed
   transaction.
8. (a) AEAD-only, tag-before-parse ↔ **Lucky 13** (2013): CBC
   MAC-then-encrypt's padding-vs-MAC timing oracle — mechanism in
   module 18 ch05; 1.3 removes the pre-parse padding path. (b)
   Ephemeral DHE only ↔ **RSA key transport's record-once-decrypt-later
   property**, weaponized by passive capture plus later key theft —
   Heartbleed (ch05) was the key-theft event. (c) The sentinel ↔ the
   **downgrade family**: POODLE-style version-stripping (2014) and
   FREAK (2015). (d) Transcript-bound Finished ↔ the
   **renegotiation/triple-handshake confusion** (CVE-2009-3555): every
   MAC bound to the whole log makes splicing two handshakes into one
   impossible. A FIN only says "the byte stream
   ends here" — an injected FIN yields a perfectly consistent stream,
   invisible to m13 ch03; only `close_notify`, an encrypted record
   unforgeable without the traffic key, tells the application the
   *peer* — not the wire — ended the conversation.

</details>
