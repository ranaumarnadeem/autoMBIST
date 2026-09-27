"""Gate-level proof that the Yosys-SYNTHESIZED MBIST collar still works, not
just the RTL: synthesize the wrapper + controller (memory kept as a blackbox
instance via the emitted <memory_name>_bbox.v stub, sources from
grade-controller's own controller_sources), swap that netlist in for the RTL
wrapper, and run the real cocotb test_mbist.test_clean against a real memory
model.

A clean pass alone is vacuous here: before shared-bus's per-memory arrays
were made packed, Yosys deleted every shared-bus memory instance and, with
the read data undefined, optimized the compare path so the synthesized BIST
passed a good memory AND a broken one. So every case is also run against a
stuck-bit memory and must FAIL there. The march-2rw case also proves the
controller synthesizes at all (its per-port ports used to be unpacked
arrays, which Yosys rejects) and, since its defect sits only in port 1's
read path, that port 1's own compare survives synthesis.
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
TWO_RW = {**DEDICATED, "wrapper_module_name": "two_rw_ctrl", "ports": {
    "porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
    "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"},
}}
# case -> (config, algo, good memory, stuck-bit memory)
CASES = {
    "dedicated": (DEDICATED, "march-c", "sram_1rw", "sram_1rw_stuck_bit"),
    "shared-bus": (SHARED_BUS, "march-c", "sram_1rw", "sram_1rw_stuck_bit"),
    "march-2rw": (TWO_RW, "march-2rw", "sram_2rw_dut", "sram_2rw_dut_stuck_bit"),
}


def _generate_and_synthesize(tmp_path: Path, case: str, *, defective: bool) -> Path:
    base, algo, good, bad = CASES[case]
    memory_name = bad if defective else good
    config = {**base, "memory_name": memory_name}
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / "out", algo=algo)
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


@pytest.mark.parametrize("case", CASES)
def test_synthesized_bist_passes_a_good_memory(tmp_path: Path, case: str) -> None:
    module_outdir = _generate_and_synthesize(tmp_path, case, defective=False)
    result = run_simulation(module_outdir)
    assert "test_mbist.test_clean passed" in result.stdout


@pytest.mark.parametrize("case", CASES)
def test_synthesized_bist_detects_a_stuck_bit(tmp_path: Path, case: str) -> None:
    module_outdir = _generate_and_synthesize(tmp_path, case, defective=True)
    with pytest.raises(SimulationError):
        run_simulation(module_outdir)
    log = (module_outdir / "simulate.log").read_text(encoding="utf-8")
    assert "test_mbist.test_clean failed" in log
    assert "MBIST reported fail in clean mode" in log
