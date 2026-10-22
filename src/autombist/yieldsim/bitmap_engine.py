"""A trial engine that draws defects and solves the bitmap directly, no RTL.

Each trial draws k defects from a :mod:`.defects` model, unions their cells and
asks ``autombist.repair.bira.analyze_detailed`` whether each bank's spares cover
its cells. This is the repair analysis alone, with the true fail map: it says
what the spare budget can repair, not what a BIST observes (``yield-sweep`` does
that on the RTL).

With ``expose_spares`` the defects land on the physical bank, spares included:
spare rows above the base rows and spare columns to the right of the base
columns (OpenRAM's port-0 placement). A word-line defect then runs on into the
spare columns and a bit-line defect up into the spare rows, and every cell that
lands in a spare is a spare fault the solver must respect. The defect count k
is then per physical array, so the yield weighting needs the area ratio the
report records.
"""
from __future__ import annotations

import random
from typing import Any

from ..repair.bira import analyze_detailed
from ..repair.types import SpareFaults, SpareGeometry
from collections import Counter

from .defects import ArrayGeometry, Defect, Itc2020Model, MixModel, class_counts, union_cells
from .shapes import classify, confusion
from .sweep import TrialRecord

__all__ = ["BitmapEngine"]

Cell = tuple[int, int]


