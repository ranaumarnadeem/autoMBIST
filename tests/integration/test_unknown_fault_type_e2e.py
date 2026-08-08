from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import resolve_algo  # noqa: E402
from autombist.algo_engine import CampaignError, FaultRecord, MemoryParams, run_algo_campaign  # noqa: E402
from autombist.fault_primitives import default_registry  # noqa: E402
from autombist.fault_ram_gen import render_and_write  # noqa: E402

# Regression for the "three-FATAL cascade" bug: $finish does not synchronously
# abort fault_ram.sv's initial block, so an unknown fault type used to fall
# through into a second, misleading "FAULT_INDEX out of range" FATAL (and a
# third, garbage FAULT_LOADED print) instead of stopping at the one real
# error. Covers both the hand-written single-port engine and the num_ports=2
# rendered engine, which had the identical bug.


def _run_with_bogus_first_fault(mem: MemoryParams, fault_ram_sv: Path | None = None) -> str:
    spec = resolve_algo("march_ss")
    faults = [
        FaultRecord("NOT_A_REAL_TYPE", vaddr=0, vbit=0, aaddr=0, abit=0, p0=0, p1=0),
        FaultRecord("SA0", vaddr=1, vbit=0, aaddr=0, abit=0, p0=0, p1=0),
    ]
    with pytest.raises(CampaignError) as excinfo:
        run_algo_campaign(mem, spec, faults, max_workers=1, fault_ram_sv=fault_ram_sv)
    return str(excinfo.value)


def test_unknown_fault_type_single_port_reports_exactly_one_fatal() -> None:
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1, num_ports=1)
    message = _run_with_bogus_first_fault(mem)

    assert message.count("FATAL:") == 1
    assert "unknown fault type 'NOT_A_REAL_TYPE'" in message
    assert "FAULT_INDEX" not in message
    assert "FAULT_LOADED" not in message


def test_unknown_fault_type_single_port_lists_valid_types() -> None:
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1, num_ports=1)
    message = _run_with_bogus_first_fault(mem)

    assert "Valid types:" in message
    for name in ("SA0", "SA1", "TF0", "CFST", "DRF", "HSD"):
        assert name in message


def test_unknown_fault_type_num_ports_2_reports_exactly_one_fatal(tmp_path: Path) -> None:
    fault_ram_sv = render_and_write(default_registry(), tmp_path / "fault_ram.sv", num_ports=2)
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1, num_ports=2)
    message = _run_with_bogus_first_fault(mem, fault_ram_sv=fault_ram_sv)

    assert message.count("FATAL:") == 1
    assert "unknown fault type 'NOT_A_REAL_TYPE'" in message
    assert "Valid types:" in message
    assert "FAULT_INDEX" not in message
    assert "FAULT_LOADED" not in message
