"""Unit + regression tests for the controller sequence-correctness checker
(Phase 1). Pure Python -- no EDA tools; the Verilator-driven end-to-end checks
live in tests/integration/test_fsm_sequence_check.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.alg_spec import (  # noqa: E402
    AccessStep,
    AlgSpecError,
    expand_expected_blocks,
    expand_expected_trace,
    parse_alg,
    resolve_algo,
)
from autombist.algo_engine import parse_result_line  # noqa: E402
from autombist.fsm_harness import render_harness, render_harness_mp  # noqa: E402
from autombist.seq_check import (  # noqa: E402
    ObservedAccess,
    compare_trace,
    parse_observed_trace,
)


# --------------------------------------------------------------------------- #
# expand_expected_trace -- faithful port of march_engine.sv's traversal
# --------------------------------------------------------------------------- #
def _obs_from_expected(steps, data_width: int) -> list[ObservedAccess]:
    """Build the memory-bus observation a *correct* controller would produce."""
    all_ones = (1 << data_width) - 1
    obs = []
    for s in steps:
        din = (all_ones if s.write_value == 1 else 0) if s.is_write else 0
        obs.append(ObservedAccess(port=s.port, is_write=s.is_write, addr=s.addr, din=din))
    return obs


def test_expand_march_c_small_depth() -> None:
    spec = resolve_algo("march_c")  # 10n, 6 elements
    steps = expand_expected_trace(spec, depth=4)
    assert len(steps) == 10 * 4  # 10n over 4 words
    # element 0 is "either w0" -> w0 to addr 0,1,2,3 (up order)
    assert [(s.addr, s.op) for s in steps[:4]] == [(0, 2), (1, 2), (2, 2), (3, 2)]
    # element 1 is "up r0 w1" -> at addr 0: r0 then w1
    assert (steps[4].addr, steps[4].op, steps[5].addr, steps[5].op) == (0, 0, 0, 3)


def test_expand_direction_down_walks_high_to_low() -> None:
    spec = parse_alg("down r0 w1", "d")
    steps = expand_expected_trace(spec, depth=3)
    assert [s.addr for s in steps] == [2, 2, 1, 1, 0, 0]


def test_expand_direction_either_treated_as_up() -> None:
    """A LEADING either has no previous element, so the canonical rule
    (resolve_directions) defaults it to up -- same result the old always-up
    rule gave for this specific (default) case."""
    spec = parse_alg("either w0", "e")
    steps = expand_expected_trace(spec, depth=3)
    assert [s.addr for s in steps] == [0, 1, 2]


def test_expand_direction_trailing_either_inherits_the_previous_direction() -> None:
    """The case the old rule got wrong: a TRAILING either after a `down`
    element must inherit `down`, not reset to `up`. This is exactly the
    march_x/march_c/march_ss divergence alg_spec.resolve_directions unified
    away -- expand_expected_trace now agrees with what the compiled engine
    (and the hand-written classic RTL) actually run."""
    spec = parse_alg("down w0\neither r0\n", "t")
    steps = expand_expected_trace(spec, depth=3)
    down_addrs = [s.addr for s in steps[:3]]     # element 0: down w0
    either_addrs = [s.addr for s in steps[3:]]   # element 1: either r0, inherits down
    assert down_addrs == [2, 1, 0]
    assert either_addrs == [2, 1, 0]


def test_expand_carries_port_suffix() -> None:
    spec = parse_alg("up r0.1 w1.0", "p")
    steps = expand_expected_trace(spec, depth=2)
    # ports parallel the ops: r0 on port 1, w1 on port 0, at each address
    assert [(s.addr, s.op, s.port) for s in steps] == [
        (0, 0, 1), (0, 3, 0), (1, 0, 1), (1, 3, 0),
    ]


def test_expand_rejects_nonpositive_depth() -> None:
    spec = resolve_algo("march_c")
    with pytest.raises(AlgSpecError):
        expand_expected_trace(spec, depth=0)


# --------------------------------------------------------------------------- #
# expand_expected_blocks -- orderings[0] is always the canonical direction
# --------------------------------------------------------------------------- #
def test_expected_blocks_orderings_are_derived_from_the_canonical_rule() -> None:
    """orderings[0] must be the canonical direction (resolve_directions), not
    a hardcoded ascending -- this is what lets a divergence message report the
    right "expected" side (seq_check.py's `canon = block.orderings[0]`), and
    is exactly what keeps this leniency a documented RELAXATION of the
    canonical rule rather than a second rule that can silently disagree with
    it, the way the engine and the classic RTL used to disagree on march_x."""
    spec = parse_alg("down w0\neither r0\n", "t")
    blocks = expand_expected_blocks(spec, depth=3)
    trailing = blocks[1]
    assert len(trailing.orderings) == 2
    canonical_addrs = [s.addr for s in trailing.orderings[0]]
    other_addrs = [s.addr for s in trailing.orderings[1]]
    assert canonical_addrs == [2, 1, 0]  # inherits element 0's `down`
    assert other_addrs == [0, 1, 2]


# --------------------------------------------------------------------------- #
# parse_observed_trace + compare_trace
# --------------------------------------------------------------------------- #
def test_parse_observed_trace_extracts_ordered_access_lines() -> None:
    text = (
        "ACCESS port=0 we=1 addr=3 din=ff\n"
        "some other simulator noise\n"
        "ACCESS port=0 we=0 addr=3 din=0\n"
        "RESULT ESCAPED alg=FSM\n"
    )
    trace = parse_observed_trace(text)
    assert trace == [
        ObservedAccess(port=0, is_write=True, addr=3, din=0xFF),
        ObservedAccess(port=0, is_write=False, addr=3, din=0),
    ]


def test_compare_self_match_is_ok() -> None:
    spec = resolve_algo("march_ss")  # 22n -- the most complex built-in
    steps = expand_expected_trace(spec, depth=4)
    blocks = expand_expected_blocks(spec, depth=4)
    obs = _obs_from_expected(steps, data_width=8)
    result = compare_trace(blocks, obs, data_width=8)
    assert result.matches is True
    assert result.observed_count == result.expected_count == len(steps)
    assert result.divergences == []


def test_compare_either_element_run_in_reverse_still_matches() -> None:
    """The key correctness property found in e2e: an ``either`` element's address
    order is free, so a controller that runs it DOWN must match a spec that
    expands it up (this is exactly what march_c_fsm does for its final 'either
    r0' element). Regression against over-strict address-order checking."""
    spec = parse_alg("either r0", "e")
    blocks = expand_expected_blocks(spec, depth=4)
    # controller reads addresses high->low (descending) -- valid for 'either'
    obs = [ObservedAccess(port=0, is_write=False, addr=a, din=0) for a in (3, 2, 1, 0)]
    assert compare_trace(blocks, obs, data_width=8).matches is True
    # ascending is equally valid
    obs_up = [ObservedAccess(port=0, is_write=False, addr=a, din=0) for a in (0, 1, 2, 3)]
    assert compare_trace(blocks, obs_up, data_width=8).matches is True
    # but a genuinely wrong (non-monotonic) order is still rejected
    obs_bad = [ObservedAccess(port=0, is_write=False, addr=a, din=0) for a in (0, 2, 1, 3)]
    assert compare_trace(blocks, obs_bad, data_width=8).matches is False


def test_compare_fixed_direction_element_wrong_order_diverges() -> None:
    """A fixed 'up'/'down' element does NOT get the either freedom: running it
    the wrong way is a real divergence."""
    spec = parse_alg("up r0", "u")
    blocks = expand_expected_blocks(spec, depth=4)
    obs = [ObservedAccess(port=0, is_write=False, addr=a, din=0) for a in (3, 2, 1, 0)]  # down
    assert compare_trace(blocks, obs, data_width=8).matches is False


def test_compare_dropped_op_diverges() -> None:
    spec = resolve_algo("march_c")
    steps = expand_expected_trace(spec, depth=4)
    blocks = expand_expected_blocks(spec, depth=4)
    obs = _obs_from_expected(steps, data_width=8)
    bad = obs[:20] + obs[21:]  # controller skipped one op
    result = compare_trace(blocks, bad, data_width=8)
    assert result.matches is False
    assert result.divergences


def test_compare_wrong_address_diverges() -> None:
    spec = resolve_algo("march_c")
    steps = expand_expected_trace(spec, depth=4)
    blocks = expand_expected_blocks(spec, depth=4)
    obs = _obs_from_expected(steps, data_width=8)
    obs[6] = ObservedAccess(port=0, is_write=obs[6].is_write, addr=obs[6].addr ^ 1, din=obs[6].din)
    result = compare_trace(blocks, obs, data_width=8)
    assert result.matches is False
    assert result.divergences[0].index == 6


def test_compare_wrong_write_value_diverges() -> None:
    # A controller that writes solid-0 where the spec says w1 (solid-1).
    spec = parse_alg("up w1", "w")
    steps = expand_expected_trace(spec, depth=2)
    blocks = expand_expected_blocks(spec, depth=2)
    obs = [ObservedAccess(port=0, is_write=True, addr=s.addr, din=0) for s in steps]  # all w0
    result = compare_trace(blocks, obs, data_width=8)
    assert result.matches is False
    assert result.divergences[0].index == 0


def test_compare_does_not_check_read_expected_value() -> None:
    # r0 vs r1 is NOT observable on the memory bus; both are just "read". A
    # controller reading the right address is a match regardless of r0/r1 -- the
    # read *value* is validated separately by the golden pass having to pass.
    spec = parse_alg("up r0", "r0")
    steps = expand_expected_trace(spec, depth=2)
    blocks = expand_expected_blocks(spec, depth=2)
    obs = [ObservedAccess(port=0, is_write=False, addr=s.addr, din=0) for s in steps]
    assert compare_trace(blocks, obs, data_width=8).matches is True


def test_compare_multiport_per_port_multiset() -> None:
    # Two ports, interleaved (possibly concurrent) emission; each port's multiset
    # of accesses must match its expected multiset (order not asserted across
    # concurrent ports -- see seq_check module docstring).
    spec = parse_alg("up r0.0 r1.1", "mp")  # per addr: read on port0, read on port1
    blocks = expand_expected_blocks(spec, depth=2)
    obs = [
        ObservedAccess(port=1, is_write=False, addr=0, din=0),  # port1 first -- order-free
        ObservedAccess(port=0, is_write=False, addr=0, din=0),
        ObservedAccess(port=0, is_write=False, addr=1, din=0),
        ObservedAccess(port=1, is_write=False, addr=1, din=0),
    ]
    assert compare_trace(blocks, obs, data_width=8, num_ports=2).matches is True
    # Corrupt only port 1's second address -> a port-1 divergence, port 0 clean.
    obs[3] = ObservedAccess(port=1, is_write=False, addr=0, din=0)
    bad = compare_trace(blocks, obs, data_width=8, num_ports=2)
    assert bad.matches is False
    assert all(d.port == 1 for d in bad.divergences)


# --------------------------------------------------------------------------- #
# Regression guards (Phase-1 plan)
# --------------------------------------------------------------------------- #
def test_parse_result_line_ignores_interleaved_access_lines() -> None:
    """The new ACCESS observer lines must never be mis-parsed as the RESULT
    line the campaign keys detection off."""
    escaped = "ACCESS port=0 we=1 addr=0 din=0\nRESULT ESCAPED alg=FSM\n"
    assert parse_result_line(escaped) == (False, None, None, None, None)
    detected = "ACCESS port=0 we=0 addr=5 din=0\nRESULT DETECTED alg=FSM elem=0 op=0 addr=0 xor=0\n"
    assert parse_result_line(detected)[0] is True


def test_harness_render_default_has_no_observer() -> None:
    """Opt-in isolation: the default render (emit_seq_trace off) contains no
    sequence-trace machinery, so a normal FSM campaign is byte-identical."""
    for text in (
        render_harness(addr_width=8, data_width=8, fsm_module_name="march_c_top"),
        render_harness_mp(addr_width=8, data_width=8, fsm_module_name="march_2rw_top"),
    ):
        assert "SEQ_TRACE" not in text
        assert "ACCESS port=" not in text
        assert "seq_trace" not in text


def test_harness_render_seq_trace_adds_observer_only() -> None:
    """With emit_seq_trace on, the ONLY additions are the observer block; the
    rest of the harness is unchanged (the observer render is a superset)."""
    off = render_harness(addr_width=8, data_width=8, fsm_module_name="march_c_top")
    on = render_harness(addr_width=8, data_width=8, fsm_module_name="march_c_top", emit_seq_trace=True)
    assert "SEQ_TRACE" in on and 'ACCESS port=0 we=%0d' in on
    # Every line of the default render still appears in the observer render.
    assert set(off.splitlines()).issubset(set(on.splitlines()))
    # Multi-port observer emits both ports.
    on_mp = render_harness_mp(addr_width=8, data_width=8, fsm_module_name="march_2rw_top", emit_seq_trace=True)
    assert "ACCESS port=0 we=%0d" in on_mp and "ACCESS port=1 we=%0d" in on_mp
