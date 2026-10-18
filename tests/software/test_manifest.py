from __future__ import annotations

import json
from pathlib import Path

import pytest

from autombist.manifest import (
    MANIFEST_FORMAT,
    ManifestError,
    build_instance_manifest,
    build_test_access_block,
    render_memory_stub,
    synthesis_sources,
    update_manifest_with_test_access,
    write_instance_manifest,
)

SP = {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}
P2RW = {
    "porta": {**SP},
    "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "we": "web1", "csb": "csb1"},
}
P1R1W = {
    "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
    "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
}


def _config(**redundancy) -> dict:
    cfg = {
        "memory_name": "sram_1rw",
        "wrapper_module_name": "sram_1rw_mbist",
        "addr_width": 4,
        "data_width": 8,
        "read_latency": 1,
        "we_active_low": True,
        "algo": "march-c",
        "algo_dir": "march_c",
        "algo_top_module": "march_c_top",
        "normalized_ports": {"port0": dict(SP)},
    }
    if redundancy:
        cfg["redundancy"] = {"mem_addr_width": 5, "mem_data_width": 8 + redundancy.get("num_spare_cols", 0), **redundancy}
    return cfg


def _shared_bus(cfg: dict, *names: str) -> dict:
    cfg = dict(cfg)
    cfg.update(topology="shared-bus", memory_name="sram_shared", wrapper_module_name="shared_ctrl",
               memories=[{"name": n} for n in names])
    return cfg


def _by_path(manifest: dict) -> dict:
    return {i["hierarchical_path"]: i for i in manifest["instances"]}


def _manifest(cfg: dict, tmp_path: Path) -> dict:
    return build_instance_manifest(cfg, tmp_path, tool_version="0.0.0")


def test_bare_config_has_memory_and_controller(tmp_path: Path) -> None:
    manifest = _manifest(_config(), tmp_path)
    assert manifest["format"] == MANIFEST_FORMAT
    assert manifest["top_module"] == "sram_1rw_mbist"
    assert manifest["sources"] == {"wrapper": "sram_1rw_mbist.v", "blackbox_stub": "sram_1rw_bbox.v"}
    paths = _by_path(manifest)
    assert set(paths) == {"u_sram", "u_algo_top"}
    mem = paths["u_sram"]
    assert mem["hierarchy_hint"] == "blackbox"
    assert mem["sources"] == ["sram_1rw_bbox.v"]
    ctrl = paths["u_algo_top"]
    assert ctrl["category"] == "mbist_controller"
    assert ctrl["hierarchy_hint"] == "separate"
    assert ctrl["module_type"] == "march_c_top"
    assert ctrl["parameters"] == {"ADDR_WIDTH": 4, "DATA_WIDTH": 8, "READ_LATENCY": 1}
    assert ctrl["sources"] == ["march_c/march_c_algo.sv", "march_c/march_c_fsm.sv", "march_c/march_c_top.sv"]
    assert manifest["test_access"] is None


def test_onchip_row_repair_instruments_carry_their_parameters(tmp_path: Path) -> None:
    paths = _by_path(_manifest(_config(num_spare_rows=2, num_spare_cols=0, onchip_selfrepair=True), tmp_path))
    assert set(paths) == {"u_sram", "u_algo_top", "u_onchip_analyzer", "u_onchip_selfrepair_ctrl", "u_repair_remap"}
    assert paths["u_onchip_analyzer"]["module_type"] == "onchip_row_repair_analyzer"
    assert paths["u_onchip_analyzer"]["parameters"] == {"ADDR_WIDTH": 4, "NUM_SPARE_ROWS": 2}
    assert paths["u_onchip_selfrepair_ctrl"]["parameters"] == {}
    assert paths["u_repair_remap"]["parameters"] == {"ADDR_WIDTH": 4, "NUM_SPARE_ROWS": 2}
    assert paths["u_repair_remap"]["sources"] == ["repair_remap_row.sv"]
    for inst in paths.values():
        assert inst["hierarchy_hint"] == ("blackbox" if inst["category"] == "memory" else "separate")


