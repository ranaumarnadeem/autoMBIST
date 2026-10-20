"""The Monte Carlo sweep core shared by every trial engine.

A sweep runs ``trials`` independent trials at each defect count k and records,
per trial, whether the spare budget repaired it. A trial engine decides what a
trial is: drawing defects and solving the bitmap directly
(:mod:`yieldsim.bitmap_engine`), or simulating RTL (``autombist.yield_sweep``).
This module only loops, derives seeds, counts and reports, so every engine's
numbers are computed and written the same way.

Each trial has one of three outcomes. "invalid" means the trial's observation
could not be trusted (an RTL scan that did not complete, for example): it is
reported, but left out of the repair rate rather than counted either way.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .stats import wilson_interval

__all__ = [
    "STATUSES",
    "SEED_SCHEMES",
    "TrialRecord",
    "TrialEngine",
    "PointResult",
    "SweepResult",
    "derive_seed",
    "run_sweep",
    "points_report",
    "REPORT_SCHEMA_VERSION",
    "build_report",
]

STATUSES = ("repaired", "unrepairable", "invalid")
#: "linear" is the RTL sweep's original scheme, base + k * trials + i: it keeps old
#: seeds reproducible, but changing the trial count reshuffles every trial and
#: different base seeds overlap. "blake2b" hashes (base, k, i) instead.
SEED_SCHEMES = ("linear", "blake2b")
#: The sweep report schema every engine writes (yield_sweep.json and yield_sim.json).
REPORT_SCHEMA_VERSION = "1.1"
TrialDetail = Literal["none", "failures", "all"]


@dataclass(frozen=True, slots=True)
class TrialRecord:
    """One trial's outcome.

    ``cells`` is the number of distinct failing cells the repair analysis saw;
    ``spares_used`` and ``min_spares`` are filled in when the engine knows them;
    ``extras`` holds engine-specific, JSON-serializable detail.
    """

    k: int
    seed: int
    status: str
    invalid_reason: str | None = None
    cells: int | None = None
    spares_used: int | None = None
    min_spares: int | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"trial status must be one of {STATUSES}, got {self.status!r}")
        if (self.status == "invalid") != (self.invalid_reason is not None):
            raise ValueError("an invalid trial needs an invalid_reason, and only an invalid one has it")

    @property
    def valid(self) -> bool:
        return self.status != "invalid"

    @property
    def repaired(self) -> bool:
        return self.status == "repaired"


class TrialEngine(Protocol):
    """What :func:`run_sweep` needs from an engine."""

    #: Short engine name, recorded in the report ("bitmap", "rtl-functional-scan", ...).
    name: str
    #: What k counts: "defects" (one defect may fail many cells) or "cells".
    unit: str

    def describe(self) -> dict[str, Any]:
        """JSON-serializable description of the engine's setup, for the report."""
        ...

    def run_trial(self, k: int, seed: int) -> TrialRecord:
        """Run one trial with k defects (or cells) from ``seed``."""
        ...


@dataclass(frozen=True, slots=True)
class PointResult:
    """All trials at one k."""

    k: int
    trials: tuple[TrialRecord, ...]

    @property
    def valid_trials(self) -> int:
        return sum(1 for t in self.trials if t.valid)

    @property
    def repaired_count(self) -> int:
        return sum(1 for t in self.trials if t.repaired)

    @property
    def invalid_count(self) -> int:
        return len(self.trials) - self.valid_trials

    @property
    def repair_rate(self) -> float | None:
        """Repaired over valid trials; None when no trial is valid (unknown, not 0)."""
        valid = self.valid_trials
        return self.repaired_count / valid if valid else None

    def wilson(self, confidence: float = 0.95) -> tuple[float, float]:
        return wilson_interval(self.repaired_count, self.valid_trials, confidence)


@dataclass(frozen=True, slots=True)
class SweepResult:
    engine: str
    unit: str
    description: Mapping[str, Any]
    trials_per_point: int
    base_seed: int
    seed_scheme: str
    points: tuple[PointResult, ...]


