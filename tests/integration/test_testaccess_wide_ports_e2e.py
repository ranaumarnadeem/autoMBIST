"""Real-Icarus proof that every control/status port this fixture's config can produce
actually works through a real, clocked JTAG scan path -- not just the structural (real
Yosys ingest + real SIB insertion, but no clocking) proof in
test_testaccess_warptap_e2e.py. Covers both the wide (multi-bit) ports
(fuse_faulty_row_addr/fuse_row_repair_en WRITE, diag_valid/diag_addr READ) and the
always-1-bit ones that were previously only structurally verified anywhere in this
project (test_mode/bist_start/bist_done/bist_fail, self_repair_done/self_repair_fail,
diag_overflow) -- self_repair_busy and the always-1-bit self_repair_start are the only
ones with a prior clocked-simulation proof (test_testaccess_warptap_e2e.py's
test_self_repair_start_write_and_busy_read_through_real_jtag), and even that only
proves self-repair STARTED, not that it ran to completion.

Deliberately uses a NEW, small, purpose-built single-wrapper fixture
(tb_sram_1rw_mbist.v) rather than the shared flow/multimem mem_subsystem_mbist.sv
fixture test_self_repair_start_write_and_busy_read_through_real_jtag reuses: that
hand-written 3-macro integration file has no persistence/diagnosis ports of its own
today, and adding them there would be an avoidable-risk edit to a fixture shared with
tests/hardware/test_mem_subsystem_mbist.py.

The tester-driven repair_ports: passthrough ports (row_repair_en/faulty_row_addr/
col_repair_en/faulty_bit) are NOT covered here -- they need a genuinely different
fixture (no onchip_selfrepair) and a testbench that also drives the functional bus, see
test_testaccess_repair_ports_e2e.py.

Same skip conditions as test_testaccess_warptap_e2e.py (warptap, iverilog, yosys).
"""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from autombist.generator import generate_from_config
from autombist.testaccess import wrap_test_access

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("warptap") is None
    or shutil.which("iverilog") is None
    or shutil.which("yosys") is None,
    reason="needs `pip install warptap` plus iverilog and yosys on PATH "
    "(Linux/WSL only) -- warptap shells out to both, no bundled fallback",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTBENCH = REPO_ROOT / "tests" / "hardware" / "tb_sram_1rw_mbist.v"
# (tms, tdi, trst_n, rst_n) -- matches _RESET_LEAD_IN in test_testaccess_warptap_e2e.py,
# minus the functional-bus columns tb_sram_1rw_mbist.v's testbench doesn't need (the
# functional bus is tied permanently idle in the testbench itself, not stimulus-driven).
_RESET_LEAD_IN = [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 1, 1)]

# Matches test_control_ports_are_jtag_exclusive_status_ports_are_not's own fixture
# (test_testaccess_warptap_e2e.py) exactly -- num_spare_rows=2/addr_width=6/
# num_diagnosis_entries=4, so fuse_faulty_row_addr is 12 bits and diag_addr is 24.
_CONFIG = {
    "memory_name": "sram_1rw", "wrapper_module_name": "sram_1rw_mbist",
    "addr_width": 6, "data_width": 8, "we_active_low": True,
    # web0, not we0 -- matches the real memory model's own port name (sram_model_spares.sv,
    # since num_spare_rows > 0), same convention _BASE_PORTS above already proves out in
    # real simulation; a structural-only test can get away with a mismatch here (never
    # elaborates against the real model), a real Icarus one cannot.
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
    "redundancy": {
        "num_spare_rows": 2, "num_spare_cols": 0,
        "onchip_selfrepair": True, "onchip_repair_persistence": True,
        "onchip_diagnosis": True, "num_diagnosis_entries": 4,
    },
}


