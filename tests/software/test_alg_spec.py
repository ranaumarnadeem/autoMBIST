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
