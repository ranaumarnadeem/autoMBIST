"""Step-A repair-row cocotb testbench.

Self-contained (it does NOT import test_mbist -- that module's import-time
_prepare_fault_files() would fire against the wrong config). Drives the generated
redundancy wrapper's boundary directly. One test, gated by REPAIR_PHASE:

  * REPAIR_PHASE=off -> no repair loaded. The forced hard defect (physical row 3,
    bit 3, stuck-at-1 -- baked into sram_spares_tiny.v) is visible: the functional
    fail scan reports it and the march BIST asserts bist_fail.
  * REPAIR_PHASE=on  -> row_repair_en/faulty_row_addr steer logical row 3 to spare
    row 0 (a top address). The defect is gone: the fail scan is clean and BIST
    passes.

Same DUT, same defect; only the loaded repair config differs -- exactly the
inject -> detect -> repair -> re-test proof, minus BIRA/BISR (which run above this).
"""
import json
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer, with_timeout


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


async def _functional_fail_scan(dut, addr_width, data_width, read_latency):
    """Write solid-0 then solid-1 to every LOGICAL cell via the functional port
    and read back, recording each bit that differs. Observes the memory THROUGH
    the remap (func addresses go through the same path the controller uses), so
    with repair loaded the steered cells read correct."""
    depth = 1 << addr_width
    data_mask = (1 << data_width) - 1
    hold = read_latency + 4
    fails = set()
    for pattern in (0, data_mask):
        for addr in range(depth):
            dut.func_csb.value = 0
            dut.func_we.value = 1
            dut.func_addr.value = addr
            dut.func_din.value = pattern
            await ClockCycles(dut.clk, 2)
        dut.func_csb.value = 1
        dut.func_we.value = 0
        await ClockCycles(dut.clk, 2)
        for addr in range(depth):
            dut.func_csb.value = 0
            dut.func_we.value = 0
            dut.func_addr.value = addr
            await ClockCycles(dut.clk, hold)
            await Timer(1, unit="ns")
            read_word = _safe_int(dut.func_dout)
            if read_word is None:
                continue
            diff = (read_word ^ pattern) & data_mask
            bit = 0
            while diff:
                if diff & 1:
                    fails.add((addr, bit))
                diff >>= 1
                bit += 1
        dut.func_csb.value = 1
        await ClockCycles(dut.clk, 2)
    return sorted(fails)


async def _wait_bist_done(dut):
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        if int(dut.bist_done.value) == 1:
            return


@cocotb.test()
async def test_repair_row(dut):
    phase = os.getenv("REPAIR_PHASE", "off").strip().lower()
    addr_width = int(os.getenv("ADDR_WIDTH", "2"))
    data_width = int(os.getenv("DATA_WIDTH", "4"))
    read_latency = int(os.getenv("READ_LATENCY", "1"))

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.func_csb.value = 1
    dut.func_we.value = 0
    dut.func_addr.value = 0
    dut.func_din.value = 0

    # Load and HOLD the repair config for the whole run (the remap is combinational
    # and steers both the functional scan and the BIST). Steer faulty logical row 3
    # to spare 0: spare-0's faulty_row_addr slot is the low ADDR_WIDTH bits.
    if phase == "on":
        dut.row_repair_en.value = 0b01
        dut.faulty_row_addr.value = 3
    else:
        dut.row_repair_en.value = 0
        dut.faulty_row_addr.value = 0

    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    # 1) Observation-derived fail-bitmap through the functional port.
    fails = await with_timeout(
        _functional_fail_scan(dut, addr_width, data_width, read_latency),
        500_000,
        "ns",
    )
    for addr, bit in fails:
        print("FAIL_CELL " + json.dumps({"addr": addr, "bit": bit}))
    print(f"FAIL_SCAN_COMPLETE cells={len(fails)}")

    # 2) A full BIST pass through the SAME remap. bist_fail reflects whether the
    # march controller still sees the defect.
    dut.func_csb.value = 1
    dut.test_mode.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    dut.bist_start.value = 1
    await RisingEdge(dut.clk)
    dut.bist_start.value = 0
    await with_timeout(_wait_bist_done(dut), 500_000, "ns")

    bist_fail = int(dut.bist_fail.value)
    expected_fail = 0 if phase == "on" else 1
    assert bist_fail == expected_fail, (
        f"REPAIR_PHASE={phase}: expected bist_fail={expected_fail}, got {bist_fail}"
    )
