"""Fast, pure-Python tests for yield_sweep's helpers -- no real simulation:
which fault types the functional scan can observe, when a trial's simulation
report cannot be trusted as a fail bitmap, and where the sweep report is
written. RtlEngine and SelfRepairEngine have their own tests
(test_yield_sweep_engine.py, test_selfrepair_sweep.py).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autombist.yield_sweep import _trial_invalid_reason, check_observable, write_sweep_report


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


def test_write_sweep_report_writes_yield_sweep_json(tmp_path: Path) -> None:
    report = {"schema_version": "1.1", "points": [{"faults": 1, "trials": [{"row_map": {0: 0}}]}]}

    report_path = write_sweep_report(report, tmp_path / "out")

    assert report_path == tmp_path / "out" / "yield_sweep.json"
    # JSON object keys are always strings, so row_map's int keys become "0".
    assert json.loads(report_path.read_text(encoding="utf-8")) == json.loads(json.dumps(report))
