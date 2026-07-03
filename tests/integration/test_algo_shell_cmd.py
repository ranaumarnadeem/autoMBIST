from __future__ import annotations

import io
import shutil
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
