"""Regression guard: run_simulation() must actually be able to RUN a
shared-bus wrapper, not just find it elaboratable with Verilator.

Before this fix, run_simulation() resolved the wrapper file as
`{memory_name}_mbist.v` -- but a shared-bus wrapper is named
`{wrapper_module_name}_mbist.v` (see generator.py's own `output_stem`), so
no shared-bus config, of any kind, could ever be simulated through the real
cocotb pipeline; every prior shared-bus proof only ever went through direct
Verilator lint (test_shared_bus_generate_e2e.py), never an actual run. Real
Icarus + cocotb, matching this project's own "prove it against real tools"
discipline -- not a hand-rolled stub.
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

CONFIG = {
    "memory_name": "sram_1rw",
    "wrapper_module_name": "shared_bus_ctrl",
    "topology": "shared-bus",
    "addr_width": 6,
    "data_width": 8,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"},
    "memories": [{"name": "mem_bank0"}, {"name": "mem_bank1"}],
}


def test_plain_shared_bus_wrapper_actually_simulates(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    wrapper_path = generate_from_config(config_path, tmp_path / "out")

    result = run_simulation(wrapper_path.parent)
    assert result.returncode == 0, result.stdout
    assert "test_mbist.test_clean" in result.stdout
