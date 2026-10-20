"""A trial engine that draws defects and solves the bitmap directly, no RTL.

Each trial draws k defects from a :mod:`.defects` model, unions their cells and
asks ``autombist.repair.bira.analyze`` whether each bank's spares cover its
cells. This is the repair analysis alone, with the true fail map: it says what
the spare budget can repair, not what a BIST observes (``yield-sweep`` does
that on the RTL).
"""
from __future__ import annotations

import random
from typing import Any

from ..repair.bira import analyze
from ..repair.types import RepairSolution, SpareGeometry
from .defects import ArrayGeometry, Itc2020Model, MixModel, class_counts, union_cells
from .sweep import TrialRecord

__all__ = ["BitmapEngine"]


class BitmapEngine:
    """``spare_rows``/``spare_cols`` are per bank. A memory is repaired only
    when every bank is."""

    name = "bitmap"

    def __init__(
        self,
        geometry: ArrayGeometry,
        *,
        spare_rows: int,
        spare_cols: int,
        model: MixModel | Itc2020Model,
    ) -> None:
        if spare_rows < 0 or spare_cols < 0:
            raise ValueError(f"spare counts must be non-negative, got {spare_rows} rows, {spare_cols} columns")
        self.geometry = geometry
        self.model = model
        self.spares = SpareGeometry(
            base_words=geometry.rows, word_size=geometry.cols,
            num_spare_rows=spare_rows, num_spare_cols=spare_cols,
        )
        self.unit = model.unit

    def describe(self) -> dict[str, Any]:
        return {
            "geometry": {
                **self.geometry.describe(),
                "spare_rows_per_bank": self.spares.num_spare_rows,
                "spare_cols_per_bank": self.spares.num_spare_cols,
                "spares_exposed": False,
            },
            "defect_model": {**self.model.describe(), "unit": self.unit},
            "analyzer": {"solver": "repair.bira.analyze", "objective": "first"},
        }

    def run_trial(self, k: int, seed: int) -> TrialRecord:
        rng = random.Random(seed)
        defects = self.model.draw(rng, self.geometry, k)
        cells = union_cells(defects)
        used = 0
        failing_banks = []
        for bank, bank_cells in enumerate(self.geometry.split(cells)):
            outcome = analyze(bank_cells, self.spares)
            if isinstance(outcome, RepairSolution):
                used += len(outcome.row_map) + len(outcome.col_map)
            else:
                failing_banks.append(bank)
        repaired = not failing_banks
        extras: dict[str, Any] = {"defects": class_counts(defects)}
        if failing_banks and self.geometry.banks > 1:
            extras["unrepairable_banks"] = failing_banks
        return TrialRecord(
            k=k, seed=seed, status="repaired" if repaired else "unrepairable",
            cells=len(cells), spares_used=used if repaired else None, extras=extras,
        )
