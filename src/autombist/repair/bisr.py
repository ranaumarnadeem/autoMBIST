"""BISR -- Built-In Self-Repair (the pure signature-encoding functions).

``encode_repair(solution, spare_geometry) -> RepairSignature`` translates BIRA's
abstract verdict (which spare replaces which faulty row/column) into the exact
packed integers ``rtl/repair_remap_row.sv`` and ``rtl/repair_remap_col.sv``
expect on their ``row_repair_en``/``faulty_row_addr`` and
``col_repair_en``/``faulty_bit`` ports. ``encode_row_repair`` is the row-only
predecessor, kept for the many row-only callers -- it now REFUSES a geometry or
solution carrying columns rather than silently emitting half a repair.

Like ``bira.analyze``, both are PURE functions: they import only ``.types``,
touch no simulator/generator/runner state, and are fully testable standalone.

For the tester-driven path, "BISR" is genuinely this thin: the remaps take their
repair config directly on combinational input pins (no serial scan chain, no
repair registers, no clock) -- so there is no boot-sequencing race to solve yet
(that only becomes real once the config is loaded through a clocked serial
chain, a later phase). The encoding itself is the one thing that has to be
exactly right, since it is the sole bridge between BIRA's abstract answer and
the physical bits a real repair-loaded memory reads.
"""
from __future__ import annotations

from .types import RepairSignature, RepairSolution, SpareGeometry

__all__ = ["encode_repair", "encode_row_repair"]


def _require_solution(solution: object, fn_name: str) -> None:
    """Shared guard: only a :class:`RepairSolution` can be encoded.

    Deliberate, not an oversight: there is no meaningful signature to encode when
    BIRA found no covering allocation, and a caller that got this far without
    checking ``isinstance(result, RepairSolution)`` first has a real bug -- one
    surfaced immediately rather than silently encoding garbage that would apply
    *some* repair, just not a correct one.
    """
    if not isinstance(solution, RepairSolution):
        raise TypeError(
            f"{fn_name} requires a RepairSolution (BIRA found a covering "
            f"repair); got {type(solution).__name__} -- check "
            "isinstance(result, RepairSolution) before encoding, since an "
            "Unrepairable verdict has no signature to apply"
        )


def encode_repair(
    solution: RepairSolution,
    spare_geometry: SpareGeometry,
) -> RepairSignature:
    """Pack BOTH halves of ``solution`` into the remaps' exact bit layouts.

    The row half is identical to :func:`encode_row_repair`. The column half sets
    ``col_repair_en`` bit ``k`` iff spare column ``k`` is in use, and packs each
    spare's assigned LOGICAL bit index into its own
    ``spare_geometry.bit_index_width``-bit slice at offset
    ``k * bit_index_width`` -- matching ``repair_remap_col.sv``'s
    ``faulty_bit[k*BIT_IDX_WIDTH +: BIT_IDX_WIDTH]`` slicing exactly, the same
    discipline the row half already applies. An unused spare's slice is left at 0
    (don't-care: its ``col_repair_en`` bit is 0, so the mux never selects it).

    This is the encoder to use for any 2D geometry. ``encode_row_repair`` stays
    available for row-only callers and rejects a 2D one outright.
    """
    _require_solution(solution, "encode_repair")

    addr_width = spare_geometry.addr_width
    row_repair_en = 0
    faulty_row_addr = 0
    for faulty_addr, spare_index in solution.row_map.items():
        row_repair_en |= 1 << spare_index
        faulty_row_addr |= faulty_addr << (spare_index * addr_width)

    bit_width = spare_geometry.bit_index_width
    col_repair_en = 0
    faulty_bit = 0
    for faulty_bit_index, spare_index in solution.col_map.items():
        col_repair_en |= 1 << spare_index
        faulty_bit |= faulty_bit_index << (spare_index * bit_width)

    return RepairSignature(
        row_repair_en=row_repair_en,
        faulty_row_addr=faulty_row_addr,
        col_repair_en=col_repair_en,
        faulty_bit=faulty_bit,
    )


def encode_row_repair(
    solution: RepairSolution,
    spare_geometry: SpareGeometry,
) -> RepairSignature:
    """Pack ``solution.row_map`` into ``repair_remap_row.sv``'s exact bit layout.

    ``row_repair_en`` sets bit ``i`` iff spare row ``i`` is in use.
    ``faulty_row_addr`` packs each spare's assigned faulty address into its own
    ``spare_geometry.addr_width``-bit slice, at bit offset ``i * addr_width``
    (matching the RTL's ``faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH]`` slicing
    exactly) -- an unused spare's slice is left at 0 (don't-care: its
    ``row_repair_en`` bit is 0, so the comparator never looks at it).

    Only accepts a :class:`RepairSolution` -- raises ``TypeError`` for an
    :class:`Unrepairable` (or anything else). This is deliberate, not an
    oversight: there is no meaningful signature to encode when BIRA found no
    covering allocation, and a caller that got this far without checking
    ``isinstance(result, RepairSolution)`` first has a real bug -- one this
    function surfaces immediately rather than silently encoding garbage that
    would apply *some* repair, just not a correct one.

    Also refuses a geometry or solution carrying spare COLUMNS, raising
    ``ValueError``: encoding only the row half of a 2D repair would silently
    apply a partial repair, exactly the failure mode the ``Unrepairable`` guard
    above already refuses. Use :func:`encode_repair` for any 2D geometry.
    """
    _require_solution(solution, "encode_row_repair")

    if spare_geometry.num_spare_cols > 0 or solution.col_map:
        raise ValueError(
            "encode_row_repair is the ROW-ONLY encoder, but this geometry/solution "
            f"has spare columns (num_spare_cols={spare_geometry.num_spare_cols}, "
            f"{len(solution.col_map)} column repair(s)) -- use encode_repair(), "
            "which packs both halves. Encoding only the row half here would "
            "silently apply a partial repair."
        )

    addr_width = spare_geometry.addr_width
    row_repair_en = 0
    faulty_row_addr = 0
    for faulty_addr, spare_index in solution.row_map.items():
        row_repair_en |= 1 << spare_index
        faulty_row_addr |= faulty_addr << (spare_index * addr_width)

    return RepairSignature(row_repair_en=row_repair_en, faulty_row_addr=faulty_row_addr)
