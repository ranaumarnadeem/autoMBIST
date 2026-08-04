"""Workstream M.1 end-to-end proof: COLUMN repair, and row+column together.

The 2D sibling of test_repair_loop_e2e.py, holding the same discipline -- every
repair value is COMPUTED from a real fail scan through repair.bira.analyze() +
repair.bisr.encode_repair(); nothing is hardcoded except the injected defects
themselves (the DUTs' baked-in DEFECT_* parameters, which stand in for "an
unknown manufacturing defect" from BIRA's point of view).

What makes the decisive test decisive: the DUT's two defects sit on the SAME BIT
in DIFFERENT ROWS, so a single spare row provably cannot cover them. The test
asserts BOTH halves of that -- the row-only geometry returns Unrepairable, and
the 2D geometry returns a pure COLUMN solution -- before driving it. Without the
paired assertion a mixed-bit defect pair would also pass with two spare rows,
and the test would prove nothing about the column path.

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
    encode_repair,
)
from autombist.runner import run_simulation  # noqa: E402

ADDR_WIDTH = 2
DATA_WIDTH = 4

# Matches sram_spares_col_tiny.v's baked-in defects: the SAME bit (3) in two
# DIFFERENT rows -- the arrangement that forces a column repair.
DEFECT_A1 = (1, 3)
DEFECT_A2 = (2, 3)
# Matches sram_spares_col2_tiny.v: different rows AND different bits -- the
# arrangement that makes BIRA allocate one spare row AND one spare column.
DEFECT_B1 = (1, 0)
DEFECT_B2 = (2, 3)

COL_PORTS = {
    "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0",
    "we": "web0", "csb": "csb0", "spare_wen": "spare_wen0",
}
# num_spare_rows(1) * addr_width(2) = 2; num_spare_cols(1) * bit_index_width(2) = 2.
COL_REPAIR_PORTS = [
    {"name": "row_repair_en", "width": 1, "dir": "input"},
    {"name": "faulty_row_addr", "width": 2, "dir": "input"},
    {"name": "col_repair_en", "width": 1, "dir": "input"},
    {"name": "faulty_bit", "width": 2, "dir": "input"},
]


def _config(memory_name: str) -> dict:
    return {
        "memory_name": memory_name,
        "wrapper_module_name": f"{memory_name}_mbist",
        "addr_width": ADDR_WIDTH,
        "data_width": DATA_WIDTH,
        "we_active_low": True,
        "ports": COL_PORTS,
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1},
        "repair_ports": COL_REPAIR_PORTS,
    }


def _geo(num_spare_cols: int = 1) -> SpareGeometry:
    return SpareGeometry(
        base_words=1 << ADDR_WIDTH, word_size=DATA_WIDTH,
        num_spare_rows=1, num_spare_cols=num_spare_cols,
    )


def _generate(tmp_path: Path, memory_name: str) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(_config(memory_name), sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / "out", algo="march-c")


def _run(wrapper_path: Path, **make_vars):
    """Re-run the SAME compiled wrapper with different repair values -- they are
    read at RUNTIME by test_repair_2d.py's os.getenv() calls, not baked into the
    compiled RTL, so reusing one generated wrapper is correct, not just faster."""
    return run_simulation(
        wrapper_path.parent,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_repair_2d", **make_vars},
    )


def _observe(wrapper: Path) -> set[tuple[int, int]]:
    """A repair-OFF baseline scan: what the defects actually look like."""
    result = _run(
        wrapper, ROW_REPAIR_EN=0, FAULTY_ROW_ADDR=0,
        COL_REPAIR_EN=0, FAULTY_BIT=0, EXPECTED_BIST_FAIL=1,
    )
    assert result.returncode == 0, result.stdout
    return fail_cells(result.report)


def test_column_repair_fixes_what_a_spare_row_provably_cannot(tmp_path: Path) -> None:
    """THE decisive proof. Two defects on the same bit in different rows:

      * with 1 spare row and NO spare column -> Unrepairable (the contrast that
        makes this test mean something),
      * with 1 spare row and 1 spare column -> a PURE COLUMN solution
        (row_map empty), which then actually repairs the memory.
    """
    wrapper = _generate(tmp_path, "sram_spares_col_tiny")
    observed = _observe(wrapper)
    assert observed == {DEFECT_A1, DEFECT_A2}

    # Contrast: a row-only geometry genuinely cannot cover this.
    row_only = analyze(observed, _geo(num_spare_cols=0))
    assert isinstance(row_only, Unrepairable)
    assert row_only.faulty_rows == (1, 2)

    # 2D: the column is FORCED (bit 3 has row-degree 2 > the row budget of 1).
    solution = analyze(observed, _geo())
    assert isinstance(solution, RepairSolution)
    assert solution.row_map == {}
    assert solution.col_map == {3: 0}

    signature = encode_repair(solution, _geo())
    assert (signature.row_repair_en, signature.faulty_row_addr) == (0, 0)
    assert signature.col_repair_en == 0b1
    assert signature.faulty_bit == 3

    result_on = _run(
        wrapper,
        ROW_REPAIR_EN=signature.row_repair_en,
        FAULTY_ROW_ADDR=signature.faulty_row_addr,
        COL_REPAIR_EN=signature.col_repair_en,
        FAULTY_BIT=signature.faulty_bit,
        EXPECTED_BIST_FAIL=0,
    )
    assert result_on.returncode == 0, result_on.stdout
    assert fail_cells(result_on.report) == set()


def test_row_and_column_repairs_compose(tmp_path: Path) -> None:
    """Coexistence: defects on different rows AND different bits make BIRA
    allocate one of each. The defect at (2, 3) sits on a row the row-remap does
    NOT steer, so its repair genuinely comes from the column path -- and reading
    steered row 1 exercises the SPARE ROW's own SPARE COLUMN, which is the
    composition property the two remaps must have."""
    wrapper = _generate(tmp_path, "sram_spares_col2_tiny")
    observed = _observe(wrapper)
    assert observed == {DEFECT_B1, DEFECT_B2}

    solution = analyze(observed, _geo())
    assert isinstance(solution, RepairSolution)
    assert solution.row_map == {1: 0}   # row 1 -> spare row 0
    assert solution.col_map == {3: 0}   # bit 3 -> spare column 0

    signature = encode_repair(solution, _geo())
    assert signature.row_repair_en == 0b1
    assert signature.faulty_row_addr == 1
    assert signature.col_repair_en == 0b1
    assert signature.faulty_bit == 3

    result_on = _run(
        wrapper,
        ROW_REPAIR_EN=signature.row_repair_en,
        FAULTY_ROW_ADDR=signature.faulty_row_addr,
        COL_REPAIR_EN=signature.col_repair_en,
        FAULTY_BIT=signature.faulty_bit,
        EXPECTED_BIST_FAIL=0,
    )
    assert result_on.returncode == 0, result_on.stdout
    assert fail_cells(result_on.report) == set()


def test_wrong_faulty_bit_does_not_falsely_repair(tmp_path: Path) -> None:
    """Teeth: enabling column repair on the WRONG bit lane (0, not the real
    defect's bit 3) must leave the defect fully visible -- proving the remap
    steers on an exact bit-index match, not merely "is column repair on".
    Also exercises faulty_bit=0 through the real RTL path (every faulty_bit
    value is separately proven exhaustively at module level)."""
    wrapper = _generate(tmp_path, "sram_spares_col_tiny")

    result = _run(
        wrapper, ROW_REPAIR_EN=0, FAULTY_ROW_ADDR=0,
        COL_REPAIR_EN=0b1, FAULTY_BIT=0, EXPECTED_BIST_FAIL=1,
    )
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == {DEFECT_A1, DEFECT_A2}


def test_column_steering_is_physically_distinct_storage(tmp_path: Path) -> None:
    """Runs the steering-distinctness/staleness sequence (see
    tests/hardware/test_repair_col_distinct.py's module docstring) against the
    behavioral DUT. Its own asserts are the verification; returncode == 0 means
    all of them passed."""
    wrapper = _generate(tmp_path, "sram_spares_col_tiny")
    result = run_simulation(
        wrapper.parent,
        extra_make_vars={
            "COCOTB_TEST_MODULES": "test_repair_col_distinct",
            "TARGET_ADDR": 0,   # defect-free row, so the proof isolates steering
            "TARGET_BIT": 3,
        },
    )
    assert result.returncode == 0, result.stdout


def test_2d_unrepairable_is_flagged_with_column_diagnostics() -> None:
    """A fault set no (1 row + 1 col) allocation can cover. Deliberately a pure
    Python assertion, not an RTL run: no TWO-defect set is unrepairable with one
    spare of each (same row -> one row repair; same bit -> one column repair;
    otherwise one of each), so this needs three defects -- and an Unrepairable
    verdict has nothing to drive into the RTL anyway. This is the first place
    the column-side diagnostics (faulty_cols/num_spare_cols) are exercised
    alongside the encoder's refusal to encode them."""
    unrepairable = analyze({(1, 0), (2, 1), (3, 2)}, _geo())
    assert isinstance(unrepairable, Unrepairable)
    assert unrepairable.faulty_rows == (1, 2, 3)
    assert unrepairable.faulty_cols == (0, 1, 2)
    assert unrepairable.num_spare_rows == 1
    assert unrepairable.num_spare_cols == 1

    # The flag has TEETH: a caller cannot accidentally encode a signature from
    # this verdict and drive it as if it were a real repair.
    with pytest.raises(TypeError):
        encode_repair(unrepairable, _geo())  # type: ignore[arg-type]
