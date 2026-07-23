# autombist CLI Reference — Classic Path

This page documents the **classic path**: the array-level MBIST flow built around
`generate` / `simulate` / `run`, plus the supporting `grade-controller`, `ram-synth`,
`init`, and `smoke` commands. For the functional fault-primitive engine and the
interactive research shell (`autombist test`, `autombist algo`), see
[algo-shell-guide.md](algo-shell-guide.md).

All commands are exposed under the single `autombist` entry point:

```bash
autombist --help
autombist --version
autombist COMMAND --help
```

`--version` is a top-level, eager flag (`autombist --version`) — it prints the
installed autombist version and exits before any command runs.

> **Platform note.** `autombist generate` (wrapper/RTL emission) and config parsing
> run anywhere Python 3.10+ runs. `simulate`, `run`, and `grade-controller --run`
> invoke Icarus Verilog / Cocotb / Yosys / FaultFlow and only work on Linux or WSL,
> using a venv created inside WSL/Linux.

---

## generate

Generate the MBIST wrapper, core MBIST RTL, and (optionally) fault masks for one memory.

### Syntax

```bash
autombist generate [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--config PATH` | `./config.yml` if it exists | YAML config file with memory parameters |
| `--out PATH` | `out` | Output directory where generated files will be written |
| `--test` / `--no-test` | `--no-test` | Generate fault-injection saboteur wrapper and fault masks (use with `--faults`) |
| `-r, --faults INTEGER` | `50` | Number of random faults to inject (only with `--test`) |
| `--seed INTEGER` | none | Random seed for reproducible fault injection (optional) |
| `--fault-type TEXT` | `stuck-at` | Fault model: `stuck-at` (SA0/SA1), `transition-up`, `transition-down`, or `port-coupling` (march-1r1w only; march-2rw supports stuck-at/transition only) |
| `--pulse-width-ns INTEGER` | `2` | Pulse width in clock cycles for transition faults |
| `--algo TEXT` | `march-c` | MBIST algorithm: `march-c`, `march-raw`, `march-1r1w`, or `march-2rw` |
| `--help` | | Show this message and exit |

If `--config` is omitted, autombist looks for `config.yml` in the current working
directory and errors out if it isn't found there.

### Config-file keys

The `--config` YAML file carries the memory parameters (`memory_name`,
`wrapper_module_name`, `addr_width`, `data_width`, `we_active_low`, `ports:`, and
the optional `redundancy:`/`repair_ports:` blocks). One easy-to-miss key:

- **`read_latency`** (integer, default `1`) — how many cycles the controller
  waits after issuing a read before sampling `dout`. It **must match your memory
  model's read timing**: `1` for this project's 2-stage-registered behavioral
  fixtures, **`0` for a real OpenRAM macro** (whose `dout` decays shortly after
  each edge). A mismatch makes MBIST/self-repair report failures on a good
  memory — the usual symptom is a "phantom" unrepairable during on-chip
  self-repair against a real macro.

