"""The Lin-2016-style shape classifier: hand-built bitmaps, repair overlays, and
agreement with the defect generator's own shapes."""
from __future__ import annotations

import random

import pytest

from autombist.yieldsim.bitmap_engine import BitmapEngine
from autombist.yieldsim.defects import DEFECT_CLASSES, ArrayGeometry, Defect, MixModel, defect_model, union_cells
from autombist.yieldsim.shapes import CLASSES, classify, confusion, expected_label, min_line_cover
from autombist.yieldsim.sweep import build_report, run_sweep

ROWS, COLS = 32, 32


def _labels(cells, **kwargs):
    return sorted(p.label for p in classify(cells, rows=ROWS, cols=COLS, **kwargs))


@pytest.mark.parametrize(("cells", "label"), [
    ({(4, 4)}, "single"),
    ({(4, 4), (4, 5)}, "twin_row"),
    ({(4, 4), (5, 4)}, "twin_col"),
    ({(4, 4), (5, 5)}, "diag_twin"),
    ({(4, 5), (5, 4)}, "diag_twin"),
    ({(4, c) for c in range(3, 9)}, "row_segment"),
    ({(r, 7) for r in range(2, 5)}, "col_segment"),
    ({(r, c) for r in range(3) for c in range(3)}, "cluster"),
    ({(r, c) for r in range(2) for c in range(2)} - {(1, 1)}, "cluster"),  # 3 of 4: 75%
    ({(0, 0), (1, 1), (2, 2)}, "other"),
    ({(9, c) for c in range(COLS)}, "row_line"),
    ({(r, 3) for r in range(10, ROWS)}, "col_line"),  # 22 of 32 rows: over half
])
def test_each_class_from_a_hand_built_bitmap(cells, label: str) -> None:
    assert _labels(cells) == [label]


def test_crossing_lines_are_one_cross_and_parallel_lines_stay_apart() -> None:
    row, col = {(5, c) for c in range(COLS)}, {(r, 9) for r in range(ROWS)}
    (cross,) = classify(row | col, rows=ROWS, cols=COLS)
    assert cross.label == "cross" and cross.spare_cost == 2 and len(cross.cells) == ROWS + COLS - 1
    assert _labels({(5, c) for c in range(COLS)} | {(6, c) for c in range(COLS)}) == ["row_line", "row_line"]


def test_lines_come_out_before_grouping() -> None:
    """A single cell next to a full row is still a single, not part of a blob."""
    cells = {(5, c) for c in range(COLS)} | {(6, 10)}
    assert _labels(cells) == ["row_line", "single"]


def test_distance_merges_nearby_cells_into_one_group() -> None:
    cells = {(4, 4), (6, 6)}
    assert _labels(cells) == ["single", "single"]
    assert _labels(cells, distance=2) == ["other"]


def test_the_overlays_spare_cost_must_repair_and_orthogonal() -> None:
    cluster = {(r, c) for r in range(10, 13) for c in range(10, 14)}  # 3 x 4
    lone = {(20, 25)}
    shared = {(0, 30), (0, 31)}
    patterns = {p.label: p for p in classify(cluster | lone | shared, rows=ROWS, cols=COLS,
                                             spare_rows=3, spare_cols=3)}
    assert patterns["cluster"].spare_cost == 3
    # 4 fails in a row exceed 3 spare columns; 3 in a column do not exceed 3 spare rows
    assert patterns["cluster"].must_row and not patterns["cluster"].must_col
    assert patterns["single"].orthogonal and patterns["single"].spare_cost == 1
    assert patterns["twin_row"].spare_cost == 1 and not patterns["twin_row"].orthogonal


def test_patterns_come_largest_first_and_cover_every_cell() -> None:
    rng = random.Random(3)
    cells = {(rng.randrange(ROWS), rng.randrange(COLS)) for _ in range(60)}
    patterns = classify(cells, rows=ROWS, cols=COLS)
    assert set().union(*(p.cells for p in patterns)) == cells
    assert sum(len(p.cells) for p in patterns) == len(cells)  # disjoint
    assert [len(p.cells) for p in patterns] == sorted((len(p.cells) for p in patterns), reverse=True)
    assert all(p.label in CLASSES for p in patterns)


