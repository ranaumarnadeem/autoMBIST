"""Interactive Tcl shell -- an EDA-native alternative front-end to the Typer
CLI, for users who live in OpenROAD/OpenSTA/magic-style Tcl consoles.

Thin adapter, by design: every registered command parses its ``-flag value``
arguments and calls the SAME core library function the Typer CLI command
calls (``generate_from_config``, ``run_simulation``, ``run_algo_campaign``,
``build_librelane_config``, etc.) -- no business logic lives here, only
argument parsing/marshaling and Tcl-shaped error/return-value handling. This
bounds the shell's maintenance cost to this one file: any change to the
underlying behavior of ``generate``/``simulate``/etc. is inherited for free,
never duplicated.

Uses ``tkinter.Tcl()`` for a headless (no display, no GUI) full Tcl
interpreter -- the standard library's only Tcl binding. Import-guarded: on a
system without a Tcl/tkinter install, importing this module still succeeds
(``is_available()`` returns False), but constructing a :class:`TclShell`
raises :class:`TclShellUnavailable` with an actionable message, mirroring the
CLI's own ``shutil.which``-based preflight guards.

A note on Tcl error messages: raising a plain Python exception out of a
``tkinter`` ``createcommand`` callback does NOT propagate its message to Tcl
-- ``catch`` sees a non-zero return code, but the caught error-message
variable is empty (an empirically-confirmed CPython/tkinter quirk, not a bug
in this module). Every command here is wrapped to instead invoke Tcl's own
``error`` command (via ``interp.call``, which needs no manual quoting) on
failure, which DOES preserve the message correctly.

A note on Tcl quoting: Tcl's double-quoted strings (``"..."``) undergo
backslash substitution, so a Windows path typed as ``"C:\\Users\\me\\x.yml"``
silently loses its backslashes (``\\U``/``\\m`` aren't recognized escapes, so
they're dropped; ``\\t`` IS recognized and becomes a real tab) -- with no
parse error to flag it. Use Tcl's brace quoting instead, e.g.
``generate -config {C:\\Users\\me\\config.yml}``, which is truly literal.
This is a property of Tcl itself, not something this module can fix.
"""
from __future__ import annotations

import difflib
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

try:
    import tkinter

    _TKINTER_IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - platform-dependent
    tkinter = None  # type: ignore[assignment]
    _TKINTER_IMPORT_ERROR = _exc

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from autombist.cli import _resolve_config_path, _resolve_module_outdir
    from autombist.cli_render import spinner
    from autombist.generator import ConfigError, generate_from_config
    from autombist.openram_flow import (
        OpenRAMConfigError,
        build_openram_command_args,
        load_openram_config,
        run_openram_synthesis,
    )
    from autombist.reporting import coverage_meets_threshold as sim_coverage_meets_threshold
    from autombist.reporting import format_simulation_summary
    from autombist.runner import SimulationError, run_controller_grading, run_simulation
    from autombist.signoff import (
        LIBRELANE_FLAKE_REF,
        SignoffConfigError,
        build_librelane_command,
        build_librelane_config,
        build_macro_signoff_command,
        normalize_lef_units,
    )
else:
    from .cli import _resolve_config_path, _resolve_module_outdir
    from .cli_render import spinner
    from .generator import ConfigError, generate_from_config
    from .openram_flow import (
        OpenRAMConfigError,
        build_openram_command_args,
        load_openram_config,
        run_openram_synthesis,
    )
    from .reporting import coverage_meets_threshold as sim_coverage_meets_threshold
    from .reporting import format_simulation_summary
    from .runner import SimulationError, run_controller_grading, run_simulation
    from .signoff import (
        LIBRELANE_FLAKE_REF,
        SignoffConfigError,
        build_librelane_command,
        build_librelane_config,
        build_macro_signoff_command,
        normalize_lef_units,
    )


class TclShellUnavailable(RuntimeError):
    """Raised when the Tcl shell is invoked but tkinter/Tcl isn't available."""


def is_available() -> bool:
    return tkinter is not None


# --------------------------------------------------------------------------- #
# Flag parsing: EDA-style "-flag value" tokens, shared by every command.
# --------------------------------------------------------------------------- #
def _looks_like_negative_number(token: str) -> bool:
    """True for tokens like "-5" or "-3.5" -- a flag VALUE, not a new flag,
    even though it starts with "-"."""
    body = token[1:]
    return bool(body) and body.replace(".", "", 1).isdigit()


