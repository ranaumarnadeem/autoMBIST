"""Column repair on a COLUMN-MUXED memory: a spare column replaces one
physical column.

OpenRAM routes spare columns around the column mux, so a spare column has one
cell per physical row, shared by every word of the row. These tests plant a
bitline defect -- bit 1 of word column 2 stuck in every physical row, spare
row included (tests/hardware/sram_spares_muxed_col.v, which models the shared
spare cell) -- and check that:

  * the tester-driven loop (fail scan -> BIRA -> BISR -> repair pins) repairs it
    with one spare column when words_per_row is in the geometry, and the
    negative control: the signature computed at one word per row (spare column
    for bit 1 of EVERY word) leaves the BIST failing, because every word of the
    row then shares the one spare cell;
  * on-chip self-repair (greedy and deferred 2D analyzer) repairs it on every
    self-repair algorithm with column repair, and fails at words_per_row 1;
  * a wordline defect in the same memory takes the spare row while the
    bitline takes the spare column, the spare row's own bitline cell then
    coming from the spare column;
  * on-chip self-repair also works with OpenRAM's read timing (read_latency
    0, sram_spares_muxed_col_l0.v);
  * back-to-back functional reads, the address moving on before dout is used,
    are steered by the read that produced the data (at both read latencies) --
    the only test that catches a remap steering by the current address, since
    the BIST and the fail scan hold the address while they read.

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
from autombist.repair import RepairSolution, SpareGeometry, analyze, encode_repair  # noqa: E402
from autombist.runner import run_simulation  # noqa: E402

HARDWARE = REPO_ROOT / "tests" / "hardware"
MUXED_COL = HARDWARE / "sram_spares_muxed_col.v"
MUXED_COL_L0 = HARDWARE / "sram_spares_muxed_col_l0.v"

ADDR_WIDTH = 3
DATA_WIDTH = 4
WORDS_PER_ROW = 4
PORTS = {
    "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0",
    "we": "web0", "csb": "csb0", "spare_wen": "spare_wen0",
}

# sram_spares_muxed_col.v's defect: bit 1 of word column 2, both physical rows.
BITLINE = {(2, 1), (6, 1)}
COL_SELFREPAIR_ALGOS = ["march-c", "march-raw", "march-x", "mats-plus", "checkerboard"]


def _config(memory_name: str, redundancy: dict, *, read_latency: int = 1,
            repair_ports: list | None = None) -> dict:
    config = {
        "memory_name": memory_name,
        "wrapper_module_name": f"{memory_name}_mbist",
        "addr_width": ADDR_WIDTH,
        "data_width": DATA_WIDTH,
        "we_active_low": True,
        "read_latency": read_latency,
        "ports": PORTS,
        "redundancy": redundancy,
    }
    if repair_ports:
        config["repair_ports"] = repair_ports
    return config


def _generate(tmp_path: Path, config: dict, algo: str = "march-c") -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / "out", algo=algo)


def _geometry(words_per_row: int) -> SpareGeometry:
    return SpareGeometry(base_words=1 << ADDR_WIDTH, word_size=DATA_WIDTH, num_spare_rows=1,
                         num_spare_cols=1, words_per_row=words_per_row)


# --- tester-driven ----------------------------------------------------------------

def _tester_config(words_per_row: int) -> dict:
    redundancy = {"num_spare_rows": 1, "num_spare_cols": 1}
    if words_per_row != 1:
        redundancy["words_per_row"] = words_per_row
    return _config("sram_spares_muxed_col", redundancy, repair_ports=[
        {"name": "row_repair_en", "width": 1, "dir": "input"},
        {"name": "faulty_row_addr", "width": ADDR_WIDTH, "dir": "input"},
        {"name": "col_repair_en", "width": 1, "dir": "input"},
        {"name": "faulty_bit", "width": _geometry(words_per_row).bit_index_width, "dir": "input"},
    ])


def _tester_run(wrapper: Path, **repair_vars: int):
    return run_simulation(
        wrapper.parent,
        memory_model=[MUXED_COL],
        extra_make_vars={"COCOTB_TEST_MODULES": "test_repair_2d", **repair_vars},
    )


def _observe(wrapper: Path) -> set[tuple[int, int]]:
    before = _tester_run(wrapper, ROW_REPAIR_EN=0, FAULTY_ROW_ADDR=0, COL_REPAIR_EN=0, FAULTY_BIT=0,
                         EXPECTED_BIST_FAIL=1)
    assert before.returncode == 0, before.stdout
    return fail_cells(before.report)


def test_tester_driven_loop_repairs_a_bitline_defect_with_one_spare_column(tmp_path: Path) -> None:
    wrapper = _generate(tmp_path, _tester_config(WORDS_PER_ROW))
    observed = _observe(wrapper)
    assert observed == BITLINE

    geo = _geometry(WORDS_PER_ROW)
    solution = analyze(observed, geo)
    assert solution == RepairSolution(row_map={}, col_map={6: 0})
    signature = encode_repair(solution, geo)
    after = _tester_run(
        wrapper,
        ROW_REPAIR_EN=signature.row_repair_en, FAULTY_ROW_ADDR=signature.faulty_row_addr,
        COL_REPAIR_EN=signature.col_repair_en, FAULTY_BIT=signature.faulty_bit,
        EXPECTED_BIST_FAIL=0,
    )
    assert after.returncode == 0, after.stdout
    assert fail_cells(after.report) == set()


def test_a_per_bit_column_repair_fails_on_the_shared_spare_cell(tmp_path: Path) -> None:
    """Negative control: words_per_row left at 1. BIRA then repairs bit 1 of
    every word with the spare column, and the remap writes the spare from all
    four words of a row -- into the ONE cell the row has. The march's
    read-then-write across the row's words exposes it: the BIST still fails
    (EXPECTED_BIST_FAIL=1 is asserted in the testbench). The functional fail
    scan writes each word and reads it straight back, so it cannot see the
    sharing and is not checked here."""
    wrapper = _generate(tmp_path, _tester_config(1))
    observed = _observe(wrapper)
    assert observed == BITLINE

    geo = _geometry(1)
    solution = analyze(observed, geo)
    assert solution == RepairSolution(row_map={}, col_map={1: 0})
    signature = encode_repair(solution, geo)
    after = _tester_run(
        wrapper,
        ROW_REPAIR_EN=signature.row_repair_en, FAULTY_ROW_ADDR=signature.faulty_row_addr,
        COL_REPAIR_EN=signature.col_repair_en, FAULTY_BIT=signature.faulty_bit,
        EXPECTED_BIST_FAIL=1,
    )
    assert after.returncode == 0, after.stdout


# --- on-chip ------------------------------------------------------------------------

def _onchip(words_per_row: int, analyzer: str = "greedy") -> dict:
    redundancy = {"num_spare_rows": 1, "num_spare_cols": 1, "onchip_selfrepair": True,
                  "onchip_col_repair": True, "onchip_analyzer": analyzer}
    if words_per_row != 1:
        redundancy["words_per_row"] = words_per_row
    return redundancy


def _self_repair(wrapper: Path, models: list[Path], scenario: str):
    return run_simulation(
        wrapper.parent,
        memory_model=models,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_onchip_selfrepair", "SELFREPAIR_SCENARIO": scenario},
    )


@pytest.mark.parametrize("algo", COL_SELFREPAIR_ALGOS)
def test_bitline_defect_is_self_repaired(tmp_path: Path, algo: str) -> None:
    wrapper = _generate(tmp_path, _config("sram_spares_muxed_col", _onchip(WORDS_PER_ROW)), algo=algo)
    result = _self_repair(wrapper, [MUXED_COL], "repairable")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


def test_deferred_analyzer_self_repairs_a_bitline_defect(tmp_path: Path) -> None:
    wrapper = _generate(tmp_path, _config("sram_spares_muxed_col", _onchip(WORDS_PER_ROW, "deferred")))
    result = _self_repair(wrapper, [MUXED_COL], "repairable")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


def test_per_bit_self_repair_fails_on_the_shared_spare_cell(tmp_path: Path) -> None:
    """Negative control: the on-chip analyzer at one word per row claims the
    spare column for bit 1 of every word, and the verify pass catches the
    shared cell (the testbench asserts self_repair_fail)."""
    wrapper = _generate(tmp_path, _config("sram_spares_muxed_col", _onchip(1)))
    result = _self_repair(wrapper, [MUXED_COL], "unrepairable")
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(("memory_name", "read_latency", "models"), [
    ("sram_spares_muxed_col", 1, [MUXED_COL]),
    ("sram_spares_muxed_col_l0", 0, [MUXED_COL_L0, MUXED_COL]),
])
def test_back_to_back_reads_are_steered_by_the_read_that_produced_them(
    tmp_path: Path, memory_name: str, read_latency: int, models: list[Path]
) -> None:
    """A read every cycle, the address moving on before dout is used: steering
    by the current address would substitute the spare into the wrong word.
    Neither the BIST nor the fail scan reads this way (both hold the address)."""
    config = _tester_config(WORDS_PER_ROW)
    config.update(memory_name=memory_name, wrapper_module_name=f"{memory_name}_mbist", read_latency=read_latency)
    wrapper = _generate(tmp_path, config)
    signature = encode_repair(RepairSolution(row_map={}, col_map={6: 0}), _geometry(WORDS_PER_ROW))
    result = run_simulation(wrapper.parent, memory_model=models, extra_make_vars={
        "COCOTB_TEST_MODULES": "test_col_mux_pipelined_reads",
        "COL_REPAIR_EN": signature.col_repair_en, "FAULTY_BIT": signature.faulty_bit,
    })
    assert result.returncode == 0, result.stdout
    summary = (result.report.get("junit") or {}).get("summary") or {}
    assert summary.get("tests") and not summary.get("failures") and not summary.get("errors"), summary


MUXED_COL_ROW = HARDWARE / "sram_spares_muxed_col_row.v"
WORDLINE_ROW0 = {(addr, 3) for addr in range(4)}


def test_tester_driven_wordline_and_bitline_take_one_spare_each(tmp_path: Path) -> None:
    config = _tester_config(WORDS_PER_ROW)
    config["memory_name"] = "sram_spares_muxed_col_row"
    config["wrapper_module_name"] = "sram_spares_muxed_col_row_mbist"
    wrapper = _generate(tmp_path, config)

    def run(**repair_vars: int):
        return run_simulation(wrapper.parent, memory_model=[MUXED_COL_ROW, MUXED_COL],
                              extra_make_vars={"COCOTB_TEST_MODULES": "test_repair_2d", **repair_vars})

    before = run(ROW_REPAIR_EN=0, FAULTY_ROW_ADDR=0, COL_REPAIR_EN=0, FAULTY_BIT=0, EXPECTED_BIST_FAIL=1)
    assert before.returncode == 0, before.stdout
    observed = fail_cells(before.report)
    assert observed == WORDLINE_ROW0 | BITLINE

    geo = _geometry(WORDS_PER_ROW)
    solution = analyze(observed, geo)
    assert solution == RepairSolution(row_map={0: 0}, col_map={6: 0})
    signature = encode_repair(solution, geo)
    after = run(
        ROW_REPAIR_EN=signature.row_repair_en, FAULTY_ROW_ADDR=signature.faulty_row_addr,
        COL_REPAIR_EN=signature.col_repair_en, FAULTY_BIT=signature.faulty_bit,
        EXPECTED_BIST_FAIL=0,
    )
    assert after.returncode == 0, after.stdout
    assert fail_cells(after.report) == set()


@pytest.mark.parametrize("analyzer", ["greedy", "deferred"])
def test_onchip_wordline_and_bitline_take_one_spare_each(tmp_path: Path, analyzer: str) -> None:
    config = _config("sram_spares_muxed_col_row", _onchip(WORDS_PER_ROW, analyzer))
    wrapper = _generate(tmp_path, config)
    result = _self_repair(wrapper, [MUXED_COL_ROW, MUXED_COL], "repairable")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


def test_onchip_self_repair_with_openram_read_timing(tmp_path: Path) -> None:
    """read_latency 0, the macro's own timing: the remap's read pipeline is one
    stage, and self-repair still fixes the bitline."""
    config = _config("sram_spares_muxed_col_l0", _onchip(WORDS_PER_ROW), read_latency=0)
    wrapper = _generate(tmp_path, config)
    result = _self_repair(wrapper, [MUXED_COL_L0, MUXED_COL], "repairable")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()
