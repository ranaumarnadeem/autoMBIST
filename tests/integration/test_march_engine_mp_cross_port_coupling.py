"""Cross-port vs same-port coupling differential proof for march_engine_mp.sv
(Phase 5 of the multi-port fault-DSL generalization).

Mirrors the verification PHILOSOPHY of tests/integration/
test_port_coupling_detection.py and tests/hardware/test_port_coupling_diff.py
(a different subsystem entirely -- the classic generate/wrapper march-1r1w
saboteur path -- reused here only as a DESIGN PATTERN, no code shared): same
fault, same memory, only the access-PATTERN differs, and detection must flip
accordingly. Here the "access pattern" is which physical port the aggressor
transition is issued on, versus which port the fault record claims as its
aggressor port (aport).

Fault under test: CFIN (write_effect, on=aggressor, sensitize.transition=p0,
effect=invert) -- an aggressor write TRANSITION on cell (aa, ab) inverts the
victim cell (va, vb). p0=2 means "either direction" (dir_match matches up or
down). vport is not load-bearing for CFIN's own match (only the aggressor
side, ap, is referenced by write_op's aggressor loop -- see
fault_ram_template.sv.j2's `if (FQ[i].aa != ea || FQ[i].ab != b || FQ[i].ap
!= port) continue;`), so vport is fixed at 0 (victim always read via port 0)
across every scenario below and only aport varies.

Concrete site: AW=4 (16 words), DW=4. Victim = (addr 5, bit 1). Aggressor =
(addr 6, bit 1) -- different word, same bit lane (mirrors faults.example.txt's
convention). A tiny 3-element hand-authored algorithm (NOT a built-in --
deliberately purpose-built for this test, exactly as the task calls for):

    either w0            -- init every word to 0 (both cells start at 0)
    up   w1[.PORT]        -- write 1 to every word, ascending address, on PORT
                             (address 5 visited before 6, so by the time
                             address 6's write triggers CFIN, mem[5][1] has
                             already been plainly written to 1 earlier in
                             this SAME element's sweep)
    up   r1               -- read every word expecting 1, on port 0

At address 6 (aggressor), the up-transition write (old=0 -> new=1) matches
CFIN's p0=2 (either) sensitizing condition. If PORT == fault.aport, write_op
inverts mem[victim] = ~mem[victim] IN PLACE the moment that write happens
(fault_ram_gen.render_write_aggressor_arm: `mem[FQ[i].va][FQ[i].vb] =
~mem[FQ[i].va][FQ[i].vb]`) -- so mem[5][1] flips from 1 (just written) back
to 0. The read element then observes 0 where 1 was expected: DETECTED. If
PORT != fault.aport (or PORT is never issued on the aggressor's port at
all), mem[5][1] stays at 1 as plainly written, matching the read's
expectation: ESCAPED.

Three scenarios, one shared fault record (only .aport differs) and one
shared algorithm SHAPE (only which port index PORT the aggressor's "up w1"
element issues on differs):

  1. SAME-PORT (vport=0, aport=0): aggressor write issued on port 0, exactly
     matching aport=0 -- CFIN fires -- must be DETECTED. This is today's
     only pre-Phase-5 mode (same-port coupling), reproduced through the new
     engine.
  2. CROSS-PORT (vport=0, aport=1): aggressor write issued on port 1,
     matching aport=1, while the victim is sensed via a port-0 read -- CFIN
     fires across ports -- must be DETECTED. This is the genuinely new
     capability: proves .aport is load-bearing and cross-port coupling
     detection actually works, not just same-port.
  3. CONTROL (vport=0, aport=1 -- same fault record as scenario 2, i.e. the
     fault list SAYS the aggressor is on port 1) but the algorithm's
     aggressor-write element is changed to issue "up w1" on port 0 ONLY
     (never touches port 1 at all, anywhere in the whole program) -- CFIN
     must NOT fire (FQ[i].ap=1 != port=0 every time) -- must ESCAPE. This is
     the "fault list says cross-port but algorithm never issues it -> the
     fault escapes" proof: if .aport were decorative/ignored (e.g. a bug
     that always matched regardless of port), this control would incorrectly
     report DETECTED.

Fault-list I/O note: this test writes its OWN fault-list file (always
9-field, explicit trailing vport/aport, never the 7-field omitted shape) to
route around a genuine PRE-EXISTING Phase 4 bug in
fault_ram_template.sv.j2's num_ports==2 fault-line $sscanf parsing --
documented in full in test_march_engine_mp_sanity.py's module docstring and
this phase's final report. That bug affects only the 7-field (both ports
omitted/defaulted) shape; every field-count actually used below (9 fields,
every scenario) is unaffected and already parses correctly (independently
probed under Verilator: n == 9 for a full 9-field line).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import find_engine_dir, parse_alg  # noqa: E402
from autombist.algo_engine import (  # noqa: E402
    FaultRecord,
    MemoryParams,
    compile_engine,
    parse_result_line,
    run_one,
)
from autombist.fault_primitives import default_registry  # noqa: E402
from autombist.fault_ram_gen import render_and_write  # noqa: E402

AW = 4
DW = 4
VADDR, VBIT = 5, 1
AADDR, ABIT = 6, 1


def _write_fault_list_explicit_ports(records: list[FaultRecord], path: Path) -> Path:
    """Always emits the trailing vport/aport columns explicitly (9 fields per
    line) -- see this file's module docstring for why (routes around the
    Phase 4 fault_ram_template.sv.j2 $sscanf bug, which affects only the
    7-field omitted-ports shape)."""
    lines = [
        f"{r.type} {r.vaddr} {r.vbit} {r.aaddr} {r.abit} {r.p0} {r.p1} {r.vport} {r.aport}"
        for r in records
    ]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def _alg_text(aggressor_write_port: int | None) -> str:
    """Builds the 3-element algorithm described in the module docstring.
    aggressor_write_port: None -> "up w1" with no port suffix (port 0, the
    plain/non-extended numeric format); 1 -> "up w1.1" (port 1, extended
    numeric format)."""
    w1_tok = "w1" if aggressor_write_port in (None, 0) else f"w1.{aggressor_write_port}"
    return f"""
