from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import autombist.runner as runner_mod
from autombist.generator import generate_from_config
from autombist.runner import (
    SimulationError,
    _build_clean_command,
    _build_fault_command,
    _find_hardware_dir,
    _load_simulation_config,
    _parse_makefile_metadata,
    run_controller_grading,
    run_simulation,
)


def _write_yaml(path: Path, content: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")


def _base_config() -> dict[str, object]:
    return {
        "memory_name": "sram_1rw",
        "wrapper_module_name": "sram_1rw_mbist",
        "addr_width": 10,
        "data_width": 32,
        "we_active_low": True,
        "ports": {
            "clk": "clk0",
            "addr": "addr0",
            "din": "din0",
            "dout": "dout0",
            "we": "we0",
            "csb": "csb0",
        },
    }


def test_run_simulation_uses_clean_path_when_config_disables_saboteur(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, _base_config())
    wrapper_path = generate_from_config(config_path, outdir, use_saboteur=False)
    module_outdir = wrapper_path.parent

    # Simulate stale artifacts from a previous fault generation.
    (module_outdir / "Makefile").write_text("all:\n\t@echo stale\n", encoding="utf-8")
    (module_outdir / "sram_1rw_saboteur.v").write_text("// stale saboteur\n", encoding="utf-8")

    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("autombist.runner.subprocess.run", fake_run)

    result = run_simulation(module_outdir, verbose=False)
    assert result.returncode == 0
    assert "fault-test" not in captured["command"]
    assert "USE_SABOTEUR=0" in captured["command"]


def test_run_simulation_legacy_outputs_still_use_fault_makefile(
    tmp_path: Path, monkeypatch
) -> None:
    module_outdir = tmp_path / "legacy"
    module_outdir.mkdir(parents=True)
    (module_outdir / "sram_1rw_mbist.v").write_text("module sram_1rw_mbist; endmodule\n", encoding="utf-8")
    (module_outdir / "sram_1rw_saboteur.v").write_text("module sram_1rw_saboteur; endmodule\n", encoding="utf-8")
    (module_outdir / "Makefile").write_text("fault-test:\n\t@echo legacy\n", encoding="utf-8")

    # Legacy config without autombist_* metadata.
    (module_outdir / "config.yml").write_text(
        yaml.safe_dump(
            {
                "memory_name": "sram_1rw",
                "wrapper_module_name": "sram_1rw_mbist",
                "addr_width": 10,
                "data_width": 32,
                "we_active_low": True,
                "ports": {
                    "clk": "clk0",
                    "addr": "addr0",
                    "din": "din0",
                    "dout": "dout0",
                    "we": "we0",
                    "csb": "csb0",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("autombist.runner.subprocess.run", fake_run)

    result = run_simulation(module_outdir, verbose=False)
    assert result.returncode == 0
    assert captured["command"][-1] == "sim"
    assert "USE_SABOTEUR=1" in captured["command"]
    assert "FAULT_MODE=faults" in captured["command"]
    assert any(token.startswith("PYTHON_BIN=") for token in captured["command"])


# ---------------------------------------------------------------------------
# _find_hardware_dir: final error-raise branch (cluster 1)
# ---------------------------------------------------------------------------


def test_find_hardware_dir_raises_when_no_candidate_exists(tmp_path: Path, monkeypatch) -> None:
    # Make the package-relative and dev-project-relative candidates resolve
    # under tmp_path, which has no tests/hardware anywhere in it.
    fake_file = tmp_path / "pkg" / "src" / "autombist" / "fake_runner.py"
    fake_file.parent.mkdir(parents=True)
    monkeypatch.setattr(runner_mod, "__file__", str(fake_file))

    # cwd-and-parents search must also fail to find a real tests/hardware,
    # so chdir deep enough that parent traversal cannot reach the real repo.
    deep_cwd = tmp_path / "deepnest" / "a" / "b" / "c"
    deep_cwd.mkdir(parents=True)
    monkeypatch.chdir(deep_cwd)

    with pytest.raises(SimulationError, match="Hardware simulation directory not found"):
        runner_mod._find_hardware_dir()


# ---------------------------------------------------------------------------
# _parse_makefile_metadata (cluster 2)
# ---------------------------------------------------------------------------


def test_parse_makefile_metadata_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    module_outdir = tmp_path / "nomake"
    module_outdir.mkdir()
    assert _parse_makefile_metadata(module_outdir) == {}


def test_parse_makefile_metadata_valid_entries(tmp_path: Path) -> None:
    module_outdir = tmp_path / "mk"
    module_outdir.mkdir()
    (module_outdir / "Makefile").write_text(
        "MEMORY_NAME := sram_1rw\n"
        "ADDR_WIDTH := 10\n",
        encoding="utf-8",
    )
    metadata = _parse_makefile_metadata(module_outdir)
    assert metadata["MEMORY_NAME"] == "sram_1rw"
    assert metadata["ADDR_WIDTH"] == "10"


def test_parse_makefile_metadata_duplicate_key_first_wins(tmp_path: Path) -> None:
    module_outdir = tmp_path / "dupe"
    module_outdir.mkdir()
    (module_outdir / "Makefile").write_text(
        "MEMORY_NAME := first_value\n"
        "MEMORY_NAME := second_value\n",
        encoding="utf-8",
    )
    metadata = _parse_makefile_metadata(module_outdir)
    assert metadata["MEMORY_NAME"] == "first_value"


def test_parse_makefile_metadata_skips_nested_make_variable_reference(tmp_path: Path) -> None:
    module_outdir = tmp_path / "nested"
    module_outdir.mkdir()
    (module_outdir / "Makefile").write_text(
        "OUTDIR := $(PROJECT_ROOT)/out\n"
        "MEMORY_NAME := sram_1rw\n",
        encoding="utf-8",
    )
    metadata = _parse_makefile_metadata(module_outdir)
    assert "OUTDIR" not in metadata
    assert metadata["MEMORY_NAME"] == "sram_1rw"


def test_parse_makefile_metadata_strips_trailing_backslash_continuation(tmp_path: Path) -> None:
    module_outdir = tmp_path / "cont"
    module_outdir.mkdir()
    (module_outdir / "Makefile").write_text(
        "FLAGS := -a -b \\\n",
        encoding="utf-8",
    )
    metadata = _parse_makefile_metadata(module_outdir)
    assert metadata["FLAGS"] == "-a -b"


def test_parse_makefile_metadata_skips_non_matching_line(tmp_path: Path) -> None:
    module_outdir = tmp_path / "junk"
    module_outdir.mkdir()
    (module_outdir / "Makefile").write_text(
        "# just a comment, no assignment\n"
        "this is not a makefile assignment at all\n"
        "MEMORY_NAME := sram_1rw\n",
        encoding="utf-8",
    )
    metadata = _parse_makefile_metadata(module_outdir)
    assert metadata == {"MEMORY_NAME": "sram_1rw"}


# ---------------------------------------------------------------------------
# _load_simulation_config fallback path (cluster 3)
# ---------------------------------------------------------------------------


def _write_fallback_makefile(module_outdir: Path) -> None:
    (module_outdir / "Makefile").write_text(
        "MEMORY_NAME := sram_1rw\n"
        "WRAPPER_MODULE := sram_1rw_mbist\n"
        "ADDR_WIDTH := 10\n"
        "DATA_WIDTH := 32\n"
        "USE_SABOTEUR := 1\n"
        "FAULTS := 5\n"
        "FAULT_SEED := 42\n"
        "FAULT_TYPE := stuck-at\n"
        "PULSE_WIDTH_NS := 3\n"
        "ALGO := march-c\n",
        encoding="utf-8",
    )


def test_load_simulation_config_fallback_uses_makefile_metadata(tmp_path: Path) -> None:
    module_outdir = tmp_path / "fallback"
    module_outdir.mkdir()
    _write_fallback_makefile(module_outdir)
    (module_outdir / "sram_1rw_mbist.v").write_text(
        "module sram_1rw_mbist; assign sram_we = selected_write_req; endmodule\n",
        encoding="utf-8",
    )

    config = _load_simulation_config(module_outdir)

    assert config["memory_name"] == "sram_1rw"
    assert config["wrapper_module_name"] == "sram_1rw_mbist"
    assert config["addr_width"] == 10
    assert config["data_width"] == 32
    assert config["autombist_use_saboteur"] is True
    assert config["autombist_faults"] == 5
    assert config["autombist_fault_seed"] == 42
    assert config["autombist_fault_type"] == "stuck-at"
    assert config["autombist_pulse_width_ns"] == 3
    assert config["autombist_algo"] == "march-c"


def test_load_simulation_config_detects_active_low_write_enable(tmp_path: Path) -> None:
    module_outdir = tmp_path / "activelow"
    module_outdir.mkdir()
    _write_fallback_makefile(module_outdir)
    (module_outdir / "sram_1rw_mbist.v").write_text(
        "module sram_1rw_mbist;\n"
        "    assign sram_we = ~selected_write_req;\n"
        "endmodule\n",
        encoding="utf-8",
    )

    config = _load_simulation_config(module_outdir)
    assert config["we_active_low"] is True


def test_load_simulation_config_detects_active_high_write_enable(tmp_path: Path) -> None:
    module_outdir = tmp_path / "activehigh"
    module_outdir.mkdir()
    _write_fallback_makefile(module_outdir)
    (module_outdir / "sram_1rw_mbist.v").write_text(
        "module sram_1rw_mbist;\n"
        "    assign sram_we = selected_write_req;\n"
        "endmodule\n",
        encoding="utf-8",
    )

    config = _load_simulation_config(module_outdir)
    assert config["we_active_low"] is False


def test_load_simulation_config_raises_when_no_wrapper_present(tmp_path: Path) -> None:
    module_outdir = tmp_path / "nowrapper"
    module_outdir.mkdir()
    _write_fallback_makefile(module_outdir)

    with pytest.raises(FileNotFoundError, match="Generated wrapper not found"):
        _load_simulation_config(module_outdir)


# ---------------------------------------------------------------------------
# _build_clean_command / _build_fault_command project_root defaults (clusters 4-6)
# ---------------------------------------------------------------------------


def test_build_clean_command_default_project_root(tmp_path: Path) -> None:
    expected_project_root = Path(runner_mod.__file__).resolve().parents[2]
    command = _build_clean_command(
        hardware_dir=tmp_path / "hw",
        module_outdir=tmp_path / "out" / "mod",
        config=_base_config(),
        algo="march-c",
    )
    assert f"PROJECT_ROOT={expected_project_root}" in command


def test_build_fault_command_default_project_root(tmp_path: Path) -> None:
    expected_project_root = Path(runner_mod.__file__).resolve().parents[2]
    command = _build_fault_command(
        hardware_dir=tmp_path / "hw",
        module_outdir=tmp_path / "out" / "mod",
        config=_base_config(),
        faults=5,
        fault_seed=1,
        fault_type="stuck-at",
        pulse_width_ns=2,
        algo="march-c",
    )
    assert f"PROJECT_ROOT={expected_project_root}" in command


def test_build_fault_command_without_seed_omits_fault_seed_token(tmp_path: Path) -> None:
    command = _build_fault_command(
        hardware_dir=tmp_path / "hw",
        module_outdir=tmp_path / "out" / "mod",
        config=_base_config(),
        faults=5,
        fault_seed=None,
        fault_type="stuck-at",
        pulse_width_ns=2,
        algo="march-c",
        project_root=tmp_path,
    )
    assert not any(token.startswith("FAULT_SEED=") for token in command)


def test_build_fault_command_with_seed_includes_fault_seed_token(tmp_path: Path) -> None:
    command = _build_fault_command(
        hardware_dir=tmp_path / "hw",
        module_outdir=tmp_path / "out" / "mod",
        config=_base_config(),
        faults=5,
        fault_seed=99,
        fault_type="stuck-at",
        pulse_width_ns=2,
        algo="march-c",
        project_root=tmp_path,
    )
    assert "FAULT_SEED=99" in command


# ---------------------------------------------------------------------------
# run_simulation error paths (clusters 7, 8, 10)
# ---------------------------------------------------------------------------


def test_run_simulation_raises_when_wrapper_missing_on_disk(tmp_path: Path, monkeypatch) -> None:
    module_outdir = tmp_path / "out"
    module_outdir.mkdir()
    config = _base_config()
    _write_yaml(module_outdir / "config.yml", config)
    # Deliberately do NOT create sram_1rw_mbist.v

    def fake_run(command, **kwargs):
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr("autombist.runner.subprocess.run", fake_run)

    with pytest.raises(SimulationError, match="Generated wrapper not found"):
        run_simulation(module_outdir, verbose=False)


def test_run_simulation_raises_when_saboteur_wrapper_missing_on_disk(
    tmp_path: Path, monkeypatch
) -> None:
    module_outdir = tmp_path / "out"
    module_outdir.mkdir()
    config = _base_config()
    config["autombist_use_saboteur"] = True
    _write_yaml(module_outdir / "config.yml", config)
    (module_outdir / "sram_1rw_mbist.v").write_text(
        "module sram_1rw_mbist; endmodule\n", encoding="utf-8"
    )
    # Deliberately do NOT create sram_1rw_saboteur.v

    def fake_run(command, **kwargs):
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr("autombist.runner.subprocess.run", fake_run)

    with pytest.raises(SimulationError, match="Saboteur wrapper not found"):
        run_simulation(module_outdir, verbose=False)


def test_run_simulation_failure_hint_when_fault_sim_log_has_trigger_substring(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, _base_config())
    wrapper_path = generate_from_config(config_path, outdir, use_saboteur=True)
    module_outdir = wrapper_path.parent

    # Pre-create fault_sim.log on disk with the trigger substring; run_simulation
    # reads this from disk after the subprocess call completes.
    (module_outdir / "fault_sim.log").write_text(
        "ERROR: scope 'top' contains no child object named dbg_actual_word\n",
        encoding="utf-8",
    )

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(args=command, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr("autombist.runner.subprocess.run", fake_run)

    with pytest.raises(SimulationError) as exc_info:
        run_simulation(module_outdir, verbose=False)

    assert "autombist generate --test" in str(exc_info.value)


# ---------------------------------------------------------------------------
# run_controller_grading (cluster 11)
# ---------------------------------------------------------------------------


def test_run_controller_grading_delegates_to_faultflow_flow(tmp_path: Path, monkeypatch) -> None:
    module_outdir = tmp_path / "out"
    module_outdir.mkdir()
    opts = object()

    captured: dict[str, object] = {}

    def fake_grade_controller(outdir, opts_arg, *, run=True):
        captured["outdir"] = outdir
        captured["opts"] = opts_arg
        captured["run"] = run
        return {"coverage": 100.0}

    monkeypatch.setattr(
        "autombist.faultflow_flow.grade_controller", fake_grade_controller
    )

    result = run_controller_grading(module_outdir, opts, run=True)

    assert result == {"coverage": 100.0}
    assert captured["outdir"] == module_outdir
    assert captured["opts"] is opts
    assert captured["run"] is True
