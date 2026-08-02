"""Pin the `redundancy:` config block validation (generator._validate_redundancy).

`redundancy: {num_spare_rows: N, num_spare_cols: 0}` is the single source that
populates the shared SpareGeometry both the wrapper generator and BIRA read. It
is paired with `repair_ports` (which drives the external remap). These pure tests
fix the block's rules; the RTL wiring it produces is covered by
test_wrapper_repair_ports.py and the e2e in test_repair_row_e2e.py.
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

BASE = {
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
MULTI_PORTS = {
    "r0": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
    "w0": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "we1"},
}


def _write(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _load(tmp_path: Path, config: dict) -> dict:
    return load_config(_write(tmp_path, config))


# --------------------------------------------------------------------------- #
# Valid + absent
# --------------------------------------------------------------------------- #
def test_valid_redundancy_derives_geometry(tmp_path: Path) -> None:
    loaded = _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 2}, "repair_ports": REPAIR_PORTS})
    r = loaded["redundancy"]
    assert r == {
        "base_words": 4,        # 1 << addr_width(2)
        "word_size": 4,
        "num_spare_rows": 2,
        "num_spare_cols": 0,
        "mem_addr_width": 3,    # ceil(log2(4 + 2))
        "onchip_selfrepair": False,
        "onchip_repair_persistence": False,
    }


def test_onchip_selfrepair_flows_through_the_rebuilt_dict(tmp_path: Path) -> None:
    """`_validate_redundancy` REBUILDS `loaded['redundancy']` from a fixed key
    list rather than updating the input block -- so a field read from the input
    but not ALSO added to that rebuilt dict would silently read as undefined in
    Jinja regardless of what the user set. This pins that `onchip_selfrepair`
    genuinely survives the rebuild (not just defaults correctly, per the test
    above)."""
    loaded = _load(
        tmp_path,
        {**BASE, "redundancy": {"num_spare_rows": 2, "onchip_selfrepair": True}},
    )
    assert loaded["redundancy"]["onchip_selfrepair"] is True


def test_absent_redundancy_leaves_no_key(tmp_path: Path) -> None:
    loaded = _load(tmp_path, BASE)
    assert "redundancy" not in loaded


# --------------------------------------------------------------------------- #
# The redundancy <-> repair_ports biconditional (both directions)
# --------------------------------------------------------------------------- #
def test_redundancy_without_repair_ports_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="repair_ports"):
        _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 2}})


def test_repair_ports_without_redundancy_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="redundancy"):
        _load(tmp_path, {**BASE, "repair_ports": REPAIR_PORTS})


# --------------------------------------------------------------------------- #
# Field rules
# --------------------------------------------------------------------------- #
def test_num_spare_cols_must_be_zero_this_phase(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="num_spare_cols"):
        _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1}, "repair_ports": REPAIR_PORTS})


def test_num_spare_rows_must_be_at_least_one(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="num_spare_rows"):
        _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 0}, "repair_ports": REPAIR_PORTS})


@pytest.mark.parametrize("bad_rows", [-1, True, "two", 1.5])
def test_num_spare_rows_must_be_non_negative_int(tmp_path: Path, bad_rows) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": bad_rows}, "repair_ports": REPAIR_PORTS})


def test_redundancy_must_be_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="redundancy"):
        _load(tmp_path, {**BASE, "redundancy": [1, 2], "repair_ports": REPAIR_PORTS})


def test_redundancy_rejected_for_multi_port(tmp_path: Path) -> None:
    config = {**BASE, "ports": MULTI_PORTS, "redundancy": {"num_spare_rows": 2}, "repair_ports": REPAIR_PORTS}
    with pytest.raises(ConfigError, match="single-port"):
        _load(tmp_path, config)


# --------------------------------------------------------------------------- #
# Cross-check: redundancy is incompatible with the saboteur (in generate_from_config)
# --------------------------------------------------------------------------- #
def test_redundancy_with_saboteur_rejected(tmp_path: Path) -> None:
    config_path = _write(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 2}, "repair_ports": REPAIR_PORTS})
    with pytest.raises(ConfigError, match="saboteur"):
        generate_from_config(config_path, tmp_path / "out", use_saboteur=True)
