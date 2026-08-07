"""Unit tests for the Pattern-Graph greedy-walk march-test synthesizer
(src/autombist/synth_engine.py). No EDA tools, no Verilator -- these exercise
the pure-Python reference-memory oracle and the greedy walk directly.
"""
from __future__ import annotations

import itertools

import pytest

from autombist.alg_spec import DIR_MAP, OP_MAP, WAIT_BASE, Element, parse_alg
from autombist.fault_primitives import (
    Effect,
    FIXED_TYPE_NAMES,
    FaultPrimitive,
    Sensitize,
    default_registry,
)
from autombist.synth_engine import (
    _apply_op,
    detects,
    is_golden_sound,
    replay,
    resolve_params,
    synth_verification_faults,
    synthesize_alg,
    synthesize_elements,
)

UP, DOWN, EITHER = DIR_MAP["up"], DIR_MAP["down"], DIR_MAP["either"]
R0, R1, W0, W1 = OP_MAP["r0"], OP_MAP["r1"], OP_MAP["w0"], OP_MAP["w1"]

REGISTRY = {p.name: p for p in default_registry()}


def _spec(*elements: Element) -> list[Element]:
    return list(elements)


# --------------------------------------------------------------------------- #
# resolve_params
# --------------------------------------------------------------------------- #
def test_resolve_params_transition_primitives_get_either():
    assert resolve_params(REGISTRY["CFIN"])[0] == 2
    assert resolve_params(REGISTRY["CFID"])[0] == 2


def test_resolve_params_cfst_hold_is_one():
    assert resolve_params(REGISTRY["CFST"])[0] == 1


def test_resolve_params_non_parameterized_primitive_is_zero_zero():
    assert resolve_params(REGISTRY["SA0"]) == (0, 0)
    assert resolve_params(REGISTRY["TF0"]) == (0, 0)


# --------------------------------------------------------------------------- #
# Wait ops (Workstream K) -- a wait must be a genuine no-op in this oracle.
# DRF (the only fault a wait sensitizes) is a FIXED_TYPE_NAMES type and can
# never reach this synthesizer at all (default_registry() never contains
# fixed-type FaultPrimitive instances), so these only guard _apply_op/replay
# against a wait-containing spec being passed in directly.
# --------------------------------------------------------------------------- #
def test_apply_op_wait_is_a_true_noop():
    v, a, observed = _apply_op(1, 0, WAIT_BASE + 5, "v", None)
    assert (v, a, observed) == (1, 0, None)


def test_apply_op_wait_does_not_mutate_aggressor_role():
    v, a, observed = _apply_op(1, 0, WAIT_BASE + 5, "a", None)
    assert (v, a, observed) == (1, 0, None)


def test_replay_ignores_wait_ops():
    with_wait = _spec(Element(EITHER, [W0]), Element(UP, [WAIT_BASE + 20, R0]))
    without_wait = _spec(Element(EITHER, [W0]), Element(UP, [R0]))
    assert replay(with_wait, None) == replay(without_wait, None)


def test_detects_unaffected_by_an_inserted_wait_op():
    positive = _spec(Element(EITHER, [W0]), Element(UP, [WAIT_BASE + 20, W1, R1]))
    assert detects(positive, REGISTRY["TF0"])


# --------------------------------------------------------------------------- #
# detects() -- one positive + one negative control per primitive family.
# --------------------------------------------------------------------------- #
def test_detects_tf0_needs_write1_from_pre0():
    # The verify-read must assert what a SUCCESSFUL w1 produces in golden
    # (1), not an arbitrary literal -- r1, not r0 (an earlier version of
    # this test used r0 here, which is itself unsound: golden's w1 always
    # succeeds and produces 1, so an r0 assertion would already mismatch
    # golden with no fault active at all).
    positive = _spec(Element(EITHER, [W0]), Element(UP, [W1, R1]))
    assert detects(positive, REGISTRY["TF0"])
    negative = _spec(Element(EITHER, [W0]), Element(UP, [R0]))  # never writes 1
    assert not detects(negative, REGISTRY["TF0"])


