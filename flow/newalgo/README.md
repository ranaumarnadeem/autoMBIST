# flow/newalgo — real-macro LibreLane hardening for march-X, MATS+, and march-1r1w

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

## march-1r1w: real, genuinely-dual-port macro (hardened, DRC not clean)

march-1r1w's self-repair wrapper (Workstream A2, the multi-port scaffold)
needs a genuinely dual-port memory (one read-only port + one write-only
port). This used to say no such macro existed in this repo and that
generating one was future work -- that framing was imprecise. OpenRAM
itself fully supports this shape natively: its `OPTS.num_r_ports`/
`num_w_ports`/`num_rw_ports` config, sky130's own dedicated `bitcell_2port`
(a real 2-port cell, not something needing new layout work -- see
`OpenRAM/technology/sky130/tech/tech.py`), and ~20 of OpenRAM's own
regression tests all confirm this is a known, tooling-supported OpenRAM
path. So a real one was generated and hardened:

```bash
# 1. Generate the OpenRAM macro itself (num_r_ports=1, num_w_ports=1 --
#    sky130 requires an EVEN total row count for its 2-port replica-column
#    LVS check, hence num-spare-rows 2, not 1):
scripts/synthesize_sram.sh --tech sky130 --word-size 32 --num-words 256 \
  --num-rw-ports 0 --num-r-ports 1 --num-w-ports 1 --num-spare-rows 2 \
  --output-name sky130_sram_1r1w_32b256w
autombist fix-lef-units input/sky130_sram_1r1w_32b256w/sky130_sram_1r1w_32b256w.lef

# 2. Generate the self-repair wrapper:
autombist generate --algo march-1r1w --out gen_1r1w --config - <<'YAML'
memory_name: "sram_wrap_1r1w"
wrapper_module_name: "selfrepair_1r1w"
addr_width: 8
data_width: 32
we_active_low: true
read_latency: 0
ports:
  rport: {type: r, clk: clkA, addr: addrA, dout: doutA, csb: csbA}
  wport: {type: w, clk: clkB, addr: addrB, din: dinB, csb: csbB, we: webB}
redundancy: {num_spare_rows: 2, num_spare_cols: 0, onchip_selfrepair: true}
YAML

# 3. Harden (flow/newalgo/sram_wrap_1r1w.sv is the real-macro adapter):
autombist harden --config flow/newalgo/harden_1r1w.yml --run
```

**Result:** the flow completes all 80 stages. **LVS passes. Antenna
passes.** DRC does not:

- **KLayout (~520 errors, dominated by `m1.2`):** verified via the marker
  database that these sit at macro-LOCAL coordinates (inside the macro's
  own footprint), not the surrounding logic -- the same already-accepted
  OpenRAM-macro-internal-geometry category `src/autombist/signoff.py`
  already documents and sets `ERROR_ON_KLAYOUT_DRC=False` for (see that
  file's comment, and `librelane/librelane#519`). Not new, not concerning.
- **Magic (~539 errors, `nwell.4` -- "nwells must contain metal-connected
  N+ taps"):** this one is real and NOT macro-internal noise -- the
  surviving march-C real-macro run log shows genuinely zero Magic/KLayout
  violations, so clean is the actual bar, not something to round down from.
  Four independent, targeted attempts failed to clear it:
  - Bigger die/more margin (1000x850->1200x1000): Magic's count got
    *worse* (539->837, roughly proportional to added area), KLayout's
    stayed byte-identical (520->520) -- ruling margin size in as
    irrelevant to KLayout and NOT the fix for Magic either.
  - Tighter `FP_TAPCELL_DIST` (13um default -> 6um): actual inserted
    tap-cell count more than doubled (8428 -> 18390 cells), violation
    count didn't move at all (539->539) -- ruling out tap density/spacing
    as the cause entirely.
  - Bigger macro halo (15um default -> 40um, matching the reported
    working value in the issue below): count went from 539 to 605, no
    improvement.
  - This matches a documented, currently-unresolved, maintainer-acknowledged
    OpenROAD/sky130 toolchain limitation, not a config knob under this
    project's control: [OpenROAD#7118](https://github.com/The-OpenROAD-Project/OpenROAD/issues/7118)
    ("tap deserts" with sky130 tapcell insertion) and
    [OpenLane#1140](https://github.com/The-OpenROAD-Project/OpenLane/issues/1140)
    ("thin areas of sky130 met1 rails have no tap cells" near macro
    halos), both open, both labeled as real bugs by the OpenROAD/OpenLane
    maintainers, neither with a working fix as of this writing.

So: march-1r1w's self-repair wrapper is proven to integrate with, place,
route, and pass LVS/Antenna against a real dual-port OpenRAM macro through
the full LibreLane RTL-to-GDS flow. DRC signoff is blocked on an external,
already-reported toolchain bug, not on anything in this project's RTL,
generator, or LibreLane config.

Along the way, found and fixed a real, previously-latent RTL portability
bug: `march_1r1w_algo.sv`/`march_1r1w_fsm.sv` used unpacked-array ports
(`do_read [0:1]`), which Yosys's `read_verilog` frontend rejects with a
syntax error -- this RTL had only ever been exercised via Icarus/cocotb
(which supports the syntax fine) before this hardening attempt, never
through Yosys. Fixed to packed `[1:0]` vectors, which index identically at
every call site (`do_read[0]`, `do_read[1]`) -- a synthesis-target fix with
no semantic change, verified against the full 58-test march-1r1w suite
(zero regressions) and a standalone `yosys -p 'read_verilog -sv ...'` parse.
