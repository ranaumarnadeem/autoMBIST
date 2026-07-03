from __future__ import annotations

from pathlib import Path

import pytest

from autombist.fsm_harness import (
    REQUIRED_PORTS_MP,
    FsmPortError,
    check_ports,
    gather_sibling_sources,
    parse_ports,
    render_harness,
    render_harness_mp,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MARCH_C_TOP = REPO_ROOT / "rtl" / "march_c" / "march_c_top.sv"
MARCH_RAW_TOP = REPO_ROOT / "rtl" / "march_raw" / "march_raw_top.sv"
MARCH_2RW_TOP = REPO_ROOT / "rtl" / "march_2rw" / "march_2rw_top.sv"

_BROKEN_FSM = """
module broken_fsm (
    input  logic clk,
    input  logic rst_n,
    input  logic bist_start,
    output logic bist_done,
    // bist_fail is missing entirely
    output logic sram_clk0,
    output logic sram_csb0,
    output logic sram_web0,
    output logic [9:0] sram_addr0,
    output logic [31:0] sram_din0,
    input  logic [31:0] sram_dout0
);
endmodule
"""

_MINIMAL_VALID_FSM = """
module tiny_fsm #(
    parameter integer ADDR_WIDTH = 4,
    parameter integer DATA_WIDTH = 8
) (
    input  logic clk,
    input  logic rst_n,
    input  logic bist_start,
    output logic bist_done,
    output logic bist_fail,
    output logic sram_clk0,
    output logic sram_csb0,
    output logic sram_web0,
    output logic [ADDR_WIDTH-1:0] sram_addr0,
    output logic [DATA_WIDTH-1:0] sram_din0,
    input  logic [DATA_WIDTH-1:0] sram_dout0
);
endmodule
"""


def test_check_ports_accepts_real_march_c_top() -> None:
    text = MARCH_C_TOP.read_text(encoding="utf-8")
    ports = check_ports(text)
    assert ports.module_name == "march_c_top"
    assert ports.missing() == {}


def test_check_ports_accepts_real_march_raw_top() -> None:
    text = MARCH_RAW_TOP.read_text(encoding="utf-8")
    ports = check_ports(text)
    assert ports.module_name == "march_raw_top"
    assert ports.missing() == {}


def test_check_ports_accepts_minimal_valid_fsm() -> None:
    ports = check_ports(_MINIMAL_VALID_FSM)
    assert ports.module_name == "tiny_fsm"


def test_check_ports_rejects_missing_bist_fail() -> None:
    with pytest.raises(FsmPortError, match="bist_fail"):
        check_ports(_BROKEN_FSM)


def test_check_ports_error_message_is_a_clear_diff() -> None:
    try:
        check_ports(_BROKEN_FSM)
        pytest.fail("expected FsmPortError")
    except FsmPortError as exc:
        msg = str(exc)
        assert "broken_fsm" in msg
        assert "bist_fail: expected output" in msg
        assert "not found" in msg


def test_parse_ports_extracts_all_declared_ports() -> None:
    ports = parse_ports(_MINIMAL_VALID_FSM)
    assert ports.ports["clk"] == "input"
    assert ports.ports["sram_addr0"] == "output"
    assert ports.ports["sram_dout0"] == "input"


def test_parse_ports_no_module_raises() -> None:
    with pytest.raises(FsmPortError):
        parse_ports("// not a verilog file at all\n")


def test_gather_sibling_sources_finds_march_c_dependencies() -> None:
    sources = gather_sibling_sources(MARCH_C_TOP)
    names = {p.name for p in sources}
    assert sources[0] == MARCH_C_TOP.resolve()  # top first
    assert {"march_c_fsm.sv", "march_c_algo.sv"} <= names


def test_gather_sibling_sources_resolves_relative_paths(monkeypatch) -> None:
    # Verilator is invoked with cwd=<campaign workdir>, not the caller's cwd,
    # so a relative --fsm path must not silently resolve against the wrong
    # directory (it previously did: "verilator build failed" with no
    # indication why -- this is the regression that bug caused).
    monkeypatch.chdir(REPO_ROOT)
    relative = Path("rtl") / "march_c" / "march_c_top.sv"
    assert not relative.is_absolute()
    sources = gather_sibling_sources(relative)
    assert all(p.is_absolute() for p in sources)
    assert sources[0] == MARCH_C_TOP.resolve()


def test_render_harness_wires_the_fsm_by_name() -> None:
    text = render_harness(addr_width=8, data_width=8, fsm_module_name="tiny_fsm")
    assert "tiny_fsm #(" in text
    assert "openram_shim #(" in text
    assert "RESULT DETECTED alg=FSM" in text
    assert "RESULT ESCAPED alg=FSM" in text


def test_render_harness_bist_busy_wiring_is_conditional() -> None:
    # bist_busy is optional in the port contract; a named connection to a pin
    # the target module doesn't declare is a Verilator error, so the harness
    # must only wire it when the FSM actually has it.
    without = render_harness(addr_width=8, data_width=8, fsm_module_name="tiny_fsm", has_bist_busy=False)
    assert ".bist_busy(bist_busy)" not in without

    withit = render_harness(addr_width=8, data_width=8, fsm_module_name="tiny_fsm", has_bist_busy=True)
    assert ".bist_busy(bist_busy)" in withit


def test_check_ports_march_c_top_has_bist_busy() -> None:
    # march_c_top.sv declares the optional bist_busy port; used by
    # run_fsm_campaign to decide whether to wire it in the harness.
    ports = parse_ports(MARCH_C_TOP.read_text(encoding="utf-8"))
    assert ports.ports.get("bist_busy") == "output"


# --------------------------------------------------------------------------- #
# num_ports=2 (REQUIRED_PORTS_MP) -- new coverage; every case above this point
# is untouched, proving the single-port default behavior of check_ports()
# (no num_ports arg, or num_ports=1) is unchanged.
# --------------------------------------------------------------------------- #

_MINIMAL_VALID_FSM_MP = """
module tiny_fsm_mp #(
    parameter integer ADDR_WIDTH = 4,
    parameter integer DATA_WIDTH = 8
) (
    input  logic clk,
    input  logic rst_n,
    input  logic bist_start,
    output logic bist_done,
    output logic bist_fail,
    output logic sram_clk0,
    output logic sram_csb0,
    output logic sram_web0,
    output logic [ADDR_WIDTH-1:0] sram_addr0,
    output logic [DATA_WIDTH-1:0] sram_din0,
    input  logic [DATA_WIDTH-1:0] sram_dout0,
    output logic sram_clk1,
    output logic sram_csb1,
    output logic sram_web1,
    output logic [ADDR_WIDTH-1:0] sram_addr1,
    output logic [DATA_WIDTH-1:0] sram_din1,
    input  logic [DATA_WIDTH-1:0] sram_dout1
);
endmodule
"""

# Same as _MINIMAL_VALID_FSM_MP but missing the entire port-1 bus -- shaped
# like a single-port FSM mistakenly registered against a 2-port session.
_MISSING_PORT1_FSM = """
module missing_port1_fsm (
    input  logic clk,
    input  logic rst_n,
    input  logic bist_start,
    output logic bist_done,
    output logic bist_fail,
    output logic sram_clk0,
    output logic sram_csb0,
    output logic sram_web0,
    output logic [9:0] sram_addr0,
    output logic [31:0] sram_din0,
    input  logic [31:0] sram_dout0
);
endmodule
"""


def test_required_ports_mp_is_superset_of_required_ports() -> None:
    from autombist.fsm_harness import REQUIRED_PORTS

    for name, direction in REQUIRED_PORTS.items():
        assert REQUIRED_PORTS_MP[name] == direction
    assert set(REQUIRED_PORTS_MP) - set(REQUIRED_PORTS) == {
        "sram_clk1", "sram_csb1", "sram_web1", "sram_addr1", "sram_din1", "sram_dout1",
    }


def test_check_ports_num_ports_2_accepts_valid_2port_fsm() -> None:
    ports = check_ports(_MINIMAL_VALID_FSM_MP, num_ports=2)
    assert ports.module_name == "tiny_fsm_mp"
    assert ports.missing(REQUIRED_PORTS_MP) == {}


def test_check_ports_num_ports_2_rejects_missing_port1_bus() -> None:
    with pytest.raises(FsmPortError, match="sram_clk1"):
        check_ports(_MISSING_PORT1_FSM, num_ports=2)


def test_check_ports_num_ports_2_error_lists_all_missing_port1_pins() -> None:
    try:
        check_ports(_MISSING_PORT1_FSM, num_ports=2)
        pytest.fail("expected FsmPortError")
    except FsmPortError as exc:
        msg = str(exc)
        for pin in ("sram_clk1", "sram_csb1", "sram_web1", "sram_addr1", "sram_din1", "sram_dout1"):
            assert pin in msg


def test_check_ports_num_ports_2_accepts_real_march_2rw_top() -> None:
    # march_2rw_top.sv's pinout is pin-for-pin the reference 2-port FSM
    # contract (clk/rst_n/bist_start/bist_busy/bist_done/bist_fail plus a
    # fully symmetric sram_*0/sram_*1 bus).
    ports = check_ports(MARCH_2RW_TOP.read_text(encoding="utf-8"), num_ports=2)
    assert ports.module_name == "march_2rw_top"
    assert ports.missing(REQUIRED_PORTS_MP) == {}


def test_check_ports_invalid_num_ports_raises() -> None:
    with pytest.raises(ValueError, match="num_ports"):
        check_ports(_MINIMAL_VALID_FSM, num_ports=3)


def test_check_ports_default_num_ports_unchanged_for_single_port_fsm() -> None:
    # Regression: no num_ports arg must behave identically to num_ports=1 for
    # every existing single-port fixture in this module.
    default_call = check_ports(_MINIMAL_VALID_FSM)
    explicit_call = check_ports(_MINIMAL_VALID_FSM, num_ports=1)
    assert default_call.module_name == explicit_call.module_name == "tiny_fsm"
    assert default_call.ports == explicit_call.ports


def test_check_ports_default_num_ports_unchanged_for_march_c_and_march_raw() -> None:
    for top in (MARCH_C_TOP, MARCH_RAW_TOP):
        text = top.read_text(encoding="utf-8")
        default_call = check_ports(text)
        explicit_call = check_ports(text, num_ports=1)
        assert default_call.ports == explicit_call.ports
        assert default_call.missing() == explicit_call.missing() == {}


def test_check_ports_default_num_ports_still_rejects_broken_fsm() -> None:
    # Regression: the num_ports=1 (default) rejection path/message is unchanged.
    with pytest.raises(FsmPortError, match="bist_fail"):
        check_ports(_BROKEN_FSM)
    with pytest.raises(FsmPortError, match="bist_fail"):
        check_ports(_BROKEN_FSM, num_ports=1)


def test_render_harness_mp_wires_the_fsm_and_openram_shim_mp() -> None:
    text = render_harness_mp(addr_width=8, data_width=8, fsm_module_name="tiny_fsm_mp")
    assert "tiny_fsm_mp #(" in text
    assert "openram_shim_mp #(" in text
    assert "sram_clk1" in text and "sram_dout1" in text
    assert "RESULT DETECTED alg=FSM" in text
    assert "RESULT ESCAPED alg=FSM" in text


def test_render_harness_mp_bist_busy_wiring_is_conditional() -> None:
    without = render_harness_mp(addr_width=8, data_width=8, fsm_module_name="tiny_fsm_mp", has_bist_busy=False)
    assert ".bist_busy(bist_busy)" not in without

    withit = render_harness_mp(addr_width=8, data_width=8, fsm_module_name="tiny_fsm_mp", has_bist_busy=True)
    assert ".bist_busy(bist_busy)" in withit
