"""Line and branch coverage of the rtl/ sources, measured with Verilator.

The Python coverage gate says nothing about the RTL. This builds every golden
case that makes silicon (tests/golden/cases.yml, all but the saboteur's
simulation-only builds), plus read-latency 0 and 2 variants of each algorithm's
plain case (only march-c has golden cases at other latencies), once each with
``verilator --coverage-line``, against the fault-injectable memory model
(memory_model.render_memory_model with stuck-at masks, rewritten between runs),
and runs:

- every unit: the BIST on a good memory, twice (bist_start held for two cycles
  after bist_done, then dropped), and the BIST on a memory with one stuck cell;
- on-chip self-repair units: self-repair on a good memory, one stuck cell, two
  stuck bits in one row, a stuck column (2D), a faulty spare row and a faulty
  spare column (spare retry), more faulty rows than spares and log entries
  (unrepairable; diagnosis overflow), and a repair_load from fuse values
  (persistence).

Every run's verdict is checked, which makes Verilator a second simulator next to
Icarus: a good memory passes, a stuck cell fails the BIST, the repairable cases
repair and the others do not, and a golden case's clean BIST and self-repair take
the clock cycles tests/golden/metrics.yml records (measured on Icarus).

The coverage points are merged over every run and instance and compared, per
rtl/ file, with tests/golden/rtl_coverage.yml, which records each file's point
count and its uncovered points. A point that loses coverage, or new RTL nothing
here exercises, fails; so does a point that gains coverage, until the record is
updated. Rewrite it after an intended change, and review the diff:
    AUTOMBIST_UPDATE_GOLDEN=1 python -m pytest tests/integration/test_rtl_coverage.py
Verilator 5.048 (the flake's) and 5.049 report identical points. The generated
wrapper, the testbench and the memory model are not measured.

What the record leaves uncovered is unreachable here: the default arm of every
case statement; the branches an algorithm's element order cannot take (each
march FSM is one template, and march-c, say, never steps from a down element to
an up one); and onchip_2d_repair_analyzer's repair_load branch, which no valid
config drives, since onchip_repair_persistence is rejected with
onchip_col_repair and the wrapper ties the 2D analyzer's repair_load low.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None or shutil.which("make") is None,
    reason="needs Verilator + make on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import _find_rtl_dir, generate_from_config  # noqa: E402
from autombist.manifest import _memory_connection_widths, _redundancy_flags  # noqa: E402
from autombist.memory_model import render_memory_model  # noqa: E402
from autombist.testbench import bist_cycle_bound, parse_wrapper_ports, rtl_sources  # noqa: E402

GOLDEN = REPO_ROOT / "tests" / "golden"
RECORD = GOLDEN / "rtl_coverage.yml"
CASES: dict[str, dict] = yaml.safe_load((GOLDEN / "cases.yml").read_text(encoding="utf-8"))
METRICS: dict[str, dict] = yaml.safe_load((GOLDEN / "metrics.yml").read_text(encoding="utf-8"))
UPDATE = os.environ.get("AUTOMBIST_UPDATE_GOLDEN") == "1"
REGENERATE = "AUTOMBIST_UPDATE_GOLDEN=1 python -m pytest tests/integration/test_rtl_coverage.py"

RECORD_HEADER = """\
# Verilator line and branch coverage of rtl/, checked by
# tests/integration/test_rtl_coverage.py (see its docstring for what runs).
# Per file: its coverage points, how many the runs reach, and the ones they do
# not ("line:column type kind"). Rewritten by:
#   AUTOMBIST_UPDATE_GOLDEN=1 python -m pytest tests/integration/test_rtl_coverage.py
"""

#: Read-latency variants of each algorithm's plain golden case: unit -> (case, overrides).
LATENCY_VARIANTS: dict[str, tuple[str, dict]] = {
    f"{case}-rl{latency}": (case, {"read_latency": latency})
    for case in ("march-raw", "march-x", "mats-plus", "checkerboard", "march-1r1w", "march-2rw")
    for latency in (0, 2)
}
UNITS: dict[str, tuple[str, dict]] = {
    **{case: (case, {}) for case, spec in CASES.items() if not spec["generate"].get("use_saboteur")},
    **LATENCY_VARIANTS,
}

TB = """\
`timescale 1ns/1ps
module tb_cov;
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
    task automatic run_bist;
        cycles = 0;
        test_mode = 1'b1;
        bist_start = 1'b1;
        while (bist_done !== 1'b1 && cycles < {max_cycles}) begin @(negedge clk); cycles = cycles + 1; end
    endtask
    initial begin
        repeat (4) @(negedge clk);
        rst_n = 1'b1;
        repeat (2) @(negedge clk);
        cycles = 0;
{modes}        begin
            run_bist();
            $display("COV RESULT: bist done=%0d fail=%0d cycles=%0d", bist_done, bist_fail, cycles);
            if ($test$plusargs("restart")) begin
                repeat (2) @(negedge clk);  // the result held while start stays high
                bist_start = 1'b0;
                repeat (3) @(negedge clk);
                run_bist();
                $display("COV RESULT: restart done=%0d fail=%0d cycles=%0d", bist_done, bist_fail, cycles);
            end
        end
        $finish;
    end
endmodule
"""

SELFREPAIR_MODE = """\
        if ($test$plusargs("selfrepair")) begin
            self_repair_start = 1'b1;
            while (self_repair_done !== 1'b1 && cycles < {max_cycles}) begin @(negedge clk); cycles = cycles + 1; end
            $display("COV RESULT: selfrepair done=%0d fail=%0d cycles=%0d", self_repair_done, self_repair_fail, cycles);
        end else
"""

LOAD_MODE = """\
        if ($test$plusargs("load")) begin
            fuse_row_repair_en = '1;
            fuse_faulty_row_addr = '1;
            repair_load = 1'b1;
            while (repair_load_done !== 1'b1 && cycles < 1000) begin @(negedge clk); cycles = cycles + 1; end
            repair_load = 1'b0;
            $display("COV RESULT: load done=%0d fail=0 cycles=%0d", repair_load_done, cycles);
        end else
"""

RESULT = re.compile(r"COV RESULT: (\w+) done=(\d) fail=(\d) cycles=(\d+)")
POINT = re.compile(r"^C '(.*)' (\d+)$")


def _unit_config(unit: str) -> tuple[dict, dict]:
    case, overrides = UNITS[unit]
    spec = CASES[case]
    return {**spec["config"], **overrides}, spec["generate"]


def _testbench(wrapper: Path, snapshot: dict) -> str:
    top = snapshot["wrapper_module_name"]
    ports = parse_wrapper_ports(wrapper.read_text(encoding="utf-8"), top)
    declarations, connections = [], []
    for port in ports:
        if port.direction == "input" and port.name not in ("clk", "rst_n"):
            idle = "'1" if port.name == "func_csb" else "'0"
            declarations.append(f"    logic {port.range}{port.name} = {idle};")
        elif port.direction == "output":
            declarations.append(f"    wire  {port.range}{port.name};")
        connections.append(f"        .{port.name}({port.name})")
    names = {port.name for port in ports}
    max_cycles = 6 * bist_cycle_bound(snapshot)
    modes = (LOAD_MODE if "repair_load" in names else "") + (
        SELFREPAIR_MODE.format(max_cycles=max_cycles) if "self_repair_start" in names else ""
    )
    return TB.format(
        addr_width=snapshot["addr_width"], data_width=snapshot["data_width"], top=top,
        declarations="\n".join(declarations), connections=",\n".join(connections),
        max_cycles=max_cycles, modes=modes,
    )


def _scenarios(snapshot: dict) -> list[tuple[str, str, dict, dict]]:
    """(name, mode, {(physical row, physical bit): stuck value}, expected {"fail": 0|1})."""
    redundancy = snapshot.get("redundancy") or {}
    depth, width = 1 << snapshot["addr_width"], snapshot["data_width"]
    rows, cols = redundancy.get("num_spare_rows", 0), redundancy.get("num_spare_cols", 0)
    column = {(r, 3): 1 for r in range(4)}
    out = [
        ("bist", "restart", {}, {"fail": 0}),
        ("bist-defect", "bist", {(1, 0): 1}, {"fail": 1}),
    ]
    if redundancy.get("onchip_selfrepair"):
        out += [
            ("selfrepair", "selfrepair", {}, {"fail": 0}),
            ("selfrepair-cell", "selfrepair", {(1, 0): 1}, {"fail": 0}),
            ("selfrepair-two-bits-one-row", "selfrepair", {(2, 0): 1, (2, width - 1): 0}, {"fail": 0}),
        ]
        if cols:
            out.append(("selfrepair-column", "selfrepair", column, {"fail": 0}))
        if redundancy.get("onchip_spare_retry"):
            # spare row 0 (physical row `depth`) is faulty; the cell takes it, verify retires it
            out.append(("selfrepair-faulty-spare-row", "selfrepair", {(1, 0): 1, (depth, 0): 1}, {"fail": 0}))
            if cols:
                # the column needs the only spare column, whose lane (physical bit `width`) is
                # faulty at row 5; retired, four faulty rows are left for two spare rows
                out.append(("selfrepair-faulty-spare-col", "selfrepair", {**column, (5, width): 1}, {"fail": 1}))
        faulty_rows = rows + cols + 1 + redundancy.get("num_diagnosis_entries", 0)
        out.append(("selfrepair-unrepairable", "selfrepair",
                    {(r, r % width): 1 for r in range(min(faulty_rows, depth))}, {"fail": 1}))
    if redundancy.get("onchip_repair_persistence"):
        out.append(("repair-load", "load", {}, {}))
    return out


def _write_masks(paths: tuple[Path, Path], snapshot: dict, stuck: dict) -> None:
    addr_w, data_w = _memory_connection_widths(snapshot, _redundancy_flags(snapshot))
    words = 1 << addr_w
    sa0, sa1 = [(1 << data_w) - 1] * words, [0] * words
    for (row, bit), value in stuck.items():
        if value:
            sa1[row] |= 1 << bit
        else:
            sa0[row] &= ~(1 << bit)
    digits = (data_w + 3) // 4
    for path, values in zip(paths, (sa0, sa1)):
        path.write_text("".join(f"{v:0{digits}X}\n" for v in values), encoding="ascii")


def _points(dat: Path, module_dir: Path, rtl: Path) -> dict[tuple[str, int, int, str, str], int]:
    """{(rtl/ path, line, column, type, kind): count} for the points in rtl/ sources."""
    points: dict[tuple[str, int, int, str, str], int] = defaultdict(int)
    for line in dat.read_text(encoding="utf-8", errors="replace").splitlines():
        match = POINT.match(line)
        if not match:
            continue
        fields = dict(kv.split("\x02", 1) for kv in match.group(1).split("\x01") if "\x02" in kv)
        try:
            rel = Path(fields["f"]).resolve().relative_to(module_dir.resolve()).as_posix()
        except ValueError:
            continue
        if not (rtl / rel).is_file():
            continue  # the wrapper, the testbench and the model are not rtl/ sources
        key = (f"rtl/{rel}", int(fields["l"]), int(fields.get("n", 0)), fields.get("t", "?"), fields.get("o", "?"))
        points[key] += int(match.group(2))
    return points


def _run_unit(unit: str, root: Path) -> dict:
    config, generate = _unit_config(unit)
    work = root / unit
    work.mkdir(parents=True)
    config_path = work / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, work / "out", **generate)
    module_dir = wrapper.parent
    snapshot = yaml.safe_load((module_dir / "config.yml").read_text(encoding="utf-8"))
    masks = (work / "sa0.hex", work / "sa1.hex")
    _write_masks(masks, snapshot, {})
    model = work / f"{snapshot['memory_name']}_cov_model.v"
    model.write_text(render_memory_model(snapshot, stuck_masks=tuple(str(p.resolve()) for p in masks)),
                     encoding="utf-8")
    tb = work / "tb_cov.sv"
    tb.write_text(_testbench(wrapper, snapshot), encoding="utf-8")
    sources = [str(module_dir / s) for s in rtl_sources(snapshot, module_dir)]
    build = subprocess.run(
        ["verilator", "--binary", "--timing", "--coverage-line", "-Wno-fatal", "-Wno-lint", "-Wno-style",
         "--top-module", "tb_cov", "-Mdir", str(work / "obj"), "-o", "sim", str(tb), *sources, str(model)],
        capture_output=True, text=True,
    )
    if build.returncode != 0:
        return {"unit": unit, "error": f"verilator build failed:\n{(build.stdout + build.stderr)[-3000:]}"}

    rtl = _find_rtl_dir()
    points: dict[tuple, int] = defaultdict(int)
    runs = []
    for name, mode, stuck, expected in _scenarios(snapshot):
        _write_masks(masks, snapshot, stuck)
        run_dir = work / name
        run_dir.mkdir()
        plusargs = {"restart": ["+restart"], "selfrepair": ["+selfrepair"], "load": ["+load"]}.get(mode, [])
        run = subprocess.run([str(work / "obj" / "sim"), *plusargs], capture_output=True, text=True, cwd=run_dir)
        results = [(m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)))
                   for m in RESULT.finditer(run.stdout)]
        runs.append({"name": name, "returncode": run.returncode, "results": results, "expected": expected,
                     "output": (run.stdout + run.stderr)[-1500:]})
        dat = run_dir / "coverage.dat"
        if dat.exists():
            for key, count in _points(dat, module_dir, rtl).items():
                points[key] += count
    return {"unit": unit, "runs": runs, "points": dict(points)}


@pytest.fixture(scope="module")
def coverage_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    root = tmp_path_factory.mktemp("rtl_coverage")
    workers = max(1, min(8, os.cpu_count() or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda unit: _run_unit(unit, root), sorted(UNITS)))
    return {r["unit"]: r for r in results}


def _merged(coverage_run: dict[str, dict]) -> dict[str, dict]:
    hits: dict[tuple, int] = defaultdict(int)
    for result in coverage_run.values():
        for key, count in result.get("points", {}).items():
            hits[key] += count
    files: dict[str, dict] = {}
    for (path, line, column, kind, comment), count in sorted(hits.items()):
        entry = files.setdefault(path, {"points": 0, "covered": 0, "uncovered": []})
        entry["points"] += 1
        if count:
            entry["covered"] += 1
        else:
            entry["uncovered"].append(f"{line}:{column} {kind} {comment}")
    return files


def test_every_unit_builds_and_every_run_reaches_its_verdict(coverage_run: dict[str, dict]) -> None:
    problems = []
    for unit, result in sorted(coverage_run.items()):
        if "error" in result:
            problems.append(f"{unit}: {result['error']}")
            continue
        for run in result["runs"]:
            where = f"{unit}/{run['name']}"
            if run["returncode"] != 0 or not run["results"]:
                problems.append(f"{where}: no result (exit {run['returncode']}):\n{run['output']}")
                continue
            for mode, done, fail, cycles in run["results"]:
                if not done:
                    problems.append(f"{where}: {mode} never finished ({cycles} cycles)")
                elif "fail" in run["expected"] and fail != run["expected"]["fail"]:
                    problems.append(f"{where}: {mode} fail={fail}, expected {run['expected']['fail']}")
    assert not problems, "\n".join(problems)


def test_verilator_agrees_with_icarus_on_the_recorded_cycle_counts(coverage_run: dict[str, dict]) -> None:
    problems = []
    for unit, result in sorted(coverage_run.items()):
        if unit not in METRICS or "runs" not in result:
            continue
        for run in result["runs"]:
            for mode, _done, _fail, cycles in run["results"]:
                metric = {"bist": "bist_cycles", "restart": "bist_cycles", "selfrepair": "selfrepair_cycles"}.get(mode)
                if run["name"] in ("bist", "selfrepair") and metric in METRICS[unit]:
                    if cycles != METRICS[unit][metric]:
                        problems.append(f"{unit}/{run['name']} {mode}: {cycles} cycles on Verilator, "
                                        f"{METRICS[unit][metric]} recorded (Icarus)")
    assert not problems, "\n".join(problems)


def test_rtl_coverage_matches_the_record(coverage_run: dict[str, dict]) -> None:
    assert all("points" in r for r in coverage_run.values()), "a unit failed to build; see the verdict test"
    actual = _merged(coverage_run)
    if UPDATE:
        total = {"points": sum(f["points"] for f in actual.values()),
                 "covered": sum(f["covered"] for f in actual.values())}
        RECORD.write_text(RECORD_HEADER + yaml.safe_dump({"total": total, "files": actual}, sort_keys=True),
                          encoding="utf-8", newline="\n")
    recorded = (yaml.safe_load(RECORD.read_text(encoding="utf-8")) or {}).get("files", {}) if RECORD.exists() else {}

    problems = []
    for path in sorted(actual.keys() | recorded.keys()):
        got, want = actual.get(path), recorded.get(path)
        if got is None or want is None:
            problems.append(f"{path}: {'no longer measured' if got is None else 'not in the record'}")
            continue
        lost = sorted(set(got["uncovered"]) - set(want["uncovered"]))
        gained = sorted(set(want["uncovered"]) - set(got["uncovered"]))
        if lost:
            problems.append(f"{path}: newly uncovered: {', '.join(lost)}")
        if gained:
            problems.append(f"{path}: newly covered: {', '.join(gained)}")
        if not lost and not gained and got["points"] != want["points"]:
            problems.append(f"{path}: {got['points']} points, recorded {want['points']}")
    assert not problems, (
        "RTL coverage moved from tests/golden/rtl_coverage.yml:\n  " + "\n  ".join(problems)
        + f"\nIf the change is intended, rerun with {REGENERATE} and review the diff."
    )
