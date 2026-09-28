"""Standalone, self-checking MBIST testbenches written next to the generated RTL.

``generate`` writes ``tb/``: a Verilog testbench that runs the built-in self-test
from the wrapper's own pins, and ``run_tb.sh``, which compiles it with Icarus
Verilog against the generated RTL and the memory's own simulation model -- the
one file the output directory can't contain, since it ships with the memory
macro. Running it needs no Python and no cocotb. ``wrap-test-access`` adds the
JTAG counterpart (``jtag_bist.py``), which runs the same test over the IJTAG
network and checks the result at TDO, the way a tester does.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .generator import _render_template
from .manifest import synthesis_sources

TB_DIRNAME = "tb"

# The ports every generated wrapper has that the testbench drives or checks
# itself; every other input is held at its idle value.
_DRIVEN = ("clk", "rst_n", "test_mode", "bist_start")
_CHECKED = ("bist_done", "bist_fail")
# Inputs whose idle value is all ones: the functional port's active-low chip select.
_IDLE_HIGH = ("func_csb",)

# Clock cycles the generated controller spends per address, per algorithm, as
# (fixed, reads): fixed + reads * READ_LATENCY. A write takes ISSUE -> CHECK
# (2 cycles), a read ISSUE -> WAIT (READ_LATENCY cycles) -> CHECK, so `reads`
# is the reads per address the controller serializes. Measured, not derived
# from the algorithms' textbook "n": exact against the RTL at READ_LATENCY 0,
# 1 and 2 and at 4 to 32 words (tests/integration/test_testbench_e2e.py keeps
# checking the bound against simulation). The two-port controllers overlap
# their ports' operations, hence their small fixed parts.
_CYCLES_PER_ADDRESS: dict[str, tuple[int, int]] = {
    "march-c": (20, 5),
    "march-raw": (28, 8),
    "march-x": (12, 3),
    "mats-plus": (10, 2),
    "checkerboard": (12, 3),
    "march-1r1w": (12, 5),
    "march-2rw": (12, 3),
}
# Cycles per memory outside the address loop: start, done, and a shared
# controller's switch to its next memory.
_CYCLES_PER_MEMORY = 3
# Margin on the computed BIST length: the timeout and the PDL's run loop need
# an upper bound, and a configuration no test measured (column repair,
# diagnosis) may add a few cycles.
_BOUND_MARGIN = 1.25
_BOUND_SLACK = 16


class TestbenchError(ValueError):
    """Raised when a testbench can't be generated for a wrapper."""

    __test__ = False  # not a pytest test class -- the name just starts with "Test"


@dataclass(frozen=True)
class WrapperPort:
    name: str
    direction: str  # "input" | "output"
    range: str  # "[ADDR_WIDTH-1:0] " or "" for a 1-bit port


_PORT_RE = re.compile(
    r"\b(input|output)\s+(?:logic|wire|reg)?\s*(\[[^\]]*\])?\s*([A-Za-z_]\w*)"
)


def parse_wrapper_ports(verilog: str, module: str) -> list[WrapperPort]:
    """The ANSI port list of ``module`` in a generated wrapper, in order.

    The wrapper template's header is regular -- ``module <name> #(<parameters>)
    (<ports>);`` with one ``input``/``output logic [range] name`` per port -- so
    the rendered header is the single source of truth for which ports a given
    configuration has."""
    header = re.search(
        rf"\bmodule\s+{re.escape(module)}\s*#\s*\((.*?)\)\s*\((.*?)\)\s*;", verilog, re.DOTALL
    )
    if header is None:
        raise TestbenchError(f"no ANSI header for module {module!r} in the wrapper")
    ports = [
        WrapperPort(name, direction, f"{rng} " if rng else "")
        for direction, rng, name in _PORT_RE.findall(header.group(2))
    ]
    missing = [p for p in (*_DRIVEN, *_CHECKED) if p not in {q.name for q in ports}]
    if missing:
        raise TestbenchError(f"wrapper {module!r} has no {', '.join(missing)} port")
    return ports


def bist_cycles(config: dict[str, Any]) -> int:
    """The BIST's length in clk cycles, from start to done, as the generated
    controller runs it: every address of every memory a shared controller
    tests in turn (spare rows are not in the BIST's address space)."""
    algo = str(config.get("algo", "march-c")).strip().lower()
    try:
        fixed, reads = _CYCLES_PER_ADDRESS[algo]
    except KeyError:
        raise TestbenchError(f"no BIST length known for algorithm {algo!r}") from None
    words = 1 << int(config["addr_width"])
    memories = len(config.get("memories") or []) or 1
    per_address = fixed + reads * int(config.get("read_latency", 1))
    return memories * (words * per_address + _CYCLES_PER_MEMORY)


