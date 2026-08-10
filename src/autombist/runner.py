from __future__ import annotations

import re
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autombist import __version__

from .generator import load_config
from .reporting import build_simulation_report, write_simulation_report, write_text_report


def _find_hardware_dir() -> Path:
    """Locate the tests/hardware directory for simulation.
    
    Tries multiple approaches to support both development and installed package modes:
    1. Check relative to autombist package location (for installed packages)
    2. Check relative to source root (for development)
    3. Check in current working directory and parent directories
    
    Raises SimulationError if tests/hardware cannot be found.
    """
    candidates = []
    
    # Approach 1: Installed package mode - look in site-packages/autombist/tests/hardware
    package_root = Path(__file__).resolve().parent
    installed_hardware = package_root / "tests" / "hardware"
    candidates.append(installed_hardware)
    
    # Approach 2: Development mode - look relative to project root
    # parents[2] goes from src/autombist/runner.py to project root
    project_root = Path(__file__).resolve().parents[2]
    dev_hardware = project_root / "tests" / "hardware"
    candidates.append(dev_hardware)
    
    # Approach 3: Check current working directory and parents
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        candidates.append(parent / "tests" / "hardware")
    
    for candidate in candidates:
        if candidate.exists() and (candidate / "Makefile").exists():
            return candidate
    
    # Provide helpful error message
    raise SimulationError(
        f"Hardware simulation directory not found. Looked in:\n"
        f"  - {installed_hardware}\n"
        f"  - {dev_hardware}\n"
        f"\nMake sure 'tests' is included in your autombist installation, or set AUTOMBIST_HARDWARE_DIR environment variable."
    )


MAKEFILE_VAR_RE = re.compile(r"^(?P<key>[A-Z0-9_]+)\s*:?=\s*(?P<value>.*)$")


@dataclass(slots=True)
class SimulationResult:
    command: list[str]
    cwd: Path
    log_path: Path
    report_path: Path
    returncode: int
    runtime_seconds: float
    stdout: str
    stderr: str
    report: dict[str, Any]


class SimulationError(RuntimeError):
    """Raised when the simulation backend cannot be executed."""


def _get_run_metadata(config: dict[str, Any], key: str, default: Any) -> Any:
    value = config.get(key, default)
    return default if value is None else value


def _require_make() -> None:
    if shutil.which("make") is None:
        raise SimulationError(
            "'make' not found on PATH. autombist simulate/run need GNU Make, "
            "Icarus Verilog (iverilog/vvp), and the Python cocotb package "
            "(all provided by `nix develop`)."
        )


_MISSING_TOOL_MARKERS = (
    "command not found",
    "is not recognized as an internal or external command",
    "No such file or directory",
    "ModuleNotFoundError: No module named 'cocotb'",
    "ModuleNotFoundError: No module named cocotb",
)


def _looks_like_missing_tool(text: str) -> bool:
    return any(marker in text for marker in _MISSING_TOOL_MARKERS)


def _parse_makefile_metadata(module_outdir: Path) -> dict[str, str]:
    makefile_path = module_outdir / "Makefile"
    metadata: dict[str, str] = {}

    if not makefile_path.exists():
        return metadata

    for line in makefile_path.read_text(encoding="utf-8").splitlines():
        match = MAKEFILE_VAR_RE.match(line.strip())
        if not match:
            continue
        key = match.group("key")
        value = match.group("value").rstrip("\\").strip()
        if key in metadata:
            continue
        if "$(" in value:
            continue
        if value:
            metadata[key] = value

    return metadata


