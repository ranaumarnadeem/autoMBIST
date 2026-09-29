"""Wraps a generated MBIST wrapper's real control/status ports with an IEEE 1149.1
(JTAG/TAP) + IEEE 1687 (IJTAG) test-access network, via the external ``warptap`` package
(https://github.com/ranaumarnadeem/warptap, PyPI ``warptap``).

Import-guarded exactly like tcl_shell.py's tkinter dependency: warptap is an optional
capability, not a core one, so its absence must never break the rest of the CLI. Callers
get a clear ``TestAccessUnavailable`` at the point of use, not an ImportError at import
time of this module (which every other autombist module transitively imports via cli.py).

Every control/status port a generated wrapper can expose is wrappable: both the
always-1-bit ones -- test_mode, bist_start, bist_done, bist_fail, self_repair_start/
done/fail/busy, repair_load/repair_load_done, diag_overflow -- and the WIDE ones this
module used to exclude entirely: diag_valid/diag_addr (the rest of diagnosis readback),
fuse_row_repair_en/fuse_faulty_row_addr (repair persistence load-in), and the generic
``repair_ports:`` tester-driven passthrough pins (never wrapped before this at all, not
a width-bug casualty). Wide ports need their own width geometry --
``num_diagnosis_entries``/``num_spare_rows``/``addr_width``/``repair_ports`` -- passed
to classify_test_access_ports/wrap_test_access; see test_access_kwargs_from_config
below for deriving them from an already-loaded config.yml snapshot (what the
wrap-test-access CLI's ``--config`` flag does) rather than restating them by hand -- a
too-small manually-typed width doesn't error, it silently leaves a WRITE port's
undriven upper bits floating.

Wide-port wrapping was blocked until warptap v0.0.2: its vendored icl_parser fork threw
a bare AssertionError constructing an IclRegisterModel for any width>1 instrument.
Confirmed fixed directly against a real width>1 round-trip, not just from the
changelog -- see tests/integration/test_testaccess_warptap_e2e.py's
test_icl_round_trips_through_the_vendored_parser (chain order, instrument names,
widths, and READ/WRITE direction all survive a real emit-then-reimport through the
vendored parser; signal_bits/capture_value do not, and icl_import.py documents that as
a permanent ICL-format limitation, not a bug).

unrepairable, row_repair_en/faulty_row_addr/col_repair_en/faulty_bit (under
``onchip_selfrepair`` specifically), and fail_valid/fail_addr are not wrappable at all,
for a structural reason unrelated to width -- confirmed directly against
wrapper_template.j2 that none of them are ever promoted to the wrapper boundary, they
are internal wires only. self_repair_fail (already wrapped) is unrepairable's
externally-visible reflection; diagnosis logging (diag_valid/diag_addr/diag_overflow)
is what superseded any need to expose fail_valid/fail_addr directly.
row_repair_en/faulty_row_addr/col_repair_en/faulty_bit ARE real, wrappable boundary
ports under the *tester-driven* ``repair_ports:`` config instead (mutually exclusive
with ``onchip_selfrepair``) -- that's the generic ``repair_ports:`` mechanism above,
not a special case of these specific names.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from warptap.errors import WarptapError
    from warptap.icl_model import InstrumentDirection, SignalBinding
    from warptap.pipeline import insert_test_access as _warptap_insert_test_access
    from warptap.sib_plan import InstrumentSpec
    from warptap.yosys_io import ingest as _warptap_ingest

    _WARPTAP_IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - depends on optional install
    WarptapError = None  # type: ignore[assignment,misc]
    InstrumentDirection = None  # type: ignore[assignment]
    SignalBinding = None  # type: ignore[assignment]
    InstrumentSpec = None  # type: ignore[assignment]
    _warptap_insert_test_access = None  # type: ignore[assignment]
    _warptap_ingest = None  # type: ignore[assignment]
    _WARPTAP_IMPORT_ERROR = _exc


class TestAccessUnavailable(RuntimeError):
    """Raised when a test-access operation is attempted without warptap installed.

    Distinct from warptap's own WarptapError hierarchy: this is autombist's own
    "the optional dependency is missing" signal, raised before any warptap code runs,
    mirroring tcl_shell.py's tkinter-unavailable error shape.
    """

    __test__ = False  # not a pytest test class -- the name just starts with "Test"


@dataclass(frozen=True, slots=True)
class TestAccessPort:
    """One control or status port on a generated wrapper, and how warptap should wrap
    it. ``name`` is the real port name on the wrapper module (verbatim, not
    normalized) -- what the generated Verilog actually calls it. ``width`` defaults to
    1 (every port this module wrapped before wide-port support) -- see
    classify_test_access_ports for which ports are actually wide and why."""

    name: str
    role: str  # "control" (WRITE) or "status" (READ) -- see classify_test_access_ports
    width: int = 1


TestAccessPort.__test__ = False  # not a pytest test class -- the name just starts with "Test"


def _require_warptap() -> None:
    if _warptap_insert_test_access is None:
        raise TestAccessUnavailable(
            "warptap is not installed. Test-access wrapping (JTAG/TAP/IJTAG/ICL/PDL) is "
            "an optional capability: `pip install warptap` (or the `test-access` extra) "
            f"to enable it. Import error was: {_WARPTAP_IMPORT_ERROR}"
        )


def classify_test_access_ports(
    *,
    onchip_selfrepair: bool = False,
    onchip_repair_persistence: bool = False,
    onchip_diagnosis: bool = False,
    num_spare_rows: int = 0,
    num_diagnosis_entries: int = 0,
    addr_width: int = 0,
    repair_ports: Sequence[Mapping[str, Any]] = (),
) -> list[TestAccessPort]:
    """The control/status ports a generated wrapper exposes, for this redundancy
    configuration -- mirrors wrapper_template.j2's own has_onchip_selfrepair /
    has_onchip_repair_persistence / has_onchip_diagnosis gating and port declaration
    order exactly (generator.py enforces persistence and diagnosis both imply
    self-repair, and repair_ports is mutually exclusive with onchip_selfrepair; this
    function does not re-validate either, it assumes a config that already passed
    generate_from_config's own validation).

    The wide ports (fuse_row_repair_en/fuse_faulty_row_addr, diag_valid/diag_addr) are
    only added when BOTH their flag and their geometry are given -- e.g.
    onchip_repair_persistence=True with num_spare_rows=0 (the default) produces exactly
    the always-1-bit ports this function has always returned, byte-identical to before
    wide-port support existed. This is what makes every existing caller (which only
    ever passes the three booleans) backward compatible by construction, not a
    separate code path to keep in sync. repair_ports entries get their width/direction
    verbatim from the caller's own already-validated {name, width, dir} list -- see
    test_access_kwargs_from_config for deriving all of these from a loaded config.yml
    snapshot.

    Order matches the wrapper's own port declaration order in wrapper_template.j2, so
    the resulting warptap chain order is stable and traceable back to the generated
    Verilog by inspection, not just by name.
    """
    ports = [
        TestAccessPort("test_mode", "control"),
        TestAccessPort("bist_start", "control"),
        TestAccessPort("bist_done", "status"),
        TestAccessPort("bist_fail", "status"),
    ]
    for rp in repair_ports:
        role = "control" if rp["dir"] == "input" else "status"
        ports.append(TestAccessPort(rp["name"], role, width=rp["width"]))
    if onchip_selfrepair:
        ports += [
            TestAccessPort("self_repair_start", "control"),
            TestAccessPort("self_repair_done", "status"),
            TestAccessPort("self_repair_fail", "status"),
            TestAccessPort("self_repair_busy", "status"),
        ]
    if onchip_repair_persistence:
        ports.append(TestAccessPort("repair_load", "control"))
        if num_spare_rows > 0 and addr_width > 0:
            ports.append(TestAccessPort("fuse_row_repair_en", "control", width=num_spare_rows))
            ports.append(
                TestAccessPort("fuse_faulty_row_addr", "control", width=num_spare_rows * addr_width)
            )
        ports.append(TestAccessPort("repair_load_done", "status"))
    if onchip_diagnosis:
        if num_diagnosis_entries > 0 and addr_width > 0:
            ports.append(TestAccessPort("diag_valid", "status", width=num_diagnosis_entries))
            ports.append(
                TestAccessPort("diag_addr", "status", width=num_diagnosis_entries * addr_width)
            )
        ports.append(TestAccessPort("diag_overflow", "status"))
    return ports


def build_instrument_specs(ports: list[TestAccessPort]) -> list[Any]:
    """Convert TestAccessPort entries to warptap InstrumentSpec objects -- one
    SignalBinding per bit (signal_bits[k] binds bit k of the port, matching how
    warptap's own iWrite()/read-back values are bit-indexed), so this is a single,
    width-generic construction rather than a special case for wide ports: for every
    existing width=1 port, ``tuple(SignalBinding(p.name, i) for i in range(1))`` is
    exactly ``(SignalBinding(p.name, 0),)``, the same binding this produced before wide
    ports existed. Raises TestAccessUnavailable if warptap is not installed -- called
    lazily, not at module import time, so importing autombist.testaccess itself never
    requires warptap."""
    _require_warptap()
    specs = []
    for p in ports:
        if p.width < 1:
            raise ValueError(f"port {p.name!r} has invalid width {p.width} (must be >= 1)")
        direction = InstrumentDirection.WRITE if p.role == "control" else InstrumentDirection.READ
        specs.append(
            InstrumentSpec(
                p.name, width=p.width, capture_value=0, direction=direction,
                signal_bits=tuple(SignalBinding(p.name, i) for i in range(p.width)),
            )
        )
    return specs


def wrap_test_access(
    sources: list[Path | str],
    top_module: str,
    *,
    onchip_selfrepair: bool = False,
    onchip_repair_persistence: bool = False,
    onchip_diagnosis: bool = False,
    num_spare_rows: int = 0,
    num_diagnosis_entries: int = 0,
    addr_width: int = 0,
    repair_ports: Sequence[Mapping[str, Any]] = (),
    idcode_value: int | None = None,
    yosys_command: str | None = None,
) -> tuple[str, Any, Any]:
    """Ingest ``sources``, wrap ``top_module``'s real control/status ports
    (classify_test_access_ports) with a JTAG/IJTAG test-access network, and return
    ``(inserted_verilog, graph, root)`` -- the same shape warptap.pipeline.insert_test_access
    returns, so ``PDLInterpreter(graph, root)`` works immediately on the result.

    Raises TestAccessUnavailable if warptap is not installed, and ValueError if
    insertion fails -- most likely a port name/width classify_test_access_ports
    produced that doesn't match the real ingested netlist (e.g. num_spare_rows/
    num_diagnosis_entries/addr_width from a --config snapshot that doesn't actually
    match ``sources``). Sources must include every file the design needs (shared
    algorithm RTL, repair RTL, the wrapper(s), macro blackboxes/models) -- this
    function does no source discovery of its own, matching
    warptap.pipeline.insert_test_access's own scope.

    ``idcode_value`` is the inserted TAP's IDCODE; ``None`` keeps warptap's own
    placeholder default. It is validated with warptap's own rule (32 bits, bit 0 set,
    as IEEE 1149.1 requires) before anything is ingested, and raises ValueError when
    it fails; it needs a warptap that can set one, else TestAccessUnavailable.
    """
    _require_warptap()
    if idcode_value is not None:
        _check_idcode(idcode_value)
    ports = classify_test_access_ports(
        onchip_selfrepair=onchip_selfrepair,
        onchip_repair_persistence=onchip_repair_persistence,
        onchip_diagnosis=onchip_diagnosis,
        num_spare_rows=num_spare_rows,
        num_diagnosis_entries=num_diagnosis_entries,
        addr_width=addr_width,
        repair_ports=repair_ports,
    )
    specs = build_instrument_specs(ports)
    extra = {} if idcode_value is None else {"idcode_value": idcode_value}
    try:
        return _warptap_insert_test_access(
            sources, top_module, specs, yosys_command=yosys_command, use_sv=True, **extra,
        )
    except TypeError as exc:
        if idcode_value is None or "idcode_value" not in str(exc):
            raise
        raise TestAccessUnavailable(_NO_IDCODE_SUPPORT) from exc
    except (IndexError, KeyError, WarptapError) as exc:
        raise ValueError(
            f"warptap insertion failed ({type(exc).__name__}: {exc}) while wrapping "
            f"{[p.name for p in ports]} -- this usually means a port name or width "
            "classify_test_access_ports produced doesn't match the real ingested "
            "netlist (a stale/mismatched --config snapshot, or --source files from a "
            "different generate run)"
        ) from exc


DEFAULT_TCK_MAX_FREQ_HZ = 10e6
"""The maximum TCK frequency the BSDL states when the caller gives none. BSDL requires the
attribute and nothing in the RTL says what it is, so this is an assumption; the CLI says so
whenever it applies it."""

_NO_IDCODE_SUPPORT = (
    "the installed warptap cannot set a TAP IDCODE (no idcode_value support) -- upgrade "
    "warptap, or omit --idcode to keep its placeholder"
)
_NO_BSDL_SUPPORT = (
    "the installed warptap cannot emit a BSDL whose ICL AccessLink names the network's "
    "instruction (it has no warptap.bsdl_emit) -- upgrade warptap"
)


def _check_idcode(value: int) -> None:
    """Raise ValueError unless ``value`` can be a TAP's IDCODE, by warptap's own rule (so
    autoMBIST doesn't restate it)."""
    try:
        from warptap.tap_model import idcode_value_error
    except ImportError as exc:
        raise TestAccessUnavailable(_NO_IDCODE_SUPPORT) from exc
    problem = idcode_value_error(value)
    if problem is not None:
        raise ValueError(problem)


def bsdl_entity_candidates(top: str) -> list[str]:
    """The BSDL entity names to try for module ``top``, best first: the module's own name,
    then that name made a VHDL identifier (letters, digits and single underscores, starting
    with a letter), then the same with ``_tap`` appended (for a VHDL reserved word). A legal
    Verilog module name such as ``a__b`` is not a legal BSDL entity name, and the BSDL's
    entity is only the name of the device the TAP belongs to, so it need not equal the RTL
    module's."""
    clean = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]", "_", top)).strip("_")
    if not clean or not clean[0].isalpha():
        clean = f"tap_{clean}".strip("_")
    candidates: list[str] = []
    for name in (top, clean, f"{clean}_tap"):
        if name not in candidates:
            candidates.append(name)
    return candidates


