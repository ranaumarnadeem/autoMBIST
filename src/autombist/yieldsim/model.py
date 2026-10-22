"""Yield with redundancy from a measured repair curve.

A sweep measures R(k), the probability that a die with exactly k defects is
repairable with the spare budget. A die's yield with redundancy weights that by
how likely each defect count is:

    Y_red = sum_k P(k) R(k),   with R(0) = 1,
    Y0    = P(0)                 (the yield with no redundancy),
    M     = Y_red / Y0           (the yield multiplier).

With Poisson counts this is Ramadan's general model (Intel Technology Journal
Q4 1997, eq. 26: M = 1 + R1 lam + R2 lam^2/2! + ...); with negative binomial
counts (clustering parameter alpha) it is Stapper's sum over fixable fault
patterns (IBM J. Res. Dev. 1980; 1989). Every sweep point has k >= 1, so M is
defined only after choosing a defect-count distribution: it is a function of an
assumed initial yield (or mean defect count lam) and alpha, never one number.
The defect statistics are assumptions, not calibrated to any silicon.

``area_ratio`` scales the mean defect count from the base array to the whole
physical array when the spares themselves can be hit (Stapper's convention):
the base array's yield Y0 is unchanged, but the redundant array draws its
defects over a larger area. Use 1.0 when the sweep never puts defects in the
spares.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .stats import bonferroni_confidence, wilson_interval

__all__ = [
    "ITC2020_ALPHA",
    "ITC2020_LAM",
    "RepairCurve",
    "YieldPoint",
    "pmf",
    "tail",
    "y0_from_lam",
    "lam_from_y0",
    "occupancy_repair_probability",
    "curve_from_report",
    "yield_with_redundancy",
    "yield_points",
    "MULTIPLIER_SCHEMA_VERSION",
    "DISCLAIMER",
    "multiplier_report",
]

#: ``yield_multiplier.json``'s own schema, independent of the sweep report's.
MULTIPLIER_SCHEMA_VERSION = "1.0"
DISCLAIMER = (
    "Assumed defect statistics, not calibrated to silicon: every figure depends on the chosen "
    "initial yield (or mean defect count) and clustering alpha, and the repair curve comes from "
    "simulated defects."
)

#: The Polya-Eggenberger defect-count parameters of the ITC'2020 RA Benchmarks
#: (Lee et al., ITC 2020, after Stapper 1975): A = 1.0, alpha = 0.6212, beta = 3.23,
#: i.e. a negative binomial with clustering 0.6212 and mean A*alpha*beta defects.
ITC2020_ALPHA = 0.6212
ITC2020_LAM = 1.0 * 0.6212 * 3.23


def _check_alpha(alpha: float | None) -> None:
    if alpha is not None and not (alpha > 0 and math.isfinite(alpha)):
        raise ValueError(f"the clustering parameter alpha must be a positive number (or Poisson), got {alpha!r}")


def _check_lam(lam: float) -> None:
    if not (lam >= 0 and math.isfinite(lam)):
        raise ValueError(f"the mean defect count lam must be a finite number >= 0, got {lam!r}")


def pmf(k: int, lam: float, alpha: float | None = None) -> float:
    """P(k defects): Poisson with mean ``lam`` when ``alpha`` is None, else the
    negative binomial with mean ``lam`` and clustering ``alpha`` (alpha -> inf is Poisson)."""
    _check_lam(lam)
    _check_alpha(alpha)
    if k < 0:
        return 0.0
    if lam == 0:
        return 1.0 if k == 0 else 0.0
    if alpha is None:
        return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))
    return math.exp(
        math.lgamma(k + alpha) - math.lgamma(k + 1) - math.lgamma(alpha)
        + alpha * math.log(alpha / (alpha + lam)) + k * math.log(lam / (alpha + lam))
    )


def tail(k_max: int, lam: float, alpha: float | None = None) -> float:
    """P(more than ``k_max`` defects)."""
    return max(0.0, 1.0 - math.fsum(pmf(k, lam, alpha) for k in range(k_max + 1)))


def y0_from_lam(lam: float, alpha: float | None = None) -> float:
    """The yield without redundancy, P(0 defects)."""
    return pmf(0, lam, alpha)


def lam_from_y0(y0: float, alpha: float | None = None) -> float:
    """The mean defect count that gives yield ``y0`` without redundancy."""
    _check_alpha(alpha)
    if not 0.0 < y0 <= 1.0:
        raise ValueError(f"the initial yield must be in (0, 1], got {y0!r}")
    if alpha is None:
        return -math.log(y0)
    return alpha * (y0 ** (-1.0 / alpha) - 1.0)


def occupancy_repair_probability(k: int, blocks: int, spares: int) -> Fraction:
    """Exact R(k) for the classic block-redundancy model: k defects land uniformly
    and independently on ``blocks`` equal blocks, and the die is repairable when at
    most ``spares`` distinct blocks are hit (each hit block replaced by a spare
    block). Ramadan's R_n and Stapper's 1989 Table 4 use this model."""
    if k < 0 or blocks < 1 or spares < 0:
        raise ValueError(f"need k >= 0, blocks >= 1, spares >= 0; got {k}, {blocks}, {spares}")
    if k == 0:
        return Fraction(1)

    def onto(defects: int, hit: int) -> int:  # placements of `defects` covering exactly `hit` given blocks
        return sum((-1) ** i * math.comb(hit, i) * (hit - i) ** defects for i in range(hit + 1))

    good = sum(math.comb(blocks, j) * onto(k, j) for j in range(1, min(spares, k) + 1))
    return Fraction(good, blocks ** k)


@dataclass(frozen=True, slots=True)
class RepairCurve:
    """Measured repair counts: k -> (repaired trials, valid trials), for k = 1..K
    (k = 0 is always repairable). ``unit`` says what k counts."""

    counts: Mapping[int, tuple[int, int]]
    unit: str = "cells"

    def __post_init__(self) -> None:
        for k, (repaired, valid) in self.counts.items():
            if k < 0 or valid < 0 or not 0 <= repaired <= valid:
                raise ValueError(f"bad repair count at k={k}: {repaired} of {valid}")

    @property
    def k_max(self) -> int:
        return max(self.counts, default=0)

    def missing(self) -> list[int]:
        """The k in 1..K with no point, or with no valid trial."""
        return [k for k in range(1, self.k_max + 1) if self.counts.get(k, (0, 0))[1] == 0]

    def rate(self, k: int) -> float:
        if k == 0:
            return 1.0
        repaired, valid = self.counts[k]
        return repaired / valid


def curve_from_report(report: Mapping[str, Any]) -> RepairCurve:
    """The repair curve of a ``yield_sweep.json`` (schema 1.0 or 1.1) or
    ``yield_sim.json``. A 1.0 report has no invalid trials, so every trial counts."""
    version = str(report.get("schema_version", ""))
    if version.split(".")[0] != "1":
        raise ValueError(f"unsupported sweep report schema_version {version!r} (this reads 1.x)")
    counts: dict[int, tuple[int, int]] = {}
    for point in report.get("points", []):
        k = int(point["faults"])
        if "valid_trials" in point:
            valid, repaired = int(point["valid_trials"]), int(point["repaired_count"])
        else:
            trials = point.get("trials", [])
            valid, repaired = len(trials), sum(1 for t in trials if t.get("repaired"))
        if k in counts:
            raise ValueError(f"the report lists k={k} twice")
        counts[k] = (repaired, valid)
    unit = (report.get("defect_model") or {}).get("unit", "cells")
    return RepairCurve(counts=counts, unit=unit)


@dataclass(frozen=True, slots=True)
class YieldPoint:
    """Yield with redundancy at one assumed defect-count distribution.

    ``y_red`` takes R(k) = 0 beyond the measured K (a lower bound); ``y_red_high``
    adds the tail at most at the curve's smallest measured rate (true R(k) never
    rises with k). ``band_low``/``band_high`` are the Bonferroni-combined Wilson
    bounds over the K points at ``confidence``, with the same tail rule."""

    y0: float
    lam: float
    alpha: float | None
    area_ratio: float
    k_max: int
    tail_mass: float
    y_red: float
    y_red_high: float
    band_low: float
    band_high: float
    confidence: float

    @property
    def m(self) -> float:
        return self.y_red / self.y0

    @property
    def m_high(self) -> float:
        return self.y_red_high / self.y0

    @property
    def m_band(self) -> tuple[float, float]:
        return self.band_low / self.y0, self.band_high / self.y0

    @property
    def r_cond(self) -> float:
        """The repair rate over dies with at least one defect (BIRA's "repaired
        over defective"), for the redundant array's own defect distribution."""
        p0 = pmf(0, self.lam * self.area_ratio, self.alpha)
        return (self.y_red - p0) / (1.0 - p0) if p0 < 1.0 else 1.0


def yield_with_redundancy(
    curve: RepairCurve,
    *,
    y0: float | None = None,
    lam: float | None = None,
    alpha: float | None = None,
    area_ratio: float = 1.0,
    confidence: float = 0.95,
) -> YieldPoint:
    """Weight ``curve`` by the defect-count distribution with clustering ``alpha``
    (None for Poisson) and either an initial yield ``y0`` or a mean defect count
    ``lam`` on the base array.

    Raises ValueError unless the curve covers every k from 1 to its largest k with
    at least one valid trial each (sweep with --faults 1..K)."""
    if (y0 is None) == (lam is None):
        raise ValueError("give exactly one of y0 or lam")
    if not (area_ratio >= 1.0 and math.isfinite(area_ratio)):
        raise ValueError(f"area_ratio must be >= 1 (physical array over base array), got {area_ratio!r}")
    missing = curve.missing()
    if missing or curve.k_max < 1:
        raise ValueError(
            "the yield weighting needs a measured repair rate at every k from 1 to the largest; "
            f"missing k = {missing or 'all'} (sweep a contiguous range, e.g. --faults 1..{max(curve.k_max, 1)})"
        )
    base_lam = lam_from_y0(y0, alpha) if y0 is not None else lam
    _check_lam(base_lam)
    base_y0 = y0 if y0 is not None else y0_from_lam(base_lam, alpha)
    phys_lam = base_lam * area_ratio
    k_max = curve.k_max
    weights = [pmf(k, phys_lam, alpha) for k in range(k_max + 1)]
    tail_mass = tail(k_max, phys_lam, alpha)

    per_point = bonferroni_confidence(confidence, k_max)
    bounds = {k: wilson_interval(curve.counts[k][0], curve.counts[k][1], per_point) for k in range(1, k_max + 1)}
    rates = [curve.rate(k) for k in range(k_max + 1)]

    y_red = math.fsum(w * r for w, r in zip(weights, rates))
    y_red_high = y_red + tail_mass * min(rates[1:])
    band_low = weights[0] + math.fsum(weights[k] * bounds[k][0] for k in range(1, k_max + 1))
    band_high = (weights[0] + math.fsum(weights[k] * bounds[k][1] for k in range(1, k_max + 1))
                 + tail_mass * min(bounds[k][1] for k in range(1, k_max + 1)))
    return YieldPoint(
        y0=base_y0, lam=base_lam, alpha=alpha, area_ratio=area_ratio, k_max=k_max, tail_mass=tail_mass,
        y_red=y_red, y_red_high=min(1.0, y_red_high), band_low=band_low, band_high=min(1.0, band_high),
        confidence=confidence,
    )


def yield_points(
    curve: RepairCurve,
    *,
    alphas: Sequence[float | None],
    y0_values: Sequence[float] = (),
    lam_values: Sequence[float] = (),
    area_ratio: float = 1.0,
    confidence: float = 0.95,
) -> list[YieldPoint]:
    """:func:`yield_with_redundancy` over every (alpha, y0) and (alpha, lam) pair."""
    points = []
    for alpha in alphas:
        for value in y0_values:
            points.append(yield_with_redundancy(curve, y0=value, alpha=alpha, area_ratio=area_ratio, confidence=confidence))
        for value in lam_values:
            points.append(yield_with_redundancy(curve, lam=value, alpha=alpha, area_ratio=area_ratio, confidence=confidence))
    return points


def _point_entry(point: YieldPoint) -> dict[str, Any]:
    m_low, m_high = point.m_band
    return {
        "distribution": "poisson" if point.alpha is None else "negative-binomial",
        "alpha": point.alpha,
        "y0": point.y0,
        "lam": point.lam,
        "area_ratio": point.area_ratio,
        "y_red": point.y_red,
        "y_red_high": point.y_red_high,
        "m": point.m,
        "m_high": point.m_high,
        "r_cond": point.r_cond,
        "tail_mass": point.tail_mass,
        "band": {
            "confidence": point.confidence,
            "method": "bonferroni-wilson",
            "y_red_low": point.band_low,
            "y_red_high": point.band_high,
            "m_low": m_low,
            "m_high": m_high,
        },
    }


def multiplier_report(
    curve: RepairCurve, points: Sequence[YieldPoint], *, source: Mapping[str, Any],
) -> dict[str, Any]:
    """The JSON-serializable ``yield_multiplier.json`` body: the repair curve
    read, and one entry per assumed distribution. ``source`` describes the sweep
    report it came from (path, sha256, schema version)."""
    return {
        "schema_version": MULTIPLIER_SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "source": dict(source),
        "curve": {
            "unit": curve.unit,
            "k_max": curve.k_max,
            "points": [
                {"faults": k, "repaired_count": curve.counts[k][0], "valid_trials": curve.counts[k][1]}
                for k in sorted(curve.counts)
            ],
        },
        "truncation": (
            "y_red takes the repair rate as 0 beyond k_max (a lower bound); y_red_high adds "
            "P(k > k_max) at the smallest measured rate (the rate never rises with k)"
        ),
        "points": [_point_entry(p) for p in points],
    }
