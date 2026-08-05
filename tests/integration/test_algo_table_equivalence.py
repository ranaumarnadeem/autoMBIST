"""Prove a RENDERED classic-path algorithm table is behaviourally identical to
the HAND-WRITTEN one, for every algorithm that exists in both subsystems.

This is the decisive result: it establishes that a `.alg` spec fully determines
the classic path's algorithm content, which is the premise the rest of the
research-mode/classic-path unification rests on. Without it, `algo_rtl_gen` is
just a plausible-looking code generator.

The comparison is exhaustive and behavioural -- all 8 phase values x all 4
op_step values, all six outputs, via simulation. Deliberately NOT a text diff:
byte-identity with the hand-written files is impossible (they disagree with each
other on comment style and line endings) and would prove less anyway.

Verified to have teeth: perturbing a single rendered arm makes this fail. See
test_equivalence_probe_detects_a_perturbed_table below, which does exactly that
rather than asserting the property on trust.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="needs Icarus Verilog (iverilog + vvp) on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.alg_spec import resolve_algo  # noqa: E402
from autombist.algo_rtl_gen import render_algo_table  # noqa: E402

PROBE_SV = REPO_ROOT / "tests" / "hardware" / "algo_table_equiv_probe.sv"

# Every algorithm present in BOTH subsystems: a .alg spec and a hand-written
# RTL table. march_raw/march_1r1w/march_2rw have RTL but no .alg; march_ss and
# march_b have .alg but exceed the FSM's op_step width (see algo_rtl_gen's
# MAX_RTL_OPS), so neither set can be compared here.
BOTH = ["march_c", "march_x", "mats_plus"]


def _build_and_run(tmp_path: Path, algo: str, mutate=None) -> str:
    """Render the table, compile it against the hand-written one, run the probe."""
    gen_module = f"{algo}_algo_gen"
    text = render_algo_table(resolve_algo(algo)).replace(
        f"module {algo}_algo ", f"module {gen_module} "
    )
    assert gen_module in text, "module rename failed -- probe would compare a module to itself"
    if mutate is not None:
        text = mutate(text)

    gen_sv = tmp_path / f"{gen_module}.sv"
    gen_sv.write_text(text, encoding="utf-8")
    ref_sv = REPO_ROOT / "rtl" / algo / f"{algo}_algo.sv"
    exe = tmp_path / f"probe_{algo}"

    build = subprocess.run(
        ["iverilog", "-g2012",
         f"-DREF_MODULE={algo}_algo", f"-DGEN_MODULE={gen_module}",
         "-s", "algo_table_equiv_probe",
         "-o", str(exe), str(PROBE_SV), str(ref_sv), str(gen_sv)],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, f"iverilog failed:\n{build.stdout}\n{build.stderr}"

    run = subprocess.run(
        ["vvp", str(exe)], capture_output=True, text=True, check=False, timeout=300,
    )
    return run.stdout + run.stderr


@pytest.mark.parametrize("algo", BOTH)
def test_rendered_table_matches_hand_written(tmp_path: Path, algo: str) -> None:
    out = _build_and_run(tmp_path, algo)
    assert "MISMATCH" not in out, out
    assert "EQUIV FAIL" not in out, out
    assert "EQUIV PASS 32 vectors" in out, out


def test_equivalence_probe_detects_a_perturbed_table(tmp_path: Path) -> None:
    """The negative control. A probe that cannot fail proves nothing, so flip
    one direction literal in the rendered march_c table and require the sweep to
    catch it."""
    def flip_first_direction(text: str) -> str:
        mutated = text.replace("phase_dir_up = 1'b1;", "phase_dir_up = 1'b0;", 1)
        assert mutated != text, "perturbation pattern did not match"
        return mutated

    out = _build_and_run(tmp_path, "march_c", mutate=flip_first_direction)
    assert "MISMATCH phase_dir_up" in out, out
    assert "EQUIV FAIL" in out, out


def test_rendered_tables_elaborate_standalone(tmp_path: Path) -> None:
    """Each rendered table must also elaborate on its own -- the equivalence run
    always compiles it alongside a hand-written sibling, which could in
    principle mask a missing declaration."""
    for algo in BOTH:
        sv = tmp_path / f"{algo}_solo.sv"
        sv.write_text(render_algo_table(resolve_algo(algo)), encoding="utf-8")
        result = subprocess.run(
            ["iverilog", "-g2012", "-t", "null", str(sv)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, f"{algo}:\n{result.stdout}\n{result.stderr}"
