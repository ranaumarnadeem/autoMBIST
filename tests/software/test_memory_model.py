"""The behavioral memory model `generate` writes into tb/.

Its ports must be exactly the ones the blackbox stub declares for the same config
(the stub is what the wrapper is synthesized against, so it is the reference for
what the wrapper connects); the behavior is checked in simulation by
tests/integration/test_memory_model_e2e.py. Everything here is pure text.
"""
from __future__ import annotations

import re
from typing import Any

import pytest

from autombist.manifest import render_memory_stub
from autombist.memory_model import MemoryModelError, render_memory_model

P1 = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"}
COL = {**P1, "we": "web0", "spare_wen": "spare_wen0"}
P1R1W = {"rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
         "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"}}
P2RW = {"porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
        "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"}}
C1R1W = {"rport": P1R1W["rport"], "wport": {**P1R1W["wport"], "spare_wen": "spare_wen1"}}
C2RW = {"porta": {**P2RW["porta"], "spare_wen": "spare_wen0"}, "portb": {**P2RW["portb"], "spare_wen": "spare_wen1"}}
SHARED_CLK = {"porta": {**P2RW["porta"]}, "portb": {**P2RW["portb"], "clk": "clk0"}}  # one clock pin, two ports
# mem_addr_width / mem_data_width are what the generator computes for the rendered config
# (4 words + 1 spare row -> 3 address bits; 4 data bits + 1 spare column -> 5), set here by hand.
SRC = {"num_spare_rows": 1, "num_spare_cols": 1, "onchip_selfrepair": True, "onchip_col_repair": True,
       "mem_addr_width": 3, "mem_data_width": 5}


