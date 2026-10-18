from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from autombist.faultflow_flow import RUN_DIRNAME, FaultFlowOptions, grade_controller
from autombist.generator import generate_from_config


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
    # have pointed the bundle's synthesis at a nonexistent source.
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
    manifest = json.loads((module_outdir / "faultflow" / "manifest.json").read_text(encoding="utf-8"))
    # Must reference the file generate_from_config actually writes
    # (shared_ctrl_mbist.v), not the pre-fix "sram_shared_mbist.v" guess.
    assert manifest["sources"]["wrapper"].endswith("shared_ctrl_mbist.v")
    assert not any("sram_shared_mbist.v" in json.dumps(i) for i in manifest["instances"])
    blackboxed = [i["hierarchical_path"] for i in manifest["instances"] if i["hierarchy_hint"] == "blackbox"]
    assert blackboxed == ["u_mem_bank0", "u_mem_bank1"]


def test_grade_controller_emit_only(tmp_path: Path) -> None:
    """The bundle must be emittable with no EDA tools present (the cross-platform path)."""
    module_outdir = _write_module(tmp_path)
    result = grade_controller(module_outdir, FaultFlowOptions(repo=_fake_repo(tmp_path)), run=False)
    assert result is None
    bundle = module_outdir / "faultflow"
    for name in ("manifest.json", "options.ofs", "run_faultflow.sh", "README.txt"):
        assert (bundle / name).exists(), f"missing {name}"


# --- the real thing: live Yosys + FaultFlow -----------------------------------

_FAULTFLOW_HOME = os.environ.get("FAULTFLOW_HOME")
_PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"}
_DEDICATED = {"memory_name": "sram_1rw", "wrapper_module_name": "gc_ded_ctrl",
              "addr_width": 4, "data_width": 4, "we_active_low": True, "ports": _PORTS}
_CASES = {
    "dedicated": _DEDICATED,
    "shared-bus": {**_DEDICATED, "wrapper_module_name": "gc_shb_ctrl", "topology": "shared-bus",
                   "memories": [{"name": "bank0"}, {"name": "bank1"}]},
}


@pytest.mark.faultflow
@pytest.mark.skipif(
    not _FAULTFLOW_HOME
    or not (Path(_FAULTFLOW_HOME) / "faultflow" / "integrations" / "autombist.py").is_file()
    or shutil.which("yosys") is None,
    reason="needs $FAULTFLOW_HOME pointing at a built FaultFlow with its autoMBIST "
    "integration (faultflow/integrations/autombist.py), plus yosys on PATH",
)
@pytest.mark.parametrize("case", _CASES)
def test_grade_controller_full_flow(tmp_path: Path, case: str) -> None:
    """Generate a real design, then run the bundle end to end: FaultFlow's
    manifest synthesis, scan insertion, scan-check and scan stuck-at ATPG."""
    config = _CASES[case]
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    module_outdir = generate_from_config(config_path, tmp_path / "out").parent
    repo = Path(_FAULTFLOW_HOME).resolve()
    top = config["wrapper_module_name"]
    assert not (repo / "output" / top).exists(), "stale FaultFlow output from an old bundle run"

    coverage = grade_controller(module_outdir, FaultFlowOptions(repo=repo), run=True)

    assert coverage is not None
    assert 0 < coverage["detected"] <= coverage["denominator"]
    assert 0 < coverage["coverage_percent"] <= 100
    # the memory's outputs are unknown in a scan test: some controller faults
    # are testable only through it, and they stay in the denominator
    assert coverage["blackbox_unresolved"] > 0
    memories = ["u_mem_bank0", "u_mem_bank1"] if case == "shared-bus" else ["u_sram"]
    assert coverage["blackbox_instances"] == memories
    assert set(coverage["blackbox_output_values"].values()) == {"x"}

    bundle = module_outdir / "faultflow"
    assert Path(coverage["coverage_json"]).is_relative_to(bundle / RUN_DIRNAME)
    assert (bundle / "synth" / f"{top}_composed.json").is_file()
    log = (bundle / "run.log").read_text(encoding="utf-8")
    assert "scan-check PASS" in log
    # everything stayed in the bundle
    assert not (repo / "output" / top).exists()


@pytest.mark.faultflow
@pytest.mark.skipif(
    not _FAULTFLOW_HOME
    or not (Path(_FAULTFLOW_HOME) / "faultflow" / "integrations" / "autombist.py").is_file()
    or shutil.which("yosys") is None,
    reason="needs $FAULTFLOW_HOME pointing at a built FaultFlow with its autoMBIST "
    "integration (faultflow/integrations/autombist.py), plus yosys on PATH",
)
def test_grade_controller_grades_a_test_build_as_its_clean_collar(tmp_path: Path) -> None:
    """`run --test --faultflow`: a --test build (saboteur in place of the memory)
    grades exactly like the clean build of the same design -- same controller,
    same fault universe, same result."""
    repo = Path(_FAULTFLOW_HOME).resolve()
    config = {**_DEDICATED, "wrapper_module_name": "gc_sab_ctrl"}
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    clean_dir = generate_from_config(config_path, tmp_path / "clean").parent
    test_dir = generate_from_config(
        config_path, tmp_path / "test", use_saboteur=True, faults=4, fault_seed=1,
    ).parent

    clean = grade_controller(clean_dir, FaultFlowOptions(repo=repo), run=True)
    graded = grade_controller(test_dir, FaultFlowOptions(repo=repo), run=True)

    keys = ("detected", "denominator", "redundant", "blackbox_unresolved", "coverage_percent")
    assert {k: graded[k] for k in keys} == {k: clean[k] for k in keys}
