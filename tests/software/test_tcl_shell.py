"""Tests for the interactive Tcl shell (Workstream C-D): the shared flag
parser, each command's thin-adapter wiring to its core library function
(monkeypatched, not real tools), catch-trapped error propagation, and the
import-guard that keeps the rest of autombist working when tkinter/Tcl is
unavailable.

Path arguments are always brace-quoted (``_q()`` below), never
double-quoted: Tcl applies backslash substitution inside `"..."` strings, so
a Windows path like `C:\\Users\\...` would have backslashes silently
stripped/reinterpreted (`\\U`/`\\P` -> dropped backslash, `\\t` -> an actual
tab). Braces are Tcl's true literal-quoting form -- no substitution at all --
which is also the idiom any real Tcl/EDA user would reach for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import autombist.tcl_shell as tcl_shell_mod
from autombist.tcl_shell import (
    TclShell,
    TclShellUnavailable,
    _parse_flags,
    _pop_bool,
    _pop_float,
    _pop_int,
    _pop_required,
    _reject_unknown,
    is_available,
)


def _q(value: object) -> str:
    """Brace-quote a value for safe literal embedding in a Tcl command
    string (see module docstring)."""
    return "{" + str(value) + "}"


# ---------------------------------------------------------------------------
# Shared flag parser
# ---------------------------------------------------------------------------


def test_parse_flags_pairs_flag_and_value() -> None:
    flags = _parse_flags(("-config", "c.yml", "-out", "out"))
    assert flags == {"-config": "c.yml", "-out": "out"}


def test_parse_flags_bare_trailing_switch_is_boolean_one() -> None:
    flags = _parse_flags(("-config", "c.yml", "-test"))
    assert flags == {"-config": "c.yml", "-test": "1"}


def test_parse_flags_rejects_non_flag_leading_token() -> None:
    with pytest.raises(ValueError, match="expected a -flag"):
        _parse_flags(("not-a-flag", "value"))


def test_pop_helpers() -> None:
    flags = {"-a": "5", "-b": "true", "-c": "1.5"}
    assert _pop_int(flags, "-a") == 5
    assert _pop_bool(flags, "-b") is True
    assert _pop_float(flags, "-c") == 1.5
    assert flags == {}  # all popped


def test_pop_bool_falsy_strings() -> None:
    for text in ("0", "false", "no", "off", ""):
        flags = {"-x": text}
        assert _pop_bool(flags, "-x") is False


def test_pop_bool_truthy_strings() -> None:
    for text in ("1", "true", "yes", "on"):
        flags = {"-x": text}
        assert _pop_bool(flags, "-x") is True


def test_parse_flags_accepts_negative_number_as_value() -> None:
    # A dash-leading VALUE (e.g. a negative seed) must not be mistaken for
    # the start of a new flag.
    flags = _parse_flags(("-seed", "-5", "-min-coverage", "-1.5"))
    assert flags == {"-seed": "-5", "-min-coverage": "-1.5"}


def test_parse_flags_still_treats_dash_word_as_new_flag() -> None:
    flags = _parse_flags(("-config", "-algo", "march-c"))
    assert flags == {"-config": "1", "-algo": "march-c"}


def test_pop_required_raises_when_missing() -> None:
    with pytest.raises(ValueError, match="missing required flag -config"):
        _pop_required({}, "-config")


def test_reject_unknown_raises_on_leftover_flags() -> None:
    with pytest.raises(ValueError, match="unknown flag"):
        _reject_unknown({"-bogus": "1"})


def test_reject_unknown_passes_when_empty() -> None:
    _reject_unknown({})  # must not raise


def test_error_message_is_not_double_prefixed_with_command_name(shell: "TclShell") -> None:
    caught = shell.eval(
        "if {[catch {generate -out out} err]} { set result $err } else { set result {NO ERROR} }"
    )
    assert caught == "generate: missing required flag -config"


# ---------------------------------------------------------------------------
# Command wiring: each Tcl command must call the SAME core function the CLI
# calls, with correctly-parsed arguments, and return a Tcl-usable value.
# ---------------------------------------------------------------------------


@pytest.fixture
def shell() -> TclShell:
    return TclShell()


def test_generate_command_calls_generate_from_config(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    wrapper_path = tmp_path / "out" / "sram_1rw" / "sram_1rw_mbist.v"

    captured: dict[str, object] = {}

    def fake_generate_from_config(config, out, **kwargs):
        captured["config"] = config
        captured["out"] = out
        captured["kwargs"] = kwargs
        return wrapper_path

    monkeypatch.setattr(tcl_shell_mod, "generate_from_config", fake_generate_from_config)

    result = shell.eval(f"generate -config {_q(config_path)} -out {_q(tmp_path / 'out')} -algo march-raw")

    assert result == str(wrapper_path)
    assert captured["config"] == config_path
    assert captured["kwargs"]["algo"] == "march-raw"
    assert captured["kwargs"]["use_saboteur"] is False


def test_generate_command_test_flag_sets_use_saboteur(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_generate_from_config(config, out, **kwargs):
        captured["kwargs"] = kwargs
        return tmp_path / "wrapper.v"

    monkeypatch.setattr(tcl_shell_mod, "generate_from_config", fake_generate_from_config)

    shell.eval(f"generate -config {_q(config_path)} -test -faults 20 -seed 7")

    assert captured["kwargs"]["use_saboteur"] is True
    assert captured["kwargs"]["faults"] == 20
    assert captured["kwargs"]["fault_seed"] == 7


def test_simulate_command_calls_run_simulation_and_returns_coverage(
    shell: TclShell, tmp_path: Path, monkeypatch
) -> None:
    module_outdir = tmp_path / "out" / "sram_1rw"
    module_outdir.mkdir(parents=True)
    (module_outdir / "sram_1rw_mbist.v").write_text("// stub\n", encoding="utf-8")
    (module_outdir / "config.yml").write_text("memory_name: sram_1rw\n", encoding="utf-8")

    class _FakeResult:
        report = {"fault_metrics": {"coverage_percent": 92.5}, "status": "pass"}

    captured: dict[str, object] = {}

    def fake_run_simulation(outdir, *, verbose=False):
        captured["outdir"] = outdir
        captured["verbose"] = verbose
        return _FakeResult()

    monkeypatch.setattr(tcl_shell_mod, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(
        tcl_shell_mod, "format_simulation_summary", lambda report: "SUMMARY"
    )

    result = shell.eval(f"simulate -out {_q(tmp_path / 'out')} -verbose")

    assert result == "92.5"
    assert captured["outdir"] == module_outdir
    assert captured["verbose"] is True


def test_simulate_command_min_coverage_gate_raises_catchable_error(
    shell: TclShell, tmp_path: Path, monkeypatch
) -> None:
    module_outdir = tmp_path / "out" / "sram_1rw"
    module_outdir.mkdir(parents=True)
    (module_outdir / "sram_1rw_mbist.v").write_text("// stub\n", encoding="utf-8")
    (module_outdir / "config.yml").write_text("memory_name: sram_1rw\n", encoding="utf-8")

    class _FakeResult:
        report = {"fault_metrics": {"coverage_percent": 50.0}, "status": "pass"}

    monkeypatch.setattr(tcl_shell_mod, "run_simulation", lambda outdir, **kw: _FakeResult())
    monkeypatch.setattr(tcl_shell_mod, "format_simulation_summary", lambda report: "SUMMARY")

    caught = shell.eval(
        f"if {{[catch {{simulate -out {_q(tmp_path / 'out')} -min-coverage 90}} err]}} "
        "{ set result $err } else { set result {NO ERROR} }"
    )
    assert "below -min-coverage" in caught


def test_run_command_generates_then_simulates(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    module_outdir = tmp_path / "out" / "sram_1rw"
    wrapper_path = module_outdir / "sram_1rw_mbist.v"

    calls: list[str] = []

    def fake_generate_from_config(config, out, **kwargs):
        calls.append("generate")
        module_outdir.mkdir(parents=True, exist_ok=True)
        return wrapper_path

    class _FakeResult:
        report = {"fault_metrics": {"coverage_percent": 100.0}, "status": "pass"}

    def fake_run_simulation(outdir, **kwargs):
        calls.append("simulate")
        assert outdir == module_outdir
        return _FakeResult()

    monkeypatch.setattr(tcl_shell_mod, "generate_from_config", fake_generate_from_config)
    monkeypatch.setattr(tcl_shell_mod, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(tcl_shell_mod, "format_simulation_summary", lambda report: "SUMMARY")

    result = shell.eval(f"run -config {_q(config_path)} -out {_q(tmp_path / 'out')}")

    assert calls == ["generate", "simulate"]
    assert result == "100.0"


def test_run_command_faultflow_grades_controller_after_simulate(
    shell: TclShell, tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    module_outdir = tmp_path / "out" / "sram_1rw"
    wrapper_path = module_outdir / "sram_1rw_mbist.v"

    calls: list[str] = []

    def fake_generate_from_config(config, out, **kwargs):
        calls.append("generate")
        module_outdir.mkdir(parents=True, exist_ok=True)
        return wrapper_path

    class _FakeResult:
        report = {"fault_metrics": {"coverage_percent": 100.0}, "status": "pass"}

    def fake_run_simulation(outdir, **kwargs):
        calls.append("simulate")
        return _FakeResult()

    def fake_run_controller_grading(outdir, opts, *, run=True):
        calls.append("grade")
        return {"coverage_percent": 91.0, "detected": 9, "denominator": 10}

    monkeypatch.setattr(tcl_shell_mod, "generate_from_config", fake_generate_from_config)
    monkeypatch.setattr(tcl_shell_mod, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(tcl_shell_mod, "format_simulation_summary", lambda report: "SUMMARY")
    monkeypatch.setattr(tcl_shell_mod, "run_controller_grading", fake_run_controller_grading)

    result = shell.eval(f"run -config {_q(config_path)} -out {_q(tmp_path / 'out')} -faultflow")

    assert calls == ["generate", "simulate", "grade"]
    # -faultflow is a side effect (mirrors the CLI); `run` still returns the
    # simulate coverage, not the controller-grading coverage.
    assert result == "100.0"


def test_test_command_calls_run_algo_campaign(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    faults_path = tmp_path / "faults.txt"
    faults_path.write_text("SA0 3 0 0 0 0 0\n", encoding="ascii")

    class _FakeSpec:
        name = "march_c"
        length_n = 10

    class _FakeResult:
        total = 1
        detected = 1
        coverage_percent = 100.0
        sequence = None

    captured: dict[str, object] = {}

    def fake_resolve_algo(name):
        captured["algo"] = name
        return _FakeSpec()

    def fake_run_algo_campaign(mem, spec, records, **kwargs):
        captured["mem"] = mem
        captured["kwargs"] = kwargs
        return _FakeResult()

    monkeypatch.setattr("autombist.alg_spec.resolve_algo", fake_resolve_algo)
    monkeypatch.setattr("autombist.algo_engine.run_algo_campaign", fake_run_algo_campaign)

    result = shell.eval(f"test -addr-width 8 -data-width 8 -algo march_c -faults {_q(faults_path)}")

    assert result == "100.0"
    assert captured["algo"] == "march_c"
    assert captured["mem"].addr_width == 8
    assert captured["mem"].data_width == 8


def test_test_command_missing_required_flag_is_catchable(shell: TclShell, tmp_path: Path) -> None:
    caught = shell.eval(
        "if {[catch {test -data-width 8 -faults x.txt} err]} "
        "{ set result $err } else { set result {NO ERROR} }"
    )
    assert "missing required flag -addr-width" in caught


def test_test_command_fault_types_threads_fault_ram_sv(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    faults_path = tmp_path / "faults.txt"
    faults_path.write_text("SA0 3 0 0 0 0 0\n", encoding="ascii")
    fault_types_path = tmp_path / "types.json"
    fault_types_path.write_text(
        '[{"name": "custom_flip", "kind": "stuck_at", "value": 0}]', encoding="utf-8"
    )

    class _FakeSpec:
        name = "march_c"
        length_n = 10

    class _FakeResult:
        total = 1
        detected = 1
        coverage_percent = 100.0
        sequence = None

    class _FakePrimitive:
        name = "custom_flip"

    captured: dict[str, object] = {}

    monkeypatch.setattr("autombist.alg_spec.resolve_algo", lambda name: _FakeSpec())
    monkeypatch.setattr(
        "autombist.fault_primitives.default_registry", lambda: []
    )
    monkeypatch.setattr(
        "autombist.fault_primitives.from_dict", lambda spec_dict: _FakePrimitive()
    )
    monkeypatch.setattr(
        "autombist.fault_primitives.validate", lambda prim, existing_names: None
    )

    def fake_render_and_write(registry, path):
        captured["registry_names"] = [p.name for p in registry]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// fault ram\n", encoding="utf-8")
        return path

    monkeypatch.setattr("autombist.fault_ram_gen.render_and_write", fake_render_and_write)

    def fake_run_algo_campaign(mem, spec, records, **kwargs):
        captured["fault_ram_sv"] = kwargs.get("fault_ram_sv")
        return _FakeResult()

    monkeypatch.setattr("autombist.algo_engine.run_algo_campaign", fake_run_algo_campaign)

    result = shell.eval(
        f"test -addr-width 8 -data-width 8 -algo march_c -faults {_q(faults_path)} "
        f"-fault-types {_q(fault_types_path)}"
    )

    assert result == "100.0"
    assert captured["registry_names"] == ["custom_flip"]
    assert captured["fault_ram_sv"] is not None


def test_harden_command_calls_build_librelane_config(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "harden.yml"
    config_path.write_text("design_name: stub\n", encoding="utf-8")
    out_path = tmp_path / "librelane-config.json"

    captured: dict[str, object] = {}

    def fake_build_librelane_config(loaded):
        captured["loaded"] = loaded
        return {"DESIGN_NAME": "stub"}

    monkeypatch.setattr(tcl_shell_mod, "build_librelane_config", fake_build_librelane_config)

    result = shell.eval(f"harden -config {_q(config_path)} -out {_q(out_path)}")

    assert result == str(out_path)
    assert captured["loaded"] == {"design_name": "stub"}
    assert out_path.exists()


def test_harden_command_run_defaults_to_pinned_librelane_ref(
    shell: TclShell, tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "harden.yml"
    config_path.write_text("design_name: stub\n", encoding="utf-8")
    out_path = tmp_path / "librelane-config.json"

    captured: dict[str, object] = {}

    class _FakeCompleted:
        returncode = 0

    monkeypatch.setattr(tcl_shell_mod, "build_librelane_config", lambda loaded: {"DESIGN_NAME": "stub"})
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/nix")

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr("subprocess.run", fake_run)

    shell.eval(f"harden -config {_q(config_path)} -out {_q(out_path)} -run")

    assert tcl_shell_mod.LIBRELANE_FLAKE_REF in captured["cmd"]


def test_harden_command_run_librelane_ref_override(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "harden.yml"
    config_path.write_text("design_name: stub\n", encoding="utf-8")
    out_path = tmp_path / "librelane-config.json"

    captured: dict[str, object] = {}

    class _FakeCompleted:
        returncode = 0

    monkeypatch.setattr(tcl_shell_mod, "build_librelane_config", lambda loaded: {"DESIGN_NAME": "stub"})
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/nix")

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr("subprocess.run", fake_run)

    shell.eval(
        f"harden -config {_q(config_path)} -out {_q(out_path)} -run "
        "-librelane-ref github:librelane/librelane/3.1.0.dev2"
    )

    assert "github:librelane/librelane/3.1.0.dev2" in captured["cmd"]
    assert tcl_shell_mod.LIBRELANE_FLAKE_REF not in captured["cmd"]


def test_harden_command_missing_config_raises_clean_message(shell: TclShell, tmp_path: Path) -> None:
    missing_config = tmp_path / "nope.yml"
    caught = shell.eval(
        f"if {{[catch {{harden -config {_q(missing_config)}}} err]}} "
        "{ set result $err } else { set result {NO ERROR} }"
    )
    assert caught == f"harden: harden config not found: {missing_config}"


def test_fix_lef_units_command_calls_normalize_lef_units(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    lef_path = tmp_path / "macro.lef"
    lef_path.write_text("DATABASE MICRONS 2000 ;\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_normalize(text, *, target_dbu=1000):
        captured["text"] = text
        captured["target_dbu"] = target_dbu
        return ("DATABASE MICRONS 1000 ;\n", 3)

    monkeypatch.setattr(tcl_shell_mod, "normalize_lef_units", fake_normalize)

    result = shell.eval(f"fix_lef_units {_q(lef_path)} -target-dbu 1000")

    assert result == str(lef_path)
    assert captured["target_dbu"] == 1000
    assert lef_path.read_text(encoding="utf-8") == "DATABASE MICRONS 1000 ;\n"


def test_macro_signoff_command_calls_build_macro_signoff_command(
    shell: TclShell, tmp_path: Path, monkeypatch
) -> None:
    script = tmp_path / "run_macro_signoff.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_build_cmd(script_arg, macros):
        captured["script"] = script_arg
        captured["macros"] = macros
        return ["bash", str(script_arg)]

    class _FakeCompleted:
        returncode = 0

    monkeypatch.setattr(tcl_shell_mod, "build_macro_signoff_command", fake_build_cmd)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/bash")
    monkeypatch.setattr("subprocess.run", lambda cmd: _FakeCompleted())

    result = shell.eval(f"macro_signoff sram_1rw sram_tiny -script {_q(script)}")

    assert result == "0"
    assert captured["script"] == script
    assert captured["macros"] == ["sram_1rw", "sram_tiny"]


def test_macro_signoff_show_command_skips_subprocess(
    shell: TclShell, tmp_path: Path, monkeypatch
) -> None:
    script = tmp_path / "run_macro_signoff.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    monkeypatch.setattr(
        tcl_shell_mod, "build_macro_signoff_command", lambda script_arg, macros: ["bash", str(script_arg)]
    )

    def _boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called with -show-command")

    monkeypatch.setattr("subprocess.run", _boom)

    result = shell.eval(f"macro_signoff -script {_q(script)} -show-command")

    assert result == f"bash {script}"


def test_grade_controller_command_calls_run_controller_grading(
    shell: TclShell, tmp_path: Path, monkeypatch
) -> None:
    module_outdir = tmp_path / "out" / "sram_1rw"
    module_outdir.mkdir(parents=True)
    (module_outdir / "sram_1rw_mbist.v").write_text("// stub\n", encoding="utf-8")
    (module_outdir / "config.yml").write_text("memory_name: sram_1rw\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run_controller_grading(outdir, opts, *, run=True):
        captured["outdir"] = outdir
        captured["run"] = run
        captured["opts"] = opts
        return {"coverage_percent": 91.0, "detected": 9, "denominator": 10, "excluded_blackbox": 2}

    monkeypatch.setattr(tcl_shell_mod, "run_controller_grading", fake_run_controller_grading)

    result = shell.eval(
        f"grade_controller -out {_q(tmp_path / 'out')} -threshold 95 -max-rounds 5"
    )

    assert result == "91.0"
    assert captured["outdir"] == module_outdir
    assert captured["run"] is True
    assert captured["opts"].threshold == 95.0
    assert captured["opts"].max_rounds == 5


def test_grade_controller_merges_faultflow_coverage_into_latest_report(
    shell: TclShell, tmp_path: Path, monkeypatch
) -> None:
    import json

    module_outdir = tmp_path / "out" / "sram_1rw"
    reports_dir = module_outdir / "reports"
    reports_dir.mkdir(parents=True)
    (module_outdir / "sram_1rw_mbist.v").write_text("// stub\n", encoding="utf-8")
    (module_outdir / "config.yml").write_text("memory_name: sram_1rw\n", encoding="utf-8")
    (reports_dir / "latest.json").write_text(
        json.dumps({"fault_metrics": {"coverage_percent": 100.0}}), encoding="utf-8"
    )

    monkeypatch.setattr(
        tcl_shell_mod,
        "run_controller_grading",
        lambda outdir, opts, *, run=True: {"coverage_percent": 91.0, "detected": 9, "denominator": 10},
    )

    shell.eval(f"grade_controller -out {_q(tmp_path / 'out')}")

    merged = json.loads((reports_dir / "latest.json").read_text(encoding="utf-8"))
    assert merged["controller_grading"]["coverage_percent"] == 91.0


def test_ram_synth_command_calls_run_openram_synthesis(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text("num_words: 256\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_load_openram_config(path):
        captured["path"] = path
        return {"num_words": 256}

    class _FakeCompleted:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_run_openram_synthesis(path):
        return _FakeCompleted()

    monkeypatch.setattr(tcl_shell_mod, "load_openram_config", fake_load_openram_config)
    monkeypatch.setattr(tcl_shell_mod, "run_openram_synthesis", fake_run_openram_synthesis)

    result = shell.eval(f"ram_synth -config {_q(config_path)}")

    assert result == "0"
    assert captured["path"] == config_path


def test_ram_synth_command_raises_on_nonzero_exit(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text("num_words: 256\n", encoding="utf-8")

    class _FakeFailedCompleted:
        stdout = ""
        stderr = "synthesis failed\n"
        returncode = 1

    monkeypatch.setattr(tcl_shell_mod, "load_openram_config", lambda path: {"num_words": 256})
    monkeypatch.setattr(tcl_shell_mod, "run_openram_synthesis", lambda path: _FakeFailedCompleted())

    caught = shell.eval(
        f"if {{[catch {{ram_synth -config {_q(config_path)}}} err]}} "
        "{ set result $err } else { set result {NO ERROR} }"
    )
    assert "exited with code 1" in caught


def test_wrap_prints_traceback_for_unexpected_exception_but_not_runtimeerror(
    shell: TclShell, tmp_path: Path, monkeypatch, capsys
) -> None:
    """A RuntimeError (the convention every _cmd_* uses for already-clean,
    expected domain errors) must not dump a traceback. Anything else escaping
    unrewrapped is a genuine surprise and should still be diagnosable."""
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")

    def raise_type_error(config, out, **kwargs):
        raise TypeError("boom: unexpected argument shape")

    monkeypatch.setattr(tcl_shell_mod, "generate_from_config", raise_type_error)

    caught = shell.eval(
        f"if {{[catch {{generate -config {_q(config_path)}}} err]}} "
        "{ set result $err } else { set result {NO ERROR} }"
    )
    assert "boom: unexpected argument shape" in caught
    stderr = capsys.readouterr().err
    assert "Traceback" in stderr
    assert "TypeError" in stderr


def test_doctor_command_returns_missing_tools_list(shell: TclShell, monkeypatch) -> None:
    fake_rows = [
        ("make", "OK", "simulate, run", "/usr/bin/make"),
        ("verilator", "MISSING", "test", "not found on PATH"),
    ]
    monkeypatch.setattr("autombist.cli._doctor_checks", lambda: fake_rows)

    result = shell.eval("doctor")

    assert result == "verilator"


# ---------------------------------------------------------------------------
# Import-guard: the rest of autombist must keep working when tkinter/Tcl is
# unavailable, and TclShell() must fail with a clear, actionable message.
# ---------------------------------------------------------------------------


def test_tclshell_unavailable_when_tkinter_absent(monkeypatch) -> None:
    monkeypatch.setattr(tcl_shell_mod, "tkinter", None)
    monkeypatch.setattr(tcl_shell_mod, "_TKINTER_IMPORT_ERROR", ImportError("no module named tkinter"))

    with pytest.raises(TclShellUnavailable, match="Tcl shell unavailable"):
        TclShell()


def test_is_available_reflects_tkinter_presence(monkeypatch) -> None:
    monkeypatch.setattr(tcl_shell_mod, "tkinter", None)
    assert is_available() is False


def test_cli_shell_command_reports_gracefully_when_tkinter_absent(monkeypatch) -> None:
    """The Typer `shell` command must not crash the whole CLI process when
    tkinter/Tcl is unavailable -- it should exit 1 with an actionable
    message, same discipline as the other preflight-tool guards."""
    from typer.testing import CliRunner

    import autombist.cli as cli_mod

    monkeypatch.setattr(tcl_shell_mod, "tkinter", None)
    monkeypatch.setattr(tcl_shell_mod, "_TKINTER_IMPORT_ERROR", ImportError("no tkinter"))

    runner = CliRunner()
    result = runner.invoke(cli_mod.app, ["shell", "--file", "nonexistent.tcl"])

    assert result.exit_code == 1
    assert "Tcl shell unavailable" in result.output


def test_autombist_cli_still_imports_when_tkinter_stubbed_absent(monkeypatch) -> None:
    """Importing autombist.cli (and every command it registers) must not
    require tkinter at all -- the shell command only imports tcl_shell
    lazily, inside its own function body."""
    import importlib

    import autombist.cli as cli_mod

    # Re-import cli.py with tkinter unimportable at the sys.modules level,
    # proving the module-level import graph doesn't require it.
    monkeypatch.setitem(sys.modules, "tkinter", None)
    importlib.reload(cli_mod)
    assert hasattr(cli_mod, "app")
    # Restore real state for any tests that run after this one.
    monkeypatch.undo()
    importlib.reload(cli_mod)
