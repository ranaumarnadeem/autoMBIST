# Challenges

Some of what we ran into while building this, in plain terms.

## Toolchain version sensitivity

Different builds of `magic` gave inconsistent results when running DRC on the
same generated macro — the check would report very different violation counts
depending on which build was used, in a way that turned out to be a magic
version issue rather than a real problem with the macro. We worked around it
by building the specific magic version the flow was actually validated
against and using that consistently.

## OpenRAM spare columns

Generating macros with spare *columns* enabled surfaced a couple of rough
edges: one geometry hit an edge case in how a write mask interacted with the
spare column, and characterization crashed on another. Both are fixed in the
vendored OpenRAM copy — the write-mask one at the root cause (the emitted
Verilog was sizing the base write to exclude the spare columns, orphaning the
top data bit), the characterization one by making that step non-fatal so it
cannot abort before the GDS/LEF are written.

For a long time we sidestepped the whole area by using row-only spares, which
matched the repair granularity we then needed. That is no longer the case:
column repair is implemented and proven, and the proof deliberately runs
against a macro generated *with* a spare column so the behavioural model's
spare-write semantics are checked against real OpenRAM output rather than only
against themselves. One genuine limitation remains, and it is a modelling one
rather than a toolchain one: under column muxing OpenRAM shares a single
spare-column set across every word in a physical row, which the repair model's
global bit-lane view does not express — so configs with more than one word per
row are rejected rather than silently mis-repaired.

## LEF units

OpenRAM's LEF output declares a coordinate grid that doesn't match what the
sky130A PDK expects, even though the underlying coordinates (and the GDS) are
already on the right grid. It's a small, mechanical fix once you know to look
for it — `autombist fix-lef-units` applies it automatically now.

## Real SPICE-based timing characterization

Getting a real Liberty timing view (rather than OpenRAM's default analytical
delay model) means running SPICE-level characterization, which is
considerably heavier than anything else in the flow — both in time and in
memory. We hit resource limits running several of these at once on a typical
development machine. This is still open; see {doc}`roadmap`.

## Merged full-hierarchy DRC

Checking a macro's actual GDS polygons as part of one merged pass (rather
than treating the macro as an opaque boundary) needs a specific layer in the
macro's GDS that OpenRAM's output doesn't include. Also still open.

Along the way we hit a related, more concrete problem: OpenRAM's own
bitcell/periphery cells use a handful of GDS layer/datatype pairs (things
like `CFOMDROP`, `CNTMADD`) that are legitimate sky130 mask-operation layers,
but are internal to the macro's own geometry and aren't in the local
Magic/KLayout DRC deck — so they get flagged as "unknown layer/datatype in
boundary". That's a known OpenRAM/open_pdks integration quirk, not a defect
in autoMBIST's own logic (see
[librelane/librelane#519](https://github.com/librelane/librelane/issues/519)),
but Magic and KLayout both treat DRC violations as fatal by default, so
whether that macro-internal noise aborted a `harden --run` outright used to
depend on whatever LibreLane defaulted to upstream rather than anything this
project pinned itself — not actually reproducible. `autombist harden` now
sets `ERROR_ON_MAGIC_DRC` and `ERROR_ON_KLAYOUT_DRC` to `false` itself
whenever a config declares `macros:`, alongside the `MAGIC_DRC_USE_GDS`/
`RUN_KLAYOUT_XOR` stance above, so those counts are deterministically
advisory now rather than accidental. They're still nonzero counts coming
from inside the macro, though — closing that for real is exactly what a
proper merged-hierarchy pass would do.

## Toolchain setup on Windows/WSL

Wiring up the physical-design tools (magic, netgen, klayout, LibreLane) took
some trial and error to get consistent within a WSL environment — mostly
around PATH resolution and matching library versions. Nothing unusual for a
mixed open-source EDA toolchain, just worth knowing going in.

`autombist doctor` now surfaces exactly this up front — it checks for
make/iverilog/cocotb/verilator/yosys/nix/bash/magic/netgen and the
`FAULTFLOW_HOME` env var in one shot and prints which commands each one
unblocks, instead of finding out via a failed command halfway through a run.
`harden --run` and `macro-signoff` also check for `nix`/`bash` before
invoking and point Windows users at WSL or Git Bash directly, rather than
surfacing a raw subprocess traceback.
