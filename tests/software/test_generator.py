from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from openmbist import ConfigError, generate_from_config, load_config, parse_args


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

    module_outdir = outdir / "sram_1rw"

    assert wrapper_path == module_outdir / "sram_1rw_mbist.v"
    assert wrapper_path.exists()

    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "module sram_1rw_mbist" in wrapper_text
    assert ".clk0(mbist_sram_clk)" in wrapper_text
    assert ".csb0(sram_csb)" in wrapper_text

    for rtl_name in ("mbist_algo.sv", "mbist_fsm.sv", "mbist_top.sv"):
        assert (module_outdir / rtl_name).exists(), f"Missing copied RTL file: {rtl_name}"

    assert not (module_outdir / "Makefile").exists()


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


def test_generate_with_saboteur_creates_fault_assets(
    tmp_path: Path, base_config: dict[str, object]
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    wrapper_path = generate_from_config(
        config_path,
        outdir,
        use_saboteur=True,
        faults=5,
        fault_seed=7,
    )

    assert wrapper_path.exists()

    module_outdir = outdir / "sram_1rw"

    saboteur_path = module_outdir / "sram_1rw_saboteur.v"
    assert saboteur_path.exists()
    assert (module_outdir / "Makefile").exists()

    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "sram_1rw_saboteur" in wrapper_text

    sa0_path = module_outdir / "faults" / "sa0_faults.hex"
    sa1_path = module_outdir / "faults" / "sa1_faults.hex"
    assert sa0_path.exists()
    assert sa1_path.exists()

    sa0_lines = sa0_path.read_text(encoding="ascii").splitlines()
    sa1_lines = sa1_path.read_text(encoding="ascii").splitlines()

    expected_depth = 1 << int(base_config["addr_width"])
    expected_hex_width = (int(base_config["data_width"]) + 3) // 4

    assert len(sa0_lines) == expected_depth
    assert len(sa1_lines) == expected_depth
    assert all(len(line) == expected_hex_width for line in sa0_lines)
    assert all(len(line) == expected_hex_width for line in sa1_lines)


def test_negative_faults_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    with pytest.raises(ValueError, match="faults"):
        generate_from_config(config_path, outdir, use_saboteur=True, faults=-1)


def test_cli_defaults() -> None:
    args = parse_args([])

    assert args.config == "config.yml"
    assert args.out == "out"
    assert args.test is False
    assert args.faults == 50
    assert args.seed is None


def test_cli_short_and_optional_values() -> None:
    args = parse_args(["--config", "a.yml", "--out", "build", "--test", "true", "-r", "12", "--seed", "9"])

    assert args.config == "a.yml"
    assert args.out == "build"
    assert args.test is True
    assert args.faults == 12
    assert args.seed == 9


def test_cli_test_flag_without_value_is_true() -> None:
    args = parse_args(["--test"])
    assert args.test is True


def test_cli_test_false_value() -> None:
    args = parse_args(["--test", "false"])
    assert args.test is False


def test_cli_invalid_bool_raises_system_exit() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--test", "maybe"])


def test_generated_fault_makefile_has_simple_targets(
    tmp_path: Path, base_config: dict[str, object]
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    generate_from_config(
        config_path,
        outdir,
        use_saboteur=True,
        faults=8,
        fault_seed=21,
    )

    makefile_path = outdir / "sram_1rw" / "Makefile"
    makefile_text = makefile_path.read_text(encoding="utf-8")

    assert "all: fault-test" in makefile_text
    assert "fault-test:" in makefile_text
    assert "debug: precheck" in makefile_text
    assert "Python3 not found at $(PYTHON_BIN). Maybe use venv? or install Python3." in makefile_text
    assert "$(MAKE) -C $(PROJECT_ROOT)/tests/hardware" in makefile_text
    assert "PYTHON_BIN=$(PYTHON_BIN)" in makefile_text
    assert "SIM_LOG ?= fault_sim.log" in makefile_text
