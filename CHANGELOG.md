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

### Added: on-chip self-repair retires a faulty spare and retries
- `redundancy.onchip_spare_retry: true`: when the verify pass shows that a
  spare the chip allocated is itself faulty, the chip marks it bad, drops its
  claim and runs the analysis again on the remaining spares, instead of
  reporting `self_repair_fail` straight away. Each retry retires at least one
  spare, so it stops after at most the number of spares. Row-only and 2D
  repair, single- and multi-port; not yet with `topology: shared-bus`. The
  diagnosis log keeps the first analyze pass. Off by default: existing
  configs behave exactly as before, and their wrappers only gain tie-offs for
  the new analyzer and sequencer ports.
- `yield-sweep --self-repair --spare-retry` builds with it, to measure the
  gain on random defects in the spares. On the documented example sweep (16 x 8,
  2 spare rows and 1 spare column, defects in the spares) it removes every
  faulty-spare failure: the normalized repair rate goes from .83 to .89 at 2
  defects and from .53 to .71 at 3.

## [0.4.0] - 2026-10-22

### Added: repair yield analysis
- `yield-sim`: a repair-rate sweep on the fail bitmap alone, no RTL, over the
  defect mixes of Ohler et al. (DDECS 2007) and the clustered sequential
  placement of the ITC'2020 RA Benchmarks, with banks, defects in the spares
  (`--expose-spares`), minimum spares and the rate of overused spares
  (`--min-spares`), and fail-bitmap shape classes (`--classes`). Writes
  `yield_sim.json`.
- `yield-multiplier`: yield with redundancy and the yield multiplier from a
  sweep's repair curve, for each assumed initial yield (or mean defect count)
  and clustering, with truncation bounds and a joint confidence band. Writes
  `yield_multiplier.json`.
- `yield-sweep --observe march`: the march's own per-read fail log as the
  observation, which sees transition faults; `--defects` injects preset defects;
  `--self-repair` runs the chip's own on-chip self-repair on a fault-injectable
  memory model (defects in the spares with `--expose-spares`, the diagnosis log
  with `--diagnosis-entries`) and names why each gap to the exact solver
  happened.
- `repair.bira.analyze_detailed()`: a minimum-spares objective, faulty spares,
  solver statistics, and `verify_solution()`. `analyze()` is unchanged.
- For library use: `run_simulation(march_log=True)` and the report's
  `march_fail_bitmap`; `render_memory_model(stuck_masks=...)` for a
  fault-injectable behavioral model.
