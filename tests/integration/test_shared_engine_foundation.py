"""Real Verilator proof for shared_engine.sv's foundation (step 4) and
per-memory fault targeting (step 5) -- docs/shared-hierarchical-mbist-
plan.md's implementation order.

Compiles shared_engine.sv directly via subprocess (not compile_engine()/
run_one() -- the compile_engine() NUM_MEMORIES -G flag path is step 6, and
the run_shared_campaign() Python integration is step 7, neither exists
yet), mirroring how this project's other sibling engines proved their own
foundation commit before their campaign-function integration landed.
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


def _build(tmp_path: Path, num_memories: int) -> Path:
    engine_dir = find_engine_dir()
    obj_dir = tmp_path / f"n{num_memories}"
    obj_dir.mkdir()
    subprocess.run(
        [
            "verilator", "--binary", "--timing",
            "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND",
            "-GAW=4", "-GDW=8", f"-GNUM_MEMORIES={num_memories}",
            "--top-module", "shared_engine",
            str(engine_dir / "fault_ram.sv"), str(engine_dir / "shared_engine.sv"),
            "-o", "shared_engine_sim",
        ],
        cwd=obj_dir, check=True, capture_output=True, text=True,
    )
    return obj_dir


def _run(obj_dir: Path, *extra_args: str) -> str:
    result = subprocess.run(
        ["./obj_dir/shared_engine_sim", "+ALG=MARCHCM", *extra_args],
        cwd=obj_dir, capture_output=True, text=True,
    )
    return result.stdout + result.stderr


def _build_and_run(tmp_path: Path, num_memories: int) -> str:
    return _run(_build(tmp_path, num_memories))


# --------------------------------------------------------------------------- #
# Step 4: foundation -- a golden run against N memories
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Step 5: per-memory fault targeting (fault_ram.sv's FAULT_TAG)
# --------------------------------------------------------------------------- #
_FAULT_LINE = "SA0 3 2 0 0 0 0\n"


def test_fault_on_memory_0_detected_there_memory_1_never_sees_it(tmp_path: Path) -> None:
    fault_file = tmp_path / "fault0.txt"
    fault_file.write_text(_FAULT_LINE, encoding="utf-8")
    obj_dir = _build(tmp_path, num_memories=2)

    out = _run(obj_dir, f"+FAULTS0={fault_file}", "+FAULT_INDEX0=0")
    assert "RESULT DETECTED" in out
    assert "mem=0" in out
    # Proves no cross-memory leakage: memory 1 got no +FAULTS1 at all, so if
    # this fired on memory 1 instead (the wrong memory), that would mean the
    # FAULT_TAG-suffixed plusarg names aren't actually distinct per instance.
    assert "mem=1" not in out


def test_fault_on_memory_1_detected_there_memory_0_stays_golden(tmp_path: Path) -> None:
    fault_file = tmp_path / "fault1.txt"
    fault_file.write_text(_FAULT_LINE, encoding="utf-8")
    obj_dir = _build(tmp_path, num_memories=2)

    out = _run(obj_dir, f"+FAULTS1={fault_file}", "+FAULT_INDEX1=0")
    assert "RESULT DETECTED" in out
    assert "mem=1" in out
    assert "mem=0" not in out
    # Memory 0 runs first and must complete its own full clean pass (same
    # duration as a true golden N=1 run) before memory 1's fault is even
    # reached -- detected later than the memory-0 case above, not earlier.
    detect_time = int(_FINISH_TIME_RE.search(out).group(1))
    golden_n1_time = int(_FINISH_TIME_RE.search(_build_and_run(tmp_path, num_memories=1)).group(1))
    assert detect_time > golden_n1_time
