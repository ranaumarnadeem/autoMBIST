"""End-to-end proof that the diagnosis fail-bitmap pins the EXACT (addr, bit) of
a known injected defect, through the real chain: fault_ram.sv injects the defect
-> march_engine.sv detects it and prints det_addr/det_xor -> parse_result_line
-> _decode_xor_bits (MSB-first) -> build_diagnosis_cells. This is the load-
bearing input a future BIRA step consumes; if any link skewed the address or bit
(endianness being the classic trap), this test fails. Verilator-gated.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.alg_spec import resolve_algo  # noqa: E402
from autombist.algo_engine import FaultRecord, MemoryParams, run_algo_campaign  # noqa: E402
from autombist.algo_reporting import build_diagnosis_cells  # noqa: E402

ADDR_WIDTH = 4  # depth 16
DATA_WIDTH = 8

# Known defect sites spanning low bit (0), a mid bit, and the HIGH bit (7) so an
# MSB/LSB decode error cannot pass unnoticed; distinct addresses across the depth.
DEFECTS = [
    ("SA0", 3, 0),
    ("SA0", 5, 2),
    ("SA1", 10, 7),
    ("SA1", 15, 5),
]


def _observed_fail_bitmap() -> dict[tuple[int, int], int]:
    """Run one march_c campaign injecting all DEFECTS, then return the observed
    fail bitmap {(addr, bit): times_observed_mismatch} the diagnosis reports."""
    mem = MemoryParams(addr_width=ADDR_WIDTH, data_width=DATA_WIDTH, init_val=1)
    faults = [FaultRecord(t, a, b, 0, 0, 0, 0) for (t, a, b) in DEFECTS]
    result = run_algo_campaign(mem, resolve_algo("march_c"), faults)
    # march_c detects every stuck-at -- sanity-check that so the coordinate
    # assertion below is meaningful (not vacuously empty).
    assert result.detected == len(DEFECTS), f"expected all detected, got {result.detected}"
    cells = build_diagnosis_cells(result)
    return {
        (c["addr"], c["bit"]): c["times_observed_mismatch"]
        for c in cells
        if c["times_observed_mismatch"] > 0
    }


def test_known_stuck_at_defects_pinned_to_exact_coordinates() -> None:
    obs = _observed_fail_bitmap()
    expected = {(a, b) for (_t, a, b) in DEFECTS}
    assert set(obs) == expected, (
        f"observed fail bitmap {sorted(obs)} != injected sites {sorted(expected)}"
    )
    # Every injected defect was observed at least once at its exact cell.
    for (_t, a, b) in DEFECTS:
        assert obs[(a, b)] >= 1


def test_high_bit_defect_not_mirrored_end_to_end() -> None:
    """The specific endianness guard, end to end: SA1@10.7 must NOT appear at
    (10, 0) (the naive-decode mirror of bit 7 in an 8-bit word)."""
    obs = _observed_fail_bitmap()
    assert (10, 7) in obs
    assert (10, 0) not in obs
