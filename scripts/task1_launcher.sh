#!/usr/bin/env bash
# JaguarTV Prematch 任务一 — robust launcher for task1_driver.py
#
# Wraps the driver with: managed-node PATH (lark-cli), the repo venv python,
# dated logging, and strict exit-code propagation so the automation can tell
# success from failure.
#
# Usage:
#   task1_launcher.sh <batch:auto|positive-number> [extra driver args...]
# Examples:
#   task1_launcher.sh 1                # production batch 1 (live fixture collect)
#   task1_launcher.sh 2                # production batch 2
#   task1_launcher.sh 3                # force a third differentiated batch
#   task1_launcher.sh 1 --dry-run      # preflight smoke test
#   task1_launcher.sh 1 --fixtures-file /path/to/fixtures.json   # file fallback
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER="$REPO/scripts/task1_driver.py"
VENV_PY="$REPO/.venv/bin/python"
LOGDIR="$REPO/runs/automation-logs"
mkdir -p "$LOGDIR"

# lark-cli lives in the managed node workspace; put managed node on PATH
export PATH=/Users/jaguar/.workbuddy/binaries/node/versions/22.22.2-2/bin:$PATH

BATCH="${1:-auto}"; shift || true
TS=$(date +%Y%m%d-%H%M%S)
LOG="$LOGDIR/task1_B${BATCH}_${TS}.log"

echo "[launcher $(date -u +%FT%TZ)] batch=$BATCH log=$LOG" | tee -a "$LOG"
"$VENV_PY" "$DRIVER" --batch "$BATCH" "$@" 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
echo "[launcher] driver exit=$RC" | tee -a "$LOG"
exit "$RC"
