"""End-to-end proof of on-chip 2D (row + column) self-repair: no tester, one
`self_repair_start` level, the chip detects its own failing bits during its
own march pass, computes a repair via onchip_2d_repair_analyzer (row spares
AND column spares, not row-only), applies it, and (except in the deliberately
disclosed-gap case) re-verifies via a second march pass -- all in silicon.

Mirrors test_onchip_selfrepair_e2e.py's exact structure (same _generate/_run
shape, same four-algo parametrization), extended with
redundancy.onchip_col_repair: true and NUM_SPARE_COLS > 0. Contrast with
test_repair_2d_e2e.py (Workstream M.1): there, Python drives
repair.bira.analyze()/encode_repair() between two separate simulator
invocations and pokes the resulting signature onto repair_ports pins; here
there are no repair_ports at all -- onchip_2d_repair_analyzer computes AND
applies both the row and column signature autonomously, inside one
simulation run. Icarus + make gated.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("make") is None,
    reason="needs Icarus Verilog + make on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.bira_input import fail_cells  # noqa: E402
from autombist.generator import generate_from_config  # noqa: E402
from autombist.runner import run_simulation  # noqa: E402

COL_PORTS = {
    "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0",
    "we": "web0", "csb": "csb0", "spare_wen": "spare_wen0",
}

# Self-repair-capable single-port algos with column repair wired up
# (generator.py's _COL_SELFREPAIR_ALGOS) -- march-1r1w/march-2rw are deferred,
# see rtl/onchip_2d_repair_analyzer.sv and generator.py's comment.
COL_SELFREPAIR_ALGOS = ["march-c", "march-raw", "march-x", "mats-plus", "checkerboard"]

# gap_demo's residual-fail set depends on march visitation order -- see
# sram_spares_col_gap_demo.v's header and test_onchip_col_repair.py's
# GAP_DEMO_VALID_RESIDUALS for the full case analysis.
GAP_DEMO_VALID_RESIDUALS = [
    {(5, 0), (5, 1)},
    {(9, 2)},
    {(20, 2)},
    {(30, 5)},
]


def _config(
    memory_name: str,
    wrapper_module_name: str,
    *,
    addr_width: int,
    data_width: int,
    num_spare_rows: int,
    num_spare_cols: int,
) -> dict:
    return {
        "memory_name": memory_name,
        "wrapper_module_name": wrapper_module_name,
        "addr_width": addr_width,
        "data_width": data_width,
        "we_active_low": True,
        "ports": COL_PORTS,
        # NO repair_ports: -- mutually exclusive with onchip_selfrepair, the
        # analyzer/sequencer drive both remaps directly.
        "redundancy": {
            "num_spare_rows": num_spare_rows,
            "num_spare_cols": num_spare_cols,
            "onchip_selfrepair": True,
            "onchip_col_repair": True,
        },
    }


def _generate(tmp_path: Path, config: dict, subdir: str, algo: str = "march-c") -> Path:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / subdir, algo=algo)


def _run(wrapper: Path, scenario: str, *, addr_width: int, data_width: int):
    return run_simulation(
        wrapper.parent,
        extra_make_vars={
            "COCOTB_TEST_MODULES": "test_onchip_col_repair",
            "COL_REPAIR_SCENARIO": scenario,
            "ADDR_WIDTH": str(addr_width),
            "DATA_WIDTH": str(data_width),
        },
    )


@pytest.mark.parametrize("algo", COL_SELFREPAIR_ALGOS)
def test_recurring_bit_is_promoted_to_a_column_repair(tmp_path: Path, algo: str) -> None:
    """The basic "it works" case: two rows sharing one failing bit, 1 spare
    row + 1 spare col. The first-seen row consumes the row spare; the
    recurrence is promoted to the column spare -- matches
    sram_spares_col_tiny.v's existing tester-driven (Workstream M.1) defect
    pair, now driven autonomously on-chip instead."""
    wrapper = _generate(
        tmp_path,
        _config("sram_spares_col_tiny", "sram_spares_col_tiny_mbist", addr_width=2, data_width=4,
                 num_spare_rows=1, num_spare_cols=1),
        "promote",
        algo=algo,
    )
    result = _run(wrapper, "promote", addr_width=2, data_width=4)
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


@pytest.mark.parametrize("algo", COL_SELFREPAIR_ALGOS)
def test_retrigger_gives_the_same_result_both_times(tmp_path: Path, algo: str) -> None:
    """Running the autonomous sequence twice in one simulation (no reset in
    between) must reach the SAME verdict both times -- the analyzer's
    accumulated seen_once/live_row/live_col state must correctly PERSIST
    across the re-trigger, not just be correct by accident of a single fresh
    reset. Same DUT/geometry as the promote case."""
    wrapper = _generate(
        tmp_path,
        _config("sram_spares_col_tiny", "sram_spares_col_tiny_mbist", addr_width=2, data_width=4,
                 num_spare_rows=1, num_spare_cols=1),
        "retrigger",
        algo=algo,
    )
    result = _run(wrapper, "retrigger", addr_width=2, data_width=4)
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


@pytest.mark.parametrize("algo", COL_SELFREPAIR_ALGOS)
def test_column_contention_falls_back_to_a_row_claim(tmp_path: Path, algo: str) -> None:
    """Finding-2 regression: a recurring bit that finds its one spare column
    already claimed by an earlier, unrelated recurrence must fall back to
    wanting a row claim, not go straight to unrepairable. 4 defects (two
    same-bit row pairs), 3 spare rows + 1 spare col -- fully repairable
    regardless of which pair's recurrence wins the column race."""
    wrapper = _generate(
        tmp_path,
        _config("sram_spares_col_contention", "sram_spares_col_contention_mbist", addr_width=2, data_width=4,
                 num_spare_rows=3, num_spare_cols=1),
        "col_contention",
        algo=algo,
    )
    result = _run(wrapper, "col_contention", addr_width=2, data_width=4)
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


