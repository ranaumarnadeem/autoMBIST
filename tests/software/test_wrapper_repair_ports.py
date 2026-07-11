"""Pin the optional `repair_ports:` passthrough seam in the wrapper generator.

`repair_ports` lets a redundant/repairable memory macro's extra pins (e.g.
repair_valid/repair_addr, driven by a future BISR) survive port normalisation --
they used to be silently dropped -- and be surfaced on the wrapper boundary and
bound straight through to the (non-saboteur) memory instance. The load-bearing
guarantees these tests fix:
  * a config WITHOUT repair_ports renders byte-identically (the block is opt-in),
  * with it, the pins appear on the boundary AND the u_sram binding,
  * malformed / colliding repair_ports are rejected loudly, not dropped.
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
    {"name": "repair_valid", "width": 2, "dir": "input"},
    {"name": "repair_addr", "width": 8, "dir": "input"},
]

# The exact lines the passthrough injects (boundary, leading-comma style, and
# the u_sram binding). Removing these from the with-repair render must reproduce
# the without-repair render byte-for-byte.
BOUNDARY_LINES = [
    "  , input logic [2-1:0] repair_valid\n",
    "  , input logic [8-1:0] repair_addr\n",
]
BINDING_LINES = [
    "      , .repair_valid(repair_valid)\n",
    "      , .repair_addr(repair_addr)\n",
]


def _render(tmp_path: Path, config: dict, subdir: str) -> str:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / subdir)
    return wrapper.read_text(encoding="utf-8")


def test_absent_repair_ports_renders_without_repair(tmp_path: Path) -> None:
    text = _render(tmp_path, BASE_CONFIG, "base")
    assert "repair_" not in text
    # sanity: the boundary and binding are intact
    assert "output logic [DATA_WIDTH-1:0] func_dout\n);" in text
    assert ".dout0(sram_dout)\n    );" in text


def test_repair_ports_appear_on_boundary_and_binding(tmp_path: Path) -> None:
    config = {**BASE_CONFIG, "repair_ports": REPAIR_PORTS}
    text = _render(tmp_path, config, "with_rp")
    for line in BOUNDARY_LINES:
        assert line in text, f"missing boundary pin: {line!r}"
    for line in BINDING_LINES:
        assert line in text, f"missing u_sram binding: {line!r}"


def test_repair_ports_are_byte_identical_except_the_injected_lines(tmp_path: Path) -> None:
    """The strongest guard: the ONLY difference between the with-repair and
    without-repair renders is exactly the injected repair lines. This proves the
    opt-in block perturbs nothing else in the wrapper."""
    base = _render(tmp_path, BASE_CONFIG, "base")
    with_rp = _render(tmp_path, {**BASE_CONFIG, "repair_ports": REPAIR_PORTS}, "with_rp")

    stripped = with_rp
    for line in BOUNDARY_LINES + BINDING_LINES:
        assert stripped.count(line) == 1, f"expected exactly one {line!r}"
        stripped = stripped.replace(line, "", 1)
    assert stripped == base


def test_output_direction_is_honored(tmp_path: Path) -> None:
    config = {**BASE_CONFIG, "repair_ports": [{"name": "repair_ack", "width": 1, "dir": "output"}]}
    text = _render(tmp_path, config, "out_rp")
    assert "  , output logic [1-1:0] repair_ack\n" in text


# --------------------------------------------------------------------------- #
# Validation teeth: bad repair_ports must be rejected, never silently dropped.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad",
    [
        [],                                                    # empty list
        [{"width": 2}],                                        # missing name
        [{"name": "1bad", "width": 2}],                        # invalid identifier
        [{"name": "func_dout", "width": 2}],                   # reserved boundary pin
        [{"name": "clk", "width": 1}],                         # reserved pin
        [{"name": "r", "width": 0}],                           # non-positive width
        [{"name": "r", "width": True}],                        # bool width
        [{"name": "r", "dir": "inout"}],                       # bad direction
        [{"name": "dup"}, {"name": "dup"}],                    # duplicate name
        "notalist",                                            # wrong type
    ],
)
def test_bad_repair_ports_rejected(tmp_path: Path, bad) -> None:
    config = {**BASE_CONFIG, "repair_ports": bad}
    config_path = tmp_path / "bad.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError):
        generate_from_config(config_path, tmp_path / "out")
