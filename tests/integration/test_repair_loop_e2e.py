"""Step-D end-to-end proof: the FULL closed loop, no hardcoded shortcuts.

inject -> observe (fail scan) -> analyze (BIRA) -> encode (BISR) -> apply
(drive the remap) -> re-observe -> clean. Unlike test_repair_row_e2e.py (Step
A's proof that the remap RTL itself works, given an already-known-good, hand-
chosen repair value), every repair value used here is COMPUTED from a REAL
fail-scan through the real repair.bira/repair.bisr code -- nothing is
hardcoded except the injected defects themselves (the DUTs' baked-in DEFECT_*
parameters, which stand in for "an unknown manufacturing defect" from BIRA's
point of view).

Covers the roadmap's DVCon-style verification recipe:
  * the repairable case (post-repair BIST passes),
  * the unrepairable case (correctly flagged, not silently passed),
  * a negative/"teeth" test (a WRONG signature must not accidentally repair),
  * a connectivity check (the computed signature matches the real defect).
Retention/power-gating is explicitly out of scope (no power domains modeled).
Icarus + make gated, same as every other RTL-backed test in this suite.
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
from autombist.generator import generate_from_config  # noqa: E402
from autombist.repair import (  # noqa: E402
    RepairSolution,
    SpareGeometry,
    Unrepairable,
    analyze,
    encode_row_repair,
)
from autombist.runner import run_simulation  # noqa: E402

ADDR_WIDTH = 2
DATA_WIDTH = 4

# Matches sram_spares_tiny.v's baked-in DEFECT_ADDR / DEFECT_BIT (Step A's DUT).
DEFECT_1 = (3, 3)
# Matches sram_spares_tiny_2defect.v's two baked-in defects.
DEFECT_2A = (3, 3)
DEFECT_2B = (1, 1)

BASE_PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}

# One spare row, one defect: repairable.
CONFIG_1DEFECT = {
    "memory_name": "sram_spares_tiny",
    "wrapper_module_name": "sram_spares_tiny_mbist",
    "addr_width": ADDR_WIDTH,
    "data_width": DATA_WIDTH,
    "we_active_low": True,
    "ports": BASE_PORTS,
    "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0},
    "repair_ports": [
        {"name": "row_repair_en", "width": 2, "dir": "input"},
        {"name": "faulty_row_addr", "width": 4, "dir": "input"},
    ],
}

# One spare row, TWO distinct-row defects: genuinely unrepairable by row-only
# allocation (2 faulty rows > 1 spare).
CONFIG_2DEFECT = {
    "memory_name": "sram_spares_tiny_2defect",
    "wrapper_module_name": "sram_spares_tiny_2defect_mbist",
    "addr_width": ADDR_WIDTH,
    "data_width": DATA_WIDTH,
    "we_active_low": True,
    "ports": BASE_PORTS,
    "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0},
    "repair_ports": [
        {"name": "row_repair_en", "width": 1, "dir": "input"},
        {"name": "faulty_row_addr", "width": 2, "dir": "input"},
    ],
}


def _generate(tmp_path: Path, config: dict) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / "out", algo="march-c")


def _run(wrapper_path: Path, **repair_vars: int):
    """Re-run the SAME compiled wrapper with different repair-signature values.
    ROW_REPAIR_EN/FAULTY_ROW_ADDR/EXPECTED_BIST_FAIL are read at RUNTIME by
    test_repair_loop.py's os.getenv() calls, not baked into the compiled RTL --
    so reusing one generated wrapper across several repair values is correct,
    not just an optimization."""
    return run_simulation(
        wrapper_path.parent,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_repair_loop", **repair_vars},
    )


def test_repairable_defect_closes_the_loop(tmp_path: Path) -> None:
    """The full loop, computed end to end: no hardcoded repair value anywhere
    on the Python side -- it comes from a real fail scan through analyze() and
    encode_row_repair()."""
    wrapper = _generate(tmp_path, CONFIG_1DEFECT)
    geo = SpareGeometry(base_words=1 << ADDR_WIDTH, word_size=DATA_WIDTH, num_spare_rows=2)

    result_off = _run(wrapper, ROW_REPAIR_EN=0, FAULTY_ROW_ADDR=0, EXPECTED_BIST_FAIL=1)
    assert result_off.returncode == 0, result_off.stdout
    observed = fail_cells(result_off.report)
    assert observed == {DEFECT_1}

    solution = analyze(observed, geo)
    assert isinstance(solution, RepairSolution)
    signature = encode_row_repair(solution, geo)

    result_on = _run(
        wrapper,
        ROW_REPAIR_EN=signature.row_repair_en,
        FAULTY_ROW_ADDR=signature.faulty_row_addr,
        EXPECTED_BIST_FAIL=0,
    )
    assert result_on.returncode == 0, result_on.stdout
    assert fail_cells(result_on.report) == set()


def test_unrepairable_defects_are_flagged_not_silently_passed(tmp_path: Path) -> None:
    """2 distinct faulty rows, 1 spare: BIRA must report Unrepairable from the
    REAL observed fail-bitmap, and the system must not be able to silently
    proceed as if a repair existed."""
    wrapper = _generate(tmp_path, CONFIG_2DEFECT)
    geo = SpareGeometry(base_words=1 << ADDR_WIDTH, word_size=DATA_WIDTH, num_spare_rows=1)

    result_off = _run(wrapper, ROW_REPAIR_EN=0, FAULTY_ROW_ADDR=0, EXPECTED_BIST_FAIL=1)
    assert result_off.returncode == 0, result_off.stdout
    observed = fail_cells(result_off.report)
    assert observed == {DEFECT_2A, DEFECT_2B}

    result = analyze(observed, geo)
    assert isinstance(result, Unrepairable)
    assert result.faulty_rows == (1, 3)
    assert result.num_spare_rows == 1

    # The flag has TEETH: a caller can't accidentally encode a signature from
    # this verdict and drive it as if it were a real repair.
    with pytest.raises(TypeError):
        encode_row_repair(result, geo)  # type: ignore[arg-type]


def test_wrong_signature_does_not_falsely_repair(tmp_path: Path) -> None:
    """Negative/teeth test: loading SOME repair (row_repair_en=1) that targets
    the WRONG address (row 0, not the real defect at row 3) must leave the
    real defect fully visible -- proving the remap steers on an exact address
    match, not merely "is repair mode on"."""
    wrapper = _generate(tmp_path, CONFIG_1DEFECT)

    result = _run(wrapper, ROW_REPAIR_EN=0b01, FAULTY_ROW_ADDR=0, EXPECTED_BIST_FAIL=1)
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == {DEFECT_1}


def test_connectivity_signature_matches_injected_defect(tmp_path: Path) -> None:
    """The computed signature's bits, decoded, must correspond EXACTLY to the
    real injected defect's address -- not merely "some repairable-looking
    answer". Cross-checks BIRA's analysis against ground truth via BISR's own
    encoding, closing the loop between algorithm and physical bit values."""
    wrapper = _generate(tmp_path, CONFIG_1DEFECT)
    geo = SpareGeometry(base_words=1 << ADDR_WIDTH, word_size=DATA_WIDTH, num_spare_rows=2)

    result_off = _run(wrapper, ROW_REPAIR_EN=0, FAULTY_ROW_ADDR=0, EXPECTED_BIST_FAIL=1)
    observed = fail_cells(result_off.report)
    solution = analyze(observed, geo)
    assert isinstance(solution, RepairSolution)
    signature = encode_row_repair(solution, geo)

    # Exactly one spare in use, and its slice decodes to the real defect row.
    assert signature.row_repair_en == 0b01
    assert signature.faulty_row_addr == DEFECT_1[0]
