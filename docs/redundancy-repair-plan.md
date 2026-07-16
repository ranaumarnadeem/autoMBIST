# Redundancy & Repair (BIRA/BISR) — the build plan

> **Status:** authoritative build plan, synthesized from a 5-thread research +
> internal-analysis pass (LibreLane **3.0.5**, July 2026; OpenRAM vendored source;
> the MBIST/BIRA/BISR literature; and autoMBIST's own internals). This **refines
> and in places corrects** [redundancy-repair-roadmap.md](redundancy-repair-roadmap.md)
> — where they disagree, this document wins. It also builds on the flow facts in
> [openroad-macro-integration.md](openroad-macro-integration.md) and the fail-bitmap
> foundation already shipped (`bira_input.fail_cells`, the functional-port fail scan).
>
> **Provenance:** external facts came from primary docs adversarially cross-checked;
> OpenRAM claims are from the vendored `./OpenRAM` source + golden files (file:line
> cited); autoMBIST claims are from direct code reading (file:line cited). Lower-
> confidence items are flagged inline. Sources at the end.

---

## 0. The five findings that reshaped the plan

1. **OpenRAM's spare rows/columns are already *addressable*** (source-verified from
   the golden netlist `OpenRAM/compiler/tests/golden/sram_2_16_1_sky130.sp`). A
   "16-word" macro with one spare row presents a **5-bit** address; the spare row is
   simply **address 16**. Spare columns become **extra `din`/`dout` bits**, write-
   gated by a real **`spare_wen`** pin. OpenRAM emits the spares' *access*, not their
   *steering* — there is **no** fuse box, repair register, remap mux, or BIST. So
   "OpenRAM emits no redundancy" was imprecise: it emits **addressable spares with no
   repair infrastructure**. *(bank.py:313-315, port_data.py:219/572, verilog.py:61-82,
   sram_1bank.py:1042-1052, golden `...sky130.sp:2361`.)*
2. **LibreLane/OpenROAD have zero redundancy awareness** — to the flow an SRAM is an
   opaque black box (LEF/GDS + optional Liberty). There is no way to author or honor
   "repair pins" inside a macro; doing so means hand-editing `.lef`/`.lib`/`.gds`
   (unsupported, fragile). *(LibreLane flow/step reference + changelog.)*
3. Therefore **the remap belongs in surrounding standard-cell logic, around a stock
   spare-augmented OpenRAM macro** — the classic academic/open-flow BISR pattern
   (BIST + address-analysis + MUX). This is the **only** approach that both simulates
   *and* hardens through LibreLane, with **no** special macro views. This **corrects**
   the roadmap's Step A (which put spare rows + repair pins *inside* a hand-written
   macro).
4. **The MBIST controller throws the failing coordinates away.** The march FSM knows
   the failing `(addr, xor)` at the compare (`march_c_fsm.sv:124`) but exposes only a
   sticky **1-bit `bist_fail`**. On-chip BIRA/BISR needs `fail_addr`/`fail_xor`/
   `fail_valid` added through FSM → top → wrapper. (For an MVP, the existing
   functional-port software scan already yields the full fail-bitmap without the
   controller — see §2.)
5. The **canonical industry partition is MBIST → BIRA → BISR** with a repair *signature*
   held in registers/fuses and shifted in at boot; only the signal *names* are vendor-
   specific (Synopsys STAR `CRE`/`FCA`/`SMART`; Tessent `BISR chain`/`fusebox`). Our
   interfaces should mirror the *roles*.

---

## 1. Target architecture — external remap around a stock macro