def test_detects_wdf0_needs_nontransition_write0_from_pre0():
    positive = _spec(Element(EITHER, [W0]), Element(UP, [W0, R0]))
    assert detects(positive, REGISTRY["WDF0"])
    negative = _spec(Element(EITHER, [W0]), Element(UP, [W1, R1]))  # writes 1, not 0
    assert not detects(negative, REGISTRY["WDF0"])


def test_detects_rdf0_single_read_from_pre0():
    positive = _spec(Element(EITHER, [W0]), Element(UP, [R0]))
    assert detects(positive, REGISTRY["RDF0"])
    negative = _spec(Element(EITHER, [W1]), Element(UP, [R1]))  # v never at 0 when read
    assert not detects(negative, REGISTRY["RDF0"])


def test_detects_drdf0_needs_two_reads():
    # DRDF0's own read returns the CORRECT value; only the SECOND read (after
    # the fault's mutation) diverges -- one bare read must NOT be enough.
    one_read = _spec(Element(EITHER, [W0]), Element(UP, [R0]))
    assert not detects(one_read, REGISTRY["DRDF0"])
    two_reads = _spec(Element(EITHER, [W0]), Element(UP, [R0, R0]))
    assert detects(two_reads, REGISTRY["DRDF0"])


def test_detects_irf0_single_read_cell_unchanged():
    spec = _spec(Element(EITHER, [W0]), Element(UP, [R0]))
    assert detects(spec, REGISTRY["IRF0"])
    # IRF corrupts only the RETURNED value, not the cell: golden's read is
    # sound (asserted == observed == 0); the faulty read's observed value is
    # corrupted to 1 while its asserted literal (from the r0 op code) stays 0.
    golden = replay(spec, None)
    faulty = replay(spec, REGISTRY["IRF0"])
    assert golden == [(0, 0)]
    assert faulty == [(0, 1)]


def test_detects_cfin_aggressor_transition_inverts_victim():
    # 'a' starts at init_val; a genuine transition write on 'a' inverts 'v'.
    positive = _spec(Element(UP, [W0]), Element(UP, [R0]))
    assert detects(positive, REGISTRY["CFIN"], init_val=1)
    # No transition (a already at 1, writing 1 again) must not fire.
    negative = _spec(Element(EITHER, [W1]), Element(UP, [W1, R1]))
    assert not detects(negative, REGISTRY["CFIN"], init_val=1)


def test_detects_cfid_aggressor_transition_forces_victim():
    positive = _spec(Element(UP, [W0]), Element(UP, [R0]))
    assert detects(positive, REGISTRY["CFID"], init_val=1)


def test_detects_cfst_level_reapplies_after_every_op():
    # CFST: aggressor holds at its resolved p0 (1); as long as 'a' hasn't
    # been touched yet, the clamp reapplies right after v's own write (before
    # a's own portion of the SAME element later changes a away from its
    # hold value) -- confirming it "wins over any coupling effect." A
    # following read (a separate element, so it isn't itself clobbered by
    # that element's own w0) is what makes the divergence observable.
    spec = _spec(Element(EITHER, [W0]), Element(UP, [R0]))
    assert detects(spec, REGISTRY["CFST"], init_val=1)


def test_detects_sa0_sa1_basic():
    assert detects(_spec(Element(EITHER, [W1]), Element(UP, [R1])), REGISTRY["SA0"])
    assert detects(_spec(Element(EITHER, [W0]), Element(UP, [R0])), REGISTRY["SA1"])


# --------------------------------------------------------------------------- #
# synthesize_alg / synthesize_elements
# --------------------------------------------------------------------------- #
def test_synthesize_alg_covers_all_15_default_registry_primitives():
    result = synthesize_alg(default_registry(), "t")
    assert result.uncovered == []
    assert sorted(result.covered) == sorted(REGISTRY)
    assert result.targeted == [p.name for p in default_registry()]


def test_synthesize_result_actually_detects_every_covered_primitive():
    # Cross-check the synthesizer's own "covered" bookkeeping against a
    # fresh, independent detects() call on the final spec -- catches any
    # accounting drift between what the walk claimed and what's true.
    result = synthesize_alg(default_registry(), "t")
    for name in result.covered:
        assert detects(result.spec.elements, REGISTRY[name]), f"{name} claimed covered but detects()==False"


