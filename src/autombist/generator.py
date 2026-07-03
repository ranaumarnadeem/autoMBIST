from __future__ import annotations

import importlib.resources
import re
import shutil
from collections import Counter
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

# Per-port-type required signal keys for the named multi-port config shape
# (ports: {name: {type: rw|r|w, ...}}). "rw" mirrors the legacy flat 6-key
# form; "r"/"w" drop the signals a read-only/write-only port doesn't have.
PORT_KEYS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "rw": ("clk", "addr", "din", "dout", "we", "csb"),
    "r": ("clk", "addr", "dout", "csb"),
    "w": ("clk", "addr", "din", "csb", "we"),
}
_VALID_PORT_TYPES = frozenset(PORT_KEYS_BY_TYPE)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _DuplicateKeyGuardLoader(yaml.SafeLoader):
    """SafeLoader that rejects literal duplicate keys instead of silently
    keeping only the last one (PyYAML's default, easy to trip over in a
    hand-edited multi-port ``ports:`` map)."""


def _construct_mapping_no_dupes(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConfigError(f"Duplicate key in config YAML: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DuplicateKeyGuardLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_no_dupes
)


def _normalize_algo(algo: str) -> tuple[str, str]:
    algo_value = algo.strip().lower()
    algo_map = {
        "march-c": ("march_c", "march_c_top"),
        "march-raw": ("march_raw", "march_raw_top"),
        "march-1r1w": ("march_1r1w", "march_1r1w_top"),
        "march-2rw": ("march_2rw", "march_2rw_top"),
    }
    if algo_value not in algo_map:
        raise ValueError("algo must be one of: march-c, march-raw, march-1r1w, march-2rw")
    return algo_map[algo_value]


# Algorithms that require a specific multi-port shape. Every other algo
# (march-c, march-raw) is still restricted to exactly 1 port.
_MULTI_PORT_ALGOS = {"march-1r1w", "march-2rw"}

# Per-algo required port-type COUNTS (type -> how many ports of that type),
# not merely a set of allowed types -- this is what lets march-2rw require
# two ports of the SAME type ("rw") while march-1r1w still requires exactly
# one of each of two DIFFERENT types ("r" and "w").
_MULTI_PORT_ROLE_REQUIREMENTS: dict[str, dict[str, int]] = {
    "march-1r1w": {"r": 1, "w": 1},
    "march-2rw": {"rw": 2},
}


def _validate_port_topology(normalized_ports: dict[str, dict[str, Any]], algo: str) -> None:
    """Cross-validate the *count* and *role composition* of normalized_ports
    against what the selected algo actually requires.

    march-c / march-raw (and any future single-port algo) require exactly one
    port (any type). march-1r1w requires exactly two ports whose types are
    exactly one "r" and one "w". march-2rw requires exactly two ports whose
    type is "rw" (both fully read/write) -- unlike march-1r1w, its two ports
    are the SAME type, so this comparison is done on a per-type count
    (Counter), not the plain "types form the right set" check used before
    march-2rw existed.
    """
    algo_value = algo.strip().lower()

    if algo_value not in _MULTI_PORT_ALGOS:
        if len(normalized_ports) != 1:
            raise ConfigError(
                f"algo {algo_value!r} requires exactly 1 port; got {len(normalized_ports)}. "
                "multi-port configs are only supported by algo=march-1r1w, march-2rw"
            )
        return

    required_counts = _MULTI_PORT_ROLE_REQUIREMENTS[algo_value]
    required_total = sum(required_counts.values())
    required_roles_sorted = sorted(required_counts)
    all_counts_are_one = required_total == len(required_counts)

    if len(normalized_ports) != required_total:
        if all_counts_are_one:
            # march-1r1w-style requirement (one of each of N distinct types):
            # byte-identical to the original pre-march-2rw wording.
            raise ConfigError(
                f"algo {algo_value!r} requires exactly {required_total} ports "
                f"(one of each of {required_roles_sorted}); got {len(normalized_ports)}"
            )
        raise ConfigError(
            f"algo {algo_value!r} requires exactly {required_total} ports "
            f"({dict(sorted(required_counts.items()))}); got {len(normalized_ports)}"
        )

    actual_counts = Counter(pdata["type"] for pdata in normalized_ports.values())
    if actual_counts != Counter(required_counts):
        port_types = sorted(pdata["type"] for pdata in normalized_ports.values())
        if all_counts_are_one:
            # Preserve march-1r1w's original wording byte-for-byte (existing
            # tests assert this exact substring).
            raise ConfigError(
                f"algo {algo_value!r} requires exactly one port of each type in "
                f"{required_roles_sorted}; got port types {port_types}"
            )
        raise ConfigError(
            f"algo {algo_value!r} requires port types {dict(sorted(required_counts.items()))}; "
            f"got port types {port_types}"
        )


def _validate_port_coupling_topology(normalized_ports: dict[str, dict[str, Any]], algo: str) -> None:
    """fault_type="port-coupling" models an inter-port bridging defect (an
    aggressor write disturbing a victim read on a genuinely concurrent same-
    address access) and is only meaningful -- and only implemented -- for the
    march-1r1w topology: exactly one read-only ("r") port and one write-only
    ("w") port. Reject it for single-port configs or any other port topology;
    a future phase may generalize this to march-2rw.
    """
    algo_value = algo.strip().lower()
    if algo_value != "march-1r1w":
        raise ConfigError(
            "fault_type=port-coupling requires algo=march-1r1w (exactly one "
            f"read-only port + one write-only port); got algo={algo_value!r}"
        )

    port_types = sorted(pdata["type"] for pdata in normalized_ports.values())
    if port_types != ["r", "w"]:
        raise ConfigError(
            "fault_type=port-coupling requires exactly one read-only ('r') port "
            f"and one write-only ('w') port; got port types {port_types}"
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


def _is_legacy_flat_ports(ports: dict[str, Any]) -> bool:
    """True when ``ports`` looks like the original flat single-port dict
    (``{clk, addr, din, dout, we, csb}``) rather than a named multi-port map.

    Deliberately robust to a *missing* required key -- keyed on "no per-port
    sub-mappings, and every present key is one of the flat signal roles" --
    so a legacy config with e.g. ``csb`` accidentally omitted still routes to
    the original clear "missing required key" error instead of being misread
    as a malformed named-port map (whose entries are themselves mappings).
    """
    if any(isinstance(value, dict) for value in ports.values()):
        return False
    return set(ports.keys()) <= set(REQUIRED_PORT_KEYS)


def _validate_port(name: str, pdata: Any, section: str) -> dict[str, Any]:
    """Validate one entry of a named multi-port ``ports:`` map and return its
    canonical form: ``{"type": ..., <role>: <signal>, ...}`` restricted to the
    keys that role actually needs (extra keys on the input are dropped)."""
    if not _IDENTIFIER_RE.match(name):
        raise ConfigError(f"{section} name {name!r} must be a valid identifier")
    if not isinstance(pdata, dict):
        raise ConfigError(f"{section} {name!r} must be a mapping")

    port_type = pdata.get("type")
    if port_type not in _VALID_PORT_TYPES:
        raise ConfigError(
            f"{section} {name!r}: type must be one of {sorted(_VALID_PORT_TYPES)}"
        )

    required = PORT_KEYS_BY_TYPE[port_type]
    _require_keys(pdata, required, f"{section} {name!r}")
    for key in required:
        value = pdata[key]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{section} {name!r}.{key} must be a non-empty string")

    return {"type": port_type, **{key: pdata[key] for key in required}}


def _algo_port_suffixes(normalized_ports: dict[str, dict[str, Any]], algo: str) -> dict[str, str]:
    """Map each port name to the numeric suffix its algo top module uses for
    that port's ``sram_*`` pins (e.g. ``sram_clk0``, ``sram_web1``).

    Single-port algos (march-c, march-raw) always use suffix "0" for their
    one ``rw`` port -- matching march_c_top/march_raw_top exactly. march-1r1w
    uses suffix "0" for its read-only ("r") port and suffix "1" for its
    write-only ("w") port -- matching march_1r1w_top exactly. This mapping is
    keyed on port *type*, not on ports-map iteration order, so it is immune to
    however the user happened to order/name their ports: mapping.

    march-2rw is different and IMPORTANT to get right: both of its ports are
    the SAME type ("rw"), so there is no type-based way to tell them apart.
    Instead, march-2rw suffixes are assigned by ITERATION ORDER of
    normalized_ports: the first port encountered (dict insertion order) gets
    suffix "0" (-> sram_*0, matching march_2rw_top's port-0 pins) and the
    second gets suffix "1" (-> sram_*1). This means, UNLIKE march-1r1w,
    reordering the two entries in the config's ``ports:`` map changes which
    named port maps to sram_*0 vs sram_*1 -- swap the two entries and the
    suffixes swap too. Callers relying on a stable port-0/port-1 assignment
    must control their YAML's key order.
    """
    algo_value = algo.strip().lower()
    if algo_value not in _MULTI_PORT_ALGOS:
        (only_name,) = normalized_ports.keys()
        return {only_name: "0"}

    if algo_value == "march-2rw":
        return {name: str(index) for index, name in enumerate(normalized_ports.keys())}

    type_to_suffix = {"r": "0", "w": "1"}
    return {name: type_to_suffix[pdata["type"]] for name, pdata in normalized_ports.items()}


def _check_signal_role_collisions(ports: dict[str, dict[str, Any]]) -> None:
    """Reject a signal name reused for two different pin roles anywhere in the
    design (e.g. one port's clk aliasing another port's write-enable). Reusing
    the same signal for the *same* role across ports (a shared clock pin) is
    fine and common on real dual-port macros."""
    signal_role: dict[str, str] = {}
    for pname, pdata in ports.items():
        for role, value in pdata.items():
            if role == "type":
                continue
            prior_role = signal_role.get(value)
            if prior_role is not None and prior_role != role:
                raise ConfigError(
                    f"signal name {value!r} is used for both role {prior_role!r} and "
                    f"{role!r} (port {pname!r}) -- a signal must play a single role "
                    "across the whole design"
                )
            signal_role[value] = role


def _normalize_ports(ports: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the canonical named-port-map view of ``ports:``, accepting both
    the legacy flat single-port dict and the new named multi-port map. Always
    returns ``{port_name: {"type": rw|r|w, <role>: <signal>, ...}}``."""
    if not isinstance(ports, dict) or not ports:
        raise ConfigError("ports must be a non-empty mapping")

    if _is_legacy_flat_ports(ports):
        _require_keys(ports, REQUIRED_PORT_KEYS, "ports")
        for key in REQUIRED_PORT_KEYS:
            value = ports[key]
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"ports.{key} must be a non-empty string")
        return {"p0": {"type": "rw", **{key: ports[key] for key in REQUIRED_PORT_KEYS}}}

    normalized: dict[str, dict[str, Any]] = {}
    for name, pdata in ports.items():
        normalized[name] = _validate_port(name, pdata, "ports")

    _check_signal_role_collisions(normalized)
    return normalized


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.load(handle, Loader=_DuplicateKeyGuardLoader)

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

    # Legacy flat single-port configs keep loaded["ports"] byte-identical (no
    # downstream consumer -- templates, RTL copy -- has to change for them).
    # loaded["normalized_ports"] is always the canonical named-port-map view,
    # for the multi-port-aware code landing in later phases.
    loaded["normalized_ports"] = _normalize_ports(ports)
    if not _is_legacy_flat_ports(ports):
        loaded["ports"] = loaded["normalized_ports"]

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


def _find_rtl_dir() -> Path:
    """Locate the MBIST RTL directory (works for both pip installs and dev installs)."""
    # Try package data first (pip-installed wheel)
    try:
        pkg_rtl = importlib.resources.files("autombist").joinpath("rtl")
        marker = pkg_rtl.joinpath("sram_model.sv")
        if marker.is_file():
            return Path(str(pkg_rtl))
    except (TypeError, FileNotFoundError, AttributeError):
        pass
    # Fallback: repo root layout (editable / dev install)
    repo_root = Path(__file__).resolve().parents[2]
    rtl_dir = repo_root / "rtl"
    if rtl_dir.is_dir():
        return rtl_dir
    raise FileNotFoundError(
        "MBIST RTL directory not found. Reinstall autombist or verify your installation."
    )


_ALGO_DIRS = {"march_c", "march_raw", "march_1r1w", "march_2rw"}


def copy_mbist_rtl(outdir: Path, algo_dir: str | None = None) -> None:
    """Copy the selected algorithm RTL and shared models into the output directory.

    Only the chosen ``algo_dir`` is copied (not every algorithm family), and the
    demo ``input_demo_*`` macros are skipped — they are never compiled from the
    output directory and only add clutter.
    """
    rtl_dir = _find_rtl_dir()
    for source_path in rtl_dir.rglob("*"):
        if not source_path.is_file():
            continue
        relative_path = source_path.relative_to(rtl_dir)
        top = relative_path.parts[0]
        if top in _ALGO_DIRS and algo_dir is not None and top != algo_dir:
            continue
        if top.startswith("input_demo"):
            continue
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

    if fault_type not in {"stuck-at", "transition-up", "transition-down", "port-coupling"}:
        raise ValueError(
            f"Invalid fault_type: {fault_type}. Must be one of: stuck-at, transition-up, transition-down, port-coupling"
        )

    config = load_config(config_path)

    _validate_port_topology(config["normalized_ports"], algo)

    if fault_type == "port-coupling":
        _validate_port_coupling_topology(config["normalized_ports"], algo)

    outdir.mkdir(parents=True, exist_ok=True)

    module_outdir = outdir / config["memory_name"]
    module_outdir.mkdir(parents=True, exist_ok=True)

    render_config = dict(config)
    render_config["read_latency"] = config.get("read_latency", 1)
    render_config["use_saboteur"] = use_saboteur
    render_config["pulse_width_ns"] = pulse_width_ns
    render_config["algo"] = algo
    render_config["fault_type"] = fault_type
    render_config["autombist_use_saboteur"] = use_saboteur
    render_config["autombist_faults"] = faults
    render_config["autombist_fault_seed"] = fault_seed
    render_config["autombist_fault_type"] = fault_type
    render_config["autombist_pulse_width_ns"] = pulse_width_ns
    render_config["autombist_algo"] = algo

    algo_dir, algo_top_module = _normalize_algo(algo)
    render_config["algo_dir"] = algo_dir
    render_config["algo_top_module"] = algo_top_module
    render_config["algo_port_suffixes"] = _algo_port_suffixes(config["normalized_ports"], algo)

    if use_saboteur:
        from .fault_gen import FaultType, generate_fault_files

        # Map fault_type string to FaultType enum
        fault_type_map = {
            "stuck-at": FaultType.STUCK_AT,
            "transition-up": FaultType.TRANSITION_UP,
            "transition-down": FaultType.TRANSITION_DOWN,
            "port-coupling": FaultType.PORT_COUPLING,
        }

        if fault_type not in fault_type_map:
            raise ValueError(
                f"Invalid fault_type: {fault_type}. Must be one of: "
                "stuck-at, transition-up, transition-down, port-coupling"
            )

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
        elif fault_enum == FaultType.PORT_COUPLING:
            render_config["pc_faults_file"] = file1_path.resolve().as_posix()

        saboteur_text = render_saboteur(render_config)
        saboteur_path = module_outdir / f"{config['memory_name']}_saboteur.v"
        saboteur_path.write_text(saboteur_text, encoding="utf-8")

        render_config["fault_count"] = faults
        render_config["fault_seed"] = fault_seed

        makefile_text = render_fault_makefile(render_config)
        makefile_path = module_outdir / "Makefile"
        makefile_path.write_text(makefile_text, encoding="utf-8")

    config_snapshot_path = module_outdir / "config.yml"
    config_snapshot_path.write_text(yaml.safe_dump(render_config, sort_keys=False), encoding="utf-8")

    wrapper_text = render_wrapper(render_config)
    wrapper_path = module_outdir / f"{config['memory_name']}_mbist.v"
    wrapper_path.write_text(wrapper_text, encoding="utf-8")

    copy_mbist_rtl(module_outdir, algo_dir)
    return wrapper_path