class BitmapEngine:
    """``spare_rows``/``spare_cols`` are per bank. A memory is repaired only
    when every bank is. ``min_spares`` also finds each repaired trial's
    minimum spare count (a second, slower solve), for the rate of overused
    spares."""

    name = "bitmap"

    def __init__(
        self,
        geometry: ArrayGeometry,
        *,
        spare_rows: int,
        spare_cols: int,
        model: MixModel | Itc2020Model,
        expose_spares: bool = False,
        min_spares: bool = False,
        classify_shapes: bool = False,
    ) -> None:
        if spare_rows < 0 or spare_cols < 0:
            raise ValueError(f"spare counts must be non-negative, got {spare_rows} rows, {spare_cols} columns")
        self.geometry = geometry
        self.model = model
        self.expose_spares = expose_spares
        self.min_spares = min_spares
        self.classify_shapes = classify_shapes
        self.spares = SpareGeometry(
            base_words=geometry.rows, word_size=geometry.cols,
            num_spare_rows=spare_rows, num_spare_cols=spare_cols,
        )
        self.placement = (
            ArrayGeometry(rows=geometry.rows + spare_rows, cols=geometry.cols + spare_cols, banks=geometry.banks)
            if expose_spares else geometry
        )
        self.unit = model.unit

    @property
    def area_ratio(self) -> float:
        """Placement area over the base area: 1.0 unless the spares are exposed."""
        return (self.placement.rows * self.placement.cols) / (self.geometry.rows * self.geometry.cols)

    def describe(self) -> dict[str, Any]:
        rows, cols = self.spares.num_spare_rows, self.spares.num_spare_cols
        shapes = {"shape_classifier": {"module": "yieldsim.shapes", "line_density": 0.5,
                                       "cluster_density": 0.75, "distance": 1}} if self.classify_shapes else {}
        return {
            **shapes,
            "geometry": {
                **self.geometry.describe(),
                "spare_rows_per_bank": rows,
                "spare_cols_per_bank": cols,
                "spares_exposed": self.expose_spares,
                "area_ratio": self.area_ratio,
            },
            "spare_budget": {"num_spare_rows": rows, "num_spare_cols": cols, "per": "bank",
                             "total": (rows + cols) * self.geometry.banks},
            "defect_model": {**self.model.describe(), "unit": self.unit},
            "analyzer": {"solver": "repair.bira.analyze_detailed", "objective": "first",
                         "min_spares": self.min_spares},
        }

    def _banks(self, cells: set[Cell]) -> list[tuple[set[Cell], SpareFaults | None, int]]:
        """Per bank: its base cells, its spare faults, and how many spare cells failed."""
        if not self.expose_spares:
            return [(bank_cells, None, 0) for bank_cells in self.geometry.split(cells)]
        rows, cols, width = self.geometry.rows, self.geometry.cols, self.placement.cols
        banks = [(set(), {}, {}, set()) for _ in range(self.geometry.banks)]
        for r, x in cells:
            base, row_cells, col_cells, corners = banks[x // width]
            c = x % width
            if r < rows and c < cols:
                base.add((r, c))
            elif r >= rows and c < cols:
                row_cells.setdefault(r - rows, set()).add(c)
            elif r < rows:
                col_cells.setdefault(c - cols, set()).add(r)
            else:
                corners.add((r - rows, c - cols))
        out = []
        for base, row_cells, col_cells, corners in banks:
            faults = SpareFaults(
                row_cells={i: frozenset(v) for i, v in row_cells.items()},
                col_cells={k: frozenset(v) for k, v in col_cells.items()},
                corners=frozenset(corners),
            )
            spare_cells = sum(map(len, row_cells.values())) + sum(map(len, col_cells.values())) + len(corners)
            out.append((base, faults, spare_cells))
        return out

    def _shapes(self, defects: list[Defect], bank_cells: list[set[Cell]]) -> dict[str, Any]:
        """Classify each bank's base fail map and score it against the defects
        that put cells there (their base cells, in bank coordinates)."""
        rows, cols, width = self.geometry.rows, self.geometry.cols, self.placement.cols
        per_bank: list[list[Defect]] = [[] for _ in bank_cells]
        for defect in defects:
            local: dict[int, set[Cell]] = {}
            for r, x in defect.cells:
                bank, c = divmod(x, width)
                if r < rows and c < cols:
                    local.setdefault(bank, set()).add((r, c))
            for bank, part in local.items():
                per_bank[bank].append(Defect(defect.kind, frozenset(part)))
        labels: Counter[str] = Counter()
        table: Counter[str] = Counter()
        extracted = injected = 0
        for cells, bank_defects in zip(bank_cells, per_bank):
            patterns = classify(cells, rows=rows, cols=cols, spare_rows=self.spares.num_spare_rows,
                                spare_cols=self.spares.num_spare_cols)
            labels.update(p.label for p in patterns)
            for (expected, found), n in confusion(bank_defects, patterns, rows=rows, cols=cols).items():
                table[f"{expected}->{found}"] += n
            extracted += len(patterns)
            injected += len(bank_defects)
        return {"patterns": dict(sorted(labels.items())), "confusion": dict(sorted(table.items())),
                "extracted": extracted, "injected": injected}

    def run_trial(self, k: int, seed: int) -> TrialRecord:
        rng = random.Random(seed)
        defects = self.model.draw(rng, self.placement, k)
        cells = union_cells(defects)
        used = 0
        fewest = 0
        base_cells = 0
        spare_cells = 0
        failing_banks = []
        banks = self._banks(cells)
        for bank, (bank_cells, faults, bank_spare_cells) in enumerate(banks):
            base_cells += len(bank_cells)
            spare_cells += bank_spare_cells
            first = analyze_detailed(bank_cells, self.spares, spare_faults=faults)
            if not first.repaired:
                failing_banks.append(bank)
                continue
            used += first.spares_used
            if self.min_spares:
                fewest += analyze_detailed(bank_cells, self.spares, objective="min_spares",
                                           spare_faults=faults).spares_used
        repaired = not failing_banks
        extras: dict[str, Any] = {"defects": class_counts(defects)}
        if self.classify_shapes:
            extras["shapes"] = self._shapes(defects, [bank_cells for bank_cells, _, _ in banks])
        if self.expose_spares:
            extras["spare_cells"] = spare_cells
        if failing_banks and self.geometry.banks > 1:
            extras["unrepairable_banks"] = failing_banks
        return TrialRecord(
            k=k, seed=seed, status="repaired" if repaired else "unrepairable",
            cells=base_cells, spares_used=used if repaired else None,
            min_spares=fewest if repaired and self.min_spares else None, extras=extras,
        )
