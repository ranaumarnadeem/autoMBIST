from __future__ import annotations

import os
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import Timer

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _selected_algo() -> str:
    return os.getenv("ALGO", "march-c").strip().lower()


async def _drive_and_sample(dut, phase: int, op_step: int) -> dict[str, int]:
    dut.phase.value = phase
    dut.op_step.value = op_step
    await Timer(1, unit="ns")
    return {
        "phase_dir_up": int(dut.phase_dir_up.value),
        "do_read": int(dut.do_read.value),
        "do_write": int(dut.do_write.value),
        "expected_data": int(dut.expected_data.value),
        "write_data": int(dut.write_data.value),
        "last_step": int(dut.last_step.value),
    }


@cocotb.test()
async def test_algorithm_module_sequences(dut):
    algo = _selected_algo()
    assert algo in {"march-c", "march-raw"}, f"Unsupported ALGO={algo}"

    zero = 0
    ones = (1 << 32) - 1

    if algo == "march-c":
        vectors = {
            (0, 0): {"phase_dir_up": 1, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": zero, "last_step": 1},
            (1, 0): {"phase_dir_up": 1, "do_read": 1, "do_write": 0, "expected_data": zero, "write_data": zero, "last_step": 0},
            (1, 1): {"phase_dir_up": 1, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": ones, "last_step": 1},
            (2, 0): {"phase_dir_up": 1, "do_read": 1, "do_write": 0, "expected_data": ones, "write_data": zero, "last_step": 0},
            (2, 1): {"phase_dir_up": 1, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": zero, "last_step": 1},
            (3, 0): {"phase_dir_up": 0, "do_read": 1, "do_write": 0, "expected_data": zero, "write_data": zero, "last_step": 0},
            (3, 1): {"phase_dir_up": 0, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": ones, "last_step": 1},
            (4, 0): {"phase_dir_up": 0, "do_read": 1, "do_write": 0, "expected_data": ones, "write_data": zero, "last_step": 0},
            (4, 1): {"phase_dir_up": 0, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": zero, "last_step": 1},
            (5, 0): {"phase_dir_up": 0, "do_read": 1, "do_write": 0, "expected_data": zero, "write_data": zero, "last_step": 1},
        }
    else:
        vectors = {
            (0, 0): {"phase_dir_up": 1, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": zero, "last_step": 1},
            (1, 0): {"phase_dir_up": 1, "do_read": 1, "do_write": 0, "expected_data": zero, "write_data": zero, "last_step": 0},
            (1, 1): {"phase_dir_up": 1, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": ones, "last_step": 0},
            (1, 2): {"phase_dir_up": 1, "do_read": 1, "do_write": 0, "expected_data": ones, "write_data": zero, "last_step": 1},
            (2, 0): {"phase_dir_up": 1, "do_read": 1, "do_write": 0, "expected_data": ones, "write_data": zero, "last_step": 0},
            (2, 1): {"phase_dir_up": 1, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": zero, "last_step": 0},
            (2, 2): {"phase_dir_up": 1, "do_read": 1, "do_write": 0, "expected_data": zero, "write_data": zero, "last_step": 1},
            (3, 0): {"phase_dir_up": 0, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": zero, "last_step": 1},
            (4, 0): {"phase_dir_up": 0, "do_read": 1, "do_write": 0, "expected_data": zero, "write_data": zero, "last_step": 0},
            (4, 1): {"phase_dir_up": 0, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": ones, "last_step": 0},
            (4, 2): {"phase_dir_up": 0, "do_read": 1, "do_write": 0, "expected_data": ones, "write_data": zero, "last_step": 1},
            (5, 0): {"phase_dir_up": 0, "do_read": 1, "do_write": 0, "expected_data": ones, "write_data": zero, "last_step": 0},
            (5, 1): {"phase_dir_up": 0, "do_read": 0, "do_write": 1, "expected_data": zero, "write_data": zero, "last_step": 0},
            (5, 2): {"phase_dir_up": 0, "do_read": 1, "do_write": 0, "expected_data": zero, "write_data": zero, "last_step": 1},
        }

    for (phase, op_step), expected in vectors.items():
        observed = await _drive_and_sample(dut, phase, op_step)
        assert observed == expected, f"{algo} mismatch for phase={phase} op_step={op_step}: {observed} != {expected}"
