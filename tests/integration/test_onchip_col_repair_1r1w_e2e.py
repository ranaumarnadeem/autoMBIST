"""End-to-end proof of on-chip 2D (row + column) self-repair for the
multi-port march-1r1w algorithm -- mirrors test_onchip_selfrepair_1r1w_e2e.py's
relationship to test_onchip_selfrepair_e2e.py: same feature, same cocotb
module (test_onchip_col_repair, reused unchanged -- the wrapper boundary
protocol is identical regardless of port topology), but exercising the
multi-port wrapper branch's column-repair scaffold and a genuinely dual-ported
spare-column memory model (sram_spares_col_tiny_1r1w.v) instead of a single rw
port.

Only `promote`/`retrigger` are exercised here, not the full four-scenario
suite test_onchip_col_repair_e2e.py runs against the single-port algos: the
analyzer's internal claim logic (onchip_2d_repair_analyzer) is REUSED
VERBATIM, already proven exhaustively there (Finding-1 race, Finding-2
fallback, the disclosed gap_demo). What's genuinely NEW here -- and what
these tests actually exercise -- is the multi-port wrapper's cross-port
repair_remap_col wiring (write port's din/spare_wen, read port's dout, one
instance instead of one per port), which never existed before this change.
Icarus + make gated, same as every other RTL-backed test in this suite.
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
PORTS_1R1W_COL = {
    "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
    "wport": {
        "type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1",
        "spare_wen": "spare_wen1",
    },
}


def _config(num_spare_rows: int, num_spare_cols: int) -> dict:
    return {
        "memory_name": "sram_spares_col_tiny_1r1w",
        "wrapper_module_name": "sram_spares_col_tiny_1r1w_mbist",
        "addr_width": ADDR_WIDTH,
        "data_width": DATA_WIDTH,
        "we_active_low": True,
        "ports": PORTS_1R1W_COL,
        # NO repair_ports: -- mutually exclusive with onchip_selfrepair, the
        # analyzer/sequencer drive both remaps directly.
        "redundancy": {
            "num_spare_rows": num_spare_rows,
            "num_spare_cols": num_spare_cols,
            "onchip_selfrepair": True,
            "onchip_col_repair": True,
        },
    }


def _generate(tmp_path: Path, config: dict, subdir: str) -> Path:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / subdir, algo="march-1r1w")


def _run(wrapper: Path, scenario: str):
    return run_simulation(
        wrapper.parent,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_onchip_col_repair", "COL_REPAIR_SCENARIO": scenario},
    )


def test_recurring_bit_is_promoted_to_a_column_repair(tmp_path: Path) -> None:
    """Matches sram_spares_col_tiny_1r1w.v's baked-in defects: the same bit
    (3) in two different rows (1, 2), 1 spare row + 1 spare col -- the
    first-seen row consumes the row spare, the recurrence is promoted to the
    column spare. Proves the write port's defect and the read port's
    steered readback agree -- the cross-port wiring this DUT exists for."""
    wrapper = _generate(tmp_path, _config(1, 1), "promote")
    result = _run(wrapper, "promote")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


def test_retrigger_gives_the_same_result_both_times(tmp_path: Path) -> None:
    """Running the autonomous sequence twice in one simulation (no reset in
    between) must reach the SAME verdict both times -- same DUT/geometry as
    the promote case."""
    wrapper = _generate(tmp_path, _config(1, 1), "retrigger")
    result = _run(wrapper, "retrigger")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()
