"""BIRA -- Built-In Redundancy Analysis (the pure allocation function).

``analyze(fail_cells, spare_geometry) -> RepairSolution | Unrepairable`` is a PURE
function: it imports only ``.types`` and takes plain data (a ``set[(addr, bit)]``
fail map + a ``SpareGeometry``). It never touches the simulator, the generator, or
the runner -- which is what makes this whole subpackage extractable. The fail map
it consumes is exactly what ``autombist.bira_input.fail_cells(report)`` produces,
but ``analyze`` does NOT import that adapter: it depends on the *shape* (a set of
integer ``(addr, bit)`` pairs), not the code.

Allocates BOTH spare rows and spare columns (2D). A spare row replaces an entire
faulty row (every bit in it); a spare column replaces one faulty bit-position
across every row it is used on. Deciding whether every fault can be covered using
at most ``num_spare_rows`` row-repairs and ``num_spare_cols`` column-repairs is a
bipartite vertex-cover problem with INDEPENDENT per-side budgets -- provably
NP-complete in general (Kuo & Fuchs, 1987). Real BIRA engines handle this with a
two-phase approach, used here too:

  1. Must-repair (``_must_repair``): a row/column whose remaining fault count
     exceeds the OTHER side's remaining spare budget cannot possibly be covered
     any other way, so it is forced. This is checked against the CURRENT
     remaining budget, not the original one -- forcing one dimension can newly
     force the other purely because its complementary budget shrank, even
     without any fault count changing. Iterated to a fixed point.
  2. Residual backtracking (``_solve_residual``): whatever remains after the
     fixed point is genuinely ambiguous (every remaining row/column already fits
     within the other side's remaining budget) and is solved by real
     backtracking -- not a greedy one-shot preference, which is provably
     insufficient in general for this class of problem.

When ``num_spare_cols == 0`` (the row-only case), every faulty row is forced
immediately in phase 1 (its remaining column-degree is always >= 1, which always
exceeds a budget of 0), so this degenerates to exactly the original row-only
algorithm: the same ``row_map``, an empty ``col_map``.
"""
from __future__ import annotations

from itertools import combinations

from .types import (
    Analysis,
    AnalysisResult,
    RepairSolution,
    SolverStats,
    SpareFaults,
    SpareGeometry,
    Unrepairable,
)

__all__ = ["analyze", "analyze_detailed", "verify_solution", "OBJECTIVES"]


def _validate_fail_cells(
    fail_cells: set[tuple[int, int]], spare_geometry: SpareGeometry
) -> None:
    for addr, bit in fail_cells:
        if addr < 0 or addr >= spare_geometry.base_words:
            raise ValueError(
                f"fail_cells row address {addr} is outside the logical address "
                f"space [0, {spare_geometry.base_words}) of the given geometry"
            )
        if bit < 0 or bit >= spare_geometry.word_size:
            raise ValueError(
                f"fail_cells bit {bit} is outside the word [0, "
                f"{spare_geometry.word_size}) of the given geometry"
            )


def _forced_row(remaining: set[tuple[int, int]], c_left: int) -> int | None:
    """The smallest-address row whose remaining distinct-column count exceeds
    ``c_left`` -- i.e. cannot possibly be fully covered by column repairs alone,
    so it must be repaired by a spare row. ``None`` if no row is forced."""
    degree: dict[int, set[int]] = {}
    for addr, bit in remaining:
        degree.setdefault(addr, set()).add(bit)
    forced = [addr for addr, cols in degree.items() if len(cols) > c_left]
    return min(forced) if forced else None


def _forced_col(remaining: set[tuple[int, int]], r_left: int) -> int | None:
    """Symmetric to :func:`_forced_row`: forces a column whose remaining
    distinct-row count exceeds the remaining row budget."""
    degree: dict[int, set[int]] = {}
    for addr, bit in remaining:
        degree.setdefault(bit, set()).add(addr)
    forced = [bit for bit, rows in degree.items() if len(rows) > r_left]
    return min(forced) if forced else None


_MustRepairResult = tuple[set[int], set[int], set[tuple[int, int]], int, int]


