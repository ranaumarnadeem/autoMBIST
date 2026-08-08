from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from autombist.alg_spec import parse_alg
from autombist.algo_engine import (
    CampaignError,
    FaultRecord,
    FaultResult,
    MemoryParams,
    parse_fault_hits,
    parse_fault_list,
    parse_fault_loaded,
    parse_result_line,
    run_fsm_campaign,
    write_fault_list,
    _engine_cache_enabled,
    _fault_concurrency,
    _run_faults_concurrently,
    _source_digest,
    _validate_fault_addresses,
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


def test_validate_fault_addresses_accepts_in_range_records() -> None:
    mem = MemoryParams(addr_width=4, data_width=8)  # depth=16
    _validate_fault_addresses(mem, [
        FaultRecord("SA0", vaddr=0, vbit=0, aaddr=15, abit=7, p0=0, p1=0),
        FaultRecord("SA1", vaddr=15, vbit=7, aaddr=0, abit=0, p0=0, p1=0),
    ])  # must not raise


@pytest.mark.parametrize(
    "record",
    [
        FaultRecord("SA0", vaddr=16, vbit=0, aaddr=0, abit=0, p0=0, p1=0),   # vaddr == depth
        FaultRecord("SA0", vaddr=999, vbit=0, aaddr=0, abit=0, p0=0, p1=0),  # vaddr >> depth
        FaultRecord("SA0", vaddr=0, vbit=8, aaddr=0, abit=0, p0=0, p1=0),    # vbit == data_width
        FaultRecord("SA0", vaddr=0, vbit=0, aaddr=16, abit=0, p0=0, p1=0),   # aaddr out of range
        FaultRecord("SA0", vaddr=0, vbit=0, aaddr=0, abit=8, p0=0, p1=0),    # abit out of range
        FaultRecord("SA0", vaddr=-1, vbit=0, aaddr=0, abit=0, p0=0, p1=0),   # negative
    ],
)
def test_validate_fault_addresses_rejects_out_of_range_fields(record: FaultRecord) -> None:
    """A hand-authored --faults file bypasses generate_all_types_faults/
    generate_random_faults (which construct in-range addresses by their own
    modulo arithmetic) -- an out-of-range value here used to reach the
    generated testbench unchecked and silently wrap (Verilog truncation onto
    an ADDR_WIDTH/DATA_WIDTH-bit register) instead of failing loudly."""
    mem = MemoryParams(addr_width=4, data_width=8)  # depth=16
    with pytest.raises(CampaignError, match="out of range"):
        _validate_fault_addresses(mem, [record])


def test_validate_fault_addresses_rejects_out_of_range_port() -> None:
    mem = MemoryParams(addr_width=4, data_width=8, num_ports=1)
    with pytest.raises(CampaignError, match="aport=1 is out of range"):
        _validate_fault_addresses(
            mem, [FaultRecord("CFIN", vaddr=0, vbit=0, aaddr=1, abit=0, p0=0, p1=0, aport=1)]
        )


def test_engine_cache_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOMBIST_ENGINE_CACHE", raising=False)
    assert _engine_cache_enabled() is True


@pytest.mark.parametrize("value", ["0", "off", "OFF", "false", "False", "no"])
def test_engine_cache_disabled_via_env_var(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("AUTOMBIST_ENGINE_CACHE", value)
    assert _engine_cache_enabled() is False


@pytest.mark.parametrize("value", ["1", "on", "/some/path", ""])
def test_engine_cache_enabled_for_non_disable_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    # Anything that isn't a recognized disable token (including an empty
    # string, e.g. AUTOMBIST_ENGINE_CACHE="" left over from an unset export)
    # leaves caching on -- only an explicit "0"/"off"/"false"/"no" opts out.
    monkeypatch.setenv("AUTOMBIST_ENGINE_CACHE", value)
    assert _engine_cache_enabled() is True


def test_source_digest_is_sensitive_to_content(tmp_path: Path) -> None:
    a = tmp_path / "a.sv"
    b = tmp_path / "b.sv"
    a.write_text("module a; endmodule\n", encoding="utf-8")
    b.write_text("module b; endmodule\n", encoding="utf-8")
    assert _source_digest([a]) != _source_digest([b])


def test_source_digest_is_stable_for_identical_name_and_content(tmp_path: Path) -> None:
    """Only the file's name (not its full path/directory) and byte content
    feed the digest, so two files that are otherwise indistinguishable to
    verilator's --top-module/source-list handling -- same name, same bytes,
    different location on disk -- must produce the same digest."""
    a = tmp_path / "same_name.sv"
    a.write_text("module m; endmodule\n", encoding="utf-8")
    renamed = tmp_path / "elsewhere" / "same_name.sv"
    renamed.parent.mkdir()
    renamed.write_text("module m; endmodule\n", encoding="utf-8")
    assert _source_digest([a]) == _source_digest([renamed])


def test_source_digest_is_sensitive_to_filename_even_with_identical_content(tmp_path: Path) -> None:
    """The digest folds in each file's name, not just its bytes, deliberately
    -- march_engine.sv and march_engine_mp.sv (or a caller-supplied
    fault_ram_sv override with a different name) must never collide in the
    cache purely because their content happened to match; what a source is
    CALLED is part of what verilator actually compiles (affects
    --top-module resolution and is a real input, not incidental metadata)."""
    a = tmp_path / "a.sv"
    b = tmp_path / "b.sv"
    a.write_text("module m; endmodule\n", encoding="utf-8")
    b.write_text("module m; endmodule\n", encoding="utf-8")
    assert _source_digest([a]) != _source_digest([b])


def test_source_digest_is_sensitive_to_order(tmp_path: Path) -> None:
    a = tmp_path / "a.sv"
    b = tmp_path / "b.sv"
    a.write_text("module a; endmodule\n", encoding="utf-8")
    b.write_text("module b; endmodule\n", encoding="utf-8")
    assert _source_digest([a, b]) != _source_digest([b, a])


def _fake_fault_record(i: int) -> FaultRecord:
    return FaultRecord("SA0", vaddr=i, vbit=0, aaddr=0, abit=0, p0=0, p1=0)


def test_fault_concurrency_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOMBIST_FAULT_CONCURRENCY", raising=False)
    assert _fault_concurrency() == 4


@pytest.mark.parametrize("value,expected", [("1", 1), ("8", 8), ("0", 1), ("-3", 1)])
def test_fault_concurrency_env_var_override(monkeypatch: pytest.MonkeyPatch, value: str, expected: int) -> None:
    # 0/negative are clamped up to 1 (still fully sequential, never zero/negative workers).
    monkeypatch.setenv("AUTOMBIST_FAULT_CONCURRENCY", value)
    assert _fault_concurrency() == expected


def test_fault_concurrency_env_var_ignores_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMBIST_FAULT_CONCURRENCY", "not-a-number")
    assert _fault_concurrency() == 4


def test_run_faults_concurrently_preserves_input_order_despite_reversed_completion(
) -> None:
    """The decisive ordering proof: faults are deliberately made to COMPLETE
    in the opposite order from how they were submitted (fault 0 sleeps
    longest, fault N-1 finishes first) -- the returned list must still be in
    original fault-list order, not completion order."""
    n = 8

    def run_one_fault(i: int, record: FaultRecord) -> FaultResult:
        time.sleep((n - i) * 0.01)  # earlier index -> longer sleep -> finishes LAST
        return FaultResult(index=i, record=record, detected=(i % 2 == 0))

    faults = [_fake_fault_record(i) for i in range(n)]
    results = _run_faults_concurrently(faults, run_one_fault, None, max_workers=4)

    assert [r.index for r in results] == list(range(n))
    assert [r.record for r in results] == faults
    assert [r.detected for r in results] == [i % 2 == 0 for i in range(n)]


def test_run_faults_concurrently_actually_overlaps_above_max_workers_1() -> None:
    """Proves real concurrency happens, not just a claim: tracks the peak
    number of simultaneously in-flight calls and requires it to exceed 1
    (impossible under the old strictly-sequential loop) while never
    exceeding the requested bound."""
    n = 12
    max_workers = 3
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}

    def run_one_fault(i: int, record: FaultRecord) -> FaultResult:
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        try:
            time.sleep(0.03)
            return FaultResult(index=i, record=record, detected=True)
        finally:
            with lock:
                state["current"] -= 1

    faults = [_fake_fault_record(i) for i in range(n)]
    results = _run_faults_concurrently(faults, run_one_fault, None, max_workers=max_workers)

    assert [r.index for r in results] == list(range(n))
    assert 1 < state["peak"] <= max_workers


def test_run_faults_concurrently_max_workers_1_is_strictly_sequential() -> None:
    """max_workers=1 must behave exactly like the original loop: never more
    than one call in flight at a time, byte-identical to before threading
    existed."""
    n = 6
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}

    def run_one_fault(i: int, record: FaultRecord) -> FaultResult:
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        try:
            time.sleep(0.01)
            return FaultResult(index=i, record=record, detected=True)
        finally:
            with lock:
                state["current"] -= 1

    faults = [_fake_fault_record(i) for i in range(n)]
    _run_faults_concurrently(faults, run_one_fault, None, max_workers=1)
    assert state["peak"] == 1


