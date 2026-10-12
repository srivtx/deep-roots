# Chapter 04 — The numeric & parsing family

> **The 30-second version.** Every number is a decision — how many
> bits, signed or not, base 2 or base 10 — and this family is what happens when
> those decisions disagree. Signed overflow in C is undefined behavior, a
> license the optimizer cashes to delete your checks; unsigned overflow is a
> clock — defined, silent. Floats are binary fractions, so 0.1 does not exist
> and money drifts. Parsers turn untyped bytes into typed values — overflow
> there is silent success, and byte-wise slicing cuts characters in half. Time
> is a number pretending to be simple: 23-hour days, 61-second minutes, and a
> signed 32-bit clock that ends in January 2038.

## Overflow: signed is a contract violation, unsigned is a clock

Module 01 ch08's raw deal: an `int` is 4 bytes (x86-64/ARM64, "LP64": `int`
32, `long` 64), two's complement, range −2,147,483,648 … +2,147,483,647. Add 1
to the top and you need bit 32, which does not exist. What *should* happen?

- **Unsigned:** C11 §6.2.5 ¶9 *defines* the result — arithmetic mod 2^32.
  `UINT32_MAX + 1 == 0`. A clock: portable, silent, a bug only because you
  believed it was a number line.
- **Signed:** C11 §6.5 ¶5 says **undefined**. Not "wraps" — no requirement at
  all.

Why? The optimizer wants algebra: `i + 1 > i` holds on a number line, so the
compiler may *assume* it — and at `-O2` (GCC 14, x86-64) a guard written
`if (t + 1 < t)` compiles to nothing: the assumption makes it provably false.
UB-as-license is the mechanism (module 10 ch02 shows the passes; here you need
the rule: *never write a check whose truth requires the UB*). Use
`__builtin_add_overflow`, or `-fwrapv` to force wrap.

### The promotion ladder: where the overflow actually happens

Most integer math never happens in the declared types. C11 §6.3.1.1 forces
**integer promotion** — anything narrower than `int` computes in `int`; §6.3.1.8
(*usual arithmetic conversions*) then unifies mixed pairs:

```text
 the promotion ladder (C11 §6.3.1.1 / §6.3.1.8; x86-64 Linux, int = 32 bits)

   you wrote        computed as     rule                      the bite
   -------------    ------------    -----------------------   ------------------------
   u16 + u16   ->   int             integer promotion         max 131,070: fits
   u16 * u16   ->   int             integer promotion         65,535 x 65,535 =
                                                              4,294,836,225 > INT_MAX:
                                                              SIGNED UB
   int + u32   ->   u32             usual arith. conversions  the int converts; wrap is
                                                              defined, the sign is gone
   u32 * u32   ->   u32             both already wide         wraps mod 2^32, silently
```

The famous row: two 16-bit values whose product overflows a *signed
32-bit intermediate*. The cast cannot save you — `(uint32_t)(a * b)` applies
after the UB — and no warning fires (GCC 14, `-Wall -Wextra -O2`: silent):
to the compiler it's just "an `int` times an `int`." Only UBSan sees
it (lab below).

### The malloc shape: `a*b*c`

The modern CVE shape is a size computation:

```c
struct pixel *buf = malloc(w * h * sizeof(struct pixel));  /* int math */
```

