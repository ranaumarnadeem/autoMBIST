"""`wrap-test-access --emit-stil` writes the run_mbist pattern as IEEE 1450 STIL, and the
STIL does what the simulation-proven JTAG vectors do.

The checks that give it teeth:
  * the STIL is replayed literally against the wrapped RTL (stil_replay.py reads the text
    itself, every drive at its time, every compare at its strobe) and passes on a good
    memory, from the lead-in on: nothing is assumed to be reset already;
  * its compares are the vectors' compares, bit for bit, in order;
  * a stuck bit in the memory fails exactly the bist_fail compare, and a run loop shorter
    than the BIST fails the bist_done compare;
  * it declares every pin of the wrapped top, outputs as Out.

Skips without warptap 0.0.4 (earlier to_stil compares TDO after the edge), Yosys or
Icarus Verilog; the Semi-ATE-STIL check also needs that parser.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from autombist.cli import app
from autombist.jtag_bist import _bit_names, parse_netlist_ports, stil_support_problem

from stil_replay import replay_stil


def _stil_ready() -> bool:
    try:
        import warptap  # noqa: F401
    except ImportError:
        return False
    return stil_support_problem() is None


pytestmark = pytest.mark.skipif(
    not _stil_ready() or any(shutil.which(t) is None for t in ("yosys", "iverilog", "vvp")),
    reason="needs warptap 0.0.4 or later plus yosys and Icarus Verilog on PATH (Linux/WSL only)",
)

runner = CliRunner()
P1 = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"}
P2RW = {"porta": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "csb": "csb0", "we": "web0"},
        "portb": {"type": "rw", "clk": "clk1", "addr": "addr1", "din": "din1", "dout": "dout1", "csb": "csb1", "we": "web1"}}


def _cfg(top: str, **kw) -> dict:
    cfg = {"memory_name": "sram_1rw", "wrapper_module_name": top, "addr_width": 3, "data_width": 4,
           "we_active_low": True, "ports": P1}
    cfg.update(kw)
    return cfg


SHAPES = {
    "rl0": _cfg("sx_rl0", read_latency=0),
    "2rw": _cfg("sx_2rw", memory_name="sram_2rw", ports=P2RW, algo="march-2rw"),
    "self-repair": _cfg("sx_sr", addr_width=2, ports={**P1, "we": "web0"},
                        redundancy={"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True,
                                    "onchip_diagnosis": True, "num_diagnosis_entries": 2}),
    "shared-bus": _cfg("sx_shb", topology="shared-bus", memories=[{"name": "b0"}, {"name": "b1"}]),
}


def _wrap(tmp_path: Path, config: dict, *extra: str):
    config = dict(config)
    algo = config.pop("algo", "march-c")
    path = tmp_path / "c.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    made = runner.invoke(app, ["generate", "--config", str(path), "--out", str(tmp_path / "out"), "--emit-manifest",
                              "--algo", algo])
    assert made.exit_code == 0, made.output
    module_outdir = tmp_path / "out" / (config["wrapper_module_name"] if "topology" in config else config["memory_name"])
    wrapped = runner.invoke(app, ["wrap-test-access", "--manifest", str(module_outdir), "--emit-stil", *extra])
    assert wrapped.exit_code == 0, wrapped.output
    return module_outdir, module_outdir / "test-access", wrapped.output


def _replay(tmp_path: Path, module_outdir: Path, access: Path, top: str, memory: str, model: Path | None = None):
    netlist = access / f"{top}_test_access.v"
    ports = parse_netlist_ports(netlist.read_text(encoding="utf-8"), top)
    stil = (access / f"{top}_run_mbist.stil").read_text(encoding="utf-8")
    model = model or module_outdir / "tb" / f"{memory}_model.v"
    return replay_stil(stil, [netlist, model], top, ports, tmp_path / "replay")


def _vec_compares(access: Path, top: str) -> list[int]:
    """Expected TDO of every checked vector in the .vec, in order."""
    lines = (access / f"{top}_run_mbist.vec").read_text(encoding="utf-8").split("\n")
    return [int(f[3]) for f in (line.split() for line in lines if line) if f[0] == "0" and f[4] != "0"]


def test_emit_stil_writes_a_stil_the_manifest_records_and_says_what_it_assumed(tmp_path: Path) -> None:
    module_outdir, access, output = _wrap(tmp_path, _cfg("sx_ctrl"))
    stil_path = access / "sx_ctrl_run_mbist.stil"
    stil = stil_path.read_text(encoding="utf-8")
    block = json.loads((module_outdir / "manifest.json").read_text(encoding="utf-8"))["test_access"]

    assert f"STIL (run_mbist): {stil_path} (TCK 100 ns, clk 100 ns -- assumed, set --tck-max-freq-mhz / " \
           "--stil-clk-period-ns)" in output
    assert block["stil"] == {"path": str(stil_path.resolve()), "tck_period_ns": 100.0, "clk_period_ns": 100.0}
    assert stil.startswith("// IEEE 1450 STIL: run the memory BIST of sx_ctrl")
    assert "STIL 1.0;" in stil and re.search(r"^\s*Loop \d+ \{", stil, re.MULTILINE)  # the run loop, compressed


def test_the_stil_declares_every_pin_of_the_wrapped_top(tmp_path: Path) -> None:
    module_outdir, access, _ = _wrap(tmp_path, _cfg("sx_pins"))
    ports = parse_netlist_ports((access / "sx_pins_test_access.v").read_text(encoding="utf-8"), "sx_pins")

    replay = _replay(tmp_path, module_outdir, access, "sx_pins", "sram_1rw")

    expected = {bit: ("Out" if p.direction == "output" else "In") for p in ports for bit in _bit_names(p)}
    assert replay.signals == expected


def test_the_stil_replays_against_the_rtl_and_compares_what_the_vectors_compare(tmp_path: Path) -> None:
    module_outdir, access, _ = _wrap(tmp_path, _cfg("sx_good"))

    replay = _replay(tmp_path, module_outdir, access, "sx_good", "sram_1rw")

    assert replay.passed, replay.output
    assert [c.signal for c in replay.compares] == ["tdo"] * len(replay.compares)
    assert [c.expected for c in replay.compares] == _vec_compares(access, "sx_good") == [1, 0]  # done=1, fail=0


@pytest.mark.parametrize("shape", SHAPES)
def test_the_stil_replays_for_every_shape(tmp_path: Path, shape: str) -> None:
    config = SHAPES[shape]
    module_outdir, access, _ = _wrap(tmp_path, config)

    replay = _replay(tmp_path, module_outdir, access, config["wrapper_module_name"], config["memory_name"])

    assert replay.passed, replay.output
    assert [c.expected for c in replay.compares] == _vec_compares(access, config["wrapper_module_name"])


def test_a_stuck_bit_fails_exactly_the_bist_fail_compare(tmp_path: Path) -> None:
    module_outdir, access, _ = _wrap(tmp_path, _cfg("sx_stuck"))
    good = module_outdir / "tb" / "sram_1rw_model.v"
    stuck = tmp_path / "stuck_model.v"
    text = good.read_text(encoding="utf-8")
    assert "dout0 <= mem[p0_addr_q];" in text
    stuck.write_text(text.replace("dout0 <= mem[p0_addr_q];", "dout0 <= mem[p0_addr_q] & ~4'b0001;"), encoding="utf-8")

    replay = _replay(tmp_path, module_outdir, access, "sx_stuck", "sram_1rw", model=stuck)

    assert replay.mismatches == [1], replay.output  # compare 0 is bist_done, compare 1 bist_fail


def test_a_run_loop_shorter_than_the_bist_fails_the_bist_done_compare(tmp_path: Path) -> None:
    module_outdir, access, _ = _wrap(tmp_path, _cfg("sx_short"), "--bist-cycles", "20")

    replay = _replay(tmp_path, module_outdir, access, "sx_short", "sram_1rw")

    assert 0 in replay.mismatches, replay.output


def test_the_timing_options_set_both_periods(tmp_path: Path) -> None:
    module_outdir, access, output = _wrap(tmp_path, _cfg("sx_fast"), "--tck-max-freq-mhz", "25",
                                          "--stil-clk-period-ns", "20")
    stil = (access / "sx_fast_run_mbist.stil").read_text(encoding="utf-8")

    assert "Period '40ns';" in stil and "Period '20ns';" in stil
    assert "(TCK 40 ns, clk 20 ns)" in output and "assumed" not in output
    replay = _replay(tmp_path, module_outdir, access, "sx_fast", "sram_1rw")
    assert replay.passed, replay.output


def test_the_stil_is_valid_to_an_independent_parser(tmp_path: Path) -> None:
    parsers = pytest.importorskip("Semi_ATE.STIL.parsers.STILParser", reason="needs `pip install Semi-ATE-STIL lark`")
    _, access, _ = _wrap(tmp_path, _cfg("sx_parse"))

    parser = parsers.STILParser(str(access / "sx_parse_run_mbist.stil"))
    assert parser.parse_syntax(), parser.err_msg
    parser.parse_semantic()  # returns None either way; success is is_parsing_done with no message
    assert parser.is_parsing_done and parser.err_msg == "", parser.err_msg
