"""Real-macro SoC boot proof: the same RV32I CPU as test_soc_selfrepair.py,
now driving flow/soc/hardened/soc_top_hw.sv -- selfrepair_a/selfrepair_b
wrapping the ACTUAL flow/multimem/mbist/ real OpenRAM macro adapters
(behavioral simulation views of the exact macros LibreLane hardens to GDS).

This is NOT a second defect-correction proof -- real macros have no
defect-injection knob pre-silicon (flow/soc/README.md's own "what this
does/doesn't prove" section explains why). It exists to catch functional
bugs BEFORE spending LibreLane time on soc_top_hw, and it found a real one:
onchip_selfrepair's generated wrapper defaults to READ_LATENCY=1 for its
internal march-C engine, which is correct for this project's toy behavioral
fixtures (dout stays registered indefinitely once set) but WRONG for
OpenRAM's real macro model, whose dout0 is forced back to X shortly after
EVERY clock edge (`#(T_HOLD) dout0 = 32'bx;` in the vendor .v, modeling a
real SRAM's narrow output-valid window) and only refreshed by an actual
read. READ_LATENCY=1 samples one cycle too late, right after that decay --
march-C saw X on the very first read-back and declared a phantom failure on
a macro that was never actually defective. READ_LATENCY=0 (see
run_soc_hw_selfrepair_tb.py's generator config) samples at the correct
edge and passes cleanly. This gap was never caught before because
flow/multimem/mbist/mem_subsystem_mbist.sv (the already-hardened design
using these same real macros) was only ever elaboration-checked, never
actually simulated with self-repair running -- see that file's own header
comment.

Verification: self_repair_fail==0 for both memories (BIST ran clean, as
expected -- there's nothing to repair on a real macro); the CPU's own
program reaches PASS via real load/store traffic through the real macro
(exercising the bus bridge's wait_cnt==1 read timing, genuinely different
from flow/soc/soc_top.sv's toy-fixture wait_cnt==2 -- see soc_top_hw.sv's
header); and row_repair_en stays 0 on both memories (no phantom repair was
ever triggered against a macro that was never actually defective).
"""
from __future__ import annotations

import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer, with_timeout

SOC_DIR = Path(__file__).resolve().parents[2] / "flow" / "soc"
if str(SOC_DIR) not in sys.path:
    sys.path.insert(0, str(SOC_DIR))

import gen_program  # noqa: E402

PASS_SIG = 0xC0FFEEEE
FAIL_SIG = 0xBAD0BAD0


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


async def _wait_self_repair_done(dut):
    """Same contract as test_soc_selfrepair.py's helper of the same name:
    self_repair_start must already be held high by the caller; returns
    self_repair_fail sampled the instant done reads back high, WITHOUT
    dropping start yet (self_repair_busy stays high, holding cpu_resetn low,
    until the caller explicitly drops start afterward)."""
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        if int(dut.self_repair_done.value) == 1:
            return int(dut.self_repair_fail.value)


async def _wait_status_nonzero(dut):
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        val = _safe_int(dut.status_reg)
        if val:
            return val


@cocotb.test()
async def test_soc_hw_boots_on_real_macros(dut):
    """Repair-at-boot (finds nothing, since real macros have no injected
    defect), then the real CPU runs the real firmware through the real
    macros' actual behavioral timing and must report PASS."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())  # matches the proven 20ns harden CLOCK_PERIOD

    dut.rst_n.value = 0
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    assert int(dut.cpu_trap.value) == 0, "CPU must not be executing/trapping before repair completes"

    dut.self_repair_start.value = 1
    await ClockCycles(dut.clk, 2)
    self_repair_fail = await with_timeout(_wait_self_repair_done(dut), 10_000_000, "ns")
    assert self_repair_fail == 0, (
        f"neither real macro is actually defective -- expected self_repair_fail=0, got {self_repair_fail} "
        "(if this regresses, check the generator's read_latency=0 override in "
        "run_soc_hw_selfrepair_tb.py -- see this module's docstring for why the default of 1 is wrong here)"
    )
    assert int(dut.fail_instr.value) == 0
    assert int(dut.fail_data.value) == 0
    assert int(dut.self_repair_busy.value) == 1, (
        "expected to still be in the S_DONE/busy window here -- cpu_resetn must not have "
        "released yet, since the firmware backdoor-load below has to land before the CPU's first fetch"
    )

    # Backdoor-load the firmware in the same deterministic gap
    # test_soc_selfrepair.py uses: after self_repair_done, before
    # self_repair_busy clears -- BIST's own write pass has already fully
    # concluded, and cpu_resetn is still held low. One hierarchy level
    # deeper than the toy-fixture test: u_sram here is the sram_wrap_a
    # adapter, and the real macro's storage array lives at u_sram.u_macro.mem.
    firmware_words = gen_program.build_program()
    for i, word in enumerate(firmware_words):
        dut.u_instr_mem.u_sram.u_macro.mem[i].value = word

    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 2)
    assert int(dut.self_repair_busy.value) == 0, "self_repair_busy should have cleared by now"

    status = await with_timeout(_wait_status_nonzero(dut), 10_000_000, "ns")
    assert int(dut.cpu_trap.value) == 0, "CPU trapped instead of completing the program normally"
    assert status == PASS_SIG, (
        f"expected the firmware's PASS signature 0x{PASS_SIG:08x}, got 0x{status:08x} "
        f"(0x{FAIL_SIG:08x} would mean the CPU's own read-back-and-compare loop failed against "
        "the real macro -- most likely a wrong bus-bridge wait_cnt for this macro's actual timing)"
    )

    # Sanity check the flip side of the repair claim: no PHANTOM repair was
    # ever triggered against a macro that was never actually defective.
    assert int(dut.u_instr_mem.row_repair_en.value) == 0, "instruction macro was never defective"
    assert int(dut.u_data_mem.row_repair_en.value) == 0, "data macro was never defective"
