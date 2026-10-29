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
    with pytest.raises(RuntimeError, match="expected a -flag"):
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
    with pytest.raises(RuntimeError, match="missing required flag -config"):
        _pop_required({}, "-config")


def test_pop_required_names_a_typo_d_flag_present_in_the_remaining_flags() -> None:
    """A typo'd flag (-confi for -config) still parses fine as its own flag,
    so it sits in the dict under the wrong key while -config is genuinely
    absent -- the naive message blames the wrong thing (implies nothing was
    passed at all). Check for a close spelling match among what's actually
    there and name it directly."""
    with pytest.raises(RuntimeError, match=r"missing required flag -config -- got '-confi', did you mean '-config'\?"):
        _pop_required({"-confi": "c.yml"}, "-config")


def test_reject_unknown_raises_on_leftover_flags() -> None:
    with pytest.raises(RuntimeError, match="unknown flag"):
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


def test_simulate_command_min_coverage_gate_fails_when_coverage_was_never_reported(
    shell: TclShell, tmp_path: Path, monkeypatch
) -> None:
    """The Tcl twin of test_run_min_coverage_gate_fails_when_coverage_was_never_
    reported in test_cli.py: a gate that was actually requested must not
    silently pass just because coverage_percent is missing, and the error
    path must not crash trying to format None with :.2f."""
    module_outdir = tmp_path / "out" / "sram_1rw"
    module_outdir.mkdir(parents=True)
    (module_outdir / "sram_1rw_mbist.v").write_text("// stub\n", encoding="utf-8")
    (module_outdir / "config.yml").write_text("memory_name: sram_1rw\n", encoding="utf-8")

    class _FakeResult:
        report = {"fault_metrics": {}, "status": "pass"}

    monkeypatch.setattr(tcl_shell_mod, "run_simulation", lambda outdir, **kw: _FakeResult())
    monkeypatch.setattr(tcl_shell_mod, "format_simulation_summary", lambda report: "SUMMARY")

    caught = shell.eval(
        f"if {{[catch {{simulate -out {_q(tmp_path / 'out')} -min-coverage 90}} err]}} "
        "{ set result $err } else { set result {NO ERROR} }"
    )
    assert "reported no coverage number" in caught


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


_REAL_CONFIG = """\
memory_name: sram_1rw
wrapper_module_name: sram_1rw_mbist
addr_width: 4
data_width: 8
we_active_low: true
ports: {clk: clk0, addr: addr0, din: din0, dout: dout0, we: web0, csb: csb0}
"""


