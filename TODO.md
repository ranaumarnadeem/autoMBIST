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

## FaultFlow integration — status re-verified 2026-09-28

- FaultFlow controller-grading (`grade-controller`, `run --faultflow`) —
  **works end to end against a real FaultFlow, verified 2026-09-28.** The
  bundle now hands synthesis to FaultFlow's autoMBIST integration (driven by
  the manifest below: every instrument standalone, memory blackboxed), then
  runs scan insertion, `scan-check` (the old bundle skipped it, so it could
  never reach ATPG) and scan stuck-at ATPG with the memory's outputs treated
  as unknown. Everything a run writes stays in the bundle; it used to run
  from inside the FaultFlow checkout and write its output there. A `--test`
  build is graded as its clean collar, so `run --test --faultflow` reports
  array and controller coverage together. The live tests
  (`tests/integration/test_grade_controller.py`, marker `faultflow`) run
  whenever `$FAULTFLOW_HOME` and Yosys are available. Example, 16x4
  `sram_1rw` with march-c: controller 554/680 (81.47%), 126 faults
  testable only through the memory.
- JTAG/IJTAG-wrapped designs (`wrap-test-access --manifest`'s `test_access`
  block) — **not graded yet.** FaultFlow reads the block but doesn't
  interpret it; that is FaultFlow-side work.
- Part B (autoMBIST bug fixes) — **confirmed fixed** (2026-09-22): sim-time
  fault-mask re-randomization, the `REPO_ROOT`/`--out` path-resolution bug,
  and the hardcoded `READ_LATENCY`. The "O(depth)/clock transition saboteur"
  item was **not** re-checked — still open/unverified.
- v3 (IEEE-1500 intest on the blackboxed memory boundary) — FaultFlow ships
  `intest`/`extest`, but autoMBIST never uses them; that wiring is undone.

### Instance manifest (synthesis plan) — done on both sides

`generate --emit-manifest` writes `manifest.json`: every instance with its
`hierarchical_path`, `module_type`, instantiation `parameters`, `sources`,
and `hierarchy_hint` — `"blackbox"` for memories (read the
`<memory_name>_bbox.v` stub `-lib`), `"separate"` for test instruments (MBIST
controller, self-repair, diagnosis, remaps: synthesize each standalone with
`chparam`, blackbox it in the glue synth, splice back — per the Yosys team's
advice to synthesize test instruments apart from the core). Proven
sufficient with Yosys alone (no FaultFlow code): driven only by the
manifest, every block synthesizes standalone to pure sky130 cells and the
glue synthesizes with every block as a `-lib` stub, with the manifest's
instance paths matching the glue's cell names exactly — for every
configuration tried (dedicated single-/multi-port incl. march-2rw and
march-1r1w, tester-driven and on-chip repair, shared-bus). FaultFlow now
consumes it (`faultflow/integrations/autombist.py`, `ff.py
autombist-generate`): per-block synthesis with parameter-carrying stubs, then
`compose_soc` splices the blocks into one netlist, checked driver-clean by
Yosys and at gate level (BIST passes a good memory, fails a stuck-bit one).

- **Reverted** (2026-09-27): baking `(* keep_hierarchy *)` into the repair
  RTL. Yosys keeps the hierarchy, but FaultFlow's loader only simulates the
  top module's cells and hard-fails on unknown types, so it broke grading.
- **Fixed, autoMBIST RTL that was not Yosys-synthesizable** (Icarus/Verilator
  always simulated them fine; nothing had ever run them through Yosys):
  - ~~`topology: shared-bus` loses its memories in synthesis~~ — **fixed
    2026-09-27**. The wrapper's per-memory arrays were unpacked, and Yosys
    lowered the variable-index read `sram_dout_arr[mem_sel_q]` to a
    never-written `$mem`, so `opt_clean` deleted every memory instance; with
    the read data undefined it also optimized the compare path, so the
    synthesized BIST passed a DEFECTIVE memory too (RTL simulation was always
    fine). Now packed arrays; proven at gate level by
    `tests/integration/test_synthesized_bist_e2e.py` (synthesized collar,
    real cocotb run: passes a good memory, fails `sram_1rw_stuck_bit.v`).
  - ~~march-2rw not synthesizable~~ — **fixed 2026-09-27**:
    `march_2rw_algo.sv`'s per-port outputs (and `march_2rw_fsm.sv`'s matching
    wires) were unpacked arrays, which Yosys's SV frontend rejects outright;
    now packed. `test_synthesized_bist_e2e.py` covers it at gate level with a
    defect only in port 1's read path, so port 1's own compare is proven to
    survive synthesis.
