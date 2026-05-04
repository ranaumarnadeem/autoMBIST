from __future__ import annotations

import sys
from pathlib import Path

import typer
import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from autombist import __version__
    from autombist.generator import ConfigError, generate_from_config
    from autombist.runner import SimulationError, run_simulation
else:
    from . import __version__
    from .generator import ConfigError, generate_from_config
    from .runner import SimulationError, run_simulation


app = typer.Typer(
    add_completion=False,
    help="Generate MBIST artifacts, simulate them, and print fault-flow summaries.",
)


def _show_version(value: bool) -> bool:
    if value:
        typer.echo(f"autombist {__version__}")
        raise typer.Exit()
    return value


def _generate(
    config: Path,
    out: Path,
    test: bool,
    faults: int,
    seed: int | None,
    fault_type: str,
    pulse_width_ns: int,
    algo: str,
) -> Path:
    try:
        return generate_from_config(
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


def _simulate(
    config: Path,
    out: Path,
    test: bool,
    faults: int,
    seed: int | None,
    fault_type: str,
    pulse_width_ns: int,
    algo: str,
    verbose: bool,
) -> None:
    try:
        result = run_simulation(
            config,
            out,
            use_saboteur=test,
            faults=faults,
            fault_seed=seed,
            fault_type=fault_type,
            pulse_width_ns=pulse_width_ns,
            algo=algo,
        )
    except (ConfigError, FileNotFoundError, OSError, ValueError, yaml.YAMLError, SimulationError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"autombist: simulation PASS (log: {result.log_path})")
    if verbose and result.stdout:
        typer.echo(result.stdout, nl=False)
    if verbose and result.stderr:
        typer.echo(result.stderr, err=True, nl=False)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_show_version,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    return None


@app.command()
def generate(
    config: Path = typer.Option("config.yml", "--config", help="Config file path"),
    out: Path = typer.Option("out", "--out", help="Base output directory"),
    test: bool = typer.Option(False, "--test/--no-test", help="Enable saboteur test mode and generate fault-sim assets"),
    faults: int = typer.Option(50, "-r", "--faults", help="Number of faults to inject in test mode"),
    seed: int | None = typer.Option(None, "--seed", help="Optional random seed"),
    fault_type: str = typer.Option("stuck-at", "--fault-type", help="Fault type: stuck-at, transition-up, or transition-down"),
    pulse_width_ns: int = typer.Option(2, "--pulse-width-ns", help="Transition pulse width in clock cycles"),
    algo: str = typer.Option("march-c", "--algo", help="Algorithm family: march-c or march-raw"),
) -> None:
    wrapper_path = _generate(config, out, test, faults, seed, fault_type, pulse_width_ns, algo)
    typer.echo(f"Generated MBIST wrapper: {wrapper_path}")
    if test:
        typer.echo(f"Generated fault masks in: {wrapper_path.parent / 'faults'}")
        typer.echo(f"Generated fault-sim Makefile: {wrapper_path.parent / 'Makefile'}")


@app.command()
def simulate(
    config: Path = typer.Option("config.yml", "--config", help="Config file path"),
    out: Path = typer.Option("out", "--out", help="Base output directory"),
    test: bool = typer.Option(False, "--test/--no-test", help="Run the faulted simulation flow"),
    faults: int = typer.Option(50, "-r", "--faults", help="Number of faults to inject in test mode"),
    seed: int | None = typer.Option(None, "--seed", help="Optional random seed"),
    fault_type: str = typer.Option("stuck-at", "--fault-type", help="Fault type: stuck-at, transition-up, or transition-down"),
    pulse_width_ns: int = typer.Option(2, "--pulse-width-ns", help="Transition pulse width in clock cycles"),
    algo: str = typer.Option("march-c", "--algo", help="Algorithm family: march-c or march-raw"),
    verbose: bool = typer.Option(False, "--verbose/--quiet", help="Print the full simulator output"),
) -> None:
    _simulate(config, out, test, faults, seed, fault_type, pulse_width_ns, algo, verbose)


@app.command()
def run(
    config: Path = typer.Option("config.yml", "--config", help="Config file path"),
    out: Path = typer.Option("out", "--out", help="Base output directory"),
    test: bool = typer.Option(False, "--test/--no-test", help="Run the faulted generation and simulation flow"),
    faults: int = typer.Option(50, "-r", "--faults", help="Number of faults to inject in test mode"),
    seed: int | None = typer.Option(None, "--seed", help="Optional random seed"),
    fault_type: str = typer.Option("stuck-at", "--fault-type", help="Fault type: stuck-at, transition-up, or transition-down"),
    pulse_width_ns: int = typer.Option(2, "--pulse-width-ns", help="Transition pulse width in clock cycles"),
    algo: str = typer.Option("march-c", "--algo", help="Algorithm family: march-c or march-raw"),
    verbose: bool = typer.Option(False, "--verbose/--quiet", help="Print the full simulator output"),
) -> None:
    wrapper_path = _generate(config, out, test, faults, seed, fault_type, pulse_width_ns, algo)
    typer.echo(f"Generated MBIST wrapper: {wrapper_path}")
    if test:
        typer.echo(f"Generated fault masks in: {wrapper_path.parent / 'faults'}")
        typer.echo(f"Generated fault-sim Makefile: {wrapper_path.parent / 'Makefile'}")
    _simulate(config, out, test, faults, seed, fault_type, pulse_width_ns, algo, verbose)