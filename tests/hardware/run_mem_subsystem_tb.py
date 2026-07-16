"""Standalone build/run script for the flow/multimem mem_subsystem testbench.

Compiles the three OpenRAM sky130 behavioral macro models plus
flow/multimem/mem_subsystem.sv with Icarus via cocotb's Python Runner API and
runs tests/hardware/test_mem_subsystem.py against the subsystem directly (the
DUT's bus ports are driven straight from cocotb; no tb_top shim is needed).

NOTE: the synthesis blackboxes (flow/multimem/sky130_srams_bb.v) must NOT be
compiled here -- their module names deliberately collide with the behavioral
models.

Invoke exactly like this (from the repo root, via WSL and the cocotb venv):

    wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \
        PYTHONPATH=src ~/cocotb/bin/python tests/hardware/run_mem_subsystem_tb.py"

Exits non-zero if any cocotb test fails or if the build fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MULTIMEM_DIR = REPO_ROOT / "flow" / "multimem"
HW_TEST_DIR = REPO_ROOT / "tests" / "hardware"
SIM_BUILD_DIR = HW_TEST_DIR / "sim_build" / "mem_subsystem"


def main() -> int:
    from cocotb_tools.runner import get_runner

    sources = [
        MULTIMEM_DIR / "sky130_sram_32b256w.v",
        MULTIMEM_DIR / "sky130_sram_32b512w.v",
        MULTIMEM_DIR / "sky130_sram_8b1024w.v",
        MULTIMEM_DIR / "mem_subsystem.sv",
    ]
    missing = [str(p) for p in sources if not p.is_file()]
    if missing:
        print(f"Missing source file(s): {missing}", file=sys.stderr)
        return 1

    runner = get_runner("icarus")
    runner.build(
        verilog_sources=sources,
        hdl_toplevel="mem_subsystem",
        always=True,
        build_dir=SIM_BUILD_DIR,
        build_args=["-g2012"],
    )

    results_xml = runner.test(
        test_module="test_mem_subsystem",
        hdl_toplevel="mem_subsystem",
        test_dir=HW_TEST_DIR,
        build_dir=SIM_BUILD_DIR,
        results_xml="results.xml",
    )

    # get_runner().test() does not raise for a failed testcase; parse the
    # JUnit results.xml (counting <testcase>/<failure>/<error> elements --
    # cocotb 2.0 leaves the testsuite counter attributes unpopulated).
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
        f"\n=== mem_subsystem testbench summary: {total_tests} tests, "
        f"{total_failures} failures, {total_errors} errors ==="
    )

    if total_tests == 0 or total_failures or total_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
