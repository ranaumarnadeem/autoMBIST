from __future__ import annotations

from pathlib import Path

import pytest

from autombist.alg_spec import parse_alg
from autombist.algo_engine import (
    CampaignError,
    FaultRecord,
    MemoryParams,
    parse_fault_hits,
    parse_fault_list,
    parse_fault_loaded,
    parse_result_line,
    run_fsm_campaign,
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
        parse_fault_list("SA0 10 3 0 0 0 0 0 0 0 0\n")  # too many (11); 10 is now valid (weight)


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


def test_fault_record_weight_defaults_to_none() -> None:
    rec = FaultRecord(type="SA0", vaddr=10, vbit=3)
    assert rec.weight is None


def test_fault_record_to_line_unaffected_when_weight_default() -> None:
    rec = FaultRecord(type="SA0", vaddr=10, vbit=3, aaddr=0, abit=0, p0=0, p1=0)
    assert rec.to_line() == "SA0 10 3 0 0 0 0"


def test_fault_record_to_line_emits_weight_field_when_set() -> None:
    rec = FaultRecord(type="SA0", vaddr=10, vbit=3, weight=0.5)
    assert rec.to_line() == "SA0 10 3 0 0 0 0 0 0 0.5"


def test_fault_record_to_line_emits_ports_even_at_zero_when_weight_set() -> None:
    # weight forces vport/aport to appear (even at their default 0) so weight
    # stays unambiguously the 10th positional field -- never confusable with
    # an 8/9-field vport/aport-only line.
    rec = FaultRecord(type="CFIN", vaddr=10, vbit=3, aaddr=11, abit=3, p0=2, p1=0, weight=1.0)
    assert rec.to_line() == "CFIN 10 3 11 3 2 0 0 0 1.0"


def test_parse_fault_list_old_7_8_9field_formats_unaffected_by_weight() -> None:
    assert parse_fault_list("SA0 10 3 0 0 0 0\n")[0].weight is None
    assert parse_fault_list("CFIN 10 3 11 3 2 0 1\n")[0].weight is None
    assert parse_fault_list("CFIN 10 3 11 3 2 0 0 1\n")[0].weight is None


def test_parse_fault_list_10field_format_sets_weight() -> None:
    records = parse_fault_list("SA0 10 3 0 0 0 0 0 0 0.25\n")
    assert records == [FaultRecord("SA0", 10, 3, 0, 0, 0, 0, vport=0, aport=0, weight=0.25)]


def test_parse_fault_list_rejects_non_float_weight_token() -> None:
    with pytest.raises(CampaignError):
        parse_fault_list("SA0 10 3 0 0 0 0 0 0 notafloat\n")


@pytest.mark.parametrize("token", ["inf", "-inf", "nan", "Infinity", "-Infinity", "NaN"])
def test_parse_fault_list_rejects_non_finite_weight_token(token: str) -> None:
    # float() accepts these, but a non-finite weight can't round-trip through
    # equality (nan != nan) and serializes as invalid JSON (bare NaN/Infinity,
    # not valid per RFC 8259) -- reject at the parse boundary instead.
    with pytest.raises(CampaignError, match="finite"):
        parse_fault_list(f"SA0 10 3 0 0 0 0 0 0 {token}\n")


def test_write_fault_list_roundtrip_byte_identical_when_weight_unset(tmp_path: Path) -> None:
    records = [FaultRecord("SA1", 17, 0, 0, 0, 0, 0), FaultRecord("CFIN", 100, 2, 101, 2, 2, 0)]
    path = write_fault_list(records, tmp_path / "faults.txt")
    assert path.read_text() == "SA1 17 0 0 0 0 0\nCFIN 100 2 101 2 2 0\n"
    assert parse_fault_list(path.read_text()) == records


def test_write_fault_list_roundtrip_with_weight(tmp_path: Path) -> None:
    records = [FaultRecord("SA0", 10, 3, 0, 0, 0, 0, weight=0.75)]
    path = write_fault_list(records, tmp_path / "faults.txt")
    assert path.read_text() == "SA0 10 3 0 0 0 0 0 0 0.75\n"
    assert parse_fault_list(path.read_text()) == records


def test_memory_params_num_ports_defaults_to_1() -> None:
    from autombist.algo_engine import MemoryParams

    mem = MemoryParams(addr_width=8, data_width=8)
    assert mem.num_ports == 1


def test_memory_params_num_ports_explicit() -> None:
    from autombist.algo_engine import MemoryParams

    mem = MemoryParams(addr_width=8, data_width=8, num_ports=2)
    assert mem.num_ports == 2


def test_run_fsm_campaign_rejects_wait_op_in_expected_spec(tmp_path: Path) -> None:
    # This check runs before any Verilator/RTL work (right after workdir setup),
    # so it's reachable as a pure software test -- no EDA tools needed.
    mem = MemoryParams(addr_width=4, data_width=4)
    expected_spec = parse_alg("either t5\n", "waity")
    with pytest.raises(CampaignError, match="wait op"):
        run_fsm_campaign(
            mem, [Path("dummy_fsm.sv")], "dummy_fsm", [],
            workdir=tmp_path, expected_spec=expected_spec,
        )


# --------------------------------------------------------------------------- #
# words_per_row / HSD (Workstream L)
# --------------------------------------------------------------------------- #
def test_memory_params_words_per_row_defaults_to_1() -> None:
    mem = MemoryParams(addr_width=8, data_width=8)
    assert mem.words_per_row == 1


def test_run_fsm_campaign_rejects_non_default_words_per_row(tmp_path: Path) -> None:
    # Same reachable-as-pure-software-test placement as the wait-op guard above:
    # this fires before any Verilator/RTL work, right after the wait-op check.
    mem = MemoryParams(addr_width=4, data_width=4, words_per_row=2)
    with pytest.raises(CampaignError, match="words_per_row"):
        run_fsm_campaign(mem, [Path("dummy_fsm.sv")], "dummy_fsm", [], workdir=tmp_path)


def test_run_fsm_campaign_allows_default_words_per_row(tmp_path: Path) -> None:
    # Negative control for the guard above: words_per_row=1 (default) must NOT
    # be rejected by this guard (it'll fail later for other reasons -- no real
    # FSM source exists -- but not with a words_per_row-related CampaignError).
    mem = MemoryParams(addr_width=4, data_width=4)
    with pytest.raises(Exception) as exc_info:
        run_fsm_campaign(mem, [Path("dummy_fsm.sv")], "dummy_fsm", [], workdir=tmp_path)
    assert "words_per_row" not in str(exc_info.value)


def test_validate_words_per_row_rejects_zero() -> None:
    from autombist.algo_engine import _validate_words_per_row

    mem = MemoryParams(addr_width=2, data_width=4, words_per_row=0)
    with pytest.raises(CampaignError, match="words_per_row must be >= 1"):
        _validate_words_per_row(mem)


def test_validate_words_per_row_rejects_negative() -> None:
    from autombist.algo_engine import _validate_words_per_row

    mem = MemoryParams(addr_width=2, data_width=4, words_per_row=-1)
    with pytest.raises(CampaignError, match="words_per_row must be >= 1"):
        _validate_words_per_row(mem)


def test_validate_words_per_row_rejects_exceeding_depth() -> None:
    from autombist.algo_engine import _validate_words_per_row

    mem = MemoryParams(addr_width=2, data_width=4, words_per_row=8)  # depth=4
    with pytest.raises(CampaignError, match="exceeds depth"):
        _validate_words_per_row(mem)


def test_validate_words_per_row_rejects_non_divisor_of_depth() -> None:
    from autombist.algo_engine import _validate_words_per_row

    mem = MemoryParams(addr_width=2, data_width=4, words_per_row=3)  # depth=4, 4%3 != 0
    with pytest.raises(CampaignError, match="does not evenly divide"):
        _validate_words_per_row(mem)


def test_validate_words_per_row_accepts_exact_divisor() -> None:
    from autombist.algo_engine import _validate_words_per_row

    mem = MemoryParams(addr_width=2, data_width=4, words_per_row=4)  # depth=4, exact
    _validate_words_per_row(mem)  # must not raise


def test_validate_words_per_row_accepts_default() -> None:
    from autombist.algo_engine import _validate_words_per_row

    mem = MemoryParams(addr_width=8, data_width=8)
    _validate_words_per_row(mem)  # must not raise


def test_generate_all_types_faults_excludes_hsd_at_default_words_per_row() -> None:
    from autombist.algo_engine import generate_all_types_faults

    mem = MemoryParams(addr_width=4, data_width=8)
    records = generate_all_types_faults(mem)
    assert not any(r.type == "HSD" for r in records)
    assert len(records) == 19


def test_generate_all_types_faults_includes_hsd_when_words_per_row_over_1() -> None:
    from autombist.algo_engine import generate_all_types_faults

    mem = MemoryParams(addr_width=4, data_width=8, words_per_row=4)
    records = generate_all_types_faults(mem)
    hsd = [r for r in records if r.type == "HSD"]
    assert len(hsd) == 1
    assert len(records) == 20
    assert hsd[0].aaddr == 0 and hsd[0].abit == 0  # no fixed aggressor address


def test_generate_all_types_faults_hsd_p0_opposite_of_init_val() -> None:
    from autombist.algo_engine import generate_all_types_faults

    mem1 = MemoryParams(addr_width=4, data_width=8, words_per_row=4, init_val=1)
    mem0 = MemoryParams(addr_width=4, data_width=8, words_per_row=4, init_val=0)
    hsd1 = next(r for r in generate_all_types_faults(mem1) if r.type == "HSD")
    hsd0 = next(r for r in generate_all_types_faults(mem0) if r.type == "HSD")
    assert hsd1.p0 == 0   # init=1 -> disturbed-toward 0 (a real, observable disturb)
    assert hsd0.p0 == 1   # init=0 -> disturbed-toward 1


def test_generate_random_faults_never_picks_hsd_at_default_words_per_row() -> None:
    from autombist.algo_engine import generate_random_faults

    mem = MemoryParams(addr_width=4, data_width=8)
    records = generate_random_faults(mem, 500, seed=1)
    assert not any(r.type == "HSD" for r in records)


def test_generate_random_faults_can_pick_hsd_when_words_per_row_over_1() -> None:
    from autombist.algo_engine import generate_random_faults

    mem = MemoryParams(addr_width=4, data_width=8, words_per_row=4)
    records = generate_random_faults(mem, 500, seed=1)
    assert any(r.type == "HSD" for r in records)