def _is_flag_token(token: str) -> bool:
    return token.startswith("-") and not _looks_like_negative_number(token)


def _parse_flags(args: tuple[str, ...]) -> dict[str, str]:
    """Parse a flat ``('-flag', 'value', '-flag2', 'value2', ...)`` tuple --
    what Tcl hands a ``createcommand`` callback -- into ``{flag: value}``.
    A trailing flag with no following value (or one immediately followed by
    another flag) is treated as a boolean switch, value ``"1"``. A value that
    looks like a negative number (e.g. ``-5``) is consumed as the current
    flag's value rather than mistaken for a new flag."""
    flags: dict[str, str] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if not _is_flag_token(token):
            # RuntimeError, not ValueError: this is a user-input mistake (a
            # stray token, or a typo'd flag), not a program bug, and _wrap
            # treats RuntimeError as "expected" -- no traceback dumped to
            # stderr for it. Every other raise in this flag-parsing section
            # follows the same rule for the same reason.
            raise RuntimeError(f"expected a -flag token, got {token!r}")
        if i + 1 < len(args) and not _is_flag_token(args[i + 1]):
            flags[token] = args[i + 1]
            i += 2
        else:
            flags[token] = "1"
            i += 1
    return flags


def _pop_str(flags: dict[str, str], name: str, default: str | None = None) -> str | None:
    return flags.pop(name, default)


def _pop_required(flags: dict[str, str], name: str) -> str:
    if name not in flags:
        # A typo'd flag (e.g. -confi for -config) parses fine as ITS OWN flag
        # (any -prefixed token is a valid flag token to _parse_flags), so it
        # sits in `flags` under its own misspelled name while -config is
        # simply absent -- the naive message ("missing required flag
        # -config") is true but blames the wrong thing: the user DID pass a
        # value, just under the wrong key. Check the flags actually present
        # for a close spelling match and name it directly, the same
        # did-you-mean style used for a typo'd port key or fault type
        # elsewhere in this tool.
        close = difflib.get_close_matches(name, flags.keys(), n=1)
        hint = f" -- got {close[0]!r}, did you mean {name!r}?" if close else ""
        raise RuntimeError(f"missing required flag {name}{hint}")
    return flags.pop(name)


def _pop_int(flags: dict[str, str], name: str, default: int | None = None) -> int | None:
    value = flags.pop(name, None)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"flag {name} expects an integer, got {value!r}") from None


def _pop_float(flags: dict[str, str], name: str, default: float | None = None) -> float | None:
    value = flags.pop(name, None)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        raise RuntimeError(f"flag {name} expects a number, got {value!r}") from None


def _pop_bool(flags: dict[str, str], name: str, default: bool = False) -> bool:
    value = flags.pop(name, None)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def _reject_unknown(flags: dict[str, str]) -> None:
    if flags:
        raise RuntimeError(f"unknown flag(s): {', '.join(sorted(flags))}")


def _none_to_empty(value: Any) -> Any:
    """Tcl's idiom for "nothing" is the empty string, not the literal text
    "None" that plain str(None) would produce (and that tkinter's own
    Python->Tcl return-value marshaling does NOT special-case)."""
    return "" if value is None else value


