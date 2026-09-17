"""Pin the `redundancy.onchip_diagnosis` config flag and the wrapper render it
selects.

`onchip_diagnosis: true` (requires `onchip_selfrepair: true`) adds a second,
independent consumer of the same fail_valid/fail_addr stream
onchip_row_repair_analyzer already reads: a full-range diagnosis log
(onchip_diagnosis_log), sized by `num_diagnosis_entries` rather than bounded
to the physical spare budget. It does not touch onchip_repair_persistence or
any tester-driven repair_ports path -- those are orthogonal, unaffected here.
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


def _render(tmp_path: Path, config: dict, subdir: str, **kwargs) -> str:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / subdir, **kwargs)
    return wrapper.read_text(encoding="utf-8")


def _onchip() -> dict:
    return {**BASE_CONFIG, "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True}}


def _onchip_with_diagnosis(num_diagnosis_entries: int = 4) -> dict:
    return {
        **BASE_CONFIG,
        "redundancy": {
            "num_spare_rows": 2, "num_spare_cols": 0,
            "onchip_selfrepair": True, "onchip_diagnosis": True,
            "num_diagnosis_entries": num_diagnosis_entries,
        },
    }


# --------------------------------------------------------------------------- #
# onchip_selfrepair alone: byte-identical, no diagnosis machinery leaks in
# --------------------------------------------------------------------------- #
def test_onchip_render_without_diagnosis_has_no_diagnosis_boundary_ports(tmp_path: Path) -> None:
    text = _render(tmp_path, _onchip(), "onchip_no_diag")
    assert "onchip_diagnosis_log" not in text
    assert "diag_valid" not in text
    assert "diag_addr" not in text
    assert "diag_overflow" not in text


# --------------------------------------------------------------------------- #
# The onchip_diagnosis=true render
# --------------------------------------------------------------------------- #
def test_onchip_diagnosis_render_has_new_symbols_and_boundary_ports(tmp_path: Path) -> None:
    text = _render(tmp_path, _onchip_with_diagnosis(), "onchip_diag")
    assert "onchip_diagnosis_log #(" in text
    assert ") u_onchip_diagnosis (" in text
    assert "  , output logic [4-1:0] diag_valid\n" in text
    assert "  , output logic [4*ADDR_WIDTH-1:0] diag_addr\n" in text
    assert "  , output logic diag_overflow\n" in text
    assert ".enable(registrar_enable)," in text
    assert ".fail_valid(algo_fail_valid)," in text
    assert ".fail_addr(algo_fail_addr)," in text
    assert ".latch_result(latch_result)," in text
    assert ".diag_valid(diag_valid)," in text
    assert ".diag_addr(diag_addr)," in text
    assert ".diag_overflow(diag_overflow)" in text
    # The repair analyzer is untouched -- diagnosis is additive, not a replacement.
    assert "onchip_row_repair_analyzer #(" in text
    assert ") u_onchip_analyzer (" in text


def test_onchip_diagnosis_uses_its_own_entry_count_not_num_spare_rows(tmp_path: Path) -> None:
    """num_diagnosis_entries is an independent parameter from num_spare_rows --
    the whole point is that the diagnosis log can be sized larger than the
    physical spare budget the repair analyzer is bounded to."""
    text = _render(tmp_path, _onchip_with_diagnosis(num_diagnosis_entries=6), "onchip_diag_sized")
    assert ".NUM_DIAGNOSIS_ENTRIES(6)" in text
    assert ".NUM_SPARE_ROWS(2)" in text  # the analyzer's own parameter, unchanged


def test_onchip_diagnosis_works_for_march_1r1w(tmp_path: Path) -> None:
    """The multi-port wrapper branch's analyzer instantiation gets the same
    diagnosis wiring treatment as the single-port branch."""
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
            "onchip_selfrepair": True, "onchip_diagnosis": True, "num_diagnosis_entries": 4,
        },
    }
    config_path = tmp_path / "onchip_1r1w_diag.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    text = generate_from_config(
        config_path, tmp_path / "onchip_1r1w_diag", algo="march-1r1w"
    ).read_text(encoding="utf-8")
    assert "onchip_diagnosis_log #(" in text
    assert ") u_onchip_diagnosis (" in text
    assert ".NUM_DIAGNOSIS_ENTRIES(4)" in text


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_onchip_diagnosis_requires_onchip_selfrepair(tmp_path: Path) -> None:
    config = {
        **BASE_CONFIG,
        "redundancy": {"num_spare_rows": 2, "onchip_diagnosis": True, "num_diagnosis_entries": 4},
    }
    config_path = tmp_path / "bad_diag.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="onchip_selfrepair"):
        generate_from_config(config_path, tmp_path / "out")


@pytest.mark.parametrize("bad", [1, "true", [], {}])
def test_onchip_diagnosis_bad_type_rejected(tmp_path: Path, bad) -> None:
    config = {
        **BASE_CONFIG,
        "redundancy": {"num_spare_rows": 2, "onchip_selfrepair": True, "onchip_diagnosis": bad},
    }
    config_path = tmp_path / "bad_diag_type.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="boolean"):
        generate_from_config(config_path, tmp_path / "out")


@pytest.mark.parametrize("bad", [0, -1, "4", 1.5, True, None])
def test_onchip_diagnosis_bad_num_entries_rejected(tmp_path: Path, bad) -> None:
    config = {
        **BASE_CONFIG,
        "redundancy": {
            "num_spare_rows": 2, "onchip_selfrepair": True,
            "onchip_diagnosis": True, "num_diagnosis_entries": bad,
        },
    }
    config_path = tmp_path / "bad_diag_entries.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="num_diagnosis_entries"):
        generate_from_config(config_path, tmp_path / "out")


def test_onchip_diagnosis_missing_num_entries_rejected(tmp_path: Path) -> None:
    """num_diagnosis_entries has no default when onchip_diagnosis is true --
    matches num_spare_rows's own philosophy of explicit hardware sizing."""
    config = {**BASE_CONFIG, "redundancy": {"num_spare_rows": 2, "onchip_selfrepair": True, "onchip_diagnosis": True}}
    config_path = tmp_path / "missing_diag_entries.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="num_diagnosis_entries"):
        generate_from_config(config_path, tmp_path / "out")
