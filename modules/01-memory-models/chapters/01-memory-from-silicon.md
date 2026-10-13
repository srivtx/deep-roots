# Chapter 01 — Memory from silicon

> **The 30-second version.** Memory is not an abstract "array of bytes" — it is a physical
> machine built from capacitors that leak, wires that are long, and grids that must be
> addressed in rows and columns. Every rule you have ever fought — alignment, endianness,
> the cache hierarchy, why 64-bit "only" has 48 usable address bits — is a direct
> consequence of that physics. This chapter builds memory from the transistor up so that
> the rest of the module is derivation, not memorization.

## The bit is a physical thing

Start at the bottom. A computer stores a bit in one of two ways, and the choice between
them explains almost everything about how computers feel.

**DRAM (dynamic RAM)** stores one bit as charge on a microscopic capacitor, guarded by a
single access transistor. The cell is tiny — one transistor and one capacitor, which is
why a DIMM today holds tens of billions of them. But capacitors leak. Within roughly
64 milliseconds, a charged cell drains enough that its value becomes ambiguous, so the
chip spends part of every cycle reading every row and writing it back refreshed. This is
*refresh*, and it silently steals bandwidth and power from you constantly — one of the
insider facts at the end of this chapter.

```
DRAM cell (1 bit)                     SRAM cell (1 bit, 6 transistors)

  word line ──┤ gate                   two cross-coupled inverters
              │                        (a latch that fights to keep its state)
  bit line ───●──── capacitor ── ⏚
              (charge = 1,             6 transistors, ~6x the silicon area
               no charge = 0)          of a DRAM cell, but no refresh, ~10x faster
```

**SRAM (static RAM)** stores a bit in a latch made of six transistors. It holds its state
as long as power flows — no refresh. It is also physically large and expensive, which is
why you get kilobytes of it near the CPU (registers, caches — Chapter 02) and gigabytes
of DRAM further away. The entire memory hierarchy is not a clever idea someone had; it
is the only thing you can build given the economics: fast memory is big-per-bit and
expensive, cheap memory is small-per-bit and slow.

## The memory grid, and what an address really is

DRAM is physically a two-dimensional grid. Cells at a row-column intersection. To read
one bit, the memory controller:

1. Activates a **row** (the whole row is sensed into a *row buffer* — hundreds of bytes
   come out together whether you wanted them or not),
2. Selects the **column** bits you actually asked for,
3. Transfers them over the **bus**.

The bus is wider than one bit and the transfer is a *burst*: DDR memory delivers a burst
of 8 transfers per read, 64 bits each — which is exactly **64 bytes**. Hold that number:
the cache line size of every mainstream CPU (Chapter 02) is 64 bytes because the DRAM
burst is 64 bytes. The hierarchy is not arbitrary; it is matched to the wire.

```
CPU ── memory controller ── channel ── rank ── bank (grid) ── row/col
                              │
                    address split:  | row bits | bank bits | column bits |
```

Channels are independent wires to separate sets of chips; interleaving successive
addresses across channels multiplies bandwidth. Banks are independent grids inside a
chip that can work in parallel. When people say "dual-channel memory," they mean two
wires that fetch in parallel — roughly twice the burst throughput, not twice the speed
of a single fetch.

## Byte addressing and word size

The CPU's memory interface addresses **bytes** — every address names one byte, even on a
64-bit machine. A 64-bit load reads 8 bytes starting at the given address. "Word size"
means the natural register width, which determines: how much arithmetic the CPU does per
instruction, which single load fills a register, and — crucially — **how wide a pointer
is**. A pointer is just a number whose unit is bytes, so a 64-bit machine needs 8 bytes
to store one.

Consequence you have already met: `sizeof(void*) == 8` on x86-64, and every array of
pointers strides by 8 (Chapter 06 will make you compute layout by hand).

## Endianness: a wiring decision that became a holy war

When you store the 32-bit value `0x04030201` at address 1000, which byte lands in
address 1000?

