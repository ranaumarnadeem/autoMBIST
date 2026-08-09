"""Integrated multi-memory on-chip self-repair proof.

Closes a real, honest gap flagged during ORConf prep: flow/multimem/mbist/
mem_subsystem_mbist.sv (the actual hardened design) is proven PHYSICALLY
(LibreLane: 0 DRT violations, LVS-clean incl. power) and each self-repair
wrapper's RTL FAMILY is proven functionally in isolation
(test_onchip_selfrepair.py), but nobody had ever functionally simulated
self-repair running on multiple memories TOGETHER, integrated, in one design.
LVS/DRC prove the layout matches the netlist -- they say nothing about
whether the netlist's logic (the repair algorithm) is correct.

This test does exactly that, on a small 2-memory analog of the real
subsystem (mem2_subsystem_selfrepair.sv): two differently-sized memories
(4-bit and 5-bit address, matching flow/multimem's "different memories on
one subsystem" character), each with a genuine, DIFFERENT, independently
placed hard defect, repaired via ONE broadcast self_repair_start -- exactly
how the real hardened subsystem's aggregate self_repair_start/_done/_fail
are wired. Proves two things a single-memory test structurally cannot:
  a) self-repair genuinely works when integrated alongside another memory
     under the same shared start/done/fail signals, not just standalone;
  b) the two memories' repairs are independent -- memory B's defect being
     fixed doesn't depend on or interfere with memory A's, and vice versa,
     since each is verified separately and their defects are placed at
     different (addr, bit) coordinates with different stuck-at polarities.
"""
from __future__ import annotations

import json

import cocotb
from cocotb.clock import Clock
from cocotb.handle import Force, Release
from cocotb.triggers import ClockCycles, RisingEdge, Timer, with_timeout


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


async def _functional_fail_scan(dut, dout_handle, mem_sel_value, addr_width, data_width):
    """Same pattern as test_onchip_selfrepair.py's scan, adapted to read back
    from a SPECIFIC memory's own func_dout (bypassing the top's read mux)
    while driving writes/reads through the shared bus with mem_sel held at
    this memory's slot value."""
    depth = 1 << addr_width
    data_mask = (1 << data_width) - 1
    hold = 1 + 4  # matches sram_spares_intmem_{a,b}.v's 1-cycle registered read
    fails = set()
    dut.mem_sel.value = mem_sel_value
    for pattern in (0, data_mask):
        for addr in range(depth):
            dut.csb.value = 0
            dut.we.value = 1
            dut.addr.value = addr
            dut.wdata.value = pattern
            await ClockCycles(dut.clk, 2)
        dut.csb.value = 1
        dut.we.value = 0
        await ClockCycles(dut.clk, 2)
        for addr in range(depth):
            dut.csb.value = 0
            dut.we.value = 0
            dut.addr.value = addr
            await ClockCycles(dut.clk, hold)
            await Timer(1, unit="ns")
            read_word = _safe_int(dout_handle)
            if read_word is None:
                # An undefined (X/Z) read is not "no information" -- it's a
                # failure at every bit of this address. sram_spares_intmem_*
                # are 1-cycle registered models that hold their value
                # indefinitely (see `hold` above), so they never legitimately
                # decay to X the way a real OpenRAM macro's behavioral model
                # does; any X seen here means a repair bug left this cell
                # genuinely undriven (e.g. a broken spare-row read mux), not
                # a benign sampling-timing artifact.
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
        dut.csb.value = 1
        await ClockCycles(dut.clk, 2)
    return sorted(fails)


async def _run_self_repair_once(dut):
    """Identical driving pattern to test_onchip_selfrepair.py's helper: pulse
    then hold self_repair_start until the AGGREGATE self_repair_done reads
    high (both memories done), then drop it."""
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


@cocotb.test()
async def test_two_memories_self_repair_independently(dut):
    """Both slot A (addr=5,bit=2,SA1) and slot B (addr=20,bit=6,SA0) have a
    genuine, DIFFERENT baked-in defect. One broadcast self_repair_start must
    autonomously repair BOTH -- proven by scanning each memory's OWN
    functional port independently after the shared repair sequence
    completes, never trusting the aggregate self_repair_fail/_done alone."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.csb.value = 1
    dut.we.value = 0
    dut.mem_sel.value = 0
    dut.addr.value = 0
    dut.wdata.value = 0
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    self_repair_fail = await with_timeout(_run_self_repair_once(dut), 1_000_000, "ns")
    assert self_repair_fail == 0, (
        f"both memories' defects fit their own single spare row -- expected "
        f"self_repair_fail=0, got {self_repair_fail}"
    )

    fails_a = await with_timeout(
        _functional_fail_scan(dut, dut.func_dout_a, 0, addr_width=4, data_width=8), 1_000_000, "ns"
    )
    for addr, bit in fails_a:
        print("FAIL_CELL_A " + json.dumps({"addr": addr, "bit": bit}))
    print(f"FAIL_SCAN_A_COMPLETE cells={len(fails_a)}")

    fails_b = await with_timeout(
        _functional_fail_scan(dut, dut.func_dout_b, 1, addr_width=5, data_width=8), 1_000_000, "ns"
    )
    for addr, bit in fails_b:
        print("FAIL_CELL_B " + json.dumps({"addr": addr, "bit": bit}))
    print(f"FAIL_SCAN_B_COMPLETE cells={len(fails_b)}")

    assert fails_a == [], (
        f"memory A (defect was addr=5,bit=2) still shows failures after the "
        f"shared self-repair sequence: {fails_a}"
    )
    assert fails_b == [], (
        f"memory B (defect was addr=20,bit=6) still shows failures after the "
        f"shared self-repair sequence: {fails_b} -- if A is clean but B is not "
        f"(or vice versa), the two memories' repairs are NOT independent"
    )


@cocotb.test()
async def test_functional_fail_scan_detects_undefined_reads(dut):
    """Positive control for _functional_fail_scan: proves the scan is
    sensitive to X/Z, not just wrong DATA (data-mismatch sensitivity is
    already exercised above by the two memories' real baked-in stuck-at
    defects). Forces func_dout_a permanently to X -- standing in for an
    unrepaired/undriven read (e.g. a broken spare-row read mux), the class of
    defect a real repair bug would produce, as opposed to the stuck-at bits
    the main test exercises. Must report every (addr, bit) as failing; before
    this fix _functional_fail_scan's `if read_word is None: continue`
    silently dropped every undefined read instead, so this scan would have
    reported zero failures against a fully-undriven memory."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.csb.value = 1
    dut.we.value = 0
    dut.mem_sel.value = 0
    dut.addr.value = 0
    dut.wdata.value = 0
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    dut.func_dout_a.value = Force("x" * 8)
    fails_a = await with_timeout(
        _functional_fail_scan(dut, dut.func_dout_a, 0, addr_width=4, data_width=8), 1_000_000, "ns"
    )
    dut.func_dout_a.value = Release()

    expected = {(addr, bit) for addr in range(16) for bit in range(8)}
    assert set(fails_a) == expected, (
        f"forced func_dout_a to X for every address/bit but the scan reported "
        f"{sorted(fails_a)} -- an undefined (X/Z) read must be flagged as a "
        f"failure at every bit of that address, not silently skipped"
    )
