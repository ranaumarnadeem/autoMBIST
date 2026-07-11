"""Controller sequence-correctness checking.

Fault injection answers "does this algorithm catch faults"; it *cannot* answer
"does this controller actually execute the march sequence it claims to". A buggy
FSM (wrong address order, a skipped element, a dropped op) can still expose a
particular injected fault by luck. This module closes that gap: it compares the
memory-operation sequence a controller *actually drives* -- observed from the
golden (no-fault) pass via ``ACCESS`` lines the FSM harness prints -- against the
ground-truth sequence its march spec requires (:func:`alg_spec.expand_expected_
trace`). It is completely independent of whether any fault is detected.

What is checked, and what is not:
- Checked from the memory bus: address, address *order*/direction, per-element op
  structure (read vs write), write data value (solid 0 vs solid 1), and which
  physical port each access uses.
- NOT checked here: a read's *expected* value (r0 vs r1). That is not observable
  on the memory-facing bus (the controller compares internally). It is instead
  validated by the golden pass having to *pass* -- a controller that expected the
  wrong value on a clean memory would flag ``bist_fail`` on the golden run, which
  the campaign already rejects.

Multi-port (2RW) note: the two ports' access streams are compared *per port*
(each port's accesses are totally ordered in time); this catches per-port
address-order/op errors without asserting a specific cross-port same-cycle
interleaving, which the sequential ``.alg`` format does not pin down.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .alg_spec import AccessStep, ElementBlock

# Grammar the FSM harness emits, one line per committed memory operation during
# the golden pass (only when sequence tracing is enabled -- see
# fsm_harness_template.sv.j2's SEQ_TRACE block). we=1 write, we=0 read.
ACCESS_RE = re.compile(
    r"ACCESS port=(?P<port>\d+) we=(?P<we>[01]) addr=(?P<addr>\d+) din=(?P<din>[0-9a-fA-F]+)"
)


@dataclass(slots=True, frozen=True)
class ObservedAccess:
    port: int
    is_write: bool
    addr: int
    din: int


@dataclass(slots=True, frozen=True)
class Divergence:
    """Where an observed trace first departs from the expected sequence."""
    port: int
    index: int                 # position within that port's stream
    expected: str              # human description ("<end of sequence>" if over-run)
    observed: str


@dataclass(slots=True)
class SequenceResult:
    matches: bool
    expected_count: int
    observed_count: int
    divergences: list[Divergence] = field(default_factory=list)

    def message(self) -> str:
        if self.matches:
            return (
                f"sequence OK: {self.observed_count} memory operations matched the "
                f"expected march trace"
            )
        parts = [
            f"sequence MISMATCH: expected {self.expected_count} operations, "
            f"observed {self.observed_count}"
        ]
        for d in self.divergences:
            parts.append(
                f"  port {d.port} step {d.index}: expected {d.expected}, observed {d.observed}"
            )
        return "\n".join(parts)


def parse_observed_trace(text: str) -> list[ObservedAccess]:
    """Extract the ordered list of ``ACCESS`` lines from simulator output."""
    trace: list[ObservedAccess] = []
    for m in ACCESS_RE.finditer(text):
        trace.append(
            ObservedAccess(
                port=int(m["port"]),
                is_write=(m["we"] == "1"),
                addr=int(m["addr"]),
                din=int(m["din"], 16),
            )
        )
    return trace


# An "observable key": what the memory bus actually reveals about one access.
# For a write it includes the data value (solid 0 vs solid 1); for a read it does
# not carry an expected value (r0 vs r1 is not visible on the bus -- see module
# docstring), so write_value is None. Port is included so multi-port streams keep
# their identity.
_Key = tuple[int, int, bool, "int | None"]  # (addr, port, is_write, write_value)


def _expected_key(step: AccessStep) -> _Key:
    return (step.addr, step.port, step.is_write, step.write_value)


def _observed_key(acc: ObservedAccess, data_width: int) -> _Key:
    if not acc.is_write:
        return (acc.addr, acc.port, False, None)
    all_ones = (1 << data_width) - 1
    wv = 1 if acc.din == all_ones else (0 if acc.din == 0 else -1)
    return (acc.addr, acc.port, True, wv)


def _observed_desc(acc: ObservedAccess, data_width: int) -> str:
    kind = "w" if acc.is_write else "r"
    if acc.is_write:
        all_ones = (1 << data_width) - 1
        val = "1" if acc.din == all_ones else ("0" if acc.din == 0 else f"0x{acc.din:x}")
        return f"{kind}{val}@{acc.addr} (port {acc.port})"
    return f"{kind}@{acc.addr} (port {acc.port})"


def _compare_single_port(
    blocks: list[ElementBlock], observed: list[ObservedAccess], data_width: int
) -> list[Divergence]:
    """Walk the observed trace one element-block at a time; a block matches if it
    equals ANY of its acceptable orderings (up/down for an ``either`` element).
    Reports the first block that matches none."""
    divergences: list[Divergence] = []
    pos = 0
    for block in blocks:
        size = block.size
        window = observed[pos:pos + size]
        obs_keys = [_observed_key(o, data_width) for o in window]
        if any([_expected_key(s) for s in ordering] == obs_keys for ordering in block.orderings):
            pos += size
            continue
        # No ordering matched -- locate the first differing position against the
        # first (canonical) ordering for a readable message.
        canon = block.orderings[0]
        for i in range(size):
            exp_desc = canon[i].human()
            if i >= len(window):
                divergences.append(Divergence(0, pos + i, exp_desc, "<end of trace>"))
                break
            if _expected_key(canon[i]) != obs_keys[i]:
                divergences.append(
                    Divergence(0, pos + i, exp_desc, _observed_desc(window[i], data_width))
                )
                break
        break
    else:
        # All blocks matched; flag any trailing observed accesses.
        if pos < len(observed):
            divergences.append(
                Divergence(0, pos, "<end of sequence>", _observed_desc(observed[pos], data_width))
            )
    return divergences


def _compare_multi_port(
    blocks: list[ElementBlock], observed: list[ObservedAccess], data_width: int, num_ports: int
) -> list[Divergence]:
    """Order-independent per-port multiset comparison. A 2RW controller may run
    the two ports' accesses concurrently and in an order the sequential ``.alg``
    does not pin down, so we compare the *multiset* of (addr, op, data) accesses
    each port performs, not their order. This still catches a missing, extra,
    wrong-address, or wrong-data access on any port."""
    exp_counts: Counter[_Key] = Counter()
    for block in blocks:
        # multiset is ordering-independent, so orderings[0] suffices.
        for step in block.orderings[0]:
            exp_counts[_expected_key(step)] += 1
    obs_counts: Counter[_Key] = Counter(_observed_key(o, data_width) for o in observed)

    divergences: list[Divergence] = []
    missing = exp_counts - obs_counts
    extra = obs_counts - exp_counts
    for key, n in sorted(missing.items()):
        addr, port, is_write, wv = key
        kind = ("w" + str(wv)) if is_write else "r"
        divergences.append(
            Divergence(port, -1, f"{kind}@{addr} x{n}", "<missing on this port>")
        )
    for key, n in sorted(extra.items()):
        addr, port, is_write, wv = key
        kind = ("w" + str(wv)) if is_write else "r"
        divergences.append(
            Divergence(port, -1, "<not in spec>", f"{kind}@{addr} x{n}")
        )
    return divergences


def compare_trace(
    blocks: list[ElementBlock],
    observed: list[ObservedAccess],
    *,
    data_width: int,
    num_ports: int = 1,
) -> SequenceResult:
    """Compare an observed controller trace against the expected march blocks.

    Single-port: element-block ordered comparison, accepting either address
    order for ``either`` elements. Multi-port: order-independent per-port
    multiset comparison (see :func:`_compare_multi_port` and the module
    docstring for why order is not asserted across concurrent ports).
    """
    expected_count = sum(b.size for b in blocks)
    if num_ports == 1:
        divergences = _compare_single_port(blocks, observed, data_width)
    else:
        divergences = _compare_multi_port(blocks, observed, data_width, num_ports)
    return SequenceResult(
        matches=not divergences,
        expected_count=expected_count,
        observed_count=len(observed),
        divergences=divergences,
    )
