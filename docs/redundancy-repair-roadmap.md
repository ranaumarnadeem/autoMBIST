# Redundancy & Repair (BIRA/BISR) — design roadmap (historical)

> ⚠️ **Fully superseded — this whole roadmap describes work that is now built,
> via a different architecture than the one proposed below.** The remap lives
> in **external standard-cell logic around a stock, spare-augmented OpenRAM
> macro** (OpenRAM's spares are already *addressable* — spare rows = top
> addresses, spare cols = extra `din`/`dout` bits + `spare_wen`), **not** as
> repair pins inside a hand-written macro (Step A below). Steps A–D as
> described here, PLUS work this document doesn't even anticipate — a fully
> autonomous on-chip self-repair FSM (no tester), real-OpenRAM-macro
> integration, LibreLane hardening to GDS, and a real RV32I CPU booting
> through repaired memory — are all built and tested. See
> [redundancy-repair-plan.md](redundancy-repair-plan.md) for current
> architecture and status. This document is kept only as a historical record
> of the original design thinking (the BIRA-algorithm shape and step
> sequencing below are still a reasonable read) — do not treat anything on
> this page as a description of current status.

> Status (as of when this was written): **design roadmap for unbuilt work.**
> This was a handoff/build guide, not product documentation, written against
> the codebase as it stood *before* any of Steps A–D existed (multi-port
> fault modeling + the validation-hardening layer only).

## 0. Goal and why now

Today autoMBIST can *generate* an MBIST controller, *inject* memory faults, and
*measure/diagnose* which cells fail. What it cannot do is **repair** a defective
memory — the classic industry follow-on:

- **BIRA** (Built-In Redundancy Analysis): given the set of failing cells the
  BIST found, decide whether the memory is repairable with its spare rows/
  columns and compute *which* spares to allocate.
- **BISR** (Built-In Self-Repair): apply that allocation — program the remap so
  the memory routes accesses around the defects and presents a clean interface.

This is worth doing now because the validation layer is finally trustworthy: a
detect/escape verdict reflects the *memory*, not a controller bug (Phase 1
sequence checker), and coverage numbers are cross-validated (Phase 2). So a
"BIST said address A bit B failed" signal can be believed as a real defect — the
exact input BIRA needs.

The whole arc, end to end:

```
inject repairable defect  ->  BIST detects failing (addr,bit) set
                          ->  BIRA computes a spare allocation (or "unrepairable")
                          ->  BISR programs the remap
                          ->  re-run BIST  ->  now PASSES
```

---

## 1. The four build steps

Recommended build order is exactly this order — each step is testable on its own
and unblocks the next.

### Step A — Redundant SRAM memory model (the foundation)

A memory RTL family with **spare rows and/or columns** plus a **remap layer**
that redirects a faulty address/column to a spare, controlled by a repair
configuration (fuses or repair registers).

**Honest dependency (refined):** OpenRAM's sky130 flow *does* accept
`num_spare_rows`/`num_spare_cols` (see `scripts/synthesize_sram.py:76-86`), but
those add only **physical** spare rows/columns — it generates **no repair logic
and no repair pins in any view**, so the spares are invisible to behavioral
simulation and to P&R-level repair. See
[openroad-macro-integration.md](openroad-macro-integration.md) §6 for the full
Tier-1 (behavioral, buildable now) vs Tier-2 (abstract views for tapeout,
deferred) split and how the macro contract / repair pins propagate through the
flow. So this step is a **hand-written/templated RTL family**, modeled on the
existing `rtl/sram_model*.sv` and `rtl/march_*` families — not OpenRAM output. Put it at
e.g. `rtl/sram_model_redundant.sv` (+ a `tests/hardware/sram_redundant_dut.v`
test copy, mirroring how `sram_2rw_dut.v` mirrors `sram_model_2rw.sv`).

Design decisions to make up front (call these out; they shape everything):

- **Redundancy granularity:**
  - *Row redundancy* — N spare rows; a repair maps a faulty row address to a
    spare. Simplest; 1D allocation (see BIRA below is trivial while purely row or
    purely column).
  - *Column (I/O) redundancy* — M spare columns/bit-lanes; a repair maps a faulty
    column to a spare. Also 1D.
  - *2D (row + column)* — both. This is what real SRAMs use and where BIRA gets
    interesting (and NP-hard). **Recommendation: start with row-only** to get the
    whole pipeline working end-to-end, then generalize to 2D.
- **Remap mechanism:** a small CAM/comparator bank — for each spare, a
  `{enable, faulty_addr}` repair register; on access, if `addr` matches an
  enabled repair register, steer to the corresponding spare row instead of the
  main array. Combinational match + mux on the address decode path.
