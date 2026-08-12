from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.algo_engine import FaultRecord, MemoryParams, run_algo_campaign  # noqa: E402
from autombist.algo_shell import AlgoShell, Session  # noqa: E402
from autombist.fault_primitives import default_registry  # noqa: E402
from autombist.synth_engine import resolve_params, synthesize_alg  # noqa: E402


def _run_script(lines: list[str]) -> tuple[AlgoShell, str]:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    for line in lines:
        if shell.onecmd(shell.precmd(line)):
            break
    return shell, shell.stdout.getvalue()


def test_synth_command_verify_reports_full_coverage() -> None:
    # Campaign total is 18, not 15: 12 single-cell targets get one
    # verification fault each, but the 3 coupling primitives (CFST/CFIN/CFID)
    # now get TWO -- aggressor-above and aggressor-below -- because a
    # synthesized spec must detect a coupling fault under both placements to
    # be a valid coverage claim (see synth_engine.py's module docstring). The
    # printed "covered: 15/15" line is unchanged -- that counts PRIMITIVES,
    # not fault records, and 15 is still the right primitive count.
    shell, out = _run_script(["set_memory 8 8", "synth mytest --verify"])
    assert "error:" not in out
    assert "covered: 15/15" in out
    result = shell.session.last_results["mytest"]
    assert (result.detected, result.total) == (18, 18)


def test_synth_command_verify_reports_full_coverage_at_init_zero() -> None:
    # Regression: at init_val=0, the mandatory init bracket's own w0 write
    # coincidentally satisfies WDF0's sensitizing condition a cycle early,
    # and CFST (gated on the aggressor's held value) needed a dedicated
    # setup that an earlier version of the synthesizer didn't have --
    # neither showed up when only the (also-supported, but not default)
    # init_val=1 path was exercised. Total is 18 for the same reason as
    # test_synth_command_verify_reports_full_coverage above.
    shell, out = _run_script(["set_memory 8 8 --init 0", "synth mytest --verify"])
    assert "error:" not in out
    assert "covered: 15/15" in out
    result = shell.session.last_results["mytest"]
    assert (result.detected, result.total) == (18, 18)
    assert shell.session.last_op == ("run", "mytest")


def test_synth_with_custom_fault_type_end_to_end() -> None:
    # 4 coupling primitives now (CFST/CFIN/CFID + the custom MYCF, also
    # write_effect/on=aggressor) x 2 records + 12 single-cell x 1 = 20.
    shell, out = _run_script([
        "set_memory 8 8",
        'add_fault_type {"name": "MYCF", "category": "write_effect", '
        '"sensitize": {"transition": "p0", "on": "aggressor"}, "effect": {"kind": "invert"}}',
        "synth withcustom --verify",
    ])
    assert "error:" not in out
    assert "targets 16/32" in out
    assert "covered: 16/16" in out
    result = shell.session.last_results["withcustom"]
    assert (result.detected, result.total) == (20, 20)


