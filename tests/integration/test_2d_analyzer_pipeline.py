"""rtl/onchip_2d_repair_analyzer.sv decides exactly as its bit-by-bit reference
does, and its pipeline stage (on with column muxing) is that decision one
cycle late.

The claim decision is computed from running counts built as trees, and
column muxing adds a pipeline stage; both were added to meet a 20 ns clock at
sky130's slow corner (flow/newalgo/README.md). The reference is the module as
it was before, tests/hardware/onchip_2d_repair_analyzer_seq.sv, which walks
the bits one by one exactly as the header rules read.
tests/hardware/tb_2d_analyzer_pipeline.sv runs the reference, the module
without the stage and the module with it on one random input stream -- fails
on consecutive cycles, often on the same physical column, with verify, latch,
retire, defer and repair-load pulses, and a reset every 300 cycles so the
state keeps changing -- and compares every output and every piece of state
each cycle. It also counts the decisions that needed the stage's forwarded
update, and fails a run that never exercised it.

Mutation checked (each fails at least one configuration below): a claim test
of <= for <, a slot choice ignoring claimed or retired slots, an inclusive
count, a one-level count tree, a retired slot counted as free, the deferred
"two or more bits" rule weakened to "any", no forwarding, forwarding across
word columns, and has_suspect without the in-flight fail.
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
HW = REPO_ROOT / "tests" / "hardware"
SOURCES = [HW / "tb_2d_analyzer_pipeline.sv", REPO_ROOT / "rtl" / "onchip_2d_repair_analyzer.sv",
           HW / "onchip_2d_repair_analyzer_seq.sv"]

# Sizes: the default is 8-bit words, 4 per row, 2 spare rows and 2 spare columns.
SHAPES = {
    "default": {},
    "one-word-per-row": {"WPR": 1},
    "one-spare-each": {"NC": 1, "NR": 1},
    "three-spare-cols": {"NC": 3, "DW": 6, "WPR": 2, "SEED": 3},
    "wide": {"DW": 16, "AW": 6, "WPR": 2, "SEED": 5},
}


@pytest.mark.parametrize("deferred", [0, 1], ids=["greedy", "deferred"])
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_analyzer_matches_its_bit_by_bit_reference(tmp_path: Path, shape: str, deferred: int) -> None:
    params = {"DEFERRED": deferred, **SHAPES[shape]}
    exe = tmp_path / "sim"
    build = subprocess.run(
        ["iverilog", "-g2012", *(f"-Ptb_2d_analyzer_pipeline.{k}={v}" for k, v in params.items()),
         "-o", str(exe), *map(str, SOURCES)],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, f"iverilog failed:\n{build.stdout}\n{build.stderr}"
    run = subprocess.run(["vvp", str(exe)], capture_output=True, text=True, check=False, timeout=600)
    assert "PIPELINE RESULT: PASS" in run.stdout, run.stdout[-3000:]
