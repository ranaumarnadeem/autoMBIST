"""Defect shapes, placement models and the solver-only bitmap engine."""
from __future__ import annotations

import random

import pytest

from autombist.repair.bira import analyze_detailed
from autombist.repair.types import SpareGeometry
from autombist.yieldsim.bitmap_engine import BitmapEngine
from autombist.yieldsim.defects import (
    DEFECT_CLASSES,
    ITC2020_READINGS,
    MIX_PRESETS,
    PRESETS,
    ArrayGeometry,
    Itc2020Model,
    MixModel,
    class_counts,
    defect_model,
    union_cells,
)
from autombist.yieldsim.sweep import run_sweep

G = ArrayGeometry(rows=16, cols=12, banks=2)


def _draw(kind: str, seed: int = 0, geometry: ArrayGeometry = G, **kwargs):
    (defect,) = MixModel(weights={kind: 1.0}, **kwargs).draw(random.Random(seed), geometry, 1)
    assert defect.kind == kind
    return defect.cells


def _one_bank(cells) -> bool:
    return len({G.bank_of(c) for _, c in cells}) == 1


@pytest.mark.parametrize("seed", range(30))
def test_every_class_has_its_shape_and_stays_in_one_bank(seed: int) -> None:
    for kind in DEFECT_CLASSES:
        cells = _draw(kind, seed)
        rows = {r for r, _ in cells}
        cols = {c for _, c in cells}
        assert cells and _one_bank(cells), kind
        assert all(0 <= r < G.rows and 0 <= c < G.total_cols for r, c in cells)
        if kind == "single":
            assert len(cells) == 1
        elif kind == "full_row":
            assert len(rows) == 1 and len(cells) == G.cols
        elif kind == "full_col":
            assert len(cols) == 1 and len(cells) == G.rows
        elif kind == "row_line":
            assert len(rows) == 1 and 3 <= len(cells) <= 8 and max(cols) - min(cols) == len(cells) - 1
        elif kind == "col_line":
            assert len(cols) == 1 and 3 <= len(cells) <= 8 and max(rows) - min(rows) == len(cells) - 1
        elif kind == "twin_row":
            assert len(rows) == 1 and max(cols) - min(cols) == 1
        elif kind == "twin_col":
            assert len(cols) == 1 and max(rows) - min(rows) == 1
        elif kind == "diag_twin":
            assert len(rows) == 2 and len(cols) == 2 and len(cells) == 2
        elif kind == "cluster":
            assert len(cells) == len(rows) * len(cols) >= 2 and len(rows) <= 3 and len(cols) <= 3
        elif kind == "wl_open":
            (row,) = rows
            bank_end = (G.bank_of(min(cols)) + 1) * G.cols - 1
            assert max(cols) == bank_end and cells == {(row, c) for c in range(min(cols), bank_end + 1)}
        elif kind == "bl_open":
            (col,) = cols
            assert max(rows) == G.rows - 1 and cells == {(r, col) for r in range(min(rows), G.rows)}


def test_shapes_are_clipped_to_a_small_bank() -> None:
    tiny = ArrayGeometry(rows=2, cols=2)
    assert len(_draw("row_line", geometry=tiny)) == 2
    assert len(_draw("col_line", geometry=tiny)) == 2
    assert 2 <= len(_draw("cluster", geometry=tiny, cluster_side=(1, 5))) <= 4


def test_a_draw_is_determined_by_its_seed() -> None:
    model = defect_model("ohler-d2")
    a = model.draw(random.Random(5), G, 6)
    assert a == model.draw(random.Random(5), G, 6)
    assert a != model.draw(random.Random(6), G, 6)


def test_mix_k_counts_defects_and_overlaps_union() -> None:
    model = MixModel(weights={"full_row": 1.0})
    defects = model.draw(random.Random(1), ArrayGeometry(rows=2, cols=4), 5)
    assert len(defects) == 5 and class_counts(defects) == {"full_row": 5}
    assert len(union_cells(defects)) <= 2 * 4


