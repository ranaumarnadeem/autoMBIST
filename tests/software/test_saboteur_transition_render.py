"""Render-level regression guards for the Phase 3 transition write-transparency
invariant. Pure Python (no EDA tools). Proves the invariant is added ONLY to the
transition-up/transition-down saboteur branches and never leaks into the
stuck-at / port-coupling branches -- the direct guard that Phase 3 did not
perturb the byte-identical rendering of the well-tested non-transition paths.
The end-to-end sim behavior (the invariant actually holding) is covered by
tests/integration/test_transition_golden_check.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import render_saboteur  # noqa: E402

# Markers the Phase 3 invariant introduces; must appear in transition renders
# and NEVER in stuck-at / port-coupling renders.
_MARKERS = ("write-transparency invariant", "GOLDEN_CHECK_ACTIVE", "GOLDEN_MISMATCH", "gchk_active")

_SINGLE = {
    "memory_name": "m", "addr_width": 3, "data_width": 8, "read_latency": 1,
    "algo": "march-c", "pulse_width_ns": 2, "sa0_faults_file": "s0", "sa1_faults_file": "s1",
    "normalized_ports": {"p": {"type": "rw", "clk": "c", "csb": "cs", "we": "w", "addr": "a", "din": "d", "dout": "o"}},
}
_2RW = {
    "memory_name": "m", "addr_width": 3, "data_width": 8, "read_latency": 1,
    "algo": "march-2rw", "pulse_width_ns": 2, "sa0_faults_file": "s0", "sa1_faults_file": "s1",
    "normalized_ports": {
        "a": {"type": "rw", "clk": "cA", "csb": "csA", "we": "wA", "addr": "aA", "din": "dA", "dout": "oA"},
        "b": {"type": "rw", "clk": "cB", "csb": "csB", "we": "wB", "addr": "aB", "din": "dB", "dout": "oB"},
    },
}
_1R1W = {
    "memory_name": "m", "addr_width": 3, "data_width": 8, "read_latency": 1,
    "algo": "march-1r1w", "pulse_width_ns": 2, "sa0_faults_file": "s0", "sa1_faults_file": "s1", "pc_faults_file": "pc",
    "normalized_ports": {
        "r": {"type": "r", "clk": "cA", "csb": "csA", "addr": "aA", "dout": "oA"},
        "w": {"type": "w", "clk": "cB", "csb": "csB", "we": "wB", "addr": "aB", "din": "dB"},
    },
}


def _render(base: dict, fault_type: str) -> str:
    cfg = dict(base, fault_type=fault_type)
    if fault_type in ("transition-up", "transition-down"):
        cfg["tf_up_faults_file"] = "tu"
        cfg["tf_down_faults_file"] = "td"
    return render_saboteur(cfg)


def test_invariant_absent_from_stuck_at_and_port_coupling() -> None:
    """The Phase 3 machinery must NOT appear in any non-transition render."""
    non_transition = [
        (_SINGLE, "stuck-at"),
        (_2RW, "stuck-at"),
        (_1R1W, "stuck-at"),
        (_1R1W, "port-coupling"),
    ]
    for base, ft in non_transition:
        text = _render(base, ft)
        for marker in _MARKERS:
            assert marker not in text, f"{marker!r} leaked into {base['algo']} {ft} render"


def test_invariant_present_in_every_transition_branch() -> None:
    """Each transition branch across all three topologies carries the invariant."""
    transition = [
        (_SINGLE, "transition-up"), (_SINGLE, "transition-down"),
        (_2RW, "transition-up"), (_2RW, "transition-down"),
        (_1R1W, "transition-up"), (_1R1W, "transition-down"),
    ]
    for base, ft in transition:
        text = _render(base, ft)
        assert "write-transparency invariant" in text
        assert "GOLDEN_CHECK_ACTIVE" in text
        assert "GOLDEN_MISMATCH" in text


def test_2rw_invariant_checks_both_ports() -> None:
    text = _render(_2RW, "transition-up")
    assert "port=0" in text and "port=1" in text
