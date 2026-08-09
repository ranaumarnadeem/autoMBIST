from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import typer
import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from autombist import __version__
    from autombist.cli_render import fault_progress, print_status_table, spinner
    from autombist.generator import ConfigError, generate_from_config
    from autombist.openram_flow import (
        OpenRAMConfigError,
        build_openram_command_args,
        default_openram_config,
        load_openram_config,
        run_openram_synthesis,
    )
    from autombist.runner import SimulationError, run_simulation
    from autombist.signoff import (
        LIBRELANE_FLAKE_REF,
        SignoffConfigError,
        build_librelane_command,
        build_librelane_config,
        build_macro_signoff_command,
        normalize_lef_units,
    )
else:
    from . import __version__
    from .cli_render import fault_progress, print_status_table, spinner
    from .generator import ConfigError, generate_from_config
    from .openram_flow import (
        OpenRAMConfigError,
        build_openram_command_args,
        default_openram_config,
        load_openram_config,
        run_openram_synthesis,
    )
    from .runner import SimulationError, run_simulation
    from .signoff import (
        LIBRELANE_FLAKE_REF,
        SignoffConfigError,
        build_librelane_command,
        build_librelane_config,
        build_macro_signoff_command,
        normalize_lef_units,
    )


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
    # Every path this returns eventually becomes OUTDIR in a `make -C
    # hardware_dir ...` invocation (runner.py's _build_clean_command/
    # _build_fault_command) that runs with cwd=project_root, not the caller's
    # cwd -- a relative --out (the default) would then resolve against the
    # wrong directory and die with a raw "No rule to make target".
    out = out.resolve()
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
    # See _resolve_module_outdir's comment -- the generated wrapper's directory
    # feeds the same make invocation, so a relative --out has to become
    # absolute here too, before generate_from_config ever joins it with the
    # memory name.
    out = out.resolve()
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
    module_outdir: Path,
    verbose: bool,
    min_coverage: float | None = None,
    json_output: bool = False,
) -> None:
    try:
        with spinner("Running MBIST simulation..."):
            result = run_simulation(module_outdir, verbose=verbose)
    except (ConfigError, FileNotFoundError, OSError, ValueError, yaml.YAMLError, SimulationError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    from autombist.reporting import coverage_meets_threshold, format_simulation_summary

    if json_output:
        import json

        typer.echo(json.dumps(result.report, indent=2, sort_keys=True))
        # --verbose's raw simulator console output still has to go SOMEWHERE --
        # it can't join stdout without corrupting the JSON document, so it
        # goes to stderr instead (same stream result.stderr already always
        # uses below), rather than silently vanishing.
        if verbose and result.stdout:
            typer.echo(result.stdout, err=True, nl=False)
    else:
        if not _is_quiet():
            typer.echo(format_simulation_summary(result.report))
        if verbose and result.stdout:
            typer.echo(result.stdout, nl=False)
    if verbose and result.stderr:
        typer.echo(result.stderr, err=True, nl=False)

    ok, coverage = coverage_meets_threshold(result.report, min_coverage)
    if not ok:
        if coverage is None:
            typer.secho(
                f"autombist: --min-coverage {min_coverage:.2f}% was requested but the "
                "simulator reported no coverage number to check it against",
                err=True,
                fg=typer.colors.RED,
            )
        else:
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


def _grade_controller(
    module_outdir: Path, opts, run: bool, *, quiet: bool = False, json_output: bool = False,
) -> None:
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

    # quiet/json_output suppress the routine status echoes below -- notably
    # json_output, since `run --faultflow --json` must keep stdout as ONE
    # parseable JSON document (this ran after _simulate already printed it).
    silent = quiet or json_output

    if not run:
        if not silent:
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

    if silent:
        return

    coverage_percent = coverage.get("coverage_percent") if coverage else None
    if isinstance(coverage_percent, (int, float)):
        typer.echo(
            "Controller structural coverage (FaultFlow): "
            f"{coverage.get('detected')}/{coverage.get('denominator')} ({coverage_percent:.2f}%), "
            f"excluded-blackbox={coverage.get('excluded_blackbox')}"
        )
    else:
        typer.echo(f"Controller grading complete. Bundle: {bundle}")


_CLI_STATE: dict[str, bool] = {"verbose": False, "quiet": False}


def _effective_verbose(local_verbose: bool) -> bool:
    """OR a command's own --verbose with the global -v flag -- additive, so
    every existing --verbose call site keeps working unchanged whether or not
    the global flag is ever touched."""
    return local_verbose or _CLI_STATE["verbose"]


def _is_quiet() -> bool:
    return _CLI_STATE["quiet"]


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_show_version,
        is_eager=True,
        help="Show version and exit",
    ),
    verbose: bool = typer.Option(
        False, "-v", "--verbose",
        help="Enable verbose output for every command (ORed with each command's own --verbose)",
    ),
    quiet: bool = typer.Option(
        False, "-q", "--quiet",
        help="Suppress routine status lines (results and errors still print); mutually exclusive with -v",
    ),
) -> None:
    if verbose and quiet:
        typer.secho("autombist: -v/--verbose and -q/--quiet are mutually exclusive", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    _CLI_STATE["verbose"] = verbose
    _CLI_STATE["quiet"] = quiet
    # Only touch the (process-global) root logger when the caller actually
    # asked for a non-default verbosity -- an ordinary invocation with
    # neither flag must not reconfigure logging out from under anything
    # embedding autombist.cli in-process.
    if verbose or quiet:
        import logging

        level = logging.DEBUG if verbose else logging.ERROR
        logging.basicConfig(level=level, format="%(levelname)s: %(message)s", force=True)


@app.command()
def generate(
    config: Path | None = typer.Option(None, "--config", help="YAML config file with memory parameters (defaults to ./config.yml if it exists)"),
    out: Path = typer.Option("out", "--out", help="Output directory where generated files will be written"),
    test: bool = typer.Option(False, "--test/--no-test", help="Generate fault-injection saboteur wrapper and fault masks (use with --faults)"),
    faults: int = typer.Option(50, "-r", "--faults", help="Number of random faults to inject (only with --test)"),
    seed: int | None = typer.Option(None, "--seed", help="Random seed for reproducible fault injection (optional)"),
    fault_type: str = typer.Option("stuck-at", "--fault-type", help="Fault model: stuck-at (SA0/SA1), transition-up, transition-down, or port-coupling (march-1r1w only; march-2rw supports stuck-at/transition only)"),
    pulse_width_ns: int = typer.Option(2, "--pulse-width-ns", help="Pulse width in clock cycles for transition faults"),
    algo: str = typer.Option("march-c", "--algo", help="MBIST algorithm: march-c, march-raw, march-1r1w, march-2rw, march-x, or mats-plus"),
) -> None:
    r"""Generate MBIST wrapper, RTL, and optionally fault masks.

    This command creates SystemVerilog wrapper modules and copies the MBIST
    algorithm RTL into the output directory. Optionally generates fault masks
    for simulation with the --test flag.

    Output: out/<memory_name>/
      - <memory_name>_mbist.v (main wrapper)
      - <algo>/ e.g. march_c/ -- the selected algorithm's RTL only
        (<algo>_algo.sv, <algo>_fsm.sv, <algo>_top.sv)
      - sram_model.sv and the shared repair/self-repair RTL
        (repair_remap_row.sv, repair_remap_col.sv, sram_model_spares.sv,
        onchip_row_repair_analyzer.sv, onchip_selfrepair_ctrl.sv, ...)
      - \[with --test] <memory_name>_saboteur.v (fault injection wrapper)
      - \[with --test] faults/*.hex (fault masks)
      - \[with --test] Makefile (for running simulation)

    Examples:
      autombist generate --config config.yml
      autombist generate --config my_sram.yml --out results --algo march-raw
      autombist generate --config config.yml --test --faults 100 --seed 42
      autombist generate  # uses ./config.yml when present
    """

    config = _resolve_config_path(config)
    wrapper_path = _generate(config, out, test, faults, seed, fault_type, pulse_width_ns, algo)
    if not _is_quiet():
        typer.echo(f"Generated MBIST wrapper: {wrapper_path}")
        if test:
            typer.echo(f"Generated fault masks in: {wrapper_path.parent / 'faults'}")
            typer.echo(f"Generated fault-sim Makefile: {wrapper_path.parent / 'Makefile'}")


@app.command()
def simulate(
    out: Path = typer.Option("out", "--out", help="Output directory containing generated autombist output"),
    verbose: bool = typer.Option(False, "--verbose", help="Print full simulator console output and detailed logs"),
    min_coverage: float | None = typer.Option(None, "--min-coverage", help="Fail (exit 1) if array fault coverage is below this percent"),
    json_output: bool = typer.Option(False, "--json", help="Print the structured report as JSON to stdout instead of the human summary"),
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
      - Terminal summary with coverage metrics (or the same report as JSON with --json)

    Examples:
      autombist simulate --out out
      autombist simulate --out out --verbose
      autombist simulate --out out/input_demo_8x16_scn4m
      autombist simulate --out out --json | jq .fault_metrics
    """

    try:
        module_outdir = _resolve_module_outdir(out)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    _simulate(module_outdir, _effective_verbose(verbose), min_coverage, json_output)


@app.command()
def run(
    config: Path | None = typer.Option(None, "--config", help="YAML config file with memory parameters (defaults to ./config.yml if it exists)"),
    out: Path = typer.Option("out", "--out", help="Output directory for all generated files and results"),
    test: bool = typer.Option(False, "--test/--no-test", help="Generate and run fault injection simulation"),
    faults: int = typer.Option(50, "-r", "--faults", help="Number of faults to inject"),
    seed: int | None = typer.Option(None, "--seed", help="Random seed for reproducible fault injection"),
    fault_type: str = typer.Option("stuck-at", "--fault-type", help="Fault model: stuck-at, transition-up, transition-down, or port-coupling (march-1r1w only; march-2rw supports stuck-at/transition only)"),
    pulse_width_ns: int = typer.Option(2, "--pulse-width-ns", help="Pulse width in clock cycles for transition faults"),
    algo: str = typer.Option("march-c", "--algo", help="MBIST algorithm: march-c, march-raw, march-1r1w, march-2rw, march-x, or mats-plus"),
    verbose: bool = typer.Option(False, "--verbose", help="Print full simulator console output and detailed logs"),
    faultflow: bool = typer.Option(False, "--faultflow/--no-faultflow", help="After sim, grade the MBIST controller logic with FaultFlow (Linux/WSL)"),
    faultflow_repo: Path | None = typer.Option(None, "--faultflow-repo", envvar="FAULTFLOW_HOME", help="FaultFlow repo path (or set FAULTFLOW_HOME)"),
    cell_lib: str = typer.Option("sky130", "--cell-lib", help="FaultFlow standard-cell library: sky130 or osu035"),
    scan_chains: int = typer.Option(1, "--scan-chains", help="Scan chains for controller grading"),
    min_coverage: float | None = typer.Option(None, "--min-coverage", help="Fail (exit 1) if array fault coverage is below this percent"),
    json_output: bool = typer.Option(False, "--json", help="Print the structured report as JSON to stdout instead of the human summary"),
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
      autombist run --config config.yml --json | jq .fault_metrics
    """

    config = _resolve_config_path(config)
    wrapper_path = _generate(config, out, test, faults, seed, fault_type, pulse_width_ns, algo)
    if not json_output and not _is_quiet():
        typer.echo(f"Generated MBIST wrapper: {wrapper_path}")
        if test:
            typer.echo(f"Generated fault masks in: {wrapper_path.parent / 'faults'}")
            typer.echo(f"Generated fault-sim Makefile: {wrapper_path.parent / 'Makefile'}")
    _simulate(wrapper_path.parent, _effective_verbose(verbose), min_coverage, json_output)
    if faultflow:
        opts = _build_faultflow_options(faultflow_repo, cell_lib, scan_chains, 90.0, 20)
        _grade_controller(wrapper_path.parent, opts, run=True, quiet=_is_quiet(), json_output=json_output)


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
    algo: str = typer.Option("march_c", "--algo", help="Built-in algorithm name (march_c, march_b, mats_plus, march_ss, march_x) or a path to a .alg file"),
    fsm: Path | None = typer.Option(None, "--fsm", help="Validate a controller FSM .sv instead of an algorithm (takes precedence over --algo); sibling .sv/.v files in its directory are gathered automatically"),
    faults: Path = typer.Option(..., "--faults", help="Fault-list file: 'TYPE VADDR VBIT AADDR ABIT P0 P1' per line"),
    fault_types: Path | None = typer.Option(None, "--fault-types", help="JSON file with a list of custom fault-primitive specs, added to the built-in 19 (see fault_primitives.py for the schema)"),
    init: int = typer.Option(1, "--init", help="Memory init value (0 or 1)"),
    sim: str = typer.Option("verilator", "--sim", help="Simulator backend (Verilator only; Icarus cannot run the SV fault engine)"),
    verbose: bool = typer.Option(False, "--verbose", help="Print per-fault activation counts (+FAULT_VERBOSE)"),
    report: Path | None = typer.Option(None, "--report", help="Write a per-fault coverage report to this path"),
    fmt: str = typer.Option("md", "--fmt", help="Report format: md, csv, or json"),
    min_coverage: float | None = typer.Option(None, "--min-coverage", help="Fail (exit 1) if coverage is below this percent"),
    diagnosis: Path | None = typer.Option(None, "--diagnosis", help="Write a per-cell (addr, bit) diagnosis/fail-bitmap report to this path"),
    diagnosis_fmt: str = typer.Option("md", "--diagnosis-fmt", help="Diagnosis report format: md, csv, or json"),
    check_sequence: bool = typer.Option(False, "--check-sequence", help="With --fsm: also verify the controller drives the exact march sequence of --algo (address order, ops, write data, port), independent of fault detection. Exits 1 on a sequence mismatch."),
    json_output: bool = typer.Option(False, "--json", help="Print the full campaign result (same shape as --report json) as JSON to stdout instead of the human summary"),
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
      autombist test -aw 8 -dw 8 --algo march_c --faults faults.txt --diagnosis diag.md
    """

    import json

    from autombist.alg_spec import AlgSpecError, resolve_algo
    from autombist.algo_engine import CampaignError, MemoryParams, load_fault_list, run_algo_campaign, run_fsm_campaign
    from autombist.algo_reporting import (
        coverage_meets_threshold,
        render_campaign_json,
        write_campaign_report,
        write_diagnosis_report,
    )
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
            # Pass expected_spec ONLY when a sequence check was requested, so the
            # default call is byte-identical to before this feature existed.
            fsm_kwargs: dict[str, object] = {}
            if check_sequence:
                fsm_kwargs["expected_spec"] = resolve_algo(algo)
            with fault_progress(len(records)) as progress_cb:
                result = run_fsm_campaign(
                    mem, sources, ports.module_name, records,
                    sim=sim, fault_ram_sv=fault_ram_sv, progress_callback=progress_cb, **fsm_kwargs,
                )
            label = f"FSM:{ports.module_name} ({len(sources)} source file(s))"
        else:
            if check_sequence:
                raise ValueError(
                    "--check-sequence requires --fsm (it validates a controller against its "
                    "algorithm spec; the algorithm front IS the reference sequence)"
                )
            spec = resolve_algo(algo)
            with fault_progress(len(records)) as progress_cb:
                result = run_algo_campaign(
                    mem, spec, records, sim=sim, verbose=_effective_verbose(verbose),
                    fault_ram_sv=fault_ram_sv, progress_callback=progress_cb,
                )
            label = f"{spec.name} ({spec.length_n}n)"
        if report is not None:
            write_campaign_report(result, report, fmt=fmt)
        if diagnosis is not None:
            write_diagnosis_report(result, diagnosis, fmt=diagnosis_fmt)
    except (AlgSpecError, CampaignError, FsmPortError, FaultPrimitiveError, FileNotFoundError, OSError, ValueError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(render_campaign_json(result))
    elif not _is_quiet():
        typer.echo(f"autombist test: {label} on {addr_width}x{data_width} memory, init={init}")
        typer.echo(f"  faults: {result.total}   detected: {result.detected}   coverage: {result.coverage_percent:.2f}%")
        typer.echo(f"  build: {result.build_seconds:.2f}s   run: {result.run_seconds:.2f}s   sim: {result.sim}")
        if report is not None:
            typer.echo(f"  report: {report}")
        if diagnosis is not None:
            typer.echo(f"  diagnosis: {diagnosis}")
        if result.sequence is not None and result.sequence.matches:
            typer.echo(
                f"  sequence: OK ({result.sequence.observed_count} ops match {algo})"
            )

    if result.sequence is not None and not result.sequence.matches:
        # Always printed (stderr, regardless of -q/--json) -- this is the
        # failure DIAGNOSIS the message below references as "above", not a
        # routine status line, so -q must not delete it and --json must not
        # push it onto stdout where it would corrupt the JSON document.
        typer.secho(f"  sequence: MISMATCH vs {algo}", err=True, fg=typer.colors.RED)
        for line in result.sequence.message().splitlines()[1:]:
            typer.secho(line, err=True, fg=typer.colors.RED)
        typer.secho(
            "autombist: controller does not implement its specified march sequence "
            "(see sequence MISMATCH above)",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

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
    testbench bundle (export_tb). Built-in algorithms (march_c, march_b,
    mats_plus, march_ss, march_x) are preloaded. Type 'help' inside the shell for the
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


@app.command()
def harden(
    config: Path = typer.Option("harden.yml", "--config", help="Harden config (design + macros); see docs/cli-reference.md"),
    out: Path = typer.Option("librelane-config.json", "--out", help="Where to write the generated LibreLane config"),
    pdk_root: Path = typer.Option(Path.home() / ".ciel", "--pdk-root", help="ciel PDK root"),
    run: bool = typer.Option(False, "--run", help="Actually run LibreLane (needs nix + PDK); default only emits the config"),
    librelane_ref: str = typer.Option(LIBRELANE_FLAKE_REF, "--librelane-ref", help="Nix flake ref for LibreLane (pinned by default; override to try a newer version)"),
) -> None:
    """Emit (and optionally run) a LibreLane config for a design + OpenRAM macros.

    Bakes in the proven macro-integration recipe (hard-IP signoff flags, PDN
    halos, PDN_MACRO_CONNECTIONS net-vs-pin format). Without --run it just writes
    the LibreLane config JSON, so you can inspect it or feed it to LibreLane
    yourself -- a quick way to verify the integration wiring.

    --run invokes LibreLane pinned to a tested tag (see --librelane-ref's
    default) rather than floating on LibreLane's main branch, so reproducing
    a hardening result doesn't depend on whatever the tip of main happens to
    be at invocation time.
    """
    import json
    import shutil
    import subprocess

    try:
        resolved = config if config.is_absolute() else (Path.cwd() / config).resolve()
        loaded = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        ll_config = build_librelane_config(loaded)
    except FileNotFoundError:
        typer.secho(f"autombist: harden config not found: {config}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)
    except (SignoffConfigError, yaml.YAMLError) as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    out.write_text(json.dumps(ll_config, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Wrote LibreLane config: {out}")

    if not run:
        typer.echo("(config only; pass --run to invoke LibreLane)")
        return

    if shutil.which("nix") is None:
        typer.secho(
            f"autombist: 'nix' not found on PATH. --run invokes LibreLane via "
            f"`nix run {librelane_ref}`; install Nix "
            "(https://nixos.org/download) or omit --run to only emit the config.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    cmd = build_librelane_command(out, pdk_root, flake=librelane_ref)
    typer.echo("$ " + " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)


@app.command("fix-lef-units")
def fix_lef_units(
    lef: Path = typer.Argument(..., help="LEF file to normalize"),
    out: Path = typer.Option(None, "--out", help="Output path (default: overwrite in place)"),
    target_dbu: int = typer.Option(1000, "--target-dbu", help="Target DATABASE MICRONS value"),
) -> None:
    """Normalize an OpenRAM LEF's DATABASE MICRONS to the sky130A grid (1000).

    OpenRAM declares 2000 dbu though the coordinates (and GDS) are already on the
    1nm grid; LibreLane's sky130A tech needs 1000. Declaration-only fix, plus a
    defensive snap of any >=4-decimal coordinate.
    """
    try:
        text = lef.read_text(encoding="utf-8")
    except FileNotFoundError:
        typer.secho(f"autombist: LEF not found: {lef}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    fixed, snapped = normalize_lef_units(text, target_dbu=target_dbu)
    dest = out or lef
    dest.write_text(fixed, encoding="utf-8")
    typer.echo(f"Wrote {dest} (DATABASE MICRONS -> {target_dbu}, {snapped} coords snapped)")


@app.command("macro-signoff")
def macro_signoff(
    macros: list[str] = typer.Argument(None, help="Macro dir names to check (default: all in the multimem set)"),
    script: Path = typer.Option(
        Path(__file__).resolve().parents[2] / "flow" / "multimem" / "signoff" / "run_macro_signoff.sh",
        "--script",
        help="Path to run_macro_signoff.sh",
    ),
    show_command: bool = typer.Option(False, "--show-command", help="Print the command instead of running it"),
) -> None:
    """Run magic DRC + netgen LVS on generated OpenRAM macros (owed when a macro
    was generated with -n). Requires magic/netgen on PATH and the sky130 PDK.
    """
    import shutil
    import subprocess

    if not script.is_file():
        typer.secho(f"autombist: signoff script not found: {script}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    cmd = build_macro_signoff_command(script, list(macros) if macros else None)
    if show_command:
        typer.echo("$ " + " ".join(cmd))
        return

    if shutil.which("bash") is None:
        typer.secho(
            "autombist: 'bash' not found on PATH. macro-signoff runs "
            f"{script.name} via bash (which in turn needs magic + netgen on "
            "PATH); on Windows use WSL/Git Bash, or pass --show-command to "
            "inspect the command without running it.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    result = subprocess.run(cmd)
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


def _cocotb_available() -> bool:
    """A separate, trivially-monkeypatchable seam -- avoids tests having to
    patch importlib's own machinery (risky: other imports in the same process
    would be affected) just to simulate cocotb being present/absent."""
    import importlib.util

    return importlib.util.find_spec("cocotb") is not None


def _doctor_checks() -> list[tuple[str, str, str, str]]:
    """Return (tool, status, needed_for, detail) for the external toolchain
    autombist's various commands shell out to. Pure/testable -- the `doctor`
    command just renders whatever this returns."""
    import os
    import shutil

    rows: list[tuple[str, str, str, str]] = []

    def which_row(tool: str, needed_for: str) -> None:
        path = shutil.which(tool)
        status = "OK" if path else "MISSING"
        detail = path or "not found on PATH"
        rows.append((tool, status, needed_for, detail))

    which_row("make", "simulate, run")
    which_row("iverilog", "simulate, run")
    cocotb_ok = _cocotb_available()
    rows.append((
        "cocotb (python)", "OK" if cocotb_ok else "MISSING", "simulate, run",
        "importable" if cocotb_ok else "not importable",
    ))
    which_row("verilator", "test (fault-model research engine)")
    which_row("yosys", "grade-controller")
    which_row("nix", "harden --run")
    which_row("bash", "macro-signoff")
    which_row("magic", "macro-signoff")
    which_row("netgen", "macro-signoff")

    faultflow_home = os.environ.get("FAULTFLOW_HOME")
    rows.append((
        "FAULTFLOW_HOME (env)", "OK" if faultflow_home else "MISSING", "grade-controller",
        faultflow_home or "not set (or pass --faultflow-repo explicitly)",
    ))
    return rows


@app.command()
def doctor() -> None:
    """Report which external tools autombist can find on this system.

    Checks the full toolchain autombist's various commands shell out to
    (make/iverilog/cocotb for simulate & run, verilator for the fault-model
    research engine, nix for harden --run, bash/magic/netgen for
    macro-signoff, yosys + FAULTFLOW_HOME for grade-controller) via
    shutil.which / an import probe / an env check, and prints a capability
    table. Purely diagnostic -- never affects any other command's behavior or
    exit code.

    Examples:
      autombist doctor
    """
    rows = _doctor_checks()
    print_status_table(
        "autombist doctor",
        ["Tool", "Status", "Needed for", "Detail"],
        [list(row) for row in rows],
        status_col=1,
    )


@app.command()
def shell(
    file: Path | None = typer.Option(None, "-f", "--file", help="Run a .tcl script (batch mode) instead of an interactive REPL"),
) -> None:
    """Launch the interactive Tcl shell -- an EDA-native alternative to the
    Typer CLI, for users who live in OpenROAD/OpenSTA/magic-style Tcl
    consoles.

    Every command is a thin adapter over the SAME core function the
    corresponding Typer CLI command calls: generate, simulate, run, test,
    harden, fix_lef_units, macro_signoff, grade_controller, ram_synth,
    doctor. Commands return Tcl-usable values (e.g. `simulate` returns the
    coverage percent) so sessions script naturally:

        set cov \\[simulate -out out]
        if {$cov < 90} { error "coverage too low" }

    Failures raise real Tcl errors, catchable via Tcl's own `catch`. Without
    --file, reads from stdin when stdin isn't a terminal (piping/redirecting
    a script in), otherwise starts an interactive REPL.

    Quoting paths: Tcl's "..." double-quotes apply backslash substitution, so
    a Windows path like "C:\\Users\\me\\config.yml" silently loses its
    backslashes -- use {...} brace-quoting instead, which is fully literal:
    generate -config {C:\\Users\\me\\config.yml}

    Requires tkinter (the stdlib's Tcl binding) -- on Debian/Ubuntu:
    `apt install python3-tk`; already included in this repo's Nix flake.

    Examples:
      autombist shell
      autombist shell --file session.tcl
      printf 'puts \\[generate -config config.yml -out out]\\n' | autombist shell
    """
    from .tcl_shell import TclShellUnavailable

    try:
        from .tcl_shell import TclShell
    except ImportError as exc:
        typer.secho(f"autombist: Tcl shell unavailable: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        tcl = TclShell()
    except TclShellUnavailable as exc:
        typer.secho(f"autombist: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if file is not None:
        raise typer.Exit(code=tcl.run_batch(file))
    if not sys.stdin.isatty():
        raise typer.Exit(code=tcl.run_batch(None))
    tcl.run_repl()
