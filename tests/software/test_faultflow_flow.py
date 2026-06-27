from __future__ import annotations

import configparser
import json
from pathlib import Path

import pytest

from autombist.faultflow_flow import (
    FaultFlowError,
    FaultFlowOptions,
    build_ofs,
    build_synth_script,
    controller_sources,
    emit_bundle,
    read_coverage,
    render_blackbox_stub,
)
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
    assert "output wire [DATA_WIDTH-1:0] dout0" in text


def test_controller_sources_excludes_macro_and_saboteur(tmp_path: Path) -> None:
    names = [p.name for p in controller_sources(tmp_path, _config())]
    assert "input_demo_8x16_scn4m_mbist.v" in names
    assert {"march_c_algo.sv", "march_c_fsm.sv", "march_c_top.sv"} <= set(names)
    # the real macro, the sim model, and the saboteur must NOT be synthesized
    assert "input_demo_8x16_scn4m.v" not in names
    assert not any("saboteur" in n or "sram_model" in n for n in names)


def test_build_synth_script_keeps_blackbox_lib(tmp_path: Path) -> None:
    cfg = _config()
    script = build_synth_script(
        sources=controller_sources(tmp_path, cfg),
        stub=tmp_path / "input_demo_8x16_scn4m_bbox.v",
        top=cfg["wrapper_module_name"],
        liberty=tmp_path / "x.lib",
        json_out=tmp_path / "x.json",
        gate_out=tmp_path / "x_gate.v",
    )
    assert "read_verilog -lib" in script
    assert "flatten" in script
    assert "synth -top input_demo_8x16_scn4m_mbist" in script
    assert "write_json" in script


def test_build_ofs_roundtrips_through_configparser(tmp_path: Path) -> None:
    text = build_ofs(
        netlist=tmp_path / "top.json",
        top="input_demo_8x16_scn4m_mbist",
        cell_json=tmp_path / "cells.json",
        liberty=tmp_path / "x.lib",
        verilog_models=tmp_path / "x.v",
        opts=FaultFlowOptions(repo=tmp_path),
    )
    cp = configparser.ConfigParser()
    cp.read_string(text)  # must parse with FaultFlow's own parser (configparser)
    assert cp["design"]["top"] == "input_demo_8x16_scn4m_mbist"
    assert cp["blackbox"]["instances"] == "u_sram"
    assert cp["fault_model"]["model"] == "stuck_at"
    assert cp["fault_model"]["collapsing"] == "false"
    assert cp["atpg"]["tool"] == "native"
    assert cp["scan"]["chains"] == "1"
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
    for name in (
        "input_demo_8x16_scn4m_bbox.v",
        "synth_collar.ys",
        "input_demo_8x16_scn4m_mbist.ofs",
        "run_faultflow.sh",
        "README.txt",
    ):
        assert (bundle / name).exists(), f"missing {name}"
    run = (bundle / "run_faultflow.sh").read_text(encoding="utf-8")
    assert "ff.py sim" in run and "--scan" in run
    assert "flatten" in run.lower()


def test_read_coverage_and_merge(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    top = "input_demo_8x16_scn4m_mbist"
    inter = repo / "output" / top / ".faultflow" / "intermediate"
    inter.mkdir(parents=True)
    (inter / "coverage_report.json").write_text(
        json.dumps(
            {
                "summary": {
                    "coverage_percent": 92.5,
                    "detected": 37,
                    "denominator": 40,
                    "excluded_blackbox": 6,
                    "test_coverage_percent": 92.5,
                    "fault_coverage_percent": 90.0,
                },
                "policy": {"blackbox_instances": ["u_sram"]},
            }
        ),
        encoding="utf-8",
    )
    block = read_coverage(repo, top)
    assert block["coverage_percent"] == 92.5
    assert block["detected"] == 37 and block["denominator"] == 40
    assert block["excluded_blackbox"] == 6
    assert block["blackbox_instances"] == ["u_sram"]

    report = {
        "config": {"memory_name": "m"},
        "simulation": {},
        "fault_metrics": {},
        "junit": {"summary": {}},
    }
    merge_faultflow_coverage(report, block)
    assert report["controller_grading"]["coverage_percent"] == 92.5
    assert "controller (FaultFlow" in report["summary"]
