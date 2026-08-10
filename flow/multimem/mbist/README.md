# flow/multimem/mbist — the subsystem with self-repair MBIST on every memory

`mem_subsystem_mbist.sv` is the [flow/multimem](../) 3-memory subsystem with an
autonomous on-chip self-repair MBIST wrapper around **each** memory — the tool's
whole purpose, on the realistic multi-memory design.

Each memory becomes an autombist-generated `selfrepair_<x>` wrapper
(`redundancy: {num_spare_rows: 1, onchip_selfrepair: true}`): march-C controller
+ on-chip BIRA (`onchip_row_repair_analyzer`) + on-chip BISR sequencer
(`onchip_selfrepair_ctrl`) + external row remap, around a spare-augmented memory.
A single broadcast `self_repair_start` runs all three; the top reports aggregate
`bist_done`(all) / `bist_fail`(any) / `self_repair_*`.

| Slot | Wrapper | Memory | logical A/D |
|---|---|---|---|
| u0 | `selfrepair_a` | `sky130_sram_32b256w` (1 KB) | 8 / 32 |
| u1 | `selfrepair_b` | `sky130_sram_32b512w` (2 KB) | 9 / 32 |
| u2 | `selfrepair_c` | `sky130_sram_8b1024w` (1 KB) | 10 / 8 |

## Two build views (same as the plain subsystem)

- **Self-repair loop simulation**: autombist's generated wrapper build uses
  `sram_model_spares` (with a forced defect) — the inject→detect→analyze→repair→
  re-pass loop is covered per memory by the Step E `test_onchip_selfrepair*`
  suites. `mem_subsystem_mbist` reuses those wrappers unchanged.
- **Hardening**: the memory inside each wrapper is the `sram_wrap_<x>` adapter
  (here) over the real OpenRAM macro — bridges the wrapper's plain interface to
  the macro's 33-bit word (spare col tied off) and drives `wmask0` for the
  32-bit macros. Elaboration verified (`yosys hierarchy -check` clean: 3
  wrappers + 3 real macros), **functionally** verified with self-repair
  actually running against the real macro models
  (`tests/hardware/test_mem_subsystem_mbist.py`, `read_latency: 0` — see the
  caveats below), and now also hardened end-to-end: `autombist harden --run`
  on `harden.yml` below closes clean (589 Magic / 3194 KLayout
  macro-internal DRC, LVS clean, exit 0 — see the caveats below for why
  those DRC counts are non-fatal by explicit config, not luck).

## Reproduce

```bash
# 1. Generate a self-repair wrapper per memory (repeat for b:9/32, c:10/8):
autombist generate --algo march-c --out gen_a --config - <<'YAML'
memory_name: "sram_wrap_a"
wrapper_module_name: "selfrepair_a"
addr_width: 8
data_width: 32
we_active_low: true
read_latency: 0          # REQUIRED for the real OpenRAM macro (see caveats below);
                         # the default of 1 is for this project's toy fixtures and
                         # would make self-repair phantom-fail on the real macro.
ports: {clk: clk0, addr: addr0, din: din0, dout: dout0, we: web0, csb: csb0}
redundancy: {num_spare_rows: 1, num_spare_cols: 0, onchip_selfrepair: true}
YAML

# 2. Harden the assembled top with the proven macro recipe (dogfoods the CLI;
#    edit flow/multimem/mbist/harden.yml paths to your generated wrappers/macros):
autombist harden --config flow/multimem/mbist/harden.yml --run
```

The shared `march_c/` + `onchip_*` + `repair_remap_row.sv` RTL is identical
across the three wrappers (parameterized) — include ONE copy in the build, plus
the three `sram_wrap_*_mbist.v` wrappers, the three adapters here, the macro
blackboxes, and `mem_subsystem_mbist.sv`.

## Honest signoff caveats

- **Repair addressing on column-muxed macros** — checked, not just assumed:
  the row remap steers a faulty logical address to `2**addr_width + i`, which
  matches OpenRAM's spare-row physical address only when a macro has no
  column muxing (`words_per_row == 1`). Traced OpenRAM's own row-decoder
  wiring and confirmed all three real macros here (`sky130_sram_32b256w`,
  `32b512w`, `8b1024w`) have `words_per_row == 1` — each macro's `ADDR_WIDTH`
  matches the no-muxing formula `ceil(log2(words+spares))` exactly. So this
  *is* trusted functionally on these three real macros, not only on the
  behavioral `sram_model_spares` loop. The latent gap is only for a
  *future* macro that does trigger column muxing (repair would then remap
  just the one observed-faulty word address, not every word sharing that
  physical row) — not a live issue here.
