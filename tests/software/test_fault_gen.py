"""Unit tests for src/autombist/fault_gen.py, focused on the port-coupling
addition (FaultType.PORT_COUPLING, PC_MASK_FILENAME, and the corresponding
branches in generate_fault_masks/write_fault_files/generate_fault_files).

Fast, software-only (no simulator) -- mirrors the style of the existing
stuck-at/transition coverage implied by test_generator*.py, but exercises
fault_gen.py directly since it previously had no dedicated test module.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.fault_gen import (
    PC_MASK_FILENAME,
    SA0_FILENAME,
    SA1_FILENAME,
    FaultType,
    generate_fault_files,
    generate_fault_masks,
    write_fault_files,
)


# ---------------------------------------------------------------------------
# FaultType / filename constants
# ---------------------------------------------------------------------------


def test_fault_type_port_coupling_value() -> None:
    assert FaultType.PORT_COUPLING.value == "port-coupling"


def test_pc_mask_filename_constant() -> None:
    assert PC_MASK_FILENAME == "pc_mask.hex"


# ---------------------------------------------------------------------------
# generate_fault_masks: FaultType.PORT_COUPLING
# ---------------------------------------------------------------------------


def test_port_coupling_zero_faults_returns_all_zero_masks() -> None:
    mask1, mask2 = generate_fault_masks(
        addr_width=4, data_width=8, fault_type=FaultType.PORT_COUPLING, faults=0
    )
    depth = 1 << 4
    assert mask1 == [0] * depth
    assert mask2 == [0] * depth
    # Mirrors the transition-fault convention: same mask object contents
    # returned for both mask1/mask2.
    assert mask1 == mask2


def test_port_coupling_masks_are_same_list_by_value() -> None:
    """Same convention as transition faults: mask1/mask2 carry identical
    fault-bit content (one logical mask, returned twice)."""
    mask1, mask2 = generate_fault_masks(
        addr_width=5, data_width=8, fault_type=FaultType.PORT_COUPLING, faults=10, seed=1
    )
    assert mask1 == mask2


def test_port_coupling_faults_sets_exactly_n_bits() -> None:
    addr_width = 6
    data_width = 8
    faults = 12
    mask1, _ = generate_fault_masks(
        addr_width=addr_width,
        data_width=data_width,
        fault_type=FaultType.PORT_COUPLING,
        faults=faults,
        seed=42,
    )
    total_bits_set = sum(bin(word).count("1") for word in mask1)
    assert total_bits_set == faults


def test_port_coupling_deterministic_with_seed() -> None:
    mask_a, _ = generate_fault_masks(
        addr_width=6, data_width=8, fault_type=FaultType.PORT_COUPLING, faults=8, seed=7
    )
    mask_b, _ = generate_fault_masks(
        addr_width=6, data_width=8, fault_type=FaultType.PORT_COUPLING, faults=8, seed=7
    )
    assert mask_a == mask_b


def test_port_coupling_different_seeds_differ() -> None:
    mask_a, _ = generate_fault_masks(
        addr_width=6, data_width=8, fault_type=FaultType.PORT_COUPLING, faults=8, seed=1
    )
    mask_b, _ = generate_fault_masks(
        addr_width=6, data_width=8, fault_type=FaultType.PORT_COUPLING, faults=8, seed=2
    )
    assert mask_a != mask_b


def test_port_coupling_faults_exceeding_capacity_raises() -> None:
    addr_width = 2
    data_width = 2
    max_sites = (1 << addr_width) * data_width  # 8
    with pytest.raises(ValueError, match="exceeds available bit locations"):
        generate_fault_masks(
            addr_width=addr_width,
            data_width=data_width,
            fault_type=FaultType.PORT_COUPLING,
            faults=max_sites + 1,
        )


def test_port_coupling_full_capacity_is_allowed() -> None:
    addr_width = 2
    data_width = 2
    max_sites = (1 << addr_width) * data_width
    mask1, _ = generate_fault_masks(
        addr_width=addr_width,
        data_width=data_width,
        fault_type=FaultType.PORT_COUPLING,
        faults=max_sites,
        seed=3,
    )
    total_bits_set = sum(bin(word).count("1") for word in mask1)
    assert total_bits_set == max_sites


# ---------------------------------------------------------------------------
# write_fault_files: FaultType.PORT_COUPLING
# ---------------------------------------------------------------------------


def test_write_fault_files_port_coupling_writes_single_file(tmp_path: Path) -> None:
    mask1, mask2 = generate_fault_masks(
        addr_width=4, data_width=8, fault_type=FaultType.PORT_COUPLING, faults=3, seed=5
    )
    file1_path, file2_path = write_fault_files(
        outdir=tmp_path,
        data_width=8,
        fault_type=FaultType.PORT_COUPLING,
        mask1_words=mask1,
        mask2_words=mask2,
    )

    assert file1_path == tmp_path / PC_MASK_FILENAME
    assert file2_path == tmp_path / PC_MASK_FILENAME
    assert file1_path.exists()

    lines = file1_path.read_text(encoding="ascii").splitlines()
    depth = 1 << 4
    assert len(lines) == depth
    # 2-hex-digit words for an 8-bit data width.
    assert all(len(line) == 2 for line in lines)


def test_write_fault_files_port_coupling_hex_content_matches_mask(tmp_path: Path) -> None:
    depth = 1 << 3
    mask1 = [0] * depth
    mask1[2] = 0b101
    file1_path, _ = write_fault_files(
        outdir=tmp_path,
        data_width=8,
        fault_type=FaultType.PORT_COUPLING,
        mask1_words=mask1,
        mask2_words=mask1,
    )
    lines = file1_path.read_text(encoding="ascii").splitlines()
    assert lines[2] == "05"
    assert all(line == "00" for i, line in enumerate(lines) if i != 2)


def test_write_fault_files_port_coupling_does_not_write_stuck_at_files(tmp_path: Path) -> None:
    """Port-coupling must not accidentally create sa0/sa1 fault files."""
    mask1, _ = generate_fault_masks(
        addr_width=4, data_width=8, fault_type=FaultType.PORT_COUPLING, faults=2, seed=1
    )
    write_fault_files(
        outdir=tmp_path,
        data_width=8,
        fault_type=FaultType.PORT_COUPLING,
        mask1_words=mask1,
        mask2_words=mask1,
    )
    assert not (tmp_path / SA0_FILENAME).exists()
    assert not (tmp_path / SA1_FILENAME).exists()


# ---------------------------------------------------------------------------
# generate_fault_files: end-to-end (mask generation + file writing)
# ---------------------------------------------------------------------------


def test_generate_fault_files_port_coupling_end_to_end(tmp_path: Path) -> None:
    file1_path, file2_path = generate_fault_files(
        outdir=tmp_path,
        addr_width=5,
        data_width=16,
        fault_type=FaultType.PORT_COUPLING,
        faults=6,
        seed=99,
    )

    assert file1_path == file2_path == tmp_path / PC_MASK_FILENAME
    assert file1_path.exists()

    lines = file1_path.read_text(encoding="ascii").splitlines()
    depth = 1 << 5
    assert len(lines) == depth
    # 4-hex-digit words for a 16-bit data width.
    assert all(len(line) == 4 for line in lines)

    total_bits_set = sum(bin(int(line, 16)).count("1") for line in lines)
    assert total_bits_set == 6


def test_generate_fault_files_port_coupling_zero_faults_all_zero_mask(tmp_path: Path) -> None:
    file1_path, _ = generate_fault_files(
        outdir=tmp_path,
        addr_width=4,
        data_width=8,
        fault_type=FaultType.PORT_COUPLING,
        faults=0,
    )
    lines = file1_path.read_text(encoding="ascii").splitlines()
    assert all(line == "00" for line in lines)


def test_generate_fault_files_port_coupling_creates_outdir(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "faults"
    assert not nested.exists()
    generate_fault_files(
        outdir=nested,
        addr_width=3,
        data_width=8,
        fault_type=FaultType.PORT_COUPLING,
        faults=1,
        seed=1,
    )
    assert nested.exists()
    assert (nested / PC_MASK_FILENAME).exists()
