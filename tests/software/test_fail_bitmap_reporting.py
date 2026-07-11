"""Pin the observation-derived fail-bitmap plumbing in reporting.py.

The functional-port fail scan (test_mbist.test_fail_scan) prints one
``FAIL_CELL {json}`` line per observed-failing cell and a
``FAIL_SCAN_COMPLETE`` marker. reporting.py must:
  * parse those into a deduplicated, sorted ``fail_bitmap`` list, and
  * attach it to the report ONLY when the marker is present, so every ordinary
    (non-fail-scan) run stays byte-identical -- the reason schema_version is
    NOT bumped. These are additive tests; test_reporting.py is untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.reporting import (  # noqa: E402
    build_simulation_report,
    fail_scan_ran,
    parse_fail_bitmap_lines,
)


# --------------------------------------------------------------------------- #
# parse_fail_bitmap_lines
# --------------------------------------------------------------------------- #
def test_parse_fail_bitmap_basic() -> None:
    stdout = (
        'FAIL_CELL {"addr": 10, "bit": 7}\n'
        'FAIL_CELL {"addr": 3, "bit": 0}\n'
        "FAIL_SCAN_COMPLETE cells=2\n"
    )
    assert parse_fail_bitmap_lines(stdout, "") == [
        {"addr": 3, "bit": 0},
        {"addr": 10, "bit": 7},
    ]  # sorted by (addr, bit)


def test_parse_fail_bitmap_dedupes() -> None:
    stdout = (
        'FAIL_CELL {"addr": 5, "bit": 2}\n'
        'FAIL_CELL {"addr": 5, "bit": 2}\n'
    )
    assert parse_fail_bitmap_lines(stdout, "") == [{"addr": 5, "bit": 2}]


def test_parse_fail_bitmap_skips_malformed_and_non_int() -> None:
    stdout = (
        "FAIL_CELL not-json\n"
        'FAIL_CELL {"addr": "x", "bit": 1}\n'      # non-int addr -> skip
        'FAIL_CELL {"addr": 2, "bit": true}\n'      # bool bit -> skip
        'FAIL_CELL [1, 2]\n'                          # not an object -> skip
        'FAIL_CELL {"addr": 1, "bit": 0}\n'          # the one good line
    )
    assert parse_fail_bitmap_lines(stdout, "") == [{"addr": 1, "bit": 0}]


def test_parse_fail_bitmap_reads_stderr_too() -> None:
    assert parse_fail_bitmap_lines("", 'FAIL_CELL {"addr": 9, "bit": 4}\n') == [
        {"addr": 9, "bit": 4}
    ]


def test_parse_fail_bitmap_empty() -> None:
    assert parse_fail_bitmap_lines("nothing here\n", "") == []


# --------------------------------------------------------------------------- #
# fail_scan_ran marker detection
# --------------------------------------------------------------------------- #
def test_fail_scan_ran_true_on_marker() -> None:
    assert fail_scan_ran("FAIL_SCAN_COMPLETE cells=0\n", "") is True


def test_fail_scan_ran_false_without_marker() -> None:
    assert fail_scan_ran('FAIL_CELL {"addr": 1, "bit": 0}\n', "") is False


# --------------------------------------------------------------------------- #
# build_simulation_report: conditional fail_bitmap key (byte-identity when off)
# --------------------------------------------------------------------------- #
def _report(tmp_path: Path, stdout: str):
    return build_simulation_report(
        tool_version="0.3.1",
        config={
            "memory_name": "sram_tiny",
            "wrapper_module_name": "sram_tiny_mbist",
            "addr_width": 2,
            "data_width": 4,
            "we_active_low": True,
        },
        command=["make"],
        cwd=tmp_path,
        log_path=tmp_path / "simulate.log",
        report_path=tmp_path / "latest.json",
        returncode=0,
        runtime_seconds=1.0,
        stdout=stdout,
        stderr="",
        use_saboteur=True,
        faults=2,
        fault_seed=1,
        fault_type="stuck-at",
        pulse_width_ns=2,
        algo="march-c",
        results_xml_path=tmp_path / "results.xml",
    )


def test_report_has_no_fail_bitmap_key_without_scan(tmp_path: Path) -> None:
    """Byte-identity guard: a run that never scanned must not gain the key, and
    schema_version must stay put."""
    report = _report(tmp_path, "[autombist] Result: PASS\n")
    assert "fail_bitmap" not in report
    assert report["schema_version"] == "1.2.0"


def test_report_has_empty_fail_bitmap_on_clean_scan(tmp_path: Path) -> None:
    """A scan that ran and found nothing -> present but empty (distinguishable
    from 'no scan')."""
    report = _report(tmp_path, "FAIL_SCAN_COMPLETE cells=0\n")
    assert report["fail_bitmap"] == []


def test_report_populates_fail_bitmap_on_defects(tmp_path: Path) -> None:
    stdout = (
        'FAIL_CELL {"addr": 3, "bit": 0}\n'
        'FAIL_CELL {"addr": 1, "bit": 2}\n'
        "FAIL_SCAN_COMPLETE cells=2\n"
    )
    report = _report(tmp_path, stdout)
    assert report["fail_bitmap"] == [{"addr": 1, "bit": 2}, {"addr": 3, "bit": 0}]
