"""Drive FaultFlow to grade the generated MBIST controller logic.

autoMBIST tests the memory *array* (March, cocotb). FaultFlow grades the MBIST
*controller logic*: we synthesize the clean collar with the SRAM macro replaced by
a port-only ``(* blackbox *)`` stub (so the ``u_sram`` instance survives Yosys
``flatten``), then point FaultFlow at the netlist with ``[blackbox] instances =
u_sram``. FaultFlow turns that boundary into pseudo-PI/PO and runs scan stuck-at
ATPG over the controller, excluding the (blackboxed) memory from the denominator.

This module only *emits* a self-contained, re-runnable bundle and (optionally) runs
it. FaultFlow is Unix-only and is invoked from its own venv.
"""
from __future__ import annotations

import configparser
import io
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .generator import _normalize_algo, _render_template, load_config


class FaultFlowError(RuntimeError):
    """Raised when the FaultFlow controller-grading flow cannot be prepared or run."""


# cell_lib name -> (cell-map JSON, liberty, behavioral verilog), relative to the repo.
_CELL_LIBS: dict[str, tuple[str, str, str]] = {
    "sky130": (
        "cells/sky130/sky130_fd_sc_hd.json",
        "cells/sky130/sky130_fd_sc_hd__tt_025C_1v80.lib",
        "cells/sky130/sky130_fd_sc_hd.v",
    ),
    "osu035": (
        "cells/osu/osu035.json",
        "cells/osu/osu035_stdcells.lib",
        "cells/osu/osu035_stdcells.v",
    ),
}


@dataclass(slots=True)
class FaultFlowOptions:
    """Knobs for the controller-grading flow. All optional with sensible defaults."""

    repo: Path | None = None          # FaultFlow checkout; falls back to $FAULTFLOW_HOME
    cell_lib: str = "sky130"
    sram_instance: str = "u_sram"     # instance label hard-coded in wrapper_template.j2
    scan_chains: int = 1
    threshold: float = 90.0
    max_rounds: int = 20
    fault_model: str = "stuck_at"
    ff_python: str | None = None      # defaults to <repo>/venv/bin/python (FaultFlow's own venv)
    yosys_bin: str = "yosys"

    def resolved_repo(self) -> Path:
        repo = self.repo or os.environ.get("FAULTFLOW_HOME")
        if not repo:
            raise FaultFlowError(
                "FaultFlow repo not set. Pass --faultflow-repo PATH or set FAULTFLOW_HOME."
            )
        repo = Path(repo)
        if not repo.exists():
            raise FaultFlowError(f"FaultFlow repo not found: {repo}")
        return repo.resolve()

    def resolved_ff_python(self, repo: Path) -> str:
        if self.ff_python:
            return self.ff_python
        candidate = repo / "venv" / "bin" / "python"
        return str(candidate) if candidate.exists() else "python3"

    def cell_lib_paths(self, repo: Path) -> tuple[Path, Path, Path]:
        try:
            rel_json, rel_lib, rel_v = _CELL_LIBS[self.cell_lib]
        except KeyError:
            raise FaultFlowError(
                f"Unknown cell_lib '{self.cell_lib}'. Choose one of: {', '.join(_CELL_LIBS)}"
            )
        return repo / rel_json, repo / rel_lib, repo / rel_v


def _algo_dir(config: dict[str, Any]) -> str:
    algo_dir = config.get("algo_dir")
    if algo_dir:
        return str(algo_dir)
    return _normalize_algo(str(config.get("algo", "march-c")))[0]


def _sh_quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def render_blackbox_stub(config: dict[str, Any]) -> str:
    """Render the port-only ``(* blackbox *)`` SRAM stub from the memory config."""
    return _render_template(config, "sram_blackbox_template.j2")


def controller_sources(module_outdir: Path, config: dict[str, Any]) -> list[Path]:
    """Verilog sources that make up the controller (collar + algo RTL).

    Excludes the saboteur, the simulation SRAM model, and the real macro — those
    are not part of the synthesizable controller netlist FaultFlow grades.
    """
    memory_name = str(config["memory_name"])
    algo_dir = _algo_dir(config)
    algo_path = module_outdir / algo_dir
    sources = [module_outdir / f"{memory_name}_mbist.v"]
    for suffix in ("algo", "fsm", "top"):
        sources.append(algo_path / f"{algo_dir}_{suffix}.sv")
    return sources


