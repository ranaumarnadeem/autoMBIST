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
    assert names[2:] == ["SOF", "AF_NOACC", "AF_ALIAS", "CFDS", "DRF", "HSD"]
    codes = [code for _name, code in table]
    assert codes == list(range(8))  # contiguous, starting at 0


def test_render_fault_ram_rejects_duplicate_names() -> None:
    dup = default_registry()[:1] * 2  # same object twice -> same name twice
    with pytest.raises(ValueError, match="duplicate fault type name"):
        render_fault_ram(dup)


def test_render_fault_ram_rejects_fixed_type_collision() -> None:
    collide = [FaultPrimitive("SOF", "static_clamp", Sensitize(), Effect(kind="force", value="0"))]
    with pytest.raises(ValueError, match="collides with a fixed built-in type"):
        render_fault_ram(collide)


def test_render_fault_ram_contains_all_21_type_codes() -> None:
    text = render_fault_ram(default_registry())
    for name in [p.name for p in default_registry()] + ["SOF", "AF_NOACC", "AF_ALIAS", "CFDS", "DRF", "HSD"]:
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


# --------------------------------------------------------------------------- #
# Multi-port (P4 of the fault-DSL generalization): num_ports=1 must remain
# byte-identical to the pre-multi-port template; num_ports=2 renders a second,
# explicit-suffix port bus and threads port-matching through the fault struct.
# --------------------------------------------------------------------------- #
def test_render_fault_ram_num_ports_1_implicit_and_explicit_are_identical() -> None:
    """The num_ports parameter is purely additive: omitting it and passing
    num_ports=1 explicitly must render byte-for-byte identical text."""
    implicit = render_fault_ram(default_registry())
    explicit = render_fault_ram(default_registry(), num_ports=1)
    assert implicit == explicit


def test_render_fault_ram_num_ports_1_is_byte_identical_to_pre_phase_golden() -> None:
    """Pins render_fault_ram(default_registry()) to its exact sha256. Deliberately
    re-pinned across both Workstream K (DRF) and Workstream L (HSD), and again
    for the fatal-cascade fix (had_fatal guard + valid-type-name list in the
    unknown-fault-type message): each added a new fixed type or a real text
    change, so num_ports=1 text growth each time is the expected, intended
    outcome, not a regression. Any *future* edit that changes a byte of this
    rendering must still fail this test and prompt a deliberate re-pin, exactly
    as these were."""
    import hashlib

    text = render_fault_ram(default_registry())
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert len(text) == 14791
    assert digest == "2a9e8cfc124a5b82f86d3abde87016fb0d7cb7e7704b14ff7377f326816f5079"


# --------------------------------------------------------------------------- #
# sensitize.port: the type-level per-port sensitizing constraint. Distinct from
# a fault line's APORT, which is instance-level and reaches only the aggressor
# site. Was validated and documented but never read by codegen, so a
# port-qualified type fired on BOTH ports and inflated the coverage number.
# --------------------------------------------------------------------------- #
def _port_prim(name: str, category: str, sensitize: Sensitize, effect: Effect) -> FaultPrimitive:
    return FaultPrimitive(name, category, sensitize, effect)


@pytest.mark.parametrize(
    "name,category,sensitize,effect,expected",
    [
        (
            "PV", "write_effect", Sensitize(pre="0", written="1", on="victim", port="1"),
            Effect(kind="force", value="0"),
            "T_PV: if (old[b] == 1'b0 && d[b] == 1'b1 && port == 1) "
            "begin nxt[b] = 1'b0; FQ[i].hits++; end",
        ),
        (
            "PA", "write_effect", Sensitize(transition="either", on="aggressor", port="1"),
            Effect(kind="invert"),
            "T_PA: if ((up || dn) && port == 1) begin mem[FQ[i].va][FQ[i].vb] = "
            "~mem[FQ[i].va][FQ[i].vb]; FQ[i].hits++; end",
        ),
        (
            "PR", "read_effect", Sensitize(pre="1", on="victim", port="0"),
            Effect(kind="corrupt_read", value="0"),
            "T_PR: if (old[b] == 1'b1 && port == 0) begin rv[b] = 1'b0; FQ[i].hits++; end",
        ),
    ],
)
def test_port_clause_emitted_at_each_supported_site(
    name: str, category: str, sensitize: Sensitize, effect: Effect, expected: str
) -> None:
    text = render_fault_ram(default_registry() + [_port_prim(name, category, sensitize, effect)],
                            num_ports=2)
    arm = next(ln.strip() for ln in text.splitlines() if f"T_{name}:" in ln)
    assert arm == expected


