from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.algo_engine import FaultRecord, MemoryParams, run_fsm_campaign  # noqa: E402
from autombist.algo_shell import AlgoShell, Session  # noqa: E402
from autombist.fsm_harness import check_ports, gather_sibling_sources  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MARCH_C_TOP = REPO_ROOT / "rtl" / "march_c" / "march_c_top.sv"


def test_run_fsm_campaign_golden_and_sa0_detect() -> None:
    """The stated P5 acceptance criterion: golden run ESCAPED, an SA0 fault DETECTED."""
    sources = gather_sibling_sources(MARCH_C_TOP)
    module_name = check_ports(MARCH_C_TOP.read_text(encoding="utf-8")).module_name
    mem = MemoryParams(addr_width=10, data_width=32, init_val=1)

    result = run_fsm_campaign(mem, sources, module_name, [FaultRecord("SA0", 10, 3, 0, 0, 0, 0)])

    assert result.golden_clean is True
    assert result.total == 1
    assert result.detected == 1
    assert result.faults[0].detected is True
    # FSM front: bist_fail only. elem/op/addr are fixed 0 placeholders (the
    # RESULT grammar requires numeric fields) -- not real attribution.
    assert (result.faults[0].elem, result.faults[0].op, result.faults[0].addr) == (0, 0, 0)


def test_shell_add_fsm_then_run_detects_sa0() -> None:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    for line in [
        "set_memory 10 32",
        f"add_fsm {MARCH_C_TOP}",
        "add_fault SA0 10 3",
        "run march_c_top",
    ]:
        shell.onecmd(shell.precmd(line))

    out = shell.stdout.getvalue()
    assert "1/1 detected" in out
    result = shell.session.last_results["march_c_top"]
    assert result.detected == 1 and result.total == 1
