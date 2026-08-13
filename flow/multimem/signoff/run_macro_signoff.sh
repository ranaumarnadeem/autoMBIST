#!/usr/bin/env bash
# Macro-internal DRC + LVS signoff for the OpenRAM sky130 macros -- the signoff
# owed because the macros were generated with `-n` (no inline DRC/LVS).
#
# Runs on the ALREADY-GENERATED GDS + OpenRAM .lvs.sp (no regeneration):
#   * magic DRC on the GDS   (drc count total)
#   * magic GDS->spice extraction, then netgen LVS extracted-layout vs .lvs.sp
# using the ciel-managed sky130A PDK's own magicrc / netgen setup.
#
# IMPORTANT: raw-GDS DRC/LVS here is INDICATIVE ONLY -- and, as of 2026-08-09,
# OpenRAM's own flow ALSO currently fails, for an unrelated, root-caused
# reason. Read both paragraphs below before trusting either result.
#
# Loading an OpenRAM GDS into ciel's STANDARD-flow magicrc (not OpenRAM's own
# tech/cif setup) flags ~1M spurious DRC violations and a spurious LVS
# net-fragmentation mismatch -- a layer/cif-style tooling mismatch, not
# evidence of a real defect on its own.
#
# The intended authority is OpenRAM's OWN flow (its exact tech/cif setup),
# via the vendored checkout's real entry point (there is no
# scripts/synthesize_sram.py in this vendored copy):
#
#   OPENRAM_HOME=OpenRAM/compiler OPENRAM_TECH=OpenRAM/technology \
#     OpenRAM/miniconda/bin/python3 OpenRAM/sram_compiler.py -i <config.py>
#
# (a .py config with check_lvsdrc=True; -i/--inlinecheck; regenerates the
# macro WITH inline DRC/LVS, slower). BUT: as of 2026-08-09 this ALSO fails,
# for a confirmed, unrelated, root-caused reason, not a macro defect: the
# vendored OpenRAM/ checkout (HEAD 449781d2, dated 2026-04-08) predates
# upstream's own fix for a wordline-pin-numbering defect in the sky130
# replica bitcell array. Reproduced directly: regenerating
# sky130_sram_32b256w with check_lvsdrc=True fails inside OpenRAM's own
# sky130_bitcell_array (the internal self-timing replica array, not the main
# data array) with 16776 real DRC errors and a real netgen LVS failure --
# wordline pins (wl_0_N) matched to the wrong index across
# extracted-vs-schematic (topologically equivalent, scrambled
# correspondence). Fixed upstream by commit 50772821 "count wordlines from
# bottom going up" (2026-04-28, 20 days after the vendored HEAD) and
# 8c4f4ef2 "when routing between the wordline drivers and the wordline pins
# of the crba... to resolve drc violations" (2026-05-14) -- confirmed via
# `git -C OpenRAM merge-base --is-ancestor <commit> HEAD` that neither is an
# ancestor of the vendored checkout. The failure is entirely inside
# OpenRAM's own bitcell-array module creation, before any openMBIST-authored
# logic is reached.
#
# Until OpenRAM/ is updated past those two commits and every macro this
# project depends on is re-verified, NEITHER this script's raw-GDS check NOR
# OpenRAM's own regeneration gives a clean signoff answer -- so the three
# macros are unverified rather than confirmed either good or bad. Note their
# .gds/.lvs.sp are NOT committed: they are gitignored build products that
# `autombist ram-synth` regenerates on demand.
#
# Requires magic + netgen on PATH (available in the LibreLane nix closure) and
# the sky130 PDK at $PDK_ROOT (default ~/.ciel).
#
# Usage:
#   ./run_macro_signoff.sh                 # all three multimem macros
#   ./run_macro_signoff.sh <name> ...      # specific macro dir name(s)
# Env: MACRO_OUT (default ~/sky130_macro_out), PDK_ROOT (default ~/.ciel),
#      OUTDIR (default ~/macro_signoff)
#
# EXIT CODE contract: 0 only when every macro's LVS reported a genuine match.
# LVS is what gates, because per the box above it is the meaningful check here
# and the raw-GDS DRC count is not. The DRC count is reported (and its three
# outcomes -- a number, or "could not determine" -- kept distinct) but is
# deliberately NOT gated on: gating it would fail every real macro on the ~1M
# spurious violations the box describes. A magic crash still fails the run,
# transitively: no extracted spice means LVS cannot run, and an LVS that could
# not run does not pass.
set -u

