# Contributing to autombist

Thanks for your interest in contributing. This document covers how to set up a
development environment and run the test suite.

## Platform Requirements

**If you're on Windows, autombist's dev/test workflow must run inside WSL, using a
virtualenv created *inside* WSL — not a native Windows Python install.**

Why: `autombist generate` (pure Python wrapper/RTL emission and config parsing) will
technically import on any Python 3.10+, but the moment you touch simulation or
synthesis — `simulate`, `run`, `test`, the `algo` shell's `run`/`compare_algo`
commands, `grade-controller`, or any test under `tests/hardware`/`tests/integration` —
you're driving Icarus Verilog, Verilator, Yosys, OpenRAM, and cocotb's simulator
hooks. cocotb loads a native shared-object VPI/VHPI layer that's built against
Linux shared libraries and expects POSIX paths and a POSIX process model; none of
that works from a native Windows Python interpreter. A venv created in Windows also
won't see the Linux-native EDA binaries (`iverilog`, `verilator`, `yosys`) even if
they happen to be on your Windows `PATH` under some emulation layer. Creating the
venv with WSL's own `python3` and installing the EDA toolchain via `apt`/source
inside WSL sidesteps all of this.

Concretely:

1. Python 3.10+
2. A venv created with WSL's (or native Linux's) `python3`, not `py`/`python.exe`
   from Windows
3. For anything that simulates or synthesizes: Icarus Verilog (`iverilog`),
   Verilator, Yosys, OpenRAM, and (optionally, for `grade-controller`) a built
   FaultFlow repo — all installed inside the same WSL/Linux environment as the venv
4. cocotb and cocotb-tools installed into that same venv (pulled in automatically
   as core dependencies — see below)

## Dev Environment Setup

From a WSL (or native Linux) shell:

```bash
# 1. Clone
git clone https://github.com/ranaumarnadeem/autoMBIST.git
cd autoMBIST

# 2. Create a venv inside WSL/Linux (do not reuse a Windows-side venv/interpreter)
python3 -m venv ~/cocotb   # any venv path/name works; ~/cocotb is just this repo's convention
source ~/cocotb/bin/activate

# 3. Install the package editable, with the dev extras
python -m pip install -e ".[dev,hardware-test]"

# 4. Install the system EDA toolchain (Debian/Ubuntu shown; matches CI)
sudo apt-get update
sudo apt-get install -y iverilog verilator yosys
```

`pip install -e ".[dev,hardware-test]"` installs:

- Core dependencies: `Jinja2`, `PyYAML`, `typer[all]`, `cocotb`, `cocotb-tools`
- `dev` extra: `pytest`, `pytest-cov`
- `hardware-test` extra: `cocotb`, `cocotb-tools` (already core deps; this extra
  exists mainly so `pip install autombist[hardware-test]` is a documented,
  self-contained install target for consumers who only need the hardware-test bits)

OpenRAM and FaultFlow are not pip-installable dependencies of this repo — they're
separate tools you install yourself and point autombist at (`autombist ram-synth`,
`autombist grade-controller --faultflow-repo ...`). You only need them if you're
touching OpenRAM synthesis or controller-grading code paths.

## Running the Test Suite

Tests are split by how much of the toolchain they need:

- **`tests/software/`** — pure Python, no EDA tools required. Fast; this is what
  you'll run on every iteration.
- **`tests/integration/`** — mostly tool-gated (Icarus/Verilator/Yosys) but written
  to skip cleanly when a tool is missing rather than hard-fail.
