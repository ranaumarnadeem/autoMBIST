"""Pin the `redundancy.onchip_selfrepair` config flag (Step E) and the wrapper
render it selects.

`onchip_selfrepair: true` inside the existing `redundancy:` block switches the
generated wrapper from the tester-driven repair flow (repair_ports -> external
remap, Steps A-D) to a fully autonomous on-chip BIRA/BISR sequencer -- no
repair_ports, no tester. These are additive/new; the pre-existing
test_wrapper_repair_ports.py and test_redundancy_config.py are untouched except
for one directly-necessary update (the redundancy dict legitimately gained this
field) -- see the diff there.
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
REPAIR_PORTS = [
    {"name": "row_repair_en", "width": 2, "dir": "input"},
    {"name": "faulty_row_addr", "width": 4, "dir": "input"},
]


def _render(tmp_path: Path, config: dict, subdir: str, **kwargs) -> str:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / subdir, **kwargs)
    return wrapper.read_text(encoding="utf-8")


def _tester_driven() -> dict:
    """Today's Step A/D shape: redundancy + repair_ports, NO onchip_selfrepair
    key at all -- the regression-critical case, not a bare no-redundancy config."""
    return {**BASE_CONFIG, "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0}, "repair_ports": REPAIR_PORTS}


def _onchip() -> dict:
    return {**BASE_CONFIG, "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True}}


def _onchip_with_persistence() -> dict:
    return {
        **BASE_CONFIG,
        "redundancy": {
            "num_spare_rows": 2, "num_spare_cols": 0,
            "onchip_selfrepair": True, "onchip_repair_persistence": True,
        },
    }


# --------------------------------------------------------------------------- #
# Byte-identity: the regression-critical case
# --------------------------------------------------------------------------- #
def test_tester_driven_config_unaffected_by_the_new_flag(tmp_path: Path) -> None:
    """A config exactly like Step A/D's, with no onchip_selfrepair key at all,
    must render EXACTLY as before -- same substrings test_wrapper_repair_ports.py
    already pins, and none of the new on-chip symbols."""
    text = _render(tmp_path, _tester_driven(), "tester")
    # Today's tester-driven shape, unaffected.
    assert "repair_remap_row #(" in text
    assert ") u_repair_remap (" in text
    assert "      , .row_repair_en(row_repair_en)\n" in text
    assert "      , .faulty_row_addr(faulty_row_addr)\n" in text
    assert ".addr0(sram_addr_phys)" in text
    assert "  , input logic [2-1:0] row_repair_en\n" in text
    assert "  , input logic [4-1:0] faulty_row_addr\n" in text
    # None of the new on-chip machinery leaks in.
    assert "onchip_row_repair_analyzer" not in text
    assert "onchip_selfrepair_ctrl" not in text
    assert "self_repair_start" not in text


# --------------------------------------------------------------------------- #
# The onchip_selfrepair=true render
# --------------------------------------------------------------------------- #
def test_onchip_render_has_new_symbols_and_no_repair_ports(tmp_path: Path) -> None:
    text = _render(tmp_path, _onchip(), "onchip")
    assert "onchip_row_repair_analyzer #(" in text
    assert ") u_onchip_analyzer (" in text
    assert "onchip_selfrepair_ctrl u_onchip_selfrepair_ctrl (" in text
    # Boundary ports.
    assert "  , input  logic self_repair_start\n" in text
    assert "  , output logic self_repair_done\n" in text
    assert "  , output logic self_repair_fail\n" in text
    assert "  , output logic self_repair_busy\n" in text
    # repair_ports-style tester pins are absent -- the analyzer drives the remap.
    assert "row_repair_en(row_repair_en)" in text  # internal wire binding, still present...
    assert "input  logic [2-1:0] row_repair_en" not in text  # ...but NOT as a boundary port
    # u_algo_top gained the streaming fail interface.
    assert ".bist_fail_valid(algo_fail_valid)," in text
    assert ".bist_fail_addr(algo_fail_addr)," in text
    # The memory still takes the remapped physical address.
    assert ".addr0(sram_addr_phys)" in text


def test_onchip_analyzer_and_remap_share_matching_parameters(tmp_path: Path) -> None:
    """The packing convention between the analyzer's faulty_row_addr output and
    repair_remap_row's expected layout is a manual invariant (not type-checked)
    -- both must be parameterized identically."""
    text = _render(tmp_path, _onchip(), "onchip_params")
    analyzer_block = text[text.index("onchip_row_repair_analyzer #(") : text.index("u_onchip_analyzer")]
    remap_block = text[text.index("repair_remap_row #(") : text.index("u_repair_remap")]
    for block in (analyzer_block, remap_block):
        assert ".ADDR_WIDTH(ADDR_WIDTH)" in block
        assert ".NUM_SPARE_ROWS(2)" in block


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_onchip_selfrepair_mutually_exclusive_with_repair_ports(tmp_path: Path) -> None:
    config = {
        **BASE_CONFIG,
        "redundancy": {"num_spare_rows": 2, "onchip_selfrepair": True},
        "repair_ports": REPAIR_PORTS,
    }
    config_path = tmp_path / "bad.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="mutually exclusive"):
        generate_from_config(config_path, tmp_path / "out")


def test_onchip_selfrepair_now_works_for_march_raw(tmp_path: Path) -> None:
    """Self-repair generalizes beyond march-c (Workstream A1): march_raw_fsm/
    march_raw_top now stream fail_valid/fail_addr just like march-c's, and the
    single-port wrapper branch already consumes that stream generically (keyed
    off {{ algo_top_module }}, not a hardcoded march_c reference) -- so this
    algo choice renders the same on-chip self-repair scaffold, just wired to
    march_raw_top instead of march_c_top."""
    text = _render(tmp_path, _onchip(), "onchip_raw", algo="march-raw")
    assert "onchip_row_repair_analyzer #(" in text
    assert "onchip_selfrepair_ctrl u_onchip_selfrepair_ctrl (" in text
    assert "march_raw_top #(" in text
    assert ".bist_fail_valid(algo_fail_valid)," in text
    assert ".bist_fail_addr(algo_fail_addr)," in text


def test_onchip_selfrepair_now_works_for_march_x_and_mats_plus(tmp_path: Path) -> None:
    """The two new generated algorithms (Workstream B1) are self-repair-ready
    from day one: single-port, fail_valid/fail_addr wired up like march-c's."""
    for algo, top_module in (("march-x", "march_x_top"), ("mats-plus", "mats_plus_top")):
        text = _render(tmp_path, _onchip(), f"onchip_{algo}", algo=algo)
        assert "onchip_row_repair_analyzer #(" in text
        assert "onchip_selfrepair_ctrl u_onchip_selfrepair_ctrl (" in text
        assert f"{top_module} #(" in text
        assert ".bist_fail_valid(algo_fail_valid)," in text
        assert ".bist_fail_addr(algo_fail_addr)," in text


