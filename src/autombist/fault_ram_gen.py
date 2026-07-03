"""Render fault_ram.sv from a fault-primitive registry.

Each FaultPrimitive is turned into a SystemVerilog case-arm string by pure,
independently-testable Python functions (no simulator needed to check this
logic) -- the Jinja template (fault_ram_template.sv.j2) is a thin loop-and-
print over the already-rendered arms.

Insertion sites and their local variable scope (see fault_ram_template.sv.j2
for the fixed scaffolding these arms are spliced into):
  - static_clamp   -> clamp_static()'s `foreach (FQ[i])` loop.
                      Scope: FQ[i]; target is always mem[FQ[i].va][FQ[i].vb].
  - write_effect,
    on=victim      -> write_op()'s per-bit victim loop.
                      Scope: FQ[i], old[b] (pre-write value), d[b] (written
                      value), nxt[b] (the bit being computed).
  - write_effect,
    on=aggressor   -> write_op()'s per-bit aggressor loop.
                      Scope: FQ[i], up/dn/nt0/nt1 (transition flags on the
                      aggressor bit), dir_match(). Target is always
                      mem[FQ[i].va][FQ[i].vb] (the victim).
  - read_effect
    (victim only)  -> read_op()'s per-bit victim loop.
                      Scope: FQ[i], ea, b, old[b] (pre-read value), rv (the
                      return value being computed), mem[ea][b] (the cell).

Fixed built-ins (SOF, AF_NOACC, AF_ALIAS, CFDS) are NOT rendered here -- they
are hand-written directly in the template; see fault_primitives.py's module
docstring for why.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .fault_primitives import FIXED_TYPE_NAMES, FaultPrimitive
from .generator import _render_template

FIXED_TYPES = FIXED_TYPE_NAMES  # re-exported for callers building the type-code table


def _bit_literal(token: str | None) -> str:
    if token in ("0", "1"):
        return f"1'b{token}"
    if token in ("p0", "p1"):
        return f"FQ[i].{token}[0]"
    raise ValueError(f"not a resolvable bit token: {token!r}")


def _cond_clauses(pre: str, written: str | None) -> str:
    clauses: list[str] = []
    if pre != "x":
        clauses.append(f"old[b] == {_bit_literal(pre)}")
    if written is not None and written != "x":
        clauses.append(f"d[b] == {_bit_literal(written)}")
    return " && ".join(clauses) if clauses else "1'b1"


def _transition_cond(token: str) -> str:
    return {
        "up": "up",
        "down": "dn",
        "either": "(up || dn)",
        "p0": "dir_match(FQ[i].p0, up, dn)",
    }[token]


def render_static_clamp_arm(p: FaultPrimitive) -> str:
    if p.raw_sv is not None:
        return f"T_{p.name}: {p.raw_sv}"
    target = "mem[FQ[i].va][FQ[i].vb]"
    value = _bit_literal(p.effect.value)
    if p.sensitize.on == "aggressor":
        cond = f"mem[FQ[i].aa][FQ[i].ab] == {_bit_literal(p.sensitize.pre)} && {target} != {value}"
    else:
        cond = f"{target} != {value}"
    return f"T_{p.name}: if ({cond}) begin {target} = {value}; FQ[i].hits++; end"


def render_write_victim_arm(p: FaultPrimitive) -> str:
    if p.raw_sv is not None:
        return f"T_{p.name}: {p.raw_sv}"
    cond = _cond_clauses(p.sensitize.pre, p.sensitize.written)
    value = _bit_literal(p.effect.value)
    return f"T_{p.name}: if ({cond}) begin nxt[b] = {value}; FQ[i].hits++; end"


def render_write_aggressor_arm(p: FaultPrimitive) -> str:
    if p.raw_sv is not None:
        return f"T_{p.name}: {p.raw_sv}"
    cond = _transition_cond(p.sensitize.transition)
    target = "mem[FQ[i].va][FQ[i].vb]"
    value = f"~{target}" if p.effect.kind == "invert" else _bit_literal(p.effect.value)
    return f"T_{p.name}: if ({cond}) begin {target} = {value}; FQ[i].hits++; end"


def render_read_victim_arm(p: FaultPrimitive) -> str:
    if p.raw_sv is not None:
        return f"T_{p.name}: {p.raw_sv}"
    cond = _cond_clauses(p.sensitize.pre, None)
    value = _bit_literal(p.effect.value)
    if p.effect.kind == "corrupt_read":
        return f"T_{p.name}: if ({cond}) begin rv[b] = {value}; FQ[i].hits++; end"
    also = _bit_literal(p.effect.also_read) if p.effect.also_read is not None else value
    return f"T_{p.name}: if ({cond}) begin mem[ea][b] = {value}; rv[b] = {also}; FQ[i].hits++; end"


def _dispatch_arm(p: FaultPrimitive) -> tuple[str, str]:
    """Returns (site, rendered_arm). site is one of the four insertion points."""
    if p.category == "static_clamp":
        return "static_clamp", render_static_clamp_arm(p)
    if p.category == "write_effect" and p.sensitize.on == "victim":
        return "write_victim", render_write_victim_arm(p)
    if p.category == "write_effect" and p.sensitize.on == "aggressor":
        return "write_aggressor", render_write_aggressor_arm(p)
    if p.category == "read_effect":
        return "read_victim", render_read_victim_arm(p)
    raise ValueError(f"cannot dispatch primitive '{p.name}' (category={p.category!r})")


def build_type_table(registry: list[FaultPrimitive]) -> list[tuple[str, int]]:
    """Contiguous (name, code) pairs: registry entries first, then the four
    fixed built-ins, so user-added types don't disturb the fixed types' codes
    relative to each other."""
    names = [p.name for p in registry] + list(FIXED_TYPES)
    return [(name, code) for code, name in enumerate(names)]


def render_fault_ram(registry: list[FaultPrimitive], num_ports: int = 1) -> str:
    """Render fault_ram.sv from a fault-primitive registry.

    num_ports=1 (the default, used by every existing caller) renders a
    byte-for-byte identical single-port module to the pre-multi-port
    template: unsuffixed clk/csb/web/wmask/addr/din/dout, no vp/ap fields in
    the fault struct, no port parameter on write_op()/read_op(). num_ports=2
    renders a second, explicit-suffix port bus (clk0/csb0/.../clk1/csb1/...,
    mirroring rtl/sram_model_2rw.sv's naming convention) and threads a `port`
    parameter through write_op()/read_op() so the aggressor side of the
    coupling-class primitives (CFIN/CFID/CFST/CFDS) can match against the
    accessing port via the fault struct's new vp/ap fields. Only num_ports 1
    and 2 are supported.
    """
    if num_ports not in (1, 2):
        raise ValueError(f"num_ports must be 1 or 2, got {num_ports!r}")

    seen: set[str] = set()
    for p in registry:
        if p.name in seen:
            raise ValueError(f"duplicate fault type name in registry: {p.name}")
        if p.name in FIXED_TYPE_NAMES:
            raise ValueError(f"registry entry '{p.name}' collides with a fixed built-in type")
        seen.add(p.name)

    type_table = build_type_table(registry)
    sites: dict[str, list[str]] = {
        "static_clamp": [], "write_victim": [], "write_aggressor": [], "read_victim": [],
    }
    for p in registry:
        site, arm = _dispatch_arm(p)
        sites[site].append(arm)

    context: dict[str, Any] = {
        "type_table": type_table,
        "static_clamp_arms": sites["static_clamp"],
        "write_victim_arms": sites["write_victim"],
        "write_aggressor_arms": sites["write_aggressor"],
        "read_victim_arms": sites["read_victim"],
        "num_ports": num_ports,
    }
    return _render_template(context, "fault_ram_template.sv.j2")


def render_and_write(registry: list[FaultPrimitive], path: Path, num_ports: int = 1) -> Path:
    path = Path(path)
    path.write_text(render_fault_ram(registry, num_ports=num_ports), encoding="utf-8")
    return path


def registry_hash(registry: list[FaultPrimitive]) -> str:
    """A stable hash of the registry's semantic content, for build caching:
    re-render/recompile fault_ram.sv only when the registry actually changed."""
    import hashlib
    import json

    from .fault_primitives import to_dict

    payload = json.dumps([to_dict(p) for p in registry], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
