from __future__ import annotations

from pathlib import Path

import pytest

from autombist.fault_primitives import Effect, FaultPrimitive, Sensitize, default_registry
from autombist.fault_ram_gen import (
    build_type_table,
    render_fault_ram,
    render_read_victim_arm,
    render_static_clamp_arm,
    render_write_aggressor_arm,
    render_write_victim_arm,
    registry_hash,
)


def test_render_static_clamp_arm_victim() -> None:
    sa0 = FaultPrimitive("SA0", "static_clamp", Sensitize(), Effect(kind="force", value="0"))
    arm = render_static_clamp_arm(sa0)
    assert arm == "T_SA0: if (mem[FQ[i].va][FQ[i].vb] != 1'b0) begin mem[FQ[i].va][FQ[i].vb] = 1'b0; FQ[i].hits++; end"


def test_render_static_clamp_arm_aggressor_gated() -> None:
    cfst = FaultPrimitive(
        "CFST", "static_clamp", Sensitize(pre="p0", on="aggressor"), Effect(kind="force", value="p1")
    )
    arm = render_static_clamp_arm(cfst)
    assert arm == (
        "T_CFST: if (mem[FQ[i].aa][FQ[i].ab] == FQ[i].p0[0] && mem[FQ[i].va][FQ[i].vb] != FQ[i].p1[0]) "
        "begin mem[FQ[i].va][FQ[i].vb] = FQ[i].p1[0]; FQ[i].hits++; end"
    )


def test_render_write_victim_arm_transition_fault() -> None:
    tf0 = FaultPrimitive("TF0", "write_effect", Sensitize(pre="0", written="1"), Effect(kind="block_write", value="0"))
    arm = render_write_victim_arm(tf0)
    assert arm == "T_TF0: if (old[b] == 1'b0 && d[b] == 1'b1) begin nxt[b] = 1'b0; FQ[i].hits++; end"


def test_render_write_aggressor_arm_invert() -> None:
    cfin = FaultPrimitive("CFIN", "write_effect", Sensitize(transition="p0", on="aggressor"), Effect(kind="invert"))
    arm = render_write_aggressor_arm(cfin)
    assert arm == (
        "T_CFIN: if (dir_match(FQ[i].p0, up, dn)) "
        "begin mem[FQ[i].va][FQ[i].vb] = ~mem[FQ[i].va][FQ[i].vb]; FQ[i].hits++; end"
    )


def test_render_read_victim_arm_corrupt_read_only() -> None:
    irf0 = FaultPrimitive("IRF0", "read_effect", Sensitize(pre="0"), Effect(kind="corrupt_read", value="1"))
    arm = render_read_victim_arm(irf0)
    assert arm == "T_IRF0: if (old[b] == 1'b0) begin rv[b] = 1'b1; FQ[i].hits++; end"


def test_render_read_victim_arm_deceptive_read_disturb() -> None:
    drdf0 = FaultPrimitive(
        "DRDF0", "read_effect", Sensitize(pre="0"), Effect(kind="force_read", value="1", also_read="0")
    )
    arm = render_read_victim_arm(drdf0)
    # cell gets forced to 1 (the "real" corruption), but the read returns 0 (the deception)
    assert arm == "T_DRDF0: if (old[b] == 1'b0) begin mem[ea][b] = 1'b1; rv[b] = 1'b0; FQ[i].hits++; end"


def test_render_uses_raw_sv_verbatim_when_present() -> None:
    weird = FaultPrimitive("MYWEIRD", "write_effect", raw_sv="begin nxt[b] = 1'bx; FQ[i].hits++; end")
    assert render_write_victim_arm(weird) == "T_MYWEIRD: begin nxt[b] = 1'bx; FQ[i].hits++; end"


def test_build_type_table_registry_first_then_fixed() -> None:
    reg = default_registry()[:2]  # SA0, SA1
    table = build_type_table(reg)
    names = [name for name, _code in table]
    assert names[:2] == ["SA0", "SA1"]
    assert names[2:] == ["SOF", "AF_NOACC", "AF_ALIAS", "CFDS"]
    codes = [code for _name, code in table]
    assert codes == list(range(6))  # contiguous, starting at 0


def test_render_fault_ram_rejects_duplicate_names() -> None:
    dup = default_registry()[:1] * 2  # same object twice -> same name twice
    with pytest.raises(ValueError, match="duplicate fault type name"):
        render_fault_ram(dup)


def test_render_fault_ram_rejects_fixed_type_collision() -> None:
    collide = [FaultPrimitive("SOF", "static_clamp", Sensitize(), Effect(kind="force", value="0"))]
    with pytest.raises(ValueError, match="collides with a fixed built-in type"):
        render_fault_ram(collide)


def test_render_fault_ram_contains_all_19_type_codes() -> None:
    text = render_fault_ram(default_registry())
    for name in [p.name for p in default_registry()] + ["SOF", "AF_NOACC", "AF_ALIAS", "CFDS"]:
        assert f"T_{name}" in text
    assert "module fault_ram" in text
    assert "endmodule" in text


def test_registry_hash_is_stable_and_sensitive_to_content() -> None:
    reg_a = default_registry()
    reg_b = default_registry()
    assert registry_hash(reg_a) == registry_hash(reg_b)  # same content -> same hash

    reg_c = default_registry() + [
        FaultPrimitive("MYNEW", "static_clamp", Sensitize(), Effect(kind="force", value="1"))
    ]
    assert registry_hash(reg_c) != registry_hash(reg_a)


def test_render_and_write_writes_a_file(tmp_path: Path) -> None:
    from autombist.fault_ram_gen import render_and_write

    out = render_and_write(default_registry(), tmp_path / "fault_ram.sv")
    assert out.exists()
    assert "module fault_ram" in out.read_text(encoding="utf-8")