def test_onchip_selfrepair_now_works_for_march_1r1w(tmp_path: Path) -> None:
    """Self-repair now generalizes to the multi-port march-1r1w shape too
    (Workstream A2): the multi-port wrapper branch gained its own analyzer/
    ctrl/remap scaffold, fed by port 0's (the read port's) fail stream, and a
    SINGLE repair_remap_row steers both ports since they always carry the
    identical logical address (see march_1r1w_fsm.sv)."""
    config = {
        "memory_name": "sram_spares_tiny_1r1w",
        "wrapper_module_name": "sram_spares_tiny_1r1w_mbist",
        "addr_width": 2,
        "data_width": 4,
        "we_active_low": True,
        "ports": {
            "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
            "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
        },
        "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True},
    }
    config_path = tmp_path / "onchip_1r1w.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    text = generate_from_config(config_path, tmp_path / "onchip_1r1w", algo="march-1r1w").read_text(encoding="utf-8")

    assert "march_1r1w_top #(" in text
    assert "onchip_row_repair_analyzer #(" in text
    assert "onchip_selfrepair_ctrl u_onchip_selfrepair_ctrl (" in text
    assert ".bist_fail_valid(algo_fail_valid)," in text
    assert ".bist_fail_addr(algo_fail_addr)," in text
    # A single remap, fed by port 0's (the read port's) logical address.
    assert ".addr_in(sram_addr0)" in text
    assert text.count("repair_remap_row #(") == 1
    # BOTH ports' memory pins take the SAME remapped physical address.
    assert ".addr0(sram_addr_phys)" in text
    assert ".addr1(sram_addr_phys)" in text
    assert ".NUM_SPARE_ROWS(2)" in text
    # No tester-driven repair_ports boundary pins.
    assert "input  logic [2-1:0] row_repair_en" not in text


