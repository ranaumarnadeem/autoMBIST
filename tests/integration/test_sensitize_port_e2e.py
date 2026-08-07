"""`sensitize.port` must actually gate the fault in SIMULATION, not just appear
in the rendered text.

The bug this proves fixed: `sensitize.port` was validated by `add_fault_type`
and documented as selecting which physical port sensitizes a fault, but
`fault_ram_gen.py` never read it -- the generated SystemVerilog was byte-identical
for port "0", "1" and "x". A researcher modelling a port-specific defect got a
fault that fired on BOTH ports, so the campaign reported a detection real silicon
would not give: a silently inflated coverage number, which is this tool's core
output.

Asserting on the rendered arm text (as tests/software/test_fault_ram_gen.py does)
shows the clause is emitted; it cannot show the RTL *behaves*. This file runs the
engine.

Design pattern borrowed from test_march_engine_mp_cross_port_coupling.py: same
fault, same memory, only which port the sensitizing op is issued on differs, and
detection must flip accordingly.

Fault under test: a user-defined `write_effect` / `on=victim` primitive with
`sensitize.port="1"` -- "a write to this cell via port 1 forces the bit to 0".
`on=victim` is deliberate: it is the site with NO pre-existing port gate, so a
detection here can only come from the new clause. (The aggressor site already had
`FQ[i].ap != port`, which would muddy the attribution.)

Concrete site: AW=4 (16 words), DW=4, victim = (addr 5, bit 1). Algorithm:

    either w0       -- init every word to 0
    up   w1[.PORT]  -- write 1 to every word, ascending, on PORT
    up   r1         -- read every word expecting 1, on port 0

At address 5 the write is 0 -> 1, matching the primitive's pre=0/written=1
condition. If PORT matches sensitize.port the effect forces the stored bit to 0,
and the read element then sees 0 where 1 was expected: DETECTED. If PORT does not
match, the write lands normally and the read is satisfied: ESCAPED.

Two scenarios plus a negative control on the fix itself:
  1. write issued on port 1 == sensitize.port -> DETECTED
  2. write issued on port 0 != sensitize.port -> ESCAPED  (the decisive one:
     under the old, inert behaviour this ALSO reported DETECTED)
  3. the same pair re-run against a deliberately port-stripped render, which must
     reproduce the old both-ports-fire behaviour -- proving these tests can fail.
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
from autombist.fault_primitives import Effect, FaultPrimitive, Sensitize, default_registry  # noqa: E402
from autombist.fault_ram_gen import render_fault_ram  # noqa: E402

AW = 4
DW = 4
VADDR, VBIT = 5, 1
TYPE_NAME = "PORTW"


def _port_primitive(port: str) -> FaultPrimitive:
    """write_effect/on=victim: the site with no pre-existing port gate, so any
    port-sensitivity observed can only come from sensitize.port."""
    return FaultPrimitive(
        TYPE_NAME, "write_effect",
        Sensitize(pre="0", written="1", on="victim", port=port),
        Effect(kind="force", value="0"),
    )


def _alg_text(write_port: int) -> str:
    w1 = "w1" if write_port == 0 else f"w1.{write_port}"
    return f"either w0\nup   {w1}\nup   r1\n"


def _run(tmp_path: Path, label: str, *, write_port: int, strip_port_clause: bool = False) -> bool:
    """Render a fault_ram.sv carrying the port-qualified type, run the algorithm
    whose write element is issued on `write_port`, return whether it DETECTED.

    strip_port_clause removes the emitted `&& port == N` from the generated
    module -- the negative control, reproducing the pre-fix inert behaviour.
    """
    engine_dir = find_engine_dir()
    workdir = tmp_path / label
    workdir.mkdir(parents=True, exist_ok=True)

    text = render_fault_ram(default_registry() + [_port_primitive("1")], num_ports=2)
    assert "&& port == 1" in text, "the port clause must be emitted, or this test proves nothing"
    if strip_port_clause:
        text = text.replace("&& port == 1", "")
    fault_ram_sv = workdir / "fault_ram.sv"
    fault_ram_sv.write_text(text, encoding="utf-8")

    mem = MemoryParams(addr_width=AW, data_width=DW, init_val=0, num_ports=2)
    artifact = compile_engine(
        mem,
        sources=[fault_ram_sv, engine_dir / "march_engine_mp.sv"],
        top_module="march_engine_mp",
        workdir=workdir,
    )

    spec = parse_alg(_alg_text(write_port), label)
    alg_file = spec.write_numeric(workdir / f"{label}.algc")

    fault = FaultRecord(type=TYPE_NAME, vaddr=VADDR, vbit=VBIT, aaddr=0, abit=0, p0=0, p1=0)
    fault_file = workdir / "faults.txt"
    fault_file.write_text(
        f"{TYPE_NAME} {VADDR} {VBIT} 0 0 0 0 0 0\n", encoding="ascii"
    )

    plusargs = [f"+INIT={mem.init_val}"]
    golden = run_one(artifact, alg_file=alg_file, extra_plusargs=plusargs)
    assert parse_result_line(golden)[0] is False, f"[{label}] golden run DETECTED:\n{golden}"

    out = run_one(artifact, alg_file=alg_file, fault_file=fault_file, index=0,
                  extra_plusargs=plusargs)
    return parse_result_line(out)[0]


def test_fault_fires_on_the_port_it_names(tmp_path: Path) -> None:
    assert _run(tmp_path, "match", write_port=1) is True, (
        "a sensitize.port='1' fault did NOT fire when the sensitizing write was "
        "issued on port 1 -- the port clause is over-constraining"
    )


def test_fault_does_not_fire_on_the_other_port(tmp_path: Path) -> None:
    """The decisive assertion. Under the old inert behaviour this reported
    DETECTED too -- the fault fired on both ports, inflating coverage."""
    assert _run(tmp_path, "mismatch", write_port=0) is False, (
        "a sensitize.port='1' fault fired when the sensitizing write was issued "
        "on port 0 -- sensitize.port is not gating, which is exactly the bug "
        "that silently inflated the reported coverage number"
    )


def test_negative_control_stripping_the_port_clause_restores_the_bug(tmp_path: Path) -> None:
    """Proof these tests can fail: delete the emitted `&& port == 1` from the
    generated module and the port-0 case must go back to firing. If this passed
    with the clause stripped, the pair above would be measuring something else."""
    assert _run(tmp_path, "nc_match", write_port=1, strip_port_clause=True) is True
    assert _run(tmp_path, "nc_mismatch", write_port=0, strip_port_clause=True) is True, (
        "with the port clause stripped the fault should fire on BOTH ports "
        "(the pre-fix behaviour) -- it did not, so these tests are not actually "
        "sensitive to the clause they claim to verify"
    )
