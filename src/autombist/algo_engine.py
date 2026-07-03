"""Shared fault-campaign engine: compile the SystemVerilog fault-injectable RAM
once, run one simulation per fault (plus a golden run), and parse the output
grammar into structured results.

Two "fronts" share this engine and its output grammar:
  - the algorithm front (``march_engine.sv`` driven by a ``.alg`` spec) -- P2
  - the FSM front (a generated harness around a researcher's controller) -- P5

Both print exactly one line beginning with ``RESULT`` per run, so one parser
serves both. The engine is Verilator-only: ``fault_ram.sv`` uses SystemVerilog
queues, ``foreach``, and ``final`` blocks, none of which Icarus Verilog supports.
"""
from __future__ import annotations

import random
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .alg_spec import AlgSpec, find_engine_dir

# The 19 functional fault primitives fault_ram.sv implements natively (see
# engine/README.md). P6 (add_fault_type) will let researchers extend this set.
BUILTIN_FAULT_TYPES: tuple[str, ...] = (
    "SA0", "SA1", "TF0", "TF1", "WDF0", "WDF1", "RDF0", "RDF1", "DRDF0", "DRDF1",
    "IRF0", "IRF1", "SOF", "AF_NOACC", "AF_ALIAS", "CFIN", "CFID", "CFST", "CFDS",
)


