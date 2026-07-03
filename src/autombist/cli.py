from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import typer
import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from autombist import __version__
    from autombist.generator import ConfigError, generate_from_config
    from autombist.openram_flow import (
        OpenRAMConfigError,
        build_openram_command_args,
        default_openram_config,
        load_openram_config,
        run_openram_synthesis,
    )
    from autombist.runner import SimulationError, run_simulation
else:
    from . import __version__
    from .generator import ConfigError, generate_from_config
    from .openram_flow import (
        OpenRAMConfigError,
        build_openram_command_args,
        default_openram_config,
        load_openram_config,
        run_openram_synthesis,
    )
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


def _default_mbist_config() -> dict[str, object]:
    return {
        "memory_name": "sram_1rw",
        "wrapper_module_name": "sram_1rw_mbist",
        "addr_width": 10,
        "data_width": 32,
        "we_active_low": True,
        "ports": {
            "clk": "clk0",
            "addr": "addr0",
            "din": "din0",
            "dout": "dout0",
            "we": "we0",
            "csb": "csb0",
        },
    }


def _default_project_makefile_text() -> str:
    return "\n".join(
        [
            "AUTOMBIST ?= autombist",
            "OUT ?= out",
            "MBIST_CONFIG ?= config.yml",
            "OPENRAM_CONFIG ?= openram.yml",
            "",
            ".PHONY: ram-synth generate simulate run smoke",
            "",
            "ram-synth:",
            "\t$(AUTOMBIST) ram-synth --config $(OPENRAM_CONFIG)",
            "",
            "generate:",
            "\t$(AUTOMBIST) generate --config $(MBIST_CONFIG) --out $(OUT)",
            "",
            "simulate:",
            "\t$(AUTOMBIST) simulate --out $(OUT)",
            "",
            "run:",
            "\t$(AUTOMBIST) run --config $(MBIST_CONFIG) --out $(OUT) --test",
            "",
            "smoke:",
            "\t$(AUTOMBIST) smoke",
            "",
        ]
    )


def _write_file_with_overwrite_guard(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path}. Use --force to overwrite.")
    path.write_text(content, encoding="utf-8")


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


