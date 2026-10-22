"""One on-chip self-repair trial, for the repair-yield sweep (yield_sweep.SelfRepairEngine).

The memory is the fault-injectable model (memory_model.render_memory_model with
stuck_masks), so the defects, spare rows and spare lanes included, are whatever
the mask files say when the simulation starts. The trial:

  1. raises self_repair_start and, during the analyze pass only (registrar_enable
     high), logs every read the algorithm's own compare flags: what the on-chip
     analyzer is fed;
  2. waits for self_repair_done and reads the outcome: self_repair_fail, the
     analyzer's unrepairable flag, the latched repair signature, and the
     diagnosis log when the wrapper has one;
  3. drops self_repair_start and scans the repaired memory through the functional
     port (solid 0s, then solid 1s), independently of the chip's own verdict.

It prints MARCH_FAIL lines and MARCH_LOG_COMPLETE for the analyze pass, one
SELF_REPAIR_RESULT {json} line, then FAIL_CELL lines and FAIL_SCAN_COMPLETE for
the post-repair scan. Self-contained on purpose (importing test_mbist would run
its import-time fault-file preparation against this build).
"""
import json
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer, with_timeout

CLOCK_NS = 10
SETTLE_NS = CLOCK_NS // 2 - 1  # from the falling edge to just before the next rise


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


def _signal(dut, name):
    try:
        return getattr(dut, name)
    except AttributeError:
        return None


def _timeout_ns(addr_width, read_latency):
    """Generous bound on the analyze march, the verify march and the scan."""
    depth = 1 << addr_width
    cycles = 2 * 64 * depth * (read_latency + 4) + 2 * depth * (2 + read_latency + 5) + 4000
    return 2 * cycles * CLOCK_NS


async def _log_analyze_pass(dut, data_width, log):
    """Record every flagged read while the analyzer is listening."""
    top = dut.u_algo_top
    valid_h, addr_h, mask_h = top.bist_fail_valid, top.bist_fail_addr, top.bist_fail_bitmask
    while True:
        await FallingEdge(dut.clk)
        await Timer(SETTLE_NS, unit="ns")
        if _safe_int(dut.registrar_enable) != 1:
            continue
        if _safe_int(dut.bist_done) == 1 and log["analyze_bist_fail"] is None:
            log["analyze_bist_fail"] = 1 if _safe_int(dut.bist_fail) == 1 else 0
        if _safe_int(valid_h) != 1:
            continue
        addr, mask = _safe_int(addr_h), _safe_int(mask_h)
        if addr is None or mask is None:
            log["unknown"] += 1
            continue
        log["events"] += 1
        log["cells"].update((addr, bit) for bit in range(data_width) if (mask >> bit) & 1)


async def _self_repair(dut):
    dut.self_repair_start.value = 1
    await ClockCycles(dut.clk, 2)  # past the previous run's sticky done
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        if _safe_int(dut.self_repair_done) == 1:
            break
    outcome = {"self_repair_fail": _safe_int(dut.self_repair_fail)}
    for name in ("unrepairable", "row_repair_en", "faulty_row_addr", "col_repair_en", "faulty_bit",
                 "diag_valid", "diag_addr", "diag_overflow"):
        handle = _signal(dut, name)
        if handle is not None:
            outcome[name] = _safe_int(handle)
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 2)
    return outcome


async def _scan(dut, addr_width, data_width, read_latency):
    depth = 1 << addr_width
    data_mask = (1 << data_width) - 1
    hold = read_latency + 4
    fails, unknown = set(), set()
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
            await FallingEdge(dut.clk)
            await Timer(SETTLE_NS, unit="ns")
            word = _safe_int(dut.func_dout)
            if word is None:
                unknown.add(addr)
                continue
            diff = (word ^ pattern) & data_mask
            fails.update((addr, bit) for bit in range(data_width) if (diff >> bit) & 1)
        dut.func_csb.value = 1
        await ClockCycles(dut.clk, 2)
    return sorted(fails), sorted(unknown)


async def _trial(dut, addr_width, data_width, read_latency):
    cocotb.start_soon(Clock(dut.clk, CLOCK_NS, unit="ns").start())
    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.self_repair_start.value = 0
    dut.func_csb.value = 1
    dut.func_we.value = 0
    dut.func_addr.value = 0
    dut.func_din.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    log = {"events": 0, "unknown": 0, "cells": set(), "analyze_bist_fail": None}
    monitor = cocotb.start_soon(_log_analyze_pass(dut, data_width, log))
    outcome = await _self_repair(dut)
    monitor.cancel()
    fails, unknown = await _scan(dut, addr_width, data_width, read_latency)
    return log, outcome, fails, unknown


@cocotb.test()
async def test_selfrepair_trial(dut):
    addr_width = int(os.getenv("ADDR_WIDTH", "10"))
    data_width = int(os.getenv("DATA_WIDTH", "32"))
    read_latency = int(os.getenv("READ_LATENCY", "1"))
    log, outcome, fails, unknown = await with_timeout(
        _trial(dut, addr_width, data_width, read_latency), _timeout_ns(addr_width, read_latency), "ns",
    )
    for addr, bit in sorted(log["cells"]):
        print("MARCH_FAIL " + json.dumps({"addr": addr, "bit": bit}))
    analyze_fail = log["analyze_bist_fail"] if log["analyze_bist_fail"] is not None else int(log["events"] > 0)
    print(f"MARCH_LOG_COMPLETE events={log['events']} cells={len(log['cells'])} unknown={log['unknown']} "
          f"bist_fail={analyze_fail}")
    print("SELF_REPAIR_RESULT " + json.dumps(outcome, sort_keys=True))
    for addr, bit in fails:
        print("FAIL_CELL " + json.dumps({"addr": addr, "bit": bit}))
    for addr in unknown:
        print("FAIL_SCAN_UNKNOWN " + json.dumps({"addr": addr}))
    print(f"FAIL_SCAN_COMPLETE cells={len(fails)} unknown={len(unknown)}")
    assert outcome["self_repair_fail"] in (0, 1), "self_repair_fail read back as X/Z"
