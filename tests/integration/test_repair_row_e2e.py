"""End-to-end Step-A proof: an external row-repair remap around a stock spare-
augmented memory actually repairs a forced defect.

The full real chain: generate a redundancy wrapper (redundancy: block +
repair_ports -> external repair_remap_row around sram_spares_tiny) -> run the
cocotb repair-row test twice (REPAIR_PHASE off/on) -> parse the observation-
derived fail-bitmap -> bira_input.fail_cells. Repair OFF: the defect at (3, 3) is
visible. Repair ON: logical row 3 is steered to a spare and the fail-bitmap is
empty. Icarus + make gated.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("make") is None,
    reason="needs Icarus Verilog + make on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.bira_input import fail_cells  # noqa: E402
from autombist.generator import generate_from_config  # noqa: E402
from autombist.runner import run_simulation  # noqa: E402

ADDR_WIDTH = 2
DATA_WIDTH = 4
# Matches sram_spares_tiny.v's baked-in DEFECT_ADDR / DEFECT_BIT.
DEFECT = (3, 3)

CONFIG = {
    "memory_name": "sram_spares_tiny",
    "wrapper_module_name": "sram_spares_tiny_mbist",
    "addr_width": ADDR_WIDTH,
    "data_width": DATA_WIDTH,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
    "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0},
    "repair_ports": [
        {"name": "row_repair_en", "width": 2, "dir": "input"},
        {"name": "faulty_row_addr", "width": 4, "dir": "input"},
    ],
}


def _run(tmp_path: Path, phase: str):
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / "out", algo="march-c")
    return run_simulation(
        wrapper.parent,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_repair_row", "REPAIR_PHASE": phase},
    )


def test_defect_visible_without_repair(tmp_path: Path) -> None:
    result = _run(tmp_path, "off")
    assert result.returncode == 0, result.stdout
    # The forced stuck-at surfaces at exactly its cell, and the march BIST tripped
    # (asserted inside the cocotb test -> returncode 0 means it matched).
    assert fail_cells(result.report) == {DEFECT}


def test_defect_repaired_with_repair(tmp_path: Path) -> None:
    result = _run(tmp_path, "on")
    assert result.returncode == 0, result.stdout
    # Row 3 steered to a spare: the fail scan is clean and BIST passed.
    assert fail_cells(result.report) == set()
