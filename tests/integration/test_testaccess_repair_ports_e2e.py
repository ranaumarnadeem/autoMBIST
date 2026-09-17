"""Real-Icarus proof that a JTAG-driven write to the tester-driven repair_ports:
passthrough ports (row_repair_en/faulty_row_addr/col_repair_en/faulty_bit) actually
STEERS a subsequent functional-bus access to a spare row/column -- not just that the
write reaches the port (test_testaccess_warptap_e2e.py's structural-only proof) or that
a wide write reaches real RTL in general (test_testaccess_wide_ports_e2e.py's
fuse_faulty_row_addr/fuse_row_repair_en proof, a different port pair under a different,
onchip_selfrepair, config -- these repair_ports: entries are structurally never present
together with onchip_selfrepair, generator.py enforces that).

Uses a NEW testbench, tb_sram_1rw_repair_ports.v, that -- unlike
tests/hardware/tb_sram_1rw_mbist.v -- ALSO drives the functional bus (func_csb/
func_addr/func_din/func_we) every cycle, alongside the JTAG/TAP stimulus, since proving
a real remap effect needs both interfaces coordinated in ONE simulation run: the memory
model (sram_model_spares.sv) has no reset port at all, so a second, separate
`run_verilog_testbench` call would start a fresh process with uninitialized memory,
losing whatever an earlier call wrote.

The verification LOGIC below (write MARKER_A, write MARKER_B, read back through both
repair-on and repair-off states, confirming the spare is genuinely separate storage --
not a passthrough no-op) mirrors tests/hardware/test_repair_col_distinct.py's own
proven 6-step pattern; what's new here is driving row_repair_en/faulty_row_addr/
col_repair_en/faulty_bit via a real JTAG scan path instead of a raw cocotb DUT pin.

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
TESTBENCH = REPO_ROOT / "tests" / "hardware" / "tb_sram_1rw_repair_ports.v"
# (tms, tdi, trst_n, rst_n, csb, we, addr, wdata) -- functional bus idle (csb=1)
# through the reset lead-in, matching every other fixture's own convention.
_RESET_LEAD_IN = [
    (0, 0, 0, 0, 1, 0, 0, 0),
    (0, 0, 0, 0, 1, 0, 0, 0),
    (0, 0, 1, 1, 1, 0, 0, 0),
]
# Cycles to hold a functional write (csb=0,we=1) before deasserting -- 2 to commit
# (sram_model_spares.sv's write is a single posedge-clocked store) + 2 margin,
# matching test_repair_col_distinct.py's own _functional_write helper's shape.
_WRITE_CYCLES = 4
# Cycles to hold a functional read (csb=0,we=0) before its last sample is trusted --
# the model's dout is a 2-stage register (addr latches on cycle N, dout updates from
# mem[addr_q] on cycle N+1), so 2 is the true minimum; 6 matches
# test_repair_col_distinct.py's own read_latency+4-cycle margin convention.
_READ_CYCLES = 6

# One shared fixture for both tests below (row-repair and column-repair), matching
# tb_sram_1rw_repair_ports.v's own hardcoded DUT port list, which needs all four
# repair_ports entries present regardless of which one a given test actually drives --
# a row-only config would produce a wrapper missing col_repair_en/faulty_bit entirely,
# which the shared testbench's DUT instantiation cannot connect to. The row-repair
# test simply never touches col_repair_en/faulty_bit (they stay at their JTAG
# reset-default of 0, i.e. column repair off throughout, no interference). Matches
# _REPAIR_PORTS_CONFIG in test_testaccess_warptap_e2e.py exactly (already structurally
# verified there) -- bit_index_width = ceil(log2(data_width=8)) = 3.
_CONFIG = {
    "memory_name": "sram_1rw", "wrapper_module_name": "sram_1rw_mbist",
    "addr_width": 4, "data_width": 8, "we_active_low": True,
    "ports": {
        "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0",
        "we": "web0", "csb": "csb0", "spare_wen": "spare_wen0",
    },
    "redundancy": {"num_spare_rows": 2, "num_spare_cols": 1},
    "repair_ports": [
        {"name": "row_repair_en", "width": 2, "dir": "input"},
        {"name": "faulty_row_addr", "width": 8, "dir": "input"},
        {"name": "col_repair_en", "width": 1, "dir": "input"},
        {"name": "faulty_bit", "width": 3, "dir": "input"},
    ],
}


def _generate_sources(tmp_path: Path) -> list[Path]:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(_CONFIG, sort_keys=False), encoding="utf-8")
    wrapper_path = generate_from_config(config_path, tmp_path / "gen", algo="march-c")
    shared = wrapper_path.parent

    # Same rename-a-copy technique as test_testaccess_wide_ports_e2e.py's own
    # _generate_sources: the wrapper instantiates its memory literally as `sram_1rw`
    # (memory_name), but the real spare-augmented model's own module is
    # `sram_model_spares` -- rename a copy for a plain iverilog elaboration (the
    # Makefile-driven flow that would otherwise do this renaming isn't in use here).
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
        shared / "repair_remap_row.sv",
        shared / "repair_remap_col.sv",
        renamed_model,
    ]
    for src in sources:
        assert src.is_file(), f"expected generated openMBIST source missing: {src}"
    return sources


class _FuncBusProgram:
    """Builds the functional-bus stimulus in lockstep with a PDLInterpreter's own
    program, so a single combined simulation run can interleave JTAG configuration
    (row_repair_en/faulty_row_addr/col_repair_en/faulty_bit, driven through
    `pdl`) with real functional-bus reads/writes (driven directly here) -- needed
    because sram_model_spares.sv has no reset port at all, so a second, separate
    run would start a fresh process with uninitialized memory, losing whatever an
    earlier run wrote.

    `catch_up()` pads with idle (csb=1) rows for however many TAP cycles a JTAG
    iApply() actually consumed -- unknown in advance (depends on this instrument's
    position in the chain and how many phase-1/phase-2 navigation rounds it needs),
    computed after the fact via to_cycles() on the real ops so far. `write()`/
    `read()` push an exact, caller-chosen cycle count (always driven by a matching
    pdl.iRunLoop(N) so the two stay in lockstep) -- iRunLoop(N) is exactly N DUT
    clk cycles with no overhead, confirmed directly against warptap's own
    tap_ir_play.to_cycles()/shift_op_ranges().
    """

    def __init__(self, pdl):
        from warptap.tap_fsm import TapState
        from warptap.tap_ir import GotoState, ShiftIR, bits_to_int

        self._pdl = pdl
        self._base_ops = [
            GotoState(TapState.SHIFT_IR),
            ShiftIR(4, tdi=bits_to_int([0, 0, 0, 0])),
            GotoState(TapState.RUN_TEST_IDLE),
        ]
        self.func_rows: list[tuple[int, int, int, int]] = []
        self.read_checkpoints: list[int] = []  # index into the post-run dout_by_cycle

    def _target_len(self) -> int:
        from warptap.tap_ir_play import to_cycles

        return len(to_cycles(self._base_ops + self._pdl.program))

    def catch_up(self) -> None:
        target = self._target_len()
        while len(self.func_rows) < target:
            self.func_rows.append((1, 0, 0, 0))

    def write(self, addr: int, data: int) -> None:
        self.catch_up()
        self._pdl.iRunLoop(_WRITE_CYCLES)
        for i in range(_WRITE_CYCLES):
            if i < 2:
                self.func_rows.append((0, 1, addr, data))
            else:
                self.func_rows.append((1, 0, 0, 0))
        assert len(self.func_rows) == self._target_len(), "write() cycle count drifted from iRunLoop"

    def read(self, addr: int) -> None:
        self.catch_up()
        self._pdl.iRunLoop(_READ_CYCLES)
        for _ in range(_READ_CYCLES):
            self.func_rows.append((0, 0, addr, 0))
        self.read_checkpoints.append(len(self.func_rows) - 1)
        assert len(self.func_rows) == self._target_len(), "read() cycle count drifted from iRunLoop"

    def run(self, tmp_path: Path, inserted_verilog: str) -> tuple[list, list[int]]:
        """Run the combined program, return (ir_ops, dout_by_cycle) -- callers index
        dout_by_cycle at their own recorded read_checkpoints for each read's result."""
        from warptap.sim_io import run_verilog_testbench
        from warptap.tap_ir_play import to_cycles

        self.catch_up()
        ir_ops = self._base_ops + self._pdl.program
        cycles = to_cycles(ir_ops)
        assert len(self.func_rows) == len(cycles), (
            f"func_rows ({len(self.func_rows)}) must match the real TAP cycle count "
            f"({len(cycles)}) exactly"
        )
        rows = _RESET_LEAD_IN + [
            (tms, tdi, 1, 1, csb, we, addr, wdata)
            for (tms, tdi), (csb, we, addr, wdata) in zip(cycles, self.func_rows)
        ]
        stimulus = "\n".join(" ".join(str(v) for v in row) for row in rows) + "\n"

        with tempfile.TemporaryDirectory(prefix="test-testaccess-repair-ports-") as tmpdir:
            v_path = Path(tmpdir) / "sram_1rw_mbist_sib_inserted.v"
            v_path.write_text(inserted_verilog, encoding="utf-8")
            stdout = run_verilog_testbench(
                [v_path, TESTBENCH], extra_inputs={"stimulus.txt": stimulus},
            )

        # func_dout stays raw 'x' (unknown) until the first real memory access
        # resolves it -- sram_model_spares.sv has no reset path for dout0 at all
        # (confirmed directly: its always_ff block only ever assigns dout0 from
        # `if (!csb0_q && web0_q) dout0 <= mem[addr0_q]`). Keep these as raw
        # strings rather than eagerly int()-parsing every cycle (most of which are
        # pure JTAG-shift cycles this test never asks about) -- only the caller's
        # own read_checkpoints are ever actually parsed, in _dout_at() below.
        trace_lines = [line for line in stdout.splitlines() if line.startswith("TRACE,")]
        dout_by_cycle = [line.split(",")[3] for line in trace_lines]
        n = len(_RESET_LEAD_IN)
        return ir_ops, dout_by_cycle[n:]


