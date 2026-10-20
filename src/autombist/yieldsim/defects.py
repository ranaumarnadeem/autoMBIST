"""Random defects over a memory array, for repair-rate sweeps.

A defect is one physical event; it fails one or more cells. Coordinates are
``(row, col)`` in the base array, row = word address and col = bit, the same
``(addr, bit)`` pairs ``autombist.repair`` takes (one word per row, so a row is
a word line and a column a bit line). The layout follows OpenRAM's port 0: the
row decoder on the left (column 0) and the sense amplifiers below (row 0), so a
word-line open fails its row from the open to the right end and a bit-line open
fails its column from the open to the top.

A memory may have several banks side by side along the columns, each ``cols``
wide with its own spares (the ITC'2020 RA Benchmarks' local spares). A row or
line defect never crosses a bank boundary; global coordinates run across banks
(bank b holds columns ``b * cols`` to ``(b + 1) * cols - 1``).

Two placement models:

* :class:`MixModel` draws each defect independently: a class from a mix
  (Ohler, Hellebrand and Wunderlich, DDECS 2007, Table 1), and an anchor
  uniform over the positions where that shape fits. Overlapping defects union
  their cells; k counts defects.
* :class:`Itc2020Model` places single-cell faults in sequence, each next one
  near or in line with the previous one (Lee et al., ITC 2020, Fig. 5). k counts
  faulty cells. The paper leaves two things open, so each is a named reading
  (see :data:`ITC2020_READINGS`).

The shape parameters below are this module's own defaults, fixed before any
calibration run; the papers do not state them.
"""
from __future__ import annotations

import random
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEFECT_CLASSES",
    "ArrayGeometry",
    "Defect",
    "MixModel",
    "Itc2020Model",
    "ITC2020_READINGS",
    "MIX_PRESETS",
    "PRESETS",
    "defect_model",
    "union_cells",
    "class_counts",
]

Cell = tuple[int, int]

DEFECT_CLASSES = (
    "single",      # one cell
    "full_row",    # every cell of a row (one bank wide)
    "full_col",    # every cell of a column
    "row_line",    # a run of adjacent cells along a row
    "col_line",    # a run of adjacent cells along a column
    "cluster",     # a filled rectangle, at least 2 cells
    "twin_row",    # two cells adjacent along a row
    "twin_col",    # two cells adjacent along a column
    "diag_twin",   # two diagonally adjacent cells
    "wl_open",     # a word-line open: its row from the open to the far (right) end
    "bl_open",     # a bit-line open: its column from the open to the far (top) end
)


