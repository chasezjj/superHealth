#!/usr/bin/env bash
set -euo pipefail

# 每日健康数据流水线入口
# 用法:
#   run_garmin_daily.sh              # 正常模式（每天早上7点cron调用）
#   run_garmin_daily.sh --test-mode  # 测试模式：仅重新生成高级日报，不写DB/不覆盖正式报告

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_DIR="$BASE_DIR/src"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"

export BASE_DIR SRC_DIR LOG_DIR

exec /usr/bin/flock -xn /tmp/fetch_garmin_daily.lock \
    bash -c '
        PYTHONPATH="$SRC_DIR" /usr/bin/python3 -m superhealth.daily_pipeline "$@" \
            >> "$LOG_DIR/fetch_garmin_daily.log" 2>&1
    ' _ "$@"
