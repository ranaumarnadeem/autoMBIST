from __future__ import annotations

from pathlib import Path

import pytest

from autombist.alg_spec import (
    WAIT_BASE,
    AlgSpecError,
    _find_pkg_subdir,
    _expand_element,
    builtin_algos,
    load_alg_file,
    parse_alg,
    resolve_algo,
    resolve_directions,
)


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


def test_parse_alg_rejects_empty_spec() -> None:
    with pytest.raises(AlgSpecError, match="no march elements found"):
        parse_alg("# just a comment\n\n", "empty")


def test_parse_alg_rejects_too_many_elements() -> None:
    text = "\n".join(["either w0"] * 17)  # MAX_ELEMENTS is 16
    with pytest.raises(AlgSpecError, match="exceeds engine max"):
        parse_alg(text, "toolong")


def test_element_human_form() -> None:
    spec = parse_alg("up r0 w1\n", "t")
    assert spec.elements[0].human() == "up r0 w1"


# --------------------------------------------------------------------------- #
# Wait/idle op (Workstream K)
# --------------------------------------------------------------------------- #
def test_wait_token_parses_to_wait_op_code() -> None:
    spec = parse_alg("either t5\n", "t")
    assert spec.elements[0].ops == [WAIT_BASE + 5]
    assert spec.elements[0].ports == [0]


def test_wait_token_human_roundtrip() -> None:
    spec = parse_alg("either t5\n", "t")
    assert spec.elements[0].human() == "either t5"


def test_wait_token_case_insensitive() -> None:
    spec = parse_alg("either T5\n", "t")
    assert spec.elements[0].ops == [WAIT_BASE + 5]


def test_wait_token_zero_count_rejected() -> None:
    with pytest.raises(AlgSpecError, match="wait count"):
        parse_alg("either t0\n", "t")


def test_wait_token_exceeds_max_rejected() -> None:
    with pytest.raises(AlgSpecError, match="exceeds engine max"):
        parse_alg("either t65536\n", "t")


def test_wait_token_rejects_port_suffix() -> None:
    with pytest.raises(AlgSpecError, match="do not take a"):
        parse_alg("either t5.1\n", "t")


def test_wait_op_mixed_with_ordinary_ops_in_one_element() -> None:
    spec = parse_alg("either w0 t20 r0\n", "t")
    assert spec.elements[0].ops == [2, WAIT_BASE + 20, 0]
    assert spec.elements[0].human() == "either w0 t20 r0"


def test_wait_op_excluded_from_length_n() -> None:
    spec = parse_alg("either w0\neither t1000\neither r0\n", "t")
    assert spec.length_n == 2


def test_wait_op_still_occupies_a_max_ops_slot() -> None:
    text = "up " + " ".join(["r0"] * 7 + ["t1"])  # exactly MAX_OPS=8
    spec = parse_alg(text + "\n", "t")
    assert len(spec.elements[0].ops) == 8
    with pytest.raises(AlgSpecError, match="exceeds engine max"):
        parse_alg(text + " r0\n", "t")  # 9th op, one over MAX_OPS


def test_numeric_line_for_wait_op() -> None:
    spec = parse_alg("either t5\n", "t")
    assert spec.elements[0].numeric_line() == "2 1 9 0 0 0 0 0 0 0"  # WAIT_BASE+5=9


def test_wait_spec_to_text_roundtrips_through_parse_alg() -> None:
    spec = parse_alg("either w0\nup t20 r0\ndown r1 t5\n", "t")
    reparsed = parse_alg(spec.to_text(), "t")
    assert reparsed.elements == spec.elements


# --------------------------------------------------------------------------- #
# Wait ops and the FSM-comparison front (_expand_element)
# --------------------------------------------------------------------------- #
def test_expand_element_skips_wait_ops() -> None:
    spec = parse_alg("up w0 t20 r0\n", "t")
    steps = _expand_element(spec.elements[0], elem_idx=0, depth=1, direction=0)
    # Only the w0 and r0 ops produce steps -- the wait has zero bus activity.
    assert [s.op for s in steps] == [2, 0]


def test_expand_element_wait_only_element_produces_no_steps() -> None:
    spec = parse_alg("either t20\n", "t")
    steps = _expand_element(spec.elements[0], elem_idx=0, depth=4, direction=0)
    assert steps == []