either w0
up   {w1_tok}
up   r1
"""


def _run_scenario(tmp_path: Path, label: str, *, aport: int, aggressor_write_port: int | None) -> bool:
    """Compiles march_engine_mp.sv against a num_ports=2 fault_ram.sv with a
    single injected CFIN fault (aport as given), runs the algorithm whose
    aggressor-write element issues "up w1" on aggressor_write_port, and
    returns whether the fault was DETECTED."""
    engine_dir = find_engine_dir()
    march_mp_sv = engine_dir / "march_engine_mp.sv"
    workdir = tmp_path / label
    workdir.mkdir(parents=True, exist_ok=True)
    fault_ram_sv = render_and_write(default_registry(), workdir / "fault_ram.sv", num_ports=2)

    mem = MemoryParams(addr_width=AW, data_width=DW, init_val=0, num_ports=2)
    artifact = compile_engine(
        mem, sources=[fault_ram_sv, march_mp_sv], top_module="march_engine_mp", workdir=workdir,
    )

    spec = parse_alg(_alg_text(aggressor_write_port), label)
    alg_file = spec.write_numeric(workdir / f"{label}.algc")

    fault = FaultRecord(
        type="CFIN", vaddr=VADDR, vbit=VBIT, aaddr=AADDR, abit=ABIT,
        p0=2, p1=0, vport=0, aport=aport,
    )
    fault_file = _write_fault_list_explicit_ports([fault], workdir / "faults.txt")

    plusargs = [f"+INIT={mem.init_val}"]

    golden_out = run_one(artifact, alg_file=alg_file, extra_plusargs=plusargs)
    golden_detected, *_ = parse_result_line(golden_out)
    assert golden_detected is False, f"[{label}] golden run unexpectedly DETECTED:\n{golden_out}"

    out = run_one(artifact, alg_file=alg_file, fault_file=fault_file, index=0, extra_plusargs=plusargs)
    detected, *_ = parse_result_line(out)
    return detected


def test_same_port_coupling_is_detected(tmp_path: Path) -> None:
    """SAME-PORT (today's only pre-Phase-5 mode): vport=0, aport=0, aggressor
    write issued on port 0 -- CFIN fires -- must be DETECTED."""
    detected = _run_scenario(tmp_path, "same_port", aport=0, aggressor_write_port=None)
    assert detected is True, (
        "same-port CFIN coupling (aport=0, aggressor write on port 0) was NOT "
        "detected -- march_engine_mp.sv's degenerate same-port behavior must "
        "match today's single-port coupling semantics"
    )


def test_cross_port_coupling_is_detected(tmp_path: Path) -> None:
    """CROSS-PORT (the new capability): vport=0, aport=1, aggressor write
    issued on port 1 while the victim is sensed via a port-0 read -- CFIN
    must fire across ports -- must be DETECTED."""
    detected = _run_scenario(tmp_path, "cross_port", aport=1, aggressor_write_port=1)
    assert detected is True, (
        "cross-port CFIN coupling (aport=1, aggressor write actually issued "
        "on port 1) was NOT detected -- a genuinely concurrent-capable "
        "multi-port algorithm must be able to catch an aggressor disturbance "
        "that only manifests via a different port than the victim's read"
    )


def test_cross_port_fault_escapes_when_algorithm_never_uses_that_port(tmp_path: Path) -> None:
    """CONTROL: the exact same fault record as the cross-port scenario
    (aport=1) but the algorithm's aggressor-write element is changed to
    issue "up w1" on port 0 ONLY -- port 1 is never touched anywhere in the
    whole program. FQ[i].ap (1) != port (always 0) on every write, so CFIN
    never fires: the fault must ESCAPE. This proves .aport is load-bearing,
    not decorative -- if port matching were ignored (or always matched),
    this would incorrectly report DETECTED."""
    detected = _run_scenario(tmp_path, "cross_port_control", aport=1, aggressor_write_port=None)
    assert detected is False, (
        "control FAILED: fault list claims aport=1 but the algorithm never "
        "issues the aggressor transition on port 1 anywhere -- this must "
        "ESCAPE. A DETECTED result here means port matching is not "
        "load-bearing (e.g. always matches regardless of the accessing port)"
    )
