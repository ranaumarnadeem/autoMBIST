"""Standalone build/run script for the SoC self-repair testbench
(test_soc_selfrepair.py): a real RV32I CPU (PicoRV32) running a hand-
assembled test program against two self-repair-wrapped, defect-injected
memories (flow/soc/soc_top.sv).

Regenerates both self-repair wrappers via generate_from_config() (same API
test_onchip_selfrepair_e2e.py and run_mem_subsystem_selfrepair_tb.py use),
takes ONE copy of the shared march_c/onchip_*/repair_remap_row RTL (identical
across both generated wrappers), and compiles everything together with
flow/soc/soc_top.sv + the vendored picorv32 core via Icarus + cocotb. The
firmware image itself is backdoor-loaded by the cocotb test at the right
simulated moment (see test_soc_selfrepair.py), not built here.

Invoke exactly like this (from the repo root, via WSL and a cocotb-capable
Python):

    wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \
        PYTHONPATH=src python3 tests/hardware/run_soc_selfrepair_tb.py"

Exits non-zero if any cocotb test fails or if the build fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

SOC_DIR = REPO_ROOT / "flow" / "soc"

from autombist.generator import generate_from_config  # noqa: E402

HW_TEST_DIR = REPO_ROOT / "tests" / "hardware"
GEN_DIR = REPO_ROOT.parent / "soc_selfrepair_gen_repo"  # avoid polluting the repo tree
SIM_BUILD_DIR = HW_TEST_DIR / "sim_build" / "soc_selfrepair"

BASE_PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}


def _generate(memory_name: str, wrapper_name: str, addr_width: int, data_width: int, subdir: str) -> Path:
    config = {
        "memory_name": memory_name,
        "wrapper_module_name": wrapper_name,
        "addr_width": addr_width,
        "data_width": data_width,
        "we_active_low": True,
        "ports": BASE_PORTS,
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0, "onchip_selfrepair": True},
    }
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    config_path = GEN_DIR / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, GEN_DIR / subdir, algo="march-c")


def main() -> int:
    from cocotb_tools.runner import get_runner

    wrapper_instr = _generate("sram_spares_soc_instr", "selfrepair_soc_instr", 6, 32, "gen_instr")
    wrapper_data = _generate("sram_spares_soc_data", "selfrepair_soc_data", 5, 32, "gen_data")

    shared_dir = wrapper_instr.parent  # one copy of march_c/ + onchip_*.sv + repair_remap_row.sv

    sources = [
        SOC_DIR / "vendor" / "picorv32" / "picorv32.v",
        shared_dir / "march_c" / "march_c_algo.sv",
        shared_dir / "march_c" / "march_c_fsm.sv",
        shared_dir / "march_c" / "march_c_top.sv",
        shared_dir / "onchip_row_repair_analyzer.sv",
        shared_dir / "onchip_selfrepair_ctrl.sv",
        shared_dir / "repair_remap_row.sv",
        wrapper_instr,
        wrapper_data,
        SOC_DIR / "sram_spares_soc_instr.v",
        SOC_DIR / "sram_spares_soc_data.v",
        SOC_DIR / "soc_top.sv",
    ]
    missing = [str(p) for p in sources if not p.is_file()]
    if missing:
        print(f"Missing source file(s): {missing}", file=sys.stderr)
        return 1

    runner = get_runner("icarus")
    runner.build(
        verilog_sources=sources,
        hdl_toplevel="soc_top",
        always=True,
        build_dir=SIM_BUILD_DIR,
        build_args=["-g2012"],
    )

    results_xml = runner.test(
        test_module="test_soc_selfrepair",
        hdl_toplevel="soc_top",
        test_dir=HW_TEST_DIR,
        build_dir=SIM_BUILD_DIR,
        results_xml="results.xml",
    )

    import xml.etree.ElementTree as ET

    tree = ET.parse(results_xml)
    root = tree.getroot()
    total_tests = 0
    total_failures = 0
    total_errors = 0
    for testcase in root.iter("testcase"):
        total_tests += 1
        total_failures += len(testcase.findall("failure"))
        total_errors += len(testcase.findall("error"))

    print(
        f"\n=== soc_selfrepair testbench summary: {total_tests} tests, "
        f"{total_failures} failures, {total_errors} errors ==="
    )

    if total_tests == 0 or total_failures or total_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
