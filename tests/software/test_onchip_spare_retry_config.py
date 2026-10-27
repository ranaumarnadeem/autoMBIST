"""redundancy.onchip_spare_retry: validation, and what the wrapper wires with it
on and off (no simulation)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autombist.generator import ConfigError, generate_from_config

BASE = {
    "memory_name": "sram_tiny",
    "wrapper_module_name": "sram_tiny_mbist",
    "addr_width": 2,
    "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}


def _render(tmp_path: Path, config: dict, **kwargs) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(path, tmp_path / "out", **kwargs).read_text(encoding="utf-8")


def _redundancy(**extra) -> dict:
    return {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True, **extra}


def test_without_retry_the_new_ports_are_tied_off(tmp_path: Path) -> None:
    text = _render(tmp_path, {**BASE, "redundancy": _redundancy()})
    assert "onchip_selfrepair_ctrl u_onchip_selfrepair_ctrl (" in text  # no parameter override
    assert "MAX_RETRIES" not in text
    assert ".verify_enable(1'b0)," in text and ".retire(1'b0)," in text and ".has_suspect()" in text
    assert ".has_suspect(1'b0)" in text and ".diag_enable()," in text
    assert "logic                  retire;" not in text


def test_with_retry_the_sequencer_retires_up_to_one_spare_per_retry(tmp_path: Path) -> None:
    text = _render(tmp_path, {**BASE, "redundancy": _redundancy(onchip_spare_retry=True)})
    assert "onchip_selfrepair_ctrl #(\n        .MAX_RETRIES(2),\n        .TWO_PASS(0)\n    ) u_onchip_selfrepair_ctrl (" in text
    for net in ("verify_enable", "retire", "has_suspect", "diag_enable"):
        assert f"logic                  {net};" in text
    assert text.count(".verify_enable(verify_enable),") == 2  # analyzer and sequencer
    assert text.count(".retire(retire),") == 2
    assert ".has_suspect(has_suspect)" in text and ".diag_enable(diag_enable)," in text
    assert ".verify_enable(1'b0)" not in text


def test_with_column_repair_the_limit_counts_both_kinds_of_spare(tmp_path: Path) -> None:
    config = {
        **BASE,
        "ports": {**BASE["ports"], "spare_wen": "spare_wen0"},
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 2, "onchip_selfrepair": True,
                       "onchip_col_repair": True, "onchip_spare_retry": True},
    }
    text = _render(tmp_path, config)
    assert "onchip_2d_repair_analyzer #(" in text and ".MAX_RETRIES(3)" in text


def test_with_retry_the_diagnosis_log_keeps_the_first_analyze_pass(tmp_path: Path) -> None:
    retry = _render(tmp_path / "a", {**BASE, "redundancy": _redundancy(
        onchip_spare_retry=True, onchip_diagnosis=True, num_diagnosis_entries=4)})
    plain = _render(tmp_path / "b", {**BASE, "redundancy": _redundancy(
        onchip_diagnosis=True, num_diagnosis_entries=4)})
    diag = retry.split("onchip_diagnosis_log #(", 1)[1].split(");", 1)[0]
    assert ".enable(diag_enable)," in diag
    assert ".enable(registrar_enable)," in plain.split("onchip_diagnosis_log #(", 1)[1].split(");", 1)[0]


def test_retry_renders_for_a_multi_port_memory(tmp_path: Path) -> None:
    config = {
        "memory_name": "sram_spares_tiny_1r1w", "wrapper_module_name": "sram_spares_tiny_1r1w_mbist",
        "addr_width": 2, "data_width": 4, "we_active_low": True,
        "ports": {
            "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
            "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
        },
        "redundancy": _redundancy(onchip_spare_retry=True),
    }
    text = _render(tmp_path, config, algo="march-1r1w")
    assert ".MAX_RETRIES(2)" in text and ".retire(retire)," in text


@pytest.mark.parametrize(("redundancy", "fragment"), [
    ({"num_spare_rows": 2, "onchip_selfrepair": True, "onchip_spare_retry": "yes"}, "must be a boolean"),
    ({"num_spare_rows": 2, "onchip_spare_retry": True}, "requires onchip_selfrepair"),
])
def test_bad_settings_are_refused(tmp_path: Path, redundancy: dict, fragment: str) -> None:
    config = {**BASE, "redundancy": redundancy}
    if not redundancy.get("onchip_selfrepair"):
        config["repair_ports"] = [{"name": "row_repair_en", "width": 2, "dir": "input"},
                                  {"name": "faulty_row_addr", "width": 4, "dir": "input"}]
    with pytest.raises(ConfigError, match=fragment):
        _render(tmp_path, config)
