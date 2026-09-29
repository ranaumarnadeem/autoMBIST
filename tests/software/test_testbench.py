from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autombist.generator import generate_from_config
from autombist.jtag_bist import (
    NetlistPort,
    PdlStep,
    Vector,
    parse_netlist_ports,
    render_jtag_testbench,
    render_pdl,
    run_mbist_steps,
)
from autombist.testbench import (
    TestbenchError,
    bist_cycle_bound,
    bist_cycles,
    parse_wrapper_ports,
    render_bist_testbench,
    rtl_sources,
)

PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}
BASE = {"memory_name": "sram_x", "wrapper_module_name": "x_ctrl", "addr_width": 4,
        "data_width": 8, "we_active_low": True, "ports": PORTS}
SELF_REPAIR = {**BASE, "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True,
                                      "onchip_repair_persistence": True, "onchip_diagnosis": True,
                                      "num_diagnosis_entries": 2}}
SHARED_BUS = {**BASE, "topology": "shared-bus", "memories": [{"name": "b0"}, {"name": "b1"}]}
TWO_RW = {**BASE, "ports": {
    "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
    "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"}}}


def _generate(tmp_path: Path, config: dict, algo: str = "march-c", **kwargs) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(path, tmp_path / "out", algo=algo, **kwargs)


# --- the pin-level testbench (generate) ----------------------------------------

@pytest.mark.parametrize(
    ("config", "algo"),
    [(BASE, "march-c"), (SELF_REPAIR, "march-c"), (SHARED_BUS, "march-c"), (TWO_RW, "march-2rw")],
    ids=["dedicated", "self-repair", "shared-bus", "march-2rw"],
)
def test_generate_writes_a_testbench_for_every_topology(tmp_path: Path, config: dict, algo: str) -> None:
    wrapper = _generate(tmp_path, config, algo)
    tb_dir = wrapper.parent / "tb"
    tb = (tb_dir / "tb_x_ctrl.sv").read_text(encoding="utf-8")
    run = (tb_dir / "run_tb.sh").read_text(encoding="utf-8")

    # every wrapper port is connected, by name
    for port in parse_wrapper_ports(wrapper.read_text(encoding="utf-8"), "x_ctrl"):
        assert f".{port.name}({port.name})" in tb
    assert "x_ctrl dut (" in tb
    assert "bist_start = 1'b1;" in tb and "`define MBIST_MAX_CYCLES" in tb
    # sources are relative to the output directory, so it stays relocatable
    assert '"$OUT/x_ctrl_mbist.v"' in run or '"$OUT/sram_x_mbist.v"' in run
    assert str(tmp_path) not in run
    assert "usage: $0 <memory model .v>" in run


def test_wrapper_ports_come_from_the_rendered_header(tmp_path: Path) -> None:
    wrapper = _generate(tmp_path, SELF_REPAIR)
    ports = {p.name: p for p in parse_wrapper_ports(wrapper.read_text(encoding="utf-8"), "x_ctrl")}
    assert ports["func_addr"].range == "[ADDR_WIDTH-1:0] " and ports["func_addr"].direction == "input"
    assert ports["fuse_faulty_row_addr"].range == "[2*ADDR_WIDTH-1:0] "
    assert ports["diag_valid"].direction == "output" and ports["diag_valid"].range == "[2-1:0] "
    assert ports["self_repair_start"].range == ""


def test_wrapper_port_parser_rejects_the_wrong_module() -> None:
    with pytest.raises(TestbenchError, match="no ANSI header"):
        parse_wrapper_ports("module other #(parameter integer A = 1) (input logic a);", "x_ctrl")


def test_testbench_idles_every_other_input(tmp_path: Path) -> None:
    wrapper = _generate(tmp_path, SELF_REPAIR)
    tb = (wrapper.parent / "tb" / "tb_x_ctrl.sv").read_text(encoding="utf-8")
    assert "logic func_csb = '1;" in tb  # active-low chip select: idle is high
    assert "logic self_repair_start = '0;" in tb
    assert "logic [2*ADDR_WIDTH-1:0] fuse_faulty_row_addr = '0;" in tb
    assert "wire  [2-1:0] diag_valid;" in tb
    assert "localparam integer ADDR_WIDTH = 4;" in tb


def test_a_test_build_compiles_its_saboteur_too(tmp_path: Path) -> None:
    wrapper = _generate(tmp_path, {**BASE, "memory_name": "sram_x"}, use_saboteur=True, faults=2, fault_seed=1)
    snapshot = yaml.safe_load((wrapper.parent / "config.yml").read_text(encoding="utf-8"))
    sources = rtl_sources(snapshot, wrapper.parent)
    assert sources[-1] == "sram_x_saboteur.v"
    assert "march_c/march_c_top.sv" in sources


# --- the BIST length -------------------------------------------------------------

@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({**BASE, "algo": "march-c"}, 16 * 25 + 3),
        ({**BASE, "algo": "march-c", "read_latency": 2}, 16 * 30 + 3),
        ({**BASE, "algo": "march-raw"}, 16 * 36 + 3),
        ({**BASE, "algo": "mats-plus", "read_latency": 0}, 16 * 10 + 3),
        ({**SHARED_BUS, "algo": "march-c"}, 2 * (16 * 25 + 3)),
        # spare rows aren't in the BIST's address space
        ({**SELF_REPAIR, "algo": "march-c"}, 16 * 25 + 3),
    ],
)
def test_bist_cycles_model(config: dict, expected: int) -> None:
    assert bist_cycles(config) == expected
    assert bist_cycle_bound(config) > expected


def test_bist_cycles_rejects_an_unknown_algorithm() -> None:
    with pytest.raises(TestbenchError, match="no BIST length"):
        bist_cycles({**BASE, "algo": "galpat"})


