"""Calibrate autoMBIST's defect models against published repair rates.

The protocol is fixed here, before any run, and the defect models' shape
defaults were committed before this script existed (yieldsim/defects.py):

ITC'2020 gate. Lee et al., "Fail Memory Configuration Set for RA Estimation",
ITC 2020, Table II, model MLS3050 (two 512 x 256 banks, 3 local spare rows and
3 local spare columns per bank, p_line 30%, p_cluster 50%), branch-and-bound
(exhaustive) repair rate at 8..12 faults per bank, 100,000 trials each. The
paper leaves two points open, so every reading in ITC2020_READINGS is run and
published. For each point z = (ours - theirs) / sqrt(theirs (1 - theirs) / N_ref
+ ours (1 - ours) / n); a reading's statistic is the sum of z^2 over the five
points. The reading with the smallest sum is chosen and labelled "inferred"
(the paper does not state it). The gate passes when every |z| <= 3 for the
chosen reading; no deviation is pre-declared as known. Nothing is tuned after
seeing the numbers: a failed gate is reported as failed.

Ohler comparison. Ohler, Hellebrand and Wunderlich, DDECS 2007, Table 2
(1024 x 1024, 5 spare rows and 5 spare columns, defect mix d2, 1..15 defects,
1000 trials, intelligentSolve). A comparison only, never a gate: the paper does
not give line lengths or cluster shapes ("a cluster fault affecting up to 33
cells"). Structural checks: rate >= 0.99 for k <= 3, <= 0.01 for k >= 12, and
the drop from k = 10 to 11 (one more defect than R + C) is the largest step.
Sensitivity: the default shapes and two declared variants (clusters with sides
up to 6, lines of 2..16 cells) are all run and published.

Run (WSL or the Nix shell, from the repository root):

    python scripts/calibrate_yield.py --out tests/data/yield_calibration.json

``--itc-trials``/``--ohler-trials`` lower the trial counts for a quick look; the
committed golden uses the defaults.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autombist import __version__  # noqa: E402
from autombist.yieldsim.bitmap_engine import BitmapEngine  # noqa: E402
from autombist.yieldsim.defects import (  # noqa: E402
    ITC2020_READINGS,
    MIX_PRESETS,
    ArrayGeometry,
    MixModel,
    defect_model,
)
from autombist.yieldsim.sweep import run_sweep  # noqa: E402

GOLDEN_SCHEMA_VERSION = "1.0"

ITC_TARGET = {
    "name": "MLS3050",
    "source": "Lee et al., ITC 2020, doi 10.1109/ITC44778.2020.9325273, Table II, B&B",
    "faults_per_bank": [8, 9, 10, 11, 12],
    "repair_rate": [0.9761, 0.8894, 0.7249, 0.4976, 0.2810],
    "trials": 100_000,
}
ITC_SETUP = {"rows": 512, "cols": 256, "banks": 2, "spare_rows": 3, "spare_cols": 3, "preset": "itc2020-3050"}
MAX_ABS_Z = 3.0

OHLER_TARGET = {
    "name": "d2",
    "source": "Ohler, Hellebrand, Wunderlich, DDECS 2007, Table 2 (intelligentSolve)",
    "defects": list(range(1, 16)),
    "repair_rate": [1.000, 1.000, 1.000, 0.993, 1.000, 0.970, 0.873, 0.835, 0.753, 0.753, 0.047,
                    0.000, 0.000, 0.000, 0.000],
    "trials": 1000,
}
OHLER_SETUP = {"rows": 1024, "cols": 1024, "banks": 1, "spare_rows": 5, "spare_cols": 5, "preset": "ohler-d2"}
OHLER_VARIANTS = {
    "default": {},
    "cluster-sides-1-6": {"cluster_side": (1, 6)},
    "line-length-2-16": {"line_length": (2, 16)},
}


def _z(ours: float, n: int, theirs: float, n_ref: int) -> float:
    variance = theirs * (1 - theirs) / n_ref + ours * (1 - ours) / n
    if variance == 0:
        return 0.0 if ours == theirs else math.inf
    return (ours - theirs) / math.sqrt(variance)


def _points(result) -> list[dict]:
    return [{"k": p.k, "valid": p.valid_trials, "repaired": p.repaired_count, "rate": p.repair_rate}
            for p in result.points]


def calibrate_itc(trials: int, seed: int) -> dict:
    geometry = ArrayGeometry(rows=ITC_SETUP["rows"], cols=ITC_SETUP["cols"], banks=ITC_SETUP["banks"])
    readings = {}
    for reading in sorted(ITC2020_READINGS):
        engine = BitmapEngine(geometry, spare_rows=ITC_SETUP["spare_rows"], spare_cols=ITC_SETUP["spare_cols"],
                              model=defect_model(ITC_SETUP["preset"], reading=reading))
        started = time.perf_counter()
        result = run_sweep(engine, ITC_TARGET["faults_per_bank"], trials, seed)
        points = _points(result)
        for point, theirs in zip(points, ITC_TARGET["repair_rate"]):
            point["reference"] = theirs
            point["z"] = _z(point["rate"], point["valid"], theirs, ITC_TARGET["trials"])
        readings[reading] = {
            **ITC2020_READINGS[reading],
            "points": points,
            "chi2": math.fsum(p["z"] ** 2 for p in points),
            "seconds": round(time.perf_counter() - started, 1),
        }
        print(f"itc2020 {reading}: chi2 {readings[reading]['chi2']:.1f}", file=sys.stderr)
    chosen = min(readings, key=lambda name: readings[name]["chi2"])
    failing = [p["k"] for p in readings[chosen]["points"] if abs(p["z"]) > MAX_ABS_Z]
    return {
        "target": ITC_TARGET,
        "setup": {**ITC_SETUP, "trials": trials, "base_seed": seed, "seed_scheme": "blake2b"},
        "rule": "smallest sum of z^2 over the five points; gate |z| <= 3 at every point",
        "readings": readings,
        "chosen": chosen,
        "chosen_label": "inferred",
        "gate": {"max_abs_z": MAX_ABS_Z, "passed": not failing, "failing_points": failing},
    }


def calibrate_ohler(trials: int, seed: int) -> dict:
    geometry = ArrayGeometry(rows=OHLER_SETUP["rows"], cols=OHLER_SETUP["cols"])
    variants = {}
    for name, shape in OHLER_VARIANTS.items():
        model = MixModel(weights=MIX_PRESETS[OHLER_SETUP["preset"]], name=OHLER_SETUP["preset"], **shape)
        engine = BitmapEngine(geometry, spare_rows=OHLER_SETUP["spare_rows"], spare_cols=OHLER_SETUP["spare_cols"],
                              model=model)
        started = time.perf_counter()
        result = run_sweep(engine, OHLER_TARGET["defects"], trials, seed)
        points = _points(result)
        for point, theirs in zip(points, OHLER_TARGET["repair_rate"]):
            point["reference"] = theirs
            point["z"] = _z(point["rate"], point["valid"], theirs, OHLER_TARGET["trials"])
        rates = {p["k"]: p["rate"] for p in points}
        steps = {k: rates[k - 1] - rates[k] for k in range(2, 16)}
        variants[name] = {
            "shape": {key: list(value) for key, value in shape.items()} or "defaults",
            "points": points,
            "checks": {
                "high_at_k_le_3": all(rates[k] >= 0.99 for k in (1, 2, 3)),
                "near_zero_at_k_ge_12": all(rates[k] <= 0.01 for k in range(12, 16)),
                "largest_drop_at_11": max(steps, key=steps.get) == 11,
            },
            "seconds": round(time.perf_counter() - started, 1),
        }
        print(f"ohler {name}: checks {variants[name]['checks']}", file=sys.stderr)
    return {
        "target": OHLER_TARGET,
        "setup": {**OHLER_SETUP, "trials": trials, "base_seed": seed, "seed_scheme": "blake2b"},
        "role": "comparison only, not a gate",
        "variants": variants,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="Where to write the golden JSON")
    parser.add_argument("--itc-trials", type=int, default=ITC_TARGET["trials"])
    parser.add_argument("--ohler-trials", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--commit", default=None, help="The git commit the run used, recorded in the golden")
    args = parser.parse_args(argv)

    golden = {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "tool_version": __version__,
        "commit": args.commit,
        "itc2020": calibrate_itc(args.itc_trials, args.seed),
        "ohler2007": calibrate_ohler(args.ohler_trials, args.seed),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(golden, indent=2) + "\n", encoding="utf-8")
    gate = golden["itc2020"]["gate"]
    print(f"ITC'2020 gate {'PASSED' if gate['passed'] else 'FAILED'} with reading "
          f"{golden['itc2020']['chosen']} (inferred); wrote {args.out}", file=sys.stderr)
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