- **Macro-internal DRC** was root-caused, not left open: the "thousands of
  errors" originally reported here are OpenRAM SRAM-macro bitcell/periphery
  cells using legitimate sky130 GDS layer/datatype pairs (e.g. `CFOMDROP`,
  `CNTMADD`) that the local Magic/KLayout deck flags as "unknown
  layer/datatype in boundary" — a known OpenRAM/open_pdks quirk
  (`librelane/librelane#519`), not a real defect or a spares-related issue.
  `Checker.MagicDRC`/`Checker.KLayoutDRC` default to *fatal* upstream
  (`ERROR_ON_MAGIC_DRC`/`ERROR_ON_KLAYOUT_DRC` both default `True`), so this
  noise would otherwise abort `harden --run` with exit code 2. `autombist
  harden`'s `build_librelane_config()` (`src/autombist/signoff.py`) sets both
  to `false` whenever the harden config declares `macros:` (as this one
  does), which is what makes these counts (589 Magic / 3194 KLayout here)
  non-fatal and lets `harden --run` exit 0 reproducibly.
  Top-level integration P&Rs LVS-clean; see [../](../) for the full recipe
  write-up and [../signoff](../signoff) for the per-macro scripts.
- **Macro-internal DRC/LVS signoff for these three macros' own GDS is
  currently unresolved — a separate, more serious question than the DRC
  noise above.** `../signoff/run_macro_signoff.sh` (the per-macro
  DRC/LVS check) was found to have never actually run against real PDK
  data — `PDK_ROOT` was set but not `export`ed, so magic's "clean" 0-count
  result meant "nothing was checked," not "nothing was found" (fixed in
  commit `fb2c1a3`). Fixing that surfaced a real LVS mismatch, root-caused
  (not merely worked around, commit `64076af`) to the vendored `OpenRAM/`
  checkout predating two upstream fixes (2026-04-28, 2026-05-14) for a
  wordline-pin-numbering defect in the sky130 replica bitcell array.
  Reproducing OpenRAM's own authoritative regeneration flow independently
  confirms the same failure inside OpenRAM's own module, before any
  openMBIST-authored logic runs. Net effect: until `OpenRAM/` is updated
  past those two commits, neither this script's raw-GDS check nor OpenRAM's
  own regeneration gives a clean signoff answer, and the three committed
  `.gds`/`.lvs.sp` pairs (generated with `-n`, back in July) remain
  genuinely unverified rather than confirmed either good or bad. This does
  not implicate autoMBIST's own generated RTL — physical macro signoff is
  the memory generator's responsibility, not the integrator's — and it does
  not affect the top-level P&R LVS-clean result above, which treats the
  macro as opaque hard IP. See `../signoff/run_macro_signoff.sh`'s header
  for the full root-cause writeup.
- **Self-repair timing against the real macros — `read_latency: 0` required,
  now fixed and tested.** This subsystem was originally only
  elaboration-checked (see "Two build views" above), never simulated with
  self-repair actually running. When it finally was, it exposed a real bug:
  the generator's default `READ_LATENCY=1` for the internal march-C engine is
  tuned for this project's toy behavioral fixtures (whose `dout` stays
  registered indefinitely once set) and is *wrong* for OpenRAM's real macro
  model, whose `dout0` is forced back to X shortly after every clock edge and
  only refreshed by an actual read (a realistic narrow-output-valid-window
  model). With the default, march-C samples one cycle too late and spuriously
  declares a defect-free macro unrepairable. `read_latency: 0` samples at the
  correct edge; the "Reproduce" config above now sets it. Verified end-to-end
  by `tests/hardware/test_mem_subsystem_mbist.py` (self-repair runs clean on
  all three real macros, no phantom fail/repair, and functional bus access
  still round-trips post-repair) — which also carries a negative control
  confirming it *does* fail at `read_latency: 1`, so the fix can't silently
  regress. Run it with `tests/hardware/run_mem_subsystem_mbist_tb.py`
  (Icarus-only, no LibreLane needed).
