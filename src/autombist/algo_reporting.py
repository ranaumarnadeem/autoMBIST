"""Reports for the fault-campaign engine: per-fault coverage and multi-algorithm
comparison matrices, in markdown, CSV, or JSON.
"""
from __future__ import annotations

import json
from pathlib import Path

from .algo_engine import CampaignResult

SCHEMA_VERSION = "1.0.0"
VALID_FORMATS = ("md", "csv", "json")


def _check_fmt(fmt: str) -> None:
    if fmt not in VALID_FORMATS:
        raise ValueError(f"unknown report format '{fmt}'. Choose one of: {', '.join(VALID_FORMATS)}")


def coverage_meets_threshold(result: CampaignResult, min_coverage: float | None) -> bool:
    """Gate a campaign on its coverage percent. No threshold => always passes."""
    if min_coverage is None:
        return True
    return result.coverage_percent >= min_coverage


# --------------------------------------------------------------------------- #
# Single-campaign report (per-fault detail)
# --------------------------------------------------------------------------- #
_FAULT_COLUMNS = (
    "idx", "type", "vaddr.vbit", "aaddr.abit", "p0", "p1",
    "result", "elem", "op", "addr", "activations",
)


def _fault_row(index: int, r) -> tuple[str, ...]:
    rec = r.record
    return (
        str(index),
        rec.type,
        f"{rec.vaddr}.{rec.vbit}",
        f"{rec.aaddr}.{rec.abit}",
        str(rec.p0),
        str(rec.p1),
        "DETECTED" if r.detected else "ESCAPED",
        "" if r.elem is None else str(r.elem),
        "" if r.op is None else str(r.op),
        "" if r.addr is None else str(r.addr),
        "" if r.activations is None else str(r.activations),
    )


def render_campaign_csv(result: CampaignResult) -> str:
    lines = [",".join(_FAULT_COLUMNS)]
    for r in result.faults:
        lines.append(",".join(_fault_row(r.index, r)))
    return "\n".join(lines) + "\n"


def render_campaign_md(result: CampaignResult) -> str:
    header = (
        f"# autombist test — {result.algo_name}\n\n"
        f"Memory: {result.mem.addr_width}x{result.mem.data_width}, init={result.mem.init_val}  \n"
        f"Coverage: **{result.detected}/{result.total} ({result.coverage_percent:.2f}%)**  \n"
        f"Golden run: {'clean' if result.golden_clean else 'FAILED'}  \n"
        f"Build: {result.build_seconds:.2f}s, run: {result.run_seconds:.2f}s, sim: {result.sim}\n\n"
    )
    rows = [list(_FAULT_COLUMNS)] + [list(_fault_row(r.index, r)) for r in result.faults]
    widths = [max(len(row[i]) for row in rows) for i in range(len(_FAULT_COLUMNS))]

    def fmt_row(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(w) for cell, w in zip(cells, widths)) + " |"

    table = [fmt_row(rows[0]), "| " + " | ".join("-" * w for w in widths) + " |"]
    table.extend(fmt_row(row) for row in rows[1:])
    return header + "\n".join(table) + "\n"


def render_campaign_json(result: CampaignResult) -> str:
    payload = {"schema_version": SCHEMA_VERSION, **result.to_dict()}
    return json.dumps(payload, indent=2, sort_keys=False)


def write_campaign_report(result: CampaignResult, path: Path, fmt: str = "md") -> Path:
    _check_fmt(fmt)
    renderers = {"md": render_campaign_md, "csv": render_campaign_csv, "json": render_campaign_json}
    Path(path).write_text(renderers[fmt](result), encoding="utf-8")
    return Path(path)


# --------------------------------------------------------------------------- #
# Multi-algorithm comparison matrix
# --------------------------------------------------------------------------- #
def _matrix_rows(results: list[CampaignResult]) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Returns (ordered fault keys, {fault_key: {algo_name: 'D'/'E'}})."""
    fault_keys: list[str] = []
    seen: set[str] = set()
    cells: dict[str, dict[str, str]] = {}
    for result in results:
        for key, mark in result.matrix_row().items():
            if key not in seen:
                seen.add(key)
                fault_keys.append(key)
            cells.setdefault(key, {})[result.algo_name] = mark
    return fault_keys, cells


def render_matrix_csv(results: list[CampaignResult]) -> str:
    algo_names = [r.algo_name for r in results]
    fault_keys, cells = _matrix_rows(results)
    lines = [",".join(["fault", *algo_names])]
    for key in fault_keys:
        row = cells.get(key, {})
        lines.append(",".join([key, *(row.get(a, "-") for a in algo_names)]))
    totals = ",".join(f"{r.detected}/{r.total}" for r in results)
    lines.append(f"total,{totals}")
    return "\n".join(lines) + "\n"


def render_matrix_md(results: list[CampaignResult]) -> str:
    algo_names = [r.algo_name for r in results]
    fault_keys, cells = _matrix_rows(results)
    header = ["fault", *algo_names]
    rows = [header]
    for key in fault_keys:
        row = cells.get(key, {})
        rows.append([key, *(row.get(a, "-") for a in algo_names)])
    rows.append(["total", *(f"{r.detected}/{r.total}" for r in results)])

    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]

    def fmt_row(vals: list[str]) -> str:
        return "| " + " | ".join(v.ljust(w) for v, w in zip(vals, widths)) + " |"

    lines = [fmt_row(rows[0]), "| " + " | ".join("-" * w for w in widths) + " |"]
    lines.extend(fmt_row(r) for r in rows[1:])
    return "\n".join(lines) + "\n"


def render_matrix_json(results: list[CampaignResult]) -> str:
    fault_keys, cells = _matrix_rows(results)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "algos": [r.algo_name for r in results],
        "totals": {r.algo_name: {"detected": r.detected, "total": r.total,
                                   "coverage_percent": r.coverage_percent} for r in results},
        "matrix": [{"fault": key, **cells.get(key, {})} for key in fault_keys],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def write_matrix_report(results: list[CampaignResult], path: Path, fmt: str = "md") -> Path:
    _check_fmt(fmt)
    renderers = {"md": render_matrix_md, "csv": render_matrix_csv, "json": render_matrix_json}
    Path(path).write_text(renderers[fmt](results), encoding="utf-8")
    return Path(path)
