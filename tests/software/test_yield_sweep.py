"""Fast, pure-Python tests for yield_sweep's dataclass logic -- no real
simulation. See test_yield_sweep_e2e.py for the real-Icarus proof that
run_trial()/sweep() actually produce correct data from a real fault
injection; these tests cover only the pure repair_rate/repaired arithmetic
built on top of that data.
"""
from __future__ import annotations

from autombist.repair.types import RepairSolution, Unrepairable
from autombist.yield_sweep import DensityPoint, TrialResult


def _trial(repaired: bool, seed: int = 0) -> TrialResult:
    outcome = RepairSolution(row_map={}) if repaired else Unrepairable(faulty_rows=(0, 1), num_spare_rows=1)
    return TrialResult(faults=1, seed=seed, fail_cells=frozenset(), outcome=outcome)


def test_trial_result_repaired_reflects_outcome_type() -> None:
    assert _trial(repaired=True).repaired is True
    assert _trial(repaired=False).repaired is False


def test_density_point_repair_rate_is_fraction_repaired() -> None:
    point = DensityPoint(faults=5, trials=(_trial(True, 0), _trial(True, 1), _trial(False, 2), _trial(False, 3)))
    assert point.repair_rate == 0.5


def test_density_point_repair_rate_all_repaired_is_one() -> None:
    point = DensityPoint(faults=1, trials=(_trial(True, 0), _trial(True, 1)))
    assert point.repair_rate == 1.0


def test_density_point_repair_rate_none_repaired_is_zero() -> None:
    point = DensityPoint(faults=16, trials=(_trial(False, 0), _trial(False, 1)))
    assert point.repair_rate == 0.0


def test_density_point_repair_rate_empty_trials_is_zero_not_a_division_error() -> None:
    point = DensityPoint(faults=0, trials=())
    assert point.repair_rate == 0.0
