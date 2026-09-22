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


def test_generated_shared_bus_selfrepair_wrapper_elaborates_cleanly_with_real_verilator(tmp_path: Path) -> None:
    # docs/shared-hierarchical-mbist-plan.md §9b: plain on-chip row
    # self-repair, generated through the REAL generate_from_config path (not
    # a hand-built render_wrapper bypass) -- proves the relaxed validation
    # above actually reaches working, elaboratable RTL for real users.
    config = {
        **BASE,
        "memory_name": "sram_spares_tiny",
        "ports": {**BASE["ports"], "we": "web0"},
        "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True},
    }
    config_path = _write_config(tmp_path, config)
    wrapper_path = generate_from_config(config_path, tmp_path / "out")
    module_outdir = wrapper_path.parent

    algo_dir = module_outdir / "march_c"
    redundancy_files = [
        module_outdir / name
        for name in ("onchip_row_repair_analyzer.sv", "onchip_selfrepair_ctrl.sv", "repair_remap_row.sv")
    ]
    sram_fixture = REPO_ROOT / "tests" / "hardware" / "sram_spares_tiny.v"
    sources = [wrapper_path, *sorted(algo_dir.glob("*.sv")), *redundancy_files, sram_fixture]

    result = subprocess.run(
        [
            "verilator", "--lint-only", "--timing",
            "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND", "-Wno-UNUSED", "-Wno-DECLFILENAME",
            "-Wno-PINMISSING", "-Wno-PINCONNECTEMPTY", "-Wno-BLKSEQ",
            *[str(s) for s in sources],
        ],
        capture_output=True, text=True, cwd=module_outdir,
    )
    assert result.returncode == 0, f"Verilator lint failed:\n{result.stdout}\n{result.stderr}"

    # The bug this test guards against: the old singular self-repair
    # instantiation block used to render unconditionally whenever
    # onchip_selfrepair was set, duplicating the per-memory generate loop's
    # own analyzer/ctrl/remap instances and referencing now-undeclared
    # singular signals. Confirm exactly ONE generate-loop instantiation site
    # exists in the rendered output, not two.
    rendered = wrapper_path.read_text(encoding="utf-8")
    assert rendered.count("genvar") == 1
    assert rendered.count("endgenerate") == 1
    assert rendered.count("onchip_selfrepair_ctrl u_onchip_selfrepair_ctrl") == 1
    assert rendered.count("onchip_row_repair_analyzer #(") == 1
    assert rendered.count("repair_remap_row #(") == 1
    # The old singular (non-array) declaration must not reappear alongside
    # the per-memory sram_addr_phys_arr.
    assert "sram_addr_phys;" not in rendered


