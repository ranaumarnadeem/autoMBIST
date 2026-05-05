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