def _generate_sources(tmp_path: Path) -> list[Path]:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(_CONFIG, sort_keys=False), encoding="utf-8")
    wrapper_path = generate_from_config(config_path, tmp_path / "gen", algo="march-c")
    shared = wrapper_path.parent

    # The wrapper instantiates its memory by `memory_name` (`sram_1rw #(...)`,
    # OpenRAM's own convention -- the real macro's module name matches the memory
    # name a real user picks) WITH a NUM_SPARE_ROWS defparam (num_spare_rows > 0
    # here) -- so it needs the spare-augmented model (sram_model_spares.sv, which
    # actually declares that parameter), not the plain sram_model.sv (confirmed by
    # a real Yosys elaboration error the other way: "Can't find object for defparam
    # `NUM_SPARE_ROWS`"). Its module is `sram_model_spares`, not `sram_1rw` -- the
    # generated Makefile flow (run_simulation) renames it as part of the build; a
    # plain iverilog elaboration like this test's needs a real module named
    # `sram_1rw` in its own source list, so rename a copy here rather than relying
    # on Makefile internals this test doesn't otherwise use.
    sram_model_src = (shared / "sram_model_spares.sv").read_text(encoding="utf-8")
    renamed_model = tmp_path / "sram_1rw.sv"
    renamed_model.write_text(
        sram_model_src.replace("module sram_model_spares ", "module sram_1rw ", 1), encoding="utf-8",
    )
    assert renamed_model.read_text(encoding="utf-8") != sram_model_src, (
        "expected 'module sram_model_spares ' to be found and renamed -- "
        "sram_model_spares.sv's own module declaration style may have changed"
    )

    sources = [
        wrapper_path,
        shared / "march_c" / "march_c_algo.sv",
        shared / "march_c" / "march_c_fsm.sv",
        shared / "march_c" / "march_c_top.sv",
        shared / "onchip_row_repair_analyzer.sv",
        shared / "onchip_selfrepair_ctrl.sv",
        shared / "onchip_diagnosis_log.sv",
        shared / "repair_remap_row.sv",
        renamed_model,
    ]
    for src in sources:
        assert src.is_file(), f"expected generated openMBIST source missing: {src}"
    return sources


def _run_pdl_program(pdl_program: list, tmp_path: Path, inserted_verilog: str) -> list[int]:
    """Wrap pdl_program in raw TAP IR ops, run it against tb_sram_1rw_mbist.v under real
    Icarus, and return the reset-lead-in-stripped tdo bitstream -- the exact shape
    test_self_repair_start_write_and_busy_read_through_real_jtag's own pattern produces,
    just against this file's own simpler (tms/tdi/trst_n/rst_n-only) stimulus/TRACE
    format instead of tb_mem_subsystem_mbist.v's 9-column one."""
    from warptap.sim_io import run_verilog_testbench
    from warptap.tap_fsm import TapState
    from warptap.tap_ir import GotoState, ShiftIR, bits_to_int
    from warptap.tap_ir_play import to_cycles

    ir_ops = [
        GotoState(TapState.SHIFT_IR),
        ShiftIR(4, tdi=bits_to_int([0, 0, 0, 0])),  # OPCODE_EXTEST = 0, IR_WIDTH = 4
        GotoState(TapState.RUN_TEST_IDLE),
    ] + pdl_program

    cycles = to_cycles(ir_ops)
    rows = _RESET_LEAD_IN + [(tms, tdi, 1, 1) for tms, tdi in cycles]
    stimulus = "\n".join(" ".join(str(v) for v in row) for row in rows) + "\n"

    with tempfile.TemporaryDirectory(prefix="test-testaccess-wide-") as tmpdir:
        v_path = Path(tmpdir) / "sram_1rw_mbist_sib_inserted.v"
        v_path.write_text(inserted_verilog, encoding="utf-8")
        stdout = run_verilog_testbench(
            [v_path, TESTBENCH], extra_inputs={"stimulus.txt": stimulus},
        )

    tdo_by_cycle = [
        int(line.split(",")[2]) for line in stdout.splitlines() if line.startswith("TRACE,")
    ]
    return ir_ops, tdo_by_cycle[len(_RESET_LEAD_IN):]


