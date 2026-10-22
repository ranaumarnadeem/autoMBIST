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

Two ways to run it. :class:`RtlEngine` (what ``autombist yield-sweep`` uses)
plugs into ``autombist.yieldsim.sweep.run_sweep``: it generates and compiles
the wrapper once, rewrites the saboteur's fault masks per trial, observes with
the functional scan or the march's own fail log, and records each trial's
truth next to its observation. The original :func:`sweep` / :func:`run_trial`
keep their per-trial ``generate_from_config`` + ``run_simulation(fail_scan=True)``
pipeline and their report (:func:`build_sweep_report`) for existing callers.
The spare budget under
evaluation (``num_spare_rows``/``num_spare_cols``) is a parameter INDEPENDENT
of the wrapper's own config: this is OFFLINE analysis ("would this budget
have repaired what was actually observed"), not on-chip self-repair, so the
same simulated array can be evaluated against several hypothetical budgets
without regenerating anything.
"""
from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml

from .bira_input import fail_cells, observed_cells
from .fault_gen import FaultType, generate_fault_masks, write_fault_files
from .generator import generate_from_config, load_config
from .memory_model import render_memory_model
from .repair.bira import analyze, analyze_detailed, verify_solution
from .repair.types import RepairSolution, SpareFaults, SpareGeometry, Unrepairable
from .runner import run_simulation
from .yieldsim.defects import ArrayGeometry, class_counts, defect_model
from .yieldsim.stats import wilson_interval
from .yieldsim.sweep import TrialRecord

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
    "OBSERVATIONS",
    "RtlEngine",
    "CAUSES",
    "SelfRepairEngine",
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


# --- the RTL trial engine on the yieldsim core ---------------------------------

#: How a trial's failures are observed: the functional-port read-back after the
#: faults are in place, or the march's own per-read fail strobes.
OBSERVATIONS = {"functional": "functional-scan", "march": "march-log"}


def _stuck_cells(sa0: list[int], sa1: list[int], data_width: int) -> set[tuple[int, int]]:
    """Cells the stuck-at masks fault: dout = (real & sa0) | sa1, so a cell is
    stuck unless its sa0 bit is 1 and its sa1 bit 0."""
    return {(addr, bit) for addr, (w0, w1) in enumerate(zip(sa0, sa1)) for bit in range(data_width)
            if not (w0 >> bit) & 1 or (w1 >> bit) & 1}


class RtlEngine:
    """Trials on the generated RTL, compiled once per sweep.

    The wrapper and saboteur are generated once into ``workdir``; the saboteur
    loads its fault masks with ``$readmemh`` from fixed absolute paths at
    simulation start, so each trial only rewrites those mask files and the
    compiled simulation is reused.

    Every trial knows its truth (the injected cells) and its observation (the
    functional scan or the march log). It counts as repaired only when the
    repair computed from the observed cells also covers every injected cell; the
    record also says whether the truth and the observation were each
    repairable, how many injected cells the observed repair misses (escapes)
    and how many observed cells were never injected (overkill, expected 0).

    Without ``defects`` a trial injects k random cells exactly as the per-trial
    sweep does (the same masks from the same seed); with a ``yieldsim.defects``
    preset it injects k defects, each stuck at a random value over all its cells.
    """

    def __init__(
        self,
        config_path: Path,
        workdir: Path,
        *,
        num_spare_rows: int,
        num_spare_cols: int = 0,
        algo: str = "march-c",
        observe: str = "functional",
        fault_type: str = "stuck-at",
        defects: str | None = None,
        pulse_width_ns: int = 2,
    ) -> None:
        if observe not in OBSERVATIONS:
            raise ValueError(f"unknown observation {observe!r}: use one of {sorted(OBSERVATIONS)}")
        if observe == "functional":
            check_observable(fault_type)
        if defects is not None and fault_type != "stuck-at":
            raise ValueError("a defect preset injects stuck cells, so it needs --fault-type stuck-at")
        self.config_path = config_path
        self.workdir = workdir
        self.config = load_config(config_path)
        self.algo = algo
        self.observe = observe
        self.fault_type = fault_type
        self.pulse_width_ns = pulse_width_ns
        self.depth = 1 << self.config["addr_width"]
        self.width = self.config["data_width"]
        self.geometry = SpareGeometry(base_words=self.depth, word_size=self.width,
                                      num_spare_rows=num_spare_rows, num_spare_cols=num_spare_cols)
        self.defects = defects
        self.model = defect_model(defects) if defects is not None else None
        self.unit = self.model.unit if self.model is not None else "cells"
        self.name = ENGINE if observe == "functional" else "rtl-march-log"
        self._module: Path | None = None

    def describe(self) -> dict[str, Any]:
        defect_block: dict[str, Any] = {"model": "saboteur", "fault_type": self.fault_type}
        if self.model is not None:
            defect_block = {**self.model.describe(), "fault_type": self.fault_type,
                            "polarity": "each defect stuck at 0 or 1 with equal probability"}
        return {
            "config": {
                "memory_name": self.config["memory_name"],
                "addr_width": self.config["addr_width"],
                "data_width": self.config["data_width"],
                "algo": self.algo,
                "fault_type": self.fault_type,
            },
            "spare_budget": {"num_spare_rows": self.geometry.num_spare_rows,
                             "num_spare_cols": self.geometry.num_spare_cols},
            "observation": {"mode": OBSERVATIONS[self.observe], "uses_algo": self.observe == "march"},
            "defect_model": defect_block,
            "analyzer": {"solver": "repair.bira.analyze_detailed", "objective": "first",
                         "repaired_means": "the repair from the observed cells covers every injected cell"},
        }

    @property
    def module_dir(self) -> Path:
        """The generated wrapper's directory (generated on first use)."""
        if self._module is None:
            wrapper = generate_from_config(
                self.config_path, self.workdir, use_saboteur=True, faults=0, fault_seed=0,
                fault_type=self.fault_type, pulse_width_ns=self.pulse_width_ns, algo=self.algo,
            )
            self._module = wrapper.parent
        return self._module

    def _inject(self, k: int, seed: int) -> tuple[set[tuple[int, int]], dict[str, int] | None]:
        """Write the trial's masks; return the injected cells and, for a defect
        preset, the count of each defect class drawn."""
        classes = None
        kind = FaultType(self.fault_type)
        if self.model is None:
            mask1, mask2 = generate_fault_masks(addr_width=self.config["addr_width"], data_width=self.width,
                                                fault_type=kind, faults=k, seed=seed)
            truth = (_stuck_cells(mask1, mask2, self.width) if kind == FaultType.STUCK_AT else
                     {(a, b) for a, word in enumerate(mask1) for b in range(self.width) if (word >> b) & 1})
        else:
            rng = random.Random(seed)
            drawn = self.model.draw(rng, ArrayGeometry(rows=self.depth, cols=self.width), k)
            mask1, mask2 = [(1 << self.width) - 1] * self.depth, [0] * self.depth
            for defect in drawn:
                high = rng.randrange(2) == 1
                for row, bit in defect.cells:
                    if high:
                        mask1[row] |= 1 << bit
                        mask2[row] |= 1 << bit
                    else:
                        mask1[row] &= ~(1 << bit)
                        mask2[row] &= ~(1 << bit)
            classes = class_counts(drawn)
            truth = _stuck_cells(mask1, mask2, self.width)
        write_fault_files(outdir=self.module_dir / "faults", data_width=self.width, fault_type=kind,
                          mask1_words=mask1, mask2_words=mask2)
        return truth, classes

    def _invalid_reason(self, report: dict[str, Any]) -> str | None:
        if self.observe == "functional":
            return _trial_invalid_reason(report)
        if "march_log" not in report:
            return "the march log did not complete (no MARCH_LOG_COMPLETE marker)"
        summary = (report.get("junit") or {}).get("summary") or {}
        if summary.get("failures") or summary.get("errors"):
            return (f"the simulation had {summary.get('failures', 0)} failure(s) and "
                    f"{summary.get('errors', 0)} error(s) (the march log disagreed with bist_fail?)")
        return None

    def run_trial(self, k: int, seed: int) -> TrialRecord:
        truth, classes = self._inject(k, seed)
        result = run_simulation(self.module_dir, fail_scan=self.observe == "functional",
                                march_log=self.observe == "march")
        invalid = self._invalid_reason(result.report)
        if invalid is not None:
            return TrialRecord(k=k, seed=seed, status="invalid", invalid_reason=invalid,
                               extras={"truth_cells": len(truth)})
        observed = observed_cells(result.report, self.observe)
        from_truth = analyze_detailed(truth, self.geometry)
        from_observed = analyze_detailed(observed, self.geometry)
        extras: dict[str, Any] = {
            "fail_cell_count": len(observed),
            "truth_cells": len(truth),
            "overkill": len(observed - truth),
            "truth_repairable": from_truth.repaired,
            "observed_repairable": from_observed.repaired,
        }
        solution = from_observed.result
        if isinstance(solution, RepairSolution):
            escapes = sum(1 for a, b in truth if a not in solution.row_map and b not in solution.col_map)
            extras.update({"escapes": escapes, "row_map": solution.row_map, "col_map": solution.col_map})
            status = "repaired" if escapes == 0 else "unrepairable"
        else:
            extras.update({"reason": solution.reason, "faulty_rows": list(solution.faulty_rows),
                           "faulty_cols": list(solution.faulty_cols)})
            status = "unrepairable"
        if classes is not None:
            extras["defects"] = classes
        return TrialRecord(k=k, seed=seed, status=status, cells=len(observed),
                           spares_used=from_observed.spares_used if status == "repaired" else None, extras=extras)