class CampaignError(RuntimeError):
    """Raised when the fault-campaign engine cannot be built or run."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class MemoryParams:
    addr_width: int
    data_width: int
    num_wmasks: int = 1
    init_val: int = 1
    num_ports: int = 1

    @property
    def depth(self) -> int:
        return 1 << self.addr_width


@dataclass(slots=True)
class FaultRecord:
    type: str
    vaddr: int
    vbit: int
    aaddr: int = 0
    abit: int = 0
    p0: int = 0
    p1: int = 0
    vport: int = 0          # victim port. Meaningful only for the coupling-class
    aport: int = 0          # primitives (CFIN/CFID/CFST/CFDS): vport==aport is
                             # today's only mode (same-port coupling); vport!=aport
                             # means the aggressor op (on aport) disturbs a victim
                             # reachable via a different port (aport).

    def to_line(self) -> str:
        base = f"{self.type} {self.vaddr} {self.vbit} {self.aaddr} {self.abit} {self.p0} {self.p1}"
        if self.vport == 0 and self.aport == 0:
            # Byte-identical to the pre-multi-port on-disk format when both
            # ports are the default -- every existing fixture/generated file
            # stays exactly as it was.
            return base
        return f"{base} {self.vport} {self.aport}"


@dataclass(slots=True)
class FaultResult:
    index: int
    record: FaultRecord
    detected: bool
    elem: int | None = None
    op: int | None = None
    addr: int | None = None
    xor: str | None = None
    activations: int | None = None


@dataclass(slots=True)
class CampaignResult:
    algo_name: str
    mem: MemoryParams
    golden_clean: bool
    faults: list[FaultResult]
    detected: int
    total: int
    coverage_percent: float
    build_seconds: float
    run_seconds: float
    sim: str

    def matrix_row(self) -> dict[str, str]:
        """{fault label: 'D'/'E'}, keyed by 'TYPE@vaddr.vbit' to disambiguate repeats."""
        row: dict[str, str] = {}
        for r in self.faults:
            key = f"{r.record.type}@{r.record.vaddr}.{r.record.vbit}"
            row[key] = "D" if r.detected else "E"
        return row

    def to_dict(self) -> dict[str, Any]:
        return {
            "algo_name": self.algo_name,
            "mem": {
                "addr_width": self.mem.addr_width,
                "data_width": self.mem.data_width,
                "init_val": self.mem.init_val,
            },
            "sim": self.sim,
            "golden_clean": self.golden_clean,
            "detected": self.detected,
            "total": self.total,
            "coverage_percent": self.coverage_percent,
            "build_seconds": round(self.build_seconds, 3),
            "run_seconds": round(self.run_seconds, 3),
            "faults": [
                {
                    "index": r.index,
                    "type": r.record.type,
                    "vaddr": r.record.vaddr,
                    "vbit": r.record.vbit,
                    "aaddr": r.record.aaddr,
                    "abit": r.record.abit,
                    "p0": r.record.p0,
                    "p1": r.record.p1,
                    "detected": r.detected,
                    "elem": r.elem,
                    "op": r.op,
                    "addr": r.addr,
                    "xor": r.xor,
                    "activations": r.activations,
                }
                for r in self.faults
            ],
        }


@dataclass(slots=True)
class BuildArtifact:
    exe: Path
    workdir: Path
    top_module: str
    build_seconds: float


# --------------------------------------------------------------------------- #
# Fault-list I/O
# --------------------------------------------------------------------------- #
_FAULT_TYPE_RE = re.compile(r"^[A-Za-z0-9_]+$")


def parse_fault_list(text: str) -> list[FaultRecord]:
    """Each non-comment/non-blank line is 7, 8, or 9 whitespace-separated
    fields: ``TYPE VADDR VBIT AADDR ABIT P0 P1 [VPORT [APORT]]``. The two
    trailing port fields are optional and default to 0 when absent, so every
    pre-multi-port fault-list file (always exactly 7 fields) parses unchanged.
    """
    records: list[FaultRecord] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) not in (7, 8, 9):
            raise CampaignError(f"fault list line {lineno}: cannot parse '{raw}'")
        fault_type = fields[0]
        if not _FAULT_TYPE_RE.match(fault_type):
            raise CampaignError(f"fault list line {lineno}: cannot parse '{raw}'")
        try:
            nums = [int(x) for x in fields[1:]]
        except ValueError:
            raise CampaignError(f"fault list line {lineno}: cannot parse '{raw}'") from None
        va, vb, aa, ab, p0, p1, *ports = nums
        vport = ports[0] if len(ports) >= 1 else 0
        aport = ports[1] if len(ports) >= 2 else 0
        records.append(
            FaultRecord(
                type=fault_type,
                vaddr=va, vbit=vb,
                aaddr=aa, abit=ab,
                p0=p0, p1=p1,
                vport=vport, aport=aport,
            )
        )
    return records


def load_fault_list(path: Path) -> list[FaultRecord]:
    path = Path(path)
    if not path.exists():
        raise CampaignError(f"fault list not found: {path}")
    return parse_fault_list(path.read_text(encoding="utf-8"))


def write_fault_list(records: list[FaultRecord], path: Path) -> Path:
    path = Path(path)
    path.write_text("\n".join(r.to_line() for r in records) + "\n", encoding="ascii")
    return path


def generate_all_types_faults(mem: MemoryParams) -> list[FaultRecord]:
    """One instance of every built-in fault primitive, spread across the memory
    (mirrors the shape of engine/faults.example.txt, scaled to this memory)."""
    depth = mem.depth
    dw = mem.data_width
    records: list[FaultRecord] = []
    for i, t in enumerate(BUILTIN_FAULT_TYPES):
        va = (i * 7 + 3) % depth
        vb = i % dw
        aa = (va + 1) % depth  # aggressor: different word, same bit lane
        ab = vb
        p0 = p1 = 0
        if t == "CFIN":
            p0 = 2  # either direction
        elif t == "CFID":
            p0, p1 = 2, 1  # either direction, forced to 1
        elif t == "CFST":
            p0, p1 = 1, 0  # aggressor holds 1, victim forced to 0
        elif t == "CFDS":
            p0 = 4  # any read disturbs
        elif t == "AF_ALIAS":
            aa = (va + 2) % depth
        records.append(FaultRecord(t, va, vb, aa, ab, p0, p1))
    return records


def generate_random_faults(mem: MemoryParams, n: int, seed: int = 0) -> list[FaultRecord]:
    """N faults with a random type/site each, for stress-testing an algorithm."""
    rng = random.Random(seed)
    depth = mem.depth
    dw = mem.data_width
    records: list[FaultRecord] = []
    for _ in range(n):
        records.append(
            FaultRecord(
                type=rng.choice(BUILTIN_FAULT_TYPES),
                vaddr=rng.randrange(depth), vbit=rng.randrange(dw),
                aaddr=rng.randrange(depth), abit=rng.randrange(dw),
                p0=rng.randrange(3), p1=rng.randrange(2),
            )
        )
    return records


# --------------------------------------------------------------------------- #
# Output grammar
# --------------------------------------------------------------------------- #
RESULT_DETECTED_RE = re.compile(
    r"RESULT DETECTED alg=(?P<alg>\S+) elem=(?P<elem>\d+) op=(?P<op>\d+) "
    r"addr=(?P<addr>\d+) xor=(?P<xor>[01]+)"
)
RESULT_ESCAPED_RE = re.compile(r"RESULT ESCAPED alg=(?P<alg>\S+)")
FAULT_LOADED_RE = re.compile(
    r"FAULT_LOADED idx=(?P<idx>\d+) type=(?P<type>\S+) "
    r"v=(?P<va>\d+)\.(?P<vb>\d+) a=(?P<aa>\d+)\.(?P<ab>\d+) "
    r"p0=(?P<p0>-?\d+) p1=(?P<p1>-?\d+)"
)
FAULT_HITS_RE = re.compile(
    r"FAULT_HITS idx=(?P<idx>\d+) type=(?P<type>\S+) activations=(?P<hits>\d+)"
)


def parse_result_line(text: str) -> tuple[bool, int | None, int | None, int | None, str | None]:
    """Returns (detected, elem, op, addr, xor_bits)."""
    m = RESULT_DETECTED_RE.search(text)
    if m:
        return True, int(m["elem"]), int(m["op"]), int(m["addr"]), m["xor"]
    m = RESULT_ESCAPED_RE.search(text)
    if m:
        return False, None, None, None, None
    raise CampaignError(f"no RESULT line found in simulator output:\n{text[-2000:]}")


def parse_fault_loaded(text: str) -> tuple[int, str, int, int, int, int, int, int] | None:
    m = FAULT_LOADED_RE.search(text)
    if not m:
        return None
    return (
        int(m["idx"]), m["type"],
        int(m["va"]), int(m["vb"]), int(m["aa"]), int(m["ab"]),
        int(m["p0"]), int(m["p1"]),
    )


def parse_fault_hits(text: str) -> tuple[int, str, int] | None:
    m = FAULT_HITS_RE.search(text)
    if not m:
        return None
    return int(m["idx"]), m["type"], int(m["hits"])


# --------------------------------------------------------------------------- #
# Subprocess execution (mirrors runner.run_simulation's pattern)
# --------------------------------------------------------------------------- #
def _exec(cmd: list[str], *, cwd: Path, log_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if log_path is not None:
        log_path.write_text(
            "".join(part for part in (completed.stdout, completed.stderr) if part),
            encoding="utf-8",
        )
    return completed


def _require_verilator(sim: str) -> None:
    if sim != "verilator":
        raise CampaignError(
            f"unsupported simulator '{sim}': the fault engine (fault_ram.sv) uses "
            "SystemVerilog queues, foreach, and final blocks, none of which Icarus "
            "Verilog supports. Use --sim verilator (Verilator 5.x)."
        )
    if shutil.which("verilator") is None:
        raise CampaignError(
            "verilator not found on PATH. Install Verilator 5.x (this engine only "
            "runs under Verilator or a commercial SV simulator, never Icarus)."
        )


# --------------------------------------------------------------------------- #
# Build + run
# --------------------------------------------------------------------------- #
def compile_engine(
    mem: MemoryParams,
    *,
    sources: list[Path],
    top_module: str,
    workdir: Path,
    sim: str = "verilator",
) -> BuildArtifact:
    _require_verilator(sim)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    exe_name = f"{top_module}_sim"
    cmd = [
        "verilator", "--binary", "--timing",
        "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND",
        # PINMISSING: FSM harnesses (P5) deliberately connect only the required
        # port contract; optional controller outputs (e.g. bist_busy) are left
        # unconnected by design, not by omission.
        "-Wno-PINMISSING",
        f"-GAW={mem.addr_width}", f"-GDW={mem.data_width}",
        "--top-module", top_module,
        *[str(s) for s in sources],
        "-o", exe_name,
    ]
    log_path = workdir / "verilator_build.log"
    start = time.time()
    completed = _exec(cmd, cwd=workdir, log_path=log_path)
    build_seconds = time.time() - start
    if completed.returncode != 0:
        raise CampaignError(f"verilator build failed (exit {completed.returncode}). See {log_path}.")
    exe = workdir / "obj_dir" / exe_name
    if not exe.exists():
        raise CampaignError(f"verilator did not produce the expected binary: {exe}")
    return BuildArtifact(exe=exe, workdir=workdir, top_module=top_module, build_seconds=build_seconds)


# Defense-in-depth: the FSM harness has its own in-simulation watchdog
# (WATCHDOG_CYCLES in fsm_harness_template.sv.j2), but a researcher-submitted
# FSM is untrusted input -- a bug in the watchdog itself, or a genuine
# simulator-level hang, must not be able to wedge the whole campaign forever.
DEFAULT_RUN_TIMEOUT_SECONDS = 120


def run_one(
    artifact: BuildArtifact,
    *,
    alg_file: Path | None = None,
    fault_file: Path | None = None,
    index: int | None = None,
    verbose: bool = False,
    extra_plusargs: list[str] | None = None,
    timeout_seconds: float | None = DEFAULT_RUN_TIMEOUT_SECONDS,
) -> str:
    args = [str(artifact.exe)]
    if alg_file is not None:
        args.append(f"+ALG_FILE={alg_file}")
    if fault_file is not None:
        args.append(f"+FAULTS={fault_file}")
    if index is not None:
        args.append(f"+FAULT_INDEX={index}")
    if verbose:
        args.append("+FAULT_VERBOSE")
    if extra_plusargs:
        args.extend(extra_plusargs)
    try:
        completed = subprocess.run(
            args, cwd=artifact.workdir, capture_output=True, text=True, check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        partial = "".join(part for part in (exc.stdout, exc.stderr) if part)
        raise CampaignError(
            f"simulation exceeded the {timeout_seconds}s timeout (top={artifact.top_module}); "
            f"the design likely never asserts bist_done/RESULT. Partial output:\n{partial[-2000:]}"
        ) from exc
    return (completed.stdout or "") + (completed.stderr or "")


def _common_plusargs(mem: MemoryParams) -> list[str]:
    return [f"+INIT={mem.init_val}"]


def run_algo_campaign(
    mem: MemoryParams,
    alg: AlgSpec,
    faults: list[FaultRecord],
    *,
    sim: str = "verilator",
    workdir: Path | None = None,
    verbose: bool = False,
    fault_ram_sv: Path | None = None,
) -> CampaignResult:
    """Compile march_engine once, run a golden pass, then one run per fault."""
    own_tmp: tempfile.TemporaryDirectory[str] | None = None
    if workdir is None:
        own_tmp = tempfile.TemporaryDirectory(prefix="autombist-algo-")
        workdir = Path(own_tmp.name)
    else:
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)

    try:
        engine_dir = find_engine_dir()
        fault_ram_sv = fault_ram_sv or (engine_dir / "fault_ram.sv")
        march_sv = engine_dir / "march_engine.sv"

        artifact = compile_engine(
            mem, sources=[fault_ram_sv, march_sv], top_module="march_engine",
            workdir=workdir, sim=sim,
        )

        alg_file = alg.write_numeric(workdir / f"{alg.name}.algc")
        fault_file = write_fault_list(faults, workdir / "faults.txt") if faults else None
        plusargs = _common_plusargs(mem)

        start = time.time()

        golden_out = run_one(artifact, alg_file=alg_file, extra_plusargs=plusargs)
        golden_detected, *_ = parse_result_line(golden_out)
        if golden_detected:
            raise CampaignError(
                f"golden run for algorithm '{alg.name}' unexpectedly reported DETECTED "
                f"(no faults were injected). The algorithm spec or engine is broken.\n{golden_out}"
            )

        results: list[FaultResult] = []
        for i, record in enumerate(faults):
            out = run_one(
                artifact, alg_file=alg_file, fault_file=fault_file, index=i,
                verbose=verbose, extra_plusargs=plusargs,
            )
            detected, elem, op, addr, xor_bits = parse_result_line(out)
            activations = None
            if verbose:
                hits = parse_fault_hits(out)
                activations = hits[2] if hits else None
            results.append(
                FaultResult(
                    index=i, record=record, detected=detected,
                    elem=elem, op=op, addr=addr, xor=xor_bits, activations=activations,
                )
            )

        run_seconds = time.time() - start
        detected_count = sum(1 for r in results if r.detected)
        total = len(results)
        coverage = 100.0 if total == 0 else (detected_count / total) * 100.0

        return CampaignResult(
            algo_name=alg.name, mem=mem, golden_clean=True, faults=results,
            detected=detected_count, total=total, coverage_percent=coverage,
            build_seconds=artifact.build_seconds, run_seconds=run_seconds, sim=sim,
        )
    finally:
        if own_tmp is not None:
            own_tmp.cleanup()


def run_fsm_campaign(
    mem: MemoryParams,
    fsm_sources: list[Path],
    fsm_module_name: str,
    faults: list[FaultRecord],
    *,
    sim: str = "verilator",
    workdir: Path | None = None,
    fault_ram_sv: Path | None = None,
) -> CampaignResult:
    """Compile FSM + openram_shim + fault_ram (via a generated harness) once,
    golden-gate, then one run per fault. Detection is bist_fail only -- no
    elem/op attribution, since a black-box controller has no step counter.
    """
    from .fsm_harness import HARNESS_TOP, parse_ports, render_harness

    own_tmp: tempfile.TemporaryDirectory[str] | None = None
    if workdir is None:
        own_tmp = tempfile.TemporaryDirectory(prefix="autombist-fsm-")
        workdir = Path(own_tmp.name)
    else:
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)

    try:
        engine_dir = find_engine_dir()
        fault_ram_sv = fault_ram_sv or (engine_dir / "fault_ram.sv")
        shim_sv = engine_dir / "openram_shim.sv"

        # bist_busy is optional in the FSM port contract; only wire it up if
        # this FSM actually declares it, or Verilator errors on a named
        # connection to a port the target module doesn't have.
        top_text = fsm_sources[0].read_text(encoding="utf-8")
        has_bist_busy = parse_ports(top_text, module_name=fsm_module_name).ports.get("bist_busy") == "output"

        harness_path = workdir / f"{HARNESS_TOP}.sv"
        harness_path.write_text(
            render_harness(
                addr_width=mem.addr_width, data_width=mem.data_width,
                fsm_module_name=fsm_module_name, has_bist_busy=has_bist_busy,
            ),
            encoding="utf-8",
        )

        artifact = compile_engine(
            mem,
            sources=[fault_ram_sv, shim_sv, *fsm_sources, harness_path],
            top_module=HARNESS_TOP,
            workdir=workdir,
            sim=sim,
        )

        fault_file = write_fault_list(faults, workdir / "faults.txt") if faults else None
        plusargs = _common_plusargs(mem)

        start = time.time()

        golden_out = run_one(artifact, extra_plusargs=plusargs)
        golden_detected, *_ = parse_result_line(golden_out)
        if golden_detected:
            raise CampaignError(
                f"golden run for FSM '{fsm_module_name}' unexpectedly reported DETECTED "
                f"(no faults were injected). The FSM or harness wiring is broken.\n{golden_out}"
            )

        results: list[FaultResult] = []
        for i, record in enumerate(faults):
            out = run_one(artifact, fault_file=fault_file, index=i, extra_plusargs=plusargs)
            detected, elem, op, addr, xor_bits = parse_result_line(out)
            results.append(
                FaultResult(index=i, record=record, detected=detected, elem=elem, op=op, addr=addr, xor=xor_bits)
            )

        run_seconds = time.time() - start
        detected_count = sum(1 for r in results if r.detected)
        total = len(results)
        coverage = 100.0 if total == 0 else (detected_count / total) * 100.0

        return CampaignResult(
            algo_name=f"FSM:{fsm_module_name}", mem=mem, golden_clean=True, faults=results,
            detected=detected_count, total=total, coverage_percent=coverage,
            build_seconds=artifact.build_seconds, run_seconds=run_seconds, sim=sim,
        )
    finally:
        if own_tmp is not None:
            own_tmp.cleanup()
