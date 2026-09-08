"""End-to-end proof of on-chip diagnosis logging: a full-range fail-address
accumulator, independent of (and in the differentiating scenario below,
larger than) the physical spare budget onchip_row_repair_analyzer is bounded
to. Icarus + make gated, mirroring test_onchip_selfrepair_e2e.py's structure.

JTAG/IJTAG wrapping of diag_valid/diag_addr/diag_overflow is deliberately out
of scope here -- see rtl/onchip_diagnosis_log.sv's header and
docs/source/roadmap.md.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("make") is None,
    reason="needs Icarus Verilog + make on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import generate_from_config  # noqa: E402
from autombist.runner import run_simulation  # noqa: E402

ADDR_WIDTH = 2
DATA_WIDTH = 4
BASE_PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}

# Self-repair-capable single-port algos (matches test_onchip_selfrepair_e2e.py):
# diagnosis is a second, independent consumer of the same fail_valid/fail_addr
# stream every one of these already produces.
SELFREPAIR_ALGOS = ["march-c", "march-raw", "march-x", "mats-plus"]


def _config(memory_name: str, wrapper_module_name: str, num_spare_rows: int, num_diagnosis_entries: int) -> dict:
    return {
        "memory_name": memory_name,
        "wrapper_module_name": wrapper_module_name,
        "addr_width": ADDR_WIDTH,
        "data_width": DATA_WIDTH,
        "we_active_low": True,
        "ports": BASE_PORTS,
        "redundancy": {
            "num_spare_rows": num_spare_rows, "num_spare_cols": 0,
            "onchip_selfrepair": True,
            "onchip_diagnosis": True, "num_diagnosis_entries": num_diagnosis_entries,
        },
    }


def _generate(tmp_path: Path, config: dict, subdir: str, algo: str = "march-c") -> Path:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / subdir, algo=algo)


def _run(wrapper: Path, scenario: str, num_diagnosis_entries: int):
    return run_simulation(
        wrapper.parent,
        extra_make_vars={
            "COCOTB_TEST_MODULES": "test_onchip_diagnosis",
            "DIAGNOSIS_SCENARIO": scenario,
            "NUM_DIAGNOSIS_ENTRIES": str(num_diagnosis_entries),
        },
    )


@pytest.mark.parametrize("algo", SELFREPAIR_ALGOS)
def test_one_defect_within_both_budgets(tmp_path: Path, algo: str) -> None:
    wrapper = _generate(
        tmp_path, _config("sram_spares_tiny", "sram_spares_tiny_mbist", 2, 4), "within_budget", algo=algo,
    )
    result = _run(wrapper, "within_budget", 4)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("algo", SELFREPAIR_ALGOS)
def test_diagnosis_sees_past_the_repair_budget(tmp_path: Path, algo: str) -> None:
    """The differentiator: 2 defects exceed a 1-spare repair budget (repair
    itself only fixes one, self_repair_fail=1), but the diagnosis log's own
    2-entry budget is sufficient -- diag_valid must show BOTH original defect
    rows, proving diagnosis genuinely sees past what onchip_row_repair_analyzer
    is bounded to."""
    wrapper = _generate(
        tmp_path, _config("sram_spares_tiny_2defect", "sram_spares_tiny_2defect_mbist", 1, 2),
        "beyond_budget", algo=algo,
    )
    result = _run(wrapper, "beyond_repair_budget", 2)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("algo", SELFREPAIR_ALGOS)
def test_retrigger_gives_an_empty_diagnosis_the_second_time(tmp_path: Path, algo: str) -> None:
    """The core reset-per-pass claim: running self-repair twice with no reset
    in between must give the same repair verdict both times (existing,
    unchanged behavior) but an EMPTY diagnosis log after the second pass --
    the memory is already transparently repaired, so the second analyze pass
    observes nothing new, and by design the log does not remember the first
    pass's finding."""
    wrapper = _generate(
        tmp_path, _config("sram_spares_tiny", "sram_spares_tiny_mbist", 2, 4), "retrigger", algo=algo,
    )
    result = _run(wrapper, "retrigger", 4)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("algo", SELFREPAIR_ALGOS)
def test_diagnosis_overflow_flags_when_the_log_is_too_small(tmp_path: Path, algo: str) -> None:
    """2 distinct defect rows against a 1-entry diagnosis log (repair budget
    kept sufficient at num_spare_rows=2, so this isolates diagnosis overflow
    from repair failure): diag_overflow must assert."""
    wrapper = _generate(
        tmp_path, _config("sram_spares_tiny_2defect", "sram_spares_tiny_2defect_mbist", 2, 1),
        "overflow", algo=algo,
    )
    result = _run(wrapper, "overflow", 1)
    assert result.returncode == 0, result.stdout
