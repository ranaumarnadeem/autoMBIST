#!/usr/bin/env bash
# Macro-internal DRC + LVS signoff for the OpenRAM sky130 macros -- the signoff
# owed because the macros were generated with `-n` (no inline DRC/LVS).
#
# Runs on the ALREADY-GENERATED GDS + OpenRAM .lvs.sp (no regeneration):
#   * magic DRC on the GDS   (drc count total)
#   * magic GDS->spice extraction, then netgen LVS extracted-layout vs .lvs.sp
# using the ciel-managed sky130A PDK's own magicrc / netgen setup.
#
# ┌── IMPORTANT: raw-GDS DRC here is INDICATIVE ONLY ─────────────────────────┐
# │ Loading an OpenRAM GDS into ciel's STANDARD-flow magicrc (not OpenRAM's   │
# │ own tech/cif setup) flags ~1M spurious violations -- a layer/cif-style    │
# │ mismatch, NOT real defects (OpenRAM sky130 macros are DRC-clean by        │
# │ construction). The AUTHORITATIVE macro DRC/LVS is OpenRAM's OWN flow,     │
# │ which uses its exact tech setup:                                          │
# │                                                                           │
# │   python3 scripts/synthesize_sram.py --tech sky130 --run-drc-lvs \        │
# │       --word-size W --num-words N --num-spare-rows 1 --num-spare-cols 1 \  │
# │       --pdk-root ~/.ciel --output-name <name>                             │
# │                                                                           │
# │ (regenerates the macro WITH inline DRC/LVS; slower). Use this script's    │
# │ LVS (extracted-vs-schematic) as a quick netlist cross-check; treat its    │
# │ DRC count as a smoke signal, not signoff.                                 │
# └───────────────────────────────────────────────────────────────────────────┘
#
# Requires magic + netgen on PATH (available in the LibreLane nix closure) and
# the sky130 PDK at $PDK_ROOT (default ~/.ciel).
#
# Usage:
#   ./run_macro_signoff.sh                 # all three multimem macros
#   ./run_macro_signoff.sh <name> ...      # specific macro dir name(s)
# Env: MACRO_OUT (default ~/sky130_macro_out), PDK_ROOT (default ~/.ciel),
#      OUTDIR (default ~/macro_signoff)
set -u

MACRO_OUT="${MACRO_OUT:-$HOME/sky130_macro_out}"
PDK_ROOT="${PDK_ROOT:-$HOME/.ciel}"
OUTDIR="${OUTDIR:-$HOME/macro_signoff}"
MAGICRC="$PDK_ROOT/sky130A/libs.tech/magic/sky130A.magicrc"
NETGEN_SETUP="$PDK_ROOT/sky130A/libs.tech/netgen/sky130A_setup.tcl"

MACROS=("$@")
if [ "${#MACROS[@]}" -eq 0 ]; then
  MACROS=(sky130_sram_32b256w sky130_sram_32b512w sky130_sram_8b1024w)
fi

command -v magic  >/dev/null || { echo "ERROR: magic not on PATH"; exit 2; }
command -v netgen >/dev/null || { echo "ERROR: netgen not on PATH"; exit 2; }
[ -f "$MAGICRC" ] || { echo "ERROR: no magicrc at $MAGICRC"; exit 2; }

mkdir -p "$OUTDIR"
overall=0

for M in "${MACROS[@]}"; do
  GDS="$MACRO_OUT/$M/$M.gds"
  SRC="$MACRO_OUT/$M/$M.lvs.sp"
  [ -f "$GDS" ] || { echo "[$M] SKIP: no GDS at $GDS"; overall=1; continue; }
  WD="$OUTDIR/$M"; mkdir -p "$WD"; cd "$WD" || exit 2
  cp "$MAGICRC" ./.magicrc
  echo "==================== $M ===================="

  # ---- DRC + extraction (one magic pass) ----
  magic -dnull -noconsole -rcfile ./.magicrc <<EOF > "$WD/drc.log" 2>&1
gds read $GDS
load $M -dereference
select top cell
expand
drc euclidean on
drc check
drc catchup
puts "DRC_COUNT_BEGIN"
drc count total
puts "DRC_COUNT_END"
extract do local
extract no capacitance
extract no coupling
extract no resistance
extract no adjust
extract unique
extract all
ext2spice lvs
ext2spice -o $WD/$M.ext.spice
quit -noprompt
EOF

  drc=$(grep -A2 "DRC_COUNT_BEGIN" "$WD/drc.log" | grep -iE "Total|error tiles|count" | head -1)
  drc_n=$(echo "$drc" | grep -oE "[0-9]+" | head -1)
  echo "[$M] DRC: ${drc:-<no count line>}  (see $WD/drc.log)"

  # ---- LVS (netgen: extracted layout vs OpenRAM schematic .lvs.sp) ----
  if [ -f "$WD/$M.ext.spice" ] && [ -f "$SRC" ]; then
    netgen -batch lvs "$WD/$M.ext.spice $M" "$SRC $M" "$NETGEN_SETUP" "$WD/lvs.out" > "$WD/lvs.log" 2>&1
    lvs=$(grep -iE "Circuits match|do not match|uniquely|Netlists match|Final result" "$WD/lvs.out" 2>/dev/null | tail -1)
    echo "[$M] LVS: ${lvs:-<see $WD/lvs.log>}"
  else
    echo "[$M] LVS: SKIP (missing extracted spice or $SRC)"
  fi

  [ "${drc_n:-1}" != "0" ] && overall=1
  echo
done

echo "MACRO_SIGNOFF_DONE overall_rc=$overall"
exit "$overall"
