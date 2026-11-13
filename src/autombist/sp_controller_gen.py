"""Render the single-port algorithms' ``<algo>_fsm.sv`` and ``<algo>_top.sv``.

The five single-port algorithms share one controller (#65): the FSM walks an
algorithm's ``<algo>_algo.sv`` table, a pure ``(phase, op_step) -> ops`` lookup,
over every address, and the top only renames its pins to the OpenRAM ones. The
copies in ``rtl/`` differed only in module names and in four facts about the
table, which drifted when one copy was edited and the others were not.

So ``templates/sp_controller_fsm.sv.j2`` and ``templates/sp_controller_top.sv.j2``
are the one source, and each ``rtl/<algo>/<algo>_{fsm,top}.sv`` is rendered from
them. Of the four facts, the phase count and the direction of each phase are read
from the table's case arms, and so is whether the table takes ``addr_lsb``. The
fourth, the op-step register's width, is :data:`SINGLE_PORT_ALGOS`: one bit is
enough for an algorithm whose longest element has two ops, and a wider register
is one more flop.

The rendered files stay checked in, so every build, source list and manifest
that names them is unchanged; ``tests/software/test_sp_controller_gen.py`` fails
while one differs from its rendering. Re-render after editing a template or a
table with::

    AUTOMBIST_UPDATE_GOLDEN=1 python -m pytest tests/software/test_sp_controller_gen.py

The multi-port controllers (march_1r1w, march_2rw) are a different FSM and are
not rendered here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .generator import _render_template

__all__ = [
    "SINGLE_PORT_ALGOS",
    "ControllerShape",
    "ControllerShapeError",
    "read_table_shape",
    "render_controller",
    "render_controllers",
]

#: Single-port algorithm (its rtl/ directory) -> op-step register width in bits.
#: The table's ``op_step`` port is always [1:0]; march_raw's three-op elements
#: need two bits, every other algorithm's elements have at most two ops.
SINGLE_PORT_ALGOS: dict[str, int] = {
    "march_c": 1,
    "march_raw": 2,
    "march_x": 1,
    "mats_plus": 1,
    "checkerboard": 1,
}

# A phase arm and the phase_dir_up it sets first, e.g.
#     3'd2: begin  // up r1 w0
#         phase_dir_up = 1'b1;
_PHASE_ARM = re.compile(
    r"^\s*3'd(\d+)\s*:\s*begin\b[^\n]*\n\s*phase_dir_up\s*=\s*1'b([01])\s*;",
    re.MULTILINE,
)
_ADDR_LSB_PORT = re.compile(r"^\s*input\s+logic\s+addr_lsb\b", re.MULTILINE)


class ControllerShapeError(ValueError):
    """Raised when an algorithm table is not one the shared FSM can walk."""


@dataclass(frozen=True)
class ControllerShape:
    """What the shared FSM needs to know about one algorithm's table."""

    algo: str
    directions_up: tuple[bool, ...]
    addr_lsb: bool
    op_step_bits: int

    @property
    def last_phase(self) -> int:
        return len(self.directions_up) - 1

    @property
    def last_up_phase(self) -> int:
        return sum(self.directions_up) - 1


def read_table_shape(algo: str, table_sv: str, op_step_bits: int) -> ControllerShape:
    """Read the phase directions and the ``addr_lsb`` port from an
    ``<algo>_algo.sv`` table's text.

    The FSM decides where the next phase starts with a threshold
    (``phase <= LAST_UP``) and starts phase 0 at address 0, so the table must
    ascend first and then descend; anything else is refused.
    """
    arms = [(int(index), bit == "1") for index, bit in _PHASE_ARM.findall(table_sv)]
    if not arms:
        raise ControllerShapeError(f"{algo}_algo.sv: no `3'dN: begin` arm setting phase_dir_up found")
    indices = [index for index, _ in arms]
    if indices != list(range(len(arms))):
        raise ControllerShapeError(
            f"{algo}_algo.sv: phase arms are {indices}, not 0..{len(arms) - 1} in order"
        )
    if len(arms) > 8:
        raise ControllerShapeError(f"{algo}_algo.sv: {len(arms)} phases, but the FSM's phase is [2:0]")
    directions = tuple(up for _, up in arms)
    ups = sum(directions)
    if ups == 0 or directions != (True,) * ups + (False,) * (len(directions) - ups):
        raise ControllerShapeError(
            f"{algo}_algo.sv: phase directions {['up' if d else 'down' for d in directions]} are not "
            "ascending phases followed by descending ones, which is all the shared FSM's "
            "next-phase lookahead and its start at address 0 can walk"
        )
    if op_step_bits not in (1, 2):
        raise ControllerShapeError(f"{algo}: op_step_bits must be 1 or 2 (the table's op_step is [1:0])")
    return ControllerShape(
        algo=algo,
        directions_up=directions,
        addr_lsb=bool(_ADDR_LSB_PORT.search(table_sv)),
        op_step_bits=op_step_bits,
    )


def render_controller(shape: ControllerShape) -> dict[str, str]:
    """The ``fsm`` and ``top`` files' text for one algorithm."""
    context = {
        "algo": shape.algo,
        "phase_count": len(shape.directions_up),
        "last_phase": shape.last_phase,
        "last_up_phase": shape.last_up_phase,
        "op_step_bits": shape.op_step_bits,
        "addr_lsb": shape.addr_lsb,
    }
    return {
        "fsm": _render_template(context, "sp_controller_fsm.sv.j2"),
        "top": _render_template(context, "sp_controller_top.sv.j2"),
    }


def render_controllers(rtl_dir: Path) -> dict[Path, str]:
    """Every single-port ``_fsm.sv``/``_top.sv`` under ``rtl_dir`` -> its text,
    rendered from that directory's own ``_algo.sv`` table."""
    rendered: dict[Path, str] = {}
    for algo, op_step_bits in SINGLE_PORT_ALGOS.items():
        directory = Path(rtl_dir) / algo
        table = (directory / f"{algo}_algo.sv").read_text(encoding="utf-8")
        files = render_controller(read_table_shape(algo, table, op_step_bits))
        for kind, text in files.items():
            rendered[directory / f"{algo}_{kind}.sv"] = text
    return rendered