def _load_simulation_config(module_outdir: Path) -> dict[str, Any]:
    config_path = module_outdir / "config.yml"
    if config_path.exists():
        return load_config(config_path)

    makefile_metadata = _parse_makefile_metadata(module_outdir)
    wrapper_candidates = list(module_outdir.glob("*_mbist.v"))
    if not wrapper_candidates:
        raise FileNotFoundError(f"Generated wrapper not found in {module_outdir}. Run `autombist generate` first.")

    wrapper_path = wrapper_candidates[0]
    wrapper_name = wrapper_path.stem
    memory_name = wrapper_name.removesuffix("_mbist")
    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    we_active_low = bool(re.search(r"assign\s+sram_we\s*=\s*~selected_write_req", wrapper_text))

    return {
        "memory_name": makefile_metadata.get("MEMORY_NAME", memory_name),
        "wrapper_module_name": makefile_metadata.get("WRAPPER_MODULE", wrapper_name),
        "addr_width": int(makefile_metadata.get("ADDR_WIDTH", "4")),
        "data_width": int(makefile_metadata.get("DATA_WIDTH", "8")),
        "we_active_low": we_active_low,
        "autombist_use_saboteur": makefile_metadata.get("USE_SABOTEUR", "1") not in {"0", "false", "False"},
        "autombist_faults": int(makefile_metadata.get("FAULTS", "0")),
        "autombist_fault_seed": None if not makefile_metadata.get("FAULT_SEED") else int(makefile_metadata["FAULT_SEED"]),
        "autombist_fault_type": makefile_metadata.get("FAULT_TYPE", "stuck-at"),
        "autombist_pulse_width_ns": int(makefile_metadata.get("PULSE_WIDTH_NS", "2")),
        "autombist_algo": makefile_metadata.get("ALGO", "march-c"),
    }


def _build_clean_command(
    *,
    hardware_dir: Path,
    module_outdir: Path,
    config: dict[str, Any],
    algo: str,
    project_root: Path | None = None,
) -> list[str]:
    if project_root is None:
        project_root = Path(__file__).resolve().parents[2]
    
    return [
        "make",
        "-C",
        str(hardware_dir),
        f"PROJECT_ROOT={project_root}",
        "SIM=icarus",
        f"OUTDIR={module_outdir.parent}",
        f"MEMORY_NAME={config['memory_name']}",
        f"WRAPPER_MODULE={config['wrapper_module_name']}",
        "USE_SABOTEUR=0",
        "FAULT_MODE=clean",
        "FAULTS=0",
        f"ADDR_WIDTH={config['addr_width']}",
        f"DATA_WIDTH={config['data_width']}",
        f"READ_LATENCY={config.get('read_latency', 1)}",
        f"ALGO={algo}",
        f"PYTHON_BIN={sys.executable}",
        "sim",
    ]


def _build_fault_command(
    *,
    hardware_dir: Path,
    module_outdir: Path,
    config: dict[str, Any],
    faults: int,
    fault_seed: int | None,
    fault_type: str,
    pulse_width_ns: int,
    algo: str,
    project_root: Path | None = None,
    fail_scan: bool = False,
) -> list[str]:
    if project_root is None:
        project_root = Path(__file__).resolve().parents[2]

    # fail_scan swaps the run into the opt-in functional fail-scan mode: the
    # saboteur still injects its faults, but instead of the march detection test
    # (FAULT_MODE=faults) only test_mbist.test_fail_scan runs -- probing every
    # cell through the functional port and emitting the ungated fail-bitmap
    # (FAIL_CELL lines) a BIRA step consumes. Every other cocotb test
    # early-returns for FAULT_MODE=failscan, so exactly one runs.
    fault_mode = "failscan" if fail_scan else "faults"

    command = [
        "make",
        "-C",
        str(hardware_dir),
        f"PROJECT_ROOT={project_root}",
        "SIM=icarus",
        f"OUTDIR={module_outdir.parent}",
        f"MEMORY_NAME={config['memory_name']}",
        f"WRAPPER_MODULE={config['wrapper_module_name']}",
        "USE_SABOTEUR=1",
        f"FAULT_MODE={fault_mode}",
        f"FAULTS={faults}",
        f"FAULT_TYPE={fault_type}",
        f"PULSE_WIDTH_NS={pulse_width_ns}",
        f"ADDR_WIDTH={config['addr_width']}",
        f"DATA_WIDTH={config['data_width']}",
        f"READ_LATENCY={config.get('read_latency', 1)}",
        f"ALGO={algo}",
        f"PYTHON_BIN={sys.executable}",
        "sim",
    ]
    if fault_seed is not None:
        command.append(f"FAULT_SEED={fault_seed}")
    return command


