from __future__ import annotations

from pathlib import Path

import pytest

from autombist.word_oriented import dbs_sequence, write_dbs_file


def test_dbs_sequence_matches_paper_table_5_for_b4() -> None:
    """A.J. van de Goor & I.B.S. Tlili, "March tests for word-oriented
    memories," DATE 1998, Table 5 -- the CFdst DBS for a 4-bit word (the
    paper states this is identical to the CFid DBS, Section 4.3). Read
    directly from the primary source PDF (TU Delft's open-access mirror),
    transcribed here as a pinned regression, not re-derived from the
    construction it's checking."""
    assert dbs_sequence(4) == [0x0, 0xF, 0x0, 0x5, 0xA, 0x5, 0x3, 0xC, 0x3]


def test_dbs_sequence_matches_paper_table_4_for_b8() -> None:
    """Same paper, Table 4 -- the CFid DBS for an 8-bit word."""
    assert dbs_sequence(8) == [0x00, 0xFF, 0x00, 0x55, 0xAA, 0x55, 0x33, 0xCC, 0x33, 0x0F, 0xF0, 0x0F]


def test_dbs_sequence_length_matches_formula() -> None:
    """d = 3 + 3*ceil(log2(B)), the paper's own closed-form count."""
    import math

    for b in (2, 4, 8, 16, 32, 64):
        expected = 3 + 3 * math.ceil(math.log2(b))
        assert len(dbs_sequence(b)) == expected, b


def test_dbs_sequence_every_value_fits_the_word_width() -> None:
    for b in (2, 4, 8, 16, 32):
        for v in dbs_sequence(b):
            assert 0 <= v < (1 << b), (b, v)


def test_dbs_sequence_rejects_non_power_of_two() -> None:
    with pytest.raises(ValueError, match="power of two"):
        dbs_sequence(6)


def test_dbs_sequence_rejects_too_small() -> None:
    with pytest.raises(ValueError, match="power of two"):
        dbs_sequence(1)


def test_write_dbs_file_matches_dbs_sequence(tmp_path: Path) -> None:
    path = tmp_path / "dbs.txt"
    write_dbs_file(8, path)
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [int(ln, 16) for ln in lines] == dbs_sequence(8)
