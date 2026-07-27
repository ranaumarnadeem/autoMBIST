"""Standalone build/run script for test_newalgo_real_macro_selfrepair.py --
proves march-X and MATS+ self-repair run clean against REAL OpenRAM sky130
macros (the flow/newalgo/ LibreLane hardening targets), the same proof
test_mem_subsystem_mbist.py already did for march-C.

Regenerates selfrepair_x (march-X over sky130_sram_32b256w) and selfrepair_mp
(MATS+ over sky130_sram_32b512w) with read_latency: 0 -- REQUIRED for the real
OpenRAM macro's negedge-committed / posedge-decayed dout timing (the
generator's default of 1 is tuned for this project's toy fixtures and would
make either controller sample one cycle too late, spuriously flagging a
defect-free real macro unrepairable). See test_mem_subsystem_mbist.py's
docstring for the full story; flow/newalgo/README.md documents the same
caveat for these two designs.

Invoke exactly like this (from the repo root, via WSL and a cocotb Python):

    wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \
        PYTHONPATH=src python3 tests/hardware/run_newalgo_real_macro_selfrepair_tb.py"

Exits non-zero if any cocotb test fails or if either build fails.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

MULTIMEM_DIR = REPO_ROOT / "flow" / "multimem"
NEWALGO_DIR = REPO_ROOT / "flow" / "newalgo"

from autombist.generator import generate_from_config  # noqa: E402

HW_TEST_DIR = REPO_ROOT / "tests" / "hardware"
GEN_DIR = REPO_ROOT.parent / "newalgo_selfrepair_gen_repo"  # avoid polluting the repo tree

BASE_PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}

# (algo, memory_name, wrapper_name, addr_width, data_width, macro_source, subdir)
DESIGNS = [
    ("march-x", "sram_wrap_x", "selfrepair_x", 8, 32, MULTIMEM_DIR / "sky130_sram_32b256w.v", "gen_x"),
    ("mats-plus", "sram_wrap_mp", "selfrepair_mp", 9, 32, MULTIMEM_DIR / "sky130_sram_32b512w.v", "gen_mp"),
]


def _generate(algo: str, memory_name: str, wrapper_name: str, addr_width: int, data_width: int, subdir: str) -> Path:
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
    return generate_from_config(config_path, GEN_DIR / subdir, algo=algo)


def _count(results_xml: Path) -> tuple[int, int, int]:
    tree = ET.parse(results_xml)
    total = failures = errors = 0
    for testcase in tree.getroot().iter("testcase"):
        total += 1
        failures += len(testcase.findall("failure"))
        errors += len(testcase.findall("error"))
    return total, failures, errors


def main() -> int:
    from cocotb_tools.runner import get_runner

    grand_total = grand_failures = grand_errors = 0

    for algo, memory_name, wrapper_name, addr_width, data_width, macro_src, subdir in DESIGNS:
        wrapper = _generate(algo, memory_name, wrapper_name, addr_width, data_width, subdir)
        gen_dir = wrapper.parent
        adapter = NEWALGO_DIR / f"{memory_name}.sv"
        algo_dirname = algo.replace("-", "_")

        sources = [
            gen_dir / algo_dirname / f"{algo_dirname}_algo.sv",
            gen_dir / algo_dirname / f"{algo_dirname}_fsm.sv",
            gen_dir / algo_dirname / f"{algo_dirname}_top.sv",
            gen_dir / "onchip_row_repair_analyzer.sv",
            gen_dir / "onchip_selfrepair_ctrl.sv",
            gen_dir / "repair_remap_row.sv",
            wrapper,
            adapter,
            macro_src,
        ]
        missing = [str(p) for p in sources if not p.is_file()]
        if missing:
            print(f"[{wrapper_name}] Missing source file(s): {missing}", file=sys.stderr)
            return 1

        build_dir = HW_TEST_DIR / "sim_build" / f"newalgo_{wrapper_name}"
        runner = get_runner("icarus")
        runner.build(
            verilog_sources=sources,
            hdl_toplevel=wrapper_name,
            always=True,
            build_dir=build_dir,
            build_args=["-g2012"],
        )

        results_xml = runner.test(
            test_module="test_newalgo_real_macro_selfrepair",
            hdl_toplevel=wrapper_name,
            test_dir=HW_TEST_DIR,
            build_dir=build_dir,
            results_xml="results.xml",
        )

        total, failures, errors = _count(Path(results_xml))
        print(f"=== {wrapper_name} ({algo}): {total} tests, {failures} failures, {errors} errors ===")
        grand_total += total
        grand_failures += failures
        grand_errors += errors

    print(
        f"\n=== newalgo real-macro self-repair summary: {grand_total} tests, "
        f"{grand_failures} failures, {grand_errors} errors ==="
    )
    if grand_total == 0 or grand_failures or grand_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
