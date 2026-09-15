"""Pin the `redundancy.onchip_col_repair` config flag and the wrapper render
it selects.

`onchip_col_repair: true` (requiring `onchip_selfrepair: true` and
`num_spare_cols > 0`) switches the on-chip analyzer from
onchip_row_repair_analyzer (row-only) to onchip_2d_repair_analyzer (row +
column), and adds one or more repair_remap_col instances sourced from the
analyzer's own outputs instead of repair_ports. Covers the single-port
wrapper branch (march-c/march-raw/march-x/mats-plus) and both multi-port
branch shapes -- march-1r1w (ONE repair_remap_col instance, cross-wired
across its clean read-only/write-only port split -- there is no
tester-driven multi-port path to have built one for before) and march-2rw
(TWO independent repair_remap_col instances, one per port, since both ports
are fully read/write and can write different addresses the same cycle, so
neither is exclusively "the" reader or writer). Neither multi-port case is a
mechanical repeat of the single-port pattern. These are additive/new;
test_redundancy_config.py
and test_onchip_selfrepair_config.py are untouched except for one
directly-necessary update there (the redundancy dict legitimately gained this
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
    "memory_name": "sram_spares_col_tiny",
    "wrapper_module_name": "sram_spares_col_tiny_mbist",
    "addr_width": 2,
    "data_width": 4,
    "we_active_low": True,
    "ports": {
        "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0",
        "we": "web0", "csb": "csb0", "spare_wen": "spare_wen0",
    },
}


def _render(tmp_path: Path, config: dict, subdir: str, **kwargs) -> str:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / subdir, **kwargs)
    return wrapper.read_text(encoding="utf-8")


def _row_only_onchip() -> dict:
    """Today's on-chip shape: onchip_selfrepair alone, no column repair --
    the regression-critical byte-identity case."""
    return {**BASE_CONFIG, "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0, "onchip_selfrepair": True}}


def _onchip_col_repair(*, num_spare_rows: int = 1, num_spare_cols: int = 1) -> dict:
    return {
        **BASE_CONFIG,
        "redundancy": {
            "num_spare_rows": num_spare_rows,
            "num_spare_cols": num_spare_cols,
            "onchip_selfrepair": True,
            "onchip_col_repair": True,
        },
    }


# --------------------------------------------------------------------------- #
# Byte-identity: the regression-critical case
# --------------------------------------------------------------------------- #
def test_row_only_onchip_unaffected_by_the_new_flag(tmp_path: Path) -> None:
    """A config exactly like today's on-chip row-only shape, with no
    onchip_col_repair key at all, must render EXACTLY as before -- the
    row-only analyzer, no 2D analyzer/repair_remap_col symbols."""
    text = _render(tmp_path, _row_only_onchip(), "row_only")
    assert "onchip_row_repair_analyzer #(" in text
    assert ") u_onchip_analyzer (" in text
    # None of the new column machinery leaks in.
    assert "onchip_2d_repair_analyzer" not in text
    assert "repair_remap_col" not in text
    assert "sram_din_phys" not in text
    assert "sram_dout_phys" not in text
    assert "sram_spare_wen" not in text
    assert "bist_fail_bitmask" not in text


# --------------------------------------------------------------------------- #
# The onchip_col_repair=true render
# --------------------------------------------------------------------------- #
def test_onchip_col_repair_render_has_new_symbols(tmp_path: Path) -> None:
    text = _render(tmp_path, _onchip_col_repair(), "col_repair")
    assert "onchip_2d_repair_analyzer #(" in text
    assert ") u_onchip_analyzer (" in text
    assert "repair_remap_col #(" in text
    assert ") u_repair_remap_col (" in text
    # The row-only module is gone from this render entirely.
    assert "onchip_row_repair_analyzer" not in text
    # u_algo_top gained the bitmask streaming port.
    assert ".bist_fail_bitmask(algo_fail_bitmask)," in text
    # Data-path physical wires for the column steer.
    assert "sram_din_phys" in text
    assert "sram_dout_phys" in text
    assert "sram_spare_wen" in text
    # No repair_ports-style tester pins -- the analyzer drives both remaps.
    assert "input  logic [1-1:0] col_repair_en" not in text
    # The analyzer's own col_repair_en/faulty_bit feed repair_remap_col directly.
    assert ".col_repair_en(col_repair_en)," in text
    assert ".faulty_bit(faulty_bit)" in text


def test_onchip_col_repair_analyzer_and_remaps_share_matching_parameters(tmp_path: Path) -> None:
    """Same manual-invariant concern as the row-only analyzer/remap pairing:
    the analyzer's faulty_row_addr/faulty_bit packing must match what
    repair_remap_row/repair_remap_col each expect -- both sides must be
    parameterized identically."""
    text = _render(tmp_path, _onchip_col_repair(), "col_repair_params")
    # Anchored on the actual instantiation pattern (") u_onchip_analyzer ("),
    # not the bare identifier -- the module's own header comment mentions
    # "u_onchip_analyzer" in prose before the real instantiation line.
    analyzer_block = text[text.index("onchip_2d_repair_analyzer #(") : text.index(") u_onchip_analyzer (")]
    row_remap_block = text[text.index("repair_remap_row #(") : text.index(") u_repair_remap (")]
    col_remap_block = text[text.index("repair_remap_col #(") : text.index(") u_repair_remap_col (")]
    assert ".ADDR_WIDTH(ADDR_WIDTH)" in analyzer_block
    assert ".DATA_WIDTH(DATA_WIDTH)" in analyzer_block
    assert ".NUM_SPARE_ROWS(1)" in analyzer_block
    assert ".NUM_SPARE_COLS(1)" in analyzer_block
    assert ".NUM_SPARE_ROWS(1)" in row_remap_block
    assert ".DATA_WIDTH(DATA_WIDTH)" in col_remap_block
    assert ".NUM_SPARE_COLS(1)" in col_remap_block


def test_onchip_col_repair_works_for_all_four_algos(tmp_path: Path) -> None:
    """Column repair generalizes across every algo generator.py wires
    fail_bitmask for (_COL_SELFREPAIR_ALGOS) -- not a march-c-only accident."""
    for algo, top_module in (
        ("march-c", "march_c_top"),
        ("march-raw", "march_raw_top"),
        ("march-x", "march_x_top"),
        ("mats-plus", "mats_plus_top"),
    ):
        text = _render(tmp_path, _onchip_col_repair(), f"col_repair_{algo}", algo=algo)
        assert "onchip_2d_repair_analyzer #(" in text
        assert f"{top_module} #(" in text
        assert ".bist_fail_bitmask(algo_fail_bitmask)," in text


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_onchip_col_repair_requires_onchip_selfrepair(tmp_path: Path) -> None:
    config = {**BASE_CONFIG, "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1, "onchip_col_repair": True}}
    config_path = tmp_path / "bad.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="onchip_col_repair requires onchip_selfrepair"):
        generate_from_config(config_path, tmp_path / "out")


def test_onchip_col_repair_requires_num_spare_cols(tmp_path: Path) -> None:
    """The reverse of the existing num_spare_cols>0+onchip_selfrepair check:
    onchip_col_repair=true with num_spare_cols=0 must ALSO be rejected, not
    silently accepted -- otherwise has_onchip_col_repair would be true while
    has_col_repair (num_spare_cols>0) stays false in wrapper_template.j2,
    referencing sram_din_phys/sram_spare_wen that were never declared."""
    config = {
        **BASE_CONFIG,
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0, "onchip_selfrepair": True, "onchip_col_repair": True},
    }
    config_path = tmp_path / "bad_cols.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="onchip_col_repair requires redundancy.num_spare_cols > 0"):
        generate_from_config(config_path, tmp_path / "out")


def test_num_spare_cols_without_onchip_col_repair_still_rejected(tmp_path: Path) -> None:
    """Regression guard: the PRE-EXISTING rejection (num_spare_cols>0 +
    onchip_selfrepair, WITHOUT opting into onchip_col_repair) must still
    fire -- relaxing it to except onchip_col_repair must not have
    accidentally relaxed it further."""
    config = {**BASE_CONFIG, "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1, "onchip_selfrepair": True}}
    config_path = tmp_path / "bad_no_flag.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="onchip_col_repair is also true"):
        generate_from_config(config_path, tmp_path / "out")


def test_onchip_col_repair_rejects_persistence_combination(tmp_path: Path) -> None:
    config = {
        **BASE_CONFIG,
        "redundancy": {
            "num_spare_rows": 1, "num_spare_cols": 1,
            "onchip_selfrepair": True, "onchip_col_repair": True, "onchip_repair_persistence": True,
        },
    }
    config_path = tmp_path / "bad_persist.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="onchip_col_repair is not supported with"):
        generate_from_config(config_path, tmp_path / "out")


@pytest.mark.parametrize("bad", [1, "true", [], {}])
def test_onchip_col_repair_bad_type_rejected(tmp_path: Path, bad) -> None:
    config = {
        **BASE_CONFIG,
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1, "onchip_selfrepair": True, "onchip_col_repair": bad},
    }
    config_path = tmp_path / "bad_type.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="boolean"):
        generate_from_config(config_path, tmp_path / "out")


_PORTS_2RW_COL = {
    "porta": {
        "type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0",
        "spare_wen": "spare_wen0",
    },
    "portb": {
        "type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1",
        "spare_wen": "spare_wen1",
    },
}


def test_onchip_col_repair_2rw_render_has_new_symbols(tmp_path: Path) -> None:
    """march-2rw's two ports are both fully read/write and can write
    DIFFERENT addresses the same cycle (march_2rw_algo.sv's E1/E4), so
    -- unlike march-1r1w's single shared cross-wired instance -- it needs
    TWO independent repair_remap_col instances, one per port, each with its
    own spare_wen. The per-port wiring pins asserted here are the load-
    bearing check: march-2rw's own algorithm structure makes a swapped
    din_in/dout_in source between the two instances behaviorally
    undetectable by simulation (both ports write the same value whenever
    both write concurrently, and read the same address whenever both read
    concurrently) -- only these render-text assertions can catch that
    class of bug."""
    config = {
        "memory_name": "sram_spares_col_tiny_2rw",
        "wrapper_module_name": "sram_spares_col_tiny_2rw_mbist",
        "addr_width": 2,
        "data_width": 4,
        "we_active_low": True,
        "ports": _PORTS_2RW_COL,
        "redundancy": {
            "num_spare_rows": 1, "num_spare_cols": 1, "onchip_selfrepair": True, "onchip_col_repair": True,
        },
    }
    config_path = tmp_path / "col_2rw.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    text = generate_from_config(config_path, tmp_path / "out", algo="march-2rw").read_text(encoding="utf-8")

    assert "march_2rw_top #(" in text
    assert "onchip_2d_repair_analyzer #(" in text
    assert "onchip_row_repair_analyzer" not in text
    assert ".bist_fail_bitmask(algo_fail_bitmask)," in text
    # TWO independent instances (vs. 1r1w's ONE shared instance), row remap
    # stays per-port and unchanged.
    assert text.count("repair_remap_col #(") == 2
    assert text.count("repair_remap_row #(") == 2
    # The exact per-port wiring: each instance's din_in/dout_out come from
    # THAT port's own logical wire, its din_out/dout_in from THAT port's own
    # physical wire, and its spare_wen from THAT port's own spare_wen wire --
    # a swapped source here is invisible to any behavioral test (see above).
    assert ".din_in(sram_din0)," in text
    assert ".din_in(sram_din1)," in text
    assert ".din_out(sram_din_phys0)," in text
    assert ".din_out(sram_din_phys1)," in text
    assert ".dout_in(sram_dout_phys0)," in text
    assert ".dout_in(sram_dout_phys1)," in text
    assert ".dout_out(sram_dout0)," in text
    assert ".dout_out(sram_dout1)," in text
    assert ".spare_wen(sram_spare_wen0)," in text
    assert ".spare_wen(sram_spare_wen1)," in text
    # And the memory's own physical pins take the matching per-port wires.
    assert ".spare_wen0(sram_spare_wen0)," in text
    assert ".spare_wen1(sram_spare_wen1)," in text
    assert ".NUM_SPARE_COLS(1)" in text


def test_onchip_col_repair_2rw_requires_spare_wen_on_both_ports(tmp_path: Path) -> None:
    """Each rw port independently commits its own writes (sram_model_2rw.sv),
    so -- unlike the single-port/1r1w "at least one port" rule -- BOTH ports
    must declare spare_wen here, or a row written only through the port
    lacking it would never get its spare lane written."""
    ports = {**_PORTS_2RW_COL, "portb": {k: v for k, v in _PORTS_2RW_COL["portb"].items() if k != "spare_wen"}}
    config = {
        "memory_name": "sram_spares_col_tiny_2rw",
        "wrapper_module_name": "sram_spares_col_tiny_2rw_mbist",
        "addr_width": 2,
        "data_width": 4,
        "we_active_low": True,
        "ports": ports,
        "redundancy": {
            "num_spare_rows": 1, "num_spare_cols": 1, "onchip_selfrepair": True, "onchip_col_repair": True,
        },
    }
    config_path = tmp_path / "bad_2rw_spare_wen.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="spare_wen on EVERY rw port"):
        generate_from_config(config_path, tmp_path / "out", algo="march-2rw")


def test_onchip_col_repair_2rw_without_flag_unaffected(tmp_path: Path) -> None:
    """Row-only onchip_selfrepair for march-2rw (no onchip_col_repair key)
    must render EXACTLY as before this workstream -- the regression-critical
    case, matching test_row_only_onchip_unaffected_by_the_new_flag above."""
    config = {
        "memory_name": "sram_spares_tiny_2rw",
        "wrapper_module_name": "sram_spares_tiny_2rw_mbist",
        "addr_width": 6,
        "data_width": 8,
        "we_active_low": True,
        "ports": {
            "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
            "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"},
        },
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0, "onchip_selfrepair": True},
    }
    config_path = tmp_path / "row_only_2rw.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    text = generate_from_config(config_path, tmp_path / "out", algo="march-2rw").read_text(encoding="utf-8")

    assert "onchip_row_repair_analyzer #(" in text
    assert "onchip_2d_repair_analyzer" not in text
    assert "repair_remap_col" not in text
    assert "bist_fail_bitmask" not in text


_PORTS_1R1W_COL = {
    "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
    "wport": {
        "type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1",
        "spare_wen": "spare_wen1",
    },
}


def test_onchip_col_repair_1r1w_render_has_new_symbols(tmp_path: Path) -> None:
    """march-1r1w's read port compares exactly like the four single-port
    algos (see march_1r1w_fsm.sv), so it is column-repair capable -- but the
    multi-port wrapper branch had never carried a repair_remap_col instance
    before (no tester-driven multi-port path exists), so this exercises
    genuinely new template logic: ONE repair_remap_col cross-wired across
    the write port (din/spare_wen) and the read port (dout), not per-port
    like repair_remap_row."""
    config = {
        "memory_name": "sram_spares_col_tiny_1r1w",
        "wrapper_module_name": "sram_spares_col_tiny_1r1w_mbist",
        "addr_width": 2,
        "data_width": 4,
        "we_active_low": True,
        "ports": _PORTS_1R1W_COL,
        "redundancy": {
            "num_spare_rows": 1, "num_spare_cols": 1, "onchip_selfrepair": True, "onchip_col_repair": True,
        },
    }
    config_path = tmp_path / "col_1r1w.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    text = generate_from_config(config_path, tmp_path / "out", algo="march-1r1w").read_text(encoding="utf-8")

    assert "march_1r1w_top #(" in text
    assert "onchip_2d_repair_analyzer #(" in text
    assert "onchip_row_repair_analyzer" not in text
    assert ".bist_fail_bitmask(algo_fail_bitmask)," in text
    # ONE repair_remap_col instance (not one per port, unlike repair_remap_row).
    assert text.count("repair_remap_col #(") == 1
    assert text.count("repair_remap_row #(") == 2
    # Cross-port wiring: write port's logical din feeds din_in, read port's
    # logical dout receives dout_out.
    assert ".din_in(sram_din1)," in text
    assert ".dout_out(sram_dout0)," in text
    # The memory's own physical pins take the widened phys signals + spare_wen.
    assert ".dout0(sram_dout_phys0)," in text
    assert ".spare_wen1(sram_spare_wen)," in text
    assert ".din1(sram_din_phys1)" in text
    assert ".NUM_SPARE_COLS(1)" in text


def test_onchip_col_repair_1r1w_without_flag_unaffected(tmp_path: Path) -> None:
    """Row-only onchip_selfrepair for march-1r1w (no onchip_col_repair key)
    must render EXACTLY as before this workstream -- the regression-critical
    case, matching test_row_only_onchip_unaffected_by_the_new_flag above."""
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
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0, "onchip_selfrepair": True},
    }
    config_path = tmp_path / "row_only_1r1w.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    text = generate_from_config(config_path, tmp_path / "out", algo="march-1r1w").read_text(encoding="utf-8")

    assert "onchip_row_repair_analyzer #(" in text
    assert "onchip_2d_repair_analyzer" not in text
    assert "repair_remap_col" not in text
    assert "bist_fail_bitmask" not in text
