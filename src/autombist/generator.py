from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, PackageLoader, TemplateNotFound

REQUIRED_TOP_KEYS = (
    "memory_name",
    "wrapper_module_name",
    "addr_width",
    "data_width",
    "we_active_low",
    "ports",
)
REQUIRED_PORT_KEYS = ("clk", "addr", "din", "dout", "we", "csb")


def _normalize_algo(algo: str) -> tuple[str, str]:
    algo_value = algo.strip().lower()
    algo_map = {
        "march-c": ("march_c", "march_c_top"),
        "march-raw": ("march_raw", "march_raw_top"),
    }
    if algo_value not in algo_map:
        raise ValueError("algo must be one of: march-c, march-raw")
    return algo_map[algo_value]

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


def _render_template(config: dict[str, Any], template_name: str) -> str:
    env = Environment(
        loader=PackageLoader("autombist", "templates"),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    try:
        template = env.get_template(template_name)
    except TemplateNotFound as exc:
        raise FileNotFoundError(f"Template not found: {template_name}") from exc

    return template.render(**config)


def render_wrapper(config: dict[str, Any]) -> str:
    return _render_template(config, "wrapper_template.j2")


def render_saboteur(config: dict[str, Any]) -> str:
    return _render_template(config, "saboteur_template.j2")


def render_fault_makefile(config: dict[str, Any]) -> str:
    return _render_template(config, "fault_makefile_template.j2")


def copy_mbist_rtl(repo_root: Path, outdir: Path) -> None:
    rtl_dir = repo_root / "rtl"
    for source_path in rtl_dir.rglob("*"):
        if source_path.is_file():
            relative_path = source_path.relative_to(rtl_dir)
            destination_path = outdir / relative_path
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)


def generate_from_config(
    config_path: Path,
    outdir: Path,
    *,
    use_saboteur: bool = False,
    faults: int = 0,
    fault_seed: int | None = None,
    fault_type: str = "stuck-at",
    pulse_width_ns: int = 2,
    algo: str = "march-c",
) -> Path:
    if faults < 0:
        raise ValueError("faults must be a non-negative integer")

    config = load_config(config_path)

    repo_root = Path(__file__).resolve().parents[2]

    outdir.mkdir(parents=True, exist_ok=True)

    module_outdir = outdir / config["memory_name"]
    module_outdir.mkdir(parents=True, exist_ok=True)

    render_config = dict(config)
    render_config["use_saboteur"] = use_saboteur
    render_config["pulse_width_ns"] = pulse_width_ns
    render_config["algo"] = algo
    render_config["fault_type"] = fault_type

    algo_dir, algo_top_module = _normalize_algo(algo)
    render_config["algo_dir"] = algo_dir
    render_config["algo_top_module"] = algo_top_module

    if use_saboteur:
        from .fault_gen import FaultType, generate_fault_files

        # Map fault_type string to FaultType enum
        fault_type_map = {
            "stuck-at": FaultType.STUCK_AT,
            "transition-up": FaultType.TRANSITION_UP,
            "transition-down": FaultType.TRANSITION_DOWN,
        }
        
        if fault_type not in fault_type_map:
            raise ValueError(f"Invalid fault_type: {fault_type}. Must be one of: stuck-at, transition-up, transition-down")
        
        fault_enum = fault_type_map[fault_type]

        fault_dir = module_outdir / "faults"
        file1_path, file2_path = generate_fault_files(
            outdir=fault_dir,
            addr_width=config["addr_width"],
            data_width=config["data_width"],
            fault_type=fault_enum,
            faults=faults,
            seed=fault_seed,
        )

        # Set file paths in render config based on fault type
        if fault_enum == FaultType.STUCK_AT:
            render_config["sa0_faults_file"] = file1_path.resolve().as_posix()
            render_config["sa1_faults_file"] = file2_path.resolve().as_posix()
        elif fault_enum == FaultType.TRANSITION_UP:
            render_config["tf_up_faults_file"] = file1_path.resolve().as_posix()
        elif fault_enum == FaultType.TRANSITION_DOWN:
            render_config["tf_down_faults_file"] = file1_path.resolve().as_posix()

        saboteur_text = render_saboteur(render_config)
        saboteur_path = module_outdir / f"{config['memory_name']}_saboteur.v"
        saboteur_path.write_text(saboteur_text, encoding="utf-8")

        render_config["fault_count"] = faults
        render_config["fault_seed"] = fault_seed

        makefile_text = render_fault_makefile(render_config)
        makefile_path = module_outdir / "Makefile"
        makefile_path.write_text(makefile_text, encoding="utf-8")

    wrapper_text = render_wrapper(render_config)
    wrapper_path = module_outdir / f"{config['memory_name']}_mbist.v"
    wrapper_path.write_text(wrapper_text, encoding="utf-8")

    copy_mbist_rtl(repo_root, module_outdir)
    return wrapper_path
