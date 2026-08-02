"""Reports for the fault-campaign engine: per-fault coverage and multi-algorithm
comparison matrices, in markdown, CSV, or JSON.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def _render_pipe_table(columns: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """Shared markdown pipe-table renderer (column-width-aligned), used by
    render_campaign_md/render_matrix_md/render_diagnosis_md -- previously
    three copy-pasted implementations of the identical algorithm."""
    all_rows = [list(columns)] + [list(row) for row in rows]
    widths = [max(len(row[i]) for row in all_rows) for i in range(len(columns))]

    def fmt_row(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(w) for cell, w in zip(cells, widths)) + " |"

    lines = [fmt_row(list(columns)), "| " + " | ".join("-" * w for w in widths) + " |"]
    lines.extend(fmt_row(list(row)) for row in rows)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Single-campaign report (per-fault detail)
# --------------------------------------------------------------------------- #
_FAULT_COLUMNS = (
    "idx", "type", "vaddr.vbit", "aaddr.abit", "p0", "p1",
    "result", "elem", "op", "addr", "activations",
)
# Appended after _FAULT_COLUMNS only when result.mem.num_ports > 1 (see
# _fault_columns_for/_fault_row) -- single-port reports (num_ports == 1, the
# default) keep the exact column set/order above, byte-identical to every
# pre-multi-port report.
_PORT_COLUMNS = ("vport", "aport")


def _fault_columns_for(result: CampaignResult) -> tuple[str, ...]:
    if result.mem.num_ports > 1:
        return _FAULT_COLUMNS + _PORT_COLUMNS
    return _FAULT_COLUMNS


def _fault_row(index: int, r, *, with_ports: bool = False) -> tuple[str, ...]:
    rec = r.record
    row = (
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
    if with_ports:
        row = row + (str(rec.vport), str(rec.aport))
    return row


def render_campaign_csv(result: CampaignResult) -> str:
    with_ports = result.mem.num_ports > 1
    lines = [",".join(_fault_columns_for(result))]
    for r in result.faults:
        lines.append(",".join(_fault_row(r.index, r, with_ports=with_ports)))
    return "\n".join(lines) + "\n"


def render_campaign_md(result: CampaignResult) -> str:
    with_ports = result.mem.num_ports > 1
    header = (
        f"# autombist test — {result.algo_name}\n\n"
        f"Memory: {result.mem.addr_width}x{result.mem.data_width}, init={result.mem.init_val}  \n"
        f"Coverage: **{result.detected}/{result.total} ({result.coverage_percent:.2f}%)**  \n"
        f"Golden run: {'clean' if result.golden_clean else 'FAILED'}  \n"
        f"Build: {result.build_seconds:.2f}s, run: {result.run_seconds:.2f}s, sim: {result.sim}\n\n"
    )
    columns = _fault_columns_for(result)
    rows = [_fault_row(r.index, r, with_ports=with_ports) for r in result.faults]
    return header + _render_pipe_table(columns, rows)


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
    columns = ("fault", *algo_names)
    rows: list[tuple[str, ...]] = []
    for key in fault_keys:
        row = cells.get(key, {})
        rows.append((key, *(row.get(a, "-") for a in algo_names)))
    rows.append(("total", *(f"{r.detected}/{r.total}" for r in results)))
    return _render_pipe_table(columns, rows)


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


# --------------------------------------------------------------------------- #
# Diagnosis / fail-bitmap report (single-campaign, cell-level)
# --------------------------------------------------------------------------- #
# NOTE: structurally independent from the two report families above -- its own
# VALID_FORMATS check, its own renderer dict, its own write_* entrypoint. This
# is deliberate: write_campaign_report/write_matrix_report and their render_*
# functions are never touched, so an existing caller asking for the pre-existing
# csv/md/json via those functions can never accidentally be routed through this
# new code path.
DIAGNOSIS_VALID_FORMATS = ("md", "csv", "json")

# List-valued cell fields are '|'-joined in the flat (CSV/MD) renderings, since
# neither format has a native list type; JSON keeps them as real lists. Applies
# to: fault_types_injected_here, escaped_types_here, observed_from_fault_types.
_DIAGNOSIS_LIST_JOIN = "|"


def _check_diagnosis_fmt(fmt: str) -> None:
    if fmt not in DIAGNOSIS_VALID_FORMATS:
        raise ValueError(
            f"unknown diagnosis report format '{fmt}'. Choose one of: {', '.join(DIAGNOSIS_VALID_FORMATS)}"
        )


def _decode_xor_bits(xor: str) -> list[int]:
    """Decode a march_engine RESULT-line ``xor=`` bitstring into the list of
    bit indices that mismatched, MSB-first.

    The RTL prints ``det_xor`` (a ``logic [DW-1:0]``, i.e. bit DW-1 is the
    MSB) with Verilog's ``%b`` format specifier
    (``march_engine.sv``/``march_engine_mp.sv``: ``$display("... xor=%b", ...,
    det_xor)``). Verilog's ``%b`` always prints the vector MSB-first: the
    leftmost character of the string is bit ``DW-1``, and the rightmost
    character is bit 0 -- the opposite of naively treating the string's left-
    to-right index as the bit index.

    Concretely, for a string of length ``len(xor)``, the character at
    (0-indexed, left-to-right) string position ``i`` corresponds to bit index
    ``len(xor) - 1 - i``.

    Worked example from engine/README.md's own quick-start output
    (``RESULT DETECTED alg=MARCHCM elem=1 op=0 addr=50 xor=00000100``, an
    8-bit-wide memory): the single ``1`` sits at string position 5 (0-indexed
    from the left), so its bit index is ``8 - 1 - 5 == 2`` -- i.e. bit 2
    mismatched, not bit 5.
    """
    width = len(xor)
    return [width - 1 - i for i, ch in enumerate(xor) if ch == "1"]


def build_diagnosis_cells(result: CampaignResult) -> list[dict[str, Any]]:
    """Sparse per-(addr, bit) diagnosis table for a single (non-matrix)
    campaign result.

    Only emits rows for cells that actually appear as either an injection
    site (some FaultRecord's vaddr/vbit) or a decoded observation site (from
    a DETECTED FaultResult's addr + xor) -- never the full addr x bit grid,
    which would be mostly empty for any real memory size.

    Escaped injection sites still appear in the table (with an empty
    observation-side contribution): a fault that was injected but never
    observed is exactly the information a fail-bitmap diagnosis exists to
    surface, so it must not silently vanish.
    """
    # cell -> mutable accumulator, built in two passes (injection sites, then
    # observation sites) before the final sort + dict conversion.
    injected_types: dict[tuple[int, int], list[str]] = {}
    injected_detected: dict[tuple[int, int], bool] = {}
    injected_escaped_types: dict[tuple[int, int], list[str]] = {}
    observed_counts: dict[tuple[int, int], int] = {}
    observed_types: dict[tuple[int, int], list[str]] = {}

    for r in result.faults:
        cell = (r.record.vaddr, r.record.vbit)
        injected_types.setdefault(cell, []).append(r.record.type)
        # A cell can be the injection site for more than one fault (e.g. SA0
        # and SA1 at the same vaddr.vbit); detected_as_injection is True if
        # ANY of them was detected.
        injected_detected[cell] = injected_detected.get(cell, False) or r.detected
        # escaped_types_here is tracked PER FAULT, not derived from the OR'd
        # detected_as_injection flag above -- if SA0 at this cell is detected
        # but SA1 at the same cell escapes, SA1 must still show up here. Using
        # "not detected_as_injection" instead would silently mask SA1's
        # escape whenever any co-located fault happened to be caught.
        if not r.detected:
            injected_escaped_types.setdefault(cell, []).append(r.record.type)

    for r in result.faults:
        if not r.detected or r.addr is None or r.xor is None:
            continue
        for bit in _decode_xor_bits(r.xor):
            cell = (r.addr, bit)
            observed_counts[cell] = observed_counts.get(cell, 0) + 1
            observed_types.setdefault(cell, []).append(r.record.type)

    all_cells = set(injected_types) | set(observed_counts)

    rows: list[dict[str, Any]] = []
    for cell in sorted(all_cells):
        addr, bit = cell
        is_injection = cell in injected_types
        is_observation = cell in observed_counts
        if is_injection and is_observation:
            role = "both"
        elif is_injection:
            role = "injection"
        else:
            role = "observation"

        types_here = injected_types.get(cell, [])
        detected_here = injected_detected.get(cell, False) if is_injection else False
        escaped_here = list(injected_escaped_types.get(cell, []))

        rows.append(
            {
                "addr": addr,
                "bit": bit,
                "role": role,
                "fault_types_injected_here": list(types_here),
                "detected_as_injection": detected_here,
                "escaped_types_here": escaped_here,
                "times_observed_mismatch": observed_counts.get(cell, 0),
                "observed_from_fault_types": list(observed_types.get(cell, [])),
            }
        )
    return rows


_DIAGNOSIS_COLUMNS = (
    "addr", "bit", "role", "fault_types_injected_here", "detected_as_injection",
    "escaped_types_here", "times_observed_mismatch", "observed_from_fault_types",
)


def _diagnosis_row(cell: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(cell["addr"]),
        str(cell["bit"]),
        cell["role"],
        _DIAGNOSIS_LIST_JOIN.join(cell["fault_types_injected_here"]),
        "True" if cell["detected_as_injection"] else "False",
        _DIAGNOSIS_LIST_JOIN.join(cell["escaped_types_here"]),
        str(cell["times_observed_mismatch"]),
        _DIAGNOSIS_LIST_JOIN.join(cell["observed_from_fault_types"]),
    )


def render_diagnosis_csv(result: CampaignResult) -> str:
    """CSV diagnosis report. List-valued columns (fault_types_injected_here,
    escaped_types_here, observed_from_fault_types) are '|'-joined -- CSV has
    no native list type."""
    cells = build_diagnosis_cells(result)
    lines = [",".join(_DIAGNOSIS_COLUMNS)]
    for cell in cells:
        lines.append(",".join(_diagnosis_row(cell)))
    return "\n".join(lines) + "\n"


def render_diagnosis_md(result: CampaignResult) -> str:
    """Markdown diagnosis report. List-valued columns are '|'-joined (see
    render_diagnosis_csv)."""
    cells = build_diagnosis_cells(result)
    header = (
        f"# autombist diagnosis — {result.algo_name}\n\n"
        f"Memory: {result.mem.addr_width}x{result.mem.data_width}, init={result.mem.init_val}  \n"
        f"Coverage: **{result.detected}/{result.total} ({result.coverage_percent:.2f}%)**  \n"
        f"Cells: {len(cells)} (sparse: injection and/or observation sites only)\n\n"
    )
    rows = [_diagnosis_row(cell) for cell in cells]
    return header + _render_pipe_table(_DIAGNOSIS_COLUMNS, rows)


def render_diagnosis_json(result: CampaignResult) -> str:
    """JSON diagnosis report. List-valued fields stay real JSON lists (unlike
    the CSV/MD renderers, which '|'-join them)."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "algo_name": result.algo_name,
        "mem": {
            "addr_width": result.mem.addr_width,
            "data_width": result.mem.data_width,
            "init_val": result.mem.init_val,
            "num_ports": result.mem.num_ports,
        },
        "detected": result.detected,
        "total": result.total,
        "coverage_percent": result.coverage_percent,
        "cells": build_diagnosis_cells(result),
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def write_diagnosis_report(result: CampaignResult, path: Path, fmt: str = "md") -> Path:
    """Write a diagnosis/fail-bitmap report (own fmt validation, own renderer
    set -- see the module-level note above this section)."""
    _check_diagnosis_fmt(fmt)
    renderers = {
        "md": render_diagnosis_md,
        "csv": render_diagnosis_csv,
        "json": render_diagnosis_json,
    }
    Path(path).write_text(renderers[fmt](result), encoding="utf-8")
    return Path(path)


# --------------------------------------------------------------------------- #
# Blind syndrome-based diagnosis (single-campaign, fault-type ambiguity)
# --------------------------------------------------------------------------- #
# Structurally independent from the report families above -- own VALID_FORMATS,
# own renderer dict, own write_* entrypoint (same deliberate isolation as the
# diagnosis section, so this never touches write_campaign_report/
# write_diagnosis_report or their render_* functions).
#
# Honest scope note: the engine's capture model records only the FIRST
# detecting (elem, op) per run (RESULT_DETECTED_RE), not a per-element
# pass/fail bit-string the way the cited literature's syndrome does. What's
# implemented here is a faithful, narrower reading of the same underlying
# problem: fault TYPES that this algorithm flags at the identical (elem, op)
# location -- or that both escape entirely -- are indistinguishable to a
# diagnostician who only observes "this algorithm detected/escaped, and
# where." This is campaign-mode diagnosis (ground truth known from the
# injected fault list), useful for algorithm R&D, not real-silicon blind
# triage -- don't conflate the two.
SYNDROME_VALID_FORMATS = ("md", "csv", "json")


def _check_syndrome_fmt(fmt: str) -> None:
    if fmt not in SYNDROME_VALID_FORMATS:
        raise ValueError(
            f"unknown syndrome report format '{fmt}'. Choose one of: {', '.join(SYNDROME_VALID_FORMATS)}"
        )


def compute_syndrome_groups(result: CampaignResult) -> list[dict[str, Any]]:
    """Groups the fault TYPES present in `result` (one injected instance per
    type is the expected shape, e.g. generate_all_types_faults(mem)'s output
    -- multiple instances of the same type make 'the' syndrome for that type
    ambiguous on its own terms, independent of this function) by identical
    (detected, elem, op) signature: the (elem, op) location where THIS march
    test first flags each type (elem=op=None if it escapes).

    Returns one dict per distinct signature, sorted by (detected desc, elem,
    op): {"detected": bool, "elem": int | None, "op": int | None,
    "fault_types": list[str], "ambiguous": bool}. ambiguous is True iff
    len(fault_types) > 1 -- exactly the case a blind diagnostician (observing
    only this algorithm's detect/escape + location output) cannot resolve
    without further information.
    """
    groups: dict[tuple[bool, int | None, int | None], list[str]] = {}
    for r in result.faults:
        key = (r.detected, r.elem, r.op)
        groups.setdefault(key, []).append(r.record.type)

    def _sort_key(key: tuple[bool, int | None, int | None]) -> tuple[bool, int, int]:
        detected, elem, op = key
        return (not detected, elem if elem is not None else -1, op if op is not None else -1)

    rows: list[dict[str, Any]] = []
    for key in sorted(groups, key=_sort_key):
        detected, elem, op = key
        fault_types = groups[key]
        rows.append(
            {
                "detected": detected,
                "elem": elem,
                "op": op,
                "fault_types": fault_types,
                "ambiguous": len(fault_types) > 1,
            }
        )
    return rows


_SYNDROME_COLUMNS = ("detected", "elem", "op", "fault_types", "ambiguous")


def _syndrome_row(group: dict[str, Any]) -> tuple[str, ...]:
    return (
        "DETECTED" if group["detected"] else "ESCAPED",
        "" if group["elem"] is None else str(group["elem"]),
        "" if group["op"] is None else str(group["op"]),
        _DIAGNOSIS_LIST_JOIN.join(group["fault_types"]),
        "True" if group["ambiguous"] else "False",
    )


def render_syndrome_csv(result: CampaignResult) -> str:
    """CSV syndrome report. fault_types is '|'-joined -- CSV has no native
    list type (same convention as render_diagnosis_csv)."""
    groups = compute_syndrome_groups(result)
    lines = [",".join(_SYNDROME_COLUMNS)]
    for group in groups:
        lines.append(",".join(_syndrome_row(group)))
    return "\n".join(lines) + "\n"


def render_syndrome_md(result: CampaignResult) -> str:
    """Markdown syndrome report. fault_types is '|'-joined (see
    render_syndrome_csv)."""
    groups = compute_syndrome_groups(result)
    ambiguous_count = sum(1 for g in groups if g["ambiguous"])
    header = (
        f"# autombist syndrome diagnosis — {result.algo_name}\n\n"
        f"Memory: {result.mem.addr_width}x{result.mem.data_width}, init={result.mem.init_val}  \n"
        f"Fault types: {result.total}  \n"
        f"Syndrome groups: {len(groups)} ({ambiguous_count} ambiguous -- 2+ fault types "
        "indistinguishable by this algorithm alone)\n\n"
    )
    rows = [_syndrome_row(group) for group in groups]
    return header + _render_pipe_table(_SYNDROME_COLUMNS, rows)


def render_syndrome_json(result: CampaignResult) -> str:
    """JSON syndrome report. fault_types stays a real JSON list (unlike the
    CSV/MD renderers, which '|'-join it)."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "algo_name": result.algo_name,
        "mem": {
            "addr_width": result.mem.addr_width,
            "data_width": result.mem.data_width,
            "init_val": result.mem.init_val,
            "num_ports": result.mem.num_ports,
        },
        "groups": compute_syndrome_groups(result),
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def write_syndrome_report(result: CampaignResult, path: Path, fmt: str = "md") -> Path:
    """Write a blind syndrome-ambiguity report (own fmt validation, own
    renderer set -- see the module-level note above this section)."""
    _check_syndrome_fmt(fmt)
    renderers = {
        "md": render_syndrome_md,
        "csv": render_syndrome_csv,
        "json": render_syndrome_json,
    }
    Path(path).write_text(renderers[fmt](result), encoding="utf-8")
    return Path(path)
