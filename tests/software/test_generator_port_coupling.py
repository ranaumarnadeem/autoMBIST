"""Unit tests for generator.py's port-coupling wiring: the fault_type
validation set, the new _validate_port_coupling_topology march-1r1w-only
gate, and the render_config["pc_faults_file"] wiring inside
generate_from_config.

Fast, software-only (no simulator). The real generate -> simulate detection
path (including the differential concurrent-vs-sequential proof) is covered
by tests/hardware/run_port_coupling_diff_tb.py and the march-1r1w e2e sweep
in tests/integration/test_1r1w_e2e.py-style runs.
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
    _validate_port_coupling_topology,
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

R_PORT = {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"}
W_PORT = {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"}
R_PORT_2 = {"type": "r", "clk": "clk2", "addr": "addr2", "dout": "dout2", "csb": "csb2"}
RW_PORT = {"type": "rw", **LEGACY_PORTS}

BASE_SINGLE_PORT_CONFIG = {
    "memory_name": "sram_1rw",
    "wrapper_module_name": "sram_1rw_mbist",
    "addr_width": 10,
    "data_width": 32,
    "we_active_low": True,
    "ports": LEGACY_PORTS,
}


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
# _validate_port_coupling_topology: direct unit coverage
# ---------------------------------------------------------------------------


def test_port_coupling_topology_accepts_march_1r1w_with_r_and_w_ports() -> None:
    ports = {"rport": R_PORT, "wport": W_PORT}
    _validate_port_coupling_topology(ports, "march-1r1w")  # must not raise


@pytest.mark.parametrize("algo", ["march-c", "march-raw"])
def test_port_coupling_topology_rejects_non_1r1w_algos(algo: str) -> None:
    ports = {"p0": RW_PORT}
    with pytest.raises(ConfigError, match="requires algo=march-1r1w"):
        _validate_port_coupling_topology(ports, algo)


def test_port_coupling_topology_rejects_single_rw_port_even_under_1r1w_name() -> None:
    """Defense in depth: even if somehow called with a single rw port under
    algo=march-1r1w, the role-composition check must still reject it."""
    ports = {"p0": RW_PORT}
    with pytest.raises(ConfigError, match="requires exactly one read-only"):
        _validate_port_coupling_topology(ports, "march-1r1w")


def test_port_coupling_topology_rejects_two_r_ports() -> None:
    ports = {"rport": R_PORT, "rport2": R_PORT_2}
    with pytest.raises(ConfigError, match="requires exactly one read-only"):
        _validate_port_coupling_topology(ports, "march-1r1w")


def test_port_coupling_topology_case_insensitive_algo_name() -> None:
    ports = {"rport": R_PORT, "wport": W_PORT}
    _validate_port_coupling_topology(ports, "March-1R1W")  # must not raise


# ---------------------------------------------------------------------------
# generate_from_config: fault_type validation set
# ---------------------------------------------------------------------------


def test_generate_from_config_accepts_port_coupling_string(tmp_path: Path) -> None:
    config = _config_1r1w()
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    wrapper_path = generate_from_config(
        config_path, outdir, use_saboteur=True, faults=2, fault_seed=1,
        fault_type="port-coupling", algo="march-1r1w",
    )
    assert wrapper_path.exists()


def test_generate_from_config_rejects_unknown_fault_type_string(tmp_path: Path) -> None:
    config = _config_1r1w()
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    with pytest.raises(ValueError, match="port-coupling"):
        generate_from_config(config_path, outdir, fault_type="bogus-fault-type", algo="march-1r1w")


# ---------------------------------------------------------------------------
# generate_from_config: port-coupling x topology cross-validation
# ---------------------------------------------------------------------------


def test_generate_from_config_port_coupling_rejected_for_single_port_config(tmp_path: Path) -> None:
    """fault_type=port-coupling on a legacy single (rw) port config must be
    rejected with a clear ConfigError -- out of scope beyond march-1r1w. The
    algo mismatch (single-port configs only pair with non-1r1w algos) is
    caught first, by the port-coupling-specific algo gate."""
    config = dict(BASE_SINGLE_PORT_CONFIG)
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    with pytest.raises(ConfigError, match="requires algo=march-1r1w"):
        generate_from_config(
            config_path, outdir, use_saboteur=True, faults=1,
            fault_type="port-coupling", algo="march-c",
        )


def test_generate_from_config_port_coupling_rejected_when_algo_is_not_1r1w(tmp_path: Path) -> None:
    """Even with a 2-port config, port-coupling must be rejected unless
    algo=march-1r1w is also selected (out of scope for march-2rw/other
    multi-port topologies in this phase)."""
    config = _config_1r1w()
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    # march-c/march-raw already reject 2-port configs at the generic
    # topology-validation stage (which runs before the port-coupling-specific
    # check), so this still surfaces as a ConfigError either way.
    with pytest.raises(ConfigError):
        generate_from_config(
            config_path, outdir, use_saboteur=True, faults=1,
            fault_type="port-coupling", algo="march-c",
        )


def test_generate_from_config_port_coupling_accepted_for_march_1r1w(tmp_path: Path) -> None:
    config = _config_1r1w()
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    wrapper_path = generate_from_config(
        config_path, outdir, use_saboteur=True, faults=4, fault_seed=2,
        fault_type="port-coupling", algo="march-1r1w",
    )

    assert wrapper_path.exists()
    module_outdir = wrapper_path.parent
    assert (module_outdir / "faults" / "pc_mask.hex").exists()

    saboteur_path = module_outdir / f"{config['memory_name']}_saboteur.v"
    assert saboteur_path.exists()
    saboteur_text = saboteur_path.read_text(encoding="utf-8")
    assert "pc_mask" in saboteur_text
    assert "coupling_bits_q" in saboteur_text


def test_generate_from_config_port_coupling_snapshot_has_pc_faults_file(tmp_path: Path) -> None:
    """render_config["pc_faults_file"] must be wired through to the config
    snapshot autombist writes alongside the wrapper."""
    config = _config_1r1w()
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    wrapper_path = generate_from_config(
        config_path, outdir, use_saboteur=True, faults=1, fault_seed=1,
        fault_type="port-coupling", algo="march-1r1w",
    )
    snapshot = yaml.safe_load((wrapper_path.parent / "config.yml").read_text(encoding="utf-8"))
    assert "pc_faults_file" in snapshot
    assert snapshot["pc_faults_file"].endswith("pc_mask.hex")


def test_generate_from_config_port_coupling_faults_zero_still_generates(tmp_path: Path) -> None:
    """faults=0 with fault_type=port-coupling must still generate a
    (all-zero) mask and a valid saboteur, matching the other fault types'
    zero-fault behavior."""
    config = _config_1r1w()
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, config)

    wrapper_path = generate_from_config(
        config_path, outdir, use_saboteur=True, faults=0,
        fault_type="port-coupling", algo="march-1r1w",
    )
    mask_path = wrapper_path.parent / "faults" / "pc_mask.hex"
    assert mask_path.exists()
    lines = mask_path.read_text(encoding="ascii").splitlines()
    assert all(line == "00" for line in lines)
