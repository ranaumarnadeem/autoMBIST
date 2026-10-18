"""`wrap-test-access --emit-icl` writes the BSDL its ICL AccessLink points at, and
the values the BSDL states are the ones the wrapped hardware has.

The checks that give it teeth:
  * the BSDL's EXTEST opcode and instruction length equal the IR load the retargeted
    vectors actually play -- two outputs built from different warptap sources;
  * `--idcode` is read back from the wrapped RTL through TDO, by the same testbench
    that plays the BIST vectors, and the BSDL and the manifest state that value;
  * the same read-back fails when it expects the wrong IDCODE.

Skips without a warptap that has BSDL and IDCODE support, Yosys or Icarus Verilog.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from autombist.cli import app


def _warptap_supports(module: str, attribute: str) -> bool:
    try:
        return hasattr(__import__(module, fromlist=[attribute]), attribute)
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("warptap") is None
    or not _warptap_supports("warptap.tap_model", "idcode_value_error")
    or importlib.util.find_spec("warptap.bsdl_emit") is None
    or any(shutil.which(tool) is None for tool in ("yosys", "iverilog", "vvp")),
    reason="needs a warptap with BSDL and IDCODE support plus yosys and Icarus Verilog on PATH (Linux/WSL only)",
)

runner = CliRunner()
HW = Path(__file__).resolve().parents[1] / "hardware"
CONFIG = {"memory_name": "sram_1rw", "wrapper_module_name": "bd_ctrl", "addr_width": 3, "data_width": 4,
          "we_active_low": True,
          "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"}}
CUSTOM_IDCODE = 0x5CA1AB1F


def _wrap(tmp_path: Path, *extra: str, expect_ok: bool = True):
    config_path = tmp_path / "c.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    result = runner.invoke(app, ["generate", "--config", str(config_path), "--out", str(tmp_path / "out"),
                                 "--emit-manifest"])
    assert result.exit_code == 0, result.output
    module_outdir = tmp_path / "out" / CONFIG["memory_name"]
    result = runner.invoke(app, ["wrap-test-access", "--manifest", str(module_outdir), "--emit-icl", *extra])
    if expect_ok:
        assert result.exit_code == 0, result.output
    manifest = json.loads((module_outdir / "manifest.json").read_text(encoding="utf-8"))
    return result, module_outdir / "test-access", manifest


def _opcode(bsdl: str, instruction: str) -> str:
    import re
    return re.search(rf'"{instruction}\s+\(([01]+)\)', bsdl).group(1)


def _idcode(bsdl: str) -> int:
    import re
    body = re.search(r"attribute IDCODE_REGISTER of \w+ : entity is(.*?);", bsdl, re.DOTALL).group(1)
    return int("".join(re.findall(r'"([01]+)"', body)), 2)


def _ir_loaded_by(vectors: str, ir_width: int) -> int:
    """The instruction the vectors shift into the IR: Run-Test/Idle -> Shift-IR takes
    TMS 1,1,0,0, then ``ir_width`` shift cycles carry TDI, LSB first."""
    rows = [line.split() for line in vectors.splitlines()]
    assert [r[1] for r in rows[:4]] == ["1", "1", "0", "0"]
    return sum(int(r[2]) << i for i, r in enumerate(rows[4:4 + ir_width]))


def _idcode_vectors(expected: int) -> str:
    """Run-Test/Idle -> Shift-DR (IDCODE is selected after reset), 32 shifts checked
    against ``expected``, then back to Run-Test/Idle."""
    from warptap.tap_fsm import TapState, next_state
    from warptap.tap_ir import bits_from_int
    from warptap.tap_ir_play import navigation_tms, shift_tms

    lines: list[str] = []
    state = TapState.RUN_TEST_IDLE
    for tms in navigation_tms(state, TapState.SHIFT_DR):
        lines.append(f"0 {tms} 0 0 0")
        state = next_state(state, tms)
    bits = bits_from_int(expected, 32)
    for i, tms in enumerate(shift_tms(32)):
        lines.append(f"0 {tms} 0 {bits[i]} 1")
        state = next_state(state, tms)
    for tms in navigation_tms(state, TapState.RUN_TEST_IDLE):
        lines.append(f"0 {tms} 0 0 0")
    return "".join(line + "\n" for line in lines)


def _run_idcode_read(access: Path, expected: int) -> subprocess.CompletedProcess[str]:
    (access / "bd_ctrl_run_mbist.vec").write_text(_idcode_vectors(expected), encoding="utf-8")
    return subprocess.run(["bash", str(access / "run_tb_jtag.sh"), str(HW / "sram_1rw.v")],
                          capture_output=True, text=True)


def test_emit_icl_writes_a_bsdl_the_icl_the_pdl_and_the_manifest_point_at(tmp_path: Path) -> None:
    result, access, manifest = _wrap(tmp_path)
    top = "bd_ctrl"
    bsdl_path = access / f"{top}_test_access.bsd"
    bsdl = bsdl_path.read_text(encoding="utf-8")
    icl = (access / f"{top}_test_access.icl").read_text(encoding="utf-8")
    pdl = (access / f"{top}_run_mbist.pdl").read_text(encoding="utf-8")
    block = manifest["test_access"]
    tap = block["tap"]

    assert f"BSDL:             {bsdl_path}" in result.output and "assumed" in result.output
    assert f"entity {top} is" in bsdl and f"BSDLEntity {top};" in icl
    assert "EXTEST { ScanInterface {" in icl
    assert f"# BSDL: {bsdl_path.name}" in pdl and f"# ICL: {top}_test_access.icl" in pdl
    assert Path(block["bsdl_path"]) == bsdl_path
    assert Path(block["icl_path"]).name == f"{top}_test_access.icl"

    # the BSDL's EXTEST and instruction length are what the vectors really load
    length = int(tap["instruction_length"])
    assert f"INSTRUCTION_LENGTH of {top} : entity is {length};" in bsdl
    loaded = _ir_loaded_by((access / f"{top}_run_mbist.vec").read_text(encoding="utf-8"), length)
    assert format(loaded, f"0{length}b") == _opcode(bsdl, "EXTEST") == tap["network_access_opcode"]
    assert tap["network_access_instruction"] == "EXTEST"
    assert tap["bsdl_entity"] == top
    assert tap["tck_max_freq_hz"] == 10e6 and "1.000000e+07, BOTH" in bsdl


def test_the_tck_limit_and_idcode_options_reach_the_bsdl_and_the_manifest(tmp_path: Path) -> None:
    result, access, manifest = _wrap(tmp_path, "--tck-max-freq-mhz", "25", "--idcode", hex(CUSTOM_IDCODE))
    bsdl = (access / "bd_ctrl_test_access.bsd").read_text(encoding="utf-8")
    tap = manifest["test_access"]["tap"]

    assert "2.500000e+07, BOTH" in bsdl and tap["tck_max_freq_hz"] == 25e6
    assert _idcode(bsdl) == CUSTOM_IDCODE and tap["idcode"] == "0x5CA1AB1F"
    assert tap["idcode_is_placeholder"] is False
    assert "set by --idcode" in result.output and "assumed" not in result.output


@pytest.mark.parametrize("idcode", [None, hex(CUSTOM_IDCODE)], ids=["default", "custom"])
def test_the_idcode_is_read_back_from_the_wrapped_rtl_and_matches_the_bsdl(tmp_path: Path, idcode) -> None:
    from warptap.tap_model import IDCODE_VALUE

    extra = () if idcode is None else ("--idcode", idcode)
    _, access, manifest = _wrap(tmp_path, *extra)
    value = IDCODE_VALUE if idcode is None else CUSTOM_IDCODE
    bsdl = (access / "bd_ctrl_test_access.bsd").read_text(encoding="utf-8")
    assert _idcode(bsdl) == value and manifest["test_access"]["tap"]["idcode"] == f"0x{value:08X}"

    run = _run_idcode_read(access, value)

    assert run.returncode == 0, run.stdout + run.stderr
    assert "MBIST RESULT: PASS" in run.stdout


def test_the_idcode_read_back_fails_when_it_expects_the_wrong_value(tmp_path: Path) -> None:
    """Control: the read-back above is a real comparison, not a test that always passes."""
    from warptap.tap_model import IDCODE_VALUE

    _, access, _ = _wrap(tmp_path, "--idcode", hex(CUSTOM_IDCODE))

    run = _run_idcode_read(access, IDCODE_VALUE)

    assert run.returncode != 0
    assert "MBIST RESULT: FAIL" in run.stdout + run.stderr


def test_an_idcode_without_bit_0_is_refused_before_any_output_is_written(tmp_path: Path) -> None:
    result, access, _ = _wrap(tmp_path, "--idcode", "0x1A5A5002", expect_ok=False)

    assert result.exit_code == 1
    assert "bit 0" in result.output
    assert not access.exists()


@pytest.mark.parametrize("bad", ["0", "-5", "nan"])
def test_an_unusable_tck_limit_is_refused(tmp_path: Path, bad: str) -> None:
    result, access, _ = _wrap(tmp_path, "--tck-max-freq-mhz", bad, expect_ok=False)

    assert result.exit_code != 0
    assert not access.exists()


def test_the_tck_limit_needs_emit_icl(tmp_path: Path) -> None:
    config_path = tmp_path / "c.yml"
    config_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    assert runner.invoke(app, ["generate", "--config", str(config_path), "--out", str(tmp_path / "out"),
                               "--emit-manifest"]).exit_code == 0

    result = runner.invoke(app, ["wrap-test-access", "--manifest", str(tmp_path / "out" / "sram_1rw"),
                                 "--tck-max-freq-mhz", "25"])

    assert result.exit_code == 1 and "needs --emit-icl" in result.output
