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
already on the right grid. It's a small, mechanical fix —
`autombist fix-lef-units` applies it automatically now.

## Macro-internal DRC noise

OpenRAM's own bitcell/periphery cells use a handful of GDS layer/datatype
pairs (things like `CFOMDROP`, `CNTMADD`) that are legitimate sky130
mask-operation layers, but are internal to the macro's own geometry and
aren't in the local Magic/KLayout DRC deck — so they get flagged as "unknown layer/datatype in
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
from inside the macro, though: macro-internal geometry belongs to whoever
produced the macro, not to this project.

## OpenRAM macro-level DRC/LVS signoff

A separate, more serious issue than the layer/datatype noise above: the
per-macro DRC/LVS check (`flow/multimem/signoff/run_macro_signoff.sh`) was
found to have never actually run against real PDK data — `PDK_ROOT` was set
in the script but never `export`ed, so a "clean" 0-violation result meant
"nothing was checked," not "nothing was found." Fixing that surfaced a real
LVS mismatch, root-caused (not merely worked around) to the vendored
`OpenRAM/` checkout predating two upstream fixes for a wordline-pin-numbering
defect in the sky130 replica bitcell array. Reproducing OpenRAM's own
authoritative regeneration flow independently confirms the same failure
inside OpenRAM's own module, before any openMBIST-authored logic runs.

Until `OpenRAM/` is updated past those two commits, neither this repo's
raw-GDS check nor OpenRAM's own regeneration gives a clean signoff answer,
and the demo macros' committed `.gds`/`.lvs.sp` pairs remain genuinely
unverified rather than confirmed either good or bad. This doesn't implicate
autoMBIST's own generated RTL — physical macro signoff is the memory
generator's responsibility, not the integrator's — and it doesn't affect the
top-level LibreLane P&R closure, which treats each macro as opaque hard IP.
See `flow/multimem/mbist/README.md`'s "Honest signoff caveats" or
`run_macro_signoff.sh`'s own header for the full root-cause writeup.

## Toolchain setup on Windows/WSL

Wiring up the physical-design tools (magic, netgen, klayout, LibreLane) took
some trial and error to get consistent within a WSL environment — mostly
around PATH resolution and matching library versions.

`autombist doctor` now surfaces exactly this up front — it checks for
make/iverilog/cocotb/verilator/yosys/nix/bash/magic/netgen/tkinter and the
`FAULTFLOW_HOME` env var in one shot and prints which commands each one
unblocks, instead of finding out via a failed command halfway through a run.
`harden --run` checks for `nix` and `macro-signoff` checks for `bash` before
invoking, each pointing Windows users at WSL or Git Bash directly rather than
surfacing a raw subprocess traceback.
