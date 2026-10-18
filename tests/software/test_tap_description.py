"""The ICL and BSDL wrap-test-access writes, and the TAP facts it records.

Built on warptap's pure-Python network model (no Yosys), so these run wherever a
warptap with BSDL support is installed. The real-RTL side -- the BSDL's opcode
against the vectors, the IDCODE read back from the wrapped netlist -- is
tests/integration/test_bsdl_e2e.py.
"""
from __future__ import annotations

import re
import sys

import pytest
import typer

from autombist.cli import _parse_idcode
from autombist.testaccess import (
    DEFAULT_TCK_MAX_FREQ_HZ,
    TestAccessUnavailable,
    bsdl_entity_candidates,
    build_instrument_specs,
    classify_test_access_ports,
    describe_test_access_tap,
    tap_facts,
    wrap_test_access,
)

CUSTOM_IDCODE = 0x5CA1AB1F


def _network(top: str = "x_ctrl"):
    pytest.importorskip("warptap.bsdl_emit", reason="needs a warptap release with BSDL emission")
    from warptap.sib_plan import build_sib_plan

    return build_sib_plan(build_instrument_specs(classify_test_access_ports()), top_name=top)


def _needs_idcode_support() -> None:
    tap_model = pytest.importorskip("warptap.tap_model")
    if not hasattr(tap_model, "idcode_value_error"):
        pytest.skip("this warptap cannot set a TAP IDCODE")


def _entity(bsdl: str) -> str:
    return re.search(r"^entity (\w+) is", bsdl, re.MULTILINE).group(1)


def _opcode(bsdl: str, instruction: str) -> str:
    return re.search(rf'"{instruction}\s+\(([01]+)\)', bsdl).group(1)


def _idcode(bsdl: str) -> int:
    body = re.search(r"attribute IDCODE_REGISTER of \w+ : entity is(.*?);", bsdl, re.DOTALL).group(1)
    return int("".join(re.findall(r'"([01]+)"', body)), 2)


@pytest.mark.parametrize(("top", "expected"), [
    ("ok_name", ["ok_name", "ok_name_tap"]),
    ("a__b", ["a__b", "a_b", "a_b_tap"]),
    ("9lives", ["9lives", "tap_9lives", "tap_9lives_tap"]),
    ("$", ["$", "tap", "tap_tap"]),
    ("trail_", ["trail_", "trail", "trail_tap"]),
])
def test_bsdl_entity_candidates_turn_a_module_name_into_vhdl_identifiers(top: str, expected: list[str]) -> None:
    assert bsdl_entity_candidates(top) == expected


def test_the_access_link_names_the_instruction_the_bsdl_declares() -> None:
    graph, root = _network("x_ctrl")  # skips here, before any warptap import, when it is absent
    from warptap.bsdl_emit import NETWORK_ACCESS_BSDL_INSTRUCTION
    from warptap.tap_model import DEFAULT_IR_WIDTH, OPCODE_EXTEST

    described = describe_test_access_tap(graph, root)

    assert described.entity == _entity(described.bsdl) == "x_ctrl"
    assert re.search(
        r"AccessLink warptap_tap Of STD_1149_1_2001 \{\s*BSDLEntity x_ctrl;\s*"
        rf"{NETWORK_ACCESS_BSDL_INSTRUCTION} \{{ ScanInterface \{{ \w+; \}} \}}\s*\}}",
        described.icl,
    )
    assert NETWORK_ACCESS_BSDL_INSTRUCTION == "EXTEST"
    assert _opcode(described.bsdl, "EXTEST") == format(OPCODE_EXTEST, f"0{DEFAULT_IR_WIDTH}b")
    assert f"INSTRUCTION_LENGTH of x_ctrl : entity is {DEFAULT_IR_WIDTH};" in described.bsdl


def test_the_tck_limit_defaults_to_the_stated_assumption_and_can_be_set() -> None:
    graph, root = _network()

    default = describe_test_access_tap(graph, root)
    fast = describe_test_access_tap(graph, root, tck_max_freq_hz=25e6)

    assert DEFAULT_TCK_MAX_FREQ_HZ == 10e6
    assert default.tck_max_freq_hz == 10e6 and "1.000000e+07, BOTH" in default.bsdl
    assert fast.tck_max_freq_hz == 25e6 and "2.500000e+07, BOTH" in fast.bsdl


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, "10"])
def test_an_unusable_tck_limit_is_refused(bad) -> None:
    graph, root = _network()
    with pytest.raises(ValueError, match="maximum frequency"):
        describe_test_access_tap(graph, root, tck_max_freq_hz=bad)


