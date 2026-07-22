"""Tiny local RV32I assembler + the SoC self-repair demo firmware.

No external RISC-V toolchain dependency (this repo doesn't otherwise need
one) -- just enough of the RV32I encoding to build one small, deterministic
test program: LUI, ADDI, SW, LW, BEQ, BNE. Two-pass: the program is built as
a flat list of abstract instructions (branch targets given as label names),
then encoded once every label's address is known.

The program (see PROG below) exercises exactly the address range that
flow/soc/sram_spares_soc_data.v's baked-in defect (word 10) lives in, through
real lw/sw instructions -- not a bypass poke:
  1. write word i (its own byte offset) to data_mem[i] for i in 0..N-1
  2. read every word back and compare against the expected value
  3. write a PASS or FAIL signature to the status-register mailbox, then
     loop forever

Run standalone to regenerate flow/soc/firmware.hex:
    python3 flow/soc/gen_program.py
"""
from __future__ import annotations

from pathlib import Path

# Must match flow/soc/soc_top.sv's DATA_BASE/STATUS_ADDR localparams exactly --
# hand-kept in sync, no shared source of truth between the two files.
DATA_BASE = 0x0000_1000
STATUS_ADDR = 0x0000_2000
N_WORDS = 16  # exercises data_mem[0..15]; defect lives at word 10, inside this range

PASS_SIG = 0xC0FFEEEE
FAIL_SIG = 0xBAD0BAD0

X_PTR, X_VAL, X_END, X_TMP, X_SIG, X_STAT = 1, 2, 3, 4, 5, 6

OUT_PATH = Path(__file__).resolve().parent / "firmware.hex"


def _u(value: int, bits: int) -> int:
    return value & ((1 << bits) - 1)


def r_lui(rd: int, imm20: int) -> int:
    assert 0 <= imm20 <= 0xFFFFF
    return (_u(imm20, 20) << 12) | (rd << 7) | 0x37


def r_addi(rd: int, rs1: int, imm12: int) -> int:
    assert -2048 <= imm12 <= 2047
    return (_u(imm12, 12) << 20) | (rs1 << 15) | (0b000 << 12) | (rd << 7) | 0x13


def r_sw(rs2: int, imm12: int, rs1: int) -> int:
    assert -2048 <= imm12 <= 2047
    imm = _u(imm12, 12)
    return ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (0b010 << 12) | ((imm & 0x1F) << 7) | 0x23


def r_lw(rd: int, imm12: int, rs1: int) -> int:
    assert -2048 <= imm12 <= 2047
    return (_u(imm12, 12) << 20) | (rs1 << 15) | (0b010 << 12) | (rd << 7) | 0x03


def r_branch(funct3: int, rs1: int, rs2: int, imm13: int) -> int:
    assert -4096 <= imm13 <= 4094 and imm13 % 2 == 0
    imm = _u(imm13, 13)
    bit12 = (imm >> 12) & 1
    bit11 = (imm >> 11) & 1
    bits10_5 = (imm >> 5) & 0x3F
    bits4_1 = (imm >> 1) & 0xF
    return (bit12 << 31) | (bits10_5 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (bits4_1 << 8) | (bit11 << 7) | 0x63


def li32(value: int) -> tuple[int, int]:
    """Standard LUI+ADDI 32-bit constant decomposition (sign-extension-aware)."""
    value &= 0xFFFFFFFF
    lo = value & 0xFFF
    if lo & 0x800:
        hi = ((value >> 12) + 1) & 0xFFFFF
        lo_signed = lo - 0x1000
    else:
        hi = (value >> 12) & 0xFFFFF
        lo_signed = lo
    assert ((hi << 12) + lo_signed) & 0xFFFFFFFF == value, "li32 decomposition is wrong"
    return hi, lo_signed


class Assembler:
    def __init__(self):
        self.prog: list[tuple] = []
        self.labels: dict[str, int] = {}

    def label(self, name: str) -> None:
        assert name not in self.labels, f"duplicate label {name}"
        self.labels[name] = len(self.prog) * 4

    def emit(self, *item) -> None:
        self.prog.append(item)

    def li(self, rd: int, value: int) -> None:
        hi, lo = li32(value)
        self.emit("LUI", rd, hi)
        self.emit("ADDI", rd, rd, lo)

    def encode(self) -> list[int]:
        words = []
        for idx, item in enumerate(self.prog):
            pc = idx * 4
            op = item[0]
            if op == "LUI":
                _, rd, imm20 = item
                words.append(r_lui(rd, imm20))
            elif op == "ADDI":
                _, rd, rs1, imm12 = item
                words.append(r_addi(rd, rs1, imm12))
            elif op == "SW":
                _, rs2, imm12, rs1 = item
                words.append(r_sw(rs2, imm12, rs1))
            elif op == "LW":
                _, rd, imm12, rs1 = item
                words.append(r_lw(rd, imm12, rs1))
            elif op in ("BEQ", "BNE"):
                _, rs1, rs2, target = item
                target_addr = target if isinstance(target, int) else self.labels[target]
                offset = target_addr - pc
                funct3 = 0b000 if op == "BEQ" else 0b001
                words.append(r_branch(funct3, rs1, rs2, offset))
            else:
                raise ValueError(f"unknown op {op}")
        return words


def build_program() -> list[int]:
    asm = Assembler()

    asm.li(X_PTR, DATA_BASE)
    asm.li(X_VAL, 0)
    asm.li(X_END, N_WORDS * 4)

    asm.label("write_loop")
    asm.emit("SW", X_VAL, 0, X_PTR)
    asm.emit("ADDI", X_PTR, X_PTR, 4)
    asm.emit("ADDI", X_VAL, X_VAL, 4)
    asm.emit("BNE", X_VAL, X_END, "write_loop")

    asm.li(X_PTR, DATA_BASE)
    asm.li(X_VAL, 0)

    asm.label("read_loop")
    asm.emit("LW", X_TMP, 0, X_PTR)
    asm.emit("BNE", X_TMP, X_VAL, "fail")
    asm.emit("ADDI", X_PTR, X_PTR, 4)
    asm.emit("ADDI", X_VAL, X_VAL, 4)
    asm.emit("BNE", X_VAL, X_END, "read_loop")

    asm.label("pass")
    asm.li(X_SIG, PASS_SIG)
    asm.li(X_STAT, STATUS_ADDR)
    asm.emit("SW", X_SIG, 0, X_STAT)
    asm.label("pass_halt")
    asm.emit("BEQ", 0, 0, "pass_halt")

    asm.label("fail")
    asm.li(X_SIG, FAIL_SIG)
    asm.li(X_STAT, STATUS_ADDR)
    asm.emit("SW", X_SIG, 0, X_STAT)
    asm.label("fail_halt")
    asm.emit("BEQ", 0, 0, "fail_halt")

    return asm.encode()


def write_hex(words: list[int], path: Path = OUT_PATH) -> Path:
    path.write_text("\n".join(f"{w:08x}" for w in words) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    words = build_program()
    out = write_hex(words)
    print(f"{len(words)} instructions ({len(words) * 4} bytes) -> {out}")
