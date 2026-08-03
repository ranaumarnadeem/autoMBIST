"""Elaboration guard for RTL files nothing else in the suite ever compiles.

`rtl/sram_model_spares.sv` is the REFERENCE spare-augmented memory model, but no
test compiles it: `tests/hardware/Makefile` builds `tests/hardware/$(MEMORY_NAME).v`,
and the actually-simulated DUTs are hand-copied, renamed clones of it. Those
clones have already diverged from the reference in places (e.g. `sram_spares_tiny.v`
carries no `DEFECT2_*` knobs), so without this guard the reference can silently rot
into a non-elaborating state and nobody would notice until the next person copies it.

Cheap insurance -- an elaborate-only pass (`iverilog -t null`), no simulation.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None,
    reason="needs Icarus Verilog on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# RTL that no other test compiles, so has no other elaboration coverage.
UNCOMPILED_REFERENCE_RTL = [
    "rtl/sram_model_spares.sv",
]


@pytest.mark.parametrize("rel_path", UNCOMPILED_REFERENCE_RTL)
def test_reference_rtl_elaborates_standalone(rel_path: str) -> None:
    path = REPO_ROOT / rel_path
    assert path.exists(), f"{rel_path} not found -- update UNCOMPILED_REFERENCE_RTL"
    result = subprocess.run(
        ["iverilog", "-g2012", "-t", "null", str(path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"{rel_path} failed to elaborate on its own defaults:\n"
        f"{result.stdout}\n{result.stderr}"
    )
