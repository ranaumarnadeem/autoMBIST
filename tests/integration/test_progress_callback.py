"""Real (non-mocked), Verilator-gated proof that run_algo_campaign's/
run_fsm_campaign's opt-in `progress_callback` (Workstream C-B) actually fires
once per fault with the correct (completed, total) pair -- not just that the
parameter exists and doesn't break anything (already covered by every other
Verilator-gated test, which passes `progress_callback=None`, its default).
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
    load_fault_list,
    run_algo_campaign,
    run_fsm_campaign,
)
from autombist.fsm_harness import check_ports, gather_sibling_sources  # noqa: E402

MARCH_C_TOP = Path(__file__).resolve().parents[2] / "rtl" / "march_c" / "march_c_top.sv"


def test_run_algo_campaign_progress_callback_fires_once_per_fault() -> None:
    faults_path = find_engine_dir() / "faults.example.txt"
    records = load_fault_list(faults_path)
    mem = MemoryParams(addr_width=8, data_width=8, init_val=1)
    spec = resolve_algo("march_c")

    calls: list[tuple[int, int]] = []
    result = run_algo_campaign(
        mem, spec, records, progress_callback=lambda completed, total: calls.append((completed, total))
    )

    assert len(calls) == len(records) == result.total
    assert calls == [(i + 1, len(records)) for i in range(len(records))]


def test_run_fsm_campaign_progress_callback_fires_once_per_fault() -> None:
    records = [FaultRecord("SA0", 3, 0, 0, 0, 0, 0), FaultRecord("SA1", 5, 1, 0, 0, 0, 0)]
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)
    sources = gather_sibling_sources(MARCH_C_TOP)
    module_name = check_ports(MARCH_C_TOP.read_text(encoding="utf-8")).module_name

    calls: list[tuple[int, int]] = []
    result = run_fsm_campaign(
        mem, sources, module_name, records,
        progress_callback=lambda completed, total: calls.append((completed, total)),
    )

    assert calls == [(1, 2), (2, 2)]
    assert result.total == 2
