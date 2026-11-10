"""The CLI's error contract, shared by the test files that check it: a failure is
exactly one error line on stderr, an "autombist: ..." line or a "[smoke] FAIL:
..." line, with no traceback. Warnings ("autombist: warning: ...") are not
error lines.
"""
from __future__ import annotations

_ERROR_PREFIXES = ("autombist: ", "[smoke] FAIL: ")


def _error_lines(result) -> list[str]:
    try:
        stderr = result.stderr  # Click 8.2+ keeps stderr apart from stdout
    except ValueError:  # Click 8.1 mixes it into the output; the error is printed last
        return result.output.splitlines()[-1:]
    return [line for line in stderr.splitlines() if line and not line.startswith("autombist: warning: ")]


def assert_one_error_line(result, fragment: str, exit_code: int = 1) -> None:
    assert result.exit_code == exit_code, result.output
    assert "Traceback" not in result.output
    errors = _error_lines(result)
    assert len(errors) == 1, result.output
    assert errors[0].startswith(_ERROR_PREFIXES), result.output
    assert fragment in errors[0], result.output