def test_synthesize_excludes_fixed_types_always():
    result = synthesize_alg(default_registry(), "t")
    assert result.excluded_fixed == list(FIXED_TYPE_NAMES)
    assert set(result.targeted).isdisjoint(FIXED_TYPE_NAMES)


def test_synthesize_excludes_raw_sv_primitives():
    reg = default_registry() + [
        FaultPrimitive("RAWX", "static_clamp", Sensitize(), Effect(kind="force", value="0"), raw_sv="/* hand-written */")
    ]
    result = synthesize_alg(reg, "t")
    assert "RAWX" not in result.targeted


def test_synthesize_respects_max_elements_cap():
    result = synthesize_alg(default_registry(), "t", max_elements=3)
    assert len(result.spec.elements) <= 3
    assert result.uncovered != []  # too tight to cover everything -- reported, not hidden


def test_synthesize_reports_partial_coverage_not_silently():
    result = synthesize_alg(default_registry(), "t", max_elements=3)
    # every uncovered name must be a real, named registry entry -- never an
    # empty/garbage placeholder standing in for "something went wrong"
    assert all(name in REGISTRY for name in result.uncovered)


def test_synthesize_handles_custom_write_effect_victim_type():
    custom = FaultPrimitive(
        "MYTF", "write_effect", Sensitize(pre="0", written="1"), Effect(kind="block_write", value="0"),
    )
    result = synthesize_alg([custom], "t")
    assert result.uncovered == []
    assert result.covered == ["MYTF"]


def test_synthesize_handles_custom_coupling_type():
    custom = FaultPrimitive(
        "MYCF", "write_effect", Sensitize(transition="p0", on="aggressor"), Effect(kind="invert"),
    )
    result = synthesize_alg([custom], "t")
    assert result.uncovered == []
    assert detects(result.spec.elements, custom)


def test_synthesize_elements_brackets_match_every_builtin_convention():
    elements, _ = synthesize_elements(default_registry())
    assert elements[0].direction == EITHER and elements[0].ops == [W0]
    # the final bracket's literal (r0 vs r1) depends on golden's live state
    # at that point -- an implementation detail, not a stable contract --
    # but it must always be a single, golden-sound read.
    assert elements[-1].direction == EITHER and elements[-1].ops in ([R0], [R1])
    # no working (non-bracket) element is 'either' -- the stated scope narrowing
    assert all(e.direction != EITHER for e in elements[1:-1])


def test_synthesize_elements_below_2_max_elements_raises():
    with pytest.raises(ValueError, match="max_elements must be >= 2"):
        synthesize_elements(default_registry(), max_elements=1)


@pytest.mark.parametrize("init_val", [0, 1])
def test_synthesize_every_default_primitive_covered_in_isolation(init_val: int) -> None:
    # Regression: synthesizing against a SINGLE primitive at a time (not the
    # full 15-set), at BOTH possible init_val settings, surfaced three real
    # bugs a full-registry-only, single-init_val test suite never exercised:
    # SA0/SA1 (a bare read of golden's already-0/1 value can never reveal a
    # clamp whose target coincides with what the init bracket already set),
    # CFID (a "force" coupling fault, where the write-element's own natural
    # write value could coincide with the forced target, masking it), CFST
    # (a static clamp gated on the AGGRESSOR's held value, which a
    # victim-only candidate can't reliably arrange -- it only "worked" by
    # accident of role-order timing at init_val=1, and failed outright at
    # init_val=0), and WDF0 specifically at init_val=0 (the mandatory init
    # bracket's own w0 write coincidentally satisfies WDF0's own pre=0/
    # written=0 condition, firing it a cycle early; the next candidate's own
    # write, checking that SAME condition against the now-already-diverged
    # value, silently fails to re-fire and erases the divergence before any
    # read observes it). Every primitive must be independently, soundly
    # detectable on its own, regardless of init_val, not just as part of the
    # full set where other primitives' candidates might incidentally carry
    # it along.
    for p in default_registry():
        result = synthesize_alg([p], f"{p.name.lower()}_only", init_val=init_val)
        assert result.uncovered == [], f"{p.name} not covered in isolation (init_val={init_val})"
        assert is_golden_sound(result.spec.elements, init_val=init_val), (
            f"{p.name}'s isolated spec is not golden-sound (init_val={init_val})"
        )
        assert detects(result.spec.elements, p, init_val=init_val), (
            f"{p.name} claimed covered but detects()==False (init_val={init_val})"
        )


