"""Emits manifest.json: a synthesis plan for a generated MBIST wrapper, complete
enough that an external synthesis-aware tool (FaultFlow) can generate its own
Yosys scripts from it without re-deriving anything from autoMBIST's config.

Every instance the wrapper contains is listed with its module, the parameter
values the wrapper instantiates it with, and its source files. hierarchy_hint
says how to synthesize it:

* ``"blackbox"`` -- memory instances. Never synthesized: read the port-only
  stub (``<memory_name>_bbox.v``, which declares every parameter the wrapper
  may override) with ``read_verilog -lib`` so the memory is a test boundary.
* ``"separate"`` -- test instruments (the MBIST controller, on-chip
  self-repair, diagnosis, repair remaps). Synthesize each one standalone
  (``chparam`` with ``parameters``, then synth), read it back as a blackbox
  stub while synthesizing the wrapper glue, then splice the block netlists in
  -- so optimization never crosses an instrument boundary and the result can
  still be one flat netlist for a flat-only fault simulator.

Instances sharing (module_type, parameters) are the same synthesized block.

Deliberately NOT enumerated: the tester-driven (repair_ports:) multi-port
repair-remap case -- wrapper_template.j2 has no per-port remap branch for it
(only the on-chip-self-repair multi-port branch does), so its instance names
would be a guess.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .generator import _algo_port_suffixes, _normalize_algo, _normalize_ports

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
    # Mirrors generate_from_config's own derivation exactly (generator.py).
    return str(config["wrapper_module_name"]) if _is_shared_bus(config) else str(config["memory_name"])


def _ports(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return config.get("normalized_ports") or _normalize_ports(config["ports"])


def _algo_dir(config: dict[str, Any]) -> str:
    return str(config.get("algo_dir") or _normalize_algo(str(config.get("algo", "march-c")))[0])


def _redundancy_flags(config: dict[str, Any]) -> dict[str, bool]:
    """Faithful transliteration of wrapper_template.j2:3-9's own `{% set %}`
    lines -- keep in sync with that template if those lines change."""
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


def _geometry(config: dict[str, Any]) -> dict[str, int]:
    redundancy = config.get("redundancy") or {}
    aw = int(config["addr_width"])
    dw = int(config["data_width"])
    return {
        "addr_width": aw,
        "data_width": dw,
        "read_latency": int(config.get("read_latency", 1)),
        "num_spare_rows": int(redundancy.get("num_spare_rows", 0) or 0),
        "num_spare_cols": int(redundancy.get("num_spare_cols", 0) or 0),
        "num_diagnosis_entries": int(redundancy.get("num_diagnosis_entries", 0) or 0),
        "mem_addr_width": int(redundancy.get("mem_addr_width", aw) or aw),
        "mem_data_width": int(redundancy.get("mem_data_width", dw) or dw),
    }


def _memory_connection_widths(config: dict[str, Any], flags: dict[str, bool]) -> tuple[int, int]:
    """(addr, data) widths of the signals the wrapper actually wires to the
    memory's ports -- mirrors which of sram_addr/sram_addr_phys and
    sram_din/sram_din_phys each wrapper_template.j2 branch connects."""
    geo = _geometry(config)
    multi_port = len(_ports(config)) > 1
    if _is_shared_bus(config):
        widened_addr = flags["has_redundancy"]
        widened_data = flags["has_onchip_col_repair"]
    elif multi_port:
        widened_addr = flags["has_onchip_selfrepair"]
        widened_data = flags["has_onchip_col_repair"]
    else:
        widened_addr = flags["has_redundancy"]
        widened_data = flags["has_col_repair"]
    addr = geo["mem_addr_width"] if widened_addr else geo["addr_width"]
    data = geo["mem_data_width"] if widened_data else geo["data_width"]
    return addr, data


def render_memory_stub(config: dict[str, Any]) -> str:
    """Port-only ``(* blackbox *)`` model of the memory the wrapper instantiates,
    for synthesis only. Declares every parameter any wrapper branch may override
    (Yosys rejects an override of an undeclared blackbox parameter); port widths
    are literal, matching what the wrapper connects."""
    geo = _geometry(config)
    flags = _redundancy_flags(config)
    addr_w, data_w = _memory_connection_widths(config, flags)
    spare_w = max(1, geo["num_spare_cols"])

    decls: dict[str, str] = {}

    def add(name: str, decl: str) -> None:
        # One physical pin may serve the same role on several ports (a shared
        # clock) -- declare it once.
        decls.setdefault(name, decl)

    for pdata in _ports(config).values():
        ptype = pdata["type"]
        add(pdata["clk"], f"input  wire {pdata['clk']}")
        add(pdata["csb"], f"input  wire {pdata['csb']}")
        if ptype in ("w", "rw"):
            add(pdata["we"], f"input  wire {pdata['we']}")
        add(pdata["addr"], f"input  wire [{addr_w - 1}:0] {pdata['addr']}")
        if ptype in ("w", "rw"):
            add(pdata["din"], f"input  wire [{data_w - 1}:0] {pdata['din']}")
        if pdata.get("spare_wen"):
            add(pdata["spare_wen"], f"input  wire [{spare_w - 1}:0] {pdata['spare_wen']}")
        if ptype in ("r", "rw"):
            add(pdata["dout"], f"output wire [{data_w - 1}:0] {pdata['dout']}")

    lines = [
        "`timescale 1ns/1ps",
        "// Auto-generated by autombist. Port-only model of the memory for SYNTHESIS",
        "// ONLY -- read it with `read_verilog -lib` so the memory instance survives",
        "// flatten as a test boundary. Not a simulation model.",
        "(* blackbox *)",
        f"module {config['memory_name']} #(",
        f"    parameter integer ADDR_WIDTH = {geo['addr_width']},",
        f"    parameter integer DATA_WIDTH = {geo['data_width']},",
        f"    parameter integer NUM_SPARE_ROWS = {geo['num_spare_rows']},",
        f"    parameter integer NUM_SPARE_COLS = {geo['num_spare_cols']}",
        ") (",
        ",\n".join(f"    {d}" for d in decls.values()),
        ");",
        "endmodule",
        "",
    ]
    return "\n".join(lines)


def _memory_instances(config: dict[str, Any], stub_name: str) -> list[dict[str, Any]]:
    geo = _geometry(config)
    flags = _redundancy_flags(config)
    addr_w, data_w = _memory_connection_widths(config, flags)
    names = (
        [f"u_mem_{mem['name']}" for mem in config["memories"]]
        if _is_shared_bus(config)
        else ["u_sram"]
    )
    return [
        {
            "hierarchical_path": name,
            "instance_name": name,
            "module_type": str(config["memory_name"]),
            "category": "memory",
            "hierarchy_hint": "blackbox",
            "sources": [stub_name],
            "geometry": {**geo, "connected_addr_width": addr_w, "connected_data_width": data_w},
            "present_because": "always",
        }
        for name in names
    ]


def _instrument(
    path: str, name: str, module_type: str, category: str, why: str,
    parameters: dict[str, int], sources: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "hierarchical_path": path,
        "instance_name": name,
        "module_type": module_type,
        "category": category,
        "hierarchy_hint": "separate",
        "parameters": parameters,
        "sources": sources or [f"{module_type}.sv"],
        "present_because": why,
    }


def _controller_instance(config: dict[str, Any]) -> dict[str, Any]:
    geo = _geometry(config)
    algo_dir = _algo_dir(config)
    top = str(config.get("algo_top_module") or _normalize_algo(str(config.get("algo", "march-c")))[1])
    return _instrument(
        "u_algo_top", "u_algo_top", top, "mbist_controller", "always",
        {"ADDR_WIDTH": geo["addr_width"], "DATA_WIDTH": geo["data_width"], "READ_LATENCY": geo["read_latency"]},
        [f"{algo_dir}/{algo_dir}_{s}.sv" for s in ("algo", "fsm", "top")],
    )


def _analyzer(path_prefix: str, config: dict[str, Any], flags: dict[str, bool]) -> dict[str, Any]:
    geo = _geometry(config)
    if flags["has_onchip_col_repair"]:
        module_type = "onchip_2d_repair_analyzer"
        params = {
            "ADDR_WIDTH": geo["addr_width"], "DATA_WIDTH": geo["data_width"],
            "NUM_SPARE_ROWS": geo["num_spare_rows"], "NUM_SPARE_COLS": geo["num_spare_cols"],
        }
    else:
        module_type = "onchip_row_repair_analyzer"
        params = {"ADDR_WIDTH": geo["addr_width"], "NUM_SPARE_ROWS": geo["num_spare_rows"]}
    return _instrument(
        f"{path_prefix}u_onchip_analyzer", "u_onchip_analyzer", module_type,
        "self_repair", "redundancy.onchip_selfrepair", params,
    )


def _selfrepair_ctrl(path_prefix: str) -> dict[str, Any]:
    return _instrument(
        f"{path_prefix}u_onchip_selfrepair_ctrl", "u_onchip_selfrepair_ctrl", "onchip_selfrepair_ctrl",
        "self_repair", "redundancy.onchip_selfrepair", {},
    )


def _remap_row(path_prefix: str, name: str, config: dict[str, Any], why: str) -> dict[str, Any]:
    geo = _geometry(config)
    return _instrument(
        f"{path_prefix}{name}", name, "repair_remap_row", "repair_remap", why,
        {"ADDR_WIDTH": geo["addr_width"], "NUM_SPARE_ROWS": geo["num_spare_rows"]},
    )


def _remap_col(path_prefix: str, name: str, config: dict[str, Any], why: str) -> dict[str, Any]:
    geo = _geometry(config)
    return _instrument(
        f"{path_prefix}{name}", name, "repair_remap_col", "repair_remap", why,
        {"DATA_WIDTH": geo["data_width"], "NUM_SPARE_COLS": geo["num_spare_cols"]},
    )


def _dedicated_instances(config: dict[str, Any], flags: dict[str, bool]) -> list[dict[str, Any]]:
    instances: list[dict[str, Any]] = []
    if flags["has_onchip_selfrepair"]:
        instances.append(_analyzer("", config, flags))
        instances.append(_selfrepair_ctrl(""))
        if flags["has_onchip_diagnosis"]:
            geo = _geometry(config)
            instances.append(_instrument(
                "u_onchip_diagnosis", "u_onchip_diagnosis", "onchip_diagnosis_log",
                "diagnosis", "redundancy.onchip_diagnosis",
                {"ADDR_WIDTH": geo["addr_width"], "NUM_DIAGNOSIS_ENTRIES": geo["num_diagnosis_entries"]},
            ))
    instances.extend(_dedicated_repair_remap_instances(config, flags))
    return instances


def _dedicated_repair_remap_instances(config: dict[str, Any], flags: dict[str, bool]) -> list[dict[str, Any]]:
    if not flags["has_redundancy"]:
        return []

    ports = _ports(config)
    onchip = flags["has_onchip_selfrepair"]
    # wrapper_template.j2:485-678 (single-port) vs. :991-1179 (multi-port):
    # col-repair presence is gated by has_onchip_col_repair inside the
    # on-chip branch, by has_col_repair inside the tester-driven branch.
    col_repair = flags["has_onchip_col_repair"] if onchip else flags["has_col_repair"]

    if len(ports) <= 1:
        instances = [_remap_row("", "u_repair_remap", config, "redundancy.num_spare_rows")]
        if col_repair:
            instances.append(_remap_col("", "u_repair_remap_col", config, "redundancy.num_spare_cols"))
        return instances

    if not onchip:
        return []

    suffixes = _algo_port_suffixes(ports, str(config.get("algo", "march-c")))
    instances = [
        _remap_row("", f"u_repair_remap{s}", config, "redundancy.onchip_selfrepair")
        for s in suffixes.values()
    ]
    if col_repair:
        has_dual_rw = sorted(p.get("type") for p in ports.values()) == ["rw", "rw"]
        names = [f"u_repair_remap_col{s}" for s in suffixes.values()] if has_dual_rw else ["u_repair_remap_col"]
        instances.extend(_remap_col("", n, config, "redundancy.onchip_col_repair") for n in names)
    return instances


def _shared_bus_instances(config: dict[str, Any], flags: dict[str, bool]) -> list[dict[str, Any]]:
    if not flags["has_onchip_selfrepair"]:
        return []
    instances: list[dict[str, Any]] = []
    for i in range(_num_memories(config)):
        prefix = f"selfrepair_inst[{i}]."
        instances.append(_analyzer(prefix, config, flags))
        instances.append(_selfrepair_ctrl(prefix))
        instances.append(_remap_row(prefix, "u_repair_remap", config, "redundancy.onchip_selfrepair"))
        if flags["has_onchip_col_repair"]:
            instances.append(_remap_col(prefix, "u_repair_remap_col", config, "redundancy.onchip_col_repair"))
    return instances


def build_instances(config: dict[str, Any]) -> list[dict[str, Any]]:
    if config.get("use_saboteur"):
        raise ManifestError(
            "this output directory was generated with --test (fault-injection saboteur "
            "in place of the memory); the manifest describes the clean collar -- "
            "regenerate without --test"
        )
    flags = _redundancy_flags(config)
    instances = _memory_instances(config, f"{config['memory_name']}_bbox.v")
    instances.append(_controller_instance(config))
    if _is_shared_bus(config):
        instances.extend(_shared_bus_instances(config, flags))
    else:
        instances.extend(_dedicated_instances(config, flags))
    return instances


def synthesis_sources(config: dict[str, Any], module_outdir: Path) -> list[Path]:
    """The wrapper plus every test instrument's RTL (memory excluded -- it is a
    blackbox), deduplicated, in instantiation order."""
    module_outdir = Path(module_outdir)
    seen: dict[str, None] = {f"{_output_stem(config)}_mbist.v": None}
    for inst in build_instances(config):
        if inst["hierarchy_hint"] != "blackbox":
            for src in inst["sources"]:
                seen.setdefault(src, None)
    return [module_outdir / rel for rel in seen]


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
        "module_outdir": str(Path(module_outdir).resolve()),
        "sources": {
            "wrapper": f"{_output_stem(config)}_mbist.v",
            "blackbox_stub": f"{config['memory_name']}_bbox.v",
        },
        "instances": build_instances(config),
        "test_access": None,
    }


def write_instance_manifest(manifest: dict[str, Any], module_outdir: Path) -> Path:
    module_outdir = Path(module_outdir)
    module_outdir.mkdir(parents=True, exist_ok=True)
    path = module_outdir / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path


# warptap's own primitive modules (its rtl/*.v) -> manifest category. A scan mux
# is a per-instance specialized copy of scan_mux_cell, so it is recognized by its
# warptap_mux_name attribute instead of a fixed module name.
_WARPTAP_CATEGORIES = {
    "tap_core": "jtag_tap",
    "sib_cell": "ijtag_sib",
    "instrument_write": "ijtag_tdr",
    "bc1_shift_only": "ijtag_tdr",
}
_TDR_MODULE_BY_ROLE = {"control": "instrument_write", "status": "bc1_shift_only"}
JTAG_BOUNDARY_PORTS = ["tck", "tms", "tdi", "tdo", "trst_n"]


def build_test_access_block(
    manifest: dict[str, Any],
    enumerated: list[dict[str, Any]],
    wrapped_ports: list[dict[str, Any]],
    *,
    output_verilog: Path,
    output_dir: Path,
    icl_path: Path | None = None,
) -> dict[str, Any]:
    """The manifest's "test_access" block: a synthesis plan for the JTAG/IJTAG-wrapped
    netlist wrap-test-access wrote, built from ``enumerated``
    (testaccess.enumerate_test_access_instances on that netlist) and ``wrapped_ports``
    ({name, role, width} per wrapped control/status port).

    Every module the wrapped netlist uses is defined in ``output_verilog`` itself,
    already parameter-specialized by warptap's Yosys ingest (e.g.
    ``$paramod$<hash>\\march_c_top``), except blackboxed memories, which still need
    the base manifest's stub. So instances here carry the module name as it appears
    in that file and no parameters -- synthesize a "separate" block with
    ``hierarchy -top <module_type>`` straight from ``output_verilog``.
    """
    base = {i["hierarchical_path"]: i for i in manifest["instances"]}
    found = {e["instance"]: e for e in enumerated}

    instances = []
    for e in enumerated:
        entry: dict[str, Any] = {
            "hierarchical_path": e["instance"],
            "instance_name": e["instance"],
            "module_type": e["module_type"],
        }
        if e["module_type"] in _WARPTAP_CATEGORIES or e["mux_name"]:
            entry["category"] = _WARPTAP_CATEGORIES.get(e["module_type"], "ijtag_scan_mux")
            entry["hierarchy_hint"] = "separate"
            for key, field in (("sib_name", "sib_name"), ("instrument", "instrument_name"),
                               ("bit", "instrument_bit"), ("mux_name", "mux_name")):
                if e[field] is not None:
                    entry[key] = e[field]
        elif e["instance"] in base:
            entry["category"] = base[e["instance"]]["category"]
            entry["hierarchy_hint"] = base[e["instance"]]["hierarchy_hint"]
            if entry["hierarchy_hint"] == "blackbox":
                entry["sources"] = base[e["instance"]]["sources"]
        else:
            entry["category"] = "unknown"
            entry["hierarchy_hint"] = "separate"
        instances.append(entry)

    memories = [p for p, i in base.items() if i["category"] == "memory"]
    missing = [m for m in memories if m not in found]
    if missing:
        raise ManifestError(f"memory instance(s) {missing} not found in the wrapped netlist")
    memory_blackboxed = all(found[m]["is_blackbox"] for m in memories)

    sibs = {e["instrument_name"]: e for e in enumerated
            if e["module_type"] == "sib_cell" and e["instrument_name"]}
    instruments = []
    for port in wrapped_ports:
        sib = sibs.get(port["name"])
        if sib is None:
            raise ManifestError(f"no SIB found for wrapped port {port['name']!r}")
        bits = sorted(
            (e["instrument_bit"], e["instance"], e["module_type"]) for e in enumerated
            if e["sib_name"] == sib["sib_name"] and e["module_type"] in _TDR_MODULE_BY_ROLE.values()
        )
        expected = _TDR_MODULE_BY_ROLE[port["role"]]
        if [b for b, _, _ in bits] != list(range(port["width"])) or any(t != expected for _, _, t in bits):
            raise ManifestError(
                f"wrapped port {port['name']!r} ({port['role']}, width {port['width']}) does not "
                f"match its TDR cells in the netlist: {bits}"
            )
        instruments.append({
            "name": port["name"],
            "role": port["role"],
            "width": port["width"],
            "sib": sib["instance"],
            "tdr_bits": [inst for _, inst, _ in bits],
        })

    return {
        "wrapped": True,
        "top_module": manifest["top_module"],
        "output_verilog": str(Path(output_verilog).resolve()),
        "output_dir": str(Path(output_dir).resolve()),
        "icl_path": str(Path(icl_path).resolve()) if icl_path is not None else None,
        "boundary_ports": list(JTAG_BOUNDARY_PORTS),
        "memory_blackboxed": memory_blackboxed,
        "instances": instances,
        "instruments": instruments,
    }


def update_manifest_with_test_access(module_outdir: Path, test_access_block: dict[str, Any]) -> Path:
    """Patches an existing manifest.json's "test_access" key in place -- called
    after wrap-test-access actually runs. Raises ManifestError if generate
    --emit-manifest was never run for this output directory."""
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
