"""Which class of memory fault each built-in fault type belongs to.

These are the classes of the memory-test literature (the same sources
``fault_primitives.py`` cites), NOT the fault classes of ISO 26262-5 Annex D.
The standard's own tables have not been checked, so nothing here claims to
match them, and nothing here rates a test.

A user-defined fault type has no class: it is reported as unmapped, not forced
into the nearest one.
"""
from __future__ import annotations

from collections.abc import Iterable

_BY_CLASS: dict[str, tuple[str, ...]] = {
    "stuck_at": ("SA0", "SA1"),
    "stuck_open": ("SOF",),
    "transition": ("TF0", "TF1"),
    "addressing": ("AF_NOACC", "AF_ALIAS"),
    "coupling": (
        "CFIN", "CFID", "CFST", "CFDS",
        "CFTR0", "CFTR1", "CFWD0", "CFWD1", "CFRD0", "CFRD1", "CFIR0", "CFIR1", "CFDRD0", "CFDRD1",
    ),
    "read_write": ("WDF0", "WDF1", "RDF0", "RDF1", "DRDF0", "DRDF1", "IRF0", "IRF1"),
    "dynamic": (
        "DYN_RDF00", "DYN_RDF01", "DYN_RDF10", "DYN_RDF11",
        "DYN_DRDF00", "DYN_DRDF01", "DYN_DRDF10", "DYN_DRDF11",
        "DYN_IRF00", "DYN_IRF01", "DYN_IRF10", "DYN_IRF11",
    ),
    "retention": ("DRF",),
    "half_select": ("HSD",),
}

FAULT_CLASSES: dict[str, str] = {
    fault_type: fault_class_name for fault_class_name, fault_types in _BY_CLASS.items() for fault_type in fault_types
}


def fault_class(fault_type: str) -> str | None:
    return FAULT_CLASSES.get(fault_type)


def unmapped_fault_types(fault_types: Iterable[str]) -> list[str]:
    return sorted({fault_type for fault_type in fault_types if fault_type not in FAULT_CLASSES})
