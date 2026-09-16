"""Pin `classify_test_access_ports`'s pure port-list logic -- no warptap, iverilog, or
yosys needed, so (unlike tests/integration/test_testaccess_warptap_e2e.py, which is
correctly skip-gated on all three for its real-insertion/simulation tests) this runs in
every normal test invocation, catching a wrapper_template.j2 port-list regression
immediately rather than only when a developer happens to have warptap installed.
"""
from __future__ import annotations

import pytest

from autombist.testaccess import TestAccessPort, classify_test_access_ports, test_access_kwargs_from_config


def test_base_ports_only(tmp_path) -> None:
    ports = classify_test_access_ports()
    assert ports == [
        TestAccessPort("test_mode", "control"),
        TestAccessPort("bist_start", "control"),
        TestAccessPort("bist_done", "status"),
        TestAccessPort("bist_fail", "status"),
    ]


def test_onchip_selfrepair_adds_four_ports(tmp_path) -> None:
    ports = classify_test_access_ports(onchip_selfrepair=True)
    assert [p.name for p in ports] == [
        "test_mode", "bist_start", "bist_done", "bist_fail",
        "self_repair_start", "self_repair_done", "self_repair_fail", "self_repair_busy",
    ]
    assert [p.role for p in ports] == [
        "control", "control", "status", "status",
        "control", "status", "status", "status",
    ]


def test_onchip_repair_persistence_adds_two_ports(tmp_path) -> None:
    ports = classify_test_access_ports(onchip_selfrepair=True, onchip_repair_persistence=True)
    assert [p.name for p in ports[-2:]] == ["repair_load", "repair_load_done"]
    assert [p.role for p in ports[-2:]] == ["control", "status"]


def test_onchip_diagnosis_adds_only_diag_overflow(tmp_path) -> None:
    """Without num_diagnosis_entries/addr_width geometry, diag_valid/diag_addr are not
    added -- this is the backward-compatibility case (every caller before wide-port
    support only ever passed the boolean), not a permanent exclusion; see
    test_onchip_diagnosis_with_geometry_adds_diag_valid_and_addr below for the
    geometry-given case."""
    ports = classify_test_access_ports(onchip_selfrepair=True, onchip_diagnosis=True)
    assert ports[-1] == TestAccessPort("diag_overflow", "status")
    assert "diag_valid" not in [p.name for p in ports]
    assert "diag_addr" not in [p.name for p in ports]


def test_onchip_diagnosis_with_geometry_adds_diag_valid_and_addr(tmp_path) -> None:
    """With num_diagnosis_entries/addr_width, diag_valid/diag_addr appear -- widths
    matching wrapper_template.j2's own [num_diagnosis_entries-1:0] and
    [num_diagnosis_entries*ADDR_WIDTH-1:0], positioned before diag_overflow, matching
    the template's own declaration order (diag_valid, diag_addr, diag_overflow)."""
    ports = classify_test_access_ports(
        onchip_selfrepair=True, onchip_diagnosis=True,
        num_diagnosis_entries=4, addr_width=6,
    )
    assert [(p.name, p.role, p.width) for p in ports[-3:]] == [
        ("diag_valid", "status", 4),
        ("diag_addr", "status", 24),
        ("diag_overflow", "status", 1),
    ]


def test_onchip_repair_persistence_with_geometry_adds_fuse_ports(tmp_path) -> None:
    """With num_spare_rows/addr_width, fuse_row_repair_en/fuse_faulty_row_addr appear
    between repair_load and repair_load_done, matching wrapper_template.j2's own
    declaration order exactly."""
    ports = classify_test_access_ports(
        onchip_selfrepair=True, onchip_repair_persistence=True,
        num_spare_rows=2, addr_width=6,
    )
    assert [(p.name, p.role, p.width) for p in ports[-4:]] == [
        ("repair_load", "control", 1),
        ("fuse_row_repair_en", "control", 2),
        ("fuse_faulty_row_addr", "control", 12),
        ("repair_load_done", "status", 1),
    ]


def test_wide_ports_absent_without_geometry_is_the_backward_compat_case(tmp_path) -> None:
    """The exact scenario every caller before wide-port support used: booleans True,
    no geometry kwargs at all. Must be byte-identical to what this function always
    returned -- the load-bearing proof that adding wide-port support didn't change
    behavior for any existing caller, only added a new opt-in path."""
    ports = classify_test_access_ports(
        onchip_selfrepair=True, onchip_repair_persistence=True, onchip_diagnosis=True,
    )
    assert ports == [
        TestAccessPort("test_mode", "control"),
        TestAccessPort("bist_start", "control"),
        TestAccessPort("bist_done", "status"),
        TestAccessPort("bist_fail", "status"),
        TestAccessPort("self_repair_start", "control"),
        TestAccessPort("self_repair_done", "status"),
        TestAccessPort("self_repair_fail", "status"),
        TestAccessPort("self_repair_busy", "status"),
        TestAccessPort("repair_load", "control"),
        TestAccessPort("repair_load_done", "status"),
        TestAccessPort("diag_overflow", "status"),
    ]


