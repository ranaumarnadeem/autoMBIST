---
orphan: true
---

# OpenROAD / LibreLane Macro Integration

> **Purpose.** Research notes grounding the redundant-memory + repair build. It
> answers one question: *how is an SRAM represented and integrated as a hard macro in the
> open-source RTL-to-GDS flow (LibreLane driving OpenROAD, memories from OpenRAM)?*
> — so a hand-written redundant SRAM model can present the **same macro contract**
> and, eventually, drop into the same flow.
>
> **Flow lineage (important).** This project uses **LibreLane + OpenROAD**.
> LibreLane is the maintained, Python/Nix-based flow — the continuation of what
> was *OpenLane 2*, renamed after the governance split. It is **not** the classic
> Tcl-based *OpenLane 1* (the `EXTRA_LEFS`/`EXTRA_LIBS`/`MACRO_PLACEMENT_CFG`
> flow). Where the two differ, this doc uses **LibreLane's** mechanics and marks
> the OpenLane-1 form as legacy. **OpenROAD** is the underlying place-and-route
> engine for both, so the physical-integration facts are flow-independent.
>
> **Provenance.** The macro view-set, synthesis black-boxing concept, OpenROAD
> physical integration, and OpenRAM facts are from a fan-out web-research pass over
> primary docs, adversarially verified (3-vote; 20 claims passed). The
> **LibreLane-specific config** (the `MACROS` dict, the exact `Macro`/`Instance`
> field names + required/optional status, placement/power/halo variables) was
> checked directly against **two** LibreLane primary sources — the *Using Macros*
> usage guide and the *config API* reference (the `Macro`/`Instance` dataclass
> definitions). Sources at the end.

## 1. The macro contract: a bundle of per-stage views

A hard SRAM macro is delivered as a set of **abstract views**, each consumed by a
different tool at a different stage. No tool ever sees the macro's internal
circuit — only these views. In LibreLane these views are the fields of a `Macro`
object (§4).

| View | Consumed by (stage) | Role | `Macro` key | In this repo |
|---|---|---|---|---|
| **Blackbox `.v` / `.vh`** | Synthesis (Yosys) | Port names + I/O widths only — **no timing**. Macro is *referenced, not flattened*. | `vh` | ✅ `src/autombist/templates/sram_blackbox_template.j2` (`(* blackbox *)`) |
| **LEF** (abstract) | Place & route | **Required.** Cell outline, pin locations, routing blockages; only metal + connection layers, not full layout. | `lef` | ⛔ only via OpenRAM |
| **Liberty `.lib`** | Synthesis + STA | Timing/power model (corner-keyed). | `lib` | ⛔ only via OpenRAM |
| **GDS** | Tapeout / signoff | **Required.** Full layout; superset of the LEF. | `gds` | ⛔ only via OpenRAM |
| **Behavioral `.v`** | Simulation | Functional model (the only view a functional sim needs). | — (sim, outside the flow) | ✅ `rtl/sram_model.sv` + DUT copies |
| Netlist / powered netlist / SPEF | STA / signoff | Optional accuracy inputs. | `nl` / `pnl` / `spef` | — |

Verified subtleties that matter for a hand-authored macro:

- **`gds` and `lef` are the two *required* views** — LibreLane's `Macro` errors if
  either is missing — while `lib`/`nl`/`pnl`/`spef`/`vh` are optional. The LEF is
  "the 'interface' of a Macro" (dimensions + pin locations); the GDS carries
  everything the LEF does plus more. *(verified 3-0, [OpenLane 2 / LibreLane
  using_macros]; corroborated by [LibreLane docs])*
- **No cross-view consistency check.** "It is the responsibility of the user to
  make sure that GDS matches LEF." Nothing validates that the abstract views agree
  with each other or with the behavioral model on the port list — if they
  disagree, it fails downstream, not at ingest. *(verified 3-0, [OpenLane
  openram.md])*

## 2. Black-boxing during synthesis

Synthesis must know the macro's ports but must never elaborate/flatten its
internals. The universal concept: a **port-only view** carries the interface, and
the macro is kept as an opaque cell.

- **In LibreLane**, the macro's **`vh` (Verilog header)** provides the blackbox
  definition for synthesis; the `__pnr__` preprocessor flag lets synthesis use the
  hardened macro's header in place of the RTL. If no `vh` is supplied, the `lib`
  view serves as the fallback that makes the macro known to synthesis. *([LibreLane
  using_macros])*
- **The underlying Yosys mechanism** (visible in the classic OpenLane 1 driver):
  `read_liberty -lib -ignore_miss_dir -setattr blackbox $lib` reads the macro's
  Liberty as an opaque blackbox, the same call used for standard-cell libs; and a
  blackbox Verilog "tells the synthesis tool the purpose and width of the input and
  output but does not carry information regarding the timings." *(verified 3-0,
  [synth.tcl], [digital_guide])* — this is exactly what our
  `sram_blackbox_template.j2` produces (`(* blackbox *)` port-only module).

