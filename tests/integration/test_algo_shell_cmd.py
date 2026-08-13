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
REFERENCE_COVERAGE = {"march_c": (20, 29), "mats_plus": (13, 29), "march_ss": (28, 29)}


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
    assert "20/29 detected" in out
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
    confirm the ORIGINAL failure mode reproduces, proving the fix (not
    something else -- a different Verilator version, a stale build) is what
    makes the positive case pass. Also pins a second, related fix: the
    golden-run check used to pipe RUN's output straight through
    `grep '^RESULT'` before capturing it, so a crash with no RESULT line at
    all -- like this one -- left the caller with "golden run FAILED: " and no
    reason. It now captures the raw output first, so the simulator's own
    "FATAL: unknown +ALG=march_x" is what a caller actually sees."""
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
    assert "coverage: 14 / 29 detected" in result.stdout, combined

    algc.unlink()
    broken = run_campaign()
    assert broken.returncode == 1, broken.stdout + broken.stderr
    assert "golden run FAILED: FATAL: unknown +ALG=march_x" in broken.stdout, broken.stdout + broken.stderr


def test_run_campaign_sh_still_uses_the_builtin_table_with_no_algc_present(tmp_path: Path) -> None:
    """The fix's fallback branch: run_campaign.sh's ORIGINAL, still-documented
    use is running the engine's 3 fixed built-in tables directly (no exported
    bundle, no .algc files around at all) -- e.g. straight from
    src/autombist/engine. That path must be untouched by the .algc-preferring
    fix above. Uses MARCHCM, whose built-in table is march_c's -- and, since
    the either-direction fix landed earlier this session, is now provably
    identical to march_c.algc's content, so 20/29 is the correct expectation
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
    assert "coverage: 20 / 29 detected" in result.stdout, combined


# A fault-list line whose type isn't in the registry makes fault_ram.sv's
# whole-file parse FATAL (see "unknown fault type" in fault_ram.sv) -- and it
# does so for EVERY +FAULT_INDEX invocation, since the entire file is
# re-parsed from scratch before the index is even applied. That gives a
# deterministic, no-RESULT-line crash for a per-fault run, which is exactly
# the scenario the golden-run fix's sibling bug needed: the loop that used to
# write these to the CSV as ESCAPED, indistinguishable from a genuine
# non-detection.
_ONE_BAD_FAULT_TYPE = "SA0 0 0 0 0 0 0\nNOT_A_REAL_TYPE 0 0 0 0 0 0\n"


def _bundle_with_builtin_algo(tmp_path: Path, name: str) -> Path:
    faults = find_engine_dir() / "faults.example.txt"
    bundle_dir = tmp_path / name
    _run_script([
        "set_memory 8 8",
        f"load_faults {faults}",
        f"export_tb {bundle_dir}",
    ])
    (bundle_dir / "faults.txt").write_text(_ONE_BAD_FAULT_TYPE, encoding="utf-8")
    return bundle_dir


