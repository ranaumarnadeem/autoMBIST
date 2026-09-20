"""Real end-to-end proof for step 2 of docs/shared-hierarchical-mbist-plan.md's
implementation order: wiring the topology:/memories: config schema (step 0)
into generate_from_config's output-dir/module-naming, and into
wrapper_template.j2's shared-bus rendering (step 1).

Elaborates a real 2-memory shared-bus wrapper with real Verilator against
the same sram_1rw.v fixture and march_c RTL every other generator e2e test
in this project already uses -- not a hand-rolled stub -- matching this
project's own "prove it against real tools" discipline.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import ConfigError, generate_from_config  # noqa: E402

SRAM_FIXTURE = REPO_ROOT / "tests" / "hardware" / "sram_1rw.v"

BASE = {
    "memory_name": "sram_1rw",
    "wrapper_module_name": "shared_bus_ctrl",
    "topology": "shared-bus",
    "addr_width": 6,
    "data_width": 8,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"},
    "memories": [{"name": "mem_bank0"}, {"name": "mem_bank1"}],
}


def _write_config(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def test_output_dir_and_wrapper_file_are_named_after_the_controller(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, BASE)
    wrapper_path = generate_from_config(config_path, tmp_path / "out")

    module_outdir = tmp_path / "out" / "shared_bus_ctrl"
    assert wrapper_path == module_outdir / "shared_bus_ctrl_mbist.v"
    assert wrapper_path.exists()
    # Not named after any single memory -- there is no single memory_name
    # driving output naming under shared-bus (§4b).
    assert not (tmp_path / "out" / "sram_1rw").exists()


def test_generated_shared_bus_wrapper_elaborates_cleanly_with_real_verilator(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, BASE)
    wrapper_path = generate_from_config(config_path, tmp_path / "out")
    module_outdir = wrapper_path.parent

    # copy_mbist_rtl copies the selected algo's RTL into a subdirectory
    # (module_outdir/march_c/...), PLUS every generic redundancy/onchip-
    # repair support file regardless of whether this config uses any of
    # them -- those unrelated files carry their own pre-existing lint
    # warnings unrelated to this feature, so only pull in what this plain
    # (no redundancy) shared-bus config's module hierarchy actually needs:
    # the wrapper itself, the selected algo's own 3 files, and the memory
    # fixture -- not every file copy_mbist_rtl happens to have staged.
    algo_dir = module_outdir / "march_c"
    sources = [wrapper_path, *sorted(algo_dir.glob("*.sv")), SRAM_FIXTURE]

    result = subprocess.run(
        [
            "verilator", "--lint-only", "--timing",
            "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND", "-Wno-UNUSED", "-Wno-DECLFILENAME",
            # march_c_top.sv's bist_fail_valid/_addr/_bitmask outputs are only
            # connected under onchip_selfrepair/onchip_col_repair (pre-existing
            # wrapper_template.j2 gating, unrelated to this feature) -- this
            # plain config leaves them harmlessly unconnected.
            "-Wno-PINMISSING",
            *[str(s) for s in sources],
        ],
        capture_output=True, text=True, cwd=module_outdir,
    )
    assert result.returncode == 0, f"Verilator lint failed:\n{result.stdout}\n{result.stderr}"


def test_shared_bus_rejects_use_saboteur(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, BASE)
    with pytest.raises(ConfigError, match="use_saboteur=True"):
        generate_from_config(config_path, tmp_path / "out", use_saboteur=True)


def test_shared_bus_rejects_redundancy(tmp_path: Path) -> None:
    config = {
        **BASE,
        "redundancy": {"num_spare_rows": 1},
        "repair_ports": [{"name": "row_repair_en", "width": 1, "dir": "input"},
                          {"name": "faulty_row_addr", "width": 6, "dir": "input"}],
    }
    config_path = _write_config(tmp_path, config)
    with pytest.raises(ConfigError, match="redundancy:"):
        generate_from_config(config_path, tmp_path / "out")


def test_shared_bus_rejects_multi_port(tmp_path: Path) -> None:
    # algo=march-c would already reject a 2-port config for an unrelated
    # reason (march-c is single-port-only) before this feature's own check
    # ever runs -- march-1r1w genuinely supports 2 ports, so this actually
    # exercises the NEW shared-bus-specific rejection, not the pre-existing
    # single-algo-port-count one.
    config = {
        **BASE,
        "ports": {
            "r0": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
            "w0": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "we1"},
        },
    }
    config_path = _write_config(tmp_path, config)
    with pytest.raises(ConfigError, match="single physical port"):
        generate_from_config(config_path, tmp_path / "out", algo="march-1r1w")
