from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# Error paths and the less common branches of each command: every failure is
# one "autombist: ..." line with a non-zero exit code, never a traceback.
# ---------------------------------------------------------------------------

_REAL_CONFIG = """\
memory_name: sram_1rw
wrapper_module_name: sram_1rw_mbist
addr_width: 4
data_width: 8
we_active_low: true
ports: {clk: clk0, addr: addr0, din: din0, dout: dout0, we: web0, csb: csb0}
"""


def _real_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(_REAL_CONFIG, encoding="utf-8")
    return config_path


def _module_outdir(tmp_path: Path) -> Path:
    module_outdir = tmp_path / "out" / "sram_1rw"
    module_outdir.mkdir(parents=True)
    (module_outdir / "sram_1rw_mbist.v").write_text("// stub\n", encoding="utf-8")
    (module_outdir / "config.yml").write_text("memory_name: sram_1rw\n", encoding="utf-8")
    return module_outdir


def _raise(exc: BaseException):
    def fake(*args, **kwargs):
        raise exc

    return fake


def _assert_one_error_line(result, fragment: str, exit_code: int = 1) -> None:
    assert result.exit_code == exit_code, result.output
    assert fragment in result.output
    assert "Traceback" not in result.output


def test_generate_missing_config_is_one_error_line(tmp_path: Path) -> None:
    result = runner.invoke(cli_mod.app, ["generate", "--config", str(tmp_path / "missing.yml"),
                                         "--out", str(tmp_path / "out")])
    _assert_one_error_line(result, "autombist:")


def test_simulate_takes_the_module_directory_itself_and_verbose_prints_the_raw_output(
    tmp_path: Path, monkeypatch
) -> None:
    import dataclasses

    module_outdir = _module_outdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_run_simulation(outdir, verbose=False):
        captured["outdir"] = outdir
        return dataclasses.replace(_fake_result(95.0), stdout="RAW SIMULATOR OUTPUT\n")

    monkeypatch.setattr(cli_mod, "run_simulation", fake_run_simulation)

    result = runner.invoke(cli_mod.app, ["simulate", "--out", str(module_outdir), "--verbose"])

    assert result.exit_code == 0, result.output
    assert captured["outdir"] == module_outdir.resolve()
    assert "RAW SIMULATOR OUTPUT" in result.output


def test_grade_controller_with_no_coverage_names_the_bundle(tmp_path: Path, monkeypatch) -> None:
    module_outdir = _module_outdir(tmp_path)
    monkeypatch.setattr("autombist.runner.run_controller_grading", lambda outdir, opts, *, run=True: None)

    result = runner.invoke(cli_mod.app, ["grade-controller", "--out", str(tmp_path / "out")])

    assert result.exit_code == 0, result.output
    assert f"Controller grading complete. Bundle: {module_outdir.resolve() / 'faultflow'}" in result.output


def test_grade_controller_leaves_an_unreadable_report_alone(tmp_path: Path, monkeypatch) -> None:
    module_outdir = _module_outdir(tmp_path)
    (module_outdir / "reports").mkdir()
    latest = module_outdir / "reports" / "latest.json"
    latest.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(
        "autombist.runner.run_controller_grading",
        lambda outdir, opts, *, run=True: {"coverage_percent": 91.0, "detected": 91, "denominator": 100},
    )

    result = runner.invoke(cli_mod.app, ["grade-controller", "--out", str(tmp_path / "out")])

    assert result.exit_code == 0, result.output
    assert "Controller structural coverage (FaultFlow): 91/100" in result.output
    assert latest.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize(
    ("snapshot", "fragment"),
    [
        (None, "config.yml"),
        ("- not\n- a mapping\n", "must be a YAML mapping"),
    ],
)
def test_generate_emit_manifest_needs_a_mapping_snapshot(
    tmp_path: Path, monkeypatch, snapshot: str | None, fragment: str
) -> None:
    module_outdir = tmp_path / "out" / "sram_1rw"

    def fake_generate(config, out, **kwargs):
        module_outdir.mkdir(parents=True, exist_ok=True)
        if snapshot is not None:
            (module_outdir / "config.yml").write_text(snapshot, encoding="utf-8")
        return module_outdir / "sram_1rw_mbist.v"

    monkeypatch.setattr(cli_mod, "generate_from_config", fake_generate)

    result = runner.invoke(cli_mod.app, ["generate", "--config", str(_real_config(tmp_path)),
                                         "--out", str(tmp_path / "out"), "--emit-manifest"])

    _assert_one_error_line(result, fragment)
    assert not (module_outdir / "manifest.json").exists()