@dataclass(frozen=True)
class TapDescription:
    """What a tester or retargeting tool needs to reach the network: the ICL (its
    AccessLink names the TAP instruction that selects the network) and the BSDL that
    instruction is declared in, both naming ``entity``."""

    icl: str
    bsdl: str
    entity: str
    tck_max_freq_hz: float


def describe_test_access_tap(
    graph: Any,
    root: Any,
    *,
    tck_max_freq_hz: float = DEFAULT_TCK_MAX_FREQ_HZ,
    idcode_value: int | None = None,
) -> TapDescription:
    """Render the ICL (with its AccessLink) and BSDL for the network ``wrap_test_access``
    inserted. Pass the same ``idcode_value`` given to ``wrap_test_access``: the BSDL states
    what the hardware was built with. Raises ValueError for an unusable frequency, IDCODE
    or entity name, and TestAccessUnavailable for a warptap without BSDL support."""
    _require_warptap()
    if (
        isinstance(tck_max_freq_hz, bool)
        or not isinstance(tck_max_freq_hz, (int, float))
        or not math.isfinite(tck_max_freq_hz)
        or tck_max_freq_hz <= 0
    ):
        raise ValueError(f"the TCK's maximum frequency must be a finite number > 0 Hz, got {tck_max_freq_hz!r}")
    if idcode_value is not None:
        _check_idcode(idcode_value)
    try:
        from warptap.bsdl_emit import BsdlEmitError, to_bsdl
        from warptap.icl_emit import to_icl
    except ImportError as exc:
        raise TestAccessUnavailable(_NO_BSDL_SUPPORT) from exc

    extra = {} if idcode_value is None else {"idcode_value": idcode_value}
    failure: Exception | None = None
    for entity in bsdl_entity_candidates(root.name):
        try:
            bsdl = to_bsdl(entity, tck_max_freq_hz=float(tck_max_freq_hz), **extra)
        except BsdlEmitError as exc:  # frequency and IDCODE are already checked: the name
            failure = exc
            continue
        except TypeError as exc:
            raise TestAccessUnavailable(_NO_IDCODE_SUPPORT if extra else _NO_BSDL_SUPPORT) from exc
        try:
            icl = to_icl(graph, root, include_access_link=True, bsdl_entity_name=entity)
        except TypeError as exc:
            raise TestAccessUnavailable(_NO_BSDL_SUPPORT) from exc
        return TapDescription(icl=icl, bsdl=bsdl, entity=entity, tck_max_freq_hz=float(tck_max_freq_hz))
    raise ValueError(f"no BSDL entity name works for module {root.name!r}: {failure}")


