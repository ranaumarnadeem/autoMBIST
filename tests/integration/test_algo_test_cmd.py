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
#
# march_x is 13, not 12: before alg_spec.resolve_directions() unified how
# `either` resolves (previously the engine ran every `either` ascending, while
# the hand-written classic RTL ran a trailing one in the previous element's
# direction), run_algo_campaign measured march_x's trailing `either r0`
# ascending and missed CFDS 130 1 131 1 4 0 (aggressor above the victim),
# which only a descending final read catches. 13/19 is what the algorithm
# actually detects once run in the direction the shipped RTL uses. The other
# three algorithms' totals were re-measured against the same fix and are
# unchanged -- march_c and march_ss also have a trailing `either` that now
# resolves to `down`, but neither one's detected-fault SET moved (confirmed
# fault-by-fault, not just by total), and mats_plus/march_b have no trailing
# `either` at all.
REFERENCE_COVERAGE = {
    "march_c": (14, 19),
    "mats_plus": (12, 19),
    "march_ss": (18, 19),
    "march_x": (13, 19),
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
