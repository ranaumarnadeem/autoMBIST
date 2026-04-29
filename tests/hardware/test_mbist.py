import os
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, with_timeout

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.fault_gen import generate_fault_files
from autombist.fault_gen import FaultType


def _prepare_fault_files() -> None:
    mode = os.getenv("FAULT_MODE", "clean").strip().lower()
    fault_type_raw = os.getenv("FAULT_TYPE", "stuck-at").strip().lower()
    addr_width = int(os.getenv("ADDR_WIDTH", "10"))
    data_width = int(os.getenv("DATA_WIDTH", "32"))
    faults = int(os.getenv("FAULTS", "0"))
    fault_seed_raw = os.getenv("FAULT_SEED", "")
    fault_seed = int(fault_seed_raw) if fault_seed_raw else None
    memory_name = os.getenv("MEMORY_NAME", "sram_1rw").strip() or "sram_1rw"

    if mode == "clean":
        faults = 0
    elif mode != "faults":
        raise ValueError(f"Unsupported FAULT_MODE: {mode}")

    fault_type_map = {
        "stuck-at": FaultType.STUCK_AT,
        "transition-up": FaultType.TRANSITION_UP,
        "transition-down": FaultType.TRANSITION_DOWN,
    }
    if fault_type_raw not in fault_type_map:
        raise ValueError(f"Unsupported FAULT_TYPE: {fault_type_raw}")

    generate_fault_files(
        outdir=REPO_ROOT / "out" / memory_name / "faults",
        addr_width=addr_width,
        data_width=data_width,
        fault_type=fault_type_map[fault_type_raw],
        faults=faults,
        seed=fault_seed,
    )


_prepare_fault_files()


async def _run_mbist_once(dut) -> None:
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.test_mode.value = 1
    dut.bist_start.value = 0

    dut.func_csb.value = 1
    dut.func_addr.value = 0
    dut.func_din.value = 0
    dut.func_we.value = 0

    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    dut.bist_start.value = 1
    await RisingEdge(dut.clk)
    dut.bist_start.value = 0

    await with_timeout(RisingEdge(dut.bist_done), 5, "ms")


@cocotb.test()
async def test_clean(dut):
    if os.getenv("FAULT_MODE", "clean").strip().lower() != "clean":
        return

    await _run_mbist_once(dut)
    assert int(dut.bist_fail.value) == 0, "MBIST reported fail in clean mode"


@cocotb.test()
async def test_faults(dut):
    if os.getenv("FAULT_MODE", "clean").strip().lower() != "faults":
        return
    if os.getenv("FAULT_TYPE", "stuck-at").strip().lower() != "stuck-at":
        return

    await _run_mbist_once(dut)
    assert int(dut.bist_fail.value) == 1, "MBIST did not detect injected faults"


@cocotb.test()
async def test_transition_faults(dut):
    if os.getenv("FAULT_MODE", "clean").strip().lower() != "faults":
        return

    fault_type = os.getenv("FAULT_TYPE", "stuck-at").strip().lower()
    if fault_type not in {"transition-up", "transition-down"}:
        return

    await _run_mbist_once(dut)
    assert int(dut.bist_fail.value) == 1, "MBIST did not detect transition faults"
