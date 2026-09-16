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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from warptap.errors import WarptapError
    from warptap.icl_model import InstrumentDirection, SignalBinding
    from warptap.pipeline import insert_test_access as _warptap_insert_test_access
    from warptap.sib_plan import InstrumentSpec

    _WARPTAP_IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - depends on optional install
    WarptapError = None  # type: ignore[assignment,misc]
    InstrumentDirection = None  # type: ignore[assignment]
    SignalBinding = None  # type: ignore[assignment]
    InstrumentSpec = None  # type: ignore[assignment]
    _warptap_insert_test_access = None  # type: ignore[assignment]
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
    """
    _require_warptap()
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
    try:
        return _warptap_insert_test_access(
            sources, top_module, specs, yosys_command=yosys_command, use_sv=True,
        )
    except (IndexError, KeyError, WarptapError) as exc:
        raise ValueError(
            f"warptap insertion failed ({type(exc).__name__}: {exc}) while wrapping "
            f"{[p.name for p in ports]} -- this usually means a port name or width "
            "classify_test_access_ports produced doesn't match the real ingested "
            "netlist (a stale/mismatched --config snapshot, or --source files from a "
            "different generate run)"
        ) from exc


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
