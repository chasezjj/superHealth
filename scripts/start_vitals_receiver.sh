#!/usr/bin/env bash
# SuperHealth Health Auto Export receiver startup script
#
# Usage:
#   bash scripts/start_vitals_receiver.sh
#
# Environment:
#   SUPERHEALTH_DB  - Path to health.db (optional, default: repo root/health.db)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="${HOME}/.superhealth/logs/vitals"
LOG_FILE="${LOG_DIR}/vitals_receiver.log"
PID_FILE="${HOME}/.superhealth/vitals_receiver.pid"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"
export PYTHONPATH="${PROJECT_DIR}/src"

PORT="$(python3 -c 'from superhealth.config import load; print(load().vitals.port)' 2>/dev/null || echo 8506)"
HOST="$(python3 -c 'from superhealth.config import load; print(load().vitals.host)' 2>/dev/null || echo 0.0.0.0)"
HEALTH_HOST="$HOST"

if [[ "$HEALTH_HOST" == "0.0.0.0" ]]; then
  HEALTH_HOST="127.0.0.1"
fi

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$EXISTING_PID" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "$EXISTING_PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

EXISTING_PID="$(lsof -ti "TCP:${PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
if [[ -n "$EXISTING_PID" ]] && curl -fsS "http://${HEALTH_HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "$EXISTING_PID" >"$PID_FILE"
  echo "$EXISTING_PID"
  exit 0
fi

nohup python3 -m superhealth.api.vitals_receiver >"$LOG_FILE" 2>&1 &
PID="$!"
echo "$PID" >"$PID_FILE"
echo "$PID"