def test_generate_emit_manifest_build_error_is_one_error_line(tmp_path: Path, monkeypatch) -> None:
    from autombist.manifest import ManifestError

    monkeypatch.setattr("autombist.manifest.build_instance_manifest",
                        _raise(ManifestError("no u_algo_top in the wrapper")))

    result = runner.invoke(cli_mod.app, ["generate", "--config", str(_real_config(tmp_path)),
                                         "--out", str(tmp_path / "out"), "--emit-manifest"])

    _assert_one_error_line(result, "autombist: no u_algo_top in the wrapper")


def test_run_test_names_the_fault_files_and_refuses_a_manifest_for_them(tmp_path: Path, monkeypatch) -> None:
    """A --test build has the saboteur in place of the memory, so it has no
    clean collar for a manifest to describe: run says so after simulating."""
    monkeypatch.setattr(cli_mod, "run_simulation", lambda outdir, verbose=False: _fake_result(100.0))

    result = runner.invoke(cli_mod.app, ["run", "--config", str(_real_config(tmp_path)), "--out", str(tmp_path / "out"),
                                         "--test", "--faults", "4", "--seed", "1", "--emit-manifest"])

    module_outdir = (tmp_path / "out" / "sram_1rw").resolve()
    assert f"Generated fault masks in: {module_outdir / 'faults'}" in result.output
    assert f"Generated fault-sim Makefile: {module_outdir / 'Makefile'}" in result.output
    assert "simulation PASS" in result.output
    _assert_one_error_line(result, "generated with --test (fault-injection saboteur in place of the memory)")
    assert not (module_outdir / "manifest.json").exists()


def test_run_emit_manifest_writes_the_manifest_after_simulating(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli_mod, "run_simulation", lambda outdir, verbose=False: _fake_result(100.0))

    result = runner.invoke(cli_mod.app, ["run", "--config", str(_real_config(tmp_path)), "--out", str(tmp_path / "out"),
                                         "--emit-manifest"])

    assert result.exit_code == 0, result.output
    module_outdir = (tmp_path / "out" / "sram_1rw").resolve()
    assert result.output.index("simulation PASS") < result.output.index("Emitted instance manifest:")
    assert (module_outdir / "manifest.json").is_file()


def _yield_sweep(tmp_path: Path, *args: str):
    return runner.invoke(cli_mod.app, ["yield-sweep", "--config", str(_real_config(tmp_path)),
                                       "--out", str(tmp_path / "sweep"), "--spare-rows", "1", "--faults", "1", *args])


def test_yield_sweep_bad_trial_detail_is_one_error_line(tmp_path: Path) -> None:
    result = _yield_sweep(tmp_path, "--trial-detail", "some")
    _assert_one_error_line(result, "--trial-detail must be none, failures or all, got 'some'")


@pytest.mark.parametrize("flag", [["--observe", "march"], ["--fault-type", "transition-up", "--observe", "march"]])
def test_yield_sweep_self_repair_keeps_its_own_observation(tmp_path: Path, flag: list[str]) -> None:
    result = _yield_sweep(tmp_path, "--self-repair", *flag)
    _assert_one_error_line(result, "leave --fault-type and --observe at their defaults")


def _recording_progress(monkeypatch, calls: list) -> None:
    from contextlib import contextmanager

    @contextmanager
    def fake_fault_progress(total: int, description: str = "faults"):
        calls.append(("start", total, description))
        yield lambda completed, grand_total: calls.append((completed, grand_total))

    monkeypatch.setattr(cli_mod, "fault_progress", fake_fault_progress)