- **`tests/hardware/`** — cocotb testbenches driven through the `tests/hardware/Makefile`
  (Icarus Verilog via `cocotb-tools`' `Makefile.sim`). These need the full WSL EDA
  toolchain and are the slowest tier; they are **not** part of the CI job (see
  below) and are typically run directly via `make` inside `tests/hardware/` rather
  than through `pytest`.

Matching this project's actual convention (and CI), the software + integration
tiers are run together with coverage:

```bash
PYTHONPATH=src ~/cocotb/bin/python -m pytest tests/software tests/integration \
    --cov=autombist --cov-report=term-missing -q
```

If you only want the fast, tool-independent slice while iterating:

```bash
PYTHONPATH=src ~/cocotb/bin/python -m pytest tests/software -q
```

Two custom pytest markers (registered in `pyproject.toml`) label tool-gated tests:

- `hardware` — needs Icarus Verilog/Verilator/cocotb on `PATH` (`tests/hardware`,
  `tests/integration`)
- `faultflow` — needs a built FaultFlow repo + Yosys on `PATH` (grade-controller
  tests)

Adjust the `~/cocotb/bin/python` prefix to wherever you created your venv in step 2
above; the `PYTHONPATH=src` prefix is needed for the same reason it appears in
`tests/hardware/Makefile` — the tests import `autombist` from the checked-out
source tree.

### What CI actually runs

`.github/workflows/test.yml` runs on every push/PR to `main` (Ubuntu, Python 3.12):

1. `apt-get install -y iverilog verilator yosys` (best-effort — tool-gated tests
   skip cleanly if a tool is somehow still absent)
2. `pip install -e ".[hardware-test]" pytest pytest-cov`
3. `pytest tests/software tests/integration --cov=autombist --cov-report=term-missing --cov-fail-under=90`

Note CI enforces a **90% coverage gate** (`--cov-fail-under=90`) and does **not**
run `tests/hardware` at all. A separate `.github/workflows/publish.yml` builds and
publishes to PyPI on tagged releases/GitHub Releases — it doesn't run tests, only
`python -m build` + `twine check`.

Keep your local `pytest tests/software tests/integration --cov=autombist
--cov-report=term-missing` run passing at 90%+ coverage before opening a PR, since
that's the exact gate CI applies.

## Project Layout

```
src/autombist/          Top-level Python modules: CLI (cli.py, main.py), RTL
                         generation (generator.py, fault_ram_gen.py), fault
                         injection (fault_gen.py, fault_primitives.py), the march
                         algorithm engine wrapper (algo_engine.py, algo_shell.py,
                         alg_spec.py), simulation/reporting (runner.py,
                         reporting.py, algo_reporting.py), and the OpenRAM/FaultFlow
                         flow integrations (openram_flow.py, faultflow_flow.py).

src/autombist/engine/   The algo-shell RTL + docs: fault_ram.sv (fault-injectable
                         behavioral RAM model, single- and multi-port),
                         march_engine.sv / march_engine_mp.sv (the programmable
                         march-algorithm runner, single- and multi-port), plus the
                         engine's own README.md and example fault lists.

src/autombist/templates/  Jinja2 templates that generator.py renders into the
                           emitted RTL/Makefiles under out/<memory>/ (wrapper,
                           saboteur, fault_ram, FSM harness, FaultFlow blackbox
                           stub, etc).

rtl/                     Static reference march-algorithm RTL and sample SRAM
                         models used by tests and examples (not templated).

tests/software/          Pure Python unit tests — no EDA tools needed.
tests/integration/       Tool-gated tests (Icarus/Verilator/Yosys), written to
                         skip cleanly when a tool is absent.
tests/hardware/          cocotb testbenches + the Makefile that drives them
                         through Icarus Verilog.
```

## Contribution Etiquette

- Open an issue (or comment on an existing one) before starting substantial work,
  so effort isn't duplicated and the approach can be discussed up front.
- Keep the existing test suite passing — run at least `tests/software` +
  `tests/integration` with coverage locally before opening a PR (see above).
- Add tests for new behavior in the appropriate tier (`tests/software` if it's pure
  Python; `tests/integration` or `tests/hardware` if it needs a simulator).
- Scope PRs to one logical change; keep unrelated formatting/refactoring out of
  functional PRs.
- This repo has a sibling project, [FaultFlow](https://github.com/ranaumarnadeem/faultflow),
  that autombist's `grade-controller` command shells out to. Don't modify FaultFlow
  from within this repo — if a change is needed there, open it as a separate PR
  against the FaultFlow repo itself.