def test_port_clause_replaces_rather_than_ands_the_empty_condition() -> None:
    """_cond_clauses yields the literal 1'b1 for a primitive that constrains
    nothing; the port clause must take its place rather than produce the
    tautological `1'b1 && port == 1`."""
    prim = _port_prim("PB", "write_effect", Sensitize(on="victim", port="1"),
                      Effect(kind="force", value="0"))
    text = render_fault_ram(default_registry() + [prim], num_ports=2)
    arm = next(ln.strip() for ln in text.splitlines() if "T_PB:" in ln)
    assert arm == "T_PB: if (port == 1) begin nxt[b] = 1'b0; FQ[i].hits++; end"


def test_wildcard_port_emits_no_clause_so_builtin_render_is_unchanged() -> None:
    """Every built-in is port='x'. This is the regression check for the whole
    change: if _with_port ever stopped short-circuiting on the wildcard, every
    generated module in the project would shift."""
    for num_ports in (1, 2):
        text = render_fault_ram(default_registry(), num_ports=num_ports)
        generated_arms = [ln for ln in text.splitlines() if ln.strip().startswith("T_")]
        assert generated_arms, "sanity: arms must exist to make this meaningful"
        offenders = [ln.strip() for ln in generated_arms
                     if "port == 0" in ln or "port == 1" in ln]
        # T_SOF's per-port output-keeper is fixed template text, not a rendered
        # sensitize.port clause, so it is the one legitimate match.
        assert all("T_SOF:" in ln for ln in offenders), offenders


def test_single_port_render_rejects_a_port_qualified_type() -> None:
    """Not merely meaningless single-port: the 1-port write_op()/read_op() take
    no `port` argument, so an emitted clause would not compile. Refusing beats
    silently dropping the constraint the user asked for."""
    prim = _port_prim("PV", "write_effect", Sensitize(on="victim", port="1"),
                      Effect(kind="force", value="0"))
    with pytest.raises(ValueError, match="requires a 2-port memory"):
        render_fault_ram(default_registry() + [prim], num_ports=1)


def test_render_fault_ram_num_ports_1_has_unsuffixed_single_port_bus() -> None:
    text = render_fault_ram(default_registry(), num_ports=1)
    assert "input  logic                    clk,\n" in text
    assert "input  logic                    csb,    // active low chip select\n" in text
    assert "clk0" not in text
    assert "clk1" not in text
    assert " vp, ap" not in text
    assert "input int port" not in text


def test_render_fault_ram_num_ports_2_has_dual_explicit_suffix_port_buses() -> None:
    text = render_fault_ram(default_registry(), num_ports=2)
    # Two independent csb/web/addr/dout buses, explicit-suffix naming (matches
    # rtl/sram_model_2rw.sv's convention), not SV unpacked-array ports.
    for sig in ("clk0", "csb0", "web0", "wmask0", "addr0", "din0", "dout0"):
        assert sig in text
    for sig in ("clk1", "csb1", "web1", "wmask1", "addr1", "din1", "dout1"):
        assert sig in text
    # Unsuffixed single-port names must not leak into the two-port render.
    assert "logic                    clk,\n" not in text
    assert "logic                    csb,    // active low chip select\n" not in text


def test_render_fault_ram_num_ports_2_fault_struct_has_vp_ap_fields() -> None:
    text = render_fault_ram(default_registry(), num_ports=2)
    assert "int vp, ap;" in text


def test_render_fault_ram_num_ports_2_threads_port_param() -> None:
    text = render_fault_ram(default_registry(), num_ports=2)
    assert "input int port" in text
    # Aggressor-side coupling match (CFIN/CFID via the registry, CFDS fixed)
    # gates on the accessing port.
    assert "FQ[i].ap != port" in text
    assert text.count("FQ[i].ap != port") >= 2  # write-aggressor loop + read CFDS loop


def test_render_fault_ram_rejects_invalid_num_ports() -> None:
    with pytest.raises(ValueError, match="num_ports must be 1 or 2"):
        render_fault_ram(default_registry(), num_ports=3)


