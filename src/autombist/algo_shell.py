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
import json
import os
import shlex
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .alg_spec import MAX_ELEMENTS, MAX_OPS, AlgSpec, AlgSpecError, builtin_algos, find_engine_dir, load_alg_file
from .algo_engine import (
    BUILTIN_FAULT_TYPES,
    CampaignResult,
    FaultRecord,
    MemoryParams,
    generate_all_types_faults,
    generate_random_faults,
    load_fault_list,
    run_algo_campaign,
    run_fsm_campaign,
    write_fault_list,
)
from .algo_reporting import render_matrix_md, write_campaign_report, write_diagnosis_report, write_matrix_report
from .fault_primitives import FaultPrimitive, FaultPrimitiveError, default_registry, from_dict, validate
from .fault_ram_gen import render_and_write
from .fsm_harness import check_ports, gather_sibling_sources
from .synth_engine import synthesize_alg, synth_verification_faults

# The 15 DSL-covered built-ins' names, for distinguishing "custom" registry
# entries (added via add_fault_type) from the defaults in `list types`.
_DEFAULT_REGISTRY_NAMES = frozenset(p.name for p in default_registry())

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


@dataclass(slots=True)
class FsmEntry:
    sources: list[Path]     # top file first, then any auto-gathered/explicit siblings
    module_name: str


