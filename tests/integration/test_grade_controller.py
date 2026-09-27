from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autombist.faultflow_flow import FaultFlowOptions, grade_controller


def _write_module(tmp_path: Path) -> Path:
    mem = "input_demo_8x16_scn4m"
    module_outdir = tmp_path / "out" / mem
    (module_outdir / "march_c").mkdir(parents=True)
    config = {
        "memory_name": mem,
        "wrapper_module_name": f"{mem}_mbist",
        "addr_width": 4,
        "data_width": 8,
        "we_active_low": True,
        "ports": {
            "clk": "clk0",
            "addr": "addr0",
            "din": "din0",
            "dout": "dout0",
            "we": "web0",
            "csb": "csb0",
        },
        "algo": "march-c",
        "algo_dir": "march_c",
    }
    (module_outdir / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    # placeholder sources so the bundle references real paths (content irrelevant for --no-run)
    (module_outdir / f"{mem}_mbist.v").write_text("// wrapper\n", encoding="utf-8")
    for stem in ("algo", "fsm", "top"):
        (module_outdir / "march_c" / f"march_c_{stem}.sv").write_text("// rtl\n", encoding="utf-8")
    return module_outdir


def _write_shared_bus_module(tmp_path: Path) -> Path:
    # topology: shared-bus names the wrapper file after wrapper_module_name,
    # NOT memory_name (memory_name there names the shared macro TYPE) --
    # regression coverage for controller_sources()'s real pre-existing bug
    # (faultflow_flow.py), which looked for the wrong filename here and would
    # have made this bundle's synth_collar.ys reference a nonexistent source.
    mem_type = "sram_shared"
    wrapper_name = "shared_ctrl"
    module_outdir = tmp_path / "out" / wrapper_name
    (module_outdir / "march_c").mkdir(parents=True)
    config = {
        "topology": "shared-bus",
        "memory_name": mem_type,
        "wrapper_module_name": wrapper_name,
        "memories": [{"name": "bank0"}, {"name": "bank1"}],
        "addr_width": 4,
        "data_width": 8,
        "we_active_low": True,
        "ports": {
            "clk": "clk0",
            "addr": "addr0",
            "din": "din0",
            "dout": "dout0",
            "we": "web0",
            "csb": "csb0",
        },
        "algo": "march-c",
        "algo_dir": "march_c",
    }
    (module_outdir / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (module_outdir / f"{wrapper_name}_mbist.v").write_text("// wrapper\n", encoding="utf-8")
    for stem in ("algo", "fsm", "top"):
        (module_outdir / "march_c" / f"march_c_{stem}.sv").write_text("// rtl\n", encoding="utf-8")
    return module_outdir


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "faultflow"
    (repo / "cells" / "sky130").mkdir(parents=True)
    return repo


def test_grade_controller_emit_only_shared_bus(tmp_path: Path) -> None:
    module_outdir = _write_shared_bus_module(tmp_path)
    result = grade_controller(module_outdir, FaultFlowOptions(repo=_fake_repo(tmp_path)), run=False)
    assert result is None
    bundle = module_outdir / "faultflow"
    synth_script = (bundle / "synth_collar.ys").read_text(encoding="utf-8")
    # Must reference the file generate_from_config actually writes
    # (shared_ctrl_mbist.v), not the pre-fix "sram_shared_mbist.v" guess.
    assert "shared_ctrl_mbist.v" in synth_script
    assert "sram_shared_mbist.v" not in synth_script


def test_grade_controller_emit_only(tmp_path: Path) -> None:
    """The bundle must be emittable with no EDA tools present (the cross-platform path)."""
    module_outdir = _write_module(tmp_path)
    result = grade_controller(module_outdir, FaultFlowOptions(repo=_fake_repo(tmp_path)), run=False)
    assert result is None
    bundle = module_outdir / "faultflow"
    for name in (
        "input_demo_8x16_scn4m_bbox.v",
        "synth_collar.ys",
        "input_demo_8x16_scn4m_mbist.ofs",
        "run_faultflow.sh",
        "README.txt",
    ):
        assert (bundle / name).exists(), f"missing {name}"


@pytest.mark.faultflow
@pytest.mark.skip(
    reason="not yet implemented -- see test_grade_controller_emit_only for the "
    "cross-platform coverage this bundle gets today; a real synth + ATPG run needs "
    "a live FaultFlow checkout with MBIST support, which doesn't exist yet (see "
    "the faultflow-tool project memory). Was previously a skipif gated on Yosys + "
    "$FAULTFLOW_HOME whose body was an unconditional pytest.skip() regardless of "
    "whether that gate passed -- so it never ran in any environment. Track real "
    "FaultFlow MBIST integration before reviving this."
)
def test_grade_controller_full_flow() -> None:
    pass
