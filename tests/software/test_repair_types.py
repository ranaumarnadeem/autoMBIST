"""Pin SpareGeometry's derived address width and validation -- the shared
geometry contract both the wrapper generator and BIRA depend on. Pure Python."""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.repair import SpareGeometry, SpareGeometryError  # noqa: E402


def test_no_spares_is_plain_log2() -> None:
    g = SpareGeometry(base_words=1024, word_size=32)
    assert g.total_words == 1024
    assert g.mem_addr_width == 10


def test_one_spare_row_inflates_address_width() -> None:
    # 16 words + 1 spare = 17 -> needs 5 bits (OpenRAM: the spare row = address 16).
    g = SpareGeometry(base_words=16, word_size=8, num_spare_rows=1)
    assert g.total_words == 17
    assert g.mem_addr_width == 5


def test_spares_within_power_of_two_do_not_inflate() -> None:
    # 12 + 2 = 14 -> still fits 4 bits.
    g = SpareGeometry(base_words=12, word_size=8, num_spare_rows=2)
    assert g.mem_addr_width == 4


@pytest.mark.parametrize(
    "base_words,num_spare_rows,expected",
    [
        (1, 0, 1),     # max(1, ...) floor
        (2, 0, 1),
        (4, 0, 2),
        (4, 1, 3),     # 5 words -> 3 bits
        (256, 0, 8),
        (256, 1, 9),   # 257 -> 9 bits
    ],
)
def test_mem_addr_width_table(base_words: int, num_spare_rows: int, expected: int) -> None:
    g = SpareGeometry(base_words=base_words, word_size=4, num_spare_rows=num_spare_rows)
    assert g.mem_addr_width == expected


def test_rejects_nonpositive_dimensions() -> None:
    with pytest.raises(SpareGeometryError):
        SpareGeometry(base_words=0, word_size=8)
    with pytest.raises(SpareGeometryError):
        SpareGeometry(base_words=16, word_size=0)


def test_rejects_negative_spares() -> None:
    with pytest.raises(SpareGeometryError):
        SpareGeometry(base_words=16, word_size=8, num_spare_rows=-1)
    with pytest.raises(SpareGeometryError):
        SpareGeometry(base_words=16, word_size=8, num_spare_cols=-1)


def test_is_frozen() -> None:
    g = SpareGeometry(base_words=16, word_size=8)
    with pytest.raises(dataclasses.FrozenInstanceError):
        g.base_words = 32  # type: ignore[misc]
