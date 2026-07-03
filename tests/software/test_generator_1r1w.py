"""Phase 3 of multi-port memory support: generator.py validation for the new
``march-1r1w`` algo -- port-count/role cross-validation, and confirmation that
every other (single-port) algo's existing behavior is unchanged.

Fast, software-only (no simulator). The real generate -> simulate path is
covered by tests/integration/test_1r1w_e2e.py.
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
    ConfigError,
    _algo_port_suffixes,
    _normalize_algo,
    _validate_port_topology,
    generate_from_config,
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

R_PORT = {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"}
W_PORT = {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"}
R_PORT_2 = {"type": "r", "clk": "clk2", "addr": "addr2", "dout": "dout2", "csb": "csb2"}
W_PORT_2 = {"type": "w", "clk": "clk3", "addr": "addr3", "din": "din3", "csb": "csb3", "we": "web3"}
RW_PORT = {"type": "rw", **LEGACY_PORTS}


def _config_1r1w(memory_name: str = "sram_1r1w_dut") -> dict[str, object]:
    return {
        "memory_name": memory_name,
        "wrapper_module_name": f"{memory_name}_mbist",
        "addr_width": 6,
        "data_width": 8,
        "we_active_low": True,
        "ports": {"rport": dict(R_PORT), "wport": dict(W_PORT)},
    }


# ---------------------------------------------------------------------------
# _normalize_algo: march-1r1w is now selectable
# ---------------------------------------------------------------------------


def test_normalize_algo_accepts_march_1r1w() -> None:
    algo_dir, algo_top_module = _normalize_algo("march-1r1w")
    assert algo_dir == "march_1r1w"
    assert algo_top_module == "march_1r1w_top"


def test_normalize_algo_still_accepts_existing_algos() -> None:
    assert _normalize_algo("march-c") == ("march_c", "march_c_top")
    assert _normalize_algo("march-raw") == ("march_raw", "march_raw_top")


def test_normalize_algo_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="march-1r1w"):
        _normalize_algo("bogus-algo")


# ---------------------------------------------------------------------------
# _validate_port_topology: direct unit coverage of the cross-validation rule
# ---------------------------------------------------------------------------


def test_topology_march_1r1w_accepts_one_r_one_w() -> None:
    ports = {"rport": R_PORT, "wport": W_PORT}
    _validate_port_topology(ports, "march-1r1w")  # must not raise


@pytest.mark.parametrize("algo", ["march-c", "march-raw"])
def test_topology_single_port_algo_accepts_exactly_one_port(algo: str) -> None:
    ports = {"p0": RW_PORT}
    _validate_port_topology(ports, algo)  # must not raise


@pytest.mark.parametrize("algo", ["march-c", "march-raw"])
def test_topology_single_port_algo_rejects_two_ports(algo: str) -> None:
    ports = {"rport": R_PORT, "wport": W_PORT}
    with pytest.raises(ConfigError, match="requires exactly 1 port"):
        _validate_port_topology(ports, algo)


@pytest.mark.parametrize("algo", ["march-c", "march-raw"])
def test_topology_single_port_algo_rejects_three_ports(algo: str) -> None:
    ports = {"p0": RW_PORT, "p1": dict(R_PORT), "p2": dict(W_PORT)}
    with pytest.raises(ConfigError, match="requires exactly 1 port"):
        _validate_port_topology(ports, algo)


def test_topology_march_1r1w_rejects_one_port() -> None:
    ports = {"p0": RW_PORT}
    with pytest.raises(ConfigError, match="requires exactly 2 ports"):
        _validate_port_topology(ports, "march-1r1w")


def test_topology_march_1r1w_rejects_three_ports() -> None:
    ports = {"rport": R_PORT, "wport": W_PORT, "rport2": R_PORT_2}
    with pytest.raises(ConfigError, match="requires exactly 2 ports"):
        _validate_port_topology(ports, "march-1r1w")


def test_topology_march_1r1w_rejects_two_r_ports() -> None:
    ports = {"rport": R_PORT, "rport2": R_PORT_2}
    with pytest.raises(ConfigError, match="requires exactly one port of each type"):
        _validate_port_topology(ports, "march-1r1w")


def test_topology_march_1r1w_rejects_two_w_ports() -> None:
    ports = {"wport": W_PORT, "wport2": W_PORT_2}
    with pytest.raises(ConfigError, match="requires exactly one port of each type"):
        _validate_port_topology(ports, "march-1r1w")


def test_topology_march_1r1w_rejects_rw_plus_r() -> None:
    ports = {"p0": RW_PORT, "rport": R_PORT}
    with pytest.raises(ConfigError, match="requires exactly one port of each type"):
        _validate_port_topology(ports, "march-1r1w")


def test_topology_march_1r1w_rejects_rw_plus_w() -> None:
    ports = {"p0": RW_PORT, "wport": W_PORT}
    with pytest.raises(ConfigError, match="requires exactly one port of each type"):
        _validate_port_topology(ports, "march-1r1w")


# ---------------------------------------------------------------------------
# _algo_port_suffixes
# ---------------------------------------------------------------------------


def test_algo_port_suffixes_single_port_is_always_0() -> None:
    ports = {"p0": RW_PORT}
    assert _algo_port_suffixes(ports, "march-c") == {"p0": "0"}
    assert _algo_port_suffixes(ports, "march-raw") == {"p0": "0"}


def test_algo_port_suffixes_1r1w_maps_r_to_0_and_w_to_1() -> None:
    ports = {"rport": R_PORT, "wport": W_PORT}
    assert _algo_port_suffixes(ports, "march-1r1w") == {"rport": "0", "wport": "1"}


def test_algo_port_suffixes_1r1w_independent_of_map_order() -> None:
    ports = {"wport": W_PORT, "rport": R_PORT}
    assert _algo_port_suffixes(ports, "march-1r1w") == {"rport": "0", "wport": "1"}


# ---------------------------------------------------------------------------
# generate_from_config: end-to-end validation wiring (still software-only --
# no simulator invoked)
# ---------------------------------------------------------------------------


def test_generate_from_config_march_1r1w_happy_path(tmp_path: Path) -> None:
    config = _config_1r1w()
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    wrapper_path = generate_from_config(config_path, outdir, algo="march-1r1w")

    assert wrapper_path.exists()
    text = wrapper_path.read_text(encoding="utf-8")
    assert "march_1r1w_top" in text
    assert ".sram_clk0(" in text
    assert ".sram_web1(" in text
    # RTL for the selected algo family got copied into the output dir.
    assert (wrapper_path.parent / "march_1r1w" / "march_1r1w_top.sv").exists()
    assert (wrapper_path.parent / "sram_model_1r1w.sv").exists()


@pytest.mark.parametrize("port_count", [1, 3])
def test_generate_from_config_march_1r1w_rejects_wrong_port_count(tmp_path: Path, port_count: int) -> None:
    config = _config_1r1w()
    if port_count == 1:
        config["ports"] = {"rport": dict(R_PORT)}
    else:
        config["ports"] = {"rport": dict(R_PORT), "wport": dict(W_PORT), "rport2": dict(R_PORT_2)}
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    with pytest.raises(ConfigError, match="requires exactly 2 ports"):
        generate_from_config(config_path, outdir, algo="march-1r1w")


@pytest.mark.parametrize(
    "bad_ports",
    [
        {"rport": R_PORT, "rport2": R_PORT_2},
        {"wport": W_PORT, "wport2": W_PORT_2},
        {"p0": RW_PORT, "rport": R_PORT},
    ],
    ids=["two-r", "two-w", "rw-plus-r"],
)
def test_generate_from_config_march_1r1w_rejects_wrong_role_composition(
    tmp_path: Path, bad_ports: dict[str, object]
) -> None:
    config = _config_1r1w()
    config["ports"] = {name: dict(pdata) for name, pdata in bad_ports.items()}
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    with pytest.raises(ConfigError, match="requires exactly one port of each type"):
        generate_from_config(config_path, outdir, algo="march-1r1w")


@pytest.mark.parametrize("algo", ["march-c", "march-raw"])
def test_generate_from_config_single_port_algos_still_require_exactly_one_port(
    tmp_path: Path, algo: str
) -> None:
    """Existing single-port algos are unaffected by the march-1r1w relaxation:
    still exactly 1 port, and the legacy flat-dict config still works
    end-to-end (wrapper renders, RTL copied) exactly as before."""
    config = dict(BASE_CONFIG)
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    wrapper_path = generate_from_config(config_path, outdir, algo=algo)
    assert wrapper_path.exists()

    # A 2-port config is still rejected for these algos.
    config2 = dict(BASE_CONFIG)
    config2["memory_name"] = "sram_1rw_2"
    config2["wrapper_module_name"] = "sram_1rw_2_mbist"
    config2["ports"] = {"rport": dict(R_PORT), "wport": dict(W_PORT)}
    config_path2 = tmp_path / "config2.yml"
    _write_yaml(config_path2, config2)
    with pytest.raises(ConfigError, match="requires exactly 1 port"):
        generate_from_config(config_path2, outdir, algo=algo)
