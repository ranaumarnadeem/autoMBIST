"""Phase 1 of multi-port memory support: config schema + validation only.

Covers the named multi-port ``ports:`` map (``{name: {type: rw|r|w, ...}}``),
its backward-compatible coexistence with the legacy flat 6-key single-port
dict, and the `generate_from_config` guard that (for now, until Phase 3 wires
real multi-port RTL/templates) rejects any config with more than one port.
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

from autombist.generator import (
    PORT_KEYS_BY_TYPE,
    ConfigError,
    _normalize_ports,
    _validate_port,
    generate_from_config,
    load_config,
)


def _write_yaml(path: Path, content: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")


LEGACY_PORTS = {
    "clk": "clk0",
    "addr": "addr0",
    "din": "din0",
    "dout": "dout0",
    "we": "we0",
    "csb": "csb0",
}

BASE_CONFIG = {
    "memory_name": "sram_1rw",
    "wrapper_module_name": "sram_1rw_mbist",
    "addr_width": 10,
    "data_width": 32,
    "we_active_low": True,
    "ports": LEGACY_PORTS,
}

RW_1R1W_PORTS = {
    "portA": {"type": "r", "clk": "clkA", "addr": "addrA", "dout": "doutA", "csb": "csbA"},
    "portB": {"type": "w", "clk": "clkB", "addr": "addrB", "din": "dinB", "csb": "csbB", "we": "webB"},
}


# ---------------------------------------------------------------------------
# Legacy flat-dict backward compatibility
# ---------------------------------------------------------------------------


def test_legacy_flat_ports_unchanged_in_returned_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    _write_yaml(config_path, BASE_CONFIG)

    config = load_config(config_path)

    # loaded["ports"] stays byte-identical to the input for legacy configs --
    # no downstream template consumer has to change for single-port users.
    assert config["ports"] == LEGACY_PORTS


def test_legacy_flat_ports_normalize_to_single_rw_port(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    _write_yaml(config_path, BASE_CONFIG)

    config = load_config(config_path)

    assert config["normalized_ports"] == {"p0": {"type": "rw", **LEGACY_PORTS}}


def test_generate_from_config_still_works_for_legacy_single_port(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, BASE_CONFIG)

    wrapper_path = generate_from_config(config_path, outdir)

    assert wrapper_path.exists()
    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "module sram_1rw_mbist" in wrapper_text
    assert ".clk0(mbist_sram_clk)" in wrapper_text


# ---------------------------------------------------------------------------
# Named multi-port map: happy path
# ---------------------------------------------------------------------------


def test_named_1r1w_ports_validate_and_normalize(tmp_path: Path) -> None:
    config = dict(BASE_CONFIG)
    config["ports"] = RW_1R1W_PORTS
    config_path = tmp_path / "config.yml"
    _write_yaml(config_path, config)

    loaded = load_config(config_path)

    assert set(loaded["normalized_ports"].keys()) == {"portA", "portB"}
    assert loaded["normalized_ports"]["portA"] == {
        "type": "r", "clk": "clkA", "addr": "addrA", "dout": "doutA", "csb": "csbA",
    }
    assert loaded["normalized_ports"]["portB"] == {
        "type": "w", "clk": "clkB", "addr": "addrB", "din": "dinB", "csb": "csbB", "we": "webB",
    }
    # Named-map configs get the canonical view mirrored into loaded["ports"] too.
    assert loaded["ports"] == loaded["normalized_ports"]


def test_normalize_ports_direct_rw_type_requires_all_six_keys() -> None:
    ports = {"p0": {"type": "rw", **LEGACY_PORTS}}
    normalized = _normalize_ports(ports)
    assert normalized["p0"] == {"type": "rw", **LEGACY_PORTS}


def test_normalize_ports_drops_unknown_extra_keys() -> None:
    ports = {"p0": {"type": "rw", **LEGACY_PORTS, "bogus_extra": "ignored"}}
    normalized = _normalize_ports(ports)
    assert "bogus_extra" not in normalized["p0"]


def test_validate_port_accepts_each_type() -> None:
    for ptype, keys in PORT_KEYS_BY_TYPE.items():
        pdata = {"type": ptype, **{k: f"{k}_sig" for k in keys}}
        result = _validate_port("p0", pdata, "ports")
        assert result["type"] == ptype
        assert set(result.keys()) == {"type", *keys}


def test_shared_clock_across_ports_is_allowed(tmp_path: Path) -> None:
    ports = {
        "portA": {"type": "r", "clk": "clk_shared", "addr": "addrA", "dout": "doutA", "csb": "csbA"},
        "portB": {"type": "w", "clk": "clk_shared", "addr": "addrB", "din": "dinB", "csb": "csbB", "we": "webB"},
    }
    normalized = _normalize_ports(ports)
    assert normalized["portA"]["clk"] == normalized["portB"]["clk"] == "clk_shared"


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


def test_rejects_bad_port_type() -> None:
    ports = {"p0": {"type": "bogus", "clk": "c", "addr": "a", "din": "d", "dout": "q", "we": "w", "csb": "s"}}
    with pytest.raises(ConfigError, match="type must be one of"):
        _normalize_ports(ports)


def test_rejects_missing_required_key_for_type() -> None:
    # "r" ports need dout; omit it.
    ports = {"p0": {"type": "r", "clk": "c", "addr": "a", "csb": "s"}}
    with pytest.raises(ConfigError, match="Missing required keys"):
        _normalize_ports(ports)


def test_rejects_non_mapping_port_entry() -> None:
    ports = {"p0": "not-a-dict"}
    with pytest.raises(ConfigError, match="must be a mapping"):
        _normalize_ports(ports)


def test_rejects_empty_string_signal() -> None:
    ports = {"p0": {"type": "rw", **{**LEGACY_PORTS, "clk": "  "}}}
    with pytest.raises(ConfigError, match="non-empty string"):
        _normalize_ports(ports)


def test_rejects_invalid_port_identifier() -> None:
    ports = {"1bad-name": {"type": "rw", **LEGACY_PORTS}}
    with pytest.raises(ConfigError, match="valid identifier"):
        _normalize_ports(ports)


def test_rejects_empty_ports_mapping() -> None:
    with pytest.raises(ConfigError, match="non-empty mapping"):
        _normalize_ports({})


def test_rejects_signal_role_collision_across_ports() -> None:
    # portA's addr signal is reused as portB's clk -- two different roles.
    ports = {
        "portA": {"type": "r", "clk": "clkA", "addr": "shared_sig", "dout": "doutA", "csb": "csbA"},
        "portB": {"type": "w", "clk": "shared_sig", "addr": "addrB", "din": "dinB", "csb": "csbB", "we": "webB"},
    }
    with pytest.raises(ConfigError, match="used for both role"):
        _normalize_ports(ports)


def test_rejects_signal_role_collision_within_single_port() -> None:
    ports = {"p0": {"type": "rw", **{**LEGACY_PORTS, "addr": LEGACY_PORTS["clk"]}}}
    with pytest.raises(ConfigError, match="used for both role"):
        _normalize_ports(ports)


def test_rejects_signal_role_collision_in_the_legacy_flat_form() -> None:
    """The flat spelling used to skip the collision check entirely: its branch
    returned before reaching the call, so the SAME memory was validated on one
    spelling and not the other.

    Note the test above does NOT cover this despite its name -- it wraps its
    ports in {"p0": {...}}, whose value is a dict, so _is_legacy_flat_ports is
    False and it takes the named path. That blind spot is why the gap survived.
    This one passes the bare flat dict (all values strings), which really does
    route to the flat branch."""
    with pytest.raises(ConfigError, match="used for both role"):
        _normalize_ports({**LEGACY_PORTS, "addr": LEGACY_PORTS["clk"]})


def test_rejects_duplicate_yaml_key_in_ports_map(tmp_path: Path) -> None:
    # Hand-write raw YAML with a literal duplicate key -- not expressible via
    # a Python dict, so this has to bypass yaml.safe_dump.
    raw = """