def test_run_campaign_sh_marks_a_crashed_fault_run_as_error_not_escaped(tmp_path: Path) -> None:
    """The per-fault loop's own version of the golden-run bug: it already had
    the FULL raw output in $OUT (unlike the golden-run pipe-straight-into-grep
    case), but never used it -- a crashed run (no RESULT line) fell through
    to the same `else` branch as a genuine non-detection and was written to
    the CSV as ESCAPED, silently. That understates coverage with an
    infrastructure failure with no visible sign anywhere in the output.

    The control against over-classification -- that ERROR is specific to
    crashes and not applied blanket -- is the sibling test below, which runs
    the same script over a VALID fault list and requires DETECTED/ESCAPED with
    no warnings at all.

    (An earlier version of this test fetched the pre-fix script via
    `git show HEAD:...` to demonstrate the old ESCAPED behaviour. That was
    self-invalidating: once the fix was committed, HEAD became the fixed
    script and the control compared it against itself. A negative control must
    not be anchored to a moving reference.)"""
    bundle_dir = _bundle_with_builtin_algo(tmp_path, "bundle")

    result = subprocess.run(
        ["bash", "run_campaign.sh", "faults.txt", "march_c"],
        cwd=bundle_dir, capture_output=True, text=True, check=False, timeout=300,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined  # golden run doesn't touch faults.txt at all
    assert "golden: clean" in result.stdout, combined

    csv_rows = (bundle_dir / "campaign_march_c.csv").read_text(encoding="utf-8").splitlines()
    # Every +FAULT_INDEX=N run re-parses the SAME 2-line fault list from
    # scratch, so the type-check FATAL on the bad line 2 fires on every run
    # regardless of which index it's seeking. fault_ram.sv's had_fatal guard
    # (the fatal-cascade fix) now skips the whole FAULT_INDEX/FAULT_LOADED
    # section once that FATAL fires, so neither run ever prints a
    # FAULT_LOADED line -- both rows have an empty type, not just index 1's.
    # Confirmed empirically, not assumed.
    assert csv_rows[1:] == ["0,,ERROR,,,", "1,,ERROR,,,"], csv_rows
    assert result.stderr.count("produced no RESULT line") == 2, combined
    assert "NOT_A_REAL_TYPE" in result.stderr, combined
    assert "coverage: 0 / 2 detected (2 run(s) errored" in result.stdout, combined


def test_run_campaign_sh_still_reports_escaped_for_a_genuine_non_detection(tmp_path: Path) -> None:
    """The control against the opposite failure: ERROR must be specific to a
    crashed run, not applied to anything that isn't DETECTED. Runs the same
    script over a VALID two-fault list -- SA0, which March C- catches, and SOF,
    which it genuinely misses (see the coverage table in engine/README.md) --
    and requires the classic DETECTED/ESCAPED split with no error path taken.

    Without this, the crash test above would still pass if the script simply
    labelled every non-detection ERROR."""
    bundle_dir = _bundle_with_builtin_algo(tmp_path, "bundle_valid")
    (bundle_dir / "faults.txt").write_text(
        "SA0 10 3 0 0 0 0\nSOF 70 5 0 0 0 0\n", encoding="utf-8"
    )

    result = subprocess.run(
        ["bash", "run_campaign.sh", "faults.txt", "march_c"],
        cwd=bundle_dir, capture_output=True, text=True, check=False, timeout=300,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "golden: clean" in result.stdout, combined

    csv_rows = (bundle_dir / "campaign_march_c.csv").read_text(encoding="utf-8").splitlines()
    assert csv_rows[1].startswith("0,SA0,DETECTED,"), csv_rows
    assert csv_rows[2] == "1,SOF,ESCAPED,,,", csv_rows
    # No ERROR row, no warning, and the summary keeps its original wording --
    # the errored-run suffix must appear only when a run actually errored.
    assert "ERROR" not in "\n".join(csv_rows), csv_rows
    assert "produced no RESULT line" not in combined, combined
    assert "coverage: 1 / 2 detected  ->" in result.stdout, combined


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


def test_run_campaign_sh_aborts_rather_than_reusing_a_stale_binary(tmp_path: Path) -> None:
    """run_campaign.sh built with verilator but never checked the exit status,
    and there is no `set -e` (the per-fault loop deliberately tolerates
    non-zero runs so it can record ERROR rows). obj_dir/ is never cleaned, so a
    failed rebuild fell straight through to RUN() executing the march_engine_sim
    left behind by the PREVIOUS build.

    Measured on the pre-fix script, against RTL that cannot compile: exit code
    0, "golden: clean", and "coverage: 20 / 29 detected" -- a plausible,
    confident, entirely wrong answer produced from stale binary, with nothing
    in the output suggesting the RTL never built. For a tool whose whole output
    is a coverage number, that is the worst available failure mode.

    algo_engine.py's in-process driver already raised CampaignError on a
    non-zero verilator exit; this standalone path -- the one an exported bundle
    runs WITHOUT autoMBIST installed -- did not.

    The test is self-contained: it builds once to establish the stale binary,
    then breaks the RTL. It deliberately does NOT fetch the old script via
    `git show HEAD:...` -- see the note on the crash test above about negative
    controls anchored to a moving reference.
    """
    bundle_dir = _bundle_with_builtin_algo(tmp_path, "bundle_stale")
    (bundle_dir / "faults.txt").write_text("SA0 10 3 0 0 0 0\n", encoding="utf-8")

    first = subprocess.run(
        ["bash", "run_campaign.sh", "faults.txt", "march_c"],
        cwd=bundle_dir, capture_output=True, text=True, check=False, timeout=300,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    sim = bundle_dir / "obj_dir" / "march_engine_sim"
    # The premise: a runnable binary is now present. Without this the test
    # would still pass on a script that merely failed for some other reason.
    assert sim.exists(), "expected a built simulator to exist before the break"

    fr = bundle_dir / "fault_ram.sv"
    fr.write_text(fr.read_text(encoding="utf-8") + "\nnot valid systemverilog\n",
                  encoding="utf-8")

    second = subprocess.run(
        ["bash", "run_campaign.sh", "faults.txt", "march_c"],
        cwd=bundle_dir, capture_output=True, text=True, check=False, timeout=300,
    )
    combined = second.stdout + second.stderr
    assert second.returncode == 1, combined
    assert "verilator build FAILED" in second.stderr, combined
    # The whole point: no coverage number may be produced from the stale binary.
    assert "coverage:" not in second.stdout, combined
    assert "golden: clean" not in second.stdout, combined
    # And the stale binary really was still sitting there to be used.
    assert sim.exists(), "stale binary vanished -- the test no longer proves anything"
