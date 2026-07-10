from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
import yaml
from typer.testing import CliRunner

# Strip ANSI/rich escape sequences so help-text assertions don't depend on
# whether the CLI is rendering colored output (a real difference between an
# interactive local TTY and CI's non-TTY environment).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist import ConfigError, __version__, generate_from_config, load_config
from autombist.main import app

runner = CliRunner()


@pytest.fixture
def base_config() -> dict[str, object]:
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


def _write_yaml(path: Path, content: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")


def test_generate_wrapper_and_package_rtl(tmp_path: Path, base_config: dict[str, object]) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    wrapper_path = generate_from_config(config_path, outdir)

    module_outdir = outdir / "sram_1rw"

    assert wrapper_path == module_outdir / "sram_1rw_mbist.v"
    assert wrapper_path.exists()

    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "module sram_1rw_mbist" in wrapper_text
    assert ".clk0(mbist_sram_clk)" in wrapper_text
    assert ".csb0(sram_csb)" in wrapper_text
    assert "march_c_top" in wrapper_text

    assert (module_outdir / "sram_model.sv").exists(), "Missing copied RTL file: sram_model.sv"
    for rtl_name in ("mbist_algo.sv", "mbist_fsm.sv", "mbist_top.sv"):
        assert not (module_outdir / rtl_name).exists(), f"Legacy RTL file should not be copied: {rtl_name}"

    for rtl_name in (
        "march_c/march_c_algo.sv",
        "march_c/march_c_fsm.sv",
        "march_c/march_c_top.sv",
    ):
        assert (module_outdir / rtl_name).exists(), f"Missing copied algorithm RTL file: {rtl_name}"

    # copy_mbist_rtl is selective: only the chosen algo's RTL is copied.
    for rtl_name in (
        "march_raw/march_raw_algo.sv",
        "march_raw/march_raw_fsm.sv",
        "march_raw/march_raw_top.sv",
    ):
        assert not (module_outdir / rtl_name).exists(), f"Unselected algorithm RTL should not be copied: {rtl_name}"

    assert not (module_outdir / "Makefile").exists()
    assert (module_outdir / "config.yml").exists()


def test_missing_required_key_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    invalid = dict(base_config)
    invalid.pop("memory_name")

    config_path = tmp_path / "missing_memory_name.yml"
    _write_yaml(config_path, invalid)

    with pytest.raises(ConfigError, match="memory_name"):
        load_config(config_path)


def test_missing_required_port_key_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    invalid = dict(base_config)
    ports = cast(dict[str, str], invalid["ports"])
    ports.pop("csb")
    invalid["ports"] = ports

    config_path = tmp_path / "missing_port_key.yml"
    _write_yaml(config_path, invalid)

    with pytest.raises(ConfigError, match="csb"):
        load_config(config_path)


def test_invalid_boolean_type_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    invalid = dict(base_config)
    invalid["we_active_low"] = "true"

    config_path = tmp_path / "invalid_bool.yml"
    _write_yaml(config_path, invalid)

    with pytest.raises(ConfigError, match="we_active_low"):
        load_config(config_path)


def test_generate_with_saboteur_creates_fault_assets(
    tmp_path: Path, base_config: dict[str, object]
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    wrapper_path = generate_from_config(
        config_path,
        outdir,
        use_saboteur=True,
        faults=5,
        fault_seed=7,
    )

    assert wrapper_path.exists()

    module_outdir = outdir / "sram_1rw"

    saboteur_path = module_outdir / "sram_1rw_saboteur.v"
    assert saboteur_path.exists()
    assert (module_outdir / "Makefile").exists()

    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "sram_1rw_saboteur" in wrapper_text

    sa0_path = module_outdir / "faults" / "sa0_faults.hex"
    sa1_path = module_outdir / "faults" / "sa1_faults.hex"
    assert sa0_path.exists()
    assert sa1_path.exists()

    sa0_lines = sa0_path.read_text(encoding="ascii").splitlines()
    sa1_lines = sa1_path.read_text(encoding="ascii").splitlines()

    expected_depth = 1 << int(cast(int, base_config["addr_width"]))
    expected_hex_width = (int(cast(int, base_config["data_width"])) + 3) // 4

    assert len(sa0_lines) == expected_depth
    assert len(sa1_lines) == expected_depth
    assert all(len(line) == expected_hex_width for line in sa0_lines)
    assert all(len(line) == expected_hex_width for line in sa1_lines)


def test_negative_faults_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    with pytest.raises(ValueError, match="faults"):
        generate_from_config(config_path, outdir, use_saboteur=True, faults=-1)


def test_cli_help_mentions_version_and_options() -> None:
    # Force a wide, uncolored render so rich/typer doesn't wrap option names
    # across lines or interleave ANSI codes -- CI runs without a TTY at a
    # narrow default width, which used to split "--version" and fail the
    # substring checks even though the help text was correct.
    result = runner.invoke(
        app, ["--help"], env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}
    )

    assert result.exit_code == 0
    output = _plain(result.output)
    assert "--version" in output
    assert "generate" in output
    assert "simulate" in output
    assert "run" in output
    assert "ram-synth" in output
    assert "init" in output
    assert "smoke" in output


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_generate_uses_local_config_yaml(tmp_path: Path, base_config: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["generate", "--out", str(outdir)])

    assert result.exit_code == 0
    assert (outdir / "sram_1rw" / "sram_1rw_mbist.v").exists()


def test_cli_generate_errors_without_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["generate", "--out", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert isinstance(result.exception, FileNotFoundError)
    assert "Config file not found" in str(result.exception)


def test_cli_generate_defaults(tmp_path: Path, base_config: dict[str, object]) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    result = runner.invoke(app, ["generate", "--config", str(config_path), "--out", str(outdir)])

    assert result.exit_code == 0
    assert (outdir / "sram_1rw" / "sram_1rw_mbist.v").exists()


def test_cli_generate_test_mode(tmp_path: Path, base_config: dict[str, object]) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    result = runner.invoke(
        app,
        ["generate", "--config", str(config_path), "--out", str(outdir), "--test", "--faults", "8", "--seed", "9"],
    )

    assert result.exit_code == 0
    module_outdir = outdir / "sram_1rw"
    assert (module_outdir / "Makefile").exists()
    assert (module_outdir / "faults" / "sa0_faults.hex").exists()


def test_cli_simulate_reports_summary(
    tmp_path: Path, base_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)
    generate_from_config(config_path, outdir)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="PASS\n", stderr="")

    monkeypatch.setattr("autombist.runner.subprocess.run", fake_run)

    result = runner.invoke(app, ["simulate", "--out", str(outdir)])

    assert result.exit_code == 0
    assert "simulation PASS" in result.output
    assert (outdir / "sram_1rw" / "simulate.log").exists()


def test_generated_fault_makefile_has_simple_targets(
    tmp_path: Path, base_config: dict[str, object]
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    generate_from_config(
        config_path,
        outdir,
        use_saboteur=True,
        faults=8,
        fault_seed=21,
    )

    makefile_path = outdir / "sram_1rw" / "Makefile"
    makefile_text = makefile_path.read_text(encoding="utf-8")

    assert "all: fault-test" in makefile_text
    assert "fault-test:" in makefile_text
    assert "debug: precheck" in makefile_text
    assert "Python3 not found at $(PYTHON_BIN). Maybe use venv? or install Python3." in makefile_text
    assert "$(MAKE) -C $(PROJECT_ROOT)/tests/hardware" in makefile_text
    assert "PYTHON_BIN=$(PYTHON_BIN)" in makefile_text
    assert "SIM_LOG ?= fault_sim.log" in makefile_text
    assert "ALGO=march-c" in makefile_text
    assert "FAULT_TYPE := stuck-at" in makefile_text
    assert "PULSE_WIDTH_NS := 2" in makefile_text


def test_generate_transition_up_faults(tmp_path: Path, base_config: dict[str, object]) -> None:
    """Verify transition-up fault generation creates proper hex files."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    wrapper_path = generate_from_config(
        config_path,
        outdir,
        use_saboteur=True,
        faults=10,
        fault_seed=42,
        fault_type="transition-up",
    )

    assert wrapper_path.exists()

    module_outdir = outdir / "sram_1rw"

    tf_up_path = module_outdir / "faults" / "tf_up_faults.hex"
    assert tf_up_path.exists(), "transition-up fault file not generated"
    assert not (module_outdir / "faults" / "sa0_faults.hex").exists()
    assert not (module_outdir / "faults" / "sa1_faults.hex").exists()

    saboteur_text = (module_outdir / "sram_1rw_saboteur.v").read_text(encoding="utf-8")
    assert "tf_up_mask" in saboteur_text
    assert "shadow_mem" in saboteur_text
    assert "sa0_mask" not in saboteur_text

    tf_up_lines = tf_up_path.read_text(encoding="ascii").splitlines()
    expected_depth = 1 << int(cast(int, base_config["addr_width"]))
    expected_hex_width = (int(cast(int, base_config["data_width"])) + 3) // 4

    assert len(tf_up_lines) == expected_depth
    assert all(len(line) == expected_hex_width for line in tf_up_lines)


def test_generate_transition_down_faults(tmp_path: Path, base_config: dict[str, object]) -> None:
    """Verify transition-down fault generation creates proper hex files."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    wrapper_path = generate_from_config(
        config_path,
        outdir,
        use_saboteur=True,
        faults=15,
        fault_seed=123,
        fault_type="transition-down",
    )

    assert wrapper_path.exists()

    module_outdir = outdir / "sram_1rw"

    tf_down_path = module_outdir / "faults" / "tf_down_faults.hex"
    assert tf_down_path.exists(), "transition-down fault file not generated"
    assert not (module_outdir / "faults" / "sa0_faults.hex").exists()
    assert not (module_outdir / "faults" / "sa1_faults.hex").exists()

    saboteur_text = (module_outdir / "sram_1rw_saboteur.v").read_text(encoding="utf-8")
    assert "tf_down_mask" in saboteur_text
    assert "shadow_mem" in saboteur_text
    assert "sa0_mask" not in saboteur_text

    tf_down_lines = tf_down_path.read_text(encoding="ascii").splitlines()
    expected_depth = 1 << int(cast(int, base_config["addr_width"]))
    expected_hex_width = (int(cast(int, base_config["data_width"])) + 3) // 4

    assert len(tf_down_lines) == expected_depth
    assert all(len(line) == expected_hex_width for line in tf_down_lines)


def test_invalid_fault_type_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    """Verify invalid fault types are rejected."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    with pytest.raises(ValueError, match="Invalid fault_type"):
        generate_from_config(
            config_path,
            outdir,
            use_saboteur=True,
            faults=5,
            fault_type="invalid-fault-type",
        )


def test_cli_transition_fault_flags(tmp_path: Path, base_config: dict[str, object]) -> None:
    """Verify CLI accepts transition fault type and pulse width flags."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    result = runner.invoke(
        app,
        [
            "generate",
            "--config", str(config_path),
            "--out", str(outdir),
            "--test",
            "--fault-type", "transition-up",
            "--pulse-width-ns", "3",
            "--algo", "march-raw",
        ],
    )

    assert result.exit_code == 0
    module_outdir = outdir / "sram_1rw"
    assert (module_outdir / "faults" / "tf_up_faults.hex").exists()
    assert (module_outdir / "sram_1rw_mbist.v").read_text(encoding="utf-8").count("march_raw_top") == 1


