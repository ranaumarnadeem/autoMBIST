from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import builtin_algos, find_engine_dir, load_alg_file  # noqa: E402
from autombist.algo_engine import (  # noqa: E402
    MemoryParams,
    generate_all_types_faults,
    run_algo_campaign,
)
from autombist.algo_reporting import compute_syndrome_groups  # noqa: E402
from autombist.algo_shell import AlgoShell, Session  # noqa: E402


def _run_script(lines: list[str]) -> tuple[AlgoShell, str]:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    for line in lines:
        if shell.onecmd(shell.precmd(line)):
            break
    return shell, shell.stdout.getvalue()


def _ambiguous_type_sets(groups: list[dict]) -> list[set[str]]:
    return [set(g["fault_types"]) for g in groups if g["ambiguous"]]


def test_syndrome_reproduces_march_c_ambiguity_and_march_ss_resolves_wdf_drdf(tmp_path: Path) -> None:
    """The real, published problem this workstream targets: a plain march
    test can't distinguish every fault type from every other by detect/
    escape + location alone. Reproduces (qualitatively -- this engine's
    capture model is a coarser (elem, op) location, not the cited paper's
    literal per-element pass/fail bit-string) the shape of the SAF/TF/RDF/
    IRF/coupling ambiguity under March C-, and confirms March SS resolves
    exactly the WDF/DRDF portion of it (the fault classes March SS adds
    detection elements for) while leaving the rest of the ambiguity intact --
    an honest, verified answer to "does March SS resolve it," not an assumed
    one."""
    mem = MemoryParams(addr_width=8, data_width=8)
    faults = generate_all_types_faults(mem)

    march_c = load_alg_file(builtin_algos()["march_c"], name="march_c")
    march_ss = load_alg_file(builtin_algos()["march_ss"], name="march_ss")

    c_result = run_algo_campaign(mem, march_c, faults, workdir=tmp_path / "march_c")
    ss_result = run_algo_campaign(mem, march_ss, faults, workdir=tmp_path / "march_ss")

    c_groups = compute_syndrome_groups(c_result)
    ss_groups = compute_syndrome_groups(ss_result)

    # March C- cannot distinguish WDF0/WDF1/DRDF0/DRDF1/SOF from each other
    # (all escape identically) -- a real, engine-specific ambiguity.
    c_escaped = next(g for g in c_groups if g["detected"] is False)
    assert {"WDF0", "WDF1", "DRDF0", "DRDF1", "SOF"} <= set(c_escaped["fault_types"])
    assert c_escaped["ambiguous"] is True

    # March SS adds detection elements specifically for WDF/DRDF -- each now
    # gets its own distinct (elem, op) group, separated from the others.
    #
    # "Distinct" is the claim, not "singleton". Since the two-cell coupling
    # family landed, WDF1/DRDF1 share a syndrome with their coupling twin
    # (CFWD1 is WDF1 plus an aggressor-state gate, so when both are detected
    # they necessarily fire at the same elem/op) -- WDF0/DRDF0 do NOT (measured,
    # not assumed: CFWD0/CFDRD0 simply land in a different group here, so
    # "shares with its coupling twin" was never a universal claim, only true
    # for the two polarities that happen to). Since the dynamic (2-operation)
    # fault family landed, WDF0/WDF1 ALSO share a syndrome with the two
    # same-polarity dynamic types March SS catches at that exact polarity
    # (DYN_RDF0X/DYN_IRF0X for WDF0, DYN_RDF1X/DYN_IRF1X for WDF1) -- a real,
    # structural, newly-created ambiguity: March SS's non-transition `w0`
    # immediately followed by `r0` (or `w1`/`r1`) is the SAME op pair that
    # both catches WDF and sensitizes+detects the same-polarity dRDF/dIRF
    # dynamic types, so a failure there cannot distinguish "non-transition
    # write flipped the bit" from "write-then-read dynamically flipped it."
    # DRDF0/DRDF1 are unaffected (no dynamic type shares their group here).
    # None of this is the syndrome analysis being WRONG -- from a failure
    # signature alone you genuinely cannot distinguish a fault from another
    # whose sensitizing sequence fires at the identical point, whether that
    # other fault is a coupling-gated variant or an unrelated dynamic one.
    ss_by_type: dict[str, frozenset[str]] = {}
    for g in ss_groups:
        if g["detected"] is True:
            for name in g["fault_types"]:
                ss_by_type[name] = frozenset(g["fault_types"])
    acceptable_extra = {
        "WDF0": {"WDF0", "CFWD0", "DYN_RDF00", "DYN_IRF00"},
        "WDF1": {"WDF1", "CFWD1", "DYN_RDF11", "DYN_IRF11"},
        "DRDF0": {"DRDF0", "CFDRD0"},
        "DRDF1": {"DRDF1", "CFDRD1"},
    }
    for wdf_type, acceptable in acceptable_extra.items():
        assert wdf_type in ss_by_type, wdf_type
        assert ss_by_type[wdf_type] <= acceptable, (
            f"{wdf_type} shares a syndrome with something unexpected: "
            f"{sorted(ss_by_type[wdf_type])}"
        )
    # ...and the four remain mutually distinguishable, which is the actual
    # March-SS-resolves-the-ambiguity claim.
    assert len({ss_by_type[t] for t in acceptable_extra}) == 4

    # SOF is structurally undetectable against solid backgrounds (README's own
    # documented model limitation, unrelated to WDF/DRDF) -- still escapes,
    # and now shares that escape bucket with the eight dynamic (2-operation)
    # types March SS's own structure doesn't sensitize+detect (the
    # opposite-polarity dRDF/dIRF pairs plus all four dDRDF variants -- see
    # engine/README.md's "Dynamic (2-operation) faults"): a real, measured
    # ambiguity, not the pre-dynamic-faults "alone in its bucket" case this
    # test used to assert. The WDF/DRDF-resolution claim above is still the
    # actual point of this test and is unaffected -- confirmed separately,
    # fault by fault, not inferred from this bucket being empty of them.
    ss_escaped = next(g for g in ss_groups if g["detected"] is False)
    assert set(ss_escaped["fault_types"]) == {
        "SOF", "DYN_RDF01", "DYN_RDF10", "DYN_DRDF00", "DYN_DRDF01",
        "DYN_DRDF10", "DYN_DRDF11", "DYN_IRF01", "DYN_IRF10",
    }
    assert ss_escaped["ambiguous"] is True
    assert not any(t in ss_escaped["fault_types"] for t in ("WDF0", "WDF1", "DRDF0", "DRDF1"))

    # The SAF(0)-vs-TF-vs-RDF-vs-IRF-vs-coupling-class ambiguity is a
    # DIFFERENT ambiguity March SS's WDF/DRDF-focused additions don't target
    # -- confirm it persists identically in both algorithms (same fault-type
    # set grouped together), not silently resolved as a side effect.
    c_detected_ambiguous = _ambiguous_type_sets(c_groups)
    ss_detected_ambiguous = _ambiguous_type_sets(ss_groups)
    surviving = {"SA1", "TF1", "RDF0", "IRF0", "AF_ALIAS", "CFIN", "CFID"}
    assert any(surviving <= s for s in c_detected_ambiguous)
    assert any(surviving <= s for s in ss_detected_ambiguous)


def test_shell_write_syndrome_after_run_produces_file(tmp_path: Path) -> None:
    faults = find_engine_dir() / "faults.example.txt"
    syn_path = tmp_path / "syn.json"
    shell, out = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        "run march_c",
        f"write_syndrome {syn_path} --fmt json",
    ])
    assert "error:" not in out
    assert syn_path.exists()
    assert "syndrome report written" in out
    payload = json.loads(syn_path.read_text())
    assert payload["algo_name"] == "march_c"
    assert len(payload["groups"]) > 0


def test_shell_write_syndrome_after_compare_algo_raises_clear_error(tmp_path: Path) -> None:
    faults = find_engine_dir() / "faults.example.txt"
    syn_path = tmp_path / "syn.md"
    shell, out = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        "compare_algo march_c -march march_ss",
        f"write_syndrome {syn_path}",
    ])
    assert not syn_path.exists()
    assert "error:" in out
    assert "syndrome diagnosis only applies to a single 'run' result" in out
