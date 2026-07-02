"""March algorithm specs (`.alg`) for the fault-campaign engine.

A ``.alg`` file is human-readable: one march *element* per line, ``DIR OP [OP ...]``
with ``DIR`` in {up, down, either} and ``OP`` in {r0, r1, w0, w1}; ``#`` comments and
blank lines are ignored. Example (March C-)::

    either w0
    up   r0 w1
    up   r1 w0
    down r0 w1
    down r1 w0
    either r0

autombist parses this to an :class:`AlgSpec` and serializes the *numeric* form the
SystemVerilog ``march_engine`` reads via ``+ALG_FILE`` (``DIR NOPS OP0..OP7``).
"""
from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from pathlib import Path

DIR_MAP = {"up": 0, "down": 1, "either": 2}
DIR_NAME = {0: "up", 1: "down", 2: "either"}
OP_MAP = {"r0": 0, "r1": 1, "w0": 2, "w1": 3}
OP_NAME = {0: "r0", 1: "r1", 2: "w0", 3: "w1"}

MAX_ELEMENTS = 16   # SystemVerilog prog[16]
MAX_OPS = 8         # SystemVerilog ops[8]


class AlgSpecError(ValueError):
    """Raised when a `.alg` spec is malformed or exceeds engine limits."""


@dataclass(slots=True)
class Element:
    direction: int          # 0=up, 1=down, 2=either
    ops: list[int]          # each 0=r0, 1=r1, 2=w0, 3=w1

    def human(self) -> str:
        return " ".join([DIR_NAME[self.direction], *(OP_NAME[o] for o in self.ops)])

    def numeric_line(self) -> str:
        padded = (self.ops + [0] * MAX_OPS)[:MAX_OPS]
        return " ".join(str(x) for x in (self.direction, len(self.ops), *padded))


@dataclass(slots=True)
class AlgSpec:
    name: str
    elements: list[Element]

    @property
    def length_n(self) -> int:
        """Test length in units of N (operations per address)."""
        return sum(len(e.ops) for e in self.elements)

    def to_numeric(self) -> str:
        header = f"# {self.name}  ({self.length_n}n)  DIR NOPS OP0..OP7\n"
        return header + "".join(e.numeric_line() + "\n" for e in self.elements)

    def write_numeric(self, path: Path) -> Path:
        path.write_text(self.to_numeric(), encoding="ascii")
        return path


def parse_alg(text: str, name: str) -> AlgSpec:
    """Parse the human `.alg` form into an :class:`AlgSpec`."""
    elements: list[Element] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        direction_tok = tokens[0].lower()
        if direction_tok not in DIR_MAP:
            raise AlgSpecError(
                f"{name}:{lineno}: bad direction '{tokens[0]}' (use up|down|either)"
            )
        op_toks = tokens[1:]
        if not op_toks:
            raise AlgSpecError(f"{name}:{lineno}: element has no operations")
        if len(op_toks) > MAX_OPS:
            raise AlgSpecError(
                f"{name}:{lineno}: {len(op_toks)} ops exceeds engine max {MAX_OPS}"
            )
        ops: list[int] = []
        for tok in op_toks:
            key = tok.lower()
            if key not in OP_MAP:
                raise AlgSpecError(
                    f"{name}:{lineno}: bad op '{tok}' (use r0|r1|w0|w1)"
                )
            ops.append(OP_MAP[key])
        elements.append(Element(direction=DIR_MAP[direction_tok], ops=ops))

    if not elements:
        raise AlgSpecError(f"{name}: no march elements found")
    if len(elements) > MAX_ELEMENTS:
        raise AlgSpecError(
            f"{name}: {len(elements)} elements exceeds engine max {MAX_ELEMENTS}"
        )
    return AlgSpec(name=name, elements=elements)


def load_alg_file(path: Path, name: str | None = None) -> AlgSpec:
    path = Path(path)
    if not path.exists():
        raise AlgSpecError(f"algorithm spec not found: {path}")
    return parse_alg(path.read_text(encoding="utf-8"), name or path.stem)


# --------------------------------------------------------------------------- #
# Asset resolution (engine SV + built-in .alg specs), dev + installed modes.
# --------------------------------------------------------------------------- #
def _find_pkg_subdir(name: str, marker: str) -> Path:
    """Locate a package data dir (engine/ or algos/) in installed or dev layout."""
    try:
        pkg = importlib.resources.files("autombist").joinpath(name)
        if pkg.joinpath(marker).is_file():
            return Path(str(pkg))
    except (TypeError, FileNotFoundError, AttributeError, ModuleNotFoundError):
        pass
    local = Path(__file__).resolve().parent / name
    if (local / marker).is_file():
        return local
    raise AlgSpecError(
        f"autombist {name}/ directory not found (looked for {marker}). "
        "Reinstall autombist or verify your checkout."
    )


def find_engine_dir() -> Path:
    """Directory holding fault_ram.sv / march_engine.sv / openram_shim.sv."""
    return _find_pkg_subdir("engine", "march_engine.sv")


def find_algos_dir() -> Path:
    """Directory holding the built-in .alg specs."""
    return _find_pkg_subdir("algos", "march_c.alg")


def builtin_algos() -> dict[str, Path]:
    """Map built-in algorithm name -> .alg path."""
    algos_dir = find_algos_dir()
    return {p.stem: p for p in sorted(algos_dir.glob("*.alg"))}


def resolve_algo(name_or_path: str) -> AlgSpec:
    """Resolve a built-in name (e.g. 'march_c') or a path to a .alg file."""
    builtins = builtin_algos()
    key = name_or_path.replace("-", "_")
    if key in builtins:
        return load_alg_file(builtins[key], name=key)
    path = Path(name_or_path)
    if path.exists():
        return load_alg_file(path)
    raise AlgSpecError(
        f"unknown algorithm '{name_or_path}'. Built-ins: {', '.join(sorted(builtins))}; "
        "or pass a path to a .alg file."
    )