# --------------------------------------------------------------------------- #
# resolve_directions -- the ONE canonical rule for what `either` resolves to.
# `either` inherits the previous element's direction, defaulting to up. This
# is the rule that makes generated RTL (and, after resolution, the numeric
# form the SystemVerilog engines read) match the hand-written classic tables;
# see the function's docstring for the measured consequence of getting it
# wrong. Moved here from tests/software/test_algo_rtl_gen.py -- the rule now
# lives in alg_spec.py, not algo_rtl_gen.py, so its tests do too.
# --------------------------------------------------------------------------- #
def _dirs(text: str) -> list[int]:
    return resolve_directions(parse_alg(text, "t").elements)


def test_explicit_directions_pass_straight_through() -> None:
    assert _dirs("up w0\ndown r0\nup r1\n") == [0, 1, 0]


def test_leading_either_defaults_to_up() -> None:
    assert _dirs("either w0\ndown r0\n") == [0, 1]


def test_trailing_either_inherits_the_previous_direction() -> None:
    """The case where the engine and the hand-written classic RTL used to
    disagree before this rule was unified: a trailing `either` inherits
    `down`, it is not forced back to `up`."""
    assert _dirs("up w0\ndown r0\neither r1\n") == [0, 1, 1]


def test_consecutive_either_all_inherit_the_same_direction() -> None:
    assert _dirs("down w0\neither r0\neither r1\neither w1\n") == [1, 1, 1, 1]


def test_all_either_spec_is_entirely_up() -> None:
    assert _dirs("either w0\neither r0\neither w1\n") == [0, 0, 0]


def test_either_between_two_explicit_directions_takes_the_earlier_one() -> None:
    assert _dirs("down w0\neither r0\nup r1\n") == [1, 1, 0]


@pytest.mark.parametrize(
    "algo_name,expected",
    [
        # Exactly the direction sequences the hand-written classic-path RTL
        # tables encode -- proven behaviourally identical to a rendered table
        # by tests/integration/test_algo_table_equivalence.py's exhaustive
        # simulation sweep.
        ("march_c", [0, 0, 0, 1, 1, 1]),
        ("march_x", [0, 0, 1, 1]),
        ("mats_plus", [0, 0, 1]),
    ],
)
def test_builtin_directions_match_the_hand_written_rtl(algo_name: str, expected: list[int]) -> None:
    assert resolve_directions(resolve_algo(algo_name).elements) == expected


def test_load_alg_file_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(AlgSpecError, match="algorithm spec not found"):
        load_alg_file(tmp_path / "does_not_exist.alg")


def test_resolve_algo_accepts_a_direct_file_path(tmp_path: Path) -> None:
    custom = tmp_path / "custom.alg"
    custom.write_text("either w0\nup r0 w1\n", encoding="utf-8")
    spec = resolve_algo(str(custom))
    assert spec.length_n == 3


def test_find_pkg_subdir_falls_back_to_dev_layout_then_raises() -> None:
    # A name that exists nowhere (neither installed package data nor a local
    # src/autombist/<name> dir) exercises the final not-found raise.
    with pytest.raises(AlgSpecError, match="directory not found"):
        _find_pkg_subdir("no_such_asset_dir_xyz", "no_such_marker.txt")


def test_find_pkg_subdir_handles_importlib_resources_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import autombist.alg_spec as alg_spec_mod

    def boom(_pkg: str) -> None:
        raise ModuleNotFoundError("simulated")

    monkeypatch.setattr(alg_spec_mod.importlib.resources, "files", boom)
    with pytest.raises(AlgSpecError, match="directory not found"):
        _find_pkg_subdir("no_such_asset_dir_xyz", "no_such_marker.txt")


# --------------------------------------------------------------------------- #
# Multi-port generalization (Phase: additive port-tagged op tokens)
# --------------------------------------------------------------------------- #
def test_port_tagged_element_parses_ops_and_ports() -> None:
    spec = parse_alg("up r0.1 w1.0\n", "t")
    elem = spec.elements[0]
    assert elem.ops == [0, 3]      # base op values unaffected by port suffix
    assert elem.ports == [1, 0]


def test_untagged_ops_default_all_ports_to_zero() -> None:
    spec = parse_alg("up r0 w1\n", "t")
    assert spec.elements[0].ports == [0, 0]


def test_port_tagged_element_human_roundtrip() -> None:
    spec = parse_alg("up r0.1 w1.0\n", "t")
    # port 0 is never printed (implicit/default); only the non-zero tag is.
    assert spec.elements[0].human() == "up r0.1 w1"


def test_bad_port_suffix_raises() -> None:
    with pytest.raises(AlgSpecError, match="bad port suffix"):
        parse_alg("up r0.x\n", "t")


