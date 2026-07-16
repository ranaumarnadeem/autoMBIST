"""Step-A repair-row proof against a REAL OpenRAM-compiled macro (not the
hand-written sram_spares_tiny.v/sram_model_spares.sv behavioral stand-ins
every other BISR test uses -- see rtl/sram_bisr_real_8x16.v for provenance).

A real OpenRAM macro has no defect-injection knob, so this cannot reuse
test_repair_row.py's inject-a-stuck-at-then-repair-it pattern. Instead it
proves the remap genuinely redirects PHYSICAL storage -- the property that
actually matters for BISR, since a broken remap that is secretly a no-op
passthrough would still make an ordinary read-after-write check pass.

Unlike test_repair_row.py (which holds row_repair_en/faulty_row_addr FIXED
for an entire run, comparing two SEPARATE runs via REPAIR_PHASE), this test
toggles repair ON/OFF WITHIN one run. That is deliberate: repair_ports are
plain combinational testbench-driven pins (no scan chain, no clock -- see
docs/redundancy-repair-plan.md Sec 2), so live toggling is valid, and it is
the only way to get a persistent-storage before/after comparison in a single
process -- there is no baked-in defect to compare across two independent
invocations the way the toy-model tests do.

Proof, one target logical address, one spare:
  1. repair OFF: write MARKER_A to the target address (lands on PHYSICAL row
     TARGET_ADDR).
  2. repair OFF: read it back -- must be MARKER_A (sanity).
  3. repair ON (steer the target logical address -> spare 0): write MARKER_B
     (lands on the PHYSICAL spare row; physical row TARGET_ADDR is untouched).
  4. repair ON: read back -- must be MARKER_B (read-after-write through the
     spare works).
  5. repair OFF again: read back -- must be MARKER_A again, NOT MARKER_B.
     This is the actual proof: if the remap were a no-op passthrough, step
     3's write would have clobbered physical row TARGET_ADDR with MARKER_B,
     and this step would wrongly read MARKER_B instead of the untouched
     original.

Also runs the existing solid-0/solid-1 fail-scan convention (verbatim from
test_repair_row.py), repair OFF, across all logical addresses, asserting a
completely clean result -- the baseline proof that the wrapper's mux/timing
genuinely interfaces with a real macro's own (posedge-register +
negedge + #DELAY) timing, not just the toy single-always_ff behavioral model
every other BISR test exercises.
"""
import json
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer, with_timeout

MARKER_A = 0xA5
MARKER_B = 0x5A
TARGET_ADDR = 5


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


async def _functional_fail_scan(dut, addr_width, data_width, read_latency):
    """Verbatim convention from test_repair_row.py: solid-0 then solid-1
    across every logical address, recording bits that differ on readback."""
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
async def test_repair_row_real_macro(dut):
    addr_width = int(os.getenv("ADDR_WIDTH", "4"))
    data_width = int(os.getenv("DATA_WIDTH", "8"))
    read_latency = int(os.getenv("READ_LATENCY", "1"))

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
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    # 1) Baseline: repair OFF, solid-0/solid-1 scan across every logical
    # address must be completely clean.
    fails = await with_timeout(
        _functional_fail_scan(dut, addr_width, data_width, read_latency),
        500_000,
        "ns",
    )
    for addr, bit in fails:
        print("FAIL_CELL " + json.dumps({"addr": addr, "bit": bit}))
    print(f"FAIL_SCAN_COMPLETE cells={len(fails)}")
    assert fails == [], f"expected a clean real-macro scan with repair off, found {fails}"

    # 2) Steering-distinctness proof (see module docstring).
    await _functional_write(dut, TARGET_ADDR, MARKER_A)
    readback_off_before = await _functional_read(dut, TARGET_ADDR, read_latency)
    assert readback_off_before == MARKER_A, (
        f"sanity check failed: wrote {MARKER_A:#x} to addr {TARGET_ADDR} with repair off, "
        f"read back {readback_off_before:#x}"
    )

    # Steer the target logical address to spare 0, then write a DIFFERENT
    # marker -- this write lands on the PHYSICAL spare row, not physical row
    # TARGET_ADDR.
    dut.row_repair_en.value = 0b01
    dut.faulty_row_addr.value = TARGET_ADDR
    await ClockCycles(dut.clk, 2)
    await _functional_write(dut, TARGET_ADDR, MARKER_B)
    readback_on = await _functional_read(dut, TARGET_ADDR, read_latency)
    assert readback_on == MARKER_B, (
        f"read-after-write through the spare failed: wrote {MARKER_B:#x} with repair on, "
        f"read back {readback_on:#x}"
    )

    # Drop repair -- the target logical address now goes straight to
    # PHYSICAL row TARGET_ADDR again, which the write above never touched.
    dut.row_repair_en.value = 0
    dut.faulty_row_addr.value = 0
    await ClockCycles(dut.clk, 2)
    readback_off_after = await _functional_read(dut, TARGET_ADDR, read_latency)
    assert readback_off_after == MARKER_A, (
        "steering was not physically real: expected the ORIGINAL marker "
        f"{MARKER_A:#x} to still be sitting in physical row {TARGET_ADDR} "
        f"(untouched by the repaired write), got {readback_off_after:#x} instead -- "
        "if this reads MARKER_B, the remap is a no-op passthrough, not a genuine redirect"
    )
