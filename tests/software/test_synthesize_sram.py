"""Regression test for a real bug found while generating a real OpenRAM macro
for BISR testing (see docs/redundancy-repair-plan.md): `build_config_text()`
only emitted `num_spare_rows`/`num_spare_cols` into the OpenRAM config when
`tech == "sky130"`, silently dropping `--num-spare-rows`/`--num-spare-cols`
for `scn4m_subm` and `freepdk45`. Both fields are generic OpenRAM
`sram_config.py` settings with no sky130-specific branching in OpenRAM itself
(`bank.py`'s `num_rows = num_rows_temp + num_spare_rows` is tech-agnostic) --
confirmed by an actual run: `--tech scn4m_subm --num-spare-rows 2` silently
produced `ADDR_WIDTH=4` (no widening) before the fix, `ADDR_WIDTH=5` (the
expected `ceil(log2(16+2))`) after.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "synthesize_sram.py"


def _load_synthesize_sram():
    spec = importlib.util.spec_from_file_location("synthesize_sram", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


synthesize_sram = _load_synthesize_sram()


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        tech="scn4m_subm",
        word_size=8,
        num_words=16,
        num_rw_ports=1,
        num_r_ports=0,
        num_w_ports=0,
        write_size=8,
        num_spare_rows=2,
        num_spare_cols=1,
        supply_voltage=5.0,
        run_drc_lvs=False,
        output_path="/tmp/out",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_scn4m_subm_config_includes_spare_rows_and_cols() -> None:
    text = synthesize_sram.build_config_text(_args(tech="scn4m_subm"), "sram")
    assert "num_spare_rows = 2" in text
    assert "num_spare_cols = 1" in text


def test_freepdk45_config_includes_spare_rows_and_cols() -> None:
    text = synthesize_sram.build_config_text(_args(tech="freepdk45"), "sram")
    assert "num_spare_rows = 2" in text
    assert "num_spare_cols = 1" in text


def test_sky130_config_still_includes_spare_rows_and_cols() -> None:
    """Not a new behavior -- pins that the fix didn't regress the path that
    already worked."""
    text = synthesize_sram.build_config_text(_args(tech="sky130"), "sram")
    assert "num_spare_rows = 2" in text
    assert "num_spare_cols = 1" in text


def test_non_sky130_config_omits_sky130_specific_settings() -> None:
    """The fix moves ONLY num_spare_rows/num_spare_cols out of the sky130
    gate -- genuinely sky130-specific physical/PDK settings must stay gated."""
    text = synthesize_sram.build_config_text(_args(tech="scn4m_subm"), "sram")
    assert "nominal_corner_only = False" in text
    assert 'route_supplies = "side"' in text
    assert "uniquify" not in text
    assert "write_size" not in text


def test_sky130_config_keeps_its_specific_settings() -> None:
    text = synthesize_sram.build_config_text(_args(tech="sky130"), "sram")
    assert "nominal_corner_only = True" in text
    assert 'route_supplies = "ring"' in text
    assert "uniquify = True" in text
    assert "write_size = 8" in text


def test_zero_spares_still_emitted_explicitly() -> None:
    """A config with no spares at all must still say so explicitly (0), not
    silently omit the line -- OpenRAM's own default is 0, but relying on that
    default rather than stating it is exactly the class of bug this fixes."""
    text = synthesize_sram.build_config_text(_args(tech="scn4m_subm", num_spare_rows=0, num_spare_cols=0), "sram")
    assert "num_spare_rows = 0" in text
    assert "num_spare_cols = 0" in text