def test_onchip_row_and_col_repair_plus_diagnosis(tmp_path: Path) -> None:
    cfg = _config(num_spare_rows=1, num_spare_cols=1, onchip_selfrepair=True, onchip_col_repair=True,
                  onchip_diagnosis=True, num_diagnosis_entries=4)
    paths = _by_path(_manifest(cfg, tmp_path))
    assert set(paths) == {"u_sram", "u_algo_top", "u_onchip_analyzer", "u_onchip_selfrepair_ctrl",
                          "u_onchip_diagnosis", "u_repair_remap", "u_repair_remap_col"}
    assert paths["u_onchip_analyzer"]["module_type"] == "onchip_2d_repair_analyzer"
    assert paths["u_onchip_analyzer"]["parameters"] == {
        "ADDR_WIDTH": 4, "DATA_WIDTH": 8, "NUM_SPARE_ROWS": 1, "NUM_SPARE_COLS": 1}
    assert paths["u_onchip_diagnosis"]["category"] == "diagnosis"
    assert paths["u_onchip_diagnosis"]["parameters"] == {"ADDR_WIDTH": 4, "NUM_DIAGNOSIS_ENTRIES": 4}
    assert paths["u_repair_remap_col"]["parameters"] == {"DATA_WIDTH": 8, "NUM_SPARE_COLS": 1}


def test_tester_driven_redundancy_has_remaps_but_no_onchip_logic(tmp_path: Path) -> None:
    paths = _by_path(_manifest(_config(num_spare_rows=1, num_spare_cols=1), tmp_path))
    assert set(paths) == {"u_sram", "u_algo_top", "u_repair_remap", "u_repair_remap_col"}


def test_multi_port_dual_rw_col_repair_gets_per_port_col_remap(tmp_path: Path) -> None:
    cfg = _config(num_spare_rows=1, num_spare_cols=1, onchip_selfrepair=True, onchip_col_repair=True)
    cfg.update(algo="march-2rw", algo_dir="march_2rw", algo_top_module="march_2rw_top", normalized_ports=P2RW)
    assert set(_by_path(_manifest(cfg, tmp_path))) == {
        "u_sram", "u_algo_top", "u_onchip_analyzer", "u_onchip_selfrepair_ctrl",
        "u_repair_remap0", "u_repair_remap1", "u_repair_remap_col0", "u_repair_remap_col1"}


def test_multi_port_non_dual_rw_col_repair_gets_single_col_remap(tmp_path: Path) -> None:
    cfg = _config(num_spare_rows=1, num_spare_cols=1, onchip_selfrepair=True, onchip_col_repair=True)
    cfg.update(algo="march-1r1w", algo_dir="march_1r1w", algo_top_module="march_1r1w_top", normalized_ports=P1R1W)
    assert set(_by_path(_manifest(cfg, tmp_path))) == {
        "u_sram", "u_algo_top", "u_onchip_analyzer", "u_onchip_selfrepair_ctrl",
        "u_repair_remap0", "u_repair_remap1", "u_repair_remap_col"}


def test_multi_port_tester_driven_redundancy_is_left_unenumerated(tmp_path: Path) -> None:
    # No template branch exists for this combination -- report no remap rather than guess.
    cfg = _config(num_spare_rows=1, num_spare_cols=0)
    cfg.update(algo="march-2rw", algo_dir="march_2rw", algo_top_module="march_2rw_top", normalized_ports=P2RW)
    assert set(_by_path(_manifest(cfg, tmp_path))) == {"u_sram", "u_algo_top"}


def test_shared_bus_generate_loop_instances_per_memory(tmp_path: Path) -> None:
    cfg = _shared_bus(_config(num_spare_rows=1, num_spare_cols=1, onchip_selfrepair=True, onchip_col_repair=True),
                      "bank0", "bank1")
    manifest = _manifest(cfg, tmp_path)
    assert manifest["generator"]["topology"] == "shared-bus"
    paths = _by_path(manifest)
    per_mem = {"u_onchip_analyzer", "u_onchip_selfrepair_ctrl", "u_repair_remap", "u_repair_remap_col"}
    assert set(paths) == {"u_mem_bank0", "u_mem_bank1", "u_algo_top"} | {
        f"selfrepair_inst[{i}].{name}" for i in (0, 1) for name in per_mem}
    assert paths["u_mem_bank1"]["module_type"] == "sram_shared"
    assert paths["u_mem_bank1"]["sources"] == ["sram_shared_bbox.v"]
    assert paths["selfrepair_inst[1].u_onchip_analyzer"]["instance_name"] == "u_onchip_analyzer"
    # Under shared-bus the wrapper file is named after wrapper_module_name, not memory_name.
    assert manifest["sources"]["wrapper"] == "shared_ctrl_mbist.v"


def test_shared_bus_without_onchip_selfrepair(tmp_path: Path) -> None:
    paths = _by_path(_manifest(_shared_bus(_config(), "bank0"), tmp_path))
    assert set(paths) == {"u_mem_bank0", "u_algo_top"}