```
                     address:  1000   1001   1002   1003
little-endian (x86, ARM64):   01     02     03     04     (least significant first)
big-endian    (network, old SPARC/PowerPC era):   04     03     02     01
```

Little-endian is the natural wiring of an incrementing-address bus onto an integer
whose low byte is closest to ground; big-endian reads in the order humans write
numbers. It matters exactly twice in most careers — and both times it matters a lot:

- **Network byte order** is big-endian by history; every socket program calls
  `htonl`/`ntohl` (host-to-network-long) — the names *are* the endianness chapter.
- **Reading memory dumps or file formats**: `hexdump` shows you bytes; whether `11 22`
  is `0x1122` or `0x2211` depends on the writer's endianness — this is precisely how a
  COBOL COMP-3 field, a PNG header, and a stack-doomed pointer are all decoded by the
  same skill (Chapter 17's storage dumps, Chapter 21's exploit development).

## Alignment: the rule that generates padding, atomics, and crashes

Hardware does not want to fetch a 4-byte value that straddles two 8-byte lanes or two
cache lines. Each access "wants" its natural alignment: an `n`-byte scalar at an address
divisible by `n`.

| Type (x86-64)      | Size | Natural alignment |
|--------------------|------|-------------------|
| `char`             | 1    | 1                 |
| `short`            | 2    | 2                 |
| `int`, `float`     | 4    | 4                 |
| `long`, `double`, pointer | 8 | 8               |

Three consequences, each a chapter of this module in embryo:

1. **Padding**: a C struct with `char; int;` is 1 + 3 pad + 4 = 8 bytes, not 5.
   Chapter 06 turns this into a design discipline (field reordering) and shows real
   20-30% size wins.
2. **Atomicity**: on every mainstream CPU, an aligned load or store of a word is
   naturally atomic — no torn reads. The entire lock-free world (Chapter 20) is built
   on this one physical fact; misaligned "atomics" tear.
3. **Faults**: x86 tolerates misaligned access (slowly, sometimes splitting a cache
   line); ARM historically could raise an alignment fault. The C `__attribute__((packed))`
   struct that worked on your desktop segfaults on some embedded ARM — a bug class that
   only makes sense once you know alignment is a physical request, not a style rule.

## Address width: why "64-bit" means 47 usable bits

A 64-bit pointer could name 16 exabytes, but no machine wires 64 address lines. x86-64
with 4-level page tables uses 48-bit virtual addresses — 256 TB of address space per
process, which was astronomy in 2003 and is merely comfortable now. The hardware
requires the top 16 bits to be a sign extension of bit 47, producing the *canonical
address* split:

```
0x0000_0000_0000_0000  ..  0x0000_7fff_ffff_ffff   canonical low half   (user space)
                       hole (non-canonical, faults)
0xffff_8000_0000_0000  ..  0xffff_ffff_ffff_ffff   canonical high half  (kernel space)
```

This is why a userspace pointer on Linux looks like `0x7ffc...` and a kernel pointer
like `0xffff...` — a fact you will use in every core dump you ever read. ARMv8 also
started at 48-bit and grew an optional 52-bit (5-level) mode, same story, different
round number. The 48-bit limit is also the reason pointer tagging works: the top 16
bits are guaranteed-zero spare room, which V8, JVM compressed oops, and Lisp systems
all exploit (Chapters 12, 15, 06).

## Putting one load on the timeline

When your CPU executes `mov rax, [rsi]`, roughly this happens: the virtual address goes
to the MMU (Chapter 03) to become a physical address; the L1 data cache is checked
(4 cycles); on miss, L2 (~12), L3 (~40); on miss, a coherence check goes out to other
cores (Chapter 20); finally a DRAM read: activate the row, wait ~15 ns for the sense
amplifiers, burst 64 bytes over the bus. **A DRAM round trip is on the order of 60-100
nanoseconds — about 200-400 CPU cycles.** At 3 GHz, light itself travels only 10 cm per
cycle; your RAM is electrically a distant city, and the cache hierarchy (Chapter 02) is
the commuter rail built to hide that distance.

