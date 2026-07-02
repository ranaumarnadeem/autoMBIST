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
    assert len(names | set(FIXED_TYPE_NAMES)) == 19  # union = all 19 built-ins


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
