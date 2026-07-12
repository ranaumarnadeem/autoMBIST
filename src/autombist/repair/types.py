"""Shared data contracts for the redundancy-repair (BIRA/BISR) subpackage.

This module is deliberately dependency-free: it imports NOTHING from the rest of
autombist (not ``generator``, not ``runner``, not ``ConfigError``). That one-way
boundary is what keeps ``autombist.repair`` copy-paste-extractable into its own
package later -- the wrapper/RTL-generation side may import ``SpareGeometry`` from
here, but nothing here ever reaches back. See
``tests/software/test_repair_package_boundary.py`` for the mechanical enforcement.
"""
from __future__ import annotations

from dataclasses import dataclass

# Bump only on an INCOMPATIBLE change to the shapes below (mirrors reporting.py's
# report-level ``schema_version``; there is no per-instance version field -- the
# plain ``set[tuple[int, int]]`` fail_cells contract has none either).
SCHEMA_VERSION = "1.0"

__all__ = [
    "SCHEMA_VERSION",
    "SpareGeometry",
    "SpareGeometryError",
    "RepairSolution",
    "Unrepairable",
    "AnalysisResult",
]


class SpareGeometryError(ValueError):
    """Raised when a ``SpareGeometry``'s fields are inconsistent.

    Local to this module on purpose: ``repair/`` must never import
    ``generator.ConfigError``. The generator re-raises this as ``ConfigError`` at
    its own config boundary.
    """


@dataclass(slots=True, frozen=True)
class SpareGeometry:
    """The redundancy inventory of a spare-augmented memory.

    The single module both the wrapper/RTL-generation side (``generator.py``) and
    BIRA import -- neither redefines these fields or the address-width derivation
    locally. ``base_words``/``word_size`` are the LOGICAL (non-spare) dimensions,
    derived by the generator from the config's ``addr_width``/``data_width``.
    """

    base_words: int
    word_size: int
    num_spare_rows: int = 0
    num_spare_cols: int = 0  # reserved for phase 2 (column repair); 0 this phase

    def __post_init__(self) -> None:
        if self.base_words <= 0 or self.word_size <= 0:
            raise SpareGeometryError("base_words and word_size must be positive")
        if self.num_spare_rows < 0 or self.num_spare_cols < 0:
            raise SpareGeometryError(
                "num_spare_rows and num_spare_cols must be non-negative"
            )

    @property
    def total_words(self) -> int:
        """Physical word count including spare rows."""
        return self.base_words + self.num_spare_rows

    @property
    def mem_addr_width(self) -> int:
        """Physical address width the spare-augmented memory needs:
        ``ceil(log2(base_words + num_spare_rows))``.

        This is the ONE place that formula lives -- both the RTL parameterization
        and any address-space reasoning call it rather than re-deriving it. Spare
        rows inflate the address space (non-power-of-two depth; undecoded top
        addresses are dead), which is exactly the OpenRAM behaviour the external
        remap steers into (a spare row is the next address past the last logical
        one).
        """
        n = self.total_words if self.num_spare_rows > 0 else self.base_words
        return max(1, (n - 1).bit_length())


@dataclass(slots=True, frozen=True)
class RepairSolution:
    """A valid, computed repair: which spare row replaces which faulty row.

    Consumed by a future BISR step to program the remap's
    ``row_repair_en``/``faulty_row_addr``. A plain, immutable record crossing the
    module boundary -- it never reaches back into ``generator.py``/``runner.py``.
    """

    row_map: dict[int, int]  # faulty (logical) row address -> spare row index (0-based)


@dataclass(slots=True, frozen=True)
class Unrepairable:
    """BIRA could not compute a repair with the available spares."""

    faulty_rows: tuple[int, ...]  # sorted distinct faulty row addresses
    num_spare_rows: int  # spares available (the geometry's budget)
    reason: str = "distinct faulty row count exceeds available spare rows"


# What analyze() returns: a solution, or a verdict that no repair fits.
AnalysisResult = RepairSolution | Unrepairable