def build_synth_script(
    *,
    sources: list[Path],
    stub: Path,
    top: str,
    liberty: Path,
    json_out: Path,
    gate_out: Path,
) -> str:
    """A Yosys script mirroring FaultFlow's own recipe, plus a `-lib` blackbox stub."""
    src_tokens = " ".join(_sh_quote(str(s)) for s in sources)
    return "\n".join(
        [
            f"read_verilog -sv {src_tokens}",
            f"read_verilog -lib {_sh_quote(str(stub))}",
            f"hierarchy -check -top {top}",
            "proc",
            "flatten",
            "opt_expr",
            "opt_clean",
            f"synth -top {top}",
            f"dfflibmap -liberty {_sh_quote(str(liberty))}",
            f"abc -liberty {_sh_quote(str(liberty))}",
            "clean",
            f"write_json {_sh_quote(str(json_out))}",
            f"write_verilog {_sh_quote(str(gate_out))}",
            "",
        ]
    )


def build_ofs(
    *,
    netlist: Path,
    top: str,
    cell_json: Path,
    liberty: Path,
    verilog_models: Path,
    opts: FaultFlowOptions,
) -> str:
    """Render the FaultFlow ``.ofs`` config (INI, configparser-compatible)."""
    cp = configparser.ConfigParser()
    cp["design"] = {
        "netlist": str(netlist),
        "top": top,
        "cell_lib": str(cell_json),
        "liberty": str(liberty),
        "verilog_models": str(verilog_models),
    }
    cp["blackbox"] = {"instances": opts.sram_instance}
    cp["fault_model"] = {"model": opts.fault_model, "collapsing": "false"}
    cp["atpg"] = {"tool": "native", "mode": "comb", "max_rounds": str(opts.max_rounds)}
    cp["scan"] = {
        "chains": str(opts.scan_chains),
        "scan_in": "scan_in",
        "scan_out": "scan_out",
        "scan_enable": "scan_en",
        "run_techmap": "true",
    }
    cp["report"] = {"output": "coverage.rpt", "threshold": str(opts.threshold)}
    cp["simulation"] = {"unsupported_cells": "fail", "verify": "false"}
    buf = io.StringIO()
    cp.write(buf)
    return buf.getvalue()


def build_run_script(
    *,
    repo: Path,
    ff_python: str,
    yosys_bin: str,
    top: str,
    synth: Path,
    json_out: Path,
    ofs: Path,
    sram_instance: str,
    memory_name: str,
) -> str:
    context = {
        "repo": str(repo),
        "ff_python": ff_python,
        "yosys": yosys_bin,
        "top": top,
        "synth": str(synth),
        "json_out": str(json_out),
        "ofs": str(ofs),
        "sram_instance": sram_instance,
        "memory_name": memory_name,
    }
    return _render_template(context, "run_faultflow_template.sh.j2")


def _readme(top: str) -> str:
    return (
        "FaultFlow controller-grading bundle (auto-generated by autombist)\n"
        "================================================================\n\n"
        "Grades the MBIST controller logic for module '%s' with the SRAM macro\n"
        "blackboxed. Run on a Linux/WSL host that has Yosys and a built FaultFlow:\n\n"
        "    FAULTFLOW_HOME=/path/to/faultflow bash run_faultflow.sh\n\n"
        "Optional overrides: FF_PYTHON=<faultflow venv python> YOSYS=<yosys path>\n\n"
        "Files:\n"
        "  %s_bbox.v        port-only (* blackbox *) SRAM stub\n"
        "  synth_collar.ys  Yosys script (collar+algo -> <top>.json, u_sram kept)\n"
        "  %s.ofs           FaultFlow config ([blackbox] instances=u_sram)\n"
        "  run_faultflow.sh synth + u_sram-survival assertion + scan stuck-at ATPG\n"
        % (top, top.removesuffix("_mbist"), top)
    )