def _must_repair(
    fail_cells: set[tuple[int, int]], r_budget: int, c_budget: int
) -> _MustRepairResult | None:
    """Iterate the must-repair fixed point.

    Returns ``(selected_rows, selected_cols, remaining, r_left, c_left)`` once no
    row/column is forced any more, or ``None`` if a forced repair is discovered
    with no budget left to apply it (immediately unrepairable). Forcing is
    monotonic -- once a row/column is forced it stays validly forced, since a
    complementary budget only ever shrinks -- so processing one forced item at a
    time and rescanning is safe and order-independent.
    """
    remaining = set(fail_cells)
    selected_rows: set[int] = set()
    selected_cols: set[int] = set()
    r_left, c_left = r_budget, c_budget

    while True:
        row = _forced_row(remaining, c_left)
        col = _forced_col(remaining, r_left)
        if row is None and col is None:
            return selected_rows, selected_cols, remaining, r_left, c_left

        # Ties (both a forced row and a forced column found in the same scan)
        # resolve row-before-column -- a fixed rule needed only for determinism
        # of the search; it has no bearing on the final spare numbering, which
        # `analyze()` assigns afterwards over the sorted selected sets.
        if row is not None:
            if r_left == 0:
                return None
            selected_rows.add(row)
            r_left -= 1
            remaining = {(a, b) for a, b in remaining if a != row}
        else:
            assert col is not None
            # No symmetric "c_left == 0" guard here (unlike the row branch
            # above): row-priority makes it unreachable. If c_left were 0, any
            # row touching this column's remaining fault would itself have
            # degree >= 1 > c_left, making `row` non-None -- so we'd never
            # reach this branch with c_left already exhausted.
            selected_cols.add(col)
            c_left -= 1
            remaining = {(a, b) for a, b in remaining if b != col}


def _solve_residual(
    remaining: set[tuple[int, int]], r_left: int, c_left: int
) -> tuple[set[int], set[int]] | None:
    """Complete backtracking search over the must-repair-reduced residual.

    Every remaining row/column here already fits the other side's remaining
    budget (the must-repair fixed point's own halting condition) -- but
    covering ALL of them simultaneously within BOTH budgets can still require
    choosing correctly, per fault, between repairing its row or its column,
    which is why this recurses on both options rather than picking one fixed
    preference. Returns the ADDITIONAL rows/columns this phase selects, or
    ``None`` if no combination fits the remaining budgets.
    """
    if not remaining:
        return set(), set()

    # Canonical, deterministic choice of which fault to branch on.
    addr, bit = min(remaining)

    if r_left > 0:
        reduced = {(a, b) for a, b in remaining if a != addr}
        sub = _solve_residual(reduced, r_left - 1, c_left)
        if sub is not None:
            sub_rows, sub_cols = sub
            return sub_rows | {addr}, sub_cols

    if c_left > 0:
        reduced = {(a, b) for a, b in remaining if b != bit}
        sub = _solve_residual(reduced, r_left, c_left - 1)
        if sub is not None:
            sub_rows, sub_cols = sub
            return sub_rows, sub_cols | {bit}

    return None


def _unrepairable(
    fail_cells: set[tuple[int, int]], spare_geometry: SpareGeometry
) -> Unrepairable:
    """Build the verdict from the ORIGINAL inputs, not partial search state, so
    the row-only case's fields match the original row-only algorithm exactly
    regardless of when during the search infeasibility was detected."""
    faulty_rows = tuple(sorted({addr for addr, _bit in fail_cells}))
    if spare_geometry.num_spare_cols > 0:
        faulty_cols = tuple(sorted({bit for _addr, bit in fail_cells}))
        return Unrepairable(
            faulty_rows=faulty_rows,
            num_spare_rows=spare_geometry.num_spare_rows,
            reason=(
                "no row/column repair allocation covers all faults within the "
                "available spares"
            ),
            faulty_cols=faulty_cols,
            num_spare_cols=spare_geometry.num_spare_cols,
        )
    return Unrepairable(
        faulty_rows=faulty_rows,
        num_spare_rows=spare_geometry.num_spare_rows,
    )


