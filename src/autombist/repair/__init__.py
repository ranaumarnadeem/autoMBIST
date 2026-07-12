"""``autombist.repair`` -- redundancy analysis (BIRA) and self-repair (BISR).

Deliberately structured as a self-contained subpackage with a **one-way**
dependency on the rest of autombist: it imports nothing from ``generator``/
``runner``/wrapper internals. The data contracts (``SpareGeometry``,
``RepairSolution``, ``Unrepairable``) cross the module boundary as plain frozen
dataclasses; the generator side may import ``SpareGeometry`` from here for RTL
sizing, but never the reverse. That discipline (enforced by
``tests/software/test_repair_package_boundary.py``) keeps this package
copy-paste-extractable into its own repo if the on-chip self-repair work ever
grows into a separate artifact.
"""
from .bira import analyze
from .bisr import encode_row_repair
from .types import (
    SCHEMA_VERSION,
    AnalysisResult,
    RepairSignature,
    RepairSolution,
    SpareGeometry,
    SpareGeometryError,
    Unrepairable,
)

__all__ = [
    "SCHEMA_VERSION",
    "AnalysisResult",
    "RepairSignature",
    "RepairSolution",
    "SpareGeometry",
    "SpareGeometryError",
    "Unrepairable",
    "analyze",
    "encode_row_repair",
]
