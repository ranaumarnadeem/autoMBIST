from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import find_engine_dir  # noqa: E402
from autombist.algo_shell import AlgoShell, Session  # noqa: E402

# Reference coverage from src/autombist/engine/README.md "Measured results" table.
REFERENCE_COVERAGE = {"march_c": (14, 19), "mats_plus": (12, 19), "march_ss": (18, 19)}


def _run_script(lines: list[str]) -> tuple[AlgoShell, str]:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    for line in lines:
        if shell.onecmd(shell.precmd(line)):
            break
    return shell, shell.stdout.getvalue()


def test_run_matches_reference_table() -> None:
    faults = find_engine_dir() / "faults.example.txt"
    shell, out = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        "run march_c",
    ])
    assert "14/19 detected" in out
    result = shell.session.last_results["march_c"]
    assert (result.detected, result.total) == REFERENCE_COVERAGE["march_c"]
    assert shell.session.last_op == ("run", "march_c")


def test_compare_algo_matrix_matches_reference_table() -> None:
    faults = find_engine_dir() / "faults.example.txt"
    shell, out = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        "compare_algo march_c -march mats_plus,march_ss",
    ])
    for name, (detected, total) in REFERENCE_COVERAGE.items():
        result = shell.session.last_results[name]
        assert (result.detected, result.total) == (detected, total), name
    assert "| fault" in out  # the markdown matrix was printed
    assert shell.session.last_matrix is not None
    assert [r.algo_name for r in shell.session.last_matrix] == ["march_c", "mats_plus", "march_ss"]


def test_write_report_after_compare(tmp_path: Path) -> None:
    faults = find_engine_dir() / "faults.example.txt"
    report_path = tmp_path / "matrix.csv"
    shell, _ = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        "compare_algo march_c -march march_ss",
        f"write_report {report_path} --fmt csv",
    ])
    assert report_path.exists()
    text = report_path.read_text()
    assert "march_c,march_ss" in text


def test_write_diagnosis_after_run_produces_file(tmp_path: Path) -> None:
    faults = find_engine_dir() / "faults.example.txt"
    diag_path = tmp_path / "diag.json"
    shell, out = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        "run march_c",
        f"write_diagnosis {diag_path} --fmt json",
    ])
    assert "error:" not in out
    assert diag_path.exists()
    assert "diagnosis written" in out
    payload = json.loads(diag_path.read_text())
    assert payload["algo_name"] == "march_c"
    assert len(payload["cells"]) > 0


def test_write_diagnosis_after_compare_algo_raises_clear_error(tmp_path: Path) -> None:
    faults = find_engine_dir() / "faults.example.txt"
    diag_path = tmp_path / "diag.md"
    shell, out = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        "compare_algo march_c -march march_ss",
        f"write_diagnosis {diag_path}",
    ])
    assert not diag_path.exists()
    assert "error:" in out
    assert "diagnosis only applies to a single 'run' result" in out


def test_export_tb_produces_runnable_bundle(tmp_path: Path) -> None:
    faults = find_engine_dir() / "faults.example.txt"
    bundle_dir = tmp_path / "bundle"
    shell, _ = _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        f"export_tb {bundle_dir}",
    ])
    for name in ("fault_ram.sv", "march_engine.sv", "openram_shim.sv", "run_campaign.sh", "faults.txt"):
        assert (bundle_dir / name).exists(), name


def test_run_campaign_sh_runs_a_non_builtin_algo_from_its_own_bundle(tmp_path: Path) -> None:
    """The bug: run_campaign.sh only ever passed +ALG=<name> to the engine,
    which understands exactly 3 hardcoded table names (MATSP/MARCHCM/MARCHSS).
    Every OTHER algorithm -- march_x, march_b, or a custom .alg -- has no
    built-in table at all, even though export_tb writes it a same-named
    <name>.algc file specifically so it CAN be run. Running the bundle's own
    march_x.algc via ./run_campaign.sh faults.txt march_x used to abort with
    "FATAL: unknown +ALG=march_x" -- the algorithm the bundle exists to
    demonstrate could never actually be run from it.

    Verified negative control: delete the .algc file the fix depends on and
    confirm the ORIGINAL failure reproduces exactly, proving the fix (not
    something else -- a different Verilator version, a stale build) is what
    makes the positive case pass. Note the simulator's own "FATAL: unknown
    +ALG=march_x" text never reaches this level: run_campaign.sh's golden-run
    check pipes RUN's output through `grep '^RESULT'` before capturing it, so
    that line -- and any reason at all -- is silently discarded; a caller
    only ever sees "golden run FAILED: " with nothing after the colon. That
    swallowed diagnostic is a pre-existing wart in the script's own error
    reporting, independent of this bug; left alone here rather than expanding
    scope beyond the .algc fix."""
    faults = find_engine_dir() / "faults.example.txt"
    bundle_dir = tmp_path / "bundle"
    _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        f"export_tb {bundle_dir}",
    ])
    algc = bundle_dir / "march_x.algc"
    assert algc.exists(), "export_tb should have written march_x.algc"

    def run_campaign() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "run_campaign.sh", "faults.txt", "march_x"],
            cwd=bundle_dir, capture_output=True, text=True, check=False, timeout=300,
        )

    result = run_campaign()
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "golden: clean" in result.stdout, combined
    # March X's measured coverage against faults.example.txt at 8x8 -- see
    # docs/source/algo-shell-guide.md's "How `either` gets resolved".
    assert "coverage: 13 / 19 detected" in result.stdout, combined

    algc.unlink()
    broken = run_campaign()
    assert broken.returncode == 1, broken.stdout + broken.stderr
    assert "golden run FAILED" in broken.stdout, broken.stdout + broken.stderr


