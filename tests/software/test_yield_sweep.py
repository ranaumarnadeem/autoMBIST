"""Fast, pure-Python tests for yield_sweep's dataclass logic -- no real
simulation. See test_yield_sweep_e2e.py for the real-Icarus proof that
run_trial()/sweep() actually produce correct data from a real fault
injection; these tests cover only the pure repair_rate/repaired arithmetic
built on top of that data.
"""
from __future__ import annotations

import pytest

from autombist.repair.types import RepairSolution, Unrepairable
from autombist.yield_sweep import DensityPoint, TrialResult, _trial_invalid_reason, check_observable, sweep


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


def _invalid(seed: int = 9) -> TrialResult:
    return TrialResult(faults=1, seed=seed, fail_cells=frozenset(), outcome=None, invalid_reason="no marker")


def test_an_invalid_trial_is_neither_repaired_nor_counted() -> None:
    trial = _invalid()
    assert (trial.valid, trial.repaired, trial.status) == (False, False, "invalid")
    assert _trial(True).status == "repaired" and _trial(False).status == "unrepairable"

    point = DensityPoint(faults=1, trials=(_trial(True, 0), _trial(False, 1), _invalid(2)))
    assert (point.valid_trials, point.repaired_count, point.repair_rate) == (2, 1, 0.5)


def test_a_point_with_only_invalid_trials_has_rate_zero_and_says_none_were_valid() -> None:
    point = DensityPoint(faults=1, trials=(_invalid(0), _invalid(1)))
    assert (point.valid_trials, point.repair_rate) == (0, 0.0)


# --- validity of a trial's simulation report -------------------------------------




def _report(**overrides):
    report = {
        "fail_bitmap": [],
        "fail_scan_unknown_addrs": [],
        "junit": {"exists": True, "summary": {"failures": 0, "errors": 0},
                  "tests": [{"name": "test_fail_scan", "status": "passed"}]},
    }
    report.update(overrides)
    return report


def test_a_complete_clean_scan_is_valid() -> None:
    assert _trial_invalid_reason(_report()) is None


@pytest.mark.parametrize(("overrides", "fragment"), [
    ({"fail_bitmap": None}, "did not complete"),
    ({"junit": {"exists": True, "summary": {"failures": 1, "errors": 0}, "tests": []}}, "1 failure"),
    ({"junit": {"exists": True, "summary": {"failures": 0, "errors": 0},
                "tests": [{"name": "test_fail_scan", "status": "skipped"}]}}, "test_fail_scan did not pass"),
    ({"fail_scan_unknown_addrs": [3, 7]}, "2 word(s) read back as unknown"),
])
def test_an_untrustworthy_scan_makes_the_trial_invalid(overrides, fragment: str) -> None:
    report = _report(**overrides)
    if report.get("fail_bitmap") is None:
        del report["fail_bitmap"]
    assert fragment in _trial_invalid_reason(report)


@pytest.mark.parametrize("fault_type", ["transition-up", "transition-down", "port-coupling"])
def test_fault_types_the_scan_cannot_see_are_refused(fault_type: str) -> None:
    with pytest.raises(ValueError, match="sees stuck-at faults only"):
        check_observable(fault_type)
    check_observable("stuck-at")


@pytest.mark.parametrize(("kwargs", "fragment"), [
    ({"fault_type": "transition-up"}, "stuck-at faults only"),
    ({"trials_per_point": 0}, "at least 1"),
    ({"fault_counts": [2, -1]}, "non-negative"),
    ({"fault_counts": [2, 2]}, "must not repeat"),
])
def test_sweep_refuses_bad_arguments_before_running_anything(tmp_path, kwargs, fragment: str) -> None:
    args = {"fault_counts": [1], "trials_per_point": 1, "num_spare_rows": 1, "fault_type": "stuck-at"}
    args.update(kwargs)
    with pytest.raises(ValueError, match=fragment):
        sweep(tmp_path / "missing.yml", tmp_path / "out", **args)