def test_wide_write_reaches_real_rtl_through_real_jtag(tmp_path: Path) -> None:
    """WRITE case: fuse_faulty_row_addr (12 bits) and fuse_row_repair_en (2 bits),
    loaded via repair_load, confirmed via repair_load_done -- the same load-done
    handshake pattern test_self_repair_start_write_and_busy_read_through_real_jtag
    already proves for a 1-bit port, now driving real 12-bit and 2-bit values through
    the same real Icarus simulation.

    Honest scope: this proves the wide WRITE reaches real RTL and the receiving FSM
    accepts it and completes its load handshake -- it does NOT independently prove
    bit-exact placement inside onchip_row_repair_analyzer (repair_load_done sets
    unconditionally on any repair_load pulse, regardless of the address's actual bit
    values). Bit-exactness instead rests on (a) this exercising the identical RTL
    insertion code path warptap's own test suite already proves bit-exact at width 16
    with a real signal (tests/test_uart_tx_cross_sim.py, no analogous 12-bit real-RTL
    proof exists in this project, so this is still meaningfully strengthening evidence,
    not redundant with it), and (b) the direct signal_bits bit-order assertion in
    test_testaccess_warptap_e2e.py's test_control_ports_are_jtag_exclusive_status_ports_are_not.
    """
    from warptap.pdl_interpreter import PDLInterpreter
    from warptap.pdl_verify import check_reads, correlate_observed

    sources = _generate_sources(tmp_path)
    inserted_verilog, graph, root = wrap_test_access(
        sources, "sram_1rw_mbist",
        onchip_selfrepair=True, onchip_repair_persistence=True, onchip_diagnosis=True,
        num_spare_rows=2, num_diagnosis_entries=4, addr_width=6,
    )
    assert len(graph.chain) == 15

    test_pattern = 0b101010101010  # 12 bits, alternating -- not all-0/all-1
    pdl = PDLInterpreter(graph, root)
    pdl.iTarget("fuse_faulty_row_addr")
    pdl.iWrite(test_pattern)
    pdl.iApply()
    pdl.iTarget("fuse_row_repair_en")
    pdl.iWrite(0b11)
    pdl.iApply()
    pdl.iTarget("repair_load")
    pdl.iWrite(1)
    pdl.iApply()
    pdl.iRunLoop(10)  # generous margin for the load to complete
    pdl.iTarget("repair_load_done")
    pdl.iRead(1)
    pdl.iApply()
    pdl.iTarget("repair_load")
    pdl.iWrite(0)
    pdl.iApply()

    ir_ops, tdo_by_cycle = _run_pdl_program(pdl.program, tmp_path, inserted_verilog)
    observed = correlate_observed(ir_ops, tdo_by_cycle)
    results = check_reads(ir_ops, observed)

    assert len(results) == 1  # exactly the one iRead(repair_load_done) queued above
    assert results[0].passed, (
        f"repair_load_done expected 1 after writing a real 12-bit fuse_faulty_row_addr "
        f"and 2-bit fuse_row_repair_en and pulsing repair_load, observed through the "
        f"real JTAG scan path -- got {results[0]}"
    )


def test_wide_read_survives_real_jtag_fault_free(tmp_path: Path) -> None:
    """READ case: diag_valid (4 bits) and diag_addr (24 bits) read back through a real
    clocked scan chain. Honest scope: generate_from_config unconditionally rejects
    redundancy: together with use_saboteur=True (the only fault-injection mechanism this
    generator has), so there is no way to force a real, known, non-zero diagnosis entry
    through this project's own generator -- on this fault-free fixture, a self-repair
    analyze pass with no injected defect finds nothing, so both ports legitimately read
    back all-zero. This still proves something real and new: that the new multi-cell
    bc1_shift_only READ chain for TWO wide ports back-to-back, sharing the same scan
    chain as 13 other instruments, doesn't corrupt or misalign neighboring reads and
    that width/direction survive actual clocking (not just structural netlist
    inspection) -- it does not independently prove a non-zero value reads back
    bit-exactly at every position. That gap is instead covered by the direct
    signal_bits bit-order assertion in test_testaccess_warptap_e2e.py's
    test_control_ports_are_jtag_exclusive_status_ports_are_not.
    """
    from warptap.pdl_interpreter import PDLInterpreter
    from warptap.pdl_verify import check_reads, correlate_observed

    sources = _generate_sources(tmp_path)
    inserted_verilog, graph, root = wrap_test_access(
        sources, "sram_1rw_mbist",
        onchip_selfrepair=True, onchip_repair_persistence=True, onchip_diagnosis=True,
        num_spare_rows=2, num_diagnosis_entries=4, addr_width=6,
    )
    assert len(graph.chain) == 15

    pdl = PDLInterpreter(graph, root)
    pdl.iTarget("diag_valid")
    pdl.iRead(0)
    pdl.iApply()

    ir_ops, tdo_by_cycle = _run_pdl_program(pdl.program, tmp_path, inserted_verilog)
    observed = correlate_observed(ir_ops, tdo_by_cycle)
    results = check_reads(ir_ops, observed)

    # NOTE: .observed is the RAW value shifted through this target's whole phase-2
    # ShiftDR (which also carries OTHER SIBs' navigation bits on the same physical
    # shift, per pdl_interpreter.py's own "closing whatever's open and opening the new
    # target" docstring) -- confirmed directly against pdl_verify.py's own source, not
    # assumed. `.passed` already does the correct masked comparison
    # ((observed & mask) == (expected & mask)) against what iRead(0) declared, so that
    # -- not a raw `.observed == 0` check -- is the right (and sufficient) assertion.
    assert len(results) == 1
    assert results[0].passed, (
        f"diag_valid expected all-zero on a fault-free run, observed through the real "
        f"JTAG scan path -- got {results[0]}"
    )