def analyze(
    fail_cells: set[tuple[int, int]],
    spare_geometry: SpareGeometry,
) -> AnalysisResult:
    """Compute a row+column repair for the observed failing cells.

    ``fail_cells`` is the set of ``(addr, bit)`` coordinates the BIST observed
    failing (e.g. from ``autombist.bira_input.fail_cells``). Returns a
    :class:`RepairSolution` mapping each repaired row/column to a spare index
    (assigned deterministically by ascending address/bit, independent of
    discovery order -- via a final ``enumerate(sorted(...))`` pass) when a
    covering allocation exists within the spare budget, else
    :class:`Unrepairable`. An empty ``fail_cells`` yields an empty
    :class:`RepairSolution` ("nothing to repair").

    Raises ``ValueError`` if any ``(addr, bit)`` is outside the geometry's
    logical bounds -- that means the caller fed a fail map inconsistent with the
    stated geometry.
    """
    _validate_fail_cells(fail_cells, spare_geometry)

    forced = _must_repair(
        fail_cells, spare_geometry.num_spare_rows, spare_geometry.num_spare_cols
    )
    if forced is None:
        return _unrepairable(fail_cells, spare_geometry)

    selected_rows, selected_cols, remaining, r_left, c_left = forced
    residual = _solve_residual(remaining, r_left, c_left)
    if residual is None:
        return _unrepairable(fail_cells, spare_geometry)

    residual_rows, residual_cols = residual
    selected_rows |= residual_rows
    selected_cols |= residual_cols

    return RepairSolution(
        row_map={row: index for index, row in enumerate(sorted(selected_rows))},
        col_map={col: index for index, col in enumerate(sorted(selected_cols))},
    )


# --- analyze_detailed: minimum spares, faulty spares, statistics --------------

OBJECTIVES = ("first", "min_spares")


class _Counter:
    __slots__ = ("nodes",)

    def __init__(self) -> None:
        self.nodes = 0


def _search_first(remaining: set[tuple[int, int]], r_left: int, c_left: int, counter: _Counter):
    """:func:`_solve_residual` with a node count: the same branching order, so the
    same answer."""
    counter.nodes += 1
    if not remaining:
        return set(), set()
    addr, bit = min(remaining)
    if r_left > 0:
        sub = _search_first({(a, b) for a, b in remaining if a != addr}, r_left - 1, c_left, counter)
        if sub is not None:
            return sub[0] | {addr}, sub[1]
    if c_left > 0:
        sub = _search_first({(a, b) for a, b in remaining if b != bit}, r_left, c_left - 1, counter)
        if sub is not None:
            return sub[0], sub[1] | {bit}
    return None


def _max_matching(cells: set[tuple[int, int]]) -> int:
    """Maximum matching of the row/column bipartite graph (Kuhn's algorithm). By
    Konig's theorem it equals the fewest lines covering every cell, ignoring the
    separate row and column budgets: a lower bound on the spares any repair uses."""
    adjacency: dict[int, list[int]] = {}
    for addr, bit in sorted(cells):
        adjacency.setdefault(addr, []).append(bit)
    match_col: dict[int, int] = {}

    def augment(row: int, seen: set[int]) -> bool:
        for col in adjacency[row]:
            if col in seen:
                continue
            seen.add(col)
            if col not in match_col or augment(match_col[col], seen):
                match_col[col] = row
                return True
        return False

    return sum(1 for row in sorted(adjacency) if augment(row, set()))


def _greedy_matching(cells: set[tuple[int, int]]) -> int:
    """Any matching bounds a cover from below; a greedy one is cheap per node."""
    rows: set[int] = set()
    cols: set[int] = set()
    for addr, bit in sorted(cells):
        if addr not in rows and bit not in cols:
            rows.add(addr)
            cols.add(bit)
    return len(rows)


def _search_bounded(remaining: set[tuple[int, int]], r_left: int, c_left: int, total: int, counter: _Counter):
    """A cover of ``remaining`` with at most ``r_left`` rows, ``c_left`` columns
    and ``total`` lines in all, or None."""
    counter.nodes += 1
    if not remaining:
        return set(), set()
    if total <= 0 or _greedy_matching(remaining) > total:
        return None
    addr, bit = min(remaining)
    if r_left > 0:
        sub = _search_bounded({(a, b) for a, b in remaining if a != addr}, r_left - 1, c_left, total - 1, counter)
        if sub is not None:
            return sub[0] | {addr}, sub[1]
    if c_left > 0:
        sub = _search_bounded({(a, b) for a, b in remaining if b != bit}, r_left, c_left - 1, total - 1, counter)
        if sub is not None:
            return sub[0], sub[1] | {bit}
    return None


