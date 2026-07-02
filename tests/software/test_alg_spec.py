from __future__ import annotations

import pytest

from autombist.alg_spec import AlgSpecError, builtin_algos, parse_alg, resolve_algo


def test_parse_human_elements() -> None:
    spec = parse_alg("either w0\nup r0 w1\ndown r1 w0\n", "t")
    assert [e.direction for e in spec.elements] == [2, 0, 1]
    assert spec.elements[1].ops == [0, 3]  # r0 w1
    assert spec.length_n == 5


def test_numeric_serialization() -> None:
    # DIR NOPS OP0..OP7  (padded to 8)
    spec = parse_alg("up r0 w1\n", "t")
    assert spec.elements[0].numeric_line() == "0 2 0 3 0 0 0 0 0 0"


def test_comments_and_blank_lines_ignored() -> None:
    spec = parse_alg("# header\n\neither w0   # init\n", "t")
    assert len(spec.elements) == 1 and spec.elements[0].ops == [2]


def test_builtins_resolve_and_match_reference_lengths() -> None:
    names = set(builtin_algos())
    assert {"march_c", "mats_plus", "march_ss", "march_x"} <= names
    assert resolve_algo("march_c").length_n == 10
    assert len(resolve_algo("march_c").elements) == 6
    assert resolve_algo("march_ss").length_n == 22
    assert resolve_algo("march-c").length_n == 10  # dash alias


def test_roundtrip_numeric_line_count() -> None:
    spec = resolve_algo("march_ss")
    body = [ln for ln in spec.to_numeric().splitlines() if ln and not ln.startswith("#")]
    assert len(body) == len(spec.elements)


def test_validation_errors() -> None:
    with pytest.raises(AlgSpecError):
        parse_alg("up r0 w9\n", "t")            # bad op
    with pytest.raises(AlgSpecError):
        parse_alg("sideways r0\n", "t")          # bad direction
    with pytest.raises(AlgSpecError):
        parse_alg("either\n", "t")               # element with no ops
    with pytest.raises(AlgSpecError):
        parse_alg("up " + " ".join(["r0"] * 9) + "\n", "t")  # >8 ops
    with pytest.raises(AlgSpecError):
        resolve_algo("no_such_algo")
