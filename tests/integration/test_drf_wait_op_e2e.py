"""Verilator-gated integration tests for Workstream K: the wait/idle `.alg`
op and Data Retention Fault (DRF) modeling.

All parameters here (p0 thresholds, victim addresses, wait counts) are
EMPIRICALLY DERIVED against the actual march_engine.sv/fault_ram.sv timing,
not hand-derived -- alg_spec.py's own module docstring warns that a wait op
inside an element repeats once per address the element visits (e.g. `either
t5` on a depth-8 memory costs 5*8=40 cycles, not 5), and march_c's own
natural traversal already leaves tens of idle cycles between two writes to
the same address even with zero wait ops. A naively "obvious" p0 threshold
picked without measurement was wrong more than once while this suite was
built (see the K-plan's problem-solving notes) -- every threshold below is
pinned to a directly-observed detected/escaped boundary at AW=3 (depth=8),
DW=8.
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
    run_algo_campaign,
)
from autombist.fault_primitives import default_registry  # noqa: E402
from autombist.fault_ram_gen import render_and_write  # noqa: E402


def _mem() -> MemoryParams:
    return MemoryParams(addr_width=3, data_width=8)  # depth=8, small enough to
    # keep the wait-op x depth multiplication (see module docstring) tractable
    # to hand-pick and verify.


def test_wait_op_advances_time_without_perturbing_golden_pass(tmp_path: Path) -> None:
    """A wait-containing spec with NO fault list must still report a clean
    golden pass -- do_wait() never touches dout/mem, so mixing t<N> in with
    ordinary ops must be a true no-op for a fault-free run."""
    mem = _mem()
    spec = parse_alg("either w0\nup r0 w1 t3\ndown r1 w0\neither r0 t10\n", "mixed_wait")
    result = run_algo_campaign(mem, spec, [], workdir=tmp_path)
    assert result.golden_clean is True
    assert result.total == 0


def test_drf_fault_escapes_march_test_with_no_wait_op(tmp_path: Path) -> None:
    """march_c contains no wait ops at all, but its own element-to-element
    traversal still leaves natural idle gaps between two writes to the same
    address (confirmed empirically: at depth=8 the worst-case gap is under
    60 cycles for every victim address). p0=100 is comfortably above that
    measured natural maximum, so the fault must escape -- proving DRF is
    NOT spuriously sensitized by ordinary march traffic."""
    mem = _mem()
    spec = resolve_algo("march_c")
    fault = FaultRecord("DRF", vaddr=0, vbit=0, aaddr=0, abit=0, p0=100, p1=0)

    result = run_algo_campaign(mem, spec, [fault], workdir=tmp_path)

    assert result.golden_clean is True
    assert result.detected == 0
    assert result.faults[0].detected is False


def test_drf_fault_detected_once_sufficient_wait_element_inserted(tmp_path: Path) -> None:
    """Same DRF fault, but against a spec with an explicit wait element
    (`either t5`, i.e. 5*8=40 idle cycles per the per-address-repetition
    rule) between the write that arms it and the read that observes it.
    p0=60 is empirically confirmed DETECTED for this exact spec/victim
    (elem=2 addr=7 -- the read element, attributing the failure to the read
    that actually observed the corrupted cell, not to the wait element
    itself, which touches no bus)."""
    mem = _mem()
    # va=7 (last address): "up w0" writes it LAST (arming/resetting DRF right
    # before the wait element runs), "up r0" reads it LAST too.
    spec = parse_alg("up w0\neither t5\nup r0\n", "custom_wait")
    fault = FaultRecord("DRF", vaddr=7, vbit=0, aaddr=0, abit=0, p0=60, p1=0)

    result = run_algo_campaign(mem, spec, [fault], workdir=tmp_path)

    r = result.faults[0]
    assert r.detected is True
    assert r.elem == 2  # the "up r0" read element
    assert r.op == 0
    assert r.addr == 7


def test_drf_reset_by_intervening_write_prevents_false_detection(tmp_path: Path) -> None:
    """The single most important correctness proof of the always_ff
    reset/re-arm logic: two identical wait windows (either t5, 40 cycles
    each at depth=8) at the SAME p0=95 threshold, differing only in whether
    a write to the victim address intervenes between them.

    - WITHOUT an intervening write (one continuous idle stretch across both
      wait elements) the combined idle time clears p0=95 -- DETECTED.
    - WITH an intervening write to the victim address between the two wait
      elements, the counter resets to 0 partway through -- neither window
      alone ever reaches p0=95 -- ESCAPED.

    This is a differential pair, not an isolated threshold pick: it proves
    the reset actually prevented a detection that would otherwise have
    happened, not just that the chosen p0 was never reached at all.
    """
    mem = _mem()
    fault = FaultRecord("DRF", vaddr=0, vbit=0, aaddr=0, abit=0, p0=95, p1=0)

    no_reset_spec = parse_alg("up w0\neither t5\neither t5\nup r0\n", "no_reset")
    no_reset_result = run_algo_campaign(mem, no_reset_spec, [fault], workdir=tmp_path / "no_reset")
    assert no_reset_result.faults[0].detected is True, (
        "control case: two back-to-back wait windows with no intervening write "
        "must clear p0=95 -- if this fails, the p0/wait parameters below no "
        "longer prove anything about the reset logic"
    )

    with_reset_spec = parse_alg("up w0\neither t5\nup w0\neither t5\nup r0\n", "with_reset")
    with_reset_result = run_algo_campaign(mem, with_reset_spec, [fault], workdir=tmp_path / "with_reset")
    assert with_reset_result.faults[0].detected is False, (
        "the intervening write to the victim address must reset drf_idle_count, "
        "keeping both windows individually under p0=95"
    )


def test_drf_templated_fault_ram_matches_hand_written_detection(tmp_path: Path) -> None:
    """fault_ram.sv (hand-written) and fault_ram_template.sv.j2 (rendered)
    carry two INDEPENDENTLY maintained copies of the DRF always_ff block
    (see fault_ram_gen.py/fault_ram_template.sv.j2 header comments) with no
    automated equivalence check between them. Re-run the exact
    detected-with-sufficient-wait scenario from
    test_drf_fault_detected_once_sufficient_wait_element_inserted, but
    against the TEMPLATED single-port render instead of the default
    hand-written file, and require the identical elem/op/addr attribution --
    catching drift between the two files before it ships."""
    mem = _mem()
    spec = parse_alg("up w0\neither t5\nup r0\n", "custom_wait")
    fault = FaultRecord("DRF", vaddr=7, vbit=0, aaddr=0, abit=0, p0=60, p1=0)
    fault_ram_sv = render_and_write(default_registry(), tmp_path / "fault_ram_templated.sv", num_ports=1)

    result = run_algo_campaign(mem, spec, [fault], workdir=tmp_path / "run", fault_ram_sv=fault_ram_sv)

    r = result.faults[0]
    assert r.detected is True
    assert r.elem == 2
    assert r.op == 0
    assert r.addr == 7


def test_drf_fault_under_num_ports_2_fails_loud_not_silent(tmp_path: Path) -> None:
    """DRF is deferred for num_ports=2 (Workstream K scope decision) behind a
    RUNTIME SystemVerilog guard in fault_ram_template.sv.j2, not a Python-level
    raise in fault_ram_gen.py (see that file's render_fault_ram docstring and
    the template's own comment for why). A campaign that actually loads a DRF
    fault under num_ports=2 must fail loud -- the simulator $finish-es with a
    FATAL message and never prints a RESULT line, which surfaces to Python as
    a CampaignError, not a silent no-op or a wrong-but-quiet result."""
    mem = MemoryParams(addr_width=3, data_width=4, num_ports=2)
    spec = resolve_algo("march_c")
    fault = FaultRecord("DRF", vaddr=1, vbit=0, aaddr=0, abit=0, p0=10, p1=0)

    with pytest.raises(CampaignError, match="DRF is not yet supported for num_ports=2"):
        run_algo_campaign(mem, spec, [fault], workdir=tmp_path)
