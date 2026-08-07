from __future__ import annotations

import pytest

from autombist.fault_primitives import (
    Effect,
    FaultPrimitive,
    FaultPrimitiveError,
    FIXED_TYPE_NAMES,
    Sensitize,
    default_registry,
    from_dict,
    to_dict,
    validate,
)


def test_default_registry_has_15_entries_no_fixed_overlap() -> None:
    reg = default_registry()
    names = {p.name for p in reg}
    assert len(names) == 15
    assert names.isdisjoint(FIXED_TYPE_NAMES)
    assert len(names | set(FIXED_TYPE_NAMES)) == 21  # union = all 21 built-ins


def test_default_registry_all_individually_valid() -> None:
    seen: set[str] = set()
    for prim in default_registry():
        validate(prim, existing_names=seen)  # must not raise
        seen.add(prim.name)


def test_validate_rejects_lowercase_name() -> None:
    prim = FaultPrimitive("myfault", "static_clamp", Sensitize(), Effect(kind="force", value="0"))
    with pytest.raises(FaultPrimitiveError, match="invalid fault type name"):
        validate(prim, existing_names=set())


def test_validate_rejects_fixed_type_name() -> None:
    prim = FaultPrimitive("SOF", "static_clamp", Sensitize(), Effect(kind="force", value="0"))
    with pytest.raises(FaultPrimitiveError, match="cannot be redefined"):
        validate(prim, existing_names=set())


def test_drf_cannot_be_registered_as_custom_type() -> None:
    # DRF (Workstream K) is a fixed built-in like SOF/AF_NOACC/AF_ALIAS/CFDS --
    # confirms the generic fixed-name rejection covers it too, not just the
    # four pre-existing fixed types.
    prim = FaultPrimitive("DRF", "static_clamp", Sensitize(), Effect(kind="force", value="0"))
    with pytest.raises(FaultPrimitiveError, match="cannot be redefined"):
        validate(prim, existing_names=set())


def test_hsd_cannot_be_registered_as_custom_type() -> None:
    # HSD (Workstream L) is also a fixed built-in -- same reasoning as DRF above.
    prim = FaultPrimitive("HSD", "static_clamp", Sensitize(), Effect(kind="force", value="0"))
    with pytest.raises(FaultPrimitiveError, match="cannot be redefined"):
        validate(prim, existing_names=set())


def test_validate_rejects_duplicate_name() -> None:
    prim = FaultPrimitive("MYCF", "static_clamp", Sensitize(), Effect(kind="force", value="0"))
    with pytest.raises(FaultPrimitiveError, match="already registered"):
        validate(prim, existing_names={"MYCF"})


def test_validate_rejects_addr_decoder_category() -> None:
    prim = FaultPrimitive("MYAF", "addr_decoder", Sensitize(), Effect(kind="force", value="0"))
    with pytest.raises(FaultPrimitiveError, match="category must be one of"):
        validate(prim, existing_names=set())


def test_validate_rejects_bad_effect_kind_for_static_clamp() -> None:
    prim = FaultPrimitive("MYSA", "static_clamp", Sensitize(), Effect(kind="invert"))
    with pytest.raises(FaultPrimitiveError, match="static_clamp primitives must use"):
        validate(prim, existing_names=set())


def test_validate_rejects_read_effect_on_aggressor() -> None:
    prim = FaultPrimitive(
        "MYRD", "read_effect", Sensitize(on="aggressor"), Effect(kind="corrupt_read", value="1")
    )
    with pytest.raises(FaultPrimitiveError, match="read_effect primitives must use sensitize.on='victim'"):
        validate(prim, existing_names=set())


def test_validate_allows_raw_sv_to_skip_dsl_field_checks() -> None:
    prim = FaultPrimitive(
        "MYWEIRD", "write_effect", raw_sv="begin nxt[b] = 1'bx; FQ[i].hits++; end",
    )
    validate(prim, existing_names=set())  # must not raise despite empty Sensitize/Effect defaults


def test_to_dict_from_dict_roundtrip() -> None:
    original = default_registry()[6]  # CFIN: has params_help, aggressor sensitize
    payload = to_dict(original)
    restored = from_dict(payload)
    assert restored == original


def test_from_dict_applies_defaults_for_missing_fields() -> None:
    prim = from_dict({"name": "MYNEW", "category": "static_clamp", "effect": {"kind": "force", "value": "1"}})
    assert prim.sensitize.on == "victim"
    assert prim.sensitize.pre == "x"
    assert prim.effect.target == "victim"


def _prim(**overrides: object) -> FaultPrimitive:
    defaults = dict(name="MYFAULT", category="static_clamp", sensitize=Sensitize(), effect=Effect(kind="force", value="0"))
    defaults.update(overrides)
    return FaultPrimitive(**defaults)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"sensitize": Sensitize(on="bogus")}, "sensitize.on must be one of"),
        ({"sensitize": Sensitize(pre="bogus")}, "sensitize.pre must be one of"),
        ({"sensitize": Sensitize(written="bogus")}, "sensitize.written must be one of"),
        ({"sensitize": Sensitize(transition="bogus")}, "sensitize.transition must be one of"),
        ({"effect": Effect(kind="bogus")}, "effect.kind must be one of"),
        ({"effect": Effect(kind="force", value="bogus")}, "effect.value must be one of"),
        ({"effect": Effect(kind="force", value="0", also_read="bogus")}, "effect.also_read must be one of"),
    ],
)
def test_validate_rejects_each_invalid_dsl_field(overrides: dict, match: str) -> None:
    with pytest.raises(FaultPrimitiveError, match=match):
        validate(_prim(**overrides), existing_names=set())


