"""Real (non-mocked) end-to-end verification of the port-coupling fault type
over march-1r1w: generate -> simulate, actually invoking Icarus Verilog +
cocotb via the real Makefile flow -- mirrors tests/integration/
test_1r1w_e2e.py's structure exactly, but exercises fault_type=
"port-coupling" instead of "stuck-at".

This is the HIGH-LEVEL half of the differential proof for the port-coupling
fault type. march-1r1w's own algorithm (rtl/march_1r1w/march_1r1w_algo.sv,
elements E1-E4) already issues several genuinely same-cycle, same-address
concurrent read(port0)+write(port1) accesses as part of its normal march
sequence -- no special-casing is needed to sensitize the fault, a real
concurrent MBIST run naturally exercises it.

  1. POSITIVE CONTROL (test_port_coupling_fault_is_detected): with faults
     injected, bist_fail must be 1 -- the concurrent algorithm actually
     catches the inter-port bridging defect.
  2. GOLDEN GATE (test_no_faults_no_false_positive /
     test_clean_simulation_no_saboteur_passes): with zero faults (an
     all-zero pc_mask, or use_saboteur disabled entirely), bist_fail must be
     0 -- the port-coupling mechanism itself introduces no false positives
     on a golden memory.

The load-bearing, low-level proof that detection is SPECIFICALLY
attributable to concurrency (same fault, only access timing differs) lives
in tests/hardware/test_port_coupling_diff.py, which drives the generated
saboteur module's raw pins directly and bypasses march_1r1w_fsm entirely.
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


def test_port_coupling_fault_is_detected(tmp_path: Path) -> None:
    """POSITIVE CONTROL: a real march-1r1w run with fault_type="port-coupling"
    and faults actually injected must report bist_fail == 1 INSIDE the
    simulation. march-1r1w's E1-E4 phases issue genuine same-cycle,
    same-address read(port0)+write(port1) accesses as part of the
    algorithm's normal sequence (see march_1r1w_algo.sv's header comment) --
    no special test sequencing is needed for the concurrent algorithm to
    sensitize the fault.

    Note: result.report["status"] == "pass" here is CORRECT, not a
    contradiction -- it reflects the cocotb test suite's own outcome, and
    tests/hardware/test_mbist.py's test_port_coupling_faults cocotb test
    itself asserts int(dut.bist_fail.value) == 1 whenever fault sites exist
    (see the pytest fixture body of that assertion) and only reports FAIL to
    pytest if that assertion is violated. So a passing pytest run together
    with detected_faults > 0 (fault_metrics below) is exactly the "MBIST
    correctly tripped bist_fail on the injected fault" outcome -- mirrors
    test_1r1w_e2e.py's stuck-at positive control (test_stuck_at_fault_
    simulation_detects_faults) exactly."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_config(config_path, CONFIG)

    wrapper_path = generate_from_config(
        config_path, outdir, use_saboteur=True, faults=4, fault_seed=1,
        fault_type="port-coupling", algo="march-1r1w",
    )
    result = run_simulation(wrapper_path.parent, verbose=False)

    assert result.returncode == 0
    assert result.report["fault_metrics"]["injected_faults"] == 4

    metrics = result.report["fault_metrics"]
    assert metrics["detected_faults"] is not None and metrics["detected_faults"] > 0


def test_port_coupling_fault_detected_across_seeds(tmp_path: Path) -> None:
    """Sweep a couple of seeds so detection isn't an artifact of one lucky
    fault placement -- every seed's injected port-coupling faults must trip
    bist_fail (surfaced here as a passing cocotb run with detected_faults >
    0 -- see the note on test_port_coupling_fault_is_detected above)."""
    for seed in (2, 3):
        config_path = tmp_path / f"config_{seed}.yml"
        outdir = tmp_path / f"out_{seed}"
        config = dict(CONFIG)
        config["memory_name"] = "sram_1r1w_dut"
        _write_config(config_path, config)

        wrapper_path = generate_from_config(
            config_path, outdir, use_saboteur=True, faults=6, fault_seed=seed,
            fault_type="port-coupling", algo="march-1r1w",
        )
        result = run_simulation(wrapper_path.parent, verbose=False)

        assert result.returncode == 0
        metrics = result.report["fault_metrics"]
        assert metrics["injected_faults"] == 6
        assert metrics["detected_faults"] is not None and metrics["detected_faults"] > 0


def test_no_faults_no_false_positive(tmp_path: Path) -> None:
    """GOLDEN GATE: use_saboteur=True with fault_type="port-coupling" but
    faults=0 (an all-zero pc_mask) must NOT trip bist_fail -- the coupling
    XOR mechanism itself must be inert when the mask has no bits set, so a
    positive detection result elsewhere can be attributed to the injected
    faults, not to the mechanism spuriously firing."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_config(config_path, CONFIG)

    wrapper_path = generate_from_config(
        config_path, outdir, use_saboteur=True, faults=0, fault_seed=None,
        fault_type="port-coupling", algo="march-1r1w",
    )
    result = run_simulation(wrapper_path.parent, verbose=False)

    assert result.returncode == 0
    assert result.report["status"] == "pass"


def test_clean_simulation_no_saboteur_passes(tmp_path: Path) -> None:
    """GOLDEN GATE (baseline): the same march-1r1w config with the saboteur
    disabled entirely must pass cleanly -- the base correctness contract,
    independent of the port-coupling fault mechanism altogether."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_config(config_path, CONFIG)

    wrapper_path = generate_from_config(config_path, outdir, algo="march-1r1w")
    result = run_simulation(wrapper_path.parent, verbose=False)

    assert result.returncode == 0
    assert result.report["status"] == "pass"
