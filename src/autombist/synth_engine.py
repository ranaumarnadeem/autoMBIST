"""Automatic march-test synthesis: a Pattern-Graph greedy-walk generator.

Cites Benso, Bosio, Di Carlo, Di Natale, Prinetto, "Automatic March Tests
Generation for Static and Dynamic Faults in SRAMs," ETS 2005, and its
extension "...for Static Linked Faults in SRAMs," DATE 2006. Given the
current fault-type registry (the 15 DSL-expressible ``FaultPrimitive``
entries -- see ``fault_primitives.py``), synthesizes a new march test (an
ordinary :class:`~autombist.alg_spec.AlgSpec`) guaranteed to detect every
targeted primitive, then hands it straight to ``run_algo_campaign`` for real
Verilator verification.

Why this module exists, not a call into the campaign engine during search:
fault detection in this project is empirical -- ``run_algo_campaign``
compiles ``march_engine.sv``/``fault_ram.sv`` via Verilator and runs one real
simulation per fault. A greedy search that invoked Verilator once per
candidate operation would be far too slow. So this module implements its own
lightweight, pure-Python reference-memory oracle (:func:`replay`/
:func:`detects`) that interprets ``Sensitize``/``Effect`` fields directly,
verified to exactly mirror ``fault_ram_gen.py``'s SystemVerilog codegen
(``render_static_clamp_arm``/``render_write_victim_arm``/
``render_write_aggressor_arm``/``render_read_victim_arm``) -- Verilator is
used only once, at the end, by a caller (``algo_shell.py``'s ``do_synth
--verify``) to confirm the synthesized result for real.

The reference-memory model: a 2-cell abstract pair ``(v, a)`` -- victim and
aggressor. Within one march Element, the address-traversal direction resolves
which role's op-list runs first: ``up`` visits the lower address first, then
the higher; ``down`` visits the higher first, then the lower (see
:func:`_role_order`). Single-cell faults (SA0/SA1/TF/WDF/RDF/IRF/DRDF) never
reference ``a`` at all -- the model is uniform for them regardless of which
physical address is higher.

For coupling faults (``sensitize.on == "aggressor"``: CFIN/CFID/CFST), which
address is higher is NOT free to assume away. A real array has coupling
defects with the aggressor cell on either side of the victim, and a march
test that only detects one placement is unsound for the other -- this module
used to get this wrong (every coupling candidate was built assuming the
aggressor sits above the victim, addr(a) > addr(v), a convention its own
module docstring called "WLOG" even though it demonstrably was not: the same
synthesized spec scored 15/15 on real faults placed that way and 12/15 on the
identical faults mirrored, with CFST/CFIN/CFID the ones that escaped --
measured via ``run_algo_campaign``, not assumed). The candidate builders for
coupling categories (:func:`_combo_candidates`, :func:`_aggressor_clamp_candidates`)
now construct a chained pair of elements per targeted primitive -- one
engineered for the aggressor-above placement, one for aggressor-below -- and
:func:`synthesize_elements`'s scoring only credits a primitive as covered once
:func:`detects` confirms it under BOTH ``aggressor_gt_victim=True`` and
``aggressor_gt_victim=False``. :func:`synth_verification_faults` mirrors this:
every coupling primitive gets two real fault records, one per placement, so
``do_synth --verify``'s Verilator campaign can actually falsify a
placement-asymmetric result instead of only ever exercising the one placement
the search happened to assume.

Excluded from synthesis targeting: the six fixed types
(SOF/AF_NOACC/AF_ALIAS/CFDS/DRF/HSD -- see ``fault_primitives.py``'s module
docstring for why each is structurally unreachable by a per-cell
value-transition walk; DRF specifically has no value-transition walk to
search over at all, since it is sensitized by elapsed idle time rather than
any op sequence, and HSD's row-membership aggressor selection has no fixed
per-cell aggressor to walk either) and any registry entry using the
``raw_sv`` escape hatch (its semantics are hand-written SystemVerilog with
no DSL description for this module's oracle to interpret). Every synthesis
result states this
exclusion explicitly -- never silently implies full registry coverage.

A read op's code is an ASSERTION, not a probe -- a critical distinction an
earlier version of this module got wrong. ``march_engine.sv`` checks a read
against the LITERAL value its own op code names (r0 asserts "this cell
reads as 0 here"); it never computes a parallel "golden" value at runtime.
``run_algo_campaign`` additionally runs an explicit no-fault golden pass
first and raises if IT reports a detection -- a march test whose own read
op-codes don't match what a fault-free memory actually produces is invalid,
regardless of whether comparing two abstract read-lists happens to show a
difference. Every candidate this module generates is therefore built by
tracking golden state explicitly and always choosing the read literal that
genuinely matches it (see :func:`_golden_state_after` and the candidate
builders below) -- never by trying both literals and letting a downstream
comparison sort out which one was sound.

``Sensitize.port`` (per-port sensitizing) is not modeled here, and a
port-qualified primitive is REJECTED as a synthesis target rather than
silently treated as port-agnostic. This synthesizer is single-port: its
2-cell model has one access stream with no notion of which port issued an
op, so a ``port="0"``/``"1"`` constraint has nothing to bind to and a
synthesized algorithm's coverage claim for such a type would be unsound.
``fault_ram_gen.render_fault_ram`` refuses the same combination at
``num_ports=1`` for the concrete reason that the single-port engine's
``write_op()``/``read_op()`` have no ``port`` argument at all.

(This docstring previously justified the omission as "matching
``fault_ram_gen.py``'s own codegen, which likewise never references it".
That was true only because the field was silently inert there -- a bug,
now fixed: codegen honours it for ``write_effect``/``read_effect`` at
``num_ports=2``.)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .alg_spec import DIR_MAP, MAX_ELEMENTS, MAX_OPS, OP_MAP, WAIT_BASE, AlgSpec, Element, resolve_directions
from .fault_primitives import FIXED_TYPE_NAMES, FaultPrimitive

OP_R0, OP_R1, OP_W0, OP_W1 = OP_MAP["r0"], OP_MAP["r1"], OP_MAP["w0"], OP_MAP["w1"]
DIR_UP, DIR_DOWN, DIR_EITHER = DIR_MAP["up"], DIR_MAP["down"], DIR_MAP["either"]


# --------------------------------------------------------------------------- #
# Parameter resolution -- generalizes algo_engine.generate_all_types_faults'
# per-hardcoded-name p0/p1 choices (CFIN/CFID p0=2, CFST p0=1) into a uniform
# rule that also covers a user's custom add_fault_type primitive.
# --------------------------------------------------------------------------- #
def resolve_params(p: FaultPrimitive) -> tuple[int, int]:
    """Resolve a primitive's ``p0``/``p1``-parameterized tokens to concrete
    integers, used identically by this module's oracle and by
    :func:`synth_verification_faults`'s ``FaultRecord`` construction, so they
    can never drift apart.

    - ``sensitize.transition == "p0"`` (CFIN/CFID-style) -> ``p0 = 2``
      (either direction: the most permissive choice, matching those
      primitives' own registry convention exactly).
    - ``sensitize.pre == "p0"`` (CFST-style aggressor hold) -> ``p0 = 1``.
    - ``effect.value == "p1"`` -> ``p1 = 1``; ``effect.value == "p0"`` ->
      ``p1 = 0``. The exact binary choice does not affect correctness: the
      greedy walk (:func:`synthesize_elements`) only accepts a candidate
      when the fault, AS INSTANTIATED WITH THESE RESOLVED PARAMS, is
      actually observed to diverge from a golden-sound trace -- any
      consistent choice works. (This means CFST resolves to ``p1=1`` here,
      not the hand-tuned ``p1=0`` ``generate_all_types_faults`` uses for its
      fixed demonstration fault list -- both are valid instantiations of the
      same parameterized fault.)
    """
    if p.sensitize.transition == "p0":
        p0 = 2
    elif p.sensitize.pre == "p0":
        p0 = 1
    else:
        p0 = 0
    p1 = 1 if p.effect.value == "p1" else 0
    return p0, p1


def _resolve_bit(token: str | None, p0: int, p1: int) -> int:
    if token == "0":
        return 0
    if token == "1":
        return 1
    if token == "p0":
        return p0
    if token == "p1":
        return p1
    raise ValueError(f"unresolvable bit token: {token!r}")


def _matches_bit(token: str, actual: int, p0: int, p1: int) -> bool:
    return True if token == "x" else _resolve_bit(token, p0, p1) == actual


def _classify_transition(old: int, new: int) -> str:
    if old == 0 and new == 1:
        return "up"
    if old == 1 and new == 0:
        return "down"
    return "none"


def _transition_matches(token: str, actual: str, p0: int) -> bool:
    """Mirrors fault_ram_gen._transition_cond's SV codegen exactly:
    up->up, down->dn, either->(up||dn), p0->dir_match(p0, up, dn) where p0
    resolves to 0=up-only/1=down-only/2=either (see :func:`resolve_params`).
    """
    if token == "up":
        return actual == "up"
    if token == "down":
        return actual == "down"
    if token == "either":
        return actual in ("up", "down")
    if token == "p0":
        if p0 == 0:
            return actual == "up"
        if p0 == 1:
            return actual == "down"
        return actual in ("up", "down")
    return False  # "x": write_effect/aggressor primitives always set a real token


# --------------------------------------------------------------------------- #
# The abstract oracle -- mirrors fault_ram_gen.py's four render_*_arm
# functions exactly (verified against that file directly, not inferred).
# --------------------------------------------------------------------------- #
def _apply_static_clamp(v: int, a: int, fault: FaultPrimitive | None) -> int:
    """Re-checked after every single op (mirrors render_static_clamp_arm,
    called every cycle in the real SV regardless of which op is executing --
    "wins over any coupling effect", per engine/README.md)."""
    if fault is None or fault.category != "static_clamp":
        return v
    p0, p1 = resolve_params(fault)
    target = _resolve_bit(fault.effect.value, p0, p1)
    if fault.sensitize.on == "aggressor":
        hold = _resolve_bit(fault.sensitize.pre, p0, p1)
        return target if (a == hold and v != target) else v
    return target if v != target else v


def _apply_op(v: int, a: int, op: int, role: str, fault: FaultPrimitive | None) -> tuple[int, int, int | None]:
    """Apply one r0/r1/w0/w1 op to the given role ('v' or 'a'). Returns
    (new_v, new_a, observed) -- observed is non-None only for a read op on
    role 'v' (the only target-observable role; Effect.target is always
    "victim" for every primitive in this project). ``observed`` is the raw
    simulated bit; the caller is responsible for comparing it against the
    op's own literal assertion (0 for r0, 1 for r1) -- see module docstring."""
    if op >= WAIT_BASE:
        # A wait/idle op: genuinely no-op for this abstract oracle -- no
        # address is touched, so nothing is asserted. DRF (the only fault
        # class a wait op can sensitize) is a FIXED_TYPE_NAMES type and can
        # never reach this synthesizer at all (see synthesize_alg's module
        # docstring), so this branch exists purely so a wait-containing
        # spec, if ever passed to replay()/detects() directly, behaves
        # correctly rather than being silently misread as a read.
        return v, a, None
    is_write = op in (OP_W0, OP_W1)
    written = 0 if op == OP_W0 else (1 if op == OP_W1 else None)
    observed: int | None = None

    if role == "v":
        if is_write:
            old_v = v
            v = written  # type: ignore[assignment]
            if fault is not None and fault.category == "write_effect" and fault.sensitize.on == "victim":
                p0, p1 = resolve_params(fault)
                if (_matches_bit(fault.sensitize.pre, old_v, p0, p1)
                        and _matches_bit(fault.sensitize.written, written, p0, p1)):  # type: ignore[arg-type]
                    v = _resolve_bit(fault.effect.value, p0, p1)
        else:
            old_v = v
            observed = v
            if fault is not None and fault.category == "read_effect":
                p0, p1 = resolve_params(fault)
                if _matches_bit(fault.sensitize.pre, old_v, p0, p1):
                    if fault.effect.kind == "corrupt_read":
                        observed = _resolve_bit(fault.effect.value, p0, p1)
                    else:  # force_read (RDF/DRDF)
                        v = _resolve_bit(fault.effect.value, p0, p1)
                        also = fault.effect.also_read if fault.effect.also_read is not None else fault.effect.value
                        observed = _resolve_bit(also, p0, p1)
    else:  # role == "a"
        if is_write:
            old_a = a
            a = written  # type: ignore[assignment]
            if fault is not None and fault.category == "write_effect" and fault.sensitize.on == "aggressor":
                p0, p1 = resolve_params(fault)
                transition = _classify_transition(old_a, written)  # type: ignore[arg-type]
                if _transition_matches(fault.sensitize.transition, transition, p0):
                    v = (1 - v) if fault.effect.kind == "invert" else _resolve_bit(fault.effect.value, p0, p1)
        # a read on role 'a' has no target-observable effect: not tracked.

    return v, a, observed


def _role_order(direction: int, aggressor_gt_victim: bool) -> tuple[str, str]:
    """``direction`` must already be resolved to a concrete 0/1 -- callers
    (``replay``, ``_golden_state_after``) resolve the whole element list ONCE,
    up front, via :func:`autombist.alg_spec.resolve_directions`, rather than
    each element guessing what an unresolved `either` (2) means on its own
    (which is what this function used to do, and which is exactly the kind of
    second, independently-guessed rule that let the engine and the classic
    RTL disagree on march_x before that rule was unified). The ``else``
    branch below is only a defensive fallback for a caller that hands this
    function a raw, unresolved ``Element.direction`` directly."""
    if direction == DIR_DOWN:
        return ("a", "v") if aggressor_gt_victim else ("v", "a")
    return ("v", "a") if aggressor_gt_victim else ("a", "v")  # up (or an unresolved either, defensively)


def replay(
    elements: list[Element], fault: FaultPrimitive | None, *,
    aggressor_gt_victim: bool = True, init_val: int = 1,
) -> list[tuple[int, int]]:
    """Simulate ``elements`` against the abstract ``(v, a)`` pair.
    ``fault=None`` is the golden trace. Returns ``(asserted, observed)`` for
    every read on role 'v', in order -- ``asserted`` is the literal value the
    read op-code names (0 for r0, 1 for r1), matching how march_engine.sv
    itself checks a read (against its own op code, not a parallel golden
    simulation; see module docstring)."""
    v = a = init_val
    obs: list[tuple[int, int]] = []
    for elem, direction in zip(elements, resolve_directions(elements)):
        for role in _role_order(direction, aggressor_gt_victim):
            for op in elem.ops:
                v, a, observed = _apply_op(v, a, op, role, fault)
                v = _apply_static_clamp(v, a, fault)
                if role == "v" and observed is not None:
                    asserted = 0 if op == OP_R0 else 1
                    obs.append((asserted, observed))
    return obs


def is_golden_sound(elements: list[Element], *, aggressor_gt_victim: bool = True, init_val: int = 1) -> bool:
    """True iff every read in ``elements`` would pass against a fault-free
    memory -- i.e. this is a *valid* march test, not just a sequence that
    happens to make some fault's faulty trace diverge from its own golden
    trace. A candidate failing this can never be accepted (see
    :func:`synthesize_elements`) -- an unsound spec would make even
    ``run_algo_campaign``'s own no-fault golden pass report a spurious
    detection."""
    return all(a == o for a, o in replay(elements, None, aggressor_gt_victim=aggressor_gt_victim, init_val=init_val))


def detects(elements: list[Element], fault: FaultPrimitive, *,
            aggressor_gt_victim: bool = True, init_val: int = 1) -> bool:
    """True iff, in the FAULTY trace, some read's observed value diverges
    from its own op-code's literal assertion -- the same criterion
    march_engine.sv itself uses. Callers that need to know a candidate is
    usable at all (not just that it "detects" something) must separately
    check :func:`is_golden_sound` -- this function alone does not guarantee
    that."""
    return any(
        asserted != observed
        for asserted, observed in replay(elements, fault, aggressor_gt_victim=aggressor_gt_victim, init_val=init_val)
    )


# --------------------------------------------------------------------------- #
# Candidate generation -- golden-state-aware by construction, so every
# generated candidate is sound (is_golden_sound-true) by design, never by
# accident or by post-hoc filtering.
# --------------------------------------------------------------------------- #
def _advance_golden(v: int, a: int, elements: list[Element], *, aggressor_gt_victim: bool) -> tuple[int, int]:
    """(v, a) after replaying ``elements`` with no fault, starting from an
    ARBITRARY ``(v, a)`` rather than ``init_val`` -- the primitive
    :func:`_golden_state_after` specializes to the whole-spec case. Used to
    chain candidate construction across sub-groups (e.g. an aggressor-above
    combo followed immediately by an aggressor-below combo for the same
    coupling primitive -- see :func:`_combo_candidates`), where the second
    sub-group's own setup logic needs the REAL state the first one left
    behind, not a fresh ``init_val``.

    Placement-invariant for the no-fault case: in :func:`_apply_op`, role
    'a's pass has no side effect on v (and vice versa) when ``fault is
    None`` -- every branch that lets one role's op influence the other is
    gated on a specific fault category being active. Since the two roles'
    passes don't interact, the ORDER ``_role_order`` puts them in cannot
    change the result, so ``aggressor_gt_victim`` is accepted only for
    interface symmetry and never actually changes the answer here."""
    for elem, direction in zip(elements, resolve_directions(elements)):
        for role in _role_order(direction, aggressor_gt_victim):
            for op in elem.ops:
                v, a, _ = _apply_op(v, a, op, role, None)
                v = _apply_static_clamp(v, a, None)
    return v, a


def _golden_state_after(elements: list[Element], *, aggressor_gt_victim: bool = True, init_val: int = 1) -> tuple[int, int]:
    """(v, a) after replaying ``elements`` with no fault, from ``init_val`` --
    the starting point for building the next candidate."""
    return _advance_golden(init_val, init_val, elements, aggressor_gt_victim=aggressor_gt_victim)


def _bit_op(value: int, *, write: bool) -> int:
    if write:
        return OP_W1 if value == 1 else OP_W0
    return OP_R1 if value == 1 else OP_R0


def _sensitize_bit(token: str, default: int = 0) -> int:
    """Concrete 0/1 target for a "0"/"1"/"x" sensitize token (this module
    only ever builds candidates for the three DSL categories, none of which
    use "p0"/"p1" on sensitize.pre/written outside CFST/CFIN/CFID, which are
    handled by their own dedicated builders below, not this helper)."""
    return 1 if token == "1" else (0 if token == "0" else default)


def _element_op_variants(p: FaultPrimitive, v: int, max_ops: int) -> list[tuple[list[int], int]]:
    """Every reasonable (ops, resulting_golden_v) shape for a single element
    targeting primitive ``p``, given the current golden v just before this
    element. Static-clamp/on=victim faults (SA0/SA1) need TWO variants, not
    one: a bare read of golden's current value only reveals the clamp when
    that value already differs from the clamp's target -- if golden's v
    already happens to equal the target (e.g. SA0's target is 0, and the
    mandatory init bracket already sets v to 0), forcing 0->0 is invisible
    to a plain read. The second variant -- write the OPPOSITE of the
    target, then read -- covers exactly that case, and is the only way
    SA0/SA1 synthesized in isolation are detectable at all.

    static_clamp/on=aggressor (CFST) is NOT built here -- its condition
    depends on role 'a' holding a specific value, which a single
    victim-only element can't reliably arrange (the same structural hazard
    write_effect/on=aggressor has); see :func:`_aggressor_clamp_candidates`.
    """
    variants: list[tuple[list[int], int]] = []

    if p.category == "static_clamp" and p.sensitize.on == "victim":
        p0, p1 = resolve_params(p)
        target = _resolve_bit(p.effect.value, p0, p1)
        variants.append(([_bit_op(v, write=False)], v))
        opposite = 1 - target
        if v != opposite:
            variants.append(([_bit_op(opposite, write=True), _bit_op(opposite, write=False)], opposite))

    elif p.category == "write_effect":  # on == "victim": TF/WDF
        # Bare-read variant FIRST: if this primitive's own condition already
        # matches golden's current v (no setup write needed below), it's
        # possible the fault already fired earlier (e.g. the mandatory init
        # bracket's own w0 satisfies pre=0/written=0 -- WDF0 exactly -- when
        # v starts at 0) and left the FAULTY trace already diverged from
        # golden. The write+read variant below would then immediately
        # re-check the SAME (pre, written) condition against the actual
        # (possibly already-diverged) v -- which no longer matches pre, so
        # it silently fails to re-fire and the write proceeds normally,
        # ERASING the leftover divergence before any read observes it. A
        # bare read of golden's current value, tried first, catches exactly
        # this case (mirroring static_clamp's variant A above); it's always
        # sound to try (asserts exactly what golden shows) whether or not it
        # ends up being the one that scores.
        variants.append(([_bit_op(v, write=False)], v))

        pre = _sensitize_bit(p.sensitize.pre)
        written = _sensitize_bit(p.sensitize.written)
        ops: list[int] = []
        vv = v
        if vv != pre:
            ops.append(_bit_op(pre, write=True))
            vv = pre
        ops.append(_bit_op(written, write=True))
        vv = written  # golden: a normal write always succeeds
        ops.append(_bit_op(vv, write=False))  # verify-read matches what golden just produced
        variants.append((ops, vv))

    elif p.category == "read_effect":  # IRF/RDF/DRDF
        pre = _sensitize_bit(p.sensitize.pre)
        ops = []
        vv = v
        if vv != pre:
            ops.append(_bit_op(pre, write=True))
            vv = pre
        needs_two = (
            p.effect.kind == "force_read"
            and p.effect.also_read is not None
            and p.effect.also_read != p.effect.value
        )
        ops.append(_bit_op(pre, write=False))
        if needs_two:
            ops.append(_bit_op(pre, write=False))  # golden v is unchanged by any read
        variants.append((ops, vv))

    return [(ops, fv) for ops, fv in variants if ops and len(ops) <= max_ops]


def _single_element_candidates(
    remaining: list[FaultPrimitive], max_ops: int, golden_v: int, golden_a: int,
) -> list[tuple[list[Element], int]]:
    """Returns (candidate_elements, resulting_golden_v) pairs -- every
    candidate here is a single element, golden-sound by construction, for
    every category except write_effect/on=aggressor (see
    :func:`_combo_candidates`)."""
    out: list[tuple[list[Element], int]] = []
    for p in remaining:
        if p.category == "write_effect" and p.sensitize.on == "aggressor":
            continue  # coupling-class: needs a combo, see _combo_candidates
        if p.category == "static_clamp" and p.sensitize.on == "aggressor":
            continue  # CFST-style: needs a combo, see _aggressor_clamp_candidates
        for ops, final_v in _element_op_variants(p, golden_v, max_ops):
            for direction in (DIR_UP, DIR_DOWN):
                out.append(([Element(direction=direction, ops=list(ops))], final_v))
    return out


def _aggressor_clamp_candidate_one(
    p: FaultPrimitive, golden_a: int, direction: int,
) -> tuple[list[Element], int]:
    """static_clamp/on=aggressor faults (CFST) are gated on role 'a'
    CURRENTLY holding a specific value -- a condition a single victim-only
    element (see :func:`_element_op_variants`) cannot reliably arrange,
    since it never touches role 'a' at all. Without an explicit setup, this
    only "works" by accident of whatever golden_a happens to be from prior
    element history (e.g. it looked correct at the default init_val=1
    purely because the mandatory init bracket's v-portion runs before its
    own a-portion, leaving 'a' briefly at its untouched init value -- this
    breaks entirely at init_val=0, or once any prior element has moved 'a'
    away from the hold value).

    The fix: if golden_a doesn't already equal the fault's hold value,
    prepend a setup element that writes it there (a side effect: the same
    op-list also writes 'v' to that value, since both roles share it).
    Then the actual detecting element writes v to the OPPOSITE of the
    clamp's forced target and reads it back -- during that element's own
    'v'-labelled pass (which, per ``direction``, runs before the 'a'-labelled
    pass touches 'a' again), 'a' still holds from the setup, so the clamp is
    guaranteed to fire and force v back to the target, diverging from what a
    plain write would have produced.

    ``direction`` (not hardcoded): the caller picks ``DIR_UP`` to engineer
    this for ``aggressor_gt_victim=True`` and ``DIR_DOWN`` for ``False`` --
    ``_role_order(DIR_UP, True) == _role_order(DIR_DOWN, False) == ("v",
    "a")``, so either choice reproduces the exact same "v-pass, then a-pass"
    ordering this construction depends on; only the physical placement it is
    valid for changes. See :func:`_aggressor_clamp_candidates`, the only
    caller, for how both orientations get chained into one candidate."""
    p0, p1 = resolve_params(p)
    hold = _resolve_bit(p.sensitize.pre, p0, p1)
    target = _resolve_bit(p.effect.value, p0, p1)

    setup: list[Element] = []
    a_state = golden_a
    if a_state != hold:
        setup.append(Element(direction=direction, ops=[_bit_op(hold, write=True)]))
        a_state = hold

    opposite = 1 - target
    detect_elem = Element(direction=direction, ops=[_bit_op(opposite, write=True), _bit_op(opposite, write=False)])
    return setup + [detect_elem], opposite


def _aggressor_clamp_candidates(
    remaining: list[FaultPrimitive], golden_v: int, golden_a: int,
) -> list[tuple[list[Element], int]]:
    """One CHAINED, bidirectional candidate per CFST-shaped primitive:
    :func:`_aggressor_clamp_candidate_one` engineered for
    aggressor-above (``DIR_UP``), immediately followed by the same shape
    re-engineered for aggressor-below (``DIR_DOWN``) -- built from the REAL
    golden state the first half leaves behind (:func:`_advance_golden`), not
    independently from ``golden_a``, since the second half's own "does 'a'
    already hold what I need" setup logic must see what actually happened.
    Concatenating both into a single candidate (rather than offering them as
    two separate options) is what lets one synthesized test detect a CFST
    defect regardless of which side of the victim the aggressor cell is
    physically on -- see the module docstring."""
    out: list[tuple[list[Element], int]] = []
    for p in remaining:
        if not (p.category == "static_clamp" and p.sensitize.on == "aggressor"):
            continue
        above_group, _ = _aggressor_clamp_candidate_one(p, golden_a, DIR_UP)
        mid_v, mid_a = _advance_golden(golden_v, golden_a, above_group, aggressor_gt_victim=True)
        below_group, below_v = _aggressor_clamp_candidate_one(p, mid_a, DIR_DOWN)
        out.append((above_group + below_group, below_v))
    return out


def _combo_candidate_one(
    p: FaultPrimitive, golden_a: int, direction: int,
) -> tuple[list[Element], int]:
    """write_effect/on=aggressor faults (CFIN/CFID) cannot self-report within
    one element: the aggressor-triggered mutation of v happens during a's
    pass of the element, which for the "v-pass-first" role order runs AFTER
    v's own pass -- so any read in the SAME element already ran too early to
    see it. The fix: an atomic group -- a write-element (v-pass writes
    normally, then a-pass's write triggers the aggressor effect as the LAST
    word on v within that element) immediately followed by a read-only
    element asserting exactly what golden's v now holds (v-pass read
    happens first, before that element's own a-pass, observing the
    still-live divergence untouched).

    For a "force" (not "invert") effect kind -- CFID-style -- there is a
    second constraint beyond "a genuine transition on a": the write-
    element's OWN v-pass write must NOT already happen to equal the forced
    target, or the force is masked (the v-pass's normal write and the
    aggressor-triggered force produce the identical value, so nothing
    observably changes). Both roles share one op-list, so the write value
    is simultaneously "whatever triggers a's transition" and "whatever v
    gets written to" -- these can conflict depending on golden_a's current
    state (concretely: if golden_a already equals 1-target, the only
    transition-triggering write value IS target, masking it). When that
    happens, an extra bare setup write flips 'a' first, so the actual
    transition-write can pick the other, observable value.

    ``direction`` (not hardcoded): every element here uses ``direction``
    uniformly, so the "v-pass, then a-pass" ordering this construction
    depends on holds for ``DIR_UP`` under ``aggressor_gt_victim=True`` and
    equally for ``DIR_DOWN`` under ``False`` (``_role_order(DIR_UP, True) ==
    _role_order(DIR_DOWN, False) == ("v", "a")``) -- only the physical
    placement each is valid for changes. See :func:`_combo_candidates`, the
    only caller, for how both get chained into one candidate."""
    p0, p1 = resolve_params(p)
    target = None if p.effect.kind == "invert" else _resolve_bit(p.effect.value, p0, p1)

    setup: list[Element] = []
    a_state = golden_a
    if target is not None:
        natural_write_val = 0 if a_state == 1 else 1
        if natural_write_val == target:
            setup_val = 1 - a_state
            setup.append(Element(direction=direction, ops=[_bit_op(setup_val, write=True)]))
            a_state = setup_val

    # Any write whose value differs from a_state is a genuine transition
    # (either direction is always accepted by this module's
    # resolve_params -- see its docstring), guaranteeing the aggressor
    # trigger fires; by construction (above), it's also never masked.
    write_val = 0 if a_state == 1 else 1
    write_elem = Element(direction=direction, ops=[_bit_op(write_val, write=True)])
    golden_v_after = write_val  # v-pass's own write, in golden, always succeeds too
    read_elem = Element(direction=direction, ops=[_bit_op(golden_v_after, write=False)])
    return setup + [write_elem, read_elem], golden_v_after


def _combo_candidates(
    remaining: list[FaultPrimitive], golden_v: int, golden_a: int,
) -> list[tuple[list[Element], int]]:
    """One CHAINED, bidirectional candidate per CFIN/CFID-shaped primitive,
    the write_effect/on=aggressor twin of :func:`_aggressor_clamp_candidates`:
    :func:`_combo_candidate_one` engineered for aggressor-above (``DIR_UP``),
    immediately followed by the same shape re-engineered for
    aggressor-below (``DIR_DOWN``), built from the REAL golden state the
    first half leaves behind (:func:`_advance_golden`) so the second half's
    own masking check sees what actually happened rather than the state
    before the first half ran. See the module docstring for why both
    placements must be covered by ONE candidate rather than offered as
    alternatives."""
    out: list[tuple[list[Element], int]] = []
    for p in remaining:
        if not (p.category == "write_effect" and p.sensitize.on == "aggressor"):
            continue
        above_group, _ = _combo_candidate_one(p, golden_a, DIR_UP)
        mid_v, mid_a = _advance_golden(golden_v, golden_a, above_group, aggressor_gt_victim=True)
        below_group, below_v = _combo_candidate_one(p, mid_a, DIR_DOWN)
        out.append((above_group + below_group, below_v))
    return out


def _candidate_groups(
    remaining: list[FaultPrimitive], max_ops: int, golden_v: int, golden_a: int,
) -> list[tuple[list[Element], int]]:
    return (
        _single_element_candidates(remaining, max_ops, golden_v, golden_a)
        + _combo_candidates(remaining, golden_v, golden_a)
        + _aggressor_clamp_candidates(remaining, golden_v, golden_a)
    )


def synthesize_elements(
    target: list[FaultPrimitive], *,
    max_elements: int = MAX_ELEMENTS, max_ops: int = MAX_OPS,
    aggressor_gt_victim: bool = True, init_val: int = 1,
) -> tuple[list[Element], list[str]]:
    """The greedy walk. Returns ``(elements, uncovered_names)``. Bracketed by
    a fixed ``either w0`` init element and ``either r0`` final element,
    matching every existing built-in ``.alg`` file's own convention; working
    elements in between are never ``either`` (sidesteps the address-order
    ambiguity a mid-walk ``either`` would introduce into the 2-cell model --
    a deliberate, stated scope narrowing, not an oversight). Every candidate
    considered is golden-sound by construction (see the candidate builders
    above); the final ``either r0`` bracket only fires when golden's v is
    actually 0 at that point -- if not, an ``either r1`` bracket is emitted
    instead, so the mandatory trailing verify-read is never itself unsound.

    A name only ever leaves ``uncovered_names`` once :func:`detects` confirms
    it under BOTH ``aggressor_gt_victim=True`` and ``=False`` (see the scoring
    loop below) -- unconditionally, regardless of what this function's own
    ``aggressor_gt_victim`` parameter is set to. That parameter no longer
    selects which placement gets covered (both always do, or the primitive is
    reported uncovered); it now only threads through to the golden-state
    bookkeeping and the final soundness assert, both of which are provably
    placement-invariant (:func:`_advance_golden`'s docstring), so changing it
    cannot change this function's result. It remains solely for internal
    consistency with :func:`replay`/:func:`detects`, which genuinely do need
    it when called directly on a hand-built spec outside a synthesis run.
    """
    if max_elements < 2:
        raise ValueError(
            f"max_elements must be >= 2 (need room for the mandatory init+final "
            f"brackets), got {max_elements}"
        )
    # See the module docstring: this synthesizer is single-port, so a per-port
    # sensitizing constraint has nothing to bind to in its 2-cell model. Refuse
    # rather than quietly synthesize an algorithm whose coverage claim for that
    # type would not hold.
    port_qualified = [p.name for p in target if p.sensitize.port != "x"]
    if port_qualified:
        raise ValueError(
            f"fault type(s) {port_qualified} set sensitize.port, which this "
            "single-port synthesizer cannot model: its 2-cell walk has no notion "
            "of which port issued an op, so any coverage it claimed for them "
            "would be unsound. Drop them from the target set, or set "
            "sensitize.port='x'"
        )
    # The two-cell state gate is not modelled yet: the oracle's write-victim and
    # read-victim firing conditions (_apply_op) do not read `a` at all, so an
    # agg_pre primitive would be simulated as its strictly-more-permissive
    # single-cell twin and marked covered, and no candidate builder emits a
    # setup element to establish the aggressor's state anyway.
    #
    # Reported as uncovered rather than raised (which is what sensitize.port
    # does): port-qualified types are exotic and never appear in the built-in
    # registry, so refusing outright costs nothing there, whereas ten agg_pre
    # types ARE built-ins now -- raising would make `synth` unusable against
    # the default registry. Landing them in `uncovered` keeps the guarantee
    # that matters: they can never be counted as covered.
    # See docs/coupling-family-plan.md, Step 6.
    unmodelled = [p.name for p in target if p.sensitize.agg_pre != "x"]
    target = [p for p in target if p.sensitize.agg_pre == "x"]
    elements: list[Element] = [Element(direction=DIR_EITHER, ops=[OP_W0])]
    golden_v, golden_a = _golden_state_after(elements, aggressor_gt_victim=aggressor_gt_victim, init_val=init_val)
    remaining = {p.name: p for p in target}

    reserved = 1  # the final verify bracket
    while remaining and len(elements) < max_elements - reserved:
        best: tuple[tuple[int, int], list[Element], set[str], int] | None = None
        for group, new_v in _candidate_groups(list(remaining.values()), max_ops, golden_v, golden_a):
            if len(elements) + len(group) > max_elements - reserved:
                continue
            trial = elements + group
            # Detection under BOTH placements, unconditionally -- not gated on
            # this function's own `aggressor_gt_victim` parameter (which now
            # governs only the golden-state bookkeeping below, itself proven
            # placement-invariant; see _advance_golden). A primitive credited
            # here is credited because a fixed march sequence catches it
            # regardless of which physical side the aggressor cell is on, not
            # because the search happened to check the one side it assumed.
            # Non-coupling primitives are placement-invariant by construction
            # (see _apply_op: only sensitize.on=="aggressor" faults let one
            # role's pass influence the other), so this costs them a redundant
            # second oracle call, never a different answer.
            newly = {
                name for name, p in remaining.items()
                if detects(trial, p, aggressor_gt_victim=True, init_val=init_val)
                and detects(trial, p, aggressor_gt_victim=False, init_val=init_val)
            }
            if not newly:
                continue
            total_ops = sum(len(e.ops) for e in group)
            score = (len(newly), -total_ops)
            if best is None or score > best[0]:
                best = (score, group, newly, new_v)
        if best is None:
            break
        _, group, newly, new_v = best
        elements.extend(group)
        golden_v = new_v
        golden_a = _golden_state_after(elements, aggressor_gt_victim=aggressor_gt_victim, init_val=init_val)[1]
        for name in newly:
            del remaining[name]

    elements.append(Element(direction=DIR_EITHER, ops=[_bit_op(golden_v, write=False)]))
    # Checked under BOTH placements, not just the one `aggressor_gt_victim`
    # names -- defense in depth for the module docstring's placement-invariance
    # claim (_advance_golden), rather than resting on that proof alone. Golden
    # soundness has no legitimate reason to depend on which side the abstract
    # aggressor cell is on: a fault-free memory has no aggressor at all.
    assert is_golden_sound(elements, aggressor_gt_victim=True, init_val=init_val), (
        "synthesized spec is not golden-sound (aggressor-above) -- this is an "
        "internal bug in synth_engine.py's candidate generation, not a "
        "user-facing condition"
    )
    assert is_golden_sound(elements, aggressor_gt_victim=False, init_val=init_val), (
        "synthesized spec is not golden-sound (aggressor-below) -- this is an "
        "internal bug in synth_engine.py's candidate generation, not a "
        "user-facing condition"
    )
    # `unmodelled` joins `remaining` so an agg_pre target is reported as
    # uncovered, never as covered -- the guarantee that actually matters.
    return elements, sorted(set(remaining.keys()) | set(unmodelled))


# --------------------------------------------------------------------------- #
# Top-level entry point.
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SynthResult:
    spec: AlgSpec
    targeted: list[str]
    covered: list[str]
    uncovered: list[str]
    excluded_fixed: list[str] = field(default_factory=lambda: list(FIXED_TYPE_NAMES))
    # Primitives this synthesizer's model cannot express, filtered out of the
    # target set rather than silently counted as covered. Distinct from
    # excluded_fixed, which is a permanent structural property of those six
    # types; these are excluded pending synthesizer support.
    excluded_unmodelled: list[str] = field(default_factory=list)


def synthesize_alg(
    registry: list[FaultPrimitive], name: str, *,
    max_elements: int = MAX_ELEMENTS, max_ops: int = MAX_OPS, init_val: int = 1,
) -> SynthResult:
    """Synthesize a new march test targeting every DSL-expressible,
    non-``raw_sv`` primitive in ``registry`` (the six fixed types are never
    targetable -- see module docstring; a ``raw_sv`` entry has no DSL
    description for this module's oracle to interpret, so it is excluded the
    same way).

    ``sensitize.agg_pre`` primitives are excluded on the same footing: the
    2-cell walk does not yet establish the aggressor's held state, so a target
    set containing one would report coverage the synthesized algorithm has not
    actually achieved. They are filtered here -- rather than raising, which
    would make `synth` unusable now that the built-in registry contains ten of
    them -- and reported in ``excluded_unmodelled`` so the omission is visible
    rather than implied. ``synthesize_elements`` still raises when handed one
    explicitly, so an explicit target can never be silently dropped."""
    target = [p for p in registry if p.raw_sv is None and p.sensitize.agg_pre == "x"]
    unmodelled = [p.name for p in registry if p.raw_sv is None and p.sensitize.agg_pre != "x"]
    elements, uncovered = synthesize_elements(target, max_elements=max_elements, max_ops=max_ops, init_val=init_val)
    targeted_names = [p.name for p in target]
    covered = [n for n in targeted_names if n not in uncovered]
    spec = AlgSpec(name=name, elements=elements)
    return SynthResult(
        spec=spec, targeted=targeted_names, covered=covered, uncovered=uncovered,
        excluded_unmodelled=unmodelled,
    )


# --------------------------------------------------------------------------- #
# Verification-fault generation for the real (Verilator) confirmation pass.
# --------------------------------------------------------------------------- #
def synth_verification_faults(mem, targets: list[FaultPrimitive]) -> list:
    """Concrete FaultRecords for a real Verilator confirmation of a
    synthesized spec's coverage claim, generalizing
    algo_engine.generate_all_types_faults' hardcoded per-name p0/p1 choices
    via :func:`resolve_params` so custom add_fault_type primitives are
    covered too (generate_all_types_faults' fixed BUILTIN_FAULT_TYPES tuple
    cannot reach them).

    Single-cell primitives get one record each. Coupling-class primitives
    (sensitize.on=="aggressor") get TWO -- one with the aggressor above the
    victim, one with it below -- because :func:`synthesize_elements` now
    requires a candidate to detect a coupling primitive under both
    placements before crediting it as covered (see the module docstring for
    why: a real array has coupling defects on both sides of a victim, and a
    march test sound for only one placement is not a valid coverage claim).
    A caller that ran only the first record, as this function used to,
    could never falsify a placement-asymmetric result -- exactly the gap
    that let this module claim "15/15, verified on real Verilator" for a
    spec that missed 3 of 15 primitives on half of all coupling placements.

    Both records reuse the SAME two addresses (``va``, ``va + 1``) with the
    victim/aggressor roles swapped, rather than deriving a second pair
    independently, so the existing never-wraps guarantee below covers both
    without new range reasoning. (generate_all_types_faults' own
    ``aa = (va + 1) % depth`` can wrap for a victim placed at the last
    address; that latent gap is not reproduced here.)"""
    from .algo_engine import FaultRecord  # local import: avoids a hard import-time
                                            # dependency from algo_engine -> synth_engine
                                            # on this pure-logic-vs-execution-engine module
    depth = mem.depth
    dw = mem.data_width
    coupling = [p for p in targets if p.sensitize.on == "aggressor"]
    single_cell = [p for p in targets if p.sensitize.on != "aggressor"]
    records = []
    # Coupling-class: va drawn from [0, depth-1) so va + 1 always stays
    # in-bounds -- never wraps, for either placement below.
    coupling_depth = max(depth - 1, 1)
    for i, p in enumerate(coupling):
        va = (i * 7 + 3) % coupling_depth
        vb = i % dw
        p0, p1 = resolve_params(p)
        records.append(FaultRecord(p.name, va, vb, va + 1, vb, p0, p1))       # aggressor above
        records.append(FaultRecord(p.name, va + 1, vb, va, vb, p0, p1))       # aggressor below
    for i, p in enumerate(single_cell):
        va = (i * 7 + 3) % depth
        vb = i % dw
        p0, p1 = resolve_params(p)
        records.append(FaultRecord(p.name, va, vb, 0, 0, p0, p1))
    return records
