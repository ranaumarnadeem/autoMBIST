from __future__ import annotations

import json
from pathlib import Path

import pytest

from autombist.algo_engine import CampaignResult, FaultRecord, FaultResult, MemoryParams
from autombist.algo_reporting import (
    coverage_meets_threshold,
    render_campaign_csv,
    render_campaign_json,
    render_campaign_md,
    render_matrix_csv,
    render_matrix_json,
    render_matrix_md,
    write_campaign_report,
    write_matrix_report,
)


def _result(algo_name: str, outcomes: list[tuple[str, int, int, bool]]) -> CampaignResult:
    mem = MemoryParams(addr_width=8, data_width=8)
    faults = [
        FaultResult(
            index=i, record=FaultRecord(t, va, vb, 0, 0, 0, 0), detected=det,
            elem=0 if det else None, op=0 if det else None, addr=va if det else None,
        )
        for i, (t, va, vb, det) in enumerate(outcomes)
    ]
    detected = sum(1 for f in faults if f.detected)
    total = len(faults)
    return CampaignResult(
        algo_name=algo_name, mem=mem, golden_clean=True, faults=faults,
        detected=detected, total=total,
        coverage_percent=100.0 if total == 0 else detected / total * 100.0,
        build_seconds=1.0, run_seconds=0.5, sim="verilator",
    )


def test_coverage_meets_threshold() -> None:
    r = _result("a", [("SA0", 1, 0, True), ("SA1", 2, 0, False)])  # 50%
    assert coverage_meets_threshold(r, None) is True
    assert coverage_meets_threshold(r, 50.0) is True
    assert coverage_meets_threshold(r, 51.0) is False


def test_render_campaign_csv_has_header_and_rows() -> None:
    r = _result("march_c", [("SA0", 1, 0, True), ("SA1", 2, 0, False)])
    csv = render_campaign_csv(r)
    lines = csv.strip().splitlines()
    assert lines[0] == "idx,type,vaddr.vbit,aaddr.abit,p0,p1,result,elem,op,addr,activations"
    assert lines[1] == "0,SA0,1.0,0.0,0,0,DETECTED,0,0,1,"
    assert lines[2] == "1,SA1,2.0,0.0,0,0,ESCAPED,,,,"


def test_render_campaign_md_contains_summary() -> None:
    r = _result("march_c", [("SA0", 1, 0, True)])
    md = render_campaign_md(r)
    assert "march_c" in md
    assert "1/1 (100.00%)" in md
    assert "| idx" in md


def test_render_campaign_json_roundtrips() -> None:
    r = _result("march_c", [("SA0", 1, 0, True), ("SA1", 2, 0, False)])
    payload = json.loads(render_campaign_json(r))
    assert payload["algo_name"] == "march_c"
    assert payload["detected"] == 1 and payload["total"] == 2
    assert len(payload["faults"]) == 2


def test_write_campaign_report_rejects_bad_format(tmp_path: Path) -> None:
    r = _result("march_c", [("SA0", 1, 0, True)])
    with pytest.raises(ValueError, match="unknown report format"):
        write_campaign_report(r, tmp_path / "x.txt", fmt="yaml")


def test_write_campaign_report_writes_file(tmp_path: Path) -> None:
    r = _result("march_c", [("SA0", 1, 0, True)])
    path = write_campaign_report(r, tmp_path / "cov.md", fmt="md")
    assert path.exists() and "march_c" in path.read_text()


def _mp_result(algo_name: str, outcomes: list[tuple[str, int, int, int, int, bool]]) -> CampaignResult:
    """Like _result, but 2-port memory and records carry explicit vport/aport."""
    mem = MemoryParams(addr_width=8, data_width=8, num_ports=2)
    faults = [
        FaultRecord(t, va, vb, 0, 0, 0, 0, vport, aport)
        for (t, va, vb, vport, aport, _det) in outcomes
    ]
    results = [
        FaultResult(index=i, record=faults[i], detected=det)
        for i, (*_rest, det) in enumerate(outcomes)
    ]
    detected = sum(1 for f in results if f.detected)
    total = len(results)
    return CampaignResult(
        algo_name=algo_name, mem=mem, golden_clean=True, faults=results,
        detected=detected, total=total,
        coverage_percent=100.0 if total == 0 else detected / total * 100.0,
        build_seconds=1.0, run_seconds=0.5, sim="verilator",
    )


def test_matrix_row_disambiguates_same_site_different_ports_when_multi_port() -> None:
    r = _mp_result("march_c", [
        ("CFIN", 5, 1, 0, 0, True),   # same-port coupling
        ("CFIN", 5, 1, 0, 1, False),  # cross-port coupling, same site -- must not collide
    ])
    row = r.matrix_row()
    assert row == {"CFIN@5.1#0.0": "D", "CFIN@5.1#0.1": "E"}


def test_matrix_row_single_port_key_unchanged() -> None:
    r = _result("march_c", [("SA0", 1, 0, True)])
    assert r.matrix_row() == {"SA0@1.0": "D"}


def test_campaign_csv_gains_port_columns_only_when_multi_port() -> None:
    r = _mp_result("march_c", [("CFIN", 5, 1, 0, 1, True)])
    csv = render_campaign_csv(r)
    lines = csv.strip().splitlines()
    assert lines[0] == "idx,type,vaddr.vbit,aaddr.abit,p0,p1,result,elem,op,addr,activations,vport,aport"
    assert lines[1].endswith(",0,1")


def test_campaign_json_always_carries_port_fields() -> None:
    r = _result("march_c", [("SA0", 1, 0, True)])
    payload = json.loads(render_campaign_json(r))
    assert payload["mem"]["num_ports"] == 1
    assert payload["faults"][0]["vport"] == 0
    assert payload["faults"][0]["aport"] == 0


def test_comparison_matrix_across_algos() -> None:
    a = _result("march_c", [("SA0", 1, 0, True), ("WDF0", 5, 0, False)])
    b = _result("march_ss", [("SA0", 1, 0, True), ("WDF0", 5, 0, True)])
    csv = render_matrix_csv([a, b])
    lines = csv.strip().splitlines()
    assert lines[0] == "fault,march_c,march_ss"
    assert "SA0@1.0,D,D" in lines
    assert "WDF0@5.0,E,D" in lines
    assert lines[-1] == "total,1/2,2/2"

    md = render_matrix_md([a, b])
    assert "march_c" in md and "march_ss" in md

    payload = json.loads(render_matrix_json([a, b]))
    assert payload["algos"] == ["march_c", "march_ss"]
    assert payload["totals"]["march_ss"]["detected"] == 2


def test_write_matrix_report(tmp_path: Path) -> None:
    a = _result("march_c", [("SA0", 1, 0, True)])
    b = _result("march_ss", [("SA0", 1, 0, True)])
    path = write_matrix_report([a, b], tmp_path / "matrix.csv", fmt="csv")
    assert path.exists()
    assert "march_c,march_ss" in path.read_text()
