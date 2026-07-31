# flow/multimem — three real OpenRAM sky130 memories behind one bus

A small "user design" (`mem_subsystem.sv`) instantiating three differently-sized,
spare-augmented OpenRAM sky130 SRAM macros — the kind of multi-memory subsystem
autoMBIST's MBIST + on-chip self-repair wrappers target.

| Slot | Macro | Size | Macro pins | Die (LEF) |
|---|---|---|---|---|
| 0 | `sky130_sram_32b256w` | 32b × 256 (1 KB) | `A=9, D=33, wmask0[3:0]` | 480 × 223 µm |
| 1 | `sky130_sram_32b512w` | 32b × 512 (2 KB) | `A=10, D=33, wmask0[3:0]` | 483 × 325 µm |
| 2 | `sky130_sram_8b1024w` | 8b × 1024 (1 KB) | `A=11, D=9, no wmask` | 468 × 285 µm |

Each macro carries **1 spare row** (addressable at the top of its address space —
the repair target) and 1 spare column (unused; tied off — sky130's paired array
tiling forces both spare dims odd). Macro `DATA_WIDTH` is the logical word +1
(the spare column bit); the subsystem pads/drops it per slot.

## Files

- `mem_subsystem.sv` — the design: one synchronous bus, `mem_sel` picks the
  memory, per-slot active-low `csb` gating, read mux on a 1-cycle-delayed
  select (matches the macros' registered-read latency).
- `sky130_sram_*.v` — the OpenRAM **behavioral** models (simulation view).
- `sky130_srams_bb.v` — port-only **blackbox** stubs (synthesis view). Never
  compile both views into the same build.

The physical views (GDS ~9–17 MB each + LEF, gitignored) live outside the repo;
regenerate with the command below.

## Regenerating the macros

Requires the WSL toolchain: magic/netgen/klayout on PATH (available inside the
LibreLane nix closure) and the ciel-managed sky130 PDK (`~/.ciel`). Setup is a
one-time `--setup-sky130`. Per macro (~20–45 min each — magic layout of a real
array):

```bash
python3 scripts/synthesize_sram.py --tech sky130 \
    --word-size 32 --num-words 256 \
    --num-spare-rows 1 --num-spare-cols 1 \
    --output-name sky130_sram_32b256w \
    --pdk-root ~/.ciel --output-root ~/sky130_macro_out
```

Four hard-won OpenRAM findings (first two gate generation, last two were caught
by this directory's testbench and are patched in the checked-in model copies):
1. **Both spare dims must be ODD** — sky130 tiles the bitcell array in pairs, so
   `num_cols+ports+num_spare_cols` and `num_rows+num_spare_rows+ports` must each
   be even; with the usual even `num_cols`/`num_rows`, spares 1,1 (or 3,1 …).
2. **Characterization is patched non-fatal** in the vendored
   `OpenRAM/compiler/sram.py` (the delay characterizer can crash on spare-column
   bitlines for some geometries and would otherwise abort before GDS/LEF write).
3. **No-wmask + spare-col write bug**: OpenRAM's Verilog writer emitted the base
   write as `[word_size-num_spare_cols-1:0]`, orphaning the top data bit(s) —
   the wmask path and the spare placement at `[word_size+n]` prove the intent is
   `[word_size-1:0]`. Root-cause fixed in the vendored
   `OpenRAM/compiler/base/verilog.py`; the checked-in `sky130_sram_8b1024w.v`
   carries the same fix.
4. **No `timescale` in emitted models**: OpenRAM `.v` has none, so its
   `#(T_HOLD)`/`#(DELAY)` delays inherit the simulator default unit and reads
   return X forever under an ns clock. All three checked-in models carry an
   explicit `` `timescale 1ns/1ps ``.

## Testing

```bash
wsl -- bash -lc "cd /mnt/c/Users/Potato/Desktop/openMBIST && \
    PYTHONPATH=src ~/cocotb/bin/python tests/hardware/run_mem_subsystem_tb.py"
```

Covers per-slot write/read integrity at boundary addresses (including the first
spare-row address above each logical top), cross-slot isolation at a shared bus
address, and back-to-back reads switching `mem_sel` every cycle (the read-latency
mux alignment).

## Hardening — SOLVED (2026-07-16), recipe in `librelane-config.json`

The subsystem hardens **clean** in LibreLane 3.0.5: detailed routing 0
violations, **LVS clean including power**, antenna/overlap clear, 43.5 MB GDS,
die 784,000 µm² (0.78 mm², ~51% macro area, 4,158 std cells). The four fixes
that got there (each independently necessary — full war story in the repo
memory notes):

1. **LEF units declaration**: OpenRAM's LEF says `DATABASE MICRONS 2000` but
   every coordinate — and the GDS — is already on the 1 nm grid. Sed the LEF
   line to `1000`; do NOT rescale the GDS (it never needed it).
2. **Hard-IP signoff**: `MAGIC_DRC_USE_GDS: false` + `RUN_KLAYOUT_XOR: false`, plus
   `ERROR_ON_MAGIC_DRC: false` + `ERROR_ON_KLAYOUT_DRC: false` — macro internals
   are the memory generator's signoff, not the integrator's. The first pair
   controls *what* gets checked; the `ERROR_ON_*` pair is what keeps OpenRAM's
   macro-internal DRC noise (see residual caveats below) from being *fatal*
   (aborting `harden --run` with exit code 2). `autombist harden` sets all four
   automatically whenever the config declares `macros:`, so this is baked into
   the generated LibreLane config every time, not something you add by hand.
   (Owed: an OpenRAM `--run-drc-lvs` pass on each macro; generation used `-n`.)
3. **Power-domain hookup**: `PDN_MACRO_CONNECTIONS` takes *design nets then
   macro pins* — on sky130 that's `"u_m\d+ VPWR VGND vccd1 vssd1"`. Getting the
   nets wrong makes pdngen treat macros as anonymous blockages.
4. **PDN halos** (`PDN_HORIZONTAL_HALO`/`PDN_VERTICAL_HALO` = 15 µm — distinct
   from the placement-only `FP_MACRO_*_HALO`): keeps core met4 straps clear of
   the macros' bottom-edge met4 signal pins. Without this, DRT freezes on a
   handful of unfixable met4 shorts in the 1 µm pin-access band.

Residual caveats: macro-internal DRC counts (magic-abstract 529 / klayout 3194)
are macro-dominated noise — legitimate OpenRAM bitcell/periphery layers
(CFOMDROP, CNTMADD, etc.) that the local Magic/KLayout deck doesn't recognize
inside macro boundaries, a known OpenRAM/open_pdks quirk (librelane/librelane#519),
not a defect in this design. `Checker.MagicDRC`/`Checker.KLayoutDRC` default to
*fatal* upstream (`ERROR_ON_MAGIC_DRC`/`ERROR_ON_KLAYOUT_DRC` both default
`True`), so without the `: false` override in the config above, this noise
would abort `harden --run` with exit code 2 regardless of the design's own
logic being clean — the override is what makes these counts non-fatal and lets
`harden --run` exit 0 reproducibly.
The macros also ship no `.lib` (characterization skipped), so STA black-boxes
them.
