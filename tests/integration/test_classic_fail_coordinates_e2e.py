"""End-to-end proof that the CLASSIC path's observation-derived fail-bitmap pins
the EXACT (addr, bit) of known injected defects.

This is the classic-path counterpart to test_diagnosis_fail_coordinates_e2e.py
(which pinned the algo-shell path). It exercises the real chain a future BIRA
consumes: generate a saboteured wrapper with known stuck-at defects -> run the
opt-in functional-port fail scan (run_simulation(fail_scan=True), which drives
test_mbist.test_fail_scan) -> FAIL_CELL lines -> reporting.parse_fail_bitmap_lines
-> report["fail_bitmap"] -> bira_input.fail_cells. If any link skewed an address
or bit (endianness being the classic trap), or the scan were injection-gated
rather than observing the memory, these tests fail. Icarus + make gated.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("make") is None,
    reason="needs Icarus Verilog + make on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.bira_input import fail_cells  # noqa: E402
from autombist.fault_gen import (  # noqa: E402
    FaultType,
    generate_fault_masks,
    write_fault_files,
)
from autombist.generator import generate_from_config  # noqa: E402
from autombist.runner import run_simulation  # noqa: E402

# The smallest sensible macro (depth 4 x width 4), active-low we, read_latency 1.
ADDR_WIDTH = 2
DATA_WIDTH = 4
CONFIG = {
    "memory_name": "sram_tiny",
    "wrapper_module_name": "sram_tiny_mbist",
    "addr_width": ADDR_WIDTH,
    "data_width": DATA_WIDTH,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}


def _write_config(path: Path) -> None:
    path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")


def _injected_stuck_at_sites(faults: int, seed: int) -> set[tuple[int, int]]:
    """Ground truth: decode the exact (addr, bit) set that generate_fault_masks
    injects for the given (faults, seed) -- SA0 = a bit CLEARED in sa0_words,
    SA1 = a bit SET in sa1_words. generate_from_config below regenerates the
    identical masks from the same seed, so this is what the saboteur applies."""
    sa0, sa1 = generate_fault_masks(
        addr_width=ADDR_WIDTH, data_width=DATA_WIDTH,
        fault_type=FaultType.STUCK_AT, faults=faults, seed=seed,
    )
    all_ones = (1 << DATA_WIDTH) - 1
    sites: set[tuple[int, int]] = set()
    for addr in range(1 << ADDR_WIDTH):
        stuck = ((~sa0[addr]) & all_ones) | (sa1[addr] & all_ones)
        for bit in range(DATA_WIDTH):
            if (stuck >> bit) & 1:
                sites.add((addr, bit))
    return sites


def test_functional_fail_scan_pins_injected_coordinates(tmp_path: Path) -> None:
    """The fail-bitmap must equal EXACTLY the injected stuck-at site set --
    every defect observed at its own (addr, bit), none missing, none spurious."""
    faults, seed = 3, 7
    config_path = tmp_path / "config.yml"
    _write_config(config_path)

    wrapper = generate_from_config(
        config_path, tmp_path / "out",
        use_saboteur=True, faults=faults, fault_seed=seed,
        fault_type="stuck-at", algo="march-c",
    )
    result = run_simulation(wrapper.parent, fail_scan=True)

    assert result.report["status"] == "pass"
    assert "fail_bitmap" in result.report, "fail scan did not attach fail_bitmap"

    observed = fail_cells(result.report)
    expected = _injected_stuck_at_sites(faults, seed)
    assert expected, "test setup error: no faults injected"
    assert observed == expected, f"observed {sorted(observed)} != injected {sorted(expected)}"


def test_no_fail_bitmap_without_fail_scan(tmp_path: Path) -> None:
    """Teeth / opt-in isolation: the SAME injected faults, run WITHOUT fail_scan,
    must produce a report with NO fail_bitmap key -- proving the fail-bitmap is
    surfaced only by the new functional scan, and existing runs are untouched."""
    config_path = tmp_path / "config.yml"
    _write_config(config_path)

    wrapper = generate_from_config(
        config_path, tmp_path / "out",
        use_saboteur=True, faults=3, fault_seed=7,
        fault_type="stuck-at", algo="march-c",
    )
    result = run_simulation(wrapper.parent)  # fail_scan defaults False

    assert "fail_bitmap" not in result.report


def test_high_bit_defect_not_mirrored_to_low_bit(tmp_path: Path) -> None:
    """Endianness guard, end to end: a single SA1 at the HIGH bit of the LAST
    address must surface at exactly (depth-1, width-1) and NOT at bit 0."""
    config_path = tmp_path / "config.yml"
    _write_config(config_path)

    wrapper = generate_from_config(
        config_path, tmp_path / "out",
        use_saboteur=True, faults=1, fault_seed=1,
        fault_type="stuck-at", algo="march-c",
    )

    # Overwrite the seed-generated masks with a single hand-placed SA1 at the
    # top address / top bit. _prepare_fault_files only regenerates masks when
    # they are MISSING, so these crafted files are what the saboteur loads.
    depth = 1 << ADDR_WIDTH
    all_ones = (1 << DATA_WIDTH) - 1
    high_addr, high_bit = depth - 1, DATA_WIDTH - 1
    sa0 = [all_ones] * depth               # no stuck-at-0 anywhere
    sa1 = [0] * depth
    sa1[high_addr] = 1 << high_bit         # one stuck-at-1 at the top bit
    faults_dir = wrapper.parent / "faults"
    write_fault_files(
        outdir=faults_dir, data_width=DATA_WIDTH,
        fault_type=FaultType.STUCK_AT, mask1_words=sa0, mask2_words=sa1,
    )

    result = run_simulation(wrapper.parent, fail_scan=True)
    observed = fail_cells(result.report)

    assert (high_addr, high_bit) in observed
    assert (high_addr, 0) not in observed  # the naive-mirror bug would put it here
    assert observed == {(high_addr, high_bit)}
