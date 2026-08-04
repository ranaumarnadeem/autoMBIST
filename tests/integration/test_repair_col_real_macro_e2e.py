"""End-to-end proof that Workstream M.1's external COLUMN-repair remap
(`repair_remap_col`) works against a REAL OpenRAM-compiled macro, not just the
hand-written sram_spares_col_tiny.v/sram_model_spares.sv behavioral stand-ins.

The column analogue of test_repair_row_real_macro_e2e.py, and it exists for the
same reason: row repair got a real-macro proof, so column repair should meet the
same evidence bar rather than shipping behavioral-only.

It needed its OWN macro. The existing `sram_bisr_real_8x16` was compiled with
`num_spare_cols = 0` and has no `spare_wen0` pin at all, so it cannot exercise
column repair however it is wrapped. `sram_bisr_real_col_8x16` was generated
specifically for this (scn4m_subm, 8x16, 2 spare rows, 1 spare col) -- see
tests/hardware/sram_bisr_real_col_8x16.v for provenance and for what the real
macro's own write block independently confirms about the behavioral model.

A real macro has no defect-injection knob, so like the row version this is a
steering-DISTINCTNESS proof, not an inject-then-repair proof -- see
tests/hardware/test_repair_col_distinct.py's module docstring for the exact
sequence and why the row proof's logic does not transfer to columns.
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

from autombist.generator import generate_from_config  # noqa: E402
from autombist.runner import run_simulation  # noqa: E402

ADDR_WIDTH = 4
DATA_WIDTH = 8
# bit_index_width = ceil(log2(8)) = 3, so faulty_bit is 1 spare * 3 = 3 bits.
CONFIG = {
    "memory_name": "sram_bisr_real_col_8x16",
    "wrapper_module_name": "sram_bisr_real_col_8x16_mbist",
    "addr_width": ADDR_WIDTH,
    "data_width": DATA_WIDTH,
    "we_active_low": True,
    "ports": {
        "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0",
        "we": "web0", "csb": "csb0", "spare_wen": "spare_wen0",
    },
    "redundancy": {"num_spare_rows": 2, "num_spare_cols": 1},
    "repair_ports": [
        {"name": "row_repair_en", "width": 2, "dir": "input"},
        {"name": "faulty_row_addr", "width": 8, "dir": "input"},
        {"name": "col_repair_en", "width": 1, "dir": "input"},
        {"name": "faulty_bit", "width": 3, "dir": "input"},
    ],
}


def test_real_openram_macro_column_repair_steering(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / "out", algo="march-c")
    result = run_simulation(
        wrapper.parent,
        extra_make_vars={
            "COCOTB_TEST_MODULES": "test_repair_col_distinct",
            "TARGET_ADDR": 5,
            "TARGET_BIT": 7,   # top logical bit, directly below spare column 0 at bit 8
        },
    )
    # The cocotb test's own asserts (read-after-write through the spare, then
    # the distinctness/staleness proof) are what verify correctness;
    # returncode == 0 means all of them passed against the real compiled macro.
    assert result.returncode == 0, result.stdout
