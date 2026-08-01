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
aggressor -- with a WLOG convention ``addr(a) > addr(v)`` (matching this
project's own existing fixtures: ``engine/faults.example.txt``'s CFIN
100->101/CFID 110->111/CFST 120->121, and ``algo_engine.
generate_all_types_faults``'s ``aa = (va + 1) % depth``). Within one march
Element, the address-traversal direction resolves which role's op-list runs
first: ``up`` visits the lower address (v) first, then the higher (a);
``down`` visits a first, then v. Single-cell faults (SA0/SA1/TF/WDF/RDF/
IRF/DRDF) never reference ``a`` at all -- the model is uniform.

Excluded from synthesis targeting: the four fixed types
(SOF/AF_NOACC/AF_ALIAS/CFDS -- see ``fault_primitives.py``'s module
docstring for why each is structurally unreachable by a per-cell
value-transition walk) and any registry entry using the ``raw_sv`` escape
hatch (its semantics are hand-written SystemVerilog with no DSL description
for this module's oracle to interpret). Every synthesis result states this
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

``Sensitize.port`` (per-port sensitizing) is not modeled here, matching
``fault_ram_gen.py``'s own codegen, which likewise never references it for
any of the three DSL-coverable categories -- single-port only, by the same
scope this project's classic-path multi-port support (march-1r1w/march-2rw)
has not yet extended research mode to (see Workstream J's own "Deferred"
section).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .alg_spec import DIR_MAP, MAX_ELEMENTS, MAX_OPS, OP_MAP, AlgSpec, Element
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
    if direction == DIR_DOWN:
        return ("a", "v") if aggressor_gt_victim else ("v", "a")
    return ("v", "a") if aggressor_gt_victim else ("a", "v")  # up, or either (treated as up)


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
    for elem in elements:
        for role in _role_order(elem.direction, aggressor_gt_victim):
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
def _golden_state_after(elements: list[Element], *, aggressor_gt_victim: bool = True, init_val: int = 1) -> tuple[int, int]:
    """(v, a) after replaying ``elements`` with no fault -- the starting
    point for building the next candidate."""
    v = a = init_val
    for elem in elements:
        for role in _role_order(elem.direction, aggressor_gt_victim):
            for op in elem.ops:
                v, a, _ = _apply_op(v, a, op, role, None)
                v = _apply_static_clamp(v, a, None)
    return v, a


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


def _aggressor_clamp_candidates(
    remaining: list[FaultPrimitive], golden_v: int, golden_a: int,
) -> list[tuple[list[Element], int]]:
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
    v-portion (which runs before its a-portion touches 'a' again), 'a'
    still holds from the setup, so the clamp is guaranteed to fire and
    force v back to the target, diverging from what a plain write would
    have produced."""
    out: list[tuple[list[Element], int]] = []
    for p in remaining:
        if not (p.category == "static_clamp" and p.sensitize.on == "aggressor"):
            continue
        p0, p1 = resolve_params(p)
        hold = _resolve_bit(p.sensitize.pre, p0, p1)
        target = _resolve_bit(p.effect.value, p0, p1)

        setup: list[Element] = []
        a_state = golden_a
        if a_state != hold:
            setup.append(Element(direction=DIR_UP, ops=[_bit_op(hold, write=True)]))
            a_state = hold

        opposite = 1 - target
        detect_elem = Element(direction=DIR_UP, ops=[_bit_op(opposite, write=True), _bit_op(opposite, write=False)])
        out.append((setup + [detect_elem], opposite))
    return out


def _combo_candidates(
    remaining: list[FaultPrimitive], golden_v: int, golden_a: int,
) -> list[tuple[list[Element], int]]:
    """write_effect/on=aggressor faults (CFIN/CFID) cannot self-report within
    one element: the aggressor-triggered mutation of v happens during a's
    portion of the element, which for 'up' direction runs AFTER v's own
    portion -- so any read in the SAME element already ran too early to see
    it. The fix: an atomic group -- an 'up' write-element (v-portion writes
    normally, then a-portion's write triggers the aggressor effect as the
    LAST word on v within that element) immediately followed by an 'up'
    read-only element asserting exactly what golden's v now holds (v-portion
    read happens first, before that element's own a-portion, observing the
    still-live divergence untouched).

    For a "force" (not "invert") effect kind -- CFID-style -- there is a
    second constraint beyond "a genuine transition on a": the write-
    element's OWN v-portion write must NOT already happen to equal the
    forced target, or the force is masked (the v-portion's normal write and
    the aggressor-triggered force produce the identical value, so nothing
    observably changes). Both roles share one op-list, so the write value
    is simultaneously "whatever triggers a's transition" and "whatever v
    gets written to" -- these can conflict depending on golden_a's current
    state (concretely: if golden_a already equals 1-target, the only
    transition-triggering write value IS target, masking it). When that
    happens, an extra bare setup write flips 'a' first, so the actual
    transition-write can pick the other, observable value."""
    out: list[tuple[list[Element], int]] = []
    for p in remaining:
        if not (p.category == "write_effect" and p.sensitize.on == "aggressor"):
            continue
        p0, p1 = resolve_params(p)
        target = None if p.effect.kind == "invert" else _resolve_bit(p.effect.value, p0, p1)

        setup: list[Element] = []
        a_state = golden_a
        if target is not None:
            natural_write_val = 0 if a_state == 1 else 1
            if natural_write_val == target:
                setup_val = 1 - a_state
                setup.append(Element(direction=DIR_UP, ops=[_bit_op(setup_val, write=True)]))
                a_state = setup_val

        # Any write whose value differs from a_state is a genuine transition
        # (either direction is always accepted by this module's
        # resolve_params -- see its docstring), guaranteeing the aggressor
        # trigger fires; by construction (above), it's also never masked.
        write_val = 0 if a_state == 1 else 1
        write_elem = Element(direction=DIR_UP, ops=[_bit_op(write_val, write=True)])
        golden_v_after = write_val  # v-portion's own write, in golden, always succeeds too
        read_elem = Element(direction=DIR_UP, ops=[_bit_op(golden_v_after, write=False)])
        out.append((setup + [write_elem, read_elem], golden_v_after))
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
    """
    if max_elements < 2:
        raise ValueError(
            f"max_elements must be >= 2 (need room for the mandatory init+final "
            f"brackets), got {max_elements}"
        )
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
            newly = {
                name for name, p in remaining.items()
                if detects(trial, p, aggressor_gt_victim=aggressor_gt_victim, init_val=init_val)
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
    assert is_golden_sound(elements, aggressor_gt_victim=aggressor_gt_victim, init_val=init_val), (
        "synthesized spec is not golden-sound -- this is an internal bug in "
        "synth_engine.py's candidate generation, not a user-facing condition"
    )
    return elements, sorted(remaining.keys())


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


def synthesize_alg(
    registry: list[FaultPrimitive], name: str, *,
    max_elements: int = MAX_ELEMENTS, max_ops: int = MAX_OPS, init_val: int = 1,
) -> SynthResult:
    """Synthesize a new march test targeting every DSL-expressible,
    non-``raw_sv`` primitive in ``registry`` (the four fixed types are never
    targetable -- see module docstring; a ``raw_sv`` entry has no DSL
    description for this module's oracle to interpret, so it is excluded the
    same way)."""
    target = [p for p in registry if p.raw_sv is None]
    elements, uncovered = synthesize_elements(target, max_elements=max_elements, max_ops=max_ops, init_val=init_val)
    targeted_names = [p.name for p in target]
    covered = [n for n in targeted_names if n not in uncovered]
    spec = AlgSpec(name=name, elements=elements)
    return SynthResult(
        spec=spec, targeted=targeted_names, covered=covered, uncovered=uncovered,
    )


# --------------------------------------------------------------------------- #
# Verification-fault generation for the real (Verilator) confirmation pass.
# --------------------------------------------------------------------------- #
def synth_verification_faults(mem, targets: list[FaultPrimitive]) -> list:
    """One concrete FaultRecord per targeted primitive, generalizing
    algo_engine.generate_all_types_faults' hardcoded per-name p0/p1 choices
    via :func:`resolve_params` so custom add_fault_type primitives are
    covered too (generate_all_types_faults' fixed BUILTIN_FAULT_TYPES tuple
    cannot reach them). Coupling-class primitives (sensitize.on=="aggressor")
    are placed with the victim strictly below the aggressor address
    (``aa = va + 1``, never wrapping) -- generate_all_types_faults' own
    ``aa = (va + 1) % depth`` can wrap for a victim placed at the last
    address, silently violating the aggressor_gt_victim convention this
    module's abstract search assumed when it verified detectability; that
    latent gap is not reproduced here."""
    from .algo_engine import FaultRecord  # local import: avoids a hard import-time
                                            # dependency from algo_engine -> synth_engine
                                            # on this pure-logic-vs-execution-engine module
    depth = mem.depth
    dw = mem.data_width
    coupling = [p for p in targets if p.sensitize.on == "aggressor"]
    single_cell = [p for p in targets if p.sensitize.on != "aggressor"]
    records = []
    # Coupling-class: va drawn from [0, depth-1) so aa = va + 1 always stays
    # in-bounds and strictly greater -- never wraps.
    coupling_depth = max(depth - 1, 1)
    for i, p in enumerate(coupling):
        va = (i * 7 + 3) % coupling_depth
        vb = i % dw
        aa, ab = va + 1, vb
        p0, p1 = resolve_params(p)
        records.append(FaultRecord(p.name, va, vb, aa, ab, p0, p1))
    for i, p in enumerate(single_cell):
        va = (i * 7 + 3) % depth
        vb = i % dw
        p0, p1 = resolve_params(p)
        records.append(FaultRecord(p.name, va, vb, 0, 0, p0, p1))
    return records
