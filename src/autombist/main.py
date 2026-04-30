from __future__ import annotations

import sys
from pathlib import Path

import typer
import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from autombist import __version__
    from autombist.generator import ConfigError, generate_from_config
else:
    from . import __version__
    from .generator import ConfigError, generate_from_config

app = typer.Typer(
    add_completion=False,
    help="Generate MBIST artifacts, algorithm-specific RTL, and optional fault simulation flow.",
)


def _show_version(value: bool) -> bool:
    if value:
        typer.echo(f"autombist {__version__}")
        raise typer.Exit()
    return value


@app.callback(invoke_without_command=True)
def main(
    config: Path = typer.Option("config.yml", "--config", help="Config file path"),
    out: Path = typer.Option("out", "--out", help="Base output directory"),
    test: bool = typer.Option(False, "--test/--no-test", help="Enable saboteur test mode and generate fault-sim assets"),
    faults: int = typer.Option(50, "-r", "--faults", help="Number of faults to inject in test mode"),
    seed: int | None = typer.Option(None, "--seed", help="Optional random seed"),
    fault_type: str = typer.Option("stuck-at", "--fault-type", help="Fault type: stuck-at, transition-up, or transition-down"),
    pulse_width_ns: int = typer.Option(2, "--pulse-width-ns", help="Transition pulse width in clock cycles"),
    algo: str = typer.Option("march-c", "--algo", help="Algorithm family: march-c or march-raw"),
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
            fault_type=fault_type,
            pulse_width_ns=pulse_width_ns,
            algo=algo,
        )
    except (ConfigError, FileNotFoundError, OSError, ValueError, yaml.YAMLError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"Generated MBIST wrapper: {wrapper_path}")
    if test:
        typer.echo(f"Generated fault masks in: {wrapper_path.parent / 'faults'}")
        typer.echo(f"Generated fault-sim Makefile: {wrapper_path.parent / 'Makefile'}")


if __name__ == "__main__":
    app()
