"""1-port sanity check for march_engine_mp.sv (Phase 5 of the multi-port
fault-DSL generalization).

This is an INTERNAL-CONSISTENCY proof, not a dispatch-logic proof: in normal
production use, mem.num_ports == 1 always dispatches to the OLD, untouched
march_engine.sv (see algo_engine._resolve_engine_sources) -- a single-port
campaign never touches march_engine_mp.sv at all. This test instead compiles
and drives march_engine_mp.sv DIRECTLY (bypassing run_algo_campaign's
dispatch entirely, via compile_engine(sources=...) with an explicit
top_module="march_engine_mp"), to prove that the new engine is ALSO
semantically correct in the degenerate single-port case, standing entirely
on its own.

march_engine_mp.sv's port bus is hardwired to fault_ram's num_ports=2 shape
(clk0/.../dout0, clk1/.../dout1 -- see the module body), so fault_ram.sv
here is necessarily rendered with num_ports=2 (there is no RTL sense in
which march_engine_mp could compile against a num_ports=1 fault_ram; that
degenerate pairing is exactly what plain march_engine.sv + num_ports=1
fault_ram.sv already is, and is covered by test_fault_ram_gen_e2e.py). The
"1-port" in "1-port sanity check" describes the ALGORITHM/fault list (every
op and every fault instance stays on port 0) -- i.e. port 1 of the RTL bus
exists but is never driven, which is the correct definition of "degenerate
single-port use of a multi-port-capable engine".

The reference table (REFERENCE_COVERAGE / REFERENCE_PER_FAULT) is copied
verbatim from tests/integration/test_fault_ram_gen_e2e.py, which exercises
the OLD march_engine.sv against the exact same faults.example.txt fixture
and default_registry()-rendered fault_ram.sv (num_ports=1). Reproducing the
identical per-fault detected/escaped table here -- not just the totals --
proves march_engine_mp.sv's port-aware do_read/do_write tasks and its
extended-format .algc parser degrade to EXACTLY today's single-port
semantics when every op is on port 0 (the "plain" numeric-line format is
what AlgSpec.to_numeric() emits for every existing built-in .alg, so this
also exercises the plain/non-extended branch of the file loader -- see
_run_campaign_against_mp_engine's docstring for confirmation this branch is
genuinely taken).

*** PRE-EXISTING PHASE 4 BUG DISCOVERED WHILE BUILDING THIS TEST -- NOW FIXED
(fault_ram_template.sv.j2's fault-list parser falls back to a plain 7-field
$sscanf when the extended 9-field scan comes up short; see
test_run_algo_campaign_num_ports_2_with_default_port_fault below for the
regression test proving the fix, added once the bug was fixed) ***

fault_ram_template.sv.j2's num_ports==2 branch parses each fault-list line
with a single call:

    n = $sscanf(line, "%s %d %d %d %d %d %d %d %d",
                ts, f.va, f.vb, f.aa, f.ab, f.p0, f.p1, f.vp, f.ap);
    if (n < 1 || ts.substr(0,0) == "#") continue;
    ...
    if (n < 7) begin  $display("FATAL: ... need 7"); $finish; end

Under Verilator (and per IEEE 1800's $sscanf semantics, which it follows
here, unlike libc scanf), a $sscanf call whose format string has MORE
conversion items than the input string can satisfy returns the NEGATIVE of
the attempted item count on failure -- NOT the partial count of items
actually converted. Confirmed directly with a standalone probe module
(built and run under this same Verilator 5.049, then deleted -- not part of
this diff): a 7-field plain fault line ("SA0 10 3 0 0 0 0") fed to this
9-item format returns n == -1, not n == 7. Consequently `n < 1` is TRUE, and
the line is silently `continue`d as if it were a comment -- EVERY
pre-multi-port ("plain", no trailing vport/aport) fault-list line is
silently dropped when parsed against a num_ports=2-rendered fault_ram.sv.
Lines that DO carry explicit trailing vport/aport fields (9 fields total)
parse correctly (confirmed n == 9 for a full 9-field line), because then
the format string's item count exactly matches what the line provides.

Practical consequence THIS BUG HAD before the fix: faults.example.txt (and
every FaultRecord round-tripped through FaultRecord.to_line(), which omits
the trailing vport/aport columns whenever both are 0 -- see algo_engine.py)
was unparseable by a num_ports=2 fault_ram.sv. fault_ram_template.sv.j2 now
falls back to a plain 7-field $sscanf when the 9-field scan comes up short
(n < 7), so plain fault lines parse correctly again even under num_ports=2 --
see test_run_algo_campaign_num_ports_2_with_default_port_fault below, which
exercises exactly this path through the real public run_algo_campaign() API.
This test file still writes its OWN fault-list with every trailing vport/
aport field always present explicitly (vport=0, aport=0 spelled out, not
omitted) -- a 9-field line per fault -- since that remains a legitimate,
already-correctly-handled fault-list shape and this test predates the fix;
no need to change it now that both shapes work. See this task's final report
and the follow-up fix commit for the full
disclosure of this finding.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import find_engine_dir, resolve_algo  # noqa: E402
from autombist.algo_engine import (  # noqa: E402
    FaultRecord,
    MemoryParams,
    compile_engine,
    load_fault_list,
    parse_result_line,
    run_one,
)
from autombist.fault_primitives import default_registry  # noqa: E402
from autombist.fault_ram_gen import render_and_write  # noqa: E402


def _write_fault_list_explicit_ports(records: list[FaultRecord], path: Path) -> Path:
    """Like algo_engine.write_fault_list, but ALWAYS emits the trailing
    vport/aport columns explicitly (9 fields per line), never the
    7-field-omitted shape FaultRecord.to_line() prefers when both ports are
    0. Required to route around the pre-existing Phase 4 fault_ram_template
    .sv.j2 $sscanf bug documented in this file's module docstring -- the
    9-field shape is unaffected by that bug and already parses correctly."""
    lines = [
        f"{r.type} {r.vaddr} {r.vbit} {r.aaddr} {r.abit} {r.p0} {r.p1} {r.vport} {r.aport}"
        for r in records
    ]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path

# Copied verbatim from test_fault_ram_gen_e2e.py's REFERENCE_COVERAGE /
# REFERENCE_PER_FAULT -- the OLD engine's (march_engine.sv) reference table.
REFERENCE_COVERAGE = {"march_c": (14, 19), "mats_plus": (12, 19), "march_ss": (18, 19)}

REFERENCE_PER_FAULT = {
    "SA0": {"march_c": True, "mats_plus": True, "march_ss": True},
    "SA1": {"march_c": True, "mats_plus": True, "march_ss": True},
    "TF0": {"march_c": True, "mats_plus": True, "march_ss": True},
    "TF1": {"march_c": True, "mats_plus": True, "march_ss": True},
    "WDF0": {"march_c": False, "mats_plus": False, "march_ss": True},
    "WDF1": {"march_c": False, "mats_plus": False, "march_ss": True},
    "RDF0": {"march_c": True, "mats_plus": True, "march_ss": True},
    "RDF1": {"march_c": True, "mats_plus": True, "march_ss": True},
    "DRDF0": {"march_c": False, "mats_plus": False, "march_ss": True},
    "DRDF1": {"march_c": False, "mats_plus": False, "march_ss": True},
    "IRF0": {"march_c": True, "mats_plus": True, "march_ss": True},
    "IRF1": {"march_c": True, "mats_plus": True, "march_ss": True},
    "SOF": {"march_c": False, "mats_plus": False, "march_ss": False},
    "AF_NOACC": {"march_c": True, "mats_plus": True, "march_ss": True},
    "AF_ALIAS": {"march_c": True, "mats_plus": True, "march_ss": True},
    "CFIN": {"march_c": True, "mats_plus": True, "march_ss": True},
    "CFID": {"march_c": True, "mats_plus": False, "march_ss": True},
    "CFST": {"march_c": True, "mats_plus": True, "march_ss": True},
    "CFDS": {"march_c": True, "mats_plus": False, "march_ss": True},
}


def _run_campaign_against_mp_engine(tmp_path: Path, algo_name: str):
    """Mirrors run_algo_campaign's mechanics exactly (golden pass, then one
    run per fault, same plusargs), but pins sources=[fault_ram.sv (num_ports=1),
    march_engine_mp.sv] and top_module="march_engine_mp" directly -- i.e. it
    deliberately bypasses algo_engine._resolve_engine_sources's num_ports
    dispatch so this test exercises march_engine_mp.sv itself, standing on
    its own, not the dispatch logic (which is proven separately by every
    other test in this suite continuing to hit march_engine.sv unmodified).
    """
    engine_dir = find_engine_dir()
    march_mp_sv = engine_dir / "march_engine_mp.sv"
    # march_engine_mp.sv's port bus is hardwired to fault_ram's num_ports=2
    # shape -- see the module docstring above for why this is still a valid
    # "1-port" sanity check (port 1 exists on the bus but is never driven).
    fault_ram_sv = render_and_write(default_registry(), tmp_path / "fault_ram.sv", num_ports=2)

    faults_path = engine_dir / "faults.example.txt"
    records = load_fault_list(faults_path)
    spec = resolve_algo(algo_name)
    mem = MemoryParams(addr_width=8, data_width=8, init_val=1, num_ports=1)

    workdir = tmp_path / f"work_{algo_name}"
    artifact = compile_engine(
        mem, sources=[fault_ram_sv, march_mp_sv], top_module="march_engine_mp", workdir=workdir,
    )

    alg_file = spec.write_numeric(workdir / f"{spec.name}.algc")
    fault_file = _write_fault_list_explicit_ports(records, workdir / "faults.txt")
    plusargs = [f"+INIT={mem.init_val}"]

    golden_out = run_one(artifact, alg_file=alg_file, extra_plusargs=plusargs)
    golden_detected, *_ = parse_result_line(golden_out)
    assert golden_detected is False, f"golden run for {algo_name} unexpectedly DETECTED:\n{golden_out}"

    results = []
    for i, record in enumerate(records):
        out = run_one(artifact, alg_file=alg_file, fault_file=fault_file, index=i, extra_plusargs=plusargs)
        detected, *_ = parse_result_line(out)
        results.append((record, detected))
    return results


@pytest.mark.parametrize("algo_name,expected", REFERENCE_COVERAGE.items())
def test_march_engine_mp_num_ports_1_matches_reference_totals(algo_name: str, expected: tuple[int, int], tmp_path: Path) -> None:
    """march_engine_mp.sv, driven directly in its degenerate num_ports=1 mode,
    must reproduce the OLD engine's exact detected/total counts."""
    results = _run_campaign_against_mp_engine(tmp_path, algo_name)
    detected_count = sum(1 for _rec, det in results if det)
    total = len(results)
    expected_detected, expected_total = expected
    assert total == expected_total
    assert detected_count == expected_detected


