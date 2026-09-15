"""End-to-end proof of Step E's autonomous on-chip BIRA/BISR loop for the
multi-port march-2rw algorithm -- the same four scenarios as
test_onchip_selfrepair_1r1w_e2e.py (march-1r1w), but exercising a genuinely
symmetric 2-read-write-port spare-augmented memory model
(sram_spares_tiny_2rw[.v]/_2defect) instead of march-1r1w's 1-read + 1-write
shape.

Unlike march-1r1w, march-2rw's two ports do NOT always share the same
address (addr_q vs. addr_q ^ PARTNER_XOR under use_partner_addr1) -- so the
wrapper template gives EACH port its own repair_remap_row instance, not one
shared remap fed by a single port's address. march_2rw_fsm's fail_valid/
fail_addr stream is still a single-address-per-cycle echo, correct because
the two ports never need to report DIFFERENT addresses failing on the same
cycle (see march_2rw_fsm.sv's fail_valid comment and
tests/hardware/test_march_2rw.py's hardened safety-net assertion).
Icarus + make gated, same as the other self-repair e2e tests.
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
PORTS_2RW = {
    "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
    "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"},
}

# Matches sram_spares_tiny_2rw.v's baked-in DEFECT_ADDR/DEFECT_BIT.
DEFECT_1 = (3, 3)
# Matches sram_spares_tiny_2rw_2defect.v's two baked-in defects.
DEFECT_2A = (3, 3)
DEFECT_2B = (1, 1)


def _config(memory_name: str, wrapper_module_name: str, num_spare_rows: int) -> dict:
    return {
        "memory_name": memory_name,
        "wrapper_module_name": wrapper_module_name,
        "addr_width": ADDR_WIDTH,
        "data_width": DATA_WIDTH,
        "we_active_low": True,
        "ports": PORTS_2RW,
        # NO repair_ports: -- mutually exclusive with onchip_selfrepair, the
        # analyzer/sequencer drive the remap directly.
        "redundancy": {"num_spare_rows": num_spare_rows, "num_spare_cols": 0, "onchip_selfrepair": True},
    }


def _generate(tmp_path: Path, config: dict, subdir: str) -> Path:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / subdir, algo="march-2rw")


def _run(wrapper: Path, scenario: str):
    return run_simulation(
        wrapper.parent,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_onchip_selfrepair", "SELFREPAIR_SCENARIO": scenario},
    )


def test_one_defect_two_spares_is_autonomously_repaired(tmp_path: Path) -> None:
    wrapper = _generate(
        tmp_path, _config("sram_spares_tiny_2rw", "sram_spares_tiny_2rw_mbist", 2), "one_defect"
    )
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
    wrapper = _generate(
        tmp_path,
        _config("sram_spares_tiny_2rw_2defect", "sram_spares_tiny_2rw_2defect_mbist", 2),
        "two_ok",
    )
    result = _run(wrapper, "repairable")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


def test_two_defects_one_spare_is_flagged_not_silently_passed(tmp_path: Path) -> None:
    """The DVCon-style check: 2 distinct faulty rows exceed the 1-spare budget
    -- self_repair_fail must read 1 (asserted inside the cocotb test), AND the
    independent re-scan must show a real, partial (not zero, not both) repair:
    exactly one of the two known defects remains."""
    wrapper = _generate(
        tmp_path,
        _config("sram_spares_tiny_2rw_2defect", "sram_spares_tiny_2rw_2defect_mbist", 1),
        "unrepairable",
    )
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
    accident of a single fresh reset."""
    wrapper = _generate(
        tmp_path, _config("sram_spares_tiny_2rw", "sram_spares_tiny_2rw_mbist", 2), "retrigger"
    )
    result = _run(wrapper, "retrigger")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()
