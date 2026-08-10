"""Standalone build/run script for the algorithm-module probe testbench
(test_algo_modules.py).

This does NOT go through generate_from_config() or the Jinja wrapper
templates, and does NOT use tests/hardware/Makefile's ALGO_TEST=1 branch
(dead: nothing in the repo ever sets ALGO_TEST=1, which is exactly why this
module went uncollected -- see docs/sweep-2026-08-plan.md's test-quality
section). Instead it compiles algo_module_probe.sv directly against each
algorithm's own STATIC RTL under rtl/<algo>/ (the same source location
run_march_1r1w_tb.py and run_march_2rw_tb.py already use for their algo
families) using cocotb's Python Runner API, so no Makefile involvement or
generated wrapper is needed at all. Runs the probe against BOTH algorithms
test_algo_modules.py knows how to check (march-c, march-raw) -- each needs
its own build, since algo_module_probe.sv picks the instantiated module via
a compile-time `ifdef ALGO_MARCH_RAW`, not a runtime switch.

Invoke exactly like this (from the repo root, via WSL and a cocotb-capable
Python):

    wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \
        PYTHONPATH=src python3 tests/hardware/run_algo_modules_tb.py"

Exits non-zero if any cocotb test fails or if either build fails.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RTL_DIR = REPO_ROOT / "rtl"
HW_TEST_DIR = REPO_ROOT / "tests" / "hardware"

# (algo, algo_dir, extra build_args)
VARIANTS = [
    ("march-c", "march_c", []),
    ("march-raw", "march_raw", ["-DALGO_MARCH_RAW"]),
]


def _count(results_xml: Path) -> tuple[int, int, int]:
    tree = ET.parse(results_xml)
    root = tree.getroot()
    total_tests = total_failures = total_errors = 0
    for testcase in root.iter("testcase"):
        total_tests += 1
        total_failures += len(testcase.findall("failure"))
        total_errors += len(testcase.findall("error"))
    return total_tests, total_failures, total_errors


def main() -> int:
    from cocotb_tools.runner import get_runner

    grand_total = grand_failures = grand_errors = 0

    for algo, algo_dir, extra_build_args in VARIANTS:
        sources = [
            HW_TEST_DIR / "algo_module_probe.sv",
            RTL_DIR / algo_dir / f"{algo_dir}_algo.sv",
        ]
        missing = [str(p) for p in sources if not p.is_file()]
        if missing:
            print(f"Missing source file(s) for {algo}: {missing}", file=sys.stderr)
            return 1

        build_dir = HW_TEST_DIR / "sim_build" / f"algo_modules_{algo_dir}"
        runner = get_runner("icarus")
        runner.build(
            verilog_sources=sources,
            hdl_toplevel="algo_module_probe",
            always=True,
            build_dir=build_dir,
            build_args=["-g2012", *extra_build_args],
        )

        results_xml = runner.test(
            test_module="test_algo_modules",
            hdl_toplevel="algo_module_probe",
            test_dir=HW_TEST_DIR,
            build_dir=build_dir,
            results_xml="results.xml",
            extra_env={"ALGO": algo},
        )

        total, failures, errors = _count(results_xml)
        print(f"\n=== algo_modules ({algo}) summary: {total} tests, "
              f"{failures} failures, {errors} errors ===")
        grand_total += total
        grand_failures += failures
        grand_errors += errors

    print(f"\n=== algo_modules combined summary: {grand_total} tests, "
          f"{grand_failures} failures, {grand_errors} errors ===")

    if grand_total == 0 or grand_failures or grand_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
