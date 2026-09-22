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
    assert repairable_point["trials"] == [
        {"seed": 100, "fail_cell_count": 1, "repaired": True, "row_map": {0: 0}, "col_map": {}}
    ]

    assert unrepairable_point["faults"] == 16
    assert unrepairable_point["repair_rate"] == 0.0
    trial = unrepairable_point["trials"][0]
    assert trial["seed"] == 200
    assert trial["fail_cell_count"] == 4
    assert trial["repaired"] is False
    assert trial["faulty_rows"] == [0, 1, 2, 3]
    assert trial["faulty_cols"] == []


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