def emit_bundle(module_outdir: Path, config: dict[str, Any], opts: FaultFlowOptions) -> Path:
    """Write the self-contained, re-runnable FaultFlow bundle. No tools required."""
    # Absolute paths throughout: run_faultflow.sh cd's into $FAULTFLOW_HOME before
    # invoking ff.py, so every path it references (ofs, netlist, synth script) must
    # be absolute, not relative to the autombist working directory.
    module_outdir = Path(module_outdir).resolve()
    repo = opts.resolved_repo()
    cell_json, liberty, vmodels = opts.cell_lib_paths(repo)

    memory_name = str(config["memory_name"])
    top = str(config["wrapper_module_name"])

    bundle = module_outdir / "faultflow"
    bundle.mkdir(parents=True, exist_ok=True)

    stub = bundle / f"{memory_name}_bbox.v"
    stub.write_text(render_blackbox_stub(config), encoding="utf-8")

    json_out = bundle / f"{top}.json"
    gate_out = bundle / f"{top}_gate.v"
    synth = bundle / "synth_collar.ys"
    synth.write_text(
        build_synth_script(
            sources=controller_sources(module_outdir, config),
            stub=stub,
            top=top,
            liberty=liberty,
            json_out=json_out,
            gate_out=gate_out,
        ),
        encoding="utf-8",
    )

    ofs = bundle / f"{top}.ofs"
    ofs.write_text(
        build_ofs(
            netlist=json_out,
            top=top,
            cell_json=cell_json,
            liberty=liberty,
            verilog_models=vmodels,
            opts=opts,
        ),
        encoding="utf-8",
    )

    run_sh = bundle / "run_faultflow.sh"
    run_sh.write_text(
        build_run_script(
            repo=repo,
            ff_python=opts.resolved_ff_python(repo),
            yosys_bin=opts.yosys_bin,
            top=top,
            synth=synth,
            json_out=json_out,
            ofs=ofs,
            sram_instance=opts.sram_instance,
            memory_name=memory_name,
        ),
        encoding="utf-8",
    )
    try:
        os.chmod(run_sh, 0o755)
    except OSError:
        pass

    (bundle / "README.txt").write_text(_readme(top), encoding="utf-8")
    return bundle


def read_coverage(repo: Path, top: str) -> dict[str, Any]:
    """Parse FaultFlow's machine-readable coverage report into a normalized block."""
    report_path = (
        repo / "output" / top / ".faultflow" / "intermediate" / "coverage_report.json"
    )
    if not report_path.exists():
        raise FaultFlowError(f"FaultFlow coverage report not found: {report_path}")
    data = json.loads(report_path.read_text(encoding="utf-8"))
    summary = data.get("summary", {})
    policy = data.get("policy", {})
    return {
        "tool": "faultflow",
        "coverage_percent": summary.get("coverage_percent"),
        "test_coverage_percent": summary.get("test_coverage_percent"),
        "fault_coverage_percent": summary.get("fault_coverage_percent"),
        "detected": summary.get("detected"),
        "undetected": summary.get("undetected"),
        "denominator": summary.get("denominator"),
        "excluded_blackbox": summary.get("excluded_blackbox"),
        "blackbox_instances": policy.get("blackbox_instances"),
        "coverage_json": str(report_path),
        "coverage_rpt": str(repo / "output" / top / "coverage.rpt"),
    }


def grade_controller(
    module_outdir: Path,
    opts: FaultFlowOptions,
    *,
    run: bool = True,
) -> dict[str, Any] | None:
    """Emit the bundle and, unless ``run`` is False, execute it and return coverage."""
    config = load_config(module_outdir / "config.yml")
    bundle = emit_bundle(module_outdir, config, opts)

    if not run:
        return None

    run_sh = bundle / "run_faultflow.sh"
    completed = subprocess.run(
        ["bash", str(run_sh)],
        capture_output=True,
        text=True,
        check=False,
    )
    (bundle / "run.log").write_text(
        "".join(part for part in (completed.stdout, completed.stderr) if part),
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise FaultFlowError(
            f"FaultFlow grading failed (exit {completed.returncode}). See {bundle / 'run.log'}."
        )

    repo = opts.resolved_repo()
    return read_coverage(repo, str(config["wrapper_module_name"]))
