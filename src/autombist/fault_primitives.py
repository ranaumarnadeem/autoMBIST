"""The fault-primitive DSL: a declarative description of a memory functional
fault, and the registry of built-ins that reproduces fault_ram.sv's behavior.

Why a DSL at all: fault_ram.sv hardcodes 21 fault-type case arms/insertion
sites across five functions/blocks (clamp_static, write_op's victim/
aggressor/row-membership checks, read_op's victim loop). `add_fault_type`
lets a researcher define a NEW fault type without editing SystemVerilog --
fault_ram_gen.py turns a list of FaultPrimitive into the equivalent case
arms.

Coverage: 15 of the 21 built-ins fit this DSL cleanly. Six do not, and stay
as fixed, hand-written scaffolding in the template (see fault_ram_gen.py):
  - SOF: its read-path arm reads the module-level `dout` register directly
    (cross-op state), which is outside the read_op() locals this DSL models.
  - AF_NOACC / AF_ALIAS: these run in an address-decoder *pre-pass*, before
    the per-bit loop, and mutate the effective address `ea` for every
    subsequent bit -- a structurally different insertion site than a per-bit
    case arm.
  - CFDS: its single P0 parameter selects among five distinct sensitizing
    conditions spanning *both* the write-aggressor and read-aggressor loops.
    It is really a union of several fault types under one name, not
    expressible as a single-site effect.
  - DRF (Workstream K): sensitized by ELAPSED IDLE TIME with no memory
    access at all -- it doesn't fit any of the three _VALID_CATEGORIES
    (static_clamp/write_effect/read_effect are all triggered by clamp_static/
    write_op/read_op being CALLED, i.e. by an access). DRF needs its own
    always_ff block, unconditional on csb, that this DSL has no concept of;
    there is no per-cell value-transition walk for a synthesizer to search
    over either, so DRF is also structurally excluded from Workstream J's
    march-test synthesizer (see synth_engine.py's module docstring).
  - HSD (Workstream L): Half-Select Disturb, sensitized by a write to ANY
    OTHER address sharing the victim's physical row (row(addr) = addr /
    WORDS_PER_ROW; see engine/README.md and flow/multimem/mbist/README.md's
    pre-existing `words_per_row` finding). Its aggressor-selection mode is
    row co-membership, not a fixed (aaddr, abit) pair, so it doesn't fit the
    write_effect/aggressor category's exact-address-match insertion site --
    it needs its own per-word (not per-bit) check in write_op(), evaluated
    once per call rather than per victim bit.
A user-defined type may use `raw_sv` to hand-write its arm verbatim for any
of the three DSL-coverable categories (see the module docstrings on codegen
in fault_ram_gen.py for the exact per-site variable scope). addr_decoder-
style faults are not supported by add_fault_type in v1 (use raw_sv is not
enough for a pre-pass site) -- AF_NOACC/AF_ALIAS remain the only ones. DRF's
idle-time site and HSD's row-membership site are both brand-new insertion
shapes, also not reachable via raw_sv/add_fault_type.

Defect-to-FaultPrimitive adapter contract (future IFA/SPICE campaign, Tier 3
Workstream Q -- scaffolding only, not implemented here): (1) enumerate
candidate defect sites -- resistive opens/shorts/bridges, per Shen, Maly &
Ferguson, "Inductive Fault Analysis of MOS Integrated Circuits," IEEE D&T
1985 (foundational IFA); (2) inject each as a resistor-value sweep in
SPICE/Spectre; (3) classify the resulting read/write behavior against this
module's existing static_clamp/write_effect/read_effect categories, per
Hamdioui & van de Goor, "An Experimental Analysis of Spot Defects in SRAMs:
Realistic Fault Models and Tests," ATS 2000 (the exact recipe: inject spot
defects, classify to functional faults, validate which textbook models
actually occur, derive a new march test from the results); (4) emit the
result as a FaultPrimitive / add_fault_type call. This module's
Sensitize/Effect shape is already the correct landing target for step 4 --
no schema change needed here. `FaultRecord.weight` (algo_engine.py) is the
matching landing spot on the campaign side: an optional relative-likelihood
field a future weighted campaign can populate from IFA-derived defect
densities, without a fault-list format break.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Names that are never user-redefinable: baked as fixed scaffolding in every
# generated fault_ram.sv (see fault_ram_gen.FIXED_TYPES).
FIXED_TYPE_NAMES = ("SOF", "AF_NOACC", "AF_ALIAS", "CFDS", "DRF", "HSD")

_VALID_CATEGORIES = ("static_clamp", "write_effect", "read_effect")
_VALID_BIT_TOKENS = ("0", "1", "p0", "p1", "x")
_VALID_TRANSITIONS = ("up", "down", "either", "p0", "x")
_VALID_ON = ("victim", "aggressor")
_VALID_EFFECT_KINDS = ("force", "invert", "block_write", "corrupt_read", "force_read")
_VALID_PORTS = ("0", "1", "x")
_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class FaultPrimitiveError(ValueError):
    """Raised when a fault-primitive spec is invalid or unsupported."""


@dataclass(slots=True)
class Sensitize:
    pre: str = "x"          # required pre-op value of the gating bit: "0"|"1"|"p0"|"p1"|"x"
    written: str = "x"      # required written value (write_effect/victim only): same tokens
    transition: str = "x"   # write_effect/aggressor only: "up"|"down"|"either"|"p0"|"x"
    on: str = "victim"      # whose site gates the arm: "victim" | "aggressor"
    port: str = "x"         # which port the sensitizing op must occur on: "0"|"1"|"x" (wildcard;
                             # "x" means "not part of the sensitizing condition, matches on address
                             # alone" -- what every existing built-in implicitly means today)
    agg_pre: str = "x"      # the AGGRESSOR cell's required held value: "0"|"1"|"p0"|"p1"|"x".
                             # Orthogonal to `on`, which selects whose OPERATION sensitizes;
                             # this constrains the other cell's STATE at that moment, which is
                             # what makes a two-cell condition expressible (the van de Goor
                             # CFtr/CFwd/CFrd/CFir/CFdrd family). "x" = unconstrained, which is
                             # what every pre-existing built-in means, so the emitted RTL for
                             # them stays byte-identical.


@dataclass(slots=True)
class Effect:
    kind: str                       # force|invert|block_write|corrupt_read|force_read
    value: str | None = None        # "0"|"1"|"p0"|"p1"
    also_read: str | None = None    # force_read only (DRDF-style): mem gets `value`, read returns this
    target: str = "victim"          # must be "victim" -- validate() rejects anything else, since
                                     # no codegen arm can target the aggressor's own storage


@dataclass(slots=True)
class FaultPrimitive:
    name: str
    category: str                   # static_clamp | write_effect | read_effect
    sensitize: Sensitize = field(default_factory=Sensitize)
    effect: Effect = field(default_factory=lambda: Effect(kind="force"))
    params_help: dict[str, str] = field(default_factory=dict)
    raw_sv: str | None = None       # escape hatch: verbatim arm body, DSL fields ignored for codegen


def validate(prim: FaultPrimitive, *, existing_names: set[str]) -> None:
    if not _NAME_RE.match(prim.name):
        raise FaultPrimitiveError(
            f"invalid fault type name '{prim.name}': must match {_NAME_RE.pattern} "
            "(uppercase letters/digits/underscore, starting with a letter)"
        )
    if prim.name in FIXED_TYPE_NAMES:
        raise FaultPrimitiveError(f"'{prim.name}' is a built-in fixed type and cannot be redefined")
    if prim.name in existing_names:
        raise FaultPrimitiveError(f"fault type '{prim.name}' is already registered")
    if prim.category not in _VALID_CATEGORIES:
        raise FaultPrimitiveError(
            f"category must be one of {_VALID_CATEGORIES} (addr_decoder-style faults are not "
            "supported by add_fault_type; AF_NOACC/AF_ALIAS remain the only address-decoder faults)"
        )
    # Checked before the raw_sv early return below: port is the one DSL field
    # that still constrains a raw_sv primitive, because codegen cannot inject a
    # port gate into a verbatim arm body.
    if prim.sensitize.port not in _VALID_PORTS:
        raise FaultPrimitiveError(f"sensitize.port must be one of {_VALID_PORTS}")
    if prim.sensitize.agg_pre not in _VALID_BIT_TOKENS:
        raise FaultPrimitiveError(f"sensitize.agg_pre must be one of {_VALID_BIT_TOKENS}")
    if prim.raw_sv is not None:
        if prim.sensitize.port != "x":
            raise FaultPrimitiveError(
                f"sensitize.port={prim.sensitize.port!r} cannot be combined with raw_sv: "
                "the port gate is emitted by wrapping the generated arm's condition, and "
                "a raw_sv arm body is copied verbatim. Gate on the arm's own `port` "
                "argument inside the raw_sv text instead, and leave sensitize.port='x'"
            )
        if prim.sensitize.agg_pre != "x":
            # Same structural reason as port: every render_*_arm returns the raw_sv
            # body verbatim before building a condition, so the clause would be
            # accepted and then silently dropped.
            raise FaultPrimitiveError(
                f"sensitize.agg_pre={prim.sensitize.agg_pre!r} cannot be combined with "
                "raw_sv: the aggressor-state gate is emitted as a clause on the "
                "generated arm's condition, and a raw_sv arm body is copied verbatim. "
                "Test mem[FQ[i].aa][FQ[i].ab] yourself inside the raw_sv text instead, "
                "and leave sensitize.agg_pre='x'"
            )
        return  # remaining DSL fields are not codegen-relevant for a raw_sv primitive

    if prim.sensitize.on not in _VALID_ON:
        raise FaultPrimitiveError(f"sensitize.on must be one of {_VALID_ON}")
    if prim.sensitize.agg_pre != "x" and prim.sensitize.on == "aggressor":
        # on="aggressor" already means the aggressor's own ACCESS sensitizes the
        # arm; additionally constraining the state it HOLDS describes a condition
        # no codegen site evaluates (the aggressor arms key on the transition being
        # written, not on a held value). The two are alternative ways to involve
        # the aggressor, not composable ones.
        raise FaultPrimitiveError(
            "sensitize.agg_pre cannot be combined with sensitize.on='aggressor': "
            "'aggressor' already gates on the aggressor's own access, while agg_pre "
            "gates on the value it holds while the VICTIM is accessed. Use "
            "on='victim' with agg_pre for a two-cell state condition, or "
            "on='aggressor' with a transition for an aggressor-triggered one"
        )
    if prim.sensitize.pre not in _VALID_BIT_TOKENS:
        raise FaultPrimitiveError(f"sensitize.pre must be one of {_VALID_BIT_TOKENS}")
    if prim.sensitize.pre == prim.sensitize.agg_pre and prim.sensitize.pre in ("p0", "p1"):
        # Both would render to the same FQ[i].pN[0], so one parameter would be
        # silently doing two jobs and the victim/aggressor states could never
        # differ -- which is exactly the case a two-cell fault exists to express.
        raise FaultPrimitiveError(
            f"sensitize.pre and sensitize.agg_pre both resolve to {prim.sensitize.pre!r}: "
            "they would share one parameter, so the victim and aggressor states could "
            "never differ. Give one of them a literal '0'/'1', or use the other parameter"
        )
    if prim.sensitize.written not in _VALID_BIT_TOKENS:
        raise FaultPrimitiveError(f"sensitize.written must be one of {_VALID_BIT_TOKENS}")
    if prim.sensitize.transition not in _VALID_TRANSITIONS:
        raise FaultPrimitiveError(f"sensitize.transition must be one of {_VALID_TRANSITIONS}")
    if prim.effect.kind not in _VALID_EFFECT_KINDS:
        raise FaultPrimitiveError(f"effect.kind must be one of {_VALID_EFFECT_KINDS}")
    if prim.effect.kind != "invert" and prim.effect.value not in _VALID_BIT_TOKENS[:-1]:
        raise FaultPrimitiveError("effect.value must be one of '0','1','p0','p1' (required unless kind=invert)")
    if prim.effect.also_read is not None and prim.effect.also_read not in _VALID_BIT_TOKENS[:-1]:
        raise FaultPrimitiveError("effect.also_read must be one of '0','1','p0','p1'")
    if prim.effect.target != "victim":
        # Every codegen arm (fault_ram_gen.py's render_*_arm functions) writes
        # only to the victim's own cell/register -- rv[b]/nxt[b]/mem[addr] at
        # the victim's address -- with no code path that ever targets the
        # aggressor's storage. "aggressor" would be silently accepted and
        # applied to the victim anyway, exactly like an unread config key.
        # A coupling fault's aggressor-triggered condition is already
        # expressed by sensitize.on='aggressor'; the effect landing on the
        # victim is the whole point of a coupling fault, not a gap to fill.
        raise FaultPrimitiveError(
            f"effect.target must be 'victim' (got {prim.effect.target!r}) -- no "
            "codegen arm can target the aggressor's own storage. Use "
            "sensitize.on='aggressor' to gate the sensitizing condition on the "
            "aggressor's access; the effect still lands on the victim"
        )

    if prim.category == "static_clamp":
        if prim.effect.kind != "force":
            raise FaultPrimitiveError("static_clamp primitives must use effect.kind='force'")
        if prim.sensitize.port != "x":
            # A static clamp is a STORAGE-level invariant, not an access event:
            # its arm is emitted into clamp_static(), which rewrites mem[] and is
            # re-asserted after every access on every port. Gating that on a port
            # would still corrupt the stored cell for BOTH ports -- the opposite
            # of a port-specific defect -- so the constraint is rejected rather
            # than silently mis-modelled. (This is the same structural reason
            # CFST cannot honour a fault line's APORT.)
            raise FaultPrimitiveError(
                f"sensitize.port={prim.sensitize.port!r} is not meaningful for a "
                "static_clamp: the clamp rewrites stored state, which every port "
                "then sees, so it cannot be scoped to one port. Model a "
                "port-specific read-path defect as category='read_effect' "
                "(which corrupts only the accessing port's returned value)"
            )
    elif prim.category == "write_effect":
        if prim.sensitize.on == "victim" and prim.effect.kind not in ("force", "block_write"):
            raise FaultPrimitiveError("write_effect on=victim primitives must use force or block_write")
        if prim.sensitize.on == "aggressor" and prim.effect.kind not in ("force", "invert"):
            raise FaultPrimitiveError("write_effect on=aggressor primitives must use force or invert")
    elif prim.category == "read_effect":
        if prim.sensitize.on != "victim":
            raise FaultPrimitiveError(
                "read_effect primitives must use sensitize.on='victim' (aggressor-triggered read "
                "effects like CFDS are not supported by the DSL; see the fault_primitives module docstring)"
            )
        if prim.effect.kind not in ("corrupt_read", "force_read"):
            raise FaultPrimitiveError("read_effect primitives must use corrupt_read or force_read")


def to_dict(prim: FaultPrimitive) -> dict[str, Any]:
    return {
        "name": prim.name,
        "category": prim.category,
        "sensitize": {
            "pre": prim.sensitize.pre, "written": prim.sensitize.written,
            "transition": prim.sensitize.transition, "on": prim.sensitize.on,
            "port": prim.sensitize.port, "agg_pre": prim.sensitize.agg_pre,
        },
        "effect": {
            "kind": prim.effect.kind, "value": prim.effect.value,
            "also_read": prim.effect.also_read, "target": prim.effect.target,
        },
        "params_help": dict(prim.params_help),
        "raw_sv": prim.raw_sv,
    }


def from_dict(data: dict[str, Any]) -> FaultPrimitive:
    sens = data.get("sensitize") or {}
    eff = data.get("effect") or {}
    return FaultPrimitive(
        name=str(data["name"]),
        category=str(data["category"]),
        sensitize=Sensitize(
            pre=str(sens.get("pre", "x")), written=str(sens.get("written", "x")),
            transition=str(sens.get("transition", "x")), on=str(sens.get("on", "victim")),
            port=str(sens.get("port", "x")), agg_pre=str(sens.get("agg_pre", "x")),
        ),
        effect=Effect(
            kind=str(eff.get("kind", "force")), value=eff.get("value"),
            also_read=eff.get("also_read"), target=str(eff.get("target", "victim")),
        ),
        params_help=dict(data.get("params_help") or {}),
        raw_sv=data.get("raw_sv"),
    )


# --------------------------------------------------------------------------- #
# The 15 DSL-expressible built-ins, semantically identical to fault_ram.sv.
# --------------------------------------------------------------------------- #
def default_registry() -> list[FaultPrimitive]:
    return [
        FaultPrimitive("SA0", "static_clamp", Sensitize(), Effect(kind="force", value="0")),
        FaultPrimitive("SA1", "static_clamp", Sensitize(), Effect(kind="force", value="1")),
        FaultPrimitive(
            "CFST", "static_clamp",
            Sensitize(pre="p0", on="aggressor"), Effect(kind="force", value="p1"),
            params_help={"p0": "aggressor hold state (0/1)", "p1": "forced victim value (0/1)"},
        ),
        FaultPrimitive(
            "TF0", "write_effect", Sensitize(pre="0", written="1"), Effect(kind="block_write", value="0"),
        ),
        FaultPrimitive(
            "TF1", "write_effect", Sensitize(pre="1", written="0"), Effect(kind="block_write", value="1"),
        ),
        FaultPrimitive(
            "WDF0", "write_effect", Sensitize(pre="0", written="0"), Effect(kind="force", value="1"),
        ),
        FaultPrimitive(
            "WDF1", "write_effect", Sensitize(pre="1", written="1"), Effect(kind="force", value="0"),
        ),
        FaultPrimitive(
            "CFIN", "write_effect", Sensitize(transition="p0", on="aggressor"), Effect(kind="invert"),
            params_help={"p0": "aggressor transition (0=up, 1=down, 2=either)"},
        ),
        FaultPrimitive(
            "CFID", "write_effect", Sensitize(transition="p0", on="aggressor"), Effect(kind="force", value="p1"),
            params_help={"p0": "aggressor transition (0=up, 1=down, 2=either)", "p1": "forced victim value"},
        ),
        FaultPrimitive("IRF0", "read_effect", Sensitize(pre="0"), Effect(kind="corrupt_read", value="1")),
        FaultPrimitive("IRF1", "read_effect", Sensitize(pre="1"), Effect(kind="corrupt_read", value="0")),
        FaultPrimitive("RDF0", "read_effect", Sensitize(pre="0"), Effect(kind="force_read", value="1")),
        FaultPrimitive("RDF1", "read_effect", Sensitize(pre="1"), Effect(kind="force_read", value="0")),
        FaultPrimitive(
            "DRDF0", "read_effect", Sensitize(pre="0"), Effect(kind="force_read", value="1", also_read="0"),
        ),
        FaultPrimitive(
            "DRDF1", "read_effect", Sensitize(pre="1"), Effect(kind="force_read", value="0", also_read="1"),
        ),
        # --- Two-cell coupling family -------------------------------------- #
        # Al-Ars, Hamdioui & van de Goor, "Space of DRAM Fault Models and
        # Corresponding Testing", DATE 2006, Table 2 (two-cell static FFMs).
        # Notation <Sa; Sv/F/R>: Sa on the aggressor, Sv on the victim, F the
        # victim's resulting stored value, R what a read returns.
        #
        # Each is the single-cell FFM of the same shape (TF/WDF/RDF/IRF/DRDF),
        # gated on the aggressor's HELD state -- so each collapses that table
        # row's two aggressor cases into one type parameterized by P0. The
        # suffix is the VICTIM's pre-state, following TF0/WDF0/RDF0 above;
        # note the paper names transition faults by write direction instead,
        # so this repo's TF0 is the paper's TF1 (a pre-existing divergence,
        # documented in engine/README.md rather than renamed).
        FaultPrimitive(
            "CFTR0", "write_effect", Sensitize(pre="0", written="1", agg_pre="p0"),
            Effect(kind="block_write", value="0"),
            params_help={"p0": "aggressor hold state (0/1)"},          # <a; 0w1/0/->
        ),
        FaultPrimitive(
            "CFTR1", "write_effect", Sensitize(pre="1", written="0", agg_pre="p0"),
            Effect(kind="block_write", value="1"),
            params_help={"p0": "aggressor hold state (0/1)"},          # <a; 1w0/1/->
        ),
        FaultPrimitive(
            "CFWD0", "write_effect", Sensitize(pre="0", written="0", agg_pre="p0"),
            Effect(kind="force", value="1"),
            params_help={"p0": "aggressor hold state (0/1)"},          # <a; 0w0/1/->
        ),
        FaultPrimitive(
            "CFWD1", "write_effect", Sensitize(pre="1", written="1", agg_pre="p0"),
            Effect(kind="force", value="0"),
            params_help={"p0": "aggressor hold state (0/1)"},          # <a; 1w1/0/->
        ),
        FaultPrimitive(
            "CFRD0", "read_effect", Sensitize(pre="0", agg_pre="p0"),
            Effect(kind="force_read", value="1"),
            params_help={"p0": "aggressor hold state (0/1)"},          # <a; 0r0/1/1>
        ),
        FaultPrimitive(
            "CFRD1", "read_effect", Sensitize(pre="1", agg_pre="p0"),
            Effect(kind="force_read", value="0"),
            params_help={"p0": "aggressor hold state (0/1)"},          # <a; 1r1/0/0>
        ),
        FaultPrimitive(
            "CFIR0", "read_effect", Sensitize(pre="0", agg_pre="p0"),
            Effect(kind="corrupt_read", value="1"),
            params_help={"p0": "aggressor hold state (0/1)"},          # <a; 0r0/0/1>
        ),
        FaultPrimitive(
            "CFIR1", "read_effect", Sensitize(pre="1", agg_pre="p0"),
            Effect(kind="corrupt_read", value="0"),
            params_help={"p0": "aggressor hold state (0/1)"},          # <a; 1r1/1/0>
        ),
        FaultPrimitive(
            "CFDRD0", "read_effect", Sensitize(pre="0", agg_pre="p0"),
            Effect(kind="force_read", value="1", also_read="0"),
            params_help={"p0": "aggressor hold state (0/1)"},          # <a; 0r0/1/0>
        ),
        FaultPrimitive(
            "CFDRD1", "read_effect", Sensitize(pre="1", agg_pre="p0"),
            Effect(kind="force_read", value="0", also_read="1"),
            params_help={"p0": "aggressor hold state (0/1)"},          # <a; 1r1/0/1>
        ),
    ]
