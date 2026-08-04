"""Pin the `repair_ports:` -> external-remap wiring in the wrapper generator.

Under the redundancy architecture, `repair_ports` no longer bind into the memory
instance; they drive an EXTERNAL `repair_remap_row` block that sits between the
address mux and a stock spare-augmented memory. `repair_ports` therefore requires
a `redundancy:` block (its own rules live in test_redundancy_config.py). The
guarantees fixed here:
  * a config with NEITHER block renders the original memory instantiation (opt-in
    isolation -- existing memories are untouched),
  * with both, the repair pins appear on the boundary AND bind to u_repair_remap
    (not u_sram), and the memory takes the remapped physical address,
  * malformed repair_ports are still rejected loudly by _validate_repair_ports.
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

from autombist.generator import ConfigError, generate_from_config  # noqa: E402

BASE_CONFIG = {
    "memory_name": "sram_tiny",
    "wrapper_module_name": "sram_tiny_mbist",
    "addr_width": 2,
    "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}
REDUNDANCY = {"num_spare_rows": 2, "num_spare_cols": 0}
# Names match repair_remap_row's ports (the pins bind to the remap by name).
REPAIR_PORTS = [
    {"name": "row_repair_en", "width": 2, "dir": "input"},
    {"name": "faulty_row_addr", "width": 4, "dir": "input"},
]


def _render(tmp_path: Path, config: dict, subdir: str) -> str:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / subdir)
    return wrapper.read_text(encoding="utf-8")


def _redundant() -> dict:
    return {**BASE_CONFIG, "redundancy": REDUNDANCY, "repair_ports": REPAIR_PORTS}


def test_absent_blocks_render_original_memory(tmp_path: Path) -> None:
    """Opt-in isolation: no redundancy/repair_ports -> the original memory
    instantiation, no remap, no spare params."""
    text = _render(tmp_path, BASE_CONFIG, "base")
    assert "repair_remap_row" not in text
    assert "row_repair_en" not in text
    assert "sram_addr_phys" not in text
    assert "NUM_SPARE_ROWS" not in text
    assert ".addr0(sram_addr)" in text          # memory sees the un-remapped address
    assert "output logic [DATA_WIDTH-1:0] func_dout\n);" in text


def test_repair_pins_on_boundary(tmp_path: Path) -> None:
    text = _render(tmp_path, _redundant(), "with_rd")
    assert "  , input logic [2-1:0] row_repair_en\n" in text
    assert "  , input logic [4-1:0] faulty_row_addr\n" in text


def test_repair_pins_bind_to_remap_and_memory_uses_phys_addr(tmp_path: Path) -> None:
    text = _render(tmp_path, _redundant(), "with_rd")
    # The external remap is instantiated and driven by the repair pins.
    assert "repair_remap_row #(" in text
    assert ") u_repair_remap (" in text
    assert "      , .row_repair_en(row_repair_en)\n" in text
    assert "      , .faulty_row_addr(faulty_row_addr)\n" in text
    # The memory takes the REMAPPED physical address + the spare count.
    assert ".addr0(sram_addr_phys)" in text
    assert ".NUM_SPARE_ROWS(2)" in text
    # ceil(log2(4 + 2)) = 3 -> the physical-address wire is [3-1:0].
    assert "logic [3-1:0] sram_addr_phys;" in text


# --------------------------------------------------------------------------- #
# Column repair (Workstream M.1): a SECOND external remap on the data path.
# --------------------------------------------------------------------------- #
COL_PORTS = {**BASE_CONFIG["ports"], "spare_wen": "spare_wen0"}
COL_REPAIR_PORTS = [
    *REPAIR_PORTS,
    {"name": "col_repair_en", "width": 1, "dir": "input"},
    {"name": "faulty_bit", "width": 2, "dir": "input"},   # 1 spare * bit_index_width(2)
]


def _col_redundant() -> dict:
    return {
        **BASE_CONFIG,
        "ports": COL_PORTS,
        "redundancy": {"num_spare_rows": 2, "num_spare_cols": 1},
        "repair_ports": COL_REPAIR_PORTS,
    }


def test_row_only_wrapper_has_no_trace_of_column_repair(tmp_path: Path) -> None:
    """The byte-identity guarantee, asserted rather than assumed: a row-only
    config must render exactly as it did before column repair existed."""
    text = _render(tmp_path, _redundant(), "row_only")
    for token in (
        "repair_remap_col", "u_repair_remap_col", "sram_din_phys",
        "sram_dout_phys", "sram_spare_wen", "NUM_SPARE_COLS",
    ):
        assert token not in text, f"row-only wrapper leaked {token!r}"


def test_column_repair_instantiates_the_data_path_remap(tmp_path: Path) -> None:
    text = _render(tmp_path, _col_redundant(), "with_col")
    assert "repair_remap_col #(" in text
    assert ") u_repair_remap_col (" in text
    assert ".NUM_SPARE_COLS(1)" in text
    # mem_data_width = data_width(4) + num_spare_cols(1) = 5
    assert "logic [5-1:0] sram_din_phys;" in text
    assert "logic [5-1:0] sram_dout_phys;" in text
    assert "logic [1-1:0] sram_spare_wen;" in text


def test_column_and_row_pins_bind_to_their_own_remaps(tmp_path: Path) -> None:
    """The R2 hazard: the repair_ports loop is generic, so without the bind
    partition a column pin would be bound into repair_remap_row -- a module
    that has no such port (an elaboration error), or worse, a typo'd column
    pin silently routing to the row remap."""
    text = _render(tmp_path, _col_redundant(), "bind_split")
    row_inst = text.split(") u_repair_remap (")[1].split(");")[0]
    col_inst = text.split(") u_repair_remap_col (")[1].split(");")[0]

    assert ".row_repair_en(row_repair_en)" in row_inst
    assert ".faulty_row_addr(faulty_row_addr)" in row_inst
    assert "col_repair_en" not in row_inst
    assert "faulty_bit" not in row_inst

    assert ".col_repair_en(col_repair_en)" in col_inst
    assert ".faulty_bit(faulty_bit)" in col_inst
    assert "row_repair_en" not in col_inst
    assert "faulty_row_addr" not in col_inst


def test_column_repair_hands_the_dout_driver_to_the_col_remap(tmp_path: Path) -> None:
    """The R5 hazard: sram_dout must be driven by the col remap, and the memory
    must drive sram_dout_phys instead -- a leftover double-drive would yield X
    that looks like a functional fault rather than a wiring bug."""
    text = _render(tmp_path, _col_redundant(), "dout_handoff")
    mem_inst = text.split(") u_sram (")[1].split(");")[0]
    assert ".dout0(sram_dout_phys)" in mem_inst
    assert ".din0(sram_din_phys)" in mem_inst
    assert ".spare_wen0(sram_spare_wen)" in mem_inst
    # The memory must NOT also drive the logical dout/din.
    assert ".dout0(sram_dout)" not in mem_inst
    assert ".din0(sram_din)" not in mem_inst

    col_inst = text.split(") u_repair_remap_col (")[1].split(");")[0]
    assert ".dout_out(sram_dout)" in col_inst
    assert ".din_in(sram_din)" in col_inst


# --------------------------------------------------------------------------- #
# Validation teeth: malformed repair_ports still rejected in _validate_repair_ports
# (which runs BEFORE the redundancy biconditional, so these fire regardless).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad",
    [
        [],
        [{"width": 2}],
        [{"name": "1bad", "width": 2}],
        [{"name": "func_dout", "width": 2}],
        [{"name": "clk", "width": 1}],
        [{"name": "r", "width": 0}],
        [{"name": "r", "width": True}],
        [{"name": "r", "dir": "inout"}],
        [{"name": "dup"}, {"name": "dup"}],
        "notalist",
    ],
)
def test_bad_repair_ports_rejected(tmp_path: Path, bad) -> None:
    config = {**BASE_CONFIG, "redundancy": REDUNDANCY, "repair_ports": bad}
    config_path = tmp_path / "bad.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError):
        generate_from_config(config_path, tmp_path / "out")