def test_transition_fault_files_have_expected_depth_and_width(
    tmp_path: Path, base_config: dict[str, object]
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    generate_from_config(
        config_path,
        outdir,
        use_saboteur=True,
        faults=12,
        fault_seed=99,
        fault_type="transition-up",
    )

    module_outdir = outdir / "sram_1rw"
    tf_up_path = module_outdir / "faults" / "tf_up_faults.hex"
    tf_lines = tf_up_path.read_text(encoding="ascii").splitlines()

    expected_depth = 1 << int(cast(int, base_config["addr_width"]))
    expected_hex_width = (int(cast(int, base_config["data_width"])) + 3) // 4

    assert len(tf_lines) == expected_depth
    assert all(len(line) == expected_hex_width for line in tf_lines)


def test_cli_march_raw_algo_accepted(tmp_path: Path, base_config: dict[str, object]) -> None:
    """Verify CLI accepts march-raw algorithm selection."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    result = runner.invoke(
        app,
        [
            "generate",
            "--config", str(config_path),
            "--out", str(outdir),
            "--algo", "march-raw",
        ],
    )

    assert result.exit_code == 0
    wrapper_text = (outdir / "sram_1rw" / "sram_1rw_mbist.v").read_text(encoding="utf-8")
    assert "march_raw_top" in wrapper_text


def test_invalid_algo_raises(tmp_path: Path, base_config: dict[str, object]) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    with pytest.raises(ValueError, match="algo must be one of"):
        generate_from_config(config_path, outdir, algo="invalid-algo")


def test_cli_init_creates_scaffold_files(tmp_path: Path) -> None:
    outdir = tmp_path / "starter"
    result = runner.invoke(app, ["init", "--out", str(outdir)])

    assert result.exit_code == 0
    assert (outdir / "config.yml").exists()
    assert (outdir / "openram.yml").exists()
    assert (outdir / "Makefile").exists()

    openram_cfg = yaml.safe_load((outdir / "openram.yml").read_text(encoding="utf-8"))
    assert openram_cfg["tech"] == "scn4m_subm"
    assert openram_cfg["word_size"] == 32


def test_cli_init_refuses_overwrite_without_force(tmp_path: Path) -> None:
    outdir = tmp_path / "starter"
    outdir.mkdir(parents=True)
    (outdir / "config.yml").write_text("memory_name: keep_me\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--out", str(outdir)])
    assert result.exit_code == 1
    assert "Refusing to overwrite existing file" in result.output


def test_cli_init_overwrites_with_force(tmp_path: Path) -> None:
    outdir = tmp_path / "starter"
    outdir.mkdir(parents=True)
    (outdir / "config.yml").write_text("memory_name: keep_me\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--out", str(outdir), "--force"])
    assert result.exit_code == 0
    rendered = yaml.safe_load((outdir / "config.yml").read_text(encoding="utf-8"))
    assert rendered["memory_name"] == "sram_1rw"


def test_cli_ram_synth_uses_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    openram_cfg_path = tmp_path / "openram.yml"
    openram_cfg_path.write_text(
        yaml.safe_dump(
            {
                "tech": "scn4m_subm",
                "word_size": 8,
                "num_words": 16,
                "num_rw_ports": 1,
                "num_r_ports": 0,
                "num_w_ports": 0,
                "output_root": "input",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    captured: dict[str, Path] = {}

    def fake_run_openram(config_path: Path):
        captured["config_path"] = config_path
        return subprocess.CompletedProcess(args=["python3", "scripts/synthesize_sram.py"], returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("autombist.cli.run_openram_synthesis", fake_run_openram)

    result = runner.invoke(app, ["ram-synth", "--config", str(openram_cfg_path)])
    assert result.exit_code == 0
    assert captured["config_path"] == openram_cfg_path.resolve()
    assert "ok" in result.output


def test_cli_smoke_no_sim_passes(tmp_path: Path) -> None:
    outdir = tmp_path / "smoke-out"
    result = runner.invoke(app, ["smoke", "--no-sim", "--out", str(outdir)])

    assert result.exit_code == 0
    assert "[smoke] generate (clean, march-c): PASS" in result.output
    assert "[smoke] generate (clean, march-raw): PASS" in result.output
    assert "[smoke] generate (stuck-at, march-c): PASS" in result.output
    assert "[smoke] generate (transition-up, march-raw): PASS" in result.output
    assert "[smoke] generate (transition-down, march-raw): PASS" in result.output
    assert "[smoke] ram-synth config parse: PASS" in result.output
    assert "[smoke] All checks passed" in result.output
    assert (outdir / "config.yml").exists()
    assert (outdir / "openram.yml").exists()


def test_cli_smoke_with_sim_runs_fault_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outdir = tmp_path / "smoke-out"
    calls: list[tuple[str, str, int, bool]] = []

    def fake_simulate(module_outdir: Path, verbose: bool) -> None:
        assert verbose is False
        snapshot = yaml.safe_load((module_outdir / "config.yml").read_text(encoding="utf-8"))
        calls.append(
            (
                str(snapshot["autombist_fault_type"]),
                str(snapshot["autombist_algo"]),
                int(snapshot["autombist_faults"]),
                bool(snapshot["autombist_use_saboteur"]),
            )
        )

    monkeypatch.setattr("autombist.cli._simulate", fake_simulate)

    result = runner.invoke(app, ["smoke", "--out", str(outdir)])

    assert result.exit_code == 0
    assert calls == [
        ("stuck-at", "march-c", 8, True),
        ("transition-up", "march-raw", 8, True),
        ("transition-down", "march-raw", 8, True),
    ]
    assert "[smoke] simulate (stuck-at, march-c, faults=8): PASS" in result.output
    assert "[smoke] simulate (transition-up, march-raw, faults=8): PASS" in result.output
    assert "[smoke] simulate (transition-down, march-raw, faults=8): PASS" in result.output


# ---------------------------------------------------------------------------
# Additional coverage: _resolve_module_outdir edge cases
# ---------------------------------------------------------------------------


def test_cli_simulate_missing_out_dir_errors(tmp_path: Path) -> None:
    """_resolve_module_outdir raises FileNotFoundError when --out doesn't exist."""
    missing = tmp_path / "does-not-exist"

    result = runner.invoke(app, ["simulate", "--out", str(missing)])

    assert result.exit_code == 1
    assert "autombist:" in result.output


def test_cli_simulate_empty_out_dir_errors(tmp_path: Path) -> None:
    """_resolve_module_outdir raises 'No generated autombist output found' for an empty dir."""
    empty = tmp_path / "out"
    empty.mkdir()

    result = runner.invoke(app, ["simulate", "--out", str(empty)])

    assert result.exit_code == 1
    assert "autombist:" in result.output
    assert "No generated autombist output found" in result.output


def test_cli_simulate_multiple_candidates_errors(tmp_path: Path) -> None:
    """_resolve_module_outdir raises when >1 generated memory directory is found under --out."""
    outdir = tmp_path / "out"
    for name in ("mem_a", "mem_b"):
        module_dir = outdir / name
        module_dir.mkdir(parents=True)
        (module_dir / f"{name}_mbist.v").write_text("// stub\n", encoding="utf-8")
        (module_dir / "config.yml").write_text("memory_name: stub\n", encoding="utf-8")

    result = runner.invoke(app, ["simulate", "--out", str(outdir)])

    assert result.exit_code == 1
    assert "Multiple generated memory directories" in result.output


def test_cli_grade_controller_missing_out_dir_errors(tmp_path: Path) -> None:
    """grade-controller surfaces the same _resolve_module_outdir FileNotFoundError path."""
    missing = tmp_path / "does-not-exist"

    result = runner.invoke(app, ["grade-controller", "--out", str(missing)])

    assert result.exit_code == 1
    assert "autombist:" in result.output


# ---------------------------------------------------------------------------
# Additional coverage: _generate / _simulate exception catches
# ---------------------------------------------------------------------------


def test_cli_simulate_simulation_error_exits_1(
    tmp_path: Path, base_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """simulate command surfaces SimulationError raised by run_simulation as exit 1."""
    from autombist.runner import SimulationError as _SimulationError

    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)
    generate_from_config(config_path, outdir)

    def fake_run_simulation(module_outdir: Path, *, verbose: bool = False):
        raise _SimulationError("sim boom")

    monkeypatch.setattr("autombist.cli.run_simulation", fake_run_simulation)

    result = runner.invoke(app, ["simulate", "--out", str(outdir)])

    assert result.exit_code == 1
    assert "autombist: sim boom" in result.output


# ---------------------------------------------------------------------------
# Additional coverage: grade-controller command + run --faultflow path
# ---------------------------------------------------------------------------


def test_cli_grade_controller_reports_coverage_and_merges_report(
    tmp_path: Path, base_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)
    generate_from_config(config_path, outdir)
    module_outdir = outdir / "sram_1rw"

    # Seed reports/latest.json so the merge branch has something to merge into.
    reports_dir = module_outdir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    import json as _json

    (reports_dir / "latest.json").write_text(
        _json.dumps({"status": "pass", "config": {"memory_name": "sram_1rw"}}), encoding="utf-8"
    )

    fake_coverage = {
        "coverage_percent": 85.5,
        "detected": 17,
        "denominator": 20,
        "excluded_blackbox": 3,
    }

    def fake_run_controller_grading(module_outdir: Path, opts, *, run: bool = True):
        return fake_coverage

    monkeypatch.setattr("autombist.runner.run_controller_grading", fake_run_controller_grading)

    result = runner.invoke(app, ["grade-controller", "--out", str(outdir)])

    assert result.exit_code == 0
    assert "Controller structural coverage (FaultFlow)" in result.output
    assert "17/20" in result.output
    assert "85.50%" in result.output
    assert "excluded-blackbox=3" in result.output

    merged = _json.loads((reports_dir / "latest.json").read_text(encoding="utf-8"))
    assert merged.get("controller_grading") == fake_coverage


def test_cli_grade_controller_no_run_emits_bundle_message(
    tmp_path: Path, base_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)
    generate_from_config(config_path, outdir)

    def fake_run_controller_grading(module_outdir: Path, opts, *, run: bool = True):
        assert run is False
        return None

    monkeypatch.setattr("autombist.runner.run_controller_grading", fake_run_controller_grading)

    result = runner.invoke(app, ["grade-controller", "--out", str(outdir), "--no-run"])

    assert result.exit_code == 0
    assert "Emitted FaultFlow bundle" in result.output
    assert "Run on Linux/WSL" in result.output


def test_cli_grade_controller_faultflow_error_exits_1(
    tmp_path: Path, base_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from autombist.faultflow_flow import FaultFlowError

    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)
    generate_from_config(config_path, outdir)

    def fake_run_controller_grading(module_outdir: Path, opts, *, run: bool = True):
        raise FaultFlowError("boom")

    monkeypatch.setattr("autombist.runner.run_controller_grading", fake_run_controller_grading)

    result = runner.invoke(app, ["grade-controller", "--out", str(outdir)])

    assert result.exit_code == 1
    assert "autombist: boom" in result.output


def test_cli_run_with_faultflow_flag_invokes_grading(
    tmp_path: Path, base_config: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 'run --faultflow' path: generate + fake-simulate + fake controller grading."""
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, base_config)

    def fake_simulate(module_outdir: Path, verbose: bool, min_coverage=None) -> None:
        return None

    fake_coverage = {
        "coverage_percent": 91.0,
        "detected": 9,
        "denominator": 10,
        "excluded_blackbox": 0,
    }

    def fake_run_controller_grading(module_outdir: Path, opts, *, run: bool = True):
        assert run is True
        return fake_coverage

    monkeypatch.setattr("autombist.cli._simulate", fake_simulate)
    monkeypatch.setattr("autombist.runner.run_controller_grading", fake_run_controller_grading)

    result = runner.invoke(
        app,
        ["run", "--config", str(config_path), "--out", str(outdir), "--faultflow"],
    )

    assert result.exit_code == 0
    assert "Controller structural coverage (FaultFlow)" in result.output
    assert "9/10" in result.output


# ---------------------------------------------------------------------------
# Additional coverage: the algo-engine-backed 'test' command
# ---------------------------------------------------------------------------


def _write_faults_file(path: Path) -> None:
    path.write_text("SA0 1 0 0 0 0 0\n", encoding="utf-8")


def _fake_campaign_result():
    from autombist.algo_engine import CampaignResult, MemoryParams

    return CampaignResult(
        algo_name="march_c",
        mem=MemoryParams(8, 8),
        golden_clean=True,
        faults=[],
        detected=1,
        total=1,
        coverage_percent=100.0,
        build_seconds=0.1,
        run_seconds=0.1,
        sim="verilator",
    )


def test_cli_test_command_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    faults_path = tmp_path / "faults.txt"
    _write_faults_file(faults_path)

    def fake_run_algo_campaign(mem, spec, records, *, sim="verilator", verbose=False, fault_ram_sv=None):
        return _fake_campaign_result()

    monkeypatch.setattr("autombist.algo_engine.run_algo_campaign", fake_run_algo_campaign)

    result = runner.invoke(
        app,
        [
            "test",
            "--addr-width", "8",
            "--data-width", "8",
            "--algo", "march_c",
            "--faults", str(faults_path),
        ],
    )

    assert result.exit_code == 0
    assert "autombist test:" in result.output
    assert "coverage: 100.00%" in result.output


def test_cli_test_command_writes_report_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    faults_path = tmp_path / "faults.txt"
    _write_faults_file(faults_path)
    report_path = tmp_path / "cov.md"

    def fake_run_algo_campaign(mem, spec, records, *, sim="verilator", verbose=False, fault_ram_sv=None):
        return _fake_campaign_result()

    monkeypatch.setattr("autombist.algo_engine.run_algo_campaign", fake_run_algo_campaign)

    result = runner.invoke(
        app,
        [
            "test",
            "--addr-width", "8",
            "--data-width", "8",
            "--algo", "march_c",
            "--faults", str(faults_path),
            "--report", str(report_path),
            "--fmt", "md",
        ],
    )

    assert result.exit_code == 0
    assert report_path.exists()
    assert f"report: {report_path}" in result.output


def test_cli_test_command_min_coverage_gate_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autombist.algo_engine import CampaignResult, MemoryParams

    faults_path = tmp_path / "faults.txt"
    _write_faults_file(faults_path)

    def fake_run_algo_campaign(mem, spec, records, *, sim="verilator", verbose=False, fault_ram_sv=None):
        return CampaignResult(
            algo_name="march_c",
            mem=MemoryParams(8, 8),
            golden_clean=True,
            faults=[],
            detected=0,
            total=2,
            coverage_percent=50.0,
            build_seconds=0.1,
            run_seconds=0.1,
            sim="verilator",
        )

    monkeypatch.setattr("autombist.algo_engine.run_algo_campaign", fake_run_algo_campaign)

    result = runner.invoke(
        app,
        [
            "test",
            "--addr-width", "8",
            "--data-width", "8",
            "--algo", "march_c",
            "--faults", str(faults_path),
            "--min-coverage", "90",
        ],
    )

    assert result.exit_code == 1
    assert "autombist: coverage 50.00% is below --min-coverage 90.00%" in result.output


def test_cli_test_command_custom_fault_types(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json as _json

    faults_path = tmp_path / "faults.txt"
    _write_faults_file(faults_path)

    fault_types_path = tmp_path / "types.json"
    fault_types_path.write_text(
        _json.dumps(
            [
                {
                    "name": "MYFAULT",
                    "category": "static_clamp",
                    "sensitize": {"pre": "x", "written": "x", "transition": "x", "on": "victim"},
                    "effect": {"kind": "force", "value": "0"},
                }
            ]
        ),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def fake_run_algo_campaign(mem, spec, records, *, sim="verilator", verbose=False, fault_ram_sv=None):
        captured["fault_ram_sv"] = fault_ram_sv
        return _fake_campaign_result()

    monkeypatch.setattr("autombist.algo_engine.run_algo_campaign", fake_run_algo_campaign)

    result = runner.invoke(
        app,
        [
            "test",
            "--addr-width", "8",
            "--data-width", "8",
            "--algo", "march_c",
            "--faults", str(faults_path),
            "--fault-types", str(fault_types_path),
        ],
    )

    assert result.exit_code == 0
    assert captured["fault_ram_sv"] is not None
    assert Path(str(captured["fault_ram_sv"])).exists()


def test_cli_test_command_fsm_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    faults_path = tmp_path / "faults.txt"
    _write_faults_file(faults_path)

    fsm_path = tmp_path / "my_fsm_top.sv"
    fsm_path.write_text(
        "module my_fsm_top(input wire clk);\nendmodule\n", encoding="utf-8"
    )

    from autombist.fsm_harness import FsmPorts

    def fake_check_ports(sv_text: str, module_name: str | None = None):
        return FsmPorts(module_name="my_fsm_top", ports={})

    def fake_run_fsm_campaign(mem, sources, module_name, records, *, sim="verilator", fault_ram_sv=None):
        return _fake_campaign_result()

    monkeypatch.setattr("autombist.fsm_harness.check_ports", fake_check_ports)
    monkeypatch.setattr("autombist.algo_engine.run_fsm_campaign", fake_run_fsm_campaign)

    result = runner.invoke(
        app,
        [
            "test",
            "--addr-width", "8",
            "--data-width", "8",
            "--fsm", str(fsm_path),
            "--faults", str(faults_path),
        ],
    )

    assert result.exit_code == 0
    assert "FSM:my_fsm_top" in result.output


def test_cli_test_command_unknown_algo_errors(tmp_path: Path) -> None:
    faults_path = tmp_path / "faults.txt"
    _write_faults_file(faults_path)

    result = runner.invoke(
        app,
        [
            "test",
            "--addr-width", "8",
            "--data-width", "8",
            "--algo", "totally-bogus-algo-name",
            "--faults", str(faults_path),
        ],
    )

    assert result.exit_code == 1
    assert "autombist:" in result.output


# ---------------------------------------------------------------------------
# Additional coverage: the 'algo' interactive shell command
# ---------------------------------------------------------------------------


def test_cli_algo_command_interactive_invokes_cmdloop(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"cmdloop": False}

    def fake_cmdloop(self, intro=None):
        called["cmdloop"] = True

    monkeypatch.setattr("autombist.algo_shell.AlgoShell.cmdloop", fake_cmdloop)

    result = runner.invoke(app, ["algo"])

    assert result.exit_code == 0
    assert called["cmdloop"] is True


def test_cli_algo_command_script_file_quit(tmp_path: Path) -> None:
    script_path = tmp_path / "session.algo"
    script_path.write_text("quit\n", encoding="utf-8")

    result = runner.invoke(app, ["algo", "--script", str(script_path)])

    assert result.exit_code == 0


def test_cli_algo_command_script_stdin(tmp_path: Path) -> None:
    result = runner.invoke(app, ["algo", "--script", "-"], input="quit\n")

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Additional coverage: _assert_smoke_file failure path
# ---------------------------------------------------------------------------


def test_assert_smoke_file_raises_typer_exit_when_missing(tmp_path: Path) -> None:
    import typer as _typer
    from autombist.cli import _assert_smoke_file

    missing = tmp_path / "nope.txt"

    with pytest.raises(_typer.Exit):
        _assert_smoke_file(missing, "some expected file")


# ---------------------------------------------------------------------------
# Additional coverage: smoke --faultflow (emit-only) branch
# ---------------------------------------------------------------------------


def test_cli_smoke_faultflow_emit_only(tmp_path: Path) -> None:
    outdir = tmp_path / "smoke-out"

    result = runner.invoke(
        app, ["smoke", "--no-sim", "--faultflow", "--out", str(outdir)]
    )

    assert result.exit_code == 0
    assert "faultflow bundle emit (emit-only): PASS" in result.output

    memory_name = "sram_1rw"
    top = "sram_1rw_mbist"
    bundle = outdir / "out" / memory_name / "faultflow"
    assert (bundle / f"{memory_name}_bbox.v").exists()
    assert (bundle / "synth_collar.ys").exists()
    assert (bundle / f"{top}.ofs").exists()
    assert (bundle / "run_faultflow.sh").exists()


def test_cli_smoke_march_raw_wrapper_missing_marker_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-process the march-raw clean-generation wrapper to strip the
    march_raw_top marker so the smoke command's own check fails."""
    from autombist import cli as cli_module

    real_generate = cli_module._generate

    def fake_generate(config, out, test, faults, seed, fault_type, pulse_width_ns, algo):
        wrapper_path = real_generate(config, out, test, faults, seed, fault_type, pulse_width_ns, algo)
        if algo == "march-raw" and not test:
            text = wrapper_path.read_text(encoding="utf-8")
            wrapper_path.write_text(text.replace("march_raw_top", "march_raw_STRIPPED"), encoding="utf-8")
        return wrapper_path

    monkeypatch.setattr("autombist.cli._generate", fake_generate)

    outdir = tmp_path / "smoke-out"
    result = runner.invoke(app, ["smoke", "--no-sim", "--out", str(outdir)])

    assert result.exit_code == 1
    assert "[smoke] FAIL: wrapper missing march_raw_top" in result.output
