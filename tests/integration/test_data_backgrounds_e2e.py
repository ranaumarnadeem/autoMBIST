from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import AlgSpec, builtin_algos, find_engine_dir, load_alg_file  # noqa: E402
from autombist.algo_engine import (  # noqa: E402
    FaultRecord,
    MemoryParams,
    load_fault_list,
    merge_background_results,
    run_algo_campaign,
    run_background_campaign,
    standard_backgrounds,
)
from autombist.algo_shell import AlgoShell, Session  # noqa: E402


def _run_script(lines: list[str]) -> tuple[AlgoShell, str]:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    for line in lines:
        if shell.onecmd(shell.precmd(line)):
            break
    return shell, shell.stdout.getvalue()


def _march_c() -> AlgSpec:
    path = builtin_algos()["march_c"]
    return load_alg_file(path, name="march_c")


def test_golden_soundness_across_standard_backgrounds(tmp_path: Path) -> None:
    """A background-masked golden (fault-free) run must never spuriously
    report DETECTED -- bg_value() applies the identical mask to both the
    write side and the read-assertion side in march_engine.sv, so this is a
    hard regression gate before anything else about the background loop can
    be trusted. run_background_campaign itself raises CampaignError if any
    background's golden pass reports DETECTED, so completing without raising
    (and with results for every standard background) IS the proof."""
    mem = MemoryParams(addr_width=8, data_width=8)
    spec = _march_c()
    faults = load_fault_list(find_engine_dir() / "faults.example.txt")
    results = run_background_campaign(mem, spec, faults, workdir=tmp_path / "golden")
    expected_names = [b.name for b in standard_backgrounds(8)]
    assert list(results.keys()) == expected_names
    for name, result in results.items():
        assert result.golden_clean, name


def test_intra_word_coupling_escapes_solid_but_detected_with_backgrounds(tmp_path: Path) -> None:
    """The concrete masking case march_engine.sv's own header comment warns
    about: an intra-word CFID (vaddr==aaddr, different bit lanes) whose
    up-transition sensitize condition and forced victim value happen to
    coincide with the natural word-write's own value under a SOLID
    background (both bits always see the identical nominal value, since
    it's literally the same write op) -- so the fault's effect is
    structurally invisible no matter how many faults are injected. A
    stripe background drives OPPOSITE physical values across the two bit
    lanes for "the same" nominal op, breaking that coincidence and exposing
    the fault. Found by an exhaustive empirical sweep (not derived by
    inspection -- the analogous CFIN/CFID cases with an 'either' transition
    turned out to already be detectable under solid, since march_c's own
    w0/w1 alternation eventually writes the opposite of any fixed forced
    value; this 'up'-only, init=0 case is the one that stays masked)."""
    mem = MemoryParams(addr_width=8, data_width=8, init_val=0)
    spec = _march_c()
    fault = FaultRecord("CFID", vaddr=5, vbit=0, aaddr=5, abit=1, p0=0, p1=1)

    solid_result = run_algo_campaign(mem, spec, [fault], workdir=tmp_path / "solid")
    assert solid_result.detected == 0, "expected the intra-word coupling fault to escape under the solid background"

    per_background = run_background_campaign(mem, spec, [fault], workdir=tmp_path / "backgrounds")
    assert per_background["solid"].detected == 0
    merged = merge_background_results(per_background)
    assert merged.detected == 1, "expected at least one stripe background to expose the intra-word coupling fault"
    assert merged.backgrounds_run == [b.name for b in standard_backgrounds(8)]


def test_shell_run_backgrounds_flag_merges_across_backgrounds(tmp_path: Path) -> None:
    shell, out = _run_script([
        "set_memory 8 8 --init 0",
        "add_fault CFID 5 0 5 1 0 1",
        "run march_c --backgrounds",
    ])
    assert "error:" not in out
    result = shell.session.last_results["march_c"]
    assert result.detected == 1
    assert result.backgrounds_run == [b.name for b in standard_backgrounds(8)]


def test_shell_run_backgrounds_matches_default_behavior_when_omitted() -> None:
    """Omitting --backgrounds must be byte-identical to before this feature:
    same coverage as run_algo_campaign, and no backgrounds_run key at all."""
    shell, out = _run_script(["set_memory 8 8", "run march_c"])
    assert "error:" not in out
    result = shell.session.last_results["march_c"]
    assert result.backgrounds_run is None
    assert "backgrounds_run" not in result.to_dict()


def test_shell_run_backgrounds_rejects_fsm_target() -> None:
    """--backgrounds is algo-only (openram_shim.sv has no +BACKGROUND path);
    register a stub FsmEntry directly to exercise the rejection without
    needing a real FSM source file."""
    from autombist.algo_shell import FsmEntry

    shell, _ = _run_script(["set_memory 8 8"])
    shell.session.fsms["stub"] = FsmEntry(sources=[find_engine_dir() / "march_engine.sv"], module_name="march_engine")
    shell.onecmd(shell.precmd("run stub --backgrounds"))
    out = shell.stdout.getvalue()
    assert "error:" in out
    assert "--backgrounds" in out


def test_shell_compare_algo_backgrounds_flag() -> None:
    shell, out = _run_script([
        "set_memory 8 8 --init 0",
        "add_fault CFID 5 0 5 1 0 1",
        "compare_algo march_c -march march_ss --backgrounds",
    ])
    assert "error:" not in out
    for name in ("march_c", "march_ss"):
        result = shell.session.last_results[name]
        assert result.backgrounds_run == [b.name for b in standard_backgrounds(8)]
