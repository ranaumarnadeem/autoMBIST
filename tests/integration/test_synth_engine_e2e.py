from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.algo_shell import AlgoShell, Session  # noqa: E402


def _run_script(lines: list[str]) -> tuple[AlgoShell, str]:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    for line in lines:
        if shell.onecmd(shell.precmd(line)):
            break
    return shell, shell.stdout.getvalue()


def test_synth_command_verify_reports_full_coverage() -> None:
    shell, out = _run_script(["set_memory 8 8", "synth mytest --verify"])
    assert "error:" not in out
    assert "covered: 15/15" in out
    result = shell.session.last_results["mytest"]
    assert (result.detected, result.total) == (15, 15)


def test_synth_command_verify_reports_full_coverage_at_init_zero() -> None:
    # Regression: at init_val=0, the mandatory init bracket's own w0 write
    # coincidentally satisfies WDF0's sensitizing condition a cycle early,
    # and CFST (gated on the aggressor's held value) needed a dedicated
    # setup that an earlier version of the synthesizer didn't have --
    # neither showed up when only the (also-supported, but not default)
    # init_val=1 path was exercised.
    shell, out = _run_script(["set_memory 8 8 --init 0", "synth mytest --verify"])
    assert "error:" not in out
    assert "covered: 15/15" in out
    result = shell.session.last_results["mytest"]
    assert (result.detected, result.total) == (15, 15)
    assert shell.session.last_op == ("run", "mytest")


def test_synth_with_custom_fault_type_end_to_end() -> None:
    shell, out = _run_script([
        "set_memory 8 8",
        'add_fault_type {"name": "MYCF", "category": "write_effect", '
        '"sensitize": {"transition": "p0", "on": "aggressor"}, "effect": {"kind": "invert"}}',
        "synth withcustom --verify",
    ])
    assert "error:" not in out
    assert "targets 16/22" in out
    assert "covered: 16/16" in out
    result = shell.session.last_results["withcustom"]
    assert (result.detected, result.total) == (16, 16)


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
    assert "targets 15/21" in out


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