> **Correction (refuted claim).** A draft claim that Liberty is the *sole* carrier
> of a macro into synthesis was **refuted 1-2**. Accurate: a **port-only view**
> (LibreLane `vh`, or a blackbox Verilog, or Liberty-with-`blackbox`) makes the
> macro known to synth; LEF/GDS are P&R/tapeout inputs, not synthesis inputs.

**Missing/mismatched view → hard failure.** If synthesis references a macro it was
never told about: `ERROR: Module '\...sram...' referenced in ... is not part of the
design.` *(verified 2-0, [openram tutorial])*

## 3. Physical integration in OpenROAD (flow-independent)

OpenROAD is the P&R engine under LibreLane, so these are the same regardless of
front-end:

- **Placement.** Automatic via the `mpl` hierarchical macro placer **"Hier-RTLMP"**
  (large complex IP), or manual via `place_macro` (single macro at a chosen
  location). *(verified 3-0, [OpenROAD mpl])*
- **Keep-out / halos.** `set_macro_base_halo` / `set_macro_halo`;
  `block_macro_channels` turns halos into **soft placement blockages** so standard
  cells stay out of macro channels. LibreLane surfaces these via
  **`FP_MACRO_HORIZONTAL_HALO` / `FP_MACRO_VERTICAL_HALO`**. *(verified 3-0,
  [OpenROAD mpl]; [LibreLane using_macros])*
- **Power delivery.** `define_pdn_grid -macro` gives macros a dedicated grid; straps
  land on the macro's PG pins via `-grid_over_pg_pins` (default) or the whole
  boundary via `-grid_over_boundary`. A **LEF-declared halo overrides** the PDN
  default. *(verified 3-0, [OpenROAD pdn])*
- **PG hookup (LibreLane).** `USE_POWER_PINS` (with `VERILOG_POWER_DEFINE`) exposes
  the macro's power pins in the netlist; **`PDN_MACRO_CONNECTIONS`** manually maps
  each macro instance's PG pins to the design's power nets. *([LibreLane
  using_macros])* — (the OpenLane-1 equivalent was `FP_PDN_MACRO_HOOKS`.)
- **Routing blockages** come from the macro LEF's obstruction layers.

## 4. LibreLane config: the `MACROS` dict (this is the seam to match)

LibreLane declares hardened macros in a single global **`MACROS`** dictionary:
keys are macro **names** (not instances), values are `librelane.config.Macro`
dataclasses. Each abstract view is a field on the `Macro`, and each instantiation
is placed inside its `instances` dict. Canonical (JSON) form, adapted from the
docs' own example:

```text
// One entry per hardened block. Paths use the dir:: relative-path prefix.
"MACROS": {
  "sram_model_redundant": {
    "gds": ["dir::./gds/sram_model_redundant.gds"],             // required
    "lef": ["dir::./lef/sram_model_redundant.lef"],             // required (P&R: outline/pins/blockage)
    "instances": {                                              // placement, per instantiation
      "u_sram": { "location": [10, 20], "orientation": "N" }    // microns; orientation per LEF/DEF
    },
    "vh":  ["dir::./vh/sram_model_redundant.vh"],               // blackbox header for synthesis
    "nl":  ["dir::./gl/sram_model_redundant.v"],                // optional gate-level netlist
    "lib": { "nom_*": ["dir::./lib/sram_model_redundant.nom.lib"] },   // optional, corner-wildcard keys
    "spef":{ "nom_*": ["dir::./spef/sram_model_redundant.nom.spef"] }  // optional parasitics
  }
}
```

Exact fields of the `Macro`/`Instance` dataclasses (from the config API reference):

- **Required (no default — the class errors if empty):** `gds` (`List[Path]`),
  `lef` (`List[Path]`).
- **`instances`** (`Dict[str, Instance]`) — defaults to empty, but is where each
  instantiation's placement lives: `Instance.location` (`Tuple[Decimal, Decimal]`,
  microns) and `Instance.orientation` (LEF/DEF orientation, e.g. `"N"`).
- **Optional, all default-empty:** `vh` (`List[Path]`, Verilog headers — the
  synthesis blackbox), `nl` / `pnl` (`List[Path]`, netlists), `lib` / `spef` /
  `sdf` (`Dict[str, List[Path]]`, **corner-wildcard-keyed**, e.g. `"nom_*"`,
  `"max_*"`), `spice` (`List[Path]`), `json_h` (`Path`). STA picks `nl`+`spef` vs
  `lib` per **`STA_MACRO_PRIORITIZE_NL`**.
