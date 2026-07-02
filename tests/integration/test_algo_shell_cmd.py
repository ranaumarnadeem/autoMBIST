from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import find_engine_dir  # noqa: E402
from autombist.algo_shell import AlgoShell, Session  # noqa: E402

# Reference coverage from src/autombist/engine/README.md "Measured results" table.
REFERENCE_COVERAGE = {"march_c": (14, 19), "mats_plus": (12, 19), "march_ss": (18, 19)}


def _run_script(lines: list[str]) -> tuple[AlgoShell, str]:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    for line in lines:
        if shell.onecmd(shell.precmd(line)):
            break
    return shell, shell.stdout.getvalue()


def test_run_matches_reference_table() -> None:
    faults = find_engine_dir() / "faults.example.txt"
    shell, out = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        "run march_c",
    ])
    assert "14/19 detected" in out
    result = shell.session.last_results["march_c"]
    assert (result.detected, result.total) == REFERENCE_COVERAGE["march_c"]
    assert shell.session.last_op == ("run", "march_c")


def test_compare_algo_matrix_matches_reference_table() -> None:
    faults = find_engine_dir() / "faults.example.txt"
    shell, out = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        "compare_algo march_c -march mats_plus,march_ss",
    ])
    for name, (detected, total) in REFERENCE_COVERAGE.items():
        result = shell.session.last_results[name]
        assert (result.detected, result.total) == (detected, total), name
    assert "| fault" in out  # the markdown matrix was printed
    assert shell.session.last_matrix is not None
    assert [r.algo_name for r in shell.session.last_matrix] == ["march_c", "mats_plus", "march_ss"]


def test_write_report_after_compare(tmp_path: Path) -> None:
    faults = find_engine_dir() / "faults.example.txt"
    report_path = tmp_path / "matrix.csv"
    shell, _ = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        "compare_algo march_c -march march_ss",
        f"write_report {report_path} --fmt csv",
    ])
    assert report_path.exists()
    text = report_path.read_text()
    assert "march_c,march_ss" in text


def test_export_tb_produces_runnable_bundle(tmp_path: Path) -> None:
    faults = find_engine_dir() / "faults.example.txt"
    bundle_dir = tmp_path / "bundle"
    shell, _ = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        f"export_tb {bundle_dir}",
    ])
    for name in ("fault_ram.sv", "march_engine.sv", "openram_shim.sv", "run_campaign.sh", "faults.txt"):
        assert (bundle_dir / name).exists(), name
