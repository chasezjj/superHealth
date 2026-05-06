#!/usr/bin/env bash
# SuperHealth dashboard startup script
#
# Usage:
#   bash scripts/start_dashboard.sh
#
# Environment:
#   DASHBOARD_PORT  - Streamlit port (default: 8505)
#   SUPERHEALTH_DB  - Path to health.db (optional, default: repo root/health.db)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="${HOME}/.superhealth/logs/dashboard"
LOG_FILE="${LOG_DIR}/dashboard.nohup.log"
PORT="${DASHBOARD_PORT:-8505}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

export PYTHONPATH="${PROJECT_DIR}/src"

nohup python3 -m superhealth dashboard --server.port="$PORT" >"$LOG_FILE" 2>&1 &
echo $!
