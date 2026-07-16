"""cocotb test for flow/multimem/mem_subsystem.sv -- three differently-sized
OpenRAM sky130 macros (behavioral models) behind one synchronous bus.

Run via tests/hardware/run_mem_subsystem_tb.py (see that file's header for the
exact invocation), e.g.:

    wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \
        PYTHONPATH=src ~/cocotb/bin/python tests/hardware/run_mem_subsystem_tb.py"

Proves three things about the subsystem:
  a) PER-SLOT INTEGRITY: writes/reads round-trip on every slot at boundary
     addresses -- 0, 1, mid, the logical top, AND the first spare-row address
     above the logical top (the repair target row is plainly addressable).
  b) ISOLATION: the same bus address written to all three slots with distinct
     data reads back correctly per slot (per-slot csb gating; no cross-write).
  c) PIPELINED SELECT: back-to-back single-cycle reads that switch mem_sel
     every cycle each return their own slot's data -- i.e. the 1-cycle sel_q
     delay in the read mux exactly matches the macros' registered-read latency.
"""
from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

# (sel, logical addr bits, data bits) per slot. "Logical" excludes the
# spare-row region; the macro ADDR_WIDTH is one bit wider where the spare
# region begins at 1 << logical_aw.
SLOTS = [
    (0, 8, 32),    # sky130_sram_32b256w : 256 words x 32b
    (1, 9, 32),    # sky130_sram_32b512w : 512 words x 32b
    (2, 10, 8),    # sky130_sram_8b1024w : 1024 words x 8b
]

# OpenRAM behavioral-model timing: inputs are registered at POSedge, the
# write/read executes at the following NEGedge, and dout updates #DELAY(3)
# after that negedge (dout is also X'd shortly after every posedge). With a
# 10 ns clock: op latched at t, data valid at t+8 -- so sample at
# negedge + DATA_VALID_NS, which is t+9, safely before the next posedge.
DATA_VALID_NS = 4


def pattern(sel: int, addr: int, width: int) -> int:
    """Deterministic, distinct data per (slot, address), truncated to width."""
    return (0x9E3779B1 * (addr + 1) + 0x85EBCA77 * (sel + 1)) & ((1 << width) - 1)


async def _init(dut) -> None:
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.csb.value = 1
    dut.we.value = 0
    dut.mem_sel.value = 0
    dut.addr.value = 0
    dut.wdata.value = 0
    await RisingEdge(dut.clk)


async def bus_write(dut, sel: int, addr: int, data: int) -> None:
    dut.mem_sel.value = sel
    dut.addr.value = addr
    dut.wdata.value = data
    dut.we.value = 1
    dut.csb.value = 0
    await RisingEdge(dut.clk)   # op registered here
    dut.csb.value = 1
    dut.we.value = 0
    await FallingEdge(dut.clk)  # write executes here
    await Timer(1, unit="ns")


async def bus_read(dut, sel: int, addr: int) -> int:
    dut.mem_sel.value = sel
    dut.addr.value = addr
    dut.we.value = 0
    dut.csb.value = 0
    await RisingEdge(dut.clk)   # op registered here
    dut.csb.value = 1
    await FallingEdge(dut.clk)  # read executes here; dout <= #DELAY mem[...]
    await Timer(DATA_VALID_NS, unit="ns")
    return int(dut.rdata.value)


@cocotb.test()
async def per_slot_integrity(dut):
    """Boundary-address write/read round-trips on every slot (incl. spare row)."""
    await _init(dut)
    for sel, logical_aw, dw in SLOTS:
        top = (1 << logical_aw) - 1
        spare0 = 1 << logical_aw  # first word of the spare-row region
        addrs = [0, 1, top // 2, top, spare0]
        for a in addrs:
            await bus_write(dut, sel, a, pattern(sel, a, dw))
        for a in addrs:
            got = await bus_read(dut, sel, a)
            exp = pattern(sel, a, dw)
            assert got == exp, (
                f"slot {sel} addr 0x{a:x}: read 0x{got:x}, expected 0x{exp:x}"
            )


@cocotb.test()
async def cross_slot_isolation(dut):
    """Same bus address, three slots, distinct data -- no cross-write."""
    await _init(dut)
    shared = 0x2A
    for sel, _aw, dw in SLOTS:
        await bus_write(dut, sel, shared, pattern(sel, shared, dw))
    for sel, _aw, dw in SLOTS:
        got = await bus_read(dut, sel, shared)
        exp = pattern(sel, shared, dw)
        assert got == exp, (
            f"isolation broken: slot {sel} addr 0x{shared:x} read 0x{got:x}, "
            f"expected 0x{exp:x}"
        )


@cocotb.test()
async def pipelined_select_switching(dut):
    """A new read on a different slot every cycle; sel_q must track latency."""
    await _init(dut)
    addr = 0x11
    for sel, _aw, dw in SLOTS:
        await bus_write(dut, sel, addr, pattern(sel, addr, dw))

    # Back-to-back reads with no idle cycles: csb stays asserted, mem_sel
    # changes every cycle. Each posedge latches one op (and sel_q); the op
    # executes at the following negedge and its data must be on rdata before
    # the next posedge -- sampled at negedge + DATA_VALID_NS.
    dut.we.value = 0
    dut.csb.value = 0
    dut.addr.value = addr
    for sel in [0, 1, 2, 1, 0, 2, 2, 0, 1]:
        dut.mem_sel.value = sel
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        await Timer(DATA_VALID_NS, unit="ns")
        dw = SLOTS[sel][2]
        got = int(dut.rdata.value)
        exp = pattern(sel, addr, dw)
        assert got == exp, (
            f"pipelined read sel={sel}: got 0x{got:x}, expected 0x{exp:x}"
        )
    dut.csb.value = 1
