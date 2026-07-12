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
- **Step B — BIRA in Python. ✅ FOUNDATION BUILT** as an *extractable* subpackage
  `src/autombist/repair/` (not a flat `bira.py`): `repair/types.py` holds the shared
  `SpareGeometry`/`RepairSolution`/`Unrepairable` dataclasses (zero imports from the rest of
  autombist — the generator imports `SpareGeometry` one-way; an AST guard,
  `test_repair_package_boundary.py`, enforces it); `repair/bira.py` is the pure
  `analyze(fail_cells, spare_geometry) -> RepairSolution | Unrepairable` (row-only 1D done,
  unit-tested in `test_bira.py`). **Still to do:** 2D (must-repair → branch-and-bound),
  and wiring `analyze` into the Step-D loop. Account for the inflated address space and
  per-row-group column granularity when column repair lands.
- **Step C — BISR.** Repair registers (soft) + load path (tester/parallel MVP; serial
  chain later) driving the external remap. Get the **boot order** right (repair before the
  post-repair scan).
- **Step D — e2e repair loop.** inject repairable defect → `run_simulation(fail_scan=True)`
  → `fail_cells` → `bira.analyze` → BISR loads → re-scan → **clean**; plus the
  **unrepairable** case (more faulty rows than spares → flagged, not silently passed).
  Adopt the DVCon verification recipe: **negative test** (null signature must still fail),
  **connectivity check** (repair-register contents == injected signature), **post-repair
  BIST passes**, and (if power-gating is modeled) a **retention check**.
- **Step E (optional, for on-chip self-repair) — controller fail-address outputs** (§2)
  and an on-chip BIRA/BISR FSM. Not needed for the tester-driven MVP.

---

## 5. Test strategy (all tiers, including LibreLane)

| Layer | Proves | How / where | Status |
|---|---|---|---|
| **0. Python** | BIRA allocation correct (repairable / unrepairable / must-repair) | `tests/software/test_bira.py`, hand-built fail maps, no sim | ✅ **built** (row-only 1D; 2D pending) |
| **1. RTL sim** *(the heart)* | Functional repair: defect→spare works; full loop scan→BIRA→BISR→re-scan clean; unrepairable flagged; DVCon negative/connectivity checks | cocotb/Icarus in the Nix devShell, tool-gated, **per-commit** | ✅ **built** for the defect→spare step (`test_repair_row_e2e.py`); full BIRA→BISR loop pending |
| **2. Synthesis** | Remap logic maps to gates; macro black-boxes; repair regs synthesize | Yosys (extend the FaultFlow `(* blackbox *)` path) | partly exists |
| **3. LibreLane harden** | `[MBIST + remap + stock OpenRAM macro]` → GDS, DRC/LVS clean, timing met | `python3 -m librelane config.yaml` via Nix, **tool-gated**, occasional/nightly (heavy) | **greenfield; now fully viable** (stock macro + std-cell remap) |

**The honest boundary:** Layer 1 proves *functional* repair (with the behavioral model's
defect knob). Layer 3 proves the design *builds and integrates* — it **cannot** prove
physical repair of a real silicon defect (you can't force a defect into a stock macro
pre-silicon; that needs silicon or the fault-injected behavioral model). This is inherent,
not a gap in the approach: sim covers the function, Layer 3 covers buildability.

**Discipline (unchanged project rules):** additions-only / opt-in / byte-identical when off
(mirror `fail_scan=False`, the conditional `fail_bitmap` key, the `repair_ports` byte-identity
guard — do **not** bump `schema_version` for additive keys); three test tiers with
`shutil.which` tool-gating; everything under `nix develop --command pytest tests/software
tests/integration --cov=autombist --cov-fail-under=90`, holding the **563-test / ≥90%**
baseline; docs in this file + `diagnosis-reports.md` (repair I/O) + `cli-reference.md` (any
new command).

---

## 6. What we're still missing / open risks

- **Controller fail-address outputs** — required only for on-chip self-repair (Step E);
  the MVP sidesteps it via the functional-port scan.
- **Fix 4 retarget** — repair config must drive wrapper remap logic, not the macro
  instance (§2).
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