```
        ┌───────────────────────── MBIST wrapper (hardens to GDS) ─────────────────────────┐
        │                                                                                   │
 boot ─▶│  ┌──────────────┐  fail_valid/fail_addr/fail_xor   ┌───────────┐  repair sig      │
        │  │ MBIST ctrl   │ ────────────────────────────────▶│   BIRA    │──┐               │
        │  │ (march FSM)  │  done / bist_fail                 │  (SW MVP  │  │ (which spare  │
        │  └──────┬───────┘                                   │  or FSM)  │  │  ← faulty line)│
        │         │ addr/din/we (test_mode=1)                 └───────────┘  │               │
        │         ▼                                                          ▼               │
 func ─▶│   test_mode MUX ─▶ REMAP (std cells) ─────────────────────▶ stock OpenRAM macro    │
        │                    • row: addr==faulty? → spare addr         (spare rows = top      │
        │                    • col: bit-mux + drive spare_wen           addrs; spare cols =   │
        │                    ▲                                          din/dout + spare_wen) │
        │        ┌───────────┴────────────┐                                                   │
        │        │ repair registers (soft)│◀── load (tester / serial BISR chain) ◀── boundary │
        │        └────────────────────────┘                                                   │
        └───────────────────────────────────────────────────────────────────────────────────┘
```

- **Memory** = a **stock, spare-augmented** macro. In simulation it's a behavioral
  model that *mirrors OpenRAM's interface exactly* (depth `num_words + num_spare_rows`
  with the spare row at the top address; data width `word_size + num_spare_cols` with a
  `spare_wen` pin) **plus a compile-time hard-defect knob**. For tapeout it's the real
  OpenRAM macro — same interface, no changes. **No repair logic lives inside the memory.**
- **Remap** = external standard-cell logic in the wrapper: **row repair** = compare the
  incoming address against the fused faulty-row address(es) and substitute a spare-row
  address; **column repair** = a bit-steer mux on `din`/`dout` plus driving `spare_wen`.
  Driven by the repair registers. This is synthesizable and hardens like any logic.
- **BIRA** computes the *repair signature* (spare ← faulty line, + repairable/
  unrepairable). **Python-side for the MVP** (`bira.py`), an on-chip FSM later.
- **BISR** = the repair registers (soft repair: flops) + a load path. **MVP: tester/
  parallel load.** Later: a serial **repair scan chain** (fuse-box stand-in) with the
  correct boot ordering (**repair loads before the post-repair BIST reads**).

Two design parameters this pins down, both from the OpenRAM source (must flow into BIRA
and the MBIST address counter):
- **Address inflation:** spares make the depth non-power-of-2 and widen `addr` by one;
  size the counter as `ceil(log2(num_words + num_spare_rows))` and treat undecoded top
  addresses as dead. *(bank.py:315.)*
- **Column-repair granularity is per-row-group**, not per-word: with column muxing
  there is one spare-column set per *physical row*, shared across the muxed words and
  indexed by the top address bits. *(functional.py:71-77.)* Start **row-only** to avoid
  this; add column repair in phase 2.

---

## 2. How BISR connects to MBIST (the wiring question, answered)

**Today (verified):** the controller exposes only `bist_fail` (1 sticky bit). The
failing address is live in `march_c_fsm.sv addr_q` and the failing bits are
`mem_rdata ^ expected_q` at the compare (`march_c_fsm.sv:124-125`), but **neither is
captured or output**. Every `*_top.sv` (march_c/raw/1r1w/2rw) exposes only
`output logic bist_fail`. The only trustworthy *full* `(addr,bit)` fail set today comes
from the **functional-port software scan** (`test_mbist.test_fail_scan`) that runs in
`test_mode=0` and **bypasses the controller entirely**, feeding `report["fail_bitmap"]`
→ `bira_input.fail_cells`.

**Two integration levels — pick per goal:**

| | **MVP (tester-driven)** | **On-chip (self-repair in silicon)** |
|---|---|---|
| Fail source | functional-port SW scan → `fail_cells` (**already built**) | new `fail_addr`/`fail_xor`/`fail_valid` controller outputs |
| BIRA | Python `bira.py` | on-chip BIRA FSM in the wrapper |
| BISR load | tester writes repair registers | serial BISR chain at boot |
| Controller change | **none** | add fail-data outputs (below) |

**The signal interface** (canonical role ↔ our name), for whichever level:

