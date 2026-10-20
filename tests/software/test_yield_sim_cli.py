"""The yield-sim command: a solver-only repair-rate sweep writing yield_sim.json,
which yield-multiplier reads."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autombist.main import app

runner = CliRunner()


def _invoke(tmp_path: Path, *args: str):
    return runner.invoke(app, ["yield-sim", "--out", str(tmp_path / "out"), *args])


def test_singles_within_the_budget_repair_and_the_report_is_1_1(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--array", "64x32", "--spare-rows", "2", "--spare-cols", "1",
                     "--faults", "1..3", "--trials", "25", "--json")

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report == json.loads((tmp_path / "out" / "yield_sim.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.1" and report["engine"] == {"name": "bitmap"}
    assert report["geometry"]["rows"] == 64 and report["geometry"]["spare_cols_per_bank"] == 1
    assert report["defect_model"]["preset"] == "singles" and report["defect_model"]["unit"] == "defects"
    assert report["seeding"] == {"scheme": "blake2b", "base_seed": 0}
    assert [p["repair_rate"] for p in report["points"]] == [1.0, 1.0, 1.0]
    assert all(p["trials"] == [] for p in report["points"])  # failures only, and none failed


def test_a_sweep_is_reproducible_from_its_seed(tmp_path: Path) -> None:
    args = ("--array", "32x32", "--spare-rows", "1", "--spare-cols", "1", "--defects", "ohler-d2",
            "--faults", "2..4", "--trials", "30", "--trial-detail", "all", "--json")
    first = json.loads(_invoke(tmp_path, *args).output)
    second = json.loads(_invoke(tmp_path, *args).output)
    assert first["points"] == second["points"]
    assert first["points"] != json.loads(_invoke(tmp_path, *args, "--seed", "1").output)["points"]


def test_the_report_feeds_yield_multiplier(tmp_path: Path) -> None:
    sim = _invoke(tmp_path, "--array", "32x32", "--spare-rows", "1", "--spare-cols", "1",
                  "--defects", "ohler-d2", "--faults", "1..4", "--trials", "20")
    assert sim.exit_code == 0, sim.output
    assert "Repair-rate sweep" in sim.output and "ohler-d2" in sim.output

    report = tmp_path / "out" / "yield_sim.json"
    mult = runner.invoke(app, ["yield-multiplier", "--report", str(report), "--out", str(tmp_path / "m"),
                               "--y0", "0.5", "--alpha", "poisson", "--json"])
    assert mult.exit_code == 0, mult.output
    body = json.loads(mult.output)
    assert body["curve"]["unit"] == "defects" and body["curve"]["k_max"] == 4


def test_itc_presets_take_a_reading_and_count_cells(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--array", "64x32", "--banks", "2", "--spare-rows", "3", "--spare-cols", "3",
                     "--defects", "itc2020-3050", "--itc-reading", "total-bank", "--faults", "2", "--trials", "5",
                     "--json")

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["defect_model"]["reading"] == "total-bank" and report["defect_model"]["unit"] == "cells"
    assert report["points"][0]["mean_cells"] == 4.0  # 2 cells in each of 2 banks


@pytest.mark.parametrize(("args", "fragment"), [
    (["--array", "64"], "ROWSxCOLS"),
    (["--array", "0x4"], "at least one row"),
    (["--defects", "blobs"], "unknown defect preset"),
    (["--defects", "itc2020-3050", "--itc-reading", "maybe"], "unknown ITC'2020 reading"),
    (["--trial-detail", "some"], "--trial-detail"),
    (["--faults", "3", "--faults", "3"], "must not repeat"),
    (["--confidence", "2"], "confidence"),
])
def test_bad_arguments_are_one_error_line(tmp_path: Path, args: list[str], fragment: str) -> None:
    base = {"--array": "16x16", "--spare-rows": "1", "--faults": "1", "--trials": "2"}
    for flag in args[::2]:
        base.pop(flag, None)
    flat = [x for kv in base.items() for x in kv]
    result = _invoke(tmp_path, *flat, *args)

    assert result.exit_code == 1, result.output
    assert fragment in result.output and "Traceback" not in result.output
