"""`wrap-test-access` writes what runs the BIST over JTAG: the run_mbist PDL
procedure, its retargeting to TCK vectors, and a self-checking testbench that
plays them against the wrapped netlist and checks the result only at TDO.

The checks that give the TDO comparison its teeth: a stuck bit in the memory
fails the bist_fail read (and only that one), and a run loop cut shorter than
the BIST fails the bist_done read.

Skips without warptap (optional dependency), Yosys or Icarus Verilog.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from autombist.cli import app
from autombist.testbench import bist_cycle_bound

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("warptap") is None
    or any(shutil.which(tool) is None for tool in ("yosys", "iverilog", "vvp")),
    reason="needs `pip install warptap` plus yosys and Icarus Verilog on PATH (Linux/WSL only)",
)

runner = CliRunner()
HW = Path(__file__).resolve().parents[1] / "hardware"
P1 = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"}
BASE = {"memory_name": "sram_1rw", "wrapper_module_name": "jb_ctrl", "addr_width": 3,
        "data_width": 4, "we_active_low": True, "ports": P1}


def _wrap(tmp_path: Path, config: dict, *extra: str) -> tuple[Path, dict, str]:
    config_path = tmp_path / "c.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    result = runner.invoke(app, ["generate", "--config", str(config_path), "--out",
                                 str(tmp_path / "out"), "--emit-manifest"])
    assert result.exit_code == 0, result.output
    module_outdir = tmp_path / "out" / config["wrapper_module_name" if "topology" in config else "memory_name"]
    result = runner.invoke(app, ["wrap-test-access", "--manifest", str(module_outdir), "--emit-icl", *extra])
    assert result.exit_code == 0, result.output
    snapshot = yaml.safe_load((module_outdir / "config.yml").read_text(encoding="utf-8"))
    return module_outdir / "test-access", snapshot, result.output


def _run(access: Path, *models: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(access / "run_tb_jtag.sh"), *map(str, models)],
                          capture_output=True, text=True)


def _stuck_1rw(tmp_path: Path) -> Path:
    stuck = tmp_path / "stuck.v"
    stuck.write_text(re.sub(r"\bmodule\s+\w+", "module sram_1rw",
                            (HW / "sram_1rw_stuck_bit.v").read_text(), count=1))
    return stuck


def test_wrap_writes_the_pdl_vectors_and_testbench(tmp_path: Path) -> None:
    access, snapshot, output = _wrap(tmp_path, BASE)
    top = BASE["wrapper_module_name"]

    for name in (f"{top}_run_mbist.pdl", f"{top}_run_mbist.vec", f"tb_{top}_jtag.sv", "run_tb_jtag.sh"):
        assert (access / name).is_file(), f"missing {name}"
    pdl = (access / f"{top}_run_mbist.pdl").read_text(encoding="utf-8")
    assert f"iRunLoop {bist_cycle_bound(snapshot)} -sck clk" in pdl
    assert f"# ICL: {top}_test_access.icl" in pdl
    # every register the PDL addresses is an instance the ICL declares
    icl = (access / f"{top}_test_access.icl").read_text(encoding="utf-8")
    for instance in re.findall(r"i(?:Write|Read) (\w+)\.DR", pdl):
        assert f"Instance {instance} Of" in icl
    assert "PDL (run_mbist):" in output


def test_jtag_testbench_passes_a_good_memory(tmp_path: Path) -> None:
    access, _, _ = _wrap(tmp_path, BASE)

    run = _run(access, HW / "sram_1rw.v")

    assert run.returncode == 0, run.stdout + run.stderr
    assert "MBIST RESULT: PASS -- read back through TDO: bist_done = 1, bist_fail = 0" in run.stdout


def test_jtag_testbench_fails_a_stuck_bit_on_the_bist_fail_read(tmp_path: Path) -> None:
    access, _, _ = _wrap(tmp_path, BASE)

    run = _run(access, _stuck_1rw(tmp_path))

    assert run.returncode != 0
    assert "TDO did not read back bist_fail = 0" in run.stdout + run.stderr


def test_a_run_loop_shorter_than_the_bist_fails_the_bist_done_read(tmp_path: Path) -> None:
    access, _, _ = _wrap(tmp_path, BASE, "--bist-cycles", "20")

    run = _run(access, HW / "sram_1rw.v")

    assert run.returncode != 0
    assert "TDO did not read back bist_done = 1" in run.stdout + run.stderr


def test_jtag_testbench_runs_a_shared_controller(tmp_path: Path) -> None:
    config = {**BASE, "wrapper_module_name": "jb_shb", "topology": "shared-bus",
              "memories": [{"name": "b0"}, {"name": "b1"}]}
    access, _, _ = _wrap(tmp_path, config)

    run = _run(access, HW / "sram_1rw.v")

    assert run.returncode == 0, run.stdout + run.stderr
    assert "MBIST RESULT: PASS" in run.stdout


def test_jtag_testbench_runs_through_a_self_repair_network(tmp_path: Path) -> None:
    """Eleven instruments on the chain. sram_spares_tiny has a built-in stuck
    bit, so the BIST (run without self-repair) finishes and reports it: the
    bist_done read passes and the bist_fail read fails."""
    config = {**BASE, "memory_name": "sram_spares_tiny", "wrapper_module_name": "jb_sr", "addr_width": 2,
              "ports": {**P1, "we": "web0"},
              "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True,
                             "onchip_diagnosis": True, "num_diagnosis_entries": 2}}
    access, _, _ = _wrap(tmp_path, config)

    run = _run(access, HW / "sram_spares_tiny.v")

    assert run.returncode != 0
    assert "TDO did not read back bist_fail = 0" in run.stdout + run.stderr


def test_without_a_config_the_run_length_comes_from_bist_cycles(tmp_path: Path) -> None:
    """--source/--top with no config: the PDL/testbench is skipped unless
    --bist-cycles gives the run loop's length."""
    config_path = tmp_path / "c.yml"
    config_path.write_text(yaml.safe_dump(BASE, sort_keys=False), encoding="utf-8")
    result = runner.invoke(app, ["generate", "--config", str(config_path), "--out", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    module_outdir = tmp_path / "out" / "sram_1rw"
    sources = ["--source", str(module_outdir / "sram_1rw_mbist.v"), "--source", str(module_outdir / "sram_1rw_bbox.v")]
    for rtl in sorted((module_outdir / "march_c").glob("*.sv")):
        sources += ["--source", str(rtl)]

    skipped = runner.invoke(app, ["wrap-test-access", *sources, "--top", "jb_ctrl",
                                  "--out", str(tmp_path / "a")])
    assert skipped.exit_code == 0, skipped.output
    assert "PDL/JTAG testbench: skipped" in skipped.output
    assert not (tmp_path / "a" / "run_tb_jtag.sh").exists()

    given = runner.invoke(app, ["wrap-test-access", *sources, "--top", "jb_ctrl",
                                "--out", str(tmp_path / "b"), "--bist-cycles", "600"])
    assert given.exit_code == 0, given.output
    run = _run(tmp_path / "b", HW / "sram_1rw.v")
    assert run.returncode == 0 and "MBIST RESULT: PASS" in run.stdout, run.stdout + run.stderr
