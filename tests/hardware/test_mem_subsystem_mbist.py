"""Functional self-repair proof for flow/multimem/mbist/mem_subsystem_mbist.sv
-- the ACTUAL hardened 3-memory subsystem (0 DRT, LVS-clean in LibreLane),
here simulated for the first time with self-repair actually running, against
the REAL OpenRAM sky130 macro adapters (sram_wrap_a/b/c over the behavioral
views of the exact macros that hardened).

Why this exists: the subsystem was proven PHYSICALLY (DRC/LVS) but its
self-repair was only ever elaboration-checked, never simulated -- LVS/DRC say
the layout matches the netlist, nothing about whether the netlist's logic
actually works. When it finally was simulated (first on the flow/soc sibling,
now here on all three macros), it exposed a real bug: the generator's default
READ_LATENCY=1 for the internal march-C engine is tuned for this project's toy
behavioral fixtures (dout stays registered indefinitely once set) and is WRONG
for OpenRAM's real macro model, whose dout0 is forced back to X shortly after
every clock edge (`#(T_HOLD) dout0 = 32'bx;` in the vendor .v) and only
refreshed by an actual read. READ_LATENCY=1 samples one cycle too late, right
after that decay, so march-C sees X on the first read-back and declares a
phantom failure on a defect-free macro. read_latency: 0 (see
run_mem_subsystem_mbist_tb.py's generator config) samples at the correct edge.

Real macros have NO defect-injection knob pre-silicon (this project's
established limit), so this is not a defect-correction proof -- it proves:
  a) self-repair's own BIST runs CLEAN on all three real macros (no phantom
     unrepairable) and triggers no phantom repair (row_repair_en stays 0);
  b) ordinary functional bus access still round-trips correctly on every slot
     after the self-repair sequence -- through the real macro, the repair
     remap, and the muxed subsystem bus.
"""
from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer, with_timeout

# (mem_sel, logical addr width, data width) per slot -- matches
# mem_subsystem_mbist.sv's three wrappers.
SLOTS = [
    (0, 8, 32),    # selfrepair_a : sky130_sram_32b256w
    (1, 9, 32),    # selfrepair_b : sky130_sram_32b512w
    (2, 10, 8),    # selfrepair_c : sky130_sram_8b1024w
]

# OpenRAM behavioral-model read timing (same as test_mem_subsystem.py): op
# registered at posedge, executes at the following negedge, dout valid
# #DELAY(3) after that negedge. With a 10 ns clock, sample at negedge+4.
DATA_VALID_NS = 4


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


def pattern(sel: int, addr: int, width: int) -> int:
    return (0x9E3779B1 * (addr + 1) + 0x85EBCA77 * (sel + 1)) & ((1 << width) - 1)


async def bus_write(dut, sel: int, addr: int, data: int) -> None:
    dut.mem_sel.value = sel
    dut.addr.value = addr
    dut.wdata.value = data
    dut.we.value = 1
    dut.csb.value = 0
    await RisingEdge(dut.clk)
    dut.csb.value = 1
    dut.we.value = 0
    await FallingEdge(dut.clk)
    await Timer(1, unit="ns")


async def bus_read(dut, sel: int, addr: int) -> int:
    dut.mem_sel.value = sel
    dut.addr.value = addr
    dut.we.value = 0
    dut.csb.value = 0
    await RisingEdge(dut.clk)
    dut.csb.value = 1
    await FallingEdge(dut.clk)
    await Timer(DATA_VALID_NS, unit="ns")
    return int(dut.rdata.value)


@cocotb.test()
async def test_subsystem_self_repair_clean_on_real_macros(dut):
    """Self-repair runs clean on all three real macros (no phantom fail/repair),
    then ordinary functional access still works on every slot."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.csb.value = 1
    dut.we.value = 0
    dut.mem_sel.value = 0
    dut.addr.value = 0
    dut.wdata.value = 0
    dut.self_repair_start.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    # Run the broadcast self-repair sequence; hold start until aggregate done.
    dut.self_repair_start.value = 1
    await ClockCycles(dut.clk, 2)

    async def _wait_done():
        while True:
            await RisingEdge(dut.clk)
            await Timer(1, unit="ns")
            if int(dut.self_repair_done.value) == 1:
                return int(dut.self_repair_fail.value)

    self_repair_fail = await with_timeout(_wait_done(), 4_000_000, "ns")
    assert self_repair_fail == 0, (
        f"none of the three real macros is actually defective -- expected "
        f"self_repair_fail=0, got {self_repair_fail} (if this regressed, check "
        "read_latency: 0 in run_mem_subsystem_mbist_tb.py -- the default of 1 is "
        "wrong for the real macro timing; see this module's docstring)"
    )

    # No phantom repair should have been latched on any defect-free macro.
    for inst in ("u0", "u1", "u2"):
        en = _safe_int(getattr(dut, inst).row_repair_en)
        assert en == 0, f"{inst}: phantom repair latched (row_repair_en={en}) on a defect-free macro"

    # Release self-repair and let the controllers return to idle (mux back to
    # the functional port).
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 4)
    assert int(dut.self_repair_busy.value) == 0, "self_repair_busy should have cleared"

    # Functional access on every slot must still round-trip through the real
    # macro + repair remap + subsystem bus.
    for sel, logical_aw, dw in SLOTS:
        top = (1 << logical_aw) - 1
        addrs = [0, 1, top // 2, top]
        for a in addrs:
            await bus_write(dut, sel, a, pattern(sel, a, dw))
        for a in addrs:
            got = await bus_read(dut, sel, a)
            exp = pattern(sel, a, dw)
            assert got == exp, (
                f"slot {sel} addr 0x{a:x} after self-repair: read 0x{got:x}, expected 0x{exp:x}"
            )
