# Changelog

All notable changes to autoMBIST are documented here, in roughly [Keep a
Changelog](https://keepachangelog.com/) style, grouped by theme rather than
by individual commit (there are 200+ of those).

**A note on versioning:** `0.1.0` was set in `pyproject.toml` during the
PyPI-to-Nix migration but sat untagged for some time while `main` fell
behind `dev`. This release merges `dev` into `main` and formally tags
`0.1.0` as the first Nix-era release — see "Legacy releases" below for
everything that predates the migration.

## [Unreleased]

## [0.1.0] — 2026-09-22

### Added — MBIST core
- Multi-port memory support: `march-1r1w` (independent read/write ports)
  and `march-2rw` (two independent read/write ports), with cross-port
  coupling-fault injection and a named `ports:` config map alongside the
  original flat single-port form.
- The `checkerboard` algorithm, on both the research-shell path and the
  RTL wrapper-generation path — the first algorithm whose per-cycle value
  depends on address (not just phase/op-step), needed a new address-LSB
  input threaded through the DSL and both march engines.
- Dynamic (2-operation) fault model: 12 new fault primitives
  (`DYN_RDF`/`DYN_DRDF`/`DYN_IRF` × 4 sensitization orders) requiring a
  shared last-operation register and a new `sensitize.prev` DSL field, plus
  `march_raw1`, a 13-element diagnostic reference algorithm for the family.
- A march-test synthesizer that constructs a test directly from the fault
  model instead of only grading a hand-written one.
- A shared MBIST controller across multiple physical memories
  (`topology: shared-bus`): one algorithm controller time-multiplexed
  across N memories via a `mem_sel_q` sequencer, instead of one controller
  instance per memory.

### Added — Redundancy repair (BIRA/BISR)
- BIRA (redundancy analysis): a 2D solver (row + column) using a
  must-repair fixed point plus backtracking search.
- BISR: tester-driven repair (external row/column remap, no special macro
  views needed) and a fully autonomous on-chip self-repair FSM
  (analyze → decide → verify, no tester) for every march algorithm.
- On-chip column repair, first tester-driven then fully autonomous
  (`onchip_col_repair: true`), via a new heuristic 2D analyzer
  (`onchip_2d_repair_analyzer`) consuming a per-bit fail-bitmask stream.
- Repair persistence across reset: a saved repair signature can be
  reloaded into the on-chip analyzer before any access.
- On-chip diagnosis logging (`onchip_diagnosis: true`): a full-range
  fail-address accumulator that sees past the physical repair budget.
- **On-chip self-repair (row AND column) combined with the shared-bus
  controller** — a second orchestration mode in the shared-bus sequencer,
  one independent analyzer/controller/remap per memory, proven with real
  cross-memory-isolation scenarios (distinct defects in different memory
  banks, repaired independently, zero interference).

### Added — Test access / physical
- `wrap-test-access`: wraps a generated design's control/status ports
  with an IEEE 1149.1/1687 (JTAG/IJTAG) test-access network via the
  external [warptap](https://github.com/ranaumarnadeem/warptap) package,
  including wide (multi-bit) diagnosis/persistence/repair-port signals.
- A proven LibreLane hardening recipe (RTL-to-GDS closure on sky130) for
  real OpenRAM macros, including self-repair-wrapped variants, plus a
  `harden`/`fix-lef-units`/`macro-signoff` CLI surface wrapping it.
- An SoC-level demonstration: an unmodified RV32I core (PicoRV32) booting
  and running a real program through self-repaired memory.
- FaultFlow controller-logic grading (`grade-controller`, `run
  --faultflow`): blackboxes the memory macro and runs FaultFlow's
  scan-ATPG structural grading against the MBIST *controller* logic,
  emitting a self-contained, re-runnable bundle.

### Changed
- Release/distribution strategy switched from PyPI to Nix — `flake.nix`
  pins the exact toolchain CI uses; the PyPI-publish GitHub Actions
  workflow was removed. See [SECURITY.md](SECURITY.md).
- CI moved to Nix-based Ubuntu runners with a pinned toolchain
  (Icarus 13.0, Verilator 5.048, Yosys 0.62, Python 3.11, cocotb 2.x) and a
  90% coverage gate, replacing an earlier drifting `apt-get`-based setup.

### Fixed
- A latent LibreLane DRC-gating bug where macro-internal DRC was always
  fatal, silently failing every hardening run until corrected.
- `run_simulation()` never resolved shared-bus wrapper file paths
  correctly — no shared-bus config could actually be simulated end to end
  (only elaborated/linted) until this was found and fixed.
- A duplicate RTL instantiation bug where the original (pre-shared-bus)
  self-repair block rendered unconditionally alongside the new per-memory
  shared-bus self-repair loop.

## Legacy releases (PyPI-published, pre-Nix)

These predate the Nix-based release strategy above and are no longer the
supported install path (see [SECURITY.md](SECURITY.md)) — kept here for
history, not as a current reference.

### v1.1.2 — 2026-05-26
- Dependency bump: `cocotb`/`cocotb-tools` versions.

### v1.1.1 — 2026-05-11
- Path-resolution fix for a production install issue.

### v1.1.0 — 2026-05-11
- A standalone, independent CLI (separate from ad-hoc scripts), with
  unified `generate`+`simulate` (`run`), verbose logging, and clearer
  reporting output.
- Direct OpenRAM SRAM-cell synthesis support from within autoMBIST.
- Better transition-fault handling and reporting in the saboteur/wrapper
  templates.

### v1.0.1 / v0.1.1 — 2026-04-30
- Packaging metadata fix only.

### v1.0.0 — 2026-04-30
- First public release: March-C MBIST wrapper generation, saboteur-based
  fault injection (stuck-at + transition faults), fault-coverage
  calculation, and an initial March-RAW algorithm — packaged as
  `autombist` and published to PyPI.
