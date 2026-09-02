#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_date="$(TZ=America/Sao_Paulo date -v+1d +%Y%m%d 2>/dev/null || TZ=America/Sao_Paulo date -d tomorrow +%Y%m%d)"
config_path="${PREMATCH_CONFIG:-${repo_dir}/config/local.json}"
run_dir="${repo_dir}/runs/${run_date}"
extra=()
if [[ -n "${PREMATCH_FIXTURES_FILE:-}" ]]; then
  extra+=(--fixtures-file "${PREMATCH_FIXTURES_FILE}")
fi
if [[ -n "${PREMATCH_RESEARCH_DIR:-}" ]]; then
  extra+=(--research-dir "${PREMATCH_RESEARCH_DIR}")
fi
if [[ "${PREMATCH_DRY_RUN:-0}" == "1" ]]; then
  extra+=(--dry-run)
fi

preflight_extra=()
if [[ "${PREMATCH_DRY_RUN:-0}" == "1" ]]; then
  preflight_extra+=(--dry-run)
fi

python3 -m jaguartv_prematch.cli preflight --config "${config_path}" "${preflight_extra[@]}"
python3 -m jaguartv_prematch.cli run --config "${config_path}" --run-dir "${run_dir}" "${extra[@]}"

printf 'Workflow complete: %s\n' "${run_dir}"