@dataclass
class Session:
    mem: MemoryParams | None = None
    algos: dict[str, AlgSpec] = field(default_factory=dict)
    fsms: dict[str, FsmEntry] = field(default_factory=dict)
    faults: list[FaultRecord] = field(default_factory=list)
    registry: list[FaultPrimitive] = field(default_factory=default_registry)
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
        # Sequence-correctness line only appears when a --check was requested
        # (result.sequence populated); ordinary runs are unchanged.
        if result.sequence is not None:
            seq = result.sequence
            if seq.matches:
                self._out(f"  sequence: OK ({seq.observed_count} ops match spec)")
            else:
                self._out(f"  sequence: MISMATCH ({seq.observed_count} observed vs {seq.expected_count} expected)")
                for line in seq.message().splitlines()[1:]:
                    self._out(line)

    def _render_fault_ram_for(self, workdir: Path) -> Path:
        """Render fault_ram.sv from the session's registry into workdir. The
        registry starts as the 19 built-ins (default_registry() + the 4 fixed
        types the template always includes); add_fault_type appends to it, so
        this always reflects any custom types the researcher has defined.
        num_ports follows the configured memory (set_memory --ports) so a
        2-port session gets a fault_ram.sv shaped for march_engine_mp.sv,
        matching what _resolve_engine_sources dispatches to."""
        num_ports = self.session.mem.num_ports if self.session.mem is not None else 1
        return render_and_write(self.session.registry, workdir / "fault_ram.sv", num_ports=num_ports)

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
        """set_memory <addr_width> <data_width> [--wmasks N] [--init 0|1] [--ports 1|2]
        Configure the memory under test. --ports selects how many physical
        ports the fault engine models (default 1); 2 enables genuine
        cross-port coupling faults (see add_fault's vport/aport args) via
        march_engine_mp.sv (see MemoryParams.num_ports)."""
        pos, flags = _parse_flags(_tokenize(arg), {"wmasks": int, "init": int, "ports": int})
        if len(pos) < 2:
            raise ValueError(
                "usage: set_memory <addr_width> <data_width> [--wmasks N] [--init 0|1] [--ports 1|2]"
            )
        aw, dw = int(pos[0]), int(pos[1])
        num_ports = int(flags.get("ports", 1))
        if num_ports not in (1, 2):
            raise ValueError(f"--ports must be 1 or 2, got {num_ports}")
        self.session.mem = MemoryParams(
            addr_width=aw, data_width=dw,
            num_wmasks=int(flags.get("wmasks", 1)), init_val=int(flags.get("init", 1)),
            num_ports=num_ports,
        )
        self._out(
            f"memory set: {aw}x{dw}, init={self.session.mem.init_val}, ports={num_ports}"
        )

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

    def do_add_fsm(self, arg: str) -> None:
        """add_fsm <top.sv> [<dep1.sv> <dep2.sv> ...] [--name NAME] [--top MODULE]
        Register a controller FSM (must expose the March-FSM port contract:
        clk/rst_n/bist_start in, bist_done/bist_fail out, sram_* bus). With a
        single file, sibling .sv/.v files in the same directory are pulled in
        automatically (matches this repo's rtl/<algo>/ layout); pass multiple
        files explicitly to override.
        If the session's memory was configured with `set_memory --ports 2`,
        the FSM is validated against the 2-port contract (sram_*0 AND
        sram_*1 buses both required); otherwise the single-port contract
        (today's default) applies."""
        pos, flags = _parse_flags(_tokenize(arg), {"name": str, "top": str})
        if not pos:
            raise ValueError("usage: add_fsm <top.sv> [<dep.sv> ...] [--name NAME] [--top MODULE]")
        top_path = Path(pos[0])
        if not top_path.exists():
            raise ValueError(f"FSM source not found: {top_path}")
        sources = [Path(p).resolve() for p in pos] if len(pos) > 1 else gather_sibling_sources(top_path)
        num_ports = self.session.mem.num_ports if self.session.mem is not None else 1
        ports = check_ports(
            top_path.read_text(encoding="utf-8"), module_name=flags.get("top"), num_ports=num_ports,
        )
        module_name = str(flags.get("top", ports.module_name))
        name = str(flags.get("name", top_path.stem))
        self.session.fsms[name] = FsmEntry(sources=sources, module_name=module_name)
        self._out(f"FSM '{name}' registered (module {module_name}, {len(sources)} source file(s))")

    def do_add_fault_type(self, arg: str) -> None:
        """add_fault_type <json> | --file <path.json>
        Define a new fault primitive from a JSON spec:
          {"name": "MYCF", "category": "write_effect",
           "sensitize": {"transition": "p0", "on": "aggressor"},
           "effect": {"kind": "invert"}}
        category is one of static_clamp | write_effect | read_effect (not
        addr_decoder -- AF_NOACC/AF_ALIAS remain the only address-decoder
        faults). See fault_primitives.py's module docstring for the full
        schema, the per-site variable scope, and the raw_sv escape hatch.
        Takes effect on the next run/compare_algo (fault_ram.sv is
        regenerated from the updated registry)."""
        stripped = arg.strip()
        if stripped.startswith("--file"):
            # Only the file PATH goes through the shell tokenizer here -- a
            # simple single argument, same treatment as add_algo/load_faults.
            file_tokens = _tokenize(stripped[len("--file") :])
            if not file_tokens:
                raise ValueError("usage: add_fault_type --file <path.json>")
            payload = json.loads(Path(file_tokens[0]).read_text(encoding="utf-8"))
        elif stripped:
            # The JSON blob is parsed directly from the raw argument, NOT
            # through _tokenize()/shlex: shlex's POSIX quote-stripping (needed
            # so file-path arguments elsewhere can contain spaces) treats
            # JSON's own double quotes as shell grouping syntax and strips
            # them, corrupting the payload (e.g. {"name": "X"} -> {name: X}).
            payload = json.loads(stripped)
        else:
            raise ValueError("usage: add_fault_type <json> | --file <path.json>")
        try:
            prim = from_dict(payload)
        except (KeyError, TypeError) as exc:
            raise FaultPrimitiveError(f"malformed fault-primitive spec: {exc}") from exc
        existing = {p.name for p in self.session.registry}
        validate(prim, existing_names=existing)
        self.session.registry.append(prim)
        self._out(f"fault type '{prim.name}' registered ({prim.category}); takes effect on the next run")

    def do_add_fault(self, arg: str) -> None:
        """add_fault TYPE VADDR VBIT [AADDR ABIT P0 P1 [VPORT APORT]]
        Append one fault instance to the current fault list. VPORT/APORT
        (default 0) select which physical port the victim/aggressor access is
        on -- meaningful only for the coupling-class primitives (CFIN/CFID/
        CFST/CFDS) in a 2-port memory (see set_memory --ports); a VPORT !=
        APORT defines a genuine cross-port coupling fault."""
        tokens = _tokenize(arg)
        if len(tokens) not in (3, 7, 9):
            raise ValueError("usage: add_fault TYPE VADDR VBIT [AADDR ABIT P0 P1 [VPORT APORT]]")
        fault_type, va, vb = tokens[0], int(tokens[1]), int(tokens[2])
        aa, ab, p0, p1 = (int(x) for x in tokens[3:7]) if len(tokens) >= 7 else (0, 0, 0, 0)
        vport, aport = (int(x) for x in tokens[7:9]) if len(tokens) == 9 else (0, 0)
        self.session.faults.append(FaultRecord(fault_type, va, vb, aa, ab, p0, p1, vport, aport))
        suffix = f" ports={vport}/{aport}" if (vport, aport) != (0, 0) else ""
        self._out(f"fault added: {fault_type} v={va}.{vb}{suffix} (total {len(self.session.faults)})")

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
        """run <algo_name|fsm_name> [--verbose] [--check ALGO]
        Run a fault campaign for one algorithm, or a registered FSM (from
        add_fsm), against the current fault list. FSM runs report detect/
        escape only (--verbose has no effect for them).
        --check ALGO (FSM targets only): also verify the controller drives the
        exact march sequence of ALGO (a built-in name or a .alg path), address
        order / ops / write data / port -- independent of fault detection."""
        mem = self._require_memory()
        pos, flags = _parse_flags(_tokenize(arg), {"verbose": None, "check": str})
        if not pos:
            raise ValueError("usage: run <algo_name|fsm_name> [--verbose] [--check ALGO]")
        name = pos[0]

        if name in self.session.fsms:
            entry = self.session.fsms[name]
            workdir = self.session.next_run_dir(f"run_fsm_{name}")
            fault_ram_sv = self._render_fault_ram_for(workdir)
            # Pass expected_spec ONLY when --check was given, so a plain FSM run
            # calls run_fsm_campaign byte-identically to before this feature.
            fsm_kwargs: dict[str, object] = {}
            if flags.get("check"):
                fsm_kwargs["expected_spec"] = self._resolve_algo(str(flags["check"]))
            result = run_fsm_campaign(
                mem, entry.sources, entry.module_name, self.session.faults,
                sim=self.session.sim, workdir=workdir, fault_ram_sv=fault_ram_sv,
                **fsm_kwargs,
            )
            result.algo_name = name
        else:
            if flags.get("check"):
                raise ValueError("--check applies only to a registered FSM target (an algorithm IS its own reference sequence)")
            spec = self._resolve_algo(name)
            workdir = self.session.next_run_dir(f"run_{spec.name}")
            fault_ram_sv = self._render_fault_ram_for(workdir)
            result = run_algo_campaign(
                mem, spec, self.session.faults, sim=self.session.sim,
                workdir=workdir, verbose=bool(flags.get("verbose")), fault_ram_sv=fault_ram_sv,
            )
            name = spec.name

        self.session.last_results[name] = result
        self.session.last_op = ("run", name)
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
            fault_ram_sv = self._render_fault_ram_for(workdir)
            result = run_algo_campaign(
                mem, spec, self.session.faults, sim=self.session.sim,
                workdir=workdir, fault_ram_sv=fault_ram_sv,
            )
            self.session.last_results[spec.name] = result
            results.append(result)

        self.session.last_matrix = results
        self.session.last_op = ("matrix", None)
        for r in results:
            self._print_result_summary(r)
        self._out("")
        self._out(render_matrix_md(results))

    def do_synth(self, arg: str) -> None:
        """synth <name> [--elements N] [--max-ops N] [--verify] [--write PATH]
        Synthesize a new march test from the CURRENT fault-type registry
        (built-ins plus any add_fault_type additions) via the Pattern-Graph
        greedy-walk algorithm (Benso et al., ETS 2005 / DATE 2006). Registers
        the result into session.algos[name] -- immediately usable by
        'run'/'compare_algo', same contract as add_algo.
          --elements N  cap on march elements (default alg_spec.MAX_ELEMENTS)
          --max-ops N   cap on ops per element (default alg_spec.MAX_OPS)
          --verify      run the synthesized test through a real Verilator
                        campaign against the targeted primitives and print
                        detected/total (also becomes the 'last run' result,
                        usable by write_report/write_diagnosis)
          --write PATH  also write the human .alg text form
        Never targets SOF/AF_NOACC/AF_ALIAS/CFDS (structurally fixed types,
        not expressible in the Sensitize/Effect DSL -- see
        fault_primitives.py) or any raw_sv custom primitive (no DSL
        description to synthesize against) -- the printed summary always
        states "targets M/N" so the exclusion is visible, never implied."""
        pos, flags = _parse_flags(
            _tokenize(arg), {"elements": int, "max-ops": int, "verify": None, "write": str}
        )
        if not pos:
            raise ValueError("usage: synth <name> [--elements N] [--max-ops N] [--verify] [--write PATH]")
        name = pos[0]
        init_val = self.session.mem.init_val if self.session.mem is not None else 1
        result = synthesize_alg(
            self.session.registry, name,
            max_elements=int(flags.get("elements", MAX_ELEMENTS)),
            max_ops=int(flags.get("max-ops", MAX_OPS)),
            init_val=init_val,
        )
        self.session.algos[name] = result.spec
        total = len(result.targeted) + len(result.excluded_fixed)
        self._out(
            f"synthesized '{name}': {result.spec.length_n}n, {len(result.spec.elements)} elements -- "
            f"targets {len(result.targeted)}/{total} registry primitives "
            f"(excludes {', '.join(result.excluded_fixed)}: structurally fixed types)"
        )
        if result.uncovered:
            self._out(f"  WARNING: {len(result.uncovered)} NOT covered within caps: {', '.join(result.uncovered)}")
        else:
            self._out(f"  covered: {len(result.covered)}/{len(result.targeted)}")
        if "write" in flags:
            path = Path(str(flags["write"]))
            result.spec.write_text(path)
            self._out(f"  .alg text written: {path}")
        if flags.get("verify"):
            mem = self._require_memory()
            targets = [p for p in self.session.registry if p.name in result.targeted]
            faults = synth_verification_faults(mem, targets)
            workdir = self.session.next_run_dir(f"synth_verify_{name}")
            fault_ram_sv = self._render_fault_ram_for(workdir)
            campaign = run_algo_campaign(
                mem, result.spec, faults, sim=self.session.sim,
                workdir=workdir, fault_ram_sv=fault_ram_sv,
            )
            self.session.last_results[name] = campaign
            self.session.last_op = ("run", name)
            self._print_result_summary(campaign)

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

    def do_write_diagnosis(self, arg: str) -> None:
        """write_diagnosis <path> [--fmt md|csv|json]
        Persist a per-cell (addr, bit) diagnosis/fail-bitmap report for the
        most recent 'run' result. Only a single 'run' result has one obvious
        cell-table shape; if the last op was 'compare_algo', this raises an
        error instead -- diagnose each algorithm individually via 'run'."""
        pos, flags = _parse_flags(_tokenize(arg), {"fmt": str})
        if not pos:
            raise ValueError("usage: write_diagnosis <path> [--fmt md|csv|json]")
        fmt = str(flags.get("fmt", "md"))
        if self.session.last_op is None:
            raise ValueError("nothing to diagnose yet -- run 'run' first")
        op, name = self.session.last_op
        if op != "run":
            raise ValueError(
                "diagnosis only applies to a single 'run' result, not 'compare_algo' -- "
                "a cross-algorithm diagnosis view has no single obvious cell-table shape. "
                "Run 'run <algo_name>' for the algorithm you want to diagnose, then retry."
            )
        assert name is not None
        path = Path(pos[0])
        write_diagnosis_report(self.session.last_results[name], path, fmt=fmt)
        self._out(f"diagnosis written: {path}")

    def do_export_tb(self, arg: str) -> None:
        """export_tb <dir>
        Dump a self-contained bundle: engine sources (fault_ram.sv regenerated
        from the session's registry, so any custom types via add_fault_type
        are included), registered .alg specs, and the current fault list,
        runnable standalone via run_campaign.sh."""
        tokens = _tokenize(arg)
        if not tokens:
            raise ValueError("usage: export_tb <dir>")
        outdir = Path(tokens[0])
        outdir.mkdir(parents=True, exist_ok=True)
        engine_dir = find_engine_dir()
        self._render_fault_ram_for(outdir)
        for name in ("march_engine.sv", "openram_shim.sv", "run_campaign.sh"):
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
            for name, entry in sorted(self.session.fsms.items()):
                self._out(f"  {name}  (module {entry.module_name}, {len(entry.sources)} source file(s))")
            if not self.session.fsms:
                self._out("  (none registered; use add_fsm)")
        if what in ("faults", "all"):
            self._out(f"faults: {len(self.session.faults)} loaded")
        if what in ("types", "all"):
            self._out("fault types (usable in add_fault/load_faults/gen_faults): " + ", ".join(BUILTIN_FAULT_TYPES))
            custom = sorted(p.name for p in self.session.registry if p.name not in _DEFAULT_REGISTRY_NAMES)
            if custom:
                self._out("custom types (added via add_fault_type): " + ", ".join(custom))

    def do_status(self, arg: str) -> None:
        """status
        Print a one-screen summary of the current session."""
        mem = self.session.mem
        if mem is None:
            mem_line = "(not set)"
        else:
            mem_line = f"{mem.addr_width}x{mem.data_width} init={mem.init_val}"
            if mem.num_ports > 1:
                mem_line += f" ports={mem.num_ports}"
        self._out(f"memory: {mem_line}")
        self._out(f"sim: {self.session.sim}")
        self._out(f"algos: {len(self.session.algos)} ({', '.join(sorted(self.session.algos))})")
        self._out(f"fsms: {len(self.session.fsms)} ({', '.join(sorted(self.session.fsms))})")
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
