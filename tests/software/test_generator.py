from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from openmbist import ConfigError, generate_from_config, load_config


@pytest.fixture
def base_config() -> dict[str, object]:
    return {
        "memory_name": "sram_1rw",
        "wrapper_module_name": "sram_1rw_mbist",
        "addr_width": 10,
        "data_width": 32,
        "we_active_low": True,
        "ports": {
            "clk": "clk0",
            "addr": "addr0",
            "din": "din0",
            "dout": "dout0",
            "we": "we0",
            "csb": "csb0",
        },
    }


def _write_yaml(path: Path, content: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")


def test_generate_wrapper_and_package_rtl(tmp_path: Path, base_config: dict[str, object]) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    wrapper_path = generate_from_config(config_path, outdir)

    assert wrapper_path == outdir / "sram_1rw_mbist.v"
    assert wrapper_path.exists()

    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "module sram_1rw_mbist" in wrapper_text
    assert ".clk0(mbist_sram_clk)" in wrapper_text
    assert ".csb0(sram_csb)" in wrapper_text

    for rtl_name in ("mbist_algo.sv", "mbist_fsm.sv", "mbist_top.sv"):
        assert (outdir / rtl_name).exists(), f"Missing copied RTL file: {rtl_name}"


def test_missing_required_key_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    invalid = dict(base_config)
    invalid.pop("memory_name")

    config_path = tmp_path / "missing_memory_name.yml"
    _write_yaml(config_path, invalid)

    with pytest.raises(ConfigError, match="memory_name"):
        load_config(config_path)


def test_missing_required_port_key_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    invalid = dict(base_config)
    ports = dict(invalid["ports"])
    ports.pop("csb")
    invalid["ports"] = ports

    config_path = tmp_path / "missing_port_key.yml"
    _write_yaml(config_path, invalid)

    with pytest.raises(ConfigError, match="csb"):
        load_config(config_path)


def test_invalid_boolean_type_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    invalid = dict(base_config)
    invalid["we_active_low"] = "true"

    config_path = tmp_path / "invalid_bool.yml"
    _write_yaml(config_path, invalid)

    with pytest.raises(ConfigError, match="we_active_low"):
        load_config(config_path)
