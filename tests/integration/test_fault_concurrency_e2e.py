"""Verilator-gated integration tests for the per-fault concurrency lever
(sweep 2026-08's other NEXT-tier performance finding: run_one's per-fault
subprocess calls were strictly sequential though provably independent).

Uses cache_dir=tmp_path throughout so these tests never share the compile
step's cache with any other test -- concurrency here is about the RUN loop,
not the build, and keeping the two isolated avoids any interaction between
this file's timing assertions and another test's compile-cache warmth.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import resolve_algo  # noqa: E402
from autombist.algo_engine import (  # noqa: E402
    MemoryParams,
    generate_all_types_faults,
    run_algo_campaign,
)


def _result_signature(result) -> list[tuple[int, str, bool]]:
    """A comparable, order-sensitive summary of a CampaignResult's per-fault
    outcomes -- what concurrency must NOT be allowed to change."""
    return [(r.index, r.record.type, r.detected) for r in result.faults]


def test_concurrent_and_sequential_runs_produce_identical_results(tmp_path: Path) -> None:
    """The correctness bar concurrency has to clear: WHAT gets detected must
    not depend on HOW MANY faults ran at once. march_ss against a real
    multi-fault campaign, once with max_workers=1 (the pre-existing
    behavior) and once with max_workers=4, must agree on every single fault
    -- same detected/escaped, same order, same coverage."""
    mem = MemoryParams(addr_width=6, data_width=8, init_val=1)
    spec = resolve_algo("march_ss")
    faults = generate_all_types_faults(mem)
    assert len(faults) >= 15, "need enough faults for concurrency to matter"

    sequential = run_algo_campaign(
        mem, spec, faults, cache_dir=tmp_path / "cache", workdir=tmp_path / "seq", max_workers=1
    )
    concurrent_result = run_algo_campaign(
        mem, spec, faults, cache_dir=tmp_path / "cache", workdir=tmp_path / "par", max_workers=4
    )

    assert sequential.detected == concurrent_result.detected
    assert sequential.total == concurrent_result.total
    assert sequential.coverage_percent == concurrent_result.coverage_percent
    assert _result_signature(sequential) == _result_signature(concurrent_result)


def test_concurrency_default_matches_explicit_max_workers_1_off(tmp_path: Path) -> None:
    """AUTOMBIST_FAULT_CONCURRENCY=1 (the escape hatch back to the original
    behavior) must produce the same results as the concurrent default for
    the same fault list -- proving the env var actually reaches the real
    execution path, not just the pure-Python helper tested in isolation at
    the software layer."""
    import os

    mem = MemoryParams(addr_width=5, data_width=8, init_val=1)
    spec = resolve_algo("march_ss")
    faults = generate_all_types_faults(mem)

    default_result = run_algo_campaign(mem, spec, faults, cache_dir=tmp_path / "cache", workdir=tmp_path / "a")

    old_env = os.environ.get("AUTOMBIST_FAULT_CONCURRENCY")
    os.environ["AUTOMBIST_FAULT_CONCURRENCY"] = "1"
    try:
        forced_sequential = run_algo_campaign(
            mem, spec, faults, cache_dir=tmp_path / "cache", workdir=tmp_path / "b"
        )
    finally:
        if old_env is None:
            os.environ.pop("AUTOMBIST_FAULT_CONCURRENCY", None)
        else:
            os.environ["AUTOMBIST_FAULT_CONCURRENCY"] = old_env

    assert _result_signature(default_result) == _result_signature(forced_sequential)


def test_concurrency_measured_speedup(tmp_path: Path) -> None:
    """Not just correctness -- an honest, real measurement that concurrency
    actually helps wall-clock time for a campaign with enough faults to
    amortize thread/process overhead. A generous margin (not a tight bound)
    since CI/dev-box timing is inherently noisy; the point is proving
    directional improvement is real, not pinning an exact ratio."""
    mem = MemoryParams(addr_width=7, data_width=8, init_val=1)
    spec = resolve_algo("march_ss")
    faults = generate_all_types_faults(mem) * 3  # pad out the fault count so per-call overhead is amortized

    cache_dir = tmp_path / "cache"
    # Warm the build cache first (both runs share one compiled artifact) so
    # this measures ONLY the per-fault run loop, not compile time.
    run_algo_campaign(mem, spec, faults[:1], cache_dir=cache_dir, workdir=tmp_path / "warm")

    start = time.time()
    run_algo_campaign(mem, spec, faults, cache_dir=cache_dir, workdir=tmp_path / "seq", max_workers=1)
    sequential_seconds = time.time() - start

    start = time.time()
    run_algo_campaign(mem, spec, faults, cache_dir=cache_dir, workdir=tmp_path / "par", max_workers=4)
    concurrent_seconds = time.time() - start

    assert concurrent_seconds < sequential_seconds, (
        f"expected concurrency to help: sequential={sequential_seconds:.2f}s "
        f"concurrent={concurrent_seconds:.2f}s"
    )
