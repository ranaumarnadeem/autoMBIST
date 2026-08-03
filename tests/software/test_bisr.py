"""Pin BISR's signature encoders (`repair.encode_row_repair`, `repair.encode_repair`).
Pure Python, no simulator -- verifies the packed bit layouts match
`rtl/repair_remap_row.sv`'s and `rtl/repair_remap_col.sv`'s exact slicing
(`faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH]`, `faulty_bit[k*BIT_IDX_WIDTH +:
BIT_IDX_WIDTH]`) by hand-computing the expected integers, that both refuse to
encode an Unrepairable verdict, and that the row-only encoder refuses a 2D
geometry rather than silently emitting half a repair.
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
    RepairSignature,
    RepairSolution,
    SpareGeometry,
    Unrepairable,
    analyze,
    encode_repair,
    encode_row_repair,
)


def test_single_spare_single_row() -> None:
    geo = SpareGeometry(base_words=4, word_size=4, num_spare_rows=1)  # addr_width=2
    solution = RepairSolution(row_map={3: 0})
    signature = encode_row_repair(solution, geo)
    assert signature == RepairSignature(row_repair_en=0b1, faulty_row_addr=0b11)


def test_two_spares_two_rows_pack_into_adjacent_slices() -> None:
    geo = SpareGeometry(base_words=16, word_size=8, num_spare_rows=2)  # addr_width=4
    solution = RepairSolution(row_map={3: 0, 10: 1})
    signature = encode_row_repair(solution, geo)
    # spare 0's slice (bits 3:0) = 3 = 0b0011; spare 1's slice (bits 7:4) = 10 = 0b1010
    # packed: 0b1010_0011 = 163.
    assert signature.row_repair_en == 0b11
    assert signature.faulty_row_addr == 163


def test_unused_low_spare_leaves_its_slice_zero() -> None:
    """Only spare index 1 is used (spare 0 sits idle) -- the encoder must not
    assume contiguous indices; it packs exactly whatever row_map says."""
    geo = SpareGeometry(base_words=4, word_size=4, num_spare_rows=2)  # addr_width=2
    solution = RepairSolution(row_map={2: 1})
    signature = encode_row_repair(solution, geo)
    assert signature.row_repair_en == 0b10       # bit 0 (spare 0) stays clear
    assert signature.faulty_row_addr == 2 << 2   # spare 1's slice only


def test_empty_row_map_is_the_all_zero_signature() -> None:
    geo = SpareGeometry(base_words=16, word_size=8, num_spare_rows=2)
    signature = encode_row_repair(RepairSolution(row_map={}), geo)
    assert signature == RepairSignature(row_repair_en=0, faulty_row_addr=0)


def test_unrepairable_input_raises_type_error() -> None:
    geo = SpareGeometry(base_words=16, word_size=8, num_spare_rows=2)
    unrepairable = Unrepairable(faulty_rows=(1, 2, 3), num_spare_rows=2)
    with pytest.raises(TypeError):
        encode_row_repair(unrepairable, geo)  # type: ignore[arg-type]


def test_composes_directly_with_analyze_output() -> None:
    """The whole Python-side loop, without any simulator: a real fail-cells set
    -> analyze() -> encode_row_repair(), and the packed bits are exactly what a
    hand trace predicts -- proving BIRA's output shape and BISR's input shape
    actually fit together, not just that each is independently well-formed."""
    geo = SpareGeometry(base_words=8, word_size=4, num_spare_rows=1)  # addr_width=3
    result = analyze({(5, 0), (5, 2)}, geo)  # one faulty row (5), two failing bits
    assert isinstance(result, RepairSolution)
    signature = encode_row_repair(result, geo)
    assert signature.row_repair_en == 0b1
    assert signature.faulty_row_addr == 5


# --------------------------------------------------------------------------- #
# encode_repair -- the 2D encoder (Workstream M.1)
# --------------------------------------------------------------------------- #
def test_encode_repair_row_half_matches_encode_row_repair() -> None:
    """The row half must be bit-identical to the row-only encoder's output --
    encode_repair is a superset, not a reimplementation."""
    geo = SpareGeometry(base_words=16, word_size=8, num_spare_rows=2)
    solution = RepairSolution(row_map={3: 0, 10: 1})
    both = encode_repair(solution, geo)
    row_only = encode_row_repair(solution, geo)
    assert both.row_repair_en == row_only.row_repair_en
    assert both.faulty_row_addr == row_only.faulty_row_addr
    assert (both.col_repair_en, both.faulty_bit) == (0, 0)


def test_encode_repair_single_spare_column() -> None:
    # word_size=4 -> bit_index_width=2. Faulty bit 3 -> spare column 0.
    geo = SpareGeometry(base_words=4, word_size=4, num_spare_rows=1, num_spare_cols=1)
    signature = encode_repair(RepairSolution(row_map={}, col_map={3: 0}), geo)
    assert signature == RepairSignature(
        row_repair_en=0, faulty_row_addr=0, col_repair_en=0b1, faulty_bit=0b11
    )


def test_encode_repair_two_spare_columns_pack_into_adjacent_slices() -> None:
    # word_size=8 -> bit_index_width=3. spare 0 <- bit 2 (0b010), spare 1 <- bit 5 (0b101).
    # packed faulty_bit: 0b101_010 = 42.
    geo = SpareGeometry(base_words=16, word_size=8, num_spare_rows=1, num_spare_cols=2)
    signature = encode_repair(RepairSolution(row_map={}, col_map={2: 0, 5: 1}), geo)
    assert signature.col_repair_en == 0b11
    assert signature.faulty_bit == 42


def test_encode_repair_unused_low_spare_column_leaves_its_slice_zero() -> None:
    """Mirrors the row-side test: the encoder must not assume contiguous spare
    indices; it packs exactly whatever col_map says."""
    geo = SpareGeometry(base_words=4, word_size=4, num_spare_rows=1, num_spare_cols=2)
    signature = encode_repair(RepairSolution(row_map={}, col_map={1: 1}), geo)
    assert signature.col_repair_en == 0b10       # bit 0 (spare col 0) stays clear
    assert signature.faulty_bit == 1 << 2        # spare col 1's slice only


def test_encode_repair_packs_row_and_column_halves_together() -> None:
    """A genuine 2D repair: one row spare AND one column spare in one signature."""
    geo = SpareGeometry(base_words=8, word_size=4, num_spare_rows=1, num_spare_cols=1)
    solution = RepairSolution(row_map={6: 0}, col_map={2: 0})
    signature = encode_repair(solution, geo)
    assert signature.row_repair_en == 0b1
    assert signature.faulty_row_addr == 6
    assert signature.col_repair_en == 0b1
    assert signature.faulty_bit == 0b10


def test_encode_repair_unrepairable_input_raises_type_error() -> None:
    geo = SpareGeometry(base_words=16, word_size=8, num_spare_rows=2, num_spare_cols=1)
    unrepairable = Unrepairable(faulty_rows=(1, 2, 3), num_spare_rows=2)
    with pytest.raises(TypeError):
        encode_repair(unrepairable, geo)  # type: ignore[arg-type]


def test_encode_repair_composes_with_analyze_on_a_2d_case() -> None:
    """The decisive 2D shape, end to end in Python: two faults on the SAME bit in
    DIFFERENT rows. One spare row cannot cover both, so BIRA must allocate the
    spare COLUMN -- and the encoder must surface that in col_repair_en/faulty_bit."""
    geo = SpareGeometry(base_words=8, word_size=4, num_spare_rows=1, num_spare_cols=1)
    result = analyze({(1, 3), (2, 3)}, geo)
    assert isinstance(result, RepairSolution)
    assert result.row_map == {}
    assert result.col_map == {3: 0}
    signature = encode_repair(result, geo)
    assert signature.row_repair_en == 0
    assert signature.col_repair_en == 0b1
    assert signature.faulty_bit == 3


# --------------------------------------------------------------------------- #
# encode_row_repair's teeth: it must refuse a 2D geometry/solution outright
# rather than silently emitting only the row half of a real repair.
# --------------------------------------------------------------------------- #
def test_encode_row_repair_rejects_geometry_with_spare_columns() -> None:
    geo = SpareGeometry(base_words=8, word_size=4, num_spare_rows=1, num_spare_cols=1)
    with pytest.raises(ValueError, match="ROW-ONLY encoder"):
        encode_row_repair(RepairSolution(row_map={2: 0}), geo)


def test_encode_row_repair_rejects_solution_carrying_column_repairs() -> None:
    """Even if the geometry somehow claims no spare columns, a solution that
    allocated one must not be silently half-encoded."""
    geo = SpareGeometry(base_words=8, word_size=4, num_spare_rows=1)
    with pytest.raises(ValueError, match="ROW-ONLY encoder"):
        encode_row_repair(RepairSolution(row_map={}, col_map={1: 0}), geo)


def test_encode_row_repair_still_accepts_a_row_only_geometry() -> None:
    """Negative control for the two rejections above -- the row-only path is
    completely unchanged."""
    geo = SpareGeometry(base_words=8, word_size=4, num_spare_rows=1)
    signature = encode_row_repair(RepairSolution(row_map={5: 0}), geo)
    assert signature == RepairSignature(row_repair_en=0b1, faulty_row_addr=5)