See the [Configuration reference](https://ranaumarnadeem.github.io/autoMBIST/configuration.html)
on the docs site for the full key-by-key reference.

### Examples

```bash
autombist generate --config config.yml
autombist generate --config my_sram.yml --out results --algo march-raw
autombist generate --config config.yml --test --faults 100 --seed 42
autombist generate  # uses ./config.yml when present
```

A concrete worked example against this repo's own sample config:

```bash
autombist generate --config config.yml --out out --test --faults 50 --seed 1234 \
  --algo march-c --fault-type stuck-at
```

### Output

Written under `out/<memory_name>/` (e.g. `out/input_demo_8x16_scn4m/`):

- `<memory_name>_mbist.v` — the main wrapper module
- `mbist_algo.sv`, `mbist_fsm.sv`, `mbist_top.sv` — core MBIST RTL
- `march_c/` or `march_raw/` (etc.) — algorithm-specific RTL for the selected `--algo`
- `config.yml` — a snapshot of the resolved config (also used by `simulate`/`run` to
  locate the module directory when you pass a parent `--out`)
- With `--test`:
  - `<memory_name>_saboteur.v` — fault-injection wrapper
  - `faults/*.hex` — fault masks (e.g. `sa0_faults.hex`, `sa1_faults.hex`,
    `tf_up_faults.hex`, `tf_down_faults.hex` depending on `--fault-type`)
  - `Makefile` — local simulation Makefile consumed by `autombist simulate`

---

## simulate

Run MBIST simulation with Cocotb + Icarus Verilog against a previously generated
output directory.

### Syntax

```bash
autombist simulate [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--out PATH` | `out` | Output directory containing generated autombist output |
| `--verbose` | off | Print full simulator console output and detailed logs |
| `--min-coverage FLOAT` | none | Fail (exit 1) if array fault coverage is below this percent |

`--out` may point either directly at a module directory (one containing
`*_mbist.v` plus a `config.yml` or fault `Makefile`) or at a parent directory —
autombist searches immediate subdirectories and auto-resolves to the single
matching module directory, erroring if none or multiple are found.

If the module directory contains a generated fault `Makefile` (from
`generate --test`), `simulate` runs the fault-simulation path; otherwise it runs
the clean-simulation path.

### Examples

```bash
autombist simulate --out out
autombist simulate --out out --verbose
autombist simulate --out out/input_demo_8x16_scn4m
```

### Output

- `out/<memory_name>/simulate.log` — full simulator output
- `out/<memory_name>/reports/latest.json` — structured JSON report (see schema below)
- `out/<memory_name>/reports/report.txt` — plain-text human report
- Terminal summary with coverage metrics

Exits with code 1 (after printing the error in red) if the underlying simulation
fails, or if `--min-coverage` is set and the reported array fault coverage falls
below the threshold.

---

## run

Convenience command: `generate` immediately followed by `simulate` (and optionally
`grade-controller`) in one invocation.

### Syntax

```bash
autombist run [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--config PATH` | `./config.yml` if it exists | YAML config file with memory parameters |
| `--out PATH` | `out` | Output directory for all generated files and results |
| `--test` / `--no-test` | `--no-test` | Generate and run fault injection simulation |
| `-r, --faults INTEGER` | `50` | Number of faults to inject |
| `--seed INTEGER` | none | Random seed for reproducible fault injection |
| `--fault-type TEXT` | `stuck-at` | Fault model: `stuck-at`, `transition-up`, `transition-down`, or `port-coupling` (march-1r1w only; march-2rw supports stuck-at/transition only) |
| `--pulse-width-ns INTEGER` | `2` | Pulse width in clock cycles for transition faults |
| `--algo TEXT` | `march-c` | MBIST algorithm: `march-c`, `march-raw`, `march-1r1w`, or `march-2rw` |
| `--verbose` | off | Print full simulator console output and detailed logs |
| `--faultflow` / `--no-faultflow` | `--no-faultflow` | After sim, grade the MBIST controller logic with FaultFlow (Linux/WSL) |
| `--faultflow-repo PATH` | none (env var `FAULTFLOW_HOME`) | FaultFlow repo path (or set `FAULTFLOW_HOME`) |
| `--cell-lib TEXT` | `sky130` | FaultFlow standard-cell library: `sky130` or `osu035` |
| `--scan-chains INTEGER` | `1` | Scan chains for controller grading |
| `--min-coverage FLOAT` | none | Fail (exit 1) if array fault coverage is below this percent |

When `--faultflow` is set, `run` internally calls the same controller-grading flow
as `grade-controller`, fixed at `--threshold 90.0` and `--max-rounds 20`.

### Examples

```bash
autombist run --config config.yml --test
autombist run --config config.yml --test --faults 200 --algo march-raw --seed 999
autombist run --config config.yml --test --fault-type transition-up --verbose
autombist run  # uses ./config.yml when present
```

### Output

- `out/<memory_name>/` — all generated wrapper and RTL files (same as `generate`)
- `out/<memory_name>/reports/latest.json` — simulation results
- `out/<memory_name>/reports/report.txt` — plain-text human report
- `out/<memory_name>/simulate.log` — simulator output
- If `--faultflow` is set: a `faultflow/` bundle under the module directory, and
  `controller_grading` merged into `reports/latest.json` (see schema below)

---

## grade-controller

Grade the MBIST **controller logic** (not the memory array) with FaultFlow's scan
stuck-at ATPG, with the memory macro blackboxed.

### Syntax

```bash
autombist grade-controller [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--out PATH` | `out` | Output directory containing a generated (clean) MBIST wrapper |
| `--faultflow-repo PATH` | none (env var `FAULTFLOW_HOME`) | FaultFlow repo path (or set `FAULTFLOW_HOME`) |
| `--cell-lib TEXT` | `sky130` | FaultFlow standard-cell library: `sky130` or `osu035` |
| `--scan-chains INTEGER` | `1` | Number of scan chains for controller grading |
| `--threshold FLOAT` | `90.0` | Target coverage percent for ATPG |
| `--max-rounds INTEGER` | `20` | Maximum progressive ATPG rounds |
| `--run` / `--no-run` | `--run` | Run the bundle (needs Yosys + FaultFlow); `--no-run` only emits it |

Like `simulate`, `--out` accepts either a module directory directly or a parent
directory to auto-resolve.

This command always emits a self-contained, re-runnable bundle under
`out/<memory_name>/faultflow/` (blackbox stub, Yosys script, FaultFlow `.ofs`,
`run_faultflow.sh`). Unless `--no-run` is given, it additionally synthesizes the
collar and runs scan stuck-at ATPG, then reports controller structural coverage
and merges it into the module's latest simulation report.

Requires (Linux/WSL only): Yosys, and a built FaultFlow at `--faultflow-repo` (or
`$FAULTFLOW_HOME`). FaultFlow is invoked from its own venv.

### Examples

```bash
autombist grade-controller --out out --faultflow-repo ~/faultflow
autombist grade-controller --out out --no-run     # just emit the bundle
```

### Output

- `out/<memory_name>/faultflow/` — the re-runnable bundle (blackbox stub, Yosys
  script, FaultFlow `.ofs`, `run_faultflow.sh`)
- With `--run` (default): `controller_grading` merged into
  `out/<memory_name>/reports/latest.json`, and a terminal line reporting
  `detected/denominator (coverage%)` plus `excluded_blackbox` count
- With `--no-run`: only the bundle is emitted; the terminal prints the bundle path
  and the exact command to run it manually on Linux/WSL
  (`FAULTFLOW_HOME=<path> bash <bundle>/run_faultflow.sh`)

---

## ram-synth

Synthesize an SRAM macro through OpenRAM using a YAML config, instead of hand-writing
OpenRAM command-line arguments.

### Syntax

```bash
autombist ram-synth [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--config PATH` | `openram.yml` | OpenRAM synthesis config file |
| `--show-command` | off | Print synthesized command before execution |

### Examples

```bash
autombist ram-synth --config openram.yml
autombist ram-synth --config openram.yml --show-command
```

### Output

Runs the OpenRAM synthesis helper (under `scripts/`) with arguments built from the
config file, streaming OpenRAM's own stdout/stderr to the terminal. The generated
SRAM macro is written to the `output_root`/`output_name` location configured in the
OpenRAM config (defaults to an `input/` directory at the repo root). Exits with
OpenRAM's own return code on failure.

---

## init

Scaffold a new autombist project: starter `config.yml`, `openram.yml`, `Makefile`,
and a sample SRAM model, all in one shot.

### Syntax

```bash
autombist init [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--out PATH` | `.` | Directory where starter files will be created |
| `--force` | off | Overwrite existing files |

Without `--force`, `init` refuses to overwrite any file that already exists at the
target path (fails with exit code 1 naming the conflicting file).

### Examples

```bash
autombist init
autombist init --out my_project
autombist init --force
```

### Output

Written to `--out` (default: current directory):

- `config.yml` — starter MBIST config (`memory_name: sram_1rw`,
  `wrapper_module_name: sram_1rw_mbist`, `addr_width: 10`, `data_width: 32`,
  `we_active_low: true`, single-port `ports:` map for `clk0`/`addr0`/`din0`/`dout0`/
  `we0`/`csb0`)
- `openram.yml` — starter OpenRAM synthesis config
- `Makefile` — a project Makefile with `ram-synth`, `generate`, `simulate`, `run`,
  and `smoke` targets that shell out to the `autombist` CLI
- `sram_1rw.v` — a sample behavioral SRAM model matching the default config's ports,
  meant to be replaced with your real OpenRAM-generated macro

The command prints each created file path, followed by a short "Next steps" block
(edit `config.yml`, replace `sram_1rw.v`, run `autombist generate`).

---

## smoke

Run built-in smoke checks exercising generation (and optionally simulation) across
all supported fault modes and algorithm families, to validate an autombist
installation.

### Syntax

```bash
autombist smoke [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--run-sim` / `--no-sim` | `--run-sim` | Run cocotb/iverilog simulation smoke check |
| `--keep-artifacts` | off | Keep generated smoke workspace |
| `--out PATH` | none (temp dir) | Optional workspace path for smoke artifacts |
| `--faultflow` / `--no-faultflow` | `--no-faultflow` | Also emit + verify a FaultFlow controller-grading bundle (emit-only; no Yosys/FaultFlow needed) |

If `--out` is not given, `smoke` uses a temporary directory that is deleted on exit
unless `--keep-artifacts` is set (in which case a temp directory is created but kept,
and its path is printed).

### Examples

```bash
autombist smoke
autombist smoke --no-sim
autombist smoke --keep-artifacts --out smoke_workspace
```

### Output

`smoke` exercises, in order, and prints a `[smoke] ... : PASS` (or `FAIL`, exiting
with code 1) line for each stage:

1. Clean generation, march-c
2. Clean generation, march-raw (checks `march_raw_top` appears in the wrapper)
3. Stuck-at fault generation, march-c (checks `sa0_faults.hex`/`sa1_faults.hex`,
   the saboteur wrapper, and the fault `Makefile` all exist)
4. Transition-up fault generation, march-raw (checks `tf_up_faults.hex` and
   `tf_up_mask` in the saboteur)
5. Transition-down fault generation, march-raw (checks `tf_down_faults.hex` and
   `tf_down_mask` in the saboteur)
6. OpenRAM config parse (loads and builds command args from the starter
   `openram.yml`, no actual OpenRAM invocation)
7. If `--run-sim` (default): small fault simulations (`faults=8`, `seed=42`) for
   stuck-at/march-c, transition-up/march-raw, and transition-down/march-raw,
   confirming each `simulate` run completes
8. If `--faultflow`: emits (but does not run) a FaultFlow controller-grading
   bundle against a fake repo stub, and checks the blackbox stub, Yosys script,
   `.ofs`, and `run_faultflow.sh` all exist under `faultflow/`

At the end it prints the workspace path and `[smoke] All checks passed`.

---

## harden

Emit (and optionally run) a LibreLane RTL-to-GDS config for a design plus its
OpenRAM sky130 macros, with the proven macro-integration recipe baked in:
hard-IP signoff flags (`MAGIC_DRC_USE_GDS=false`, `RUN_KLAYOUT_XOR=false`),
placement + PDN halos (15 µm), and the `PDN_MACRO_CONNECTIONS` net-vs-pin format
(`<instance> VPWR VGND vccd1 vssd1`). See
[`flow/multimem/`](../flow/multimem) for the design this was proven on.

### Syntax

```bash
autombist harden [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--config PATH` | `harden.yml` | Compact harden config (design + macros); see [`flow/multimem/harden.yml`](../flow/multimem/harden.yml) |
| `--out PATH` | `librelane-config.json` | Where to write the generated LibreLane config |
| `--pdk-root PATH` | `~/.ciel` | ciel-managed PDK root (used with `--run`) |
| `--run` | off | Actually invoke LibreLane (needs nix + PDK). Default only writes the config. |

The config maps a `design_name`, `verilog_files`, `clock_port`/`clock_period`,
optional `die_area`, and a list of `macros` (each `{name, gds, lef, instance,
location}`) into a full LibreLane config. Point `gds`/`lef` at the
**units-normalized** LEF (run `fix-lef-units` first; the GDS needs no change).

```bash
autombist harden --config flow/multimem/harden.yml            # emit config only
autombist harden --config flow/multimem/harden.yml --run      # emit + run LibreLane
```

## fix-lef-units

Normalize an OpenRAM LEF's `DATABASE MICRONS` declaration to the sky130A grid
(1000). OpenRAM declares 2000 dbu even though its coordinates — and the GDS — are
already on the 1 nm grid, which LibreLane's sky130A tech rejects/mis-scales. This
is a declaration-only fix (plus a defensive snap of any ≥4-decimal coordinate);
**the GDS is left untouched.**

```bash
autombist fix-lef-units macro.lef                  # overwrite in place
autombist fix-lef-units macro.lef --out fixed.lef  # write a copy
```

## macro-signoff

Run magic DRC + netgen LVS on generated OpenRAM macros — the macro-internal
signoff owed when a macro was compiled with `-n` (no inline DRC/LVS). Wraps
[`flow/multimem/signoff/run_macro_signoff.sh`](../flow/multimem/signoff/run_macro_signoff.sh);
requires `magic`/`netgen` on `PATH` and the sky130 PDK.

```bash
autombist macro-signoff                          # the multimem macro set
autombist macro-signoff sky130_sram_32b256w      # a specific macro dir
autombist macro-signoff --show-command           # print, don't run
```

---

## JSON report schema (`reports/latest.json` / `reports/results.json`)

Both `simulate` and `run` write the same structured report via
`build_simulation_report()` / `write_simulation_report()`
(`src/autombist/reporting.py`). Top-level keys:

| Key | Type | Description |
|---|---|---|
| `schema_version` | string | Currently `"1.2.0"` |
| `generated_at` | string | UTC ISO-8601 timestamp |
| `tool_version` | string | autombist package version |
| `status` | string | `"pass"` or `"fail"` (derived from `returncode`) |
| `returncode` | int | Simulator process return code |
| `runtime_seconds` | float | Wall-clock runtime of the simulation invocation |
| `command` | list[string] | The exact command line that was executed |
| `cwd` | string | Working directory the command ran in |
| `log_path` | string | Path to `simulate.log` |
| `report_path` | string | Path to this report (`reports/latest.json`) |
| `results_path` | string | Path to `reports/results.json` |
| `config` | object | `memory_name`, `wrapper_module_name`, `addr_width`, `data_width`, `we_active_low` |
| `simulation` | object | `use_saboteur`, `faults_requested`, `fault_seed`, `fault_type`, `pulse_width_ns`, `algo` |
| `fault_metrics` | object | `detected_faults`, `total_fault_sites`, `coverage_percent`, `injected_faults` (parsed from simulator stdout/stderr) |
| `transition_metrics` | object | Only present when `fault_type` is `transition-up`/`transition-down`: `model`, `algo`, `direction`, `blocked_write_cycles`, `read_verifications`, `detected_bit_events`, `resolved_before_read_bits`, `pending_overwrites`, `unverified_pending_writes`, `max_pending_depth`, `top_blocked_addresses`, `top_detected_addresses` |
| `fault_details` | list[object] | Per-fault-site records parsed from `FAULT_SITE {json}` lines in simulator output (sparse; empty list if the simulator emitted none) |
| `fail_bitmap` | list[object] | **Opt-in, present only after a functional fail scan** (`run_simulation(fail_scan=True)`): the observation-derived set of failing cells, each `{"addr": int, "bit": int}`, parsed from `FAIL_CELL {json}` lines. Reports every cell that read wrong through the functional port, independent of any injected fault list — the input a redundancy-analysis (BIRA) step consumes. Absent (not `[]`) on ordinary runs, so those reports stay byte-identical. |
| `junit` | object | Parsed JUnit XML: `path`, `exists`, `summary` (`tests`/`failures`/`errors`/`skipped`/`time_seconds`), `tests` (list), `system_out` (list) |
| `summary` | string | Rendered human-readable multi-line summary (same text printed to the terminal) |
| `controller_grading` | object | Only present after `grade-controller`/`run --faultflow` merges it in: FaultFlow's `detected`, `denominator`, `coverage_percent`, `excluded_blackbox` |

`reports/report.txt` is a plain-text rendering of the same report (`render_text_report()`),
intended for humans rather than tooling.
