"""Differential proof for the port-coupling fault model.

Drives the REAL generated saboteur module (sram_1r1w_dut_saboteur, built by
run_port_coupling_diff_tb.py via generate_from_config(fault_type=
"port-coupling", algo="march-1r1w") -- not a hand-rolled stand-in) two
different ways, both targeting the exact same faulted address with the exact
same data:

  1. CONCURRENT: read (port 0) and write (port 1) issued on the SAME clock
     edge at the SAME address -- genuine concurrent access, exactly what
     march_1r1w_fsm.sv produces during its E1-E4 phases.
  2. SEQUENTIAL: the write completes fully first; the read is issued only
     after, on a later, non-overlapping cycle -- exactly what a
     "test each port separately" (non-concurrent) algorithm would do.

The port-coupling defect must be OBSERVED in case 1 and NOT OBSERVED in case
2. This is the entire reason multi-port MBIST exists as a discipline: only a
genuinely concurrent algorithm can catch an inter-port bridging defect.

Both scenarios run inside ONE cocotb test (not two separate @cocotb.test()
functions) so there is exactly one clock-driving coroutine for the whole
simulation -- cocotb does not stop per-test coroutines automatically, so
starting a fresh Clock() in a second @cocotb.test() would leave two
independent clock generators racing on the same clk0/clk1 signals.
"""
from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

FAULT_BIT = int(os.getenv("PC_FAULT_BIT", "2"))
FAULT_ADDR = int(os.getenv("PC_FAULT_ADDR", "5"))
WRITE_DATA = 0xFF


async def _idle(dut) -> None:
    dut.csb0.value = 1
    dut.addr0.value = 0
    dut.csb1.value = 1
    dut.web1.value = 1
    dut.addr1.value = 0
    dut.din1.value = 0
    await ClockCycles(dut.clk0, 2)


async def _concurrent_write_and_read(dut) -> int:
    """Issue read (port 0) and write (port 1) on the SAME clock edge, same
    address. Returns the observed dout0 value one cycle later (when the
    memory's registered read becomes valid)."""
    dut.csb0.value = 0
    dut.addr0.value = FAULT_ADDR
    dut.csb1.value = 0
    dut.web1.value = 0
    dut.addr1.value = FAULT_ADDR
    dut.din1.value = WRITE_DATA
    await RisingEdge(dut.clk0)
    await Timer(1, unit="ns")

    dut.csb0.value = 1
    dut.csb1.value = 1
    dut.web1.value = 1
    await RisingEdge(dut.clk0)
    await Timer(1, unit="ns")

    return int(dut.dout0.value)


async def _sequential_write_then_read(dut) -> int:
    """Write (port 1) completes fully with port 0 idle; several cycles
    later, read (port 0) is issued with port 1 idle -- no possible overlap.
    Returns the observed dout0 value."""
    # Step 1: write ONLY (port 0 fully idle/deasserted this whole cycle).
    await RisingEdge(dut.clk1)
    dut.csb0.value = 1
    dut.addr0.value = 0
    dut.csb1.value = 0
    dut.web1.value = 0
    dut.addr1.value = FAULT_ADDR
    dut.din1.value = WRITE_DATA
    await RisingEdge(dut.clk1)
    await Timer(1, unit="ns")
    dut.csb1.value = 1
    dut.web1.value = 1
    dut.addr1.value = 0
    dut.din1.value = 0

    # Step 2: let the write fully settle -- several idle cycles with BOTH
    # ports deasserted, so there is no possible overlap with the write.
    await ClockCycles(dut.clk0, 3)

    # Step 3: read ONLY, well after the write has completed (port 1 stays
    # idle/deasserted this whole cycle).
    dut.csb0.value = 0
    dut.addr0.value = FAULT_ADDR
    await RisingEdge(dut.clk0)
    await Timer(1, unit="ns")
    dut.csb0.value = 1

    # Registered read becomes valid the following edge.
    await RisingEdge(dut.clk0)
    await Timer(1, unit="ns")

    return int(dut.dout0.value)


@cocotb.test()
async def test_concurrent_vs_sequential_port_coupling(dut):
    cocotb.start_soon(Clock(dut.clk0, 10, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk1, 10, unit="ns").start())

    expected_bit = (WRITE_DATA >> FAULT_BIT) & 1

    # --- Scenario 1: CONCURRENT access -- must OBSERVE the coupling fault.
    await _idle(dut)
    concurrent_dout = await _concurrent_write_and_read(dut)
    concurrent_bit = (concurrent_dout >> FAULT_BIT) & 1

    print()
    print("CONCURRENT access (read(port0) + write(port1), SAME cycle, SAME address):")
    print(f"  wrote 0x{WRITE_DATA:02X} to addr 0x{FAULT_ADDR:X} while reading it the SAME cycle")
    print(f"  observed dout0 = 0x{concurrent_dout:02X} (bit {FAULT_BIT} = {concurrent_bit}, expected {expected_bit})")

    assert concurrent_bit != expected_bit, (
        "port-coupling fault was NOT observed on a genuinely concurrent "
        "same-address read+write -- the fault model is not sensitized"
    )

    # --- Scenario 2: SEQUENTIAL access (test-each-port-separately baseline)
    # -- must NOT observe the coupling fault, using the exact same address
    # and data as scenario 1.
    await _idle(dut)
    sequential_dout = await _sequential_write_then_read(dut)
    sequential_bit = (sequential_dout >> FAULT_BIT) & 1

    print()
    print("SEQUENTIAL access (test-each-port-separately baseline):")
    print(f"  wrote 0x{WRITE_DATA:02X} to addr 0x{FAULT_ADDR:X}, then read it back on a LATER, non-overlapping cycle")
    print(f"  observed dout0 = 0x{sequential_dout:02X} (bit {FAULT_BIT} = {sequential_bit}, expected {expected_bit})")

    assert sequential_bit == expected_bit, (
        "port-coupling fault was observed on a purely SEQUENTIAL access with "
        "no concurrency -- the fault model is corrupting every future read "
        "of the address, not just the genuinely concurrent one (this would "
        "defeat the entire point of the fault type)"
    )

    print()
    print("DIFFERENTIAL PROOF: concurrent access detects the port-coupling "
          "defect; a test-each-port-separately (non-concurrent) access to "
          "the exact same address/data misses it entirely.")
