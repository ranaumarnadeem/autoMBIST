"""Real (non-mocked), Verilator-gated tests for the algo-shell fault-DSL
diagnosis/fail-bitmap report: `autombist test --diagnosis`, its byte-for-byte
regression proof against the pre-existing `--report` path, and an end-to-end
proof that a coupling-class fault's injection site and observation site can
genuinely differ.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import find_engine_dir, resolve_algo  # noqa: E402
from autombist.algo_engine import MemoryParams, load_fault_list, run_algo_campaign  # noqa: E402
from autombist.algo_reporting import build_diagnosis_cells  # noqa: E402
from autombist.main import app  # noqa: E402

runner = CliRunner()


def test_cli_diagnosis_produces_structured_file(tmp_path: Path) -> None:
    faults = find_engine_dir() / "faults.example.txt"
    diag_path = tmp_path / "diag.json"
    result = runner.invoke(
        app,
        [
            "test", "-aw", "8", "-dw", "8", "--algo", "march_c",
            "--faults", str(faults), "--diagnosis", str(diag_path), "--diagnosis-fmt", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert diag_path.exists()
    payload = json.loads(diag_path.read_text())
    assert payload["algo_name"] == "march_c"
    assert "cells" in payload
    assert len(payload["cells"]) > 0
    for cell in payload["cells"]:
        assert set(cell) == {
            "addr", "bit", "role", "fault_types_injected_here", "detected_as_injection",
            "escaped_types_here", "times_observed_mismatch", "observed_from_fault_types",
        }


def test_cli_without_diagnosis_flag_is_unchanged_from_today(tmp_path: Path) -> None:
    """Regression proof: omitting --diagnosis produces byte-identical output
    to before this feature existed (no diagnosis file, no extra CLI lines
    about it, --report path unaffected)."""
    faults = find_engine_dir() / "faults.example.txt"
    report_path_a = tmp_path / "a" / "cov.md"
    report_path_b = tmp_path / "b" / "cov.md"
    report_path_a.parent.mkdir()
    report_path_b.parent.mkdir()

    args_common = [
        "test", "-aw", "8", "-dw", "8", "--algo", "march_c", "--faults", str(faults),
    ]

    result_a = runner.invoke(app, [*args_common, "--report", str(report_path_a)])
    result_b = runner.invoke(app, [*args_common, "--report", str(report_path_b)])

    assert result_a.exit_code == 0, result_a.output
    assert result_b.exit_code == 0, result_b.output

    def normalize(output: str, report_path: Path) -> str:
        out = output.replace(str(report_path), "<REPORT>")
        # build/run seconds are wall-clock timings that legitimately vary
        # run-to-run; blank them out so the comparison is about shape/content,
        # not timing noise.
        out = re.sub(r"build: [\d.]+s   run: [\d.]+s", "build: <T>s   run: <T>s", out)
        return out

    normalized_a = normalize(result_a.output, report_path_a)
    normalized_b = normalize(result_b.output, report_path_b)
    assert normalized_a == normalized_b
    assert "diagnosis:" not in result_a.output  # the "  diagnosis: <path>" echo line is absent
    # The report content itself may embed the same build/run timings
    # (render_campaign_md includes them) -- normalize those too before
    # comparing file contents.
    text_a = re.sub(r"Build: [\d.]+s, run: [\d.]+s", "Build: <T>s, run: <T>s", report_path_a.read_text())
    text_b = re.sub(r"Build: [\d.]+s, run: [\d.]+s", "Build: <T>s, run: <T>s", report_path_b.read_text())
    assert text_a == text_b

    # No diagnosis file was created anywhere near the report.
    assert not (tmp_path / "a" / "diag.md").exists()
    assert not (tmp_path / "b" / "diag.md").exists()


def test_e2e_address_decoder_fault_shows_injection_observation_divergence() -> None:
    """Run a real campaign against faults.example.txt (which includes all 4
    coupling-class primitives plus AF_ALIAS, an address-decoder fault, via
    generate_all_types_faults's sibling fixture) and prove that at least one
    cell in the diagnosis table shows a genuine injection-site-vs-
    observation-site divergence: a fault's detected observation addr differs
    from its own vaddr.

    Empirically (see march_engine.sv's do_read: detection is always attributed
    to the address of the read op that caught the mismatch), the 4 same-word
    coupling primitives (CFIN/CFID/CFST/CFDS) corrupt the victim cell itself,
    so their detecting read is always issued at addr==vaddr in this engine's
    march algorithms -- no divergence there (a coupling fault's corruption
    lives IN the victim cell's own storage, so whichever address later reads
    that cell is, by construction, the victim's own address). AF_ALIAS
    ("accesses to VADDR land on word AADDR instead") is the fault whose
    *victim address itself is redirected* at access time, so its detecting
    read is issued at the redirected address (AADDR), not VADDR -- addr=AADDR
    != vaddr=VADDR. This is exactly the injection-site-vs-observation-site
    divergence the diagnosis table's dual-view (role="injection" vs
    role="observation") is designed to capture: a real, RTL-verified case
    (address-decoder faults, not coupling faults -- see the name of this
    test), not a hypothetical one.
    """
    faults_path = find_engine_dir() / "faults.example.txt"
    records = load_fault_list(faults_path)
    spec = resolve_algo("march_ss")  # highest coverage (18/19) -- most detections to inspect
    mem = MemoryParams(addr_width=8, data_width=8, init_val=1)

    result = run_algo_campaign(mem, spec, records)
    assert result.golden_clean is True

    # Direct proof at the FaultResult level: find a detected fault whose
    # observation addr != its own injection vaddr.
    divergent = [
        r for r in result.faults
        if r.detected and r.addr is not None and r.addr != r.record.vaddr
    ]
    assert divergent, (
        "expected at least one fault whose detected observation address "
        "differs from its injection vaddr"
    )
    # AF_ALIAS is the concrete fault type this diverges for in this engine.
    assert any(r.record.type == "AF_ALIAS" for r in divergent)

    # And the same proof surfacing through the diagnosis table itself: the
    # divergent fault's injection cell and observation cell must be different
    # rows, with the injection cell listing the fault type in
    # fault_types_injected_here and the observation cell listing it in
    # observed_from_fault_types.
    cells = {(c["addr"], c["bit"]): c for c in build_diagnosis_cells(result)}
    example = next(r for r in divergent if r.record.type == "AF_ALIAS")
    injection_cell = cells[(example.record.vaddr, example.record.vbit)]
    assert example.record.type in injection_cell["fault_types_injected_here"]

    assert example.xor is not None
    from autombist.algo_reporting import _decode_xor_bits

    observed_bits = _decode_xor_bits(example.xor)
    assert observed_bits, "a DETECTED fault must have at least one mismatched bit"
    observation_cell = cells[(example.addr, observed_bits[0])]
    assert example.record.type in observation_cell["observed_from_fault_types"]
    assert (example.addr, observed_bits[0]) != (example.record.vaddr, example.record.vbit)
