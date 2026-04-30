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

1. Python 3.10+
2. OpenRAM-generated memory and matching config file
3. For fault simulation:
	- Icarus Verilog (`iverilog`) installed system-wide
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

## Basic Generation

Generate MBIST outputs using the default output directory (`out`):

```bash
autombist --config config.yml --out out
```

If `--out` is omitted, `out/` is used by default.

## Fault Simulation Flow

Generate fault-enabled artifacts (saboteur + fault masks + module Makefile):

```bash
autombist --config config.yml --out out --test --faults 50 --seed 1234 --algo march-c --fault-type stuck-at
```

This command injects random `SA0`/`SA1` faults into the memory model and writes fault files to:

```text
out/<memory_name>/faults/
```

Then run simulation from the generated module directory:

```bash
cd out/<memory_name>
make
```

For transition fault simulation, set `--fault-type transition-up` or `--fault-type transition-down` and run the generated module Makefile the same way.

For verbose simulation output:

```bash
make debug
```

## Synthesis

Use the generated wrapper and MBIST RTL files in your synthesis flow (for example, Yosys or equivalent EDA tools).

## CLI Help

```bash
autombist --help
autombist --version
```
