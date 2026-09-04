#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
extra=()
batch="${PREMATCH_BATCH:-auto}"
if [[ -n "${PREMATCH_CONFIG:-}" ]]; then
  extra+=(--config "${PREMATCH_CONFIG}")
fi
if [[ -n "${PREMATCH_FIXTURES_FILE:-}" ]]; then
  extra+=(--fixtures-file "${PREMATCH_FIXTURES_FILE}")
fi
if [[ -n "${PREMATCH_RESEARCH_DIR:-}" ]]; then
  extra+=(--research-dir "${PREMATCH_RESEARCH_DIR}")
fi
if [[ "${PREMATCH_DRY_RUN:-0}" == "1" ]]; then
  extra+=(--dry-run)
fi

exec "${repo_dir}/scripts/task1_launcher.sh" "${batch}" "${extra[@]}"