def test_testbench_renders_its_timeout() -> None:
    ports = parse_wrapper_ports(
        "module t #(parameter integer ADDR_WIDTH = 4) (input logic clk, input logic rst_n, "
        "input logic test_mode, input logic bist_start, output logic bist_done, output logic bist_fail);",
        "t",
    )
    text = render_bist_testbench({"wrapper_module_name": "t", "addr_width": 4, "data_width": 8}, ports, 777)
    assert "`define MBIST_MAX_CYCLES 777" in text


# --- the JTAG side (wrap-test-access) -------------------------------------------

def test_run_mbist_holds_start_until_the_result_is_read() -> None:
    steps = run_mbist_steps(500)
    kinds = [(s.kind, s.instrument, s.value) for s in steps if s.kind != "apply"]
    assert kinds == [
        ("write", "test_mode", 1), ("write", "bist_start", 1), ("runloop", None, None),
        ("read", "bist_done", 1), ("read", "bist_fail", 0),
        ("write", "bist_start", 0), ("write", "test_mode", 0),
    ]
    assert next(s.count for s in steps if s.kind == "runloop") == 500
    # every write/read is committed by its own iApply
    for i, step in enumerate(steps):
        if step.kind in ("write", "read"):
            assert steps[i + 1] == PdlStep("apply")


def test_pdl_addresses_the_icl_registers() -> None:
    names = ("test_mode", "bist_start", "bist_done", "bist_fail")
    text = render_pdl(
        "x_ctrl", run_mbist_steps(520),
        widths={n: 1 for n in names},
        register_path={n: f"warptap_instr_{n}.DR" for n in names},
        icl_file="x_ctrl_test_access.icl", ir_width=4, extest=0, bist_cycles=403,
    )
    assert "iProcsForModule x_ctrl" in text
    assert "iProc run_mbist {} {" in text
    assert "    iWrite warptap_instr_bist_start.DR 0b1" in text
    assert "    iRead warptap_instr_bist_fail.DR 0b0" in text
    assert "    iRunLoop 520 -sck clk" in text
    assert "(IR 4'b0000)" in text and "# ICL: x_ctrl_test_access.icl" in text
    assert "up to 403 clk cycles" in text
    assert "iTarget" not in text  # IEEE 1687 has no iTarget; paths are absolute


def test_pdl_points_at_the_icl_and_the_bsdl_its_access_link_names() -> None:
    names = ("test_mode", "bist_start", "bist_done", "bist_fail")
    kwargs = dict(
        widths={n: 1 for n in names},
        register_path={n: f"warptap_instr_{n}.DR" for n in names},
        ir_width=4, extest=0, bist_cycles=403,
    )
    with_bsdl = render_pdl("x_ctrl", run_mbist_steps(520), icl_file="x.icl", bsdl_file="x.bsd", **kwargs)
    icl_only = render_pdl("x_ctrl", run_mbist_steps(520), icl_file="x.icl", **kwargs)

    assert "# ICL: x.icl" in with_bsdl and "# BSDL: x.bsd" in with_bsdl
    assert "AccessLink names the TAP instruction" in with_bsdl
    # what a tester without a retargeting tool needs is still stated
    assert "EXTEST (IR 4'b0000) loaded first" in with_bsdl
    assert "BSDL" not in icl_only and "(IR 4'b0000)" in icl_only


def test_vector_lines() -> None:
    assert Vector("tck", tms=1, tdi=0, tdo=1, read=2).line() == "0 1 0 1 2"
    assert Vector("sck", count=520).line() == "1 520 0 0 0"


YOSYS_NETLIST = """\
module \\$paramod$abc\\march_c_top (clk, x);
  input clk;
  output x;
endmodule
module x_ctrl(clk, rst_n, test_mode, bist_start, bist_done, bist_fail, func_addr, tck, tms, tdi, trst_n, tdo);
  wire _00_;
  output bist_done;
  input clk;
  input rst_n;
  input test_mode;
  input bist_start;
  output bist_fail;
  input [3:0] func_addr;
  wire [3:0] func_addr;
  input tck;
  input tms;
  input tdi;
  input trst_n;
  output tdo;
endmodule
"""


def test_netlist_ports_come_from_the_yosys_declarations() -> None:
    ports = parse_netlist_ports(YOSYS_NETLIST, "x_ctrl")
    assert [p.name for p in ports][:3] == ["clk", "rst_n", "test_mode"]
    assert NetlistPort("func_addr", "input", "[3:0] ") in ports
    assert NetlistPort("tdo", "output", "") in ports
    with pytest.raises(TestbenchError, match="no module"):
        parse_netlist_ports(YOSYS_NETLIST, "missing")


def test_jtag_testbench_labels_each_read() -> None:
    text = render_jtag_testbench(
        "x_ctrl", parse_netlist_ports(YOSYS_NETLIST, "x_ctrl"),
        vectors_file="x_ctrl_run_mbist.vec", pdl_file="x_ctrl_run_mbist.pdl",
        read_labels=["bist_done = 1", "bist_fail = 0"],
    )
    assert '1: read_label = "bist_done = 1";' in text
    assert '2: read_label = "bist_fail = 0";' in text
    assert "`define MBIST_VECTORS \"x_ctrl_run_mbist.vec\"" in text
    assert ".tdo(tdo)" in text and "logic [3:0] func_addr = '0;" in text


def test_jtag_testbench_needs_the_jtag_ports() -> None:
    ports = [NetlistPort("clk", "input", ""), NetlistPort("rst_n", "input", "")]
    with pytest.raises(TestbenchError, match="tck"):
        render_jtag_testbench("x", ports, vectors_file="v", pdl_file="p", read_labels=[])
