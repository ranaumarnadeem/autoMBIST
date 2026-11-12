"""Measured fault coverage per class, as evidence for a functional-safety analysis.

The coverage a campaign reports counts fault records equally. Diagnostic coverage
is weighted by failure rate:

    DC = sum(lambda_i * c_i) / sum(lambda_i)

autoMBIST has no failure rates, which depend on the process and the memory, so a
DC figure appears only when the user gives them. Their unit does not matter, only
their ratios. This report rates nothing: it prints no low/medium/high band and
does not compare a result to the standard's.

Input and output are plain data: the dict `autombist test --report x.json --fmt
json` writes, and a dict that renders to JSON.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

from .fault_classes import CLASS_NAMES, fault_class

SCHEMA_VERSION = "1.0.0"

LIMITS = (
    "The fault classes come from the memory-test literature, not Annex D of ISO 26262-5. "
    "Mapping them to the standard's is part of the safety analysis.",
    "Measured by injecting the listed faults into a behavioural memory model in simulation, "
    "so the figures hold for this fault list, not for silicon.",
    "Covers a BIST run at power-on. It is no evidence for faults that appear while the chip is running.",
    "Transient faults and soft errors are not modelled.",
    "Faults in the BIST controller itself are not graded here.",
    "This report makes no ASIL or certification claim.",
)


class SafetyReportError(ValueError):
    """The campaign is no evidence, or the failure rates cannot be used."""


def _check_campaign(campaign: Any) -> list[dict[str, Any]]:
    if not isinstance(campaign, Mapping) or not isinstance(campaign.get("faults"), list):
        raise SafetyReportError("not a campaign report (write one with: autombist test --report x.json --fmt json)")
    faults = campaign["faults"]
    if not faults:
        raise SafetyReportError("the campaign report has no faults, so it measured nothing")
    if campaign.get("golden_clean") is not True:
        raise SafetyReportError("the campaign's golden run was not clean, so its detections mean nothing")
    sequence = campaign.get("sequence")
    if isinstance(sequence, Mapping) and sequence.get("matches") is False:
        raise SafetyReportError("the controller does not implement its specified march sequence")
    for index, fault in enumerate(faults):
        if (not isinstance(fault, Mapping) or not isinstance(fault.get("type"), str)
                or not isinstance(fault.get("detected"), bool)):
            raise SafetyReportError(f"fault record {index} needs a type and a true/false detected")
    detected = sum(fault["detected"] for fault in faults)
    if campaign.get("total") != len(faults) or campaign.get("detected") != detected:
        raise SafetyReportError(
            f"the report's totals ({campaign.get('detected')}/{campaign.get('total')}) "
            f"do not match its {len(faults)} fault records ({detected} detected)"
        )
    return faults


def _is_rate(rate: Any) -> bool:
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        return False
    try:
        return math.isfinite(rate) and rate >= 0
    except OverflowError:  # an int too large for a float
        return False


def _check_failure_rates(failure_rates: Any, measured: set[str]) -> dict[str, float]:
    if not isinstance(failure_rates, Mapping):
        raise SafetyReportError("failure rates must be a mapping of fault class to rate")
    for name, rate in failure_rates.items():
        if name not in CLASS_NAMES:
            raise SafetyReportError(f"unknown fault class {name!r} (classes: {', '.join(CLASS_NAMES)})")
        if not _is_rate(rate):
            raise SafetyReportError(f"failure rate for {name} must be a finite number >= 0, not {rate!r}")
    missing = [name for name in CLASS_NAMES if name in measured and name not in failure_rates]
    if missing:
        raise SafetyReportError(
            f"no failure rate for measured class {', '.join(missing)} (give 0 to leave a class out of the DC figure)"
        )
    if sum(failure_rates.values()) == 0:
        raise SafetyReportError("the failure rates add up to zero, so there is no DC to compute")
    return {name: float(rate) for name, rate in failure_rates.items()}


def _fault_list_digest(faults: list[dict[str, Any]]) -> str:
    """The list itself, not its order or its results, so two reports on one list
    can be told apart from two reports on different lists. A fault's memory index
    (``mi``, a shared controller's) is part of the record when the report has it;
    a list without one hashes as before."""
    fields = ("type", "vaddr", "vbit", "aaddr", "abit", "p0", "p1", "vport", "aport", "weight")
    lines = sorted(
        " ".join(str(fault.get(name)) for name in fields) + (f" mi={fault['mi']}" if fault.get("mi") else "")
        for fault in faults
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _coverage(detected: int, total: int) -> float | None:
    return 100.0 * detected / total if total else None


def build_safety_report(campaign: Any, failure_rates: Any = None) -> dict[str, Any]:
    faults = _check_campaign(campaign)

    counts: dict[str, list[int]] = {}
    unmapped: dict[str, list[int]] = {}
    for fault in faults:
        name = fault_class(fault["type"])
        tally = counts.setdefault(name, [0, 0]) if name else unmapped.setdefault(fault["type"], [0, 0])
        tally[0] += fault["detected"]
        tally[1] += 1

    rates = None if failure_rates is None else _check_failure_rates(failure_rates, set(counts))

    classes = []
    for name in CLASS_NAMES:
        detected, total = counts.get(name, (0, 0))
        classes.append({
            "class": name,
            "detected": detected,
            "total": total,
            "coverage_percent": _coverage(detected, total),
            "failure_rate": rates.get(name) if rates else None,
        })

    diagnostic_coverage = None
    if rates:
        # A class given a rate but not measured adds to the rate and not to what
        # was detected, so it counts as undetected.
        weighted = sum(row["failure_rate"] * (row["coverage_percent"] or 0.0)
                       for row in classes if row["failure_rate"] is not None)
        diagnostic_coverage = weighted / sum(rates.values())

    return {
        "schema_version": SCHEMA_VERSION,
        "campaign": {
            "algo_name": campaign.get("algo_name"),
            "mem": campaign.get("mem"),
            "sim": campaign.get("sim"),
            "tool_versions": campaign.get("tool_versions"),
            "backgrounds_run": campaign.get("backgrounds_run"),
        },
        "fault_list": {"records": len(faults), "sha256": _fault_list_digest(faults)},
        "classes": classes,
        "unmapped": [
            {"fault_type": name, "detected": detected, "total": total}
            for name, (detected, total) in sorted(unmapped.items())
        ],
        "unweighted_coverage_percent": _coverage(sum(fault["detected"] for fault in faults), len(faults)),
        "diagnostic_coverage_percent": diagnostic_coverage,
        "record_weights_ignored": any(fault.get("weight") is not None for fault in faults),
        "limits": list(LIMITS),
    }


def format_percent(value: float) -> str:
    """Rounded down, so a figure just under full never prints as 100.00%."""
    return f"{math.floor(round(value * 100, 6)) / 100:.2f}%"


def _memory_text(mem: Any) -> str:
    if not isinstance(mem, Mapping):
        return "not recorded"
    words = f"{2 ** mem['addr_width']}" if isinstance(mem.get("addr_width"), int) else "?"
    return (f"{words} words x {mem.get('data_width')} bits ({mem.get('addr_width')}x{mem.get('data_width')}), "
            f"init {mem.get('init_val')}, {mem.get('num_ports')} port(s)")


def render_safety_report_md(report: dict[str, Any]) -> str:
    campaign = report["campaign"]
    versions = campaign["tool_versions"] if isinstance(campaign["tool_versions"], Mapping) else {}
    lines = ["# Safety report", ""]
    if report["diagnostic_coverage_percent"] is None:
        lines.append(
            f"Unweighted coverage: {format_percent(report['unweighted_coverage_percent'])}. This is not diagnostic "
            "coverage, which needs a failure rate for each fault class (`--failure-rates`)."
        )
    else:
        lines.append(
            f"Diagnostic coverage: {format_percent(report['diagnostic_coverage_percent'])}, from the failure rates "
            "given. It covers only the classes given a rate, and leaves out unmapped fault types."
        )
        lines.append("")
        lines.append(f"Unweighted coverage: {format_percent(report['unweighted_coverage_percent'])}.")

    lines += ["", "## What these numbers depend on", ""]
    lines.append(f"- Algorithm: {campaign['algo_name']}")
    lines.append(f"- Memory: {_memory_text(campaign['mem'])}")
    if campaign["backgrounds_run"]:
        lines.append(f"- Data backgrounds: {', '.join(map(str, campaign['backgrounds_run']))}")
    fault_list = report["fault_list"]
    lines.append(f"- Fault list: {fault_list['records']} records, sha256 {fault_list['sha256']}")
    lines.append(f"- Simulator: {campaign['sim']}, {versions.get('simulator', 'version not recorded')}")
    commit = versions.get("autombist_commit")
    lines.append(f"- autoMBIST: {versions.get('autombist', 'version not recorded')}"
                 + (f", source commit {commit}" if commit else ""))

    lines += ["", "## Coverage by fault class", "", "| Class | Detected | Total | Coverage | Failure rate |",
              "|---|---|---|---|---|"]
    for row in report["classes"]:
        coverage = "not measured" if row["coverage_percent"] is None else format_percent(row["coverage_percent"])
        rate = "-" if row["failure_rate"] is None else f"{row['failure_rate']:g}"
        lines.append(f"| {row['class']} | {row['detected']} | {row['total']} | {coverage} | {rate} |")

    if report["unmapped"]:
        lines += ["", "## Unmapped fault types", "",
                  "These have no fault class. They count in the unweighted coverage only.", ""]
        lines += [f"- {row['fault_type']}: {row['detected']}/{row['total']} detected" for row in report["unmapped"]]

    lines += ["", "## What this report does not cover", ""]
    if report["record_weights_ignored"]:
        lines.append("- The fault records carry weights, which this report ignores: it counts records equally "
                     "within a class.")
    lines += [f"- {limit}" for limit in report["limits"]]
    return "\n".join(lines) + "\n"
