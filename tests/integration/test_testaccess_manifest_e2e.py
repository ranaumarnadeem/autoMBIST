"""`wrap-test-access --manifest DIR` end to end: the JTAG/IJTAG-wrapped netlist keeps
the memory blackboxed (its stub stands in for the model), and manifest.json's
test_access block lists every instance of that netlist -- TAP, one SIB per wrapped
port, one TDR bit per port bit, the MBIST blocks and the memory -- completely enough
that the block alone drives a per-block synthesis of the wrapped netlist: every
"separate" module synthesizes standalone from the wrapped file, and the glue
synthesizes with them blackboxed, leaving exactly the listed instances.

Skips without warptap (optional dependency) or Yosys.
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
from autombist.testaccess import classify_test_access_ports, test_access_kwargs_from_config

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("warptap") is None or shutil.which("yosys") is None,
    reason="needs `pip install warptap` plus yosys on PATH (Linux/WSL only)",
)

runner = CliRunner()
PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}
CASES = {
    "self-repair + diagnosis": {
        "memory_name": "sram_spares_tiny", "wrapper_module_name": "sr_ctrl", "addr_width": 3,
        "data_width": 4, "we_active_low": True, "ports": PORTS,
        "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True,
                       "onchip_diagnosis": True, "num_diagnosis_entries": 2},
    },
    "shared-bus self-repair": {
        "memory_name": "sram_spares_tiny", "wrapper_module_name": "shb_ctrl", "addr_width": 3,
        "data_width": 4, "we_active_low": True, "ports": PORTS, "topology": "shared-bus",
        "memories": [{"name": "bank0"}, {"name": "bank1"}],
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0, "onchip_selfrepair": True},
    },
}


def _wrap(tmp_path: Path, config: dict) -> tuple[Path, dict]:
    config_path = tmp_path / "c.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    result = runner.invoke(app, ["generate", "--config", str(config_path), "--out",
                                 str(tmp_path / "out"), "--emit-manifest"])
    assert result.exit_code == 0, result.output
    module_outdir = tmp_path / "out" / config["wrapper_module_name" if "topology" in config else "memory_name"]
    result = runner.invoke(app, ["wrap-test-access", "--manifest", str(module_outdir), "--emit-icl"])
    assert result.exit_code == 0, result.output
    return module_outdir, json.loads((module_outdir / "manifest.json").read_text(encoding="utf-8"))


def _yosys(script: str, cwd: Path) -> None:
    (cwd / "run.ys").write_text(script, encoding="utf-8")
    result = subprocess.run(["yosys", "-q", "-s", "run.ys"], cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", CASES)
def test_manifest_lists_every_jtag_instance_and_keeps_the_memory_blackboxed(tmp_path: Path, case: str) -> None:
    module_outdir, manifest = _wrap(tmp_path, CASES[case])
    block = manifest["test_access"]
    top = manifest["top_module"]

    assert block["memory_blackboxed"] is True
    assert Path(block["output_verilog"]) == module_outdir / "test-access" / f"{top}_test_access.v"
    assert Path(block["icl_path"]).exists()

    snapshot = yaml.safe_load((module_outdir / "config.yml").read_text(encoding="utf-8"))
    ports = classify_test_access_ports(**test_access_kwargs_from_config(snapshot))
    assert [(i["name"], i["role"], i["width"]) for i in block["instruments"]] == [
        (p.name, p.role, p.width) for p in ports
    ]

    paths = {i["hierarchical_path"]: i for i in block["instances"]}
    assert [p for p, i in paths.items() if i["category"] == "jtag_tap"] == ["warptap_tap_core"]
    for inst in block["instruments"]:
        assert paths[inst["sib"]]["category"] == "ijtag_sib"
        assert paths[inst["sib"]]["instrument"] == inst["name"]
        expected = "instrument_write" if inst["role"] == "control" else "bc1_shift_only"
        assert [paths[b]["module_type"] for b in inst["tdr_bits"]] == [expected] * inst["width"]
        assert [paths[b]["bit"] for b in inst["tdr_bits"]] == list(range(inst["width"]))
    # Every base-manifest instance (MBIST blocks, memory) survives into the wrapped netlist.
    base = {i["hierarchical_path"]: i["category"] for i in manifest["instances"]}
    assert {p: paths[p]["category"] for p in base} == base


@pytest.mark.parametrize("case", CASES)
def test_test_access_block_alone_drives_per_block_synthesis(tmp_path: Path, case: str) -> None:
    module_outdir, manifest = _wrap(tmp_path, CASES[case])
    block = manifest["test_access"]
    wrapped = block["output_verilog"]
    stub = module_outdir / manifest["sources"]["blackbox_stub"]
    separate = sorted({i["module_type"] for i in block["instances"] if i["hierarchy_hint"] == "separate"})

    for n, module_type in enumerate(separate):
        work = tmp_path / f"blk{n}"
        work.mkdir()
        _yosys(f"read_verilog -sv {wrapped}\nread_verilog -lib {stub}\nhierarchy -top {module_type}\n"
               f"proc\nflatten\nsynth -top {module_type}\n", work)

    work = tmp_path / "glue"
    work.mkdir()
    blackboxes = "\n".join(f"blackbox {m}" for m in separate)
    _yosys(f"read_verilog -sv {wrapped}\nread_verilog -lib {stub}\n{blackboxes}\n"
           f"hierarchy -check -top {block['top_module']}\nproc\nflatten\nsynth -top {block['top_module']}\n"
           f"write_json glue.json\n", work)
    top = json.loads((work / "glue.json").read_text(encoding="utf-8"))["modules"][block["top_module"]]
    instance_cells = {name for name, cell in top["cells"].items() if not cell["type"].startswith("$")}
    assert instance_cells == {i["hierarchical_path"] for i in block["instances"]}
