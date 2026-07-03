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
MARCH_2RW_TOP = REPO_ROOT / "rtl" / "march_2rw" / "march_2rw_top.sv"


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


# --------------------------------------------------------------------------- #
# 2-port FSM front (num_ports=2): rtl/march_2rw/march_2rw_top.sv's pinout is
# pin-for-pin the REQUIRED_PORTS_MP contract, so it's reused directly as the
# reference 2-port FSM, exactly mirroring how the single-port tests above
# reuse rtl/march_c/march_c_top.sv. Everything above this point is byte-
# identical to before this phase -- proving the single-port FSM-under-test
# path is genuinely untouched.
# --------------------------------------------------------------------------- #


def test_run_fsm_campaign_2port_golden_and_sa0_detect() -> None:
    """2-port sibling of the P5 acceptance criterion: golden ESCAPED, SA0 DETECTED."""
    sources = gather_sibling_sources(MARCH_2RW_TOP)
    module_name = check_ports(MARCH_2RW_TOP.read_text(encoding="utf-8"), num_ports=2).module_name
    mem = MemoryParams(addr_width=10, data_width=32, init_val=1, num_ports=2)

    result = run_fsm_campaign(mem, sources, module_name, [FaultRecord("SA0", 10, 3, 0, 0, 0, 0)])

    assert result.golden_clean is True
    assert result.total == 1
    assert result.detected == 1
    assert result.faults[0].detected is True


def test_run_fsm_campaign_2port_cross_port_coupling_fault_detected() -> None:
    """A genuine cross-port coupling fault against march_2rw_top's E2 phase
    (CONCURRENT READ/READ to the SAME address on both ports -- see
    march_2rw_algo.sv's header comment): a CFDS fault ("any read disturbs")
    whose AGGRESSOR access is address 9 read via PORT 1 (aport=1) disturbs a
    VICTIM bit at address 10 -- a different word, later read back and found
    wrong. This aggressor condition is keyed to port 1's read specifically
    (fault_ram's write_op/read_op both gate the aggressor match on
    `FQ[i].ap == port`); it can only activate if the shared fault_ram core
    genuinely observes port 1's accesses against the SAME memory array port 0
    later reads from. If openram_shim_mp.sv wrapped two independent fault_ram
    cores instead of one, port 1's read of address 9 would land in a
    completely separate array from the one port 0 (or a later port-1 read)
    observes address 10 through -- so this fault would never have any way to
    reach a queue matched against `mem[]` shared with the victim's own word,
    and FAULT_HITS would show 0 activations. It activates exactly once and
    DETECTS here, which is direct evidence of the shared-core wiring."""
    sources = gather_sibling_sources(MARCH_2RW_TOP)
    module_name = check_ports(MARCH_2RW_TOP.read_text(encoding="utf-8"), num_ports=2).module_name
    mem = MemoryParams(addr_width=10, data_width=32, init_val=1, num_ports=2)

    fault = FaultRecord(type="CFDS", vaddr=10, vbit=0, aaddr=9, abit=0, p0=4, p1=0, vport=0, aport=1)

    result = run_fsm_campaign(mem, sources, module_name, [fault])

    assert result.golden_clean is True
    assert result.total == 1
    assert result.detected == 1
    assert result.faults[0].detected is True


def test_shell_add_fsm_2port_then_run_detects_sa0() -> None:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    for line in [
        "set_memory 10 32 --ports 2",
        f"add_fsm {MARCH_2RW_TOP}",
        "add_fault SA0 10 3",
        "run march_2rw_top",
    ]:
        shell.onecmd(shell.precmd(line))

    out = shell.stdout.getvalue()
    assert "1/1 detected" in out
    result = shell.session.last_results["march_2rw_top"]
    assert result.detected == 1 and result.total == 1