def test_synthesized_spec_detects_coupling_faults_at_both_placements() -> None:
    """The headline regression guard for the WLOG bug, by real Verilator
    campaign rather than the pure-Python oracle -- the same kind of gap this
    project has hit before (research-mode engine vs. classic RTL disagreeing
    on `either`) was only actually caught by simulating both sides, not by
    reasoning about the code.

    Before the fix (measured against this exact config): aggressor-above
    100.00% (15/15), aggressor-below 80.00% (12/15) -- CFST/CFIN/CFID, the
    three coupling primitives, the only ones that ever missed."""
    mem = MemoryParams(addr_width=6, data_width=8)
    result = synthesize_alg(default_registry(), "t", init_val=1)
    assert result.uncovered == []
    # Scope to what the synthesizer actually CLAIMED. The agg_pre coupling
    # family is excluded from targeting by design (see synthesize_alg), so
    # demanding detection for it here would assert a claim the tool never
    # made -- and would fail for a correct implementation.
    claimed = set(result.targeted)
    registry = [p for p in default_registry() if p.name in claimed]
    by_name = {p.name: p for p in registry}

    coupling_names = [p.name for p in registry if p.sensitize.on == "aggressor"]
    single_names = [p.name for p in registry if p.sensitize.on != "aggressor"]

    def build_faults(*, aggressor_above: bool) -> list[FaultRecord]:
        records = []
        for i, name in enumerate(coupling_names):
            p0, p1 = resolve_params(by_name[name])
            va, vb = i, i % mem.data_width
            if aggressor_above:
                records.append(FaultRecord(name, va, vb, va + 1, vb, p0, p1))
            else:
                records.append(FaultRecord(name, va + 1, vb, va, vb, p0, p1))
        for i, name in enumerate(single_names):
            p0, p1 = resolve_params(by_name[name])
            va, vb = (i * 7 + 3) % mem.depth, i % mem.data_width
            records.append(FaultRecord(name, va, vb, 0, 0, p0, p1))
        return records

    above = run_algo_campaign(mem, result.spec, build_faults(aggressor_above=True), sim="verilator")
    below = run_algo_campaign(mem, result.spec, build_faults(aggressor_above=False), sim="verilator")

    above_missed = sorted(f.record.type for f in above.faults if not f.detected)
    below_missed = sorted(f.record.type for f in below.faults if not f.detected)
    assert above_missed == [], f"aggressor-above escapes: {above_missed}"
    assert below_missed == [], f"aggressor-below escapes: {below_missed}"
    assert (above.detected, above.total) == (15, 15)
    assert (below.detected, below.total) == (15, 15)


def test_synth_compare_against_march_ss() -> None:
    shell, out = _run_script([
        "set_memory 8 8",
        "synth mytest",
        "compare_algo mytest -march march_c,march_ss",
    ])
    assert "error:" not in out
    assert "| fault" in out
    names = [r.algo_name for r in shell.session.last_matrix]
    assert names == ["mytest", "march_c", "march_ss"]
    march_ss_len = shell.session.algos["march_ss"].length_n
    synth_len = shell.session.algos["mytest"].length_n
    assert march_ss_len == 22  # sanity: matches engine/README.md's documented length
    # Not claiming "shorter" as a general property -- March SS targets this
    # exact fault set near-optimally already (per the original planning
    # doc). Just confirm the synthesized test is a real, comparably-scoped
    # result, not a degenerate one.
    assert 0 < synth_len <= 40


def test_synth_write_flag_produces_parseable_alg_file(tmp_path: Path) -> None:
    out_path = tmp_path / "synthed.alg"
    shell, out = _run_script([
        "set_memory 8 8",
        f"synth mytest --write {out_path}",
        f"add_algo {out_path} --name reloaded",
    ])
    assert "error:" not in out
    assert out_path.exists()
    assert shell.session.algos["reloaded"].elements == shell.session.algos["mytest"].elements


def test_synth_excludes_fixed_types_in_printed_summary() -> None:
    shell, out = _run_script(["set_memory 8 8", "synth mytest"])
    assert "excludes SOF, AF_NOACC, AF_ALIAS, CFDS, DRF, HSD" in out
    assert "targets 15/31" in out


def test_synth_registered_algo_immediately_usable_by_run() -> None:
    """Confirms the plan's core integration claim end-to-end: synth's output
    needs zero special-casing anywhere else in the shell -- 'run' treats a
    synthesized algo exactly like any add_algo-loaded one."""
    faults_shell, _ = _run_script(["set_memory 8 8", "gen_faults --all-types"])
    shell, out = _run_script([
        "set_memory 8 8",
        "gen_faults --all-types",
        "synth mytest",
        "run mytest",
    ])
    assert "error:" not in out
    result = shell.session.last_results["mytest"]
    assert result.total == len(faults_shell.session.faults)