# --- parsing helpers -------------------------------------------------------
# Defined before the source guard below so tests can source this file and call
# them without running the flow (see tests/software/test_macro_signoff_parse.py).

# Echo magic's DRC total from a drc.log; return 1 (echoing nothing) when no
# count can be determined, so "clean" and "magic never got there" stay apart.
parse_drc_count() {
  local log="$1" region line
  [ -f "$log" ] || return 1
  # Strictly BETWEEN the sentinels. `grep -A2 DRC_COUNT_BEGIN` printed the
  # sentinel line FIRST, and "DRC_COUNT_BEGIN" itself matches a case-insensitive
  # /count/ -- so head -1 always selected the sentinel, which has no digits, and
  # the count was always empty. The 2-line window was also too narrow for
  # magic's real output.
  region=$(awk '/DRC_COUNT_BEGIN/{f=1;next} /DRC_COUNT_END/{f=0} f' "$log")
  line=$(printf '%s\n' "$region" | grep -iE "total|error tiles|count" | grep -E "[0-9]" | tail -1)
  [ -n "$line" ] || return 1
  printf '%s\n' "$line" | grep -oE "[0-9]+" | tail -1
}

# Echo MATCH / MISMATCH / UNKNOWN for a netgen lvs.out.
classify_lvs() {
  local out="$1"
  [ -f "$out" ] || { echo UNKNOWN; return 0; }
  # Negative FIRST: "Circuits do not match." also contains the substring
  # "match", so any positive test has to run second or it swallows a real
  # mismatch -- which is precisely how the old code could print
  # "Circuits do not match." and still exit 0.
  if grep -qiE "do not match|mismatch" "$out"; then echo MISMATCH; return 0; fi
  if grep -qiE "circuits match|netlists match" "$out"; then echo MATCH;    return 0; fi
  echo UNKNOWN
}

# Sourced (by a test) rather than executed: stop here, expose only the helpers.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  return 0
fi

MACRO_OUT="${MACRO_OUT:-$HOME/sky130_macro_out}"
# Exported, not just set: magic's own sky130A.magicrc bootstrap reads
# $env(PDK_ROOT) directly (a Tcl `$env(...)` lookup only sees real
# environment variables, not a plain shell variable of the same name) and,
# if unset, silently falls back to a hardcoded volare-style path baked into
# ciel's generated magicrc that does not exist on this system. That fallback
# doesn't error -- magic just runs with no real PDK tech loaded ("technology
# minimum"), so DRC trivially reports 0 (nothing was checked) and extraction
# produces nothing or garbage, masquerading as a clean run or a spurious LVS
# mismatch instead of the actual missing-PDK failure.
export PDK_ROOT="${PDK_ROOT:-$HOME/.ciel}"
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

  # Reported, never gated -- see the EXIT CODE contract note in the header.
  if drc_n=$(parse_drc_count "$WD/drc.log"); then
    echo "[$M] DRC: $drc_n violation(s) -- INDICATIVE ONLY, not signoff  (see $WD/drc.log)"
  else
    echo "[$M] DRC: COULD NOT DETERMINE -- no count in magic's output  (see $WD/drc.log)"
  fi

  # ---- LVS (netgen: extracted layout vs OpenRAM schematic .lvs.sp) ----
  if [ -f "$WD/$M.ext.spice" ] && [ -f "$SRC" ]; then
    netgen -batch lvs "$WD/$M.ext.spice $M" "$SRC $M" "$NETGEN_SETUP" "$WD/lvs.out" > "$WD/lvs.log" 2>&1
    lvs_rc=$?
    case "$(classify_lvs "$WD/lvs.out")" in
      MATCH)
        echo "[$M] LVS: MATCH  (see $WD/lvs.out)" ;;
      MISMATCH)
        echo "[$M] LVS: MISMATCH -- extracted layout differs from $SRC  (see $WD/lvs.out)"
        overall=1 ;;
      *)
        echo "[$M] LVS: COULD NOT DETERMINE -- netgen rc=$lvs_rc  (see $WD/lvs.log)"
        overall=1 ;;
    esac
  else
    # Not a pass: a check that never ran cannot be signed off on.
    echo "[$M] LVS: SKIP -- no extracted spice or no $SRC  (see $WD/drc.log for the magic run)"
    overall=1
  fi

  echo
done

echo "MACRO_SIGNOFF_DONE overall_rc=$overall"
exit "$overall"
