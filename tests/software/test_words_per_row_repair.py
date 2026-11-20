"""Row repair on a column-muxed memory (redundancy.words_per_row > 1).

A physical row holds words_per_row consecutive addresses, and a spare row
replaces a whole one. These pure tests pin the geometry, BIRA's grouping of
failing addresses into physical rows, the config rules, and the rendered RTL
parameters; tests/integration/test_repair_muxed_e2e.py runs the RTL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import ConfigError, generate_from_config, load_config  # noqa: E402
from autombist.repair import (  # noqa: E402
    RepairSolution,
    SpareGeometry,
    SpareGeometryError,
    Unrepairable,
    analyze,
    encode_row_repair,
)
from autombist.repair.bira import analyze_detailed, verify_solution  # noqa: E402

PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}


def _geo(**kw) -> SpareGeometry:
    base = {"base_words": 8, "word_size": 4, "num_spare_rows": 1, "words_per_row": 4}
    return SpareGeometry(**{**base, **kw})


# --- geometry -----------------------------------------------------------------

@pytest.mark.parametrize(("base_words", "spares", "wpr", "width"), [
    (8, 1, 4, 4),        # 8 words + one 4-word spare row = 12 -> 4 bits
    (1024, 1, 16, 11),   # sky130_sram_8b1024w: ADDR_WIDTH 11, as OpenRAM emits
    (256, 1, 4, 9),      # sky130_sram_32b256w: ADDR_WIDTH 9
    (512, 1, 4, 10),     # sky130_sram_32b512w: ADDR_WIDTH 10
    (8, 2, 4, 4),        # two spare rows fill addresses 8..15
])
def test_mem_addr_width_counts_spare_rows_as_whole_rows(base_words, spares, wpr, width) -> None:
    geo = SpareGeometry(base_words=base_words, word_size=8, num_spare_rows=spares, words_per_row=wpr)
    assert geo.mem_addr_width == width
    assert geo.total_words == base_words + spares * wpr


def test_row_of_groups_consecutive_addresses() -> None:
    geo = _geo()
    assert [geo.row_of(a) for a in range(8)] == [0, 0, 0, 0, 1, 1, 1, 1]
    assert geo.num_rows == 2


@pytest.mark.parametrize("wpr", [0, 3, 6, -4])
def test_words_per_row_must_be_a_power_of_two(wpr: int) -> None:
    with pytest.raises(SpareGeometryError, match="power of two"):
        _geo(words_per_row=wpr)


def test_words_per_row_must_divide_the_array() -> None:
    with pytest.raises(SpareGeometryError, match="divide"):
        _geo(base_words=4, words_per_row=8)


# --- BIRA ---------------------------------------------------------------------

WORDLINE = {(a, 2) for a in range(4, 8)}  # bit 2 broken in every word of row 1


def test_a_wordline_defect_takes_one_spare() -> None:
    solution = analyze(WORDLINE, _geo())
    assert solution == RepairSolution(row_map={4: 0})


def test_the_same_defect_is_unrepairable_word_by_word() -> None:
    assert isinstance(analyze(WORDLINE, _geo(words_per_row=1)), Unrepairable)


def test_faults_in_two_physical_rows_need_two_spares() -> None:
    cells = {(1, 0), (6, 3)}
    verdict = analyze(cells, _geo())
    assert isinstance(verdict, Unrepairable)
    assert verdict.faulty_rows == (0, 4)  # each row named by its first address
    assert analyze(cells, _geo(num_spare_rows=2)) == RepairSolution(row_map={0: 0, 4: 1})


def test_analyze_detailed_groups_rows_too() -> None:
    result = analyze_detailed(WORDLINE, _geo(), objective="min_spares")
    assert result.repaired and result.spares_used == 1
    assert result.result == RepairSolution(row_map={4: 0})


def test_verify_solution_accepts_any_address_in_the_repaired_row() -> None:
    # The remap compares only the row bits, so naming row 1 by address 5 works.
    assert verify_solution(WORDLINE, _geo(), RepairSolution(row_map={5: 0})) == []
    problems = verify_solution(WORDLINE, _geo(words_per_row=1), RepairSolution(row_map={5: 0}))
    assert any("not covered" in p for p in problems)


def test_verify_solution_flags_two_repairs_of_one_physical_row() -> None:
    problems = verify_solution(WORDLINE, _geo(num_spare_rows=2), RepairSolution(row_map={4: 0, 6: 1}))
    assert "two row repairs name the same physical row" in problems


def test_encoded_signature_names_the_row() -> None:
    signature = encode_row_repair(analyze(WORDLINE, _geo()), _geo())
    assert (signature.row_repair_en, signature.faulty_row_addr) == (1, 4)


# --- config -------------------------------------------------------------------

def _config(tmp_path: Path, redundancy: dict, addr_width: int = 3, **extra) -> Path:
    config = {
        "memory_name": "sram_mux", "wrapper_module_name": "sram_mux_mbist",
        "addr_width": addr_width, "data_width": 4, "we_active_low": True,
        "ports": dict(PORTS), "redundancy": redundancy, **extra,
    }
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


ONCHIP = {"num_spare_rows": 1, "num_spare_cols": 0, "onchip_selfrepair": True}


def test_words_per_row_is_accepted_and_sizes_the_geometry(tmp_path: Path) -> None:
    loaded = load_config(_config(tmp_path, {**ONCHIP, "words_per_row": 4}))
    assert loaded["redundancy"]["words_per_row"] == 4
    assert loaded["redundancy"]["mem_addr_width"] == 4


@pytest.mark.parametrize("bad", [3, 0, True, "4"])
def test_words_per_row_must_be_a_power_of_two_integer(tmp_path: Path, bad) -> None:
    with pytest.raises(ConfigError, match="power of two"):
        load_config(_config(tmp_path, {**ONCHIP, "words_per_row": bad}))


def test_spare_rows_cannot_hold_more_words_than_the_array(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="spare words exceed"):
        load_config(_config(tmp_path, {**ONCHIP, "num_spare_rows": 2, "words_per_row": 4}, addr_width=2))


def _col_config(tmp_path: Path, faulty_bit_width: int) -> Path:
    return _config(
        tmp_path,
        {"num_spare_rows": 1, "num_spare_cols": 1, "words_per_row": 4},
        ports={**PORTS, "spare_wen": "spare_wen0"},
        repair_ports=[
            {"name": "row_repair_en", "width": 1, "dir": "input"},
            {"name": "faulty_row_addr", "width": 3, "dir": "input"},
            {"name": "col_repair_en", "width": 1, "dir": "input"},
            {"name": "faulty_bit", "width": faulty_bit_width, "dir": "input"},
        ],
    )


def test_column_repair_under_muxing_names_physical_columns(tmp_path: Path) -> None:
    # 4 bits x 4 words per row = 16 physical columns: a 4-bit faulty_bit slice.
    loaded = load_config(_col_config(tmp_path, 4))
    assert loaded["redundancy"]["bit_index_width"] == 4
    with pytest.raises(ConfigError, match="faulty_bit' width must be 4"):
        load_config(_col_config(tmp_path, 2))


def test_multi_port_column_repair_is_refused_under_muxing(tmp_path: Path) -> None:
    ports = {
        "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
        "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1",
                  "spare_wen": "spare_wen1"},
    }
    redundancy = {**ONCHIP, "num_spare_cols": 1, "onchip_col_repair": True, "words_per_row": 2}
    with pytest.raises(ConfigError, match="single-port memory"):
        load_config(_config(tmp_path, redundancy, ports=ports))
    # Row repair alone is fine there.
    load_config(_config(tmp_path, {**ONCHIP, "words_per_row": 2}, ports=ports))


# --- rendered RTL -------------------------------------------------------------

def test_remap_and_analyzer_get_words_per_row(tmp_path: Path) -> None:
    wrapper = generate_from_config(
        _config(tmp_path, {**ONCHIP, "words_per_row": 4}), tmp_path / "out", algo="march-c"
    )
    text = wrapper.read_text(encoding="utf-8")
    assert text.count(".WORDS_PER_ROW(4)") == 2  # u_onchip_analyzer and u_repair_remap


def test_column_remap_gets_the_column_bits_under_muxing(tmp_path: Path) -> None:
    redundancy = {**ONCHIP, "num_spare_cols": 1, "onchip_col_repair": True, "words_per_row": 4}
    config = _config(tmp_path, redundancy, ports={**PORTS, "spare_wen": "spare_wen0"}, read_latency=0)
    text = generate_from_config(config, tmp_path / "out", algo="march-c").read_text(encoding="utf-8")
    assert text.count(".WORDS_PER_ROW(4)") == 3  # analyzer, row remap, column remap
    assert ".READ_LATENCY(0)" in text
    assert ".wr_col(sram_addr[1:0])" in text and ".rd_issue(!sram_csb && !selected_write_req)" in text


def test_no_column_mux_ports_at_one_word_per_row(tmp_path: Path) -> None:
    redundancy = {**ONCHIP, "num_spare_cols": 1, "onchip_col_repair": True}
    config = _config(tmp_path, redundancy, ports={**PORTS, "spare_wen": "spare_wen0"})
    text = generate_from_config(config, tmp_path / "out", algo="march-c").read_text(encoding="utf-8")
    assert "WORDS_PER_ROW" not in text and ".wr_col(" not in text


def test_no_words_per_row_parameter_at_the_default(tmp_path: Path) -> None:
    wrapper = generate_from_config(_config(tmp_path, dict(ONCHIP)), tmp_path / "out", algo="march-c")
    assert "WORDS_PER_ROW" not in wrapper.read_text(encoding="utf-8")
