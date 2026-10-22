"""Fast, pure-Python tests for build_sweep_report/write_sweep_report's JSON
schema -- no real simulation. See test_yield_sweep_cli_e2e.py for the real
end-to-end CLI proof that this schema round-trips through an actual
`autombist yield-sweep` invocation.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from autombist.repair.types import RepairSolution, Unrepairable
from autombist.yield_sweep import SCHEMA_VERSION, DensityPoint, TrialResult, build_sweep_report, write_sweep_report
from autombist.yieldsim.stats import wilson_interval

CONFIG = {
    "memory_name": "sram_tiny",
    "wrapper_module_name": "sram_tiny_mbist",
    "addr_width": 2,
    "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    return config_path


def _points() -> list[DensityPoint]:
    repaired = TrialResult(faults=1, seed=100, fail_cells=frozenset({(0, 0)}), outcome=RepairSolution(row_map={0: 0}))
    unrepaired = TrialResult(
        faults=16, seed=200, fail_cells=frozenset({(0, 0), (1, 0), (2, 0), (3, 0)}),
        outcome=Unrepairable(faulty_rows=(0, 1, 2, 3), num_spare_rows=1),
    )
    return [
        DensityPoint(faults=1, trials=(repaired,)),
        DensityPoint(faults=16, trials=(unrepaired,)),
    ]


def test_build_sweep_report_shape(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    report = build_sweep_report(
        _points(), config_path,
        tool_version="0.0.0-test", num_spare_rows=1, num_spare_cols=0,
        algo="march-c", fault_type="stuck-at", trials_per_point=1, base_seed=0,
    )

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["config"] == {
        "memory_name": "sram_tiny", "addr_width": 2, "data_width": 4,
        "algo": "march-c", "fault_type": "stuck-at",
    }
    assert report["spare_budget"] == {"num_spare_rows": 1, "num_spare_cols": 0}
    assert report["trials_per_point"] == 1
    assert report["base_seed"] == 0

    assert len(report["points"]) == 2
    repairable_point, unrepairable_point = report["points"]

    assert repairable_point["faults"] == 1
    assert repairable_point["repair_rate"] == 1.0
    assert (repairable_point["valid_trials"], repairable_point["repaired_count"]) == (1, 1)
    assert repairable_point["trials"] == [
        {"seed": 100, "fail_cell_count": 1, "status": "repaired", "repaired": True,
         "row_map": {0: 0}, "col_map": {}}
    ]

    assert unrepairable_point["faults"] == 16
    assert unrepairable_point["repair_rate"] == 0.0
    trial = unrepairable_point["trials"][0]
    assert trial["seed"] == 200
    assert trial["fail_cell_count"] == 4
    assert trial["status"] == "unrepairable"
    assert trial["repaired"] is False
    assert trial["faulty_rows"] == [0, 1, 2, 3]
    assert trial["faulty_cols"] == []
    # the scan does not run the march: the report says so
    assert report["observation"] == {"mode": "functional-scan", "uses_algo": False}


def test_an_invalid_trial_is_reported_as_such_and_left_out_of_the_rate(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    good = TrialResult(faults=2, seed=1, fail_cells=frozenset(), outcome=RepairSolution(row_map={}))
    bad = TrialResult(faults=2, seed=2, fail_cells=frozenset(), outcome=None,
                      invalid_reason="the fail scan did not complete (no FAIL_SCAN_COMPLETE marker)")

    report = build_sweep_report(
        [DensityPoint(faults=2, trials=(good, bad))], config_path,
        tool_version="0.0.0-test", num_spare_rows=1, num_spare_cols=0,
        algo="march-c", fault_type="stuck-at", trials_per_point=2, base_seed=0,
    )

    point = report["points"][0]
    assert (point["repair_rate"], point["valid_trials"], point["repaired_count"]) == (1.0, 1, 1)
    assert point["invalid_count"] == 1
    assert point["wilson"]["confidence"] == 0.95 and point["wilson"]["high"] == 1.0
    assert point["trials"][1] == {
        "seed": 2, "fail_cell_count": 0, "status": "invalid", "repaired": False,
        "invalid_reason": "the fail scan did not complete (no FAIL_SCAN_COMPLETE marker)",
    }


def test_build_sweep_report_is_json_serializable(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    report = build_sweep_report(
        _points(), config_path,
        tool_version="0.0.0-test", num_spare_rows=1, num_spare_cols=0,
        algo="march-c", fault_type="stuck-at", trials_per_point=1, base_seed=0,
    )
    # json.dumps would raise on a dict[int,int] key (row_map) unless it's
    # coerced to str keys by the encoder -- confirm it round-trips, not just
    # that it doesn't raise.
    payload = json.dumps(report, sort_keys=True)
    round_tripped = json.loads(payload)
    assert round_tripped["points"][0]["trials"][0]["row_map"] == {"0": 0}


def test_write_sweep_report_writes_yield_sweep_json(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    report = build_sweep_report(
        _points(), config_path,
        tool_version="0.0.0-test", num_spare_rows=1, num_spare_cols=0,
        algo="march-c", fault_type="stuck-at", trials_per_point=1, base_seed=0,
    )

    report_path = write_sweep_report(report, tmp_path / "out")

    assert report_path == tmp_path / "out" / "yield_sweep.json"
    # Compare against a self-round-tripped copy, not the raw in-memory
    # `report` -- JSON object keys are always strings, so row_map's int keys
    # (e.g. {0: 0}) legitimately become {"0": 0} on the way through disk.
    assert json.loads(report_path.read_text(encoding="utf-8")) == json.loads(json.dumps(report))


def test_the_1_1_report_names_its_engine_seeding_and_unit(tmp_path: Path) -> None:
    report = build_sweep_report(
        _points(), _write_config(tmp_path),
        tool_version="0.0.0-test", num_spare_rows=1, num_spare_cols=0,
        algo="march-c", fault_type="stuck-at", trials_per_point=1, base_seed=7,
    )

    assert report["engine"] == {"name": "rtl-functional-scan"}
    assert report["seeding"] == {"scheme": "linear", "base_seed": 7}
    assert report["defect_model"] == {"model": "saboteur", "fault_type": "stuck-at", "unit": "cells"}
    low, high = wilson_interval(1, 1)
    assert report["points"][0]["wilson"] == {"confidence": 0.95, "low": low, "high": high}
