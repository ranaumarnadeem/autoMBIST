"""The march fail log on real RTL: what the BIST algorithm itself flags, against
the injected faults and the functional-port scan.

Icarus + make gated, like every other fault-injection e2e test here.
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

from autombist.bira_input import observed_cells  # noqa: E402
from autombist.fault_gen import FaultType, generate_fault_masks  # noqa: E402
from autombist.generator import generate_from_config  # noqa: E402
from autombist.runner import run_simulation  # noqa: E402

TINY = {
    "memory_name": "sram_tiny", "wrapper_module_name": "sram_tiny_mbist", "addr_width": 2, "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}
# The OpenRAM-style demo macro: combinational read (latency 0), 16 words of 8 bits.
DEMO = {
    "memory_name": "input_demo_8x16_scn4m", "wrapper_module_name": "input_demo_8x16_scn4m_mbist",
    "addr_width": 4, "data_width": 8, "read_latency": 0, "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}
TWO_RW = {
    "memory_name": "sram_2rw_dut", "wrapper_module_name": "sram_2rw_dut_mbist", "addr_width": 6, "data_width": 8,
    "we_active_low": True, "ports": {
        "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
        "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"},
    },
}
ONE_R_ONE_W = {
    "memory_name": "sram_1r1w_dut", "wrapper_module_name": "sram_1r1w_dut_mbist", "addr_width": 6, "data_width": 8,
    "we_active_low": True, "ports": {
        "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
        "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
    },
}
SINGLE_PORT_ALGOS = ["march-c", "march-raw", "march-x", "mats-plus", "checkerboard"]


def _generate(tmp_path: Path, config: dict, **kwargs) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / "out", use_saboteur=True, **kwargs).parent


def _stuck_truth(config: dict, faults: int, seed: int) -> set[tuple[int, int]]:
    sa0, sa1 = generate_fault_masks(addr_width=config["addr_width"], data_width=config["data_width"],
                                    fault_type=FaultType.STUCK_AT, faults=faults, seed=seed)
    return {(a, b) for a in range(1 << config["addr_width"]) for b in range(config["data_width"])
            if not (sa0[a] >> b) & 1 or (sa1[a] >> b) & 1}


def _passed(report: dict) -> bool:
    summary = (report.get("junit") or {}).get("summary") or {}
    return not summary.get("failures") and not summary.get("errors")


@pytest.mark.parametrize("algo", SINGLE_PORT_ALGOS)
def test_a_clean_march_logs_nothing(tmp_path: Path, algo: str) -> None:
    module = _generate(tmp_path, TINY, faults=0, fault_seed=0, fault_type="stuck-at", algo=algo)
    report = run_simulation(module, march_log=True).report
    assert report["march_log"] == {"events": 0, "cells": 0, "unknown": 0, "bist_fail": 0}
    assert report["march_fail_bitmap"] == [] and _passed(report)


@pytest.mark.parametrize(("config", "algo"), [
    *((TINY, algo) for algo in SINGLE_PORT_ALGOS),
    (DEMO, "march-c"),
    (DEMO, "checkerboard"),
    (TWO_RW, "march-2rw"),
    (ONE_R_ONE_W, "march-1r1w"),
])
def test_stuck_at_cells_seen_by_the_march_are_the_injected_ones(tmp_path: Path, config: dict, algo: str) -> None:
    faults, seed = 3, 11
    module = _generate(tmp_path, config, faults=faults, fault_seed=seed, fault_type="stuck-at", algo=algo)
    report = run_simulation(module, march_log=True).report

    assert _passed(report), report.get("junit")
    # an event is one failing read, which can carry several of the cells
    assert report["march_log"]["bist_fail"] == 1 and report["march_log"]["events"] >= 1
    assert observed_cells(report, "march") == _stuck_truth(config, faults, seed)
    assert "fail_bitmap" not in report  # one observation per run


@pytest.mark.parametrize("config", [TINY, DEMO])
def test_the_march_and_the_functional_scan_agree_on_stuck_at(tmp_path: Path, config: dict) -> None:
    module = _generate(tmp_path, config, faults=4, fault_seed=3, fault_type="stuck-at", algo="march-c")
    march = observed_cells(run_simulation(module, march_log=True).report, "march")
    functional = observed_cells(run_simulation(module, fail_scan=True).report, "functional")
    assert march == functional == _stuck_truth(config, 4, 3)


@pytest.mark.parametrize("fault_type", ["transition-up", "transition-down"])
def test_transition_faults_are_seen_by_the_march_but_not_by_a_read_back(tmp_path: Path, fault_type: str) -> None:
    """The saboteur's transition fault delays a write. March RAW reads right
    after writing, soon enough to see it (march-c's next read comes after the
    write has settled, as test_mbist.test_transition_faults also assumes); the
    functional scan reads long after."""
    faults, seed = 3, 2
    module = _generate(tmp_path, TINY, faults=faults, fault_seed=seed, fault_type=fault_type, algo="march-raw")
    masks, _ = generate_fault_masks(addr_width=2, data_width=4, fault_type=FaultType(fault_type), faults=faults, seed=seed)
    truth = {(a, b) for a in range(4) for b in range(4) if (masks[a] >> b) & 1}

    march_report = run_simulation(module, march_log=True).report
    march = observed_cells(march_report, "march")
    assert _passed(march_report) and march and march <= truth
    assert observed_cells(run_simulation(module, fail_scan=True).report, "functional") == set()


def test_one_run_cannot_be_both_observations(tmp_path: Path) -> None:
    module = _generate(tmp_path, TINY, faults=1, fault_seed=0, fault_type="stuck-at", algo="march-c")
    with pytest.raises(ValueError, match="choose one"):
        run_simulation(module, fail_scan=True, march_log=True)