def test_onchip_selfrepair_rejects_unsupported_algo(tmp_path: Path) -> None:
    """march-2rw is still rejected: redundancy remains single-port only (its
    own, separate restriction), so a 2-port march-2rw config combined with
    onchip_selfrepair still fails -- proving the A1 allowlist relaxation for
    march-raw didn't quietly open the door to multi-port algos the wrapper
    template has no self-repair scaffold for yet."""
    config = {
        "memory_name": "sram_2rw_dut",
        "wrapper_module_name": "sram_2rw_dut_mbist",
        "addr_width": 6,
        "data_width": 8,
        "we_active_low": True,
        "ports": {
            "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
            "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"},
        },
        "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True},
    }
    config_path = tmp_path / "bad_multiport.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="single-port"):
        generate_from_config(config_path, tmp_path / "out", algo="march-2rw")


@pytest.mark.parametrize("bad", [1, "true", [], {}])
def test_onchip_selfrepair_bad_type_rejected(tmp_path: Path, bad) -> None:
    config = {**BASE_CONFIG, "redundancy": {"num_spare_rows": 2, "onchip_selfrepair": bad}}
    config_path = tmp_path / "bad_type.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="boolean"):
        generate_from_config(config_path, tmp_path / "out")


# --------------------------------------------------------------------------- #
# onchip_repair_persistence (Workstream 1.7)
# --------------------------------------------------------------------------- #
def test_onchip_render_without_persistence_has_no_persistence_boundary_ports(tmp_path: Path) -> None:
    """onchip_selfrepair alone (no onchip_repair_persistence key) renders
    byte-identically to before this workstream at the wrapper BOUNDARY -- no
    repair_load/fuse_*/repair_load_done ports leak in. The analyzer module
    itself always has these ports now (not templated), so the instantiation
    ties them off internally instead."""
    text = _render(tmp_path, _onchip(), "onchip_no_persist")
    assert "input  logic repair_load" not in text
    assert "fuse_row_repair_en" not in text or ".fuse_row_repair_en('0)," in text
    assert "output logic repair_load_done" not in text
    assert ".repair_load(1'b0)," in text
    assert ".repair_load_done()," in text


def test_onchip_repair_persistence_render_has_new_boundary_ports(tmp_path: Path) -> None:
    text = _render(tmp_path, _onchip_with_persistence(), "onchip_persist")
    assert "  , input  logic repair_load\n" in text
    assert "  , input  logic [2-1:0] fuse_row_repair_en\n" in text
    assert "  , input  logic [2*ADDR_WIDTH-1:0] fuse_faulty_row_addr\n" in text
    assert "  , output logic repair_load_done\n" in text
    assert ".repair_load(repair_load)," in text
    assert ".fuse_row_repair_en(fuse_row_repair_en)," in text
    assert ".fuse_faulty_row_addr(fuse_faulty_row_addr)," in text
    assert ".repair_load_done(repair_load_done)," in text


def test_onchip_repair_persistence_works_for_march_1r1w(tmp_path: Path) -> None:
    """The multi-port wrapper branch's analyzer instantiation gets the same
    persistence wiring treatment as the single-port branch."""
    config = {
        "memory_name": "sram_spares_tiny_1r1w",
        "wrapper_module_name": "sram_spares_tiny_1r1w_mbist",
        "addr_width": 2,
        "data_width": 4,
        "we_active_low": True,
        "ports": {
            "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
            "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
        },
        "redundancy": {
            "num_spare_rows": 2, "num_spare_cols": 0,
            "onchip_selfrepair": True, "onchip_repair_persistence": True,
        },
    }
    config_path = tmp_path / "onchip_1r1w_persist.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    text = generate_from_config(
        config_path, tmp_path / "onchip_1r1w_persist", algo="march-1r1w"
    ).read_text(encoding="utf-8")
    assert "  , input  logic repair_load\n" in text
    assert ".repair_load(repair_load)," in text
    assert ".repair_load_done(repair_load_done)," in text


def test_onchip_repair_persistence_requires_onchip_selfrepair(tmp_path: Path) -> None:
    config = {**BASE_CONFIG, "redundancy": {"num_spare_rows": 2, "onchip_repair_persistence": True}}
    config_path = tmp_path / "bad_persist.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="onchip_selfrepair"):
        generate_from_config(config_path, tmp_path / "out")


@pytest.mark.parametrize("bad", [1, "true", [], {}])
def test_onchip_repair_persistence_bad_type_rejected(tmp_path: Path, bad) -> None:
    config = {
        **BASE_CONFIG,
        "redundancy": {"num_spare_rows": 2, "onchip_selfrepair": True, "onchip_repair_persistence": bad},
    }
    config_path = tmp_path / "bad_persist_type.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="boolean"):
        generate_from_config(config_path, tmp_path / "out")
