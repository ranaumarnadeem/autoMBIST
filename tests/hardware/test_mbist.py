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


def _prepare_fault_files() -> None:
    mode = os.getenv("FAULT_MODE", "clean").strip().lower()
    faults = int(os.getenv("FAULTS", "0"))
    fault_seed_raw = os.getenv("FAULT_SEED", "")
    fault_seed = int(fault_seed_raw) if fault_seed_raw else None
    memory_name = os.getenv("MEMORY_NAME", "sram_1rw").strip() or "sram_1rw"

    if mode == "clean":
        faults = 0
    elif mode != "faults":
        raise ValueError(f"Unsupported FAULT_MODE: {mode}")

    generate_fault_files(
        outdir=REPO_ROOT / "out" / memory_name / "faults",
        addr_width=10,
        data_width=32,
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

    await _run_mbist_once(dut)
    assert int(dut.bist_fail.value) == 1, "MBIST did not detect injected faults"
