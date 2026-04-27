from __future__ import annotations

import random
from pathlib import Path

SA0_FILENAME = "sa0_faults.hex"
SA1_FILENAME = "sa1_faults.hex"


def _validate_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _validate_non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _hex_width(data_width: int) -> int:
    return (data_width + 3) // 4


def _format_words(words: list[int], width: int) -> str:
    return "\n".join(f"{word:0{width}X}" for word in words) + "\n"


def generate_fault_masks(
    *,
    addr_width: int,
    data_width: int,
    faults: int = 0,
    seed: int | None = None,
) -> tuple[list[int], list[int]]:
    _validate_positive_int("addr_width", addr_width)
    _validate_positive_int("data_width", data_width)
    _validate_non_negative_int("faults", faults)

    depth = 1 << addr_width
    all_ones = (1 << data_width) - 1

    sa0_words = [all_ones] * depth
    sa1_words = [0] * depth

    if faults == 0:
        return sa0_words, sa1_words

    max_fault_sites = depth * data_width
    if faults > max_fault_sites:
        raise ValueError(
            f"faults ({faults}) exceeds available bit locations ({max_fault_sites})"
        )

    rng = random.Random(seed)
    selected_locations = rng.sample(range(max_fault_sites), faults)

    for location in selected_locations:
        addr, bit = divmod(location, data_width)
        bit_mask = 1 << bit

        if rng.randrange(2) == 0:
            sa0_words[addr] &= ~bit_mask
            sa1_words[addr] &= ~bit_mask
        else:
            sa1_words[addr] |= bit_mask
            sa0_words[addr] |= bit_mask

    return sa0_words, sa1_words


def write_fault_files(
    *,
    outdir: Path,
    data_width: int,
    sa0_words: list[int],
    sa1_words: list[int],
) -> tuple[Path, Path]:
    _validate_positive_int("data_width", data_width)
    outdir.mkdir(parents=True, exist_ok=True)

    width = _hex_width(data_width)
    sa0_path = outdir / SA0_FILENAME
    sa1_path = outdir / SA1_FILENAME

    sa0_path.write_text(_format_words(sa0_words, width), encoding="ascii")
    sa1_path.write_text(_format_words(sa1_words, width), encoding="ascii")

    return sa0_path, sa1_path


def generate_fault_files(
    *,
    outdir: Path | str,
    addr_width: int,
    data_width: int,
    faults: int = 0,
    seed: int | None = None,
) -> tuple[Path, Path]:
    outdir_path = Path(outdir)
    sa0_words, sa1_words = generate_fault_masks(
        addr_width=addr_width,
        data_width=data_width,
        faults=faults,
        seed=seed,
    )

    return write_fault_files(
        outdir=outdir_path,
        data_width=data_width,
        sa0_words=sa0_words,
        sa1_words=sa1_words,
    )
