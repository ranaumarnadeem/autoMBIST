"""Shared console-rendering helpers for the CLI: a rich.Table when the output
is an interactive terminal, degrading to a plain fixed-width table otherwise
(non-TTY, ``NO_COLOR`` set, or ``rich`` unavailable for any reason) -- so a
piped/scripted invocation never sees ANSI escapes or table-drawing characters
it didn't ask for.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Callable, Iterator

import typer

_STATUS_STYLE = {
    "OK": "green",
    "PASS": "green",
    "MISSING": "red",
    "FAIL": "red",
    "WARN": "yellow",
}


def use_rich_output() -> bool:
    """True when stdout is an interactive terminal and ``NO_COLOR`` isn't set."""
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def print_status_table(
    title: str,
    headers: list[str],
    rows: list[list[str]],
    *,
    status_col: int = 1,
) -> None:
    """Print a table whose ``status_col`` is color-coded by its leading word
    (OK/PASS -> green, MISSING/FAIL -> red, WARN -> yellow). Degrades to a
    plain fixed-width table when rich output isn't appropriate.
    """
    if use_rich_output():
        try:
            from rich.console import Console
            from rich.table import Table

            table = Table(title=title)
            for header in headers:
                table.add_column(header)
            for row in rows:
                styled = list(row)
                status = str(row[status_col])
                style = _STATUS_STYLE.get(status.split()[0])
                if style:
                    styled[status_col] = f"[{style}]{status}[/{style}]"
                table.add_row(*styled)
            Console().print(table)
            return
        except ImportError:
            pass

    _print_plain_table(title, headers, rows)


def _print_plain_table(title: str, headers: list[str], rows: list[list[str]]) -> None:
    typer.echo(title)
    all_rows = [headers, *rows]
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(headers))]

    def fmt(cells: list[str]) -> str:
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))

    typer.echo(fmt(headers))
    typer.echo("  ".join("-" * w for w in widths))
    for row in rows:
        typer.echo(fmt(row))


@contextmanager
def spinner(message: str) -> Iterator[None]:
    """A rich spinner around a blocking operation (e.g. the Makefile-driven
    simulator subprocess) -- a silent no-op when rich output isn't
    appropriate (non-TTY, ``NO_COLOR``, piped/scripted invocation, or rich
    unavailable), so nothing is ever printed that a script parsing stdout
    would have to filter out.
    """
    if use_rich_output():
        try:
            from rich.console import Console
        except ImportError:
            pass
        else:
            with Console().status(message):
                yield
            return
    yield


@contextmanager
def fault_progress(total: int, description: str = "faults") -> Iterator[Callable[[int, int], None] | None]:
    """Context manager yielding a ``(completed, total) -> None`` callback
    suitable for ``algo_engine.run_algo_campaign``/``run_fsm_campaign``'s
    ``progress_callback`` parameter. Renders a rich progress bar when
    appropriate; yields ``None`` otherwise (non-TTY, ``NO_COLOR``, rich
    unavailable, or a campaign too small to bother -- callers pass ``None``
    straight through, which is already that parameter's own no-op default).
    """
    if not use_rich_output() or total <= 1:
        yield None
        return
    try:
        from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
    except ImportError:
        yield None
        return

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    )
    with progress:
        task_id = progress.add_task(description, total=total)

        def _callback(completed: int, grand_total: int) -> None:
            progress.update(task_id, completed=completed)

        yield _callback
