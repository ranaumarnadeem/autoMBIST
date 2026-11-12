"""tests/golden_tools.py: a tool-dependent golden deviation fails on a checked
version and under AUTOMBIST_GOLDEN_STRICT=1, and is a warning otherwise."""
from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

import golden_tools
import pytest
import yaml

PROBLEMS = ["march-c: cells: 174, recorded 185 (-5.9%, tolerance 5%)"]


@pytest.fixture
def versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tool_versions.yml"
    path.write_text("yosys:\n- 0.61+129\n- '0.62'\nverilator:\n- '5.050'\n", encoding="utf-8")
    monkeypatch.setattr(golden_tools, "TOOL_VERSIONS", path)
    monkeypatch.delenv("AUTOMBIST_GOLDEN_STRICT", raising=False)
    return path


def _running(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    monkeypatch.setattr(golden_tools, "tool_version", lambda tool: version)


@pytest.mark.parametrize("version", ["0.61+129", "0.62"])
def test_a_checked_version_fails(versions: Path, monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    _running(monkeypatch, version)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert golden_tools.failing("yosys", PROBLEMS, "march-c: area") == PROBLEMS


def test_another_version_warns_and_passes(versions: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _running(monkeypatch, "0.70")
    with pytest.warns(UserWarning, match=r"yosys 0\.70, .*checked with 0\.61\+129, 0\.62") as caught:
        assert golden_tools.failing("yosys", PROBLEMS, "march-c: area") == []
    assert PROBLEMS[0] in str(caught[0].message)


def test_strict_fails_on_any_version(versions: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _running(monkeypatch, "0.70")
    monkeypatch.setenv("AUTOMBIST_GOLDEN_STRICT", "1")
    assert golden_tools.failing("yosys", PROBLEMS, "march-c: area") == PROBLEMS


def test_no_problems_is_never_a_warning(versions: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _running(monkeypatch, "0.70")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert golden_tools.failing("yosys", [], "march-c: area") == []


def test_a_tool_with_no_list_warns(versions: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _running(monkeypatch, "13.0")
    with pytest.warns(UserWarning, match="checked with none"):
        assert golden_tools.failing("iverilog", PROBLEMS, "cycles") == []


def test_a_quoted_version_keeps_its_trailing_zero(versions: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _running(monkeypatch, "5.050")
    assert golden_tools.failing("verilator", PROBLEMS, "RTL coverage") == PROBLEMS


def test_an_update_records_only_the_version_it_ran_on(versions: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _running(monkeypatch, "0.70")
    golden_tools.record_tool_version("yosys")
    assert yaml.safe_load(versions.read_text(encoding="utf-8")) == {"yosys": ["0.70"], "verilator": ["5.050"]}
    assert versions.read_text(encoding="utf-8").startswith("# The tool versions")


@pytest.mark.parametrize(("tool", "stdout", "version"), [
    ("yosys", "Yosys 0.62 (git sha1 7326bb7d6, clang++ 18.1.8 -fPIC -O3)\n", "0.62"),
    ("verilator", "Verilator 5.048 2026-01-01 rev v5.048\n", "5.048"),
    ("yosys", "", "unknown"),
])
def test_the_version_is_the_second_word(monkeypatch: pytest.MonkeyPatch, tool: str, stdout: str,
                                        version: str) -> None:
    def run(args, **kwargs):
        assert args == [tool, {"yosys": "-V", "verilator": "--version"}[tool]]
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(golden_tools.subprocess, "run", run)
    assert golden_tools.tool_version(tool) == version


def test_the_repo_lists_the_versions_the_records_were_checked_with() -> None:
    """The file's shape, not its contents: an AUTOMBIST_UPDATE_GOLDEN run
    rewrites it to the version it ran on, which pinned contents then failed
    (#63)."""
    checked = golden_tools._checked()
    assert set(checked) == {"yosys", "verilator"}
    for tool, versions in checked.items():
        assert versions and all(isinstance(v, str) and v for v in versions), (tool, versions)
        assert len(set(versions)) == len(versions), (tool, versions)
