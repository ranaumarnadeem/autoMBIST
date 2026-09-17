"""End-to-end proof of on-chip 2D (row + column) self-repair for the
multi-port march-2rw algorithm -- mirrors test_onchip_col_repair_1r1w_e2e.py's
relationship to test_onchip_col_repair_e2e.py: same feature, same cocotb
module (test_onchip_col_repair, reused unchanged -- the wrapper boundary
protocol is identical regardless of port topology), but exercising the
multi-port wrapper branch's TWO-INDEPENDENT-INSTANCE column-repair scaffold
(one repair_remap_col per port, since march-2rw's two ports are both fully
read/write and can write different addresses the same cycle -- unlike
march-1r1w's clean read-only/write-only split, which gets away with ONE
shared cross-wired instance) and a genuinely dual-read/write-port
spare-column memory model (sram_spares_col_tiny_2rw.v).

Only `promote`/`retrigger` are exercised here, not the full four-scenario
suite test_onchip_col_repair_e2e.py runs against the single-port algos: the
analyzer's internal claim logic (onchip_2d_repair_analyzer) is REUSED
VERBATIM, already proven exhaustively there (Finding-1 race, Finding-2
fallback, the disclosed gap_demo). What's genuinely NEW here -- and what
these tests actually exercise -- is the multi-port wrapper's per-port
repair_remap_col wiring. Note precisely what these e2e tests CAN and CANNOT
prove: march-2rw's own algorithm structure (both ports write the identical
value whenever both write concurrently; both ports read the identical
address, from the identical physical storage cell, whenever both read
concurrently) makes a wrapper bug that swaps which port's din_in/dout_in
feeds which repair_remap_col instance behaviorally UNDETECTABLE by any
simulation -- self-repair-then-rescan reads back clean either way. That
specific correctness question is closed by the render-text wiring assertions
in tests/software/test_onchip_col_repair_config.py, not by this file; these
e2e tests instead prove the STRUCTURAL wiring elaborates and behaves
correctly end-to-end (parameter sizing, spare_wen gating, both instances
actually driving real spare columns). Icarus + make gated.
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
PORTS_2RW_COL = {
    "porta": {
        "type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0",
        "spare_wen": "spare_wen0",
    },
    "portb": {
        "type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1",
        "spare_wen": "spare_wen1",
    },
}


def _config(num_spare_rows: int, num_spare_cols: int) -> dict:
    return {
        "memory_name": "sram_spares_col_tiny_2rw",
        "wrapper_module_name": "sram_spares_col_tiny_2rw_mbist",
        "addr_width": ADDR_WIDTH,
        "data_width": DATA_WIDTH,
        "we_active_low": True,
        "ports": PORTS_2RW_COL,
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
    return generate_from_config(config_path, tmp_path / subdir, algo="march-2rw")


def _run(wrapper: Path, scenario: str):
    return run_simulation(
        wrapper.parent,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_onchip_col_repair", "COL_REPAIR_SCENARIO": scenario},
    )


def test_recurring_bit_is_promoted_to_a_column_repair(tmp_path: Path) -> None:
    """Matches sram_spares_col_tiny_2rw.v's baked-in defects: the same bit
    (3) in two different rows (1, 2), 1 spare row + 1 spare col -- the
    first-seen row consumes the row spare, the recurrence is promoted to the
    column spare. Both ports independently write both defect rows across the
    algorithm's phases, so this exercises both repair_remap_col instances'
    structural wiring (elaboration, parameter sizing, spare_wen gating)."""
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
