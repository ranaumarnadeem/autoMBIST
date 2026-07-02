from __future__ import annotations

import io
import json
import shutil
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import resolve_algo  # noqa: E402
from autombist.algo_engine import FaultRecord, MemoryParams, run_algo_campaign  # noqa: E402
from autombist.algo_shell import AlgoShell, Session  # noqa: E402
from autombist.fault_primitives import Effect, FaultPrimitive, Sensitize, default_registry, validate  # noqa: E402
from autombist.fault_ram_gen import render_and_write  # noqa: E402

# A user-defined parametric read-corruption fault: p0 = trigger value (the
# bit's true value that sensitizes it), p1 = the value returned instead. With
# p0=0, p1=1 this is semantically identical to the built-in IRF0.
IRFP_SPEC = {
    "name": "IRFP",
    "category": "read_effect",
    "sensitize": {"pre": "p0"},
    "effect": {"kind": "corrupt_read", "value": "p1"},
}


def test_custom_type_end_to_end_matches_equivalent_builtin() -> None:
    """Proves add_fault_type's generated arm is live-wired into a real
    campaign (not just syntactically present in the .sv text): a custom type
    parameterized to IRF0's exact semantics must be detected exactly where
    IRF0 itself is (march_ss catches it, per the reference table)."""
    prim = FaultPrimitive(
        name="IRFP", category="read_effect",
        sensitize=Sensitize(pre="p0"), effect=Effect(kind="corrupt_read", value="p1"),
    )
    registry = default_registry()
    validate(prim, existing_names={p.name for p in registry})
    registry.append(prim)

    mem = MemoryParams(addr_width=8, data_width=8, init_val=1)
    spec = resolve_algo("march_ss")

    with tempfile.TemporaryDirectory(prefix="autombist-customtype-") as td:
        fault_ram = render_and_write(registry, Path(td) / "fault_ram.sv")
        assert "T_IRFP" in fault_ram.read_text(encoding="utf-8")

        irfp_as_irf0 = FaultRecord("IRFP", vaddr=5, vbit=0, aaddr=0, abit=0, p0=0, p1=1)
        result = run_algo_campaign(mem, spec, [irfp_as_irf0], fault_ram_sv=fault_ram)

    assert result.golden_clean is True
    assert result.detected == 1


def test_shell_add_fault_type_registers_and_runs() -> None:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    for line in [
        "set_memory 8 8",
        f"add_fault_type {json.dumps(IRFP_SPEC)}",
        "add_fault IRFP 5 0 0 0 0 1",
        "run march_ss",
    ]:
        shell.onecmd(shell.precmd(line))

    out = shell.stdout.getvalue()
    assert any(p.name == "IRFP" for p in shell.session.registry)
    assert "1/1 detected" in out, out


def test_shell_add_fault_type_rejects_bad_category() -> None:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    bad = json.dumps({"name": "MYBAD", "category": "addr_decoder", "effect": {"kind": "force", "value": "0"}})
    shell.onecmd(f"add_fault_type {bad}")
    out = shell.stdout.getvalue()
    assert "error:" in out
    assert not any(p.name == "MYBAD" for p in shell.session.registry)
