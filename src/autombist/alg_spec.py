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

An op token may carry an OPTIONAL port suffix, ``.PORT`` (e.g. ``r0.1``, ``w1.0``),
meaning "same base op, issued on port PORT" -- for multi-port memories. The suffix
is a dot, chosen because it cannot collide with anything else in this grammar (``#``
starts a comment, whitespace tokenizes, and base op tokens are exactly ``r0|r1|w0|
w1``). Omitting the suffix (every existing built-in `.alg` file) means port 0.

An op token may also be ``t<N>`` (e.g. ``t5``), meaning "idle for N clock cycles,
no memory access" -- for Data Retention Fault (DRF) modeling, which is sensitized
by elapsed idle time rather than any read/write sequence. A wait token does not
accept a ``.PORT`` suffix (it touches no bus) and does not count toward
:attr:`AlgSpec.length_n` (see that property's docstring).

Like every other op, a wait op is executed once per address the enclosing
element visits -- there is no "run once regardless of address" wait shape.
``up t5`` on a depth-8 memory idles 5 cycles at EACH of the 8 addresses (40
cycles total), not once. To get a single bounded idle window (e.g. between
two march elements for a DRF differential test), account for this
multiplication explicitly -- either divide the desired total by the memory's
depth, or use a small/known depth.
"""
from __future__ import annotations

import importlib.resources
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

DIR_MAP = {"up": 0, "down": 1, "either": 2}
DIR_NAME = {0: "up", 1: "down", 2: "either"}
OP_MAP = {"r0": 0, "r1": 1, "w0": 2, "w1": 3}
OP_NAME = {0: "r0", 1: "r1", 2: "w0", 3: "w1"}

MAX_ELEMENTS = 16   # SystemVerilog prog[16]
MAX_OPS = 8         # SystemVerilog ops[8]

# Op codes >= WAIT_BASE encode an N-cycle wait/idle op: op_code = WAIT_BASE + N
# (N >= 1). march_engine.sv's op-dispatch `case` already silently no-ops any
# code outside {0,1,2,3} today, so this reuses that slot with zero numeric-
# format growth -- to_numeric()/numeric_line() already emit str(x) for
# arbitrary ints, no `$sscanf` format-string change needed on the RTL side.
WAIT_BASE = 4
MAX_WAIT_CYCLES = 65535   # blocks a typo like "t500000" from stalling simulation


class AlgSpecError(ValueError):
    """Raised when a `.alg` spec is malformed or exceeds engine limits."""


@dataclass(slots=True)
class Element:
    direction: int          # 0=up, 1=down, 2=either
    ops: list[int]          # each 0=r0, 1=r1, 2=w0, 3=w1
    ports: list[int] = field(default_factory=list)   # parallel to ops; 0 = port 0 (default)

    def __post_init__(self) -> None:
        if not self.ports:
            self.ports = [0] * len(self.ops)

    def human(self) -> str:
        toks = []
        for op, port in zip(self.ops, self.ports):
            if op >= WAIT_BASE:
                toks.append(f"t{op - WAIT_BASE}")
                continue
            tok = OP_NAME[op]
            if port:
                tok = f"{tok}.{port}"
            toks.append(tok)
        return " ".join([DIR_NAME[self.direction], *toks])

    def numeric_line(self) -> str:
        """Byte-identical to the pre-multi-port format (``DIR NOPS OP0..OP7``)
        when every op in this element is on port 0. Only when a non-zero port
        is actually present does this emit an extended format with additional
        trailing PORT0..PORT7 columns."""
        padded = (self.ops + [0] * MAX_OPS)[:MAX_OPS]
        base = " ".join(str(x) for x in (self.direction, len(self.ops), *padded))
        if not any(self.ports):
            return base
        padded_ports = (self.ports + [0] * MAX_OPS)[:MAX_OPS]
        return base + " " + " ".join(str(x) for x in padded_ports)


@dataclass(slots=True, frozen=True)
class AccessStep:
    """One memory operation a correct controller must drive, as expanded from a
    march spec. ``op`` uses the same 0=r0/1=r1/2=w0/3=w1 encoding as
    :data:`OP_MAP`."""
    elem_idx: int
    op_idx: int
    addr: int
    op: int
    port: int

    @property
    def is_write(self) -> bool:
        return self.op in (2, 3)

    @property
    def write_value(self) -> int | None:
        """0 for w0, 1 for w1, ``None`` for a read (whose expected data is not
        visible on the memory bus -- it is validated instead by the golden pass
        succeeding)."""
        if self.op == 2:
            return 0
        if self.op == 3:
            return 1
        return None

    def human(self) -> str:
        # Defensive: _expand_element skips wait ops entirely (they have no
        # bus activity to expand into a step), so this branch shouldn't be
        # reachable in practice -- kept for the same reason Element.human()
        # has one, in case something upstream ever constructs an AccessStep
        # for a wait code directly.
        if self.op >= WAIT_BASE:
            tok = f"t{self.op - WAIT_BASE}"
            return f"e{self.elem_idx}o{self.op_idx} {tok}@{self.addr}"
        tok = OP_NAME[self.op]
        if self.port:
            tok = f"{tok}.{self.port}"
        return f"e{self.elem_idx}o{self.op_idx} {tok}@{self.addr}"


@dataclass(slots=True)
class AlgSpec:
    name: str
    elements: list[Element]

    @property
    def length_n(self) -> int:
        """Test length in units of N (operations per address). Wait ops are
        excluded -- they are not memory operations and would make an "Xn"
        comparison between a wait-using and a wait-free algorithm meaningless
        (a test adding one long wait would look like "+1n" while actually
        adding thousands of simulated cycles)."""
        return sum(sum(1 for op in e.ops if op < WAIT_BASE) for e in self.elements)

    def resolved(self) -> AlgSpec:
        """Return a new spec with every element's direction collapsed to a
        concrete up/down value via :func:`resolve_directions` -- the ONE
        canonical rule for what `either` means when a consumer needs an actual
        address order. ``self`` is left untouched: `either` is preserved in a
        freshly-parsed spec so it round-trips through :meth:`to_text` (it is
        author intent, not yet a decision), and this method is the one place
        that makes the decision on a caller's behalf. :meth:`to_numeric` calls
        this before serializing, so every consumer downstream of the numeric
        form (both SystemVerilog engines) sees only 0/1, never 2."""
        dirs = resolve_directions(self.elements)
        resolved_elements = [
            Element(direction=d, ops=list(e.ops), ports=list(e.ports))
            for e, d in zip(self.elements, dirs)
        ]
        return AlgSpec(name=self.name, elements=resolved_elements)

    def to_numeric(self) -> str:
        """Byte-identical to the pre-multi-port serialization when every
        element's ports are all zero (true of every existing built-in `.alg`
        file) AND no element's direction needed resolving (true of any spec
        with no `either`). Only emits the extended ``...PORT0..PORT7``
        header/columns when a non-zero port tag is actually present somewhere
        in the spec. Serializes :meth:`resolved`, not ``self`` -- the DIR
        column a consumer reads is always a concrete 0/1, never 2 (`either`)."""
        resolved = self.resolved()
        extended = any(any(e.ports) for e in resolved.elements)
        suffix = "  PORT0..PORT7" if extended else ""
        header = f"# {self.name}  ({self.length_n}n)  DIR NOPS OP0..OP7{suffix}\n"
        return header + "".join(e.numeric_line() + "\n" for e in resolved.elements)

    def write_numeric(self, path: Path) -> Path:
        path.write_text(self.to_numeric(), encoding="ascii")
        return path

    def to_text(self) -> str:
        """Serialize back to the human `.alg` grammar (inverse of
        :func:`parse_alg`, modulo the leading ``#`` header comment, which
        parsing ignores) -- ``parse_alg(spec.to_text(), name).elements ==
        spec.elements``. One line per element via :meth:`Element.human`."""
        header = f"# {self.name}  ({self.length_n}n)\n"
        return header + "".join(e.human() + "\n" for e in self.elements)

    def write_text(self, path: Path) -> Path:
        path = Path(path)
        path.write_text(self.to_text(), encoding="utf-8")
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
        ports: list[int] = []
        for tok in op_toks:
            key = tok.lower()
            if key.startswith("t") and key[1:].isdigit():
                n = int(key[1:])
                if n < 1:
                    raise AlgSpecError(
                        f"{name}:{lineno}: bad wait count in '{tok}' (N must be >= 1)"
                    )
                if n > MAX_WAIT_CYCLES:
                    raise AlgSpecError(
                        f"{name}:{lineno}: wait count {n} exceeds engine max {MAX_WAIT_CYCLES}"
                    )
                ops.append(WAIT_BASE + n)
                ports.append(0)
                continue
            port = 0
            if "." in key:
                key, _, port_tok = key.partition(".")
                if key.startswith("t"):
                    raise AlgSpecError(
                        f"{name}:{lineno}: bad wait token '{tok}' (wait ops do not take a "
                        "'.PORT' suffix)"
                    )
                if not port_tok.isdigit() or int(port_tok) not in (0, 1):
                    raise AlgSpecError(
                        f"{name}:{lineno}: bad port suffix in '{tok}' (use r0|r1|w0|w1, "
                        "optionally suffixed '.PORT' with PORT in {0, 1}, e.g. r0.1)"
                    )
                port = int(port_tok)
            if key not in OP_MAP:
                raise AlgSpecError(
                    f"{name}:{lineno}: bad op '{tok}' (use r0|r1|w0|w1, or t<N> for an "
                    "N-cycle wait)"
                )
            ops.append(OP_MAP[key])
            ports.append(port)
        elements.append(Element(direction=DIR_MAP[direction_tok], ops=ops, ports=ports))

    if not elements:
        raise AlgSpecError(f"{name}: no march elements found")
    if len(elements) > MAX_ELEMENTS:
        raise AlgSpecError(
            f"{name}: {len(elements)} elements exceeds engine max {MAX_ELEMENTS}"
        )
    return AlgSpec(name=name, elements=elements)


def resolve_directions(elements: Sequence[Element]) -> list[int]:
    """Resolve each element's traversal direction to a concrete up/down value
    (0/1, :data:`DIR_MAP` encoding -- ``either`` (2) never appears in the result).

    ``up``/``down`` pass straight through. ``either`` means "this element's
    direction does not affect what it tests" (typically a lone init write or a
    lone verify read), so the caller is free to choose -- and the rule is
    **inherit the previous element's direction, defaulting to up when there is
    none**.

    This is the ONE canonical rule for the whole project. Every consumer that
    needs a concrete address order calls this function rather than resolving
    independently: the numeric serialization handed to the SystemVerilog
    engines (:meth:`AlgSpec.resolved`/:meth:`AlgSpec.to_numeric`), the
    classic-path RTL table renderer (:mod:`autombist.algo_rtl_gen`), the
    reference linear trace (:func:`expand_expected_trace`), and the
    synthesizer's internal replay model (:mod:`autombist.synth_engine`). Before
    this function existed, those sites disagreed -- the engine ran every
    ``either`` element ascending while the hand-written classic RTL ran a
    *trailing* ``either`` in the previous element's direction -- and that was
    not cosmetic: on ``faults.example.txt`` at addr_width=8/data_width=8,
    ``march_x`` scored 13/29 under the engine's rule and 14/29 under this one
    (its ``CFDS 130 1 131 1 4 0``, aggressor above the victim, is caught only
    by a descending final read). This rule was chosen because it is the one
    real silicon already implements: it reproduces the direction sequence of
    all three hand-written classic-path tables exactly (``march_c`` up,up,up,
    down,down,down; ``march_x`` up,up,down,down; ``mats_plus`` up,up,down --
    proven behaviourally identical by
    ``tests/integration/test_algo_table_equivalence.py``'s exhaustive
    simulation sweep), and it has a hardware rationale: continuing in the same
    direction means the address counter never has to rewind between elements.

    The one place ``either``'s freedom is still honored rather than collapsed
    to this single answer is the FSM sequence checker
    (:func:`expand_expected_blocks`), which validates an arbitrary hand-written
    controller against a spec -- a controller is free to choose either order
    for a genuine ``either`` element, so the checker accepts both, with this
    function's answer listed first.
    """
    directions: list[int] = []
    previous = 0  # default direction when a spec opens with `either`: up
    for element in elements:
        if element.direction != 2:  # not `either` -- adopt it as the running direction
            previous = element.direction
        directions.append(previous)
    return directions


def _expand_element(elem: Element, elem_idx: int, depth: int, direction: int) -> list[AccessStep]:
    """Expand one element with an explicit address direction (0=up, 1=down).
    Wait ops are skipped -- they have zero bus activity, so there is no
    memory operation for a real controller to drive or for a sequence
    comparison to expect."""
    addr_iter: range = range(depth - 1, -1, -1) if direction == 1 else range(0, depth)
    steps: list[AccessStep] = []
    for addr in addr_iter:
        for o_idx, op in enumerate(elem.ops):
            if op >= WAIT_BASE:
                continue
            port = elem.ports[o_idx] if o_idx < len(elem.ports) else 0
            steps.append(AccessStep(elem_idx=elem_idx, op_idx=o_idx, addr=addr, op=op, port=port))
    return steps


def expand_expected_trace(spec: AlgSpec, depth: int) -> list[AccessStep]:
    """Expand a march spec into a single flat per-address memory-operation
    sequence, resolving every ``either`` element via :func:`resolve_directions`
    -- the same canonical rule :meth:`AlgSpec.to_numeric` uses, so this trace
    always matches what a compiled engine actually runs. This is the canonical
    linear form; the *comparison* against a real controller uses
    :func:`expand_expected_blocks` instead, which additionally accepts a
    reversed traversal for ``either`` elements -- a real controller is free to
    choose either order, but this trace makes no claim about what's allowed,
    only about what one canonical run does.
    """
    if depth <= 0:
        raise AlgSpecError(f"depth must be a positive number of words, got {depth}")
    dirs = resolve_directions(spec.elements)
    steps: list[AccessStep] = []
    for e_idx, elem in enumerate(spec.elements):
        steps.extend(_expand_element(elem, e_idx, depth, dirs[e_idx]))
    return steps


@dataclass(slots=True, frozen=True)
class ElementBlock:
    """One march element's expected memory operations, with every address
    ordering a correct controller is allowed to use. A fixed-direction element
    (``up``/``down``) has exactly one acceptable ordering; an ``either`` (↕)
    element has two -- ascending OR descending -- because the march definition
    leaves the address order of an ``either`` element free (a controller like
    ``march_c_fsm`` legitimately runs one ``either`` element up and another
    down). The comparison accepts the observed block if it matches ANY
    ordering. ``orderings[0]`` is always the canonical direction (see
    :func:`resolve_directions`) -- for a fixed-direction element that's its
    only ordering anyway, and for an ``either`` element it is what a
    divergence message reports as "expected", rather than an arbitrary pick.
    """
    elem_idx: int
    orderings: tuple[tuple[AccessStep, ...], ...]

    @property
    def size(self) -> int:
        return len(self.orderings[0])


def expand_expected_blocks(spec: AlgSpec, depth: int) -> list[ElementBlock]:
    """Expand a march spec into per-element blocks for sequence comparison,
    encoding the ``either``-direction freedom (see :class:`ElementBlock`). The
    two orderings an ``either`` element accepts are DERIVED from the one
    canonical rule (:func:`resolve_directions`) rather than independently
    fixed at ascending/descending -- so this stays a documented RELAXATION of
    the canonical rule, not a second rule that could silently disagree with
    it (which is exactly the bug that made the engine and the hand-written
    classic RTL disagree on `march_x` before that rule was unified)."""
    if depth <= 0:
        raise AlgSpecError(f"depth must be a positive number of words, got {depth}")
    dirs = resolve_directions(spec.elements)
    blocks: list[ElementBlock] = []
    for e_idx, elem in enumerate(spec.elements):
        canonical = tuple(_expand_element(elem, e_idx, depth, dirs[e_idx]))
        if elem.direction == 2:  # either -- the other order is also acceptable
            other = tuple(_expand_element(elem, e_idx, depth, 1 - dirs[e_idx]))
            orderings = (canonical, other)
        else:
            orderings = (canonical,)
        blocks.append(ElementBlock(elem_idx=e_idx, orderings=orderings))
    return blocks


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
