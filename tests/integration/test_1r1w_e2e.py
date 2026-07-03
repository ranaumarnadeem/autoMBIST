"""Real (non-mocked) end-to-end verification of the march-1r1w path: generate
-> simulate, actually invoking Icarus Verilog + cocotb via the real Makefile
flow -- mirrors tests/integration/test_array_e2e.py's structure but for the
new 1-read-port + 1-write-port algo family.

The DUT memory (tests/hardware/sram_1r1w_dut.v) is a renamed copy of
rtl/sram_model_1r1w.sv, so its port names (clk0/csb0/addr0/dout0 for the read
port; clk1/csb1/web1/addr1/din1 for the write port) match this test's config
exactly -- no pin-name translation layer needed.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from autombist.generator import generate_from_config
from autombist.runner import run_simulation

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("make") is None,
    reason="needs Icarus Verilog + make on PATH (Linux/WSL only)",
)

CONFIG = {
    "memory_name": "sram_1r1w_dut",
    "wrapper_module_name": "sram_1r1w_dut_mbist",
    "addr_width": 6,
    "data_width": 8,
    "we_active_low": True,
    "ports": {
        "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
        "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
    },
}


def _write_config(path: Path, config: dict) -> None:
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_clean_simulation_passes(tmp_path: Path) -> None:
    """Golden (fault-free) simulation must report bist_fail == 0 for the
    march-1r1w algo -- the base correctness contract, same as the single-port
    algos in test_array_e2e.py."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_config(config_path, CONFIG)

    wrapper_path = generate_from_config(config_path, outdir, algo="march-1r1w")
    result = run_simulation(wrapper_path.parent, verbose=False)

    assert result.returncode == 0
    assert result.report["status"] == "pass"


def test_stuck_at_fault_simulation_detects_faults(tmp_path: Path) -> None:
    """A stuck-at fault seeded into the shared array must trip bist_fail
    regardless of which port (read port 0 or write port 1) touches the
    faulted cell -- the array-fault detection contract for march-1r1w."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_config(config_path, CONFIG)

    wrapper_path = generate_from_config(
        config_path, outdir, use_saboteur=True, faults=4, fault_seed=1,
        fault_type="stuck-at", algo="march-1r1w",
    )
    result = run_simulation(wrapper_path.parent, verbose=False)

    assert result.returncode == 0
    assert result.report["fault_metrics"]["injected_faults"] == 4

    metrics = result.report["fault_metrics"]
    assert metrics["detected_faults"] is not None and metrics["detected_faults"] > 0


def test_stuck_at_fault_different_seeds_all_detected(tmp_path: Path) -> None:
    """Sweep a couple of seeds so detection isn't an artifact of one lucky
    fault placement -- every seed's injected faults must be detected."""
    for seed in (2, 3):
        config_path = tmp_path / f"config_{seed}.yml"
        outdir = tmp_path / f"out_{seed}"
        config = dict(CONFIG)
        config["memory_name"] = f"sram_1r1w_dut"
        _write_config(config_path, config)

        wrapper_path = generate_from_config(
            config_path, outdir, use_saboteur=True, faults=6, fault_seed=seed,
            fault_type="stuck-at", algo="march-1r1w",
        )
        result = run_simulation(wrapper_path.parent, verbose=False)

        assert result.returncode == 0
        metrics = result.report["fault_metrics"]
        assert metrics["injected_faults"] == 6
        assert metrics["detected_faults"] is not None and metrics["detected_faults"] > 0
