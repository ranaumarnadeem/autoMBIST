"""Gate-level proof that the Yosys-SYNTHESIZED MBIST collar still works, not
just the RTL: synthesize the wrapper + controller (memory kept as a blackbox
instance via the emitted <memory_name>_bbox.v stub, sources from
grade-controller's own controller_sources), swap that netlist in for the RTL
wrapper, and run the real cocotb tests against a real memory model.

A clean pass alone is vacuous here: before shared-bus's per-memory arrays
were made packed, Yosys deleted every shared-bus memory instance and, with
the read data undefined, optimized the compare path so the synthesized BIST
passed a good memory AND a broken one. So every case is also run against a
stuck-bit memory and must FAIL there. The march-2rw case also proves the
controller synthesizes at all (its per-port ports used to be unpacked
arrays, which Yosys rejects) and, since its defect sits only in port 1's
read path, that port 1's own compare survives synthesis.

The on-chip self-repair cases do the same for the repair logic: the analyzer,
the self-repair sequencer, the row and column remaps and the diagnosis log,
synthesized with the controller. Each repairable case must leave a clean
functional re-scan, which needs the synthesized remap to steer around the
defect; each has an unrepairable counterpart that the synthesized logic must
report as such.
"""
from __future__ import annotations

import shutil
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

from autombist.bira_input import fail_cells  # noqa: E402
from autombist.faultflow_flow import controller_sources, memory_instances  # noqa: E402
from autombist.generator import generate_from_config  # noqa: E402
from autombist.runner import SimulationError, run_simulation  # noqa: E402
from fixture_models import fixture_model
from synth_netlist import synthesize

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


def _synthesize_in_place(tmp_path: Path, config: dict, algo: str) -> Path:
    """Generate ``config``, synthesize the controller with the memory blackboxed
    and put the netlist where the RTL wrapper was. Returns the module directory."""
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / "out", algo=algo)
    module_outdir = wrapper.parent
    snapshot = yaml.safe_load((module_outdir / "config.yml").read_text(encoding="utf-8"))

    netlist = synthesize(controller_sources(module_outdir, snapshot), top=config["wrapper_module_name"],
                         out=module_outdir / "gate.v",
                         blackboxes=[module_outdir / f"{config['memory_name']}_bbox.v"])
    for inst in memory_instances(snapshot):
        assert f") {inst} (" in netlist, f"synthesis dropped memory instance {inst}"
    wrapper.write_text(netlist, encoding="utf-8")  # simulate the netlist, not the RTL
    return module_outdir


def _generate_and_synthesize(tmp_path: Path, case: str, *, defective: bool) -> Path:
    base, algo, good, bad = CASES[case]
    return _synthesize_in_place(tmp_path, {**base, "memory_name": bad if defective else good}, algo)


@pytest.mark.parametrize("case", CASES)
def test_synthesized_bist_passes_a_good_memory(tmp_path: Path, case: str) -> None:
    module_outdir = _generate_and_synthesize(tmp_path, case, defective=False)
    result = run_simulation(module_outdir, memory_model=fixture_model(module_outdir))
    assert "test_mbist.test_clean passed" in result.stdout


@pytest.mark.parametrize("case", CASES)
def test_synthesized_bist_detects_a_stuck_bit(tmp_path: Path, case: str) -> None:
    module_outdir = _generate_and_synthesize(tmp_path, case, defective=True)
    with pytest.raises(SimulationError):
        run_simulation(module_outdir, memory_model=fixture_model(module_outdir))
    log = (module_outdir / "simulate.log").read_text(encoding="utf-8")
    assert "test_mbist.test_clean failed" in log
    assert "MBIST reported fail in clean mode" in log


# --- on-chip self-repair and diagnosis ---------------------------------------------

HARDWARE = REPO_ROOT / "tests" / "hardware"
SPARE_PORTS = {**PORTS, "we": "web0"}
COL_PORTS = {**SPARE_PORTS, "spare_wen": "spare_wen0"}


