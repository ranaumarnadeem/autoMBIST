"""Column-repair STEERING-DISTINCTNESS proof (Workstream M.1).

Proves the spare column is genuinely separate physical storage -- the property
that actually matters for BISR, since a broken column remap that is secretly a
no-op passthrough would still pass an ordinary read-after-write check.

Deliberately parameterized entirely by env (widths, target address, target bit,
markers) so the SAME testbench serves both the behavioral DUT
(sram_spares_col_tiny.v) and a REAL OpenRAM-compiled macro with a spare column.

WHY THIS DIFFERS FROM THE ROW PROOF (test_repair_row_real_macro.py). That one
writes MARKER_A with repair off, MARKER_B with repair on, then drops repair and
confirms MARKER_A survived -- the repaired write landed on a different physical
ROW, leaving the original untouched. That logic does NOT transfer to columns:
repair_remap_col writes the logical lane UNCONDITIONALLY (a stock macro has no
per-bit write disable; see the module header), so the logical lane always tracks
the most recent write and there is nothing left to compare.

The column-side equivalent exploits spare_wen instead -- the spare lane is
written ONLY while col_repair_en is high. With faulty_bit held at TARGET_BIT
throughout and only col_repair_en toggling:

  1. repair ON : write MARKER_A (TARGET_BIT clear) -> logical lane clear, SPARE clear
  2. repair ON : read  -> MARKER_A          (read-after-write through the spare works)
  3. repair OFF: write MARKER_B (TARGET_BIT set) -> logical lane set,
                                                    SPARE UNTOUCHED (still clear)
  4. repair OFF: read  -> MARKER_B          (the logical lane is what's visible)
  5. repair ON : read  -> MARKER_B with TARGET_BIT CLEARED, sourced from the
                          stale spare.
  6. repair ON : write MARKER_B, then read -> MARKER_B in full.

Step 5 is the actual proof: a no-op passthrough would read MARKER_B unchanged.

Step 6 exists because steps 1-5 only ever store a ZERO in the spare lane, so a
remap that hardwired the spare-write source to 0 (`din_out[spare] = 1'b0`)
would satisfy all of them. Found by mutation testing: that exact break left the
real-macro run passing while the behavioural 2D e2e failed. Step 6 stores a ONE
through the spare and reads it back, so both polarities are covered -- and it
doubles as the "rewrite clears the staleness" demonstration.

One sequence establishes five properties at once:
  * the spare column is physically distinct storage (5 differs from 4),
  * spare_wen genuinely gates the spare write (step 3 did not disturb it),
  * the read mux really substitutes the spare for the faulty lane,
  * the spare lane can hold BOTH polarities, not just the reset/zero one (6),
  * and the DOCUMENTED staleness caveat is real, not hand-waved -- a spare lane
    holds stale data until the first write after repair is enabled. Every
    production flow here writes before it reads, which is exactly why that
    caveat is bounded in practice; this test makes it visible on purpose, then
    shows the rewrite clearing it.

Toggling repair mid-run is valid: repair_ports are plain combinational
testbench-driven pins (no scan chain, no clock -- docs/redundancy-repair-plan.md
Sec 2), and it is the only way to get a persistent-storage before/after
comparison inside a single process.
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


async def _functional_write(dut, addr, data):
    dut.func_csb.value = 0
    dut.func_we.value = 1
    dut.func_addr.value = addr
    dut.func_din.value = data
    await ClockCycles(dut.clk, 2)
    dut.func_csb.value = 1
    dut.func_we.value = 0
    await ClockCycles(dut.clk, 2)


async def _functional_read(dut, addr, read_latency):
    dut.func_csb.value = 0
    dut.func_we.value = 0
    dut.func_addr.value = addr
    await ClockCycles(dut.clk, read_latency + 4)
    await Timer(1, unit="ns")
    value = _safe_int(dut.func_dout)
    dut.func_csb.value = 1
    await ClockCycles(dut.clk, 2)
    return value


@cocotb.test()
async def test_repair_col_distinct(dut):
    data_width = int(os.getenv("DATA_WIDTH", "4"))
    read_latency = int(os.getenv("READ_LATENCY", "1"))
    target_addr = int(os.getenv("TARGET_ADDR", "0"))
    target_bit = int(os.getenv("TARGET_BIT", "3"))

    data_mask = (1 << data_width) - 1
    # MARKER_A must have TARGET_BIT CLEAR and MARKER_B must have it SET -- that
    # difference is the entire signal step 5 reads out. Derived rather than
    # hardcoded so this works at any width/bit the caller picks.
    marker_a = int(os.getenv("MARKER_A", str(0xA5 & data_mask))) & ~(1 << target_bit) & data_mask
    marker_b = int(os.getenv("MARKER_B", str(data_mask))) | (1 << target_bit)
    marker_b &= data_mask
    expected_step5 = marker_b & ~(1 << target_bit) & data_mask

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.func_csb.value = 1
    dut.func_we.value = 0
    dut.func_addr.value = 0
    dut.func_din.value = 0
    dut.row_repair_en.value = 0
    dut.faulty_row_addr.value = 0
    # faulty_bit is held for the WHOLE run; only col_repair_en toggles.
    dut.col_repair_en.value = 0
    dut.faulty_bit.value = target_bit
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    # 1) repair ON: write MARKER_A. Both the logical lane and the spare take it.
    dut.col_repair_en.value = 1
    await ClockCycles(dut.clk, 2)
    await _functional_write(dut, target_addr, marker_a)

    # 2) repair ON: read back -- read-after-write through the spare.
    readback_on = await _functional_read(dut, target_addr, read_latency)
    assert readback_on == marker_a, (
        f"read-after-write through the spare column failed: wrote {marker_a:#x} to "
        f"addr {target_addr} with col repair on (faulty_bit={target_bit}), read back "
        f"{readback_on:#x}"
    )

    # 3) repair OFF: write MARKER_B. The logical lane takes it; spare_wen is low,
    # so the SPARE lane must keep MARKER_A's bit.
    dut.col_repair_en.value = 0
    await ClockCycles(dut.clk, 2)
    await _functional_write(dut, target_addr, marker_b)

    # 4) repair OFF: the logical lane is what's visible.
    readback_off = await _functional_read(dut, target_addr, read_latency)
    assert readback_off == marker_b, (
        f"sanity check failed: wrote {marker_b:#x} with col repair off, read back "
        f"{readback_off:#x}"
    )

    # 5) THE PROOF: repair ON again. TARGET_BIT must now come from the spare,
    # which still holds MARKER_A's (clear) bit -- NOT from the logical lane
    # MARKER_B just set.
    dut.col_repair_en.value = 1
    await ClockCycles(dut.clk, 2)
    readback_steered = await _functional_read(dut, target_addr, read_latency)
    assert readback_steered == expected_step5, (
        "column steering was not physically real: expected "
        f"{expected_step5:#x} (MARKER_B {marker_b:#x} with bit {target_bit} sourced "
        f"from the spare column, which still holds MARKER_A's clear bit), got "
        f"{readback_steered:#x} instead -- if this reads {marker_b:#x}, the column "
        "remap is a no-op passthrough, not a genuine redirect onto separate storage"
    )

    # 6) The spare must be able to hold a ONE too. Steps 1-5 only ever stored a
    # zero there, so a remap that hardwired the spare-write source to 0 would
    # pass all of them (confirmed by mutation testing). Rewriting MARKER_B with
    # repair ON must now put its SET bit into the spare and read back in full --
    # which is also the staleness caveat being cleared by a rewrite.
    await _functional_write(dut, target_addr, marker_b)
    readback_rewritten = await _functional_read(dut, target_addr, read_latency)
    assert readback_rewritten == marker_b, (
        f"the spare column cannot hold a SET bit: rewrote {marker_b:#x} with col "
        f"repair on (faulty_bit={target_bit}), read back {readback_rewritten:#x}. "
        f"Reading {expected_step5:#x} here means the spare-write source is stuck "
        "at 0 rather than carrying the bit it replaces"
    )
