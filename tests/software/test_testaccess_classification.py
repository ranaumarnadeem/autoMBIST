"""Pin `classify_test_access_ports`'s pure port-list logic -- no warptap, iverilog, or
yosys needed, so (unlike tests/integration/test_testaccess_warptap_e2e.py, which is
correctly skip-gated on all three for its real-insertion/simulation tests) this runs in
every normal test invocation, catching a wrapper_template.j2 port-list regression
immediately rather than only when a developer happens to have warptap installed.
"""
from __future__ import annotations

from autombist.testaccess import TestAccessPort, classify_test_access_ports


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
    """diag_valid/diag_addr are deliberately excluded (both multi-bit -- see
    testaccess.py's own module docstring for why); only diag_overflow, confirmed
    single-bit against wrapper_template.j2, is wrappable today."""
    ports = classify_test_access_ports(onchip_selfrepair=True, onchip_diagnosis=True)
    assert ports[-1] == TestAccessPort("diag_overflow", "status")
    assert "diag_valid" not in [p.name for p in ports]
    assert "diag_addr" not in [p.name for p in ports]


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
