"""Pin BIRA's row-only allocation (`repair.analyze`). Pure Python, hand-built
fail maps, NO simulator -- the extractable core, tested exactly as it would be
in its own repo."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.repair import (  # noqa: E402
    RepairSolution,
    SpareGeometry,
    Unrepairable,
    analyze,
)

GEO = SpareGeometry(base_words=16, word_size=8, num_spare_rows=2)


def test_empty_fail_map_is_trivially_repaired() -> None:
    result = analyze(set(), GEO)
    assert isinstance(result, RepairSolution)
    assert result.row_map == {}


def test_single_faulty_row_repaired() -> None:
    result = analyze({(3, 0)}, GEO)
    assert isinstance(result, RepairSolution)
    assert result.row_map == {3: 0}


def test_multiple_bits_same_row_collapse_to_one_faulty_row() -> None:
    # Three failing bits at addr 5 -> ONE faulty row (a spare replaces the word).
    result = analyze({(5, 0), (5, 3), (5, 7)}, GEO)
    assert isinstance(result, RepairSolution)
    assert result.row_map == {5: 0}


def test_two_faulty_rows_fit_two_spares_at_the_boundary() -> None:
    result = analyze({(3, 0), (10, 7)}, GEO)
    assert isinstance(result, RepairSolution)
    assert result.row_map == {3: 0, 10: 1}


def test_spare_assignment_is_deterministic_by_ascending_address() -> None:
    # Insertion order in the set must not matter: 3 -> spare 0, 10 -> spare 1.
    result = analyze({(10, 0), (3, 0)}, GEO)
    assert isinstance(result, RepairSolution)
    assert result.row_map == {3: 0, 10: 1}


def test_one_over_budget_is_unrepairable() -> None:
    result = analyze({(1, 0), (2, 0), (3, 0)}, GEO)  # 3 distinct rows, 2 spares
    assert isinstance(result, Unrepairable)
    assert result.faulty_rows == (1, 2, 3)
    assert result.num_spare_rows == 2


def test_zero_spares_any_fault_unrepairable_but_clean_is_fine() -> None:
    geo = SpareGeometry(base_words=16, word_size=8, num_spare_rows=0)
    assert isinstance(analyze({(0, 0)}, geo), Unrepairable)
    assert isinstance(analyze(set(), geo), RepairSolution)


def test_address_outside_geometry_raises() -> None:
    with pytest.raises(ValueError):
        analyze({(16, 0)}, GEO)  # 16 >= base_words (16)
    with pytest.raises(ValueError):
        analyze({(-1, 0)}, GEO)


# --------------------------------------------------------------------------- #
# 2D (row + column) allocation: must-repair fixed point + residual backtracking.
# GEO only ever exercises num_spare_cols=0 (row-only); these use a dedicated
# geometry with both budgets nonzero.
# --------------------------------------------------------------------------- #
GEO_2D = SpareGeometry(base_words=8, word_size=4, num_spare_rows=2, num_spare_cols=1)


def test_bit_outside_word_size_raises() -> None:
    with pytest.raises(ValueError):
        analyze({(0, 4)}, GEO_2D)  # 4 >= word_size (4)
    with pytest.raises(ValueError):
        analyze({(0, -1)}, GEO_2D)


def test_cascading_must_repair_forces_via_shrinking_budget() -> None:
    """Row 0 is forced by its own fault count (2 distinct cols > c_budget=1).
    Column 2 is NOT forced by the ORIGINAL budgets (its degree of 2 does not
    exceed the original r_budget=2) -- it only becomes forced once row 0's
    repair drops r_left to 1, at which point column 2's (unchanged) degree of 2
    exceeds the NEW r_left. This is the budget-shrinkage cascade, not a
    fault-count coincidence: column 2's own fault count never changes."""
    fail_cells = {(0, 0), (0, 1), (1, 2), (2, 2)}
    result = analyze(fail_cells, GEO_2D)
    assert isinstance(result, RepairSolution)
    assert result.row_map == {0: 0}
    assert result.col_map == {2: 0}


def test_full_block_conflict_is_unrepairable() -> None:
    """A complete 2x2 fault block: every row and every column is individually
    forced (each touches 2 of the other dimension, exceeding a budget of 1), but
    there is only 1 spare of each -- no 1-row+1-col combination covers all 4
    faults (each leaves exactly the opposite corner uncovered). Provably
    infeasible, not merely "the search gave up"."""
    fail_cells = {(0, 0), (0, 1), (1, 0), (1, 1)}
    geo = SpareGeometry(base_words=4, word_size=4, num_spare_rows=1, num_spare_cols=1)
    result = analyze(fail_cells, geo)
    assert isinstance(result, Unrepairable)
    assert result.faulty_rows == (0, 1)
    assert result.faulty_cols == (0, 1)
    assert result.num_spare_rows == 1
    assert result.num_spare_cols == 1


def test_residual_exhausted_when_must_repair_forces_nothing() -> None:
    """Three MUTUALLY independent faults (no two share a row or a column): every
    row/column has degree 1, which never exceeds a budget of >=1, so must-repair
    forces nothing at all -- this is unrepairable purely because the RESIDUAL
    backtracking search exhausts every row/column combination, not because
    must-repair detected an early conflict (that's the full-block test above).
    Each independent fault needs its own dedicated repair, so 3 of them need 3
    total repairs; only 2 are available (1 row + 1 col)."""
    fail_cells = {(0, 0), (1, 1), (2, 2)}
    geo = SpareGeometry(base_words=4, word_size=4, num_spare_rows=1, num_spare_cols=1)
    result = analyze(fail_cells, geo)
    assert isinstance(result, Unrepairable)
    assert result.faulty_rows == (0, 1, 2)
    assert result.faulty_cols == (0, 1, 2)


def test_independent_faults_use_one_row_and_one_column() -> None:
    """Two faults sharing neither a row nor a column: must-repair forces
    nothing (each row/column's degree of 1 fits the other's budget of 1), so
    this is pure residual backtracking. Repairable only by using exactly one
    row-repair AND one column-repair -- proving the search falls back to the
    other option for the second fault once the first choice exhausts a budget,
    rather than only ever trying one fixed preference."""
    fail_cells = {(0, 0), (1, 1)}
    geo = SpareGeometry(base_words=4, word_size=4, num_spare_rows=1, num_spare_cols=1)
    result = analyze(fail_cells, geo)
    assert isinstance(result, RepairSolution)
    assert len(result.row_map) == 1
    assert len(result.col_map) == 1
