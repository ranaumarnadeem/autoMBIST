# autombist

autombist automatically generates MBIST integration artifacts for OpenRAM-generated SRAM macros.
It builds a selectable MBIST wrapper around your memory interface, emits the required MBIST RTL files, and creates outputs under `out/` by default.

The generated artifacts can be synthesized with Yosys or other synthesis tools.
autombist also supports fault simulation by injecting stuck-at faults (`SA0` and `SA1`) or transition faults (`transition-up` and `transition-down`) and validating behavior through Cocotb with Icarus Verilog.

## What It Generates

For each memory in your config, autombist generates a module directory under `out/<memory_name>/` with:

- MBIST wrapper Verilog
- Required MBIST RTL support files
- Optional saboteur wrapper for fault injection (`--test` mode)
- Optional fault masks and a local simulation Makefile (`--test` mode)
- Algorithm-specific RTL for the selected `--algo` family

## Prerequisites

> **Platform.** `autombist generate` (wrapper/RTL emission) and config/algorithm-spec
> tooling run anywhere Python 3.10+ runs. Everything that invokes a simulator or
> synthesis tool — `simulate`, `run`, `test`, `algo`'s `run`/`compare_algo` commands, and
> `grade-controller --run` — needs the Unix EDA toolchain (Icarus Verilog, Verilator,
> Yosys, OpenRAM, and the optional FaultFlow flow) and only works on Linux or WSL. Use a
> venv created inside WSL/Linux for any of those.

1. Python 3.10+
2. OpenRAM-generated memory and matching config file
3. For fault simulation:
	- Icarus Verilog (`iverilog`) installed system-wide (array-fault `simulate`/`run`)
	- Verilator installed system-wide (functional fault-primitive `test`/`algo` commands)
	- Cocotb installed in your Python environment

## Installation

Install from the repository root:

```bash
python -m pip install .
```

For development:

```bash
python -m pip install -e .
```

Or you can install directly with Pypi:

```bash
python -m pip install autombist
```

## Quick Start

Generate MBIST outputs using the default output directory (`out`):

```bash
autombist generate --config config.yml --out out
```

Run generation + simulation in one step:

```bash
autombist run --config config.yml --out out
```

## Fault Simulation Flow

Generate fault-enabled artifacts (saboteur + fault masks + module Makefile):

```bash
autombist generate --config config.yml --out out --test --faults 50 --seed 1234 --algo march-c --fault-type stuck-at
```

This command injects random `SA0`/`SA1` faults into the memory model and writes fault files to:

```text
out/<memory_name>/faults/
```

Then run simulation:

```bash
autombist simulate --out out/<memory_name>
```

For transition fault simulation, set `--fault-type transition-up` or `--fault-type transition-down`, then run `autombist simulate --out out/<memory_name>`.

## Functional Fault-Primitive Grading (`test` / `algo`)

Beyond the array-level stuck-at/transition masks above, autombist ships a richer
functional fault library (19 primitives: stuck-at, transition, write/read disturb,
address-decoder, and all four coupling classes) and a programmable march-algorithm
engine, driven through Verilator instead of Icarus.

Grade a memory + march algorithm against a fault list in one shot:

```bash
autombist test --addr-width 8 --data-width 8 --algo march_c --faults faults.txt
autombist test -aw 10 -dw 32 --algo march_ss --faults faults.txt --report cov.md
```

Or validate an actual controller FSM (rather than an algorithm spec) against the same
fault library:

```bash
autombist test -aw 10 -dw 32 --fsm rtl/march_c/march_c_top.sv --faults faults.txt
```

For interactive exploration — registering custom algorithms/fault instances, running
campaigns, comparing against the built-in marches, and exporting reports or standalone
testbenches — launch the research shell:

```bash
autombist algo
autombist algo --script session.algo   # or '-' for stdin
```

Run `help` inside the shell for the full command list.

## Controller Structural Grading (`grade-controller`)

Grade the MBIST controller logic itself (not the memory array) with FaultFlow's scan
stuck-at ATPG, with the memory macro blackboxed:

```bash
autombist grade-controller --out out --faultflow-repo ~/faultflow
autombist grade-controller --out out --no-run     # just emit the re-runnable bundle
```

Requires Yosys and a built FaultFlow repo (path via `--faultflow-repo` or `$FAULTFLOW_HOME`),
Linux/WSL only.

## OpenRAM Synthesis + Starter Scaffolding

Generate starter files (`config.yml`, `openram.yml`, and `Makefile`) for a new project:

```bash
autombist init --out .
```

Synthesize SRAM through OpenRAM from config:

```bash
autombist ram-synth --config openram.yml
```

Show the exact OpenRAM command before execution:

```bash
autombist ram-synth --config openram.yml --show-command
```

Run installation smoke checks (generation + OpenRAM config parse + optional small fault simulations for coverage):

```bash
autombist smoke
autombist smoke --no-sim
```

## Synthesis

Use the generated wrapper and MBIST RTL files in your synthesis flow (for example, Yosys or equivalent EDA tools).

## CLI Help

```bash
autombist --help
autombist --version
```