- **Placement/power/halo** variables outside the `Macro`: power via
  **`USE_POWER_PINS`** (with **`VERILOG_POWER_DEFINE`**) or **`PDN_MACRO_CONNECTIONS`**;
  halos via **`FP_MACRO_HORIZONTAL_HALO`** / **`FP_MACRO_VERTICAL_HALO`**.

> **Legacy (not used here).** Classic **OpenLane 1** points at each view with a
> separate Tcl variable — `EXTRA_LEFS`, `EXTRA_LIBS`, `EXTRA_GDS_FILES`,
> `VERILOG_FILES_BLACKBOX`, `MACRO_PLACEMENT_CFG`, `FP_PDN_MACRO_HOOKS`. Same
> concepts, different (older) surface. *(verified 3-0, [OpenLane chip_integration])*
> If you ever read an OpenLane-1 tutorial, mentally map those onto the `Macro`
> fields above.

## 5. OpenRAM's view set and pinout contract

OpenRAM emits the full multi-view package a standard-cell library would — `.sp`
(SPICE), `.gds`, behavioral `.v`, `.lib`, `.lef` — one bundle per generated macro.
*(consistent with [OpenRAM] docs and this repo's `scripts/synthesize_sram.py`,
which drives exactly this generation.)*

**Single-port pinout** (the contract a redundant model must match pin-for-pin).
This repo's `rtl/sram_model.sv` / `tests/hardware/sram_tiny.v` already follow the
OpenRAM single-port convention:

| Pin | Meaning |
|---|---|
| `clk0` | clock (active-high / posedge) |
| `csb0` | chip-select, **active-low** |
| `web0` | write-enable, **active-low** (0 = write, 1 = read) |
| `addr0` | address |
| `din0` | write data |
| `dout0` | read data (registered; 1-cycle latency in our model) |
| `wmask0` | per-bit write mask (write-masked variants) |

Our wrapper (`wrapper_template.j2`) binds a controller to exactly this set by pin
*name* (the config `ports:` block), so any macro matching the contract drops in.

## 6. What this means for the redundant SRAM model (the actual split)

The design decision actually taken — and now proven through the real LibreLane
RTL-to-GDS flow — is **external remap around a stock, spare-augmented memory**,
not repair pins baked into a hand-written macro. Repair steering is a
**standard-cell-logic concern**, so it never touches the macro's own view set
(LEF/Liberty/GDS/`vh`) at all; only the wrapper's `repair_ports:` boundary and
the surrounding logic change. That splits the work into two tiers:

### Tier 1 — behavioral repair loop, built and proven in real hardening

The memory model is `rtl/sram_model_spares.sv`: it widens `addr0` to
`MEM_ADDR_WIDTH` so `NUM_SPARE_ROWS` spare rows are addressable as the top
addresses (mirroring how OpenRAM exposes spare rows), plus an optional
compile-time hard-defect knob for test DUTs — **keeping the `sram_model.sv`
pinout** (`clk0`, `csb0`-low, `web0`-low, `addr0`, `din0`, `dout0`) exactly.
As its own header states, **there are no repair pins on this module**: all
remap steering lives outside, in `rtl/repair_remap_row.sv` — a purely
combinational block that sits between the address mux and the memory and
substitutes a spare-row physical address for any logical address a loaded
repair register marks faulty. This is exactly the architecture that lets the
memory macro itself stay a stock, unmodified view set (§2, §4) while repair
becomes ordinary synthesizable logic around it.

On-chip self-repair (autonomous BIRA analyzer + BISR sequencer +
`repair_remap_row`, config key `redundancy.onchip_selfrepair: true`) now covers
five of the project's six march algorithms — march-C, march-raw, march-X,
MATS+ (all single-port), and march-1r1w (the first multi-port case: a
read-only + write-only dual-port memory steered by one `repair_remap_row`
shared off the FSM's address register). march-2rw is **deliberately excluded**:
its concurrent same-cycle dual compare breaks the analyzer's
single-fail-per-cycle assumption — a documented scope boundary, not a gap to
be closed later without new arbitration RTL.

### Tier 2 — LibreLane-integrable / tapeout-ready repairable macro

Because repair pins never touch the macro's own views, hardening a self-repair
design in LibreLane needed **no new LEF/Liberty/`vh`/GDS work at all** — the
real macros' pre-existing widened `addr0` + `spare_wen0` pins (already present
whenever OpenRAM is asked for spare rows) were sufficient. This has now been
proven clean through the full LibreLane RTL-to-GDS flow for three single-port
algorithms, each wrapping an unmodified real OpenRAM macro (`.spare_wen0(1'b0)`,
no macro modification):

- march-C, wrapping 3 real macros in a multi-memory subsystem
  (`flow/multimem/mbist/`).
