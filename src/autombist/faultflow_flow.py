"""Drive FaultFlow to grade the generated MBIST controller logic.

autoMBIST tests the memory *array* (March, cocotb). FaultFlow grades the MBIST
*controller logic*: its autoMBIST integration synthesizes the design from a
manifest (see manifest.py) -- each test instrument on its own, so Yosys can't
optimize it into the glue, and the memory a blackbox -- then scan stuck-at ATPG
runs over the result. FaultFlow treats the blackboxed memory's outputs as
unknown during the scan test, so a fault testable only through the memory is
reported as blackbox_unresolved and still counts against coverage.

This module only *emits* a self-contained, re-runnable bundle and (optionally)
runs it. FaultFlow is Unix-only and is invoked from its own venv. Everything a
run writes -- FaultFlow's synthesis, its campaign database and reports -- stays
inside the bundle; nothing is written into the FaultFlow checkout.
"""
from __future__ import annotations

import configparser
import io
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .generator import _render_template, generate_from_config, load_config
from .manifest import (
    MANIFEST_FILENAME,
    ManifestError,
    build_instance_manifest,
    build_instances,
    render_memory_stub,
    synthesis_sources,
)


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

# The bundle's FaultFlow working directory: ff.py writes output/<top>/ under it.
RUN_DIRNAME = "run"
# Where a --test build's clean collar is regenerated, inside the bundle.
CLEAN_DIRNAME = "clean"


@dataclass(slots=True)
class FaultFlowOptions:
    """Knobs for the controller-grading flow. All optional with sensible defaults."""

    repo: Path | None = None          # FaultFlow checkout; falls back to $FAULTFLOW_HOME
    cell_lib: str = "sky130"
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


def render_blackbox_stub(config: dict[str, Any]) -> str:
    """Render the port-only ``(* blackbox *)`` memory stub from the memory config."""
    return render_memory_stub(config)


def controller_sources(module_outdir: Path, config: dict[str, Any]) -> list[Path]:
    """Verilog sources that make up the controller: the wrapper plus every test
    instrument's RTL (algo top/fsm/algo, and any on-chip repair/diagnosis
    modules the config instantiates).

    Excludes the saboteur, the simulation SRAM model, and the real macro — those
    are not part of the synthesizable controller netlist FaultFlow grades.
    """
    try:
        return synthesis_sources(config, module_outdir)
    except ManifestError as exc:
        raise FaultFlowError(str(exc)) from exc


def memory_instances(config: dict[str, Any]) -> list[str]:
    """Instance names of the blackboxed memory (``u_sram``, or one
    ``u_mem_<name>`` per memory under topology: shared-bus)."""
    try:
        return [i["instance_name"] for i in build_instances(config) if i["category"] == "memory"]
    except ManifestError as exc:
        raise FaultFlowError(str(exc)) from exc


def grading_manifest(config: dict[str, Any], module_outdir: Path) -> dict[str, Any]:
    """The instance manifest FaultFlow synthesizes the bundle's design from.

    The same manifest `generate --emit-manifest` writes, rebuilt from the
    current config snapshot so it can never be stale, but with every source
    path absolute: the bundle keeps its own copy and leaves the output
    directory's manifest.json (owned by generate and wrap-test-access) alone.
    FaultFlow resolves a relative source against the manifest's directory and
    takes an absolute one as is.
    """
    root = Path(module_outdir).resolve()
    try:
        manifest = build_instance_manifest(
            config, root, tool_version=__version__, command="grade-controller",
        )
    except ManifestError as exc:
        raise FaultFlowError(str(exc)) from exc
    manifest["sources"] = {key: str(root / rel) for key, rel in manifest["sources"].items()}
    for inst in manifest["instances"]:
        inst["sources"] = [str(root / rel) for rel in inst["sources"]]
    return manifest


def build_grading_options(opts: FaultFlowOptions) -> str:
    """The grading knobs, as FaultFlow ``.ofs`` sections (INI,
    configparser-compatible). At run time they are merged over the .ofs
    FaultFlow's synthesis writes, which owns [design] and [blackbox]."""
    cp = configparser.ConfigParser()
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
    bundle: Path,
    liberty: Path,
    cell_json: Path,
    memory_instances: list[str],
    memory_name: str,
) -> str:
    context = {
        "repo": str(repo),
        "ff_python": ff_python,
        "yosys": yosys_bin,
        "top": top,
        "bundle": str(bundle),
        "liberty": str(liberty),
        "cell_json": str(cell_json),
        "run_dirname": RUN_DIRNAME,
        "memory_instances": " ".join(memory_instances),
        "memory_name": memory_name,
    }
    return _render_template(context, "run_faultflow_template.sh.j2")