def test_out_of_range_port_suffix_raises() -> None:
    # Only ports 0 and 1 exist (2-port scope) -- a numeric but out-of-range
    # suffix like .99 must be rejected, not silently accepted.
    with pytest.raises(AlgSpecError, match="bad port suffix"):
        parse_alg("up r0.99\n", "t")
    with pytest.raises(AlgSpecError, match="bad port suffix"):
        parse_alg("up r0.2\n", "t")


def test_bad_op_with_port_suffix_still_raises_bad_op() -> None:
    with pytest.raises(AlgSpecError, match="bad op"):
        parse_alg("up r9.1\n", "t")


def test_numeric_line_extended_when_port_nonzero() -> None:
    spec = parse_alg("up r0.1 w1\n", "t")
    # DIR NOPS OP0..OP7 PORT0..PORT7 (extended form only when a port != 0 present)
    assert spec.elements[0].numeric_line() == "0 2 0 3 0 0 0 0 0 0 1 0 0 0 0 0 0 0"


def test_numeric_line_unaffected_when_all_ports_zero() -> None:
    # Byte-identical to the pre-multi-port format.
    spec = parse_alg("up r0 w1\n", "t")
    assert spec.elements[0].numeric_line() == "0 2 0 3 0 0 0 0 0 0"


def test_to_numeric_extended_header_when_port_present() -> None:
    spec = parse_alg("up r0.1 w1\n", "test_ext")
    text = spec.to_numeric()
    assert text.startswith("# test_ext  (2n)  DIR NOPS OP0..OP7  PORT0..PORT7\n")


# Golden strings captured from to_numeric() BEFORE any multi-port change was
# made to alg_spec.py (see the task's verification requirement) -- the
# strongest proof that every existing built-in .alg file's numeric
# serialization is byte-identical to its pre-phase value.
#
# They ALSO predate AlgSpec.resolved(): back then to_numeric() emitted each
# element's direction verbatim, so an `either` element's DIR column is a
# literal "2" in every one of these strings. Once to_numeric() started
# resolving directions first, that stopped being true -- but overwriting the
# strings would destroy the evidence they were captured to preserve (that the
# multi-port change touched nothing else). So they stay exactly as captured,
# and the two tests below use them DIFFERENTIALLY instead of asserting
# byte-identity: every column must still match except the DIR of a resolved
# `either`, and that DIR must match resolve_directions() exactly -- not just
# "some value 0 or 1". See _assert_numeric_matches_pre_resolution_golden.
_GOLDEN_TO_NUMERIC = {
    "march_c": "# march_c  (10n)  DIR NOPS OP0..OP7\n2 1 2 0 0 0 0 0 0 0\n0 2 0 3 0 0 0 0 0 0\n0 2 1 2 0 0 0 0 0 0\n1 2 0 3 0 0 0 0 0 0\n1 2 1 2 0 0 0 0 0 0\n2 1 0 0 0 0 0 0 0 0\n",
    "march_ss": "# march_ss  (22n)  DIR NOPS OP0..OP7\n2 1 2 0 0 0 0 0 0 0\n0 5 0 0 2 0 3 0 0 0\n0 5 1 1 3 1 2 0 0 0\n1 5 0 0 2 0 3 0 0 0\n1 5 1 1 3 1 2 0 0 0\n2 1 0 0 0 0 0 0 0 0\n",
    "march_x": "# march_x  (6n)  DIR NOPS OP0..OP7\n2 1 2 0 0 0 0 0 0 0\n0 2 0 3 0 0 0 0 0 0\n1 2 1 2 0 0 0 0 0 0\n2 1 0 0 0 0 0 0 0 0\n",
    "mats_plus": "# mats_plus  (5n)  DIR NOPS OP0..OP7\n2 1 2 0 0 0 0 0 0 0\n0 2 0 3 0 0 0 0 0 0\n1 2 1 2 0 0 0 0 0 0\n",
}

# Golden length_n values, likewise pinned before any change (mirrors the
# reference lengths already asserted in test_builtins_resolve_and_match_reference_lengths).
# Unaffected by direction resolution -- length_n never reads .direction -- so
# these stay a byte-identity assertion, not a differential one.
_GOLDEN_LENGTH_N = {"march_c": 10, "march_ss": 22, "march_x": 6, "mats_plus": 5}


def _numeric_header_and_body_rows(text: str) -> tuple[str, list[list[str]]]:
    """Split a to_numeric()-shaped string into its leading `# ...` header line
    and its per-element body rows' whitespace-separated columns."""
    lines = text.splitlines()
    assert lines and lines[0].startswith("#"), "expected a leading header comment line"
    return lines[0], [line.split() for line in lines[1:] if line]


