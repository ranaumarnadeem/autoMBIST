"""Step-E on-chip self-repair cocotb testbench.

Self-contained (does NOT import test_mbist -- that module's import-time
_prepare_fault_files() would fire against the wrong config). Drives the
generated on-chip-self-repair wrapper's boundary directly: one
`self_repair_start` LEVEL (held until `self_repair_done` reads back high, then
DROPPED so the wrapper's test_mode mux releases back to the functional port --
this is required, not optional: ctrl_test_mode_override stays asserted for as
long as self_repair_start is held, since S_DONE is not S_IDLE), then an
INDEPENDENT functional-port fail-scan (reusing the exact pattern from
test_repair_row.py) verifies the outcome -- never trusting the chip's own
status outputs alone.

CRITICAL CONSTRAINT, unlike test_repair_row.py/test_repair_loop.py: this test
must NEVER toggle rst_n a second time between self_repair_done and the
verification scan. Those existing tests' mid-run reset is harmless there only
because their repair signature lives on combinational testbench-driven pins,
immune to rst_n; here the signature lives in flops *cleared by* rst_n -- a
copy-pasted reset would silently wipe the just-computed repair right before
checking it.
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
    a correctly-applied repair reads back clean at the repaired cell(s)."""
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
    """Mirrors test_repair_row.py's tester-side poll -- used only by the
    held_bist_start scenario to drive a genuine tester-owned BIST run before
    self_repair_start ever gets raised."""
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        if int(dut.bist_done.value) == 1:
            return


async def _run_self_repair_once(dut):
    """Pulse-then-hold self_repair_start until self_repair_done reads back
    high (polled every clock edge, mirroring the existing _wait_bist_done
    pattern -- required now that these outputs are sticky, not merely
    defensive), then DROP self_repair_start so ctrl_state settles back to
    S_IDLE (releasing ctrl_test_mode_override, and thus effective_test_mode,
    back to the external test_mode pin -- held at 0 throughout this whole
    test, since there is no tester in an autonomous self-repair scenario).

    Returns self_repair_fail's value. Does NOT touch rst_n -- the repair
    signature (row_repair_en/faulty_row_addr, latched inside
    onchip_row_repair_analyzer) survives this, and survives a later re-trigger
    too: the analyzer's known-defect state accumulates for the chip's
    lifetime and is cleared ONLY by rst_n, never by a fresh
    self_repair_start (see onchip_row_repair_analyzer.sv's header comment --
    a re-triggered analyze pass runs through the already-repaired memory and
    would otherwise "correctly" observe no fault, silently erasing a
    still-valid repair).

    On a RE-trigger (this helper called a second time in one simulation),
    self_repair_done is still HIGH from the previous run at the instant
    self_repair_start is raised again -- it is only cleared once ctrl_state
    actually reaches S_ANALYZE_KICK, one cycle later. Polling for "done" without
    first waiting past that point would see the stale prior-run value and
    return immediately, before the new run has done anything at all. So: wait
    2 cycles (past S_IDLE -> S_ANALYZE_KICK, where self_repair_done is cleared)
    before polling for the NEW completion.
    """
    dut.self_repair_start.value = 1
    await ClockCycles(dut.clk, 2)
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        if int(dut.self_repair_done.value) == 1:
            break
    self_repair_fail = int(dut.self_repair_fail.value)
    dut.self_repair_start.value = 0
    # Let ctrl_state genuinely settle back to S_IDLE (S_DONE -> S_IDLE takes
    # one edge) before anything relies on effective_test_mode having dropped.
    await ClockCycles(dut.clk, 2)
    return self_repair_fail


async def _run_held_bist_start_scenario(dut):
    """Reproduces a tester that holds bist_start through its own run's
    completion and then ALSO raises self_repair_start before ever releasing
    it -- the exact race the S_IDLE `!bist_done` arbitration term exists for
    (see onchip_selfrepair_ctrl.sv's header comment).

    First runs a real tester-driven BIST pass (test_mode/bist_start held,
    mirroring test_repair_row.py's tester flow) against the same defect
    `repairable` uses. Once bist_done reads high, bist_start is deliberately
    NOT dropped -- self_repair_start is raised on top of it instead.

    Proves the negative first: self-repair must NOT falsely complete while
    the tester still holds bist_start -- self_repair_busy and
    self_repair_done must both read 0 for several cycles, even though
    bist_done itself is already (stale-)high. Only once the tester finally
    relinquishes control (bist_start dropped) can self-repair legitimately
    proceed; that run must be a genuine one, polled and returned exactly like
    _run_self_repair_once's tail.
    """
    dut.test_mode.value = 1
    dut.bist_start.value = 1
    await with_timeout(_wait_bist_done(dut), 500_000, "ns")

    # bist_start is deliberately held past bist_done -- this is the reproduction.
    dut.self_repair_start.value = 1
    for _ in range(20):
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        assert int(dut.self_repair_busy.value) == 0, (
            "self-repair entered ANALYZE while the tester still held bist_start -- "
            "the S_IDLE !bist_done guard did not block it"
        )
        assert int(dut.self_repair_done.value) == 0, (
            "self-repair falsely reported done while the tester still held bist_start"
        )

    # The tester finally relinquishes control: effective_bist_start genuinely
    # drops, letting the algo FSM's stale ST_DONE settle back to ST_IDLE
    # (bist_done deasserts) before self-repair's own genuine run kicks off.
    dut.bist_start.value = 0
    dut.test_mode.value = 0

    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        if int(dut.self_repair_done.value) == 1:
            break
    self_repair_fail = int(dut.self_repair_fail.value)
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 2)
    return self_repair_fail


