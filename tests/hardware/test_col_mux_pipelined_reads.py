"""Back-to-back functional reads through a column remap on a column-muxed memory.

On a column-muxed memory repair_remap_col steers a read only when the column
bits of the read that PRODUCED dout match the repaired word column. dout lags
the address (READ_LATENCY), so the remap must use the column bits it captured
when the read was issued, not the address on the bus now. The BIST and the
functional fail scan both hold the address while they look at dout, so neither
can tell the two apart; this test issues a read every cycle and changes the
address right after each clock edge, as a pipelined reader does.

It loads the repair signature from COL_REPAIR_EN / FAULTY_BIT (computed in
Python, see tests/integration/test_repair_muxed_col_e2e.py), writes every word
with a pattern whose repaired bit differs between neighbouring words, then
reads a sequence of addresses back to back and checks every word.
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer, with_timeout


def _safe_int(handle):
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


def _pattern(addr: int, data_width: int) -> int:
    # Alternating bits, flipped between even and odd addresses: every bit of a
    # word differs from the same bit of the next word.
    word = int("01" * data_width, 2) & ((1 << data_width) - 1)
    return word if addr % 2 == 0 else word ^ ((1 << data_width) - 1)


async def _write_all(dut, depth: int, data_width: int) -> None:
    for addr in range(depth):
        dut.func_csb.value = 0
        dut.func_we.value = 1
        dut.func_addr.value = addr
        dut.func_din.value = _pattern(addr, data_width)
        await ClockCycles(dut.clk, 2)
    dut.func_csb.value = 1
    dut.func_we.value = 0
    await ClockCycles(dut.clk, 2)


async def _pipelined_reads(dut, sequence: list[int], read_latency: int) -> list[tuple[int, int | None]]:
    """A read every cycle. The address changes 1 ns after each rising edge and
    dout is sampled 6 ns after it (after the falling edge, before the next
    rising one), when it holds the read captured read_latency edges earlier."""
    got = []
    dut.func_we.value = 0
    dut.func_csb.value = 0
    dut.func_addr.value = sequence[0]
    for edge in range(len(sequence) + read_latency):
        await RisingEdge(dut.clk)   # captures sequence[edge], if any
        await Timer(1, unit="ns")
        if edge + 1 < len(sequence):
            dut.func_addr.value = sequence[edge + 1]
        else:
            dut.func_csb.value = 1
        await Timer(5, unit="ns")
        read = edge - read_latency
        if read >= 0:
            got.append((sequence[read], _safe_int(dut.func_dout)))
    return got


@cocotb.test()
async def test_col_mux_pipelined_reads(dut):
    addr_width = int(os.getenv("ADDR_WIDTH", "3"))
    data_width = int(os.getenv("DATA_WIDTH", "4"))
    read_latency = int(os.getenv("READ_LATENCY", "1"))
    depth = 1 << addr_width

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.test_mode.value = 0
    dut.bist_start.value = 0
    dut.func_csb.value = 1
    dut.func_we.value = 0
    dut.func_addr.value = 0
    dut.func_din.value = 0
    dut.row_repair_en.value = 0
    dut.faulty_row_addr.value = 0
    dut.col_repair_en.value = int(os.getenv("COL_REPAIR_EN", "0"))
    dut.faulty_bit.value = int(os.getenv("FAULTY_BIT", "0"))
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    await with_timeout(_write_all(dut, depth, data_width), 100_000, "ns")
    # Neighbouring words of one physical row, both orders, then across rows.
    sequence = [2, 3, 2, 1, 2, 2, 3, 0, 6, 7, 6, 5, 2, 6, 3, 7] + list(range(depth))
    got = await with_timeout(_pipelined_reads(dut, sequence, read_latency), 100_000, "ns")
    wrong = [(addr, word) for addr, word in got if word != _pattern(addr, data_width)]
    for addr, word in wrong:
        print(f"PIPELINED_READ_MISMATCH addr={addr} got={word} want={_pattern(addr, data_width)}")
    assert not wrong, f"{len(wrong)} of {len(got)} back-to-back reads came back wrong: {wrong}"
