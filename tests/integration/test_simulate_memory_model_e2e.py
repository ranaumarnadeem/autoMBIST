"""`simulate` and `run` grade the user's memory model, never the test fixture
of the same name in this repo's tests/hardware (#44).

Each test simulates for real (Icarus + cocotb). A broken model -- every read
returns 0 -- has to make the clean BIST fail: before the fix, `run` compiled
tests/hardware/sram_1rw.v whatever model the user had, and passed.
"""
from __future__ import annotations

import re
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


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An `autombist init`-style project directory, made the working directory."""
    (tmp_path / "config.yml").write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _broken_copy(good: Path, dest: Path) -> Path:
    """The good model with every read returning 0: a memory the BIST must fail."""
    text, n = re.subn(r"dout0 <= mem\[p0_addr_q\];", "dout0 <= 8'h00;", good.read_text(encoding="utf-8"))
    assert n == 1, "the generated model's read line moved; update this test"
    dest.write_text(text, encoding="utf-8")
    return dest


def test_run_defaults_to_the_generated_model_and_names_it(tmp_path, monkeypatch) -> None:
    _project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["run", "--config", "config.yml", "--out", "out"])
    assert result.exit_code == 0, result.output
    generated = (tmp_path / "out" / "sram_1rw" / "tb" / "sram_1rw_model.v").resolve()
    assert f"Memory model: {generated} (generated behavioral model" in result.output
    log = (tmp_path / "out" / "sram_1rw" / "simulate.log").read_text(encoding="utf-8")
    assert str(generated) in log
    assert "tests/hardware/sram_1rw.v" not in log


def test_a_broken_model_in_the_project_directory_fails_run(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path, monkeypatch)
    good = runner.invoke(app, ["generate", "--config", "config.yml", "--out", "gen"])
    assert good.exit_code == 0, good.output
    model = _broken_copy(project / "gen" / "sram_1rw" / "tb" / "sram_1rw_model.v", project / "sram_1rw.v")

    result = runner.invoke(app, ["run", "--config", "config.yml", "--out", "out"])
    assert result.exit_code == 1, result.output
    log = (project / "out" / "sram_1rw" / "simulate.log").read_text(encoding="utf-8")
    assert str(model.resolve()) in log  # on the compiler's command line
    assert "MBIST reported fail in clean mode" in log


def test_simulate_model_option_replaces_the_default(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path, monkeypatch)
    generated = runner.invoke(app, ["generate", "--config", "config.yml", "--out", "out"])
    assert generated.exit_code == 0, generated.output
    module = project / "out" / "sram_1rw"
    # Written before the first simulate, so it is OLDER than that build: make's
    # mtime check alone would reuse the simulator compiled from the good model.
    broken = _broken_copy(module / "tb" / "sram_1rw_model.v", project / "elsewhere.v")

    assert runner.invoke(app, ["simulate", "--out", "out"]).exit_code == 0
    result = runner.invoke(app, ["simulate", "--out", "out", "--model", str(broken)])
    assert result.exit_code == 1, result.output
    assert runner.invoke(app, ["simulate", "--out", "out"]).exit_code == 0  # and back again

    missing = runner.invoke(app, ["simulate", "--out", "out", "--model", "nope.v"])
    assert missing.exit_code == 1
    assert "memory model not found" in missing.output and "Traceback" not in missing.output


def test_tcl_run_with_a_relative_out_passes(tmp_path, monkeypatch) -> None:
    """#54: the shell's `run` passes generate's relative output path straight to
    the simulation, which runs make in another directory."""
    pytest.importorskip("tkinter")
    from autombist.tcl_shell import TclShell

    _project(tmp_path, monkeypatch)
    coverage = TclShell().eval("run -config config.yml -out out")
    assert coverage == ""  # a clean run reports no fault coverage
    assert (tmp_path / "out" / "sram_1rw" / "reports" / "latest.json").is_file()