@dataclass(frozen=True, slots=True)
class ArrayGeometry:
    """``rows`` x ``cols`` base cells per bank, ``banks`` banks side by side."""

    rows: int
    cols: int
    banks: int = 1

    def __post_init__(self) -> None:
        if self.rows < 1 or self.cols < 1 or self.banks < 1:
            raise ValueError(f"rows, cols and banks must be positive, got {self.rows}x{self.cols}x{self.banks}")

    @property
    def total_cols(self) -> int:
        return self.cols * self.banks

    @property
    def cells(self) -> int:
        return self.rows * self.total_cols

    def bank_of(self, col: int) -> int:
        return col // self.cols

    def split(self, cells: set[Cell]) -> list[set[Cell]]:
        """The cells of each bank, in bank-local coordinates."""
        banks: list[set[Cell]] = [set() for _ in range(self.banks)]
        for row, col in cells:
            banks[col // self.cols].add((row, col % self.cols))
        return banks

    def describe(self) -> dict[str, int]:
        return {"rows": self.rows, "cols": self.cols, "banks": self.banks}


@dataclass(frozen=True, slots=True)
class Defect:
    kind: str
    cells: frozenset[Cell]


def union_cells(defects: list[Defect]) -> set[Cell]:
    out: set[Cell] = set()
    for defect in defects:
        out |= defect.cells
    return out


def class_counts(defects: list[Defect]) -> dict[str, int]:
    return dict(sorted(Counter(d.kind for d in defects).items()))


def _uniform(lo: int, hi: int, rng: random.Random) -> int:
    return rng.randint(lo, hi)


@dataclass(frozen=True, slots=True)
class MixModel:
    """Independent defects drawn from a class mix.

    ``weights`` maps classes in :data:`DEFECT_CLASSES` to relative weights.
    ``line_length`` is the inclusive range of a line's length and
    ``cluster_side`` of a cluster's height and width (drawn independently, a 1x1
    draw redrawn); both are clipped to the bank."""

    weights: Mapping[str, float]
    line_length: tuple[int, int] = (3, 8)
    cluster_side: tuple[int, int] = (1, 3)
    name: str = "custom"
    unit: str = field(default="defects", init=False)

    def __post_init__(self) -> None:
        unknown = set(self.weights) - set(DEFECT_CLASSES)
        if unknown:
            raise ValueError(f"unknown defect classes {sorted(unknown)}: use {DEFECT_CLASSES}")
        if any(w < 0 for w in self.weights.values()) or sum(self.weights.values()) <= 0:
            raise ValueError("defect class weights must be non-negative and not all zero")
        for label, (lo, hi) in (("line_length", self.line_length), ("cluster_side", self.cluster_side)):
            if not 1 <= lo <= hi:
                raise ValueError(f"{label} must be a range 1 <= low <= high, got {(lo, hi)}")
        if self.weights.get("cluster", 0) > 0 and self.cluster_side[1] < 2:
            raise ValueError("a cluster has at least 2 cells, so cluster_side must allow a side of 2")

    def describe(self) -> dict[str, Any]:
        return {
            "model": "mix",
            "preset": self.name,
            "weights": dict(self.weights),
            "line_length": list(self.line_length),
            "cluster_side": list(self.cluster_side),
            "placement": "anchor uniform where the shape fits; overlaps unioned",
        }

    def draw(self, rng: random.Random, geometry: ArrayGeometry, k: int) -> list[Defect]:
        classes = [c for c in DEFECT_CLASSES if self.weights.get(c, 0) > 0]
        weights = [self.weights[c] for c in classes]
        return [self._shape(rng.choices(classes, weights)[0], rng, geometry) for _ in range(k)]

    def _shape(self, kind: str, rng: random.Random, g: ArrayGeometry) -> Defect:
        bank = rng.randrange(g.banks)
        x0 = bank * g.cols  # the bank's first global column

        def run(length: int, along_row: bool) -> frozenset[Cell]:
            if along_row:
                length = min(length, g.cols)
                row, col = rng.randrange(g.rows), rng.randint(0, g.cols - length)
                return frozenset((row, x0 + col + i) for i in range(length))
            length = min(length, g.rows)
            row, col = rng.randint(0, g.rows - length), rng.randrange(g.cols)
            return frozenset((row + i, x0 + col) for i in range(length))

        if kind == "single":
            cells = frozenset({(rng.randrange(g.rows), x0 + rng.randrange(g.cols))})
        elif kind == "full_row":
            row = rng.randrange(g.rows)
            cells = frozenset((row, x0 + c) for c in range(g.cols))
        elif kind == "full_col":
            col = x0 + rng.randrange(g.cols)
            cells = frozenset((r, col) for r in range(g.rows))
        elif kind in ("row_line", "col_line"):
            cells = run(_uniform(*self.line_length, rng), kind == "row_line")
        elif kind in ("twin_row", "twin_col"):
            cells = run(2, kind == "twin_row")
        elif kind == "diag_twin":
            if g.rows < 2 or g.cols < 2:
                raise ValueError("a diagonal twin needs at least 2 rows and 2 columns")
            row, col = rng.randrange(g.rows - 1), rng.randrange(g.cols - 1)
            if rng.random() < 0.5:  # rising or falling diagonal
                cells = frozenset({(row, x0 + col), (row + 1, x0 + col + 1)})
            else:
                cells = frozenset({(row + 1, x0 + col), (row, x0 + col + 1)})
        elif kind == "cluster":
            cells = self._cluster(rng, g, x0)
        elif kind == "wl_open":
            row, start = rng.randrange(g.rows), rng.randrange(g.cols)
            cells = frozenset((row, x0 + c) for c in range(start, g.cols))
        else:  # bl_open
            col, start = x0 + rng.randrange(g.cols), rng.randrange(g.rows)
            cells = frozenset((r, col) for r in range(start, g.rows))
        return Defect(kind, cells)

    def _cluster(self, rng: random.Random, g: ArrayGeometry, x0: int) -> frozenset[Cell]:
        lo, hi = self.cluster_side
        if g.rows * g.cols < 2:
            raise ValueError("a cluster needs at least 2 cells in a bank")
        while True:
            h = min(_uniform(lo, hi, rng), g.rows)
            w = min(_uniform(lo, hi, rng), g.cols)
            if h * w >= 2:
                break
        row, col = rng.randint(0, g.rows - h), rng.randint(0, g.cols - w)
        return frozenset((row + i, x0 + col + j) for i in range(h) for j in range(w))


#: The two things Fig. 5 of the ITC'2020 paper leaves open, as named readings.
#: ``line``: "each" gives the same-row and the same-column branch p_line each
#: (2 * p_line in all); "total" gives the two together p_line, split evenly.
#: ``scope``: "memory" places banks * k faults over the whole memory at once
#: (the paper generates both banks together, so k per bank is nominal);
#: "bank" places k faults in each bank separately.
ITC2020_READINGS: dict[str, dict[str, str]] = {
    "each-memory": {"line": "each", "scope": "memory"},
    "each-bank": {"line": "each", "scope": "bank"},
    "total-memory": {"line": "total", "scope": "memory"},
    "total-bank": {"line": "total", "scope": "bank"},
}

_ORTHOGONAL = ((0, -1), (0, 1), (-1, 0), (1, 0))
_DIAGONAL = ((-1, -1), (-1, 1), (1, -1), (1, 1))


@dataclass(frozen=True, slots=True)
class Itc2020Model:
    """Sequential single-cell faults after the ITC'2020 RA Benchmarks (Fig. 5).

    The first fault is uniform. Each next one, relative to the latest fault: with
    ``p_cluster`` it is one of its 8 neighbours (in line with it at the line
    probability, else diagonal); otherwise with the line probability it is
    elsewhere on the same row or column; otherwise uniform. A candidate outside
    the memory (or bank) or already faulty is redrawn within the same branch; a
    branch with no candidate left falls back to uniform. k is per bank."""

    p_line: float
    p_cluster: float
    reading: str = "each-memory"
    name: str = "itc2020"
    unit: str = field(default="cells", init=False)

    def __post_init__(self) -> None:
        if self.reading not in ITC2020_READINGS:
            raise ValueError(f"unknown ITC'2020 reading {self.reading!r}: use one of {sorted(ITC2020_READINGS)}")
        if not (0 <= self.p_cluster <= 1 and 0 <= self.p_line <= 1):
            raise ValueError("p_line and p_cluster must be probabilities")
        if ITC2020_READINGS[self.reading]["line"] == "each" and self.p_line > 0.5:
            raise ValueError("with the 'each' reading p_line is per orientation, so it must be at most 0.5")

    def describe(self) -> dict[str, Any]:
        return {
            "model": "itc2020",
            "preset": self.name,
            "p_line": self.p_line,
            "p_cluster": self.p_cluster,
            "reading": self.reading,
            **ITC2020_READINGS[self.reading],
            "unit_note": "k is faulty cells per bank",
        }

    def draw(self, rng: random.Random, geometry: ArrayGeometry, k: int) -> list[Defect]:
        if ITC2020_READINGS[self.reading]["scope"] == "memory":
            region = (0, geometry.total_cols)
            cells = self._sequence(rng, geometry.rows, region, k * geometry.banks)
        else:
            cells = []
            for bank in range(geometry.banks):
                cells += self._sequence(rng, geometry.rows, (bank * geometry.cols, (bank + 1) * geometry.cols), k)
        return [Defect("single", frozenset({cell})) for cell in cells]

    def _sequence(self, rng: random.Random, rows: int, region: tuple[int, int], n: int) -> list[Cell]:
        x_lo, x_hi = region
        if n > rows * (x_hi - x_lo):
            raise ValueError(f"cannot place {n} distinct faulty cells in {rows * (x_hi - x_lo)} cells")
        each = ITC2020_READINGS[self.reading]["line"] == "each"
        p_row = self.p_line if each else self.p_line / 2
        p_col = p_row
        p_orth = 2 * self.p_line if each else self.p_line  # in-line share of the neighbour branch
        faulty: set[Cell] = set()
        order: list[Cell] = []

        def inside(cell: Cell) -> bool:
            return 0 <= cell[0] < rows and x_lo <= cell[1] < x_hi and cell not in faulty

        def uniform() -> Cell:
            while True:
                cell = (rng.randrange(rows), rng.randrange(x_lo, x_hi))
                if cell not in faulty:
                    return cell

        def pick(candidates: list[Cell]) -> Cell:
            free = [c for c in candidates if inside(c)]
            return rng.choice(free) if free else uniform()

        for _ in range(n):
            if not order:
                cell = uniform()
            else:
                r, c = order[-1]
                u = rng.random()
                if u < self.p_cluster:
                    offsets = _ORTHOGONAL if rng.random() < p_orth else _DIAGONAL
                    cell = pick([(r + dr, c + dc) for dr, dc in offsets])
                else:
                    v = rng.random()
                    if v < p_row:
                        cell = pick([(r, x) for x in range(x_lo, x_hi) if x != c])
                    elif v < p_row + p_col:
                        cell = pick([(y, c) for y in range(rows) if y != r])
                    else:
                        cell = uniform()
            faulty.add(cell)
            order.append(cell)
        return order


#: Ohler et al., DDECS 2007, Table 1 (row, column, line, cluster, single). A
#: line's orientation is even, so the line share is split over row_line and col_line.
MIX_PRESETS: dict[str, dict[str, float]] = {
    "singles": {"single": 1.0},
    "ohler-d1": {"full_row": 0.10, "full_col": 0.10, "row_line": 0.05, "col_line": 0.05, "cluster": 0.05, "single": 0.65},
    "ohler-d2": {"full_row": 0.10, "full_col": 0.10, "row_line": 0.10, "col_line": 0.10, "cluster": 0.10, "single": 0.50},
    "ohler-d3": {"full_row": 0.10, "full_col": 0.10, "row_line": 0.20, "col_line": 0.20, "cluster": 0.20, "single": 0.20},
}

#: Every preset name :func:`defect_model` accepts.
PRESETS = (*MIX_PRESETS, *(f"itc2020-{p}" for p in ("1020", "1050", "3020", "3050")))


def defect_model(preset: str, *, reading: str = "each-memory") -> MixModel | Itc2020Model:
    """A defect model by preset name. ``itc2020-LLCC`` is the ITC'2020 placement
    with p_line LL% and p_cluster CC% (the benchmark names' last four digits)."""
    if preset in MIX_PRESETS:
        return MixModel(weights=MIX_PRESETS[preset], name=preset)
    if preset.startswith("itc2020-") and preset in PRESETS:
        digits = preset.removeprefix("itc2020-")
        return Itc2020Model(p_line=int(digits[:2]) / 100, p_cluster=int(digits[2:]) / 100, reading=reading, name=preset)
    raise ValueError(f"unknown defect preset {preset!r}: use one of {', '.join(PRESETS)}")
