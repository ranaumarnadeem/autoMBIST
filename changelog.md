# Changelog

## 2026-04-27

- Packaged the generator as an installable `autombist` project under `src/autombist`:
  - Moved the generator, fault-mask logic, and Jinja templates into the package namespace.
  - Switched runtime template loading to `PackageLoader("autombist", "templates")`.
  - Replaced the legacy argparse CLI with a Typer-based `autombist` entry point that supports `--help` and `--version`.
- Updated verification and packaging plumbing for the packaged layout:
  - `tests/software/test_generator.py` now exercises the Typer app and packaged imports.
  - `tests/hardware/Makefile` and `tests/hardware/test_mbist.py` now import from `autombist`.
  - Added `pyproject.toml` packaging metadata for wheel/sdist builds and CLI-entry-point installation.
- Updated user-facing docs and generated flow text:
  - `README.md` now documents installation and CLI usage.
  - Generated fault-sim Makefile text now uses `autombist` labels.

## 2026-04-24

- Added modular parameterized MBIST RTL in /rtl:
  - mbist_algo.sv: March-C- operation generator by phase/substep.
  - mbist_fsm.sv: controller FSM for phase traversal, address march, read check, and done/fail signaling.
  - mbist_top.sv: integration boundary with OpenRAM-like active-low SRAM controls.
  - sram_model.sv: simple synchronous SRAM model for standalone verification.
  - mbist_tb.sv: smoke testbench for Icarus Verilog simulation.
- Updated mbist_algo.sv to use plain case for cleaner Icarus Verilog compatibility.
- Added root-level config and dependency bootstrap for wrapper generation:
  - config.yml: strict MBIST wrapper config schema with memory dimensions, wrapper module name, WE polarity flag, and SRAM port map.
  - requirements.txt: Jinja2, PyYAML, pytest, and cocotb dependencies.
- Added MBIST wrapper generation engine:
  - src/openmbist.py: argparse CLI (--config, --outdir), YAML validation with required-key checks, Jinja2 wrapper rendering, and MBIST RTL packaging copy step.
  - src/templates/wrapper_template.j2: parameterized SystemVerilog wrapper with functional/MBIST MUXing and dynamic SRAM port mapping.
- Added verification scaffolding:
  - tests/software/test_generator.py: pytest coverage for successful generation and missing/invalid config key failures.
  - tests/hardware/Makefile: cocotb + Icarus simulation setup for generated wrapper and packaged MBIST RTL.
  - tests/hardware/test_mbist.py: cocotb smoke test for reset/start flow and pass/fail assertion on bist_done.
  - tests/hardware/sram_1rw.v: schema-matching dummy SRAM model for cocotb runs.
- Updated cocotb hardware integration for WSL + cocotb 2.x:
  - tests/hardware/Makefile: resolve cocotb makefiles from cocotb_tools package using venv python, add wrapper module override, and use COCOTB_TEST_MODULES.
  - tests/hardware/test_mbist.py: switch Clock argument from units to unit for cocotb 2.x compatibility.
- Improved generator CLI help output:
  - src/openmbist.py: added detailed --help text with exact copy/paste commands for wrapper generation, software tests, hardware simulation, and full end-to-end run sequence.
- Updated sample generation schema values in config.yml to match input_demo_8x16_scn4m:
  - Set memory_name/wrapper_module_name for the input_demo_8x16_scn4m macro.
  - Set addr_width/data_width to 4/8.
  - Mapped write-enable port to web0 while keeping active-low behavior via we_active_low=true.

## 2026-04-25

- Implemented Phase 2 fault injection engine with a saboteur architecture while preserving the golden SRAM RTL:
  - Added src/fault_gen.py with reusable API + CLI for generating sa0_faults.hex and sa1_faults.hex from addr/data widths, fault count, and optional random seed.
  - Added src/templates/saboteur_template.j2 to generate <memory_name>_saboteur wrappers that instantiate the real SRAM and overlay read faults using mask logic.
  - Updated src/templates/wrapper_template.j2 to conditionally instantiate either the saboteur module or the golden SRAM via use_saboteur rendering context.
- Extended generator/runtime plumbing in src/openmbist.py:
  - Added --use-saboteur, --faults, and --fault-seed CLI options.
  - Added saboteur template rendering and output emission (<memory_name>_saboteur.v) when saboteur mode is enabled.
  - Added fault mask generation in out/faults and passed mask paths into saboteur rendering.
  - Updated mask path handling to use absolute paths so $readmemh resolves correctly from simulator working directories.
- Updated verification stack for clean and faulted simulation modes:
  - Updated tests/hardware/Makefile with USE_SABOTEUR/FAULT_MODE/FAULTS/FAULT_SEED controls and conditional saboteur source inclusion.
  - Updated tests/hardware/test_mbist.py with split cocotb tests (test_clean and test_faults), plus pre-sim fault mask preparation hook.
  - Extended tests/software/test_generator.py with saboteur/fault asset assertions and negative-fault validation.
- Validated via WSL + repository venv flows:
  - Software tests pass.
  - Clean hardware flow passes.
  - Faulted saboteur hardware flow passes after absolute-path mask fix.
- Simplified openmbist CLI for shorter optional commands:
  - Replaced required/verbose flags with optional `--config CONFIG` and `--out OUTDIR` defaults (`config.yml`, `out`).
  - Added `--test [BOOL]` (default false) for saboteur fault-injection mode.
  - Added short fault count flag `-r NUMBEROFFAULTS` with default 50.
  - Added `--seed` as optional deterministic seed.
  - Reworked `--help` to concise option-only output.
- Updated output layout to per-module directories:
  - Generator now writes all artifacts to `OUTDIR/<memory_name>/` instead of directly in `OUTDIR/`.
  - Fault masks are now generated under `OUTDIR/<memory_name>/faults/`.
  - Updated software and hardware verification paths to consume the new layout.
- Added generated module-local fault simulation Makefile (test mode only):
  - When `--test true` is used, OpenMBIST now emits `OUTDIR/<memory_name>/Makefile` pre-filled to run cocotb/iverilog fault simulation with `make`.
  - The generated Makefile resolves SRAM source modularly by checking `rtl/<memory_name>.v`, then `tests/hardware/<memory_name>.v`, then fallback `tests/hardware/sram_1rw.v`.
  - In normal mode (`--test false`), no Makefile is generated in output.
- Updated `openmbist --help` text to clearly state:
  - Artifacts are emitted under `OUTDIR/<memory_name>`.
  - Test mode generates the runnable fault-sim Makefile.
- Simplified generated fault simulation output and workflow:
  - Generated `OUTDIR/<memory_name>/Makefile` now acts as a thin wrapper over `tests/hardware/Makefile` instead of embedding cocotb internals.
  - Default `make` output is concise (PASS/FAIL summary, fault count, seed, and log file path).
  - Added `make debug` target for full live cocotb/RTL output when debugging is needed.
  - Added explicit precheck with friendly error when Python interpreter is missing:
    `Python3 not found at <path>. Maybe use venv? or install Python3.`
- Expanded software verification coverage:
  - Added CLI argument tests for defaults, boolean parsing, optional forms, short `-r`, seed handling, and invalid values.
  - Added integration checks for generated module-local Makefile content and targets.
- Improved `make debug` readability in generated module Makefile:
  - Filtered out cocotb ASCII summary table block (`** TEST ... PASS ...`) from debug console output.
  - Added clear end-of-run debug summary (`fault test PASS/FAIL`) and explicit raw-log path pointer.
