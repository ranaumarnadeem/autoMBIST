"""The on-chip 2D analyzer on real RTL: the lenient greedy default, the deferred
two-pass mode (redundancy.onchip_analyzer: deferred), and agreement with the
Python model in yieldsim.onchip_model, decision for decision.

Runs through yield_sweep.SelfRepairEngine (a fault-injectable model). Icarus +
make gated, like every other self-repair e2e test here.
"""
from __future__ import annotations

import random
import shutil
import subprocess
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

from autombist.generator import generate_from_config  # noqa: E402
from autombist.yield_sweep import SelfRepairEngine  # noqa: E402
from autombist.yieldsim.defects import ArrayGeometry, defect_model  # noqa: E402
from autombist.yieldsim.onchip_model import onchip_solution  # noqa: E402

SINGLE_PORT = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}
TWO_RW = {
    "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0",
              "spare_wen": "spare_wen0"},
    "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1",
              "spare_wen": "spare_wen1"},
}
GAP_DEMO = {(5, 0): 1, (5, 1): 1, (9, 2): 1, (20, 2): 1, (30, 5): 1}  # tests/hardware/sram_spares_col_gap_demo.v
FOUR_SINGLES = {(0, 0): 1, (2, 3): 1, (4, 5): 1, (6, 7): 1}


def _engine(tmp_path: Path, addr_width: int, data_width: int, rows: int, cols: int, *,
            ports: dict = SINGLE_PORT, algo: str = "march-c", **kwargs) -> SelfRepairEngine:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump({
        "memory_name": "sram_sweep", "wrapper_module_name": "sram_sweep_mbist", "addr_width": addr_width,
        "data_width": data_width, "we_active_low": True, "ports": ports,
    }, sort_keys=False), encoding="utf-8")
    return SelfRepairEngine(path, tmp_path / "work", num_spare_rows=rows, num_spare_cols=cols, algo=algo, **kwargs)


def test_the_lenient_greedy_analyzer_covers_a_single_it_had_no_row_for(tmp_path: Path) -> None:
    """Three spare rows go to the first three singles; the fourth takes a spare
    column when march-c reads it again (the analyzer used to give up there)."""
    record = _engine(tmp_path, 3, 8, 3, 3).run_stuck(FOUR_SINGLES)
    assert record.repaired and record.extras["row_map"] == {0: 0, 2: 1, 4: 2} and record.extras["col_map"] == {7: 0}


@pytest.mark.parametrize(("algo", "ports"), [("march-c", SINGLE_PORT), ("march-2rw", TWO_RW)])
def test_the_deferred_analyzer_repairs_the_gap_demo(tmp_path: Path, algo: str, ports: dict) -> None:
    greedy = _engine(tmp_path / "greedy", 5, 8, 2, 1, ports=ports, algo=algo).run_stuck(GAP_DEMO)
    deferred = _engine(tmp_path / "deferred", 5, 8, 2, 1, ports=ports, algo=algo, analyzer="deferred").run_stuck(GAP_DEMO)
    assert greedy.status == "unrepairable" and greedy.extras["cause"] == "analyzer"
    assert deferred.repaired and deferred.extras["row_map"] == {5: 0, 30: 1} and deferred.extras["col_map"] == {2: 0}


def test_the_deferred_analyzer_works_with_spare_retry(tmp_path: Path) -> None:
    """Row 3 has two failing bits, so pass one gives it spare row 0, which is
    itself faulty (physical row 4): the retry moves it to spare row 1."""
    cells = {(3, 1): 1, (3, 2): 1, (4, 0): 0}
    record = _engine(tmp_path, 2, 4, 2, 1, analyzer="deferred", spare_retry=True).run_stuck(cells)
    assert record.repaired and record.extras["row_map"] == {3: 1}


@pytest.mark.parametrize("mode", ["greedy", "deferred"])
def test_the_rtl_makes_the_models_decisions(tmp_path: Path, mode: str) -> None:
    """Random fail maps (Ohler's mix d2, 2 spare rows and 2 spare columns): the
    chip repairs exactly the trials the model says its analyzer repairs."""
    engine = _engine(tmp_path, 4, 8, 2, 2, analyzer=mode)
    model, geometry = defect_model("ohler-d2"), ArrayGeometry(rows=16, cols=8)
    outcomes = []
    for trial in range(12):
        rng = random.Random(7000 + trial)
        stuck = {}
        for defect in model.draw(rng, geometry, rng.choice([2, 3, 4, 5])):
            value = rng.randrange(2)
            for cell in defect.cells:
                stuck[cell] = value
        predicted = onchip_solution(stuck, 16, 2, 2, mode) is not None
        outcomes.append((engine.run_stuck(stuck).repaired, predicted))
    assert all(chip == model_says for chip, model_says in outcomes), outcomes
    assert any(chip for chip, _ in outcomes) and not all(chip for chip, _ in outcomes)  # both outcomes occur


@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs Verilator on PATH")
@pytest.mark.parametrize("retry", [False, True])
def test_the_deferred_wrapper_lints_clean_with_verilator(tmp_path: Path, retry: bool) -> None:
    config = {
        "memory_name": "sram_spares_col_tiny", "wrapper_module_name": "sram_spares_col_tiny_mbist",
        "addr_width": 2, "data_width": 4, "we_active_low": True,
        "ports": {**SINGLE_PORT, "spare_wen": "spare_wen0"},
        "redundancy": {"num_spare_rows": 2, "num_spare_cols": 1, "onchip_selfrepair": True, "onchip_col_repair": True,
                       "onchip_analyzer": "deferred", "onchip_spare_retry": retry},
    }
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    module = generate_from_config(path, tmp_path / "out").parent
    sources = [next(module.glob("*_mbist.v")), *sorted((module / "march_c").glob("*.sv")),
               *(module / name for name in ("onchip_2d_repair_analyzer.sv", "onchip_selfrepair_ctrl.sv",
                                            "repair_remap_row.sv", "repair_remap_col.sv")),
               REPO_ROOT / "tests" / "hardware" / "sram_spares_col_tiny.v"]
    result = subprocess.run(
        ["verilator", "--lint-only", "--timing",
         "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND", "-Wno-UNUSED", "-Wno-DECLFILENAME",
         "-Wno-PINMISSING", "-Wno-PINCONNECTEMPTY", "-Wno-BLKSEQ", *[str(s) for s in sources]],
        capture_output=True, text=True, cwd=module,
    )
    assert result.returncode == 0, f"Verilator lint failed:\n{result.stdout}\n{result.stderr}"