def _solve_classic(
    fail_cells: set[tuple[int, int]],
    pre_rows: frozenset[int],
    pre_cols: frozenset[int],
    r_budget: int,
    c_budget: int,
    objective: str,
    counter: _Counter,
) -> tuple[tuple[set[int], set[int]] | None, str, int, int]:
    """The classic problem with some lines repaired in advance. Returns
    (rows, cols) or None, the stage that decided, and the must-repair counts."""
    r_left, c_left = r_budget - len(pre_rows), c_budget - len(pre_cols)
    if r_left < 0 or c_left < 0:
        return None, "must-repair", 0, 0
    remaining = {(a, b) for a, b in fail_cells if a not in pre_rows and b not in pre_cols}
    forced = _must_repair(remaining, r_left, c_left)
    if forced is None:
        return None, "must-repair", 0, 0
    rows, cols, residual, r_left, c_left = forced
    if not residual:
        stage = "must-repair" if rows or cols else "empty"
        return (set(pre_rows) | rows, set(pre_cols) | cols), stage, len(rows), len(cols)
    # After the fixed point every row has at most c_left cells and every column
    # at most r_left, so r_left rows and c_left columns cover at most 2 r c cells.
    if len(residual) > 2 * r_left * c_left:
        return None, "early-abort", len(rows), len(cols)
    if objective == "first":
        found = _search_first(residual, r_left, c_left, counter)
    else:
        found = None
        for total in range(_max_matching(residual), r_left + c_left + 1):
            found = _search_bounded(residual, r_left, c_left, total, counter)
            if found is not None:
                break
    if found is None:
        return None, "search", len(rows), len(cols)
    return (set(pre_rows) | rows | found[0], set(pre_cols) | cols | found[1]), "search", len(rows), len(cols)


def _validate_spare_faults(faults: SpareFaults, geometry: SpareGeometry) -> None:
    for i, bits in faults.row_cells.items():
        if not 0 <= i < geometry.num_spare_rows or any(not 0 <= j < geometry.word_size for j in bits):
            raise ValueError(f"spare row {i} faults {sorted(bits)} are outside the geometry")
    for k, addrs in faults.col_cells.items():
        if not 0 <= k < geometry.num_spare_cols or any(not 0 <= a < geometry.base_words for a in addrs):
            raise ValueError(f"spare column {k} faults {sorted(addrs)} are outside the geometry")
    for i, k in faults.corners:
        if not (0 <= i < geometry.num_spare_rows and 0 <= k < geometry.num_spare_cols):
            raise ValueError(f"spare corner {(i, k)} is outside the geometry")


def _spare_subsets(bad_rows: list[int], bad_cols: list[int], corners: frozenset[tuple[int, int]]):
    """Every set of faulty spares that can be used together (no faulty corner
    between a chosen spare row and a chosen spare column), smallest first."""
    pool = [("r", i) for i in bad_rows] + [("c", k) for k in bad_cols]
    for size in range(len(pool) + 1):
        for chosen in combinations(pool, size):
            rows = [i for kind, i in chosen if kind == "r"]
            cols = [k for kind, k in chosen if kind == "c"]
            if not any((i, k) in corners for i in rows for k in cols):
                yield rows, cols


