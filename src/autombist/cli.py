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
) -> None:
    """Run smoke checks to verify all generation modes and optionally simulate.

    Exercises all generation modes (clean, stuck-at, transition-up,
    transition-down) with both march-c and march-raw algorithms.
    Validates expected output artifacts exist after each generation step.
    Optionally runs a clean-mode simulation to verify the simulator
    toolchain (iverilog + cocotb).

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

        # --- 3. Stuck-at fault generation ---
        wrapper_path = _generate(
            smoke_config_path, smoke_out,
            test=True, faults=10, seed=42,
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
            test=True, faults=10, seed=42,
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
            test=True, faults=10, seed=42,
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

        # --- 7. Optional clean simulation ---
        if run_sim:
            wrapper_path = _generate(
                smoke_config_path, smoke_out,
                test=False, faults=0, seed=None,
                fault_type="stuck-at", pulse_width_ns=2, algo="march-c",
            )
            _simulate(wrapper_path.parent, verbose=False)
            typer.echo("[smoke] simulate (clean): PASS")

        typer.echo(f"[smoke] workspace: {workspace}")
        typer.echo("[smoke] All checks passed")
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()
