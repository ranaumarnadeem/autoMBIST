"""redundancy.onchip_spare_retry on real RTL: a faulty spare the on-chip verify
pass catches is retired and the repair retried on another spare.

Single-port cases run through yield_sweep.SelfRepairEngine (a fault-injectable
model, spares included); the multi-port case through the existing
test_onchip_selfrepair scenarios with a fixture whose spare row 0 is faulty.
Icarus + make gated, like every other self-repair e2e test here.
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
from autombist.runner import SimulationError, run_simulation  # noqa: E402
from autombist.yield_sweep import SelfRepairEngine  # noqa: E402


def _engine(tmp_path: Path, addr_width: int, data_width: int, rows: int, cols: int, **kwargs) -> SelfRepairEngine:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump({
        "memory_name": "sram_sweep", "wrapper_module_name": "sram_sweep_mbist",
        "addr_width": addr_width, "data_width": data_width, "we_active_low": True,
        "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
    }, sort_keys=False), encoding="utf-8")
    return SelfRepairEngine(path, tmp_path / "work", num_spare_rows=rows, num_spare_cols=cols, **kwargs)


# Row 3 is faulty and so is spare row 0 (physical row 4 at 2 address bits): the
# analyzer gives row 3 spare row 0 first.
BAD_SPARE_ROW = {(3, 1): 1, (4, 0): 0}


@pytest.mark.parametrize("retry", [False, True])
def test_an_allocated_faulty_spare_row_is_retired_and_replaced(tmp_path: Path, retry: bool) -> None:
    record = _engine(tmp_path, 2, 4, 2, 0, spare_retry=retry).run_stuck(BAD_SPARE_ROW)
    assert record.extras["truth_repairable"]
    if retry:
        assert record.repaired and record.extras["row_map"] == {3: 1}
    else:
        assert record.status == "unrepairable" and record.extras["cause"] == "faulty_spare"


def test_with_every_spare_faulty_the_retry_ends_unrepairable(tmp_path: Path) -> None:
    record = _engine(tmp_path, 2, 4, 2, 0, spare_retry=True).run_stuck({**BAD_SPARE_ROW, (5, 2): 1})
    assert not record.extras["truth_repairable"]
    assert record.status == "unrepairable" and record.extras["onchip_unrepairable"] == 1
    assert record.extras["row_map"] == {}  # both spares retired


def test_an_allocated_faulty_spare_column_is_retired_and_replaced(tmp_path: Path) -> None:
    """Bit 1 fails on rows 1, 4 and 6: row 1 takes the spare row, the recurrences
    take spare column 0, whose own cell at row 2 is stuck (physical bit 8)."""
    cells = {(1, 1): 1, (4, 1): 1, (6, 1): 1, (2, 8): 0}
    without = _engine(tmp_path / "a", 3, 8, 1, 2).run_stuck(cells)
    with_retry = _engine(tmp_path / "b", 3, 8, 1, 2, spare_retry=True).run_stuck(cells)
    assert without.extras["cause"] == "faulty_spare"
    assert with_retry.repaired and with_retry.extras["col_map"] == {1: 1}


def test_retry_does_not_change_the_analyzers_own_gap(tmp_path: Path) -> None:
    record = _engine(tmp_path, 5, 8, 2, 1, spare_retry=True).run_stuck(
        {(5, 0): 1, (5, 1): 1, (9, 2): 1, (20, 2): 1, (30, 5): 1})
    assert record.extras["cause"] == "analyzer"


def test_the_diagnosis_log_keeps_the_rows_of_the_first_analyze_pass(tmp_path: Path) -> None:
    """Rows 1 and 3 are faulty; spare row 0, which row 1 gets first, is faulty
    too. The retry pass sees only row 1 (row 3's repair holds); the log must
    still hold both."""
    engine = _engine(tmp_path, 3, 4, 3, 0, spare_retry=True, diagnosis_entries=4)
    record = engine.run_stuck({(1, 0): 1, (3, 2): 0, (8, 1): 1})
    assert record.repaired and record.extras["row_map"] == {1: 2, 3: 1}
    assert record.extras["diag_rows"] == [1, 3] and record.extras["diag_overflow"] == 0


def _multi_port(tmp_path: Path, retry: bool):
    config = {
        "memory_name": "sram_spares_tiny_1r1w_badspare", "wrapper_module_name": "sram_spares_tiny_1r1w_badspare_mbist",
        "addr_width": 2, "data_width": 4, "we_active_low": True,
        "ports": {
            "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
            "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
        },
        "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True,
                       "onchip_spare_retry": retry},
    }
    path = tmp_path / f"retry_{retry}.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(path, tmp_path / f"retry_{retry}", algo="march-1r1w")
    return run_simulation(wrapper.parent, extra_make_vars={
        "COCOTB_TEST_MODULES": "test_onchip_selfrepair", "SELFREPAIR_SCENARIO": "repairable",
    })


def test_a_multi_port_chip_retires_its_faulty_spare_row(tmp_path: Path) -> None:
    repaired = _multi_port(tmp_path, retry=True)
    assert repaired.returncode == 0, repaired.stdout
    assert fail_cells(repaired.report) == set()

    # Without the retry the faulty spare fails the repairable scenario.
    with pytest.raises(SimulationError):
        _multi_port(tmp_path, retry=False)


@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs Verilator on PATH")
@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("columns", [False, True])
def test_the_wrapper_lints_clean_with_verilator(tmp_path: Path, retry: bool, columns: bool) -> None:
    """The same lint the shared-bus wrappers pass: the retry counter's limit
    compare must not be a constant unsigned compare at MAX_RETRIES 0."""
    import subprocess

    memory = "sram_spares_col_tiny" if columns else "sram_spares_tiny"
    ports = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}
    redundancy = {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True, "onchip_spare_retry": retry}
    if columns:
        ports["spare_wen"] = "spare_wen0"
        redundancy.update(num_spare_cols=1, onchip_col_repair=True)
    config = {"memory_name": memory, "wrapper_module_name": f"{memory}_mbist", "addr_width": 2, "data_width": 4,
              "we_active_low": True, "ports": ports, "redundancy": redundancy}
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(path, tmp_path / "out")
    module = wrapper.parent
    analyzer = "onchip_2d_repair_analyzer.sv" if columns else "onchip_row_repair_analyzer.sv"
    remaps = ["repair_remap_row.sv", *(["repair_remap_col.sv"] if columns else [])]
    sources = [wrapper, *sorted((module / "march_c").glob("*.sv")),
               *(module / name for name in (analyzer, "onchip_selfrepair_ctrl.sv", *remaps)),
               REPO_ROOT / "tests" / "hardware" / f"{memory}.v"]
    result = subprocess.run(
        ["verilator", "--lint-only", "--timing",
         "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND", "-Wno-UNUSED", "-Wno-DECLFILENAME",
         "-Wno-PINMISSING", "-Wno-PINCONNECTEMPTY", "-Wno-BLKSEQ", *[str(s) for s in sources]],
        capture_output=True, text=True, cwd=module,
    )
    assert result.returncode == 0, f"Verilator lint failed:\n{result.stdout}\n{result.stderr}"
