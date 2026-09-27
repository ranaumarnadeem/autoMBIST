"""Gate-level proof that the Yosys-SYNTHESIZED MBIST collar still works, not
just the RTL: synthesize the wrapper + controller (memory kept as a blackbox
instance via the emitted <memory_name>_bbox.v stub, sources from
grade-controller's own controller_sources), swap that netlist in for the RTL
wrapper, and run the real cocotb test_mbist.test_clean against a real memory
model.

A clean pass alone is vacuous here: before shared-bus's per-memory arrays
were made packed, Yosys deleted every shared-bus memory instance and, with
the read data undefined, optimized the compare path so the synthesized BIST
passed a good memory AND a broken one. So every topology is also run against
sram_1rw_stuck_bit.v and must FAIL there.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("iverilog", "make", "yosys")),
    reason="needs Icarus Verilog + make + Yosys on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.faultflow_flow import controller_sources, memory_instances  # noqa: E402
from autombist.generator import generate_from_config  # noqa: E402
from autombist.runner import SimulationError, run_simulation  # noqa: E402

PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"}
DEDICATED = {"wrapper_module_name": "ded_ctrl", "addr_width": 6, "data_width": 8,
             "we_active_low": True, "ports": PORTS}
SHARED_BUS = {**DEDICATED, "wrapper_module_name": "shared_bus_ctrl", "topology": "shared-bus",
              "memories": [{"name": "mem_bank0"}, {"name": "mem_bank1"}]}
TOPOLOGIES = {"dedicated": DEDICATED, "shared-bus": SHARED_BUS}


def _generate_and_synthesize(tmp_path: Path, base: dict, memory_name: str) -> Path:
    config = {**base, "memory_name": memory_name}
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / "out")
    module_outdir = wrapper.parent
    snapshot = yaml.safe_load((module_outdir / "config.yml").read_text(encoding="utf-8"))

    sources = " ".join(str(p) for p in controller_sources(module_outdir, snapshot))
    stub = module_outdir / f"{memory_name}_bbox.v"
    top = config["wrapper_module_name"]
    gate = module_outdir / "gate.v"
    result = subprocess.run(
        ["yosys", "-q", "-p",
         f"read_verilog -sv {sources}; read_verilog -lib {stub}; hierarchy -check -top {top}; "
         f"proc; flatten; synth -top {top}; write_verilog -noattr {gate}"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    netlist = gate.read_text(encoding="utf-8")
    for inst in memory_instances(snapshot):
        assert f") {inst} (" in netlist, f"synthesis dropped memory instance {inst}"
    wrapper.write_text(netlist, encoding="utf-8")  # simulate the netlist, not the RTL
    return module_outdir


@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_synthesized_bist_passes_a_good_memory(tmp_path: Path, topology: str) -> None:
    module_outdir = _generate_and_synthesize(tmp_path, TOPOLOGIES[topology], "sram_1rw")
    result = run_simulation(module_outdir)
    assert "test_mbist.test_clean passed" in result.stdout


@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_synthesized_bist_detects_a_stuck_bit(tmp_path: Path, topology: str) -> None:
    module_outdir = _generate_and_synthesize(tmp_path, TOPOLOGIES[topology], "sram_1rw_stuck_bit")
    with pytest.raises(SimulationError):
        run_simulation(module_outdir)
    log = (module_outdir / "simulate.log").read_text(encoding="utf-8")
    assert "test_mbist.test_clean failed" in log
    assert "MBIST reported fail in clean mode" in log
