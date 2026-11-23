"""Yosys synthesis for the gate-level tests: what a downstream flow does to the
RTL and netlists autoMBIST hands over, so a test can simulate that result
instead of the RTL.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable

# An instance line in a Yosys-written netlist: `  <module> <instance> (`, either
# name possibly escaped (`\name `).
_INSTANCE = re.compile(r"^\s+(\\\S+|[A-Za-z_]\w*)\s+(\\\S+|[A-Za-z_]\w*)\s*\(\s*$", re.MULTILINE)


def synthesize(
    sources: Iterable[Path],
    *,
    top: str,
    out: Path,
    blackboxes: Iterable[Path] = (),
    flatten: bool = True,
) -> str:
    """Synthesize ``sources`` (``blackboxes`` read as cell libraries, the way a
    memory macro is) to Yosys generic gates, write the netlist to ``out`` and
    return it. ``flatten=False`` keeps the module hierarchy, so the netlist's
    instances can be compared with the input's."""
    commands = [f"read_verilog -sv {' '.join(str(s) for s in sources)}"]
    commands += [f"read_verilog -lib {b}" for b in blackboxes]
    commands += [f"hierarchy -check -top {top}", "proc"]
    if flatten:
        commands.append("flatten")
    commands += [f"synth -top {top}", f"write_verilog -noattr {out}"]
    result = subprocess.run(["yosys", "-q", "-p", "; ".join(commands)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return Path(out).read_text(encoding="utf-8")


def instances(verilog: str, *, prefix: str) -> dict[str, str]:
    """``{instance: module}`` for every instance whose name starts with ``prefix``."""
    return {inst.lstrip("\\"): module.lstrip("\\") for module, inst in _INSTANCE.findall(verilog)
            if inst.lstrip("\\").startswith(prefix)}
