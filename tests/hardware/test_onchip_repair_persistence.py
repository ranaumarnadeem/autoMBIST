"""Persisted-repair-signature model (Workstream 1.7) cocotb testbench.

Self-contained, NOT importing test_onchip_selfrepair.py: this is the first
test in this area that deliberately toggles rst_n mid-test, which
test_onchip_selfrepair.py's own header comment explicitly forbids (its
signature lives in flops cleared by rst_n, so a copy-pasted reset there would
silently wipe a just-computed repair). Here that's the entire point -- proving
a repair signature survives (or, without repair_load, does NOT survive) a
reset -- so this module owns its own reset helper and its own copy of
_functional_fail_scan rather than importing either.

Scenario: reset -> repair_load a KNOWN-GOOD signature BEFORE any
self_repair_start or functional access -> confirm the functional-port fail
scan is immediately clean with NO self-repair sequence having run at all ->
reset AGAIN with no repair_load -> confirm the defect resurfaces (proving the
pre-1.7 gap -- "a reset reverts the chip to unrepaired passthrough" -- was
real) -> repair_load once more -> confirm restored.
"""
import json
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer, with_timeout


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


async def _functional_fail_scan(dut, addr_width, data_width, read_latency):
    """Write solid-0 then solid-1 to every LOGICAL cell via the functional
    port and read back, recording each bit that differs (copied from
    test_onchip_selfrepair.py -- see this module's own header for why)."""
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


async def _reset(dut, assert_cycles=4):
    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.func_csb.value = 1
    dut.func_we.value = 0
    dut.func_addr.value = 0
    dut.func_din.value = 0
    dut.self_repair_start.value = 0
    dut.repair_load.value = 0
    dut.fuse_row_repair_en.value = 0
    dut.fuse_faulty_row_addr.value = 0
    await ClockCycles(dut.clk, assert_cycles)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def _pulse_repair_load(dut, row_repair_en, faulty_row_addr):
    dut.fuse_row_repair_en.value = row_repair_en
    dut.fuse_faulty_row_addr.value = faulty_row_addr
    dut.repair_load.value = 1
    await ClockCycles(dut.clk, 1)
    dut.repair_load.value = 0
    await ClockCycles(dut.clk, 2)
    assert int(dut.repair_load_done.value) == 1, "repair_load_done did not assert after a repair_load pulse"


@cocotb.test()
async def test_onchip_repair_persistence(dut):
    addr_width = int(os.getenv("ADDR_WIDTH", "2"))
    data_width = int(os.getenv("DATA_WIDTH", "4"))
    read_latency = int(os.getenv("READ_LATENCY", "1"))
    # Matches sram_spares_tiny.v's baked-in DEFECT_ADDR/DEFECT_BIT (Step A's
    # DUT, also used by test_onchip_selfrepair_e2e.py): one defect fits in
    # spare slot 0 -- row_repair_en=0b01, faulty_row_addr packs spare0's
    # logical address in its low ADDR_WIDTH bits, spare1 unused (0).
    defect_addr = int(os.getenv("DEFECT_ADDR", "3"))
    known_row_repair_en = 0b01
    known_faulty_row_addr = defect_addr

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    # 1. Reset, then repair_load a known-good signature BEFORE any
    #    self_repair_start or functional access -- proves the load path
    #    itself, independent of ever running a live analyze pass.
    await _reset(dut)
    await _pulse_repair_load(dut, known_row_repair_en, known_faulty_row_addr)
    assert int(dut.self_repair_busy.value) == 0, "no self-repair sequence should have run for this scenario"

    fails = await with_timeout(
        _functional_fail_scan(dut, addr_width, data_width, read_latency), 500_000, "ns"
    )
    for addr, bit in fails:
        print("FAIL_CELL " + json.dumps({"addr": addr, "bit": bit}))
    print(f"FAIL_SCAN_COMPLETE cells={len(fails)}")
    assert fails == [], f"expected an immediately-clean scan after repair_load with no self-repair run, got {fails}"

    # 2. Reset AGAIN, with no repair_load this time -- the pre-1.7 documented
    #    gap ("a reset reverts the chip to unrepaired passthrough") must be
    #    real: the defect resurfaces.
    await _reset(dut)
    fails_after_plain_reset = await with_timeout(
        _functional_fail_scan(dut, addr_width, data_width, read_latency), 500_000, "ns"
    )
    assert fails_after_plain_reset == [(defect_addr, 3)], (
        f"expected the baked-in defect at (addr={defect_addr}, bit=3) to resurface after a reset "
        f"with no repair_load, got {fails_after_plain_reset}"
    )

    # 3. repair_load once more -- confirm the signature restores cleanly,
    #    same result as step 1, not a one-shot fluke.
    await _pulse_repair_load(dut, known_row_repair_en, known_faulty_row_addr)
    fails_restored = await with_timeout(
        _functional_fail_scan(dut, addr_width, data_width, read_latency), 500_000, "ns"
    )
    assert fails_restored == [], f"expected repair_load to restore the clean state, got {fails_restored}"
