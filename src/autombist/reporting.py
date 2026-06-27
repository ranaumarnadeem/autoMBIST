from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

FAULT_COVERAGE_RE = re.compile(r"Fault coverage:\s*(\d+)\/(\d+)\s*\((\d+(?:\.\d+)?)%\)")
INJECTED_FAULTS_RE = re.compile(r"Injected faults:\s*(\d+)")
FAULT_COUNT_RE = re.compile(r"Fault count:\s*(\d+)")
TRANSITION_MODEL_RE = re.compile(r"Transition model:\s*(.+)")
TRANSITION_ALGO_RE = re.compile(r"Algorithm:\s*(.+)")
TRANSITION_DIR_RE = re.compile(r"Fault direction:\s*(.+)")
TRANSITION_BLOCKED_WRITES_RE = re.compile(r"Writes with transition attempts:\s*(\d+)")
TRANSITION_READ_VERIFICATIONS_RE = re.compile(r"Read verifications:\s*(\d+)")
TRANSITION_DETECTED_BITS_RE = re.compile(r"Detected delayed bits:\s*(\d+)")
TRANSITION_RESOLVED_BITS_RE = re.compile(r"Resolved-before-read bits:\s*(\d+)")
TRANSITION_OVERWRITES_RE = re.compile(r"Pending overwrites before read:\s*(\d+)")
TRANSITION_UNVERIFIED_RE = re.compile(r"Unverified pending writes at done:\s*(\d+)")
TRANSITION_MAX_PENDING_RE = re.compile(r"Max pending depth:\s*(\d+)")
TRANSITION_TOP_BLOCKED_RE = re.compile(r"Top blocked addresses \(addr:bits\):\s*(.+)")
TRANSITION_TOP_DETECTED_RE = re.compile(r"Top detected addresses \(addr:bits\):\s*(.+)")


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


