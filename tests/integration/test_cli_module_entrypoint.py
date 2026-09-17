"""`python -m autombist.cli ...` must actually invoke the CLI, not silently
do nothing.

cli.py defines the Typer `app` and every subcommand, but historically had no
`if __name__ == "__main__":` guard. Running it as a module (`python -m
autombist.cli`, as opposed to `python -m autombist.main` or the installed
`autombist` console script) gives it a non-empty `__package__`, so the
direct-script fallback at the top of the file never triggers either -- the
module just imports, defines `app`, and exits 0 with no output and no side
effect. `cli.py` is also the single most plausible file to guess for this
invocation, since every command in the tool is defined there.

typer.testing.CliRunner (used elsewhere in this test suite) calls the Typer
app object directly in-process and can never exercise this: `__name__` is
never `"__main__"` under CliRunner, so a regression here would not be caught
by any of the existing CliRunner-based tests. This needs a real subprocess.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_python_dash_m_autombist_dot_cli_actually_runs(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autombist.cli", "init", "--out", str(tmp_path)],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Created starter MBIST config" in result.stdout, result.stdout
    assert (tmp_path / "config.yml").is_file(), (
        "the command reported success but created no files -- this is the "
        "exact silent no-op this test exists to catch"
    )


def test_python_dash_m_autombist_dot_cli_help_shows_real_usage() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autombist.cli", "--help"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Usage:" in result.stdout, (
        f"expected Typer's usage text, got: {result.stdout!r}"
    )