def _spares(memory_name: str, redundancy: dict, *, addr_width: int = 2, ports: dict = SPARE_PORTS) -> dict:
    return {"memory_name": memory_name, "wrapper_module_name": f"{memory_name}_mbist",
            "addr_width": addr_width, "data_width": 4, "we_active_low": True, "ports": ports,
            "redundancy": {"num_spare_cols": 0, "onchip_selfrepair": True, **redundancy}}


def _row(rows: int, **extra) -> dict:
    return {"num_spare_rows": rows, **extra}


def _col(words_per_row: int = 1) -> dict:
    redundancy = {"num_spare_rows": 1, "num_spare_cols": 1, "onchip_col_repair": True}
    if words_per_row != 1:
        redundancy["words_per_row"] = words_per_row
    return redundancy


def _selfrepair(scenario: str) -> dict:
    return {"COCOTB_TEST_MODULES": "test_onchip_selfrepair", "SELFREPAIR_SCENARIO": scenario}


def _diagnosis(scenario: str, entries: int) -> dict:
    return {"COCOTB_TEST_MODULES": "test_onchip_diagnosis", "DIAGNOSIS_SCENARIO": scenario,
            "NUM_DIAGNOSIS_ENTRIES": str(entries)}


# case -> (config, make variables, the cells the re-scan must leave failing or
# None when the cocotb test checks them itself). The cocotb test asserts the
# self_repair_fail each scenario expects.
SELFREPAIR_CASES = {
    # row repair: one defect, two spare rows / two defective rows, one spare
    "row-repaired": (_spares("sram_spares_tiny", _row(2)), _selfrepair("repairable"), set()),
    "row-partial": (_spares("sram_spares_tiny_2defect", _row(1)), _selfrepair("partial"), None),
    # 2D analyzer: the recurring bit is promoted to the spare column
    "column-promoted": (_spares("sram_spares_col_tiny", _col(), ports=COL_PORTS),
                        {"COCOTB_TEST_MODULES": "test_onchip_col_repair", "COL_REPAIR_SCENARIO": "promote"},
                        None),
    # a bitline defect on a 4-words-per-row memory takes one physical spare
    # column; computed at one word per row it is unrepairable (shared spare cell)
    "muxed-column-repaired": (_spares("sram_spares_muxed_col", _col(4), addr_width=3, ports=COL_PORTS),
                              _selfrepair("repairable"), set()),
    "muxed-column-per-bit": (_spares("sram_spares_muxed_col", _col(1), addr_width=3, ports=COL_PORTS),
                             _selfrepair("unrepairable"), None),
    # the diagnosis log sees both defects past a one-spare repair budget, and
    # flags overflow when it has one entry for two defects
    "diagnosis-past-repair": (_spares("sram_spares_tiny_2defect",
                                      _row(1, onchip_diagnosis=True, num_diagnosis_entries=2)),
                              _diagnosis("beyond_repair_budget", 2), None),
    "diagnosis-overflow": (_spares("sram_spares_tiny_2defect",
                                   _row(2, onchip_diagnosis=True, num_diagnosis_entries=1)),
                           _diagnosis("overflow", 1), None),
}


@pytest.mark.parametrize("case", SELFREPAIR_CASES)
def test_synthesized_selfrepair(tmp_path: Path, case: str) -> None:
    config, make_vars, remaining = SELFREPAIR_CASES[case]
    module_outdir = _synthesize_in_place(tmp_path, config, "march-c")

    result = run_simulation(module_outdir, memory_model=[HARDWARE / f"{config['memory_name']}.v"],
                            extra_make_vars={**make_vars, "ADDR_WIDTH": str(config["addr_width"]),
                                             "DATA_WIDTH": str(config["data_width"])})

    assert result.returncode == 0, result.stdout
    if remaining is not None:
        assert fail_cells(result.report) == remaining
