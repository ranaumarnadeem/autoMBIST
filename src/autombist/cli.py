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
    help="autombist: MBIST wrapper and fault-injection tool for OpenRAM SRAM macros.\n\nUse 'autombist COMMAND --help' for command-specific usage (e.g., 'autombist generate --help').",
)


def _resolve_config_path(config: Path | None) -> Path:
    if config is not None:
        return config

    default_config = Path.cwd() / "config.yml"
    if default_config.exists():
        return default_config

    raise FileNotFoundError("Config file not found. Pass --config PATH or create config.yml in the current working directory.")


def _resolve_module_outdir(out: Path) -> Path:
    has_wrapper = any(out.glob("*_mbist.v"))
    has_config_snapshot = (out / "config.yml").exists()
    has_fault_makefile = (out / "Makefile").exists()

    if has_wrapper and (has_config_snapshot or has_fault_makefile):
        return out

    if not out.exists():
        raise FileNotFoundError(f"Output directory not found: {out}")

    candidates = [
        path
        for path in out.iterdir()
        if path.is_dir()
        and any(path.glob("*_mbist.v"))
        and ((path / "config.yml").exists() or (path / "Makefile").exists())
    ]
    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) > 1:
        candidate_list = ", ".join(path.name for path in candidates)
        raise FileNotFoundError(f"Multiple generated memory directories found under {out}: {candidate_list}. Pass the specific memory directory.")

    raise FileNotFoundError(f"No generated autombist output found under {out}. Run 'autombist generate' first.")


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


def _simulate(module_outdir: Path, verbose: bool) -> None:
    try:
        result = run_simulation(module_outdir, verbose=verbose)
    except (ConfigError, FileNotFoundError, OSError, ValueError, yaml.YAMLError, SimulationError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    from autombist.reporting import format_simulation_summary

    typer.echo(format_simulation_summary(result.report))
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
    config: Path | None = typer.Option(None, "--config", help="YAML config file with memory parameters (defaults to ./config.yml if it exists)"),
    out: Path = typer.Option("out", "--out", help="Output directory where generated files will be written"),
    test: bool = typer.Option(False, "--test/--no-test", help="Generate fault-injection saboteur wrapper and fault masks (use with --faults)"),
    faults: int = typer.Option(50, "-r", "--faults", help="Number of random faults to inject (only with --test)"),
    seed: int | None = typer.Option(None, "--seed", help="Random seed for reproducible fault injection (optional)"),
    fault_type: str = typer.Option("stuck-at", "--fault-type", help="Fault model: stuck-at (SA0/SA1), transition-up, or transition-down"),
    pulse_width_ns: int = typer.Option(2, "--pulse-width-ns", help="Pulse width in clock cycles for transition faults"),
    algo: str = typer.Option("march-c", "--algo", help="MBIST algorithm: march-c or march-raw"),
) -> None:
    """Generate MBIST wrapper, RTL, and optionally fault masks.

    This command creates SystemVerilog wrapper modules and copies the MBIST
    algorithm RTL into the output directory. Optionally generates fault masks
    for simulation with the --test flag.

    Output: out/<memory_name>/
      - <memory_name>_mbist.v (main wrapper)
      - mbist_algo.sv, mbist_fsm.sv, mbist_top.sv (core MBIST RTL)
      - march_c/ or march_raw/ (algorithm-specific files)
      - [with --test] <memory_name>_saboteur.v (fault injection wrapper)
      - [with --test] faults/*.hex (fault masks)
      - [with --test] Makefile (for running simulation)

    Examples:
      autombist generate --config config.yml
      autombist generate --config my_sram.yml --out results --algo march-raw
      autombist generate --config config.yml --test --faults 100 --seed 42
      autombist generate  # uses ./config.yml when present
    """

    config = _resolve_config_path(config)
    wrapper_path = _generate(config, out, test, faults, seed, fault_type, pulse_width_ns, algo)
    typer.echo(f"Generated MBIST wrapper: {wrapper_path}")
    if test:
        typer.echo(f"Generated fault masks in: {wrapper_path.parent / 'faults'}")
        typer.echo(f"Generated fault-sim Makefile: {wrapper_path.parent / 'Makefile'}")


@app.command()
def simulate(
    out: Path = typer.Option("out", "--out", help="Output directory containing generated autombist output"),
    verbose: bool = typer.Option(False, "--verbose", help="Print full simulator console output and detailed logs"),
) -> None:
    """Run MBIST simulation using Cocotb and Icarus Verilog.

    This command runs the simulation using the generated output directory.
    If the output directory contains a generated fault Makefile, it runs the
    fault simulation path. Otherwise it runs the clean simulation path.
    Outputs a JSON report and terminal summary to out/<memory_name>/reports/.

    Requirements:
      - Run 'autombist generate' first to create the wrapper
      - Icarus Verilog (iverilog) and Cocotb must be installed

    Output:
      - out/<memory_name>/simulate.log (full simulator output)
      - out/<memory_name>/reports/latest.json (structured results)
      - Terminal summary with coverage metrics

    Examples:
      autombist simulate --out out
      autombist simulate --out out --verbose
      autombist simulate --out out/input_demo_8x16_scn4m
    """

    try:
        module_outdir = _resolve_module_outdir(out)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    _simulate(module_outdir, verbose)


@app.command()
def run(
    config: Path | None = typer.Option(None, "--config", help="YAML config file with memory parameters (defaults to ./config.yml if it exists)"),
    out: Path = typer.Option("out", "--out", help="Output directory for all generated files and results"),
    test: bool = typer.Option(False, "--test/--no-test", help="Generate and run fault injection simulation"),
    faults: int = typer.Option(50, "-r", "--faults", help="Number of faults to inject"),
    seed: int | None = typer.Option(None, "--seed", help="Random seed for reproducible fault injection"),
    fault_type: str = typer.Option("stuck-at", "--fault-type", help="Fault model: stuck-at, transition-up, or transition-down"),
    pulse_width_ns: int = typer.Option(2, "--pulse-width-ns", help="Pulse width in clock cycles for transition faults"),
    algo: str = typer.Option("march-c", "--algo", help="MBIST algorithm: march-c or march-raw"),
    verbose: bool = typer.Option(False, "--verbose", help="Print full simulator console output and detailed logs"),
) -> None:
    """Generate wrapper AND run simulation in one command (convenience mode).

    This is equivalent to running 'generate' followed by 'simulate'.
    Useful for quick end-to-end validation or batch processing.

    Output:
      - out/<memory_name>/ (all generated wrapper and RTL files)
      - out/<memory_name>/reports/latest.json (simulation results)
      - out/<memory_name>/simulate.log (simulator output)

    Requirements:
      - Icarus Verilog and Cocotb must be installed

    Examples:
      autombist run --config config.yml --test
      autombist run --config config.yml --test --faults 200 --algo march-raw --seed 999
      autombist run --config config.yml --test --fault-type transition-up --verbose
      autombist run  # uses ./config.yml when present
    """

    config = _resolve_config_path(config)
    wrapper_path = _generate(config, out, test, faults, seed, fault_type, pulse_width_ns, algo)
    typer.echo(f"Generated MBIST wrapper: {wrapper_path}")
    if test:
        typer.echo(f"Generated fault masks in: {wrapper_path.parent / 'faults'}")
        typer.echo(f"Generated fault-sim Makefile: {wrapper_path.parent / 'Makefile'}")
    _simulate(wrapper_path.parent, verbose)