"""End-to-end proof of Step E: a fully AUTONOMOUS on-chip BIRA/BISR loop -- no
tester, one `self_repair_start` level, the chip detects its own failing rows
during its own march-C pass, computes the repair via
onchip_row_repair_analyzer, applies it, and (except in the deliberately
unrepairable case) re-verifies via a second march-C pass -- all in silicon.

Contrast with test_repair_loop_e2e.py (Step D): there, Python drives
bira.analyze()/encode_row_repair() between two separate simulator invocations
and pokes the resulting signature onto combinational input pins. Here,
`redundancy.onchip_selfrepair: true` means there ARE no repair_ports/tester
pins at all -- the ENTIRE inject-detect-analyze-apply-(verify) sequence
happens inside ONE simulation run, triggered by a single self_repair_start
level and read back via self_repair_done/self_repair_fail. Icarus + make gated.
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

from autombist.bira_input import fail_cells  # noqa: E402
from autombist.generator import generate_from_config  # noqa: E402
from autombist.runner import run_simulation  # noqa: E402

ADDR_WIDTH = 2
DATA_WIDTH = 4
BASE_PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}

# Matches sram_spares_tiny.v's baked-in DEFECT_ADDR/DEFECT_BIT (Step A's DUT).
DEFECT_1 = (3, 3)
# Matches sram_spares_tiny_2defect.v's two baked-in defects.
DEFECT_2A = (3, 3)
DEFECT_2B = (1, 1)


def _config(memory_name: str, wrapper_module_name: str, num_spare_rows: int) -> dict:
    return {
        "memory_name": memory_name,
        "wrapper_module_name": wrapper_module_name,
        "addr_width": ADDR_WIDTH,
        "data_width": DATA_WIDTH,
        "we_active_low": True,
        "ports": BASE_PORTS,
        # NO repair_ports: -- mutually exclusive with onchip_selfrepair, the
        # analyzer/sequencer drive the remap directly.
        "redundancy": {"num_spare_rows": num_spare_rows, "num_spare_cols": 0, "onchip_selfrepair": True},
    }


def _generate(tmp_path: Path, config: dict, subdir: str) -> Path:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / subdir, algo="march-c")


def _run(wrapper: Path, scenario: str):
    return run_simulation(
        wrapper.parent,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_onchip_selfrepair", "SELFREPAIR_SCENARIO": scenario},
    )


def test_one_defect_two_spares_is_autonomously_repaired(tmp_path: Path) -> None:
    wrapper = _generate(tmp_path, _config("sram_spares_tiny", "sram_spares_tiny_mbist", 2), "one_defect")
    result = _run(wrapper, "repairable")
    assert result.returncode == 0, result.stdout
    # The forced stuck-at is gone after the chip repaired itself, verified
    # independently through the functional-port fail scan (not just trusting
    # the chip's own self_repair_fail status).
    assert fail_cells(result.report) == set()


def test_two_defects_two_spares_is_autonomously_repaired(tmp_path: Path) -> None:
    """Both distinct faulty rows fit within the spare budget -- the on-chip
    registrar must catch BOTH during the single analyze pass, not just the
    first one it encounters."""
    wrapper = _generate(tmp_path, _config("sram_spares_tiny_2defect", "sram_spares_tiny_2defect_mbist", 2), "two_ok")
    result = _run(wrapper, "repairable")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


def test_two_defects_one_spare_is_flagged_not_silently_passed(tmp_path: Path) -> None:
    """The DVCon-style check: 2 distinct faulty rows exceed the 1-spare budget
    -- self_repair_fail must read 1 (asserted inside the cocotb test), AND the
    independent re-scan must show a real, partial (not zero, not both) repair:
    exactly one of the two known defects remains."""
    wrapper = _generate(tmp_path, _config("sram_spares_tiny_2defect", "sram_spares_tiny_2defect_mbist", 1), "unrepairable")
    result = _run(wrapper, "partial")
    assert result.returncode == 0, result.stdout
    observed = fail_cells(result.report)
    assert observed, "expected exactly one defect to remain, found none"
    assert observed <= {DEFECT_2A, DEFECT_2B}
    assert len(observed) == 1


def test_retrigger_gives_the_same_result_both_times(tmp_path: Path) -> None:
    """Running the autonomous sequence twice in one simulation (no reset in
    between) must reach the SAME verdict both times -- proves the analyzer's
    known-defect state correctly PERSISTS across the re-trigger (accumulate
    for the chip's lifetime, cleared only by rst_n), not just correct by
    accident of a single fresh reset. A design that wrongly cleared this
    state at the start of the second pass would see no fault (the first
    pass's repair already masks it) and silently erase the still-valid
    repair -- see onchip_row_repair_analyzer.sv's header comment."""
    wrapper = _generate(tmp_path, _config("sram_spares_tiny", "sram_spares_tiny_mbist", 2), "retrigger")
    result = _run(wrapper, "retrigger")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()
