---
orphan: true
---

# autoMBIST demo — zero to result

A copy-pasteable walkthrough of the three things autoMBIST does: generate + fault-simulate
an MBIST wrapper, grade a march algorithm against the functional fault model, and harden a
self-repairing memory subsystem to GDS. Run everything from inside **WSL or Linux** (the
simulators/PDK toolchain are not visible to a Windows-native Python).

## 0. Install + sanity check

```bash
nix develop              # from the repo root; puts autombist on PATH
autombist doctor         # toolchain check: make/iverilog/verilator/yosys/nix/magic/... on PATH?
autombist --help         # 14 commands
autombist smoke          # generation + OpenRAM config parse + small fault sims
```

## 1. Classic path — generate an MBIST wrapper and measure fault coverage

Needs `iverilog` + `cocotb` on PATH.

```bash
# Scaffold a runnable starter project (config.yml, openram.yml, Makefile):
autombist init --out .

# Generate the MBIST wrapper + march-C controller RTL, then simulate it:
autombist run --config config.yml --out out

# Or inject faults into a saboteur copy and measure detection:
autombist generate --config config.yml --out out --test --faults 50 --seed 1234 \
    --algo march-c --fault-type stuck-at
autombist simulate --out out/<memory_name>
# -> out/<memory_name>/results.json  (coverage_percent, detected/total, fail-bitmap)
```

## 2. Research path — grade a march algorithm against the 19-primitive fault model

Needs `verilator` on PATH. No real memory macro or config required.

```bash
autombist test --addr-width 8 --data-width 8 --algo march_c  --faults faults.txt
autombist test -aw 10 -dw 32     --algo march_ss --faults faults.txt --report cov.md

# Interactive research shell (register custom algorithms/faults, compare marches):
autombist algo
```

See [Fault coverage](https://github.com/ranaumarnadeem/autoMBIST/blob/main/README.md#fault-coverage) for the measured MATS+ / March C- / March SS
detection matrix this path produces.

## 3. Redundancy repair + RTL-to-GDS closure (sky130)

Generate a self-repair wrapper per memory and harden the assembled multi-memory subsystem
through LibreLane. The proven recipe + honest caveats live in
[`flow/multimem/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/multimem) and [`flow/multimem/mbist/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/multimem/mbist).

```bash
# One self-repair wrapper per memory (march-C + on-chip BIRA/BISR + row remap):
autombist generate --algo march-c --out gen_a --config - <<'YAML'
memory_name: "sram_wrap_a"
wrapper_module_name: "selfrepair_a"
addr_width: 8
data_width: 32
we_active_low: true
read_latency: 0          # REQUIRED for the real OpenRAM macro; the default of 1
                         # is for this project's toy fixtures and would make
                         # self-repair phantom-fail on the real macro.
ports: {clk: clk0, addr: addr0, din: din0, dout: dout0, we: web0, csb: csb0}
redundancy: {num_spare_rows: 1, num_spare_cols: 0, onchip_selfrepair: true}
YAML

# Normalize an OpenRAM macro LEF (2000 -> 1000 dbu) if needed:
autombist fix-lef-units macro.lef

# Harden the assembled top with the proven macro recipe (drives LibreLane):
autombist harden --config flow/multimem/mbist/harden.yml --run
```

Reference result (from [`flow/multimem/mbist/README.md`](https://github.com/ranaumarnadeem/autoMBIST/blob/main/flow/multimem/mbist/README.md)):
the self-repair-wrapped subsystem closes in LibreLane 3.0.5 at **0.91 mm² die, 7,189 std
cells, 0 detailed-routing violations, LVS-clean including power** (589 Magic / 3194 KLayout
macro-internal DRC, explicitly non-fatal by config — a known OpenRAM/open_pdks quirk, not a
design defect; see {doc}`librelane`). The same three macros
without the MBIST wrap (from [`flow/multimem/README.md`](https://github.com/ranaumarnadeem/autoMBIST/blob/main/flow/multimem/README.md)) harden
the same way at **0.78 mm² die, ~51% memory area, 4,158 std cells.**
