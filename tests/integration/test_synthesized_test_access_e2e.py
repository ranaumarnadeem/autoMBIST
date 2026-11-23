"""Gate-level proof that the JTAG/IJTAG test-access network survives synthesis:
`wrap-test-access` hands over a netlist with the TAP, the SIBs, the TDRs and
their TCK synchronizers inserted around the MBIST controller; this synthesizes
it with Yosys (memory blackboxed, hierarchy kept), checks every network
instance is still there as the same module, and plays the JTAG testbench
against the gate-level netlist instead.

The JTAG testbench checks only TDO, so it sees exactly what a tester would.
Each pass has a matching failure to show the TDO compare still discriminates
after synthesis:

  * run_mbist over a good memory passes, over a stuck bit fails the bist_fail
    read; the IDCODE reads back through the gate-level TAP;
  * a shared-bus controller's network passes run_mbist;
  * on-chip self-repair started and read back over JTAG (with the diagnosis
    log's wide status registers on the chain) repairs one defect and reports
    two as unrepairable;
  * a tester-driven repair signature written over JTAG through the wide
    repair-pin TDRs makes a defective memory pass, and not writing it fails.

Skips without warptap, Yosys or Icarus Verilog.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from synth_netlist import instances, synthesize
from typer.testing import CliRunner

from autombist.cli import app
from autombist.testbench import bist_cycle_bound

TESTS = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("warptap") is None
    or any(shutil.which(tool) is None for tool in ("yosys", "iverilog", "vvp")),
    reason="needs warptap (the Nix dev shell pins it) plus yosys and Icarus Verilog on PATH (Linux/WSL only)",
)

runner = CliRunner()
HW = TESTS / "hardware"
P1 = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"}
BASE = {"memory_name": "sram_1rw", "wrapper_module_name": "gt_ctrl", "addr_width": 3,
        "data_width": 4, "we_active_low": True, "ports": P1}
SPARES = {**BASE, "memory_name": "sram_spares_tiny", "addr_width": 2, "ports": {**P1, "we": "web0"}}
NETWORK = "warptap_"  # the prefix of every instance warptap inserts


class Wrapped:
    """A design after `generate` + `wrap-test-access`, its inserted netlist
    replaced by the synthesized one."""

    def __init__(self, access: Path, top: str, snapshot: dict, graph, root) -> None:
        self.access, self.top, self.snapshot, self.graph, self.root = access, top, snapshot, graph, root

    def run(self, *models: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(self.access / "run_tb_jtag.sh"), *map(str, models)],
                              capture_output=True, text=True)

    def use_procedure(self, steps, labels: list[str]) -> None:
        """Replace run_mbist's vectors (and the testbench's names for its reads)
        with ``steps``, retargeted through this network."""
        from autombist.jtag_bist import parse_netlist_ports, render_jtag_testbench, retarget
        from autombist.testaccess import network_access

        access = network_access("ijtag-access")
        vectors = retarget(self.graph, self.root, steps, access=access)
        vec = self.access / f"{self.top}_run_mbist.vec"
        vec.write_text("".join(v.line() + "\n" for v in vectors), encoding="utf-8")
        netlist = (self.access / f"{self.top}_test_access.v").read_text(encoding="utf-8")
        tb = render_jtag_testbench(self.top, parse_netlist_ports(netlist, self.top), vectors_file=vec.name,
                                   pdl_file=f"{self.top}_run_mbist.pdl", read_labels=labels,
                                   instruction=access.instruction)
        (self.access / f"tb_{self.top}_jtag.sv").write_text(tb, encoding="utf-8")


def _wrap_and_synthesize(tmp_path: Path, config: dict, monkeypatch: pytest.MonkeyPatch) -> Wrapped:
    import autombist.testaccess as testaccess

    captured = {}
    real = testaccess.wrap_test_access

    def capture(*args, **kwargs):
        captured["result"] = real(*args, **kwargs)
        return captured["result"]

    monkeypatch.setattr(testaccess, "wrap_test_access", capture)

    config_path = tmp_path / "c.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    result = runner.invoke(app, ["generate", "--config", str(config_path), "--out",
                                 str(tmp_path / "out"), "--emit-manifest"])
    assert result.exit_code == 0, result.output
    top = config["wrapper_module_name"]
    module_outdir = tmp_path / "out" / (top if "topology" in config else config["memory_name"])
    result = runner.invoke(app, ["wrap-test-access", "--manifest", str(module_outdir), "--emit-icl"])
    assert result.exit_code == 0, result.output
    _, graph, root = captured["result"]

    access = module_outdir / "test-access"
    inserted_path = access / f"{top}_test_access.v"
    inserted = inserted_path.read_text(encoding="utf-8")
    gate = synthesize([inserted_path], top=top, out=access / "gate.v", flatten=False,
                      blackboxes=[module_outdir / f"{config['memory_name']}_bbox.v"])

    network = instances(inserted, prefix=NETWORK)
    assert {"tap_core", "sib_cell"} <= set(network.values()), network
    assert instances(gate, prefix=NETWORK) == network, "synthesis dropped or changed part of the network"
    inserted_path.write_text(gate, encoding="utf-8")  # play the vectors against the gates

    snapshot = yaml.safe_load((module_outdir / "config.yml").read_text(encoding="utf-8"))
    return Wrapped(access, top, snapshot, graph, root)


def _stuck_1rw(tmp_path: Path) -> Path:
    stuck = tmp_path / "stuck.v"
    stuck.write_text(re.sub(r"\bmodule\s+\w+", "module sram_1rw",
                            (HW / "sram_1rw_stuck_bit.v").read_text(), count=1))
    return stuck


def _idcode_steps_vectors(expected: int) -> str:
    """Run-Test/Idle -> Shift-DR (IDCODE is selected after TAP reset), 32 shifts
    checked against ``expected``, then back to Run-Test/Idle."""
    from warptap.tap_fsm import TapState, next_state
    from warptap.tap_ir import bits_from_int
    from warptap.tap_ir_play import navigation_tms, shift_tms

    lines: list[str] = []
    state = TapState.RUN_TEST_IDLE
    for tms in navigation_tms(state, TapState.SHIFT_DR):
        lines.append(f"0 {tms} 0 0 0")
        state = next_state(state, tms)
    bits = bits_from_int(expected, 32)
    for i, tms in enumerate(shift_tms(32)):
        lines.append(f"0 {tms} 0 {bits[i]} 1")
        state = next_state(state, tms)
    for tms in navigation_tms(state, TapState.RUN_TEST_IDLE):
        lines.append(f"0 {tms} 0 0 0")
    return "".join(line + "\n" for line in lines)


def test_gate_level_run_mbist_passes_a_good_memory_and_fails_a_stuck_bit(tmp_path, monkeypatch) -> None:
    wrapped = _wrap_and_synthesize(tmp_path, BASE, monkeypatch)

    good = wrapped.run(HW / "sram_1rw.v")
    assert good.returncode == 0, good.stdout + good.stderr
    assert "MBIST RESULT: PASS -- read back through TDO: bist_done = 1, bist_fail = 0" in good.stdout

    bad = wrapped.run(_stuck_1rw(tmp_path))
    assert bad.returncode != 0
    assert "TDO did not read back bist_fail = 0" in bad.stdout + bad.stderr


def test_gate_level_tap_reads_back_its_idcode(tmp_path, monkeypatch) -> None:
    from warptap.tap_model import IDCODE_VALUE

    wrapped = _wrap_and_synthesize(tmp_path, BASE, monkeypatch)
    vec = wrapped.access / f"{wrapped.top}_run_mbist.vec"

    vec.write_text(_idcode_steps_vectors(IDCODE_VALUE), encoding="utf-8")
    run = wrapped.run(HW / "sram_1rw.v")
    assert run.returncode == 0, run.stdout + run.stderr

    vec.write_text(_idcode_steps_vectors(IDCODE_VALUE ^ 0x10), encoding="utf-8")
    assert wrapped.run(HW / "sram_1rw.v").returncode != 0


def test_gate_level_shared_bus_network_runs_mbist(tmp_path, monkeypatch) -> None:
    config = {**BASE, "wrapper_module_name": "gt_shb", "topology": "shared-bus",
              "memories": [{"name": "b0"}, {"name": "b1"}]}
    wrapped = _wrap_and_synthesize(tmp_path, config, monkeypatch)

    run = wrapped.run(HW / "sram_1rw.v")

    assert run.returncode == 0, run.stdout + run.stderr
    assert "MBIST RESULT: PASS" in run.stdout


def _self_repair_steps(snapshot: dict):
    from autombist.jtag_bist import PdlStep

    apply = PdlStep("apply")
    # Self-repair runs the march, analyzes, applies and re-runs it: well within
    # four BIST lengths. self_repair_done holds while self_repair_start does.
    steps = [
        PdlStep("write", "self_repair_start", 1), apply,
        PdlStep("runloop", count=4 * bist_cycle_bound(snapshot)),
        PdlStep("read", "self_repair_done", 1), apply,
        PdlStep("read", "self_repair_fail", 0), apply,
        PdlStep("write", "self_repair_start", 0), apply,
    ]
    return steps, ["self_repair_done = 1", "self_repair_fail = 0"]


@pytest.mark.parametrize(("memory_name", "spares", "repaired"), [
    ("sram_spares_tiny", 2, True),          # one defect, two spare rows
    ("sram_spares_tiny_2defect", 1, False),  # two defective rows, one spare
], ids=["repairable", "unrepairable"])
def test_gate_level_self_repair_runs_over_jtag(tmp_path, monkeypatch, memory_name, spares, repaired) -> None:
    config = {**SPARES, "memory_name": memory_name,
              "redundancy": {"num_spare_rows": spares, "num_spare_cols": 0, "onchip_selfrepair": True,
                             "onchip_diagnosis": True, "num_diagnosis_entries": 2}}
    wrapped = _wrap_and_synthesize(tmp_path, config, monkeypatch)
    widths = {n.instrument.name: n.instrument.width for n in wrapped.graph.chain if n.instrument is not None}
    assert widths["diag_valid"] == 2 and widths["diag_addr"] == 4  # the wide status TDRs are on the chain

    wrapped.use_procedure(*_self_repair_steps(wrapped.snapshot))
    run = wrapped.run(HW / f"{memory_name}.v")

    if repaired:
        assert run.returncode == 0, run.stdout + run.stderr
        assert "MBIST RESULT: PASS" in run.stdout
    else:
        assert run.returncode != 0
        assert "TDO did not read back self_repair_fail = 0" in run.stdout + run.stderr


def _repair_steps(snapshot: dict, *, enable: int, row: int):
    from autombist.jtag_bist import PdlStep

    apply = PdlStep("apply")
    steps = [
        PdlStep("write", "row_repair_en", enable), apply,
        PdlStep("write", "faulty_row_addr", row), apply,
        PdlStep("write", "test_mode", 1), apply,
        PdlStep("write", "bist_start", 1), apply,
        PdlStep("runloop", count=bist_cycle_bound(snapshot)),
        PdlStep("read", "bist_done", 1), apply,
        PdlStep("read", "bist_fail", 0), apply,
        PdlStep("write", "bist_start", 0), apply,
        PdlStep("write", "test_mode", 0), apply,
    ]
    return steps, ["bist_done = 1", "bist_fail = 0"]


@pytest.mark.parametrize("enable", [1, 0], ids=["signature-written", "no-signature"])
def test_gate_level_repair_signature_written_over_jtag(tmp_path, monkeypatch, enable) -> None:
    """sram_spares_tiny's defect is at address 3: the spare row replaces it only
    when the repair pins, written through their TDRs, say so."""
    config = {**SPARES, "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0},
              "repair_ports": [{"name": "row_repair_en", "width": 1, "dir": "input"},
                               {"name": "faulty_row_addr", "width": 2, "dir": "input"}]}
    wrapped = _wrap_and_synthesize(tmp_path, config, monkeypatch)

    wrapped.use_procedure(*_repair_steps(wrapped.snapshot, enable=enable, row=3))
    run = wrapped.run(HW / "sram_spares_tiny.v")

    if enable:
        assert run.returncode == 0, run.stdout + run.stderr
        assert "MBIST RESULT: PASS" in run.stdout
    else:
        assert run.returncode != 0
        assert "TDO did not read back bist_fail = 0" in run.stdout + run.stderr
