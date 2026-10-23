"""redundancy.onchip_analyzer: validation, and what the wrapper wires for the
greedy default and the deferred two-pass mode (no simulation)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autombist.generator import ConfigError, generate_from_config

PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0",
         "spare_wen": "spare_wen0"}


def _config(**redundancy) -> dict:
    return {
        "memory_name": "sram_tiny", "wrapper_module_name": "sram_tiny_mbist", "addr_width": 2, "data_width": 4,
        "we_active_low": True, "ports": PORTS,
        "redundancy": {"num_spare_rows": 2, "num_spare_cols": 1, "onchip_selfrepair": True,
                       "onchip_col_repair": True, **redundancy},
    }


def _render(tmp_path: Path, config: dict) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(path, tmp_path / "out").read_text(encoding="utf-8")


def test_the_greedy_default_ties_the_defer_ports_off(tmp_path: Path) -> None:
    text = _render(tmp_path, _config())
    assert ".DEFERRED(1)" not in text and "TWO_PASS" not in text
    assert ".NUM_SPARE_COLS(1)\n    ) u_onchip_analyzer (" in text  # the default layout is unchanged
    assert ".defer(1'b0)" in text and ".defer()" in text
    assert "logic                  defer;" not in text


def test_deferred_builds_the_two_pass_analyzer_and_sequencer(tmp_path: Path) -> None:
    text = _render(tmp_path, _config(onchip_analyzer="deferred"))
    assert ".NUM_SPARE_COLS(1),\n        .DEFERRED(1)\n    ) u_onchip_analyzer (" in text
    assert "onchip_selfrepair_ctrl #(\n        .MAX_RETRIES(0),\n        .TWO_PASS(1)\n    ) u_onchip_selfrepair_ctrl (" in text
    assert text.count(".defer(defer)") == 2 and "logic                  defer;" in text


def test_deferred_combines_with_spare_retry(tmp_path: Path) -> None:
    text = _render(tmp_path, _config(onchip_analyzer="deferred", onchip_spare_retry=True))
    assert ".MAX_RETRIES(3),\n        .TWO_PASS(1)" in text and ".retire(retire)," in text


@pytest.mark.parametrize(("redundancy", "fragment"), [
    ({"onchip_analyzer": "clever"}, "must be 'greedy' or 'deferred'"),
    ({"onchip_analyzer": "deferred", "num_spare_cols": 0, "onchip_col_repair": False}, "needs onchip_col_repair"),
])
def test_bad_settings_are_refused(tmp_path: Path, redundancy: dict, fragment: str) -> None:
    config = _config(**redundancy)
    if not config["redundancy"]["num_spare_cols"]:
        config["ports"] = {k: v for k, v in PORTS.items() if k != "spare_wen"}
    with pytest.raises(ConfigError, match=fragment):
        _render(tmp_path, config)
