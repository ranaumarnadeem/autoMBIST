"""autoMBIST's own logic around warptap's BSDL/IDCODE API, tested against stand-ins.

warptap is an optional dependency that CI's environment does not have, so the code that
wraps it (entity-name fallback, error mapping, IDCODE passthrough, the manifest's TAP
facts, the CLI's validate-before-writing order) is tested here with fakes for the few
warptap names it calls. These do not replace the tests against the real warptap
(test_tap_description.py, tests/integration/test_bsdl_e2e.py): they pin what autoMBIST
does with whatever warptap returns, and they run with or without warptap installed.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from autombist import testaccess
from autombist.cli import app
from autombist.testaccess import (
    DEFAULT_TCK_MAX_FREQ_HZ,
    TapDescription,
    TestAccessUnavailable,
    describe_test_access_tap,
    tap_facts,
    wrap_test_access,
)

runner = CliRunner()
PLACEHOLDER = 0x1A5A5003
CUSTOM = 0x5CA1AB1F


class FakeBsdlEmitError(Exception):
    pass


@pytest.fixture
def fake_warptap(monkeypatch):
    """Stand-ins for the warptap names autoMBIST calls, plus a note of each call."""
    calls = SimpleNamespace(to_bsdl=[], to_icl=[])

    def to_bsdl(entity, *, tck_max_freq_hz, **extra):
        calls.to_bsdl.append((entity, tck_max_freq_hz, extra))
        if entity in {"a__b", "select"}:  # not valid BSDL entity names
            raise FakeBsdlEmitError(f"{entity!r} is not a valid entity name")
        return f"BSDL {entity} {tck_max_freq_hz} {extra}"

    def to_icl(graph, root, *, include_access_link, bsdl_entity_name):
        calls.to_icl.append((root.name, include_access_link, bsdl_entity_name))
        return f"ICL {root.name} link={include_access_link} entity={bsdl_entity_name}"

    def idcode_value_error(value):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 1 << 32:
            return f"IDCODE value {value!r} is not a 32-bit value"
        return None if value & 1 else f"IDCODE value {value:#010x} has bit 0 clear"

    modules = {
        "warptap": types.ModuleType("warptap"),
        "warptap.bsdl_emit": types.SimpleNamespace(BsdlEmitError=FakeBsdlEmitError, to_bsdl=to_bsdl),
        "warptap.icl_emit": types.SimpleNamespace(to_icl=to_icl),
        "warptap.tap_model": types.SimpleNamespace(
            idcode_value_error=idcode_value_error, DEFAULT_IR_WIDTH=4, IDCODE_VALUE=PLACEHOLDER,
            OPCODE_EXTEST=0,
        ),
    }
    modules["warptap"].__path__ = []
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    # `_require_warptap` passes when the insertion entry point imported
    monkeypatch.setattr(testaccess, "_warptap_insert_test_access", lambda *a, **k: None, raising=False)
    return calls


def _root(name: str = "x_ctrl") -> SimpleNamespace:
    return SimpleNamespace(name=name)


# --- describe_test_access_tap --------------------------------------------------

def test_describe_renders_the_icl_and_bsdl_under_one_entity(fake_warptap) -> None:
    described = describe_test_access_tap(object(), _root("x_ctrl"))

    assert described == TapDescription(
        icl="ICL x_ctrl link=True entity=x_ctrl",
        bsdl=f"BSDL x_ctrl {DEFAULT_TCK_MAX_FREQ_HZ} {{}}",
        entity="x_ctrl",
        tck_max_freq_hz=DEFAULT_TCK_MAX_FREQ_HZ,
    )
    # no idcode keyword at all when none was asked for: an older warptap lacks it
    assert fake_warptap.to_bsdl == [("x_ctrl", DEFAULT_TCK_MAX_FREQ_HZ, {})]


def test_describe_passes_the_tck_limit_and_idcode_through(fake_warptap) -> None:
    described = describe_test_access_tap(object(), _root(), tck_max_freq_hz=25, idcode_value=CUSTOM)

    assert described.tck_max_freq_hz == 25.0 and isinstance(described.tck_max_freq_hz, float)
    assert fake_warptap.to_bsdl == [("x_ctrl", 25.0, {"idcode_value": CUSTOM})]


@pytest.mark.parametrize(("top", "entity", "tried"), [
    ("a__b", "a_b", ["a__b", "a_b"]),
    ("select", "select_tap", ["select", "select_tap"]),
    ("ok", "ok", ["ok"]),
])
def test_describe_falls_back_to_a_derived_entity_name_and_the_icl_follows(
    fake_warptap, top: str, entity: str, tried: list[str]
) -> None:
    described = describe_test_access_tap(object(), _root(top))

    assert described.entity == entity
    assert [t[0] for t in fake_warptap.to_bsdl] == tried
    assert fake_warptap.to_icl == [(top, True, entity)]


def test_describe_gives_up_with_the_last_reason_when_no_entity_name_works(fake_warptap, monkeypatch) -> None:
    monkeypatch.setattr(testaccess, "bsdl_entity_candidates", lambda top: ["a__b", "select"])

    with pytest.raises(ValueError, match="no BSDL entity name works for module 'x_ctrl'.*'select'"):
        describe_test_access_tap(object(), _root())


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, "10"])
def test_describe_refuses_an_unusable_tck_limit_before_calling_warptap(fake_warptap, bad) -> None:
    with pytest.raises(ValueError, match="maximum frequency"):
        describe_test_access_tap(object(), _root(), tck_max_freq_hz=bad)
    assert fake_warptap.to_bsdl == []


@pytest.mark.parametrize("bad", [PLACEHOLDER - 1, 1 << 32, -1])
def test_describe_refuses_a_bad_idcode_with_warptaps_own_message(fake_warptap, bad: int) -> None:
    with pytest.raises(ValueError, match="IDCODE"):
        describe_test_access_tap(object(), _root(), idcode_value=bad)
    assert fake_warptap.to_bsdl == []


def test_describe_reports_a_warptap_without_bsdl_support(fake_warptap, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "warptap.bsdl_emit", None)  # `import` raises ImportError

    with pytest.raises(TestAccessUnavailable, match="upgrade warptap"):
        describe_test_access_tap(object(), _root())


def test_describe_maps_a_typeerror_to_the_missing_feature(fake_warptap, monkeypatch) -> None:
    def old_to_bsdl(entity, *, tck_max_freq_hz):  # an older to_bsdl: no idcode_value keyword
        return "BSDL"

    monkeypatch.setattr(sys.modules["warptap.bsdl_emit"], "to_bsdl", old_to_bsdl)
    with pytest.raises(TestAccessUnavailable, match="IDCODE"):
        describe_test_access_tap(object(), _root(), idcode_value=CUSTOM)

    def old_to_icl(graph, root, *, include_access_link):  # an older to_icl: no bsdl_entity_name
        return "ICL"

    monkeypatch.setattr(sys.modules["warptap.icl_emit"], "to_icl", old_to_icl)
    with pytest.raises(TestAccessUnavailable, match="BSDL"):
        describe_test_access_tap(object(), _root())


def test_describe_needs_warptap_installed(monkeypatch) -> None:
    monkeypatch.setattr(testaccess, "_warptap_insert_test_access", None, raising=False)

    with pytest.raises(TestAccessUnavailable, match="warptap is not installed"):
        describe_test_access_tap(object(), _root())


# --- tap_facts -----------------------------------------------------------------

def test_tap_facts_state_what_the_hardware_holds(fake_warptap) -> None:
    plain = tap_facts()
    custom = tap_facts(idcode_value=CUSTOM, description=TapDescription("i", "b", "ent", 25e6))

    assert plain == {"idcode": "0x1A5A5003", "idcode_is_placeholder": True, "instruction_length": 4,
                     "network_access_instruction": "EXTEST", "network_access_opcode": "0000"}
    assert custom == {**plain, "idcode": "0x5CA1AB1F", "idcode_is_placeholder": False,
                      "bsdl_entity": "ent", "tck_max_freq_hz": 25e6}


# --- wrap_test_access ----------------------------------------------------------

@pytest.fixture
def fake_insert(fake_warptap, monkeypatch):
    seen = {}

    def insert(sources, top, specs, **kwargs):
        seen.update(kwargs)
        return "module x(); endmodule", object(), _root(top)

    monkeypatch.setattr(testaccess, "_warptap_insert_test_access", insert, raising=False)
    monkeypatch.setattr(testaccess, "build_instrument_specs", lambda ports: [])
    return seen


def test_wrap_passes_the_idcode_to_warptap_only_when_given(fake_insert) -> None:
    wrap_test_access(["a.v"], "x")
    assert "idcode_value" not in fake_insert

    wrap_test_access(["a.v"], "x", idcode_value=CUSTOM)
    assert fake_insert["idcode_value"] == CUSTOM


def test_wrap_refuses_a_bad_idcode_before_inserting_anything(fake_insert) -> None:
    with pytest.raises(ValueError, match="bit 0"):
        wrap_test_access(["a.v"], "x", idcode_value=PLACEHOLDER - 1)
    assert fake_insert == {}


def test_wrap_maps_an_idcode_typeerror_to_the_missing_feature(fake_warptap, monkeypatch) -> None:
    def insert(sources, top, specs, **kwargs):
        if "idcode_value" in kwargs:
            raise TypeError("insert_test_access() got an unexpected keyword argument 'idcode_value'")
        return "v", object(), _root()

    monkeypatch.setattr(testaccess, "_warptap_insert_test_access", insert, raising=False)
    monkeypatch.setattr(testaccess, "build_instrument_specs", lambda ports: [])

    with pytest.raises(TestAccessUnavailable, match="IDCODE"):
        wrap_test_access(["a.v"], "x", idcode_value=CUSTOM)


def test_wrap_does_not_hide_an_unrelated_typeerror(fake_warptap, monkeypatch) -> None:
    def insert(sources, top, specs, **kwargs):
        raise TypeError("something else entirely")

    monkeypatch.setattr(testaccess, "_warptap_insert_test_access", insert, raising=False)
    monkeypatch.setattr(testaccess, "build_instrument_specs", lambda ports: [])

    with pytest.raises(TypeError, match="something else"):
        wrap_test_access(["a.v"], "x")
    with pytest.raises(TypeError, match="something else"):  # even with an idcode given
        wrap_test_access(["a.v"], "x", idcode_value=CUSTOM)


def test_wrap_needs_the_idcode_check_from_a_warptap_that_has_it(fake_warptap, monkeypatch) -> None:
    del sys.modules["warptap.tap_model"].idcode_value_error
    monkeypatch.setattr(testaccess, "build_instrument_specs", lambda ports: [])

    with pytest.raises(TestAccessUnavailable, match="IDCODE"):
        wrap_test_access(["a.v"], "x", idcode_value=CUSTOM)


# --- the CLI: validation first, nothing written on a failure -------------------

def _wrap_cli(tmp_path: Path, *args: str):
    source = tmp_path / "a.v"
    source.write_text("module t(); endmodule\n", encoding="utf-8")
    return runner.invoke(app, ["wrap-test-access", "--source", str(source), "--top", "t",
                               "--out", str(tmp_path / "out"), *args])


def test_cli_refuses_a_tck_limit_without_emit_icl(tmp_path: Path) -> None:
    result = _wrap_cli(tmp_path, "--tck-max-freq-mhz", "25")

    assert result.exit_code == 1 and "needs --emit-icl" in result.output
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("bad", ["0", "-5", "nan", "inf"])
def test_cli_refuses_an_unusable_tck_limit(tmp_path: Path, bad: str) -> None:
    result = _wrap_cli(tmp_path, "--emit-icl", "--tck-max-freq-mhz", bad)

    assert result.exit_code == 1 and "finite number > 0" in result.output
    assert not (tmp_path / "out").exists()


def test_cli_refuses_an_idcode_that_is_not_a_number(tmp_path: Path) -> None:
    result = _wrap_cli(tmp_path, "--idcode", "zzz")

    assert result.exit_code == 1 and "not a number" in result.output


def test_cli_says_so_when_warptap_is_not_installed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(testaccess, "_warptap_insert_test_access", None, raising=False)

    result = _wrap_cli(tmp_path)

    assert result.exit_code == 1 and "warptap is not installed" in result.output


@pytest.fixture
def faked_wrap(monkeypatch):
    seen = SimpleNamespace(wrap=None, describe=None)

    def wrap(sources, top, **kwargs):
        seen.wrap = kwargs
        return "module t(); endmodule\n", SimpleNamespace(chain=[]), _root(top)

    def describe(graph, root, *, tck_max_freq_hz, idcode_value):
        seen.describe = (tck_max_freq_hz, idcode_value)
        return TapDescription("ICL TEXT", "BSDL TEXT", "t", tck_max_freq_hz)

    monkeypatch.setattr(testaccess, "wrap_test_access", wrap)
    monkeypatch.setattr(testaccess, "describe_test_access_tap", describe)
    monkeypatch.setattr(testaccess, "tap_facts", lambda **kw: {
        "idcode": f"0x{(kw['idcode_value'] or PLACEHOLDER):08X}",
        "idcode_is_placeholder": kw["idcode_value"] is None})
    return seen


def test_cli_writes_the_netlist_icl_and_bsdl_and_reports_the_assumptions(tmp_path: Path, faked_wrap) -> None:
    result = _wrap_cli(tmp_path, "--emit-icl")

    out = tmp_path / "out"
    assert result.exit_code == 0, result.output
    assert (out / "t_test_access.v").read_text() == "module t(); endmodule\n"
    assert (out / "t_test_access.icl").read_text() == "ICL TEXT"
    assert (out / "t_test_access.bsd").read_text() == "BSDL TEXT"
    # the defaults are stated, not silent
    assert "TCK max 10 MHz -- assumed" in result.output
    assert "0x1A5A5003 (warptap placeholder" in result.output
    assert faked_wrap.describe == (DEFAULT_TCK_MAX_FREQ_HZ, None)
    assert faked_wrap.wrap["idcode_value"] is None


def test_cli_passes_the_options_through_and_stops_calling_them_assumptions(tmp_path: Path, faked_wrap) -> None:
    result = _wrap_cli(tmp_path, "--emit-icl", "--tck-max-freq-mhz", "25", "--idcode", hex(CUSTOM))

    assert result.exit_code == 0, result.output
    assert faked_wrap.describe == (25e6, CUSTOM) and faked_wrap.wrap["idcode_value"] == CUSTOM
    assert "TCK max 25 MHz)" in result.output and "assumed" not in result.output
    assert "0x5CA1AB1F (set by --idcode)" in result.output


def test_cli_without_emit_icl_writes_no_icl_or_bsdl(tmp_path: Path, faked_wrap) -> None:
    result = _wrap_cli(tmp_path, "--idcode", hex(CUSTOM))

    out = tmp_path / "out"
    assert result.exit_code == 0, result.output
    assert (out / "t_test_access.v").is_file()
    assert not list(out.glob("*.icl")) and not list(out.glob("*.bsd"))
    assert faked_wrap.describe is None  # nothing rendered


def test_cli_renders_before_writing_so_a_failure_leaves_no_output(tmp_path: Path, faked_wrap, monkeypatch) -> None:
    def refuse(graph, root, *, tck_max_freq_hz, idcode_value):
        raise ValueError("no BSDL entity name works")

    monkeypatch.setattr(testaccess, "describe_test_access_tap", refuse)

    result = _wrap_cli(tmp_path, "--emit-icl")

    assert result.exit_code == 1 and "no BSDL entity name works" in result.output
    assert not (tmp_path / "out").exists()
