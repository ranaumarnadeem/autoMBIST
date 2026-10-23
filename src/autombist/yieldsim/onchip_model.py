"""A model of the on-chip 2D repair analyzer (rtl/onchip_2d_repair_analyzer.sv),
decision for decision, so its repair rate can be measured without RTL.

The on-chip analyzer sees the march's failing reads one at a time, as
(word address, failing-bit mask) events, and decides each immediately: it never
buffers the fail map. :func:`march_c_events` gives the events march-c produces
for a set of stuck cells; :func:`greedy` and :func:`deferred` replay the two
analyzer modes over them; :func:`onchip_solution` says whether the result
covers every cell (the chip's own verify pass decides the same).

Two modes:

* ``greedy`` (the default RTL): per failing bit of a word whose row is not
  spare-mapped, a bit already column-mapped is covered; a bit seen failing
  before takes a free spare column; otherwise (a first sighting, or no column
  left) the word wants a spare row. A word that wants a row takes one if one is
  free; if none is, nothing happens yet, and a later read of the same word may
  still take columns for its bits (the analyzer is lenient: the verify pass
  decides whether the repair holds).
* ``deferred`` (``redundancy.onchip_analyzer: deferred``), two analyze passes.
  Pass one defers: a failing bit takes a spare column only when it fails at a
  DIFFERENT row from the one it first failed at, a word with two or more
  failing bits left uncovered takes a row, and a lone first sighting is only
  remembered (bit -> row). Pass two runs the greedy rules on what pass one left
  uncovered, starting from its claims, with pass one's sightings counting as
  seen.

The model assumes fault-free spares (the verify pass and spare retirement deal
with faulty ones) and march-c's read order; other algorithms read in other
orders.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

__all__ = ["MODES", "march_c_events", "greedy", "deferred", "onchip_solution"]

Cell = tuple[int, int]
Event = tuple[int, int]  # (word address, failing-bit mask)

MODES = ("greedy", "deferred")


def _bits(mask: int) -> Iterable[int]:
    bit = 0
    while mask:
        if mask & 1:
            yield bit
        mask >>= 1
        bit += 1


def march_c_events(stuck: Mapping[Cell, int], rows: int) -> list[Event]:
    """The failing reads of march-c over ``rows`` words with these stuck cells
    (value 0 or 1): up r0 (stuck-at-1 bits fail), up r1 (stuck-at-0 bits), down
    r0, down r1, then up r0 again."""
    sa1: dict[int, int] = {}
    sa0: dict[int, int] = {}
    for (row, bit), value in stuck.items():
        table = sa1 if value else sa0
        table[row] = table.get(row, 0) | (1 << bit)
    up, down = range(rows), range(rows - 1, -1, -1)
    events: list[Event] = []
    for order, table in ((up, sa1), (up, sa0), (down, sa1), (down, sa0), (up, sa1)):
        events += [(addr, table[addr]) for addr in order if addr in table]
    return events


def greedy(
    events: Sequence[Event], spare_rows: int, spare_cols: int, *,
    rows: Sequence[int] = (), cols: Sequence[int] = (), seen: Iterable[int] = (),
) -> tuple[list[int], list[int]]:
    """The greedy (lenient) rules over ``events``, starting from claimed
    ``rows``/``cols`` and bits already ``seen``. Returns the claimed rows and
    columns, in claim order."""
    rows, cols, seen = list(rows), list(cols), set(seen)
    for addr, mask in events:
        if addr in rows:
            continue
        wants_row = False
        for bit in _bits(mask):
            if bit in cols:
                continue
            if bit in seen and len(cols) < spare_cols:
                cols.append(bit)
                continue
            wants_row = True
            seen.add(bit)
        if wants_row and len(rows) < spare_rows:
            rows.append(addr)
    return rows, cols


def deferred(events: Sequence[Event], spare_rows: int, spare_cols: int) -> tuple[list[int], list[int]]:
    """The two-pass deferred mode over ``events`` (both passes see the same
    march; pass two skips what pass one claimed, as the remap does)."""
    rows: list[int] = []
    cols: list[int] = []
    first_row: dict[int, int] = {}  # bit -> row it first failed at
    for addr, mask in events:
        if addr in rows:
            continue
        open_bits = [bit for bit in _bits(mask) if bit not in cols]
        for bit in open_bits:
            if bit in first_row and first_row[bit] != addr and len(cols) < spare_cols:
                cols.append(bit)
        left = [bit for bit in open_bits if bit not in cols]
        if len(left) >= 2 and len(rows) < spare_rows:
            rows.append(addr)
            continue
        for bit in left:
            first_row.setdefault(bit, addr)
    return greedy(events, spare_rows, spare_cols, rows=rows, cols=cols, seen=first_row)


def onchip_solution(
    stuck: Mapping[Cell, int], rows: int, spare_rows: int, spare_cols: int, mode: str = "greedy",
) -> tuple[list[int], list[int]] | None:
    """The on-chip analyzer's repair for these stuck cells under march-c, or
    None when it leaves a cell uncovered (the chip's verify pass fails)."""
    if mode not in MODES:
        raise ValueError(f"unknown analyzer mode {mode!r}: use one of {MODES}")
    events = march_c_events(stuck, rows)
    claimed_rows, claimed_cols = (greedy if mode == "greedy" else deferred)(events, spare_rows, spare_cols)
    covered = all(row in claimed_rows or bit in claimed_cols for row, bit in stuck)
    return (claimed_rows, claimed_cols) if covered else None
