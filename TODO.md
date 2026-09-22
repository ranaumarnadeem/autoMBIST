# TODO

Running list of known-open work. Not a promise or a deadline — an honest
snapshot, same spirit as `docs/source/roadmap.md`. Update this alongside the
work it describes; don't let it silently go stale the way
`docs/source/roadmap.md` did (see below).

## In progress

- **Milestone 4 — Diagnosis & yield analysis.** Scoped in
  `docs/diagnosis-yield-analysis-plan.md` (gitignored, local). Shipped so
  far: the Monte Carlo repair-yield sweep harness and `autombist
  yield-sweep` CLI (schema-versioned JSON report). Still open:
  - Empirical yield-multiplier metric (secondary, explicitly-synthetic
    disclaimer) — plan doc step 5.
  - Docs (`engine/README.md`, `docs/source/roadmap.md`) once real measured
    numbers exist against a real config — step 6.
  - Stretch items: bitmap shape/defect-type classifier (single-cell/row/
    column/cluster), a repair-margin metric on `bira.py`'s existing
    solver internals, on-chip (RTL) bit-level diagnosis, an optional
    documented `PART_FIX`-shaped JSON sidecar export.
  - Not yet publicized anywhere (README/CHANGELOG/GitHub description) —
    deliberate, per owner instruction, until it actually ships.

## Not started (scoped as GitHub milestones, no work begun)

- **STIL pattern export** — export MBIST test patterns in STIL format.
- **ISO 26262 diagnostic-coverage mapping** — map fault-model coverage
  onto ISO 26262's diagnostic-coverage metrics for automotive use.
- **IEEE 1450.6.2 memory modeling (CTL)** — describe a memory core's test
  structure/repair mechanism in CTL for EDA-tool interop. (Confirmed
  during milestone-4 scoping: this is a different problem from diagnosis
  *result* reporting — don't conflate the two when scoping either.)
- **UVM verification environment** — a UVM-based verification wrapper.

## Shared-bus follow-ups (deliberately deferred, not urgent)

`topology: shared-bus` + `redundancy:` currently supports on-chip
row-and-column self-repair only. Still rejected, each its own separately-
scoped follow-up:
- Tester-driven redundancy under shared-bus (repair_ports pins would bind
  to a single physical remap, meaningless across N memories).
- Persisted-repair-signature load (`onchip_repair_persistence`) under
  shared-bus.
- On-chip diagnosis log (`onchip_diagnosis`) under shared-bus.
- Multi-port memories combined with shared-bus (shared-bus is currently
  single-port only).
- A hierarchical controller-of-controllers orchestrator built on top of
  the shared-bus controller (the "pattern 2" case from the original
  shared/hierarchical scoping doc — deliberately not started before the
  flat case was proven, which it now is).

## FaultFlow integration — status needs re-verification

The original approved plan (see memory / `docs/` history) had three parts:
FaultFlow controller-grading integration itself (**shipped** —
`grade-controller`, `run --faultflow`, fully documented), a list of
autoMBIST bug fixes ("Part B"), and a v3 extension (IEEE-1500 intest on
the blackboxed memory boundary, ~100-200 lines in FaultFlow's own
`build_mode_config()`). Part B and v3's actual current status were not
re-checked before this TODO was written — worth a real audit before
assuming either is done or not done.

## Housekeeping

- ~~`main` significantly behind `dev`~~ / ~~no tagged release since the
  Nix migration~~ — resolved 2026-09-22: `dev` merged into `main` and
  `0.1.0` tagged as the first Nix-era release.
- **`docs/source/roadmap.md` is stale** — found during milestone-4
  scoping: still lists GALPAT as open (it's closed, measured zero
  fault-coverage benefit), still lists "combining the shared-controller
  feature with on-chip redundancy" as "Further out" (it's done, both row
  and column repair), and doesn't mention `wrap-test-access` or
  `yield-sweep` at all. Needs a real pass now that `main` is caught up.
- **Per-macro OpenRAM signoff (DRC/LVS on the macros' own GDS) is
  unresolved** — a stale vendored OpenRAM checkout, not a defect in this
  project's own RTL. Already documented honestly in
  `flow/multimem/mbist/README.md#honest-signoff-caveats`; tracked here so
  it doesn't get lost.
- **MAINTAINERS.md does not exist yet.**
- ~~GitHub repo description/topics refresh~~ — done 2026-09-22 (description
  now mentions shared-bus/multi-memory + row/column repair; added
  `openram`/`sky130`/`jtag` topics).
- **`CONTRIBUTING.md`'s test-tier guidance doesn't warn that the newest
  `tests/integration/test_yield_sweep_*` and
  `tests/integration/test_shared_bus_*` files are meaningfully slower**
  than the rest of the integration tier (each trial/scenario is a real,
  freshly-compiled Icarus run) — worth a one-line note so a contributor
  running the full suite isn't surprised by the wall-clock jump.