def test_run_faults_concurrently_progress_callback_never_regresses() -> None:
    """format_simulation_summary-style progress bars use an ABSOLUTE
    completed-count, not a delta -- if reordered completion ever leaked the
    fault INDEX into the callback instead of a monotonic counter, the
    reported "progress" could visibly jump backward."""
    n = 10
    seen: list[int] = []
    lock = threading.Lock()

    def run_one_fault(i: int, record: FaultRecord) -> FaultResult:
        time.sleep((n - i) * 0.005)  # reversed completion order again
        return FaultResult(index=i, record=record, detected=True)

    def progress_callback(completed: int, total: int) -> None:
        with lock:
            seen.append(completed)
        assert total == n

    faults = [_fake_fault_record(i) for i in range(n)]
    _run_faults_concurrently(faults, run_one_fault, progress_callback, max_workers=4)

    assert seen == sorted(seen), f"progress regressed: {seen}"
    assert seen == list(range(1, n + 1))


def test_run_faults_concurrently_propagates_exception() -> None:
    def run_one_fault(i: int, record: FaultRecord) -> FaultResult:
        if i == 2:
            raise CampaignError("simulated failure for fault 2")
        return FaultResult(index=i, record=record, detected=True)

    faults = [_fake_fault_record(i) for i in range(5)]
    with pytest.raises(CampaignError, match="simulated failure for fault 2"):
        _run_faults_concurrently(faults, run_one_fault, None, max_workers=3)


