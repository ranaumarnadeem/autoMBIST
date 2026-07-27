# flow/newalgo — real-macro LibreLane hardening for march-X and MATS+

Proves the same claim `flow/multimem/mbist/` already proved for march-C --
that autoMBIST's on-chip self-repair (BIRA analyzer + BISR sequencer +
external row remap) closes DRC/LVS/timing through the full LibreLane
RTL-to-GDS flow -- generalizes to the two algorithms hand-written this
session (Workstream B1), each wrapping a REAL sky130 OpenRAM macro (not the
toy behavioral fixtures).

Each design is a single generated self-repair wrapper used directly as the
LibreLane `DESIGN_NAME` (no multi-macro subsystem needed): `selfrepair_x`
(march-X over `sky130_sram_32b256w`) and `selfrepair_mp` (MATS+ over
`sky130_sram_32b512w`) -- same real-macro-adapter substitution trick as
`flow/multimem/mbist/sram_wrap_a.sv`/`sram_wrap_b.sv` (a hand-written module
named after `memory_name` that instantiates the real macro in place of the
generated `sram_model_spares.sv` toy fixture).

## Functional proof (done, Icarus)

`tests/hardware/test_newalgo_real_macro_selfrepair.py` +
`run_newalgo_real_macro_selfrepair_tb.py` regenerate both wrappers with
`read_latency: 0` (REQUIRED for the real macro's dout timing -- same fix
documented in `flow/multimem/mbist/README.md`) and prove: self-repair's own
BIST runs clean on the defect-free real macro (no phantom fail, no phantom
`row_repair_en`), and ordinary functional access round-trips correctly
afterward. Both pass.

## Reproduce (LibreLane)

```bash
# 1. Generate each self-repair wrapper (same shape as flow/multimem/mbist/,
#    new algo):
autombist generate --algo march-x --out gen_x --config - <<'YAML'
memory_name: "sram_wrap_x"
wrapper_module_name: "selfrepair_x"
addr_width: 8
data_width: 32
we_active_low: true
read_latency: 0
ports: {clk: clk0, addr: addr0, din: din0, dout: dout0, we: web0, csb: csb0}
redundancy: {num_spare_rows: 1, num_spare_cols: 0, onchip_selfrepair: true}
YAML

# 2. Harden (edit flow/newalgo/harden_x.yml's paths to your generated
#    wrapper + units-normalized macro, same as flow/multimem/mbist/harden.yml):
autombist harden --config flow/newalgo/harden_x.yml --run
```

Same for `harden_mp.yml` / MATS+ / `sky130_sram_32b512w`.

## march-1r1w: scope decision (not hardened here)

march-1r1w's self-repair wrapper (Workstream A2, the multi-port scaffold)
needs a genuinely dual-port memory (one read-only port + one write-only
port) -- none of the three real sky130 macros in this repo are dual-port
(they're all single-port r/w, accessed here through a muxed `web0`). A real
dual-port OpenRAM macro isn't available and generating one is a real
OpenRAM-synthesis undertaking, not a quick LibreLane config change.

Hardening march-1r1w's controller logic alone against the existing
synthesizable dual-port behavioral model (`sram_model_1r1w.sv`-style, no
hard macro -- Yosys would infer/synthesize it as flip-flops rather than
blackbox it) is a reasonable follow-on and is left as future work rather than
rushed here; see the forward-looking optimization/research plan for where
this is tracked.