class TclShell:
    """An in-process Tcl interpreter with autombist's operations registered as
    native Tcl commands. Construct raises :class:`TclShellUnavailable` if
    tkinter/Tcl isn't installed."""

    def __init__(self) -> None:
        if tkinter is None:
            raise TclShellUnavailable(
                "Tcl shell unavailable: tkinter/Tcl not found. Install it "
                "(e.g. `apt install python3-tk` on Debian/Ubuntu, or add "
                "tkinter to the Nix flake's pythonEnv) to use `autombist shell`."
            ) from _TKINTER_IMPORT_ERROR
        self.interp = tkinter.Tcl()
        self._register_commands()

    # ----------------------------------------------------------------- #
    # Registration + the Tcl-error-message workaround (see module docstring)
    # ----------------------------------------------------------------- #
    def _register_commands(self) -> None:
        commands: dict[str, Callable[..., Any]] = {
            "generate": self._cmd_generate,
            "simulate": self._cmd_simulate,
            "run": self._cmd_run,
            "test": self._cmd_test,
            "harden": self._cmd_harden,
            "fix_lef_units": self._cmd_fix_lef_units,
            "macro_signoff": self._cmd_macro_signoff,
            "grade_controller": self._cmd_grade_controller,
            "ram_synth": self._cmd_ram_synth,
            "doctor": self._cmd_doctor,
        }
        for name, fn in commands.items():
            self.interp.createcommand(name, self._wrap(name, fn))

    def _puts(self, text: str, *, nl: bool = True, err: bool = False) -> None:
        """print(), explicitly flushed. A bare, unflushed print() from inside
        a createcommand callback can visibly reorder relative to Tcl's own
        `puts` calls in the same eval() -- both paths write the same stdout
        file descriptor, but through independent buffers, so without a flush
        here the two can interleave out of the order they actually executed
        in. (Routing through Tcl's own `puts` command instead -- so
        everything shares one buffer -- was tried and rejected: it breaks
        under pytest's output-capturing, which swaps Python's sys.stdout for
        an object Tcl's channel layer can't write to, raising a hard
        "error writing stdout: bad file number" TclError.)
        """
        stream = sys.stderr if err else sys.stdout
        print(text, end=("\n" if nl else ""), file=stream, flush=True)

    def _wrap(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        def _bound(*args: str) -> Any:
            try:
                return fn(*args)
            except Exception as exc:
                # Every _cmd_* catches its own expected domain errors (bad
                # config, missing file, tool failure, ...) and re-raises them
                # as RuntimeError with a clean message -- so a RuntimeError
                # reaching here is "expected" and gets no traceback noise.
                # Anything else escaping unrewrapped (AttributeError, a typo,
                # a domain exception nobody thought to catch) is a genuine
                # surprise, so dump its traceback to stderr for diagnosis --
                # mirroring the CLI, where an unlisted exception type isn't
                # caught at all and propagates with Python's own traceback.
                if not isinstance(exc, RuntimeError):
                    traceback.print_exc(file=sys.stderr)
                # interp.call("error", ...) itself raises TclError -- this is
                # the ONLY reliable way to hand Tcl's catch a real message
                # (see module docstring). Never `raise` the original
                # exception directly here.
                self.interp.call("error", f"{name}: {exc}")
        return _bound

    # ----------------------------------------------------------------- #
    # Commands -- each is a thin adapter over one core library function.
    # ----------------------------------------------------------------- #
    def _cmd_generate(self, *args: str) -> str:
        flags = _parse_flags(args)
        config = Path(_pop_required(flags, "-config"))
        out = Path(_pop_str(flags, "-out", "out"))
        test = _pop_bool(flags, "-test", False)
        faults = _pop_int(flags, "-faults", 50)
        seed = _pop_int(flags, "-seed", None)
        fault_type = _pop_str(flags, "-fault-type", "stuck-at")
        pulse_width_ns = _pop_int(flags, "-pulse-width-ns", 2)
        algo = _pop_str(flags, "-algo", "march-c")
        _reject_unknown(flags)

        try:
            wrapper_path = generate_from_config(
                _resolve_config_path(config), out, use_saboteur=test, faults=faults,
                fault_seed=seed, fault_type=fault_type, pulse_width_ns=pulse_width_ns, algo=algo,
            )
        except (ConfigError, FileNotFoundError, OSError, ValueError) as exc:
            raise RuntimeError(str(exc)) from exc
        self._puts(f"Generated MBIST wrapper: {wrapper_path}")
        return str(wrapper_path)

    def _cmd_simulate(self, *args: str) -> Any:
        flags = _parse_flags(args)
        out = Path(_pop_str(flags, "-out", "out"))
        verbose = _pop_bool(flags, "-verbose", False)
        min_coverage = _pop_float(flags, "-min-coverage", None)
        _reject_unknown(flags)

        try:
            module_outdir = _resolve_module_outdir(out)
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise RuntimeError(str(exc)) from exc
        return self._run_simulation_and_report(module_outdir, verbose, min_coverage)

    def _run_simulation_and_report(self, module_outdir: Path, verbose: bool, min_coverage: float | None) -> Any:
        try:
            with spinner("Running MBIST simulation..."):
                result = run_simulation(module_outdir, verbose=verbose)
        except (ConfigError, FileNotFoundError, OSError, ValueError, SimulationError) as exc:
            raise RuntimeError(str(exc)) from exc

        self._puts(format_simulation_summary(result.report))
        # coverage_meets_threshold's own return value only carries a coverage
        # number when min_coverage was actually given (its documented
        # "no threshold => (True, None)" short-circuit) -- but the shell
        # needs the number regardless, for `set cov [simulate ...]`-style
        # scripting, so it's read directly off the report instead.
        coverage = result.report.get("fault_metrics", {}).get("coverage_percent")
        ok, _ = sim_coverage_meets_threshold(result.report, min_coverage)
        if not ok:
            if coverage is None:
                raise RuntimeError(
                    f"-min-coverage {min_coverage:.2f}% was requested but the simulator "
                    "reported no coverage number to check it against"
                )
            raise RuntimeError(f"coverage {coverage:.2f}% is below -min-coverage {min_coverage:.2f}%")
        return _none_to_empty(coverage)

    def _cmd_run(self, *args: str) -> Any:
        flags = _parse_flags(args)
        config = Path(_pop_required(flags, "-config"))
        out = Path(_pop_str(flags, "-out", "out"))
        test = _pop_bool(flags, "-test", False)
        faults = _pop_int(flags, "-faults", 50)
        seed = _pop_int(flags, "-seed", None)
        fault_type = _pop_str(flags, "-fault-type", "stuck-at")
        pulse_width_ns = _pop_int(flags, "-pulse-width-ns", 2)
        algo = _pop_str(flags, "-algo", "march-c")
        verbose = _pop_bool(flags, "-verbose", False)
        min_coverage = _pop_float(flags, "-min-coverage", None)
        faultflow = _pop_bool(flags, "-faultflow", False)
        faultflow_repo = _pop_str(flags, "-faultflow-repo", None)
        cell_lib = _pop_str(flags, "-cell-lib", "sky130")
        scan_chains = _pop_int(flags, "-scan-chains", 1)
        _reject_unknown(flags)

        try:
            wrapper_path = generate_from_config(
                _resolve_config_path(config), out, use_saboteur=test, faults=faults,
                fault_seed=seed, fault_type=fault_type, pulse_width_ns=pulse_width_ns, algo=algo,
            )
        except (ConfigError, FileNotFoundError, OSError, ValueError) as exc:
            raise RuntimeError(str(exc)) from exc
        self._puts(f"Generated MBIST wrapper: {wrapper_path}")
        coverage = self._run_simulation_and_report(wrapper_path.parent, verbose, min_coverage)

        if faultflow:
            from .faultflow_flow import FaultFlowOptions

            opts = FaultFlowOptions(
                repo=Path(faultflow_repo) if faultflow_repo else None,
                cell_lib=cell_lib, scan_chains=scan_chains, threshold=90.0, max_rounds=20,
            )
            self._grade_and_merge(wrapper_path.parent, opts, run=True)
        return coverage

    def _cmd_test(self, *args: str) -> Any:
        import json
        import tempfile

        from .alg_spec import AlgSpecError, resolve_algo
        from .algo_engine import CampaignError, MemoryParams, load_fault_list, run_algo_campaign, run_fsm_campaign
        from .algo_reporting import _check_diagnosis_fmt, _check_fmt, write_campaign_report, write_diagnosis_report
        from .algo_reporting import coverage_meets_threshold as campaign_coverage_meets_threshold
        from .cli_render import fault_progress
        from .fault_primitives import FaultPrimitiveError
        from .fault_primitives import default_registry as fp_default_registry
        from .fault_primitives import from_dict as fp_from_dict
        from .fault_primitives import validate as fp_validate
        from .fault_ram_gen import render_and_write
        from .fsm_harness import FsmPortError, check_ports, gather_sibling_sources

        flags = _parse_flags(args)
        addr_width = int(_pop_required(flags, "-addr-width"))
        data_width = int(_pop_required(flags, "-data-width"))
        algo = _pop_str(flags, "-algo", "march_c")
        fsm = _pop_str(flags, "-fsm", None)
        faults_path = Path(_pop_required(flags, "-faults"))
        fault_types = _pop_str(flags, "-fault-types", None)
        init = _pop_int(flags, "-init", 1)
        sim = _pop_str(flags, "-sim", "verilator")
        verbose = _pop_bool(flags, "-verbose", False)
        report = _pop_str(flags, "-report", None)
        fmt = _pop_str(flags, "-fmt", "md")
        min_coverage = _pop_float(flags, "-min-coverage", None)
        diagnosis = _pop_str(flags, "-diagnosis", None)
        diagnosis_fmt = _pop_str(flags, "-diagnosis-fmt", "md")
        check_sequence = _pop_bool(flags, "-check-sequence", False)
        _reject_unknown(flags)

        try:
            # Validate up front, before the (potentially multi-minute) campaign
            # runs -- a typo'd -fmt/-diagnosis-fmt used to only surface after
            # the simulation finished, wasting the whole run.
            if report is not None:
                _check_fmt(fmt)
            if diagnosis is not None:
                _check_diagnosis_fmt(diagnosis_fmt)
            records = load_fault_list(faults_path)
            mem = MemoryParams(addr_width=addr_width, data_width=data_width, init_val=init)

            fault_ram_sv = None
            if fault_types is not None:
                specs = json.loads(Path(fault_types).read_text(encoding="utf-8"))
                if not isinstance(specs, list):
                    raise ValueError("-fault-types file must contain a JSON list of fault-primitive specs")
                registry = fp_default_registry()
                for spec_dict in specs:
                    prim = fp_from_dict(spec_dict)
                    fp_validate(prim, existing_names={p.name for p in registry})
                    registry.append(prim)
                fault_ram_sv = render_and_write(
                    registry, Path(tempfile.mkdtemp(prefix="autombist-test-types-")) / "fault_ram.sv"
                )

            if fsm is not None:
                fsm_path = Path(fsm)
                sources = gather_sibling_sources(fsm_path)
                ports = check_ports(fsm_path.read_text(encoding="utf-8"))
                kwargs: dict[str, object] = {}
                if check_sequence:
                    kwargs["expected_spec"] = resolve_algo(algo)
                with fault_progress(len(records)) as progress_cb:
                    result = run_fsm_campaign(
                        mem, sources, ports.module_name, records, sim=sim, fault_ram_sv=fault_ram_sv,
                        progress_callback=progress_cb, **kwargs
                    )
                label = f"FSM:{ports.module_name} ({len(sources)} source file(s))"
            else:
                if check_sequence:
                    raise ValueError("-check-sequence requires -fsm")
                spec = resolve_algo(algo)
                with fault_progress(len(records)) as progress_cb:
                    result = run_algo_campaign(
                        mem, spec, records, sim=sim, verbose=verbose, fault_ram_sv=fault_ram_sv,
                        progress_callback=progress_cb,
                    )
                label = f"{spec.name} ({spec.length_n}n)"

            if report is not None:
                write_campaign_report(result, Path(report), fmt=fmt)
            if diagnosis is not None:
                write_diagnosis_report(result, Path(diagnosis), fmt=diagnosis_fmt)
        except (
            AlgSpecError, CampaignError, FsmPortError, FaultPrimitiveError, FileNotFoundError, OSError, ValueError,
        ) as exc:
            raise RuntimeError(str(exc)) from exc

        self._puts(f"autombist test: {label} on {addr_width}x{data_width} memory, init={init}")
        self._puts(f"  faults: {result.total}   detected: {result.detected}   coverage: {result.coverage_percent:.2f}%")
        if result.sequence is not None:
            if result.sequence.matches:
                self._puts(f"  sequence: OK ({result.sequence.observed_count} ops match {algo})")
            else:
                self._puts(f"  sequence: MISMATCH vs {algo}")
                self._puts(result.sequence.message())
                raise RuntimeError("controller does not implement its specified march sequence")

        if not campaign_coverage_meets_threshold(result, min_coverage):
            raise RuntimeError(f"coverage {result.coverage_percent:.2f}% is below -min-coverage {min_coverage:.2f}%")
        return result.coverage_percent

    def _cmd_harden(self, *args: str) -> str:
        import json

        flags = _parse_flags(args)
        config = Path(_pop_str(flags, "-config", "harden.yml"))
        out = Path(_pop_str(flags, "-out", "librelane-config.json"))
        pdk_root = Path(_pop_str(flags, "-pdk-root", str(Path.home() / ".ciel")))
        run = _pop_bool(flags, "-run", False)
        librelane_ref = _pop_str(flags, "-librelane-ref", LIBRELANE_FLAKE_REF)
        _reject_unknown(flags)

        import yaml

        try:
            resolved = config if config.is_absolute() else (Path.cwd() / config).resolve()
            loaded = yaml.safe_load(resolved.read_text(encoding="utf-8"))
            ll_config = build_librelane_config(loaded)
        except FileNotFoundError as exc:
            raise RuntimeError(f"harden config not found: {config}") from exc
        except (SignoffConfigError, yaml.YAMLError) as exc:
            raise RuntimeError(str(exc)) from exc

        out.write_text(json.dumps(ll_config, indent=2) + "\n", encoding="utf-8")
        self._puts(f"Wrote LibreLane config: {out}")
        if not run:
            return str(out)

        import shutil
        import subprocess

        if shutil.which("nix") is None:
            raise RuntimeError(f"'nix' not found on PATH -- -run invokes LibreLane via `nix run {librelane_ref}`")
        cmd = build_librelane_command(out, pdk_root, flake=librelane_ref)
        self._puts("$ " + " ".join(cmd))
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"LibreLane exited with code {result.returncode}")
        return str(out)

    def _cmd_fix_lef_units(self, *args: str) -> str:
        # LEF path is the one positional argument, expected first (before any
        # flags) -- mirrors "fix_lef_units path/to.lef -target-dbu 1000".
        if not args or args[0].startswith("-"):
            raise ValueError("fix_lef_units: missing LEF path argument")
        lef, *rest = args
        flags = _parse_flags(tuple(rest))
        out = _pop_str(flags, "-out", None)
        target_dbu = _pop_int(flags, "-target-dbu", 1000)
        _reject_unknown(flags)

        lef_path = Path(lef)
        try:
            text = lef_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise RuntimeError(f"LEF not found: {lef_path}") from exc
        fixed, snapped = normalize_lef_units(text, target_dbu=target_dbu)
        dest = Path(out) if out else lef_path
        dest.write_text(fixed, encoding="utf-8")
        self._puts(f"Wrote {dest} (DATABASE MICRONS -> {target_dbu}, {snapped} coords snapped)")
        return str(dest)

    def _cmd_macro_signoff(self, *args: str) -> Any:
        import shutil
        import subprocess

        # Leading non-flag tokens are macro dir names, e.g.
        # "macro_signoff sram_1rw sram_tiny -script path/to.sh".
        i = 0
        while i < len(args) and not args[i].startswith("-"):
            i += 1
        macros = list(args[:i])
        flags = _parse_flags(tuple(args[i:]))
        default_script = Path(__file__).resolve().parents[2] / "flow" / "multimem" / "signoff" / "run_macro_signoff.sh"
        script = Path(_pop_str(flags, "-script", str(default_script)))
        show_command = _pop_bool(flags, "-show-command", False)
        _reject_unknown(flags)

        if not script.is_file():
            raise RuntimeError(f"signoff script not found: {script}")
        cmd = build_macro_signoff_command(script, macros or None)
        if show_command:
            self._puts("$ " + " ".join(cmd))
            return " ".join(cmd)

        if shutil.which("bash") is None:
            raise RuntimeError("'bash' not found on PATH -- macro_signoff runs the script via bash")
        self._puts("$ " + " ".join(cmd))
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"macro-signoff exited with code {result.returncode}")
        return result.returncode

    def _grade_and_merge(self, module_outdir: Path, opts: Any, run: bool) -> Any:
        """Shared FaultFlow grading + report-merge logic behind both
        `grade_controller` and `run -faultflow`, mirroring cli.py's
        `_grade_controller` so both front-ends leave an identically-merged
        reports/latest.json (controller_grading block) behind."""
        from .faultflow_flow import FaultFlowError

        try:
            coverage = run_controller_grading(module_outdir, opts, run=run)
        except (FaultFlowError, ConfigError, FileNotFoundError, OSError, ValueError) as exc:
            raise RuntimeError(str(exc)) from exc

        bundle = module_outdir / "faultflow"
        if not run:
            self._puts(f"Emitted FaultFlow bundle: {bundle}")
            return str(bundle)

        report_path = module_outdir / "reports" / "latest.json"
        if coverage and report_path.exists():
            import json

            from .reporting import merge_faultflow_coverage, write_simulation_report

            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                merge_faultflow_coverage(report, coverage)
                write_simulation_report(report, module_outdir / "reports")
            except (OSError, ValueError):
                pass

        coverage_percent = coverage.get("coverage_percent") if coverage else None
        if isinstance(coverage_percent, (int, float)):
            self._puts(
                "Controller structural coverage (FaultFlow): "
                f"{coverage.get('detected')}/{coverage.get('denominator')} ({coverage_percent:.2f}%), "
                f"excluded-blackbox={coverage.get('excluded_blackbox')}"
            )
        return _none_to_empty(coverage_percent)

    def _cmd_grade_controller(self, *args: str) -> Any:
        from .faultflow_flow import FaultFlowOptions

        flags = _parse_flags(args)
        out = Path(_pop_str(flags, "-out", "out"))
        faultflow_repo = _pop_str(flags, "-faultflow-repo", None)
        cell_lib = _pop_str(flags, "-cell-lib", "sky130")
        scan_chains = _pop_int(flags, "-scan-chains", 1)
        threshold = _pop_float(flags, "-threshold", 90.0)
        max_rounds = _pop_int(flags, "-max-rounds", 20)
        run = _pop_bool(flags, "-run", True)
        _reject_unknown(flags)

        try:
            module_outdir = _resolve_module_outdir(out)
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise RuntimeError(str(exc)) from exc

        opts = FaultFlowOptions(
            repo=Path(faultflow_repo) if faultflow_repo else None,
            cell_lib=cell_lib, scan_chains=scan_chains, threshold=threshold, max_rounds=max_rounds,
        )
        return self._grade_and_merge(module_outdir, opts, run)

    def _cmd_ram_synth(self, *args: str) -> int:
        flags = _parse_flags(args)
        config = Path(_pop_str(flags, "-config", "openram.yml"))
        show_command = _pop_bool(flags, "-show-command", False)
        _reject_unknown(flags)

        try:
            resolved = config if config.is_absolute() else (Path.cwd() / config).resolve()
            cfg = load_openram_config(resolved)
            if show_command:
                cmd = build_openram_command_args(cfg, resolved)
                self._puts("$ " + " ".join(str(token) for token in cmd))
            result = run_openram_synthesis(resolved)
        except (FileNotFoundError, OpenRAMConfigError, OSError) as exc:
            raise RuntimeError(str(exc)) from exc

        if result.stdout:
            self._puts(result.stdout, nl=False)
        if result.stderr:
            self._puts(result.stderr, nl=False, err=True)
        if result.returncode != 0:
            raise RuntimeError(f"OpenRAM synthesis exited with code {result.returncode}")
        return result.returncode

    def _cmd_doctor(self, *args: str) -> str:
        from .cli import _doctor_checks

        rows = _doctor_checks()
        missing: list[str] = []
        self._puts("autombist doctor")
        for tool, status, needed_for, detail in rows:
            self._puts(f"  {tool:<24}{status:<10}{needed_for:<38}{detail}")
            if status != "OK":
                missing.append(tool)
        return " ".join(missing)

    # ----------------------------------------------------------------- #
    # REPL / batch entrypoints
    # ----------------------------------------------------------------- #
    def eval(self, script: str) -> str:
        """Evaluate a chunk of Tcl and return its result string. Raises
        tkinter.TclError (with a real message, per the module docstring) on
        failure."""
        return self.interp.eval(script)

    def source(self, path: Path) -> None:
        self.interp.call("source", str(path))

    def run_repl(self) -> None:
        try:
            import readline  # noqa: F401  (Unix only; enables history/editing as a side effect)
        except ImportError:
            pass

        print("autombist Tcl shell. Type 'exit' or Ctrl-D to quit.")
        while True:
            try:
                line = input("autombist> ")
            except EOFError:
                print()
                break
            if not line.strip():
                continue
            if line.strip() in ("exit", "quit"):
                break
            try:
                result = self.eval(line)
            except tkinter.TclError as exc:
                print(f"error: {exc}", file=sys.stderr)
                continue
            if result:
                print(result)

    def run_batch(self, path: Path | None) -> int:
        try:
            if path is not None:
                self.source(path)
            else:
                self.eval(sys.stdin.read())
        except tkinter.TclError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
