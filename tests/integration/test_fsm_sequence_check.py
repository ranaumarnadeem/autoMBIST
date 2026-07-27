"""End-to-end tests for controller sequence-correctness checking (Phase 1),
Verilator-gated. Proves the checker confirms a correct controller drives the
exact march sequence its spec requires -- and, critically, catches a controller
whose sequence does NOT match its claimed algorithm, a class of bug that fault
injection cannot detect by construction."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.alg_spec import resolve_algo  # noqa: E402
from autombist.algo_engine import FaultRecord, MemoryParams, run_fsm_campaign  # noqa: E402
from autombist.fsm_harness import check_ports, gather_sibling_sources  # noqa: E402
from autombist.main import app  # noqa: E402

MARCH_C_TOP = REPO_ROOT / "rtl" / "march_c" / "march_c_top.sv"
MARCH_2RW_TOP = REPO_ROOT / "rtl" / "march_2rw" / "march_2rw_top.sv"
MARCH_X_TOP = REPO_ROOT / "rtl" / "march_x" / "march_x_top.sv"
MATS_PLUS_TOP = REPO_ROOT / "rtl" / "mats_plus" / "mats_plus_top.sv"

runner = CliRunner()


def _write_faults(tmp_path: Path) -> Path:
    p = tmp_path / "faults.txt"
    p.write_text("SA0 3 0 0 0 0 0\n", encoding="ascii")
    return p


# --------------------------------------------------------------------------- #
# API-level: run_fsm_campaign(expected_spec=...)
# --------------------------------------------------------------------------- #
def test_march_c_controller_matches_its_own_spec() -> None:
    """The reference march_c controller must drive exactly the march_c sequence.
    Uses a small memory so the golden trace is a few hundred ops, not millions."""
    sources = gather_sibling_sources(MARCH_C_TOP)
    module_name = check_ports(MARCH_C_TOP.read_text(encoding="utf-8")).module_name
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)

    result = run_fsm_campaign(
        mem, sources, module_name, [FaultRecord("SA0", 3, 0, 0, 0, 0, 0)],
        expected_spec=resolve_algo("march_c"),
    )

    assert result.sequence is not None
    assert result.sequence.matches is True, result.sequence.message()
    # march_c is 10n; over 16 words that's 160 memory operations.
    assert result.sequence.observed_count == 10 * mem.depth
    # Fault detection is unaffected -- the golden run still gates and the fault
    # is still detected, independent of the sequence check.
    assert result.detected == 1


def test_march_c_controller_diverges_from_wrong_spec() -> None:
    """The teeth of the check: point the SAME correct march_c controller at the
    WRONG algorithm spec (march_ss). Fault injection would still pass; the
    sequence checker must report a divergence, because march_c does not drive
    march_ss's 22n sequence."""
    sources = gather_sibling_sources(MARCH_C_TOP)
    module_name = check_ports(MARCH_C_TOP.read_text(encoding="utf-8")).module_name
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)

    result = run_fsm_campaign(
        mem, sources, module_name, [FaultRecord("SA0", 3, 0, 0, 0, 0, 0)],
        expected_spec=resolve_algo("march_ss"),
    )

    assert result.sequence is not None
    assert result.sequence.matches is False
    assert result.sequence.divergences, "expected at least one divergence vs march_ss"


def test_march_x_controller_matches_its_own_spec() -> None:
    """march_x_top.sv (Workstream B1, hand-written to mirror march_c_fsm.sv's
    style with its own phase count/direction table) must drive exactly its
    own 4-element .alg spec: {either(w0); up(r0,w1); down(r1,w0); either(r0)}."""
    sources = gather_sibling_sources(MARCH_X_TOP)
    module_name = check_ports(MARCH_X_TOP.read_text(encoding="utf-8")).module_name
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)

    result = run_fsm_campaign(
        mem, sources, module_name, [FaultRecord("SA0", 3, 0, 0, 0, 0, 0)],
        expected_spec=resolve_algo("march_x"),
    )

    assert result.sequence is not None
    assert result.sequence.matches is True, result.sequence.message()
    # march_x is 6n; over 16 words that's 96 memory operations.
    assert result.sequence.observed_count == 6 * mem.depth
    assert result.detected == 1


def test_mats_plus_controller_matches_its_own_spec() -> None:
    """mats_plus_top.sv (Workstream B1) must drive exactly its own 3-element
    .alg spec: {either(w0); up(r0,w1); down(r1,w0)} -- the minimal march this
    codebase generates."""
    sources = gather_sibling_sources(MATS_PLUS_TOP)
    module_name = check_ports(MATS_PLUS_TOP.read_text(encoding="utf-8")).module_name
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)

    result = run_fsm_campaign(
        mem, sources, module_name, [FaultRecord("SA0", 3, 0, 0, 0, 0, 0)],
        expected_spec=resolve_algo("mats_plus"),
    )

    assert result.sequence is not None
    assert result.sequence.matches is True, result.sequence.message()
    # mats_plus is 5n (1 + 2 + 2 ops across its 3 elements); over 16 words
    # that's 80 memory operations.
    assert result.sequence.observed_count == 5 * mem.depth
    assert result.detected == 1


def test_march_x_controller_diverges_from_mats_plus_spec() -> None:
    """march_x is mats_plus PLUS a trailing either(r0) element -- pointing
    march_x's own controller at mats_plus's (shorter) spec must diverge,
    proving the extra phase is a real, distinguishing difference and not an
    accidental copy-paste of the same sequence."""
    sources = gather_sibling_sources(MARCH_X_TOP)
    module_name = check_ports(MARCH_X_TOP.read_text(encoding="utf-8")).module_name
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)

    result = run_fsm_campaign(
        mem, sources, module_name, [FaultRecord("SA0", 3, 0, 0, 0, 0, 0)],
        expected_spec=resolve_algo("mats_plus"),
    )

    assert result.sequence is not None
    assert result.sequence.matches is False
    assert result.sequence.divergences, "expected at least one divergence vs mats_plus"


