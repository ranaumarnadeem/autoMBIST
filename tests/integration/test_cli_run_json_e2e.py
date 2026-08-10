"""Real (non-mocked), Icarus-gated end-to-end proof that `autombist run --json`
and `autombist simulate --json` (Workstream C-B) work through the actual CLI
entrypoint -- not just the library functions directly, and not mocked. Every
other `--json` test either mocks run_simulation (fast, cli-wiring-only) or
drives the research engine via Verilator (test_cli_json_output.py); this file
is the one place that proves the array-fault path's --json plumbing works
against a real Icarus Verilog + cocotb run.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("make") is None,
    reason="needs Icarus Verilog + make on PATH (Linux/WSL only)",
)

from autombist.main import app  # noqa: E402

runner = CliRunner()

CONFIG = {
    "memory_name": "sram_1rw",
    "wrapper_module_name": "sram_1rw_mbist",
    "addr_width": 4,
    "data_width": 8,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"},
}


def test_cli_run_json_prints_parseable_report(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")

    result = runner.invoke(
        app,
        ["run", "--config", str(config_path), "--out", str(tmp_path / "out"), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["config"]["memory_name"] == "sram_1rw"
    assert "Generated MBIST wrapper" not in result.output


def test_cli_simulate_json_prints_parseable_report(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    out_dir = tmp_path / "out"

    generate_result = runner.invoke(
        app, ["generate", "--config", str(config_path), "--out", str(out_dir)]
    )
    assert generate_result.exit_code == 0, generate_result.output

    result = runner.invoke(app, ["simulate", "--out", str(out_dir), "--json"])

    assert result.exit_code == 0, result.output
    # The WHOLE of stdout must be one parseable JSON document -- not "JSON plus
    # a trailing human summary line" -- even though the report dict legitimately
    # embeds its own rendered "summary" field as JSON string data inside it.
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["summary"].startswith("autombist: simulation")


def test_relative_out_works_across_separate_generate_and_simulate_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: `--out out` (relative, exactly as README.md and
    quickstart.md instruct) used to die with a raw 'make: No rule to make
    target'. OUTDIR was passed to `make -C hardware_dir` unresolved, and that
    subprocess runs with cwd=project_root (the dev/installed package root),
    not the caller's actual working directory -- so a relative OUTDIR
    resolved against the wrong directory entirely, no matter how the wrapper
    was actually generated. Every other test in this file passes
    `str(tmp_path / "out")`, which is already absolute and never exercised
    this path -- this one drives the CLI with a relative --out from a chdir'd
    cwd, matching a fresh checkout followed literally."""
    monkeypatch.chdir(tmp_path)
    Path("config.yml").write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")

    generate_result = runner.invoke(app, ["generate", "--config", "config.yml", "--out", "out"])
    assert generate_result.exit_code == 0, generate_result.output

    result = runner.invoke(app, ["simulate", "--out", "out"])
    assert result.exit_code == 0, result.output