@pytest.mark.parametrize(("kwargs", "fragment"), [
    ({"weights": {"blob": 1.0}}, "unknown defect classes"),
    ({"weights": {"single": 0.0}}, "not all zero"),
    ({"weights": {"single": -1.0, "cluster": 2.0}}, "non-negative"),
    ({"weights": {"single": 1.0}, "line_length": (0, 3)}, "line_length"),
    ({"weights": {"cluster": 1.0}, "cluster_side": (1, 1)}, "side of 2"),
])
def test_bad_mixes_are_refused(kwargs, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        MixModel(**kwargs)


def test_the_ohler_presets_are_table_1_of_the_paper() -> None:
    for name in ("ohler-d1", "ohler-d2", "ohler-d3"):
        assert sum(MIX_PRESETS[name].values()) == pytest.approx(1.0)
    d2 = MIX_PRESETS["ohler-d2"]
    assert (d2["full_row"], d2["full_col"], d2["row_line"] + d2["col_line"], d2["cluster"], d2["single"]) == (
        0.10, 0.10, 0.20, 0.10, 0.50)


def test_presets_resolve_and_unknown_ones_are_refused() -> None:
    for name in PRESETS:
        assert defect_model(name).name == name
    itc = defect_model("itc2020-3050", reading="total-bank")
    assert (itc.p_line, itc.p_cluster, itc.reading, itc.unit) == (0.30, 0.50, "total-bank", "cells")
    with pytest.raises(ValueError, match="unknown defect preset"):
        defect_model("itc2020-9999")


# --- the ITC'2020 sequential placement -----------------------------------------

@pytest.mark.parametrize("reading", sorted(ITC2020_READINGS))
def test_itc_places_k_distinct_cells_per_bank(reading: str) -> None:
    model = Itc2020Model(p_line=0.3, p_cluster=0.5, reading=reading)
    for seed in range(20):
        cells = union_cells(model.draw(random.Random(seed), G, 7))
        assert len(cells) == 14
        if ITC2020_READINGS[reading]["scope"] == "bank":
            assert [len(b) for b in G.split(cells)] == [7, 7]


def test_itc_with_certain_clustering_walks_through_neighbours() -> None:
    model = Itc2020Model(p_line=0.0, p_cluster=1.0, reading="each-bank")
    big = ArrayGeometry(rows=64, cols=64)
    defects = model.draw(random.Random(3), big, 10)
    order = [next(iter(d.cells)) for d in defects]
    for (r0, c0), (r1, c1) in zip(order, order[1:]):
        assert max(abs(r1 - r0), abs(c1 - c0)) == 1 and r1 != r0 and c1 != c0  # p_line 0: diagonal only


def test_itc_with_certain_lines_stays_in_line_with_the_previous_fault() -> None:
    model = Itc2020Model(p_line=0.5, p_cluster=0.0, reading="each-bank")
    defects = model.draw(random.Random(4), ArrayGeometry(rows=64, cols=64), 12)
    order = [next(iter(d.cells)) for d in defects]
    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(order, order[1:]))


def test_itc_refuses_impossible_settings() -> None:
    with pytest.raises(ValueError, match="unknown ITC'2020 reading"):
        Itc2020Model(p_line=0.1, p_cluster=0.2, reading="maybe")
    with pytest.raises(ValueError, match="at most 0.5"):
        Itc2020Model(p_line=0.6, p_cluster=0.2)
    with pytest.raises(ValueError, match="distinct faulty cells"):
        Itc2020Model(p_line=0.1, p_cluster=0.2).draw(random.Random(0), ArrayGeometry(rows=2, cols=2), 5)


# --- the bitmap engine ----------------------------------------------------------

def test_singles_within_the_budget_are_always_repaired() -> None:
    engine = BitmapEngine(ArrayGeometry(rows=32, cols=32), spare_rows=2, spare_cols=2, model=defect_model("singles"))
    result = run_sweep(engine, [1, 2, 3, 4], trials=40)
    assert all(p.repair_rate == 1.0 for p in result.points)
    assert result.unit == "defects" and result.engine == "bitmap"


def test_more_full_rows_than_spares_with_too_few_columns_is_unrepairable() -> None:
    engine = BitmapEngine(ArrayGeometry(rows=8, cols=8), spare_rows=1, spare_cols=1,
                          model=MixModel(weights={"full_row": 1.0}))
    for seed in range(20):
        record = engine.run_trial(3, seed)
        rows = record.cells // 8
        assert record.repaired == (rows <= 1)


def test_a_bank_failing_fails_the_memory_and_is_named() -> None:
    g = ArrayGeometry(rows=8, cols=4, banks=2)
    engine = BitmapEngine(g, spare_rows=0, spare_cols=0, model=defect_model("singles"))
    record = engine.run_trial(1, seed=0)
    assert record.status == "unrepairable" and len(record.extras["unrepairable_banks"]) == 1
    assert record.extras["defects"] == {"single": 1}