def test_generate_command_emit_manifest_writes_the_manifest(shell: TclShell, tmp_path: Path) -> None:
    """-emit-manifest is the CLI's --emit-manifest: generate for real, then
    manifest.json from the config.yml snapshot next to the wrapper."""
    import json

    from autombist import __version__

    config_path = tmp_path / "config.yml"
    config_path.write_text(_REAL_CONFIG, encoding="utf-8")

    plain = shell.eval(f"generate -config {_q(config_path)} -out {_q(tmp_path / 'plain')}")
    assert not (Path(plain).parent / "manifest.json").exists()

    result = shell.eval(f"generate -config {_q(config_path)} -out {_q(tmp_path / 'out')} -emit-manifest")
    manifest = json.loads((Path(result).parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tool_version"] == __version__
    assert any(i["hierarchical_path"].endswith("u_algo_top") for i in manifest["instances"])


def test_run_command_emit_manifest_writes_it_after_simulating(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(_REAL_CONFIG, encoding="utf-8")

    class _FakeResult:
        report = {"fault_metrics": {"coverage_percent": 100.0}, "status": "pass"}

    monkeypatch.setattr(tcl_shell_mod, "run_simulation", lambda outdir, **kwargs: _FakeResult())
    monkeypatch.setattr(tcl_shell_mod, "format_simulation_summary", lambda report: "SUMMARY")

    result = shell.eval(f"run -config {_q(config_path)} -out {_q(tmp_path / 'out')} -emit-manifest")

    assert result == "100.0"
    assert (tmp_path / "out" / "sram_1rw" / "manifest.json").is_file()


def test_emit_manifest_without_a_config_snapshot_is_a_tcl_error(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    module_outdir = tmp_path / "out" / "sram_1rw"

    def fake_generate_from_config(config, out, **kwargs):
        module_outdir.mkdir(parents=True, exist_ok=True)
        return module_outdir / "sram_1rw_mbist.v"

    monkeypatch.setattr(tcl_shell_mod, "generate_from_config", fake_generate_from_config)

    caught = shell.eval(
        f"if {{[catch {{generate -config {_q(config_path)} -out {_q(tmp_path / 'out')} -emit-manifest}} err]}}"
        " { set result $err } else { set result {NO ERROR} }"
    )
    assert caught.startswith("generate: ") and "config.yml" in caught


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
        return {"coverage_percent": 91.0, "detected": 9, "denominator": 10, "blackbox_unresolved": 2}

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


def test_wrap_does_not_print_traceback_for_a_typo_d_flag(
    shell: TclShell, capsys
) -> None:
    """The regression this guards: _parse_flags/_pop_required/_reject_unknown
    used to raise bare ValueError, which _wrap's isinstance(exc, RuntimeError)
    check does NOT treat as expected -- so a simple typo'd flag dumped a full
    Python traceback to stderr, exactly like a genuine internal bug would.
    These are ordinary user-input mistakes and must produce a clean message
    with no traceback, matching every other _cmd_* domain error."""
    caught = shell.eval(
        "if {[catch {generate -config c.yml -tets} err]} { set result $err } else { set result {NO ERROR} }"
    )
    assert caught == "generate: unknown flag(s): -tets"
    stderr = capsys.readouterr().err
    assert "Traceback" not in stderr


def test_wrap_does_not_print_traceback_for_a_typo_d_required_flag(
    shell: TclShell, capsys
) -> None:
    caught = shell.eval(
        "if {[catch {generate -confi c.yml} err]} { set result $err } else { set result {NO ERROR} }"
    )
    assert caught == "generate: missing required flag -config -- got '-confi', did you mean '-config'?"
    stderr = capsys.readouterr().err
    assert "Traceback" not in stderr


def test_pop_int_bad_value_raises_runtimeerror_not_bare_valueerror() -> None:
    with pytest.raises(RuntimeError, match=r"flag -seed expects an integer, got 'abc'"):
        _pop_int({"-seed": "abc"}, "-seed")


def test_pop_float_bad_value_raises_runtimeerror_not_bare_valueerror() -> None:
    with pytest.raises(RuntimeError, match=r"flag -min-coverage expects a number, got 'abc'"):
        _pop_float({"-min-coverage": "abc"}, "-min-coverage")


def test_test_command_non_integer_width_is_a_clean_tcl_error(shell: TclShell, capsys) -> None:
    """-addr-width and -data-width are required integers: a value that is not
    one gets _pop_int's message, not a bare int() ValueError's traceback."""
    caught = shell.eval(
        "if {[catch {test -addr-width eight -data-width 8 -faults x.txt} err]} "
        "{ set result $err } else { set result {NO ERROR} }"
    )
    assert caught == "test: flag -addr-width expects an integer, got 'eight'"
    assert "Traceback" not in capsys.readouterr().err


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


# ---------------------------------------------------------------------------
# Error paths: every failure a command can meet comes back as a catchable Tcl
# error with a clean message, never a Python traceback.
# ---------------------------------------------------------------------------


def _tcl_error(shell: TclShell, command: str) -> str:
    """Run ``command`` under Tcl's catch and return its error message."""
    return shell.eval(
        f"if {{[catch {{{command}}} err]}} {{ set result $err }} else {{ set result {{NO ERROR}} }}"
    )


def _module_outdir(tmp_path: Path) -> Path:
    module_outdir = tmp_path / "out" / "sram_1rw"
    module_outdir.mkdir(parents=True)
    (module_outdir / "sram_1rw_mbist.v").write_text("// stub\n", encoding="utf-8")
    (module_outdir / "config.yml").write_text("memory_name: sram_1rw\n", encoding="utf-8")
    return module_outdir


def _config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text("memory_name: sram_1rw\n", encoding="utf-8")
    return config_path


def _raise(exc: BaseException):
    def fake(*args, **kwargs):
        raise exc

    return fake


class _FakeSpec:
    name = "march_c"
    length_n = 10


class _FakeCampaign:
    total = 4
    detected = 3
    coverage_percent = 75.0
    sequence = None


def _faults_file(tmp_path: Path) -> Path:
    faults_path = tmp_path / "faults.txt"
    faults_path.write_text("SA0 3 0 0 0 0 0\n", encoding="ascii")
    return faults_path


def test_generate_command_config_error_is_a_clean_tcl_error(
    shell: TclShell, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        tcl_shell_mod, "generate_from_config", _raise(tcl_shell_mod.ConfigError("addr_width must be > 0"))
    )

    caught = _tcl_error(shell, f"generate -config {_q(_config(tmp_path))}")

    assert caught == "generate: addr_width must be > 0"
    assert "Traceback" not in capsys.readouterr().err


def test_simulate_command_missing_out_directory_is_a_tcl_error(shell: TclShell, tmp_path: Path) -> None:
    caught = _tcl_error(shell, f"simulate -out {_q(tmp_path / 'nowhere')}")
    assert caught.startswith("simulate: Output directory not found")


def test_simulate_command_simulation_error_is_a_tcl_error(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    _module_outdir(tmp_path)
    monkeypatch.setattr(tcl_shell_mod, "run_simulation", _raise(tcl_shell_mod.SimulationError("make exited 2")))

    caught = _tcl_error(shell, f"simulate -out {_q(tmp_path / 'out')}")

    assert caught == "simulate: make exited 2"


def test_run_command_generate_error_stops_before_simulating(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tcl_shell_mod, "generate_from_config", _raise(ValueError("unknown algo 'march-q'")))
    monkeypatch.setattr(
        tcl_shell_mod, "run_simulation", _raise(AssertionError("run must not simulate after a failed generate"))
    )

    caught = _tcl_error(shell, f"run -config {_q(_config(tmp_path))}")

    assert caught == "run: unknown algo 'march-q'"


def test_emit_manifest_snapshot_that_is_not_a_mapping_is_a_tcl_error(
    shell: TclShell, tmp_path: Path, monkeypatch
) -> None:
    module_outdir = tmp_path / "out" / "sram_1rw"

    def fake_generate_from_config(config, out, **kwargs):
        module_outdir.mkdir(parents=True, exist_ok=True)
        (module_outdir / "config.yml").write_text("- not\n- a mapping\n", encoding="utf-8")
        return module_outdir / "sram_1rw_mbist.v"

    monkeypatch.setattr(tcl_shell_mod, "generate_from_config", fake_generate_from_config)

    caught = _tcl_error(shell, f"generate -config {_q(_config(tmp_path))} -out {_q(tmp_path / 'out')} -emit-manifest")

    assert caught.startswith("generate: ") and caught.endswith("must be a YAML mapping")


def test_emit_manifest_build_error_is_a_tcl_error(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    from autombist.manifest import ManifestError

    config_path = tmp_path / "real.yml"
    config_path.write_text(_REAL_CONFIG, encoding="utf-8")
    monkeypatch.setattr(
        "autombist.manifest.build_instance_manifest", _raise(ManifestError("no u_algo_top in the wrapper"))
    )

    caught = _tcl_error(shell, f"generate -config {_q(config_path)} -out {_q(tmp_path / 'out')} -emit-manifest")

    assert caught == "generate: no u_algo_top in the wrapper"


def test_test_command_writes_the_report_and_the_diagnosis(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr("autombist.alg_spec.resolve_algo", lambda name: _FakeSpec())
    monkeypatch.setattr("autombist.algo_engine.run_algo_campaign", lambda *args, **kwargs: _FakeCampaign())
    monkeypatch.setattr(
        "autombist.algo_reporting.write_campaign_report",
        lambda result, path, *, fmt: captured.update(report=(path, fmt)),
    )
    monkeypatch.setattr(
        "autombist.algo_reporting.write_diagnosis_report",
        lambda result, path, *, fmt: captured.update(diagnosis=(path, fmt)),
    )

    result = shell.eval(
        f"test -addr-width 4 -data-width 8 -faults {_q(_faults_file(tmp_path))} "
        f"-report {_q(tmp_path / 'cov.json')} -fmt json -diagnosis {_q(tmp_path / 'diag.md')}"
    )

    assert result == "75.0"
    assert captured["report"] == (tmp_path / "cov.json", "json")
    assert captured["diagnosis"] == (tmp_path / "diag.md", "md")


@pytest.mark.parametrize(
    ("flags", "fragment"),
    [
        ("-report r.md -fmt docx", "unknown report format 'docx'"),
        ("-diagnosis d.md -diagnosis-fmt docx", "unknown diagnosis report format 'docx'"),
    ],
)
def test_test_command_bad_format_fails_before_the_campaign_runs(
    shell: TclShell, tmp_path: Path, monkeypatch, flags: str, fragment: str
) -> None:
    monkeypatch.setattr(
        "autombist.algo_engine.run_algo_campaign", _raise(AssertionError("the campaign must not run"))
    )

    caught = _tcl_error(shell, f"test -addr-width 4 -data-width 8 -faults {_q(_faults_file(tmp_path))} {flags}")

    assert caught.startswith("test: ") and fragment in caught


def test_test_command_fault_types_that_is_not_a_list_is_a_tcl_error(shell: TclShell, tmp_path: Path) -> None:
    fault_types_path = tmp_path / "types.json"
    fault_types_path.write_text('{"name": "custom_flip"}', encoding="utf-8")

    caught = _tcl_error(
        shell,
        f"test -addr-width 4 -data-width 8 -faults {_q(_faults_file(tmp_path))} -fault-types {_q(fault_types_path)}",
    )

    assert caught == "test: -fault-types file must contain a JSON list of fault-primitive specs"


def _fake_fsm(monkeypatch, tmp_path: Path, *, matches: bool) -> dict[str, object]:
    captured: dict[str, object] = {}

    class _Ports:
        module_name = "march_c_top"

    class _Sequence:
        observed_count = 10

        def message(self) -> str:
            return "op 4: expected r1, observed r0"

    _Sequence.matches = matches

    class _FsmCampaign(_FakeCampaign):
        sequence = _Sequence()

    def fake_run_fsm_campaign(mem, sources, module_name, records, **kwargs):
        captured.update(sources=sources, module_name=module_name, kwargs=kwargs)
        return _FsmCampaign()

    monkeypatch.setattr("autombist.fsm_harness.gather_sibling_sources", lambda path: [path, path.parent / "pkg.sv"])
    monkeypatch.setattr("autombist.fsm_harness.check_ports", lambda text: _Ports())
    monkeypatch.setattr("autombist.alg_spec.resolve_algo", lambda name: _FakeSpec())
    monkeypatch.setattr("autombist.algo_engine.run_fsm_campaign", fake_run_fsm_campaign)
    fsm_path = tmp_path / "march_c_top.sv"
    fsm_path.write_text("module march_c_top; endmodule\n", encoding="utf-8")
    captured["fsm"] = fsm_path
    return captured


def test_test_command_fsm_with_check_sequence_reports_a_match(
    shell: TclShell, tmp_path: Path, monkeypatch, capsys
) -> None:
    captured = _fake_fsm(monkeypatch, tmp_path, matches=True)

    result = shell.eval(
        f"test -addr-width 4 -data-width 8 -faults {_q(_faults_file(tmp_path))} "
        f"-fsm {_q(captured['fsm'])} -check-sequence"
    )

    assert result == "75.0"
    assert captured["module_name"] == "march_c_top"
    assert len(captured["sources"]) == 2
    assert isinstance(captured["kwargs"]["expected_spec"], _FakeSpec)
    out = capsys.readouterr().out
    assert "FSM:march_c_top (2 source file(s))" in out
    assert "sequence: OK (10 ops match march_c)" in out


def test_test_command_fsm_sequence_mismatch_is_a_tcl_error(
    shell: TclShell, tmp_path: Path, monkeypatch, capsys
) -> None:
    captured = _fake_fsm(monkeypatch, tmp_path, matches=False)

    caught = _tcl_error(
        shell,
        f"test -addr-width 4 -data-width 8 -faults {_q(_faults_file(tmp_path))} "
        f"-fsm {_q(captured['fsm'])} -check-sequence",
    )

    assert caught == "test: controller does not implement its specified march sequence"
    out = capsys.readouterr().out
    assert "sequence: MISMATCH vs march_c" in out
    assert "op 4: expected r1, observed r0" in out


def test_test_command_check_sequence_without_fsm_is_a_tcl_error(shell: TclShell, tmp_path: Path) -> None:
    caught = _tcl_error(
        shell, f"test -addr-width 4 -data-width 8 -faults {_q(_faults_file(tmp_path))} -check-sequence"
    )
    assert caught == "test: -check-sequence requires -fsm"


def test_test_command_min_coverage_gate_is_a_tcl_error(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("autombist.alg_spec.resolve_algo", lambda name: _FakeSpec())
    monkeypatch.setattr("autombist.algo_engine.run_algo_campaign", lambda *args, **kwargs: _FakeCampaign())

    caught = _tcl_error(
        shell, f"test -addr-width 4 -data-width 8 -faults {_q(_faults_file(tmp_path))} -min-coverage 90"
    )

    assert caught == "test: coverage 75.00% is below -min-coverage 90.00%"


def test_harden_command_invalid_config_is_a_tcl_error(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "harden.yml"
    config_path.write_text("design_name: stub\n", encoding="utf-8")
    monkeypatch.setattr(
        tcl_shell_mod, "build_librelane_config", _raise(tcl_shell_mod.SignoffConfigError("die_area is required"))
    )

    caught = _tcl_error(shell, f"harden -config {_q(config_path)} -out {_q(tmp_path / 'll.json')}")

    assert caught == "harden: die_area is required"


def _harden_run(shell: TclShell, tmp_path: Path, monkeypatch, *, nix: str | None, returncode: int = 0) -> str:
    config_path = tmp_path / "harden.yml"
    config_path.write_text("design_name: stub\n", encoding="utf-8")

    class _Completed:
        pass

    _Completed.returncode = returncode
    monkeypatch.setattr(tcl_shell_mod, "build_librelane_config", lambda loaded: {"DESIGN_NAME": "stub"})
    monkeypatch.setattr("shutil.which", lambda name: nix)
    monkeypatch.setattr("subprocess.run", lambda cmd: _Completed())
    return _tcl_error(shell, f"harden -config {_q(config_path)} -out {_q(tmp_path / 'll.json')} -run")


def test_harden_command_run_without_nix_is_a_tcl_error(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    caught = _harden_run(shell, tmp_path, monkeypatch, nix=None)
    assert caught.startswith("harden: 'nix' not found on PATH")


def test_harden_command_run_librelane_failure_is_a_tcl_error(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    caught = _harden_run(shell, tmp_path, monkeypatch, nix="/usr/bin/nix", returncode=3)
    assert caught == "harden: LibreLane exited with code 3"


@pytest.mark.parametrize("command", ["fix_lef_units", "fix_lef_units -target-dbu 1000"])
def test_fix_lef_units_without_a_lef_is_a_clean_tcl_error(shell: TclShell, capsys, command: str) -> None:
    """A usage mistake, like a typo'd flag: one message naming the command
    once, and no traceback."""
    caught = _tcl_error(shell, command)

    assert caught == "fix_lef_units: missing LEF path argument"
    assert "Traceback" not in capsys.readouterr().err


def test_fix_lef_units_missing_lef_file_is_a_tcl_error(shell: TclShell, tmp_path: Path) -> None:
    caught = _tcl_error(shell, f"fix_lef_units {_q(tmp_path / 'missing.lef')}")
    assert caught == f"fix_lef_units: LEF not found: {tmp_path / 'missing.lef'}"


def test_macro_signoff_missing_script_is_a_tcl_error(shell: TclShell, tmp_path: Path) -> None:
    caught = _tcl_error(shell, f"macro_signoff -script {_q(tmp_path / 'missing.sh')}")
    assert caught == f"macro_signoff: signoff script not found: {tmp_path / 'missing.sh'}"


@pytest.mark.parametrize(
    ("bash", "returncode", "expected"),
    [
        (None, 0, "macro_signoff: 'bash' not found on PATH -- macro_signoff runs the script via bash"),
        ("/usr/bin/bash", 2, "macro_signoff: macro-signoff exited with code 2"),
    ],
)
def test_macro_signoff_run_failures_are_tcl_errors(
    shell: TclShell, tmp_path: Path, monkeypatch, bash: str | None, returncode: int, expected: str
) -> None:
    script = tmp_path / "run_macro_signoff.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    class _Completed:
        pass

    _Completed.returncode = returncode
    monkeypatch.setattr("shutil.which", lambda name: bash)
    monkeypatch.setattr("subprocess.run", lambda cmd: _Completed())

    assert _tcl_error(shell, f"macro_signoff -script {_q(script)}") == expected


def test_grade_controller_faultflow_error_is_a_tcl_error(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    from autombist.faultflow_flow import FaultFlowError

    _module_outdir(tmp_path)
    monkeypatch.setattr(tcl_shell_mod, "run_controller_grading", _raise(FaultFlowError("FAULTFLOW_HOME is not set")))

    caught = _tcl_error(shell, f"grade_controller -out {_q(tmp_path / 'out')}")

    assert caught == "grade_controller: FAULTFLOW_HOME is not set"


def test_grade_controller_without_run_returns_the_bundle(
    shell: TclShell, tmp_path: Path, monkeypatch, capsys
) -> None:
    module_outdir = _module_outdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_run_controller_grading(outdir, opts, *, run=True):
        captured["run"] = run
        return None

    monkeypatch.setattr(tcl_shell_mod, "run_controller_grading", fake_run_controller_grading)

    result = shell.eval(f"grade_controller -out {_q(tmp_path / 'out')} -run 0")

    assert result == str(module_outdir / "faultflow")
    assert captured["run"] is False
    assert f"Emitted FaultFlow bundle: {module_outdir / 'faultflow'}" in capsys.readouterr().out


def test_grade_controller_leaves_an_unreadable_report_alone(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    """A corrupt reports/latest.json is not grading's to fix: the coverage is
    still returned and the file is left as it was."""
    module_outdir = _module_outdir(tmp_path)
    (module_outdir / "reports").mkdir()
    latest = module_outdir / "reports" / "latest.json"
    latest.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(
        tcl_shell_mod,
        "run_controller_grading",
        lambda outdir, opts, *, run=True: {"coverage_percent": 91.0, "detected": 9, "denominator": 10},
    )

    result = shell.eval(f"grade_controller -out {_q(tmp_path / 'out')}")

    assert result == "91.0"
    assert latest.read_text(encoding="utf-8") == "{not json"


def test_grade_controller_missing_out_directory_is_a_tcl_error(shell: TclShell, tmp_path: Path) -> None:
    caught = _tcl_error(shell, f"grade_controller -out {_q(tmp_path / 'nowhere')}")
    assert caught.startswith("grade_controller: Output directory not found")


def test_ram_synth_show_command_prints_the_command_and_the_output(
    shell: TclShell, tmp_path: Path, monkeypatch, capsys
) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text("num_words: 256\n", encoding="utf-8")

    class _Completed:
        stdout = "OpenRAM: done\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(tcl_shell_mod, "load_openram_config", lambda path: {"num_words": 256})
    monkeypatch.setattr(
        tcl_shell_mod, "build_openram_command_args", lambda cfg, path: ["python3", "synthesize_sram.py", 256]
    )
    monkeypatch.setattr(tcl_shell_mod, "run_openram_synthesis", lambda path: _Completed())

    result = shell.eval(f"ram_synth -config {_q(config_path)} -show-command")

    assert result == "0"
    out = capsys.readouterr().out
    assert "$ python3 synthesize_sram.py 256" in out
    assert "OpenRAM: done" in out


def test_ram_synth_invalid_config_is_a_tcl_error(shell: TclShell, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text("num_words: -1\n", encoding="utf-8")
    monkeypatch.setattr(
        tcl_shell_mod, "load_openram_config", _raise(tcl_shell_mod.OpenRAMConfigError("num_words must be > 0"))
    )

    caught = _tcl_error(shell, f"ram_synth -config {_q(config_path)}")

    assert caught == "ram_synth: num_words must be > 0"


# ---------------------------------------------------------------------------
# REPL and batch entry points (what `autombist shell` runs).
# ---------------------------------------------------------------------------


def _feed_input(monkeypatch, lines: list[str]) -> None:
    """input() returns ``lines`` in turn, then raises EOFError (Ctrl-D)."""
    remaining = iter(lines)

    def fake_input(prompt: str = "") -> str:
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)
    # readline is Unix-only; the REPL runs without it, so the tests do too.
    monkeypatch.setitem(sys.modules, "readline", None)


def test_repl_prints_results_and_errors_and_stops_at_exit(shell: TclShell, monkeypatch, capsys) -> None:
    _feed_input(monkeypatch, ["", "   ", "set x 5", "no_such_command", "set unset_result {}", "exit", "set x 9"])

    shell.run_repl()

    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["autombist Tcl shell. Type 'exit' or Ctrl-D to quit.", "5"]
    assert 'error: invalid command name "no_such_command"' in captured.err
    # The line after `exit` was never read.
    assert shell.eval("set x") == "5"


def test_repl_quit_also_stops_it(shell: TclShell, monkeypatch, capsys) -> None:
    _feed_input(monkeypatch, ["quit", "set x 1"])

    shell.run_repl()

    assert capsys.readouterr().out.splitlines() == ["autombist Tcl shell. Type 'exit' or Ctrl-D to quit."]


def test_repl_stops_at_end_of_input(shell: TclShell, monkeypatch, capsys) -> None:
    _feed_input(monkeypatch, ["set y 7"])

    shell.run_repl()

    assert capsys.readouterr().out.splitlines() == ["autombist Tcl shell. Type 'exit' or Ctrl-D to quit.", "7", ""]


def test_source_runs_a_script_file(shell: TclShell, tmp_path: Path) -> None:
    script = tmp_path / "session.tcl"
    script.write_text("set a 2\nset b [expr {$a * 21}]\n", encoding="utf-8")

    shell.source(script)

    assert shell.eval("set b") == "42"


def test_run_batch_returns_0_for_a_script_that_succeeds(shell: TclShell, tmp_path: Path) -> None:
    script = tmp_path / "session.tcl"
    script.write_text("set done 1\n", encoding="utf-8")

    assert shell.run_batch(script) == 0
    assert shell.eval("set done") == "1"


def test_run_batch_returns_1_and_reports_a_failing_script(shell: TclShell, tmp_path: Path, capsys) -> None:
    script = tmp_path / "session.tcl"
    script.write_text('error "coverage too low"\nset never 1\n', encoding="utf-8")

    assert shell.run_batch(script) == 1
    assert "error: coverage too low" in capsys.readouterr().err
    assert _tcl_error(shell, "set never") == "can't read \"never\": no such variable"


def test_run_batch_without_a_path_reads_stdin(shell: TclShell, monkeypatch) -> None:
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO("set piped 3\n"))

    assert shell.run_batch(None) == 0
    assert shell.eval("set piped") == "3"
