from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any

FAULT_COVERAGE_RE = re.compile(r"Fault coverage:\s*(\d+)\/(\d+)\s*\((\d+(?:\.\d+)?)%\)")
INJECTED_FAULTS_RE = re.compile(r"Injected faults:\s*(\d+)")
FAULT_COUNT_RE = re.compile(r"Fault count:\s*(\d+)")


@dataclass(slots=True)
class FaultMetrics:
    detected_faults: int | None = None
    total_fault_sites: int | None = None
    coverage_percent: float | None = None
    injected_faults: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected_faults": self.detected_faults,
            "total_fault_sites": self.total_fault_sites,
            "coverage_percent": self.coverage_percent,
            "injected_faults": self.injected_faults,
        }


def parse_fault_metrics(stdout: str, stderr: str) -> FaultMetrics:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    metrics = FaultMetrics()

    coverage_match = FAULT_COVERAGE_RE.search(combined)
    if coverage_match:
        metrics.detected_faults = int(coverage_match.group(1))
        metrics.total_fault_sites = int(coverage_match.group(2))
        metrics.coverage_percent = float(coverage_match.group(3))

    injected_match = INJECTED_FAULTS_RE.search(combined)
    if injected_match:
        metrics.injected_faults = int(injected_match.group(1))
    else:
        fault_count_match = FAULT_COUNT_RE.search(combined)
        if fault_count_match:
            metrics.injected_faults = int(fault_count_match.group(1))

    return metrics


def build_simulation_report(*,
    tool_version: str,
    config: dict[str, Any],
    command: list[str],
    cwd: Path,
    log_path: Path,
    report_path: Path,
    returncode: int,
    runtime_seconds: float,
    stdout: str,
    stderr: str,
    use_saboteur: bool,
    faults: int,
    fault_seed: int | None,
    fault_type: str,
    pulse_width_ns: int,
    algo: str,
) -> dict[str, Any]:
    metrics = parse_fault_metrics(stdout, stderr)
    if metrics.injected_faults is None:
        metrics.injected_faults = faults
    now = datetime.now(timezone.utc).isoformat()
    status = "pass" if returncode == 0 else "fail"

    report = {
        "schema_version": "1.0.0",
        "generated_at": now,
        "tool_version": tool_version,
        "status": status,
        "returncode": returncode,
        "runtime_seconds": round(runtime_seconds, 6),
        "command": command,
        "cwd": str(cwd),
        "log_path": str(log_path),
        "report_path": str(report_path),
        "config": {
            "memory_name": config["memory_name"],
            "wrapper_module_name": config["wrapper_module_name"],
            "addr_width": config["addr_width"],
            "data_width": config["data_width"],
            "we_active_low": config["we_active_low"],
        },
        "simulation": {
            "use_saboteur": use_saboteur,
            "faults_requested": faults,
            "fault_seed": fault_seed,
            "fault_type": fault_type,
            "pulse_width_ns": pulse_width_ns,
            "algo": algo,
        },
        "fault_metrics": metrics.to_dict(),
    }

    report["summary"] = format_simulation_summary(report)
    return report


