from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from autombist import __version__

from .generator import load_config
from .reporting import build_simulation_report, write_simulation_report


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


def _build_command(
    *,
    hardware_dir: Path,
    outdir: Path,
    config: dict[str, object],
    use_saboteur: bool,
    faults: int,
    fault_seed: int | None,
    fault_type: str,
    pulse_width_ns: int,
    algo: str,
) -> list[str]:
    command = [
        "make",
        "-C",
        str(hardware_dir),
        "SIM=icarus",
        f"OUTDIR={outdir}",
        f"MEMORY_NAME={config['memory_name']}",
        f"WRAPPER_MODULE={config['wrapper_module_name']}",
        f"USE_SABOTEUR={1 if use_saboteur else 0}",
        f"FAULT_MODE={'faults' if use_saboteur else 'clean'}",
        f"FAULTS={faults}",
        f"FAULT_TYPE={fault_type}",
        f"PULSE_WIDTH_NS={pulse_width_ns}",
        f"ADDR_WIDTH={config['addr_width']}",
        f"DATA_WIDTH={config['data_width']}",
        f"ALGO={algo}",
        f"PYTHON_BIN={sys.executable}",
    ]
    if fault_seed is not None:
        command.append(f"FAULT_SEED={fault_seed}")
    return command


def run_simulation(
    config_path: Path,
    outdir: Path,
    *,
    use_saboteur: bool = False,
    faults: int = 0,
    fault_seed: int | None = None,
    fault_type: str = "stuck-at",
    pulse_width_ns: int = 2,
    algo: str = "march-c",
) -> SimulationResult:
    start_time = time.time()
    config = load_config(config_path)
    module_outdir = outdir / config["memory_name"]
    wrapper_path = module_outdir / f"{config['memory_name']}_mbist.v"

    if not wrapper_path.exists():
        raise SimulationError(
            f"Generated wrapper not found: {wrapper_path}. Run `autombist generate` first."
        )

    if use_saboteur:
        saboteur_path = module_outdir / f"{config['memory_name']}_saboteur.v"
        if not saboteur_path.exists():
            raise SimulationError(
                f"Saboteur wrapper not found: {saboteur_path}. Run `autombist generate --test` first."
            )

    repo_root = Path(__file__).resolve().parents[2]
    hardware_dir = repo_root / "tests" / "hardware"
    command = _build_command(
        hardware_dir=hardware_dir,
        outdir=outdir,
        config=config,
        use_saboteur=use_saboteur,
        faults=faults,
        fault_seed=fault_seed,
        fault_type=fault_type,
        pulse_width_ns=pulse_width_ns,
        algo=algo,
    )

    completed = subprocess.run(
        command,
        cwd=repo_root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    log_path = module_outdir / "simulate.log"
    log_contents = "".join(part for part in (completed.stdout, completed.stderr) if part)
    log_path.write_text(log_contents, encoding="utf-8")

    reports_dir = module_outdir / "reports"
    runtime = time.time() - start_time

    report = build_simulation_report(
        tool_version=__version__,
        config=config,
        command=command,
        cwd=repo_root,
        log_path=log_path,
        report_path=reports_dir / "latest.json",
        returncode=completed.returncode,
        runtime_seconds=runtime,
        stdout=completed.stdout,
        stderr=completed.stderr,
        use_saboteur=use_saboteur,
        faults=faults,
        fault_seed=fault_seed,
        fault_type=fault_type,
        pulse_width_ns=pulse_width_ns,
        algo=algo,
    )
    report_path = write_simulation_report(report, reports_dir)

    result = SimulationResult(
        command=command,
        cwd=repo_root,
        log_path=log_path,
        report_path=report_path,
        returncode=completed.returncode,
        runtime_seconds=runtime,
        stdout=completed.stdout,
        stderr=completed.stderr,
        report=report,
    )

    if completed.returncode != 0:
        raise SimulationError(
            f"Simulation failed with exit code {completed.returncode}. See {log_path}."
        )

    return result