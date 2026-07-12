"""Step-D end-to-end repair-loop cocotb testbench.

Self-contained (mirrors test_repair_row.py's isolation: does not import
test_mbist, and duplicates its small scan/wait helpers rather than
cross-importing another test module -- each cocotb test file stays
independently runnable).

Unlike test_repair_row.py's fixed off/on phase (which drives one hardcoded,
already-known-good repair value to prove the remap RTL works), this reads the
repair signature to drive from GENERIC env vars -- ROW_REPAIR_EN /
FAULTY_ROW_ADDR, arbitrary integers computed in Python by a real prior fail
scan through repair.bira.analyze() + repair.bisr.encode_row_repair(), not
hardcoded here. EXPECTED_BIST_FAIL is also passed in (the Python side knows
what it computed, so it knows what the RTL run should show), letting one test
function serve every scenario the Step-D integration test needs: no repair,
a correct computed repair, or a deliberately WRONG one (the negative test).
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
async def test_repair_loop(dut):
    addr_width = int(os.getenv("ADDR_WIDTH", "2"))
    data_width = int(os.getenv("DATA_WIDTH", "4"))
    read_latency = int(os.getenv("READ_LATENCY", "1"))
    row_repair_en = int(os.getenv("ROW_REPAIR_EN", "0"))
    faulty_row_addr = int(os.getenv("FAULTY_ROW_ADDR", "0"))
    expected_bist_fail = int(os.getenv("EXPECTED_BIST_FAIL", "1"))

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.func_csb.value = 1
    dut.func_we.value = 0
    dut.func_addr.value = 0
    dut.func_din.value = 0

    # Load and HOLD the repair signature for the whole run (the remap is
    # combinational and steers both the functional scan and the BIST).
    dut.row_repair_en.value = row_repair_en
    dut.faulty_row_addr.value = faulty_row_addr

    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    # 1) Observation-derived fail-bitmap through the functional port (and thus
    # through the remap, with whatever signature is loaded).
    fails = await with_timeout(
        _functional_fail_scan(dut, addr_width, data_width, read_latency),
        500_000,
        "ns",
    )
    for addr, bit in fails:
        print("FAIL_CELL " + json.dumps({"addr": addr, "bit": bit}))
    print(f"FAIL_SCAN_COMPLETE cells={len(fails)}")

    # 2) A full BIST pass through the SAME remap/signature.
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
    assert bist_fail == expected_bist_fail, (
        f"ROW_REPAIR_EN={row_repair_en:#x} FAULTY_ROW_ADDR={faulty_row_addr:#x}: "
        f"expected bist_fail={expected_bist_fail}, got {bist_fail}"
    )
