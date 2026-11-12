"""The CLI's contract for a bad input is one `autombist: ...` line and exit 1,
never a Python traceback (#59). One test per path that used to break it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from autombist.generator import ConfigError, load_config
from autombist.main import app

runner = CliRunner()

GOOD = {
    "memory_name": "sram_1rw",
    "wrapper_module_name": "sram_1rw_mbist",
    "addr_width": 4,
    "data_width": 8,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}


def _one_error_line(result, expected: str) -> None:
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    assert f"autombist: {expected}" in result.output, result.output


def test_generate_without_any_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _one_error_line(runner.invoke(app, ["generate"]), "Config file not found")


def test_run_without_any_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _one_error_line(runner.invoke(app, ["run"]), "Config file not found")


def test_fix_lef_units_on_a_directory(tmp_path: Path) -> None:
    _one_error_line(runner.invoke(app, ["fix-lef-units", str(tmp_path)]), "cannot read LEF")


def test_a_config_that_is_not_yaml(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    config.write_text("ports: {clk: [unclosed\n", encoding="utf-8")
    _one_error_line(runner.invoke(app, ["generate", "--config", str(config)]), f"{config} is not valid YAML")


@pytest.mark.parametrize(("extra", "named"), [({True: 1}, "True"), ({"ports": {0: {"clk": "c"}}}, "0 in ports")])
def test_a_config_key_that_is_not_a_name(tmp_path: Path, extra: dict, named: str) -> None:
    """`on: 1` is a boolean key in YAML 1.1, `0:` a number."""
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump({**GOOD, **extra}), encoding="utf-8")
    with pytest.raises(ConfigError, match=f"config key {named}"):
        load_config(config)
    _one_error_line(runner.invoke(app, ["generate", "--config", str(config)]), "config key")


@pytest.mark.parametrize(("specs", "message"), [
    ([{"name": "MYF"}], "entry 0 (MYF) is missing 'category'"),
    (["SA0"], "entry 0 must be a JSON object"),
    ({"name": "MYF"}, "must contain a JSON list"),
])
def test_test_with_a_malformed_fault_types_file(tmp_path: Path, specs, message: str) -> None:
    types = tmp_path / "types.json"
    types.write_text(json.dumps(specs), encoding="utf-8")
    faults = tmp_path / "faults.txt"
    faults.write_text("SA0 0 0 0 0 0 0\n", encoding="utf-8")
    result = runner.invoke(app, ["test", "-aw", "2", "-dw", "2", "--faults", str(faults), "--fault-types", str(types)])
    _one_error_line(result, "")
    assert message in result.output


@pytest.mark.parametrize(("bad", "named"), [
    ({"addr_width": None}, "addr_width must be an integer"),
    ({"addr_width": [4]}, "addr_width must be an integer"),
    ({"redundancy": 4}, "redundancy must be a mapping"),
    ({"redundancy": {"onchip_selfrepair": "yes"}}, "redundancy.onchip_selfrepair must be true or false"),
])
def test_wrap_test_access_with_a_malformed_config_value(tmp_path: Path, bad: dict, named: str) -> None:
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump({**GOOD, **bad}), encoding="utf-8")
    source = tmp_path / "w.v"
    source.write_text("module sram_1rw_mbist; endmodule\n", encoding="utf-8")
    result = runner.invoke(app, [
        "wrap-test-access", "--source", str(source), "--top", "sram_1rw_mbist", "--config", str(config),
        "--out", str(tmp_path / "ta"),
    ])
    _one_error_line(result, f"config {named}")


def test_wrap_test_access_with_an_empty_snapshot_beside_the_manifest(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config.yml").write_text("", encoding="utf-8")
    _one_error_line(runner.invoke(app, ["wrap-test-access", "--manifest", str(tmp_path)]), str(tmp_path))
