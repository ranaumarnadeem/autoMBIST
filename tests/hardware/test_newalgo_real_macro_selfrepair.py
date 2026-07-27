"""Functional self-repair proof for march-X and MATS+ against REAL OpenRAM
sky130 macros (the same real-macro behavioral views flow/multimem/mbist/
already hardens clean via LibreLane), extending the march-C-only proof in
test_mem_subsystem_mbist.py to the two new algorithms added this session.

Toplevel-agnostic on purpose: run_newalgo_real_macro_selfrepair_tb.py invokes
this SAME test module twice, once per generated single-wrapper design
(selfrepair_x wrapping sky130_sram_32b256w via march-X, selfrepair_mp
wrapping sky130_sram_32b512w via MATS+) -- both expose the identical
single-port self-repair wrapper port set (clk/rst_n/test_mode/bist_*/
func_*/self_repair_*), so one test body covers both.

Real macros have no defect-injection knob pre-silicon, so (like
test_mem_subsystem_mbist.py) this is not a defect-correction proof -- it
proves: a) self-repair's own BIST runs CLEAN (no phantom unrepairable, no
phantom row_repair_en) against the real macro's actual read timing
(read_latency: 0 -- see run_newalgo_real_macro_selfrepair_tb.py), and
b) ordinary functional access still round-trips correctly afterward.
"""
from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer, with_timeout

# OpenRAM behavioral-model read timing (matches test_mem_subsystem_mbist.py):
# op registered at posedge, executes at the following negedge, dout valid
# #DELAY(3) after that negedge. With a 10 ns clock, sample at negedge+4.
DATA_VALID_NS = 4


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


def pattern(addr: int, width: int) -> int:
    return (0x9E3779B1 * (addr + 1)) & ((1 << width) - 1)


async def func_write(dut, addr: int, data: int) -> None:
    dut.func_addr.value = addr
    dut.func_din.value = data
    dut.func_we.value = 1
    dut.func_csb.value = 0
    await RisingEdge(dut.clk)
    dut.func_csb.value = 1
    dut.func_we.value = 0
    await FallingEdge(dut.clk)
    await Timer(1, unit="ns")


async def func_read(dut, addr: int) -> int:
    dut.func_addr.value = addr
    dut.func_we.value = 0
    dut.func_csb.value = 0
    await RisingEdge(dut.clk)
    dut.func_csb.value = 1
    await FallingEdge(dut.clk)
    await Timer(DATA_VALID_NS, unit="ns")
    return int(dut.func_dout.value)


@cocotb.test()
async def test_selfrepair_clean_on_real_macro(dut):
    """Self-repair BIST runs clean on a defect-free real macro (no phantom
    fail/repair), then ordinary functional access still round-trips."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.func_csb.value = 1
    dut.func_we.value = 0
    dut.func_addr.value = 0
    dut.func_din.value = 0
    dut.self_repair_start.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    dut.self_repair_start.value = 1
    await ClockCycles(dut.clk, 2)

    async def _wait_done():
        while True:
            await RisingEdge(dut.clk)
            await Timer(1, unit="ns")
            if int(dut.self_repair_done.value) == 1:
                return int(dut.self_repair_fail.value)

    self_repair_fail = await with_timeout(_wait_done(), 4_000_000, "ns")
    assert self_repair_fail == 0, (
        "the real macro is not actually defective -- expected self_repair_fail=0, "
        f"got {self_repair_fail} (if this regressed, check read_latency: 0 in "
        "run_newalgo_real_macro_selfrepair_tb.py -- see its docstring)"
    )

    en = _safe_int(dut.row_repair_en)
    assert en == 0, f"phantom repair latched (row_repair_en={en}) on a defect-free macro"

    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 4)
    assert int(dut.self_repair_busy.value) == 0, "self_repair_busy should have cleared"

    addr_width = len(dut.func_addr.value)
    data_width = len(dut.func_din.value)
    top = (1 << addr_width) - 1
    addrs = [0, 1, top // 2, top]
    for a in addrs:
        await func_write(dut, a, pattern(a, data_width))
    for a in addrs:
        got = await func_read(dut, a)
        exp = pattern(a, data_width)
        assert got == exp, f"addr 0x{a:x} after self-repair: read 0x{got:x}, expected 0x{exp:x}"
