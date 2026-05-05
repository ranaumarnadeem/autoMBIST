from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

from autombist import __version__

from .generator import load_config
from .reporting import build_simulation_report, write_simulation_report, write_text_report


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


def _build_clean_command(*, hardware_dir: Path, module_outdir: Path, config: dict[str, Any], algo: str) -> list[str]:
    return [
        "make",
        "-C",
        str(hardware_dir),
        "SIM=icarus",
        f"OUTDIR={module_outdir.parent}",
        f"MEMORY_NAME={config['memory_name']}",
        f"WRAPPER_MODULE={config['wrapper_module_name']}",
        "USE_SABOTEUR=0",
        "FAULT_MODE=clean",
        "FAULTS=0",
        f"ADDR_WIDTH={config['addr_width']}",
        f"DATA_WIDTH={config['data_width']}",
        f"ALGO={algo}",
        f"PYTHON_BIN={sys.executable}",
        "sim",
    ]


def _build_fault_command(*, module_outdir: Path, verbose: bool) -> list[str]:
    return ["make", "-C", str(module_outdir), "debug" if verbose else "fault-test"]


def run_simulation(
    module_outdir: Path,
    *,
    verbose: bool = False,
) -> SimulationResult:
    start_time = time.time()
    config = _load_simulation_config(module_outdir)
    wrapper_path = module_outdir / f"{config['memory_name']}_mbist.v"

    if not wrapper_path.exists():
        raise SimulationError(
            f"Generated wrapper not found: {wrapper_path}. Run `autombist generate` first."
        )

    use_saboteur = bool(config.get("autombist_use_saboteur", False)) or (module_outdir / "Makefile").exists()
    if use_saboteur:
        saboteur_path = module_outdir / f"{config['memory_name']}_saboteur.v"
        if not saboteur_path.exists():
            raise SimulationError(
                f"Saboteur wrapper not found: {saboteur_path}. Run `autombist generate --test` first."
            )

    repo_root = Path(__file__).resolve().parents[2]
    hardware_dir = repo_root / "tests" / "hardware"

    algo = str(_get_run_metadata(config, "autombist_algo", "march-c"))
    fault_seed = _get_run_metadata(config, "autombist_fault_seed", None)
    faults = int(_get_run_metadata(config, "autombist_faults", 0))
    fault_type = str(_get_run_metadata(config, "autombist_fault_type", "stuck-at"))
    pulse_width_ns = int(_get_run_metadata(config, "autombist_pulse_width_ns", 2))

    if use_saboteur:
        command = _build_fault_command(module_outdir=module_outdir, verbose=verbose)
    else:
        command = _build_clean_command(
            hardware_dir=hardware_dir,
            module_outdir=module_outdir,
            config=config,
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

    backend_log_name = "fault_sim.log" if use_saboteur else "simulate.log"
    backend_log_path = module_outdir / backend_log_name
    backend_log_contents = ""
    if backend_log_path.exists():
        backend_log_contents = backend_log_path.read_text(encoding="utf-8")

    # If cocotb produced a JUnit XML (results.xml), extract its <system-out>
    # text and include it in the parser input so coverage metrics inside the
    # XML are discovered reliably.
    results_xml_path = hardware_dir / "results.xml"
    results_xml_contents = ""
    if results_xml_path.exists():
        try:
            import xml.etree.ElementTree as ET

            tree = ET.parse(str(results_xml_path))
            root = tree.getroot()
            # gather any system-out text nodes
            outs: list[str] = []
            for elem in root.iter():
                if elem.tag.endswith("system-out") and elem.text:
                    outs.append(elem.text)
            results_xml_contents = "\n".join(outs)
        except Exception:
            # best-effort: ignore XML problems and continue with plain logs
            results_xml_contents = ""

    reports_dir = module_outdir / "reports"
    runtime = time.time() - start_time

    # include the backend simulator log contents in the parser input so
    # coverage/injected metrics printed by cocotb are detected
    full_stdout = "".join(part for part in (completed.stdout, backend_log_contents, results_xml_contents) if part)

    report = build_simulation_report(
        tool_version=__version__,
        config=config,
        command=command,
        cwd=repo_root,
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
    )
    report_path = write_simulation_report(report, reports_dir)
    write_text_report(report, reports_dir, backend_log_contents)

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
        failure_hint = ""
        if "contains no child object named dbg_actual_word" in backend_log_contents:
            failure_hint = (
                " The generated saboteur wrapper looks stale or incompatible; run `autombist generate --test` again to refresh the output directory."
            )
        raise SimulationError(
            f"Simulation failed with exit code {completed.returncode}. See {log_path}.{failure_hint}"
        )

    return result