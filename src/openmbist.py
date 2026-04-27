from __future__ import annotations

import argparse
import shutil
import sys
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


def _render_template(config: dict[str, Any], template_dir: Path, template_name: str) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    try:
        template = env.get_template(template_name)
    except TemplateNotFound as exc:
        raise FileNotFoundError(f"Template not found: {template_dir / template_name}") from exc

    return template.render(**config)


def render_wrapper(config: dict[str, Any], template_dir: Path) -> str:
    return _render_template(config, template_dir, "wrapper_template.j2")


def render_saboteur(config: dict[str, Any], template_dir: Path) -> str:
    return _render_template(config, template_dir, "saboteur_template.j2")


def render_fault_makefile(config: dict[str, Any], template_dir: Path) -> str:
    return _render_template(config, template_dir, "fault_makefile_template.j2")


def copy_mbist_rtl(repo_root: Path, outdir: Path) -> None:
    rtl_dir = repo_root / "rtl"
    for rtl_file in MBIST_RTL_FILES:
        source_path = rtl_dir / rtl_file
        if not source_path.exists():
            raise FileNotFoundError(f"Required RTL file not found: {source_path}")
        shutil.copy2(source_path, outdir / rtl_file)


def generate_from_config(
    config_path: Path,
    outdir: Path,
    *,
    use_saboteur: bool = False,
    faults: int = 0,
    fault_seed: int | None = None,
) -> Path:
    if faults < 0:
        raise ValueError("faults must be a non-negative integer")

    config = load_config(config_path)

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    template_dir = script_dir / "templates"

    outdir.mkdir(parents=True, exist_ok=True)

    module_outdir = outdir / config["memory_name"]
    module_outdir.mkdir(parents=True, exist_ok=True)

    render_config = dict(config)
    render_config["use_saboteur"] = use_saboteur

    if use_saboteur:
        from fault_gen import generate_fault_files

        fault_dir = module_outdir / "faults"
        sa0_path, sa1_path = generate_fault_files(
            outdir=fault_dir,
            addr_width=config["addr_width"],
            data_width=config["data_width"],
            faults=faults,
            seed=fault_seed,
        )

        render_config["sa0_faults_file"] = sa0_path.resolve().as_posix()
        render_config["sa1_faults_file"] = sa1_path.resolve().as_posix()

        saboteur_text = render_saboteur(render_config, template_dir)
        saboteur_path = module_outdir / f"{config['memory_name']}_saboteur.v"
        saboteur_path.write_text(saboteur_text, encoding="utf-8")

        render_config["fault_count"] = faults
        render_config["fault_seed"] = fault_seed

        makefile_text = render_fault_makefile(render_config, template_dir)
        makefile_path = module_outdir / "Makefile"
        makefile_path.write_text(makefile_text, encoding="utf-8")

    wrapper_text = render_wrapper(render_config, template_dir)
    wrapper_path = module_outdir / f"{config['memory_name']}_mbist.v"
    wrapper_path.write_text(wrapper_text, encoding="utf-8")

    copy_mbist_rtl(repo_root, module_outdir)
    return wrapper_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    def parse_bool(value: str) -> bool:
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off"}:
            return False
        raise argparse.ArgumentTypeError("Expected true/false")

    parser = argparse.ArgumentParser(
        prog="python3 src/openmbist.py",
        description="Generate MBIST artifacts in OUTDIR/<memory_name>. Test mode also emits a runnable fault-sim Makefile.",
    )
    parser.add_argument("--config", default="config.yml", metavar="CONFIG", help="Config file path")
    parser.add_argument(
        "--out",
        default="out",
        metavar="OUTDIR",
        help="Base output directory (artifacts go to OUTDIR/<memory_name>)",
    )
    parser.add_argument(
        "--test",
        nargs="?",
        const=True,
        default=False,
        type=parse_bool,
        metavar="BOOL",
        help="Enable saboteur test mode and generate Makefile in output module dir (default: false)",
    )
    parser.add_argument(
        "-r",
        "--faults",
        type=int,
        default=50,
        metavar="NUMBEROFFAULTS",
        help="Number of faults in test mode (default: 50)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="SEED",
        help="Optional random seed",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        wrapper_path = generate_from_config(
            Path(args.config),
            Path(args.out),
            use_saboteur=args.test,
            faults=args.faults,
            fault_seed=args.seed,
        )
    except (ConfigError, FileNotFoundError, OSError, ValueError, yaml.YAMLError) as exc:
        print(f"openmbist: {exc}", file=sys.stderr)
        return 1

    print(f"Generated MBIST wrapper: {wrapper_path}")
    if args.test:
        print(f"Generated fault masks in: {wrapper_path.parent / 'faults'}")
        print(f"Generated fault-sim Makefile: {wrapper_path.parent / 'Makefile'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
