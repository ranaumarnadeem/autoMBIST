"""Real Verilator proof for shared_engine.sv's foundation -- step 4 of
docs/shared-hierarchical-mbist-plan.md's implementation order.

Compiles shared_engine.sv directly via subprocess (not compile_engine()/
run_one() -- the compile_engine() NUM_MEMORIES -G flag path is step 6, and
the run_shared_campaign() Python integration is step 7, neither exists
yet), mirroring how this project's other sibling engines proved their own
foundation commit before their campaign-function integration landed.

Two things pinned: a golden (fault-free) run against N memories reports
ESCAPED -- the expected, correct result -- and N=2 genuinely exercises
BOTH memories sequentially (not just memory 0), proven by simulation time
roughly doubling versus N=1 (the "full algorithm pass per memory before
advancing" sequencing this engine is supposed to implement, matching
wrapper_template.j2's own SHB_RUN/SHB_ADVANCE sequencer -- see that
file's step 1 and this engine's own header comment).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.alg_spec import find_engine_dir  # noqa: E402

_FINISH_TIME_RE = re.compile(r"\$finish at (\d+)us")


def _build_and_run(tmp_path: Path, num_memories: int) -> str:
    engine_dir = find_engine_dir()
    obj_dir = tmp_path / f"n{num_memories}"
    obj_dir.mkdir()
    subprocess.run(
        [
            "verilator", "--binary", "--timing",
            "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND",
            f"-GAW=4", "-GDW=8", f"-GNUM_MEMORIES={num_memories}",
            "--top-module", "shared_engine",
            str(engine_dir / "fault_ram.sv"), str(engine_dir / "shared_engine.sv"),
            "-o", "shared_engine_sim",
        ],
        cwd=obj_dir, check=True, capture_output=True, text=True,
    )
    result = subprocess.run(
        ["./obj_dir/shared_engine_sim", "+ALG=MARCHCM"],
        cwd=obj_dir, capture_output=True, text=True,
    )
    return result.stdout + result.stderr


def test_golden_run_escapes_for_a_single_memory(tmp_path: Path) -> None:
    out = _build_and_run(tmp_path, num_memories=1)
    assert "RESULT ESCAPED alg=MARCHCM" in out


def test_golden_run_escapes_for_two_memories(tmp_path: Path) -> None:
    out = _build_and_run(tmp_path, num_memories=2)
    assert "RESULT ESCAPED alg=MARCHCM" in out


def test_two_memories_genuinely_run_a_full_pass_each_not_just_memory_0(tmp_path: Path) -> None:
    """A bug that made the mi loop a no-op (e.g. accidentally hardcoding
    mi=0 somewhere) would still report ESCAPED -- a clean-looking result for
    the wrong reason. Simulation time roughly doubling from N=1 to N=2
    proves the second memory's full algorithm pass genuinely ran, not just
    that nothing crashed."""
    out1 = _build_and_run(tmp_path, num_memories=1)
    out2 = _build_and_run(tmp_path, num_memories=2)

    m1 = _FINISH_TIME_RE.search(out1)
    m2 = _FINISH_TIME_RE.search(out2)
    assert m1 and m2, f"could not find $finish time in output:\n{out1}\n---\n{out2}"

    t1, t2 = int(m1.group(1)), int(m2.group(1))
    assert t1 > 0
    assert t2 == 2 * t1, f"expected N=2 to take exactly 2x N=1's time (full pass per memory), got {t1}us vs {t2}us"