def _assert_numeric_matches_pre_resolution_golden(golden: str, actual: str, elements) -> None:
    """Prove `actual` (today's to_numeric(), which resolves `either` first)
    differs from `golden` (captured before resolution existed) in NOTHING but
    the DIR column of rows the golden encoded as `either` (DIR=2) -- and that
    the resolved value in each such row is exactly what resolve_directions()
    computes, not merely "some value 0 or 1". Every other column, on every
    row, must be byte-identical to the golden -- preserving the original
    byte-identity evidence for everything resolution did not touch.

    The header line (algo name, `(Nn)` length, column-label text) is asserted
    byte-identical too: it's untouched by direction resolution (unlike the
    body rows), and none of these golden algos use non-default ports, so
    there's no legitimate source of header drift to differentiate around --
    a header-only regression should fail here, not slip through silently."""
    golden_header, golden_rows = _numeric_header_and_body_rows(golden)
    actual_header, actual_rows = _numeric_header_and_body_rows(actual)
    assert actual_header == golden_header, (golden_header, actual_header)
    assert len(golden_rows) == len(actual_rows) == len(elements), "element count changed"
    expected_dirs = resolve_directions(elements)
    resolved_any = False
    for golden_row, actual_row, expected_dir in zip(golden_rows, actual_rows, expected_dirs):
        assert actual_row[1:] == golden_row[1:], (golden_row, actual_row)  # NOPS/OP.../PORT... untouched
        if golden_row[0] == "2":
            resolved_any = True
            assert actual_row[0] == str(expected_dir), (golden_row, actual_row, expected_dir)
        else:
            assert actual_row[0] == golden_row[0], (golden_row, actual_row)  # fixed direction untouched
    assert resolved_any, "golden has no `either` element -- differential test is pointless here"


@pytest.mark.parametrize("algo_name", sorted(_GOLDEN_TO_NUMERIC))
def test_builtin_alg_to_numeric_matches_pre_resolution_golden_except_either_dir(algo_name: str) -> None:
    spec = resolve_algo(algo_name)
    _assert_numeric_matches_pre_resolution_golden(
        _GOLDEN_TO_NUMERIC[algo_name], spec.to_numeric(), spec.elements
    )


@pytest.mark.parametrize("algo_name", sorted(_GOLDEN_LENGTH_N))
def test_builtin_alg_length_n_byte_identical_to_pre_phase_golden(algo_name: str) -> None:
    spec = resolve_algo(algo_name)
    assert spec.length_n == _GOLDEN_LENGTH_N[algo_name]


# March B is pinned SEPARATELY from the two _GOLDEN_* maps above rather than
# added to them: those exist specifically to prove the four PRE-EXISTING built-in
# .alg files serialize byte-identically to their pre-multi-port values, and
# folding a newer file into that set would blur what they attest to. Captured
# before either-direction resolution existed, same as _GOLDEN_TO_NUMERIC, so it
# is compared the same differential way rather than overwritten -- see
# _assert_numeric_matches_pre_resolution_golden.
_MARCH_B_NUMERIC = (
    "# march_b  (17n)  DIR NOPS OP0..OP7\n"
    "2 1 2 0 0 0 0 0 0 0\n"
    "0 6 0 3 1 2 0 3 0 0\n"
    "0 3 1 2 3 0 0 0 0 0\n"
    "1 4 1 2 3 2 0 0 0 0\n"
    "1 3 0 3 2 0 0 0 0 0\n"
)


def test_march_b_is_exactly_17n() -> None:
    """The literature figure for Suk & Reddy's March B, and the one number an
    earlier planning note got wrong: it recorded the same quoted sequence as
    summing to 18n and deferred the algorithm over the discrepancy. The sequence
    is 1 + 6 + 3 + 4 + 3 = 17."""
    assert resolve_algo("march_b").length_n == 17


def test_march_b_element_shape() -> None:
    """Pins the actual op sequence, not just its length -- a different sequence
    that happened to total 17 ops would otherwise slip through."""
    assert [e.human() for e in resolve_algo("march_b").elements] == [
        "either w0",
        "up r0 w1 r1 w0 r0 w1",
        "up r1 w0 w1",
        "down r1 w0 w1 w0",
        "down r0 w1 w0",
    ]


def test_march_b_numeric_serialization_matches_pre_resolution_golden() -> None:
    spec = resolve_algo("march_b")
    _assert_numeric_matches_pre_resolution_golden(_MARCH_B_NUMERIC, spec.to_numeric(), spec.elements)
