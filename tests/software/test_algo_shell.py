from __future__ import annotations

import io
from pathlib import Path

from autombist.algo_engine import CampaignResult, FaultRecord, FaultResult, MemoryParams
from autombist.algo_shell import AlgoShell, Session

REPO_ROOT = Path(__file__).resolve().parents[2]
MARCH_C_TOP = REPO_ROOT / "rtl" / "march_c" / "march_c_top.sv"
MARCH_2RW_TOP = REPO_ROOT / "rtl" / "march_2rw" / "march_2rw_top.sv"


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


def test_set_memory_defaults_words_per_row_to_1() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8")
    assert shell.session.mem.words_per_row == 1
    assert "words_per_row=1" in _output(shell)


def test_set_memory_words_per_row_flag() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8 --words-per-row 4")
    assert shell.session.mem == MemoryParams(addr_width=8, data_width=8, words_per_row=4)
    assert "words_per_row=4" in _output(shell)


def test_set_memory_rejects_words_per_row_below_1() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8 --words-per-row 0")
    assert shell.session.mem is None
    assert "error:" in _output(shell)


def test_set_memory_rejects_words_per_row_exceeding_depth() -> None:
    # Regression test: an adversarial review found this was silently accepted
    # (only "< 1" was checked), deferring the real error until `run` finally
    # reached compile_engine -- potentially after faults were already built
    # up around a nonsensical memory shape. addr_width=3 -> depth=8.
    shell = _shell()
    shell.onecmd("set_memory 3 8 --words-per-row 100")
    assert shell.session.mem is None
    assert "error:" in _output(shell)
    assert "exceeds depth" in _output(shell)


def test_set_memory_rejects_words_per_row_not_a_divisor_of_depth() -> None:
    # addr_width=3 -> depth=8; 3 does not evenly divide 8.
    shell = _shell()
    shell.onecmd("set_memory 3 8 --words-per-row 3")
    assert shell.session.mem is None
    assert "error:" in _output(shell)
    assert "does not evenly divide" in _output(shell)


def test_add_fault_hsd_warns_at_default_words_per_row() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8")
    shell.onecmd("add_fault HSD 10 3 0 0 1 0")
    assert len(shell.session.faults) == 1
    assert "WARNING" in _output(shell)
    assert "words_per_row" in _output(shell)


def test_add_fault_hsd_no_warning_when_words_per_row_over_1() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8 --words-per-row 4")
    shell.onecmd("add_fault HSD 10 3 0 0 1 0")
    assert len(shell.session.faults) == 1
    assert "WARNING" not in _output(shell)


def test_add_fault_hsd_warns_before_set_memory() -> None:
    # session.mem is None before any set_memory call -- the warning helper
    # must treat that as words_per_row=1 (the default), not crash.
    shell = _shell()
    shell.onecmd("add_fault HSD 10 3 0 0 1 0")
    assert len(shell.session.faults) == 1
    assert "WARNING" in _output(shell)


def test_add_fault_non_hsd_never_warns() -> None:
    shell = _shell()
    shell.onecmd("set_memory 8 8")
    shell.onecmd("add_fault SA0 10 3")
    assert "WARNING" not in _output(shell)


def test_load_faults_hsd_warns_at_default_words_per_row(tmp_path: Path) -> None:
    faults_path = tmp_path / "f.txt"
    faults_path.write_text("HSD 10 3 0 0 1 0\n", encoding="utf-8")
    shell = _shell()
    shell.onecmd("set_memory 8 8")
    shell.onecmd(f"load_faults {faults_path}")
    assert len(shell.session.faults) == 1
    assert "WARNING" in _output(shell)


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


def test_add_fsm_defaults_to_single_port_contract_with_no_memory_configured() -> None:
    # Regression: no set_memory call at all -> single-port validation, exactly
    # today's behavior. march_2rw_top.sv is 2-port-shaped, so it MUST be
    # rejected (missing nothing relative to single-port contract, but this
    # confirms the single-port path is what's actually exercised, not a
    # silent 2-port fallback).
    shell = _shell()
    shell.onecmd(f"add_fsm {MARCH_C_TOP}")
    assert "march_c_top" in shell.session.fsms


def test_add_fsm_validates_against_2port_contract_when_session_configured_for_2_ports() -> None:
    shell = _shell()
    shell.onecmd("set_memory 10 32 --ports 2")
    shell.onecmd(f"add_fsm {MARCH_2RW_TOP}")
    assert "march_2rw_top" in shell.session.fsms
    assert "registered" in _output(shell)


def test_add_fsm_2port_session_rejects_single_port_fsm() -> None:
    # march_c_top.sv is single-port-shaped (no sram_*1 bus); registering it
    # against a session configured for 2 ports must fail with the 2-port
    # contract's missing-pin diff, not silently succeed.
    shell = _shell()
    shell.onecmd("set_memory 10 32 --ports 2")
    shell.onecmd(f"add_fsm {MARCH_C_TOP}")
    assert "march_c_top" not in shell.session.fsms
    out = _output(shell)
    assert "missing the required MBIST-FSM port contract" in out
    assert "sram_clk1" in out


def test_add_fsm_1port_session_still_uses_single_port_contract() -> None:
    # Regression: an explicit --ports 1 session must behave identically to no
    # memory configured at all (today's default single-port validation).
    shell = _shell()
    shell.onecmd("set_memory 10 32 --ports 1")
    shell.onecmd(f"add_fsm {MARCH_C_TOP}")
    assert "march_c_top" in shell.session.fsms


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


def _fake_campaign_result(algo_name: str) -> CampaignResult:
    mem = MemoryParams(addr_width=8, data_width=8)
    faults = [
        FaultResult(
            index=0, record=FaultRecord(type="SA0", vaddr=1, vbit=0, aaddr=0, abit=0, p0=0, p1=0),
            detected=True, elem=0, op=0, addr=1, xor="00000001",
        ),
    ]
    return CampaignResult(
        algo_name=algo_name, mem=mem, golden_clean=True, faults=faults,
        detected=1, total=1, coverage_percent=100.0,
        build_seconds=1.0, run_seconds=0.5, sim="verilator",
    )


def test_write_diagnosis_without_run_errors() -> None:
    shell = _shell()
    shell.onecmd("write_diagnosis /tmp/x.md")
    assert "nothing to diagnose yet" in _output(shell)


def test_write_diagnosis_after_run_produces_file(tmp_path: Path) -> None:
    shell = _shell()
    result = _fake_campaign_result("march_c")
    shell.session.last_results["march_c"] = result
    shell.session.last_op = ("run", "march_c")
    out_path = tmp_path / "diag.md"
    shell.onecmd(f"write_diagnosis {out_path} --fmt md")
    assert out_path.exists()
    assert "march_c" in out_path.read_text()
    assert "diagnosis written" in _output(shell)


def test_write_diagnosis_after_compare_algo_raises_clear_error(tmp_path: Path) -> None:
    shell = _shell()
    a = _fake_campaign_result("march_c")
    b = _fake_campaign_result("march_ss")
    shell.session.last_results["march_c"] = a
    shell.session.last_results["march_ss"] = b
    shell.session.last_matrix = [a, b]
    shell.session.last_op = ("matrix", None)
    out_path = tmp_path / "diag.md"
    shell.onecmd(f"write_diagnosis {out_path}")
    assert not out_path.exists()
    out = _output(shell)
    assert "error:" in out
    assert "diagnosis only applies to a single 'run' result" in out


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