def analyze_detailed(
    fail_cells: set[tuple[int, int]],
    spare_geometry: SpareGeometry,
    *,
    objective: str = "first",
    spare_faults: SpareFaults | None = None,
) -> Analysis:
    """:func:`analyze` with an objective, faulty spares and statistics.

    ``objective="first"`` returns the first repair the search finds; with no
    faulty spares it is exactly :func:`analyze`'s. ``"min_spares"`` returns a
    repair using the fewest spares in all (must-repair lines, then iterative
    deepening on the total from the Konig matching bound).

    Faulty spares reduce to the classic problem: for each set of faulty spares
    allowed in use (smallest first; no faulty corner between them), the lines
    their faults need are repaired in advance and the budget is the fault-free
    spares plus that set. A spare that could never be used (more faulty cells
    than the other side has spares) is never tried. Spare indices are assigned
    fault-free spares first, in ascending order.
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {OBJECTIVES}, got {objective!r}")
    _validate_fail_cells(fail_cells, spare_geometry)
    faults = spare_faults if spare_faults is not None else SpareFaults()
    _validate_spare_faults(faults, spare_geometry)
    n_rows, n_cols = spare_geometry.num_spare_rows, spare_geometry.num_spare_cols

    bad_row_set = {i for i, bits in faults.row_cells.items() if bits} | {i for i, _ in faults.corners}
    bad_col_set = {k for k, addrs in faults.col_cells.items() if addrs} | {k for _, k in faults.corners}
    good_rows = [i for i in range(n_rows) if i not in bad_row_set]
    good_cols = [k for k in range(n_cols) if k not in bad_col_set]
    usable_rows = sorted(i for i in bad_row_set if len(faults.row_cells.get(i, ())) <= n_cols)
    usable_cols = sorted(k for k in bad_col_set if len(faults.col_cells.get(k, ())) <= n_rows)

    counter = _Counter()
    floor = _max_matching(set(fail_cells)) if objective == "min_spares" else 0
    best = None
    subsets = 0
    stage, forced_r, forced_c = "", 0, 0
    for used_rows, used_cols in _spare_subsets(usable_rows, usable_cols, faults.corners):
        subsets += 1
        pre_cols = frozenset().union(*(faults.row_cells.get(i, frozenset()) for i in used_rows))
        pre_rows = frozenset().union(*(faults.col_cells.get(k, frozenset()) for k in used_cols))
        found, stage, forced_r, forced_c = _solve_classic(
            set(fail_cells), pre_rows, pre_cols,
            len(good_rows) + len(used_rows), len(good_cols) + len(used_cols), objective, counter,
        )
        if found is None:
            continue
        total = len(found[0]) + len(found[1])
        if best is None or total < best[0]:
            best = (total, found, used_rows, used_cols, stage, forced_r, forced_c)
        if objective == "first" or best[0] <= floor:
            break

    if best is None:
        decided = stage if subsets == 1 else "spare-subsets"
        stats = SolverStats(nodes=counter.nodes, forced_rows=forced_r, forced_cols=forced_c,
                            spare_subsets=subsets, decided_by=decided)
        return Analysis(result=_unrepairable(fail_cells, spare_geometry), objective=objective,
                        spares_used=None, stats=stats)

    total, (rows, cols), used_rows, used_cols, stage, forced_r, forced_c = best
    row_spares = good_rows + sorted(used_rows)
    col_spares = good_cols + sorted(used_cols)
    solution = RepairSolution(
        row_map={row: row_spares[n] for n, row in enumerate(sorted(rows))},
        col_map={col: col_spares[n] for n, col in enumerate(sorted(cols))},
    )
    stats = SolverStats(nodes=counter.nodes, forced_rows=forced_r, forced_cols=forced_c,
                        spare_subsets=subsets, decided_by=stage)
    return Analysis(result=solution, objective=objective, spares_used=total, stats=stats)


def verify_solution(
    fail_cells: set[tuple[int, int]],
    spare_geometry: SpareGeometry,
    solution: RepairSolution,
    spare_faults: SpareFaults | None = None,
) -> list[str]:
    """Every way ``solution`` fails to repair ``fail_cells`` on this geometry,
    spare faults included; an empty list means it is a valid repair."""
    faults = spare_faults if spare_faults is not None else SpareFaults()
    problems: list[str] = []
    for label, mapping, lines, spares in (
        ("row", solution.row_map, spare_geometry.base_words, spare_geometry.num_spare_rows),
        ("column", solution.col_map, spare_geometry.word_size, spare_geometry.num_spare_cols),
    ):
        if len(mapping) > spares:
            problems.append(f"{len(mapping)} {label} repairs exceed the {spares} spare {label}s")
        if len(set(mapping.values())) != len(mapping):
            problems.append(f"two {label}s share a spare {label}")
        for line, spare in sorted(mapping.items()):
            if not 0 <= line < lines:
                problems.append(f"repaired {label} {line} is outside the logical array")
            if not 0 <= spare < spares:
                problems.append(f"spare {label} {spare} does not exist")
    for addr, bit in sorted(fail_cells):
        if addr not in solution.row_map and bit not in solution.col_map:
            problems.append(f"faulty cell {(addr, bit)} is not covered")
    used_rows = set(solution.row_map.values())
    used_cols = set(solution.col_map.values())
    for i in sorted(used_rows):
        for bit in sorted(faults.row_cells.get(i, ())):
            if bit not in solution.col_map:
                problems.append(f"spare row {i} is used but its faulty bit {bit} is not column-repaired")
    for k in sorted(used_cols):
        for addr in sorted(faults.col_cells.get(k, ())):
            if addr not in solution.row_map:
                problems.append(f"spare column {k} is used but its faulty row {addr} is not row-repaired")
    for i, k in sorted(faults.corners):
        if i in used_rows and k in used_cols:
            problems.append(f"spare row {i} and spare column {k} are both used but their corner cell is faulty")
    return problems
