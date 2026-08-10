"""Render a classic-path ``<algo>_algo.sv`` table from a ``.alg`` spec.

This is the bridge between the project's two subsystems. Research mode parses
``.alg`` files (:mod:`autombist.alg_spec`) and drives a simulation engine; the
classic MBIST path uses hand-written SystemVerilog, one directory per algorithm.
Every algorithm added to the classic self-repair path so far has needed a fresh
hand-written FSM -- which is why ``march_ss``/``march_b`` exist only as ``.alg``
and ``march_raw``/``march_1r1w``/``march_2rw`` only as RTL.

What makes the bridge possible: ``<algo>_algo.sv`` is a PURE COMBINATIONAL
LOOKUP, ``(phase, op_step) -> (phase_dir_up, do_read, do_write, expected_data,
write_data, last_step)``, where ``phase`` is the march element index and
``op_step`` the op index within that element. That is exactly an
:class:`~autombist.alg_spec.AlgSpec`'s shape, so the spec fully determines the
file. The surrounding ``_fsm.sv``/``_top.sv`` are (near-)boilerplate and are NOT
rendered here -- see "Scope" below.

Structured like :mod:`autombist.fault_ram_gen`: pure functions build the per-
element case-arm text, and a thin Jinja template loops over them. Same one-way
dependency -- this imports ``alg_spec`` and nothing imports it back.

Scope: this module renders the algorithm TABLE only. It deliberately does not
touch ``_fsm.sv``, ``_top.sv``, ``copy_mbist_rtl``, or the CLI, so no generated
file is wired into any build and every hardened design keeps its hand-written
RTL. The FSM constraints in :data:`MAX_RTL_ELEMENTS`/:data:`MAX_RTL_OPS` are
what currently keep March SS and March B off the classic path.
"""
from __future__ import annotations

from .alg_spec import WAIT_BASE, AlgSpec
from .alg_spec import resolve_directions as _resolve_directions
from .generator import _render_template

__all__ = [
    "MAX_RTL_ELEMENTS",
    "MAX_RTL_OPS",
    "AlgoRtlError",
    "render_algo_table",
    "resolve_directions",
]

# Limits imposed by the EXISTING hand-written FSM's port widths, not by the .alg
# grammar (which allows MAX_ELEMENTS=16 / MAX_OPS=8): `_fsm.sv` declares
# `phase` as [2:0] and drives `op_step` from a register at most 2 bits wide.
# Rendering past these would produce a table the FSM cannot address, so
# render_algo_table refuses rather than emit silently-unreachable arms.
MAX_RTL_ELEMENTS = 8    # phase is [2:0]
MAX_RTL_OPS = 4         # op_step is [1:0]

_OP_R0, _OP_R1, _OP_W0, _OP_W1 = 0, 1, 2, 3


class AlgoRtlError(ValueError):
    """Raised when an AlgSpec cannot be rendered for the classic RTL path."""


def resolve_directions(spec: AlgSpec) -> list[int]:
    """Shim over :func:`autombist.alg_spec.resolve_directions` -- kept here
    (and re-exported) because it predates that function and is part of this
    module's public/documented surface. See the canonical function for the
    resolution rule and why it is the same one every consumer in the project
    now uses. Returns ``DIR_MAP`` ints (0=up, 1=down), NOT the bools this
    function returned before the rule was unified -- there is no other
    resolve_directions left to disagree with the encoding.
    """
    return _resolve_directions(spec.elements)


