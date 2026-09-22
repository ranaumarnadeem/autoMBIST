"""Pin the `topology:`/`memories:` config-schema validation
(generator._validate_shared_memories) -- step 0 of
docs/shared-hierarchical-mbist-plan.md's implementation order for a shared-
bus MBIST controller (one controller, N memories). Pure `load_config`-level
tests, matching test_redundancy_config.py's own convention: the RTL/
generation-pipeline wiring for `topology: shared-bus` is a later step, not
covered here.
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

from autombist.generator import ConfigError, load_config  # noqa: E402

BASE = {
    "wrapper_module_name": "sram_tiny_mbist",
    "addr_width": 2,
    "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}
DEDICATED = {**BASE, "memory_name": "sram_tiny"}
SHARED_BUS = {
    **BASE,
    "memory_name": "sram_tiny",  # the shared macro TYPE all `memories` entries instantiate
    "topology": "shared-bus",
    "memories": [{"name": "mem_bank0"}, {"name": "mem_bank1"}],
}


def _write(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _load(tmp_path: Path, config: dict) -> dict:
    return load_config(_write(tmp_path, config))


# --------------------------------------------------------------------------- #
# topology: dedicated (default) -- today's behaviour, unchanged
# --------------------------------------------------------------------------- #
def test_topology_absent_behaves_exactly_like_dedicated(tmp_path: Path) -> None:
    loaded = _load(tmp_path, DEDICATED)
    assert loaded["memory_name"] == "sram_tiny"
    assert "memories" not in loaded
    assert "topology" not in loaded  # not injected -- absence stays absence


def test_dedicated_still_requires_memory_name(tmp_path: Path) -> None:
    missing = {k: v for k, v in DEDICATED.items() if k != "memory_name"}
    with pytest.raises(ConfigError, match="memory_name"):
        _load(tmp_path, missing)


def test_memories_rejected_under_dedicated_topology(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="memories is only valid under topology: shared-bus"):
        _load(tmp_path, {**DEDICATED, "memories": [{"name": "mem_bank0"}]})


# --------------------------------------------------------------------------- #
# topology: shared-bus
# --------------------------------------------------------------------------- #
def test_valid_shared_bus_config_normalizes_memories(tmp_path: Path) -> None:
    loaded = _load(tmp_path, SHARED_BUS)
    assert loaded["memories"] == [{"name": "mem_bank0"}, {"name": "mem_bank1"}]
    # memory_name is REQUIRED under shared-bus too -- it names the shared
    # macro type every memories[] entry instantiates, a different concept
    # from each entry's own per-instance name (corrected from an earlier,
    # wrong "forbidden under shared-bus" draft -- see the docstring above
    # _validate_shared_memories).
    assert loaded["memory_name"] == "sram_tiny"


def test_shared_bus_requires_memory_name(tmp_path: Path) -> None:
    missing = {k: v for k, v in SHARED_BUS.items() if k != "memory_name"}
    with pytest.raises(ConfigError, match="memory_name"):
        _load(tmp_path, missing)


def test_shared_bus_requires_memories(tmp_path: Path) -> None:
    missing = {k: v for k, v in SHARED_BUS.items() if k != "memories"}
    with pytest.raises(ConfigError, match="memories must be a non-empty list"):
        _load(tmp_path, missing)


def test_shared_bus_rejects_empty_memories_list(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="memories must be a non-empty list"):
        _load(tmp_path, {**SHARED_BUS, "memories": []})


def test_shared_bus_rejects_duplicate_memory_names(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="duplicate memory name 'mem_bank0'"):
        _load(tmp_path, {**SHARED_BUS, "memories": [{"name": "mem_bank0"}, {"name": "mem_bank0"}]})


def test_shared_bus_rejects_non_identifier_memory_name(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"memories\[0\]\.name must be a valid identifier"):
        _load(tmp_path, {**SHARED_BUS, "memories": [{"name": "not an identifier"}]})


def test_shared_bus_rejects_non_mapping_entry(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"memories\[1\] must be a mapping"):
        _load(tmp_path, {**SHARED_BUS, "memories": [{"name": "mem_bank0"}, "not-a-dict"]})


# --------------------------------------------------------------------------- #
# topology itself
# --------------------------------------------------------------------------- #
def test_invalid_topology_value_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="topology must be one of"):
        _load(tmp_path, {**DEDICATED, "topology": "bogus"})


def test_explicit_topology_dedicated_is_accepted(tmp_path: Path) -> None:
    loaded = _load(tmp_path, {**DEDICATED, "topology": "dedicated"})
    assert loaded["memory_name"] == "sram_tiny"