def tap_facts(
    *, idcode_value: int | None = None, description: TapDescription | None = None
) -> dict[str, Any]:
    """The TAP facts a consumer of the manifest needs, from warptap's own constants: the
    IDCODE the hardware holds after reset, the instruction register's length and the
    instruction that selects the IJTAG network. ``description`` adds the BSDL's entity and
    TCK limit. The network's instruction is EXTEST because that is the opcode the vectors
    load (``OPCODE_EXTEST``)."""
    _require_warptap()
    from warptap.tap_model import DEFAULT_IR_WIDTH, IDCODE_VALUE, OPCODE_EXTEST

    idcode = IDCODE_VALUE if idcode_value is None else idcode_value
    facts: dict[str, Any] = {
        "idcode": f"0x{idcode:08X}",
        "idcode_is_placeholder": idcode == IDCODE_VALUE,
        "instruction_length": DEFAULT_IR_WIDTH,
        "network_access_instruction": "EXTEST",
        "network_access_opcode": format(OPCODE_EXTEST, f"0{DEFAULT_IR_WIDTH}b"),
    }
    if description is not None:
        facts["bsdl_entity"] = description.entity
        facts["tck_max_freq_hz"] = description.tck_max_freq_hz
    return facts


def _attr_int(value: Any) -> int | None:
    # Yosys JSON writes integer attributes as binary strings ("000...0101").
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 2) if text and set(text) <= {"0", "1"} else None


