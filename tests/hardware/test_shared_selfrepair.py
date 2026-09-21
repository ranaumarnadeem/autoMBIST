"""Shared-bus + on-chip self-repair cocotb testbench
(docs/shared-hierarchical-mbist-plan.md §9b).

Self-contained, mirroring test_onchip_selfrepair.py's own pattern and its
CRITICAL CONSTRAINT (never toggle rst_n between self_repair_done and
verification -- the repair signature lives in flops rst_n clears).

Unlike the single-memory self-repair test, the functional port (func_*) is
NOT usable as an independent per-memory fail-scan here: it is time-muxed by
the SAME mem_sel_q the BIST sequencer owns, so it can only ever reach
whichever ONE memory the sequencer last selected -- an accurate reflection
of what a real shared bus can do, not a test limitation to work around.
Verification instead combines two independent signals, neither of which is
just "trust the chip's own status pins":

  1. self_repair_fail_arr[i] for EVERY memory i (a per-instance internal
     signal each memory's own onchip_selfrepair_ctrl drives independently --
     reading each one separately, not just the aggregate self_repair_fail
     OR, is what actually proves cross-memory isolation: a design that
     accidentally shared state between instances could still OR to 0 by
     coincidence on a single-defect scenario).
  2. A completely separate, ordinary bist_start-driven verify pass through
     the SAME shared-bus sequencer (test_mode=1, self-repair mode NOT
     entered) -- re-runs u_algo_top against every memory through its
     (possibly just-repaired) physical address and checks the aggregate
     bist_fail reads 0. This exercises the repaired remap through the exact
     path a real tester would use, independent of the self-repair FSM's own
     internal re-verify pass.
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer, with_timeout


async def _run_self_repair_once(dut):
    """See test_onchip_selfrepair.py's _run_self_repair_once -- identical
    pulse-then-hold-then-poll-then-drop protocol; self_repair_start/_done
    are wrapper-level ports with the same LEVEL contract regardless of
    topology."""
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


async def _run_verify_bist_pass(dut):
    """An ordinary tester-driven pass (test_mode + bist_start), independent
    of self-repair, through the same shared-bus sequencer. bist_done only
    fires once EVERY memory's own pass has completed (SHB_DONE), so this
    alone sweeps all memories."""
    dut.test_mode.value = 1
    dut.bist_start.value = 1
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        if int(dut.bist_done.value) == 1:
            break
    bist_fail = int(dut.bist_fail.value)
    dut.bist_start.value = 0
    dut.test_mode.value = 0
    await ClockCycles(dut.clk, 2)
    return bist_fail


@cocotb.test()
async def test_shared_selfrepair(dut):
    """SCENARIO selects which memory bank(s) carry a baked-in defect (via a
    defparam override compiled alongside this wrapper -- see
    test_shared_bus_selfrepair_e2e.py):

      * bank0 -- only mem_bank0 defective, mem_bank1 clean.
      * bank1 -- only mem_bank1 defective, mem_bank0 clean (bank-order
        swapped from `bank0`, catching an indexing/order-dependent bug the
        symmetric case alone could hide).
      * both  -- BOTH memories independently defective (distinct defects,
        each within its own spare budget) -- proves the per-memory
        analyzer/ctrl/remap instances repair their own memory in the SAME
        self-repair pass, not just one at a time.

    Every scenario must end with self_repair_fail == 0, EVERY
    self_repair_fail_arr[i] == 0, and a subsequent independent verify BIST
    pass reading bist_fail == 0.
    """
    num_memories = int(os.getenv("NUM_MEMORIES", "2"))

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
    assert self_repair_fail == 0, f"expected every repairable defect to succeed, self_repair_fail={self_repair_fail}"

    per_memory_fail = [int(dut.self_repair_fail_arr[i].value) for i in range(num_memories)]
    assert per_memory_fail == [0] * num_memories, (
        f"per-memory self_repair_fail_arr={per_memory_fail} -- at least one memory's OWN "
        "analyzer/ctrl instance reported failure even though the aggregate self_repair_fail read 0"
    )

    verify_bist_fail = await with_timeout(_run_verify_bist_pass(dut), 500_000, "ns")
    assert verify_bist_fail == 0, (
        f"independent tester-driven verify pass found bist_fail={verify_bist_fail} after self-repair -- "
        "the chip's own self_repair_fail=0 status did not match a real re-run through the repaired remap"
    )