def run_simulation(
    module_outdir: Path,
    *,
    verbose: bool = False,
    fail_scan: bool = False,
    extra_make_vars: dict[str, str] | None = None,
) -> SimulationResult:
    """Run the generated MBIST simulation and build its JSON report.

    ``fail_scan`` (opt-in) runs the functional-port fail scan instead of the
    march detection test: it probes every cell and attaches an observation-
    derived ``fail_bitmap`` to the report (the input a redundancy-analysis step
    consumes). It requires the saboteur build (``use_saboteur``); on a clean,
    no-saboteur build it is ignored. Default False keeps every existing caller
    byte-identical.

    ``extra_make_vars`` (opt-in) appends arbitrary ``KEY=VALUE`` overrides to the
    make command -- used by the redundancy repair-row test to select its cocotb
    module (``COCOTB_TEST_MODULES=test_repair_row``) and phase
    (``REPAIR_PHASE=off|on``). Default None keeps every existing caller's command
    line byte-identical. A redundancy build additionally auto-adds
    ``USE_REPAIR_REMAP=1`` so the external remap RTL is compiled in.
    """
    start_time = time.time()
    config = _load_simulation_config(module_outdir)
    wrapper_path = module_outdir / f"{config['memory_name']}_mbist.v"

    if not wrapper_path.exists():
        raise SimulationError(
            f"Generated wrapper not found: {wrapper_path}. Run `autombist generate` first."
        )

    config_declares_saboteur = "autombist_use_saboteur" in config
    if config_declares_saboteur:
        use_saboteur = bool(config.get("autombist_use_saboteur"))
    else:
        # Backward compatibility for legacy outputs that did not snapshot metadata.
        use_saboteur = (module_outdir / "Makefile").exists()
    if use_saboteur:
        saboteur_path = module_outdir / f"{config['memory_name']}_saboteur.v"
        if not saboteur_path.exists():
            raise SimulationError(
                f"Saboteur wrapper not found: {saboteur_path}. Run `autombist generate --test` first."
            )

    _require_make()

    hardware_dir = _find_hardware_dir()
    repo_root = Path(__file__).resolve().parents[2]

    algo = str(_get_run_metadata(config, "autombist_algo", "march-c"))
    fault_seed = _get_run_metadata(config, "autombist_fault_seed", None)
    faults = int(_get_run_metadata(config, "autombist_faults", 0))
    fault_type = str(_get_run_metadata(config, "autombist_fault_type", "stuck-at"))
    pulse_width_ns = int(_get_run_metadata(config, "autombist_pulse_width_ns", 2))

    # Determine PROJECT_ROOT for the makefile
    # Try dev mode first, then fallback to hardware_dir parent
    dev_project_root = Path(__file__).resolve().parents[2]
    if (dev_project_root / "tests" / "hardware").exists():
        project_root = dev_project_root
    else:
        # Installed mode: use a sensible default (hardware dir or cwd)
        project_root = Path.cwd()

    if use_saboteur:
        command = _build_fault_command(
            hardware_dir=hardware_dir,
            module_outdir=module_outdir,
            config=config,
            faults=faults,
            fault_seed=fault_seed,
            fault_type=fault_type,
            pulse_width_ns=pulse_width_ns,
            algo=algo,
            project_root=project_root,
            fail_scan=fail_scan,
        )
    else:
        command = _build_clean_command(
            hardware_dir=hardware_dir,
            module_outdir=module_outdir,
            config=config,
            algo=algo,
            project_root=project_root,
        )

    # Redundancy builds add the external row-repair remap RTL to the compile.
    if config.get("redundancy"):
        command.append("USE_REPAIR_REMAP=1")
    # Caller-supplied Make overrides (e.g. COCOTB_TEST_MODULES / REPAIR_PHASE for
    # the repair-row test). Appended as KEY=VALUE; make treats them as variable
    # overrides regardless of position relative to the `sim` goal.
    if extra_make_vars:
        for key, value in extra_make_vars.items():
            command.append(f"{key}={value}")

    completed = subprocess.run(
        command,
        cwd=project_root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    log_path = module_outdir / "simulate.log"
    log_contents = "".join(part for part in (completed.stdout, completed.stderr) if part)
    log_path.write_text(log_contents, encoding="utf-8")

    backend_log_name = "fault_sim.log" if use_saboteur else "simulate.log"
    backend_log_path = module_outdir / backend_log_name
    backend_log_contents = ""
    if backend_log_path.exists():
        backend_log_contents = backend_log_path.read_text(encoding="utf-8")

    results_xml_path = module_outdir / "results.xml"

    reports_dir = module_outdir / "reports"
    runtime = time.time() - start_time

    full_stdout = "".join(part for part in (completed.stdout, backend_log_contents) if part)

    report = build_simulation_report(
        tool_version=__version__,
        config=config,
        command=command,
        cwd=project_root,
        log_path=log_path,
        report_path=reports_dir / "latest.json",
        returncode=completed.returncode,
        runtime_seconds=runtime,
        stdout=full_stdout,
        stderr=completed.stderr,
        use_saboteur=use_saboteur,
        faults=faults,
        fault_seed=fault_seed,
        fault_type=fault_type,
        pulse_width_ns=pulse_width_ns,
        algo=algo,
        results_xml_path=results_xml_path,
    )
    report_path = write_simulation_report(report, reports_dir)
    write_text_report(report, reports_dir, backend_log_contents)

    result = SimulationResult(
        command=command,
        cwd=project_root,
        log_path=log_path,
        report_path=report_path,
        returncode=completed.returncode,
        runtime_seconds=runtime,
        stdout=completed.stdout,
        stderr=completed.stderr,
        report=report,
    )

    if completed.returncode != 0:
        failure_hint = ""
        if "contains no child object named dbg_actual_word" in backend_log_contents:
            failure_hint = (
                " The generated saboteur wrapper looks stale or incompatible; run `autombist generate --test` again to refresh the output directory."
            )
        elif "MBIST reported fail in clean mode" in backend_log_contents:
            failure_hint = (
                " A clean (fault-free) run should never fail MBIST; a mismatched "
                "read_latency is the first thing to check -- it makes the "
                "controller sample dout at the wrong moment and report failures "
                "on a perfectly good memory (see docs/source/configuration.md#read-latency)."
            )
        elif _looks_like_missing_tool(backend_log_contents) or _looks_like_missing_tool(completed.stderr):
            failure_hint = (
                " This looks like a missing simulation tool: simulate/run need "
                "Icarus Verilog (iverilog/vvp) and the Python cocotb package on "
                "PATH/installed (both provided by `nix develop`)."
            )
        raise SimulationError(
            f"Simulation failed with exit code {completed.returncode}. See {log_path}.{failure_hint}"
        )

    return result


def run_controller_grading(
    module_outdir: Path,
    opts: Any,
    *,
    run: bool = True,
) -> dict[str, Any] | None:
    """Emit (and optionally run) the FaultFlow controller-grading bundle.

    Returns the normalized coverage block when run, else ``None``. Separate from
    ``run_simulation`` so array-test simulation and structural controller grading
    are independently invocable.
    """
    from .faultflow_flow import grade_controller

    return grade_controller(module_outdir, opts, run=run)
