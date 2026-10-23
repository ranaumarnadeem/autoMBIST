"""The chip's own self-repair with injected defects, spares included, on real
RTL: hand-checked cases for each outcome the sweep attributes, then a short
CLI sweep.

Icarus + make gated, like every other fault-injection e2e test here.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("make") is None,
    reason="needs Icarus Verilog + make on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.main import app  # noqa: E402
from autombist.yield_sweep import SelfRepairEngine  # noqa: E402


def _config(tmp_path: Path, addr_width: int, data_width: int) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump({
        "memory_name": "sram_sweep", "wrapper_module_name": "sram_sweep_mbist",
        "addr_width": addr_width, "data_width": data_width, "we_active_low": True,
        "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
    }, sort_keys=False), encoding="utf-8")
    return path


def test_one_defect_is_repaired_with_a_spare_row_and_the_build_is_reused(tmp_path: Path) -> None:
    engine = SelfRepairEngine(_config(tmp_path, 2, 4), tmp_path / "work", num_spare_rows=2)
    first = engine.run_stuck({(3, 1): 1})
    (vvp,) = list(engine.module_dir.glob("sim_build/*/sim.vvp"))
    built = vvp.stat().st_mtime_ns
    second = engine.run_stuck({(1, 2): 0})

    assert vvp.stat().st_mtime_ns == built, "the second trial recompiled the simulation"
    assert first.repaired and first.extras["row_map"] == {3: 0} and first.extras["cause"] is None
    assert second.repaired and second.extras["row_map"] == {1: 0}


def test_the_gap_demo_shows_the_greedy_analyzer_falling_short(tmp_path: Path) -> None:
    """The five cells of tests/hardware/sram_spares_col_gap_demo.v: the exact
    solver repairs them with rows 5 and 30 and column 2; the greedy on-chip 2D
    analyzer spends its rows on first sightings and runs out. It does not flag
    unrepairable itself (it is lenient); its repair leaves a cell it saw
    uncovered and the verify pass fails."""
    engine = SelfRepairEngine(_config(tmp_path, 5, 8), tmp_path / "work", num_spare_rows=2, num_spare_cols=1)
    record = engine.run_stuck({(5, 0): 1, (5, 1): 1, (9, 2): 1, (20, 2): 1, (30, 5): 1})

    assert record.status == "unrepairable"
    assert record.extras["truth_repairable"] and record.extras["observed_repairable"]
    assert record.extras["onchip_fail"] == 1 and record.extras["onchip_unrepairable"] == 0
    assert record.extras["cause"] == "analyzer"


def test_a_defect_in_the_allocated_spare_fails_verify_and_one_elsewhere_does_not(tmp_path: Path) -> None:
    engine = SelfRepairEngine(_config(tmp_path, 2, 4), tmp_path / "work", num_spare_rows=2)
    # physical row 4 is spare row 0, the one the analyzer gives the first faulty row
    allocated = engine.run_stuck({(3, 1): 1, (4, 0): 0})
    unallocated = engine.run_stuck({(3, 1): 1, (5, 0): 0})

    assert allocated.status == "unrepairable" and allocated.extras["onchip_fail"] == 1
    assert allocated.extras["truth_repairable"] and allocated.extras["cause"] == "faulty_spare"
    assert unallocated.repaired and unallocated.extras["spare_cells"] == 1


def test_a_spare_column_repairs_a_column_of_faults(tmp_path: Path) -> None:
    engine = SelfRepairEngine(_config(tmp_path, 3, 4), tmp_path / "work", num_spare_rows=1, num_spare_cols=1)
    record = engine.run_stuck({(1, 2): 1, (4, 2): 1, (6, 2): 1})
    assert record.repaired and record.extras["col_map"] == {2: 0}


def test_more_faulty_rows_than_log_entries_overflow_the_diagnosis_log(tmp_path: Path) -> None:
    engine = SelfRepairEngine(_config(tmp_path, 3, 4), tmp_path / "work", num_spare_rows=3, diagnosis_entries=2)
    record = engine.run_stuck({(1, 0): 1, (3, 1): 1, (6, 2): 0})
    assert record.repaired and record.extras["diag_overflow"] == 1
    assert engine.run_stuck({(1, 0): 1}).extras["diag_overflow"] == 0


def test_a_cli_sweep_with_defects_in_the_spares(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = CliRunner().invoke(app, [
        "yield-sweep", "--config", str(_config(tmp_path, 3, 4)), "--out", str(out),
        "--faults", "1..2", "--trials", "2", "--spare-rows", "2", "--spare-cols", "1",
        "--self-repair", "--expose-spares", "--json",
    ])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["engine"] == {"name": "rtl-onchip-selfrepair"}
    assert report["geometry"]["spares_exposed"] is True
    for point in report["points"]:
        assert point["valid_trials"] == 2 and "self_repair" in point
        assert point["self_repair"]["onchip_repair_rate"] <= point["self_repair"]["exact_repair_rate"]
