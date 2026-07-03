"""cocotb test for the standalone 1R1W march algorithm proof-of-concept.

Run via tests/hardware/run_march_1r1w_tb.py (see that file's header comment
for the exact invocation), e.g.:

    wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \
        PYTHONPATH=src ~/cocotb/bin/python tests/hardware/run_march_1r1w_tb.py"

This test produces hard evidence for three things:
  a) CONCURRENCY: the FSM's read-enable-for-port-0 and write-enable-for-
     port-1 signals (and their address registers) are active on the SAME
     clock edge with the SAME address value.
  b) GOLDEN CLEAN: bist_fail == 0 at bist_done with an unmodified
     sram_model_1r1w.
  c) FAULT DETECTION: with a stuck-at fault injected on the read path (an
     XOR mask forced onto one bit for one address, applied in
     march_1r1w_tb_top -- NOT by touching bist_fail directly),
     bist_fail == 1 at bist_done.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _get_hier_value(root, dotted_path: str) -> int:
    current = root
    for name in dotted_path.split("."):
        current = getattr(current, name)
    return int(current.value)


async def _reset(dut) -> None:
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.bist_start.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def _start_bist(dut) -> None:
    dut.bist_start.value = 1
    await RisingEdge(dut.clk)
    dut.bist_start.value = 0


async def _run_until_done(dut, concurrency_events: list[dict[str, int]] | None = None) -> None:
    """Run the BIST to completion, optionally recording concurrency evidence.

    On every clock edge while busy, hierarchically probe the FSM's port-0
    read-enable/address and port-1 write-enable/address signals. Any cycle
    where both are asserted simultaneously is direct proof of same-cycle,
    same-address read+write concurrency.
    """
    fsm_path = "u_algo_top.u_march_1r1w_fsm"
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")

        if concurrency_events is not None and int(dut.rst_n.value) == 1:
            mem_en0 = _get_hier_value(dut, f"{fsm_path}.mem_en0")
            mem_addr0 = _get_hier_value(dut, f"{fsm_path}.mem_addr0")
            mem_en1 = _get_hier_value(dut, f"{fsm_path}.mem_en1")
            mem_addr1 = _get_hier_value(dut, f"{fsm_path}.mem_addr1")
            phase = _get_hier_value(dut, f"{fsm_path}.phase_q")
            if mem_en0 == 1 and mem_en1 == 1:
                concurrency_events.append(
                    {
                        "phase": phase,
                        "mem_addr0": mem_addr0,
                        "mem_addr1": mem_addr1,
                        "mem_en0": mem_en0,
                        "mem_en1": mem_en1,
                    }
                )

        if int(dut.bist_done.value) == 1:
            break


@cocotb.test()
async def test_concurrency_and_golden_clean(dut):
    """Proves (a) same-cycle same-address read+write concurrency and (b) a
    golden-clean run against an unmodified sram_model_1r1w reports no fail.
    """
    await _reset(dut)
    await _start_bist(dut)

    concurrency_events: list[dict[str, int]] = []
    await _run_until_done(dut, concurrency_events)

    # (a) CONCURRENCY: at least one cycle must show mem_en0 and mem_en1 both
    # asserted with mem_addr0 == mem_addr1 (same address, same edge).
    assert concurrency_events, "No cycle observed with both ports active simultaneously"

    same_addr_events = [e for e in concurrency_events if e["mem_addr0"] == e["mem_addr1"]]
    assert same_addr_events, (
        f"Ports were concurrently active but never at the same address: {concurrency_events[:5]}"
    )

    print()
    print("Concurrency evidence (same-cycle, same-address read(port0)+write(port1)):")
    print(f"  total concurrent-port cycles observed: {len(concurrency_events)}")
    print(f"  total same-address concurrent cycles : {len(same_addr_events)}")
    for event in same_addr_events[:8]:
        print(
            f"  phase={event['phase']} addr0=0x{event['mem_addr0']:X} "
            f"addr1=0x{event['mem_addr1']:X} mem_en0={event['mem_en0']} "
            f"mem_en1={event['mem_en1']}"
        )

    # Expect concurrency on elements 1-4 (4 phases) across every address in
    # the sweep -- assert we saw a substantial number of such cycles, not
    # just a fluke single edge.
    addr_width = int(os.getenv("ADDR_WIDTH", "6"))
    depth = 1 << addr_width
    expected_min = depth * 4 - 1  # 4 concurrent phases, allow off-by-one at boundaries
    assert len(same_addr_events) >= expected_min, (
        f"Expected at least {expected_min} same-address concurrent cycles, "
        f"got {len(same_addr_events)}"
    )

    # (b) GOLDEN CLEAN.
    print()
    print(f"bist_fail (golden, unmodified memory) = {int(dut.bist_fail.value)}")
    assert int(dut.bist_fail.value) == 0, "MBIST reported fail against unmodified sram_model_1r1w"


@cocotb.test()
async def test_fault_detection(dut):
    """Injects a stuck-at-like fault via an XOR mask on the read path (NOT
    by touching bist_fail directly) and proves the FSM detects it.
    """
    await _reset(dut)

    # Inject a stuck-at fault: force bit 2 of the read-path XOR mask high
    # for the entire run. This flips bit 2 of every value observed through
    # sram_dout0_faulted, modeling a bit stuck at the inverse of whatever
    # the array actually stores -- genuinely exercised through the shared
    # mem[] array (the fault masks the *observation* of a real array read,
    # it does not fabricate a result).
    fault_bit = 2
    dut.fault_xor_mask.value = (1 << fault_bit)
    print()
    print(f"Injected fault: XOR mask on port-0 read data, bit {fault_bit} forced to flip")

    await _start_bist(dut)
    await _run_until_done(dut)

    print(f"bist_fail (with injected stuck-at fault) = {int(dut.bist_fail.value)}")
    assert int(dut.bist_fail.value) == 1, "MBIST did not detect the injected stuck-at fault"