def enumerate_test_access_instances(
    verilog_path: Path | str,
    top_module: str,
    *,
    extra_sources: Sequence[Path | str] = (),
    yosys_command: str | None = None,
) -> list[dict[str, Any]]:
    """Every module instance in ``top_module`` of a wrap_test_access output, read back
    through warptap's own ingest (the same Yosys front end its insertion used), with the
    warptap_* attributes it tags each inserted cell with: warptap_sib_name /
    warptap_instrument_name on a SIB, warptap_sib_name / warptap_instrument_bit on each
    TDR bit. Yosys primitive cells ($and, $dff, ...) are skipped.

    ``extra_sources`` must include any blackbox stub that stood in for a module during
    insertion -- warptap's output omits blackbox module definitions, so the re-read
    needs the stub again to resolve the hierarchy.
    """
    _require_warptap()
    try:
        raw = _warptap_ingest(
            [verilog_path, *extra_sources], top_module, yosys_command=yosys_command, use_sv=True,
        )
    except WarptapError as exc:
        raise ValueError(f"could not re-read {verilog_path} to enumerate its instances: {exc}") from exc
    modules = raw["modules"]
    instances = []
    for name, cell in modules[top_module]["cells"].items():
        module_type = cell["type"]
        if module_type.startswith("$"):
            continue
        attrs = cell.get("attributes", {})
        module_attrs = modules.get(module_type, {}).get("attributes", {})
        instances.append({
            "instance": name,
            "module_type": module_type,
            "is_blackbox": bool(_attr_int(module_attrs.get("blackbox"))),
            "sib_name": attrs.get("warptap_sib_name"),
            "instrument_name": attrs.get("warptap_instrument_name"),
            "instrument_bit": _attr_int(attrs.get("warptap_instrument_bit")),
            "mux_name": attrs.get("warptap_mux_name"),
        })
    return instances


