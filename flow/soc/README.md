# flow/soc — a real RV32I CPU booting through self-repaired memory

A minimal SoC-level proof for autoMBIST's on-chip self-repair: a genuine
RV32I core ([PicoRV32](https://github.com/YosysHQ/picorv32), vendored
unmodified) fetches instructions from, and loads/stores through, two
independently self-repair-wrapped memories — each with a real, baked-in hard
defect. Everything else in this project proved repair works on a memory's own
functional bus, driven directly by a testbench; this proves it survives real
CPU fetch/load/store traffic, on a design close to what a real chip looks like.

## Files

- `vendor/picorv32/picorv32.v` + `LICENSE` — PicoRV32, unmodified, vendored at
  a pinned commit recorded in `vendor/picorv32/VERSION` (ISC license).
- `sram_spares_soc_instr.v`, `sram_spares_soc_data.v` — defect-injectable
  behavioral memories (32-bit word, matching PicoRV32's native word size),
  siblings of `tests/hardware/sram_spares_intmem_*.v`.
- `soc_top.sv` — the integration: PicoRV32 + a small valid/ready bus bridge +
  two self-repair-wrapped memories + a status-register mailbox.
- `gen_program.py` — a tiny local RV32I assembler (just enough of the ISA:
  LUI, ADDI, SW, LW, BEQ, BNE) and the test firmware itself. No RV32I
  toolchain dependency.

The cocotb test lives at `tests/hardware/test_soc_selfrepair.py` (with a
standalone runner at `tests/hardware/run_soc_selfrepair_tb.py`), per this
repo's convention that all cocotb tests live under `tests/hardware/`
regardless of which `flow/` example they exercise.

## Memory map

| Region | Base | Size | Notes |
|---|---|---|---|
| Instruction memory | `0x0000_0000` | 64 words (32-bit) | read-only from the bus; 1 spare row |
| Data memory | `0x0000_1000` | 32 words (32-bit) | read/write; 1 spare row |
| Status register | `0x0000_2000` | 1 word | firmware writes its PASS/FAIL signature here |

Each memory has one real, baked-in defect: instruction memory's is at word 63
(the last word, deliberately past the program's own length, so it's never
fetched); data memory's is at word 10, deliberately inside the range the
firmware actually writes and reads back through real `sw`/`lw` instructions.

## The firmware

`gen_program.py` builds a small program: write 16 words to data memory (each
word gets its own byte offset as its value), read them all back and compare,
then write a PASS or FAIL signature to the status register and loop forever.
Word 10 — the defect's address — is inside that range, so a real repair bug
would show up as the CPU's own comparison failing, not just a bypass-scan
catching it after the fact.

## Boot sequencing: repair runs before the CPU ever starts

`soc_top`'s `cpu_resetn` is gated off both memories' aggregate
`self_repair_done` (repair completed) **and** `!self_repair_busy` (the repair
controller has fully returned to idle) — mirroring how a real BISR chip runs
its power-on self-test to completion in its own reset domain before the
application core is ever released. The CPU never gets a chance to observe the
pre-repair, defective state.

This mattered in practice, not just in theory: gating on `self_repair_done`
alone left a one-cycle gap where the repair controller's internal test-mode
override was still routing each memory's functional port to the (by-then-idle)
MBIST path instead of real storage — the CPU would boot into that gap and
fetch zeroes. Requiring `!self_repair_busy` too closes it.

## Firmware loading has to happen *after* BIST, not before

The instruction memory's content can't simply be preloaded once, before
simulation starts: march-C's own BIST pass — the same pass that finds and
repairs the defect — runs across the *entire* array, including whatever the
program's own bytes would occupy. A preloaded firmware image would just get
overwritten by the BIST pass before the CPU ever read it. The cocotb test
instead backdoor-loads the firmware into the memory array directly, in the
one deterministic window where BIST has already finished (`self_repair_done`
is high) but the CPU is still held in reset (`self_repair_busy` hasn't
cleared yet) — the same ordering a real system would use if its boot image
were loaded by a bootloader or DMA engine only after POST completes.

## Running it

```bash
wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \
    PYTHONPATH=src python3 tests/hardware/run_soc_selfrepair_tb.py"
```

The test checks three independent things, matching this project's existing
"never trust a single signal" discipline: (1) both memories report
`self_repair_fail == 0`; (2) the CPU's own program reaches its PASS signature
— proof that real load/store traffic round-tripped correctly through the
repaired path; (3) a direct, CPU-independent peek at the raw physical cell the
defect was remapped to (computed from the wrapper's own repair registers, not
assumed), confirming the actual bits landed where the remap says they should.

## What this does and doesn't prove

This is a simulation-only proof — `soc_top` is not hardened through
LibreLane, and PicoRV32 is used as-is with no synthesis/timing work of its
own. It demonstrates functional, CPU-level transparency of on-chip self-repair
on a design shaped like a real SoC; it says nothing new about physical
closure (that's `flow/multimem/`) or real silicon defects (which, as
elsewhere in this project, can't be injected into a compiled macro
pre-silicon).

The bus bridge's address decode only covers the three mapped regions above;
any other word-aligned address would silently fall through to reading
`status_reg`, with no bus-error signal back to the CPU. The fixed firmware
never generates such an address, so this is dormant, not a live bug — but it
would matter for a more dynamic program, and isn't guarded against here.
`DATA_BASE`/`STATUS_ADDR` are also hand-kept in sync between `gen_program.py`
and `soc_top.sv` rather than sharing one source of truth; a future edit to
one side without the other would silently break the address mapping.
