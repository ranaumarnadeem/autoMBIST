"""Verilator-gated integration tests for Workstream L: the words_per_row
memory parameter and Half-Select Disturb (HSD) fault modeling.

All parameters here are empirically derived against the actual fault_ram.sv
insertion site, not hand-derived -- a same-row disturb is only OBSERVABLE if
it happens after the victim's own most recent direct write in the traversal
(a later direct write always overwrites an earlier disturb), which is not
obvious from the design alone and was confirmed by a real differential run
before being written into fault_ram.sv's own comment and this test file (see
that comment for the full explanation).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import parse_alg, resolve_algo  # noqa: E402
from autombist.algo_engine import (  # noqa: E402
    CampaignError,
    FaultRecord,
    MemoryParams,
    compile_engine,
    find_engine_dir,
    generate_all_types_faults,
    parse_fault_hits,
    run_algo_campaign,
    run_one,
    write_fault_list,
    _resolve_engine_sources,
)
from autombist.fault_primitives import default_registry  # noqa: E402
from autombist.fault_ram_gen import render_and_write  # noqa: E402

# depth=4 (AW=2), DW=4, init_val=0. "up w0" writes 0 ascending 0,1,2,3.
# "up r0" reads expecting 0 (matches the golden/nominal value). Victim bit
# is p0=1 (disturbed-toward 1, the opposite of the nominal 0), so a disturb
# is directly observable against a mismatch.
_SPEC_TEXT = "up w0\nup r0\n"


def _mem(words_per_row: int) -> MemoryParams:
    return MemoryParams(addr_width=2, data_width=4, words_per_row=words_per_row, init_val=0)


def test_words_per_row_1_default_makes_hsd_provably_inert(tmp_path: Path) -> None:
    """At the default words_per_row=1, row(addr)=addr for every address, so
    'a different address in the same row' is mathematically unsatisfiable --
    HSD must escape regardless of victim/row-mate placement."""
    mem = _mem(1)
    spec = parse_alg(_SPEC_TEXT, "depth4")
    fault = FaultRecord("HSD", vaddr=0, vbit=0, aaddr=0, abit=0, p0=1, p1=0)
    result = run_algo_campaign(mem, spec, [fault], workdir=tmp_path)
    assert result.golden_clean is True
    assert result.faults[0].detected is False


def test_words_per_row_2_row_mate_written_after_victim_detects(tmp_path: Path) -> None:
    """rows are {0,1},{2,3} under words_per_row=2. victim=0's row-mate is
    addr1, which 'up w0' writes AFTER addr0's own direct write (ascending
    order) -- the disturb survives to the read pass."""
    mem = _mem(2)
    spec = parse_alg(_SPEC_TEXT, "depth4")
    fault = FaultRecord("HSD", vaddr=0, vbit=0, aaddr=0, abit=0, p0=1, p1=0)
    result = run_algo_campaign(mem, spec, [fault], workdir=tmp_path)
    r = result.faults[0]
    assert r.detected is True
    assert r.elem == 1  # the "up r0" read element
    assert r.op == 0
    assert r.addr == 0


def test_words_per_row_2_row_mate_written_before_victim_escapes(tmp_path: Path) -> None:
    """Same rows as above, but victim=3's row-mate is addr2, which 'up w0'
    writes BEFORE addr3's own direct write in ascending order -- the disturb
    is overwritten by the victim's own subsequent legitimate write before it
    is ever read. This is the single most important correctness proof of the
    write-always-wins-over-disturb precedence (fault_ram.sv's per-bit victim
    loop computes `nxt` unconditionally from `d`, with no HSD-awareness)."""
    mem = _mem(2)
    spec = parse_alg(_SPEC_TEXT, "depth4")
    fault = FaultRecord("HSD", vaddr=3, vbit=0, aaddr=0, abit=0, p0=1, p1=0)
    result = run_algo_campaign(mem, spec, [fault], workdir=tmp_path)
    assert result.faults[0].detected is False


def test_words_per_row_4_whole_array_is_one_row(tmp_path: Path) -> None:
    """words_per_row == depth degenerates 'same row' into 'same memory' --
    every other address is a row-mate. Still a valid (if degenerate)
    configuration, not rejected by _validate_words_per_row (4 <= depth=4,
    4 divides depth=4 exactly)."""
    mem = _mem(4)
    spec = parse_alg(_SPEC_TEXT, "depth4")
    fault = FaultRecord("HSD", vaddr=0, vbit=0, aaddr=0, abit=0, p0=1, p1=0)
    result = run_algo_campaign(mem, spec, [fault], workdir=tmp_path)
    assert result.faults[0].detected is True


def test_hsd_full_word_write_activates_exactly_once_not_per_bit(tmp_path: Path) -> None:
    """The HSD check is a SINGLE per-fault, per-word check deliberately not
    nested in either per-bit loop in write_op() -- a full-word write (the
    default wmask, all bits set) must activate it exactly once, not
    DATA_WIDTH times (which nesting in a per-bit loop would produce)."""
    mem = _mem(2)
    spec = parse_alg(_SPEC_TEXT, "depth4")
    fault = FaultRecord("HSD", vaddr=0, vbit=0, aaddr=0, abit=0, p0=1, p1=0)
    engine_dir = find_engine_dir()
    sources, top_module = _resolve_engine_sources(mem, engine_dir, tmp_path, None)
    artifact = compile_engine(mem, sources=sources, top_module=top_module, workdir=tmp_path)
    alg_file = spec.write_numeric(tmp_path / "spec.algc")
    fault_file = write_fault_list([fault], tmp_path / "faults.txt")
    out = run_one(
        artifact, alg_file=alg_file, fault_file=fault_file, index=0, verbose=True,
        extra_plusargs=[f"+INIT={mem.init_val}"],
    )
    hits = parse_fault_hits(out)
    assert hits == (0, "HSD", 1)


def test_hsd_cross_port_disturb_under_num_ports_2(tmp_path: Path) -> None:
    """HSD ships for num_ports=2 in this same pass (unlike DRF): the check is
    stateless and lives inside write_op(), which march_engine_mp.sv already
    calls once per port against the SAME shared mem[]/FQ[] -- 'physical row
    shared across both ports' falls out for free. A write on port 1 to the
    victim's row-mate must disturb a victim read back via port 0."""
    mem = MemoryParams(addr_width=2, data_width=4, words_per_row=2, init_val=0, num_ports=2)
    spec = parse_alg("up w0.1\nup r0.0\n", "depth4_2port")
    fault = FaultRecord("HSD", vaddr=0, vbit=0, aaddr=0, abit=0, p0=1, p1=0)
    result = run_algo_campaign(mem, spec, [fault], workdir=tmp_path)
    assert result.faults[0].detected is True