def _simulate(module_outdir: Path, verbose: bool, min_coverage: float | None = None) -> None:
    try:
        result = run_simulation(module_outdir, verbose=verbose)
    except (ConfigError, FileNotFoundError, OSError, ValueError, yaml.YAMLError, SimulationError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    from autombist.reporting import coverage_meets_threshold, format_simulation_summary

    typer.echo(format_simulation_summary(result.report))
    if verbose and result.stdout:
        typer.echo(result.stdout, nl=False)
    if verbose and result.stderr:
        typer.echo(result.stderr, err=True, nl=False)

    ok, coverage = coverage_meets_threshold(result.report, min_coverage)
    if not ok:
        typer.secho(
            f"autombist: coverage {coverage:.2f}% is below --min-coverage {min_coverage:.2f}%",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)


def _build_faultflow_options(
    faultflow_repo: Path | None,
    cell_lib: str,
    scan_chains: int,
    threshold: float,
    max_rounds: int,
):
    from autombist.faultflow_flow import FaultFlowOptions

    return FaultFlowOptions(
        repo=faultflow_repo,
        cell_lib=cell_lib,
        scan_chains=scan_chains,
        threshold=threshold,
        max_rounds=max_rounds,
    )


def _grade_controller(module_outdir: Path, opts, run: bool) -> None:
    import json

    from autombist.faultflow_flow import FaultFlowError
    from autombist.reporting import merge_faultflow_coverage, write_simulation_report
    from autombist.runner import run_controller_grading

    bundle = module_outdir / "faultflow"
    try:
        coverage = run_controller_grading(module_outdir, opts, run=run)
    except (FaultFlowError, ConfigError, FileNotFoundError, OSError, ValueError, yaml.YAMLError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if not run:
        typer.echo(f"Emitted FaultFlow bundle: {bundle}")
        typer.echo(f"  Run on Linux/WSL:  FAULTFLOW_HOME=<path> bash {bundle / 'run_faultflow.sh'}")
        return

    report_path = module_outdir / "reports" / "latest.json"
    if coverage and report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            merge_faultflow_coverage(report, coverage)
            write_simulation_report(report, module_outdir / "reports")
        except (OSError, ValueError):
            pass

    coverage_percent = coverage.get("coverage_percent") if coverage else None
    if isinstance(coverage_percent, (int, float)):
        typer.echo(
            "Controller structural coverage (FaultFlow): "
            f"{coverage.get('detected')}/{coverage.get('denominator')} ({coverage_percent:.2f}%), "
            f"excluded-blackbox={coverage.get('excluded_blackbox')}"
        )
    else:
        typer.echo(f"Controller grading complete. Bundle: {bundle}")


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
    fault_type: str = typer.Option("stuck-at", "--fault-type", help="Fault model: stuck-at (SA0/SA1), transition-up, transition-down, or port-coupling (march-1r1w only; march-2rw supports stuck-at/transition only)"),
    pulse_width_ns: int = typer.Option(2, "--pulse-width-ns", help="Pulse width in clock cycles for transition faults"),
    algo: str = typer.Option("march-c", "--algo", help="MBIST algorithm: march-c, march-raw, march-1r1w, or march-2rw"),
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
    min_coverage: float | None = typer.Option(None, "--min-coverage", help="Fail (exit 1) if array fault coverage is below this percent"),
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
            - out/<memory_name>/reports/report.txt (plain-text human report)
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

    _simulate(module_outdir, verbose, min_coverage)


@app.command()
def run(
    config: Path | None = typer.Option(None, "--config", help="YAML config file with memory parameters (defaults to ./config.yml if it exists)"),
    out: Path = typer.Option("out", "--out", help="Output directory for all generated files and results"),
    test: bool = typer.Option(False, "--test/--no-test", help="Generate and run fault injection simulation"),
    faults: int = typer.Option(50, "-r", "--faults", help="Number of faults to inject"),
    seed: int | None = typer.Option(None, "--seed", help="Random seed for reproducible fault injection"),
    fault_type: str = typer.Option("stuck-at", "--fault-type", help="Fault model: stuck-at, transition-up, transition-down, or port-coupling (march-1r1w only; march-2rw supports stuck-at/transition only)"),
    pulse_width_ns: int = typer.Option(2, "--pulse-width-ns", help="Pulse width in clock cycles for transition faults"),
    algo: str = typer.Option("march-c", "--algo", help="MBIST algorithm: march-c, march-raw, march-1r1w, or march-2rw"),
    verbose: bool = typer.Option(False, "--verbose", help="Print full simulator console output and detailed logs"),
    faultflow: bool = typer.Option(False, "--faultflow/--no-faultflow", help="After sim, grade the MBIST controller logic with FaultFlow (Linux/WSL)"),
    faultflow_repo: Path | None = typer.Option(None, "--faultflow-repo", envvar="FAULTFLOW_HOME", help="FaultFlow repo path (or set FAULTFLOW_HOME)"),
    cell_lib: str = typer.Option("sky130", "--cell-lib", help="FaultFlow standard-cell library: sky130 or osu035"),
    scan_chains: int = typer.Option(1, "--scan-chains", help="Scan chains for controller grading"),
    min_coverage: float | None = typer.Option(None, "--min-coverage", help="Fail (exit 1) if array fault coverage is below this percent"),
) -> None:
    """Generate wrapper AND run simulation in one command (convenience mode).

    This is equivalent to running 'generate' followed by 'simulate'.
    Useful for quick end-to-end validation or batch processing.

    Output:
      - out/<memory_name>/ (all generated wrapper and RTL files)
      - out/<memory_name>/reports/latest.json (simulation results)
            - out/<memory_name>/reports/report.txt (plain-text human report)
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
    if faultflow:
        opts = _build_faultflow_options(faultflow_repo, cell_lib, scan_chains, 90.0, 20)
        _grade_controller(wrapper_path.parent, opts, run=True)


@app.command("grade-controller")
def grade_controller(
    out: Path = typer.Option("out", "--out", help="Output directory containing a generated (clean) MBIST wrapper"),
    faultflow_repo: Path | None = typer.Option(None, "--faultflow-repo", envvar="FAULTFLOW_HOME", help="FaultFlow repo path (or set FAULTFLOW_HOME)"),
    cell_lib: str = typer.Option("sky130", "--cell-lib", help="FaultFlow standard-cell library: sky130 or osu035"),
    scan_chains: int = typer.Option(1, "--scan-chains", help="Number of scan chains for controller grading"),
    threshold: float = typer.Option(90.0, "--threshold", help="Target coverage percent for ATPG"),
    max_rounds: int = typer.Option(20, "--max-rounds", help="Maximum progressive ATPG rounds"),
    run: bool = typer.Option(True, "--run/--no-run", help="Run the bundle (needs Yosys + FaultFlow); --no-run only emits it"),
) -> None:
    """Grade the MBIST controller logic with FaultFlow (memory macro blackboxed).

    Emits a self-contained, re-runnable bundle under out/<memory>/faultflow/
    (blackbox stub, Yosys script, FaultFlow .ofs, run_faultflow.sh) and, unless
    --no-run is given, synthesizes the collar and runs scan stuck-at ATPG, then
    reports controller structural coverage and merges it into the latest report.

    Requirements (Linux/WSL): Yosys, and a built FaultFlow at --faultflow-repo
    (or $FAULTFLOW_HOME). FaultFlow is invoked from its own venv.

    Examples:
      autombist grade-controller --out out --faultflow-repo ~/faultflow
      autombist grade-controller --out out --no-run     # just emit the bundle
    """

    try:
        module_outdir = _resolve_module_outdir(out)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    opts = _build_faultflow_options(faultflow_repo, cell_lib, scan_chains, threshold, max_rounds)
    _grade_controller(module_outdir, opts, run)


@app.command()
def test(
    addr_width: int = typer.Option(..., "--addr-width", "-aw", help="Memory address width in bits"),
    data_width: int = typer.Option(..., "--data-width", "-dw", help="Memory data width in bits"),
    algo: str = typer.Option("march_c", "--algo", help="Built-in algorithm name (march_c, mats_plus, march_ss, march_x) or a path to a .alg file"),
    fsm: Path | None = typer.Option(None, "--fsm", help="Validate a controller FSM .sv instead of an algorithm (takes precedence over --algo); sibling .sv/.v files in its directory are gathered automatically"),
    faults: Path = typer.Option(..., "--faults", help="Fault-list file: 'TYPE VADDR VBIT AADDR ABIT P0 P1' per line"),
    fault_types: Path | None = typer.Option(None, "--fault-types", help="JSON file with a list of custom fault-primitive specs, added to the built-in 19 (see fault_primitives.py for the schema)"),
    init: int = typer.Option(1, "--init", help="Memory init value (0 or 1)"),
    sim: str = typer.Option("verilator", "--sim", help="Simulator backend (Verilator only; Icarus cannot run the SV fault engine)"),
    verbose: bool = typer.Option(False, "--verbose", help="Print per-fault activation counts (+FAULT_VERBOSE)"),
    report: Path | None = typer.Option(None, "--report", help="Write a per-fault coverage report to this path"),
    fmt: str = typer.Option("md", "--fmt", help="Report format: md, csv, or json"),
    min_coverage: float | None = typer.Option(None, "--min-coverage", help="Fail (exit 1) if coverage is below this percent"),
) -> None:
    """Grade a memory against a functional fault library with an MBIST algorithm.

    Compiles the fault-injectable RAM model once (Verilator), runs a golden pass,
    then one simulation per fault in the list, and reports detection coverage.
    This models 19 functional fault primitives (stuck-at, transition, write/read
    disturb, address-decoder, and all four coupling classes) -- richer than the
    stuck-at/transition mask faults used by `autombist generate --test`. Pass
    --fsm to validate an actual controller (bist_fail) instead of an algorithm
    spec -- no elem/op attribution in that mode, since a black-box controller
    has no step counter to report.

    Examples:
      autombist test --addr-width 8 --data-width 8 --algo march_c --faults faults.txt
      autombist test -aw 10 -dw 32 --algo march_ss --faults faults.txt --verbose
      autombist test -aw 8 -dw 8 --algo my_algo.alg --faults faults.txt --report cov.md
      autombist test -aw 8 -dw 8 --algo march_c --faults faults.txt --min-coverage 90
      autombist test -aw 10 -dw 32 --fsm rtl/march_c/march_c_top.sv --faults faults.txt
      autombist test -aw 8 -dw 8 --algo march_ss --faults faults.txt --fault-types mytypes.json
    """

    import json

    from autombist.alg_spec import AlgSpecError, resolve_algo
    from autombist.algo_engine import CampaignError, MemoryParams, load_fault_list, run_algo_campaign, run_fsm_campaign
    from autombist.algo_reporting import coverage_meets_threshold, write_campaign_report
    from autombist.fault_primitives import FaultPrimitiveError
    from autombist.fault_primitives import default_registry as fp_default_registry
    from autombist.fault_primitives import from_dict as fp_from_dict
    from autombist.fault_primitives import validate as fp_validate
    from autombist.fault_ram_gen import render_and_write
    from autombist.fsm_harness import FsmPortError, check_ports, gather_sibling_sources

    try:
        records = load_fault_list(faults)
        mem = MemoryParams(addr_width=addr_width, data_width=data_width, init_val=init)

        fault_ram_sv = None
        if fault_types is not None:
            specs = json.loads(fault_types.read_text(encoding="utf-8"))
            if not isinstance(specs, list):
                raise ValueError("--fault-types file must contain a JSON list of fault-primitive specs")
            registry = fp_default_registry()
            for spec_dict in specs:
                prim = fp_from_dict(spec_dict)
                fp_validate(prim, existing_names={p.name for p in registry})
                registry.append(prim)
            fault_ram_sv = render_and_write(
                registry, Path(tempfile.mkdtemp(prefix="autombist-test-types-")) / "fault_ram.sv"
            )

        if fsm is not None:
            sources = gather_sibling_sources(fsm)
            ports = check_ports(fsm.read_text(encoding="utf-8"))
            result = run_fsm_campaign(mem, sources, ports.module_name, records, sim=sim, fault_ram_sv=fault_ram_sv)
            label = f"FSM:{ports.module_name} ({len(sources)} source file(s))"
        else:
            spec = resolve_algo(algo)
            result = run_algo_campaign(mem, spec, records, sim=sim, verbose=verbose, fault_ram_sv=fault_ram_sv)
            label = f"{spec.name} ({spec.length_n}n)"
        if report is not None:
            write_campaign_report(result, report, fmt=fmt)
    except (AlgSpecError, CampaignError, FsmPortError, FaultPrimitiveError, FileNotFoundError, OSError, ValueError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"autombist test: {label} on {addr_width}x{data_width} memory, init={init}")
    typer.echo(f"  faults: {result.total}   detected: {result.detected}   coverage: {result.coverage_percent:.2f}%")
    typer.echo(f"  build: {result.build_seconds:.2f}s   run: {result.run_seconds:.2f}s   sim: {result.sim}")
    if report is not None:
        typer.echo(f"  report: {report}")

    if not coverage_meets_threshold(result, min_coverage):
        typer.secho(
            f"autombist: coverage {result.coverage_percent:.2f}% is below --min-coverage {min_coverage:.2f}%",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)


@app.command()
def algo(
    script: Path | None = typer.Option(None, "--script", help="Run commands from a file (or '-' for stdin) instead of an interactive prompt"),
) -> None:
    """Launch the interactive MBIST algorithm research shell.

    Register algorithms (add_algo) and fault instances (add_fault/load_faults/
    gen_faults), run a campaign (run), compare against built-in marches
    (compare_algo), and export a report (write_report) or a standalone
    testbench bundle (export_tb). Built-in algorithms (march_c, mats_plus,
    march_ss, march_x) are preloaded. Type 'help' inside the shell for the
    full command list.

    Examples:
      autombist algo
      autombist algo --script session.algo
      printf 'set_memory 8 8\\nrun march_c\\nquit\\n' | autombist algo --script -
    """

    from autombist.algo_shell import AlgoShell, Session

    shell = AlgoShell(Session())
    if script is None:
        shell.cmdloop()
        return

    lines = sys.stdin.read().splitlines() if str(script) == "-" else script.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if shell.onecmd(shell.precmd(line)):
            break


@app.command("ram-synth")
def ram_synth(
    config: Path = typer.Option("openram.yml", "--config", help="OpenRAM synthesis config file"),
    show_command: bool = typer.Option(False, "--show-command", help="Print synthesized command before execution"),
) -> None:
    """Synthesize an SRAM macro through OpenRAM using YAML config.

    This command reuses the existing OpenRAM synthesis helper under scripts/
    and lets users pass dimensions/settings through a config file instead of
    manually writing command-line arguments.
    """

    try:
        resolved = config if config.is_absolute() else (Path.cwd() / config).resolve()
        cfg = load_openram_config(resolved)
        if show_command:
            cmd = build_openram_command_args(cfg, resolved)
            typer.echo("$ " + " ".join(str(token) for token in cmd))
        result = run_openram_synthesis(resolved)
    except (FileNotFoundError, OpenRAMConfigError, OSError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if result.stdout:
        typer.echo(result.stdout, nl=False)
    if result.stderr:
        typer.echo(result.stderr, err=True, nl=False)
    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)


def _default_sram_model_text() -> str:
    """Return a sample SRAM model matching the default config ports."""
    return "\n".join([
        "// Sample SRAM model for autombist",
        "// Replace this with your OpenRAM-generated SRAM macro",
        "`timescale 1ns/1ps",
        "",
        "module sram_1rw #(",
        "    parameter integer ADDR_WIDTH = 10,",
        "    parameter integer DATA_WIDTH = 32",
        ") (",
        "    input  wire                  clk0,",
        "    input  wire                  csb0,",
        "    input  wire [ADDR_WIDTH-1:0] addr0,",
        "    input  wire [DATA_WIDTH-1:0] din0,",
        "    input  wire                  we0,",
        "    output reg [DATA_WIDTH-1:0]  dout0",
        ");",
        "",
        "    localparam integer DEPTH = (1 << ADDR_WIDTH);",
        "",
        "    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];",
        "",
        "    reg                  csb0_q;",
        "    reg                  we0_q;",
        "    reg [ADDR_WIDTH-1:0] addr0_q;",
        "",
        "    always @(posedge clk0) begin",
        "        csb0_q  <= csb0;",
        "        we0_q   <= we0;",
        "        addr0_q <= addr0;",
        "",
        "        if (!csb0 && !we0) begin",
        "            mem[addr0] <= din0;",
        "        end",
        "",
        "        if (!csb0_q && we0_q) begin",
        "            dout0 <= mem[addr0_q];",
        "        end",
        "    end",
        "",
        "endmodule",
        "",
    ])


@app.command()
def init(
    out: Path = typer.Option(".", "--out", help="Directory where starter files will be created"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
) -> None:
    """Create starter config.yml, openram.yml, Makefile, and sample SRAM model.

    Generates a complete starter project that can immediately be used
    with 'autombist generate' and 'autombist simulate'.

    Examples:
      autombist init
      autombist init --out my_project
      autombist init --force
    """

    target_dir = out if out.is_absolute() else (Path.cwd() / out).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    mbist_config_path = target_dir / "config.yml"
    openram_config_path = target_dir / "openram.yml"
    makefile_path = target_dir / "Makefile"
    sram_model_path = target_dir / "sram_1rw.v"

    try:
        _write_file_with_overwrite_guard(
            mbist_config_path,
            yaml.safe_dump(_default_mbist_config(), sort_keys=False),
            force=force,
        )
        _write_file_with_overwrite_guard(
            openram_config_path,
            yaml.safe_dump(default_openram_config(), sort_keys=False),
            force=force,
        )
        _write_file_with_overwrite_guard(
            makefile_path,
            _default_project_makefile_text(),
            force=force,
        )
        _write_file_with_overwrite_guard(
            sram_model_path,
            _default_sram_model_text(),
            force=force,
        )
    except (FileExistsError, OSError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"Created starter MBIST config: {mbist_config_path}")
    typer.echo(f"Created starter OpenRAM config: {openram_config_path}")
    typer.echo(f"Created starter Makefile: {makefile_path}")
    typer.echo(f"Created sample SRAM model: {sram_model_path}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo("  1. Edit config.yml to match your SRAM macro")
    typer.echo("  2. Replace sram_1rw.v with your OpenRAM-generated SRAM")
    typer.echo("  3. Run: autombist generate --config config.yml")


def _assert_smoke_file(path: Path, label: str) -> None:
    """Fail the smoke run if an expected file is missing."""
    if not path.exists():
        typer.secho(f"[smoke] FAIL: expected {label} at {path}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command()
def smoke(
    run_sim: bool = typer.Option(True, "--run-sim/--no-sim", help="Run cocotb/iverilog simulation smoke check"),
    keep_artifacts: bool = typer.Option(False, "--keep-artifacts", help="Keep generated smoke workspace"),
    out: Path | None = typer.Option(None, "--out", help="Optional workspace path for smoke artifacts"),
    faultflow: bool = typer.Option(False, "--faultflow/--no-faultflow", help="Also emit + verify a FaultFlow controller-grading bundle (emit-only; no Yosys/FaultFlow needed)"),
) -> None:
    """Run smoke checks to verify generation modes and optional fault simulation.

    Exercises all generation modes (clean, stuck-at, transition-up,
    transition-down) with both march-c and march-raw algorithms.
    Validates expected output artifacts exist after each generation step.
    Optionally runs small fault-injection simulations so coverage
    reporting is exercised for stuck-at and transition fault modes.

    Examples:
      autombist smoke
      autombist smoke --no-sim
      autombist smoke --keep-artifacts --out smoke_workspace
    """

    temp_ctx: tempfile.TemporaryDirectory[str] | None = None
    try:
        if out is None:
            if keep_artifacts:
                workspace = Path(tempfile.mkdtemp(prefix="autombist-smoke-"))
            else:
                temp_ctx = tempfile.TemporaryDirectory(prefix="autombist-smoke-")
                workspace = Path(temp_ctx.name)
        else:
            workspace = out if out.is_absolute() else (Path.cwd() / out).resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        smoke_config_path = workspace / "config.yml"
        smoke_openram_config = workspace / "openram.yml"
        smoke_out = workspace / "out"
        memory_name = str(_default_mbist_config()["memory_name"])

        smoke_config_path.write_text(
            yaml.safe_dump(_default_mbist_config(), sort_keys=False),
            encoding="utf-8",
        )
        smoke_openram_config.write_text(
            yaml.safe_dump(default_openram_config(), sort_keys=False),
            encoding="utf-8",
        )

        # --- 1. Clean generation (march-c) ---
        wrapper_path = _generate(
            smoke_config_path, smoke_out,
            test=False, faults=0, seed=None,
            fault_type="stuck-at", pulse_width_ns=2, algo="march-c",
        )
        _assert_smoke_file(wrapper_path, "wrapper (clean, march-c)")
        _assert_smoke_file(wrapper_path.parent / "config.yml", "config snapshot")
        typer.echo("[smoke] generate (clean, march-c): PASS")

        # --- 2. Clean generation (march-raw) ---
        wrapper_path = _generate(
            smoke_config_path, smoke_out,
            test=False, faults=0, seed=None,
            fault_type="stuck-at", pulse_width_ns=2, algo="march-raw",
        )
        _assert_smoke_file(wrapper_path, "wrapper (clean, march-raw)")
        wrapper_text = wrapper_path.read_text(encoding="utf-8")
        if "march_raw_top" not in wrapper_text:
            typer.secho("[smoke] FAIL: wrapper missing march_raw_top", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.echo("[smoke] generate (clean, march-raw): PASS")

        smoke_faults = 8
        smoke_seed = 42

        # --- 3. Stuck-at fault generation ---
        wrapper_path = _generate(
            smoke_config_path, smoke_out,
            test=True, faults=smoke_faults, seed=smoke_seed,
            fault_type="stuck-at", pulse_width_ns=2, algo="march-c",
        )
        module_outdir = wrapper_path.parent
        _assert_smoke_file(module_outdir / "faults" / "sa0_faults.hex", "sa0_faults.hex")
        _assert_smoke_file(module_outdir / "faults" / "sa1_faults.hex", "sa1_faults.hex")
        _assert_smoke_file(module_outdir / f"{memory_name}_saboteur.v", "saboteur (stuck-at)")
        _assert_smoke_file(module_outdir / "Makefile", "fault Makefile")
        typer.echo("[smoke] generate (stuck-at, march-c): PASS")

        # --- 4. Transition-up fault generation (march-raw) ---
        wrapper_path = _generate(
            smoke_config_path, smoke_out,
            test=True, faults=smoke_faults, seed=smoke_seed,
            fault_type="transition-up", pulse_width_ns=2, algo="march-raw",
        )
        module_outdir = wrapper_path.parent
        _assert_smoke_file(module_outdir / "faults" / "tf_up_faults.hex", "tf_up_faults.hex")
        saboteur_text = (module_outdir / f"{memory_name}_saboteur.v").read_text(encoding="utf-8")
        if "tf_up_mask" not in saboteur_text:
            typer.secho("[smoke] FAIL: saboteur missing tf_up_mask", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.echo("[smoke] generate (transition-up, march-raw): PASS")

        # --- 5. Transition-down fault generation (march-raw) ---
        wrapper_path = _generate(
            smoke_config_path, smoke_out,
            test=True, faults=smoke_faults, seed=smoke_seed,
            fault_type="transition-down", pulse_width_ns=3, algo="march-raw",
        )
        module_outdir = wrapper_path.parent
        _assert_smoke_file(module_outdir / "faults" / "tf_down_faults.hex", "tf_down_faults.hex")
        saboteur_text = (module_outdir / f"{memory_name}_saboteur.v").read_text(encoding="utf-8")
        if "tf_down_mask" not in saboteur_text:
            typer.secho("[smoke] FAIL: saboteur missing tf_down_mask", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.echo("[smoke] generate (transition-down, march-raw): PASS")

        # --- 6. OpenRAM config parse ---
        try:
            cfg = load_openram_config(smoke_openram_config)
            build_openram_command_args(cfg, smoke_openram_config)
            typer.echo("[smoke] ram-synth config parse: PASS")
        except (FileNotFoundError, OpenRAMConfigError, OSError) as exc:
            typer.secho(f"autombist: smoke ram-synth config parse failed: {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)

        # --- 7. Optional fault simulations with small fault counts ---
        if run_sim:
            simulate_scenarios = [
                ("stuck-at", "march-c", 2),
                ("transition-up", "march-raw", 2),
                ("transition-down", "march-raw", 3),
            ]
            for fault_type, algo, pulse_width in simulate_scenarios:
                wrapper_path = _generate(
                    smoke_config_path,
                    smoke_out,
                    test=True,
                    faults=smoke_faults,
                    seed=smoke_seed,
                    fault_type=fault_type,
                    pulse_width_ns=pulse_width,
                    algo=algo,
                )
                module_outdir = wrapper_path.parent
                _simulate(module_outdir, verbose=False)
                typer.echo(f"[smoke] simulate ({fault_type}, {algo}, faults={smoke_faults}): PASS")

        # --- FaultFlow controller-grading bundle (emit-only; no Yosys/FaultFlow needed) ---
        if faultflow:
            from autombist.faultflow_flow import FaultFlowError, FaultFlowOptions, grade_controller

            wrapper_path = _generate(
                smoke_config_path, smoke_out,
                test=False, faults=0, seed=None,
                fault_type="stuck-at", pulse_width_ns=2, algo="march-c",
            )
            ff_module_outdir = wrapper_path.parent
            fake_repo = workspace / "faultflow_repo"
            (fake_repo / "cells" / "sky130").mkdir(parents=True, exist_ok=True)
            try:
                grade_controller(ff_module_outdir, FaultFlowOptions(repo=fake_repo), run=False)
            except (FaultFlowError, ConfigError, OSError, ValueError, yaml.YAMLError) as exc:
                typer.secho(f"[smoke] FAIL: FaultFlow bundle emit failed: {exc}", err=True, fg=typer.colors.RED)
                raise typer.Exit(code=1)
            bundle = ff_module_outdir / "faultflow"
            top = str(_default_mbist_config()["wrapper_module_name"])
            _assert_smoke_file(bundle / f"{memory_name}_bbox.v", "faultflow blackbox stub")
            _assert_smoke_file(bundle / "synth_collar.ys", "faultflow synth script")
            _assert_smoke_file(bundle / f"{top}.ofs", "faultflow .ofs")
            _assert_smoke_file(bundle / "run_faultflow.sh", "faultflow run script")
            typer.echo("[smoke] faultflow bundle emit (emit-only): PASS")

        typer.echo(f"[smoke] workspace: {workspace}")
        typer.echo("[smoke] All checks passed")
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()