Attacker picks `w`, `h` so `w * h * sizeof` wraps to a small positive value —
say 3,000. `malloc(3000)` succeeds; the loop writes `w * h` pixels through the
window: heap overflow (module 02 ch04's exploitation view). Dozens of decoder
CVEs share this line. The fix is type discipline: compute in `size_t` and
check each factor — `if (h != 0 && w > SIZE_MAX / h / sizeof *buf)
return NULL;`.

### Ariane 5, derived

June 4, 1996, flight 501: 37 s after liftoff the rocket veered and
self-destructed. From the official inquiry: the inertial reference system (Ada)
converted a 64-bit float — horizontal bias, an alignment correction inherited
from Ariane 4 — into a **16-bit signed integer**. Ariane 5's early trajectory
was faster; the value passed 32,767; Ada raised an exception on a conversion
whose protecting handler had been disabled, and both redundant SRIs went down:

```c
double bh = 64_bit_float;        /* range ~1.8e19 */
int16_t scaled = (int16_t) bh;   /* range ±32,767 — ratio 2.8e14 : 1  */
/* Ariane 4 analysis "proved" bh < 32767. Ariane 5 broke the proof; the
   cast had no runtime guard.                                       */
```

A conversion is a range compression; it needs either a proof that holds for all
future inputs or a runtime check. C's float→int cast when the value doesn't fit
is again UB; Ada raises. The languages differ; the ratio doesn't.

## Floats: numbers that are not on the number line

An IEEE-754 `double` (8 bytes everywhere here) is a binary fraction: only
denominators that are powers of 2 are exact. 1/2, 1/4: exact. 1/10 =
0.000110011… *repeating* — like 1/3 in decimal. Rounded to 52 fraction bits:

```python
# CPython 3.12; any IEEE-754 float64 (C, Java, JS, Go) gives identical bits
0.1 + 0.2          # 0.30000000000000004
0.1 + 0.2 == 0.3   # False
0.1 * 3 == 0.3     # False
```

**The money rule:** never store money in binary floats. The ~10^-17 error per
operation aggregates — 10,000 additions carry 10,000 roundings, and rounding
the *final* sum snaps an already-wrong number. Correct homes: integer cents
(`int64_t` cents overflows past $92 quadrillion) or a real decimal type —
Python `decimal.Decimal`, Java `BigDecimal`, SQL `NUMERIC`, COBOL COMP-3
(module 08 ch03), which is why ledgers never left base 10.

**Patriot, February 25, 1991** (Dharan): the clock advanced in 0.1-second
ticks, counted in a **24-bit** register. 0.1 is infinitely long in binary;
truncated to 24 bits, each tick is short by 0.000000095 s. After 100 hours:
3,600,000 ticks × 9.5e-8 ≈ **0.3433 s**. The range gate was computed 0.34 s
stale; at ~1,676 m/s the Scud was **687 m** outside it. Miss; 28 dead. The
error lived in the *representation of a constant*; the tick counter multiplied
it three million times.

### The values that break `==`

```python
nan = float("nan")
nan == nan                            # False — NaN is "unordered" by spec
other = float("inf") - float("inf")   # a DIFFERENT NaN object
[1.0, nan, 2.0].remove(nan)           # works -> [1.0, 2.0]
[1.0, other, 2.0].remove(nan)         # ValueError: x not in list
[nan] == [nan]                        # True   (same object)
[nan] == [float("nan")]               # False  (different objects)
```

Three behaviors, one cause: collections don't use `==`, they use an *equality
protocol*. CPython's `PyObject_RichCompareBool` (behind `remove`/`index`/`in`)
short-circuits on identity first: `x is y or x == y`. Same object → hit;
different NaN → miss. Java is the mirror image: `ArrayList.remove(Object)`
routes through `Double.equals`, which compares bits — the Javadoc states
`Double.NaN.equals(Double.NaN)` is *true* while `==` is false. NaN must never
reach a keyed collection or comparator; filter it at the boundary.

**−0.0** is numerally `==` to 0.0, but the sign bit is set
(`struct.pack(">d", -0.0)` → `8000000000000000` vs `0000000000000000`), and
`1 / -0.0` is `-inf` where `1 / 0.0` is `+inf`. Equality passed; the sign
survived.

## Parsing: the boundary where types are born

Parsing is chapter 01's bug definition made literal: the *spec* for
"3000000000" is "three billion"; the *behavior* depends on the function:

```c
/* glibc 2.41, GCC 14, x86-64 — verified below */
int d = 0;
int n = sscanf("3000000000", "%d", &d);   /* n = 1 (success!), d = -1294967296 */
```

The standard makes `%d` overflow **undefined** (C11 §7.21.6.2 ¶10); glibc wraps
mod 2^32 and *reports success* — the caller cannot tell. The defined tool is:

```c
errno = 0;
long v = strtol("99999999999999999999", NULL, 10);
/* v = LONG_MAX (9223372036854775807), errno = ERANGE  (C11 §7.22.1.4) */
```

Parse with `strtol`-family or your language's checked equivalent; test `errno`
*and* the end pointer; then narrow with an explicit range check — Ariane's
lesson applies to your `int64 → int32` step too.

### Bytes are not characters

UTF-8 stores one code point in 1–4 bytes: ASCII is 1; `é` is 2 (`0xC3 0xA9`),
announced by the lead byte (`110xxxxx`), continued by `10xxxxxx`:

```python
b = "café".encode()   # b'caf\xc3\xa9' — 5 bytes, 4 characters
b[:4]                 # b'caf\xc3' — lead byte severed from its continuation
b[:4].decode()        # UnicodeDecodeError: 'utf-8' codec can't decode byte
                      # 0xc3 in position 3: unexpected end of data
b.decode("latin-1")   # 'cafÃ©' — mojibake: UTF-8 bytes read as Latin-1
```

Two faces of one bug: the *crash* (invalid continuation) and the *silent*
mojibake, where the bytes decode under a wrong single-byte encoding and the
damage propagates. `truncate(80)` on a status field, `log_line[:120]` —
byte-wise in Go `[]byte`, Python `bytes`, C `char*`. Rule: slice on characters
(Python `str`, Go `[]rune`, Rust `is_char_boundary`), or verify the cut point
isn't a continuation byte — and declare the encoding at every boundary;
mis-declared Latin-1/UTF-8 is *the* mojibake generator.

**Identifier/length limits** are the same class at another layer: every tier
has its own maximum. C11 §5.2.4.1 requires only 31 significant characters
in external identifiers; ext4 caps filenames at 255 bytes; RFC 5321 caps the
email local part at 64 octets. A frontend allowing a 256-byte username over a
column that silently truncates to 255 merges two users, or turns
`user@example.com` into `user@examp`. Check length at the parser against the
*strictest* limit downstream, and fail loudly.

## Time: the number that pretends to be simple

Unix time is one integer: seconds since 1970-01-01T00:00:00 UTC. Then the width
and the calendar answer back.

**2038, derived.** Signed 32-bit `time_t` counts at most 2^31 − 1 =
2,147,483,647 s. A year is ~31,556,952 s (365.2425 days), so the budget is
2,147,483,647 / 31,556,952 ≈ 68.1 years — 1970 + 68.1 lands in **January
2038**, exactly 2038-01-19 03:14:07 UTC. One second later the sign bit flips
and the value reads −2^31 = **1901-12-13 20:45:52** — which is why 32-bit
systems show files dated 1901, not crashes:

```text
 time_t, signed 32-bit — the last normal second

   bit:   31   30                             0
        +----+--------------------------------+
        | S  |   31 magnitude bits           |   S=0: 0 .. 2147483647 s
        +----+--------------------------------+        = 1970 .. 2038-01-19
   2147483647 = 0x7FFFFFFF -> 2038-01-19 03:14:07 UTC (last future tick)
   2147483648 = 0x80000000 -> S=1, value -2^31  -> 1901-12-13 20:45:52 UTC
```

On 64-bit Linux `sizeof(time_t) == 8`: the wall is 292 billion years out.
The 32-bit exposure is embedded systems; `_TIME_BITS=64` (glibc 2.34+)
migrates them. Note the shape: an *unsigned* 32-bit counter would merely run
out in 2106; the signed reading turns the corner into 1901.

**Timezones/DST** are local law, not physics. US Eastern, 2025-03-09: 02:00
becomes 03:00; that wall-clock day is **23 hours** (82,800 s) and local
02:30 never happens; fall-back makes a **25-hour** day and 01:30 happens
twice. Derive the bugs: a job at 02:30 skips a day each year; differences of
local timestamps are off by an hour twice a year; durations must be computed
on UTC instants. CPython 3.12 hides a trap here — subtracting two `datetime`s
sharing one `tzinfo` object ignores the zone, so the 23-hour day computes as
24; use `.timestamp()` (lab below).

**Leap seconds:** UTC occasionally inserts `23:59:60` — a 61-second minute (27
insertions since 1972) — but POSIX defines a day as exactly 86,400 s and
*ignores* them; kernels reconcile by repeating or smearing a second (Google's
24-hour smear stretches each second ~11.6 ppm). Clocks that reconcile
differently disagree by 1 s — enough to mis-order event logs.

## What insiders know

- **The promotion rule, exactly:** on any 32-bit-`int` target no `u16`
  arithmetic ever executes — §6.3.1.1 computes in `int`. The `u16 * u16`
  danger is *signed* UB in the intermediate; the cast arrives one instruction
  too late.
- **UB overflow policy per language:** C/C++ signed = UB, unsigned = wrap;
  Java = wrap by spec, `Math.addExact` throws; C# = wrap unless `checked`;
  Rust = panic in debug, wrap in release (`overflow-checks = true` flips it);
  Go = wrap by spec; JS integers are float64, exact only to 2^53 − 1;
  Python never overflows (arbitrary precision, module 01 ch14).
- **The money-in-decimal rule:** cents in `int64` or a true decimal —
  `Decimal`, `BigDecimal`, `NUMERIC`, COMP-3. Binary floats fail at 0.1 *by
  construction*; output rounding doesn't undo accumulated input error.
- **The UTF-8 slicing rule:** a cut is safe only where the next byte is not
  `10xxxxxx`. Rust `str::is_char_boundary`, Go `utf8.RuneStart`, Python
  slicing on `str` (never `bytes`) encode exactly that check.
- **Collections don't use `==`:** CPython's `list.remove`/`in` fast-path on
  identity; Java's `Double.equals` compares bits — NaN behaves differently
  inside a collection than inside an expression, in both languages, in
  opposite directions.
- **Patriot's actual numbers:** 24-bit register, 0.1 s short by 9.5e-8 s,
  0.3433 s after 100 hours, 687 m of gate error at ~1,676 m/s.
- **glibc `sscanf("%d")` on overflow:** UB per the standard, but glibc 2.41
  wraps mod 2^32 and returns success (n = 1); `strtol` saturates and sets
  `errno = ERANGE` — that's the parser you want.

## See it yourself

Four traps, one laptop. Stack: GCC 14.2, glibc 2.41, CPython 3.12, Debian
x86-64 (LP64); outputs from exactly it.

**1. The promotion overflow no warning catches:**

```bash
$ cat > promo.c <<'EOF'
#include <stdio.h>
#include <stdint.h>
int main(void) {
    uint16_t a = 65535, b = 65535;
    printf("c = %u, sizeof(a*b) = %zu\n", (uint32_t)(a * b), sizeof(a * b));
    return 0;
}
EOF
$ gcc -Wall -Wextra -O2 -o promo promo.c && ./promo
c = 4294836225, sizeof(a*b) = 4
$ gcc -O1 -g -fsanitize=undefined -o promo_ub promo.c && ./promo_ub
c = 4294836225, sizeof(a*b) = 4
promo.c:5:56: runtime error: signed integer overflow: 65535 * 65535 cannot be
represented in type 'int'
```

`-Wall -Wextra -O2`: silent. Only UBSan's per-operation check sees it — the
wrapped value matching the unsigned product is luck UB *permits*, not a
guarantee. `sizeof(a*b) = 4` is the promotion.

**2. Float money and the NaN protocols:**

```bash
$ python3 -c "print(0.1 + 0.2); print(0.1 * 3 == 0.3); \
from decimal import Decimal; print(Decimal('0.10') + Decimal('0.20') == Decimal('0.30'))"
0.30000000000000004
False
True
$ python3 -c "nan = float('nan'); print(nan == nan, [nan] == [nan], \
[nan] == [float('nan')])"
False True False
```

**3. Byte-slicing a UTF-8 string:**

```bash
$ python3 -c "b = 'café'.encode(); print(b, len(b)); print(b[:4]); \
print(b[:4].decode())"
b'caf\xc3\xa9' 5
b'caf\xc3'
Traceback ...: UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc3
in position 3: unexpected end of data
$ python3 -c "print('café'.encode().decode('latin-1'))"
cafÃ©
```

One cut crashes the decoder; a mis-declared encoding never crashes — decide
which failure you prefer.

**4. Parsing and the 23-hour day:**

```bash
$ cat > parse.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
int main(void) {
    int d = 0, n = sscanf("3000000000", "%d", &d);
    printf("sscanf: n=%d d=%d\n", n, d);
    errno = 0;
    long v = strtol("99999999999999999999", NULL, 10);
    printf("strtol: v=%ld errno=%d\n", v, errno);
    return 0;
}
EOF
$ gcc -o parse parse.c && ./parse
sscanf: n=1 d=-1294967296
strtol: v=9223372036854775807 errno=34
$ python3 -c "from datetime import datetime; from zoneinfo import ZoneInfo; \
NY = ZoneInfo('America/New_York'); \
s1 = datetime(2025,3,8,12,tzinfo=NY).timestamp(); \
s2 = datetime(2025,3,9,12,tzinfo=NY).timestamp(); \
print('spring wall-clock day:', int(s2-s1), 'seconds')"
spring wall-clock day: 82800 seconds
```

82800 = 23 × 3600: the day the clock skips. And `n = 1` means "success" — the
only wrong thing in the program is the number.

## How to use this knowledge

- **Code review triage:** every `malloc(a * b)`, narrowing cast, `float` near a
  currency symbol, `[:80]` on user text, and local-time subtraction now reads
  as a decision to verify — with a one-line question: *what is the widest
  value that reaches this line?*
- **Incident analysis:** "totals off by 3 cents," "usernames collide," "logs
  mis-ordered by a second," "files dated 1901" — each symptom names its
  mechanism here; you find the line in minutes.
- **Detector choice:** UBSan for signed overflow and bad conversions, decimal
  diff tests for money, decode round-trips at encoding boundaries,
  `.timestamp()`-based tests around DST dates — and fuzzing for parser
  boundaries (chapter 05).
- **Interview answers that derive themselves:** signed overflow is UB because
  the optimizer buys algebra; 0.1 + 0.2 ≠ 0.3 because 1/10 is infinite in
  base 2; 2038 is January because 2^31 − 1 s ≈ 68.1 years; NaN breaks
  collections because their equality protocol isn't `==`.

## Connections

- **Module 01 ch08 (C: the explicit model)** — this chapter is that contract in
  motion: UB as the price of raw bytes with no runtime checks.
- **Module 03 ch01 (the bug from first principles)** — the four-family
  taxonomy; this is family three: spec-vs-behavior divergence at type
  boundaries.
- **Module 03 ch05 (how bugs are found)** — UBSan's per-operation checks and
  parser fuzzing are the detectors named throughout this chapter.
- **Module 08 ch03 (COBOL data division)** — COMP-3 packed decimal: exact
  base-10, the ledger world's answer to the float half of this chapter.
- **Module 10 ch02 (UB as license)** — the optimizer passes that consume
  signed-overflow UB, from the compiler author's side.

## Quiz

1. **Concept.** C defines unsigned overflow to wrap but leaves signed overflow
   undefined. What does each choice buy, and why does `if (t + 1 < t)` compile
   to nothing at `-O2`?
2. **Concept.** Why is `uint16_t a = 65535, b = 65535; a * b` *signed*
   undefined behavior while `uint32_t x = 65535, y = 65535; x * y` is
   well-defined? Name the rule.
3. **Predict the output.** glibc 2.41, x86-64: what do `n` and `d` hold after
   `int d = 0; int n = sscanf("3000000000", "%d", &d);`? What does
   `strtol("3000000000", NULL, 10)` return here, and on a 32-bit-`long`
   platform?
4. **Predict the output.** CPython 3.12, one object `nan = float("nan")`:
   `0.1 + 0.2 == 0.3`, `[nan] == [nan]`, `[nan] == [float("nan")]`,
   `-0.0 == 0.0`?
5. **Predict the output.** `b = "café".encode()`: what is `len(b)`, and what
   does `b[:4].decode()` raise (give the reason string)?
6. **Bug diagnosis.** A billing service sums millions of row amounts as
   `double`, rounds the final sum to cents, and is off by cents on some days.
   Why doesn't rounding at the end fix it, and what is the minimal correct
   change?
7. **Bug diagnosis.** `malloc(w * h * 4)` succeeds for attacker-chosen
   `w = 40,000, h = 20,000`, then the pixel loop `for (i = 0; i < w*h; i++)`
   corrupts the heap. Where did the failure *begin*, and what check belongs
   before the `malloc`?
8. **Cross-chapter.** A 32-bit embedded system guards its `time_t` with
   `if (t + 1 < t) reboot();`. Using module 10 ch02's "UB as license" plus this
   chapter's derivation: why may the guard be dead code, what date does the
   unguarded value reach, and what does it become one second later?

<details>
<summary>Answers</summary>

1. Defined wrap makes unsigned arithmetic pure mod-2^N math — cheap, portable,
   no machine check. UB buys the optimizer number-line algebra: `i + 1 > i`,
   loop induction without carry checks. Under "signed overflow never
   happens," `t + 1 < t` is provably false, so GCC `-O2` deletes the branch —
   the license the guard polices erases the guard. Use
   `__builtin_add_overflow` or `-fwrapv`.
2. C11 §6.3.1.1 integer promotion: `a` and `b` compute in `int`;
   4,294,836,225 > INT_MAX → signed overflow → UB. `x * y` are already
   `int`-rank, stay `uint32_t`, and §6.2.5 ¶9 defines mod-2^32 wrap —
   harmlessly here. The cast in `(uint32_t)(a * b)` applies after the UB, so
   it rescues nothing.
3. `n = 1`, `d = -1294967296` (3,000,000,000 mod 2^32, signed reading) — UB
   per the standard, wrapped silently by glibc with success reported.
   `strtol` saturates: on LP64 it returns 3000000000, errno = 0 (fits); on
   32-bit `long`, LONG_MAX = 2,147,483,647 with `errno = ERANGE` — the
   detectable contract `sscanf` lacks.
4. `False` — 0.1 and 0.2 each round, and the sum's nearest float64 is
   0.30000000000000004. `True` — list comparison uses
   `PyObject_RichCompareBool`, which fast-paths on identity — same object.
   `False` — different NaN objects reach `==`, and NaN is
   unordered. `True` — −0.0 is numerally 0.0; the sign bit differs
   (`1/-0.0` = −inf).
5. `len(b) = 5`: 4 characters, but `é` is two bytes. `b[:4]` is
   `b'caf\xc3'` — a 2-byte lead byte cut from its continuation — so decode
   raises `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc3 in
   position 3: unexpected end of data`.
6. The error is inside the *inputs*: `double` has no exact 0.01, every add
   rounds, millions of adds accumulate cents of drift, and the final round
   snaps an already-wrong sum. Minimal change: accumulate `int64_t` cents
   (or decimal/`NUMERIC`) end to end — the stored values become exact, so
   the drift disappears.
7. In the `malloc` argument: 40,000 × 20,000 × 4 = 3.2e9 > INT_MAX — signed
   UB in `int` math; it wraps to a small value, the allocation succeeds at
   the wrong size, and the loop writes 800,000,000 pixels through the
   undersized window (module 02 ch04's target). Check before the call, per
   factor, in `size_t`: `if (h != 0 && w > SIZE_MAX / h / 4) return NULL;`.
8. `t + 1` is signed overflow — UB — so the optimizer may assume it never
   happens and delete the comparison (module 10 ch02): the reboot never
   runs. Unguarded, the counter reaches 2^31 − 1 s after the epoch —
   2038-01-19 03:14:07 UTC. One second later the sign bit flips, the value
   reads −2^31, and dates jump to 1901-12-13 20:45:52 UTC. Fix: widen
   `time_t` (`_TIME_BITS=64` on 32-bit glibc 2.34+); never check with
   arithmetic whose truth requires the overflow.

</details>