def test_run_faults_concurrently_empty_fault_list() -> None:
    assert _run_faults_concurrently([], lambda i, r: FaultResult(index=i, record=r, detected=False), None, 4) == []


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
    assert len(records) == 20  # 19 BUILTIN_FAULT_TYPES + DRF (single-port)


def test_generate_all_types_faults_includes_hsd_when_words_per_row_over_1() -> None:
    from autombist.algo_engine import generate_all_types_faults

    mem = MemoryParams(addr_width=4, data_width=8, words_per_row=4)
    records = generate_all_types_faults(mem)
    hsd = [r for r in records if r.type == "HSD"]
    assert len(hsd) == 1
    assert len(records) == 21  # 19 BUILTIN_FAULT_TYPES + DRF + HSD
    assert hsd[0].aaddr == 0 and hsd[0].abit == 0  # no fixed aggressor address


def test_generate_all_types_faults_includes_drf_for_single_port() -> None:
    from autombist.algo_engine import generate_all_types_faults

    mem = MemoryParams(addr_width=4, data_width=8, num_ports=1)
    records = generate_all_types_faults(mem)
    drf = [r for r in records if r.type == "DRF"]
    assert len(drf) == 1
    assert drf[0].aaddr == 0 and drf[0].abit == 0  # unused, matches SOF/AF_NOACC
    assert drf[0].p0 > 0  # idle-cycle threshold, not a polarity selector


def test_generate_all_types_faults_excludes_drf_for_multi_port() -> None:
    """DRF's idle-cycle tracking is single-port only -- a fault list that
    actually LOADS a DRF entry against a num_ports=2 fault_ram.sv fails loud
    (FATAL + $finish, see fault_ram_template.sv.j2's header). Unconditionally
    including DRF here would crash `gen_faults --all-types` for every
    multi-port memory, which is exactly why it's gated on mem.num_ports == 1
    the same way HSD is gated on mem.words_per_row > 1."""
    from autombist.algo_engine import generate_all_types_faults

    mem = MemoryParams(addr_width=4, data_width=8, num_ports=2)
    records = generate_all_types_faults(mem)
    assert not any(r.type == "DRF" for r in records)


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
