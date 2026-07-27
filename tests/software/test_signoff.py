"""Unit tests for the pure physical-signoff helpers (src/autombist/signoff.py).

No EDA tools, no PDK, no LibreLane -- these exercise the LEF-units transform and
the LibreLane-config builder that back the `harden` / `fix-lef-units` /
`macro-signoff` CLI commands.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.signoff import (  # noqa: E402
    SignoffConfigError,
    build_librelane_command,
    build_librelane_config,
    build_macro_signoff_command,
    normalize_lef_units,
)


# --------------------------- normalize_lef_units ---------------------------
def test_lef_units_declaration_rewritten():
    text = "VERSION 5.7 ;\nUNITS\n  DATABASE MICRONS 2000 ;\nEND UNITS\n"
    fixed, snapped = normalize_lef_units(text)
    assert "DATABASE MICRONS 1000 ;" in fixed
    assert "2000" not in fixed
    assert snapped == 0  # no coordinates in this snippet


def test_lef_units_snaps_deep_decimals():
    text = "DATABASE MICRONS 2000 ;\n  RECT 1.2345 0.0 2.5 3.33335 ;\n"
    fixed, snapped = normalize_lef_units(text)
    assert snapped == 2  # 1.2345 and 3.33335
    assert "1.234" in fixed or "1.235" in fixed  # snapped to 3 places
    assert "1.2345" not in fixed


def test_lef_units_idempotent_when_clean():
    text = "DATABASE MICRONS 1000 ;\n  SIZE 480.725 BY 223.055 ;\n"
    fixed, snapped = normalize_lef_units(text)
    assert fixed == text  # already 1000, no deep decimals -> untouched
    assert snapped == 0


def test_lef_units_preserves_three_decimal_coords():
    # A real SIZE line with 3 decimals must survive verbatim.
    text = "DATABASE MICRONS 2000 ;\nMACRO m\n  SIZE 468.485 BY 284.77 ;\nEND m\n"
    fixed, _ = normalize_lef_units(text)
    assert "SIZE 468.485 BY 284.77 ;" in fixed


# --------------------------- build_librelane_config ---------------------------
def _min_cfg():
    return {
        "design_name": "mem_subsystem",
        "verilog_files": ["mem_subsystem.sv", "sky130_srams_bb.v"],
        "clock_port": "clk",
    }


def test_config_minimal_no_macros():
    out = build_librelane_config(_min_cfg())
    assert out["DESIGN_NAME"] == "mem_subsystem"
    assert out["CLOCK_PORT"] == "clk"
    assert out["CLOCK_PERIOD"] == 20  # default
    assert out["VERILOG_FILES"] == ["mem_subsystem.sv", "sky130_srams_bb.v"]
    assert "MACROS" not in out  # no macros -> no macro machinery
    assert "PDN_MACRO_CONNECTIONS" not in out


def test_config_with_macros_bakes_the_recipe():
    cfg = _min_cfg()
    cfg["die_area"] = [0, 0, 1120, 700]
    cfg["macros"] = [
        {"name": "sky130_sram_32b256w", "gds": "a.gds", "lef": "a.lef",
         "instance": "u_m0", "location": [50, 60]},
        {"name": "sky130_sram_8b1024w", "gds": "c.gds", "lef": "c.lef",
         "instance": "u_m2", "location": [50, 340]},
    ]
    out = build_librelane_config(cfg)

    assert out["FP_SIZING"] == "absolute"
    assert out["DIE_AREA"] == [0, 0, 1120, 700]
    # hard-IP signoff + halos baked in
    assert out["MAGIC_DRC_USE_GDS"] is False
    assert out["RUN_KLAYOUT_XOR"] is False
    # macro-internal DRC noise (OpenRAM bitcell layers) must not fail signoff
    assert out["ERROR_ON_MAGIC_DRC"] is False
    assert out["ERROR_ON_KLAYOUT_DRC"] is False
    assert out["PDN_HORIZONTAL_HALO"] == 15
    assert out["FP_MACRO_VERTICAL_HALO"] == 15
    # macros collapsed by name, instances under each
    assert set(out["MACROS"]) == {"sky130_sram_32b256w", "sky130_sram_8b1024w"}
    assert out["MACROS"]["sky130_sram_32b256w"]["instances"]["u_m0"]["location"] == [50, 60]
    # PDN connections: net-vs-pin order (VPWR/VGND design nets, vccd1/vssd1 pins)
    assert out["PDN_MACRO_CONNECTIONS"] == [
        "u_m0 VPWR VGND vccd1 vssd1",
        "u_m2 VPWR VGND vccd1 vssd1",
    ]


def test_config_custom_power_nets():
    cfg = _min_cfg()
    cfg["macros"] = [{"name": "m", "gds": "m.gds", "lef": "m.lef", "instance": "u"}]
    cfg["power"] = {"vdd_net": "VDD", "gnd_net": "VSS", "macro_vdd_pin": "vpwr", "macro_gnd_pin": "vgnd"}
    out = build_librelane_config(cfg)
    assert out["PDN_MACRO_CONNECTIONS"] == ["u VDD VSS vpwr vgnd"]


@pytest.mark.parametrize("bad,msg", [
    ({}, "design_name"),
    # empty verilog_files is falsy -> caught by the required-key check first
    ({"design_name": "d", "clock_port": "clk", "verilog_files": []}, "verilog_files"),
    # a present-but-non-list verilog_files reaches the type/non-empty check
    ({"design_name": "d", "clock_port": "clk", "verilog_files": "x.sv"}, "non-empty list"),
    ({"design_name": "d", "clock_port": "clk", "verilog_files": ["x.sv"],
      "macros": [{"name": "m", "gds": "g", "lef": "l"}]}, "instance"),
])
def test_config_rejects_malformed(bad, msg):
    with pytest.raises(SignoffConfigError) as exc:
        build_librelane_config(bad)
    assert msg in str(exc.value)


def test_config_rejects_duplicate_instance():
    cfg = _min_cfg()
    cfg["macros"] = [
        {"name": "m", "gds": "g", "lef": "l", "instance": "u_m0"},
        {"name": "n", "gds": "g2", "lef": "l2", "instance": "u_m0"},
    ]
    with pytest.raises(SignoffConfigError, match="duplicate macro instance"):
        build_librelane_config(cfg)


# --------------------------- command builders ---------------------------
def test_librelane_command_shape():
    cmd = build_librelane_command("cfg.json", "/home/u/.ciel")
    assert cmd[0] == "nix"
    assert "run" in cmd and "github:librelane/librelane" in cmd
    assert cmd[-1] == "cfg.json"
    assert "--pdk-root" in cmd and "/home/u/.ciel" in cmd


def test_macro_signoff_command_all_vs_named():
    assert build_macro_signoff_command("s.sh") == ["bash", "s.sh"]
    assert build_macro_signoff_command("s.sh", ["m1", "m2"]) == ["bash", "s.sh", "m1", "m2"]
