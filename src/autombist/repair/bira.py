"""BIRA -- Built-In Redundancy Analysis (the pure allocation function).

``analyze(fail_cells, spare_geometry) -> RepairSolution | Unrepairable`` is a PURE
function: it imports only ``.types`` and takes plain data (a ``set[(addr, bit)]``
fail map + a ``SpareGeometry``). It never touches the simulator, the generator, or
the runner -- which is what makes this whole subpackage extractable. The fail map
it consumes is exactly what ``autombist.bira_input.fail_cells(report)`` produces,
but ``analyze`` does NOT import that adapter: it depends on the *shape* (a set of
integer ``(addr, bit)`` pairs), not the code.

This phase implements row-only (1D) allocation: a spare row replaces an ENTIRE
faulty row, so a memory is repairable iff the number of DISTINCT faulty row
addresses does not exceed the available spare rows. (2D row+column allocation --
must-repair + branch-and-bound -- is a later phase.)
"""
from __future__ import annotations

from .types import AnalysisResult, RepairSolution, SpareGeometry, Unrepairable

__all__ = ["analyze"]


def analyze(
    fail_cells: set[tuple[int, int]],
    spare_geometry: SpareGeometry,
) -> AnalysisResult:
    """Compute a row-only repair for the observed failing cells.

    ``fail_cells`` is the set of ``(addr, bit)`` coordinates the BIST observed
    failing (e.g. from ``autombist.bira_input.fail_cells``). A row-only repair
    replaces the whole word at a faulty address, so only the distinct addresses
    matter -- the ``bit`` half is irrelevant to which rows must be repaired.

    Returns a :class:`RepairSolution` (mapping each faulty row address to a spare
    index, assigned deterministically by ascending address) when the distinct
    faulty rows fit the spare budget, else :class:`Unrepairable`. An empty
    ``fail_cells`` yields an empty ``RepairSolution`` ("nothing to repair") --
    which the ``0 <= N`` inequality gives for free, not as a special case.

    Raises ``ValueError`` if any address is outside ``[0, base_words)`` -- that
    means the caller fed a fail map inconsistent with the stated geometry.
    """
    faulty_rows = sorted({addr for addr, _bit in fail_cells})

    for addr in faulty_rows:
        if addr < 0 or addr >= spare_geometry.base_words:
            raise ValueError(
                f"fail_cells row address {addr} is outside the logical address "
                f"space [0, {spare_geometry.base_words}) of the given geometry"
            )

    if len(faulty_rows) <= spare_geometry.num_spare_rows:
        return RepairSolution(
            row_map={row: index for index, row in enumerate(faulty_rows)}
        )
    return Unrepairable(
        faulty_rows=tuple(faulty_rows),
        num_spare_rows=spare_geometry.num_spare_rows,
    )
