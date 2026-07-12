"""Property-based cross-check of `repair.analyze`'s 2D allocation against a
brute-force oracle.

The hand-built named scenarios in `test_bira.py` document WHY the algorithm
needs a must-repair pass plus real backtracking (the cascading-budget case, the
full-block infeasibility, the independent-faults case). This file is the broad
correctness gate: for an NP-complete allocation problem, one hand-picked
"gotcha" instance is a weak substitute for exhaustively cross-checking many
small random instances against an obviously-correct (if exponential) oracle.
"""
from __future__ import annotations

import itertools
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.repair import RepairSolution, SpareGeometry, analyze  # noqa: E402


def _brute_force_repairable(
    fail_cells: set[tuple[int, int]], num_spare_rows: int, num_spare_cols: int
) -> bool:
    """Obviously-correct, exponential oracle: try every row-subset up to the
    row budget; for each, check whether the LEFTOVER faults' distinct column
    count fits the column budget. Deliberately naive -- fine at the small
    instance sizes used here, and independent of `analyze()`'s own logic."""
    distinct_rows = sorted({addr for addr, _bit in fail_cells})
    for size in range(min(len(distinct_rows), num_spare_rows) + 1):
        for row_subset in itertools.combinations(distinct_rows, size):
            row_set = set(row_subset)
            leftover_cols = {bit for addr, bit in fail_cells if addr not in row_set}
            if len(leftover_cols) <= num_spare_cols:
                return True
    return False


def test_brute_force_cross_check_small_random_instances() -> None:
    rng = random.Random(20260712)  # fixed seed -- reproducible across runs
    for _ in range(300):
        num_rows = rng.randint(2, 6)
        num_cols = rng.randint(2, 6)
        all_cells = [(r, c) for r in range(num_rows) for c in range(num_cols)]
        num_faults = rng.randint(0, len(all_cells))
        fail_cells = set(rng.sample(all_cells, k=num_faults))
        r_spares = rng.randint(0, 3)
        c_spares = rng.randint(0, 3)

        geo = SpareGeometry(
            base_words=num_rows,
            word_size=num_cols,
            num_spare_rows=r_spares,
            num_spare_cols=c_spares,
        )
        result = analyze(fail_cells, geo)
        expected_repairable = _brute_force_repairable(fail_cells, r_spares, c_spares)

        assert isinstance(result, RepairSolution) == expected_repairable, (
            f"mismatch for fail_cells={sorted(fail_cells)}, "
            f"num_spare_rows={r_spares}, num_spare_cols={c_spares}: "
            f"analyze()={'repairable' if isinstance(result, RepairSolution) else 'unrepairable'}, "
            f"brute force={'repairable' if expected_repairable else 'unrepairable'}"
        )
