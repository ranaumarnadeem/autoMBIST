"""Column repair on a COLUMN-MUXED memory, multi-port and shared-bus.

test_repair_muxed_col_e2e.py proves the single-port case: OpenRAM's spare
columns bypass the column mux, so a spare has one cell per physical row,
shared by the row's words, and replaces one physical column. This file runs
the same bitline defect through the other wrappers that place a
repair_remap_col:

  * march-1r1w: ONE remap across the read-only and write-only ports, the
    write port's column bits gating spare_wen and the read port's steering
    the read (tests/hardware/sram_spares_muxed_col_1r1w.v, 2 words per row);
  * march-2rw: one remap PER rw port, each from its own port's column bits
    (sram_spares_muxed_col_2rw.v, 2 words per row, the spare cell shared by
    both ports too);
  * shared-bus: one remap per memory, each from that memory's own access,
    with a different physical column broken in each bank
    (sram_spares_muxed_col.v, 4 words per row).

Each has a negative control at words_per_row 1: the analyzer then claims the
spare column for one bit of EVERY word, the remap writes the one shared cell
from all of the row's words, and the verify pass fails.

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
from autombist.runner import SimulationError, run_simulation  # noqa: E402
from fixture_models import fixture_model  # noqa: E402

ADDR_WIDTH = 3
DATA_WIDTH = 4

PORTS_1R1W = {
    "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
    "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1",
              "spare_wen": "spare_wen1"},
}
PORTS_2RW = {
    "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0",
              "we": "web0", "spare_wen": "spare_wen0"},
    "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1",
              "we": "web1", "spare_wen": "spare_wen1"},
}
PORTS_1RW = {
    "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0",
    "we": "web0", "csb": "csb0", "spare_wen": "spare_wen0",
}


def _redundancy(words_per_row: int) -> dict:
    redundancy = {"num_spare_rows": 1, "num_spare_cols": 1, "onchip_selfrepair": True,
                  "onchip_col_repair": True}
    if words_per_row != 1:
        redundancy["words_per_row"] = words_per_row
    return redundancy


def _generate(tmp_path: Path, config: dict, algo: str) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / "out", algo=algo)


# --- multi-port ---------------------------------------------------------------------

MULTI_PORT = {
    "march-1r1w": ("sram_spares_muxed_col_1r1w", PORTS_1R1W),
    "march-2rw": ("sram_spares_muxed_col_2rw", PORTS_2RW),
}


def _multi_port(tmp_path: Path, algo: str, words_per_row: int) -> Path:
    memory_name, ports = MULTI_PORT[algo]
    config = {
        "memory_name": memory_name, "wrapper_module_name": f"{memory_name}_mbist",
        "addr_width": ADDR_WIDTH, "data_width": DATA_WIDTH, "we_active_low": True,
        "ports": ports, "redundancy": _redundancy(words_per_row),
    }
    return _generate(tmp_path, config, algo)


def _self_repair(wrapper: Path, scenario: str):
    return run_simulation(
        wrapper.parent,
        memory_model=fixture_model(wrapper.parent),
        extra_make_vars={"COCOTB_TEST_MODULES": "test_onchip_selfrepair", "SELFREPAIR_SCENARIO": scenario},
    )


@pytest.mark.parametrize("algo", sorted(MULTI_PORT))
def test_multi_port_self_repairs_a_bitline_defect(tmp_path: Path, algo: str) -> None:
    result = _self_repair(_multi_port(tmp_path, algo, 2), "repairable")
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


@pytest.mark.parametrize("algo", sorted(MULTI_PORT))
def test_multi_port_per_bit_repair_fails_on_the_shared_spare_cell(tmp_path: Path, algo: str) -> None:
    """Negative control: at one word per row the spare column is written from
    both words of each row, into the one cell the row has; the verify pass
    fails (the testbench asserts self_repair_fail)."""
    result = _self_repair(_multi_port(tmp_path, algo, 1), "unrepairable")
    assert result.returncode == 0, result.stdout


# --- shared-bus ---------------------------------------------------------------------

SHARED_WRAPPER = "shared_muxed_col_ctrl"
# sram_spares_muxed_col.v's defect knobs: bit DEFECT_BIT of word column
# DEFECT_WCOL in every physical row. Bank 1 gets a different physical column
# (bit 3 of word column 1 = 13) than bank 0's default (bit 1 of word column 2
# = 6), so a remap fed another memory's claim or column bits would fail.
BANK0_DEFECT: dict[str, int] = {}
BANK1_DEFECT = {"DEFECT_WCOL": 1, "DEFECT_BIT": 3}
NO_DEFECT = {"DEFECT_WCOL": -1}


def _shared_bus(tmp_path: Path, words_per_row: int) -> Path:
    config = {
        "memory_name": "sram_spares_muxed_col", "wrapper_module_name": SHARED_WRAPPER,
        "topology": "shared-bus", "addr_width": ADDR_WIDTH, "data_width": DATA_WIDTH,
        "we_active_low": True, "ports": PORTS_1RW,
        "memories": [{"name": "mem_bank0"}, {"name": "mem_bank1"}],
        "redundancy": _redundancy(words_per_row),
    }
    return _generate(tmp_path, config, "march-c")


def _shared_run(wrapper: Path, overrides: dict[str, dict[str, int]]):
    lines = [f"module {SHARED_WRAPPER}_defect_overrides;"]
    for instance, params in overrides.items():
        lines += [f"    defparam {SHARED_WRAPPER}.{instance}.{name} = {value};" for name, value in params.items()]
    lines.append("endmodule")
    override = wrapper.parent / "defect_overrides.v"
    override.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run_simulation(
        wrapper.parent,
        memory_model=fixture_model(wrapper.parent),
        extra_make_vars={"COCOTB_TEST_MODULES": "test_shared_selfrepair",
                         "EXTRA_SOURCES": str(override), "NUM_MEMORIES": "2"},
    )


@pytest.mark.parametrize(("bank0", "bank1"), [
    (BANK0_DEFECT, BANK1_DEFECT),
    (BANK0_DEFECT, NO_DEFECT),
    (NO_DEFECT, BANK1_DEFECT),
], ids=["both-banks", "bank0-only", "bank1-only"])
def test_shared_bus_self_repairs_each_banks_bitline(
    tmp_path: Path, bank0: dict[str, int], bank1: dict[str, int]
) -> None:
    """The testbench checks the self-repair, a verify BIST, two re-runs and a
    mission-mode pass that writes every word of every bank before reading any
    back -- the order that exposes a spare cell written from the wrong word."""
    wrapper = _shared_bus(tmp_path, 4)
    result = _shared_run(wrapper, {"u_mem_mem_bank0": bank0, "u_mem_mem_bank1": bank1})
    assert result.returncode == 0, result.stdout


def test_shared_bus_per_bit_repair_fails_on_the_shared_spare_cell(tmp_path: Path) -> None:
    """Negative control: at one word per row bank 0's analyzer claims the spare
    column for bit 1 of every word, and the self-repair reports the failure the
    testbench's first check rejects."""
    wrapper = _shared_bus(tmp_path, 1)
    with pytest.raises(SimulationError):
        _shared_run(wrapper, {"u_mem_mem_bank0": BANK0_DEFECT, "u_mem_mem_bank1": NO_DEFECT})
    log = (wrapper.parent / "simulate.log").read_text(encoding="utf-8")
    assert "expected every repairable defect to succeed, self_repair_fail=1" in log