## What insiders know

- **Refresh steals your bandwidth**: DRAM refreshes every row every ~64 ms (tREFI ≈
  7.8 µs between refresh commands at standard temperature). Under heavy load, refresh
  can consume several percent of memory bandwidth; error-correcting and high-density
  chips refresh more.
- **Row-buffer locality is the hidden performance lever**: reading from the row
  currently open in a bank's row buffer is fast; switching rows costs a precharge +
  activate (~10s of ns). Access patterns that hop rows (pointer-chasing linked
  structures — Chapter 05) pay this constantly, even with "the same amount of data."
- **ECC DIMMs are 72-bit, not 64-bit**: 8 extra bits per burst store a Hamming code;
  the machine can correct 1-bit and detect 2-bit errors per burst. Most consumer
  boards simply don't wire the chip-selects for it.
- **`memset` of a huge buffer outruns a naive loop** not because of magic but because
  the runtime uses wide vector stores and, above a threshold, *non-temporal* stores
  that bypass the cache (Chapter 02) — writing 4 GB should not evict your entire cache.
- **A pointer is bytes like anything else**: leak it, and you have leaked an address —
  the atomic unit of the exploit economics in Chapter 21 (defeating ASLR).

## See it yourself

Everything in this chapter is observable from a shell on Linux:

```console
$ sudo dmidecode -t memory | grep -E 'Size|Speed|Locator' | head
        Size: 16 GB
        Speed: 5600 MT/s
        Locator: ChannelA-DIMM0           # channels are physical and visible here
$ cat /proc/meminfo | head -3
MemTotal:       16294968 kB                # ~16 GB minus reserved regions
MemFree:         ...
MemAvailable:    ...                       # free + reclaimable page cache
```

Endianness and alignment, in 20 lines of C:

```c
#include <stdio.h>
#include <stdint.h>
int main(void) {
    uint32_t x = 0x04030201;
    unsigned char *p = (unsigned char *)&x;
    printf("bytes: %02x %02x %02x %02x\n", p[0], p[1], p[2], p[3]);
    // x86-64/ARM64 Linux prints: 01 02 03 04  -> little-endian

    struct { char c; int i; char c2; } s;
    printf("sizeof = %zu, offsets = %zu, %zu, %zu\n",
           sizeof s, (size_t)&s.c - (size_t)&s, (size_t)&s.i - (size_t)&s, 0);
    // sizeof = 12 on x86-64: 1 char + 3 pad + 4 int + 1 char + 3 trailing pad
    return 0;
}
```

Compile with `gcc -Wall demo.c && ./a.out`, then flip the struct to
`int; char; char;` and watch it drop to 8 bytes. You have just derived Chapter 06's
first lesson from hardware.

## How to use this knowledge

- **Reading specifications**: "8-byte aligned," "wire format is big-endian,"
  "burst of 64" stop being incantations and become physical statements.
