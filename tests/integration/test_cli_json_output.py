"""Real (non-mocked), Verilator-gated test for `autombist test --json`
(Workstream C-B): prints the full campaign result to stdout as JSON instead of
the human summary -- same shape as `--report <path> --fmt json` produces to a
file, just to stdout for scripting/piping.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import find_engine_dir  # noqa: E402
from autombist.main import app  # noqa: E402

runner = CliRunner()


def test_cli_test_json_prints_campaign_result_to_stdout() -> None:
    faults = find_engine_dir() / "faults.example.txt"
    result = runner.invoke(
        app,
        [
            "test", "-aw", "8", "-dw", "8", "--algo", "march_c",
            "--faults", str(faults), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["algo_name"] == "march_c"
    assert payload["total"] > 0
    assert payload["detected"] >= 0
    # The human-summary lines never leak into the JSON stdout stream.
    assert "autombist test:" not in result.output


def test_cli_test_json_includes_sequence_when_check_sequence_used(tmp_path: Path) -> None:
    faults_path = tmp_path / "faults.txt"
    faults_path.write_text("SA0 3 0 0 0 0 0\n", encoding="ascii")
    march_c_top = Path(__file__).resolve().parents[2] / "rtl" / "march_c" / "march_c_top.sv"

    result = runner.invoke(
        app,
        [
            "test", "-aw", "4", "-dw", "8",
            "--fsm", str(march_c_top), "--algo", "march_c",
            "--faults", str(faults_path), "--check-sequence", "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["sequence"]["matches"] is True
