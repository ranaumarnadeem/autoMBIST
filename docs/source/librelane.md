# LibreLane hardening

autoMBIST's generated RTL is plain synthesizable SystemVerilog — it hardens
through any flow. This page documents the path we've actually proven:
[LibreLane](https://librelane.org) 3.0.5, targeting a real
[OpenRAM](https://github.com/VLSIDA/OpenRAM)-compiled sky130 macro.

## What's been proven

A realistic three-memory subsystem — differently-sized sky130 SRAM macros,
each wrapped in the autonomous MBIST + self-repair block described in
{doc}`example` — hardens **clean**:

- **Detailed routing: 0 violations**
- **LVS-clean, including power**
- Antenna and overlap checks clear
- Die: 0.91 mm² (self-repair-wrapped variant), 7,189 standard cells across the
  three repair-wrapped memories

This isn't a toy design: it's the actual self-repair RTL from {doc}`introduction`,
placed and routed around real, spare-augmented memory macros — and the same
recipe isn't march-C-specific. It's now proven clean against real OpenRAM
macros for two more hand-written algorithms, plus a full SoC built on top of
the same self-repaired memories:

- **March-X** wrapping `sky130_sram_32b256w` (`selfrepair_x`, 900×600 µm die,
  25% placement density) — hardens clean.
- **MATS+** wrapping `sky130_sram_32b512w` (`selfrepair_mp`, same die size and
  density) — hardens clean.
- **The SoC** (`soc_top_hw` — an unmodified RV32I core, PicoRV32, driving two
  real self-repair-wrapped macros through actual fetch/load/store traffic,
  not just a bus testbench) — 1400×900 µm die, 25% density — hardens clean.

march-1r1w (the multi-port self-repair scaffold) is the one exception: it
needs a genuinely dual-port memory, and none of this project's real sky130
macros are dual-port, so it hasn't been hardened against a real macro — see
"What isn't proven yet" below.

## The recipe

Five fixes were needed to get an OpenRAM macro cleanly through LibreLane's
macro-integration path — none of them exotic once known, but each one silently
breaks the harden if missed. The `autombist harden` command bakes all five in
by default:

1. **LEF units.** OpenRAM's LEF declares `DATABASE MICRONS 2000`, but its
   coordinates — and the GDS — are already on the 1 nm grid LibreLane's sky130A
   tech expects. The declaration alone needs fixing (`autombist fix-lef-units`);
   the GDS needs no change.
2. **Hard-IP signoff stance.** `MAGIC_DRC_USE_GDS: false` and
   `RUN_KLAYOUT_XOR: false` — treat the macro as hard IP whose internals are
   the memory generator's own signoff responsibility, not the integrator's.
3. **Power-domain hookup.** `PDN_MACRO_CONNECTIONS` takes *design nets*, then
   *macro pins* — on sky130 that's `"<instance> VPWR VGND vccd1 vssd1"`.
   Getting the net names wrong makes the power-delivery step treat the macro
   as an anonymous obstacle instead of a powered block.
4. **PDN halos**, distinct from placement halos — `PDN_HORIZONTAL_HALO` /
   `PDN_VERTICAL_HALO` (≈15 µm) keep the core power straps clear of the
   macro's edge pins, where OpenRAM's signal pins sit as thin met4 pads.
5. **Macro-internal DRC treated as non-fatal.** `ERROR_ON_MAGIC_DRC: false`
   and `ERROR_ON_KLAYOUT_DRC: false` — OpenRAM's own bitcell/periphery cells
   use GDS layer/datatype pairs (`CFOMDROP`, `CNTMADD`, and similar) that are
   legitimate sky130 mask-operation layers internal to the macro's geometry,
   but aren't in the local Magic/KLayout DRC deck, so they get flagged as
   "unknown layer/datatype in boundary" — a known OpenRAM/open_pdks
   integration quirk
   ([librelane/librelane#519](https://github.com/librelane/librelane/issues/519)),
   not a defect in autoMBIST's own design. Both checkers default to fatal
   upstream, so without this a macro-containing design fails signoff on
   macro-internal noise regardless of whether the design's own logic is
   clean — LVS and antenna still gate normally either way.

```bash
autombist fix-lef-units macros_1000/sky130_sram_32b256w.lef
autombist harden --config flow/multimem/mbist/harden.yml --run
```

Full config format and every flag: `harden` in {doc}`cli-reference`.
Worked examples of the same recipe in the repository:
[`flow/multimem/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/multimem)
(march-C, three memories), [`flow/newalgo/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/newalgo)
(March-X and MATS+, one real macro each), and
[`flow/soc/hardened/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/soc/hardened)
(the RV32I SoC).

## Macro-internal signoff

`macro-signoff` runs magic DRC + netgen LVS on a compiled OpenRAM macro
directly — the macro-internal check owed when a macro is generated with `-n`
(no inline DRC/LVS, the default for fast iteration):

```bash
autombist macro-signoff sky130_sram_32b256w
```

## What isn't proven yet

Full timing signoff (a real Liberty `.lib` for each macro — today's runs
black-box memory timing) and a merged full-hierarchy DRC pass that includes
macro polygons directly are both open. See {doc}`challenges` for what we ran
into trying to close those, and {doc}`roadmap` for where they sit.

march-1r1w's self-repair wrapper is also not part of the "proven" list above.
It's a genuinely dual-port design (one read-only port, one write-only port),
and none of this project's three real sky130 macros are dual-port — they're
all single-port r/w. Building a real dual-port OpenRAM macro to wrap is a
separate undertaking, not a LibreLane config change, and is left as future
work rather than rushed here; see `flow/newalgo/README.md`'s "march-1r1w:
scope decision" section for the full reasoning.
