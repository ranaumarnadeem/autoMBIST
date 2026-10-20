"""The yield-multiplier command: reads a sweep report, weights its repair curve
by assumed defect statistics, and writes yield_multiplier.json."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autombist.main import app
from autombist.yieldsim.model import (
    DISCLAIMER,
    ITC2020_ALPHA,
    ITC2020_LAM,
    RepairCurve,
    yield_with_redundancy,
)

runner = CliRunner()

SWEEP_1_1 = {
    "schema_version": "1.1",
    "points": [
        {"faults": 1, "repair_rate": 1.0, "valid_trials": 10, "repaired_count": 10, "trials": []},
        {"faults": 2, "repair_rate": 0.6, "valid_trials": 10, "repaired_count": 6, "trials": []},
        {"faults": 3, "repair_rate": 0.1, "valid_trials": 10, "repaired_count": 1, "trials": []},
    ],
}


def _write(tmp_path: Path, report: dict) -> Path:
    path = tmp_path / "yield_sweep.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _invoke(tmp_path: Path, report_path: Path, *args: str):
    return runner.invoke(app, ["yield-multiplier", "--report", str(report_path), "--out", str(tmp_path / "out"), *args])


def test_the_json_output_matches_the_model_and_is_written_to_disk(tmp_path: Path) -> None:
    path = _write(tmp_path, SWEEP_1_1)
    result = _invoke(tmp_path, path, "--y0", "0.5", "--alpha", "poisson", "--json")

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report == json.loads((tmp_path / "out" / "yield_multiplier.json").read_text(encoding="utf-8"))
    assert report["disclaimer"] == DISCLAIMER
    assert report["source"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["source"]["schema_version"] == "1.1"
    (point,) = report["points"]
    expected = yield_with_redundancy(RepairCurve(counts={1: (10, 10), 2: (6, 10), 3: (1, 10)}), y0=0.5)
    assert point["m"] == pytest.approx(expected.m)
    assert point["band"]["m_low"] <= point["m"] <= point["band"]["m_high"]
    assert "tool_version" in report and "generated_at" in report


def test_the_default_grid_is_three_clusterings_by_five_initial_yields(tmp_path: Path) -> None:
    result = _invoke(tmp_path, _write(tmp_path, SWEEP_1_1), "--json")

    assert result.exit_code == 0, result.output
    points = json.loads(result.output)["points"]
    assert len(points) == 15
    assert sorted({p["alpha"] for p in points}, key=str) == sorted({None, 2.0, 0.5}, key=str)


def test_values_may_be_comma_separated_and_presets_named(tmp_path: Path) -> None:
    result = _invoke(tmp_path, _write(tmp_path, SWEEP_1_1), "--lam", "itc2020,1.0", "--alpha", "itc2020", "--json")

    assert result.exit_code == 0, result.output
    points = json.loads(result.output)["points"]
    assert [(p["alpha"], p["lam"]) for p in points] == [(ITC2020_ALPHA, ITC2020_LAM), (ITC2020_ALPHA, 1.0)]


def test_the_human_table_names_the_assumptions_and_the_disclaimer(tmp_path: Path) -> None:
    result = _invoke(tmp_path, _write(tmp_path, SWEEP_1_1), "--y0", "0.5", "--alpha", "poisson,2")

    assert result.exit_code == 0, result.output
    assert "poisson" in result.output and "alpha 2" in result.output
    assert "not calibrated to silicon" in result.output
    assert "k = 1..3" in result.output
    assert (tmp_path / "out" / "yield_multiplier.json").is_file()


def test_a_1_0_report_from_the_shipped_sweep_is_read(tmp_path: Path) -> None:
    old = {"schema_version": "1.0", "points": [
        {"faults": 1, "repair_rate": 1.0, "trials": [{"seed": 1, "repaired": True, "cells": 1}]},
    ]}
    result = _invoke(tmp_path, _write(tmp_path, old), "--y0", "0.9", "--alpha", "poisson", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["curve"]["points"] == [{"faults": 1, "repaired_count": 1, "valid_trials": 1}]


@pytest.mark.parametrize(("args", "fragment"), [
    (["--alpha", "clumpy"], "--alpha 'clumpy'"),
    (["--alpha", "0"], "alpha must be a positive number"),
    (["--y0", "1.5"], "initial yield"),
    (["--y0", "0.5", "--lam", "1"], "not both"),
    (["--lam", "lots"], "--lam 'lots'"),
    (["--confidence", "1.0"], "confidence"),
    (["--area-ratio", "0.5"], "area-ratio"),
])
def test_bad_assumptions_are_one_error_line(tmp_path: Path, args: list[str], fragment: str) -> None:
    result = _invoke(tmp_path, _write(tmp_path, SWEEP_1_1), *args)

    assert result.exit_code != 0
    assert fragment in result.output and "Traceback" not in result.output


def test_a_curve_with_a_gap_says_which_range_to_sweep(tmp_path: Path) -> None:
    gappy = {"schema_version": "1.1", "points": [SWEEP_1_1["points"][0], SWEEP_1_1["points"][2]]}
    result = _invoke(tmp_path, _write(tmp_path, gappy))

    assert result.exit_code == 1 and "--faults 1..3" in result.output


@pytest.mark.parametrize("content", ["not json", json.dumps({"schema_version": "1.1", "points": [{"k": 1}]})])
def test_an_unreadable_report_is_one_error_line(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    result = _invoke(tmp_path, path)

    assert result.exit_code == 1 and "autombist:" in result.output and "Traceback" not in result.output


def test_a_missing_report_is_one_error_line(tmp_path: Path) -> None:
    result = _invoke(tmp_path, tmp_path / "nope.json")

    assert result.exit_code == 1 and "Traceback" not in result.output
