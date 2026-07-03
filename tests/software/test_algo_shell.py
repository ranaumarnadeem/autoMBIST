from __future__ import annotations

import io
from pathlib import Path

from autombist.algo_engine import MemoryParams
from autombist.algo_shell import AlgoShell, Session

REPO_ROOT = Path(__file__).resolve().parents[2]
MARCH_C_TOP = REPO_ROOT / "rtl" / "march_c" / "march_c_top.sv"


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


def test_set_memory_defaults_to_one_port() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8")
    assert shell.session.mem.num_ports == 1
    assert "ports=" not in _output(shell) or "ports=1" in _output(shell)


def test_set_memory_ports_flag_sets_two_port_memory() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8 --ports 2")
    assert shell.session.mem == MemoryParams(addr_width=8, data_width=8, num_ports=2)
    assert "ports=2" in _output(shell)


def test_set_memory_rejects_invalid_ports() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8 --ports 3")
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
    assert shell.session.faults[0].vport == 0 and shell.session.faults[0].aport == 0


def test_add_fault_with_explicit_cross_port() -> None:
    shell = _shell()
    shell.onecmd("add_fault CFIN 5 1 6 1 2 0 0 1")
    assert len(shell.session.faults) == 1
    fault = shell.session.faults[0]
    assert fault.vport == 0 and fault.aport == 1
    assert "ports=0/1" in _output(shell)


def test_add_fault_rejects_bad_arg_count() -> None:
    shell = _shell()
    shell.onecmd("add_fault SA0 10 3 0 0")  # 5 tokens: not 3, 7, or 9
    assert shell.session.faults == []
    assert "error:" in _output(shell)


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


def test_add_fsm_gathers_sibling_sources() -> None:
    shell = _shell()
    shell.onecmd(f"add_fsm {MARCH_C_TOP}")
    assert "march_c_top" in shell.session.fsms
    entry = shell.session.fsms["march_c_top"]
    assert entry.module_name == "march_c_top"
    names = {p.name for p in entry.sources}
    assert {"march_c_top.sv", "march_c_fsm.sv", "march_c_algo.sv"} <= names
    assert "registered" in _output(shell)


def test_add_fsm_custom_name() -> None:
    shell = _shell()
    shell.onecmd(f"add_fsm {MARCH_C_TOP} --name mine")
    assert "mine" in shell.session.fsms
    assert "march_c_top" not in shell.session.fsms


def test_add_fsm_rejects_missing_ports(tmp_path: Path) -> None:
    broken = tmp_path / "broken.sv"
    broken.write_text(
        "module broken(input logic clk, input logic rst_n, output logic bist_done);\nendmodule\n",
        encoding="utf-8",
    )
    shell = _shell()
    shell.onecmd(f"add_fsm {broken}")
    assert "broken" not in shell.session.fsms
    assert "missing the required MBIST-FSM port contract" in _output(shell)


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


def test_status_shows_ports_only_when_multi_port() -> None:
    single = _shell()
    single.onecmd("set_memory 8 8")
    single.stdout = io.StringIO()  # discard the `set_memory` confirmation line
    single.onecmd("status")
    assert "ports=" not in _output(single)

    multi = _shell()
    multi.onecmd("set_memory 8 8 --ports 2")
    multi.stdout = io.StringIO()
    multi.onecmd("status")
    assert "ports=2" in _output(multi)


def test_render_fault_ram_for_follows_session_num_ports(tmp_path: Path) -> None:
    # 1-port session (default): rendered fault_ram.sv has no per-port bus suffix.
    shell1 = _shell()
    shell1.onecmd("set_memory 8 8")
    workdir1 = tmp_path / "one_port"
    workdir1.mkdir()
    path1 = shell1._render_fault_ram_for(workdir1)
    text1 = path1.read_text(encoding="utf-8")
    assert "clk0" not in text1 and "clk1" not in text1

    # 2-port session: rendered fault_ram.sv exposes the dual port bus.
    shell2 = _shell()
    shell2.onecmd("set_memory 8 8 --ports 2")
    workdir2 = tmp_path / "two_port"
    workdir2.mkdir()
    path2 = shell2._render_fault_ram_for(workdir2)
    text2 = path2.read_text(encoding="utf-8")
    assert "clk0" in text2 and "clk1" in text2


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
