"""Shared-bus + on-chip self-repair cocotb testbench
(docs/shared-hierarchical-mbist-plan.md §9b).

Self-contained, mirroring test_onchip_selfrepair.py's own pattern and its
CRITICAL CONSTRAINT (never toggle rst_n between self_repair_done and
verification -- the repair signature lives in flops rst_n clears).

Verification combines three independent signals, none of which is just
"trust the chip's own status pins":

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
  3. Mission mode (test_mode=0): every memory is written and read back
     through its OWN func_*_<name> port, each with its own data. Every
     memory must be reachable after a BIST run, keep its own contents, and
     read back clean through its repaired remap.
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer, with_timeout


def _memory_names(num_memories):
    names = os.getenv("MEMORY_NAMES")
    if names:
        return names.split(",")
    return [f"mem_bank{i}" for i in range(num_memories)]


def _func(dut, signal, name):
    return getattr(dut, f"func_{signal}_{name}")


def _mission_word(index, addr, data_mask):
    """A different word per memory at the same address, so a read that
    reaches the wrong memory shows up as a mismatch."""
    return (addr * 5 + index * 3 + 1) & data_mask


async def _mission_mode_mismatches(dut, names):
    """Write every word of every memory through its own functional port, then
    read every word back the same way. Returns [(memory, addr, read, expected)]."""
    addr_width = len(_func(dut, "addr", names[0]))
    data_mask = (1 << len(_func(dut, "din", names[0]))) - 1
    depth = 1 << addr_width
    dut.test_mode.value = 0
    for index, name in enumerate(names):
        for addr in range(depth):
            _func(dut, "csb", name).value = 0
            _func(dut, "we", name).value = 1
            _func(dut, "addr", name).value = addr
            _func(dut, "din", name).value = _mission_word(index, addr, data_mask)
            await ClockCycles(dut.clk, 1)
        _func(dut, "csb", name).value = 1
        _func(dut, "we", name).value = 0
    await ClockCycles(dut.clk, 2)
    mismatches = []
    for index, name in enumerate(names):
        for addr in range(depth):
            _func(dut, "csb", name).value = 0
            _func(dut, "we", name).value = 0
            _func(dut, "addr", name).value = addr
            await ClockCycles(dut.clk, 3)
            await Timer(1, unit="ns")
            raw = _func(dut, "dout", name).value
            read = int(raw) if raw.is_resolvable else None
            expected = _mission_word(index, addr, data_mask)
            if read != expected:
                mismatches.append((name, addr, read, expected))
        _func(dut, "csb", name).value = 1
    await ClockCycles(dut.clk, 2)
    return mismatches


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
    names = _memory_names(num_memories)

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    for name in names:
        _func(dut, "csb", name).value = 1
        _func(dut, "we", name).value = 0
        _func(dut, "addr", name).value = 0
        _func(dut, "din", name).value = 0
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    self_repair_fail = await with_timeout(_run_self_repair_once(dut), 500_000, "ns")
    assert self_repair_fail == 0, f"expected every repairable defect to succeed, self_repair_fail={self_repair_fail}"

    # self_repair_fail_arr is a packed logic [N-1:0]: memory i is bit i.
    fail_vec = int(dut.self_repair_fail_arr.value)
    per_memory_fail = [(fail_vec >> i) & 1 for i in range(num_memories)]
    assert per_memory_fail == [0] * num_memories, (
        f"per-memory self_repair_fail_arr={per_memory_fail} -- at least one memory's OWN "
        "analyzer/ctrl instance reported failure even though the aggregate self_repair_fail read 0"
    )

    verify_bist_fail = await with_timeout(_run_verify_bist_pass(dut), 500_000, "ns")
    assert verify_bist_fail == 0, (
        f"independent tester-driven verify pass found bist_fail={verify_bist_fail} after self-repair -- "
        "the chip's own self_repair_fail=0 status did not match a real re-run through the repaired remap"
    )

    # #45: run self-repair twice more without a reset. Every memory still holds
    # the first run's sticky done; the sequencer must not take that as this
    # run's, or it moves the bus on while a controller is still running -- which
    # then analyzes another memory's reads and burns spares on a good memory.
    repairs = _repair_state(dut)
    for rerun in (2, 3):
        rerun_fail = await with_timeout(_run_self_repair_once(dut), 500_000, "ns")
        assert rerun_fail == 0, f"self-repair run {rerun} without reset reported self_repair_fail={rerun_fail}"
        # The last memory's controller returns to idle a few cycles after
        # self_repair_start drops (sequencer SHB_DONE -> SHB_IDLE, then its
        # own start input falls); a controller left running is busy for
        # a whole pass, far longer than this.
        await ClockCycles(dut.clk, 8)
        busy = int(dut.self_repair_busy_arr.value)
        assert busy == 0, f"run {rerun}: self_repair_done rose while controllers were still busy ({busy:#b})"
        assert _repair_state(dut) == repairs, (
            f"run {rerun} changed the repair state {repairs} -> {_repair_state(dut)}: spares used on a re-run"
        )
    verify_bist_fail = await with_timeout(_run_verify_bist_pass(dut), 500_000, "ns")
    assert verify_bist_fail == 0, f"verify pass after the re-runs found bist_fail={verify_bist_fail}"

    # Mission mode after BIST: each memory through its own port. The shared
    # sequencer has just left mem_sel_q on the last memory, which used to be
    # the only one the functional port could reach.
    mismatches = await with_timeout(_mission_mode_mismatches(dut, names), 500_000, "ns")
    assert mismatches == [], (
        "mission-mode write/read through each memory's own port failed "
        f"(memory, addr, read, expected): {mismatches}"
    )


def _repair_state(dut) -> dict[str, int]:
    """Every memory's spare enables, packed as the wrapper holds them."""
    state = {"row": int(dut.row_repair_en_arr.value)}
    if hasattr(dut, "col_repair_en_arr"):
        state["col"] = int(dut.col_repair_en_arr.value)
    return state