def _cfg(**kw: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {"memory_name": "mem_x", "wrapper_module_name": "mem_x_ctrl", "addr_width": 3,
                           "data_width": 4, "we_active_low": True, "ports": P1}
    cfg.update(kw)
    return cfg


SHAPES = {
    "single-port": _cfg(),
    "single-port-rl0": _cfg(read_latency=0),
    "single-port-we-high": _cfg(we_active_low=False, ports={**P1}),
    "wide": _cfg(addr_width=5, data_width=16),
    "1r1w": _cfg(ports=P1R1W),
    "1r1w-rl0": _cfg(ports=P1R1W, read_latency=0),
    "2rw": _cfg(ports=P2RW),
    "2rw-rl0": _cfg(ports=P2RW, read_latency=0),
    "spare-rows": _cfg(addr_width=2, ports={**P1, "we": "web0"},
                       redundancy={"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True,
                                   "mem_addr_width": 3}),
    "spare-cols": _cfg(addr_width=2, ports=COL, redundancy=SRC),
    "spare-cols-rl0": _cfg(addr_width=2, ports=COL, redundancy=SRC, read_latency=0),
    "spare-cols-1r1w": _cfg(addr_width=2, ports=C1R1W, redundancy=SRC),
    "spare-cols-2rw-rl0": _cfg(addr_width=2, ports=C2RW, redundancy=SRC, read_latency=0),
    "shared-bus": _cfg(addr_width=4, topology="shared-bus", memories=[{"name": "b0"}, {"name": "b1"}]),
    "shared-clock": _cfg(ports=SHARED_CLK),
}


def _header_ports(verilog: str, module: str) -> dict[str, tuple[str, str]]:
    """{port name: (direction, range)} from a module header; ``wire``/``reg`` ignored."""
    header = re.search(rf"module\s+{module}\s*#\((.*?)\)\s*\((.*?)\);", verilog, re.DOTALL)
    assert header, f"no module {module} header"
    ports = {}
    for decl in header.group(2).split(","):
        m = re.match(r"\s*(input|output)\s+(?:wire|reg)?\s*(\[[^\]]*\])?\s*(\w+)\s*$", decl)
        assert m, f"unparsable port declaration {decl!r}"
        ports[m.group(3)] = (m.group(1), (m.group(2) or "").replace(" ", ""))
    return ports


def _parameters(verilog: str) -> dict[str, int]:
    return {n: int(v) for n, v in re.findall(r"parameter integer (\w+)\s*=\s*(\d+)", verilog)}


@pytest.mark.parametrize("shape", SHAPES)
def test_the_model_has_exactly_the_stubs_ports_and_parameters(shape: str) -> None:
    config = SHAPES[shape]

    model = render_memory_model(config)
    stub = render_memory_stub(config)

    assert _header_ports(model, "mem_x") == _header_ports(stub, "mem_x")
    # the wrapper overrides these four on the instance; each must exist, with the stub's defaults
    assert _parameters(model) == _parameters(stub)
    assert set(_parameters(model)) == {"ADDR_WIDTH", "DATA_WIDTH", "NUM_SPARE_ROWS", "NUM_SPARE_COLS"}


def test_a_clock_pin_shared_by_two_ports_is_declared_once() -> None:
    model = render_memory_model(SHAPES["shared-clock"])

    assert len(re.findall(r"^\s*input\s+wire\s+clk0\b", model, re.MULTILINE)) == 1
    assert len(re.findall(r"always @\(posedge clk0\)", model)) == 2  # still one block per port


def test_write_polarity_follows_we_active_low() -> None:
    low = render_memory_model(_cfg(we_active_low=True))
    high = render_memory_model(_cfg(we_active_low=False))

    assert "if (!csb0 && !we0)" in low and "!p0_we_q" not in low and "&& p0_we_q)" in low
    assert "if (!csb0 && we0)" in high and "&& !p0_we_q)" in high


@pytest.mark.parametrize(("latency", "falling_edge"), [(0, True), (1, False), (2, False), (3, False)])
def test_read_latency_selects_the_timing_style(latency: int, falling_edge: bool) -> None:
    model = render_memory_model(_cfg(read_latency=latency))

    assert ("negedge clk0" in model) is falling_edge
    assert f"read_latency {latency}" in model or (latency >= 1 and "read_latency" in model)


@pytest.mark.parametrize(
    ("shape", "forwards"),
    [("1r1w-rl0", True), ("2rw-rl0", True), ("spare-cols-2rw-rl0", True),
     ("single-port-rl0", False), ("1r1w", False), ("2rw", False), ("single-port", False)],
)
def test_only_a_multi_port_falling_edge_model_forwards_a_same_edge_write(shape: str, forwards: bool) -> None:
    """The two-port algorithms expect a read concurrent with a same-address write to
    return the new data. The registered-read style gets that for free; the falling-edge
    style must forward it."""
    model = render_memory_model(SHAPES[shape])

    assert ("function" in model and "p0_read(" in model) is forwards


def test_a_port_never_forwards_its_own_write_to_its_own_read() -> None:
    model = render_memory_model(SHAPES["2rw-rl0"])

    port0_read = model[model.index("function [3:0] p0_read"):model.index("endfunction")]
    assert "p1_addr_q == a" in port0_read and "p0_addr_q == a" not in port0_read


def test_spare_lanes_are_written_only_under_their_spare_wen_bit() -> None:
    model = render_memory_model(SHAPES["spare-cols"])

    assert "mem[addr0][LOGICAL_W-1:0] <= din0[LOGICAL_W-1:0];" in model
    assert "if (spare_wen0[p0_k]) mem[addr0][LOGICAL_W + p0_k] <= din0[LOGICAL_W + p0_k];" in model
    assert "reg [4:0] mem [0:DEPTH-1];" in model  # 4 logical bits + 1 spare lane, 8 rows (4 + 1 spare, rounded up)
    assert "localparam integer DEPTH = 8;" in model


def test_a_memory_without_spare_columns_stores_whole_words() -> None:
    model = render_memory_model(SHAPES["single-port"])

    assert "mem[addr0] <= din0;" in model and "spare" not in model.lower().replace("num_spare", "")


def test_connected_data_narrower_than_the_logical_width_is_refused() -> None:
    config = _cfg(data_width=8, addr_width=2, ports=COL,
                  redundancy={**SRC, "mem_data_width": 4})

    with pytest.raises(MemoryModelError, match="data_width"):
        render_memory_model(config)
