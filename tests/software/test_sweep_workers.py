"""Yield-sweep trials in worker processes give the serial run's records (#64)."""
from __future__ import annotations

import pytest

from autombist.yieldsim.bitmap_engine import BitmapEngine
from autombist.yieldsim.defects import ArrayGeometry, defect_model
from autombist.yieldsim.sweep import SWEEP_WORKERS_ENV, _sweep_workers, run_sweep


def _engine() -> BitmapEngine:
    return BitmapEngine(ArrayGeometry(rows=32, cols=32), spare_rows=2, spare_cols=2, model=defect_model("singles"))


def test_workers_reproduce_the_serial_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SWEEP_WORKERS_ENV, "1")
    serial = run_sweep(_engine(), [1, 3, 5], trials=40, base_seed=7)
    monkeypatch.setenv(SWEEP_WORKERS_ENV, "3")
    seen: list[tuple[int, int]] = []
    parallel = run_sweep(_engine(), [1, 3, 5], trials=40, base_seed=7, progress=lambda k, i, r: seen.append((k, i)))
    assert parallel == serial
    assert seen == [(k, i) for k in (1, 3, 5) for i in range(40)]  # progress still in order


def test_only_independent_engines_get_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SWEEP_WORKERS_ENV, raising=False)

    class SharedDirEngine:
        name = "rtl"

    assert _sweep_workers(SharedDirEngine(), 1000) == 1
    assert _sweep_workers(_engine(), 10) == 1  # too few trials to pay for the processes
    assert _sweep_workers(_engine(), 1000) >= 1