def bist_cycle_bound(config: dict[str, Any]) -> int:
    """An upper bound on the BIST's length in clk cycles (``bist_cycles`` plus
    a margin): the testbench's timeout and the PDL's run loop."""
    return int(math.ceil(bist_cycles(config) * _BOUND_MARGIN)) + _BOUND_SLACK


def _idle_value(port: WrapperPort) -> str:
    return "'1" if port.name in _IDLE_HIGH else "'0"


def render_bist_testbench(config: dict[str, Any], ports: list[WrapperPort], max_cycles: int) -> str:
    top = str(config["wrapper_module_name"])
    special = {*_DRIVEN, *_CHECKED}
    context = {
        "top": top,
        "addr_width": config["addr_width"],
        "data_width": config["data_width"],
        "max_cycles": max_cycles,
        "ports": ports,
        "idle_inputs": [
            {"name": p.name, "range": p.range, "idle": _idle_value(p)}
            for p in ports if p.direction == "input" and p.name not in special
        ],
        "other_outputs": [p for p in ports if p.direction == "output" and p.name not in special],
    }
    return _render_template(context, "bist_tb_template.sv.j2")


def render_run_script(
    *,
    what: str,
    top: str,
    memory_name: str,
    script: str,
    tb_module: str,
    tb_file: str,
    out_rel: str,
    sources: list[str],
    timeout_define: bool = True,
    model_required: bool = True,
    data_files: tuple[str, ...] = (),
) -> str:
    """A bash script compiling ``tb_file`` with ``sources`` (relative to the
    output directory, itself ``out_rel`` from the script's own directory) and
    the memory model(s) given on the command line, then running it.
    ``timeout_define`` passes $MBIST_MAX_CYCLES on as the testbench's timeout;
    ``model_required`` makes the memory model a mandatory first argument;
    ``data_files`` (next to the script) are copied to where it runs."""
    return _render_template(
        {
            "what": what,
            "top": top,
            "memory_name": memory_name,
            "script": script,
            "tb_module": tb_module,
            "tb_file": tb_file,
            "out_rel": out_rel,
            "sources": sources,
            "timeout_define": timeout_define,
            "model_required": model_required,
            "data_files": list(data_files),
        },
        "run_tb_template.sh.j2",
    )


def rtl_sources(config: dict[str, Any], module_outdir: Path) -> list[str]:
    """The generated RTL the testbench compiles, relative to ``module_outdir``:
    the wrapper and every instrument's RTL, plus the fault-injection saboteur
    for a --test build (which wraps the memory model rather than replacing it)."""
    clean = {**config, "use_saboteur": False}
    sources = [Path(p).relative_to(module_outdir).as_posix() for p in synthesis_sources(clean, module_outdir)]
    if config.get("use_saboteur"):
        sources.append(f"{config['memory_name']}_saboteur.v")
    return sources


def _chmod_x(path: Path) -> None:
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


def write_bist_testbench(module_outdir: Path, config: dict[str, Any], wrapper_text: str) -> Path:
    """Write ``tb/tb_<top>.sv`` and ``tb/run_tb.sh`` into ``module_outdir``;
    returns the ``tb/`` directory."""
    module_outdir = Path(module_outdir)
    top = str(config["wrapper_module_name"])
    ports = parse_wrapper_ports(wrapper_text, top)
    tb_dir = module_outdir / TB_DIRNAME
    tb_dir.mkdir(parents=True, exist_ok=True)
    tb_file = f"tb_{top}.sv"
    (tb_dir / tb_file).write_text(
        render_bist_testbench(config, ports, bist_cycle_bound(config)), encoding="utf-8"
    )
    run_sh = tb_dir / "run_tb.sh"
    run_sh.write_text(
        render_run_script(
            what="MBIST testbench",
            top=top,
            memory_name=str(config["memory_name"]),
            script=f"{TB_DIRNAME}/run_tb.sh",
            tb_module=f"tb_{top}",
            tb_file=tb_file,
            out_rel="..",
            sources=rtl_sources(config, module_outdir),
        ),
        encoding="utf-8",
    )
    _chmod_x(run_sh)
    return tb_dir
