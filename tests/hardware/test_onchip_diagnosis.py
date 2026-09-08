"""On-chip diagnosis log cocotb testbench.

Self-contained (does NOT import test_onchip_selfrepair.py -- this repo's
established convention: every hardware test file in this directory carries
its own copy of shared helpers rather than importing between them, so each
stays runnable/readable in isolation).

Oracle strategy: run `_functional_fail_scan` BEFORE self_repair_start is ever
raised, against the raw unrepaired memory. That is the genuinely independent
ground truth -- a scan taken AFTER self-repair completes can only prove
repair worked (existing tests already do that), it cannot validate what the
diagnosis log itself reported. The functional port goes through the same
remap path the controller uses, but with row_repair_en still all-zero at
that point (no repair has been applied yet), so it reads/writes straight
through to the raw, unrepaired cells.

diag_valid/diag_addr operate at the ROW level (one fail_valid pulse per
row-compare mismatch), not the bit level -- the pre-scan's (addr, bit) pairs
are reduced to their distinct row addresses before comparing against the
decoded diagnosis ports.
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
    and read back, recording each bit that differs."""
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


async def _run_self_repair_once(dut):
    """Pulse-then-hold self_repair_start until self_repair_done reads back
    high, then drop it so ctrl_state settles back to S_IDLE. Mirrors
    test_onchip_selfrepair.py's helper of the same name exactly -- diagnosis
    latches at the SAME point (S_ANALYZE_LATCH) self-repair itself does, so
    this is the correct, only way to drive a pass."""
    dut.self_repair_start.value = 1
    await ClockCycles(dut.clk, 2)
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        if int(dut.self_repair_done.value) == 1:
            break
    self_repair_fail = int(dut.self_repair_fail.value)
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 2)
    return self_repair_fail


def _decode_diag(dut, num_entries, addr_width):
    """diag_valid/diag_addr -> the set of distinct row addresses currently
    latched, using the same LSB-first packing convention
    onchip_row_repair_analyzer/repair_remap_row already use."""
    valid_mask = int(dut.diag_valid.value)
    addr_bus = int(dut.diag_addr.value)
    addr_mask = (1 << addr_width) - 1
    return {
        (addr_bus >> (i * addr_width)) & addr_mask
        for i in range(num_entries)
        if (valid_mask >> i) & 1
    }


@cocotb.test()
async def test_onchip_diagnosis(dut):
    """One test, gated by DIAGNOSIS_SCENARIO, mirroring test_onchip_selfrepair.py's
    single-test-multi-phase convention:

      * DIAGNOSIS_SCENARIO=within_budget -- sram_spares_tiny.v (1 defect, 2
        spares, diagnosis budget sufficient): diag_valid must show exactly
        the pre-scan's one defect row, diag_overflow must read 0.
      * DIAGNOSIS_SCENARIO=beyond_repair_budget -- sram_spares_tiny_2defect.v
        (2 defects), num_spare_rows=1 (repair budget insufficient --
        self_repair_fail=1, only ONE defect actually gets repaired) but
        num_diagnosis_entries=2 (diagnosis budget sufficient): diag_valid
        must show BOTH original defect rows even though row_repair_en (not
        directly observed here, but proven elsewhere) only ever captured
        one -- the differentiator this module exists for.
      * DIAGNOSIS_SCENARIO=retrigger -- sram_spares_tiny.v, run self-repair
        TWICE with no reset in between (mirroring test_onchip_selfrepair.py's
        own retrigger scenario). self_repair_fail must read the same both
        times (repair knowledge persists, unchanged existing behavior), but
        diag_valid after the SECOND pass must read EMPTY -- the memory is
        already transparently repaired, so the second analyze pass observes
        nothing new, and by design the diagnosis log does not remember the
        first pass's finding. This is the core reset-per-pass claim,
        checked directly rather than merely asserted.
      * DIAGNOSIS_SCENARIO=overflow -- sram_spares_tiny_2defect.v,
        num_diagnosis_entries=1 (insufficient for 2 distinct defect rows):
        diag_overflow must assert.
    """
    scenario = os.getenv("DIAGNOSIS_SCENARIO", "within_budget").strip().lower()
    addr_width = int(os.getenv("ADDR_WIDTH", "2"))
    data_width = int(os.getenv("DATA_WIDTH", "4"))
    read_latency = int(os.getenv("READ_LATENCY", "1"))
    num_diagnosis_entries = int(os.getenv("NUM_DIAGNOSIS_ENTRIES", "4"))

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.func_csb.value = 1
    dut.func_we.value = 0
    dut.func_addr.value = 0
    dut.func_din.value = 0
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    pre_scan = await with_timeout(
        _functional_fail_scan(dut, addr_width, data_width, read_latency), 500_000, "ns",
    )
    expected_rows = {addr for addr, _bit in pre_scan}
    print(f"PRE_SCAN_ROWS {sorted(expected_rows)}")
    assert expected_rows, "expected the DUT's baked-in defect(s) to show up in the pre-repair scan"

    self_repair_fail = await with_timeout(_run_self_repair_once(dut), 500_000, "ns")

    if scenario == "beyond_repair_budget":
        assert self_repair_fail == 1, "2 distinct faulty rows / 1 spare must be unrepairable"
    else:
        assert self_repair_fail == 0, f"expected repair to succeed, self_repair_fail={self_repair_fail}"

    if scenario == "retrigger":
        self_repair_fail_2 = await with_timeout(_run_self_repair_once(dut), 500_000, "ns")
        assert self_repair_fail_2 == self_repair_fail, (
            f"re-triggered self-repair gave a DIFFERENT result ({self_repair_fail_2}) "
            f"than the first run ({self_repair_fail})"
        )
        observed_rows = _decode_diag(dut, num_diagnosis_entries, addr_width)
        assert observed_rows == set(), (
            f"expected an EMPTY diagnosis log after a retrigger against an already-"
            f"repaired memory (reset-per-pass design), got {observed_rows}"
        )
    elif scenario == "overflow":
        # By construction more distinct fails than NUM_DIAGNOSIS_ENTRIES can hold --
        # an exact match is impossible BY DESIGN here. What must still hold: the log
        # only ever reports REAL defect rows (never a phantom one) and is genuinely
        # full, not just partially populated.
        observed_rows = _decode_diag(dut, num_diagnosis_entries, addr_width)
        assert observed_rows <= expected_rows, (
            f"diagnosis log reported {observed_rows - expected_rows}, which were never "
            f"in the independently pre-scanned defect set {expected_rows}"
        )
        assert len(observed_rows) == num_diagnosis_entries, (
            f"expected the diagnosis log to be genuinely full ({num_diagnosis_entries} "
            f"entries) when overflowing, got {len(observed_rows)}: {observed_rows}"
        )
    else:
        observed_rows = _decode_diag(dut, num_diagnosis_entries, addr_width)
        assert observed_rows == expected_rows, (
            f"diagnosis log {observed_rows} did not match the independently pre-scanned "
            f"defect rows {expected_rows}"
        )

    overflow = int(dut.diag_overflow.value)
    print(json.dumps({"diag_overflow": overflow, "observed_rows": sorted(observed_rows)}))

    if scenario == "overflow":
        assert overflow == 1, "expected diag_overflow to assert -- more distinct fails than NUM_DIAGNOSIS_ENTRIES could hold"
    else:
        assert overflow == 0, f"expected no diagnosis overflow, got diag_overflow={overflow}"
