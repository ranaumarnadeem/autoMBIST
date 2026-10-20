"""Yield with redundancy from a repair curve, checked against published tables
and closed forms (no simulation)."""
from __future__ import annotations

import json
import math
from fractions import Fraction

import pytest

from autombist.yieldsim.model import (
    DISCLAIMER,
    ITC2020_ALPHA,
    ITC2020_LAM,
    MULTIPLIER_SCHEMA_VERSION,
    RepairCurve,
    curve_from_report,
    lam_from_y0,
    multiplier_report,
    occupancy_repair_probability,
    pmf,
    tail,
    y0_from_lam,
    yield_points,
    yield_with_redundancy,
)

# Stapper, "Large-area fault clusters and fault tolerance in VLSI circuits: A
# review", IBM J. Res. Dev. 33(2), 1989, Table 4: a chip needs 10 good circuit
# blocks of 10% joint yield; with R redundant blocks (which can fail too) its
# yield in percent, for cluster parameter alpha (None = Poisson, no clustering).
STAPPER_TABLE_4 = {
    0.5: [10, 14.6, 17.8, 20.3, 22.4, 24.1],
    1: [10, 18.3, 25.2, 31.1, 36.1, 40.6],
    2: [10, 22.4, 34.3, 44.8, 53.7, 61.1],
    None: [10, 30.6, 53.8, 73.0, 85.8, 93.1],
}


def _exact_curve(blocks: int, spares: int, k_max: int) -> RepairCurve:
    counts = {}
    for k in range(1, k_max + 1):
        r = occupancy_repair_probability(k, blocks, spares)
        counts[k] = (r.numerator, r.denominator)
    return RepairCurve(counts=counts, unit="defects")


@pytest.mark.parametrize("alpha", list(STAPPER_TABLE_4))
@pytest.mark.parametrize("spares", range(6))
def test_stapper_1989_table_4_is_reproduced(alpha, spares: int) -> None:
    """The weighting over an exact occupancy repair curve gives Stapper's table
    within 0.1 point: 22 entries round to the printed value and two differ in
    the last digit (alpha 1, R 5: 40.55; alpha 2, R 5: 61.17). The spares are
    exposed to defects, so the mean defect count scales with the physical area
    (10 + R)/10."""
    blocks = 10 + spares
    point = yield_with_redundancy(
        _exact_curve(blocks, spares, k_max=120), y0=0.10, alpha=alpha, area_ratio=blocks / 10,
    )
    assert 100 * point.y_red == pytest.approx(STAPPER_TABLE_4[alpha][spares], abs=0.1)
    assert point.y0 == pytest.approx(0.10)


def test_occupancy_repair_probability_small_cases_by_hand() -> None:
    assert occupancy_repair_probability(0, 4, 0) == 1
    assert occupancy_repair_probability(1, 4, 0) == 0
    assert occupancy_repair_probability(1, 4, 1) == 1
    assert occupancy_repair_probability(2, 4, 1) == Fraction(1, 4)  # both in the same block
    assert occupancy_repair_probability(3, 3, 3) == 1


def test_ramadan_simple_model_m_is_one_plus_lambda() -> None:
    """Only single-defect dies repairable (R(1) = 1, R(k > 1) = 0): Ramadan's
    simple model, M = 1 + lam under Poisson."""
    curve = RepairCurve(counts={1: (10, 10), 2: (0, 10), 3: (0, 10)})
    for y0 in (0.9, 0.5, 0.1):
        point = yield_with_redundancy(curve, y0=y0)
        assert point.m == pytest.approx(1 + lam_from_y0(y0))


@pytest.mark.parametrize("alpha", [None, 4.0, 1.0, 0.5])
def test_when_everything_is_repairable_m_reaches_one_over_y0(alpha) -> None:
    """Ramadan's maximum improvement (e^lam under Poisson, printed with a sign
    typo as exp(-lam) in the paper) is 1/Y0 for any distribution."""
    curve = RepairCurve(counts={k: (5, 5) for k in range(1, 400)})
    point = yield_with_redundancy(curve, y0=0.2, alpha=alpha)
    assert point.m == pytest.approx(1 / 0.2, rel=1e-6)


