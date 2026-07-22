"""Real-SoC on-chip self-repair proof: a genuine RV32I CPU (PicoRV32,
vendored unmodified at flow/soc/vendor/picorv32/) boots and runs a hand-
assembled test program entirely through two self-repair-wrapped memories
(flow/soc/soc_top.sv), each with a real, baked-in hard defect
(flow/soc/sram_spares_soc_{instr,data}.v).

Everything proven earlier in this project stopped short of this: the
hardened 3-macro subsystem (flow/multimem/mbist/) is proven physically
(LibreLane: 0 DRT, LVS-clean) but was never simulated running real code, and
the multi-memory self-repair proof (test_mem_subsystem_selfrepair.py) drove
the memories directly over their functional bus -- never through an actual
CPU's fetch/load/store path. This test closes that gap: repair runs to
completion in its own reset domain at power-on (cpu_resetn is gated off both
memories' aggregate self_repair_done AND self_repair_busy clearing, so the
CPU never observes the pre-repair state -- see soc_top.sv's header comment
for why self_repair_done alone isn't enough), then the CPU is released and
runs a real program that writes 16 words to
data memory -- including the exact word holding the data memory's defect --
reads them all back, and reports PASS/FAIL through a status-register
mailbox (never an internal CPU register peek, which would be fragile).

Verification is three layers deep, matching this project's "never trust a
single signal" discipline:
  1. self_repair_fail == 0 for BOTH memories (the repair subsystem's own claim)
  2. the CPU's own program reports PASS via status_reg -- proof that real
     lw/sw instructions round-tripped correctly THROUGH the repaired path,
     not a testbench bypass poke
  3. a direct, CPU-independent, status-independent peek at the raw physical
     storage cell the defect was remapped to (computed from the wrapper's
     own row_repair_en/faulty_row_addr, not assumed), confirming the actual
     bits landed where the remap says they should have
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
# Must match flow/soc/sram_spares_soc_data.v's DEFECT_ADDR/DEFECT_BIT/DEFECT_SA1 exactly.
DEFECT_DATA_ADDR = 10
DEFECT_DATA_BIT = 4
DEFECT_DATA_SA1 = 1
DEFECT_DATA_EXPECTED_VALUE = DEFECT_DATA_ADDR * 4  # the program stores its own byte offset

# Guards against a vacuous test: if a future change to DEFECT_DATA_ADDR/BIT ever
# coincided with the firmware's own written value already having the defect's
# bit set (SA1) or clear (SA0), an unrepaired defect would round-trip correctly
# by accident and this test would pass even with self-repair completely broken.
_defect_bit_in_expected = bool(DEFECT_DATA_EXPECTED_VALUE & (1 << DEFECT_DATA_BIT))
assert _defect_bit_in_expected != bool(DEFECT_DATA_SA1), (
    f"word {DEFECT_DATA_ADDR}'s expected value 0x{DEFECT_DATA_EXPECTED_VALUE:x} already has "
    f"bit {DEFECT_DATA_BIT} {'set' if _defect_bit_in_expected else 'clear'}, which matches "
    f"what an unrepaired stuck-at-{DEFECT_DATA_SA1} fault would force it to anyway -- an "
    "unrepaired defect would be undetectable here; this test would pass vacuously"
)


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


async def _wait_self_repair_done(dut):
    """Drives nothing -- just waits. self_repair_start must already be held
    high by the caller. Returns self_repair_fail sampled the instant done
    reads back high, WITHOUT dropping start yet: self_repair_busy (and
    therefore cpu_resetn, gated on it) stays low/held until the caller
    explicitly drops start afterward -- see the firmware backdoor-load note
    in flow/soc/sram_spares_soc_instr.v for why that gap matters."""
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
async def test_soc_boots_and_survives_real_defects(dut):
    """Repair-at-boot, then a real CPU runs real code through both repaired
    memories and must report PASS -- with an independent raw-storage check
    on top of the CPU's own self-report."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    assert int(dut.cpu_trap.value) == 0, "CPU must not be executing/trapping before repair completes"

    dut.self_repair_start.value = 1
    await ClockCycles(dut.clk, 2)
    self_repair_fail = await with_timeout(_wait_self_repair_done(dut), 2_000_000, "ns")
    assert self_repair_fail == 0, (
        f"both memories' single defects fit their own single spare row -- expected "
        f"self_repair_fail=0, got {self_repair_fail}"
    )
    assert int(dut.fail_instr.value) == 0, "instruction memory's defect should have been repaired"
    assert int(dut.fail_data.value) == 0, "data memory's defect should have been repaired"
    assert int(dut.self_repair_busy.value) == 1, (
        "expected to still be in the S_DONE/busy window here -- cpu_resetn must not have "
        "released yet, since the firmware backdoor-load below has to land before the CPU's "
        "first fetch"
    )

    # Backdoor-load the firmware NOW, in the deterministic gap between
    # self_repair_done first reading high and self_repair_busy actually
    # clearing (see _wait_self_repair_done's docstring): march-C's own BIST
    # pass has already fully concluded (it drove self_repair_done), and
    # cpu_resetn is still held low (self_repair_busy hasn't cleared yet, since
    # that needs self_repair_start dropped below) -- so this is the one
    # window where writing directly into the array can't race either march-C
    # or the CPU's own first fetch.
    firmware_words = gen_program.build_program()
    for i, word in enumerate(firmware_words):
        dut.u_instr_mem.u_sram.mem[i].value = word

    dut.self_repair_start.value = 0
    await ClockCycles(dut.clk, 2)
    assert int(dut.self_repair_busy.value) == 0, "self_repair_busy should have cleared by now"

    # Layer 2: the CPU itself, running real fetch/load/store, must reach PASS.
    status = await with_timeout(_wait_status_nonzero(dut), 5_000_000, "ns")
    assert int(dut.cpu_trap.value) == 0, "CPU trapped instead of completing the program normally"
    assert status == PASS_SIG, (
        f"expected the firmware's PASS signature 0x{PASS_SIG:08x}, got 0x{status:08x} "
        f"(0x{FAIL_SIG:08x} would mean the CPU itself detected a mismatched read-back "
        "-- i.e. the repair did not actually survive real load/store traffic)"
    )

    # Layer 3: independent, CPU-bypassing, status-bypassing check of the raw
    # physical cell the data memory's defect was remapped to. Computed from
    # the wrapper's own repair registers, not assumed.
    row_repair_en = int(dut.u_data_mem.row_repair_en.value)
    faulty_row_addr = int(dut.u_data_mem.faulty_row_addr.value)
    assert row_repair_en == 1, "data memory's one spare row should be in use"
    assert faulty_row_addr == DEFECT_DATA_ADDR, (
        f"the repaired logical address should be the injected defect's own address "
        f"({DEFECT_DATA_ADDR}), got {faulty_row_addr}"
    )
    data_addr_width = int(dut.u_data_mem.ADDR_WIDTH.value)
    physical_spare_addr = (1 << data_addr_width) + 0  # spare index 0
    stored = _safe_int(dut.u_data_mem.u_sram.mem[physical_spare_addr])
    assert stored == DEFECT_DATA_EXPECTED_VALUE, (
        f"raw storage at the physical spare row (addr={physical_spare_addr}) should hold "
        f"the CPU's own write ({DEFECT_DATA_EXPECTED_VALUE}), got {stored} -- if this "
        "doesn't match even though the CPU reported PASS, the CPU-level proof alone would "
        "have been misleading"
    )
