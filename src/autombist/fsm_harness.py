"""Port-contract check + harness generation for `add_fsm` -- validate a
researcher's MBIST controller FSM against the fault-campaign engine.

The FSM must expose the same contract as rtl/march_c/march_c_top.sv /
rtl/march_raw/march_raw_top.sv: a start/done/fail handshake plus an OpenRAM-
style 1rw SRAM bus. The generated harness instantiates FSM -> openram_shim ->
fault_ram and reports detect/escape via the same RESULT grammar the algorithm
front uses (see algo_engine.parse_result_line), so both fronts share one
campaign runner and output parser. The FSM front cannot report elem/op
attribution -- bist_fail is a single bit, not an algorithm's internal step
counter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .generator import _render_template

HARNESS_TOP = "fsm_harness"

# Mirrors rtl/march_c/march_c_top.sv and rtl/march_raw/march_raw_top.sv.
REQUIRED_PORTS: dict[str, str] = {
    "clk": "input",
    "rst_n": "input",
    "bist_start": "input",
    "bist_done": "output",
    "bist_fail": "output",
    "sram_clk0": "output",
    "sram_csb0": "output",
    "sram_web0": "output",
    "sram_addr0": "output",
    "sram_din0": "output",
    "sram_dout0": "input",
}

# 2-port sibling of REQUIRED_PORTS. Mirrors rtl/march_2rw/march_2rw_top.sv:
# the clk/rst_n/bist_start/bist_done/bist_fail handshake is unchanged, and
# BOTH port-0 and port-1 OpenRAM-style SRAM buses are required (a genuinely
# symmetric 2RW pinout, not a second read-only or write-only port).
REQUIRED_PORTS_MP: dict[str, str] = {
    **REQUIRED_PORTS,
    "sram_clk1": "output",
    "sram_csb1": "output",
    "sram_web1": "output",
    "sram_addr1": "output",
    "sram_din1": "output",
    "sram_dout1": "input",
}

_MODULE_RE = re.compile(r"\bmodule\s+(\w+)\b")
# One port declaration per line (the convention used throughout this repo's RTL).
_PORT_LINE_RE = re.compile(
    r"\b(input|output|inout)\b\s*(?:logic|wire|reg)?\s*(?:\[[^\]]*\]\s*)?(\w+)\s*[,)]?"
)


class FsmPortError(ValueError):
    """Raised when an FSM source is missing required ports for the harness."""


@dataclass(slots=True)
class FsmPorts:
    module_name: str
    ports: dict[str, str]  # name -> direction ("input"/"output"/"inout")

    def missing(self, contract: dict[str, str] = REQUIRED_PORTS) -> dict[str, str]:
        return {name: expected for name, expected in contract.items() if self.ports.get(name) != expected}


def _skip_balanced(text: str, open_pos: int) -> int:
    """Given the index of an opening '(', return the index just past its match."""
    depth = 0
    i = open_pos
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise FsmPortError("unbalanced parentheses while scanning the module declaration")


def parse_ports(sv_text: str, module_name: str | None = None) -> FsmPorts:
    """Extract the port list of a module. If module_name is given, find that
    specific module; otherwise use the first 'module <name>' declaration."""
    if module_name is not None:
        match = re.search(rf"\bmodule\s+{re.escape(module_name)}\b", sv_text)
        if not match:
            raise FsmPortError(f"module '{module_name}' not found in source")
    else:
        match = _MODULE_RE.search(sv_text)
        if not match:
            raise FsmPortError("no 'module <name>' declaration found in FSM source")
        module_name = match.group(1)

    pos = match.end()

    # Skip an optional `#( ... )` parameter block (balanced scan, so nested
    # parens in default expressions like $clog2(...) don't confuse it).
    param_match = re.match(r"\s*#\s*\(", sv_text[pos:])
    if param_match:
        open_paren = pos + param_match.end() - 1
        pos = _skip_balanced(sv_text, open_paren)

    port_open = sv_text.find("(", pos)
    if port_open == -1:
        raise FsmPortError(f"module '{module_name}' has no port list")
    port_close = _skip_balanced(sv_text, port_open) - 1

    port_block = sv_text[port_open + 1 : port_close]
    ports: dict[str, str] = {}
    for line in port_block.splitlines():
        m = _PORT_LINE_RE.search(line)
        if m:
            ports[m.group(2)] = m.group(1)
    return FsmPorts(module_name=module_name, ports=ports)


def check_ports(sv_text: str, module_name: str | None = None, num_ports: int = 1) -> FsmPorts:
    """Validate the FSM exposes the required port contract. Raises FsmPortError
    with an expected-vs-found diff if anything is missing or mis-directioned.

    num_ports=1 (the default, preserving every existing call site's exact
    behavior) validates against REQUIRED_PORTS (single sram_*0 bus).
    num_ports=2 validates against REQUIRED_PORTS_MP (sram_*0 AND sram_*1
    buses both required), mirroring rtl/march_2rw/march_2rw_top.sv's pinout.
    """
    if num_ports not in (1, 2):
        raise ValueError(f"num_ports must be 1 or 2, got {num_ports!r}")
    contract = REQUIRED_PORTS_MP if num_ports == 2 else REQUIRED_PORTS
    parsed = parse_ports(sv_text, module_name=module_name)
    missing = parsed.missing(contract)
    if missing:
        lines = [f"module '{parsed.module_name}' is missing the required MBIST-FSM port contract:"]
        for name, expected_dir in missing.items():
            found_dir = parsed.ports.get(name, "(not found)")
            lines.append(f"  {name}: expected {expected_dir}, found {found_dir}")
        lines.append("Required ports: " + ", ".join(f"{n}({d})" for n, d in contract.items()))
        lines.append(
            "(Port declarations must be one-per-line, e.g. 'output logic bist_fail,' -- "
            "this is a structural check, not a full Verilog parser.)"
        )
        raise FsmPortError("\n".join(lines))
    return parsed


def gather_sibling_sources(top_path: Path) -> list[Path]:
    """top_path plus every other .sv/.v file in its directory (top first),
    resolved to absolute paths.

    Real MBIST FSMs are usually split across files (e.g. march_c_top.sv
    instantiates march_c_fsm.sv and march_c_algo.sv); pointing at just the top
    file and pulling in its directory siblings matches how this repo's own
    rtl/<algo>/ directories are laid out. Paths are resolved to absolute
    because Verilator is invoked with cwd set to the campaign's (temporary)
    workdir, not the caller's cwd -- a relative path would silently resolve
    against the wrong directory and fail with a confusing "file not found".
    """
    top_path = Path(top_path).resolve()
    directory = top_path.parent
    siblings = sorted(p for p in directory.glob("*.sv") if p != top_path)
    siblings += sorted(p for p in directory.glob("*.v") if p != top_path)
    return [top_path, *siblings]


def render_harness(*, addr_width: int, data_width: int, fsm_module_name: str, has_bist_busy: bool = False) -> str:
    return _render_template(
        {
            "harness_top": HARNESS_TOP,
            "addr_width": addr_width,
            "data_width": data_width,
            "fsm_module_name": fsm_module_name,
            "has_bist_busy": has_bist_busy,
        },
        "fsm_harness_template.sv.j2",
    )


def render_harness_mp(*, addr_width: int, data_width: int, fsm_module_name: str, has_bist_busy: bool = False) -> str:
    """2-port sibling of render_harness -- same parameters, wires a 2-port
    researcher FSM (REQUIRED_PORTS_MP contract) through openram_shim_mp.sv
    (which wraps ONE fault_ram core rendered with num_ports=2) instead of
    openram_shim.sv."""
    return _render_template(
        {
            "harness_top": HARNESS_TOP,
            "addr_width": addr_width,
            "data_width": data_width,
            "fsm_module_name": fsm_module_name,
            "has_bist_busy": has_bist_busy,
        },
        "fsm_harness_mp_template.sv.j2",
    )