- The calibration protocol and its results (`scripts/calibrate_yield.py`,
  `tests/data/yield_calibration.json`): the ITC'2020 MLS3050 gate failed, by 0.35
  to 1.5 points at every count, and nothing was tuned. See
  [Repair Yield Analysis](https://ranaumarnadeem.github.io/autoMBIST/yield-analysis.html).

### Changed
- `yield-sweep`'s repair rate now counts valid trials only. A trial whose fail
  scan did not complete, failed, or read a word as X used to count as repaired;
  it is now reported invalid and left out.
- `yield-sweep` generates and compiles the wrapper once per sweep instead of once
  per trial, and counts a trial repaired only when the repair from the observed
  cells covers every injected cell (identical to before for stuck-at faults seen
  by the functional scan). The seeds are unchanged.
- `yield-sweep` refuses fault types its observation cannot see (the functional
  scan sees stuck-at faults only), accepts `--faults` ranges such as `1..8`, and
  reports an error as one line instead of a traceback.
- `yield_sweep.json` is schema 1.1, additive over 1.0: trial `status` and
  `invalid_reason`, point `valid_trials`, `repaired_count`, `invalid_count` and
  `wilson`, and top-level `engine`, `seeding`, `observation` and `defect_model`.

### Fixed
- A `FAIL_CELL` (or `FAULT_SITE`) marker that landed glued to the end of a
  memory model's `$display` line was dropped from the fail bitmap. Markers are now
  found anywhere in a line.

### Documentation
- Releases are archived on Zenodo. `CITATION.cff` carries the concept DOI,
  10.5281/zenodo.23453213, which resolves to the latest version, and the README
  has a DOI badge and says how to cite one version. v0.3.0's own DOI is
  10.5281/zenodo.23453214.

## [0.3.0] - 2026-10-18

### Changed (breaking): the network has its own TAP instruction
- `wrap-test-access` puts the IJTAG network behind warptap's dedicated
  `IJTAG_ACCESS` instruction (`IR 4'b1100`), no longer behind EXTEST. EXTEST,
  SAMPLE and PRELOAD now select BYPASS, so a board-level boundary-scan EXTEST no
  longer reaches `test_mode` or `bist_start`. The PDL, the vectors, the STIL, the
  BSDL (which declares `IJTAG_ACCESS` and gives the other three BYPASS), the ICL
  `AccessLink` and the manifest all follow. A PDL, vector file or STIL from 0.2.0
  loads EXTEST and does not reach a network wrapped by this version: regenerate
  them with the netlist, or pass `--access-instruction extest` to keep the network
  behind EXTEST.

### Added: the inserted test access, hardened for silicon
- Every status read (`bist_done`, `bist_fail`, the self-repair and diagnosis
  statuses) is captured through a two-flop TCK synchronizer: they come from the
  `clk` domain and Capture-DR samples them on TCK.
- A chip reset clears the controls JTAG drives: every control TDR also clears on
  the wrapper's `rst_n`, through a TCK release synchronizer, not only on TRST.
  Before, a chip reset on a board that never pulses TRST left the memory in test
  mode with `bist_start` high, and the BIST ran again.
- `--access-instruction ijtag-access|extest` picks the instruction above.
- Tests replay both behaviors against the wrapped RTL next to a netlist wrapped
  without them: after a chip reset `bist_done` stays 0 where the old netlist runs
  the BIST again, and scans under EXTEST leave an `IJTAG_ACCESS` network as reset
  where a network behind EXTEST starts the BIST. The synchronizers are checked
  structurally; RTL simulation cannot show metastability.
- Manifest schema 1.1.0: `test_access` records `network_instruction`,
  `network_opcode`, `idcode_value` and `chip_reset` (`{port, active}`), and each
  instrument `capture_sync`, the fields FaultFlow's parser reads. Additive: a block
  without the network fields means EXTEST.

### Changed
- `wrap-test-access` needs warptap 0.0.4, the version the Nix dev shell pins, for
  every run, not only `--emit-stil`; an older one is refused with the version it
  needs. The `test-access` extra in `pyproject.toml` states the same floor.

### Documentation
- The docs site has an Open Graph image, the same 1280x640 card as the
  repository's GitHub social preview, so shared links show it.

## [0.2.0] - 2026-10-18

### Added: running the BIST without the cocotb flow, and from a tester
- `generate` writes a standalone, self-checking testbench into `tb/`:
  `tb_<wrapper>.sv` runs the BIST from the wrapper's own pins and `run_tb.sh`
  compiles and runs it with Icarus Verilog, no Python or cocotb involved. Its
  timeout comes from a BIST-length model measured against the RTL for every
  algorithm (never short, at most two cycles long).
- `tb/<memory>_model.v`, a behavioral model of the memory generated from the
  config, with the same ports as the blackbox stub: spare rows and columns,
  1R1W and 2RW ports, shared-bus, both write polarities, any `read_latency`.
  `run_tb.sh` uses it when given no arguments; a model on the command line
  replaces it. At `read_latency: 0` a multi-port model forwards a write to a
  read of the same address on the same edge, which the two-port algorithms
  expect.
- `wrap-test-access` writes what runs the BIST over JTAG: the `run_mbist`
  IEEE 1687 PDL procedure, its retargeting to TCK-level vectors with the
  expected TDO, and a self-checking JTAG testbench that checks the result only
  at TDO, as a tester would. `--bist-cycles` sets the run loop when there is
  no config to compute it from.
- `wrap-test-access --emit-icl` also writes a TAP-only BSDL, and the ICL now
  carries an `AccessLink` naming the instruction that selects the IJTAG network
  (EXTEST), which that BSDL declares. `--idcode` bakes a 32-bit IDCODE into the
  TAP (bit 0 must be 1); `--tck-max-freq-mhz` sets the BSDL's TCK limit. The
  defaults (warptap's placeholder IDCODE, 10 MHz) are stated as assumptions.
- `wrap-test-access --emit-stil` writes the `run_mbist` pattern as IEEE 1450
  STIL for a tester, through warptap's `to_stil`: TRST and `rst_n` held low
  while TCK and `clk` run, then the same TCK cycles and `clk` run loop as the
  vectors, with every pin of the wrapped top declared. TCK's period is
  1 / `--tck-max-freq-mhz`, `clk`'s is `--stil-clk-period-ns`. Tests replay the
  file against the wrapped RTL, independently of warptap: a good memory passes,
  a stuck bit fails exactly the `bist_fail` compare, and a run loop shorter
  than the BIST fails the `bist_done` one. It refuses warptap older than 0.0.4,
  whose `to_stil` compared TDO after the rising TCK edge.

### Added: handoff to FaultFlow and other synthesis-aware tools
- `generate --emit-manifest` writes `manifest.json`, a synthesis plan: every
  instance with its parameters and sources, the memories marked `blackbox` and
  the test instruments (MBIST controller, self-repair, diagnosis, remaps)
  `separate`, so a downstream flow synthesizes each one standalone and never
  optimizes across an instrument boundary. `generate` always writes the
  port-only `<memory>_bbox.v` stub, now built from the real port list and
  redundancy geometry.
- `wrap-test-access --manifest DIR` takes the sources, top, config and output
  location from that directory, wraps the memory's stub, and records every
  instance of the wrapped netlist in a `test_access` block: the TAP, one SIB per
  wrapped port, one TDR bit per port bit, the MBIST blocks and the memory, read
  back from the inserted netlist rather than predicted. The block also records
  the ICL, BSDL and STIL paths and the TAP's facts (IDCODE, instruction length,
  the instruction and opcode that select the network).
- `grade-controller` and `run --faultflow` grade through FaultFlow's autoMBIST
  integration: each instrument synthesized standalone, the memory blackboxed,
  `scan-check` run, and nothing written into the FaultFlow checkout. A `--test`
  build is graded as its clean collar, so array and controller coverage are
  reported together, with faults testable only through the memory shown as
  blackbox-unresolved.

### Fixed
- The shared-bus wrapper lost every memory when synthesized. Yosys lowered its
  unpacked per-memory arrays to memories its instance ports never reached,
  deleted the memory instances, and the synthesized BIST then passed a
  defective memory. All shared-bus arrays are packed; dedicated wrappers render
  byte-identically. A new test synthesizes the collar and runs the BIST on the
  netlist: a good memory passes and a stuck bit fails.
- The `march-2rw` controller could not be synthesized: Yosys rejects its
  unpacked per-port arrays. They are packed, and a gate-level test catches a
  defect that sits only in port 1's read path.
- `onchip_2d_repair_analyzer` inferred latches, which Yosys rejects in an
  `always_comb`. Fixed, and the Verilator LATCH lint is no longer suppressed for it.
- `grade-controller` wrote a synthesis script FaultFlow could never accept
  (leftover `$scopeinfo` cells, missing repair sources, a memory stub that fit
  only one read/write port, a hardcoded memory instance), and under shared-bus
  it pointed Yosys at a wrapper file that does not exist.

### Changed
- The Nix dev shell, and so CI, pins warptap `v0.0.4` as a flake input, so CI
  runs the `wrap-test-access`, BSDL, IDCODE and STIL tests instead of skipping
  them. Tests that need warptap skip cleanly without it, and the code around it
  is also tested against stand-ins.
- `pyproject.toml`: a new description, keywords, an authors entry and the
  documentation URL. The `test-access` extra stays at `warptap>=0.0.2`, because
  PyPI has nothing newer; the BSDL and `--idcode` need 0.0.3 and `--emit-stil`
  0.0.4, and each refuses with an upgrade message on an older one.

### Documentation
- README: a new introduction and at-a-glance table, the open-source DFT
  toolchain it belongs to (faultflow, OpenTestability, warptap), a FAQ and a
  Citing section. `CITATION.cff`: version 0.2.0, the docs URL, more keywords.
  The docs site's description and `llms.txt` are updated.

### Known limits
- The STIL has not been run on a real tester; the tests replay it against the
  RTL in simulation.
- The BSDL describes the TAP only, with no boundary register, and the
  `AccessLink` names only the first SIB of the network (an open question in
  warptap).
- The inserted test access does not yet use three options warptap 0.0.4 offers:
  `capture_sync` (status reads cross from the `clk` domain to TCK without a
  synchronizer), `chip_reset` (a chip reset does not clear `test_mode` or
  `bist_start`), and a dedicated IJTAG access instruction (a board-level EXTEST
  selects the network).

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