def _float_or_none(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


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


def _parse_addr_bits_map(value: str) -> dict[str, int]:
    value = value.strip()
    if not value or value == "-":
        return {}
    result: dict[str, int] = {}
    for part in value.split(","):
        token = part.strip()
        if ":" not in token:
            continue
        addr, bits = token.split(":", 1)
        try:
            result[addr.strip()] = int(bits.strip())
        except ValueError:
            continue
    return result


def parse_transition_metrics(stdout: str, stderr: str) -> dict[str, Any]:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    metrics: dict[str, Any] = {}

    patterns: list[tuple[re.Pattern[str], str, type]] = [
        (TRANSITION_MODEL_RE, "model", str),
        (TRANSITION_ALGO_RE, "algo", str),
        (TRANSITION_DIR_RE, "direction", str),
        (TRANSITION_BLOCKED_WRITES_RE, "blocked_write_cycles", int),
        (TRANSITION_READ_VERIFICATIONS_RE, "read_verifications", int),
        (TRANSITION_DETECTED_BITS_RE, "detected_bit_events", int),
        (TRANSITION_RESOLVED_BITS_RE, "resolved_before_read_bits", int),
        (TRANSITION_OVERWRITES_RE, "pending_overwrites", int),
        (TRANSITION_UNVERIFIED_RE, "unverified_pending_writes", int),
        (TRANSITION_MAX_PENDING_RE, "max_pending_depth", int),
    ]

    for pattern, key, cast_type in patterns:
        match = pattern.search(combined)
        if not match:
            continue
        raw = match.group(1).strip()
        metrics[key] = cast_type(raw) if cast_type is not str else raw

    top_blocked = TRANSITION_TOP_BLOCKED_RE.search(combined)
    if top_blocked:
        metrics["top_blocked_addresses"] = _parse_addr_bits_map(top_blocked.group(1))
    top_detected = TRANSITION_TOP_DETECTED_RE.search(combined)
    if top_detected:
        metrics["top_detected_addresses"] = _parse_addr_bits_map(top_detected.group(1))

    return metrics


def parse_junit_xml(results_xml_path: Path) -> dict[str, Any]:
    if not results_xml_path.exists():
        return {
            "path": str(results_xml_path),
            "exists": False,
            "summary": {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time_seconds": 0.0},
            "tests": [],
            "system_out": [],
        }

    try:
        root = ET.parse(results_xml_path).getroot()
    except ET.ParseError:
        return {
            "path": str(results_xml_path),
            "exists": True,
            "parse_error": True,
            "summary": {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time_seconds": 0.0},
            "tests": [],
            "system_out": [],
        }
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites and root.tag == "testsuites":
        suites = [elem for elem in root if elem.tag == "testsuite"]

    tests: list[dict[str, Any]] = []
    system_out: list[str] = []
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time_seconds": 0.0}

    for suite in suites:
        suite_cases = suite.findall("testcase")
        summary["tests"] += int(suite.attrib.get("tests", str(len(suite_cases))))
        summary["failures"] += int(suite.attrib.get("failures", "0"))
        summary["errors"] += int(suite.attrib.get("errors", "0"))
        summary["skipped"] += int(suite.attrib.get("skipped", "0"))
        summary["time_seconds"] += float(suite.attrib.get("time", "0") or "0")

        suite_stdout = suite.findtext("system-out")
        if suite_stdout and suite_stdout.strip():
            system_out.append(suite_stdout.strip())

        for case in suite_cases:
            case_stdout = case.findtext("system-out")
            case_status = "passed"
            details: dict[str, Any] = {}

            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            if failure is not None:
                case_status = "failed"
                details = {
                    "message": failure.attrib.get("message", ""),
                    "type": failure.attrib.get("type", ""),
                    "text": (failure.text or "").strip(),
                }
            elif error is not None:
                case_status = "error"
                details = {
                    "message": error.attrib.get("message", ""),
                    "type": error.attrib.get("type", ""),
                    "text": (error.text or "").strip(),
                }
            elif skipped is not None:
                case_status = "skipped"
                details = {
                    "message": skipped.attrib.get("message", ""),
                    "text": (skipped.text or "").strip(),
                }

            test_entry: dict[str, Any] = {
                "name": case.attrib.get("name", ""),
                "classname": case.attrib.get("classname", ""),
                "file": case.attrib.get("file", ""),
                "lineno": case.attrib.get("lineno", ""),
                "time_seconds": _float_or_none(case.attrib.get("time")),
                "sim_time_ns": _float_or_none(case.attrib.get("sim_time_ns")),
                "ratio_time": _float_or_none(case.attrib.get("ratio_time")),
                "status": case_status,
            }
            if details:
                test_entry["details"] = details
            if case_stdout and case_stdout.strip():
                test_entry["system_out"] = case_stdout.strip()
                system_out.append(case_stdout.strip())
            tests.append(test_entry)

    return {
        "path": str(results_xml_path),
        "exists": True,
        "summary": summary,
        "tests": tests,
        "system_out": system_out,
    }


def build_simulation_report(
    *,
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
    results_xml_path: Path,
) -> dict[str, Any]:
    metrics = parse_fault_metrics(stdout, stderr)
    if metrics.injected_faults is None:
        metrics.injected_faults = faults

    report = {
        "schema_version": "1.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool_version": tool_version,
        "status": "pass" if returncode == 0 else "fail",
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
        "junit": parse_junit_xml(results_xml_path),
    }
    if fault_type in {"transition-up", "transition-down"}:
        report["transition_metrics"] = parse_transition_metrics(stdout, stderr)
    report["summary"] = format_simulation_summary(report)
    return report


def write_simulation_report(report: dict[str, Any], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    results_path = report_dir / "results.json"
    latest_path = report_dir / "latest.json"

    report["results_path"] = str(results_path)
    report["report_path"] = str(latest_path)
    report["summary"] = format_simulation_summary(report)

    payload = json.dumps(report, indent=2, sort_keys=True)
    results_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")
    return latest_path


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
    junit = report.get("junit", {})
    transition_metrics = report.get("transition_metrics", {})

    memory_name = config.get("memory_name", "unknown")
    wrapper_module_name = config.get("wrapper_module_name", "unknown")
    coverage_percent = metrics.get("coverage_percent")
    coverage_text = f"{coverage_percent:.2f}%" if coverage_percent is not None else "not reported"
    fault_summary_block = _extract_fault_summary_block(fault_log_text) or "Fault summary\n(none available)"
    junit_out = [entry for entry in junit.get("system_out", []) if entry][:3]
    junit_excerpt = "\n\n".join(junit_out) if junit_out else "(none)"
    fault_type = str(simulation.get("fault_type", "unknown"))

    if fault_type in {"transition-up", "transition-down"}:
        table_semantics = [
            "TABLE SEMANTICS (TRANSITION FAULTS)",
            "  - PREV: bit value before the write attempt.",
            "  - ATTEMPT: bit value requested by the write operation.",
            "  - READ: value observed on readback after the write.",
            "  - OBS=DELAYED: transition delay observed (ATTEMPT != READ on a faulted transition site).",
        ]
        transition_summary = [
            "TRANSITION LIFECYCLE METRICS",
            f"  - model: {transition_metrics.get('model', 'unknown')}",
            f"  - direction: {transition_metrics.get('direction', 'unknown')}",
            f"  - blocked write cycles: {transition_metrics.get('blocked_write_cycles', 'unknown')}",
            f"  - read verifications: {transition_metrics.get('read_verifications', 'unknown')}",
            f"  - detected delayed bits: {transition_metrics.get('detected_bit_events', 'unknown')}",
            f"  - resolved-before-read bits: {transition_metrics.get('resolved_before_read_bits', 'unknown')}",
            f"  - pending overwrites: {transition_metrics.get('pending_overwrites', 'unknown')}",
            f"  - unverified pending writes: {transition_metrics.get('unverified_pending_writes', 'unknown')}",
            f"  - max pending depth: {transition_metrics.get('max_pending_depth', 'unknown')}",
            f"  - top blocked addresses: {transition_metrics.get('top_blocked_addresses', {}) or '-'}",
            f"  - top detected addresses: {transition_metrics.get('top_detected_addresses', {}) or '-'}",
        ]
    else:
        table_semantics = [
            "TABLE SEMANTICS (STUCK-AT FAULTS)",
            "  - ACTUAL: golden/internal reference value observed by the harness.",
            "  - FAULT: value imposed by fault masking at that site.",
            "  - READ: value observed on MBIST readback.",
        ]
        transition_summary = []

    divider = "-" * 70
    lines = [
        f"autoMBIST Simulation Report — {memory_name}",
        "",
        f"Source:     {report.get('report_path', 'unknown')}",
        f"Results:    {report.get('results_path', 'unknown')}",
        f"Simulator:  {report.get('log_path', 'unknown')}",
        f"JUnit XML:  {junit.get('path', 'unknown')}",
        "",
        divider,
        "SUMMARY",
        divider,
        f"{'Memory name:':<23}{memory_name}",
        f"{'Wrapper module:':<23}{wrapper_module_name}",
        f"{'Address width:':<23}{config.get('addr_width', 'unknown')}",
        f"{'Data width:':<23}{config.get('data_width', 'unknown')}",
        f"{'Algorithm:':<23}{simulation.get('algo', 'unknown')}",
        f"{'Fault model:':<23}{simulation.get('fault_type', 'unknown')}",
        f"{'Faults requested:':<23}{simulation.get('faults_requested', 'unknown')}",
        f"{'Injected faults (obs):':<23}{metrics.get('injected_faults', 'unknown')}",
        f"{'Detected faults:':<23}{metrics.get('detected_faults', 'unknown')}",
        f"{'Total fault sites:':<23}{metrics.get('total_fault_sites', 'unknown')}",
        f"{'Coverage:':<23}{coverage_text}",
        f"{'Run status:':<23}{str(report.get('status', 'unknown')).upper()}",
        f"{'Runtime:':<23}{float(report.get('runtime_seconds', 0.0)):.3f} s",
        "",
        divider,
        fault_summary_block,
        "",
        divider,
        *table_semantics,
        *(["", divider, *transition_summary] if transition_summary else []),
        "",
        divider,
        "JUNIT SYSTEM-OUT (EXCERPT)",
        divider,
        junit_excerpt,
        "",
        "ARTIFACTS",
        f"  - latest.json:  {report.get('report_path', 'unknown')}",
        f"  - results.json: {report.get('results_path', 'unknown')}",
        f"  - report.txt:   {Path(str(report.get('report_path', 'unknown'))).with_name('report.txt')}",
        f"  - results.xml:  {junit.get('path', 'unknown')}",
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
    status = str(report.get("status", "unknown")).upper()
    runtime = float(report.get("runtime_seconds", 0.0))
    lines = [
        f"autombist: simulation {status}",
        f"  memory: {report.get('config', {}).get('memory_name', 'unknown')}",
        f"  algo: {simulation.get('algo', 'unknown')}",
        f"  fault type: {simulation.get('fault_type', 'unknown')}",
        f"  runtime: {runtime:.3f}s",
        f"  log: {report.get('log_path', 'unknown')}",
        f"  report: {report.get('report_path', 'unknown')}",
    ]
    detected = metrics.get("detected_faults")
    total = metrics.get("total_fault_sites")
    if detected is not None and total is not None:
        coverage = float(metrics.get("coverage_percent") or 0.0)
        lines.append(f"  coverage: {detected}/{total} ({coverage:.2f}%)")
    else:
        lines.append("  coverage: not reported by the simulator")
    if metrics.get("injected_faults") is not None:
        lines.append(f"  injected faults: {metrics['injected_faults']}")
    transition_metrics = report.get("transition_metrics", {})
    if transition_metrics:
        lines.append(
            "  transition: "
            f"detected-bits={transition_metrics.get('detected_bit_events', 'unknown')}, "
            f"resolved-before-read={transition_metrics.get('resolved_before_read_bits', 'unknown')}, "
            f"overwrites={transition_metrics.get('pending_overwrites', 'unknown')}"
        )
    controller = report.get("controller_grading")
    if controller:
        coverage = controller.get("coverage_percent")
        if isinstance(coverage, (int, float)):
            lines.append(
                "  controller (FaultFlow scan SA): "
                f"{controller.get('detected')}/{controller.get('denominator')} "
                f"({coverage:.2f}%), excluded-blackbox={controller.get('excluded_blackbox')}"
            )
        else:
            lines.append("  controller (FaultFlow scan SA): not reported")
    junit_summary = report.get("junit", {}).get("summary", {})
    lines.append(
        "  junit: "
        f"{junit_summary.get('tests', 0)} tests, "
        f"{junit_summary.get('failures', 0)} failures, "
        f"{junit_summary.get('errors', 0)} errors"
    )
    return "\n".join(lines)


def coverage_meets_threshold(
    report: dict[str, Any], min_coverage: float | None
) -> tuple[bool, float | None]:
    """Gate a run on array fault coverage.

    Returns ``(ok, coverage)``. ``ok`` is True when no gate is set or the reported
    coverage meets the threshold; ``coverage`` is the reported percent (or None if
    the simulator reported none — which never fails the gate).
    """
    if min_coverage is None:
        return True, None
    coverage = report.get("fault_metrics", {}).get("coverage_percent")
    if coverage is None:
        return True, None
    return coverage >= min_coverage, coverage


def merge_faultflow_coverage(
    report: dict[str, Any], faultflow_block: dict[str, Any] | None
) -> dict[str, Any]:
    """Attach FaultFlow controller-structural coverage to a simulation report.

    Stored under the top-level ``controller_grading`` key, kept distinct from the
    array-test ``fault_metrics``. Refreshes the rendered ``summary``.
    """
    if faultflow_block:
        report["controller_grading"] = faultflow_block
        report["summary"] = format_simulation_summary(report)
    return report
