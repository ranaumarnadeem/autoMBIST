"""The RTL sweep engine on real simulations: one compile per sweep, and the
observed-versus-truth bookkeeping for both observations.

Icarus + make gated, like every other fault-injection e2e test here.
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

from autombist.yield_sweep import RtlEngine  # noqa: E402
from autombist.yieldsim.sweep import build_report, run_sweep  # noqa: E402

TINY = {
    "memory_name": "sram_tiny", "wrapper_module_name": "sram_tiny_mbist", "addr_width": 2, "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}
DEMO = {
    "memory_name": "input_demo_8x16_scn4m", "wrapper_module_name": "input_demo_8x16_scn4m_mbist",
    "addr_width": 4, "data_width": 8, "read_latency": 0, "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}


def _config(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def test_a_sweep_compiles_once_and_reuses_the_simulation(tmp_path: Path) -> None:
    engine = RtlEngine(_config(tmp_path, TINY), tmp_path / "dut", num_spare_rows=1)
    first = engine.run_trial(1, seed=3)
    (vvp,) = list(engine.module_dir.glob("sim_build/*/sim.vvp"))
    built = vvp.stat().st_mtime_ns

    second = engine.run_trial(2, seed=4)
    assert vvp.stat().st_mtime_ns == built, "the second trial recompiled the simulation"
    assert first.valid and second.valid
    assert first.extras["truth_cells"] == 1 and second.extras["truth_cells"] == 2
    assert first.extras["overkill"] == 0 and second.extras["overkill"] == 0


def test_the_march_sees_transition_faults_and_never_invents_a_cell(tmp_path: Path) -> None:
    engine = RtlEngine(_config(tmp_path, TINY), tmp_path / "dut", num_spare_rows=4, num_spare_cols=4,
                       algo="march-raw", observe="march", fault_type="transition-up")
    report = build_report(run_sweep(engine, [1, 3], trials=3, seed_scheme="linear"), trial_detail="all")

    for point in report["points"]:
        assert point["valid_trials"] == 3
        assert point["observed_vs_truth"]["overkill_cells"] == 0
        for trial in point["trials"]:
            assert trial["fail_cell_count"] <= trial["truth_cells"]
    assert sum(t["fail_cell_count"] for p in report["points"] for t in p["trials"]) > 0


def test_a_defect_preset_on_the_demo_macro_is_observed_exactly_by_the_march(tmp_path: Path) -> None:
    engine = RtlEngine(_config(tmp_path, DEMO), tmp_path / "dut", num_spare_rows=2, num_spare_cols=2,
                       observe="march", defects="ohler-d2")
    report = build_report(run_sweep(engine, [1, 2], trials=3, seed_scheme="linear"), trial_detail="all")

    assert report["defect_model"]["preset"] == "ohler-d2" and report["defect_model"]["unit"] == "defects"
    for point in report["points"]:
        truth = point["observed_vs_truth"]
        assert truth["escape_trials"] == 0 and truth["overkill_cells"] == 0
        assert truth["observed_repair_rate"] == truth["truth_repair_rate"] == point["repair_rate"]
        for trial in point["trials"]:
            assert trial["fail_cell_count"] == trial["truth_cells"] and "defects" in trial