def test_yield_sweep_self_repair_table_and_progress(tmp_path: Path, monkeypatch) -> None:
    """The self-repair columns of the human table, from a stand-in engine (the
    real one simulates the RTL): the exact solver repairs both trials, the chip
    one, and the gap is put down to the analyzer. The progress bar advances
    once per trial."""
    from autombist.yieldsim.sweep import TrialRecord

    class _FakeSelfRepairEngine:
        name = "rtl-onchip-selfrepair"
        unit = "cells"

        def __init__(self, *args, **kwargs) -> None:
            self.trials = 0

        def describe(self) -> dict:
            return {}

        def run_trial(self, k: int, seed: int) -> TrialRecord:
            self.trials += 1
            if self.trials == 1:
                return TrialRecord(k=k, seed=seed, status="repaired", cells=k, spares_used=1,
                                   extras={"onchip_fail": False, "truth_repairable": True,
                                           "observed_repairable": True})
            return TrialRecord(k=k, seed=seed, status="unrepairable", cells=k,
                               extras={"onchip_fail": True, "truth_repairable": True, "observed_repairable": True,
                                       "cause": "analyzer"})

    calls: list = []
    monkeypatch.setattr(cli_mod, "SelfRepairEngine", _FakeSelfRepairEngine)
    _recording_progress(monkeypatch, calls)

    result = _yield_sweep(tmp_path, "--self-repair", "--trials", "2")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    title = lines.index("Repair-yield sweep")
    assert lines[title + 1].split() == ["Faults", "Trials", "Invalid", "Repair", "rate", "Exact", "solver",
                                        "Normalized", "Why", "not", "repaired"]
    assert lines[title + 3].split() == ["1", "2", "0", "50.0%", "100.0%", "50.0%", "analyzer", "1"]
    assert "Observed by: the chip's own analyze pass and self-repair; spares: 1 rows, 0 columns" in result.output
    assert calls == [("start", 2, "trials"), (1, 2), (2, 2)]
    assert json.loads((tmp_path / "sweep" / "yield_sweep.json").read_text(encoding="utf-8"))["points"][0][
        "self_repair"]["causes"] == {"analyzer": 1}


