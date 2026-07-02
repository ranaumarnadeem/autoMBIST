#!/usr/bin/env bash
# Serial fault campaign: one simulation per fault, plus a golden run.
#
#   ./run_campaign.sh [faults.txt] [ALG]
#     ALG in {MATSP, MARCHCM, MARCHSS}, default MARCHCM
#     SIM=xrun|verilator to force a simulator (default: xrun if found)
#
# Output: campaign_<ALG>.csv  with columns
#   idx,type,result,elem,op,addr

set -u
FAULTS="${1:-faults.example.txt}"
ALG="${2:-MARCHCM}"
SIM="${SIM:-auto}"
CSV="campaign_${ALG}.csv"

if [ "$SIM" = "auto" ]; then
  if command -v xrun >/dev/null 2>&1; then SIM=xrun; else SIM=verilator; fi
fi

if [ "$SIM" = "verilator" ]; then
  verilator --binary --timing -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND \
    --top-module march_tb fault_ram.sv march_tb.sv -o march_tb_sim >/dev/null
  RUN() { ./obj_dir/march_tb_sim "$@" 2>/dev/null; }
else
  RUN() { xrun -64bit -q -sv fault_ram.sv march_tb.sv "$@" 2>/dev/null; }
fi

NF=$(grep -cv '^[[:space:]]*\(#\|$\)' "$FAULTS")
echo "campaign: $NF faults, alg=$ALG, sim=$SIM"

echo "idx,type,result,elem,op,addr" > "$CSV"

G=$(RUN +ALG="$ALG" | grep '^RESULT')
case "$G" in
  *ESCAPED*) echo "golden: clean" ;;
  *)         echo "golden run FAILED: $G"; exit 1 ;;
esac

DET=0
for i in $(seq 0 $((NF - 1))); do
  OUT=$(RUN +ALG="$ALG" +FAULTS="$FAULTS" +FAULT_INDEX="$i")
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