@pytest.mark.parametrize("init_val", [0, 1])
def test_synthesize_alg_every_candidate_element_is_golden_sound(init_val: int) -> None:
    # The core correctness property the review's Finding 1 was about:
    # every element the walk ever accepts must be sound against a
    # fault-free replay -- checked here on the final spec (is_golden_sound
    # is also asserted internally by synthesize_elements itself).
    result = synthesize_alg(default_registry(), "t", init_val=init_val)
    assert is_golden_sound(result.spec.elements, init_val=init_val)


@pytest.mark.parametrize(
    "p1,p2,init_val",
    [(p1, p2, iv) for p1, p2 in itertools.combinations(default_registry(), 2) for iv in (0, 1)],
)
def test_synthesize_every_pair_of_default_primitives(p1: FaultPrimitive, p2: FaultPrimitive, init_val: int) -> None:
    # Exhaustive: all 105 two-primitive combinations x both init_val
    # settings (210 cases), catching any interaction the full-15-set and
    # isolated-primitive tests above could each individually miss (a
    # candidate that happens to serve two faults at once is exactly what
    # the greedy scorer optimizes for).
    result = synthesize_alg([p1, p2], "pair", init_val=init_val)
    assert result.uncovered == [], f"{p1.name}+{p2.name} (init_val={init_val}): {result.uncovered}"
    assert is_golden_sound(result.spec.elements, init_val=init_val)
    assert detects(result.spec.elements, p1, init_val=init_val) and detects(result.spec.elements, p2, init_val=init_val)


# --------------------------------------------------------------------------- #
# AlgSpec.to_text round-trip (the missing human-.alg serializer this module needed)
# --------------------------------------------------------------------------- #
def test_synthesized_spec_to_text_roundtrips_through_parse_alg():
    result = synthesize_alg(default_registry(), "roundtrip")
    reparsed = parse_alg(result.spec.to_text(), "roundtrip")
    assert reparsed.elements == result.spec.elements


def test_builtin_spec_to_text_roundtrips():
    from autombist.alg_spec import resolve_algo
    spec = resolve_algo("march_c")
    reparsed = parse_alg(spec.to_text(), "march_c")
    assert reparsed.elements == spec.elements


# --------------------------------------------------------------------------- #
# synth_verification_faults <-> resolve_params single-source-of-truth check
# --------------------------------------------------------------------------- #
def test_synth_verification_faults_matches_resolve_params():
    from autombist.algo_engine import MemoryParams

    mem = MemoryParams(addr_width=8, data_width=8)
    targets = [REGISTRY["CFIN"], REGISTRY["CFID"], REGISTRY["CFST"]]
    records = synth_verification_faults(mem, targets)
    for rec, prim in zip(records, targets):
        assert (rec.p0, rec.p1) == resolve_params(prim)
        assert rec.type == prim.name


def test_synth_verification_faults_one_per_target_no_type_collisions():
    from autombist.algo_engine import MemoryParams

    mem = MemoryParams(addr_width=8, data_width=8)
    records = synth_verification_faults(mem, default_registry())
    assert len(records) == 15
    assert {r.type for r in records} == set(REGISTRY)


def test_synthesize_elements_rejects_a_port_qualified_target():
    """This synthesizer is single-port: its 2-cell walk has no notion of which
    port issued an op, so a sensitize.port constraint has nothing to bind to and
    any coverage claimed for such a type would be unsound. Refused rather than
    silently treated as port-agnostic -- which is exactly what the codegen bug
    this accompanies used to do."""
    prim = FaultPrimitive(
        "PORTF", "write_effect",
        Sensitize(pre="0", written="1", on="victim", port="1"),
        Effect(kind="force", value="0"),
    )
    with pytest.raises(ValueError, match="single-port synthesizer cannot model"):
        synthesize_elements([prim])


def test_synthesize_elements_still_accepts_wildcard_port_targets():
    """The rejection must be specific to a port-qualified target: every built-in
    is port='x' and must keep synthesizing."""
    elements, _ = synthesize_elements([p for p in default_registry() if p.name == "TF0"])
    assert elements, "a wildcard-port target must still synthesize"