def test_yield_sim_advances_the_progress_bar_once_per_trial(tmp_path: Path, monkeypatch) -> None:
    calls: list = []
    _recording_progress(monkeypatch, calls)

    result = runner.invoke(cli_mod.app, ["yield-sim", "--array", "8x8", "--spare-rows", "1", "--faults", "1",
                                         "--trials", "3", "--out", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert calls == [("start", 3, "trials"), (1, 3), (2, 3), (3, 3)]


def test_is_blackbox_source_reads_the_attribute_and_tolerates_a_missing_file(tmp_path: Path) -> None:
    stub = tmp_path / "sram_bbox.v"
    stub.write_text("(* blackbox *)\nmodule sram_1rw(); endmodule\n", encoding="utf-8")
    model = tmp_path / "sram.v"
    model.write_text("module sram_1rw(); endmodule\n", encoding="utf-8")

    assert cli_mod._is_blackbox_source(stub) is True
    assert cli_mod._is_blackbox_source(model) is False
    assert cli_mod._is_blackbox_source(tmp_path / "missing.v") is False


@pytest.mark.parametrize(
    ("config_text", "fragment"),
    [
        (None, "autombist:"),
        ("- not\n- a mapping\n", "must be a YAML mapping"),
        ("wrapper_module_name: sram_1rw_mbist\n", "config is missing required key 'addr_width'"),
    ],
)
def test_wrap_test_access_rejects_a_config_that_is_not_a_snapshot(
    tmp_path: Path, config_text: str | None, fragment: str
) -> None:
    """--config is checked before anything is synthesized, so none of these
    needs Yosys or warptap."""
    config_path = tmp_path / "snapshot.yml"
    if config_text is not None:
        config_path.write_text(config_text, encoding="utf-8")

    result = runner.invoke(cli_mod.app, ["wrap-test-access", "--source", str(tmp_path / "w.v"),
                                         "--top", "sram_1rw_mbist", "--config", str(config_path),
                                         "--out", str(tmp_path / "ta")])

    _assert_one_error_line(result, fragment)
    assert not (tmp_path / "ta").exists()


def test_test_fault_types_that_is_not_a_list_is_one_error_line(tmp_path: Path) -> None:
    faults_path = tmp_path / "faults.txt"
    faults_path.write_text("SA0 3 0 0 0 0 0\n", encoding="ascii")
    fault_types_path = tmp_path / "types.json"
    fault_types_path.write_text('{"name": "custom_flip"}', encoding="utf-8")

    result = runner.invoke(cli_mod.app, ["test", "--addr-width", "4", "--data-width", "8", "--faults", str(faults_path),
                                         "--fault-types", str(fault_types_path)])

    _assert_one_error_line(result, "--fault-types file must contain a JSON list of fault-primitive specs")


def _fake_openram(monkeypatch, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
    import subprocess

    monkeypatch.setattr(cli_mod, "load_openram_config", lambda path: {"num_words": 256})
    monkeypatch.setattr(cli_mod, "build_openram_command_args",
                        lambda cfg, path: ["python3", "synthesize_sram.py", "--num-words", 256])
    monkeypatch.setattr(cli_mod, "run_openram_synthesis",
                        lambda path: subprocess.CompletedProcess(["python3"], returncode, stdout, stderr))


def test_ram_synth_show_command_prints_the_command_and_the_output(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text("num_words: 256\n", encoding="utf-8")
    _fake_openram(monkeypatch, stdout="OpenRAM: done\n", stderr="OpenRAM: a warning\n")

    result = runner.invoke(cli_mod.app, ["ram-synth", "--config", str(config_path), "--show-command"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["$ python3 synthesize_sram.py --num-words 256", "OpenRAM: done"]
    assert result.stderr == "OpenRAM: a warning\n"


def test_ram_synth_exits_with_openram_exit_code(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text("num_words: 256\n", encoding="utf-8")
    _fake_openram(monkeypatch, stderr="OpenRAM: failed\n", returncode=4)

    result = runner.invoke(cli_mod.app, ["ram-synth", "--config", str(config_path)])

    assert result.exit_code == 4
    assert "OpenRAM: failed" in result.output


def test_ram_synth_invalid_config_is_one_error_line(tmp_path: Path, monkeypatch) -> None:
    from autombist.openram_flow import OpenRAMConfigError

    config_path = tmp_path / "openram.yml"
    config_path.write_text("num_words: -1\n", encoding="utf-8")
    monkeypatch.setattr(cli_mod, "load_openram_config", _raise(OpenRAMConfigError("num_words must be > 0")))

    result = runner.invoke(cli_mod.app, ["ram-synth", "--config", str(config_path)])

    _assert_one_error_line(result, "autombist: num_words must be > 0")


def test_harden_missing_config_is_one_error_line(tmp_path: Path) -> None:
    missing = tmp_path / "harden.yml"
    result = runner.invoke(cli_mod.app, ["harden", "--config", str(missing), "--out", str(tmp_path / "ll.json")])
    _assert_one_error_line(result, f"autombist: harden config not found: {missing}")


def test_harden_invalid_config_is_one_error_line(tmp_path: Path, monkeypatch) -> None:
    from autombist.signoff import SignoffConfigError

    harden_config = tmp_path / "harden.yml"
    harden_config.write_text("design_name: stub\n", encoding="utf-8")
    monkeypatch.setattr(cli_mod, "build_librelane_config", _raise(SignoffConfigError("die_area is required")))

    result = runner.invoke(cli_mod.app, ["harden", "--config", str(harden_config), "--out", str(tmp_path / "ll.json")])

    _assert_one_error_line(result, "autombist: die_area is required")
    assert not (tmp_path / "ll.json").exists()


def test_harden_run_exits_with_librelane_exit_code(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    harden_config = tmp_path / "harden.yml"
    harden_config.write_text("design_name: stub\n", encoding="utf-8")
    monkeypatch.setattr(cli_mod, "build_librelane_config", lambda loaded: {"DESIGN_NAME": "stub"})
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/nix")
    monkeypatch.setattr("subprocess.run", lambda cmd: subprocess.CompletedProcess(cmd, 5))

    result = runner.invoke(cli_mod.app, ["harden", "--config", str(harden_config), "--out", str(tmp_path / "ll.json"),
                                         "--run"])

    assert result.exit_code == 5


_OPENRAM_LEF = """\
VERSION 5.4 ;
UNITS
  DATABASE MICRONS 2000 ;
END UNITS
MACRO sram_1rw
  SIZE 120.5 BY 80.25 ;
END sram_1rw
END LIBRARY
"""


def test_fix_lef_units_rewrites_the_declaration_in_place(tmp_path: Path) -> None:
    lef = tmp_path / "sram_1rw.lef"
    lef.write_text(_OPENRAM_LEF, encoding="utf-8")

    result = runner.invoke(cli_mod.app, ["fix-lef-units", str(lef)])

    assert result.exit_code == 0, result.output
    assert f"Wrote {lef} (DATABASE MICRONS -> 1000, 0 coords snapped)" in result.output
    assert lef.read_text(encoding="utf-8") == _OPENRAM_LEF.replace("MICRONS 2000", "MICRONS 1000")


def test_fix_lef_units_out_and_target_dbu_leave_the_source_alone(tmp_path: Path) -> None:
    lef = tmp_path / "sram_1rw.lef"
    lef.write_text(_OPENRAM_LEF, encoding="utf-8")
    dest = tmp_path / "fixed.lef"

    result = runner.invoke(cli_mod.app, ["fix-lef-units", str(lef), "--out", str(dest), "--target-dbu", "4000"])

    assert result.exit_code == 0, result.output
    assert "DATABASE MICRONS 4000 ;" in dest.read_text(encoding="utf-8")
    assert lef.read_text(encoding="utf-8") == _OPENRAM_LEF


def test_fix_lef_units_missing_lef_is_one_error_line(tmp_path: Path) -> None:
    missing = tmp_path / "missing.lef"
    result = runner.invoke(cli_mod.app, ["fix-lef-units", str(missing)])
    _assert_one_error_line(result, f"autombist: LEF not found: {missing}")


def test_macro_signoff_missing_script_is_one_error_line(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sh"
    result = runner.invoke(cli_mod.app, ["macro-signoff", "--script", str(missing)])
    _assert_one_error_line(result, f"autombist: signoff script not found: {missing}")


@pytest.mark.parametrize("returncode", [0, 3])
def test_macro_signoff_exits_with_the_script_exit_code(tmp_path: Path, monkeypatch, returncode: int) -> None:
    import subprocess

    script = tmp_path / "run_macro_signoff.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    ran: list = []
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/bash")
    monkeypatch.setattr("subprocess.run", lambda cmd: ran.append(cmd) or subprocess.CompletedProcess(cmd, returncode))

    result = runner.invoke(cli_mod.app, ["macro-signoff", "sram_1rw", "--script", str(script)])

    assert result.exit_code == returncode
    assert ran and ran[0][-1] == "sram_1rw"


def _smoke_workspace(output: str) -> Path:
    line = next(line for line in output.splitlines() if line.startswith("[smoke] workspace: "))
    return Path(line.removeprefix("[smoke] workspace: "))


def test_smoke_without_out_uses_a_temporary_workspace_and_removes_it() -> None:
    result = runner.invoke(cli_mod.app, ["smoke", "--no-sim"])

    assert result.exit_code == 0, result.output
    assert "[smoke] All checks passed" in result.output
    workspace = _smoke_workspace(result.output)
    assert workspace.name.startswith("autombist-smoke-")
    assert not workspace.exists()


def test_smoke_keep_artifacts_without_out_keeps_its_workspace(tmp_path: Path, monkeypatch) -> None:
    import tempfile

    kept = tmp_path / "kept"
    real_mkdtemp = tempfile.mkdtemp

    def fake_mkdtemp(*args, **kwargs):
        if kwargs.get("prefix") == "autombist-smoke-":
            kept.mkdir()
            return str(kept)
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(tempfile, "mkdtemp", fake_mkdtemp)

    result = runner.invoke(cli_mod.app, ["smoke", "--no-sim", "--keep-artifacts"])

    assert result.exit_code == 0, result.output
    assert _smoke_workspace(result.output) == kept
    assert (kept / "config.yml").is_file() and (kept / "out").is_dir()


@pytest.mark.parametrize(("fault_type", "mask"), [("transition-up", "tf_up_mask"), ("transition-down", "tf_down_mask")])
def test_smoke_fails_when_the_saboteur_lacks_its_transition_mask(
    tmp_path: Path, monkeypatch, fault_type: str, mask: str
) -> None:
    real_generate = cli_mod._generate

    def generate_then_drop_the_mask(*args, **kwargs):
        wrapper_path = real_generate(*args, **kwargs)
        if kwargs.get("fault_type") == fault_type:
            for saboteur in wrapper_path.parent.glob("*_saboteur.v"):
                saboteur.write_text(saboteur.read_text(encoding="utf-8").replace(mask, "no_mask"), encoding="utf-8")
        return wrapper_path

    monkeypatch.setattr(cli_mod, "_generate", generate_then_drop_the_mask)

    result = runner.invoke(cli_mod.app, ["smoke", "--no-sim", "--out", str(tmp_path / "ws")])

    _assert_one_error_line(result, f"[smoke] FAIL: saboteur missing {mask}")


def test_smoke_fails_when_the_openram_config_does_not_parse(tmp_path: Path, monkeypatch) -> None:
    from autombist.openram_flow import OpenRAMConfigError

    monkeypatch.setattr(cli_mod, "load_openram_config", _raise(OpenRAMConfigError("num_words must be > 0")))

    result = runner.invoke(cli_mod.app, ["smoke", "--no-sim", "--out", str(tmp_path / "ws")])

    _assert_one_error_line(result, "autombist: smoke ram-synth config parse failed: num_words must be > 0")


def test_smoke_fails_when_the_faultflow_bundle_cannot_be_emitted(tmp_path: Path, monkeypatch) -> None:
    from autombist.faultflow_flow import FaultFlowError

    monkeypatch.setattr("autombist.faultflow_flow.grade_controller", _raise(FaultFlowError("no cells/sky130")))

    result = runner.invoke(cli_mod.app, ["smoke", "--no-sim", "--faultflow", "--out", str(tmp_path / "ws")])

    _assert_one_error_line(result, "[smoke] FAIL: FaultFlow bundle emit failed: no cells/sky130")


def test_shell_file_exits_with_the_script_status(tmp_path: Path) -> None:
    pytest.importorskip("tkinter")
    good = tmp_path / "good.tcl"
    good.write_text("set x 1\n", encoding="utf-8")
    bad = tmp_path / "bad.tcl"
    bad.write_text('error "coverage too low"\n', encoding="utf-8")

    assert runner.invoke(cli_mod.app, ["shell", "--file", str(good)]).exit_code == 0
    result = runner.invoke(cli_mod.app, ["shell", "--file", str(bad)])
    assert result.exit_code == 1
    assert "error: coverage too low" in result.output


def test_shell_reads_a_piped_script_from_stdin() -> None:
    """Tcl's own puts writes to the process's stdout, past CliRunner, so the
    piped script is seen through its exit status and error message."""
    pytest.importorskip("tkinter")

    assert runner.invoke(cli_mod.app, ["shell"], input="set x 1\n").exit_code == 0
    result = runner.invoke(cli_mod.app, ["shell"], input='error "piped script ran"\n')
    assert result.exit_code == 1
    assert "error: piped script ran" in result.output


def test_shell_on_a_terminal_starts_the_repl(monkeypatch) -> None:
    """The REPL itself is tested in test_tcl_shell.py; this is the dispatch to
    it, which CliRunner cannot reach (its stdin is never a terminal)."""
    pytest.importorskip("tkinter")
    import io

    import autombist.tcl_shell as tcl_shell_mod

    class _Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    started: list = []
    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr(tcl_shell_mod.TclShell, "run_repl", lambda self: started.append(self))

    cli_mod.shell(file=None)

    assert len(started) == 1