def test_render_and_write_num_ports_2(tmp_path: Path) -> None:
    from autombist.fault_ram_gen import render_and_write

    out = render_and_write(default_registry(), tmp_path / "fault_ram_2p.sv", num_ports=2)
    text = out.read_text(encoding="utf-8")
    assert "clk0" in text and "clk1" in text


# --------------------------------------------------------------------------- #
# DRF (Workstream K): single-port-only fixed type, deferred for num_ports=2
# behind a RUNTIME SystemVerilog guard -- not a Python-level render_fault_ram()
# raise. Codegen time has no visibility into whether any given campaign's
# fault LIST will ever actually contain a DRF entry (T_DRF's type code is
# always present in the type table regardless), so the guard must fire only
# when a DRF fault is actually loaded, at simulation time.
# --------------------------------------------------------------------------- #
def test_render_fault_ram_num_ports_1_has_drf_always_ff_block() -> None:
    text = render_fault_ram(default_registry(), num_ports=1)
    assert "T_DRF" in text
    assert "drf_idle_count" in text
    assert "drf_corrupted" in text
    assert "drf_hits" in text


def test_render_fault_ram_num_ports_2_has_runtime_drf_guard_not_a_raise() -> None:
    # render_fault_ram() itself must NOT raise for num_ports=2 -- T_DRF is
    # always in the type table, but the guard is emitted as SystemVerilog
    # text that only fires if a fault LIST loaded at simulation time actually
    # contains a DRF entry.
    text = render_fault_ram(default_registry(), num_ports=2)
    assert "T_DRF" in text
    assert "DRF is not yet supported for num_ports=2" in text
    assert "$finish" in text
    # The single-port idle-tracking block must not be present under num_ports=2.
    assert "drf_idle_count" not in text


def test_render_fault_ram_num_ports_1_has_no_num_ports_2_drf_guard_text() -> None:
    text = render_fault_ram(default_registry(), num_ports=1)
    assert "DRF is not yet supported for num_ports=2" not in text


# --------------------------------------------------------------------------- #
# HSD (Workstream L): unlike DRF, ships for BOTH num_ports=1 and num_ports=2
# in this same pass -- the check is stateless and lives inside write_op(),
# which the num_ports=2 template already calls once per port against the SAME
# shared mem[]/FQ[], so no runtime guard is needed for either port count.
# --------------------------------------------------------------------------- #
def test_render_fault_ram_has_words_per_row_parameter() -> None:
    for num_ports in (1, 2):
        text = render_fault_ram(default_registry(), num_ports=num_ports)
        assert "WORDS_PER_ROW" in text


def test_render_fault_ram_has_hsd_check_both_port_counts() -> None:
    for num_ports in (1, 2):
        text = render_fault_ram(default_registry(), num_ports=num_ports)
        assert "T_HSD" in text
        assert "FQ[i].t != T_HSD" in text
        # Unlike DRF, HSD gets no runtime num_ports=2 rejection guard.
        assert "HSD is not yet supported for num_ports=2" not in text


def test_render_fault_ram_num_ports_2_still_has_drf_guard_but_not_hsd() -> None:
    text = render_fault_ram(default_registry(), num_ports=2)
    assert "DRF is not yet supported for num_ports=2" in text
    assert "HSD is not yet supported for num_ports=2" not in text


# --------------------------------------------------------------------------- #
# conflicting_port_faults: sensitize.port (type-level) and a fault line's APORT
# (instance-level) both gate the aggressor's port, and meet at exactly one site.
# ANDing them is correct, but a disagreement yields a permanently dead arm that
# would otherwise score as an ordinary ESCAPE.
# --------------------------------------------------------------------------- #
def _aggressor_prim(port: str) -> FaultPrimitive:
    return FaultPrimitive(
        "PA", "write_effect", Sensitize(transition="either", on="aggressor", port=port),
        Effect(kind="invert"),
    )


def _rec(ftype: str, aport: int):
    from autombist.algo_engine import FaultRecord
    return FaultRecord(type=ftype, vaddr=5, vbit=1, aaddr=6, abit=1, p0=2, p1=0, aport=aport)


def test_conflicting_port_faults_reports_a_dead_arm() -> None:
    from autombist.fault_ram_gen import conflicting_port_faults

    msgs = conflicting_port_faults([_aggressor_prim("1")], [_rec("PA", aport=0)], num_ports=2)
    assert len(msgs) == 1
    assert "can never activate" in msgs[0]


