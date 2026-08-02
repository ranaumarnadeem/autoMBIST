from __future__ import annotations

import json
from pathlib import Path

import pytest

from autombist.algo_engine import CampaignResult, FaultRecord, FaultResult, MemoryParams
from autombist.algo_reporting import (
    SYNDROME_VALID_FORMATS,
    _decode_xor_bits,
    build_diagnosis_cells,
    compute_syndrome_groups,
    coverage_meets_threshold,
    render_campaign_csv,
    render_campaign_json,
    render_campaign_md,
    render_diagnosis_csv,
    render_diagnosis_json,
    render_diagnosis_md,
    render_matrix_csv,
    render_matrix_json,
    render_matrix_md,
    render_syndrome_csv,
    render_syndrome_json,
    render_syndrome_md,
    write_campaign_report,
    write_diagnosis_report,
    write_matrix_report,
    write_syndrome_report,
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


# --------------------------------------------------------------------------- #
# Diagnosis / fail-bitmap report
# --------------------------------------------------------------------------- #
def test_decode_xor_bits_readme_worked_example() -> None:
    # engine/README.md quick-start: "RESULT DETECTED alg=MARCHCM elem=1 op=0
    # addr=50 xor=00000100" for an 8-bit-wide memory. The single '1' is at
    # (0-indexed, left-to-right) string position 5; Verilog %b prints MSB
    # first, so bit_index = len(xor)-1-i = 8-1-5 = 2.
    assert _decode_xor_bits("00000100") == [2]


def test_decode_xor_bits_all_zero_is_empty() -> None:
    assert _decode_xor_bits("00000000") == []


def test_decode_xor_bits_msb_and_lsb() -> None:
    # leftmost char -> highest bit index; rightmost char -> bit 0.
    assert _decode_xor_bits("10000000") == [7]
    assert _decode_xor_bits("00000001") == [0]


def test_decode_xor_bits_multiple_set_bits_in_msb_first_order() -> None:
    # "11000000" -> bits 7 and 6 mismatched.
    assert _decode_xor_bits("11000000") == [7, 6]


def test_decode_xor_bits_narrow_width() -> None:
    # A 4-bit-wide memory: "0100" -> position 1 -> bit_index = 4-1-1 = 2.
    assert _decode_xor_bits("0100") == [2]


def _diagnosis_result() -> CampaignResult:
    """Hand-built CampaignResult exercising:
      - a coupling-class fault (CFIN) injected at vaddr.vbit=(5,1) whose
        DETECTED observation addr=6 (genuinely different from vaddr=5) with
        xor="00000010" (8-bit) -> decoded mismatch bit = 8-1-6 = 1, so the
        observation cell is (6, 1) -- distinct from the injection cell (5, 1).
        This proves role="observation"-only vs role="both" distinctions work:
        (5,1) is injection-only ("both" would require it to ALSO be observed),
        (6,1) is observation-only.
      - an SA0 fault injected at (10, 3) that is never detected (escapes) --
        proving escaped injection sites still appear in the table.
      - an SA1 fault injected at (5, 1) [same cell as the CFIN above] that IS
        detected, with its own observation landing back on its own vaddr --
        making cell (5,1) both an injection site (for CFIN and SA1) AND an
        observation site (from SA1's own detection), i.e. role="both".
    """
    mem = MemoryParams(addr_width=8, data_width=8)
    faults = [
        FaultResult(
            index=0,
            record=FaultRecord(type="CFIN", vaddr=5, vbit=1, aaddr=6, abit=1, p0=2, p1=0),
            detected=True, elem=0, op=0, addr=6, xor="00000010",
        ),
        FaultResult(
            index=1,
            record=FaultRecord(type="SA0", vaddr=10, vbit=3, aaddr=0, abit=0, p0=0, p1=0),
            detected=False,
        ),
        FaultResult(
            index=2,
            record=FaultRecord(type="SA1", vaddr=5, vbit=1, aaddr=0, abit=0, p0=0, p1=0),
            detected=True, elem=1, op=1, addr=5, xor="01000000",  # bit 8-1-1=6 -- NOT bit 1
        ),
    ]
    detected = sum(1 for f in faults if f.detected)
    total = len(faults)
    return CampaignResult(
        algo_name="march_c", mem=mem, golden_clean=True, faults=faults,
        detected=detected, total=total,
        coverage_percent=100.0 if total == 0 else detected / total * 100.0,
        build_seconds=1.0, run_seconds=0.5, sim="verilator",
    )


def test_build_diagnosis_cells_shapes_and_sorting() -> None:
    cells = build_diagnosis_cells(_diagnosis_result())
    keys = [(c["addr"], c["bit"]) for c in cells]
    # sorted by (addr, bit)
    assert keys == sorted(keys)
    # expected cells: (5,1) injection (CFIN+SA1) -- SA1 detected here too via
    # its own addr=5 xor="01000000" decoding to bit 6, NOT bit 1, so (5,1) is
    # injection-only (never independently observed) -> role "injection".
    # (5,6) is SA1's observation site -> role "observation".
    # (6,1) is CFIN's observation site -> role "observation".
    # (10,3) is the escaped SA0 injection site -> role "injection".
    assert (5, 1) in keys
    assert (5, 6) in keys
    assert (6, 1) in keys
    assert (10, 3) in keys


def test_build_diagnosis_cells_coupling_observation_differs_from_injection() -> None:
    """The CFIN fault's decoded observation cell (6, 1) is a different
    address than its injection site (5, 1) -- the core proof that a
    coupling-class fault's mismatch can be observed elsewhere."""
    cells = {(c["addr"], c["bit"]): c for c in build_diagnosis_cells(_diagnosis_result())}

    injection_cell = cells[(5, 1)]
    assert injection_cell["role"] == "injection"
    assert "CFIN" in injection_cell["fault_types_injected_here"]
    assert "SA1" in injection_cell["fault_types_injected_here"]
    assert injection_cell["detected_as_injection"] is True  # SA1 detected somewhere
    assert injection_cell["escaped_types_here"] == []  # not escaped: SA1 was detected
    assert injection_cell["times_observed_mismatch"] == 0  # never itself an observation site

    observation_cell = cells[(6, 1)]
    assert observation_cell["role"] == "observation"
    assert observation_cell["fault_types_injected_here"] == []
    assert observation_cell["detected_as_injection"] is False
    assert observation_cell["times_observed_mismatch"] == 1
    assert observation_cell["observed_from_fault_types"] == ["CFIN"]


def test_build_diagnosis_cells_escaped_injection_site_still_appears() -> None:
    """An injected-but-never-detected fault (SA0@10.3) must still show up in
    the table, with escaped_types_here populated and no observation data."""
    cells = {(c["addr"], c["bit"]): c for c in build_diagnosis_cells(_diagnosis_result())}
    escaped_cell = cells[(10, 3)]
    assert escaped_cell["role"] == "injection"
    assert escaped_cell["fault_types_injected_here"] == ["SA0"]
    assert escaped_cell["detected_as_injection"] is False
    assert escaped_cell["escaped_types_here"] == ["SA0"]
    assert escaped_cell["times_observed_mismatch"] == 0
    assert escaped_cell["observed_from_fault_types"] == []


def test_build_diagnosis_cells_role_both_when_site_is_also_observed() -> None:
    """Construct a fault whose own detection lands back on its own vaddr.vbit
    -- that cell must be role='both' (injection AND observation)."""
    mem = MemoryParams(addr_width=4, data_width=4)
    faults = [
        FaultResult(
            index=0,
            record=FaultRecord(type="SA1", vaddr=2, vbit=0, aaddr=0, abit=0, p0=0, p1=0),
            detected=True, elem=0, op=0, addr=2, xor="0001",  # bit_index = 4-1-3=0 -> (2,0)
        ),
    ]
    result = CampaignResult(
        algo_name="march_c", mem=mem, golden_clean=True, faults=faults,
        detected=1, total=1, coverage_percent=100.0,
        build_seconds=1.0, run_seconds=0.5, sim="verilator",
    )
    cells = {(c["addr"], c["bit"]): c for c in build_diagnosis_cells(result)}
    assert (2, 0) in cells
    cell = cells[(2, 0)]
    assert cell["role"] == "both"
    assert cell["fault_types_injected_here"] == ["SA1"]
    assert cell["detected_as_injection"] is True
    assert cell["escaped_types_here"] == []
    assert cell["times_observed_mismatch"] == 1
    assert cell["observed_from_fault_types"] == ["SA1"]


def test_build_diagnosis_cells_partial_escape_at_shared_cell_not_masked() -> None:
    """Two DIFFERENT fault types injected at the exact same cell, one
    detected and one genuinely escaped. detected_as_injection is an OR
    across co-located faults (True, since SA0 was caught), but
    escaped_types_here must still list SA1 individually -- it must not be
    masked just because SA0, a different fault at the same cell, happened
    to be detected. This is tracked per-fault-instance, not derived from
    the cell-level OR'd detected_as_injection flag."""
    mem = MemoryParams(addr_width=4, data_width=4)
    faults = [
        FaultResult(
            index=0,
            record=FaultRecord(type="SA0", vaddr=3, vbit=2, aaddr=0, abit=0, p0=0, p1=0),
            detected=True, elem=0, op=0, addr=3, xor="0100",  # bit_index = 4-1-1=2 -> (3,2)
        ),
        FaultResult(
            index=1,
            record=FaultRecord(type="SA1", vaddr=3, vbit=2, aaddr=0, abit=0, p0=0, p1=0),
            detected=False,
        ),
    ]
    result = CampaignResult(
        algo_name="march_c", mem=mem, golden_clean=True, faults=faults,
        detected=1, total=2, coverage_percent=50.0,
        build_seconds=1.0, run_seconds=0.5, sim="verilator",
    )
    cells = {(c["addr"], c["bit"]): c for c in build_diagnosis_cells(result)}
    cell = cells[(3, 2)]
    assert set(cell["fault_types_injected_here"]) == {"SA0", "SA1"}
    assert cell["detected_as_injection"] is True  # SA0 was caught somewhere
    assert cell["escaped_types_here"] == ["SA1"]  # SA1 must NOT be masked by SA0's detection


def test_render_diagnosis_csv_shape() -> None:
    csv = render_diagnosis_csv(_diagnosis_result())
    lines = csv.strip().splitlines()
    assert lines[0] == (
        "addr,bit,role,fault_types_injected_here,detected_as_injection,"
        "escaped_types_here,times_observed_mismatch,observed_from_fault_types"
    )
    # one data row per emitted cell; escaped SA0@10.3 row present with pipe-joined empties
    assert any(line.startswith("10,3,injection,SA0,False,SA0,0,") for line in lines[1:])


def test_render_diagnosis_csv_list_fields_pipe_joined() -> None:
    csv = render_diagnosis_csv(_diagnosis_result())
    lines = csv.strip().splitlines()
    injection_row = next(line for line in lines[1:] if line.startswith("5,1,"))
    assert "CFIN|SA1" in injection_row


def test_render_diagnosis_md_contains_summary_and_table() -> None:
    md = render_diagnosis_md(_diagnosis_result())
    assert "march_c" in md
    assert "2/3" in md
    assert "| addr" in md


def test_render_diagnosis_json_keeps_real_lists() -> None:
    payload = json.loads(render_diagnosis_json(_diagnosis_result()))
    assert payload["algo_name"] == "march_c"
    assert payload["detected"] == 2 and payload["total"] == 3
    cells = {(c["addr"], c["bit"]): c for c in payload["cells"]}
    assert cells[(5, 1)]["fault_types_injected_here"] == ["CFIN", "SA1"]
    assert isinstance(cells[(5, 1)]["fault_types_injected_here"], list)


def test_write_diagnosis_report_rejects_bad_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown diagnosis report format"):
        write_diagnosis_report(_diagnosis_result(), tmp_path / "x.txt", fmt="yaml")


def test_write_diagnosis_report_writes_file(tmp_path: Path) -> None:
    path = write_diagnosis_report(_diagnosis_result(), tmp_path / "diag.md", fmt="md")
    assert path.exists() and "march_c" in path.read_text()


def test_write_diagnosis_report_json_format(tmp_path: Path) -> None:
    path = write_diagnosis_report(_diagnosis_result(), tmp_path / "diag.json", fmt="json")
    payload = json.loads(path.read_text())
    assert "cells" in payload


# --------------------------------------------------------------------------- #
# Blind syndrome-based diagnosis (1.5)
# --------------------------------------------------------------------------- #
def _syndrome_result() -> CampaignResult:
    """Hand-built CampaignResult exercising:
      - SAF(0) and TF<up,0> DETECTED at the identical (elem=1, op=0) --
        an ambiguous group (2 types, indistinguishable by this algorithm).
      - WDF0 DETECTED at a DIFFERENT (elem=2, op=1) -- its own unambiguous
        group, distinguishable from the SAF(0)/TF<up,0> pair.
      - CFIN and CFID both ESCAPED -- grouped into the single (detected=False,
        elem=None, op=None) bucket, also ambiguous (2 types)."""
    mem = MemoryParams(addr_width=8, data_width=8)
    faults = [
        FaultResult(index=0, record=FaultRecord("SA0", 1, 0, 0, 0, 0, 0), detected=True, elem=1, op=0, addr=1),
        FaultResult(index=1, record=FaultRecord("TF0", 2, 0, 0, 0, 0, 0), detected=True, elem=1, op=0, addr=2),
        FaultResult(index=2, record=FaultRecord("WDF0", 3, 0, 0, 0, 0, 0), detected=True, elem=2, op=1, addr=3),
        FaultResult(index=3, record=FaultRecord("CFIN", 4, 0, 5, 0, 2, 0), detected=False),
        FaultResult(index=4, record=FaultRecord("CFID", 6, 0, 7, 0, 2, 1), detected=False),
    ]
    detected = sum(1 for f in faults if f.detected)
    total = len(faults)
    return CampaignResult(
        algo_name="march_c", mem=mem, golden_clean=True, faults=faults,
        detected=detected, total=total,
        coverage_percent=100.0 if total == 0 else detected / total * 100.0,
        build_seconds=1.0, run_seconds=0.5, sim="verilator",
    )


def test_compute_syndrome_groups_flags_same_location_as_ambiguous() -> None:
    groups = compute_syndrome_groups(_syndrome_result())
    same_loc = next(g for g in groups if g["elem"] == 1 and g["op"] == 0)
    assert same_loc["detected"] is True
    assert sorted(same_loc["fault_types"]) == ["SA0", "TF0"]
    assert same_loc["ambiguous"] is True


def test_compute_syndrome_groups_distinguishes_different_locations() -> None:
    groups = compute_syndrome_groups(_syndrome_result())
    distinct = next(g for g in groups if g["elem"] == 2 and g["op"] == 1)
    assert distinct["fault_types"] == ["WDF0"]
    assert distinct["ambiguous"] is False


def test_compute_syndrome_groups_escapes_form_one_ambiguous_bucket() -> None:
    groups = compute_syndrome_groups(_syndrome_result())
    escaped = next(g for g in groups if g["detected"] is False)
    assert escaped["elem"] is None and escaped["op"] is None
    assert sorted(escaped["fault_types"]) == ["CFID", "CFIN"]
    assert escaped["ambiguous"] is True


def test_compute_syndrome_groups_sorted_detected_first_then_elem_op() -> None:
    groups = compute_syndrome_groups(_syndrome_result())
    detected_flags = [g["detected"] for g in groups]
    assert detected_flags == sorted(detected_flags, reverse=True)
    detected_groups = [g for g in groups if g["detected"]]
    locations = [(g["elem"], g["op"]) for g in detected_groups]
    assert locations == sorted(locations)


def test_compute_syndrome_groups_no_ambiguity_when_every_group_unique() -> None:
    r = _result("march_c", [("SA0", 1, 0, True), ("SA1", 2, 0, False)])
    groups = compute_syndrome_groups(r)
    assert all(not g["ambiguous"] for g in groups)


def test_render_syndrome_csv_has_header_and_rows() -> None:
    csv = render_syndrome_csv(_syndrome_result())
    lines = csv.strip().splitlines()
    assert lines[0] == "detected,elem,op,fault_types,ambiguous"
    assert any("SA0|TF0" in line or "TF0|SA0" in line for line in lines[1:])


def test_render_syndrome_md_reports_ambiguous_count() -> None:
    md = render_syndrome_md(_syndrome_result())
    assert "march_c" in md
    # 2 ambiguous groups: the same-location DETECTED pair and the escaped bucket.
    assert "2 ambiguous" in md


def test_render_syndrome_json_keeps_real_lists() -> None:
    payload = json.loads(render_syndrome_json(_syndrome_result()))
    assert payload["algo_name"] == "march_c"
    groups = payload["groups"]
    same_loc = next(g for g in groups if g["elem"] == 1 and g["op"] == 0)
    assert isinstance(same_loc["fault_types"], list)
    assert sorted(same_loc["fault_types"]) == ["SA0", "TF0"]


def test_syndrome_valid_formats_matches_renderer_set() -> None:
    assert set(SYNDROME_VALID_FORMATS) == {"md", "csv", "json"}


def test_write_syndrome_report_rejects_bad_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown syndrome report format"):
        write_syndrome_report(_syndrome_result(), tmp_path / "x.txt", fmt="yaml")


def test_write_syndrome_report_writes_file(tmp_path: Path) -> None:
    path = write_syndrome_report(_syndrome_result(), tmp_path / "syn.md", fmt="md")
    assert path.exists() and "march_c" in path.read_text()


def test_write_syndrome_report_json_format(tmp_path: Path) -> None:
    path = write_syndrome_report(_syndrome_result(), tmp_path / "syn.json", fmt="json")
    payload = json.loads(path.read_text())
    assert "groups" in payload