def test_run_campaign_sh_still_uses_the_builtin_table_with_no_algc_present(tmp_path: Path) -> None:
    """The fix's fallback branch: run_campaign.sh's ORIGINAL, still-documented
    use is running the engine's 3 fixed built-in tables directly (no exported
    bundle, no .algc files around at all) -- e.g. straight from
    src/autombist/engine. That path must be untouched by the .algc-preferring
    fix above. Uses MARCHCM, whose built-in table is march_c's -- and, since
    the either-direction fix landed earlier this session, is now provably
    identical to march_c.algc's content, so 14/19 is the correct expectation
    either way this ever ran."""
    faults = find_engine_dir() / "faults.example.txt"
    bundle_dir = tmp_path / "bundle"
    _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        f"export_tb {bundle_dir}",
    ])
    for algc in bundle_dir.glob("*.algc"):
        algc.unlink()
    assert not list(bundle_dir.glob("*.algc")), "must simulate a directory with no .algc files at all"

    result = subprocess.run(
        ["bash", "run_campaign.sh", "faults.txt", "MARCHCM"],
        cwd=bundle_dir, capture_output=True, text=True, check=False, timeout=300,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "golden: clean" in result.stdout, combined
    assert "coverage: 14 / 19 detected" in result.stdout, combined


# --------------------------------------------------------------------------- #
# Multi-port (Phase 6): the shell's own command surface must be able to set up
# a 2-port memory, define a genuine cross-port coupling fault, and run a real
# campaign against march_engine_mp.sv end to end -- mirroring the scenario in
# tests/integration/test_march_engine_mp_cross_port_coupling.py (same fault
# site/type, same algorithm shape), but driven purely through
# do_set_memory/do_add_algo/do_add_fault/do_run instead of calling the
# algo_engine/fault_ram_gen APIs directly.
# --------------------------------------------------------------------------- #
def _write_cross_port_algo(tmp_path: Path) -> Path:
    """AW=4, DW=4: init every word to 0, then write 1 ascending on PORT 1
    (address 5 before 6), then read back on port 0. Victim=(5,1),
    aggressor=(6,1) -- identical site to test_march_engine_mp_cross_port_coupling.py."""
    alg_path = tmp_path / "cross_port.alg"
    alg_path.write_text("either w0\nup w1.1\nup r1\n", encoding="utf-8")
    return alg_path


def test_shell_sets_up_two_port_memory_via_set_memory() -> None:
    shell, out = _run_script(["set_memory 4 4 --ports 2 --init 0"])
    assert shell.session.mem is not None
    assert shell.session.mem.num_ports == 2
    assert "ports=2" in out


def test_shell_cross_port_coupling_fault_is_detected(tmp_path: Path) -> None:
    """End-to-end: do_set_memory (2-port) -> do_add_algo (custom cross-port
    algorithm) -> do_add_fault (explicit vport/aport, cross-port) -> do_run.
    The aggressor write happens on port 1 while the fault record claims
    aport=1 and the victim is sensed via a port-0 read -- must DETECT (see
    test_march_engine_mp_cross_port_coupling.py's
    test_cross_port_coupling_is_detected for the same proof against the raw API)."""
    alg_path = _write_cross_port_algo(tmp_path)
    shell, out = _run_script([
        "set_memory 4 4 --ports 2 --init 0",
        f"add_algo {alg_path} --name xport",
        "add_fault CFIN 5 1 6 1 2 0 0 1",
        "run xport",
    ])
    assert "error:" not in out
    result = shell.session.last_results["xport"]
    assert result.total == 1
    assert result.detected == 1, f"cross-port CFIN fault was not detected:\n{out}"
    fault = shell.session.faults[0]
    assert (fault.vport, fault.aport) == (0, 1)


def test_shell_cross_port_fault_escapes_when_algorithm_never_uses_that_port(tmp_path: Path) -> None:
    """CONTROL, mirroring test_march_engine_mp_cross_port_coupling.py's
    test_cross_port_fault_escapes_when_algorithm_never_uses_that_port: same
    fault record (aport=1) but the algorithm's aggressor write element issues
    'w1' on port 0 only -- port 1 is never touched, so the fault must ESCAPE.
    Proves the shell's vport/aport plumbing is load-bearing, not decorative."""
    alg_path = tmp_path / "control.alg"
    alg_path.write_text("either w0\nup w1\nup r1\n", encoding="utf-8")  # aggressor write on port 0 only
    shell, out = _run_script([
        "set_memory 4 4 --ports 2 --init 0",
        f"add_algo {alg_path} --name control",
        "add_fault CFIN 5 1 6 1 2 0 0 1",  # fault list still claims aport=1
        "run control",
    ])
    assert "error:" not in out
    result = shell.session.last_results["control"]
    assert result.total == 1
    assert result.detected == 0, f"control should ESCAPE (algorithm never issues on port 1):\n{out}"