# --- the chip's own self-repair, against the exact solver ----------------------

SELF_REPAIR_TEST_MODULE = "test_selfrepair_sweep"
#: Why a trial the exact solver repairs was not repaired on chip.
CAUSES = {
    "analyzer": "the on-chip analyzer gave up on a fail map the exact solver repairs",
    "faulty_spare": "the on-chip repair covers every faulty cell but allocates a faulty spare",
    "observation_escape": "the analyze pass did not observe every faulty cell",
    "false_pass": "the chip reported a pass but the repaired memory still reads wrong",
    "unexplained": "none of the above",
}


def _unpack(value: int | None, width: int, count: int) -> list[int]:
    value = value or 0
    return [(value >> (width * i)) & ((1 << width) - 1) for i in range(count)]


def _flat_ports(ports: dict[str, Any]) -> bool:
    return all(isinstance(value, str) for value in ports.values())


class SelfRepairEngine:
    """Trials of the chip's own on-chip self-repair, on the RTL.

    The sweep's config (without ``redundancy:``) gets a ``redundancy:`` block with
    on-chip self-repair over ``num_spare_rows`` spare rows and ``num_spare_cols``
    spare columns (the 2D analyzer when there are spare columns), optionally the
    diagnosis log. The memory is the fault-injectable behavioral model
    (``memory_model.render_memory_model`` with stuck-at masks over the physical
    array), compiled once; each trial rewrites the masks.

    Each trial draws defects from a ``yieldsim.defects`` preset, each stuck at a
    random value; with ``expose_spares`` over the physical array, so spare rows
    and spare lanes can be faulty. It records the exact repair of the truth
    (``analyze_detailed`` with the spare faults: the upper bound), the exact
    repair of what the analyze pass observed, and the chip's outcome, verified by
    an independent post-repair scan. A trial is repaired when the chip reports a
    pass and the scan reads back clean; when the exact solver repairs a trial the
    chip did not, ``cause`` says why (see :data:`CAUSES`).
    """

    name = "rtl-onchip-selfrepair"

    def __init__(
        self,
        config_path: Path,
        workdir: Path,
        *,
        num_spare_rows: int,
        num_spare_cols: int = 0,
        algo: str = "march-c",
        defects: str | None = None,
        expose_spares: bool = False,
        diagnosis_entries: int = 0,
        spare_retry: bool = False,
    ) -> None:
        if num_spare_rows < 1:
            raise ValueError("on-chip self-repair needs at least one spare row (the wrapper's redundancy path requires one)")
        if diagnosis_entries < 0:
            raise ValueError(f"diagnosis entries must be non-negative, got {diagnosis_entries}")
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict) and raw.get("redundancy"):
            raise ValueError("the sweep config must not declare redundancy: the spares are --spare-rows/--spare-cols")
        base = load_config(config_path)
        self.workdir = workdir
        self.algo = algo
        self.depth = 1 << base["addr_width"]
        self.width = base["data_width"]
        self.geometry = SpareGeometry(base_words=self.depth, word_size=self.width,
                                      num_spare_rows=num_spare_rows, num_spare_cols=num_spare_cols)
        self.expose_spares = expose_spares
        self.placement = (ArrayGeometry(rows=self.depth + num_spare_rows, cols=self.width + num_spare_cols)
                          if expose_spares else ArrayGeometry(rows=self.depth, cols=self.width))
        self.defects = defects or "singles"
        self.model = defect_model(self.defects)
        self.unit = self.model.unit
        self.diagnosis_entries = diagnosis_entries
        self.spare_retry = spare_retry
        self.config = self._with_redundancy(base)
        self._module: Path | None = None

    def _with_redundancy(self, base: dict[str, Any]) -> dict[str, Any]:
        config = copy.deepcopy(base)
        redundancy: dict[str, Any] = {
            "num_spare_rows": self.geometry.num_spare_rows,
            "num_spare_cols": self.geometry.num_spare_cols,
            "onchip_selfrepair": True,
        }
        if self.geometry.num_spare_cols:
            redundancy["onchip_col_repair"] = True
            ports = config.get("ports") or {}
            if _flat_ports(ports) and "spare_wen" not in ports:
                ports["spare_wen"] = "spare_wen0"
        if self.diagnosis_entries:
            redundancy.update(onchip_diagnosis=True, num_diagnosis_entries=self.diagnosis_entries)
        if self.spare_retry:
            redundancy["onchip_spare_retry"] = True
        config["redundancy"] = redundancy
        return config

    @property
    def area_ratio(self) -> float:
        return (self.placement.rows * self.placement.cols) / (self.depth * self.width)

    def describe(self) -> dict[str, Any]:
        cols = self.geometry.num_spare_cols
        return {
            "config": {"memory_name": self.config["memory_name"], "addr_width": self.config["addr_width"],
                       "data_width": self.width, "algo": self.algo, "fault_type": "stuck-at"},
            "spare_budget": {"num_spare_rows": self.geometry.num_spare_rows, "num_spare_cols": cols},
            "geometry": {"rows": self.depth, "cols": self.width, "banks": 1,
                         "spare_rows_per_bank": self.geometry.num_spare_rows, "spare_cols_per_bank": cols,
                         "spares_exposed": self.expose_spares, "area_ratio": self.area_ratio},
            "observation": {"mode": "onchip-selfrepair", "uses_algo": True},
            "defect_model": {**self.model.describe(), "fault_type": "stuck-at",
                             "polarity": "each defect stuck at 0 or 1 with equal probability"},
            "analyzer": {
                "on_chip": "onchip_2d_repair_analyzer (greedy)" if cols else "onchip_row_repair_analyzer",
                "spare_retry": self.spare_retry,
                "reference": "repair.bira.analyze_detailed with the spare faults",
                "repaired_means": "self_repair_fail is 0 and a post-repair functional scan reads back clean",
            },
            "causes": CAUSES,
            **({"diagnosis": {"entries": self.diagnosis_entries}} if self.diagnosis_entries else {}),
        }

    @property
    def mask_paths(self) -> tuple[Path, Path]:
        faults = self.workdir / "masks"
        return faults / "phys_sa0.hex", faults / "phys_sa1.hex"

    @property
    def module_dir(self) -> Path:
        """The generated wrapper and the injectable model (generated on first use)."""
        if self._module is None:
            self.workdir.mkdir(parents=True, exist_ok=True)
            config_path = self.workdir / "selfrepair_config.yml"
            config_path.write_text(yaml.safe_dump(self.config, sort_keys=False), encoding="utf-8")
            wrapper = generate_from_config(config_path, self.workdir / "build", algo=self.algo)
            module = wrapper.parent
            snapshot = yaml.safe_load((module / "config.yml").read_text(encoding="utf-8"))
            self._write_masks({})
            sa0, sa1 = (str(p.resolve()) for p in self.mask_paths)
            model = render_memory_model(snapshot, stuck_masks=(sa0, sa1))
            (module / f"{self.config['memory_name']}_inject_model.v").write_text(model, encoding="utf-8")
            self._module = module
        return self._module

    @property
    def model_path(self) -> Path:
        return self.module_dir / f"{self.config['memory_name']}_inject_model.v"

    def _write_masks(self, stuck: dict[tuple[int, int], int]) -> None:
        depth = 1 << self.geometry.mem_addr_width
        width = self.geometry.mem_data_width
        sa0, sa1 = [(1 << width) - 1] * depth, [0] * depth
        for (row, col), value in stuck.items():
            if value:
                sa1[row] |= 1 << col
            else:
                sa0[row] &= ~(1 << col)
        digits = (width + 3) // 4
        for path, words in zip(self.mask_paths, (sa0, sa1)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(f"{word:0{digits}X}\n" for word in words), encoding="ascii")

    def _split(self, stuck: dict[tuple[int, int], int]) -> tuple[set[tuple[int, int]], SpareFaults, int]:
        base: set[tuple[int, int]] = set()
        row_cells: dict[int, set[int]] = {}
        col_cells: dict[int, set[int]] = {}
        corners: set[tuple[int, int]] = set()
        for row, col in stuck:
            if row < self.depth and col < self.width:
                base.add((row, col))
            elif col < self.width:
                row_cells.setdefault(row - self.depth, set()).add(col)
            elif row < self.depth:
                col_cells.setdefault(col - self.width, set()).add(row)
            else:
                corners.add((row - self.depth, col - self.width))
        faults = SpareFaults(row_cells={i: frozenset(v) for i, v in row_cells.items()},
                             col_cells={k: frozenset(v) for k, v in col_cells.items()},
                             corners=frozenset(corners))
        return base, faults, len(stuck) - len(base)

    def _signature(self, outcome: dict[str, Any]) -> RepairSolution:
        geometry = self.geometry
        rows = _unpack(outcome.get("faulty_row_addr"), geometry.addr_width, geometry.num_spare_rows)
        cols = _unpack(outcome.get("faulty_bit"), geometry.bit_index_width, geometry.num_spare_cols)
        row_en, col_en = outcome.get("row_repair_en") or 0, outcome.get("col_repair_en") or 0
        return RepairSolution(
            row_map={rows[i]: i for i in range(geometry.num_spare_rows) if (row_en >> i) & 1},
            col_map={cols[k]: k for k in range(geometry.num_spare_cols) if (col_en >> k) & 1},
        )

    @staticmethod
    def _invalid_reason(report: dict[str, Any]) -> str | None:
        summary = (report.get("junit") or {}).get("summary") or {}
        if summary.get("failures") or summary.get("errors"):
            return f"the simulation had {summary.get('failures', 0)} failure(s) and {summary.get('errors', 0)} error(s)"
        outcome = report.get("self_repair")
        if outcome is None:
            return "the self-repair trial did not finish (no SELF_REPAIR_RESULT)"
        if outcome.get("self_repair_fail") is None:
            return "self_repair_fail read back as unknown (X/Z)"
        if "march_log" not in report:
            return "the analyze-pass log did not complete"
        if "fail_bitmap" not in report:
            return "the post-repair scan did not complete"
        if report.get("fail_scan_unknown_addrs"):
            return f"{len(report['fail_scan_unknown_addrs'])} word(s) read back as unknown after repair"
        return None

    def run_trial(self, k: int, seed: int) -> TrialRecord:
        rng = random.Random(seed)
        drawn = self.model.draw(rng, self.placement, k)
        stuck: dict[tuple[int, int], int] = {}
        for defect in drawn:
            value = rng.randrange(2)
            for cell in defect.cells:
                stuck[cell] = value
        return self.run_stuck(stuck, k=k, seed=seed, classes=class_counts(drawn))

    def run_stuck(
        self, stuck: dict[tuple[int, int], int], *, k: int = 0, seed: int = 0,
        classes: dict[str, int] | None = None,
    ) -> TrialRecord:
        """One trial with these stuck cells, in physical coordinates (spare rows
        at 2^A + i, spare columns at bit W + k), value 0 or 1 each."""
        module = self.module_dir  # generated first: generating writes blank masks
        self._write_masks(stuck)
        result = run_simulation(module, extra_make_vars={
            "COCOTB_TEST_MODULES": SELF_REPAIR_TEST_MODULE, "SRAM_SOURCE": str(self.model_path),
        })
        report = result.report
        base, faults, spare_cells = self._split(stuck)
        invalid = self._invalid_reason(report)
        if invalid is not None:
            return TrialRecord(k=k, seed=seed, status="invalid", invalid_reason=invalid,
                               extras={"truth_cells": len(base), "spare_cells": spare_cells})

        outcome = report["self_repair"]
        observed = observed_cells(report, "march")
        residual = observed_cells(report, "functional")
        exact = analyze_detailed(base, self.geometry, spare_faults=faults)
        observed_exact = analyze_detailed(observed, self.geometry)
        signature = self._signature(outcome)
        failed = outcome["self_repair_fail"] == 1
        repaired = not failed and not residual

        cause = None
        if repaired and not exact.repaired:
            cause = "unexplained"  # the chip beat the exact solver: the reference is wrong
        elif not repaired and exact.repaired:
            if not failed:
                cause = "false_pass"
            elif outcome.get("unrepairable"):
                cause = "analyzer" if observed_exact.repaired else (
                    "observation_escape" if observed != base else "unexplained")
            elif verify_solution(base, self.geometry, signature) == []:
                cause = "faulty_spare" if verify_solution(base, self.geometry, signature, faults) else "unexplained"
            else:
                cause = "observation_escape" if observed != base else "unexplained"

        extras: dict[str, Any] = {
            "truth_cells": len(base),
            "spare_cells": spare_cells,
            "fail_cell_count": len(observed),
            "overkill": len(observed - base),
            "truth_repairable": exact.repaired,
            "observed_repairable": observed_exact.repaired,
            "onchip_fail": int(failed),
            "onchip_unrepairable": outcome.get("unrepairable"),
            "residual_cells": len(residual),
            "cause": cause,
            "row_map": signature.row_map,
            "col_map": signature.col_map,
        }
        if not failed and residual:
            extras["escapes"] = len(residual)
        if outcome.get("diag_overflow") is not None:
            extras["diag_overflow"] = outcome["diag_overflow"]
        if self.diagnosis_entries and outcome.get("diag_valid") is not None:
            addrs = _unpack(outcome.get("diag_addr"), self.geometry.addr_width, self.diagnosis_entries)
            extras["diag_rows"] = sorted(addrs[n] for n in range(self.diagnosis_entries)
                                         if (outcome["diag_valid"] >> n) & 1)
        if classes is not None:
            extras["defects"] = classes
        used = len(signature.row_map) + len(signature.col_map) if repaired else None
        fewest = None
        if repaired and exact.repaired:
            fewest = analyze_detailed(base, self.geometry, objective="min_spares", spare_faults=faults).spares_used
        return TrialRecord(k=k, seed=seed, status="repaired" if repaired else "unrepairable",
                           cells=len(observed), spares_used=used, min_spares=fewest, extras=extras)
