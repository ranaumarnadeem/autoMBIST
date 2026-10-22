"""Fail-bitmap shape classification, after Lin et al. (IEEE TC 2016).

A fail bitmap is split into patterns, each one defect's likely footprint, and
each pattern gets a class. Lines come first: a row (column) whose fail count
reaches ``line_density`` of the bank width (height) is a line, and a row line
and a column line that cross form one cross. The cells left are grouped by
single linkage at Chebyshev distance ``distance`` (8-neighbour connectivity
for the default 1), and each group is classified as a whole:

    single          one cell
    twin_row/col    two cells adjacent along a row / a column
    diag_twin       two diagonally adjacent cells
    row/col_segment three or more contiguous cells along a row / a column
    cluster         a group whose bounding box is at least ``cluster_density`` full
    other           anything else (Lin's run-length naming of unknown shapes is
                    not implemented)

This classifies spatial shape. It does not replace
``autombist.algo_reporting.compute_syndrome_groups``, which groups failures
by fault type from the march syndrome. Rows and columns are logical (one word
per row), the base array only; physical scrambling is not modelled.

Each pattern also carries overlays that matter for repair: its spare cost (the
fewest lines covering it, by Konig's theorem the maximum matching), whether a
row or column in it must be repaired by a spare row or column given the spare
budget, and whether it is an orthogonal single (sharing neither its row nor its
column with any other fail).

:func:`expected_label` gives the class a generator defect should receive from
its own shape, without the classifier, so :func:`confusion` measures the
classifier rather than a mismatch between two vocabularies.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .defects import Defect

__all__ = [
    "CLASSES",
    "Pattern",
    "classify",
    "expected_label",
    "confusion",
    "min_line_cover",
]

Cell = tuple[int, int]

CLASSES = (
    "single", "twin_row", "twin_col", "diag_twin", "row_segment", "col_segment",
    "cluster", "row_line", "col_line", "cross", "other",
)


@dataclass(frozen=True, slots=True)
class Pattern:
    label: str
    cells: frozenset[Cell]
    spare_cost: int
    must_row: bool = False
    must_col: bool = False
    orthogonal: bool = False


def min_line_cover(cells: Iterable[Cell]) -> int:
    """The fewest rows and columns covering ``cells``: the maximum matching of
    their row/column bipartite graph (Konig's theorem)."""
    adjacency: dict[int, list[int]] = {}
    for r, c in sorted(set(cells)):
        adjacency.setdefault(r, []).append(c)
    match: dict[int, int] = {}

    def augment(row: int, seen: set[int]) -> bool:
        for col in adjacency[row]:
            if col not in seen:
                seen.add(col)
                if col not in match or augment(match[col], seen):
                    match[col] = row
                    return True
        return False

    return sum(1 for row in sorted(adjacency) if augment(row, set()))


def _line_thresholds(rows: int, cols: int, line_density: float) -> tuple[int, int]:
    """Fail counts that make a row (column) a line; never fewer than 3 cells."""
    return max(3, math.ceil(line_density * cols)), max(3, math.ceil(line_density * rows))


def _shape_label(cells: frozenset[Cell], cluster_density: float) -> str:
    n = len(cells)
    rows = {r for r, _ in cells}
    cols = {c for _, c in cells}
    if n == 1:
        return "single"
    height, width = max(rows) - min(rows) + 1, max(cols) - min(cols) + 1
    if len(rows) == 1 and width == n:
        return "twin_row" if n == 2 else "row_segment"
    if len(cols) == 1 and height == n:
        return "twin_col" if n == 2 else "col_segment"
    if n == 2 and height == 2 and width == 2:
        return "diag_twin"
    if height >= 2 and width >= 2 and n / (height * width) >= cluster_density:
        return "cluster"
    return "other"


def _groups(cells: set[Cell], distance: int) -> list[frozenset[Cell]]:
    left = set(cells)
    groups = []
    offsets = [(dr, dc) for dr in range(-distance, distance + 1) for dc in range(-distance, distance + 1)
               if (dr, dc) != (0, 0)]
    while left:
        start = min(left)
        left.discard(start)
        group, frontier = {start}, [start]
        while frontier:
            r, c = frontier.pop()
            for dr, dc in offsets:
                cell = (r + dr, c + dc)
                if cell in left:
                    left.discard(cell)
                    group.add(cell)
                    frontier.append(cell)
        groups.append(frozenset(group))
    return groups


def classify(
    cells: Iterable[Cell],
    *,
    rows: int,
    cols: int,
    line_density: float = 0.5,
    cluster_density: float = 0.75,
    distance: int = 1,
    spare_rows: int | None = None,
    spare_cols: int | None = None,
) -> list[Pattern]:
    """The patterns of one bank's fail bitmap (``rows`` x ``cols`` base cells),
    largest first. ``spare_rows``/``spare_cols`` set the must-repair overlay:
    a row with more fails than there are spare columns must take a spare row."""
    if not 0 < line_density <= 1 or not 0 < cluster_density <= 1:
        raise ValueError("line_density and cluster_density must be in (0, 1]")
    if distance < 1:
        raise ValueError(f"distance must be at least 1, got {distance}")
    cells = set(cells)
    row_count = Counter(r for r, _ in cells)
    col_count = Counter(c for _, c in cells)
    row_min, col_min = _line_thresholds(rows, cols, line_density)
    line_rows = {r for r, n in row_count.items() if n >= row_min}
    line_cols = {c for c, n in col_count.items() if n >= col_min}

    found: list[tuple[str, frozenset[Cell]]] = []
    # Crossing row and column lines join into one cross (a component of the
    # row-column incidence among line rows and line columns that cross at a fail).
    crossing = {(r, c) for r in line_rows for c in line_cols if (r, c) in cells}
    pending_rows, pending_cols = set(line_rows), set(line_cols)
    while crossing:
        r0, c0 = min(crossing)
        group_rows, group_cols = {r0}, {c0}
        grew = True
        while grew:
            grew = False
            for r, c in crossing:
                if (r in group_rows) != (c in group_cols):
                    group_rows.add(r)
                    group_cols.add(c)
                    grew = True
        crossing = {(r, c) for r, c in crossing if r not in group_rows}
        part = frozenset(x for x in cells if x[0] in group_rows or x[1] in group_cols)
        found.append(("cross", part))
        pending_rows -= group_rows
        pending_cols -= group_cols
    for r in sorted(pending_rows):
        found.append(("row_line", frozenset(x for x in cells if x[0] == r)))
    for c in sorted(pending_cols):
        found.append(("col_line", frozenset(x for x in cells if x[1] == c)))

    taken = set().union(*(part for _, part in found)) if found else set()
    for group in _groups(cells - taken, distance):
        found.append((_shape_label(group, cluster_density), group))

    patterns = []
    for label, part in found:
        must_row = spare_cols is not None and any(row_count[r] > spare_cols for r in {r for r, _ in part})
        must_col = spare_rows is not None and any(col_count[c] > spare_rows for c in {c for _, c in part})
        orthogonal = label == "single" and all(row_count[r] == 1 and col_count[c] == 1 for r, c in part)
        patterns.append(Pattern(label=label, cells=part, spare_cost=min_line_cover(part),
                                must_row=must_row, must_col=must_col, orthogonal=orthogonal))
    patterns.sort(key=lambda p: (-len(p.cells), min(p.cells)))
    return patterns


def expected_label(defect: Defect, *, rows: int, cols: int, line_density: float = 0.5) -> str:
    """The class a defect should get on its own, from its generator class and its
    extent alone (never by running the classifier)."""
    cells = defect.cells
    n = len(cells)
    if n == 1:
        return "single"
    if defect.kind in ("twin_row", "twin_col", "diag_twin"):
        return defect.kind
    row_min, col_min = _line_thresholds(rows, cols, line_density)
    if len({r for r, _ in cells}) == 1:
        return "row_line" if n >= row_min else ("twin_row" if n == 2 else "row_segment")
    if len({c for _, c in cells}) == 1:
        return "col_line" if n >= col_min else ("twin_col" if n == 2 else "col_segment")
    return "cluster"  # the generator's only other shape, a filled rectangle


def confusion(
    defects: Iterable[Defect], patterns: list[Pattern], *, rows: int, cols: int, line_density: float = 0.5,
) -> Counter[tuple[str, str]]:
    """(expected, found) counts over the defects. "found" is the class of the
    pattern holding most of the defect's cells; "merged" when that pattern also
    holds another defect's cells (two footprints touching or overlapping);
    "split" when the defect's cells fall into more than one pattern."""
    defects = [d for d in defects if d.cells]
    owner = {cell: i for i, p in enumerate(patterns) for cell in p.cells}
    defect_of: dict[int, set[int]] = {}
    homes = []
    for j, defect in enumerate(defects):
        votes = Counter(owner[c] for c in defect.cells if c in owner)
        home = votes.most_common(1)[0][0] if votes else None
        homes.append(home)
        if home is not None:
            defect_of.setdefault(home, set()).add(j)
    table: Counter[tuple[str, str]] = Counter()
    for defect, home in zip(defects, homes):
        expected = expected_label(defect, rows=rows, cols=cols, line_density=line_density)
        if home is None:
            found = "missing"
        elif len(defect_of[home]) > 1:
            found = "merged"
        elif any(owner.get(c) != home for c in defect.cells):
            found = "split"
        else:
            found = patterns[home].label
        table[(expected, found)] += 1
    return table
