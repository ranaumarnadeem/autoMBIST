"""Monte Carlo repair-yield sweep on the RTL.

A repair-rate curve R(k): run N independent randomized fault-injection trials
at each of several fault counts k, classify each trial's OBSERVED failures
(never the chip's own self-report -- see ``bira_input.fail_cells``'s own
docstring) against a hypothetical spare budget via the existing BIRA solver,
and report the fraction of trials that solver actually repairs. This is the
"repair rate" metric from the BIRA literature, a property of the spare budget
and the defects, not a yield: ``autombist yield-multiplier``
(``autombist.yieldsim.model``) turns the curve into yield with redundancy for an
ASSUMED defect-count distribution, never a silicon-calibrated one.

Reuses the existing generate/simulate pipeline unchanged, per trial: each
trial is a REAL ``generate_from_config(use_saboteur=True, ...)`` +
``run_simulation(fail_scan=True)`` invocation, exactly the same pipeline
every existing fault-injection test in this repo already uses (see
``tests/integration/test_classic_fail_coordinates_e2e.py``) -- zero changes
to ``generator.py``/``runner.py``/``reporting.py``. The spare budget under
evaluation (``num_spare_rows``/``num_spare_cols``) is a parameter INDEPENDENT
of the wrapper's own config: this is OFFLINE analysis ("would this budget
have repaired what was actually observed"), not on-chip self-repair, so the
same simulated array can be evaluated against several hypothetical budgets
without regenerating anything.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .bira_input import fail_cells
from .generator import generate_from_config, load_config
from .repair.bira import analyze
from .repair.types import RepairSolution, SpareGeometry, Unrepairable
from .runner import run_simulation
from .yieldsim.stats import wilson_interval

__all__ = [
    "SCHEMA_VERSION",
    "OBSERVATION",
    "OBSERVABLE_FAULT_TYPES",
    "check_observable",
    "TrialResult",
    "DensityPoint",
    "run_trial",
    "sweep",
    "build_sweep_report",
    "write_sweep_report",
]

# Independent of reporting.py's own per-simulation-run schema_version -- this
# is a sibling artifact to results.json/latest.json, not a variant of it. Bump
# the major only on an incompatible change to build_sweep_report's shape.
# 1.1 (additive, a 1.0 reader still parses it): each trial's "status"
# (repaired / unrepairable / invalid) and "invalid_reason", each point's
# "valid_trials", "repaired_count", "invalid_count" and "wilson" (the 95%
# Wilson interval), and the top-level "observation", "engine", "seeding" and
# "defect_model" (whose "unit" says what "faults" counts). A point's
# "repair_rate" now counts valid trials only.
SCHEMA_VERSION = "1.1"


#: The only observation this sweep has: a functional solid-0 / solid-1 scan
#: through the wrapper's functional port, after the faults are in place. It sees
#: stuck-at faults only. A transition fault's delayed write has committed long
#: before the scan reads the word back, and a port-coupling fault needs a read
#: and a write to the same address in the same cycle, which the scan never does.
OBSERVATION = "functional-scan"
OBSERVABLE_FAULT_TYPES = ("stuck-at",)
#: The report's engine name, as the yieldsim engines name theirs.
ENGINE = "rtl-functional-scan"


def check_observable(fault_type: str) -> None:
    """Raise ValueError when the functional scan cannot observe ``fault_type``:
    every trial would then report zero failing cells and a 100% repair rate."""
    if fault_type not in OBSERVABLE_FAULT_TYPES:
        raise ValueError(
            f"the yield sweep observes failures with a functional solid-0/solid-1 scan, which sees "
            f"{', '.join(OBSERVABLE_FAULT_TYPES)} faults only; {fault_type!r} faults would all read "
            "back correctly and every trial would count as repaired"
        )


@dataclass(slots=True, frozen=True)
class TrialResult:
    """One randomized fault-injection trial's outcome. A trial is invalid, and
    neither repaired nor unrepairable, when its observation did not complete:
    ``outcome`` is then None and ``invalid_reason`` says why."""

    faults: int
    seed: int
    fail_cells: frozenset[tuple[int, int]]
    outcome: object  # RepairSolution | Unrepairable, from repair.bira; None when invalid
    invalid_reason: str | None = None

    @property
    def valid(self) -> bool:
        return self.invalid_reason is None

    @property
    def repaired(self) -> bool:
        return self.valid and isinstance(self.outcome, RepairSolution)

    @property
    def status(self) -> str:
        if not self.valid:
            return "invalid"
        return "repaired" if self.repaired else "unrepairable"


@dataclass(slots=True, frozen=True)
class DensityPoint:
    """All trials run at one fault count -- one point on the repair-yield curve."""

    faults: int
    trials: tuple[TrialResult, ...]

    @property
    def valid_trials(self) -> int:
        return sum(1 for t in self.trials if t.valid)

    @property
    def repaired_count(self) -> int:
        return sum(1 for t in self.trials if t.repaired)

    @property
    def repair_rate(self) -> float:
        """Fraction of the VALID trials repair.bira.analyze() solved; invalid
        trials (an observation that did not complete) are left out rather than
        counted as repaired. 0.0 when no trial is valid (never divides by zero,
        never a spurious 100%); ``valid_trials`` tells that case apart."""
        valid = self.valid_trials
        if not valid:
            return 0.0
        return self.repaired_count / valid


def _trial_invalid_reason(report: dict[str, Any]) -> str | None:
    """Why a simulation report cannot be trusted as a complete fail bitmap, or None.

    run_simulation already raises on a failing simulator exit code, so a report
    that reaches here says "pass"; what it can still lack is a completed scan,
    a clean test run, or a read-back of every word."""
    if "fail_bitmap" not in report:
        return "the fail scan did not complete (no FAIL_SCAN_COMPLETE marker)"
    junit = report.get("junit") or {}
    summary = junit.get("summary") or {}
    if summary.get("failures") or summary.get("errors"):
        return f"the simulation had {summary.get('failures', 0)} failure(s) and {summary.get('errors', 0)} error(s)"
    tests = {t.get("name"): t.get("status") for t in junit.get("tests") or []}
    if junit.get("exists") and tests.get("test_fail_scan") not in (None, "passed"):
        return f"test_fail_scan did not pass (status {tests['test_fail_scan']!r})"
    unknown = report.get("fail_scan_unknown_addrs") or []
    if unknown:
        return f"{len(unknown)} word(s) read back as unknown (X/Z), so their cells were not observed"
    return None


def run_trial(
    config_path: Path,
    trial_outdir: Path,
    *,
    faults: int,
    seed: int,
    num_spare_rows: int,
    num_spare_cols: int = 0,
    algo: str = "march-c",
    fault_type: str = "stuck-at",
    pulse_width_ns: int = 2,
) -> TrialResult:
    """Run ONE fault-injection trial and classify it against a spare budget.

    ``num_spare_rows``/``num_spare_cols`` describe the budget under
    evaluation, not the wrapper's own ``redundancy:`` config; ``config_path``
    must not declare one (the fault-injecting saboteur has no spare rows).

    A trial whose scan did not complete, failed, or read a word as X/Z is
    returned invalid (``outcome`` None), never as repaired.
    """
    check_observable(fault_type)
    wrapper_path = generate_from_config(
        config_path,
        trial_outdir,
        use_saboteur=True,
        faults=faults,
        fault_seed=seed,
        fault_type=fault_type,
        pulse_width_ns=pulse_width_ns,
        algo=algo,
    )
    result = run_simulation(wrapper_path.parent, fail_scan=True)
    cells = fail_cells(result.report)
    invalid = _trial_invalid_reason(result.report)
    if invalid is not None:
        return TrialResult(faults=faults, seed=seed, fail_cells=frozenset(cells), outcome=None,
                           invalid_reason=invalid)

    config = load_config(config_path)
    geometry = SpareGeometry(
        base_words=1 << config["addr_width"],
        word_size=config["data_width"],
        num_spare_rows=num_spare_rows,
        num_spare_cols=num_spare_cols,
    )
    outcome = analyze(cells, geometry)

    return TrialResult(
        faults=faults,
        seed=seed,
        fail_cells=frozenset(cells),
        outcome=outcome,
    )


def sweep(
    config_path: Path,
    outdir_root: Path,
    *,
    fault_counts: Sequence[int],
    trials_per_point: int,
    num_spare_rows: int,
    num_spare_cols: int = 0,
    algo: str = "march-c",
    fault_type: str = "stuck-at",
    pulse_width_ns: int = 2,
    base_seed: int = 0,
) -> list[DensityPoint]:
    """Run the full Monte Carlo repair-yield sweep: ``trials_per_point``
    independent trials at each of ``fault_counts``.

    Seeds are derived deterministically from ``base_seed`` (never
    ``random``/``time``-based) so a sweep is exactly reproducible -- matching
    the same "reproducible fault injection" contract the existing
    ``--seed`` CLI option already gives a single run.

    Raises ValueError, before any trial runs, for a fault type the scan cannot
    observe, a non-positive trial count, or a negative or repeated fault count.
    """
    check_observable(fault_type)
    if trials_per_point < 1:
        raise ValueError(f"trials per point must be at least 1, got {trials_per_point}")
    if any(k < 0 for k in fault_counts):
        raise ValueError(f"fault counts must be non-negative, got {list(fault_counts)}")
    if len(set(fault_counts)) != len(fault_counts):
        raise ValueError(f"fault counts must not repeat (a repeat reruns the same seeds), got {list(fault_counts)}")
    points: list[DensityPoint] = []
    for faults in fault_counts:
        trials = []
        for i in range(trials_per_point):
            seed = base_seed + faults * trials_per_point + i
            trial_outdir = outdir_root / f"faults{faults}_seed{seed}"
            trials.append(
                run_trial(
                    config_path,
                    trial_outdir,
                    faults=faults,
                    seed=seed,
                    num_spare_rows=num_spare_rows,
                    num_spare_cols=num_spare_cols,
                    algo=algo,
                    fault_type=fault_type,
                    pulse_width_ns=pulse_width_ns,
                )
            )
        points.append(DensityPoint(faults=faults, trials=tuple(trials)))
    return points


def _trial_to_dict(trial: TrialResult) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "seed": trial.seed,
        "fail_cell_count": len(trial.fail_cells),
        "status": trial.status,
    }
    outcome = trial.outcome
    if not trial.valid:
        entry.update({"repaired": False, "invalid_reason": trial.invalid_reason})
    elif isinstance(outcome, RepairSolution):
        entry.update({"repaired": True, "row_map": outcome.row_map, "col_map": outcome.col_map})
    else:
        assert isinstance(outcome, Unrepairable)
        entry.update({
            "repaired": False,
            "reason": outcome.reason,
            "faulty_rows": list(outcome.faulty_rows),
            "faulty_cols": list(outcome.faulty_cols),
        })
    return entry


def _wilson(point: DensityPoint, confidence: float = 0.95) -> dict[str, float]:
    low, high = wilson_interval(point.repaired_count, point.valid_trials, confidence)
    return {"confidence": confidence, "low": low, "high": high}


def build_sweep_report(
    points: Sequence[DensityPoint],
    config_path: Path,
    *,
    tool_version: str,
    num_spare_rows: int,
    num_spare_cols: int,
    algo: str,
    fault_type: str,
    trials_per_point: int,
    base_seed: int,
) -> dict[str, Any]:
    """The JSON-serializable sweep summary: per-point repair rate plus
    per-trial detail (seed, observed fail-cell count, repaired verdict).
    Does not include each trial's raw fail_cells set -- a sweep is exactly
    reproducible from (config, faults, seed), so the full set is always
    recoverable by re-running that one trial rather than duplicating it into
    every report.
    """
    config = load_config(config_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool_version": tool_version,
        "config": {
            "memory_name": config["memory_name"],
            "addr_width": config["addr_width"],
            "data_width": config["data_width"],
            "algo": algo,
            "fault_type": fault_type,
        },
        "spare_budget": {"num_spare_rows": num_spare_rows, "num_spare_cols": num_spare_cols},
        "trials_per_point": trials_per_point,
        "base_seed": base_seed,
        # How failures were observed. The scan does not run the march, so the
        # algorithm only chooses which controller RTL is compiled.
        "observation": {"mode": OBSERVATION, "uses_algo": False},
        "engine": {"name": ENGINE},
        # The original seed formula, kept so a 1.0 sweep's trials reproduce.
        "seeding": {"scheme": "linear", "base_seed": base_seed},
        # Each trial injects exactly "faults" distinct cells (fault_gen samples sites).
        "defect_model": {"model": "saboteur", "fault_type": fault_type, "unit": "cells"},
        "points": [
            {
                "faults": point.faults,
                "repair_rate": point.repair_rate,
                "valid_trials": point.valid_trials,
                "repaired_count": point.repaired_count,
                "invalid_count": len(point.trials) - point.valid_trials,
                "wilson": _wilson(point),
                "trials": [_trial_to_dict(trial) for trial in point.trials],
            }
            for point in points
        ],
    }


def write_sweep_report(report: dict[str, Any], report_dir: Path) -> Path:
    """Write the sweep summary to ``report_dir/yield_sweep.json`` -- a sibling
    filename to (never a collision with) any per-trial ``results.json``/
    ``latest.json``, which each live under that trial's OWN outdir, not
    ``report_dir``."""
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "yield_sweep.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report_path
