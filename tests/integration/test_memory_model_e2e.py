"""The testbench `generate` writes into tb/ runs with no memory model supplied: it
uses the behavioral model generated from the config (tb/<memory>_model.v).

A pass is only worth something if the model is a real memory and the testbench
really compares data, so there are controls: a stuck bit mutated into the
generated model fails the BIST, the write-forwarding a falling-edge two-port model
needs is removed and the BIST fails, and a model given on the command line replaces
the generated one (the macro's own model still runs, and still catches a fault).

Skips without Icarus Verilog; the JTAG case also needs warptap and Yosys.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from autombist.cli import app
from autombist.generator import generate_from_config

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="needs Icarus Verilog (iverilog, vvp) on PATH",
)

HW = Path(__file__).resolve().parents[1] / "hardware"
runner = CliRunner()

P1 = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"}
P1W = {**P1, "we": "web0"}
COL = {**P1W, "spare_wen": "spare_wen0"}
P1R1W = {"rport": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
         "wport": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "web1"}}
P2RW = {"porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
        "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"}}
C1R1W = {"rport": P1R1W["rport"], "wport": {**P1R1W["wport"], "spare_wen": "spare_wen1"}}
C2RW = {"porta": {**P2RW["porta"], "spare_wen": "spare_wen0"}, "portb": {**P2RW["portb"], "spare_wen": "spare_wen1"}}
SR = {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True}
SRC = {"num_spare_rows": 1, "num_spare_cols": 1, "onchip_selfrepair": True, "onchip_col_repair": True}


def _cfg(name: str, **kw) -> dict:
    cfg = {"memory_name": name, "wrapper_module_name": f"{name}_ctrl", "addr_width": 3, "data_width": 4,
           "we_active_low": True, "ports": P1}
    cfg.update(kw)
    return cfg


# case -> (config, algorithm). Latency 0 is the real-OpenRAM style, 1 the default, 2 and 3 hold dout.
CASES = {
    "march-c-rl1": (_cfg("m_a"), "march-c"),
    "march-c-rl0": (_cfg("m_b", read_latency=0), "march-c"),
    "march-c-rl2": (_cfg("m_c", read_latency=2), "march-c"),
    "march-c-rl3": (_cfg("m_d", read_latency=3), "march-c"),
    "we-active-high": (_cfg("m_e", we_active_low=False), "march-c"),
    "we-active-high-rl0": (_cfg("m_f", we_active_low=False, read_latency=0), "march-c"),
    "march-x": (_cfg("m_g"), "march-x"),
    "mats-plus": (_cfg("m_h"), "mats-plus"),
    "checkerboard": (_cfg("m_i"), "checkerboard"),
    "march-raw": (_cfg("m_j"), "march-raw"),
    "wide-words": (_cfg("m_k", addr_width=5, data_width=16), "march-c"),
    "1r1w-rl1": (_cfg("m_l", ports=P1R1W), "march-1r1w"),
    "1r1w-rl0": (_cfg("m_m", ports=P1R1W, read_latency=0), "march-1r1w"),
    "1r1w-rl2": (_cfg("m_n", ports=P1R1W, read_latency=2), "march-1r1w"),
    "2rw-rl1": (_cfg("m_o", ports=P2RW), "march-2rw"),
    "2rw-rl0": (_cfg("m_p", ports=P2RW, read_latency=0), "march-2rw"),
    "spare-rows": (_cfg("m_q", addr_width=2, ports=P1W, redundancy=SR), "march-c"),
    "spare-cols-rl1": (_cfg("m_r", addr_width=2, ports=COL, redundancy=SRC), "march-c"),
    "spare-cols-rl0": (_cfg("m_s", addr_width=2, ports=COL, redundancy=SRC, read_latency=0), "march-c"),
    "spare-cols-1r1w-rl1": (_cfg("m_t", addr_width=2, ports=C1R1W, redundancy=SRC), "march-1r1w"),
    "spare-cols-1r1w-rl0": (_cfg("m_u", addr_width=2, ports=C1R1W, redundancy=SRC, read_latency=0), "march-1r1w"),
    "spare-cols-2rw-rl1": (_cfg("m_v", addr_width=2, ports=C2RW, redundancy=SRC), "march-2rw"),
    "spare-cols-2rw-rl0": (_cfg("m_w", addr_width=2, ports=C2RW, redundancy=SRC, read_latency=0), "march-2rw"),
    "shared-bus-x3": (_cfg("m_x", addr_width=4, topology="shared-bus",
                           memories=[{"name": "b0"}, {"name": "b1"}, {"name": "b2"}]), "march-c"),
}
RESULT = re.compile(r"MBIST RESULT: (PASS|FAIL|TIMEOUT)[^\n]*")


def _generate(tmp_path: Path, name: str) -> Path:
    config, algo = CASES[name]
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(path, tmp_path / "out", algo=algo).parent


def _run_tb(module_outdir: Path, *models: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(module_outdir / "tb" / "run_tb.sh"), *map(str, models)],
                          capture_output=True, text=True, env=dict(os.environ))


def _mutate_model(module_outdir: Path, old: str, new: str) -> Path:
    (model,) = (module_outdir / "tb").glob("*_model.v")
    text = model.read_text(encoding="utf-8")
    assert text.count(old) >= 1, f"{old!r} not in the generated model"
    model.write_text(text.replace(old, new), encoding="utf-8")
    return model


@pytest.mark.parametrize("case", CASES)
def test_the_generated_testbench_runs_the_bist_with_no_model_argument(tmp_path: Path, case: str) -> None:
    module_outdir = _generate(tmp_path, case)

    run = _run_tb(module_outdir)

    result = RESULT.search(run.stdout + run.stderr)
    assert run.returncode == 0 and result and result.group(1) == "PASS", run.stdout + run.stderr
    assert "using the generated behavioral model" in run.stderr


@pytest.mark.parametrize(
    ("case", "old", "new"),
    [
        ("march-c-rl1", "dout0 <= mem[p0_addr_q];", "dout0 <= mem[p0_addr_q] & ~4'b0001;"),
        ("march-c-rl0", "dout0 <= mem[p0_addr_q];", "dout0 <= mem[p0_addr_q] & ~4'b0001;"),
        # port 1 reads in one element only, which expects ones: a stuck-at-0 is what it can see
        ("2rw-rl1", "dout1 <= mem[p1_addr_q];", "dout1 <= mem[p1_addr_q] & ~4'b0001;"),
    ],
    ids=["stuck-bit-rl1", "stuck-bit-rl0", "stuck-bit-2rw-port1"],
)
def test_a_stuck_bit_mutated_into_the_generated_model_fails_the_bist(
    tmp_path: Path, case: str, old: str, new: str
) -> None:
    """Control: the generated model is a real memory and the testbench compares data."""
    module_outdir = _generate(tmp_path, case)
    _mutate_model(module_outdir, old, new)

    run = _run_tb(module_outdir)

    assert run.returncode != 0
    assert "MBIST RESULT: FAIL -- bist_fail=1" in run.stdout + run.stderr


def test_without_forwarding_a_falling_edge_two_port_model_fails(tmp_path: Path) -> None:
    """Control: the 1R1W algorithm reads a address while writing it and expects the new
    data. Take the forwarding out of the latency-0 model and the BIST must fail."""
    module_outdir = _generate(tmp_path, "1r1w-rl0")
    _mutate_model(module_outdir, "dout0 <= p0_read(p0_addr_q);", "dout0 <= mem[p0_addr_q];")

    run = _run_tb(module_outdir)

    assert run.returncode != 0
    assert "MBIST RESULT: FAIL -- bist_fail=1" in run.stdout + run.stderr


def test_a_model_on_the_command_line_replaces_the_generated_one(tmp_path: Path) -> None:
    module_outdir = _generate(tmp_path, "march-c-rl1")
    stuck = tmp_path / "stuck.v"
    stuck.write_text(re.sub(r"\bmodule\s+\w+", "module m_a", (HW / "sram_1rw_stuck_bit.v").read_text(), count=1))
    good = tmp_path / "good.v"
    good.write_text(re.sub(r"\bmodule\s+\w+", "module m_a", (HW / "sram_1rw.v").read_text(), count=1))

    passes = _run_tb(module_outdir, good)
    fails = _run_tb(module_outdir, stuck)

    # replaced, not added: both define module m_a, which would be a redefinition error
    assert passes.returncode == 0 and "MBIST RESULT: PASS" in passes.stdout, passes.stdout + passes.stderr
    assert "using the generated behavioral model" not in passes.stderr
    assert fails.returncode != 0 and "MBIST RESULT: FAIL -- bist_fail=1" in fails.stdout + fails.stderr


@pytest.mark.skipif(
    importlib.util.find_spec("warptap") is None
    or any(shutil.which(tool) is None for tool in ("yosys", "iverilog", "vvp")),
    reason="needs `pip install warptap` plus yosys and Icarus Verilog on PATH (Linux/WSL only)",
)
@pytest.mark.parametrize("read_latency", [1, 0], ids=["rl1", "rl0"])
def test_the_jtag_testbench_runs_bare_on_the_generated_model(tmp_path: Path, read_latency: int) -> None:
    config = _cfg("sram_1rw", wrapper_module_name="mm_jtag", read_latency=read_latency,
                  ports={**P1, "we": "we0"})
    config_path = tmp_path / "c.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    assert runner.invoke(app, ["generate", "--config", str(config_path), "--out", str(tmp_path / "out"),
                               "--emit-manifest"]).exit_code == 0
    module_outdir = tmp_path / "out" / "sram_1rw"
    wrapped = runner.invoke(app, ["wrap-test-access", "--manifest", str(module_outdir)])
    assert wrapped.exit_code == 0, wrapped.output
    access = module_outdir / "test-access"
    script = (access / "run_tb_jtag.sh").read_text(encoding="utf-8")
    assert 'set -- "$HERE/../tb/sram_1rw_model.v"' in script and "usage:" not in script

    run = subprocess.run(["bash", str(access / "run_tb_jtag.sh")], capture_output=True, text=True)

    assert run.returncode == 0, run.stdout + run.stderr
    assert "MBIST RESULT: PASS -- read back through TDO: bist_done = 1, bist_fail = 0" in run.stdout
    assert json.loads((module_outdir / "manifest.json").read_text())["test_access"]["memory_blackboxed"] is True
