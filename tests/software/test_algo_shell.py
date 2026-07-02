from __future__ import annotations

import io
from pathlib import Path

from autombist.algo_engine import MemoryParams
from autombist.algo_shell import AlgoShell, Session


def _shell() -> AlgoShell:
    shell = AlgoShell(Session())
    shell.stdout = io.StringIO()
    return shell


def _output(shell: AlgoShell) -> str:
    return shell.stdout.getvalue()


def test_session_preloads_builtin_algos() -> None:
    session = Session()
    assert {"march_c", "mats_plus", "march_ss", "march_x"} <= set(session.algos)


def test_set_memory() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8 --init 0")
    assert shell.session.mem == MemoryParams(addr_width=8, data_width=8, init_val=0)
    assert "memory set" in _output(shell)


def test_set_memory_missing_args_reports_error() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8")
    assert shell.session.mem is None
    assert "error:" in _output(shell)


def test_add_algo_from_file(tmp_path: Path) -> None:
    algfile = tmp_path / "custom.alg"
    algfile.write_text("either w0\nup r0 w1\n", encoding="utf-8")
    shell = _shell()
    shell.onecmd(f"add_algo {algfile} --name custom")
    assert "custom" in shell.session.algos
    assert shell.session.algos["custom"].length_n == 3


def test_add_fault_short_and_long_forms() -> None:
    shell = _shell()
    shell.onecmd("add_fault SA0 10 3")
    shell.onecmd("add_fault CFIN 100 2 101 2 2 0")
    assert len(shell.session.faults) == 2
    assert shell.session.faults[0].type == "SA0" and shell.session.faults[0].aaddr == 0
    assert shell.session.faults[1].p0 == 2


def test_load_faults_replace_and_append(tmp_path: Path) -> None:
    faults_path = tmp_path / "f.txt"
    faults_path.write_text("SA0 1 0 0 0 0 0\nSA1 2 0 0 0 0 0\n", encoding="utf-8")
    shell = _shell()
    shell.onecmd(f"load_faults {faults_path}")
    assert len(shell.session.faults) == 2
    shell.onecmd(f"load_faults {faults_path} --append")
    assert len(shell.session.faults) == 4
    shell.onecmd(f"load_faults {faults_path}")  # no --append: replaces
    assert len(shell.session.faults) == 2


def test_gen_faults_all_types_needs_memory_first() -> None:
    shell = _shell()
    shell.onecmd("gen_faults")
    assert shell.session.faults == []
    assert "no memory configured" in _output(shell)


def test_gen_faults_all_types() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8")
    shell.onecmd("gen_faults --all-types")
    assert len(shell.session.faults) == 19  # one of each built-in primitive


def test_gen_faults_random_is_seed_reproducible() -> None:
    a, b = _shell(), _shell()
    for s in (a, b):
        s.onecmd("set_memory 8 8")
        s.onecmd("gen_faults --n 5 --seed 42")
    assert a.session.faults == b.session.faults
    assert len(a.session.faults) == 5


def test_run_requires_memory() -> None:
    shell = _shell()
    shell.onecmd("run march_c")
    assert "no memory configured" in _output(shell)


def test_run_unknown_algo_reports_error() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8")
    shell.onecmd("run does_not_exist")
    assert "unknown algorithm" in _output(shell)


def test_write_report_without_run_errors() -> None:
    shell = _shell()
    shell.onecmd("write_report /tmp/x.md")
    assert "nothing to report yet" in _output(shell)


def test_list_and_status_do_not_crash() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8")
    shell.onecmd("list")
    shell.onecmd("list algos")
    shell.onecmd("list types")
    shell.onecmd("status")
    out = _output(shell)
    assert "march_c" in out
    assert "SA0" in out  # from `list types`


def test_set_sim_rejects_icarus() -> None:
    shell = _shell()
    shell.onecmd("set_sim icarus")
    assert shell.session.sim == "verilator"
    assert "Icarus Verilog supports" in _output(shell)


def test_set_sim_accepts_verilator() -> None:
    shell = _shell()
    shell.onecmd("set_sim verilator")
    assert shell.session.sim == "verilator"


def test_unknown_command_does_not_crash_shell() -> None:
    shell = _shell()
    assert shell.onecmd("frobnicate --wat") is False
    assert "unknown command" in _output(shell)


def test_comment_and_blank_lines_are_noops() -> None:
    shell = _shell()
    assert shell.onecmd("# a comment") is False
    assert shell.onecmd("") is False
    assert _output(shell) == ""


def test_quit_and_eof_stop_the_loop() -> None:
    shell = _shell()
    assert shell.onecmd("quit") is True
    shell2 = _shell()
    assert shell2.onecmd("EOF") is True
