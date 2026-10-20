"""The yieldsim sweep core and its statistics, with a stand-in trial engine."""
from __future__ import annotations

import json

import pytest

from autombist.yieldsim.stats import bonferroni_confidence, wilson_interval
from autombist.yieldsim.sweep import (
    REPORT_SCHEMA_VERSION,
    PointResult,
    SweepResult,
    TrialRecord,
    build_report,
    derive_seed,
    points_report,
    run_sweep,
)


class FakeEngine:
    """Trial i at k: invalid when the seed is divisible by 7, else repaired when
    k <= 2. Deterministic in the seed, like a real engine."""

    name = "fake"
    unit = "defects"

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def describe(self):
        return {"kind": "stand-in"}

    def run_trial(self, k: int, seed: int) -> TrialRecord:
        self.calls.append((k, seed))
        if seed % 7 == 0:
            return TrialRecord(k=k, seed=seed, status="invalid", invalid_reason="seed divisible by 7")
        status = "repaired" if k <= 2 else "unrepairable"
        return TrialRecord(k=k, seed=seed, status=status, cells=k, spares_used=k if k <= 2 else None,
                           extras={"note": "x"})


# --- statistics ---------------------------------------------------------------

@pytest.mark.parametrize(("successes", "trials", "low", "high"), [
    (5, 10, 0.2366, 0.7634),
    (0, 10, 0.0, 0.2775),
    (10, 10, 0.7225, 1.0),
])
def test_wilson_matches_textbook_values(successes: int, trials: int, low: float, high: float) -> None:
    lo, hi = wilson_interval(successes, trials, 0.95)
    assert lo == pytest.approx(low, abs=1e-4) and hi == pytest.approx(high, abs=1e-4)


def test_wilson_with_no_trials_is_the_whole_unit_interval() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


