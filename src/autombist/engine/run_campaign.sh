#!/usr/bin/env bash
# Serial fault campaign: one simulation per fault, plus a golden run.
#
#   ./run_campaign.sh [faults.txt] [ALG]
#     ALG: a name with a sibling <ALG>.algc numeric file in this directory
#     (as algo_shell.py's export_tb writes for every algorithm registered in
#     the session, e.g. march_c, march_x, or a custom name) -- loaded via
#     +ALG_FILE=. Falls back to one of the engine's 3 fixed built-in tables,
#     {MATSP, MARCHCM, MARCHSS}, loaded via +ALG=, only when no such file
#     exists (this script's original use: run directly from
#     src/autombist/engine, with no exported .algc files around). Default
#     MARCHCM.
#     SIM=xrun|verilator to force a simulator (default: xrun if found)
#
# Output: campaign_<ALG>.csv  with columns
#   idx,type,result,elem,op,addr

set -u
FAULTS="${1:-faults.example.txt}"
ALG="${2:-MARCHCM}"
SIM="${SIM:-auto}"
CSV="campaign_${ALG}.csv"

# See the ALG description above: an exported bundle's own algorithm(s) always
# have a same-named .algc file, which the 3 fixed built-in tables cannot
# represent (any algorithm other than MATS+, March C-, or March SS -- e.g.
# March X, March B, or a custom .alg -- has no built-in table at all, and
# +ALG=<that name> would abort with "FATAL: unknown +ALG=...").
if [ -f "${ALG}.algc" ]; then
  ALG_PLUSARG="+ALG_FILE=${ALG}.algc"
else
  ALG_PLUSARG="+ALG=${ALG}"
fi

if [ "$SIM" = "auto" ]; then
  if command -v xrun >/dev/null 2>&1; then SIM=xrun; else SIM=verilator; fi
fi

if [ "$SIM" = "verilator" ]; then
  verilator --binary --timing -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND \
    --top-module march_engine fault_ram.sv march_engine.sv -o march_engine_sim >/dev/null
  RUN() { ./obj_dir/march_engine_sim "$@" 2>/dev/null; }
else
  RUN() { xrun -64bit -q -sv fault_ram.sv march_engine.sv "$@" 2>/dev/null; }
fi

NF=$(grep -cv '^[[:space:]]*\(#\|$\)' "$FAULTS")
echo "campaign: $NF faults, alg=$ALG, sim=$SIM"

echo "idx,type,result,elem,op,addr" > "$CSV"

# Capture the FULL raw output before filtering for a RESULT line: piping
# straight into grep (as this used to) discards everything else, so a crash
# with no RESULT line at all (e.g. the FATAL this script's ALG handling used
# to produce) left $G empty and "golden run FAILED: " with no reason.
RAW=$(RUN "$ALG_PLUSARG")
G=$(echo "$RAW" | grep '^RESULT')
case "$G" in
  *ESCAPED*) echo "golden: clean" ;;
  *)         echo "golden run FAILED: ${G:-$RAW}"; exit 1 ;;
esac

DET=0
for i in $(seq 0 $((NF - 1))); do
  OUT=$(RUN "$ALG_PLUSARG" +FAULTS="$FAULTS" +FAULT_INDEX="$i")
  TYPE=$(echo "$OUT" | sed -n 's/^FAULT_LOADED.*type=\([A-Z_0-9]*\).*/\1/p')
  RES=$(echo  "$OUT" | grep '^RESULT')
  if echo "$RES" | grep -q DETECTED; then
    E=$(echo "$RES" | sed -n 's/.*elem=\([0-9]*\).*/\1/p')
    O=$(echo "$RES" | sed -n 's/.*op=\([0-9]*\).*/\1/p')
    A=$(echo "$RES" | sed -n 's/.*addr=\([0-9]*\).*/\1/p')
    echo "$i,$TYPE,DETECTED,$E,$O,$A" >> "$CSV"
    DET=$((DET + 1))
  else
    echo "$i,$TYPE,ESCAPED,,," >> "$CSV"
  fi
done

echo "coverage: $DET / $NF detected  ->  $CSV"
