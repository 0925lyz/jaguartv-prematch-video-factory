#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_date="$(TZ=America/Sao_Paulo date -v+1d +%Y%m%d 2>/dev/null || TZ=America/Sao_Paulo date -d tomorrow +%Y%m%d)"
config_path="${PREMATCH_CONFIG:-${repo_dir}/config/local.json}"
run_dir="${repo_dir}/runs/${run_date}"

python3 -m jaguartv_prematch.cli preflight --config "${config_path}"
python3 -m jaguartv_prematch.cli collect --config "${config_path}" --output "${run_dir}/phase1"

printf 'Collection complete: %s\n' "${run_dir}/phase1/selected-fixtures.json"
