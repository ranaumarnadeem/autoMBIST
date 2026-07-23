"""Standalone build/run script for the self-repair functional testbench of
flow/multimem/mbist/mem_subsystem_mbist.sv -- the actual hardened 3-memory
subsystem, wrapping the REAL OpenRAM sky130 macro adapters (sram_wrap_a/b/c
over the behavioral simulation views of the exact macros LibreLane hardens).

Regenerates selfrepair_a/selfrepair_b/selfrepair_c with the EXACT config
documented in flow/multimem/mbist/README.md's "Reproduce" section -- same
module names, same redundancy config -- PLUS read_latency: 0, which the real
OpenRAM macro's negedge-committed / posedge-decayed dout timing requires (the
generator default of 1 is tuned for this project's toy fixtures and would make
march-C sample one cycle too late, spuriously flagging a defect-free real macro
unrepairable). See test_mem_subsystem_mbist.py's docstring for the full story.

Invoke exactly like this (from the repo root, via WSL and a cocotb Python):

    wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \
        PYTHONPATH=src python3 tests/hardware/run_mem_subsystem_mbist_tb.py"

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

MULTIMEM_DIR = REPO_ROOT / "flow" / "multimem"
MBIST_DIR = MULTIMEM_DIR / "mbist"

from autombist.generator import generate_from_config  # noqa: E402

HW_TEST_DIR = REPO_ROOT / "tests" / "hardware"
GEN_DIR = REPO_ROOT.parent / "mem_subsystem_mbist_gen_repo"  # avoid polluting the repo tree
SIM_BUILD_DIR = HW_TEST_DIR / "sim_build" / "mem_subsystem_mbist"

BASE_PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}

# (memory_name, wrapper_name, addr_width, subdir) -- matches mem_subsystem_mbist.sv.
WRAPPERS = [
    ("sram_wrap_a", "selfrepair_a", 8, "gen_a"),
    ("sram_wrap_b", "selfrepair_b", 9, "gen_b"),
    ("sram_wrap_c", "selfrepair_c", 10, "gen_c"),
]


def _generate(memory_name: str, wrapper_name: str, addr_width: int, data_width: int, subdir: str) -> Path:
    config = {
        "memory_name": memory_name,
        "wrapper_module_name": wrapper_name,
        "addr_width": addr_width,
        "data_width": data_width,
        "we_active_low": True,
        "ports": BASE_PORTS,
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0, "onchip_selfrepair": True},
        "read_latency": 0,
    }
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    config_path = GEN_DIR / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, GEN_DIR / subdir, algo="march-c")


def main() -> int:
    from cocotb_tools.runner import get_runner

    # slot c is the 8-bit macro; a/b are 32-bit.
    wrapper_a = _generate("sram_wrap_a", "selfrepair_a", 8, 32, "gen_a")
    wrapper_b = _generate("sram_wrap_b", "selfrepair_b", 9, 32, "gen_b")
    wrapper_c = _generate("sram_wrap_c", "selfrepair_c", 10, 8, "gen_c")

    shared_dir = wrapper_a.parent  # one copy of march_c/ + onchip_*.sv + repair_remap_row.sv

    sources = [
        shared_dir / "march_c" / "march_c_algo.sv",
        shared_dir / "march_c" / "march_c_fsm.sv",
        shared_dir / "march_c" / "march_c_top.sv",
        shared_dir / "onchip_row_repair_analyzer.sv",
        shared_dir / "onchip_selfrepair_ctrl.sv",
        shared_dir / "repair_remap_row.sv",
        wrapper_a,
        wrapper_b,
        wrapper_c,
        MBIST_DIR / "sram_wrap_a.sv",
        MBIST_DIR / "sram_wrap_b.sv",
        MBIST_DIR / "sram_wrap_c.sv",
        MULTIMEM_DIR / "sky130_sram_32b256w.v",
        MULTIMEM_DIR / "sky130_sram_32b512w.v",
        MULTIMEM_DIR / "sky130_sram_8b1024w.v",
        MBIST_DIR / "mem_subsystem_mbist.sv",
    ]
    missing = [str(p) for p in sources if not p.is_file()]
    if missing:
        print(f"Missing source file(s): {missing}", file=sys.stderr)
        return 1

    runner = get_runner("icarus")
    runner.build(
        verilog_sources=sources,
        hdl_toplevel="mem_subsystem_mbist",
        always=True,
        build_dir=SIM_BUILD_DIR,
        build_args=["-g2012"],
    )

    results_xml = runner.test(
        test_module="test_mem_subsystem_mbist",
        hdl_toplevel="mem_subsystem_mbist",
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
        f"\n=== mem_subsystem_mbist testbench summary: {total_tests} tests, "
        f"{total_failures} failures, {total_errors} errors ==="
    )

    if total_tests == 0 or total_failures or total_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
