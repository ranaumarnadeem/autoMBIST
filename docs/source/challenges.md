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
spare column, and characterization crashed on another. We worked around both
locally and, for the macros in this repository, moved to row-only spares,
which sidesteps the issue entirely and matches the repair granularity we
actually needed.

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

## Toolchain setup on Windows/WSL

Wiring up the physical-design tools (magic, netgen, klayout, LibreLane) took
some trial and error to get consistent within a WSL environment — mostly
around PATH resolution and matching library versions. Nothing unusual for a
mixed open-source EDA toolchain, just worth knowing going in.
