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

import concurrent.futures
import functools
import hashlib
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .alg_spec import WAIT_BASE, AlgSpec, expand_expected_blocks, find_engine_dir
from .seq_check import SequenceResult, compare_trace, parse_observed_trace

# fault_ram.sv natively implements 31 functional fault primitives (see
# engine/README.md). This tuple lists the 29 that are UNCONDITIONALLY
# available; DRF and HSD are the other two, added at call time -- see below.
# P6 (add_fault_type) will let researchers extend this set.
#
# DRF and HSD (Workstreams K/L) are deliberately absent from this STATIC
# tuple: both depend on a mem.* property at call time, so generate_all_types_
# faults/generate_random_faults below include each CONDITIONALLY via
# _effective_all_types rather than baking a runtime-dependent fact into this
# module-level constant. DRF needs mem.num_ports == 1 -- its idle-cycle
# tracking is a single scalar register not yet extended to num_ports=2, and a
# fault list that actually loads a DRF entry against a num_ports=2
# fault_ram.sv fails loud (FATAL + $finish, see fault_ram_template.sv.j2) --
# unconditionally including it here would crash `gen_faults --all-types` for
# every multi-port memory. HSD needs mem.words_per_row > 1 -- it needs
# another same-row address to ever be written, which does not exist at the
# default words_per_row=1 (see engine/README.md).
#
# Excluding DRF whenever an algorithm CAN'T detect it (no built-in march
# algorithm contains a wait op) was tried first and is the wrong fix: that
# conflates "the memory structurally cannot exhibit this" (HSD's actual
# condition) with "the algorithm about to run happens not to look for this"
# (every fault type in this list has algorithms that miss it -- that is what
# a coverage report is FOR). It silently dropped DRF from `gen_faults
# --all-types` even for a caller with their own wait-containing custom
# algorithm, understating what "all types" actually covers.
BUILTIN_FAULT_TYPES: tuple[str, ...] = (
    "SA0", "SA1", "TF0", "TF1", "WDF0", "WDF1", "RDF0", "RDF1", "DRDF0", "DRDF1",
    "IRF0", "IRF1", "SOF", "AF_NOACC", "AF_ALIAS", "CFIN", "CFID", "CFST", "CFDS",
    # Two-cell coupling family (DATE 2006 Tbl 2). NOTE this tuple is NOT derived
    # from fault_primitives.default_registry() -- the two lists are maintained
    # by hand and must agree, or gen_faults emits a type the engine has no code
    # for and the simulation dies with "unknown fault type".
    "CFTR0", "CFTR1", "CFWD0", "CFWD1", "CFRD0", "CFRD1", "CFIR0", "CFIR1",
    "CFDRD0", "CFDRD1",
)

# Coupling types whose P0 carries the aggressor's required hold state (the
# CFST convention). Kept next to BUILTIN_FAULT_TYPES so the two stay together.
_AGGRESSOR_HOLD_TYPES = frozenset({
    "CFTR0", "CFTR1", "CFWD0", "CFWD1", "CFRD0", "CFRD1", "CFIR0", "CFIR1",
    "CFDRD0", "CFDRD1",
})


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
    words_per_row: int = 1   # physical-row width for HSD (Half-Select Disturb,
                               # Workstream L): row(addr) = addr / words_per_row.
                               # Default 1 -> row(addr) = addr, so "different
                               # address, same row" is mathematically unsatisfiable
                               # and HSD is provably inert -- see engine/README.md
                               # and flow/multimem/mbist/README.md's pre-existing
                               # words_per_row finding (same term, same formula).
                               # Validated (>=1, <=depth, depth%words_per_row==0)
                               # at point-of-use (algo_shell.do_set_memory AND
                               # compile_engine both call _validate_words_per_row),
                               # not in this dataclass -- MemoryParams itself stays
                               # a plain data holder, matching every other field
                               # here (e.g. num_ports' own range check also lives
                               # in its callers, not a dataclass __post_init__).

    @property
    def depth(self) -> int:
        return 1 << self.addr_width


@dataclass(slots=True, frozen=True)
class DataBackground:
    """A word-oriented data background (van de Goor & Al-Ars): a DW-bit mask
    such that a nominal w0/r0 op drives/expects `mask` and w1/r1 drives/
    expects `~mask`. mask=0 is the solid background -- today's only mode,
    reproduced byte-identically."""
    name: str
    mask: int


def standard_backgrounds(data_width: int) -> list[DataBackground]:
    """[DataBackground('solid', 0)] plus ceil(log2(data_width)) column-stripe
    backgrounds: mask_k has bit i set iff bit k of i is set, for k in
    range(ceil(log2(data_width))). Any two distinct bit lanes i != j differ
    in at least one bit of their binary index, so they differ under at least
    one mask_k -- the property needed to expose every intra-word bit-lane
    pair to an opposite-polarity write at least once."""
    backgrounds = [DataBackground("solid", 0)]
    num_stripes = (data_width - 1).bit_length()
    for k in range(num_stripes):
        mask = 0
        for i in range(data_width):
            if (i >> k) & 1:
                mask |= 1 << i
        backgrounds.append(DataBackground(f"stripe{k}", mask))
    return backgrounds


