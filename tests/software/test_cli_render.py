from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.cli_render import fault_progress, print_status_table, spinner, use_rich_output


def test_use_rich_output_false_when_no_color_set(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert use_rich_output() is False


def test_use_rich_output_false_when_not_a_tty(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert use_rich_output() is False


def test_use_rich_output_true_when_tty_and_no_color_unset(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert use_rich_output() is True


def test_print_status_table_plain_fallback_shows_headers_and_rows(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NO_COLOR", "1")  # force the plain path deterministically

    print_status_table(
        "my title",
        ["Tool", "Status", "Detail"],
        [["make", "OK", "/usr/bin/make"], ["verilator", "MISSING", "not found on PATH"]],
        status_col=1,
    )

    out = capsys.readouterr().out
    assert "my title" in out
    assert "Tool" in out and "Status" in out and "Detail" in out
    assert "make" in out and "OK" in out
    assert "verilator" in out and "MISSING" in out
    # Plain fallback: no ANSI/rich markup leaks into the output.
    assert "\x1b[" not in out
    assert "[green]" not in out and "[red]" not in out


def test_print_status_table_plain_fallback_columns_are_aligned(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    print_status_table(
        "t",
        ["Tool", "Status"],
        [["a", "OK"], ["much-longer-name", "MISSING"]],
        status_col=1,
    )

    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    # header, separator, row, row
    data_lines = [line for line in lines if line.startswith("a ") or line.startswith("much-longer-name")]
    assert len(data_lines) == 2
    # Both rows' "Status" column must start at the same character offset.
    status_offsets = [line.index("OK") if "OK" in line else line.index("MISSING") for line in data_lines]
    assert status_offsets[0] == status_offsets[1]


# ---------------------------------------------------------------------------
# spinner / fault_progress: no-op (but still functionally correct) when rich
# output isn't appropriate -- true for every non-TTY test run.
# ---------------------------------------------------------------------------


def test_spinner_is_a_transparent_noop_on_non_tty(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    ran = []
    with spinner("doing a thing"):
        ran.append(1)
    assert ran == [1]


def test_spinner_propagates_exceptions(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    with pytest.raises(ValueError, match="boom"):
        with spinner("doing a thing"):
            raise ValueError("boom")


def test_spinner_propagates_import_error_even_on_the_rich_active_path(monkeypatch) -> None:
    """Regression test: an ImportError raised INSIDE the "with spinner(...):"
    block must propagate as itself, not be misidentified by spinner()'s own
    "rich unavailable" try/except as a signal to silently fall back (which
    would then hit contextlib's "generator didn't stop after throw()"
    RuntimeError instead of the real exception). Forces the rich-ACTIVE path
    (not the non-TTY no-op path test_spinner_propagates_exceptions covers) by
    faking an interactive terminal."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    with pytest.raises(ImportError, match="simulated missing module inside block"):
        with spinner("doing a thing"):
            raise ImportError("simulated missing module inside block")


def test_fault_progress_yields_none_on_non_tty(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    with fault_progress(50) as callback:
        assert callback is None


def test_fault_progress_yields_none_for_trivially_small_campaigns(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    with fault_progress(1) as callback:
        assert callback is None
    with fault_progress(0) as callback:
        assert callback is None


def test_fault_progress_callback_is_callable_and_safe_when_rich_active(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    with fault_progress(10, description="test faults") as callback:
        assert callback is not None
        for i in range(1, 11):
            callback(i, 10)  # must not raise


# ---------------------------------------------------------------------------
# The rich paths: a forced terminal Console renders the table with ANSI
# colour, so the status styling is checked on the escape codes themselves.
# ---------------------------------------------------------------------------


def _tty(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)


def _forced_console(monkeypatch) -> None:
    """Every Console() the helpers build renders as a 16-colour terminal 200
    columns wide, whatever the environment (TERM, FORCE_COLOR) says."""
    import rich.console

    real_console = rich.console.Console

    def make_console(*args, **kwargs):
        kwargs.setdefault("force_terminal", True)
        kwargs.setdefault("color_system", "standard")
        kwargs.setdefault("width", 200)
        return real_console(*args, **kwargs)

    monkeypatch.setattr(rich.console, "Console", make_console)


def test_print_status_table_rich_path_colours_the_status_by_its_leading_word(monkeypatch, capsys) -> None:
    _tty(monkeypatch)
    _forced_console(monkeypatch)

    print_status_table(
        "my title",
        ["Tool", "Status", "Detail"],
        [
            ["make", "OK", "/usr/bin/make"],
            ["verilator", "MISSING", "not found on PATH"],
            ["yosys", "WARN old", "0.9"],
            ["iverilog", "SKIP", "n/a"],
        ],
        status_col=1,
    )

    out = capsys.readouterr().out
    assert "my title" in out
    assert "\x1b[32mOK" in out
    assert "\x1b[31mMISSING" in out
    assert "\x1b[33mWARN old" in out
    # A status with no style of its own is printed plain, and the markup the
    # styled ones use never reaches the output as text.
    assert re.search(r"\x1b\[3[0-7]mSKIP", out) is None and "SKIP" in out
    assert "[green]" not in out and "[red]" not in out and "[yellow]" not in out


def test_print_status_table_falls_back_to_plain_when_rich_is_unavailable(monkeypatch, capsys) -> None:
    _tty(monkeypatch)
    monkeypatch.setitem(sys.modules, "rich.console", None)

    print_status_table("t", ["Tool", "Status"], [["make", "OK"]], status_col=1)

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "t"
    assert lines[1].split() == ["Tool", "Status"]
    assert lines[3].split() == ["make", "OK"]


def test_spinner_rich_path_shows_the_message_and_runs_the_block(monkeypatch, capsys) -> None:
    _tty(monkeypatch)
    _forced_console(monkeypatch)
    ran = []

    with spinner("doing a thing"):
        ran.append(1)

    assert ran == [1]
    assert "doing a thing" in capsys.readouterr().out


def test_spinner_is_a_noop_when_rich_is_unavailable(monkeypatch, capsys) -> None:
    _tty(monkeypatch)
    monkeypatch.setitem(sys.modules, "rich.console", None)
    ran = []

    with spinner("doing a thing"):
        ran.append(1)

    assert ran == [1]
    assert capsys.readouterr().out == ""


def test_fault_progress_yields_none_when_rich_is_unavailable(monkeypatch) -> None:
    _tty(monkeypatch)
    monkeypatch.setitem(sys.modules, "rich.progress", None)

    with fault_progress(10) as callback:
        assert callback is None
