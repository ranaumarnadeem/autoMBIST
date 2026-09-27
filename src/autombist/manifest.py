"""Emits a machine-readable manifest describing a generated MBIST wrapper's
memory/controller/self-repair instances, for consumption by external
synthesis-aware tooling (FaultFlow) -- so it can blackbox the memory
instance(s) and keep MBIST-controller/self-repair/diagnosis instances visible
through its own Yosys synthesis, instead of treating the wrapper as one
opaque netlist to fully flatten and optimize.

Companion to faultflow_flow.py (autoMBIST -> FaultFlow controller grading,
subprocess-driven in the OTHER direction): this module only *describes* a
generated output directory -- it writes no Yosys scripts and runs no
external tools itself.

Scope (v1): the memory instance(s), the on-chip self-repair/diagnosis/
repair-remap instances (dedicated and shared-bus topologies, single- and
multi-port), and an honest placeholder for the JTAG/IJTAG test-access
network (whose internal instance names are not obtainable from this
codebase at all -- see update_manifest_with_test_access). Deliberately NOT
covered: the tester-driven (repair_ports:) multi-port repair-remap case --
wrapper_template.j2 has no per-port remap branch for it today (only the
on-chip-self-repair multi-port branch does), so guessing its instance names
would be fabrication, not description.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .faultflow_flow import _algo_dir, render_blackbox_stub
from .generator import _algo_port_suffixes

SCHEMA_VERSION = "1.0.0"
MANIFEST_FORMAT = "autombist_instance_manifest"
MANIFEST_FILENAME = "manifest.json"


class ManifestError(RuntimeError):
    """Raised when an instance manifest cannot be built, written, or updated."""


def _num_memories(config: dict[str, Any]) -> int:
    memories = config.get("memories")
    return len(memories) if memories else 1


def _is_shared_bus(config: dict[str, Any]) -> bool:
    return config.get("topology", "dedicated") == "shared-bus"


def _output_stem(config: dict[str, Any]) -> str:
    # Mirrors generate_from_config's own derivation exactly (generator.py) --
    # see the controller_sources() fix in faultflow_flow.py for the bug this
    # avoids repeating.
    return str(config["wrapper_module_name"]) if _is_shared_bus(config) else str(config["memory_name"])


def _redundancy_flags(config: dict[str, Any]) -> dict[str, bool]:
    """Faithful transliteration of wrapper_template.j2:3-9's own `{% set %}`
    lines -- the single source of truth for instance presence, not a second
    guess at it. Keep this in sync with that template if those lines change."""
    redundancy = config.get("redundancy") or {}
    has_redundancy = bool(redundancy) and int(redundancy.get("num_spare_rows", 0) or 0) > 0
    has_col_repair = has_redundancy and int(redundancy.get("num_spare_cols", 0) or 0) > 0
    has_onchip_selfrepair = has_redundancy and bool(redundancy.get("onchip_selfrepair", False))
    has_onchip_col_repair = has_onchip_selfrepair and bool(redundancy.get("onchip_col_repair", False))
    has_onchip_diagnosis = has_onchip_selfrepair and bool(redundancy.get("onchip_diagnosis", False))
    return {
        "has_redundancy": has_redundancy,
        "has_col_repair": has_col_repair,
        "has_onchip_selfrepair": has_onchip_selfrepair,
        "has_onchip_col_repair": has_onchip_col_repair,
        "has_onchip_diagnosis": has_onchip_diagnosis,
    }


def _analyzer_module_type(has_col_repair: bool) -> str:
    return "onchip_2d_repair_analyzer" if has_col_repair else "onchip_row_repair_analyzer"


def _memory_instances(config: dict[str, Any], stub_name: str) -> list[dict[str, Any]]:
    memory_type = str(config["memory_name"])
    if _is_shared_bus(config):
        return [
            {
                "hierarchical_path": f"u_mem_{mem['name']}",
                "instance_name": f"u_mem_{mem['name']}",
                "module_type": memory_type,
                "category": "memory",
                "hierarchy_hint": "blackbox",
                "stub_source": stub_name,
                "present_because": "always",
            }
            for mem in config["memories"]
        ]
    return [
        {
            "hierarchical_path": "u_sram",
            "instance_name": "u_sram",
            "module_type": memory_type,
            "category": "memory",
            "hierarchy_hint": "blackbox",
            "stub_source": stub_name,
            "present_because": "always",
        }
    ]


def _selfrepair_instance(path: str, name: str, module_type: str, category: str, why: str) -> dict[str, Any]:
    return {
        "hierarchical_path": path,
        "instance_name": name,
        "module_type": module_type,
        "category": category,
        "hierarchy_hint": "keep_hierarchy",
        "rtl_source": f"{module_type}.sv",
        "present_because": why,
    }


def _dedicated_instances(config: dict[str, Any], flags: dict[str, bool]) -> list[dict[str, Any]]:
    instances: list[dict[str, Any]] = []

    if flags["has_onchip_selfrepair"]:
        analyzer_type = _analyzer_module_type(flags["has_onchip_col_repair"])
        instances.append(_selfrepair_instance(
            "u_onchip_analyzer", "u_onchip_analyzer", analyzer_type,
            "self_repair", "redundancy.onchip_selfrepair",
        ))
        instances.append(_selfrepair_instance(
            "u_onchip_selfrepair_ctrl", "u_onchip_selfrepair_ctrl", "onchip_selfrepair_ctrl",
            "self_repair", "redundancy.onchip_selfrepair",
        ))
        if flags["has_onchip_diagnosis"]:
            instances.append(_selfrepair_instance(
                "u_onchip_diagnosis", "u_onchip_diagnosis", "onchip_diagnosis_log",
                "diagnosis", "redundancy.onchip_diagnosis",
            ))

    instances.extend(_dedicated_repair_remap_instances(config, flags))
    return instances


def _dedicated_repair_remap_instances(config: dict[str, Any], flags: dict[str, bool]) -> list[dict[str, Any]]:
    if not flags["has_redundancy"]:
        return []

    normalized_ports = config.get("normalized_ports") or {}
    onchip = flags["has_onchip_selfrepair"]
    # wrapper_template.j2:485-678 (single-port) vs. :991-1179 (multi-port):
    # col-repair presence is gated by has_onchip_col_repair inside the
    # on-chip branch, by has_col_repair inside the tester-driven branch --
    # never both at once for a given wrapper.
    col_repair = flags["has_onchip_col_repair"] if onchip else flags["has_col_repair"]

    if len(normalized_ports) <= 1:
        instances = [_selfrepair_instance(
            "u_repair_remap", "u_repair_remap", "repair_remap_row",
            "repair_remap", "redundancy.num_spare_rows",
        )]
        if col_repair:
            instances.append(_selfrepair_instance(
                "u_repair_remap_col", "u_repair_remap_col", "repair_remap_col",
                "repair_remap", "redundancy.num_spare_cols",
            ))
        return instances

    # Multi-port: only the on-chip self-repair branch instantiates a per-port
    # repair_remap_row (wrapper_template.j2:1103-1114, suffixed via
    # _algo_port_suffixes) -- the tester-driven multi-port case has no
    # equivalent branch in the template today, so it is left un-enumerated
    # here rather than guessed at.
    if not onchip:
        return []

    suffixes = _algo_port_suffixes(normalized_ports, str(config.get("algo", "march-c")))
    instances = [
        _selfrepair_instance(
            f"u_repair_remap{suffix}", f"u_repair_remap{suffix}", "repair_remap_row",
            "repair_remap", "redundancy.onchip_selfrepair",
        )
        for suffix in suffixes.values()
    ]
    if col_repair:
        port_types = sorted(p.get("type") for p in normalized_ports.values())
        has_dual_rw = port_types == ["rw", "rw"]
        if has_dual_rw:
            instances.extend(
                _selfrepair_instance(
                    f"u_repair_remap_col{suffix}", f"u_repair_remap_col{suffix}", "repair_remap_col",
                    "repair_remap", "redundancy.onchip_col_repair",
                )
                for suffix in suffixes.values()
            )
        else:
            instances.append(_selfrepair_instance(
                "u_repair_remap_col", "u_repair_remap_col", "repair_remap_col",
                "repair_remap", "redundancy.onchip_col_repair",
            ))
    return instances


def _shared_bus_instances(config: dict[str, Any], flags: dict[str, bool]) -> list[dict[str, Any]]:
    if not flags["has_onchip_selfrepair"]:
        return []
    analyzer_type = _analyzer_module_type(flags["has_onchip_col_repair"])
    instances: list[dict[str, Any]] = []
    for i in range(_num_memories(config)):
        prefix = f"selfrepair_inst[{i}]"
        instances.append(_selfrepair_instance(
            f"{prefix}.u_onchip_analyzer", "u_onchip_analyzer", analyzer_type,
            "self_repair", "redundancy.onchip_selfrepair",
        ))
        instances.append(_selfrepair_instance(
            f"{prefix}.u_onchip_selfrepair_ctrl", "u_onchip_selfrepair_ctrl", "onchip_selfrepair_ctrl",
            "self_repair", "redundancy.onchip_selfrepair",
        ))
        instances.append(_selfrepair_instance(
            f"{prefix}.u_repair_remap", "u_repair_remap", "repair_remap_row",
            "repair_remap", "redundancy.onchip_selfrepair",
        ))
        if flags["has_onchip_col_repair"]:
            instances.append(_selfrepair_instance(
                f"{prefix}.u_repair_remap_col", "u_repair_remap_col", "repair_remap_col",
                "repair_remap", "redundancy.onchip_col_repair",
            ))
    return instances


def build_instance_manifest(
    config: dict[str, Any],
    module_outdir: Path,
    *,
    tool_version: str,
    config_path: Path | None = None,
    command: str = "generate",
) -> dict[str, Any]:
    """Pure function, no I/O: builds the manifest dict from an already-loaded
    render_config (the same shape as the config.yml snapshot generate_from_config
    writes into every output directory)."""
    module_outdir = Path(module_outdir)
    stub_name = f"{config['memory_name']}_bbox.v"
    flags = _redundancy_flags(config)

    instances = list(_memory_instances(config, stub_name))
    if _is_shared_bus(config):
        instances.extend(_shared_bus_instances(config, flags))
    else:
        instances.extend(_dedicated_instances(config, flags))

    algo_dir = _algo_dir(config)
    sources = {
        "wrapper": f"{_output_stem(config)}_mbist.v",
        "algo": [f"{algo_dir}/{algo_dir}_{suffix}.sv" for suffix in ("algo", "fsm", "top")],
        "blackbox_stub": stub_name,
    }

    return {
        "format": MANIFEST_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool_version": tool_version,
        "generator": {
            "command": command,
            "config_path": str(config_path) if config_path is not None else None,
            "topology": "shared-bus" if _is_shared_bus(config) else "dedicated",
        },
        "top_module": str(config["wrapper_module_name"]),
        "module_outdir": str(module_outdir.resolve()),
        "sources": sources,
        "instances": instances,
        "test_access": None,
    }


def write_instance_manifest(manifest: dict[str, Any], module_outdir: Path) -> Path:
    module_outdir = Path(module_outdir)
    module_outdir.mkdir(parents=True, exist_ok=True)
    path = module_outdir / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path


def update_manifest_with_test_access(module_outdir: Path, test_access_block: dict[str, Any]) -> Path:
    """Patches an existing manifest.json's "test_access" key in place -- called
    after wrap-test-access actually runs. Raises ManifestError if generate
    --emit-manifest was never run for this output directory.

    test_access_block's "internal_instances" should be the literal string
    "not_enumerated": wrap_test_access's return value (testaccess.py) only
    ever yields an architecture-level ICL register description, never a
    gate-level TAP/SIB instance list -- there is no warptap API today that
    would make that field anything but an honest placeholder.
    """
    path = Path(module_outdir) / MANIFEST_FILENAME
    if not path.exists():
        raise ManifestError(
            f"{path} does not exist -- run `generate --emit-manifest` for this "
            "output directory before wrap-test-access --manifest"
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"could not read {path}: {exc}") from exc

    manifest["test_access"] = test_access_block
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path
