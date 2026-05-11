from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


class OpenRAMConfigError(ValueError):
    """Raised when openram.yml is missing required values or has invalid types."""


def default_openram_config() -> dict[str, Any]:
    return {
        "tech": "scn4m_subm",
        "word_size": 32,
        "num_words": 256,
        "num_rw_ports": 1,
        "num_r_ports": 0,
        "num_w_ports": 0,
        "write_size": None,
        "num_spare_rows": 1,
        "num_spare_cols": 1,
        "supply_voltage": None,
        "output_name": None,
        "setup_sky130": False,
        "sky130_setup_retries": 5,
        "run_drc_lvs": False,
        "keep_config": False,
        "openram_dir": None,
        "pdk_root": None,
        "output_root": "input",
        "verbose": False,
    }


def _require_type(name: str, value: Any, expected: tuple[type, ...]) -> None:
    if not isinstance(value, expected):
        expected_names = ", ".join(t.__name__ for t in expected)
        raise OpenRAMConfigError(f"{name} must be of type: {expected_names}")


def _require_positive_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise OpenRAMConfigError(f"{name} must be a positive integer")


def _require_non_negative_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OpenRAMConfigError(f"{name} must be a non-negative integer")


def load_openram_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"OpenRAM config file not found: {config_path}")

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise OpenRAMConfigError("OpenRAM config must be a YAML mapping")

    config = default_openram_config()
    config.update(loaded)

    _require_type("tech", config["tech"], (str,))
    if config["tech"] not in {"scn4m_subm", "sky130", "freepdk45"}:
        raise OpenRAMConfigError("tech must be one of: scn4m_subm, sky130, freepdk45")

    _require_positive_int("word_size", config["word_size"])
    _require_positive_int("num_words", config["num_words"])
    _require_non_negative_int("num_rw_ports", config["num_rw_ports"])
    _require_non_negative_int("num_r_ports", config["num_r_ports"])
    _require_non_negative_int("num_w_ports", config["num_w_ports"])
    _require_non_negative_int("num_spare_rows", config["num_spare_rows"])
    _require_non_negative_int("num_spare_cols", config["num_spare_cols"])
    _require_positive_int("sky130_setup_retries", config["sky130_setup_retries"])

    if config["write_size"] is not None:
        _require_positive_int("write_size", config["write_size"])
    if config["supply_voltage"] is not None and not isinstance(config["supply_voltage"], (int, float)):
        raise OpenRAMConfigError("supply_voltage must be a number")

    for bool_key in ("setup_sky130", "run_drc_lvs", "keep_config", "verbose"):
        _require_type(bool_key, config[bool_key], (bool,))

    for path_key in ("output_name", "openram_dir", "pdk_root", "output_root"):
        if config[path_key] is not None:
            _require_type(path_key, config[path_key], (str,))

    return config


def build_openram_command_args(config: dict[str, Any], config_path: Path) -> list[str]:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "synthesize_sram.py"
    cmd = [sys.executable, str(script_path)]

    def _add(name: str, value: Any) -> None:
        cmd.extend([name, str(value)])

    _add("--tech", config["tech"])
    _add("--word-size", config["word_size"])
    _add("--num-words", config["num_words"])
    _add("--num-rw-ports", config["num_rw_ports"])
    _add("--num-r-ports", config["num_r_ports"])
    _add("--num-w-ports", config["num_w_ports"])
    _add("--num-spare-rows", config["num_spare_rows"])
    _add("--num-spare-cols", config["num_spare_cols"])
    _add("--sky130-setup-retries", config["sky130_setup_retries"])

    if config["write_size"] is not None:
        _add("--write-size", config["write_size"])
    if config["supply_voltage"] is not None:
        _add("--supply-voltage", config["supply_voltage"])
    if config["output_name"]:
        _add("--output-name", config["output_name"])

    for path_key, arg_name in (
        ("openram_dir", "--openram-dir"),
        ("pdk_root", "--pdk-root"),
        ("output_root", "--output-root"),
    ):
        if config[path_key]:
            candidate = Path(str(config[path_key]))
            if not candidate.is_absolute():
                candidate = (config_path.parent / candidate).resolve()
            _add(arg_name, candidate)

    if config["setup_sky130"]:
        cmd.append("--setup-sky130")
    if config["run_drc_lvs"]:
        cmd.append("--run-drc-lvs")
    if config["keep_config"]:
        cmd.append("--keep-config")
    if config["verbose"]:
        cmd.append("--verbose")

    return cmd


def run_openram_synthesis(config_path: Path) -> subprocess.CompletedProcess[str]:
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)
    repo_root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        cmd,
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

