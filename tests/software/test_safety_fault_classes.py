"""Every fault type autoMBIST can measure has a fault class, so a new one cannot
reach a safety report unclassified. A user-defined type has none and is reported
as unmapped, not forced into the nearest class."""
from __future__ import annotations

import pytest

from autombist.algo_engine import BUILTIN_FAULT_TYPES
from autombist.fault_primitives import FIXED_TYPE_NAMES, default_registry
from autombist.safety.fault_classes import FAULT_CLASSES, fault_class, unmapped_fault_types


def _measurable_fault_types() -> set[str]:
    return {*BUILTIN_FAULT_TYPES, *FIXED_TYPE_NAMES, *(prim.name for prim in default_registry())}


def test_every_built_in_fault_type_has_a_class() -> None:
    assert unmapped_fault_types(_measurable_fault_types()) == []


def test_the_table_names_no_fault_type_that_does_not_exist() -> None:
    assert set(FAULT_CLASSES) <= _measurable_fault_types()


@pytest.mark.parametrize(
    ("fault_type", "expected"),
    [
        ("SA1", "stuck_at"),
        ("SOF", "stuck_open"),
        ("TF0", "transition"),
        ("AF_ALIAS", "addressing"),
        ("CFDS", "coupling"),
        ("CFDRD1", "coupling"),
        ("WDF0", "read_write"),
        ("IRF1", "read_write"),
        ("DYN_DRDF10", "dynamic"),
        ("DRF", "retention"),
        ("HSD", "half_select"),
    ],
)
def test_a_fault_type_is_in_its_class(fault_type: str, expected: str) -> None:
    assert fault_class(fault_type) == expected


def test_a_user_defined_fault_type_is_unmapped() -> None:
    assert fault_class("MYFAULT") is None
    assert unmapped_fault_types(["SA0", "MYFAULT", "ZFAULT", "MYFAULT"]) == ["MYFAULT", "ZFAULT"]
