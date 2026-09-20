"""Real Verilator campaign proof for run_shared_campaign() -- step 7 of
docs/shared-hierarchical-mbist-plan.md's implementation order, the final
step of that plan's v1 (shared-bus-only) scope.

A real 2-memory campaign, march_c, against a hand-derived expected
detect/escape split spanning both memories:
  - SA0 on memory 0: DETECTED -- any march test catches a simple stuck-at.
  - SA1 on memory 1: DETECTED -- same, on the OTHER memory (proves
    per-memory fault targeting -- step 5 -- actually reaches
    run_shared_campaign's own per-fault plusarg construction correctly,
    not just the raw engine).
  - DRF (Data Retention Fault) on memory 0, a huge idle threshold: ESCAPED
    -- already documented, not newly asserted here: engine/README.md's own
    "Idle/wait op and Data Retention Fault (DRF)" section states DRF
    "escapes unconditionally" against March C- (no algorithm here issues a
    wait op at all -- shared_engine.sv's built-in MARCHCM table has none),
    so no realistic idle threshold is ever reached within one march pass.
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

from autombist.algo_engine import (  # noqa: E402
    CampaignError,
    FaultRecord,
    MemoryParams,
    SharedMemoryParams,
    run_shared_campaign,
)


def _shared(num_memories: int = 2) -> SharedMemoryParams:
    return SharedMemoryParams(
        memories=[MemoryParams(addr_width=4, data_width=8) for _ in range(num_memories)]
    )


def test_golden_run_is_clean() -> None:
    result = run_shared_campaign(_shared(), "MARCHCM", [])
    assert result.golden_clean is True
    assert result.algo_name == "MARCHCM_SHARED"
    assert result.total == 0


def test_hand_derived_detect_escape_split_across_both_memories() -> None:
    faults = [
        FaultRecord(type="SA0", vaddr=3, vbit=2, mi=0),
        FaultRecord(type="SA1", vaddr=5, vbit=1, mi=1),
        FaultRecord(type="DRF", vaddr=2, vbit=0, p0=1_000_000, mi=0),
    ]
    result = run_shared_campaign(_shared(), "MARCHCM", faults)

    assert result.total == 3
    assert result.detected == 2

    by_type_mi = {(r.record.type, r.record.mi): r.detected for r in result.faults}
    assert by_type_mi[("SA0", 0)] is True
    assert by_type_mi[("SA1", 1)] is True
    assert by_type_mi[("DRF", 0)] is False


def test_rejects_mi_out_of_range() -> None:
    faults = [FaultRecord(type="SA0", vaddr=3, vbit=2, mi=5)]
    with pytest.raises(CampaignError, match="out of range for 2 memories"):
        run_shared_campaign(_shared(), "MARCHCM", faults)


def test_rejects_unknown_alg() -> None:
    with pytest.raises(CampaignError, match="alg must be one of"):
        run_shared_campaign(_shared(), "BOGUS", [])


def test_rejects_mismatched_geometry() -> None:
    shared = SharedMemoryParams(memories=[
        MemoryParams(addr_width=4, data_width=8),
        MemoryParams(addr_width=5, data_width=8),
    ])
    with pytest.raises(CampaignError, match=r"memories\[1\]\.addr_width"):
        run_shared_campaign(shared, "MARCHCM", [])