def test_generated_shared_bus_col_repair_wrapper_elaborates_cleanly_with_real_verilator(tmp_path: Path) -> None:
    # docs/shared-hierarchical-mbist-plan.md §9b step 4: on-chip 2D (row+col)
    # self-repair under shared-bus, generated through the REAL
    # generate_from_config path -- proves the col-repair validation
    # relaxation above actually reaches working, elaboratable RTL.
    config = {
        **BASE,
        "memory_name": "sram_spares_col_tiny",
        "addr_width": 2,
        "data_width": 4,
        "ports": {**BASE["ports"], "we": "web0", "spare_wen": "spare_wen0"},
        "redundancy": {
            "num_spare_rows": 1, "num_spare_cols": 1,
            "onchip_selfrepair": True, "onchip_col_repair": True,
        },
    }
    config_path = _write_config(tmp_path, config)
    wrapper_path = generate_from_config(config_path, tmp_path / "out")
    module_outdir = wrapper_path.parent

    algo_dir = module_outdir / "march_c"
    redundancy_files = [
        module_outdir / name
        for name in (
            "onchip_2d_repair_analyzer.sv", "onchip_selfrepair_ctrl.sv",
            "repair_remap_row.sv", "repair_remap_col.sv",
        )
    ]
    sram_fixture = REPO_ROOT / "tests" / "hardware" / "sram_spares_col_tiny.v"
    sources = [wrapper_path, *sorted(algo_dir.glob("*.sv")), *redundancy_files, sram_fixture]

    result = subprocess.run(
        [
            "verilator", "--lint-only", "--timing",
            "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND", "-Wno-UNUSED", "-Wno-DECLFILENAME",
            "-Wno-PINMISSING", "-Wno-PINCONNECTEMPTY", "-Wno-BLKSEQ",
            # onchip_2d_repair_analyzer.sv's already_col_covered/claimed are
            # loop-scoped combinational temporaries, always assigned before
            # being read within the SAME iteration -- Verilator's local,
            # per-always-block reachability check can't see that and infers
            # a (non-existent) latch. Pre-existing in already-shipped,
            # already Icarus-functionally-proven RTL (test_onchip_col_repair_e2e.py
            # et al.); this is simply the first time this file has been run
            # through Verilator lint at all. Unrelated to this feature.
            "-Wno-LATCH",
            *[str(s) for s in sources],
        ],
        capture_output=True, text=True, cwd=module_outdir,
    )
    assert result.returncode == 0, f"Verilator lint failed:\n{result.stdout}\n{result.stderr}"

    # Exactly one analyzer/remap-col instantiation site per memory (the
    # generate loop's own text, not per-elaborated-instance) -- guards
    # against the same class of duplicate-instantiation bug the row-only
    # work found and fixed.
    rendered = wrapper_path.read_text(encoding="utf-8")
    assert rendered.count("onchip_2d_repair_analyzer #(") == 1
    assert rendered.count("onchip_row_repair_analyzer #(") == 0
    assert rendered.count("repair_remap_col #(") == 1
    assert rendered.count("repair_remap_row #(") == 1


def test_shared_bus_rejects_use_saboteur(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, BASE)
    with pytest.raises(ConfigError, match="use_saboteur=True"):
        generate_from_config(config_path, tmp_path / "out", use_saboteur=True)


def test_shared_bus_rejects_tester_driven_redundancy(tmp_path: Path) -> None:
    # Tester-driven redundancy (no onchip_selfrepair) is still rejected: the
    # repair_ports pins would bind to a single physical remap, meaningless
    # when N memories share the bus. Plain on-chip row self-repair IS
    # supported -- see test_generated_shared_bus_selfrepair_wrapper_elaborates_cleanly_with_real_verilator.
    config = {
        **BASE,
        "redundancy": {"num_spare_rows": 1},
        "repair_ports": [{"name": "row_repair_en", "width": 1, "dir": "input"},
                          {"name": "faulty_row_addr", "width": 6, "dir": "input"}],
    }
    config_path = _write_config(tmp_path, config)
    with pytest.raises(ConfigError, match="shared-bus supports on-chip row self-repair"):
        generate_from_config(config_path, tmp_path / "out")


def test_shared_bus_rejects_onchip_repair_persistence(tmp_path: Path) -> None:
    # Persisted-repair-signature load (fuse_row_repair_en/fuse_faulty_row_addr)
    # isn't wired per-memory in the shared-bus generate loop -- see
    # docs/shared-hierarchical-mbist-plan.md §9b.
    config = {
        **BASE,
        "redundancy": {
            "num_spare_rows": 1, "onchip_selfrepair": True, "onchip_repair_persistence": True,
        },
    }
    config_path = _write_config(tmp_path, config)
    with pytest.raises(ConfigError, match="shared-bus supports on-chip row self-repair"):
        generate_from_config(config_path, tmp_path / "out")


def test_shared_bus_rejects_onchip_diagnosis(tmp_path: Path) -> None:
    # The diagnosis log isn't wired per-memory in the shared-bus generate
    # loop -- see docs/shared-hierarchical-mbist-plan.md §9b.
    config = {
        **BASE,
        "redundancy": {
            "num_spare_rows": 1, "onchip_selfrepair": True,
            "onchip_diagnosis": True, "num_diagnosis_entries": 4,
        },
    }
    config_path = _write_config(tmp_path, config)
    with pytest.raises(ConfigError, match="shared-bus supports on-chip row self-repair"):
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
