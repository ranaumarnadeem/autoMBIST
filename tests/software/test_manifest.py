from __future__ import annotations

import json
from pathlib import Path

import pytest

from autombist.manifest import (
    MANIFEST_FORMAT,
    ManifestError,
    build_instance_manifest,
    update_manifest_with_test_access,
    write_instance_manifest,
)

COL_PORTS = {
    "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0",
    "we": "web0", "csb": "csb0", "spare_wen": "spare_wen0",
}


def _dedicated_config(**redundancy_overrides) -> dict:
    cfg = {
        "memory_name": "sram_1rw",
        "wrapper_module_name": "sram_1rw_mbist",
        "addr_width": 4,
        "data_width": 8,
        "we_active_low": True,
        "algo": "march-c",
        "algo_dir": "march_c",
        "normalized_ports": {
            "port0": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
        },
    }
    if redundancy_overrides:
        cfg["redundancy"] = redundancy_overrides
    return cfg


def _instances_by_path(manifest: dict) -> dict:
    return {i["hierarchical_path"]: i for i in manifest["instances"]}


def test_bare_dedicated_config_has_only_the_memory_instance(tmp_path: Path) -> None:
    manifest = build_instance_manifest(_dedicated_config(), tmp_path, tool_version="0.0.0")
    assert manifest["format"] == MANIFEST_FORMAT
    assert manifest["generator"]["topology"] == "dedicated"
    assert manifest["top_module"] == "sram_1rw_mbist"
    paths = _instances_by_path(manifest)
    assert set(paths) == {"u_sram"}
    assert paths["u_sram"]["hierarchy_hint"] == "blackbox"
    assert paths["u_sram"]["stub_source"] == "sram_1rw_bbox.v"
    assert manifest["sources"]["wrapper"] == "sram_1rw_mbist.v"
    assert manifest["sources"]["algo"] == [
        "march_c/march_c_algo.sv", "march_c/march_c_fsm.sv", "march_c/march_c_top.sv",
    ]
    assert manifest["test_access"] is None


def test_onchip_row_repair_only(tmp_path: Path) -> None:
    cfg = _dedicated_config(num_spare_rows=1, num_spare_cols=0, onchip_selfrepair=True)
    manifest = build_instance_manifest(cfg, tmp_path, tool_version="0.0.0")
    paths = _instances_by_path(manifest)
    assert set(paths) == {"u_sram", "u_onchip_analyzer", "u_onchip_selfrepair_ctrl", "u_repair_remap"}
    assert paths["u_onchip_analyzer"]["module_type"] == "onchip_row_repair_analyzer"
    assert paths["u_onchip_analyzer"]["category"] == "self_repair"
    assert paths["u_repair_remap"]["module_type"] == "repair_remap_row"
    for name in ("u_onchip_analyzer", "u_onchip_selfrepair_ctrl", "u_repair_remap"):
        assert paths[name]["hierarchy_hint"] == "keep_hierarchy"


def test_onchip_row_and_col_repair_plus_diagnosis(tmp_path: Path) -> None:
    cfg = _dedicated_config(
        num_spare_rows=1, num_spare_cols=1,
        onchip_selfrepair=True, onchip_col_repair=True,
        onchip_diagnosis=True, num_diagnosis_entries=4,
    )
    manifest = build_instance_manifest(cfg, tmp_path, tool_version="0.0.0")
    paths = _instances_by_path(manifest)
    assert set(paths) == {
        "u_sram", "u_onchip_analyzer", "u_onchip_selfrepair_ctrl",
        "u_onchip_diagnosis", "u_repair_remap", "u_repair_remap_col",
    }
    assert paths["u_onchip_analyzer"]["module_type"] == "onchip_2d_repair_analyzer"
    assert paths["u_onchip_diagnosis"]["category"] == "diagnosis"
    assert paths["u_onchip_diagnosis"]["module_type"] == "onchip_diagnosis_log"
    assert paths["u_repair_remap_col"]["module_type"] == "repair_remap_col"


def test_tester_driven_redundancy_without_onchip_selfrepair(tmp_path: Path) -> None:
    # repair_ports:-driven (tester) redundancy -- no analyzer/ctrl instances,
    # just the remap(s), since nothing on-chip is computing the repair.
    cfg = _dedicated_config(num_spare_rows=1, num_spare_cols=1)
    manifest = build_instance_manifest(cfg, tmp_path, tool_version="0.0.0")
    paths = _instances_by_path(manifest)
    assert set(paths) == {"u_sram", "u_repair_remap", "u_repair_remap_col"}
    assert "u_onchip_analyzer" not in paths
    assert "u_onchip_selfrepair_ctrl" not in paths


def test_multi_port_dual_rw_col_repair_gets_per_port_col_remap(tmp_path: Path) -> None:
    cfg = _dedicated_config(num_spare_rows=1, num_spare_cols=1, onchip_selfrepair=True, onchip_col_repair=True)
    cfg["algo"] = "march-2rw"
    cfg["normalized_ports"] = {
        "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
        "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "we": "web1", "csb": "csb1"},
    }
    manifest = build_instance_manifest(cfg, tmp_path, tool_version="0.0.0")
    paths = _instances_by_path(manifest)
    assert set(paths) == {
        "u_sram", "u_onchip_analyzer", "u_onchip_selfrepair_ctrl",
        "u_repair_remap0", "u_repair_remap1", "u_repair_remap_col0", "u_repair_remap_col1",
    }


