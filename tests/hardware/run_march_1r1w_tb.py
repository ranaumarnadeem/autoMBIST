"""Standalone build/run script for the 1R1W march algorithm testbench.

This does NOT go through generate_from_config() or the Jinja wrapper
templates -- it compiles the march_1r1w RTL family directly against
march_1r1w_tb_top.sv + sram_model_1r1w.sv using cocotb's Python Runner API
(cocotb_tools.runner), so no hand-written Makefile is required.

Invoke exactly like this (from the repo root, via WSL and the project venv):

    wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \
        PYTHONPATH=src ~/cocotb/bin/python tests/hardware/run_march_1r1w_tb.py"

Exits non-zero if any cocotb test fails or if the build fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RTL_DIR = REPO_ROOT / "rtl"
HW_TEST_DIR = REPO_ROOT / "tests" / "hardware"
SIM_BUILD_DIR = HW_TEST_DIR / "sim_build" / "march_1r1w"


def main() -> int:
    from cocotb_tools.runner import get_runner

    sources = [
        RTL_DIR / "sram_model_1r1w.sv",
        RTL_DIR / "march_1r1w" / "march_1r1w_algo.sv",
        RTL_DIR / "march_1r1w" / "march_1r1w_fsm.sv",
        RTL_DIR / "march_1r1w" / "march_1r1w_top.sv",
        HW_TEST_DIR / "march_1r1w_tb_top.sv",
    ]
    missing = [str(p) for p in sources if not p.is_file()]
    if missing:
        print(f"Missing source file(s): {missing}", file=sys.stderr)
        return 1

    runner = get_runner("icarus")
    runner.build(
        verilog_sources=sources,
        hdl_toplevel="march_1r1w_tb_top",
        always=True,
        build_dir=SIM_BUILD_DIR,
        build_args=["-g2012"],
    )

    results_xml = runner.test(
        test_module="test_march_1r1w",
        hdl_toplevel="march_1r1w_tb_top",
        test_dir=HW_TEST_DIR,
        build_dir=SIM_BUILD_DIR,
        results_xml="results.xml",
    )

    # Fail the process (and thus the WSL invocation) on any cocotb test
    # failure -- get_runner().test() does not raise on its own for a failed
    # testcase, it only raises for a build/harness error, so we must parse
    # the JUnit-style results.xml to detect real test failures. cocotb 2.0's
    # results.xml does not populate testsuite-level tests/failures/errors
    # counter attributes, so count <testcase> elements and look for
    # <failure>/<error> child elements directly instead.
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

    print(f"\n=== march_1r1w testbench summary: {total_tests} tests, "
          f"{total_failures} failures, {total_errors} errors ===")

    if total_tests == 0 or total_failures or total_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
