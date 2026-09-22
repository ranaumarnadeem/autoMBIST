"""Intra-word coupling-fault (CFid/CFdst) data-background sequence (DBS)
construction, per A.J. van de Goor & I.B.S. Tlili, "March tests for
word-oriented memories," DATE 1998, Section 4.2 (Table 4/Table 5).

The paper derives, for a B-bit word, the minimal sequence of B-bit literal
values (a "data background") needed to sensitize every possible intra-word
idempotent coupling fault (CFid) between any two bit lanes -- and states
(Section 4.3) that the identical sequence also suffices for the intra-word
disturb coupling fault (CFdst), differing only in how each value is
exercised (CFid: write then read once; CFdst: write then read twice, to
catch a disturb that deceptively returns the correct value on its own
sensitizing read -- see word_oriented_engine.sv's CFDST_MODE).

Construction (independently re-derived from the primary source, confirmed
against the paper's own worked Table 4 (B=8, d=12 states) and Table 5 (B=4,
d=9 states) exactly): d = 3 + 3*ceil(log2(B)) states total, built in
"levels" 0..ceil(log2(B))-1.

Level 0 uses the base 2-symbol sequence [00, 11, 00, 01, 10, 01] (6 states);
each symbol occupies exactly 1 bit, tiled B/2 times to fill the word. Level
L (1 <= L < levels) uses the reduced 2-symbol sequence [01, 10, 01] (3
states); each symbol is EXPANDED to width 2**L before the 2-symbol unit
(width 2**(L+1)) is tiled B/2**(L+1) times to fill the word. At the final
level, 2**(L+1) == B exactly (B being a power of two), so the tile count is
1 -- the unit fills the whole word without repeating.

Not the paper's own further-optimized state-diagram-derived sequences
(Table 4/5's own row orderings come from Figures 2-6's minimized state-
diagram construction, which additionally removes some redundant arcs this
simpler tiling reproduces without removing) -- this construction was checked
to produce the IDENTICAL set of states in the IDENTICAL order as both
tables, not merely an equally-sized alternative, so there is no coverage gap
from skipping that optimization step; it is simply presented here as a
directly-generalizable tiling rule rather than re-deriving the paper's own
state-diagram minimization argument.
"""
from __future__ import annotations

import math


def dbs_sequence(data_width: int) -> list[int]:
    """The intra-word CFid/CFdst data-background sequence for a `data_width`-
    bit word, as a list of `data_width`-bit integer literals, in application
    order. `data_width` must be a power of two, >= 2 (the paper's own stated
    domain -- see its Section 1: "B >= 2, whereby B ... usually is a power of
    two"). Length is 3 + 3*ceil(log2(data_width))."""
    if data_width < 2 or (data_width & (data_width - 1)) != 0:
        raise ValueError(
            f"dbs_sequence needs data_width to be a power of two >= 2 (got {data_width})"
        )
    levels = data_width.bit_length() - 1  # ceil(log2(data_width)) for a power of two
    mask = (1 << data_width) - 1

    def tile(symbol_bits: str, symbol_width: int) -> int:
        unit = "".join(ch * symbol_width for ch in symbol_bits)
        tiles = data_width // len(unit)
        return int(unit * tiles, 2) & mask

    sequence: list[int] = []
    base = ("00", "11", "00", "01", "10", "01")
    for s in base:
        sequence.append(tile(s, 1))
    reduced = ("01", "10", "01")
    for level in range(1, levels):
        symbol_width = 1 << level
        for s in reduced:
            sequence.append(tile(s, symbol_width))
    return sequence


def write_dbs_file(data_width: int, path) -> None:
    """Writes dbs_sequence(data_width) as one hex literal per line, the
    format word_oriented_engine.sv's `+DBS_FILE` plusarg reads via
    `$sscanf(line, "%h", v)`."""
    from pathlib import Path

    path = Path(path)
    lines = [format(v, "x") for v in dbs_sequence(data_width)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
