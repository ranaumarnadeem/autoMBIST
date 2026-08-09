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


# --------------------------------------------------------------------------- #
# PDK_ROOT export -- a THIRD execution-coverage gap discovered the same way as
# the two documented above: magic's own sky130A.magicrc bootstrap reads
# $env(PDK_ROOT) directly (a Tcl `$env(...)` lookup only sees real
# environment variables), and if unset falls back to a hardcoded volare-style
# path that does not exist outside ciel-based installs. The script set
# PDK_ROOT as a plain shell variable without `export`, so magic never saw it,
# silently loaded "technology minimum" instead of the real sky130A tech, and
# every DRC/LVS check ran against zero real PDK data while still producing
# plausible-looking output (DRC trivially 0, LVS SKIP or a spurious
# mismatch) -- confirmed by running the real flow against real cached
# OpenRAM macros with magic/netgen from the LibreLane nix closure.
#
# This can't be tested by sourcing (the same technique the parser tests
# above use): the source guard returns before reaching the export line. Runs
# the script for real instead, with fake magic/netgen stand-ins on PATH that
# report what environment they actually saw -- no real EDA tools needed.
# --------------------------------------------------------------------------- #
_FAKE_MAGIC = """#!/usr/bin/env bash
cat > /dev/null  # consume the heredoc so the real script's pipe doesn't break
echo "PDK_ROOT_SEEN=${PDK_ROOT:-<unset>}"
echo "DRC_COUNT_BEGIN"
echo "Total DRC errors found: 0"
echo "DRC_COUNT_END"
"""

_FAKE_NETGEN = """#!/usr/bin/env bash
exit 0
"""


def test_pdk_root_is_exported_before_magic_runs(tmp_path: Path) -> None:
    """PDK_ROOT must be genuinely absent from the subprocess's own env=, not
    merely pointed somewhere -- if the test itself pre-sets PDK_ROOT in
    env=, bash imports it as ALREADY exported (any variable present in a
    process's initial environment is exported by definition), so the
    script's own bare `PDK_ROOT=...` reassignment (without `export`) would
    stay exported regardless of whether the fix is present, and this test
    would pass either way. The real bug only reproduces when the script's
    own `${PDK_ROOT:-$HOME/.ciel}` default creates PDK_ROOT as a brand-new
    shell variable -- so PDK_ROOT is deliberately left out of env here, and
    the fake PDK tree is placed at $HOME/.ciel (the script's real default)
    instead."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    magic_path = bin_dir / "magic"
    magic_path.write_text(_FAKE_MAGIC, encoding="utf-8")
    magic_path.chmod(0o755)
    netgen_path = bin_dir / "netgen"
    netgen_path.write_text(_FAKE_NETGEN, encoding="utf-8")
    netgen_path.chmod(0o755)

    pdk_root = tmp_path / ".ciel"
    (pdk_root / "sky130A" / "libs.tech" / "magic").mkdir(parents=True)
    (pdk_root / "sky130A" / "libs.tech" / "magic" / "sky130A.magicrc").write_text(
        "", encoding="utf-8"
    )

    macro_out = tmp_path / "macro_out" / "some_macro"
    macro_out.mkdir(parents=True)
    (macro_out / "some_macro.gds").write_text("", encoding="utf-8")

    outdir = tmp_path / "signoff_out"

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "MACRO_OUT": str(tmp_path / "macro_out"),
        "OUTDIR": str(outdir),
        "HOME": str(tmp_path),  # PDK_ROOT's default is $HOME/.ciel -- no PDK_ROOT key here
    }
    subprocess.run(
        ["bash", str(SCRIPT), "some_macro"],
        capture_output=True, text=True, check=False, timeout=60, env=env,
    )

    drc_log = (outdir / "some_macro" / "drc.log").read_text(encoding="utf-8")
    assert f"PDK_ROOT_SEEN={pdk_root}" in drc_log, drc_log
