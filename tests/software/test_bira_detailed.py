"""analyze_detailed and verify_solution against a brute-force physical oracle:
every assignment of spares, checked cell by cell through the remap, spare
faults included."""
from __future__ import annotations

import random
from itertools import combinations, permutations

import pytest

from autombist.repair.bira import analyze, analyze_detailed, verify_solution
from autombist.repair.types import RepairSolution, SpareFaults, SpareGeometry, Unrepairable


def _faulty_physical(geometry: SpareGeometry, cells, faults: SpareFaults) -> set[tuple[int, int]]:
    rows, bits = geometry.base_words, geometry.word_size
    physical = set(cells)
    physical |= {(rows + i, j) for i, js in faults.row_cells.items() for j in js}
    physical |= {(a, bits + k) for k, addrs in faults.col_cells.items() for a in addrs}
    physical |= {(rows + i, bits + k) for i, k in faults.corners}
    return physical


def _maps(lines: int, spares: int):
    for size in range(spares + 1):
        for chosen in combinations(range(lines), size):
            for assigned in permutations(range(spares), size):
                yield dict(zip(chosen, assigned))


def _physically_valid(geometry, physical, row_map, col_map) -> bool:
    rows, bits = geometry.base_words, geometry.word_size
    for a in range(rows):
        pa = rows + row_map[a] if a in row_map else a
        for j in range(bits):
            pj = bits + col_map[j] if j in col_map else j
            if (pa, pj) in physical:
                return False
    return True


def _oracle(geometry, cells, faults=None):
    """(repairable, fewest spares) by trying every spare assignment."""
    faults = faults or SpareFaults()
    physical = _faulty_physical(geometry, cells, faults)
    best = None
    for row_map in _maps(geometry.base_words, geometry.num_spare_rows):
        for col_map in _maps(geometry.word_size, geometry.num_spare_cols):
            if _physically_valid(geometry, physical, row_map, col_map):
                total = len(row_map) + len(col_map)
                best = total if best is None else min(best, total)
    return best is not None, best


def _random_case(rng: random.Random, rows=4, bits=4, spare_rows=2, spare_cols=2, faulty_spares=True):
    geometry = SpareGeometry(base_words=rows, word_size=bits, num_spare_rows=spare_rows, num_spare_cols=spare_cols)
    cells = {(rng.randrange(rows), rng.randrange(bits)) for _ in range(rng.randint(0, 11))}
    faults = SpareFaults()
    if faulty_spares:
        row_cells = {i: frozenset(j for j in range(bits) if rng.random() < 0.15) for i in range(spare_rows)}
        col_cells = {k: frozenset(a for a in range(rows) if rng.random() < 0.15) for k in range(spare_cols)}
        corners = frozenset((i, k) for i in range(spare_rows) for k in range(spare_cols) if rng.random() < 0.1)
        faults = SpareFaults(row_cells=row_cells, col_cells=col_cells, corners=corners)
    return geometry, cells, faults


# --- equivalence with analyze() -------------------------------------------------

@pytest.mark.parametrize("seed", range(300))
def test_the_first_objective_without_spare_faults_is_exactly_analyze(seed: int) -> None:
    rng = random.Random(seed)
    rows, bits = rng.randint(1, 12), rng.randint(1, 12)
    geometry = SpareGeometry(base_words=rows, word_size=bits,
                             num_spare_rows=rng.randint(0, 4), num_spare_cols=rng.randint(0, 4))
    cells = {(rng.randrange(rows), rng.randrange(bits)) for _ in range(rng.randint(0, 14))}

    detailed = analyze_detailed(cells, geometry)
    assert detailed.result == analyze(cells, geometry)
    if isinstance(detailed.result, RepairSolution):
        assert detailed.spares_used == len(detailed.result.row_map) + len(detailed.result.col_map)
    else:
        assert detailed.spares_used is None


# --- the physical oracle ----------------------------------------------------------

@pytest.mark.parametrize("seed", range(150))
@pytest.mark.parametrize("objective", ["first", "min_spares"])
def test_feasibility_minimality_and_validity_match_the_oracle(seed: int, objective: str) -> None:
    geometry, cells, faults = _random_case(random.Random(seed), faulty_spares=seed % 3 != 0)
    repairable, fewest = _oracle(geometry, cells, faults)

    result = analyze_detailed(cells, geometry, objective=objective, spare_faults=faults)

    assert result.repaired == repairable
    if repairable:
        assert verify_solution(cells, geometry, result.result, faults) == []
        physical = _faulty_physical(geometry, cells, faults)
        assert _physically_valid(geometry, physical, result.result.row_map, result.result.col_map)
        if objective == "min_spares":
            assert result.spares_used == result.min_spares == fewest
        else:
            assert result.spares_used >= fewest and result.min_spares is None
    else:
        assert isinstance(result.result, Unrepairable)


