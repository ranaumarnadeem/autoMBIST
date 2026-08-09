"""Wires 10 of tests/hardware/'s cocotb testbenches into pytest.

Each of these has its own standalone tests/hardware/run_*_tb.py driver
(cocotb_tools.runner.get_runner("icarus") called directly, no Makefile
involved) instead of the shared tests/hardware/Makefile + COCOTB_TEST_MODULES
convention the other cocotb modules use -- because each compiles a
multi-source/multi-wrapper design (multiple generated wrappers, or a raw
macro glued into a subsystem/SoC toplevel) or a standalone algorithm POC with
no generated MBIST wrapper at all, neither of which fits the Makefile's
one-macro/one-wrapper data model. That bespoke-script shape is exactly why
pytest never collected them: nothing in tests/software or tests/integration
ever invoked them, so `pytest tests/software tests/integration` (what CI
actually runs) silently exercised zero of this coverage. Each script already
exits non-zero on any cocotb test failure or build failure (documented in its
own module docstring) -- so subprocessing it and asserting success is a
faithful, minimal wrapper, not a reimplementation.

test_mem_subsystem_selfrepair (a 10th script in this same family) already has
its own dedicated pytest driver elsewhere; it isn't duplicated here.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None,
    reason="needs Icarus Verilog on PATH (Linux/WSL only) -- these scripts call "
    "cocotb_tools.runner.get_runner('icarus') directly, no make involved",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HW_DIR = REPO_ROOT / "tests" / "hardware"

SCRIPTS = [
    "run_algo_modules_tb.py",
    "run_march_1r1w_tb.py",
    "run_march_2rw_tb.py",
    "run_mem_subsystem_tb.py",
    "run_mem_subsystem_mbist_tb.py",
    "run_newalgo_real_macro_selfrepair_tb.py",
    "run_port_coupling_diff_tb.py",
    "run_soc_hw_selfrepair_tb.py",
    "run_soc_selfrepair_tb.py",
]


@pytest.mark.parametrize("script", SCRIPTS)
def test_standalone_cocotb_script_passes(script: str) -> None:
    # PYTEST_CURRENT_TEST must NOT reach the child: these scripts pass a
    # relative results_xml path (by design -- see each script's own
    # runner.test(results_xml="results.xml", ...) call), and
    # cocotb_tools.runner.Runner.test() raises NotImplementedError for a
    # relative path the instant it sees that env var, since it assumes
    # (wrongly, here) that a relative path under pytest is ambiguous. The
    # child is a genuinely separate process, invoked exactly as each script's
    # own docstring documents -- inheriting pytest's env var is what breaks
    # it, not a real conflict with running under pytest.
    env = {
        key: value for key, value in os.environ.items() if key != "PYTEST_CURRENT_TEST"
    }
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    result = subprocess.run(
        [sys.executable, str(HW_DIR / script)],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False, timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
