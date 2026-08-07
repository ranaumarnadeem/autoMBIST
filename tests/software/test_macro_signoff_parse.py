"""Pin the two output parsers in flow/multimem/signoff/run_macro_signoff.sh.

That script had ZERO execution coverage -- every test that touches the signoff
path stubs it out with an `echo hi` placeholder -- which is how it carried two
severe bugs at once:

  1. The DRC count was extracted with `grep -A2 DRC_COUNT_BEGIN | grep -iE
     "Total|error tiles|count" | head -1`. `grep -A2` prints the MATCHING line
     first, and "DRC_COUNT_BEGIN" itself matches a case-insensitive /count/ --
     so head -1 always selected the sentinel, which has no digits. The count was
     therefore ALWAYS empty, `${drc_n:-1}` always 1, and the script could never
     report success for any macro.
  2. The LVS verdict was printed but never gated the exit code, and its grep
     matched "Circuits match" and "do not match" alike -- so a genuine
     layout-vs-schematic MISMATCH printed to the console and still exited 0.

Both parsers are now shell functions, and this file sources the script (its
`BASH_SOURCE` guard stops it before the flow runs) to exercise them directly.
No magic/netgen needed -- only the log-parsing logic, which is what broke.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs bash to source the signoff script"
)

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "flow" / "multimem" / "signoff" / "run_macro_signoff.sh"
)


def _call(func: str, *args: str) -> tuple[int, str]:
    """Source the script (guard stops it before the flow) and call one helper."""
    proc = subprocess.run(
        ["bash", "-c", 'source "$1" || exit 99; shift; "$@"', "_", str(SCRIPT), func, *args],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert proc.returncode != 99, f"sourcing the script failed:\n{proc.stderr}"
    return proc.returncode, proc.stdout.strip()


def _log(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


# Magic emits the sentinel, then (possibly) an echo of the command, then the
# real total. The echoed-command line is the one that made the old -A2 window
# fragile even aside from the sentinel match.
_CLEAN = "DRC_COUNT_BEGIN\ndrc count total\nTotal DRC errors found: 0\nDRC_COUNT_END\n"
_DIRTY = "DRC_COUNT_BEGIN\ndrc count total\nTotal DRC errors found: 47\nDRC_COUNT_END\n"


# --------------------------------------------------------------------------- #
# parse_drc_count
# --------------------------------------------------------------------------- #
def test_drc_count_reads_zero_from_a_clean_log(tmp_path: Path) -> None:
    """The regression that matters: this is the case the old pipeline got wrong
    in the direction that made every signoff run fail."""
    assert _call("parse_drc_count", _log(tmp_path, "drc.log", _CLEAN)) == (0, "0")


def test_drc_count_reads_a_nonzero_total(tmp_path: Path) -> None:
    assert _call("parse_drc_count", _log(tmp_path, "drc.log", _DIRTY)) == (0, "47")


def test_drc_count_never_returns_the_sentinel(tmp_path: Path) -> None:
    """Directly pins the specific defect: DRC_COUNT_BEGIN contains "COUNT", so
    a case-insensitive /count/ filter matches the sentinel itself."""
    for body in (_CLEAN, _DIRTY):
        _, out = _call("parse_drc_count", _log(tmp_path, "drc.log", body))
        assert "DRC_COUNT" not in out, out
        assert out.isdigit(), out


def test_drc_count_fails_when_magic_produced_no_count(tmp_path: Path) -> None:
    """"magic crashed" must stay distinguishable from "zero violations" -- the
    conflation is the whole point of returning non-zero here."""
    crashed = _log(tmp_path, "drc.log", "magic: fatal error, could not open GDS\n")
    assert _call("parse_drc_count", crashed) == (1, "")


def test_drc_count_fails_on_a_missing_log(tmp_path: Path) -> None:
    assert _call("parse_drc_count", str(tmp_path / "nope.log")) == (1, "")


def test_drc_count_ignores_digits_outside_the_sentinel_region(tmp_path: Path) -> None:
    """The count must come from between the sentinels, not from unrelated
    chatter -- magic prints plenty of numbers during extraction."""
    body = (
        "loaded 1234 cells\n"
        "DRC_COUNT_BEGIN\n"
        "Total DRC errors found: 0\n"
        "DRC_COUNT_END\n"
        "extract: wrote 5678 nets\n"
    )
    assert _call("parse_drc_count", _log(tmp_path, "drc.log", body)) == (0, "0")


# --------------------------------------------------------------------------- #
# classify_lvs
# --------------------------------------------------------------------------- #
def test_lvs_match(tmp_path: Path) -> None:
    out = _log(tmp_path, "lvs.out", "Circuits match uniquely.\n")
    assert _call("classify_lvs", out) == (0, "MATCH")


def test_lvs_mismatch_is_not_read_as_a_match(tmp_path: Path) -> None:
    """The substring trap: "Circuits do not match." CONTAINS "match", so any
    positive test running first swallows a real mismatch. This is the exact
    string that used to print to the console while the script exited 0."""
    out = _log(tmp_path, "lvs.out", "Circuits do not match.\n")
    assert _call("classify_lvs", out) == (0, "MISMATCH")


def test_lvs_mismatch_wins_even_when_a_match_line_also_appears(tmp_path: Path) -> None:
    """netgen reports per-subcircuit results, so a run can contain both -- any
    mismatch anywhere must lose."""
    out = _log(
        tmp_path, "lvs.out",
        "Subcircuit summary:\nCircuits match uniquely.\nCircuits do not match.\n",
    )
    assert _call("classify_lvs", out) == (0, "MISMATCH")


def test_lvs_unknown_on_empty_or_missing_output(tmp_path: Path) -> None:
    """netgen crashing before writing lvs.out must not read as a pass."""
    assert _call("classify_lvs", _log(tmp_path, "lvs.out", "")) == (0, "UNKNOWN")
    assert _call("classify_lvs", str(tmp_path / "nope.out")) == (0, "UNKNOWN")


def test_lvs_unknown_on_unrecognized_output(tmp_path: Path) -> None:
    out = _log(tmp_path, "lvs.out", "netgen: some future message we don't parse\n")
    assert _call("classify_lvs", out) == (0, "UNKNOWN")