- **Performance intuition**: every slow data structure you will ever meet is slow
  because it fights one of these facts — pointer chasing (row/bank switches), poor
  locality (cache lines you don't use — Chapter 02), or allocation churn (Chapter 05).
- **Bug hunting**: crashes "only on ARM," torn reads under concurrency, and corrupt
  file parsers are all this chapter's material wearing a costume.
- **Interview fluency**: "why is a cache line 64 bytes?" now has a first-principles
  answer: it is the DRAM burst width, and the hierarchy is matched to the wire.

## Connections

- **Chapter 02** — SRAM economics + DRAM distance *are* the cache hierarchy.
- **Chapter 03** — the 48-bit canonical hole and the page as the OS's currency.
- **Chapter 06** — padding, field reordering, and SoA all derive from natural alignment.
- **Chapter 12 / 15 / 06** — compressed oops, V8 Smi tagging, and NaN-boxing all live
  in the spare bits that 48-bit addressing leaves in a pointer.
- **Chapter 20** — "aligned word access is atomic" is the physical floor of the memory
  model.
- **Chapter 17** — COBOL storage dumps are endianness + PICTURE byte decoding.

*Forward links — where the rest of the program picks this chapter up:*

- **Module 17 ch01 (transistors to gates)** — the DRAM cell this chapter drew is one MOSFET-passivated capacitor away from the CMOS logic this program bottoms out in.
- **Module 02 ch07 (side channels)** — Rowhammer hammers exactly this chapter's leaky capacitor — the physics you learned is the attack surface.
- **Module 20 ch01 (the no-MMU world)** — SRAM's 6T cell needs no refresh: the microcontroller's memory is this chapter's physics minus the DRAM tax.

## Quiz

1. **Concept.** Why is SRAM used for caches and DRAM for main memory, rather than all
   SRAM? Give both the economic and the physical reason.
2. **Concept.** A colleague insists memory is "just an array of bytes." Name two
   physical properties of DRAM that falsify the "just an array" model.
3. **Predict the output.** On x86-64 Linux, `uint16_t v = 0xBEEF;` — write the two bytes
   `hexdump` would show at `&v`, in order.
4. **Predict the number.** On x86-64, what is `sizeof(struct {char a; long b; char c;})`
   and where are the padding bytes?
5. **Predict the number.** A 3 GHz CPU with 48-bit canonical low addresses: what is the
   exact top address of user space in `0x0000...`-form?
6. **Bug diagnosis.** A firmware team's `struct __attribute__((packed)) {uint32_t x;}`
   pointer points to an odd address. The code works on x86 but faults on the ARM board.
   What two facts did the team conflate?
7. **Bug diagnosis.** A program copies a 12 GB buffer with a naive byte loop and the
   *rest* of the system slows measurably. What is the mechanism?
8. **Cross-chapter.** The DRAM burst is 64 bytes. Name the Chapter 02 structure whose
   size this determines, and explain what that structure transfers per miss.

<details>
<summary>Answers</summary>

1. SRAM's 6-transistor cell is ~6x the area of DRAM's 1T1C cell, so all-SRAM main memory
   would cost tens of times more per GB; physically, SRAM needs no refresh and drives
   short wires at register speed, while DRAM's capacitors leak (refresh tax) and live
   far away (wire latency). The hierarchy exists to get SRAM speed where it counts
   (caches) and DRAM density where it counts (capacity).
2. (a) Cells leak and must be refreshed — the array periodically rewrites itself, which
   a passive "array of bytes" never does; (b) the grid is addressed row-then-column with
   a row buffer — access has *shape*: row-buffer hits are much faster than row switches,
   and bursts deliver 64 bytes at a time, not 1.
3. `EF BE` — little-endian stores the least-significant byte first, so byte 0 of the
   value (0xEF) lands at the lower address, which `hexdump` prints first.
4. 24 bytes: `a` at 0 (1 byte), 7 bytes padding, `b` at 8 (8 bytes), `c` at 16, then
   7 trailing padding bytes so the struct's size is a multiple of its largest alignment
   (8) — needed so arrays of the struct keep every `b` aligned.
5. `0x00007fff_ffffffff` — bit 47 is the highest usable bit in the low canonical half,
   so the top user address is 2^47 − 1.
6. They conflated *tolerated* with *supported*: x86 chooses to handle misaligned
   accesses (possibly slowly) rather than fault; many ARM configurations (and strict
   alignment modes) raise an alignment fault. Packing removed the compiler's padding,
   but the hardware still wants `uint32_t` at a 4-divisible address — on x86 it's
   merely slow, on that ARM board it's a fault.
7. The byte loop brings every 64-byte line of the 12 GB source into the cache and
   writes the destination through the same lines, evicting everything else the system
   had cached (caches are shared machinery — Chapter 02). The runtime's `memcpy`
   switches to non-temporal stores above a threshold precisely to avoid poisoning the
   cache with streaming data.
8. The cache line (64 bytes). On a cache miss, the hardware fetches one full line from
   the next level — and ultimately the DRAM burst matches it, so one miss = one burst:
   the hierarchy's unit of currency at every level is the 64-byte line.

</details>