@cocotb.test()
async def test_onchip_selfrepair(dut):
    """One test, gated by SELFREPAIR_SCENARIO, mirroring test_repair_row.py's
    single-test-multi-phase convention:

      * SELFREPAIR_SCENARIO=repairable   -- sram_spares_tiny.v (1 defect, 2
        spares): self_repair_fail must read 0, and the independent re-scan
        must be completely clean.
      * SELFREPAIR_SCENARIO=retrigger    -- same DUT; runs the self-repair
        sequence TWICE in one simulation (drop low, re-raise) to prove the
        analyzer's accumulate-for-the-chip's-lifetime defect state gives the
        SAME repair result both times, not a fresh (and wrong) empty
        signature on the second pass.
      * SELFREPAIR_SCENARIO=partial      -- sram_spares_tiny_2defect.v (2
        defects, 1 spare): self_repair_fail must read 1 (unrepairable), AND
        the independent re-scan must show EXACTLY ONE of the two original
        defect coordinates still failing -- proving the documented
        fail-open-partially behaviour is real, not just asserted.
      * SELFREPAIR_SCENARIO=held_bist_start -- sram_spares_tiny.v (same DUT as
        `repairable`). A tester runs its OWN bist_start-driven march first
        (mirroring test_repair_row.py's tester flow) and, unlike every other
        scenario here, does NOT drop bist_start after bist_done reads high --
        it stays held while self_repair_start is also raised. Regression
        guard for the S_IDLE `!bist_done` arbitration term
        (onchip_selfrepair_ctrl.sv): first proves self-repair does NOT
        falsely complete while the tester still owns bist_start (self_repair_
        busy/done must stay 0 for several cycles), THEN the tester finally
        relinquishes (bist_start dropped) and self-repair must still reach a
        genuine, correct repair -- exactly like `repairable`.
    """
    scenario = os.getenv("SELFREPAIR_SCENARIO", "repairable").strip().lower()
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

    if scenario == "held_bist_start":
        self_repair_fail = await with_timeout(_run_held_bist_start_scenario(dut), 500_000, "ns")
    else:
        self_repair_fail = await with_timeout(_run_self_repair_once(dut), 500_000, "ns")

    if scenario == "retrigger":
        # Re-run the ENTIRE sequence a second time, no reset in between --
        # the analyzer's known-defect state must PERSIST (not clear) across
        # this re-trigger, since the remap is always active and the second
        # analyze pass runs through the already-repaired memory. If that
        # state were wrongly cleared at the start of the new pass, the
        # second pass would see no fault (it's already fixed) and latch an
        # EMPTY signature, erasing the still-valid repair.
        self_repair_fail_2 = await with_timeout(_run_self_repair_once(dut), 500_000, "ns")
        assert self_repair_fail_2 == self_repair_fail, (
            f"re-triggered self-repair gave a DIFFERENT result ({self_repair_fail_2}) "
            f"than the first run ({self_repair_fail}) against the SAME defect -- "
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

    if scenario == "partial":
        assert self_repair_fail == 1, "2 distinct faulty rows / 1 spare must be unrepairable"
        remaining = set(fails)
        known_defects = {(3, 3), (1, 1)}
        assert remaining, "expected exactly one defect to remain uncorrected, found none"
        assert remaining <= known_defects, f"unexpected remaining fails: {remaining}"
        assert len(remaining) == 1, (
            f"expected exactly ONE of {known_defects} to remain after a partial "
            f"repair (1 spare, 2 defects), got {remaining}"
        )
    else:
        assert self_repair_fail == 0, f"expected a repairable defect to succeed, self_repair_fail={self_repair_fail}"
        assert fails == [], f"expected a clean re-scan after successful repair, found {fails}"