@pytest.mark.parametrize("seed", range(60))
def test_verify_solution_agrees_with_the_oracle_on_arbitrary_candidates(seed: int) -> None:
    rng = random.Random(1000 + seed)
    geometry, cells, faults = _random_case(rng)
    physical = _faulty_physical(geometry, cells, faults)
    maps_r = list(_maps(geometry.base_words, geometry.num_spare_rows))
    maps_c = list(_maps(geometry.word_size, geometry.num_spare_cols))
    for _ in range(40):
        row_map, col_map = rng.choice(maps_r), rng.choice(maps_c)
        problems = verify_solution(cells, geometry, RepairSolution(row_map=row_map, col_map=col_map), faults)
        assert (problems == []) == _physically_valid(geometry, physical, row_map, col_map), (row_map, col_map, problems)


# --- hand-built cases ---------------------------------------------------------------

def test_a_clean_column_repaired_only_to_unlock_a_faulty_spare_row() -> None:
    """Six rows too full for column repair force six spare rows, but only five
    are fault-free. Spare row 0 is faulty at bit 5, a column with no faulty
    cell: repairing that clean column with the only spare column unlocks it."""
    geometry = SpareGeometry(base_words=8, word_size=8, num_spare_rows=6, num_spare_cols=1)
    cells = {(row, bit) for row in range(6) for bit in (0, 1, 2)}
    faults = SpareFaults(row_cells={0: frozenset({5})})

    for objective in ("first", "min_spares"):
        result = analyze_detailed(cells, geometry, objective=objective, spare_faults=faults)
        assert result.repaired, objective
        assert result.result.col_map == {5: 0} and sorted(result.result.row_map.values()) == list(range(6))
        assert result.spares_used == 7
    assert _oracle(SpareGeometry(base_words=8, word_size=8, num_spare_rows=6, num_spare_cols=1), cells, faults)[0]


def test_min_spares_finds_fewer_spares_than_the_first_repair() -> None:
    geometry = SpareGeometry(base_words=4, word_size=4, num_spare_rows=3, num_spare_cols=1)
    cells = {(0, 0), (1, 0), (2, 0)}

    first = analyze_detailed(cells, geometry)
    fewest = analyze_detailed(cells, geometry, objective="min_spares")
    assert first.spares_used == 3 and first.result.row_map == {0: 0, 1: 1, 2: 2}
    assert fewest.spares_used == 1 and fewest.result == RepairSolution(row_map={}, col_map={0: 0})


def test_a_word_line_open_through_the_spare_columns_needs_a_spare_row() -> None:
    """Amirkhanyan et al. (EWDTS 2024): a word-line open fails its row from the
    open to the far end, spare columns included, so a spare column cannot
    replace those cells. The rule is not coded anywhere; it follows from the
    spare faults."""
    rows, bits = 4, 6
    open_row = 2
    cells = {(open_row, bit) for bit in range(3, bits)}
    wl_open = SpareFaults(col_cells={k: frozenset({open_row}) for k in range(3)})

    with_row = SpareGeometry(base_words=rows, word_size=bits, num_spare_rows=1, num_spare_cols=3)
    result = analyze_detailed(cells, with_row, objective="min_spares", spare_faults=wl_open)
    assert result.result == RepairSolution(row_map={open_row: 0}, col_map={})

    no_row = SpareGeometry(base_words=rows, word_size=bits, num_spare_rows=0, num_spare_cols=3)
    assert isinstance(analyze(cells, no_row), RepairSolution)  # blind to the spare faults
    assert not analyze_detailed(cells, no_row, spare_faults=wl_open).repaired


def test_a_bit_line_open_through_the_spare_rows_needs_a_spare_column() -> None:
    rows, bits = 6, 4
    open_bit = 1
    cells = {(row, open_bit) for row in range(2, rows)}
    bl_open = SpareFaults(row_cells={i: frozenset({open_bit}) for i in range(4)})

    geometry = SpareGeometry(base_words=rows, word_size=bits, num_spare_rows=4, num_spare_cols=1)
    result = analyze_detailed(cells, geometry, objective="min_spares", spare_faults=bl_open)
    assert result.result == RepairSolution(row_map={}, col_map={open_bit: 0})
    no_col = SpareGeometry(base_words=rows, word_size=bits, num_spare_rows=4, num_spare_cols=0)
    assert not analyze_detailed(cells, no_col, spare_faults=bl_open).repaired