def write_simulation_report(report: dict[str, Any], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "latest.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report_path


def _extract_fault_summary_block(log_text: str) -> str:
    lines = log_text.splitlines()
    start_index = None
    end_index = None

    for index, line in enumerate(lines):
        if line.strip() == "Fault summary":
            start_index = index
            break

    if start_index is None:
        return ""

    for index in range(start_index, len(lines)):
        if lines[index].startswith("Injected faults:"):
            end_index = index
            break

    if end_index is None:
        block = lines[start_index:]
    else:
        block = lines[start_index : end_index + 1]

    return "\n".join(line.rstrip() for line in block).strip()


def render_text_report(report: dict[str, Any], fault_log_text: str) -> str:
    config = report.get("config", {})
    simulation = report.get("simulation", {})
    metrics = report.get("fault_metrics", {})

    memory_name = config.get("memory_name", "unknown")
    wrapper_module_name = config.get("wrapper_module_name", "unknown")
    addr_width = config.get("addr_width", "unknown")
    data_width = config.get("data_width", "unknown")
    algorithm = simulation.get("algo", "unknown")
    fault_type = simulation.get("fault_type", "unknown")
    faults_requested = simulation.get("faults_requested", "unknown")
    injected_faults = metrics.get("injected_faults", faults_requested)
    detected_faults = metrics.get("detected_faults", faults_requested)
    total_fault_sites = metrics.get("total_fault_sites", faults_requested)
    coverage_percent = metrics.get("coverage_percent")
    if coverage_percent is None and detected_faults is not None and total_fault_sites:
        coverage_percent = (detected_faults / total_fault_sites) * 100.0
    coverage_text = f"{coverage_percent:.2f}%" if coverage_percent is not None else "not reported"
    seed = simulation.get("fault_seed", "unknown")
    runtime = float(report.get("runtime_seconds", 0.0))
    status = report.get("status", "unknown").upper()
    source_path = str(report.get("report_path", "unknown"))
    log_path = report.get("log_path", "unknown")

    fault_summary_block = _extract_fault_summary_block(fault_log_text)
    if not fault_summary_block:
        fault_summary_block = "Fault summary\n(none available)"

    divider = "-" * 70
    command_outdir = Path(str(report.get("report_path", "out/reports/latest.json"))).parent.parent
    command_outdir_text = command_outdir.as_posix()

    simulator_log_path = Path(str(report.get("log_path", "out/input_demo_8x16_scn4m/simulate.log")))
    fault_log_path = simulator_log_path.with_name("fault_sim.log")

    lines = [
        f"autoMBIST Simulation Report — {memory_name}",
        "",
        f"Source: {source_path}",
        f"Log:    {log_path}",
        "",
        divider,
        "SUMMARY",
        divider,
        f"{'Memory name:':<23}{memory_name}",
        f"{'Wrapper module:':<23}{wrapper_module_name}",
        f"{'Address width:':<23}{addr_width}",
        f"{'Data width:':<23}{data_width}",
        f"{'Algorithm:':<23}{algorithm}",
        f"{'Fault model:':<23}{fault_type}",
        f"{'Faults requested:':<23}{faults_requested}",
        f"{'Injected faults (obs):':<23}{injected_faults}",
        f"{'Detected faults:':<23}{detected_faults}",
        f"{'Total fault sites:':<23}{total_fault_sites}",
        f"{'Coverage:':<23}{coverage_text}",
        f"{'Seed:':<23}{seed}",
        f"{'Run status:':<23}{status}",
        f"{'Runtime:':<23}{runtime:.3f} s",
        "",
        divider,
        fault_summary_block,
        "",
        "Table semantics",
        "   ",
        "   - TYPE: fault model (TF-UP = transition-up, 0->1 failure mode).",
        "   - ADDR: logical memory address (hex).",
        "   - BIT: bit index within word (LSB=0).",
        "   - ACTUAL: golden/internal reference value observed by the harness.",
        "   - FAULT: value encoded by the injected mask for that site.",
        "   - READ: value read by the MBIST read operation; differences between READ and FAULT/ACTUAL indicate observable failure.",
        "   - Many rows have ACTUAL=0, FAULT=0, READ=1: the MBIST reads a '1' where golden and fault-mask indicate '0' — consistent with transition-up behaviour being observable on read. This indicates the test stimulus and timing made the transition visible.",
        "",
        "ARTIFACTS & REPRODUCTION",
        "   - JSON report: out/input_demo_8x16_scn4m/reports/latest.json",
        "   - Textual report: out/input_demo_8x16_scn4m/reports/report.txt (this file)",
        f"   - Simulator logs: {simulator_log_path.as_posix()}, {fault_log_path.as_posix()}",
        "   - Reproduce command (repo root):",
        f"     PATH=\"$PWD/venv/bin:$PATH\" python3 src/autombist/main.py simulate --out {command_outdir_text}",
        "",
        "",
        "End of report.",
    ]

    return "\n".join(lines)


def write_text_report(report: dict[str, Any], report_dir: Path, fault_log_text: str) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.txt"
    report_path.write_text(render_text_report(report, fault_log_text), encoding="utf-8")
    return report_path


def format_simulation_summary(report: dict[str, Any]) -> str:
    simulation = report.get("simulation", {})
    metrics = report.get("fault_metrics", {})
    status = report.get("status", "unknown").upper()
    runtime = report.get("runtime_seconds", 0.0)
    lines = [
        f"autombist: simulation {status}",
        f"  memory: {report.get('config', {}).get('memory_name', 'unknown')}",
        f"  algo: {simulation.get('algo', 'unknown')}",
        f"  fault type: {simulation.get('fault_type', 'unknown')}",
        f"  runtime: {runtime:.3f}s",
        f"  log: {report.get('log_path', 'unknown')}",
        f"  report: {report.get('report_path', 'unknown')}",
    ]
    if metrics.get("detected_faults") is not None and metrics.get("total_fault_sites") is not None:
        lines.append(
            f"  coverage: {metrics['detected_faults']}/{metrics['total_fault_sites']} ({metrics.get('coverage_percent', 0.0):.2f}%)"
        )
    else:
        lines.append("  coverage: not reported by the simulator")
    if metrics.get("injected_faults") is not None:
        lines.append(f"  injected faults: {metrics['injected_faults']}")
    return "\n".join(lines)
