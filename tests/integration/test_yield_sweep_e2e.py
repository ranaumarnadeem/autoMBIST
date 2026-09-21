"""Real end-to-end proof for docs/diagnosis-yield-analysis-plan.md's v1
sweep harness (steps 1-3): a Monte Carlo repair-yield sweep built entirely
on the existing generate/simulate/BIRA pipeline, zero core changes.

Icarus + make gated, matching every other fault-injection e2e test in this
project (see test_classic_fail_coordinates_e2e.py, whose exact
generate_from_config(use_saboteur=True, ...) -> run_simulation(fail_scan=True)
-> bira_input.fail_cells() pipeline yield_sweep.run_trial() reuses
unchanged).
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

from autombist.repair.bira import analyze  # noqa: E402
from autombist.repair.types import RepairSolution, SpareGeometry, Unrepairable  # noqa: E402
from autombist.yield_sweep import run_trial, sweep  # noqa: E402

# The smallest sensible macro (depth 4 x width 4) -- same fixture
# test_classic_fail_coordinates_e2e.py already proves the fail-bitmap
# pipeline against.
ADDR_WIDTH = 2
DATA_WIDTH = 4
CONFIG = {
    "memory_name": "sram_tiny",
    "wrapper_module_name": "sram_tiny_mbist",
    "addr_width": ADDR_WIDTH,
    "data_width": DATA_WIDTH,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    return config_path


def test_sweep_collects_every_trial_with_no_silent_drops(tmp_path: Path) -> None:
    # docs/diagnosis-yield-analysis-plan.md step 1's own proof requirement:
    # run N trials programmatically, confirm N distinct reports collected.
    config_path = _write_config(tmp_path)
    trials_per_point = 5

    points = sweep(
        config_path, tmp_path / "out",
        fault_counts=[1], trials_per_point=trials_per_point,
        num_spare_rows=1, base_seed=100,
    )

    assert len(points) == 1
    assert points[0].faults == 1
    assert len(points[0].trials) == trials_per_point
    seeds = {t.seed for t in points[0].trials}
    assert len(seeds) == trials_per_point, "trials collapsed onto duplicate seeds"


def test_trial_classification_matches_an_independent_direct_bira_call(tmp_path: Path) -> None:
    # docs/diagnosis-yield-analysis-plan.md step 2's own proof requirement:
    # the aggregator's classification must match a SEPARATE, direct
    # repair.bira.analyze() call on the SAME observed fail_cells -- not
    # re-derived/trusted logic inside run_trial() itself.
    config_path = _write_config(tmp_path)

    trial = run_trial(
        config_path, tmp_path / "out" / "trial",
        faults=2, seed=42, num_spare_rows=2, num_spare_cols=0,
    )

    geometry = SpareGeometry(
        base_words=1 << ADDR_WIDTH, word_size=DATA_WIDTH,
        num_spare_rows=2, num_spare_cols=0,
    )
    independent_outcome = analyze(trial.fail_cells, geometry)

    assert isinstance(independent_outcome, (RepairSolution, Unrepairable))
    assert trial.repaired == isinstance(independent_outcome, RepairSolution)
    assert trial.outcome == independent_outcome


def test_repair_yield_curve_hits_the_deterministic_100_and_0_percent_extremes(tmp_path: Path) -> None:
    # docs/diagnosis-yield-analysis-plan.md step 3's own proof requirement:
    # confirm the curve hits its expected extremes for a small, hand-picked
    # geometry. With depth=4/width=4 (16 total cells) and num_spare_rows=1:
    #   faults=1  -> exactly 1 (addr,bit) stuck -> confined to ONE row ->
    #                ALWAYS repairable, deterministically, regardless of seed.
    #   faults=16 -> ALL 16 cells stuck (rng.sample of the full population,
    #                see fault_gen.generate_fault_masks) -> spans all 4 rows
    #                -> ALWAYS exceeds the 1-spare-row budget, deterministically.
    config_path = _write_config(tmp_path)

    points = sweep(
        config_path, tmp_path / "out",
        fault_counts=[1, 16], trials_per_point=2,
        num_spare_rows=1, base_seed=7,
    )

    by_faults = {p.faults: p for p in points}
    assert by_faults[1].repair_rate == 1.0, "a single-row defect must always be repairable with 1 spare row"
    assert by_faults[16].repair_rate == 0.0, "a defect spanning all 4 rows must always exceed a 1-spare-row budget"
