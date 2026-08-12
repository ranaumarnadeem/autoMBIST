from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from autombist.openram_flow import (
    OpenRAMConfigError,
    build_openram_command_args,
    load_openram_config,
    run_openram_synthesis,
)


def test_load_openram_config_rejects_invalid_tech(tmp_path: Path) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text(
        yaml.safe_dump({"tech": "invalid-tech", "word_size": 8, "num_words": 16}, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(OpenRAMConfigError, match="tech must be one of"):
        load_openram_config(config_path)


def test_build_openram_command_resolves_relative_paths(tmp_path: Path) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "tech": "scn4m_subm",
                "word_size": 8,
                "num_words": 16,
                "num_rw_ports": 1,
                "num_r_ports": 0,
                "num_w_ports": 0,
                "output_root": "input",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)

    output_root_index = cmd.index("--output-root") + 1
    assert cmd[output_root_index] == str((tmp_path / "input").resolve())


def _base_config_dict(**overrides: object) -> dict:
    config = {
        "tech": "scn4m_subm",
        "word_size": 8,
        "num_words": 16,
        "num_rw_ports": 1,
        "num_r_ports": 0,
        "num_w_ports": 0,
    }
    config.update(overrides)
    return config


def _write_config(tmp_path: Path, data: dict) -> Path:
    config_path = tmp_path / "openram.yml"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return config_path


# -- _require_type: wrong Python type entirely -----------------------------


def test_load_openram_config_rejects_non_string_tech(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(tech=4))

    with pytest.raises(OpenRAMConfigError, match="tech must be of type"):
        load_openram_config(config_path)


# -- _require_positive_int: bool / zero / negative --------------------------


def test_load_openram_config_rejects_bool_word_size(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(word_size=True))

    with pytest.raises(OpenRAMConfigError, match="word_size must be a positive integer"):
        load_openram_config(config_path)


def test_load_openram_config_rejects_zero_word_size(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(word_size=0))

    with pytest.raises(OpenRAMConfigError, match="word_size must be a positive integer"):
        load_openram_config(config_path)


def test_load_openram_config_rejects_negative_word_size(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(word_size=-1))

    with pytest.raises(OpenRAMConfigError, match="word_size must be a positive integer"):
        load_openram_config(config_path)


# -- _require_non_negative_int: bool / negative ------------------------------


def test_load_openram_config_rejects_bool_num_rw_ports(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(num_rw_ports=True))

    with pytest.raises(OpenRAMConfigError, match="num_rw_ports must be a non-negative integer"):
        load_openram_config(config_path)


def test_load_openram_config_rejects_negative_num_rw_ports(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(num_rw_ports=-1))

    with pytest.raises(OpenRAMConfigError, match="num_rw_ports must be a non-negative integer"):
        load_openram_config(config_path)


# -- load_openram_config: missing file --------------------------------------


def test_load_openram_config_missing_file_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "does_not_exist.yml"

    with pytest.raises(FileNotFoundError, match="OpenRAM config file not found"):
        load_openram_config(config_path)


# -- load_openram_config: empty YAML file (parses to None) ------------------


def test_load_openram_config_empty_file_returns_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text("# just a comment\n", encoding="utf-8")

    from autombist.openram_flow import default_openram_config

    config = load_openram_config(config_path)
    assert config == default_openram_config()


# -- load_openram_config: top-level YAML is a list, not a mapping -----------


def test_load_openram_config_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text(
        yaml.safe_dump(["scn4m_subm", 8, 16], sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(OpenRAMConfigError, match="OpenRAM config must be a YAML mapping"):
        load_openram_config(config_path)


# -- write_size: optional positive-int check ---------------------------------


def test_load_openram_config_rejects_zero_write_size(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(write_size=0))

    with pytest.raises(OpenRAMConfigError, match="write_size must be a positive integer"):
        load_openram_config(config_path)


def test_load_openram_config_rejects_negative_write_size(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(write_size=-4))

    with pytest.raises(OpenRAMConfigError, match="write_size must be a positive integer"):
        load_openram_config(config_path)


# -- supply_voltage: must be numeric -----------------------------------------


def test_load_openram_config_rejects_non_numeric_supply_voltage(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(supply_voltage="high"))

    with pytest.raises(OpenRAMConfigError, match="supply_voltage must be a number"):
        load_openram_config(config_path)


def test_load_openram_config_accepts_numeric_supply_voltage(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(supply_voltage=1.8))

    config = load_openram_config(config_path)
    assert config["supply_voltage"] == 1.8


# -- build_openram_command_args: optional flag/value presence ---------------


def test_build_openram_command_includes_write_size_when_set(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(write_size=4))
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)

    assert "--write-size" in cmd
    assert cmd[cmd.index("--write-size") + 1] == "4"


def test_build_openram_command_includes_supply_voltage_when_set(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(supply_voltage=1.8))
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)

    assert "--supply-voltage" in cmd
    assert cmd[cmd.index("--supply-voltage") + 1] == "1.8"


def test_build_openram_command_includes_output_name_when_set(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(output_name="my_sram"))
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)

    assert "--output-name" in cmd
    assert cmd[cmd.index("--output-name") + 1] == "my_sram"


