"""Pin `algo_rtl_gen`: the `.alg` -> classic-path algorithm table renderer.

Pure Python, no simulator. The behavioural proof that a rendered table matches
the hand-written RTL lives in tests/integration/test_algo_table_equivalence.py.
What is fixed here is the rendering (arm shape, port contract, rejections that
keep the renderer from emitting RTL the existing FSM cannot drive) -- the
direction-RESOLUTION rule itself now lives in alg_spec.py and is pinned in
tests/software/test_alg_spec.py, since algo_rtl_gen is one of several
consumers of that rule, not its owner.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.alg_spec import builtin_algos, parse_alg, resolve_algo, resolve_directions as _alg_spec_resolve_directions  # noqa: E402
from autombist.algo_rtl_gen import (  # noqa: E402
    MAX_RTL_ELEMENTS,
    MAX_RTL_OPS,
    AlgoRtlError,
    render_algo_table,
    resolve_directions,
)


def test_resolve_directions_shim_delegates_to_alg_spec() -> None:
    """algo_rtl_gen.resolve_directions is kept only because it predates the
    canonical function and is part of this module's documented surface (see
    its docstring). Pin that it is a pure delegation, not a second rule --
    which is exactly the bug this whole fix removes."""
    spec = resolve_algo("march_c")
    assert resolve_directions(spec) == _alg_spec_resolve_directions(spec.elements)
    assert resolve_directions(spec) == [0, 0, 0, 1, 1, 1]  # up,up,up,down,down,down


@pytest.mark.parametrize("algo_name", sorted(builtin_algos()))
def test_engine_and_classic_rtl_directions_always_agree(algo_name: str) -> None:
    """THE regression test for the bug this whole fix exists to close: the
    direction sequence the SystemVerilog engines actually run (the DIR column
    of AlgSpec.to_numeric(), written to every .algc file) and the direction
    sequence the classic-path RTL renderer emits (algo_rtl_gen.resolve_directions)
    must always agree, for every built-in algorithm -- not just the three
    narrow enough to render.

    Deliberately does NOT go through alg_spec.resolve_directions directly on
    both sides -- that would just prove the shared function agrees with
    itself. It compares the two SUBSYSTEMS' actual public outputs, exactly as
    they disagreed before this fix: to_numeric() ran every `either` ascending
    while algo_rtl_gen.resolve_directions inherited the previous element's
    direction, and on march_x that was a measured 12/19 vs 13/19 for the same
    algorithm (see docs/source/algo-shell-guide.md). It fails the moment
    either side stops delegating to the shared rule and starts guessing
    again."""
    spec = resolve_algo(algo_name)
    numeric_dirs = [int(line.split()[0]) for line in spec.to_numeric().splitlines()[1:] if line]
    rtl_dirs = resolve_directions(spec)
    assert numeric_dirs == rtl_dirs


# --------------------------------------------------------------------------- #
# Rejections: refuse rather than emit RTL the FSM cannot address.
# --------------------------------------------------------------------------- #
def test_rejects_more_elements_than_the_fsm_can_address() -> None:
    spec = parse_alg("\n".join(["up w0"] * (MAX_RTL_ELEMENTS + 1)) + "\n", "toomany")
    with pytest.raises(AlgoRtlError, match="phase.*\\[2:0\\]"):
        render_algo_table(spec)


def test_rejects_more_ops_per_element_than_the_fsm_can_address() -> None:
    spec = parse_alg("up " + " ".join(["r0"] * (MAX_RTL_OPS + 1)) + "\n", "wide")
    with pytest.raises(AlgoRtlError, match="op_step"):
        render_algo_table(spec)


def test_rejects_wait_ops_outright() -> None:
    """A wait op has no classic-path equivalent at all -- the FSM has no idle
    state -- so this is a different kind of rejection from the width limits."""
    with pytest.raises(AlgoRtlError, match="no idle state"):
        render_algo_table(parse_alg("up w0\neither t5\n", "waity"))


def test_rejects_an_empty_spec() -> None:
    spec = parse_alg("up w0\n", "empty")
    spec.elements.clear()
    with pytest.raises(AlgoRtlError, match="no elements"):
        render_algo_table(spec)


@pytest.mark.parametrize("algo", ["march_ss", "march_b"])
def test_research_only_algorithms_are_rejected_with_the_op_step_reason(algo: str) -> None:
    """March SS (5 ops/element) and March B (6) are exactly why neither can
    reach the classic self-repair path today. Pinning the reason here means the
    boundary moves visibly if the FSM's op_step ever widens, rather than these
    quietly starting to render."""
    with pytest.raises(AlgoRtlError, match="op_step"):
        render_algo_table(resolve_algo(algo))


# --------------------------------------------------------------------------- #
# Rendered text
# --------------------------------------------------------------------------- #
def test_module_name_defaults_to_algo_suffix() -> None:
    assert "module march_c_algo #(" in render_algo_table(resolve_algo("march_c"))


def test_module_name_override() -> None:
    text = render_algo_table(resolve_algo("march_c"), module_name="custom_table")
    assert "module custom_table #(" in text
    assert "module march_c_algo #(" not in text


def test_rendered_table_declares_the_fsm_port_contract() -> None:
    """The rendered module has to drop into the existing FSM's instantiation
    unchanged, so its port list is part of the contract, not an implementation
    detail."""
    text = render_algo_table(resolve_algo("march_c"))
    for port in (
        "input  logic [2:0]            phase",
        "input  logic [1:0]            op_step",
        "output logic                  phase_dir_up",
        "output logic                  do_read",
        "output logic                  do_write",
        "output logic [DATA_WIDTH-1:0] expected_data",
        "output logic [DATA_WIDTH-1:0] write_data",
        "output logic                  last_step",
    ):
        assert port in text, port


def test_rendered_table_is_marked_generated() -> None:
    """It must not look hand-editable: the .alg file is the source of truth."""
    text = render_algo_table(resolve_algo("march_c"))
    assert "GENERATED" in text
    assert "Do not edit by hand" in text


def test_each_element_carries_its_human_form_as_a_comment() -> None:
    text = render_algo_table(resolve_algo("mats_plus"))
    for comment in ("// either w0", "// up r0 w1", "// down r1 w0"):
        assert comment in text, comment