- **Repair config interface:** how the remap registers get loaded. Two flavors —
  *soft repair* (repair registers loaded at boot from BISR, volatile) vs *hard
  repair* (fuses, one-time). **Recommendation: soft repair** (a scan/shift
  register or a simple parallel load port) — vastly easier to simulate and to
  drive from BISR than a fuse model, and functionally equivalent for validation.

Suggested module interface sketch (row-redundant, soft repair):

```systemverilog
module sram_model_redundant #(
  parameter int ADDR_WIDTH = 6,
  parameter int DATA_WIDTH = 8,
  parameter int NUM_SPARE_ROWS = 2
)(
  // normal SRAM port (match your existing sram_model_* pinout)
  input  logic clk, csb, web,
  input  logic [ADDR_WIDTH-1:0] addr,
  input  logic [DATA_WIDTH-1:0] din,
  output logic [DATA_WIDTH-1:0] dout,
  // repair config (driven by BISR): one {valid,addr} per spare row
  input  logic [NUM_SPARE_ROWS-1:0]                 repair_valid,
  input  logic [NUM_SPARE_ROWS-1:0][ADDR_WIDTH-1:0] repair_addr
);
```

Ship it with a *hard-defect injection knob* too (a compile-time or plusarg way to
force a specific cell defective) so you have "a memory with a real, fixed defect"
to test repair against — distinct from the saboteur (which is a *test* tool).
This is the "faulty memories" half of your stated goal.

**Test for Step A:** with `repair_valid=0` it behaves as a normal SRAM (reuse an
existing sram_model test). With a forced defect at row R and `repair_valid[0]=1,
repair_addr[0]=R`, writes/reads to R land in the spare and read back correctly;
without the repair, they read the defect. This proves the remap works before any
BIRA/BISR exists.

### Step B — BIRA (redundancy analysis)

Given the BIST's **failing-cell set** and the memory's spare inventory, decide
repairability and compute an allocation.

**Input seam (built):** `bira_input.fail_cells(report) -> set[(addr, bit)]` is
the adapter — it returns exactly the set of cells the BIST *observed* failing,
keyed by address and bit, with escaped/undetected sites excluded. Feed it a
report from `run_simulation(fail_scan=True)`, whose `fail_bitmap` is the
observation-derived, ungated fail map (every cell that read wrong through the
functional port, regardless of any injected fault list). BIRA consumes that set
directly. The old warning still applies conceptually — you want *real* failures,
not injected-but-detected sites — but `fail_cells` already enforces it (it only
takes observed failures), so this is no longer a manual "first check".

**Algorithm:**
- *Row-only or column-only (1D):* trivial — collect distinct faulty rows; if
  `count <= NUM_SPARE_ROWS`, allocate one spare per faulty row; else
  **unrepairable**. Do this first.