@pytest.mark.parametrize("algo", COL_SELFREPAIR_ALGOS)
def test_simultaneous_bit_failures_do_not_race_for_a_column_slot(tmp_path: Path, algo: str) -> None:
    """Finding-1 regression: two bits failing in the identical cycle, both
    already seen_once, must not race for the same column slot from a stale
    pre-cycle snapshot. 2 rows sharing the same 2 failing bits, 1 spare row +
    2 spare cols -- fully repairable only if both simultaneous claims are
    correctly sequenced, not silently clobbered."""
    wrapper = _generate(
        tmp_path,
        _config("sram_spares_col_simultaneous", "sram_spares_col_simultaneous_mbist", addr_width=2, data_width=4,
                 num_spare_rows=1, num_spare_cols=2),
        "simultaneous",
        algo=algo,
    )
    result = _run(wrapper, "simultaneous_bits", addr_width=2, data_width=4)
    assert result.returncode == 0, result.stdout
    assert fail_cells(result.report) == set()


@pytest.mark.parametrize("algo", COL_SELFREPAIR_ALGOS)
def test_gap_demo_is_the_disclosed_limitation_not_a_false_pass(tmp_path: Path, algo: str) -> None:
    """The disclosed-gap proof: a 5-fault set that repair.bira.analyze() finds
    a COMPLETE repair for (2 spare rows + 1 spare col -- verified directly
    against bira.py, see sram_spares_col_gap_demo.v's header), but the
    single-pass on-chip heuristic cannot fully resolve. self_repair_fail must
    read 1, and the re-scan's residual fails must be exactly one of the
    hand-verified GAP_DEMO_VALID_RESIDUALS -- never empty (that would be an
    undetected false pass) and never anything outside that set (that would be
    an unexplained new failure mode)."""
    wrapper = _generate(
        tmp_path,
        _config("sram_spares_col_gap_demo", "sram_spares_col_gap_demo_mbist", addr_width=5, data_width=8,
                 num_spare_rows=2, num_spare_cols=1),
        "gap_demo",
        algo=algo,
    )
    result = _run(wrapper, "gap_demo", addr_width=5, data_width=8)
    assert result.returncode == 0, result.stdout
    observed = fail_cells(result.report)
    assert observed, "expected a real residual failure (the disclosed gap), found none"
    assert observed in GAP_DEMO_VALID_RESIDUALS, f"unexpected residual fails: {observed}"
