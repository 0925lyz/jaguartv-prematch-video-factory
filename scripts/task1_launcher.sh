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

if ! "$VENV_PY" -c 'import sys' >/dev/null 2>&1; then
  VENV_PY=/usr/bin/python3
fi
export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"

# lark-cli lives in the managed node workspace; put managed node on PATH
export PATH=/Users/jaguar/.workbuddy/binaries/node/versions/22.22.2-2/bin:$PATH

BATCH="${1:-auto}"; shift || true
TS=$(date +%Y%m%d-%H%M%S)
LOG="$LOGDIR/task1_B${BATCH}_${TS}.log"
LOCKDIR="$REPO/runs/.task1-production.lock"
LOCK_WAIT_SECONDS="${JAGUARTV_LOCK_WAIT_SECONDS:-21600}"
LOCK_STARTED=$(date +%s)

while ! mkdir "$LOCKDIR" 2>/dev/null; do
  LOCK_PID=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
  if [ -n "$LOCK_PID" ] && ! kill -0 "$LOCK_PID" 2>/dev/null; then
    rm -f "$LOCKDIR/pid"
    rmdir "$LOCKDIR" 2>/dev/null || true
    continue
  fi
  LOCK_ELAPSED=$(( $(date +%s) - LOCK_STARTED ))
  if [ "$LOCK_ELAPSED" -ge "$LOCK_WAIT_SECONDS" ]; then
    echo "[launcher] timed out waiting for production lock after ${LOCK_ELAPSED}s; lock=$LOCKDIR" | tee -a "$LOG"
    exit 75
  fi
  echo "[launcher] another batch is running; waiting for lock=$LOCKDIR" | tee -a "$LOG"
  sleep 30
done
echo "$$" > "$LOCKDIR/pid"
cleanup_lock() { rm -f "$LOCKDIR/pid"; rmdir "$LOCKDIR" 2>/dev/null || true; }
trap cleanup_lock EXIT INT TERM

echo "[launcher $(date -u +%FT%TZ)] batch=$BATCH log=$LOG" | tee -a "$LOG"
"$VENV_PY" "$DRIVER" --batch "$BATCH" "$@" 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
echo "[launcher] driver exit=$RC" | tee -a "$LOG"
exit "$RC"