def test_access_kwargs_from_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Derive wrap_test_access's/classify_test_access_ports's keyword arguments from an
    already-loaded config -- the same shape as the config.yml snapshot
    generate_from_config writes into every output directory (render_config, the exact
    dict render_wrapper() itself renders from). Deliberately does not use
    generator.load_config(): that validates RAW USER INPUT (rejects unknown top-level
    keys), while a config.yml snapshot is the RESOLVED render_config, a superset with
    many keys (algo_dir, autombist_*, ...) load_config would reject as unrecognized.

    Raises ValueError if ``config`` is missing ``addr_width`` -- the cheapest signal
    that this isn't really a generate config.yml snapshot at all.
    """
    if "addr_width" not in config:
        raise ValueError(
            "config is missing required key 'addr_width' -- is this a real generate "
            "config.yml snapshot?"
        )
    redundancy = config.get("redundancy") or {}
    return {
        "onchip_selfrepair": bool(redundancy.get("onchip_selfrepair", False)),
        "onchip_repair_persistence": bool(redundancy.get("onchip_repair_persistence", False)),
        "onchip_diagnosis": bool(redundancy.get("onchip_diagnosis", False)),
        "num_spare_rows": int(redundancy.get("num_spare_rows", 0) or 0),
        "num_diagnosis_entries": int(redundancy.get("num_diagnosis_entries", 0) or 0),
        "addr_width": int(config["addr_width"]),
        "repair_ports": config.get("repair_ports") or (),
    }


test_access_kwargs_from_config.__test__ = False  # not a pytest test function -- name starts with "test_"
