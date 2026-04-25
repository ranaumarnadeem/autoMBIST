import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, with_timeout


@cocotb.test()
async def test_mbist_completes_without_fail(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.test_mode.value = 1
    dut.bist_start.value = 0

    dut.func_csb.value = 1
    dut.func_addr.value = 0
    dut.func_din.value = 0
    dut.func_we.value = 0

    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    dut.bist_start.value = 1
    await RisingEdge(dut.clk)
    dut.bist_start.value = 0

    await with_timeout(RisingEdge(dut.bist_done), 5, "ms")

    assert int(dut.bist_fail.value) == 0, "MBIST reported fail"
