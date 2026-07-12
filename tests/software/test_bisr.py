"""Pin BISR's signature encoder (`repair.encode_row_repair`). Pure Python, no
simulator -- verifies the packed bit layout matches `rtl/repair_remap_row.sv`'s
exact slicing (`faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH]`) by hand-computing
the expected integers, and that it refuses to encode an Unrepairable verdict.
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