@pytest.mark.parametrize(("args", "fragment"), [((11, 10), "successes"), ((1, 10, 1.0), "confidence")])
def test_wilson_refuses_impossible_inputs(args, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        wilson_interval(*args)


def test_bonferroni_splits_the_error_rate() -> None:
    assert bonferroni_confidence(0.95, 5) == pytest.approx(0.99)
    with pytest.raises(ValueError):
        bonferroni_confidence(0.95, 0)


# --- seeds --------------------------------------------------------------------

def test_the_linear_scheme_is_the_rtl_sweeps_original_formula() -> None:
    assert derive_seed("linear", 3, k=4, i=2, trials_per_point=10) == 3 + 4 * 10 + 2


def test_hashed_seeds_are_stable_distinct_and_independent_of_the_trial_count() -> None:
    a = derive_seed("blake2b", 0, k=4, i=2, trials_per_point=10)
    assert a == derive_seed("blake2b", 0, k=4, i=2, trials_per_point=1000)
    seeds = {derive_seed("blake2b", 0, k, i, 1) for k in range(20) for i in range(50)}
    assert len(seeds) == 1000
    assert derive_seed("blake2b", 1, 4, 2, 10) != a


def test_an_unknown_seed_scheme_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown seed scheme"):
        derive_seed("random", 0, 1, 0, 1)


# --- the sweep ----------------------------------------------------------------

def test_the_sweep_counts_valid_repaired_and_invalid_trials() -> None:
    engine = FakeEngine()
    result = run_sweep(engine, [1, 3], trials=14, base_seed=0, seed_scheme="linear")

    low, high = result.points
    assert [r.seed for r in low.trials] == list(range(14, 28))
    assert (low.valid_trials, low.repaired_count, low.invalid_count) == (12, 12, 2)  # seeds 14 and 21
    assert low.repair_rate == 1.0
    assert (high.valid_trials, high.repaired_count) == (12, 0) and high.repair_rate == 0.0
    assert (result.engine, result.unit, result.description) == ("fake", "defects", {"kind": "stand-in"})


def test_a_point_with_no_valid_trial_has_an_unknown_rate() -> None:
    point = PointResult(k=1, trials=(TrialRecord(k=1, seed=7, status="invalid", invalid_reason="x"),))
    assert point.repair_rate is None and point.wilson() == (0.0, 1.0)
    result = SweepResult(engine="fake", unit="defects", description={}, trials_per_point=1, base_seed=0,
                         seed_scheme="linear", points=(point,))
    assert points_report(result)[0]["mean_cells"] is None


def test_the_points_report_has_the_yield_sweep_1_1_shape_and_detail_levels() -> None:
    result = run_sweep(FakeEngine(), [1, 3], trials=14, seed_scheme="linear")

    full = points_report(result)
    assert [p["faults"] for p in full] == [1, 3]
    first = full[0]
    assert (first["repair_rate"], first["valid_trials"], first["repaired_count"], first["invalid_count"]) == (1.0, 12, 12, 2)
    assert first["wilson"]["confidence"] == 0.95 and first["wilson"]["high"] == 1.0
    assert first["mean_cells"] == 1.0  # the valid trials each saw k cells
    assert first["trials"][0] == {"seed": 14, "status": "invalid", "repaired": False,
                                  "invalid_reason": "seed divisible by 7"}
    assert first["trials"][1] == {"seed": 15, "status": "repaired", "repaired": True,
                                  "cells": 1, "spares_used": 1, "note": "x"}
    json.dumps(full)

    failures = points_report(result, trial_detail="failures")
    assert [t["status"] for t in failures[0]["trials"]] == ["invalid", "invalid"]
    assert len(failures[1]["trials"]) == 14
    assert all(p["trials"] == [] for p in points_report(result, trial_detail="none"))
    with pytest.raises(ValueError, match="trial_detail"):
        points_report(result, trial_detail="some")


@pytest.mark.parametrize(("ks", "trials", "fragment"), [
    ([1], 0, "at least 1"),
    ([1, -2], 1, "non-negative"),
    ([1, 1], 1, "must not repeat"),
])
def test_the_sweep_refuses_bad_arguments_before_any_trial(ks, trials, fragment: str) -> None:
    engine = FakeEngine()
    with pytest.raises(ValueError, match=fragment):
        run_sweep(engine, ks, trials)
    assert engine.calls == []


def test_an_engine_answering_for_the_wrong_k_is_caught() -> None:
    class Wrong(FakeEngine):
        def run_trial(self, k, seed):
            return TrialRecord(k=k + 1, seed=seed, status="repaired")

    with pytest.raises(ValueError, match="returned a trial for k=2"):
        run_sweep(Wrong(), [1], 1)


@pytest.mark.parametrize(("kwargs", "fragment"), [
    ({"status": "fixed"}, "status must be one of"),
    ({"status": "invalid"}, "needs an invalid_reason"),
    ({"status": "repaired", "invalid_reason": "x"}, "only an invalid one"),
])
def test_a_trial_record_refuses_inconsistent_fields(kwargs, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        TrialRecord(k=1, seed=0, **kwargs)


def test_progress_is_reported_after_every_trial() -> None:
    seen = []
    run_sweep(FakeEngine(), [1, 2], trials=3, progress=lambda k, i, r: seen.append((k, i, r.k)))
    assert [(k, i) for k, i, _ in seen] == [(1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2)]


def test_the_whole_report_lifts_the_engine_blocks_and_records_the_unit() -> None:
    class Described(FakeEngine):
        def describe(self):
            return {"geometry": {"rows": 4}, "defect_model": {"model": "fake"}}

    result = run_sweep(Described(), [1, 2], trials=3, base_seed=9)
    report = build_report(result, trial_detail="none", confidence=0.9)

    assert report["schema_version"] == REPORT_SCHEMA_VERSION == "1.1"
    assert report["engine"] == {"name": "fake"}
    assert report["geometry"] == {"rows": 4}
    assert report["defect_model"] == {"model": "fake", "unit": "defects"}
    assert report["seeding"] == {"scheme": "blake2b", "base_seed": 9}
    assert (report["trials_per_point"], report["base_seed"], report["trial_detail"]) == (3, 9, "none")
    assert [p["faults"] for p in report["points"]] == [1, 2]
    assert report["points"][0]["wilson"]["confidence"] == 0.9
    json.dumps(report)