@pytest.mark.parametrize("algo_name", ["march_c", "mats_plus", "march_ss"])
def test_march_engine_mp_num_ports_1_matches_reference_per_fault(algo_name: str, tmp_path: Path) -> None:
    """Stronger than totals: the exact same faults must be caught by
    march_engine_mp.sv (num_ports=1, plain non-extended .algc format) as by
    the OLD march_engine.sv -- this is the internal-consistency proof that
    the new engine's do_read/do_write/file-loader logic is correct on its
    own, independent of the (separately-tested) num_ports dispatch."""
    results = _run_campaign_against_mp_engine(tmp_path, algo_name)
    for record, detected in results:
        expected = REFERENCE_PER_FAULT[record.type][algo_name]
        assert detected == expected, f"{record.type} under {algo_name} (march_engine_mp, 1-port): expected {expected}, got {detected}"


def test_run_algo_campaign_num_ports_2_with_default_port_fault(tmp_path: Path) -> None:
    """Regression test for the fault_ram_template.sv.j2 $sscanf bug described
    in this file's module docstring: a plain (vport=0, aport=0) FaultRecord,
    serialized via the real FaultRecord.to_line() (which omits the trailing
    port columns for the default case -- the shorthand every existing caller
    uses), driven through the actual PUBLIC run_algo_campaign() API with
    num_ports=2 -- not compile_engine()/run_one() bypassed directly, unlike
    every other test in this file. Before the fix, $sscanf's 9-item format
    request against this 7-field line returned -1 under Verilator, so the
    line was silently dropped and this exact call would report total=0."""
    from autombist.algo_engine import run_algo_campaign

    mem = MemoryParams(addr_width=3, data_width=4, init_val=1, num_ports=2)
    spec = resolve_algo("march_c")
    faults = [FaultRecord(type="SA0", vaddr=1, vbit=0)]  # vport=aport=0 (default)

    result = run_algo_campaign(mem, spec, faults, sim="verilator", workdir=tmp_path / "work")

    assert result.golden_clean is True
    assert result.total == 1, "the plain 7-field fault line was silently dropped (sscanf bug regressed)"
    assert result.detected == 1
