"""The committed calibration golden (scripts/calibrate_yield.py): its verdicts
follow the declared protocol, and small fresh samples from today's code agree
with it. This checks that the defect models still produce the calibrated
numbers; it does not re-judge them against the papers (the golden records that,
gate result included)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from autombist.yieldsim.bitmap_engine import BitmapEngine
from autombist.yieldsim.defects import ITC2020_READINGS, MIX_PRESETS, ArrayGeometry, MixModel, defect_model
from autombist.yieldsim.sweep import run_sweep

GOLDEN = Path(__file__).resolve().parents[1] / "data" / "yield_calibration.json"
RERUN_SEED = 987_654  # independent of the golden's base seed
MAX_ABS_Z = 4.0


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _z(ours: float, n: int, theirs: float, n_ref: int) -> float:
    variance = ours * (1 - ours) / n + theirs * (1 - theirs) / n_ref
    return 0.0 if variance == 0 and ours == theirs else (ours - theirs) / math.sqrt(variance or 1e-12)


def test_the_itc_verdict_follows_the_declared_rule(golden: dict) -> None:
    itc = golden["itc2020"]
    readings = itc["readings"]
    assert set(readings) == set(ITC2020_READINGS)
    assert itc["chosen"] == min(readings, key=lambda name: readings[name]["chi2"])
    assert itc["chosen_label"] == "inferred"
    for reading in readings.values():
        assert reading["chi2"] == pytest.approx(math.fsum(p["z"] ** 2 for p in reading["points"]))
    failing = [p["k"] for p in readings[itc["chosen"]]["points"] if abs(p["z"]) > itc["gate"]["max_abs_z"]]
    assert itc["gate"]["failing_points"] == failing and itc["gate"]["passed"] == (not failing)
    assert [p["reference"] for p in readings[itc["chosen"]]["points"]] == itc["target"]["repair_rate"]


@pytest.mark.parametrize("k", [8, 10, 12])
def test_a_fresh_itc_sample_matches_the_golden(golden: dict, k: int) -> None:
    itc = golden["itc2020"]
    setup = itc["setup"]
    engine = BitmapEngine(
        ArrayGeometry(rows=setup["rows"], cols=setup["cols"], banks=setup["banks"]),
        spare_rows=setup["spare_rows"], spare_cols=setup["spare_cols"],
        model=defect_model(setup["preset"], reading=itc["chosen"]),
    )
    trials = 2000
    ours = run_sweep(engine, [k], trials, RERUN_SEED).points[0].repair_rate
    point = next(p for p in itc["readings"][itc["chosen"]]["points"] if p["k"] == k)
    assert abs(_z(ours, trials, point["rate"], point["valid"])) <= MAX_ABS_Z, (ours, point["rate"])


@pytest.mark.parametrize("k", [3, 9, 11])
def test_a_fresh_ohler_sample_matches_the_golden_default(golden: dict, k: int) -> None:
    ohler = golden["ohler2007"]
    setup = ohler["setup"]
    engine = BitmapEngine(
        ArrayGeometry(rows=setup["rows"], cols=setup["cols"]),
        spare_rows=setup["spare_rows"], spare_cols=setup["spare_cols"],
        model=MixModel(weights=MIX_PRESETS[setup["preset"]], name=setup["preset"]),
    )
    trials = 300
    ours = run_sweep(engine, [k], trials, RERUN_SEED).points[0].repair_rate
    point = next(p for p in ohler["variants"]["default"]["points"] if p["k"] == k)
    assert abs(_z(ours, trials, point["rate"], point["valid"])) <= MAX_ABS_Z, (ours, point["rate"])


def test_the_ohler_comparison_is_recorded_as_a_comparison(golden: dict) -> None:
    ohler = golden["ohler2007"]
    assert ohler["role"] == "comparison only, not a gate"
    assert set(ohler["variants"]) == {"default", "cluster-sides-1-6", "line-length-2-16"}
    assert all(set(v["checks"]) == {"high_at_k_le_3", "near_zero_at_k_ge_12", "largest_drop_at_11"}
               for v in ohler["variants"].values())
