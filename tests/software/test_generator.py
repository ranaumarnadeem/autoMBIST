from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import pytest
import yaml
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist import ConfigError, __version__, generate_from_config, load_config
from autombist.main import app

runner = CliRunner()


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
    assert "march_c_top" in wrapper_text

    for rtl_name in ("mbist_algo.sv", "mbist_fsm.sv", "mbist_top.sv"):
        assert (module_outdir / rtl_name).exists(), f"Missing copied RTL file: {rtl_name}"

    for rtl_name in (
        "march_c/march_c_algo.sv",
        "march_c/march_c_fsm.sv",
        "march_c/march_c_top.sv",
        "march_raw/march_raw_algo.sv",
        "march_raw/march_raw_fsm.sv",
        "march_raw/march_raw_top.sv",
    ):
        assert (module_outdir / rtl_name).exists(), f"Missing copied algorithm RTL file: {rtl_name}"

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
    ports = cast(dict[str, str], invalid["ports"])
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

    expected_depth = 1 << int(cast(int, base_config["addr_width"]))
    expected_hex_width = (int(cast(int, base_config["data_width"])) + 3) // 4

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


def test_cli_help_mentions_version_and_options() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "--version" in result.output
    assert "--config" in result.output
    assert "--out" in result.output


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_generate_defaults(tmp_path: Path, base_config: dict[str, object]) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    result = runner.invoke(app, ["--config", str(config_path), "--out", str(outdir)])

    assert result.exit_code == 0
    assert (outdir / "sram_1rw" / "sram_1rw_mbist.v").exists()


def test_cli_generate_test_mode(tmp_path: Path, base_config: dict[str, object]) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    result = runner.invoke(
        app,
        ["--config", str(config_path), "--out", str(outdir), "--test", "--faults", "8", "--seed", "9"],
    )

    assert result.exit_code == 0
    module_outdir = outdir / "sram_1rw"
    assert (module_outdir / "Makefile").exists()
    assert (module_outdir / "faults" / "sa0_faults.hex").exists()


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
    assert "ALGO=march-c" in makefile_text
    assert "FAULT_TYPE := stuck-at" in makefile_text
    assert "PULSE_WIDTH_NS := 2" in makefile_text


def test_generate_transition_up_faults(tmp_path: Path, base_config: dict[str, object]) -> None:
    """Verify transition-up fault generation creates proper hex files."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    wrapper_path = generate_from_config(
        config_path,
        outdir,
        use_saboteur=True,
        faults=10,
        fault_seed=42,
        fault_type="transition-up",
    )

    assert wrapper_path.exists()

    module_outdir = outdir / "sram_1rw"

    tf_up_path = module_outdir / "faults" / "tf_up_faults.hex"
    assert tf_up_path.exists(), "transition-up fault file not generated"
    assert not (module_outdir / "faults" / "sa0_faults.hex").exists()
    assert not (module_outdir / "faults" / "sa1_faults.hex").exists()

    saboteur_text = (module_outdir / "sram_1rw_saboteur.v").read_text(encoding="utf-8")
    assert "tf_up_mask" in saboteur_text
    assert "shadow_mem" in saboteur_text
    assert "sa0_mask" not in saboteur_text

    tf_up_lines = tf_up_path.read_text(encoding="ascii").splitlines()
    expected_depth = 1 << int(cast(int, base_config["addr_width"]))
    expected_hex_width = (int(cast(int, base_config["data_width"])) + 3) // 4

    assert len(tf_up_lines) == expected_depth
    assert all(len(line) == expected_hex_width for line in tf_up_lines)


def test_generate_transition_down_faults(tmp_path: Path, base_config: dict[str, object]) -> None:
    """Verify transition-down fault generation creates proper hex files."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    wrapper_path = generate_from_config(
        config_path,
        outdir,
        use_saboteur=True,
        faults=15,
        fault_seed=123,
        fault_type="transition-down",
    )

    assert wrapper_path.exists()

    module_outdir = outdir / "sram_1rw"

    tf_down_path = module_outdir / "faults" / "tf_down_faults.hex"
    assert tf_down_path.exists(), "transition-down fault file not generated"
    assert not (module_outdir / "faults" / "sa0_faults.hex").exists()
    assert not (module_outdir / "faults" / "sa1_faults.hex").exists()

    saboteur_text = (module_outdir / "sram_1rw_saboteur.v").read_text(encoding="utf-8")
    assert "tf_down_mask" in saboteur_text
    assert "shadow_mem" in saboteur_text
    assert "sa0_mask" not in saboteur_text

    tf_down_lines = tf_down_path.read_text(encoding="ascii").splitlines()
    expected_depth = 1 << int(cast(int, base_config["addr_width"]))
    expected_hex_width = (int(cast(int, base_config["data_width"])) + 3) // 4

    assert len(tf_down_lines) == expected_depth
    assert all(len(line) == expected_hex_width for line in tf_down_lines)


def test_invalid_fault_type_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    """Verify invalid fault types are rejected."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    with pytest.raises(ValueError, match="Invalid fault_type"):
        generate_from_config(
            config_path,
            outdir,
            use_saboteur=True,
            faults=5,
            fault_type="invalid-fault-type",
        )


def test_cli_transition_fault_flags(tmp_path: Path, base_config: dict[str, object]) -> None:
    """Verify CLI accepts transition fault type and pulse width flags."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    result = runner.invoke(
        app,
        [
            "--config", str(config_path),
            "--out", str(outdir),
            "--test",
            "--fault-type", "transition-up",
            "--pulse-width-ns", "3",
            "--algo", "march-raw",
        ],
    )

    assert result.exit_code == 0
    module_outdir = outdir / "sram_1rw"
    assert (module_outdir / "faults" / "tf_up_faults.hex").exists()
    assert (module_outdir / "sram_1rw_mbist.v").read_text(encoding="utf-8").count("march_raw_top") == 1


def test_transition_fault_files_have_expected_depth_and_width(
    tmp_path: Path, base_config: dict[str, object]
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    generate_from_config(
        config_path,
        outdir,
        use_saboteur=True,
        faults=12,
        fault_seed=99,
        fault_type="transition-up",
    )

    module_outdir = outdir / "sram_1rw"
    tf_up_path = module_outdir / "faults" / "tf_up_faults.hex"
    tf_lines = tf_up_path.read_text(encoding="ascii").splitlines()

    expected_depth = 1 << int(cast(int, base_config["addr_width"]))
    expected_hex_width = (int(cast(int, base_config["data_width"])) + 3) // 4

    assert len(tf_lines) == expected_depth
    assert all(len(line) == expected_hex_width for line in tf_lines)


def test_cli_march_raw_algo_accepted(tmp_path: Path, base_config: dict[str, object]) -> None:
    """Verify CLI accepts march-raw algorithm selection."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    result = runner.invoke(
        app,
        [
            "--config", str(config_path),
            "--out", str(outdir),
            "--algo", "march-raw",
        ],
    )

    assert result.exit_code == 0
    wrapper_text = (outdir / "sram_1rw" / "sram_1rw_mbist.v").read_text(encoding="utf-8")
    assert "march_raw_top" in wrapper_text


def test_invalid_algo_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    with pytest.raises(ValueError, match="algo must be one of"):
        generate_from_config(config_path, outdir, algo="invalid-algo")