def test_sequence_check_is_opt_in_absent_by_default() -> None:
    """Without expected_spec, run_fsm_campaign behaves exactly as before:
    no sequence result attached."""
    sources = gather_sibling_sources(MARCH_C_TOP)
    module_name = check_ports(MARCH_C_TOP.read_text(encoding="utf-8")).module_name
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)

    result = run_fsm_campaign(mem, sources, module_name, [FaultRecord("SA0", 3, 0, 0, 0, 0, 0)])

    assert result.sequence is None
    assert result.detected == 1


def test_two_port_observer_taps_both_ports_end_to_end() -> None:
    """Multi-port smoke: the 2-port harness observer must actually emit and parse
    ACCESS lines under real Verilator (proving the MP observer taps work). The
    per-port comparison logic itself is unit-tested in test_seq_check.py; here we
    only assert the MP path produces a well-defined, non-empty observed trace."""
    sources = gather_sibling_sources(MARCH_2RW_TOP)
    module_name = check_ports(MARCH_2RW_TOP.read_text(encoding="utf-8"), num_ports=2).module_name
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1, num_ports=2)

    result = run_fsm_campaign(
        mem, sources, module_name, [FaultRecord("SA0", 3, 0, 0, 0, 0, 0)],
        expected_spec=resolve_algo("march_c"),
    )

    assert result.sequence is not None
    assert result.sequence.observed_count > 0  # MP observer emitted parseable ACCESS lines


# --------------------------------------------------------------------------- #
# CLI-level: `autombist test --fsm ... --check-sequence`
# --------------------------------------------------------------------------- #
def test_cli_check_sequence_reports_ok_for_matching_controller(tmp_path: Path) -> None:
    faults = _write_faults(tmp_path)
    result = runner.invoke(
        app,
        [
            "test", "-aw", "4", "-dw", "8",
            "--fsm", str(MARCH_C_TOP),
            "--algo", "march_c",
            "--faults", str(faults),
            "--check-sequence",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "sequence: OK" in result.output


def test_cli_check_sequence_reports_ok_for_march_x(tmp_path: Path) -> None:
    faults = _write_faults(tmp_path)
    result = runner.invoke(
        app,
        [
            "test", "-aw", "4", "-dw", "8",
            "--fsm", str(MARCH_X_TOP),
            "--algo", "march_x",
            "--faults", str(faults),
            "--check-sequence",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "sequence: OK" in result.output


def test_cli_check_sequence_reports_ok_for_mats_plus(tmp_path: Path) -> None:
    faults = _write_faults(tmp_path)
    result = runner.invoke(
        app,
        [
            "test", "-aw", "4", "-dw", "8",
            "--fsm", str(MATS_PLUS_TOP),
            "--algo", "mats_plus",
            "--faults", str(faults),
            "--check-sequence",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "sequence: OK" in result.output


def test_cli_check_sequence_exits_1_on_mismatch(tmp_path: Path) -> None:
    faults = _write_faults(tmp_path)
    result = runner.invoke(
        app,
        [
            "test", "-aw", "4", "-dw", "8",
            "--fsm", str(MARCH_C_TOP),
            "--algo", "march_ss",   # wrong spec for a march_c controller
            "--faults", str(faults),
            "--check-sequence",
        ],
    )
    assert result.exit_code == 1
    assert "sequence: MISMATCH" in result.output


def test_cli_check_sequence_mismatch_diagnostic_survives_quiet(tmp_path: Path) -> None:
    """'-q' must not delete the mismatch diagnostic: the final error message
    says '(see sequence MISMATCH above)', so something must actually be
    above it, regardless of -q. -q's own contract is 'suppress routine
    status lines', not 'suppress failure diagnosis'."""
    faults = _write_faults(tmp_path)
    result = runner.invoke(
        app,
        [
            "-q", "test", "-aw", "4", "-dw", "8",
            "--fsm", str(MARCH_C_TOP),
            "--algo", "march_ss",
            "--faults", str(faults),
            "--check-sequence",
        ],
    )
    assert result.exit_code == 1
    assert "sequence: MISMATCH" in result.output
    assert "see sequence MISMATCH above" in result.output


def test_cli_check_sequence_mismatch_diagnostic_present_with_json(tmp_path: Path) -> None:
    """'--json' must not lose the mismatch diagnostic either: it has to land
    on stderr, keeping stdout pure JSON (never corrupted by the diagnostic
    text) even in the failure case."""
    import json

    faults = _write_faults(tmp_path)
    result = runner.invoke(
        app,
        [
            "test", "-aw", "4", "-dw", "8",
            "--fsm", str(MARCH_C_TOP),
            "--algo", "march_ss",
            "--faults", str(faults),
            "--check-sequence", "--json",
        ],
    )
    assert result.exit_code == 1
    # stdout alone must still be exactly one parseable JSON document...
    payload = json.loads(result.stdout)
    assert payload["sequence"]["matches"] is False
    # ...while the human-readable diagnostic lands on stderr, not stdout.
    assert "sequence: MISMATCH" in result.stderr
    assert "sequence: MISMATCH" not in result.stdout


def test_cli_check_sequence_requires_fsm(tmp_path: Path) -> None:
    faults = _write_faults(tmp_path)
    result = runner.invoke(
        app,
        [
            "test", "-aw", "4", "-dw", "8",
            "--algo", "march_c",
            "--faults", str(faults),
            "--check-sequence",
        ],
    )
    assert result.exit_code == 1
    assert "--check-sequence requires --fsm" in result.output
