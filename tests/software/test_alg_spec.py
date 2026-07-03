from __future__ import annotations

from pathlib import Path

import pytest

from autombist.alg_spec import AlgSpecError, _find_pkg_subdir, builtin_algos, load_alg_file, parse_alg, resolve_algo


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
_GOLDEN_TO_NUMERIC = {
    "march_c": "# march_c  (10n)  DIR NOPS OP0..OP7\n2 1 2 0 0 0 0 0 0 0\n0 2 0 3 0 0 0 0 0 0\n0 2 1 2 0 0 0 0 0 0\n1 2 0 3 0 0 0 0 0 0\n1 2 1 2 0 0 0 0 0 0\n2 1 0 0 0 0 0 0 0 0\n",
    "march_ss": "# march_ss  (22n)  DIR NOPS OP0..OP7\n2 1 2 0 0 0 0 0 0 0\n0 5 0 0 2 0 3 0 0 0\n0 5 1 1 3 1 2 0 0 0\n1 5 0 0 2 0 3 0 0 0\n1 5 1 1 3 1 2 0 0 0\n2 1 0 0 0 0 0 0 0 0\n",
    "march_x": "# march_x  (6n)  DIR NOPS OP0..OP7\n2 1 2 0 0 0 0 0 0 0\n0 2 0 3 0 0 0 0 0 0\n1 2 1 2 0 0 0 0 0 0\n2 1 0 0 0 0 0 0 0 0\n",
    "mats_plus": "# mats_plus  (5n)  DIR NOPS OP0..OP7\n2 1 2 0 0 0 0 0 0 0\n0 2 0 3 0 0 0 0 0 0\n1 2 1 2 0 0 0 0 0 0\n",
}

# Golden length_n values, likewise pinned before any change (mirrors the
# reference lengths already asserted in test_builtins_resolve_and_match_reference_lengths).
_GOLDEN_LENGTH_N = {"march_c": 10, "march_ss": 22, "march_x": 6, "mats_plus": 5}


@pytest.mark.parametrize("algo_name", sorted(_GOLDEN_TO_NUMERIC))
def test_builtin_alg_to_numeric_byte_identical_to_pre_phase_golden(algo_name: str) -> None:
    spec = resolve_algo(algo_name)
    assert spec.to_numeric() == _GOLDEN_TO_NUMERIC[algo_name]


@pytest.mark.parametrize("algo_name", sorted(_GOLDEN_LENGTH_N))
def test_builtin_alg_length_n_byte_identical_to_pre_phase_golden(algo_name: str) -> None:
    spec = resolve_algo(algo_name)
    assert spec.length_n == _GOLDEN_LENGTH_N[algo_name]