def test_validate_rejects_write_effect_victim_with_bad_kind() -> None:
    prim = _prim(category="write_effect", sensitize=Sensitize(on="victim"), effect=Effect(kind="invert"))
    with pytest.raises(FaultPrimitiveError, match="write_effect on=victim primitives must use force or block_write"):
        validate(prim, existing_names=set())


def test_validate_rejects_write_effect_aggressor_with_bad_kind() -> None:
    prim = _prim(category="write_effect", sensitize=Sensitize(on="aggressor"), effect=Effect(kind="block_write", value="0"))
    with pytest.raises(FaultPrimitiveError, match="write_effect on=aggressor primitives must use force or invert"):
        validate(prim, existing_names=set())


def test_validate_rejects_read_effect_with_bad_kind() -> None:
    prim = _prim(category="read_effect", sensitize=Sensitize(on="victim"), effect=Effect(kind="force", value="0"))
    with pytest.raises(FaultPrimitiveError, match="read_effect primitives must use corrupt_read or force_read"):
        validate(prim, existing_names=set())


# --------------------------------------------------------------------------- #
# Multi-port generalization (Phase: additive Sensitize.port field)
# --------------------------------------------------------------------------- #
def test_sensitize_port_defaults_to_x_wildcard() -> None:
    assert Sensitize().port == "x"


def test_default_registry_all_primitives_have_wildcard_port() -> None:
    # Every existing built-in must implicitly mean "port not part of the
    # sensitizing condition" -- i.e. zero behavior change from this phase.
    for prim in default_registry():
        assert prim.sensitize.port == "x"


@pytest.mark.parametrize("port", ["0", "1", "x"])
def test_validate_accepts_each_valid_port_token(port: str) -> None:
    # write_effect, not _prim's static_clamp default: a port-qualified static
    # clamp is now rejected outright (see below), so this must exercise a
    # category where the constraint is actually meaningful.
    prim = _prim(category="write_effect", sensitize=Sensitize(port=port))
    validate(prim, existing_names=set())  # must not raise


def test_validate_rejects_bad_port_token() -> None:
    prim = _prim(sensitize=Sensitize(port="2"))
    with pytest.raises(FaultPrimitiveError, match="sensitize.port must be one of"):
        validate(prim, existing_names=set())


@pytest.mark.parametrize("port", ["0", "1"])
def test_static_clamp_rejects_port_qualification(port: str) -> None:
    """A static clamp is a storage-level invariant, not an access event: its arm
    is emitted into clamp_static(), which rewrites mem[] and is re-asserted after
    every access on every port. Gating that on a port would still corrupt the
    stored cell for both ports -- the opposite of a port-specific defect -- so it
    is refused rather than silently mis-modelled."""
    prim = _prim(category="static_clamp", sensitize=Sensitize(port=port))
    with pytest.raises(FaultPrimitiveError, match="not meaningful for a static_clamp"):
        validate(prim, existing_names=set())


@pytest.mark.parametrize("port", ["0", "1"])
def test_raw_sv_rejects_port_qualification(port: str) -> None:
    """The port gate is emitted by wrapping the generated arm's condition, and a
    raw_sv body is copied verbatim -- so the constraint would be silently
    dropped. Note this is checked BEFORE raw_sv's early return, which otherwise
    skips every DSL field."""
    prim = _prim(
        category="write_effect",
        sensitize=Sensitize(port=port),
        raw_sv="if (1'b1) begin mem[FQ[i].va][FQ[i].vb] = 1'b0; FQ[i].hits++; end",
    )
    with pytest.raises(FaultPrimitiveError, match="cannot be combined with raw_sv"):
        validate(prim, existing_names=set())


def test_raw_sv_with_wildcard_port_still_accepted() -> None:
    """The rejection above must be specific to a port-qualified raw_sv -- an
    ordinary raw_sv primitive keeps working."""
    prim = _prim(
        category="write_effect",
        raw_sv="if (1'b1) begin mem[FQ[i].va][FQ[i].vb] = 1'b0; FQ[i].hits++; end",
    )
    validate(prim, existing_names=set())  # must not raise


def test_to_dict_includes_port_field() -> None:
    prim = _prim(sensitize=Sensitize(port="1"))
    payload = to_dict(prim)
    assert payload["sensitize"]["port"] == "1"


def test_from_dict_defaults_port_to_x_when_absent() -> None:
    prim = from_dict({"name": "MYNEW", "category": "static_clamp", "effect": {"kind": "force", "value": "1"}})
    assert prim.sensitize.port == "x"


def test_to_dict_from_dict_roundtrip_with_explicit_port() -> None:
    original = FaultPrimitive(
        "MYPORTF", "write_effect",
        Sensitize(pre="0", written="1", on="aggressor", port="1"),
        Effect(kind="invert"),
    )
    restored = from_dict(to_dict(original))
    assert restored == original


def test_default_registry_to_dict_roundtrip_unaffected_by_port_field() -> None:
    # Strongest backward-compat proof: every built-in must still round-trip
    # to an identical object after adding the port field.
    for prim in default_registry():
        assert from_dict(to_dict(prim)) == prim
