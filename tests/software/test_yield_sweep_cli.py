"""The yield-sweep command's argument handling, without running a simulation:
fault-count ranges, and errors reported as one line with exit code 1 rather
than a traceback."""
from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from autombist.cli import _parse_fault_counts
from autombist.main import app

runner = CliRunner()


@pytest.mark.parametrize(("values", "expected"), [
    (["3"], [3]),
    (["1..4"], [1, 2, 3, 4]),
    (["0", "2..3", "8"], [0, 2, 3, 8]),
    ([" 5..5 "], [5]),
])
def test_fault_counts_take_integers_and_inclusive_ranges(values: list[str], expected: list[int]) -> None:
    assert _parse_fault_counts(values) == expected


@pytest.mark.parametrize("bad", ["x", "1..", "4..2", "1.5"])
def test_a_bad_fault_count_is_refused(bad: str) -> None:
    with pytest.raises(typer.Exit):
        _parse_fault_counts([bad])


def _invoke(tmp_path: Path, *args: str):
    return runner.invoke(app, ["yield-sweep", "--out", str(tmp_path / "out"), "--spare-rows", "1", *args])


def test_a_missing_config_is_one_error_line_not_a_traceback(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--config", str(tmp_path / "missing.yml"), "--faults", "1")

    assert result.exit_code == 1
    assert "autombist:" in result.output and "Traceback" not in result.output


def test_a_fault_type_the_scan_cannot_see_is_refused(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--config", str(tmp_path / "missing.yml"), "--faults", "1",
                     "--fault-type", "transition-up")

    assert result.exit_code == 1 and "stuck-at faults only" in result.output


def test_a_repeated_fault_count_is_refused(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--config", str(tmp_path / "missing.yml"), "--faults", "1..3", "--faults", "2")

    assert result.exit_code == 1 and "must not repeat" in result.output


def test_zero_trials_is_refused_by_the_option_itself(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--faults", "1", "--trials", "0")

    assert result.exit_code != 0
