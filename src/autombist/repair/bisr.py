"""BISR -- Built-In Self-Repair (the pure signature-encoding function).

``encode_row_repair(solution, spare_geometry) -> RepairSignature`` translates
BIRA's abstract verdict (which spare replaces which faulty row) into the exact
packed integers ``rtl/repair_remap_row.sv`` expects on its
``row_repair_en``/``faulty_row_addr`` ports. Like ``bira.analyze``, this is a
PURE function: it imports only ``.types``, touches no simulator/generator/
runner state, and is fully testable standalone.

For the tester-driven MVP, "BISR" is genuinely this thin: Step A's remap takes
its repair config directly on combinational input pins (no serial scan chain,
no repair registers, no clock) -- so there is no boot-sequencing race to solve
yet (that only becomes real once the config is loaded through a clocked serial
chain, a later phase). The encoding itself is the one thing that has to be
exactly right, since it is the sole bridge between BIRA's abstract answer and
the physical bits a real repair-loaded memory reads.
"""
from __future__ import annotations

from .types import RepairSignature, RepairSolution, SpareGeometry

__all__ = ["encode_row_repair"]


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
    """
    if not isinstance(solution, RepairSolution):
        raise TypeError(
            "encode_row_repair requires a RepairSolution (BIRA found a covering "
            f"repair); got {type(solution).__name__} -- check "
            "isinstance(result, RepairSolution) before encoding, since an "
            "Unrepairable verdict has no signature to apply"
        )

    addr_width = spare_geometry.addr_width
    row_repair_en = 0
    faulty_row_addr = 0
    for faulty_addr, spare_index in solution.row_map.items():
        row_repair_en |= 1 << spare_index
        faulty_row_addr |= faulty_addr << (spare_index * addr_width)

    return RepairSignature(row_repair_en=row_repair_en, faulty_row_addr=faulty_row_addr)