- **MBIST → BIRA (fail reporting):** `fail_valid` (per-cell strobe), `fail_addr[AW-1:0]`,
  `fail_xor[DW-1:0]` (or `fail_bit`), `bist_done`. *(bist_done already exists;
  wrapper:12/60.)*
- **BIRA → controller/BISR (status):** `analyze_start`, `analyze_done`, `repairable`/
  `unrepairable`, and the `repair_signature` register.
- **BISR → memory (apply):** the external remap's inputs — `row_repair_en` +
  `faulty_row_addr` per spare row; `col_repair_en` + `faulty_bit` + `spare_wen` per spare
  column. (These are the roles of Synopsys `CRE`/`FCA`; here they drive *wrapper* logic,
  not macro pins.)

**Where each is added (on-chip level), file-precise:**
1. `rtl/march_c/march_c_fsm.sv` — add `fail_addr`/`fail_xor`/`fail_valid` outputs, captured
   at the compare (`:124-125`, where `addr_q` and `mem_rdata^expected_q` are both valid).
   Because the scan is no-stop and hits many cells, this must be a **streaming strobe**
   (or an on-chip fail buffer), not a single register.
2. `rtl/march_c/march_c_top.sv` — matching ports + plumb through `u_march_c_fsm` (repeat for
   other families if repair is wanted there).
3. `src/autombist/templates/wrapper_template.j2` — thread the new fail-data ports out of
   `u_algo_top`, then either surface them as wrapper **outputs** (tester/BIRA) or feed a
   new on-chip BIRA block. **Note: `repair_ports` gives the config-*in* seam; there is
   currently no seam for controller fail-data going *out* — that must be added.**
4. `src/autombist/generator.py` — if fail-data becomes wrapper outputs, add their names to
   `_WRAPPER_RESERVED_PINS` (:305-309) so a user `repair_ports` entry can't collide.

**Fix 4 repurpose (important):** Fix 4's `repair_ports` currently binds the repair pins
*through to the memory instance* (`.repair_valid(repair_valid)` on `u_sram`) — that fit the
*inside-the-macro* model we've now rejected. Under external remap, the repair config is
still a wrapper-boundary **input**, but it should drive the **wrapper's repair
registers/remap logic**, not pass through to the (stock, repair-pin-less) macro. Keep the
boundary-pin machinery; **retarget the binding** from `u_sram` to the new remap block.

---

## 3. LibreLane 3.0.5 integration (the physical/tapeout path)

**Version/flow:** LibreLane **3.0.5** (2026-07-10); use the **Classic** flow (RTL→GDS,
for macro hardening — the new *Chip* flow is for pad-ring top-levels, not us). Nix or
Docker (pip is unsupported). Run: `python3 -m librelane --pdk-root $HOME/.ciel
./config.yaml`. Subset runs via `--from/--to/--skip` (verify spellings with
`librelane --help`). Steps: Yosys (synth/EQY) → OpenROAD (floorplan→place→CTS→route→RCX→
multi-corner STA, incl. `Odb.*` macro placement/PDN) → Magic+KLayout (stream-out + dual
DRC) → Netgen (LVS).