@dataclass(slots=True)
class FaultRecord:
    type: str
    vaddr: int
    vbit: int
    aaddr: int = 0
    abit: int = 0
    p0: int = 0
    p1: int = 0
    vport: int = 0          # NOT YET HONOURED -- parsed and carried through to
                             # FQ[i].vp, but no expression in the generated engine
                             # reads it, so setting it is a no-op for every fault
                             # type today. Reserved for a future per-port victim
                             # gate; the victim-side guards match on address/bit
                             # alone. See fault_ram_template.sv.j2's header.
    aport: int = 0          # aggressor port, and the one that IS load-bearing:
                             # gates the aggressor match via `FQ[i].ap != port`.
                             # Honoured by CFIN/CFID (write-aggressor loop) and
                             # CFDS (both loops) -- but NOT by CFST, whose arm
                             # lives in the portless clamp_static().
    weight: float | None = None   # optional relative-likelihood weight for a future
                                    # IFA/SPICE-derived campaign (see fault_primitives.py's
                                    # module docstring for the adapter contract this feeds).
                                    # None == unweighted -- today's only mode.

    def to_line(self) -> str:
        base = f"{self.type} {self.vaddr} {self.vbit} {self.aaddr} {self.abit} {self.p0} {self.p1}"
        if self.weight is None:
            if self.vport == 0 and self.aport == 0:
                # Byte-identical to the pre-multi-port on-disk format when both
                # ports are the default -- every existing fixture/generated file
                # stays exactly as it was.
                return base
            return f"{base} {self.vport} {self.aport}"
        # weight forces vport/aport to be emitted (even at 0) so weight stays
        # unambiguously the 10th positional field -- never confusable with the
        # 8/9-field vport/aport-only formats.
        return f"{base} {self.vport} {self.aport} {self.weight!r}"


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
    sequence: SequenceResult | None = None   # populated only when a sequence
                                             # check was requested (FSM front)
    backgrounds_run: list[str] | None = None  # populated only by
                                               # merge_background_results

    def matrix_row(self) -> dict[str, str]:
        """{fault label: 'D'/'E'}, keyed by 'TYPE@vaddr.vbit' to disambiguate
        repeats. When mem.num_ports > 1, the key also carries '#vport.aport'
        so two otherwise-identical-site faults that only differ by which
        physical port the victim/aggressor access uses (e.g. a same-port vs.
        cross-port coupling variant of the same CFIN@addr.bit) get distinct
        rows instead of colliding; single-port sessions (num_ports == 1, the
        default) keep the original, unsuffixed key unchanged."""
        row: dict[str, str] = {}
        for r in self.faults:
            key = f"{r.record.type}@{r.record.vaddr}.{r.record.vbit}"
            if self.mem.num_ports > 1:
                key = f"{key}#{r.record.vport}.{r.record.aport}"
            row[key] = "D" if r.detected else "E"
        return row

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "algo_name": self.algo_name,
            "mem": {
                "addr_width": self.mem.addr_width,
                "data_width": self.mem.data_width,
                "init_val": self.mem.init_val,
                "num_ports": self.mem.num_ports,
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
                    "vport": r.record.vport,
                    "aport": r.record.aport,
                    "weight": r.record.weight,
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
        # Sequence-correctness result is present only when a check was requested
        # (FSM front with an expected spec); omitted entirely otherwise so
        # existing consumers see a byte-identical dict.
        if self.sequence is not None:
            d["sequence"] = {
                "matches": self.sequence.matches,
                "expected_count": self.sequence.expected_count,
                "observed_count": self.sequence.observed_count,
                "divergences": [
                    {
                        "port": dv.port,
                        "index": dv.index,
                        "expected": dv.expected,
                        "observed": dv.observed,
                    }
                    for dv in self.sequence.divergences
                ],
            }
        # Present only for a merge_background_results output; omitted
        # entirely otherwise so existing consumers see a byte-identical dict.
        if self.backgrounds_run is not None:
            d["backgrounds_run"] = list(self.backgrounds_run)
        return d


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
    """Each non-comment/non-blank line is 7, 8, 9, or 10 whitespace-separated
    fields: ``TYPE VADDR VBIT AADDR ABIT P0 P1 [VPORT [APORT [WEIGHT]]]``. The
    two trailing port fields are optional and default to 0 when absent, so
    every pre-multi-port fault-list file (always exactly 7 fields) parses
    unchanged. WEIGHT (10th field only) is the first non-integer trailing
    field this format has ever needed, so it's split off and parsed as a
    float *before* the all-int pass over the remaining fields.
    """
    records: list[FaultRecord] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) not in (7, 8, 9, 10):
            raise CampaignError(f"fault list line {lineno}: cannot parse '{raw}'")
        fault_type = fields[0]
        if not _FAULT_TYPE_RE.match(fault_type):
            raise CampaignError(f"fault list line {lineno}: cannot parse '{raw}'")
        weight: float | None = None
        int_fields = fields[1:]
        if len(fields) == 10:
            try:
                weight = float(fields[-1])
            except ValueError:
                raise CampaignError(f"fault list line {lineno}: cannot parse '{raw}'") from None
            if not math.isfinite(weight):
                # float() also accepts "inf"/"nan" tokens -- reject them here,
                # not downstream: a non-finite weight can't round-trip through
                # equality (nan != nan) and serializes as an invalid bare
                # NaN/Infinity literal via json.dumps (not valid per RFC 8259).
                raise CampaignError(f"fault list line {lineno}: weight must be finite, got '{fields[-1]}'")
            int_fields = fields[1:-1]
        try:
            nums = [int(x) for x in int_fields]
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
                weight=weight,
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


def _validate_fault_addresses(mem: MemoryParams, faults: list[FaultRecord]) -> None:
    """A fault record's address/bit/port fields feed registers in the
    generated testbench that are exactly ADDR_WIDTH/DATA_WIDTH/num_ports bits
    wide, with no bounds check downstream -- an out-of-range value (most
    likely from a hand-authored --faults file; the generator functions above
    all construct addresses in range by their own arithmetic) silently wraps
    via Verilog truncation onto some OTHER, unintended cell instead of
    failing loudly. generate_all_types_faults/generate_random_faults never
    trip this, since depth/dw-modulo construction keeps them in range by
    definition -- this exists for the one path that bypasses them: a
    user-supplied fault-list file loaded via load_fault_list.
    """
    for i, f in enumerate(faults):
        for label, value, bound in (
            ("vaddr", f.vaddr, mem.depth),
            ("aaddr", f.aaddr, mem.depth),
            ("vbit", f.vbit, mem.data_width),
            ("abit", f.abit, mem.data_width),
            ("vport", f.vport, mem.num_ports),
            ("aport", f.aport, mem.num_ports),
        ):
            if not (0 <= value < bound):
                raise CampaignError(
                    f"fault #{i} ({f.type} vaddr={f.vaddr} vbit={f.vbit} aaddr={f.aaddr} "
                    f"abit={f.abit}): {label}={value} is out of range for this memory "
                    f"({label} must be in [0, {bound}))"
                )


