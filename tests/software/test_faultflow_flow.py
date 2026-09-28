from __future__ import annotations

import configparser
import json
from pathlib import Path

import pytest

from autombist.faultflow_flow import (
    RUN_DIRNAME,
    FaultFlowError,
    FaultFlowOptions,
    build_grading_options,
    controller_sources,
    emit_bundle,
    grading_manifest,
    memory_instances,
    read_coverage,
    render_blackbox_stub,
)
from autombist.generator import load_config
from autombist.reporting import merge_faultflow_coverage


def _config() -> dict:
    return {
        "memory_name": "input_demo_8x16_scn4m",
        "wrapper_module_name": "input_demo_8x16_scn4m_mbist",
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


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "faultflow"
    (repo / "cells" / "sky130").mkdir(parents=True)
    venv_bin = repo / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    return repo


def test_render_blackbox_stub_has_blackbox_and_ports() -> None:
    text = render_blackbox_stub(_config())
    assert "(* blackbox *)" in text
    assert "module input_demo_8x16_scn4m " in text
    for port in ("clk0", "csb0", "addr0", "din0", "web0", "dout0"):
        assert port in text
    assert "output wire [7:0] dout0" in text


def test_controller_sources_excludes_macro_and_saboteur(tmp_path: Path) -> None:
    names = [p.name for p in controller_sources(tmp_path, _config())]
    assert "input_demo_8x16_scn4m_mbist.v" in names
    assert {"march_c_algo.sv", "march_c_fsm.sv", "march_c_top.sv"} <= set(names)
    # the real macro, the sim model, and the saboteur must NOT be synthesized
    assert "input_demo_8x16_scn4m.v" not in names
    assert not any("saboteur" in n or "sram_model" in n for n in names)


def test_controller_sources_shared_bus_uses_wrapper_module_name(tmp_path: Path) -> None:
    # Under topology: shared-bus, generate_from_config names the wrapper file
    # after wrapper_module_name, not memory_name (memory_name there names the
    # shared macro TYPE, e.g. "sram_8x16" reused by every u_mem_<name>
    # instance — it was never the generated wrapper's filename). Regression
    # for the real bug: this used to look for "{memory_name}_mbist.v", which
    # doesn't exist under shared-bus whenever the two names differ.
    cfg = _config()
    cfg["topology"] = "shared-bus"
    cfg["memory_name"] = "sram_8x16"
    cfg["wrapper_module_name"] = "shared_ctrl"
    cfg["memories"] = [{"name": "bank0"}, {"name": "bank1"}]
    names = [p.name for p in controller_sources(tmp_path, cfg)]
    assert "shared_ctrl_mbist.v" in names
    assert "sram_8x16_mbist.v" not in names


def test_memory_instances_follow_topology() -> None:
    assert memory_instances(_config()) == ["u_sram"]
    cfg = _config()
    cfg.update(topology="shared-bus", wrapper_module_name="shared_ctrl",
               memories=[{"name": "bank0"}, {"name": "bank1"}])
    assert memory_instances(cfg) == ["u_mem_bank0", "u_mem_bank1"]


def test_grading_manifest_has_absolute_sources(tmp_path: Path) -> None:
    # The bundle keeps its own manifest; FaultFlow resolves relative sources
    # against the manifest's directory, so every path must be absolute.
    manifest = grading_manifest(_config(), tmp_path)
    assert manifest["generator"]["command"] == "grade-controller"
    for path in manifest["sources"].values():
        assert Path(path).is_absolute()
    assert Path(manifest["sources"]["wrapper"]) == tmp_path.resolve() / "input_demo_8x16_scn4m_mbist.v"
    for inst in manifest["instances"]:
        for src in inst["sources"]:
            assert Path(src).is_absolute() and Path(src).parent.is_relative_to(tmp_path.resolve())
    memories = [i for i in manifest["instances"] if i["hierarchy_hint"] == "blackbox"]
    assert [Path(m["sources"][0]).name for m in memories] == ["input_demo_8x16_scn4m_bbox.v"]


def test_grading_manifest_lists_every_shared_bus_memory(tmp_path: Path) -> None:
    cfg = _config()
    cfg.update(topology="shared-bus", memory_name="sram_8x16", wrapper_module_name="shared_ctrl",
               memories=[{"name": "bank0"}, {"name": "bank1"}])
    manifest = grading_manifest(cfg, tmp_path)
    blackboxed = [i["hierarchical_path"] for i in manifest["instances"] if i["hierarchy_hint"] == "blackbox"]
    assert blackboxed == ["u_mem_bank0", "u_mem_bank1"]
    assert manifest["sources"]["wrapper"].endswith("shared_ctrl_mbist.v")


def test_grading_manifest_rejects_a_saboteur_build(tmp_path: Path) -> None:
    cfg = _config()
    cfg["use_saboteur"] = True
    with pytest.raises(FaultFlowError, match="--test"):
        grading_manifest(cfg, tmp_path)


def test_build_grading_options_roundtrips_through_configparser() -> None:
    cp = configparser.ConfigParser()
    cp.read_string(build_grading_options(FaultFlowOptions(scan_chains=2, threshold=95.0, max_rounds=7)))
    # [design] and [blackbox] belong to the .ofs FaultFlow's synthesis writes.
    assert "design" not in cp and "blackbox" not in cp
    assert cp["fault_model"]["model"] == "stuck_at"
    assert cp["fault_model"]["collapsing"] == "false"
    assert cp["atpg"]["tool"] == "native"
    assert cp["atpg"]["max_rounds"] == "7"
    assert cp["scan"]["chains"] == "2"
    assert cp["report"]["threshold"] == "95.0"
    assert cp["simulation"]["unsupported_cells"] == "fail"


def test_options_resolution(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    opts = FaultFlowOptions(repo=repo, cell_lib="sky130")
    assert opts.resolved_repo() == repo
    assert opts.resolved_ff_python(repo).replace("\\", "/").endswith("venv/bin/python")
    cell_json, _liberty, _models = opts.cell_lib_paths(repo)
    assert cell_json.name == "sky130_fd_sc_hd.json"
    with pytest.raises(FaultFlowError):
        FaultFlowOptions(repo=tmp_path / "nope").resolved_repo()
    with pytest.raises(FaultFlowError):
        FaultFlowOptions(repo=repo, cell_lib="bogus").cell_lib_paths(repo)


def test_emit_bundle_writes_all_files(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    module_outdir = tmp_path / "out" / "input_demo_8x16_scn4m"
    module_outdir.mkdir(parents=True)
    bundle = emit_bundle(module_outdir, _config(), FaultFlowOptions(repo=repo))
    for name in ("manifest.json", "options.ofs", "run_faultflow.sh", "README.txt"):
        assert (bundle / name).exists(), f"missing {name}"
    # the output directory's own manifest.json (generate --emit-manifest,
    # wrap-test-access) is never touched
    assert not (module_outdir / "manifest.json").exists()
    run = (bundle / "run_faultflow.sh").read_text(encoding="utf-8")
    assert "synthesize_from_manifest" in run
    steps = ["ff.py\" init", "ff.py\" scan ", "ff.py\" scan-check", "ff.py\" sim "]
    positions = [run.index(step) for step in steps]
    assert positions == sorted(positions), "scan-check must run between scan and sim --scan"
    assert "--scan" in run[positions[-1]:]


def test_emit_bundle_grades_the_clean_collar_of_a_test_build(tmp_path: Path) -> None:
    # A --test build's memory is the fault-injection saboteur (and it has no
    # memory stub); the bundle regenerates the clean collar from the build's
    # own config snapshot and grades that, so `run --test --faultflow` works.
    from autombist.faultflow_flow import CLEAN_DIRNAME
    from autombist.generator import generate_from_config

    config_path = tmp_path / "config.yml"
    config_path.write_text(json.dumps(_config()), encoding="utf-8")  # JSON is valid YAML
    wrapper = generate_from_config(
        config_path, tmp_path / "out", use_saboteur=True, faults=2, fault_seed=1, algo="march-c",
    )
    module_outdir = wrapper.parent
    assert "saboteur" in wrapper.read_text(encoding="utf-8")

    bundle = emit_bundle(module_outdir, load_config(module_outdir / "config.yml"),
                         FaultFlowOptions(repo=_fake_repo(tmp_path)))

    clean = bundle / CLEAN_DIRNAME / "input_demo_8x16_scn4m"
    assert (clean / "input_demo_8x16_scn4m_bbox.v").is_file()
    assert "saboteur" not in (clean / "input_demo_8x16_scn4m_mbist.v").read_text(encoding="utf-8")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert Path(manifest["sources"]["wrapper"]) == clean.resolve() / "input_demo_8x16_scn4m_mbist.v"
    memory = next(i for i in manifest["instances"] if i["hierarchy_hint"] == "blackbox")
    assert Path(memory["sources"][0]) == clean.resolve() / "input_demo_8x16_scn4m_bbox.v"
    # the build under test itself is untouched
    assert "saboteur" in wrapper.read_text(encoding="utf-8")


def test_run_script_keeps_faultflow_output_in_the_bundle(tmp_path: Path) -> None:
    # FaultFlow writes output/<top>/ under its working directory; the run
    # script must cd into the bundle, never into the FaultFlow checkout.
    repo = _fake_repo(tmp_path)
    module_outdir = tmp_path / "out" / "input_demo_8x16_scn4m"
    module_outdir.mkdir(parents=True)
    bundle = emit_bundle(module_outdir, _config(), FaultFlowOptions(repo=repo))
    run = (bundle / "run_faultflow.sh").read_text(encoding="utf-8")
    assert f'BUNDLE="{bundle}"' in run
    assert f'RUN="$BUNDLE/{RUN_DIRNAME}"' in run
    assert 'cd "$RUN"' in run
    assert 'cd "$FAULTFLOW_HOME"' not in run
    assert '"$FAULTFLOW_HOME/ff.py"' in run


def test_emit_bundle_resolves_to_absolute_paths(tmp_path: Path, monkeypatch) -> None:
    # The bundle runs from any working directory, so its paths must be
    # absolute even if --out was relative.
    repo = _fake_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    rel_module = Path("out") / "input_demo_8x16_scn4m"
    rel_module.mkdir(parents=True)
    bundle = emit_bundle(rel_module, _config(), FaultFlowOptions(repo=repo))
    assert bundle.is_absolute()
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert Path(manifest["sources"]["wrapper"]).is_absolute()
    assert f'BUNDLE="{bundle}"' in (bundle / "run_faultflow.sh").read_text(encoding="utf-8")


def test_read_coverage_and_merge(tmp_path: Path) -> None:
    workdir = tmp_path / "faultflow" / RUN_DIRNAME
    top = "input_demo_8x16_scn4m_mbist"
    inter = workdir / "output" / top / ".faultflow" / "intermediate"
    inter.mkdir(parents=True)
    (inter / "coverage_report.json").write_text(
        json.dumps(
            {
                "summary": {
                    "coverage_percent": 80.29,
                    "detected": 444,
                    "denominator": 553,
                    "redundant": 22,
                    "blackbox_unresolved": 109,
                    "excluded_blackbox": 0,
                    "test_coverage_percent": 80.29,
                    "fault_coverage_percent": 77.2,
                },
                "policy": {"blackbox_instances": ["u_sram"], "blackbox_output_values": {"u_sram": "x"}},
            }
        ),
        encoding="utf-8",
    )
    block = read_coverage(workdir, top)
    assert block["coverage_percent"] == 80.29
    assert block["detected"] == 444 and block["denominator"] == 553
    assert block["blackbox_unresolved"] == 109 and block["redundant"] == 22
    assert block["blackbox_instances"] == ["u_sram"]
    assert block["blackbox_output_values"] == {"u_sram": "x"}
    assert Path(block["coverage_rpt"]) == workdir / "output" / top / "coverage.rpt"
    with pytest.raises(FaultFlowError, match="not found"):
        read_coverage(tmp_path / "elsewhere", top)

    report = {
        "config": {"memory_name": "m"},
        "simulation": {},
        "fault_metrics": {},
        "junit": {"summary": {}},
    }
    merge_faultflow_coverage(report, block)
    assert report["controller_grading"]["coverage_percent"] == 80.29
    assert "444/553 (80.29%), blackbox-unresolved=109" in report["summary"]
