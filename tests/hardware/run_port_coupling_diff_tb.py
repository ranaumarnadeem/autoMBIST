"""Standalone build/run script for the port-coupling differential-proof
testbench.

Generates the REAL saboteur module via generate_from_config(fault_type=
"port-coupling", algo="march-1r1w") -- the exact same code path a real
`autombist generate --test --fault-type port-coupling` run uses -- then
compiles it directly against port_coupling_diff_tb_top.sv using cocotb's
Python Runner API (cocotb_tools.runner), so no hand-written Makefile is
required. Mirrors tests/hardware/run_march_1r1w_tb.py's structure.

Invoke exactly like this (from the repo root, via WSL and the project venv):

    wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \\
        PYTHONPATH=src ~/cocotb/bin/python tests/hardware/run_port_coupling_diff_tb.py"

Exits non-zero if any cocotb test fails or if the build fails.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

HW_TEST_DIR = REPO_ROOT / "tests" / "hardware"
SIM_BUILD_DIR = HW_TEST_DIR / "sim_build" / "port_coupling_diff"
GEN_WORKDIR = HW_TEST_DIR / "sim_build" / "port_coupling_diff_gen"

# Must match test_port_coupling_diff.py's PC_FAULT_ADDR/PC_FAULT_BIT
# defaults so the fault mask generated here actually covers the site the
# cocotb test drives.
FAULT_ADDR = 5
FAULT_BIT = 2
ADDR_WIDTH = 6
DATA_WIDTH = 8

CONFIG = {
    "memory_name": "sram_1r1w_dut",
    "wrapper_module_name": "sram_1r1w_dut_mbist",
    "addr_width": ADDR_WIDTH,
    "data_width": DATA_WIDTH,
    "we_active_low": True,
    "ports": {
        "rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
        "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"},
    },
}


def _generate_saboteur() -> Path:
    """Write a pc_mask.hex with exactly one bit set (FAULT_ADDR/FAULT_BIT)
    and render the saboteur against it -- via the same generate_from_config
    + fault_gen path a real run uses, just with a hand-picked deterministic
    site instead of a random one, so the cocotb test's hard-coded address/bit
    match exactly."""
    import yaml

    from autombist.fault_gen import FaultType, write_fault_files
    from autombist.generator import generate_from_config

    if GEN_WORKDIR.exists():
        shutil.rmtree(GEN_WORKDIR)
    GEN_WORKDIR.mkdir(parents=True, exist_ok=True)

    config_path = GEN_WORKDIR / "config.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    outdir = GEN_WORKDIR / "out"

    wrapper_path = generate_from_config(
        config_path, outdir, use_saboteur=True, faults=0, fault_seed=None,
        fault_type="port-coupling", algo="march-1r1w",
    )
    module_outdir = wrapper_path.parent

    # Overwrite the (empty, faults=0) pc_mask.hex with a deterministic single
    # -bit fault at (FAULT_ADDR, FAULT_BIT), then regenerate ONLY the
    # saboteur so it $readmemh's the deterministic mask instead.
    depth = 1 << ADDR_WIDTH
    mask1_words = [0] * depth
    mask1_words[FAULT_ADDR] = 1 << FAULT_BIT
    fault_dir = module_outdir / "faults"
    write_fault_files(
        outdir=fault_dir,
        data_width=DATA_WIDTH,
        fault_type=FaultType.PORT_COUPLING,
        mask1_words=mask1_words,
        mask2_words=mask1_words,
    )
    # Re-render the saboteur so it picks up the (already-referenced-by-path)
    # updated pc_mask.hex -- the saboteur $readmemh's the file at sim start,
    # so simply having overwritten its contents on disk is already enough;
    # no re-render is actually required, but do it anyway for clarity/safety
    # against any future caching change.
    from autombist.generator import render_saboteur

    render_config = yaml.safe_load((module_outdir / "config.yml").read_text(encoding="utf-8"))
    saboteur_text = render_saboteur(render_config)
    saboteur_path = module_outdir / "sram_1r1w_dut_saboteur.v"
    saboteur_path.write_text(saboteur_text, encoding="utf-8")

    return module_outdir


def main() -> int:
    from cocotb_tools.runner import get_runner

    module_outdir = _generate_saboteur()
    saboteur_path = module_outdir / "sram_1r1w_dut_saboteur.v"
    dut_path = REPO_ROOT / "tests" / "hardware" / "sram_1r1w_dut.v"

    sources = [
        dut_path,
        saboteur_path,
        HW_TEST_DIR / "port_coupling_diff_tb_top.sv",
    ]
    missing = [str(p) for p in sources if not p.is_file()]
    if missing:
        print(f"Missing source file(s): {missing}", file=sys.stderr)
        return 1

    runner = get_runner("icarus")
    runner.build(
        verilog_sources=sources,
        hdl_toplevel="port_coupling_diff_tb_top",
        always=True,
        build_dir=SIM_BUILD_DIR,
        build_args=["-g2012"],
    )

    results_xml = runner.test(
        test_module="test_port_coupling_diff",
        hdl_toplevel="port_coupling_diff_tb_top",
        test_dir=HW_TEST_DIR,
        build_dir=SIM_BUILD_DIR,
        results_xml="results.xml",
        extra_env={
            "PC_FAULT_ADDR": str(FAULT_ADDR),
            "PC_FAULT_BIT": str(FAULT_BIT),
        },
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

    print(f"\n=== port_coupling_diff testbench summary: {total_tests} tests, "
          f"{total_failures} failures, {total_errors} errors ===")

    if total_tests == 0 or total_failures or total_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