def test_multi_port_non_dual_rw_col_repair_gets_single_col_remap(tmp_path: Path) -> None:
    cfg = _dedicated_config(num_spare_rows=1, num_spare_cols=1, onchip_selfrepair=True, onchip_col_repair=True)
    cfg["algo"] = "march-1r1w"
    cfg["normalized_ports"] = {
        "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
        "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
    }
    manifest = build_instance_manifest(cfg, tmp_path, tool_version="0.0.0")
    paths = _instances_by_path(manifest)
    assert set(paths) == {
        "u_sram", "u_onchip_analyzer", "u_onchip_selfrepair_ctrl",
        "u_repair_remap0", "u_repair_remap1", "u_repair_remap_col",
    }


def test_multi_port_tester_driven_redundancy_is_left_unenumerated(tmp_path: Path) -> None:
    # No template branch exists for this combination (wrapper_template.j2's
    # multi-port repair_remap_row loop only fires inside has_onchip_selfrepair) --
    # only the memory instance should be reported, not a guess.
    cfg = _dedicated_config(num_spare_rows=1, num_spare_cols=0)
    cfg["algo"] = "march-2rw"
    cfg["normalized_ports"] = {
        "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
        "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "we": "web1", "csb": "csb1"},
    }
    manifest = build_instance_manifest(cfg, tmp_path, tool_version="0.0.0")
    paths = _instances_by_path(manifest)
    assert set(paths) == {"u_sram"}


def test_shared_bus_generate_loop_instances_per_memory(tmp_path: Path) -> None:
    cfg = _dedicated_config(num_spare_rows=1, num_spare_cols=1, onchip_selfrepair=True, onchip_col_repair=True)
    cfg["topology"] = "shared-bus"
    cfg["memory_name"] = "sram_shared"
    cfg["wrapper_module_name"] = "shared_ctrl"
    cfg["memories"] = [{"name": "bank0"}, {"name": "bank1"}]
    manifest = build_instance_manifest(cfg, tmp_path, tool_version="0.0.0")
    assert manifest["generator"]["topology"] == "shared-bus"
    paths = _instances_by_path(manifest)
    assert set(paths) == {
        "u_mem_bank0", "u_mem_bank1",
        "selfrepair_inst[0].u_onchip_analyzer", "selfrepair_inst[0].u_onchip_selfrepair_ctrl",
        "selfrepair_inst[0].u_repair_remap", "selfrepair_inst[0].u_repair_remap_col",
        "selfrepair_inst[1].u_onchip_analyzer", "selfrepair_inst[1].u_onchip_selfrepair_ctrl",
        "selfrepair_inst[1].u_repair_remap", "selfrepair_inst[1].u_repair_remap_col",
    }
    assert paths["u_mem_bank0"]["module_type"] == "sram_shared"
    assert paths["u_mem_bank1"]["stub_source"] == "sram_shared_bbox.v"
    # The real bug controller_sources() used to have (faultflow_flow.py):
    # under shared-bus the wrapper file is named after wrapper_module_name,
    # not memory_name.
    assert manifest["sources"]["wrapper"] == "shared_ctrl_mbist.v"


def test_shared_bus_without_onchip_selfrepair_has_only_memory_instances(tmp_path: Path) -> None:
    cfg = _dedicated_config()
    cfg["topology"] = "shared-bus"
    cfg["memory_name"] = "sram_shared"
    cfg["wrapper_module_name"] = "shared_ctrl"
    cfg["memories"] = [{"name": "bank0"}]
    manifest = build_instance_manifest(cfg, tmp_path, tool_version="0.0.0")
    paths = _instances_by_path(manifest)
    assert set(paths) == {"u_mem_bank0"}


def test_write_instance_manifest_roundtrips(tmp_path: Path) -> None:
    manifest = build_instance_manifest(_dedicated_config(), tmp_path, tool_version="0.0.0")
    path = write_instance_manifest(manifest, tmp_path)
    assert path == tmp_path / "manifest.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["format"] == MANIFEST_FORMAT
    assert loaded["instances"][0]["instance_name"] == "u_sram"


def test_update_manifest_with_test_access_patches_in_place(tmp_path: Path) -> None:
    manifest = build_instance_manifest(_dedicated_config(), tmp_path, tool_version="0.0.0")
    write_instance_manifest(manifest, tmp_path)

    block = {
        "wrapped": True,
        "output_verilog": str(tmp_path / "out" / "sram_1rw_mbist_test_access.v"),
        "internal_instances": "not_enumerated",
        "hierarchy_hint": "opaque_shell",
    }
    path = update_manifest_with_test_access(tmp_path, block)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["test_access"] == block
    # Everything else must survive untouched.
    assert loaded["instances"][0]["instance_name"] == "u_sram"


def test_update_manifest_with_test_access_requires_existing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        update_manifest_with_test_access(tmp_path, {"wrapped": True})