- ~~JTAG/IJTAG instances not enumerated~~ — **done 2026-09-27**.
  `wrap-test-access --manifest DIR` alone derives its sources from the manifest
  with the memory's stub in place of the model, so the JTAG-wrapped netlist
  keeps the memory blackboxed (this reconciles the collar and JTAG-wrapped
  netlists, previously two unrelated outputs), writes into `DIR/test-access/`,
  and records a `test_access` synthesis plan: every instance of the wrapped
  top (TAP, one SIB per port, one TDR bit per port bit, MBIST blocks under
  their parameter-specialized module names, the memory) plus each
  instrument's SIB and ordered TDR bits, enumerated by re-reading the output
  through warptap's own ingest (not predicted). Proven sufficient with Yosys
  alone by `tests/integration/test_testaccess_manifest_e2e.py` (every block
  standalone from the wrapped file; glue with them blackboxed leaves exactly
  the listed instances). BSR (boundary-scan register) insertion is a
  separate warptap step `wrap-test-access` does not do.
- **Not started (FaultFlow repo, separate PR)**: consuming the manifest —
  subprocess-invoke `autombist generate --emit-manifest` (and optionally
  `autombist wrap-test-access --manifest DIR`), load and validate
  `manifest.json`, generate per-block/glue synth scripts for the collar or
  the JTAG-wrapped netlist, splice, then `sim`/`scan` — plus
  `autombist-generate` CLI and Tcl commands.

### Network access description (BSDL) — autoMBIST side done, three things open

`wrap-test-access --emit-icl` now writes the BSDL its ICL `AccessLink` names, plus
`--tck-max-freq-mhz`, `--idcode`, and `bsdl_path`/`tap` in the manifest.

- **Raise the `warptap` floor in `pyproject.toml` to 0.0.3** once 0.0.3 is published to
  PyPI (it is tagged on GitHub and has `bsdl_emit`, `idcode_value` and the EXTEST-only
  network select, but PyPI stops at 0.0.2). Until then the floor stays 0.0.2 so the pip
  extra still installs, and `--emit-icl`/`--idcode` refuse with an upgrade message.
- **warptap in CI** is done through the flake: `flake.nix` pins warptap `v0.0.3` as a
  source input (`flake.lock` fixes the commit), so CI runs the wrap-test-access, BSDL and
  IDCODE tests instead of skipping them. Move it with
  `nix flake lock --update-input warptap`. `nix run` (the packaged CLI) still has no
  warptap, so `wrap-test-access` there reports it is not installed.
- **The `AccessLink` lists only the first SIB** in its `ScanInterface`. warptap records
  it as an open question no readable source settles; nothing here can check it without
  a real retargeting tool. Our networks have many top-level SIBs.
- **The BSDL has no independent parser check** (TAP-only, so a strict tool rejects it).
  Its claims are checked against the real TAP RTL, and the IDCODE is read back from the
  wrapped netlist (`tests/integration/test_bsdl_e2e.py`).

## Housekeeping

- ~~`main` significantly behind `dev`~~ / ~~no tagged release since the
  Nix migration~~ — resolved 2026-09-22: `dev` merged into `main` and
  `0.1.0` tagged as the first Nix-era release.
- ~~`docs/source/roadmap.md` is stale~~ — fixed 2026-09-22: GALPAT's status
  corrected (investigated and deliberately not pursued, not "open"), the
  shared-controller + on-chip self-repair combination moved from "Further
  out" into "Done" (row + column), and the diagnosis-logging bullet's
  stale "JTAG/IJTAG wrapping still pending" note removed (`wrap-test-access`
  already covers it). `yield-sweep`/milestone 4 deliberately still excluded.
- **Per-macro OpenRAM signoff (DRC/LVS on the macros' own GDS) is
  unresolved** — a stale vendored OpenRAM checkout, not a defect in this
  project's own RTL. Re-confirmed 2026-09-23 during the docs full-audit
  pass: vendored `OpenRAM/` HEAD is still `449781d2` (2026-04-08); the two
  upstream fixes (`5077282`/`8c4f4ef`, dated 2026-04-28/2026-05-14) are
  still not ancestors of it. Already documented honestly in
  `flow/multimem/mbist/README.md#honest-signoff-caveats`; tracked here so
  it doesn't get lost.
- ~~MAINTAINERS.md does not exist yet~~ — done 2026-09-22.
- ~~GitHub repo description/topics refresh~~ — done 2026-09-22 (description
  now mentions shared-bus/multi-memory + row/column repair; added
  `openram`/`sky130`/`jtag` topics).
- ~~`CONTRIBUTING.md`'s test-tier guidance doesn't warn about the slow
  shared-bus/yield-sweep integration tests~~ — done 2026-09-22.
- ~~`docs/source/*.md` full audit~~ — done 2026-09-23: all 17 pages read in
  full (8 previously, 8 more in a parallel pass, `roadmap.md` separately).
  12 real findings fixed (biggest: `multi-port-guide.md` flatly claimed
  march-2rw does not and will not support on-chip self-repair — it does,
  row and column both). `quickstart.md` verified clean by reproducing its
  commands against a live install, not just reading it.
