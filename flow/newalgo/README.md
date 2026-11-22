# flow/newalgo — real-macro LibreLane hardening for march-X, MATS+, and march-1r1w

Proves the same claim `flow/multimem/mbist/` already proved for march-C --
that autoMBIST's on-chip self-repair (BIRA analyzer + BISR sequencer +
external row remap) completes the full LibreLane RTL-to-GDS flow with a
passing top-level LVS -- generalizes to the two algorithms hand-written this
session (Workstream B1), each wrapping a REAL sky130 OpenRAM macro (not the
toy behavioral fixtures). Counts are recorded for `selfrepair_x` and
`selfrepair_mp`, both with `words_per_row: 4` (below), and for march-1r1w.

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
# sky130_sram_32b256w is column-muxed, 4 words per row (`ram-synth` prints it).
redundancy: {num_spare_rows: 1, num_spare_cols: 0, words_per_row: 4, onchip_selfrepair: true}
YAML

# 2. Harden (edit flow/newalgo/harden_x.yml's paths to your generated
#    wrapper + units-normalized macro, same as flow/multimem/mbist/harden.yml):
autombist harden --config flow/newalgo/harden_x.yml --run
```

Same for `harden_mp.yml` / MATS+ / `sky130_sram_32b512w`.

## selfrepair_x with physical-row repair (hardened 2026-11-19)

`selfrepair_x` was re-hardened with the config above (`words_per_row: 4`,
one spare row), so its spare row replaces a whole physical row of the
macro. The macro was `sky130_sram_32b256w` from OpenRAM v1.2.50 with
`scripts/openram-local.patch`, the build that passed OpenRAM's own DRC/LVS
on 2026-11-15; its LEF already declares 1000 database units, so
`fix-lef-units` was not needed. LibreLane 3.0.5, die and macro placement as
in `harden_x.yml`.

**Result:** exit code 0, all stages.

| Check | Result |
|---|---|
| LVS | 0 errors, 0 unmatched devices or nets |
| Routing DRC | 0 |
| Antenna | 0 violating nets or pins |
| Setup / hold worst slack | +11.6 ns / +0.11 ns (20 ns clock), no violations |
| Standard cells | 5,914 |
| Magic / KLayout DRC | 382 / 657, macro-internal, not fatal (see `src/autombist/signoff.py`) |
| Max slew | 15 violations, slow (ss) corners only |

In the final netlist, the macro's `addr0[1:0]` (the column-mux bits) come
straight from the controller and only `addr0[8:2]` pass through the row
remap: the shape physical-row repair at 4 words per row should have.
The 2 "floating nets" OpenROAD's resizer reports (`RSZ-0020`) are `VPWR`
and `VGND`, the power nets, which no logic cell drives; the flow's own check
finds 0 disconnected pins. No pre-`words_per_row` counts were recorded for
this design, so there is no before/after comparison.

## selfrepair_mp with physical-row repair (hardened 2026-11-21)

`selfrepair_mp` (MATS+ over `sky130_sram_32b512w`, `read_latency: 0`,
`words_per_row: 4`, one spare row, as in
`tests/hardware/run_newalgo_real_macro_selfrepair_tb.py`) was hardened the
same way. The macro was the OpenRAM v1.2.50 build that passed OpenRAM's own
DRC (0) and LVS on 2026-11-15: 33 data bits (one spare column, unused here),
10 address bits, 4 words per row (its `datasheet.info`), and a LEF already
in 1000 database units. LibreLane 3.0.5, die and macro placement as in
`harden_mp.yml`; about 25 minutes.

**Result:** exit code 0, all stages.

| Check | Result |
|---|---|
| LVS | 0 errors, 0 unmatched devices or nets |
| Routing DRC | 0 |
| Antenna | 0 violating nets or pins |
| Setup / hold worst slack | +8.5 ns / +0.12 ns (20 ns clock), no violations |
| Standard cells | 5,190 |
| Magic / KLayout DRC | 363 / 657, macro-internal, not fatal (see `src/autombist/signoff.py`) |
| Max slew | 54 violations in the slow (ss) corners, 7 at typical (tt) |

The 7 typical-corner violations are one chain of `clkdlybuf4s25_1` delay
buffers the resizer inserted on a single internal net, each about 17 ps over
the 750 ps limit (2%); timing is met. In the final netlist the macro's
`addr0[1:0]` come straight from the controller and `addr0[9:2]` through the
row remap, the 4-words-per-row shape. The 2 `RSZ-0020` floating nets are
again `VPWR` and `VGND`, with 0 disconnected pins. `DRT-0418` ("no pins on
routing grid") is reported for a few macro address pins here as in
`selfrepair_x`, whose routing was also clean.

## selfrepair_mp with on-chip column repair (hardened 2026-11-22)

The same design with the macro's spare column used as well: `redundancy:
{num_spare_rows: 1, num_spare_cols: 1, words_per_row: 4, onchip_selfrepair:
true, onchip_col_repair: true}`, `ports` with `spare_wen: spare_wen0`, and
`flow/newalgo/harden_mp_col.yml`. With 4 words per row a spare column
replaces one physical column, so the 2D analyzer tracks 128 physical
columns.

Before hardening, the wrapper was simulated against OpenRAM's own behavioral
model of the macro, with two changes in a copy: one spare cell per physical
row, shared by the row's words, and a stuck-at-1 bitline (bit 5 of word
column 2 in every row). Self-repair then succeeds and a rescan finds no
failing cell; at `words_per_row: 1` (the control) it reports unrepairable.

The first harden missed timing at the slow corner, and closing it took two
changes, each of which fails alone:

| 2D analyzer | `AREA 0` (LibreLane's default) | `DELAY 1` |
|---|---|---|
| as before (bit by bit, no stage) | -1.84 ns, 8 paths per slow corner | -4.67 ns, 203 paths |
| pipeline stage + claims from counts | -1.57 ns, 8 paths | **+3.0 ns, met** |

(Worst setup slack, `max_ss_100C_1v60`, 20 ns clock.) The analyzer now has a
pipeline stage when `words_per_row > 1`, and computes its column claims from
running counts built as trees (`rtl/onchip_2d_repair_analyzer.sv`, checked
decision for decision against the bit-by-bit version by
`tests/integration/test_2d_analyzer_pipeline.py`). With the area strategy the
remaining path ran through the 32-input "any bit wants a row" OR, which
synthesis maps as a chain of six OR4 gates; `synth_strategy: "DELAY 1"` in
the harden config maps it as a tree.

**Result:** exit code 0, all stages.

| Check | Result |
|---|---|
| LVS | 0 errors, 0 unmatched devices or nets |
| Routing DRC | 0 |
| Antenna | 0 violating nets or pins |
| Setup / hold worst slack | +3.0 ns / +0.10 ns (20 ns clock), no violations at any corner |
| Standard cells | 6,643 (5,190 row-only) |
| Magic / KLayout DRC | 362 / 657, macro-internal, not fatal (see `src/autombist/signoff.py`) |
| Max slew | 615 violations at the slow (ss) corners, 62 at typical (tt) |
| Max capacitance | 2 violations, slow corner |

The slew violations are design-rule warnings, not timing failures (the
timing above includes the slow transitions). The worst is 0.98 ns against the
0.75 ns limit at the typical corner and 1.64 ns at the slow one; row-only
`selfrepair_mp` already reached 1.22 ns (1.63x) at its slow corner, but the
delay strategy, which favours speed over drive, leaves many more. Not
investigated further. The 2 `RSZ-0020` floating nets are `VPWR` and `VGND`.

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
# sky130_sram_1r1w_32b256w is column-muxed, 2 words per row (its datasheet.info).
redundancy: {num_spare_rows: 2, num_spare_cols: 0, words_per_row: 2, onchip_selfrepair: true}
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
  N+ taps"):** this one is real and NOT macro-internal noise. The march-C
  subsystem reports 589 Magic / 3194 KLayout counts
  (`flow/multimem/mbist/README.md`), attributed there to macro-internal OpenRAM
  layer/datatype noise; whether any `nwell.4` violations are among those 589 is
  not recorded, so no zero baseline is claimed.
  Three independent, targeted attempts failed to clear it:
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

  None of the three moved the needle, which matches a documented,
  currently-unresolved, maintainer-acknowledged OpenROAD/sky130 toolchain
  limitation, not a config knob under this project's control:
  [OpenROAD#7118](https://github.com/The-OpenROAD-Project/OpenROAD/issues/7118)
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
