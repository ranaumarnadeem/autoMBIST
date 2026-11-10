"""The tool versions the tool-dependent golden records were checked with, and
what a comparison does on any other version.

Some golden figures belong to a tool, not to the design: Yosys's cell count in
tests/golden/metrics.yml, and how Verilator classifies the coverage points in
tests/golden/rtl_coverage.yml. On a version tests/golden/tool_versions.yml lists,
a deviation fails. On another one (a Homebrew or distro install, say) it is a
warning, since it may be the tool's drift and not a change in the RTL; the
figures that do not depend on the tool (flops, latches, clock cycles, verdicts)
still fail. AUTOMBIST_GOLDEN_STRICT=1, which CI sets, makes it fail on any
version, so a pin bump that moves a figure cannot pass unnoticed.

An update run (AUTOMBIST_UPDATE_GOLDEN=1) records the version it ran on as the
only one. To add another, run the tests on it with AUTOMBIST_GOLDEN_STRICT=1 and,
if they pass, add it to the list by hand.
"""
from __future__ import annotations

import os
import subprocess
import warnings
from pathlib import Path

import yaml

TOOL_VERSIONS = Path(__file__).resolve().parent / "golden" / "tool_versions.yml"
_VERSION_FLAGS = {"yosys": "-V", "verilator": "--version"}

_HEADER = """\
# The tool versions the tool-dependent golden records were checked with: Yosys
# for metrics.yml's cell counts, Verilator for rtl_coverage.yml. On another
# version a deviation in those is a warning, not a failure, unless
# AUTOMBIST_GOLDEN_STRICT=1. Quote a version added by hand, so 5.050 stays
# 5.050. See tests/golden_tools.py.
"""


def tool_version(tool: str) -> str:
    """The second word of the tool's version line: "0.62" for "Yosys 0.62 (git sha1 ...)"."""
    run = subprocess.run([tool, _VERSION_FLAGS[tool]], capture_output=True, text=True)
    words = run.stdout.split()
    return words[1] if run.returncode == 0 and len(words) > 1 else "unknown"


def _checked() -> dict[str, list[str]]:
    if not TOOL_VERSIONS.exists():
        return {}
    checked = yaml.safe_load(TOOL_VERSIONS.read_text(encoding="utf-8")) or {}
    return {tool: [str(version) for version in versions] for tool, versions in checked.items()}


def record_tool_version(tool: str) -> None:
    checked = _checked()
    checked[tool] = [tool_version(tool)]
    TOOL_VERSIONS.write_text(_HEADER + yaml.safe_dump(checked, sort_keys=True), encoding="utf-8", newline="\n")


def failing(tool: str, problems: list[str], what: str) -> list[str]:
    """``problems`` if they should fail; otherwise warns about them and returns none."""
    if not problems or os.environ.get("AUTOMBIST_GOLDEN_STRICT") == "1":
        return problems
    version, checked = tool_version(tool), _checked().get(tool, [])
    if version in checked:
        return problems
    warnings.warn(
        f"{what} moved from the record on {tool} {version}, which tests/golden/tool_versions.yml does not "
        f"list (checked with {', '.join(checked) or 'none'}), so it may be the tool and not the RTL; "
        "AUTOMBIST_GOLDEN_STRICT=1 makes this fail:\n  " + "\n  ".join(problems),
        stacklevel=2,
    )
    return []
