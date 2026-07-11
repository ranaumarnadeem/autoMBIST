"""Adapter: turn a simulation report into the fail-bitmap a BIRA step consumes.

BIRA (Built-In Redundancy Analysis) needs exactly one thing from a run: the set
of memory cells the BIST *observed* failing, keyed by ``(address, bit)``. This
module is the single, uniform seam that produces that set, so a future BIRA
never has to know how the underlying report happens to be shaped.

Two report shapes feed in, in preference order:

* ``report["fail_bitmap"]`` -- the observation-derived full-scan produced by the
  functional-port fail scan (``tests/hardware/test_mbist.py``'s ``test_fail_scan``,
  parsed by ``reporting.parse_fail_bitmap_lines``): a list of
  ``{"addr": int, "bit": int}``. This is the authoritative source for a real
  defective memory, because it reports every cell that read wrong *independent of
  any injected fault list* -- the input a repair flow actually needs. Present only
  when a fail scan ran (opt-in ``FAIL_SCAN``), so it may be absent.
* ``report["fault_details"]`` -- the legacy per-injected-site records. DETECTED
  sites carry a hex-string ``ADDR`` + string ``BIT``; ESCAPED sites carry int
  ``addr`` + int ``bit`` (two schemas -- see ``docs/diagnosis-reports.md``). Only
  DETECTED sites are real observed failures; escaped sites are *undetected* and
  must never enter the fail-bitmap -- BIRA can only repair what the BIST caught.

``fail_cells`` unions both sources (the full scan is a superset of the detected
injected sites for stuck-at defects, so the union is just the complete observed-
failure set) and normalises every coordinate to a plain ``int`` so the caller
gets one uniform ``set[(addr, bit)]`` regardless of report provenance.
"""
from __future__ import annotations

from typing import Any

__all__ = ["fail_cells"]


def _coerce_int(value: Any) -> int | None:
    """Parse an address/bit that may already be an ``int`` or a decimal/hex
    string (``"7"``, ``"0x0004"``). Returns ``None`` for anything unparseable
    -- callers skip such entries rather than raising, since a report can be
    truncated (e.g. a simulator timeout) and a partial fail-bitmap is still
    useful."""
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for base in (0, 16):  # base 0 -> "0x.."/decimal; 16 -> bare hex fallback
            try:
                return int(text, base)
            except ValueError:
                continue
    return None


def fail_cells(report: dict[str, Any]) -> set[tuple[int, int]]:
    """Return ``{(addr, bit)}`` of cells the BIST OBSERVED failing.

    Unions ``report["fail_bitmap"]`` (the ungated full-scan, when present) with
    the DETECTED entries of ``report["fault_details"]``. Escaped/undetected
    sites are excluded. Malformed or partial entries are skipped defensively.
    This is the load-bearing input a redundancy-analysis (BIRA) step consumes.
    """
    cells: set[tuple[int, int]] = set()

    bitmap = report.get("fail_bitmap")
    if isinstance(bitmap, list):
        for entry in bitmap:
            if not isinstance(entry, dict):
                continue
            addr = _coerce_int(entry.get("addr"))
            bit = _coerce_int(entry.get("bit"))
            if addr is not None and bit is not None:
                cells.add((addr, bit))

    details = report.get("fault_details")
    if isinstance(details, list):
        for site in details:
            if not isinstance(site, dict):
                continue
            if site.get("status") != "detected":
                continue
            # Detected sites use uppercase hex-string ADDR/BIT; be tolerant of
            # the lowercase int form too so a single code path serves both.
            addr = _coerce_int(site.get("ADDR", site.get("addr")))
            bit = _coerce_int(site.get("BIT", site.get("bit")))
            if addr is not None and bit is not None:
                cells.add((addr, bit))

    return cells