def _readme(top: str, memory_instances: list[str]) -> str:
    return (
        "FaultFlow controller-grading bundle (auto-generated by autombist)\n"
        "================================================================\n\n"
        "Grades the MBIST controller logic for module '%s' with the memory\n"
        "blackboxed. Run on a Linux/WSL host that has Yosys and a built FaultFlow\n"
        "with its autoMBIST integration (faultflow.integrations.autombist):\n\n"
        "    FAULTFLOW_HOME=/path/to/faultflow bash run_faultflow.sh\n\n"
        "Optional overrides: FF_PYTHON=<faultflow venv python> YOSYS=<yosys path>\n\n"
        "Files:\n"
        "  manifest.json    what to synthesize: every instrument standalone, the\n"
        "                   memory (%s) a blackbox; sources are absolute paths\n"
        "  options.ofs      grading options, merged into FaultFlow's .ofs at run time\n"
        "  run_faultflow.sh FaultFlow synthesis + memory-survival check + scan\n"
        "                   insertion, scan-check and scan stuck-at ATPG\n\n"
        "  %s/            only for a --test build: the clean collar (real memory\n"
        "                   stub, no saboteur) regenerated from its config snapshot\n\n"
        "Written by a run:\n"
        "  synth/           FaultFlow's synthesis (composed netlist, its .ofs)\n"
        "  %s.ofs           the .ofs the run uses\n"
        "  %s/              FaultFlow's working directory (output/%s/ coverage reports)\n"
        % (top, ", ".join(memory_instances), CLEAN_DIRNAME, top, RUN_DIRNAME, top)
    )


def _clean_collar(
    module_outdir: Path, config: dict[str, Any], bundle: Path,
) -> tuple[Path, dict[str, Any]]:
    """The output directory and config of the clean collar a ``--test`` build
    stands for, regenerated inside the bundle.

    A --test build's memory instance is the fault-injection saboteur, and it
    has no memory stub. The controller RTL is the same either way, so the clean
    collar -- the one that goes on silicon -- is regenerated from the build's
    own config snapshot with the same algorithm and graded instead. This is
    what lets ``run --test --faultflow`` report array and controller coverage
    together.
    """
    clean_root = bundle / CLEAN_DIRNAME
    shutil.rmtree(clean_root, ignore_errors=True)
    wrapper = generate_from_config(
        module_outdir / "config.yml", clean_root, algo=str(config.get("algo", "march-c")),
    )
    return wrapper.parent, load_config(wrapper.parent / "config.yml")


def emit_bundle(module_outdir: Path, config: dict[str, Any], opts: FaultFlowOptions) -> Path:
    """Write the self-contained, re-runnable FaultFlow bundle. No tools required."""
    # Absolute paths throughout, so the bundle runs from any working directory.
    module_outdir = Path(module_outdir).resolve()
    repo = opts.resolved_repo()
    cell_json, liberty, _vmodels = opts.cell_lib_paths(repo)

    bundle = module_outdir / "faultflow"
    bundle.mkdir(parents=True, exist_ok=True)
    if config.get("use_saboteur"):
        module_outdir, config = _clean_collar(module_outdir, config, bundle)

    memory_name = str(config["memory_name"])
    top = str(config["wrapper_module_name"])
    mem_instances = memory_instances(config)

    (bundle / MANIFEST_FILENAME).write_text(
        json.dumps(grading_manifest(config, module_outdir), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (bundle / "options.ofs").write_text(build_grading_options(opts), encoding="utf-8")

    run_sh = bundle / "run_faultflow.sh"
    run_sh.write_text(
        build_run_script(
            repo=repo,
            ff_python=opts.resolved_ff_python(repo),
            yosys_bin=opts.yosys_bin,
            top=top,
            bundle=bundle,
            liberty=liberty,
            cell_json=cell_json,
            memory_instances=mem_instances,
            memory_name=memory_name,
        ),
        encoding="utf-8",
    )
    try:
        os.chmod(run_sh, 0o755)
    except OSError:
        pass

    (bundle / "README.txt").write_text(_readme(top, mem_instances), encoding="utf-8")
    return bundle


def read_coverage(workdir: Path, top: str) -> dict[str, Any]:
    """Parse FaultFlow's machine-readable coverage report, written under its
    working directory ``workdir``, into a normalized block."""
    output = Path(workdir) / "output" / top
    report_path = output / ".faultflow" / "intermediate" / "coverage_report.json"
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
        "redundant": summary.get("redundant"),
        "blackbox_unresolved": summary.get("blackbox_unresolved"),
        "excluded_blackbox": summary.get("excluded_blackbox"),
        "blackbox_instances": policy.get("blackbox_instances"),
        "blackbox_output_values": policy.get("blackbox_output_values"),
        "coverage_json": str(report_path),
        "coverage_rpt": str(output / "coverage.rpt"),
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

    return read_coverage(bundle / RUN_DIRNAME, str(config["wrapper_module_name"]))
