"""`wrap-test-access` failures that come after a real test-access insertion: each
is one "autombist: ..." line with exit code 1, and the manifest is left as it
was. The triggers are real ones, not stand-ins: a directory where the PDL file
goes, and a manifest.json the run cannot write.

Skips without warptap (optional dependency) or Yosys.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from autombist.cli import app

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("warptap") is None or shutil.which("yosys") is None,
    reason="needs warptap (the Nix dev shell pins it) plus yosys on PATH (Linux/WSL only)",
)

runner = CliRunner()
TOP = "ta_err_ctrl"
CONFIG = {
    "memory_name": "sram_tiny", "wrapper_module_name": TOP, "addr_width": 3, "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}


def _generate(tmp_path: Path) -> Path:
    config_path = tmp_path / "c.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    result = runner.invoke(app, ["generate", "--config", str(config_path), "--out", str(tmp_path / "out"),
                                 "--emit-manifest"])
    assert result.exit_code == 0, result.output
    return tmp_path / "out" / CONFIG["memory_name"]


def _assert_one_error_line(result, fragment: str) -> None:
    assert result.exit_code == 1, result.output
    assert "autombist: " in result.output and fragment in result.output
    assert "Traceback" not in result.output


def test_a_run_mbist_file_it_cannot_write_is_one_error_line(tmp_path: Path) -> None:
    module_outdir = _generate(tmp_path)
    manifest = module_outdir / "manifest.json"
    before = manifest.read_text(encoding="utf-8")
    (module_outdir / "test-access" / f"{TOP}_run_mbist.pdl").mkdir(parents=True)

    result = runner.invoke(app, ["wrap-test-access", "--manifest", str(module_outdir)])

    _assert_one_error_line(result, f"{TOP}_run_mbist.pdl")
    assert (module_outdir / "test-access" / f"{TOP}_test_access.v").is_file()
    assert manifest.read_text(encoding="utf-8") == before


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes a read-only file anyway")
def test_a_manifest_it_cannot_update_is_one_error_line(tmp_path: Path) -> None:
    module_outdir = _generate(tmp_path)
    manifest = module_outdir / "manifest.json"
    before = manifest.read_text(encoding="utf-8")
    manifest.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        result = runner.invoke(app, ["wrap-test-access", "--manifest", str(module_outdir)])
    finally:
        manifest.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

    _assert_one_error_line(result, "manifest.json")
    assert "Updated manifest:" not in result.output
    assert manifest.read_text(encoding="utf-8") == before
