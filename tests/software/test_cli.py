from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from typer.testing import CliRunner

import autombist.cli as cli_mod
from autombist.runner import SimulationResult

runner = CliRunner()


def _fake_result(coverage_percent: float) -> SimulationResult:
    report = {
        "schema_version": "1.2.0",
        "status": "pass",
        "returncode": 0,
        "runtime_seconds": 0.01,
        "config": {"memory_name": "sram_1rw"},
        "simulation": {"algo": "march-c", "fault_type": "stuck-at"},
        "fault_metrics": {
            "detected_faults": 1,
            "total_fault_sites": 10,
            "coverage_percent": coverage_percent,
            "injected_faults": 10,
        },
        "junit": {"summary": {"tests": 1, "failures": 0, "errors": 0}},
        "log_path": "sim.log",
        "report_path": "reports/latest.json",
    }
    return SimulationResult(
        command=["make"],
        cwd=Path("."),
        log_path=Path("sim.log"),
        report_path=Path("reports/latest.json"),
        returncode=0,
        runtime_seconds=0.01,
        stdout="",
        stderr="",
        report=report,
    )


def _stub_generate(wrapper_path: Path):
    def fake_generate(config, out, **kwargs):
        return wrapper_path

    return fake_generate


# ---------------------------------------------------------------------------
# `run --min-coverage`: encodes the fix for the no-op bug (cli.py used to drop
# min_coverage on the path from `run`, only honoring it from `simulate`).
# ---------------------------------------------------------------------------


