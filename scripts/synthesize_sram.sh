#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_ROOT="${REPO_ROOT}/input"

mkdir -p "${OUTPUT_ROOT}"

if [[ -d "/pdk" ]]; then
    DEFAULT_PDK_ROOT="/pdk"
elif [[ -d "${REPO_ROOT}/pdk" ]]; then
    DEFAULT_PDK_ROOT="${REPO_ROOT}/pdk"
else
    DEFAULT_PDK_ROOT="${REPO_ROOT}/OpenRAM"
fi

PDK_ROOT_SELECTED="${PDK_ROOT:-${DEFAULT_PDK_ROOT}}"

exec python3 "${SCRIPT_DIR}/synthesize_sram.py" \
    --output-root "${OUTPUT_ROOT}" \
    --pdk-root "${PDK_ROOT_SELECTED}" \
    "$@"