def _dout_at(dout_by_cycle: list[str], index: int) -> int:
    raw = dout_by_cycle[index]
    if "x" in raw or "X" in raw:
        raise AssertionError(
            f"func_dout is still unknown ('{raw}') at the recorded read checkpoint "
            f"(cycle index {index}) -- the read window (_READ_CYCLES) may be too "
            "short, or this cycle was never actually reached by a real read"
        )
    return int(raw, 2)


def test_row_repair_steers_functional_access_through_real_jtag(tmp_path: Path) -> None:
    """row_repair_en/faulty_row_addr, tester-driven: JTAG-configure a steer of
    logical address 5 onto spare row 0, prove it with two DISTINCT known markers
    rather than relying on reset/X semantics (matching
    tests/hardware/test_repair_col_distinct.py's own rigor):

    1. repair off, functional-write MARKER_B=0x5A to addr 5, read back -- expect
       0x5A (sanity: plain logical access works).
    2. repair on, functional-write MARKER_A=0xA5 to addr 5 -- repair_remap_row.sv
       steers this to physical address 2**ADDR_WIDTH+0, NOT logical row 5. Read
       back (still on) -- expect 0xA5, through the spare.
    3. repair off again, read addr 5 -- expect 0x5A again. This is the decisive
       assertion: if step 2's write had actually landed in logical row 5 (remap a
       no-op), this would read 0xA5, not 0x5A.
    """
    from warptap.pdl_interpreter import PDLInterpreter

    sources = _generate_sources(tmp_path)
    inserted_verilog, graph, root = wrap_test_access(
        sources, "sram_1rw_mbist", repair_ports=_CONFIG["repair_ports"],
    )
    assert len(graph.chain) == 8  # base 4 + all 4 repair_ports entries

    ADDR = 5
    MARKER_A = 0xA5
    MARKER_B = 0x5A

    pdl = PDLInterpreter(graph, root)
    fb = _FuncBusProgram(pdl)

    pdl.iTarget("faulty_row_addr")
    pdl.iWrite(ADDR)  # spare 0's slice; spare 1's slice stays 0 and is never enabled
    pdl.iApply()
    pdl.iTarget("row_repair_en")
    pdl.iWrite(0b00)
    pdl.iApply()
    fb.write(ADDR, MARKER_B)
    fb.read(ADDR)

    pdl.iTarget("row_repair_en")
    pdl.iWrite(0b01)
    pdl.iApply()
    fb.write(ADDR, MARKER_A)
    fb.read(ADDR)

    pdl.iTarget("row_repair_en")
    pdl.iWrite(0b00)
    pdl.iApply()
    fb.read(ADDR)

    ir_ops, dout_by_cycle = fb.run(tmp_path, inserted_verilog)
    assert len(fb.read_checkpoints) == 3
    observed = [_dout_at(dout_by_cycle, i) for i in fb.read_checkpoints]

    assert observed[0] == MARKER_B, f"step 1 (repair off): expected {MARKER_B:#x}, got {observed[0]:#x}"
    assert observed[1] == MARKER_A, f"step 2 (repair on): expected {MARKER_A:#x}, got {observed[1]:#x}"
    assert observed[2] == MARKER_B, (
        f"step 3 (repair off again): expected {MARKER_B:#x} (logical row 5 untouched by "
        f"step 2's repair-on write), got {observed[2]:#x} -- if this is {MARKER_A:#x}, "
        "the row remap is a no-op passthrough, not a genuine redirect onto spare storage"
    )


