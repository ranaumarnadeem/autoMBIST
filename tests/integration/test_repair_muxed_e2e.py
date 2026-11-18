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
  * the tester-driven loop (fail scan -> BIRA -> BISR -> repair pins) repairs it
    with words_per_row in the geometry, and BIRA calls it unrepairable without.

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
