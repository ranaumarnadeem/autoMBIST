from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import find_engine_dir, parse_alg, resolve_algo  # noqa: E402
from autombist.algo_engine import FaultRecord, MemoryParams, load_fault_list, run_algo_campaign  # noqa: E402
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


# ---------------------------------------------------------------------------
# Dynamic (2-operation) faults: real dual-file differential -- the hand-
# written twin (fault_ram_sv=None, the engine/ path every FaultPrimitive-DSL
# campaign uses by default) and the generated file must agree, not just both
# compile. Each type gets a probe hand-targeted at its own sensitize.prev
# token, with a double read so DRDF's deceptive first read is still observed.
# ---------------------------------------------------------------------------

_DYN_PROBES = {
    "0w0": "either w0 w0 r0 r0",
    "0w1": "either w0 w1 r1 r1",
    "1w0": "either w1 w0 r0 r0",
    "1w1": "either w1 w1 r1 r1",
}
_DYN_PRIMITIVES = [p for p in default_registry() if p.sensitize.prev != "x"]


@pytest.mark.parametrize("prim", _DYN_PRIMITIVES, ids=[p.name for p in _DYN_PRIMITIVES])
def test_dynamic_fault_detected_on_twin_and_generated(prim, generated_fault_ram: Path) -> None:
    spec = parse_alg(_DYN_PROBES[prim.sensitize.prev], f"probe_{prim.name}")
    fault = [FaultRecord(type=prim.name, vaddr=7, vbit=4)]
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)

    twin = run_algo_campaign(mem, spec, fault, fault_ram_sv=None)
    generated = run_algo_campaign(mem, spec, fault, fault_ram_sv=generated_fault_ram)

    assert twin.golden_clean and generated.golden_clean
    assert twin.detected == 1, f"{prim.name}: twin escaped its own targeted probe"
    assert generated.detected == 1, f"{prim.name}: generated escaped its own targeted probe"


def test_dynamic_fault_activates_exactly_once_not_per_matching_read(generated_fault_ram: Path) -> None:
    """The plan's own critique finding: DYN_RDF00/DYN_DRDF00 can't distinguish
    ordering (their own force_read masks a second, non-qualifying read).
    DYN_IRF00 (corrupt_read, no storage mutation) can: w0, w0, r0, r0 --
    only the FIRST read's own immediately-preceding op is the qualifying
    write; the second read's own immediately-preceding op is the FIRST
    read, not the write, so it must not also activate."""
    out = generated_fault_ram
    spec = parse_alg("either w0 w0 r0 r0", "ordering_probe")
    fault = [FaultRecord(type="DYN_IRF00", vaddr=7, vbit=4)]
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)

    for fault_ram_sv in (None, out):
        result = run_algo_campaign(mem, spec, fault, fault_ram_sv=fault_ram_sv, verbose=True)
        assert result.faults[0].activations == 1