def test_min_line_cover_is_konig() -> None:
    assert min_line_cover(set()) == 0
    assert min_line_cover({(0, 0), (0, 1), (1, 0)}) == 2
    assert min_line_cover({(r, c) for r in range(3) for c in range(5)}) == 3


@pytest.mark.parametrize(("kwargs", "fragment"), [
    ({"line_density": 0.0}, "line_density"),
    ({"cluster_density": 1.5}, "cluster_density"),
    ({"distance": 0}, "distance"),
])
def test_bad_parameters_are_refused(kwargs, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        classify({(0, 0)}, rows=4, cols=4, **kwargs)


# --- against the generator -----------------------------------------------------

def test_expected_labels_follow_each_generator_class() -> None:
    g = ArrayGeometry(rows=64, cols=64)
    model = MixModel(weights={kind: 1.0 for kind in DEFECT_CLASSES})
    rng = random.Random(0)
    seen = set()
    for _ in range(400):
        (defect,) = model.draw(rng, g, 1)
        label = expected_label(defect, rows=g.rows, cols=g.cols)
        seen.add((defect.kind, label))
        assert label in CLASSES
    assert ("full_row", "row_line") in seen and ("full_col", "col_line") in seen
    assert ("cluster", "cluster") in seen and ("single", "single") in seen
    assert ("row_line", "row_segment") in seen and ("diag_twin", "diag_twin") in seen


def test_one_defect_at_a_time_the_confusion_is_diagonal() -> None:
    """With a single defect there is nothing to merge with, so every defect must
    get the class its own shape says: this measures the classifier alone."""
    g = ArrayGeometry(rows=64, cols=64)
    model = MixModel(weights={kind: 1.0 for kind in DEFECT_CLASSES})
    rng = random.Random(1)
    for _ in range(500):
        defects = model.draw(rng, g, 1)
        patterns = classify(union_cells(defects), rows=g.rows, cols=g.cols)
        table = confusion(defects, patterns, rows=g.rows, cols=g.cols)
        ((expected, found),) = table
        assert expected == found, defects[0]
        assert len(patterns) == 1  # the defect spectrum: one extracted for one injected


def test_touching_defects_are_counted_as_merged() -> None:
    a = Defect("single", frozenset({(3, 3)}))
    b = Defect("single", frozenset({(3, 4)}))
    patterns = classify({(3, 3), (3, 4)}, rows=ROWS, cols=COLS)
    assert confusion([a, b], patterns, rows=ROWS, cols=COLS) == {("single", "merged"): 2}


def test_a_defect_cut_by_a_line_is_split() -> None:
    line = Defect("full_row", frozenset((5, c) for c in range(COLS)))
    blob = Defect("cluster", frozenset({(5, 40 % COLS), (6, 40 % COLS)}))  # half on the line
    patterns = classify(line.cells | blob.cells, rows=ROWS, cols=COLS)
    table = confusion([line, blob], patterns, rows=ROWS, cols=COLS)
    assert table[("row_line", "merged")] + table[("row_line", "row_line")] == 1
    assert sum(n for (e, f), n in table.items() if e == "twin_col") == 1


def test_the_engine_reports_classes_merges_and_the_spectrum() -> None:
    engine = BitmapEngine(ArrayGeometry(rows=64, cols=64), spare_rows=4, spare_cols=4,
                          model=defect_model("ohler-d2"), classify_shapes=True)
    report = build_report(run_sweep(engine, [1, 12], trials=40), trial_detail="none")
    one, many = report["points"]
    assert one["shapes"]["merge_rate"] == 0.0 and one["shapes"]["spectrum"] == {"0": 40}
    assert all(pair.split("->")[0] == pair.split("->")[1] for pair in one["shapes"]["confusion"])
    assert many["shapes"]["merge_rate"] > 0.0
    assert sum(many["shapes"]["patterns"].values()) <= 12 * 40
    assert report["shape_classifier"]["module"] == "yieldsim.shapes"
