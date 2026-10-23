"""The on-chip analyzer model (yieldsim.onchip_model): march-c's fail order,
both analyzer modes on hand-checked fail maps, and the properties every
repair it returns must have. tests/integration/test_onchip_analyzer_e2e.py
checks it against the RTL."""
from __future__ import annotations

import random

import pytest

from autombist.repair.bira import analyze
from autombist.repair.types import RepairSolution, SpareGeometry
from autombist.yieldsim.defects import ArrayGeometry, defect_model
from autombist.yieldsim.onchip_model import MODES, deferred, greedy, march_c_events, onchip_solution

GAP_DEMO = {(5, 0): 1, (5, 1): 1, (9, 2): 1, (20, 2): 1, (30, 5): 1}  # tests/hardware/sram_spares_col_gap_demo.v
FOUR_SINGLES = {(0, 0): 1, (2, 3): 1, (4, 5): 1, (6, 7): 1}


def test_march_c_reads_stuck_at_1_on_r0_and_stuck_at_0_on_r1_in_both_directions() -> None:
    events = march_c_events({(1, 2): 1, (3, 0): 0, (3, 1): 1}, rows=4)
    sa1 = [(1, 0b100), (3, 0b010)]
    sa0 = [(3, 0b001)]
    assert events == [*sa1, *sa0, *sa1[::-1], *sa0[::-1], *sa1]


def test_greedy_takes_a_row_per_first_sighting_and_a_column_on_recurrence() -> None:
    events = march_c_events({(1, 4): 1, (6, 4): 1}, rows=8)  # one bit failing at two rows
    assert greedy(events, 2, 1) == ([1], [4])


def test_greedy_is_lenient_a_later_read_can_still_take_a_column() -> None:
    """Three rows go to the first three singles; the fourth finds no row on its
    first read but takes a column when the march reads it again."""
    assert onchip_solution(FOUR_SINGLES, 8, 3, 3, "greedy") == ([0, 2, 4], [7])


def test_greedy_gives_up_on_the_gap_demo_and_deferred_repairs_it() -> None:
    assert onchip_solution(GAP_DEMO, 32, 2, 1, "greedy") is None
    assert onchip_solution(GAP_DEMO, 32, 2, 1, "deferred") == ([5, 30], [2])


def test_deferred_keeps_lone_first_sightings_for_pass_two() -> None:
    """Pass one: row 2 has two failing bits and takes a row; bit 6 recurs at a
    different row and takes a column; the single at (5, 1) only waits. Pass
    two gives it the remaining row."""
    stuck = {(2, 0): 1, (2, 3): 1, (1, 6): 1, (4, 6): 1, (5, 1): 1}
    rows, cols = deferred(march_c_events(stuck, 8), 2, 1)
    assert (sorted(rows), cols) == ([2, 5], [6])


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown analyzer mode"):
        onchip_solution(FOUR_SINGLES, 8, 3, 3, "psychic")


@pytest.mark.parametrize("mode", MODES)
def test_every_repair_covers_its_cells_within_budget_and_needs_an_exact_solution(mode: str) -> None:
    geo = ArrayGeometry(rows=32, cols=16)
    model = defect_model("ohler-d2")
    spares = SpareGeometry(base_words=32, word_size=16, num_spare_rows=3, num_spare_cols=2)
    for seed in range(300):
        rng = random.Random(seed)
        stuck = {}
        for defect in model.draw(rng, geo, rng.randint(1, 6)):
            value = rng.randrange(2)
            for cell in defect.cells:
                stuck[cell] = value
        result = onchip_solution(stuck, 32, 3, 2, mode)
        if result is None:
            continue
        rows, cols = result
        assert len(rows) <= 3 and len(cols) <= 2 and len(set(rows)) == len(rows)
        assert all(r in rows or c in cols for r, c in stuck)
        assert isinstance(analyze(set(stuck), spares), RepairSolution)  # never beats the exact solver