def test_wide_read_addr_survives_real_jtag_fault_free(tmp_path: Path) -> None:
    """diag_addr's own half of test_wide_read_survives_real_jtag_fault_free -- kept as
    a separate program/simulation run rather than chained after diag_valid in one
    program, matching every other real-JTAG test in this project (always exactly one
    iRead per program)."""
    from warptap.pdl_interpreter import PDLInterpreter
    from warptap.pdl_verify import check_reads, correlate_observed

    sources = _generate_sources(tmp_path)
    inserted_verilog, graph, root = wrap_test_access(
        sources, "sram_1rw_mbist",
        onchip_selfrepair=True, onchip_repair_persistence=True, onchip_diagnosis=True,
        num_spare_rows=2, num_diagnosis_entries=4, addr_width=6,
    )
    assert len(graph.chain) == 15

    pdl = PDLInterpreter(graph, root)
    pdl.iTarget("diag_addr")
    pdl.iRead(0)
    pdl.iApply()

    ir_ops, tdo_by_cycle = _run_pdl_program(pdl.program, tmp_path, inserted_verilog)
    observed = correlate_observed(ir_ops, tdo_by_cycle)
    results = check_reads(ir_ops, observed)

    # See test_wide_read_survives_real_jtag_fault_free's own comment on why .passed
    # (not a raw `.observed == 0` check) is the right assertion here.
    assert len(results) == 1
    assert results[0].passed, (
        f"diag_addr expected all-zero on a fault-free run, observed through the real "
        f"JTAG scan path -- got {results[0]}"
    )


def test_base_bist_completes_through_real_jtag(tmp_path: Path) -> None:
    """The base march-C BIST quartet (test_mode/bist_start control, bist_done/bist_fail
    status) -- always present on every wrapper, previously only structurally verified
    (real Yosys ingest + SIB insertion, no clocking). wrapper_template.j2's
    `effective_bist_start = (bist_start && test_mode && !self_repair_busy) ||
    ctrl_bist_start` collapses to `bist_start && test_mode` since self-repair is never
    triggered here -- both must be WRITTEN AND HELD (an instrument_write shadow
    register only changes on Update-DR), matching march_c_fsm.sv's own
    `ST_DONE: if (!start) ...` (dropping either early would truncate the run).

    march-C is 10n; for this fixture's addr_width=6 (n=64) and the generator's default
    READ_LATENCY=1, a full pass is ~1601 cycles (traced directly through
    march_c_fsm.sv's own ST_ISSUE/ST_WAIT/ST_CHECK states: 2 cycles/write,
    2+READ_LATENCY cycles/read, 5 of each per address) -- iRunLoop(2500) below is a
    generous margin, not a tight estimate. Both reads chained in one program (proven
    safe: check_reads' masked .passed comparison is per-target, independent of how
    many other reads share the program) rather than two separate simulation runs, to
    avoid paying for the ~2500-cycle wait twice.
    """
    from warptap.pdl_interpreter import PDLInterpreter
    from warptap.pdl_verify import check_reads, correlate_observed

    sources = _generate_sources(tmp_path)
    inserted_verilog, graph, root = wrap_test_access(
        sources, "sram_1rw_mbist",
        onchip_selfrepair=True, onchip_repair_persistence=True, onchip_diagnosis=True,
        num_spare_rows=2, num_diagnosis_entries=4, addr_width=6,
    )
    assert len(graph.chain) == 15

    pdl = PDLInterpreter(graph, root)
    pdl.iTarget("test_mode")
    pdl.iWrite(1)
    pdl.iApply()
    pdl.iTarget("bist_start")
    pdl.iWrite(1)
    pdl.iApply()
    pdl.iRunLoop(2500)
    pdl.iTarget("bist_done")
    pdl.iRead(1)
    pdl.iApply()
    pdl.iTarget("bist_fail")
    pdl.iRead(0)
    pdl.iApply()

    ir_ops, tdo_by_cycle = _run_pdl_program(pdl.program, tmp_path, inserted_verilog)
    observed = correlate_observed(ir_ops, tdo_by_cycle)
    results = check_reads(ir_ops, observed)

    assert len(results) == 2
    assert results[0].passed, f"bist_done expected 1 after a full march-C pass -- got {results[0]}"
    assert results[1].passed, f"bist_fail expected 0 on a fault-free run -- got {results[1]}"


