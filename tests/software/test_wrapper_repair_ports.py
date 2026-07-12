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