- *2D (row + column):* the classic redundancy-allocation problem —
  **must-repair analysis** (a row/column with more faults than the *other*
  dimension's total spares MUST be repaired by its own dimension) followed by a
  **branch-and-bound / repair-most search** over the remaining faults. This is
  NP-hard in general but tiny in practice (a handful of spares); a bounded
  branch-and-bound is standard and fast. Reference: the "CRESTA"/"essential
  spare pivoting" family of BIRA algorithms.

**Where it lives:** model it in **Python first** (`src/autombist/bira.py`) — a
pure function `analyze(fail_cells, spares) -> RepairSolution | Unrepairable`.
Pure Python means fast unit tests with hand-built fail maps (mirror how
`seq_check.py` is unit-tested with synthetic traces). An RTL BIRA engine is a
later, optional step (only needed if you want on-chip analysis rather than
tester-driven).

**Test for Step B:** unit tests over hand-built fail maps — repairable cases
produce a valid allocation (every faulty row covered, no spare double-used);
over-capacity cases return "unrepairable"; must-repair edge cases (a fault line
exceeding the orthogonal spare count) are forced correctly. No simulator needed.

### Step C — BISR (self-repair)

Apply the BIRA solution: drive the redundant memory's `repair_valid`/`repair_addr`
config from the computed allocation. In soft-repair, this is a load sequence
(parallel load, or shift into a scan register) that BISR performs after BIRA.

**Where it lives:** if BIRA is SW-modeled (tester-driven repair), BISR is just
"write the repair registers via the config port" — a step in the test/campaign
harness. If you want *built-in* self-repair (on-chip), BISR becomes a small FSM
that runs BIRA's RTL and programs the fuses/registers — a bigger lift; defer
unless you specifically need the on-chip story.

**Test for Step C:** given a RepairSolution from Step B, load it into the Step-A
memory and confirm the previously-defective addresses now read/write correctly.

### Step D — End-to-end repair loop (the headline)

Tie it together: a redundant memory with a forced repairable defect →
generate/run BIST → it detects the failing addresses → BIRA computes a repair →
BISR loads it → re-run BIST → passes.

**Test for Step D (mirror the existing e2e patterns in `tests/integration/`):**
- Repairable case: inject a defect coverable by the spares; assert
  (1) pre-repair BIST fails at the expected addresses, (2) BIRA returns a valid
  allocation, (3) post-repair BIST passes.
- Unrepairable case: inject more distinct faulty rows than spares; assert BIRA
  reports unrepairable and the part is correctly flagged (not silently "passed").

---

## 2. Integration seams in the current codebase

| Need | Existing hook to build on |
|---|---|
| Failing-cell input for BIRA | **BUILT.** `bira_input.fail_cells(report) -> set[(addr,bit)]` is the uniform adapter. Its authoritative source is the observation-derived `report["fail_bitmap"]` from the functional fail scan (`run_simulation(fail_scan=True)`), which reports every cell that read wrong through the functional port, ungated by any injected fault list; it also normalises the legacy `fault_details` detected sites. Coordinate accuracy is pinned end-to-end (`tests/integration/test_classic_fail_coordinates_e2e.py`). |
| Bring the redundant memory into the classic flow | the config `ports:` block + `generate_from_config` wrap *any* memory macro by pin name; a redundant macro's extra `repair_*` pins are now surfaced via the **optional `repair_ports:` config block** (a list of `{name, width, dir}`) — the wrapper puts them on its boundary and binds them straight through to the (non-saboteur) memory instance. This is the passthrough hook only; the repair *semantics* (loading them from BISR) is Step C. |
| Drive/observe the memory in the algo-shell flow | an `openram_shim`-style adapter (like `openram_shim.sv` / `_mp.sv`) for the redundant memory, if you want algo-shell campaigns against it |
| Repair phase in the controller | the march FSM families (`rtl/march_*`) are the model; a repair-aware flow would add a post-BIST analyze/repair phase, or keep BIRA/BISR tester-side first |
| Trust the fail data | already handled — the Phase 1 sequence checker means a fail report reflects the memory, not a controller bug |

---

## 3. Recommended MVP and sequencing

Smallest thing that demonstrates the whole loop, then generalize:

1. **Row-only redundancy, soft repair, SW BIRA** — Step A (row spares) → Step B
   (1D allocation in Python) → Step C (load repair regs in the harness) → Step D
   (e2e repairable + unrepairable). This proves inject→detect→analyze→repair→pass
   with minimal RTL and no NP-hard search.
2. **Generalize BIRA to 2D** (row + column spares, must-repair + branch-and-bound)
   — the interesting algorithmic piece, still pure-Python-testable.
3. **(Optional) On-chip BISR FSM / RTL BIRA** — only if you need built-in
   (not tester-driven) repair. Bigger lift.

Keep the project's established discipline: additions-only, tool-gated integration
tests, everything through the pinned Nix devShell
(`nix develop --command pytest ...`), hold the current 563-test baseline.

---

## 4. Known hard parts / gotchas

- **2D spare allocation is NP-hard** — but tiny in practice; use bounded
  branch-and-bound with must-repair pruning, not brute force. Don't over-engineer
  the 1D case with it.
- **The fail map must be address-accurate.** *Done for both paths:* the
  algo-shell diagnosis and the classic `fail_bitmap` are each pinned to exact
  known-defect `(addr,bit)` coordinates end-to-end
  (`tests/integration/test_diagnosis_fail_coordinates_e2e.py` and
  `test_classic_fail_coordinates_e2e.py`, both with a high-bit endianness
  guard), and `bira_input.fail_cells` is the adapter. When you add real
  redundant memories, re-confirm the mapping stays identity (see next point).
- **Multi-port + redundancy** — if the redundant memory is multi-port, the
  *one-shared-array* invariant still applies (a repair must remap the shared
  storage, not per-port copies), same as the multi-port fault-modeling work.
- **"Faulty memory" vs "saboteur"** — keep them distinct: the saboteur is a
  *test instrument* (injects faults for coverage measurement); a "faulty memory"
  with a forced hard defect is the *thing under repair*. Step A's defect-injection
  knob is the latter.
- **Repair register loading order** — in soft repair, BISR must load repair regs
  *before* the post-repair BIST reads them; get the reset/boot sequencing right
  or the re-test races the repair load (compare to the existing FSM harness's
  careful `bist_start` negedge-clear comment for the kind of race to watch for).

---

## 5. Where this leaves the tool

With A–D done, autoMBIST would cover the full memory-test-and-repair loop
(generate → test → diagnose → analyze → repair → verify), which is the complete
industry MBIST+repair story and the last major unbuilt scope. The
[Cadence/Synopsys integration idea](../docs) remains separately gated on a
structural-descriptor exporter (out of scope here) — redundancy/repair is the
higher-value, self-contained next build.
