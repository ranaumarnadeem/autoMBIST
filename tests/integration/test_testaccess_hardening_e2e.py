"""The inserted test access, hardened with warptap 0.0.4's options, does what each option
is for, shown in simulation of the wrapped RTL against the same netlist wrapped without it:

  * a chip reset clears the controls JTAG drives: after test_mode=1 and bist_start=1, a
    pulse on rst_n with TRST held high leaves the BIST idle, so bist_done reads 0 after the
    run loop. Wrapped without the clear, the same pattern reruns the BIST (the controller
    starts on the level of bist_start), and bist_done reads 1.

Each pattern is written as STIL and replayed literally (stil_replay.py), so it can pulse
rst_n part-way through, which the .vec format cannot.

Skips without warptap 0.0.4, Yosys or Icarus Verilog.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from autombist.cli import app
from autombist.jtag_bist import SYSTEM_CLOCK, PdlStep, StilTiming, parse_netlist_ports, render_stil, stil_support_problem
from autombist.manifest import synthesis_sources
from autombist.testaccess import test_access_kwargs_from_config, wrap_test_access
from autombist.testbench import bist_cycle_bound

from stil_replay import replay_stil


def _ready() -> bool:
    try:
        import warptap  # noqa: F401
    except ImportError:
        return False
    return stil_support_problem() is None


pytestmark = pytest.mark.skipif(
    not _ready() or any(shutil.which(t) is None for t in ("yosys", "iverilog", "vvp")),
    reason="needs warptap 0.0.4 or later plus yosys and Icarus Verilog on PATH (Linux/WSL only)",
)

runner = CliRunner()
TOP = "hx_ctrl"
CONFIG = {"memory_name": "sram_1rw", "wrapper_module_name": TOP, "addr_width": 3, "data_width": 4,
          "we_active_low": True,
          "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"}}
TIMING = StilTiming(tck_period_ns=100.0, clk_period_ns=100.0)


@pytest.fixture(scope="module")
def design(tmp_path_factory) -> dict:
    """A generated design: the sources wrap-test-access --manifest would wrap, its config
    snapshot and the generated memory model."""
    root = tmp_path_factory.mktemp("hardening")
    path = root / "c.yml"
    path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    made = runner.invoke(app, ["generate", "--config", str(path), "--out", str(root / "out"), "--emit-manifest"])
    assert made.exit_code == 0, made.output
    module_outdir = root / "out" / CONFIG["memory_name"]
    manifest = json.loads((module_outdir / "manifest.json").read_text(encoding="utf-8"))
    snapshot = yaml.safe_load((module_outdir / "config.yml").read_text(encoding="utf-8"))
    return {
        "root": root,
        "sources": [*synthesis_sources(snapshot, module_outdir), module_outdir / manifest["sources"]["blackbox_stub"]],
        "snapshot": snapshot,
        "model": module_outdir / "tb" / f"{CONFIG['memory_name']}_model.v",
    }


def _wrap(design: dict, name: str, **options):
    verilog, graph, root = wrap_test_access(
        design["sources"], TOP, **test_access_kwargs_from_config(design["snapshot"]), **options,
    )
    path = design["root"] / f"{name}.v"
    path.write_text(verilog, encoding="utf-8")
    return path, verilog, graph, root


def _play(pdl, step: PdlStep) -> None:
    if step.kind == "write":
        pdl.iTarget(step.instrument)
        pdl.iWrite(step.value)
    elif step.kind == "read":
        pdl.iTarget(step.instrument)
        pdl.iRead(step.value)
    elif step.kind == "apply":
        pdl.iApply()
    elif step.kind == "runloop":
        pdl.iRunLoop(step.count, sck_port=SYSTEM_CLOCK)


def _ops(graph, root, before: list[PdlStep], middle: list, after: list[PdlStep]) -> list:
    """``before`` and ``after`` retargeted through one PDL interpreter (so it tracks the
    network's state across both), with the raw TAP ops ``middle`` played between them,
    behind the instruction that selects the network."""
    from warptap.pdl_interpreter import PDLInterpreter
    from warptap.tap_fsm import TapState
    from warptap.tap_ir import GotoState, ShiftIR
    from warptap.tap_model import DEFAULT_IR_WIDTH, OPCODE_EXTEST

    pdl = PDLInterpreter(graph, root)
    for step in before:
        _play(pdl, step)
    mark = len(pdl.program)
    for step in after:
        _play(pdl, step)
    program = list(pdl.program)
    return [
        GotoState(TapState.SHIFT_IR), ShiftIR(DEFAULT_IR_WIDTH, tdi=OPCODE_EXTEST), GotoState(TapState.RUN_TEST_IDLE),
        *program[:mark], *middle, *program[mark:],
    ]


def _replay(design: dict, netlist: Path, verilog: str, ops: list, name: str):
    ports = parse_netlist_ports(verilog, TOP)
    stil = render_stil(TOP, ops, ports, TIMING)
    return replay_stil(stil, [netlist, design["model"]], TOP, ports, design["root"] / f"replay_{name}")


def test_a_chip_reset_clears_the_controls_jtag_drives(design: dict) -> None:
    from warptap.tap_ir import PulsePin, SetPins

    apply = PdlStep("apply")
    start = [PdlStep("write", "test_mode", 1), apply, PdlStep("write", "bist_start", 1), apply]
    chip_reset = [SetPins((("rst_n", 0),)), PulsePin(SYSTEM_CLOCK, 2), SetPins((("rst_n", 1),))]
    then = [PdlStep("runloop", count=bist_cycle_bound(design["snapshot"])), PdlStep("read", "bist_done", 0), apply]

    results = {}
    for name, options in (("cleared", {}), ("trst_only", {"chip_reset": None})):
        netlist, verilog, graph, root = _wrap(design, name, **options)
        results[name] = _replay(design, netlist, verilog, _ops(graph, root, start, chip_reset, then), name)

    # cleared: the BIST stays idle after the chip reset, so bist_done reads the 0 expected
    assert results["cleared"].passed, results["cleared"].output
    # TRST only: test_mode and bist_start survive the chip reset and the BIST runs again
    assert results["trst_only"].mismatches == [0], results["trst_only"].output
