"""tests/cli_errors.py checks the fragment on the error line itself, not anywhere
in the output, under both Click 8.1 (stderr mixed in) and Click 8.2+ (stderr
kept apart)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from cli_errors import assert_one_error_line

SUCCESS = "PDL (run_mbist):  out/top_run_mbist.pdl\n"


@dataclass
class _Separate:  # Click 8.2+
    output: str
    stderr: str
    exit_code: int = 1


class _Mixed:  # Click 8.1
    def __init__(self, output: str, exit_code: int = 1) -> None:
        self.output = output
        self.exit_code = exit_code

    @property
    def stderr(self) -> str:
        raise ValueError("stderr not separately captured")


def _both(stdout: str, stderr: str):
    return [_Separate(stdout + stderr, stderr), _Mixed(stdout + stderr)]


@pytest.mark.parametrize("result", _both(SUCCESS, "autombist: cannot write out/top_run_mbist.vec\n"))
def test_a_fragment_on_a_success_line_only_is_not_the_error(result) -> None:
    with pytest.raises(AssertionError):
        assert_one_error_line(result, "top_run_mbist.pdl")


@pytest.mark.parametrize("result", _both(SUCCESS, "autombist: cannot write out/top_run_mbist.pdl\n"))
def test_the_fragment_on_the_error_line_passes(result) -> None:
    assert_one_error_line(result, "top_run_mbist.pdl")


@pytest.mark.parametrize("result", _both("", "autombist: warning: short run\nautombist: cannot write x\n"))
def test_a_warning_is_not_an_error_line(result) -> None:
    assert_one_error_line(result, "cannot write x")


def test_two_error_lines_are_not_one() -> None:
    result = _Separate("autombist: first\nautombist: second\n", "autombist: first\nautombist: second\n")
    with pytest.raises(AssertionError):
        assert_one_error_line(result, "second")


@pytest.mark.parametrize("result", _both(SUCCESS, "Traceback (most recent call last):\nautombist: x\n"))
def test_a_traceback_fails(result) -> None:
    with pytest.raises(AssertionError):
        assert_one_error_line(result, "x")
