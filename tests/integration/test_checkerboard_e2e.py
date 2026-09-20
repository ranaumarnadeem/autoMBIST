"""Verilator-gated integration tests for the checkerboard march algorithm
(logical/address-LSB-parity checkerboard -- see alg_spec.py's module
docstring and src/autombist/algos/checkerboard.alg for the design).

The stuck-at attribution points below (elem/op/addr) are HAND-DERIVED, not
just asserted, by tracing the real 4-element sequence against depth=4
addresses -- see checkerboard.alg's own header for the sequence:

    either wc          # elem 0: mem <- [0,1,0,1]  (wc(a) = a & 1)
    up   rc wcb         # elem 1: verify wc, write wcb -> mem <- [1,0,1,0]
    down rcb wc          # elem 2: verify wcb, write wc -> mem <- [0,1,0,1]
    either rc            # elem 3 (inherits 'down'): verify wc

SA0@addr=1 (odd, wc(1)=1): the stuck-at-0 value conflicts with what elem 0
just wrote there, so the very first read-check (elem 1's "rc", the first op
in that element) already diverges -- DETECTED immediately.

SA0@addr=0 (even, wc(0)=0): the stuck-at-0 value happens to COINCIDE with
what elem 0 writes there, so elem 1's "rc" at addr=0 sees the (accidentally)
correct value and elem 1's "wcb" write is silently defeated by the stuck-at
(mem[0] stays 0 instead of becoming wcb(0)=1). Detection is deferred to elem
2's "rcb" (its first op), which expects wcb(0)=1 but observes the still-stuck
0 -- the same "a stuck value can coincidentally match the first check" case
every march algorithm has for SA0/SA1, exercised here explicitly rather than
left as a claim.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

from autombist.alg_spec import find_engine_dir, resolve_algo  # noqa: E402
from autombist.algo_engine import (  # noqa: E402
    FaultRecord,
    MemoryParams,
    compile_engine,
    load_fault_list,
    parse_result_line,
    run_algo_campaign,
    run_one,
)
from autombist.fault_primitives import default_registry  # noqa: E402
from autombist.fault_ram_gen import render_and_write  # noqa: E402


def _mem() -> MemoryParams:
    return MemoryParams(addr_width=2, data_width=8)  # depth=4, matches the hand-trace above


def test_checkerboard_golden_run_is_clean(tmp_path: Path) -> None:
    """A fault-free campaign must not raise: run_algo_campaign's golden pass
    raises CampaignError (not a golden_clean=False return) the moment it sees
    an unexpected DETECTED, so a bare successful return here IS the proof of
    soundness -- see algo_engine._run_campaign_against_artifact."""
    result = run_algo_campaign(_mem(), resolve_algo("checkerboard"), [], workdir=tmp_path)
    assert result.golden_clean is True
    assert result.total == 0


@pytest.mark.parametrize(
    "fault_type,vaddr,expected_elem",
    [
        ("SA0", 1, 1),  # odd address: stuck value conflicts immediately
        ("SA0", 0, 2),  # even address: stuck value coincides once, deferred
        ("SA1", 0, 1),  # SA1 mirrors SA0 with parity swapped
        ("SA1", 1, 2),
    ],
)
def test_checkerboard_detects_stuck_at_both_parities(
    tmp_path: Path, fault_type: str, vaddr: int, expected_elem: int
) -> None:
    """Pinned elem/op/addr attribution, not just detected=True -- proves the
    hand-derived trace in this file's own module docstring is what the real
    engine actually does, including the deferred-detection case."""
    fault = FaultRecord(fault_type, vaddr=vaddr, vbit=0)
    result = run_algo_campaign(_mem(), resolve_algo("checkerboard"), [fault], workdir=tmp_path)

    assert result.golden_clean is True
    r = result.faults[0]
    assert r.detected is True, f"{fault_type}@addr={vaddr} escaped -- expected detection at elem={expected_elem}"
    assert r.elem == expected_elem
    assert r.op == 0  # the first op in the detecting element (rc or rcb)
    assert r.addr == vaddr


def test_checkerboard_mp_engine_one_port_sanity(tmp_path: Path) -> None:
    """One small run through march_engine_mp.sv (mirrors
    test_march_engine_mp_sanity.py's own 1-port-sanity convention) -- proves
    the SEPARATE mirrored case-arm fix in the mp engine file, not just
    march_engine.sv. Every op stays on port 0 (checkerboard.alg carries no
    .PORT suffix), so this is the "plain", non-extended .algc format."""
    engine_dir = find_engine_dir()
    march_mp_sv = engine_dir / "march_engine_mp.sv"
    fault_ram_sv = render_and_write(default_registry(), tmp_path / "fault_ram.sv", num_ports=2)

    spec = resolve_algo("checkerboard")
    mem = _mem()
    artifact = compile_engine(
        mem, sources=[fault_ram_sv, march_mp_sv], top_module="march_engine_mp", workdir=tmp_path / "work",
    )
    alg_file = spec.write_numeric(tmp_path / "work" / f"{spec.name}.algc")
    plusargs = [f"+INIT={mem.init_val}"]

    golden_out = run_one(artifact, alg_file=alg_file, extra_plusargs=plusargs)
    golden_detected, *_ = parse_result_line(golden_out)
    assert golden_detected is False, f"golden run unexpectedly DETECTED:\n{golden_out}"


def test_checkerboard_coverage_against_faults_example(tmp_path: Path) -> None:
    """Measured coverage against faults.example.txt, matching every existing
    built-in's convention of stamping a real number into its own header
    comment (see checkerboard.alg). Not pinned to a specific count here --
    the number belongs in the .alg file's header, hand-copied after reading
    this test's own output once; this test just proves the campaign runs
    clean end-to-end against the full 41-fault reference list. addr_width=8/
    data_width=8 matches test_fault_ram_gen_e2e.py's own reference-table
    dimensions -- faults.example.txt's addresses/bits need this size, unlike
    the depth=4 memory the hand-derived attribution tests above use."""
    engine_dir = find_engine_dir()
    faults = load_fault_list(engine_dir / "faults.example.txt")
    mem = MemoryParams(addr_width=8, data_width=8, init_val=1)
    result = run_algo_campaign(mem, resolve_algo("checkerboard"), faults, workdir=tmp_path)
    assert result.golden_clean is True
    assert result.total == len(faults)
    print(f"checkerboard coverage: {result.detected}/{result.total}")
