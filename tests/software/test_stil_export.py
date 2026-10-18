"""The STIL export's own logic: which pins a STIL file declares and the values it holds
them at, the warptap version it needs, the CLI's checks before anything is written, and
the shape of what render_stil writes. Behavior against the RTL (replay, controls) is
tests/integration/test_stil_e2e.py.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autombist.cli import app
from autombist.jtag_bist import (
    NetlistPort,
    StilTiming,
    TestbenchError,
    _bit_names,
    render_stil,
    run_mbist_steps,
    stil_pins,
    stil_support_problem,
)

runner = CliRunner()
PORTS = [
    NetlistPort("clk", "input", ""), NetlistPort("rst_n", "input", ""),
    NetlistPort("test_mode", "input", ""), NetlistPort("bist_start", "input", ""),
    NetlistPort("bist_done", "output", ""), NetlistPort("bist_fail", "output", ""),
    NetlistPort("func_csb", "input", ""), NetlistPort("func_addr", "input", "[2:0] "),
    NetlistPort("func_dout", "output", "[3:0] "),
    NetlistPort("tck", "input", ""), NetlistPort("tms", "input", ""), NetlistPort("tdi", "input", ""),
    NetlistPort("trst_n", "input", ""), NetlistPort("tdo", "output", ""),
]


@pytest.mark.parametrize(("rng", "bits"), [("", ["p"]), ("[0:0] ", ["p[0]"]), ("[3:0] ", ["p[0]", "p[1]", "p[2]", "p[3]"]),
                                          ("[0:2] ", ["p[0]", "p[1]", "p[2]"])])
def test_a_bus_is_one_stil_signal_per_bit(rng: str, bits: list[str]) -> None:
    assert _bit_names(NetlistPort("p", "input", rng)) == bits


def test_stil_pins_declare_every_non_tap_pin_with_its_idle_value() -> None:
    inputs, outputs = stil_pins(PORTS)

    assert inputs == {"clk": 0, "rst_n": 1, "test_mode": 0, "bist_start": 0, "func_csb": 1,
                      "func_addr[0]": 0, "func_addr[1]": 0, "func_addr[2]": 0, "trst_n": 1}
    assert outputs == ["bist_done", "bist_fail", "func_dout[0]", "func_dout[1]", "func_dout[2]", "func_dout[3]"]
    assert not {"tck", "tms", "tdi", "tdo"} & {*inputs, *outputs}  # to_stil declares the TAP's own


@pytest.mark.parametrize(("version", "ok"), [("0.0.2", False), ("0.0.3", False), ("0.0.4", True), ("0.1.0", True)])
def test_the_stil_needs_warptap_0_0_4(monkeypatch, version: str, ok: bool) -> None:
    monkeypatch.setitem(sys.modules, "warptap", types.SimpleNamespace(__version__=version))

    problem = stil_support_problem()

    assert (problem is None) is ok
    if not ok:
        assert "0.0.4" in problem and "after the rising TCK edge" in problem


def test_without_warptap_the_support_check_leaves_the_message_to_wrapping(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "warptap", None)  # `import warptap` raises ImportError

    assert stil_support_problem() is None


# --- the CLI's checks, before anything is wrapped or written -------------------

def _cli(tmp_path: Path, *args: str):
    source = tmp_path / "a.v"
    source.write_text("module t(); endmodule\n", encoding="utf-8")
    return runner.invoke(app, ["wrap-test-access", "--source", str(source), "--top", "t",
                               "--out", str(tmp_path / "out"), *args])


def test_cli_stil_clock_period_needs_emit_stil(tmp_path: Path) -> None:
    result = _cli(tmp_path, "--stil-clk-period-ns", "10")
    assert result.exit_code == 1 and "needs --emit-stil" in result.output


@pytest.mark.parametrize("bad", ["0", "-1", "nan", "inf"])
def test_cli_refuses_an_unusable_stil_clock_period(tmp_path: Path, bad: str) -> None:
    result = _cli(tmp_path, "--emit-stil", "--stil-clk-period-ns", bad)
    assert result.exit_code == 1 and "finite number > 0" in result.output


def test_cli_emit_stil_needs_the_bist_run_length(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "warptap", types.SimpleNamespace(__version__="0.0.4"))

    result = _cli(tmp_path, "--emit-stil")

    assert result.exit_code == 1 and "needs the BIST's run length" in result.output
    assert not (tmp_path / "out").exists()


def test_cli_tck_limit_is_accepted_with_emit_stil_alone(tmp_path: Path, monkeypatch) -> None:
    """--tck-max-freq-mhz sets the STIL's TCK period too, so it no longer needs --emit-icl."""
    monkeypatch.setitem(sys.modules, "warptap", types.SimpleNamespace(__version__="0.0.4"))

    result = _cli(tmp_path, "--emit-stil", "--tck-max-freq-mhz", "25")

    assert "needs --emit-icl" not in result.output
    assert "needs the BIST's run length" in result.output  # the next check, not the TCK one


def test_cli_refuses_stil_with_a_warptap_that_compares_after_the_edge(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "warptap", types.SimpleNamespace(__version__="0.0.3"))

    result = _cli(tmp_path, "--emit-stil", "--bist-cycles", "100")

    assert result.exit_code == 1 and "0.0.4" in result.output
    assert not (tmp_path / "out").exists()


# --- what render_stil writes (real warptap) ------------------------------------

def _network():
    if stil_support_problem() is not None:
        pytest.skip("needs warptap 0.0.4 or later")
    pytest.importorskip("warptap.tap_ir_stil", reason="needs warptap 0.0.4 or later")
    from warptap.sib_plan import build_sib_plan

    from autombist.testaccess import build_instrument_specs, classify_test_access_ports

    return build_sib_plan(build_instrument_specs(classify_test_access_ports()), top_name="t")


def test_render_stil_opens_with_the_reset_lead_in_and_states_its_periods() -> None:
    graph, root = _network()
    from autombist.jtag_bist import run_mbist_ops
    from autombist.testaccess import network_access

    access = network_access()
    ops = run_mbist_ops(graph, root, run_mbist_steps(50), access=access)
    stil = render_stil("t", ops, PORTS, StilTiming(tck_period_ns=40, clk_period_ns=12.5), access)

    assert stil.startswith("// IEEE 1450 STIL: run the memory BIST of t over JTAG")
    assert "// TCK period 40 ns, clk period 12.5 ns." in stil
    assert "// Then loads IJTAG_ACCESS (IR 1100)" in stil
    assert "Period '40ns';" in stil and "Period '12.5ns';" in stil
    first = re.search(r"V \{([^}]*)\}", stil.split("Pattern ", 1)[1]).group(1)
    assert "trst_n=0;" in first and "rst_n=0;" in first  # both resets asserted from the first vector
    assert re.search(r"Loop 50 \{", stil)  # the run loop, as one statement


def test_render_stil_refuses_a_top_without_its_reset_pins() -> None:
    graph, root = _network()

    with pytest.raises(TestbenchError, match="rst_n"):
        render_stil("t", [], [p for p in PORTS if p.name != "rst_n"], StilTiming(100, 100))
