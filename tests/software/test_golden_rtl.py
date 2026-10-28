"""Golden-file tests for `generate`: for each reference config in
tests/golden/cases.yml, the RTL, testbench and scripts generate writes must match
tests/golden/<case>/ byte for byte.

The behavioural tests elsewhere check that the generated RTL works; these check
that it did not change by accident. A template edit that alters output nobody
meant to alter (a newline Jinja's trim_blocks ate, a port tie-off that moved)
fails here, and an intended one shows up as a reviewable diff of the golden files.

The rtl/ sources generate only copies are not stored twice: each case lists them
in rtl_copied.txt instead, so a change to which ones are copied still shows. Not
compared at all: the config.yml snapshot (it restates the input) and the
saboteur's fault lists (seeded random data, not RTL). The saboteur names its
fault lists by absolute path; the output directory is replaced by <OUT>.

After an intended change, regenerate and review the diff:
    AUTOMBIST_UPDATE_GOLDEN=1 python -m pytest tests/software/test_golden_rtl.py
"""
from __future__ import annotations

import difflib
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import _ALGO_DIRS, _find_rtl_dir, _normalize_algo, generate_from_config  # noqa: E402

GOLDEN = REPO_ROOT / "tests" / "golden"
CASES: dict[str, dict] = yaml.safe_load((GOLDEN / "cases.yml").read_text(encoding="utf-8"))
UPDATE = os.environ.get("AUTOMBIST_UPDATE_GOLDEN") == "1"
REGENERATE = "AUTOMBIST_UPDATE_GOLDEN=1 python -m pytest tests/software/test_golden_rtl.py"
COPIED_LIST = "rtl_copied.txt"
OUT_TOKEN = "<OUT>"
DIFF_LINES = 60


def _not_compared(rel: str) -> bool:
    return rel == "config.yml" or rel.startswith("faults/")


def _generate(case: str, tmp_path: Path) -> dict[str, str]:
    """What generate writes for ``case``, as {path relative to its output directory: text}."""
    spec = CASES[case]
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(spec["config"], sort_keys=False), encoding="utf-8")
    out = tmp_path / "out"
    module_dir = generate_from_config(config_path, out, **spec["generate"]).parent

    rtl = _find_rtl_dir()
    files: dict[str, str] = {}
    copied: list[str] = []
    for path in sorted(module_dir.rglob("*")):
        rel = path.relative_to(module_dir).as_posix()
        if not path.is_file() or _not_compared(rel):
            continue
        source = rtl / rel
        if source.is_file() and source.read_bytes() == path.read_bytes():
            copied.append(rel)
            continue
        text = path.read_text(encoding="utf-8")
        for prefix in {out.resolve().as_posix(), out.as_posix()}:
            text = text.replace(prefix, OUT_TOKEN)
        files[rel] = text
    files[COPIED_LIST] = "".join(f"{rel}\n" for rel in copied)
    return files


def _expected(case: str) -> dict[str, str]:
    root = GOLDEN / case
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write(case: str, files: dict[str, str]) -> None:
    root = GOLDEN / case
    if root.exists():
        shutil.rmtree(root)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def _mismatch_report(case: str, actual: dict[str, str], expected: dict[str, str]) -> str:
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    changed = sorted(rel for rel in actual.keys() & expected.keys() if actual[rel] != expected[rel])
    lines = [f"generate's output for golden case {case!r} differs from tests/golden/{case}/:"]
    lines += [f"  no longer written: {rel}" for rel in missing]
    lines += [f"  newly written:     {rel}" for rel in extra]
    lines += [f"  changed:           {rel}" for rel in changed]
    if changed:
        rel = changed[0]
        diff = list(difflib.unified_diff(
            expected[rel].splitlines(keepends=True), actual[rel].splitlines(keepends=True),
            fromfile=f"tests/golden/{case}/{rel}", tofile=f"generated/{rel}",
        ))
        shown = diff[:DIFF_LINES]
        lines.append("".join(shown).rstrip("\n"))
        if len(diff) > DIFF_LINES:
            lines.append(f"... {len(diff) - DIFF_LINES} more diff lines")
    lines.append(f"If the change is intended, regenerate and review the diff: {REGENERATE}")
    return "\n".join(lines)


@pytest.mark.parametrize("case", sorted(CASES))
def test_generate_matches_the_golden_files(case: str, tmp_path: Path) -> None:
    actual = _generate(case, tmp_path)
    if UPDATE:
        _write(case, actual)
    expected = _expected(case)
    assert actual == expected, _mismatch_report(case, actual, expected)


def test_every_golden_directory_has_a_case() -> None:
    """A directory left behind by a removed or renamed case is stale, not a test."""
    orphans = sorted(p.name for p in GOLDEN.iterdir() if p.is_dir() and p.name not in CASES)
    if UPDATE:
        for name in orphans:
            shutil.rmtree(GOLDEN / name)
        orphans = []
    assert not orphans, f"tests/golden/ has directories with no case in cases.yml: {orphans}"


def test_every_case_has_a_golden_directory() -> None:
    missing = sorted(case for case in CASES if not (GOLDEN / case).is_dir())
    assert not missing, f"no golden files yet for {missing}; generate them with {REGENERATE}"


def test_the_cases_cover_every_algorithm_generate_accepts() -> None:
    covered = {_normalize_algo(spec["generate"]["algo"])[0] for spec in CASES.values()}
    assert covered == _ALGO_DIRS, f"algorithms with no golden case: {sorted(_ALGO_DIRS - covered)}"


def test_a_case_names_only_generate_options() -> None:
    allowed = {"algo", "use_saboteur", "faults", "fault_seed", "fault_type"}
    for case, spec in CASES.items():
        assert set(spec) == {"generate", "config"}, case
        assert set(spec["generate"]) <= allowed, (case, set(spec["generate"]) - allowed)
