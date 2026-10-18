"""The testbench `generate` writes into tb/ runs the BIST on its own -- Icarus
Verilog, the generated RTL and the memory's own model, no cocotb or Python --
and the BIST-length model behind its timeout and the PDL's run loop is checked
against simulation for every algorithm here, not trusted: it may run up to two
cycles long, never short.

Skips without Icarus Verilog.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from autombist.generator import generate_from_config
from autombist.testbench import bist_cycle_bound, bist_cycles

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="needs Icarus Verilog (iverilog, vvp) on PATH",
)

HW = Path(__file__).resolve().parents[1] / "hardware"
P1 = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"}
P1R1W = {"rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
         "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"}}
P2RW = {"porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
        "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"}}
BASE = {"memory_name": "sram_1rw", "wrapper_module_name": "tb_e2e_ctrl", "addr_width": 3,
        "data_width": 4, "we_active_low": True, "ports": P1}
# algo -> (config, memory model)
ALGOS = {
    "march-c": (BASE, "sram_1rw.v"),
    "march-raw": (BASE, "sram_1rw.v"),
    "march-x": (BASE, "sram_1rw.v"),
    "mats-plus": (BASE, "sram_1rw.v"),
    "checkerboard": (BASE, "sram_1rw.v"),
    "march-1r1w": ({**BASE, "memory_name": "sram_1r1w_dut", "ports": P1R1W}, "sram_1r1w_dut.v"),
    "march-2rw": ({**BASE, "memory_name": "sram_2rw_dut", "ports": P2RW}, "sram_2rw_dut.v"),
}
RESULT = re.compile(r"MBIST RESULT: (PASS|FAIL|TIMEOUT) -- .*?(\d+) clk cycles")


def _generate(tmp_path: Path, config: dict, algo: str) -> tuple[Path, dict]:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    module_outdir = generate_from_config(path, tmp_path / "out", algo=algo).parent
    return module_outdir, yaml.safe_load((module_outdir / "config.yml").read_text(encoding="utf-8"))


def _run_tb(module_outdir: Path, *models: Path, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(module_outdir / "tb" / "run_tb.sh"), *map(str, models)],
        capture_output=True, text=True, env={**os.environ, **(env or {})},
    )


def _renamed(model: Path, module: str, dest: Path) -> Path:
    dest.write_text(re.sub(r"\bmodule\s+\w+", f"module {module}", model.read_text(), count=1))
    return dest


@pytest.mark.parametrize("algo", ALGOS)
def test_testbench_passes_a_good_memory_within_the_modelled_length(tmp_path: Path, algo: str) -> None:
    config, model = ALGOS[algo]
    module_outdir, snapshot = _generate(tmp_path, config, algo)

    run = _run_tb(module_outdir, HW / model)

    result = RESULT.search(run.stdout + run.stderr)
    assert run.returncode == 0 and result and result.group(1) == "PASS", run.stdout + run.stderr
    measured = int(result.group(2))
    # the model may run up to two cycles long, never short -- the timeout and
    # the PDL's run loop rely on it being an upper bound
    assert measured <= bist_cycles(snapshot) <= measured + 2
    assert measured < bist_cycle_bound(snapshot)


@pytest.mark.parametrize(
    "extra",
    [{"read_latency": 2}, {"topology": "shared-bus", "memories": [{"name": "b0"}, {"name": "b1"}, {"name": "b2"}]}],
    ids=["read-latency-2", "shared-bus-x3"],
)
def test_the_length_model_holds_across_latency_and_topology(tmp_path: Path, extra: dict) -> None:
    module_outdir, snapshot = _generate(tmp_path, {**BASE, "addr_width": 4, **extra}, "march-c")

    run = _run_tb(module_outdir, HW / "sram_1rw.v")

    result = RESULT.search(run.stdout)
    assert run.returncode == 0 and result and result.group(1) == "PASS", run.stdout + run.stderr
    assert int(result.group(2)) <= bist_cycles(snapshot) <= int(result.group(2)) + 2


@pytest.mark.parametrize(
    ("algo", "stuck_model"),
    [("march-c", "sram_1rw_stuck_bit.v"), ("march-2rw", "sram_2rw_dut_stuck_bit.v")],
)
def test_testbench_fails_a_stuck_bit_memory(tmp_path: Path, algo: str, stuck_model: str) -> None:
    config, _good = ALGOS[algo]
    module_outdir, _ = _generate(tmp_path, config, algo)
    model = _renamed(HW / stuck_model, config["memory_name"], tmp_path / "stuck.v")

    run = _run_tb(module_outdir, model)

    assert run.returncode != 0
    assert "MBIST RESULT: FAIL -- bist_fail=1" in run.stdout + run.stderr


def test_testbench_times_out(tmp_path: Path) -> None:
    module_outdir, _ = _generate(tmp_path, BASE, "march-c")

    run = _run_tb(module_outdir, HW / "sram_1rw.v", env={"MBIST_MAX_CYCLES": "10"})

    assert run.returncode != 0
    assert "MBIST RESULT: TIMEOUT -- bist_done not set after 10 clk cycles" in run.stdout + run.stderr


def test_run_script_without_a_model_uses_the_generated_one_and_says_so(tmp_path: Path) -> None:
    module_outdir, _ = _generate(tmp_path, BASE, "march-c")

    run = _run_tb(module_outdir)

    assert run.returncode == 0 and "MBIST RESULT: PASS" in run.stdout, run.stdout + run.stderr
    assert "using the generated behavioral model sram_1rw_model.v" in run.stderr


def test_testbench_runs_from_a_moved_output_directory(tmp_path: Path) -> None:
    """The run script finds everything relative to itself."""
    module_outdir, _ = _generate(tmp_path, BASE, "march-c")
    moved = tmp_path / "elsewhere"
    shutil.copytree(module_outdir, moved)
    shutil.rmtree(module_outdir)

    run = _run_tb(moved, HW / "sram_1rw.v")

    assert run.returncode == 0 and "MBIST RESULT: PASS" in run.stdout, run.stdout + run.stderr
