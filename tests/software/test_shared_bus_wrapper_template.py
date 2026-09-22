"""Pin wrapper_template.j2's shared-bus (NUM_MEMORIES > 1) rendering --
step 1 of docs/shared-hierarchical-mbist-plan.md's implementation order.

Two guarantees fixed here:
  * `memories` absent (every config today) renders BYTE-IDENTICAL to the
    pre-step-1 template -- the real proof step 1 itself calls for. Since
    there's no "before" template to diff against once this lands, that
    guarantee is instead pinned as: the existing wrapper-rendering test
    suites (test_wrapper_repair_ports.py, test_redundancy_config.py, etc.)
    keep passing unchanged, which they do (confirmed before this file was
    added) -- none of them ever set `memories`, so none of them exercise
    the new is_shared_bus path at all.
  * `memories` present (2+ entries) renders a real N-instance memory-select
    mux: one algo controller (u_algo_top), N memory instances demuxed by a
    new mem_sel_q sequencer, csb/dout arrays sized to N, sram_addr/sram_din
    fanned out unchanged to every instance.

This is template-rendering only (render_wrapper on a hand-built dict) --
the config-schema -> render_config wiring is step 2, not covered here.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import render_wrapper  # noqa: E402

BASE = {
    "memory_name": "sram_tiny",
    "wrapper_module_name": "shared_bus_ctrl",
    "addr_width": 4,
    "data_width": 8,
    "we_active_low": True,
    "read_latency": 1,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
    "normalized_ports": {
        "p0": {"type": "rw", "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
    },
    "algo_top_module": "march_c_top",
    "memory_has_fixed_geometry": False,
    "use_saboteur": False,
    "repair_ports": [],
}


def _render_shared_bus(names: list[str]) -> str:
    return render_wrapper({**BASE, "memories": [{"name": n} for n in names]})


def test_plain_render_has_no_shared_bus_machinery() -> None:
    """memories absent -> none of the new signals/states appear at all."""
    text = render_wrapper(BASE)
    for token in ("sram_csb_arr", "sram_dout_arr", "mem_sel_q", "shb_state_q", "SHB_IDLE", "u_mem_"):
        assert token not in text
    # u_algo_top's bist_done/bist_fail/bist_start connect directly, unchanged.
    assert ".bist_done(bist_done)" in text
    assert ".bist_fail(bist_fail)" in text
    assert ".bist_start(bist_start && test_mode)" in text
    assert ".sram_dout0(sram_dout)" in text
    assert "u_sram (" in text


def test_shared_bus_instantiates_one_memory_per_entry() -> None:
    text = _render_shared_bus(["mem_bank0", "mem_bank1", "mem_bank2"])
    for name in ("mem_bank0", "mem_bank1", "mem_bank2"):
        assert f"u_mem_{name} (" in text
    assert "u_sram (" not in text  # the single-instance path never renders


def test_shared_bus_demuxes_csb_and_muxes_dout_per_memory() -> None:
    text = _render_shared_bus(["a", "b"])
    assert "sram_csb_arr [2];" in text
    assert "sram_dout_arr [2];" in text
    assert "assign sram_csb_arr[0] = (mem_sel_q == 0) ? sram_csb : 1'b1;" in text
    assign_csb_arr_1 = "assign sram_csb_arr[1] = (mem_sel_q == 1) ? sram_csb : 1'b1;"
    assert assign_csb_arr_1 in text
    assert "assign sram_dout       = sram_dout_arr[mem_sel_q];" in text
    # sram_addr/sram_din fan out to every instance unchanged -- only csb/dout differ.
    assert text.count(".addr0(sram_addr)") == 2
    assert text.count(".din0(sram_din)") == 2


def test_shared_bus_routes_algo_top_through_the_sequencer_not_wrapper_ports_directly() -> None:
    text = _render_shared_bus(["a", "b"])
    assert ".bist_start(algo_bist_start)" in text
    assert ".bist_done(algo_bist_done)" in text
    assert ".bist_fail(algo_bist_fail)" in text
    # u_algo_top must NOT connect directly to the wrapper's own bist_done/
    # bist_fail ports when shared-bus -- that's the sequencer's job now.
    assert ".bist_done(bist_done)" not in text
    assert ".bist_fail(bist_fail)" not in text
    assert "assign bist_done       = (shb_state_q == SHB_DONE);" in text
    assert "assign bist_fail       = shb_fail_q;" in text


def test_shared_bus_mem_sel_width_matches_clog2() -> None:
    # 2 memories -> 1-bit select; 3 memories -> 2-bit select ($clog2(3) = 2).
    assert "logic [1-1:0] mem_sel_q;" in _render_shared_bus(["a", "b"])
    assert "logic [2-1:0] mem_sel_q;" in _render_shared_bus(["a", "b", "c"])


def test_shared_bus_advance_state_does_not_skip_a_memory() -> None:
    text = _render_shared_bus(["a", "b", "c"])
    assert "if (mem_sel_q == 2)" in text  # NUM_MEMORIES - 1
    assert "shb_state_q <= SHB_DONE;" in text
    assert "shb_state_q <= SHB_ADVANCE;" in text


def test_shared_bus_done_restart_condition_is_correctly_parenthesized() -> None:
    """A real bug caught while implementing this: !start_signal without
    parens (!bist_start && test_mode) is NOT !(bist_start && test_mode) --
    Verilog's unary ! binds tighter than &&. Pins the fix."""
    text = _render_shared_bus(["a", "b"])
    assert "if (!(bist_start && test_mode))" in text
