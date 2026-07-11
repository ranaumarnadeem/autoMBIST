"""Pin the diagnosis fail-bitmap's (addr, bit) coordinate accuracy.

This is the load-bearing input a future BIRA (redundancy analysis) step consumes:
the set of DEFECTIVE cells the BIST observed failing, keyed by exact address and
bit. The bug most likely to silently corrupt that input is the MSB-first `xor`
decode (the RTL prints det_xor with %b, which is MSB-first -- string position i
is bit width-1-i, NOT bit i). These pure-Python tests fix the decode and the
resulting observation-cell coordinates; the end-to-end proof against a real
Verilator campaign is in tests/integration/test_diagnosis_fail_coordinates_e2e.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.algo_engine import CampaignResult, FaultRecord, FaultResult, MemoryParams  # noqa: E402
from autombist.algo_reporting import _decode_xor_bits, build_diagnosis_cells  # noqa: E402


def _xor_str(data_width: int, *bits: int) -> str:
    """Build the RTL-shaped det_xor string (MSB-first) with the given bit
    indices set -- exactly what march_engine prints via %b for those bits."""
    chars = ["0"] * data_width
    for b in bits:
        chars[data_width - 1 - b] = "1"  # bit b lives at string position width-1-b
    return "".join(chars)


# --------------------------------------------------------------------------- #
# The MSB-first decode itself
# --------------------------------------------------------------------------- #
def test_decode_xor_is_msb_first() -> None:
    # engine/README.md's own worked example: 8-bit, xor=00000100 -> bit 2.
    assert _decode_xor_bits("00000100") == [2]
    # bit 0 is the RIGHTMOST char; bit width-1 is the LEFTMOST.
    assert _decode_xor_bits("00000001") == [0]
    assert _decode_xor_bits("10000000") == [7]
    # multiple bits, returned high-to-low as decoded left-to-right.
    assert set(_decode_xor_bits("10000001")) == {0, 7}
    assert _decode_xor_bits("00000000") == []


def test_xor_helper_roundtrips_through_decode() -> None:
    for dw in (4, 8, 16, 32):
        for b in (0, 1, dw // 2, dw - 1):
            assert _decode_xor_bits(_xor_str(dw, b)) == [b]


# --------------------------------------------------------------------------- #
# Observation cell coordinates in the diagnosis table
# --------------------------------------------------------------------------- #
def _result(mem: MemoryParams, faults: list[FaultResult]) -> CampaignResult:
    detected = sum(1 for f in faults if f.detected)
    return CampaignResult(
        algo_name="march_c", mem=mem, golden_clean=True, faults=faults,
        detected=detected, total=len(faults),
        coverage_percent=100.0 * detected / len(faults) if faults else 100.0,
        build_seconds=0.0, run_seconds=0.0, sim="verilator",
    )


def _obs_cells(cells: list[dict]) -> dict[tuple[int, int], int]:
    """{(addr, bit): times_observed_mismatch} for cells the BIST OBSERVED
    failing -- the fail bitmap BIRA would consume."""
    return {
        (c["addr"], c["bit"]): c["times_observed_mismatch"]
        for c in cells
        if c["times_observed_mismatch"] > 0
    }


def test_stuck_at_observation_cell_is_exact_injected_coordinate() -> None:
    """A stuck-at defect at (addr, bit) must surface as an observation cell at
    exactly (addr, bit) -- same address, same bit, no endianness skew."""
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)
    # Detected SA0 injected at (vaddr=5, vbit=2); observed failing at addr 5,
    # xor bit 2 set (MSB-first string).
    fr = FaultResult(
        index=0, record=FaultRecord("SA0", 5, 2, 0, 0, 0, 0),
        detected=True, elem=1, op=0, addr=5, xor=_xor_str(8, 2),
    )
    cells = build_diagnosis_cells(_result(mem, [fr]))
    obs = _obs_cells(cells)
    assert obs == {(5, 2): 1}, obs
    # And the injection site is the same cell -> role "both".
    both = [c for c in cells if c["addr"] == 5 and c["bit"] == 2]
    assert len(both) == 1 and both[0]["role"] == "both"


def test_high_bit_defect_is_not_mirrored_to_low_bit() -> None:
    """The endianness trap: a defect at bit 7 must NOT show up at bit 0 (or
    width-1-7). This is exactly the failure a naive left-to-right decode causes."""
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)
    fr = FaultResult(
        index=0, record=FaultRecord("SA1", 9, 7, 0, 0, 0, 0),
        detected=True, elem=2, op=0, addr=9, xor=_xor_str(8, 7),
    )
    obs = _obs_cells(build_diagnosis_cells(_result(mem, [fr])))
    assert obs == {(9, 7): 1}, obs
    assert (9, 0) not in obs  # would be the bug


def test_multiple_defects_each_pinned_independently() -> None:
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)
    faults = [
        FaultResult(0, FaultRecord("SA0", 3, 0, 0, 0, 0, 0), True, 1, 0, 3, _xor_str(8, 0)),
        FaultResult(1, FaultRecord("SA0", 5, 2, 0, 0, 0, 0), True, 1, 0, 5, _xor_str(8, 2)),
        FaultResult(2, FaultRecord("SA1", 10, 7, 0, 0, 0, 0), True, 2, 0, 10, _xor_str(8, 7)),
    ]
    obs = _obs_cells(build_diagnosis_cells(_result(mem, faults)))
    assert obs == {(3, 0): 1, (5, 2): 1, (10, 7): 1}, obs


def test_address_decoder_fault_separates_injection_from_observation() -> None:
    """BIRA repairs the OBSERVED failing address, which is not always the
    injected one: an address-decoder fault is injected at vaddr but read back
    wrong at a different address. The report must keep them distinct."""
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)
    fr = FaultResult(
        index=0, record=FaultRecord("AF_ALIAS", 9, 0, 10, 0, 0, 0),
        detected=True, elem=1, op=0, addr=10, xor=_xor_str(8, 0),
    )
    cells = build_diagnosis_cells(_result(mem, [fr]))
    by_cell = {(c["addr"], c["bit"]): c for c in cells}
    assert by_cell[(9, 0)]["role"] == "injection"     # where the fault was placed
    assert by_cell[(10, 0)]["role"] == "observation"  # where the BIST saw it fail
    assert _obs_cells(cells) == {(10, 0): 1}


def test_escaped_defect_has_no_observation_cell() -> None:
    """An undetected fault contributes no observation coordinate -- BIRA only
    ever sees what the BIST actually caught."""
    mem = MemoryParams(addr_width=4, data_width=8, init_val=1)
    fr = FaultResult(
        index=0, record=FaultRecord("SOF", 7, 4, 0, 0, 0, 0),
        detected=False, elem=None, op=None, addr=None, xor=None,
    )
    cells = build_diagnosis_cells(_result(mem, [fr]))
    assert _obs_cells(cells) == {}
    # It still appears as an injection site (a fail-bitmap must not hide escapes).
    inj = [c for c in cells if c["addr"] == 7 and c["bit"] == 4]
    assert len(inj) == 1 and inj[0]["role"] == "injection"
