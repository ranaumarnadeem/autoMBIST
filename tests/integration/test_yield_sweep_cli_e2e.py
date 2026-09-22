"""Real, non-mocked end-to-end proof that `autombist yield-sweep` works
through the actual CLI entrypoint -- see test_yield_sweep_report.py for the
fast, pure-Python schema tests this builds on, and test_yield_sweep_e2e.py
for the direct real-Icarus proof of the underlying sweep()/run_trial()
functions this CLI command wraps unchanged.
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
    "memory_name": "sram_tiny",
    "wrapper_module_name": "sram_tiny_mbist",
    "addr_width": 2,
    "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}


def test_cli_yield_sweep_writes_a_real_report_and_json_round_trips(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    out_dir = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "yield-sweep", "--config", str(config_path), "--out", str(out_dir),
            "--faults", "1", "--trials", "2", "--spare-rows", "1", "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["config"]["memory_name"] == "sram_tiny"
    assert payload["spare_budget"] == {"num_spare_rows": 1, "num_spare_cols": 0}
    assert len(payload["points"]) == 1
    point = payload["points"][0]
    assert point["faults"] == 1
    assert len(point["trials"]) == 2
    # A single-fault trial on this geometry is deterministically repairable
    # with 1 spare row -- see test_yield_sweep_e2e.py's own proof of why.
    assert point["repair_rate"] == 1.0

    report_path = out_dir / "yield_sweep.json"
    assert report_path.exists()
    assert json.loads(report_path.read_text(encoding="utf-8")) == payload


def test_cli_yield_sweep_human_output_shows_a_summary_table(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    out_dir = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "yield-sweep", "--config", str(config_path), "--out", str(out_dir),
            "--faults", "1", "--trials", "1", "--spare-rows", "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Repair-yield sweep" in result.output
    assert str(out_dir / "yield_sweep.json") in result.output