memory_name: sram_dup
wrapper_module_name: sram_dup_mbist
addr_width: 4
data_width: 8
we_active_low: true
ports:
  portA:
    type: r
    clk: clkA
    addr: addrA
    dout: doutA
    csb: csbA
  portA:
    type: w
    clk: clkB
    addr: addrB
    din: dinB
    csb: csbB
    we: webB
"""
    config_path = tmp_path / "config.yml"
    config_path.write_text(raw, encoding="utf-8")

    with pytest.raises(ConfigError, match="Duplicate key"):
        load_config(config_path)


def test_rejects_duplicate_yaml_key_at_top_level(tmp_path: Path) -> None:
    raw = """
memory_name: sram_dup
memory_name: sram_dup_again
wrapper_module_name: sram_dup_mbist
addr_width: 4
data_width: 8
we_active_low: true
ports:
  clk: clk0
  addr: addr0
  din: din0
  dout: dout0
  we: we0
  csb: csb0
"""
    config_path = tmp_path / "config.yml"
    config_path.write_text(raw, encoding="utf-8")

    with pytest.raises(ConfigError, match="Duplicate key"):
        load_config(config_path)


# ---------------------------------------------------------------------------
# generate_from_config: multi-port is only supported by algo=march-1r1w
# (Phase 3). Every other algo (the default march-c included) still requires
# exactly 1 port -- see test_generator_1r1w.py for the march-1r1w happy path.
# ---------------------------------------------------------------------------


def test_generate_from_config_rejects_multiport_for_single_port_algo(tmp_path: Path) -> None:
    config = dict(BASE_CONFIG)
    config["ports"] = RW_1R1W_PORTS
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    with pytest.raises(ConfigError, match="requires exactly 1 port"):
        generate_from_config(config_path, outdir)
