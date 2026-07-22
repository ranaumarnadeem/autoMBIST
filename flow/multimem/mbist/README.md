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
  errors" originally reported here turned out to be a `magic` 8.3.623
  GDS-hierarchy tooling artifact (unstable violation counts across runs of
  the *same* geometry), not a real defect or a spares-related issue. Building
  `magic` 8.3.363 (the version OpenRAM's own CI pins) against the identical
  GDS gives zero violations, reliably. Top-level integration P&Rs LVS-clean;
  see [../signoff](../signoff) for the per-macro scripts.
- **Self-repair against the real macros has never actually been simulated
  here** — only elaboration-checked (see "Two build views" above). When it
  finally was, on a sibling design (`flow/soc/hardened/soc_top_hw.sv`, same
  `selfrepair_a`/`selfrepair_b` wrappers over these same real macros), it
  found a real bug: the generator's default `READ_LATENCY=1` for the
  internal march-C engine is tuned for this project's toy behavioral
  fixtures and is *wrong* for OpenRAM's real macro model, whose `dout0` is
  forced back to X shortly after every clock edge and only refreshed by an
  actual read (a realistic narrow-output-valid-window model). With the
  default, march-C samples one cycle too late and spuriously declares a
  defect-free macro unrepairable. `read_latency: 0` (an existing but
  previously-unused generator option) fixes it — confirmed by actually
  running self-repair against these macros in simulation. This file's own
  `harden.yml`/"Reproduce" config above does **not** set it, so regenerating
  these exact wrappers today would carry the same bug. Not yet updated here;
  tracked as a follow-up.