@pytest.mark.parametrize(("top", "entity"), [("a__b", "a_b"), ("select", "select_tap"), ("9lives", "tap_9lives")])
def test_a_module_name_that_is_not_a_bsdl_entity_gets_one_the_icl_agrees_with(top: str, entity: str) -> None:
    graph, root = _network(top)

    described = describe_test_access_tap(graph, root)

    assert described.entity == entity == _entity(described.bsdl)
    assert f"BSDLEntity {entity};" in described.icl


def test_the_bsdl_states_the_idcode_the_hardware_was_built_with() -> None:
    _needs_idcode_support()
    from warptap.tap_model import IDCODE_VALUE

    graph, root = _network()

    default = describe_test_access_tap(graph, root)
    custom = describe_test_access_tap(graph, root, idcode_value=CUSTOM_IDCODE)

    assert _idcode(default.bsdl) == IDCODE_VALUE
    assert _idcode(custom.bsdl) == CUSTOM_IDCODE


@pytest.mark.parametrize("bad", [0x1A5A5002, 1 << 32, -1, True, "0x5CA1AB1F"])
def test_a_bad_idcode_is_refused_before_anything_is_rendered_or_ingested(bad) -> None:
    _needs_idcode_support()
    graph, root = _network()

    with pytest.raises(ValueError, match="IDCODE"):
        describe_test_access_tap(graph, root, idcode_value=bad)
    # wrap_test_access checks it first: no source is read (a missing file would raise
    # FileNotFoundError, or a Yosys error, first)
    with pytest.raises(ValueError, match="IDCODE"):
        wrap_test_access(["/no/such/file.v"], "top", idcode_value=bad)


def test_the_tap_facts_come_from_warptaps_own_constants() -> None:
    graph, root = _network("x_ctrl")  # skips here, before any warptap import, when it is absent
    from warptap.tap_model import DEFAULT_IR_WIDTH, IDCODE_VALUE, OPCODE_EXTEST

    described = describe_test_access_tap(graph, root, tck_max_freq_hz=25e6)

    plain = tap_facts()
    with_bsdl = tap_facts(description=described)

    assert plain == {
        "idcode": f"0x{IDCODE_VALUE:08X}",
        "idcode_is_placeholder": True,
        "instruction_length": DEFAULT_IR_WIDTH,
        "network_access_instruction": "EXTEST",
        "network_access_opcode": format(OPCODE_EXTEST, f"0{DEFAULT_IR_WIDTH}b"),
    }
    assert with_bsdl == {**plain, "bsdl_entity": "x_ctrl", "tck_max_freq_hz": 25e6}
    # the instruction the manifest names is the one the BSDL declares and the ICL links
    assert _opcode(described.bsdl, plain["network_access_instruction"]) == plain["network_access_opcode"]

    custom = tap_facts(idcode_value=CUSTOM_IDCODE)
    assert custom["idcode"] == "0x5CA1AB1F" and custom["idcode_is_placeholder"] is False


def test_parse_idcode_takes_hex_and_decimal_and_refuses_the_rest() -> None:
    assert _parse_idcode("0x5CA1AB1F") == CUSTOM_IDCODE
    assert _parse_idcode(str(CUSTOM_IDCODE)) == CUSTOM_IDCODE
    with pytest.raises(typer.Exit):
        _parse_idcode("zzz")


def test_a_warptap_without_bsdl_support_is_refused_with_an_upgrade_message(monkeypatch) -> None:
    graph, root = _network()
    monkeypatch.setitem(sys.modules, "warptap.bsdl_emit", None)  # `import` now raises ImportError

    with pytest.raises(TestAccessUnavailable, match="upgrade warptap"):
        describe_test_access_tap(graph, root)


def test_a_warptap_without_idcode_support_is_refused_with_an_upgrade_message(monkeypatch) -> None:
    tap_model = pytest.importorskip("warptap.tap_model")
    monkeypatch.delattr(tap_model, "idcode_value_error", raising=False)

    with pytest.raises(TestAccessUnavailable, match="IDCODE"):
        wrap_test_access(["/no/such/file.v"], "top", idcode_value=CUSTOM_IDCODE)
