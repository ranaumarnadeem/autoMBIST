"""Binomial confidence intervals for repair rates.

A repair rate is a binomial proportion (repaired trials out of valid trials), so
its uncertainty is a Wilson score interval. When several per-point intervals are
combined into one statement (a yield figure summed over defect counts), each
point's confidence is raised by the Bonferroni rule so the combined statement
holds at the requested level.
"""
from __future__ import annotations

import math
from statistics import NormalDist

__all__ = ["wilson_interval", "bonferroni_confidence"]


def _check_confidence(confidence: float) -> None:
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be strictly between 0 and 1, got {confidence!r}")


def wilson_interval(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    """The two-sided Wilson score interval for ``successes`` out of ``trials``.

    With no trials the proportion is unknown and the interval is (0, 1)."""
    _check_confidence(confidence)
    if trials < 0 or not 0 <= successes <= max(trials, 0):
        raise ValueError(f"need 0 <= successes <= trials, got {successes} of {trials}")
    if trials == 0:
        return 0.0, 1.0
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    p = successes / trials
    z2n = z * z / trials
    centre = (p + z2n / 2.0) / (1.0 + z2n)
    half = z * math.sqrt(p * (1.0 - p) / trials + z2n / (4.0 * trials)) / (1.0 + z2n)
    return max(0.0, centre - half), min(1.0, centre + half)


def bonferroni_confidence(confidence: float, statements: int) -> float:
    """The per-statement confidence that makes ``statements`` simultaneous
    intervals hold together at ``confidence``."""
    _check_confidence(confidence)
    if statements < 1:
        raise ValueError(f"need at least one statement, got {statements}")
    return 1.0 - (1.0 - confidence) / statements
