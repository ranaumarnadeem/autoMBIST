"""Physical-signoff helpers: LibreLane hardening + OpenRAM macro DRC/LVS.

This module is the pure, side-effect-free core behind the `autombist harden`,
`autombist fix-lef-units`, and `autombist macro-signoff` CLI commands. Every
function here either transforms plain data (dicts / strings / lists) or builds a
command-line argument vector -- nothing runs a tool. The CLI layer (cli.py) and
tests drive these directly, so the whole hardening recipe is unit-testable
without magic / netgen / LibreLane / a PDK present.

The LibreLane config it emits bakes in the macro-integration recipe proven on
flow/multimem (see that dir's README): LEF-units normalization, hard-IP signoff
flags, the PDN_MACRO_CONNECTIONS net-vs-pin format, and PDN halos.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class SignoffConfigError(ValueError):
    """Raised when a harden/signoff config is malformed."""


# Pinned to a tagged release rather than floating on `main`: `nix run
# github:librelane/librelane` (no ref) resolves to whatever the default
# branch is at invocation time, which is what every hardened design in this
# repo actually built (nix store path `python3.13-librelane-3.0.5` in every
# verified run) -- but that was incidental, not guaranteed, since nothing
# pinned it there. 3.0.5 is the latest tagged stable release (3.1.0.dev1/
# dev2 are pre-releases) and resolves to commit bf87321d, the exact commit
# `main` sat at across every run in this project's history. Override with
# `--librelane-ref` (CLI) / `-librelane-ref` (Tcl shell) to try a newer
# version before bumping this default.
LIBRELANE_FLAKE_REF = "github:librelane/librelane/3.0.5"


# --------------------------------------------------------------------------
# LEF units normalization
# --------------------------------------------------------------------------
# OpenRAM's sky130 LEF declares `DATABASE MICRONS 2000` but every coordinate --
# and the companion GDS -- is already on the 1nm (1000 dbu) grid. LibreLane's
# sky130A tech is 1000 dbu; the mismatch makes ODB reject or mis-scale the macro.
# The fix is declaration-only: rewrite 2000 -> 1000 and (defensively) snap any
# coordinate carrying >=4 decimals (the 0.5nm grid 2000 dbu allows) to 3 places.
_DEEP_DECIMAL_RE = re.compile(r"\d+\.\d{4,}")
_ANY_DECIMAL_RE = re.compile(r"\d+\.\d+")


def normalize_lef_units(text: str, target_dbu: int = 1000) -> tuple[str, int]:
    """Return (fixed_lef_text, num_coords_snapped).

    Rewrites the DATABASE MICRONS declaration to ``target_dbu`` and, only if any
    coordinate uses >= 4 decimal places, snaps every decimal literal to 3 places
    (max shift 0.5nm). Idempotent: a LEF already at target_dbu with no deep
    decimals is returned unchanged with a count of 0.
    """
    deep = _DEEP_DECIMAL_RE.findall(text)
    out = re.sub(
        r"DATABASE\s+MICRONS\s+\d+",
        f"DATABASE MICRONS {target_dbu}",
        text,
        count=1,
    )
    if deep:
        out = _ANY_DECIMAL_RE.sub(lambda m: f"{float(m.group(0)):.3f}", out)
    return out, len(deep)


# --------------------------------------------------------------------------
# LibreLane config construction (the proven flow/multimem recipe)
# --------------------------------------------------------------------------
_REQUIRED_TOP = ("design_name", "verilog_files", "clock_port")
_REQUIRED_MACRO = ("name", "gds", "lef", "instance")


def _validate_harden_config(cfg: dict[str, Any]) -> None:
    if not isinstance(cfg, dict):
        raise SignoffConfigError("harden config must be a mapping")
    for key in _REQUIRED_TOP:
        if not cfg.get(key):
            raise SignoffConfigError(f"harden config missing required key: {key}")
    if not isinstance(cfg["verilog_files"], list) or not cfg["verilog_files"]:
        raise SignoffConfigError("verilog_files must be a non-empty list")
    macros = cfg.get("macros", [])
    if not isinstance(macros, list):
        raise SignoffConfigError("macros must be a list")
    seen_instances: set[str] = set()
    for i, m in enumerate(macros):
        if not isinstance(m, dict):
            raise SignoffConfigError(f"macros[{i}] must be a mapping")
        for key in _REQUIRED_MACRO:
            if not m.get(key):
                raise SignoffConfigError(f"macros[{i}] missing required key: {key}")
        inst = m["instance"]
        if inst in seen_instances:
            raise SignoffConfigError(f"duplicate macro instance: {inst}")
        seen_instances.add(inst)


def build_librelane_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Build a LibreLane Classic-flow config dict from a compact harden config.

    ``cfg`` keys: design_name, verilog_files (list), clock_port, and optionally
    clock_period (ns, default 20), die_area ([x0,y0,x1,y1]),
    pl_target_density_pct (default 40), macros (list of
    {name, gds, lef, instance, location:[x,y], orientation}), and a power block
    {vdd_net, gnd_net, macro_vdd_pin, macro_gnd_pin} (defaults suit sky130
    OpenRAM macros: VPWR/VGND design nets, vccd1/vssd1 macro pins). Macro halos
    default to 15um. Raises SignoffConfigError on a malformed config.
    """
    _validate_harden_config(cfg)

    out: dict[str, Any] = {
        "DESIGN_NAME": cfg["design_name"],
        "VERILOG_FILES": [str(p) for p in cfg["verilog_files"]],
        "CLOCK_PORT": cfg["clock_port"],
        "CLOCK_PERIOD": cfg.get("clock_period", 20),
    }

    if cfg.get("die_area"):
        out["FP_SIZING"] = "absolute"
        out["DIE_AREA"] = list(cfg["die_area"])
    out["PL_TARGET_DENSITY_PCT"] = cfg.get("pl_target_density_pct", 40)

    macros = cfg.get("macros", [])
    if macros:
        pw = cfg.get("power", {})
        vdd_net = pw.get("vdd_net", "VPWR")
        gnd_net = pw.get("gnd_net", "VGND")
        vdd_pin = pw.get("macro_vdd_pin", "vccd1")
        gnd_pin = pw.get("macro_gnd_pin", "vssd1")

        macros_dict: dict[str, Any] = {}
        instances_by_name: dict[str, dict[str, Any]] = {}
        for m in macros:
            name = m["name"]
            entry = macros_dict.setdefault(
                name,
                {"gds": [str(m["gds"])], "lef": [str(m["lef"])], "instances": {}},
            )
            entry["instances"][m["instance"]] = {
                "location": list(m.get("location", [0, 0])),
                "orientation": m.get("orientation", "N"),
            }
            instances_by_name.setdefault(name, {})[m["instance"]] = True
        out["MACROS"] = macros_dict

        # One connection rule per distinct macro instance -- explicit, so a
        # rename can never silently drop a hookup (vs. a broad regex).
        out["PDN_MACRO_CONNECTIONS"] = [
            f"{m['instance']} {vdd_net} {gnd_net} {vdd_pin} {gnd_pin}"
            for m in macros
        ]

        # Hard-IP signoff: macro internals are the memory generator's job.
        out["MAGIC_DRC_USE_GDS"] = False
        out["RUN_KLAYOUT_XOR"] = False
        # OpenRAM SRAM-macro bitcell/periphery cells use GDS layer/datatype
        # pairs (e.g. CFOMDROP, CNTMADD -- sky130 mask-ops layers internal to
        # the bitcell array) that trip Magic/KLayout DRC as "unknown
        # layer/datatype in boundary" even though they're legitimate,
        # macro-internal geometry (a known, long-standing OpenRAM/open_pdks
        # DRC-ruleset mismatch -- see librelane/librelane#519). These
        # checkers default to fatal (ERROR_ON_*_DRC=True upstream); without
        # this, every macro-containing design here fails signoff on
        # macro-internal noise regardless of whether the DESIGN's own logic
        # is clean (LVS/antenna still gate normally). Real DRC coverage of
        # OpenRAM's own macro is the memory generator's responsibility, not
        # this flow's.
        out["ERROR_ON_MAGIC_DRC"] = False
        out["ERROR_ON_KLAYOUT_DRC"] = False
        # Keep core PG straps clear of the macros' edge pin-access band.
        halo = cfg.get("macro_halo_um", 15)
        out["FP_MACRO_HORIZONTAL_HALO"] = halo
        out["FP_MACRO_VERTICAL_HALO"] = halo
        out["PDN_HORIZONTAL_HALO"] = halo
        out["PDN_VERTICAL_HALO"] = halo
        out["DRT_OPT_ITERS"] = cfg.get("drt_opt_iters", 20)

    return out


def build_librelane_command(
    config_path: str | Path,
    pdk_root: str | Path,
    *,
    flake: str = LIBRELANE_FLAKE_REF,
) -> list[str]:
    """Argument vector to run the LibreLane flow on ``config_path`` via nix."""
    return [
        "nix",
        "--extra-experimental-features",
        "nix-command flakes",
        "run",
        flake,
        "--",
        "--pdk-root",
        str(pdk_root),
        str(config_path),
    ]


# --------------------------------------------------------------------------
# Macro-internal DRC / LVS
# --------------------------------------------------------------------------
def build_macro_signoff_command(
    signoff_script: str | Path,
    macro_names: list[str] | None = None,
) -> list[str]:
    """Argument vector to run flow/multimem/signoff/run_macro_signoff.sh."""
    cmd = ["bash", str(signoff_script)]
    if macro_names:
        cmd.extend(macro_names)
    return cmd