def test_column_repair_steers_functional_access_through_real_jtag(tmp_path: Path) -> None:
    """col_repair_en/faulty_bit, tester-driven: the exact 6-step MARKER_A/MARKER_B
    sequence from tests/hardware/test_repair_col_distinct.py's own proven cocotb
    test, replayed here with the repair config driven via a real JTAG scan path
    instead of a raw cocotb DUT pin.

    target_bit=3, MARKER_A=0x00 (bit 3 clear), MARKER_B=0xFF (bit 3 set):
    1. repair on,  write MARKER_A -- logical lane AND spare (spare_wen=col_repair_en=1
       here) both take it.
    2. repair on,  read -- expect MARKER_A.
    3. repair off, write MARKER_B -- logical lane takes it, spare untouched
       (spare_wen=col_repair_en=0).
    4. repair off, read -- expect MARKER_B (sanity: logical lane visible).
    5. repair on again, read -- expect MARKER_B with bit 3 cleared (0xF7): bit 3 is
       now sourced from the STALE spare (still holding MARKER_A's clear bit from
       step 1) -- the decisive "not a passthrough" assertion.
    6. repair on, rewrite MARKER_B, read -- expect the full 0xFF: proves the spare
       can hold a real 1, not a hardwired 0 that would otherwise also pass step 5.
    """
    from warptap.pdl_interpreter import PDLInterpreter

    sources = _generate_sources(tmp_path)
    inserted_verilog, graph, root = wrap_test_access(
        sources, "sram_1rw_mbist", repair_ports=_CONFIG["repair_ports"],
    )
    assert len(graph.chain) == 8  # base 4 + all 4 repair_ports entries

    ADDR = 5
    TARGET_BIT = 3
    MARKER_A = 0x00
    MARKER_B = 0xFF

    pdl = PDLInterpreter(graph, root)
    fb = _FuncBusProgram(pdl)

    pdl.iTarget("faulty_bit")
    pdl.iWrite(TARGET_BIT)
    pdl.iApply()

    pdl.iTarget("col_repair_en")
    pdl.iWrite(1)
    pdl.iApply()
    fb.write(ADDR, MARKER_A)          # step 1
    fb.read(ADDR)                     # step 2

    pdl.iTarget("col_repair_en")
    pdl.iWrite(0)
    pdl.iApply()
    fb.write(ADDR, MARKER_B)          # step 3
    fb.read(ADDR)                     # step 4

    pdl.iTarget("col_repair_en")
    pdl.iWrite(1)
    pdl.iApply()
    fb.read(ADDR)                     # step 5

    fb.write(ADDR, MARKER_B)          # step 6 write (repair still on)
    fb.read(ADDR)                     # step 6 read

    ir_ops, dout_by_cycle = fb.run(tmp_path, inserted_verilog)
    assert len(fb.read_checkpoints) == 4
    observed = [_dout_at(dout_by_cycle, i) for i in fb.read_checkpoints]

    assert observed[0] == MARKER_A, f"step 2: expected {MARKER_A:#x}, got {observed[0]:#x}"
    assert observed[1] == MARKER_B, f"step 4: expected {MARKER_B:#x}, got {observed[1]:#x}"
    expected_step5 = MARKER_B & ~(1 << TARGET_BIT) & 0xFF
    assert observed[2] == expected_step5, (
        f"step 5: expected {expected_step5:#x} (bit {TARGET_BIT} sourced from the "
        f"stale spare, still 0 from step 1) -- got {observed[2]:#x}. If this is "
        f"{MARKER_B:#x}, the column remap is a no-op passthrough, not a genuine "
        "redirect onto separate spare storage"
    )
    assert observed[3] == MARKER_B, (
        f"step 6: expected the full {MARKER_B:#x} (proves the spare can hold a real "
        f"1, not a hardwired 0) -- got {observed[3]:#x}"
    )
