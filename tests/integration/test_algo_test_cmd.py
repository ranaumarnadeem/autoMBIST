from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import find_engine_dir, resolve_algo  # noqa: E402
from autombist.algo_engine import MemoryParams, load_fault_list, run_algo_campaign  # noqa: E402

# Reference coverage from src/autombist/engine/README.md "Measured results" table
# (faults.example.txt, INIT=1 defaults).
REFERENCE_COVERAGE = {
    "march_c": (14, 19),
    "mats_plus": (12, 19),
    "march_ss": (18, 19),
    "march_x": (12, 19),
}


@pytest.mark.parametrize("algo_name,expected", REFERENCE_COVERAGE.items())
def test_campaign_matches_reference_table(algo_name: str, expected: tuple[int, int]) -> None:
    faults_path = find_engine_dir() / "faults.example.txt"
    records = load_fault_list(faults_path)
    spec = resolve_algo(algo_name)
    mem = MemoryParams(addr_width=8, data_width=8, init_val=1)

    result = run_algo_campaign(mem, spec, records)

    detected, total = expected
    assert result.total == total
    assert result.detected == detected
    assert result.golden_clean is True
