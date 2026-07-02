"""``autombist algo`` -- an interactive research shell for validating MBIST
algorithms (and, from P5/P6 on, controller FSMs and custom fault primitives)
against the fault-campaign engine (:mod:`autombist.algo_engine`).

Session state is intentionally plain (a dataclass), so the same commands can be
driven either interactively (:meth:`AlgoShell.cmdloop`) or scripted
(``autombist algo --script FILE``, or directly via :meth:`AlgoShell.onecmd` in
tests) without depending on a TTY.
"""
from __future__ import annotations

import cmd
import os
import shlex
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .alg_spec import AlgSpec, AlgSpecError, builtin_algos, find_engine_dir, load_alg_file
from .algo_engine import (
    BUILTIN_FAULT_TYPES,
    CampaignResult,
    FaultRecord,
    MemoryParams,
    generate_all_types_faults,
    generate_random_faults,
    load_fault_list,
    run_algo_campaign,
    write_fault_list,
)
from .algo_reporting import render_matrix_md, write_campaign_report, write_matrix_report

# Shorthand aliases so `compare_algo mine -march C,X,SS` reads the way the
# literature abbreviates these algorithms.
ALGO_ALIASES = {
    "C": "march_c", "C-": "march_c", "MARCHC": "march_c",
    "X": "march_x", "MARCHX": "march_x",
    "SS": "march_ss", "MARCHSS": "march_ss",
    "MATS": "mats_plus", "MATS+": "mats_plus",
}


def _tokenize(arg: str) -> list[str]:
    """shlex.split's default POSIX mode treats backslash as an escape char,
    which mangles Windows paths (e.g. 'C:\\Users' -> 'C:Users'). This tool's
    target is WSL/Linux, where paths are POSIX and default splitting is
    correct; fall back to non-POSIX splitting only when actually on Windows."""
    return shlex.split(arg, posix=(os.name != "nt"))


def _parse_flags(tokens: list[str], spec: dict[str, type | None]) -> tuple[list[str], dict[str, object]]:
    """Split tokens into (positional args, {flag: value}). spec maps a flag name
    (without leading dashes) to its value type, or None for a boolean flag."""
    positional: list[str] = []
    flags: dict[str, object] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-") and len(tok) > 1 and not tok[1:].lstrip("-").isdigit():
            name = tok.lstrip("-")
            if name not in spec:
                raise ValueError(f"unknown option '{tok}'")
            typ = spec[name]
            if typ is None:
                flags[name] = True
                i += 1
            else:
                if i + 1 >= len(tokens):
                    raise ValueError(f"option '{tok}' requires a value")
                flags[name] = typ(tokens[i + 1])
                i += 2
        else:
            positional.append(tok)
            i += 1
    return positional, flags


@dataclass
class Session:
    mem: MemoryParams | None = None
    algos: dict[str, AlgSpec] = field(default_factory=dict)
    fsms: dict[str, Path] = field(default_factory=dict)
    faults: list[FaultRecord] = field(default_factory=list)
    sim: str = "verilator"
    last_results: dict[str, CampaignResult] = field(default_factory=dict)
    last_matrix: list[CampaignResult] | None = None
    last_op: tuple[str, str | None] | None = None
    workdir: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="autombist-algo-")))
    _run_counter: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if not self.algos:
            try:
                for name, path in builtin_algos().items():
                    self.algos[name] = load_alg_file(path, name=name)
            except AlgSpecError:
                pass  # degrade gracefully; user can still add_algo manually

    def next_run_dir(self, tag: str) -> Path:
        self._run_counter += 1
        d = self.workdir / f"{self._run_counter:03d}_{tag}"
        d.mkdir(parents=True, exist_ok=True)
        return d