def _validate(spec: AlgSpec) -> None:
    if not spec.elements:
        raise AlgoRtlError(f"'{spec.name}' has no elements -- nothing to render")

    if len(spec.elements) > MAX_RTL_ELEMENTS:
        raise AlgoRtlError(
            f"'{spec.name}' has {len(spec.elements)} elements, but the classic-path FSM "
            f"addresses at most {MAX_RTL_ELEMENTS} (its `phase` port is [2:0]). Widening "
            "that is an FSM-side change, not a table-side one"
        )

    for index, element in enumerate(spec.elements):
        if any(op >= WAIT_BASE for op in element.ops):
            raise AlgoRtlError(
                f"'{spec.name}' element {index} contains a wait op -- the classic-path "
                "FSM has no idle state, so elapsed-time ops (t<N>) have no RTL "
                "equivalent at all. Wait ops are research-mode only"
            )
        if len(element.ops) > MAX_RTL_OPS:
            raise AlgoRtlError(
                f"'{spec.name}' element {index} has {len(element.ops)} ops, but the "
                f"classic-path FSM addresses at most {MAX_RTL_OPS} (its `op_step` "
                "register is 2 bits). Widening that is an FSM-side change, not a "
                "table-side one"
            )


def _op_body(op: int, is_last: bool, indent: str) -> str:
    """The assignment block for one op. Only the signals this op actually drives
    are assigned; the rest fall through to the always_comb defaults, matching
    the hand-written tables."""
    last = "1'b1" if is_last else "1'b0"
    if op in (_OP_R0, _OP_R1):
        value = "ALL_ZERO" if op == _OP_R0 else "ALL_ONE"
        lines = [
            f"do_read       = 1'b1;",
            f"expected_data = {value};",
            f"last_step     = {last};",
        ]
    else:
        value = "ALL_ZERO" if op == _OP_W0 else "ALL_ONE"
        lines = [
            f"do_write     = 1'b1;",
            f"write_data   = {value};",
            f"last_step    = {last};",
        ]
    return "\n".join(indent + line for line in lines)


def _element_arm(index: int, element, direction: int, comment: str) -> str:
    """One `case (phase)` arm. Single-op elements assign flat; multi-op elements
    nest a `case (op_step)`, the shape march_raw_algo.sv already uses -- emitted
    uniformly for every multi-op element rather than matching each hand-written
    file's own idiom, since byte-identity with those files is not a goal (they
    differ from each other in comment style and line endings).

    ``direction`` is a resolved ``DIR_MAP`` int (0=up, 1=down) -- never 2 --
    compared by value rather than by truthiness so that a 0/1 encoding can
    never silently invert (0 is falsy, which is exactly the trap: an `if
    direction` check would treat "up" as "down")."""
    dir_lit = "1'b1" if direction == 0 else "1'b0"
    head = f"            3'd{index}: begin  // {comment}\n"

    if len(element.ops) == 1:
        body = _op_body(element.ops[0], is_last=True, indent=" " * 16)
        return head + f"                phase_dir_up = {dir_lit};\n" + body + "\n            end"

    arms = [f"                phase_dir_up = {dir_lit};", "                case (op_step)"]
    last_index = len(element.ops) - 1
    for op_index, op in enumerate(element.ops):
        is_last = op_index == last_index
        label = "default" if is_last else f"2'd{op_index}"
        arms.append(f"                    {label}: begin")
        arms.append(_op_body(op, is_last=is_last, indent=" " * 24))
        arms.append("                    end")
    arms.append("                endcase")
    return head + "\n".join(arms) + "\n            end"


def render_algo_table(spec: AlgSpec, module_name: str | None = None) -> str:
    """Render ``<module_name>.sv``'s combinational algorithm table from ``spec``.

    ``module_name`` defaults to ``<spec.name>_algo``, matching the classic path's
    naming convention. Raises :class:`AlgoRtlError` when the spec exceeds what
    the existing FSM can drive (see :func:`_validate`).
    """
    _validate(spec)
    module_name = module_name or f"{spec.name}_algo"
    directions = resolve_directions(spec)

    arms = [
        _element_arm(index, element, directions[index], element.human())
        for index, element in enumerate(spec.elements)
    ]
    return _render_template(
        {
            "module_name": module_name,
            "algo_name": spec.name,
            "length_n": spec.length_n,
            "element_count": len(spec.elements),
            "arms": arms,
        },
        "algo_table_template.sv.j2",
    )
