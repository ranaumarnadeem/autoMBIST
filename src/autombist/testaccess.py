"""Wraps a generated MBIST wrapper's real control/status ports with an IEEE 1149.1
(JTAG/TAP) + IEEE 1687 (IJTAG) test-access network, via the external ``warptap`` package
(https://github.com/ranaumarnadeem/warptap, PyPI ``warptap``).

Import-guarded exactly like tcl_shell.py's tkinter dependency: warptap is an optional
capability, not a core one, so its absence must never break the rest of the CLI. Callers
get a clear ``TestAccessUnavailable`` at the point of use, not an ImportError at import
time of this module (which every other autombist module transitively imports via cli.py).

Scope, decided deliberately narrow rather than exhaustive:

Only the ALWAYS-1-bit control/status ports are wrapped -- test_mode, bist_start,
bist_done, bist_fail, and (when configured) self_repair_start/done/fail/busy and
repair_load/repair_load_done. This is not an arbitrary subset: it is exactly the port
set warptap's own test suite already proves end-to-end against a real generated
mem_subsystem_mbist (real Yosys ingest, real SIB insertion, real Icarus simulation of
the inserted RTL, real ICL round-trip through the vendored icl_parser). The ICL
round-trip claim is independently re-proven against THIS project's own generated output
too, not only warptap's fixture -- see
tests/integration/test_testaccess_warptap_e2e.py's
test_icl_round_trips_through_the_vendored_parser (chain order, instrument names,
widths, and READ/WRITE direction all survive; signal_bits/capture_value do not, and
icl_import.py documents that as a permanent ICL-format limitation, not a bug). Deliberately
EXCLUDED: fuse_row_repair_en/fuse_faulty_row_addr (persistence load-in) and
row_repair_en/faulty_row_addr/col_repair_en/faulty_bit (tester-driven repair). The
*_row_addr ports are `[num_spare_rows*ADDR_WIDTH-1:0]` in wrapper_template.j2 -- multi-bit
for any realistic address width, confirmed directly against the template, not assumed.
The *_repair_en ports are `[num_spare_rows-1:0]` -- actually 1 bit wide in a
single-spare-row config, which is every config this module has been tested against so
far -- but excluded unconditionally regardless: making an enable port's wrappability
depend on how many spares a given design happens to have would be a stranger, more
surprising rule than excluding the whole repair-port group together. What actually needs
the width>1 ICL path is any config with more than one spare row/column, and that path is
exactly what warptap's own icl_import round-trip test found broken (a single width=3
READ-only instrument alone reproduces it) -- every port wrapped here is confirmed width=1
regardless of redundancy config, so that failure mode cannot be hit by this module's
output. Wrapping the wider repair ports is a real, separate, larger piece of work (each
needs one SignalBinding per bit, and the width>1 ICL path needs its own verification),
not a v1 decision made here.

fail_valid/fail_addr are not listed above because they are not wrappable at all: they
are internal, single-functional-cycle combinational wires, never ports on the generated
wrapper (confirmed directly against wrapper_template.j2, not assumed) -- see
docs/manifest-plan.md and docs/ijtag-handoff.md's Stage-0 finding for the full account
of why diagnosis readback needs an additive RTL change this module does not make.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from warptap.icl_model import InstrumentDirection, SignalBinding
    from warptap.pipeline import insert_test_access as _warptap_insert_test_access
    from warptap.sib_plan import InstrumentSpec

    _WARPTAP_IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - depends on optional install
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
    """One 1-bit control or status port on a generated wrapper, and how warptap should
    wrap it. ``name`` is the real port name on the wrapper module (verbatim, not
    normalized) -- what the generated Verilog actually calls it."""

    name: str
    role: str  # "control" (WRITE) or "status" (READ) -- see classify_test_access_ports


def _require_warptap() -> None:
    if _warptap_insert_test_access is None:
        raise TestAccessUnavailable(
            "warptap is not installed. Test-access wrapping (JTAG/TAP/IJTAG/ICL/PDL) is "
            "an optional capability: `pip install warptap` (or the `test-access` extra) "
            f"to enable it. Import error was: {_WARPTAP_IMPORT_ERROR}"
        )


def classify_test_access_ports(
    *, onchip_selfrepair: bool = False, onchip_repair_persistence: bool = False
) -> list[TestAccessPort]:
    """The always-1-bit control/status ports a generated wrapper exposes, for this
    redundancy configuration -- mirrors wrapper_template.j2's own has_onchip_selfrepair /
    has_onchip_repair_persistence gating exactly (generator.py enforces persistence
    implies self-repair; this function does not re-validate that, it assumes a config
    that already passed generate_from_config's own validation).

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
    if onchip_selfrepair:
        ports += [
            TestAccessPort("self_repair_start", "control"),
            TestAccessPort("self_repair_done", "status"),
            TestAccessPort("self_repair_fail", "status"),
            TestAccessPort("self_repair_busy", "status"),
        ]
    if onchip_repair_persistence:
        ports += [
            TestAccessPort("repair_load", "control"),
            TestAccessPort("repair_load_done", "status"),
        ]
    return ports


def build_instrument_specs(ports: list[TestAccessPort]) -> list[Any]:
    """Convert TestAccessPort entries to warptap InstrumentSpec objects. Raises
    TestAccessUnavailable if warptap is not installed -- called lazily, not at module
    import time, so importing autombist.testaccess itself never requires warptap."""
    _require_warptap()
    specs = []
    for p in ports:
        direction = InstrumentDirection.WRITE if p.role == "control" else InstrumentDirection.READ
        specs.append(
            InstrumentSpec(
                p.name, width=1, capture_value=0, direction=direction,
                signal_bits=(SignalBinding(p.name),),
            )
        )
    return specs


def wrap_test_access(
    sources: list[Path | str],
    top_module: str,
    *,
    onchip_selfrepair: bool = False,
    onchip_repair_persistence: bool = False,
    yosys_command: str | None = None,
) -> tuple[str, Any, Any]:
    """Ingest ``sources``, wrap ``top_module``'s real control/status ports
    (classify_test_access_ports) with a JTAG/IJTAG test-access network, and return
    ``(inserted_verilog, graph, root)`` -- the same shape warptap.pipeline.insert_test_access
    returns, so ``PDLInterpreter(graph, root)`` works immediately on the result.

    Raises TestAccessUnavailable if warptap is not installed. Sources must include every
    file the design needs (shared algorithm RTL, repair RTL, the wrapper(s), macro
    blackboxes/models) -- this function does no source discovery of its own, matching
    warptap.pipeline.insert_test_access's own scope.
    """
    _require_warptap()
    ports = classify_test_access_ports(
        onchip_selfrepair=onchip_selfrepair,
        onchip_repair_persistence=onchip_repair_persistence,
    )
    specs = build_instrument_specs(ports)
    return _warptap_insert_test_access(
        sources, top_module, specs, yosys_command=yosys_command, use_sv=True,
    )
