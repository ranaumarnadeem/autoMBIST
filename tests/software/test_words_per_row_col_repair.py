"""Column repair on a column-muxed memory (redundancy.words_per_row > 1).

OpenRAM routes spare columns around the column mux, so a spare column has one
cell per physical row and replaces ONE physical column: bit b of the words
whose low address bits are w, physical column b * words_per_row + w. These
pure tests pin that model in the geometry, BIRA, verify_solution and the
encoded signature; tests/integration/test_repair_muxed_col_e2e.py runs the RTL.
"""
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
    encode_repair,
)
from autombist.repair.bira import analyze_detailed, verify_solution  # noqa: E402
from autombist.repair.types import SpareFaults  # noqa: E402


def _geo(**kw) -> SpareGeometry:
    # 8 words of 4 bits, 4 words per row: 2 physical rows of 16 physical columns.
    base = {"base_words": 8, "word_size": 4, "num_spare_rows": 1, "num_spare_cols": 1, "words_per_row": 4}
    return SpareGeometry(**{**base, **kw})


# --- geometry -----------------------------------------------------------------

def test_col_of_interleaves_the_words_bits() -> None:
    geo = _geo()
    # Bit b of word column w is physical column b*4 + w; the row does not matter.
    assert [geo.col_of(a, 0) for a in range(8)] == [0, 1, 2, 3, 0, 1, 2, 3]
    assert geo.col_of(2, 1) == 6 and geo.col_of(6, 1) == 6
    assert geo.col_of(3, 3) == 15
    assert geo.num_cols == 16


def test_col_of_is_the_bit_without_muxing() -> None:
    geo = _geo(words_per_row=1)
    assert [geo.col_of(5, b) for b in range(4)] == [0, 1, 2, 3]
    assert geo.num_cols == 4


@pytest.mark.parametrize(("word_size", "wpr", "width"), [
    (4, 1, 2),     # unchanged without muxing
    (4, 4, 4),     # 16 physical columns
    (32, 4, 7),    # sky130_sram_32b256w / 32b512w: 128 columns
    (8, 16, 7),    # sky130_sram_8b1024w: 128 columns
    (32, 2, 6),    # sky130_sram_1r1w_32b256w: 64 columns
])
def test_bit_index_width_counts_physical_columns(word_size: int, wpr: int, width: int) -> None:
    geo = SpareGeometry(base_words=64, word_size=word_size, num_spare_rows=1, num_spare_cols=1,
                        words_per_row=wpr)
    assert geo.bit_index_width == width


def test_mem_data_width_still_adds_one_lane_per_spare() -> None:
    assert _geo(num_spare_cols=2).mem_data_width == 6


# --- BIRA ---------------------------------------------------------------------

# Bit 1 of word column 2, in both physical rows: one physical column (6).
BITLINE = {(2, 1), (6, 1)}


def test_a_bitline_defect_takes_one_spare_column() -> None:
    assert analyze(BITLINE, _geo(num_spare_rows=0)) == RepairSolution(row_map={}, col_map={6: 0})


def test_the_same_bit_of_another_word_is_another_column() -> None:
    # Bit 1 at word columns 2 and 3: physical columns 6 and 7, so one spare
    # column cannot cover both -- it could at one word per row (bit 1 of 2 rows).
    cells = {(2, 1), (7, 1)}
    assert isinstance(analyze(cells, _geo(num_spare_rows=0)), Unrepairable)
    assert analyze(cells, _geo(num_spare_rows=0, words_per_row=1)) == RepairSolution(row_map={}, col_map={1: 0})
    assert analyze(cells, _geo(num_spare_rows=0, num_spare_cols=2)) == RepairSolution(row_map={}, col_map={6: 0, 7: 1})


def test_unrepairable_names_physical_columns() -> None:
    verdict = analyze({(2, 1), (7, 1)}, _geo(num_spare_rows=0))
    assert isinstance(verdict, Unrepairable)
    assert verdict.faulty_cols == (6, 7)
    assert verdict.faulty_rows == (0, 4)


def test_a_row_and_a_column_together() -> None:
    # A wordline defect in row 0 plus a bitline defect at physical column 6.
    cells = {(a, 3) for a in range(4)} | BITLINE
    solution = analyze(cells, _geo())
    assert solution == RepairSolution(row_map={0: 0}, col_map={6: 0})
    assert verify_solution(cells, _geo(), solution) == []


def test_analyze_detailed_counts_physical_columns() -> None:
    result = analyze_detailed(BITLINE | {(3, 0)}, _geo(num_spare_cols=2), objective="min_spares")
    # (3, 0) is physical column 3 in row 0: covered by the spare row or a second column.
    assert result.repaired and result.spares_used == 2


# --- verify_solution ------------------------------------------------------------

def test_verify_solution_wants_the_physical_column() -> None:
    assert verify_solution(BITLINE, _geo(num_spare_rows=0), RepairSolution(row_map={}, col_map={6: 0})) == []
    # Naming the bare bit (1) covers physical column 1, not 6.
    problems = verify_solution(BITLINE, _geo(num_spare_rows=0), RepairSolution(row_map={}, col_map={1: 0}))
    assert any("not covered" in p for p in problems)


def test_verify_solution_bounds_columns_by_the_physical_array() -> None:
    problems = verify_solution(set(), _geo(), RepairSolution(row_map={}, col_map={16: 0}))
    assert "repaired column 16 is outside the logical array" in problems
    assert verify_solution(set(), _geo(), RepairSolution(row_map={}, col_map={15: 0})) == []


def test_a_faulty_spare_column_cell_needs_its_physical_row_repaired() -> None:
    # Spare column 0 is broken at physical row 1 (any address 4..7 names it).
    faults = SpareFaults(col_cells={0: frozenset({5})})
    solution = analyze_detailed(BITLINE, _geo(), spare_faults=faults).result
    assert isinstance(solution, RepairSolution)
    assert solution.col_map == {6: 0} and set(solution.row_map) == {4}
    assert verify_solution(BITLINE, _geo(), solution, faults) == []
    assert any("not row-repaired" in p for p in
               verify_solution(BITLINE, _geo(), RepairSolution(row_map={0: 0}, col_map={6: 0}), faults))


def test_a_faulty_spare_row_cell_names_a_physical_column() -> None:
    # Spare row 0 broken at physical column 9 (bit 2 of word column 1).
    faults = SpareFaults(row_cells={0: frozenset({9})})
    cells = {(a, 3) for a in range(4)}  # wordline defect, row 0
    solution = analyze_detailed(cells, _geo(), spare_faults=faults).result
    assert solution == RepairSolution(row_map={0: 0}, col_map={9: 0})
    assert verify_solution(cells, _geo(), solution, faults) == []


def test_spare_row_faults_are_bounded_by_the_physical_columns() -> None:
    with pytest.raises(ValueError, match="outside the geometry"):
        analyze_detailed(set(), _geo(), spare_faults=SpareFaults(row_cells={0: frozenset({16})}))


# --- signature ----------------------------------------------------------------

def test_signature_packs_the_physical_column() -> None:
    geo = _geo(num_spare_cols=2)
    signature = encode_repair(RepairSolution(row_map={4: 0}, col_map={6: 0, 15: 1}), geo)
    assert signature.col_repair_en == 0b11
    assert signature.faulty_bit == 6 | (15 << 4)  # 4-bit slices: 16 physical columns
    assert (signature.row_repair_en, signature.faulty_row_addr) == (1, 4)
