"""Pin the BIRA fail-bitmap adapter (`bira_input.fail_cells`).

`fail_cells` is the single seam a future redundancy-analysis (BIRA) step
consumes: it must turn either report shape -- the ungated full-scan
`fail_bitmap` (ints) or the legacy `fault_details` (hex-string ADDR + string
BIT for detected sites, int addr/bit for escaped) -- into one uniform
integer-keyed set of OBSERVED-failing cells, with escapes excluded. These pure
tests fix that contract with no simulator.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.bira_input import fail_cells  # noqa: E402


# --------------------------------------------------------------------------- #
# fail_bitmap (the ungated full-scan source)
# --------------------------------------------------------------------------- #
def test_fail_bitmap_ints_pass_through() -> None:
    report = {"fail_bitmap": [{"addr": 3, "bit": 0}, {"addr": 10, "bit": 7}]}
    assert fail_cells(report) == {(3, 0), (10, 7)}


def test_fail_bitmap_string_coordinates_are_coerced() -> None:
    # A producer that emitted decimal/hex strings must still normalise to ints:
    # "5" (decimal), "0x0f" (prefixed hex, base 0), and "1a" (bare hex, the
    # base-16 fallback) all resolve.
    report = {
        "fail_bitmap": [
            {"addr": "5", "bit": "2"},
            {"addr": "0x0f", "bit": "3"},
            {"addr": "1a", "bit": "1"},
        ]
    }
    assert fail_cells(report) == {(5, 2), (15, 3), (26, 1)}


def test_fail_bitmap_empty_is_empty_set() -> None:
    # A clean scan that ran and found nothing -> present but empty -> no cells.
    assert fail_cells({"fail_bitmap": []}) == set()


def test_missing_everything_is_empty_set() -> None:
    assert fail_cells({}) == set()


# --------------------------------------------------------------------------- #
# fault_details (the legacy dual-schema source)
# --------------------------------------------------------------------------- #
def test_fault_details_detected_hex_addr_decoded() -> None:
    """Detected sites carry hex-string ADDR + string BIT (the classic path's
    fault-summary-table shape); they must decode to ints."""
    report = {
        "fault_details": [
            {"#": "1", "TYPE": "SA0", "ADDR": "0x0004", "BIT": "3", "status": "detected"},
        ]
    }
    assert fail_cells(report) == {(4, 3)}


def test_fault_details_escaped_sites_are_excluded() -> None:
    """BIRA repairs only what the BIST caught. An escaped (undetected) site --
    even though it carries clean int addr/bit -- must never enter the bitmap."""
    report = {
        "fault_details": [
            {"ADDR": "0x0004", "BIT": "3", "status": "detected"},
            {"addr": 7, "bit": 2, "fault_value": 1, "kind": "SA1", "status": "escaped"},
        ]
    }
    assert fail_cells(report) == {(4, 3)}  # (7, 2) escaped -> excluded


def test_fault_details_multiple_detected() -> None:
    report = {
        "fault_details": [
            {"ADDR": "0x0000", "BIT": "0", "status": "detected"},
            {"ADDR": "0x000A", "BIT": "7", "status": "detected"},
            {"ADDR": "0x0003", "BIT": "1", "status": "detected"},
        ]
    }
    assert fail_cells(report) == {(0, 0), (10, 7), (3, 1)}


# --------------------------------------------------------------------------- #
# Union + robustness
# --------------------------------------------------------------------------- #
def test_union_of_both_sources() -> None:
    """When both are present the result is their union (the full scan is a
    superset of detected injected sites, so this is just the complete set)."""
    report = {
        "fail_bitmap": [{"addr": 3, "bit": 0}, {"addr": 5, "bit": 2}],
        "fault_details": [
            {"ADDR": "0x0003", "BIT": "0", "status": "detected"},  # dup of a bitmap cell
            {"ADDR": "0x0009", "BIT": "4", "status": "detected"},  # only in details
        ],
    }
    assert fail_cells(report) == {(3, 0), (5, 2), (9, 4)}


def test_malformed_entries_are_skipped_not_raised() -> None:
    report = {
        "fail_bitmap": [
            {"addr": 1, "bit": 0},
            {"addr": None, "bit": 2},          # unparseable addr -> skip
            {"addr": "nope", "bit": "x"},      # unparseable -> skip
            "not-a-dict",                       # wrong type -> skip
            {"bit": 5},                         # missing addr -> skip
        ]
    }
    assert fail_cells(report) == {(1, 0)}


def test_non_list_fields_are_ignored() -> None:
    # Defensive: a truncated/garbled report must not raise.
    assert fail_cells({"fail_bitmap": "garbage", "fault_details": 42}) == set()


def test_boolean_coordinates_rejected() -> None:
    # bool is an int subclass; a stray True/False must not become (1,0)/(0,0).
    report = {"fail_bitmap": [{"addr": True, "bit": False}, {"addr": 2, "bit": 1}]}
    assert fail_cells(report) == {(2, 1)}
