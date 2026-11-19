"""Row repair on a COLUMN-MUXED memory: a spare row replaces a physical row.

Every real OpenRAM macro this project builds is column-muxed (4 or 16 words per
row), and a spare row there is a physical row of words_per_row consecutive
addresses. These tests plant a wordline defect -- one bit stuck in every word of
a physical row (tests/hardware/sram_spares_muxed.v) -- and check that:

  * on-chip self-repair with redundancy.words_per_row set fixes it with ONE
    spare, on every self-repair algorithm;
  * a second spare row is found at 2**addr_width + 1*words_per_row, not + 1;
  * the negative control: the same memory with words_per_row left at 1 spends
    a spare per faulty WORD, runs out, and reports unrepairable;
  * march-1r1w repairs the 1-read + 1-write port version
    (sram_spares_muxed_1r1w.v, 2 words per row), and fails at words_per_row 1;
  * the tester-driven loop (fail scan -> BIRA -> BISR -> repair pins) repairs it
    with words_per_row in the geometry, and BIRA calls it unrepairable without.
  * yield-sweep's engines take words_per_row: the self-repair engine builds
    with it and repairs the defect (and runs out of spares without it), and
    the scan engine observes a muxed defect preset exactly.

The memory model has storage only for real rows (reads past the last spare row
return X), so a remap that steers off the array fails the re-scan.
Icarus + make gated, like the other RTL-backed tests.
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

HARDWARE = REPO_ROOT / "tests" / "hardware"
MUXED = HARDWARE / "sram_spares_muxed.v"
MUXED_2ROW = HARDWARE / "sram_spares_muxed_2row.v"

ADDR_WIDTH = 3
DATA_WIDTH = 4
WORDS_PER_ROW = 4
BASE_PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}

# sram_spares_muxed.v's defect: bit 2 stuck-at-1 in every word of physical row 1.
ROW1_DEFECT = {(addr, 2) for addr in range(4, 8)}
SELFREPAIR_ALGOS = ["march-c", "march-raw", "march-x", "mats-plus", "checkerboard"]


def _redundancy(num_spare_rows: int, words_per_row: int | None, onchip: bool) -> dict:
    block: dict = {"num_spare_rows": num_spare_rows, "num_spare_cols": 0}
    if words_per_row is not None:
        block["words_per_row"] = words_per_row
    if onchip:
        block["onchip_selfrepair"] = True
    return block


def _config(memory_name: str, redundancy: dict, repair_ports: list | None = None) -> dict:
    config = {
        "memory_name": memory_name,
        "wrapper_module_name": f"{memory_name}_mbist",
        "addr_width": ADDR_WIDTH,
        "data_width": DATA_WIDTH,
        "we_active_low": True,
        "ports": BASE_PORTS,
        "redundancy": redundancy,
    }
    if repair_ports:
        config["repair_ports"] = repair_ports
    return config


def _generate(tmp_path: Path, config: dict, algo: str = "march-c") -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / "out", algo=algo)


def _self_repair(wrapper: Path, models: list[Path], scenario: str):
    return run_simulation(
        wrapper.parent,
        memory_model=models,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_onchip_selfrepair", "SELFREPAIR_SCENARIO": scenario},
    )


@pytest.mark.parametrize("algo", SELFREPAIR_ALGOS)
def test_wordline_defect_is_self_repaired_with_one_spare(tmp_path: Path, algo: str) -> None:
    config = _config("sram_spares_muxed", _redundancy(1, WORDS_PER_ROW, onchip=True))
    wrapper = _generate(tmp_path, config, algo=algo)
    result = _self_repair(wrapper, [MUXED], "repairable")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


def test_second_spare_row_starts_one_physical_row_later(tmp_path: Path) -> None:
    """Both physical rows broken, two spares: the second spare row is at
    2**addr_width + words_per_row (address 12). The model has no storage past
    the spare rows and spare row 0's words are taken, so steering anywhere else
    fails the re-scan."""
    config = _config("sram_spares_muxed_2row", _redundancy(2, WORDS_PER_ROW, onchip=True))
    wrapper = _generate(tmp_path, config)
    result = _self_repair(wrapper, [MUXED_2ROW, MUXED], "repairable")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


def test_word_level_repair_runs_out_of_spares_on_a_wordline_defect(tmp_path: Path) -> None:
    """Negative control: words_per_row left at its default of 1 on the same
    muxed memory. The analyzer sees four faulty "rows" (words) for one spare,
    reports unrepairable, and three of the four words stay broken."""
    config = _config("sram_spares_muxed", _redundancy(1, None, onchip=True))
    wrapper = _generate(tmp_path, config)
    result = _self_repair(wrapper, [MUXED], "unrepairable")
    assert result.returncode == 0, result.stdout
    remaining = fail_cells(result.report)
    assert remaining < ROW1_DEFECT
    assert len(remaining) == WORDS_PER_ROW - 1


def test_tester_driven_loop_repairs_a_wordline_defect(tmp_path: Path) -> None:
    """Fail scan -> BIRA -> BISR -> repair pins, nothing hardcoded but the defect."""
    config = _config(
        "sram_spares_muxed",
        _redundancy(1, WORDS_PER_ROW, onchip=False),
        repair_ports=[
            {"name": "row_repair_en", "width": 1, "dir": "input"},
            {"name": "faulty_row_addr", "width": ADDR_WIDTH, "dir": "input"},
        ],
    )
    wrapper = _generate(tmp_path, config)

    def run(**repair_vars: int):
        return run_simulation(
            wrapper.parent,
            memory_model=[MUXED],
            extra_make_vars={"COCOTB_TEST_MODULES": "test_repair_loop", **repair_vars},
        )

    before = run(ROW_REPAIR_EN=0, FAULTY_ROW_ADDR=0, EXPECTED_BIST_FAIL=1)
    assert before.returncode == 0, before.stdout
    observed = fail_cells(before.report)
    assert observed == ROW1_DEFECT

    word_level = SpareGeometry(base_words=1 << ADDR_WIDTH, word_size=DATA_WIDTH, num_spare_rows=1)
    assert isinstance(analyze(observed, word_level), Unrepairable)

    geo = SpareGeometry(
        base_words=1 << ADDR_WIDTH, word_size=DATA_WIDTH, num_spare_rows=1, words_per_row=WORDS_PER_ROW
    )
    solution = analyze(observed, geo)
    assert isinstance(solution, RepairSolution)
    assert solution.row_map == {4: 0}
    signature = encode_row_repair(solution, geo)

    after = run(
        ROW_REPAIR_EN=signature.row_repair_en,
        FAULTY_ROW_ADDR=signature.faulty_row_addr,
        EXPECTED_BIST_FAIL=0,
    )
    assert after.returncode == 0, after.stdout
    assert fail_cells(after.report) == set()


# The 1-read + 1-write port model: 2 words per row, the value OpenRAM picks for
# sky130_sram_1r1w_32b256w. Bit 3 stuck-at-1 in both words of physical row 1.
MUXED_1R1W = HARDWARE / "sram_spares_muxed_1r1w.v"
PORTS_1R1W = {
    "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
    "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
}
ROW1_DEFECT_1R1W = {(2, 3), (3, 3)}


def _config_1r1w(words_per_row: int | None) -> dict:
    config = _config("sram_spares_muxed_1r1w", _redundancy(1, words_per_row, onchip=True))
    config["ports"] = PORTS_1R1W
    return config


def test_march_1r1w_self_repairs_a_wordline_defect_with_one_spare(tmp_path: Path) -> None:
    wrapper = _generate(tmp_path, _config_1r1w(2), algo="march-1r1w")
    result = _self_repair(wrapper, [MUXED_1R1W], "repairable")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


def test_march_1r1w_word_level_repair_runs_out_of_spares(tmp_path: Path) -> None:
    """Negative control: the same dual-port memory with words_per_row left at 1."""
    wrapper = _generate(tmp_path, _config_1r1w(None), algo="march-1r1w")
    result = _self_repair(wrapper, [MUXED_1R1W], "unrepairable")
    assert result.returncode == 0, result.stdout
    remaining = fail_cells(result.report)
    assert remaining < ROW1_DEFECT_1R1W
    assert len(remaining) == 1


# --- yield-sweep's engines on a muxed memory ---------------------------------------

def _sweep_config(tmp_path: Path) -> Path:
    path = tmp_path / "sweep.yml"
    path.write_text(yaml.safe_dump({
        "memory_name": "sram_sweep", "wrapper_module_name": "sram_sweep_mbist", "addr_width": ADDR_WIDTH,
        "data_width": DATA_WIDTH, "we_active_low": True, "ports": BASE_PORTS,
    }, sort_keys=False), encoding="utf-8")
    return path


def test_yield_sweep_self_repair_fixes_a_muxed_word_line_defect(tmp_path: Path) -> None:
    from autombist.yield_sweep import SelfRepairEngine

    engine = SelfRepairEngine(_sweep_config(tmp_path), tmp_path / "work", num_spare_rows=1,
                              words_per_row=WORDS_PER_ROW)
    record = engine.run_stuck({cell: 1 for cell in ROW1_DEFECT})
    assert record.repaired, record.extras
    assert record.extras["truth_repairable"] and record.extras["cause"] is None
    assert record.extras["residual_cells"] == 0


def test_yield_sweep_self_repair_word_level_runs_out_of_spares(tmp_path: Path) -> None:
    """Negative control: the same defect on a build left at one word per row."""
    from autombist.yield_sweep import SelfRepairEngine

    engine = SelfRepairEngine(_sweep_config(tmp_path), tmp_path / "work", num_spare_rows=1)
    record = engine.run_stuck({cell: 1 for cell in ROW1_DEFECT})
    assert not record.repaired
    assert record.extras["onchip_fail"] == 1 and not record.extras["truth_repairable"]


def test_yield_sweep_scan_observes_a_muxed_defect_preset_exactly(tmp_path: Path) -> None:
    from autombist.yield_sweep import RtlEngine

    engine = RtlEngine(_sweep_config(tmp_path), tmp_path / "dut", num_spare_rows=1, defects="ohler-d2",
                       words_per_row=WORDS_PER_ROW)
    records = [engine.run_trial(1, seed) for seed in range(4)]
    assert all(r.valid and r.extras["overkill"] == 0 for r in records)
    assert all(r.extras["fail_cell_count"] == r.extras["truth_cells"] for r in records)
