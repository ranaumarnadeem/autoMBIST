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
  wrappers + 3 real macros).

## Reproduce

```bash
# 1. Generate a self-repair wrapper per memory (repeat for b:9/32, c:10/8):
autombist generate --algo march-c --out gen_a --config - <<'YAML'
memory_name: "sram_wrap_a"
wrapper_module_name: "selfrepair_a"
addr_width: 8
data_width: 32
we_active_low: true
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

- **Repair addressing on column-muxed macros**: the row remap steers a faulty
  logical address to `2**addr_width + i`. This matches OpenRAM's spare-row
  physical address for the no-column-mux case; the 32-bit macros use column
  muxing, so their spare-row physical address needs per-macro confirmation
  before the repair is trusted *functionally on the real macro* (the behavioral
  `sram_model_spares` loop, which the tests exercise, is unambiguous).
- **Macro-internal DRC is not clean** — OpenRAM's own DRC reports thousands of
  errors on the spared macros (see [../signoff](../signoff) and the repo memory
  notes). Top-level integration P&Rs LVS-clean; the macro internals are an open
  OpenRAM-generator issue, not introduced here.
