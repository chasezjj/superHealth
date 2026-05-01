#!/usr/bin/env bash
# SuperHealth daily pipeline cron script
#
# Usage:
#   Add to crontab (run daily at 7:05 AM):
#     5 7 * * * /path/to/superhealth/scripts/run_daily_pipeline.sh >> /tmp/superhealth.log 2>&1
#
# Environment:
#   SUPERHEALTH_DB   - Path to health.db (optional, default: repo root/health.db)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

export PYTHONPATH="${PROJECT_DIR}/src"

python -m superhealth.daily_pipeline "$@"
