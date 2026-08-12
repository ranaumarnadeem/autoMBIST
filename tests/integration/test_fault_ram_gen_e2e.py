from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import find_engine_dir, resolve_algo  # noqa: E402
from autombist.algo_engine import MemoryParams, load_fault_list, run_algo_campaign  # noqa: E402
from autombist.fault_primitives import default_registry  # noqa: E402
from autombist.fault_ram_gen import render_and_write  # noqa: E402

# The P6 acceptance gate: fault_ram.sv, regenerated from the DSL registry,
# must reproduce the hand-written engine's reference table bit-for-bit --
# not just the totals, but which specific faults each algorithm catches.
REFERENCE_COVERAGE = {"march_c": (20, 29), "mats_plus": (13, 29), "march_ss": (28, 29)}

# From engine/README.md "Measured results" table (faults.example.txt, INIT=1):
# fault type -> {algo: DETECTED/ESCAPED}, keyed by the exact instance in the file.
REFERENCE_PER_FAULT = {
    "SA0": {"march_c": True, "mats_plus": True, "march_ss": True},
    "SA1": {"march_c": True, "mats_plus": True, "march_ss": True},
    "TF0": {"march_c": True, "mats_plus": True, "march_ss": True},
    "TF1": {"march_c": True, "mats_plus": True, "march_ss": True},
    "WDF0": {"march_c": False, "mats_plus": False, "march_ss": True},
    "WDF1": {"march_c": False, "mats_plus": False, "march_ss": True},
    "RDF0": {"march_c": True, "mats_plus": True, "march_ss": True},
    "RDF1": {"march_c": True, "mats_plus": True, "march_ss": True},
    "DRDF0": {"march_c": False, "mats_plus": False, "march_ss": True},
    "DRDF1": {"march_c": False, "mats_plus": False, "march_ss": True},
    "IRF0": {"march_c": True, "mats_plus": True, "march_ss": True},
    "IRF1": {"march_c": True, "mats_plus": True, "march_ss": True},
    "SOF": {"march_c": False, "mats_plus": False, "march_ss": False},
    "AF_NOACC": {"march_c": True, "mats_plus": True, "march_ss": True},
    "AF_ALIAS": {"march_c": True, "mats_plus": True, "march_ss": True},
    "CFIN": {"march_c": True, "mats_plus": True, "march_ss": True},
    "CFID": {"march_c": True, "mats_plus": False, "march_ss": True},
    "CFST": {"march_c": True, "mats_plus": True, "march_ss": True},
    "CFDS": {"march_c": True, "mats_plus": False, "march_ss": True},
    "CFTR0": {"march_c": True, "mats_plus": False, "march_ss": True},
    "CFTR1": {"march_c": True, "mats_plus": True, "march_ss": True},
    "CFWD0": {"march_c": False, "mats_plus": False, "march_ss": True},
    "CFWD1": {"march_c": False, "mats_plus": False, "march_ss": True},
    "CFRD0": {"march_c": True, "mats_plus": False, "march_ss": True},
    "CFRD1": {"march_c": True, "mats_plus": False, "march_ss": True},
    "CFIR0": {"march_c": True, "mats_plus": False, "march_ss": True},
    "CFIR1": {"march_c": True, "mats_plus": False, "march_ss": True},
    "CFDRD0": {"march_c": False, "mats_plus": False, "march_ss": True},
    "CFDRD1": {"march_c": False, "mats_plus": False, "march_ss": True},
}


@pytest.fixture()
def generated_fault_ram(tmp_path: Path) -> Path:
    return render_and_write(default_registry(), tmp_path / "fault_ram.sv")


@pytest.mark.parametrize("algo_name,expected", REFERENCE_COVERAGE.items())
def test_generated_fault_ram_matches_reference_totals(
    algo_name: str, expected: tuple[int, int], generated_fault_ram: Path
) -> None:
    faults_path = find_engine_dir() / "faults.example.txt"
    records = load_fault_list(faults_path)
    spec = resolve_algo(algo_name)
    mem = MemoryParams(addr_width=8, data_width=8, init_val=1)

    result = run_algo_campaign(mem, spec, records, fault_ram_sv=generated_fault_ram)

    detected, total = expected
    assert result.total == total
    assert result.detected == detected
    assert result.golden_clean is True


@pytest.mark.parametrize("algo_name", ["march_c", "mats_plus", "march_ss"])
def test_generated_fault_ram_matches_reference_per_fault(algo_name: str, generated_fault_ram: Path) -> None:
    """Stronger than the totals check: the exact same faults must be caught,
    not just the same count (two different fault sets could coincidentally
    sum to the same total)."""
    faults_path = find_engine_dir() / "faults.example.txt"
    records = load_fault_list(faults_path)
    spec = resolve_algo(algo_name)
    mem = MemoryParams(addr_width=8, data_width=8, init_val=1)

    result = run_algo_campaign(mem, spec, records, fault_ram_sv=generated_fault_ram)

    for fault_result in result.faults:
        fault_type = fault_result.record.type
        expected = REFERENCE_PER_FAULT[fault_type][algo_name]
        assert fault_result.detected == expected, f"{fault_type} under {algo_name}: expected {expected}"