def derive_seed(scheme: str, base_seed: int, k: int, i: int, trials_per_point: int) -> int:
    """The seed of trial ``i`` at defect count ``k``."""
    if scheme == "linear":
        return base_seed + k * trials_per_point + i
    if scheme == "blake2b":
        digest = hashlib.blake2b(f"{base_seed}:{k}:{i}".encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big")
    raise ValueError(f"unknown seed scheme {scheme!r}: use one of {SEED_SCHEMES}")


def _check_sweep_arguments(ks: Sequence[int], trials: int) -> None:
    if trials < 1:
        raise ValueError(f"trials per point must be at least 1, got {trials}")
    if any(k < 0 for k in ks):
        raise ValueError(f"defect counts must be non-negative, got {list(ks)}")
    if len(set(ks)) != len(ks):
        raise ValueError(f"defect counts must not repeat (a repeat reruns the same seeds), got {list(ks)}")


def run_sweep(
    engine: TrialEngine,
    ks: Sequence[int],
    trials: int,
    base_seed: int = 0,
    *,
    seed_scheme: str = "blake2b",
    progress: Callable[[int, int, TrialRecord], None] | None = None,
) -> SweepResult:
    """Run ``trials`` trials of ``engine`` at each k in ``ks`` (in that order).

    ``progress`` is called after every trial with (k, i, record). Raises
    ValueError for bad arguments before any trial runs."""
    _check_sweep_arguments(ks, trials)
    if seed_scheme not in SEED_SCHEMES:
        raise ValueError(f"unknown seed scheme {seed_scheme!r}: use one of {SEED_SCHEMES}")
    points = []
    for k in ks:
        records = []
        for i in range(trials):
            record = engine.run_trial(k, derive_seed(seed_scheme, base_seed, k, i, trials))
            if record.k != k:
                raise ValueError(f"engine {engine.name!r} returned a trial for k={record.k} when asked for k={k}")
            records.append(record)
            if progress is not None:
                progress(k, i, record)
        points.append(PointResult(k=k, trials=tuple(records)))
    return SweepResult(
        engine=engine.name, unit=engine.unit, description=dict(engine.describe()),
        trials_per_point=trials, base_seed=base_seed, seed_scheme=seed_scheme, points=tuple(points),
    )


def _trial_entry(record: TrialRecord) -> dict[str, Any]:
    entry: dict[str, Any] = {"seed": record.seed, "status": record.status, "repaired": record.repaired}
    if record.invalid_reason is not None:
        entry["invalid_reason"] = record.invalid_reason
    for key in ("cells", "spares_used", "min_spares"):
        value = getattr(record, key)
        if value is not None:
            entry[key] = value
    entry.update(record.extras)
    return entry


def points_report(
    result: SweepResult, *, trial_detail: TrialDetail = "all", confidence: float = 0.95,
) -> list[dict[str, Any]]:
    """The per-point part of a sweep report, in the shape ``yield_sweep.json``
    1.1 uses: "faults" is k (its unit is the report's), "repair_rate" is over
    valid trials (0.0 when none is valid; "valid_trials" tells that case apart),
    plus the Wilson interval. ``trial_detail`` keeps every trial, only the ones
    that were not repaired, or none."""
    if trial_detail not in ("none", "failures", "all"):
        raise ValueError(f"trial_detail must be none, failures or all, got {trial_detail!r}")
    rows = []
    for point in result.points:
        low, high = point.wilson(confidence)
        if trial_detail == "all":
            kept = point.trials
        elif trial_detail == "failures":
            kept = tuple(t for t in point.trials if not t.repaired)
        else:
            kept = ()
        rows.append({
            "faults": point.k,
            "repair_rate": point.repair_rate if point.repair_rate is not None else 0.0,
            "valid_trials": point.valid_trials,
            "repaired_count": point.repaired_count,
            "invalid_count": point.invalid_count,
            "wilson": {"confidence": confidence, "low": low, "high": high},
            "trials": [_trial_entry(t) for t in kept],
        })
    return rows


def build_report(
    result: SweepResult, *, trial_detail: TrialDetail = "failures", confidence: float = 0.95,
) -> dict[str, Any]:
    """A whole sweep report, schema 1.1: the engine's own description blocks
    (geometry, defect_model, analyzer, ...) at the top level, the unit of k in
    ``defect_model.unit``, the seeding, and :func:`points_report`. The caller
    adds ``generated_at`` and ``tool_version``."""
    description = dict(result.description)
    defect_model = dict(description.pop("defect_model", {}))
    defect_model["unit"] = result.unit
    return {
        **description,
        "schema_version": REPORT_SCHEMA_VERSION,
        "engine": {"name": result.engine},
        "defect_model": defect_model,
        "trials_per_point": result.trials_per_point,
        "base_seed": result.base_seed,
        "seeding": {"scheme": result.seed_scheme, "base_seed": result.base_seed},
        "trial_detail": trial_detail,
        "points": points_report(result, trial_detail=trial_detail, confidence=confidence),
    }
