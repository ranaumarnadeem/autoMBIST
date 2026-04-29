from __future__ import annotations

import random
from enum import Enum
from pathlib import Path


class FaultType(Enum):
    """Enumeration of supported fault types."""
    STUCK_AT = "stuck-at"
    TRANSITION_UP = "transition-up"
    TRANSITION_DOWN = "transition-down"


SA0_FILENAME = "sa0_faults.hex"
SA1_FILENAME = "sa1_faults.hex"
TF_UP_FILENAME = "tf_up_faults.hex"
TF_DOWN_FILENAME = "tf_down_faults.hex"


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
    fault_type: FaultType = FaultType.STUCK_AT,
    faults: int = 0,
    seed: int | None = None,
) -> tuple[list[int], list[int]]:
    _validate_positive_int("addr_width", addr_width)
    _validate_positive_int("data_width", data_width)
    _validate_non_negative_int("faults", faults)

    depth = 1 << addr_width
    all_ones = (1 << data_width) - 1

    if fault_type == FaultType.STUCK_AT:
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
    
    else:
        # Transition fault generation: return two mask arrays
        # For TF-UP: mask[addr] has 1s for bits that cannot transition 0->1
        # For TF-DOWN: mask[addr] has 1s for bits that cannot transition 1->0
        tf_mask = [0] * depth
        
        if faults == 0:
            return tf_mask, tf_mask
        
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
            tf_mask[addr] |= bit_mask
        
        # Return the same mask for both TF-UP and TF-DOWN; the caller decides which to use
        return tf_mask, tf_mask


def write_fault_files(
    *,
    outdir: Path,
    data_width: int,
    fault_type: FaultType = FaultType.STUCK_AT,
    mask1_words: list[int],
    mask2_words: list[int],
) -> tuple[Path, Path]:
    _validate_positive_int("data_width", data_width)
    outdir.mkdir(parents=True, exist_ok=True)

    width = _hex_width(data_width)
    
    if fault_type == FaultType.STUCK_AT:
        file1_path = outdir / SA0_FILENAME
        file2_path = outdir / SA1_FILENAME
    elif fault_type == FaultType.TRANSITION_UP:
        file1_path = outdir / TF_UP_FILENAME
        file2_path = outdir / TF_UP_FILENAME  # Both point to same file for TF
    elif fault_type == FaultType.TRANSITION_DOWN:
        file1_path = outdir / TF_DOWN_FILENAME
        file2_path = outdir / TF_DOWN_FILENAME  # Both point to same file for TF
    else:
        raise ValueError(f"Unknown fault type: {fault_type}")

    file1_path.write_text(_format_words(mask1_words, width), encoding="ascii")
    if fault_type == FaultType.STUCK_AT:
        file2_path.write_text(_format_words(mask2_words, width), encoding="ascii")

    return file1_path, file2_path


def generate_fault_files(
    *,
    outdir: Path | str,
    addr_width: int,
    data_width: int,
    fault_type: FaultType = FaultType.STUCK_AT,
    faults: int = 0,
    seed: int | None = None,
) -> tuple[Path, Path]:
    outdir_path = Path(outdir)
    mask1_words, mask2_words = generate_fault_masks(
        addr_width=addr_width,
        data_width=data_width,
        fault_type=fault_type,
        faults=faults,
        seed=seed,
    )

    return write_fault_files(
        outdir=outdir_path,
        data_width=data_width,
        fault_type=fault_type,
        mask1_words=mask1_words,
        mask2_words=mask2_words,
    )
