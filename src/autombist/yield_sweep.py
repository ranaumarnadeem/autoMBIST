"""Monte Carlo repair-yield sweep (docs/diagnosis-yield-analysis-plan.md, §4-5).

A repair-yield curve: run N independent randomized fault-injection trials at
each of several defect densities, classify each trial's OBSERVED failures
(never the chip's own self-report -- see ``bira_input.fail_cells``'s own
docstring) against a hypothetical spare budget via the existing BIRA solver,
and report the fraction of trials that solver actually repairs. This is the
"repair rate" metric from the BIRA literature (algorithm-quality, not a
silicon-calibrated yield law -- see the plan doc's own §1/§6 for why a
closed-form Stapper/Poisson model needs real measured defect-density data
this project doesn't have).

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

__all__ = [
    "SCHEMA_VERSION",
    "TrialResult",
    "DensityPoint",
    "run_trial",
    "sweep",
    "build_sweep_report",
    "write_sweep_report",
]

# Independent of reporting.py's own per-simulation-run schema_version -- this
# is a sibling artifact (docs/diagnosis-yield-analysis-plan.md's own framing:
# "sibling to, not a mutation of, results.json/latest.json"), not a variant
# of it. Bump only on an incompatible change to build_sweep_report's shape.
SCHEMA_VERSION = "1.0"


@dataclass(slots=True, frozen=True)
class TrialResult:
    """One randomized fault-injection trial's outcome."""

    faults: int
    seed: int
    fail_cells: frozenset[tuple[int, int]]
    outcome: object  # RepairSolution | Unrepairable, from repair.bira

    @property
    def repaired(self) -> bool:
        return isinstance(self.outcome, RepairSolution)


@dataclass(slots=True, frozen=True)
class DensityPoint:
    """All trials run at one fault count -- one point on the repair-yield curve."""

    faults: int
    trials: tuple[TrialResult, ...]

    @property
    def repair_rate(self) -> float:
        """Fraction of trials repair.bira.analyze() actually solved. 0.0 for
        an empty trial set (never divides by zero, never a spurious 100%)."""
        if not self.trials:
            return 0.0
        return sum(1 for t in self.trials if t.repaired) / len(self.trials)


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
    evaluation, not the wrapper's own (if any) ``redundancy:`` config --
    ``config_path`` need not declare redundancy at all.
    """
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
    """
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


def _outcome_to_dict(outcome: RepairSolution | Unrepairable) -> dict[str, Any]:
    if isinstance(outcome, RepairSolution):
        return {"repaired": True, "row_map": outcome.row_map, "col_map": outcome.col_map}
    return {
        "repaired": False,
        "reason": outcome.reason,
        "faulty_rows": list(outcome.faulty_rows),
        "faulty_cols": list(outcome.faulty_cols),
    }


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
        "points": [
            {
                "faults": point.faults,
                "repair_rate": point.repair_rate,
                "trials": [
                    {
                        "seed": trial.seed,
                        "fail_cell_count": len(trial.fail_cells),
                        **_outcome_to_dict(trial.outcome),
                    }
                    for trial in point.trials
                ],
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
