"""Real-Verilator completeness proofs for run_word_oriented_campaign (CFid/
CFdst intra-word coupling coverage), per van de Goor & Tlili, "March tests
for word-oriented memories," DATE 1998. See docs/algo-library-expansion-plan.md
(gitignored) for the full citation trail and how these numbers were first
established (a throwaway spike, deleted after use, before this campaign
integration existed)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.algo_engine import (  # noqa: E402
    CampaignError,
    FaultRecord,
    MemoryParams,
    run_word_oriented_campaign,
)


def _every_intra_word_bit_pair(vaddr: int, data_width: int):
    for vb in range(data_width):
        for ab in range(data_width):
            if vb != ab:
                yield vb, ab


def test_golden_run_is_clean_for_both_modes(tmp_path: Path) -> None:
    mem = MemoryParams(addr_width=3, data_width=4, init_val=1)
    cfid = run_word_oriented_campaign(mem, [], mode="cfid", workdir=tmp_path / "cfid")
    cfdst = run_word_oriented_campaign(mem, [], mode="cfdst", workdir=tmp_path / "cfdst")
    assert cfid.golden_clean is True
    assert cfdst.golden_clean is True
    assert cfid.algo_name == "CFID_WOM"
    assert cfdst.algo_name == "CFDST_WOM"


def test_cfid_intra_word_completeness_dw4(tmp_path: Path) -> None:
    """Full, proven completeness: every ordered (vbit, abit) pair in a 4-bit
    word x both forced polarities (P1), P0=2 ("either" transition, this
    project's own default convention for CFIN/CFID -- see
    algo_engine.py's _coupling_p0_p1). 24/24, matching the exact numbers
    from the throwaway spike this campaign integration reproduces."""
    mem = MemoryParams(addr_width=3, data_width=4, init_val=1)
    faults = [
        FaultRecord("CFID", vaddr=3, vbit=vb, aaddr=3, abit=ab, p0=2, p1=p1)
        for vb, ab in _every_intra_word_bit_pair(3, 4)
        for p1 in (0, 1)
    ]
    assert len(faults) == 24

    result = run_word_oriented_campaign(mem, faults, mode="cfid", workdir=tmp_path)
    assert result.total == 24
    assert result.detected == 24, (
        f"expected every intra-word CFID instance to be caught, got {result.detected}/24"
    )


def test_cfdst_mode_against_cfds_has_the_measured_non_transition_gap(tmp_path: Path) -> None:
    """CFdst-mode measured against this project's CFDS primitive (the
    closest existing match -- CFDS is parameterized by which aggressor op
    triggers the disturb, the same shape the paper's own CFdst subtypes
    have). 45/60 overall. Per-P0 breakdown, MEASURED (not the simpler
    all-or-nothing split a first look at the escapes suggested -- checked
    directly rather than assumed, see docs/algo-library-expansion-plan.md
    section 1.3/1b for the correction): P0 in {0=r0, 1=r1, 4=any read} (the
    paper's own read-disturb CFdst scope) is fully detected, 12/12 each. P0
    in {2, 3} (non-transition write triggers) is OUTSIDE the source paper's
    own CFdst subtype list (transition-write and read disturbs only -- see
    word_oriented.py's module docstring) and only partially detected --
    P0=2 (non-transition w0): 6/12; P0=3 (non-transition w1): 3/12 -- by
    coincidence, wherever the DBS sequence happens to also produce the
    right non-transition write at that specific bit position, not by
    design. Asserted per-P0 below so a regression in any one bucket is
    caught precisely, not just as a combined 45/60 that could mask which
    direction broke."""
    mem = MemoryParams(addr_width=3, data_width=4, init_val=1)
    expected_detected_by_p0 = {0: 12, 1: 12, 2: 6, 3: 3, 4: 12}

    faults = [
        FaultRecord("CFDS", vaddr=3, vbit=vb, aaddr=3, abit=ab, p0=p0, p1=0)
        for vb, ab in _every_intra_word_bit_pair(3, 4)
        for p0 in sorted(expected_detected_by_p0)
    ]
    assert len(faults) == 60

    result = run_word_oriented_campaign(mem, faults, mode="cfdst", workdir=tmp_path)
    assert result.total == 60
    assert result.detected == 45

    by_p0: dict[int, list[bool]] = {}
    for fr in result.faults:
        by_p0.setdefault(fr.record.p0, []).append(fr.detected)

    for p0, expected in expected_detected_by_p0.items():
        actual = sum(by_p0[p0])
        assert actual == expected, f"P0={p0}: expected {expected}/12 detected, got {actual}/12"


def test_rejects_multi_port(tmp_path: Path) -> None:
    mem = MemoryParams(addr_width=3, data_width=4, num_ports=2)
    with pytest.raises(CampaignError, match="num_ports=1"):
        run_word_oriented_campaign(mem, [], mode="cfid", workdir=tmp_path)


def test_rejects_unknown_mode(tmp_path: Path) -> None:
    mem = MemoryParams(addr_width=3, data_width=4)
    with pytest.raises(CampaignError, match="mode must be one of"):
        run_word_oriented_campaign(mem, [], mode="bogus", workdir=tmp_path)
