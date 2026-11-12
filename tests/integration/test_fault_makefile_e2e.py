"""The Makefile `generate --test` writes runs where it says it does: `make` in
its own directory, in any project (#63). It used to need an autoMBIST checkout
two levels up, with a venv and tests/hardware, and dropped read_latency."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("make") is None,
    reason="needs Icarus Verilog + make on PATH (Linux/WSL only)",
)

from autombist.generator import generate_from_config  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "src"


def test_the_generated_makefile_runs_in_a_project_outside_the_repo(tmp_path: Path) -> None:
    config = {
        "memory_name": "sram_1rw", "wrapper_module_name": "sram_1rw_mbist", "addr_width": 4, "data_width": 8,
        "we_active_low": True, "read_latency": 0,
        "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
    }
    (tmp_path / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    module = generate_from_config(tmp_path / "config.yml", tmp_path / "out", use_saboteur=True,
                                  faults=4, fault_seed=3).parent

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}
    result = subprocess.run(
        ["make", f"AUTOMBIST={sys.executable} -m autombist"],
        cwd=module, env=env, capture_output=True, text=True, timeout=600, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Coverage" in result.stdout or "coverage" in result.stdout, result.stdout
    assert (module / "reports" / "latest.json").is_file()
