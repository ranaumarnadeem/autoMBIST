"""Exhaustive module-level proof of rtl/repair_remap_col.sv (Workstream M.1a).

Compiles and runs tests/hardware/repair_remap_col_probe.sv, a self-checking
testbench that sweeps EVERY reachable input combination of the column-remap
module against an independently-written golden model -- 4,096 vectors at
NUM_SPARE_COLS=1 plus 65,536 at NUM_SPARE_COLS=2 (which is what proves the
packed multi-spare faulty_bit slicing and the last-match-wins precedence when
two spares name the same logical bit).

This exists so a later wrapper-template failure can never be misdiagnosed as a
bit-steer bug -- the same "prove the module before proving the integration"
role tests/hardware/algo_module_probe.sv plays for the algo FSMs. It bypasses
cocotb/make entirely (iverilog + vvp directly), so it is a pure add with no
Makefile coupling.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="needs Icarus Verilog (iverilog + vvp) on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_SV = REPO_ROOT / "tests" / "hardware" / "repair_remap_col_probe.sv"
DUT_SV = REPO_ROOT / "rtl" / "repair_remap_col.sv"


def test_repair_remap_col_exhaustive_probe(tmp_path: Path) -> None:
    exe = tmp_path / "probe"
    build = subprocess.run(
        ["iverilog", "-g2012", "-o", str(exe), str(PROBE_SV), str(DUT_SV)],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, f"iverilog failed:\n{build.stdout}\n{build.stderr}"

    run = subprocess.run(
        ["vvp", str(exe)], capture_output=True, text=True, check=False, timeout=600,
    )
    out = run.stdout + run.stderr
    assert "PROBE FAIL" not in out, out
    assert "MISMATCH" not in out, out
    assert "PROBE PASS" in out, out
    # Pin the vector count: a silently-shrunk sweep would still print PASS.
    assert "PROBE PASS 69632 vectors" in out, out


def test_repair_remap_col_elaborates_standalone_with_defaults(tmp_path: Path) -> None:
    """The module must elaborate on its own defaults -- it is compiled but NOT
    instantiated in every row-only redundancy build (see tests/hardware/Makefile's
    REMAP_SOURCE), so a defaults-only elaboration error would break those."""
    result = subprocess.run(
        ["iverilog", "-g2012", "-t", "null", str(DUT_SV)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