def test_saboteur_output_dir_is_rejected(tmp_path: Path) -> None:
    cfg = _config()
    cfg["use_saboteur"] = True
    with pytest.raises(ManifestError, match="--test"):
        _manifest(cfg, tmp_path)


def test_synthesis_sources_are_wrapper_plus_instrument_rtl_without_memory(tmp_path: Path) -> None:
    cfg = _config(num_spare_rows=1, num_spare_cols=0, onchip_selfrepair=True)
    names = [p.relative_to(tmp_path).as_posix() for p in synthesis_sources(cfg, tmp_path)]
    assert names == [
        "sram_1rw_mbist.v",
        "march_c/march_c_algo.sv", "march_c/march_c_fsm.sv", "march_c/march_c_top.sv",
        "onchip_row_repair_analyzer.sv", "onchip_selfrepair_ctrl.sv", "repair_remap_row.sv",
    ]


def test_memory_stub_declares_all_overridable_parameters_and_widened_ports() -> None:
    cfg = _config(num_spare_rows=1, num_spare_cols=1, onchip_selfrepair=True, onchip_col_repair=True)
    cfg["normalized_ports"] = {"port0": {**SP, "spare_wen": "spare_wen0"}}
    stub = render_memory_stub(cfg)
    assert "(* blackbox *)" in stub and "module sram_1rw #(" in stub
    for param in ("ADDR_WIDTH = 4", "DATA_WIDTH = 8", "NUM_SPARE_ROWS = 1", "NUM_SPARE_COLS = 1"):
        assert f"parameter integer {param}" in stub
    assert "input  wire [4:0] addr0" in stub      # mem_addr_width (spare row)
    assert "input  wire [8:0] din0" in stub       # mem_data_width (spare col)
    assert "output wire [8:0] dout0" in stub
    assert "input  wire [0:0] spare_wen0" in stub


def test_memory_stub_dedupes_a_shared_clock_pin() -> None:
    cfg = _config()
    shared_clk = {**P2RW["portb"], "clk": "clk0"}
    cfg["normalized_ports"] = {"porta": P2RW["porta"], "portb": shared_clk}
    stub = render_memory_stub(cfg)
    assert stub.count(" clk0") == 1
    assert "output wire [7:0] dout1" in stub


def test_write_instance_manifest_roundtrips(tmp_path: Path) -> None:
    path = write_instance_manifest(_manifest(_config(), tmp_path), tmp_path)
    assert path == tmp_path / "manifest.json"
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == MANIFEST_FORMAT


def test_update_manifest_with_test_access_patches_in_place(tmp_path: Path) -> None:
    write_instance_manifest(_manifest(_config(), tmp_path), tmp_path)
    block = {"wrapped": True, "hierarchy_hint": "opaque_shell"}
    loaded = json.loads(update_manifest_with_test_access(tmp_path, block).read_text(encoding="utf-8"))
    assert loaded["test_access"] == block
    assert {i["hierarchical_path"] for i in loaded["instances"]} == {"u_sram", "u_algo_top"}


def test_update_manifest_with_test_access_requires_existing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        update_manifest_with_test_access(tmp_path, {"wrapped": True})


def _enumerated(*, memory_blackbox: bool = True, tdr_type: str = "instrument_write", bits=(0,)) -> list[dict]:
    def e(instance, module_type, **kw):
        return {"instance": instance, "module_type": module_type, "is_blackbox": False,
                "sib_name": None, "instrument_name": None, "instrument_bit": None, "mux_name": None, **kw}
    return [
        e("u_sram", "sram_1rw", is_blackbox=memory_blackbox),
        e("u_algo_top", r"\$paramod$abc\march_c_top"),
        e("warptap_tap_core", "tap_core"),
        e("warptap_sib_bist_start", "sib_cell", sib_name="sib_bist_start", instrument_name="bist_start"),
        *[e(f"warptap_sib_bist_start_inst_{k}", tdr_type, sib_name="sib_bist_start", instrument_bit=k)
          for k in bits],
    ]


def _block(tmp_path: Path, enumerated: list[dict], width: int = 1, role: str = "control") -> dict:
    manifest = _manifest(_config(), tmp_path)
    return build_test_access_block(
        manifest, enumerated, [{"name": "bist_start", "role": role, "width": width}],
        output_verilog=tmp_path / "x_test_access.v", output_dir=tmp_path, icl_path=None,
    )