- march-X, wrapping `sky130_sram_32b256w` (`flow/newalgo/`, target
  `selfrepair_x`) — hardens clean (Antenna pass, LVS pass, exit 0).
- MATS+, wrapping `sky130_sram_32b512w` (`flow/newalgo/`, target
  `selfrepair_mp`) — hardens clean (Antenna pass, LVS pass, exit 0).

march-1r1w's multi-port self-repair scaffold **has** been hardened against a
real, genuinely dual-port OpenRAM macro (`sky130_sram_1r1w_32b256w`, purpose
-built for this: `num_r_ports=1`, `num_w_ports=1`). The flow completes all 80
stages with LVS and Antenna both passing; DRC is not clean, and the dominant
finding traces to a documented, currently-unresolved OpenROAD/sky130 tapcell
limitation ([OpenROAD#7118](https://github.com/The-OpenROAD-Project/OpenROAD/issues/7118),
[OpenLane#1140](https://github.com/The-OpenROAD-Project/OpenLane/issues/1140)),
not to this project's RTL or LibreLane config — four independent, targeted
attempts (floorplan, tap-cell distance, macro halo) confirmed it doesn't
respond to anything under this project's control. See `flow/newalgo/README.md`
for the full breakdown. march-2rw is out of scope entirely (see above), so
there is nothing to harden there.

**OpenRAM-redundancy status** (`scripts/synthesize_sram.py`): OpenRAM's flow
accepts `num_spare_rows` / `num_spare_cols` unconditionally for every tech
(`synthesize_sram.py:74-79` — a generic `sram_config.py` feature, not
sky130-specific; the code's own comment notes emitting it only under the
sky130 branch would silently no-op the flags on other techs), which add
**physical** spare rows/columns to the array. OpenRAM itself generates no
repair *logic* — the BIRA analyzer, BISR sequencer, and `repair_remap_row` are
all this project's own RTL, sitting outside the macro's view set as described
above.

### Symmetry with the `repair_ports` seam already built

The `repair_ports:` config (validated by `_validate_repair_ports` in
`src/autombist/generator.py`) puts repair pins on the **wrapper** boundary and
binds them to the macro instance — pin names are arbitrary, declared per-entry
as `{name, width, dir}`, not fixed names like `repair_valid`/`repair_addr`
(those are just the docstring's illustrative example). The redundant
**macro's own** view set (the blackbox stub `sram_blackbox_template.j2`, and
its real LEF/Liberty/GDS) never needs the same pins — it stays exactly the
stock macro contract from §1–§5, which is what let Tier 2 close without any
macro-side work.

## Sources

- **LibreLane — Using Macros** (the `MACROS` dict / `Macro` fields / placement /
  power / halo variables): <https://librelane.readthedocs.io/en/latest/usage/using_macros.html>
- LibreLane — config API (`librelane.config` / `Macro`): <https://librelane.readthedocs.io/en/latest/reference/api/config/index.html>
- LibreLane — variable migration guide (OpenLane 2 → LibreLane renames): <https://librelane.readthedocs.io/en/latest/getting_started/migrants/variables.html>
- OpenLane 2 — Using Macros (LibreLane's direct predecessor; the verified `MACROS`/view-key claims): <https://openlane2.readthedocs.io/en/latest/usage/using_macros.html>
- OpenROAD — PDN generator (macro power grid, halos): <https://openroad.readthedocs.io/en/latest/main/src/pdn/README.html>
- OpenROAD — Macro Placement `mpl` (Hier-RTLMP, halos, blockages): <https://openroad.readthedocs.io/en/latest/main/src/mpl/README.html>
- OpenRAM: <https://github.com/VLSIDA/OpenRAM> · <https://openram.org/>
- Cornell ECE5745 SRAM tutorial (view set + pinout, *edu/secondary*): <https://cornell-ece5745.github.io/ece5745-tut8-sram/>
- *Legacy reference* — OpenLane 1 Chip Integration (`EXTRA_*` Tcl vars, not used here): <https://openlane.readthedocs.io/en/2023.09.07/usage/chip_integration.html> and its Yosys `synth.tcl`: <https://github.com/The-OpenROAD-Project/OpenLane/blob/master/scripts/yosys/synth.tcl>

> **Caveat.** The view-set, black-boxing concept, OpenROAD, and OpenRAM facts were
> adversarially verified in the research pass; the LibreLane config (§4) was
> confirmed field-by-field against LibreLane's *Using Macros* guide and its
> *config API* dataclass reference. LibreLane is still actively developed, so
> re-check field spellings against your installed version's docs before a real
> tapeout config. The Tier-2 abstract-view authoring (getting repair pins into a
> real LEF/Liberty) is the part that still needs hands-on work when tapeout
> matters — that is design effort, not a lookup.
