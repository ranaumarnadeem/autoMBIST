"""End-to-end validation of the Phase 3 transition write-transparency invariant
(Icarus-gated). A transition-fault campaign now carries a non-fatal in-RTL
invariant: at any address with no transition-fault mask bit, the value the
saboteur commits (real_din) must equal the intended write (din). This confirms
the transition injection is transparent for fault-free data -- guarding the
masking logic against a future change that corrupts unfaulted words.

Why this shape (and not a real-macro cross-check): wrapping the real memory
macro and comparing its reads to the shadow model's reads was tried and does
not work -- the two models have legitimately different write-to-read visibility
timing, so a cycle-accurate comparison produces spurious mismatches even when
both are correct. (This is exactly why stuck-at / port-coupling wrap the real
macro but transition uses a shadow: transition needs the pre-write value and a
delay queue, which a black-box macro cannot provide.) The real macro remains
validated by the stuck-at / port-coupling campaigns.
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

# depth-64 memories so a handful of faults leave many unfaulted addresses for
# the invariant to actually run against.
_SINGLE = {
    "memory_name": "sram_ahi", "wrapper_module_name": "sram_ahi_mbist",
    "addr_width": 6, "data_width": 16, "we_active_low": False,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"},
}
_2RW = {
    "memory_name": "sram_2rw_dut", "wrapper_module_name": "sram_2rw_dut_mbist",
    "addr_width": 6, "data_width": 8, "we_active_low": True,
    "ports": {
        "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
        "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"},
    },
}
_1R1W = {
    "memory_name": "sram_1r1w_dut", "wrapper_module_name": "sram_1r1w_dut_mbist",
    "addr_width": 6, "data_width": 8, "we_active_low": True,
    "ports": {
        "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
        "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
    },
}


def _run_transition(tmp_path: Path, config: dict, algo: str) -> str:
    """Generate + simulate a transition-up campaign; return the simulate.log."""
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper_path = generate_from_config(
        config_path, tmp_path / "out", use_saboteur=True, faults=3, fault_seed=5,
        fault_type="transition-up", pulse_width_ns=2, algo=algo,
    )
    result = run_simulation(wrapper_path.parent, verbose=False)
    assert result.returncode == 0, result.report.get("summary", "")
    return (wrapper_path.parent / "simulate.log").read_text(encoding="utf-8")


def _assert_invariant_ran_clean(log: str) -> None:
    assert "GOLDEN_CHECK_ACTIVE" in log, "the write-transparency invariant never ran (no unfaulted write?)"
    assert "GOLDEN_MISMATCH" not in log, (
        "the transition saboteur corrupted a fault-free word:\n"
        + "\n".join(line for line in log.splitlines() if "GOLDEN_MISMATCH" in line)
    )


def test_single_port_transition_invariant_holds(tmp_path: Path) -> None:
    _assert_invariant_ran_clean(_run_transition(tmp_path, _SINGLE, "march-raw"))


def test_2rw_transition_invariant_holds(tmp_path: Path) -> None:
    _assert_invariant_ran_clean(_run_transition(tmp_path, _2RW, "march-2rw"))


def test_1r1w_transition_invariant_holds(tmp_path: Path) -> None:
    _assert_invariant_ran_clean(_run_transition(tmp_path, _1R1W, "march-1r1w"))
