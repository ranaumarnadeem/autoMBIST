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
placed and routed around real, spare-augmented memory macros.

## The recipe

Four fixes were needed to get an OpenRAM macro cleanly through LibreLane's
macro-integration path — none of them exotic once known, but each one silently
breaks the harden if missed. The `autombist harden` command bakes all four in
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

```bash
autombist fix-lef-units macros_1000/sky130_sram_32b256w.lef
autombist harden --config flow/multimem/mbist/harden.yml --run
```

Full config format and every flag: `harden` in
[`docs/cli-reference.md`](https://github.com/ranaumarnadeem/autoMBIST/blob/main/docs/cli-reference.md);
the worked example this recipe was proven on:
[`flow/multimem/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/multimem)
in the repository.

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