def test_conflicting_port_faults_silent_when_they_agree() -> None:
    from autombist.fault_ram_gen import conflicting_port_faults

    assert conflicting_port_faults([_aggressor_prim("1")], [_rec("PA", aport=1)], num_ports=2) == []


def test_conflicting_port_faults_ignores_wildcard_types() -> None:
    """A port='x' type imposes no constraint, so no APORT can contradict it."""
    from autombist.fault_ram_gen import conflicting_port_faults

    assert conflicting_port_faults([_aggressor_prim("x")], [_rec("PA", aport=1)], num_ports=2) == []


def test_conflicting_port_faults_ignores_non_aggressor_sites() -> None:
    """Only the write_effect/aggressor arm has an APORT gate to collide with;
    a victim-site port constraint is orthogonal to the fault line's APORT."""
    from autombist.fault_ram_gen import conflicting_port_faults

    victim = FaultPrimitive(
        "PA", "write_effect", Sensitize(pre="0", written="1", on="victim", port="1"),
        Effect(kind="force", value="0"),
    )
    assert conflicting_port_faults([victim], [_rec("PA", aport=0)], num_ports=2) == []


def test_conflicting_port_faults_silent_single_port() -> None:
    """sensitize.port is rejected outright at num_ports=1, and the single-port
    engine never consults a fault line's ports."""
    from autombist.fault_ram_gen import conflicting_port_faults

    assert conflicting_port_faults([_aggressor_prim("1")], [_rec("PA", aport=0)], num_ports=1) == []


# ---------------------------------------------------------------------------
# sensitize.agg_pre: the two-cell state gate.
# ---------------------------------------------------------------------------


def _agg(**sens: str) -> FaultPrimitive:
    return FaultPrimitive(
        "TCOUP", "write_effect",
        Sensitize(pre="0", written="1", **sens),
        Effect(kind="block_write", value="0"),
    )


def test_agg_pre_emits_the_aggressor_state_clause_on_a_write_victim_arm() -> None:
    arm = render_write_victim_arm(_agg(agg_pre="p0"))
    assert "mem[FQ[i].aa][FQ[i].ab] == FQ[i].p0[0]" in arm
    # ANDed onto the victim's own clauses, not replacing them.
    assert "old[b] == 1'b0 && d[b] == 1'b1 && mem[FQ[i].aa][FQ[i].ab] == FQ[i].p0[0]" in arm


def test_agg_pre_emits_the_clause_on_a_read_victim_arm() -> None:
    prim = FaultPrimitive(
        "TCOUPR", "read_effect", Sensitize(pre="0", agg_pre="1"),
        Effect(kind="corrupt_read", value="1"),
    )
    arm = render_read_victim_arm(prim)
    assert "old[b] == 1'b0 && mem[FQ[i].aa][FQ[i].ab] == 1'b1" in arm


def test_agg_pre_wildcard_emits_no_clause() -> None:
    # The property every byte-identical built-in render depends on.
    assert "FQ[i].aa" not in render_write_victim_arm(_agg())


def test_agg_pre_alone_replaces_the_empty_condition_sentinel() -> None:
    """A primitive constraining ONLY the aggressor state must render
    `if (mem[...] == ...)`, not `if (1'b1 && mem[...] == ...)` -- the same
    sentinel rule sensitize.port follows."""
    prim = FaultPrimitive(
        "TCOUPO", "read_effect", Sensitize(agg_pre="1"),
        Effect(kind="corrupt_read", value="1"),
    )
    arm = render_read_victim_arm(prim)
    assert "if (mem[FQ[i].aa][FQ[i].ab] == 1'b1)" in arm
    assert "1'b1 &&" not in arm


def test_agg_pre_changes_the_registry_hash() -> None:
    """The build cache is keyed on registry_hash. If agg_pre were missing from
    to_dict(), two registries differing only in it would hash identically and
    the second would silently reuse the first's compiled engine -- i.e. run
    against the wrong RTL while reporting success."""
    base = default_registry()
    gated = default_registry() + [_agg(agg_pre="p0")]
    ungated = default_registry() + [_agg()]
    assert registry_hash(gated) != registry_hash(ungated)
    assert registry_hash(gated) != registry_hash(base)
