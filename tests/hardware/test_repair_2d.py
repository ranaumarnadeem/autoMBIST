"""Workstream M.1 2D (row + column) repair-loop cocotb testbench.

The column-repair sibling of test_repair_loop.py, and self-contained in the same
way (does not import test_mbist or test_repair_loop; duplicates their small
scan/wait helpers rather than cross-importing, so each cocotb test file stays
independently runnable).

A SEPARATE module rather than an hasattr-guarded extension of
test_repair_loop.py: a row-only wrapper has no col_repair_en/faulty_bit pins at
all, so probing for them there would be guessing at the DUT's shape rather than
knowing it.

Like test_repair_loop.py, every repair value is read from env vars computed in
Python by a real prior fail scan through repair.bira.analyze() +
repair.bisr.encode_repair() -- ROW_REPAIR_EN / FAULTY_ROW_ADDR /
COL_REPAIR_EN / FAULTY_BIT, plus EXPECTED_BIST_FAIL so the Python side can say
what it expects the RTL run to show. Nothing here is hardcoded, so one test
function serves every 2D scenario: no repair, a correct computed row+column
repair, or a deliberately WRONG one (the negative test).

Those env vars reach os.getenv without any Makefile `export`: runner.py appends
them as make command-line variable overrides, and GNU make exports command-line
variables to recipes automatically (exactly how ROW_REPAIR_EN already works).

The signature is loaded BEFORE the fail scan and held for the whole run. That
matters for columns specifically: spare_wen is driven from col_repair_en, so a
spare lane is only written while repair is enabled. Holding it high across the
scan's write phase means the spare lanes are properly initialized before
anything reads them -- the staleness path is exercised deliberately, and
separately, by test_repair_col_distinct.py.
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
    both remaps (func accesses take the same path the controller uses), so with
    a repair loaded the steered rows AND steered bit lanes read correct."""
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
                # An unreadable (X) word is a FAILING word, not an absent
                # observation. Silently skipping it -- as the row-only scans do
                # -- would let `fail_cells(...) == set()` pass vacuously in
                # exactly the situation the column staleness caveat describes
                # (a spare lane read before it was ever written reads X).
                for bit in range(data_width):
                    fails.add((addr, bit))
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
async def test_repair_2d(dut):
    addr_width = int(os.getenv("ADDR_WIDTH", "2"))
    data_width = int(os.getenv("DATA_WIDTH", "4"))
    read_latency = int(os.getenv("READ_LATENCY", "1"))
    row_repair_en = int(os.getenv("ROW_REPAIR_EN", "0"))
    faulty_row_addr = int(os.getenv("FAULTY_ROW_ADDR", "0"))
    col_repair_en = int(os.getenv("COL_REPAIR_EN", "0"))
    faulty_bit = int(os.getenv("FAULTY_BIT", "0"))
    expected_bist_fail = int(os.getenv("EXPECTED_BIST_FAIL", "1"))

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.func_csb.value = 1
    dut.func_we.value = 0
    dut.func_addr.value = 0
    dut.func_din.value = 0

    # Load and HOLD the full 2D signature for the whole run (both remaps are
    # combinational and steer the functional scan and the BIST alike).
    dut.row_repair_en.value = row_repair_en
    dut.faulty_row_addr.value = faulty_row_addr
    dut.col_repair_en.value = col_repair_en
    dut.faulty_bit.value = faulty_bit

    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    # 1) Observation-derived fail-bitmap through the functional port (and thus
    # through both remaps, with whatever signature is loaded).
    fails = await with_timeout(
        _functional_fail_scan(dut, addr_width, data_width, read_latency),
        500_000,
        "ns",
    )
    for addr, bit in fails:
        print("FAIL_CELL " + json.dumps({"addr": addr, "bit": bit}))
    print(f"FAIL_SCAN_COMPLETE cells={len(fails)}")

    # 2) A full BIST pass through the SAME remaps/signature.
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
        f"ROW_REPAIR_EN={row_repair_en:#x} FAULTY_ROW_ADDR={faulty_row_addr:#x} "
        f"COL_REPAIR_EN={col_repair_en:#x} FAULTY_BIT={faulty_bit:#x}: "
        f"expected bist_fail={expected_bist_fail}, got {bist_fail}"
    )
