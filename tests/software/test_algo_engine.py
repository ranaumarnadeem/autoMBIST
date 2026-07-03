from __future__ import annotations

from pathlib import Path

import pytest

from autombist.algo_engine import (
    CampaignError,
    FaultRecord,
    parse_fault_hits,
    parse_fault_list,
    parse_fault_loaded,
    parse_result_line,
    write_fault_list,
)


def test_fault_record_to_line() -> None:
    rec = FaultRecord(type="SA0", vaddr=10, vbit=3, aaddr=0, abit=0, p0=0, p1=0)
    assert rec.to_line() == "SA0 10 3 0 0 0 0"


def test_parse_fault_list_skips_comments_and_blanks() -> None:
    text = "# header\n\nSA0 10 3 0 0 0 0\nAF_ALIAS 90 0 91 0 0 0\n"
    records = parse_fault_list(text)
    assert len(records) == 2
    assert records[0] == FaultRecord("SA0", 10, 3, 0, 0, 0, 0)
    assert records[1] == FaultRecord("AF_ALIAS", 90, 0, 91, 0, 0, 0)


def test_parse_fault_list_rejects_malformed_line() -> None:
    with pytest.raises(CampaignError):
        parse_fault_list("SA0 10 3\n")  # missing fields


def test_write_fault_list_roundtrip(tmp_path: Path) -> None:
    records = [FaultRecord("SA1", 17, 0, 0, 0, 0, 0), FaultRecord("CFIN", 100, 2, 101, 2, 2, 0)]
    path = write_fault_list(records, tmp_path / "faults.txt")
    assert parse_fault_list(path.read_text()) == records


def test_parse_result_line_detected() -> None:
    detected, elem, op, addr, xor_bits = parse_result_line(
        "some noise\nRESULT DETECTED alg=MARCHCM elem=1 op=0 addr=50 xor=00000100\n"
    )
    assert (detected, elem, op, addr, xor_bits) == (True, 1, 0, 50, "00000100")


def test_parse_result_line_escaped() -> None:
    detected, elem, op, addr, xor_bits = parse_result_line("RESULT ESCAPED alg=MARCHCM\n")
    assert (detected, elem, op, addr, xor_bits) == (False, None, None, None, None)


def test_parse_result_line_missing_raises() -> None:
    with pytest.raises(CampaignError):
        parse_result_line("nothing here\n")


def test_parse_fault_loaded() -> None:
    parsed = parse_fault_loaded(
        "FAULT_LOADED idx=6 type=RDF0 v=50.2 a=0.0 p0=0 p1=0\n"
    )
    assert parsed == (6, "RDF0", 50, 2, 0, 0, 0, 0)
    assert parse_fault_loaded("no match") is None


def test_parse_fault_hits() -> None:
    assert parse_fault_hits("FAULT_HITS idx=0 type=SA0 activations=15\n") == (0, "SA0", 15)
    assert parse_fault_hits("no match") is None


# --------------------------------------------------------------------------- #
# Multi-port generalization (Phase: additive vport/aport + num_ports fields)
# --------------------------------------------------------------------------- #
def test_fault_record_vport_aport_default_to_zero() -> None:
    rec = FaultRecord(type="SA0", vaddr=10, vbit=3)
    assert rec.vport == 0
    assert rec.aport == 0


def test_fault_record_to_line_unaffected_when_ports_default() -> None:
    # Byte-identical to the pre-multi-port on-disk format.
    rec = FaultRecord(type="SA0", vaddr=10, vbit=3, aaddr=0, abit=0, p0=0, p1=0)
    assert rec.to_line() == "SA0 10 3 0 0 0 0"


def test_fault_record_to_line_emits_extra_fields_when_ports_nonzero() -> None:
    rec = FaultRecord(type="CFIN", vaddr=10, vbit=3, aaddr=11, abit=3, p0=2, p1=0, vport=0, aport=1)
    assert rec.to_line() == "CFIN 10 3 11 3 2 0 0 1"


def test_parse_fault_list_old_7field_format_unaffected() -> None:
    text = "# header\n\nSA0 10 3 0 0 0 0\nAF_ALIAS 90 0 91 0 0 0\n"
    records = parse_fault_list(text)
    assert len(records) == 2
    assert records[0] == FaultRecord("SA0", 10, 3, 0, 0, 0, 0)
    assert records[0].vport == 0 and records[0].aport == 0
    assert records[1] == FaultRecord("AF_ALIAS", 90, 0, 91, 0, 0, 0)


def test_parse_fault_list_8field_format_sets_vport_only() -> None:
    records = parse_fault_list("CFIN 10 3 11 3 2 0 1\n")
    assert records == [FaultRecord("CFIN", 10, 3, 11, 3, 2, 0, vport=1, aport=0)]


def test_parse_fault_list_9field_format_sets_both_ports() -> None:
    records = parse_fault_list("CFIN 10 3 11 3 2 0 0 1\n")
    assert records == [FaultRecord("CFIN", 10, 3, 11, 3, 2, 0, vport=0, aport=1)]


def test_parse_fault_list_rejects_wrong_field_counts() -> None:
    with pytest.raises(CampaignError):
        parse_fault_list("SA0 10 3\n")  # too few (3)
    with pytest.raises(CampaignError):
        parse_fault_list("SA0 10 3 0 0 0\n")  # too few (6)
    with pytest.raises(CampaignError):
        parse_fault_list("SA0 10 3 0 0 0 0 0 0 0\n")  # too many (10)


def test_parse_fault_list_rejects_malformed_type_token() -> None:
    # The pre-multi-port regex enforced [A-Za-z0-9_]+ on the type field; the
    # field-count-dispatch rewrite must keep rejecting anything outside that.
    with pytest.raises(CampaignError):
        parse_fault_list("SA-0 10 3 0 0 0 0\n")
    with pytest.raises(CampaignError):
        parse_fault_list("SA0! 10 3 0 0 0 0\n")


def test_parse_fault_list_rejects_non_numeric_field() -> None:
    with pytest.raises(CampaignError):
        parse_fault_list("SA0 10 x 0 0 0 0\n")


def test_write_fault_list_roundtrip_byte_identical_when_num_ports_1(tmp_path: Path) -> None:
    # Strongest backward-compat proof: default (0,0) ports produce the exact
    # same on-disk text as before this phase.
    records = [FaultRecord("SA1", 17, 0, 0, 0, 0, 0), FaultRecord("CFIN", 100, 2, 101, 2, 2, 0)]
    path = write_fault_list(records, tmp_path / "faults.txt")
    assert path.read_text() == "SA1 17 0 0 0 0 0\nCFIN 100 2 101 2 2 0\n"
    assert parse_fault_list(path.read_text()) == records


def test_write_fault_list_emits_extra_fields_when_ports_nonzero(tmp_path: Path) -> None:
    records = [FaultRecord("CFIN", 100, 2, 101, 2, 2, 0, vport=0, aport=1)]
    path = write_fault_list(records, tmp_path / "faults.txt")
    assert path.read_text() == "CFIN 100 2 101 2 2 0 0 1\n"
    assert parse_fault_list(path.read_text()) == records


def test_memory_params_num_ports_defaults_to_1() -> None:
    from autombist.algo_engine import MemoryParams

    mem = MemoryParams(addr_width=8, data_width=8)
    assert mem.num_ports == 1


def test_memory_params_num_ports_explicit() -> None:
    from autombist.algo_engine import MemoryParams

    mem = MemoryParams(addr_width=8, data_width=8, num_ports=2)
    assert mem.num_ports == 2