def test_a_huge_alpha_is_poisson() -> None:
    for k in range(6):
        assert pmf(k, 1.7, alpha=1e7) == pytest.approx(pmf(k, 1.7), rel=1e-5)


@pytest.mark.parametrize("alpha", [None, 4.0, 0.5, ITC2020_ALPHA])
@pytest.mark.parametrize("y0", [0.99, 0.5, 0.05])
def test_lambda_and_y0_round_trip(alpha, y0: float) -> None:
    assert y0_from_lam(lam_from_y0(y0, alpha), alpha) == pytest.approx(y0)


def test_the_itc2020_preset_has_about_41_percent_initial_yield() -> None:
    assert ITC2020_LAM == pytest.approx(2.0065, abs=1e-4)
    assert y0_from_lam(ITC2020_LAM, ITC2020_ALPHA) == pytest.approx(0.408, abs=0.001)


def test_pmfs_sum_to_one_and_tail_is_the_rest() -> None:
    for alpha in (None, 0.5, 3.0):
        assert math.fsum(pmf(k, 2.5, alpha) for k in range(400)) == pytest.approx(1.0)
        assert tail(3, 2.5, alpha) == pytest.approx(1 - math.fsum(pmf(k, 2.5, alpha) for k in range(4)))


def test_the_truncation_bounds_bracket_the_full_answer() -> None:
    """Measuring only k = 1..4 of a curve that keeps going: the plain sum is a
    lower bound and adding the tail at the smallest measured rate an upper one."""
    full = _exact_curve(6, 2, k_max=150)
    short = RepairCurve(counts={k: full.counts[k] for k in range(1, 5)}, unit="defects")
    exact = yield_with_redundancy(full, y0=0.3, alpha=1.0)
    cut = yield_with_redundancy(short, y0=0.3, alpha=1.0)
    assert cut.y_red <= exact.y_red <= cut.y_red_high
    assert cut.tail_mass > 0


def test_the_band_brackets_the_estimate_and_narrows_with_more_trials() -> None:
    few = RepairCurve(counts={1: (9, 10), 2: (5, 10), 3: (1, 10)})
    many = RepairCurve(counts={1: (900, 1000), 2: (500, 1000), 3: (100, 1000)})
    a = yield_with_redundancy(few, y0=0.5)
    b = yield_with_redundancy(many, y0=0.5)
    assert a.band_low <= a.y_red <= a.band_high
    assert b.band_high - b.band_low < a.band_high - a.band_low
    assert a.m_band[0] <= a.m <= a.m_band[1]


def test_r_cond_is_the_rate_over_defective_dies() -> None:
    curve = RepairCurve(counts={1: (10, 10), 2: (0, 10)})
    point = yield_with_redundancy(curve, y0=0.5)
    p0, p1 = pmf(0, point.lam), pmf(1, point.lam)
    assert point.r_cond == pytest.approx(p1 / (1 - p0))


