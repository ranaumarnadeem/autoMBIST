"""Area and test-time limits for the golden cases (tests/golden/cases.yml), the
way OpenROAD checks each flow's metrics against limits: for every case that
builds silicon (all but the saboteur's simulation-only builds), synthesize the
generated wrapper with Yosys, run its BIST and its self-repair with Icarus, and
compare with tests/golden/metrics.yml.

- cells: Yosys generic cells of the flattened wrapper, the memory a blackbox.
  Within CELL_TOLERANCE of the recorded count, in both directions: growth is
  area, and a drop can mean logic was pruned because something got
  disconnected. The tolerance absorbs optimizer drift across Yosys versions
  (0.61 and 0.62 agree exactly on every case).
- flops: exact. They are the design's state, which synthesis keeps unless it is
  constant or unused.
- latches: none, always.
- bist_cycles: the generated testbench's BIST length, exact, and within
  testbench.bist_cycle_bound, which the testbench's timeout and the PDL's run
  loop rely on.
- selfrepair_cycles (on-chip self-repair cases): self_repair_start held to
  self_repair_done on a good memory, exact: the analyze and verify marches, plus
  the deferred analyzer's second analyze pass.

After an intended change, rerun with AUTOMBIST_UPDATE_GOLDEN=1 to rewrite
metrics.yml, and review its diff.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("yosys") is None,
    reason="needs Icarus Verilog + Yosys on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import generate_from_config  # noqa: E402
from autombist.testbench import bist_cycle_bound, parse_wrapper_ports, rtl_sources  # noqa: E402

GOLDEN = REPO_ROOT / "tests" / "golden"
METRICS = GOLDEN / "metrics.yml"
CASES: dict[str, dict] = yaml.safe_load((GOLDEN / "cases.yml").read_text(encoding="utf-8"))
SILICON_CASES = sorted(case for case, spec in CASES.items() if not spec["generate"].get("use_saboteur"))
UPDATE = os.environ.get("AUTOMBIST_UPDATE_GOLDEN") == "1"
REGENERATE = "AUTOMBIST_UPDATE_GOLDEN=1 python -m pytest tests/integration/test_golden_metrics.py"
CELL_TOLERANCE = 0.05

METRICS_HEADER = """\
# Area and test time of the golden cases, checked by
# tests/integration/test_golden_metrics.py (see its docstring for each metric and
# its tolerance). Rewritten by:
#   AUTOMBIST_UPDATE_GOLDEN=1 python -m pytest tests/integration/test_golden_metrics.py
"""

BIST_RESULT = re.compile(r"MBIST RESULT: (PASS|FAIL|TIMEOUT) -- .*?(\d+) clk cycles")
SELFREPAIR_RESULT = re.compile(r"SELF-REPAIR RESULT: (PASS|FAIL|TIMEOUT) -- (\d+) clk cycles")

# Holds self_repair_start until self_repair_done; every other input idles as in
# the generated BIST testbench (func_csb high, the rest low).
SELFREPAIR_TB = """\
`timescale 1ns/1ps
module tb_selfrepair;
    localparam integer ADDR_WIDTH = {addr_width};
    localparam integer DATA_WIDTH = {data_width};
    logic clk = 1'b0;
    logic rst_n = 1'b0;
{declarations}
    {top} dut (
{connections}
    );
    always #5 clk = ~clk;
    integer cycles;
    initial begin
        repeat (4) @(negedge clk);
        rst_n = 1'b1;
        repeat (2) @(negedge clk);
        self_repair_start = 1'b1;
        cycles = 0;
        while (self_repair_done !== 1'b1 && cycles < {max_cycles}) begin
            @(negedge clk);
            cycles = cycles + 1;
        end
        if (self_repair_done !== 1'b1)
            $fatal(1, "SELF-REPAIR RESULT: TIMEOUT -- %0d clk cycles", cycles);
        if (self_repair_fail !== 1'b0)
            $fatal(1, "SELF-REPAIR RESULT: FAIL -- %0d clk cycles", cycles);
        $display("SELF-REPAIR RESULT: PASS -- %0d clk cycles", cycles);
        $finish;
    end
endmodule
"""


def _generate(case: str, tmp_path: Path) -> tuple[Path, dict]:
    """generate's wrapper file for ``case`` and its config snapshot."""
    spec = CASES[case]
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(spec["config"], sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / "out", **spec["generate"])
    snapshot = yaml.safe_load((wrapper.parent / "config.yml").read_text(encoding="utf-8"))
    return wrapper, snapshot


def _synthesize(module_dir: Path, snapshot: dict) -> dict[str, int]:
    top = snapshot["wrapper_module_name"]
    sources = [module_dir / s for s in rtl_sources(snapshot, module_dir)]
    sources.append(module_dir / f"{snapshot['memory_name']}_bbox.v")
    stat = module_dir / "stat.json"
    script = module_dir / "synth.ys"
    script.write_text("\n".join([
        "read_verilog -sv " + " ".join(s.as_posix() for s in sources),
        f"hierarchy -check -top {top}",
        f"synth -flatten -top {top}",
        f"tee -q -o {stat.as_posix()} stat -json",
    ]) + "\n", encoding="utf-8")
    run = subprocess.run(["yosys", "-q", "-s", str(script)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    modules = json.loads(stat.read_text(encoding="utf-8"))["modules"]
    module = modules.get(f"\\{top}") or modules[top]
    by_type: dict[str, int] = module.get("num_cells_by_type", {})
    return {
        "cells": int(module["num_cells"]),
        "flops": sum(n for kind, n in by_type.items() if "DFF" in kind.upper()),
        "latches": sum(n for kind, n in by_type.items() if "LATCH" in kind.upper()),
    }


def _bist_cycles(module_dir: Path) -> int:
    run = subprocess.run(["bash", str(module_dir / "tb" / "run_tb.sh")], capture_output=True, text=True)
    result = BIST_RESULT.search(run.stdout + run.stderr)
    assert run.returncode == 0 and result and result.group(1) == "PASS", run.stdout + run.stderr
    return int(result.group(2))


def _selfrepair_cycles(wrapper: Path, snapshot: dict) -> int:
    module_dir = wrapper.parent
    top = snapshot["wrapper_module_name"]
    declarations, connections = [], []
    for port in parse_wrapper_ports(wrapper.read_text(encoding="utf-8"), top):
        if port.direction == "input" and port.name not in ("clk", "rst_n"):
            idle = "'1" if port.name == "func_csb" else "'0"
            declarations.append(f"    logic {port.range}{port.name} = {idle};")
        elif port.direction == "output":
            declarations.append(f"    wire  {port.range}{port.name};")
        connections.append(f"        .{port.name}({port.name})")
    tb = module_dir / "tb_selfrepair.sv"
    tb.write_text(SELFREPAIR_TB.format(
        addr_width=snapshot["addr_width"], data_width=snapshot["data_width"], top=top,
        declarations="\n".join(declarations), connections=",\n".join(connections),
        max_cycles=4 * bist_cycle_bound(snapshot),
    ), encoding="utf-8")
    sources = [str(module_dir / s) for s in rtl_sources(snapshot, module_dir)]
    sources.append(str(module_dir / "tb" / f"{snapshot['memory_name']}_model.v"))
    vvp = module_dir / "tb_selfrepair.vvp"
    build = subprocess.run(["iverilog", "-g2012", "-s", "tb_selfrepair", "-o", str(vvp), str(tb), *sources],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    run = subprocess.run(["vvp", "-n", str(vvp)], capture_output=True, text=True, cwd=module_dir)
    result = SELFREPAIR_RESULT.search(run.stdout + run.stderr)
    assert run.returncode == 0 and result and result.group(1) == "PASS", run.stdout + run.stderr
    return int(result.group(2))


def _measure(case: str, tmp_path: Path) -> tuple[dict[str, int], dict]:
    wrapper, snapshot = _generate(case, tmp_path)
    metrics = _synthesize(wrapper.parent, snapshot)
    metrics["bist_cycles"] = _bist_cycles(wrapper.parent)
    if (snapshot.get("redundancy") or {}).get("onchip_selfrepair"):
        metrics["selfrepair_cycles"] = _selfrepair_cycles(wrapper, snapshot)
    return metrics, snapshot


def _recorded() -> dict[str, dict[str, int]]:
    if not METRICS.exists():
        return {}
    return yaml.safe_load(METRICS.read_text(encoding="utf-8")) or {}


def _record(entries: dict[str, dict[str, int]]) -> None:
    METRICS.write_text(METRICS_HEADER + yaml.safe_dump(entries, sort_keys=True), encoding="utf-8", newline="\n")


def _deviations(actual: dict[str, int], recorded: dict[str, int]) -> list[str]:
    problems = []
    for name in sorted(actual.keys() | recorded.keys()):
        if name == "latches":
            continue
        got, want = actual.get(name), recorded.get(name)
        if got is None or want is None:
            problems.append(f"{name}: measured {got}, recorded {want}")
        elif name == "cells":
            if abs(got - want) > CELL_TOLERANCE * want:
                problems.append(f"cells: {got}, recorded {want} ({(got - want) / want:+.1%}, "
                                f"tolerance {CELL_TOLERANCE:.0%})")
        elif got != want:
            problems.append(f"{name}: {got}, recorded {want} ({got - want:+d})")
    return problems


@pytest.mark.parametrize("case", SILICON_CASES)
def test_area_and_test_time_match_the_recorded_metrics(case: str, tmp_path: Path) -> None:
    metrics, snapshot = _measure(case, tmp_path)

    assert metrics["latches"] == 0, f"{case}: synthesis inferred {metrics['latches']} latches"
    bound = bist_cycle_bound(snapshot)
    assert metrics["bist_cycles"] <= bound, (
        f"{case}: the BIST takes {metrics['bist_cycles']} cycles, more than testbench.bist_cycle_bound's "
        f"{bound}, which the testbench's timeout and the PDL's run loop rely on"
    )

    if UPDATE:
        entries = _recorded()
        entries[case] = {k: v for k, v in metrics.items() if k != "latches"}
        _record(entries)
    recorded = _recorded().get(case)
    assert recorded is not None, f"no recorded metrics for {case!r}; record them with {REGENERATE}"
    problems = _deviations(metrics, recorded)
    assert not problems, (
        f"{case}: area or test time moved from tests/golden/metrics.yml:\n  " + "\n  ".join(problems)
        + f"\nIf the change is intended, rerun with {REGENERATE} and review the diff."
    )


def test_the_recorded_metrics_name_exactly_the_silicon_cases() -> None:
    entries = _recorded()
    stale = sorted(entries.keys() - set(SILICON_CASES))
    if UPDATE and stale:
        _record({k: v for k, v in entries.items() if k not in stale})
        entries, stale = _recorded(), []
    missing = sorted(set(SILICON_CASES) - entries.keys())
    assert not stale, f"tests/golden/metrics.yml records cases that are gone or simulation-only: {stale}"
    assert not missing, f"tests/golden/metrics.yml has no metrics for {missing}; record them with {REGENERATE}"