def test_repair_ports_generic_passthrough_both_directions(tmp_path) -> None:
    """repair_ports: is a generic {name, width, dir} passthrough (generator.py's
    _validate_repair_ports), not hardcoded to specific names -- dir='input' means the
    tester writes it (control/WRITE), dir='output' means the tester reads it
    (status/READ). Declared before the onchip_selfrepair block in wrapper_template.j2
    (line 29 vs 34), so classify_test_access_ports must put them first too -- these two
    configs are mutually exclusive in practice (generator.py), but nothing here needs
    to know that, matching this function's existing "trust the caller" convention."""
    ports = classify_test_access_ports(
        repair_ports=[
            {"name": "row_repair_en", "width": 2, "dir": "input"},
            {"name": "faulty_bit", "width": 5, "dir": "output"},
        ],
    )
    assert [(p.name, p.role, p.width) for p in ports] == [
        ("test_mode", "control", 1),
        ("bist_start", "control", 1),
        ("bist_done", "status", 1),
        ("bist_fail", "status", 1),
        ("row_repair_en", "control", 2),
        ("faulty_bit", "status", 5),
    ]


def test_kwargs_from_config_extracts_geometry_and_flags(tmp_path) -> None:
    """The exact shape a config.yml snapshot has: redundancy is a nested dict, addr_width
    is top-level. repair_ports defaults to () when absent, matching
    classify_test_access_ports's own default."""
    config = {
        "addr_width": 6,
        "redundancy": {
            "onchip_selfrepair": True, "onchip_repair_persistence": True, "onchip_diagnosis": True,
            "num_spare_rows": 2, "num_diagnosis_entries": 4,
        },
    }
    assert test_access_kwargs_from_config(config) == {
        "onchip_selfrepair": True, "onchip_repair_persistence": True, "onchip_diagnosis": True,
        "num_spare_rows": 2, "num_diagnosis_entries": 4, "addr_width": 6, "repair_ports": (),
    }


def test_kwargs_from_config_defaults_when_no_redundancy_block(tmp_path) -> None:
    """A plain non-redundant config (no redundancy: key at all) -- everything defaults
    to the always-narrow, backward-compatible shape."""
    assert test_access_kwargs_from_config({"addr_width": 8}) == {
        "onchip_selfrepair": False, "onchip_repair_persistence": False, "onchip_diagnosis": False,
        "num_spare_rows": 0, "num_diagnosis_entries": 0, "addr_width": 8, "repair_ports": (),
    }


def test_kwargs_from_config_passes_through_repair_ports(tmp_path) -> None:
    repair_ports = [{"name": "row_repair_en", "width": 1, "dir": "input"}]
    config = {"addr_width": 4, "repair_ports": repair_ports}
    assert test_access_kwargs_from_config(config)["repair_ports"] == repair_ports


def test_kwargs_from_config_requires_addr_width(tmp_path) -> None:
    with pytest.raises(ValueError, match="addr_width"):
        test_access_kwargs_from_config({"redundancy": {}})


def test_onchip_diagnosis_alone_still_adds_diag_overflow(tmp_path) -> None:
    """onchip_diagnosis is independent of onchip_selfrepair in this function's own
    signature (generator.py enforces the real dependency at config-validation time; this
    function trusts the caller, matching its own docstring) -- confirms the flag isn't
    silently no-op'd without onchip_selfrepair also being passed."""
    ports = classify_test_access_ports(onchip_diagnosis=True)
    assert ports[-1] == TestAccessPort("diag_overflow", "status")


def test_diagnosis_ports_come_after_persistence_ports_matching_declaration_order(tmp_path) -> None:
    """Order matches wrapper_template.j2's own port declaration order (self_repair_* ,
    then repair_load/fuse_*/repair_load_done, then diag_valid/diag_addr/diag_overflow) --
    checked explicitly since a future edit reordering the function could silently produce
    a chain order that no longer matches the generated Verilog by inspection."""
    ports = classify_test_access_ports(
        onchip_selfrepair=True, onchip_repair_persistence=True, onchip_diagnosis=True,
    )
    assert [p.name for p in ports] == [
        "test_mode", "bist_start", "bist_done", "bist_fail",
        "self_repair_start", "self_repair_done", "self_repair_fail", "self_repair_busy",
        "repair_load", "repair_load_done",
        "diag_overflow",
    ]