def _effective_all_types(mem: MemoryParams) -> tuple[str, ...]:
    """BUILTIN_FAULT_TYPES, plus DRF when mem.num_ports == 1 and HSD when
    mem.words_per_row > 1 -- see BUILTIN_FAULT_TYPES' own comment for why
    each is conditional rather than static."""
    types = BUILTIN_FAULT_TYPES
    if mem.num_ports == 1:
        types = types + ("DRF",)
    if mem.words_per_row > 1:
        types = types + ("HSD",)
    return types


def generate_all_types_faults(mem: MemoryParams) -> list[FaultRecord]:
    """One instance of every built-in fault primitive, spread across the memory
    (mirrors the shape of engine/faults.example.txt, scaled to this memory).
    Includes DRF only when mem.num_ports == 1 and HSD only when
    mem.words_per_row > 1 (see _effective_all_types)."""
    depth = mem.depth
    dw = mem.data_width
    records: list[FaultRecord] = []
    for i, t in enumerate(_effective_all_types(mem)):
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
        elif t in _AGGRESSOR_HOLD_TYPES:
            # P0 is the aggressor's required hold state (CFST's convention).
            # 1 rather than 0 so the choice is not indistinguishable from the
            # p0=0 default a missing branch would leave behind.
            p0 = 1
        elif t == "CFDS":
            p0 = 4  # any read disturbs
        elif t == "AF_ALIAS":
            aa = (va + 2) % depth
        elif t == "DRF":
            # AADDR/ABIT unused (matches SOF/AF_NOACC's convention). P0 is the
            # idle-cycle threshold, not a polarity/direction selector like
            # every other type here -- 20 is an arbitrary but comfortably
            # small value (see engine/README.md's "wait ops repeat once per
            # address" cost note): it never fires against any built-in march
            # algorithm (none contains a wait op, so this always reports
            # ESCAPED there, which is the honest, expected result -- see
            # BUILTIN_FAULT_TYPES' own comment), only against a caller's own
            # wait-containing custom algorithm, where it needs to be small
            # enough that a modest wait duration exceeds it.
            aa, ab = 0, 0
            p0 = 20
        elif t == "HSD":
            # No fixed aggressor address (unlike the coupling types above) --
            # AADDR/ABIT unused, write 0 (matches SOF/AF_NOACC's convention).
            # p0 = disturbed-toward polarity, chosen opposite of init_val so a
            # real disturb is actually observable rather than a same-value no-op.
            aa, ab = 0, 0
            p0 = 0 if mem.init_val else 1
        records.append(FaultRecord(t, va, vb, aa, ab, p0, p1))
    return records


