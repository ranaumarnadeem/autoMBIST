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

from .types import AnalysisResult, RepairSolution, SpareGeometry, Unrepairable

__all__ = ["analyze"]


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