**The design we harden:** `[MBIST controller + external remap std-cells + one stock
OpenRAM macro (generated with `num_spare_rows`/`_cols`)]`. Because the macro is stock and
the remap is standard cells, **this is an ordinary "logic + one macro" harden** — no
special views. Declare the macro in the **`MACROS`** dict (`gds`+`lef` required; `lib`/
`spef` corner-keyed; per-instance `location`/`orientation`); guard power pins with
`` `ifdef USE_POWER_PINS ``; hook power via `PDN_MACRO_CONNECTIONS`.

**Config drift to write correctly (3.0 renames):** `FP_PDN_*` → **`PDN_*`**,
`EXTRA_GDS_FILES` → **`EXTRA_GDS`**, `FILL_CELL`/`DECAP_CELL` → **`FILL_CELLS`/
`DECAP_CELLS`** (lists), `VIAS_RC` → `VIAS_R`. `Macro` gained `vh`/`pnl`.

**Gotchas we'd otherwise underestimate** (budget iteration here):
- **Multi-corner Liberty:** OpenRAM ships **TT-only** `.lib`; LibreLane runs multi-corner
  signoff STA. Either supply only `nom_*` (reduced corner coverage), characterize ss/ff,
  or set **`STA_MACRO_PRIORITIZE_NL=true`** to use `.nl.v`+`.spef` instead.
- **Macro power rails to the top PDN** (the #1 recurring SRAM complaint) — if the macro's
  supplies are on higher metals than the top grid, hand-tune `define_pdn_grid`/
  `add_pdn_connect`.
- **Gated/muxed macro clock:** if the remap gates the memory clock, CTS + hold closure
  need a custom SDC (`create_generated_clock`/`set_clock_groups`).
- **Escaped instance names** (`\submodule.sram0` vs `submodule.sram0`) silently drop
  placement/PDN hookups.
- Ensure the PDK config populates tie/decap/tap/**fill**/diode cells (3.0 skips them
  silently if unset) — else LVS/antenna/DRC surprises.

**Worked examples to copy:** the IHP-SG13G2 AMS chip template (best current LibreLane-3.x
macro example) and the OpenLane sky130+OpenRAM tutorial (closest to our stack, OL2-lineage
config keys that map onto `MACROS`/`PDN_MACRO_CONNECTIONS`).

---

## 4. Revised build steps

Row-only + soft-repair + SW-BIRA MVP first, then generalize.

- **Step A — redundant memory model + external remap. ✅ BUILT** (branch `bisr-dev`).
  `rtl/sram_model_spares.sv` — a behavioral memory mirroring OpenRAM's spare interface
  (widened `addr0` so spare rows are the top addresses; **no repair pins**) + a compile-time
  hard-defect knob. `rtl/repair_remap_row.sv` — the **external** combinational row remap
  (address compare-and-substitute), instantiated by the wrapper *between* the address mux and
  the memory, driven by the `repair_ports` pins (Fix 4 retargeted from `u_sram` to the remap).
  Declared via a dedicated **`redundancy: {num_spare_rows, num_spare_cols: 0}`** config block
  (`generator._validate_redundancy`, paired with `repair_ports`, single-port + non-saboteur
  only). Proven e2e (`tests/integration/test_repair_row_e2e.py`): repair-off → the forced
  defect at (3,3) is visible in the fail-bitmap and `bist_fail`; repair-on → row 3 steered to
  a spare, fail-bitmap empty, BIST passes. (Column repair + `spare_wen`/widened data is Phase 2.)
- **Step B — BIRA in Python. ✅ BUILT** as an *extractable* subpackage
  `src/autombist/repair/` (not a flat `bira.py`): `repair/types.py` holds the shared
  `SpareGeometry`/`RepairSolution`/`Unrepairable` dataclasses (zero imports from the rest of
  autombist — the generator imports `SpareGeometry` one-way; an AST guard,
  `test_repair_package_boundary.py`, enforces it); `repair/bira.py` is the pure
  `analyze(fail_cells, spare_geometry) -> RepairSolution | Unrepairable`, now **2D** (row
  *and* column allocation): a must-repair fixed point (forces a row/column whose remaining
  fault count exceeds the *other* side's *current remaining* budget — checked against the
  live budget, not the original one, so one forced dimension can cascade a force onto the
  other purely via shrunk budget) followed by real backtracking over the residual (proven
  necessary, not just a greedy shortcut, by the `test_independent_faults_use_one_row_and_
  one_column` scenario). Correctness verified two ways: named hand-traced scenarios in
  `test_bira.py` (cascading must-repair, full-block infeasibility, independent-row+column
  faults) documenting *why*, plus a 300-instance brute-force oracle cross-check in
  `test_bira_2d_property.py` for broad correctness on an NP-complete problem (Kuo & Fuchs,
  1987) where one hand-picked example is a weak substitute. The row-only case
  (`num_spare_cols=0`) is proven to degenerate to byte-identical results — all 8 original
  `test_bira.py` assertions pass unmodified. **`generator.py`'s config validation still
  rejects `num_spare_cols != 0`** — the algorithm is ready; the RTL-side column-remap module
  (Phase 2 RTL, `spare_wen`/widened `din`/`dout`, the per-row-group column-mux granularity)
  is not yet built (that's Step C).
- **Step C — BISR. ✅ BUILT.** `repair/bisr.py`'s pure
  `encode_row_repair(solution, spare_geometry) -> RepairSignature` translates a
  `RepairSolution.row_map` into the exact packed `row_repair_en`/`faulty_row_addr` integers
  `repair_remap_row.sv` expects (bit `i` / slice `i` per spare, matching the RTL's own
  slicing). For the tester-driven MVP this is genuinely the whole of BISR: Step A's remap
  reads its config on plain combinational input pins (no serial scan chain, no clock), so
  there is no boot-sequencing race yet — that only becomes real once the config is loaded
  through a clocked chain (a later, on-chip phase). Rejects an `Unrepairable` input with a
  `TypeError` (a caller must check `isinstance(result, RepairSolution)` first) — proven not
  just at the unit level (`test_bisr.py`) but against a REAL `Unrepairable` produced by an
  actual simulation, in the Step D e2e suite.
- **Step D — e2e repair loop. ✅ BUILT** (`tests/integration/test_repair_loop_e2e.py`,
  `tests/hardware/test_repair_loop.py`). The full loop with nothing hardcoded on the Python
  side: inject → `run_simulation` (repair off) → `fail_cells` → `bira.analyze` →
  `bisr.encode_row_repair` → drive the computed signature → re-run → **clean**. Covers the
  DVCon recipe: the **unrepairable** case (`sram_spares_tiny_2defect.v`, 2 distinct faulty
  rows vs. 1 spare — flagged via `Unrepairable`, and `encode_row_repair` provably can't be
  called on it), a **negative/teeth test** (a signature targeting the *wrong* address leaves
  the real defect fully visible — proves the remap steers on an exact match, not just "is
  repair mode on"), and a **connectivity check** (the computed signature's bits, decoded,
  equal the real injected defect's address). Retention/power-gating is out of scope (no
  power domains modeled).
- **Step E (optional, fully autonomous on-chip self-repair). ✅ BUILT** (branch
  `bisr-dev`). No tester, no Python, no separate simulator invocations: one
  `self_repair_start` level and the chip repairs itself. Two new RTL modules, both
  additive and only instantiated when the config sets `redundancy.onchip_selfrepair:
  true` (mutually exclusive with `repair_ports` — validated in `generator.py`; the
  signature is computed on-chip, not driven by boundary pins):
  - **`rtl/onchip_row_repair_analyzer.sv`** — a CAM-style registrar, the hardware
    equivalent of `repair.bira.analyze()` + `repair.bisr.encode_row_repair()`, *scoped
    to the row-only degenerate case*: with `num_spare_cols=0`, `analyze()`'s must-repair
    phase alone (no backtracking) already forces every distinct faulty row — there is no
    combinatorial ambiguity left for a search to resolve, so a simple first-come/
    lowest-free-slot registrar is provably equivalent to the full algorithm in this
    case. (A hardware analog of the general 2D backtracking search would only be
    justified once column-repair RTL exists, which it doesn't yet — `generator.py`
    still hard-rejects `num_spare_cols != 0`.) It tracks fails streamed live from the
    controller — new `fail_valid`/`fail_addr` outputs added to `march_c_fsm.sv`/
    `march_c_top.sv`, purely additive and mirroring the existing sticky `fail_q`
    compare at `ST_CHECK` — and freezes a signature into `row_repair_en`/
    `faulty_row_addr` on demand: the *exact* packed layout `repair_remap_row.sv` and
    `bisr.py::encode_row_repair` already use, no new convention.
  - **`rtl/onchip_selfrepair_ctrl.sv`** — an 8-state sequencer (`S_IDLE →
    S_ANALYZE_KICK → S_ANALYZE_WAIT → S_ANALYZE_LATCH → S_DECIDE → {S_VERIFY_KICK →
    S_VERIFY_WAIT | (skip, if unrepairable)} → S_DONE → S_IDLE`) that runs march-C
    twice — once to analyze, once to independently re-verify — composing *additively*
    with the wrapper's existing tester-driven `test_mode`/`bist_start` pins rather than
    replacing them: `S_IDLE` only starts a self-repair run when `!mbist_busy` (won't
    hijack an in-progress tester run), and the tester's own `bist_start` is likewise
    ignored while the sequencer owns the algo FSM. `self_repair_done`/`self_repair_fail`
    are sticky (cleared only at the next run's kickoff or reset), so a caller can query
    the outcome long after `self_repair_start` is dropped.

  **Load-bearing design decision, found the hard way (via simulation, not static
  reasoning alone):** the analyzer's known-defect state must **accumulate for the life
  of the chip**, cleared only by `rst_n`, never by re-triggering `self_repair_start`.
  The repair remap is *always active* once any repair is applied, so any later analyze
  pass — including a deliberate re-trigger — runs the march algorithm *through* the
  already-repaired memory and, correctly from its own point of view, finds nothing
  wrong there. An earlier draft cleared the analyzer's live state at the start of every
  pass (an `analyze_start` pulse); that "nothing wrong here" result then overwrote the
  previously-correct repair with an empty one, silently re-exposing a real,
  already-fixed defect. Since a hard defect never "un-happens," only a genuine reset —
  not a re-trigger — is the correct point to forget one. Proven by
  `test_retrigger_gives_the_same_result_both_times`: running the full sequence twice in
  one simulation with no reset in between must reach the identical verdict both times.

  **Scope, deliberately bounded:** row-only repair (no column-repair RTL exists, see
  §6), and `algo=="march-c"` only — `fail_valid`/`fail_addr` are wired up only on
  `march_c_fsm`/`march_c_top` in this phase; `generate_from_config` rejects
  `onchip_selfrepair: true` for any other algo.

  **Reset-persistence limitation** (also §6): there is no fuse/NVM path in this design —
  after *any* reset, repair state reverts to unrepaired passthrough until
  `self_repair_start` completes again, and nothing *enforces* gating functional access
  on the new `self_repair_busy` output; that is left to the system integrator.

  Tests: `tests/software/test_onchip_selfrepair_config.py` (config validation, and a
  byte-identical render when the flag is absent — the regression-critical case),
  `tests/hardware/test_onchip_selfrepair.py` (self-contained cocotb: repairable,
  re-trigger, and partial-repair-on-unrepairable scenarios, each independently
  re-verified via the functional-port fail scan, never trusting the chip's own status
  outputs alone), `tests/integration/test_onchip_selfrepair_e2e.py` (4 full-stack e2e
  scenarios via `run_simulation`).

---

## 5. Test strategy (all tiers, including LibreLane)

| Layer | Proves | How / where | Status |
|---|---|---|---|
| **0. Python** | BIRA allocation correct (repairable / unrepairable / must-repair, row+column 2D); BISR signature encoding correct | `tests/software/test_bira.py`, `test_bira_2d_property.py`, `test_bisr.py`, hand-built fail maps + brute-force oracle, no sim | ✅ **built** |
| **1. RTL sim** *(the heart)* | Functional repair: defect→spare works; full loop scan→BIRA→BISR→re-scan clean; unrepairable flagged; DVCon negative/connectivity checks | cocotb/Icarus in the Nix devShell, tool-gated, **per-commit** | ✅ **built** — both the defect→spare RTL proof (`test_repair_row_e2e.py`) and the full computed-signature loop with unrepairable/negative/connectivity coverage (`test_repair_loop_e2e.py`) |
| **1b. On-chip autonomy** | Fully autonomous self-repair (no tester): on-chip analyze→decide→apply→verify, including the accumulate-across-retriggers invariant and partial-repair-on-unrepairable | cocotb/Icarus in the Nix devShell, tool-gated, **per-commit** | ✅ **built** — `test_onchip_selfrepair.py` (cocotb: repairable/re-trigger/partial) + `test_onchip_selfrepair_e2e.py` (4 full-stack scenarios) |
| **1c. Real macro** | The Step-A remap genuinely redirects physical storage on a REAL OpenRAM-compiled macro (not the toy behavioral models) — a steering-distinctness proof, since a real macro has no defect-injection knob to reuse the inject→repair pattern | cocotb/Icarus, tool-gated, generated via `scripts/synthesize_sram.sh --tech scn4m_subm` (no PDK/magic needed) | ✅ **built** — `test_repair_row_real_macro.py` + `test_repair_row_real_macro_e2e.py` against `tests/hardware/sram_bisr_real_8x16.v` (see below) |
| **2. Synthesis** | Remap logic maps to gates; macro black-boxes; repair regs synthesize | Yosys (extend the FaultFlow `(* blackbox *)` path) | partly exists |
| **3. LibreLane harden** | `[MBIST + remap + stock OpenRAM macro]` → GDS, DRC/LVS clean, timing met | `python3 -m librelane config.yaml` via Nix, **tool-gated**, occasional/nightly (heavy) | **greenfield; now fully viable** (stock macro + std-cell remap) |

**The honest boundary:** Layer 1 proves *functional* repair (with the behavioral model's
defect knob). Layer 1c narrows that boundary a little further — it proves the remap
genuinely redirects a REAL compiled macro's physical storage (write-then-toggle-repair-
then-readback: the pre-repair marker is still sitting, untouched, in the original physical
row after the repaired write went to the spare instead), not just the toy model's. But it
still **cannot** inject a genuine defect into that real macro (there is no fault-injection
knob on compiled OpenRAM output) — only a hand-written behavioral model can simulate a
stuck-at bit pre-silicon. Layer 3 proves the design *builds and integrates* — it also
**cannot** prove physical repair of a real silicon defect (you can't force a defect into a
stock macro pre-silicon; that needs silicon or the fault-injected behavioral model). This is
inherent, not a gap in the approach: sim covers the function, Layer 3 covers buildability.

**Discipline (unchanged project rules):** additions-only / opt-in / byte-identical when off
(mirror `fail_scan=False`, the conditional `fail_bitmap` key, the `repair_ports` byte-identity
guard — do **not** bump `schema_version` for additive keys); three test tiers with
`shutil.which` tool-gating; everything under `nix develop --command pytest tests/software
tests/integration --cov=autombist --cov-fail-under=90`, holding the **676-test / ≥90%**
baseline (676 passed, 2 skipped, 94.80% coverage as of Step E); docs in this file +
`diagnosis-reports.md` (repair I/O) + `cli-reference.md` (any new command).

---

## 6. What we're still missing / open risks

- **Controller fail-address outputs — ✅ done for `march-c`** (Step E), via additive
  `fail_valid`/`fail_addr` outputs on `march_c_fsm.sv`/`march_c_top.sv`. Not yet wired up
  for `march-1r1w`/`march-2rw`; `onchip_selfrepair: true` is rejected at config time for
  any algo other than `march-c`.
- **On-chip repair persistence** — no fuse/NVM path (Step E); a reset reverts the chip to
  unrepaired passthrough until `self_repair_start` completes again, and nothing enforces
  gating functional access on `self_repair_busy` — that's left to the system integrator.
- **Fix 4 retarget** — repair config must drive wrapper remap logic, not the macro
  instance (§2).
- **Real-macro parameter interface (Layer 1c finding)** — the wrapper's generic redundancy
  memory instantiation (`wrapper_template.j2`) assumes `memory_name`'s module takes
  `(ADDR_WIDTH=logical, DATA_WIDTH, NUM_SPARE_ROWS)` and derives its own physical port
  width internally, because that is the only shape that existed until now (the toy
  `sram_model_spares.sv`/`sram_spares_tiny.v` models). A REAL OpenRAM-compiled macro has
  **no** `NUM_SPARE_ROWS` parameter at all — its physical `ADDR_WIDTH` is baked in
  permanently at compile time, matching real silicon (confirmed by actually driving
  `scripts/synthesize_sram.sh`'s raw output through the wrapper and hitting an elaboration
  error). The fix used here — a thin per-DUT compatibility shim
  (`tests/hardware/sram_bisr_real_8x16.v`) that exposes the expected parameter shape and
  derives `MEM_ADDR_WIDTH` internally before instantiating the real (renamed) macro
  underneath — works but is manual and per-macro; a future real-macro integration (sky130
  or another OpenRAM target) will need either the same shim pattern repeated, or the
  wrapper template generalized to accept a memory module with no spare-count parameter at
  all. `scripts/synthesize_sram.py` also had a related pre-existing bug fixed alongside
  this: `build_config_text()` only emitted `num_spare_rows`/`num_spare_cols` for
  `tech=="sky130"`, silently dropping `--num-spare-rows`/`--num-spare-cols` for
  `scn4m_subm`/`freepdk45` (both are generic OpenRAM `sram_config.py` fields, not
  sky130-specific).
- **Multi-corner macro Liberty** — OpenRAM is TT-only; decide `STA_MACRO_PRIORITIZE_NL`
  vs. characterization before Layer 3 signoff.
- **Column repair granularity** — per-row-group under column muxing; row-only first.
- **Retention / power-gating** (`safe_rr`-style reset-inhibit on repair registers) — an
  advanced detail; only if we model power domains.
- **OpenRAM spare intent** *(lower confidence)* — the "external BIST/BISR programs the
  repair" intent is evident from source (OpenRAM's own verifier `FIXME: ignore spare
  columns`, `functional.py:300-302`) but **not** documented as a protocol; we define our
  own remap convention.

---

## Sources

- LibreLane: [repo](https://github.com/librelane/librelane) · [Using Macros](https://librelane.readthedocs.io/en/stable/usage/using_macros.html) · [Flows reference](https://librelane.readthedocs.io/en/stable/reference/flows.html) · [3.0 announcement](https://fossi-foundation.org/librelane/blog/2026-03-25-website_release_3-0) · [Changelog](https://github.com/librelane/librelane/blob/main/Changelog.md)
- OpenRAM (vendored `./OpenRAM` source + golden files): `bank.py:313-315`, `port_data.py:219/572`, `verilog.py:61-82/231`, `sram_1bank.py:1042-1052`, `functional.py:71-77/300-302`, golden `compiler/tests/golden/sram_2_16_1_sky130.sp:2361`; repo: <https://github.com/VLSIDA/OpenRAM>
- MBIST/BIRA/BISR: Kothari et al., "In-System SRAM Repair Verification," **DVCon Europe 2021** (block diagram, CRE/FCA, SMART, verification recipe); Siemens Tessent MemoryBIST + fusebox/BISR blogs; Synopsys STAR (Semiwiki); Wang/Wu/Wen *VLSI Test Principles*; Bushnell & Agrawal; CRESTA (Kawagoe, ITC 2000); Kuo & Fuchs (2-D NP-completeness).
- Worked macro-harden examples: IHP-SG13G2 AMS template (iic-jku); OpenLane sky130+OpenRAM tutorial; efabless/VLSIDA `sky130_sram_macros`.
- autoMBIST internals (this repo): `wrapper_template.j2`, `rtl/march_c/march_c_{top,fsm}.sv`, `generator.py`, `bira_input.py`, `reporting.py`, `runner.py`, `tests/hardware/test_mbist.py`, `flake.nix`, `.github/workflows/test.yml`.
