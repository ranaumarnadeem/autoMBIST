"""Pin the spare-COLUMN store semantics of the spare-augmented memory model
(Workstream M.1b) -- the highest-severity silent-bug risk in column repair.

If the logical-word store in `rtl/sram_model_spares.sv` (and its DUT clone
`tests/hardware/sram_spares_col_tiny.v`) were a whole-word store rather than a
part-select to `[DATA_WIDTH-1:0]`, the spare lanes would be written straight
from `din0`'s top bits, BYPASSING `spare_wen0` entirely. The behavioral model
would then pass column-repair tests that a real OpenRAM macro -- which gates its
spare column strictly on `spare_wen0`
(`flow/multimem/sky130_sram_32b512w.v:78-79`) -- would fail. Nothing else in the
suite would catch that, which is why this probe exists as its own pinned test.

Verified to have teeth: reverting the part-select to a whole-word store makes
`test_spare_column_store_semantics` fail with the spare lane clobbered to 0.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="needs Icarus Verilog (iverilog + vvp) on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_SV = REPO_ROOT / "tests" / "hardware" / "sram_spares_col_probe.sv"
DUT_SV = REPO_ROOT / "tests" / "hardware" / "sram_spares_col_tiny.v"
REFERENCE_SV = REPO_ROOT / "rtl" / "sram_model_spares.sv"

# (label, dut source, extra iverilog defines). The REFERENCE case is the one
# that matters most for drift: nothing else in the suite ever exercises
# rtl/sram_model_spares.sv's spare-column logic. Its only other coverage is an
# elaborate-only check at DEFAULT parameters (NUM_SPARE_COLS = 0), where that
# entire path is dead code -- so without this the reference could rot silently
# while every clone-based test stayed green.
_DUTS = [
    ("clone", DUT_SV, []),
    ("reference", REFERENCE_SV, ["-DDUT_MODULE=sram_model_spares", "-DDUT_NEEDS_DEFECTS"]),
]


@pytest.mark.parametrize("label,dut_sv,defines", _DUTS, ids=[d[0] for d in _DUTS])
def test_spare_column_store_semantics(
    tmp_path: Path, label: str, dut_sv: Path, defines: list[str]
) -> None:
    exe = tmp_path / f"probe_{label}"
    build = subprocess.run(
        ["iverilog", "-g2012", *defines, "-o", str(exe), str(PROBE_SV), str(dut_sv)],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, f"iverilog failed:\n{build.stdout}\n{build.stderr}"

    run = subprocess.run(
        ["vvp", str(exe)], capture_output=True, text=True, check=False, timeout=300,
    )
    out = run.stdout + run.stderr
    assert "MISMATCH" not in out, out
    assert "PROBE FAIL" not in out, out
    assert "PROBE PASS" in out, out


def test_spare_column_dut_defect_knobs_target_the_same_bit() -> None:
    """The column DUT's whole point is TWO defects on the SAME bit in DIFFERENT
    rows -- that is what makes a single spare ROW insufficient and forces BIRA to
    allocate the spare COLUMN. A future edit that quietly moves one defect to a
    different bit would make the 2D e2e pass for the wrong reason (two spare rows
    would also cover it), so pin the intent here."""
    text = DUT_SV.read_text(encoding="utf-8")
    assert "parameter integer DEFECT_ADDR    = 1," in text
    assert "parameter integer DEFECT_BIT     = 3," in text
    assert "parameter integer DEFECT2_ADDR   = 2," in text
    assert "parameter integer DEFECT2_BIT    = 3," in text