# -- build_openram_command_args: bare boolean flags --------------------------


def test_build_openram_command_includes_all_bool_flags_when_true(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        _base_config_dict(
            setup_sky130=True,
            run_drc_lvs=True,
            keep_config=True,
            verbose=True,
        ),
    )
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)

    assert "--setup-sky130" in cmd
    assert "--run-drc-lvs" in cmd
    assert "--keep-config" in cmd
    assert "--verbose" in cmd


def test_build_openram_command_excludes_all_bool_flags_when_false(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict())
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)

    assert "--setup-sky130" not in cmd
    assert "--run-drc-lvs" not in cmd
    assert "--keep-config" not in cmd
    assert "--verbose" not in cmd


# -- run_openram_synthesis: subprocess invocation ----------------------------


def test_run_openram_synthesis_invokes_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _write_config(tmp_path, _base_config_dict())

    captured: dict = {}
    fake_result = subprocess.CompletedProcess(args=["fake"], returncode=0, stdout="ok", stderr="")

    def fake_run(cmd, cwd=None, text=None, capture_output=None, check=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return fake_result

    monkeypatch.setattr("autombist.openram_flow.subprocess.run", fake_run)

    result = run_openram_synthesis(config_path)

    repo_root = Path(__file__).resolve().parents[2]
    assert captured["cwd"] == repo_root
    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][1] == str(repo_root / "scripts" / "synthesize_sram.py")
    assert result is fake_result


# -- build_openram_command_args: absolute vs relative openram_dir/pdk_root --


def test_build_openram_command_keeps_absolute_openram_dir_unchanged(tmp_path: Path) -> None:
    absolute_dir = tmp_path / "openram_install"
    config_path = _write_config(tmp_path, _base_config_dict(openram_dir=str(absolute_dir)))
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)

    openram_dir_index = cmd.index("--openram-dir") + 1
    assert cmd[openram_dir_index] == str(absolute_dir)


def test_build_openram_command_resolves_relative_pdk_root(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _base_config_dict(pdk_root="pdk"))
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)

    pdk_root_index = cmd.index("--pdk-root") + 1
    assert cmd[pdk_root_index] == str((tmp_path / "pdk").resolve())


def test_build_openram_command_keeps_absolute_pdk_root_unchanged(tmp_path: Path) -> None:
    absolute_pdk = tmp_path / "pdk_install"
    config_path = _write_config(tmp_path, _base_config_dict(pdk_root=str(absolute_pdk)))
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)

    pdk_root_index = cmd.index("--pdk-root") + 1
    assert cmd[pdk_root_index] == str(absolute_pdk)