def test_run_min_coverage_gate_fails_on_low_coverage(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    wrapper_path = tmp_path / "out" / "sram_1rw" / "sram_1rw_mbist.v"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("// stub\n", encoding="utf-8")

    monkeypatch.setattr(cli_mod, "generate_from_config", _stub_generate(wrapper_path))
    monkeypatch.setattr(
        cli_mod, "run_simulation", lambda module_outdir, verbose=False: _fake_result(50.0)
    )

    result = runner.invoke(
        cli_mod.app,
        [
            "run",
            "--config", str(config_path),
            "--out", str(tmp_path / "out"),
            "--min-coverage", "90",
        ],
    )

    assert result.exit_code == 1
    assert "below --min-coverage" in result.output


def test_run_min_coverage_gate_passes_on_sufficient_coverage(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    wrapper_path = tmp_path / "out" / "sram_1rw" / "sram_1rw_mbist.v"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("// stub\n", encoding="utf-8")

    monkeypatch.setattr(cli_mod, "generate_from_config", _stub_generate(wrapper_path))
    monkeypatch.setattr(
        cli_mod, "run_simulation", lambda module_outdir, verbose=False: _fake_result(95.0)
    )

    result = runner.invoke(
        cli_mod.app,
        [
            "run",
            "--config", str(config_path),
            "--out", str(tmp_path / "out"),
            "--min-coverage", "90",
        ],
    )

    assert result.exit_code == 0


def test_run_min_coverage_gate_fails_when_coverage_was_never_reported(
    tmp_path: Path, monkeypatch
) -> None:
    """A gate that was actually requested must not silently pass just because
    the simulator reported no coverage number to check it against (e.g. a
    fault mode/type whose stdout never prints "Fault coverage: X/Y (Z%)").
    Also guards against a crash: the old code path would have tried to
    format None with :.2f once this case correctly started failing the gate."""
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    wrapper_path = tmp_path / "out" / "sram_1rw" / "sram_1rw_mbist.v"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("// stub\n", encoding="utf-8")

    monkeypatch.setattr(cli_mod, "generate_from_config", _stub_generate(wrapper_path))
    monkeypatch.setattr(
        cli_mod, "run_simulation", lambda module_outdir, verbose=False: _fake_result(None)
    )

    result = runner.invoke(
        cli_mod.app,
        [
            "run",
            "--config", str(config_path),
            "--out", str(tmp_path / "out"),
            "--min-coverage", "90",
        ],
    )

    assert result.exit_code == 1
    assert "reported no coverage number" in result.output


# ---------------------------------------------------------------------------
# `--json`: simulate/run print the structured report to stdout instead of
# the human summary.
# ---------------------------------------------------------------------------


def test_simulate_json_prints_report_dict(tmp_path: Path, monkeypatch) -> None:
    wrapper_path = tmp_path / "out" / "sram_1rw" / "sram_1rw_mbist.v"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("// stub\n", encoding="utf-8")
    (wrapper_path.parent / "config.yml").write_text("memory_name: sram_1rw\n", encoding="utf-8")
    (wrapper_path.parent / "Makefile").write_text("sim:\n\t@true\n", encoding="utf-8")

    monkeypatch.setattr(
        cli_mod, "run_simulation", lambda module_outdir, verbose=False: _fake_result(87.5)
    )

    result = runner.invoke(cli_mod.app, ["simulate", "--out", str(tmp_path / "out"), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["fault_metrics"]["coverage_percent"] == 87.5
    assert payload["simulation"]["algo"] == "march-c"


def test_simulate_verbose_json_keeps_stdout_pure_and_routes_raw_output_to_stderr(
    tmp_path: Path, monkeypatch
) -> None:
    """'--verbose --json' must not silently drop the raw simulator console
    output --verbose promises: since it can't join stdout without breaking
    the JSON document, it has to land on stderr instead."""
    wrapper_path = tmp_path / "out" / "sram_1rw" / "sram_1rw_mbist.v"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("// stub\n", encoding="utf-8")
    (wrapper_path.parent / "config.yml").write_text("memory_name: sram_1rw\n", encoding="utf-8")

    base = _fake_result(87.5)
    verbose_result = SimulationResult(
        command=base.command, cwd=base.cwd, log_path=base.log_path, report_path=base.report_path,
        returncode=0, runtime_seconds=base.runtime_seconds,
        stdout="RAW SIMULATOR STDOUT MARKER\n", stderr="RAW SIMULATOR STDERR MARKER\n",
        report=base.report,
    )
    monkeypatch.setattr(cli_mod, "run_simulation", lambda module_outdir, verbose=False: verbose_result)

    result = runner.invoke(
        cli_mod.app, ["simulate", "--out", str(tmp_path / "out"), "--json", "--verbose"]
    )

    assert result.exit_code == 0
    # stdout alone is still exactly one parseable JSON document...
    payload = json.loads(result.stdout)
    assert payload["fault_metrics"]["coverage_percent"] == 87.5
    # ...while BOTH raw streams (stdout's console output can't join stdout
    # without corrupting the JSON, so it's redirected to stderr too) show up
    # on stderr, and neither leaks into stdout.
    assert "RAW SIMULATOR STDOUT MARKER" in result.stderr
    assert "RAW SIMULATOR STDERR MARKER" in result.stderr
    assert "RAW SIMULATOR STDOUT MARKER" not in result.stdout
    # No human-summary lines leaked into the JSON stdout stream.
    assert "autombist: simulation" not in result.output


def test_run_json_prints_report_dict_and_suppresses_status_lines(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    wrapper_path = tmp_path / "out" / "sram_1rw" / "sram_1rw_mbist.v"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("// stub\n", encoding="utf-8")

    monkeypatch.setattr(cli_mod, "generate_from_config", _stub_generate(wrapper_path))
    monkeypatch.setattr(
        cli_mod, "run_simulation", lambda module_outdir, verbose=False: _fake_result(95.0)
    )

    result = runner.invoke(
        cli_mod.app,
        ["run", "--config", str(config_path), "--out", str(tmp_path / "out"), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["fault_metrics"]["coverage_percent"] == 95.0
    assert "Generated MBIST wrapper" not in result.output


def test_run_json_still_honors_min_coverage_exit_code(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    wrapper_path = tmp_path / "out" / "sram_1rw" / "sram_1rw_mbist.v"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("// stub\n", encoding="utf-8")

    monkeypatch.setattr(cli_mod, "generate_from_config", _stub_generate(wrapper_path))
    monkeypatch.setattr(
        cli_mod, "run_simulation", lambda module_outdir, verbose=False: _fake_result(50.0)
    )

    result = runner.invoke(
        cli_mod.app,
        [
            "run", "--config", str(config_path), "--out", str(tmp_path / "out"),
            "--json", "--min-coverage", "90",
        ],
    )

    assert result.exit_code == 1
    # The report is still printed as JSON before the gate failure is reported
    # (a separate message on stderr) -- --json doesn't bypass the gate.
    assert '"coverage_percent": 50.0' in result.output
    assert "below --min-coverage" in result.output


# ---------------------------------------------------------------------------
# Preflight tool guards: `harden --run` needs `nix`, `macro-signoff` needs
# `bash` -- both used to hand a raw, unhelpful OSError to the user.
# ---------------------------------------------------------------------------


def test_harden_run_missing_nix_gives_actionable_error(tmp_path: Path, monkeypatch) -> None:
    harden_config = tmp_path / "harden.yml"
    harden_config.write_text("design_name: stub\n", encoding="utf-8")
    out_path = tmp_path / "librelane-config.json"

    monkeypatch.setattr(cli_mod, "build_librelane_config", lambda loaded: {"DESIGN_NAME": "stub"})
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = runner.invoke(
        cli_mod.app,
        ["harden", "--config", str(harden_config), "--out", str(out_path), "--run"],
    )

    assert result.exit_code == 1
    assert "'nix' not found on PATH" in result.output


def test_harden_without_run_does_not_require_nix(tmp_path: Path, monkeypatch) -> None:
    harden_config = tmp_path / "harden.yml"
    harden_config.write_text("design_name: stub\n", encoding="utf-8")
    out_path = tmp_path / "librelane-config.json"

    monkeypatch.setattr(cli_mod, "build_librelane_config", lambda loaded: {"DESIGN_NAME": "stub"})
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = runner.invoke(
        cli_mod.app,
        ["harden", "--config", str(harden_config), "--out", str(out_path)],
    )

    assert result.exit_code == 0


def test_harden_run_defaults_to_pinned_librelane_ref(tmp_path: Path, monkeypatch) -> None:
    harden_config = tmp_path / "harden.yml"
    harden_config.write_text("design_name: stub\n", encoding="utf-8")
    out_path = tmp_path / "librelane-config.json"

    captured: dict[str, object] = {}

    class _FakeCompleted:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(cli_mod, "build_librelane_config", lambda loaded: {"DESIGN_NAME": "stub"})
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/nix")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = runner.invoke(
        cli_mod.app,
        ["harden", "--config", str(harden_config), "--out", str(out_path), "--run"],
    )

    assert result.exit_code == 0
    assert cli_mod.LIBRELANE_FLAKE_REF in captured["cmd"]


def test_harden_run_librelane_ref_override(tmp_path: Path, monkeypatch) -> None:
    harden_config = tmp_path / "harden.yml"
    harden_config.write_text("design_name: stub\n", encoding="utf-8")
    out_path = tmp_path / "librelane-config.json"

    captured: dict[str, object] = {}

    class _FakeCompleted:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(cli_mod, "build_librelane_config", lambda loaded: {"DESIGN_NAME": "stub"})
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/nix")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = runner.invoke(
        cli_mod.app,
        [
            "harden", "--config", str(harden_config), "--out", str(out_path), "--run",
            "--librelane-ref", "github:librelane/librelane/3.1.0.dev2",
        ],
    )

    assert result.exit_code == 0
    assert "github:librelane/librelane/3.1.0.dev2" in captured["cmd"]
    assert cli_mod.LIBRELANE_FLAKE_REF not in captured["cmd"]
    assert out_path.exists()


def test_macro_signoff_missing_bash_gives_actionable_error(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "run_macro_signoff.sh"
    script.write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: None)

    result = runner.invoke(cli_mod.app, ["macro-signoff", "--script", str(script)])

    assert result.exit_code == 1
    assert "'bash' not found on PATH" in result.output


def test_macro_signoff_show_command_works_without_bash(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "run_macro_signoff.sh"
    script.write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: None)

    result = runner.invoke(
        cli_mod.app, ["macro-signoff", "--script", str(script), "--show-command"]
    )

    assert result.exit_code == 0
    assert "$ bash" in result.output


# ---------------------------------------------------------------------------
# `doctor`: capability report over the external toolchain.
# ---------------------------------------------------------------------------


def test_doctor_reports_ok_and_missing_tools(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")  # deterministic plain-text output
    monkeypatch.delenv("FAULTFLOW_HOME", raising=False)

    present = {"make", "iverilog", "nix"}
    monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}" if tool in present else None)
    monkeypatch.setattr(cli_mod, "_cocotb_available", lambda: True)

    result = runner.invoke(cli_mod.app, ["doctor"])

    assert result.exit_code == 0
    assert "make" in result.output and "OK" in result.output
    assert "verilator" in result.output and "MISSING" in result.output
    assert "cocotb" in result.output
    assert "FAULTFLOW_HOME" in result.output
    assert "not set" in result.output


def test_doctor_cocotb_missing_reported(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("shutil.which", lambda tool: None)
    monkeypatch.setattr(cli_mod, "_cocotb_available", lambda: False)

    result = runner.invoke(cli_mod.app, ["doctor"])

    assert result.exit_code == 0
    assert "cocotb (python)" in result.output
    assert "not importable" in result.output


def test_doctor_tcl_available_reported(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("shutil.which", lambda tool: None)
    monkeypatch.setattr(cli_mod, "_cocotb_available", lambda: False)
    monkeypatch.setattr(cli_mod, "_tcl_available", lambda: True)

    result = runner.invoke(cli_mod.app, ["doctor"])

    assert result.exit_code == 0
    assert "tkinter/Tcl (python)" in result.output
    assert "importable" in result.output
    assert "shell" in result.output


def test_doctor_tcl_missing_reported(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("shutil.which", lambda tool: None)
    monkeypatch.setattr(cli_mod, "_cocotb_available", lambda: False)
    monkeypatch.setattr(cli_mod, "_tcl_available", lambda: False)

    result = runner.invoke(cli_mod.app, ["doctor"])

    assert result.exit_code == 0
    assert "tkinter/Tcl (python)" in result.output
    assert "not importable" in result.output


def test_doctor_faultflow_home_set_reported(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FAULTFLOW_HOME", "/home/user/faultflow")
    monkeypatch.setattr("shutil.which", lambda tool: None)
    monkeypatch.setattr(cli_mod, "_cocotb_available", lambda: False)

    result = runner.invoke(cli_mod.app, ["doctor"])

    assert result.exit_code == 0
    assert "/home/user/faultflow" in result.output


# ---------------------------------------------------------------------------
# Global -v/-q: OR into each command's own --verbose, suppress routine status
# lines, and reject being combined with each other.
# ---------------------------------------------------------------------------


def test_global_verbose_ors_into_simulate_effective_verbose(tmp_path: Path, monkeypatch) -> None:
    wrapper_path = tmp_path / "out" / "sram_1rw" / "sram_1rw_mbist.v"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("// stub\n", encoding="utf-8")
    (wrapper_path.parent / "config.yml").write_text("memory_name: sram_1rw\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run_simulation(module_outdir, verbose=False):
        captured["verbose"] = verbose
        return _fake_result(90.0)

    monkeypatch.setattr(cli_mod, "run_simulation", fake_run_simulation)

    # -v is a GLOBAL option: it must precede the subcommand name.
    result = runner.invoke(cli_mod.app, ["-v", "simulate", "--out", str(tmp_path / "out")])

    assert result.exit_code == 0
    assert captured["verbose"] is True


def test_global_quiet_suppresses_generate_status_lines(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    wrapper_path = tmp_path / "out" / "sram_1rw" / "sram_1rw_mbist.v"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("// stub\n", encoding="utf-8")

    monkeypatch.setattr(cli_mod, "generate_from_config", _stub_generate(wrapper_path))

    result = runner.invoke(
        cli_mod.app,
        ["-q", "generate", "--config", str(config_path), "--out", str(tmp_path / "out")],
    )

    assert result.exit_code == 0
    assert "Generated MBIST wrapper" not in result.output


def test_global_verbose_and_quiet_are_mutually_exclusive(tmp_path: Path) -> None:
    result = runner.invoke(cli_mod.app, ["-v", "-q", "doctor"])

    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_plain_invocation_does_not_touch_root_logger(monkeypatch) -> None:
    """An ordinary invocation (neither -v nor -q) must not reconfigure the
    process-global root logger -- otherwise embedding autombist.cli
    in-process alongside independent logging setup would have that setup
    silently torn down on every command call."""
    import logging

    sentinel = logging.Handler()
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    root.handlers = [sentinel]
    try:
        result = runner.invoke(cli_mod.app, ["doctor"])
        assert result.exit_code == 0
        assert root.handlers == [sentinel], "plain invocation reconfigured the root logger"
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_verbose_flag_does_reconfigure_root_logger(monkeypatch) -> None:
    import logging

    sentinel = logging.Handler()
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    root.handlers = [sentinel]
    try:
        result = runner.invoke(cli_mod.app, ["-v", "doctor"])
        assert result.exit_code == 0
        assert root.handlers != [sentinel], "-v should have reconfigured the root logger"
        assert root.level == logging.DEBUG
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)