def test_test_access_block_lists_every_wrapped_instance(tmp_path: Path) -> None:
    block = _block(tmp_path, _enumerated())
    paths = {i["hierarchical_path"]: i for i in block["instances"]}
    assert paths["warptap_tap_core"]["category"] == "jtag_tap"
    assert paths["warptap_sib_bist_start"]["category"] == "ijtag_sib"
    assert paths["warptap_sib_bist_start"]["instrument"] == "bist_start"
    assert paths["warptap_sib_bist_start_inst_0"]["category"] == "ijtag_tdr"
    assert paths["warptap_sib_bist_start_inst_0"]["bit"] == 0
    # MBIST blocks keep their base-manifest category but carry the module name as
    # it appears in the wrapped netlist (parameter-specialized, no parameters).
    assert paths["u_algo_top"]["category"] == "mbist_controller"
    assert paths["u_algo_top"]["module_type"] == r"\$paramod$abc\march_c_top"
    assert "parameters" not in paths["u_algo_top"]
    assert paths["u_sram"]["hierarchy_hint"] == "blackbox"
    assert paths["u_sram"]["sources"] == ["sram_1rw_bbox.v"]
    assert all(i["hierarchy_hint"] == "separate" for p, i in paths.items() if p != "u_sram")
    assert block["memory_blackboxed"] is True
    assert block["boundary_ports"] == ["tck", "tms", "tdi", "tdo", "trst_n"]
    assert block["instruments"] == [{
        "name": "bist_start", "role": "control", "width": 1,
        "sib": "warptap_sib_bist_start", "tdr_bits": ["warptap_sib_bist_start_inst_0"],
    }]


def test_test_access_block_records_the_bsdl_and_the_tap(tmp_path: Path) -> None:
    tap = {"idcode": "0x5CA1AB1F", "idcode_is_placeholder": False, "instruction_length": 4,
           "network_access_instruction": "EXTEST", "network_access_opcode": "0000",
           "bsdl_entity": "x_ctrl", "tck_max_freq_hz": 25e6}
    manifest = _manifest(_config(), tmp_path)

    block = build_test_access_block(
        manifest, _enumerated(), [{"name": "bist_start", "role": "control", "width": 1}],
        output_verilog=tmp_path / "x_test_access.v", output_dir=tmp_path,
        icl_path=tmp_path / "x.icl", bsdl_path=tmp_path / "x.bsd", tap=tap,
    )

    assert block["bsdl_path"] == str((tmp_path / "x.bsd").resolve())
    assert block["icl_path"] == str((tmp_path / "x.icl").resolve())
    assert block["tap"] == tap


def test_test_access_block_without_a_bsdl_or_tap_says_so(tmp_path: Path) -> None:
    block = _block(tmp_path, _enumerated())

    assert block["bsdl_path"] is None and block["tap"] is None


def test_test_access_block_orders_wide_tdr_bits(tmp_path: Path) -> None:
    block = _block(tmp_path, _enumerated(bits=(2, 0, 1)), width=3)
    assert block["instruments"][0]["tdr_bits"] == [f"warptap_sib_bist_start_inst_{k}" for k in range(3)]


def test_test_access_block_reports_a_synthesized_memory(tmp_path: Path) -> None:
    assert _block(tmp_path, _enumerated(memory_blackbox=False))["memory_blackboxed"] is False


@pytest.mark.parametrize("enumerated, width, role", [
    (_enumerated(bits=(0,)), 2, "control"),                    # a TDR bit missing
    (_enumerated(tdr_type="bc1_shift_only"), 1, "control"),    # wrong TDR cell for the role
])
def test_test_access_block_rejects_tdr_cells_that_do_not_match_the_port(
    tmp_path: Path, enumerated: list[dict], width: int, role: str,
) -> None:
    with pytest.raises(ManifestError, match="does not match its TDR cells"):
        _block(tmp_path, enumerated, width=width, role=role)


def test_test_access_block_rejects_a_port_without_a_sib(tmp_path: Path) -> None:
    enumerated = [e for e in _enumerated() if e["module_type"] != "sib_cell"]
    with pytest.raises(ManifestError, match="no SIB"):
        _block(tmp_path, enumerated)


def test_test_access_block_rejects_a_missing_memory(tmp_path: Path) -> None:
    enumerated = [e for e in _enumerated() if e["instance"] != "u_sram"]
    with pytest.raises(ManifestError, match="memory instance"):
        _block(tmp_path, enumerated)
