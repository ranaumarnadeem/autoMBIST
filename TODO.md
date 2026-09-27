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

## FaultFlow integration — status re-verified 2026-09-27

- FaultFlow controller-grading (`grade-controller`, `run --faultflow`) —
  **bundle emission works; a real FaultFlow run has never been verified.**
  The earlier "shipped" was overstated: its full-flow test has always been
  skipped, and a 2026-09-27 audit with Yosys 0.61 found the synth script it
  emits could not have produced a netlist FaultFlow accepts — it never
  deleted `$scopeinfo` cells (FaultFlow hard-fails on unknown cell types),
  omitted the repair/self-repair RTL for any `redundancy:` config, used a
  memory stub valid only for single-port/no-redundancy memories, and
  blackboxed a hardcoded `u_sram` even under shared-bus. All four are fixed;
  the collar now synthesizes clean (no unknown cells, every memory instance
  kept) for plain, tester-driven row+col, on-chip row+diagnosis, on-chip
  row+col, march-1r1w, and shared-bus (plain, row, row+col) configs. Still unverified: an actual `ff.py sim`
  run on that netlist (writes into the faultflow repo's output/, so left to
  the FaultFlow side).
- Part B (autoMBIST bug fixes) — **confirmed fixed** (2026-09-22): sim-time
  fault-mask re-randomization, the `REPO_ROOT`/`--out` path-resolution bug,
  and the hardcoded `READ_LATENCY`. The "O(depth)/clock transition saboteur"
  item was **not** re-checked — still open/unverified.
- v3 (IEEE-1500 intest on the blackboxed memory boundary) — FaultFlow ships
  `intest`/`extest`, but autoMBIST never uses them; that wiring is undone.

### Instance manifest (synthesis plan) — autoMBIST side done, FaultFlow side not started

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
march-1r1w, tester-driven and on-chip repair, shared-bus). FaultFlow's existing `assemble.py`
(`block_stub_verilog`/`compose_soc`) already implements the stub-and-splice
step for SoC blocks, but its stubs declare no parameters, which Yosys
rejects for a parameterized instrument — its stubs must carry the
manifest's `parameters`.

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
- **Open, JTAG/IJTAG**: warptap splices in deterministically named module
  instances (`tap_core`, `sib_cell` `warptap_<sib>`, `bc1_shift_only`/
  `instrument_write` `<prefix>_inst_<k>`, `scan_mux_cell`) and never flattens
  (its ingest is `hierarchy; proc; memory_collect` only), so they can be
  listed from the inserted Verilog into the manifest's `test_access` block —
  not done yet (`internal_instances` is still `"not_enumerated"`). Untested
  idea for reconciling the collar and JTAG-wrapped netlists: pass the
  `<memory_name>_bbox.v` stub to `wrap-test-access` instead of the memory
  model, so the JTAG-wrapped output keeps the memory blackboxed.
- **Not started (FaultFlow repo, separate PR)**: consuming the manifest —
  subprocess-invoke `autombist generate --emit-manifest`, load and validate
  `manifest.json`, generate per-block/glue synth scripts, splice, then
  `sim`/`scan` — plus `autombist-generate` CLI and Tcl commands.

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
