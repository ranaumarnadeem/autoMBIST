"""On-chip 2D (row + column) self-repair cocotb testbench.

Self-contained (does NOT import test_mbist/test_onchip_selfrepair -- mirrors
test_onchip_selfrepair.py's own reasoning for staying self-contained). Column
repair is invisible at the functional boundary (repair_remap_col steers bits
transparently underneath func_dout), so the wrapper-level protocol this test
drives is BYTE-IDENTICAL to test_onchip_selfrepair.py's: one self_repair_start
LEVEL held until self_repair_done reads back high, then an INDEPENDENT
functional-port fail-scan verifies the outcome -- never trusting the chip's
own status outputs alone. Same CRITICAL CONSTRAINT applies: never toggle
rst_n a second time between self_repair_done and the verification scan, or
the just-computed repair signature (now including col_repair_en/faulty_bit,
also flops cleared by rst_n) would be silently wiped.
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
    both remaps (func addresses/data go through the same path the controller
    uses), so a correctly-applied row OR column repair reads back clean at the
    repaired cell(s). Identical to test_onchip_selfrepair.py's helper -- column
    repair adds no new functional-boundary surface to scan."""
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
    high, then DROP self_repair_start. Identical to
    test_onchip_selfrepair.py's helper -- see there for the full rationale
    (sticky done/fail outputs, the 2-cycle settle before re-polling on a
    retrigger, ctrl_test_mode_override release)."""
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


# gap_demo's residual-fail set after a partial repair depends on which of the
# fault set's three competing "first sighting" row claims (row 5's word,
# whichever of rows 9/20 is visited first for bit 2, row 30) a given march
# algorithm's address-visitation order happens to leave without a spare --
# see sram_spares_col_gap_demo.v's header for the full case analysis. All
# four outcomes below are independently valid; anything else is a real bug.
GAP_DEMO_VALID_RESIDUALS = [
    {(5, 0), (5, 1)},
    {(9, 2)},
    {(20, 2)},
    {(30, 5)},
]


@cocotb.test()
async def test_onchip_col_repair(dut):
    """One test, gated by COL_REPAIR_SCENARIO, mirroring
    test_onchip_selfrepair.py's single-test-multi-phase convention:

      * COL_REPAIR_SCENARIO=promote -- sram_spares_col_tiny.v (2 defects, same
        bit, 1 spare row + 1 spare col): self_repair_fail must read 0, and the
        re-scan must be completely clean -- the basic "it works" case (row
        claims the first-seen row, column claims the recurrence).
      * COL_REPAIR_SCENARIO=retrigger -- same DUT as promote; runs the
        self-repair sequence TWICE (no reset in between) to prove the
        analyzer's accumulated seen_once/live_row/live_col state gives the
        SAME result both times.
      * COL_REPAIR_SCENARIO=col_contention -- sram_spares_col_contention.v (4
        defects, two same-bit row pairs, 3 spare rows + 1 spare col): proves
        Finding 2's fix (a recurring bit that finds no free column slot falls
        back to wanting a row, instead of going straight to unrepairable) --
        self_repair_fail must read 0 and the re-scan must be completely clean.
      * COL_REPAIR_SCENARIO=simultaneous_bits -- sram_spares_col_simultaneous.v
        (2 rows, both sharing the SAME 2 failing bits, 1 spare row + 2 spare
        cols): proves Finding 1's fix (two bits recurring in the same cycle
        must not race for the same column slot) -- self_repair_fail must read
        0 and the re-scan must be completely clean; a race would leave one of
        the two bits permanently unrepaired.
      * COL_REPAIR_SCENARIO=gap_demo -- sram_spares_col_gap_demo.v (5 faults,
        2 spare rows + 1 spare col): the disclosed-gap proof. bira.py finds a
        complete repair for this exact fault set; the on-chip heuristic
        cannot. self_repair_fail must read 1, AND the re-scan's residual fails
        must be exactly one of GAP_DEMO_VALID_RESIDUALS (never empty, never
        anything else) -- pinning the real severity, not just asserting it in
        prose.
    """
    scenario = os.getenv("COL_REPAIR_SCENARIO", "promote").strip().lower()
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
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    self_repair_fail = await with_timeout(_run_self_repair_once(dut), 500_000, "ns")

    if scenario == "retrigger":
        self_repair_fail_2 = await with_timeout(_run_self_repair_once(dut), 500_000, "ns")
        assert self_repair_fail_2 == self_repair_fail, (
            f"re-triggered self-repair gave a DIFFERENT result ({self_repair_fail_2}) "
            f"than the first run ({self_repair_fail}) against the SAME defects -- "
            "the analyzer's known-defect state was not correctly preserved across the re-trigger"
        )

    fails = await with_timeout(
        _functional_fail_scan(dut, addr_width, data_width, read_latency),
        500_000,
        "ns",
    )
    for addr, bit in fails:
        print("FAIL_CELL " + json.dumps({"addr": addr, "bit": bit}))
    print(f"FAIL_SCAN_COMPLETE cells={len(fails)}")

    if scenario == "gap_demo":
        assert self_repair_fail == 1, "the disclosed-gap fault set must report unrepairable on-chip"
        observed = set(fails)
        assert observed in GAP_DEMO_VALID_RESIDUALS, (
            f"unexpected residual fails after gap_demo's partial repair: {observed} "
            f"-- expected one of {GAP_DEMO_VALID_RESIDUALS}"
        )
    else:
        assert self_repair_fail == 0, f"expected a repairable defect set to succeed, self_repair_fail={self_repair_fail}"
        assert fails == [], f"expected a clean re-scan after successful repair, found {fails}"
