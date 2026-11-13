"""The single-port controllers in rtl/ are renderings of one template (#65).

Each rtl/<algo>/<algo>_fsm.sv and _top.sv for a single-port algorithm must be
exactly what autombist.sp_controller_gen renders from the template and that
directory's own _algo.sv table. After editing the template or a table,
re-render them and review the diff:

    AUTOMBIST_UPDATE_GOLDEN=1 python -m pytest tests/software/test_sp_controller_gen.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from autombist.generator import _MULTI_PORT_ALGOS, RTL_ALGORITHMS
from autombist.sp_controller_gen import (
    SINGLE_PORT_ALGOS,
    ControllerShapeError,
    read_table_shape,
    render_controllers,
)

RTL_DIR = Path(__file__).resolve().parents[2] / "rtl"
UPDATE = os.environ.get("AUTOMBIST_UPDATE_GOLDEN") == "1"


def test_checked_in_controllers_are_the_rendering() -> None:
    rendered = render_controllers(RTL_DIR)
    if UPDATE:
        for path, text in rendered.items():
            path.write_text(text, encoding="utf-8", newline="\n")
    stale = [
        str(path.relative_to(RTL_DIR))
        for path, text in rendered.items()
        if path.read_text(encoding="utf-8") != text
    ]
    assert not stale, (
        f"{stale} differ from what sp_controller_gen renders. Edit "
        "src/autombist/templates/sp_controller_{fsm,top}.sv.j2, not the files, then re-render: "
        "AUTOMBIST_UPDATE_GOLDEN=1 python -m pytest tests/software/test_sp_controller_gen.py"
    )


def test_every_rtl_algorithm_is_single_port_or_multi_port() -> None:
    single = {name for name, directory in RTL_ALGORITHMS.items() if directory in SINGLE_PORT_ALGOS}
    assert single | _MULTI_PORT_ALGOS == set(RTL_ALGORITHMS)
    assert not single & _MULTI_PORT_ALGOS


@pytest.mark.parametrize(
    ("algo", "directions", "addr_lsb"),
    [
        ("march_c", (True, True, True, False, False, False), False),
        ("march_raw", (True, True, True, False, False, False), False),
        ("march_x", (True, True, False, False), False),
        ("mats_plus", (True, True, False), False),
        ("checkerboard", (True, True, False, False), True),
    ],
)
def test_table_shapes_read_from_the_algo_tables(algo: str, directions: tuple, addr_lsb: bool) -> None:
    table = (RTL_DIR / algo / f"{algo}_algo.sv").read_text(encoding="utf-8")
    shape = read_table_shape(algo, table, SINGLE_PORT_ALGOS[algo])
    assert shape.directions_up == directions
    assert shape.addr_lsb is addr_lsb


def _table(*directions: str) -> str:
    arms = "".join(
        f"        3'd{i}: begin\n            phase_dir_up = 1'b{d};\n        end\n"
        for i, d in enumerate(directions)
    )
    return f"module t_algo (\n    input  logic [2:0] phase\n);\n    case (phase)\n{arms}    endcase\n"


def test_a_down_phase_before_an_up_phase_is_refused() -> None:
    with pytest.raises(ControllerShapeError, match="not ascending phases followed by descending"):
        read_table_shape("t", _table("1", "0", "1"), 1)


def test_a_table_starting_down_is_refused() -> None:
    with pytest.raises(ControllerShapeError, match="not ascending"):
        read_table_shape("t", _table("0", "0"), 1)


def test_out_of_order_arms_are_refused() -> None:
    text = _table("1", "1").replace("3'd1:", "3'd2:")
    with pytest.raises(ControllerShapeError, match="not 0..1 in order"):
        read_table_shape("t", text, 1)


def test_a_table_without_phase_arms_is_refused() -> None:
    with pytest.raises(ControllerShapeError, match="no `3'dN: begin` arm"):
        read_table_shape("t", "module t_algo; endmodule\n", 1)


def test_all_up_table_renders_a_threshold_at_the_last_phase() -> None:
    shape = read_table_shape("t", _table("1", "1"), 1)
    assert shape.last_phase == shape.last_up_phase == 1