def test_a_faulty_corner_forbids_its_spare_row_and_column_together() -> None:
    geometry = SpareGeometry(base_words=4, word_size=4, num_spare_rows=1, num_spare_cols=1)
    cells = {(0, 0), (0, 1), (1, 3), (2, 3)}  # needs row 0 and column 3
    assert analyze_detailed(cells, geometry).repaired
    blocked = analyze_detailed(cells, geometry, spare_faults=SpareFaults(corners=frozenset({(0, 0)})))
    assert not blocked.repaired and blocked.stats.decided_by == "spare-subsets"


def test_a_spare_that_can_never_be_used_is_not_tried() -> None:
    geometry = SpareGeometry(base_words=4, word_size=4, num_spare_rows=2, num_spare_cols=1)
    faults = SpareFaults(row_cells={1: frozenset({0, 1})})  # two faulty bits, one spare column
    result = analyze_detailed({(0, 2), (3, 2), (3, 3)}, geometry, spare_faults=faults)
    assert result.stats.spare_subsets == 1  # only the empty set: spare row 1 is unusable
    assert result.repaired and 1 not in result.result.row_map.values()


def test_spare_indices_prefer_fault_free_spares() -> None:
    geometry = SpareGeometry(base_words=4, word_size=4, num_spare_rows=3, num_spare_cols=1)
    cells = {(1, 0), (2, 0)}

    no_fault = analyze_detailed(cells, geometry, spare_faults=SpareFaults(row_cells={0: frozenset()}))
    assert no_fault.result.row_map == {1: 0, 2: 1}  # an empty fault set is no fault

    faulty = SpareFaults(row_cells={0: frozenset({3})})
    result = analyze_detailed(cells, geometry, spare_faults=faulty)
    assert result.result.row_map == {1: 1, 2: 2}  # spare row 0 is left unused
    assert verify_solution(cells, geometry, result.result, faulty) == []


def test_stats_say_how_the_answer_was_reached() -> None:
    geometry = SpareGeometry(base_words=8, word_size=8, num_spare_rows=2, num_spare_cols=2)
    assert analyze_detailed(set(), geometry).stats.decided_by == "empty"
    forced = analyze_detailed({(0, b) for b in range(5)}, geometry)
    assert forced.stats.decided_by == "must-repair" and forced.stats.forced_rows == 1
    searched = analyze_detailed({(0, 0), (1, 1)}, geometry)
    assert searched.stats.decided_by == "search" and searched.stats.nodes > 0
    aborted = analyze_detailed({(r, c) for r in range(0, 8, 2) for c in range(0, 6, 2)}, geometry)
    assert not aborted.repaired and aborted.stats.decided_by in ("early-abort", "must-repair")


@pytest.mark.parametrize(("kwargs", "fragment"), [
    ({"objective": "cheapest"}, "objective must be one of"),
    ({"spare_faults": SpareFaults(row_cells={5: frozenset()})}, "spare row 5"),
    ({"spare_faults": SpareFaults(col_cells={0: frozenset({9})})}, "spare column 0"),
    ({"spare_faults": SpareFaults(corners=frozenset({(0, 4)}))}, "corner"),
])
def test_bad_arguments_are_refused(kwargs, fragment: str) -> None:
    geometry = SpareGeometry(base_words=4, word_size=4, num_spare_rows=1, num_spare_cols=1)
    with pytest.raises(ValueError, match=fragment):
        analyze_detailed({(0, 0)}, geometry, **kwargs)


def test_verify_solution_names_each_problem() -> None:
    geometry = SpareGeometry(base_words=4, word_size=4, num_spare_rows=1, num_spare_cols=1)
    faults = SpareFaults(row_cells={0: frozenset({2})}, col_cells={0: frozenset({3})}, corners=frozenset({(0, 0)}))
    bad = RepairSolution(row_map={0: 0, 1: 0, 7: 3}, col_map={1: 0})
    problems = " | ".join(verify_solution({(2, 2)}, geometry, bad, faults))
    for fragment in ("3 row repairs exceed", "two rows share", "row 7 is outside", "spare row 3 does not exist",
                     "(2, 2) is not covered", "faulty bit 2 is not column-repaired",
                     "faulty row 3 is not row-repaired", "corner cell is faulty"):
        assert fragment in problems, fragment