def test_hsd_templated_fault_ram_matches_hand_written_detection(tmp_path: Path) -> None:
    """fault_ram.sv (hand-written) and fault_ram_template.sv.j2 (rendered)
    carry two INDEPENDENTLY maintained copies of the HSD check, with no
    automated equivalence check between them. Re-run the row-mate-detects
    scenario against the TEMPLATED single-port render instead of the default
    hand-written file, requiring the identical attribution."""
    mem = _mem(2)
    spec = parse_alg(_SPEC_TEXT, "depth4")
    fault = FaultRecord("HSD", vaddr=0, vbit=0, aaddr=0, abit=0, p0=1, p1=0)
    fault_ram_sv = render_and_write(default_registry(), tmp_path / "fault_ram_templated.sv", num_ports=1)
    result = run_algo_campaign(mem, spec, [fault], workdir=tmp_path / "run", fault_ram_sv=fault_ram_sv)
    r = result.faults[0]
    assert r.detected is True
    assert r.elem == 1
    assert r.op == 0
    assert r.addr == 0


def test_hsd_naturally_detected_by_march_c_when_words_per_row_over_1(tmp_path: Path) -> None:
    """generate_all_types_faults' auto-placed HSD entry (no hand-tuned victim
    address) must be naturally detectable by a REAL march algorithm's own
    traversal once words_per_row > 1 -- confirming HSD does not need a
    DRF-style hand-crafted wait-containing spec to ever fire."""
    mem = MemoryParams(addr_width=4, data_width=8, words_per_row=4, init_val=1)
    spec = resolve_algo("march_c")
    faults = generate_all_types_faults(mem)
    hsd = next(f for f in faults if f.type == "HSD")
    result = run_algo_campaign(mem, spec, [hsd], workdir=tmp_path)
    assert result.golden_clean is True
    assert result.faults[0].detected is True


def test_words_per_row_validation_errors_are_reachable_through_run_algo_campaign(tmp_path: Path) -> None:
    spec = parse_alg(_SPEC_TEXT, "depth4")
    fault = FaultRecord("HSD", vaddr=0, vbit=0, aaddr=0, abit=0, p0=1, p1=0)
    bad_mem = MemoryParams(addr_width=2, data_width=4, words_per_row=3)  # depth=4, not a divisor
    with pytest.raises(CampaignError, match="does not evenly divide"):
        run_algo_campaign(bad_mem, spec, [fault], workdir=tmp_path)