def test_self_repair_completes_through_real_jtag(tmp_path: Path) -> None:
    """self_repair_done/self_repair_fail: the on-chip self-repair FSM's other two
    status outputs -- self_repair_busy, its third, is already proven by
    test_self_repair_start_write_and_busy_read_through_real_jtag
    (test_testaccess_warptap_e2e.py), but only that self-repair STARTED, not that it
    finished. onchip_selfrepair_ctrl.sv's own fault-free trace (S_IDLE ->
    S_ANALYZE_KICK -> S_ANALYZE_WAIT -> S_ANALYZE_LATCH -> S_DECIDE -> S_VERIFY_KICK ->
    S_VERIFY_WAIT -> S_DONE) runs a FULL march-C analyze pass AND a full march-C verify
    pass before `self_repair_done &lt;= 1; self_repair_fail &lt;= bist_fail` (0, fault-free)
    -- roughly 2x a plain BIST pass's ~1601 cycles. self_repair_busy does NOT drop
    before self_repair_done asserts (it is a pure function of `ctrl_state != S_IDLE`,
    staying high through all of S_DONE too, confirmed independently by
    tests/hardware/test_soc_hw_selfrepair.py) -- this test polls self_repair_done
    directly rather than treating self_repair_busy dropping as "done"."""
    from warptap.pdl_interpreter import PDLInterpreter
    from warptap.pdl_verify import check_reads, correlate_observed

    sources = _generate_sources(tmp_path)
    inserted_verilog, graph, root = wrap_test_access(
        sources, "sram_1rw_mbist",
        onchip_selfrepair=True, onchip_repair_persistence=True, onchip_diagnosis=True,
        num_spare_rows=2, num_diagnosis_entries=4, addr_width=6,
    )
    assert len(graph.chain) == 15

    pdl = PDLInterpreter(graph, root)
    pdl.iTarget("self_repair_start")
    pdl.iWrite(1)
    pdl.iApply()
    pdl.iRunLoop(5000)
    pdl.iTarget("self_repair_done")
    pdl.iRead(1)
    pdl.iApply()
    pdl.iTarget("self_repair_fail")
    pdl.iRead(0)
    pdl.iApply()

    ir_ops, tdo_by_cycle = _run_pdl_program(pdl.program, tmp_path, inserted_verilog)
    observed = correlate_observed(ir_ops, tdo_by_cycle)
    results = check_reads(ir_ops, observed)

    assert len(results) == 2
    assert results[0].passed, (
        f"self_repair_done expected 1 after a full analyze+verify pass -- got {results[0]}"
    )
    assert results[1].passed, f"self_repair_fail expected 0 on a fault-free run -- got {results[1]}"


def test_diag_overflow_survives_real_jtag_fault_free(tmp_path: Path) -> None:
    """diag_overflow's own turn in the same family as
    test_wide_read_survives_real_jtag_fault_free/
    test_wide_read_addr_survives_real_jtag_fault_free -- the third and last
    diagnosis-log status port, previously only structurally verified. Fault-free run,
    so expect 0 (no overflow -- nothing was ever logged)."""
    from warptap.pdl_interpreter import PDLInterpreter
    from warptap.pdl_verify import check_reads, correlate_observed

    sources = _generate_sources(tmp_path)
    inserted_verilog, graph, root = wrap_test_access(
        sources, "sram_1rw_mbist",
        onchip_selfrepair=True, onchip_repair_persistence=True, onchip_diagnosis=True,
        num_spare_rows=2, num_diagnosis_entries=4, addr_width=6,
    )
    assert len(graph.chain) == 15

    pdl = PDLInterpreter(graph, root)
    pdl.iTarget("diag_overflow")
    pdl.iRead(0)
    pdl.iApply()

    ir_ops, tdo_by_cycle = _run_pdl_program(pdl.program, tmp_path, inserted_verilog)
    observed = correlate_observed(ir_ops, tdo_by_cycle)
    results = check_reads(ir_ops, observed)

    assert len(results) == 1
    assert results[0].passed, f"diag_overflow expected 0 on a fault-free run -- got {results[0]}"
