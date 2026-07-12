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
