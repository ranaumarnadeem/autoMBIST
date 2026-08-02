from __future__ import annotations

import pytest

from autombist.algo_engine import (
    CampaignError,
    CampaignResult,
    DataBackground,
    FaultRecord,
    FaultResult,
    MemoryParams,
    _common_plusargs,
    merge_background_results,
    standard_backgrounds,
)


def _mem(dw: int = 8) -> MemoryParams:
    return MemoryParams(addr_width=8, data_width=dw)


def test_standard_backgrounds_includes_solid_first() -> None:
    backgrounds = standard_backgrounds(8)
    assert backgrounds[0] == DataBackground("solid", 0)


def test_standard_backgrounds_stripe_count_matches_ceil_log2() -> None:
    # data_width=8 -> ceil(log2(8))=3 stripes, plus the solid entry = 4 total.
    backgrounds = standard_backgrounds(8)
    assert len(backgrounds) == 4
    assert [b.name for b in backgrounds] == ["solid", "stripe0", "stripe1", "stripe2"]


def test_standard_backgrounds_width_1_has_no_stripes() -> None:
    backgrounds = standard_backgrounds(1)
    assert backgrounds == [DataBackground("solid", 0)]


def test_standard_backgrounds_stripes_separate_every_bit_lane_pair() -> None:
    """The property the docstring claims: any two distinct bit lanes i != j
    differ under at least one stripe mask -- otherwise a background loop
    built from these masks could still fail to expose some intra-word
    coupling pair."""
    dw = 8
    backgrounds = standard_backgrounds(dw)
    stripes = [b.mask for b in backgrounds if b.name != "solid"]
    for i in range(dw):
        for j in range(i + 1, dw):
            assert any(((m >> i) & 1) != ((m >> j) & 1) for m in stripes), (i, j)


def test_standard_backgrounds_masks_fit_data_width() -> None:
    dw = 5
    for bg in standard_backgrounds(dw):
        assert 0 <= bg.mask < (1 << dw)


def test_common_plusargs_omits_background_when_none() -> None:
    assert _common_plusargs(_mem()) == ["+INIT=1"]
    assert _common_plusargs(_mem(), None) == ["+INIT=1"]


def test_common_plusargs_omits_background_when_solid() -> None:
    # mask=0 (the 'solid' background) must be byte-identical to omitting
    # +BACKGROUND entirely -- this is the "default behavior unchanged" guardrail.
    assert _common_plusargs(_mem(), DataBackground("solid", 0)) == ["+INIT=1"]


def test_common_plusargs_emits_background_hex_when_nonzero() -> None:
    args = _common_plusargs(_mem(), DataBackground("stripe0", 0xAA))
    assert args == ["+INIT=1", "+BACKGROUND=aa"]


def test_common_plusargs_respects_init_val() -> None:
    mem = MemoryParams(addr_width=8, data_width=8, init_val=0)
    assert _common_plusargs(mem, DataBackground("stripe1", 0x0F)) == ["+INIT=0", "+BACKGROUND=f"]


def _fault_result(i: int, *, detected: bool, elem: int | None = None,
                   op: int | None = None) -> FaultResult:
    return FaultResult(
        index=i, record=FaultRecord("SA0", i, 0), detected=detected,
        elem=elem, op=op, addr=i if detected else None,
        xor="1" if detected else None,
    )


def _campaign(name: str, faults: list[FaultResult], *, build=1.0, run=2.0) -> CampaignResult:
    mem = _mem()
    detected = sum(1 for f in faults if f.detected)
    total = len(faults)
    coverage = 100.0 if total == 0 else (detected / total) * 100.0
    return CampaignResult(
        algo_name="mytest", mem=mem, golden_clean=True, faults=faults,
        detected=detected, total=total, coverage_percent=coverage,
        build_seconds=build, run_seconds=run, sim="verilator",
    )


def test_merge_background_results_detected_if_any_background_caught_it() -> None:
    per_bg = {
        "solid": _campaign("solid", [_fault_result(0, detected=False)]),
        "stripe0": _campaign("stripe0", [_fault_result(0, detected=True, elem=2, op=1)]),
    }
    merged = merge_background_results(per_bg)
    assert merged.detected == 1
    assert merged.faults[0].detected is True
    assert (merged.faults[0].elem, merged.faults[0].op) == (2, 1)


def test_merge_background_results_escaped_when_every_background_escapes() -> None:
    per_bg = {
        "solid": _campaign("solid", [_fault_result(0, detected=False)]),
        "stripe0": _campaign("stripe0", [_fault_result(0, detected=False)]),
    }
    merged = merge_background_results(per_bg)
    assert merged.detected == 0
    r = merged.faults[0]
    assert (r.detected, r.elem, r.op, r.addr, r.xor, r.activations) == (False, None, None, None, None, None)


def test_merge_background_results_uses_first_detecting_background_in_insertion_order() -> None:
    per_bg = {
        "solid": _campaign("solid", [_fault_result(0, detected=True, elem=1, op=0)]),
        "stripe0": _campaign("stripe0", [_fault_result(0, detected=True, elem=9, op=3)]),
    }
    merged = merge_background_results(per_bg)
    assert (merged.faults[0].elem, merged.faults[0].op) == (1, 0)


def test_merge_background_results_sums_build_and_run_seconds() -> None:
    per_bg = {
        "solid": _campaign("solid", [_fault_result(0, detected=False)], build=1.0, run=2.0),
        "stripe0": _campaign("stripe0", [_fault_result(0, detected=False)], build=3.0, run=4.0),
    }
    merged = merge_background_results(per_bg)
    assert merged.build_seconds == pytest.approx(4.0)
    assert merged.run_seconds == pytest.approx(6.0)


def test_merge_background_results_records_backgrounds_run() -> None:
    per_bg = {
        "solid": _campaign("solid", [_fault_result(0, detected=False)]),
        "stripe0": _campaign("stripe0", [_fault_result(0, detected=False)]),
    }
    merged = merge_background_results(per_bg)
    assert merged.backgrounds_run == ["solid", "stripe0"]


def test_merge_background_results_rejects_empty_input() -> None:
    with pytest.raises(CampaignError):
        merge_background_results({})


def test_merge_background_results_rejects_mismatched_fault_counts() -> None:
    per_bg = {
        "solid": _campaign("solid", [_fault_result(0, detected=False)]),
        "stripe0": _campaign("stripe0", [_fault_result(0, detected=False), _fault_result(1, detected=False)]),
    }
    with pytest.raises(CampaignError):
        merge_background_results(per_bg)


def test_campaign_result_to_dict_omits_backgrounds_run_when_none() -> None:
    result = _campaign("solid", [_fault_result(0, detected=False)])
    assert "backgrounds_run" not in result.to_dict()


def test_campaign_result_to_dict_includes_backgrounds_run_when_set() -> None:
    per_bg = {
        "solid": _campaign("solid", [_fault_result(0, detected=False)]),
        "stripe0": _campaign("stripe0", [_fault_result(0, detected=False)]),
    }
    merged = merge_background_results(per_bg)
    assert merged.to_dict()["backgrounds_run"] == ["solid", "stripe0"]