def test_spares_used_is_summed_over_banks() -> None:
    g = ArrayGeometry(rows=8, cols=4, banks=2)
    engine = BitmapEngine(g, spare_rows=1, spare_cols=0, model=Itc2020Model(p_line=0, p_cluster=0, reading="each-bank"))
    record = engine.run_trial(1, seed=2)
    assert record.repaired and record.spares_used == 2 and record.cells == 2


def test_the_engine_describes_its_setup() -> None:
    engine = BitmapEngine(G, spare_rows=3, spare_cols=3, model=defect_model("itc2020-3050"))
    info = engine.describe()
    assert info["geometry"] == {"rows": 16, "cols": 12, "banks": 2, "spare_rows_per_bank": 3,
                                "spare_cols_per_bank": 3, "spares_exposed": False, "area_ratio": 1.0}
    assert info["defect_model"]["unit"] == "cells" and info["defect_model"]["reading"] == "each-memory"
    with pytest.raises(ValueError, match="non-negative"):
        BitmapEngine(G, spare_rows=-1, spare_cols=0, model=defect_model("singles"))
    assert info["spare_budget"] == {"num_spare_rows": 3, "num_spare_cols": 3, "per": "bank", "total": 12}


# --- defects in the spares --------------------------------------------------------

def test_exposed_spares_widen_the_placement_and_record_the_area_ratio() -> None:
    engine = BitmapEngine(ArrayGeometry(rows=8, cols=6), spare_rows=2, spare_cols=1,
                          model=defect_model("singles"), expose_spares=True)
    assert (engine.placement.rows, engine.placement.cols) == (10, 7)
    assert engine.area_ratio == pytest.approx(70 / 48)
    assert engine.describe()["geometry"]["spares_exposed"] is True


def test_cells_in_the_spares_become_spare_faults_per_bank() -> None:
    engine = BitmapEngine(ArrayGeometry(rows=4, cols=3, banks=2), spare_rows=1, spare_cols=1,
                          model=defect_model("singles"), expose_spares=True)
    # physical bank width 4: bank 1 starts at column 4
    cells = {(0, 0), (4, 2), (1, 3), (4, 3), (2, 5), (4, 7)}
    (base0, faults0, n0), (base1, faults1, n1) = engine._banks(cells)
    assert base0 == {(0, 0)} and base1 == {(2, 1)}
    assert faults0.row_cells == {0: frozenset({2})} and faults0.col_cells == {0: frozenset({1})}
    assert faults0.corners == frozenset({(0, 0)}) and n0 == 3
    assert faults1.corners == frozenset({(0, 0)}) and n1 == 1


def test_a_word_line_open_into_the_spare_columns_takes_a_spare_row() -> None:
    """The Amirkhanyan et al. (EWDTS 2024) rule as an outcome: with spares
    exposed, a word-line open runs to the far end through the spare columns, so
    it is repairable with a spare row and never with spare columns alone."""
    model = MixModel(weights={"wl_open": 1.0})
    engine = BitmapEngine(ArrayGeometry(rows=16, cols=8), spare_rows=1, spare_cols=8, model=model,
                          expose_spares=True)
    cols_only = SpareGeometry(base_words=16, word_size=8, num_spare_rows=0, num_spare_cols=8)
    checked = 0
    for seed in range(60):
        (defect,) = model.draw(random.Random(seed), engine.placement, 1)
        ((base, faults, _),) = engine._banks(set(defect.cells))
        if not base or faults.row_cells:  # the open is on the spare row, or starts past the base columns
            continue
        checked += 1
        (row,) = {r for r, _ in base}
        best = analyze_detailed(base, engine.spares, objective="min_spares", spare_faults=faults)
        assert best.result.row_map == {row: 0} and not best.result.col_map
        assert not analyze_detailed(base, cols_only, spare_faults=faults).repaired
    assert checked > 20


def test_min_spares_and_the_overused_spares_rate() -> None:
    engine = BitmapEngine(ArrayGeometry(rows=32, cols=32), spare_rows=3, spare_cols=3,
                          model=defect_model("ohler-d2"), min_spares=True)
    result = run_sweep(engine, [3, 4, 5], trials=60)
    for point in result.points:
        for trial in point.trials:
            if trial.repaired:
                assert trial.min_spares is not None and trial.min_spares <= trial.spares_used
            else:
                assert trial.min_spares is None
    from autombist.yieldsim.sweep import build_report

    report = build_report(result)
    spares = report["points"][1]["spares"]
    assert spares["mean_min"] <= spares["mean_used"] and spares["ros"] >= 0.0
    assert spares["utilization"] == pytest.approx(spares["mean_used"] / 6)