class AlgoShell(cmd.Cmd):
    intro = "autombist algo -- MBIST algorithm research shell. Type 'help' for commands, 'quit' to exit."
    prompt = "algo> "

    def __init__(self, session: Session | None = None) -> None:
        super().__init__()
        self.session = session or Session()

    # -- output helpers ----------------------------------------------------
    def _out(self, msg: str) -> None:
        self.stdout.write(msg + "\n")

    def _err(self, msg: str) -> None:
        self.stdout.write(f"error: {msg}\n")

    def _require_memory(self) -> MemoryParams:
        if self.session.mem is None:
            raise ValueError("no memory configured; run 'set_memory <addr_width> <data_width>' first")
        return self.session.mem

    def _resolve_algo(self, name: str) -> AlgSpec:
        key = ALGO_ALIASES.get(name.upper(), name).replace("-", "_")
        if key in self.session.algos:
            return self.session.algos[key]
        path = Path(name)
        if path.exists():
            spec = load_alg_file(path)
            self.session.algos[spec.name] = spec
            return spec
        raise AlgSpecError(
            f"unknown algorithm '{name}'. Registered: {', '.join(sorted(self.session.algos)) or '(none)'}"
        )

    def _print_result_summary(self, result: CampaignResult) -> None:
        self._out(
            f"{result.algo_name}: {result.detected}/{result.total} detected "
            f"({result.coverage_percent:.2f}%)  build={result.build_seconds:.2f}s run={result.run_seconds:.2f}s"
        )

    # -- cmd.Cmd overrides ---------------------------------------------------
    def emptyline(self) -> bool:
        return False  # do nothing on a blank line (default cmd.Cmd repeats the last command)

    def default(self, line: str) -> None:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return
        self._err(f"unknown command: {stripped.split()[0]!r}. Type 'help' for a list.")

    def onecmd(self, line: str) -> bool:
        try:
            return bool(super().onecmd(line))
        except Exception as exc:  # keep the REPL alive on any command failure
            self._err(str(exc))
            return False

    # -- commands -------------------------------------------------------
    def do_set_memory(self, arg: str) -> None:
        """set_memory <addr_width> <data_width> [--wmasks N] [--init 0|1]
        Configure the memory under test."""
        pos, flags = _parse_flags(_tokenize(arg), {"wmasks": int, "init": int})
        if len(pos) < 2:
            raise ValueError("usage: set_memory <addr_width> <data_width> [--wmasks N] [--init 0|1]")
        aw, dw = int(pos[0]), int(pos[1])
        self.session.mem = MemoryParams(
            addr_width=aw, data_width=dw,
            num_wmasks=int(flags.get("wmasks", 1)), init_val=int(flags.get("init", 1)),
        )
        self._out(f"memory set: {aw}x{dw}, init={self.session.mem.init_val}")

    def do_add_algo(self, arg: str) -> None:
        """add_algo <path.alg> [--name NAME]
        Register a march algorithm spec (see the .alg format in the docs)."""
        pos, flags = _parse_flags(_tokenize(arg), {"name": str})
        if not pos:
            raise ValueError("usage: add_algo <path.alg> [--name NAME]")
        path = Path(pos[0])
        name = str(flags["name"]) if "name" in flags else path.stem
        spec = load_alg_file(path, name=name)
        self.session.algos[name] = spec
        self._out(f"algorithm '{name}' registered ({spec.length_n}n, {len(spec.elements)} elements)")

    def do_add_fault(self, arg: str) -> None:
        """add_fault TYPE VADDR VBIT [AADDR ABIT P0 P1]
        Append one fault instance to the current fault list."""
        tokens = _tokenize(arg)
        if len(tokens) not in (3, 7):
            raise ValueError("usage: add_fault TYPE VADDR VBIT [AADDR ABIT P0 P1]")
        fault_type, va, vb = tokens[0], int(tokens[1]), int(tokens[2])
        aa, ab, p0, p1 = (int(x) for x in tokens[3:7]) if len(tokens) == 7 else (0, 0, 0, 0)
        self.session.faults.append(FaultRecord(fault_type, va, vb, aa, ab, p0, p1))
        self._out(f"fault added: {fault_type} v={va}.{vb} (total {len(self.session.faults)})")

    def do_load_faults(self, arg: str) -> None:
        """load_faults <path> [--append]
        Load a fault-list file (replaces the current list unless --append)."""
        pos, flags = _parse_flags(_tokenize(arg), {"append": None})
        if not pos:
            raise ValueError("usage: load_faults <path> [--append]")
        records = load_fault_list(Path(pos[0]))
        if flags.get("append"):
            self.session.faults.extend(records)
        else:
            self.session.faults = records
        self._out(f"loaded {len(records)} faults from {pos[0]} (total {len(self.session.faults)})")

    def do_gen_faults(self, arg: str) -> None:
        """gen_faults [--all-types] [--n N --seed S]
        Generate a fault list: one of each built-in type (default), or N random faults."""
        mem = self._require_memory()
        pos, flags = _parse_flags(_tokenize(arg), {"all-types": None, "n": int, "seed": int})
        if "n" in flags:
            records = generate_random_faults(mem, int(flags["n"]), int(flags.get("seed", 0)))
        else:
            records = generate_all_types_faults(mem)
        self.session.faults = records
        self._out(f"generated {len(records)} faults")

    def do_run(self, arg: str) -> None:
        """run <algo_name> [--verbose]
        Run a fault campaign for one algorithm against the current fault list."""
        mem = self._require_memory()
        pos, flags = _parse_flags(_tokenize(arg), {"verbose": None})
        if not pos:
            raise ValueError("usage: run <algo_name> [--verbose]")
        spec = self._resolve_algo(pos[0])
        workdir = self.session.next_run_dir(f"run_{spec.name}")
        result = run_algo_campaign(
            mem, spec, self.session.faults, sim=self.session.sim,
            workdir=workdir, verbose=bool(flags.get("verbose")),
        )
        self.session.last_results[spec.name] = result
        self.session.last_op = ("run", spec.name)
        self._print_result_summary(result)

    def do_compare_algo(self, arg: str) -> None:
        """compare_algo <name> -march NAME1,NAME2,...
        Run <name> plus each named algorithm and print a fault-by-fault matrix."""
        mem = self._require_memory()
        pos, flags = _parse_flags(_tokenize(arg), {"march": str})
        if not pos:
            raise ValueError("usage: compare_algo <name> -march NAME1,NAME2,...")
        others = [t for t in str(flags.get("march", "")).split(",") if t]
        names = [pos[0], *others]

        results: list[CampaignResult] = []
        for name in names:
            spec = self._resolve_algo(name)
            workdir = self.session.next_run_dir(f"cmp_{spec.name}")
            result = run_algo_campaign(mem, spec, self.session.faults, sim=self.session.sim, workdir=workdir)
            self.session.last_results[spec.name] = result
            results.append(result)

        self.session.last_matrix = results
        self.session.last_op = ("matrix", None)
        for r in results:
            self._print_result_summary(r)
        self._out("")
        self._out(render_matrix_md(results))

    def do_write_report(self, arg: str) -> None:
        """write_report <path> [--fmt md|csv|json]
        Persist the most recent 'run' or 'compare_algo' result."""
        pos, flags = _parse_flags(_tokenize(arg), {"fmt": str})
        if not pos:
            raise ValueError("usage: write_report <path> [--fmt md|csv|json]")
        fmt = str(flags.get("fmt", "md"))
        if self.session.last_op is None:
            raise ValueError("nothing to report yet -- run 'run' or 'compare_algo' first")
        op, name = self.session.last_op
        path = Path(pos[0])
        if op == "run":
            assert name is not None
            write_campaign_report(self.session.last_results[name], path, fmt=fmt)
        else:
            assert self.session.last_matrix is not None
            write_matrix_report(self.session.last_matrix, path, fmt=fmt)
        self._out(f"report written: {path}")

    def do_export_tb(self, arg: str) -> None:
        """export_tb <dir>
        Dump a self-contained bundle: engine sources, registered .alg specs,
        and the current fault list, runnable standalone via run_campaign.sh."""
        tokens = _tokenize(arg)
        if not tokens:
            raise ValueError("usage: export_tb <dir>")
        outdir = Path(tokens[0])
        outdir.mkdir(parents=True, exist_ok=True)
        engine_dir = find_engine_dir()
        for name in ("fault_ram.sv", "march_engine.sv", "openram_shim.sv", "run_campaign.sh"):
            shutil.copy2(engine_dir / name, outdir / name)
        for name, spec in self.session.algos.items():
            spec.write_numeric(outdir / f"{name}.algc")
        if self.session.faults:
            write_fault_list(self.session.faults, outdir / "faults.txt")
        self._out(f"exported testbench bundle to {outdir}")

    def do_list(self, arg: str) -> None:
        """list [algos|fsms|faults|types]
        Inspect session state (default: everything)."""
        what = (_tokenize(arg) or ["all"])[0]
        if what in ("algos", "all"):
            self._out("algos:")
            for name, spec in sorted(self.session.algos.items()):
                self._out(f"  {name}  ({spec.length_n}n, {len(spec.elements)} elements)")
        if what in ("fsms", "all"):
            self._out("fsms:")
            for name in sorted(self.session.fsms):
                self._out(f"  {name}")
            if not self.session.fsms:
                self._out("  (none -- add_fsm is not yet available)")
        if what in ("faults", "all"):
            self._out(f"faults: {len(self.session.faults)} loaded")
        if what in ("types", "all"):
            self._out("built-in fault types: " + ", ".join(BUILTIN_FAULT_TYPES))

    def do_status(self, arg: str) -> None:
        """status
        Print a one-screen summary of the current session."""
        mem = self.session.mem
        self._out(f"memory: {f'{mem.addr_width}x{mem.data_width} init={mem.init_val}' if mem else '(not set)'}")
        self._out(f"sim: {self.session.sim}")
        self._out(f"algos: {len(self.session.algos)} ({', '.join(sorted(self.session.algos))})")
        self._out(f"faults loaded: {len(self.session.faults)}")
        self._out(f"workdir: {self.session.workdir}")

    def do_set_sim(self, arg: str) -> None:
        """set_sim verilator
        Select the simulator backend (Verilator only)."""
        tokens = _tokenize(arg)
        if not tokens:
            raise ValueError("usage: set_sim verilator")
        backend = tokens[0].lower()
        if backend != "verilator":
            self._err(
                f"unsupported simulator '{backend}': the fault engine (fault_ram.sv) uses "
                "SystemVerilog queues, foreach, and final blocks, none of which Icarus "
                "Verilog supports. Use 'verilator' (Verilator 5.x)."
            )
            return
        self.session.sim = backend
        self._out("simulator set: verilator")

    def do_quit(self, arg: str) -> bool:
        """quit -- exit the shell"""
        return True

    def do_EOF(self, arg: str) -> bool:
        """EOF (Ctrl-D) -- exit the shell"""
        self._out("")
        return True