def generate_random_faults(mem: MemoryParams, n: int, seed: int = 0) -> list[FaultRecord]:
    """N faults with a random type/site each, for stress-testing an algorithm.
    Includes DRF only when mem.num_ports == 1 and HSD only when
    mem.words_per_row > 1 (see _effective_all_types)."""
    rng = random.Random(seed)
    depth = mem.depth
    dw = mem.data_width
    types = _effective_all_types(mem)
    records: list[FaultRecord] = []
    for _ in range(n):
        records.append(
            FaultRecord(
                type=rng.choice(types),
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
def _validate_words_per_row(mem: MemoryParams) -> None:
    """words_per_row must describe a shape a real column-muxed macro could
    actually have: >=1 (0/negative divides by zero or is meaningless), <=depth
    and an exact divisor of depth (a partial trailing physical row can't exist
    -- see flow/multimem/mbist/README.md's words_per_row finding, which derives
    ADDR_WIDTH from an EXACT words+spares/words_per_row relationship). Called by
    both compile_engine (algo front) and run_fsm_campaign's FSM-front guard."""
    wpr = mem.words_per_row
    if wpr < 1:
        raise CampaignError(f"mem.words_per_row must be >= 1, got {wpr}")
    if wpr > mem.depth:
        raise CampaignError(
            f"mem.words_per_row={wpr} exceeds depth={mem.depth} -- this would silently "
            "degenerate 'same row' into 'same memory' rather than model a real row shape"
        )
    if mem.depth % wpr != 0:
        raise CampaignError(
            f"mem.words_per_row={wpr} does not evenly divide depth={mem.depth} -- a real "
            "column-muxed macro's row decoder can't produce a partial trailing row"
        )


_ENGINE_CACHE_ENV = "AUTOMBIST_ENGINE_CACHE"
_ENGINE_CACHE_DISABLE_VALUES = {"0", "off", "false", "no"}


def _engine_cache_enabled() -> bool:
    return os.environ.get(_ENGINE_CACHE_ENV, "").strip().lower() not in _ENGINE_CACHE_DISABLE_VALUES


def _default_engine_cache_root() -> Path:
    # A plain subdirectory of the OS temp dir: persists across separate
    # `pytest`/CLI invocations within one machine session (unlike each call's
    # own per-run TemporaryDirectory, which is always fresh), with no new
    # user-facing configuration required. AUTOMBIST_ENGINE_CACHE below
    # overrides the location entirely (a CI job could point this at a
    # restored actions/cache directory); set to "0"/"off" to disable caching
    # outright and always rebuild, exactly like before this existed.
    return Path(tempfile.gettempdir()) / "autombist-engine-cache"


def _engine_cache_root() -> Path:
    override = os.environ.get(_ENGINE_CACHE_ENV, "").strip()
    if override and override.lower() not in _ENGINE_CACHE_DISABLE_VALUES:
        return Path(override)
    return _default_engine_cache_root()


@functools.lru_cache(maxsize=1)
def _verilator_version() -> str:
    """Memoized: the installed verilator binary cannot change mid-process, and
    this is called once per compile_engine invocation when caching is on."""
    completed = subprocess.run(
        ["verilator", "--version"], capture_output=True, text=True, check=False
    )
    text = (completed.stdout or completed.stderr or "").strip()
    return text.splitlines()[0] if text else "unknown"


def _source_digest(sources: list[Path]) -> str:
    """Hashes the RESOLVED bytes of every source file, not e.g. a registry
    object upstream of rendering -- covers a rendered fault_ram.sv (whose
    content is a pure function of the registry + num_ports, but this way
    also covers a future template change with no separate cache-invalidation
    path to keep in sync) exactly the same as a static engine/*.sv file, and
    is the one thing that provably determines the compiled binary's
    behavior. Order-sensitive (sources are always passed in the same fixed
    order by each _resolve_*_engine_sources call site), so this doubles as a
    cheap sanity check against source-list reordering ever meaning something
    it didn't before."""
    h = hashlib.sha256()
    for src in sources:
        h.update(src.name.encode("utf-8"))
        h.update(b"\0")
        h.update(src.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _engine_build_cache_key(
    mem: MemoryParams, sources: list[Path], top_module: str, sim: str
) -> str:
    """Content-addressed: source bytes + top module + the only mem.* fields
    that actually reach a verilator -G flag (addr_width/data_width/
    words_per_row -- NOT num_ports, num_wmasks, or init_val, none of which
    compile_engine's command line ever references) + sim + tool version."""
    parts = [
        sim,
        _verilator_version(),
        top_module,
        str(mem.addr_width),
        str(mem.data_width),
        str(mem.words_per_row),
        _source_digest(sources),
    ]
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:24]


def _materialize_exe(cached_exe: Path, dest: Path) -> None:
    """Puts a copy of the cached, already-built exe at `dest` (creating
    parent dirs as needed) -- hardlinked when possible (same filesystem, no
    data copy, and safe: the cache entry is never mutated after creation, so
    an extra directory entry pointing at the same inode cannot corrupt it),
    falling back to a real copy across filesystems (e.g. a user-configured
    AUTOMBIST_ENGINE_CACHE on a different mount than the OS temp dir)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(cached_exe, dest)
    except OSError:
        shutil.copy2(cached_exe, dest)


def _populate_engine_cache(cmd: list[str], cache_entry_dir: Path, exe_name: str) -> Path:
    """Runs the real verilator build into a fresh scratch dir, then atomically
    renames it into place as `cache_entry_dir` -- so a reader can only ever
    see a fully-built entry, never a partial one from a build that crashed or
    was still in progress. Returns the cached exe's path.

    Not lock-protected: no test/CLI path in this codebase runs concurrent
    compiles against the SAME cache key today (confirmed: no pytest-xdist,
    no threading/multiprocessing anywhere in the campaign-driving code). If
    two builders ever do race, os.replace's destination-must-be-empty
    semantics make the loser's rename raise, which is caught below and
    treated as "someone else already populated this key" -- their own
    (functionally equivalent, same cache key) build is simply discarded
    rather than corrupting the winner's entry.
    """
    cache_root = cache_entry_dir.parent
    cache_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="build-", dir=str(cache_root)))
    try:
        log_path = scratch / "verilator_build.log"
        start = time.time()
        completed = _exec(cmd, cwd=scratch, log_path=log_path)
        build_seconds = time.time() - start
        if completed.returncode != 0:
            raise CampaignError(f"verilator build failed (exit {completed.returncode}). See {log_path}.")
        built_exe = scratch / "obj_dir" / exe_name
        if not built_exe.exists():
            raise CampaignError(f"verilator did not produce the expected binary: {built_exe}")
        (scratch / "build_seconds.txt").write_text(f"{build_seconds}\n", encoding="utf-8")
        try:
            os.replace(str(scratch), str(cache_entry_dir))
        except OSError:
            # Lost a race, or cache_entry_dir is a non-empty leftover from a
            # prior run under the same key -- either way, a build already
            # sitting there under this exact content-addressed key is
            # functionally identical to the one we just made; keep it.
            if not (cache_entry_dir / "obj_dir" / exe_name).exists():
                raise
            return cache_entry_dir / "obj_dir" / exe_name
        return cache_entry_dir / "obj_dir" / exe_name
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def compile_engine(
    mem: MemoryParams,
    *,
    sources: list[Path],
    top_module: str,
    workdir: Path,
    sim: str = "verilator",
    cache_dir: Path | None = None,
) -> BuildArtifact:
    _require_verilator(sim)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    exe_name = f"{top_module}_sim"
    # Only march_engine.sv/march_engine_mp.sv (the algo front) expose a
    # WORDS_PER_ROW top parameter -- the FSM front's generated harness does
    # not (run_fsm_campaign rejects a non-default words_per_row before ever
    # reaching here; see its own guard). Omitting this flag entirely at the
    # default (1) means an FSM campaign that never touches words_per_row is
    # completely unaffected by HSD's existence, byte-identical to before.
    words_per_row_flags: list[str] = []
    if mem.words_per_row != 1:
        _validate_words_per_row(mem)
        words_per_row_flags = [f"-GWORDS_PER_ROW={mem.words_per_row}"]
    cmd = [
        "verilator", "--binary", "--timing",
        "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND",
        # PINMISSING: FSM harnesses (P5) deliberately connect only the required
        # port contract; optional controller outputs (e.g. bist_busy) are left
        # unconnected by design, not by omission.
        "-Wno-PINMISSING",
        f"-GAW={mem.addr_width}", f"-GDW={mem.data_width}",
        *words_per_row_flags,
        "--top-module", top_module,
        *[str(s) for s in sources],
        "-o", exe_name,
    ]

    if _engine_cache_enabled():
        cache_root = Path(cache_dir) if cache_dir is not None else _engine_cache_root()
        key = _engine_build_cache_key(mem, sources, top_module, sim)
        cache_entry_dir = cache_root / key
        cached_exe = cache_entry_dir / "obj_dir" / exe_name
        start = time.time()
        if not cached_exe.exists():
            cached_exe = _populate_engine_cache(cmd, cache_entry_dir, exe_name)
        exe = workdir / "obj_dir" / exe_name
        _materialize_exe(cached_exe, exe)
        build_seconds = time.time() - start
        return BuildArtifact(exe=exe, workdir=workdir, top_module=top_module, build_seconds=build_seconds)

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


def _common_plusargs(mem: MemoryParams, background: DataBackground | None = None) -> list[str]:
    args = [f"+INIT={mem.init_val}"]
    if background is not None and background.mask != 0:
        args.append(f"+BACKGROUND={background.mask:x}")
    return args


_FAULT_CONCURRENCY_ENV = "AUTOMBIST_FAULT_CONCURRENCY"
# Deliberately modest, not os.cpu_count(): a per-fault run_one() call spawns
# an already-COMPILED verilator binary (the heavy, memory-hungry step is the
# BUILD -- compile_engine's own verilator invocation, which stays strictly
# single-threaded per artifact regardless of this setting, protected by its
# own build cache). Nothing in this repo's history documents a specific prior
# concurrency-related OOM incident to size this against -- start conservative
# and let AUTOMBIST_FAULT_CONCURRENCY raise it on a box known to tolerate
# more, rather than guessing a number this code can't justify. "1" recovers
# the original fully-sequential behavior exactly.
_DEFAULT_FAULT_CONCURRENCY = 4


def _fault_concurrency() -> int:
    raw = os.environ.get(_FAULT_CONCURRENCY_ENV, "").strip()
    if not raw:
        return _DEFAULT_FAULT_CONCURRENCY
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_FAULT_CONCURRENCY


def _run_faults_concurrently(
    faults: list[FaultRecord],
    run_one_fault: Callable[[int, FaultRecord], FaultResult],
    progress_callback: Callable[[int, int], None] | None,
    max_workers: int,
) -> list[FaultResult]:
    """Runs `run_one_fault(i, record)` for every fault and returns results in
    ORIGINAL fault-list order, regardless of completion order -- callers
    (report rendering, coverage matrices, `result.faults[i]`) all assume
    index i corresponds to the i-th entry of the fault list they passed in.

    `run_one_fault` must be safe to call concurrently: true today for every
    caller of this helper, since each call is a read-only run_one() subprocess
    invocation against an already-built, never-mutated artifact.exe/alg_file/
    fault_file (see algo_engine.py's own per-fault loops) -- no shared mutable
    state between faults beyond the artifact and the two files, both written
    once before any fault runs and only ever read afterward.

    max_workers<=1 (or a single-fault campaign) takes the plain sequential
    path -- byte-identical to before this existed, and the only path exercised
    when AUTOMBIST_FAULT_CONCURRENCY=1. Above that, ThreadPoolExecutor is
    enough (not multiprocessing): run_one's subprocess.run call blocks on I/O
    and releases the GIL while waiting, so this is genuinely concurrent
    despite the GIL, with none of multiprocessing's pickling/IPC overhead.

    Progress reporting uses a monotonically increasing completed-COUNT (this
    function's own local counter), not the fault's index i -- under
    concurrent, out-of-order completion, reporting index-based "progress"
    could visibly regress (fault 9 finishing before fault 3 would flash "9"
    then "3"). The counter only ever increases, exactly like the original
    sequential loop's i+1 did.

    A raised exception from any one fault propagates via future.result() (the
    same CampaignError a sequential loop would raise), but -- unlike the old
    loop, which stopped launching entirely at the first failure -- futures
    already submitted before the failing one is observed keep running to
    completion before this function's ThreadPoolExecutor context manager
    exits; their results are simply discarded once the exception propagates.
    This trades a modest amount of wasted work on the (expected-rare) error
    path for not needing an active-cancellation mechanism verilator's
    subprocess.run doesn't cleanly support anyway.
    """
    total = len(faults)
    if max_workers <= 1 or total <= 1:
        results: list[FaultResult] = []
        for i, record in enumerate(faults):
            results.append(run_one_fault(i, record))
            if progress_callback is not None:
                progress_callback(i + 1, total)
        return results

    results_by_index: list[FaultResult | None] = [None] * total
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(run_one_fault, i, record): i for i, record in enumerate(faults)
        }
        # as_completed() itself yields on this (the calling) thread, one at a
        # time -- results_by_index/completed/progress_callback are never
        # touched from more than one thread, so none of this needs a lock.
        for future in concurrent.futures.as_completed(future_to_index):
            i = future_to_index[future]
            results_by_index[i] = future.result()
            completed += 1
            if progress_callback is not None:
                progress_callback(completed, total)
    return results_by_index  # type: ignore[return-value]  # every slot filled: one future per index, all awaited above


def _resolve_engine_sources(mem: MemoryParams, engine_dir: Path, workdir: Path,
                             fault_ram_sv: Path | None) -> tuple[list[Path], str]:
    """Dispatch on mem.num_ports for the algo front.

    num_ports==1 (every existing caller's default): the EXISTING, UNTOUCHED
    march_engine.sv, paired with the hand-written single-port fault_ram.sv
    (or a caller-supplied override, e.g. a DSL-rendered registry render --
    always num_ports=1 shaped) -- byte-identical dispatch to before this
    phase, zero risk to any single-port campaign.

    num_ports==2: march_engine_mp.sv (new; never touches march_engine.sv),
    paired with a fault_ram.sv rendered with num_ports=2. A caller-supplied
    fault_ram_sv is trusted as already being num_ports=2 shaped (e.g. a
    researcher's custom fault-type registry rendered accordingly); otherwise
    the default registry is rendered fresh into workdir.
    """
    if mem.num_ports == 1:
        resolved_fault_ram = fault_ram_sv or (engine_dir / "fault_ram.sv")
        march_sv = engine_dir / "march_engine.sv"
        return [resolved_fault_ram, march_sv], "march_engine"
    if mem.num_ports == 2:
        if fault_ram_sv is not None:
            resolved_fault_ram = fault_ram_sv
        else:
            from .fault_primitives import default_registry
            from .fault_ram_gen import render_and_write

            resolved_fault_ram = render_and_write(
                default_registry(), workdir / "fault_ram.sv", num_ports=2
            )
        march_mp_sv = engine_dir / "march_engine_mp.sv"
        return [resolved_fault_ram, march_mp_sv], "march_engine_mp"
    raise CampaignError(f"unsupported mem.num_ports={mem.num_ports!r}: only 1 or 2 are supported")


def _run_campaign_against_artifact(
    artifact: BuildArtifact,
    mem: MemoryParams,
    alg: AlgSpec,
    faults: list[FaultRecord],
    *,
    workdir: Path,
    sim: str,
    verbose: bool = False,
    background: DataBackground | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    max_workers: int | None = None,
) -> CampaignResult:
    """Golden pass + one run per fault against an already-compiled artifact.
    Factored out of run_algo_campaign so run_background_campaign can reuse a
    single compiled binary across multiple backgrounds instead of
    recompiling once per background.

    ``max_workers`` (None = AUTOMBIST_FAULT_CONCURRENCY / the default) bounds
    how many faults run concurrently -- see _run_faults_concurrently.
    """
    alg_file = alg.write_numeric(workdir / f"{alg.name}.algc")
    fault_file = write_fault_list(faults, workdir / "faults.txt") if faults else None
    plusargs = _common_plusargs(mem, background)

    start = time.time()

    golden_out = run_one(artifact, alg_file=alg_file, extra_plusargs=plusargs)
    golden_detected, *_ = parse_result_line(golden_out)
    if golden_detected:
        bg_note = f" (background={background.name})" if background is not None else ""
        raise CampaignError(
            f"golden run for algorithm '{alg.name}'{bg_note} unexpectedly reported DETECTED "
            f"(no faults were injected). The algorithm spec or engine is broken.\n{golden_out}"
        )

    def _run_one_fault(i: int, record: FaultRecord) -> FaultResult:
        out = run_one(
            artifact, alg_file=alg_file, fault_file=fault_file, index=i,
            verbose=verbose, extra_plusargs=plusargs,
        )
        detected, elem, op, addr, xor_bits = parse_result_line(out)
        activations = None
        if verbose:
            hits = parse_fault_hits(out)
            activations = hits[2] if hits else None
        return FaultResult(
            index=i, record=record, detected=detected,
            elem=elem, op=op, addr=addr, xor=xor_bits, activations=activations,
        )

    results = _run_faults_concurrently(
        faults, _run_one_fault, progress_callback,
        max_workers if max_workers is not None else _fault_concurrency(),
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


def run_algo_campaign(
    mem: MemoryParams,
    alg: AlgSpec,
    faults: list[FaultRecord],
    *,
    sim: str = "verilator",
    workdir: Path | None = None,
    verbose: bool = False,
    fault_ram_sv: Path | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    cache_dir: Path | None = None,
    max_workers: int | None = None,
) -> CampaignResult:
    """Compile march_engine once, run a golden pass, then one run per fault.

    ``progress_callback`` (opt-in) is invoked as ``callback(completed, total)``
    after each per-fault run -- e.g. to drive a CLI progress bar. Default None
    keeps every existing caller byte-identical (no behavior change, just skips
    the call).

    Dispatches on mem.num_ports (see _resolve_engine_sources): num_ports==1
    (default) uses the existing march_engine.sv unmodified; num_ports==2
    uses the new march_engine_mp.sv against a num_ports=2 fault_ram.sv.

    ``cache_dir`` (opt-in) overrides where compile_engine looks for/populates
    its content-addressed build cache -- None uses the shared default
    location (see _default_engine_cache_root), which is what every real
    caller wants; tests pass an isolated tmp_path here to observe cache-hit
    behavior deterministically without touching the shared cache.

    ``max_workers`` (opt-in) overrides how many faults run concurrently --
    None uses AUTOMBIST_FAULT_CONCURRENCY / the default (see
    _run_faults_concurrently); pass 1 to force the original fully-sequential
    behavior.
    """
    _validate_fault_addresses(mem, faults)
    own_tmp: tempfile.TemporaryDirectory[str] | None = None
    if workdir is None:
        own_tmp = tempfile.TemporaryDirectory(prefix="autombist-algo-")
        workdir = Path(own_tmp.name)
    else:
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)

    try:
        engine_dir = find_engine_dir()
        sources, top_module = _resolve_engine_sources(mem, engine_dir, workdir, fault_ram_sv)

        artifact = compile_engine(
            mem, sources=sources, top_module=top_module,
            workdir=workdir, sim=sim, cache_dir=cache_dir,
        )

        return _run_campaign_against_artifact(
            artifact, mem, alg, faults, workdir=workdir, sim=sim, verbose=verbose,
            progress_callback=progress_callback, max_workers=max_workers,
        )
    finally:
        if own_tmp is not None:
            own_tmp.cleanup()


def run_background_campaign(
    mem: MemoryParams,
    alg: AlgSpec,
    faults: list[FaultRecord],
    *,
    backgrounds: list[DataBackground] | None = None,
    sim: str = "verilator",
    workdir: Path | None = None,
    verbose: bool = False,
    fault_ram_sv: Path | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    cache_dir: Path | None = None,
    max_workers: int | None = None,
) -> dict[str, CampaignResult]:
    """Runs the same algorithm/fault-list once per data background (default:
    standard_backgrounds(mem.data_width)), reusing ONE compiled artifact,
    keyed by DataBackground.name. Opt-in and additive -- run_algo_campaign
    itself is untouched; a caller that never calls this function sees no
    behavior change at all.

    Each per-background run gets its own golden-soundness check (see
    _run_campaign_against_artifact): a fault-free run under a non-zero
    background must still report ESCAPED, since bg_value() applies the same
    mask to both the write side and the read-assertion side.

    ``cache_dir``/``max_workers``: see run_algo_campaign's docstring.
    """
    _validate_fault_addresses(mem, faults)
    backgrounds = backgrounds if backgrounds is not None else standard_backgrounds(mem.data_width)
    if not backgrounds:
        raise CampaignError("run_background_campaign: no backgrounds to run")

    own_tmp: tempfile.TemporaryDirectory[str] | None = None
    if workdir is None:
        own_tmp = tempfile.TemporaryDirectory(prefix="autombist-algo-bg-")
        workdir = Path(own_tmp.name)
    else:
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)

    try:
        engine_dir = find_engine_dir()
        sources, top_module = _resolve_engine_sources(mem, engine_dir, workdir, fault_ram_sv)

        artifact = compile_engine(
            mem, sources=sources, top_module=top_module,
            workdir=workdir, sim=sim, cache_dir=cache_dir,
        )

        results: dict[str, CampaignResult] = {}
        for background in backgrounds:
            results[background.name] = _run_campaign_against_artifact(
                artifact, mem, alg, faults, workdir=workdir, sim=sim, verbose=verbose,
                background=background, progress_callback=progress_callback,
                max_workers=max_workers,
            )
        return results
    finally:
        if own_tmp is not None:
            own_tmp.cleanup()


def merge_background_results(per_background: dict[str, CampaignResult]) -> CampaignResult:
    """Collapses N run_background_campaign results (same mem/alg/fault-list,
    keyed by DataBackground.name) into one CampaignResult: a fault is
    'detected' if ANY background detected it; elem/op/addr/xor/activations
    are taken from the first background (in dict insertion order) that
    detected it, None if escaped in every background. build_seconds and
    run_seconds are summed across all per-background runs.
    ``backgrounds_run`` records which background names were merged."""
    if not per_background:
        raise CampaignError("merge_background_results: no per-background results to merge")

    names = list(per_background.keys())
    per_faults = [per_background[n].faults for n in names]
    n_faults = len(per_faults[0])
    for lst in per_faults:
        if len(lst) != n_faults:
            raise CampaignError(
                "merge_background_results: mismatched fault counts across backgrounds "
                f"({[len(lst) for lst in per_faults]})"
            )

    merged_faults: list[FaultResult] = []
    for i in range(n_faults):
        detected = False
        first_hit: FaultResult | None = None
        base = per_faults[0][i]
        for lst in per_faults:
            r = lst[i]
            if r.detected:
                detected = True
                if first_hit is None:
                    first_hit = r
        source = first_hit if first_hit is not None else base
        merged_faults.append(
            FaultResult(
                index=base.index, record=base.record, detected=detected,
                elem=source.elem if detected else None,
                op=source.op if detected else None,
                addr=source.addr if detected else None,
                xor=source.xor if detected else None,
                activations=source.activations if detected else None,
            )
        )

    first_result = per_background[names[0]]
    detected_count = sum(1 for r in merged_faults if r.detected)
    total = len(merged_faults)
    coverage = 100.0 if total == 0 else (detected_count / total) * 100.0

    return CampaignResult(
        algo_name=first_result.algo_name, mem=first_result.mem,
        golden_clean=all(r.golden_clean for r in per_background.values()),
        faults=merged_faults, detected=detected_count, total=total,
        coverage_percent=coverage,
        build_seconds=sum(r.build_seconds for r in per_background.values()),
        run_seconds=sum(r.run_seconds for r in per_background.values()),
        sim=first_result.sim, backgrounds_run=names,
    )


def _resolve_fsm_engine_sources(
    mem: MemoryParams, engine_dir: Path, workdir: Path, fault_ram_sv: Path | None,
) -> tuple[Path, Path]:
    """Dispatch on mem.num_ports for the FSM front (mirrors
    _resolve_engine_sources's dispatch pattern for the algo front).

    num_ports==1 (every existing caller's default): the EXISTING, UNTOUCHED
    openram_shim.sv, paired with the hand-written single-port fault_ram.sv
    (or a caller-supplied override) -- byte-identical dispatch to before this
    phase, zero risk to any single-port FSM campaign.

    num_ports==2: openram_shim_mp.sv (new; never touches openram_shim.sv),
    paired with a fault_ram.sv rendered with num_ports=2. A caller-supplied
    fault_ram_sv is trusted as already being num_ports=2 shaped; otherwise the
    default registry is rendered fresh into workdir. Returns
    (fault_ram_sv, shim_sv).
    """
    if mem.num_ports == 1:
        resolved_fault_ram = fault_ram_sv or (engine_dir / "fault_ram.sv")
        shim_sv = engine_dir / "openram_shim.sv"
        return resolved_fault_ram, shim_sv
    if mem.num_ports == 2:
        if fault_ram_sv is not None:
            resolved_fault_ram = fault_ram_sv
        else:
            from .fault_primitives import default_registry
            from .fault_ram_gen import render_and_write

            resolved_fault_ram = render_and_write(
                default_registry(), workdir / "fault_ram.sv", num_ports=2
            )
        shim_sv = engine_dir / "openram_shim_mp.sv"
        return resolved_fault_ram, shim_sv
    raise CampaignError(f"unsupported mem.num_ports={mem.num_ports!r}: only 1 or 2 are supported")


def run_fsm_campaign(
    mem: MemoryParams,
    fsm_sources: list[Path],
    fsm_module_name: str,
    faults: list[FaultRecord],
    *,
    sim: str = "verilator",
    workdir: Path | None = None,
    fault_ram_sv: Path | None = None,
    expected_spec: AlgSpec | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    cache_dir: Path | None = None,
    max_workers: int | None = None,
) -> CampaignResult:
    """Compile FSM + openram_shim + fault_ram (via a generated harness) once,
    golden-gate, then one run per fault. Detection is bist_fail only -- no
    elem/op attribution, since a black-box controller has no step counter.

    Dispatches on mem.num_ports (see _resolve_fsm_engine_sources): num_ports==1
    (default) uses the existing openram_shim.sv + single-port harness template
    unmodified; num_ports==2 uses the new openram_shim_mp.sv (wrapping ONE
    fault_ram core rendered with num_ports=2) + the new 2-port harness
    template, and validates the FSM against REQUIRED_PORTS_MP.

    ``expected_spec`` is opt-in: when given, the harness is rendered with an
    ACCESS observer and the GOLDEN pass is traced with ``+SEQ_TRACE`` so the
    controller's actual memory-operation sequence can be checked against the
    march sequence ``expected_spec`` requires (see :mod:`autombist.seq_check`),
    independent of fault detection. The result is attached to
    ``CampaignResult.sequence``. When None (the default) NOTHING about this path
    changes: the harness renders byte-identically and no ``+SEQ_TRACE`` is
    passed, so every existing FSM campaign is unaffected.

    ``progress_callback`` (opt-in) is invoked as ``callback(completed, total)``
    after each per-fault run -- e.g. to drive a CLI progress bar. Default None
    keeps every existing caller byte-identical.

    ``cache_dir``/``max_workers``: see run_algo_campaign's docstring.
    """
    from .fsm_harness import HARNESS_TOP, check_ports, parse_ports, render_harness, render_harness_mp

    _validate_fault_addresses(mem, faults)
    own_tmp: tempfile.TemporaryDirectory[str] | None = None
    if workdir is None:
        own_tmp = tempfile.TemporaryDirectory(prefix="autombist-fsm-")
        workdir = Path(own_tmp.name)
    else:
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)

    check_sequence = expected_spec is not None
    if check_sequence:
        assert expected_spec is not None  # for type checkers
        if any(op >= WAIT_BASE for e in expected_spec.elements for op in e.ops):
            raise CampaignError(
                "expected_spec contains a wait op -- FSM sequence comparison cannot observe "
                "elapsed idle cycles on a real controller's bus trace; wait ops are "
                "march_engine-front only in this phase"
            )
    if mem.words_per_row != 1:
        raise CampaignError(
            "mem.words_per_row != 1 (HSD/Half-Select Disturb) is not yet supported on the "
            "FSM front -- only march_engine.sv/march_engine_mp.sv (the algo front) expose "
            "a WORDS_PER_ROW top parameter to Verilator's -G override in this phase; the "
            "generated FSM harness does not"
        )
    try:
        engine_dir = find_engine_dir()
        resolved_fault_ram, shim_sv = _resolve_fsm_engine_sources(mem, engine_dir, workdir, fault_ram_sv)

        # bist_busy is optional in the FSM port contract; only wire it up if
        # this FSM actually declares it, or Verilator errors on a named
        # connection to a port the target module doesn't have.
        top_text = fsm_sources[0].read_text(encoding="utf-8")
        has_bist_busy = parse_ports(top_text, module_name=fsm_module_name).ports.get("bist_busy") == "output"

        harness_path = workdir / f"{HARNESS_TOP}.sv"
        if mem.num_ports == 2:
            check_ports(top_text, module_name=fsm_module_name, num_ports=2)
            harness_text = render_harness_mp(
                addr_width=mem.addr_width, data_width=mem.data_width,
                fsm_module_name=fsm_module_name, has_bist_busy=has_bist_busy,
                emit_seq_trace=check_sequence,
            )
        else:
            harness_text = render_harness(
                addr_width=mem.addr_width, data_width=mem.data_width,
                fsm_module_name=fsm_module_name, has_bist_busy=has_bist_busy,
                emit_seq_trace=check_sequence,
            )
        harness_path.write_text(harness_text, encoding="utf-8")

        artifact = compile_engine(
            mem,
            sources=[resolved_fault_ram, shim_sv, *fsm_sources, harness_path],
            top_module=HARNESS_TOP,
            workdir=workdir,
            sim=sim,
            cache_dir=cache_dir,
        )

        fault_file = write_fault_list(faults, workdir / "faults.txt") if faults else None
        plusargs = _common_plusargs(mem)

        start = time.time()

        # Only the golden run carries +SEQ_TRACE (the observer emits nothing
        # without it), so per-fault runs stay identical whether or not a
        # sequence check was requested.
        golden_plusargs = plusargs + ["+SEQ_TRACE"] if check_sequence else plusargs
        golden_out = run_one(artifact, extra_plusargs=golden_plusargs)
        golden_detected, *_ = parse_result_line(golden_out)
        if golden_detected:
            raise CampaignError(
                f"golden run for FSM '{fsm_module_name}' unexpectedly reported DETECTED "
                f"(no faults were injected). The FSM or harness wiring is broken.\n{golden_out}"
            )

        sequence: SequenceResult | None = None
        if check_sequence:
            assert expected_spec is not None  # for type checkers; guarded by check_sequence
            observed = parse_observed_trace(golden_out)
            blocks = expand_expected_blocks(expected_spec, mem.depth)
            sequence = compare_trace(
                blocks, observed, data_width=mem.data_width, num_ports=mem.num_ports,
            )

        def _run_one_fault(i: int, record: FaultRecord) -> FaultResult:
            out = run_one(artifact, fault_file=fault_file, index=i, extra_plusargs=plusargs)
            detected, elem, op, addr, xor_bits = parse_result_line(out)
            return FaultResult(index=i, record=record, detected=detected, elem=elem, op=op, addr=addr, xor=xor_bits)

        results = _run_faults_concurrently(
            faults, _run_one_fault, progress_callback,
            max_workers if max_workers is not None else _fault_concurrency(),
        )

        run_seconds = time.time() - start
        detected_count = sum(1 for r in results if r.detected)
        total = len(results)
        coverage = 100.0 if total == 0 else (detected_count / total) * 100.0

        return CampaignResult(
            algo_name=f"FSM:{fsm_module_name}", mem=mem, golden_clean=True, faults=results,
            detected=detected_count, total=total, coverage_percent=coverage,
            build_seconds=artifact.build_seconds, run_seconds=run_seconds, sim=sim,
            sequence=sequence,
        )
    finally:
        if own_tmp is not None:
            own_tmp.cleanup()
