from __future__ import annotations

import argparse
import shutil
import sys
from textwrap import dedent
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, TemplateNotFound

REQUIRED_TOP_KEYS = (
    "memory_name",
    "wrapper_module_name",
    "addr_width",
    "data_width",
    "we_active_low",
    "ports",
)
REQUIRED_PORT_KEYS = ("clk", "addr", "din", "dout", "we", "csb")
MBIST_RTL_FILES = ("mbist_algo.sv", "mbist_fsm.sv", "mbist_top.sv")

HELP_EPILOG = dedent(
     """
     Examples (run from repository root):
        1) Generate wrapper + packaged MBIST RTL:
            python3 src/openmbist.py --config config.yml --outdir out

        2) Run software tests:
            venv/bin/python3 -m pytest tests/software/test_generator.py -q

        3) Run hardware smoke test (cocotb + iverilog):
            PATH=\"$PWD/venv/bin:$PATH\" make -C tests/hardware SIM=icarus

        4) Run everything in sequence:
            python3 src/openmbist.py --config config.yml --outdir out && \
            venv/bin/python3 -m pytest tests/software/test_generator.py -q && \
            PATH=\"$PWD/venv/bin:$PATH\" make -C tests/hardware SIM=icarus
     """
)


class ConfigError(ValueError):
    """Raised when config.yml is missing required values or has invalid types."""


def _require_keys(data: dict[str, Any], required: tuple[str, ...], section: str) -> None:
    missing = [key for key in required if key not in data]
    if missing:
        missing_keys = ", ".join(missing)
        raise ConfigError(f"Missing required keys in {section}: {missing_keys}")


def _validate_positive_int(data: dict[str, Any], key: str) -> None:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer")


def _validate_non_empty_str(data: dict[str, Any], key: str) -> None:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if not isinstance(loaded, dict):
        raise ConfigError("Config must be a YAML mapping")

    _require_keys(loaded, REQUIRED_TOP_KEYS, "root")

    _validate_non_empty_str(loaded, "memory_name")
    _validate_non_empty_str(loaded, "wrapper_module_name")
    _validate_positive_int(loaded, "addr_width")
    _validate_positive_int(loaded, "data_width")

    if not isinstance(loaded["we_active_low"], bool):
        raise ConfigError("we_active_low must be a boolean")

    ports = loaded["ports"]
    if not isinstance(ports, dict):
        raise ConfigError("ports must be a mapping")

    _require_keys(ports, REQUIRED_PORT_KEYS, "ports")
    for key in REQUIRED_PORT_KEYS:
        value = ports[key]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"ports.{key} must be a non-empty string")

    return loaded


def render_wrapper(config: dict[str, Any], template_dir: Path) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    try:
        template = env.get_template("wrapper_template.j2")
    except TemplateNotFound as exc:
        raise FileNotFoundError(f"Template not found: {template_dir / 'wrapper_template.j2'}") from exc

    return template.render(**config)


def copy_mbist_rtl(repo_root: Path, outdir: Path) -> None:
    rtl_dir = repo_root / "rtl"
    for rtl_file in MBIST_RTL_FILES:
        source_path = rtl_dir / rtl_file
        if not source_path.exists():
            raise FileNotFoundError(f"Required RTL file not found: {source_path}")
        shutil.copy2(source_path, outdir / rtl_file)


def generate_from_config(config_path: Path, outdir: Path) -> Path:
    config = load_config(config_path)

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    template_dir = script_dir / "templates"

    outdir.mkdir(parents=True, exist_ok=True)

    wrapper_text = render_wrapper(config, template_dir)
    wrapper_path = outdir / f"{config['memory_name']}_mbist.v"
    wrapper_path.write_text(wrapper_text, encoding="utf-8")

    copy_mbist_rtl(repo_root, outdir)
    return wrapper_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 src/openmbist.py",
        description="Generate MBIST wrapper and package MBIST RTL artifacts from a YAML config.",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", required=True, help="Path to config.yml")
    parser.add_argument("--outdir", default="./out", help="Output directory for generated files")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        wrapper_path = generate_from_config(Path(args.config), Path(args.outdir))
    except (ConfigError, FileNotFoundError, OSError, yaml.YAMLError) as exc:
        print(f"openmbist: {exc}", file=sys.stderr)
        return 1

    print(f"Generated MBIST wrapper: {wrapper_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
