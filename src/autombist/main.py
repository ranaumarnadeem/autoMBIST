from __future__ import annotations

from pathlib import Path

import typer
import yaml

from . import __version__
from .generator import ConfigError, generate_from_config

app = typer.Typer(add_completion=False, help="Generate MBIST artifacts and optional fault flow.")


def _show_version(value: bool) -> bool:
    if value:
        typer.echo(f"autombist {__version__}")
        raise typer.Exit()
    return value


@app.callback(invoke_without_command=True)
def main(
    config: Path = typer.Option("config.yml", "--config", help="Config file path"),
    out: Path = typer.Option("out", "--out", help="Base output directory"),
    test: bool = typer.Option(False, "--test/--no-test", help="Enable saboteur test mode"),
    faults: int = typer.Option(50, "-r", "--faults", help="Number of faults in test mode"),
    seed: int | None = typer.Option(None, "--seed", help="Optional random seed"),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_show_version,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    try:
        wrapper_path = generate_from_config(
            config,
            out,
            use_saboteur=test,
            faults=faults,
            fault_seed=seed,
        )
    except (ConfigError, FileNotFoundError, OSError, ValueError, yaml.YAMLError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"Generated MBIST wrapper: {wrapper_path}")
    if test:
        typer.echo(f"Generated fault masks in: {wrapper_path.parent / 'faults'}")
        typer.echo(f"Generated fault-sim Makefile: {wrapper_path.parent / 'Makefile'}")