@pytest.mark.parametrize(("kwargs", "fragment"), [
    ({}, "exactly one of y0 or lam"),
    ({"y0": 0.5, "lam": 1.0}, "exactly one of y0 or lam"),
    ({"y0": 1.5}, "initial yield"),
    ({"y0": 0.5, "alpha": -1.0}, "alpha"),
    ({"y0": 0.5, "area_ratio": 0.5}, "area_ratio"),
])
def test_bad_assumptions_are_refused(kwargs, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        yield_with_redundancy(RepairCurve(counts={1: (1, 1)}), **kwargs)


def test_a_gap_in_the_curve_is_refused_with_the_range_to_sweep() -> None:
    with pytest.raises(ValueError, match=r"missing k = \[2\].*--faults 1..3"):
        yield_with_redundancy(RepairCurve(counts={1: (1, 1), 3: (0, 1)}), y0=0.5)
    with pytest.raises(ValueError, match=r"missing k = \[2\]"):
        yield_with_redundancy(RepairCurve(counts={1: (1, 1), 2: (0, 0), 3: (0, 1)}), y0=0.5)


def test_yield_points_cover_every_assumption_pair() -> None:
    curve = RepairCurve(counts={1: (1, 1), 2: (0, 1)})
    points = yield_points(curve, alphas=[None, 2.0], y0_values=[0.9, 0.5], lam_values=[1.0])
    assert [(p.alpha, round(p.y0, 3)) for p in points] == [
        (None, 0.9), (None, 0.5), (None, round(math.exp(-1), 3)),
        (2.0, 0.9), (2.0, 0.5), (2.0, round(y0_from_lam(1.0, 2.0), 3)),
    ]


# --- reading sweep reports ---------------------------------------------------

def test_a_1_0_report_counts_every_trial() -> None:
    report = {"schema_version": "1.0", "points": [
        {"faults": 1, "repair_rate": 1.0, "trials": [{"repaired": True}, {"repaired": True}]},
        {"faults": 2, "repair_rate": 0.5, "trials": [{"repaired": True}, {"repaired": False}]},
    ]}
    curve = curve_from_report(report)
    assert curve.counts == {1: (2, 2), 2: (1, 2)} and curve.unit == "cells"


def test_a_1_1_report_uses_valid_trials_and_its_unit() -> None:
    report = {"schema_version": "1.1", "defect_model": {"unit": "defects"}, "points": [
        {"faults": 3, "valid_trials": 8, "repaired_count": 6, "trials": []},
    ]}
    curve = curve_from_report(report)
    assert curve.counts == {3: (6, 8)} and curve.unit == "defects"


@pytest.mark.parametrize(("report", "fragment"), [
    ({"schema_version": "2.0", "points": []}, "unsupported"),
    ({"schema_version": "1.1", "points": [{"faults": 1, "valid_trials": 1, "repaired_count": 1},
                                          {"faults": 1, "valid_trials": 1, "repaired_count": 1}]}, "twice"),
    ({"schema_version": "1.1", "points": [{"faults": 1, "valid_trials": 1, "repaired_count": 2}]}, "bad repair count"),
])
def test_bad_reports_are_refused(report, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        curve_from_report(report)


def test_a_curve_with_no_defective_point_is_refused() -> None:
    with pytest.raises(ValueError, match="every k from 1"):
        yield_with_redundancy(RepairCurve(counts={0: (3, 3)}), y0=0.5)


def test_the_multiplier_report_records_the_curve_assumptions_and_bands() -> None:
    curve = RepairCurve(counts={1: (9, 10), 2: (4, 10)}, unit="defects")
    points = yield_points(curve, alphas=[None, 2.0], y0_values=[0.5])
    report = multiplier_report(curve, points, source={"path": "r.json", "sha256": "ab"})

    assert report["schema_version"] == MULTIPLIER_SCHEMA_VERSION == "1.0"
    assert report["disclaimer"] == DISCLAIMER and "not calibrated to silicon" in DISCLAIMER
    assert report["source"] == {"path": "r.json", "sha256": "ab"}
    assert report["curve"] == {"unit": "defects", "k_max": 2, "points": [
        {"faults": 1, "repaired_count": 9, "valid_trials": 10},
        {"faults": 2, "repaired_count": 4, "valid_trials": 10},
    ]}
    poisson, clustered = report["points"]
    assert (poisson["distribution"], poisson["alpha"]) == ("poisson", None)
    assert (clustered["distribution"], clustered["alpha"]) == ("negative-binomial", 2.0)
    assert poisson["m"] == pytest.approx(points[0].m)
    band = poisson["band"]
    assert band["method"] == "bonferroni-wilson" and band["confidence"] == 0.95
    assert band["m_low"] <= poisson["m"] <= band["m_high"]
    json.dumps(report)
