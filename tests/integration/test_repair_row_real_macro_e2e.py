"""End-to-end proof that Step A's external row-repair remap (redundancy: +
repair_ports:) works against a REAL OpenRAM-compiled macro, not just the
hand-written sram_spares_tiny.v/sram_model_spares.sv behavioral stand-ins
every other BISR test uses.

See rtl/sram_bisr_real_8x16.v for the macro's provenance (scn4m_subm,
8-bit words x 16 words x 2 spare rows x 0 spare cols, generated via
`scripts/synthesize_sram.sh --tech scn4m_subm --word-size 8 --num-words 16
--num-rw-ports 1 --num-spare-rows 2 --num-spare-cols 0`) and
tests/hardware/test_repair_row_real_macro.py for what this actually proves --
a real macro has no defect-injection knob, so this is a steering-distinctness
proof (does the remap genuinely redirect physical storage?), not an
inject-then-repair proof like test_repair_row_e2e.py's.
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

CONFIG = {
    "memory_name": "sram_bisr_real_8x16",
    "wrapper_module_name": "sram_bisr_real_8x16_mbist",
    "addr_width": ADDR_WIDTH,
    "data_width": DATA_WIDTH,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
    "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0},
    "repair_ports": [
        {"name": "row_repair_en", "width": 2, "dir": "input"},
        {"name": "faulty_row_addr", "width": 8, "dir": "input"},
    ],
}


def test_real_openram_macro_repair_steering(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / "out", algo="march-c")
    result = run_simulation(
        wrapper.parent,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_repair_row_real_macro"},
    )
    # The cocotb test's own asserts (clean baseline scan + the steering-
    # distinctness proof) are what actually verify correctness; returncode==0
    # means all of them passed against the real compiled macro.
    assert result.returncode == 0, result.stdout
